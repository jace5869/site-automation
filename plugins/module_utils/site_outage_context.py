# -*- coding: utf-8 -*-
"""1.116.1: what else saw the outage. One read-only report database transaction, then one optional Log Analyzer read."""
from collections import Counter, defaultdict
import datetime
import ipaddress
import json
import re

EVENTS, ALERTS, MAP, AD, SYSLOG, TRAPS, APPS = SOURCES = (
    "SolarWinds events", "SolarWinds alerts", "Network map", "Active Directory", "Syslog", "SNMP traps", "SolarWinds applications")
TITLE = "What else saw it (same time, not cause)"
DB_ERROR = "the report database could not be read"
EMPTY = "no data for that window"
MISSING = "no data yet"
UNKNOWN = "storage path not known from SolarWinds"
ACTIVE = "Only alerts still active at the snapshot are kept."
LAST = "Only the last failure per replication link in each AD report is kept."
NODE_LIMIT = 500  # one chunk in site_host_swis.syslog_live
ROW_LIMIT = 40
LOG_LIMIT = 100


def card(source, title, status="info", subject="", detail=""):
    if source == ALERTS:
        detail = (detail + " " + ACTIVE).strip()
    return dict(chip=source, title=title, status=status, meta="same time, not cause", subject=subject, next=detail)


def unavailable(reason):
    return dict(cards=[card(s, "not read: " + reason) for s in SOURCES], issues=[], judged="SolarWinds topology: not read: " + reason)


class NoData(Exception):
    """Only fixed, reportable reasons for a single source."""


def select(cur, sql, args=(), limit=ROW_LIMIT):
    try:
        cur.execute(sql + " LIMIT %d" % limit, args)
        return cur.fetchall()
    except Exception as e:
        # site_portal.missing_table: 1932 (a table missing in the engine) is a database fault.
        code = (getattr(e, "args", None) or [None])[0]
        missing = code == 1146 if isinstance(code, int) else (
            "no such table" in str(e).lower() or "(mysql error 1146)" in str(e).lower())
        if missing:
            raise NoData(MISSING)
        raise NoData("not read: " + DB_ERROR) from e


def marks(values):
    return ", ".join(["%s"] * len(values))


def short(name):
    try:
        return str(ipaddress.ip_address(name))
    except ValueError:
        return name.split(".", 1)[0]


