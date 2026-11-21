# -*- coding: utf-8 -*-
"""SolarWinds in the report database (1.96.0, docs/REPORT_DATABASE.md "The SolarWinds snapshot"): one read of
SolarWinds kept as a snapshot, so the reports answer from the database instead of asking SolarWinds each time.

The job "SolarWinds - snapshot" (playbooks/solarwinds_snapshot.yml, roles/solarwinds_snapshot/library/site_swis_snapshot.py)
calls read_swis() and write() every 10 minutes. The reports call answer() or the reader functions at the end.

Replace by snapshot, not deltas. Each run is one snapshot: a row in sw_snapshot and its rows in the state tables, written
in one transaction. A reader takes the newest snapshot with ok = 1, so it always sees one whole read. The state tables
keep the last keep_snapshots snapshots. sw_snapshot's own rows (the counts) are kept keep_days. sw_event is one row per
SolarWinds event (EventID), kept event_days (400), read incrementally.

Read only on SolarWinds' side: every query goes through site_swis.Client (one SWQL SELECT, every column named). The
core read is site_swis.collect() itself, with every node custom property, so answer() gives back exactly the dict
collect() gives (the contract with the network map and the portal: the same keys, plus snapshot_at, age_seconds and
source). The other tables ask only for the columns this Orion has (Metadata.Property, one query).

Standard library only, apart from the database session (site_report_db, PyMySQL). MySQL 8.4 and MariaDB 10.11."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import json
import re
import sys
import time

try:
    from ansible.module_utils import site_swis
except ImportError:                      # the tests and the portal load it as a plain module
    import site_swis


def _swis_errors():
    """SwisError as raised by the client: site_swis can be loaded twice (ansible.module_utils.site_swis and a plain
    site_swis, as in one test run or a portal that has both), each with its own class. Both are caught."""
    out = [site_swis.SwisError]
    for name in ("site_swis", "ansible.module_utils.site_swis"):
        cls = getattr(sys.modules.get(name), "SwisError", None)
        if cls is not None and cls not in out:
            out.append(cls)
    return tuple(out)


SCHEMA_VERSION = 1
COMPONENT = "solarwinds"
LOCK = "site_reports.sw_snapshot"
TABLE_OPTS = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"
DB_SNAPSHOT, SWIS_LIVE = "db-snapshot", "swis-live"
# a node custom property's columns that are SolarWinds' own, not a property
CP_SYSTEM = set(getattr(site_swis, "CP_SYSTEM", ("NodeID", "InstanceType", "Uri", "InstanceSiteId", "DisplayName", "Description")))

# ------------------------------------------------------------------------------------------------------ the tables
CREATE = [
    "CREATE TABLE IF NOT EXISTS sw_snapshot (snap_id BIGINT NOT NULL AUTO_INCREMENT, read_at DATETIME NOT NULL, "
    "finished_at DATETIME NULL, job BIGINT NOT NULL DEFAULT 0, ok TINYINT NOT NULL DEFAULT 0, orion VARCHAR(255) NOT NULL DEFAULT '', "
    "error VARCHAR(1000) NULL, seconds DECIMAL(8,1) NULL, queries INT NULL, nodes INT NULL, nodes_down INT NULL, "
    "nodes_warning INT NULL, links INT NULL, interfaces INT NULL, alerts INT NULL, events INT NULL, apps INT NULL, "
    "apps_down INT NULL, components INT NULL, volumes INT NULL, vim_hosts INT NULL, vim_vms INT NULL, "
    "core MEDIUMTEXT NULL, not_read MEDIUMTEXT NULL, "
    "PRIMARY KEY (snap_id), KEY sw_snapshot_ok (ok, snap_id), KEY sw_snapshot_read_at (read_at)) " + TABLE_OPTS,
    "CREATE TABLE IF NOT EXISTS sw_node (snap_id BIGINT NOT NULL, node_id BIGINT NOT NULL, seq INT NOT NULL, "
    "name VARCHAR(512) NOT NULL, name_key VARCHAR(255) NOT NULL, dns VARCHAR(512) NULL, ip VARCHAR(64) NULL, ips TEXT NULL, "
    "status_code INT NULL, status_text VARCHAR(512) NULL, unmanaged TINYINT NOT NULL DEFAULT 0, vendor VARCHAR(255) NULL, "
    "model VARCHAR(255) NULL, location VARCHAR(512) NULL, resp DOUBLE NULL, loss DOUBLE NULL, cpu_pct DOUBLE NULL, "
    "mem_pct DOUBLE NULL, last_poll DATETIME NULL, last_boot DATETIME NULL, kind VARCHAR(64) NULL, os_version VARCHAR(255) NULL, "
    "wm_lat DOUBLE NULL, wm_lon DOUBLE NULL, props MEDIUMTEXT NULL, "
    "PRIMARY KEY (snap_id, node_id), KEY sw_node_name (snap_id, name_key), KEY sw_node_status (snap_id, status_code), "
    "KEY sw_node_ip (snap_id, ip)) " + TABLE_OPTS,
    "CREATE TABLE IF NOT EXISTS sw_link (snap_id BIGINT NOT NULL, seq INT NOT NULL, src_node BIGINT NULL, src_if BIGINT NULL, "
    "dst_node BIGINT NULL, dst_if BIGINT NULL, "
    "PRIMARY KEY (snap_id, seq), KEY sw_link_src (snap_id, src_node), KEY sw_link_dst (snap_id, dst_node)) " + TABLE_OPTS,
    "CREATE TABLE IF NOT EXISTS sw_interface (snap_id BIGINT NOT NULL, interface_id BIGINT NOT NULL, seq INT NOT NULL, "
    "node_id BIGINT NULL, name VARCHAR(512) NULL, caption VARCHAR(512) NULL, speed DOUBLE NULL, in_pct DOUBLE NULL, "
    "out_pct DOUBLE NULL, in_bps DOUBLE NULL, out_bps DOUBLE NULL, status INT NULL, oper_status INT NULL, admin_status INT NULL, "
    "in_errors_hour DOUBLE NULL, out_errors_hour DOUBLE NULL, in_discards_hour DOUBLE NULL, out_discards_hour DOUBLE NULL, "
    "in_errors_today DOUBLE NULL, out_errors_today DOUBLE NULL, "
    "PRIMARY KEY (snap_id, interface_id), KEY sw_interface_node (snap_id, node_id)) " + TABLE_OPTS,
    "CREATE TABLE IF NOT EXISTS sw_alert_active (snap_id BIGINT NOT NULL, alert_active_id BIGINT NOT NULL, alert_id BIGINT NULL, "
    "name VARCHAR(512) NULL, severity INT NULL, triggered_at DATETIME NULL, message VARCHAR(1000) NULL, acknowledged TINYINT NULL, "
    "entity_type VARCHAR(255) NULL, entity_caption VARCHAR(512) NULL, node_id BIGINT NULL, node_caption VARCHAR(512) NULL, "
    "PRIMARY KEY (snap_id, alert_active_id), KEY sw_alert_node (snap_id, node_id)) " + TABLE_OPTS,
    "CREATE TABLE IF NOT EXISTS sw_event (event_id BIGINT NOT NULL, event_time DATETIME NOT NULL, node_id BIGINT NULL, "
    "net_object_type VARCHAR(32) NULL, net_object_id BIGINT NULL, event_type INT NULL, type_name VARCHAR(255) NULL, "
    "message VARCHAR(1000) NULL, acknowledged TINYINT NULL, snap_id BIGINT NOT NULL, "
    "PRIMARY KEY (event_id), KEY sw_event_time (event_time), KEY sw_event_node (node_id, event_time)) " + TABLE_OPTS,
    "CREATE TABLE IF NOT EXISTS sw_app (snap_id BIGINT NOT NULL, app_id BIGINT NOT NULL, node_id BIGINT NULL, name VARCHAR(512) NULL, "
    "status INT NULL, status_text VARCHAR(512) NULL, unmanaged TINYINT NULL, components INT NULL, components_not_up INT NULL, "
    "PRIMARY KEY (snap_id, app_id), KEY sw_app_node (snap_id, node_id), KEY sw_app_status (snap_id, status)) " + TABLE_OPTS,
    "CREATE TABLE IF NOT EXISTS sw_component (snap_id BIGINT NOT NULL, component_id BIGINT NOT NULL, app_id BIGINT NULL, "
    "name VARCHAR(512) NULL, kind VARCHAR(255) NULL, status INT NULL, status_text VARCHAR(512) NULL, value DOUBLE NULL, "
    "message VARCHAR(1000) NULL, PRIMARY KEY (snap_id, component_id), KEY sw_component_app (snap_id, app_id)) " + TABLE_OPTS,
    "CREATE TABLE IF NOT EXISTS sw_volume (snap_id BIGINT NOT NULL, volume_id BIGINT NOT NULL, node_id BIGINT NULL, "
    "caption VARCHAR(512) NULL, kind VARCHAR(255) NULL, size_bytes DOUBLE NULL, used_bytes DOUBLE NULL, used_pct DOUBLE NULL, "
    "status INT NULL, "
    "PRIMARY KEY (snap_id, volume_id), KEY sw_volume_node (snap_id, node_id)) " + TABLE_OPTS,
    "CREATE TABLE IF NOT EXISTS sw_vim_host (snap_id BIGINT NOT NULL, host_id BIGINT NOT NULL, node_id BIGINT NULL, "
    "name VARCHAR(512) NULL, name_key VARCHAR(255) NULL, cluster VARCHAR(512) NULL, status INT NULL, connection_state VARCHAR(64) NULL, "
    "power_state VARCHAR(64) NULL, cpu_pct DOUBLE NULL, mem_pct DOUBLE NULL, vm_count INT NULL, vm_running INT NULL, "
    "product VARCHAR(255) NULL, "
    "PRIMARY KEY (snap_id, host_id), KEY sw_vim_host_name (snap_id, name_key)) " + TABLE_OPTS,
    "CREATE TABLE IF NOT EXISTS sw_vim_vm (snap_id BIGINT NOT NULL, vm_id BIGINT NOT NULL, host_id BIGINT NULL, node_id BIGINT NULL, "
    "name VARCHAR(512) NULL, name_key VARCHAR(255) NULL, power_state VARCHAR(64) NULL, status INT NULL, cpu_pct DOUBLE NULL, "
    "mem_pct DOUBLE NULL, ip VARCHAR(64) NULL, guest_state VARCHAR(64) NULL, "
    "PRIMARY KEY (snap_id, vm_id), KEY sw_vim_vm_name (snap_id, name_key), KEY sw_vim_vm_host (snap_id, host_id)) " + TABLE_OPTS,
]
SCHEMA_VERSION_TABLE = ("CREATE TABLE IF NOT EXISTS schema_version (component VARCHAR(64) NOT NULL PRIMARY KEY, version INT NOT NULL, "
                        "applied_at DATETIME NOT NULL) " + TABLE_OPTS)
TABLES = [re.match(r"CREATE TABLE IF NOT EXISTS (\w+)", s).group(1) for s in CREATE]
STATE = [t for t in TABLES if t not in ("sw_snapshot", "sw_event")]      # the tables a snapshot replaces

# each table's columns, in the order write() sends them (snap_id first where it has one)
COLS = {
    "sw_node": ["snap_id", "node_id", "seq", "name", "name_key", "dns", "ip", "ips", "status_code", "status_text", "unmanaged",
                "vendor", "model", "location", "resp", "loss", "cpu_pct", "mem_pct", "last_poll", "last_boot", "kind", "os_version",
                "wm_lat", "wm_lon", "props"],
    "sw_link": ["snap_id", "seq", "src_node", "src_if", "dst_node", "dst_if"],
    "sw_interface": ["snap_id", "interface_id", "seq", "node_id", "name", "caption", "speed", "in_pct", "out_pct", "in_bps", "out_bps",
                     "status", "oper_status", "admin_status", "in_errors_hour", "out_errors_hour", "in_discards_hour",
                     "out_discards_hour", "in_errors_today", "out_errors_today"],
    "sw_alert_active": ["snap_id", "alert_active_id", "alert_id", "name", "severity", "triggered_at", "message", "acknowledged",
                        "entity_type", "entity_caption", "node_id", "node_caption"],
    "sw_event": ["event_id", "event_time", "node_id", "net_object_type", "net_object_id", "event_type", "type_name", "message",
                 "acknowledged", "snap_id"],
    "sw_app": ["snap_id", "app_id", "node_id", "name", "status", "status_text", "unmanaged", "components", "components_not_up"],
    "sw_component": ["snap_id", "component_id", "app_id", "name", "kind", "status", "status_text", "value", "message"],
    "sw_volume": ["snap_id", "volume_id", "node_id", "caption", "kind", "size_bytes", "used_bytes", "used_pct", "status"],
    "sw_vim_host": ["snap_id", "host_id", "node_id", "name", "name_key", "cluster", "status", "connection_state", "power_state",
                    "cpu_pct", "mem_pct", "vm_count", "vm_running", "product"],
    "sw_vim_vm": ["snap_id", "vm_id", "host_id", "node_id", "name", "name_key", "power_state", "status", "cpu_pct", "mem_pct", "ip",
                  "guest_state"],
}
# the raw interface row collect() keeps, column by column
IF_RAW = [("InterfaceID", "interface_id"), ("NodeID", "node_id"), ("Name", "name"), ("Caption", "caption"), ("Speed", "speed"),
          ("InPercentUtil", "in_pct"), ("OutPercentUtil", "out_pct"), ("Inbps", "in_bps"), ("Outbps", "out_bps"),
          ("Status", "status"), ("OperStatus", "oper_status")]

# ------------------------------------------------------------------------- what the extra tables ask for: key, SWIS names
# The first SWIS name this Orion has is asked for (Metadata.Property). A key marked * must be there, or the part is not read.
WANT = {
    "node": ("Orion.Nodes", [("*node_id", ["NodeID"]), ("cpu_pct", ["CPULoad"]), ("mem_pct", ["PercentMemoryUsed"]),
                             ("last_poll", ["LastSystemUpTimePollUtc", "LastSync"]), ("last_boot", ["LastBoot"]),
                             ("kind", ["ObjectSubType"]), ("os_version", ["IOSVersion", "Version"])]),
    "ip": ("Orion.NodeIPAddresses", [("*node_id", ["NodeID"]), ("*ip", ["IPAddress"])]),
    "iface": ("Orion.NPM.Interfaces", [("*interface_id", ["InterfaceID"]), ("admin_status", ["AdminStatus"]),
                                       ("in_errors_hour", ["InErrorsThisHour"]), ("out_errors_hour", ["OutErrorsThisHour"]),
                                       ("in_discards_hour", ["InDiscardsThisHour"]), ("out_discards_hour", ["OutDiscardsThisHour"]),
                                       ("in_errors_today", ["InErrorsToday"]), ("out_errors_today", ["OutErrorsToday"])]),
    "alert": ("Orion.AlertActive", [("*alert_active_id", ["AlertActiveID"]), ("*object_id", ["AlertObjectID"]),
                                    ("triggered_at", ["TriggeredDateTime"]), ("message", ["TriggeredMessage"]),
                                    ("acknowledged", ["Acknowledged"])]),
    "alert_object": ("Orion.AlertObjects", [("*object_id", ["AlertObjectID"]), ("alert_id", ["AlertID"]),
                                            ("entity_type", ["EntityType"]), ("entity_caption", ["EntityCaption"]),
                                            ("node_id", ["RelatedNodeId", "RelatedNodeID"]), ("node_caption", ["RelatedNodeCaption"])]),
    "alert_config": ("Orion.AlertConfigurations", [("*alert_id", ["AlertID"]), ("name", ["Name"]), ("severity", ["Severity"])]),
    "event_type": ("Orion.EventTypes", [("*event_type", ["EventType"]), ("*name", ["Name"])]),
    "event": ("Orion.Events", [("*event_id", ["EventID"]), ("*event_time", ["EventTime"]), ("node_id", ["NetworkNode"]),
                               ("net_object_type", ["NetObjectType"]), ("net_object_id", ["NetObjectID"]),
                               ("event_type", ["EventType"]), ("message", ["Message"]), ("acknowledged", ["Acknowledged"])]),
    "app": ("Orion.APM.Application", [("*app_id", ["ApplicationID"]), ("node_id", ["NodeID"]), ("name", ["Name"]),
                                      ("status", ["Status"]), ("status_text", ["StatusDescription"]), ("unmanaged", ["Unmanaged"])]),
    "component": ("Orion.APM.Component", [("*component_id", ["ComponentID"]), ("app_id", ["ApplicationID"]), ("name", ["Name"]),
                                          ("kind", ["ComponentType"]), ("*status", ["Status"]), ("status_text", ["StatusDescription"]),
                                          # the statistic and message SAM shows for a component (the names vary by SAM version)
                                          ("value", ["StatisticData", "ComponentStatisticData", "Statistic"]),
                                          ("message", ["ComponentMessage", "StatusMessage", "ErrorMessage", "Message"])]),
    "volume": ("Orion.Volumes", [("*volume_id", ["VolumeID"]), ("node_id", ["NodeID"]), ("caption", ["Caption"]),
                                 ("kind", ["VolumeType"]), ("size_bytes", ["VolumeSize"]), ("used_bytes", ["VolumeSpaceUsed"]),
                                 ("used_pct", ["VolumePercentUsed"]), ("status", ["Status"])]),
    "vim_cluster": ("Orion.VIM.Clusters", [("*cluster_id", ["ClusterID"]), ("*name", ["Name"])]),
    "vim_host": ("Orion.VIM.Hosts", [("*host_id", ["HostID"]), ("node_id", ["NodeID"]), ("name", ["HostName", "Name"]),
                                     ("cluster_id", ["ClusterID"]), ("status", ["Status", "OverallStatus"]),
                                     ("connection_state", ["ConnectionState"]), ("power_state", ["PowerState"]),
                                     ("cpu_pct", ["CpuLoad", "CPULoad"]), ("mem_pct", ["MemUsage", "MemoryUsage"]),
                                     ("vm_count", ["VmCount"]), ("vm_running", ["VmRunningCount"]),
                                     ("product", ["VMwareProductName", "ProductName"])]),
    "vim_vm": ("Orion.VIM.VirtualMachines", [("*vm_id", ["VirtualMachineID"]), ("host_id", ["HostID"]), ("node_id", ["NodeID"]),
                                             ("name", ["Name", "DisplayName"]), ("power_state", ["PowerState"]),
                                             ("status", ["Status", "OverallStatus"]), ("cpu_pct", ["CPULoad", "CpuLoad"]),
                                             ("mem_pct", ["MemoryUsagePercent", "MemUsage"]), ("ip", ["IPAddress"]),
                                             ("guest_state", ["GuestState"])]),
}
CUSTOM = "Orion.NodesCustomProperties"
PARTS = ["node", "ip", "iface", "alert", "event", "app", "component", "volume", "vim"]
UP_CODES = (1, 22)                       # Up, and "Up" for an external node (site_swis.STATUS)
DOWN_CODES = (2, 12)


# ---------------------------------------------------------------------------------------------------------- small helpers
def now_utc():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, microsecond=0)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if isinstance(dt, datetime.datetime) else ""


def stamp(text):
    """SWIS's '2026-11-21T09:00:05.1230000Z' (or a datetime) -> a naive UTC datetime, or None."""
    if isinstance(text, datetime.datetime):
        return text.replace(tzinfo=None, microsecond=0)
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)", str(text or ""))
    return datetime.datetime(*[int(x) for x in m.groups()]) if m else None


