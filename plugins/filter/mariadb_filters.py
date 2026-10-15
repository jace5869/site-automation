"""Performance findings for the MariaDB / MySQL health check (roles/check_mariadb/tasks/health.yml).

mariadb_perf(status, extra_lines, opts) -> {'findings': [...], 'line': 'one status line', 'numbers': {...}}

status:       SHOW GLOBAL STATUS + SHOW GLOBAL VARIABLES of one server, names upper case (the
              check's _mdb.S / _mdb.V; the variables are merged into the same dict there).
extra_lines:  the lines of the check's performance query (tab separated):
                T <thread id> <seconds open>|<state>|<rows locked>|<rows modified>|<user>|<db>|<command>
                     an InnoDB transaction open longer than opts.long_trx_seconds
                W <id> <user>|<db>|<seconds>|<state>   a session waiting for a table / metadata / global lock
                R <count> <longest wait in seconds>     InnoDB transactions waiting for a row lock now
                M <metric name> <value>                 information_schema.INNODB_METRICS (enabled ones)
opts:         the limits (check_mariadb_* settings, see roles/check_mariadb/defaults/main.yml) and
              'sql': the client command a person runs to look further (used in the hints).

Counters such as Slow_queries count since the server started: they are judged as an average per
hour (or a share) since then, and only after opts.min_uptime_hours, so a fresh start (cold
caches) is not judged. What is happening NOW (lock waits, old transactions, purge lag, files and
connections in use) is judged at once. Read-only: it only looks at numbers the check already read.
Standard library only.
"""


def _num(v, default=0.0):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default


def _fmt(n):
    n = float(n)
    if n >= 100 or n == int(n):
        return "{:,}".format(int(round(n)))
    return "%.1f" % n


