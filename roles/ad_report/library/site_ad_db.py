#!/usr/bin/python
# -*- coding: utf-8 -*-
"""1.101.2: each AD health run in the report database (MySQL; the "Report database" credential, docs/REPORT_DATABASE.md) -
the run, each DC's state and each replication link's - for the portal's live AD page (/portal/ad), which reads them with
a read-only login: what is now, and since when (a DC's trend, a link failing since its last success). One call a run
(roles/ad_report/tasks/db.yml): makes the tables when they are not there (ad_db_create), writes this run in one
transaction, deletes runs older than ad_db_keep_days. Never fails the job: a database that is down, a login refused or a
grant missing is one warning, and the report goes on. Needs PyMySQL in the execution environment."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_ad_db
short_description: Keep each AD health run (its DCs and replication links) in the report database
description:
  - Writes the run (ad_run), its DCs (ad_dc) and its replication links (ad_link) in one transaction - not in check mode.
  - Deletes this template's runs older than keep_days, with their rows (0 = never); other templates' runs are left.
  - The tables are made on the first run (create); with create false and no tables, nothing is written and error says so.
    docs/REPORT_DATABASE.md has the statements for making them by hand.
options:
  host: {description: The database server., type: str, required: true}
  port: {description: Its port., type: int, default: 3309}
  database: {description: The database., type: str, required: true}
  user: {description: The account., type: str, required: true}
  password: {description: Its password., type: str, required: true}
  tls: {description: Use TLS and check the server's certificate., type: bool, default: false}
  ca_file: {description: The CA file the certificate is checked with., type: str, default: ''}
  connect_timeout: {description: Seconds to wait for the connection., type: int, default: 10}
  create: {description: Make the tables when they are not there., type: bool, default: true}
  read_at: {description: "When this run read AD (2026-11-13T06:00:05Z).", type: str, required: true}
  job: {description: This job's ID (0 outside AAP)., type: int, default: 0}
  template_id: {description: This job's template's ID (0 outside AAP) - two AD templates (two forests) are two lines., type: int, default: 0}
  run: {description: "The run (plugins/filter/ad_db_filters.py ad_db_payload): status, critical, warning, dcs, links, live.", type: dict, required: true}
  keep_days: {description: Delete this template's runs older than this many days (0 = never)., type: int, default: 400}
author: site automation
'''

RETURN = r'''
ok: {description: The run was kept., type: bool}
error: {description: Why not., type: str}
run_id: {description: The run's row., type: int}
dcs: {description: DC rows written., type: int}
links: {description: Link rows written., type: int}
pruned: {description: Runs deleted (older than keep_days)., type: int}
created: {description: The tables were made this run., type: bool}
'''

import datetime
import hashlib
import json
import re

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils import site_report_db as db

SCHEMA_VERSION = 2                      # 2 (1.101.2 reviews, before it shipped): ad_run why/repl_dcs/repl_read, ad_dc.repl_read, ad_link domain/src/link_key
COMPONENT = "ad_run"
TABLES = ("ad_run", "ad_dc", "ad_link")
CREATE = (
    "CREATE TABLE IF NOT EXISTS ad_run (run_id BIGINT NOT NULL AUTO_INCREMENT, read_at DATETIME NOT NULL, "
    "job BIGINT NOT NULL DEFAULT 0, template_id BIGINT NOT NULL DEFAULT 0, status VARCHAR(16) NOT NULL DEFAULT '', "
    "critical INT NOT NULL DEFAULT 0, warning INT NOT NULL DEFAULT 0, dcs INT NOT NULL DEFAULT 0, dcs_read INT NOT NULL DEFAULT 0, "
    "live MEDIUMTEXT NULL, why VARCHAR(300) NOT NULL DEFAULT '', repl_dcs INT NOT NULL DEFAULT 0, repl_read INT NOT NULL DEFAULT 0, "
    "PRIMARY KEY (run_id), KEY ad_run_read_at (read_at), KEY ad_run_template (template_id, read_at)) " + db.TABLE_OPTS,
    "CREATE TABLE IF NOT EXISTS ad_dc (run_id BIGINT NOT NULL, dc VARCHAR(191) NOT NULL, domain VARCHAR(191) NOT NULL DEFAULT '', "
    "site VARCHAR(191) NOT NULL DEFAULT '', is_read TINYINT NOT NULL DEFAULT 0, repl_read TINYINT NOT NULL DEFAULT 0, "
    "status VARCHAR(16) NOT NULL DEFAULT '', "
    "problems INT NOT NULL DEFAULT 0, worst VARCHAR(300) NOT NULL DEFAULT '', PRIMARY KEY (run_id, domain, dc)) " + db.TABLE_OPTS,
    "CREATE TABLE IF NOT EXISTS ad_link (run_id BIGINT NOT NULL, link_key CHAR(40) NOT NULL, domain VARCHAR(191) NOT NULL DEFAULT '', "
    "frm VARCHAR(191) NOT NULL, src VARCHAR(255) NOT NULL DEFAULT '', to_dc VARCHAR(191) NOT NULL, "
    "nc VARCHAR(191) NOT NULL DEFAULT '', last_success DATETIME NULL, failures INT NOT NULL DEFAULT 0, "
    "error VARCHAR(255) NOT NULL DEFAULT '', last_failure DATETIME NULL, status VARCHAR(16) NOT NULL DEFAULT '', "
    "PRIMARY KEY (run_id, link_key)) " + db.TABLE_OPTS)
INSERT_RUN = ("INSERT INTO ad_run (read_at, job, template_id, status, critical, warning, dcs, dcs_read, live, why, repl_dcs, repl_read) "
              "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)")
INSERT_DC = ("INSERT INTO ad_dc (run_id, dc, domain, site, is_read, repl_read, status, problems, worst) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
             "ON DUPLICATE KEY UPDATE status = VALUES(status)")
INSERT_LINK = ("INSERT INTO ad_link (run_id, link_key, domain, frm, src, to_dc, nc, last_success, failures, error, last_failure, status) "
               "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE failures = VALUES(failures), status = VALUES(status)")


def link_key(x):
    """A link's identity: the receiving DC's domain, the source as AD names it (else its short name), the receiving DC and
    the partition - two sources of one short name (two domains' DC01) are two links, never one row."""
    ident = "\x1f".join(str(v or "").lower() for v in (x.get("domain"), x.get("src") or x.get("frm"), x.get("to"), x.get("nc")))
    return hashlib.sha1(ident.encode("utf-8")).hexdigest()


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
    """The statements the first run sends, for making the tables by hand (ad_db_create: false). The doc carries this text;
    a test keeps them alike."""
    return "\n\n".join([pretty(db.SCHEMA_VERSION_TABLE)] + [pretty(c) for c in CREATE] +
                       ["INSERT INTO schema_version (component, version, applied_at) VALUES ('%s', %d, UTC_TIMESTAMP());"
                        % (COMPONENT, SCHEMA_VERSION)])


def stamp(text):
    """'2026-11-13T06:00:05Z' (or '2026-11-13 06:00') -> a datetime (UTC, naive: what the DATETIME column holds), or None."""
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d)(?::(\d\d))?", str(text or ""))
    return datetime.datetime(*[int(x or 0) for x in m.groups()]) if m else None


