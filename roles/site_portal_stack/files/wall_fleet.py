"""1.136.0: the ops wall's "Ops monitor" area (/portal/wall?area=monitor, docs/SITE_PORTAL.md "The wall"): every node
SolarWinds watches, up or down, grouped by service, compared with what vCenter says of the same VM.

  Reader   reads SolarWinds itself (plugins/module_utils/site_swis.py: one SWQL SELECT per call, never anything else) every
           portal_wall_monitor_seconds while the wall is open: Orion.Nodes (status, response,
           loss, last poll) every read; the groups (Orion.Container, Orion.ContainerMembers) every GROUPS_EVERY s. Its own
           thread - never the vCenter collector's (a SolarWinds refusal never stops a vCenter login, a slow Orion never
           delays one). A refused login waits auth_retry s; any other failure backs off. The last good read is kept (grey).
  model()  that read and the wall collector's vCenter view -> the area's answer (/api/wall/fleet): groups, one compact row per
           node, the SolarWinds-vs-vCenter counts. Standard library, no I/O: the tests drive it with made-up reads.
  conds()  the area's wall conditions: one per group with a node down (critical) or warning - never one per node.

The rules, one place for all of them:
  - status from SolarWinds (Orion's own status codes; unmanaged grey); a node in no group: "Workloads (no group)";
  - vCenter inventory is information only. Unique FQDN or IP matches supply a dated note.
  - a stale source (the wall's LATE / STALE) is grey on the page, never green; a read cut short is not whole: never green."""
from collections import Counter
import fnmatch
import heapq
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

UP, WARN, DOWN, UNMANAGED, UNKNOWN = "u", "w", "d", "m", "x"
VERDICTS = {UP: "up", WARN: "warn", DOWN: "down", UNMANAGED: "off", UNKNOWN: "unk"}
NO_GROUP = "Workloads (no group)"
VC_ONLY = "vCenter only"
GROUPS_EVERY = 900                         # the groups change seldom: read again every 15 min (and at the first read)
IDLE = 300                                 # no viewer for this long: SolarWinds is not read until the next one
LATE_S, STALE_S = 180, 600                 # the wall's own (wall_model.LATE_S / STALE_S)
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}\Z")      # a custom property's name goes into the SWQL text: nothing else
DEFAULTS = {"seconds": 60, "group_by": "groups", "groups": [], "vm_match": ["*VMware*"], "max_nodes": 10000, "page": 1000, "poll_stale_minutes": 15}
NODE_COLS = ["N.NodeID", "N.Caption", "N.DNS", "N.IPAddress", "N.Status", "N.StatusDescription", "N.Vendor", "N.MachineType",
             "N.ResponseTime", "N.PercentLoss", "N.UnManaged"]
OPTIONAL = [("N.LastSync", "LastSync", "the last poll time (LastSync)")]
MAX_TEXT = 80
# one row per node in the answer (/api/wall/fleet "nodes"; static/wall-fleet.js reads them by these places)
ROW = ["group", "name", "ip", "code", "response_ms", "loss_pct", "since", "since_exact", "why", "vcenter_vm", "host", "vcenter", "flags",
       "key", "also_in", "polled_at", "solarwinds_says", "model", "verdict", "valid_until"]


def opts_of(o):
    """the Ops monitor settings (PORTAL_WALL_MONITOR_OPTS) with their defaults -> (opts, [what was wrong])"""
    if not isinstance(o, dict):
        return dict(DEFAULTS), ["monitor settings need an object; defaults used"]
    out, bad = dict(DEFAULTS), []
    for key, low, high in (("seconds", 60, 3600), ("max_nodes", 100, 50000), ("poll_stale_minutes", 5, 1440)):
        value = o.get(key, DEFAULTS[key])
        if type(value) is int:
            out[key] = max(low, min(high, value))
        else:
            bad.append("portal_wall_monitor_%s needs an integer; default used" % key)
    gb = o.get("group_by", "groups")
    gb = gb.strip() if isinstance(gb, str) else ""
    if gb.lower() == "groups":
        out["group_by"] = "groups"
    elif gb.lower().startswith("custom:") and IDENT.match(gb.split(":", 1)[1].strip()):
        out["group_by"] = "custom:" + gb.split(":", 1)[1].strip()
    else:
        bad.append("portal_wall_monitor_group_by is groups or custom:<a node custom property> (letters, digits and _): %r - groups used" % gb[:64])
    for k in ("groups", "vm_match"):
        v = o.get(k, DEFAULTS[k])
        if isinstance(v, list) and all(isinstance(y, str) for y in v):
            seen = set()                        # once each, any case (a duplicate would draw an empty tile)
            out[k] = [x for x in (_txt(y, 200) for y in v) if x and not (x.casefold() in seen or seen.add(x.casefold()))]
        else:
            bad.append("portal_wall_monitor_%s needs a list of strings; default used" % k)
    for key in ("swis_url", "ca"):
        value = o.get(key, "")
        if not isinstance(value, str):
            raise ValueError("%s needs a string" % key)
        out[key] = value.strip()
    return out, bad


def limits(seconds):
    """(late, stale) for the Ops monitor feed: the wall's 3 / 10 min, longer when SolarWinds is read less often than that"""
    return max(LATE_S, 2 * int(seconds) + 30), max(STALE_S, 4 * int(seconds))


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _txt(v, n=MAX_TEXT):
    v = re.sub(r"[\ud800-\udfff]", "\ufffd", str(v if v is not None else ""))
    return re.sub(r"[\x00-\x1f\x7f]+", " ", v).strip()[:n]


def valid_id(value):
    return type(value) is int and value > 0 or isinstance(value, str) and bool(re.fullmatch(r"[1-9][0-9]{0,9}", value))


