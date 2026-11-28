#!/usr/bin/env python3
"""Unit tests for the CORA risk score in the STIG scorecard (1.70.0): the math, worked by hand, as STIG Manager's own Collection
dashboard works it (its CORA panel: weights CAT I 10, CAT II 4, CAT III 1; Very High from 20 %, High from 10 %, Moderate above
0, Very Low at 0, Low when CAT I is 0 and CAT II and III are under 5 %); what is read of STIG Manager's metrics (counts by
severity, no more calls); the point and the report database's JSON; the tile, the website's columns, the week's change, the
email and the workbook; the Findings' weight. Standard library plus ansible-core: python3 tests/test_stig_cora.py"""
import copy
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_stig_scorecard as t  # noqa: E402
import stig_scorecard_filters as f  # noqa: E402
import site_stig_scorecard_db as cdb  # noqa: E402

fx, sc = t.fx, t.sc


class Math(unittest.TestCase):
    """Each case worked by hand: a = the rules of CAT I, II, III; d = assessed; o = Open."""

    def test_everything_assessed_and_none_open_is_very_low(self):
        self.assertEqual(f.cora([10, 40, 20], [10, 40, 20], [0, 0, 0]), {"pct": 0.0, "rating": "Very Low", "cats": [0.0, 0.0, 0.0]})

    def test_a_collection_without_rules_is_very_low(self):
        self.assertEqual(f.cora([0, 0, 0], [0, 0, 0], [0, 0, 0])["rating"], "Very Low")

    def test_unassessed_counts_as_open(self):
        # CAT II: 40 rules, 36 assessed, 0 Open -> 4 unassessed = 10 %: (4 x 10) / (10 + 4 + 1) = 2.67 -> 2.7 %, Moderate
        x = f.cora([10, 40, 20], [10, 36, 20], [0, 0, 0])
        self.assertEqual((x["pct"], x["rating"], x["cats"]), (2.7, "Moderate", [0.0, 10.0, 0.0]))

    def test_open_and_unassessed_add(self):
        # CAT I: 10 rules, 9 assessed, 1 Open -> (1 + 1) / 10 = 20 %; CAT II 0; CAT III 0 -> (10 x .2) / 15 = 13.3 %: High
        x = f.cora([10, 40, 20], [9, 40, 20], [1, 0, 0])
        self.assertEqual((x["pct"], x["rating"], x["cats"][0]), (13.3, "High", 20.0))

    def test_each_band(self):
        a = [10, 10, 10]
        self.assertEqual(f.cora(a, a, [1, 0, 0])["rating"], "Moderate")             # 10 x .1 / 15 = 6.7 %
        self.assertEqual(f.cora(a, a, [2, 0, 0])["rating"], "High")                 # 10 x .2 / 15 = 13.3 %
        self.assertEqual(f.cora(a, a, [5, 5, 5])["rating"], "Very High")            # .5 in each: 50 %
        self.assertEqual(f.cora(a, a, [5, 5, 5])["pct"], 50.0)
        self.assertEqual(f.cora(a, a, [0, 0, 1])["rating"], "Moderate")             # CAT III 10 %: not under 5 %, so no Low (0.7 %)

    def test_the_low_special_case(self):
        # CAT I 0; CAT II 3 of 100 (3 %); CAT III 10 of 1000 (1 %): both under 5 % -> Low, not Moderate; (4 x .03 + .01) / 15 = 0.9 %
        x = f.cora([10, 100, 1000], [10, 98, 990], [0, 1, 0])
        self.assertEqual((x["pct"], x["rating"]), (0.9, "Low"))
        # CAT II at exactly 5 %: not under 5 -> Moderate
        self.assertEqual(f.cora([10, 100, 1000], [10, 95, 1000], [0, 0, 0])["rating"], "Moderate")
        # CAT I with one unassessed rule: no Low, however small the rest
        self.assertEqual(f.cora([100, 100, 1000], [99, 100, 1000], [0, 0, 0])["rating"], "Moderate")

    def test_the_bands_edges(self):
        a = [100, 100, 100]
        # CAT II 50 % alone: 4 x .5 / 15 = 13.3 -> High; CAT II 100 % alone: 26.7 -> Very High
        self.assertEqual(f.cora(a, a, [0, 50, 0])["rating"], "High")
        self.assertEqual(f.cora(a, a, [0, 100, 0])["rating"], "Very High")
        # CAT II 20 % alone: 0.8 / 15 = 5.3 -> Moderate (CAT II is 20 %, so no Low)
        self.assertEqual(f.cora(a, a, [0, 20, 0])["rating"], "Moderate")

    def test_the_edges_are_exact(self):
        """The bands' edges worked in whole numbers - no float error (STIG Manager's own panel works in floats, which can fall a
        hair under an edge)."""
        a = [100, 100, 100]
        # exactly 20 %: (10 x 18 + 4 x 29 + 1 x 4) / 15 = 300 / 15 = 20 -> Very High (18 + 29 + 4 would be 19.999... in floats)
        x = f.cora(a, a, [18, 29, 4])
        self.assertEqual((x["pct"], x["rating"]), (20.0, "Very High"))
        self.assertEqual(f.cora(a, a, [18, 29, 3])["rating"], "High")                # just under
        # exactly 10 %: (10 x 10 + 4 x 10 + 10) / 15 = 150 / 15 -> High; one fewer in CAT III is Moderate
        self.assertEqual(f.cora(a, a, [10, 10, 10])["rating"], "High")
        self.assertEqual(f.cora(a, a, [10, 10, 9])["rating"], "Moderate")
        self.assertEqual(f.cora(a, a, [10, 10, 9])["pct"], 9.9)
        # just above 0: one rule in 1000 -> Moderate (CAT I), never Very Low; and Low when it is CAT III alone, under 5 %
        big = [1000, 1000, 1000]
        self.assertEqual(f.cora(big, big, [1, 0, 0])["rating"], "Moderate")
        self.assertEqual(f.cora(big, big, [0, 0, 1])["rating"], "Low")
        self.assertEqual(f.cora(big, big, [0, 0, 0])["rating"], "Very Low")
        # the Low edges: CAT II and CAT III at exactly 5 % are not under 5 % - Moderate; one rule fewer is Low
        h = [100, 100, 100]
        self.assertEqual(f.cora(h, h, [0, 5, 0])["rating"], "Moderate")
        self.assertEqual(f.cora(h, h, [0, 4, 0])["rating"], "Low")
        self.assertEqual(f.cora(h, h, [0, 0, 5])["rating"], "Moderate")
        self.assertEqual(f.cora(h, h, [0, 0, 4])["rating"], "Low")
        self.assertEqual(f.cora(h, h, [0, 4, 4])["rating"], "Low")
        self.assertEqual(f.cora(h, h, [1, 0, 0])["rating"], "Moderate")              # CAT I at all: never Low

    def test_a_cat_without_rules_is_left_out_of_the_weights(self):
        # As STIG Manager's own panel (its stigmanUtils.js): CAT I has no rules, so the weight is 4 + 1 = 5, not 15.
        # CAT II 10 of 100 Open -> (4 x .1) / 5 = 8 %: Moderate (over 15 it would be 2.7 %)
        x = f.cora([0, 100, 100], [0, 100, 100], [0, 10, 0])
        self.assertEqual((x["pct"], x["rating"], x["cats"]), (8.0, "Moderate", [0.0, 10.0, 0.0]))

    def test_no_counts_no_score(self):
        self.assertIsNone(f.cora(None, [1, 1, 1], [0, 0, 0]))
        self.assertIsNone(f._cora_of({"cat1": 1, "cat2": 1, "cat3": 1}))

    def test_the_sum_leaves_out_what_has_no_counts(self):
        a = {"ra": [10, 40, 20], "rd": [10, 40, 20], "cat1": 0, "cat2": 0, "cat3": 0}
        b = {"ra": [10, 40, 20], "rd": [9, 40, 20], "cat1": 1, "cat2": 0, "cat3": 0}
        x, n = f._cora_sum([a, b, {"cat1": 3}])
        self.assertEqual(n, 2)
        self.assertEqual(x, f.cora([20, 80, 40], [19, 80, 40], [1, 0, 0]))          # CAT I: 2 of 20 = 10 %: 10 x .1 / 15 = 6.7 % Moderate
        self.assertEqual((x["pct"], x["rating"]), (6.7, "Moderate"))

    def test_the_weights(self):
        self.assertEqual(f.CORA_WEIGHT, (10, 4, 1))