def name_key(*names):
    """The short, lower-case name a host is matched by: 'ESX07.corp.example.mil' -> 'esx07'. An IP stays whole."""
    for n in names:
        n = str(n or "").strip().lower()
        if not n:
            continue
        if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", n) or ":" in n:
            return n[:255]
        return n.split(".", 1)[0][:255]
    return ""


def _cut(v, n):
    return None if v is None else str(v)[:n]


def _int(v):
    try:
        return None if v is None or v == "" else int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None


def _flag(v):
    return None if v is None else (1 if v in (True, 1, "1", "true", "True") else 0)


def _float(v):
    try:
        f = float(v)
        return f if f == f and abs(f) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _num_back(v):
    """A DOUBLE or DECIMAL read back: a float (an int when it was one), None as None."""
    if v is None:
        return None
    f = float(v)
    return int(f) if f.is_integer() and abs(f) < 2 ** 53 else f


# ------------------------------------------------------------------------------------------- reading SolarWinds once
def _have(client, entities):
    """{entity: set(property names)} for the entities this Orion has (Metadata.Property, one query). A navigation
    property (a link to another entity) is not a column."""
    rows = client.query("SELECT EntityName, Name, IsNavigable FROM Metadata.Property WHERE EntityName IN @names",
                        {"names": sorted(set(entities))})
    out = {}
    for r in rows:
        if r.get("IsNavigable"):
            continue
        out.setdefault(str(r.get("EntityName") or ""), set()).add(str(r.get("Name") or ""))
    return out


