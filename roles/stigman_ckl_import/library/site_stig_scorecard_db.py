#!/usr/bin/python
# -*- coding: utf-8 -*-
"""The STIG compliance scorecard's points in the report database (MySQL; the "Report database" credential,
docs/REPORT_DATABASE.md): a longer history than AAP keeps its jobs. One call a run (roles/stigman_ckl_import/tasks/
scorecard.yml): makes the table stig_scorecard_point when it is not there (stigman_scorecard_db_create), reads the earlier
runs' points of the window back, writes this run's (a row per collection), and deletes the rows older than
stigman_scorecard_db_keep_days. Never fails the job: a database that is down, a login refused or a grant missing is one
warning, and the scorecard goes on with AAP's job history. Needs PyMySQL in the execution environment."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_stig_scorecard_db
short_description: Keep and read back the STIG compliance scorecard's points in the report database
description:
  - Reads the points read between this run and days before it, as runs (oldest first) in the layout of the job artifact
    stig_scorecard.
  - Writes this run's point (point - a row per collection) - not in check mode, and not when write is false.
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
  write: {description: Write this run's point., type: bool, default: true}
  point: {description: "This run's point (the artifact stig_scorecard: read_at, collections).", type: dict, required: true}
  job: {description: This job's ID (0 outside AAP)., type: int, default: 0}
  days: {description: How far back to read., type: float, default: 64}
  keep_days: {description: Delete rows older than this many days (0 = never)., type: int, default: 800}
author: site automation
'''

RETURN = r'''
ok: {description: The database answered and the table is there., type: bool}
error: {description: Why not, when ok is false., type: str}
runs: {description: The earlier runs' points, oldest first (the layout of the artifact stig_scorecard, each with its job)., type: list}
jobs: {description: The job IDs of those runs (AAP's history need not read them again)., type: list}
written: {description: Rows written this run., type: int}
pruned: {description: Rows deleted (older than keep_days)., type: int}
held: {description: "What the table holds after this run: {rows, runs, oldest, newest}.", type: dict}
created: {description: The table was made this run., type: bool}
'''

import datetime
import json
import re

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils import site_report_db as db

SCHEMA_VERSION = 1
COMPONENT = "stig_scorecard"
TABLE = "stig_scorecard_point"
CREATE = (
    "CREATE TABLE IF NOT EXISTS stig_scorecard_point (read_at DATETIME NOT NULL, job BIGINT NOT NULL DEFAULT 0, "
    "collection VARCHAR(64) NOT NULL, collection_name VARCHAR(255) NOT NULL DEFAULT '', assets INT NOT NULL DEFAULT 0, "
    "cat1 INT NOT NULL DEFAULT 0, cat2 INT NOT NULL DEFAULT 0, cat3 INT NOT NULL DEFAULT 0, rules MEDIUMTEXT NOT NULL, "
    "PRIMARY KEY (collection, read_at), KEY stig_scorecard_point_read_at (read_at)) " + db.TABLE_OPTS)
INSERT = ("INSERT INTO stig_scorecard_point (read_at, job, collection, collection_name, assets, cat1, cat2, cat3, rules) "
          "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE job = VALUES(job), "
          "collection_name = VALUES(collection_name), assets = VALUES(assets), cat1 = VALUES(cat1), cat2 = VALUES(cat2), "
          "cat3 = VALUES(cat3), rules = VALUES(rules)")
CATS = ("r1", "r2", "r3")


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
    """The statements the first run sends, for making the table by hand (stigman_scorecard_db_create: false). The doc
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


def rows_of(point, now, job):
    """This run's point -> the table's rows: a collection each, its rules ({r1: {SV-number: hosts}, r2, r3}) as JSON."""
    out = []
    for c in (point or {}).get("collections") or []:
        if not isinstance(c, dict) or c.get("id") in (None, ""):
            continue
        rules = dict((k, c.get(k) or {}) for k in CATS)
        out.append((now, job, str(c["id"])[:64], str(c.get("name") or "")[:255], int(c.get("assets") or 0), int(c.get("cat1") or 0),
                    int(c.get("cat2") or 0), int(c.get("cat3") or 0), json.dumps(rules, sort_keys=True, separators=(",", ":"))))
    return out