def read_cora(**kw):
    """The made-up site read through site_stig_scorecard.read, STIG Manager giving the counts by severity."""
    cols, missing = sc.read(t.HostLists.P, t.SlowClient(cora=True, **kw))
    return {"ok": True, "error": "", "read_at": t.NOW, "collections": cols, "missing": missing}


class Read(unittest.TestCase):
    def test_the_counts_come_with_the_metrics_no_more_calls(self):
        cl = t.SlowClient(cora=True)
        cols, missing = sc.read(t.HostLists.P, cl)
        rhel = next(c for c in cols if c["name"] == t.RHEL)
        # 6 assets, 7 checklists (rhel01 has two): 10 CAT I, 40 CAT II, 20 CAT III rules each
        self.assertEqual((rhel["cora_a"], rhel["cora_d"]), ([70, 280, 140], [63, 252, 140]))
        self.assertEqual([x["cora_a"] for x in rhel["checklists"]][:1], [[10, 40, 20]])
        plain = t.SlowClient()
        sc.read(t.HostLists.P, plain)
        self.assertEqual(len(cl.calls), len(plain.calls))

    def test_an_older_stig_manager_gives_none(self):
        cols, _ = sc.read(t.HostLists.P, t.SlowClient())
        self.assertTrue(all("cora_a" not in c for c in cols))
        cols, _ = sc.read(t.HostLists.P, t.SlowClient(metrics=False))
        self.assertTrue(all("cora_a" not in c for c in cols))

    def test_a_metrics_row_with_a_part_missing_has_none(self):
        self.assertIsNone(sc.sev_of({"assessmentsBySeverity": {"high": 1, "medium": 2}}, "assessmentsBySeverity"))
        self.assertIsNone(sc.sev_of({}, "assessedBySeverity"))
        self.assertEqual(sc.sev_of({"assessedBySeverity": {"high": 1, "medium": 2, "low": 3}}, "assessedBySeverity"), [1, 2, 3])


