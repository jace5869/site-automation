#!/usr/bin/env python3
"""1.102.0: the network map's Region view and its layers (WAN traffic, AD replication) - roles/netmap_report/files/web/
netregion.js, maplabel.js (shared with the AD report's map), netglobe.js's one rule for a pair's WAN state (the 1.95.1
review: one link down and the others not read is Down), plugins/module_utils/site_ad_layer.py (the AD report's matching,
shared), the netmap job's filters (netmap_ad_layer, netmap_page_extra), site_report_history by template name, and the
portal's report-database reader (ad_repl_from_db, on SQLite). Placeholders only.
  python tests/test_netmap_region.py"""
import datetime
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "plugins", "module_utils"), os.path.join(ROOT, "plugins", "filter"), os.path.join(ROOT, "tests", "netmap"),
                os.path.join(ROOT, "roles", "site_portal_stack", "files")]
import ad_map_filters as am  # noqa: E402
import netmap_filters as nf  # noqa: E402
import region_fixture as fx  # noqa: E402
import report_page_filters as rp  # noqa: E402
import site_ad_layer as al  # noqa: E402
import site_swis as sw  # noqa: E402

WEB = os.path.join(ROOT, "roles", "netmap_report", "files", "web")
NOW = datetime.datetime(2026, 10, 15, 9, 30)


