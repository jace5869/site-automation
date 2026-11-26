"""The portal's ops-center wall (/portal/wall, docs/SITE_PORTAL.md "The wall"): the one model every style of the page draws.

Its inputs: the live collector's view (wall_live.Collector.view(): each vCenter's last good read and the short history of
host and VM CPU and memory), the NetApp page's newest run in the report database (netapp_live.Database.read()), and the
settings (the thresholds). Its answer: the wall conditions open now and cleared in the last 4 hours, each area's feeds with
"as of", and the levels the gauges show. The rules, one place for all of them:
  - a condition is on the wall only when a wall rule says so (hosts not responding, a datastore 85 / 95 % used, CPU or memory
    90 % or more for 15 min or longer ...); routine alarms and short spikes never are;
  - freshness is per feed: LATE after 3 min, STALE after 10 min; a stale feed's values are the last known ones, never green;
  - a condition keeps its start while it stays open (Tracker); one that goes away is "cleared" for 4 hours (the Map's timeline).
Standard library only; no I/O (the caller reads, this works it out), so the tests drive it with made-up reads."""
import collections
import fnmatch
import math
import re

try:
    import site_swis                       # the network map's own matching (plugins/module_utils): one rule for both maps
except ImportError:                        # pragma: no cover - the portal image has it
    site_swis = None
try:
    import wall_fleet                      # 1.136.0: the Ops monitor area's conditions and summary
except ImportError:                        # pragma: no cover - an older deploy's files: no Ops monitor area
    wall_fleet = None

LATE_S = 180                     # amber (LATE) after 3 min
STALE_S = 600                    # STALE (not current, never green) after 10 min
KEEP_CLEARED_S = 4 * 3600        # cleared conditions on the Map's timeline
AUTO_SITE = r"^([A-Za-z]{4}[0-9]{2})"
DOWN_STATES = ("notResponding", "disconnected")
# GREEN ONLY FROM AN ALLOW-LIST (Codex verify 4): each object is OK only when its fields say exactly so; a known bad value raises
# its condition; anything else (missing, None, "gray", an unexpected word) is UNKNOWN - the area not whole, grey, never OK, and
# never what clears a condition
POWER_KNOWN = ("poweredOn", "poweredOff", "suspended")
SM_GOOD = ("snapmirrored", "in_sync")
STATE_BAD = ("offline", "restricted", "error", "mixed", "nvfail", "space_error", "foreign_lun_error", "aggregate_offline", "volume_offline")
NODE_BAD = ("down", "taken_over", "waiting_for_giveback", "degraded")

AREAS = (
    {"id": "storage", "name": "Storage", "sub": "NetApp", "team": "Storage team"},
    {"id": "vmware", "name": "VMware", "sub": "vCenter", "team": "VMware team"},
    {"id": "vdi", "name": "VDI", "sub": "Horizon", "team": "VDI team"},
    {"id": "ad", "name": "Active Directory", "short": "AD", "sub": "", "team": "AD team"},
    {"id": "tickets", "name": "Outages & problems", "short": "Outages & PRB", "sub": "ServiceNow", "team": "Service desk"},
)
BUILT = ("storage", "vmware")    # stage 1: the others are grey tiles "not on the wall yet"
STORAGE_OFF = "not on the wall yet: no live ONTAP cluster (portal_wall_ontap_clusters) - the wall's Storage is read live only"
NOT_ON_WALL = {
    "storage": "EMS informational events, snapshot counts, autosupport.",
    "vmware": "vCenter informational alarms, short spikes (under the sustained window), snapshot age, tools out of date.",
    "vdi": "not on the wall yet.", "ad": "not on the wall yet.", "tickets": "not on the wall yet.",
    "monitor": "a node's own alerts (CPU, disk, interfaces), SolarWinds events; a single node down lights its group, never a wall of its own.",
}
# 1.136.0: Ops monitor (wall_fleet.py) - on the wall only with portal_wall_monitor (auto: when SolarWinds is configured)
FLEET_AREA = {"id": "monitor", "name": "Ops monitor", "short": "Monitor", "sub": "SolarWinds", "team": "Service owners"}
FLEET_KINDS = {
    "fleetdown": {"area": "monitor", "sev": "crit", "label": "A node of a service group down in SolarWinds (powered off in vCenter: never down)", "grp": "fleet"},
    "fleetwarn": {"area": "monitor", "sev": "warn", "label": "A node of a service group warning in SolarWinds (packet loss or slow answers)", "grp": "fleet"},
}

# 1.131.1 (Codex #11): vCenter's default hardware alarm definitions, by whole name (any case) - portal_wall_hw_alarms
HW_ALARMS = ["Host hardware fan status", "Host hardware power status", "Host hardware temperature status",
             "Host hardware voltage", "Host hardware system board status", "Host memory status", "Host processor status",
             "Host storage status", "Host battery status", "Host IPMI System Event Log status",
             "Host Baseboard Management Controller status", "Status of other host hardware objects"]

DEFAULTS = {"ds_warn": 85.0, "ds_crit": 95.0, "aggr_warn": 85.0, "aggr_crit": 97.0, "high_pct": 90.0, "high_minutes": 15,
            "exclude": {}, "ds_ignore": ["iso", "isos", "iso-*", "iso_*", "*-iso", "*_iso", "*-isos", "*content-library*"], "snapmirror_lag_warn_hours": 24.0, "snapmirror_lag_crit_hours": 72.0,
            "seconds": 60, "maintenance_expected": [], "vm_heartbeat": True, "site_pattern": "", "site_places": {},
            "netmap_sites": {}, "hw_alarms": list(HW_ALARMS), "late": LATE_S, "stale": STALE_S}


def opts_of(o):
    """the settings with their defaults; a wrong value falls back to the default (the deploy checks them first)"""
    out = dict(DEFAULTS)
    for k, v in (o or {}).items():
        if k not in DEFAULTS or v is None:
            continue
        d = DEFAULTS[k]
        try:
            if isinstance(d, bool):
                out[k] = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
            elif isinstance(d, float):
                out[k] = float(v)
            elif isinstance(d, int):
                out[k] = int(v)
            elif isinstance(d, list):
                out[k] = [str(x) for x in v] if isinstance(v, (list, tuple)) else list(d)
            elif isinstance(d, dict):
                out[k] = dict(v) if isinstance(v, dict) else {}
            else:
                out[k] = str(v)
        except (TypeError, ValueError, OverflowError):
            pass
    if not 0 < out["ds_warn"] < out["ds_crit"] <= 100:
        out["ds_warn"], out["ds_crit"] = DEFAULTS["ds_warn"], DEFAULTS["ds_crit"]
    if not 0 < out["aggr_warn"] < out["aggr_crit"] <= 100:
        out["aggr_warn"], out["aggr_crit"] = DEFAULTS["aggr_warn"], DEFAULTS["aggr_crit"]
    if not 0 <= out["snapmirror_lag_warn_hours"] < out["snapmirror_lag_crit_hours"] < math.inf:
        for k in ("snapmirror_lag_warn_hours", "snapmirror_lag_crit_hours"):
            out[k] = DEFAULTS[k]
    out["high_pct"] = min(100.0, max(1.0, out["high_pct"]))
    out["high_minutes"] = max(1, min(240, out["high_minutes"]))
    out["seconds"] = max(30, min(3600, out["seconds"]))
    return out