def topology(cur, sid, hosts, patterns):
    """Follow edges in either direction; a short host name must identify a single VIM node."""
    def bounded(sql, args):
        rows = select(cur, sql, args, NODE_LIMIT + 1)
        if len(rows) > NODE_LIMIT:
            raise NoData("not read: storage topology exceeds the read limit")
        return rows

    def edges(ids):
        if not ids:
            return []
        ids = sorted(ids)
        m = marks(ids)
        return bounded("SELECT DISTINCT src_node, dst_node FROM sw_link WHERE snap_id = %s AND src_node <> dst_node "
                       "AND (src_node IN (" + m + ") OR dst_node IN (" + m + "))", [sid] + ids + ids)

    def site_of(location, props):
        # SolarWinds' Site custom property or the site part of Location.
        try:
            values = json.loads(props or "{}")
        except ValueError:
            return ""
        if not isinstance(values, dict):
            return ""
        site = values.get("Site") or values.get("site") or (location or "").split(",")[0]
        return str(site).strip().casefold()

    if len(hosts) > NODE_LIMIT:
        raise NoData("not read: storage topology exceeds the read limit")
    nodes, neighbours, linked = {}, defaultdict(set), set()
    candidates, owners = defaultdict(set), defaultdict(set)
    judged = "SolarWinds topology: %d of %d hosts linked to storage (sw_link)"
    try:
        keys = sorted({short(name) for name, _ in hosts.values()})
        rows = bounded("SELECT name, name_key, node_id FROM sw_vim_host WHERE snap_id = %s "
                       "AND node_id IS NOT NULL AND name_key IN (" + marks(keys) + ")", [sid] + keys) if keys else []
        by_name, by_key = defaultdict(set), defaultdict(set)
        for vim, key, nid in rows:
            by_name[(vim or "").lower().rstrip(".")].add(nid)
            by_key[key].add(nid)
        for host, (name, _) in hosts.items():
            key = short(name)
            if name != key and by_name[name]:
                candidates[host] = by_name[name]
            elif name == key or by_name[key]:
                candidates[host] = by_key[key]
            for nid in candidates[host]:
                owners[nid].add(host)
        roots = {nid: host for host, ids in candidates.items() if len(ids) == 1 for nid in ids if len(owners[nid]) == 1}
        first = edges(roots)
        near = {n for edge in first for n in edge} - roots.keys()
        second = edges(near)
        ids = sorted({n for edge in first + second for n in edge})
        if ids:
            params = [sid] + ids
            rows = bounded("SELECT node_id, name, location, props FROM sw_node WHERE snap_id = %s "
                           "AND node_id IN (" + marks(ids) + ")", params)
            names = {nid: name for nid, name, _, _ in rows}
            sites = {nid: site_of(location, props) for nid, _, location, props in rows}
            vim_ids = {row[0] for row in bounded("SELECT DISTINCT node_id FROM sw_vim_host WHERE snap_id = %s "
                                                 "AND node_id IN (" + marks(ids) + ")", params)}
            adjacent = defaultdict(set)
            for a, b in first + second:
                adjacent[a].add(b)
                adjacent[b].add(a)

            for root, host in roots.items():
                if root not in names:
                    continue
                site = hosts[host][1] or sites[root]
                if not site or sites[root] and sites[root] != site:
                    continue
                for switch in adjacent[root] - vim_ids:
                    if sites.get(switch) != site:
                        continue
                    linked.add(host)
                    peers = {n for n in adjacent[switch] - vim_ids if sites.get(n) == site} | {root}
                    for peer in peers:
                        neighbours[switch].add(peer)
                        neighbours[peer].add(switch)
            nodes = {nid: names[nid] for nid in sorted(neighbours)}
    except NoData as e:
        if str(e) != MISSING:
            raise
        return {}, "SolarWinds topology: no data yet (sw_link)", MISSING, {}
    note = judged % (len(linked), len(hosts))
    if any(len(ids) > 1 for ids in candidates.values()) or any(len(matches) > 1 for matches in owners.values()):
        note += "; ambiguous host matches left unresolved"
    path = "sw_link, two hops" if nodes else "not read: " + UNKNOWN
    if patterns and not nodes:
        path += " (configured storage nodes require a verified link)"
    return nodes, note, path, neighbours


def issue(kind, group, name, state, what, since, details):
    return dict(kind=kind, group=group, name=name, state=state, what=what, since=since, n=len(details),
                items=[dict(name=label, state=state, what=line) for label, line in details])


def summary(lines, capped=False):
    return " · ".join(lines[:5] + (["more rows in the window"] if capped or len(lines) > 5 else []))


