#!/usr/bin/python
# -*- coding: utf-8 -*-
"""The STIG compliance scorecard's points in the report database (MySQL; the "Report database" credential,
docs/REPORT_DATABASE.md): a longer history than AAP keeps its jobs. One call a run (roles/stigman_ckl_import/tasks/
scorecard.yml): makes the table stig_scorecard_point when it is not there (stigman_scorecard_db_create), reads the earlier
runs' points of the window back, writes this run's (a row per collection), and deletes the rows older than
stigman_scorecard_db_keep_days. Never fails the job: a database that is down, a login refused or a grant missing is one
warning, and the scorecard goes on with AAP's job history. Needs PyMySQL in the execution environment.

1.67.0 (schema version 2): a second table, stig_scorecard_platform - a row per run, collection and platform (the point's
p). A version 1 database is brought to 2 by making that table (create): the rows it holds stay as they are, and those
runs have no platforms."""
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
  - 1.67.0, schema version 2 - each collection's platforms in the table stig_scorecard_platform. A version 1 database
    gets that table on the next run (create), its rows kept. Without it (create false) the platforms are neither kept
    nor read, and error says so.
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
migrated: {description: "1.67.0: the database was brought from schema version 1 to 2 this run (the table stig_scorecard_platform made).", type: bool}
platforms: {description: "1.67.0: the table stig_scorecard_platform is there (the platforms kept and read back).", type: bool}
'''

import datetime
import json
import re

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils import site_report_db as db

SCHEMA_VERSION = 2
COMPONENT = "stig_scorecard"
TABLE = "stig_scorecard_point"
TABLE_P = "stig_scorecard_platform"
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
# 1.67.0 (version 2): a collection's platforms (the point's p) - its assets and open findings per platform, its rules
CREATE_P = (
    "CREATE TABLE IF NOT EXISTS stig_scorecard_platform (read_at DATETIME NOT NULL, job BIGINT NOT NULL DEFAULT 0, "
    "collection VARCHAR(64) NOT NULL, platform VARCHAR(64) NOT NULL, assets INT NOT NULL DEFAULT 0, "
    "cat1 INT NOT NULL DEFAULT 0, cat2 INT NOT NULL DEFAULT 0, cat3 INT NOT NULL DEFAULT 0, rules MEDIUMTEXT NOT NULL, "
    "PRIMARY KEY (collection, platform, read_at), KEY stig_scorecard_platform_read_at (read_at)) " + db.TABLE_OPTS)
INSERT_P = ("INSERT INTO stig_scorecard_platform (read_at, job, collection, platform, assets, cat1, cat2, cat3, rules) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE job = VALUES(job), assets = VALUES(assets), "
            "cat1 = VALUES(cat1), cat2 = VALUES(cat2), cat3 = VALUES(cat3), rules = VALUES(rules)")
UPGRADE = "UPDATE schema_version SET version = %d, applied_at = UTC_TIMESTAMP() WHERE component = '%s';" % (SCHEMA_VERSION, COMPONENT)


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
    return "\n\n".join([pretty(db.SCHEMA_VERSION_TABLE), pretty(CREATE), pretty(CREATE_P),
                        "INSERT INTO schema_version (component, version, applied_at) VALUES ('%s', %d, UTC_TIMESTAMP());"
                        % (COMPONENT, SCHEMA_VERSION)])


def ddl_upgrade():
    """1.67.0: from version 1 to 2 by hand (stigman_scorecard_db_create: false) - the new table, then the version. The
    rows of stig_scorecard_point stay."""
    return "\n\n".join([pretty(CREATE_P), UPGRADE])


def stamp(text):
    """'2026-11-13T06:00:05Z' -> a datetime (UTC, naive: what the DATETIME column holds), or None."""
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)", str(text or ""))
    return datetime.datetime(*[int(x) for x in m.groups()]) if m else None


def _table(cur, name):
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = %s", (name,))
    return cur.fetchone() is not None


def schema(conn, create):
    """{there, created, platforms, migrated}: the tables, made when they are not there and create is true; schema_version's
    row with them. A version 1 database (1.58.0 to 1.66.x) gets the table stig_scorecard_platform and version 2 - its
    rows stay. A newer version than this release knows: an error."""
    cur = conn.cursor()
    there = _table(cur, TABLE)
    if not there:
        if not create:
            return {"there": False, "created": False, "platforms": False, "migrated": False}
        cur.execute(db.SCHEMA_VERSION_TABLE)
        cur.execute(CREATE)
    plat = _table(cur, TABLE_P)
    migrated = False
    if _table(cur, "schema_version"):
        cur.execute("SELECT version FROM schema_version WHERE component = %s", (COMPONENT,))
        r = cur.fetchone()
        if r is not None and int(r[0]) > SCHEMA_VERSION:
            raise RuntimeError("the table %s is version %s, newer than this release knows (%s) - update site-automation" % (TABLE, r[0], SCHEMA_VERSION))
        if create and not plat:
            cur.execute(CREATE_P)
            plat, migrated = True, there
        if r is None and create:
            cur.execute("INSERT INTO schema_version (component, version, applied_at) VALUES (%s, %s, UTC_TIMESTAMP())", (COMPONENT, SCHEMA_VERSION))
        elif r is not None and int(r[0]) < SCHEMA_VERSION and plat:
            cur.execute(UPGRADE)
            migrated = migrated or there
    elif create and not plat:
        cur.execute(CREATE_P)
        plat = True
    conn.commit()
    return {"there": True, "created": not there, "platforms": plat, "migrated": migrated}


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


def rows_p_of(point, now, job):
    """1.67.0: this run's point -> stig_scorecard_platform's rows: a collection and platform each, its rules as JSON."""
    out = []
    for c in (point or {}).get("collections") or []:
        if not isinstance(c, dict) or c.get("id") in (None, "") or not isinstance(c.get("p"), dict):
            continue
        for name, v in sorted(c["p"].items()):
            if isinstance(v, dict):
                out.append((now, job, str(c["id"])[:64], str(name)[:64], int(v.get("assets") or 0), int(v.get("cat1") or 0),
                            int(v.get("cat2") or 0), int(v.get("cat3") or 0),
                            json.dumps(sorted(str(x) for x in v.get("rules") or []), separators=(",", ":"))))
    return out


def read(conn, now, days, platforms=False):
    """The runs read in the days before now (not now's own), oldest first, as artifact-shaped points. platforms (1.67.0):
    each collection's p from stig_scorecard_platform - a run kept before 1.67.0 has none (version 1 points)."""
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
    if platforms and runs:
        cur.execute("SELECT read_at, collection, platform, assets, cat1, cat2, cat3, rules FROM stig_scorecard_platform "
                    "WHERE read_at >= %s AND read_at < %s", (since, now))
        at = dict(((run["read_at"], c["id"]), c) for run in runs for c in run["collections"])
        for read_at, cid, name, assets, c1, c2, c3, rules in cur.fetchall():
            c = at.get((read_at.strftime("%Y-%m-%dT%H:%M:%SZ"), str(cid)))
            if c is None:
                continue
            try:
                rl = json.loads(rules or "[]")
            except ValueError:
                rl = []
            c.setdefault("p", {})[str(name)] = {"assets": int(assets), "cat1": int(c1), "cat2": int(c2), "cat3": int(c3),
                                                "rules": [str(x) for x in rl] if isinstance(rl, list) else []}
        for run in runs:
            if any("p" in c for c in run["collections"]):
                run["v"] = 2
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
           "held": {}, "created": False, "migrated": False, "platforms": False}

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
        sch = schema(conn, p["create"] and not module.check_mode)
        there, res["created"], res["platforms"], res["migrated"] = sch["there"], sch["created"], sch["platforms"], sch["migrated"]
        if not there:
            conn.close()
            conn = None
            give_up("the table %s is not there%s - make it once, docs/REPORT_DATABASE.md" % (
                TABLE, " (check mode: nothing made)" if module.check_mode and p["create"] else " (stigman_scorecard_db_create: false)"))
        res["runs"] = read(conn, now, p["days"], res["platforms"])
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
            prow = rows_p_of(p["point"], now, p["job"]) if res["platforms"] else []
            if prow:
                cur.executemany(INSERT_P, prow)
            if p["keep_days"] > 0:
                old = now - datetime.timedelta(days=p["keep_days"])
                res["pruned"] = int(cur.execute("DELETE FROM stig_scorecard_point WHERE read_at < %s", (old,)) or 0)
                if res["platforms"]:
                    cur.execute("DELETE FROM stig_scorecard_platform WHERE read_at < %s", (old,))
            conn.commit()
        res["held"] = held(conn)
        res["changed"] = bool(res["written"] or res["pruned"] or res["created"] or res["migrated"])
        if not res["platforms"] and not res["error"]:
            res["error"] = ("the table %s is not there: the platforms are not kept (stigman_scorecard_db_create: false - make it "
                            "once, docs/REPORT_DATABASE.md)" % TABLE_P)
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
