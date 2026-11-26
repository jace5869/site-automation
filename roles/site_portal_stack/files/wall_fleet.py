"""1.138.0: the ops wall's "Fleet & services" area (/portal/wall?area=fleet, docs/SITE_PORTAL.md "The wall"): every node
SolarWinds watches, up or down, grouped by service, compared with what vCenter says of the same VM.

  Reader   reads SolarWinds itself (plugins/module_utils/site_swis.py: one SWQL SELECT per call, never anything else) every
           portal_wall_fleet_seconds while the wall (or the Actions page's Ops center) is open: Orion.Nodes (status, response,
           loss, last poll) every read; the groups (Orion.Container, Orion.ContainerMembers) every GROUPS_EVERY s. Its own
           thread - never the vCenter collector's (a SolarWinds refusal never stops a vCenter login, a slow Orion never
           delays one). A refused login waits auth_retry s; any other failure backs off. The last good read is kept (grey).
  model()  that read and the wall collector's vCenter view -> the area's answer (/api/wall/fleet): groups, one compact row per
           node, the SolarWinds-vs-vCenter counts. Standard library, no I/O: the tests drive it with made-up reads.
  conds()  the area's wall conditions: one per group with a node down (critical) or warning - never one per node.

The rules, one place for all of them:
  - status from SolarWinds (Orion's own status codes; unmanaged grey); a node in no group: "Workloads (no group)";
  - SolarWinds down + vCenter, CURRENT, says powered off (or suspended): "Powered off" - never down;
  - SolarWinds down + vCenter, current, says powered on with the Tools heartbeat green: down, flagged "Disagree - likely the
    network path";
  - matched by name (any case, short name against FQDN, the guest's host name too); two VMs of one name: the IP decides, else
    no compare (said); a VM not in SolarWinds: the "vCenter only" group with vCenter's status; a node whose Vendor or
    MachineType says VM (portal_wall_fleet_vm_match) not found in vCenter: counted "not found in vCenter";
  - a stale source (the wall's LATE / STALE) is grey on the page, never green; a read cut short is not whole: never green."""
import fnmatch
import ipaddress
import json
import os
import re
import threading
import time

try:
    import site_swis                       # the network map's SolarWinds reader (plugins/module_utils): SWQL SELECTs only
except ImportError:                        # pragma: no cover - the portal image has it
    site_swis = None

UP, WARN, DOWN, OFF, UNMANAGED, UNKNOWN = "u", "w", "d", "p", "m", "x"
RANK = {DOWN: 0, WARN: 1, UNKNOWN: 2, UNMANAGED: 3, OFF: 4, UP: 5}
NO_GROUP = "Workloads (no group)"
VC_ONLY = "vCenter only"
GROUPS_EVERY = 900                         # the groups change seldom: read again every 15 min (and at the first read)
IDLE = 300                                 # no viewer for this long: SolarWinds is not read until the next one
LATE_S, STALE_S = 180, 600                 # the wall's own (wall_model.LATE_S / STALE_S)
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")      # a custom property's name goes into the SWQL text: nothing else
DEFAULTS = {"seconds": 60, "group_by": "groups", "groups": [], "vm_match": ["*VMware*"], "max_nodes": 10000, "page": 1000, "poll_stale_minutes": 15}
NODE_COLS = ["N.NodeID", "N.Caption", "N.DNS", "N.IPAddress", "N.Status", "N.StatusDescription", "N.Vendor", "N.MachineType",
             "N.ResponseTime", "N.PercentLoss", "N.UnManaged"]
OPTIONAL = [("N.LastSync", "LastSync", "the last poll time (LastSync)")]
MAX_TEXT = 80
# one row per node in the answer (/api/wall/fleet "nodes"; static/wall-fleet.js reads them by these places)
ROW = ["group", "name", "ip", "code", "response_ms", "loss_pct", "since", "since_exact", "why", "vcenter_vm", "host", "vcenter", "flags",
       "key", "also_in", "polled_s_ago", "solarwinds_says", "model"]


