#!/usr/bin/env python3
"""Unit tests for the MariaDB / MySQL performance findings (plugins/filter/mariadb_filters.py).
Standard library only: python3 tests/test_mariadb_perf.py"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
import mariadb_filters as mf  # noqa: E402

DAY = 86400


def ids(r):
    return sorted(f["id"] for f in r["findings"])


def perf(status, lines=None, **opts):
    o = {"sql": "mariadb -u monitor -p"}
    o.update(opts)
    return mf.mariadb_perf(status, lines or [], o)


QUIET = {"UPTIME": 10 * DAY, "QUESTIONS": 10000000, "SLOW_QUERIES": 5, "CREATED_TMP_TABLES": 100000,
         "CREATED_TMP_DISK_TABLES": 100, "SELECT_FULL_JOIN": 10, "INNODB_ROW_LOCK_WAITS": 10, "INNODB_ROW_LOCK_TIME": 50,
         "OPEN_FILES": 40, "OPEN_FILES_LIMIT": 32768, "TABLE_OPEN_CACHE": 2000, "OPEN_TABLES": 300, "OPENED_TABLES": 400,
         "INNODB_DEADLOCKS": 1, "INNODB_HISTORY_LIST_LENGTH": 50}


class Quiet(unittest.TestCase):
    def test_no_findings(self):
        r = perf(QUIET)
        self.assertEqual(r["findings"], [])
        self.assertIn("11.6 queries/s", r["line"])
        self.assertIn("history length 50", r["line"])

    def test_empty_status(self):
        r = perf({})
        self.assertEqual(r["findings"], [])


class SinceStart(unittest.TestCase):
    def test_slow_queries_per_hour(self):
        r = perf(dict(QUIET, SLOW_QUERIES=24 * 10 * 100, LONG_QUERY_TIME="10.000000", SLOW_QUERY_LOG="OFF"))
        self.assertEqual(ids(r), ["mariadb:slow-queries"])
        self.assertIn("100 slow queries per hour since it started 10 days ago", r["findings"][0]["summary"])
        self.assertIn("The slow query log is OFF", r["findings"][0]["summary"])

    def test_slow_log_on_and_pfs_hint(self):
        r = perf(dict(QUIET, SLOW_QUERIES=24 * 10 * 100, SLOW_QUERY_LOG="ON", SLOW_QUERY_LOG_FILE="/var/lib/mysql/slow.log",
                      PERFORMANCE_SCHEMA="ON"))
        self.assertIn("The slow query log (/var/lib/mysql/slow.log) says which", r["findings"][0]["summary"])
        self.assertIn("events_statements_summary_by_digest", r["findings"][0]["hint"])

    def test_not_judged_right_after_a_start(self):
        r = perf(dict(QUIET, UPTIME=3600, SLOW_QUERIES=5000, SELECT_FULL_JOIN=5000))
        self.assertEqual(r["findings"], [])
        self.assertIn("judged after 2 h", r["line"])
        self.assertEqual(ids(perf(dict(QUIET, UPTIME=3600, SLOW_QUERIES=5000), min_uptime_hours=0.5)), ["mariadb:slow-queries"])

    def test_tmp_disk(self):
        r = perf(dict(QUIET, CREATED_TMP_DISK_TABLES=40000, TMP_TABLE_SIZE=33554432, MAX_HEAP_TABLE_SIZE=16777216))
        self.assertEqual(ids(r), ["mariadb:tmp-disk"])
        self.assertIn("40% of the temporary tables", r["findings"][0]["summary"])
        self.assertIn("16 MB", r["findings"][0]["summary"])
        self.assertEqual(perf(dict(QUIET, CREATED_TMP_TABLES=500, CREATED_TMP_DISK_TABLES=400))["findings"], [])   # too few

    def test_full_joins_table_locks_row_locks(self):
        r = perf(dict(QUIET, SELECT_FULL_JOIN=240 * 200, TABLE_LOCKS_WAITED=500, TABLE_LOCKS_IMMEDIATE=20000,
                      INNODB_ROW_LOCK_WAITS=1000, INNODB_ROW_LOCK_TIME=2000000, INNODB_ROW_LOCK_TIME_MAX=51000))
        self.assertEqual(ids(r), ["mariadb:full-joins", "mariadb:row-locks", "mariadb:table-locks"])
        rl = [f for f in r["findings"] if f["id"] == "mariadb:row-locks"][0]
        self.assertIn("on average 2,000 ms", rl["summary"])

    def test_row_lock_time_avg_from_status_wins(self):
        r = perf(dict(QUIET, INNODB_ROW_LOCK_WAITS=1000, INNODB_ROW_LOCK_TIME=10, INNODB_ROW_LOCK_TIME_AVG=900))
        self.assertEqual(ids(r), ["mariadb:row-locks"])

    def test_deadlocks_mariadb_status_or_mysql_metric(self):
        self.assertEqual(ids(perf(dict(QUIET, INNODB_DEADLOCKS=500))), ["mariadb:deadlocks"])
        my = dict(QUIET)
        del my["INNODB_DEADLOCKS"]
        self.assertEqual(ids(perf(my, ["M\tlock_deadlocks\t500"])), ["mariadb:deadlocks"])
        self.assertNotIn("deadlocks", perf(my)["line"])                # not enabled: not shown, not judged

    def test_innodb_waits_aborted_table_cache(self):
        r = perf(dict(QUIET, INNODB_BUFFER_POOL_WAIT_FREE=500, INNODB_LOG_WAITS=300, INNODB_LOG_BUFFER_SIZE=8388608,
                      ABORTED_CONNECTS=24 * 10 * 100, OPEN_TABLES=2000, OPENED_TABLES=24 * 10 * 1000))
        self.assertEqual(ids(r), ["mariadb:aborted-connects", "mariadb:bufferpool-wait", "mariadb:log-waits", "mariadb:table-cache"])
        lw = [f for f in r["findings"] if f["id"] == "mariadb:log-waits"][0]
        self.assertIn("innodb_log_buffer_size (8 MB)", lw["summary"])
        self.assertIn("Innodb_log_waits", lw["hint"])

    def test_table_cache_not_full_is_fine(self):
        self.assertEqual(perf(dict(QUIET, OPEN_TABLES=500, OPENED_TABLES=24 * 10 * 1000))["findings"], [])


class Now(unittest.TestCase):
    def test_open_files(self):
        r = perf(dict(QUIET, OPEN_FILES=900, OPEN_FILES_LIMIT=1000))
        self.assertEqual(ids(r), ["mariadb:open-files"])
        self.assertIn("(90%)", r["findings"][0]["summary"])

    def test_judged_at_once_even_after_a_start(self):
        r = perf(dict(QUIET, UPTIME=60, OPEN_FILES=900, OPEN_FILES_LIMIT=1000, INNODB_HISTORY_LIST_LENGTH=2000000),
                 ["T\t7\t700|RUNNING|3|0|app|shop|Sleep"])
        self.assertEqual(ids(r), ["mariadb:long-transactions", "mariadb:open-files", "mariadb:purge-lag"])

    def test_lock_waits(self):
        lines = ["W\t51\tetl|shop|400|Waiting for table metadata lock", "W\t52\tapp|shop|5|Waiting for table metadata lock",
                 "R\t3\t95"]
        r = perf(QUIET, lines)
        self.assertEqual(ids(r), ["mariadb:lock-waits"])
        s = r["findings"][0]["summary"]
        self.assertIn("1 session(s) wait for a lock: id 51: etl / shop / 400 s", s)      # 5 s is below the limit
        self.assertIn("3 transaction(s) wait for a row lock, the longest for 95 s", s)
        self.assertEqual(perf(QUIET, ["R\t3\t10"])["findings"], [])                      # short waits are normal

    def test_long_transactions_idle(self):
        r = perf(QUIET, ["T\t42\t3600|RUNNING|120|7|app|shop|Sleep", "T\t43\t900|LOCK WAIT|1|0|app|shop|Query"])
        s = r["findings"][0]["summary"]
        self.assertTrue(s.startswith("2 transaction(s) open longer than 600 s"))
        self.assertIn("thread 42: 60 minutes open, RUNNING, 120 rows locked, 7 rows changed, app / shop / Sleep", s)
        self.assertIn("1 of them sit idle (Sleep)", s)

    def test_purge_lag_mysql_metric(self):
        r = perf(dict((k, v) for k, v in QUIET.items() if k != "INNODB_HISTORY_LIST_LENGTH"), ["M\ttrx_rseg_history_len\t3000000"])
        self.assertEqual(ids(r), ["mariadb:purge-lag"])

    def test_bad_lines_are_ignored(self):
        self.assertEqual(perf(QUIET, ["", "garbage", "T\tx", "R\tnot\ta number"])["findings"], [])

    def test_hints_use_the_given_client(self):
        r = perf(dict(QUIET, OPEN_FILES=900, OPEN_FILES_LIMIT=1000), sql="podman exec -it db mysql")
        self.assertTrue(r["findings"][0]["hint"].startswith("podman exec -it db mysql -e"))
        self.assertTrue(all(f["check"] == "mariadb" and f["severity"] == "warning" for f in r["findings"]))


if __name__ == "__main__":
    unittest.main(verbosity=1)
