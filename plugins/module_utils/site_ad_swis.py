# -*- coding: utf-8 -*-
"""1.94.0: the AD health report's "From SolarWinds" - each domain controller as SolarWinds sees it. Read only.

From the SolarWinds snapshot in the report database (1.96.0, plugins/module_utils/site_swis_db.py: latest, nodes_for,
apps_for, alerts_for, events_between) when there is one not older than max_age; else SolarWinds itself (SWQL through
site_swis.Client, the DCs only). Never raises: what could not be read is said (why), the rest is given.

read(dcs, opts, conn=None, swdb=None, client=None) -> {
  ok, source ("db-snapshot" | "swis-live" | ""), read_at (UTC "YYYY-MM-DD HH:MM"), age_min, interval_min, stale, why: [...],
  sam (False: no SAM on this Orion), sam_why, events (bool: the last downs were read),
  dcs: [{dc, ip, matched ("name" | "ip" | ""), status, status_text, last_poll, cpu, mem, apps: {...}, alerts: [...],
         last_down: {at, minutes, ongoing, blip (1.116.5: a down under 1 min, given only when the window has no longer one)} | None}]}
dcs: [{dc, ip}]; opts: max_age (min), days (last down window), now (test: "YYYY-MM-DDTHH:MM:SSZ")."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

try:
    from ansible.module_utils import site_ad_ident as ident
except ImportError:
    import site_ad_ident as ident

import datetime
import inspect
import re

try:
    from ansible.module_utils import site_swis as sw
    from ansible.module_utils import site_swis_db as sdb
except ImportError:                                      # the tests: plugins/module_utils on sys.path
    import site_swis as sw
    import site_swis_db as sdb

APPS = "Orion.APM.Application"
COMPS = "Orion.APM.Component"
ALERTS = ("Orion.AlertActive", "Orion.AlertObjects", "Orion.AlertConfigurations")
EVENT_DOWN, EVENT_UP = 1, 5                              # Orion's EventTypes: Node Down, Node Up


def _pct(x):
    """CPU / memory %: a number, None when SolarWinds has none (-1, -2: not polled)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v < 0 else v


def _say(e):
    """What failed, in words the report may show: SolarWinds' own answer (site_swis never puts the login in one); for
    anything else (a database error names its user and host) fixed words by its class or code."""
    if isinstance(e, sw.SwisError):
        return _clean(str(e), 200)
    code = e.args[0] if getattr(e, "args", None) and isinstance(e.args[0], int) else None
    words = {1045: "the login was refused (the Report database credential)", 1044: "the account may not read that database",
             1049: "no such database (the Report database credential's database name)", 2003: "the database server did not answer",
             2005: "the database server's name is not known", 2013: "the connection was lost", 1146: "a table is missing"}
    return words.get(code) or ("a %s" % e.__class__.__name__)


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
    s = re.sub(r"\.\d+", "", s).replace("T", " ")
    off = datetime.timedelta(0)
    m = re.search(r"(Z|[+-]\d\d:?\d\d)$", s)
    if m:                                                # an offset: converted to UTC, not dropped
        z = m.group(1)
        if z != "Z":
            sign = 1 if z[0] == "+" else -1
            off = sign * datetime.timedelta(hours=int(z[1:3]), minutes=int(z[-2:]))
        s = s[:m.start()].strip()
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.datetime.strptime(s, f) - off
        except ValueError:
            pass
    return None


def _hm(d):
    return d.strftime("%Y-%m-%d %H:%M") if d else ""


def _now(opts):
    return _utc(opts.get("now")) or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


DISABLED_CODE = 27                                       # Orion.StatusInfo's Disabled


def disabled(c):
    """1.116.5: a SAM component (or application) switched off in SolarWinds: Orion's Disabled status, or SolarWinds' own
    word "Disabled" as its message or status text (SAM gives such a component the Unknown status). Never a problem."""
    try:
        if int(c.get("status_code")) == DISABLED_CODE:
            return True
    except (TypeError, ValueError):
        pass
    return any(str(c.get(k) or "").strip().lower() == "disabled" for k in ("message", "status_text"))