def opts_of(o):
    """the fleet's settings (PORTAL_WALL_OPTS fleet_*) with their defaults -> (opts, [what was wrong])"""
    o = o if isinstance(o, dict) else {}
    out, bad = dict(DEFAULTS), []
    try:
        out["seconds"] = max(60, min(3600, int(o.get("fleet_seconds", DEFAULTS["seconds"]))))
    except (TypeError, ValueError):
        bad.append("portal_wall_fleet_seconds is not a number")
    gb = str(o.get("fleet_group_by") or "groups").strip()
    if gb.lower() == "groups":
        out["group_by"] = "groups"
    elif gb.lower().startswith("custom:") and IDENT.match(gb.split(":", 1)[1].strip()):
        out["group_by"] = "custom:" + gb.split(":", 1)[1].strip()
    else:
        bad.append("portal_wall_fleet_group_by is groups or custom:<a node custom property> (letters, digits and _): %r - groups used" % gb[:64])
    for k, d in (("groups", "fleet_groups"), ("vm_match", "fleet_vm_match")):
        v = o.get(d, DEFAULTS[k])
        if isinstance(v, (list, tuple)):
            out[k] = [str(x).strip()[:200] for x in v if isinstance(x, str) and str(x).strip()]
        elif v is not None:
            bad.append("portal_wall_fleet_%s is a list" % k)
    try:
        out["max_nodes"] = max(100, min(50000, int(o.get("fleet_max_nodes", DEFAULTS["max_nodes"]))))
    except (TypeError, ValueError):
        bad.append("portal_wall_fleet_max_nodes is not a number")
    try:
        out["poll_stale_minutes"] = max(5, min(1440, int(o.get("fleet_poll_stale_minutes", DEFAULTS["poll_stale_minutes"]))))
    except (TypeError, ValueError):
        bad.append("portal_wall_fleet_poll_stale_minutes is not a number")
    return out, bad


def limits(seconds):
    """(late, stale) for the fleet's feed: the wall's 3 / 10 min, longer when SolarWinds is read less often than that"""
    return max(LATE_S, 2 * int(seconds) + 30), max(STALE_S, 4 * int(seconds))


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _txt(v, n=MAX_TEXT):
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(v if v is not None else "")).strip()[:n]


def epoch(v):
    """a SWIS date ('2026-11-26T05:21:00.123Z', '...+00:00', no zone = UTC) -> epoch seconds, or None"""
    t = str(v or "").strip()
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)(?:\.\d+)?(Z|[+-]\d\d:?\d\d)?$", t)
    if not m:
        return None
    import calendar
    y, mo, d, hh, mi, ss = (int(x) for x in m.groups()[:6])
    try:
        sec = calendar.timegm((y, mo, d, hh, mi, ss, 0, 0, 0))
    except (ValueError, OverflowError):
        return None
    z = m.group(7)
    if z and z != "Z":
        sign = -1 if z[0] == "-" else 1
        z = z[1:].replace(":", "")
        sec -= sign * (int(z[:2]) * 3600 + int(z[2:]) * 60)
    return float(sec)


def short(name):
    """'SITEADIRDCV01.corp.example.mil' -> 'siteadirdcv01' (any case); an IP address is no name: ''"""
    n = str(name or "").strip().rstrip(".")
    if not n:
        return ""
    try:
        ipaddress.ip_address(n)
        return ""
    except ValueError:
        pass
    return n.split(".", 1)[0].casefold()


# ---- the reads ------------------------------------------------------------------------------------------------------------
def read_nodes(client, o, prop=""):
    """-> (rows, cut, [not read]). Orion.Nodes, named columns only, paged; an optional column this Orion refuses is left out
    (said). prop: a node custom property (IDENT checked) read as cp_group."""
    extra = list(OPTIONAL)
    notes = []
    if prop:
        if not IDENT.match(prop):
            raise ValueError("not a property name: %r" % prop[:64])
        extra.append(("N.CustomProperties.%s AS cp_group" % prop, "cp_group", "custom property %s" % prop))
    base = "SELECT %s FROM Orion.Nodes N"
    try:
        rows, cut = client.paged(base % ", ".join(NODE_COLS + [x[0] for x in extra]), "N.NodeID", None, o["page"], o["max_nodes"])
        return rows, cut, notes
    except site_swis.SwisError as e:
        if getattr(e, "kind", "") != "query":
            raise
    good = []
    for x in extra:                             # which optional column this Orion refuses: each asked for alone, once
        try:
            client.query("SELECT TOP 1 N.NodeID, %s FROM Orion.Nodes N" % x[0])
            good.append(x)
        except site_swis.SwisError as e2:
            if getattr(e2, "kind", "") != "query":
                raise
            notes.append("%s: not read (%s)%s" % (x[2], e2, " - is it one of your node custom properties? (the case counts)" if x[1] == "cp_group" else ""))
    rows, cut = client.paged(base % ", ".join(NODE_COLS + [x[0] for x in good]), "N.NodeID", None, o["page"], o["max_nodes"])
    return rows, cut, notes