def schema(conn, create):
    """(there, created): the three tables, made when one is not there and create is true; schema_version's row with them."""
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name IN (%s, %s, %s)", TABLES)
    have = set(str(r[0]) for r in cur.fetchall())
    there = have >= set(TABLES)
    if not there:
        if not create:
            return False, False
        cur.execute(db.SCHEMA_VERSION_TABLE)
        for c in CREATE:
            cur.execute(c)
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'schema_version'")
    if cur.fetchone() is not None:
        cur.execute("SELECT version FROM schema_version WHERE component = %s", (COMPONENT,))
        r = cur.fetchone()
        if r is None and create:
            cur.execute("INSERT INTO schema_version (component, version, applied_at) VALUES (%s, %s, UTC_TIMESTAMP())", (COMPONENT, SCHEMA_VERSION))
        elif r is not None and int(r[0]) > SCHEMA_VERSION:
            raise RuntimeError("the AD tables are version %s, newer than this release knows (%s) - update site-automation" % (r[0], SCHEMA_VERSION))
        elif r is not None and int(r[0]) < SCHEMA_VERSION:   # 1: a build from before 1.101.2 shipped - its columns differ
            raise RuntimeError("the AD tables are version %s, from a build before 1.101.2 shipped: drop ad_run, ad_dc and ad_link "
                               "(and its schema_version row) and the next run makes them again" % r[0])
    conn.commit()
    return True, not there


