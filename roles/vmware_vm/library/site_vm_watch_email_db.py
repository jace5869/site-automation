#!/usr/bin/python
# -*- coding: utf-8 -*-
"""1.118.8: the VM outage watch's email state in the report database (MySQL; the "Report database" credential,
docs/REPORT_DATABASE.md), for when AAP cannot be read: when the last email went and what it was about. One row per job
template (vm_watch_email_state). Makes the table when it is not there. Never fails the job: a database that is down, a
login refused or a grant missing is one warning, and the watch goes on without the state. Needs PyMySQL in the execution
environment. A write that fails says nothing (it runs every run); a read that fails - when AAP could not be read -
warns, and the watch's job output says what to attach."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vm_watch_email_db
short_description: Keep and read back the VM outage watch's email state in the report database
description:
  - read - this template's row (state, or none when there is no row yet).
  - write - this run's state, as the row of this template (not in check mode).
  - The table is made on the first run; with no right to make it, error says so. docs/REPORT_DATABASE.md has the
    statement for making it by hand.
options:
  host: {description: The database server., type: str, required: true}
  port: {description: Its port., type: int, default: 3309}
  database: {description: The database., type: str, required: true}
  user: {description: The account., type: str, required: true}
  password: {description: Its password., type: str, required: true}
  tls: {description: Use TLS and check the server's certificate., type: bool, default: false}
  ca_file: {description: The CA file the certificate is checked with., type: str, default: ''}
  connect_timeout: {description: Seconds to wait for the connection., type: int, default: 10}
  mode: {description: "read (and check that a write would work: the row written and rolled back) or write.", type: str, default: read}
  needed: {description: "write: the state is needed here (AAP could not be read) - a failed write warns. Otherwise it is one line in the result.", type: bool, default: false}
  read_timeout: {description: Seconds to wait for the database to answer once connected (a server that takes the connection and never answers)., type: int, default: 20}
  create: {description: Make the table when it is not there (false in a dry run)., type: bool, default: true}
  template: {description: This job's template ID (0 outside AAP)., type: int, default: 0}
  job: {description: This job's ID (0 outside AAP)., type: int, default: 0}
  state: {description: "The state to keep (write): {sent_at, sent_ts, read_ts, ids, review_ts, held}.", type: dict, default: {}}
author: site automation
'''

RETURN = r'''
ok: {description: "The database answered, the table is there, and (read) a write works.", type: bool}
error: {description: Why not, when ok is false., type: str}
state: {description: The kept state (read), or none when this template has no row yet., type: dict}
written: {description: The row was written., type: bool}
created: {description: The table was made this run., type: bool}
'''

import json

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils import site_report_db as db

SCHEMA_VERSION = 1
COMPONENT = "vm_watch_email"
TABLE = "vm_watch_email_state"
CREATE = (
    "CREATE TABLE IF NOT EXISTS vm_watch_email_state (template BIGINT NOT NULL PRIMARY KEY, job BIGINT NOT NULL DEFAULT 0, "
    "updated_at DATETIME NOT NULL, state MEDIUMTEXT NOT NULL) " + db.TABLE_OPTS)
UPSERT = ("INSERT INTO vm_watch_email_state (template, job, updated_at, state) VALUES (%s, %s, UTC_TIMESTAMP(), %s) "
          "ON DUPLICATE KEY UPDATE job = VALUES(job), updated_at = VALUES(updated_at), state = VALUES(state)")


def pretty(sql):
    """A CREATE TABLE as docs/REPORT_DATABASE.md shows it: a column per line."""
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
    """The statements the first run sends, for making the table by hand. The doc carries this text; a test keeps them alike."""
    return "\n\n".join([pretty(db.SCHEMA_VERSION_TABLE), pretty(CREATE),
                        "INSERT INTO schema_version (component, version, applied_at) VALUES ('%s', %d, UTC_TIMESTAMP());"
                        % (COMPONENT, SCHEMA_VERSION)])


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


def read(conn, template):
    """This template's state, or None (no row yet, or a row that is not a state)."""
    cur = conn.cursor()
    cur.execute("SELECT state FROM vm_watch_email_state WHERE template = %s", (template,))
    r = cur.fetchone()
    if r is None:
        return None
    try:
        st = json.loads(r[0] or "")
    except (ValueError, TypeError):
        return None
    return st if isinstance(st, dict) and isinstance(st.get("ids", {}), dict) else None


