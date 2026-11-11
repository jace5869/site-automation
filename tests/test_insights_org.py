#!/usr/bin/env python3
"""Ticket insights by the org map (1.8.0, servicenow_insights_hierarchy_source: map): management's own tree, any depth -
the map's forms (a list of levels, "A > B", the older {site, directorate}), the groups placed only by it, those with
tickets it does not name in the unmapped bucket (listed with their tickets) and tickets with no group in Unassigned, both
last; a page and rollup at every level (each ticket once at every level), the pages' paths nested, the breadcrumbs, the
side by side of a node's children, the View menu and side bar at any depth; the Custom view - the tickets' compact rows
carried in the All page as JSON, the figures for any choice of groups worked out the way the model does (custom_figures,
and the page's script in a headless browser when there is one), the address keeping the choice. The made-up site of
tests/servicenow/insights_seed.py, its groups renamed into a four-level tree of placeholders.
    python3 tests/test_insights_org.py"""
import copy
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_servicenow_insights as T  # noqa: E402

F, P = T.F, T.P
# the seed's groups, renamed; the map: four levels, a group straight under a site, names with "&" and "/", one not mapped
REN = {"SITEA - J6 - Service Desk": "SITEA - Ops - Desk - SPLIT", "SITEA - j63 - Windows": "SITEA - Ops - Windows - SPLIT",
       "SITEB - Network": "SITEB - Network", "SITEB - Ops - VDI - nova": "SITEB - Ops - VDI", "SITE-C - CODEX - Linux": "SITEC - Linux"}
MAP = {"SITEA - Ops - Desk - SPLIT": ["SITEA", "SPLIT", "Operations", "EUCS"],
       "SITEA - Ops - Windows - SPLIT": "SITEA > SPLIT > Operations > Systems",
       "SITEB - Network": ["SITEB", "Networks & Comms / East"],
       "SITEB - Ops - VDI": ["SITEB"],
       "SITEA - Business - Apps": ["SITEA", "SPLIT", "Business"],          # no tickets: no node
       "SITEA - Empty": []}                                                  # no level: said, left out
_M = {}


def build():
    r = copy.deepcopy(T.site(sites=True)["r"])
    for i, it in enumerate(r["items"]):
        it["group"] = REN.get(it.get("group"), it.get("group"))
        if i % 97 == 0:
            it["group"] = ""                                                 # no group: Unassigned
    return r


def model(**kw):
    key = json.dumps(kw, sort_keys=True)
    if key not in _M:
        o = dict(T.OPTS, hierarchy_source="map", hierarchy_map=MAP, **kw)
        _M[key] = F.servicenow_insights_model(build(), None, T.site(sites=True)["mask"], o)
    return _M[key]


def node(m, *path):
    return next(n for n in F.org_walk(m["org"]) if n["path"] == list(path))


