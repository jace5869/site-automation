# -*- coding: utf-8 -*-
"""1.97.0: the hosts of a health check, a troubleshoot run or a VMware alarms report as SolarWinds sees them. Read only.

Everything from the report database's SolarWinds snapshot (1.96.0, site_swis_db): one read-only transaction, one
snapshot, never SolarWinds itself per page or per host. A snapshot older than max_age minutes, of another Orion, or
none: ok is False and why says why (the report shows one muted line). The one live read is Log Analyzer's syslog
(syslog_live): one capped SWQL query a run, for the matched nodes only, after a check of its entity and columns.

read(hosts, opts, conn, swdb) -> {
  ok, read_at (UTC "YYYY-MM-DD HH:MM"), age_min, orion, why: [...], sam (bool), volumes (bool), events (bool),
  hosts: [{host, short, fqdn, ip, matched ("name" | "ip" | "other ip" | "vim" | ""), unmatched_why, node: {node_id, name, status, status_code,
           status_text, ip, last_poll, cpu, mem, vendor, model, os_version} | None,
           alerts: [...], apps: [...], volumes: [...], ports: [...], events: [...]}]}
hosts: [{host, fqdn, ip}]; opts: max_age (min), mode (health | troubleshoot | vcenter), event_hours, now (tests),
orion (the SolarWinds address the job has, when it has one: another Orion's snapshot is not used)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

from collections import defaultdict
import datetime
import re

try:
    from ansible.module_utils import site_swis as sw
    from ansible.module_utils import site_swis_db as sdb
    from ansible.module_utils import site_ad_swis as ad
except ImportError:                                      # the tests: plugins/module_utils on sys.path
    import site_swis as sw
    import site_swis_db as sdb
    import site_ad_swis as ad

OLM = "Orion.OLM.LogEntry"
# the columns the syslog query needs, each the first of its choices this Orion has (Metadata.Property)
OLM_COLS = (("node_id", ("NodeID",)), ("time", ("DateTimeUtc", "DateTime")), ("level", ("Level",)), ("message", ("Message",)))
CHUNK = 500                                              # node ids a query (as the snapshot's own readers)
LEVELS = ("Emergency", "Alert", "Critical", "Error")


def _now(opts):
    return ad._now(opts)


canon = sdb.canon


def _short(name):
    n = canon(name)
    return n if _ip(n) else n.split(".", 1)[0]


def _ip(x):
    return sdb.normalized_ip(x)


def _full(x):
    """A name as written, lower case, when it has a domain (not an IP), else ''."""
    x = canon(x)
    return x if "." in x and not _ip(x) else ""


def _hosts(hosts):
    """[{host, fqdn, ip, kind}] -> each with its full name (the inventory name, else ansible_host when it is a name: review
    P1-1), its short name and its IP address."""
    out = []
    for h in hosts or []:
        name = str(h.get("host") or "").strip()
        if not name:
            continue
        given = str(h.get("ip") or "").strip()
        fq = _full(name) or _full(h.get("fqdn")) or _full(given)
        ip = _ip(given) or _ip(name)
        out.append({"host": name, "short": _short(fq or name), "fqdn": fq, "ip": ip, "kind": str(h.get("kind") or ""),
                    "vcenter": h.get("vcenter") or ""})
    return out


def match(hs, nodes, allow_short=True, allow_ip=True):
    """Resolve full names, then addresses, then compatible short names across the whole job."""
    rows = {n["node_id"]: n for n in nodes or []}
    full, short, address = defaultdict(set), defaultdict(set), defaultdict(set)
    domains, ips = {}, {}
    for nid, node in rows.items():
        names = [canon(node.get(key)) for key in ("name", "dns")]
        names = [n for n in names if n and not _ip(n)]
        domains[nid] = {n for n in names if "." in n}
        for n in domains[nid]:
            full[n].add(nid)
        for n in names:
            short[n.split(".", 1)[0]].add(nid)
        addresses = [node.get("ip")] + list(node.get("other_ips") or [])
        ips[nid] = set(filter(None, map(_ip, addresses)))
        for ip in ips[nid]:
            address[ip].add(nid)

    found, claimed = {}, {}
    reasons = {}
    # an address counts only when one host of the job and one node have it: a VIP, a cluster address or a re-used DHCP
    # address otherwise names the wrong box (the director's rule) - shared: the short name decides
    host_ips = defaultdict(int)
    for h in hs:
        if _ip(h["ip"]):
            host_ips[_ip(h["ip"])] += 1
    for stage in ("full", "ip", "short"):
        if (stage == "short" and not allow_short) or (stage == "ip" and not allow_ip):
            continue
        candidates, owners = {}, defaultdict(list)
        for i, h in enumerate(hs):
            if i in found:
                continue
            fq, sn, ip = _full(h["fqdn"]), _short(h["short"]), _ip(h["ip"])
            if stage == "full":
                ids = full[fq]
            elif stage == "ip":
                ids = set()
                if ip and (len(address[ip]) > 1 or host_ips[ip] > 1):
                    reasons[i] = "its IP %s is shared (%d %s of this job, %d SolarWinds %s): not used to match" % (
                        ip, host_ips[ip], "host" if host_ips[ip] == 1 else "hosts", len(address[ip]),
                        "node" if len(address[ip]) == 1 else "nodes")
                elif ip:
                    ids = set(address[ip])
            else:
                ids = set()
                pool = short[sn]
                if full[fq]:
                    pool = pool & full[fq]
                for nid in sorted(pool):
                    if nid in claimed:
                        reasons[i] = "the same SolarWinds node as %s" % claimed[nid]
                    elif domains[nid] and fq not in domains[nid]:
                        reasons[i] = "a SolarWinds node named %s is %s" % (sn, min(domains[nid]))
                    elif ip and ips[nid] and ip not in ips[nid]:
                        reasons[i] = "the SolarWinds node named %s has another IP (%s)" % (sn, _ip(rows[nid].get("ip")) or min(ips[nid]))
                    else:
                        ids.add(nid)
            candidates[i] = ids
            for nid in ids:
                owners[nid].append(i)
        for i, ids in candidates.items():
            h = hs[i]
            shared = next((nid for nid in sorted(ids) if len(owners[nid]) > 1), None)
            if shared is not None:
                reasons[i] = "%d hosts of this job could be the SolarWinds node %s" % (len(owners[shared]), rows[shared]["name"])
            elif len(ids) > 1:
                if stage == "ip":
                    reasons[i] = "%d SolarWinds nodes have its IP %s (a cluster address?)" % (len(ids), h["ip"])
                else:
                    name = h["fqdn"] if stage == "full" else h["short"]
                    reasons[i] = "%d SolarWinds nodes are named %s" % (len(ids), name)
            elif ids:
                nid = next(iter(ids))
                if nid in claimed:
                    reasons[i] = "the same SolarWinds node as %s" % claimed[nid]
                    continue
                how = "name"
                if stage == "ip":
                    how = "ip" if _ip(rows[nid].get("ip")) == _ip(h["ip"]) else "other ip"
                found[i] = (rows[nid], how, "")
                claimed[nid] = h["host"]
    return {i: found.get(i, (None, "", reasons.get(i, ""))) for i in range(len(hs))}


def _vcenter_matches(hs, rows, vim, linked, allow_short, allow_ip):
    by_id = {n["node_id"]: n for n in rows + linked}
    kinds = defaultdict(set)
    for n in linked:
        kinds[n["node_id"]].add(n["kind"])
    owners, names = defaultdict(list), defaultdict(list)
    for i, h in enumerate(hs):
        names[h["host"].lower().rstrip(".")].append(i)
        obj = vim.get("%s|%s" % (h["kind"], h["host"])) or {}
        if obj.get("node_id") is not None:
            owners[obj["node_id"]].append(i)
            kinds[obj["node_id"]].add(h["kind"])
    found = {}
    for nid, indices in owners.items():
        for i in indices:
            if nid in by_id and len(indices) == 1 and kinds[nid] == {hs[i]["kind"]}:
                found[i] = (by_id[nid], "vim", "")
            else:
                found[i] = (None, "", "the VIM node could not be identified")
    for indices in names.values():
        if len(indices) > 1:
            for i in indices:
                found.setdefault(i, (None, "", "several vCenter objects have this name"))
    pending = [i for i in range(len(hs)) if i not in found]
    fallback = match([hs[i] for i in pending], [n for n in rows if n["node_id"] not in kinds], allow_short, allow_ip)
    found.update((i, fallback[j]) for j, i in enumerate(pending))
    return found


def _age_text(seconds):
    m = int(seconds // 60)
    if m < 1:
        return "under a minute"
    if m < 120:
        return "%d min" % m
    return "%d h %d min" % (m // 60, m % 60) if m % 60 else "%d h" % (m // 60)


def snapshot(conn, swdb, opts):
    """(snap, age_seconds, why): the newest whole snapshot when it can be used, else (None, None, why). 1.97.0 (review
    P1-5): its age in seconds against solarwinds_db_max_age in seconds (20 min 59 s is past 20 min); no age given:
    from its time; no time either: never used."""
    try:
        snap = swdb.latest(conn, fresh=False)
    except Exception as e:  # noqa: BLE001
        if "1146" in str(e) or "doesn't exist" in str(e):
            return None, None, "the report database has no SolarWinds snapshot yet (the job SolarWinds - snapshot has not run)"
        return None, None, "the report database's SolarWinds snapshot could not be read: %s" % ad._say(e)
    if not snap:
        f = None
        try:
            f = swdb.last_failure(conn)
        except Exception:  # noqa: BLE001
            pass
        return None, None, "the report database has no whole SolarWinds snapshot yet%s" % (
            (" (the last run: %s)" % ad._clean(str(f[1]), 160)) if f else "")
    want = sdb.orion_key(opts.get("orion")) if opts.get("orion") else ""
    if want and snap.get("orion") and snap["orion"] != want:
        return None, None, "the SolarWinds snapshot is of another SolarWinds (%s, not %s)" % (snap["orion"], want)
    limit = int(opts.get("max_age") if opts.get("max_age") is not None else 20)
    if limit <= 0:
        return None, None, "solarwinds_db_max_age is 0: the snapshot is never used"
    age = snap.get("age_seconds")
    if age is None:
        ra = ad._utc(snap.get("read_at"))
        if ra is None:
            return None, None, "the SolarWinds snapshot has no time: it is never used"
        age = (_now(opts) - ra).total_seconds()
    age = max(0, int(age))
    if age > limit * 60:
        why = "the SolarWinds snapshot is %s old (of %s UTC), older than solarwinds_db_max_age (%d min)" % (
            _age_text(age), ad._hm(ad._utc(snap.get("read_at"))) or "?", limit)
        try:
            f = swdb.last_failure(conn)
            if f:
                why += ". The newer runs failed: %s" % ad._clean(str(f[1]), 160)
        except Exception:  # noqa: BLE001
            pass
        return None, None, why
    return snap, age, ""


def read(hosts, opts, conn, swdb):
    """The hosts as the snapshot has them. Never raises: what could not be read is in why."""
    o = dict(opts or {})
    hs = _hosts(hosts)
    res = {"ok": False, "read_at": "", "age_min": None, "age_text": "", "orion": "", "why": [], "not_read": [], "sam": False,
           "volumes": False, "events": False, "hosts": []}
    if conn is None or swdb is None:
        return res
    try:
        swdb.begin_read(conn)                            # one moment of the database for the whole read
        snap, age, why = snapshot(conn, swdb, o)
        if snap is None:
            res["why"].append(why)
            return res
        res.update(_read(hs, o, conn, swdb, snap), ok=True, age_min=age // 60, age_text=_age_text(age),
                   read_at=ad._hm(ad._utc(snap.get("read_at"))), orion=snap.get("orion") or "")
        return res
    except Exception as e:  # noqa: BLE001
        res["why"].append("the SolarWinds snapshot could not be read: %s" % ad._say(e))
        return res
    finally:
        swdb.end_read(conn)


def _part(swdb, snap, part, words):
    """(read, line): the part was read; a part this Orion lacks: False and no line; one that failed: None and a line."""
    note = swdb.part_note(snap, part) or {}
    if not note:
        return True, ""
    if note.get("absent"):
        return False, ""
    return None, "%s: not read by the last snapshot (%s)" % (words, ad._clean(str(note.get("why") or "?"), 160))


def _read(hs, o, conn, swdb, snap):
    sid = snap["snap_id"]
    mode = o.get("mode") or "health"
    vim = {}
    if mode == "vcenter":
        vim = swdb.vim_for(conn, sid, [{"name": h["host"], "kind": h["kind"]} for h in hs]) or {}
    fulls, shorts, ips = (sorted({h[key] for h in hs if h[key]}) for key in ("fqdn", "short", "ip"))
    not_read = []
    core = snap.get("core") or {}
    allow_short = not core.get("cut")
    allow_ip = _part(swdb, snap, "ip", "the other addresses")[0] is not None
    if not allow_short:
        not_read.append("short name matching is off. The snapshot reached solarwinds_snapshot_max_nodes")
    if not allow_ip:
        not_read.append("IP matching is off. The other node addresses were not read")
    rows = swdb.nodes_for_hosts(conn, sid, fulls, shorts, ips)
    if mode == "vcenter":
        ids = {n["node_id"] for n in rows}
        ids.update(v["node_id"] for v in vim.values() if v and v.get("node_id") is not None)
        found = _vcenter_matches(hs, rows, vim, swdb.vim_nodes(conn, sid, ids), allow_short, allow_ip)
    else:
        found = match(hs, rows, allow_short, allow_ip)
    ids = sorted(set(v[0]["node_id"] for v in found.values() if v[0]))
    sam, line = _part(swdb, snap, "app", "SAM's applications")
    not_read += [line] if line else []
    vols_on, line = _part(swdb, snap, "volume", "the volumes")
    not_read += [line] if line else []
    apps = (swdb.apps_for(conn, sid, ids, own_tx=False) or {}) if sam and ids else {}
    alerts_read = snap.get("alerts") is not None and _part(swdb, snap, "alert", "the active alerts")[0] is not None
    if not alerts_read:
        not_read.append("the active alerts: not read by the last snapshot")
    poll_read, line = _part(swdb, snap, "node", "the last poll")
    if line:
        not_read.append(line)
    alerts = (swdb.alerts_for(conn, sid, ids) or {}) if alerts_read and ids else {}
    vols = (swdb.volumes_for(conn, sid, ids) or {}) if vols_on and ids else {}
    more = (swdb.node_details(conn, sid, ids) or {}) if ids else {}
    detail = mode in ("troubleshoot", "vcenter")
    ports_read = core.get("topology_ok") is not False
    events_read = snap.get("events") is not None and _part(swdb, snap, "event", "the events")[0] is not None
    if detail and not ports_read:                     # only where the report shows them (troubleshoot, the alarms)
        not_read.append("the topology: not read by the last snapshot")
    if detail and not events_read:
        not_read.append("the events: not read by the last snapshot")
    ports = (swdb.ports_for(conn, sid, ids) or {}) if ids and detail and ports_read else {}
    events, ev_ok = {}, False
    if ids and detail and events_read:
        now = _now(o)
        hours = float(o.get("event_hours") or 24)
        port_of = {}
        for nid, v in ports.items():
            for p in v:
                port_of.setdefault((p["switch_id"], p["interface_id"]), []).append((nid, p.get("switch") or ""))
        try:
            evs = swdb.events_for(conn, now - datetime.timedelta(hours=hours), now, ids, sorted(port_of),
                                  limit=int(o.get("event_limit") or 2000), **({"orion": snap["orion"]} if snap.get("orion") else {}))
            ev_ok = True
        except Exception:  # noqa: BLE001 - the events are a nicety: said as not read
            evs = []
            not_read.append("SolarWinds' events: not read")
        for e in evs:
            n = e.get("node_id")
            n = int(n) if n is not None else None
            if e.get("port"):                            # a switch's event of the port a host is linked to (review P2-4)
                for nid, swname in port_of.get(tuple(e["port"]), ()):
                    events.setdefault(nid, []).append(dict(e, on="port", switch=swname))
            elif n in ids:
                events.setdefault(n, []).append(dict(e, on="node"))
        for v in events.values():
            v.sort(key=lambda e: (ad._utc(e.get("event_time")) or datetime.datetime(1, 1, 1), e.get("event_id") or 0), reverse=True)
    if any(not p.get("status") or p.get("errors") is None for group in ports.values() for p in group):
        not_read.append("some switch port status or error readings were not read")
    out = []
    for i, h in enumerate(hs):
        node, how, why = found.get(i, (None, "", ""))
        rec = dict(h, matched=how, unmatched_why=why, node=None, alerts=[], apps=[], volumes=[], ports=[], events=[])
        if node:
            nid = int(node["node_id"])
            m = more.get(nid) or {}
            crit = str(node.get("status_code")) == "14"
            rec["node"] = {"node_id": nid, "name": node.get("name") or "", "status": "Critical" if crit else (node.get("status") or sw.UNKNOWN),
                           "status_code": node.get("status_code"), "status_text": node.get("status_text") or "", "ip": node.get("ip") or "",
                           "last_poll": ad._hm(ad._utc(node.get("last_poll"))), "cpu": ad._pct(node.get("cpu_pct")),
                           "mem": ad._pct(node.get("mem_pct")), "vendor": m.get("vendor") or "", "model": m.get("model") or "",
                           "os_version": m.get("os_version") or ""}
            rec.update(alerts=[_alert(a) for a in alerts.get(nid) or []], apps=list(apps.get(nid) or []),
                       volumes=list(vols.get(nid) or []), ports=list(ports.get(nid) or []),
                       events=[_event(e) for e in events.get(nid) or []])
        out.append(rec)
    res = {"alerts_read": alerts_read, "ports_read": ports_read, "poll_read": poll_read is True,
           "sam": sam, "volumes": vols_on, "events": ev_ok, "hosts": out, "not_read": not_read}
    if mode == "vcenter":
        res["vim"] = vim
    return res


def redact_read(res, redact_fn):
    """Redact SolarWinds message text before the module returns it."""
    for group in ((res.get("syslog") or {}).get("by_node") or {}).values():
        for entry in group.get("top") or []:
            entry["message"] = redact_fn(entry["message"])
    for host in res.get("hosts") or []:
        for alert in host.get("alerts") or []:
            alert["name"] = redact_fn(alert["name"])
            alert["message"] = redact_fn(alert["message"])
        for event in host.get("events") or []:
            event["message"] = redact_fn(event["message"])
    return res


def _alert(a):
    return {"name": a.get("name") or "", "severity": a.get("severity"), "since": ad._hm(ad._utc(a.get("triggered_at"))),
            "message": a.get("message") or "", "on": a.get("entity_caption") or "", "entity_type": a.get("entity_type") or "",
            "acknowledged": bool(a.get("acknowledged"))}


def _event(e):
    return {"time": ad._hm(ad._utc(e.get("event_time"))), "kind": e.get("type_name") or "", "event_type": e.get("event_type"),
            "message": e.get("message") or "", "on": e.get("on") or "node", "switch": e.get("switch") or "", "event_id": e.get("event_id")}


# ---------------------------------------------------------------------------------------- Log Analyzer (live, one query)
def norm(message):
    """A syslog message's key: numbers, hex and addresses made alike, so 'child pid 4121 exit' and 'child pid 977 exit'
    count as one."""
    t = re.sub(r"\s+", " ", str(message or "")).strip()
    t = re.sub(r"\b0x[0-9a-fA-F]+\b", "#", t)
    t = re.sub(r"\b[0-9a-fA-F]{8,}\b", "#", t)
    t = re.sub(r"\d+", "#", t)
    return t.lower()[:300]


def syslog_live(client, node_ids, opts=None):
    """Log Analyzer's entries of these nodes at the levels asked, in the last hours: one query (one per 500 nodes), capped
    at max_rows in all.
    -> {ok, why, capped, max_rows, hours, levels, by_node: {node_id: {count, top: [{message, count, last}]}}}.
    An Orion without Log Analyzer (no Orion.OLM.LogEntry), or one without a column it needs: ok False, why says so."""
    o = dict(opts or {})
    ids = sorted(set(int(i) for i in node_ids or [] if i is not None))
    hours = max(1, int(o.get("hours") or 24))
    cap = max(1, int(o.get("max_rows") or 5000))
    levels = [str(x).strip().capitalize() for x in (o.get("levels") or LEVELS) if str(x).strip()]
    res = {"ok": False, "why": "", "capped": False, "max_rows": cap, "hours": hours, "levels": levels, "by_node": {}, "queried": []}
    if not ids:
        res.update(ok=True)
        return res
    try:
        ents = ad._has(client, [OLM])
        if OLM not in ents:
            res["why"] = "this SolarWinds has no Log Analyzer (no %s)" % OLM
            return res
        have = sdb._have(client, [OLM]).get(OLM) or set()
        cols = {}
        for key, choices in OLM_COLS:
            c = next((x for x in choices if x in have), None)
            if c is None:
                res["why"] = "Log Analyzer's %s has no %s column on this SolarWinds" % (OLM, " or ".join(choices))
                return res
            cols[key] = c
        since = _now(o) - datetime.timedelta(hours=hours)
        rows = []
        for i in range(0, len(ids), CHUNK):
            left = cap - len(rows)
            if left <= 0:
                break
            q = ("SELECT TOP %d %s, %s, %s, %s FROM %s WHERE %s IN @ids AND %s >= @since AND %s IN @levels ORDER BY %s DESC"
                 % (left, cols["node_id"], cols["time"], cols["level"], cols["message"], OLM, cols["node_id"], cols["time"], cols["level"],
                    cols["time"]))
            got = client.query(q, {"ids": ids[i:i + CHUNK], "since": since.strftime("%Y-%m-%dT%H:%M:%S"), "levels": levels})
            rows.extend(got)
            res["queried"].extend(ids[i:i + CHUNK])     # review P2-2: a node never asked is "?", never 0
            if len(got) >= left:
                break
    except Exception as e:  # noqa: BLE001
        res["why"] = "Log Analyzer could not be read: %s" % ad._say(e)
        return res
    res["capped"] = len(rows) >= cap
    by = {}
    for r in rows:
        nid = sdb._int(r.get(cols["node_id"]))
        if nid is None:
            continue
        msg = re.sub(r"\s+", " ", str(r.get(cols["message"]) or "")).strip()
        t = ad._utc(r.get(cols["time"]))
        n = by.setdefault(nid, {"count": 0, "groups": {}})
        n["count"] += 1
        g = n["groups"].setdefault(norm(msg), {"message": msg[:300], "count": 0, "last": None})
        g["count"] += 1
        if t and (g["last"] is None or t > g["last"]):
            g["last"], g["message"] = t, msg[:300]
    for nid, n in by.items():
        top = sorted(n["groups"].values(), key=lambda g: (g["count"], g["last"] or datetime.datetime(1, 1, 1)), reverse=True)
        res["by_node"][nid] = {"count": n["count"], "top": [{"message": g["message"], "count": g["count"], "last": ad._hm(g["last"])}
                                                            for g in top[:3]]}
    res["ok"] = True
    return res
