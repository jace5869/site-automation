#!/usr/bin/env python3
"""The network map's reads in the report database (1.111.0, roles/netmap_report/library/site_netmap_db.py): what a run writes (a read, the sites, the WAN
links; a missed read is one row), the roll-up of the reads older than netmap_db_keep_reads_days into one row a site and a link a day (reads, reads down and
warning, the busiest in and out, the averages, outages = two reads down in a row, the minutes down), the pruning, the rows the filter makes of a run, and the
statements in the doc. The SQL itself runs against a real MySQL in tests/netmap/run_netmap_db_test.sh.
  python3 tests/test_netmap_db.py"""
import datetime
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "roles", "netmap_report", "library"))
sys.path.insert(0, os.path.join(REPO, "plugins", "module_utils"))
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
import site_report_db  # noqa: E402
import ansible.module_utils  # noqa: E402
sys.modules.setdefault("ansible.module_utils.site_report_db", site_report_db)
ansible.module_utils.site_report_db = site_report_db
import site_netmap_db as ndb  # noqa: E402
import netmap_filters as nf  # noqa: E402

NOW = datetime.datetime(2026, 11, 23, 9, 30)


class Cursor(object):
    """Records every statement; answers SELECTs from a script (a list of rowsets, used in order) and the DELETE row count."""
    def __init__(self, answers=()):
        self.answers, self.sql, self.many, self.out, self.rowcount, self.header_rows = list(answers), [], [], [], 0, 2

    def execute(self, sql, args=()):
        sql = " ".join(sql.split())
        self.sql.append((sql, args))
        self.out = self.answers.pop(0) if sql.startswith("SELECT") and self.answers else []
        if sql.startswith("INSERT INTO netmap_read"):
            return self.header_rows                             # 1 = a new row, 2 = an existing one replaced
        return 1

    def executemany(self, sql, rows):
        self.many.append((" ".join(sql.split()), list(rows)))

    def fetchall(self):
        return self.out

    def fetchone(self):
        return self.out[0] if self.out else None


class Conn(object):
    def __init__(self, answers=()):
        self.cur, self.commits, self.rollbacks = Cursor(answers), 0, 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def T(day, hh, mm=0):
    return datetime.datetime(2026, 8, day, hh, mm)


def reads(day, spec, gap=30):
    """[(read_at, ok, status, in, out)]: spec is a string, one letter a read every `gap` minutes from midnight: U up, D down, W warning, x a missed read, - no row."""
    out = []
    for i, ch in enumerate(spec):
        t = T(day, 0) + datetime.timedelta(minutes=gap * i)
        out.append((t, ch != "x", {"U": "Up", "D": "Down", "W": "Warning"}.get(ch), None if ch in "Dx-" else 40, None if ch in "Dx-" else 10))
    return out


