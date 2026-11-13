#!/usr/bin/python
# -*- coding: utf-8 -*-
"""The capacity trends report's points in the report database (MySQL; the "Report database" credential,
docs/REPORT_DATABASE.md): a longer history than AAP keeps its jobs. One call a run (roles/ontap_report/tasks/capacity.yml):
makes the table ontap_capacity_point when it is not there (ontap_capacity_db_create), reads the earlier runs' points of
the window back, writes this run's (a row per cluster, aggregate and volume), and deletes the rows older than
ontap_capacity_db_keep_days. Never fails the job: a database that is down, a login refused or a grant missing is one
warning, and the report goes on with AAP's job history. Needs PyMySQL in the execution environment."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_ontap_capacity_db
short_description: Keep and read back the NetApp ONTAP capacity trends report's points in the report database
description:
  - Reads the points read between this run and days before it, as runs (oldest first) in the layout of the job artifact
    ontap_capacity; when there are more runs than max_runs, evenly spread ones of them (the newest always).
  - Writes this run's rows (rows; read_at, job) - not in check mode, and not when write is false.
  - Deletes rows older than keep_days (0 = never).
  - The table is made on the first run (create); with create false and no table, nothing is read or written and error
    says so. docs/REPORT_DATABASE.md has the statement for making it by hand.
options:
  host: {description: The database server., type: str, required: true}
  port: {description: Its port., type: int, default: 3309}
  database: {description: The database., type: str, required: true}
  user: {description: The account., type: str, required: true}
  password: {description: Its password., type: str, required: true}
  tls: {description: Use TLS and check the server's certificate., type: bool, default: false}
  ca_file: {description: The CA file the certificate is checked with., type: str, default: ''}
  connect_timeout: {description: Seconds to wait for the connection., type: int, default: 10}
  create: {description: Make the table when it is not there., type: bool, default: true}
  write: {description: Write this run's rows., type: bool, default: true}
  read_at: {description: "When this run read the clusters (2026-11-13T06:00:05Z).", type: str, required: true}
  job: {description: This job's ID (0 outside AAP)., type: int, default: 0}
  rows: {description: "This run's rows: [cluster, cluster_name, kind, uuid, name, svm, size_b, used_b, reserve_pct, snap_used_b] each (the filter ontap_capacity_db_rows).", type: list, elements: list, default: []}
  days: {description: How far back to read (ontap_capacity_days)., type: float, default: 30}
  max_runs: {description: Read at most this many runs (ontap_capacity_runs_max)., type: int, default: 400}
  keep_days: {description: Delete rows older than this many days (0 = never)., type: int, default: 800}
author: site automation
'''

RETURN = r'''
ok: {description: The database answered and the table is there., type: bool}
error: {description: Why not, when ok is false., type: str}
runs: {description: The earlier runs' points, oldest first (the layout of the artifact ontap_capacity, each with its job)., type: list}
jobs: {description: The job IDs of those runs (AAP's history need not read them again)., type: list}
written: {description: Rows written this run., type: int}
pruned: {description: Rows deleted (older than keep_days)., type: int}
held: {description: "What the table holds after this run: {rows, runs, oldest, newest}.", type: dict}
created: {description: The table was made this run., type: bool}
'''

import datetime
import re

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils import site_report_db as db

SCHEMA_VERSION = 1
COMPONENT = "ontap_capacity"
TABLE = "ontap_capacity_point"
KINDS = ("cluster", "aggregate", "volume")
CREATE = (
    "CREATE TABLE IF NOT EXISTS ontap_capacity_point (read_at DATETIME NOT NULL, job BIGINT NOT NULL DEFAULT 0, "
    "cluster VARCHAR(64) NOT NULL, cluster_name VARCHAR(255) NOT NULL DEFAULT '', kind VARCHAR(16) NOT NULL, "
    "uuid VARCHAR(64) NOT NULL, name VARCHAR(255) NOT NULL DEFAULT '', svm VARCHAR(255) NOT NULL DEFAULT '', "
    "size_b BIGINT UNSIGNED NOT NULL, used_b BIGINT UNSIGNED NOT NULL, reserve_pct DECIMAL(6,2) NULL, "
    "snap_used_b BIGINT UNSIGNED NULL, PRIMARY KEY (cluster, kind, uuid, read_at), "
    "KEY ontap_capacity_point_read_at (read_at)) " + db.TABLE_OPTS)
INSERT = ("INSERT INTO ontap_capacity_point (read_at, job, cluster, cluster_name, kind, uuid, name, svm, size_b, used_b, reserve_pct, "
          "snap_used_b) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE job = VALUES(job), "
          "cluster_name = VALUES(cluster_name), name = VALUES(name), svm = VALUES(svm), size_b = VALUES(size_b), "
          "used_b = VALUES(used_b), reserve_pct = VALUES(reserve_pct), snap_used_b = VALUES(snap_used_b)")
CHUNK = 500


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
    """The statements the first run sends, for making the table by hand (ontap_capacity_db_create: false). The doc
    carries this text; a test keeps them alike."""
    return "\n\n".join([pretty(db.SCHEMA_VERSION_TABLE), pretty(CREATE),
                        "INSERT INTO schema_version (component, version, applied_at) VALUES ('%s', %d, UTC_TIMESTAMP());"
                        % (COMPONENT, SCHEMA_VERSION)])


def stamp(text):
    """'2026-11-13T06:00:05Z' -> a datetime (UTC, naive: what the DATETIME column holds), or None."""
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)", str(text or ""))
    return datetime.datetime(*[int(x) for x in m.groups()]) if m else None


def schema(conn, create):
    """(there, created): the table, made when it is not there and create is true; schema_version's row with it."""
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = %s", (TABLE,))
    there = cur.fetchone() is not None
    if not there:
        if not create:
            return False, False
        cur.execute(db.SCHEMA_VERSION_TABLE)
        cur.execute(CREATE)
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'schema_version'")
    if cur.fetchone() is not None:
        cur.execute("SELECT version FROM schema_version WHERE component = %s", (COMPONENT,))
        r = cur.fetchone()
        if r is None and create:
            cur.execute("INSERT INTO schema_version (component, version, applied_at) VALUES (%s, %s, UTC_TIMESTAMP())", (COMPONENT, SCHEMA_VERSION))
        elif r is not None and int(r[0]) > SCHEMA_VERSION:
            raise RuntimeError("the table %s is version %s, newer than this release knows (%s) - update site-automation" % (TABLE, r[0], SCHEMA_VERSION))
    conn.commit()
    return True, not there