def epoch(v):
    """a SWIS date ('2026-11-26T05:21:00.1234567Z', '...+00:00', no zone = UTC) -> epoch seconds, or None (any Python 3)"""
    t = str(v or "").strip()
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)(\.\d+)?(Z|[+-](?:[01]\d|2[0-3]):?[0-5]\d)?\Z", t)
    if not m:
        return None
    import calendar
    fields = tuple(int(x) for x in m.groups()[:6])
    try:
        sec = calendar.timegm(fields + (0, 0, 0))
        if time.gmtime(sec)[:6] != fields:
            return None
        sec += float(m.group(7) or 0)
    except (ValueError, OverflowError, OSError):
        return None
    z = m.group(8)
    if z and z != "Z":
        sign = -1 if z[0] == "-" else 1
        z = z[1:].replace(":", "")
        sec -= sign * (int(z[:2]) * 3600 + int(z[2:]) * 60)
    return float(sec)

# ---- the reads ------------------------------------------------------------------------------------------------------------
def _cap_rows(rows, cap):
    if len(rows) <= cap:
        return rows
    kept = rows[:cap]
    # Model inputs can also be supplied directly, without the SWQL paging reader.
    owners = {str(r["NodeID"]): i for i, r in enumerate(kept) if isinstance(r, dict) and valid_id(r.get("NodeID"))}
    for row in rows[cap:]:
        if not isinstance(row, dict) or not valid_id(row.get("NodeID")):
            continue
        i = owners.get(str(row["NodeID"]))
        if i is not None:
            if kept[i] is rows[i]:
                kept[i] = dict(kept[i], _duplicate=True, _claimed_groups=list(kept[i].get("_claimed_groups", ())))
            kept[i]["_claimed_groups"].append(_txt(row.get("cp_group"), 120))
    return kept


def read_nodes(client, o, prop="", refused=None, clock=time.time):
    """-> (rows, cut, [not read]). Orion.Nodes, named columns only, paged; an optional column this Orion refuses is left out
    (said) - and, with refused (a set kept by the caller), not asked again at the next reads. prop: a node custom property
    (IDENT checked) read as cp_group."""
    refused = refused if refused is not None else set()
    extra = [x for x in OPTIONAL if x[1] not in refused]
    notes = ["%s: not read (this Orion refused it earlier)" % x[2] for x in OPTIONAL if x[1] in refused]
    if prop:
        if not IDENT.match(prop):
            raise ValueError("not a property name: %r" % prop[:64])
        if "cp_group" in refused:
            notes.append("custom property %s: not read (this Orion refused it earlier) - is it one of your node custom properties? (the case counts)" % prop)
        else:
            extra.append(("N.CustomProperties.%s AS cp_group" % prop, "cp_group", "custom property %s" % prop))
    def pages(columns):
        fields = ", ".join(NODE_COLS + [x[0] for x in columns])
        rows, last, overlap = [], -1, 0
        cap = o["max_nodes"]
        while True:
            # Re-read the trailing ID. Its complete set may straddle the previous limit.
            size = min(o["page"] + int(bool(overlap)), cap + 1 - len(rows) + overlap)
            op = ">=" if overlap else ">"
            query = "SELECT TOP %d %s FROM Orion.Nodes N WHERE N.NodeID %s @last ORDER BY N.NodeID" % (size, fields, op)
            page = client.query(query, {"last": last})
            at = clock()
            if overlap and page and isinstance(page[0], dict) and str(page[0].get("NodeID")) == str(last):
                del rows[-overlap:]
            rows.extend(dict(r, _read_at=at) if isinstance(r, dict) else r for r in page)
            cursor = page[-1].get("NodeID") if page and isinstance(page[-1], dict) else None
            boundary_only = overlap and valid_id(cursor) and int(cursor) == last
            if boundary_only:
                rows[-1]["_duplicate"] = True
            if len(rows) > cap or (len(page) < size and not boundary_only):
                return _cap_rows(rows, cap), len(rows) > cap, notes
            if not valid_id(cursor) or int(cursor) < last:
                return rows, True, notes
            if boundary_only and len(page) == size and any(col[1] == "cp_group" for col in columns):
                # Other rows with this same ID cannot be paged by ID. Only their group names
                # can change the verdict: every service claimed by this duplicate is grey.
                claims = client.query("SELECT DISTINCT N.CustomProperties.%s AS cp_group FROM Orion.Nodes N "
                                      "WHERE N.NodeID = @last" % prop, {"last": last})
                rows[-1]["_claimed_groups"] = [_txt(r.get("cp_group"), 120) for r in claims]
            overlap = 0
            if int(cursor) != last:
                for row in reversed(page):
                    if not isinstance(row, dict) or str(row.get("NodeID")) != str(cursor):
                        break
                    overlap += 1
            last = int(cursor)

    try:
        return pages(extra)
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
            refused.add(x[1])
            notes.append("%s: not read (%s)%s" % (x[2], e2, " - is it one of your node custom properties? (the case counts)" if x[1] == "cp_group" else ""))
    return pages(good)