class Pure(unittest.TestCase):
    def test_a_time_keeps_its_offset_and_a_bad_one_is_none(self):
        self.assertEqual(ndb.stamp("2026-11-23T09:30:05Z"), datetime.datetime(2026, 11, 23, 9, 30, 5))
        self.assertEqual(ndb.stamp("2026-11-23T00:30:00+02:00"), datetime.datetime(2026, 11, 22, 22, 30, 0))     # UTC: the day before
        self.assertEqual(ndb.stamp("2026-11-23T00:30:00-0530"), datetime.datetime(2026, 11, 23, 6, 0, 0))
        self.assertEqual(ndb.stamp("2026-11-23 09:30:05.123456Z"), datetime.datetime(2026, 11, 23, 9, 30, 5))
        for bad in ("yesterday", "", None, "2026-02-30T09:30:05Z", "2026-13-01T00:00:00Z", "2026-11-23T25:00:00Z"):
            self.assertIsNone(ndb.stamp(bad), bad)                                                               # never an exception

    def test_a_bad_offset_or_overflow_is_none(self):
        for bad in ("9999-12-31T23:59:59-01:00", "2026-11-23T00:00:00+00:99", "2026-11-23T00:00:00+15:00", "0001-01-01T00:00:00+01:00"):
            self.assertIsNone(ndb.stamp(bad), bad)

    def test_a_time_the_database_would_not_keep_is_refused(self):
        db_now = datetime.datetime(2026, 11, 23, 9, 30)
        self.assertIsNone(ndb.sane_time(db_now, db_now, 90))
        self.assertIn("ahead", ndb.sane_time(db_now + datetime.timedelta(days=2), db_now, 90))        # a collector clock two days ahead
        self.assertIn("older", ndb.sane_time(db_now - datetime.timedelta(days=91), db_now, 90))
        self.assertIsNone(ndb.sane_time(db_now - datetime.timedelta(days=400), db_now, 0))             # no roll-up: nothing is "too old"
        self.assertIsNone(ndb.sane_time(db_now, None, 90))                                              # no database clock: the time is used

    def test_the_values(self):
        self.assertEqual((ndb._status("down"), ndb._status("weird"), ndb._pct(150), ndb._pct(-3), ndb._pct(None), ndb._pct("x")), ("Down", "Unknown", 100, 0, None, None))

    def test_the_usual_gap_is_the_true_median(self):
        t = [T(1, 0) + datetime.timedelta(minutes=m) for m in (0, 30, 60, 95, 125)]
        self.assertEqual(ndb.nominal_gap(t), 30.0)                                                              # gaps 30 30 35 30
        t = [T(1, 0) + datetime.timedelta(minutes=m) for m in (0, 10, 40)]
        self.assertEqual(ndb.nominal_gap(t), 20.0)                                                              # even count: the mean of the middle two
        self.assertEqual(ndb.nominal_gap([T(1, 0)]), 30.0)
        self.assertEqual(ndb.nominal_gap([T(d, 6) for d in (1, 2, 3)]), 1440.0)                                 # a daily schedule

    def test_an_outage_is_two_reads_down_in_a_row(self):
        self.assertEqual(ndb.outage_runs(["Up", "Down", "Up", "Down"]), 0)
        self.assertEqual(ndb.outage_runs(["Up", "Down", "Down", "Up", "Down", "Down", "Down"]), 2)
        self.assertEqual(ndb.outage_runs(["Down"] * 9), 1)