def solar_events(cur, sid, orion, hosts, patterns, topologies, window, stamp, clean):
    like = orion.replace("!", "!!").replace("_", "!_").replace("%", "!%") + "#%"
    where = " FROM sw_event WHERE (orion = %s OR orion LIKE %s ESCAPE '!') AND event_time BETWEEN %s AND %s"
    args = [orion, like] + list(window)
    # sw_event.snap_id is its collecting snapshot, including across an EventID reset.
    try:
        retained = select(cur, "SELECT snap_id FROM sw_snapshot WHERE ok = 1 AND orion = %s AND snap_id <> %s "
                          "AND snap_id IN (SELECT snap_id" + where + ") AND EXISTS "
                          "(SELECT 1 FROM sw_link l WHERE l.snap_id = sw_snapshot.snap_id)", [orion, sid] + args, NODE_LIMIT + 1)
    except NoData as e:
        if str(e) != MISSING:
            raise
        retained = []  # Without links, the event count can still report the rows elsewhere.
    if len(retained) > NODE_LIMIT:
        raise NoData("not read: storage topology exceeds the read limit")
    for (snap,) in retained:
        nodes, _, _, neighbours = topology(cur, snap, hosts, patterns)
        topologies[snap] = ({nid: clean(name) for nid, name in nodes.items()}, neighbours)
    verified, params = [], []
    for snap, (nodes, _) in topologies.items():
        if nodes:
            verified.append("(snap_id = %s AND node_id IN (" + marks(nodes) + "))")
            params.extend([snap, *nodes])
    inside = "(" + " OR ".join(verified) + ")" if verified else "1 = 0"
    total, mine = select(cur, "SELECT COUNT(*), SUM(CASE WHEN " + inside + " THEN 1 ELSE 0 END)" + where, params + args, 1)[0]
    mine = int(mine or 0)
    elsewhere = "%d event%s elsewhere in the window left out." % (total - mine, "" if total - mine == 1 else "s")
    rows = select(cur, "SELECT node_id, net_object_type, net_object_id, event_time, type_name, message, snap_id, orion" + where +
                  " AND " + inside + " ORDER BY event_time, orion, event_id", args + params)
    lines, groups, types = [], defaultdict(list), Counter()
    latest = {}
    for nid, typ, oid, at, name, msg, snap, epoch in rows:
        node = topologies[snap][0][nid]
        name, msg = clean(name or "Event"), clean(msg or node)[:300]
        types[name] += 1
        line = "%s · %s · %s" % (stamp(at), name, msg)
        lines.append(line)
        if name.lower() in ("node down", "node up", "interface down", "interface up"):
            # Some Orions give only the interface caption in the transition message. Match it verbatim.
            key = (nid, typ, oid if typ != "N" else nid)
            if typ == "I" and oid is None:
                caption = re.fullmatch(r"(.+?) is " + name.split()[-1], msg, re.I)
                key += (caption.group(1) if caption else len(lines),)
            identity = (epoch, node, key)
            groups[identity].append((name.lower(), stamp(at), line))
            latest[identity] = max(latest.get(identity, snap), snap)
    title = EMPTY
    if rows:
        title = "%d events on the storage path" % mine
        if mine == len(rows):
            title += ", %s to %s: %s" % (stamp(rows[0][3]), stamp(rows[-1][3]), ", ".join("%d %s" % (v, k) for k, v in types.items()))
    c = card(EVENTS, title,
             "critical" if any("down" in (r[4] or "").lower() for r in rows) else "info",
             elsewhere, summary(lines, mine > len(rows)))
    by_snapshot = defaultdict(dict)
    for identity, changes in groups.items():
        by_snapshot[latest[identity]][identity[2]] = changes
    return c, by_snapshot, mine > len(rows)


def solar_alerts(cur, sid, nodes, window, stamp, clean):
    ids = sorted(nodes)
    rows = select(cur, "SELECT node_id, name, triggered_at, acknowledged, message, severity, entity_caption FROM sw_alert_active WHERE snap_id = %s "
                  "AND triggered_at BETWEEN %s AND %s AND node_id IN (" + marks(ids) + ") ORDER BY triggered_at, alert_active_id",
                  [sid] + list(window) + ids, ROW_LIMIT + 1)
    lines, issues = [], []
    critical = False
    for nid, name, at, ack, msg, severity, entity in rows[:ROW_LIMIT]:
        name, entity = clean(name or "Alert"), clean(entity or nodes[nid])
        line = "%s on %s, raised %s, %s" % (name, entity, stamp(at),
                "acknowledged" if ack else "acknowledgement unknown" if ack is None else "not acknowledged")
        critical = critical or severity == 2
        lines.append(line)
        issues.append((nid, issue("net", "SolarWinds (storage path)", entity, "At risk", name + " still active",
                                  stamp(at), [("Active alert", line + ". " + clean(msg or "")[:300])])))
    return card(ALERTS, summary(lines, len(rows) > ROW_LIMIT) or EMPTY,
                "critical" if critical else "warning" if rows else "info"), issues


