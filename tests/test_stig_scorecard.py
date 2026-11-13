#!/usr/bin/env python3
"""Unit tests for the STIG compliance scorecard (1.58.0), a part of the "STIG Manager - checklist import" report: what is
read of STIG Manager's findings (roles/stigman_ckl_import/library/site_stig_scorecard.py), this run's point, the runs of
AAP's job history and the report database as one list, the weekly points, the run "last week" is compared with, closed
and opened rule by rule, fewer than 2 weekly points, the trend line, fixed on the host and still Open, the scorecard and
its join with the import's report (plugins/filter/stig_scorecard_filters.py), what the drift reports keep for it
(stig_fixed_open), AAP's job history read (site_stig_scorecard_history.py) and the database module's statements
(site_stig_scorecard_db.py). The made-up collections: tests/stig/scorecard_fixture.py. Standard library plus ansible-core:
python3 tests/test_stig_scorecard.py"""
import datetime
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "roles", "stigman_ckl_import", "library"))
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
sys.path.insert(0, os.path.join(REPO, "plugins", "module_utils"))
sys.path.insert(0, os.path.join(HERE, "stig"))
import site_report_db  # noqa: E402
import ansible.module_utils  # noqa: E402
sys.modules.setdefault("ansible.module_utils.site_report_db", site_report_db)
ansible.module_utils.site_report_db = site_report_db
import site_aap  # noqa: E402
sys.modules.setdefault("ansible.module_utils.site_aap", site_aap)
ansible.module_utils.site_aap = site_aap
import scorecard_fixture as fx  # noqa: E402
import stig_scorecard_filters as f  # noqa: E402
import stig_filters  # noqa: E402
import site_stig_scorecard as sc  # noqa: E402
import site_stig_scorecard_db as cdb  # noqa: E402
import site_stig_scorecard_history as hist  # noqa: E402

DAY = 86400.0
NOW = "2026-11-13T06:00:05Z"
T0 = f.parse_time(NOW)
RHEL, VMW, WIN = "SITEA - RHEL 9", "SITEA - VMware", "SITEA - Windows Server"


def findings(s, cid):
    """A collection's state as STIG Manager answers GET /collections/ID/findings (by rule, with the assets)."""
    out = []
    for sv, hosts in sorted(s.get(cid, {}).items()):
        rule, version, severity, title = fx.RULES[sv]
        out.append({"ruleId": rule, "version": version, "severity": severity, "title": title, "assetCount": len(hosts),
                    "assets": [{"assetId": str(i), "name": h} for i, h in enumerate(hosts)]})
    return out


def read_of(s=None, read_at=NOW, errors=None):
    """What site_stig_scorecard gives for a state (today's by default)."""
    s = fx.state(0) if s is None else s
    cols = []
    for cid, name, assets in sorted(fx.COLLECTIONS, key=lambda c: c[1].lower()):
        if cid not in s:
            continue
        rec = {"id": cid, "name": name, "assets": 0, "cat1": 0, "cat2": 0, "cat3": 0, "rules": [], "error": (errors or {}).get(cid, "")}
        if not rec["error"]:
            rec["rules"], cats = sc.findings_of(findings(s, cid))
            rec.update(cat1=cats[1], cat2=cats[2], cat3=cats[3], assets=len(assets))
        cols.append(rec)
    return {"ok": True, "error": "", "read_at": read_at, "collections": cols, "missing": []}


def earlier(weeks=8):
    """The earlier runs as AAP's job history gives them: the import template's points, each with its job."""
    return [dict(j["artifacts"]["stig_scorecard"], job=j["id"]) for j in fx.jobs(NOW, weeks)
            if j["job_template"] == fx.TEMPLATE and j["artifacts"].get("stig_scorecard")]


def card(runs=None, read=None, opts=None):
    read = read or read_of()
    point = f.stig_scorecard_point(read)
    got = f.stig_scorecard_runs(point, earlier() if runs is None else runs, [], {"weeks": (opts or {}).get("weeks", 8), "job": 5000})
    return f.stig_scorecard(read, point, got["runs"], dict({"weeks": 8, "top": 5}, **(opts or {})))


def section(doc, start, key="sections"):
    return next(s for s in doc[key] if s["title"].startswith(start))


def tile(doc, label):
    return next(t for t in doc["tiles"] if t["label"] == label)


def words(doc):
    return json.dumps(doc, sort_keys=True, default=str).lower()


class Read(unittest.TestCase):
    def test_a_rule_keeps_its_number_across_revisions(self):
        self.assertEqual(sc.sv_of("SV-257985r1069364_rule"), "SV-257985")
        self.assertEqual(sc.sv_of("SV-257985r991589_rule"), "SV-257985")
        self.assertEqual(sc.sv_of("custom-rule"), "custom-rule")

    def test_findings_are_rule_and_asset_pairs_by_category(self):
        rules, cats = sc.findings_of(findings(fx.state(0), "2"))
        self.assertEqual(cats, {1: 1, 2: 8, 3: 2})
        self.assertEqual(rules[0]["sv"], "SV-257777")                               # CAT I first
        self.assertEqual([r["sv"] for r in rules if r["cat"] == 2], ["SV-258036", "SV-257985"])   # then the most hosts
        self.assertEqual(rules[1]["assets"], ["rhel01", "rhel03", "rhel04", "rhel05", "rhel06"])
        self.assertEqual(rules[1]["version"], "RHEL-09-291010")

    def test_an_asset_counts_once_and_no_projection_counts_assetcount(self):
        rules, cats = sc.findings_of([
            {"ruleId": "SV-1r1_rule", "severity": "high", "assets": [{"name": "a"}, {"name": "a"}, {"name": "b"}], "assetCount": 9},
            {"ruleId": "SV-2r1_rule", "severity": "low", "assetCount": 4},
            {"ruleId": "SV-3r1_rule", "severity": "medium", "assetCount": 0},
            {"severity": "high", "assetCount": 3}, "junk"])
        self.assertEqual(cats, {1: 2, 2: 0, 3: 4})
        self.assertEqual([(r["sv"], r["count"]) for r in rules], [("SV-1", 2), ("SV-2", 4)])

    def test_an_unknown_severity_is_cat_ii(self):
        self.assertEqual(sc.findings_of([{"ruleId": "SV-1r1_rule", "severity": "unknown", "assetCount": 2}])[1], {1: 0, 2: 2, 3: 0})

    def test_the_collections_asked_for_by_name_or_id_any_case(self):
        cols = [{"collectionId": cid, "name": name} for cid, name, hosts in fx.COLLECTIONS]
        self.assertEqual(sc.wanted(cols, []), (cols, []))
        self.assertEqual(sc.wanted(cols, ["*"]), (cols, []))
        got, missing = sc.wanted(cols, ["sitea - rhel 9", "3", "SITEA - Gone", " ", "2"])
        self.assertEqual([c["collectionId"] for c in got], ["2", "3"])
        self.assertEqual(missing, ["SITEA - Gone"])


class Point(unittest.TestCase):
    def test_this_runs_point(self):
        p = f.stig_scorecard_point(read_of())
        want = fx.point(fx.state(0), NOW)
        self.assertEqual(p, dict(want, collections=sorted(want["collections"], key=lambda c: c["name"])))
        by = dict((c["name"], c) for c in p["collections"])
        self.assertEqual((by[RHEL]["cat1"], by[RHEL]["cat2"], by[RHEL]["cat3"], by[RHEL]["assets"]), (1, 8, 2, 6))
        self.assertEqual(by[RHEL]["r2"], {"SV-257985": 3, "SV-258036": 5})
        self.assertEqual(by[WIN]["r1"], {"SV-254239": 1, "SV-254247": 2})

    def test_the_point_is_small_and_has_no_host_names(self):
        text = json.dumps(f.stig_scorecard_point(read_of()))
        self.assertLess(len(text), 1500)
        for cid, name, hosts in fx.COLLECTIONS:
            for h in hosts:
                self.assertNotIn(h, text)

    def test_a_collection_not_read_has_no_point(self):
        p = f.stig_scorecard_point(read_of(errors={"3": "HTTP 403"}))
        self.assertEqual([c["name"] for c in p["collections"]], [RHEL, VMW])

    def test_nothing_read(self):
        self.assertEqual(f.stig_scorecard_point(None), {"v": 1, "read_at": "", "collections": []})
        self.assertEqual(f.stig_scorecard_point({"ok": False, "error": "x"})["collections"], [])

    def test_the_database_rows(self):
        p = f.stig_scorecard_point(read_of())
        rows = cdb.rows_of(p, cdb.stamp(NOW), 5000)
        self.assertEqual(len(rows), 3)
        r = next(x for x in rows if x[3] == RHEL)
        self.assertEqual(r[:8], (datetime.datetime(2026, 11, 13, 6, 0, 5), 5000, "2", RHEL, 6, 1, 8, 2))
        self.assertEqual(json.loads(r[8]), {"r1": {"SV-257777": 1}, "r2": {"SV-257985": 3, "SV-258036": 5}, "r3": {"SV-257842": 2}})
        self.assertEqual(cdb.rows_of({"collections": [{"name": "no id"}, "junk"]}, cdb.stamp(NOW), 1), [])