class Walking(unittest.TestCase):
    HI = T(2, 0)

    def w(self, spec, day=1, **kw):
        return ndb.walk(reads(day, spec), kw.pop("nominal", 30), kw.pop("hi", T(day + 1, 0)), **kw)

    def test_a_days_figures(self):
        r = ndb.walk([(T(1, 0), True, "Up", 40, 20), (T(1, 0, 30), True, "Warning", 85, 30), (T(1, 1), True, "Down", None, None),
                      (T(1, 1, 30), True, "Down", None, None), (T(1, 2), True, "Up", 10, 5)], 30, T(2, 0))
        self.assertEqual({k: r[k] for k in ("n_reads", "down_reads", "warn_reads", "in_max", "out_max", "in_avg", "out_avg", "outages", "down_min")},
                         {"n_reads": 5, "down_reads": 2, "warn_reads": 1, "in_max": 85, "out_max": 30, "in_avg": 45.0, "out_avg": 18.3, "outages": 1, "down_min": 60})

    def test_one_outage_across_midnight_is_counted_once(self):
        d1 = self.w("U" * 46 + "DD")                                 # 23:00 and 23:30 down: counted on day 1 (tail 2)
        self.assertEqual((d1["outages"], d1["tail_down"], d1["tail_at"]), (1, 2, T(1, 23, 30)))
        d2 = self.w("DDUU", day=2, init_run=d1["tail_down"], init_at=d1["tail_at"])     # it goes on at 00:00 and 00:30: not again
        self.assertEqual((d2["outages"], d2["down_reads"], d2["tail_down"]), (0, 2, 0))
        # down at 23:30 only, then at 00:00: the second read down makes it - on the second day, once
        a = self.w("U" * 47 + "D")
        self.assertEqual((a["outages"], a["tail_down"]), (0, 1))
        b = self.w("DUU", day=2, init_run=a["tail_down"], init_at=a["tail_at"])
        self.assertEqual(b["outages"], 1)
        # the carried streak is not used when the next read is far away (the gap rule)
        c = ndb.walk([(T(2, 6), True, "Down", None, None), (T(2, 6, 30), True, "Up", 1, 1)], 30, T(3, 0), init_run=1, init_at=T(1, 23, 30))
        self.assertEqual(c["outages"], 0)
        c = self.w("UUDUU", day=2, init_run=1, init_at=T(1, 23, 30))   # an Up read ends it
        self.assertEqual(c["outages"], 0)

    def test_a_daily_schedule_still_counts_outages(self):
        a = ndb.walk([(T(1, 6), True, "Down", None, None)], 1440, T(2, 0))
        b = ndb.walk([(T(2, 6), True, "Down", None, None)], 1440, T(3, 0), init_run=a["tail_down"], init_at=a["tail_at"])
        self.assertEqual((a["outages"], b["outages"], a["down_min"]), (0, 1, 1080))                      # 06:00 to midnight, never more than the day

    def test_a_missed_read_or_a_gap_never_makes_an_outage(self):
        r = self.w("D" + "x" * 9 + "D" + "U")                         # down, nine missed reads, down: not in a row
        self.assertEqual(r["outages"], 0)
        r = ndb.walk([(T(1, 8), True, "Down", None, None), (T(1, 17), True, "Down", None, None), (T(1, 17, 30), True, "Up", 1, 1)], 30, T(2, 0))
        self.assertEqual((r["outages"], r["down_min"]), (0, 60))                                         # 9 hours apart: one usual gap each, not 9 hours
        r = self.w("DD-DDU")                                          # a read without the link ends the streak: two outages' worth, each of two
        self.assertEqual(r["outages"], 2)
        r = self.w("D-D-D")
        self.assertEqual(r["outages"], 0)

    def test_down_minutes_never_exceed_what_the_reads_show(self):
        extra = [(T(1, 0, 10) + datetime.timedelta(minutes=2 * i), True, "Down", None, None) for i in range(5)]      # five manual reruns
        seq = sorted(reads(1, "D" * 48) + extra, key=lambda x: x[0])
        r = ndb.walk(seq, 30, T(2, 0))
        self.assertLessEqual(r["down_min"], 1440)
        self.assertEqual(r["down_min"], 1440)                                                            # the whole day down, once
        r = self.w("DUU")                                             # a blip: one read down = one gap
        self.assertEqual(r["down_min"], 30)
        r = ndb.walk([(T(1, 0), True, "Down", None, None), (T(1, 0, 5), True, "Up", 1, 1), (T(1, 6), True, "Up", 1, 1)], 30, T(2, 0))
        self.assertEqual(r["down_min"], 5)                                                               # a 5-minute blip is 5 minutes
        r = ndb.walk(reads(1, "D" * 48), 1.0, T(2, 0))               # a tiny usual gap does not shrink 48 reads' worth below what the reads cover
        self.assertEqual(r["down_min"], 48)

    def test_a_missed_read_never_makes_the_count_pass_a_later_good_read(self):
        r = ndb.walk([(T(1, 6), True, "Down", None, None), (T(1, 6, 10), False, None, None, None), (T(1, 6, 20), True, "Up", 1, 1)], 1440, T(2, 0))
        self.assertEqual(r["down_min"], 20)                                                      # a 20-minute event, not 18 hours
        r = ndb.walk([(T(1, 10), True, "Down", None, None), (T(1, 10, 5), False, None, None, None), (T(1, 10, 10), True, "Down", None, None),
                      (T(1, 10, 15), True, "Up", 1, 1)], 30, T(2, 0))
        self.assertEqual(r["down_min"], 15)

    def test_the_next_success_across_midnight_is_the_first_that_worked(self):
        r = ndb.walk([(T(1, 23, 15), True, "Down", None, None)], 30, T(2, 0), after=(T(2, 0, 10), True))     # 23:15 Down, the failed 00:01 skipped, 00:10 worked
        self.assertEqual(r["down_min"], 45)                                                                     # to midnight, not one usual gap

    def test_the_tail_belongs_to_the_days_last_read(self):
        old = {"n_reads": 48, "down_reads": 1, "warn_reads": 0, "in_max": 1, "out_max": 1, "in_avg": 1.0, "out_avg": 1.0, "outages": 0, "down_min": 30,
               "tail_down": 1, "tail_at": T(1, 23, 30), "last_at": T(1, 23, 30), "gap_min": 30.0}
        late = {"n_reads": 1, "down_reads": 0, "warn_reads": 0, "in_max": 1, "out_max": 1, "in_avg": 1.0, "out_avg": 1.0, "outages": 0, "down_min": 0,
                "tail_down": 0, "tail_at": None, "last_at": T(1, 12, 15), "gap_min": 30.0}
        m = ndb.merge(old, late)                                                                   # a late read from the middle of the day: the tail stays
        self.assertEqual((m["tail_down"], m["tail_at"], m["last_at"]), (1, T(1, 23, 30), T(1, 23, 30)))
        newer = dict(late, last_at=T(2, 0, 0), tail_down=2, tail_at=T(2, 0, 0))
        m = ndb.merge(old, newer)                                                                  # a read after the old last one: its tail
        self.assertEqual((m["tail_down"], m["last_at"]), (2, T(2, 0, 0)))

    def test_the_last_read_of_the_day_is_clamped_to_midnight(self):
        r = ndb.walk([(T(1, 23, 50), True, "Down", None, None)], 30, T(2, 0))
        self.assertEqual(r["down_min"], 10)

    def test_merge_adds_never_overwrites(self):
        old = {"n_reads": 48, "down_reads": 4, "warn_reads": 2, "in_max": 40, "out_max": 10, "in_avg": 40.0, "out_avg": 10.0, "outages": 1, "down_min": 120}
        new = {"n_reads": 1, "down_reads": 0, "warn_reads": 0, "in_max": 70, "out_max": 5, "in_avg": 70.0, "out_avg": 5.0, "outages": 0, "down_min": 0,
               "tail_down": 0, "tail_at": None}
        m = ndb.merge(old, new)
        self.assertEqual((m["n_reads"], m["down_reads"], m["warn_reads"], m["outages"], m["down_min"], m["in_max"], m["out_max"]), (49, 4, 2, 1, 120, 70, 10))
        self.assertEqual(m["in_avg"], 40.7)                                                              # weighted by the reads that had figures


