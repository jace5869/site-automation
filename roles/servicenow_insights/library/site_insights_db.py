#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Ticket insights' history (1.13.0): the report database (MySQL 8.4, docs/REPORT_DATABASE.md) - the run's figures
written (one run row, a row per org node, the figures as metric / value rows; a run of the same window replaces its
rows) and the last months read back for the page's trend. Plain TCP by default: MySQL 8.4's caching_sha2_password then
sends the password RSA-encrypted with the server's public key (the cryptography library). TLS when tls is true, the
certificate checked against the CA and the host's name. PyMySQL (pure Python)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_insights_db
short_description: Write the ticket insights' figures to the report database, and read the last months back
description:
  - mode read - the newest run of each of the last months (the same instance and period), the figures the trend draws.
  - mode write - the schema when it is not there (create - the tables, the views, schema_version), the run upserted by its
    key (source, period, window end). A run of the same window replaces its nodes and figures. Runs are kept for ever.
  - tls false (the default) - plain TCP. caching_sha2_password then needs the cryptography library.
  - tls true - the server's certificate must be signed by the CA (ca_file, else the EE's trust store) and name the host.
    A session that is not encrypted is refused.
  - mode check (1.15.0) - read only, nothing made or written (the reader account is enough) - the server's version, the
    account and whether it may write, the tables and views there or missing, and what is held per instance and period.
  - After a write, what the database holds is read back (held - a SELECT, not the rows sent).
  - mode tickets_load (1.17.0) - the ticket store's rows of this instance (insights_ticket) written to path, for the read.
  - mode tickets_save (1.17.0) - the rows the read wrote to path upserted; the tickets done more than 800 days ago deleted,
    and (1.17.0b) those the light read did not see that were last updated more than 800 days ago.
  - The ticket store's and the ACT cache's three tables (1.17.0b) - when they are missing and cannot be made (no CREATE
    grant, or create false), the figures are read and written as before; tickets_load and act_load are ok false, with
    one warning naming the tables (warn_tables).
  - A database that cannot be reached is a warning and ok false - unless required is true, then the task fails.
options:
  mode: {description: "read, write, check; tickets_load, tickets_save (1.17.0, the ticket store); act_load, act_save (the ACT cache).", type: str,
         choices: [read, write, check, tickets_load, tickets_save, act_load, act_save], required: true}
  host: {description: The database server., type: str, required: true}
  port: {description: "Its port (the report database's: 3309).", type: int, default: 3309}
  database: {description: The schema (database) name., type: str, required: true}
  user: {description: The account., type: str, required: true}
  password: {description: Its password., type: str, required: true}
  tls: {description: Connect over TLS., type: bool, default: false}
  ca_file: {description: "tls: the CA that signed the server's certificate ('' = the EE's trust store).", type: str, default: ""}
  connect_timeout: {description: Seconds to connect., type: int, default: 10}
  create: {description: Create the tables and views when they are not there., type: bool, default: true}
  rows: {description: "write: one run (servicenow_insights_db_rows) or {runs: [...]} (servicenow_insights_db_runs).", type: dict}
  source: {description: "read: the instance (the run key's source).", type: str, default: ""}
  period: {description: "read: the period (month - the calendar months).", type: str, default: "month"}
  month: {description: "read: the newest month wanted (YYYY-MM); months back from it.", type: str, default: ""}
  months: {description: "read: how many months back.", type: int, default: 12}
  metrics: {description: "read: the metrics wanted.", type: list, elements: str, default: []}
  job: {description: "write: the AAP job, kept on the run row.", type: str, default: ""}
  required: {description: "true: a database that cannot be reached fails the task.", type: bool, default: false}
  path: {description: "tickets_load, tickets_save (1.17.0): the file the ticket store's rows are written to or read from.", type: path, default: ""}
  cache_key: {description: "act_load, act_save (1.17.0): the provider, the model and the prompt's hash.", type: str, default: ""}
  clear: {description: "act_load: nothing held is given; act_save: everything held of the instance deleted first (servicenow_insights_act_cache_clear).", type: bool, default: false}
  warn: {description: "false: a database not reached is not warned of here (the figures' read warns once, later).", type: bool, default: true}
  warn_tables: {description: "tickets_load, act_load (1.17.0b): warn when the store's three tables are missing and cannot be made.", type: bool, default: true}
