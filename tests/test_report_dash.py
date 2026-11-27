#!/usr/bin/env python3
"""1.71.0: the dashboards (plugins/filter/report_dash_filters.py and the [data-dash] block of site-report.js).

- Parity: site-report.js's engine (dash-engine to end-dash-engine, run in node) gives the same view as dash_view for
  every selection: no filter, each collection, platform, CAT, a part of a host's name, and their pairs.
- The engine: CORA's band edges exact, a residual row keeps the totals, a filter a fact cannot follow is said (not by
  host), a tile or a line of another CAT is left out, the top lists by weight.
- The scorecard's dashboard: its totals are the report's own (the tiles, By collection), its rules and hosts the
  Findings' and the Hosts' rows (their links), the ops look draws it inside At a glance and the other looks do not.
- The page: in headless Chrome the filters draw the panels again (a collection, a CAT, a host) - skipped without a
  browser."""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
sys.path.insert(0, os.path.join(REPO, "tests"))
import report_dash_filters as R  # noqa: E402

FIX = os.path.join(REPO, "tests", "fixtures", "report_fit")
JS = os.path.join(REPO, "roles", "site_email", "templates", "site_report.js")
GEOM = ("d", "end", "dots", "ty", "xs")


def engine():
    with open(JS) as f:
        src = f.read()
    m = re.search(r"/\*dash-engine\*/(.*?)/\*end-dash-engine\*/", src, re.S)
    assert m, "no dash-engine block in site_report.js"
    return m.group(1)


def js_views(dash, sels):
    """The JS engine's views, one per selection (node)."""
    if not shutil.which("node"):
        return None
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "run.js")
        with open(p, "w") as f:
            f.write(engine() + "\nvar I=JSON.parse(require('fs').readFileSync(0,'utf8'));"
                    "process.stdout.write(JSON.stringify(I.sels.map(function(s){return dashE.view(I.dash,s)})));\n")
        out = subprocess.run(["node", p], input=json.dumps({"dash": dash, "sels": sels}), capture_output=True, text=True, timeout=60)
        if out.returncode:
            raise AssertionError(out.stderr[-2000:])
        return json.loads(out.stdout)


def strip(v):
    """A view without the drawing's coordinates (compared apart, to 0.15)."""
    if isinstance(v, dict):
        return dict((k, strip(x)) for k, x in v.items() if k not in GEOM)
    if isinstance(v, list):
        return [strip(x) for x in v]
    if isinstance(v, float) and v == int(v):
        return int(v)
    return v


def sels_of(dash):
    cols, pfs, hosts = set(), set(), set()
    for f in (dash.get("facts") or {}).values():
        c = f.get("cols") or []
        for r in f.get("rows") or []:
            row = dict(zip(c, r))
            if "col" in row:
                cols.add(row["col"])
            if "pf" in row and row["pf"]:
                pfs.add(row["pf"])
            if "h" in row:
                h = row["h"]
                h = (f.get("dict") or {}).get("h", [])[h] if isinstance(h, int) and "h" in (f.get("dict") or {}) else h
                if h:
                    hosts.add(h)
    out = [{}]
    out += [{"col": c} for c in sorted(cols)] + [{"pf": p} for p in sorted(pfs)] + [{"cat": "k%d" % k} for k in (1, 2, 3)]
    out += [{"h": h[:4]} for h in sorted(hosts)][:6] + [{"h": "ZZ-none"}, {"h": "WIN"}]
    out += [{"col": c, "cat": "k2"} for c in sorted(cols)] + [{"pf": p, "cat": "k1"} for p in sorted(pfs)]
    out += [{"col": c, "pf": p} for c in sorted(cols) for p in sorted(pfs)][:8]
    out += [{"col": c, "h": "0"} for c in sorted(cols)]
    return out


def pi(dash, t):
    return [i for i, p in enumerate(dash["panels"]) if p["t"] == t][0]


def fixture_dash(name):
    with open(os.path.join(FIX, name + ".json")) as f:
        return json.load(f)


