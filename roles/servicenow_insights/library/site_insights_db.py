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
  - A database that cannot be reached is a warning and ok false - unless required is true, then the task fails.
options:
  mode: {description: read or write., type: str, choices: [read, write], required: true}
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
rows: {description: "read: [[month, window_end, node, variant, metric, value, partial]].", type: list}
months: {description: "write: the calendar months written (the month so far marked).", type: list}
tickets: {description: "write: each month's tickets and rows, in words (the backfill's progress).", type: str}
kept: {description: "write: months not written - their complete figures are kept over a partial run's.", type: list}
months_kept: {description: "read: the months with a run of this instance and period.", type: int}
'''

import datetime
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
OBJECTS = (["schema_version", "insights_run", "insights_node", "insights_figure", "insights_v_month_run", "insights_v_latest_run",
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
        try:                    # PyMySQL 1.1+ would use TLS unchecked when the server offers it: plain TCP, as asked
            conn = pymysql.connect(ssl_disabled=True, **kw)
        except TypeError:       # an older PyMySQL: plain TCP is its default
            conn = pymysql.connect(**kw)
    cur = conn.cursor()
    cur.execute("SHOW SESSION STATUS WHERE Variable_name IN ('Ssl_version', 'Ssl_cipher')")
    st = dict((str(k), str(v or "")) for k, v in cur.fetchall())
    if p["tls"] and not st.get("Ssl_cipher"):
        conn.close()
        raise RuntimeError("the session is not encrypted - the server must offer TLS")
    return conn, ("%s %s" % (st.get("Ssl_version", ""), st.get("Ssl_cipher", ""))) if st.get("Ssl_cipher") else "none"


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
    """(version, created): the tables when they are not there (and create is true)."""
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name IN (%s)" % (
        ", ".join(["%s"] * len(OBJECTS))), OBJECTS)
    have = set(str(x[0]) for x in cur.fetchall())
    created = False
    if len(have) < len(OBJECTS):
        if not create and read_only:
            return 0, False                     # nothing kept yet
        if not create:
            raise RuntimeError("the tables are not there (servicenow_insights_db_create: false) - make them once, docs/REPORT_DATABASE.md")
        for name, sql in zip(OBJECTS, CREATE + VIEWS):     # only what is missing: no DROP grant needed
            if name not in have:
                cur.execute(sql)
        created = True
    cur.execute("SELECT version FROM schema_version WHERE component = %s", (COMPONENT,))
    r = cur.fetchone()
    if r is None:
        cur.execute("INSERT INTO schema_version (component, version, applied_at) VALUES (%s, %s, UTC_TIMESTAMP())", (COMPONENT, SCHEMA_VERSION))
        conn.commit()
        return SCHEMA_VERSION, created
    if int(r[0]) > SCHEMA_VERSION:
        raise RuntimeError("the schema is version %s, newer than this release knows (%s) - update site-automation" % (r[0], SCHEMA_VERSION))
    conn.commit()
    return int(r[0]), created


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
        return False, True, 0, 0
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
    return bool(r), False, len(nodes), len(figs)


def write(conn, p):
    """rows: one run ({run, nodes, figures}) or {runs: [...]} - all in one transaction."""
    rows = p["rows"] or {}
    runs = rows.get("runs") if "runs" in rows else [rows]
    cur = conn.cursor()
    out = {"replaced": False, "written": {"runs": 0, "nodes": 0, "figures": 0}, "months": [], "kept": [], "tickets": ""}
    said = []
    for one in runs or []:
        replaced, skipped, nn, nf = write_run(cur, one, str(p["job"])[:64])
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


def main():
    module = AnsibleModule(
        argument_spec=dict(
            mode=dict(type="str", required=True, choices=["read", "write"]), host=dict(type="str", required=True),
            port=dict(type="int", default=3309), database=dict(type="str", required=True), user=dict(type="str", required=True),
            password=dict(type="str", required=True, no_log=True), tls=dict(type="bool", default=False), ca_file=dict(type="str", default=""),
            connect_timeout=dict(type="int", default=10), create=dict(type="bool", default=True), rows=dict(type="dict"),
            source=dict(type="str", default=""), period=dict(type="str", default="month"), month=dict(type="str", default=""),
            months=dict(type="int", default=12), metrics=dict(type="list", elements="str", default=[]),
            job=dict(type="str", default=""), required=dict(type="bool", default=False),
        ),
        supports_check_mode=True)
    p = module.params
    where = "%s:%s/%s" % (p["host"], p["port"], p["database"])
    res = {"changed": False, "ok": False, "error": "", "tls": "", "auth": "", "created": False, "schema_version": 0,
           "replaced": False, "written": {"runs": 0, "nodes": 0, "figures": 0}, "months": [], "kept": [], "tickets": "", "rows": [], "months_kept": 0, "where": where}

    def give_up(msg):
        res["error"] = msg
        if p["required"]:
            module.fail_json(msg="The report database %s: %s (servicenow_insights_db_required: true)" % (where, msg), **res)
        module.warn("The report database %s: %s - the report is whole, the history not %s this run (servicenow_insights_db_required: false)" % (
            where, msg, "read" if p["mode"] == "read" else "written"))
        module.exit_json(**res)

    if not HAS_PYMYSQL:
        give_up("PyMySQL is not in this execution environment (pip install PyMySQL; the all-in-one EE has it)")
    if p["mode"] == "write" and module.check_mode:
        res.update(ok=True, error="check mode: nothing written")
        module.exit_json(**res)
    conn = None
    try:
        conn, res["tls"] = connect(p)
        res["auth"] = auth_plugin(conn)
        res["schema_version"], res["created"] = schema(conn, p["create"] and not module.check_mode, p["mode"] == "read")
        if p["mode"] == "write":
            res.update(write(conn, p))
        elif res["schema_version"]:
            res.update(read(conn, p))
        res["ok"] = True
        res["changed"] = p["mode"] == "write" or res["created"]
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