class TheMap(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(F.org_levels(["SITEA", " SPLIT ", "", "Operations"]), ["SITEA", "SPLIT", "Operations"])
        self.assertEqual(F.org_levels("SITEA >  SPLIT > Ops & Eng / East"), ["SITEA", "SPLIT", "Ops & Eng / East"])
        self.assertEqual(F.org_levels({"site": "SITEA", "directorate": "J6"}), ["SITEA", "J6"])
        m, bad = F.org_map(MAP)
        self.assertEqual(m["siteb - ops - vdi"], ("SITEB - Ops - VDI", ["SITEB"]))
        self.assertEqual(bad, ["SITEA - Empty"])

    def test_placed_only_by_the_map(self):
        h = F.servicenow_insights_hierarchy(["SITEA - OPS - DESK - SPLIT", "SITEC - Linux"], {"hierarchy_source": "map", "hierarchy_map": MAP})
        self.assertEqual((h["SITEA - OPS - DESK - SPLIT"]["path"], h["SITEA - OPS - DESK - SPLIT"]["rule"]), (["SITEA", "SPLIT", "Operations", "EUCS"], "map"))
        self.assertEqual((h["SITEC - Linux"]["path"], h["SITEC - Linux"]["rule"]), (["Unknown"], "unknown"))
        h = F.servicenow_insights_hierarchy(["SITEC - Linux"], {"hierarchy_source": "map", "hierarchy_map": MAP, "unmapped_bucket": "Not mapped"})
        self.assertEqual(h["SITEC - Linux"]["path"], ["Not mapped"])


class TheTree(unittest.TestCase):
    def test_any_depth_and_the_buckets_last(self):
        m = model()
        top = [n["label"] for n in m["org"]]
        self.assertEqual(top, ["SITEA", "SITEB", "Unknown", "Unassigned"])
        self.assertEqual([n["label"] for n in node(m, "SITEA", "SPLIT", "Operations")["children"]], ["EUCS", "Systems"])
        self.assertEqual(node(m, "SITEA", "SPLIT", "Operations", "EUCS")["groups"], ["SITEA - Ops - Desk - SPLIT"])
        self.assertEqual(node(m, "SITEB")["groups"], ["SITEB - Ops - VDI"])                     # straight under the site
        self.assertEqual(node(m, "Unassigned")["groups"], [""])
        self.assertFalse([n for n in F.org_walk(m["org"]) if n["label"] == "Business"])         # no tickets: no node
        self.assertEqual(m["hier_unknown"], [["SITEC - Linux", 93]])
        self.assertEqual(m["org_bad"], ["SITEA - Empty"])

    def test_rollups_at_every_level(self):
        """Each ticket once at every level: a node's figures are its groups' and its children's."""
        m = model()
        for nd in F.org_walk(m["org"]):
            s = m["scopes"][nd["key"]]
            parts = [m["scopes"][c["key"]]["counts"]["incidents"] for c in nd["children"]]
            parts += [m["custom"] and F.custom_figures(m["custom"], [m["custom"]["groups"].index(g)])["incidents"] for g in nd["groups"]]
            self.assertEqual(s["counts"]["incidents"], sum(parts), nd["key"])
        self.assertEqual(sum(m["scopes"][n["key"]]["counts"]["incidents"] for n in m["org"]), m["scopes"]["all"]["counts"]["incidents"])

    def test_pages_paths_crumbs_and_side_by_side(self):
        m = model()
        eucs = node(m, "SITEA", "SPLIT", "Operations", "EUCS")
        s = m["scopes"][eucs["key"]]
        self.assertEqual(s["path"], ["sitea", "split", "operations", "eucs"])
        self.assertEqual([c[1] for c in s["crumbs"]], ["All", "SITEA", "SPLIT", "Operations", "EUCS"])
        team = m["scopes"]["SITEA - Ops - Desk - SPLIT"]
        self.assertEqual((team["path"], team["parent"]), (["sitea", "split", "operations", "eucs", "sitea-ops-desk-split"], eucs["key"]))
        self.assertEqual(m["scopes"]["all"]["side"]["kind"], "Sites")
        self.assertEqual([x["label"] for x in m["scopes"]["all"]["side"]["rows"]], ["SITEA", "SITEB", "Unknown", "Unassigned"])
        siteb = m["scopes"][node(m, "SITEB")["key"]]
        self.assertEqual((siteb["side"]["kind"], [x["label"] for x in siteb["side"]["rows"]]), ("Units and teams", ["Networks & Comms / East", "SITEB - Ops - VDI"]))
        self.assertEqual(m["scopes"][node(m, "SITEA", "SPLIT")["key"]]["teams_under"], ["SITEA - Ops - Desk - SPLIT", "SITEA - Ops - Windows - SPLIT"])
        self.assertEqual(sorted(p["path"][-1] for p in m["pages"] if p["level"] == "team"),
                         ["sitea-ops-desk-split", "sitea-ops-windows-split", "siteb-ops-vdi"])     # SITEB - Network: inactive in the seed

    def test_the_old_sources_unchanged(self):
        m = T.site(sites=True)["model"]
        self.assertEqual((m["org"], m["custom"]), ([], {}))
        self.assertNotIn('id="custom"', P.servicenow_insights_page(m, "all", {"links": T.page_links(m)}))


class TheCustomView(unittest.TestCase):
    def test_rows_and_figures(self):
        """The compact rows give the same figures as the model, for every node and All."""
        m = model()
        c = m["custom"]
        self.assertEqual(sorted(c["cols"]), ["d", "g", "hold", "hops", "noslo", "o", "ob", "out", "p", "r7", "resp", "risk", "s", "t", "tis"])
        self.assertEqual((c["hop_limit"], [c["outcomes"][i] for i in c["unable"]]), (3, ["Known Error", "No Resolution Provided"]))
        self.assertTrue(all(len(v) == c["n"] for v in c["cols"].values()))
        self.assertLess(len(json.dumps(c, separators=(",", ":"))) / c["n"], 64)                     # 15 small columns: 1.7.0's and 1.9.0's
        gi = dict((g, i) for i, g in enumerate(c["groups"]))
        for nd in list(F.org_walk(m["org"])) + [{"key": "all", "all": c["groups"]}]:
            s = m["scopes"][nd["key"]]
            got = F.custom_figures(c, [gi[g] for g in nd["all"]])
            # 1.9.0's, as its scope_model: the time in each state (carried in tenths of hours), the backlog's age
            tis, tis_n = got.pop("tis"), got.pop("tis_n")
            self.assertEqual(tis_n, s["lifecycle"]["n"], nd["key"])
            for k_, v in s["lifecycle"]["avg"].items():
                self.assertAlmostEqual(tis[k_] or 0, v or 0, delta=0.051, msg=(nd["key"], k_))
            self.assertEqual(got.pop("aging"), s["aging"]["total"], nd["key"])
            self.assertEqual(got,
                             {"incidents": s["counts"]["incidents"], "requests": s["counts"]["requests"], "resolved": s["counts"]["resolved"],
                              "backlog": [s["backlog"][0], s["backlog"][1]], "median_h": s["median_h"][0], "sla": s["sla"][0],
                              # 1.7.0's, as its scope_model works them out
                              "resp": s["resp"][0], "no_slo": s["no_slo"][0], "hops_open": s["hops"]["open"], "hops_done_pct": s["hops"]["done_pct"],
                              "fcr": s["fcr"]["now"], "hold": s["hold"]["open"], "unable": s["outcomes"]["unable"][0]}, nd["key"])

    def test_too_many_rows_or_off(self):
        # 1.10.0: the window before's tickets go first (no compare), then the Custom view
        one = model(custom_view_max_rows=1500)["custom"]
        self.assertTrue(one["no_compare"])
        self.assertLess(one["n"], model()["custom"]["n"])
        self.assertEqual([p["ca"] for p in one["periods"] if p["name"] == "all"], [None])
        self.assertIn("The window before is not carried", P.servicenow_insights_page(model(custom_view_max_rows=1500), "all", {"links": T.page_links(model())}))
        self.assertEqual(model(custom_view_max_rows=10)["custom"], {"skipped": one["n"], "max": 10})
        self.assertEqual(model(custom_view=False)["custom"], {})
        page = P.servicenow_insights_page(model(custom_view_max_rows=10), "all", {"links": T.page_links(model())})
        self.assertIn("more than servicenow_insights_custom_view_max_rows (10)", page)
        self.assertNotIn('id="cv-data"', page)

    def test_the_address(self):
        self.assertEqual(P.cv_ranges([7, 0, 1, 2, 3, 9, 10]), "0-3,7,9-10")


class ThePages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = model()
        cls.all = P.servicenow_insights_page(cls.m, "all", {"links": T.page_links(cls.m), "look": "brief"})

    def test_the_all_page_carries_the_custom_view(self):
        h = self.all
        self.assertIn('<section class="card" id="custom">', h)
        data = re.search(r'<script type="application/json" id="cv-data">(.*?)</script>', h, re.S).group(1)
        self.assertNotIn("</", data)
        d = json.loads(data)
        self.assertEqual((d["n"], d["groups"][-1]), (self.m["custom"]["n"], ""))
        self.assertEqual(h.count('class="cvc" data-g='), len(d["groups"]))
        self.assertEqual(h.count('<li class="cvn"'), sum(1 for _ in F.org_walk(self.m["org"])))
        self.assertLess(h.index('id="custom"'), h.index('id="glance"'))
        self.assertNotRegex(h, r"<script>(?!\s*$)")                     # no inline script but the page's own (moved to a file)

    def test_tree_navigation_any_depth(self):
        h = P.servicenow_insights_page(self.m, "SITEA - Ops - Desk - SPLIT", {"links": T.page_links(self.m, "../../../../../")})
        self.assertIn('class="team lv5"', h)                              # the team under four levels
        self.assertIn("Units and teams", h)
        self.assertIn('<details class="tn lv4" open>', h)
        self.assertRegex(h, r'All</a>.*SITEA</a>.*SPLIT</a>.*Operations</a>.*EUCS</a>.*<b>SITEA - Ops - Desk - SPLIT</b>')

    def test_a_unit_links_to_its_custom_view(self):
        k = node(self.m, "SITEB")["key"]
        h = P.servicenow_insights_page(self.m, k, {"links": T.page_links(self.m, "../")})
        c = self.m["custom"]["groups"]
        want = P.cv_ranges([c.index("SITEB - Network"), c.index("SITEB - Ops - VDI")])
        self.assertIn('href="../latest.html#cv=%s"' % want, h)
        self.assertIn("Custom view of these 2 groups", h)

    def test_how_this_was_made_lists_the_unmapped(self):
        self.assertIn("SITEC - Linux (93 tickets)", self.all)
        self.assertIn("Add them to servicenow_insights_hierarchy_map.", self.all)
        self.assertIn("SITEA - Empty", self.all)


class TheSplit(unittest.TestCase):
    """The split with the org map (1.8.0): the groups by the pattern, or by the label as one of their map levels
    (split_by), the pages twice more; the Custom view's quick choices; a split's page says where the Custom view is."""
    def test_by_level_pattern_or_both(self):
        self.assertEqual(model(split_label="SPLIT")["split"]["groups"], ["SITEA - Ops - Desk - SPLIT", "SITEA - Ops - Windows - SPLIT"])
        m = model(split_label="SPLIT", split_pattern="*vdi*", split_by="pattern")
        self.assertEqual(m["split"]["groups"], ["SITEB - Ops - VDI"])
        m = model(split_label="SPLIT", split_pattern="*vdi*")
        self.assertEqual(m["split"]["groups"], ["SITEA - Ops - Desk - SPLIT", "SITEA - Ops - Windows - SPLIT", "SITEB - Ops - VDI"])
        self.assertNotIn("split", model(split_label="SPLIT", split_by="pattern"))          # no pattern: no split

    def test_the_halves_add_up(self):
        m = model(split_label="SPLIT")
        k = node(m, "SITEA")["key"]
        self.assertEqual(sorted(m["variants"]), ["in", "out"])
        for key in ("all", k):
            self.assertEqual(m["variants"]["in"][key]["counts"]["incidents"] + m["variants"]["out"][key]["counts"]["incidents"],
                             m["scopes"][key]["counts"]["incidents"], key)

    def test_presets(self):
        m = model(split_label="SPLIT", custom_view_presets={"East": {"levels": ["SITEB"]}, "VDI": {"pattern": "*vdi*"}, "None": {"levels": ["Nowhere"]}})
        g = m["custom"]["groups"]
        got = dict((p["name"], [g[i] for i in p["idx"]]) for p in m["custom"]["presets"])
        self.assertEqual([p["name"] for p in m["custom"]["presets"]], ["SPLIT", "non-SPLIT", "East", "None", "VDI"])
        self.assertEqual(got["SPLIT"], ["SITEA - Ops - Desk - SPLIT", "SITEA - Ops - Windows - SPLIT"])
        self.assertEqual(got["non-SPLIT"], ["SITEB - Network", "SITEB - Ops - VDI", "SITEC - Linux", ""])
        self.assertEqual((got["East"], got["VDI"], got["None"]), (["SITEB - Network", "SITEB - Ops - VDI"], ["SITEB - Ops - VDI"], []))
        h = P.servicenow_insights_page(m, "all", {"links": T.page_links(m)})
        self.assertIn('<button type="button" class="cvp" data-name="SPLIT" data-idx="0-1"', h)

    def test_a_split_page_says_where_the_custom_view_is(self):
        m = model(split_label="SPLIT")
        links = dict(T.page_links(m), all="latest-split.html")
        h = P.servicenow_insights_page(m, "all", {"links": links, "variant": "in", "variant_links": {"": "latest.html", "in": "latest-split.html"}})
        self.assertIn('<b>Custom view:</b> on the <a href="latest.html">All page</a>.', h)
        self.assertNotIn('id="cv-data"', h)

    def test_the_tree_counts_what_tickets_opened_counts(self):
        m = model()
        c = m["custom"]
        h = P.servicenow_insights_page(m, "all", {"links": T.page_links(m)})
        i = c["groups"].index("SITEA - Ops - Desk - SPLIT")
        f = F.custom_figures(c, [i])
        self.assertIn('data-g="%d"><span class="cvl">SITEA - Ops - Desk - SPLIT</span></label><span class="cvs" title="Incidents and requests '
                      'opened in the period">%s opened</span>' % (i, "{:,}".format(f["incidents"] + f["requests"])), h)


class ThePeriods(unittest.TestCase):
    """1.10.0: the Custom view's periods - calendar months and Mon-Sun weeks, each against the one before - and their
    figures: the same as a model whose window is that period."""
    NY = F._tz("America/New_York")

    def b(self, until, prev_months=6, **o):
        u = datetime.datetime(*until, tzinfo=self.NY)
        s = F._midnight(F._month((u - datetime.timedelta(seconds=1)).date(), -5), self.NY)
        prev = F._midnight(F._month(s.date(), -prev_months), self.NY)
        return dict((p["name"], p) for p in F.period_bounds(s, u, prev, self.NY, o))

    def day(self, x):
        return x.astimezone(self.NY).strftime("%Y-%m-%d %H:%M")

    def test_a_window_that_ends_with_its_month(self):
        p = self.b((2026, 11, 1))                                         # the window ends on 2026-10-31
        self.assertEqual([p[k]["title"] for k in ("all", "1m", "2m")], ["May to October 2026", "October 2026", "September to October 2026"])
        self.assertNotIn("this_month", p)                                 # this month is last month: one button
        self.assertEqual([p[k]["ctitle"] for k in ("all", "1m", "2m")], ["November 2025 to April 2026", "September 2026", "July to August 2026"])
        # 2026-11-01 is a Sunday: the last whole Mon-Sun week is Oct 19-25, not the week Nov 1 ends
        self.assertEqual((p["1w"]["title"], p["1w"]["ctitle"]), ("2026-10-19 to 2026-10-25", "2026-10-12 to 2026-10-18"))
        self.assertEqual((p["2w"]["title"], p["2w"]["ctitle"]), ("2026-10-12 to 2026-10-25", "2026-09-28 to 2026-10-11"))
        self.assertEqual([p[k]["label"] for k in F.PERIODS if k in p], ["The window", "Last month", "Last 2 months", "Last 3 months",
                                                                         "Last 6 months", "Last week", "Last 2 weeks", "Last 4 weeks"])
        self.assertEqual((p["6m"]["title"], p["6m"]["ctitle"]), ("May to October 2026", "November 2025 to April 2026"))
        self.assertEqual((p["4w"]["title"], p["4w"]["ctitle"]), ("2026-09-28 to 2026-10-25", "2026-08-31 to 2026-09-27"))
        self.assertEqual([x["label"] for x in self.b((2026, 11, 10)).values()],       # June to November 10: 6 months do not fit
                         ["The window", "This month", "Last month", "Last 2 months", "Last 3 months", "Last week", "Last 2 weeks", "Last 4 weeks"])

    def test_a_window_that_ends_now(self):
        p = self.b((2026, 11, 10, 15, 30))                                # a Tuesday afternoon
        tm = p["this_month"]
        self.assertEqual((self.day(tm["a"]), self.day(tm["b"])), ("2026-11-01 00:00", "2026-11-10 15:30"))
        self.assertEqual((self.day(tm["ca"]), self.day(tm["cb"])), ("2026-10-01 00:00", "2026-10-10 15:30"))   # the same days of last month
        self.assertEqual(tm["title"], "2026-11-01 to 2026-11-10")
        self.assertEqual((p["1m"]["title"], p["1m"]["ctitle"]), ("October 2026", "September 2026"))
        self.assertEqual(p["1w"]["title"], "2026-11-02 to 2026-11-08")
        # the clocks went back on 2026-11-01: the week is 7 local days, every edge a local midnight
        w = p["1w"]
        utc = datetime.timezone.utc
        self.assertEqual((w["b"].astimezone(utc) - w["a"].astimezone(utc)).total_seconds(), 7 * 86400)
        c2 = self.b((2026, 11, 3))["1w"]
        self.assertEqual((c2["title"], (c2["b"].astimezone(utc) - c2["a"].astimezone(utc)).total_seconds()),
                         ("2026-10-26 to 2026-11-01", 7 * 86400 + 3600))
        self.assertTrue(all(self.day(x).endswith("00:00") for x in (c2["a"], c2["b"], c2["ca"], c2["cb"])))

    def test_the_week_start_and_what_does_not_fit(self):
        p = self.b((2026, 11, 10), week_start="sun")
        self.assertEqual(p["1w"]["title"], "2026-11-01 to 2026-11-07")
        q = self.b((2026, 11, 10), prev_months=0)                         # no window before: no compare for the window
        self.assertEqual((q["all"]["ca"], q["1m"]["ctitle"]), (None, "September 2026"))
        u = datetime.datetime(2026, 11, 10, tzinfo=self.NY)
        nine = u - datetime.timedelta(days=9)                             # 2026-11-01 to 11-09: one whole week, no whole month
        short, left = F.period_plan(nine, u, nine, self.NY, {"periods": "all, 1m, 1w"})
        self.assertEqual([x["name"] for x in short], ["all"])            # 1.16.0: 1w without the week before - left out, said
        self.assertEqual(left, [{"name": "1m", "why": "window"}, {"name": "1w", "why": "before"}])
        self.assertEqual(short[0]["ca"], None)                            # the window keeps its button without a compare
        self.assertEqual([x["name"] for x in F.period_bounds(u - datetime.timedelta(days=7), u, u, self.NY, {"periods": "1w"})], [])
        for bad in ("fortnight", "13m", "9w", "0m", "1d"):
            with self.assertRaises(ValueError) as e:
                F.period_bounds(u - datetime.timedelta(days=7), u, u, self.NY, {"periods": [bad]})
            self.assertEqual(str(e.exception), "servicenow_insights_periods: %s is not a period - all, this_month, 1m to 12m (calendar "
                                               "months) or 1w to 8w (weeks)" % bad)
        with self.assertRaises(ValueError):
            F.period_bounds(u - datetime.timedelta(days=7), u, u, self.NY, {"week_start": "someday"})

    def test_business_days_from_the_calendar(self):
        spec = F.business_hours_spec({"days": ["mon", "tue", "wed", "thu", "sat"], "holidays": ["2026-10-13", "2026-10-17", "2026-10-18"]})
        since = datetime.datetime(2026, 10, 1, tzinfo=self.NY)
        until = datetime.datetime(2026, 11, 3, 12, 0, tzinfo=self.NY)
        cal = F.day_calendar(datetime.datetime(2026, 9, 1, tzinfo=self.NY), until, since, spec, self.NY)
        days, hol = set(cal["days"]), cal["hol"]
        self.assertEqual(hol, ["2026-10-13", "2026-10-17"])               # a holiday on a day off is no business day anyway
        for a in [datetime.datetime(2026, 8, 20, 9, tzinfo=self.NY), datetime.datetime(2026, 10, 2, 23, tzinfo=self.NY),
                  datetime.datetime(2026, 10, 12, 1, tzinfo=self.NY)]:
            for k in range(0, len(cal["mid"]), 3):
                b = since + datetime.timedelta(seconds=cal["mid"][k])
                ob = F._bd_ord(a.date(), days, hol) - cal["base"]
                self.assertEqual(max(cal["bo"][k] - ob, 0), F.business_days(a, b, spec, self.NY), (a, b))

    def test_the_figures_of_a_month_are_a_month_model(self):
        m = model()
        c = m["custom"]
        idx = list(range(len(c["groups"])))
        r = build()
        r["window"] = T.rd.window(1, 0, "2026-10-31", True, "", T.NOW)   # October, against September
        m1 = F.servicenow_insights_model(r, None, T.site(sites=True)["mask"], dict(T.OPTS, hierarchy_source="map", hierarchy_map=MAP))
        s1 = m1["scopes"]["all"]
        p = [x for x in c["periods"] if x["name"] == "1m"][0]
        got, was = F.custom_figures(c, idx, (p["a"], p["b"])), F.custom_figures(c, idx, (p["ca"], p["cb"]))
        self.assertEqual((got["incidents"], got["requests"], got["resolved"], got["backlog"]),
                         (s1["counts"]["incidents"], s1["counts"]["requests"], s1["counts"]["resolved"], [s1["backlog"][0], s1["backlog"][1]]))
        self.assertEqual((got["median_h"], got["sla"], got["resp"], got["no_slo"], got["hops_done_pct"], got["fcr"], got["unable"]),
                         (s1["median_h"][0], s1["sla"][0], s1["resp"][0], s1["no_slo"][0], s1["hops"]["done_pct"], s1["fcr"]["now"],
                          s1["outcomes"]["unable"][0]))
        self.assertEqual(got["aging"], s1["aging"]["total"])
        for k_, v in s1["lifecycle"]["avg"].items():
            self.assertAlmostEqual(got["tis"][k_] or 0, v or 0, delta=0.051, msg=k_)
        # September, the month before: the model's window before
        self.assertEqual((was["incidents"], was["median_h"], was["sla"], was["resp"]),
                         (s1["counts"]["incidents_prev"], s1["median_h"][1], s1["sla"][1], s1["resp"][1]))
        # what is known only now: for a period that ends at the report's end only
        w1 = [x for x in c["periods"] if x["name"] == "1w"][0]
        self.assertEqual((got["hold"] is None, F.custom_figures(c, idx, (w1["a"], w1["b"]))["hold"]), (False, None))


def browser():
    for b in ("google-chrome", "chromium", "chromium-browser"):
        if shutil.which(b):
            return shutil.which(b)
    return None


@unittest.skipUnless(browser(), "no headless browser here")
class PeriodButtons(unittest.TestCase):
    """1.16.0: any Nm and Nw; two short labeled rows (Months, Weeks) after The window and This month, the number on the
    button and the full label in its title and aria-label; one select on a phone; what the page's tickets can not cover
    left out, in one muted line - the window too short, or the window before over the row cap."""

    def test_the_rows(self):
        m = model()
        c = m["custom"]
        self.assertEqual([p["name"] for p in c["periods"]], ["all", "1m", "2m", "3m", "6m", "1w", "2w", "4w"])
        h = P.servicenow_insights_page(m, "all", {"links": T.page_links(m)})
        bar = h[h.index('<div class="cvper js"'):h.index('<div class="tiles">')]
        self.assertIn('<div class="cvprow cvpfirst"><button type="button" class="cvpd" data-name="all" aria-pressed="false" '
                      'title="The window: May to October 2026, compared with November 2025 to April 2026" aria-label="The window">The window</button></div>', bar)
        self.assertIn('<div class="cvprow" role="group" aria-label="Months"><span class="cvplab">Months</span>', bar)
        self.assertIn('<button type="button" class="cvpd num" data-name="3m" aria-pressed="false" title="Last 3 months: August to October 2026, '
                      'compared with May to July 2026" aria-label="Last 3 months">3</button>', bar)
        self.assertIn('<div class="cvprow" role="group" aria-label="Weeks"><span class="cvplab">Weeks</span>', bar)
        self.assertIn('aria-label="Last 4 weeks">4</button>', bar)
        self.assertLess(bar.index("Months"), bar.index("Weeks"))
        self.assertLess(bar.index("Weeks</span>"), bar.index('id="cvfrom"'))       # the from / to after the buttons
        sel = bar[bar.index('<select id="cvpsel">'):bar.index("</select>")]
        self.assertEqual(re.findall(r'<option value="([^"]*)"', sel), [p["name"] for p in c["periods"]] + [""])
        self.assertIn('<p class="cvleft mut">9 and 12 months need a longer window: servicenow_insights_months.</p>', bar)
        self.assertIn("@media (max-width:640px){.cvpbtn{display:none}.cvpsel{display:inline-flex", h)

    def test_the_row_cap_said(self):
        n = model()["custom"]["n"]
        m = model(custom_view_max_rows=n - 1)
        c = m["custom"]
        if not c.get("no_compare"):
            self.skipTest("the window alone is over the cap too")
        self.assertEqual([p["name"] for p in c["periods"] if p.get("kind")], ["1m", "2m", "3m", "1w", "2w", "4w"])
        self.assertIn("6 months need the period before as well: the window before is over servicenow_insights_custom_view_max_rows (%s)."
                      % "{:,}".format(n - 1), c["left_line"])
        self.assertIn("9 and 12 months need a longer window", c["left_line"])

    def test_any_names_in_any_order(self):
        m = model(periods=["12m", "all", "8w", "5m", "1w"])
        self.assertEqual([p["name"] for p in m["custom"]["periods"]], ["all", "8w", "5m", "1w"])     # in the order given
        self.assertEqual(m["custom"]["left_line"], "12 months need a longer window: servicenow_insights_months.")
        h = P.servicenow_insights_page(m, "all", {"links": T.page_links(m)})
        self.assertIn('aria-label="Last 5 months">5</button>', h)
        self.assertIn('aria-label="Last 8 weeks">8</button>', h)
        with self.assertRaises(ValueError):
            model(periods=["all", "14m"])


class TheScript(unittest.TestCase):
    """The page's own script, in a headless browser: the address's choice ticked (its units half-ticked, the counts), the
    figures as custom_figures works them out."""
    def test_in_a_browser(self):
        m = model()
        c = m["custom"]
        page = P.servicenow_insights_page(m, "all", {"links": T.page_links(m), "look": "brief"})
        probe = ('<script>addEventListener("load",function(){setTimeout(function(){var f=window.siteInsightsFigures,D=document;'
                 'var o={one:f([0]),two:f([0,2]),every:f(%s),open:D.getElementById("cv").open,shown:!D.getElementById("cvres").hidden,'
                 'ticked:[].slice.call(D.querySelectorAll("input.cvc[data-g]")).filter(function(b){return b.checked}).map(function(b){return +b.getAttribute("data-g")}),'
                 'half:[].slice.call(D.querySelectorAll("input.cvc")).filter(function(b){return b.indeterminate}).length,'
                 'rows:D.getElementById("cvtab").tBodies[0].rows.length,opened:D.getElementById("cv-opened").textContent};'
                 'D.title="R "+JSON.stringify(o)},500)})</script></body>') % json.dumps(list(range(len(c["groups"]))))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "all.html")
            with open(path, "w") as fh:
                fh.write(page.replace("</body>", probe))
            out = subprocess.run([browser(), "--headless=new", "--disable-gpu", "--no-sandbox", "--virtual-time-budget=6000", "--dump-dom",
                                  "file://%s#cv=0,2" % path], capture_output=True, text=True, timeout=120).stdout
        got = json.loads(re.search(r"<title>R (.*?)</title>", out).group(1).replace("&quot;", '"'))
        for k, idx in (("one", [0]), ("two", [0, 2]), ("every", list(range(len(c["groups"]))))):
            want = F.custom_figures(c, idx)
            for f in ("incidents", "requests", "resolved", "backlog", "no_slo", "hops_open", "hold"):
                self.assertEqual(got[k][f], want[f], (k, f))
            for f in ("median_h", "sla", "resp", "hops_done_pct", "fcr", "unable"):
                self.assertAlmostEqual(got[k][f], want[f], delta=0.051, msg=(k, f))
        self.assertEqual((got["open"], got["shown"], got["ticked"], got["rows"]), (True, True, [0, 2], 2))
        self.assertEqual(got["half"], 4)                                  # SITEA, SPLIT, Operations, SITEB: some under them
        want = F.custom_figures(c, [0, 2])
        self.assertEqual(got["opened"], "{:,}".format(want["incidents"] + want["requests"]))

    def test_the_periods_in_a_browser(self):
        """1.10.0: every period and the one before it, worked out by the page's script as custom_figures does; a period
        in the address (a calendar one, and a from / to) chosen, titled, charted; what is known only now said so."""
        m = model()
        c = m["custom"]
        page = P.servicenow_insights_page(m, "all", {"links": T.page_links(m), "look": "brief"})
        idx = [0, 2, 4]
        spans = [[p["a"], p["b"]] for p in c["periods"]] + [[p["ca"], p["cb"]] for p in c["periods"] if p["ca"] is not None]
        probe = ('<script>addEventListener("load",function(){setTimeout(function(){var f=window.siteInsightsFigures,D=document,sp=%s;'
                 'function tx(i){return D.getElementById(i).textContent}'
                 'D.title="R "+JSON.stringify({figs:sp.map(function(s){return f(%s,s[0],s[1])}),'
                 'pressed:[].slice.call(D.querySelectorAll("button.cvpd[aria-pressed=true]")).map(function(b){return b.getAttribute("data-name")}),'
                 'title:tx("cvpt"),from:D.getElementById("cvfrom").value,to:D.getElementById("cvto").value,label:tx("cv-backlog-l"),'
                 'hold:tx("cv-hold"),holdd:tx("cv-hold-d"),bars:D.querySelectorAll("#cvchart rect").length,rows:D.getElementById("cvtab").tBodies[0].rows.length,'
                 'x:tx("cv-opened-x"),hash:location.hash})},500)})</script></body>') % (json.dumps(spans), json.dumps(idx))
        got = {}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "all.html")
            with open(path, "w") as fh:
                fh.write(page.replace("</body>", probe))
            for k, h in (("week", "#cv=0,2,4&p=1w"), ("range", "#cv=0,2,4&p=2026-10-05_2026-10-18")):
                out = subprocess.run([browser(), "--headless=new", "--disable-gpu", "--no-sandbox", "--virtual-time-budget=6000", "--dump-dom",
                                      "file://%s%s" % (path, h)], capture_output=True, text=True, timeout=120).stdout
                got[k] = json.loads(re.search(r"<title>R (.*?)</title>", out).group(1).replace("&quot;", '"').replace("&amp;", "&"))
        for s_, g in zip(spans, got["week"]["figs"]):
            want = F.custom_figures(c, idx, tuple(s_))
            for f in ("incidents", "requests", "resolved", "backlog", "no_slo", "hops_open", "hold", "aging", "tis_n"):
                self.assertEqual(g[f], want[f], (s_, f))
            for f in ("median_h", "sla", "resp", "hops_done_pct", "fcr", "unable"):     # a tie (91.25): Python rounds it half
                self.assertAlmostEqual(g[f] or 0, want[f] or 0, delta=0.101, msg=(s_, f))  # to even, the script half up
            for k_ in want["tis"]:
                self.assertAlmostEqual(g["tis"][k_] or 0, want["tis"][k_] or 0, delta=0.051, msg=(s_, k_))
        w = got["week"]
        self.assertEqual((w["pressed"], w["title"], w["from"], w["to"]), (["1w"], "2026-10-19 to 2026-10-25, compared with 2026-10-12 to 2026-10-18",
                                                                          "2026-10-19", "2026-10-25"))
        self.assertEqual((w["label"], w["hold"], w["holdd"], w["rows"], w["bars"]), ("Open at the period's end", "—", "known now only", 3, 14))
        self.assertRegex(w["x"], r"^(▲ |▼ )\d+%$|^no change$")
        r = got["range"]
        self.assertEqual((r["pressed"], r["title"], r["from"], r["to"], r["bars"]),
                         ([], "2026-10-05 to 2026-10-18, compared with 2026-09-21 to 2026-10-04", "2026-10-05", "2026-10-18", 28))
        self.assertEqual(r["hash"], "#cv=0,2,4&p=2026-10-05_2026-10-18")

    def test_a_preset_in_the_address(self):
        m = model(split_label="SPLIT")
        c = m["custom"]
        page = P.servicenow_insights_page(m, "all", {"links": T.page_links(m)})
        probe = ('<script>addEventListener("load",function(){setTimeout(function(){var D=document;D.title="R "+JSON.stringify({'
                 'ticked:[].slice.call(D.querySelectorAll("input.cvc[data-g]")).filter(function(b){return b.checked}).map(function(b){return +b.getAttribute("data-g")}),'
                 'pressed:[].slice.call(D.querySelectorAll("button.cvp[aria-pressed=true]")).map(function(b){return b.getAttribute("data-name")}),'
                 'opened:D.getElementById("cv-opened").textContent,hash:location.hash})},500)})</script></body>')
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "all.html")
            with open(path, "w") as fh:
                fh.write(page.replace("</body>", probe))
            out = subprocess.run([browser(), "--headless=new", "--disable-gpu", "--no-sandbox", "--virtual-time-budget=6000", "--dump-dom",
                                  "file://%s#cv=preset:non-SPLIT" % path], capture_output=True, text=True, timeout=120).stdout
        got = json.loads(re.search(r"<title>R (.*?)</title>", out).group(1).replace("&quot;", '"'))
        want = [p["idx"] for p in c["presets"] if p["name"] == "non-SPLIT"][0]
        f = F.custom_figures(c, want)
        self.assertEqual((got["ticked"], got["pressed"], got["hash"]), (want, ["non-SPLIT"], "#cv=preset:non-SPLIT"))
        self.assertEqual(got["opened"], "{:,}".format(f["incidents"] + f["requests"]))


if __name__ == "__main__":
    unittest.main(verbosity=1)
