#!/usr/bin/python
# -*- coding: utf-8 -*-
"""The portal's NetApp page (/portal/netapp): each run of NetApp - refresh volume list in the report database (MySQL; the
"Report database" credential, docs/REPORT_DATABASE.md) - every volume and aggregate it read (the volumes the expand job
offers with their survey choice, the others with their reason), a point per volume and aggregate for the growth, and the
health parts when they were read (SnapMirror, nodes, disks, error events, the busiest volumes and LUNs). The portal reads
the newest run with a read-only login. One call a run (roles/ontap_report/tasks/live_db.yml): makes the tables when they are
not there (ontap_live_db_create), writes this run in one transaction, works out each volume's and aggregate's growth from
the points of the last ontap_live_growth_days, deletes points older than ontap_live_db_keep_days and runs older than
ontap_live_db_keep_hours. Never fails the job: a database that is down, a login refused or a grant missing is one warning.
Needs PyMySQL in the execution environment."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_ontap_live_db
short_description: Keep each NetApp volume list run (volumes, aggregates, growth points, health parts) in the report database
description:
  - Writes the run (ontap_live_run), its volumes (ontap_live_volume), aggregates (ontap_live_aggr), a point per volume and
    aggregate (ontap_live_point) and its health parts (ontap_live_part) in one transaction - not in check mode.
  - Each volume's and aggregate's growth (GB a day) is the least-squares slope of its used space over the points of the last
    growth_days, written with the days until full (and, for an aggregate, until the expand job's limit).
  - Deletes points older than keep_days and runs older than keep_hours (the newest run is always kept).
  - The tables are made on the first run (create); with create false and no tables, nothing is written and error says so.
options:
  host: {description: The database server., type: str, required: true}
  port: {description: Its port., type: int, default: 3309}
  database: {description: The database., type: str, required: true}
  user: {description: The account., type: str, required: true}
  password: {description: Its password., type: str, required: true}
  tls: {description: Use TLS and check the server's certificate., type: bool, default: false}
  ca_file: {description: The CA file the certificate is checked with., type: str, default: ''}
  connect_timeout: {description: Seconds to wait for the connection., type: int, default: 10}
  read_timeout: {description: "Seconds to wait for each answer of the database (a server that takes the connection and then stops answering never stalls the refresh).", type: int, default: 20}
  create: {description: Make the tables when they are not there., type: bool, default: true}
  read_at: {description: "When this run read the clusters (2026-11-24T14:15:00Z).", type: str, required: true}
  job: {description: This job's ID (0 outside AAP)., type: int, default: 0}
  template_id: {description: This job's template's ID (0 outside AAP)., type: int, default: 0}
  run: {description: "The run (plugins/filter/ontap_live_filters.py ontap_live_payload): clusters, volumes, aggrs, parts, not_read.", type: dict, required: true}
  expand_template: {description: The expand job's template (the first of ontap_expand_templates)., type: str, default: ''}
  expand_question: {description: The variable of its Volume question (ontap_expand_question)., type: str, default: ''}
  opts: {description: "The page's settings: volume_warn_pct, volume_crit_pct, aggr_warn_pct, aggr_crit_pct, aggr_max_pct, days_warn, days_crit, min_points, min_hours, growth_days, min_gb, lag_warn_hours, lat_warn_ms, lat_crit_ms, ems_hours, health, perf.", type: dict, default: {}}
  keep_days: {description: Delete points older than this many days (0 = never)., type: int, default: 14}
  keep_hours: {description: Delete runs older than this many hours (the newest is kept)., type: int, default: 48}
author: site automation
'''

RETURN = r'''
ok: {description: The run was kept., type: bool}
error: {description: Why not., type: str}
run_id: {description: The run's row., type: int}
volumes: {description: Volume rows written., type: int}
aggrs: {description: Aggregate rows written., type: int}
points: {description: Points written., type: int}
parts: {description: Health parts written., type: list}
rated: {description: Volumes with a growth rate., type: int}
pruned: {description: "{points, runs} deleted.", type: dict}
created: {description: The tables were made this run., type: bool}
'''

import datetime
import json
import re

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils import site_report_db as db
from ansible.module_utils import site_ontap_live as L

SCHEMA_VERSION = 1
COMPONENT = "ontap_live"
TABLES = ("ontap_live_run", "ontap_live_volume", "ontap_live_aggr", "ontap_live_point", "ontap_live_part")
CREATE = (
    "CREATE TABLE IF NOT EXISTS ontap_live_run (run_id BIGINT NOT NULL AUTO_INCREMENT, read_at DATETIME NOT NULL, "
    "job BIGINT NOT NULL DEFAULT 0, template_id BIGINT NOT NULL DEFAULT 0, clusters TEXT NULL, not_read TEXT NULL, "
    "expand_template VARCHAR(255) NOT NULL DEFAULT '', expand_question VARCHAR(128) NOT NULL DEFAULT '', opts TEXT NULL, "
    "volumes INT NOT NULL DEFAULT 0, offered INT NOT NULL DEFAULT 0, "
    "PRIMARY KEY (run_id), KEY ontap_live_run_read_at (read_at)) " + db.TABLE_OPTS,
    "CREATE TABLE IF NOT EXISTS ontap_live_volume (run_id BIGINT NOT NULL, row_key CHAR(40) NOT NULL, address VARCHAR(191) NOT NULL, "
    "cluster VARCHAR(191) NOT NULL DEFAULT '', svm VARCHAR(191) NOT NULL DEFAULT '', volume VARCHAR(191) NOT NULL, "
    "lun VARCHAR(255) NOT NULL DEFAULT '', luns INT NOT NULL DEFAULT 0, size_b BIGINT NOT NULL DEFAULT 0, used_b BIGINT NOT NULL DEFAULT 0, "
    "aggr VARCHAR(191) NOT NULL DEFAULT '', aggr_free_b BIGINT NOT NULL DEFAULT 0, datastore VARCHAR(255) NOT NULL DEFAULT '', "
    "ds_kind VARCHAR(8) NOT NULL DEFAULT '', offered TINYINT NOT NULL DEFAULT 0, why VARCHAR(300) NOT NULL DEFAULT '', "
    "choice TEXT NULL, root TINYINT NOT NULL DEFAULT 0, full_size_b BIGINT NOT NULL DEFAULT 0, full_used_b BIGINT NOT NULL DEFAULT 0, "
    "full_src VARCHAR(16) NOT NULL DEFAULT '', thick TINYINT NOT NULL DEFAULT 0, pt_obj CHAR(40) NOT NULL DEFAULT '', growth_gb_day DOUBLE NULL, days_full DOUBLE NULL, "
    "rate_why VARCHAR(120) NOT NULL DEFAULT '', status VARCHAR(16) NOT NULL DEFAULT '', PRIMARY KEY (run_id, row_key)) " + db.TABLE_OPTS,
    "CREATE TABLE IF NOT EXISTS ontap_live_aggr (run_id BIGINT NOT NULL, address VARCHAR(191) NOT NULL, aggr VARCHAR(191) NOT NULL, "
    "cluster VARCHAR(191) NOT NULL DEFAULT '', node VARCHAR(191) NOT NULL DEFAULT '', size_b BIGINT NOT NULL DEFAULT 0, "
    "used_b BIGINT NOT NULL DEFAULT 0, growth_gb_day DOUBLE NULL, days_limit DOUBLE NULL, days_full DOUBLE NULL, "
    "rate_why VARCHAR(120) NOT NULL DEFAULT '', status VARCHAR(16) NOT NULL DEFAULT '', PRIMARY KEY (run_id, address, aggr)) " + db.TABLE_OPTS,
    "CREATE TABLE IF NOT EXISTS ontap_live_point (address VARCHAR(191) NOT NULL, kind VARCHAR(8) NOT NULL, obj VARCHAR(255) NOT NULL, "
    "at_s BIGINT NOT NULL, read_at DATETIME NOT NULL, size_b BIGINT NOT NULL DEFAULT 0, used_b BIGINT NOT NULL DEFAULT 0, "
    "PRIMARY KEY (address, kind, obj, at_s), KEY ontap_live_point_at (at_s)) " + db.TABLE_OPTS,
    "CREATE TABLE IF NOT EXISTS ontap_live_part (run_id BIGINT NOT NULL, part VARCHAR(16) NOT NULL, body MEDIUMTEXT NOT NULL, "
    "PRIMARY KEY (run_id, part)) " + db.TABLE_OPTS)
INSERT_RUN = ("INSERT INTO ontap_live_run (read_at, job, template_id, clusters, not_read, expand_template, expand_question, opts, volumes, offered) "
              "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)")
INSERT_VOL = ("INSERT INTO ontap_live_volume (run_id, row_key, address, cluster, svm, volume, lun, luns, size_b, used_b, aggr, aggr_free_b, "
              "datastore, ds_kind, offered, why, choice, root, full_size_b, full_used_b, full_src, thick, pt_obj) "
              "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
              "ON DUPLICATE KEY UPDATE used_b = VALUES(used_b)")
INSERT_AGGR = ("INSERT INTO ontap_live_aggr (run_id, address, aggr, cluster, node, size_b, used_b) VALUES (%s, %s, %s, %s, %s, %s, %s) "
               "ON DUPLICATE KEY UPDATE used_b = VALUES(used_b)")
INSERT_POINT = ("INSERT INTO ontap_live_point (address, kind, obj, at_s, read_at, size_b, used_b) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE size_b = VALUES(size_b), used_b = VALUES(used_b)")
INSERT_PART = "INSERT INTO ontap_live_part (run_id, part, body) VALUES (%s, %s, %s)"
# the sums of each object's points in the window (x: days before this run, y: GB used) - the slope from them (site_ontap_live.rate);
# 1.0E0: in floating point (MySQL's DECIMAL division keeps 4 digits more only, a rate 0.1 % off)
SUMS = ("SELECT kind, address, obj, COUNT(*), MIN(at_s), MAX(at_s), SUM(x), SUM(y), SUM(x * x), SUM(x * y) FROM "
        "(SELECT kind, address, obj, at_s, (at_s - %s) * 1.0E0 / 86400 AS x, used_b * 1.0E0 / 1073741824 AS y FROM ontap_live_point "
        "WHERE at_s >= %s AND at_s <= %s) pts GROUP BY kind, address, obj")
CHUNK = 500
PARTS = ("snapmirror", "nodes", "disks", "ems", "clusters", "perf")


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
    """The statements the first run sends, for making the tables by hand (ontap_live_db_create: false). The doc carries
    this text; a test keeps them alike."""
    return "\n\n".join([pretty(db.SCHEMA_VERSION_TABLE)] + [pretty(c) for c in CREATE] +
                       ["INSERT INTO schema_version (component, version, applied_at) VALUES ('%s', %d, UTC_TIMESTAMP());"
                        % (COMPONENT, SCHEMA_VERSION)])


def stamp(text):
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d)(?::(\d\d))?", str(text or ""))
    return datetime.datetime(*[int(x or 0) for x in m.groups()]) if m else None


def epoch(dt):
    return int((dt - datetime.datetime(1970, 1, 1)).total_seconds())


def columns(sql):
    """a CREATE TABLE -> (table, [(column, its definition)]) - the keys left out"""
    head, rest = sql.split(" (", 1)
    body = rest.rsplit(") ", 1)[0]
    parts, depth, cur = [], 0, ""
    for ch in body:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    parts.append(cur.strip())
    return head.split()[-1], [(x.split()[0], x) for x in parts if not x.startswith(("PRIMARY KEY", "KEY "))]


def add_missing(conn, create):
    """Every column of this release in its table (a table made by a pre-release build may lack one): the missing ones added
    (ALTER TABLE ... ADD COLUMN, as docs/REPORT_DATABASE.md shows) -> the columns added; with create false, an error that
    names them."""
    cur = conn.cursor()
    added = []
    for c in CREATE:
        table, cols = columns(c)
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = %s", (table,))
        have = set(str(r[0]).lower() for r in cur.fetchall())
        missing = [(n, d) for n, d in cols if n.lower() not in have]
        if missing and not create:
            raise RuntimeError("%s lacks %s (a table from a build before this one) - add them as docs/REPORT_DATABASE.md shows, or set "
                               "ontap_live_db_create: true" % (table, ", ".join(n for n, _d in missing)))
        for n, d in missing:
            cur.execute("ALTER TABLE %s ADD COLUMN %s" % (table, d))
            added.append("%s.%s" % (table, n))
    return added


def schema(conn, create):
    """(there, created): the tables, made when one is not there and create is true; schema_version's row with them."""
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name IN (%s)"
                % ", ".join(["%s"] * len(TABLES)), TABLES)
    have = set(str(r[0]) for r in cur.fetchall())
    there = have >= set(TABLES)
    if not there:
        if not create:
            return False, False
        cur.execute(db.SCHEMA_VERSION_TABLE)
        for c in CREATE:
            cur.execute(c)
    cur.execute("SELECT version FROM schema_version WHERE component = %s", (COMPONENT,))
    r = cur.fetchall()
    if not r and create:
        cur.execute("INSERT INTO schema_version (component, version, applied_at) VALUES (%s, %s, UTC_TIMESTAMP())", (COMPONENT, SCHEMA_VERSION))
    elif r and int(r[0][0]) > SCHEMA_VERSION:
        raise RuntimeError("the NetApp page's tables are version %s, newer than this release knows (%s) - update site-automation" % (r[0][0], SCHEMA_VERSION))
    add_missing(conn, create)                   # also after making the missing ones: another table may lack a column
    conn.commit()
    return True, not there