def read(conn, now, days, max_runs):
    """The runs read in the days before now (not now's own), oldest first, as artifact-shaped points."""
    cur = conn.cursor()
    since = now - datetime.timedelta(days=days, hours=2)    # a little more: the report's filter draws the line (its SLACK)
    cur.execute("SELECT DISTINCT read_at FROM ontap_capacity_point WHERE read_at >= %s AND read_at < %s ORDER BY read_at", (since, now))
    times = [r[0] for r in cur.fetchall()]
    if len(times) > max_runs:                               # evenly spread, the newest always
        step = len(times) / float(max_runs)
        times = sorted(set(times[len(times) - 1 - int(i * step)] for i in range(max_runs)))
    runs = []
    for k in range(0, len(times), 50):
        part = times[k:k + 50]
        cur.execute("SELECT read_at, job, cluster, cluster_name, kind, uuid, size_b, used_b, reserve_pct, snap_used_b FROM ontap_capacity_point "
                    "WHERE read_at IN (%s) ORDER BY read_at, cluster, kind, uuid" % ", ".join(["%s"] * len(part)), part)
        run, clusters, at = None, {}, None
        for read_at, job, cluster, cname, kind, uuid, size_b, used_b, reserve, snap in cur.fetchall():
            if read_at != at:
                at, clusters = read_at, {}
                run = {"v": 1, "read_at": read_at.strftime("%Y-%m-%dT%H:%M:%SZ"), "job": int(job or 0), "clusters": []}
                runs.append(run)
            c = clusters.get(cluster)
            if c is None:
                c = clusters[cluster] = {"uuid": str(cluster), "name": str(cname)}
                run["clusters"].append(c)
            if kind == "cluster":
                c.update(size_b=int(size_b), used_b=int(used_b))
            elif kind == "aggregate":
                c.setdefault("aggregates", []).append({"uuid": str(uuid), "size_b": int(size_b), "used_b": int(used_b)})
            elif kind == "volume":
                c.setdefault("volumes", []).append({"uuid": str(uuid), "size_b": int(size_b), "used_b": int(used_b)})
                c.setdefault("snapshot_reserve", [])
                if reserve is not None and snap is not None:
                    c["snapshot_reserve"].append({"uuid": str(uuid), "reserve_pct": float(reserve), "snap_used_b": int(snap)})
    return runs