class Parity(unittest.TestCase):
    def test_js_and_python_agree(self):
        docs = [d for d in (fixture_dash(n) for n in ("stig_scorecard_cora_web", "stig_scorecard_open_web")) if d.get("dashboard")]
        self.assertTrue(docs, "no fixture with a dashboard")
        for doc in docs:
            dash = doc["dashboard"]
            sels = sels_of(dash)
            got = js_views(dash, sels)
            if got is None:
                self.skipTest("no node here")
            for s, j in zip(sels, got):
                p = json.loads(json.dumps(R.dash_view(dash, s)))
                self.assertEqual(strip(j), strip(p), "selection %s" % s)
                for pv, jv in zip(p, j):              # the drawing: the same to a tenth of a unit
                    for a, b in zip(pv.get("lines") or [], jv.get("lines") or []):
                        self.assertEqual(a["d"].count("M"), b["d"].count("M"))
                        for x, y in zip(re.findall(r"[-\d.]+", a["d"]), re.findall(r"[-\d.]+", b["d"])):
                            self.assertAlmostEqual(float(x), float(y), delta=0.15)

    def test_cora_edges_in_both(self):
        cases = [([100, 100, 100], [100, 100, 100], [18, 29, 4]),       # exactly 20 %: Very High
                 ([10, 10, 10], [10, 10, 10], [1, 0, 0]),             # exactly 10 %... of CAT I alone
                 ([200, 400, 80], [200, 400, 80], [0, 19, 3]),        # CAT II 4.75 %: Low
                 ([200, 400, 80], [200, 400, 80], [0, 20, 3]),        # CAT II exactly 5 %: Moderate
                 ([10, 40, 8], [5, 40, 8], [0, 0, 0]),                # unassessed CAT I counts
                 ([0, 0, 0], [0, 0, 0], [0, 0, 0]),
                 ([0, 12, 0], [12, 12, 0], [0, 0, 0])]
        if not shutil.which("node"):
            self.skipTest("no node here")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "c.js")
            with open(p, "w") as f:
                f.write(engine() + "\nvar I=JSON.parse(require('fs').readFileSync(0,'utf8'));"
                        "process.stdout.write(JSON.stringify(I.map(function(c){return dashE.cora(c[0],c[1],c[2])})));\n")
            got = json.loads(subprocess.run(["node", p], input=json.dumps(cases), capture_output=True, text=True, timeout=60).stdout)
        for c, j in zip(cases, got):
            self.assertEqual(strip(j), strip(R.cora_of(*c)), c)
        self.assertEqual(R.cora_of([100, 100, 100], [100, 100, 100], [18, 29, 4])["rating"], "Very High")
        self.assertEqual(R.cora_of([200, 400, 80], [200, 400, 80], [0, 19, 3])["rating"], "Low")
        self.assertEqual(R.cora_of([200, 400, 80], [200, 400, 80], [0, 20, 3])["rating"], "Moderate")
        self.assertEqual(R.cora_of([200, 400, 80], [200, 400, 80], [0, 20, 3])["needs"], "Low needs: CAT II under 5%")