def read_groups(client, o):
    """-> {"list": [[ContainerID, Name, Status]], "members": {NodeID: [ContainerID]}, "cut": bool}. Raises SwisError."""
    have = set(r.get("FullName") for r in client.query("SELECT FullName FROM Metadata.Entity WHERE FullName IN @names",
                                                       {"names": ["Orion.Container", "Orion.ContainerMembers"]}))
    if not {"Orion.Container", "Orion.ContainerMembers"} <= have:
        raise site_swis.SwisError("this Orion has no groups (Orion.Container / Orion.ContainerMembers)", "query")
    cap = o["max_nodes"]
    groups, gcut = client.paged("SELECT ContainerID, Name, Status FROM Orion.Container", "ContainerID", None, o["page"], cap)
    members, mcut = client.paged("SELECT ContainerID, MemberPrimaryID, MemberEntityType FROM Orion.ContainerMembers WHERE MemberEntityType IN @types",
                                 "ContainerID, MemberPrimaryID", {"types": ["Orion.Nodes"]}, o["page"], cap * 20)
    by = {}
    for m in members:
        if m.get("MemberEntityType") not in (None, "Orion.Nodes"):
            continue                            # the WHERE says so; an Orion that answers more: never a node by another's id
        nid, gid = m.get("MemberPrimaryID"), m.get("ContainerID")
        if nid is None or gid is None:
            continue
        by.setdefault(str(nid), [])
        if gid not in by[str(nid)]:
            by[str(nid)].append(gid)
    return {"list": [[g.get("ContainerID"), _txt(g.get("Name"), 120), g.get("Status")] for g in groups if g.get("ContainerID") is not None],
            "members": by, "cut": bool(gcut or mcut)}


class Reader(object):
    """SolarWinds for the fleet, read by this server alone while someone looks (viewed()): one read every `seconds`, the
    groups every GROUPS_EVERY s. snapshot() -> what the model reads. A refused login: no read for auth_retry s (said)."""

    def __init__(self, client_fn, opts, log=None, clock=time.time, auth_retry=900):
        self.client_fn, self.o = client_fn, opts
        self.log = log or (lambda d: None)
        self.clock, self.auth_retry = clock, max(60, int(auth_retry))
        self.lock, self.wake = threading.Lock(), threading.Event()
        self.thread, self.seen = None, 0.0
        self.st = {"state": "never", "why": "", "at": None, "tried": None, "fail_since": None, "next_try": None,
                   "nodes": None, "cut": False, "notes": [], "groups": None, "groups_at": None, "groups_tried": None, "groups_why": "", "reads": 0}
        self.fails = 0

    def viewed(self):
        now = self.clock()
        with self.lock:
            idle = now - self.seen > IDLE
            self.seen = now
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name="wall-fleet", daemon=True)
                self.thread.start()
            elif idle:
                self.wake.set()

    def _run(self):
        while True:
            now = self.clock()
            with self.lock:
                idle, due = now - self.seen > IDLE, self.st["next_try"] or 0
            if idle:
                self.wake.wait(3600)
                self.wake.clear()
                continue
            if now >= due:
                try:
                    self.read_once()
                except Exception as e:  # pylint: disable=broad-except  - never kills the thread: said, tried again
                    self._failed(e, self.clock())         # the wait counts from the failure's end, never from the read's start
            self.wake.wait(max(1.0, min(30.0, (self.st["next_try"] or now + 30) - self.clock())))
            self.wake.clear()

    def _failed(self, e, now):
        kind = getattr(e, "kind", "other")
        why = (site_swis.explain(e, getattr(self, "url", ""), 60) if site_swis is not None and isinstance(e, getattr(site_swis, "SwisError", ())) else
               "%s: %s" % (e.__class__.__name__, _txt(e, 200)))
        with self.lock:
            first = self.st["state"] not in ("auth", "error")
            self.fails += 1
            wait = self.auth_retry if kind == "auth" else min(self.o["seconds"] * 4, 15 * 2 ** min(self.fails, 6))
            self.st.update(state="auth" if kind == "auth" else "error", why=_txt(why, 300), tried=now, next_try=now + wait,
                           fail_since=self.st["fail_since"] or now)
        if first:
            self.log({"event": "wall", "source": "solarwinds-fleet", "state": "not read", "why": _txt(why, 300),
                      "again_in": int(wait)})

    def read_once(self):
        """one read (the groups too when due) -> the snapshot. Raises on a failed node read (the last good one is kept)."""
        o, now = self.o, self.clock()
        client = self.client_fn()
        self.url = getattr(client, "url", "")
        prop = o["group_by"].split(":", 1)[1] if o["group_by"].startswith("custom:") else ""
        groups, gwhy, gat, gtried = self.st["groups"], self.st["groups_why"], self.st["groups_at"], self.st["groups_tried"]
        if not prop and (gtried is None or now - gtried >= GROUPS_EVERY):
            gtried = now                        # an attempt, good or not: the next one GROUPS_EVERY s later (never each minute)
            try:
                groups, gwhy, gat = read_groups(client, o), "", now
            except Exception as e:  # pylint: disable=broad-except  - the nodes still read; the groups said not read
                if getattr(e, "kind", "") == "auth":
                    raise
                gwhy = "the groups were not read: %s" % _txt(e, 200) + (" - the last read's groups are shown" if groups else "")
        rows, cut, notes = read_nodes(client, o, prop)
        done = self.clock()
        with self.lock:
            was = self.st["state"]
            self.fails = 0
            self.st.update(state="ok", why="", at=done, tried=now, fail_since=None, next_try=done + o["seconds"], nodes=rows, cut=bool(cut),
                           notes=notes, groups=groups if not prop else None, groups_at=gat, groups_tried=gtried, groups_why=gwhy, reads=self.st["reads"] + 1)
        if was in ("auth", "error"):
            self.log({"event": "wall", "source": "solarwinds-fleet", "state": "read again"})
        return self.snapshot()

    def snapshot(self):
        with self.lock:
            return dict(self.st)


