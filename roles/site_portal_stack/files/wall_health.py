# -*- coding: utf-8 -*-
"""1.139.0: Network & AD health wall area + Site map (SolarWinds lat/lon) model and live read-only collector.

Reuses the portal's existing read-only data sources (SolarWinds SWIS via plugins/module_utils/site_swis.py,
Active Directory report DB runs via AdDb.read_wall, and optional Ops monitor fleet data):
- SWQL SELECTs only against SolarWinds (never writes, never modifies).
- Only important conditions appear on the wall (down, full, sustained errors); short blips and dependent
  children behind a down parent are suppressed and counted.
- Missing, unconfigured, failed, or stale feeds always report grey 'not read' / 'Not current' with why,
  never green.
- Site map places sites by SolarWinds Orion.WorldMap.Point lat/lon (then custom properties, then
  portal_wall_site_places / netmap_sites), rejects invalid / (0, 0) coordinates, and never drops unplaced sites.
Standard library only (site_swis imported optionally)."""
import collections
import datetime
import hashlib
import json
import math
import re
import threading
import time

try:
    import site_swis
except ImportError:
    site_swis = None

import wall_model

VERSION = "1.139.0"

# ---- 1.139.0: defaults and option clamping -------------------------------------------------------------------
DEFAULTS = {
    "seconds": 60,
    "late": 180,
    "stale": 600,
    "ad_old_minutes": 90,
    "ad_repl_warn_hours": 6.0,
    "ad_repl_crit_hours": 24.0,
    "volume_warn": 85.0,
    "volume_crit": 95.0,
    "if_err_warn": 1.0,
    "if_err_crit": 2.5,
    "wan_util_warn": 75.0,
    "wan_util_crit": 90.0,
    "wan_rtt_warn": 80.0,
    "wan_rtt_crit": 150.0,
    "wan_loss_warn": 1.0,
    "wan_loss_crit": 3.0,
    "sustained_minutes": 15,
    "down_polls": 2,
    "poll_stale_minutes": 15,
    "max_nodes": 5000,
    "page": 1000,
}

IDLE_STOP = 900          # stop background polling after 15 min with no viewer
GROUP_EVERY = 900        # refresh Orion.Container group membership every 15 min
HISTORY_HOURS = 4        # rolling 4h trend and timeline window
BUCKET_SECONDS = 300     # 5-minute trend buckets (48 points = 4h)
MAX_CONDITIONS = 500
MAX_SUPPRESSED = 200
MAX_SITES = 2000

SEV_RANK = {"crit": 3, "warn": 2, "stale": 1, "ok": 0}
BUCKET_ORDER = ("down", "full", "err")


def _num(v):
    """Finite float or None (bools are not numbers)."""
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


def _int(v, lo, hi, default):
    n = _num(v)
    if n is None:
        return default
    return max(lo, min(hi, int(round(n))))


def _pair(raw, wk, ck, lo, hi, dw, dc):
    w, c = _num(raw.get(wk)), _num(raw.get(ck))
    if w is None or c is None or not (lo <= w < c <= hi):
        return float(dw), float(dc)
    return float(w), float(c)


def health_opts(raw=None):
    """1.139.0: validate and clamp Network & AD health wall settings -> plain dict."""
    r = raw if isinstance(raw, dict) else {}
    sec = _int(r.get("seconds"), 30, 3600, DEFAULTS["seconds"])
    late = max(DEFAULTS["late"], sec * 3)
    stale = max(DEFAULTS["stale"], sec * 10)
    vw, vc = _pair(r, "volume_warn", "volume_crit", 1, 100, DEFAULTS["volume_warn"], DEFAULTS["volume_crit"])
    ew, ec = _pair(r, "if_err_warn", "if_err_crit", 0.01, 100, DEFAULTS["if_err_warn"], DEFAULTS["if_err_crit"])
    uw, uc = _pair(r, "wan_util_warn", "wan_util_crit", 1, 100, DEFAULTS["wan_util_warn"], DEFAULTS["wan_util_crit"])
    rw, rc = _pair(r, "wan_rtt_warn", "wan_rtt_crit", 1, 10000, DEFAULTS["wan_rtt_warn"], DEFAULTS["wan_rtt_crit"])
    lw, lc = _pair(r, "wan_loss_warn", "wan_loss_crit", 0.01, 100, DEFAULTS["wan_loss_warn"], DEFAULTS["wan_loss_crit"])
    aw, ac = _pair(r, "ad_repl_warn_hours", "ad_repl_crit_hours", 0.1, 720, DEFAULTS["ad_repl_warn_hours"], DEFAULTS["ad_repl_crit_hours"])
    return {
        "seconds": sec,
        "late": late,
        "stale": stale,
        "ad_old_minutes": _int(r.get("ad_old_minutes"), 10, 10080, DEFAULTS["ad_old_minutes"]),
        "ad_repl_warn_hours": aw,
        "ad_repl_crit_hours": ac,
        "volume_warn": vw,
        "volume_crit": vc,
        "if_err_warn": ew,
        "if_err_crit": ec,
        "wan_util_warn": uw,
        "wan_util_crit": uc,
        "wan_rtt_warn": rw,
        "wan_rtt_crit": rc,
        "wan_loss_warn": lw,
        "wan_loss_crit": lc,
        "sustained_minutes": _int(r.get("sustained_minutes"), 1, 240, DEFAULTS["sustained_minutes"]),
        "down_polls": _int(r.get("down_polls"), 1, 10, DEFAULTS["down_polls"]),
        "poll_stale_minutes": _int(r.get("poll_stale_minutes"), 2, 1440, DEFAULTS["poll_stale_minutes"]),
        "max_nodes": _int(r.get("max_nodes"), 100, 50000, DEFAULTS["max_nodes"]),
        "page": _int(r.get("page"), 50, 5000, DEFAULTS["page"]),
    }