class Runs(unittest.TestCase):
    def runs(self, aap=None, db=None, **opts):
        return f.stig_scorecard_runs(fx.point(fx.state(0), NOW), aap, db, dict({"weeks": 8, "job": 5000}, **opts))

    def test_each_run_once_oldest_first(self):
        got = self.runs(list(reversed(earlier())))
        self.assertEqual(got["counts"], {"aap": 9, "db": 0, "both": 0, "used": 10})
        self.assertEqual([r["job"] for r in got["runs"]][:3], [4001, 4002, 4003])
        self.assertEqual(got["runs"][-1]["job"], "5000")
        self.assertEqual(got["runs"][-1]["_t"], T0)

    def test_a_run_in_aap_and_in_the_database_counts_once(self):
        got = self.runs(earlier(), earlier()[:4])
        self.assertEqual(got["counts"], {"aap": 9, "db": 0, "both": 4, "used": 10})

    def test_the_same_minute_without_a_job_id_counts_once(self):
        db = [dict(r, job=0, read_at=r["read_at"][:17] + "59Z") for r in earlier()[:2]]
        got = self.runs(earlier(), db)
        self.assertEqual((got["counts"]["both"], got["counts"]["used"]), (2, 10))

    def test_the_database_fills_what_aap_no_longer_has(self):
        got = self.runs(earlier()[5:], earlier())
        self.assertEqual(got["counts"], {"aap": 4, "db": 5, "both": 4, "used": 10})
        self.assertEqual([r["job"] for r in got["runs"]][:9], list(range(4001, 4010)))

    def test_this_job_launched_again_is_not_its_own_history(self):
        again = dict(fx.point(fx.state(3), fx.stamp(T0 - 3600)), job=5000)
        self.assertEqual(self.runs([again])["counts"]["used"], 1)

    def test_only_the_window_and_nothing_after_this_run(self):
        late = dict(fx.point(fx.state(0), fx.stamp(T0 + 3600)), job=6000)
        got = self.runs(earlier() + [late], weeks=3)
        self.assertEqual([r["job"] for r in got["runs"]], [4006, 4007, 4008, 4009, "5000"])

    def test_what_is_not_a_point_is_left_out(self):
        got = self.runs([None, "x", {"read_at": "soon", "collections": []}, {"read_at": NOW}, {"read_at": fx.stamp(T0 - DAY), "collections": {}}])
        self.assertEqual(got["counts"]["used"], 1)


class Weeks(unittest.TestCase):
    def runs(self, days):
        return [{"_t": T0 - d * DAY, "d": d} for d in sorted(days, reverse=True)]

    def test_one_point_a_week_the_weeks_last_run(self):
        wk = f.weekly(self.runs([21, 14, 10, 7, 3, 0]), T0, 4)
        self.assertEqual([r and r["d"] for t, r in wk], [21, 14, 7, 0])
        self.assertEqual([t for t, r in wk], [T0 - 21 * DAY, T0 - 14 * DAY, T0 - 7 * DAY, T0])

    def test_a_run_a_little_late_still_counts_for_its_week(self):
        wk = f.weekly(self.runs([6.6, 0]), T0, 2)
        self.assertEqual([r and r["d"] for t, r in wk], [6.6, 0])
        wk = f.weekly(self.runs([6.4, 0]), T0, 2)                 # more than half a day late: it is this week's
        self.assertEqual([r and r["d"] for t, r in wk], [0])

    def test_a_week_without_a_run_is_a_gap_and_the_weeks_before_the_first_run_are_left_out(self):
        wk = f.weekly(self.runs([21, 7, 0]), T0, 8)
        self.assertEqual([r and r["d"] for t, r in wk], [21, None, 7, 0])

    def test_the_first_run(self):
        self.assertEqual([r and r["d"] for t, r in f.weekly(self.runs([0]), T0, 8)], [0])

    def test_last_week_is_the_run_nearest_7_days_6_to_8_days_ago(self):
        self.assertEqual(f.baseline(self.runs([8, 7.2, 6.1, 3, 0]), T0)["d"], 7.2)
        self.assertEqual(f.baseline(self.runs([8, 6.5, 0]), T0)["d"], 6.5)
        self.assertEqual(f.baseline(self.runs([8.02, 0]), T0)["d"], 8.02)         # an hour of slack for a run that took longer
        self.assertIsNone(f.baseline(self.runs([9, 5, 3, 0]), T0))
        self.assertIsNone(f.baseline([], T0))


class Change(unittest.TestCase):
    def test_closed_and_opened_rule_by_rule(self):
        was = {"r1": {"SV-1": 2}, "r2": {"SV-2": 6, "SV-3": 1, "SV-4": 4}, "r3": {}}
        now = {"r1": {"SV-1": 2}, "r2": {"SV-2": 3, "SV-3": 2}, "r3": {"SV-5": 1}}
        ch = f.change(was, now)
        self.assertEqual((ch["closed"], ch["opened"]), (7, 2))
        self.assertEqual(ch["closed_rules"], [("SV-4", 2, 4), ("SV-2", 2, 3)])
        self.assertEqual(ch["opened_rules"], [("SV-3", 2, 1), ("SV-5", 3, 1)])

    def test_the_fixtures_week(self):
        was, now = fx.point(fx.state(1), "x"), fx.point(fx.state(0), "x")
        by = dict((c["name"], f.change(f._find(was, c), c)) for c in now["collections"])
        self.assertEqual((by[RHEL]["closed"], by[RHEL]["opened"]), (4, 1))
        self.assertEqual(by[RHEL]["closed_rules"], [("SV-257985", 2, 3), ("SV-258036", 2, 1)])
        self.assertEqual((by[WIN]["closed"], by[WIN]["opened"]), (0, 1))
        self.assertEqual((by[VMW]["closed"], by[VMW]["opened"]), (0, 0))

    def test_a_rule_closed_on_one_host_and_opened_on_another_counts_as_neither(self):
        self.assertEqual(f.change({"r2": {"SV-1": 3}}, {"r2": {"SV-1": 3}})["closed"], 0)

    def test_a_rule_that_changed_category_is_one_rule(self):
        ch = f.change({"r2": {"SV-1": 3}}, {"r1": {"SV-1": 1}})
        self.assertEqual((ch["closed_rules"], ch["opened_rules"]), ([("SV-1", 1, 2)], []))

    def test_nothing_before_or_now(self):
        self.assertEqual(f.change(None, {"r1": {"SV-1": 2}})["opened"], 2)
        self.assertEqual(f.change({"r1": {"SV-1": 2}}, {})["closed"], 2)

    def test_a_collection_is_found_by_its_id_then_by_its_name(self):
        run = {"collections": [{"id": "7", "name": "A"}, {"id": "8", "name": "b"}]}
        self.assertEqual(f._find(run, {"id": "8", "name": "A"})["name"], "b")
        self.assertEqual(f._find(run, {"id": "9", "name": "B"})["id"], "8")
        self.assertIsNone(f._find(run, {"id": "9", "name": "c"}))

    def test_the_hover_lists_ten_rules_then_how_many_more(self):
        rules = [("SV-%d" % i, 2, 1) for i in range(13)]
        lines = f._ids(rules, "Closed")
        self.assertEqual(lines[0], "Closed 13:")
        self.assertEqual(len(lines), 12)
        self.assertEqual(lines[-1], "  3 more (the workbook lists every rule)")
        self.assertEqual(f._ids([], "Opened"), ["Opened: none"])