class Engine(unittest.TestCase):
    def dash(self):
        open_ = R.dash_fact(["col", "pf", "h", "cat", "n"],
                            [("c1", "pf1", "a1", "k1", 2), ("c1", "pf1", "b2", "k2", 3), ("c1", "", "", "k2", 1),   # a residual
                             ("c2", "pf2", "w9", "k3", 4)], ["h"])
        trend = R.dash_fact(["w", "col", "pf", "cat", "n"], [(0, "c1", "pf1", "k1", 1), (1, "c1", "pf1", "k1", 2), (1, "c2", "pf2", "k3", 4)])
        return {"weights": {"k1": 10, "k2": 4, "k3": 1}, "facts": {"open": open_, "trend": trend},
                "panels": [R.dash_tiles([{"t": "sum", "label": "CAT I", "fact": "open", "col": "n", "where": {"cat": "k1"}, "cat": 1, "status": "any"},
                                         {"t": "sum", "label": "All", "fact": "open", "col": "n"}]),
                           R.dash_bars("By", "open", "col", [["k1", "CAT I", 1], ["k2", "CAT II", 2], ["k3", "CAT III", 3]],
                                       [{"k": "c1", "name": "One"}, {"k": "c2", "name": "Two"}]),
                           R.dash_series("Weeks", "trend", ["a", "b", "c"], [True, True, False],
                                         [{"name": "All", "where": {}}, {"name": "CAT I", "where": {"cat": "k1"}, "cat": 1}])]}

    def test_totals_and_residual(self):
        v = R.dash_view(self.dash())
        self.assertEqual([t["v"] for t in v[0]["tiles"]], [2, 10])
        self.assertEqual(v[0]["tiles"][0]["status"], "critical")
        self.assertEqual([(g["i"], g["v"]) for g in v[1]["groups"]], [(0, [2, 4, 0]), (1, [0, 0, 4])])
        # a host's name: the residual (no host) is not a host's
        v = R.dash_view(self.dash(), {"h": "B2"})
        self.assertEqual(v[0]["tiles"][1]["v"], 3)
        self.assertEqual(v[2]["notby"], ["h"])                 # the weeks keep no host: said

    def test_a_cat_chosen(self):
        v = R.dash_view(self.dash(), {"cat": "k3"})
        self.assertTrue(v[0]["tiles"][0]["off"])               # the CAT I tile does not apply
        self.assertEqual(v[0]["tiles"][1]["v"], 4)
        self.assertEqual([ln["j"] for ln in v[2]["lines"]], [0])   # the CAT I line left out
        self.assertEqual(v[2]["lines"][0]["vals"], [None, 4, None])  # no row of CAT III that week, and a week without a run: gaps
        v = R.dash_view(self.dash(), {"cat": "k1"})
        self.assertEqual([ln["j"] for ln in v[2]["lines"]], [0])   # the CAT I line is the line itself: once
        self.assertEqual(v[2]["ticks"], [0, 1, 2, 3])

    def test_fact_dict(self):
        f = R.dash_fact(["h", "n"], [("x", 1), ("y", 2), ("x", 3)], ["h"])
        self.assertEqual(f["dict"]["h"], ["x", "y"])
        self.assertEqual([r[0] for r in f["rows"]], [0, 1, 0])