def parse_ts(v):
    """Parse an ISO timestamp or epoch float -> epoch float or None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) and v > 0 else None
    s = str(v).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d)(?::(\d\d))?", s)
        if not m:
            return None
        dt = datetime.datetime(*[int(x or 0) for x in m.groups()], tzinfo=datetime.timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.timestamp()


def valid_coord(lat, lon):
    """1.139.0: validate SolarWinds / configured lat/lon.
    Rejects non-numbers, out-of-range values, and Null Island (0, 0) -> (ok, (lat, lon) or None, why)."""
    la, lo = _num(lat), _num(lon)
    if la is None or lo is None:
        return False, None, "no latitude/longitude coordinate"
    if not (-90.0 <= la <= 90.0 and -180.0 <= lo <= 180.0):
        return False, None, "coordinate (%.4f, %.4f) out of valid range" % (la, lo)
    if abs(la) < 1e-6 and abs(lo) < 1e-6:
        return False, None, "coordinate (0, 0) rejected as unset default (Null Island)"
    return True, (round(la, 4), round(lo, 4)), ""


def _mean_valid(pts):
    """Vector spherical mean of validated (lat, lon) points (works across the date line)."""
    good = []
    for p in pts or []:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            ok, coord, _ = valid_coord(p[0], p[1])
            if ok:
                good.append(coord)
    if not good:
        return None
    x = y = z = 0.0
    for la, lo in good:
        a, b = math.radians(la), math.radians(lo)
        x += math.cos(a) * math.cos(b)
        y += math.cos(a) * math.sin(b)
        z += math.sin(a)
    h = math.hypot(x, y)
    if h < 1e-9 and abs(z) < 1e-9:
        return good[0]
    return (round(math.degrees(math.atan2(z, h)), 4), round(math.degrees(math.atan2(y, x)), 4))


def feed_freshness(state, asof, now, late, stale):
    """Return ('ok'|'late'|'stale'|'not_read', age_s or None). Missing/failed/stale is never 'ok'."""
    if state in ("never", "off") or asof is None:
        return "not_read", None
    age = max(0.0, float(now) - float(asof))
    if state != "ok":
        return "stale", age
    if age > stale:
        return "stale", age
    if age > late:
        return "late", age
    return "ok", age


def _node_status(code, unmanaged=False):
    """SolarWinds Orion.Nodes Status integer -> 'up'|'down'|'warn'|'unmanaged'|'unknown'."""
    if unmanaged:
        return "unmanaged"
    if isinstance(code, bool) or code is None:
        return "unknown"
    try:
        c = int(code)
    except (TypeError, ValueError):
        return "unknown"
    if c == 1:
        return "up"
    if c == 2:
        return "down"
    if c in (3, 14):
        return "warn"
    if c == 9:
        return "unmanaged"
    return "unknown"


# ---- 1.139.0: Read-only SolarWinds SWQL collector for Health Wall + Site Map ---------------------------------
class SwisHealthReader(object):
    """1.139.0: Read-only SolarWinds SWIS collector (SWQL SELECTs only) for Network & AD health and Site map.
    Never modifies SolarWinds; optional entities (Orion.Volumes, Orion.NPM.Interfaces, Orion.Container,
    Orion.TopologyConnections) are checked in Metadata.Entity and reported as 'not read' with reason if absent."""

    ENTITIES = (
        "Orion.Nodes",
        "Orion.WorldMap.Point",
        "Orion.Volumes",
        "Orion.NPM.Interfaces",
        "Orion.TopologyConnections",
        "Orion.TopologyData",
        "Orion.Container",
        "Orion.ContainerMembers",
    )

    def __init__(self, url, user, password, verify=True, ca=None, timeout=60, netmap_opts=None, opts=None):
        self.url = str(url or "").strip()
        self.user = str(user or "")
        self.password = str(password or "")
        self.verify = bool(verify)
        self.ca = ca or None
        self.timeout = max(5, int(timeout or 60))
        self.netmap_opts = dict(netmap_opts or {})
        self.opts = health_opts(opts)
        self._groups_at = 0.0
        self._groups_cache = {}
        self._groups_why = ""
        self._entities = None

    def _client(self):
        if site_swis is None:
            raise RuntimeError("the SolarWinds reader (site_swis.py) is not in the service files")
        return site_swis.Client(self.url, self.user, self.password, verify=self.verify, timeout=self.timeout, ca_path=self.ca)

    def read(self, now=None):
        """Read nodes, WorldMap points, volumes, interfaces, topology, and service groups (SWQL SELECT only).
        Returns snapshot dict with per-entity not_read reasons; raises SwisError/RuntimeError on core failure."""
        now = time.time() if now is None else float(now)
        cl = self._client()
        o = self.opts
        nm = self.netmap_opts
        page = o["page"]
        cap = o["max_nodes"]
        not_read = {}

        have = set(r.get("FullName") for r in cl.query(
            "SELECT FullName FROM Metadata.Entity WHERE FullName IN @names", {"names": list(self.ENTITIES)}))
        self._entities = have

        # Custom properties for site / lat / lon
        props = []
        for p in (nm.get("site_by"), nm.get("lat_property"), nm.get("lon_property")):
            p = str(p or "").strip()
            if p and p.lower() not in ("location", "auto") and p not in props and site_swis.IDENT.match(p):
                props.append(p)

        cols = [
            "N.NodeID", "N.Caption", "N.DNS", "N.IPAddress", "N.Status", "N.StatusDescription",
            "N.Vendor", "N.MachineType", "N.Location", "N.ResponseTime", "N.PercentLoss",
            "N.UnManaged", "N.LastSync",
        ]
        extra = [("N.CustomProperties.%s AS cp_%s" % (p, p), "custom property %s" % p) for p in props]
        if "Orion.WorldMap.Point" in have:
            extra.append(("N.WorldMapPoint.Latitude AS WmLat, N.WorldMapPoint.Longitude AS WmLon", "Orion.WorldMap.Point"))
        else:
            not_read["worldmap"] = "Orion.WorldMap.Point is not available on this SolarWinds server"

        base = "SELECT %s FROM Orion.Nodes N"
        try:
            rows, cut = cl.paged(base % ", ".join(cols + [x[0] for x in extra]), "N.NodeID", None, page, cap)
        except site_swis.SwisError as err:
            if err.kind != "query" or not extra:
                raise
            good = []
            for x in extra:
                try:
                    cl.query("SELECT TOP 1 N.NodeID, %s FROM Orion.Nodes N" % x[0])
                    good.append(x)
                except site_swis.SwisError as e2:
                    not_read[x[1]] = str(e2)[:180]
            rows, cut = cl.paged(base % ", ".join(cols + [x[0] for x in good]), "N.NodeID", None, page, cap)

        nodes = []
        for r in rows:
            wm = None
            if "WmLat" in r or "WmLon" in r:
                wm = [_num(r.get("WmLat")), _num(r.get("WmLon"))]
            nodes.append({
                "id": r.get("NodeID"),
                "name": str(r.get("Caption") or r.get("DNS") or r.get("IPAddress") or r.get("NodeID") or ""),
                "dns": str(r.get("DNS") or ""),
                "ip": str(r.get("IPAddress") or ""),
                "status_code": r.get("Status"),
                "status_text": str(r.get("StatusDescription") or ""),
                "vendor": str(r.get("Vendor") or ""),
                "model": str(r.get("MachineType") or ""),
                "location": str(r.get("Location") or ""),
                "resp": _num(r.get("ResponseTime")),
                "loss": _num(r.get("PercentLoss")),
                "unmanaged": bool(r.get("UnManaged")),
                "last_sync": parse_ts(r.get("LastSync")),
                "props": {p: r.get("cp_" + p) for p in props if "cp_" + p in r},
                "wm": wm,
            })

        # Optional: Orion.Volumes (Full bucket)
        volumes = []
        vol_read = False
        if "Orion.Volumes" in have:
            try:
                vrows, vcut = cl.paged(
                    "SELECT VolumeID, NodeID, Caption, FullName, VolumePercentUsed, VolumeSpaceAvailable, VolumeSize, Status "
                    "FROM Orion.Volumes WHERE VolumePercentUsed IS NOT NULL",
                    "VolumeID", None, page, cap * 4,
                )
                vol_read = True
                if vcut:
                    not_read["volumes_cut"] = "volume list cut at %d rows" % len(vrows)
                for vr in vrows:
                    volumes.append({
                        "id": vr.get("VolumeID"),
                        "node_id": vr.get("NodeID"),
                        "caption": str(vr.get("Caption") or vr.get("FullName") or vr.get("VolumeID") or ""),
                        "pct": _num(vr.get("VolumePercentUsed")),
                        "free_bytes": _num(vr.get("VolumeSpaceAvailable")),
                        "size_bytes": _num(vr.get("VolumeSize")),
                        "status": vr.get("Status"),
                    })
            except site_swis.SwisError as err:
                not_read["volumes"] = "Orion.Volumes not read: %s" % str(err)[:180]
        else:
            not_read["volumes"] = "Orion.Volumes is not present on this SolarWinds server"

        # Optional: Topology connections (inter-site WAN links + parent/child)
        connections = []
        topo_ent = "Orion.TopologyConnections" if "Orion.TopologyConnections" in have else (
            "Orion.TopologyData" if "Orion.TopologyData" in have else ""
        )
        topo_ok = False
        if topo_ent:
            try:
                crows, tcut = cl.paged(
                    "SELECT SrcNodeID, SrcInterfaceID, DestNodeID, DestInterfaceID FROM %s" % topo_ent,
                    "SrcNodeID, DestNodeID, SrcInterfaceID, DestInterfaceID", None, page, cap * 10,
                )
                connections = [[c.get("SrcNodeID"), c.get("SrcInterfaceID"), c.get("DestNodeID"), c.get("DestInterfaceID")] for c in crows]
                topo_ok = not tcut
                if tcut:
                    not_read["topology"] = "topology links cut at %d rows" % len(crows)
            except site_swis.SwisError as err:
                not_read["topology"] = "%s not read: %s" % (topo_ent, str(err)[:180])
        else:
            not_read["topology"] = "no Orion.TopologyConnections / Orion.TopologyData (NPM topology unavailable)"

        # Optional: Orion.NPM.Interfaces (WAN utilization + sustained errors)
        interfaces = []
        if_read = False
        if "Orion.NPM.Interfaces" in have:
            try:
                # Read interfaces referenced by topology PLUS any with errors/discards or high utilization
                irows, icut = cl.paged(
                    "SELECT InterfaceID, NodeID, Name, Caption, Speed, Status, OperStatus, "
                    "InPercentUtil, OutPercentUtil, InErrorsThisHour, OutErrorsThisHour, "
                    "InDiscardsThisHour, OutDiscardsThisHour, InPktsPerSec, OutPktsPerSec "
                    "FROM Orion.NPM.Interfaces",
                    "InterfaceID", None, page, cap * 6,
                )
                if_read = True
                if icut:
                    not_read["interfaces_cut"] = "interface list cut at %d rows" % len(irows)
                for ir in irows:
                    interfaces.append({
                        "id": ir.get("InterfaceID"),
                        "node_id": ir.get("NodeID"),
                        "name": str(ir.get("Caption") or ir.get("Name") or ir.get("InterfaceID") or ""),
                        "speed": _num(ir.get("Speed")),
                        "status": ir.get("Status"),
                        "oper_status": ir.get("OperStatus"),
                        "in_util": _num(ir.get("InPercentUtil")),
                        "out_util": _num(ir.get("OutPercentUtil")),
                        "in_err": _num(ir.get("InErrorsThisHour")),
                        "out_err": _num(ir.get("OutErrorsThisHour")),
                        "in_disc": _num(ir.get("InDiscardsThisHour")),
                        "out_disc": _num(ir.get("OutDiscardsThisHour")),
                        "in_pps": _num(ir.get("InPktsPerSec")),
                        "out_pps": _num(ir.get("OutPktsPerSec")),
                    })
            except site_swis.SwisError as err:
                not_read["interfaces"] = "Orion.NPM.Interfaces not read: %s" % str(err)[:180]
        else:
            not_read["interfaces"] = "Orion.NPM.Interfaces is not present on this SolarWinds server"

        # Optional: Service groups (Orion.Container + Orion.ContainerMembers, cached 15 min)
        groups = self._groups_cache
        if now - self._groups_at >= GROUP_EVERY or not self._groups_cache:
            if "Orion.Container" in have and "Orion.ContainerMembers" in have:
                try:
                    crows, _ = cl.paged("SELECT ContainerID, Name FROM Orion.Container", "ContainerID", None, 500, 2000)
                    cname = {r.get("ContainerID"): str(r.get("Name") or "") for r in crows if r.get("ContainerID") is not None}
                    mrows, _ = cl.paged(
                        "SELECT ContainerID, MemberPrimaryID FROM Orion.ContainerMembers WHERE MemberEntityType = 'Orion.Nodes'",
                        "ContainerID, MemberPrimaryID", None, 1000, cap * 4,
                    )
                    by_node = collections.defaultdict(list)
                    for mr in mrows:
                        gn = cname.get(mr.get("ContainerID"))
                        mid = mr.get("MemberPrimaryID")
                        if gn and mid is not None:
                            try:
                                by_node[int(mid)].append(gn)
                            except (TypeError, ValueError):
                                by_node[str(mid)].append(gn)
                    self._groups_cache = dict(by_node)
                    self._groups_at = now
                    self._groups_why = ""
                    groups = self._groups_cache
                except site_swis.SwisError as err:
                    self._groups_why = "Orion.Container not read: %s" % str(err)[:180]
            else:
                self._groups_why = "Orion.Container not available; nodes grouped by site/vendor"
        if self._groups_why:
            not_read["groups"] = self._groups_why

        return {
            "read_at": now,
            "url": self.url,
            "nodes": nodes,
            "cut": bool(cut),
            "volumes": volumes,
            "volumes_read": vol_read,
            "interfaces": interfaces,
            "interfaces_read": if_read,
            "connections": connections,
            "topology": topo_ent,
            "topology_ok": topo_ok,
            "groups": groups,
            "not_read": not_read,
        }


# ---- 1.139.0: Sustained-condition & rolling 4h history tracker -----------------------------------------------
class HealthTracker(object):
    """Tracks poll streaks (down_polls), sustained durations (sustained_minutes), open/cleared conditions,
    and rolling 4h KPI/metric trends (5-minute buckets)."""

    def __init__(self):
        self.down_streak = {}       # key -> {"count": int, "first": ts, "last_read": ts}
        self.sustained = {}         # key -> {"first": ts, "last": ts, "peak": float}
        self.open_conds = {}        # id -> cond dict
        self.cleared = []           # cleared cond dicts within HISTORY_HOURS
        self.trends = collections.deque(maxlen=60)    # 5-min bucket dicts
        self.site_rtt = collections.defaultdict(lambda: collections.deque(maxlen=48))
        self.last_swis_at = None

    def mark_down(self, key, active, read_at, down_polls):
        """Return (confirmed_bool, streak_count, first_ts) for a down entity."""
        cur = self.down_streak.get(key)
        if not active:
            self.down_streak.pop(key, None)
            return False, 0, None
        if cur is None:
            cur = {"count": 1, "first": read_at, "last_read": read_at}
            self.down_streak[key] = cur
        elif read_at != cur["last_read"]:
            cur["count"] += 1
            cur["last_read"] = read_at
        return cur["count"] >= down_polls, cur["count"], cur["first"]

    def mark_sustained(self, key, active, val, read_at, sustained_seconds, gap_limit):
        """Return (sustained_bool, held_seconds, first_ts) for a threshold breach."""
        if not active:
            self.sustained.pop(key, None)
            return False, 0.0, None
        cur = self.sustained.get(key)
        if cur is None or (read_at - cur["last"]) > gap_limit:
            cur = {"first": read_at, "last": read_at, "peak": val}
            self.sustained[key] = cur
        else:
            cur["last"] = read_at
            if val is not None and (cur["peak"] is None or val > cur["peak"]):
                cur["peak"] = val
        held = max(0.0, read_at - cur["first"])
        return held >= sustained_seconds, held, cur["first"]

    def reconcile(self, active_conds, ok_ids, now):
        """Merge active conditions with history; keep last_known when not confirmed OK, archive cleared for 4h."""
        horizon = now - HISTORY_HOURS * 3600
        seen = {}
        for c in active_conds:
            cid = c["id"]
            prev = self.open_conds.get(cid)
            if prev is not None and prev.get("start") is not None:
                c["start"] = min(prev["start"], c.get("start") or prev["start"])
            else:
                c["start"] = c.get("start") or now
            c["end"] = None
            c["last_known"] = False
            seen[cid] = c

        for cid, prev in list(self.open_conds.items()):
            if cid in seen:
                continue
            if cid in ok_ids:
                done = dict(prev, end=now, last_known=False)
                self.cleared.append(done)
            else:
                # Feed stale/missing or entity disappeared without confirmed OK -> last_known (grey)
                lk = dict(prev, last_known=True, end=None)
                seen[cid] = lk

        self.open_conds = seen
        self.cleared = [c for c in self.cleared if (c.get("end") or 0) >= horizon][-200:]
        return list(self.open_conds.values()), list(self.cleared)

    def record_trend(self, now, point, site_rtts=None):
        """Append or update current 5-minute trend bucket."""
        bucket = int(now // BUCKET_SECONDS) * BUCKET_SECONDS
        if self.trends and self.trends[-1]["t"] == bucket:
            self.trends[-1].update(point)
            self.trends[-1]["t"] = bucket
        else:
            row = dict(point)
            row["t"] = bucket
            self.trends.append(row)
        cutoff = now - HISTORY_HOURS * 3600 - BUCKET_SECONDS
        while self.trends and self.trends[0]["t"] < cutoff:
            self.trends.popleft()
        if site_rtts:
            for site_name, rtt_val in site_rtts.items():
                dq = self.site_rtt[site_name]
                if dq and dq[-1][0] == bucket:
                    dq[-1] = [bucket, rtt_val]
                else:
                    dq.append([bucket, rtt_val])


# ---- 1.139.0: Pure model builders (deterministic, easily unit-tested) ---------------------------------------
def _assign_sites(nodes, connections, netmap_opts, site_places):
    """Map every node id -> site name, and gather site coordinates with provenance."""
    nm = dict(netmap_opts or {"site_by": "location"})
    no_site = str(nm.get("no_site") or "Unassigned")
    node_site = {}
    if site_swis is not None and nodes:
        try:
            rid = {n["id"] for n in nodes}
            pairs = []
            seen_p = set()
            for c in connections or []:
                if len(c) >= 4 and c[0] in rid and c[2] in rid and c[0] != c[2]:
                    k = tuple(sorted((str(c[0]), str(c[2]))))
                    if k not in seen_p:
                        seen_p.add(k)
                        pairs.append((c[0], c[2]))
            sw_nodes = [{"id": n["id"], "name": n["name"], "location": n.get("location") or "",
                         "props": n.get("props") or {}, "vendor": n.get("vendor") or "", "model": n.get("model") or ""}
                        for n in nodes]
            disc = site_swis.discover(sw_nodes, pairs, nm)
            for n in nodes:
                node_site[n["id"]] = disc["site"].get(n["id"]) or no_site
        except Exception:  # pylint: disable=broad-except
            node_site = {}

    if not node_site:
        pat_txt = str(nm.get("site_pattern") or "").strip()
        pat = re.compile(pat_txt, re.I) if pat_txt else None
        split = str(nm.get("site_split") or ",")
        site_by = str(nm.get("site_by") or "").strip()
        for n in nodes:
            val = ""
            if site_by and site_by.lower() not in ("auto", "location"):
                val = str((n.get("props") or {}).get(site_by) or "").strip()
            if not val:
                val = str(n.get("location") or "").strip()
                if split and split in val:
                    val = val.split(split, 1)[0].strip()
            if not val and pat:
                m = pat.search(str(n.get("name") or ""))
                if m:
                    val = (m.group(1) if m.lastindex else m.group(0)).strip()
            node_site[n["id"]] = val or no_site

    # Canonicalize site spelling case-insensitively
    canon = {}
    for nid, sname in list(node_site.items()):
        node_site[nid] = canon.setdefault(sname.casefold(), sname)

    # Gather coordinates by provenance: 1) WorldMapPoint, 2) custom lat/lon props, 3) site_places / netmap_sites
    wm_pts = collections.defaultdict(list)
    wm_rejected = collections.defaultdict(list)
    prop_pts = collections.defaultdict(list)
    lat_p = str(nm.get("lat_property") or "").strip()
    lon_p = str(nm.get("lon_property") or "").strip()
    for n in nodes:
        sname = node_site[n["id"]]
        wm = n.get("wm")
        if isinstance(wm, (list, tuple)) and len(wm) >= 2 and (wm[0] is not None or wm[1] is not None):
            ok, coord, why = valid_coord(wm[0], wm[1])
            if ok:
                wm_pts[sname].append(coord)
            elif why:
                wm_rejected[sname].append(why)
        props = n.get("props") or {}
        if lat_p and lon_p and (props.get(lat_p) is not None or props.get(lon_p) is not None):
            ok, coord, _ = valid_coord(props.get(lat_p), props.get(lon_p))
            if ok:
                prop_pts[sname].append(coord)

    # Configured fallback map (portal_wall_site_places / netmap_sites)
    conf_places = {}
    for src in (nm.get("sites") or {}, site_places or {}):
        if not isinstance(src, dict):
            continue
        for k, v in src.items():
            if isinstance(v, dict):
                ok, coord, _ = valid_coord(v.get("lat"), v.get("lon"))
                if ok:
                    conf_places[str(k).strip()] = coord
            elif isinstance(v, (list, tuple)) and len(v) >= 2:
                # portal_wall_site_places pairs are [longitude, latitude].
                ok, coord, _ = valid_coord(v[1], v[0])
                if ok:
                    conf_places[str(k).strip()] = coord

    def lookup_conf(sname):
        for k, coord in conf_places.items():
            if "*" not in k and "?" not in k and k.casefold() == sname.casefold():
                return coord
        if site_swis is not None:
            hit = site_swis.place(conf_places, sname)
            if hit:
                return tuple(hit)
        return None

    coords = {}
    for sname in set(node_site.values()):
        p = _mean_valid(wm_pts.get(sname))
        if p:
            coords[sname] = {"placed": True, "lat": p[0], "lon": p[1], "source": "Orion.WorldMap.Point", "why": ""}
            continue
        p = _mean_valid(prop_pts.get(sname))
        if p:
            coords[sname] = {"placed": True, "lat": p[0], "lon": p[1], "source": "CustomProperty(%s/%s)" % (lat_p, lon_p), "why": ""}
            continue
        if sname != no_site:
            c = lookup_conf(sname)
            if c:
                coords[sname] = {"placed": True, "lat": c[0], "lon": c[1], "source": "Configured site_places", "why": ""}
                continue
        why = (wm_rejected[sname][0] if wm_rejected.get(sname)
               else "no Orion.WorldMap.Point latitude/longitude or configured site_places entry")
        coords[sname] = {"placed": False, "lat": None, "lon": None, "source": "", "why": why}

    return node_site, coords, no_site


def _ad_matrix(ad_run, o, ad_fresh, now):
    """Build DC-to-DC replication matrix and summary stats from an AdDb.read_wall snapshot."""
    if not ad_run or not isinstance(ad_run, dict):
        return {"read": False, "dcs": [], "pairs": [], "max_lag_min": None, "failing": 0, "ok_pairs": 0, "total_pairs": 0}
    dcs = [d for d in (ad_run.get("dcs") or []) if isinstance(d, dict) and d.get("dc")]
    links = [l for l in (ad_run.get("links") or []) if isinstance(l, dict) and l.get("frm") and l.get("to")]
    run_ts = parse_ts(ad_run.get("read_at")) or now
    warn_h = float(o["ad_repl_warn_hours"])
    crit_h = float(o["ad_repl_crit_hours"])
    stale_feed = ad_fresh in ("stale", "not_read")

    dc_rows = []
    dc_by = {}
    for d in sorted(dcs, key=lambda x: (str(x.get("site") or "").lower(), str(x.get("dc") or "").lower())):
        name = str(d["dc"])
        key = name.lower()
        read_ok = bool(d.get("read"))
        repl_read = bool(d.get("repl_read"))
        row = {
            "id": key,
            "name": name,
            "site": str(d.get("site") or ""),
            "read": read_ok,
            "repl_read": repl_read,
            "status": "stale" if stale_feed else ("crit" if not read_ok else ("warn" if not repl_read else "ok")),
            "worst": str(d.get("worst") or ""),
        }
        dc_rows.append(row)
        dc_by[key] = row

    # Group links by (src_lower, dst_lower)
    pair_map = collections.defaultdict(list)
    for lk in links:
        src = str(lk.get("frm") or "").strip().lower()
        dst = str(lk.get("to") or "").strip().lower()
        if not src or not dst or src == dst:
            continue
        ls_ts = parse_ts(lk.get("last_success"))
        lag_min = round(max(0.0, (run_ts - ls_ts) / 60.0), 1) if ls_ts is not None else None
        fails = int(lk.get("failures") or 0)
        err = str(lk.get("error") or "")
        if stale_feed:
            pst = "stale"
        elif lag_min is None:
            pst = "crit" if fails > 0 else "warn"
        elif (lag_min / 60.0) >= crit_h:
            pst = "crit"
        elif fails > 0 or (lag_min / 60.0) >= warn_h:
            pst = "warn"
        else:
            pst = "ok"
        pair_map[(src, dst)].append({
            "nc": str(lk.get("nc") or "Domain"),
            "lag_min": lag_min,
            "failures": fails,
            "error": err,
            "last_success": str(lk.get("last_success") or ""),
            "status": pst,
        })

    pairs = []
    max_lag = None
    failing = 0
    ok_pairs = 0
    for (src, dst), parts in sorted(pair_map.items()):
        parts.sort(key=lambda p: (-SEV_RANK.get(p["status"], 0), -(p["lag_min"] if p["lag_min"] is not None else 1e9)))
        worst_p = parts[0]
        lag_vals = [p["lag_min"] for p in parts if p["lag_min"] is not None]
        lag_max = max(lag_vals) if lag_vals else None
        if lag_max is not None and (max_lag is None or lag_max > max_lag):
            max_lag = lag_max
        fails_max = max(p["failures"] for p in parts)
        st = worst_p["status"]
        if st in ("crit", "warn"):
            failing += 1
        elif st == "ok":
            ok_pairs += 1
        pairs.append({
            "src": (dc_by.get(src) or {}).get("name", src),
            "dst": (dc_by.get(dst) or {}).get("name", dst),
            "src_site": (dc_by.get(src) or {}).get("site", ""),
            "dst_site": (dc_by.get(dst) or {}).get("site", ""),
            "status": st,
            "lag_min": lag_max,
            "failures": fails_max,
            "error": worst_p["error"],
            "partitions": parts[:8],
        })

    return {
        "read": True,
        "dcs": dc_rows,
        "pairs": pairs,
        "max_lag_min": max_lag,
        "failing": failing,
        "ok_pairs": ok_pairs,
        "total_pairs": len(pairs),
    }


def _if_err_rate(ifc):
    """Compute interface error+discard percentage from hourly counters and pps, or None if unreadable."""
    ie = _num(ifc.get("in_err")) or 0.0
    oe = _num(ifc.get("out_err")) or 0.0
    idisc = _num(ifc.get("in_disc")) or 0.0
    odisc = _num(ifc.get("out_disc")) or 0.0
    bad = ie + oe + idisc + odisc
    if bad <= 0:
        return 0.0
    ipps = _num(ifc.get("in_pps")) or 0.0
    opps = _num(ifc.get("out_pps")) or 0.0
    pkts_hr = (ipps + opps) * 3600.0
    if pkts_hr <= 0:
        # Without packet counter, treat non-zero hourly errors cautiously (rate unavailable unless high absolute count)
        return None
    return round(min(100.0, (bad / pkts_hr) * 100.0), 2)


def build_health_model(swis_snap, swis_meta, ad_run, ad_meta, opts, tracker, now,
                       netmap_opts=None, site_places=None, enclave="", title="", full_links=None):
    """1.139.0: Build the Network & AD health wall model from live SolarWinds + AD report DB snapshots.
    Strict rules:
    - Only important conditions on the wall (buckets: down, full, err).
    - Missing/stale/failed feeds show grey 'not read' / 'Not current' with exact why, NEVER green.
    - Short blips & dependent children behind a down parent are suppressed and counted."""
    o = health_opts(opts)
    now = float(now)
    sw_meta = dict(swis_meta or {})
    ad_m = dict(ad_meta or {})
    sw_state = sw_meta.get("state") or ("ok" if swis_snap else "never")
    sw_asof = sw_meta.get("asof") if sw_meta.get("asof") is not None else (swis_snap.get("read_at") if isinstance(swis_snap, dict) else None)
    sw_why = str(sw_meta.get("why") or "")
    sw_fresh, sw_age = feed_freshness(sw_state, sw_asof, now, o["late"], o["stale"])

    ad_old_s = o["ad_old_minutes"] * 60
    ad_stale_s = max(ad_old_s * 2, ad_old_s + 1800)
    ad_state = ad_m.get("state") or ("ok" if ad_run else "never")
    ad_asof = ad_m.get("asof") if ad_m.get("asof") is not None else (parse_ts(ad_run.get("read_at")) if isinstance(ad_run, dict) else None)
    ad_why = str(ad_m.get("why") or "")
    ad_fresh, ad_age = feed_freshness(ad_state, ad_asof, now, ad_old_s, ad_stale_s)

    not_read = dict((swis_snap or {}).get("not_read") or {})
    vol_state = "ok" if (sw_fresh == "ok" and isinstance(swis_snap, dict) and swis_snap.get("volumes_read")) else (
        "late" if (sw_fresh == "late" and isinstance(swis_snap, dict) and swis_snap.get("volumes_read")) else "not_read")
    if_state = "ok" if (sw_fresh == "ok" and isinstance(swis_snap, dict) and swis_snap.get("interfaces_read")) else (
        "late" if (sw_fresh == "late" and isinstance(swis_snap, dict) and swis_snap.get("interfaces_read")) else "not_read")

    feeds = [
        {"id": "swis", "name": "SolarWinds SWIS", "state": sw_state, "fresh": sw_fresh, "asof": sw_asof,
         "age_s": round(sw_age, 1) if sw_age is not None else None, "late": o["late"], "stale": o["stale"],
         "why": sw_why or (not_read.get("volumes") if not swis_snap else "") or ("not configured (attach SolarWinds read-only credential)" if sw_state == "never" else "")},
        {"id": "adjob", "name": "AD Health Report DB", "state": ad_state, "fresh": ad_fresh, "asof": ad_asof,
         "age_s": round(ad_age, 1) if ad_age is not None else None, "late": ad_old_s, "stale": ad_stale_s,
         "why": ad_why or ("no AD report run in report database yet" if ad_state == "never" else "")},
        {"id": "interfaces", "name": "NPM Interfaces", "state": "ok" if (swis_snap and swis_snap.get("interfaces_read")) else "never",
         "fresh": if_state, "asof": sw_asof if (swis_snap and swis_snap.get("interfaces_read")) else None,
         "age_s": round(sw_age, 1) if (sw_age is not None and swis_snap and swis_snap.get("interfaces_read")) else None,
         "late": o["late"], "stale": o["stale"],
         "why": not_read.get("interfaces") or (sw_why if not swis_snap else "") or ("" if (swis_snap and swis_snap.get("interfaces_read")) else "SolarWinds interfaces not read")},
        {"id": "volumes", "name": "Orion Volumes", "state": "ok" if (swis_snap and swis_snap.get("volumes_read")) else "never",
         "fresh": vol_state, "asof": sw_asof if (swis_snap and swis_snap.get("volumes_read")) else None,
         "age_s": round(sw_age, 1) if (sw_age is not None and swis_snap and swis_snap.get("volumes_read")) else None,
         "late": o["late"], "stale": o["stale"],
         "why": not_read.get("volumes") or (sw_why if not swis_snap else "") or ("" if (swis_snap and swis_snap.get("volumes_read")) else "SolarWinds volumes not read")},
    ]

    active_conds = []
    ok_ids = set()
    suppressed = []
    sus_sec = o["sustained_minutes"] * 60
    gap_limit = max(o["seconds"] * 4, 300)
    poll_stale_s = o["poll_stale_minutes"] * 60

    nodes = list((swis_snap or {}).get("nodes") or [])
    conns = list((swis_snap or {}).get("connections") or [])
    volumes = list((swis_snap or {}).get("volumes") or [])
    interfaces = list((swis_snap or {}).get("interfaces") or [])
    node_groups = dict((swis_snap or {}).get("groups") or {})
    node_site, _, no_site = _assign_sites(nodes, conns, netmap_opts, site_places)
    node_by = {n["id"]: n for n in nodes if n.get("id") is not None}

    # Pre-compute confirmed down nodes + parent/child dependency suppression via topology
    raw_down_ids = set()
    node_verdict = {}
    up_cnt = down_cnt = warn_cnt = unk_cnt = unman_cnt = 0
    sw_current = sw_fresh == "ok" and sw_state == "ok" and not (swis_snap or {}).get("cut")
    for n in nodes:
        nid = n["id"]
        st = _node_status(n.get("status_code"), n.get("unmanaged"))
        ls = n.get("last_sync")
        poll_old = (ls is not None and (now - ls) > poll_stale_s)
        if sw_fresh in ("stale", "not_read"):
            eff = "stale"
        elif st == "unmanaged":
            eff = "unmanaged"
            unman_cnt += 1
        elif poll_old and st == "up":
            eff = "stale"
            unk_cnt += 1
        elif st == "down":
            eff = "down"
            down_cnt += 1
            raw_down_ids.add(nid)
        elif st == "warn":
            eff = "warn"
            warn_cnt += 1
        elif st == "up":
            eff = "up"
            up_cnt += 1
        else:
            eff = "stale"
            unk_cnt += 1
        node_verdict[nid] = eff

    # Topology adjacency to identify leaf nodes whose ONLY topology neighbor is a down router/switch
    adj = collections.defaultdict(set)
    for c in conns:
        if len(c) >= 4 and c[0] in node_by and c[2] in node_by and c[0] != c[2]:
            adj[c[0]].add(c[2])
            adj[c[2]].add(c[0])

    # Evaluate node down conditions (only when SolarWinds snapshot is present & not stale)
    sw_read_ts = sw_asof or now
    if swis_snap and sw_fresh in ("ok", "late"):
        for n in nodes:
            nid = n["id"]
            cid = "net:down:%s" % nid
            eff = node_verdict[nid]
            is_down = (eff == "down")
            confirmed, streak, first_ts = tracker.mark_down(cid, is_down, sw_read_ts, o["down_polls"])
            if not is_down:
                if eff == "up" and sw_current:
                    ok_ids.add(cid)
                elif eff == "unmanaged":
                    suppressed.append({"domain": "net", "obj": n["name"], "why": "unmanaged in SolarWinds"})
                continue
            # Check dependent child suppression (only 1 neighbor and that neighbor is also down)
            nbrs = adj.get(nid)
            if nbrs and len(nbrs) == 1:
                parent_id = next(iter(nbrs))
                if parent_id in raw_down_ids and len(adj.get(parent_id) or ()) > 1:
                    suppressed.append({"domain": "net", "obj": n["name"],
                                       "why": "dependent behind down parent %s" % (node_by[parent_id]["name"])})
                    continue
            if not confirmed:
                suppressed.append({"domain": "net", "obj": n["name"],
                                   "why": "down %d of %d polls (waiting for confirmation)" % (streak, o["down_polls"])})
                continue
            sname = node_site.get(nid) or no_site
            active_conds.append({
                "id": cid, "domain": "net", "bucket": "down", "sev": "crit",
                "obj": n["name"], "site": sname, "ip": n.get("ip") or "",
                "title": "Node unreachable",
                "detail": n.get("status_text") or "SolarWinds status Down",
                "val": 100.0, "unit": "% loss", "warn": o["wan_loss_warn"], "crit": o["wan_loss_crit"],
                "val_text": "DOWN (%d polls)" % streak,
                "start": first_ts or sw_read_ts, "feed": "swis",
                "check": "SolarWinds › Node Details › %s (%s)" % (n["name"], n.get("ip") or "no IP"),
            })

    # Evaluate Volumes (Full bucket)
    max_vol_pct = None
    max_vol_obj = ""
    if swis_snap and swis_snap.get("volumes_read") and sw_fresh in ("ok", "late"):
        for v in volumes:
            pct = v.get("pct")
            if pct is None:
                continue
            node = node_by.get(v.get("node_id")) or {}
            if node.get("unmanaged"):
                continue
            nname = node.get("name") or ("Node %s" % v.get("node_id"))
            obj_label = "%s · %s" % (nname, v.get("caption") or "volume")
            if max_vol_pct is None or pct > max_vol_pct:
                max_vol_pct = round(pct, 1)
                max_vol_obj = obj_label
            cid = "net:vol:%s" % v.get("id")
            if pct >= o["volume_crit"]:
                sev = "crit"
            elif pct >= o["volume_warn"]:
                sev = "warn"
            else:
                if sw_current:
                    ok_ids.add(cid)
                continue
            active_conds.append({
                "id": cid, "domain": "net", "bucket": "full", "sev": sev,
                "obj": obj_label, "site": node_site.get(v.get("node_id")) or no_site,
                "title": "Volume %.1f%% full" % pct,
                "detail": "Warn %.0f%% · Crit %.0f%%" % (o["volume_warn"], o["volume_crit"]),
                "val": round(pct, 1), "unit": "%", "warn": o["volume_warn"], "crit": o["volume_crit"],
                "val_text": "%.1f%%" % pct,
                "start": sw_read_ts, "feed": "volumes",
                "check": "SolarWinds › Volume Details › %s" % obj_label,
            })

    # Evaluate Interfaces (WAN down, sustained full util, sustained interface errors)
    wan_if_ids = set()
    for c in conns:
        if len(c) >= 4 and c[0] in node_by and c[2] in node_by and node_site.get(c[0]) != node_site.get(c[2]):
            if c[1] is not None:
                wan_if_ids.add(c[1])
            if c[3] is not None:
                wan_if_ids.add(c[3])

    if swis_snap and swis_snap.get("interfaces_read") and sw_fresh in ("ok", "late"):
        for ifc in interfaces:
            iid = ifc.get("id")
            node = node_by.get(ifc.get("node_id")) or {}
            if node.get("unmanaged") or ifc.get("node_id") in raw_down_ids:
                continue
            nname = node.get("name") or ("Node %s" % ifc.get("node_id"))
            if_label = "%s · %s" % (nname, ifc.get("name") or "interface")
            sname = node_site.get(ifc.get("node_id")) or no_site
            is_wan = iid in wan_if_ids

            # WAN interface down -> Down bucket
            if is_wan:
                cid_d = "net:wandown:%s" % iid
                if_down = (ifc.get("oper_status") == 2 or ifc.get("status") == 2)
                conf, streak, first_ts = tracker.mark_down(cid_d, if_down, sw_read_ts, o["down_polls"])
                if if_down:
                    if conf:
                        active_conds.append({
                            "id": cid_d, "domain": "net", "bucket": "down", "sev": "crit",
                            "obj": if_label, "site": sname,
                            "title": "WAN uplink down",
                            "detail": "Interface operationally down (%d polls)" % streak,
                            "val": 100.0, "unit": "%", "warn": o["wan_util_warn"], "crit": o["wan_util_crit"],
                            "val_text": "DOWN",
                            "start": first_ts or sw_read_ts, "feed": "interfaces",
                            "check": "SolarWinds › Interface Details › %s" % if_label,
                        })
                    else:
                        suppressed.append({"domain": "net", "obj": if_label, "why": "WAN down %d/%d polls" % (streak, o["down_polls"])})
                elif sw_current:
                    ok_ids.add(cid_d)

            # High utilization (WAN or >= crit) -> Full bucket (sustained)
            util = max([u for u in (ifc.get("in_util"), ifc.get("out_util")) if u is not None] or [-1.0])
            cid_u = "net:util:%s" % iid
            over_u = util >= o["wan_util_warn"] and (is_wan or util >= o["wan_util_crit"])
            sus_u, held_u, first_u = tracker.mark_sustained(cid_u, over_u, util if over_u else None, sw_read_ts, sus_sec, gap_limit)
            if over_u:
                if sus_u:
                    sev = "crit" if util >= o["wan_util_crit"] else "warn"
                    active_conds.append({
                        "id": cid_u, "domain": "net", "bucket": "full", "sev": sev,
                        "obj": if_label, "site": sname,
                        "title": "Link utilization %.0f%% sustained" % util,
                        "detail": "Held %d min · Warn %.0f%% · Crit %.0f%%" % (int(held_u // 60), o["wan_util_warn"], o["wan_util_crit"]),
                        "val": round(util, 1), "unit": "%", "warn": o["wan_util_warn"], "crit": o["wan_util_crit"],
                        "val_text": "%.0f%%" % util,
                        "start": first_u or sw_read_ts, "feed": "interfaces",
                        "check": "SolarWinds › Interface Details › %s" % if_label,
                    })
                else:
                    suppressed.append({"domain": "net", "obj": if_label,
                                       "why": "util %.0f%% for %d min (under %d min sustained limit)" % (util, int(held_u // 60), o["sustained_minutes"])})
            elif sw_current:
                ok_ids.add(cid_u)

            # Interface errors/discards -> Sustained errors bucket
            err_pct = _if_err_rate(ifc)
            cid_e = "net:iferr:%s" % iid
            over_e = (err_pct is not None and err_pct >= o["if_err_warn"])
            sus_e, held_e, first_e = tracker.mark_sustained(cid_e, over_e, err_pct, sw_read_ts, sus_sec, gap_limit)
            if over_e:
                if sus_e:
                    sev = "crit" if err_pct >= o["if_err_crit"] else "warn"
                    active_conds.append({
                        "id": cid_e, "domain": "net", "bucket": "err", "sev": sev,
                        "obj": if_label, "site": sname,
                        "title": "Sustained interface errors (%.2f%%)" % err_pct,
                        "detail": "Held %d min · Warn %.1f%% · Crit %.1f%%" % (int(held_e // 60), o["if_err_warn"], o["if_err_crit"]),
                        "val": round(err_pct, 2), "unit": "%", "warn": o["if_err_warn"], "crit": o["if_err_crit"],
                        "val_text": "%.2f%%" % err_pct,
                        "start": first_e or sw_read_ts, "feed": "interfaces",
                        "check": "SolarWinds › Interface Errors › %s" % if_label,
                    })
                else:
                    suppressed.append({"domain": "net", "obj": if_label,
                                       "why": "interface errors %.2f%% for %d min (short blip)" % (err_pct, int(held_e // 60))})
            elif sw_current:
                ok_ids.add(cid_e)

    # Evaluate node RTT / packet loss (sustained errors) + gather site RTTs
    worst_rtt = None
    worst_loss = None
    site_rtts = collections.defaultdict(list)
    if swis_snap and sw_fresh in ("ok", "late"):
        for n in nodes:
            nid = n["id"]
            if node_verdict[nid] in ("down", "unmanaged", "stale"):
                continue
            sname = node_site.get(nid) or no_site
            rtt = n.get("resp")
            loss = n.get("loss")
            if rtt is not None and rtt >= 0:
                site_rtts[sname].append(rtt)
                if worst_rtt is None or rtt > worst_rtt:
                    worst_rtt = round(rtt, 1)
            if loss is not None and loss >= 0:
                if worst_loss is None or loss > worst_loss:
                    worst_loss = round(loss, 1)

            cid_l = "net:loss:%s" % nid
            over_l = (loss is not None and loss >= o["wan_loss_warn"])
            sus_l, held_l, first_l = tracker.mark_sustained(cid_l, over_l, loss, sw_read_ts, sus_sec, gap_limit)
            if over_l:
                if sus_l:
                    sev = "crit" if loss >= o["wan_loss_crit"] else "warn"
                    active_conds.append({
                        "id": cid_l, "domain": "net", "bucket": "err", "sev": sev,
                        "obj": n["name"], "site": sname, "ip": n.get("ip") or "",
                        "title": "Sustained packet loss (%.1f%%)" % loss,
                        "detail": "Held %d min · Warn %.1f%% · Crit %.1f%%" % (int(held_l // 60), o["wan_loss_warn"], o["wan_loss_crit"]),
                        "val": round(loss, 1), "unit": "%", "warn": o["wan_loss_warn"], "crit": o["wan_loss_crit"],
                        "val_text": "%.1f%%" % loss,
                        "start": first_l or sw_read_ts, "feed": "swis",
                        "check": "SolarWinds › Node Details › %s" % n["name"],
                    })
                else:
                    suppressed.append({"domain": "net", "obj": n["name"],
                                       "why": "packet loss %.1f%% for %d min (blip suppressed)" % (loss, int(held_l // 60))})
            elif sw_current:
                ok_ids.add(cid_l)

            cid_r = "net:rtt:%s" % nid
            over_r = (rtt is not None and rtt >= o["wan_rtt_warn"])
            sus_r, held_r, first_r = tracker.mark_sustained(cid_r, over_r, rtt, sw_read_ts, sus_sec, gap_limit)
            if over_r:
                if sus_r:
                    sev = "crit" if rtt >= o["wan_rtt_crit"] else "warn"
                    active_conds.append({
                        "id": cid_r, "domain": "net", "bucket": "err", "sev": sev,
                        "obj": n["name"], "site": sname, "ip": n.get("ip") or "",
                        "title": "Sustained high latency (%.0f ms)" % rtt,
                        "detail": "Held %d min · Warn %.0f ms · Crit %.0f ms" % (int(held_r // 60), o["wan_rtt_warn"], o["wan_rtt_crit"]),
                        "val": round(rtt, 1), "unit": "ms", "warn": o["wan_rtt_warn"], "crit": o["wan_rtt_crit"],
                        "val_text": "%.0f ms" % rtt,
                        "start": first_r or sw_read_ts, "feed": "swis",
                        "check": "SolarWinds › Node Details › %s" % n["name"],
                    })
                else:
                    suppressed.append({"domain": "net", "obj": n["name"],
                                       "why": "latency %.0f ms for %d min (blip suppressed)" % (rtt, int(held_r // 60))})
            elif sw_current:
                ok_ids.add(cid_r)

    # Evaluate AD Health from report DB run (reusing old-wallad.diff rules)
    ad_mx = _ad_matrix(ad_run, o, ad_fresh, now)
    ad_current = ad_fresh == "ok" and ad_state == "ok"
    ad_read_ts = ad_asof or now
    if ad_run and isinstance(ad_run, dict) and ad_fresh in ("ok", "late"):
        prev_unread = set(str(x).lower() for x in (ad_run.get("prev_unread") or []))
        for d in ad_run.get("dcs") or []:
            if not isinstance(d, dict) or not d.get("dc"):
                continue
            dc_name = str(d["dc"])
            dc_low = dc_name.lower()
            dc_site = str(d.get("site") or "")
            cid_dc = "ad:dc:%s" % dc_low
            is_down = not bool(d.get("read")) or str(d.get("sw_status") or "").lower() == "down"
            if is_down:
                two_checks = (dc_low in prev_unread) or str(d.get("sw_status") or "").lower() == "down"
                if two_checks:
                    active_conds.append({
                        "id": cid_dc, "domain": "ad", "bucket": "down", "sev": "crit",
                        "obj": dc_name, "site": dc_site,
                        "title": "Domain controller unreachable",
                        "detail": str(d.get("worst") or "Failed two consecutive AD health checks"),
                        "val": 100.0, "unit": "%", "warn": 50.0, "crit": 100.0,
                        "val_text": "DOWN",
                        "start": ad_read_ts, "feed": "adjob",
                        "check": "AD Health page › %s · dcdiag /s:%s" % (dc_name, dc_name),
                    })
                else:
                    suppressed.append({"domain": "ad", "obj": dc_name,
                                       "why": "1 failed AD check (promoted to Critical after 2 consecutive checks)"})
            elif ad_current:
                ok_ids.add(cid_dc)

            # Critical AD problem on a reachable DC (services / SYSVOL / FSMO)
            cid_p = "ad:prob:%s" % dc_low
            if bool(d.get("read")) and str(d.get("status") or "").lower() == "critical":
                active_conds.append({
                    "id": cid_p, "domain": "ad", "bucket": "down", "sev": "crit",
                    "obj": dc_name, "site": dc_site,
                    "title": "AD critical service/role fault",
                    "detail": str(d.get("worst") or "Critical AD problem reported"),
                    "val": 100.0, "unit": "%", "warn": 50.0, "crit": 100.0,
                    "val_text": "CRIT",
                    "start": ad_read_ts, "feed": "adjob",
                    "check": "AD Health page › %s" % dc_name,
                })
            elif bool(d.get("read")) and ad_current:
                ok_ids.add(cid_p)

        # AD replication pairs -> Sustained errors bucket
        for p in ad_mx["pairs"]:
            cid_r = "ad:repl:%s>%s" % (p["src"].lower(), p["dst"].lower())
            if p["status"] in ("crit", "warn"):
                lag_h = round((p["lag_min"] / 60.0), 1) if p["lag_min"] is not None else None
                val_txt = ("%.1fh lag" % lag_h) if lag_h is not None else ("%d failures" % p["failures"])
                active_conds.append({
                    "id": cid_r, "domain": "ad", "bucket": "err", "sev": p["status"],
                    "obj": "%s → %s" % (p["src"], p["dst"]),
                    "site": p["dst_site"] or p["src_site"],
                    "title": "AD replication %s" % ("stalled" if p["status"] == "crit" else "failing / lagging"),
                    "detail": ("%d consecutive failures · %s" % (p["failures"], p["error"] or "check repadmin")) if p["failures"] else (
                        "Warn %.1fh · Crit %.1fh" % (o["ad_repl_warn_hours"], o["ad_repl_crit_hours"])),
                    "val": lag_h if lag_h is not None else float(p["failures"]),
                    "unit": "h" if lag_h is not None else "fails",
                    "warn": o["ad_repl_warn_hours"], "crit": o["ad_repl_crit_hours"],
                    "val_text": val_txt,
                    "start": ad_read_ts, "feed": "adjob",
                    "check": "repadmin /showrepl %s /verbose" % p["dst"],
                })
            elif ad_current:
                ok_ids.add(cid_r)

    # Reconcile open / last_known / cleared conditions in tracker
    open_list, cleared_list = tracker.reconcile(active_conds, ok_ids, now)
    open_list.sort(key=lambda c: (
        1 if c.get("last_known") else 0,
        -SEV_RANK.get(c.get("sev"), 0),
        BUCKET_ORDER.index(c.get("bucket")) if c.get("bucket") in BUCKET_ORDER else 9,
        c.get("start") or 0,
    ))
    open_list = open_list[:MAX_CONDITIONS]

    # Estate node squares grouped by SolarWinds service group (fallback: site)
    estate_groups = collections.defaultdict(list)
    for n in nodes:
        nid = n["id"]
        grps = node_groups.get(nid) or [node_site.get(nid) or no_site]
        gname = grps[0]
        estate_groups[gname].append({
            "id": nid,
            "name": n["name"],
            "ip": n.get("ip") or "",
            "site": node_site.get(nid) or no_site,
            "status": node_verdict[nid],
            "rtt": n.get("resp"),
            "loss": n.get("loss"),
            "why": n.get("status_text") or "",
        })
    estate = []
    for gname, members in estate_groups.items():
        members.sort(key=lambda m: (0 if m["status"] == "down" else 1 if m["status"] == "warn" else 2 if m["status"] == "stale" else 3, m["name"].lower()))
        d_c = sum(1 for m in members if m["status"] == "down")
        w_c = sum(1 for m in members if m["status"] == "warn")
        s_c = sum(1 for m in members if m["status"] == "stale")
        u_c = sum(1 for m in members if m["status"] == "up")
        g_st = "stale" if sw_fresh in ("stale", "not_read") else ("crit" if d_c else "warn" if w_c else "stale" if (s_c and not u_c) else "ok")
        estate.append({
            "name": gname, "status": g_st, "total": len(members),
            "up": u_c, "down": d_c, "warn": w_c, "stale": s_c,
            "nodes": members[:120],
        })
    estate.sort(key=lambda g: (-SEV_RANK.get(g["status"], 0), -g["down"], -g["warn"], g["name"].lower()))

    # Counts and overall verdict (NEVER green if core feeds are stale/unread!)
    cur_conds = [c for c in open_list if not c.get("last_known")]
    crit_cnt = sum(1 for c in cur_conds if c.get("sev") == "crit")
    warn_cnt = sum(1 for c in cur_conds if c.get("sev") == "warn")
    any_fresh = (sw_fresh == "ok") or (ad_fresh == "ok")
    any_stale = (sw_fresh in ("stale", "not_read")) or (ad_fresh in ("stale", "not_read"))
    if crit_cnt:
        overall = "crit"
    elif warn_cnt:
        overall = "warn"
    elif not any_fresh or any_stale or (swis_snap and swis_snap.get("cut")):
        overall = "stale"
    else:
        overall = "ok"

    # Record rolling 4h trend sample
    site_rtt_avg = {s: round(sum(vals) / len(vals), 1) for s, vals in site_rtts.items() if vals}
    tracker.record_trend(now, {
        "conds": len(cur_conds) if any_fresh else None,
        "down": down_cnt if sw_fresh in ("ok", "late") else None,
        "rtt": worst_rtt,
        "loss": worst_loss,
        "ad_lag_min": ad_mx["max_lag_min"] if ad_fresh in ("ok", "late") else None,
        "vol_pct": max_vol_pct,
    }, site_rtts=site_rtt_avg)

    # 4 KPI tiles matching mockup (grey 'not read' when source missing/stale)
    kpis = [
        {
            "id": "conds", "label": "Active wall conditions",
            "value": ("%d" % len(cur_conds)) if any_fresh else "Not read",
            "sub": ("%d crit · %d warn · %d suppressed" % (crit_cnt, warn_cnt, len(suppressed))) if any_fresh else "No current feed",
            "state": "crit" if crit_cnt else "warn" if warn_cnt else ("ok" if (any_fresh and not any_stale) else "stale"),
            "spark": [p.get("conds") for p in tracker.trends],
        },
        {
            "id": "nodes", "label": "Monitored nodes",
            "value": ("%d / %d" % (up_cnt, len(nodes))) if (sw_fresh in ("ok", "late") and nodes) else "Not read",
            "sub": ("%d down · %d warn · %d unman" % (down_cnt, warn_cnt, unman_cnt)) if (sw_fresh in ("ok", "late") and nodes) else (feeds[0]["why"] or "SolarWinds not read"),
            "state": "stale" if sw_fresh in ("stale", "not_read") or not nodes else ("crit" if down_cnt else "warn" if warn_cnt else ("stale" if unk_cnt else "ok")),
            "spark": [p.get("down") for p in tracker.trends],
        },
        {
            "id": "ad", "label": "AD replication",
            "value": ("%d / %d" % (ad_mx["ok_pairs"], ad_mx["total_pairs"])) if (ad_mx["read"] and ad_fresh in ("ok", "late")) else "Not read",
            "sub": (("max lag %.0f m · %d failing" % (ad_mx["max_lag_min"], ad_mx["failing"])) if ad_mx["max_lag_min"] is not None else ("%d DCs read" % len(ad_mx["dcs"]))) if (ad_mx["read"] and ad_fresh in ("ok", "late")) else (feeds[1]["why"] or "AD report DB not read"),
            "state": "stale" if (not ad_mx["read"] or ad_fresh in ("stale", "not_read")) else (
                "crit" if any(p["status"] == "crit" for p in ad_mx["pairs"]) else
                "warn" if ad_mx["failing"] else ("stale" if not ad_mx["dcs"] else "ok")),
            "spark": [p.get("ad_lag_min") for p in tracker.trends],
        },
        {
            "id": "capacity", "label": "Fullest volume",
            "value": ("%.1f%%" % max_vol_pct) if (max_vol_pct is not None and vol_state in ("ok", "late")) else "Not read",
            "sub": (max_vol_obj or "No volumes reported") if (max_vol_pct is not None and vol_state in ("ok", "late")) else (feeds[3]["why"] or "Orion.Volumes not read"),
            "state": "stale" if (max_vol_pct is None or vol_state != "ok") else (
                "crit" if max_vol_pct >= o["volume_crit"] else "warn" if max_vol_pct >= o["volume_warn"] else "ok"),
            "spark": [p.get("vol_pct") for p in tracker.trends],
        },
    ]

    timeline = []
    for c in open_list + cleared_list:
        timeline.append({
            "id": c["id"], "sev": c.get("sev"), "domain": c.get("domain"), "bucket": c.get("bucket"),
            "obj": c.get("obj"), "title": c.get("title"), "start": c.get("start"), "end": c.get("end"),
            "last_known": bool(c.get("last_known")),
        })
    timeline.sort(key=lambda x: -(x.get("start") or 0))

    return {
        "version": VERSION,
        "now": now,
        "title": title or "Network & AD health",
        "enclave": enclave,
        "state": overall,
        "poll": o["seconds"],
        "feeds": feeds,
        "kpis": kpis,
        "counts": {"crit": crit_cnt, "warn": warn_cnt, "open": len(open_list),
                   "down": sum(1 for c in open_list if c.get("bucket") == "down"),
                   "full": sum(1 for c in open_list if c.get("bucket") == "full"),
                   "err": sum(1 for c in open_list if c.get("bucket") == "err"),
                   "suppressed": len(suppressed)},
        "conds": open_list,
        "suppressed": suppressed[:MAX_SUPPRESSED],
        "ad": ad_mx,
        "estate": estate[:40],
        "trends": list(tracker.trends),
        "timeline": timeline[:80],
        "limits": {
            "volume": [o["volume_warn"], o["volume_crit"]],
            "if_err": [o["if_err_warn"], o["if_err_crit"]],
            "wan_util": [o["wan_util_warn"], o["wan_util_crit"]],
            "wan_rtt": [o["wan_rtt_warn"], o["wan_rtt_crit"]],
            "wan_loss": [o["wan_loss_warn"], o["wan_loss_crit"]],
            "ad_repl_hours": [o["ad_repl_warn_hours"], o["ad_repl_crit_hours"]],
            "sustained_minutes": o["sustained_minutes"],
            "down_polls": o["down_polls"],
        },
        "links": dict(full_links or {}),
    }


def _region_of(lat, lon):
    """Coarse geographic region label from live lat/lon for quick-zoom presets."""
    if 15 <= lat <= 72 and -170 <= lon <= -50:
        return "North America"
    if -58 <= lat < 15 and -90 <= lon <= -30:
        return "South America"
    if 35 <= lat <= 72 and -15 <= lon <= 45:
        return "Europe"
    if 12 <= lat <= 42 and 32 <= lon <= 65:
        return "Middle East"
    if -36 <= lat < 35 and -20 <= lon <= 52:
        return "Africa"
    if -50 <= lat <= 60 and (lon > 65 or lon < -130):
        return "Indo-Pacific"
    return "Other"


def build_sitemap(swis_snap, swis_meta, opts, tracker, now, netmap_opts=None, site_places=None, enclave="", title=""):
    """1.139.0: Build the live Site map model from SolarWinds nodes, Orion.WorldMap.Point lat/lon, and topology.
    - Rejects (0, 0) / invalid coordinates.
    - Never drops unplaced sites (placed: false + unplaced_why).
    - Missing/failed/stale SolarWinds feed -> every site status is 'stale' (grey), never green."""
    o = health_opts(opts)
    now = float(now)
    meta = dict(swis_meta or {})
    sw_state = meta.get("state") or ("ok" if swis_snap else "never")
    sw_asof = meta.get("asof") if meta.get("asof") is not None else (swis_snap.get("read_at") if isinstance(swis_snap, dict) else None)
    sw_why = str(meta.get("why") or "")
    sw_fresh, sw_age = feed_freshness(sw_state, sw_asof, now, o["late"], o["stale"])
    stale_feed = sw_fresh in ("stale", "not_read") or bool((swis_snap or {}).get("cut"))

    nodes = list((swis_snap or {}).get("nodes") or [])
    conns = list((swis_snap or {}).get("connections") or [])
    interfaces = list((swis_snap or {}).get("interfaces") or [])
    if_by = {i.get("id"): i for i in interfaces if i.get("id") is not None}
    node_site, coords, no_site = _assign_sites(nodes, conns, netmap_opts, site_places)
    node_by = {n["id"]: n for n in nodes if n.get("id") is not None}

    # Group nodes into sites
    site_nodes = collections.defaultdict(list)
    for n in nodes:
        site_nodes[node_site[n["id"]]].append(n)

    # Inter-site WAN links
    links = []
    seen_lk = set()
    site_wan_down = collections.defaultdict(int)
    site_wan_warn = collections.defaultdict(int)
    site_wan_util = collections.defaultdict(float)
    for c in conns:
        if len(c) < 4 or c[0] not in node_by or c[2] not in node_by:
            continue
        sa, sb = node_site[c[0]], node_site[c[2]]
        if sa == sb:
            continue
        pair_key = tuple(sorted((sa, sb))) + (str(c[1]), str(c[3]))
        if pair_key in seen_lk:
            continue
        seen_lk.add(pair_key)
        fa, fb = if_by.get(c[1]) or {}, if_by.get(c[3]) or {}
        na, nb = node_by[c[0]], node_by[c[2]]
        sta, stb = _node_status(na.get("status_code"), na.get("unmanaged")), _node_status(nb.get("status_code"), nb.get("unmanaged"))
        util = max((u for u in (fa.get("in_util"), fa.get("out_util"), fb.get("in_util"), fb.get("out_util")) if u is not None and u >= 0), default=None)
        if stale_feed:
            lst, lwhy = "stale", "SolarWinds feed not current"
        elif fa.get("oper_status") == 2 or fa.get("status") == 2 or fb.get("oper_status") == 2 or fb.get("status") == 2:
            lst, lwhy = "crit", "WAN interface operationally down"
        elif sta == "down" or stb == "down":
            lst, lwhy = "crit", "%s unreachable" % (na["name"] if sta == "down" else nb["name"])
        elif util is not None and util >= o["wan_util_crit"]:
            lst, lwhy = "crit", "WAN utilization %.0f%% (crit %.0f%%)" % (util, o["wan_util_crit"])
        elif util is not None and util >= o["wan_util_warn"]:
            lst, lwhy = "warn", "WAN utilization %.0f%%" % util
        elif fa.get("status") == 3 or fb.get("status") == 3:
            lst, lwhy = "warn", "Interface warning"
        elif util is None:
            lst, lwhy = "stale", "WAN utilization not read"
        elif sta == "unknown" or stb == "unknown":
            lst, lwhy = "stale", "endpoint status unknown"
        else:
            lst, lwhy = "ok", ""
        if lst == "crit":
            site_wan_down[sa] += 1
            site_wan_down[sb] += 1
        elif lst == "warn":
            site_wan_warn[sa] += 1
            site_wan_warn[sb] += 1
        if util is not None:
            site_wan_util[sa] = max(site_wan_util[sa], util)
            site_wan_util[sb] = max(site_wan_util[sb], util)
        rtts = [r for r in (na.get("resp"), nb.get("resp")) if r is not None and r >= 0]
        losses = [l for l in (na.get("loss"), nb.get("loss")) if l is not None and l >= 0]
        spd = min([s for s in (fa.get("speed"), fb.get("speed")) if s and s > 0] or [0])
        links.append({
            "id": "%s--%s" % (sa, sb),
            "a": sa, "b": sb,
            "na": na["name"], "nb": nb["name"],
            "ia": fa.get("name") or "", "ib": fb.get("name") or "",
            "status": lst, "why": lwhy,
            "util": round(util, 1) if util is not None else None,
            "speed_bps": spd or None,
            "rtt": round(max(rtts), 1) if rtts else None,
            "loss": round(max(losses), 1) if losses else None,
        })

    # Build site entries (placed + unplaced)
    sites = []
    region_pts = collections.defaultdict(list)
    poll_stale_s = o["poll_stale_minutes"] * 60
    for sname, snodes in site_nodes.items():
        cinfo = coords.get(sname) or {"placed": False, "lat": None, "lon": None, "source": "", "why": "no coordinate"}
        up = down = warn = unk = unman = 0
        issues = []
        rtts = []
        losses = []
        for n in snodes:
            st = _node_status(n.get("status_code"), n.get("unmanaged"))
            ls = n.get("last_sync")
            old_poll = (ls is not None and (now - ls) > poll_stale_s)
            if st == "unmanaged":
                unman += 1
            elif st == "down":
                down += 1
                issues.append({"sev": "crit", "obj": n["name"], "ip": n.get("ip") or "", "why": n.get("status_text") or "Down"})
            elif st == "warn":
                warn += 1
                issues.append({"sev": "warn", "obj": n["name"], "ip": n.get("ip") or "", "why": n.get("status_text") or "Warning"})
            elif st == "up" and not old_poll:
                up += 1
            else:
                unk += 1
            if n.get("resp") is not None and n["resp"] >= 0 and st != "down":
                rtts.append(n["resp"])
            if n.get("loss") is not None and n["loss"] >= 0:
                losses.append(n["loss"])

        max_rtt = round(max(rtts), 1) if rtts else None
        max_loss = round(max(losses), 1) if losses else None
        if stale_feed:
            sst = "stale"
        elif down or site_wan_down[sname] or (max_loss is not None and max_loss >= o["wan_loss_crit"]):
            sst = "crit"
        elif warn or site_wan_warn[sname] or (max_rtt is not None and max_rtt >= o["wan_rtt_warn"]) or (max_loss is not None and max_loss >= o["wan_loss_warn"]):
            sst = "warn"
        elif up == 0 or unk == len(snodes):
            sst = "stale"
        else:
            sst = "ok"

        reg = _region_of(cinfo["lat"], cinfo["lon"]) if cinfo["placed"] else "Unplaced"
        if cinfo["placed"]:
            region_pts[reg].append((cinfo["lat"], cinfo["lon"]))

        issues.sort(key=lambda x: (-SEV_RANK.get(x["sev"], 0), x["obj"].lower()))
        rtt_hist = list(tracker.site_rtt.get(sname) or []) if tracker is not None else []
        sites.append({
            "id": sname,
            "name": sname,
            "region": reg,
            "placed": bool(cinfo["placed"]),
            "lat": cinfo["lat"],
            "lon": cinfo["lon"],
            "coord_source": cinfo["source"],
            "unplaced_why": cinfo["why"],
            "status": sst,
            "nodes": len(snodes),
            "up": up, "down": down, "warn": warn, "stale": unk, "unmanaged": unman,
            "wan_util": round(site_wan_util[sname], 1) if sname in site_wan_util else None,
            "wan_down": site_wan_down[sname],
            "rtt": max_rtt,
            "loss": max_loss,
            "issues": issues[:20],
            "rtt_hist": rtt_hist,
        })

    sites.sort(key=lambda s: (-SEV_RANK.get(s["status"], 0), 0 if s["placed"] else 1, -s["down"], -s["warn"], s["name"].lower()))
    sites = sites[:MAX_SITES]

    # Dynamic quick-zoom region presets derived from placed live sites
    regions = [{"id": "all", "name": "All sites", "count": sum(1 for s in sites if s["placed"])}]
    for rname, pts in sorted(region_pts.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        lats = [p[0] for p in pts]
        lons = [p[1] for p in pts]
        regions.append({
            "id": re.sub(r"[^a-z0-9]+", "-", rname.lower()).strip("-") or "region",
            "name": rname,
            "count": len(pts),
            "bounds": [min(lats), min(lons), max(lats), max(lons)],
        })

    counts = {
        "all": len(sites),
        "placed": sum(1 for s in sites if s["placed"]),
        "unplaced": sum(1 for s in sites if not s["placed"]),
        "crit": sum(1 for s in sites if s["status"] == "crit"),
        "warn": sum(1 for s in sites if s["status"] == "warn"),
        "stale": sum(1 for s in sites if s["status"] == "stale"),
        "ok": sum(1 for s in sites if s["status"] == "ok"),
    }
    if not sites or stale_feed:
        overall = "stale"
    elif counts["crit"]:
        overall = "crit"
    elif counts["warn"]:
        overall = "warn"
    elif counts["stale"] == counts["all"]:
        overall = "stale"
    else:
        overall = "ok"

    return {
        "version": VERSION,
        "now": now,
        "title": title or "Site map",
        "enclave": enclave,
        "state": overall,
        "poll": o["seconds"],
        "feed": {
            "id": "swis", "name": "SolarWinds SWIS", "state": sw_state, "fresh": sw_fresh,
            "asof": sw_asof, "age_s": round(sw_age, 1) if sw_age is not None else None,
            "late": o["late"], "stale": o["stale"],
            "why": sw_why or ("SolarWinds not configured (attach SolarWinds read-only credential)" if sw_state == "never" else ""),
            "not_read": dict((swis_snap or {}).get("not_read") or {}),
        },
        "counts": counts,
        "regions": regions,
        "sites": sites,
        "links": links,
        "limits": {
            "wan_util": [o["wan_util_warn"], o["wan_util_crit"]],
            "wan_rtt": [o["wan_rtt_warn"], o["wan_rtt_crit"]],
            "wan_loss": [o["wan_loss_warn"], o["wan_loss_crit"]],
        },
    }


# ---- 1.139.0: Shared background runner (one SWIS poller + one AD DB poller) ----------------------------------
class HealthBoard(object):
    """1.139.0: Background reader serving both /api/wall/health and /api/sitemap without double-polling SolarWinds.
    Runs while viewed (stops after IDLE_STOP), backs off on auth failure, never blocks the HTTP thread."""

    def __init__(self, cfg, log, ad_db=None, reader=None, netlive=None):
        self.cfg = cfg
        self.log = log
        self.ad_db = ad_db
        self.netlive = netlive
        self.opts = health_opts(getattr(cfg, "wall_health_opts", None) or getattr(cfg, "wall_opts", None))
        self.tracker = HealthTracker()
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.seen = 0.0
        self.thread = None
        self.swis_snap = None
        self.swis_meta = {"state": "never", "asof": None, "why": ""}
        self.ad_run = None
        self.ad_meta = {"state": "never", "asof": None, "why": ""}
        self.swis_fails = 0
        self.swis_auth_until = 0.0
        self.ad_at = 0.0
        self.swis_at = 0.0
        self.force_until = 0.0

        swis_url = getattr(cfg, "health_swis_url", getattr(cfg, "swis_url", ""))
        if reader is not None:
            self.reader = reader
        elif swis_url and getattr(cfg, "swis_user", "") and getattr(cfg, "swis_password", "") and site_swis is not None:
            self.reader = SwisHealthReader(
                swis_url, cfg.swis_user, cfg.swis_password,
                verify=getattr(cfg, "swis_verify", True),
                ca=getattr(cfg, "swis_ca", None),
                timeout=getattr(cfg, "swis_timeout", 60),
                netmap_opts=getattr(cfg, "netmap_opts", None),
                opts=self.opts,
            )
        else:
            self.reader = None
            why = ('no SolarWinds login: attach the "SolarWinds (read-only)" credential on Site portal - deploy'
                   if not (swis_url and getattr(cfg, "swis_user", "") and getattr(cfg, "swis_password", ""))
                   else "the SolarWinds reader (site_swis.py) is missing")
            self.swis_meta = {"state": "never", "asof": None, "why": why}

        if self.ad_db is None and not getattr(cfg, "report_db", None):
            self.ad_meta = {"state": "never", "asof": None,
                            "why": 'no report database login: attach the "Report database" credential on Site portal - deploy'}

    def viewed(self, force=False):
        now = time.time()
        with self.lock:
            self.seen = now
            if force and now >= self.force_until:
                self.swis_at = 0.0
                self.ad_at = 0.0
                self.force_until = now + 15.0
            if (self.reader is not None or self.ad_db is not None) and (self.thread is None or not self.thread.is_alive()):
                self.thread = threading.Thread(target=self._loop, name="wall-health", daemon=True)
                self.thread.start()
        self.wake.set()

    def _poll_swis(self, now):
        if self.reader is None:
            return
        if now < self.swis_auth_until:
            return
        try:
            snap = self.reader.read(now=now)
            with self.lock:
                self.swis_snap = snap
                self.swis_meta = {"state": "ok", "asof": snap.get("read_at") or now, "why": ""}
                self.swis_fails = 0
                self.swis_at = now
        except Exception as exc:  # pylint: disable=broad-except
            msg = str(exc)
            is_auth = getattr(exc, "kind", "") == "auth" or "401" in msg or "403" in msg
            with self.lock:
                self.swis_fails += 1
                self.swis_meta = {
                    "state": "auth" if is_auth else "error",
                    "asof": self.swis_snap.get("read_at") if self.swis_snap else None,
                    "why": "SolarWinds login refused - check credential" if is_auth else ("SolarWinds read failed: %s" % msg[:180]),
                }
                self.swis_at = now
                if is_auth:
                    self.swis_auth_until = now + getattr(self.cfg, "wall_auth_retry", 900)
            if self.log:
                self.log({"event": "wall-health", "source": "swis", "state": self.swis_meta["state"], "why": self.swis_meta["why"]})

    def _poll_ad(self, now):
        if self.ad_db is None:
            return
        try:
            run, why = self.ad_db.read_wall(getattr(self.cfg, "ad_template", 0))
            with self.lock:
                if run is not None:
                    self.ad_run = run
                    self.ad_meta = {"state": "ok", "asof": parse_ts(run.get("read_at")), "why": why or ""}
                elif why:
                    self.ad_meta = {
                        "state": "never" if self.ad_run is None else "error",
                        "asof": parse_ts(self.ad_run.get("read_at")) if self.ad_run else None,
                        "why": why,
                    }
                self.ad_at = now
        except Exception as exc:  # pylint: disable=broad-except
            with self.lock:
                self.ad_meta = {
                    "state": "error",
                    "asof": parse_ts(self.ad_run.get("read_at")) if self.ad_run else None,
                    "why": "AD report DB read failed: %s" % str(exc)[:180],
                }
                self.ad_at = now
            if self.log:
                self.log({"event": "wall-health", "source": "adjob", "state": "error", "why": self.ad_meta["why"]})

    def _loop(self):
        while True:
            now = time.time()
            with self.lock:
                idle = now - self.seen
                do_sw = self.reader is not None and (now - self.swis_at >= self.opts["seconds"])
                do_ad = self.ad_db is not None and (now - self.ad_at >= max(60, self.opts["seconds"]))
            if idle > IDLE_STOP:
                return
            if do_sw:
                self._poll_swis(now)
            if do_ad:
                self._poll_ad(now)
            self.wake.wait(timeout=min(15.0, float(self.opts["seconds"])))
            self.wake.clear()

    def _swis_fallback(self):
        """If NetLive already has a fresh raw SolarWinds snapshot and HealthBoard hasn't completed its first poll, reuse it."""
        if self.swis_snap is not None or self.netlive is None or self.swis_meta["state"] != "never":
            return self.swis_snap, self.swis_meta
        with self.netlive.lock:
            if self.netlive.stale:
                return self.swis_snap, self.swis_meta
            raw = self.netlive.raw
            at = self.netlive.read_at
        if isinstance(raw, dict) and raw.get("nodes"):
            snap = {
                "read_at": at or parse_ts(raw.get("read_at")),
                "url": raw.get("url") or "",
                "nodes": [dict(n, last_sync=None) for n in raw.get("nodes") or []],
                "cut": bool(raw.get("cut")),
                "volumes": [], "volumes_read": False,
                "interfaces": [{"id": i.get("InterfaceID"), "node_id": i.get("NodeID"),
                                "name": str(i.get("Caption") or i.get("Name") or ""),
                                "speed": _num(i.get("Speed")), "status": i.get("Status"), "oper_status": i.get("OperStatus"),
                                "in_util": _num(i.get("InPercentUtil")), "out_util": _num(i.get("OutPercentUtil"))}
                               for i in raw.get("interfaces") or []],
                "interfaces_read": bool(raw.get("interfaces")),
                "connections": raw.get("connections") or [],
                "topology": raw.get("topology") or "",
                "topology_ok": bool(raw.get("topology_ok")),
                "groups": {},
                "not_read": {"volumes": "waiting for health collector read (Orion.Volumes)"},
            }
            return snap, {"state": "ok", "asof": snap["read_at"], "why": ""}
        return self.swis_snap, self.swis_meta

    def health_model(self, now=None):
        now = time.time() if now is None else float(now)
        with self.lock:
            snap, sw_meta = self._swis_fallback()
            ad_run, ad_meta = self.ad_run, dict(self.ad_meta)
            links = {
                "ad": (self.cfg.path + "ad") if getattr(self.cfg, "ad", False) else "",
                "netmap": (self.cfg.path + "netmap") if (getattr(self.cfg, "netmap", False) and not getattr(self.cfg, "netmap_why", "")) else "",
                "sitemap": (self.cfg.path + "sitemap") if getattr(self.cfg, "site_map", True) else "",
                "wall": (self.cfg.path + "wall") if getattr(self.cfg, "wall", False) else "",
            }
            return build_health_model(
                snap, sw_meta, ad_run, ad_meta, self.opts, self.tracker, now,
                netmap_opts=getattr(self.cfg, "netmap_opts", None),
                site_places=(getattr(self.cfg, "wall_opts", None) or {}).get("site_places"),
                enclave=getattr(self.cfg, "wall_enclave", ""),
                title=getattr(self.cfg, "wall_title", "Ops center"),
                full_links=links,
            )

    def sitemap_model(self, now=None):
        now = time.time() if now is None else float(now)
        with self.lock:
            snap, sw_meta = self._swis_fallback()
            return build_sitemap(
                snap, sw_meta, self.opts, self.tracker, now,
                netmap_opts=getattr(self.cfg, "netmap_opts", None),
                site_places=(getattr(self.cfg, "wall_opts", None) or {}).get("site_places"),
                enclave=getattr(self.cfg, "wall_enclave", ""),
                title="Site map",
            )

    def answer_health(self, etag_in="", now=None):
        m = self.health_model(now=now)
        m["zone"] = getattr(self.cfg, "tz_name", "") or "UTC"
        body = json.dumps(m, separators=(",", ":"), ensure_ascii=False, default=str)
        tag = '"%s"' % hashlib.sha256(re.sub(r'"now":[0-9.]+,', "", body).encode("utf-8")).hexdigest()[:20]
        return (304, "", tag) if _etag_matches(etag_in, tag) else (200, body, tag)

    def answer_sitemap(self, etag_in="", now=None):
        m = self.sitemap_model(now=now)
        m["zone"] = getattr(self.cfg, "tz_name", "") or "UTC"
        body = json.dumps(m, separators=(",", ":"), ensure_ascii=False, default=str)
        tag = '"%s"' % hashlib.sha256(re.sub(r'"now":[0-9.]+,', "", body).encode("utf-8")).hexdigest()[:20]
        return (304, "", tag) if _etag_matches(etag_in, tag) else (200, body, tag)