def network_issues(cur, sid, nodes, events, alerts, capped, neighbours, clean):
    def path_item(nid):
        ends = [nodes[n] for n in sorted(neighbours.get(nid, ()))]
        if not ends:
            return []
        text = "%s linked to %s (sw_link)" % (nodes[nid], ", ".join(ends[:8]))
        if len(ends) > 8:
            text += " and %d more" % (len(ends) - 8)
        return [dict(name="Storage path", state="Topology", what=text)]

    ports = {}
    ids = sorted({key[2] for key in events if key[1] == "I" and key[2] is not None})
    if ids:
        try:
            ports = {(nid, oid): (caption, status, errors) for nid, oid, caption, status, errors in select(cur,
                     "SELECT node_id, interface_id, caption, oper_status, in_errors_today FROM sw_interface WHERE snap_id = %s "
                     "AND interface_id IN (" + marks(ids) + ")", [sid] + ids, len(ids))}
        except NoData:
            pass  # The event still identifies the port when there is no interface snapshot.
    issues = [i for _, i in alerts]
    by_node = defaultdict(list)
    for nid, i in alerts:
        i["items"].extend(path_item(nid))
        i["n"] = len(i["items"])
        by_node[nid].append(i)
    for key, changes in events.items():
        nid, typ, oid = key[:3]
        down = next((c for c in changes if c[0].endswith(" down")), None)
        if not down:
            continue
        back = changes[-1][0].endswith(" up") and not capped
        state = "Recovered" if back else "Not judged"
        details = [dict(name=c[0], state=state, what=c[2]) for c in changes]
        if typ == "N" and nid in by_node:
            for i in by_node[nid]:
                i["items"].extend(details)
                i["n"] = len(i["items"])
            continue
        name = nodes[nid] + (" · interface %s" % (oid if oid is not None else "(ID unknown)") if typ == "I" else "")
        if len(key) == 4 and isinstance(key[3], str):
            name = key[3]
        if typ == "I" and (nid, oid) in ports:
            caption, oper, errors = ports[nid, oid]
            name = nodes[nid] + " · " + (clean(caption) if caption else "interface %s" % oid)
            if oper == 2:
                state = "Down now"
            details.append(dict(name="Interface at the snapshot", state=state,
                                what="%s%s" % ({1: "Up", 2: "Down"}.get(oper, "State unknown"),
                                               ", %d input errors today" % errors if errors is not None else "")))
        i = issue("net", "SolarWinds (storage path)", name, state, down[0] + ("; back " + changes[-1][1] if back else "; recovery not established"),
                  down[1], [])
        details.extend(path_item(nid))
        i.update(items=details, n=len(details))
        issues.append(i)
    return issues


def network_map(cur, window, stamp, clean):
    reads = select(cur, "SELECT COUNT(*) FROM netmap_read WHERE ok = 1 AND read_at BETWEEN %s AND %s", window, 1)[0][0]
    good = " JOIN netmap_read r ON r.read_at = x.read_at WHERE r.ok = 1 AND x.read_at BETWEEN %s AND %s"
    links = select(cur, "SELECT site_a, site_b, MIN(CASE WHEN LOWER(status) = 'down' THEN x.read_at END), "
                   "MAX(CASE WHEN LOWER(status) = 'down' THEN x.read_at END), MAX(CASE WHEN LOWER(status) = 'up' THEN x.read_at END) "
                   "FROM netmap_link x" + good + " GROUP BY r.source, site_a, site_b, link_key HAVING MIN(CASE WHEN LOWER(status) = 'down' "
                   "THEN x.read_at END) IS NOT NULL ORDER BY site_a, site_b, link_key", window, ROW_LIMIT + 1)
    sites = select(cur, "SELECT site, MAX(down), MIN(x.read_at), MAX(x.read_at) FROM netmap_site x" + good +
                   " AND (down > 0 OR LOWER(status) = 'down') GROUP BY r.source, site, site_key ORDER BY site, site_key", window, ROW_LIMIT + 1)
    observed = select(cur, "SELECT COUNT(*) FROM netmap_link x" + good, window, 1)[0][0]
    lines, issues = [], []
    for a, b, first, last, up in links[:ROW_LIMIT]:
        name = "%s - %s" % (clean(a), clean(b))
        detail = "WAN link seen down %s to %s" % (stamp(first), stamp(last))
        back = up and up > last
        if back:
            detail += ", seen up " + stamp(up)
        lines.append(name + ": " + detail)
        issues.append(issue("net", "Network map", name, "Recovered" if back else "Not judged", detail, stamp(first), [("WAN link", detail)]))
    for site, down, first, last in sites[:ROW_LIMIT]:
        count = "%d node%s down" % (down, "" if down == 1 else "s") if down else "site down"
        lines.append("%s: %s, first seen %s, last seen %s" % (clean(site), count, stamp(first), stamp(last)))
    title = "WAN links seen down" if links else "No WAN link seen down" if observed else "No WAN link readings"
    return card(MAP, title if reads else EMPTY, "critical" if links else "warning" if sites else "info",
                "%d successful map reads" % reads, summary(lines, max(len(links), len(sites)) > ROW_LIMIT)), issues


