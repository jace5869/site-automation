#!/usr/bin/env python3
"""Unit tests for the NetApp ONTAP capacity trends report (1.52.0): this run's point (plugins/filter/
ontap_capacity_filters.py), the runs of AAP's job history and the report database as one list, a volume's story (back
again or not, a resize against a drop of used space, the rate after the change, fewer than 3 runs), the snapshots over
their reserve, the report and its website document with a trend line per row, AAP's job history read
(roles/ontap_report/library/site_ontap_capacity_history.py) and the database module's statements
(site_ontap_capacity_db.py). The made-up clusters: tests/netapp/capacity_fixture.py. Standard library plus ansible-core:
python3 tests/test_ontap_capacity.py"""
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "roles", "ontap_report", "library"))
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
sys.path.insert(0, os.path.join(REPO, "plugins", "module_utils"))
sys.path.insert(0, os.path.join(HERE, "netapp"))
import site_report_db  # noqa: E402
import ansible.module_utils  # noqa: E402
sys.modules.setdefault("ansible.module_utils.site_report_db", site_report_db)
ansible.module_utils.site_report_db = site_report_db
import site_aap  # noqa: E402
sys.modules.setdefault("ansible.module_utils.site_aap", site_aap)
ansible.module_utils.site_aap = site_aap
import capacity_fixture as fx  # noqa: E402
import ontap_capacity_filters as f  # noqa: E402
import site_ontap_capacity_db as cdb  # noqa: E402
import site_ontap_capacity_history as hist  # noqa: E402

DAY, GB, TB = 86400.0, 1024 ** 3, 1024 ** 4
T0 = f.parse_time(fx.NOW)
OPTS = {"days": 30, "aggr_warn_pct": 85, "aggr_crit_pct": 90, "volume_warn_pct": 85, "volume_crit_pct": 90, "volume_min_gb": 50,
        "snap_warn_pct": 100, "snap_crit_pct": 150, "again_days": 7, "fast_days": 30, "top": 5, "max_rows": 200}


def point(ago=0, **kw):
    return f.ontap_capacity_points(fx.collected(ago=ago, **kw), {"volume_min_gb": 50})


def earlier(days=30, every=1):
    """The earlier runs as AAP's job history gives them: the artifact (no names) with the job's ID."""
    out = []
    for d in range(days, 0, -every):
        p = point(d)
        out.append(dict(((k, v) for k, v in p.items() if k != "names"), job=4000 + days - d))
    return out


def report(runs=None, opts=None, col=None, **kw):
    col = col or fx.collected()
    now = f.ontap_capacity_points(col, {"volume_min_gb": 50})
    got = f.ontap_capacity_runs(now, earlier() if runs is None else runs, [], {"days": (opts or {}).get("days", 30), "job": 5000})
    return f.ontap_capacity(col, now, got["runs"], dict(OPTS, counts=got["counts"], **(opts or {})), **kw)


def section(doc, title):
    return next(s for s in doc["sections"] if s["title"] == title)


def row(sec, name):
    return dict(zip(sec["columns"], next(r for r in sec["rows"] if r[0] == name)))


def pts(*used_pct, size_tb=1.0, sizes=None, step=1.0):
    """A volume's points, a day apart, the last one now: its used % a run (sizes: its size in TB a run)."""
    n = len(used_pct)
    out = []
    for i, p in enumerate(used_pct):
        size = (sizes[i] if sizes else size_tb) * TB
        out.append((T0 - (n - 1 - i) * step * DAY, size, size * p / 100.0))
    return out