class Spark(unittest.TestCase):
    HEADS = ["16 Oct", "23 Oct", "30 Oct", "6 Nov", "13 Nov"]

    def test_fewer_than_two_points_no_line(self):
        self.assertEqual(f.spark([], self.HEADS, "x"), {})
        self.assertEqual(f.spark([(4, 11, 1, 8, 2)], self.HEADS, "x"), {})

    def test_the_line_fits_its_box(self):
        sp = f.spark([(0, 20, 1, 17, 2), (1, 19, 1, 16, 2), (2, 18, 1, 15, 2), (3, 14, 1, 12, 1), (4, 11, 1, 8, 2)], self.HEADS, RHEL)
        pts = [tuple(float(v) for v in p.split(",")) for p in sp["points"].split()]
        self.assertEqual(len(pts), 5)
        for x, y in pts:
            self.assertTrue(f.SPARK_PAD <= x <= sp["w"] - f.SPARK_PAD and f.SPARK_PAD <= y <= sp["h"] - f.SPARK_PAD, (x, y))
        self.assertEqual((pts[0][0], pts[-1][0]), (f.SPARK_PAD, sp["w"] - f.SPARK_PAD))
        self.assertEqual((pts[0][1], pts[-1][1]), (f.SPARK_PAD, sp["h"] - f.SPARK_PAD))    # the most open is the top
        self.assertEqual(sp["last"], list(pts[-1]))
        self.assertEqual(sp["label"], "SITEA - RHEL 9, open findings: 20 in the week to 16 Oct, 11 now; by week: 20, 19, 18, 14, 11")
        self.assertEqual(sp["data_points"][-1], {"x": pts[-1][0], "y": pts[-1][1], "label": "Week to 13 Nov",
                                                 "text": "11 open: CAT I 1, CAT II 8, CAT III 2; -3 since the point before"})
        self.assertNotIn("since", sp["data_points"][0]["text"])

    def test_a_week_without_a_run_is_a_gap_in_x(self):
        sp = f.spark([(0, 5, 0, 5, 0), (2, 5, 0, 5, 0), (4, 5, 0, 5, 0)], self.HEADS, "x")
        xs = [float(p.split(",")[0]) for p in sp["points"].split()]
        self.assertEqual(xs[1] - xs[0], xs[2] - xs[1])
        self.assertEqual(xs[1], sp["w"] / 2.0)
        self.assertEqual(set(p.split(",")[1] for p in sp["points"].split()), {str(sp["h"] / 2.0)})   # a flat line in the middle
        self.assertIn("no change since the point before", sp["data_points"][1]["text"])


class Fixed(unittest.TestCase):
    ROW = ["rhel01.example.mil", RHEL, "rhel01", "SV-257985", "CAT II", "RHEL 9 must not  allow\na root login over SSH.", "2026-11-12T06:00:05Z"]

    def test_only_what_is_still_open_now(self):
        gone = ["rhel03.example.mil", RHEL, "rhel03", "SV-257900", "CAT II", "x", "2026-11-12T06:00:05Z"]       # closed since
        other = ["rhel04.example.mil", RHEL, "rhel04", "SV-257985", "CAT II", "x", "2026-11-12T06:00:05Z"]     # open, not on this host
        got = f.fixed_rows({"rows": [gone, self.ROW, other, ["short"], None]}, read_of())
        self.assertEqual(got, [["rhel01.example.mil", RHEL, "SV-257985", "CAT II", "RHEL 9 must not allow a root login over SSH.",
                                "2026-11-12T06:00:05Z"]])

    def test_the_asset_by_its_name_or_its_short_name_any_case(self):
        for host, asset in (("RHEL01.example.mil", "rhel01.example.mil"), ("rhel01", "RHEL01"), ("rhel01.example.mil", "other-name")):
            row = [host, RHEL.upper(), asset] + self.ROW[3:]
            self.assertEqual(len(f.fixed_rows({"rows": [row]}, read_of())), 1, (host, asset))

    def test_a_collection_not_read_this_run_gives_no_row(self):
        self.assertEqual(f.fixed_rows({"rows": [self.ROW]}, read_of(errors={"2": "HTTP 403"})), [])
        self.assertEqual(f.fixed_rows(None, read_of()), [])

    def test_what_the_drift_report_keeps(self):
        results = [
            {"host": "rhel01.example.mil", "scanned": True, "checklist_time": "2026-11-12T05:10:00Z",
             "stigman": {"status": "found", "collection": RHEL, "asset": "rhel01"},
             "fixed_open": [{"key": "SV-257985r1069364_rule", "severity": "medium", "title": "RHEL 9 must  not allow a root login over SSH."},
                            {"key": "V-257985", "severity": "medium", "title": "no SV number"}]},
            {"host": "rhel03.example.mil", "scanned": True, "stigman": {"status": "found", "collection": RHEL, "asset": "rhel03"}, "fixed_open": []},
            {"host": "rhel04.example.mil", "scanned": False, "stigman": {"status": "found", "collection": RHEL, "asset": "rhel04"}},
            {"host": "rhel05.example.mil", "scanned": True, "stigman": {"status": "missing"}},
            {"host": "rhel06.example.mil", "scanned": True}, "junk"]
        got = stig_filters.stig_fixed_open(results, NOW)
        self.assertEqual(got["assets"], [[RHEL, "rhel01"], [RHEL, "rhel03"]])
        self.assertEqual(len(got["rows"]), 1)
        row = got["rows"][0]
        self.assertEqual(row[:4] + row[5:], ["rhel01.example.mil", RHEL, "rhel01", "SV-257985", "RHEL 9 must not allow a root login over SSH.",
                                             "2026-11-12T05:10:00Z"])
        self.assertEqual((got["v"], got["read_at"], got["more"]), (1, NOW, 0))
        self.assertEqual(len(f.fixed_rows(got, read_of())), 1)                     # and the scorecard takes it

    def test_the_drift_reports_rows_are_capped(self):
        results = [{"host": "h", "scanned": True, "stigman": {"status": "found", "collection": "c", "asset": "h"},
                    "fixed_open": [{"key": "SV-%dr1_rule" % i, "severity": "low", "title": "t"} for i in range(30)]}]
        got = stig_filters.stig_fixed_open(results, NOW, 10)
        self.assertEqual((len(got["rows"]), got["more"]), (10, 20))