class Scorecard(unittest.TestCase):
    def test_the_dashboard_says_the_reports_numbers(self):
        doc = fixture_dash("stig_scorecard_cora_web")
        dash = doc["dashboard"]
        tiles = dict((t["label"], t["value"]) for t in doc["summary"])
        ti, bi = pi(dash, "tiles"), pi(dash, "bars")
        tv = dict((t["label"], v) for t, v in zip(dash["panels"][ti]["tiles"], dash["panels"][ti]["view"]["tiles"]))
        self.assertEqual(tv["CAT I open"]["v"], tiles["CAT I open"])
        self.assertEqual(tv["CAT II open"]["v"], tiles["CAT II open"])
        self.assertEqual(tv["CAT III open"]["v"], tiles["CAT III open"])
        self.assertEqual((tv["This week"]["v"], tv["This week"]["v2"]), (tiles["Closed this week"], tiles["Opened this week"]))
        self.assertEqual("%s %.1f%%" % (tv["CORA risk"]["cora"]["rating"], tv["CORA risk"]["cora"]["pct"]), tiles["CORA risk"])
        # By collection: each collection's CAT I, II, III, as its row
        sec = [s for s in doc["sections"] if s["title"].startswith("By collection")][0]
        at = sec["columns"].index("CAT I open")
        rows = dict((r[sec["columns"].index("Collection")], r[at:at + 3]) for r in sec["rows"])
        bars = [p for p in dash["panels"] if p["t"] == "bars"][0]
        for g in bars["view"]["groups"]:
            self.assertEqual(g["v"], rows[bars["groups"][g["i"]]["name"]])
        # every collection, platform and host total of a filter: the same from the hosts' counts as from the tiles
        for s in sels_of(dash):
            v = R.dash_view(dash, s)
            tl = dash["panels"][ti]["tiles"]
            tot = sum(x.get("v") or 0 for t, x in zip(tl, v[ti]["tiles"]) if t["t"] == "sum" and not x.get("off"))
            self.assertEqual(tot, sum(g["t"] for g in v[bi]["groups"]) if v[bi]["count"] <= 8 else tot, s)
            mi = pi(dash, "meta")
            cnt = [y for x, y in zip(dash["panels"][mi]["items"], v[mi]["items"]) if x.get("t") == "count"][0]
            self.assertEqual(cnt["all"], 13)
            self.assertLessEqual(cnt["v"], 13)
            if s.get("col") and not s.get("pf"):
                self.assertLess(cnt["v"], 13, s)             # a collection: its own assets (the user: "2 of 13 assets")

    def test_links_go_to_rows_that_are_there(self):
        doc = fixture_dash("stig_scorecard_open_web")
        ids = set()
        for s in doc["sections"]:
            for t in s.get("row_tags") or []:
                if t.get("id"):
                    ids.add(t["id"])
        for p in doc["dashboard"]["panels"]:
            for it in (p.get("items") if p["t"] == "top" else []) or []:
                self.assertIn(it["href"][1:], ids, it)
            for g in p.get("groups") or []:
                self.assertIn(g["href"][1:], ids, g)

    def test_all_web_looks_draw_it(self):
        """1.139.0 (step 8): atlas, brief and ops all draw report_dash.html.j2 when r.dashboard and s.glance are set."""
        import test_report_fit as T
        doc = fixture_dash("stig_scorecard_cora_web")
        for look in ("ops", "atlas", "brief"):
            page = T.render(look, doc)
            self.assertIn('<div class="dash" data-dash>', page, look)
            self.assertEqual(page.count('<h2>At a glance</h2>'), 1, look)          # the section stays (the user: never dropped)
            self.assertIn('class="glance"', page, look)                            # its lines under the dashboard
            self.assertNotIn('<div class="tiles', page, look)                      # the tiles are the dashboard's
            self.assertNotIn('class="todo"', page, look)                           # no Start here beside it
            h2 = page.index('<h2>At a glance</h2>')
            hdr = page[page.rindex('<details', 0, h2):h2 + 120]
            self.assertNotIn('class="stc', hdr, look)
            self.assertNotIn("<script>", page.split('class="dash-data">', 1)[1].split("</script>", 1)[0], look)


def scorecard(runs):
    """The scorecard's document over the made-up site read (with the CORA counts) and these earlier runs."""
    import test_stig_cora as tc
    import stig_scorecard_filters as f
    read = tc.read_cora()
    point = f.stig_scorecard_point(read)
    got = f.stig_scorecard_runs(point, runs, [], {"weeks": 12, "job": 5000})
    return f, point, f.stig_scorecard(read, point, got["runs"], {"weeks": 12, "top": 5})