def _pick(have, part):
    """(entity, [(key, swis name)]) for a part of WANT. A column it must have that is not there: SwisError "absent"."""
    entity, specs = WANT[part]
    if entity not in have:
        raise site_swis.SwisError("not on this Orion (no %s)" % entity, "absent")
    got, props = [], have[entity]
    for key, names in specs:
        k = key.lstrip("*")
        name = next((n for n in names if n in props), None)
        if name:
            got.append((k, name))
        elif key.startswith("*"):
            raise site_swis.SwisError("not on this Orion (%s has no %s)" % (entity, " or ".join(names)), "absent")
    return entity, got


def _select(cols):
    return ", ".join(n for _, n in cols)


def _rows(rows, cols):
    return [dict((k, r.get(n)) for k, n in cols) for r in rows]


def read_swis(client, opts=None):
    """Read SolarWinds for one snapshot. opts: properties ('all' or a list: the node custom properties kept), skip
    (custom properties never kept), page, chunk, max_nodes, max_rows (a table's rows at most), events_after (the newest
    EventID kept), event_hours (the first read: this many hours back), parts (which extra tables: node, ip, iface, alert,
    event, app, component, volume, vim; default all).
    -> {raw (what site_swis.collect() gave, with every custom property), props (their names), worldmap, extra: {part:
       rows}, not_read (the extra parts'), seconds, queries}. Never raises."""
    o = dict(opts or {})
    started = time.time()
    page, chunk = max(50, int(o.get("page") or 1000)), max(10, int(o.get("chunk") or 500))
    max_rows = max(100, int(o.get("max_rows") or 50000))
    parts = set(PARTS if o.get("parts") is None else o["parts"])         # [] = the network map's read only
    skip = set(str(x) for x in o.get("skip") or [])
    not_read, extra = [], {}
    ents = [CUSTOM] + [v[0] for v in WANT.values()]
    try:
        have = _have(client, ents)
    except _swis_errors() as e:              # not reached at all: collect() below says it the network map's way
        have = None
        not_read.append({"what": "which columns this Orion has (Metadata.Property)",
                         "why": "%s - the other tables are not read, the node custom properties are only the ones named" % e})
    # the node custom properties: every one this Orion has, or the ones named; never one in skip
    want = o.get("properties", "all")
    if isinstance(want, str) and want.strip().lower() in ("all", "*", ""):
        props = sorted(p for p in (have or {}).get(CUSTOM, set()) if p not in CP_SYSTEM)
    else:
        props = [str(p).strip() for p in (want or []) if str(p).strip()]
    props = [p for p in props if p not in skip and site_swis.IDENT.match(p)]
    before, client.calls = client.calls, 0      # collect()'s own count of queries, as the network map's read has it
    # discover off: collect() reads exactly these (1.95.0's discovery would add the ones in skip)
    raw = site_swis.collect(client, {"site_by": "Location", "properties": props, "discover": False, "page": page, "chunk": chunk,
                                     "max_nodes": o.get("max_nodes") or 5000})
    client.calls += before
    out = {"raw": raw, "props": [p for p in props if not any(x.get("what") == "custom property %s" % p for x in raw.get("not_read") or [])],
           "worldmap": any(n.get("wm") is not None for n in raw.get("nodes") or []), "extra": extra, "not_read": not_read}
    if not raw.get("reachable") or have is None:
        out.update(seconds=round(time.time() - started, 1), queries=client.calls)
        return out
    if not raw.get("nodes"):
        out["worldmap"] = False

    def part(name, fn):
        if name not in parts:
            return
        try:
            fn()
        except _swis_errors() as e:
            extra.pop(name, None)
            not_read.append({"what": PART_WORDS[name], "why": str(e)})
        except Exception as e:   # noqa: BLE001 - never raises: what failed is said, the rest is kept
            extra.pop(name, None)
            not_read.append({"what": PART_WORDS[name], "why": "could not be read (%s)" % e.__class__.__name__})

    def paged(p, where="", params=None, order=None):
        entity, cols = _pick(have, p)
        rows, cut = client.paged("SELECT %s FROM %s%s" % (_select(cols), entity, (" WHERE " + where) if where else ""),
                                 order or cols[0][1], params, page, max_rows)
        if cut:
            not_read.append({"what": "%s past %d rows" % (PART_WORDS.get(p, p), max_rows), "why": "only the first were kept (solarwinds_snapshot_max_rows)"})
        return _rows(rows, cols)

    def by_ids(p, ids):
        entity, cols = _pick(have, p)
        got, ids = [], sorted(set(i for i in ids if i is not None))
        for i in range(0, len(ids), chunk):
            got.extend(client.query("SELECT %s FROM %s WHERE %s IN @ids" % (_select(cols), entity, cols[0][1]), {"ids": ids[i:i + chunk]}))
        return _rows(got, cols)

    def nodes():
        extra["node"] = paged("node")

    def ips():
        extra["ip"] = paged("ip", order="NodeID, IPAddress")

    def ifaces():
        ids = [i.get("InterfaceID") for i in raw.get("interfaces") or []]
        extra["iface"] = by_ids("iface", ids) if ids else []

    def alerts():
        act = paged("alert")
        objs = dict((r["object_id"], r) for r in by_ids("alert_object", [a["object_id"] for a in act])) if act else {}
        confs = {}
        try:
            confs = dict((r["alert_id"], r) for r in by_ids("alert_config", [x.get("alert_id") for x in objs.values()])) if objs else {}
        except _swis_errors() as e:
            not_read.append({"what": "the alerts' names (Orion.AlertConfigurations)", "why": str(e)})
        out_ = []
        for a in act:
            ob = objs.get(a["object_id"]) or {}
            cf = confs.get(ob.get("alert_id")) or {}
            out_.append(dict(a, alert_id=ob.get("alert_id"), entity_type=ob.get("entity_type"), entity_caption=ob.get("entity_caption"),
                             node_id=ob.get("node_id"), node_caption=ob.get("node_caption"), name=cf.get("name"), severity=cf.get("severity")))
        extra["alert"] = out_

    def events():
        names = {}
        try:
            names = dict((r["event_type"], r["name"]) for r in paged("event_type"))
        except _swis_errors() as e:
            not_read.append({"what": "the events' kinds (Orion.EventTypes)", "why": str(e)})
        since = now_utc() - datetime.timedelta(hours=max(1, float(o.get("event_hours") or 24)))
        after = _int(o.get("events_after"))
        where, params = "EventTime >= @since", {"since": iso(since)}
        if after:
            where, params["after"] = where + " AND EventID > @after", after
        rows = paged("event", where, params)
        for r in rows:
            r["type_name"] = names.get(r.get("event_type"))
        extra["event"] = rows

    def apps():
        extra["app"] = paged("app")

    def components():
        rows = [c for c in paged("component", "Status <> 1") if _int(c.get("status")) not in UP_CODES]
        extra["component"] = rows
        # every application's count of components ("2 of 9 down"): one query, grouped by SolarWinds
        total = {}
        try:
            for r in client.query("SELECT ApplicationID, COUNT(ComponentID) AS Components FROM Orion.APM.Component GROUP BY ApplicationID"):
                total[_int(r.get("ApplicationID"))] = _int(r.get("Components"))
        except _swis_errors() as e:
            not_read.append({"what": "how many components each SAM application has", "why": str(e)})
            total = None
        down = {}
        for c in rows:
            down[_int(c.get("app_id"))] = down.get(_int(c.get("app_id")), 0) + 1
        for a in extra.get("app") or []:
            aid = _int(a.get("app_id"))
            a["components"] = None if total is None else total.get(aid, 0)
            a["components_not_up"] = down.get(aid, 0)

    def volumes():
        extra["volume"] = paged("volume")

    def vim():
        cl = {}
        try:
            cl = dict((r["cluster_id"], r["name"]) for r in paged("vim_cluster"))
        except _swis_errors() as e:
            if e.kind != "absent":
                not_read.append({"what": "the VIM clusters' names (Orion.VIM.Clusters)", "why": str(e)})
        hosts = paged("vim_host")
        for h in hosts:
            h["cluster"] = cl.get(h.pop("cluster_id", None))
        extra["vim_host"] = hosts
        extra["vim_vm"] = paged("vim_vm")

    for name, fn in (("node", nodes), ("ip", ips), ("iface", ifaces), ("alert", alerts), ("event", events), ("app", apps),
                     ("component", components), ("volume", volumes), ("vim", vim)):
        part(name, fn)
    # a module this Orion does not have is said once, in plain words
    for x in not_read:
        if x["why"].startswith("not on this Orion"):
            x["absent"] = True
    out.update(seconds=round(time.time() - started, 1), queries=client.calls)
    return out