class Scorecard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        api = FakeApi(fx.jobs(NOW))
        cls.fixed = hist.fixed(api, ["STIG drift report", "Windows STIG drift report"], 14, 20, now=T0)
        cls.doc = card(opts={"fixed": cls.fixed, "api": "https://stigman.example.mil/api", "history": ["9 earlier points from AAP."]})

    def test_the_title_and_the_tiles(self):
        d = self.doc
        self.assertEqual(d["title"], "STIG compliance: 4 CAT I open; 4 findings closed this week")
        self.assertEqual(d["status"], "critical")
        self.assertEqual(d["subtitle"], "Read 2026-11-13 06:00 UTC from STIG Manager at stigman.example.mil; 3 collections, 13 assets; "
                                        "compared with 6 Nov")
        self.assertEqual([(t["label"], t["value"]) for t in d["tiles"]],
                         [("CAT I open", 4), ("CAT II open", 15), ("CAT III open", 3), ("Closed this week", 4), ("Opened this week", 2),
                          ("Fixed on host, still open", 2)])
        self.assertEqual(tile(d, "CAT I open")["status"], "critical")
        self.assertEqual(tile(d, "Closed this week")["status"], "ok")
        self.assertNotIn("status", tile(d, "Opened this week"))                       # fewer opened than closed
        self.assertEqual((tile(d, "Fixed on host, still open")["status"], tile(d, "Fixed on host, still open")["note"]), ("low", "Low"))
        self.assertEqual(d["counts"], {"cat1": 4, "cat2": 15, "cat3": 3, "assets": 13, "closed": 4, "opened": 2, "collections": 3, "not_read": 0,
                                       "fixed": 2, "weekly_points": 8, "compared": True, "rules": 10})

    def test_the_sections_results_first(self):
        self.assertEqual([s["title"] for s in self.doc["sections"]],
                         ["Start here", "By collection (3)", "Top 5 rules open across hosts", "Fixed on the host, still Open in STIG Manager (2)",
                          "The numbers behind the lines (open findings per week)"])
        self.assertEqual([s["title"] for s in self.doc["web"]], [s["title"] for s in self.doc["sections"]])
        self.assertEqual(self.doc["notes"]["title"], "How the scorecard counts")
        self.assertTrue(self.doc["notes"]["collapsed"])

    def test_the_cards(self):
        cards = section(self.doc, "Start here")["cards"]
        self.assertEqual([c["chip"] for c in cards], ["CAT I first", "This week", "Paperwork, not hosts"])
        self.assertEqual(cards[0]["status"], "critical")
        self.assertIn("SV-254247 (WN22-00-000110)", cards[0]["next"])
        self.assertNotIn("..", cards[0]["next"])
        self.assertEqual((cards[1]["title"], cards[1]["status"], cards[1]["meta"]), ("4 closed, 2 opened", "ok", "since 6 Nov"))
        self.assertEqual(cards[1]["subject"], "SITEA - RHEL 9 improved most (4 closed, 1 opened). SITEA - Windows Server got worse most "
                                              "(1 opened, 0 closed).")
        self.assertEqual((cards[2]["title"], cards[2]["status"], cards[2]["meta"]), ("2 fixed, still open", "low", "Low"))

    def test_by_collection(self):
        s = section(self.doc, "By collection")
        self.assertEqual(s["columns"], ["Collection", "Assets", "CAT I open", "CAT II open", "CAT III open", "Since last week", "Open, 8 weeks",
                                        "Status"])
        by = dict((r[0], r) for r in s["rows"])
        self.assertEqual(by[RHEL], [RHEL, 6, 1, 8, 2, u"▼ Better\n4 closed, 1 opened", u"20 → 11", "CAT I open"])
        self.assertEqual(by[VMW], [VMW, 3, 0, 3, 0, u"▬ Same\n0 closed, none opened", u"3 → 3", "OK"])
        self.assertEqual(by[WIN], [WIN, 4, 3, 4, 1, u"▲ Worse\n0 closed, 1 opened", u"7 → 8", "CAT I open"])
        self.assertEqual(s["row_status"], ["critical", "", "critical"])
        self.assertEqual(s["cell_status"][0][2], "critical")
        self.assertEqual(s["cell_status"][1], [""] * 8)
        tip = s["tips"][0][5].split("\n")
        self.assertEqual(tip[:4], ["SITEA - RHEL 9 since 6 Nov", "Closed 4:", "  SV-257985 on 3 hosts", "  SV-258036 on 1 host"])
        self.assertEqual(tip[4:], ["Opened 1:", "  SV-257842 on 1 host"])
        self.assertNotIn("lines", s)

    def test_worse_without_cat_i_is_a_warning(self):
        s = fx.state(0)
        del s["3"]["SV-254239"], s["3"]["SV-254247"]
        was = fx.state(1)
        del was["3"]["SV-254239"], was["3"]["SV-254247"]
        was["3"]["SV-254269"] = ["win01"]
        d = card(runs=[dict(fx.point(was, fx.stamp(T0 - 7 * DAY)), job=1)], read=read_of(s))
        sec = section(d, "By collection")
        row = next(i for i, r in enumerate(sec["rows"]) if r[0] == WIN)
        self.assertEqual((sec["rows"][row][7], sec["row_status"][row]), ("Worse", "warning"))
        self.assertIn("more findings opened than closed", sec["tips"][row][7])

    def test_cat_ii_over_the_level(self):
        d = card(opts={"cat2_warn": 3})
        sec = section(d, "By collection")
        row = next(i for i, r in enumerate(sec["rows"]) if r[0] == VMW)
        self.assertEqual((sec["rows"][row][7], sec["row_status"][row]), ("CAT II over 3", "warning"))
        self.assertIn("stigman_scorecard_cat2_warn", sec["tips"][row][7])
        self.assertEqual(section(self.doc, "By collection")["rows"][row][7], "OK")           # 0 = no level

    def test_the_website_has_a_trend_line_per_collection_the_email_none(self):
        web, mail = section(self.doc, "By collection", "web"), section(self.doc, "By collection")
        self.assertEqual(web["kinds"][6], "spark")
        self.assertNotIn("spark", mail["kinds"])
        self.assertNotIn("sparks", mail)
        self.assertEqual(len(web["sparks"]), 3)
        by = dict((r[0], sp) for r, sp in zip(web["rows"], web["sparks"]))
        self.assertEqual(len(by[RHEL]["points"].split()), 8)
        self.assertEqual(len(by[VMW]["points"].split()), 4)                           # the collection was made later
        self.assertTrue(by[RHEL]["label"].endswith("by week: 20, 19, 18, 17, 16, 15, 14, 11"))
        row = next(r for r in web["rows"] if r[0] == RHEL)
        self.assertEqual(row[6], by[RHEL]["label"])                                   # the cell's text is the line in words
        self.assertEqual([r[:6] + r[7:] for r in web["rows"]], [r[:6] + r[7:] for r in mail["rows"]])

    def test_the_numbers_behind_the_lines(self):
        s = section(self.doc, "The numbers behind")
        self.assertEqual(s["columns"], ["Collection", "25 Sep", "2 Oct", "9 Oct", "16 Oct", "23 Oct", "30 Oct", "6 Nov", "13 Nov"])
        by = dict((r[0], r[1:]) for r in s["rows"])
        self.assertEqual(by[RHEL], [20, 19, 18, 17, 16, 15, 14, 11])
        self.assertEqual(by[VMW], ["-", "-", "-", "-", 3, 3, 3, 3])
        self.assertEqual(by[WIN], [7, 7, 7, 7, 7, 7, 7, 8])

    def test_the_top_rules(self):
        s = section(self.doc, "Top 5 rules")
        self.assertEqual([(r[0], r[1], r[3]) for r in s["rows"]],
                         [("SV-258036 (RHEL-09-291010)", "CAT II", 5), ("SV-254269 (WN22-00-000330)", "CAT II", 4),
                          ("SV-257985 (RHEL-09-255045)", "CAT II", 3), ("SV-254247 (WN22-00-000110)", "CAT I", 2),
                          ("SV-256375 (ESXI-70-000001)", "CAT II", 2)])
        self.assertEqual(s["row_status"], ["", "", "", "critical", ""])
        self.assertEqual(s["rows"][0][4], "SITEA - RHEL 9 (5)")
        self.assertIn("rhel01, rhel03, rhel04, rhel05, rhel06", s["tips"][0][3])
        d = card(opts={"top": 2})
        self.assertEqual(len(section(d, "Top 2 rules")["rows"]), 2)
        d = card(opts={"top": 50})
        self.assertEqual(len(section(d, "The rules open across hosts (10)")["rows"]), 10)

    def test_a_rule_in_two_collections_is_one_rule(self):
        s = fx.state(0)
        s["1"]["SV-258036"] = ["esx01"]
        row = section(card(read=read_of(s)), "Top 5 rules")["rows"][0]
        self.assertEqual((row[3], row[4]), (6, "SITEA - RHEL 9 (5), SITEA - VMware (1)"))

    def test_fixed_on_the_host_still_open(self):
        s = section(self.doc, "Fixed on the host")
        self.assertEqual(s["status"], "low")
        self.assertEqual([(r[0], r[1], r[2], r[3], r[5], r[6]) for r in s["rows"]],
                         [("rhel01.example.mil", RHEL, "SV-257985", "CAT II", "12 Nov", "Low"),
                          ("win03.corp.example.mil", WIN, "SV-254239", "CAT I", "12 Nov", "Low")])
        self.assertEqual(s["cell_status"], [["", "", "", "", "", "", "low"]] * 2)      # 1.56.0's level, with the word Low
        self.assertEqual(s["row_status"], ["low", "low"])
        self.assertNotIn("critical", json.dumps(s))                                  # paperwork: never red

    def test_no_scan_results_and_none_fixed(self):
        d = card()
        s = section(d, "Fixed on the host")
        self.assertEqual(s["title"], "Fixed on the host, still Open in STIG Manager (0)")
        self.assertIn("stigman_scorecard_drift_templates", s["lines"][0])
        self.assertEqual(tile(d, "Fixed on host, still open")["value"], "-")
        self.assertNotIn("status", tile(d, "Fixed on host, still open"))           # nothing to compare: no colour
        self.assertEqual(section(d, "Start here")["cards"][2]["status"], "info")
        d = card(opts={"fixed": {"jobs": [{"job": 1, "assets": 6}], "rows": [], "why": []}})
        self.assertEqual(section(d, "Fixed on the host")["lines"],
                         ["None: every rule the last scans (1 job, 6 assets) found passing is closed in STIG Manager too."])
        self.assertEqual(tile(d, "Fixed on host, still open")["value"], 0)
        self.assertEqual(section(d, "Start here")["cards"][2]["title"], "Nothing fixed and still open")
        self.assertEqual(section(d, "Start here")["cards"][2]["status"], "ok")      # Low only when there is one
        self.assertEqual(tile(d, "Fixed on host, still open")["status"], "ok")

    def test_a_long_list_of_fixed_rows_is_cut_with_the_rest_in_the_workbook(self):
        rows = [["rhel0%d.example.mil" % i, RHEL, "rhel0%d" % i, "SV-258036", "CAT II", "t", NOW] for i in (1, 3, 4, 5, 6)]
        d = card(opts={"fixed": {"jobs": [{"job": 1, "assets": 6}], "rows": rows, "why": []}, "list_max": 2})
        s = section(d, "Fixed on the host, still Open in STIG Manager (5)")
        self.assertEqual((len(s["rows"]), s["lines"]), (2, ["3 more are in the workbook."]))
        self.assertEqual(len(next(x for x in d["xlsx"] if x["name"] == "Fixed on host, still open")["rows"]), 5)

    def test_the_first_run_no_line_and_no_change(self):
        d = card(runs=[])
        self.assertEqual(d["title"], "STIG compliance: 4 CAT I open; 22 findings open")
        self.assertEqual((tile(d, "Closed this week")["value"], tile(d, "Closed this week")["note"]), ("-", "no run last week"))
        self.assertNotIn("status", tile(d, "Closed this week"))
        web = section(d, "By collection", "web")
        self.assertNotIn("spark", web["kinds"])
        self.assertNotIn("sparks", web)
        self.assertEqual(set(r[6] for r in web["rows"]), {"trend starts next week"})
        self.assertEqual(set(r[5] for r in web["rows"]), {"-"})
        self.assertEqual(web["columns"][6], "Open")
        self.assertEqual(len(web["lines"]), 2)
        self.assertIn("The trend starts next week", web["lines"][0])
        self.assertEqual([s["title"] for s in d["sections"]][-1], "Fixed on the host, still Open in STIG Manager (0)")   # no numbers table
        self.assertEqual(section(d, "Start here")["cards"][1]["title"], "No run last week to compare with")
        self.assertEqual((d["counts"]["weekly_points"], d["counts"]["compared"]), (1, False))
        self.assertNotIn("Open per week", [x["name"] for x in d["xlsx"]])

    def test_a_run_three_days_ago_only_is_no_weekly_point_and_no_last_week(self):
        d = card(runs=earlier()[-1:])
        self.assertEqual((d["counts"]["weekly_points"], d["counts"]["compared"]), (1, False))

    def test_two_weekly_points_are_a_line(self):
        d = card(runs=earlier()[7:8])
        web = section(d, "By collection", "web")
        self.assertEqual(web["columns"][6], "Open, 2 weeks")
        self.assertEqual([len(sp["points"].split()) for sp in web["sparks"]], [2, 2, 2])
        self.assertEqual(d["counts"]["compared"], True)

    def test_a_collection_new_since_last_week(self):
        was = fx.state(1)
        del was["1"]
        d = card(runs=[dict(fx.point(was, fx.stamp(T0 - 7 * DAY)), job=1)])
        sec = section(d, "By collection")
        row = next(i for i, r in enumerate(sec["rows"]) if r[0] == VMW)
        self.assertEqual(sec["rows"][row][5], "New here")
        self.assertEqual(sec["tips"][row][5], "SITEA - VMware was not in the run of 6 Nov.")
        self.assertEqual((d["counts"]["closed"], d["counts"]["opened"]), (4, 2))          # not in the week's totals

    def test_a_collection_not_read_is_listed_and_the_rest_whole(self):
        d = card(read=read_of(errors={"3": "HTTP 403 from https://stigman.example.mil/api/collections/3/findings: Forbidden"}))
        sec = section(d, "By collection (3)")
        self.assertEqual(sec["rows"][-1], [WIN, "-", "-", "-", "-", "-", "-", "Not read"])
        self.assertEqual(sec["row_status"][-1], "warning")
        self.assertIn("HTTP 403", sec["tips"][-1][7])
        self.assertTrue(sec["lines"][0].startswith("Not read: SITEA - Windows Server - HTTP 403"))
        self.assertEqual((d["counts"]["cat1"], d["counts"]["collections"], d["counts"]["not_read"]), (1, 2, 1))
        self.assertIn("Not read: SITEA - Windows Server - HTTP 403 from", "\n".join(d["lines"]))
        self.assertNotIn("CAT I -", "\n".join(d["lines"]))

    def test_a_collection_asked_for_that_is_not_there(self):
        read = read_of()
        read["missing"] = ["SITEA - Gone"]
        sec = section(card(read=read), "By collection")
        self.assertIn("Not there, or the client cannot read it (stigman_scorecard_collections): SITEA - Gone.", sec["lines"])
        read = dict(read_of(), collections=[], missing=["SITEA - Gone"])
        d = card(read=read)
        sec = section(d, "By collection (0)")
        self.assertEqual(sec["status"], "warning")
        self.assertIn("The client reads no collection of stigman_scorecard_collections", sec["lines"][0])
        self.assertEqual(d["title"], "STIG compliance: 0 CAT I open; 0 findings closed this week")

    def test_no_cat_i_is_green(self):
        s = fx.state(0)
        del s["2"]["SV-257777"], s["3"]["SV-254239"], s["3"]["SV-254247"]
        was = fx.state(1)
        del was["2"]["SV-257777"], was["3"]["SV-254239"], was["3"]["SV-254247"]
        d = card(runs=[dict(fx.point(was, fx.stamp(T0 - 7 * DAY)), job=1)], read=read_of(s))
        self.assertEqual((d["status"], d["title"]), ("ok", "STIG compliance: 0 CAT I open; 4 findings closed this week"))
        self.assertEqual(tile(d, "CAT I open")["status"], "ok")
        self.assertEqual(section(d, "Start here")["cards"][0]["title"], "No CAT I open")
        self.assertEqual(section(d, "Start here")["status"], "ok")

    def test_one_finding_is_said_as_one(self):
        was = fx.state(0)
        was["2"]["SV-257842"] = ["rhel03", "rhel07", "rhel01"]
        d = card(runs=[dict(fx.point(was, fx.stamp(T0 - 7 * DAY)), job=1)])
        self.assertEqual(d["title"], "STIG compliance: 4 CAT I open; 1 finding closed this week")

    def test_stig_manager_not_read(self):
        d = f.stig_scorecard({"ok": False, "error": "no token for the client autostig: cannot reach https://sso.example.mil"}, None, [], {})
        self.assertEqual((d["ok"], d["status"]), (False, "critical"))
        self.assertTrue(d["title"].startswith("STIG compliance: not read - no token for the client autostig"))
        self.assertEqual(d["tiles"], [])
        self.assertIn("Nothing was written.", d["sections"][0]["lines"][1])

    def test_the_workbook(self):
        sheets = dict((x["name"], x) for x in self.doc["xlsx"])
        self.assertEqual(list(x["name"] for x in self.doc["xlsx"]),
                         ["By collection", "Rules across hosts", "Open rules by collection", "Since last week", "Fixed on host, still open",
                          "Open per week"])
        row = next(r for r in sheets["By collection"]["rows"] if r[0] == RHEL)
        self.assertEqual(row, [RHEL, 6, 1, 8, 2, u"▼ Better: 4 closed, 1 opened", 11, "CAT I open"])
        self.assertEqual(len(sheets["Rules across hosts"]["rows"]), 10)
        self.assertEqual(len(sheets["Open rules by collection"]["rows"]), 10)
        rule = next(r for r in sheets["Open rules by collection"]["rows"] if r[1] == "SV-257985")
        self.assertEqual(rule, [RHEL, "SV-257985", "RHEL-09-255045", "CAT II", "RHEL 9 must not allow a root login over SSH.", 3,
                                "rhel01, rhel03, rhel05"])
        self.assertEqual(sorted(sheets["Since last week"]["rows"]), sorted([
            [RHEL, "SV-257985", "CAT II", "Closed", 6, 3], [RHEL, "SV-258036", "CAT II", "Closed", 6, 5],
            [RHEL, "SV-257842", "CAT III", "Opened", 1, 2], [WIN, "SV-254247", "CAT I", "Opened", 1, 2]]))
        for x in self.doc["xlsx"]:
            for r in x["rows"]:
                self.assertEqual(len(r), len(x["columns"]), x["name"])

    def test_a_long_list_of_hosts_fits_a_workbook_cell(self):
        s = fx.state(0)
        s["2"]["SV-257985"] = ["host%05d.example.mil" % i for i in range(2500)]
        d = card(read=read_of(s))
        cell = next(r for r in next(x for x in d["xlsx"] if x["name"] == "Open rules by collection")["rows"] if r[1] == "SV-257985")[6]
        self.assertLess(len(cell), 32767)
        self.assertTrue(cell.endswith(" ... (2500 in all)"))

    def test_the_lines_of_the_jobs_log(self):
        lines = self.doc["lines"]
        self.assertEqual(lines[0], self.doc["title"])
        self.assertIn("Closed this week: 4 (since 6 Nov)", lines)
        self.assertIn(u"SITEA - RHEL 9: CAT I 1, CAT II 8, CAT III 2, ▼ Better: 4 closed, 1 opened", lines)

    def test_how_it_counts_is_last_and_names_the_run_compared_with(self):
        lines = self.doc["notes"]["lines"]
        self.assertIn("Compared with the run of 2026-11-06 06:02 UTC.", lines)
        self.assertEqual(lines[-1], "9 earlier points from AAP.")
        self.assertIn("No run 6 to 8 days ago was found.", card(runs=[])["notes"]["lines"])

    def test_the_tables_are_whole(self):
        for key in ("sections", "web"):
            for s in self.doc[key]:
                for r in s.get("rows") or []:
                    self.assertEqual(len(r), len(s["columns"]), s["title"])
                for part in ("kinds", "column_titles"):
                    if part in s:
                        self.assertEqual(len(s[part]), len(s["columns"]), (s["title"], part))
                for part in ("row_status", "cell_status", "tips", "sparks"):
                    if part in s:
                        self.assertEqual(len(s[part]), len(s["rows"]), (s["title"], part))

    def test_the_words_of_the_report(self):
        for d in (self.doc, card(runs=[]), card(read=read_of(errors={"3": "HTTP 403"}))):
            for word in ("purchase", "buy", "cost"):
                self.assertIsNone(re.search(r"\b%s" % word, words(d)), word)