def read_groups(client, o):
    """-> {"list": [[ContainerID, Name, Status]], "members": {NodeID: [ContainerID]}, "cut": bool}. Raises SwisError."""
    have = set(r.get("FullName") for r in client.query("SELECT FullName FROM Metadata.Entity WHERE FullName IN @names",
                                                       {"names": ["Orion.Container", "Orion.ContainerMembers"]}))
    if not {"Orion.Container", "Orion.ContainerMembers"} <= have:
        raise site_swis.SwisError("this Orion has no groups (Orion.Container / Orion.ContainerMembers)", "query")
    cap = o["max_nodes"]
    groups, gcut = client.paged("SELECT ContainerID, Name, Status FROM Orion.Container", "ContainerID", None, o["page"], cap)
    valid_groups = [g for g in groups if isinstance(g, dict) and valid_id(g.get("ContainerID"))]
    gcut = gcut or len(valid_groups) != len(groups)
    group_ids = {str(g["ContainerID"]) for g in valid_groups}
    members, mcut = [], False
    params = {"types": ["Orion.Nodes"]}
    predicate = ""
    while True:
        size = min(o["page"], cap * 20 + 1 - len(members))
        page = client.query("SELECT TOP %d ContainerID, MemberPrimaryID, MemberEntityType FROM Orion.ContainerMembers "
                            "WHERE MemberEntityType IN @types%s ORDER BY ContainerID, MemberPrimaryID" % (size, predicate), params)
        members.extend(page)
        if len(members) > cap * 20:
            members.pop()
            mcut = True
            break
        if len(page) < size:
            break
        end = page[-1] if isinstance(page[-1], dict) else {}
        container, member = end.get("ContainerID"), end.get("MemberPrimaryID")
        if not valid_id(container) or not valid_id(member) or (container, member) == (params.get("container"), params.get("member")):
            mcut = True
            break
        predicate = " AND (ContainerID > @container OR (ContainerID = @container AND MemberPrimaryID > @member))"
        params = dict(params, container=container, member=member)
    by = {}
    for m in members:
        if not isinstance(m, dict):
            mcut = True
            continue
        if m.get("MemberEntityType") not in (None, "Orion.Nodes"):
            continue                            # the WHERE says so; an Orion that answers more: never a node by another's id
        nid, gid = m.get("MemberPrimaryID"), m.get("ContainerID")
        if not valid_id(nid) or not valid_id(gid) or str(gid) not in group_ids:
            mcut = True
            continue
        gid = int(gid)
        by.setdefault(str(nid), [])
        if gid not in by[str(nid)]:
            by[str(nid)].append(gid)
    return {"list": [[g.get("ContainerID"), _txt(g.get("Name"), 120), g.get("Status")] for g in valid_groups],
            "members": by, "cut": bool(gcut or mcut)}