def _vol_obj(v):
    """a point's object: the row (svm, volume, LUN) with what it was measured by (datastore, LUN or volume) and its UUID -
    a history of its own each: vCenter lost or back, or a LUN made again under the same path, starts a new one (never a
    jump inside one, which would make a false rate)"""
    return L.row_key(str(v.get("address") or "").lower(), v.get("svm"), v.get("volume"), v.get("lun"), v.get("full_src") or "volume", v.get("uid"))


def write(conn, now, job, template_id, run, expand_template="", expand_question="", opts=None):
    """(run_id, volumes, aggrs, points, parts): the run and its rows, in the caller's transaction."""
    o = opts or {}
    r = run or {}
    vols = [v for v in r.get("volumes") or [] if isinstance(v, dict) and v.get("volume") and v.get("address")]
    aggrs = [a for a in r.get("aggrs") or [] if isinstance(a, dict) and a.get("name") and a.get("address")]
    cur = conn.cursor()
    cur.execute(INSERT_RUN, (now, job, template_id, json.dumps(r.get("clusters") or [], separators=(",", ":")),
                             json.dumps(r.get("not_read") or [], separators=(",", ":")), str(expand_template or "")[:255],
                             str(expand_question or "")[:128], json.dumps(o, separators=(",", ":"), sort_keys=True), len(vols),
                             sum(1 for v in vols if v.get("offered"))))
    rid = int(cur.lastrowid or 0)
    rows = [(rid, L.row_key(v["address"].lower(), v.get("svm"), v["volume"], v.get("lun")), str(v["address"])[:191], str(v.get("cluster") or "")[:191],
             str(v.get("svm") or "")[:191], str(v["volume"])[:191], str(v.get("lun") or "")[:255], int(v.get("luns") or 0), int(v.get("size") or 0),
             int(v.get("used") or 0), str(v.get("aggr") or "")[:191], int(v.get("aggr_free") or 0), str(v.get("datastore") or "")[:255],
             str(v.get("ds_kind") or "")[:8], 1 if v.get("offered") else 0, str(v.get("why") or "")[:300], str(v.get("choice") or ""),
             1 if v.get("root") else 0, int(v.get("full_size") or v.get("size") or 0), int(v.get("full_used") if v.get("full_used") is not None else v.get("used") or 0),
             str(v.get("full_src") or "volume")[:16], 1 if v.get("thick") else 0, _vol_obj(v)) for v in vols]
    for i in range(0, len(rows), CHUNK):
        cur.executemany(INSERT_VOL, rows[i:i + CHUNK])
    arows = [(rid, str(a["address"])[:191], str(a["name"])[:191], str(a.get("cluster") or "")[:191], str(a.get("node") or "")[:191],
              int(a.get("size") or 0), int(a.get("used") or 0)) for a in aggrs]
    if arows:
        cur.executemany(INSERT_AGGR, arows)
    at = epoch(now)
    pts, seen = [], set()
    for v in vols:                                     # one point a row: each LUN its own (its datastore's or its own fullness)
        k = (v["address"].lower(), "volume", _vol_obj(v))
        if k not in seen:
            seen.add(k)
            pts.append((k[0][:191], "volume", k[2][:255], at, now, int(v.get("full_size") or v.get("size") or 0),
                        int(v.get("full_used") if v.get("full_used") is not None else v.get("used") or 0)))
    for a in aggrs:
        pts.append((a["address"].lower()[:191], "aggr", str(a["name"])[:255], at, now, int(a.get("size") or 0), int(a.get("used") or 0)))
    for i in range(0, len(pts), CHUNK):
        cur.executemany(INSERT_POINT, pts[i:i + CHUNK])
    parts = []
    for name in PARTS:
        p = (r.get("parts") or {}).get(name)
        if isinstance(p, dict):
            cur.execute(INSERT_PART, (rid, name, json.dumps(p, separators=(",", ":"), default=str)))
            parts.append(name)
    return rid, len(rows), len(arows), len(pts), parts