class Join(unittest.TestCase):
    IMPORT = {"title": "Checklist import: 2 new assets", "status": "warning", "subtitle": "3 checklists read",
              "summary": [{"label": "Checklists", "value": 3}], "footer": "the import's footer",
              "sections": [{"title": "New assets (2)", "columns": ["Asset", "STIG"], "rows": [["vrops01", "Aria"]]},
                           {"title": "What happens next", "lines": ["Approve to write."]}]}

    @classmethod
    def setUpClass(cls):
        cls.card = card()

    def test_the_scorecard_first_then_the_import_as_it_is(self):
        got = f.stig_scorecard_join(self.card, self.IMPORT, "import and scorecard", "//fs/stig/ckl", False)
        rep = got["report"]
        self.assertEqual(rep["title"], self.card["title"] + " - Checklist import: 2 new assets")
        self.assertEqual(rep["status"], "critical")
        self.assertEqual(rep["subtitle"], self.card["subtitle"] + ". 3 checklists read")
        self.assertEqual([t["label"] for t in rep["summary"]], [t["label"] for t in self.card["tiles"]] + ["Checklists"])
        n = len(self.card["sections"])
        self.assertEqual(rep["sections"][:n], self.card["sections"])
        self.assertEqual(rep["sections"][n:n + 2], self.IMPORT["sections"])              # nothing of it left out
        self.assertEqual(rep["sections"][-1]["title"], "How the scorecard counts")
        self.assertEqual(rep["footer"], "the import's footer")
        self.assertEqual([s["title"] for s in got["web"]["sections"]], [s["title"] for s in rep["sections"]])
        self.assertEqual(section(got["web"], "By collection")["kinds"][6], "spark")
        self.assertEqual([x["name"] for x in got["xlsx"]][-1], "New assets (2)")
        self.assertEqual(len(got["xlsx"]), len(self.card["xlsx"]) + 1)

    def test_the_imports_status_when_it_is_worse(self):
        green = dict(self.card, status="ok")
        self.assertEqual(f.stig_scorecard_join(green, self.IMPORT)["report"]["status"], "warning")
        self.assertEqual(f.stig_scorecard_join(green, dict(self.IMPORT, status="critical"))["report"]["status"], "critical")
        self.assertEqual(f.stig_scorecard_join(self.card, dict(self.IMPORT, status="ok"))["report"]["status"], "critical")

    def test_scorecard_alone(self):
        got = f.stig_scorecard_join(self.card, {}, "scorecard", "", False)
        rep = got["report"]
        self.assertEqual((rep["title"], rep["subtitle"]), (self.card["title"], self.card["subtitle"]))
        self.assertEqual(len(rep["sections"]), len(self.card["sections"]) + 1)
        self.assertNotIn("footer", rep)
        self.assertNotIn("checklist import", " ".join(s["title"].lower() for s in rep["sections"]))
        self.assertEqual(got["xlsx"], self.card["xlsx"])
        got = f.stig_scorecard_join(self.card, self.IMPORT, "scorecard")                   # an import's report is not used
        self.assertEqual(got["report"]["title"], self.card["title"])

    def test_an_empty_drop_folder_is_one_line(self):
        got = f.stig_scorecard_join(self.card, self.IMPORT, "import and scorecard", "//fs/stig/ckl", True)
        rep = got["report"]
        self.assertEqual(rep["title"], self.card["title"])
        line = rep["sections"][-2]
        self.assertEqual(line, {"title": "Checklist import", "status": "ok", "lines": ["No checklists to import this run (//fs/stig/ckl)"]})
        self.assertEqual(got["web"]["sections"][-2], line)
        self.assertEqual(got["xlsx"], self.card["xlsx"])
        self.assertEqual(rep["status"], "critical")                                      # the scorecard's, not the import's

    def test_the_scorecard_not_read_the_import_still_whole(self):
        bad = f.stig_scorecard({"ok": False, "error": "cannot reach https://stigman.example.mil"}, None, [], {})
        got = f.stig_scorecard_join(bad, self.IMPORT, "import and scorecard", "//fs/stig/ckl", False)
        self.assertEqual(got["report"]["status"], "critical")
        self.assertEqual(got["report"]["sections"][1:], self.IMPORT["sections"])
        self.assertEqual([s["title"] for s in got["web"]["sections"]][0], "STIG compliance scorecard: not read")
        self.assertEqual([x["name"] for x in got["xlsx"]], ["New assets (2)"])

    def test_nothing_at_all(self):
        got = f.stig_scorecard_join(None, None, "scorecard")
        self.assertEqual((got["report"]["title"], got["report"]["status"], got["web"], got["xlsx"]), ("STIG compliance", "info", {}, []))