class Numbers(unittest.TestCase):
    """1.71.0b (review): a platform at 0 in a week it was recorded is a 0, not a gap; the CORA tile's change compares the
    collections both runs have the counts of (a new collection is in the score, not in the change)."""

    def same_both(self, dash, sels):
        got = js_views(dash, sels)
        if got is None:
            return
        for s, j in zip(sels, got):
            self.assertEqual(strip(j), strip(json.loads(json.dumps(R.dash_view(dash, s)))), "selection %s" % s)

    def test_a_platform_going_to_zero_is_a_zero(self):
        import copy
        import test_stig_scorecard as t
        f, point, _ = scorecard([])
        now = dict((c["id"], c) for c in point["collections"])
        app = [c for c in point["collections"] if "Applications" in (c.get("p") or {})][0]
        self.assertEqual([app["p"]["Applications"]["cat%d" % k] for k in (1, 2, 3)], [0, 0, 0])   # none open now
        runs = t.earlier()
        for run in runs:                              # the earlier weeks: the platforms recorded, Applications with 2 CAT II
            for c in run["collections"]:
                c["p"] = copy.deepcopy(now[c["id"]].get("p") or {})
                if "Applications" in c["p"]:
                    c["p"]["Applications"]["cat2"] = 2
                    c["cat2"] = _n2(c.get("cat2")) + 2
        runs[0]["collections"][0].pop("p")            # one week without the platforms: a gap
        f, point, doc = scorecard(runs)
        dash = doc["dashboard"]
        si, key = pi(dash, "series"), f._key("Applications", None)
        for sel in ({"pf": key}, {"pf": key, "cat": "k2"}):
            vals = R.dash_view(dash, sel)[si]["lines"][0]["vals"]
            self.assertEqual(vals[-1], 0, sel)                       # this week: a measured 0 (was a gap before 1.71.0b)
            self.assertIn(2, vals, sel)
            self.assertIn(None, vals, sel)                           # the weeks with no platform counts stay gaps
            self.assertEqual(set(v for v in vals if v is not None), {0, 2}, sel)
        self.same_both(dash, [{"pf": key}, {"pf": key, "cat": "k2"}, {"pf": key, "cat": "k1"}, {}])

    def test_a_new_collection_is_not_in_the_change(self):
        import test_stig_scorecard as t
        f, point, _ = scorecard([])
        now = dict((c["id"], c) for c in point["collections"])
        runs = t.earlier()
        vm = [c["id"] for c in point["collections"] if "VMware" in c["name"]][0]
        for run in runs:                              # the same counts as now, for the collections the runs had; no VMware
            run["collections"] = [c for c in run["collections"] if c["id"] != vm]
            for c in run["collections"]:
                x = now[c["id"]]
                c["ra"], c["rd"] = list(x["ra"]), list(x["rd"])
                for k in ("cat1", "cat2", "cat3"):
                    c[k] = x[k]
        olds = set(c["id"] for run in runs for c in run["collections"])
        self.assertNotIn(vm, olds)
        f, point, doc = scorecard(runs)
        self.assertRegex(t.tile(doc, "CORA risk")["note"], r"^same as ")             # the email's tile: unchanged
        dash = doc["dashboard"]
        ti = pi(dash, "tiles")
        cora = dash["panels"][ti]["view"]["tiles"][0]
        self.assertEqual(cora["d"], 0)                                               # not the new collection's pull
        whole = R.cora_of(*[[sum(c[k][i] for c in point["collections"]) for i in range(3)] for k in ("ra", "rd")],
                          [sum(c["cat%d" % k] for c in point["collections"]) for k in (1, 2, 3)])
        self.assertEqual(cora["cora"]["p10"], whole["p10"])                           # the score: every collection
        for c in point["collections"]:                                               # each collection: its own change
            v = R.dash_view(dash, {"col": "c%s" % c["id"]})[ti]["tiles"][0]
            self.assertEqual("d" in v, c["id"] in olds, c["name"])
        self.same_both(dash, [{}] + [{"col": "c%s" % c["id"]} for c in point["collections"]] + [{"cat": "k1"}])


def _n2(x):
    try:
        return int(x or 0)
    except (TypeError, ValueError):
        return 0