def summarize_apps(apps):
    """apps_for's list for one node -> {total, down, warning, unknown, disabled, names (not up, worst first), not_up: [...],
    text, status}. 1.116.5: a component disabled in SolarWinds is not counted (not in total, not_up or the status); the
    text says how many once, at its end."""
    total = sum(int(a.get("components") or 0) for a in apps)
    not_up, off = [], 0
    for a in apps:
        raw = a.get("components_not_up") or []
        for c in raw:
            if disabled(c):
                off += 1
            else:
                not_up.append(dict(c, app=a.get("app") or ""))
        if not raw and a.get("status") in (sw.DOWN, sw.WARNING, sw.UNKNOWN) and not a.get("components"):
            if disabled(a):
                off += 1
            else:
                not_up.append({"name": a.get("app") or "", "app": a.get("app") or "", "status": a.get("status"), "value": "",
                               "message": a.get("status_text") or ""})
    not_up.sort(key=lambda c: (sw.RANK.get(c.get("status"), 2), str(c.get("name") or "").lower()))
    cnt = dict((k, sum(1 for c in not_up if c.get("status") == k)) for k in (sw.DOWN, sw.WARNING, sw.UNKNOWN))
    total = max(total - off, len(not_up), 0)
    if not apps:
        return {"total": 0, "down": 0, "warning": 0, "unknown": 0, "disabled": 0, "not_up": [], "status": "", "text": "no AD template"}
    names = [c.get("name") or "" for c in not_up]
    note = " · %d disabled in SolarWinds" % off if off else ""

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
    elif not total and off:                              # every monitor switched off: nothing up, nothing wrong
        text, st, note = "all %d disabled in SolarWinds" % off, "", ""
    else:
        text, st = "%d of %d up" % (total, total), sw.UP
    return {"total": total, "down": cnt[sw.DOWN], "warning": cnt[sw.WARNING], "unknown": cnt[sw.UNKNOWN], "disabled": off,
            "names": names, "not_up": not_up, "status": st, "text": text + note}