def directory(cur, window, stamp, clean):
    # One observation for each failure, even when several subsequent runs retain the same last_failure.
    rows = select(cur, "SELECT l.frm, l.to_dc, l.nc, l.last_failure, l.last_success, l.failures, l.error, r.read_at, l.domain, l.link_key, "
                  "COUNT(*) OVER (PARTITION BY r.template_id, l.frm), r.template_id, r.run_id "
                  "FROM ad_link l JOIN ad_run r ON r.run_id = l.run_id JOIN "
                  "(SELECT retained.link_key, retained.run_id, ROW_NUMBER() OVER ("
                  "PARTITION BY history.template_id, retained.link_key, retained.last_failure "
                  "ORDER BY history.read_at DESC, retained.run_id DESC) AS latest FROM ad_link retained "
                  "JOIN ad_run history ON history.run_id = retained.run_id WHERE retained.last_failure BETWEEN %s AND %s) pick "
                  "ON pick.latest = 1 AND pick.run_id = l.run_id AND pick.link_key = l.link_key ORDER BY l.last_failure, l.link_key",
                  window, ROW_LIMIT + 1)
    lines, controllers, link_states = [], {}, defaultdict(dict)
    # Site labels are optional. A link remains evidence even without the DC inventory.
    sites = {}
    pairs = sorted({(r[-1], r[0]) for r in rows[:ROW_LIMIT]})
    if pairs:
        try:
            keys = ", ".join(["(%s, %s)"] * len(pairs))
            sites = {(rid, dc): site for rid, dc, site in select(cur, "SELECT run_id, dc, site FROM ad_dc "
                     "WHERE (run_id, dc) IN (" + keys + ")", [value for pair in pairs for value in pair], len(pairs))}
        except NoData as e:
            if str(e) != MISSING:
                raise
    for frm, to, nc, at, success, failures, error, read_at, domain, link_key, total, template, rid in rows[:ROW_LIMIT]:
        key = (template, frm)
        site = sites.get((rid, frm))
        group = "AD site " + clean(site) if site else clean(domain or "Active Directory")
        frm, to, nc = (clean(v) for v in (frm, to, nc))
        back = not failures and success and success > at
        what = "Replication to %s (%s) failed: %s" % (to, nc, clean(error or "no error recorded"))
        detail = "%s · %s · %s. %s (AD read %s)." % (stamp(at), frm, what, "Worked again at " + stamp(success) if back else
                                                      "%d consecutive failures" % failures, stamp(read_at))
        lines.append(detail)
        state = "Recovered" if back else "At risk" if failures else "Not judged"
        since = "last success " + stamp(success) if back else stamp(at)
        previous = link_states[key].get(link_key)
        observation = (read_at, rid)
        if previous is None or observation > previous[0]:
            link_states[key][link_key] = (observation, state)
        if key not in controllers:
            controllers[key] = issue("dc", group, frm, state, what, since, [])
        controller = controllers[key]
        controller["items"].append(dict(name="%s → %s, %s" % (frm, to, nc), state=state, what=detail + " Same time, not cause."))
        controller["n"] = len(controller["items"])
        if controller["n"] > 1:
            controller["what"] = "%d replication failures in the window" % controller["n"]
            controller["since"] = "See individual links"
            # A controller is recovered only when every affected link has a later success.
            states = {value[1] for value in link_states[key].values()}
            controller["state"] = "At risk" if "At risk" in states else "Not judged" if "Not judged" in states else "Recovered"
        if controller["n"] < total:
            controller.update(state="Not judged", since="See individual links",
                              what="%d of %d replication failures shown" % (controller["n"], total))
    return card(AD, summary(lines, len(rows) > ROW_LIMIT) or EMPTY, "warning" if rows else "info", detail=LAST), list(controllers.values())


