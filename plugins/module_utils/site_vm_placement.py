# -*- coding: utf-8 -*-
"""1.116.6: the VM placement history in the report database (docs/REPORT_DATABASE.md, "The VM placement history"):
which VM was on which host, cluster and datastores, and its power state, over time. Written by "VMware - VM placement
snapshot" (site_vm_placement_snapshot), read by the outage review (site_vm_placement_read) for the VMs on a lost datastore
or host when the outage began.

A run keeps only what changed since the run before, and one whole snapshot a day (UTC), so the table stays small:
    vm_placement_run   one row per run and vCenter: when, whole (1) or changes (0), how many VMs, how many rows
    vm_placement       one row per VM in a whole snapshot (kind F), and per change (C) or VM gone (G) after it
The placement as of a time is the newest whole snapshot at or before it, with the changes after it up to that time.
Pure functions over a DB-API cursor, so tests/test_vm_placement.py proves them with a fake one."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import json
import re

COMPONENT = "vm_placement"
SCHEMA_VERSION = 1
TABLE_OPTS = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"
CREATE = [
    "CREATE TABLE IF NOT EXISTS vm_placement_run (vcenter VARCHAR(255) NOT NULL, at DATETIME NOT NULL, whole TINYINT NOT NULL, "
    "vms INT NOT NULL, kept INT NOT NULL, job BIGINT NOT NULL DEFAULT 0, PRIMARY KEY (vcenter, at)) " + TABLE_OPTS,
    "CREATE TABLE IF NOT EXISTS vm_placement (vcenter VARCHAR(255) NOT NULL, at DATETIME NOT NULL, vm_moid VARCHAR(64) NOT NULL, "
    "kind CHAR(1) NOT NULL, name VARCHAR(255) NULL, host VARCHAR(255) NULL, host_moid VARCHAR(64) NULL, cluster VARCHAR(255) NULL, "
    "datastores TEXT NULL, datastore_moids TEXT NULL, power VARCHAR(32) NULL, PRIMARY KEY (vcenter, vm_moid, at), KEY vm_placement_at (vcenter, at)) " + TABLE_OPTS,
]
TABLES = ("vm_placement_run", "vm_placement")
FIELDS = ("name", "host", "host_moid", "cluster", "datastores", "datastore_moids", "power")


SCHEMA_VERSION_TABLE = ("CREATE TABLE IF NOT EXISTS schema_version (component VARCHAR(64) NOT NULL PRIMARY KEY, version INT NOT NULL, "
                        "applied_at DATETIME NOT NULL) " + TABLE_OPTS)


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
    """The statements the first run sends, for making the tables by hand (vm_placement_db_create: false). The doc carries
    this text; tests/test_vm_placement.py keeps them alike."""
    return "\n\n".join([pretty(SCHEMA_VERSION_TABLE)] + [pretty(sql) for sql in CREATE] + [
        "INSERT INTO schema_version (component, version, applied_at) VALUES ('%s', %d, UTC_TIMESTAMP());" % (COMPONENT, SCHEMA_VERSION)])


def stamp(text):
    """'2026-11-23T06:15:58Z' (or a datetime) -> a naive UTC datetime, or None."""
    if isinstance(text, datetime.datetime):
        return text.replace(tzinfo=None) if text.tzinfo is None else text.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)", str(text or ""))
    return datetime.datetime(*[int(x) for x in m.groups()]) if m else None


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ") if t else ""


def _short(name):
    name = str(name or "")
    return name if re.match(r"^\d+(\.\d+){3}$", name) else name.split(".")[0]


def norm(vm):
    """A VM read from vCenter -> the fields kept: the datastores' names and their IDs (MoIDs) as two JSON lists in the same
    order, and the host's ID, so a name two hosts or datastores share is told apart (1.116.6 review)."""
    ids = list(vm.get("datastore_moids") or [])
    pairs = sorted({(str(d), str(ids[i]) if i < len(ids) else "") for i, d in enumerate(vm.get("datastores") or []) if d})
    return {"name": str(vm.get("name") or "")[:255], "host": str(vm.get("host") or "")[:255],
            "host_moid": str(vm.get("host_moid") or "")[:64], "cluster": str(vm.get("cluster") or "")[:255],
            "datastores": json.dumps([n for n, _ in pairs]), "datastore_moids": json.dumps([m for _, m in pairs]),
            "power": str(vm.get("power") or "")[:32]}


def plan(previous, now_vms, full):
    """The rows a run writes: every VM (F) when full, else the VMs new or changed (C) and those gone (G)."""
    rows = []
    for moid, vm in sorted(now_vms.items()):
        if full or previous.get(moid) != vm:
            rows.append(("F" if full else "C", moid, vm))
    if not full:
        rows.extend(("G", moid, dict.fromkeys(FIELDS, None)) for moid in sorted(set(previous) - set(now_vms)))
    return rows


def schema(cur, create):
    """(there, created): both tables, made when not there and create is true; schema_version's row with them."""
    there = []
    for t in TABLES:
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = %s", (t,))
        there.append(cur.fetchone() is not None)
    if all(there):
        cur.execute("SELECT version FROM schema_version WHERE component = %s", (COMPONENT,))
        r = cur.fetchone()
        if r is not None and int(r[0]) > SCHEMA_VERSION:
            raise RuntimeError("the table vm_placement is version %s, newer than this release knows (%s) - update site-automation"
                               % (r[0], SCHEMA_VERSION))
        return True, False
    if not create:
        return False, False
    cur.execute(SCHEMA_VERSION_TABLE)
    for sql in CREATE:
        cur.execute(sql)
    cur.execute("INSERT INTO schema_version (component, version, applied_at) VALUES (%s, %s, UTC_TIMESTAMP()) "
                "ON DUPLICATE KEY UPDATE version = VALUES(version)", (COMPONENT, SCHEMA_VERSION))
    return True, True


