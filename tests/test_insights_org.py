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
        self.assertEqual(sorted(c["cols"]), ["d", "g", "hold", "hops", "noslo", "o", "out", "p", "r7", "resp", "risk", "s", "t"])
        self.assertEqual((c["hop_limit"], [c["outcomes"][i] for i in c["unable"]]), (3, ["Known Error", "No Resolution Provided"]))
        self.assertTrue(all(len(v) == c["n"] for v in c["cols"].values()))
        self.assertLess(len(json.dumps(c, separators=(",", ":"))) / c["n"], 56)                     # 13 small columns with the 1.7.0 figures
        gi = dict((g, i) for i, g in enumerate(c["groups"]))
        for nd in list(F.org_walk(m["org"])) + [{"key": "all", "all": c["groups"]}]:
            s = m["scopes"][nd["key"]]
            self.assertEqual(F.custom_figures(c, [gi[g] for g in nd["all"]]),
                             {"incidents": s["counts"]["incidents"], "requests": s["counts"]["requests"], "resolved": s["counts"]["resolved"],
                              "backlog": [s["backlog"][0], s["backlog"][1]], "median_h": s["median_h"][0], "sla": s["sla"][0],
                              # 1.7.0's, as its scope_model works them out
                              "resp": s["resp"][0], "no_slo": s["no_slo"][0], "hops_open": s["hops"]["open"], "hops_done_pct": s["hops"]["done_pct"],
                              "fcr": s["fcr"]["now"], "hold": s["hold"]["open"], "unable": s["outcomes"]["unable"][0]}, nd["key"])

    def test_too_many_rows_or_off(self):
        self.assertEqual(model(custom_view_max_rows=10)["custom"], {"skipped": model()["custom"]["n"], "max": 10})
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


def browser():
    for b in ("google-chrome", "chromium", "chromium-browser"):
        if shutil.which(b):
            return shutil.which(b)
    return None


@unittest.skipUnless(browser(), "no headless browser here")
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