class Page(unittest.TestCase):
    """Headless Chrome: the page's filters draw the panels again."""
    def test_filters_draw_the_panels(self):
        import test_report_fit as T
        b = T._browser()
        if not b:
            self.skipTest("no headless browser here")
        doc = fixture_dash("stig_scorecard_cora_web")
        dash = doc["dashboard"]
        col = [g for g in [p for p in dash["panels"] if p["t"] == "bars"][0]["groups"]][1]
        want = {"none": R.dash_view(dash, {}), "col": R.dash_view(dash, {"col": col["k"]}), "cat": R.dash_view(dash, {"cat": "k2"}),
                "h": R.dash_view(dash, {"h": "win"})}
        h = r"""<!doctype html><html><body><pre id="out"></pre><script>
var f=document.createElement('iframe');f.style.cssText='width:1280px;height:900px;border:0';
function tiles(d){return [].map.call(d.querySelectorAll('.dtiles .dt .dbig'),function(e){return e.textContent.trim()})}
function bars(d){return [].map.call(d.querySelectorAll('.dp-bars .dbr'),function(e){return e.querySelector('.dbn').textContent+'|'+e.querySelector('.dbv').textContent})}
function topr(d){return [].map.call(d.querySelectorAll('.dp-top .dtl tbody tr'),function(e){return e.textContent.trim().replace(/\s+/g,' ')})}
function nb(d){return [].map.call(d.querySelectorAll('.dnb:not([hidden])'),function(e){return e.textContent})}
function grab(d){return {meta:d.querySelector('.dmeta').textContent,tiles:tiles(d),bars:bars(d),top:topr(d),nb:nb(d),rows:d.querySelectorAll('#s3 tbody tr:not(.xr):not([hidden])').length}}
window.onerror=function(m,u,l){document.getElementById('out').textContent=JSON.stringify({err:m+' '+l})};
f.onload=function(){try{var d=f.contentDocument,w=f.contentWindow,R={},sel=function(k,v){var s=d.querySelector('.dfx [data-fk="'+k+'"]');s.value=v;
  s.dispatchEvent(new w.Event(s.tagName==='INPUT'?'input':'change',{bubbles:true}))};
  setTimeout(function(){R.none=grab(d);sel('col','%(col)s');
  setTimeout(function(){R.col=grab(d);sel('col','');sel('cat','k2');
  setTimeout(function(){R.cat=grab(d);sel('cat','');sel('h','win');
  setTimeout(function(){R.h=grab(d);R.facets=!d.querySelector('.dfx').hidden;document.getElementById('out').textContent=JSON.stringify(R)},700)},300)},300)},300)}catch(e){document.getElementById('out').textContent=JSON.stringify({err:String(e)})}};
f.src='page.html';document.body.appendChild(f);
</script></body></html>""" % {"col": col["k"]}
        with tempfile.TemporaryDirectory() as d:
            d = os.environ.get("DASH_KEEP") or d
            with open(os.path.join(d, "page.html"), "w") as o:
                o.write(T.render("ops", doc))
            with open(os.path.join(d, "h.html"), "w") as o:
                o.write(h)
            got, err = T._run(b, os.path.join(d, "h.html"), 1280, 120)
        self.assertTrue(got, err)
        self.assertNotIn("err", got, got.get("err"))
        self.assertTrue(got["facets"], "the filter row is shown by the script")
        ti = pi(dash, "tiles")
        tl = dash["panels"][ti]["tiles"]
        for k in ("none", "col", "cat", "h"):
            v = want[k][ti]["tiles"]
            exp = []
            for t, x in zip(tl, v):
                if t["t"] == "cora":
                    exp.append(x["cora"]["rating"] if x.get("cora") else "-")
                elif t["t"] == "fixed":
                    exp.append(str(t["value"]))
                else:
                    exp.append("-" if x.get("off") else str(x["v"]))
            self.assertEqual(got[k]["tiles"], exp, k)
            bv = want[k][pi(dash, "bars")]
            bp = [p for p in dash["panels"] if p["t"] == "bars"][0]
            self.assertEqual(got[k]["bars"], ["%s|%s" % (bp["groups"][g["i"]]["name"], u" · ".join(str(x) for x in g["v"])) for g in bv["groups"]], k)
            tp = [i for i, p in enumerate(dash["panels"]) if p["t"] == "top"]
            self.assertEqual(len(got[k]["top"]), sum(len(want[k][i]["rows"]) or 1 for i in tp), k)
        self.assertTrue(any("host" in x for x in got["h"]["nb"]), got["h"]["nb"])      # the weeks: not by host, said
        self.assertIn("13 assets", got["none"]["meta"])
        self.assertRegex(got["col"]["meta"], r"(^|\D)\d+ of 13 assets")                     # the user: the count follows the filters
        self.assertTrue(any("CORA counts every CAT" in x for x in got["cat"]["nb"]), got["cat"]["nb"])


if __name__ == "__main__":
    unittest.main()