class Reader(object):
    """SolarWinds for the Ops monitor, read by this server alone while someone looks (viewed()): one read every `seconds`, the
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
        self.refused = set()                    # optional columns this Orion refused: not asked again (Site portal - deploy restarts it)

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
        why = (site_swis.explain(e, getattr(self, "url", ""), getattr(self, "timeout", 60)) if site_swis is not None and isinstance(e, getattr(site_swis, "SwisError", ())) else
               "%s: %s" % (e.__class__.__name__, _txt(e, 200)))
        kind_state = "auth" if kind == "auth" else "error"
        with self.lock:
            first = self.st["state"] != kind_state             # every change of state is said (an error that becomes a refusal too)
            self.fails += 1
            wait = self.auth_retry if kind == "auth" else min(self.o["seconds"] * 4, 15 * 2 ** min(self.fails, 6))
            self.st.update(state="auth" if kind == "auth" else "error", why=_txt(why, 300), tried=now, next_try=now + wait,
                           fail_since=self.st["fail_since"] or now)
        if first:
            self.log({"event": "wall", "source": "solarwinds-monitor", "state": "not read", "why": _txt(why, 300),
                      "again_in": int(wait)})

    def read_once(self):
        """one read (the groups too when due) -> the snapshot. Raises on a failed node read (the last good one is kept)."""
        o, now = self.o, self.clock()
        client = self.client_fn()
        self.url, self.timeout = getattr(client, "url", ""), getattr(client, "timeout", 60)
        prop = o["group_by"].split(":", 1)[1] if o["group_by"].startswith("custom:") else ""
        with self.lock:
            groups, gwhy, gat, gtried = self.st["groups"], self.st["groups_why"], self.st["groups_at"], self.st["groups_tried"]
        if not prop and (gtried is None or now - gtried >= GROUPS_EVERY):
            gtried = now                        # an attempt, good or not: the next one GROUPS_EVERY s later (never each minute)
            try:
                groups, gwhy, gat = read_groups(client, o), "", now
            except Exception as e:  # pylint: disable=broad-except  - the nodes still read; the groups said not read
                if getattr(e, "kind", "") == "auth":
                    raise
                gwhy = "the groups were not read: %s" % _txt(e, 200) + (" - the last read's groups are shown" if groups else "")
            with self.lock:                     # the attempt and what it gave, together - kept even when the node read fails
                self.st.update(groups=groups, groups_why=gwhy, groups_at=gat, groups_tried=gtried)
        rows, cut, notes = read_nodes(client, o, prop, self.refused, self.clock)
        done = self.clock()
        with self.lock:
            was = self.st["state"]
            self.fails = 0
            self.st.update(state="ok", why="", at=done, tried=now, fail_since=None, next_try=done + o["seconds"], nodes=rows, cut=bool(cut),
                           notes=notes, groups=groups if not prop else None, groups_at=gat, groups_tried=gtried, groups_why=gwhy, reads=self.st["reads"] + 1)
        if was in ("auth", "error"):
            self.log({"event": "wall", "source": "solarwinds-monitor", "state": "read again"})
        return self.snapshot()

    def snapshot(self):
        with self.lock:
            return dict(self.st)


# ---- the since-tracker: when each node left "up" (kept across restarts) -------------------------------------------------
class Since(object):
    """{key: [code, since, exact]} - since: when this code was first seen; exact: the read before said otherwise (else the
    node was already so at the first read: "since then or earlier"). Kept in path (0600, written whole) when given."""

    GAP = STALE_S                       # no update for longer than this (a portal stopped): a change seen after it is not exact

    def __init__(self, path=None, log=None):
        self.path, self.log = path, log or (lambda d: None)
        self.d, self.last = {}, {}
        if path:
            try:
                with open(path, encoding="utf-8") as fh:
                    f = json.load(fh)
                d = f.get("d") if isinstance(f, dict) else None
                if isinstance(d, dict):
                    def timestamp(v):
                        return type(v) in (int, float) and 0 <= v < 253402300800  # through year 9999
                    self.last = dict((str(k), float(v)) for k, v in f["at"].items() if timestamp(v)) if isinstance(f.get("at"), dict) else {}
                    self.d = dict((str(k), v) for k, v in d.items()
                                  if isinstance(v, list) and len(v) == 3 and isinstance(v[0], str) and timestamp(v[1]))
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as e:
                self.log({"event": "wall", "fleet": "since file not read", "why": _txt(e, 200)})

    def update(self, codes, at, whole=True, gaps=None):
        """codes: {key: code} or {key: (code, when it was read)} -> {key: (since, exact)}. at: the read's time for a key without
        its own. whole: True (every key), False (none) or a set of key prefixes ("sw:", "vc:") whose reads cover every node -
        only a key of those is forgotten when not in this read (a cut read, an unread vCenter keep theirs)."""
        prefixes = ("",) if whole is True else tuple(whole or ())
        out, new = {}, dict((k, v) for k, v in self.d.items() if not any(k.startswith(p) for p in prefixes))
        changed = False
        # each source (the key's prefix) has its own last update: a change seen after a longer silence than its own limit
        # (a portal stopped, a vCenter not read for an hour) is not exact
        gaps = gaps or {}
        last = self.last if isinstance(self.last, dict) else {}
        gap = {}
        for k, c in codes.items():
            c, t = (c if isinstance(c, tuple) else (c, at))
            t = float(t if t is not None else at)
            src = k.split(":", 1)[0]
            if src not in gap:
                gap[src] = src in last and t - float(last[src]) > max(self.GAP, gaps.get(src, 0))
            old = self.d.get(k)
            if old and old[0] == c:
                new[k] = old
            else:
                new[k] = [c, t, bool(old) and not gap[src]]
                changed = True
            out[k] = (new[k][1], new[k][2])
            last[src] = max(t, float(last.get(src) or 0))
        self.last = last
        gap = any(gap.values())
        if changed or len(new) != len(self.d) or gap:
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
                json.dump({"at": self.last, "d": self.d}, fh, separators=(",", ":"))
            os.replace(tmp, self.path)
        except OSError as e:
            self.log({"event": "wall", "fleet": "since file not written", "why": _txt(e, 200)})


# ---- the model --------------------------------------------------------------------------------------------------------------
def sw_code(status, unmanaged):
    if type(status) is not int:
        return UNKNOWN
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
    if v.get("tools") == "guestToolsNotRunning":
        return "4"
    if v.get("tools_version") == "guestToolsNotInstalled":
        return "5"
    return {"green": "1", "red": "2", "yellow": "3"}.get(hb, "6")


def _generic(name):
    """a guest host name that says nothing (a fresh install's): never a match key"""
    return _full(name).split(".", 1)[0] in ("", "localhost", "localhost-live")


def vcenter_vms(view, now, late=LATE_S):
    """the collector's view -> (vms, vcenters): each VM {vc, current, name, host, power, heartbeat, tools, ip[], guest, id};
    vcenters [[name, current, at, state]]. current: that vCenter's last read worked and is younger than LATE."""
    vms, vcs = [], []
    for k, s in sorted(((view or {}).get("sources") or {}).items()):
        if not isinstance(s, dict) or s.get("kind") != "vcenter":
            continue
        at = _num(s.get("at"))
        cur = s.get("state") == "ok" and at is not None and now < at + late
        vcs.append([_txt(s.get("name") or k, 120), bool(cur), at, _txt(s.get("state") or "never")])
        for v in ((s.get("snap") or {}).get("vms") or []):
            if not isinstance(v, dict) or v.get("template") is True or v.get("vcls") is True:
                continue                        # a template, a vCLS agent: never a workload
            ips = v.get("ip")
            fields = {key: _txt(v[key], None) if isinstance(v.get(key), str) else None
                      for key in ("name", "host", "power", "heartbeat", "tools", "tools_version", "guest", "id")}
            if _generic(fields["guest"]):
                fields["guest"] = None
            fields.update(vc=len(vcs) - 1, current=bool(cur),
                          ip=[_txt(ip) for ip in (ips if isinstance(ips, list) else [ips]) if isinstance(ip, str)])
            vms.append(fields)
    return vms, vcs


def _full(name):
    n = str(name or "").strip().rstrip(".").casefold()
    try:
        ipaddress.ip_address(n)
        return ""
    except ValueError:
        labels = n.split(".")
        return n if len(labels) > 1 and len(n) <= 253 and all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels) else ""


def _ip(v):
    try:
        addr = ipaddress.ip_address(str(v or "").strip())
    except ValueError:
        return ""
    addr = getattr(addr, "ipv4_mapped", None) or addr
    return "" if addr.is_unspecified or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_reserved else str(addr)


def match(nodes, vms):
    """Only a one-to-one identity across both inventories supplies a note. Index keys once, even for shared names."""
    vm_keys = [({_full(v["name"]), _full(v["guest"])} | set(map(_ip, v["ip"]))) - {""} for v in vms]
    node_keys = [{_full(n["name"]), _full(n["dns"]), _ip(n["ip"])} - {""} for n in nodes]
    def index(keysets):
        owners = {}
        for i, keys in enumerate(keysets):
            for key in keys:
                owners[key] = None if key in owners else i
        return owners
    guests, solar = index(vm_keys), index(node_keys)
    used = {i for i, keys in enumerate(vm_keys) if any(k in solar for k in keys)}
    pairs = {}
    for i, keys in enumerate(node_keys):
        fits = {guests[k] for k in keys if k in guests}
        if fits:
            vi = next(iter(fits))
            unique = len(fits) == 1 and vi is not None and all(solar[k] == i for k in vm_keys[vi] if k in solar)
            pairs[i] = vi if unique else None
    return pairs, used