class Point(unittest.TestCase):
    def test_this_runs_point(self):
        p = point()
        self.assertEqual((p["v"], p["read_at"]), (1, fx.NOW))
        c1, c2 = p["clusters"]
        self.assertEqual((c1["name"], len(c1["aggregates"]), len(c1["volumes"])), ("cluster1", 3, 8))
        self.assertEqual((c2["name"], len(c2["aggregates"]), len(c2["volumes"])), ("cluster2", 2, 1))
        self.assertEqual(c1["size_b"], 140 * TB)
        self.assertEqual(c1["used_b"], sum(a["used_b"] for a in c1["aggregates"]))

    def test_an_svm_root_and_a_small_volume_are_left_out(self):
        names = [v["name"] for v in point()["names"][point()["clusters"][0]["uuid"]]["volumes"].values()]
        self.assertIn("vol_home", names)
        self.assertNotIn("svm1_root", names)
        self.assertNotIn("vol_small", names)
        small = f.ontap_capacity_points(fx.collected(), {"volume_min_gb": 5})
        self.assertIn("vol_small", [v["name"] for v in small["names"][small["clusters"][0]["uuid"]]["volumes"].values()])

    def test_the_snapshot_reserve_only_where_there_is_one(self):
        c1 = point()["clusters"][0]
        got = dict((s["uuid"], s) for s in c1["snapshot_reserve"])
        self.assertIn(fx.uuid("vol", "vol_db_logs"), got)
        self.assertNotIn(fx.uuid("vol", "vol_scans"), got)             # no reserve
        self.assertEqual(got[fx.uuid("vol", "vol_db_logs")]["reserve_pct"], 5)

    def test_a_cluster_not_reached_has_no_point(self):
        p = point(unreachable=True)
        self.assertEqual([c["name"] for c in p["clusters"]], ["cluster1", "cluster2"])

    def test_a_part_not_read_is_left_out_not_empty(self):
        col = fx.collected()
        col["clusters"][0]["data"]["volumes"] = {"records": [], "error": "HTTP 500", "reduced": "", "truncated": False}
        col["clusters"][1]["data"]["aggregates"]["truncated"] = True
        c1, c2 = f.ontap_capacity_points(col)["clusters"]
        self.assertNotIn("volumes", c1)
        self.assertIn("aggregates", c1)
        self.assertNotIn("aggregates", c2)
        self.assertNotIn("size_b", c2)

    def test_the_artifact_is_small_and_has_no_names(self):
        # what set_stats keeps: the point without names
        art = dict((k, v) for k, v in point().items() if k != "names")
        text = json.dumps(art)
        self.assertNotIn("vol_home", text)
        self.assertLess(len(text), 4000)

    def test_the_database_rows(self):
        rows = f.ontap_capacity_db_rows(point())
        self.assertEqual(len(rows), 16)
        self.assertEqual(sorted(set(r[2] for r in rows)), ["aggregate", "cluster", "volume"])
        self.assertTrue(all(len(r) == 10 for r in rows))
        logs = next(r for r in rows if r[4] == "vol_db_logs")
        self.assertEqual((logs[1], logs[5], logs[8]), ("cluster1", "svm2", 5))
        self.assertGreater(logs[9], 0)
        scans = next(r for r in rows if r[4] == "vol_scans")
        self.assertEqual((scans[8], scans[9]), (None, None))
        self.assertEqual(f.ontap_capacity_db_rows({}), [])


class Runs(unittest.TestCase):
    def test_each_run_once_oldest_first(self):
        got = f.ontap_capacity_runs(point(), earlier(), [], {"days": 30, "job": 5000})
        self.assertEqual(got["counts"], {"aap": 30, "db": 0, "both": 0, "used": 31})
        times = [r["_t"] for r in got["runs"]]
        self.assertEqual(times, sorted(times))
        self.assertEqual(got["runs"][-1]["job"], "5000")

    def test_a_run_in_aap_and_in_the_database_counts_once(self):
        runs = earlier()
        db = [dict(r) for r in runs[10:]]                               # the same jobs
        got = f.ontap_capacity_runs(point(), runs, db, {"days": 30, "job": 5000})
        self.assertEqual(got["counts"], {"aap": 30, "db": 0, "both": 20, "used": 31})

    def test_the_same_minute_without_a_job_id_counts_once(self):
        runs = earlier()
        db = [dict(r, job=0, read_at=r["read_at"][:17] + "40Z") for r in runs[:5]]     # written outside AAP, seconds later
        got = f.ontap_capacity_runs(point(), runs, db, {"days": 30, "job": 5000})
        self.assertEqual(got["counts"], {"aap": 30, "db": 0, "both": 5, "used": 31})

    def test_the_database_fills_what_aap_no_longer_has(self):
        runs = earlier()
        got = f.ontap_capacity_runs(point(), runs[20:], [dict(r, job=0) for r in runs[:25]], {"days": 30, "job": 5000})
        self.assertEqual(got["counts"], {"aap": 10, "db": 20, "both": 5, "used": 31})

    def test_two_database_runs_of_one_minute_are_one_run_not_both(self):
        runs = [dict(r, job=0) for r in earlier(3)]
        got = f.ontap_capacity_runs(point(), [], runs + [dict(runs[0])], {"days": 30})
        self.assertEqual(got["counts"], {"aap": 0, "db": 3, "both": 0, "used": 4})

    def test_this_job_launched_again_is_not_its_own_history(self):
        again = dict((k, v) for k, v in point().items() if k != "names")
        got = f.ontap_capacity_runs(point(), [dict(again, job=5000)], [dict(again, job=5000)], {"days": 30, "job": 5000})
        self.assertEqual(got["counts"]["used"], 1)

    def test_only_the_window_and_nothing_after_this_run(self):
        runs = earlier(40)
        late = dict(runs[-1], job=9999, read_at="2026-11-14T06:00:05Z")
        got = f.ontap_capacity_runs(point(), runs + [late], [], {"days": 10, "job": 5000})
        self.assertEqual(got["counts"]["used"], 11)
        self.assertGreaterEqual(got["runs"][0]["_t"], T0 - 10 * DAY - f.SLACK)

    def test_the_run_of_the_windows_first_day_counts_when_it_started_a_little_earlier(self):
        runs = earlier(30)
        runs[0] = dict(runs[0], read_at="2026-10-14T05:58:41Z")
        self.assertEqual(f.ontap_capacity_runs(point(), runs, [], {"days": 30})["counts"]["used"], 31)

    def test_what_is_not_a_point_is_left_out(self):
        bad = [None, {}, {"read_at": "yesterday", "clusters": []}, {"read_at": fx.when(fx.NOW, 2)}, {"read_at": fx.when(fx.NOW, 3), "clusters": "x"}]
        got = f.ontap_capacity_runs(point(), bad, bad, {"days": 30})
        self.assertEqual(got["counts"]["used"], 1)