def syslog_card(live, nodes, orion, window, stamp, clean):
    try:
        result = live(orion, sorted(nodes), window)
        if not result["ok"]:
            return card(SYSLOG, "not read: " + clean(result["why"]))
        lines, total = [], 0
        for nid, data in result["by_node"].items():
            total += data["count"]
            for entry in data["top"]:
                lines.append("%s · %s · %s (×%d)" % (stamp(entry["last"]), nodes[nid], clean(entry["message"]), entry["count"]))
        return card(SYSLOG, ("%d entries on the storage path" % total) if total else EMPTY,
                    subject="Log Analyzer, capped at %d entries%s." % (LOG_LIMIT, " (cap reached)" if result["capped"] else ""),
                    detail=summary(lines))
    except NoData as e:
        return card(SYSLOG, "not read: " + str(e))
    except Exception:
        return card(SYSLOG, "not read: Log Analyzer could not be read")


def solarwinds(cur, hosts, patterns, window, stamp, clean, cards, issues):
    snaps = select(cur, "SELECT snap_id, orion, read_at, alerts FROM sw_snapshot WHERE ok = 1 ORDER BY snap_id DESC", limit=1)
    if not snaps:
        raise NoData(MISSING)
    sid, orion, snap_at, alert_count = snaps[0]
    nodes, judged, path, neighbours = topology(cur, sid, hosts, patterns)
    nodes = {nid: clean(name) for nid, name in nodes.items()}
    topologies = {sid: (nodes, neighbours)}
    events, alerts, capped, elsewhere = {}, [], False, ""
    try:
        cards[EVENTS], events, capped = solar_events(cur, sid, orion, hosts, patterns, topologies, window, stamp, clean)
        elsewhere = cards[EVENTS]["subject"]
        names = list(dict.fromkeys(name for snapshot_nodes, _ in topologies.values() for name in snapshot_nodes.values()))
        if names:
            listed = ", ".join(names[:8]) + (", and %d more" % (len(names) - 8) if len(names) > 8 else "")
            cards[EVENTS]["subject"] += " Path (sw_link, two hops): %s." % listed
        else:
            cards[EVENTS] = card(EVENTS, path, subject=elsewhere)
    except NoData as e:
        cards[EVENTS] = card(EVENTS, str(e))
    if nodes:
        try:
            cards[ALERTS], alerts = solar_alerts(cur, sid, nodes, window, stamp, clean)
            cards[ALERTS]["subject"] = "sw_alert_active, snapshot of " + stamp(snap_at)
            if alert_count is None and not alerts:
                cards[ALERTS]["title"] = "not read: the snapshot did not read active alerts"
        except NoData as e:
            cards[ALERTS] = card(ALERTS, str(e))
    else:
        for source in (ALERTS, SYSLOG):
            cards[source] = card(source, path, subject=UNKNOWN if path == MISSING else "")
    for snap in sorted(events.keys() | {sid}):
        snapshot_nodes, neighbours = topologies[snap]
        issues.extend(network_issues(cur, snap, snapshot_nodes, events.get(snap, {}), alerts if snap == sid else [],
                                     capped, neighbours, clean))
    return nodes, orion, judged, elsewhere


