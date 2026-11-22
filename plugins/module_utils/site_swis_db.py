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
import ipaddress
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


SCHEMA_VERSION = 2                      # 2 (1.113.0): sw_vim_host.vcenter
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
    "CREATE TABLE IF NOT EXISTS sw_event (orion VARCHAR(255) NOT NULL, event_id BIGINT NOT NULL, event_time DATETIME NOT NULL, "
    "node_id BIGINT NULL, net_object_type VARCHAR(32) NULL, net_object_id BIGINT NULL, event_type INT NULL, "
    "type_name VARCHAR(255) NULL, message VARCHAR(1000) NULL, acknowledged TINYINT NULL, snap_id BIGINT NOT NULL, "
    "PRIMARY KEY (orion, event_id), KEY sw_event_time (orion, event_time), KEY sw_event_node (orion, node_id, event_time)) "
    + TABLE_OPTS,
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
    "product VARCHAR(255) NULL, vcenter VARCHAR(512) NULL, "
    "PRIMARY KEY (snap_id, host_id), KEY sw_vim_host_name (snap_id, name_key)) " + TABLE_OPTS,
    "CREATE TABLE IF NOT EXISTS sw_vim_vm (snap_id BIGINT NOT NULL, vm_id BIGINT NOT NULL, host_id BIGINT NULL, node_id BIGINT NULL, "
    "name VARCHAR(512) NULL, name_key VARCHAR(255) NULL, power_state VARCHAR(64) NULL, status INT NULL, cpu_pct DOUBLE NULL, "
    "mem_pct DOUBLE NULL, ip VARCHAR(64) NULL, guest_state VARCHAR(64) NULL, "
    "PRIMARY KEY (snap_id, vm_id), KEY sw_vim_vm_name (snap_id, name_key), KEY sw_vim_vm_host (snap_id, host_id)) " + TABLE_OPTS,
]
# 1.113.0 (schema 2): the columns added to tables an older release made - ALTER TABLE when information_schema lacks them
# (MySQL 8.4 has no ADD COLUMN IF NOT EXISTS). A VIM object's vCenter: without it a VIM node link is never used.
ADDED = [("sw_vim_host", "vcenter", "ALTER TABLE sw_vim_host ADD COLUMN vcenter VARCHAR(512) NULL")]
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
    "sw_event": ["orion", "event_id", "event_time", "node_id", "net_object_type", "net_object_id", "event_type", "type_name", "message",
                 "acknowledged", "snap_id"],
    "sw_app": ["snap_id", "app_id", "node_id", "name", "status", "status_text", "unmanaged", "components", "components_not_up"],
    "sw_component": ["snap_id", "component_id", "app_id", "name", "kind", "status", "status_text", "value", "message"],
    "sw_volume": ["snap_id", "volume_id", "node_id", "caption", "kind", "size_bytes", "used_bytes", "used_pct", "status"],
    "sw_vim_host": ["snap_id", "host_id", "node_id", "name", "name_key", "cluster", "status", "connection_state", "power_state",
                    "cpu_pct", "mem_pct", "vm_count", "vm_running", "product", "vcenter"],
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
                                     ("cluster_id", ["ClusterID"]), ("datacenter_id", ["DataCenterID"]), ("status", ["Status", "OverallStatus"]),
                                     ("connection_state", ["ConnectionState"]), ("power_state", ["PowerState"]),
                                     ("cpu_pct", ["CpuLoad", "CPULoad"]), ("mem_pct", ["MemUsage", "MemoryUsage"]),
                                     ("vm_count", ["VmCount"]), ("vm_running", ["VmRunningCount"]),
                                     ("product", ["VMwareProductName", "ProductName"])]),
    # 1.113.0: a host's vCenter - its datacenter's VCenterID, that vCenter's name (its address when it has none)
    "vim_dc": ("Orion.VIM.DataCenters", [("*datacenter_id", ["DataCenterID"]), ("*vcenter_id", ["VCenterID"])]),
    "vim_vcenter": ("Orion.VIM.VCenters", [("*vcenter_id", ["VCenterID"]), ("name", ["Name", "DisplayName"]), ("ip", ["IPAddress"])]),
    "vim_vm": ("Orion.VIM.VirtualMachines", [("*vm_id", ["VirtualMachineID"]), ("host_id", ["HostID"]), ("node_id", ["NodeID"]),
                                             ("name", ["Name", "DisplayName"]), ("power_state", ["PowerState"]),
                                             ("status", ["Status", "OverallStatus"]), ("cpu_pct", ["CPULoad", "CpuLoad"]),
                                             ("mem_pct", ["MemoryUsagePercent", "MemUsage"]), ("ip", ["IPAddress"]),
                                             ("guest_state", ["GuestState"])]),
}
CUSTOM = "Orion.NodesCustomProperties"
PARTS = ["node", "ip", "iface", "alert", "event", "app", "component", "volume", "vim"]
# each table's primary key (CREATE), for to_rows' one row per key
PKEY = {"sw_node": ("snap_id", "node_id"), "sw_link": ("snap_id", "seq"), "sw_interface": ("snap_id", "interface_id"),
        "sw_alert_active": ("snap_id", "alert_active_id"), "sw_event": ("orion", "event_id"), "sw_app": ("snap_id", "app_id"),
        "sw_component": ("snap_id", "component_id"), "sw_volume": ("snap_id", "volume_id"), "sw_vim_host": ("snap_id", "host_id"),
        "sw_vim_vm": ("snap_id", "vm_id")}
UP_CODES = (1, 22)                       # Up, and "Up" for an external node (site_swis.STATUS)
EVENT_OVERLAP_MINUTES = 10               # the events of the last 10 minutes before the newest kept are read again
DOWN_CODES = (2, 12)


# ---------------------------------------------------------------------------------------------------------- small helpers
def now_utc():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, microsecond=0)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if isinstance(dt, datetime.datetime) else ""


def stamp(text):
    """SWIS's '2026-11-21T09:00:05.1230000Z', '...+01:00' (or a datetime) -> a naive UTC datetime, or None. An offset is
    turned into UTC (1.96.1, review P2-4: it was dropped)."""
    if isinstance(text, datetime.datetime):
        off = text.utcoffset()
        return (text - off if off else text).replace(tzinfo=None, microsecond=0)
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)(?:\.\d+)?\s*(Z|[+-]\d\d:?\d\d)?", str(text or ""))
    if not m:
        return None
    dt = datetime.datetime(*[int(x) for x in m.groups()[:6]])
    z = m.group(7)
    if z and z != "Z":
        sign = -1 if z[0] == "-" else 1
        dt -= sign * datetime.timedelta(hours=int(z[1:3]), minutes=int(z[-2:]))
    return dt


def orion_key(url):
    """Which SolarWinds a snapshot is of: its address's host and port, lower case ('orion.example.mil:17774'). Two
    spellings of one address are one Orion; another host or port is another (1.96.1, review P1-2). An address with a
    login in it (an @ anywhere) or that is not a server's name: "" - never a piece of it (1.96.2, review P1-6)."""
    t = str(url or "").strip().lower()
    if "@" in t:
        return ""
    t = re.sub(r"^[a-z]+://", "", t).split("/", 1)[0]
    m = re.match(r"^([a-z0-9._-]+|\[[0-9a-f:.]+\])(?::(\d{1,5}))?$", t)
    if not m:
        return ""
    return ("%s:%s" % (m.group(1), m.group(2) or site_swis.PORT))[:255]


def next_epoch(key):
    """The events' key for an Orion whose EventIDs started again (rebuilt at the same address): 'host:port' ->
    'host:port#2' -> 'host:port#3' (1.96.2, review P1-2). The events kept before stay under their own key."""
    return "%s#%d" % (str(key or "").partition("#")[0], _epoch_n(str(key or "")) + 1)


def _epoch_like(orion):
    """The LIKE pattern of an Orion's later counts (orion#N), its own _ and % taken literally."""
    return orion.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "#%"