class Story(unittest.TestCase):
    def story(self, points, **kw):
        return f.volume_story(points, dict(OPTS, **kw))

    def test_back_again_over_then_under_then_over_within_the_days(self):
        s = self.story(pts(88, 89, 90, 91, 78, 80, 83, 86))
        self.assertTrue(s["back_again"])
        self.assertEqual(s["changed"], "used space dropped 14%")
        self.assertEqual(round(s["earlier"]["pct"]), 91)
        self.assertEqual(s["after"], 4)                                # the runs from the low point on

    def test_not_back_again_when_the_earlier_time_over_is_longer_ago(self):
        s = self.story(pts(88, 91, *([78] * 9), 86))
        self.assertFalse(s["back_again"])
        self.assertTrue(self.story(pts(88, 91, *([78] * 9), 86), again_days=14)["back_again"])

    def test_not_back_again_when_it_never_dropped_under_the_level(self):
        self.assertFalse(self.story(pts(86, 88, 87, 86, 88))["back_again"])

    def test_not_back_again_under_the_level_now(self):
        s = self.story(pts(88, 91, 78, 80, 84))
        self.assertFalse(s["back_again"])
        self.assertEqual(s["changed"], "used space dropped 14%")       # the change is still said

    def test_a_resize_is_said_as_a_resize_not_as_a_drop(self):
        # 4.0 TB at 90% grown to 4.4 TB: the used % falls, the used space does not
        used = 3.6
        points = [(T0 - (5 - i) * DAY, size * TB, (used + 0.01 * i) * TB) for i, size in enumerate([4.0, 4.0, 4.0, 4.4, 4.4, 4.4])]
        s = self.story(points)
        self.assertEqual(s["changed"], "resized 4.0 TB to 4.4 TB")
        self.assertEqual(s["change"], 3)
        self.assertEqual(round(s["earlier"]["pct"], 1), 90.5)

    def test_a_resize_and_a_drop_together(self):
        points = pts(88, 89, 70, 71, 72, sizes=[1.0, 1.0, 1.2, 1.2, 1.2])
        self.assertEqual(self.story(points)["changed"], "resized 1.0 TB to 1.2 TB, used space dropped 6%")

    def test_a_volume_made_larger_and_over_again_is_back_again_with_the_resize_said(self):
        points = pts(86, 87, 79.1, 82, 86, sizes=[1.0, 1.0, 1.1, 1.1, 1.1])
        s = self.story(points)
        self.assertTrue(s["back_again"])
        self.assertEqual(s["changed"], "resized 1.0 TB to 1.1 TB")

    def test_the_rate_is_over_the_runs_after_the_change(self):
        s = self.story(pts(60, 70, 80, 90, 50, 51, 52, 53))            # +10 a day before, +1 a day after
        self.assertAlmostEqual(s["rate_b"] / TB, 0.01, places=4)
        self.assertEqual(s["after"], 4)
        self.assertAlmostEqual(s["days_full"], 47.0, places=1)
        plain = self.story(pts(50, 51, 52, 53))
        self.assertAlmostEqual(plain["rate_b"], s["rate_b"], places=0)

    def test_fewer_than_3_runs_no_rate(self):
        for points in (pts(40), pts(40, 60)):
            s = self.story(points)
            self.assertIsNone(s["rate_b"])
            self.assertIsNone(s["days_full"])
            self.assertFalse(s["fast"])
        self.assertIsNotNone(self.story(pts(40, 41, 42))["rate_b"])

    def test_fewer_than_3_runs_after_the_change_no_rate(self):
        s = self.story(pts(88, 90, 91, 70, 86))
        self.assertTrue(s["back_again"])
        self.assertIsNone(s["rate_b"])
        self.assertEqual(s["after"], 2)

    def test_filling_fast_reaches_the_red_level_within_the_days(self):
        self.assertTrue(self.story(pts(70, 71, 72, 73))["fast"])       # 17 points to 90%: 17 days
        self.assertFalse(self.story(pts(70, 71, 72, 73), fast_days=10)["fast"])
        self.assertFalse(self.story(pts(73, 72, 71, 70))["fast"])      # shrinking
        self.assertIsNone(self.story(pts(73, 72, 71, 70))["days_full"])

    def test_over_the_red_level_already(self):
        s = self.story(pts(91, 92, 93))
        self.assertEqual(s["days_crit"], 0.0)
        self.assertTrue(s["fast"])

    def test_a_small_wobble_is_not_a_change(self):
        s = self.story(pts(60, 61, 60.5, 61.5, 62))
        self.assertIsNone(s["change"])
        self.assertEqual(s["changed"], "")

    def test_slope(self):
        self.assertIsNone(f.slope([(0, 1), (DAY, 2)]))
        self.assertIsNone(f.slope([(5, 1), (5, 2), (5, 3)]))
        self.assertAlmostEqual(f.slope([(0, 1), (DAY, 2), (2 * DAY, 3)]), 1.0)
        self.assertAlmostEqual(f.slope([(0, 1), (2 * DAY, 3), (3 * DAY, 4)]), 1.0)      # a gap is a gap, not a step


