#!/usr/bin/env python3
"""1.101.0: the AD report's replication map - plugins/filter/ad_map_filters.py (AD sites matched to network-map sites, the
replication between and inside sites, the sections, the page piece) and site_ad_swis.netmap_place (the network map's own
read and model, through tests/netmap/fake_swis.py). Placeholders only.
  python tests/test_ad_map.py"""
import datetime
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "plugins", "module_utils"), os.path.join(ROOT, "plugins", "filter")]
import ad_map_filters as am  # noqa: E402
import report_page_filters as rp  # noqa: E402
import site_ad_swis as ad  # noqa: E402
import site_swis as sw  # noqa: E402

NOW = datetime.datetime(2026, 10, 15, 9, 30)


def iso(h):
    return (NOW - datetime.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")


def repl():
    dcs = [{"name": "DC01", "site": "HQ"}, {"name": "DC02", "site": "HQ"}, {"name": "DC03", "site": "HQ"},
           {"name": "BR1-DC1", "site": "Branch-East"}, {"name": "SITC-DC1", "site": "SITC-0301"}, {"name": "S5-DC1", "site": "site0501"},
           {"name": "LAB-DC1", "site": "Lab-Annex"}]
    links = [{"frm": "DC01", "to": "BR1-DC1", "nc": "corp.test", "last_success": iso(0.3), "failures": 0},
             {"frm": "BR1-DC1", "to": "DC01", "nc": "Configuration", "last_success": iso(9), "failures": 0},
             {"frm": "DC01", "to": "SITC-DC1", "nc": "corp.test", "last_success": iso(31), "failures": 3, "error": "8524", "last_failure": iso(.2)},
             {"frm": "DC02", "to": "S5-DC1", "nc": "corp.test", "last_success": iso(4.5), "failures": 0},
             {"frm": "DC01", "to": "LAB-DC1", "nc": "corp.test", "last_success": "", "failures": 0},
             {"frm": "DC01", "to": "DC02", "nc": "corp.test", "last_success": iso(.1), "failures": 0},
             {"frm": "DC02", "to": "DC03", "nc": "corp.test", "last_success": iso(7), "failures": 0}]
    return {"dcs": dcs, "links": links, "now": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")}


def swis(ok=True):
    return {"dcs": [{"dc": "DC01", "node_id": 1}, {"dc": "DC02", "node_id": 2}, {"dc": "BR1-DC1", "node_id": 3}, {"dc": "S5-DC1", "node_id": 9}],
            "netmap": {"ok": ok, "source": "db-snapshot", "names": ["HUBA0001", "SITB0201", "SITC0301", "SITE0501", "SITE-0501", "Not placed"],
                       "places": {"HUBA0001": [38.9, -77.0], "SITB0201": [40.7, -74.0], "SITC0301": [41.9, -87.6]},
                       "node_site": {"1": "HUBA0001", "2": "HUBA0001", "3": "SITB0201", "9": "Not placed"}, "no_site": "Not placed",
                       "wan": [{"a": "HUBA0001", "b": "SITB0201", "n": 2, "bps": 2e8, "util": 77, "status": "Up", "ifs": ["x"]}]}}


O = {"low_hours": 4, "warn_hours": 6, "crit_hours": 24}


class Map1101(unittest.TestCase):
    def setUp(self):
        self.v = am.ad_repl_map(repl(), swis(), O)
        self.d = self.v["extra"]["data"]
        self.s = dict((x["n"], x) for x in self.d["sites"])

    def test_1_matched_dcs_nodes_name_code_none(self):
        self.assertEqual((self.s["HQ"]["nm"], self.s["HQ"]["by"]), ("HUBA0001", "the DCs' nodes"))
        self.assertIn("2 of 3 DCs are SolarWinds nodes in HUBA0001", self.s["HQ"]["why"])
        self.assertEqual((self.s["Branch-East"]["nm"], self.s["Branch-East"]["by"]), ("SITB0201", "the DCs' nodes"))
        self.assertEqual((self.s["SITC-0301"]["nm"], self.s["SITC-0301"]["by"]), ("SITC0301", "code"))
        self.assertEqual((self.s["site0501"]["nm"], self.s["site0501"]["by"]), ("SITE0501", "name"))   # two sites share its code: the name
        self.assertEqual((self.s["Lab-Annex"]["nm"], self.s["Lab-Annex"]["by"]), ("", "none"))
        self.assertIn("set ad_site_map", self.s["Lab-Annex"]["why"])
        self.assertEqual(self.s["HQ"]["p"], [38.9, -77.0])
        v = am.ad_repl_map(repl(), swis(), dict(O, ad_site_map={"lab-annex": "SITB0201", "HQ": "SITC0301"}))
        s = dict((x["n"], x) for x in v["extra"]["data"]["sites"])
        self.assertEqual((s["Lab-Annex"]["nm"], s["Lab-Annex"]["by"]), ("SITB0201", "ad_site_map"))
        self.assertEqual(s["HQ"]["nm"], "SITC0301")                                   # set by hand beats the nodes

    def test_2_between_and_inside(self):
        ln = dict(((x["a"], x["b"]), x) for x in self.d["links"])
        be = ln[("Branch-East", "HQ")]
        self.assertEqual((be["h"], be["cls"], sorted(be["parts"])), (9.0, "warning", ["Configuration", "corp.test"]))   # its worst
        self.assertEqual(be["wan"]["bps"], 2e8)                                       # the WAN between their network-map sites
        self.assertEqual(ln[("HQ", "SITC-0301")]["cls"], "critical")
        self.assertEqual(ln[("HQ", "SITC-0301")]["err"], "8524")
        self.assertEqual(ln[("HQ", "site0501")]["cls"], "low")
        self.assertIsNone(ln[("HQ", "Lab-Annex")]["h"])                               # never: no last success
        self.assertEqual(ln[("HQ", "Lab-Annex")]["cls"], "critical")
        self.assertIsNone(ln[("HQ", "SITC-0301")]["wan"])                             # no WAN link between them in SolarWinds
        inside = dict(((x["a"], x["b"]), x["cls"]) for x in self.d["inside"]["HQ"])
        self.assertEqual(inside, {("DC01", "DC02"): "ok", ("DC02", "DC03"): "warning"})

    def test_3_sections_shown_not_counted(self):
        sec = dict((x["title"], x) for x in self.v["sections"])
        self.assertEqual(sec["Replication map"]["text"], am.MARK)
        self.assertFalse(sec["Replication map"]["counted"])
        self.assertFalse(sec["How AD sites were matched"]["counted"])
        self.assertEqual(sec["How AD sites were matched"]["rows"][0][:3], ["Branch-East", "SITB0201", "the DCs' nodes"])
        self.assertEqual([x["title"] for x in am.ad_map_sections(self.v["sections"], False)], ["How AD sites were matched"])
        self.assertIn("1 not (set ad_site_map)", self.v["lines"][0])

    def test_4_no_netmap_read(self):
        v = am.ad_repl_map(repl(), swis(ok=False), O)
        self.assertTrue(all(x["p"] is None for x in v["extra"]["data"]["sites"]))  # a ring: nothing placed
        self.assertIn("Not placed", [x for x in v["sections"] if x["title"] == "Replication map"][0]["note"])
        v = am.ad_repl_map(repl(), {}, O)                                             # no From SolarWinds at all
        self.assertEqual(len(v["sections"]), 2)

    def test_5_never_raises(self):
        v = am.ad_repl_map({"dcs": [{"name": "X", "site": "S"}], "links": "not a list", "now": "x"}, {"netmap": "bad"}, O)
        self.assertEqual(v["sections"], [])
        self.assertIn("not made", v["lines"][0])

    def test_6_the_page_piece(self):
        p = rp.report_page_piece(self.v["extra"], ROOT)
        self.assertEqual(p["marker"], am.MARK)
        self.assertIn('id="admap-data"', p["html"])
        self.assertIn("NM_LAND", p["script"])
        self.assertIn("amTest", p["script"])
        page = "<html><head></head><body><p>%s</p></body></html>" % am.MARK
        out = rp.report_page_extend(page, p)
        self.assertIn('class="am"', out)
        self.assertNotIn(am.MARK, out)
        self.assertNotIn("<script>", self.v["extra"]["data"].__repr__())

    def test_7_the_script_and_markup_parse_and_stay_csp_safe(self):
        web = os.path.join(ROOT, "roles", "ad_report", "files", "web")
        t = open(os.path.join(web, "admap.html")).read()
        self.assertNotIn(" style=", t)
        self.assertNotIn("<script", t)
        js = open(os.path.join(web, "admap.js")).read()
        for bad in (".innerHTML", "eval(", "new Function", "insertAdjacentHTML", "document.write"):
            self.assertNotIn(bad, js)
        if shutil.which("node"):
            subprocess.check_call(["node", "--check", os.path.join(web, "admap.js")])


    def test_8_code_before_name_and_two_domains_dc01(self):
        sw2 = swis()
        sw2["netmap"]["names"] = ["SITC0301", "SITD0401"]
        v = am.ad_repl_map({"dcs": [{"name": "X1", "site": "SITC0301"}, {"name": "X2", "site": "sitd-0401"}], "links": [], "now": "2026-10-15T09:30:00Z"}, sw2, O)
        s = dict((x["n"], x) for x in v["extra"]["data"]["sites"])
        self.assertEqual((s["SITC0301"]["nm"], s["SITC0301"]["by"]), ("SITC0301", "code"))   # the site code first ("we use site codes")
        self.assertEqual((s["sitd-0401"]["nm"], s["sitd-0401"]["by"]), ("SITD0401", "code"))
        # the review's case: two domains' DC01 - SolarWinds has both, the replication data one DC01 (short names only)
        sw3 = {"dcs": [{"dc": "DC01", "domain": "alpha.test", "node_id": 1}, {"dc": "DC01", "domain": "beta.test", "node_id": 2}],
               "netmap": {"ok": True, "names": ["SITA0101", "SITB0201"], "places": {}, "node_site": {"1": "SITA0101", "2": "SITB0201"}, "wan": []}}
        v = am.ad_repl_map({"dcs": [{"name": "DC01", "site": "Alpha"}], "links": [], "now": "2026-10-15T09:30:00Z"}, sw3, O)
        s = v["extra"]["data"]["sites"][0]
        self.assertNotEqual(s["by"], "the DCs' nodes")                              # never "1 of 1 DC is a node in SITB0201"
        self.assertNotIn("SITB0201", s["why"])
        self.assertEqual((s["nm"], s["by"]), ("", "none"))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Place1101(unittest.TestCase):
    """netmap_place: the network map's own read and model place the DCs' nodes (live, the fake SolarWinds over HTTPS)."""

    def test_1_live(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        crt, key, port = os.path.join(tmp, "c.pem"), os.path.join(tmp, "k.pem"), free_port()
        subprocess.check_call(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", crt, "-days", "2",
                               "-subj", "/CN=127.0.0.1"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        fx = os.path.join(ROOT, "tests", "ad", "swis_fixture.json")
        p = subprocess.Popen([sys.executable, os.path.join(ROOT, "tests", "netmap", "fake_swis.py"), str(port), fx, os.path.join(tmp, "log"), crt, key],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(p.kill)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        f = json.load(open(fx))
        c = sw.Client("https://127.0.0.1:%d" % port, f["user"], f["password"], verify=False, timeout=10)
        r = ad.netmap_place({"site_by": "auto", "sites": {"DC*": {"lat": 38.9, "lon": -77.0}}, "max_age": 20}, [{"node_id": 11}, {"node_id": 12}], client=c)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["source"], "swis-live")
        self.assertEqual(set(r["node_site"]), {"11", "12"})
        bad = ad.netmap_place({"site_pattern": "(["}, [], client=c)                  # a setting that cannot work: said, nothing read
        self.assertFalse(bad["ok"])
        self.assertIn("netmap settings", bad["why"])


def _browser():
    for b in ("google-chrome", "chromium", "chromium-browser"):
        if shutil.which(b):
            return shutil.which(b)
    return None


@unittest.skipUnless(_browser(), "no google-chrome or chromium")
class Places1011(unittest.TestCase):
    """1.101.1, in Chromium: co-located AD sites are one location bubble; dots never overlap; a label is never on a dot or
    another label and never out of the drawing; an opened location has nothing else under its disc; one place: the ring."""

    GEOM = r"""
var svg=document.querySelector('.am-svg'),vb=svg.viewBox.baseVal,dots=[].map.call(svg.querySelectorAll('.am-s:not(.dc) > circle'),function(c){
 return [+c.getAttribute('cx'),+c.getAttribute('cy'),+c.getAttribute('r')]});
var gs=[].slice.call(svg.querySelectorAll('.am-s'));var lab=[].map.call(svg.querySelectorAll('.am-s > text:not(.cnt)'),function(t){var b=t.getBBox();return [b.x,b.y,b.x+b.width,b.y+b.height,gs.indexOf(t.parentNode)]});
var over=0,ondot=0,out=0,dd=0;
for(var i=0;i<dots.length;i++)for(var j=i+1;j<dots.length;j++){var a=dots[i],b=dots[j];if(Math.hypot(a[0]-b[0],a[1]-b[1])<a[2]+b[2]-0.5)dd++;}
lab.forEach(function(a,i){if(a[0]<-1||a[2]>vb.width+1||a[1]<-1||a[3]>vb.height+1)out++;
 dots.forEach(function(d){if(a[0]<d[0]+d[2]-1&&a[2]>d[0]-d[2]+1&&a[1]<d[1]+d[2]-1&&a[3]>d[1]-d[2]+1)ondot++;});
 lab.forEach(function(b,j){if(j>i&&b[4]!==a[4]&&a[0]<b[2]-1&&a[2]>b[0]+1&&a[1]<b[3]-1&&a[3]>b[1]+1)over++;});});
r.bubbles=svg.querySelectorAll('.am-s.bub').length;r.dots=dots.length;r.dotsOverlap=dd;r.labelsOnDots=ondot;r.labelsOverlap=over;r.labelsOut=out;
"""

    def page(self, repl, sw_, js, width=1280):
        v = am.ad_repl_map(repl, sw_, O)
        p = rp.report_page_piece(v["extra"], ROOT)
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        with open(os.path.join(d, "a.js"), "w") as fh:
            fh.write(p["script"])
        with open(os.path.join(d, "t.js"), "w") as fh:
            fh.write("window.addEventListener('load',function(){setTimeout(function(){var r={};try{%s}catch(e){r.err=String(e)}"
                     "document.getElementById('res').textContent=JSON.stringify(r)},300)});" % js)
        with open(os.path.join(d, "p.html"), "w") as fh:
            fh.write('<!doctype html><html><head><meta charset="utf-8"><style>%s</style></head><body style="margin:0">%s<pre id="res"></pre>'
                     '<script src="a.js"></script><script src="t.js"></script></body></html>' % (p["css"], p["html"]))
        out = subprocess.run([_browser(), "--headless=new", "--no-sandbox", "--disable-gpu", "--window-size=%d,900" % width, "--virtual-time-budget=5000",
                              "--dump-dom", "file://" + os.path.join(d, "p.html")], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             universal_newlines=True, timeout=120).stdout
        import re
        return json.loads(re.search(r'<pre id="res">(.*?)</pre>', out, re.S).group(1).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))

    def test_1_a_busy_forest_closed(self):
        sys.path.insert(0, os.path.join(ROOT, "tests", "ad"))
        import admap_fixture as fx
        for w in (1280, 800):
            r = self.page(*fx.realistic(), js=self.GEOM, width=w)
            self.assertNotIn("err", r)
            self.assertGreaterEqual(r["bubbles"], 4, r)                      # the 5 shared locations (or fewer, merged)
            self.assertEqual((r["dotsOverlap"], r["labelsOnDots"], r["labelsOverlap"], r["labelsOut"]), (0, 0, 0, 0), (w, r))

    def test_2_an_opened_location_has_nothing_under_its_disc(self):
        import admap_fixture as fx
        js = ("amTest.openLoc();var ring=document.querySelector('.am-ring'),cx=+ring.getAttribute('cx'),cy=+ring.getAttribute('cy'),R=+ring.getAttribute('r');"
              "r.under=[].filter.call(document.querySelectorAll('.am-svg > g:nth-of-type(2) .am-s > circle'),function(c){"
              "return Math.hypot(+c.getAttribute('cx')-cx,+c.getAttribute('cy')-cy)<R+(+c.getAttribute('r'))-1}).length;"
              "r.members=document.querySelectorAll('.am-svg > g:nth-of-type(3) .am-s.many').length;")
        for w in (1280, 800):
            r = self.page(*fx.realistic(), js=js, width=w)
            self.assertNotIn("err", r)
            self.assertEqual(r["under"], 0, (w, r))
            self.assertGreaterEqual(r["members"], 2)

    def test_3_one_place_is_the_ring(self):
        import admap_fixture as fx
        r = self.page(*fx.one_place(), js="r.note=document.querySelector('.am-note').textContent;r.land=document.querySelectorAll('.am-land').length;" + self.GEOM)
        self.assertNotIn("err", r)
        self.assertIn("Every AD site is at HUBA0001", r["note"])
        self.assertEqual(r["land"], 0)                                           # no geography
        self.assertEqual((r["bubbles"], r["dots"], r["dotsOverlap"], r["labelsOverlap"]), (0, 8, 0, 0))


if __name__ == "__main__":
    unittest.main(verbosity=1)