def clean_source(value, redact):
    text = str(value or "")
    parts, start = [], 0
    for url in re.finditer(r"(?i)(?<![a-z0-9+.-])[a-z][a-z0-9+.-]*://([^\s/?#]+)", text):
        authority = url.group(1)
        if "@" in authority:
            _, _, host = authority.rpartition("@")
            if not host:
                return "<removed>"
            parts.extend((text[start:url.start(1)], host))
            start = url.end()
        elif ":" in authority:
            # Legacy snapshots can end inside userinfo, before the @host.
            ipv6 = re.fullmatch(r"\[[a-fA-F0-9:.]+\](?::\d+)?", authority)
            port_and_path = re.fullmatch(r"[^:@]+:\d+", authority) and text[url.end():].startswith("/")
            if not ipv6 and not port_and_path:
                return "<removed>"
    return redact("".join(parts) + text[start:])


def read(conn, scope, minutes=15, storage_nodes=(), live=None, tz=None, clean=None):
    """scope is the story's UTC epoch bounds and lost hosts. End the database transaction before querying SWIS."""
    # The controller also loads this file just for the cards when a read was skipped.
    try:
        from ansible.module_utils.site_redact import redact
        from ansible.module_utils.site_swis_db import begin_read
    except ImportError:
        from site_redact import redact
        from site_swis_db import begin_read

    redact_text = clean or redact

    def clean(value):
        return clean_source(value, redact_text)

    def stamp(value):
        if not value:
            return "unknown time"
        dt = datetime.datetime.fromisoformat(str(value))
        return dt.replace(tzinfo=datetime.timezone.utc).astimezone(tz or datetime.timezone.utc).strftime("%d %b %Y %H:%M:%S %Z")

    pad = datetime.timedelta(minutes=max(0, minutes))
    begin, end = [datetime.datetime.fromtimestamp(scope[k], datetime.timezone.utc).replace(tzinfo=None) for k in ("begin", "end")]
    window = (begin - pad, end + pad)
    cards, issues = {}, []
    nodes, orion, elsewhere = {}, "", ""
    try:
        begin_read(conn)
        try:
            sites = scope.get("sites") or []
            default_site = sites[0] if len(sites) == 1 else ""
            hosts = {}
            for host in scope["hosts"]:
                if isinstance(host, str):
                    host = dict(vcenter="", host=host, name=host)
                hosts[host["vcenter"], host["host"]] = (host["name"].lower().rstrip("."),
                                                        (host.get("site") or default_site).casefold())
            try:
                nodes, orion, judged, elsewhere = solarwinds(conn.cursor(), hosts, storage_nodes, window, stamp, clean, cards, issues)
            except NoData as e:
                judged = "SolarWinds topology: " + str(e)
                for source in (EVENTS, ALERTS, SYSLOG):
                    cards[source] = card(source, str(e))
            for source, reader in ((MAP, network_map), (AD, directory)):
                try:
                    cards[source], rows = reader(conn.cursor(), window, stamp, clean)
                    issues.extend(rows)
                except NoData as e:
                    cards[source] = card(source, str(e))
            try:
                select(conn.cursor(), "SELECT 1 FROM sw_app", limit=1)
                cards[APPS] = card(APPS, EMPTY, subject="sw_app keeps the newest snapshot only.")
            except NoData as e:
                cards[APPS] = card(APPS, str(e))
        finally:
            conn.rollback()
    except Exception:
        return unavailable(DB_ERROR)

    if not live:
        cards[SYSLOG] = card(SYSLOG, "not read: vm_watch_review_syslog is off")
    elif nodes:
        cards[SYSLOG] = syslog_card(live, nodes, orion, window, stamp, clean)
    elif not cards[SYSLOG]["title"].startswith("not read"):
        cards[SYSLOG] = card(SYSLOG, "not read: " + cards[SYSLOG]["title"])
    cards[TRAPS] = card(TRAPS, "not read: SNMP traps are unavailable")
    net_note = [cards[MAP]["title"] + " (" + cards[MAP]["subject"] + ")."] if cards[MAP]["subject"] else []
    if elsewhere:
        net_note.append(elsewhere)
    return dict(cards=[cards[s] for s in SOURCES], issues=issues, judged=judged, network_note=" ".join(net_note),
                window="Outage context from %s to %s." % (stamp(window[0]), stamp(window[1])))