PART_WORDS = {"node": "the nodes' last poll, CPU and memory (Orion.Nodes)", "ip": "the nodes' other IP addresses (Orion.NodeIPAddresses)",
              "iface": "the links' interface errors and discards (Orion.NPM.Interfaces)", "alert": "the active alerts (Orion.AlertActive)",
              "event": "the events (Orion.Events)", "app": "SAM's applications (Orion.APM.Application)",
              "component": "SAM's components not up (Orion.APM.Component)", "volume": "the volumes (Orion.Volumes)",
              "vim": "VIM's hosts and VMs (Orion.VIM.Hosts, Orion.VIM.VirtualMachines)", "alert_object": "the alerts' objects",
              "event_type": "the events' kinds", "vim_cluster": "the VIM clusters", "vim_host": "VIM's hosts", "vim_vm": "VIM's VMs"}


# -------------------------------------------------------------------------------------------- a snapshot as table rows
def core_meta(snap):
    """What a snapshot keeps of collect()'s dict besides its rows: everything to give that dict back."""
    raw = snap["raw"]
    return {"url": raw.get("url") or "", "version": raw.get("version") or {}, "not_read": raw.get("not_read") or [],
            "topology": raw.get("topology") or "", "topology_ok": bool(raw.get("topology_ok")), "cut": bool(raw.get("cut")),
            "read_at": raw.get("read_at") or "", "seconds": raw.get("seconds"), "queries": raw.get("queries"),
            "props": list(snap.get("props") or []), "worldmap": bool(snap.get("worldmap"))}