class Point(unittest.TestCase):
    def test_the_point_keeps_the_counts_and_the_database_too(self):
        read = read_cora()
        p = f.stig_scorecard_point(read)
        c = next(x for x in p["collections"] if x["name"] == t.RHEL)
        self.assertEqual((c["ra"], c["rd"]), ([70, 280, 140], [63, 252, 140]))
        self.assertEqual(c["p"]["Linux"]["ra"], [60, 240, 120])           # RHEL_9 only (rhel01's Apache is Applications)
        self.assertEqual(c["p"]["Applications"]["ra"], [10, 40, 20])
        rows = cdb.rows_of(p, "2026-11-13 06:00:05", 5000)
        import json
        js = json.loads(next(r for r in rows if r[3] == t.RHEL)[8])
        self.assertEqual((js["ra"], js["rd"]), ([70, 280, 140], [63, 252, 140]))

    def test_the_platform_counts_survive_the_database(self):
        """1.70.0: the report database keeps each platform's CORA counts too (the rules column, no schema change)."""
        import json
        p = f.stig_scorecard_point(read_cora())
        rows = cdb.rows_p_of(p, "2026-11-13 06:00:05", 5000)
        c = next(x for x in p["collections"] if x["name"] == t.RHEL)
        got = dict((r[3], cdb.platform_of(r[4], r[5], r[6], r[7], r[8])) for r in rows if r[2] == c["id"])
        self.assertEqual(got, c["p"])
        self.assertEqual(got["Linux"]["ra"], [60, 240, 120])
        # a row written before 1.70.0 (a list) reads as it did, without the counts
        self.assertEqual(cdb.platform_of(6, 1, 8, 2, '["SV-1","SV-2"]'), {"assets": 6, "cat1": 1, "cat2": 8, "cat3": 2, "rules": ["SV-1", "SV-2"]})
        self.assertEqual(json.loads(next(r[8] for r in cdb.rows_p_of(f.stig_scorecard_point(t.read_of()), "x", 1))).__class__, list)

    def test_no_counts_no_keys(self):
        p = f.stig_scorecard_point(t.read_of())
        self.assertTrue(all("ra" not in c and "rd" not in c for c in p["collections"]))