def iso(h):
    return (NOW - datetime.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")


def repl():
    dcs = [{"name": "DC01", "site": "HQ"}, {"name": "DC02", "site": "HQ"}, {"name": "BR1-DC1", "site": "Branch-East"},
           {"name": "SITC-DC1", "site": "SITC-0301"}, {"name": "S5-DC1", "site": "site0501"}, {"name": "LAB-DC1", "site": "Lab-Annex"}]
    links = [{"frm": "DC01", "to": "BR1-DC1", "nc": "corp.example.mil", "last_success": iso(0.3), "failures": 0},
             {"frm": "BR1-DC1", "to": "DC01", "nc": "Configuration", "last_success": iso(9), "failures": 0},
             {"frm": "DC01", "to": "SITC-DC1", "nc": "corp.example.mil", "last_success": iso(31), "failures": 3, "error": "8524", "last_failure": iso(.2)},
             {"frm": "DC02", "to": "S5-DC1", "nc": "corp.example.mil", "last_success": iso(4.5), "failures": 0},
             {"frm": "DC01", "to": "LAB-DC1", "nc": "corp.example.mil", "last_success": "", "failures": 0},
             {"frm": "DC01", "to": "DC02", "nc": "corp.example.mil", "last_success": iso(.1), "failures": 0}]
    return {"dcs": dcs, "links": links, "now": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")}


NM_NAMES = ["HUBA0001", "SITB0201", "SITC0301", "SITE0501", "Not placed"]
NODES = [{"id": 1, "name": "DC01", "site": "HUBA0001"}, {"id": 2, "name": "DC02.corp.example.mil", "site": "HUBA0001"},
         {"id": 3, "name": "BR1-DC1", "site": "SITB0201"}, {"id": 9, "name": "S5-DC1", "site": "Not placed"}]
O = {"low_hours": 4, "warn_hours": 6, "crit_hours": 24}


class Layer(unittest.TestCase):
    def page(self):
        return {"sites": NM_NAMES, "nodes": NODES, "links": [], "geo": {}}

    def test_1_the_same_matching_as_the_ad_report(self):
        sw_ = {"dcs": [{"dc": "DC01", "node_id": 1}, {"dc": "DC02", "node_id": 2}, {"dc": "BR1-DC1", "node_id": 3}, {"dc": "S5-DC1", "node_id": 9}],
               "netmap": {"ok": True, "names": NM_NAMES, "places": {}, "no_site": "Not placed",
                          "node_site": dict((str(n["id"]), n["site"]) for n in NODES)}}
        adr = dict((x["n"], (x["nm"], x["by"])) for x in am.ad_repl_map(repl(), sw_, O)["extra"]["data"]["sites"])
        lay = al.layer(am.ad_layer_artifact(repl(), sw_, O), self.page())
        mine = dict((x["n"], (x["nm"], x["by"])) for x in lay["sites"])
        self.assertEqual(adr, mine)
        self.assertEqual(mine["HQ"], ("HUBA0001", "the DCs' nodes"))
        self.assertEqual(mine["SITC-0301"], ("SITC0301", "code"))
        self.assertEqual(mine["Lab-Annex"], ("", "none"))
        self.assertEqual(lay["none"], 1)

    def test_2_dc_nodes_by_name_without_the_ad_jobs_ids(self):
        lay = al.layer(al.compact(repl(), O), self.page())                 # no SolarWinds ids: the map's nodes by short name
        s = dict((x["n"], x) for x in lay["sites"])
        self.assertEqual((s["HQ"]["nm"], s["HQ"]["by"]), ("HUBA0001", "the DCs' nodes"))   # DC02.corp.example.mil is DC02
        self.assertEqual(s["Branch-East"]["nm"], "SITB0201")
        self.assertEqual(s["site0501"]["by"], "code")                        # its DC is "Not placed": the code then
        lay2 = al.layer(al.compact(repl(), O), self.page(), site_map={"lab-annex": "SITE0501"})
        self.assertEqual(dict((x["n"], x["by"]) for x in lay2["sites"])["Lab-Annex"], "ad_site_map")

    def test_3_pairs_and_the_scale(self):
        c = al.compact(repl(), O)
        p = dict(((x["a"], x["b"]), x) for x in c["pairs"])
        self.assertEqual(p[("HQ", "SITC-0301")]["cls"], "critical")
        self.assertTrue(p[("HQ", "SITC-0301")]["fail"])
        self.assertEqual(p[("Branch-East", "HQ")]["cls"], "warning")         # the worse of the two ways (9 h)
        self.assertEqual(p[("HQ", "site0501")]["cls"], "low")
        self.assertIsNone(p[("HQ", "Lab-Annex")]["h"])                     # never: critical
        self.assertEqual(c["pairs"][0]["cls"], "critical")                  # worst first
        self.assertEqual(c["scale"], {"low": 4.0, "warn": 6.0, "crit": 24.0})
        self.assertNotIn(("HQ", "HQ"), p)                                   # DC to DC inside a site: not kept

    def test_4_from_the_report_databases_rows(self):
        r = repl()
        dcs = [(d["name"], d["site"]) for d in r["dcs"]]
        links = [(x["frm"], x["to"], x["nc"], al.utc(x["last_success"]), x["failures"], x.get("error", ""), al.utc(x.get("last_failure"))) for x in r["links"]]
        a, b = al.from_rows(NOW, dcs, links, O), al.compact(r, O)
        self.assertEqual(a["pairs"], b["pairs"])
        self.assertEqual(a["dcs"], b["dcs"])

    def test_5_never_raises(self):
        self.assertTrue(al.layer(None, {})["why"])
        self.assertEqual(al.layer({"dcs": {}}, {}, why="x")["why"], "x")
        self.assertIn("could not be made", al.layer({"dcs": {"A": "notalist"}, "pairs": [None]}, {"nodes": [1]})["why"])

    def test_6_the_job_reads_the_ad_jobs_artifact(self):
        res = {"data": {"sites": NM_NAMES, "nodes": NODES, "links": []}, "map": {"no_site": "Not placed"}}
        lay = nf.netmap_ad_layer(res, [], {"template": "Active Directory health report", "why": "not run from AAP (no awx_job_id)"})
        self.assertIn("not run from AAP", lay["why"])
        old = [{"id": 7, "status": "successful", "artifacts": {"ad_repl": {"sites": {"HQ": 1.0}}}}]
        self.assertIn("has not run since 1.102.0", nf.netmap_ad_layer(res, old, {"template": "AD"})["why"])
        new = old + [{"id": 9, "status": "successful", "artifacts": {"ad_repl": {"sites": {}, "layer": al.compact(repl(), O)}}}]
        lay = nf.netmap_ad_layer(res, new, {"template": "AD", "tz": "Europe/Berlin"})
        self.assertEqual(lay["why"], "")
        self.assertEqual(lay["job"], 9)
        self.assertEqual(lay["as_of"], "2026-10-15 11:30 CEST")
        self.assertTrue(nf.netmap_ad_lines(lay)[0].startswith("AD layer: 5 AD site(s), 4 matched"), nf.netmap_ad_lines(lay))
        self.assertEqual(nf.netmap_ad_layer(res, new, {"enabled": False})["why"], "netmap_ad_layer is false")
        ex = nf.netmap_page_extra({"data": res["data"], "reachable": True}, "auto", lay)
        self.assertEqual(ex["data"]["ad"]["job"], 9)
        self.assertNotIn("ad", res["data"])                                 # the job's own data untouched
        self.assertNotIn("ad", nf.netmap_page_extra({"data": res["data"], "reachable": True})["data"])

    def test_7_region_is_the_first_look(self):
        m = {"sites": [], "nodes": [], "links": []}
        self.assertEqual(sw.page_data(m)["view"], "region")
        self.assertEqual(sw.page_data(m, view="globe")["view"], "globe")
        self.assertEqual(sw.page_data(m, view="sideways")["view"], "region")


class History(unittest.TestCase):
    """site_report_history by template name (the network map reads the AD job's runs)."""

    def test_by_name(self):
        import importlib.util
        try:
            import ansible.module_utils  # noqa: F401
        except ImportError as e:                                           # ansible not importable here
            self.skipTest(str(e))
        spec = importlib.util.spec_from_file_location("ansible.module_utils.site_aap", os.path.join(ROOT, "plugins", "module_utils", "site_aap.py"))
        aap = importlib.util.module_from_spec(spec)
        sys.modules["ansible.module_utils.site_aap"] = aap
        spec.loader.exec_module(aap)
        sys.path.insert(0, os.path.join(ROOT, "roles", "site_email", "library"))
        import site_report_history as h
        calls = []

        class Api(object):
            user = "audit"

            def probe(self):
                return ""

            def call(self, method, path):
                calls.append(path)
                if path.startswith("job_templates/?"):
                    ids = {"Active%20Directory%20health%20report": [77], "Twice": [77, 78]}.get(path.split("name=")[1].split("&")[0], [])
                    return 200, {"results": [{"id": i} for i in ids]}
                if path.startswith("jobs/?"):
                    return 200, {"results": [{"id": 41, "status": "error", "finished": "x"}] if "job_template=77&" in path else []}
                return 200, {"artifacts": {}}
        runs, why = h.history(Api(), 50, 3, 5, ["ad_repl"], "Active Directory health report")
        self.assertEqual(why, "")
        self.assertEqual((runs[0]["id"], runs[0]["status"]), (41, "error"))   # 1.102.0 review: an error run is read too
        self.assertIn("job_template=77&id__lt=50&status__in=successful,failed,error,canceled", calls[1])
        runs, why = h.history(Api(), 50, 3, 5, ["ad_repl"], "Some other name")
        self.assertEqual(runs, [])
        self.assertIn('no job template "Some other name"', why)
        runs, why = h.history(Api(), 50, 3, 5, ["ad_repl"], "Twice")         # one name in two organizations: refused
        self.assertEqual(runs, [])
        self.assertIn('2 job templates are named "Twice" (IDs 77, 78)', why)
        calls[:] = []
        runs, why = h.history(Api(), 50, 3, 5, ["ad_repl"], "77")            # its ID: that one
        self.assertEqual((why, calls[0].startswith("jobs/?job_template=77&")), ("", True))


class PortalReader(unittest.TestCase):
    """The portal's reader against 1.101.2's tables (the columns site_ad_db.py makes), on SQLite."""
    DDL = ["CREATE TABLE ad_run (run_id INTEGER PRIMARY KEY, read_at TEXT NOT NULL, job INT, template_id INT, status TEXT, critical INT, warning INT, dcs INT, dcs_read INT, live TEXT)",
           "CREATE TABLE ad_dc (run_id INT NOT NULL, dc TEXT NOT NULL, domain TEXT NOT NULL DEFAULT '', site TEXT NOT NULL DEFAULT '', is_read INT, status TEXT, problems INT, worst TEXT)",
           "CREATE TABLE ad_link (run_id INT NOT NULL, frm TEXT NOT NULL, to_dc TEXT NOT NULL, nc TEXT NOT NULL DEFAULT '', last_success TEXT NULL, failures INT NOT NULL DEFAULT 0, "
           "error TEXT NOT NULL DEFAULT '', last_failure TEXT NULL, status TEXT NOT NULL DEFAULT '')"]

    def setUp(self):
        try:
            import site_portal as sp
        except Exception as e:  # noqa: BLE001
            self.skipTest("site_portal not importable here: %s" % e)
        self.sp = sp
        self.db = os.path.join(tempfile.mkdtemp(), "r.db")
        self.addCleanup(shutil.rmtree, os.path.dirname(self.db))

    def connect(self):
        return sqlite3.connect(self.db)

    def test_1_missing_tables(self):
        c, why = self.sp.ad_repl_from_db(self.connect, mark="?")
        self.assertIsNone(c)
        self.assertEqual(why, "AD replication not in the report database yet")

    def test_2_the_newest_run_every_link(self):
        con = self.connect()
        for d in self.DDL:
            con.execute(d)
        con.execute("INSERT INTO ad_run (run_id, read_at, template_id) VALUES (1, '2026-10-14 09:30:00', 5), (2, '2026-10-15 09:30:00', 5)")
        r = repl()
        for rid in (1, 2):
            con.executemany("INSERT INTO ad_dc (run_id, dc, site) VALUES (?, ?, ?)", [(rid, d["name"], d["site"]) for d in r["dcs"]])
            con.executemany("INSERT INTO ad_link (run_id, frm, to_dc, nc, last_success, failures, error, last_failure, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            [(rid, x["frm"], x["to"], x["nc"], (x["last_success"] or None) and x["last_success"].replace("T", " ").rstrip("Z"), x["failures"],
                              x.get("error", ""), None, "ok") for x in r["links"]])
        con.commit()
        con.close()
        c, why = self.sp.ad_repl_from_db(self.connect, mark="?", opts=O)
        self.assertEqual(why, "")
        self.assertEqual(c["read_at"], "2026-10-15T09:30:00Z")
        self.assertEqual(c["pairs"], al.compact(r, O)["pairs"])           # every link (an ok one too), the newest run's ages

    def test_3_empty_and_broken(self):
        con = self.connect()
        for d in self.DDL:
            con.execute(d)
        con.commit()
        self.assertIn("no AD run kept there", self.sp.ad_repl_from_db(self.connect, mark="?")[1])

        def boom():
            raise OSError("refused")
        c, why = self.sp.ad_repl_from_db(boom)
        self.assertIsNone(c)
        self.assertEqual(why, self.sp.AD_NOT_READ)                          # fixed text to the browser (review P2)

    def test_4_no_login_on_the_portal(self):
        class Cfg(object):
            pass
        conn, why = self.sp.ad_db_connect(Cfg())
        self.assertIsNone(conn)
        self.assertTrue(why.startswith("AD replication not in the report database yet"))