def to_rows(snap, snap_id):
    """{table: [tuple in COLS order]} for a read that reached SolarWinds."""
    raw, ex = snap["raw"], snap.get("extra") or {}
    more = dict((r.get("node_id"), r) for r in ex.get("node") or [])
    ips = {}
    for r in ex.get("ip") or []:
        ips.setdefault(r.get("node_id"), []).append(str(r.get("ip") or ""))
    out = dict((t, []) for t in COLS)
    for i, n in enumerate(raw.get("nodes") or []):
        m = more.get(n["id"]) or {}
        wm = n.get("wm") or [None, None]
        out["sw_node"].append((snap_id, n["id"], i, _cut(n["name"], 512), name_key(n.get("dns"), n.get("name")), _cut(n.get("dns"), 512),
                               _cut(n.get("ip"), 64), json.dumps(ips[n["id"]]) if n["id"] in ips else None, _int(n.get("status_code")),
                               _cut(n.get("status_text"), 512), 1 if n.get("unmanaged") else 0, _cut(n.get("vendor"), 255),
                               _cut(n.get("model"), 255), _cut(n.get("location"), 512), _float(n.get("resp")), _float(n.get("loss")),
                               _float(m.get("cpu_pct")), _float(m.get("mem_pct")), stamp(m.get("last_poll")), stamp(m.get("last_boot")),
                               _cut(m.get("kind"), 64), _cut(m.get("os_version"), 255), _float(wm[0]), _float(wm[1]),
                               json.dumps(n.get("props") or {}, sort_keys=True, default=str)))
    for i, c in enumerate(raw.get("connections") or []):
        out["sw_link"].append((snap_id, i, _int(c[0]), _int(c[1]), _int(c[2]), _int(c[3])))
    errs = dict((r.get("interface_id"), r) for r in ex.get("iface") or [])
    seen = set()
    for i, f in enumerate(raw.get("interfaces") or []):
        fid = _int(f.get("InterfaceID"))
        if fid is None or fid in seen:
            continue
        seen.add(fid)
        e = errs.get(f.get("InterfaceID")) or {}
        out["sw_interface"].append((snap_id, fid, i, _int(f.get("NodeID")), _cut(f.get("Name"), 512), _cut(f.get("Caption"), 512),
                                    _float(f.get("Speed")), _float(f.get("InPercentUtil")), _float(f.get("OutPercentUtil")),
                                    _float(f.get("Inbps")), _float(f.get("Outbps")), _int(f.get("Status")), _int(f.get("OperStatus")),
                                    _int(e.get("admin_status")), _float(e.get("in_errors_hour")), _float(e.get("out_errors_hour")),
                                    _float(e.get("in_discards_hour")), _float(e.get("out_discards_hour")),
                                    _float(e.get("in_errors_today")), _float(e.get("out_errors_today"))))
    for a in ex.get("alert") or []:
        out["sw_alert_active"].append((snap_id, _int(a.get("alert_active_id")), _int(a.get("alert_id")), _cut(a.get("name"), 512),
                                       _int(a.get("severity")), stamp(a.get("triggered_at")), _cut(a.get("message"), 1000),
                                       _flag(a.get("acknowledged")), _cut(a.get("entity_type"), 255), _cut(a.get("entity_caption"), 512),
                                       _int(a.get("node_id")), _cut(a.get("node_caption"), 512)))
    for e in ex.get("event") or []:
        t = stamp(e.get("event_time"))
        if t is None or _int(e.get("event_id")) is None:
            continue
        out["sw_event"].append((_int(e["event_id"]), t, _int(e.get("node_id")), _cut(e.get("net_object_type"), 32),
                                _int(e.get("net_object_id")), _int(e.get("event_type")), _cut(e.get("type_name"), 255),
                                _cut(e.get("message"), 1000), _flag(e.get("acknowledged")), snap_id))
    for a in ex.get("app") or []:
        out["sw_app"].append((snap_id, _int(a.get("app_id")), _int(a.get("node_id")), _cut(a.get("name"), 512), _int(a.get("status")),
                              _cut(a.get("status_text"), 512), _flag(a.get("unmanaged")), _int(a.get("components")),
                              _int(a.get("components_not_up"))))
    for c in ex.get("component") or []:
        out["sw_component"].append((snap_id, _int(c.get("component_id")), _int(c.get("app_id")), _cut(c.get("name"), 512),
                                    _cut(c.get("kind"), 255), _int(c.get("status")), _cut(c.get("status_text"), 512),
                                    _float(c.get("value")), _cut(c.get("message"), 1000)))
    for v in ex.get("volume") or []:
        out["sw_volume"].append((snap_id, _int(v.get("volume_id")), _int(v.get("node_id")), _cut(v.get("caption"), 512),
                                 _cut(v.get("kind"), 255), _float(v.get("size_bytes")), _float(v.get("used_bytes")),
                                 _float(v.get("used_pct")), _int(v.get("status"))))
    for h in ex.get("vim_host") or []:
        out["sw_vim_host"].append((snap_id, _int(h.get("host_id")), _int(h.get("node_id")), _cut(h.get("name"), 512), name_key(h.get("name")),
                                   _cut(h.get("cluster"), 512), _int(h.get("status")), _cut(h.get("connection_state"), 64),
                                   _cut(h.get("power_state"), 64), _float(h.get("cpu_pct")), _float(h.get("mem_pct")),
                                   _int(h.get("vm_count")), _int(h.get("vm_running")), _cut(h.get("product"), 255)))
    for v in ex.get("vim_vm") or []:
        out["sw_vim_vm"].append((snap_id, _int(v.get("vm_id")), _int(v.get("host_id")), _int(v.get("node_id")), _cut(v.get("name"), 512),
                                 name_key(v.get("name")), _cut(v.get("power_state"), 64), _int(v.get("status")), _float(v.get("cpu_pct")),
                                 _float(v.get("mem_pct")), _cut(v.get("ip"), 64), _cut(v.get("guest_state"), 64)))
    for t in out:                        # a row without its key (SolarWinds gave none) is never written
        out[t] = [r for r in out[t] if r[1] is not None]
    return out