class Page(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.read = read_cora()
        cls.doc = cls.card()

    @classmethod
    def card(cls, runs=None, **kw):
        point = f.stig_scorecard_point(cls.read)
        # 1.71.0: 12 weeks, as stigman_scorecard_weeks (its website document is the dashboard's fixture)
        got = f.stig_scorecard_runs(point, t.earlier() if runs is None else runs, [], {"weeks": 12, "job": 5000})
        return f.stig_scorecard(kw.get("read", cls.read), point, got["runs"], dict({"weeks": 12, "top": 5}, **kw.get("opts", {})))

    def test_the_tile(self):
        x = t.tile(self.doc, "CORA risk")
        # 13 assets, 14 checklists (rhel01: two): CAT I 140 rules, 4 Open + 14 unassessed -> 18 / 140 = 12.86 %;
        # CAT II 560: 56 unassessed + 15 Open = 71 / 560 = 12.68 %; CAT III 280: 3 Open -> 1.07 %
        # weighted: (10 x .12857 + 4 x .12679 + 1 x .01071) / 15 = 12.0 %: High
        self.assertEqual((x["value"], x["status"]), ("High 12.0%", "critical"))
        self.assertEqual(self.doc["tiles"][0]["label"], "CORA risk")
        self.assertEqual(self.doc["counts"]["cora"], {"pct": 12.0, "rating": "High"})
        self.assertNotIn("note", x)                                                    # no run with the counts last week: no change shown

    def test_report_fit_document(self):
        """The website's document - a report fit document (tests/report_fit_docs.py: stig_scorecard_cora_web)."""
        if os.environ.get("REPORT_FIT_FROM_TESTS"):
            self.skipTest("a named report fit document")
        web = f.stig_scorecard_join(self.doc, None, "scorecard")["web"]
        self.assertEqual(web["summary"][0]["label"], "CORA risk")
        self.assertIn("CORA %", t.section(web, "By collection")["columns"])

    def test_atlas_tiles_by_their_count(self):
        from test_server_view_reports import _render
        web = f.stig_scorecard_join(self.doc, None, "scorecard")["web"]
        self.assertEqual(len(web["summary"]), 6)
        for look in ("atlas", "ops"):
            with self.subTest(look=look):
                template = "report_%s.html.j2" % look
                html = _render(template, web)
                self.assertRegex(html, r'<div class="dtiles n5" data-p="\d+">')
                self.assertIn('aria-label="CORA 12.0 %"', html)
                self.assertIn('>This week</div>', html)
        legacy = _render("report_atlas.html.j2", web, report_servers_view=False)
        self.assertIn('<div class="tiles t6">', legacy)
        self.assertIn("@media(min-width:721px){.tiles.t6{grid-template-columns:repeat(3,minmax(0,1fr))}}", legacy)
        without_dashboard = {k: v for k, v in web.items() if k != "dashboard"}
        self.assertIn('<div class="tiles t6">', _render("report_ops.html.j2", without_dashboard))

    def test_the_label_is_short(self):
        self.assertNotIn("method", t.tile(self.doc, "CORA risk")["label"])

    def test_the_websites_columns(self):
        for title, name in (("By collection", t.RHEL), ("By platform", "Linux")):
            s = t.section(self.doc, title, "web")
            cols = s["columns"]
            at = cols.index("CORA %")
            self.assertEqual(cols[at:at + 3], ["CORA %", "Risk", "Since last week"])
            self.assertEqual(len(s["kinds"]), len(cols))
            self.assertEqual(len(s["column_titles"]), len(cols))
            for r, cs, tp in zip(s["rows"], s["cell_status"], s["tips"]):
                self.assertEqual((len(r), len(cs), len(tp)), (len(cols),) * 3)
            row = next(r for r in s["rows"] if name in r)
            self.assertRegex(row[at], r"^\d+\.\d$")
        s = t.section(self.doc, "By collection", "web")
        at = s["columns"].index("CORA %")
        by = dict((r[1] if s["columns"][0] == "" else r[0], r) for r in s["rows"])
        # the RHEL collection: CAT I 70 rules: 1 Open + 7 unassessed -> 8 / 70; CAT II 280: 8 Open + 28 -> 36 / 280; CAT III 140: 2 -> 2 / 140
        # (10 x 8/70 + 4 x 36/280 + 2/140) / 15 = (1.142857 + 0.514286 + 0.014286) / 15 = 11.14 %: High
        row = by[t.RHEL]
        self.assertEqual((row[at], row[at + 1]), ("11.1", "High"))
        i = s["rows"].index(row)
        self.assertEqual(s["cell_status"][i][at + 1], "critical")
        self.assertIn("CAT I 11.4 %, CAT II 12.9 %, CAT III 1.4 %", s["tips"][i][at + 1])
        self.assertIn("High", s["tips"][i][at + 1])

    def test_the_risk_word_is_always_shown(self):
        s = t.section(self.doc, "By collection", "web")
        at = s["columns"].index("Risk")
        self.assertTrue(all(r[at] in ("Very High", "High", "Moderate", "Low", "Very Low", "-") for r in s["rows"]))
        self.assertTrue(all(c in ("", "critical", "warning", "ok") for cs in s["cell_status"] for c in cs))

    def test_the_email_has_the_tile_and_its_tables_stay_as_they_were(self):
        for title in ("By collection", "By platform"):
            self.assertNotIn("CORA %", t.section(self.doc, title)["columns"])
        self.assertEqual(self.doc["tiles"][0]["label"], "CORA risk")

    def test_the_workbook(self):
        x = dict((s["name"], s) for s in self.doc["xlsx"])
        for name in ("By collection", "By platform"):
            s = x[name]
            self.assertEqual(s["columns"][-4:], ["Open now", "CORA %", "Risk", "Status"])
            last = s["rows"][-1]
            self.assertEqual(len(last), 1)
            self.assertTrue(last[0].startswith("How CORA is worked out (STIG Manager's CORA method):"))
            self.assertEqual(len(s["sparklines"][0]["points"]), len(s["rows"]))
            for r in s["rows"][:-1]:
                self.assertEqual(len(r), len(s["columns"]))
                self.assertIsInstance(r[-3], float)
        self.assertEqual(x["Rules across hosts"]["columns"][4:6], ["Hosts open", "Weight"])

    def test_how_it_is_counted_names_the_method_once(self):
        lines = self.doc["notes"]["lines"]
        at = next(i for i, x in enumerate(lines) if x.startswith("CORA (STIG Manager's method):"))
        mine = lines[at:at + 2]
        self.assertTrue(mine[1].startswith("Very High from 20 %"))
        self.assertEqual(len([x for x in lines if "STIG Manager's method" in x]), 1)
        self.assertTrue(all(len(x.split(". ")) <= 2 for x in mine))

    def test_the_job_output_has_it(self):
        self.assertIn("CORA risk: High 12.0%", self.doc["lines"])

    def test_the_week_s_change(self):
        # last week's run had the counts: CAT I 140 rules, 21 open or unassessed (15 %), the rest as now -> a worse week before
        runs = t.earlier()
        for run in runs:
            for c in run["collections"]:
                now = next(x for x in f.stig_scorecard_point(self.read)["collections"] if x["id"] == c["id"])
                c["ra"], c["rd"] = list(now["ra"]), list(now["rd"])
                c["rd"][0] -= 2                                                     # two more CAT I unassessed than now
        d = self.card(runs=runs)
        x = t.tile(d, "CORA risk")
        self.assertRegex(x["note"], r"^▼ \d+\.\d points since ")
        self.assertIn("(▼", [s for s in t.section(d, "By collection", "web")["tips"][0] if "CORA" in s][0])

    def test_a_week_without_the_counts_shows_no_change(self):
        d = self.card()                                  # the earlier points are of 1.69.x: no counts
        self.assertNotIn("note", t.tile(d, "CORA risk"))

    def test_the_same_week_says_so(self):
        runs = t.earlier()
        point = f.stig_scorecard_point(self.read)
        for run in runs:
            for c in run["collections"]:
                now = next(x for x in point["collections"] if x["id"] == c["id"])
                c["ra"], c["rd"] = list(now["ra"]), list(now["rd"])
                for k in ("cat1", "cat2", "cat3"):
                    c[k] = now[k]
        self.assertRegex(t.tile(self.card(runs=runs), "CORA risk")["note"], r"^same as ")

    def test_without_the_counts_no_tile_no_columns(self):
        d = t.card()
        self.assertNotIn("CORA risk", [x["label"] for x in d["tiles"]])
        for s in d["web"]:
            self.assertNotIn("CORA %", s.get("columns") or [])
        self.assertTrue(any(x.startswith("CORA risk: not shown") for x in d["lines"]))
        self.assertNotIn("cora", d["counts"])
        self.assertFalse([1 for x in d["xlsx"] if "CORA %" in x["columns"]])
        self.assertFalse([x for x in d["notes"]["lines"] if "CORA" in x])

    def test_a_platform_filter_scores_those_platforms_only(self):
        d = self.card(opts={"platform_filter": "Linux"})
        x = t.tile(d, "CORA risk")
        # Linux: RHEL_9 checklists only (6 assets): CAT I 60 rules, 6 unassessed + 1 Open = 7/60; CAT II 240: 24 + 8 = 32/240; CAT III 120: 2/120
        self.assertEqual(x["value"], "%s %.1f%%" % ("High", f.cora([60, 240, 120], [54, 216, 120], [1, 8, 2])["pct"]))
        self.assertEqual(f.cora([60, 240, 120], [54, 216, 120], [1, 8, 2])["pct"], 11.4)       # (10 x 7/60 + 4 x 32/240 + 2/120) / 15

    def test_a_platform_filter_shows_the_weeks_change_from_the_database(self):
        """The earlier points as the database gives them back (platform counts in the rules column): the filtered tile has its change."""
        runs = t.earlier()
        point = f.stig_scorecard_point(self.read)
        for run in runs:
            for c in run["collections"]:
                now = next(x for x in point["collections"] if x["id"] == c["id"])
                rows = cdb.rows_p_of({"collections": [now]}, "x", 1)
                c["p"] = dict((r[3], cdb.platform_of(r[4], r[5], r[6], r[7], r[8])) for r in rows)
                for pl in c["p"].values():
                    pl["rd"][0] -= 1                                                # one more CAT I unassessed than now, on each platform
                c.update(cat1=now["cat1"], cat2=now["cat2"], cat3=now["cat3"])
        d = self.card(runs=runs, opts={"platform_filter": "Linux"})
        self.assertRegex(t.tile(d, "CORA risk")["note"], r"^▼ \d+\.\d points since ")

    def test_status_by_rating(self):
        self.assertEqual(dict((k, v) for k, v in f.CORA_STATUS.items()),
                         {"Very High": "critical", "High": "critical", "Moderate": "warning", "Low": "ok", "Very Low": "ok"})


class Weight(unittest.TestCase):
    def test_the_weight_is_hosts_times_the_cat(self):
        d = t.card()
        for r in t.section(d, "Findings", "web")["rows"]:
            self.assertEqual(r[6], r[5] * {"CAT I": 10, "CAT II": 4, "CAT III": 1}[r[2]])
        w = next(x for x in d["xlsx"] if x["name"] == "Rules across hosts")
        self.assertEqual([r[5] for r in w["rows"]], sorted([r[5] for r in w["rows"]], reverse=True))


if __name__ == "__main__":
    unittest.main()