def writable(conn, template):
    """A write works: this template's row written as it is (or an empty one) and rolled back. A login with SELECT only
    fails here - its state could be read but never kept, and every run would then be a first email."""
    cur = conn.cursor()
    cur.execute("SELECT state FROM vm_watch_email_state WHERE template = %s", (template,))
    r = cur.fetchone()
    try:
        cur.execute(UPSERT, (template, 0, (r[0] if r is not None else None) or "{}"))
    finally:
        conn.rollback()
    return True


def write(conn, template, job, state):
    conn.cursor().execute(UPSERT, (template, job, json.dumps(state or {}, sort_keys=True)))
    conn.commit()
    return True


def shut(conn):
    try:
        if conn is not None:
            conn.close()
    except Exception:           # noqa: BLE001
        pass


def main():
    module = AnsibleModule(
        argument_spec=dict(db.ARGS, mode=dict(type="str", default="read", choices=["read", "write"]),
                           create=dict(type="bool", default=True), needed=dict(type="bool", default=False),
                           read_timeout=dict(type="int", default=20),
                           template=dict(type="int", default=0), job=dict(type="int", default=0),
                           state=dict(type="dict", default={})),
        supports_check_mode=True)
    p = module.params
    where = "%s:%s/%s" % (p["host"], p["port"], p["database"])
    res = {"changed": False, "ok": False, "error": "", "tls": "", "where": where, "state": None, "written": False, "created": False}

    def give_up(msg):
        res["error"] = msg
        if p["mode"] == "write" and not p["needed"]:   # AAP has the state: a reader's login would warn every run
            module.exit_json(**res)
        module.warn("The report database %s: %s - the VM outage watch's email state is not %s there (the login must make "
                    "and write the table vm_watch_email_state: the writer's Report database credential, or the table and "
                    "grant made by hand, docs/REPORT_DATABASE.md; vm_watch_email_db: false = not used)"
                    % (where, msg, "read" if p["mode"] == "read" else "kept"))
        module.exit_json(**res)

    if not db.HAS_PYMYSQL:
        give_up(db.NO_PYMYSQL)
    if p["mode"] == "write" and module.check_mode:
        module.exit_json(**res)
    conn = None
    try:
        conn, res["tls"] = db.connect(p)              # read_timeout: a server that stops answering never blocks the email
        there, res["created"] = schema(conn, p["create"] and not module.check_mode)
        if not there:
            shut(conn)
            give_up("the table %s is not there (a dry run makes nothing) - make it once, docs/REPORT_DATABASE.md" % TABLE)
        if p["mode"] == "read":
            res["state"] = read(conn, p["template"])
            try:
                writable(conn, p["template"])
            except Exception as e:  # noqa: BLE001 - SELECT only: read, but never kept - as good as no state
                res["state"] = None
                shut(conn)
                give_up("the login cannot write the table %s (%s)" % (TABLE, db.explain(e, p)))
        else:
            res["written"] = write(conn, p["template"], p["job"], p["state"])
        res["ok"] = True
        res["changed"] = bool(res["written"] or res["created"])
    except Exception as e:      # noqa: BLE001 - unreachable, refused, a grant missing: one line, the job goes on
        res["state"], res["written"] = None, False
        shut(conn)
        give_up(db.explain(e, p))
    shut(conn)
    module.exit_json(**res)


if __name__ == "__main__":
    main()