def _epoch_n(key):
    n = key.partition("#")[2]
    return int(n) if n.isdigit() else 1


def _epochs(cur, orion):
    """Every key this Orion's events are kept under: orion itself and orion#N, oldest first."""
    cur.execute("SELECT DISTINCT orion FROM sw_event WHERE orion = %s OR orion LIKE %s", (orion, _epoch_like(orion)))
    return sorted((str(r[0]) for r in cur.fetchall() if r and r[0] is not None), key=_epoch_n)


def short_error(e):
    """A database error for a report's line: no account or host in it (1.96.1, review P2-12)."""
    t = str(e).strip() or e.__class__.__name__
    t = re.sub(r"'[^']*'@'[^']*'", "the account", t)
    # 1.96.2 (review P2-10): PyMySQL's 2003/2005 name the host ("Can't connect to MySQL server on 'db01' (...)")
    t = re.sub(r"((?:server|host) (?:on |at )?)'[^']*'", r"\1the database's host", t, flags=re.I)
    return re.sub(r"\s+", " ", t)[:300]


def normalized_ip(value):
    try:
        return str(ipaddress.ip_address(str(value or "").strip().lower()))
    except ValueError:
        return ""


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
    # never one named Location or Auto: collect() takes those for netmap_site_by's words (review b: the snapshot then
    # listed a property it had not read)
    props = [p for p in props if p not in skip and site_swis.IDENT.match(p) and p.lower() not in ("location", "auto")]
    before, client.calls = client.calls, 0      # collect()'s own count of queries, as the network map's read has it
    # discover off: collect() reads exactly these (1.95.0's discovery would add the ones in skip)
    raw = site_swis.collect(client, {"site_by": "Location", "properties": props, "discover": False, "page": page, "chunk": chunk,
                                     "max_nodes": o.get("max_nodes") or 5000})
    client.calls += before
    out = {"raw": raw, "props": [p for p in props if not any(x.get("what") == "custom property %s" % p for x in raw.get("not_read") or [])],
           "worldmap": any(n.get("wm") is not None for n in raw.get("nodes") or []), "extra": extra, "not_read": not_read}
    if have is not None and "Orion.Nodes" not in have:
        # 1.96.2 (review P1-4): Metadata.Property answered, but with nothing for Orion.Nodes (an empty list): the same as
        # not read - every custom property would be left out and every other table "not on this Orion"
        have = None
        not_read.append({"what": "which columns this Orion has (Metadata.Property)",
                         "why": "it listed no columns of Orion.Nodes - the other tables are not read"})
    if raw.get("reachable") and have is None:
        # 1.96.1 (review P1-3): without the columns this Orion has, the custom properties (the sites, the points) and the
        # other tables are not read: never a whole snapshot - written with ok 0, the reports keep the last whole one
        why = not_read[-1]["why"].split(" - ")[0] if not_read else "no answer"
        out["failed"] = "which columns this Orion has could not be read (Metadata.Property): %s" % why
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

    def keyset(p, where="", params=None):
        """1.96.1 (review d): page by the id (WHERE id > @last ORDER BY id), never by row numbers - a row that moves
        mid-read is neither read twice nor skipped."""
        entity, cols = _pick(have, p)
        key, got, last = cols[0][1], [], None
        while True:
            w, prm = ([where] if where else []), dict(params or {})        # where: its own OR in parentheses
            if last is not None:
                w.append("%s > @last" % key)
                prm["last"] = last
            rows = client.query("SELECT TOP %d %s FROM %s%s ORDER BY %s" % (page, _select(cols), entity,
                                                                         (" WHERE " + " AND ".join(w)) if w else "", key), prm)
            got.extend(rows)
            if len(got) >= max_rows:
                if len(got) > max_rows or len(rows) == page:
                    not_read.append({"what": "%s past %d rows" % (PART_WORDS.get(p, p), max_rows),
                                     "why": "only the first were kept (solarwinds_snapshot_max_rows)"})
                return _rows(got[:max_rows], cols)
            if len(rows) < page or rows[-1].get(key) is None:
                return _rows(got, cols)
            last = rows[-1].get(key)

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
        act = keyset("alert")
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
        after = _int(o.get("events_after"))
        days = _int(o.get("event_days")) or 0
        floor = now_utc() - datetime.timedelta(days=days) if days > 0 else None
        key = str(o.get("events_orion") or "")
        out["event_orion"] = key
        top = None
        if after:
            # 1.96.2 (review P1-2): an Orion rebuilt at the same address starts its EventIDs again - "EventID > the
            # newest kept" would read nothing until the new ones pass the old. Its newest EventID below ours: a new
            # count, read by time and kept under a key of its own (next_epoch), the events kept before untouched.
            try:
                r = client.query("SELECT MAX(EventID) AS Newest FROM Orion.Events")
                top = _int((r[0] if r else {}).get("Newest"))
            except _swis_errors() as e:
                not_read.append({"what": "whether Orion's EventIDs started again (its newest EventID)", "why": str(e)})
        if after and top and top < after:
            out["events_reset"] = {"newest_there": top, "newest_kept": after}
            out["event_orion"] = next_epoch(key) if key else ""
            after = 0
            where, params = ("EventTime >= @floor", {"floor": iso(floor)}) if floor is not None else ("", {})
        elif after:
            # 1.96.1 (review P1-1): from the newest kept on, whatever its age - an outage of the job or of Orion longer
            # than event_hours lost the gap's events. 1.96.2 (review P1-2): an event committed late (EventIDs need not
            # commit in order) is read again by its time - every event of the last EVENT_OVERLAP_MINUTES before the
            # newest kept, however many (the upsert keeps one of each). Only solarwinds_db_event_days (what the
            # database keeps) bounds it, and a gap older than that is said.
            last = stamp(o.get("events_after_at"))
            since = last - datetime.timedelta(minutes=EVENT_OVERLAP_MINUTES) if last is not None else None
            if floor is not None and since is not None and since < floor:
                since = floor
            where, params = "EventID > @after", {"after": after}
            if since is not None:
                where, params["since"] = "(EventID > @after OR EventTime >= @since)", iso(since)
            if floor is not None:
                where, params["floor"] = where + " AND EventTime >= @floor", iso(floor)
            if last is not None and floor is not None and last < floor:
                not_read.append({"what": "the events between %s and %s" % (iso(last), iso(floor)),
                                 "why": "older than solarwinds_db_event_days (%d): the database would not keep them" % days})
        else:
            since = now_utc() - datetime.timedelta(hours=max(1, float(o.get("event_hours") or 24)))
            where, params = "EventTime >= @since", {"since": iso(since)}
        # 1.96.2 (review P1-1): paged by EventID, never by row numbers - an event purged between two pages moved the
        # rest up, and the one at the page's edge was never read
        rows = keyset("event", where, params)
        out["events_after"] = after or 0
        for r in rows:
            r["type_name"] = names.get(r.get("event_type"))
        extra["event"] = rows

    def apps():
        extra["app"] = paged("app")

    def components():
        rows = [c for c in keyset("component", "Status <> 1") if _int(c.get("status")) not in UP_CODES]
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
        hosts = paged("vim_host")       # first: an Orion without VIM is said once, as VIM's hosts and VMs
        dcs = {}
        try:
            centers = dict((r["vcenter_id"], r.get("name") or r.get("ip")) for r in paged("vim_vcenter"))
            dcs = dict((r["datacenter_id"], centers.get(r["vcenter_id"])) for r in paged("vim_dc"))
        except _swis_errors() as e:      # no vCenter known: the reports use no VIM node link (1.113.0)
            not_read.append({"what": "the VIM objects' vCenters (Orion.VIM.DataCenters, Orion.VIM.VCenters)", "why": str(e)})
        for h in hosts:
            h["cluster"] = cl.get(h.pop("cluster_id", None))
            h["vcenter"] = dcs.get(h.pop("datacenter_id", None))
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
              "event_type": "the events' kinds", "vim_cluster": "the VIM clusters", "vim_host": "VIM's hosts", "vim_vm": "VIM's VMs",
              "vim_dc": "the VIM datacenters", "vim_vcenter": "the VIM vCenters"}


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
    orion = orion_key(raw.get("url"))
    # the events' key: this Orion's, or its new count's after its EventIDs started again (1.96.2, review P1-2)
    ev_key = snap.get("event_orion") or (next_epoch(orion) if snap.get("events_reset") and orion else orion)
    more = dict((r.get("node_id"), r) for r in ex.get("node") or [])
    ips = {}
    for r in ex.get("ip") or []:
        ips.setdefault(r.get("node_id"), []).append(str(r.get("ip") or "").lower())   # 1.96.2 (P2-9): matched lower case
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
        out["sw_event"].append((ev_key, _int(e["event_id"]), t, _int(e.get("node_id")), _cut(e.get("net_object_type"), 32),
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
                                   _int(h.get("vm_count")), _int(h.get("vm_running")), _cut(h.get("product"), 255),
                                   _cut(h.get("vcenter"), 512)))
    for v in ex.get("vim_vm") or []:
        out["sw_vim_vm"].append((snap_id, _int(v.get("vm_id")), _int(v.get("host_id")), _int(v.get("node_id")), _cut(v.get("name"), 512),
                                 name_key(v.get("name")), _cut(v.get("power_state"), 64), _int(v.get("status")), _float(v.get("cpu_pct")),
                                 _float(v.get("mem_pct")), _cut(v.get("ip"), 64), _cut(v.get("guest_state"), 64)))
    for t in out:                        # a row without its key (SolarWinds gave none) is never written
        ix = [COLS[t].index(c) for c in PKEY[t]]
        seen, keep = set(), []
        for r in out[t]:
            k = tuple(r[i] for i in ix)
            if any(x is None for x in k) or k in seen:
                continue                 # 1.96.1 (review P2-7): a row read twice (paged while it moved) once - never a PK error
            seen.add(k)
            keep.append(r)
        out[t] = keep
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
            # the new ones: an event read again (the overlap, review P2-5) is not counted twice
            "events": sum(1 for r in rows["sw_event"] if r[1] > (snap.get("events_after") or 0)) if "event" in snap.get("extra", {}) else None,
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