def kinds(o):
    """the wall conditions with the thresholds in force: what lights the wall, said on "What the wall watches"."""
    hp, hm = _n(o["high_pct"]), o["high_minutes"]
    lag_hours = (_n(o["snapmirror_lag_warn_hours"]), _n(o["snapmirror_lag_crit_hours"]))
    return {
        "vc": {"area": "vmware", "sev": "crit", "label": "vCenter not answering, or its login failed", "grp": "hostvc"},
        "host": {"area": "vmware", "sev": "crit", "label": "Host not responding or disconnected", "grp": "hostvc"},
        "hardware": {"area": "vmware", "sev": "crit", "levels": "warning / critical", "label": "Host hardware (a hardware alarm red or yellow)", "grp": "hostvc"},
        "maint": {"area": "vmware", "sev": "warn", "label": "Host in maintenance mode (not listed as expected)", "grp": "hostvc"},
        "dsi": {"area": "vmware", "sev": "crit", "label": "Datastore inaccessible", "grp": "dsvm"},
        "vmhung": {"area": "vmware", "sev": "crit", "label": "Watched VM lost its Tools heartbeat or Tools stopped", "grp": "dsvm"},
        "cpu": {"area": "vmware", "sev": "warn", "label": "Host or VM CPU %s %% or more for %d min or more" % (hp, hm), "grp": "load"},
        "mem": {"area": "vmware", "sev": "warn", "label": "Host or VM memory %s %% or more for %d min or more" % (hp, hm), "grp": "load"},
        "dsfull": {"area": "storage", "sev": "crit", "label": "Datastore %s %% used or more" % _n(o["ds_crit"]), "grp": "ds"},
        "dswarn": {"area": "storage", "sev": "warn", "label": "Datastore %s %% used or more" % _n(o["ds_warn"]), "grp": "ds"},
        "aggrfull": {"area": "storage", "sev": "crit", "label": "Aggregate %s %% used or more" % _n(o["aggr_crit"]), "grp": "aggr"},
        "aggrwarn": {"area": "storage", "sev": "warn", "label": "Aggregate %s %% used or more" % _n(o["aggr_warn"]), "grp": "aggr"},
        "voloff": {"area": "storage", "sev": "crit", "label": "Volume or LUN offline", "grp": "aggr"},
        "node": {"area": "storage", "sev": "crit", "label": "Storage node down or cluster not answering", "grp": "aggr"},
        "sm": {"area": "storage", "sev": "warn", "levels": "warning / critical", "grp": "sm",
               "label": "SnapMirror broken, unhealthy or lag over %s / %s hours" % lag_hours},
    }


def _n(x):
    return ("%d" % x) if float(x) == int(x) else ("%.1f" % x)


def fstate(age, o=None, f=None):
    """a feed's age (s, None = never read) -> ok | late | stale (f: the feed - 1.136.0: its own late / stale when it has them)"""
    late, stale = (o or DEFAULTS)["late"], (o or DEFAULTS)["stale"]
    if isinstance(f, dict):
        late = f["late"] if isinstance(f.get("late"), (int, float)) and not isinstance(f.get("late"), bool) else late
        stale = f["stale"] if isinstance(f.get("stale"), (int, float)) and not isinstance(f.get("stale"), bool) else stale
    if age is None:
        return "stale"
    return "stale" if age > stale else "late" if age > late else "ok"


def level(v, warn, crit):
    return "crit" if v >= crit else "warn" if v >= warn else "ok"


def site_rule(pattern):
    try:
        rx = re.compile(str(pattern or "").strip() or AUTO_SITE)
    except re.error:
        return None
    return rx if rx.groups >= 1 else None


def _lonlat(v):
    """a netmap_sites value {lat, lon} -> [lon, lat] (the wall's order); anything else (not a number, NaN, inf, too big,
    out of range): None"""
    try:
        lat, lon = float(v.get("lat")), float(v.get("lon"))
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None
    return [lon, lat] if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def _glob(pattern, name):
    rx = "^" + re.escape(str(pattern).casefold()).replace(r"\*", ".*").replace(r"\?", ".") + "$"
    return re.match(rx, str(name).casefold()) is not None


def _netmap_place(conf, name):
    """the network map's own rule (site_swis.place): the site's name first (any case), else the MOST specific pattern"""
    if site_swis is not None:
        return site_swis.place(conf, name)
    for k, v in conf.items():
        if not re.search(r"[*?]", k) and k.casefold() == str(name).casefold():
            return v
    best = None
    for k, v in conf.items():
        if re.search(r"[*?]", k) and _glob(k, name):
            n = len(re.sub(r"[*?]", "", k))
            if best is None or n > best[0]:
                best = (n, v)
    return best[1] if best else None


def netmap_ignored(o):
    """the netmap_sites keys the wall cannot use (lat / lon not a number in range) - logged once by the portal"""
    nm = o.get("netmap_sites") if isinstance(o.get("netmap_sites"), dict) else {}
    return sorted(str(k) for k, v in nm.items() if _lonlat(v) is None)


def place_of(site, o):
    """1.131.0: where the Map puts a site - portal_wall_site_places first (its own key, any case), else the network map's
    netmap_sites ({lat, lon}; a name in any case first, then a pattern with * and ?, as the network map matches), else None"""
    s = str(site or "")
    own = o.get("site_places") or {}
    if s in own:
        return own[s]
    for k, v in own.items():
        if str(k).casefold() == s.casefold():
            return v
    nm = o.get("netmap_sites") if isinstance(o.get("netmap_sites"), dict) else {}
    conf = {}
    for k, v in nm.items():
        p = _lonlat(v)
        if p is not None:                  # an entry that is no place is left out (the network map does the same)
            conf[re.sub(r"\s+", " ", str(k).strip())] = p
    return _netmap_place(conf, s)


def netmap_places(o):
    """how many netmap_sites entries the wall can use (valid {lat, lon}) - the deploy summary's count"""
    return sum(1 for v in (o.get("netmap_sites") or {}).values() if _lonlat(v) is not None)




def hardware_of(h, o):
    """a host's hardware light: red / yellow from its triggered hardware alarms (whole names), green when none of them is
    triggered and every triggered alarm's name was read, None (grey) when a triggered alarm's name is unknown and no hardware
    alarm is red. h["alarms"] is set to the hardware alarms' names. A host without "triggered" (an older read): its
    "hardware" value as given."""
    if "triggered" not in h:
        return h.get("hardware")
    want = set(str(x).strip().casefold() for x in (o.get("hw_alarms") or HW_ALARMS))
    names, colors, unknown = [], set(), False
    for item in h.get("triggered") or []:
        nm, color = (list(item) + [None, None])[:2] if isinstance(item, (list, tuple)) else (None, None)
        if not isinstance(nm, str) or not nm.strip():
            unknown = True
        elif nm.strip().casefold() in want:
            names.append(nm.strip())
            colors.add(color)
    h["alarms"] = sorted(set(names))
    if "red" in colors:
        return "red"
    if unknown:
        return None
    return "yellow" if "yellow" in colors else "green"


def site_of(rx, *names):
    for n in names:
        m = rx.search(str(n or "")) if rx is not None else None
        if m and m.group(1):
            return m.group(1).upper()
    return ""


# ---- sustained high: from the collector's own samples ------------------------------------------------------------------
def run_of(samples, thr, seconds):
    """samples [(t, pct)], oldest first -> (now value, start of the run at or over thr that reaches the newest sample, or None).
    A gap longer than 2.5 polls breaks a run (nobody knows what it was then)."""
    if not samples:
        return None, None
    t_last, v_last = samples[-1]
    if v_last is None or v_last < thr:
        return v_last, None
    start, prev = t_last, t_last
    for t, v in reversed(samples[:-1]):
        if v is None or v < thr or prev - t > 2.5 * seconds:
            break
        start, prev = t, t
    return v_last, start


def held(start, t_last, seconds):
    """how long a run has held (s): from its first sample to its newest, plus the poll that newest sample stands for"""
    return (t_last - start) + seconds