class FakeApi(object):
    """AAP's jobs as site_aap.Api gives them: (HTTP code, JSON), every call remembered."""
    user = "auditor"

    def __init__(self, jobs, forbid=False, hide=False, cut=(), why="", no_names=False):
        self.jobs, self.forbid, self.hide, self.cut, self.why, self.calls = jobs, forbid, hide, set(cut), why, []
        self.no_names = no_names

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
        if self.forbid or (self.no_names and "job_template__name" in q):
            return 403, {"detail": "You do not have permission to perform this action."}
        if "job_template__name" in q:
            name = q["job_template__name"].replace("%20", " ")
            rows = [j for j in self.jobs if j["name"] == name]
        else:
            rows = [j for j in self.jobs if j["job_template"] == int(q["job_template"]) and j["id"] < int(q["id__lt"])]
        rows = [] if self.hide else sorted([j for j in rows if j["status"] in q["status__in"].split(",")], key=lambda j: -j["id"])
        size, page = int(q["page_size"]), int(q.get("page", 1))
        part = rows[(page - 1) * size:page * size]
        if page > 1 and not part:
            return 404, {"detail": "Invalid page."}
        return 200, {"count": len(rows), "next": "x" if page * size < len(rows) else None,
                     "results": [dict((k, v) for k, v in j.items() if k != "artifacts") for j in part]}