def _since_words(c, rt, loss):
    if c == DOWN:
        return "not answering (100% loss)" if loss is not None and loss >= 100 else "down in SolarWinds"
    if c == WARN:
        if loss:
            return "packet loss %d%%" % round(loss)
        if rt is not None and rt >= 0:
            return "response %d ms" % round(rt)
        return "warning in SolarWinds"
    if c == UNMANAGED:
        return "unmanaged in SolarWinds"
    if c == UNKNOWN:
        return "status unknown"
    return ""


def model(snap, view, o, now, since=None, late=LATE_S):
    """snap (Reader.snapshot()), view (the wall collector's), opts (opts_of) -> the Ops monitor answer (compact: see the page)"""
    snap = snap or {}
    at = _num(snap.get("at"))
    sw_late, sw_stale = limits(o["seconds"])
    rows = snap.get("nodes")
    feed = {"name": "SolarWinds", "asof": at, "state": snap.get("state") or "never", "why": _txt(snap.get("why"), 500),
            "src": "live, read every %d s while the wall is open" % o["seconds"], "fail_since": snap.get("fail_since"),
            "next_try": snap.get("next_try"), "late": sw_late, "stale": sw_stale}
    out = {"v": 1, "now": now, "seconds": o["seconds"], "late": sw_late, "stale": sw_stale, "feed": feed, "group_by": o["group_by"],
           "poll_stale": o["poll_stale_minutes"] * 60, "row": ROW, "vmw": VMW, "groups": [], "nodes": [], "vcs": [], "hosts": [], "models": [], "incomplete": [], "notes": [_txt(note, 500) for note in snap.get("notes") or []], "counts": {},
           "cmp": {"vc_only": 0, "not_in_vc": 0, "not_in_vc_names": [], "ambiguous": 0, "matched": 0,
                   "vm_rule": ", ".join(o["vm_match"]) or "(none)", "vc_read": False}}
    if snap.get("groups_why"):
        out["notes"].append(_txt(snap["groups_why"], 500))
    membership_until = None
    if rows is not None and o["group_by"] == "groups":       # the services need their members: not read or cut is not whole
        gat = _num(snap.get("groups_at"))
        membership_until = gat + GROUPS_EVERY + 2 * o["seconds"] if gat is not None else None
        if not snap.get("groups") or snap.get("groups_why"):
            out["incomplete"].append(_txt(snap.get("groups_why"), 500) or "the SolarWinds groups were not read")
        elif snap["groups"].get("cut"):
            out["incomplete"].append("the group membership read was incomplete")
    out["membership_until"] = membership_until
    membership_old = o["group_by"] == "groups" and (membership_until is None or now >= membership_until)
    if any(n.startswith("custom property") for n in snap.get("notes") or []):
        out["incomplete"].append("the custom property %s was not read - nodes not in their services" % o["group_by"][7:])
    if rows is None:
        out["incomplete"].append("SolarWinds not read yet" if feed["state"] == "never" else "SolarWinds: " + feed["why"])
        rows = []
    nodes_cut = snap.get("cut") or len(rows) > o["max_nodes"]
    if nodes_cut:
        out["incomplete"].append("only the first %d nodes were read (portal_wall_monitor_max_nodes)" % o["max_nodes"])
        rows = _cap_rows(rows, o["max_nodes"])
    if rows == [] and feed["state"] == "ok":
        out["incomplete"].append("SolarWinds answered with no node (does the account see the nodes?)")
    prop = o["group_by"].split(":", 1)[1] if o["group_by"].startswith("custom:") else ""
    by_id, odd = {}, 0
    lastsync = not any("LastSync" in x for x in snap.get("notes") or [])     # this Orion gives a node's last poll
    for r in rows:
        if not isinstance(r, dict) or not valid_id(r.get("NodeID")):
            odd += 1
            continue
        rt, loss = _num(r.get("ResponseTime")), _num(r.get("PercentLoss"))
        um = r.get("UnManaged")
        c = sw_code(r.get("Status"), um is True or str(um).strip().lower() in ("true", "1"))
        nid = str(r["NodeID"])
        if nid in by_id:
            by_id[nid].update(c=UNKNOWN, desc="")
            by_id[nid]["cp"].add(_txt(r.get("cp_group"), 120))
            by_id[nid]["cp"].update(r.get("_claimed_groups", ()))
            continue
        by_id[nid] = {"id": nid, "name": _txt(r.get("Caption") or r.get("DNS"), None),
                      "at": _num(r.get("_read_at", at)), "dns": _txt(r.get("DNS"), None), "ip": _txt(r.get("IPAddress")), "c": UNKNOWN if r.get("_duplicate") else c, "rt": rt, "loss": loss,
                      "desc": _txt(r.get("StatusDescription")), "vendor": _txt(r.get("Vendor"), 60), "model": _txt(r.get("MachineType"), 60),
                      "polled": epoch(r.get("LastSync")) if lastsync else None, "cp": {_txt(r.get("cp_group"), 120)} if prop else set()}
        by_id[nid]["cp"].update(r.get("_claimed_groups", ()))
    nodes = list(by_id.values())
    if odd:
        out["incomplete"].append("%d SolarWinds rows without a NodeID" % odd)
    structural_gap = bool(out["incomplete"])
    if membership_old:
        out["incomplete"].append("SolarWinds group membership is not current")
    unknown = sum(1 for n in nodes if n["c"] == UNKNOWN)
    if unknown:
        out["incomplete"].append("%d node%s with a status SolarWinds does not know (grey)" % (unknown, "" if unknown == 1 else "s"))
    # ---- groups: SolarWinds groups (direct node members) or a node custom property's value; portal_wall_monitor_groups limits and orders
    gnames, member = {}, {}
    if prop:
        for n in nodes:
            for group in sorted(n["cp"] - {""}):
                gnames.setdefault(group.casefold(), group)
                member.setdefault(n["id"], []).append(group.casefold())
        swst = {}
    else:
        g = snap.get("groups") or {}
        swst = {}
        group_list = [(gid, _txt(name, 120), st) for gid, name, st in g.get("list") or []]
        for gid, name, st in group_list:
            if name:
                gnames.setdefault(name.casefold(), name)
                swst[name.casefold()] = sw_code(st, False)
        idname = dict((str(gid), name.casefold()) for gid, name, st in group_list if name)
        for nid, gids in (g.get("members") or {}).items():
            member[str(nid)] = [idname[str(x)] for x in gids if str(x) in idname]
    missing = member.keys() - {n["id"] for n in nodes}
    if missing:
        out["incomplete"].append("%d group members not in the node read" % len(missing))
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
    # SolarWinds whole and current: only then may a VM be said "not monitored in SolarWinds"
    sw_whole = rows is not None and not out["incomplete"] and feed["state"] == "ok" and at is not None and now < at + sw_late
    deadlines = [membership_until] if membership_until is not None else []
    if feed["state"] == "ok" and at is not None and now < at + sw_late:
        deadlines.append(at + sw_late)
    poll_stale = out["poll_stale"]
    pairs, used_vm = match(nodes, vms) if vms else ({}, set())
    identities_complete = not nodes_cut and not odd and not missing and all(v[2] is not None for v in vcs)
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
        why = _since_words(c, n["rt"], n["loss"])
        vi = pairs.get(ni)
        if ni in pairs and vi is None:
            flags += "a"
            out["cmp"]["ambiguous"] += 1
        pa = now - n["polled"] if n["polled"] is not None else None
        expired = pa is not None and now >= n["polled"] + poll_stale
        page_until = n["at"] + sw_late if n["at"] is not None else None
        page_current = feed["state"] == "ok" and page_until is not None and now < page_until
        if pa is None or n["at"] is None or n["polled"] > n["at"] + 120 or expired:
            flags += "o"                        # Orion's own poll of it is old: its status is not current (never green)
            why = (why + " · " if why else "") + ("SolarWinds last polled it %d min ago" % (pa // 60) if expired else
                                                  "SolarWinds gives no reliable last poll time for it")
        if vi is not None and identities_complete:
            v = vms[vi]
            out["cmp"]["matched"] += 1
            vcode, host, vci = _vm_code(v), v["host"], v["vc"]
        elif ni not in pairs and out["cmp"]["vc_read"] and any(fnmatch.fnmatchcase(x.casefold(), p.casefold()) for p in rx for x in (n["vendor"], n["model"]) if x):
            flags += "n"
            out["cmp"]["not_in_vc"] += 1
            if len(out["cmp"]["not_in_vc_names"]) < 20:
                out["cmp"]["not_in_vc_names"].append(_txt(n["name"]))
        until = None
        if page_current and c != UNKNOWN and "o" not in flags and not (structural_gap and c == UP):
            until = min(page_until, n["polled"] + poll_stale)
        verdict = VERDICTS[c] if until is not None else "unk"
        said = n["desc"] if verdict not in ("up", "unk") else ""
        words = re.findall(r"\b(up|down|warning|unmanaged|unknown)\b", said.lower())
        if any(word != {"warn": "warning", "off": "unmanaged"}.get(verdict, verdict) for word in words):
            said = ""
        codes["sw:" + n["id"]] = (c, n["at"])
        built.append([gi, _txt(n["name"] or n["ip"] or n["id"]), n["ip"], c, None if n["rt"] is None or n["rt"] < 0 else round(n["rt"]), None if n["loss"] is None else round(n["loss"]),
                      0, 0, why, vcode, idx("hosts", host), vci, flags, n["id"], [x for x in gl if x != gi],
                      n["polled"], said,
                      idx("models", (n["vendor"] + " · " + n["model"]).strip(" ·")), verdict, until])
    if any(n["at"] is None or now >= n["at"] + sw_late for n in nodes):
        out["incomplete"].append("SolarWinds node page not current")
        sw_whole = False
    room = min(200, o["max_nodes"] - len(built))
    for nid in heapq.nsmallest(room, missing):
        missing.remove(nid)  # remaining IDs count in the totals without a placeholder row
        gl = sorted({gi_of[k] for k in member[nid] if k in gi_of}) or [nog]
        built.append([gl[0], "Node " + nid, "", UNKNOWN, None, None, 0, 0, "group member not in the node read",
                      "", -1, -1, "", nid, gl[1:], None, "", -1, "unk", None])
    old = sum(1 for r in built if "o" in r[12])
    if old:
        out["incomplete"].append("%d node%s without a current SolarWinds poll (LastSync, limit %d min)" %
                                 (old, "" if old == 1 else "s", o["poll_stale_minutes"]))
        sw_whole = False
    # ---- vCenter inventory without a monitored node
    vco, vc_count = [], 0
    for i, v in enumerate(vms if sw_whole else []):
        if i in used_vm:
            continue
        vc_count += 1
        if len(built) + len(vco) >= o["max_nodes"]:
            continue
        key = "vc:%s:%s" % (vcs[v["vc"]][0], v["id"])
        vco.append([nog + 1, _txt(v["name"]), (v["ip"] or [""])[0], UNKNOWN, None, None, 0, 0, "", "", -1,
                    v["vc"], "v", key, [], None, "", -1, "info", None])
    if vc_count > len(vco):
        out["notes"].append("%d vCenter-only VMs beyond the display limit" % (vc_count - len(vco)))
    if vco:
        groups.append([VC_ONLY, "v", ""])
    out["cmp"]["vc_only"] = vc_count if sw_whole else None        # None: SolarWinds not whole and current - not counted
    built.extend(vco)
    # ---- since: when each left its last code (the tracker, each source by its own read's time), only for what is not up
    sn = (since.update(codes, at, whole=sw_whole, gaps={"sw": sw_stale}) if since is not None and at else {})
    for row in built:
        s = sn.get("sw:" + row[13])
        if s and row[3] in (DOWN, WARN, UNKNOWN, UNMANAGED):
            row[6], row[7] = round(s[0]), 1 if s[1] else 0
    built.sort(key=lambda r: (r[0], r[1].casefold()))
    out["groups"], out["nodes"] = groups, built
    cnt = dict((k, 0) for k in (UP, WARN, DOWN, UNMANAGED, UNKNOWN))
    per = [[] for g in groups]
    solar = []
    for r in built:
        if "v" not in r[12]:
            solar.append(r)
            cnt[UNKNOWN if r[18] == "unk" else r[3]] += 1
        for gi in [r[0]] + r[14]:
            per[gi].append(r)
        if r[19] is not None:
            deadlines.append(r[19])
    omitted = [0] * len(groups)
    for nid in missing:
        for gi in {gi_of[k] for k in member[nid] if k in gi_of} or {nog}:
            omitted[gi] += 1
    if vco:
        omitted[-1] = vc_count - len(vco)
    for gi, g in enumerate(groups):
        # An empty group is confirmed only by a current, complete read.
        incomplete = structural_gap or (not per[gi] and not sw_whole)
        if g[1] == "v":
            g.append(dict(state="info", label="information only", total=vc_count, up=0, down=0, warn=0, valid_until=None))
            continue
        g.append(rollup(per[gi], omitted[gi], incomplete or membership_old, at + sw_late if at is not None else None))
    out["verdict"] = rollup(solar, len(missing), structural_gap or membership_old or not solar, None)
    if membership_until is not None:
        for verdict in [out["verdict"]] + [g[3] for g in groups]:
            if verdict["valid_until"] is not None:
                verdict["valid_until"] = min(verdict["valid_until"], membership_until)
    out["next_expiry"] = min((d for d in deadlines if d > now), default=None)
    cnt[UNKNOWN] += len(missing)
    out["counts"] = cnt
    out["complete"] = not out["incomplete"]
    return out


def rollup(rows, missing, incomplete, empty_until):
    counts = Counter(r[18] for r in rows)
    until = min((r[19] for r in rows if r[19] is not None), default=empty_until)
    if incomplete or missing or counts["unk"]:
        state, until = "stale", None
    else:
        state = "crit" if counts["down"] else "warn" if counts["warn"] else "ok" if counts["up"] else "off"
    label = {"crit": "down", "warn": "warning", "ok": "up", "off": "no monitored nodes", "stale": "not current"}[state]
    return dict(state=state, label=label, total=len(rows) + missing, up=counts["up"], down=counts["down"],
                warn=counts["warn"], valid_until=until)


def conds(fm, now):
    """the Ops monitor wall conditions: per group, one for its nodes down (critical) and one for its nodes warning - a node in two
    groups counts in both; the vCenter-only group none (the VMware area has those VMs). -> (conds, ok ids)."""
    cs, ok = [], []
    if not fm or fm["feed"].get("state") != "ok" and fm["feed"].get("asof") is None:
        return cs, ok
    if fm["group_by"] == "groups" and (fm["membership_until"] is None or now >= fm["membership_until"]):
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
        verdict = g[3]
        can_clear = verdict["state"] == "ok" and now < (_num(verdict.get("valid_until")) or 0)
        for state, kind, word, sev in (("down", "fleetdown", "down", "crit"), ("warn", "fleetwarn", "warning", "warn")):
            bad = sorted([r for r in rs if r[18] == state and now < (_num(r[19]) or 0)], key=lambda r: (r[6] or now, r[1]))
            cid = "fleet:%s:%s" % (kind[5:], g[0])
            if not bad:
                if can_clear:
                    ok.append(cid)
                continue
            names = ", ".join(r[1] for r in bad[:3]) + (" +%d more" % (len(bad) - 3) if len(bad) > 3 else "")
            starts = [r[6] for r in bad if r[6]]
            cs.append({"id": cid, "kind": kind, "sev": sev, "t": "%s: %d node%s %s - %s" % (g[0], len(bad), "" if len(bad) == 1 else "s", word, names),
                       "obj": " · ".join("%s %s%s" % (r[1], r[2] or "no IP", (" - " + r[8]) if r[8] else "") for r in bad[:2]) + (" · SolarWinds group" if g[1] == "g" else ""),
                       "feed": "sw:fleet", "group": g[0], "n": len(bad), "start": min(starts) if starts else None})
    return cs, ok


def summary(fm):
    """the area in short for the wall's own answer (/api/wall): counts and the worst groups - the page's Strata / Relay / Metro tiles"""
    if not fm:
        return None
    worst = [[g[0], g[3]["down"], g[3]["warn"]] for g in fm["groups"] if g[1] != "v" and (g[3]["down"] or g[3]["warn"])]
    worst.sort(key=lambda group: (-group[1], -group[2], group[0]))
    return {**fm["verdict"], "counts": fm["counts"],
            "groups": len([g for g in fm["groups"] if g[1] in ("g", "c")]), "worst": worst[:6],
            "cmp": dict((k, fm["cmp"][k]) for k in ("vc_only", "not_in_vc", "ambiguous")), "complete": fm.get("complete", False)}

def extend(wall, fleet, why, tracker, now):
    """Add the preview area after building the base wall. Track its conditions independently."""
    from wall_model import Tracker, fstate
    kinds = {
        "fleetdown": {"area": "monitor", "sev": "crit", "label": "A node of a service group down in SolarWinds", "grp": "fleet"},
        "fleetwarn": {"area": "monitor", "sev": "warn", "label": "A node of a service group warning in SolarWinds", "grp": "fleet"},
    }
    inc = fleet["incomplete"] if fleet is not None else [why]
    area = dict(id="monitor", name="Ops monitor", short="Monitor", sub="SolarWinds", team="Service owners",
                on=fleet is not None, built=True, feeds=["sw:fleet"] if fleet is not None else [],
                complete=not inc, incomplete=inc[:5], not_on_wall="Individual node alerts and SolarWinds events.")
    wall["areas"].append(area)
    wall["kinds"].update(kinds)
    if fleet is None:
        area["why"] = why
        return
    feed = dict(fleet["feed"])
    feed["fresh"] = fstate(None if feed["asof"] is None else max(0, now - feed["asof"]), feed)
    wall["feeds"]["sw:fleet"] = feed
    wall["fleet"] = summary(fleet)
    conditions, recovered = conds(fleet, now)
    for c in conditions:
        c.update(area="monitor", sev=kinds[c["kind"]]["sev"], seen=feed["asof"], end=None)
    if tracker is not None:
        if not hasattr(tracker, "fleet"):
            tracker.fleet = Tracker()
        conditions = tracker.fleet.update(conditions, now, set(recovered))
    wall["conds"].extend(conditions)


def ordering(data, log):
    """Reserve a boot number before publishing the first board response."""
    from itertools import count
    import tempfile
    path = os.path.join(data, "wall-fleet-boot")
    import fcntl
    try:
        directory = os.open(data, os.O_RDONLY)
        try:
            fcntl.flock(directory, fcntl.LOCK_EX)
            try:
                with open(path) as source:
                    boot = int(source.read()) + 1
            except FileNotFoundError:
                boot = 1
            with tempfile.NamedTemporaryFile(mode="w", dir=data, delete=False) as target:
                target.write(str(boot))
            try:
                os.replace(target.name, path)
            finally:
                if os.path.exists(target.name):
                    os.unlink(target.name)
        finally:
            os.close(directory)
    except (OSError, ValueError) as err:
        boot = int(time.time())
        log({"event": "wall", "fleet": "using clock for boot counter", "why": str(err)})
    return boot, count(1)

class Board:
    """Monitor reader, cached join and ordered answers. The caller holds the wall lock for model and answer."""
    def __init__(self, cfg, log, reader=None):
        self.cfg, self.log, self.reader = cfg, log, reader
        self.boot, self.seq = ordering(cfg.data, log)
        self.cached, self.key, self.body, self.since = None, None, "", None
        if reader is None and not cfg.wall_fleet_why:
            opts = cfg.wall_fleet_opts
            client = None

            def get_client():
                nonlocal client
                if client is None:
                    client = site_swis.Client(opts["swis_url"], cfg.swis_user, cfg.swis_password, verify=cfg.swis_verify,
                                              ca_path=opts["ca"], timeout=cfg.swis_timeout)
                return client
            self.reader = Reader(get_client, opts, log=log, auth_retry=cfg.wall_auth_retry)
        if self.reader is not None:
            self.since = Since(os.path.join(cfg.data, "wall-fleet-since.json"), log=log)

    def viewed(self):
        if self.reader is not None:
            self.reader.viewed()

    def model(self, view, now):
        """Cache the join under the wall lock until a source changes or the first freshness deadline."""
        if self.reader is None:
            return None
        snap = self.reader.snapshot()
        vcs = tuple(sorted((k, s.get("at"), s.get("state")) for k, s in ((view or {}).get("sources") or {}).items()
                           if isinstance(s, dict) and s.get("kind") == "vcenter"))
        key = (snap.get("at"), snap.get("state"), snap.get("reads"), snap.get("why"),
               snap.get("groups_why"), snap.get("groups_at"), snap.get("groups_tried"), vcs)
        expiry = (self.cached or {}).get("next_expiry")
        if key != self.key or self.cached is None or (expiry is not None and now >= expiry):
            self.cached = model(snap, view, self.cfg.wall_fleet_opts, now, since=self.since)
            self.key, self.body = key, ""
        return self.cached

    def answer(self, collector):
        now, seq = time.time(), next(self.seq)
        try:
            self.viewed()
            fm = self.model(collector.view() if collector is not None else None, now)
            if fm is None:
                return 404, json.dumps(dict(boot=self.boot, seq=seq, error=self.cfg.wall_fleet_why))
            if not self.body:
                fields = dict(fm, zone=self.cfg.tz_name or "UTC", title=self.cfg.wall_title, enclave=self.cfg.wall_enclave)
                del fields["now"]
                self.body = json.dumps(fields, separators=(",", ":"), default=str)[1:]
            return 200, '{"now":%s,"boot":%d,"seq":%d,%s' % (now, self.boot, seq, self.body)
        except Exception as err:
            self.log({"event": "wall", "fleet": "not made", "why": _txt(err, 200)})
            return 503, json.dumps(dict(boot=self.boot, seq=seq, error="The Ops monitor answer could not be made."))