def rate_all(conn, now, rid, opts=None):
    """Each volume's and aggregate's growth and days of this run, from the points of the window -> volumes with a rate."""
    o = opts or {}
    at = epoch(now)
    cur = conn.cursor()
    cur.execute(SUMS, (at, at - int(float(o.get("growth_days", 7)) * 86400), at))
    sums = dict(((str(k), str(a), str(ob)), row) for (k, a, ob), row in ((x[:3], x[3:]) for x in cur.fetchall()))
    mp, mh = int(o.get("min_points", 3)), float(o.get("min_hours", 6))
    cur.execute("SELECT row_key, address, pt_obj, full_size_b, full_used_b FROM ontap_live_volume WHERE run_id = %s", (rid,))
    vrows, rated = [], 0
    for key, addr, obj, size, used in cur.fetchall():
        s = sums.get(("volume", str(addr).lower(), str(obj)))
        g, why = L.rate(*s, min_points=mp, min_hours=mh) if s else (None, "no point yet")
        days = L.days_until(max(0, int(size) - int(used)), g) if g is not None else None
        rated += g is not None
        st = L.volume_status(100.0 * int(used) / int(size) if int(size) else 0.0, days, o)
        vrows.append((g, days, why[:120], st, rid, key))
    for i in range(0, len(vrows), CHUNK):
        cur.executemany("UPDATE ontap_live_volume SET growth_gb_day = %s, days_full = %s, rate_why = %s, status = %s WHERE run_id = %s AND row_key = %s",
                        vrows[i:i + CHUNK])
    cur.execute("SELECT address, aggr, size_b, used_b FROM ontap_live_aggr WHERE run_id = %s", (rid,))
    arows = []
    lim = float(o.get("aggr_max_pct", 97))
    for addr, aggr, size, used in cur.fetchall():
        s = sums.get(("aggr", str(addr).lower(), str(aggr)))
        g, why = L.rate(*s, min_points=mp, min_hours=mh) if s else (None, "no point yet")
        size, used = int(size), int(used)
        dfull = L.days_until(max(0, size - used), g) if g is not None else None
        dlim = L.days_until(max(0, size * lim / 100.0 - used), g) if g is not None else None
        pctu = 100.0 * used / size if size else 0.0
        if pctu >= lim:
            dlim = 0.0
        arows.append((g, dlim, dfull, why[:120], L.aggr_status(pctu, dlim, o), rid, addr, aggr))
    if arows:
        cur.executemany("UPDATE ontap_live_aggr SET growth_gb_day = %s, days_limit = %s, days_full = %s, rate_why = %s, status = %s "
                        "WHERE run_id = %s AND address = %s AND aggr = %s", arows)
    return rated