class Report(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = report()
        cls.doc, cls.web = cls.r["report"], cls.r["web"]

    def test_the_title_and_the_counts(self):
        self.assertEqual(self.doc["title"], "NetApp ONTAP capacity trends: 2 aggregates full within 90 days, 2 volumes back again, 2 filling fast")
        self.assertEqual(self.doc["status"], "critical")
        c = self.r["counts"]
        self.assertEqual((c["clusters"], c["read"], c["runs"], c["volumes"]), (2, 2, 31, 9))
        self.assertEqual((c["aggr_warn"], c["aggr_soon"], c["snapshots_over"], c["filling_fast"], c["back_again"]), (1, 2, 1, 2, 2))
        self.assertEqual(self.doc["subtitle"], "Read 2026-11-13 06:00 UTC - 31 runs in the last 30 days")

    def test_the_sections_results_first(self):
        self.assertEqual([s["title"] for s in self.doc["sections"]],
                         ["Clusters", "Aggregates", "Volumes filling again", "Fastest growing volumes", "Snapshot space", "The numbers",
                          "How this was made"])
        self.assertEqual([s["title"] for s in self.web["sections"]], [s["title"] for s in self.doc["sections"]])

    def test_aggregates_days_to_the_red_level(self):
        s = section(self.doc, "Aggregates")
        a2 = row(s, "aggr2_node04")
        self.assertEqual((a2["Used % now"], a2["Days to 90%"]), ("87.4", "14"))
        self.assertEqual(s["row_status"][0], "critical")                # 90% within 30 days
        self.assertEqual(s["rows"][1][0], "aggr1_node05")
        self.assertEqual(s["row_status"][1], "warning")                 # within 90 days
        self.assertEqual(row(s, "aggr_sas_node03")["Days to 90%"], "-")  # not growing

    def test_volumes_back_again_and_filling_fast(self):
        s = section(self.doc, "Volumes filling again")
        self.assertEqual([r[0] for r in s["rows"]], ["vol_home", "vol_scans", "vol_vdi_profiles"])
        home = row(s, "vol_home")
        self.assertEqual(home["Why"], "Back again, filling fast")
        self.assertEqual(home["What changed"], "used space dropped 13%, from 91.1% on 2026-11-08")
        self.assertEqual(row(s, "vol_scans")["Why"], "Back again")
        vdi = row(s, "vol_vdi_profiles")
        self.assertEqual((vdi["Why"], vdi["What changed"]), ("Filling fast", "-"))
        self.assertNotIn("vol_projects", [r[0] for r in s["rows"]])     # resized 12 days ago: not back again

    def test_the_fastest_growing(self):
        s = section(self.doc, "Fastest growing volumes")
        self.assertEqual(len(s["rows"]), 5)
        self.assertEqual(s["rows"][0][0], "vol_home")
        rates = [float(r[4]) for r in s["rows"]]
        self.assertEqual(rates, sorted(rates, reverse=True))
        self.assertEqual(len(section(report(opts={"top": 2})["report"], "Fastest growing volumes")["rows"]), 2)

    def test_a_volume_with_two_runs_has_no_rate_and_is_said(self):
        self.assertNotIn("vol_new", json.dumps(section(self.doc, "Fastest growing volumes")["rows"]))
        self.assertIn("1 of 9 volumes have fewer than 3 runs", json.dumps(self.doc))
        vols = next(x for x in self.r["xlsx"] if x["name"] == "Volumes")
        new = dict(zip(vols["columns"], next(r for r in vols["rows"] if r[2] == "vol_new")))
        self.assertEqual((new["Runs"], new["Growth GB/day"], new["Days to full"]), (2, "", ""))

    def test_snapshots_over_their_reserve(self):
        s = section(self.doc, "Snapshot space")
        logs = row(s, "vol_db_logs")
        self.assertEqual((logs["Reserve used % now"], logs["Note"]), ("130.0", "Spills into the volume"))
        self.assertEqual(s["row_status"][0], "warning")                 # over 100%, under 150%
        self.assertEqual([r[0] for r in s["rows"]], ["vol_db_logs", "vol_home"])
        self.assertEqual((row(s, "vol_home")["Reserve used % now"], row(s, "vol_home")["Note"]), ("85.0", "Near its reserve"))
        self.assertEqual(s["row_status"][1], "")
        self.assertEqual(self.r["counts"]["snapshots_over"], 1)
        # other levels: red from 120%, listed from 80% of 110%; under 100% nothing spills, whatever the level
        low = report(opts={"snap_warn_pct": 110, "snap_crit_pct": 120})
        s = section(low["report"], "Snapshot space")
        self.assertEqual([r[0] for r in s["rows"]], ["vol_db_logs"])
        self.assertEqual(s["row_status"], ["critical"])
        s = section(report(opts={"snap_warn_pct": 80})["report"], "Snapshot space")
        self.assertEqual(row(s, "vol_home")["Note"], "Over 80% of its reserve")
        self.assertEqual(row(s, "vol_db_logs")["Note"], "Spills into the volume")

    def test_the_resize_is_in_the_workbook(self):
        vols = next(x for x in self.r["xlsx"] if x["name"] == "Volumes")
        pr = dict(zip(vols["columns"], next(r for r in vols["rows"] if r[2] == "vol_projects")))
        self.assertEqual((pr["What changed"], pr["Why"]), ("resized 4.0 TB to 4.4 TB", ""))
        self.assertEqual([x["name"] for x in self.r["xlsx"]],
                         ["Clusters", "Aggregates", "Volumes filling again", "Fastest growing volumes", "Snapshot space", "Volumes", "Points"])
        points = next(x for x in self.r["xlsx"] if x["name"] == "Points")["rows"]
        self.assertEqual(sorted(set(r[2] for r in points)), ["Aggregate", "Cluster", "Snapshot reserve", "Volume"])
        self.assertEqual(len([r for r in points if r[2] == "Aggregate"]), 5 * 31)
        self.assertEqual(len([r for r in points if r[3] == "vol_home" and r[2] == "Volume"]), 31)

    def test_the_website_has_a_trend_line_per_row_the_email_none(self):
        for s in self.doc["sections"]:
            self.assertNotIn("Trend", s.get("columns") or [])
            self.assertNotIn("sparks", s)
        n = 0
        for s in self.web["sections"]:
            if s["title"] in ("Clusters", "Aggregates", "Volumes filling again", "Fastest growing volumes", "Snapshot space"):
                self.assertEqual(s["columns"][-1], "Trend")
                self.assertEqual(s["kinds"][-1], "spark")
                self.assertEqual(len(s["sparks"]), len(s["rows"]))
                self.assertTrue(s["lines"][0].startswith("Trend: "))
                for sp, r in zip(s["sparks"], s["rows"]):
                    self.assertEqual(r[-1], sp["label"])                # the cell's text: the line in words (search, CSV, copy)
                    n += 1
        self.assertEqual(n, 2 + 5 + 3 + 5 + 2)

    def test_a_trend_line_fits_its_box_and_marks_the_change(self):
        s = section(self.web, "Volumes filling again")
        home = s["sparks"][0]
        self.assertEqual((home["w"], home["h"]), (f.SPARK_W, f.SPARK_H))
        xy = [tuple(float(v) for v in p.split(",")) for p in home["points"].split()]
        self.assertEqual(len(xy), 31)
        self.assertTrue(all(0 <= x <= home["w"] and 0 <= y <= home["h"] for x, y in xy))
        self.assertEqual(xy[-1], tuple(home["last"]))
        self.assertEqual(len(home["lines"]), 2)                         # 85% and 90%
        self.assertGreater(home["lines"][0], home["lines"][1])          # 90% is higher on the page
        self.assertTrue(xy[-8][0] < home["mark"] < xy[-1][0])           # the drop, five days ago
        self.assertIsNone(s["sparks"][2]["mark"])                       # vol_vdi_profiles: no change
        self.assertTrue(home["label"].startswith("Used % of vol_home: 88.6 on 2026-10-14 to 87.8 on 2026-11-13, 31 runs; by week: "))

    def test_a_long_history_is_drawn_with_at_most_spark_max_points(self):
        vals = [(T0 - (399 - i) * 3600.0, 50 + i * 0.01) for i in range(400)]
        sp = f.spark(vals, T0 - 400 * 3600.0, T0, [85, 90], what="Used % of x")
        self.assertLessEqual(len(sp["points"].split()), f.SPARK_MAX)
        self.assertIn("400 runs", sp["label"])
        self.assertEqual(f.spark([], 0, 1, []), {})
        one = f.spark([(T0, 40.0)], T0 - DAY, T0, [])
        self.assertEqual(len(one["points"].split()), 1)

    def test_the_numbers_behind_the_lines(self):
        s = section(self.doc, "The numbers")
        self.assertEqual(s["columns"][:3], ["What", "Name", "Cluster"])
        self.assertEqual(len(s["columns"]), 3 + 5)                      # 30 days: five weeks
        self.assertTrue(s.get("collapsed"))
        self.assertEqual(len(s["rows"]), 15)

    def test_the_first_run_says_a_trend_needs_3(self):
        r = report(runs=[])
        self.assertEqual(r["report"]["title"], "NetApp ONTAP capacity trends: run 1 - a trend needs 3")
        self.assertEqual(r["counts"]["runs"], 1)
        self.assertEqual(row(section(r["report"], "Aggregates"), "aggr2_node04")["Days to 90%"], "-")
        self.assertIn("Fewer than 3 runs so far: no rate and no days yet. Each run adds one point.", json.dumps(r["report"]))
        self.assertEqual(r["report"]["subtitle"], "Read 2026-11-13 06:00 UTC - 1 run in the last 30 days")
        r2 = report(runs=earlier(1))
        self.assertEqual(r2["report"]["title"], "NetApp ONTAP capacity trends: run 2 - a trend needs 3")
        self.assertNotIn("run 3", report(runs=earlier(2))["report"]["title"])

    def test_a_cluster_not_read_is_listed_and_red(self):
        r = report(col=fx.collected(unreachable=True))
        self.assertEqual(r["report"]["status"], "critical")
        self.assertEqual((r["counts"]["clusters"], r["counts"]["read"]), (3, 2))
        self.assertIn("cluster3.example.mil: could not be read", json.dumps(r["report"]))
        self.assertEqual(r["counts"]["back_again"], 2)

    def test_a_shorter_window(self):
        r = report(opts={"days": 10})
        self.assertEqual(r["counts"]["runs"], 11)
        self.assertEqual(len(section(r["report"], "The numbers")["columns"]), 3 + 2)
        vols = next(x for x in r["xlsx"] if x["name"] == "Volumes")
        pr = dict(zip(vols["columns"], next(x for x in vols["rows"] if x[2] == "vol_projects")))
        self.assertEqual(pr["What changed"], "")                        # the resize is outside the window

    def test_other_levels(self):
        r = report(opts={"volume_warn_pct": 95, "volume_crit_pct": 98, "fast_days": 3})
        self.assertEqual((r["counts"]["back_again"], r["counts"]["filling_fast"]), (0, 0))
        self.assertIn("No volume is back over 95%", json.dumps(r["report"]))

    def test_how_this_was_made_names_the_settings_and_the_sources(self):
        made = " ".join(section(report(opts={"history_notes": ["Not run by AAP: no job history to read.", ""]})["report"], "How this was made")["lines"])
        for text in ("31 runs in the last 30 days (ontap_capacity_days), this one included: 30 from AAP's job history, 0 from the report database.",
                     "The oldest point: 2026-10-14 06:00 UTC.", "Not run by AAP: no job history to read.", "ontap_volume_warn_pct", "It needs 3 runs."):
            self.assertIn(text, made)

    def test_the_words_of_the_report(self):
        text = json.dumps([self.doc, self.web, self.r["lines"]]).lower()
        for word in ("purchase", "buy", "cost"):
            self.assertIsNone(re.search(r"\b%s" % word, text), word)

    def test_report_fit_document(self):
        """tests/report_fit_docs.py keeps this document (the website's, with its trend lines) for tests/test_report_fit.py."""
        self.assertEqual(f._capacity_web({"title": "t"}, [1]), {"title": "t", "sections": [1]})
        self.assertTrue(all("sparks" in s for s in self.web["sections"] if s.get("rows") and s["title"] != "The numbers"))


class FakeApi(object):
    """AAP's jobs as site_aap.Api gives them: (HTTP code, JSON), every call remembered."""
    user = "auditor"

    def __init__(self, jobs, forbid=False, hide=False, cut=(), why=""):
        self.jobs, self.forbid, self.hide, self.cut, self.why, self.calls = jobs, forbid, hide, set(cut), why, []

    def probe(self):
        return self.why

    def call(self, method, path):
        self.calls.append((method, path))
        m = re.match(r"^jobs/(\d+)/$", path)
        if m:
            j = next((j for j in self.jobs if j["id"] == int(m.group(1))), None)
            if j is None or self.hide:
                return 404, {"detail": "Not found."}
            if j["id"] in self.cut:
                self.cut.discard(j["id"])
                return -1, {"detail": "cut off"}
            return 200, j
        q = dict(kv.split("=") for kv in path.split("?")[1].split("&"))
        if self.forbid:
            return 403, {"detail": "You do not have permission to perform this action."}
        rows = [] if self.hide else sorted([j for j in self.jobs if j["job_template"] == int(q["job_template"]) and j["id"] < int(q["id__lt"])
                                            and j["status"] in q["status__in"].split(",")], key=lambda j: -j["id"])
        size, page = int(q["page_size"]), int(q["page"])
        part = rows[(page - 1) * size:page * size]
        if page > 1 and not part:
            return 404, {"detail": "Invalid page."}
        return 200, {"count": len(rows), "next": "x" if page * size < len(rows) else None,
                     "results": [dict((k, v) for k, v in j.items() if k != "artifacts") for j in part]}


class History(unittest.TestCase):
    def read(self, api, days=30, max_runs=400, skip=()):
        return hist.history(api, 5000, 88, days, max_runs, set(skip), now=T0)

    def test_the_templates_earlier_jobs_with_a_point_oldest_first(self):
        api = FakeApi(fx.jobs())
        runs, counts, why = self.read(api)
        self.assertEqual(why, "")
        self.assertEqual(counts, {"listed": 32, "read": 30, "skipped": 0, "no_point": 2})       # a failed run, an older version's
        self.assertEqual([r["job"] for r in runs], sorted(r["job"] for r in runs))
        self.assertEqual(len(runs), 30)
        self.assertTrue(all(m == "GET" for m, _ in api.calls))
        self.assertFalse([p for _, p in api.calls if p in ("jobs/4002/", "jobs/4005/")])       # canceled; another template's

    def test_more_jobs_than_a_page(self):
        api = FakeApi(fx.jobs() + [dict(j, id=j["id"] + 500) for j in fx.jobs() if j["status"] == "successful" and j["job_template"] == 88])
        runs, counts, why = self.read(api)
        self.assertEqual((why, counts["listed"]), ("", 63))
        self.assertEqual(hist.PAGE, 50)
        self.assertEqual(len([p for _, p in api.calls if p.startswith("jobs/?")]), 2)

    def test_the_jobs_the_database_gave_are_not_read_again(self):
        api = FakeApi(fx.jobs())
        runs, counts, why = self.read(api, skip=[4000, 4003, 4006])
        self.assertEqual((counts["skipped"], counts["read"]), (3, 27))
        self.assertNotIn(("GET", "jobs/4003/"), api.calls)

    def test_only_the_window(self):
        runs, counts, why = self.read(FakeApi(fx.jobs()), days=10)
        self.assertLessEqual(len(runs), 11)
        self.assertGreaterEqual(len(runs), 10)

    def test_at_most_max_runs_the_newest(self):
        runs, counts, why = self.read(FakeApi(fx.jobs()), max_runs=5)
        self.assertEqual(counts["listed"], 5)
        self.assertEqual(runs[-1]["job"], 4087)

    def test_an_answer_cut_off_is_asked_for_once_more(self):
        api = FakeApi(fx.jobs(), cut=[4087])
        runs, counts, why = self.read(api)
        self.assertEqual(counts["read"], 30)
        self.assertEqual(api.calls.count(("GET", "jobs/4087/")), 2)

    def test_the_list_refused_says_the_role_to_give(self):
        runs, counts, why = self.read(FakeApi(fx.jobs(), forbid=True))
        self.assertEqual(runs, [])
        self.assertIn("AAP refused the list of this template's jobs (HTTP 403)", why)
        self.assertIn("Organization Auditor", why)

    def test_a_user_who_sees_no_job_is_not_a_first_run(self):
        runs, counts, why = self.read(FakeApi(fx.jobs(), hide=True))
        self.assertIn("The AAP user auditor cannot see this template's jobs", why)
        self.assertIn("job template 88", why)

    def test_a_first_run_is_no_error(self):
        api = FakeApi([{"id": 5000, "job_template": 88, "status": "running", "finished": None, "artifacts": {}}])
        self.assertEqual(self.read(api), ([], {"listed": 0, "read": 0, "skipped": 0, "no_point": 0}, ""))

    def test_no_credential(self):
        runs, counts, why = self.read(FakeApi([], why=hist.NO_CREDENTIAL))
        self.assertIn("Red Hat Ansible Automation Platform", why)

    def test_seconds(self):
        self.assertEqual(hist.seconds("2026-11-13T06:00:05.123456Z"), T0)
        self.assertIsNone(hist.seconds(None))


class FakeCursor(object):
    def __init__(self, rows):
        self.rows, self.sql, self.out = rows, [], []

    def execute(self, sql, args=None):
        self.sql.append((sql, args))
        if sql.startswith("SELECT DISTINCT read_at"):
            self.out = [(t,) for t in sorted(set(r[0] for r in self.rows if args[0] <= r[0] < args[1]))]
        elif sql.startswith("SELECT read_at, job"):
            self.out = sorted([r for r in self.rows if r[0] in args], key=lambda r: (r[0], r[2], r[4], r[5]))
        return len(self.out)

    def fetchall(self):
        return self.out


class FakeConn(object):
    def __init__(self, rows):
        self.cur = FakeCursor(rows)

    def cursor(self):
        return self.cur


class Database(unittest.TestCase):
    def rows(self, days):
        out = []
        for d in days:
            p = point(d)
            at = cdb.stamp(p["read_at"])
            for r in f.ontap_capacity_db_rows(p):
                out.append((at, 4000 + d, r[0], r[1], r[2], r[3], r[6], r[7], r[8], r[9]))
        return out

    def test_what_is_read_back_is_what_was_written(self):
        """A point -> its rows -> the runs read back: the same sizes, in the artifact's layout, with the job."""
        conn = FakeConn(self.rows([3, 2, 1]))
        runs = cdb.read(conn, cdb.stamp(fx.NOW), 30, 400)
        self.assertEqual([r["job"] for r in runs], [4003, 4002, 4001])
        want = point(2)
        got = runs[1]
        self.assertEqual(got["read_at"], want["read_at"])
        for a, b in zip(sorted(got["clusters"], key=lambda c: c["name"]), want["clusters"]):
            self.assertEqual((a["uuid"], a["name"], a["size_b"], a["used_b"]), (b["uuid"], b["name"], b["size_b"], b["used_b"]))
            for part in ("aggregates", "volumes", "snapshot_reserve"):
                self.assertEqual(sorted(json.dumps(x, sort_keys=True) for x in a[part]),
                                 sorted(json.dumps(dict(x, reserve_pct=float(x["reserve_pct"])) if "reserve_pct" in x else x, sort_keys=True)
                                        for x in b[part]), part)
        # and the report takes them as it takes AAP's
        got = f.ontap_capacity_runs(point(), [], runs, {"days": 30})
        self.assertEqual(got["counts"], {"aap": 0, "db": 3, "both": 0, "used": 4})

    def test_not_this_runs_own_rows_and_not_older_than_the_window(self):
        conn = FakeConn(self.rows([40, 5, 0]))
        runs = cdb.read(conn, cdb.stamp(fx.NOW), 30, 400)
        self.assertEqual([r["job"] for r in runs], [4005])

    def test_more_runs_than_max_runs_are_spread_with_the_newest(self):
        conn = FakeConn(self.rows(range(1, 21)))
        runs = cdb.read(conn, cdb.stamp(fx.NOW), 30, 5)
        self.assertEqual(len(runs), 5)
        self.assertEqual(runs[-1]["job"], 4001)
        self.assertLess(runs[0]["job"] - 4000, 21)
        self.assertGreater(runs[0]["job"] - 4000, 12)                   # from the whole window, not the last five days

    def test_stamp(self):
        self.assertEqual(cdb.stamp("2026-11-13T06:00:05Z").isoformat(), "2026-11-13T06:00:05")
        self.assertIsNone(cdb.stamp("soon"))

    def test_the_statement_in_the_doc_is_the_modules(self):
        doc = open(os.path.join(REPO, "docs", "REPORT_DATABASE.md")).read()
        for part in cdb.ddl().split("\n\n"):
            self.assertIn(part, doc, "docs/REPORT_DATABASE.md: the statement differs from site_ontap_capacity_db.ddl()")
        self.assertIn("PRIMARY KEY (cluster, kind, uuid, read_at)", cdb.ddl())

    def test_the_settings_are_in_the_defaults_and_the_docs(self):
        defaults = open(os.path.join(REPO, "roles", "ontap_report", "defaults", "main.yml")).read()
        ref = open(os.path.join(REPO, "docs", "VARIABLES_REFERENCE.md")).read()
        guide = open(os.path.join(REPO, "docs", "NETAPP.md")).read()
        for name in ("ontap_capacity_days", "ontap_volume_again_days", "ontap_volume_fast_days", "ontap_capacity_top", "ontap_capacity_runs_max",
                     "ontap_capacity_db", "ontap_capacity_db_create", "ontap_capacity_db_timeout", "ontap_capacity_db_keep_days",
                     "ontap_capacity_fail"):
            self.assertIn("\n%s:" % name, defaults, name)
            self.assertIn("`%s`" % name, ref, name)
            self.assertIn("`%s`" % name, guide, name)


if __name__ == "__main__":
    unittest.main()