class Files(unittest.TestCase):
    def test_no_html_from_strings_no_eval_and_it_parses(self):
        for n in ("netregion.js", "maplabel.js"):
            t = open(os.path.join(WEB, n)).read()
            for bad in (".innerHTML", ".outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "DOMParser", ".setAttribute('style'", "cssText"):
                self.assertNotIn(bad, t, (n, bad))
            if shutil.which("node"):
                subprocess.check_call([shutil.which("node"), "--check", os.path.join(WEB, n)])

    def test_everywhere_the_map_is(self):
        piece = rp.PIECES["netmap"]["script"]
        self.assertEqual([os.path.basename(x) for x in piece][-2:], ["maplabel.js", "netregion.js"])
        self.assertIn("roles/netmap_report/files/web/maplabel.js", rp.PIECES["admap"]["script"])
        tasks = open(os.path.join(ROOT, "roles", "site_portal_stack", "tasks", "main.yml")).read()
        for n in ("maplabel.js", "netregion.js", "site_ad_layer.py"):
            self.assertIn(n, tasks)
        portal = open(os.path.join(ROOT, "roles", "site_portal_stack", "files", "site_portal.py")).read()
        self.assertIn('"maplabel.js", "netregion.js", "netmap-live.js"', portal)


def _browser():
    for b in ("google-chrome", "chromium", "chromium-browser"):
        if shutil.which(b):
            return shutil.which(b)
    return None


@unittest.skipUnless(_browser(), "no google-chrome or chromium")
class Region(unittest.TestCase):
    """In Chromium: Region is the first look, both layers on; labels never on each other, on a dot or out of the drawing
    (also with a wider font); down sites always labelled; the 1.95.1 review's pair is Down; never no layer; no AD data:
    the layer off and said; motion off: the dashes still; a pointed line fades the others; the hub's fan into trunks."""

    GEOM = r"""
var svg=document.querySelector('.rg-svg'),vb=svg.viewBox.baseVal;
var dots=[].map.call(svg.querySelectorAll('.rg-sites circle'),function(c){return [+c.getAttribute('cx'),+c.getAttribute('cy'),+c.getAttribute('r')]});
var labs=[].slice.call(svg.querySelectorAll('.rg-lab'));
var lab=[];labs.forEach(function(g,gi){[].forEach.call(g.querySelectorAll('text'),function(t){var b=t.getBBox();lab.push([b.x,b.y,b.x+b.width,b.y+b.height,gi,t.textContent])})});
var over=0,ondot=0,out=0,bad=[];
lab.forEach(function(a,i){if(a[0]<-1||a[2]>vb.width+1||a[1]<-1||a[3]>vb.height+1){out++;bad.push('out '+a[5])}
 dots.forEach(function(d){if(a[0]<d[0]+d[2]-1&&a[2]>d[0]-d[2]+1&&a[1]<d[1]+d[2]-1&&a[3]>d[1]-d[2]+1){ondot++;bad.push('dot '+a[5])}});
 lab.forEach(function(b,j){if(j>i&&b[4]!==a[4]&&a[0]<b[2]-1&&a[2]>b[0]+1&&a[1]<b[3]-1&&a[3]>b[1]+1){over++;bad.push(a[5]+' / '+b[5])}});});
r.labels=labs.length;r.dots=dots.length;r.labelsOnDots=ondot;r.labelsOverlap=over;r.labelsOut=out;r.bad=bad.slice(0,6);
var names=lab.map(function(a){return a[5].split(' +')[0]});
r.downUnlabelled=[].filter.call(svg.querySelectorAll('.rg-sites .rg-s.k-down[aria-label]'),function(g){return names.indexOf(g.getAttribute('aria-label').split(',')[0])<0}).length;
r.side=document.documentElement.scrollWidth-document.documentElement.clientWidth;
"""

    def page(self, data, js, width=1280, query="", head="", old_markup=False):
        p = rp.report_page_piece({"kind": "netmap", "data": data, "motion": ""}, ROOT)
        if old_markup:                                   # a page from before 1.102.0: no Region in its markup
            p["html"] = re.sub(r'<div class="rg-wrap">.*?</div></div>', "", p["html"], flags=re.S)
            p["html"] = re.sub(r'<button[^>]*data-p="region"[^>]*>Region</button>', "", p["html"])
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        with open(os.path.join(d, "a.js"), "w") as fh:
            fh.write(p["script"])
        with open(os.path.join(d, "a.css"), "w") as fh:
            fh.write(p["css"] + head)
        with open(os.path.join(d, "t.js"), "w") as fh:
            fh.write("window.addEventListener('load',function(){setTimeout(function(){var r={};try{%s}catch(e){r.err=String(e)+' '+(e.stack||'')}"
                     "document.getElementById('res').textContent=JSON.stringify(r)},400)});" % js)
        with open(os.path.join(d, "p.html"), "w") as fh:
            fh.write('<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="a.css"></head><body style="margin:0;padding:0 16px">%s<pre id="res"></pre>'
                     '<script src="a.js"></script><script src="t.js"></script></body></html>' % p["html"])
        out = subprocess.run([_browser(), "--headless=new", "--no-sandbox", "--disable-gpu", "--window-size=%d,900" % width, "--virtual-time-budget=6000",
                              "--dump-dom", "file://" + os.path.join(d, "p.html") + query], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             universal_newlines=True, timeout=120).stdout
        return json.loads(re.search(r'<pre id="res">(.*?)</pre>', out, re.S).group(1).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))

    def test_1_first_look_region_both_layers(self):
        js = ("r.mode=NG.mode();r.cls=document.getElementById('nm').className;r.view=document.getElementById('nm').getAttribute('data-view');"
              "r.layers=[].map.call(document.querySelectorAll('.ng-layer'),function(b){return b.getAttribute('aria-pressed')});"
              "r.match=!document.querySelector('.rg-match').hidden;r.rows=document.querySelectorAll('.rg-match tbody tr').length;"
              "r.ad=document.querySelectorAll('.rg-al').length;r.wan=document.querySelectorAll('.rg-wl').length;"
              "r.both=document.querySelector('.rg-svg').classList.contains('rg-both');r.cv=getComputedStyle(document.querySelector('.ng-cv')).display;"
              "r.none=document.querySelector('.rg-match td.none').textContent;r.leg=document.querySelector('.rg-leg').textContent;")
        r = self.page(fx.region(40, 5), js)
        self.assertNotIn("err", r)
        self.assertEqual((r["mode"], r["view"], r["layers"], r["match"]), ("region", "world", ["true", "true"], True))
        self.assertIn("ng-rg", r["cls"])
        self.assertEqual(r["cv"], "none")                                   # the globe's canvas stops and hides
        self.assertEqual(r["rows"], 7)
        self.assertGreaterEqual(r["ad"], 4)
        self.assertEqual(r["wan"], 47)
        self.assertTrue(r["both"])                                          # both on: the WAN lines fade
        self.assertEqual(r["none"], "none")
        self.assertIn("under 4 h", r["leg"])
        self.assertIn("24 h or failing", r["leg"])

    def test_2_labels_at_1280_and_800_and_a_wider_font(self):
        for w in (1280, 800):
            for head in ("", ".rg-s text{letter-spacing:1.4px;font-family:serif}"):
                for data in (fx.region(40, 5), fx.region(60, 11)):
                    r = self.page(data, self.GEOM, width=w, head=head)
                    self.assertNotIn("err", r)
                    self.assertEqual((r["labelsOnDots"], r["labelsOverlap"], r["labelsOut"]), (0, 0, 0), (w, head, r))
                    self.assertEqual(r["downUnlabelled"], 0, (w, head, r))
                    self.assertGreater(r["labels"], 15, r)
                    self.assertEqual(r["side"], 0)

    def test_3_one_down_the_others_not_read_is_down(self):
        js = ("var s=NG.wanState([{status:'Down'},{status:'Unknown'},{status:'Unknown'}]);r.st=s.status;r.why=s.why;"
              "r.one=NG.wanState([{status:'Down',why:'x'}]).why;r.unk=NG.wanState([{status:'Unknown'}]).status;"
              "r.warn=NG.wanState([{status:'Up'},{status:'Down'}]).status;"
              "var A=NG.arcs().filter(function(a){return a.ls.length===2&&a.ls.some(function(l){return l.status==='Unknown'})})[0];r.arc=A.l.status;r.tip=A.tip;"
              "r.cls=document.querySelector('.rg-wl[data-k=\"'+A.key+'\"] .rg-w').getAttribute('class');")
        r = self.page(fx.region(40, 5), js)
        self.assertNotIn("err", r)
        self.assertEqual((r["st"], r["why"]), ("Down", "1 down, the others not read"))
        self.assertEqual((r["one"], r["unk"], r["warn"]), ("x", "Unknown", "Warning"))
        self.assertEqual(r["arc"], "Down")
        self.assertIn("1 down, the other not read", r["tip"])
        self.assertIn("down", r["cls"])

    def test_4_never_no_layer(self):
        js = ("var b=document.querySelectorAll('.ng-layer');b[0].click();r.a=[b[0].getAttribute('aria-pressed'),b[1].getAttribute('aria-pressed')];"
              "r.wan1=document.querySelectorAll('.rg-wl').length;b[1].click();r.b=[b[0].getAttribute('aria-pressed'),b[1].getAttribute('aria-pressed')];"
              "r.match=document.querySelector('.rg-match').hidden;b[1].click();r.c=[b[0].getAttribute('aria-pressed'),b[1].getAttribute('aria-pressed')];")
        r = self.page(fx.region(30, 3), js)
        self.assertNotIn("err", r)
        self.assertEqual(r["a"], ["false", "true"])                         # WAN off, AD on
        self.assertEqual(r["wan1"], 0)
        self.assertEqual(r["b"], ["true", "false"])                         # AD off would be none: the WAN layer comes on
        self.assertTrue(r["match"])                                         # no AD layer: no matching table
        self.assertEqual(r["c"], ["true", "true"])                          # both again

    def test_5_no_ad_data_the_layer_is_off_and_says_why(self):
        d = fx.region(30, 3, ad=False)
        d["ad"] = {"why": "AD replication not in the report database yet", "source": "db"}
        js = ("var b=document.querySelectorAll('.ng-layer');r.dis=b[1].disabled;r.p=[b[0].getAttribute('aria-pressed'),b[1].getAttribute('aria-pressed')];"
              "r.leg=document.querySelector('.rg-leg').textContent;r.match=document.querySelector('.rg-match').hidden;r.ad=document.querySelectorAll('.rg-al').length;"
              "b[0].click();r.after=b[0].getAttribute('aria-pressed');")
        r = self.page(d, js)
        self.assertNotIn("err", r)
        self.assertTrue(r["dis"])
        self.assertEqual(r["p"], ["true", "false"])
        self.assertIn("AD replication: AD replication not in the report database yet", r["leg"])
        self.assertTrue(r["match"])
        self.assertEqual(r["ad"], 0)
        self.assertEqual(r["after"], "true")                                # the only layer stays on

    def test_6_motion(self):
        js = "r.anim=getComputedStyle(document.querySelector('.rg-flow')).animationName;r.dur=document.querySelector('.rg-flow').style.animationDuration;"
        self.assertEqual(self.page(fx.region(20, 2), js, query="?motion=off")["anim"], "none")
        r = self.page(fx.region(20, 2), "document.documentElement.classList.add('motion-always');" + js)
        self.assertTrue(r["dur"].endswith("s"))

    def test_7_a_pointed_line_fades_the_others_and_its_card(self):
        js = ("var k=rgTest.hits()[2];rgTest.select(k);var gs=[].slice.call(document.querySelectorAll('.rg-wl'));"
              "r.on=gs.filter(function(g){return g.classList.contains('on')}).length;"
              "r.op=getComputedStyle(gs.filter(function(g){return !g.classList.contains('on')})[0]).opacity;"
              "var t=document.querySelector('.rg-tip');r.tip=!t.hidden;r.text=t.textContent;"
              "var ka=rgTest.hits().filter(function(x){return x.indexOf('ad')===0})[0];rgTest.select(ka);r.adtext=t.textContent;"
              "r.trunks=[].filter.call(document.querySelectorAll('.rg-wl .rg-w'),function(p){return / C.* L/.test(p.getAttribute('d'))}).length;")
        r = self.page(fx.region(40, 5), js, head="*{transition:none!important}")
        self.assertNotIn("err", r)
        self.assertEqual(r["on"], 1)
        self.assertAlmostEqual(float(r["op"]), 0.12, places=2)
        self.assertTrue(r["tip"])
        self.assertRegex(r["text"], r"WAN link.*bps")
        self.assertIn("AD replication", r["adtext"])
        self.assertIn("WAN:", r["adtext"])
        self.assertGreater(r["trunks"], 10)                                 # the hub's fan: lines merged into trunks near it

    def test_8_the_date_line_one_site_and_back_to_the_globe(self):
        d = fx.region(6, 4, ad=False, lon=(170, 179))
        d["geo"]["SITB0102"] = [10.0, -172.0]                               # the other side of the date line
        js = ("var vb=document.querySelector('.rg-svg').viewBox.baseVal;r.inside=[].every.call(document.querySelectorAll('.rg-sites circle'),function(c){"
              "return +c.getAttribute('cx')>0&&+c.getAttribute('cx')<vb.width&&+c.getAttribute('cy')>0&&+c.getAttribute('cy')<vb.height});"
              "r.n=document.querySelectorAll('.rg-sites circle').length;"
              "document.querySelector('.ng-p[data-p=globe]').click();r.mode=NG.mode();r.cv=getComputedStyle(document.querySelector('.ng-cv')).display;"
              "r.wrap=getComputedStyle(document.querySelector('.rg-wrap')).display;document.querySelector('.ng-p[data-p=region]').click();r.back=NG.mode();")
        r = self.page(d, js)
        self.assertNotIn("err", r)
        self.assertTrue(r["inside"], r)
        self.assertEqual(r["n"], 7)
        self.assertEqual((r["mode"], r["cv"], r["wrap"], r["back"]), ("globe", "block", "none", "region"))
        one = fx.region(1, 1, ad=False)
        r = self.page(one, "r.n=document.querySelectorAll('.rg-sites circle').length;" + self.GEOM)
        self.assertNotIn("err", r)
        self.assertEqual(r["n"], 2)


class Review11020(unittest.TestCase):
    """The review of 1.102.0 (Neon): one test a finding."""

    def res(self, nodes=None, names=None):
        return {"data": {"sites": names or NM_NAMES, "nodes": nodes or NODES, "links": []}, "map": {"no_site": "Not placed"}}

    def test_p1_1_the_newest_ad_run_wins_whatever_its_result(self):
        good = {"id": 7, "status": "successful", "artifacts": {"ad_repl": {"layer": al.compact(repl(), O)}}}
        failed_bare = {"id": 9, "status": "failed", "artifacts": {}}
        lay = nf.netmap_ad_layer(self.res(), [good, failed_bare], {"template": "AD", "now": NOW})
        self.assertIn("newest run (job 9) failed", lay["why"])               # never the older healthy run
        self.assertNotIn("sites", lay)
        failed = {"id": 9, "status": "failed", "artifacts": {"ad_repl": {"layer": al.compact(repl(), O)}}}
        lay = nf.netmap_ad_layer(self.res(), [good, failed], {"template": "AD", "now": NOW})
        self.assertEqual((lay["why"], lay["job"], lay["run_status"]), ("", 9, "failed"))
        self.assertIn("which FAILED", nf.netmap_ad_lines(lay)[0])
        src = open(os.path.join(ROOT, "roles", "site_email", "library", "site_report_history.py")).read()
        self.assertIn('"jobs/?job_template=%s&id__lt=%d&status__in=%s&order_by=-id&page_size=%d"', src)
        lay = nf.netmap_ad_layer(self.res(), [good, {"id": 10, "status": "error", "artifacts": {}}], {"template": "AD", "now": NOW})
        self.assertIn("newest run (job 10) error", lay["why"])

    def test_p1_2_a_reading_is_aged_to_now_and_stale_past_the_scale(self):
        c = al.compact(repl(), O)                                            # read at NOW
        fresh = al.layer(c, self.res()["data"], now=NOW + datetime.timedelta(hours=1))
        old = dict((x["a"] + "/" + x["b"], x) for x in fresh["links"])
        self.assertEqual(old["Branch-East/HQ"]["h"], 10.0)                    # 9 h at the read, 1 h later
        self.assertFalse(fresh["stale"])
        stale = al.layer(c, self.res()["data"], now=NOW + datetime.timedelta(hours=30))
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["age_h"], 30.0)
        self.assertEqual(set(x["cls"] for x in stale["links"]), {"critical"})   # never a green line from a 30 h old run
        self.assertTrue(all(x["h"] is None or x["h"] >= 30 for x in stale["links"]))

    def test_p1_3_matching_shared_short_names_fqdn_and_ad_site_map(self):
        r = {"dcs": [{"name": "dc01.alpha.example.mil", "site": "Alpha"}, {"name": "dc01.beta.example.mil", "site": "Beta"},
                     {"name": "dc05.alpha.example.mil", "site": "Gamma"}, {"name": "DC07", "site": "Delta"}, {"name": "DC07", "site": "Epsilon"}],
             "links": [{"frm": "DC07", "to": "dc05.alpha.example.mil", "nc": "x", "last_success": iso(1), "failures": 0}], "now": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")}
        nodes = [{"id": 1, "name": "DC01", "site": "HUBA0001"}, {"id": 5, "name": "dc05.beta.example.mil", "site": "SITB0201"}]
        lay = al.layer(al.compact(r, O), {"sites": NM_NAMES, "nodes": nodes}, site_map={"DELTA": "huba0001"}, now=NOW)
        s = dict((x["n"], x) for x in lay["sites"])
        self.assertNotEqual(s["Alpha"]["by"], "the DCs' nodes")               # two DCs called dc01: never by the short name
        self.assertNotEqual(s["Beta"]["by"], "the DCs' nodes")
        self.assertNotEqual(s["Gamma"]["by"], "the DCs' nodes")               # dc05.alpha is not dc05.beta
        self.assertEqual((s["Delta"]["nm"], s["Delta"]["by"]), ("HUBA0001", "ad_site_map"))   # the map's own name, any case
        self.assertEqual(lay["links"], [])                                    # DC07 is in two sites: no link placed
        lay2 = al.layer(al.compact({"dcs": [{"name": "Z1", "site": "Not placed"}], "links": []}, O), {"sites": NM_NAMES, "nodes": []}, now=NOW)
        self.assertEqual(lay2["sites"][0]["by"], "none")                      # never the map's "Not placed"
        sw_ = {"dcs": [], "netmap": {"ok": True, "names": NM_NAMES, "places": {}, "node_site": {}}}
        rep = am.ad_repl_map(r, sw_, dict(O, ad_site_map={"DELTA": "huba0001"}))["extra"]["data"]
        self.assertEqual(dict((x["n"], x["nm"]) for x in rep["sites"])["Delta"], "HUBA0001")   # the AD report: the same answer
        self.assertEqual(rep["links"], [])

    def portal(self):
        try:
            import site_portal as sp
        except Exception as e:  # noqa: BLE001
            self.skipTest("site_portal not importable here: %s" % e)
        return sp

    def test_p1_4_a_database_set_up_error_never_breaks_the_map(self):
        sp = self.portal()
        logged = []

        class Log(object):
            def add(self, x):
                logged.append(x)

        class Cfg(object):
            netmap_opts = {}
            report_db = {"host": "db.corp.example.mil", "user": "reader"}

        class Boom(object):
            def __init__(self, cfg):
                raise RuntimeError("secret detail: host db.corp.example.mil")

        class Stub(object):
            pass
        st = Stub()
        st.cfg, st.log, st.ad_at, st.ad_c, st.ad_why, st.ad_connect, st.ad_mark = Cfg(), Log(), 0.0, None, "", None, "%s"
        had = sp.__dict__.get("AdDb")
        sp.AdDb = Boom
        try:
            lay = sp.NetLive._ad(st, {"sites": [], "nodes": []})
        finally:
            if had is None:
                del sp.AdDb
            else:
                sp.AdDb = had
        self.assertEqual(lay["why"], sp.AD_NOT_READ)
        self.assertNotIn("secret", json.dumps(lay))
        self.assertTrue(any("secret detail" in str(x) for x in logged))      # the detail in the server's log only
        st.ad_at, st.ad_connect = 0.0, (lambda: None)                         # the reader itself failing: off, said, never "no AD read"
        orig = sp.ad_repl_from_db
        sp.ad_repl_from_db = lambda *a, **k: (_ for _ in ()).throw(TypeError("x"))
        try:
            self.assertEqual(sp.NetLive._ad(st, {"sites": [], "nodes": []})["why"], sp.AD_NOT_READ)
        finally:
            sp.ad_repl_from_db = orig

    def test_p2_6_read_only_transaction_and_fixed_messages(self):
        sp = self.portal()
        calls = []

        class Cur(object):
            def __init__(self, fail_on=None):
                self.fail_on, self.last = fail_on, ""

            def execute(self, sql, args=()):
                calls.append(sql)
                if self.fail_on and self.fail_on in sql:
                    raise self.fail_on_exc
                self.last = sql

            def fetchall(self):
                return [(1, datetime.datetime(2026, 10, 15, 9, 30))] if "FROM ad_run" in self.last else []

        class Conn(object):
            def __init__(self, cur):
                self.cur, self.rolled = cur, 0

            def cursor(self):
                return self.cur

            def rollback(self):
                self.rolled += 1
                calls.append("ROLLBACK")

            def close(self):
                pass
        cur = Cur()
        conn = Conn(cur)
        c, why = sp.ad_repl_from_db(lambda: conn)
        self.assertEqual(why, "")
        self.assertIn("START TRANSACTION READ ONLY", calls)
        self.assertLess(calls.index("START TRANSACTION READ ONLY"), [i for i, x in enumerate(calls) if "SELECT" in x][0])
        self.assertEqual(calls[-1], "ROLLBACK")                               # nothing left open
        cur2 = Cur("FROM ad_dc")
        cur2.fail_on_exc = Exception(1146, "Table 'site_reports.ad_dc' doesn't exist")
        self.assertEqual(sp.ad_repl_from_db(lambda: Conn(cur2))[1], sp.AD_NOT_YET)   # a missing table on any query
        cur3 = Cur("FROM ad_link")
        cur3.fail_on_exc = Exception(1045, "Access denied for user 'reader'@'192.0.2.7'")
        logged = []
        c, why = sp.ad_repl_from_db(lambda: Conn(cur3), log=logged.append)
        self.assertEqual(why, sp.AD_NOT_READ)
        self.assertNotIn("192.0.2.7", why)
        self.assertIn("192.0.2.7", json.dumps(logged))

    GEO = r"""
var svg=document.querySelector('.rg-svg');
var dots=[].map.call(svg.querySelectorAll('.rg-sites circle'),function(c){return [+c.getAttribute('cx'),+c.getAttribute('cy'),+c.getAttribute('r')]});
r.leads=0;r.offCentre=0;[].forEach.call(svg.querySelectorAll('.rg-lead'),function(l){r.leads++;var x=+l.getAttribute('x1'),y=+l.getAttribute('y1');
 var best=dots.map(function(d){return Math.abs(Math.hypot(x-d[0],y-d[1])-(d[2]+1))}).sort(function(a,b){return a-b})[0];if(best>0.6)r.offCentre++;});
"""

    def test_p1_5_a_down_link_is_drawn_over_the_up_ones(self):
        js = ("var st=[].map.call(document.querySelectorAll('.rg-wan .rg-wl .rg-w'),function(p){return /\\bdown\\b/.test(p.getAttribute('class'))?'D':/\\b(b0|b1|b2|b3)\\b/.test(p.getAttribute('class'))?'U':'o'}).join('');"
              "r.st=st;r.lastUp=st.lastIndexOf('U');r.firstDown=st.indexOf('D');")
        r = Region.page(self, fx.region(40, 5), js)
        self.assertNotIn("err", r)
        self.assertGreater(r["firstDown"], r["lastUp"], r["st"])

    def test_p2_7_one_trunk_across_the_west_seam(self):
        js = ("var A=[],pos={HUB:[600,300]};for(var i=0;i<8;i++){var n='S'+i;pos[n]=[100,300+(i%2?1:-1)*(1+i)];"
              "A.push({a:{name:'HUB'},b:{name:n},key:'HUB\\u0001'+n,l:{status:'Up'}})}"
              "var w=rgTest.wanPaths(A,pos);r.trunks=w.filter(function(x){return x.trunk}).map(function(x){return x.trunk.T.map(Math.round).join(',')});"
              "r.distinct=r.trunks.filter(function(t,i,a){return a.indexOf(t)===i}).length;")
        r = Region.page(self, fx.region(10, 2, ad=False), js)
        self.assertNotIn("err", r)
        self.assertEqual(len(r["trunks"]), 8)
        self.assertEqual(r["distinct"], 1, r)                                  # above and below the hub's west: one trunk

    def test_p2_8_leaders_start_at_the_dots_centre(self):
        for w, data in ((800, fx.region(60, 11)), (800, fx.region(40, 5)), (1280, fx.region(60, 11))):
            r = Region.page(self, data, self.GEO, width=w)
            self.assertNotIn("err", r)
            if r["leads"]:
                self.assertEqual(r["offCentre"], 0, r)
                return
        self.skipTest("no leader drawn in these fixtures")

    def test_p2_9_keyboard_cards_sit_on_the_line_and_enter_keeps_them(self):
        js = ("var k=rgTest.hits()[5],m=rgTest.mid(k),svg=document.querySelector('.rg-svg'),w=document.querySelector('.rg-wrap');"
              "var h=[].filter.call(document.querySelectorAll('.rg-hit'),function(x){return x.parentNode.getAttribute('data-k')===k})[0];"
              "h.dispatchEvent(new FocusEvent('focus'));var t=document.querySelector('.rg-tip'),sr=svg.getBoundingClientRect(),wr=w.getBoundingClientRect();"
              "var y=sr.top-wr.top+m[1]/svg.viewBox.baseVal.height*sr.height;r.dy=Math.min(Math.abs(t.offsetTop-(y+14)),Math.abs(t.offsetTop-(y-t.offsetHeight-10)));"
              "h.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));r.sel=rgTest.R.sel===k;"
              "h.dispatchEvent(new KeyboardEvent('keydown',{key:' ',bubbles:true}));r.off=rgTest.R.sel==='';")
        r = Region.page(self, fx.region(40, 5), js, width=800)
        self.assertNotIn("err", r)
        self.assertLess(r["dy"], 2, r)
        self.assertTrue(r["sel"] and r["off"], r)

    def test_p2_10_sites_on_one_spot_all_in_the_card(self):
        d = fx.region(12, 3)
        d["geo"][d["sites"][6]] = list(d["geo"][d["sites"][2]])           # SITE.. on top of the one by name (its AD site)
        js = ("var g=[].filter.call(document.querySelectorAll('.rg-sites .rg-s[aria-label]'),function(x){return x.getAttribute('aria-label').indexOf('%s')===0})[0];"
              "g.dispatchEvent(new FocusEvent('focus'));r.text=document.querySelector('.rg-tip').textContent;" % d["sites"][6])
        r = Region.page(self, d, js)
        self.assertNotIn("err", r)
        self.assertIn(d["sites"][6], r["text"])
        self.assertIn(d["sites"][2], r["text"])
        self.assertIn("AD", r["text"])                                        # the other site's AD site too


class ReviewCodex11020(unittest.TestCase):
    """Codex's P1 pass on 1.102.0: one test a finding."""

    def test_c1_shared_short_names_counted_as_identities(self):
        r = {"dcs": [{"name": "DC01", "site": "Alpha"}, {"name": "DC01", "site": "Beta"}], "links": [], "now": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")}
        page = {"sites": ["SITE0001"], "nodes": [{"id": 7, "name": "DC01", "site": "SITE0001"}]}
        c = al.compact(r, O, [{"dc": "DC01", "node_id": 7}], "https://orion.corp.example.mil")
        lay = al.layer(c, page, now=NOW, orion_url="https://orion.corp.example.mil")
        self.assertEqual([x["by"] for x in lay["sites"]], ["none", "none"])     # neither by the saved id nor by the name
        rep = am.ad_repl_map(r, {"dcs": [{"dc": "DC01", "node_id": 7}], "netmap": {"ok": True, "names": ["SITE0001"], "places": {}, "node_site": {"7": "SITE0001"}}}, O)
        self.assertEqual([x["by"] for x in rep["extra"]["data"]["sites"]], ["none", "none"])   # the AD report: the same

    def test_c2_the_dcs_full_name_is_kept_for_the_fallback(self):
        r = {"dcs": [{"name": "DC05", "site": "Alpha"}], "links": [], "now": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")}
        c = al.compact(r, O, [{"dc": "DC05", "fqdn": "dc05.alpha.example.mil", "node_id": None}])
        lay = al.layer(c, {"sites": ["SITB0201"], "nodes": [{"id": 3, "name": "dc05.beta.example.mil", "site": "SITB0201"}]}, now=NOW)
        self.assertEqual(lay["sites"][0]["by"], "none")                     # dc05.alpha is not dc05.beta
        lay = al.layer(c, {"sites": ["SITB0201"], "nodes": [{"id": 3, "name": "dc05.alpha.example.mil", "site": "SITB0201"}]}, now=NOW)
        self.assertEqual(lay["sites"][0]["by"], "the DCs' nodes")

    def test_c3_saved_node_ids_only_from_the_same_solarwinds(self):
        r = {"dcs": [{"name": "DC01", "site": "HQ"}], "links": [], "now": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")}
        c = al.compact(r, O, [{"dc": "DC01", "node_id": 7}], "https://orion-a.corp.example.mil")
        page = {"sites": ["SITB0201"], "nodes": [{"id": 7, "name": "SITB0201-RT01", "site": "SITB0201"}]}   # node 7 on another Orion
        self.assertEqual(al.layer(c, page, now=NOW, orion_url="https://orion-b.corp.example.mil")["sites"][0]["by"], "none")
        self.assertEqual(al.layer(c, page, now=NOW)["sites"][0]["by"], "none")                      # not known: not used
        self.assertEqual(al.layer(c, page, now=NOW, orion_url="orion-a.corp.example.mil:17774")["sites"][0]["by"], "the DCs' nodes")

    def test_c4_no_layer_when_no_dc_was_read(self):
        r = {"dcs": [{"name": "DC01", "site": "HQ", "read": False}, {"name": "DC02", "site": "Branch", "read": False}], "links": [],
             "now": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")}
        lay = al.layer(al.compact(r, O), {"sites": NM_NAMES, "nodes": []}, now=NOW)
        self.assertIn("no DC's replication could be read", lay["why"])
        one = {"dcs": [{"name": "DC01", "site": "HQ", "read": True}], "links": [], "now": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")}
        self.assertEqual(al.layer(al.compact(one, O), {"sites": NM_NAMES, "nodes": []}, now=NOW)["why"], "")   # one read site: on

    def test_c5_ad_site_map_to_no_such_site_is_invalid(self):
        for target in ("Not placed", "SITZ9999"):
            lay = al.layer(al.compact(repl(), O), {"sites": NM_NAMES, "nodes": NODES}, site_map={"HQ": target}, now=NOW)
            s = dict((x["n"], x) for x in lay["sites"])["HQ"]
            self.assertEqual((s["nm"], s["by"]), ("", "none"), target)
            self.assertIn("not a site on the network map", s["why"])

    def test_c6_ages_at_display_time(self):
        d = fx.region(40, 5)
        t = time.time() - 30 * 3600
        when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
        d["ad"]["read_at"] = when                          # made a day and more ago, saying "fresh" then
        for l in d["ad"]["links"]:
            l.update(cls="ok", h=0.1, fail=False, never=False, ls=when)
        d["ad"]["stale"] = False
        js = ("r.cls=[].map.call(document.querySelectorAll('.rg-ad'),function(p){return p.getAttribute('class')});"
              "r.leg=document.querySelector('.rg-leg').textContent;var k=rgTest.hits().filter(function(x){return x.indexOf('ad')===0})[0];"
              "rgTest.select(k);r.card=document.querySelector('.rg-tip').textContent;")
        r = Region.page(self, d, js)
        self.assertNotIn("err", r)
        self.assertTrue(r["cls"] and all("k-critical" in c for c in r["cls"]), r["cls"])
        self.assertIn("stale", r["leg"])
        self.assertNotIn("min ago", r["card"])

    def test_c7_no_utilization_read_is_not_zero_percent(self):
        d = fx.region(20, 2, ad=False)
        for l in d["links"]:
            l.pop("in", None)
            l.pop("out", None)
        js = ("var A=NG.arcs().filter(function(a){return a.l.status==='Up'})[0];r.known=A.l.known;"
              "r.cls=document.querySelector('.rg-wl[data-k=\"'+A.key+'\"] .rg-w').getAttribute('class');"
              "rgTest.select(A.key);r.card=document.querySelector('.rg-tip').textContent;r.tip=A.tip;")
        r = Region.page(self, d, js)
        self.assertNotIn("err", r)
        self.assertFalse(r["known"])
        self.assertIn("nk", r["cls"].split())
        self.assertIn("traffic not known", r["card"])
        self.assertNotIn("0%", r["card"])
        self.assertIn("traffic not known", r["tip"])

    def test_c8_an_older_page_falls_back_to_the_globe(self):
        js = "r.mode=NG.mode();r.cls=document.getElementById('nm').className;r.cv=getComputedStyle(document.querySelector('.ng-cv')).display;"
        r = Region.page(self, fx.region(20, 2), js, old_markup=True)
        self.assertNotIn("err", r)
        self.assertEqual((r["mode"], r["cv"]), ("globe", "block"))
        self.assertNotIn("ng-rg", r["cls"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