def _ago(seconds):
    s = float(seconds)
    if s < 7200:
        return "%d minutes" % (s // 60)
    if s < 172800:
        return "%.1f hours" % (s / 3600)
    return "%.0f days" % (s / 86400)


def _mb(v):
    return "%d MB" % (_num(v) / 1048576) if _num(v) >= 1048576 else "%d KB" % (_num(v) / 1024)


def parse_extra(lines):
    out = {"trx": [], "waits": [], "row_waits": 0, "row_wait_max": 0, "metrics": {}}
    for line in lines or []:
        c = str(line).rstrip("\n").split("\t")
        if len(c) < 3:
            continue
        if c[0] == "T":
            p = (c[2].split("|") + ["-"] * 7)[:7]
            out["trx"].append({"id": c[1], "secs": int(_num(p[0])), "state": p[1], "locked": int(_num(p[2])),
                               "modified": int(_num(p[3])), "user": p[4], "db": p[5], "command": p[6]})
        elif c[0] == "W":
            p = (c[2].split("|") + ["-"] * 4)[:4]
            out["waits"].append({"id": c[1], "user": p[0], "db": p[1], "secs": int(_num(p[2])), "state": p[3]})
        elif c[0] == "R":
            out["row_waits"] = int(_num(c[1]))
            out["row_wait_max"] = int(_num(c[2]))
        elif c[0] == "M":
            out["metrics"][c[1].strip().lower()] = _num(c[2])
    return out


def mariadb_perf(status, extra_lines=None, opts=None):
    s = dict((str(k).upper(), v) for k, v in (status or {}).items())
    o = dict(opts or {})
    x = parse_extra(extra_lines)
    sql = o.get("sql") or "mariadb"
    f = []

    def add(fid, summary, hint, severity="warning"):
        f.append({"check": "mariadb", "id": "mariadb:" + fid, "severity": severity, "summary": summary, "hint": hint})

    def g(name):
        return _num(s.get(name))

    uptime = g("UPTIME")
    hours = uptime / 3600.0
    judged = hours >= _num(o.get("min_uptime_hours"), 2) and uptime > 0
    since = "since it started %s ago" % _ago(uptime) if uptime > 0 else "since it started"

    def per_hour(name):
        return g(name) / hours if hours > 0 else 0.0

    questions = g("QUESTIONS")
    numbers = {"uptime_hours": round(hours, 1), "qps": round(questions / uptime, 1) if uptime > 0 else 0}
    look_running = sql + (' -e "SELECT ID, USER, DB, TIME, STATE FROM information_schema.PROCESSLIST'
                          ' WHERE COMMAND <> \'Sleep\' ORDER BY TIME DESC LIMIT 15"')
    digest = sql + (' -e "SELECT SCHEMA_NAME, COUNT_STAR, ROUND(SUM_TIMER_WAIT/1e12) AS total_s, LEFT(DIGEST_TEXT, 120)'
                    ' FROM performance_schema.events_statements_summary_by_digest ORDER BY SUM_TIMER_WAIT DESC LIMIT 10"')
    pfs_on = str(s.get("PERFORMANCE_SCHEMA", "")).upper() == "ON"

    # ---- averages since the start ------------------------------------------------------------
    slow_h = per_hour("SLOW_QUERIES")
    numbers["slow_per_hour"] = round(slow_h, 1)
    if judged and slow_h >= _num(o.get("slow_per_hour_warn"), 60):
        log_on = str(s.get("SLOW_QUERY_LOG", "")).upper() in ("ON", "1")
        share = (" (%.2f%% of all queries)" % (g("SLOW_QUERIES") / questions * 100)) if questions > 0 else ""
        add("slow-queries",
            "%s slow queries per hour %s%s; a query is slow when it runs longer than long_query_time (%s s). %s"
            % (_fmt(slow_h), since, share, _fmt(g("LONG_QUERY_TIME")),
               ("The slow query log (%s) says which." % (s.get("SLOW_QUERY_LOG_FILE") or "slow_query_log_file")) if log_on
               else "The slow query log is OFF: turn it on (slow_query_log) to see which queries they are."),
            (digest if pfs_on else sql + ' -e "SHOW GLOBAL VARIABLES LIKE \'slow_query_log%\'"'))

    tmp, tmp_disk = g("CREATED_TMP_TABLES"), g("CREATED_TMP_DISK_TABLES")
    numbers["tmp_disk_pct"] = round(tmp_disk / tmp * 100, 1) if tmp > 0 else 0
    if judged and tmp >= _num(o.get("tmp_min_tables"), 1000) and tmp_disk / tmp * 100 >= _num(o.get("tmp_disk_pct_warn"), 25):
        mem = min(g("TMP_TABLE_SIZE") or g("MAX_HEAP_TABLE_SIZE"), g("MAX_HEAP_TABLE_SIZE") or g("TMP_TABLE_SIZE"))
        add("tmp-disk",
            "%d%% of the temporary tables that queries needed went to disk (%s of %s %s): slow. The limit in memory is %s "
            "(the smaller of tmp_table_size and max_heap_table_size); tables with TEXT/BLOB columns always go to disk"
            % (round(tmp_disk / tmp * 100), _fmt(tmp_disk), _fmt(tmp), since, _mb(mem)),
            sql + ' -e "SHOW GLOBAL STATUS LIKE \'Created_tmp%\'; SHOW GLOBAL VARIABLES WHERE Variable_name IN '
                  '(\'tmp_table_size\', \'max_heap_table_size\')"')

    fj_h = per_hour("SELECT_FULL_JOIN")
    numbers["full_joins_per_hour"] = round(fj_h, 1)
    if judged and fj_h >= _num(o.get("full_join_per_hour_warn"), 100):
        add("full-joins",
            "%s joins per hour %s read a whole table because no index fits the join (Select_full_join %s): "
            "an index is missing, and they get slower as the tables grow" % (_fmt(fj_h), since, _fmt(g("SELECT_FULL_JOIN"))),
            digest.replace("ORDER BY SUM_TIMER_WAIT", "WHERE SUM_SELECT_FULL_JOIN > 0 ORDER BY SUM_SELECT_FULL_JOIN") if pfs_on
            else sql + ' -e "SHOW GLOBAL STATUS LIKE \'Select_full%\'"')

    tl_wait, tl_now = g("TABLE_LOCKS_WAITED"), g("TABLE_LOCKS_IMMEDIATE")
    if judged and tl_wait + tl_now >= _num(o.get("table_lock_min"), 10000) \
            and tl_wait / (tl_wait + tl_now) * 100 >= _num(o.get("table_lock_wait_pct_warn"), 1):
        add("table-locks",
            "%.1f%% of table locks had to wait (%s of %s %s): tables that lock as a whole (MyISAM / Aria) are busy - "
            "InnoDB locks rows instead" % (tl_wait / (tl_wait + tl_now) * 100, _fmt(tl_wait), _fmt(tl_wait + tl_now), since),
            sql + ' -e "SELECT TABLE_SCHEMA, TABLE_NAME, ENGINE FROM information_schema.TABLES WHERE ENGINE IN '
                  '(\'MyISAM\', \'Aria\') AND TABLE_SCHEMA NOT IN (\'mysql\', \'information_schema\', \'performance_schema\', \'sys\')"')

    rl_waits = g("INNODB_ROW_LOCK_WAITS")
    rl_avg = g("INNODB_ROW_LOCK_TIME_AVG") or (g("INNODB_ROW_LOCK_TIME") / rl_waits if rl_waits else 0)
    numbers["row_lock_avg_ms"] = round(rl_avg)
    if judged and rl_waits >= _num(o.get("row_lock_min_waits"), 100) and rl_avg >= _num(o.get("row_lock_avg_ms_warn"), 500):
        add("row-locks",
            "queries waited on average %s ms for a row another transaction had locked (%s waits %s, longest %s ms): "
            "long transactions, or many writing the same rows"
            % (_fmt(rl_avg), _fmt(rl_waits), since, _fmt(g("INNODB_ROW_LOCK_TIME_MAX"))),
            sql + ' -e "SHOW GLOBAL STATUS LIKE \'Innodb_row_lock%\'"')

    deadlocks = g("INNODB_DEADLOCKS") if "INNODB_DEADLOCKS" in s else x["metrics"].get("lock_deadlocks")
    if deadlocks is not None:
        numbers["deadlocks"] = int(deadlocks)
        if judged and hours > 0 and deadlocks >= 10 and deadlocks / hours >= _num(o.get("deadlocks_per_hour_warn"), 1):
            add("deadlocks",
                "%s deadlocks %s (%s per hour): InnoDB rolled back one transaction of each pair, so the application "
                "got an error and had to retry" % (_fmt(deadlocks), since, _fmt(deadlocks / hours)),
                sql + ' -e "SHOW ENGINE INNODB STATUS\\G" | sed -n \'/LATEST DETECTED DEADLOCK/,/TRANSACTIONS/p\'')

    for name, var, fid, what, fix in (
            ("INNODB_BUFFER_POOL_WAIT_FREE", "Innodb_buffer_pool_wait_free", "bufferpool-wait", "had to wait for a free page in the InnoDB buffer pool",
             "the buffer pool is too small, or pages are not flushed fast enough (innodb_buffer_pool_size, innodb_io_capacity)"),
            ("INNODB_LOG_WAITS", "Innodb_log_waits", "log-waits", "waited because the InnoDB log buffer was full",
             "innodb_log_buffer_size (%s) is too small for the writes" % _mb(g("INNODB_LOG_BUFFER_SIZE")))):
        if judged and g(name) >= 10 and per_hour(name) >= _num(o.get("innodb_waits_per_hour_warn"), 1):
            add(fid, "%s times %s, InnoDB %s (%s per hour): %s" % (_fmt(g(name)), since, what, _fmt(per_hour(name)), fix),
                sql + ' -e "SHOW GLOBAL STATUS LIKE \'%s\'"' % var)

    ab_h = per_hour("ABORTED_CONNECTS")
    if judged and ab_h >= _num(o.get("aborted_connects_per_hour_warn"), 60):
        add("aborted-connects",
            "%s connection attempts per hour %s failed (Aborted_connects %s): wrong passwords, a client or "
            "health probe that does not finish the login, or a scan" % (_fmt(ab_h), since, _fmt(g("ABORTED_CONNECTS"))),
            sql + ' -e "SHOW GLOBAL STATUS LIKE \'Connection_errors%\'; SHOW GLOBAL STATUS LIKE \'Aborted%\'"')

    opened_h = per_hour("OPENED_TABLES")
    cache = g("TABLE_OPEN_CACHE")
    if judged and cache > 0 and g("OPEN_TABLES") >= cache * 0.95 and opened_h >= _num(o.get("table_open_per_hour_warn"), 600):
        add("table-cache",
            "the table cache is full (%s of table_open_cache %s open) and %s tables are opened per hour %s: "
            "every open reads the table's definition again" % (_fmt(g("OPEN_TABLES")), _fmt(cache), _fmt(opened_h), since),
            sql + ' -e "SHOW GLOBAL STATUS LIKE \'Open%tables\'; SHOW GLOBAL VARIABLES LIKE \'table_open_cache%\'"')

    # ---- what is happening now ---------------------------------------------------------------
    limit = g("OPEN_FILES_LIMIT")
    if limit > 0 and g("OPEN_FILES") / limit * 100 >= _num(o.get("open_files_warn_pct"), 85):
        add("open-files",
            "%s of %s files the server may open are open (%d%%): at the limit it refuses to open tables"
            % (_fmt(g("OPEN_FILES")), _fmt(limit), round(g("OPEN_FILES") / limit * 100)),
            sql + ' -e "SHOW GLOBAL VARIABLES LIKE \'open_files_limit\'; SHOW GLOBAL STATUS LIKE \'Open_files\'"')

    lock_secs = _num(o.get("lock_wait_seconds"), 30)
    waits = [w for w in x["waits"] if w["secs"] >= lock_secs]
    row_waiting = x["row_waits"] if x["row_wait_max"] >= lock_secs else 0
    if waits or row_waiting:
        parts = []
        if waits:
            parts.append("%d session(s) wait for a lock: %s" % (len(waits), "; ".join(
                "id %s: %s / %s / %d s / %s" % (w["id"], w["user"], w["db"], w["secs"], w["state"])
                for w in sorted(waits, key=lambda w: -w["secs"])[:3])))
        if row_waiting:
            parts.append("%d transaction(s) wait for a row lock, the longest for %d s" % (row_waiting, x["row_wait_max"]))
        add("lock-waits",
            "waiting for a lock for %d s or longer right now (user / db / seconds / state): %s. The session holding it "
            "is usually a long transaction or an ALTER TABLE" % (lock_secs, ". ".join(parts)),
            sql + ' -e "SELECT trx_mysql_thread_id, trx_state, trx_started, trx_wait_started, trx_rows_locked '
                  'FROM information_schema.INNODB_TRX ORDER BY trx_started"; ' + look_running)

    if x["trx"]:
        t = sorted(x["trx"], key=lambda t: -t["secs"])
        idle = [a for a in t if a["command"] == "Sleep"]
        add("long-transactions",
            "%d transaction(s) open longer than %s s (they hold their locks, and old row versions cannot be purged "
            "while they are open): %s%s"
            % (len(t), _fmt(_num(o.get("long_trx_seconds"), 600)),
               "; ".join("thread %s: %s open, %s, %s rows locked, %s rows changed, %s / %s / %s"
                         % (a["id"], _ago(a["secs"]), a["state"], _fmt(a["locked"]), _fmt(a["modified"]), a["user"], a["db"], a["command"])
                         for a in t[:3]),
               (". %d of them sit idle (Sleep): the application opened a transaction and did not commit it" % len(idle)) if idle else ""),
            sql + ' -e "SELECT trx_mysql_thread_id, trx_state, trx_started, trx_rows_locked, trx_rows_modified '
                  'FROM information_schema.INNODB_TRX ORDER BY trx_started LIMIT 10"')

    hist = g("INNODB_HISTORY_LIST_LENGTH") if "INNODB_HISTORY_LIST_LENGTH" in s else x["metrics"].get("trx_rseg_history_len")
    if hist is not None:
        numbers["history_length"] = int(hist)
        if hist >= _num(o.get("history_length_warn"), 1000000):
            add("purge-lag",
                "%s old row versions wait to be purged (InnoDB history list length): a long-open transaction is "
                "holding them, or purge cannot keep up. Reads get slower and the undo space grows" % _fmt(hist),
                sql + ' -e "SELECT trx_mysql_thread_id, trx_started FROM information_schema.INNODB_TRX ORDER BY trx_started LIMIT 5"')

    line = ("performance %s: %s queries/s, %s slow/h, %s%% temporary tables on disk, %s full joins/h, row lock wait avg %s ms%s%s, "
            "%d transaction(s) open > %s s, %d lock wait(s) now"
            % ("since start, %s" % _ago(uptime) if uptime > 0 else "", _fmt(numbers["qps"]), _fmt(slow_h), _fmt(numbers["tmp_disk_pct"]),
               _fmt(fj_h), _fmt(rl_avg),
               (", %d deadlocks" % numbers["deadlocks"]) if "deadlocks" in numbers else "",
               (", history length %s" % _fmt(numbers["history_length"])) if "history_length" in numbers else "",
               len(x["trx"]), _fmt(_num(o.get("long_trx_seconds"), 600)), len(waits) + row_waiting))
    if not judged and uptime > 0:
        line += " (averages since the start are judged after %s h)" % _fmt(_num(o.get("min_uptime_hours"), 2))
    return {"findings": f, "line": line, "numbers": numbers}


class FilterModule(object):
    def filters(self):
        return {"mariadb_perf": mariadb_perf}