# ---- the since-tracker: when each node left "up" (kept across restarts) -------------------------------------------------
class Since(object):
    """{key: [code, since, exact]} - since: when this code was first seen; exact: the read before said otherwise (else the
    node was already so at the first read: "since then or earlier"). Kept in path (0600, written whole) when given."""

    def __init__(self, path=None, log=None):
        self.path, self.log = path, log or (lambda d: None)
        self.d = {}
        if path:
            try:
                with open(path, encoding="utf-8") as fh:
                    d = json.load(fh)
                if isinstance(d, dict):
                    self.d = dict((str(k), v) for k, v in d.items()
                                  if isinstance(v, list) and len(v) == 3 and isinstance(v[0], str) and isinstance(v[1], (int, float)))
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as e:
                self.log({"event": "wall", "fleet": "since file not read", "why": _txt(e, 200)})

    def update(self, codes, at, whole=True):
        """codes: {key: code} or {key: (code, when it was read)} -> {key: (since, exact)}. at: the read's time for a key without
        its own. whole: the read covers every node - only then is a key not in it forgotten (a cut read keeps the others)."""
        out, new = {}, ({} if whole else dict(self.d))
        changed = False
        for k, c in codes.items():
            c, t = (c if isinstance(c, tuple) else (c, at))
            old = self.d.get(k)
            if old and old[0] == c:
                new[k] = old
            else:
                new[k] = [c, float(t if t is not None else at), bool(old)]
                changed = True
            out[k] = (new[k][1], new[k][2])
        if changed or len(new) != len(self.d):
            self.d = new
            self._save()
        return out

    def _save(self):
        if not self.path:
            return
        tmp = self.path + ".tmp"
        try:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.d, fh, separators=(",", ":"))
            os.replace(tmp, self.path)
        except OSError as e:
            self.log({"event": "wall", "fleet": "since file not written", "why": _txt(e, 200)})


# ---- the model --------------------------------------------------------------------------------------------------------------
def sw_code(status, unmanaged):
    if unmanaged:
        return UNMANAGED
    w = site_swis.status_of(status) if site_swis is not None else None
    return {"Up": UP, "Warning": WARN, "Down": DOWN}.get(w, UNKNOWN)


# what vCenter says of a VM, as one letter in each row (the words go once, in the answer's "vmw")
VMW = {"1": "powered on · heartbeat OK", "2": "powered on · heartbeat lost", "3": "powered on · heartbeat intermittent",
       "4": "powered on · Tools not running", "5": "powered on · no VMware Tools", "6": "powered on · heartbeat not read",
       "o": "powered off", "s": "suspended", "?": "power state not read"}


def _vm_code(v):
    p, hb = v.get("power"), v.get("heartbeat")
    if p in ("poweredOff", "suspended"):
        return "o" if p == "poweredOff" else "s"
    if p != "poweredOn":
        return "?"
    if hb in ("green", "red", "yellow"):
        return {"green": "1", "red": "2", "yellow": "3"}[hb]
    if v.get("tools") == "guestToolsNotRunning":
        return "4"
    if v.get("tools_version") == "guestToolsNotInstalled":
        return "5"
    return "6"