def write(conn, now, job, rows):
    cur = conn.cursor()
    good = []
    for r in rows:
        if len(r) != 10 or r[2] not in KINDS:
            continue
        good.append((now, job, str(r[0])[:64], str(r[1])[:255], r[2], str(r[3])[:64], str(r[4])[:255], str(r[5])[:255],
                     int(r[6]), int(r[7]), None if r[8] is None else round(min(float(r[8]), 9999.99), 2), None if r[9] is None else int(r[9])))
    for k in range(0, len(good), CHUNK):
        cur.executemany(INSERT, good[k:k + CHUNK])
    return len(good)


def held(conn):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), COUNT(DISTINCT read_at), MIN(read_at), MAX(read_at) FROM ontap_capacity_point")
    n, runs, lo, hi = cur.fetchone()
    return {"rows": int(n or 0), "runs": int(runs or 0), "oldest": lo.strftime("%Y-%m-%dT%H:%M:%SZ") if lo else "",
            "newest": hi.strftime("%Y-%m-%dT%H:%M:%SZ") if hi else ""}


def shut(conn):
    try:
        if conn is not None:
            conn.close()
    except Exception:           # noqa: BLE001
        pass


def main():
    module = AnsibleModule(
        argument_spec=dict(db.ARGS, create=dict(type="bool", default=True), write=dict(type="bool", default=True),
                           read_at=dict(type="str", required=True), job=dict(type="int", default=0),
                           rows=dict(type="list", elements="list", default=[]), days=dict(type="float", default=30),
                           max_runs=dict(type="int", default=400), keep_days=dict(type="int", default=800)),
        supports_check_mode=True)
    p = module.params
    where = "%s:%s/%s" % (p["host"], p["port"], p["database"])
    res = {"changed": False, "ok": False, "error": "", "tls": "", "where": where, "runs": [], "jobs": [], "written": 0, "pruned": 0,
           "held": {}, "created": False}

    def give_up(msg):
        res["error"] = msg
        module.warn("The report database %s: %s - the capacity trends report goes on with AAP's job history "
                    "(ontap_capacity_db: false turns the database off for it)" % (where, msg))
        module.exit_json(**res)

    if not db.HAS_PYMYSQL:
        give_up(db.NO_PYMYSQL)
    now = stamp(p["read_at"])
    if now is None:
        give_up("read_at is not a time: %r" % p["read_at"])
    conn = None
    try:
        conn, res["tls"] = db.connect(p)
        there, res["created"] = schema(conn, p["create"] and not module.check_mode)
        if not there:
            conn.close()
            conn = None
            give_up("the table %s is not there%s - make it once, docs/REPORT_DATABASE.md" % (
                TABLE, " (check mode: nothing made)" if module.check_mode and p["create"] else " (ontap_capacity_db_create: false)"))
        res["runs"] = read(conn, now, p["days"], max(1, p["max_runs"]))
        res["jobs"] = sorted(set(r["job"] for r in res["runs"] if r["job"]))
        res["ok"] = True
    except Exception as e:      # noqa: BLE001 - unreachable, refused, a grant missing: one line, the job goes on
        res["runs"], res["jobs"] = [], []
        shut(conn)
        give_up(db.explain(e, p))
    try:                        # the earlier points are read: a write that fails loses this run's point only
        if p["write"] and not module.check_mode:
            res["written"] = write(conn, now, p["job"], p["rows"] or [])
            if p["keep_days"] > 0:
                cur = conn.cursor()
                res["pruned"] = int(cur.execute("DELETE FROM ontap_capacity_point WHERE read_at < %s",
                                                (now - datetime.timedelta(days=p["keep_days"]),)) or 0)
            conn.commit()
        res["held"] = held(conn)
        res["changed"] = bool(res["written"] or res["pruned"] or res["created"])
    except Exception as e:      # noqa: BLE001 - a read-only account, a full disk: said, the report has the earlier points
        try:
            conn.rollback()
        except Exception:       # noqa: BLE001
            pass
        res["written"], res["pruned"] = 0, 0
        res["error"] = "this run's point was not kept: %s" % db.explain(e, p)
        module.warn("The report database %s: %s - the capacity trends report has the earlier runs' points (%d read)"
                    % (where, res["error"], len(res["runs"])))
    shut(conn)
    module.exit_json(**res)


if __name__ == "__main__":
    main()