def counts(rows, snap):
    nodes = rows["sw_node"]
    st = COLS["sw_node"].index("status_code")
    um = COLS["sw_node"].index("unmanaged")
    word = lambda r: site_swis.status_of(r[st], r[um])
    app_st = COLS["sw_app"].index("status")
    return {"nodes": len(nodes), "nodes_down": sum(1 for r in nodes if word(r) == site_swis.DOWN),
            "nodes_warning": sum(1 for r in nodes if word(r) == site_swis.WARNING), "links": len(rows["sw_link"]),
            "interfaces": len(rows["sw_interface"]), "alerts": len(rows["sw_alert_active"]) if "alert" in snap.get("extra", {}) else None,
            "events": len(rows["sw_event"]) if "event" in snap.get("extra", {}) else None,
            "apps": len(rows["sw_app"]) if "app" in snap.get("extra", {}) else None,
            "apps_down": sum(1 for r in rows["sw_app"] if r[app_st] in DOWN_CODES) if "app" in snap.get("extra", {}) else None,
            "components": len(rows["sw_component"]) if "component" in snap.get("extra", {}) else None,
            "volumes": len(rows["sw_volume"]) if "volume" in snap.get("extra", {}) else None,
            "vim_hosts": len(rows["sw_vim_host"]) if "vim_host" in snap.get("extra", {}) else None,
            "vim_vms": len(rows["sw_vim_vm"]) if "vim_vm" in snap.get("extra", {}) else None}


# ------------------------------------------------------------------------------------------------------- the database
def pretty(sql):
    """A CREATE TABLE as docs/REPORT_DATABASE.md shows it: a column or key per line."""
    head, rest = sql.split(" (", 1)
    body, tail = rest.rsplit(") ", 1)
    parts, depth, cur = [], 0, ""
    for ch in body:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    return "%s (\n  %s\n) %s;" % (head, ",\n  ".join(parts + [cur.strip()]), tail)


def ddl():
    """The statements the first run sends, for making the tables by hand (solarwinds_db_create: false). The doc carries
    this text and a test keeps them alike."""
    return "\n\n".join([pretty(SCHEMA_VERSION_TABLE)] + [pretty(s) for s in CREATE] +
                       ["INSERT INTO schema_version (component, version, applied_at) VALUES ('%s', %d, UTC_TIMESTAMP());"
                        % (COMPONENT, SCHEMA_VERSION)])


def schema(conn, create):
    """(there, created): every table, made when one is missing and create is true; schema_version's row with them."""
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name LIKE 'sw\\_%%'")
    have = set(str(r[0]) for r in cur.fetchall())
    missing = [t for t in TABLES if t not in have]
    if missing:
        if not create:
            return False, False
        cur.execute(SCHEMA_VERSION_TABLE)
        for s in CREATE:
            cur.execute(s)
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'schema_version'")
    if cur.fetchone() is not None:
        cur.execute("SELECT version FROM schema_version WHERE component = %s", (COMPONENT,))
        r = cur.fetchone()
        if r is None and create:
            cur.execute("INSERT INTO schema_version (component, version, applied_at) VALUES (%s, %s, UTC_TIMESTAMP())", (COMPONENT, SCHEMA_VERSION))
        elif r is not None and int(r[0]) > SCHEMA_VERSION:
            raise RuntimeError("the SolarWinds tables are version %s, newer than this release knows (%s) - update site-automation"
                               % (r[0], SCHEMA_VERSION))
    conn.commit()
    return True, bool(missing)


def lock(conn):
    """True when this session holds the writers' lock (GET_LOCK, at once or not at all): two runs never write together."""
    cur = conn.cursor()
    cur.execute("SELECT GET_LOCK(%s, 0)", (LOCK,))
    r = cur.fetchone()
    return bool(r and r[0] == 1)


def unlock(conn):
    try:
        conn.cursor().execute("SELECT RELEASE_LOCK(%s)", (LOCK,))
    except Exception:            # noqa: BLE001 - the session's end frees it too
        pass


def newest_event(conn):
    cur = conn.cursor()
    cur.execute("SELECT MAX(event_id) FROM sw_event")
    r = cur.fetchone()
    return int(r[0]) if r and r[0] is not None else 0


def _insert(cur, table, rows):
    if not rows:
        return
    cols = COLS[table]
    sql = "INSERT INTO %s (%s) VALUES (%s)" % (table, ", ".join(cols), ", ".join(["%s"] * len(cols)))
    if table == "sw_event":              # an event seen again: only whether it was acknowledged changes
        sql += " ON DUPLICATE KEY UPDATE acknowledged = VALUES(acknowledged)"
    for i in range(0, len(rows), 2000):
        cur.executemany(sql, rows[i:i + 2000])


def write(conn, snap, job=0, now=None, error=""):
    """One snapshot in one transaction -> {snap_id, ok, counts, rows}. A read that did not reach SolarWinds is a row of
    sw_snapshot with ok = 0 and the reason, nothing else."""
    now = now or now_utc()
    raw = snap.get("raw") or {}
    ok = bool(raw.get("reachable")) and not error
    read_at = stamp(raw.get("read_at")) or now
    cur = conn.cursor()
    cur.execute("INSERT INTO sw_snapshot (read_at, job, ok, orion, error) VALUES (%s, %s, 0, %s, %s)",
                (read_at, int(job or 0), _cut(raw.get("url") or "", 255), _cut(error or raw.get("error") or "", 1000) or None))
    sid = int(cur.lastrowid)
    c, n = {}, 0
    if ok:
        rows = to_rows(snap, sid)
        for t in COLS:
            _insert(cur, t, rows[t])
        n = sum(len(v) for v in rows.values())
        c = counts(rows, snap)
    sets = dict(c, ok=1 if ok else 0, finished_at=now_utc(), seconds=snap.get("seconds"), queries=snap.get("queries"),
                core=json.dumps(core_meta(snap), sort_keys=True) if ok else None,
                not_read=json.dumps(snap.get("not_read") or []) if ok else None)
    keys = sorted(sets)
    cur.execute("UPDATE sw_snapshot SET %s WHERE snap_id = %%s" % ", ".join("%s = %%s" % k for k in keys), [sets[k] for k in keys] + [sid])
    conn.commit()
    return {"snap_id": sid, "ok": ok, "counts": c, "rows": n}