def vcenter_vms(view, now, late=LATE_S):
    """the collector's view -> (vms, vcenters): each VM {vc, current, name, host, power, heartbeat, tools, ip[], guest, id};
    vcenters [[name, current, at, state]]. current: that vCenter's last read worked and is younger than LATE."""
    vms, vcs = [], []
    for k, s in sorted(((view or {}).get("sources") or {}).items()):
        if not isinstance(s, dict) or s.get("kind") != "vcenter":
            continue
        at = s.get("at")
        cur = s.get("state") == "ok" and at is not None and now - float(at) <= late
        vcs.append([str(s.get("name") or k), bool(cur), at, s.get("state") or "never"])
        for v in ((s.get("snap") or {}).get("vms") or []):
            if not isinstance(v, dict) or v.get("template") is True or v.get("vcls") is True:
                continue                        # a template, a vCLS agent: never a workload
            ips = v.get("ip")
            ips = [str(x).strip() for x in (ips if isinstance(ips, list) else [ips]) if x]
            vms.append({"vc": len(vcs) - 1, "current": bool(cur), "name": str(v.get("name") or ""), "host": str(v.get("host") or ""),
                        "power": v.get("power"), "heartbeat": v.get("heartbeat"), "tools": v.get("tools"), "tools_version": v.get("tools_version"),
                        "tools_seen": v.get("tools_seen"), "ip": ips, "guest": str(v.get("guest") or ""), "id": str(v.get("id") or v.get("name") or "")})
    return vms, vcs


def _full(name):
    """an FQDN, lower case, no trailing dot ('' for a short name or an IP)"""
    n = str(name or "").strip().rstrip(".").casefold()
    return n if "." in n and short(n) else ""


def _ip(v):
    try:
        return str(ipaddress.ip_address(str(v or "").strip()))
    except ValueError:
        return ""


def match(nodes, vms):
    """name -> VM: {node index: (vm index | None, why, the VMs it could be)}. A node and a VM are one machine when an FQDN of
    each is the same (SolarWinds' caption or DNS name; vCenter's VM name or guest host name), else when the short names are -
    but never when both have an FQDN in different domains, or both an IP and they differ (another machine of that name: a VM
    powered off in "test" never greys a "prod" node down). Two that still fit: the IP settles it, else "ambiguous" (those VMs
    are then neither compared nor "vCenter only": the node's card says so)."""
    by, byfull = {}, {}
    for i, v in enumerate(vms):
        v["_full"] = set(x for x in (_full(v["name"]), _full(v["guest"])) if x)
        v["_ips"] = set(x for x in (_ip(a) for a in v["ip"]) if x)
        for key in set(x for x in (short(v["name"]), short(v["guest"])) if x):
            by.setdefault(key, []).append(i)
        for f in v["_full"]:
            byfull.setdefault(f, []).append(i)
    out = {}
    for ni, n in enumerate(nodes):
        nfull = set(x for x in (_full(n["name"]), _full(n["dns"])) if x)
        nip = _ip(n["ip"])
        exact = set(i for f in nfull for i in byfull.get(f, ()))
        cands = exact or set(i for key in set(x for x in (short(n["name"]), short(n["dns"])) if x) for i in by.get(key, ()))
        if not cands:
            continue
        ok = set()
        for i in cands:
            v = vms[i]
            if nip and v["_ips"] and nip not in v["_ips"]:
                continue                        # both have an IP and they differ: another machine
            if i not in exact and nfull and v["_full"] and not (set(f.split(".", 1)[1] for f in nfull) & set(f.split(".", 1)[1] for f in v["_full"])):
                continue                        # both have a domain and they differ: another machine
            ok.add(i)
        if not ok:
            out[ni] = (None, "conflict: a VM of this name in vCenter has another %s - not the same machine, not compared" %
                       ("IP" if nip else "domain"), sorted(cands))
            continue
        if len(ok) > 1:
            byip = [i for i in ok if nip and nip in vms[i]["_ips"]]
            if len(byip) == 1:
                ok = set(byip)
            else:
                out[ni] = (None, "ambiguous: %d VMs named %s in vCenter%s" % (len(ok), short(n["name"]) or n["name"],
                                                                                " (the IP does not settle it)" if nip else ""), sorted(ok))
                continue
        out[ni] = (ok.pop(), "", [])
    return out


def _since_words(c, rt, loss, desc):
    if c == DOWN:
        return "not answering (100% loss)" if loss is not None and loss >= 100 else (desc or "down in SolarWinds")
    if c == WARN:
        if loss:
            return "packet loss %d%%" % round(loss)
        if rt is not None and rt >= 0:
            return "response %d ms" % round(rt) + (" · " + desc if desc and "warn" not in desc.lower() else "")
        return desc or "warning in SolarWinds"
    if c == UNMANAGED:
        return "unmanaged in SolarWinds"
    if c == UNKNOWN:
        return "SolarWinds: " + (desc or "status unknown")
    return ""