class Writing(unittest.TestCase):
    SITES = [{"site": "SITEA", "nodes": 312, "down": 0, "warning": 4, "unknown": 1, "status": "Warning"}, {"site": "SITEB", "nodes": 188, "down": 3, "warning": 0, "unknown": 0, "status": "Down"}]
    LINKS = [{"key": "1/2|3/4", "site_a": "SITEA", "site_b": "SITEB", "node_a": "a1", "node_b": "b1", "status": "Down", "in": None, "out": None, "speed": "1 Gb/s"},
             {"key": "5/6|7/8", "site_a": "SITEA", "site_b": "SITEC", "node_a": "a2", "node_b": "c1", "status": "Warning", "in": 91.4, "out": 33, "speed": "100 Mb/s"}]

    def test_a_read_a_row_each_site_each_wan_link(self):
        c = Conn()
        n = ndb.write(c, NOW, 4711, True, self.SITES, self.LINKS, links_total=40, source="Orion.Example.mil")
        self.assertEqual(n, {"reads": 1, "sites": 2, "links": 2})
        sql = [s for s, a in c.cur.sql]
        self.assertTrue(sql[0].startswith("INSERT INTO netmap_read"))                                            # the header first: it locks the read's time
        self.assertEqual(sql[1:3], ["DELETE FROM netmap_site WHERE read_at = %s", "DELETE FROM netmap_link WHERE read_at = %s"])    # the same read again replaces, never mixes
        isql, args = c.cur.sql[0]
        self.assertTrue(isql.startswith("INSERT INTO netmap_read"))
        self.assertEqual(args, (NOW, 4711, 1, "orion.example.mil", 500, 2, 40, 2, 3, 4, 1, 1, 1))     # source, nodes, sites, ALL links, wan, nodes down/warning/unknown, wan down/warning
        (ssql, srows), (lsql, lrows) = c.cur.many
        self.assertEqual(srows[1], (NOW, "siteb", "SITEB", 188, 3, 0, 0, "Down"))                      # the key is the folded name, the spelling kept beside it
        self.assertEqual(lrows[1], (NOW, "5/6|7/8", "SITEA", "SITEC", "a2", "c1", "Warning", 91, 33, "100 Mb/s"))
        self.assertEqual(lrows[0][7:9], (None, None))

    def test_a_new_header_has_no_children_to_delete(self):
        c = Conn()
        c.cur.header_rows = 1
        ndb.write(c, NOW, 1, True, self.SITES, self.LINKS)
        self.assertFalse(any(s.startswith("DELETE") for s, a in c.cur.sql))

    def test_a_site_is_one_site_whatever_its_case(self):
        c = Conn()
        n = ndb.write(c, NOW, 1, True, [{"site": "Site A ", "nodes": 5}, {"site": "SITE A", "nodes": 9}, {"site": "Site B", "nodes": 1}], [])
        self.assertEqual(n["sites"], 2)
        self.assertEqual([r[1] for r in c.cur.many[0][1]], ["site a", "site b"])
        self.assertEqual(ndb.skey("  Éa "), "éa")

    def test_a_deadlock_or_lock_timeout_is_tried_again_three_times(self):
        class Dead(Exception):
            pass

        class C(Conn):
            tries = 0

            def cursor(self_):
                if self_.tries < 2:
                    self_.tries += 1
                    raise Dead(1213, "deadlock")
                return self_.cur
        orig = ndb.time.sleep
        ndb.time.sleep = lambda x: None
        try:
            c = C()
            self.assertEqual(ndb.write_retry(c, 3, NOW, 1, True, [], [])["reads"], 1)            # the third try works
            self.assertEqual(c.rollbacks, 2)

            class Always(C):
                tries = -99
            with self.assertRaises(Dead):
                ndb.write_retry(Always(), 3, NOW, 1, True, [], [])                                # still failing: raised, said by the caller
            e = Conn()
            e.cursor = lambda: (_ for _ in ()).throw(RuntimeError("Access denied"))               # a code that is not a deadlock: no second try
            with self.assertRaises(RuntimeError):
                ndb.write_retry(e, 3, NOW, 1, True, [], [])
            self.assertEqual(e.rollbacks, 1)
        finally:
            ndb.time.sleep = orig

    def test_the_source_is_the_configured_name_never_the_answering_server(self):
        base = {"reachable": True, "url": "https://Orion.Example.mil.:17774", "map": {"sites": [], "nodes": [], "links": []}}
        a = dict(base, version={"engine": "ORION-WEB01"}, engines=["ORION-WEB01"])
        b = dict(base, version={"engine": "ORION-WEB02"}, engines=["ORION-WEB02"])      # the same name, another server behind it answered
        self.assertEqual(nf.netmap_db_rows(a)["source"], "orion.example.mil:17774")       # case-folded, trailing dot stripped
        self.assertEqual(nf.netmap_db_rows(a)["source"], nf.netmap_db_rows(b)["source"])
        stored = nf.netmap_db_rows(a)["source"]
        self.assertEqual(ndb.source_conflict(Conn([[(stored,)]]), nf.netmap_db_rows(b)["source"]), "")      # never refused
        # and the outage is counted across both reads: the data keeps no backend identity at all
        r = ndb.walk([(T(1, 10), True, "Down", None, None), (T(1, 10, 30), True, "Down", None, None)], 30, T(2, 0))
        self.assertEqual(r["outages"], 1)

    def test_one_solarwinds_server(self):
        self.assertEqual(ndb.source_conflict(Conn([[("orion.example.mil",)]]), "ORION.example.mil"), "")
        self.assertEqual(ndb.source_conflict(Conn([[("orion.example.mil",)]]), "other.example.mil"), "orion.example.mil")
        self.assertEqual(ndb.source_conflict(Conn([[]]), "other.example.mil"), "")                 # nothing kept yet: any
        self.assertEqual(ndb.source_conflict(Conn(), ""), "")                                      # no source given: no check

    def test_a_missed_read_is_one_row_with_zero_counts(self):
        c = Conn()
        n = ndb.write(c, NOW, 4712, False, self.SITES, self.LINKS, links_total=40)
        self.assertEqual(n, {"reads": 1, "sites": 0, "links": 0})
        args = [a for s, a in c.cur.sql if s.startswith("INSERT INTO netmap_read")][0]
        self.assertEqual(args, (NOW, 4712, 0, "", 0, 0, 0, 0, 0, 0, 0, 0, 0))
        self.assertEqual(c.cur.many, [])

    def test_what_a_run_gives_the_writer(self):
        res = {"reachable": True, "map": {
            "sites": [{"name": "SITEA", "nodes": 2, "down": 0, "warning": 1, "unknown": 0, "worst": "Warning"}, {"name": "SITEB", "nodes": 1, "down": 1, "warning": 0, "unknown": 0, "worst": "Down"}],
            "nodes": [{"id": 1, "name": "a1", "site": "SITEA", "status": "Up"}, {"id": 2, "name": "a2", "site": "SITEA", "status": "Warning"}, {"id": 3, "name": "b1", "site": "SITEB", "status": "Down"}],
            "links": [{"a": 1, "b": 2, "ka": 10, "kb": 11, "status": "Up"},
                      {"a": 2, "b": 3, "ka": 12, "kb": 13, "status": "Down", "wan": True, "speed": "1 Gb/s"},
                      {"a": 1, "b": 3, "ka": 14, "kb": 15, "status": "Up", "wan": True, "in": 40, "out": 12, "gone": True}]}}
        r = nf.netmap_db_rows(res)
        self.assertEqual((r["ok"], len(r["sites"]), len(r["links"]), r["links_total"]), (True, 2, 1, 2))
        self.assertEqual((r["links"][0]["site_a"], r["links"][0]["site_b"], r["links"][0]["status"], r["links"][0]["speed"]), ("SITEA", "SITEB", "Down", "1 Gb/s"))
        self.assertEqual(r["links"][0]["key"], "2/12|3/13")
        none = {"ok": False, "sites": [], "links": [], "links_total": 0, "source": ""}
        self.assertEqual(nf.netmap_db_rows({"reachable": False, "map": {}}), none)
        self.assertEqual(nf.netmap_db_rows(None), none)
        self.assertEqual(nf.netmap_db_rows(dict(res, url="https://Orion.Example.mil:17774/path?x=1"))["source"], "orion.example.mil:17774")
        self.assertEqual(nf.netmap_db_rows({"reachable": False, "url": "https://svc:pw@orion.example.mil"})["source"], "")      # a login in the address: no source at all, never a piece of it