def prune(conn, keep_snapshots=12, keep_days=400, event_days=400, now=None):
    """Deletes the state rows of all but the newest keep_snapshots whole snapshots, sw_snapshot rows older than
    keep_days and events older than event_days (0 = never). -> {table: rows deleted}."""
    now = now or now_utc()
    cur = conn.cursor()
    out = {}
    cur.execute("SELECT snap_id FROM sw_snapshot WHERE ok = 1 ORDER BY snap_id DESC LIMIT 1 OFFSET %s", (max(1, int(keep_snapshots)) - 1,))
    r = cur.fetchone()
    if r:
        for t in STATE:
            out[t] = int(cur.execute("DELETE FROM %s WHERE snap_id < %%s" % t, (int(r[0]),)) or 0)
    if keep_days and int(keep_days) > 0:
        cur.execute("SELECT MAX(snap_id) FROM sw_snapshot WHERE ok = 1")
        newest = cur.fetchone()
        out["sw_snapshot"] = int(cur.execute("DELETE FROM sw_snapshot WHERE read_at < %s AND snap_id <> %s",
                                             (now - datetime.timedelta(days=int(keep_days)), int(newest[0] or 0) if newest else 0)) or 0)
    if event_days and int(event_days) > 0:
        out["sw_event"] = int(cur.execute("DELETE FROM sw_event WHERE event_time < %s", (now - datetime.timedelta(days=int(event_days)),)) or 0)
    conn.commit()
    return out


# ------------------------------------------------------------------------------------------------- reading it back
def latest(conn, now=None):
    """The newest whole snapshot: {snap_id, read_at, core, not_read, age_seconds, counts...}, or None."""
    now = now or now_utc()
    cur = conn.cursor()
    cur.execute("SELECT snap_id, read_at, core, not_read, nodes, alerts, events FROM sw_snapshot WHERE ok = 1 ORDER BY snap_id DESC LIMIT 1")
    r = cur.fetchone()
    if not r:
        return None
    try:
        core = json.loads(r[2] or "{}")
    except ValueError:
        core = {}
    try:
        nr = json.loads(r[3] or "[]")
    except ValueError:
        nr = []
    return {"snap_id": int(r[0]), "read_at": r[1], "core": core, "not_read": nr, "nodes": r[4], "alerts": r[5], "events": r[6],
            "age_seconds": max(0, int((now - r[1]).total_seconds()))}


def last_failure(conn):
    """The newest snapshot row that failed after the newest whole one: (read_at, error), or None."""
    cur = conn.cursor()
    cur.execute("SELECT read_at, error FROM sw_snapshot WHERE ok = 0 AND snap_id > (SELECT COALESCE(MAX(snap_id), 0) FROM sw_snapshot "
                "WHERE ok = 1) ORDER BY snap_id DESC LIMIT 1")
    r = cur.fetchone()
    return (r[0], r[1] or "") if r else None


def wanted_props(opts):
    """The custom properties collect() would ask for with these opts, and its not_read lines for the names it refuses."""
    o = dict(opts or {})
    props, bad = [], []
    for p in [o.get("site_by"), o.get("lat_property"), o.get("lon_property")] + list(o.get("properties") or []):
        p = str(p or "").strip()
        if not p or p.lower() in ("location", "auto") or p in props:
            continue
        if not site_swis.IDENT.match(p):
            bad.append({"what": "custom property %s" % p[:64], "why": "not a property name (letters, digits and _ only)"})
            continue
        props.append(p)
    return props, bad


def raw_from_rows(core, nodes, links, ifaces, opts=None, snapshot_at="", age=0):
    """collect()'s dict, from a snapshot's rows (as the database gives them back, in seq order) and the opts the caller
    would give collect(): site_by, lat_property, lon_property, properties, max_nodes."""
    o = dict(opts or {})
    props, bad = wanted_props(o)
    kept = set(core.get("props") or [])
    not_read = list(bad)
    for p in props:
        if p not in kept:
            not_read.append({"what": "custom property %s" % p, "why": "not among the node custom properties in the snapshot "
                             "(SolarWinds has none of that name - the case counts - or solarwinds_snapshot_properties leaves it out)"})
    props = [p for p in props if p in kept]
    if o.get("discover", True):       # 1.95.0: collect() then reads every node custom property too - the snapshot's are those
        found = [p for p in core.get("props") or [] if p not in props]
        props = props + found[:max(0, getattr(site_swis, "MAX_PROPS", 40) - len(props))]
    # collect()'s own not_read, without its custom property lines (those depend on the opts: said above)
    for x in core.get("not_read") or []:
        if not str(x.get("what", "")).startswith("custom property "):
            not_read.append(x)
    wm = bool(core.get("worldmap"))
    cap = max(1, int(o.get("max_nodes") or 5000))
    out_nodes = []
    for r in nodes:
        try:
            pv = json.loads(r["props"] or "{}")
        except ValueError:
            pv = {}
        out_nodes.append({"id": _num_back(r["node_id"]), "name": r["name"], "dns": r["dns"] or "", "ip": r["ip"] or "",
                          "status_code": r["status_code"], "status_text": r["status_text"] or "", "vendor": r["vendor"] or "",
                          "model": r["model"] or "", "location": r["location"] or "", "resp": _num_back(r["resp"]),
                          "loss": _num_back(r["loss"]), "unmanaged": bool(r["unmanaged"]),
                          "props": dict((p, pv.get(p)) for p in props if p in pv),
                          "wm": [_num_back(r["wm_lat"]), _num_back(r["wm_lon"])] if wm else None})
    cut = bool(core.get("cut"))
    if len(out_nodes) > cap:
        out_nodes, cut = out_nodes[:cap], True
    return {"url": core.get("url") or "", "reachable": True, "error": "", "version": core.get("version") or {},
            "not_read": not_read, "nodes": out_nodes,
            "connections": [[_num_back(l["src_node"]), _num_back(l["src_if"]), _num_back(l["dst_node"]), _num_back(l["dst_if"])] for l in links],
            "interfaces": [dict((k, _num_back(f[c]) if c not in ("name", "caption") else f[c]) for k, c in IF_RAW) for f in ifaces],
            "custom_properties": sorted(set(p for n in out_nodes for p in n["props"])),
            "topology": core.get("topology") or "", "topology_ok": bool(core.get("topology_ok")), "cut": cut,
            "read_at": core.get("read_at") or "", "seconds": core.get("seconds"), "queries": core.get("queries"),
            "snapshot_at": snapshot_at, "age_seconds": int(age), "source": DB_SNAPSHOT}


def _dicts(cur):
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def raw_from_db(conn, snap, opts=None):
    """collect()'s dict from the snapshot latest() found."""
    cur = conn.cursor()
    sid = snap["snap_id"]
    cur.execute("SELECT node_id, name, dns, ip, status_code, status_text, unmanaged, vendor, model, location, resp, loss, wm_lat, "
                "wm_lon, props FROM sw_node WHERE snap_id = %s ORDER BY seq", (sid,))
    nodes = _dicts(cur)
    cur.execute("SELECT src_node, src_if, dst_node, dst_if FROM sw_link WHERE snap_id = %s ORDER BY seq", (sid,))
    links = _dicts(cur)
    cur.execute("SELECT %s FROM sw_interface WHERE snap_id = %%s ORDER BY seq" % ", ".join(c for _, c in IF_RAW), (sid,))
    ifaces = _dicts(cur)
    return raw_from_rows(snap["core"], nodes, links, ifaces, opts, iso(snap["read_at"]), snap["age_seconds"])