def prune(conn, now, rid, keep_days, keep_hours):
    """Points older than keep_days; runs older than keep_hours with their rows (never this run) -> {points, runs}."""
    cur = conn.cursor()
    out = {"points": 0, "runs": 0}
    if keep_days > 0:
        out["points"] = int(cur.execute("DELETE FROM ontap_live_point WHERE at_s < %s", (epoch(now) - keep_days * 86400,)) or 0)
    if keep_hours > 0:
        cut = now - datetime.timedelta(hours=keep_hours)
        cur.execute("SELECT run_id FROM ontap_live_run WHERE read_at < %s AND run_id <> %s", (cut, rid))
        old = [int(x[0]) for x in cur.fetchall()]
        for i in range(0, len(old), CHUNK):
            ids = old[i:i + CHUNK]
            marks = ", ".join(["%s"] * len(ids))
            for t in ("ontap_live_volume", "ontap_live_aggr", "ontap_live_part"):
                cur.execute("DELETE FROM %s WHERE run_id IN (%s)" % (t, marks), ids)
            cur.execute("DELETE FROM ontap_live_run WHERE run_id IN (%s)" % marks, ids)
        out["runs"] = len(old)
    return out


def shut(conn):
    try:
        if conn is not None:
            conn.close()
    except Exception:           # noqa: BLE001
        pass


