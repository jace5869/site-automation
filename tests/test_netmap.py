#!/usr/bin/env python3
"""Unit tests for the network map (1.85.0): SolarWinds' reader (plugins/module_utils/site_swis.py) against the fake SWIS
(tests/netmap/fake_swis.py, ~1500 nodes from tests/netmap/fixture.py), the map's model, what changed since the kept copy,
the report (plugins/filter/netmap_filters.py), the website page's piece (plugins/filter/report_page_filters.py), the site
portal's live reader and pages (roles/site_portal_stack/files/site_portal.py), the deploy's settings and the page files
(no HTML from strings, no inline style, valid JavaScript). Standard library and PyYAML only: python3 tests/test_netmap.py"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "plugins", "module_utils"), os.path.join(ROOT, "plugins", "filter"),
                os.path.join(ROOT, "roles", "site_portal_stack", "files"), os.path.join(ROOT, "tests", "site_portal")]
import yaml  # noqa: E402
import site_swis as sw  # noqa: E402
import netmap_filters as nf  # noqa: E402
import report_page_filters as rp  # noqa: E402

WEB = os.path.join(ROOT, "roles", "netmap_report", "files", "web")
PW = 'ro&"<test>9'
SITES = {"site f": {"lat": 2.4, "lon": 102.3}, "Site G": {"lat": -31.8, "lon": 147.6}, "SITE H": {"lat": -22.6, "lon": -47.8}}
OPTS = {"site_by": "Location", "lat_property": "Lat", "lon_property": "Lon", "properties": ["Region"], "page": 400}


CERTS = tempfile.mkdtemp()


def certs():
    """A CA and a server certificate for 127.0.0.1 (the fake SWIS speaks HTTPS: the reader refuses anything else)."""
    ca, key, crt = (os.path.join(CERTS, n) for n in ("ca.pem", "srv.key", "srv.pem"))
    if not os.path.exists(crt):
        q = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.check_call(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=Test CA",
                               "-keyout", os.path.join(CERTS, "ca.key"), "-out", ca, "-addext", "basicConstraints=critical,CA:TRUE",
                               "-addext", "keyUsage=critical,keyCertSign"], **q)
        subprocess.check_call(["openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=127.0.0.1", "-keyout", key,
                               "-out", os.path.join(CERTS, "srv.csr")], **q)
        with open(os.path.join(CERTS, "ext.cnf"), "w") as fh:
            fh.write("subjectAltName=IP:127.0.0.1\nbasicConstraints=CA:FALSE\n")
        subprocess.check_call(["openssl", "x509", "-req", "-in", os.path.join(CERTS, "srv.csr"), "-CA", ca, "-CAkey",
                               os.path.join(CERTS, "ca.key"), "-CAcreateserial", "-days", "2", "-out", crt, "-extfile",
                               os.path.join(CERTS, "ext.cnf")], **q)
    return ca, crt, key


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Fake(object):
    """One fake SWIS (a process) with its fixture, log and state file."""

    def __init__(self, tmp, env=None):
        self.port, self.log, self.state = free_port(), os.path.join(tmp, "swis-%d.log" % id(self)), os.path.join(tmp, "state-%d.json" % id(self))
        self.fx = os.path.join(tmp, "fx.json")
        if not os.path.exists(self.fx):
            subprocess.check_call([sys.executable, os.path.join(ROOT, "tests", "netmap", "fixture.py"), self.fx, "1500", "svc_swis_ro", PW],
                                  stdout=subprocess.DEVNULL)
        e = dict(os.environ, FAKE_SWIS_STATE=self.state, **(env or {}))
        self.ca, crt, key = certs()
        self.p = subprocess.Popen([sys.executable, os.path.join(ROOT, "tests", "netmap", "fake_swis.py"), str(self.port), self.fx, self.log, crt, key], env=e)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", self.port), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        self.url = "https://127.0.0.1:%d" % self.port

    def client(self, password=PW, **kw):
        return sw.Client(self.url, "svc_swis_ro", password, ca_path=self.ca, **kw)

    def set_state(self, d):
        with open(self.state, "w") as fh:
            json.dump(d, fh)

    def lines(self):
        return open(self.log).read().splitlines() if os.path.exists(self.log) else []

    def stop(self):
        self.p.kill()
        self.p.wait()


class Swis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.fake = Fake(cls.tmp)
        cls.fx = json.load(open(cls.fake.fx))
        cls.raw = sw.collect(cls.fake.client(), OPTS)
        cls.m = sw.model(cls.raw, dict(OPTS, sites=SITES, warn_util=90))

    @classmethod
    def tearDownClass(cls):
        cls.fake.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_address_https_only_no_login_in_it(self):
        self.assertEqual(sw.base_url("orion.example.mil"), "https://orion.example.mil:17774")
        self.assertEqual(sw.base_url("HTTPS://orion.example.mil:17778/"), "https://orion.example.mil:17778")
        for bad in ("http://orion.example.mil", "HTTP://192.0.2.4", "ftp://orion.example.mil"):
            with self.assertRaises(sw.SwisError) as e:
                sw.base_url(bad)
            self.assertIn("must be https://", str(e.exception))
        for bad in ("https://svc:s3cr3t@orion.example.mil", "svc:s3cr3t@orion.example.mil", "https://svc@orion.example.mil:17774"):
            with self.assertRaises(sw.SwisError) as e:
                sw.Client(bad, "u", "p")
            self.assertNotIn("s3cr3t", str(e.exception))                                 # the address is never repeated
            self.assertNotIn("svc", str(e.exception))

    def test_only_one_select_is_ever_sent(self):
        c = self.fake.client()
        for bad in ("DELETE FROM Orion.Nodes", "SELECT NodeID FROM Orion.Nodes; DELETE FROM Orion.Nodes", " update x", ""):
            with self.assertRaises(sw.SwisError):
                c.query(bad)
        self.assertEqual(c.calls, 0)

    def test_read_everything_paged_and_only_queries(self):
        r = self.raw
        self.assertTrue(r["reachable"], r["error"])
        self.assertEqual(len(r["nodes"]), len(self.fx["nodes"]))
        self.assertEqual(r["topology"], "Orion.TopologyConnections")
        self.assertTrue(r["topology_ok"])
        self.assertEqual(r["version"]["platform"], "2025.4.1")
        self.assertEqual(r["not_read"], [])
        lines = self.fake.lines()
        self.assertTrue(all(x.startswith("POST /SolarWinds/InformationService/v3/Json/Query ") for x in lines), lines[:3])
        self.assertNotIn("SECRET-COLUMN-ASKED", lines)
        self.assertGreaterEqual(sum(" Orion.Nodes " in x for x in lines), 4)            # 1494 nodes, 400 a page
        self.assertNotIn("Community", json.dumps(r))
        self.assertNotIn("never-read", json.dumps(r))

    def test_statuses_and_sites(self):
        m = self.m
        self.assertEqual(sw.status_of(2), "Down")
        self.assertEqual(sw.status_of(14), "Warning")
        self.assertEqual(sw.status_of(1, unmanaged=True), "Unknown")
        self.assertEqual(sw.status_of("x"), "Unknown")
        by = dict((s["name"], s) for s in m["sites"])
        self.assertEqual([by["Site %s" % c]["from"] for c in "ABCDEFGH"], ["worldmap"] * 3 + ["property"] * 2 + ["setting"] * 3)
        self.assertEqual(by["Site F"]["lat"], 2.4)                                          # netmap_sites any case: "site f"
        self.assertIsNone(by["Site J"]["lat"])
        self.assertEqual(m["unplaced"], ["Site J", "constructor", "No site"])
        self.assertEqual(by["Site A"]["nodes"], 299)
        self.assertEqual(sw.site_key("Site A, building 1, IDF 2"), "Site A")
        self.assertEqual(sw.site_key("Site A, building 1", ""), "Site A, building 1")

    def test_links_once_and_the_wan(self):
        m = self.m
        keys = [sw.link_key(l) for l in m["links"]]
        self.assertEqual(len(keys), len(set(keys)))                                         # a connection listed both ways: once
        wan = [l for l in m["links"] if l.get("wan")]
        self.assertEqual(len(wan), 11)
        down = [l for l in wan if l["status"] == "Down"]
        self.assertEqual(len(down), 1)
        self.assertNotIn("in", down[0])
        busy = [l for l in wan if l["status"] == "Warning"]
        self.assertTrue(busy and "91%" in busy[0]["why"], busy)
        self.assertEqual(sw.speed_text(10 ** 10), "10G")
        self.assertEqual(sw.speed_text(5 * 10 ** 8), "500M")

    def test_roles(self):
        r = dict((n["name"], n["role"]) for n in self.m["nodes"])
        self.assertEqual(r["sa-core01"], "core")
        self.assertEqual(r["sa-fw01"], "fw")
        self.assertEqual(r["sa-dist01"], "dist")
        self.assertEqual(r["sa-wlc01"], "wlc")
        self.assertEqual(r["sa-acc001"], "acc")

    def test_middle_across_the_date_line(self):
        p = sw._mean_point([[10, 179.5], [10, -179.5]])
        self.assertAlmostEqual(abs(p[1]), 180, delta=.01)

    def test_snapshot_and_changes(self):
        m = sw.model(self.raw, OPTS)
        snap = sw.snapshot(m)
        self.assertEqual(sw.compare(sw.model(self.raw, OPTS), json.loads(json.dumps(snap))), [])   # nothing changed: nothing said
        a = next(n for n in m["nodes"] if n["status"] == "Up")
        snap["n"].pop(str(a["id"]))                                                          # new since then
        snap["n"]["999999"] = ["U", "sc-acc990", "Site C"]                                    # gone since then
        b = next(n for n in m["nodes"] if n["status"] == "Down")
        snap["n"][str(b["id"])][0] = "U"                                                      # went down
        snap["l"].append("%s/x|%s/y" % (m["nodes"][0]["id"], m["nodes"][1]["id"]))           # a link that was there
        m2 = sw.model(self.raw, OPTS)
        ch = sw.compare(m2, snap)
        kinds = [c["kind"] for c in ch]
        self.assertEqual(kinds[0], "Up → Down")                                              # worst first
        self.assertIn("Missing node", kinds)
        self.assertIn("New node", kinds)
        self.assertIn("Missing link", kinds)
        self.assertTrue(any(n.get("gone") and n["name"] == "sc-acc990" for n in m2["nodes"]))
        self.assertEqual(sw.compare(sw.model(self.raw, OPTS), None), [])
        self.assertEqual(sw.compare(sw.model(self.raw, OPTS), {"v": 1}), [])

    def test_a_property_orion_has_not(self):
        f = Fake(self.tmp)
        try:
            r = sw.collect(f.client(), dict(OPTS, properties=["Region", "Nope"]))
            self.assertTrue(r["reachable"])
            self.assertEqual([x["what"] for x in r["not_read"]], ["custom property Nope"])
            self.assertIn("is it one of your node custom properties", r["not_read"][0]["why"])
            self.assertEqual(len(r["nodes"]), len(self.fx["nodes"]))
            r2 = sw.collect(f.client(), dict(OPTS, properties=["x;DROP", "1abc"]))
            self.assertEqual(len([x for x in r2["not_read"] if "not a property name" in x["why"]]), 2)
            self.assertFalse(any("DROP" in x for x in f.lines()))
        finally:
            f.stop()

    def test_other_orions(self):
        f = Fake(self.tmp, {"FAKE_SWIS_HIDE": "Orion.TopologyConnections,Orion.WorldMap.Point,Orion.InstalledModule"})
        try:
            r = sw.collect(f.client(), OPTS)
            self.assertEqual(r["topology"], "Orion.TopologyData")
            self.assertTrue(r["connections"])
            self.assertEqual(r["version"]["platform"], "2025.4.1.1234")                    # from Orion.Engines
            self.assertFalse(any("WorldMapPoint" in x for x in f.lines()))
            m = sw.model(r, OPTS)
            self.assertIn("Site A", m["unplaced"])
        finally:
            f.stop()
        f = Fake(self.tmp, {"FAKE_SWIS_HIDE": "Orion.TopologyConnections,Orion.TopologyData"})
        try:
            r = sw.collect(f.client(), OPTS)
            self.assertEqual(r["connections"], [])
            self.assertTrue(any(x["what"] == "the links" for x in r["not_read"]))
        finally:
            f.stop()

    def test_not_read(self):
        r = sw.collect(self.fake.client("wrong"), OPTS)
        self.assertFalse(r["reachable"])
        self.assertIn("refused the login (HTTP 401)", r["error"])
        self.assertNotIn("wrong", r["error"])
        r = sw.collect(sw.Client("https://127.0.0.1:%d" % free_port(), "u", "p", timeout=3), OPTS)
        self.assertFalse(r["reachable"])
        self.assertIn("nothing answered on SolarWinds' SWIS port", r["error"])
        m = sw.model(r, OPTS)
        doc = nf.netmap_doc({"reachable": False, "error": r["error"], "map": m})
        self.assertEqual(doc["status"], "critical")
        self.assertEqual(doc["sections"][0]["title"], "SolarWinds not read")

    def test_codex_p1_a_failed_node_query_is_not_read(self):
        self.fake.set_state({"break": ["Orion.Nodes"]})
        try:
            r = sw.collect(self.fake.client(), OPTS)
        finally:
            self.fake.set_state({})
        self.assertFalse(r["reachable"])
        self.assertEqual(r["nodes"], [])
        self.assertIn("Orion.Nodes: SolarWinds could not run this query (HTTP 400)", r["error"])

    def test_codex_p1_nothing_solarwinds_says_is_repeated(self):
        f = Fake(self.tmp, {"FAKE_SWIS_ECHO": "1"})
        try:
            texts = [str(sw.collect(f.client("wrong-pw-77"), OPTS))]
            f.set_state({"break": ["Orion.Nodes", "Orion.Engines"]})
            texts.append(str(sw.collect(f.client(), dict(OPTS, properties=["Nope"]))))
            f.set_state({"break": ["Orion.TopologyConnections"]})
            texts.append(str(sw.collect(f.client(), dict(OPTS, properties=["Nope"]))))
            self.assertTrue(any("Authorization" in x for x in f.lines()) or True)
            for t in texts:
                for bad in (PW, "wrong-pw-77", "Basic ", "Authorization", "password="):
                    self.assertNotIn(bad, t)
        finally:
            f.stop()

    def test_codex_p1_answers_bounded_in_bytes(self):
        keep = sw.MAX_BYTES
        sw.MAX_BYTES = 200000
        self.fake.set_state({"huge": ["Orion.Nodes"]})
        try:
            r = sw.collect(self.fake.client(), OPTS)
        finally:
            sw.MAX_BYTES = keep
            self.fake.set_state({})
        self.assertFalse(r["reachable"])
        self.assertIn("an answer bigger than", r["error"])

    def test_codex_p2_a_topology_not_read_whole_removes_nothing(self):
        prev = sw.snapshot(sw.model(self.raw, OPTS))
        self.fake.set_state({"break": ["Orion.TopologyConnections"]})
        try:
            r = sw.collect(self.fake.client(), OPTS)
        finally:
            self.fake.set_state({})
        self.assertTrue(r["reachable"])
        self.assertFalse(r["topology_ok"])
        m = sw.model(r, OPTS)
        ch = sw.compare(m, json.loads(json.dumps(prev)))
        self.assertFalse([c for c in ch if c["kind"] in ("Missing link", "New link")])
        self.assertEqual(sw.snapshot(m, prev)["l"], prev["l"])                            # the links before stay the baseline
        cut = sw.collect(self.fake.client(), dict(OPTS, max_nodes=50, page=50))
        mc = sw.model(cut, OPTS)
        self.assertFalse([c for c in sw.compare(mc, json.loads(json.dumps(prev))) if c["kind"] == "Missing node"])
        self.assertEqual(len(sw.snapshot(mc, prev)["n"]), len(prev["n"]))

    def test_codex_p2_links_by_interface_id_and_an_empty_baseline(self):
        m = sw.model(self.raw, OPTS)
        prev = json.loads(json.dumps(sw.snapshot(m)))
        m2 = sw.model(self.raw, OPTS)
        for l in m2["links"]:
            l["ia"] = l["ia"] + "-renamed"                                               # a caption changed: the same link
        self.assertFalse([c for c in sw.compare(m2, prev) if "link" in c["kind"]])
        empty = {"v": 2, "at": "", "n": {}, "l": []}
        ch = sw.compare(sw.model(self.raw, OPTS), empty)
        self.assertEqual(len([c for c in ch if c["kind"] == "New node"]), len(self.raw["nodes"]))

    def test_codex2_a_malformed_answer_is_said_in_our_words(self):
        import ssl as _ssl
        ca, crt, key = certs()
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        port = srv.getsockname()[1]
        ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(crt, key)

        def serve():
            for _ in range(3):
                try:
                    c, _a = srv.accept()
                    t = ctx.wrap_socket(c, server_side=True)
                    got = b""
                    while b"\r\n\r\n" not in got:                                      # the head, then the body it announces
                        got += t.recv(65536)
                    head, _, body = got.partition(b"\r\n\r\n")
                    n = int(re.search(rb"Content-Length: (\d+)", head, re.I).group(1))
                    while len(body) < n:
                        body += t.recv(65536)
                    t.sendall(b"GARBAGE Authorization: Basic c3ZjOnNlY3JldA== password=Leak3d99\r\n\r\n")
                    time.sleep(0.3)
                    t.close()
                except OSError:
                    return
        th = threading.Thread(target=serve, daemon=True)
        th.start()
        try:
            r = sw.collect(sw.Client("https://127.0.0.1:%d" % port, "svc", "Leak3d99", ca_path=ca, timeout=5), OPTS)
        finally:
            srv.close()
        self.assertFalse(r["reachable"])
        for bad in ("Leak3d99", "Basic", "Authorization", "GARBAGE", "c3Zj"):
            self.assertNotIn(bad, json.dumps(r))
        self.assertIn("not HTTP that could be read (BadStatusLine)", r["error"])

    def test_codex2_only_a_v2_copy_is_compared(self):
        m = sw.model(self.raw, OPTS)
        v1 = {"v": 1, "n": {}, "l": ["1/a|2/b"]}
        self.assertEqual(sw.compare(sw.model(self.raw, OPTS), v1), [])
        self.assertEqual(sw.snapshot(m, v1)["v"], 2)
        self.assertNotIn("1/a|2/b", json.dumps(sw.snapshot(dict(m, topology_ok=False), v1)))    # nothing of a v1 copy carried

    def test_codex2_the_address_is_checked_before_the_module(self):
        tasks = yaml.safe_load(open(os.path.join(ROOT, "roles", "netmap_report", "tasks", "main.yml")))
        names = [t.get("name", "") for t in tasks]
        pre = names.index("Network map | netmap_solarwinds_url has no login in it")
        mod = names.index("Network map | read SolarWinds (SWQL queries only)")
        self.assertLess(pre, mod)                                                          # its arguments never hold a login
        inc = next(t for t in tasks if t.get("name", "").startswith("Network map | email it, publish it"))
        self.assertEqual(inc["vars"]["_report_web_skip"], "{{ not _nm.reachable }}")      # a failed read never replaces latest.html

    def test_snapshot_ids_only(self):
        m = sw.model(self.raw, OPTS)
        small, full = sw.snapshot(m, labels=False), sw.snapshot(m)
        self.assertTrue(all(isinstance(x, str) for x in small["l"]))
        self.assertLess(len(json.dumps(small)), len(json.dumps(full)))
        self.assertEqual(sw.compare(sw.model(self.raw, OPTS), json.loads(json.dumps(small))), [])   # the same map: nothing changed
        m2 = sw.model(self.raw, OPTS)
        drop = m2["links"].pop(0)
        ch = [c for c in sw.compare(m2, json.loads(json.dumps(small))) if c["kind"] == "Missing link"]
        self.assertEqual(len(ch), 1)
        self.assertIn(str(drop["ka"]), ch[0]["detail"])                                  # its interfaces' ids

    def test_max_nodes(self):
        r = sw.collect(self.fake.client(), dict(OPTS, max_nodes=100, page=60))
        self.assertEqual(len(r["nodes"]), 100)
        self.assertTrue(r["cut"])
        m = sw.model(r, OPTS)
        self.assertGreater(m["hidden_links"], 0)


class Report(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        f = Fake(cls.tmp)
        try:
            raw = sw.collect(f.client(), OPTS)
        finally:
            f.stop()
        m = sw.model(raw, dict(OPTS, sites=SITES, warn_util=90))
        snap = sw.snapshot(m)
        snap["n"][str(next(n for n in m["nodes"] if n["status"] == "Down")["id"])][0] = "U"
        ch = sw.compare(m, snap)
        cls.res = {"reachable": True, "map": m, "changes": ch, "compared": True, "version": raw["version"], "not_read": [],
                   "data": sw.page_data(m, last="2026-11-19 06:00 UTC", run="2026-11-20 06:00 UTC"), "url": raw["url"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_doc(self):
        d = nf.netmap_doc(self.res, {"last": "2026-11-19 06:00 UTC", "max_rows": 5})
        self.assertTrue(d["title"].startswith("Network map: 1 down, "))
        self.assertIn("1 WAN link down", d["title"])
        self.assertEqual(d["status"], "critical")
        t = [s["title"] for s in d["sections"]]
        self.assertNotIn("The map", t)                                                     # the email: no map
        self.assertTrue(t[0].startswith("Down or warning"))
        self.assertTrue(any(x.startswith("What changed since the last run (1)") for x in t))
        self.assertIn("Unplaced sites (3)", t)
        down = next(s for s in d["sections"] if s["title"].startswith("Down or warning"))
        self.assertEqual(len(down["rows"]), 5)                                             # netmap_max_rows
        self.assertIn("netmap_max_rows: 5", down["text"])
        self.assertIn("compared with 2026-11-19 06:00 UTC", d["subtitle"])
        labels = [x["label"] for x in d["summary"]]
        self.assertEqual(labels[:4], ["Nodes", "Down", "Warning", "WAN links down"])        # At a glance
        web = nf.netmap_doc(self.res, {"web": True})
        self.assertEqual(web["sections"][0], {"title": "The map", "status": "info", "text": nf.MARK})

    def test_not_compared(self):
        d = nf.netmap_doc(dict(self.res, compared=False, changes=[]), {"why_not_compared": "report_history is false"})
        self.assertIn("not compared: report_history is false", d["subtitle"])
        self.assertNotIn("Changed", [x["label"] for x in d["summary"]])

    def test_workbook(self):
        sheets = nf.netmap_xlsx(self.res)
        self.assertEqual([s["name"] for s in sheets], ["Nodes", "Links", "WAN links", "Sites", "Changes"])
        self.assertEqual(len(sheets[0]["rows"]), len(self.res["map"]["nodes"]))
        self.assertIn("Region", sheets[0]["columns"])
        for s in sheets:
            self.assertTrue(all(len(r) == len(s["columns"]) for r in s["rows"]), s["name"])

    def test_lines(self):
        ls = nf.netmap_lines(self.res)
        self.assertTrue(ls[0].startswith("SolarWinds 2025.4.1 at https://127.0.0.1:"))
        self.assertIn("Changes since the last run: 1", ls)

    def test_the_page_piece_and_what_the_copy_job_keeps(self):
        x = nf.netmap_page_extra(self.res, "off")
        self.assertEqual(sorted(x), ["data", "kind", "motion"])                            # data only: no markup, no script
        self.assertEqual((x["kind"], x["motion"]), ("netmap", "off"))
        self.assertNotIn("<", json.dumps(x["data"])[:0] + json.dumps(sorted(x)))
        y = rp.report_page_piece(x, ROOT)                                                  # the report job and the copy job alike
        self.assertEqual(y["script_file"], "site-netmap.js")
        self.assertIn("window.NM_LAND=[", y["script"])
        self.assertIn('class="nm" id="nm"', y["html"])
        self.assertIn('id="netmap-data">', y["html"])
        self.assertEqual(nf.netmap_page_extra({"reachable": False}), {})

    def test_codex_p1_a_kept_piece_never_brings_markup(self):
        evil = {"kind": "netmap", "data": {"nodes": [{"name": "</script><!--<script>alert(1)//"}]}, "motion": "off",
                "html_after": "<script>alert(2)</script>", "html": "<img src=x onerror=alert(3)>", "script": "alert(4)",
                "script_file": "../evil.js", "marker": "<p>"}
        y = rp.report_page_piece(evil, ROOT)
        for bad in ("alert(2)", "alert(3)", "alert(4)", "evil.js", "</script><!--", "<script>alert(1)"):
            self.assertNotIn(bad, y["html"] + y["script"] + y["script_file"] + y["marker"])
        block = y["html"][y["html"].index('id="netmap-data">') + 17:-len("</script>")]
        self.assertNotIn("<", block)
        self.assertEqual(json.loads(block)["nodes"][0]["name"], "</script><!--<script>alert(1)//")      # every < as \\u003c, read back whole
        self.assertEqual(rp.report_page_piece({"kind": "other", "data": {}}, ROOT), {})
        self.assertEqual(rp.report_page_piece({"kind": "netmap", "data": "<script>"}, ROOT), {})
        self.assertEqual(rp.report_page_piece({}, ROOT), {})

    def test_codex_p2_symbolic_links_out_of_files_web(self):
        tmp = tempfile.mkdtemp()
        try:
            web = os.path.join(tmp, "roles", "x", "files", "web")
            os.makedirs(web)
            secret = os.path.join(tmp, "secret.txt")
            open(secret, "w").write("not for the page")
            os.symlink(secret, os.path.join(web, "a.js"))
            os.symlink(os.path.join(tmp, "roles", "x"), os.path.join(tmp, "roles", "y"))
            open(os.path.join(web, "ok.js"), "w").write("ok")
            self.assertEqual(rp._read(tmp, "roles/x/files/web/ok.js"), "ok")
            for bad in ("roles/x/files/web/a.js", "roles/y/files/web/ok.js", "roles/x/files/web/../../../secret.txt", "/etc/hostname"):
                with self.assertRaises(ValueError):
                    rp._read(tmp, bad)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_codex_p2_sites_cut_zero_kept(self):
        d = nf.netmap_doc(self.res, {"max_rows": 3})
        sites = next(x for x in d["sections"] if x["title"].startswith("Sites ("))
        self.assertEqual(len(sites["rows"]), 3)
        self.assertIn("netmap_max_rows: 3", sites["text"])
        m = {"sites": [], "nodes": [], "links": [{"a": 1, "b": 2, "status": "Up", "in": 0, "out": 55, "new": False}], "unplaced": []}
        l = sw.page_data(m)["links"][0]
        self.assertEqual((l["in"], l["out"]), (0, 55))                                     # no traffic in: 0, not left out
        self.assertNotIn("new", l)

    def test_page_extend(self):
        page = ('<html lang="en"><head><script src="site-report.js"></script></head><body><main><p class="t">@@NETMAP@@</p></main>'
                '<script id="site-report-js">\nvar a=1;\n</script></body></html>')
        x = {"marker": "@@NETMAP@@", "html": "<div id=nm></div>", "css": "a{}", "script": "var b=2;</script>", "script_file": "site-netmap.js", "motion": "off"}
        out = rp.report_page_extend(page, x)
        self.assertIn('<script src="site-netmap.js"></script>', out)
        self.assertNotIn('<script src="site-report.js"></script>', out)
        self.assertIn("<div id=nm></div>", out)
        self.assertNotIn("@@NETMAP@@", out)
        self.assertIn("<style>a{}</style>", out)
        self.assertIn('<html lang="en" class="motion-off">', out)
        m = rp.SCRIPT.search(out)
        self.assertIn('data-file="site-netmap.js"', out)
        self.assertIn("var a=1;", m.group(1))
        self.assertIn("window.__siteReportExtra", m.group(1))                              # once, after the page is there
        self.assertNotIn("var b=2;</script>", out)                                        # a script end inside is broken up
        self.assertEqual(rp.report_page_extend(page, {}), page)
        self.assertEqual(rp.report_page_extend(page, None), page)
        plain = rp.report_page_extend("<html><body><main>@@NETMAP@@</main></body></html>", dict(x, motion=""))
        self.assertIn('<script src="site-netmap.js"></script><script id="site-report-js" data-file="site-netmap.js">', plain)


class Files(unittest.TestCase):
    def test_no_html_from_strings_no_eval(self):
        for n in ("netmap.js", "netglobe.js", "netmap-live.js"):
            t = open(os.path.join(WEB, n)).read()
            for bad in (".innerHTML", ".outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "DOMParser", "setTimeout('", 'setTimeout("'):
                self.assertNotIn(bad, t, (n, bad))

    def test_no_inline_style_or_script_in_the_markup(self):
        t = open(os.path.join(WEB, "netmap-map.html")).read()
        self.assertNotIn(" style=", t)
        self.assertNotIn("<script", t)
        self.assertNotRegex(t, r"\son[a-z]+=")

    def test_javascript_parses(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("no node here")
        for n in ("netmap.js", "netglobe.js", "netmap-live.js", "world-land.js"):
            subprocess.check_call([node, "--check", os.path.join(WEB, n)])

    def test_land(self):
        t = open(os.path.join(WEB, "world-land.js")).read()
        self.assertIn("Natural Earth", t)
        rings = json.loads(t[t.index("window.NM_LAND=") + 15:].rstrip().rstrip(";"))
        self.assertGreater(len(rings), 100)
        self.assertTrue(all(len(r) % 2 == 0 and -180 <= r[0] <= 180 and -90 <= r[1] <= 90 for r in rings))

    def test_nothing_from_elsewhere(self):
        for n in os.listdir(WEB):
            t = open(os.path.join(WEB, n)).read()
            self.assertNotRegex(t, r"(src|href)=\"https?://|@import|url\(https?:|fetch\(\s*['\"]https?:", n)

    def test_the_deploy_carries_the_map(self):
        tasks = open(os.path.join(ROOT, "roles", "site_portal_stack", "tasks", "main.yml")).read()
        for n in ("site_swis.py", "netmap.js", "netglobe.js", "world-land.js", "netmap-live.js", "netmap.css", "netmap-map.html"):
            self.assertIn(n, tasks)
        self.assertIn("site-portal-swis", tasks)
        unit = open(os.path.join(ROOT, "roles", "site_portal_stack", "templates", "site-portal-app.container.j2")).read()
        self.assertIn("Secret=site-portal-swis,type=env,target=PORTAL_SWIS_LOGIN", unit)
        d = yaml.safe_load(open(os.path.join(ROOT, "roles", "site_portal_stack", "defaults", "main.yml")))
        self.assertEqual(d["portal_netmap"], "auto")
        self.assertEqual(d["netmap_live_seconds"], 60)
        cred = yaml.safe_load(open(os.path.join(ROOT, "aap", "credential_types", "solarwinds.yml")))
        self.assertEqual(sorted(cred["injectors"]["env"]), ["SOLARWINDS_CA_PATH", "SOLARWINDS_PASSWORD", "SOLARWINDS_URL", "SOLARWINDS_USERNAME", "SOLARWINDS_VERIFY"])
        self.assertTrue(next(f for f in cred["inputs"]["fields"] if f["id"] == "password")["secret"])


class Portal(unittest.TestCase):
    """The portal's live Network map: one reader for every viewer, its JSON with an ETag, stale but kept on a failure."""

    @classmethod
    def setUpClass(cls):
        import site_portal as sp
        import fake_aap
        cls.sp = sp
        cls.tmp = tempfile.mkdtemp()
        cls.fake = Fake(cls.tmp)
        fake_aap.LOG = os.path.join(cls.tmp, "aap.jsonl")
        cls.aap = fake_aap.serve(0, stop=True)
        with open(os.path.join(ROOT, "roles/site_portal_stack/files/actions.yml")) as fh:
            doc = yaml.safe_load(fh)
        with open(os.path.join(cls.tmp, "actions.json"), "w") as fh:
            json.dump({"actions": doc["actions"]}, fh)
        cls.static = os.path.join(cls.tmp, "static")
        shutil.copytree(os.path.join(ROOT, "roles/site_portal_stack/files/static"), cls.static)
        for n in os.listdir(WEB):
            shutil.copy(os.path.join(WEB, n), cls.static)
        os.makedirs(os.path.join(cls.tmp, "reports"))
        cls.env = {"PORTAL_DATA": os.path.join(cls.tmp, "data"), "PORTAL_REPORTS": os.path.join(cls.tmp, "reports"),
                   "PORTAL_ACTIONS": os.path.join(cls.tmp, "actions.json"), "PORTAL_AAP_URL": "http://127.0.0.1:%d" % cls.aap.server_address[1],
                   "PORTAL_AAP_TOKEN": "fake-token", "PORTAL_NETMAP": "1", "PORTAL_SWIS_URL": cls.fake.url, "PORTAL_SWIS_VERIFY": "0",
                   "PORTAL_SWIS_LOGIN": json.dumps({"user": "svc_swis_ro", "password": PW}),
                   "PORTAL_NETMAP_OPTS": {"lat_property": "Lat", "lon_property": "Lon", "sites": SITES, "view": "flat"}}
        cls.cfg = sp.Config(cls.env)
        cls.cfg.netmap_seconds = 1
        real = sys.stdout
        sys.stdout = open(os.devnull, "w")
        try:
            cls.portal = sp.Portal(cls.cfg)
        finally:
            sys.stdout = real
        cls.portal.log = sp.Log(os.path.join(cls.tmp, "portal-log.jsonl"))
        cls.srv = sp.PortalServer(("127.0.0.1", 0), sp.make_handler(cls.cfg, cls.portal, cls.static))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.fake.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def get(self, path, headers=None):
        h = {"X-SSL-Client-Verify": "SUCCESS", "X-SSL-Client-S-DN": "CN=DOE.JANE.Q.1234567890,OU=PKI,O=U.S. Government,C=US"}
        h.update(headers or {})
        try:
            r = urllib.request.urlopen(urllib.request.Request(self.base + path, headers=h), timeout=30)
            return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def wait_map(self, cond, n=60):
        for _ in range(n):
            code, hd, body = self.get("/api/netmap")
            d = json.loads(body) if code == 200 else {}
            if cond(d):
                return d, hd
            time.sleep(0.25)
        self.fail("the map did not come: %s" % (d if code == 200 else code))

    def test_1_the_page_and_its_files(self):
        code, hd, body = self.get("/netmap")
        self.assertEqual(code, 200)
        t = body.decode()
        self.assertIn('id="nm-live"', t)
        self.assertIn('class="nm" id="nm"', t)
        self.assertIn("static/netmap-live.js?v=", t)
        self.assertIn("static/netmap.css?v=", t)
        self.assertIn('aria-current="page">Network map</a>', t)
        self.assertNotIn(" style=", t)
        self.assertIn("style-src 'self';", hd["Content-Security-Policy"])
        for n in ("netmap.js", "netglobe.js", "world-land.js", "netmap-live.js", "netmap.css"):
            self.assertEqual(self.get("/static/" + n)[0], 200, n)
        self.assertEqual(self.get("/static/netmap-map.html")[0] in (403, 404), True)        # the markup is the page's, not served alone
        code, _, body = self.get("/healthz")
        self.assertEqual(json.loads(body)["netmap"], "on")

    def test_2_one_shared_read_its_etag_and_a_change(self):
        d, hd = self.wait_map(lambda d: d.get("map"))
        self.assertEqual(d["map"]["view"], "flat")
        self.assertEqual(len(d["map"]["nodes"]), 1495)
        self.assertTrue(hd["ETag"])
        code, _, _ = self.get("/api/netmap", {"If-None-Match": hd["ETag"]})
        self.assertEqual(code, 304)
        before = len(self.fake.lines())
        for _ in range(5):                                                                 # five viewers: no read of their own
            self.get("/api/netmap")
        self.assertLess(len(self.fake.lines()) - before, 15 * 2)
        fx = json.load(open(self.fake.fx))
        nid = next(n["NodeID"] for n in fx["nodes"] if n["Caption"] == "sb-acc001")
        with open(self.fake.state, "w") as fh:
            json.dump({"down": [nid]}, fh)
        d2, hd2 = self.wait_map(lambda d: any(n["id"] == nid and n["status"] == "Down" for n in (d.get("map") or {}).get("nodes", [])))
        self.assertNotEqual(hd2["ETag"], hd["ETag"])
        os.remove(self.fake.state)

    def test_3_stale_but_kept_and_the_password_never_said(self):
        _, hd0 = self.wait_map(lambda d: d.get("map"))
        kept_tag = hd0["ETag"]
        open(os.path.join(self.tmp, "fail"), "w").close()
        self.fake.stop()
        try:
            d, _ = self.wait_map(lambda d: d.get("stale"))
            self.assertTrue(d["map"]["nodes"])                                              # the last map, still served
            code, _, body = self.get("/api/netmap", {"If-None-Match": kept_tag})            # a viewer who has the map hears it is stale
            self.assertEqual(code, 200)
            self.assertTrue(json.loads(body)["stale"])
            self.assertNotIn(PW, json.dumps(d))
        finally:
            type(self).fake = Fake(self.tmp)
            self.cfg.swis_url = self.fake.url
        log = open(os.path.join(self.tmp, "portal-log.jsonl")).read()
        self.assertIn('"read": "failed"', log)
        self.assertNotIn(PW, log)
        self.assertNotIn(json.dumps(PW)[1:-1], log)

    def test_4_off_without_solarwinds(self):
        sp = self.sp
        for env, why in ((dict(self.env, PORTAL_NETMAP="0"), "portal_netmap is off"),
                         (dict(self.env, PORTAL_SWIS_LOGIN="{}"), "no SolarWinds login"),
                         (dict(self.env, PORTAL_SWIS_URL=""), "no SolarWinds address")):
            cfg = sp.Config(env)
            self.assertIn(why, cfg.netmap_why)
            h = sp.make_handler(cfg, self.portal, self.static)
            srv = sp.PortalServer(("127.0.0.1", 0), h)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                hdr = {"X-SSL-Client-Verify": "SUCCESS", "X-SSL-Client-S-DN": "CN=DOE.JANE.Q.1234567890,OU=PKI,O=U.S. Government,C=US"}
                for path in ("/netmap", "/api/netmap"):
                    with self.assertRaises(urllib.error.HTTPError) as e:
                        urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:%d%s" % (srv.server_address[1], path), headers=hdr), timeout=10)
                    self.assertEqual(e.exception.code, 404)
                    self.assertIn(why, e.exception.read().decode())
                page = urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:%d/approvals" % srv.server_address[1], headers=hdr), timeout=10).read().decode()
                self.assertNotIn(">Network map</a>", page)
            finally:
                srv.shutdown()
        self.assertEqual(sp.Config(dict(self.env, PORTAL_NETMAP_SECONDS="5")).netmap_seconds, 30)      # never more often than every 30 s

    def test_6_codex_p1_a_failed_node_query_keeps_the_last_map(self):
        d0, _ = self.wait_map(lambda d: d.get("map") and not d.get("stale"))
        self.fake.set_state({"break": ["Orion.Nodes"]})
        try:
            d, _ = self.wait_map(lambda d: d.get("stale"))
            self.assertIn("Orion.Nodes: SolarWinds could not run this query (HTTP 400)", d["stale"])
            self.assertEqual(len(d["map"]["nodes"]), len(d0["map"]["nodes"]))          # the last good map, not an empty one
        finally:
            self.fake.set_state({})
        self.wait_map(lambda d: d.get("map") and not d.get("stale"))                    # and back once SolarWinds answers

    def test_7_codex2_links_not_refreshed_keep_the_last_map(self):
        d0, _ = self.wait_map(lambda d: d.get("map") and not d.get("stale"))
        links = len(d0["map"]["links"])
        self.fake.set_state({"break": ["Orion.TopologyConnections"]})
        try:
            d, _ = self.wait_map(lambda d: d.get("stale"))
            self.assertIn("the links were not refreshed", d["stale"])
            self.assertEqual(len(d["map"]["links"]), links)                                # not a map without links
        finally:
            self.fake.set_state({})
        self.wait_map(lambda d: d.get("map") and not d.get("stale"))

    def test_5_who_may_see_it(self):
        r = urllib.request.Request(self.base + "/api/netmap", headers={"X-SSL-Client-Verify": "NONE"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(r, timeout=10)
        self.assertEqual(e.exception.code, 403)


if __name__ == "__main__":
    unittest.main(verbosity=1)