def upgrade_ddl():
    """The statements that bring tables an older release made up to this schema, by hand (solarwinds_db_create: false).
    The doc carries this text too."""
    return "\n".join(["%s;" % sql for _t, _c, sql in ADDED] +
                     ["UPDATE schema_version SET version = %d, applied_at = UTC_TIMESTAMP() WHERE component = '%s' AND version < %d;"
                      % (SCHEMA_VERSION, COMPONENT, SCHEMA_VERSION)])


def missing_columns(conn):
    """[(table, column)] of ADDED that information_schema does not show: the tables of an older release."""
    cur = conn.cursor()
    out = []
    for table, column, _sql in ADDED:
        cur.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = %s "
                    "AND column_name = %s", (table, column))
        r = cur.fetchone()
        if not r or not int(r[0]):
            out.append((table, column))
    return out


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
        # 1.113.0: an older release's tables brought up to this schema in place - each column only when it is not there
        if create:
            for table, column in missing_columns(conn):
                cur.execute(next(sql for t, c, sql in ADDED if (t, c) == (table, column)))
            if r is not None and int(r[0]) < SCHEMA_VERSION and not missing_columns(conn):     # only when it is older
                cur.execute("UPDATE schema_version SET version = %s, applied_at = UTC_TIMESTAMP() WHERE component = %s AND version < %s",
                            (SCHEMA_VERSION, COMPONENT, SCHEMA_VERSION))
    conn.commit()
    return True, bool(missing)


def lock(conn):
    """True when this session holds the writers' lock (GET_LOCK, at once or not at all): two runs never write together."""
    cur = conn.cursor()
    cur.execute("SELECT GET_LOCK(CONCAT(DATABASE(), '.sw_snapshot'), 0)")     # 1.96.1 (P2-8): per database
    r = cur.fetchone()
    return bool(r and r[0] == 1)


def unlock(conn):
    try:
        conn.cursor().execute("SELECT RELEASE_LOCK(CONCAT(DATABASE(), '.sw_snapshot'))")
    except Exception:            # noqa: BLE001 - the session's end frees it too
        pass


def event_cursor(conn, orion=""):
    """(key, EventID, its time): where this Orion's events go on - the key of its newest count (orion, or orion#N after
    its EventIDs started again, 1.96.2 review P1-2) and the newest event kept under it, or (orion, 0, None). Each Orion
    its own cursor (1.96.1, review P1-2)."""
    cur = conn.cursor()
    keys = _epochs(cur, orion) if orion else [orion]
    key = keys[-1] if keys else orion
    cur.execute("SELECT event_id, event_time FROM sw_event WHERE orion = %s ORDER BY event_id DESC LIMIT 1", (key,))
    r = cur.fetchone()
    return (key, int(r[0]), r[1]) if r and r[0] is not None else (key, 0, None)


def newest_event(conn, orion=""):
    """(EventID, its time) of the newest event kept of this Orion's newest count (event_cursor), or (0, None)."""
    return event_cursor(conn, orion)[1:]


def _insert(cur, table, rows, skip=()):
    if not rows:
        return
    cols = COLS[table]
    drop = [i for i, c in enumerate(cols) if (table, c) in skip]
    if drop:                             # an older release's table (solarwinds_db_create: false): written without the column
        cols = [c for i, c in enumerate(cols) if i not in drop]
        rows = [tuple(v for i, v in enumerate(r) if i not in drop) for r in rows]
    sql = "INSERT INTO %s (%s) VALUES (%s)" % (table, ", ".join(cols), ", ".join(["%s"] * len(cols)))
    if table == "sw_event":              # an event seen again: only whether it was acknowledged changes
        sql += " ON DUPLICATE KEY UPDATE acknowledged = VALUES(acknowledged)"
    for i in range(0, len(rows), 2000):
        cur.executemany(sql, rows[i:i + 2000])