def last_down(events, now, days):
    """A node's events (oldest first: event_time, event_type) -> its newest down in the window and how long it lasted.
    1.116.5: a down under 1 minute (SolarWinds' polling blip) is not an outage: the newest down of 1 minute or more is
    given; only when there is none, the newest blip ({..., "blip": True})."""
    evs = sorted((e for e in events if _utc(e.get("event_time"))), key=lambda e: _utc(e["event_time"]))
    start = now - datetime.timedelta(days=days)
    downs = [e for e in evs if int(e.get("event_type") or 0) == EVENT_DOWN and _utc(e["event_time"]) >= start]
    if not downs:
        return None
    blip = None
    for d in reversed(downs):
        at = _utc(d["event_time"])
        ups = [e for e in evs if int(e.get("event_type") or 0) == EVENT_UP and _utc(e["event_time"]) > at]
        end = _utc(ups[0]["event_time"]) if ups else now
        rec = {"at": _hm(at), "minutes": max(0, int((end - at).total_seconds() // 60)), "ongoing": not ups}
        if rec["ongoing"] or rec["minutes"] >= 1:
            return rec
        if blip is None:
            blip = dict(rec, blip=True)
    return blip


def _full(x):
    """A host name as written (lower case) when it has a domain, else ''."""
    n = str(x or "").strip().lower().rstrip(".")
    return n if "." in n and not sw.IPV4.match(n) else ""


def match(dcs, nodes):
    """Full DNS name, unique label, then unique IP; a node claimed twice belongs to neither."""
    full, short, ips = {}, {}, {}
    for n in nodes:
        for k in set(x for x in (_full(n.get("name")), _full(n.get("dns"))) if x):
            full.setdefault(k, []).append(n)
        for k in set(x for x in (_short(n.get("name")), _short(n.get("dns"))) if x and not sw.IPV4.match(x)):
            short.setdefault(k, []).append(n)
        if n.get("ip"):
            ips.setdefault(str(n["ip"]).strip(), []).append(n)

    def uniq(rows):
        out = []
        for r in rows:
            if all(r.get("node_id") != o.get("node_id") for o in out):
                out.append(r)
        return out
    out, claimed = {}, {}
    for i, d in enumerate(dcs):
        fq, sn, ip = _full(d.get("dns_host") or d.get("fqdn")), ident.short_name(d), str(d.get("ip") or "").strip()
        node, how, why = None, "", ""
        c = uniq(full.get(fq, [])) if fq else []
        if len(c) == 1:
            node, how = c[0], "name"
        elif len(c) > 1:
            why = "ambiguous (%d nodes named %s)" % (len(c), fq)
        if node is None and not c:
            c = uniq(short.get(sn, []))
            if len(c) == 1:
                node, how = c[0], "name"
            elif len(c) > 1:
                why = "ambiguous (%d nodes named %s)" % (len(c), sn)
        if node is None and ip:
            c = uniq(ips.get(ip, []))
            if len(c) == 1:
                node, how, why = c[0], "ip", ""
            elif len(c) > 1:
                why = "ambiguous (%d nodes with IP %s)" % (len(c), ip)
        if node is not None:
            claimed.setdefault(node.get("node_id"), []).append(i)
        out[i] = (node, how, why)
    for indices in claimed.values():
        if len(indices) > 1:
            names = ", ".join(ident.short_name(dcs[i]) for i in indices)
            for i in indices:
                out[i] = (None, "", "not matched: the same SolarWinds node claimed by " + names)
    return out


def _dc_rows(dcs, found, apps, alerts, downs, sam, ifaces=None):
    res = []
    for i, d in enumerate(dcs):
        node, how, why = found.get(i, (None, "", ""))
        rec = {"dc_id": ident.dc_id(d), "dc": d["dc"], "domain": d.get("domain") or "", "fqdn": d.get("fqdn") or "", "ip": d.get("ip") or "", "matched": how if node else "", "unmatched_why": why if not node else "", "status": "", "status_text": "", "last_poll": "",
               "cpu": None, "mem": None, "apps": None, "alerts": [], "last_down": None, "node_id": d.get("node_id"), "ifaces": None}
        if node:
            nid = node.get("node_id")
            crit = str(node.get("status_code")) == "14"          # Orion's Critical: worse than a warning, said as it is
            rec.update(node_id=nid, status="Critical" if crit else (node.get("status") or sw.UNKNOWN), status_text=node.get("status_text") or "",
                       last_poll=_hm(_utc(node.get("last_poll"))), cpu=_pct(node.get("cpu_pct")), mem=_pct(node.get("mem_pct")))
            if sam:
                rec["apps"] = summarize_apps(apps.get(nid) or [])
            rec["alerts"] = list(alerts.get(nid) or [])
            ld = downs.get(nid)
            if ld and ld.get("ongoing") and rec["status"] != sw.DOWN:   # no Node Up event seen, but the node is up: not ongoing
                ld = dict(ld, ongoing=False, open=True)
            rec["last_down"] = ld
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
        if hasattr(swdb, "begin_read"):                  # 1.101.0: one moment of the database for the whole read
            swdb.begin_read(conn)
            snap = swdb.latest(conn, fresh=False)        # inside the read: no rollback of it (as answer() does)
        else:
            snap = swdb.latest(conn)
    except Exception as e:  # noqa: BLE001
        if "1146" in str(e) or "doesn't exist" in str(e):
            return None, "the SolarWinds snapshot is not in the report database yet (the job SolarWinds - snapshot has not run)"
        return None, "the report database's SolarWinds snapshot could not be read: %s" % _say(e)
    if not snap:
        f = None
        try:
            f = swdb.last_failure(conn)
        except Exception:  # noqa: BLE001
            pass
        return None, "no whole SolarWinds snapshot yet%s" % ((" (the last run: %s)" % _clean(str(f[1]), 160)) if f else "")
    want = sdb.orion_key(opts.get("orion")) if opts.get("orion") else ""
    if want and snap.get("orion") and snap["orion"] != want:    # another Orion's snapshot: SolarWinds itself
        return None, "the SolarWinds snapshot is of another SolarWinds (%s, not %s)" % (snap["orion"], want)
    if snap.get("age_seconds") is not None:
        age = int(snap["age_seconds"]) // 60
    else:                                                # no age given: from its time; no time at all = too old
        ra = _utc(snap.get("read_at"))
        age = int((now - ra).total_seconds() // 60) if ra else 10 ** 6
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
    keys = sorted(set([_short(d["dc"]) for d in dcs] + [str(d.get("ip") or "") for d in dcs if d.get("ip")]))
    reader = swdb.nodes_for_all if hasattr(swdb, "nodes_for_all") else swdb.nodes_for     # 1.96.1: every node of a name
    got = reader(conn, sid, keys) or {}                   # by the short name or the primary IP
    rows = []
    for v in got.values():
        for r in (v if isinstance(v, list) else [v]):     # a reader that gives every node of a name: all of them
            if r and all(r.get("node_id") != o.get("node_id") for o in rows):
                rows.append(r)
    found = match(dcs, rows) if not opts.get("by_node_id") else {
        i: (next((n for n in rows if d.get("node_id") is not None and str(n["node_id"]) == str(d["node_id"])), None), "node_id", ("not in SolarWinds" if d.get("node_id") is not None else d.get("match_why") or "not matched"))
        for i, d in enumerate(dcs)}
    ids = sorted(set(v[0]["node_id"] for v in found.values() if v[0]))
    note = {}
    try:
        note = swdb.part_note(snap, "app") or {}
    except Exception:  # noqa: BLE001 - an older 1.96.0 without part_note: SAM is there when apps_for answers
        note = {}
    sam = not note.get("absent")
    apps = {}
    if sam and ids:
        kw = {"fresh": False} if "fresh" in inspect.signature(swdb.apps_for).parameters else {}
        apps = swdb.apps_for(conn, sid, ids, **kw) or {}
    alerts = (swdb.alerts_for(conn, sid, ids) or {}) if ids else {}
    days = float(opts.get("days") or 30)
    downs, events = {}, True
    try:
        kw = {"orion": snap["orion"]} if snap.get("orion") else {}
        evs = swdb.events_between(conn, now - datetime.timedelta(days=days), now, ids, **kw) if ids else []
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
    """The first of each [choice, ...] this Orion's entity has (None: none of them) - as the snapshot asks (EntityName IN)."""
    have = sdb._have(client, [entity]).get(entity) or set()
    return [next((c for c in ch if c in have), None) for ch in want]


def from_live(dcs, opts, client, swdb=None):
    """SolarWinds itself, the DCs only. Events (the last down) are not read live."""
    now = _now(opts)
    where = ""
    if opts.get("by_node_id"):
        ids = sorted({int(d["node_id"]) for d in dcs if str(d.get("node_id") or "").isdigit()})
        where = " WHERE N.NodeID IN (%s)" % ",".join(str(n) for n in ids) if ids else " WHERE 1 = 0"
    nodes, cut = client.paged("SELECT N.NodeID, N.Caption, N.DNS, N.IPAddress, N.Status, N.StatusDescription, N.UnManaged, N.CPULoad, "
                               "N.PercentMemoryUsed, N.LastSync FROM Orion.Nodes N" + where, "N.NodeID", page=int(opts.get("page") or 1000),
                               cap=int(opts.get("max_nodes") or 20000))
    rows = [{"node_id": n.get("NodeID"), "name": n.get("Caption") or "", "dns": n.get("DNS") or "", "ip": n.get("IPAddress") or "",
             "status": sw.status_of(n.get("Status"), n.get("UnManaged")), "status_text": n.get("StatusDescription") or "",
             "last_poll": n.get("LastSync"), "cpu_pct": n.get("CPULoad"), "mem_pct": n.get("PercentMemoryUsed")} for n in nodes]
    found = match(dcs, rows) if not opts.get("by_node_id") else {
        i: (next((n for n in rows if d.get("node_id") is not None and str(n["node_id"]) == str(d["node_id"])), None), "node_id", ("not in SolarWinds" if d.get("node_id") is not None else d.get("match_why") or "not matched"))
        for i, d in enumerate(dcs)}
    why = []
    if cut:                                              # past max_nodes: a DC not found may be one not read
        why.append("SolarWinds has more nodes than were read (%d): a DC not found may be past them" % len(nodes))
        for k, v in list(found.items()):
            if v[0] is None and not v[2]:
                found[k] = (None, "", "not read: SolarWinds' node list was cut at %d" % len(nodes))
    ids = sorted(set(v[0]["node_id"] for v in found.values() if v[0]))
    meta = True
    try:
        ents = _has(client, (APPS, COMPS) + ALERTS)
    except Exception as e:  # noqa: BLE001 - the nodes stay; SAM and the alerts not read
        ents, meta = set(), False
        why.append("which SolarWinds modules there are was not read (%s): no AD monitors or alerts" % _say(e))
    sam = APPS in ents and COMPS in ents
    apps = {}
    if sam and ids:
        nr = []
        try:                                             # 1.101.0: the snapshot's own live reader (one reader in one place)
            apps = sdb.apps_live(client, ids, nr)
            sam = not any(x.get("absent") for x in nr)
        except Exception as e:  # noqa: BLE001
            why.append("SAM's applications not read: %s" % _say(e))
    alerts = {}
    if all(a in ents for a in ALERTS) and ids:
        try:
            alerts = sdb.alerts_live(client, ids, [])
        except Exception as e:  # noqa: BLE001
            why.append("the active alerts not read: %s" % _say(e))
    ifaces = None
    if ids:
        try:
            ifaces = _live_ifaces(client, ids)
        except Exception as e:  # noqa: BLE001
            why.append("the DCs' interfaces not read: %s" % _say(e))
    return {"ok": True, "source": "swis-live", "read_at": _hm(now), "age_min": 0, "interval_min": None, "stale": False, "why": why,
            "sam": sam, "sam_why": "" if sam else ("SAM's applications are not on this Orion (no %s)" % APPS if meta else "SAM's applications were not read"),
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


def read(dcs, opts, conn=None, swdb=None, client=None):
    """The snapshot when it is there and fresh enough; else SolarWinds live; else the old snapshot, said as old; else nothing."""
    dcs = [d for d in (dcs or []) if d.get("dc")]
    why, stale = [], None
    if conn is not None and swdb is not None and hasattr(swdb, "latest"):
        try:
            r, w = from_db(dcs, opts, conn, swdb)
        except Exception as e:  # noqa: BLE001
            r, w = None, "the SolarWinds snapshot could not be read: %s" % _say(e)
        finally:
            if hasattr(swdb, "end_read"):
                swdb.end_read(conn)
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
            why.append("SolarWinds could not be read: %s" % _say(e))
    if stale:                                            # the old snapshot is all there is: given, and said as old
        snap, age = stale
        try:
            if hasattr(swdb, "begin_read"):
                swdb.begin_read(conn)
            r = _db_read(dcs, opts, conn, swdb, snap, age, _now(opts))
            r["stale"], r["why"] = True, why
            return r
        except Exception as e:  # noqa: BLE001
            why.append("the old snapshot could not be read either: %s" % _say(e))
        finally:
            if hasattr(swdb, "end_read"):
                swdb.end_read(conn)
    return {"ok": False, "source": "", "read_at": "", "age_min": None, "interval_min": None, "stale": False, "why": why, "sam": False,
            "sam_why": "", "events": False, "dcs": []}


def netmap_place(nm, dcs_rows, conn=None, client=None):
    """1.101.0: where the network map puts the DCs' nodes - its own read (site_swis_db.answer: the snapshot, else SolarWinds
    itself) and its own model (site_swis.model with the netmap_* settings), so the AD map never disagrees with the network map.
    nm: the netmap_* settings (site_by, site_split, sites, site_pattern, hierarchy, roles, properties, lat_property,
    lon_property, discover, max_nodes, max_age (min), orion). -> {ok, source, why, node_site {node id: site}, names,
    places {site: [lat, lon]}, wan [{a, b, n, bps, util, status, ifs}], no_site}. Never raises; why in fixed words."""
    o = dict(nm or {})
    hier = [h.get("value") for h in o.get("hierarchy") or [] if isinstance(h, dict) and h.get("from") == "property"]
    sb = str(o.get("site_by") or "auto")
    collect = {"site_by": "" if sb.lower() in ("auto", "location") else sb, "lat_property": o.get("lat_property") or "",
               "lon_property": o.get("lon_property") or "", "properties": list(o.get("properties") or []) + hier,
               "discover": o.get("discover", True), "page": 1000, "max_nodes": int(o.get("max_nodes") or 5000)}
    model_o = {"site_by": sb, "site_split": o.get("site_split", ","), "sites": o.get("sites") or {}, "lat_property": o.get("lat_property") or "",
               "lon_property": o.get("lon_property") or "", "properties": o.get("properties") or [], "no_site": o.get("no_site") or "Not placed",
               "site_pattern": o.get("site_pattern") or "", "hierarchy": o.get("hierarchy") or [], "roles": o.get("roles") or {}}
    bad = sw.check_opts(model_o)
    if bad:
        return {"ok": False, "why": "the netmap settings cannot work here (%s)" % "; ".join(bad)[:200]}
    why = []
    try:
        age = o.get("max_age")
        raw = sdb.answer(collect, conn, client, None if age is None else int(age) * 60, why, orion=o.get("orion") or "")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": "the network map's sites were not read (%s)" % _say(e)}
    if not raw or not raw.get("nodes"):
        return {"ok": False, "why": "the network map's sites were not read (%s)" % ("no SolarWinds to read" if raw is None else "no nodes")}
    try:
        m = sw.model(raw, model_o)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": "the network map's sites could not be worked out (%s)" % e.__class__.__name__}
    site_of = dict((n["id"], n["site"]) for n in m["nodes"])
    ids = set(d.get("node_id") for d in dcs_rows or [] if d.get("node_id") is not None)
    ifs = dict((i.get("InterfaceID"), i) for i in raw.get("interfaces") or [])
    pairs = {}
    for l in m.get("links") or []:
        if not l.get("wan"):
            continue
        sa, sbb = site_of.get(l["a"]), site_of.get(l["b"])
        if not sa or not sbb or sa == sbb:
            continue
        k = tuple(sorted([sa, sbb]))
        p = pairs.setdefault(k, {"a": k[0], "b": k[1], "n": 0, "bps": 0, "util": None, "status": sw.UP, "ifs": []})
        sp = [x for x in (_pctnum(ifs.get(l.get("ka"), {}).get("Speed")), _pctnum(ifs.get(l.get("kb"), {}).get("Speed"))) if x]
        p["n"] += 1
        p["bps"] += min(sp) if sp else 0
        if l.get("in") is not None or l.get("out") is not None:
            p["util"] = max(p["util"] or 0, l.get("in") or 0, l.get("out") or 0)
        if sw.RANK.get(l.get("status"), 2) < sw.RANK.get(p["status"], 2):
            p["status"] = l["status"]
        if len(p["ifs"]) < 3:
            na, nb = (next((n["name"] for n in m["nodes"] if n["id"] == x), "") for x in (l["a"], l["b"]))
            p["ifs"].append("%s %s - %s %s" % (na, l.get("ia") or "", nb, l.get("ib") or ""))
    src = raw.get("source") or ""
    return {"ok": True, "source": src, "age_min": int(raw.get("age_seconds") or 0) // 60,
            "why": "" if src == "db-snapshot" else ("read live from SolarWinds" + ("" if not why else " (the snapshot not used)")),
            "node_site": dict((str(i), site_of.get(i)) for i in ids if site_of.get(i)), "names": [x["name"] for x in m["sites"]],
            "places": dict((x["name"], [x["lat"], x["lon"]]) for x in m["sites"] if x.get("lat") is not None),
            "wan": list(pairs.values()), "no_site": m.get("no_site") or "Not placed"}


def _pctnum(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None