class History(unittest.TestCase):
    def read(self, api, days=64, max_runs=200, skip=()):
        return hist.history(api, 5000, fx.TEMPLATE, days, max_runs, set(skip), now=T0)

    def test_the_templates_earlier_jobs_with_a_point_oldest_first(self):
        api = FakeApi(fx.jobs(NOW))
        runs, counts, why = self.read(api)
        self.assertEqual(why, "")
        self.assertEqual([r["job"] for r in runs], list(range(4001, 4010)))                # not the failed one, not another template's
        self.assertEqual(counts, {"listed": 10, "read": 9, "skipped": 0, "no_point": 1})
        self.assertEqual(set(m for m, p in api.calls), {"GET"})
        got = f.stig_scorecard_runs(fx.point(fx.state(0), NOW), runs, [], {"weeks": 8, "job": 5000})
        self.assertEqual(got["counts"]["used"], 10)

    def test_more_jobs_than_a_page(self):
        jobs = fx.jobs(NOW)[:1]
        many = [dict(jobs[0], id=1000 + i, finished=fx.stamp(T0 - 3600 * (130 - i))) for i in range(120)]
        runs, counts, why = self.read(FakeApi(many))
        self.assertEqual((len(runs), counts["listed"], why), (120, 120, ""))
        self.assertEqual((runs[0]["job"], runs[-1]["job"]), (1000, 1119))

    def test_the_jobs_the_database_gave_are_not_read_again(self):
        api = FakeApi(fx.jobs(NOW))
        runs, counts, why = self.read(api, skip=[4001, 4002, 4003])
        self.assertEqual(([r["job"] for r in runs], counts["skipped"]), (list(range(4004, 4010)), 3))
        self.assertNotIn(("GET", "jobs/4002/"), api.calls)

    def test_only_the_window(self):
        runs, counts, why = self.read(FakeApi(fx.jobs(NOW)), days=22)
        self.assertEqual([r["job"] for r in runs], [4006, 4007, 4008, 4009])

    def test_at_most_max_runs_the_newest(self):
        runs, counts, why = self.read(FakeApi(fx.jobs(NOW)), max_runs=3)
        self.assertEqual(([r["job"] for r in runs], counts["listed"]), ([4008, 4009], 3))

    def test_an_answer_cut_off_is_asked_for_once_more(self):
        api = FakeApi(fx.jobs(NOW), cut=[4005])
        runs, counts, why = self.read(api)
        self.assertEqual(len(runs), 9)
        self.assertEqual(api.calls.count(("GET", "jobs/4005/")), 2)

    def test_the_list_refused_says_the_role_to_give(self):
        runs, counts, why = self.read(FakeApi(fx.jobs(NOW), forbid=True))
        self.assertEqual(runs, [])
        self.assertIn("HTTP 403", why)
        self.assertIn("Organization Auditor", why)

    def test_a_user_who_sees_no_job_is_not_a_first_run(self):
        runs, counts, why = self.read(FakeApi(fx.jobs(NOW) + [{"id": 5000, "job_template": fx.TEMPLATE, "status": "running"}], hide=True))
        self.assertEqual(runs, [])
        self.assertIn("The AAP user auditor cannot see this template's jobs", why)
        self.assertIn("job template %d" % fx.TEMPLATE, why)

    def test_a_first_run_is_no_error(self):
        runs, counts, why = self.read(FakeApi([{"id": 5000, "job_template": fx.TEMPLATE, "status": "running", "name": "x"}]))
        self.assertEqual((runs, why, counts["listed"]), ([], "", 0))

    def test_no_credential(self):
        runs, counts, why = self.read(FakeApi([], why=hist.NO_CREDENTIAL))
        self.assertEqual((runs, why), ([], hist.NO_CREDENTIAL))

    def test_seconds(self):
        self.assertEqual(hist.seconds("2026-11-13T06:00:05.512345Z"), T0)
        self.assertIsNone(hist.seconds(None))


class DriftJobs(unittest.TestCase):
    NAMES = ["STIG drift report", "Windows STIG drift report"]

    def scan(self, jid, name, days_ago, assets, rows):
        at = fx.stamp(T0 - days_ago * DAY)
        return {"id": jid, "job_template": 141, "name": name, "status": "successful", "finished": at,
                "artifacts": {"stig_fixed_open": {"v": 1, "read_at": at, "assets": [[RHEL, a] for a in assets],
                                                  "rows": [[a + ".example.mil", RHEL, a, sv, "CAT II", "t", at] for a, sv in rows]}}}

    def test_the_newest_jobs_of_each_template(self):
        api = FakeApi(fx.jobs(NOW))
        got = hist.fixed(api, self.NAMES, 14, 20, now=T0)
        self.assertEqual(got["why"], [])
        self.assertEqual([(j["job"], j["template"], j["assets"]) for j in got["jobs"]],
                         [(4013, "Windows STIG drift report", 1), (4012, "STIG drift report", 2)])
        self.assertEqual(sorted(r[2] for r in got["rows"]), ["rhel01", "rhel03", "win03"])
        self.assertIn(("GET", "jobs/?job_template__name=Windows%20STIG%20drift%20report&status__in=successful,failed&order_by=-id&page_size=20"),
                      api.calls)
        self.assertEqual(set(m for m, p in api.calls), {"GET"})

    def test_an_asset_counts_once_the_newest_scan_wins(self):
        jobs = [self.scan(10, self.NAMES[0], 5, ["rhel01", "rhel03"], [("rhel01", "SV-1"), ("rhel03", "SV-2")]),
                self.scan(11, self.NAMES[0], 1, ["rhel01", "rhel04"], [("rhel04", "SV-3")])]          # rhel01 scanned again: none now
        got = hist.fixed(FakeApi(jobs), self.NAMES[:1], 14, 20, now=T0)
        self.assertEqual(sorted((r[2], r[3]) for r in got["rows"]), [("rhel03", "SV-2"), ("rhel04", "SV-3")])
        self.assertEqual([j["job"] for j in got["jobs"]], [11, 10])

    def test_only_the_last_days(self):
        jobs = [self.scan(10, self.NAMES[0], 20, ["rhel01"], [("rhel01", "SV-1")])]
        got = hist.fixed(FakeApi(jobs), self.NAMES[:1], 14, 20, now=T0)
        self.assertEqual((got["rows"], got["jobs"]), ([], []))
        self.assertIn("no job of the last 14 days has what it found", got["why"][0])

    def test_a_template_without_jobs_and_one_refused_are_said(self):
        got = hist.fixed(FakeApi([]), ["STIG drift report"], 14, 20, now=T0)
        self.assertEqual(got["why"], ["STIG drift report: no finished job of a template with this name"])
        got = hist.fixed(FakeApi(fx.jobs(NOW), no_names=True), self.NAMES, 14, 20, now=T0)
        self.assertEqual(len(got["why"]), 2)
        self.assertIn("HTTP 403", got["why"][0])

    def test_a_job_from_before_1_58_0_has_nothing_kept(self):
        old = dict(self.scan(10, self.NAMES[0], 1, [], []), artifacts={"stig_findings": 3})
        got = hist.fixed(FakeApi([old]), self.NAMES[:1], 14, 20, now=T0)
        self.assertIn("kept since 1.58.0", got["why"][0])