READ_TIMEOUT = 20


def connect_args(p):
    """the connection's settings: always a finite wait for each answer (Codex 1.127.0: a database that takes the connection
    and then stops answering would stall the refresh)"""
    return dict(p, read_timeout=int(p.get("read_timeout") or READ_TIMEOUT))


def main():
    module = AnsibleModule(
        argument_spec=dict(db.ARGS, create=dict(type="bool", default=True), read_at=dict(type="str", required=True),
                           job=dict(type="int", default=0), template_id=dict(type="int", default=0),
                           run=dict(type="dict", required=True), expand_template=dict(type="str", default=""),
                           expand_question=dict(type="str", default=""), opts=dict(type="dict", default={}),
                           keep_days=dict(type="int", default=14), keep_hours=dict(type="int", default=48),
                           read_timeout=dict(type="int", default=READ_TIMEOUT)),
        supports_check_mode=True)
    p = module.params
    where = "%s:%s/%s" % (p["host"], p["port"], p["database"])
    res = {"changed": False, "ok": False, "error": "", "tls": "", "where": where, "run_id": 0, "volumes": 0, "aggrs": 0, "points": 0,
           "parts": [], "rated": 0, "pruned": {}, "created": False}

    def give_up(msg):
        res["error"] = msg
        module.warn("The report database %s: %s - this volume list is not kept there, so the portal's NetApp page shows the run "
                    "before it (ontap_live_db: false turns this off)" % (where, msg))
        module.exit_json(**res)

    if not db.HAS_PYMYSQL:
        give_up(db.NO_PYMYSQL)
    now = stamp(p["read_at"])
    if now is None:
        give_up("read_at is not a time: %r" % p["read_at"])
    conn = None
    try:
        conn, res["tls"] = db.connect(connect_args(p))
        there, res["created"] = schema(conn, p["create"] and not module.check_mode)
        if not there:
            shut(conn)
            give_up("the tables %s are not there%s - make them once, docs/REPORT_DATABASE.md" % (
                ", ".join(TABLES), " (check mode: nothing made)" if module.check_mode and p["create"] else " (ontap_live_db_create: false)"))
        if module.check_mode:
            res["ok"] = True
            shut(conn)
            module.exit_json(**res)
        rid, res["volumes"], res["aggrs"], res["points"], res["parts"] = write(conn, now, p["job"], p["template_id"], p["run"],
                                                                              p["expand_template"], p["expand_question"], p["opts"])
        res["run_id"] = rid
        res["rated"] = rate_all(conn, now, rid, p["opts"])
        res["pruned"] = prune(conn, now, rid, p["keep_days"], p["keep_hours"])
        conn.commit()
        res["ok"], res["changed"] = True, True
    except Exception as e:      # noqa: BLE001 - unreachable, refused, a grant missing: one line, the job goes on
        try:
            if conn is not None:
                conn.rollback()
        except Exception:       # noqa: BLE001
            pass
        shut(conn)
        res.update(run_id=0, volumes=0, aggrs=0, points=0, parts=[], rated=0, pruned={})
        give_up(db.explain(e, p))
    shut(conn)
    module.exit_json(**res)


if __name__ == "__main__":
    main()