def model(snap, view, o, now, since=None, late=LATE_S):
    """snap (Reader.snapshot()), view (the wall collector's), opts (opts_of) -> the fleet's answer (compact: see the page)"""
    snap = snap or {}
    sw_late, sw_stale = limits(o["seconds"])
    rows = snap.get("nodes")
    feed = {"name": "SolarWinds", "asof": snap.get("at"), "state": snap.get("state") or "never", "why": snap.get("why") or "",
            "src": "live, read every %d s while the wall is open" % o["seconds"], "fail_since": snap.get("fail_since"),
            "next_try": snap.get("next_try"), "late": sw_late, "stale": sw_stale}
    out = {"v": 1, "now": now, "seconds": o["seconds"], "late": sw_late, "stale": sw_stale, "feed": feed, "group_by": o["group_by"],
           "row": ROW, "vmw": VMW, "groups": [], "nodes": [], "vcs": [], "hosts": [], "models": [], "incomplete": [], "notes": list(snap.get("notes") or []), "counts": {},
           "cmp": {"vc_only": 0, "not_in_vc": 0, "not_in_vc_names": [], "disagree": 0, "disagree_names": [], "ambiguous": 0, "matched": 0,
                   "off_in_vc": 0, "vm_rule": ", ".join(o["vm_match"]) or "(none)", "vc_read": False}}
    if snap.get("groups_why"):
        out["notes"].append(snap["groups_why"])
    if rows is not None and o["group_by"] == "groups":       # the services need their members: not read or cut is not whole
        if not snap.get("groups"):
            out["incomplete"].append(snap.get("groups_why") or "the SolarWinds groups were not read")
        elif snap["groups"].get("cut"):
            out["incomplete"].append("the groups' members were cut short (portal_wall_fleet_max_nodes)")
    if any(n.startswith("custom property") for n in snap.get("notes") or []):
        out["incomplete"].append("the custom property %s was not read - nodes not in their services" % o["group_by"][7:])
    if rows is None:
        out["incomplete"].append("SolarWinds not read yet" if feed["state"] == "never" else "SolarWinds: " + feed["why"])
        rows = []
    if snap.get("cut"):
        out["incomplete"].append("only the first %d nodes were read (portal_wall_fleet_max_nodes)" % o["max_nodes"])
    prop = o["group_by"].split(":", 1)[1] if o["group_by"].startswith("custom:") else ""
    nodes = []
    for r in rows:
        if not isinstance(r, dict) or r.get("NodeID") is None:
            continue
        rt, loss = _num(r.get("ResponseTime")), _num(r.get("PercentLoss"))
        c = sw_code(r.get("Status"), bool(r.get("UnManaged")))
        nodes.append({"id": str(r.get("NodeID")), "name": _txt(r.get("Caption") or r.get("DNS") or r.get("IPAddress") or r.get("NodeID")),
                      "dns": _txt(r.get("DNS")), "ip": _txt(r.get("IPAddress"), 45), "c": c, "rt": rt, "loss": loss,
                      "desc": _txt(r.get("StatusDescription")), "vendor": _txt(r.get("Vendor"), 60), "model": _txt(r.get("MachineType"), 60),
                      "polled": epoch(r.get("LastSync")), "cp": _txt(r.get("cp_group"), 120) if prop else ""})
    # ---- groups: SolarWinds groups (direct node members) or a node custom property's value; portal_wall_fleet_groups limits and orders
    gnames, member = {}, {}
    if prop:
        for n in nodes:
            if n["cp"]:
                gnames.setdefault(n["cp"].casefold(), n["cp"])
                member[n["id"]] = [n["cp"].casefold()]
        swst = {}
    else:
        g = snap.get("groups") or {}
        swst = {}
        for gid, name, st in g.get("list") or []:
            if name:
                gnames.setdefault(name.casefold(), name)
                swst[name.casefold()] = sw_code(st, False)
        idname = dict((gid, name.casefold()) for gid, name, st in g.get("list") or [] if name)
        for nid, gids in (g.get("members") or {}).items():
            member[str(nid)] = [idname[x] for x in gids if x in idname]
    want = [x.casefold() for x in o["groups"]]
    if want:
        order = [k for k in want if k in gnames]
    else:
        used = set(k for v in member.values() for k in v)
        order = sorted((k for k in gnames if k in used), key=lambda k: gnames[k].casefold())
    gi_of = dict((k, i) for i, k in enumerate(order))
    groups = [[gnames[k], "c" if prop else "g", swst.get(k, "")] for k in order]
    nog = len(groups)
    groups.append([NO_GROUP, "n", ""])
    # ---- vCenter
    vms, vcs = vcenter_vms(view, now, late)
    out["vcs"] = [[v[0], v[2], v[3]] for v in vcs]
    # "not found in vCenter" only when every vCenter's read is current (else a VM may just be unread there)
    out["cmp"]["vc_read"] = bool(vcs) and all(v[1] for v in vcs)
    vct = dict((i, v[2]) for i, v in enumerate(vcs))
    at = snap.get("at")
    # SolarWinds whole and current: only then may a VM be said "not monitored in SolarWinds"
    sw_whole = rows is not None and not out["incomplete"] and feed["state"] == "ok" and at is not None and now - float(at) <= sw_stale
    poll_stale = o["poll_stale_minutes"] * 60
    pairs = match(nodes, vms) if vms else {}
    used_vm = set()
    rx = o["vm_match"]
    codes = {}
    built = []
    tabs = {"hosts": {}, "models": {}}

    def idx(t, v):
        if not v:
            return -1
        d = tabs[t]
        if v not in d:
            d[v] = len(d)
            out[t].append(v)
        return d[v]
    for ni, n in enumerate(nodes):
        gl = [gi_of[k] for k in member.get(n["id"], []) if k in gi_of]
        gl = sorted(set(gl))
        gi = gl[0] if gl else nog
        c, why, flags, vcode, host, vci = n["c"], "", "", "", "", -1
        why = _since_words(c, n["rt"], n["loss"], n["desc"])
        vi, mwhy, maybe = pairs.get(ni, (None, "", []))
        if mwhy.startswith("ambiguous"):
            flags += "a"
            used_vm.update(maybe)               # neither compared nor "vCenter only": which one is not known
            out["cmp"]["ambiguous"] += 1
        elif mwhy:
            flags += "c"                        # a VM of the name is another machine: it stays "vCenter only"
        pa = round(at - n["polled"]) if at and n["polled"] else None
        if pa is not None and pa > poll_stale and c in (UP, WARN, DOWN):
            flags += "o"                        # Orion's own poll of it is old: its status is not current (never green)
            why = (why + " · " if why else "") + "SolarWinds last polled it %d min before the read" % (pa // 60)
        if vi is not None:
            v = vms[vi]
            used_vm.add(vi)
            out["cmp"]["matched"] += 1
            vcode, host, vci = _vm_code(v), v["host"], v["vc"]
            if not v["current"]:
                flags += "s"                    # that vCenter's read is not current: shown, never compared
            elif c == DOWN and v["power"] in ("poweredOff", "suspended"):
                c, why = OFF, "%s in vCenter (SolarWinds: down)" % ("powered off" if v["power"] == "poweredOff" else "suspended")
                out["cmp"]["off_in_vc"] += 1
            elif c == DOWN and v["power"] == "poweredOn" and v["heartbeat"] == "green":
                flags += "g"
                why += " · vCenter: running, heartbeat OK - check the network path"
                out["cmp"]["disagree"] += 1
                if len(out["cmp"]["disagree_names"]) < 20:
                    out["cmp"]["disagree_names"].append(n["name"])
        elif not mwhy and out["cmp"]["vc_read"] and any(fnmatch.fnmatchcase(x.casefold(), p.casefold()) for p in rx for x in (n["vendor"], n["model"]) if x):
            flags += "n"
            out["cmp"]["not_in_vc"] += 1
            if len(out["cmp"]["not_in_vc_names"]) < 20:
                out["cmp"]["not_in_vc_names"].append(n["name"])
        codes["sw:" + n["id"]] = (c, at)
        built.append([gi, n["name"], n["ip"], c, None if n["rt"] is None or n["rt"] < 0 else round(n["rt"]), None if n["loss"] is None else round(n["loss"]),
                      0, 0, why, vcode, idx("hosts", host), vci, flags, n["id"], [x for x in gl if x != gi],
                      pa, n["desc"] if c != UP else "",
                      idx("models", (n["vendor"] + " · " + n["model"]).strip(" ·"))])
    # ---- vCenter only: VMs SolarWinds does not watch (their status from vCenter; not current there: grey on the page)
    vco = []
    for i, v in enumerate(vms if sw_whole else []):
        if i in used_vm:
            continue
        p, hb = v["power"], v["heartbeat"]
        if p in ("poweredOff", "suspended"):
            c, why = OFF, "%s in vCenter" % ("powered off" if p == "poweredOff" else "suspended")
        elif p != "poweredOn":
            c, why = UNKNOWN, "vCenter: power state not read"
        elif hb == "green":
            c, why = UP, ""
        elif hb == "red" or (v["tools"] == "guestToolsNotRunning" and v["tools_seen"] is True):
            c, why = DOWN, "vCenter: powered on, " + ("Tools heartbeat lost" if hb == "red" else "Tools stopped") + " (not in SolarWinds)"
        elif hb == "yellow":
            c, why = WARN, "vCenter: heartbeat intermittent (not in SolarWinds)"
        else:
            c, why = UNKNOWN, "vCenter: powered on, no heartbeat to judge (Tools not running or not installed)"
        key = "vc:%s:%s" % (vcs[v["vc"]][0], v["id"])
        codes[key] = (c, vct.get(v["vc"]) or at)
        vco.append([nog + 1, v["name"], (v["ip"] or [""])[0], c, None, None, 0, 0, why, _vm_code(v), idx("hosts", v["host"]), v["vc"], "v" + ("" if v["current"] else "s"), key, [], None, "", -1])
    if vco:
        groups.append([VC_ONLY, "v", ""])
    out["cmp"]["vc_only"] = len(vco) if sw_whole else None        # None: SolarWinds not whole and current - not counted
    built.extend(vco)
    # ---- since: when each left its last code (the tracker, each source by its own read's time), only for what is not up
    sn = (since.update(codes, at, whole=sw_whole) if since is not None and at else {})
    for row in built:
        key = ("sw:" + row[13]) if "v" not in row[12] else row[13]
        s = sn.get(key)
        if s and row[3] in (DOWN, WARN, OFF, UNKNOWN, UNMANAGED):
            row[6], row[7] = round(s[0]), 1 if s[1] else 0
    built.sort(key=lambda r: (r[0], r[1].casefold()))
    out["groups"], out["nodes"] = groups, built
    cnt = dict((k, 0) for k in (UP, WARN, DOWN, OFF, UNMANAGED, UNKNOWN))
    seen = set()
    for r in built:
        if r[13] not in seen:
            seen.add(r[13])
            cnt[r[3]] += 1
    out["counts"] = cnt
    out["complete"] = not out["incomplete"]
    return out


def conds(fm, now):
    """the fleet's wall conditions: per group, one for its nodes down (critical) and one for its nodes warning - a node in two
    groups counts in both; the vCenter-only group none (the VMware area has those VMs). -> (conds, ok ids)."""
    cs, ok = [], []
    if not fm or fm["feed"].get("state") != "ok" and fm["feed"].get("asof") is None:
        return cs, ok
    per = {}
    for r in fm["nodes"]:
        if "v" in r[12]:
            continue
        for gi in [r[0]] + list(r[14] or []):
            per.setdefault(gi, []).append(r)
    for gi, g in enumerate(fm["groups"]):
        if g[1] == "v":
            continue
        rs = per.get(gi, [])
        for code, kind, word in ((DOWN, "fleetdown", "down"), (WARN, "fleetwarn", "warning")):
            bad = sorted([r for r in rs if r[3] == code], key=lambda r: (r[6] or now, r[1]))
            cid = "fleet:%s:%s" % (kind[5:], g[0])
            if not bad:
                # seen OK only when every member's state is known and current: an unknown, unmanaged or old poll is no recovery
                if fm.get("complete") and all(r[3] in (UP, WARN, DOWN, OFF) and "o" not in r[12] for r in rs):
                    ok.append(cid)
                continue
            names = ", ".join(r[1] for r in bad[:3]) + (" +%d more" % (len(bad) - 3) if len(bad) > 3 else "")
            starts = [r[6] for r in bad if r[6]]
            cs.append({"id": cid, "kind": kind, "t": "%s: %d node%s %s - %s" % (g[0], len(bad), "" if len(bad) == 1 else "s", word, names),
                       "obj": " · ".join("%s %s%s" % (r[1], r[2] or "no IP", (" - " + r[8]) if r[8] else "") for r in bad[:2]) + (" · SolarWinds group" if g[1] == "g" else ""),
                       "feed": "sw:fleet", "group": g[0], "n": len(bad), "start": min(starts) if starts else None})
    return cs, ok


def summary(fm):
    """the area in short for the wall's own answer (/api/wall): counts and the worst groups - the page's Strata / Relay / Metro tiles"""
    if not fm:
        return None
    bad = {}
    for r in fm["nodes"]:
        if r[3] in (DOWN, WARN) and "v" not in r[12]:       # vCenter-only VMs: the VMware area's, as in conds()
            for gi in [r[0]] + list(r[14] or []):            # every group it is in, as conds()
                b = bad.setdefault(fm["groups"][gi][0], [0, 0])
                b[0 if r[3] == DOWN else 1] += 1
    worst = sorted(bad.items(), key=lambda kv: (-kv[1][0], -kv[1][1], kv[0]))[:6]
    return {"counts": fm["counts"], "groups": len([g for g in fm["groups"] if g[1] in ("g", "c")]), "worst": [[k, v[0], v[1]] for k, v in worst],
            "cmp": dict((k, fm["cmp"][k]) for k in ("vc_only", "not_in_vc", "disagree", "ambiguous", "off_in_vc")), "complete": fm.get("complete", False)}