'''

RETURN = r'''
ok: {description: Reached and done., type: bool}
error: {description: "Why not ('' = done).", type: str}
tls: {description: "The session's TLS version and cipher, or none.", type: str}
auth: {description: The account's authentication plugin., type: str}
created: {description: The tables and views were made this run., type: bool}
schema_version: {description: The schema's version., type: int}
replaced: {description: "write: a run of the same window was there and was replaced.", type: bool}
written: {description: "write: {runs, nodes, figures}.", type: dict}
store: {description: "tickets_load, tickets_save (1.17.0): {held (rows of this instance now), saved, pruned}.", type: dict}
act: {description: "act_load (1.17.0): {issues, classes}; act_save: {issues (written), classes (held now)}.", type: dict}
rows: {description: "read: [[month, window_end, node, variant, metric, value, partial]].", type: list}
months: {description: "write: the calendar months written (the month so far marked).", type: list}
tickets: {description: "write: each month's tickets and rows, in words (the backfill's progress).", type: str}
kept: {description: "write: months not written - their complete figures are kept over a partial run's.", type: list}
months_kept: {description: "read: the months with a run of this instance and period.", type: int}
run_id: {description: "write: the first run written (the window's).", type: int}
held: {description: "write, check: [{source, period, runs, months, complete, first, last, newest}] - read back from insights_run.", type: list}
version: {description: "check: the server's version.", type: str}
account: {description: "check: the account as the server knows it, and can_write (INSERT granted).", type: dict}
objects: {description: "check: {present: [...], missing: [...]} - the tables and views.", type: dict}
store_missing: {description: "1.17.0b: the ticket store's and the ACT cache's tables that are not there (and could not be made).", type: list}
'''

import datetime
import json
import os
import re
import ssl

from ansible.module_utils.basic import AnsibleModule

try:
    import pymysql
    HAS_PYMYSQL = True
except ImportError:
    HAS_PYMYSQL = False

SCHEMA_VERSION = 1
COMPONENT = "servicenow_insights"
NODE_COLUMNS = ("incidents", "requests", "resolved", "backlog", "sla_met_pct", "median_resolve_h")
# a view per figure set (Grafana, a query): a row per run and node, a column per figure - the names
# servicenow_insights_history.scope_figures writes. The figures with an item (outcome, aging_bdays, category, hot_ci)
# are in insights_v_labelled.
FIGURE_SETS = (
    ("service", ("sla_met_pct", "response_met_pct", "fcr_pct", "fcr_desk_pct", "reopen_pct", "reopen7_pct", "median_resolve_h")),
    ("volume", ("incidents", "requests", "changes", "problems", "resolved", "backlog", "open_old")),
    ("process", ("hops_over_pct", "hops_open", "hold_open", "hold_no_reason", "at_risk", "breached", "handoff_pct", "pingpong_pct",
                 "matrix_off_pct", "closure_late", "changes_failed", "changes_backed_out", "ctasks_overdue")),
    ("lifecycle", ("state_new_h", "state_progress_h", "state_hold_h", "state_resolved_h", "hold_share_pct")),
    ("quality", ("notes_good_pct", "notes_thin_pct", "notes_bare_pct", "filed_other_pct", "filed_no_ci_pct", "filed_short_pct",
                 "after_hours_pct", "survey_avg", "survey_n")),
)
TABLE_OPTS = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"
CREATE = [
    "CREATE TABLE IF NOT EXISTS schema_version (component VARCHAR(64) NOT NULL PRIMARY KEY, version INT NOT NULL, "
    "applied_at DATETIME NOT NULL) " + TABLE_OPTS,
    "CREATE TABLE IF NOT EXISTS insights_run (run_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, "
    "source VARCHAR(190) NOT NULL, period VARCHAR(16) NOT NULL, window_start DATETIME NOT NULL, window_end DATETIME NOT NULL, "
    "month CHAR(7) NOT NULL, partial TINYINT NOT NULL DEFAULT 0, data_end DATETIME NULL, written_at DATETIME NOT NULL, job VARCHAR(64) NOT NULL DEFAULT '', "
    "UNIQUE KEY run_key (source, period, window_end)) " + TABLE_OPTS,
    "CREATE TABLE IF NOT EXISTS insights_node (run_id BIGINT UNSIGNED NOT NULL, variant VARCHAR(8) NOT NULL DEFAULT '', "
    "node VARCHAR(255) NOT NULL, label VARCHAR(190) NOT NULL DEFAULT '', level VARCHAR(16) NOT NULL DEFAULT '', "
    "parent VARCHAR(255) NOT NULL DEFAULT '', depth INT NOT NULL DEFAULT 0, incidents INT NULL, requests INT NULL, "
    "resolved INT NULL, backlog INT NULL, sla_met_pct DECIMAL(6,2) NULL, median_resolve_h DECIMAL(12,2) NULL, "
    "PRIMARY KEY (run_id, variant, node)) " + TABLE_OPTS,
    "CREATE TABLE IF NOT EXISTS insights_figure (run_id BIGINT UNSIGNED NOT NULL, variant VARCHAR(8) NOT NULL DEFAULT '', "
    "node VARCHAR(255) NOT NULL, metric VARCHAR(64) NOT NULL, label VARCHAR(190) NOT NULL DEFAULT '', value DOUBLE NOT NULL, "
    "PRIMARY KEY (run_id, variant, node, metric, label), KEY by_metric (metric, node)) " + TABLE_OPTS,
    # 1.17.0, the ticket store (servicenow_insights_incremental): a row per ticket - its numbers, times, codes, flags and
    # group, and its follow-up reads' results; never its text, its people or its CI (the light read gives those each run)
    "CREATE TABLE IF NOT EXISTS insights_ticket (source VARCHAR(190) NOT NULL, sys_id VARCHAR(64) NOT NULL, tbl VARCHAR(40) NOT NULL, "
    "number VARCHAR(40) NOT NULL DEFAULT '', updated_on DATETIME NULL, active TINYINT NOT NULL DEFAULT 1, opened_at DATETIME NULL, "
    "done_at DATETIME NULL, grp VARCHAR(255) NOT NULL DEFAULT '', code VARCHAR(8) NOT NULL, fields JSON NOT NULL, follow JSON NOT NULL, "
    "written_at DATETIME NOT NULL, PRIMARY KEY (source, sys_id), KEY by_done (source, done_at)) " + TABLE_OPTS,
    # 1.17.0, the ACT cache: each description's issue (its key as a SHA-256, never the words) and the issues - by ACT's short
    # name, masked (the one text the database holds); cache_key: the provider, the model and the prompt's hash
    "CREATE TABLE IF NOT EXISTS insights_act_issue (source VARCHAR(190) NOT NULL, cache_key VARCHAR(190) NOT NULL, issue_no INT NOT NULL, "
    "label VARCHAR(80) NOT NULL, kind VARCHAR(16) NOT NULL DEFAULT 'incident', self_service TINYINT NOT NULL DEFAULT 0, "
    "written_at DATETIME NOT NULL, PRIMARY KEY (source, cache_key, issue_no)) " + TABLE_OPTS,
    "CREATE TABLE IF NOT EXISTS insights_act_class (source VARCHAR(190) NOT NULL, cache_key VARCHAR(190) NOT NULL, key_hash CHAR(64) NOT NULL, "
    "issue_no INT NOT NULL, written_at DATETIME NOT NULL, PRIMARY KEY (source, cache_key, key_hash)) " + TABLE_OPTS,
]
RUN_COLS = "r.run_id, r.source, r.period, r.window_start AS period_start, r.window_end AS period_end, r.month, r.partial"
NODE_COLS = "n.node, n.variant, n.label, n.level, n.depth, n.parent"
FIGURES_JOIN = ("FROM insights_run r JOIN insights_node n ON n.run_id = r.run_id LEFT JOIN insights_figure f ON f.run_id = n.run_id "
                "AND f.variant = n.variant AND f.node = n.node AND f.label = ''")
VIEWS = [
    # the newest run of each month, per instance and period: what the page's trend draws
    "CREATE VIEW insights_v_month_run AS SELECT * FROM (SELECT %s, ROW_NUMBER() OVER (PARTITION BY r.source, r.period, "
    "r.month ORDER BY r.window_end DESC) AS newest FROM insights_run r) x "
    "WHERE newest = 1" % RUN_COLS,
    # the newest run, per instance and period: a "now" panel
    "CREATE VIEW insights_v_latest_run AS SELECT * FROM (SELECT %s, ROW_NUMBER() OVER (PARTITION BY r.source, r.period "
    "ORDER BY r.window_end DESC) AS newest FROM insights_run r) x WHERE newest = 1" % RUN_COLS,
    "CREATE VIEW insights_v_labelled AS SELECT %s, %s, f.metric, f.label AS item, f.value FROM insights_run r "
    "JOIN insights_node n ON n.run_id = r.run_id JOIN insights_figure f ON f.run_id = n.run_id AND f.variant = n.variant "
    "AND f.node = n.node AND f.label <> ''" % (RUN_COLS, NODE_COLS),
] + ["CREATE VIEW insights_v_%s AS SELECT %s, %s, %s %s GROUP BY r.run_id, n.run_id, n.variant, n.node" % (
     name, RUN_COLS, NODE_COLS, ", ".join("MAX(CASE WHEN f.metric = '%s' THEN f.value END) AS %s" % (k, k) for k in keys), FIGURES_JOIN)
     for name, keys in FIGURE_SETS]
# 1.17.0: the ticket store's and the ACT cache's tables. A 1.16.0 database whose writer cannot make them (no CREATE grant,
# or servicenow_insights_db_create false) keeps its figures: the store and the cache are off until they are made.
STORE_TABLES = ("insights_ticket", "insights_act_issue", "insights_act_class")
OBJECTS = (["schema_version", "insights_run", "insights_node", "insights_figure", "insights_ticket", "insights_act_issue", "insights_act_class",
            "insights_v_month_run", "insights_v_latest_run",
            "insights_v_labelled"] + ["insights_v_" + name for name, _ in FIGURE_SETS])


def pretty(sql):
    """One statement as docs/REPORT_DATABASE.md shows it (the tables made by hand): a table's column or key per line; a
    view's selected columns one per line, FROM, the joins, WHERE and GROUP BY each on its own line."""
    def split0(text, depth0=0):
        parts, depth, cur = [], 0, ""
        for ch in text:
            depth += {"(": 1, ")": -1}.get(ch, 0)
            if ch == "," and depth == depth0:
                parts.append(cur.strip())
                cur = ""
            else:
                cur += ch
        return parts + [cur.strip()]
    if sql.startswith("CREATE TABLE"):
        head, rest = sql.split(" (", 1)
        body, tail = rest.rsplit(") ", 1)
        return "%s (\n  %s\n) %s;" % (head, ",\n  ".join(split0(body)), tail)
    head, rest = sql.split(" AS SELECT ", 1)
    sel, frm = (rest.split(" FROM ", 1) + [""])[:2] if not rest.startswith("* ") else ("*", rest[len("* FROM "):])
    text = "%s AS SELECT %s\nFROM %s" % (head, ",\n  ".join(split0(sel)) if sel != "*" else "*", frm)
    text = re.sub(r"(?<!LEFT) JOIN ", "\nJOIN ", text.replace(" LEFT JOIN ", "\nLEFT JOIN "))
    text = text.replace(", ROW_NUMBER() OVER", ",\n  ROW_NUMBER() OVER").replace(" FROM insights_run r) x", "\n  FROM insights_run r) x")
    for kw in (" WHERE ", " GROUP BY "):
        text = text.replace(kw, "\n" + kw.strip() + " ")
    return text + ";"


def ddl():
    """Every statement the first run sends (create): the tables, the views and schema_version's row - for doing it by
    hand (servicenow_insights_db_create: false). The doc carries this text; a test keeps them alike."""
    return "\n\n".join([pretty(x) for x in CREATE + VIEWS] + [
        "INSERT INTO schema_version (component, version, applied_at) VALUES ('%s', %d, UTC_TIMESTAMP());" % (COMPONENT, SCHEMA_VERSION)])


def ts(text):
    t = str(text or "").strip().replace("T", " ")
    for fmt, n in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)):
        try:
            return datetime.datetime.strptime(t[:n], fmt)
        except ValueError:
            pass
    raise ValueError("not a date and time: %r" % text)


def months_back(last, n):
    """'2026-10', 3 -> ['2026-08', '2026-09', '2026-10']."""
    y, m = int(last[:4]), int(last[5:7])
    out = []
    for _ in range(max(1, n)):
        out.append("%04d-%02d" % (y, m))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out[::-1]


def month_of(t):
    return (t - datetime.timedelta(seconds=1)).strftime("%Y-%m")


def connect(p):
    """A session - plain TCP, or (tls) TLS with the certificate checked (the CA and the host's name) - or an exception."""
    ctx = None
    if p["tls"]:
        ca = p["ca_file"] if p["ca_file"] and os.path.isfile(p["ca_file"]) and os.path.getsize(p["ca_file"]) > 0 else None
        ctx = ssl.create_default_context(cafile=ca)          # no CA on the credential: the EE's trust store
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
    kw = dict(host=p["host"], port=p["port"], user=p["user"], password=p["password"], database=p["database"],
              connect_timeout=p["connect_timeout"], autocommit=False, charset="utf8mb4")
    if ctx is not None:
        conn = pymysql.connect(ssl=ctx, **kw)
    else:
        conn = connect_plain(kw)
    cur = conn.cursor()
    cur.execute("SHOW SESSION STATUS WHERE Variable_name IN ('Ssl_version', 'Ssl_cipher')")
    st = dict((str(k), str(v or "")) for k, v in cur.fetchall())
    if p["tls"] and not st.get("Ssl_cipher"):
        conn.close()
        raise RuntimeError("the session is not encrypted - the server must offer TLS")
    return conn, ("%s %s" % (st.get("Ssl_version", ""), st.get("Ssl_cipher", ""))) if st.get("Ssl_cipher") else "none"


PYMYSQL_AUTH_BUG = ("PyMySQL %s has a bug in the caching_sha2_password login over plain TCP (1.2.1 to 1.2.3: \"'NoneType' "
                    "object has no attribute 'is_auth_switch_request'\"). Launch again: the second login usually works. "
                    "The lasting fix: PyMySQL 1.2.4 or later in the execution environment (ee/*/requirements.txt). First error: %s")


def connect_plain(kw):
    """Plain TCP. PyMySQL 1.2.1 to 1.2.3 raise AttributeError on the first caching_sha2_password login after the server's
    password cache is empty (a restart, FLUSH PRIVILEGES); the server has accepted the login by then, so the second takes
    the fast path. One more try, never more."""
    def once():
        try:                    # PyMySQL 1.1+ would use TLS unchecked when the server offers it: plain TCP, as asked
            return pymysql.connect(ssl_disabled=True, **kw)
        except TypeError:       # an older PyMySQL: plain TCP is its default
            return pymysql.connect(**kw)
    try:
        return once()
    except AttributeError as first:
        try:
            return once()
        except AttributeError:
            raise RuntimeError(PYMYSQL_AUTH_BUG % (getattr(pymysql, "VERSION_STRING", "?"), first))


def auth_plugin(conn):
    """The plugin the login used (caching_sha2_password on MySQL 8.4): mysql.user when the account may read it, else
    what the server named in the handshake."""
    cur = conn.cursor()
    try:
        cur.execute("SELECT plugin FROM mysql.user WHERE CONCAT(user, '@', host) = CURRENT_USER()")
        r = cur.fetchone()
        if r:
            return str(r[0])
    except Exception:        # no SELECT on mysql.user (the usual case)
        pass
    v = getattr(conn, "_auth_plugin_name", "") or ""
    return v.decode() if isinstance(v, bytes) else str(v)


def schema(conn, create, read_only=False):
    """(version, created, store): the tables when they are not there (and create is true). store (1.17.0b): {missing:
    the store's tables still not there, error: why they could not be made} - the figures go on without them."""
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name IN (%s)" % (
        ", ".join(["%s"] * len(OBJECTS))), OBJECTS)
    have = set(str(x[0]) for x in cur.fetchall())
    core = [o for o in OBJECTS if o not in have and o not in STORE_TABLES]
    store = {"missing": [o for o in STORE_TABLES if o not in have], "error": ""}
    created = False
    if core:
        if not create and read_only:
            return 0, False, store              # nothing kept yet
        if not create:
            raise RuntimeError("the tables are not there (servicenow_insights_db_create: false) - make them once, docs/REPORT_DATABASE.md")
        for name, sql in zip(OBJECTS, CREATE + VIEWS):     # only what is missing: no DROP grant needed
            if name in core:
                cur.execute(sql)
        created = True
    if store["missing"] and create:
        for name, sql in zip(OBJECTS, CREATE + VIEWS):
            if name in store["missing"]:
                try:
                    cur.execute(sql)
                except Exception as e:      # noqa: BLE001 - no CREATE grant: the figures go on, the store is off
                    store["error"] = short_error(e)
                    break
                store["missing"].remove(name)
                created = True
    cur.execute("SELECT version FROM schema_version WHERE component = %s", (COMPONENT,))
    r = cur.fetchone()
    if r is None:
        cur.execute("INSERT INTO schema_version (component, version, applied_at) VALUES (%s, %s, UTC_TIMESTAMP())", (COMPONENT, SCHEMA_VERSION))
        conn.commit()
        return SCHEMA_VERSION, created, store
    if int(r[0]) > SCHEMA_VERSION:
        raise RuntimeError("the schema is version %s, newer than this release knows (%s) - update site-automation" % (r[0], SCHEMA_VERSION))
    conn.commit()
    return int(r[0]), created, store


def store_tables_say(store, create):
    """The one warning when the store's tables are missing (1.17.0b)."""
    why = (" and this account cannot make them (%s)" % store["error"]) if store.get("error") else (
        "" if create else " (servicenow_insights_db_create: false)")
    return ("the tables insights_ticket, insights_act_issue and insights_act_class are not there%s - the ticket store and the ACT cache "
            "are off this run, the figures are written as before. Make the three tables once (docs/REPORT_DATABASE.md, Making the "
            "tables by hand), or grant the writer CREATE on the database" % why)


def write_run(cur, rows, job):
    """One run upserted by its key (source, period, window end): its nodes and figures replaced. A month's partial
    figures (the month so far) never replace that month's complete ones. -> (replaced, skipped, nodes, figures)."""
    run = rows.get("run") or {}
    if not run.get("source") or not run.get("period") or not run.get("window_end"):
        raise ValueError("a run needs source, period and window_end")
    we, ws = ts(run["window_end"]), ts(run.get("window_start") or run["window_end"])
    month = str(run.get("month") or "")[:7] or month_of(we)
    partial = 1 if run.get("partial") else 0
    de = ts(run.get("data_end") or run["window_end"])
    cur.execute("SELECT run_id, partial FROM insights_run WHERE source = %s AND period = %s AND window_end = %s FOR UPDATE",
                (run["source"], run["period"], we))
    r = cur.fetchone()
    if r and partial and not int(r[1]):
        return False, True, 0, 0, 0
    if r:
        run_id = int(r[0])
        cur.execute("UPDATE insights_run SET window_start = %s, month = %s, partial = %s, data_end = %s, written_at = UTC_TIMESTAMP(), "
                    "job = %s WHERE run_id = %s", (ws, month, partial, de, job, run_id))
        cur.execute("DELETE FROM insights_figure WHERE run_id = %s", (run_id,))
        cur.execute("DELETE FROM insights_node WHERE run_id = %s", (run_id,))
    else:
        cur.execute("INSERT INTO insights_run (source, period, window_start, window_end, month, partial, data_end, written_at, job) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, UTC_TIMESTAMP(), %s)", (run["source"], run["period"], ws, we, month, partial, de, job))
        run_id = int(cur.lastrowid)
    nodes = [[run_id, str(x[1] or ""), str(x[0])] + list(x[2:5]) + [int(x[5] or 0)] + [
        None if v is None else (int(round(v)) if i < 4 else round(float(v), 2)) for i, v in enumerate(x[6:6 + len(NODE_COLUMNS)])]
        for x in rows.get("nodes") or []]
    figs = [[run_id, str(x[1] or ""), str(x[0]), str(x[2]), str(x[3] or ""), float(x[4])] for x in rows.get("figures") or []]
    if nodes:
        cur.executemany("INSERT INTO insights_node (run_id, variant, node, label, level, parent, depth, %s) VALUES (%s)" % (
            ", ".join(NODE_COLUMNS), ", ".join(["%s"] * (7 + len(NODE_COLUMNS)))), nodes)
    for i in range(0, len(figs), 2000):
        cur.executemany("INSERT INTO insights_figure (run_id, variant, node, metric, label, value) VALUES (%s, %s, %s, %s, %s, %s)",
                        figs[i:i + 2000])
    return bool(r), False, len(nodes), len(figs), run_id


def write(conn, p):
    """rows: one run ({run, nodes, figures}) or {runs: [...]} - all in one transaction."""
    rows = p["rows"] or {}
    runs = rows.get("runs") if "runs" in rows else [rows]
    cur = conn.cursor()
    out = {"replaced": False, "written": {"runs": 0, "nodes": 0, "figures": 0}, "months": [], "kept": [], "tickets": "", "run_id": 0,
           "cut": []}
    said = []
    for one in runs or []:
        if one.get("cut"):          # 1.16.0: records cut (servicenow_insights_max_records) - incomplete, never written
            run = one.get("run") or {}
            out["cut"].append(run.get("month") if run.get("period") == "month" else "the window")
            continue
        replaced, skipped, nn, nf, rid = write_run(cur, one, str(p["job"])[:64])
        out["run_id"] = out["run_id"] or rid
        run = one.get("run") or {}
        if skipped:
            out["kept"].append(run.get("month"))
            continue
        out["replaced"] = out["replaced"] or replaced
        out["written"]["runs"] += 1
        out["written"]["nodes"] += nn
        out["written"]["figures"] += nf
        if run.get("period") == "month":
            out["months"].append(run.get("month") + (" (so far)" if run.get("partial") else ""))
        t = one.get("tickets") or {}
        if t:
            said.append("%s: %s incidents, %s requested items (%s records) - %s org node rows, %s figures%s" % (
                run.get("month"), t.get("incidents", 0), t.get("requests", 0), t.get("items", 0), nn, nf,
                ", replaced" if replaced else ""))
    out["tickets"] = "; ".join(said)
    conn.commit()
    out["held"] = held(conn)            # read back: what the database holds now, not what was sent
    return out


def held(conn):
    """What insights_run holds, per instance and period: [{source, period, runs, months, complete, first, last, newest}]."""
    cur = conn.cursor()
    cur.execute("SELECT source, period, COUNT(*), COUNT(DISTINCT month), COUNT(DISTINCT CASE WHEN partial = 0 THEN month END), "
                "MIN(month), MAX(month), MAX(written_at) FROM insights_run GROUP BY source, period ORDER BY source, period")
    out = [{"source": str(a), "period": str(b), "runs": int(c), "months": int(d), "complete": int(e), "first": str(f or ""), "last": str(g or ""),
            "newest": h.strftime("%Y-%m-%d %H:%M:%S UTC") if h else ""} for a, b, c, d, e, f, g, h in cur.fetchall()]
    conn.commit()
    return out


def check(conn):
    """Read only (the reader account is enough): the server, the account, the objects, what is held."""
    cur = conn.cursor()
    cur.execute("SELECT VERSION(), CURRENT_USER()")
    version, account = cur.fetchone()
    grants = []
    try:
        cur.execute("SHOW GRANTS FOR CURRENT_USER()")
        grants = [str(x[0]) for x in cur.fetchall()]
    except Exception:           # noqa: BLE001 - not allowed: unknown
        pass
    can_write = any(re.search(r"\b(INSERT|ALL PRIVILEGES)\b", g) for g in grants) if grants else None
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name IN (%s)" % (
        ", ".join(["%s"] * len(OBJECTS))), OBJECTS)
    have = set(str(x[0]) for x in cur.fetchall())
    out = {"version": str(version), "account": {"name": str(account), "can_write": can_write},
           "objects": {"present": [o for o in OBJECTS if o in have], "missing": [o for o in OBJECTS if o not in have]}, "held": [], "schema_version": 0}
    if "schema_version" in have:
        cur.execute("SELECT version FROM schema_version WHERE component = %s", (COMPONENT,))
        r = cur.fetchone()
        out["schema_version"] = int(r[0]) if r else 0
    if "insights_run" in have:
        out["held"] = held(conn)
    conn.commit()
    return out


def read(conn, p):
    """The calendar months (period month) of the last months up to month: [[month, window_end, node, variant, metric,
    value, partial]]."""
    if not p["month"]:
        raise ValueError("read needs month (YYYY-MM)")
    want = months_back(p["month"], p["months"])
    cur = conn.cursor()
    cur.execute("SELECT run_id, window_end, month, partial FROM insights_run WHERE source = %s AND period = %s AND month IN (%s)" % (
        "%s", "%s", ", ".join(["%s"] * len(want))), [p["source"], p["period"] or "month"] + want)
    runs = dict((int(a), (str(c), b.strftime("%Y-%m-%d %H:%M:%S"), int(d))) for a, b, c, d in cur.fetchall())
    out = []
    if runs and p["metrics"]:
        ids = list(runs)
        cur.execute("SELECT run_id, node, variant, metric, value FROM insights_figure WHERE run_id IN (%s) AND label = '' AND metric IN (%s)" % (
            ", ".join(["%s"] * len(ids)), ", ".join(["%s"] * len(p["metrics"]))), ids + list(p["metrics"]))
        out = [[runs[int(a)][0], runs[int(a)][1], b, c, d, float(e), runs[int(a)][2]] for a, b, c, d, e in cur.fetchall()]
    cur.execute("SELECT COUNT(DISTINCT month) FROM insights_run WHERE source = %s AND period = %s AND partial = 0", (p["source"], p["period"] or "month"))
    kept = int(cur.fetchone()[0])
    conn.commit()
    return {"rows": out, "months_kept": kept}


def short_error(e):
    a = getattr(e, "args", None) or ()
    if len(a) >= 2 and isinstance(a[0], int):
        return "%s (MySQL error %s)" % (str(a[1]).strip(), a[0])
    return str(e).strip() or e.__class__.__name__


# 1.17.0: a ticket done more than this long ago is out of every window a run can ask (12 months and the 12 before)
TICKET_KEEP_DAYS = 800


def _dt(text):
    try:
        return ts(text) if str(text or "").strip() else None
    except ValueError:
        return None


def tickets_load(conn, p):
    """The rows the database holds for this instance -> the file at path ({rows: {sys_id: {u, c, i, f}}}), for the read."""
    cur = conn.cursor()
    cur.execute("SELECT sys_id, updated_on, code, fields, follow FROM insights_ticket WHERE source = %s", (p["source"],))
    rows = {}
    for sid, upd, code, fields, follow in cur.fetchall():
        try:
            rows[str(sid)] = {"u": upd.strftime("%Y-%m-%d %H:%M:%S") if upd else "", "c": str(code),
                              "i": json.loads(fields) if isinstance(fields, (str, bytes)) else fields,
                              "f": json.loads(follow) if isinstance(follow, (str, bytes)) else follow}
        except ValueError:
            continue
    conn.commit()
    with open(p["path"], "w") as f:
        json.dump({"rows": rows}, f)
    return {"store": {"held": len(rows), "saved": 0, "pruned": 0}}


def tickets_save(conn, p, chunk=500):
    """The rows the read wrote to path, upserted by (source, sys_id); the tickets done more than TICKET_KEEP_DAYS ago
    deleted, and (1.17.0b) the ones the light read did not see (deleted in ServiceNow, moved out of the report while open:
    no done_at) last updated more than TICKET_KEEP_DAYS ago."""
    t0 = datetime.datetime.now()
    with open(p["path"]) as f:
        got = json.load(f) or {}
    rows = got.get("rows") or []
    cur = conn.cursor()
    sql = ("INSERT INTO insights_ticket (source, sys_id, tbl, number, updated_on, active, opened_at, done_at, grp, code, fields, follow, written_at) "
           "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, UTC_TIMESTAMP()) ON DUPLICATE KEY UPDATE tbl = VALUES(tbl), "
           "number = VALUES(number), updated_on = VALUES(updated_on), active = VALUES(active), opened_at = VALUES(opened_at), "
           "done_at = VALUES(done_at), grp = VALUES(grp), code = VALUES(code), fields = VALUES(fields), follow = VALUES(follow), "
           "written_at = VALUES(written_at)")
    for i in range(0, len(rows), chunk):
        cur.executemany(sql, [(p["source"], r["s"], r["t"], str(r.get("n") or "")[:40], _dt(r.get("u")), 1 if r.get("a") else 0,
                               _dt(r.get("o")), _dt(r.get("d")), str(r.get("g") or "")[:255], str(r["c"]), json.dumps(r["i"]),
                               json.dumps(r.get("f") or {})) for r in rows[i:i + chunk]])
    cur.execute("DELETE FROM insights_ticket WHERE source = %s AND done_at < UTC_TIMESTAMP() - INTERVAL %d DAY" % ("%s", TICKET_KEEP_DAYS),
                (p["source"],))
    pruned = int(cur.rowcount or 0)
    if "seen" in got:           # the light read's tickets (a file without them: this prune waits for the next run)
        seen = set(str(x) for x in got.get("seen") or [])
        cur.execute("SELECT sys_id FROM insights_ticket WHERE source = %%s AND updated_on < UTC_TIMESTAMP() - INTERVAL %d DAY" % TICKET_KEEP_DAYS,
                    (p["source"],))
        gone = [str(x[0]) for x in cur.fetchall() if str(x[0]) not in seen]
        for i in range(0, len(gone), chunk):
            part = gone[i:i + chunk]
            cur.execute("DELETE FROM insights_ticket WHERE source = %%s AND sys_id IN (%s)" % ", ".join(["%s"] * len(part)), [p["source"]] + part)
            pruned += int(cur.rowcount or 0)
    cur.execute("SELECT COUNT(*) FROM insights_ticket WHERE source = %s", (p["source"],))
    held = int(cur.fetchone()[0])
    conn.commit()
    return {"store": {"held": held, "saved": len(rows), "pruned": pruned, "seconds": round((datetime.datetime.now() - t0).total_seconds(), 1)}}


def act_load(conn, p):
    """The ACT cache of this instance and cache key -> {issues: [{no, label, kind, self_service}], classes: {key hash: no}}
    (clear: nothing - the run asks everything, act_save then replaces it)."""
    if p["clear"]:
        return {"act": {"issues": [], "classes": {}}}
    cur = conn.cursor()
    cur.execute("SELECT issue_no, label, kind, self_service FROM insights_act_issue WHERE source = %s AND cache_key = %s ORDER BY issue_no",
                (p["source"], p["cache_key"]))
    issues = [{"no": int(a), "label": str(b), "kind": str(c), "self_service": bool(d)} for a, b, c, d in cur.fetchall()]
    cur.execute("SELECT key_hash, issue_no FROM insights_act_class WHERE source = %s AND cache_key = %s", (p["source"], p["cache_key"]))
    classes = dict((str(a), int(b)) for a, b in cur.fetchall())
    conn.commit()
    return {"act": {"issues": issues, "classes": classes}}


def act_save(conn, p, chunk=500):
    """This run's issues and description keys (rows: {issues, classes}) into the ACT cache: upserted; another cache key's
    rows of this instance (another model or prompt), and with clear everything held before, deleted first - the deletes
    and the writes in one transaction (a failure leaves the cache as it was). act_cache.yml sends clear only when ACT
    answered every grouping question (1.17.0b)."""
    rows = p["rows"] or {}
    cur = conn.cursor()
    for t in ("insights_act_issue", "insights_act_class"):
        if p["clear"]:
            cur.execute("DELETE FROM %s WHERE source = %%s" % t, (p["source"],))
        else:
            cur.execute("DELETE FROM %s WHERE source = %%s AND cache_key <> %%s" % t, (p["source"], p["cache_key"]))
    cur.executemany("INSERT INTO insights_act_issue (source, cache_key, issue_no, label, kind, self_service, written_at) VALUES "
                    "(%s, %s, %s, %s, %s, %s, UTC_TIMESTAMP()) ON DUPLICATE KEY UPDATE label = VALUES(label), kind = VALUES(kind), "
                    "self_service = VALUES(self_service), written_at = VALUES(written_at)",
                    [(p["source"], p["cache_key"], int(x["no"]), str(x["label"])[:80], str(x.get("kind") or "incident")[:16],
                      1 if x.get("self_service") else 0) for x in rows.get("issues") or []])
    cl = rows.get("classes") or []
    for i in range(0, len(cl), chunk):
        cur.executemany("INSERT INTO insights_act_class (source, cache_key, key_hash, issue_no, written_at) VALUES (%s, %s, %s, %s, UTC_TIMESTAMP()) "
                        "ON DUPLICATE KEY UPDATE issue_no = VALUES(issue_no), written_at = VALUES(written_at)",
                        [(p["source"], p["cache_key"], str(h)[:64], int(n)) for h, n in cl[i:i + chunk]])
    cur.execute("SELECT COUNT(*) FROM insights_act_class WHERE source = %s AND cache_key = %s", (p["source"], p["cache_key"]))
    held = int(cur.fetchone()[0])
    conn.commit()
    return {"act": {"issues": len(rows.get("issues") or []), "classes": held}}


def main():
    module = AnsibleModule(
        argument_spec=dict(
            mode=dict(type="str", required=True, choices=["read", "write", "check", "tickets_load", "tickets_save", "act_load", "act_save"]), host=dict(type="str", required=True),
            port=dict(type="int", default=3309), database=dict(type="str", required=True), user=dict(type="str", required=True),
            password=dict(type="str", required=True, no_log=True), tls=dict(type="bool", default=False), ca_file=dict(type="str", default=""),
            connect_timeout=dict(type="int", default=10), create=dict(type="bool", default=True), rows=dict(type="dict"),
            source=dict(type="str", default=""), period=dict(type="str", default="month"), month=dict(type="str", default=""),
            months=dict(type="int", default=12), metrics=dict(type="list", elements="str", default=[]),
            job=dict(type="str", default=""), required=dict(type="bool", default=False), path=dict(type="path", default=""),
            warn=dict(type="bool", default=True), cache_key=dict(type="str", default=""), clear=dict(type="bool", default=False),
            warn_tables=dict(type="bool", default=True),
        ),
        supports_check_mode=True)
    p = module.params
    where = "%s:%s/%s" % (p["host"], p["port"], p["database"])
    res = {"changed": False, "ok": False, "error": "", "tls": "", "auth": "", "created": False, "schema_version": 0,
           "replaced": False, "written": {"runs": 0, "nodes": 0, "figures": 0}, "months": [], "kept": [], "tickets": "", "rows": [], "months_kept": 0, "where": where,
           "run_id": 0, "held": [], "version": "", "account": {}, "objects": {}, "store": {"held": 0, "saved": 0, "pruned": 0},
           "act": {"issues": [], "classes": {}}, "store_missing": []}

    def give_up(msg):
        res["error"] = msg
        if p["required"]:
            module.fail_json(msg="The report database %s: %s (servicenow_insights_db_required: true)" % (where, msg), **res)
        if p["warn"]:
            module.warn("The report database %s: %s - the report is whole, the history not %s this run (servicenow_insights_db_required: false)" % (
                where, msg, "read" if p["mode"] in ("read", "check") else "written"))
        module.exit_json(**res)

    if not HAS_PYMYSQL:
        give_up("PyMySQL is not in this execution environment (pip install PyMySQL; the all-in-one EE has it)")
    if p["mode"] in ("write", "tickets_save", "act_save") and module.check_mode:
        res.update(ok=True, error="check mode: nothing written")
        module.exit_json(**res)
    conn = None
    try:
        conn, res["tls"] = connect(p)
        res["auth"] = auth_plugin(conn)
        if p["mode"] == "check":            # 1.15.0: nothing made, nothing written
            res.update(check(conn))
            res["ok"] = True
            module.exit_json(**res)
        res["schema_version"], res["created"], store = schema(conn, p["create"] and not module.check_mode, p["mode"] in ("read", "tickets_load", "act_load"))
        res["store_missing"] = store["missing"]
        if store["missing"] and res["schema_version"] and p["mode"] in ("tickets_load", "tickets_save", "act_load", "act_save"):
            res["error"] = store_tables_say(store, p["create"] and not module.check_mode)
            if p["warn_tables"] and p["mode"] in ("tickets_load", "act_load"):
                module.warn("The report database %s: %s." % (where, res["error"]))
            res["changed"] = res["created"]
            module.exit_json(**res)
        if p["mode"] == "write":
            res.update(write(conn, p))
        elif p["mode"] == "tickets_save":
            res.update(tickets_save(conn, p))
        elif p["mode"] == "tickets_load":
            res.update(tickets_load(conn, p) if res["schema_version"] else {})
        elif p["mode"] == "act_save":
            res.update(act_save(conn, p))
        elif p["mode"] == "act_load":
            res.update(act_load(conn, p) if res["schema_version"] else {"act": {"issues": [], "classes": {}}})
        elif res["schema_version"]:
            res.update(read(conn, p))
        res["ok"] = True
        res["changed"] = p["mode"] in ("write", "tickets_save", "act_save") or res["created"]
    except ssl.SSLError as e:
        give_up("TLS: %s - the server's certificate must be signed by the CA and name %s" % (short_error(e), p["host"]))
    except Exception as e:      # unreachable, refused, a login or grant missing: one line, the job goes on
        msg = short_error(e)
        if "cryptography" in msg:
            msg = ("the login needs the cryptography library: without TLS, caching_sha2_password sends the password RSA-encrypted "
                   "(the all-in-one EE has it; docs/REPORT_DATABASE.md, The EE)")
        elif "secure transport" in msg.lower() and not p["tls"]:
            msg += " - the server wants TLS: set Use TLS on the Report database credential"
        elif p["tls"] and "support" in msg:
            msg += " - the server does not offer TLS: clear Use TLS on the Report database credential, or give the server a certificate"
        elif p["tls"] and ("SSL" in msg or "certificate" in msg.lower()):
            msg += " - the server's certificate must be signed by the CA and name %s" % p["host"]
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        give_up(msg)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    module.exit_json(**res)


if __name__ == "__main__":
    main()