def answer(opts=None, conn=None, client=None, max_age=1200, why=None, now=None):
    """The network map's read: collect()'s dict, from the newest snapshot when it is at most max_age seconds old, else
    from SolarWinds itself (client: a site_swis.Client, or a function that makes one; None = no live read). Always
    adds snapshot_at, age_seconds and source (db-snapshot or swis-live). why (a list): the reason it was not the
    snapshot is appended. Raises nothing for the database's sake: a database that fails is a reason in why."""
    why = why if why is not None else []
    if conn is not None:
        try:
            s = latest(conn, now)
            if s is None:
                why.append("the report database has no SolarWinds snapshot yet (the job SolarWinds - snapshot)")
            elif max_age and s["age_seconds"] > max_age:
                why.append("the newest SolarWinds snapshot is %d min old (solarwinds_db_max_age: %d min)" % (s["age_seconds"] // 60, max_age // 60))
                f = last_failure(conn)
                if f:
                    why[-1] += "; the last snapshot run failed: %s" % (f[1] or "no reason kept")[:300]
            else:
                return raw_from_db(conn, s, opts)
        except Exception as e:  # noqa: BLE001 - no table, no grant, gone: said, then SolarWinds itself
            why.append("the report database's SolarWinds snapshot could not be read: %s" % (str(e).strip() or e.__class__.__name__)[:300])
    if client is None:
        return None
    c = client() if callable(client) else client
    o = dict(opts or {})
    raw = site_swis.collect(c, o)
    raw.update(snapshot_at=raw.get("read_at") or "", age_seconds=0, source=SWIS_LIVE)
    return raw


def source_text(raw, version="", zone_text=None):
    """The source line a page shows: 'SolarWinds 2026.1 (the report database's snapshot of 09:05 UTC, 4 min old)' or
    'SolarWinds 2026.1 (SWIS, read-only)'."""
    v = (" " + version) if version else ""
    if raw.get("source") == DB_SNAPSHOT:
        t = raw.get("snapshot_at") or ""
        m = re.match(r"^\d{4}-\d\d-\d\dT(\d\d:\d\d)", t)
        when = zone_text(t) if zone_text else ((m.group(1) + " UTC") if m else t)
        age = int(raw.get("age_seconds") or 0) // 60
        return "SolarWinds%s (the report database's snapshot of %s, %s)" % (v, when, "under a minute old" if age < 1 else "%d min old" % age)
    return "SolarWinds%s (SWIS, read-only)" % v


# -------------------------------------------------------------------------- the other reports' readers (part 2's sections)
def _ph(n):
    return ", ".join(["%s"] * n)


def nodes_for(conn, snap_id, names):
    """Each host name (short, full DNS or IP) -> its node row in the snapshot, or None when SolarWinds does not know it.
    One query. {name: {node_id, name, dns, ip, status_code, status, status_text, unmanaged, last_poll, cpu_pct, mem_pct}}"""
    keys = dict((n, name_key(n)) for n in names or [] if name_key(n))
    out = dict((n, None) for n in names or [])
    if not keys:
        return out
    ks = sorted(set(keys.values()))
    cur = conn.cursor()
    cur.execute("SELECT node_id, name, name_key, dns, ip, status_code, status_text, unmanaged, last_poll, cpu_pct, mem_pct FROM sw_node "
                "WHERE snap_id = %%s AND (name_key IN (%s) OR ip IN (%s))" % (_ph(len(ks)), _ph(len(ks))), [snap_id] + ks + ks)
    rows = _dicts(cur)
    by = {}
    for r in rows:
        r["status"] = site_swis.status_of(r["status_code"], r["unmanaged"])
        by.setdefault(r["name_key"], r)
        if r["ip"]:
            by.setdefault(str(r["ip"]).lower(), r)
    for n, k in keys.items():
        out[n] = by.get(k)
    return out


def alerts_for(conn, snap_id, node_ids):
    """The active alerts on these nodes, newest first: {node_id: [{name, severity, triggered_at, message, entity_caption}]}."""
    ids = sorted(set(int(i) for i in node_ids or [] if i is not None))
    out = dict((i, []) for i in ids)
    if not ids:
        return out
    cur = conn.cursor()
    cur.execute("SELECT node_id, name, severity, triggered_at, message, entity_type, entity_caption, acknowledged FROM sw_alert_active "
                "WHERE snap_id = %%s AND node_id IN (%s) ORDER BY triggered_at DESC" % _ph(len(ids)), [snap_id] + ids)
    for r in _dicts(cur):
        out.setdefault(int(r["node_id"]), []).append(r)
    return out


def apps_for(conn, snap_id, node_ids):
    """SAM's applications on these nodes, by name: {node_id: [{app_id, app, status, status_code, status_text, unmanaged,
    components (how many it has; None when not counted), components_not_up: [{component_id, name, kind, status,
    status_code, status_text, value, message}] (worst first)}]}. Two queries. A node without one, or an Orion without SAM
    (part_note(snap, "app") then says so): []."""
    ids = sorted(set(int(i) for i in node_ids or [] if i is not None))
    out = dict((i, []) for i in ids)
    if not ids:
        return out
    cur = conn.cursor()
    cur.execute("SELECT app_id, node_id, name, status, status_text, unmanaged, components, components_not_up FROM sw_app "
                "WHERE snap_id = %%s AND node_id IN (%s) ORDER BY name, app_id" % _ph(len(ids)), [snap_id] + ids)
    apps = _dicts(cur)
    by = {}
    for a in apps:
        app = {"app_id": int(a["app_id"]), "app": a["name"] or "", "status": site_swis.status_of(a["status"], a["unmanaged"]),
               "status_code": a["status"], "status_text": a["status_text"] or "", "unmanaged": bool(a["unmanaged"]),
               "components": a["components"], "components_not_up": []}
        by[app["app_id"]] = app
        out.setdefault(int(a["node_id"]), []).append(app)
    if by:
        aids = sorted(by)
        cur.execute("SELECT component_id, app_id, name, kind, status, status_text, value, message FROM sw_component "
                    "WHERE snap_id = %%s AND app_id IN (%s) ORDER BY name, component_id" % _ph(len(aids)), [snap_id] + aids)
        for c in _dicts(cur):
            by[int(c["app_id"])]["components_not_up"].append(
                {"component_id": int(c["component_id"]), "name": c["name"] or "", "kind": c["kind"] or "",
                 "status": site_swis.status_of(c["status"]), "status_code": c["status"], "status_text": c["status_text"] or "",
                 "value": _num_back(c["value"]), "message": c["message"] or ""})
        for a in by.values():
            a["components_not_up"].sort(key=lambda c: site_swis.RANK.get(c["status"], 9))
    return out


def part_note(snap, part):
    """What latest() says of a part this snapshot did not read (e.g. "app": no SAM on this Orion): {what, why, absent} or
    None when it was read."""
    words = PART_WORDS.get(part)
    for x in (snap or {}).get("not_read") or []:
        if words and x.get("what") == words:
            return x
    return None


def events_between(conn, start, end, node_ids=None, limit=2000):
    """SolarWinds' events in [start, end] (naive UTC datetimes), oldest first, of these nodes (None = all)."""
    cur = conn.cursor()
    sql = ("SELECT event_id, event_time, node_id, net_object_type, net_object_id, event_type, type_name, message FROM sw_event "
           "WHERE event_time >= %s AND event_time <= %s")
    args = [start, end]
    ids = sorted(set(int(i) for i in node_ids or [] if i is not None)) if node_ids is not None else None
    if ids is not None:
        if not ids:
            return []
        sql += " AND node_id IN (%s)" % _ph(len(ids))
        args += ids
    cur.execute(sql + " ORDER BY event_time, event_id LIMIT %d" % max(1, int(limit)), args)
    return _dicts(cur)