def state_as_of(cur, vcenter, t):
    """(as_of, {moid: fields}) of a vCenter at time t: the newest whole snapshot at or before t with the changes after it.
    as_of is the last run at or before t; (None, {}) when no whole snapshot is that old."""
    cur.execute("SELECT MAX(at) FROM vm_placement_run WHERE vcenter = %s AND whole = 1 AND at <= %s", (vcenter, t))
    r = cur.fetchone()
    base = r[0] if r else None
    if base is None:
        return None, {}
    cur.execute("SELECT MAX(at) FROM vm_placement_run WHERE vcenter = %s AND at <= %s", (vcenter, t))
    r = cur.fetchone()
    as_of = r[0] if r and r[0] else base
    cur.execute("SELECT at, vm_moid, kind, name, host, host_moid, cluster, datastores, datastore_moids, power FROM vm_placement "
                "WHERE vcenter = %s AND at >= %s AND at <= %s ORDER BY at, vm_moid", (vcenter, base, t))
    state = {}
    for at, moid, kind, name, host, host_moid, cluster, stores, store_ids, power in cur.fetchall():
        if kind == "F" and at != base:
            continue
        if kind == "G":
            state.pop(moid, None)
        else:
            state[moid] = {"name": name or "", "host": host or "", "host_moid": host_moid or "", "cluster": cluster or "",
                           "datastores": stores or "[]", "datastore_moids": store_ids or "[]", "power": power or ""}
    return as_of, state


def need_full(cur, vcenter, now):
    """True when this vCenter has no whole snapshot yet on now's (UTC) day."""
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cur.execute("SELECT COUNT(*) FROM vm_placement_run WHERE vcenter = %s AND whole = 1 AND at >= %s AND at <= %s",
                (vcenter, day, now))
    r = cur.fetchone()
    return not (r and int(r[0] or 0))


def write(cur, vcenter, now, vms, job=0):
    """Write one vCenter's run -> (full, rows written). vms: [{moid, name, host, cluster, datastores, power}]."""
    now_vms = {str(v["moid"]): norm(v) for v in vms if v.get("moid")}
    full = need_full(cur, vcenter, now)
    previous = {} if full else state_as_of(cur, vcenter, now)[1]
    rows = plan(previous, now_vms, full)
    cur.executemany("INSERT INTO vm_placement (vcenter, at, vm_moid, kind, name, host, host_moid, cluster, datastores, "
                    "datastore_moids, power) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [(vcenter, now, moid, kind) + tuple(vm[f] for f in FIELDS) for kind, moid, vm in rows])
    cur.execute("INSERT INTO vm_placement_run (vcenter, at, whole, vms, kept, job) VALUES (%s, %s, %s, %s, %s, %s)",
                (vcenter, now, 1 if full else 0, len(now_vms), len(rows), int(job or 0)))
    return full, len(rows)


def prune(cur, now, days):
    """Delete what is older than days, from the oldest whole snapshot still inside them (so every kept time can be told)."""
    if days <= 0:
        return 0
    cutoff = now - datetime.timedelta(days=days)
    cur.execute("SELECT vcenter, MIN(at) FROM vm_placement_run WHERE whole = 1 AND at >= %s GROUP BY vcenter", (cutoff,))
    gone = 0
    for vcenter, keep_from in cur.fetchall():
        for table in TABLES:
            gone += int(cur.execute("DELETE FROM %s WHERE vcenter = %%s AND at < %%s" % table, (vcenter, keep_from)) or 0)
    return gone


def _on(state, kind, name, moid):
    """The VMs of a state on one host or datastore -> (vms, ambiguous). By its ID when given; else by its name (a host also
    by its short name), only when that name means one object in this vCenter."""
    vms, ids = [], set()
    for vm_moid, f in state.items():
        stores, store_ids = json.loads(f["datastores"] or "[]"), json.loads(f.get("datastore_moids") or "[]")
        if kind == "datastore":
            pairs = list(zip(stores, store_ids + [""] * (len(stores) - len(store_ids))))
            hit = [m for n, m in pairs if (m == moid if moid else n == name)]
        else:
            same = f["host"] == name or _short(f["host"]) == _short(name)
            hit = [f.get("host_moid", "")] if (f.get("host_moid") == moid if moid else same) else []
        if hit:
            ids.update(hit)
            vms.append({"name": f["name"], "moid": vm_moid, "host": f["host"], "cluster": f["cluster"], "power": f["power"],
                        "datastores": stores})
    ambiguous = not moid and len(ids - {""}) > 1
    return ([] if ambiguous else sorted(vms, key=lambda v: (v["name"], v["moid"]))), ambiguous


def lookup(cur, items):
    """For each {vcenter, kind: datastore | host, name, moid, at}: the VMs on it as of at -> the item with as_of ('' = no
    snapshot that old, or a name that means two objects) and vms [{name, moid, host, cluster, power, datastores}]."""
    cache, out = {}, []
    for item in items or []:
        t = stamp(item.get("at"))
        vcenter, kind, name = str(item.get("vcenter") or ""), item.get("kind"), str(item.get("name") or "")
        if t is None or kind not in ("datastore", "host"):
            out.append(dict(item, as_of="", vms=[], source="snapshot"))
            continue
        if (vcenter, t) not in cache:
            cache[vcenter, t] = state_as_of(cur, vcenter, t)
        as_of, state = cache[vcenter, t]
        vms, ambiguous = _on(state, kind, name, str(item.get("moid") or ""))
        out.append(dict(item, as_of="" if ambiguous else iso(as_of), vms=vms, source="snapshot", ambiguous=ambiguous))
    return out