class Schema(unittest.TestCase):
    def test_tables_there_without_schema_version(self):
        c = Conn([[(t,) for t in ndb.TABLES], []])                                            # the five tables, no schema_version, then no version row
        self.assertEqual(ndb.schema(c, True), ([], []))
        sql = [s for s, a in c.cur.sql]
        self.assertTrue(any(s.startswith("CREATE TABLE IF NOT EXISTS schema_version") for s in sql))
        self.assertTrue(any(s.startswith("INSERT IGNORE INTO schema_version") for s in sql))
        c = Conn([[(t,) for t in ndb.TABLES]])                                                # create false: no schema_version to ask, nothing made
        self.assertEqual(ndb.schema(c, False), ([], []))
        self.assertFalse(any(s.startswith("CREATE") or s.startswith("INSERT") for s, a in c.cur.sql))

    def test_missing_tables_with_create_false_are_named(self):
        self.assertEqual(ndb.schema(Conn([[("schema_version",)]]), False)[0], list(ndb.TABLES))


class Rolling(unittest.TestCase):
    def test_a_day_at_a_time_each_in_its_own_transaction(self):
        d1, d2 = datetime.date(2026, 8, 1), datetime.date(2026, 8, 2)
        c = Conn([[(d1,), (d2,)]])
        seen = []
        orig = ndb.roll_day
        ndb.roll_day = lambda cur, day: seen.append(day) or True
        try:
            self.assertEqual(ndb.rollup(c, NOW, 90, 40), 2)
        finally:
            ndb.roll_day = orig
        self.assertEqual(seen, [d1, d2])
        self.assertEqual(c.commits, 3)                                                         # the list, then one for each day

    def test_a_failing_day_is_rolled_back_and_raised(self):
        c = Conn([[(datetime.date(2026, 8, 1),)]])
        orig = ndb.roll_day

        def boom(cur, day):
            raise RuntimeError("no DELETE grant")
        ndb.roll_day = boom
        try:
            with self.assertRaises(RuntimeError):
                ndb.rollup(c, NOW, 90, 40)
        finally:
            ndb.roll_day = orig
        self.assertEqual(c.rollbacks, 1)

    def test_the_cutoff_keeps_every_read_of_the_last_days(self):
        c = Conn()
        ndb.rollup(c, NOW, 90, 40)
        sql, args = c.cur.sql[0]
        self.assertEqual(args[0], datetime.datetime(2026, 8, 25))
        self.assertEqual(ndb.rollup(Conn(), NOW, 0, 40), 0)

    def test_the_reads_are_read_locked_and_deleted_after_the_day_rows(self):
        d = datetime.date(2026, 8, 1)
        t = [T(1, 0), T(1, 0, 30), T(1, 1)]
        c = Conn([[(x, 1) for x in t], [(x, 1) for x in t],
                  [(x, "siteb", "SITEB", "Down" if i else "Up", 1 if i else 0, 0) for i, x in enumerate(t)],
                  [(x, "L1", "SITEA", "SITEB", "a1", "b1", "Down" if i else "Up", None if i else 40, None if i else 10) for i, x in enumerate(t)],
                  [], [], [], []])
        self.assertTrue(ndb.roll_day(c.cur, d))
        sql = [s for s, a in c.cur.sql]
        self.assertTrue(all(s.endswith("FOR UPDATE") for s in sql if s.startswith("SELECT") and "FROM netmap_site_day WHERE day" in s and "tail" not in s))
        self.assertTrue(sql[0].endswith("FOR UPDATE"))
        ins = [a for s, a in c.cur.sql if s.startswith("INSERT INTO netmap_link_day")][0]
        self.assertEqual(ins[:2], (d, "L1"))
        # the children of exactly the reads locked, by their times - never a range, never a row at a time
        dels = [(s_, a_) for s_, a_ in c.cur.sql if s_.startswith("DELETE")]
        self.assertEqual([d[0] for d in dels], ["DELETE FROM netmap_link WHERE read_at IN (%s, %s, %s)", "DELETE FROM netmap_site WHERE read_at IN (%s, %s, %s)",
                                                "DELETE FROM netmap_read WHERE read_at IN (%s, %s, %s)"])
        self.assertTrue(all(d[1] == tuple(t) for d in dels))
        self.assertFalse(any("read_at >=" in s_ for s_, a_ in dels))
        self.assertEqual(c.cur.many, [])

    def test_a_day_rolled_again_does_not_use_the_tail_twice(self):
        d = datetime.date(2026, 8, 2)
        t = [T(2, 0, 15)]                                                                          # the late read, a Down 45 minutes after the tail
        old_l = ("L1", 48, 1, 0, None, None, None, None, 1, 30, 0, None, T(2, 23, 30), 30.0)        # the day was rolled: one outage, counted with the tail of the day before
        c = Conn([[(x, 1) for x in t], [(x, 1) for x in t], [(30.0,)], [], [(x, "L1", "SITEA", "SITEB", "a1", "b1", "Down", None, None) for x in t],
                  [], [list(old_l)], [], [("L1", 1, T(1, 23, 30))]])
        c.cur.answers[6] = [old_l]
        self.assertTrue(ndb.roll_day(c.cur, d))
        ins = [a for s, a in c.cur.sql if s.startswith("INSERT INTO netmap_link_day")][0]
        cols = dict(zip(("day", "link_key", "site_a", "site_b", "node_a", "node_b") + ndb.LINK_DAY_COLS, ins))
        self.assertEqual(cols["outages"], 1)                                                      # 1 (before) + 0, not 1 + 1: the late reads do not use the tail again

    def test_the_usual_gap_of_a_day_whose_neighbours_are_gone(self):
        prev = datetime.date(2026, 8, 5)
        c = Conn([[(1440.0,)]])
        self.assertEqual(ndb.usual_gap(c.cur, [T(6, 6)], prev), 1440.0)                          # the day before was rolled with a daily gap
        c = Conn([[(None,)], [(None,)], [(T(9, 6),), (T(8, 6),), (T(7, 6),)]])
        self.assertEqual(ndb.usual_gap(c.cur, [], prev), 1440.0)                                  # else the latest reads
        self.assertEqual(ndb.usual_gap(Conn([[(None,)], [(None,)], []]).cur, [], prev), 30.0)
        self.assertEqual(ndb.usual_gap(Conn().cur, [T(1, 0), T(1, 0, 30)], prev), 30.0)           # reads enough around it: no query

    def test_the_day_rows_are_pruned_by_age(self):
        c = Conn()
        ndb.prune(c, NOW, 400)
        self.assertEqual([a for s, a in c.cur.sql], [(datetime.date(2025, 10, 19),), (datetime.date(2025, 10, 19),)])
        self.assertEqual(ndb.prune(Conn(), NOW, 0), {"sites": 0, "links": 0})

    def test_one_roll_up_at_a_time(self):
        c = Conn([[(1,)]])
        self.assertTrue(ndb.lock(c, "site_reports"))
        self.assertIn("GET_LOCK", c.cur.sql[0][0])
        self.assertFalse(ndb.lock(Conn([[(0,)]]), "site_reports"))