def write(conn, snap, job=0, now=None, error=""):
    """One snapshot in one transaction -> {snap_id, ok, counts, rows}. A read that did not reach SolarWinds is a row of
    sw_snapshot with ok = 0 and the reason, nothing else."""
    raw = snap.get("raw") or {}
    error = error or snap.get("failed") or ""
    ok = bool(raw.get("reachable")) and not error
    cur = conn.cursor()
    started = stamp(raw.get("read_at"))
    if now is None and started is not None:
        # 1.96.1 (review P2-6): read_at on the database's clock (now there less the read's own time), so a report's age
        # (TIMESTAMPDIFF against UTC_TIMESTAMP()) never mixes this host's clock with the database's
        took = max(0, int((now_utc() - started).total_seconds()))
        cur.execute("INSERT INTO sw_snapshot (read_at, job, ok, orion, error) VALUES (DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s SECOND), %s, 0, %s, %s)",
                    (took, int(job or 0), orion_key(raw.get("url")), _cut(error or raw.get("error") or "", 1000) or None))
    else:
        cur.execute("INSERT INTO sw_snapshot (read_at, job, ok, orion, error) VALUES (%s, %s, 0, %s, %s)",
                    (started or now or now_utc(), int(job or 0), orion_key(raw.get("url")), _cut(error or raw.get("error") or "", 1000) or None))
    sid = int(cur.lastrowid)
    c, n = {}, 0
    skip = missing_columns(conn) if ok else []
    if ok:
        rows = to_rows(snap, sid)
        for t in COLS:
            _insert(cur, t, rows[t], skip)
        n = sum(len(v) for v in rows.values())
        c = counts(rows, snap)
    sets = dict(c, ok=1 if ok else 0, finished_at=now or now_utc(), seconds=snap.get("seconds"), queries=snap.get("queries"),
                core=json.dumps(core_meta(snap), sort_keys=True) if ok else None,
                not_read=json.dumps(snap.get("not_read") or []) if ok else None)
    keys = sorted(sets)
    cur.execute("UPDATE sw_snapshot SET %s WHERE snap_id = %%s" % ", ".join("%s = %%s" % k for k in keys), [sets[k] for k in keys] + [sid])
    conn.commit()
    return {"snap_id": sid, "ok": ok, "counts": c, "rows": n, "missing_columns": ["%s.%s" % x for x in skip]}


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
def latest(conn, now=None, fresh=True):
    """The newest whole snapshot: {snap_id, read_at, orion, core, not_read, age_seconds, counts...}, or None. age_seconds
    on the database's clock (1.96.1, review P2-6), or against now when given."""
    if fresh:
        try:
            conn.rollback()          # 1.96.1 (review P2-11): a long-lived session (the portal) sees the newest snapshot
        except Exception:        # noqa: BLE001
            pass
    cur = conn.cursor()
    cur.execute("SELECT snap_id, read_at, core, not_read, nodes, alerts, events, orion, TIMESTAMPDIFF(SECOND, read_at, UTC_TIMESTAMP()), links "
                "FROM sw_snapshot WHERE ok = 1 ORDER BY snap_id DESC LIMIT 1")
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
    age = (now - r[1]).total_seconds() if now is not None else (r[8] or 0)
    return {"snap_id": int(r[0]), "read_at": r[1], "core": core, "not_read": nr, "nodes": r[4], "alerts": r[5], "events": r[6],
            "orion": r[7] or "", "age_seconds": max(0, int(age)), "links": r[9]}


def newer_snapshot(conn, before):
    """The snap_id of a whole snapshot written after before (another run's, while this one read), or 0 (1.96.2, review
    P1-3: a run that lost its lock never writes its older read as the newest)."""
    try:
        conn.rollback()              # a fresh look: this session's transaction began before the other run's commit
    except Exception:            # noqa: BLE001
        pass
    cur = conn.cursor()
    cur.execute("SELECT MAX(snap_id) FROM sw_snapshot WHERE ok = 1 AND snap_id > %s", (int(before or 0),))
    r = cur.fetchone()
    return int(r[0]) if r and r[0] is not None else 0


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
    if snap.get("nodes") is not None and len(nodes) != int(snap["nodes"]):
        # 1.96.1 (review c): its rows deleted while it was read - never half a map that says it is whole
        raise RuntimeError("snapshot %d has %d of its %d nodes (deleted while it was read)" % (sid, len(nodes), int(snap["nodes"])))
    cur.execute("SELECT src_node, src_if, dst_node, dst_if FROM sw_link WHERE snap_id = %s ORDER BY seq", (sid,))
    links = _dicts(cur)
    if snap.get("links") is not None and len(links) != int(snap["links"]):
        raise RuntimeError("snapshot %d is incomplete: read %d of %d links" % (sid, len(links), int(snap["links"])))
    cur.execute("SELECT %s FROM sw_interface WHERE snap_id = %%s ORDER BY seq" % ", ".join(c for _, c in IF_RAW), (sid,))
    ifaces = _dicts(cur)
    return raw_from_rows(snap["core"], nodes, links, ifaces, opts, iso(snap["read_at"]), snap["age_seconds"])


def begin_read(conn):
    """A read-only transaction: every query after it sees one moment of the database (REPEATABLE READ), and a long-lived
    session (the portal) sees the newest snapshot (1.96.1, review c and P2-11)."""
    cur = conn.cursor()
    cur.execute("ROLLBACK AND NO CHAIN")
    cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    cur.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")


def end_read(conn):
    try:
        conn.cursor().execute("COMMIT AND NO CHAIN")
    except Exception:            # noqa: BLE001
        pass