def spark(samples, now, minutes=60):
    """the last hour, one value a minute (None where there was no sample), for the sparkline"""
    out = [None] * minutes
    for t, v in samples:
        i = minutes - 1 - int((now - t) // 60)
        if 0 <= i < minutes and v is not None:
            out[i] = round(float(v), 1)
    return out


# ---- the tracker: when a condition started, which ones cleared ---------------------------------------------------------------
class Tracker(object):
    """Conditions by their id: the start stays while one is open (the earliest seen). A condition clears ONLY when the same
    object is seen OK (its id in `ok`: the object in a fresh, whole read, passing the allow-list) - kept as cleared for
    KEEP_CLEARED_S. One that is no longer in the read but was not seen OK stays open, marked last_known (Codex verify 4: loss
    of data is never a recovery). Memory only: a restart of the portal starts again."""

    def __init__(self, keep=KEEP_CLEARED_S):
        self.keep, self.open, self.cleared = keep, {}, collections.OrderedDict()
        self.aliases = set()
        self.first = None                      # the first read it saw: "watching since" (memory only)

    def merge_vcenter(self, alias, name):
        """Rekey prior observations when two authenticated endpoints prove to be the same inventory."""
        if (alias, name) in self.aliases:
            return
        self.aliases.add((alias, name))
        for conditions in (self.open, self.cleared):
            for old_id, c in list(conditions.items()):
                if c.get("vc") != alias:
                    continue
                prefix = c["kind"] + ":"
                if c["kind"] in ("cpu", "mem"):
                    prefix += c["obj_kind"] + ":"
                elif c["kind"] in ("dswarn", "dsfull"):
                    prefix = "ds:"
                cid = prefix + name + old_id[len(prefix + alias):]
                previous = conditions.get(cid)
                c = dict(c, vc=name, id=cid)
                if c.get("feed"):
                    c["feed"] = "vc:" + name
                if previous:
                    c["start"] = min(previous["start"], c["start"])
                del conditions[old_id]
                conditions[cid] = c

    def update(self, conds, now, ok=()):
        ok = set(ok or ())
        if self.first is None:
            self.first = now
        seen = set()
        for c in conds:
            cid = c["id"]
            seen.add(cid)
            old = self.open.get(cid)
            start = c.get("start") or now
            if old is not None:
                start = min(old["start"], start)
            c["start"] = start
            self.open[cid] = dict(c)
            self.cleared.pop(cid, None)
        kept = []
        for cid in [k for k in self.open if k not in seen]:
            if cid not in ok:
                kept.append(dict(self.open[cid], last_known=True))     # not seen OK: open, as last known
                continue
            c = self.open.pop(cid)
            c["end"] = now
            self.cleared[cid] = c
        for cid in [k for k, c in self.cleared.items() if now - c["end"] > self.keep]:
            self.cleared.pop(cid)
        return conds + kept + [dict(c) for c in self.cleared.values()]


# ---- the model -----------------------------------------------------------------------------------------------------------------
def _vc_feed(src):
    return "vc:" + str(src.get("name") or "")


def matches(name, patterns):
    return isinstance(patterns, list) and any(isinstance(p, str) and fnmatch.fnmatchcase(str(name).casefold(), p.casefold()) for p in patterns)


def excluded(part, o, kind, name, source, reason=""):
    if matches(name, o["exclude"].get(kind)):
        reason = "excluded by portal_wall_exclude"
    if reason:
        part["excluded"].append({"kind": kind, "name": name, "source": source, "reason": reason})
    return bool(reason)


def vmware_part(view, o, now):
    """the vCenters' last good reads -> (part, conds, feeds, ok): ok = [(feed, condition id)] of the objects seen OK"""
    conds, feeds, ok = [], {}, []
    part = {"configured": False, "vcenters": [], "clusters": [], "hosts": [], "datastores": [], "hot": [], "building": [], "vms": 0,
            "incomplete": [], "excluded": [], "heartbeat_unwatched": 0, "heartbeat_watched": 0}
    srcs = [dict(s, key=k) for k, s in ((view or {}).get("sources") or {}).items() if s.get("kind") == "vcenter"]
    hist = (view or {}).get("history") or {}
    if not srcs:
        return part, conds, feeds, ok
    part["configured"] = True
    rx = site_rule(o["site_pattern"])
    thr, win, sec = o["high_pct"], o["high_minutes"] * 60, o["seconds"]
    for src in sorted(srcs, key=lambda s: str(s.get("name"))):
        name, fk = str(src.get("name") or ""), _vc_feed(src)
        state = src.get("state") or "never"
        feeds[fk] = {"name": "vCenter " + name, "asof": src.get("at"), "state": state, "why": src.get("why") or "",
                     "src": "live, read every %d s" % sec, "fail_since": src.get("fail_since"), "next_try": src.get("next_try")}
        part["vcenters"].append({"name": name, "up": state == "ok", "state": state, "why": src.get("why") or "", "feed": fk})
        if state == "ok":
            ok.append((fk, "vc:" + name))
        if state in ("auth", "error"):
            why = (src.get("why") or "login failed - check the credential") if state == "auth" else (src.get("why") or "not answering")
            conds.append({"id": "vc:" + name, "kind": "vc", "t": "vCenter %s %s" % (name, "login failed" if state == "auth" else "not answering"),
                          "obj": why, "start": src.get("fail_since") or now, "feed": "", "vc": name, "site": site_of(rx, name)})
        snap = src.get("snap") or {}
        initial = len(part["incomplete"])
        if not src.get("snap"):
            part["incomplete"].append("vCenter %s: no inventory read yet" % name)
        hosts = [h for h in snap.get("hosts") or [] if not excluded(part, o, "hosts", h.get("name"), name)]
        by_cluster = collections.OrderedDict()
        for h in hosts:
            by_cluster.setdefault(h.get("cluster") or "(standalone)", []).append(h)
        for cn, hs in by_cluster.items():
            up = [h for h in hs if h.get("state") == "connected"]          # Codex 1.130.0: only a host vCenter says is connected is up
            cpus = [h["cpu_pct"] for h in up if h.get("cpu_pct") is not None]
            mems = [h["mem_pct"] for h in up if h.get("mem_pct") is not None]
            part["clusters"].append({"n": cn, "vc": name, "feed": fk, "up": len(up), "of": len(hs),
                                     "cpu": round(sum(cpus) / len(cpus), 1) if cpus else None,
                                     "mem": round(sum(mems) / len(mems), 1) if mems else None,
                                     "down": [h["name"] for h in hs if h.get("state") in DOWN_STATES],
                                     "unknown": [h["name"] for h in hs if h.get("state") not in DOWN_STATES + ("connected",)],
                                     "maint": [h["name"] for h in hs if h.get("maint") is True], "site": site_of(rx, cn, hs[0].get("name"))})
        for h in hosts:
            hn, st = h.get("name") or "", h.get("state") or ""
            down = st in DOWN_STATES
            maint = h.get("maint") if isinstance(h.get("maint"), bool) else None
            row = {"n": hn, "vc": name, "cluster": h.get("cluster") or "", "state": st, "maint": maint, "feed": fk,
                   "cpu": h.get("cpu_pct"), "mem": h.get("mem_pct"), "vms": sorted(h.get("vms") or [])[:200], "nvms": len(h.get("vms") or []),
                   "ds": sorted(h.get("datastores") or []), "power": h.get("power") or "", "site": site_of(rx, h.get("cluster"), hn), "st": "ok"}
            if down:
                row["st"] = "crit"
                conds.append({"id": "host:%s:%s" % (name, hn), "kind": "host", "t": "%s %s" % (hn, "not responding" if st == "notResponding" else "disconnected"),
                              "obj": "cluster %s · %d VMs on it" % (row["cluster"] or "-", row["nvms"]), "feed": fk, "vc": name, "host": hn,
                              "cluster": row["cluster"], "site": row["site"]})
            elif st != "connected" or maint is None:
                row["st"] = "stale"                    # its state or its maintenance mode not read: unknown - never up, never green
            else:
                ok.append((fk, "host:%s:%s" % (name, hn)))
                if maint and not any(fnmatch.fnmatch(hn.lower(), p.lower()) for p in o["maintenance_expected"]):
                    row["st"] = "warn"
                    conds.append({"id": "maint:%s:%s" % (name, hn), "kind": "maint", "t": "%s in maintenance mode" % hn,
                                  "obj": "cluster %s · not listed in portal_wall_maintenance_expected" % (row["cluster"] or "-"), "feed": fk, "vc": name,
                                  "host": hn, "cluster": row["cluster"], "site": row["site"]})
                else:
                    ok.append((fk, "maint:%s:%s" % (name, hn)))
                for metric in ("cpu", "mem"):
                    v_ = row[metric]
                    if v_ is not None and v_ < thr:
                        ok.append((fk, "%s:host:%s:%s" % (metric, name, hn)))
            if maint is None and st == "connected":
                part["incomplete"].append("host %s: maintenance mode not read" % hn)
            if st not in DOWN_STATES + ("connected",):
                part["incomplete"].append("host %s: state not read" % hn)
            elif st == "connected" and (row["cpu"] is None or row["mem"] is None):
                part["incomplete"].append("host %s: CPU or memory not read" % hn)
            hardware = hardware_of(h, o)
            hid = "hardware:%s:%s" % (name, hn)
            if hardware == "green":
                ok.append((fk, hid))
            elif hardware in ("red", "yellow"):
                severity = "crit" if hardware == "red" else "warn"
                conds.append({"id": hid, "kind": "hardware", "sev": severity, "t": "Host hardware: " + hn,
                              "obj": "overallStatus " + hardware + (" · " + ", ".join(h["alarms"]) if h.get("alarms") else ""),
                              "host": hn, "vc": name, "feed": fk, "cluster": row["cluster"], "site": row["site"]})
                if severity == "crit" or row["st"] == "ok":
                    row["st"] = severity
            else:
                part["incomplete"].append("host %s: overallStatus %s" % (hn, hardware or "not read"))
                if row["st"] == "ok":
                    row["st"] = "stale"
            part["hosts"].append(row)
        dsrows = []
        for d in snap.get("datastores") or []:
            dn = d.get("name") or ""
            if excluded(part, o, "datastores", dn, name):
                continue
            shared = d.get("shared")
            ignored = matches(dn, o["ds_ignore"])
            local = shared is False
            if local or ignored:
                excluded(part, o, "datastores", dn, name, "local - not watched" if local else "portal_wall_ds_ignore - not watched")
            elif shared is not True:
                # 1.131.1 (Codex #3): sharing not read - said, but its measured capacity still counts (never hidden for it)
                part["incomplete"].append("datastore %s: multipleHostAccess not read" % dn)
            capacity_watched = not local and not ignored
            cap, free = int(d.get("capacity") or 0), d.get("free")
            # not read, or impossible (free below 0 or over the capacity): unknown - never 100 %, never OK (Codex r5 #14)
            pct = round(100.0 * (cap - int(free)) / cap, 1) if cap and free is not None and 0 <= int(free) <= cap else None
            free = int(free or 0)
            if capacity_watched and pct is None:
                part["incomplete"].append("datastore %s: capacity or free space not read" % (d.get("name") or "?"))
            acc = d.get("accessible") if isinstance(d.get("accessible"), bool) else None
            r = {"n": d.get("name") or "", "vc": name, "feed": fk, "type": d.get("type") or "", "size": cap, "free": free, "v": pct,
                 "accessible": acc, "capacity_watched": capacity_watched, "sharing_unknown": capacity_watched and shared is not True, "hosts": sorted(d.get("hosts") or []), "nvms": int(d.get("vms") or 0)}
            r["site"] = site_of(rx, r["n"], (r["hosts"] or [""])[0])
            if not capacity_watched:
                r["watch_reason"] = "local - not watched" if local else "portal_wall_ds_ignore - not watched"
            dsrows.append(r)
            if acc is None:
                part["incomplete"].append("datastore %s: accessibility not read" % r["n"])
            elif acc:
                ok.append((fk, "dsi:%s:%s" % (name, r["n"])))
                if capacity_watched and pct is not None and pct < o["ds_warn"]:
                    ok.append((fk, "ds:%s:%s" % (name, r["n"])))
            if acc is False:
                conds.append({"id": "dsi:%s:%s" % (name, r["n"]), "kind": "dsi", "t": "Datastore %s inaccessible" % r["n"],
                              "obj": "mounted on %d hosts · %d VMs" % (len(r["hosts"]), r["nvms"]), "feed": fk, "vc": name, "ds": r["n"], "site": r["site"]})
            elif capacity_watched and acc and pct is not None and pct >= o["ds_warn"]:
                k = "dsfull" if pct >= o["ds_crit"] else "dswarn"
                conds.append({"id": "ds:%s:%s" % (name, r["n"]), "kind": k, "t": "%s %s %% full" % (r["n"], _n(round(pct))),
                              "obj": "%s free · warning %s, critical %s" % (gb(free), _n(o["ds_warn"]), _n(o["ds_crit"])),
                              "feed": fk, "vc": name, "ds": r["n"], "v": pct, "site": r["site"]})
        part["datastores"].extend(dsrows)
        vms = snap.get("vms") or []
        vm_host = {}
        for v in vms:
            vn, power = v.get("name"), v.get("power")
            if excluded(part, o, "vms", vn, name):
                continue
            part["vms"] += 1
            if v.get("vcls") is True or v.get("template") is True:     # 1.131.1 (Codex #7): vCLS by its managedBy, never its name
                continue
            if power not in POWER_KNOWN:
                part["incomplete"].append("VM %s: power state not read" % vn)
                continue
            heartbeat_id = "vmhung:%s:%s" % (name, v.get("id") or vn)
            if power != "poweredOn":                   # off or suspended, known: none of its conditions holds
                ok.append((fk, heartbeat_id))
                ok.extend((fk, x % (name, vn)) for x in ("cpu:vm:%s:%s", "mem:vm:%s:%s"))
                continue
            known = v.get("tools_seen") is True         # its Tools ran in an earlier read: a workload VM
            if v.get("template") is not False:
                part["incomplete"].append("VM %s: template flag not read" % vn)
                if not known:
                    continue
                # 1.131.1 (Codex #6): a VM known to run Tools stays watched for its heartbeat when a later read leaves the
                # template flag out (a template is never powered on); its load is not counted from this read
            vm_host[vn] = v.get("host") or ""
            if v.get("cpu_pct") is None or v.get("mem_pct") is None:
                part["incomplete"].append("VM %s: CPU or memory not read" % vn)
            for metric in ("cpu", "mem"):
                if v.get(metric + "_pct") is not None and v[metric + "_pct"] < thr:
                    ok.append((fk, "%s:vm:%s:%s" % (metric, name, vn)))
            if o["vm_heartbeat"]:
                hb, tools = v.get("heartbeat"), v.get("tools")
                if not known and (v.get("tools_version") == "guestToolsNotInstalled" or v.get("tools_seen") is False):
                    part["heartbeat_unwatched"] += 1
                    if not (hb or tools or v.get("tools_version")):
                        part["incomplete"].append("VM %s: Tools and heartbeat not read" % vn)
                    continue
                if v.get("tools_seen") is not True:
                    part["incomplete"].append("VM %s: Tools history not read" % vn)
                    continue
                part["heartbeat_watched"] += 1
                if hb == "red" or tools == "guestToolsNotRunning":
                    conds.append({"id": heartbeat_id, "kind": "vmhung", "t": "VM %s no heartbeat" % vn,
                                  "obj": ("VMware Tools heartbeat red" if hb == "red" else "VMware Tools stopped") + " · host " + (v.get("host") or "-"),
                                  "feed": fk, "vc": name, "vm": vn, "host": vm_host[vn], "site": site_of(rx, vm_host[vn])})
                elif hb == "green":
                    ok.append((fk, heartbeat_id))
                else:
                    part["incomplete"].append("VM %s: heartbeat %s" % (vn, hb or "not read"))
        # Only objects eligible in this read can contribute a load condition.
        host_cluster = dict((h.get("name"), h.get("cluster") or "") for h in hosts)
        for key, samples in hist.items():
            if not (isinstance(key, (tuple, list)) and len(key) == 4 and key[0] == src["key"]):
                continue
            _k, kind, oname, metric = key
            if oname not in (vm_host if kind == "vm" else host_cluster):
                continue
            samples = [(float(t), v) for t, v in samples]
            v_now, start = run_of(samples, thr, sec)
            if start is None or not samples or (src.get("at") and samples[-1][0] < float(src["at"]) - 1):
                continue                      # not high now, or not in the newest good read (a VM gone)
            long_s = held(start, samples[-1][0], sec)
            label = "VM" if kind == "vm" else "Host"
            m = "CPU" if metric == "cpu" else "Memory"
            host = vm_host.get(oname, "") if kind == "vm" else oname
            if long_s >= win:
                cid = "%s:%s:%s:%s" % (metric, kind, name, oname)
                conds.append({"id": cid, "kind": metric, "t": "%s %s %s %s %%" % (label, oname, m.lower() if kind == "host" else m, _n(round(v_now))),
                              "obj": ("host %s" % host if kind == "vm" else "cluster %s" % (host_cluster.get(oname) or "-")) + " · sustained, not a blip",
                              "start": start, "feed": fk, "vc": name, "v": round(v_now, 1), "site": site_of(rx, host_cluster.get(host), host, oname),
                              "obj_kind": kind, "obj_name": oname, "metric": metric, "host": host})
                part["hot"].append({"cond": cid, "n": oname, "k": label, "m": m, "v": round(v_now, 1), "spark": spark(samples, now),
                                    "host": host, "vc": name, "feed": fk})
            else:
                part["building"].append({"n": oname, "k": label, "m": m, "v": round(v_now, 1), "min": int(long_s // 60), "vc": name})
        feeds[fk]["unread"] = part["incomplete"][initial:initial + 3]
    part["hot"].sort(key=lambda x: -x["v"])
    part["building"].sort(key=lambda x: -x["v"])
    part["datastores"].sort(key=lambda d: (-(d["v"] or 0), d["n"]))
    return part, conds, feeds, ok


def gb(b):
    b = float(b or 0)
    return ("%.1f TB" % (b / 1024 ** 4)) if b >= 1024 ** 4 else ("%.0f GB" % (b / 1024 ** 3))


def storage_part(nm, nm_asof, vm, o, now, why="", view=None):
    """Storage - LIVE ONTAP ONLY (Codex verify 4, cut 1): the lamps, counters, SnapMirror and node checks, aggregates and Map
    dots come only from the portal's own reads of portal_wall_ontap_clusters; with none configured, Storage is not on the
    wall. The NetApp run in the report database (15-30 min old by design) is never a light: it gives only the datastore ->
    volume / LUN path card its rows, as information ("from the NetApp refresh, N min old"). -> (part, conds, feeds, ok)"""
    conds, feeds, ok = [], {}, []
    rx = site_rule(o["site_pattern"])
    srcs = sorted([dict(x, key=k) for k, x in ((view or {}).get("sources") or {}).items() if x.get("kind") == "ontap"], key=lambda x: str(x.get("name")))
    part = {"configured": bool(srcs), "why": "" if srcs else STORAGE_OFF, "incomplete": [], "ran": {"sm": False, "nodes": False}, "clusters": [],
            "nodes": [0, 0], "volumes": 0, "volOffline": None, "smRel": 0, "aggr": [], "ds": [], "dsTotal": 0, "smBroken": [], "smUnknown": [], "paths": {},
            "source": "live", "paths_why": why or "", "excluded": []}
    ran = {"sm": [], "nodes": []}
    for src in srcs:
        fk, snap, state = src["key"], src.get("snap") or {}, src.get("state") or "never"
        addr = str(src.get("name") or "")
        cl = str(snap.get("cluster") or addr)
        site = site_of(rx, cl)
        feeds[fk] = {"name": "ONTAP " + addr, "asof": src.get("at"), "state": state, "why": src.get("why") or "",
                     "src": "live, read every %d s" % o["seconds"], "fail_since": src.get("fail_since"), "next_try": src.get("next_try")}
        if state in ("auth", "error", "paused"):
            conds.append({"id": "node:" + addr, "kind": "node",
                          "t": "Storage cluster %s %s" % (addr, "login failed" if state == "auth" else "login paused" if state == "paused" else "not answering"),
                          "obj": (src.get("why") or "login failed - check the credential") if state == "auth" else (src.get("why") or ""), "feed": "", "cluster": cl,
                          "start": src.get("fail_since"), "site": site})
        elif state == "ok":
            ok.append((fk, "node:" + addr))
        if not snap:
            part["incomplete"].append("ONTAP %s: not read yet" % addr)
            ran["sm"].append(False)
            ran["nodes"].append(False)
            continue
        if cl not in part["clusters"]:
            part["clusters"].append(cl)
        initial = len(part["incomplete"])
        for k, why_ in (snap.get("failed") or {}).items():
            part["incomplete"].append("ONTAP %s: %s not read whole (%s)" % (addr, k, why_))
        if snap.get("aggrs") is None:
            part["incomplete"].append("ONTAP %s: aggregates not read" % addr)
        root_aggregates = {a["name"] for a in snap.get("aggrs") or [] if a.get("name") and a.get("is_root") is True}
        for a in snap.get("aggrs") or []:
            if excluded(part, o, "aggregates", a.get("name"), addr, "root aggregate" if a.get("name") in root_aggregates else ""):
                continue
            size_b, used = a.get("size"), a.get("used")
            pct = round(100.0 * used / size_b, 1) if size_b and used is not None and 0 <= used <= size_b else None    # not read or impossible: unknown
            cid = "aggr:%s:%s" % (addr, a.get("name") or "")
            r = {"n": a.get("name") or "", "node": a.get("node") or "", "c": cl, "addr": addr, "ref": "%s|%s" % (fk, a.get("name") or ""), "v": pct,
                 "size": size_b or 0, "free": max(0, size_b - used) if pct is not None else None, "feed": fk, "site": site}
            if pct is None:
                part["incomplete"].append("aggregate %s (%s): size or use not read" % (r["n"], addr))
            elif pct >= o["aggr_warn"]:
                r["cond"] = cid
                conds.append({"id": cid, "kind": "aggrfull" if pct >= o["aggr_crit"] else "aggrwarn", "t": "%s %s %% used" % (r["n"], _n(round(pct))),
                              "obj": "%s free · cluster %s · warning %s, critical %s" % (gb(r["free"]), cl, _n(o["aggr_warn"]), _n(o["aggr_crit"])),
                              "feed": fk, "aggr": r["n"], "ref": r["ref"], "cluster": cl, "v": pct, "site": site})
            else:
                ok.append((fk, cid))
            part["aggr"].append(r)
        nodes = snap.get("nodes")
        known = True
        for n in nodes or []:
            ns, cid = n.get("state"), "node:%s:%s" % (addr, n.get("name"))
            if ns == "up":
                ok.append((fk, cid))
            elif ns in NODE_BAD:
                conds.append({"id": cid, "kind": "node", "t": "Storage node %s %s" % (n.get("name"), ns),
                              "obj": "cluster %s · takeover %s" % (cl, {True: "possible", False: "not possible"}.get(n.get("takeover"), "?")), "feed": fk,
                              "cluster": cl, "site": site, "obj_name": n.get("name")})
            else:
                known = False
                part["incomplete"].append("node %s: state %s" % (n.get("name"), "not read" if ns is None else "%r not known" % (ns,)))
        ran["nodes"].append(nodes is not None and known)
        part["nodes"] = [part["nodes"][0] + sum(1 for n in nodes or [] if n.get("state") == "up"), part["nodes"][1] + len(nodes or [])]
        off = 0
        ls_destinations = {r.get("dst") for r in snap.get("snapmirror") or [] if str(r.get("type")).lower() == "ls"}
        skipped_volumes = set()
        for coll, label in (("volumes", "Volume"), ("luns", "LUN")):
            if snap.get(coll) is None:
                part["incomplete"].append("ONTAP %s: %s not read" % (addr, coll))
            for v in snap.get(coll) or []:
                nm_ = v.get("name") if coll == "volumes" else v.get("path")
                if coll == "volumes":
                    identity = (v.get("svm"), nm_)
                    dest = "%s:%s" % identity
                    reason = ""
                    if v.get("is_svm_root") is True or v.get("is_node_root") is True or v.get("aggr") in root_aggregates:
                        reason = "root volume"
                    elif v.get("type") == "ls" or (v.get("type") == "dp" and (dest in ls_destinations or v.get("destination_type") == "ls")):
                        reason = "load-sharing mirror destination"
                    if excluded(part, o, "volumes", nm_, addr, reason):
                        if v.get("svm") and nm_:        # 1.131.1 (Codex #8): its LUNs inherit it only by a known SVM and name
                            skipped_volumes.add(identity)
                        continue
                    part["volumes"] += 1
                elif ((v.get("svm") and v.get("volume") and (v.get("svm"), v.get("volume")) in skipped_volumes)
                      or matches(v.get("volume"), o["exclude"].get("volumes"))):
                    continue
                vs, cid = v.get("state"), "voloff:%s:%s:%s" % (addr, v.get("svm"), nm_)
                if vs == "online":
                    ok.append((fk, cid))
                elif vs in STATE_BAD:
                    off += 1
                    conds.append({"id": cid, "kind": "voloff", "t": "%s %s %s" % (label, nm_, vs),
                                  "obj": "SVM %s · %s" % (v.get("svm"), "cluster " + cl if coll == "volumes" else "volume %s" % v.get("volume")), "feed": fk,
                                  "cluster": cl, "site": site, "obj_name": nm_})
                else:
                    part["incomplete"].append("%s %s: state %s" % (label.lower(), nm_, "not read" if vs is None else "%r not known" % (vs,)))
        if snap.get("volumes") is not None or snap.get("luns") is not None:
            part["volOffline"] = (part["volOffline"] or 0) + off
        sm = snap.get("snapmirror")
        known = True
        part["smRel"] += len(sm or [])
        for r in sm or []:
            st_ = str(r.get("state") or "").replace("-", "_")
            cid = "sm:%s:%s" % (addr, r.get("dst"))
            severity = "warn"
            lag_h = None                          # 1.135.0: the lag in hours, for its limit chart (Share to Teams)
            if r.get("healthy") is False or st_ == "broken_off":
                word = "broken off" if st_ == "broken_off" else "unhealthy"
            elif r.get("healthy") is True and st_ in SM_GOOD:
                lag = r.get("lag_s")
                if type(lag) not in (float, int) or not (0 <= lag < math.inf):
                    known = False
                    part["smUnknown"].append("%s · %s: lag_time unknown" % (addr, r.get("dst")))
                    part["incomplete"].append("SnapMirror %s: lag_time unknown" % r.get("dst"))
                    continue
                if lag <= o["snapmirror_lag_warn_hours"] * 3600:
                    ok.append((fk, cid))
                    continue
                word = "lag %.2f h" % (lag / 3600)
                lag_h = round(lag / 3600.0, 2)
                if lag > o["snapmirror_lag_crit_hours"] * 3600:
                    severity = "crit"
            else:
                known = False
                part["incomplete"].append("SnapMirror %s: health or state not known (healthy %s, state %s)" % (r.get("dst"), r.get("healthy"), r.get("state")))
                continue
            part["smBroken"].append({"dst": r.get("dst") or "", "src": r.get("src") or "", "why": r.get("why") or "", "word": word.capitalize(),
                                     "cond": cid, "c": cl, "feed": fk, "sev": severity})
            conds.append({"id": cid, "kind": "sm", "sev": severity, "t": "SnapMirror %s %s" % (r.get("dst"), word),
                          "obj": "from %s · %s" % (r.get("src") or "-", r.get("why") or r.get("state") or ""), "feed": fk,
                          "cluster": cl, "obj_name": r.get("dst"), "site": site})
            if lag_h is not None:
                conds[-1]["lag_h"] = lag_h
        if sm is None and "snapmirror" not in (snap.get("failed") or {}):
            part["incomplete"].append("ONTAP %s: SnapMirror not read" % addr)
        if nodes is None and "nodes" not in (snap.get("failed") or {}):
            part["incomplete"].append("ONTAP %s: nodes not read" % addr)
        feeds[fk]["unread"] = part["incomplete"][initial:initial + 3]
        ran["sm"].append(sm is not None and known)
    if srcs:
        # the datastores (Storage's fill gauges): the vCenters' live reads
        for d in vm.get("datastores") or []:
            if not d["capacity_watched"]:
                if d.get("watch_reason") == "multipleHostAccess not read":
                    part["incomplete"].append("vCenter %s: datastore %s: multipleHostAccess not read" % (d["vc"], d["n"]))
                continue
            part["ds"].append(dict(d))
            if d.get("sharing_unknown"):              # 1.131.1 (Codex #3): watched, its sharing said as not read
                part["incomplete"].append("vCenter %s: datastore %s: multipleHostAccess not read" % (d["vc"], d["n"]))
            if d.get("v") is None:
                part["incomplete"].append("datastore %s: capacity or free space not read" % d["n"])
            if d.get("accessible") is None:
                part["incomplete"].append("datastore %s: accessibility not read" % d["n"])
    if nm:
        # the path card only: which volume / LUN is behind a datastore - information, never a status
        feeds["netapp-run"] = {"name": "NetApp refresh (report database)", "asof": nm_asof, "state": "ok" if nm_asof and not why else "error",
                               "why": why or "", "src": "the refresh job's newest run - information on the storage path card, never a light"}
        for v in nm.get("volumes") or []:
            if not v.get("ds"):
                continue
            part["paths"].setdefault(v["ds"], {"rows": []})["rows"].append(
                {"cluster": v.get("c") or "", "svm": v.get("svm") or "", "vol": v.get("v") or "", "lun": v.get("lun") or "", "kind": v.get("kind") or "",
                 "size": v.get("size"), "used": v.get("used"), "pct": v.get("pct"), "src": v.get("src") or "", "vol_size": v.get("vol_size"),
                 "aggr": v.get("aggr") or "", "aggr_free": v.get("aggr_free"), "thick": bool(v.get("thick")), "feed": "netapp-run", "info": True})
    part["aggr"].sort(key=lambda a: -(a["v"] if a["v"] is not None else 101))
    part["ran"] = dict((k, bool(v) and all(v)) for k, v in ran.items())
    part["clusters"].sort()
    part["dsTotal"] = len(part["ds"])
    return part, conds, feeds, ok


def build(view, nm, nm_asof, opts, tracker, now, enclave="", title="", nm_why="", off_why=None, fleet=None, fleet_why=""):
    """-> the answer of /api/wall (times: epoch seconds). fleet: the Ops monitor area's model (wall_fleet.model) when that
    area is on (portal_wall_monitor), with fleet_why when it is on but cannot read; None: the area is not on the wall."""
    o = opts_of(opts)
    ks = kinds(o)
    fleet_on = fleet is not None or bool(fleet_why)
    if fleet_on:
        ks.update(FLEET_KINDS)
    vm, c1, f1, ok1 = vmware_part(view, o, now)
    st, c2, f2, ok2 = storage_part(nm, nm_asof, vm, o, now, nm_why, view)
    if not st["configured"]:                   # Storage is not on the wall: none of its conditions (a datastore's fill) either
        c1 = [c for c in c1 if ks[c["kind"]]["area"] != "storage"]
    if fleet is not None and wall_fleet is not None:
        c3, ok3 = wall_fleet.conds(fleet, now)
        f2 = dict(f2, **{"sw:fleet": dict(fleet["feed"])})
        c2 = c2 + c3
        ok2 = ok2 + [("sw:fleet", x) for x in ok3]
    fall = dict(f1)
    fall.update(f2)
    for c in c1 + c2:
        k = ks[c["kind"]]
        c.update(area=k["area"], sev=c.get("sev", k["sev"]), end=None)
        c["seen"] = (fall.get(c.get("feed")) or {}).get("asof") or c.get("start")     # when its data was read (a last-known one: last seen)
    # what clears a condition: its object seen OK in a fresh read (its feed's latest read worked, within the stale limit)
    cur = dict((k, f.get("state") == "ok" and f.get("asof") is not None and now - float(f["asof"]) <= (f.get("stale") or o["stale"])) for k, f in fall.items())
    ok_ids = set(cid for fk, cid in ok1 + ok2 if cur.get(fk))
    if tracker is not None:
        for src in ((view or {}).get("sources") or {}).values():
            for alias in src.get("aliases", []):
                tracker.merge_vcenter(alias, src["name"])
    conds = tracker.update(c1 + c2, now, ok_ids) if tracker is not None else [dict(c, start=c.get("start") or now) for c in c1 + c2]
    # 1.131.1 (Codex #9): a SnapMirror condition still open as last known (no recovery observed) stays on the SnapMirror
    # panel - never "None over threshold" while the wall keeps it
    listed = set(b["cond"] for b in st["smBroken"])
    for c in conds:
        if c.get("kind") == "sm" and c.get("end") is None and c["id"] not in listed:
            st["smBroken"].append({"dst": c.get("obj_name") or "", "src": "", "why": "last known - not seen recovered", "word": "Last known",
                                   "cond": c["id"], "c": c.get("cluster") or "", "feed": c.get("feed"), "sev": c.get("sev") or "warn",
                                   "last_known": True})
    # a datastore / aggregate row points at its condition
    by = {}
    for c in conds:
        if c.get("end") is None and c.get("ds"):
            by[("ds", c.get("vc", ""), c["ds"])] = c["id"]
    for d in st["ds"]:
        cid = by.get(("ds", d.get("vc", ""), d["n"]))
        if cid:
            d["cond"] = cid
    feeds = dict(f1)
    feeds.update(f2)
    for f in feeds.values():
        # the state at this answer (the page works the age out itself every second - no age here: the ETag stays until a change)
        f["fresh"] = fstate(None if not f.get("asof") else max(0, int(now - float(f["asof"]))), o, f)
    area_feeds = {"vmware": sorted(f1), "storage": (sorted(k for k in f2 if k != "netapp-run" and k != "sw:fleet") + sorted(f1)) if st["configured"] else [],
                  "monitor": ["sw:fleet"] if fleet is not None else []}
    areas = []
    for a in AREAS + ((FLEET_AREA,) if fleet_on else ()):
        x = dict(a)
        x["on"] = (a["id"] in BUILT or a["id"] == "monitor") and (st["configured"] if a["id"] == "storage" else bool(area_feeds.get(a["id"])))
        x["built"] = a["id"] in BUILT or a["id"] == "monitor"
        x["feeds"] = area_feeds.get(a["id"], [])
        x["not_on_wall"] = NOT_ON_WALL[a["id"]]
        inc = (vm["incomplete"] if a["id"] == "vmware" else st["incomplete"] if a["id"] == "storage" else
               ((fleet or {}).get("incomplete") or [] if fleet is not None else [fleet_why]) if a["id"] == "monitor" else [])
        if a["id"] == "monitor" and fleet and not inc and not any(fleet["counts"][c] for c in ("u", "w", "d", "x")):
            inc = ["no monitored nodes"]
        x["complete"], x["incomplete"] = not inc, inc[:5] + (["... %d more" % (len(inc) - 5)] if len(inc) > 5 else [])
        off_why = off_why if isinstance(off_why, dict) else {}
        if a["id"] == "storage" and not st["configured"]:
            x["why"] = off_why.get("storage") or STORAGE_OFF          # 1.131.0: a login there but no cluster: that, not "no login"
        elif a["id"] == "vmware" and not vm["configured"]:
            x["why"] = off_why.get("vmware") or "no vCenter login: the portal's deploy needs the \"VMware vCenter\" credential (portal_wall_vcenter)"
        elif a["id"] == "monitor" and fleet is None:
            x["why"] = fleet_why
        areas.append(x)
    sites = sorted(set(c.get("site") for c in conds if c.get("site")) | set(h["site"] for h in vm["hosts"] if h.get("site")))
    return {"now": now, "since": tracker.first if tracker is not None else None, "title": title or "Ops center", "enclave": enclave, "late": o["late"], "stale": o["stale"], "seconds": o["seconds"],
            "areas": areas, "feeds": feeds, "kinds": ks, "conds": conds, "vmware": vm, "storage": st,
            "fleet": wall_fleet.summary(fleet) if fleet is not None and wall_fleet is not None else None,
            "thresholds": {"snapmirror_lag_hours": [o["snapmirror_lag_warn_hours"], o["snapmirror_lag_crit_hours"]], "ds": [o["ds_warn"], o["ds_crit"]], "aggr": [o["aggr_warn"], o["aggr_crit"]], "high": [o["high_pct"], o["high_minutes"]],
                           "maintenance_expected": o["maintenance_expected"], "vm_heartbeat": o["vm_heartbeat"]},
            "sites": [{"id": s, "place": place_of(s, o)} for s in sites]}


# ---- 1.135.0: the areas in short, for the portal's looks (the Actions page's "Ops center" section) ---------------------------
# The same rule as the wall page (static/wall-page.js areaState / status): green only when every feed of the area is current
# (read OK, younger than LATE), its read whole and no condition open; a stale feed or a last-known condition is grey, never green.
def _feed_fresh(m, k, now):
    f = (m.get("feeds") or {}).get(k)
    if not isinstance(f, dict) or f.get("state") != "ok" or f.get("asof") is None:
        return "stale"
    return fstate(max(0.0, now - float(f["asof"])), {"late": m.get("late") or LATE_S, "stale": m.get("stale") or STALE_S}, f)


def _home(k):
    return "vmware" if k.startswith("vc:") else "storage" if k.startswith("ontap:") else "monitor" if k.startswith("sw:") else ""


def _home_ok(m, k, now, by):
    a = _home(k)
    if not a:
        return True
    ar = by.get(a)
    if not ar or ar.get("complete") is not True or k not in (ar.get("feeds") or []):
        return False
    feeds = m.get("feeds") or {}
    return all(_feed_fresh(m, x, now) == "ok" for x in feeds if _home(x) == a) and \
        all(x in feeds for x in (ar.get("feeds") or []) if _home(x) == a)


def _status(m, st, k, now, by):
    """the wall's one rule for a value from feed k: stale feed -> stale; ok only when the feed is current and its area whole"""
    if not k:
        return "off" if st == "ok" else st
    fr = _feed_fresh(m, k, now)
    if fr == "stale":
        return "stale"
    if st == "ok" and (not isinstance(m.get("conds"), list) or fr != "ok" or not _home_ok(m, k, now, by)):
        return "stale"
    return st


def _cond_stale(m, c, now):
    return bool(c.get("last_known")) or bool(c.get("feed") and _feed_fresh(m, c["feed"], now) == "stale")


def _metric(label, obj, v, warn, crit, unit, feed, m, now, by):
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return None
    st = "crit" if crit is not None and v >= crit else "warn" if warn is not None and v >= warn else "ok"
    f = (m.get("feeds") or {}).get(feed) if feed else None
    return {"label": label, "obj": obj, "v": round(float(v), 1), "warn": warn, "crit": crit, "unit": unit, "feed": feed,
            "state": _status(m, st, feed, now, by), "asof": f.get("asof") if isinstance(f, dict) else None}


def summary(m, now):
    """the wall's model (build()) -> {"state", "areas": [{id, name, state, crit, warn, big, unit, note, metric, worst, asof, why}]}.
    No value is made up: an area not on the wall is "off" ("Not on the wall yet"); a metric only from what the model read."""
    areas = [a for a in (m.get("areas") or []) if isinstance(a, dict) and a.get("id")]
    by = dict((a.get("id"), a) for a in m.get("areas") or [] if isinstance(a, dict))
    conds = m.get("conds") if isinstance(m.get("conds"), list) else None
    th = m.get("thresholds") or {}
    rank = {"crit": 3, "warn": 2, "stale": 1, "ok": 0, "off": -1}
    out, overall = [], "off"
    for a in areas:
        aid = a.get("id")
        feeds = [k for k in (a.get("feeds") or []) if isinstance(k, str)]
        opened = [c for c in conds or [] if isinstance(c, dict) and c.get("end") is None and c.get("area") == aid]
        cur = [c for c in opened if not _cond_stale(m, c, now)]
        n = {"crit": sum(1 for c in cur if c.get("sev") == "crit"), "warn": sum(1 for c in cur if c.get("sev") == "warn")}
        if not a.get("on") or not a.get("built"):
            st = "off"
        elif conds is None:
            st = "stale"
        elif n["crit"]:
            st = "crit"
        elif n["warn"]:
            st = "warn"
        elif any(_cond_stale(m, c, now) for c in opened) or not feeds or a.get("complete") is not True or \
                any(_feed_fresh(m, k, now) != "ok" or _status(m, "ok", k, now, by) != "ok" for k in feeds):
            st = "stale"
        else:
            st = "ok"
        asofs = [float((m.get("feeds") or {}).get(k, {}).get("asof")) for k in feeds
                 if isinstance((m.get("feeds") or {}).get(k), dict) and (m.get("feeds") or {})[k].get("asof") is not None]
        why = ""
        if st == "stale":
            bad = []
            for k in feeds:
                f = (m.get("feeds") or {}).get(k)
                if not isinstance(f, dict):
                    bad.append(k + ": source missing")
                elif f.get("state") != "ok":
                    bad.append("%s: %s" % (f.get("name") or k, f.get("why") or {"never": "not read yet", "auth": "login refused",
                                                                                     "error": "not answering", "paused": "login paused"}.get(f.get("state"), "not read")))
                elif _feed_fresh(m, k, now) != "ok":
                    bad.append(("%s: last read %d min ago" % (f.get("name") or k, int((now - float(f["asof"])) // 60))) if f.get("asof") is not None
                               else "%s: not read yet" % (f.get("name") or k))
            if not bad and a.get("complete") is not True:
                bad = list(a.get("incomplete") or [])[:2] or ["read not whole"]
            why = " · ".join(bad[:2])
        elif st == "off":
            why = a.get("not_on_wall") or "not on the wall yet."
        big = unit = note = ""
        metric = None
        if st != "off" and aid == "storage":
            st_ = m.get("storage") or {}
            ag = [x for x in st_.get("aggr") or [] if isinstance(x, dict) and isinstance(x.get("v"), (int, float))]
            aw, ac = (th.get("aggr") or [85, 97])[:2]
            dw, dc = (th.get("ds") or [85, 95])[:2]
            if ag:
                w_ = max(ag, key=lambda x: x["v"])
                metric = _metric("fullest aggregate", w_.get("n"), w_["v"], aw, ac, "%", w_.get("feed"), m, now, by)
            else:
                ds = [x for x in st_.get("ds") or [] if isinstance(x, dict) and isinstance(x.get("v"), (int, float)) and x.get("capacity_watched") is not False]
                if ds:
                    # an inaccessible datastore is critical whatever its fill (the wall's dsLevel): it is the one shown
                    bad = [x for x in ds if x.get("accessible") is False]
                    w_ = bad[0] if bad else max(ds, key=lambda x: x["v"])
                    metric = _metric("fullest datastore" if not bad else "datastore inaccessible", w_.get("n"), w_["v"], dw, dc, "%", w_.get("feed"), m, now, by)
                    if bad and metric:
                        metric["state"] = _status(m, "crit", w_.get("feed"), now, by)
                        metric["forced"] = "inaccessible (vCenter)"
            if metric:
                big, unit = _n(metric["v"]), "% used · " + str(metric["obj"] or "")
            nodes = st_.get("nodes") if isinstance(st_.get("nodes"), list) and len(st_.get("nodes")) == 2 else None
            note = ("%d of %d storage nodes up" % (nodes[0], nodes[1])) if nodes else ""
        elif st != "off" and aid == "vmware":
            vm = m.get("vmware") or {}
            cl = [x for x in vm.get("clusters") or [] if isinstance(x, dict)]
            up = sum(int(x.get("up") or 0) for x in cl)
            of = sum(int(x.get("of") or 0) for x in cl)
            if of:
                big, unit = "%d/%d" % (up, of), "hosts up"
            hw = (th.get("high") or [90, 15])[0]
            busy = [x for x in cl if isinstance(x.get("cpu"), (int, float))]
            if busy:
                w_ = max(busy, key=lambda x: x["cpu"])
                metric = _metric("busiest cluster CPU", w_.get("n"), w_["cpu"], hw, None, "%", w_.get("feed"), m, now, by)
            note = "%d VMs on %d hosts" % (sum(int(h.get("nvms") or 0) for h in vm.get("hosts") or [] if isinstance(h, dict)), of) if of else ""
        elif st != "off" and aid == "monitor":
            fs = m.get("fleet") or {}
            cn = fs.get("counts") or {}
            tot = sum(int(v or 0) for v in cn.values())
            if st == "stale":
                big, unit = "–", "no current data"
            elif tot:
                big, unit = "%d/%d" % (int(cn.get("u") or 0), tot), "nodes up"
                note = "%d down · %d warning · %d off or unmanaged" % (int(cn.get("d") or 0), int(cn.get("w") or 0), int(cn.get("p") or 0) + int(cn.get("m") or 0))
        worst = sorted(cur, key=lambda c: (-rank.get(c.get("sev"), 0), c.get("start") or 0))[:3]
        # until when the page may show this as it is, on its own clock (the wall's rule, between two reads): OK until the oldest
        # source of the area passes LATE; a current condition until its own source passes STALE (a condition with no feed of its
        # own - a vCenter or cluster not answering - stays until the next read says otherwise)
        late_s, stale_s = float(m.get("late") or LATE_S), float(m.get("stale") or STALE_S)
        fd = m.get("feeds") or {}
        lims = [float(fd[k]["asof"]) + float(fd[k].get("late") or late_s) for k in feeds if isinstance(fd.get(k), dict) and fd[k].get("asof") is not None]
        ok_until = min(lims) if st == "ok" and asofs and len(asofs) == len(feeds) else None
        live = []                                  # each current condition: its severity, until when (None: until the next read)
        for c in cur:
            f = fd.get(c.get("feed")) if c.get("feed") else None
            live.append([c.get("sev"), (float(f["asof"]) + float(f.get("stale") or stale_s)) if isinstance(f, dict) and f.get("asof") is not None else None])
        out.append({"id": aid, "name": a.get("name"), "short": a.get("short") or a.get("name"), "team": a.get("team") or "",
                    "ok_until": ok_until, "live": live,
                    "state": st, "crit": n["crit"], "warn": n["warn"], "big": big, "unit": unit, "note": note, "metric": metric,
                    "worst": [{"t": c.get("t"), "sev": c.get("sev"), "start": c.get("start")} for c in worst],
                    "asof": min(asofs) if asofs else None, "why": why})
        if rank[st] > rank[overall]:
            overall = st
    return {"now": now, "state": overall, "areas": out, "late": m.get("late") or LATE_S, "stale": m.get("stale") or STALE_S,
            "title": m.get("title") or "", "enclave": m.get("enclave") or ""}