class Exit(Exception):
    pass


class FakeModule(object):
    """AnsibleModule for main(): the params it is given, the warnings and the result it hands back."""
    last = None

    def __init__(self, argument_spec=None, supports_check_mode=False):
        self.params = dict(FakeModule.params)
        self.check_mode, self.warnings, self.result = False, [], None
        FakeModule.last = self

    def warn(self, msg):
        self.warnings.append(msg)

    def exit_json(self, **kw):
        self.result = kw
        raise Exit()


class Main(unittest.TestCase):
    """main() with a fake module and a fake database: what it refuses, warns about and never does."""
    BASE = {"host": "db.example.mil", "port": 3309, "database": "site_reports", "user": "w", "password": "secret-pw", "tls": False, "ca_file": "",
            "connect_timeout": 10, "create": True, "write": True, "read_at": "2026-11-23T09:30:05Z", "job": 1, "ok": True, "sites": [], "links": [],
            "links_total": -1, "source": "orion.example.mil", "read_timeout": 120, "keep_reads_days": 90, "keep_days": 400, "max_days": 40}

    def run_main(self, answers=(), clock=NOW, **params):
        FakeModule.params = dict(self.BASE, **params)
        conn = Conn([[(t,) for t in ndb.TABLES + ("schema_version",)], [(1,)]] + list(answers))        # the tables there, schema version 1
        saved = (ndb.AnsibleModule, ndb.db.connect, ndb.db.HAS_PYMYSQL, ndb.db_clock)
        ndb.AnsibleModule, ndb.db.connect, ndb.db.HAS_PYMYSQL = FakeModule, (lambda p: (conn, "none")), True
        ndb.db_clock = lambda c: clock
        try:
            ndb.main()
        except Exit:
            pass
        finally:
            ndb.AnsibleModule, ndb.db.connect, ndb.db.HAS_PYMYSQL, ndb.db_clock = saved
        return FakeModule.last, conn

    def statements(self, conn):
        return [s for s, a in conn.cur.sql]

    def test_a_host_with_a_login_is_refused_before_connecting_and_never_repeated(self):
        m, conn = self.run_main(host="svc:hunter2@db.example.mil")
        self.assertEqual(conn.cur.sql, [])
        self.assertTrue(m.warnings and "user information" in m.warnings[0])
        self.assertNotIn("hunter2", " ".join(m.warnings) + str(m.result))
        self.assertNotIn("svc", " ".join(m.warnings) + str(m.result))

    def test_no_database_clock_nothing_is_written_or_rolled_up(self):
        m, conn = self.run_main(clock=None)
        self.assertTrue(any("clock" in w for w in m.warnings))
        s = self.statements(conn)
        self.assertFalse(any(x.startswith(("INSERT INTO netmap_read", "DELETE", "SELECT GET_LOCK")) for x in s))

    def test_a_read_from_another_solarwinds_is_refused(self):
        m, conn = self.run_main(answers=[[("other.example.mil",)]])
        self.assertTrue(any("one SolarWinds server" in w for w in m.warnings))
        self.assertFalse(any(x.startswith("INSERT INTO netmap_read") for x in self.statements(conn)))

    def test_a_good_read_is_written_and_rolled_with_the_database_clock(self):
        m, conn = self.run_main(answers=[[("orion.example.mil",)]])
        self.assertEqual(m.warnings, [])
        self.assertTrue(any(x.startswith("INSERT INTO netmap_read") for x in self.statements(conn)))
        self.assertTrue(any("GET_LOCK" in x for x in self.statements(conn)))