def _etag_matches(header, tag):
    return any(item.strip() in (tag, "W/" + tag, "*") for item in str(header or "").split(","))


def extend_wall(result, health_m):
    """1.139.0: Project live Network & AD health summary into /api/wall's ad, network, and health areas
    without breaking existing VMware/Storage/Ops monitor fields."""
    if not isinstance(result, dict) or not isinstance(health_m, dict):
        return result
    feeds = result.setdefault("feeds", {})
    by_feed = {f["id"]: f for f in health_m.get("feeds") or [] if isinstance(f, dict) and f.get("id")}
    sw = by_feed.get("swis") or {}
    ad = by_feed.get("adjob") or {}
    sw_live = sw.get("state") not in ("never", "off") or bool(sw.get("asof"))
    ad_live = ad.get("state") not in ("never", "off") or bool(ad.get("asof"))

    if sw_live:
        feeds["sw:health"] = {
            "name": sw.get("name") or "SolarWinds SWIS",
            "state": sw.get("state") or "never",
            "asof": sw.get("asof"),
            "fresh": sw.get("fresh") or "stale",
            "why": sw.get("why") or "",
            "late": sw.get("late"),
            "stale": sw.get("stale"),
        }
    if ad_live:
        feeds["ad:db"] = {
            "name": ad.get("name") or "AD Health Report DB",
            "state": ad.get("state") or "never",
            "asof": ad.get("asof"),
            "fresh": ad.get("fresh") or "stale",
            "why": ad.get("why") or "",
            "late": ad.get("late"),
            "stale": ad.get("stale"),
        }

    # Add health area descriptor (Network & AD health)
    area_feeds = ([k for k in ("sw:health", "ad:db") if k in feeds])
    areas = result.setdefault("areas", [])
    for a in areas:
        if a.get("id") == "ad" and ad_live:
            a["on"] = True
            a["built"] = True
            a["feeds"] = ["ad:db"]
            a["complete"] = ad.get("fresh") == "ok"
            a["why"] = ad.get("why") or ""
        elif a.get("id") == "network" and sw_live:
            a["on"] = True
            a["built"] = True
            a["feeds"] = ["sw:health"]
            a["complete"] = sw.get("fresh") == "ok"
            a["why"] = sw.get("why") or ""

    if not any(a.get("id") == "health" for a in areas):
        areas.append({
            "id": "health",
            "name": "Network & AD health",
            "short": "Net & AD",
            "team": "Network / AD",
            "on": True,
            "built": True,
            "feeds": area_feeds,
            "not_on_wall": "",
            "complete": health_m.get("state") != "stale" and sw.get("fresh") == "ok" and ad.get("fresh") == "ok",
            "incomplete": [] if area_feeds else ["no live SolarWinds or AD report DB feed yet"],
            "why": "" if area_feeds else "Waiting for SolarWinds / AD report DB feed",
        })

    # Project active conditions from health wall into wall conds (preserving unique ids)
    existing_ids = {c.get("id") for c in result.get("conds") or [] if isinstance(c, dict)}
    for c in health_m.get("conds") or []:
        cid = c.get("id")
        if not cid or cid in existing_ids:
            continue
        existing_ids.add(cid)
        area = "ad" if c.get("domain") == "ad" else "network"
        feed_key = "ad:db" if area == "ad" else "sw:health"
        result.setdefault("conds", []).append({
            "id": cid,
            "kind": "ad_" + str(c.get("bucket") or "err") if area == "ad" else "net_" + str(c.get("bucket") or "err"),
            "area": area,
            "sev": c.get("sev") or "warn",
            "t": "%s: %s" % (c.get("obj") or "", c.get("title") or ""),
            "obj": c.get("obj") or "",
            "site": c.get("site") or "",
            "v": c.get("val"),
            "unit": c.get("unit") or "",
            "warn": c.get("warn"),
            "crit": c.get("crit"),
            "start": c.get("start"),
            "end": c.get("end"),
            "feed": feed_key if feed_key in feeds else "",
            "last_known": bool(c.get("last_known")),
            "check": c.get("check") or "",
        })

    result["health"] = {
        "state": health_m.get("state") or "stale",
        "counts": health_m.get("counts") or {},
        "kpis": health_m.get("kpis") or [],
        "feeds": health_m.get("feeds") or [],
    }
    return result