def write(conn, now, job, template_id, run):
    """(run_id, dcs, links): the run and its rows, in the caller's transaction."""
    r = run or {}
    dcs = [d for d in r.get("dcs") or [] if isinstance(d, dict) and d.get("dc")]
    links = [x for x in r.get("links") or [] if isinstance(x, dict) and x.get("frm") and x.get("to")]
    cur = conn.cursor()
    cur.execute(INSERT_RUN, (now, job, template_id, str(r.get("status") or "")[:16], int(r.get("critical") or 0), int(r.get("warning") or 0),
                             len(dcs), sum(1 for d in dcs if d.get("read")), json.dumps(r.get("live") or {}, separators=(",", ":")),
                             str(r.get("why") or "")[:300], int(r.get("repl_dcs") or 0), int(r.get("repl_read") or 0)))
    rid = cur.lastrowid
    if dcs:
        cur.executemany(INSERT_DC, [(rid, str(d["dc"])[:191], str(d.get("domain") or "")[:191], str(d.get("site") or "")[:191],
                                     1 if d.get("read") else 0, 1 if d.get("repl_read") else 0, str(d.get("status") or "")[:16], int(d.get("problems") or 0),
                                     str(d.get("worst") or "")[:300]) for d in dcs])
    if links:
        cur.executemany(INSERT_LINK, [(rid, link_key(x), str(x.get("domain") or "")[:191], str(x["frm"])[:191], str(x.get("src") or "")[:255], str(x["to"])[:191], str(x.get("nc") or "")[:191], stamp(x.get("last_success")),
                                       int(x.get("failures") or 0), str(x.get("error") or "")[:255], stamp(x.get("last_failure")),
                                       str(x.get("status") or "")[:16]) for x in links])
    return int(rid or 0), len(dcs), len(links)


def prune(conn, now, keep_days, template_id=0):
    """This template's runs older than keep_days, with their DC and link rows - another AD template's runs keep their own
    keep_days (each forest's job prunes its own)."""
    if keep_days <= 0:
        return 0
    cut = now - datetime.timedelta(days=keep_days)
    cur = conn.cursor()
    old = "SELECT run_id FROM ad_run WHERE read_at < %s AND template_id = %s"
    cur.execute("DELETE FROM ad_link WHERE run_id IN (%s)" % old, (cut, template_id))
    cur.execute("DELETE FROM ad_dc WHERE run_id IN (%s)" % old, (cut, template_id))
    return int(cur.execute("DELETE FROM ad_run WHERE read_at < %s AND template_id = %s", (cut, template_id)) or 0)


def shut(conn):
    try:
        if conn is not None:
            conn.close()
    except Exception:           # noqa: BLE001
        pass


def main():
    module = AnsibleModule(
        argument_spec=dict(db.ARGS, create=dict(type="bool", default=True), read_at=dict(type="str", required=True),
                           job=dict(type="int", default=0), template_id=dict(type="int", default=0),
                           run=dict(type="dict", required=True), keep_days=dict(type="int", default=400)),
        supports_check_mode=True)
    p = module.params
    where = "%s:%s/%s" % (p["host"], p["port"], p["database"])
    res = {"changed": False, "ok": False, "error": "", "tls": "", "where": where, "run_id": 0, "dcs": 0, "links": 0, "pruned": 0, "created": False}

    def give_up(msg):
        res["error"] = msg
        module.warn("The report database %s: %s - this AD run is not kept there, so the portal's live AD page shows the run "
                    "before it (ad_db: false turns this off)" % (where, msg))
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
            shut(conn)
            give_up("the tables %s are not there%s - make them once, docs/REPORT_DATABASE.md" % (
                ", ".join(TABLES), " (check mode: nothing made)" if module.check_mode and p["create"] else " (ad_db_create: false)"))
        if module.check_mode:
            res["ok"] = True
            shut(conn)
            module.exit_json(**res)
        res["run_id"], res["dcs"], res["links"] = write(conn, now, p["job"], p["template_id"], p["run"])
        res["pruned"] = prune(conn, now, p["keep_days"], p["template_id"])
        conn.commit()
        res["ok"], res["changed"] = True, True
    except Exception as e:      # noqa: BLE001 - unreachable, refused, a grant missing: one line, the job goes on
        try:
            if conn is not None:
                conn.rollback()
        except Exception:       # noqa: BLE001
            pass
        shut(conn)
        res["run_id"], res["dcs"], res["links"], res["pruned"] = 0, 0, 0, 0
        give_up(db.explain(e, p))
    shut(conn)
    module.exit_json(**res)


if __name__ == "__main__":
    main()