class FakeCursor(object):
    """The table's rows; the statements the module sends, remembered."""
    def __init__(self, rows, tables=("stig_scorecard_point", "schema_version"), version=None):
        self.rows, self.tables, self.version, self.sql, self.out = rows, list(tables), version, [], []

    def execute(self, sql, args=None):
        self.sql.append((sql, args))
        if sql.startswith("SELECT table_name"):
            name = args[0] if args else "schema_version"
            self.out = [(name,)] if name in self.tables else []
        elif sql.startswith("CREATE TABLE IF NOT EXISTS"):
            self.tables.append(sql.split()[5])
        elif sql.startswith("SELECT version"):
            self.out = [(self.version,)] if self.version is not None else []
        elif sql.startswith("INSERT INTO schema_version"):
            self.version = args[1]
        elif sql.startswith("SELECT read_at, job"):
            self.out = sorted([r for r in self.rows if args[0] <= r[0] < args[1]], key=lambda r: (r[0], r[3], r[2]))
        elif sql.startswith("SELECT COUNT"):
            ats = sorted(set(r[0] for r in self.rows))
            self.out = [(len(self.rows), len(ats), ats[0] if ats else None, ats[-1] if ats else None)]
        return len(self.out)

    def fetchall(self):
        return self.out

    def fetchone(self):
        return self.out[0] if self.out else None


class FakeConn(object):
    def __init__(self, rows=(), **kw):
        self.cur, self.commits, self.closed = FakeCursor(list(rows), **kw), 0, False

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class Database(unittest.TestCase):
    def rows(self, weeks):
        out = []
        for k in weeks:
            at = fx.stamp(T0 - k * 7 * DAY)
            out += cdb.rows_of(fx.point(fx.state(k), at), cdb.stamp(at), 4000 + k)
        return out

    def test_what_is_read_back_is_what_was_written(self):
        """A point -> its rows -> the runs read back: the artifact's layout, with the job."""
        runs = cdb.read(FakeConn(self.rows([3, 2, 1])), cdb.stamp(NOW), 64)
        self.assertEqual([r["job"] for r in runs], [4003, 4002, 4001])
        want = fx.point(fx.state(2), fx.stamp(T0 - 14 * DAY))
        self.assertEqual(dict((k, v) for k, v in runs[1].items() if k != "job"),
                         dict(want, collections=sorted(want["collections"], key=lambda c: c["name"])))
        # and the report takes them as it takes AAP's
        got = f.stig_scorecard_runs(fx.point(fx.state(0), NOW), [], runs, {"weeks": 8, "job": 5000})
        self.assertEqual(got["counts"], {"aap": 0, "db": 3, "both": 0, "used": 4})
        d = f.stig_scorecard(read_of(), f.stig_scorecard_point(read_of()), got["runs"], {"weeks": 8})
        self.assertEqual(d["title"], "STIG compliance: 4 CAT I open; 4 findings closed this week")

    def test_not_this_runs_own_rows_and_not_older_than_the_window(self):
        runs = cdb.read(FakeConn(self.rows([10, 2, 0])), cdb.stamp(NOW), 64)
        self.assertEqual([r["job"] for r in runs], [4002])

    def test_rules_that_are_not_json_are_no_rules(self):
        at = cdb.stamp(fx.stamp(T0 - DAY))
        runs = cdb.read(FakeConn([(at, 7, "2", RHEL, 6, 1, 8, 2, "{broken"), (at, 7, "3", WIN, 4, 3, 4, 1, "[1]")]), cdb.stamp(NOW), 64)
        self.assertEqual([(c["name"], c["cat2"], c["r2"]) for c in runs[0]["collections"]], [(RHEL, 8, {}), (WIN, 4, {})])

    def test_the_table_is_made_once_with_its_version(self):
        conn = FakeConn(tables=())
        self.assertEqual(cdb.schema(conn, True), (True, True))
        sent = [s for s, a in conn.cur.sql]
        self.assertIn(cdb.CREATE, sent)
        self.assertIn(site_report_db.SCHEMA_VERSION_TABLE, sent)
        self.assertEqual((conn.cur.version, conn.commits), (cdb.SCHEMA_VERSION, 1))
        self.assertEqual(cdb.schema(conn, True), (True, False))                           # the next run makes nothing
        self.assertEqual(len([s for s in [x for x, a in conn.cur.sql] if s.startswith("CREATE")]), 2)

    def test_not_made_when_told_not_to(self):
        conn = FakeConn(tables=())
        self.assertEqual(cdb.schema(conn, False), (False, False))
        self.assertEqual([s for s, a in conn.cur.sql if not s.startswith("SELECT")], [])

    def test_a_newer_table_is_refused(self):
        with self.assertRaises(RuntimeError) as e:
            cdb.schema(FakeConn(version=cdb.SCHEMA_VERSION + 1), True)
        self.assertIn("update site-automation", str(e.exception))

    def test_what_the_table_holds(self):
        self.assertEqual(cdb.held(FakeConn(self.rows([2, 1]))),
                         {"rows": 6, "runs": 2, "oldest": fx.stamp(T0 - 14 * DAY), "newest": fx.stamp(T0 - 7 * DAY)})
        self.assertEqual(cdb.held(FakeConn()), {"rows": 0, "runs": 0, "oldest": "", "newest": ""})

    def test_stamp(self):
        self.assertEqual(cdb.stamp("2026-11-13T06:00:05Z").isoformat(), "2026-11-13T06:00:05")
        self.assertIsNone(cdb.stamp("soon"))

    def test_the_statement_in_the_doc_is_the_modules(self):
        doc = open(os.path.join(REPO, "docs", "REPORT_DATABASE.md")).read()
        for part in cdb.ddl().split("\n\n"):
            self.assertIn(part, doc, "docs/REPORT_DATABASE.md: the statement differs from site_stig_scorecard_db.ddl()")
        self.assertIn("PRIMARY KEY (collection, read_at)", cdb.ddl())

    def test_the_settings_are_in_the_defaults_and_the_docs(self):
        defaults = open(os.path.join(REPO, "roles", "stigman_ckl_import", "defaults", "main.yml")).read()
        ref = open(os.path.join(REPO, "docs", "VARIABLES_REFERENCE.md")).read()
        guide = open(os.path.join(REPO, "docs", "CKL_IMPORT.md")).read()
        for name in ("stigman_ckl_import_mode", "stigman_scorecard_collections", "stigman_scorecard_top", "stigman_scorecard_weeks",
                     "stigman_scorecard_cat2_warn", "stigman_scorecard_db", "stigman_scorecard_db_create", "stigman_scorecard_db_keep_days",
                     "stigman_scorecard_drift_templates", "stigman_scorecard_drift_days"):
            self.assertIn("\n%s:" % name, defaults, name)
            self.assertIn("`%s`" % name, ref, name)
            self.assertIn("`%s`" % name, guide, name)
        self.assertIn("\nstigman_ckl_import_mode: import\n", defaults)
        self.assertRegex(defaults, r"\nstigman_scorecard_top: 5\s")
        self.assertRegex(defaults, r"\nstigman_scorecard_weeks: 8\s")
        self.assertRegex(defaults, r"\nstigman_scorecard_collections: \[\]\s")


if __name__ == "__main__":
    unittest.main()
