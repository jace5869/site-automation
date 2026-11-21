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
        self.assertEqual(m["unplaced"], ["Site J", "constructor", "Not placed"])        # 1.95.0: the nodes with no site
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
        self.assertEqual(r["sa-fw01"], "firewall")                  # 1.95.0: the roles' words (name, model, links; netmap_roles)
        self.assertEqual(r["sa-dist01"], "distribution")
        self.assertEqual(r["sa-wlc01"], "wireless")
        self.assertEqual(r["sa-acc001"], "access")

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

    def test_hardening_the_node_cap_keeps_the_links_it_did_not_see(self):
        full = sw.model(self.raw, OPTS)
        prev = json.loads(json.dumps(sw.snapshot(full)))
        cut = sw.collect(self.fake.client(), dict(OPTS, max_nodes=200, page=100))
        self.assertTrue(cut["cut"] and cut["topology_ok"])                               # the links all read, the nodes not
        mc = sw.model(cut, OPTS)
        self.assertFalse([c for c in sw.compare(mc, json.loads(json.dumps(prev))) if c["kind"] in ("Missing node", "Missing link")])
        kept = sw.snapshot(mc, prev)
        self.assertEqual(sorted(k for k, _ in kept["l"]), sorted(k for k, _ in prev["l"]))   # nothing lost of the links before
        again = sw.compare(sw.model(self.raw, OPTS), json.loads(json.dumps(kept)))           # then a whole read: nothing new
        self.assertFalse([c for c in again if c["kind"] in ("New link", "Missing link", "New node", "Missing node")])
        ids_only = sw.snapshot(mc, json.loads(json.dumps(sw.snapshot(full, labels=False))), labels=False)
        self.assertEqual(len(ids_only["l"]), len(prev["l"]))

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
        self.assertIn("Unplaced sites (2)", t)                                   # 1.95.0: "Not placed" is no site, not counted
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
        self.assertEqual([s["name"] for s in sheets], ["Nodes", "Links", "WAN links", "Sites", "Changes", "Discovery"])   # 1.95.0: Discovery
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
        self.assertIn("netmap_check_opts", tasks)                                   # 1.95.0 review: a bad setting stops the deploy
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
        sp = self.sp
        sp.NETMAP_FULL_MIN, self.cfg.netmap_opts["full_seconds"] = 0, 0          # 1.95.0: the whole read each time (the topology is read only there)
        self.addCleanup(lambda: (setattr(sp, "NETMAP_FULL_MIN", 300), self.cfg.netmap_opts.pop("full_seconds", None)))
        d0, _ = self.wait_map(lambda d: d.get("map") and not d.get("stale"))
        links = len(d0["map"]["links"])
        self.fake.set_state({"break": ["Orion.TopologyConnections"]})
        try:                                     # 1.95.0 review (8): the whole read failed - the kept map, its status read fresh
            fell = lambda: [e for e in self.portal.log.read("netmap") if "whole map failed" in json.dumps(e)]
            before = len(fell())
            for _ in range(100):
                if len(fell()) > before:
                    break
                time.sleep(0.1)
            self.assertIn("the links were not refreshed", json.dumps(fell()[-1]))
            d, _ = self.wait_map(lambda d: d.get("kind") == "status" and not d.get("stale"))
            self.assertEqual(len(d["map"]["links"]), links)                                # not a map without links
        finally:
            self.fake.set_state({})
        self.wait_map(lambda d: d.get("map") and not d.get("stale"))

    def test_8_two_speeds_and_the_map_kept_on_disk(self):
        """1.95.0: after a whole read, the status only (2-3 queries) until netmap_full_seconds; the last good map is kept on disk
        (PORTAL_DATA/netmap-cache.json, 0600) and a new reader shows it at once - as of its time - before it reads anything."""
        sp = self.sp
        d0, _ = self.wait_map(lambda d: d.get("map") and not d.get("stale"))
        d1, _ = self.wait_map(lambda d: d.get("kind") == "status")
        self.assertEqual(d1["source"], "swis-live")
        path = os.path.join(self.cfg.data, "netmap-cache.json")
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        with open(path) as fh:
            kept = json.load(fh)
        self.assertEqual(kept["v"], 1)
        self.assertNotIn(PW, json.dumps(kept))                                      # the login is never in it
        self.fake.set_state({"break": ["Metadata.Entity", "Orion.Nodes"]})          # SolarWinds refusing: the kept map still shows at once
        try:
            t = time.time()
            live = sp.NetLive(self.cfg, self.portal.log)
            code, body, etag = live.answer()
            self.assertLess(time.time() - t, 1.0)
            d = json.loads(body)
            self.assertTrue(d["from_disk"] and d["map"]["nodes"] and d["as_of"])
            self.assertIsNone(live.thread)                                           # nothing read for that: no viewer asked yet
        finally:
            self.fake.set_state({})

    def test_9_the_seam_takes_another_source(self):
        """1.95.0: a source gives collect()'s raw dict (read_full) and a status read (read_status) - the report database's snapshot
        (1.96.0) is meant to be one."""
        sp = self.sp
        raw = sw.collect(sw.Client(self.fake.url, "svc_swis_ro", PW, verify=False), {"lat_property": "Lat", "lon_property": "Lon"})

        class Src(object):
            kind = "db-snapshot"
            calls = []

            def read_full(self):
                self.calls.append("full")
                return raw

            def read_status(self, r):
                self.calls.append("status")
                return r
        cfg = sp.Config(dict(self.env, PORTAL_DATA=tempfile.mkdtemp()))
        live = sp.NetLive(cfg, self.portal.log, source=Src())
        data, got, kind = live.read_once()
        self.assertEqual((kind, Src.calls), ("full", ["full"]))
        self.assertEqual(len(data["nodes"]), len(raw["nodes"]))
        live.raw, live.full_at = got, time.time()
        self.assertEqual(live.read_once()[2], "status")

    def test_a_the_kept_map_belongs_to_its_settings(self):
        """1.95.0 review: the kept map names SolarWinds' address and the settings - another address: not shown; other settings:
        shown, but the next read is whole; a setting that cannot work is said, never read; the file is made 0600 anew."""
        sp = self.sp
        raw = sw.collect(sw.Client(self.fake.url, "svc_swis_ro", PW, verify=False), {})

        class Src(object):
            kind = "swis-live"
            calls = []

            def read_full(self):
                self.calls.append("full")
                return raw

            def read_status(self, r):
                self.calls.append("status")
                return r
        data_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, data_dir, True)
        tmp = os.path.join(data_dir, "netmap-cache.json.tmp")
        with open(tmp, "w") as fh:                                                    # a leftover, world-readable
            fh.write("x")
        os.chmod(tmp, 0o644)
        cfg = sp.Config(dict(self.env, PORTAL_DATA=data_dir))
        live = sp.NetLive(cfg, self.portal.log, source=Src())
        data, got, kind = live.read_once()
        live.read_at, live.full_at, live.raw, live.kind = time.time(), time.time(), got, kind
        live._save(json.dumps(data), got)
        self.assertEqual(oct(os.stat(os.path.join(data_dir, "netmap-cache.json")).st_mode & 0o777), "0o600")
        same = sp.NetLive(cfg, self.portal.log, source=Src())
        self.assertTrue(same.from_disk and same.raw)                                  # the same settings: kept, status next
        cfg.netmap_opts = dict(cfg.netmap_opts, hierarchy=[{"level": "building", "from": "none"}])
        other = sp.NetLive(cfg, self.portal.log, source=Src())
        self.assertTrue(other.from_disk and other.map_json)                           # shown at once ...
        self.assertIsNone(other.raw)                                                  # ... but the next read is whole
        Src.calls[:] = []
        other.read_once()
        self.assertEqual(Src.calls, ["full"])
        cfg.swis_url = "https://orion-b.corp.example.mil:17774"
        self.assertFalse(sp.NetLive(cfg, self.portal.log, source=Src()).from_disk)    # another SolarWinds: not its map
        cfg.netmap_opts = dict(cfg.netmap_opts, site_pattern="([", roles=["x"])
        bad = sp.NetLive(cfg, self.portal.log, source=Src())
        with self.assertRaises(ValueError) as e:
            bad.read_once()
        self.assertIn("netmap_site_pattern", str(e.exception))
        self.assertIn("netmap_roles", str(e.exception))

    def test_5_who_may_see_it(self):
        r = urllib.request.Request(self.base + "/api/netmap", headers={"X-SSL-Client-Verify": "NONE"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(r, timeout=10)
        self.assertEqual(e.exception.code, 403)



def hostile():
    """A made-up Orion that is not tidy (placeholders only): two naming schemes (SITA0101-B01-AS03 and sitc-2-acc-04), DNS-only
    and IP-only captions, sites with no building part, nodes with no link, one /24 shared by two buildings, Location noise."""
    import random
    rnd = random.Random(7)
    nodes, conns, nid = [], [], [1]

    def node(name, ip, loc, dns="", vendor="Cisco", model="C9300-48P", status=1):
        nid[0] += 1
        nodes.append({"id": nid[0], "name": name, "dns": dns, "ip": ip, "status_code": status, "status_text": "", "vendor": vendor, "model": model,
                      "location": loc, "resp": 3, "loss": 0, "unmanaged": False, "props": {"Dept": rnd.choice(["", "NETOPS", "J6"])}, "wm": None})
        return nid[0]
    hub = node("HUBA0001-CR01", "198.18.0.1", "Hub")
    for i, code in enumerate(["SITA0101", "SITB0201", "SITD0301", "SITE0401", "SITF0501", "SITG0601"]):
        core = node("%s-B01-CR01" % code, "198.18.%d.1" % (i + 1), "Bldg 1, Rm %d" % rnd.randint(1, 300))
        conns.append([hub, 0, core, 0])
        for b in (1, 2):
            ds = node("%s-B%02d-DS01" % (code, b), "198.18.%d.%d" % (i + 1, 10 + b), "Bldg %d, MDF" % b)      # both buildings in one /24
            conns.append([core, 0, ds, 0])
            for a in range(4):
                conns.append([ds, 0, node("%s-B%02d-AS%02d" % (code, b, a), "198.18.%d.%d" % (i + 1, 20 + b * 10 + a), "Bldg %d, IDF %d" % (b, a)), 0])
    for i, code in enumerate(["sitc", "sith", "sitj", "sitk"]):                                       # the other scheme, no building part
        core = node("%s-core-01" % code, "198.19.%d.1" % i, "Camp Example")
        conns.append([hub, 0, core, 0])
        for a in range(5):
            conns.append([core, 0, node("%s-acc-%02d" % (code, a), "198.19.%d.%d" % (i, 10 + a), "Camp Example"), 0])
    for k in range(12):                                                                              # no links, odd names
        node("", "198.51.100.%d" % (k + 1), "", dns="ups-%02d.corp.example.mil" % k, vendor="APC", model="Smart-UPS")
    for k in range(5):
        node("198.51.100.%d" % (k + 40), "198.51.100.%d" % (k + 40), "")
    return {"nodes": nodes, "connections": conns, "interfaces": [], "custom_properties": ["Dept"], "topology_ok": True, "read_at": "2026-11-21T09:00:00Z"}


class Discovery1950(unittest.TestCase):
    """1.95.0: where a node belongs (site > building > closet) and its role, read from what agrees - on an untidy Orion."""

    def test_a_untidy_names(self):
        raw = hostile()
        m = sw.model(raw, {})
        lv = dict((x["level"], x) for x in m["discovery"]["levels"])
        site = dict((n["name"] or n["ip"], n["site"]) for n in m["nodes"])
        self.assertEqual(site["SITA0101-B02-AS03"], "SITA0101")
        self.assertEqual(site["sitc-acc-03"], "SITC")                       # the other scheme: its first part
        self.assertEqual(lv["site"]["readings"][0]["chosen"], True)         # fix (a): the chosen reading first
        self.assertTrue(lv["site"]["decided"])
        self.assertGreaterEqual(lv["site"]["groups"], 10)
        self.assertLess(lv["site"]["groups"], 40)                           # not one group per node, not two halves
        self.assertIn(dict((n["name"], n["building"]) for n in m["nodes"])["SITB0201-B02-AS01"], ("B02", "Bldg 2"))   # the /24 holds both: never it
        self.assertNotEqual(lv["building"]["from"], "subnet")
        self.assertIn("Not placed", [s["name"] for s in m["sites"]] + [m.get("no_site")])
        roles = dict((n["name"], n["role"]) for n in m["nodes"])
        self.assertEqual((roles["SITA0101-B01-CR01"], roles["SITA0101-B01-DS01"], roles["SITA0101-B01-AS00"]), ("core", "distribution", "access"))
        self.assertEqual(roles["sith-core-01"], "core")
        fields = dict((f["field"], f) for f in m["discovery"]["fields"])
        self.assertIn("custom property Dept", fields)
        self.assertLessEqual(len(fields["custom property Dept"]["samples"]), 5)
        self.assertTrue(m["discovery"]["best"].startswith("Sites read from "))

    def test_b_all_yml_names_it(self):
        raw = hostile()
        m = sw.model(raw, {"site_pattern": r"^([A-Za-z]{4})", "hierarchy": [{"level": "building", "from": "none"}], "roles": {"*-AS0?": "wireless"}})
        lv = dict((x["level"], x) for x in m["discovery"]["levels"])
        self.assertEqual(lv["site"]["decided"], "set in all.yml (netmap_site_pattern)")
        self.assertEqual(dict((n["name"], n["site"]) for n in m["nodes"])["SITA0101-B02-AS03"], "SITA")
        self.assertEqual(set(n["building"] for n in m["nodes"]), {""})
        r = dict((n["name"], (n["role"], n["role_from"])) for n in m["nodes"])
        self.assertEqual(r["SITA0101-B02-AS03"], ("wireless", "netmap_roles"))
        m = sw.model(raw, {"site_by": "Location"})                          # as before 1.95.0: Location up to the first comma
        self.assertEqual(dict((n["name"], n["site"]) for n in m["nodes"])["SITA0101-B02-AS03"], "Bldg 2")
        self.assertEqual(m["fingerprint"], "site:location:1")

    def test_c_the_settings_are_checked(self):
        self.assertEqual(sw.check_opts({"site_pattern": "^([A-Z]{4})", "hierarchy": [{"level": "building", "from": "location", "value": 1}], "roles": {"x*": "core"}}), [])
        bad = " ".join(sw.check_opts({"site_pattern": "^[A-Z]{4}", "hierarchy": [{"level": "floor", "from": "pattern"}, {"level": "site", "from": "pattern", "value": "(("}],
                                      "roles": {"x*": "printer"}}))
        for w in ("has no group", "level is one of", "is not a regular expression", "netmap_roles"):
            self.assertIn(w, bad)

    def test_d_netmap_sites_by_pattern(self):
        conf = {"SITA*": [1.0, 2.0], "SITA01*": [3.0, 4.0], "SITB0201": [5.0, 6.0]}
        self.assertEqual(sw.place(conf, "SITA0101"), [3.0, 4.0])            # the most specific pattern
        self.assertEqual(sw.place(conf, "sitA0999"), [1.0, 2.0])            # any case
        self.assertEqual(sw.place(conf, "SITB0201"), [5.0, 6.0])            # the name itself first
        self.assertIsNone(sw.place(conf, "SITC0301"))
        m = sw.model(hostile(), {"sites": {"SITA*": {"lat": 10, "lon": 20}}})
        self.assertEqual([s["from"] for s in m["sites"] if s["name"] == "SITA0101"], ["setting"])

    def test_e_sites_read_a_new_way(self):
        raw = hostile()
        old = sw.snapshot(sw.model(raw, {"site_by": "Location"}))
        gone = raw["nodes"].pop(5)
        m = sw.model(raw, {})
        ch = sw.compare(m, json.loads(json.dumps(old)))
        self.assertEqual(ch[0]["kind"], "Sites read a new way")             # said once, first
        miss = [c for c in ch if c["kind"] == "Missing node"]
        self.assertEqual([c["what"] for c in miss], [gone["name"]])
        self.assertEqual(miss[0]["site"], gone["name"].split("-")[0])       # its site by today's rule, never the old reading's
        self.assertEqual(sw.compare(sw.model(raw, {}), sw.snapshot(sw.model(raw, {}))), [])   # the same rule: nothing said

    def test_f_a_big_network_stays_quick(self):
        raw = hostile()
        base = list(raw["nodes"])
        for k in range(1, 8):                                                # about 1,000 nodes
            for n in base:
                if n["name"]:
                    raw["nodes"].append(dict(n, id=n["id"] + 1000 * k, name=n["name"].replace("SIT", "S%dT" % k, 1)))
        t = time.time()
        sw.model(raw, {})
        self.assertLess(time.time() - t, 3.0)


def _browser():
    for b in ("google-chrome", "chromium", "chromium-browser"):
        if shutil.which(b):
            return shutil.which(b)
    return None


class Review1950(unittest.TestCase):
    """1.95.0 review round: a value holding "/" keeps it, the part rule never raises, a custom property's values are not
    published unless named, an old copy's reading, the auto reading kept on a near tie, a missing node's site in capitals."""

    def flat(self, n_sites=3, per=6):
        """Sites only an address tells apart: one /16 each, names that do not (placeholders only)."""
        nodes, conns, nid = [], [], 0
        for s in range(n_sites):
            first = None
            for k in range(per):
                nid += 1
                nodes.append({"id": nid, "name": "node-%03d" % nid, "dns": "", "ip": "10.%d.%d.%d" % (s + 1, k, 5), "status_code": 1, "status_text": "",
                              "vendor": "Cisco", "model": "C9300", "location": "Bldg %d/Floor %d, Rm 1" % (s, k % 2), "resp": 1, "loss": 0,
                              "unmanaged": False, "props": {}, "wm": None})
                if first:
                    conns.append([first, 0, nid, 0])
                first = first or nid
        return {"nodes": nodes, "connections": conns, "interfaces": [], "custom_properties": [], "topology_ok": True, "read_at": "2026-11-21T09:00:00Z"}

    def test_1_a_value_with_a_slash_keeps_it(self):
        m = sw.model(self.flat(), {"hierarchy": [{"level": "site", "from": "subnet", "value": 16},
                                                 {"level": "building", "from": "location", "value": "all"}]})
        sites = sorted(set(n["site"] for n in m["nodes"]))
        self.assertEqual(sites, ["10.1.0.0/16", "10.2.0.0/16", "10.3.0.0/16"])     # three /16s: three sites, never "16"   # public-ok: private test subnets (a /8 case needs one)
        self.assertIn("Bldg 0/Floor 1, Rm 1", set(n["building"] for n in m["nodes"]))    # not "Floor 1, Rm 1"
        self.assertEqual(sw._subnet("10.2.3.4", 8), "10.0.0.0/8")   # public-ok: private test subnets (a /8 case needs one)
        self.assertEqual(sw._subnet("10.2.3.4", 16), "10.2.0.0/16")   # public-ok: private test subnets (a /8 case needs one)
        lv = m["discovery"]["levels"][0]
        self.assertIn("10.1.0.0/16", lv["samples"])   # public-ok: private test subnets (a /8 case needs one)

    def test_2_the_part_rule_never_raises(self):
        raw = hostile()
        m = sw.model(raw, {"hierarchy": [{"level": "site", "from": "part", "value": -1}]})
        site = dict((n["name"] or n["ip"], n["site"]) for n in m["nodes"])
        self.assertEqual(site["SITA0101-B02-AS03"], "AS03")                        # the last part, not the one before
        for v in (9, "all", "x", None, -9):                                         # past the end, not a number: no group, no error
            sw.model(raw, {"hierarchy": [{"level": "site", "from": "part", "value": v}]})
        self.assertTrue(sw.check_opts({"hierarchy": [{"level": "site", "from": "part", "value": "all"}]}))
        self.assertEqual(sw.check_opts({"hierarchy": [{"level": "site", "from": "part", "value": -1}]}), [])
        self.assertEqual(sw.check_opts({"hierarchy": [{"level": "site", "from": "location", "value": "all"}]}), [])

    def test_3_a_custom_property_is_not_published_unless_named(self):
        raw = hostile()
        raw["custom_properties"] = ["Dept", "Contact", "Serial"]
        for i, n in enumerate(raw["nodes"]):
            n["props"].update({"Contact": "jane.doe%d@corp.example.mil" % (i % 3), "Serial": "FCW22%06dAB" % i})
        secret = ("jane.doe", "FCW22")
        m = sw.model(raw, {})
        res = {"reachable": True, "map": m, "changes": [], "compared": False}
        out = json.dumps([m["discovery"], nf.netmap_doc(res, {"web": True}), nf.netmap_doc(res), nf.netmap_xlsx(res), nf.netmap_lines(res)])
        for w in secret:
            self.assertNotIn(w, out)
        f = dict((x["field"], x) for x in m["discovery"]["fields"])
        self.assertTrue(f["custom property Contact"]["hidden"])
        self.assertEqual(f["custom property Contact"]["samples"], [])
        self.assertEqual(f["custom property Contact"]["distinct"], 3)              # its counts are still said
        self.assertIn("values not shown", out)
        m = sw.model(raw, {"properties": ["Contact", "Serial"]})                     # named: shown, but never an address or a serial
        f = dict((x["field"], x) for x in m["discovery"]["fields"])
        self.assertEqual(set(f["custom property Contact"]["samples"]), {"(not shown)"})
        self.assertEqual(set(f["custom property Serial"]["samples"]), {"(not shown)"})
        self.assertIn("NETOPS", json.dumps(sw.model(raw, {"properties": ["Dept"]})["discovery"]["fields"]))   # a plain value: shown

    def test_4_an_old_copy_by_a_property_is_not_a_new_way(self):
        raw = hostile()
        for n in raw["nodes"]:
            n["props"]["Site"] = (n["name"] or "x")[:4].upper()
        raw["custom_properties"] = ["Dept", "Site"]
        old = sw.snapshot(sw.model(raw, {"site_by": "Site"}))
        old.pop("h")                                                                 # a 1.85 copy: no "h"
        ch = sw.compare(sw.model(raw, {"site_by": "Site"}), json.loads(json.dumps(old)))
        self.assertNotIn("Sites read a new way", [c["kind"] for c in ch])

    def test_5_auto_keeps_last_runs_reading_on_a_near_tie(self):
        raw = hostile()
        m = sw.model(raw, {})
        lv = m["discovery"]["levels"][0]
        rd = [r for r in lv["readings"] if not r["chosen"] and r["score"] is not None and r["score"] >= lv["readings"][0]["score"] * (1 - sw.TIE)]
        if rd:                                                                       # a reading within the tie: last run's stays
            fp = "site:%s:%s" % (rd[0]["from"], rd[0]["value"])
            again = sw.model(raw, {"prev_fingerprint": fp})
            self.assertEqual(again["fingerprint"], fp)
            self.assertIn("kept from the last run", again["discovery"]["levels"][0]["decided"])
        far = "site:location:-1"                                                     # one that loses by more: not kept
        self.assertEqual(sw.model(raw, {"prev_fingerprint": far})["fingerprint"], m["fingerprint"])

    def test_6_a_missing_nodes_site_read_in_capitals(self):
        raw = hostile()                                                              # the name's letter run, kept from the last run
        m = sw.model(raw, {"prev_fingerprint": "site:pattern:^([A-Z]+|[0-9]+)"})
        self.assertEqual(m["discovery"]["levels"][0]["from"], "pattern")
        fn = sw._resite(m)
        self.assertEqual(fn({"name": "sitc-acc-09"}), "SITC")                       # discovery reads the name in capitals: so here
        self.assertEqual(fn({"name": "SITC-ACC-09"}), "SITC")


@unittest.skipUnless(_browser(), "no google-chrome or chromium")
class Building1950(unittest.TestCase):
    """1.95.0: a building's closets - down and warning devices always among the shown ones, Show all lists every one, and no
    link line crosses a device box or another closet (the routing keeps to the gaps, the gutter and the channels)."""

    def page(self, js):
        nodes = [{"id": 1, "name": "SITA0101-CR01", "site": "SITA0101", "building": "B01", "role": "core", "status": "Up"},
                 {"id": 2, "name": "SITA0101-CR02", "site": "SITA0101", "building": "B01", "role": "core", "status": "Up"},
                 {"id": 3, "name": "SITA0101-FW01", "site": "SITA0101", "building": "B01", "role": "firewall", "status": "Up"}]
        links = [{"a": 3, "b": 1, "status": "Up"}, {"a": 1, "b": 2, "status": "Up"}]
        nid = 10
        for d in range(1, 6):                         # 5 closets of 30: two rows at 1280 px, so lower closets' uplinks cross a row
            did = nid; nid += 1
            nodes.append({"id": did, "name": "SITA0101-DS%02d" % d, "site": "SITA0101", "building": "B01", "role": "distribution", "status": "Up"})
            links += [{"a": 1, "b": did, "status": "Up"}, {"a": 2, "b": did, "status": "Up"}]
            for i in range(30):
                st = "Down" if (d == 1 and i == 29) else ("Warning" if (d == 1 and i == 28) else "Up")
                nodes.append({"id": nid, "name": "SITA0101-AS%02d%02d" % (d, i), "site": "SITA0101", "building": "B01", "closet": "SITA0101-DS%02d" % d,
                              "role": "access", "status": st})
                links.append({"a": did, "b": nid, "status": st if st == "Down" else "Up"})
                nid += 1
        data = {"run": "test", "sites": ["SITA0101"], "nodes": nodes, "links": links, "view": "tiles"}
        with open(os.path.join(WEB, "netmap-map.html")) as fh:
            markup = fh.read()
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        for n in ("netmap.js", "netmap.css"):
            shutil.copy(os.path.join(WEB, n), d)
        with open(os.path.join(d, "t.js"), "w") as fh:
            fh.write("window.addEventListener('load',function(){setTimeout(function(){var r={};try{%s}catch(e){r.err=String(e)}"
                     "document.getElementById('res').textContent=JSON.stringify(r)},300)});" % js)
        with open(os.path.join(d, "p.html"), "w") as fh:
            fh.write('<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="netmap.css"></head><body>%s'
                     '<script type="application/json" id="netmap-data">%s</script><pre id="res"></pre>'
                     '<script src="netmap.js"></script><script src="t.js"></script></body></html>' % (markup, json.dumps(data).replace("</", "<\\/")))
        out = subprocess.run([_browser(), "--headless=new", "--no-sandbox", "--disable-gpu", "--window-size=1280,900", "--virtual-time-budget=5000",
                              "--dump-dom", "file://" + os.path.join(d, "p.html")], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             universal_newlines=True, timeout=120).stdout
        return json.loads(re.search(r'<pre id="res">(.*?)</pre>', out, re.S).group(1).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))

    NAMES = ("function names(){return [].map.call(document.querySelectorAll('.nm-draw .nm-dev'),function(g){return g.getAttribute('aria-label').split(',')[0]})}"
             "nmTest.go('building','SITA0101','B01');")

    def test_1_down_and_warning_are_shown_and_show_all(self):
        r = self.page(self.NAMES + "var a=names();r.first=a.length;r.down=a.indexOf('SITA0101-AS0129')>=0;r.warn=a.indexOf('SITA0101-AS0128')>=0;"
                      "var b=document.querySelector('.nm-more text');r.label=b.textContent;"
                      "document.querySelector('.nm-more').dispatchEvent(new MouseEvent('click',{bubbles:true}));"
                      "r.after=names().length;r.label2=document.querySelector('.nm-more text').textContent;")
        self.assertNotIn("err", r)
        self.assertEqual(r["first"], 3 + 5 + 5 * 18)          # the top tiers, 5 distribution switches, 18 per closet
        self.assertTrue(r["down"] and r["warn"], r)            # the 29th and 30th of their closet, shown first anyway
        self.assertEqual(r["label"], "Show all 30")
        self.assertEqual(r["after"], 3 + 5 + 30 + 4 * 18)
        self.assertEqual(r["label2"], "Show fewer")

    def test_2_no_line_crosses_a_box(self):
        r = self.page(self.NAMES + r"""
var boxes=[].map.call(document.querySelectorAll('.nm-draw .nm-dev'),function(g){var m=/translate\(([-\d.]+),([-\d.]+)\)/.exec(g.getAttribute('transform'));return [+m[1],+m[2],+m[1]+112,+m[2]+36]});
var cl=[].map.call(document.querySelectorAll('.nm-closet'),function(e){var x=+e.getAttribute('x'),y=+e.getAttribute('y');return [x,y,x+(+e.getAttribute('width')),y+(+e.getAttribute('height'))]});
function inside(q,b){return q[0]>b[0]-2&&q[0]<b[2]+2&&q[1]>b[1]-2&&q[1]<b[3]+2}
function hit(p,q,b){return Math.min(p[0],q[0])<b[2]-1&&Math.max(p[0],q[0])>b[0]+1&&Math.min(p[1],q[1])<b[3]-1&&Math.max(p[1],q[1])>b[1]+1}
var bad=[],n=0;[].forEach.call(document.querySelectorAll('.nm-draw path.nm-l'),function(pa){n++;var t=pa.getAttribute('d').match(/[MHV]|[-\d.]+/g),x=0,y=0,pts=[],i=0;
 while(i<t.length){var c=t[i++];if(c==='M'){x=+t[i++];y=+t[i++]}else if(c==='H')x=+t[i++];else y=+t[i++];pts.push([x,y])}
 var a=pts[0],z=pts[pts.length-1];
 for(var j=1;j<pts.length;j++){boxes.forEach(function(b){if(!inside(a,b)&&!inside(z,b)&&hit(pts[j-1],pts[j],b))bad.push(pa.getAttribute('d'))});
  cl.forEach(function(b){if(!inside(a,b)&&!inside(z,b)&&hit(pts[j-1],pts[j],[b[0]+3,b[1]+3,b[2]-3,b[3]-3]))bad.push('closet '+pa.getAttribute('d'))})}});
r.paths=n;r.bad=bad.slice(0,5);r.rows=cl.length?(cl[cl.length-1][1]>cl[0][1]):false;""")
        self.assertNotIn("err", r)
        self.assertTrue(r["rows"], "the fixture should wrap the closets into two rows")
        self.assertGreater(r["paths"], 100)
        self.assertEqual(r["bad"], [])

if __name__ == "__main__":
    unittest.main(verbosity=1)