def answer(opts=None, conn=None, client=None, max_age=1200, why=None, now=None, orion=""):
    """The network map's read: collect()'s dict, from the newest snapshot when it is at most max_age seconds old (None =
    any age; 0 = never: SolarWinds itself every time), else
    from SolarWinds itself (client: a site_swis.Client, or a function that makes one; None = no live read). Always
    adds snapshot_at, age_seconds and source (db-snapshot or swis-live). why (a list): the reason it was not the
    snapshot is appended. Raises nothing for the database's sake: a database that fails is a reason in why.
    orion (1.96.1, review P1-2): the SolarWinds address the caller would read - a snapshot of another is not served.
    A snapshot without the custom property site_by, lat_property or lon_property names is not served either (P1-3), nor
    one without a property opts' hierarchy_properties names (netmap_hierarchy's, 1.96.2 review P1-5)."""
    why = why if why is not None else []
    o = dict(opts or {})
    if conn is not None and max_age is not None and max_age <= 0:
        # 1.96.2 (review P2-10): 0 asks nothing of the database
        why.append("solarwinds_db_max_age is 0: SolarWinds itself every time")      # review a: 0 never serves the snapshot
        conn = None
    if conn is not None:
        try:
            # 1.96.1 (review c): one read-only transaction - a snapshot pruned between latest() and its rows is never half read
            begin_read(conn)
            s = latest(conn, now, fresh=False)
            cap = max(1, int(o.get("max_nodes") or 5000))
            need = [str(o.get(k) or "").strip() for k in ("site_by", "lat_property", "lon_property")]
            need += [str(x or "").strip() for x in o.get("hierarchy_properties") or []]
            need = [x for i, x in enumerate(need) if x and x.lower() not in ("location", "auto") and x not in need[:i]]
            lacks = [x for x in need if s is not None and x not in ((s.get("core") or {}).get("props") or [])]
            if s is None:
                why.append("the report database has no SolarWinds snapshot yet (the job SolarWinds - snapshot)")
            elif orion and orion_key(orion) and s.get("orion") and orion_key(orion) != s["orion"]:
                why.append("the newest SolarWinds snapshot is of %s, not %s (the address of this template's credential or "
                           "netmap_solarwinds_url)" % (s["orion"], orion_key(orion)))
            elif lacks:
                why.append("the newest SolarWinds snapshot has no node custom property %s (solarwinds_snapshot_properties or "
                           "solarwinds_snapshot_skip_properties)" % ", ".join(lacks))
            elif max_age is not None and s["age_seconds"] > max_age:
                why.append("the newest SolarWinds snapshot is %d min old (solarwinds_db_max_age: %d min)" % (s["age_seconds"] // 60, max_age // 60))
                f = last_failure(conn)
                if f:
                    why[-1] += "; the last snapshot run failed: %s" % (f[1] or "no reason kept")[:300]
            elif (s.get("core") or {}).get("cut"):
                # review b: the snapshot's own read stopped at solarwinds_snapshot_max_nodes - not what collect() reads
                why.append("the newest SolarWinds snapshot was cut at solarwinds_snapshot_max_nodes")
            elif (s.get("links") or 0) > cap * 20:
                why.append("the newest SolarWinds snapshot has more links (%d) than netmap_max_nodes x 20 (%d)" % (s["links"], cap * 20))
            else:
                raw = raw_from_db(conn, s, opts)
                end_read(conn)
                return raw
            end_read(conn)
        except Exception as e:  # noqa: BLE001 - no table, no grant, gone: said, then SolarWinds itself
            end_read(conn)
            why.append("the report database's SolarWinds snapshot could not be read: %s" % short_error(e))
    if client is None:
        return None
    c = client() if callable(client) else client
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
                "WHERE snap_id = %%s AND (name_key IN (%s) OR LOWER(ip) IN (%s))" % (_ph(len(ks)), _ph(len(ks))), [snap_id] + ks + ks)
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


def nodes_for_all(conn, snap_id, names):
    """Each host name or IP -> every node in the snapshot that matches it, so a caller can see two nodes with the same
    short name (dc01 in two domains) or one IP on several nodes (a cluster's address). One query.
    {name: [row, ...]} by node_id, [] when none; a row is nodes_for()'s plus matched_by: "name", "ip" (the node's
    address) or "other ip" (one of its other addresses, Orion.NodeIPAddresses)."""
    keys = dict((n, name_key(n)) for n in names or [] if name_key(n))
    out = dict((n, []) for n in names or [])
    if not keys:
        return out
    ks = sorted(set(keys.values()))
    ipk = [k for k in ks if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", k) or ":" in k]
    like = "".join(" OR ips LIKE %s" for _ in ipk)
    cur = conn.cursor()
    cur.execute("SELECT node_id, name, name_key, dns, ip, ips, status_code, status_text, unmanaged, last_poll, cpu_pct, mem_pct "
                "FROM sw_node WHERE snap_id = %%s AND (name_key IN (%s) OR LOWER(ip) IN (%s)%s) ORDER BY node_id"
                % (_ph(len(ks)), _ph(len(ks)), like), [snap_id] + ks + ks + ['%%"%s"%%' % k for k in ipk])
    rows = _dicts(cur)
    for n, k in keys.items():
        for r in rows:
            try:
                other = [str(x).lower() for x in json.loads(r.get("ips") or "[]")]
            except ValueError:
                other = []
            how = ("name" if r["name_key"] == k else "ip" if str(r["ip"] or "").lower() == k else
                   "other ip" if k in other else "")
            if how:
                row = dict((c, r[c]) for c in r if c != "ips")
                # 1.97.0 (review P2-6): the node's other addresses, so a host by one of them is matched
                row.update(status=site_swis.status_of(r["status_code"], r["unmanaged"]), matched_by=how, other_ips=other)
                out[n].append(row)
    return out


_NODE_COLUMNS = "node_id, name, name_key, dns, ip, ips, status_code, status_text, unmanaged, last_poll, cpu_pct, mem_pct"


def _host_node(row):
    try:
        other = json.loads(row.pop("ips") or "[]")
    except ValueError:
        other = []
    row.update(other_ips=other, status=site_swis.status_of(row["status_code"], row["unmanaged"]))
    return row


def canon(x):
    """A name as matched: every Unicode white space (a tab, a non-breaking space) a space, trimmed, a trailing dot off,
    lower case. SQL cannot trim them all, so nodes_for_hosts also selects by name_key (trimmed in Python) and the match
    is decided here."""
    return re.sub(r"\s", " ", str(x or "")).strip(" ").rstrip(".").lower()


def nodes_for_hosts(conn, snap_id, fulls, shorts, ips):
    """Candidate node rows for one job. Include both the caption and DNS identity."""
    clauses, args = [], [snap_id]
    canonical = "LOWER(TRIM(TRAILING '.' FROM TRIM(REPLACE(REPLACE(REPLACE(%s, CHAR(9), ' '), CHAR(13), ' '), CHAR(10), ' '))))"
    name, dns = canonical % "name", canonical % "dns"
    for column, values in ((name, fulls), (dns, fulls), ("name_key", shorts),
                           ("SUBSTRING_INDEX(%s, '.', 1)" % name, shorts),
                           ("SUBSTRING_INDEX(%s, '.', 1)" % dns, shorts), ("LOWER(ip)", ips)):
        if values:
            clauses.append("%s IN (%s)" % (column, _ph(len(values))))
            args.extend(values)
    for ip in ips:
        clauses.append("ips LIKE %s")
        args.append('%%"%s"%%' % ip)
    # Older snapshots can spell the same IPv6 address with different zero compression.
    v6 = [ip for ip in ips if ":" in ip]
    if v6:
        packed = ", ".join(["INET6_ATON(%s)"] * len(v6))
        clauses.extend(["INET6_ATON(ip) IN (%s)" % packed,
                        "EXISTS (SELECT 1 FROM JSON_TABLE(ips, '$[*]' COLUMNS(addr VARCHAR(64) PATH '$')) AS j "
                        "WHERE INET6_ATON(j.addr) IN (%s))" % packed])
        args.extend(v6 + v6)
    if not clauses:
        return []
    cur = conn.cursor()
    cur.execute("SELECT " + _NODE_COLUMNS + " FROM sw_node WHERE snap_id = %s AND (" + " OR ".join(clauses) + ") ORDER BY node_id", args)
    rows = [_host_node(r) for r in _dicts(cur)]
    for r in rows:
        names = [canon(r.get(k)) for k in ("name", "dns")]
        if any(n in fulls or n.split(".", 1)[0] in shorts for n in names):
            r["matched_by"] = "name"
        else:
            r["matched_by"] = "ip" if normalized_ip(r["ip"]) in ips else "other ip"
    return rows


def vim_nodes(conn, snap_id, ids):
    """The node row, owning kind and VIM object (vim_id, vim_name) for every VIM link to these node IDs, including links
    under unrelated names: a node linked to two VIM objects is neither's (review 1970c P1-3)."""
    if not ids:
        return []
    cur = conn.cursor()
    queries, args = [], []
    for kind in ("host", "vm"):
        queries.append("SELECT n." + _NODE_COLUMNS.replace(", ", ", n.") + ", '" + kind + "' AS kind, v." + kind +
                       "_id AS vim_id, v.name AS vim_name FROM sw_vim_" + kind +
                       " v JOIN sw_node n ON n.snap_id = v.snap_id AND n.node_id = v.node_id "
                       "WHERE v.snap_id = %%s AND v.node_id IN (%s)" % _ph(len(ids)))
        args.extend([snap_id] + sorted(ids))
    cur.execute(" UNION ALL ".join(queries), args)
    return [_host_node(r) for r in _dicts(cur)]


def alerts_for(conn, snap_id, node_ids):
    """The active alerts on these nodes, newest first: {node_id: [{name, severity, triggered_at, message, entity_caption}]}."""
    ids = sorted(set(int(i) for i in node_ids or [] if i is not None))
    out = dict((i, []) for i in ids)
    if not ids:
        return out
    cur = conn.cursor()
    cur.execute("SELECT node_id, name, severity, triggered_at, message, entity_type, entity_caption, acknowledged FROM sw_alert_active "
                "WHERE snap_id = %%s AND node_id IN (%s) ORDER BY triggered_at DESC, alert_active_id DESC" % _ph(len(ids)), [snap_id] + ids)
    for r in _dicts(cur):
        out.setdefault(int(r["node_id"]), []).append(r)
    return out


def apps_for(conn, snap_id, node_ids, fresh=True, own_tx=None):
    """SAM's applications on these nodes, by name: {node_id: [{app_id, app, status, status_code, status_text, unmanaged,
    components (how many it has; None when not counted), components_not_up: [{component_id, name, kind, status,
    status_code, status_text, value, message}] (worst first)}]}. Two queries. A node without one, or an Orion without SAM
    (part_note(snap, "app") then says so): []. Pass fresh=False (or own_tx=False: 1.97.0's name) inside a caller-owned read
    transaction."""
    own = fresh if own_tx is None else own_tx    # 1.97.0 (review P2-1): a caller's read-only transaction is never ended here
    ids = sorted(set(int(i) for i in node_ids or [] if i is not None))
    out = dict((i, []) for i in ids)
    if not ids:
        return out
    if own:
        begin_read(conn)             # review c: the applications and their components of one moment
    try:
        cur = conn.cursor()
        cur.execute("SELECT app_id, node_id, name, status, status_text, unmanaged, components, components_not_up FROM sw_app "
                    "WHERE snap_id = %%s AND node_id IN (%s) ORDER BY name, app_id" % _ph(len(ids)), [snap_id] + ids)
        apps = _dicts(cur)
        comps = []
        if apps:
            aids = sorted(set(int(a["app_id"]) for a in apps))
            cur.execute("SELECT component_id, app_id, name, kind, status, status_text, value, message FROM sw_component "
                        "WHERE snap_id = %%s AND app_id IN (%s) ORDER BY name, component_id" % _ph(len(aids)), [snap_id] + aids)
            comps = _dicts(cur)
    finally:
        if own:
            end_read(conn)
    return _apps_out(out, apps, comps)


def _apps_out(out, apps, comps):
    """apps_for()'s and apps_live()'s answer from rows as the database holds them (one shape for both)."""
    by = {}
    for a in sorted(apps, key=lambda a: (a["name"] or "", int(a["app_id"]))):
        app = {"app_id": int(a["app_id"]), "app": a["name"] or "", "status": site_swis.status_of(a["status"], a["unmanaged"]),
               "status_code": a["status"], "status_text": a["status_text"] or "", "unmanaged": bool(a["unmanaged"]),
               "components": a["components"], "components_not_up": []}
        by[app["app_id"]] = app
        out.setdefault(int(a["node_id"]), []).append(app)
    for c in sorted(comps, key=lambda c: (c["name"] or "", int(c["component_id"]))):
        if int(c["app_id"]) in by:
            by[int(c["app_id"])]["components_not_up"].append(
                {"component_id": int(c["component_id"]), "name": c["name"] or "", "kind": c["kind"] or "",
                 "status": site_swis.status_of(c["status"]), "status_code": c["status"], "status_text": c["status_text"] or "",
                 "value": _num_back(c["value"]), "message": c["message"] or ""})
    for a in by.values():
        a["components_not_up"].sort(key=lambda c: site_swis.RANK.get(c["status"], 9))
    return out


def _ids_of(node_ids):
    return sorted(set(int(i) for i in node_ids or [] if i is not None))


def _live_pick(client, parts, not_read):
    """{part: (entity, cols)} for the parts this Orion has (Metadata.Property, one query); a part it lacks is left out
    and said in not_read (absent). Raises SwisError when SolarWinds cannot be read."""
    have = _have(client, [WANT[p][0] for p in parts])
    got = {}
    for p in parts:
        try:
            got[p] = _pick(have, p)
        except _swis_errors() as e:
            if getattr(e, "kind", "") != "absent":
                raise
            if not_read is not None and p in PART_WORDS:
                not_read.append({"what": PART_WORDS[p], "why": str(e), "absent": True})
    return got


def _by_ids(client, entity, cols, key, ids, chunk=500, where=""):
    """Rows of entity whose column key (a SWIS name) is in ids, in chunks: [{our key: value}]."""
    got = []
    for i in range(0, len(ids), chunk):
        got.extend(client.query("SELECT %s FROM %s WHERE %s IN @ids%s" % (_select(cols), entity, key, where), {"ids": ids[i:i + chunk]}))
    return _rows(got, cols)


def apps_live(client, node_ids, not_read=None):
    """apps_for()'s answer read from SolarWinds itself (a site_swis.Client), for these nodes only: the same keys, values
    and order. Each column as this Orion has it (Metadata.Property). An Orion without SAM: [] for each node, said in
    not_read (a list) as the snapshot says it. Raises site_swis.SwisError when SolarWinds cannot be read."""
    ids = _ids_of(node_ids)
    out = dict((i, []) for i in ids)
    if not ids:
        return out
    got = _live_pick(client, ["app", "component"], not_read)
    if "app" not in got:
        return out
    entity, cols = got["app"]
    col = dict(cols)
    if "node_id" not in col:
        if not_read is not None:
            not_read.append({"what": PART_WORDS["app"], "why": "not on this Orion (%s has no NodeID)" % entity, "absent": True})
        return out
    apps = [a for a in _by_ids(client, entity, cols, col["node_id"], ids) if _int(a.get("app_id")) is not None]
    rows, comps, aids = [], [], sorted(set(_int(a["app_id"]) for a in apps))
    total = None
    if aids and "component" in got:
        centity, ccols = got["component"]
        ccol = dict(ccols)
        if "app_id" in ccol:
            raw = _by_ids(client, centity, ccols, ccol["app_id"], aids, where=" AND %s <> 1" % ccol["status"])
            comps = [c for c in raw if _int(c.get("status")) not in UP_CODES and _int(c.get("component_id")) is not None]
            total = {}
            for i in range(0, len(aids), 500):
                for r in client.query("SELECT %s, COUNT(%s) AS Components FROM %s WHERE %s IN @ids GROUP BY %s"
                                      % (ccol["app_id"], ccol["component_id"], centity, ccol["app_id"], ccol["app_id"]),
                                      {"ids": aids[i:i + 500]}):
                    total[_int(r.get(ccol["app_id"]))] = _int(r.get("Components"))
    down = {}
    for c in comps:
        down[_int(c.get("app_id"))] = down.get(_int(c.get("app_id")), 0) + 1
    for a in apps:                     # as the snapshot writes them (to_rows), so both paths give the same values
        aid = _int(a["app_id"])
        rows.append({"app_id": aid, "node_id": _int(a.get("node_id")), "name": _cut(a.get("name"), 512), "status": _int(a.get("status")),
                     "status_text": _cut(a.get("status_text"), 512), "unmanaged": _flag(a.get("unmanaged")),
                     "components": None if total is None else total.get(aid, 0), "components_not_up": down.get(aid, 0)})
    crow = [{"component_id": _int(c["component_id"]), "app_id": _int(c.get("app_id")), "name": _cut(c.get("name"), 512),
             "kind": _cut(c.get("kind"), 255), "status": _int(c.get("status")), "status_text": _cut(c.get("status_text"), 512),
             "value": _float(c.get("value")), "message": _cut(c.get("message"), 1000)} for c in comps]
    return _apps_out(out, [r for r in rows if r["node_id"] in out], crow)


def alerts_live(client, node_ids, not_read=None):
    """alerts_for()'s answer read from SolarWinds itself (a site_swis.Client), for these nodes only: the same keys, values
    and order (newest first). Never who acknowledged it. Raises site_swis.SwisError when SolarWinds cannot be read."""
    ids = _ids_of(node_ids)
    out = dict((i, []) for i in ids)
    if not ids:
        return out
    got = _live_pick(client, ["alert", "alert_object", "alert_config"], not_read)
    if "alert" not in got or "alert_object" not in got:
        return out
    oent, ocols = got["alert_object"]
    ocol = dict(ocols)
    if "node_id" not in ocol:
        return out
    # 1.96.2 (review P2-8): the active alerts first (few) - Orion.AlertObjects keeps every object an alert ever had
    aent, acols = got["alert"]
    act = [a for a in _rows(client.query("SELECT %s FROM %s" % (_select(acols), aent)), acols) if a.get("object_id") is not None]
    if not act:
        return out
    objs = dict((o["object_id"], o) for o in _by_ids(client, oent, ocols, ocol["object_id"], sorted(set(a["object_id"] for a in act)))
                if o.get("object_id") is not None and _int(o.get("node_id")) in out)
    if not objs:
        return out
    act = [a for a in act if a["object_id"] in objs]
    confs = {}
    if "alert_config" in got:
        cent, ccols = got["alert_config"]
        try:
            aids = sorted(set(o["alert_id"] for o in objs.values() if o.get("alert_id") is not None))
            confs = dict((r["alert_id"], r) for r in _by_ids(client, cent, ccols, dict(ccols)["alert_id"], aids)) if aids else {}
        except _swis_errors() as e:
            if not_read is not None:
                not_read.append({"what": "the alerts' names (Orion.AlertConfigurations)", "why": str(e)})
    rows = []
    for a in act:
        ob = objs.get(a.get("object_id")) or {}
        cf = confs.get(ob.get("alert_id")) or {}
        node = _int(ob.get("node_id"))
        if node not in out or _int(a.get("alert_active_id")) is None:
            continue
        rows.append((stamp(a.get("triggered_at")), _int(a.get("alert_active_id")),
                     {"node_id": node, "name": _cut(cf.get("name"), 512), "severity": _int(cf.get("severity")),
                      "triggered_at": stamp(a.get("triggered_at")), "message": _cut(a.get("message"), 1000),
                      "entity_type": _cut(ob.get("entity_type"), 255), "entity_caption": _cut(ob.get("entity_caption"), 512),
                      "acknowledged": _flag(a.get("acknowledged"))}))
    old = datetime.datetime(1, 1, 1)
    for _t, _i, r in sorted(rows, key=lambda x: (x[0] or old, x[1]), reverse=True):
        out[r["node_id"]].append(r)
    return out


def interfaces_for(conn, snap_id, node_ids):
    """The interfaces the snapshot has on these nodes: {node_id: [{interface_id, name, caption, status, status_code, speed,
    in_pct, out_pct, errors, discards}]} by name. errors and discards: in + out in the last hour (None when not read).
    The snapshot keeps only the interfaces on NPM's links (the network map's), so a server's network card is often not
    there: []. One query."""
    ids = _ids_of(node_ids)
    out = dict((i, []) for i in ids)
    if not ids:
        return out
    cur = conn.cursor()
    cur.execute("SELECT interface_id, node_id, name, caption, status, speed, in_pct, out_pct, in_errors_hour, out_errors_hour, "
                "in_discards_hour, out_discards_hour FROM sw_interface WHERE snap_id = %%s AND node_id IN (%s) "
                "ORDER BY name, interface_id" % _ph(len(ids)), [snap_id] + ids)

    def both(a, b):
        return None if a is None and b is None else int(a or 0) + int(b or 0)
    for r in _dicts(cur):
        out.setdefault(int(r["node_id"]), []).append(
            {"interface_id": int(r["interface_id"]), "name": r["name"] or "", "caption": r["caption"] or "",
             "status": site_swis.status_of(r["status"]), "status_code": r["status"], "speed": _num_back(r["speed"]),
             "in_pct": _num_back(r["in_pct"]), "out_pct": _num_back(r["out_pct"]),
             "errors": both(r["in_errors_hour"], r["out_errors_hour"]), "discards": both(r["in_discards_hour"], r["out_discards_hour"])})
    for v in out.values():
        v.sort(key=lambda f: (f["name"], f["interface_id"]))
    return out


def part_note(snap, part):
    """What latest() says of a part this snapshot did not read (e.g. "app": no SAM on this Orion): {what, why, absent} or
    None when it was read."""
    words = PART_WORDS.get(part)
    for x in (snap or {}).get("not_read") or []:
        if words and x.get("what") == words:
            return x
    return None


def events_between(conn, start, end, node_ids=None, limit=2000, orion=None):
    """SolarWinds' events in [start, end] (naive UTC datetimes), oldest first, of these nodes (None = all) - of one Orion:
    orion (orion_key), else the newest whole snapshot's (1.96.1, review P1-2: never two Orions' events mixed). Its
    events from before its EventIDs started again too (1.96.2: kept under orion#N)."""
    cur = conn.cursor()
    if orion is None:
        cur.execute("SELECT orion FROM sw_snapshot WHERE ok = 1 ORDER BY snap_id DESC LIMIT 1")
        r = cur.fetchone()
        if not r or not r[0]:
            return []
        orion = str(r[0])
    like = _epoch_like(orion)
    sql = ("SELECT event_id, event_time, node_id, net_object_type, net_object_id, event_type, type_name, message FROM sw_event "
           "WHERE event_time >= %s AND event_time <= %s AND (orion = %s OR orion LIKE %s)")
    args = [start, end, orion, like]
    ids = sorted(set(int(i) for i in node_ids or [] if i is not None)) if node_ids is not None else None
    if ids is not None:
        if not ids:
            return []
        sql += " AND node_id IN (%s)" % _ph(len(ids))
        args += ids
    cur.execute(sql + " ORDER BY event_time, event_id LIMIT %d" % max(1, int(limit)), args)
    return _dicts(cur)


# ------------------------------------------------- 1.97.0: the health checks', troubleshoot's and the alarms' readers
def node_details(conn, snap_id, node_ids):
    """What a page says of a node besides nodes_for(): {node_id: {vendor, model, os_version, kind, location, last_boot}}.
    One query."""
    ids = _ids_of(node_ids)
    out = {}
    if not ids:
        return out
    cur = conn.cursor()
    cur.execute("SELECT node_id, vendor, model, os_version, kind, location, last_boot FROM sw_node WHERE snap_id = %%s AND node_id IN (%s)"
                % _ph(len(ids)), [snap_id] + ids)
    for r in _dicts(cur):
        out[int(r["node_id"])] = dict((k, r[k]) for k in ("vendor", "model", "os_version", "kind", "location", "last_boot"))
    return out


def volumes_for(conn, snap_id, node_ids):
    """The volumes on these nodes, fullest first: {node_id: [{volume_id, caption, kind, size_bytes, used_bytes, used_pct,
    status}]}. A node without one, or an Orion that does not read volumes (part_note(snap, "volume")): []. One query."""
    ids = _ids_of(node_ids)
    out = dict((i, []) for i in ids)
    if not ids:
        return out
    cur = conn.cursor()
    cur.execute("SELECT volume_id, node_id, caption, kind, size_bytes, used_bytes, used_pct, status FROM sw_volume "
                "WHERE snap_id = %%s AND node_id IN (%s) ORDER BY used_pct DESC, volume_id" % _ph(len(ids)), [snap_id] + ids)
    for r in _dicts(cur):
        out.setdefault(int(r["node_id"]), []).append(
            {"volume_id": int(r["volume_id"]), "caption": r["caption"] or "", "kind": r["kind"] or "",
             "size_bytes": _num_back(r["size_bytes"]), "used_bytes": _num_back(r["used_bytes"]),
             "used_pct": _num_back(r["used_pct"]), "status": site_swis.status_of(r["status"]) if r["status"] is not None else ""})
    return out


def ports_for(conn, snap_id, node_ids):
    """The switch ports SolarWinds' topology links to these nodes (the other end of each link): {node_id: [{switch,
    switch_id, interface_id, port, status, status_code, speed, in_pct, out_pct, errors, discards}]}, by switch and port.
    errors and discards: in + out in the last hour (None when not read). A node no link reaches: []. Three queries."""
    ids = _ids_of(node_ids)
    out = dict((i, []) for i in ids)
    if not ids:
        return out
    cur = conn.cursor()
    cur.execute("SELECT src_node, src_if, dst_node, dst_if FROM sw_link WHERE snap_id = %%s AND (src_node IN (%s) OR dst_node IN (%s))"
                % (_ph(len(ids)), _ph(len(ids))), [snap_id] + ids + ids)
    ends = {}
    for r in _dicts(cur):
        for me, other, oif in ((r["src_node"], r["dst_node"], r["dst_if"]), (r["dst_node"], r["src_node"], r["src_if"])):
            # 1.97.0 (review P2-4): a link between two of the nodes asked is kept (each sees the other's end)
            if me is not None and int(me) in out and other is not None and int(other) != int(me) and oif is not None:
                ends.setdefault(int(me), set()).add((int(other), int(oif)))
    if not ends:
        return out
    ifs = sorted(set(i for v in ends.values() for _, i in v))
    sws = sorted(set(n for v in ends.values() for n, _ in v))
    cur.execute("SELECT interface_id, node_id, name, caption, status, speed, in_pct, out_pct, in_errors_hour, out_errors_hour, "
                "in_discards_hour, out_discards_hour FROM sw_interface WHERE snap_id = %%s AND interface_id IN (%s)" % _ph(len(ifs)),
                [snap_id] + ifs)
    by_if = dict((int(r["interface_id"]), r) for r in _dicts(cur))
    cur.execute("SELECT node_id, name FROM sw_node WHERE snap_id = %%s AND node_id IN (%s)" % _ph(len(sws)), [snap_id] + sws)
    names = dict((int(r["node_id"]), r["name"] or "") for r in _dicts(cur))

    def both(a, b):
        return None if a is None and b is None else int(a or 0) + int(b or 0)
    for me, v in ends.items():
        for sw_id, iid in v:
            f = by_if.get(iid) or {}
            out[me].append({"switch": names.get(sw_id, ""), "switch_id": sw_id, "interface_id": iid,
                            "port": f.get("caption") or f.get("name") or "", "status": site_swis.status_of(f["status"]) if f.get("status") is not None else "",
                            "status_code": f.get("status"), "speed": _num_back(f.get("speed")), "in_pct": _num_back(f.get("in_pct")),
                            "out_pct": _num_back(f.get("out_pct")), "errors": both(f.get("in_errors_hour"), f.get("out_errors_hour")),
                            "discards": both(f.get("in_discards_hour"), f.get("out_discards_hour"))})
        out[me].sort(key=lambda p: (p["switch"], p["port"], p["interface_id"]))
    return out


def same_center(job, snap, centers):
    """1.113.0: is the snapshot's vCenter name snap the job's vCenter job? Names compared after canon(). An FQDN against
    a short name only when that short name fits exactly one of the snapshot's vCenters (centers), and that one is the
    other name. Anything doubtful: False."""
    a, b = canon(job), canon(snap)
    if not a or not b:
        return False
    if a == b:
        return True
    if normalized_ip(a) or normalized_ip(b) or ("." in a) == ("." in b):
        return False
    short = a if "." not in a else b
    fits = set(c for c in (canon(x) for x in centers or []) if c.split(".", 1)[0] == short)
    return fits == {b}


def vim_for(conn, snap_id, names):
    """VIM's hosts and VMs: {key: {kind (host | vm), name, status, status_code, connection_state, power_state, cluster,
    node_id, vcenter, why}} or None. names: [{name, kind, vcenter}], key "kind|vcenter|name" (vim_key). Only the full name
    (case and white space aside; review 1970c P1-2), only an object of the kind asked (a host among VIM's hosts), only
    in the job's vCenter (1.113.0, same_center). node_id is None with why when the snapshot cannot say: tables of an older
    release, or a VIM object without its vCenter (a snapshot before 1.113.0) - never a guess. Three queries."""
    objs = [(str(n.get("name") or ""), str(n.get("kind") or ""), str(n.get("vcenter") or "")) for n in names or []]
    out = dict((vim_key(k, v, n), None) for n, k, v in objs)
    keys = sorted(set(name_key(n) for n, _k, _v in objs if name_key(n)))
    if not keys:
        return out
    if missing_columns(conn):
        for n, k, v in objs:
            out[vim_key(k, v, n)] = {"kind": k, "name": n, "node_id": None, "vcenter": "",
                                     "why": "VIM not verifiable: the report database's tables are of an older release (the next snapshot run brings them up to date)"}
        return out
    cur = conn.cursor()
    rows = []
    cur.execute("SELECT host_id AS vim_id, name, name_key, status, connection_state, power_state, cluster, node_id, vcenter FROM sw_vim_host "
                "WHERE snap_id = %%s AND name_key IN (%s) ORDER BY host_id" % _ph(len(keys)), [snap_id] + keys)
    rows += [dict(r, kind="host") for r in _dicts(cur)]
    cur.execute("SELECT v.vm_id AS vim_id, v.name, v.name_key, v.status, NULL AS connection_state, v.power_state, NULL AS cluster, "
                "v.node_id, h.vcenter FROM sw_vim_vm v LEFT JOIN sw_vim_host h ON h.snap_id = v.snap_id AND h.host_id = v.host_id "
                "WHERE v.snap_id = %%s AND v.name_key IN (%s) ORDER BY v.vm_id" % _ph(len(keys)), [snap_id] + keys)
    rows += [dict(r, kind="vm") for r in _dicts(cur)]
    cur.execute("SELECT DISTINCT vcenter FROM sw_vim_host WHERE snap_id = %s AND vcenter IS NOT NULL", [snap_id])
    centers = [r[0] for r in cur.fetchall()]
    for n, kind, vc in objs:
        same = [r for r in rows if r["kind"] == kind and canon(r["name"]) == canon(n)]
        known = [r for r in same if canon(r.get("vcenter"))]
        mine = [r for r in known if same_center(vc, r["vcenter"], centers)]
        if len(mine) > 1 or (not mine and not (same and not known)):
            continue                     # none, or two in the job's vCenter: no VIM object, never a guess
        if not mine:
            out[vim_key(kind, vc, n)] = {"kind": kind, "name": n, "node_id": None, "vcenter": "",
                                         "why": "VIM not verifiable: the snapshot has no vCenter for it (the next snapshot run adds it)"}
            continue
        r = mine[0]
        out[vim_key(kind, vc, n)] = {"kind": r["kind"], "name": r["name"] or "",
                                     "status": site_swis.status_of(r["status"]) if r["status"] is not None else "",
                                     "status_code": r["status"], "connection_state": r["connection_state"] or "",
                                     "power_state": r["power_state"] or "", "cluster": r["cluster"] or "", "node_id": _int(r["node_id"]),
                                     "vcenter": r["vcenter"] or "", "why": ""}
    # the other kind under the same name (a host and a VM both esx01): a node of that name is not this object's (review 1970c P1-5)
    for n, kind, vc in objs:
        e = out.get(vim_key(kind, vc, n))
        other = any(r["kind"] != kind and canon(r["name"]) == canon(n) for r in rows)
        if e is None and other:
            out[vim_key(kind, vc, n)] = {"kind": kind, "name": n, "node_id": None, "vcenter": "", "other_kind": True, "why": ""}
        elif e is not None:
            e["other_kind"] = other
    return out


def vim_key(kind, vcenter, name):
    return "%s|%s|%s" % (kind, canon(vcenter), canon(name))


def events_for(conn, start, end, node_ids, ports=(), limit=2000, orion=None):
    """1.97.0 (review P2-3, P2-4): SolarWinds' events in [start, end], NEWEST first: the nodes' own, and of ports
    ((switch node_id, interface_id), ...) only that interface's events of that switch - chosen in SQL, so another
    port's events never use up the limit. Of one Orion (as events_between). Each row has port: [switch, iface] or None."""
    cur = conn.cursor()
    if orion is None:
        cur.execute("SELECT orion FROM sw_snapshot WHERE ok = 1 ORDER BY snap_id DESC LIMIT 1")
        r = cur.fetchone()
        if not r or not r[0]:
            return []
        orion = str(r[0])
    ids = sorted(set(int(i) for i in node_ids or [] if i is not None))
    ps = sorted(set((int(a), int(b)) for a, b in ports or () if a is not None and b is not None))
    if not ids and not ps:
        return []
    cond, args = [], [start, end, orion, _epoch_like(orion)]
    if ids:
        cond.append("node_id IN (%s)" % _ph(len(ids)))
        args += ids
    for a, b in ps:
        cond.append("(node_id = %s AND net_object_type = 'I' AND net_object_id = %s)")
        args += [a, b]
    cur.execute("SELECT event_id, event_time, node_id, net_object_type, net_object_id, event_type, type_name, message FROM sw_event "
                "WHERE event_time >= %%s AND event_time <= %%s AND (orion = %%s OR orion LIKE %%s) AND (%s) "
                "ORDER BY event_time DESC, event_id DESC LIMIT %d" % (" OR ".join(cond), max(1, int(limit))), args)
    out = []
    for r in _dicts(cur):
        n = _int(r.get("node_id"))
        on_port = str(r.get("net_object_type") or "").upper() == "I" and (n, _int(r.get("net_object_id"))) in ps
        r["port"] = [n, _int(r.get("net_object_id"))] if on_port and n not in ids else None
        out.append(r)
    return out
