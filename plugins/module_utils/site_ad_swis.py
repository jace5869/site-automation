# -*- coding: utf-8 -*-
"""1.94.0: the AD health report's "From SolarWinds" - each domain controller as SolarWinds sees it. Read only.

From the SolarWinds snapshot in the report database (1.96.0, plugins/module_utils/site_swis_db.py: latest, nodes_for,
apps_for, alerts_for, events_between) when there is one not older than max_age; else SolarWinds itself (SWQL through
site_swis.Client, the DCs only). Never raises: what could not be read is said (why), the rest is given.

read(dcs, opts, conn=None, swdb=None, client=None) -> {
  ok, source ("db-snapshot" | "swis-live" | ""), read_at (UTC "YYYY-MM-DD HH:MM"), age_min, interval_min, stale, why: [...],
  sam (False: no SAM on this Orion), sam_why, events (bool: the last downs were read),
  dcs: [{dc, ip, matched ("name" | "ip" | ""), status, status_text, last_poll, cpu, mem, apps: {...}, alerts: [...],
         last_down: {at, minutes, ongoing} | None}]}
dcs: [{dc, ip}]; opts: max_age (min), days (last down window), now (test: "YYYY-MM-DDTHH:MM:SSZ")."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import re

try:
    from ansible.module_utils import site_swis as sw
except ImportError:                                      # the tests: plugins/module_utils on sys.path
    import site_swis as sw

APPS = "Orion.APM.Application"
COMPS = "Orion.APM.Component"
ALERTS = ("Orion.AlertActive", "Orion.AlertObjects", "Orion.AlertConfigurations")
UP_CODES = (1, 22)                                       # SAM: Up, and "Up" as some versions say it
EVENT_DOWN, EVENT_UP = 1, 5                              # Orion's EventTypes: Node Down, Node Up


def _clean(text, n):
    """An error said in one line, never longer than n (SWIS's own message; site_swis never puts the login in one)."""
    return re.sub(r"\s+", " ", str(text or "")).strip()[:n]


def _short(name):
    n = str(name or "").strip().lower()
    return n if sw.IPV4.match(n) else n.split(".")[0]


def _utc(x):
    """A naive UTC datetime, an ISO string or nothing -> a naive UTC datetime or None."""
    if isinstance(x, datetime.datetime):
        return x.replace(tzinfo=None) if x.tzinfo is None else x.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    s = str(x or "").strip()
    if not s:
        return None
    s = re.sub(r"\.\d+", "", s).replace("Z", "").replace("T", " ")
    s = re.sub(r"[+-]\d\d:?\d\d$", "", s).strip()
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.datetime.strptime(s, f)
        except ValueError:
            pass
    return None


def _hm(d):
    return d.strftime("%Y-%m-%d %H:%M") if d else ""


def _now(opts):
    return _utc(opts.get("now")) or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def summarize_apps(apps):
    """apps_for's list for one node -> {total, down, warning, unknown, names (not up, worst first), not_up: [...], text, status}."""
    total = sum(int(a.get("components") or 0) for a in apps)
    not_up = []
    for a in apps:
        for c in a.get("components_not_up") or []:
            not_up.append(dict(c, app=a.get("app") or ""))
        if not (a.get("components_not_up") or []) and a.get("status") in (sw.DOWN, sw.WARNING, sw.UNKNOWN) and not a.get("components"):
            not_up.append({"name": a.get("app") or "", "app": a.get("app") or "", "status": a.get("status"), "value": "",
                           "message": a.get("status_text") or ""})
    not_up.sort(key=lambda c: (sw.RANK.get(c.get("status"), 2), str(c.get("name") or "").lower()))
    cnt = dict((k, sum(1 for c in not_up if c.get("status") == k)) for k in (sw.DOWN, sw.WARNING, sw.UNKNOWN))
    total = max(total, len(not_up))
    if not apps:
        return {"total": 0, "down": 0, "warning": 0, "unknown": 0, "not_up": [], "status": "", "text": "no AD template"}
    names = [c.get("name") or "" for c in not_up]

    def some(k, word):
        sel = [c.get("name") or "" for c in not_up if c.get("status") == k]
        return "%d of %d %s: %s" % (len(sel), total, word, ", ".join(sel[:3]) + (" +%d" % (len(sel) - 3) if len(sel) > 3 else ""))
    if cnt[sw.DOWN]:
        text, st = some(sw.DOWN, "down"), sw.DOWN
    elif cnt[sw.WARNING]:
        text, st = some(sw.WARNING, "warning"), sw.WARNING
    elif cnt[sw.UNKNOWN] and cnt[sw.UNKNOWN] >= total:
        text, st = "%d of %d unknown" % (total, total), sw.UNKNOWN
    elif cnt[sw.UNKNOWN]:
        text, st = some(sw.UNKNOWN, "unknown"), sw.UNKNOWN
    else:
        text, st = "%d of %d up" % (total, total), sw.UP
    return {"total": total, "down": cnt[sw.DOWN], "warning": cnt[sw.WARNING], "unknown": cnt[sw.UNKNOWN], "names": names,
            "not_up": not_up, "status": st, "text": text}


def last_down(events, now, days):
    """A node's events (oldest first: event_time, event_type) -> its newest down in the window and how long it lasted."""
    evs = sorted((e for e in events if _utc(e.get("event_time"))), key=lambda e: _utc(e["event_time"]))
    start = now - datetime.timedelta(days=days)
    downs = [e for e in evs if int(e.get("event_type") or 0) == EVENT_DOWN and _utc(e["event_time"]) >= start]
    if not downs:
        return None
    at = _utc(downs[-1]["event_time"])
    ups = [e for e in evs if int(e.get("event_type") or 0) == EVENT_UP and _utc(e["event_time"]) > at]
    end = _utc(ups[0]["event_time"]) if ups else now
    return {"at": _hm(at), "minutes": max(0, int((end - at).total_seconds() // 60)), "ongoing": not ups}


def _match(dcs, rows_by_name, rows_by_ip):
    out = {}
    for d in dcs:
        n, ip = _short(d.get("dc")), str(d.get("ip") or "").strip()
        r = rows_by_name.get(n)
        if r:
            out[d["dc"]] = (r, "name")
        elif ip and rows_by_ip.get(ip):
            out[d["dc"]] = (rows_by_ip[ip], "ip")
        else:
            out[d["dc"]] = (None, "")
    return out


def _dc_rows(dcs, found, apps, alerts, downs, sam, ifaces=None):
    res = []
    for d in dcs:
        node, how = found.get(d["dc"], (None, ""))
        rec = {"dc": d["dc"], "ip": d.get("ip") or "", "matched": how, "status": "", "status_text": "", "last_poll": "",
               "cpu": None, "mem": None, "apps": None, "alerts": [], "last_down": None, "node_id": None, "ifaces": None}
        if node:
            nid = node.get("node_id")
            rec.update(node_id=nid, status=node.get("status") or sw.UNKNOWN, status_text=node.get("status_text") or "",
                       last_poll=_hm(_utc(node.get("last_poll"))), cpu=node.get("cpu_pct"), mem=node.get("mem_pct"))
            if sam:
                rec["apps"] = summarize_apps(apps.get(nid) or [])
            rec["alerts"] = list(alerts.get(nid) or [])
            rec["last_down"] = downs.get(nid)
            if ifaces is not None:                       # None: not read; []: none monitored
                rec["ifaces"] = list(ifaces.get(nid) or [])
        res.append(rec)
    return res


def _interval(conn):
    """The snapshot's own interval: the median gap of its last runs (minutes), or None."""
    try:
        cur = conn.cursor()
        cur.execute("SELECT read_at FROM sw_snapshot ORDER BY snap_id DESC LIMIT 4")
        ts = [_utc(r[0] if isinstance(r, (list, tuple)) else list(r.values())[0]) for r in cur.fetchall()]
    except Exception:  # noqa: BLE001 - the interval is a nicety
        return None
    ts = [t for t in ts if t]
    gaps = sorted(abs((a - b).total_seconds()) / 60.0 for a, b in zip(ts, ts[1:]))
    if not gaps:
        return None
    m = gaps[len(gaps) // 2] if len(gaps) % 2 else (gaps[len(gaps) // 2 - 1] + gaps[len(gaps) // 2]) / 2
    return int(round(m))


def from_db(dcs, opts, conn, swdb):
    """The snapshot: -> (result, why) - result None when it cannot be used (why says why)."""
    now = _now(opts)
    try:
        snap = swdb.latest(conn)
    except Exception as e:  # noqa: BLE001
        if "1146" in str(e) or "doesn't exist" in str(e):
            return None, "the SolarWinds snapshot is not in the report database yet (the job SolarWinds - snapshot has not run)"
        return None, "the report database's SolarWinds snapshot could not be read: %s" % _clean(str(e), 200)
    if not snap:
        f = None
        try:
            f = swdb.last_failure(conn)
        except Exception:  # noqa: BLE001
            pass
        return None, "no whole SolarWinds snapshot yet%s" % ((" (the last run: %s)" % _clean(str(f[1]), 160)) if f else "")
    age = int(snap.get("age_seconds") or 0) // 60
    if age > int(opts.get("max_age") or 60):
        why = "the SolarWinds snapshot is %d min old (ad_solarwinds_max_age: %d)" % (age, int(opts.get("max_age") or 60))
        try:
            f = swdb.last_failure(conn)
            if f:
                why += "; the newer runs failed: %s" % _clean(str(f[1]), 160)
        except Exception:  # noqa: BLE001
            pass
        return {"stale_snapshot": (snap, age)}, why
    return _db_read(dcs, opts, conn, swdb, snap, age, now), ""


def _db_read(dcs, opts, conn, swdb, snap, age, now):
    sid = snap.get("snap_id")
    by_name = swdb.nodes_for(conn, sid, [_short(d["dc"]) for d in dcs]) or {}
    rows_n = dict((_short(k), v) for k, v in by_name.items() if v)
    miss = [str(d.get("ip") or "") for d in dcs if not rows_n.get(_short(d["dc"])) and d.get("ip")]
    rows_i = dict((k, v) for k, v in (swdb.nodes_for(conn, sid, miss) or {}).items() if v) if miss else {}
    found = _match(dcs, rows_n, rows_i)
    ids = sorted(set(r["node_id"] for r, _ in found.values() if r))
    note = {}
    try:
        note = swdb.part_note(snap, "app") or {}
    except Exception:  # noqa: BLE001 - an older 1.96.0 without part_note: SAM is there when apps_for answers
        note = {}
    sam = not note.get("absent")
    apps = (swdb.apps_for(conn, sid, ids) or {}) if sam and ids else {}
    alerts = (swdb.alerts_for(conn, sid, ids) or {}) if ids else {}
    days = float(opts.get("days") or 30)
    downs, events = {}, True
    try:
        evs = swdb.events_between(conn, now - datetime.timedelta(days=days), now, ids) if ids else []
        per = {}
        for e in evs or []:
            per.setdefault(e.get("node_id"), []).append(e)
        downs = dict((k, last_down(v, now, days)) for k, v in per.items())
    except Exception:  # noqa: BLE001
        events = False
    ifaces = None
    if ids and hasattr(swdb, "interfaces_for"):
        try:
            ifaces = swdb.interfaces_for(conn, sid, ids) or {}
        except Exception:  # noqa: BLE001
            ifaces = None
    read_at = _utc(snap.get("read_at"))
    return {"ok": True, "source": "db-snapshot", "read_at": _hm(read_at), "age_min": age, "interval_min": _interval(conn),
            "stale": False, "why": [], "sam": sam, "sam_why": "" if sam else (note.get("why") or "no SAM on this Orion"),
            "events": events, "dcs": _dc_rows(dcs, found, apps, alerts, downs, sam, ifaces)}


def _has(client, names):
    rows = client.query("SELECT FullName FROM Metadata.Entity WHERE FullName IN @names", {"names": list(names)})
    return set(r.get("FullName") for r in rows)


def _cols(client, entity, want):
    """The first of each [choice, ...] this Orion's entity has (None: none of them)."""
    have = set(r.get("Name") for r in client.query("SELECT Name, Type FROM Metadata.Property WHERE EntityName = @e", {"e": entity}))
    return [next((c for c in ch if c in have), None) for ch in want]


def from_live(dcs, opts, client, swdb=None):
    """SolarWinds itself, the DCs only. Events (the last down) are not read live."""
    now = _now(opts)
    nodes, _cut = client.paged("SELECT N.NodeID, N.Caption, N.DNS, N.IPAddress, N.Status, N.StatusDescription, N.UnManaged, N.CPULoad, "
                               "N.PercentMemoryUsed, N.LastSync FROM Orion.Nodes N", "N.NodeID", page=int(opts.get("page") or 1000),
                               cap=int(opts.get("max_nodes") or 20000))
    rows_n, rows_i = {}, {}
    for n in nodes:
        r = {"node_id": n.get("NodeID"), "status": sw.status_of(n.get("Status"), n.get("UnManaged")), "status_text": n.get("StatusDescription") or "",
             "last_poll": n.get("LastSync"), "cpu_pct": n.get("CPULoad"), "mem_pct": n.get("PercentMemoryUsed")}
        for k in (_short(n.get("Caption")), _short(n.get("DNS"))):
            if k:
                rows_n.setdefault(k, r)
        if n.get("IPAddress"):
            rows_i.setdefault(str(n["IPAddress"]), r)
    found = _match(dcs, rows_n, rows_i)
    ids = sorted(set(r["node_id"] for r, _ in found.values() if r))
    why, ents = [], _has(client, (APPS, COMPS) + ALERTS)
    sam = APPS in ents and COMPS in ents
    apps = {}
    if sam and ids:
        try:                                             # 1.96.1's one reader when it is there, else the same queries here
            apps = swdb.apps_live(client, ids, []) if hasattr(swdb, "apps_live") else _live_apps(client, ids)
        except Exception as e:  # noqa: BLE001
            why.append("SAM's applications not read: %s" % _clean(str(e), 160))
    alerts = {}
    if all(a in ents for a in ALERTS) and ids:
        try:
            alerts = swdb.alerts_live(client, ids, []) if hasattr(swdb, "alerts_live") else _live_alerts(client, ids)
        except Exception as e:  # noqa: BLE001
            why.append("the active alerts not read: %s" % _clean(str(e), 160))
    ifaces = None
    if ids:
        try:
            ifaces = _live_ifaces(client, ids)
        except Exception as e:  # noqa: BLE001
            why.append("the DCs' interfaces not read: %s" % _clean(str(e), 160))
    return {"ok": True, "source": "swis-live", "read_at": _hm(now), "age_min": 0, "interval_min": None, "stale": False, "why": why,
            "sam": sam, "sam_why": "" if sam else "SAM's applications are not on this Orion (no %s)" % APPS,
            "events": False, "dcs": _dc_rows(dcs, found, apps, alerts, {}, sam, ifaces)}


def _live_ifaces(client, ids):
    """The DCs' monitored interfaces: name, status, and the errors and discards of the last hour where this Orion has them."""
    ein, eout, din, dout = _cols(client, "Orion.NPM.Interfaces", [("InErrorsThisHour",), ("OutErrorsThisHour",), ("InDiscardsThisHour",),
                                                                   ("OutDiscardsThisHour",)])
    extra = "".join(", " + c for c in (ein, eout, din, dout) if c)
    rows = client.query("SELECT InterfaceID, NodeID, Caption, Status%s FROM Orion.NPM.Interfaces WHERE NodeID IN @ids" % extra, {"ids": ids})
    out = {}
    for r in rows:
        num = lambda *cs: sum(int(r.get(c) or 0) for c in cs if c) if any(cs) else None   # noqa: E731
        out.setdefault(r.get("NodeID"), []).append({"name": r.get("Caption") or "", "status": sw.status_of(r.get("Status")),
                                                    "errors": num(ein, eout), "discards": num(din, dout)})
    return out


def _live_apps(client, ids):
    apps = client.query("SELECT ApplicationID, NodeID, Name, Status, StatusDescription, Unmanaged FROM Orion.APM.Application WHERE NodeID IN @ids",
                        {"ids": ids})
    aids = [a.get("ApplicationID") for a in apps]
    val, msg = _cols(client, COMPS, [("StatisticData", "ComponentStatisticData", "Statistic"),
                                     ("ComponentMessage", "StatusMessage", "ErrorMessage", "Message")])
    extra = "".join(", " + c for c in (val, msg) if c)
    comps = client.query("SELECT ComponentID, ApplicationID, Name, ComponentType, Status, StatusDescription%s FROM Orion.APM.Component "
                         "WHERE ApplicationID IN @apps" % extra, {"apps": aids}) if aids else []
    per = {}
    for c in comps:
        per.setdefault(c.get("ApplicationID"), []).append(c)
    out = {}
    for a in apps:
        cs = per.get(a.get("ApplicationID"), [])
        bad = [c for c in cs if int(c.get("Status") or 0) not in UP_CODES]
        nu = [{"component_id": c.get("ComponentID"), "name": c.get("Name") or "", "kind": c.get("ComponentType"),
               "status": sw.status_of(c.get("Status")), "status_code": c.get("Status"), "status_text": c.get("StatusDescription") or "",
               "value": c.get(val) if val else None, "message": c.get(msg) if msg else ""} for c in bad]
        nu.sort(key=lambda c: sw.RANK.get(c["status"], 2))
        out.setdefault(a.get("NodeID"), []).append({"app_id": a.get("ApplicationID"), "app": a.get("Name") or "",
                                                    "status": sw.status_of(a.get("Status"), a.get("Unmanaged")),
                                                    "status_code": a.get("Status"), "status_text": a.get("StatusDescription") or "",
                                                    "unmanaged": bool(a.get("Unmanaged")), "components": len(cs), "components_not_up": nu})
    return out


def _live_alerts(client, ids):
    node_col = _cols(client, "Orion.AlertObjects", [("RelatedNodeId", "RelatedNodeID")])[0] or "RelatedNodeId"
    objs = client.query("SELECT AlertObjectID, AlertID, EntityType, EntityCaption, %s FROM Orion.AlertObjects WHERE %s IN @ids" % (node_col, node_col),
                        {"ids": ids})
    if not objs:
        return {}
    act = client.query("SELECT AlertActiveID, AlertObjectID, TriggeredDateTime, TriggeredMessage, Acknowledged FROM Orion.AlertActive "
                       "WHERE AlertObjectID IN @ids", {"ids": [o.get("AlertObjectID") for o in objs]})
    conf = dict((c.get("AlertID"), c) for c in client.query("SELECT AlertID, Name, Severity FROM Orion.AlertConfigurations WHERE AlertID IN @ids",
                                                            {"ids": sorted(set(o.get("AlertID") for o in objs))}))
    by_obj = dict((o.get("AlertObjectID"), o) for o in objs)
    out = {}
    for a in act:
        o = by_obj.get(a.get("AlertObjectID")) or {}
        c = conf.get(o.get("AlertID")) or {}
        out.setdefault(o.get(node_col), []).append({"name": c.get("Name") or "", "severity": c.get("Severity"),
                                                    "triggered_at": a.get("TriggeredDateTime"), "message": a.get("TriggeredMessage") or "",
                                                    "entity_type": o.get("EntityType") or "", "entity_caption": o.get("EntityCaption") or "",
                                                    "acknowledged": bool(a.get("Acknowledged"))})
    for v in out.values():
        v.sort(key=lambda x: str(x.get("triggered_at") or ""), reverse=True)
    return out


def read(dcs, opts, conn=None, swdb=None, client=None):
    """The snapshot when it is there and fresh enough; else SolarWinds live; else the old snapshot, said as old; else nothing."""
    dcs = [d for d in (dcs or []) if d.get("dc")]
    why, stale = [], None
    if conn is not None and swdb is not None and hasattr(swdb, "latest"):
        try:
            r, w = from_db(dcs, opts, conn, swdb)
        except Exception as e:  # noqa: BLE001
            r, w = None, "the SolarWinds snapshot could not be read: %s" % _clean(str(e), 200)
        if r and "stale_snapshot" not in r:
            return r
        if w:
            why.append(w)
        stale = r.get("stale_snapshot") if r else None
    if client is not None:
        try:
            r = from_live(dcs, opts, client, swdb)
            r["why"] = why + r["why"]
            return r
        except Exception as e:  # noqa: BLE001
            why.append("SolarWinds could not be read: %s" % _clean(str(e), 200))
    if stale:                                            # the old snapshot is all there is: given, and said as old
        snap, age = stale
        try:
            r = _db_read(dcs, opts, conn, swdb, snap, age, _now(opts))
            r["stale"], r["why"] = True, why
            return r
        except Exception as e:  # noqa: BLE001
            why.append("the old snapshot could not be read either: %s" % _clean(str(e), 160))
    return {"ok": False, "source": "", "read_at": "", "age_min": None, "interval_min": None, "stale": False, "why": why, "sam": False,
            "sam_why": "", "events": False, "dcs": []}
