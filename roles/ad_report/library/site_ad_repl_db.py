#!/usr/bin/python
# -*- coding: utf-8 -*-
"""The replication matrix's trend points in the report database (MySQL; the "Report database" credential,
docs/REPORT_DATABASE.md), when AAP's job history is not enough: each site's worst inbound replication age, one row a site
a run. One call a run (roles/ad_report/tasks/repl.yml): makes the table ad_repl_point when it is not there
(ad_repl_db_create), reads the earlier runs of the window back, writes this run's rows, and deletes rows older than
ad_repl_db_keep_days. Never fails the job: a database that is down, a login refused or a grant missing is one warning,
and the report goes on with AAP's job history. Needs PyMySQL in the execution environment."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_ad_repl_db
short_description: Keep and read back the AD replication matrix's trend points in the report database
description:
  - Reads the runs between this run and days before it, oldest first, in the layout of AAP's job history runs with the
    artifact ad_repl ({artifacts: {ad_repl: {read_at, sites}}}).
  - Writes this run's rows (sites; read_at, job) - not in check mode, and not when write is false.
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
  read_at: {description: "When this run read AD (2026-11-13T06:00:05Z).", type: str, required: true}
  job: {description: This job's ID (0 outside AAP)., type: int, default: 0}
  sites: {description: "This run's points: {site: worst hours (null: not read)} (the artifact ad_repl's sites).", type: dict, default: {}}
  status: {description: "Each site's status: {site: ok | low | warning | critical | unknown}.", type: dict, default: {}}
  days: {description: How far back to read (ad_repl_trend_days)., type: float, default: 14}
  keep_days: {description: Delete rows older than this many days (0 = never)., type: int, default: 400}
author: site automation
'''

RETURN = r'''
ok: {description: The database answered and the table is there., type: bool}
error: {description: Why not, when ok is false., type: str}
runs: {description: "The earlier runs, oldest first: [{id: job, finished, artifacts: {ad_repl: {read_at, sites}}}].", type: list}
written: {description: Rows written this run., type: int}
pruned: {description: Rows deleted (older than keep_days)., type: int}
created: {description: The table was made this run., type: bool}
'''

import datetime
import re

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils import site_report_db as db

SCHEMA_VERSION = 1
COMPONENT = "ad_repl"
TABLE = "ad_repl_point"
CREATE = (
    "CREATE TABLE IF NOT EXISTS ad_repl_point (read_at DATETIME NOT NULL, job BIGINT NOT NULL DEFAULT 0, "
    "site VARCHAR(255) NOT NULL, worst_hours DECIMAL(10,2) NULL, status VARCHAR(16) NOT NULL DEFAULT '', "
    "PRIMARY KEY (site, read_at), KEY ad_repl_point_read_at (read_at)) " + db.TABLE_OPTS)
INSERT = ("INSERT INTO ad_repl_point (read_at, job, site, worst_hours, status) VALUES (%s, %s, %s, %s, %s) "
          "ON DUPLICATE KEY UPDATE job = VALUES(job), worst_hours = VALUES(worst_hours), status = VALUES(status)")


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
    """The statements the first run sends, for making the table by hand (ad_repl_db_create: false). The doc carries this
    text; a test keeps them alike."""
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


def read(conn, now, days):
    """The runs in the days before now (not now's own), oldest first, as AAP history runs with the artifact ad_repl."""
    cur = conn.cursor()
    cur.execute("SELECT read_at, job, site, worst_hours FROM ad_repl_point WHERE read_at >= %s AND read_at < %s ORDER BY read_at, site",
                (now - datetime.timedelta(days=days, hours=2), now))
    runs, at = [], None
    for read_at, job, site, hours in cur.fetchall():
        if read_at != at:
            at = read_at
            t = read_at.strftime("%Y-%m-%dT%H:%M:%SZ")
            runs.append({"id": int(job or 0), "finished": t, "artifacts": {"ad_repl": {"read_at": t, "sites": {}}}})
        runs[-1]["artifacts"]["ad_repl"]["sites"][str(site)] = None if hours is None else float(hours)
    return runs


def write(conn, now, job, sites, status):
    rows = []
    for site, hours in sorted((sites or {}).items()):
        try:
            h = None if hours is None else round(min(float(hours), 99999999.0), 2)
        except (TypeError, ValueError):
            continue
        rows.append((now, job, str(site)[:255], h, str((status or {}).get(site) or "")[:16]))
    if rows:
        conn.cursor().executemany(INSERT, rows)
    return len(rows)


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
                           sites=dict(type="dict", default={}), status=dict(type="dict", default={}),
                           days=dict(type="float", default=14), keep_days=dict(type="int", default=400)),
        supports_check_mode=True)
    p = module.params
    where = "%s:%s/%s" % (p["host"], p["port"], p["database"])
    res = {"changed": False, "ok": False, "error": "", "tls": "", "where": where, "runs": [], "written": 0, "pruned": 0, "created": False}

    def give_up(msg):
        res["error"] = msg
        module.warn("The report database %s: %s - the replication trend goes on with AAP's job history "
                    "(ad_repl_db: false turns the database off for it)" % (where, msg))
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
                TABLE, " (check mode: nothing made)" if module.check_mode and p["create"] else " (ad_repl_db_create: false)"))
        res["runs"] = read(conn, now, p["days"])
        res["ok"] = True
    except Exception as e:      # noqa: BLE001 - unreachable, refused, a grant missing: one line, the job goes on
        res["runs"] = []
        shut(conn)
        give_up(db.explain(e, p))
    try:                        # the earlier points are read: a write that fails loses this run's point only
        if p["write"] and not module.check_mode:
            res["written"] = write(conn, now, p["job"], p["sites"], p["status"])
            if p["keep_days"] > 0:
                cur = conn.cursor()
                res["pruned"] = int(cur.execute("DELETE FROM ad_repl_point WHERE read_at < %s",
                                                (now - datetime.timedelta(days=p["keep_days"]),)) or 0)
            conn.commit()
        res["changed"] = bool(res["written"] or res["pruned"] or res["created"])
    except Exception as e:      # noqa: BLE001 - a read-only account, a full disk: said, the report has the earlier points
        try:
            conn.rollback()
        except Exception:       # noqa: BLE001
            pass
        res["written"], res["pruned"] = 0, 0
        res["error"] = "this run's points were not kept: %s" % db.explain(e, p)
        module.warn("The report database %s: %s - the replication trend has the earlier runs (%d read)" % (where, res["error"], len(res["runs"])))
    shut(conn)
    module.exit_json(**res)


if __name__ == "__main__":
    main()