class Doc(unittest.TestCase):
    def test_the_statements_in_the_doc_are_the_modules(self):
        doc = open(os.path.join(REPO, "docs", "REPORT_DATABASE.md"), encoding="utf-8").read()
        for part in ndb.ddl().split("\n\n"):
            self.assertIn(part, doc, "docs/REPORT_DATABASE.md: the statement differs from site_netmap_db.ddl()")
        self.assertIn("PRIMARY KEY (link_key, read_at)", ndb.ddl())

    def test_the_settings_are_in_the_defaults_and_the_docs(self):
        defaults = open(os.path.join(REPO, "roles", "netmap_report", "defaults", "main.yml"), encoding="utf-8").read()
        ref = open(os.path.join(REPO, "docs", "VARIABLES_REFERENCE.md"), encoding="utf-8").read()
        guide = open(os.path.join(REPO, "docs", "NETWORK_MAP.md"), encoding="utf-8").read()
        for name in ("netmap_db", "netmap_db_create", "netmap_db_timeout", "netmap_db_keep_reads_days", "netmap_db_keep_days"):
            self.assertIn("\n%s:" % name, defaults, name)
            self.assertIn("`%s`" % name, ref, name)
            self.assertIn("`%s`" % name, guide, name)


if __name__ == "__main__":
    unittest.main()
