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
            "netmap": {"ok": ok, "source": "db-snapshot", "names": ["HUBA0001", "SITB0201", "SITC0301", "SITE0501", "Not placed"],
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
        self.assertEqual((self.s["site0501"]["nm"], self.s["site0501"]["by"]), ("SITE0501", "name"))   # its DC's node is not placed
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


if __name__ == "__main__":
    unittest.main(verbosity=1)