def read(conn, now, days):
    """The runs read in the days before now (not now's own), oldest first, as artifact-shaped points."""
    cur = conn.cursor()
    since = now - datetime.timedelta(days=days, hours=2)
    cur.execute("SELECT read_at, job, collection, collection_name, assets, cat1, cat2, cat3, rules FROM stig_scorecard_point "
                "WHERE read_at >= %s AND read_at < %s ORDER BY read_at, collection_name, collection", (since, now))
    runs, at, run = [], None, None
    for read_at, job, cid, name, assets, c1, c2, c3, rules in cur.fetchall():
        if read_at != at:
            at = read_at
            run = {"v": 1, "read_at": read_at.strftime("%Y-%m-%dT%H:%M:%SZ"), "job": int(job or 0), "collections": []}
            runs.append(run)
        try:
            r = json.loads(rules or "{}")
        except ValueError:
            r = {}
        rec = {"id": str(cid), "name": str(name), "assets": int(assets), "cat1": int(c1), "cat2": int(c2), "cat3": int(c3)}
        rec.update((k, r.get(k) if isinstance(r, dict) and isinstance(r.get(k), dict) else {}) for k in CATS)
        run["collections"].append(rec)
    return runs


def held(conn):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), COUNT(DISTINCT read_at), MIN(read_at), MAX(read_at) FROM stig_scorecard_point")
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
                           point=dict(type="dict", required=True), job=dict(type="int", default=0),
                           days=dict(type="float", default=64), keep_days=dict(type="int", default=800)),
        supports_check_mode=True)
    p = module.params
    where = "%s:%s/%s" % (p["host"], p["port"], p["database"])
    res = {"changed": False, "ok": False, "error": "", "tls": "", "where": where, "runs": [], "jobs": [], "written": 0, "pruned": 0,
           "held": {}, "created": False}

    def give_up(msg):
        res["error"] = msg
        module.warn("The report database %s: %s - the STIG compliance scorecard goes on with AAP's job history "
                    "(stigman_scorecard_db: false turns the database off for it)" % (where, msg))
        module.exit_json(**res)

    if not db.HAS_PYMYSQL:
        give_up(db.NO_PYMYSQL)
    now = stamp(p["point"].get("read_at"))
    if now is None:
        give_up("the point's read_at is not a time: %r" % p["point"].get("read_at"))
    conn = None
    try:
        conn, res["tls"] = db.connect(p)
        there, res["created"] = schema(conn, p["create"] and not module.check_mode)
        if not there:
            conn.close()
            conn = None
            give_up("the table %s is not there%s - make it once, docs/REPORT_DATABASE.md" % (
                TABLE, " (check mode: nothing made)" if module.check_mode and p["create"] else " (stigman_scorecard_db_create: false)"))
        res["runs"] = read(conn, now, p["days"])
        res["jobs"] = sorted(set(r["job"] for r in res["runs"] if r["job"]))
        res["ok"] = True
    except Exception as e:      # noqa: BLE001 - unreachable, refused, a grant missing: one line, the job goes on
        res["runs"], res["jobs"] = [], []
        shut(conn)
        give_up(db.explain(e, p))
    try:                        # the earlier points are read: a write that fails loses this run's point only
        if p["write"] and not module.check_mode:
            rows = rows_of(p["point"], now, p["job"])
            cur = conn.cursor()
            if rows:
                cur.executemany(INSERT, rows)
            res["written"] = len(rows)
            if p["keep_days"] > 0:
                res["pruned"] = int(cur.execute("DELETE FROM stig_scorecard_point WHERE read_at < %s",
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
        module.warn("The report database %s: %s - the STIG compliance scorecard has the earlier runs' points (%d read)"
                    % (where, res["error"], len(res["runs"])))
    shut(conn)
    module.exit_json(**res)


if __name__ == "__main__":
    main()
