#!/usr/bin/env python3
"""Unit tests for the self-hosted part of the ServiceNow health check: the node module's pure parts
(roles/servicenow/library/site_servicenow_node.py - the stats page, the log, the port, the node's folder) and
plugins/filter/servicenow_node_filters.py (findings, the cluster, the tables): python3 tests/test_servicenow_node.py"""
import datetime
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
sys.path.insert(0, os.path.join(HERE, "..", "roles", "servicenow", "library"))
import servicenow_node_filters as nf  # noqa: E402
import site_servicenow_node as nm  # noqa: E402

NODE = os.path.join(HERE, "servicenow", "node")
NOW = datetime.datetime(2026, 11, 9, 12, 0, 0, tzinfo=datetime.timezone.utc).timestamp()


def page(name, **kv):
    vals = {"MEMUSED": "1200.0", "ACTIVE": "3", "SEMAVAIL": "14", "SEMUSE": "2", "SEMQ": "0", "SCHEDQ": "1"}
    vals.update({k.upper(): str(v) for k, v in kv.items()})
    with open(os.path.join(NODE, name)) as f:
        body = f.read()
    for k, v in vals.items():
        body = body.replace("@%s@" % k, v)
    return body


def unit(name="snc_prod.service", env="prod", active="active", enabled="enabled", port=16000, mem=(1200.0, 4096.0),
         semq=(0, 0, 0), schedq=1, up=True, status=200, why="", node_id="", oom=0, sem=0, severe=0, groups=None):
    st = {"status": status, "page": "/xmlstats.do" if up else "", "why": why, "up": up, "node_id": node_id, "uptime": "1 Days",
          "mem_used": mem[0], "mem_max": mem[1], "mem_pct": round(100.0 * mem[0] / mem[1], 1) if up else None,
          "sem_in_use": 2.0, "sem_queued": semq[-1] if semq else None, "sem_queued_samples": list(semq) if up else [],
          "sched_workers": 8.0, "sched_queue": schedq, "transactions": 3.0}
    return {"unit": name, "env": env, "active": active, "sub": "running" if active == "active" else "dead", "enabled": enabled,
            "node_dir": "/glide/nodes/%s_%d" % (env, port), "port": port, "port_from": "glide.properties", "stats": st,
            "log": {"dir": "/glide/nodes/%s_%d/logs" % (env, port), "files": ["localhost_log.2026-11-09.txt"], "hours": 24,
                    "severe": severe, "oom": oom, "semaphore": sem, "groups": groups or [], "why": ""}}


def node(host="node01.yoursite.mil", units=None, disk=None, mem=None, **kw):
    h = {"host": host, "hostname": host.split(".")[0], "reachable": True, "error": "", "units": units if units is not None else [unit()],
         "missing": [], "disk": disk if disk is not None else [{"path": "/glide", "size_gb": 200.0, "free_gb": 120.0, "used_pct": 40.0}],
         "memory": mem if mem is not None else {"total_mb": 32000, "available_mb": 12000, "available_pct": 37.5,
                                                "swap_total_mb": 4096, "swap_used_pct": 0.0}}
    h.update(kw)
    return h


def crow(sid, status="online", minutes_ago=1):
    t = datetime.datetime.fromtimestamp(NOW - minutes_ago * 60, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return {"system_id": sid, "node_id": sid.replace(":", "_"), "status": status, "most_recent_message": t, "schedulers": "any"}


def ids(findings):
    return sorted((f["id"], f["severity"]) for f in findings)


class StatsPage(unittest.TestCase):
    def test_xmlstats(self):
        r = nm.parse_stats(page("xmlstats.xml", memused=3600, semq=3, schedq=70, active=5))
        self.assertEqual((r["mem_used"], r["mem_max"], r["mem_pct"]), (3600.0, 4096.0, 87.9))
        self.assertEqual((r["sem_in_use"], r["sem_queued"]), (2.0, 3.0))
        self.assertEqual([s["name"] for s in r["sem_sets"]], ["Default", "AMB_SEND"])
        self.assertEqual((r["sched_workers"], r["sched_queue"], r["transactions"]), (8.0, 70.0, 5.0))
        self.assertEqual(r["node_id"], "node01:prod_16000")
        self.assertEqual(r["uptime"], "1 Days 4 Hours 2 Minutes")

    def test_stats_do(self):
        r = nm.parse_stats(page("stats.html", memused="1,000.0"))
        self.assertEqual((r["mem_used"], r["mem_max"], r["mem_pct"]), (1000.0, 2048.0, 48.8))
        self.assertEqual((r["sem_in_use"], r["sem_available"], r["sem_queued"]), (2.0, 14.0, 0.0))
        self.assertEqual((r["sched_workers"], r["sched_queue"], r["transactions"]), (4.0, 1.0, 3.0))
        self.assertEqual(r["node_id"], "node12:test_16001")

    def test_nothing_recognised(self):
        self.assertEqual(nm.parse_stats("<html><body>Log in</body></html>")["found"], [])

    def test_units(self):
        self.assertEqual(nm._num("2 GB"), 2048.0)
        self.assertEqual(nm._num("1,536 MB"), 1536.0)


class Log(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_grouped_counted_window(self):
        subprocess.run([sys.executable, os.path.join(NODE, "make_logs.py"), self.d, "bad"], check=True)
        lg = nm.read_log(self.d, "localhost_log*", 24, 200, 10)
        self.assertEqual(lg["files"], ["localhost_log.%s.txt" % datetime.date.today().isoformat()])   # the 40-day-old file left out
        self.assertEqual((lg["severe"], lg["oom"], lg["semaphore"]), (144, 1, 4))   # the line before the window left out
        first = lg["groups"][0]
        self.assertEqual((first["message"], first["kinds"]), ("java.lang.OutOfMemoryError: Java heap space", ["OutOfMemory", "SEVERE"]))
        sem = [g for g in lg["groups"] if g["kinds"] == ["semaphore"]]
        self.assertEqual([g["count"] for g in sem], [4])        # the numbers in them do not split them
        big = max(lg["groups"], key=lambda g: g["count"])
        self.assertEqual(big["count"], 140)
        self.assertTrue(big["message"].startswith("Transaction cancelled: maximum execution time exceeded"))
        inv = [g for g in lg["groups"] if g["message"].startswith("Invalid query")]
        self.assertEqual([g["count"] for g in inv], [3])

    def test_semaphore_info_is_not_an_error(self):
        subprocess.run([sys.executable, os.path.join(NODE, "make_logs.py"), self.d, "well"], check=True)
        lg = nm.read_log(self.d, "localhost_log*", 24, 200, 10)
        self.assertEqual((lg["severe"], lg["oom"], lg["semaphore"]), (3, 0, 0))

    def write(self, lines):
        t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 600))
        with open(os.path.join(self.d, "localhost_log.%s.txt" % time.strftime("%Y-%m-%d")), "w") as f:
            f.write("".join(l.replace("@T@", t) + "\n" for l in lines))

    def test_slow_transactions_are_not_semaphore_errors(self):
        # the shape of a real node's lines (made-up threads, transaction IDs, paths and people)
        line = ("@T@ (%d) %s SYSTEM txid=%s GlideServletTransaction *** WARNING *** GlideServlet:Transaction waited excessive "
                "time to run: %dms, semaphore wait: %dms, session wait: %dms - transaction: #%d %s, user: %s")
        tx = lambda n, thread, run, sem, sess, path, who: line % (100 + n, thread, "%032x" % (n * 7919), run, sem, sess, 5000 + n, path, who)
        self.write([
            # the session synchronizer: one user's own requests queued behind each other (session wait about the run)
            tx(1, "Default-thread-30", 3295, 0, 3290, "/api/now/ui/page_timing/0123456789abcdef0123456789abcdef", "jane.roe.ctr"),
            tx(2, "Default-thread-7", 8120, 1, 8100, "/api/now/ui/page_timing/fedcba9876543210fedcba9876543210", "john.doe"),
            tx(3, "AMB_SEND-thread-22", 4410, 0, 4400, "/api/now/ui/page_timing/00112233445566778899aabbccddeeff", "pat.smith.ctr"),
            # waiting for a semaphore: the node's limit
            tx(4, "Default-thread-3", 45210, 42000, 0, "/incident_list.do", "alex.lee"),
            # slow by itself
            tx(5, "Default-thread-9", 6100, 0, 10, "/sys_report_template.do", "sam.ray"),
            "@T@ (77) http-3306-3 0F0E0D0C0B0A09080706050403020100 *** WARNING *** Default semaphore queue depth 23 exceeded",
            "@T@ (78) http-3306-4 *** WARNING *** semaphore Default timed out after 30000 ms",
            "@T@ (79) worker.2 INFO *** Semaphore set Default: 16 available"])
        lg = nm.read_log(self.d, "localhost_log*", 24, 200, 10, sem_wait_ms=1000)
        self.assertEqual((lg["slow"], lg["slow_max_ms"], lg["slow_sem"], lg["semaphore"], lg["severe"]), (5, 45210, 1, 3, 0))
        by = {tuple(g["kinds"]): g for g in lg["groups"]}
        session = by[("slow transaction (session wait)",)]
        self.assertEqual((session["count"], session["max_ms"], session["max_session_ms"]), (3, 8120, 8100))   # one group
        self.assertEqual(by[("slow transaction (semaphore wait)",)]["count"], 1)
        self.assertEqual(by[("slow transaction",)]["count"], 1)
        self.assertEqual(session["message"][:61], "GlideServlet:Transaction waited excessive time to run: 4410ms")
        self.assertTrue(session["message"].endswith("/api/now/ui/page_timing/00112233445566778899aabbccddeeff, user: …"))
        shown = json.dumps(lg)
        for who in ("jane.roe", "john.doe", "pat.smith", "alex.lee", "sam.ray"):
            self.assertNotIn(who, shown)                      # who did it is never shown
        f = nf.sn_node_findings([node(units=[unit(sem=lg["semaphore"], groups=lg["groups"])])], [], {})
        self.assertEqual([x["id"].rsplit(":", 1)[1] for x in f], ["log-semaphore"])
        u = unit(groups=lg["groups"])
        u["log"] = dict(u["log"], slow=250, slow_max_ms=45210)
        f = nf.sn_node_findings([node(units=[u])], [], {})
        self.assertEqual([(x["id"].rsplit(":", 1)[1], x["summary"]) for x in f],
                         [("slow", "the ServiceNow node snc_prod.service on node01.yoursite.mil logged 250 slow transaction(s) in "
                                   "the last 24 h (the longest ran 45210 ms)")])
        rows = nf.sn_node_sections([node(units=[unit(groups=lg["groups"])])], [], [], {})[-1]["rows"]
        self.assertIn(["node01.yoursite.mil", "snc_prod", "slow transaction (session wait)", 3, "8120 ms, session wait 8100 ms"],
                      [r[:5] for r in rows])

    def test_key(self):
        a = nm.key_of("GlideServlet:Transaction waited excessive time to run: 3295ms, semaphore wait: 0ms - transaction: #12 "
                      "/api/now/ui/page_timing/0123456789abcdef0123456789abcdef, user: a.b")
        b = nm.key_of("GlideServlet:Transaction waited excessive time to run: 811ms, semaphore wait: 1ms - transaction: #9 "
                      "/api/now/ui/page_timing/fedcba9876543210fedcba9876543210, user: c.d")
        self.assertEqual(a, b)
        self.assertIn("/api/now/ui/page_timing,", a)
        self.assertEqual(nm.key_of("2026-11-09 10:00:01 (512) Default-thread-30 txid=abc123 x"), nm.key_of("2026-11-09 11:00:01 (7) Default-thread-2 txid=ff x"))
        self.assertEqual(nm.no_user("Transaction cancelled for /x (user 1139)"), "Transaction cancelled for /x (user …)")

    def test_no_folder(self):
        self.assertIn("no logs folder", nm.read_log(os.path.join(self.d, "nope"), "localhost_log*", 24, 200, 10)["why"])

    def test_message_of(self):
        self.assertEqual(nm.message_of("2026-11-09 10:00:01 (123) glide.scheduler.worker.2 5F3C0D2B9A1E4C77B0A1D2E3F4A5B6C7 SEVERE "
                                       "*** ERROR *** the message"), "the message")


class StatsWays(unittest.TestCase):
    """How the stats page is asked for: a TLS connector, a node that wants its own name, one that wants a login."""
    procs = []

    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        cls.cert = os.path.join(cls.d, "node.pem")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=node01.yoursite.mil", "-days", "1",
                        "-keyout", cls.cert, "-out", cls.cert], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        for p in cls.procs:
            p.kill()

    def serve(self, *opts):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        self.procs.append(subprocess.Popen([sys.executable, os.path.join(NODE, "statsmock.py"), str(port), "xml"] + list(opts)))
        for _ in range(50):
            with socket.socket() as c:
                if c.connect_ex(("127.0.0.1", port)) == 0:
                    return port
            time.sleep(0.1)
        self.fail("the fake node did not start")

    def test_order(self):
        ways = nm.stats_bases(3306, ["node01.yoursite.mil", "localhost"])
        self.assertEqual(ways[:4], [("https://node01.yoursite.mil:3306", ""), ("http://node01.yoursite.mil:3306", ""),
                                    ("http://127.0.0.1:3306", ""), ("https://127.0.0.1:3306", "")])
        self.assertEqual(ways[4:], [("http://127.0.0.1:3306", "node01.yoursite.mil"), ("https://127.0.0.1:3306", "node01.yoursite.mil")])
        self.assertEqual(nm.stats_bases(3306, ["node01", "node01.yoursite.mil"], "https://{fqdn}:{port}/stats.do"),
                         [("https://node01.yoursite.mil:3306/stats.do", "")])

    def test_tls_connector_answers_plain_http_400(self):
        port = self.serve("tls=" + self.cert)
        st = nm.read_stats(port, ["/xmlstats.do", "/stats.do"], 1, 0, 5, ["node01.invalid"], validate=False)
        self.assertTrue(st["up"], st)
        self.assertEqual(st["url"], "https://127.0.0.1:%d/xmlstats.do" % port)
        self.assertIn("http://127.0.0.1:%d/xmlstats.do -> 400" % port, st["tried"])

    def test_wants_its_own_name(self):
        port = self.serve("host=node01.invalid")
        st = nm.read_stats(port, ["/xmlstats.do"], 1, 0, 5, ["node01.invalid"])
        self.assertTrue(st["up"], st)
        self.assertEqual((st["url"], st["host_header"]), ("http://127.0.0.1:%d/xmlstats.do" % port, "node01.invalid"))

    def test_login_only_when_asked_and_never_shown(self):
        port = self.serve("auth=svc_aap:S3cret-pw!")
        st = nm.read_stats(port, ["/xmlstats.do"], 1, 0, 5, ["node01.invalid"], auth="none", login=("svc_aap", "S3cret-pw!"))
        self.assertFalse(st["up"])
        self.assertEqual(st["status"], 401)
        st = nm.read_stats(port, ["/xmlstats.do"], 1, 0, 5, ["node01.invalid"], login=("svc_aap", "S3cret-pw!"))
        self.assertTrue(st["up"] and st["login"], st)
        self.assertNotIn("S3cret", json.dumps(st))
        self.assertTrue(nm.may_send_login("http://127.0.0.1:3306/x", ["node01.invalid"]))
        self.assertTrue(nm.may_send_login("https://node01.invalid:3306/x", ["node01.invalid"]))
        self.assertFalse(nm.may_send_login("http://node01.invalid:3306/x", ["node01.invalid"]))        # not over plain HTTP
        self.assertFalse(nm.may_send_login("https://elsewhere.invalid:3306/x", ["node01.invalid"]))

    def test_https_is_checked_by_default(self):
        ctx = nm.context()
        self.assertTrue(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        ctx = nm.context(False)
        self.assertEqual((ctx.check_hostname, ctx.verify_mode), (False, ssl.CERT_NONE))
        # a certificate the host does not trust: not read, and it says why
        port = self.serve("tls=" + self.cert)
        st = nm.read_stats(port, ["/xmlstats.do"], 1, 0, 5, ["node01.invalid"])
        self.assertFalse(st["up"])
        self.assertIn("certificate is not trusted", st["why"])
        self.assertIn("servicenow_node_validate_certs", st["why"])

    def test_https_read_when_its_certificate_is_trusted(self):
        cert = os.path.join(self.d, "ip.pem")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=127.0.0.1", "-days", "1",
                        "-addext", "subjectAltName=IP:127.0.0.1", "-addext", "basicConstraints=critical,CA:TRUE",
                        "-addext", "keyUsage=critical,digitalSignature,keyCertSign",
                        "-keyout", cert, "-out", cert], check=True, capture_output=True)
        port = self.serve("tls=" + cert)
        before = os.environ.get("SSL_CERT_FILE")
        os.environ["SSL_CERT_FILE"] = cert            # the host's trust store, for this test: that certificate
        try:
            st = nm.read_stats(port, ["/xmlstats.do"], 1, 0, 5, [])
        finally:
            if before is None:
                os.environ.pop("SSL_CERT_FILE", None)
            else:
                os.environ["SSL_CERT_FILE"] = before
        self.assertTrue(st["up"], st)
        self.assertEqual(st["url"], "https://127.0.0.1:%d/xmlstats.do" % port)

    def test_not_read_says_what_was_tried(self):
        port = self.serve("tls=" + self.cert, "host=node01.invalid", "auth=a:b")
        st = nm.read_stats(port, ["/xmlstats.do"], 1, 0, 5, ["other.invalid"], auth="none")
        self.assertFalse(st["up"])
        self.assertEqual(st["status"], 400)
        self.assertGreaterEqual(len(st["tried"]), 4)
        f = nf.sn_node_findings([node(units=[unit(up=False, status=400, port=port)])], [], {})
        u = unit(up=False, status=400, port=port)
        u["stats"] = dict(st)
        f = nf.sn_node_findings([node(units=[u])], [], {})
        self.assertEqual([x["id"].rsplit(":", 1)[1] for x in f], ["stats-unread"])
        self.assertIn("(port %d; the last answer: HTTP 400)" % port, f[0]["summary"])
        self.assertIn("# tried: https://other.invalid", f[0]["hint"])
        self.assertIn("servicenow_node_stats_url", f[0]["hint"])



def _ca_and_cert(d, tag, name):
    """A local CA and a server certificate for name signed by it (key and certificate in one file): (ca.pem, server.pem)."""
    ca, cakey, key, csr, crt, ext = (os.path.join(d, "%s-%s" % (tag, f)) for f in ("ca.pem", "ca.key", "srv.key", "srv.csr", "srv.crt", "ext"))
    run = lambda *a: subprocess.run(list(a), check=True, capture_output=True)      # noqa: E731
    run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=Test CA %s" % tag, "-days", "1",
        "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        "-keyout", cakey, "-out", ca)
    run("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=%s" % name, "-keyout", key, "-out", csr)
    with open(ext, "w") as f:
        f.write("subjectAltName=DNS:%s\nbasicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
                "extendedKeyUsage=serverAuth\n" % name)
    run("openssl", "x509", "-req", "-in", csr, "-CA", ca, "-CAkey", cakey, "-CAcreateserial", "-days", "1", "-extfile", ext, "-out", crt)
    srv = os.path.join(d, "%s-server.pem" % tag)
    with open(srv, "w") as out:
        for f in (key, crt):
            with open(f) as i:
                out.write(i.read())
    return ca, srv


class StatsNamedCert(unittest.TestCase):
    """A TLS-only node whose certificate (from a CA the host trusts) names a name that does not resolve: read on
    127.0.0.1 with that name as the Host, the handshake saying it (SNI) and the certificate checked against it."""
    NAME = "node01.example.invalid"
    procs = []
    serve = StatsWays.serve

    @classmethod
    def tearDownClass(cls):
        for p in cls.procs:
            p.kill()

    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        cls.ca, cls.srv = _ca_and_cert(cls.d, "good", cls.NAME)
        cls.other_ca, cls.other_srv = _ca_and_cert(cls.d, "other", "elsewhere.example.invalid")

    def read(self, srv, trust, names=None, **kw):
        port = self.serve("tls=" + srv)
        before = os.environ.get("SSL_CERT_FILE")
        os.environ["SSL_CERT_FILE"] = trust            # the host's trust store, for this test: that CA
        try:
            return port, nm.read_stats(port, ["/xmlstats.do"], 1, 0, 5, names or [self.NAME], **kw)
        finally:
            if before is None:
                os.environ.pop("SSL_CERT_FILE", None)
            else:
                os.environ["SSL_CERT_FILE"] = before

    def test_read_on_127_with_the_name_as_host_and_sni(self):
        port, st = self.read(self.srv, self.ca)
        self.assertTrue(st["up"], st)
        self.assertEqual((st["url"], st["host_header"], st["status"]), ("https://127.0.0.1:%d/xmlstats.do" % port, self.NAME, 200))
        # the ways before it: the name does not resolve, 127.0.0.1 alone is not what the certificate names
        self.assertTrue(any(t.startswith("https://127.0.0.1:%d/xmlstats.do -> the certificate does not name 127.0.0.1" % port)
                            for t in st["tried"]), st["tried"])

    def test_fetch_checks_against_the_host_it_sends(self):
        port = self.serve("tls=" + self.srv)
        before = os.environ.get("SSL_CERT_FILE")
        os.environ["SSL_CERT_FILE"] = self.ca
        try:
            code, body = nm.fetch("https://127.0.0.1:%d/xmlstats.do" % port, self.NAME, 5)
            self.assertEqual(code, 200, body)
            code, body = nm.fetch("https://127.0.0.1:%d/xmlstats.do" % port, "", 5)
            self.assertEqual(code, -1)
            self.assertTrue(body.startswith("the certificate does not name 127.0.0.1:"), body)
            code, body = nm.fetch("https://127.0.0.1:%d/xmlstats.do" % port, "wrong.example.invalid", 5)
            self.assertEqual(code, -1)
            self.assertTrue(body.startswith("the certificate does not name wrong.example.invalid:"), body)
        finally:
            if before is None:
                os.environ.pop("SSL_CERT_FILE", None)
            else:
                os.environ["SSL_CERT_FILE"] = before
        code, body = nm.fetch("https://127.0.0.1:%d/xmlstats.do" % port, "wrong.example.invalid", 5, validate=False)
        self.assertEqual(code, 200, body)

    def test_certificate_for_another_name(self):
        port, st = self.read(self.other_srv, self.other_ca)          # trusted CA, the certificate names elsewhere
        self.assertFalse(st["up"])
        self.assertIn("(Host: %s): the certificate does not name %s" % (self.NAME, self.NAME), st["why"])
        self.assertIn("or set servicenow_node_validate_certs false", st["why"])
        self.assertNotIn("add its CA", st["why"])
        self.assertNotIn("add its CA", json.dumps(st["tried"]))

    def test_untrusted_ca(self):
        port, st = self.read(self.srv, self.other_ca)                # the right name, a CA the host does not trust
        self.assertFalse(st["up"])
        self.assertIn("(Host: %s): its certificate is not trusted" % self.NAME, st["why"])
        self.assertIn("add its CA to the host's trust store, or set servicenow_node_validate_certs false", st["why"])
        self.assertNotIn("does not name", st["why"])

    def test_not_checked_when_validate_false(self):
        port, st = self.read(self.other_srv, self.ca, validate=False)
        self.assertTrue(st["up"], st)

class PortAndFolder(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.nd = os.path.join(self.home, "nodes", "prod_16000")
        os.makedirs(os.path.join(self.nd, "conf"))
        os.makedirs(os.path.join(self.nd, "logs"))

    def test_folder_from_execstart(self):
        show = {"ExecStart": "{ path=%s/startup.sh ; argv[]=%s/startup.sh run }" % (self.nd, self.nd)}
        self.assertEqual(nm.node_dir(self.home, "prod", show), self.nd)

    def test_folder_by_env(self):
        self.assertEqual(nm.node_dir(self.home, "prod", {}), self.nd)
        self.assertEqual(nm.node_dir(self.home, "dev", {}), self.home)      # none: the install folder's own logs

    def test_port(self):
        with open(os.path.join(self.nd, "conf", "glide.properties"), "w") as f:
            f.write("# x\nglide.servlet.port = 16000\n")
        self.assertEqual(nm.port_for("snc_prod.service", "prod", 0, self.nd, self.home, {})[0], 16000)
        self.assertEqual(nm.port_for("snc_prod.service", "prod", {"snc_prod": 17000}, self.nd, self.home, {}), (17000, "servicenow_node_port"))
        self.assertEqual(nm.port_for("snc_prod.service", "prod", 18000, self.nd, self.home, {})[0], 18000)
        self.assertEqual(nm.port_for("snc_dev_16002.service", "dev_16002", 0, "/nope", "/nope", {})[0], 16002)
        self.assertEqual(nm.port_for("snc_dev.service", "dev", 0, "/nope", "/nope", {}), (0, ""))

    def test_unit_names(self):
        self.assertEqual(nm.unit_name("snc_prod"), "snc_prod.service")
        self.assertEqual(nm.unit_name("snc_*"), "snc_*")


class Findings(unittest.TestCase):
    O = {"cluster_read": True, "inventory": ["node01.yoursite.mil"], "group": "servicenow_nodes"}

    def test_well(self):
        self.assertEqual(nf.sn_node_findings([node()], nf.sn_node_cluster([crow("node01:prod_16000")], {"now": NOW}), self.O), [])

    def test_unit_states(self):
        f = nf.sn_node_findings([node(units=[unit(active="inactive", enabled="disabled", up=False)])], [], {})
        self.assertEqual(ids(f), [("servicenow:node:node01.yoursite.mil:snc_prod.service:not-enabled", "warning"),
                                  ("servicenow:node:node01.yoursite.mil:snc_prod.service:stopped", "critical")])

    def test_units_not_installed_are_no_finding(self):
        # a box runs one of snc_prod, snc_test, snc_dev: the others not installed are nothing
        self.assertEqual(nf.sn_node_findings([node(missing=["snc_test.service", "snc_dev.service"],
                                                   looked_for=["snc_prod.service", "snc_test.service", "snc_dev.service"])], [], {}), [])
        f = nf.sn_node_findings([node(units=[], missing=["snc_prod.service", "snc_test.service", "snc_dev.service"],
                                      looked_for=["snc_prod.service", "snc_test.service", "snc_dev.service"])], [], {})
        self.assertEqual(ids(f), [("servicenow:node:node01.yoursite.mil:no-unit", "critical")])
        self.assertEqual(f[0]["summary"], "no ServiceNow node unit found on node01.yoursite.mil (looked for snc_prod, snc_test, snc_dev)")

    def test_fapolicyd(self):
        fa = lambda active, groups: {"installed": True, "active": active, "since": "today", "groups": groups,
                                     "denials": sum(g["count"] for g in groups)}
        g_java = {"exe": "/usr/lib/jvm/jre/bin/java", "path": "/glide/nodes/prod_16000/lib/glide.jar", "count": 3, "critical": True}
        g_bash = {"exe": "/usr/bin/bash", "path": "/tmp/x.sh", "count": 2, "critical": False}
        self.assertEqual(nf.sn_node_findings([node(fapolicyd=fa("inactive", [g_bash]))], [], {}), [])     # inactive: info, no finding
        self.assertEqual(nf.sn_node_findings([node(fapolicyd={"installed": False})], [], {}), [])
        f = nf.sn_node_findings([node(fapolicyd=fa("active", [g_java, g_bash]))], [], {})
        self.assertEqual(ids(f), [("servicenow:node:node01.yoursite.mil:fapolicyd", "warning"),
                                  ("servicenow:node:node01.yoursite.mil:fapolicyd-critical", "critical")])
        self.assertIn("/usr/lib/jvm/jre/bin/java -> /glide/nodes/prod_16000/lib/glide.jar (3x)", f[0]["summary"])

    def test_memory(self):
        f = nf.sn_node_findings([node(units=[unit(mem=(3600.0, 4096.0))])], [], {})
        self.assertEqual([x["severity"] for x in f], ["warning"])
        f = nf.sn_node_findings([node(units=[unit(mem=(3990.0, 4096.0))])], [], {})
        self.assertEqual([x["severity"] for x in f], ["critical"])
        self.assertIn("uses 97.4% of its JVM memory (3990 of 4096 MB; critical at 95%)", f[0]["summary"])

    def test_semaphores_sustained_only(self):
        self.assertEqual(nf.sn_node_findings([node(units=[unit(semq=(3, 0, 2))])], [], {}), [])      # it passed
        f = nf.sn_node_findings([node(units=[unit(semq=(3, 4, 2))])], [], {})
        self.assertEqual([x["severity"] for x in f], ["warning"])
        f = nf.sn_node_findings([node(units=[unit(semq=(12, 15, 11))])], [], {})
        self.assertEqual([x["severity"] for x in f], ["critical"])

    def test_scheduler(self):
        self.assertEqual(nf.sn_node_findings([node(units=[unit(schedq=50)])], [], {}), [])
        self.assertEqual([x["id"].rsplit(":", 1)[1] for x in nf.sn_node_findings([node(units=[unit(schedq=51)])], [], {})], ["scheduler"])

    def test_stats_down_or_refused(self):
        f = nf.sn_node_findings([node(units=[unit(up=False, status=-1, why="/xmlstats.do: [Errno 111] Connection refused")])], [], {})
        self.assertEqual([(x["id"].rsplit(":", 1)[1], x["severity"]) for x in f], [("stats-down", "critical")])
        f = nf.sn_node_findings([node(units=[unit(up=False, status=403, why="/stats.do: HTTP 403 (Forbidden)")])], [], {})
        self.assertEqual([(x["id"].rsplit(":", 1)[1], x["severity"]) for x in f], [("stats-unread", "warning")])
        f = nf.sn_node_findings([node(units=[unit(up=False, status=0, port=0, why="no port")])], [], {})
        self.assertEqual([(x["id"].rsplit(":", 1)[1], x["severity"]) for x in f], [("stats-unread", "warning")])

    def test_log(self):
        g = [{"message": "java.lang.OutOfMemoryError: Java heap space", "count": 1, "kinds": ["OutOfMemory", "SEVERE"], "last": ""},
             {"message": "Transaction cancelled", "count": 140, "kinds": ["SEVERE"], "last": ""}]
        f = nf.sn_node_findings([node(units=[unit(oom=1, sem=2, severe=141, groups=g)])], [], {})
        self.assertEqual([(x["id"].rsplit(":", 1)[1], x["severity"]) for x in f],
                         [("oom", "critical"), ("log-semaphore", "warning"), ("severe", "warning")])
        self.assertIn("logged 141 SEVERE line(s) in the last 24 h (the most frequent 140 times)", f[2]["summary"])
        self.assertIn("# Transaction cancelled", f[2]["hint"])          # the log's own line: in the hint, not the sentence
        self.assertNotIn("Java heap space", f[0]["summary"])

    def test_disk_and_swap(self):
        f = nf.sn_node_findings([node(disk=[{"path": "/glide", "used_pct": 96.0, "free_gb": 4.0}, {"path": "/", "used_pct": 86.0, "free_gb": 2.0}],
                                      mem={"total_mb": 32000, "available_mb": 900, "swap_total_mb": 4096, "swap_used_pct": 70.0})], [], {})
        self.assertEqual(ids(f), [("servicenow:node:node01.yoursite.mil:disk:/", "warning"),
                                  ("servicenow:node:node01.yoursite.mil:disk:/glide", "critical"),
                                  ("servicenow:node:node01.yoursite.mil:swap", "warning")])

    def test_host_not_reached_or_not_read(self):
        f = nf.sn_node_findings([node(reachable=False, error="timed out"), node(host="node12.yoursite.mil", error="sudo: a password is required")], [], {})
        self.assertEqual(ids(f), [("servicenow:node:node01.yoursite.mil:unreachable", "critical"),
                                  ("servicenow:node:node12.yoursite.mil:check-error", "warning")])


class Cluster(unittest.TestCase):
    def test_rows(self):
        c = nf.sn_node_cluster([crow("node01.yoursite.mil:prod_16000", minutes_ago=3), {"system_id": ""}], {"now": NOW})
        self.assertEqual([(x["host"], x["status"], x["age_min"]) for x in c], [("node01", "online", 3.0)])

    def test_offline_stale_unknown_missing(self):
        c = nf.sn_node_cluster([crow("node01:prod_16000", minutes_ago=30), crow("node01:test_16001", "offline", 600),
                                crow("node03:prod_16000", "offline", 9000), crow("node04:prod_16000")], {"now": NOW})
        o = {"cluster_read": True, "inventory": ["node01.yoursite.mil", "node12.yoursite.mil"], "group": "servicenow_nodes"}
        self.assertEqual(ids(nf.sn_node_findings([], c, o)), [
            ("servicenow:cluster-missing:node12", "warning"),
            ("servicenow:cluster-stale:node01:prod_16000", "critical"),
            ("servicenow:cluster-unknown:node04:prod_16000", "warning"),
            ("servicenow:cluster:node01:test_16001", "critical"),
            ("servicenow:cluster:node03:prod_16000", "warning")])        # offline and not in the inventory: a retired record

    def test_not_read_nothing_said(self):
        self.assertEqual(nf.sn_node_findings([], [], {"cluster_read": False, "inventory": ["node01"]}), [])

    def test_unit_matched_to_its_row(self):
        h = node(units=[unit(), unit("snc_test.service", "test", port=16001, node_id="node01:test_16001")])
        c = nf.sn_node_cluster([crow("node01:prod_16000"), crow("node01:test_16001", "offline", 60)], {"now": NOW})
        rows = nf.sn_node_sections([h], c, [], {"cluster_read": True, "inventory": ["node01.yoursite.mil"]})[0]["rows"]
        self.assertEqual([r[3] for r in rows], ["online, 1 min ago", "offline, 60 min ago"])


class Sections(unittest.TestCase):
    def test_tables(self):
        g = [{"message": "Transaction cancelled", "count": 140, "kinds": ["SEVERE"], "last": "2026-11-09 11:00:00"}]
        secs = nf.sn_node_sections([node(units=[unit(severe=140, groups=g)])], [], [{"host": "snowdb01.yoursite.mil", "reached": True, "problems": 0}],
                                   {"log_hours": 24})
        self.assertEqual([s["title"] for s in secs], ["Front-end nodes", "Node and database hosts", "Node log errors (the most frequent, last 24 h)"])
        front = secs[0]
        self.assertEqual(len(front["columns"]), len(front["rows"][0]))
        self.assertEqual(front["rows"][0][:3], ["node01.yoursite.mil", "snc_prod :16000", "running, at boot"])
        self.assertEqual(front["rows"][0][4], "29.3% (1200 / 4096 MB)")
        self.assertEqual(front["row_status"], ["warning"])         # 140 SEVERE lines >= 100
        self.assertEqual(secs[1]["rows"][1], ["snowdb01.yoursite.mil", "database", "-", "-", "-", "-", "none"])
        self.assertEqual(secs[2]["rows"][0][2:4], ["SEVERE", 140])
        self.assertTrue(front["top"])
        self.assertEqual([(b["label"], b["value"], b["status"]) for b in front["boxes"]],
                         [("Nodes running", "1 of 1", "ok"), ("Database", "1 of 1 read", "ok")])

    def test_database_tables(self):
        G = 1073741824
        db = {"datadir": "/database/data", "datadir_under": True, "source": "information_schema", "total_bytes": 50 * G, "system_read": True,
              "datadir_fs": {"mount": "/database", "size_b": 500 * G, "used_b": 300 * G, "free_b": 200 * G, "used_pct": 60.0},
              "schemas": [["snprod", 45 * G, 5120]], "tables": [["snprod.sys_audit", 9 * G]],
              "history": {"change_bytes": 2 * G, "last_time": "2026-11-08 06:00 UTC", "per_day_bytes": 2 * G, "days": 3.0}}
        d = {"host": "snowdb01.yoursite.mil", "reached": True, "problems": 0, "result": {"db": db, "disk": [], "memory": {}, "fapolicyd": {}}}
        secs = nf.sn_node_sections([], [], [d], {})
        size = [x for x in secs if x["title"] == "Database size"][0]
        self.assertEqual(size["rows"][0], ["snowdb01.yoursite.mil", "/database/data",
                                           "50.0 GB (data, indexes and InnoDB's files; from information_schema)",
                                           "/database: 300.0 GB used, 200.0 GB free (60.0%)", "10.0%",
                                           "+2.0 GB since the last run (2026-11-08 06:00 UTC); +2.0 GB a day over 3.0 days", "100 days"])
        self.assertEqual(len(size["columns"]), len(size["rows"][0]))
        self.assertEqual([x["title"] for x in secs][:4], ["Node and database hosts", "Database size", "Largest schemas", "Largest tables"])
        self.assertEqual(secs[0]["boxes"][0]["value"], "50.0 GB")
        self.assertEqual(secs[0]["boxes"][0]["note"], "10% of /database, 60.0% used")


class Database(unittest.TestCase):
    G = 1073741824

    def result(self, free_gb=200, per_day_gb=2, under=True, used_pct=60.0, disk=None):
        G = self.G
        return {"host": "snowdb01.yoursite.mil", "disk": disk or [], "fapolicyd": {},
                "db": {"datadir": "/database/data" if under else "/var/lib/mysql", "datadir_under": under,
                       "datadir_fs": {"mount": "/database", "size_b": 500 * G, "free_b": free_gb * G, "used_pct": used_pct},
                       "history": {"per_day_bytes": per_day_gb * G if per_day_gb is not None else None, "days": 3.0}}}

    def test_fill_days(self):
        self.assertEqual(nf.sn_db_findings([self.result()], {}), [])                                     # 100 days
        self.assertEqual(ids(nf.sn_db_findings([self.result(free_gb=40)], {})), [("servicenow:db:snowdb01.yoursite.mil:fill", "warning")])
        f = nf.sn_db_findings([self.result(free_gb=10)], {})
        self.assertEqual(ids(f), [("servicenow:db:snowdb01.yoursite.mil:fill", "critical")])
        self.assertIn("is full in 5 days", f[0]["summary"])
        self.assertEqual(nf.sn_db_findings([self.result(per_day_gb=None)], {}), [])                      # no history yet
        self.assertEqual(nf.sn_db_findings([self.result(per_day_gb=-1)], {}), [])                        # shrinking

    def test_datadir_and_disk(self):
        self.assertEqual(ids(nf.sn_db_findings([self.result(under=False)], {"db_paths": ["/database"]})),
                         [("servicenow:db:snowdb01.yoursite.mil:datadir", "warning")])
        f = nf.sn_db_findings([self.result(disk=[{"path": "/database", "used_pct": 96.0, "free_gb": 20.0}])], {})
        self.assertEqual(ids(f), [("servicenow:db:snowdb01.yoursite.mil:disk:/database", "critical")])
        self.assertEqual(f[0]["subject"], "snowdb01.yoursite.mil")


class DatabaseHost(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.dd = os.path.join(self.d, "database", "data")
        for sch, files in (("snprod", {"sys_audit.ibd": 9000, "syslog.ibd": 6000, "task.ibd": 100, "task.frm": 50}),
                           ("snaudit", {"x.MYD": 300, "x.MYI": 200}), ("mysql", {"user.MAD": 10})):
            os.makedirs(os.path.join(self.dd, sch))
            for n, size in files.items():
                with open(os.path.join(self.dd, sch, n), "wb") as f:
                    f.write(b"x" * size)
        for n, size in (("ibdata1", 1000), ("undo001", 500), ("ib_logfile0", 400)):
            with open(os.path.join(self.dd, n), "wb") as f:
                f.write(b"x" * size)
        os.makedirs(os.path.join(self.dd, "deep", "deeper"))       # one level only: never walked
        with open(os.path.join(self.dd, "deep", "deeper", "big.ibd"), "wb") as f:
            f.write(b"x" * 99999)

    def params(self, **kw):
        p = {"db_datadir": self.dd, "paths": [os.path.join(self.d, "database")], "db_sizes": {}, "db_top_schemas": 5, "db_top_tables": 10,
             "db_history_file": os.path.join(self.d, "hist.json"), "db_history_keep_days": 60}
        p.update(kw)
        return p

    def test_files_one_level(self):
        db = nm.database(self.params(), False)
        self.assertEqual(db["source"], "the data dir's files")
        self.assertEqual(db["schemas"][0], ["snprod", 15150, 3])
        self.assertEqual(db["tables"][:2], [["snprod.sys_audit", 9000], ["snprod.syslog", 6000]])
        self.assertIn(["snaudit.x", 500], db["tables"])                     # .MYD and .MYI: one table
        self.assertEqual((db["system_bytes"], db["system_read"]), (1900, True))
        self.assertTrue(db["datadir_under"])
        self.assertEqual(db["total_bytes"], 15150 + 500 + 0 + 1900)        # snprod, snaudit, deep (its own files: none); not mysql

    def test_information_schema_first(self):
        db = nm.database(self.params(db_sizes={"schemas": [["snprod", "4096", "5120"]], "tables": [["snprod.sys_audit", "2048"]]}), False)
        self.assertEqual((db["source"], db["data_bytes"], db["total_bytes"]), ("information_schema", 4096, 4096 + 1900))

    def test_not_under_and_not_here(self):
        self.assertFalse(nm.database(self.params(paths=["/database"]), False)["datadir_under"])
        db = nm.database(self.params(db_datadir="/var/lib/mysql-in-a-container"), False)
        self.assertIn("is not on this host", db["why"])

    def test_history_growth_and_check_mode(self):
        h = os.path.join(self.d, "h.json")
        t0 = 1_800_000_000
        self.assertEqual(nm.history(h, 1000, 0, 60, True, now=t0)["samples"], 1)
        r = nm.history(h, 1000 + 3 * 500, 0, 60, True, now=t0 + 3 * 86400)
        self.assertEqual((r["change_bytes"], r["per_day_bytes"], r["days"]), (1500, 500, 3.0))
        before = open(h).read()
        nm.history(h, 99999, 0, 60, False, now=t0 + 4 * 86400)             # check mode: nothing written
        self.assertEqual(open(h).read(), before)
        r = nm.history(h, 5000, 0, 1, True, now=t0 + 10 * 86400)             # older than keep_days: dropped
        self.assertEqual((r["samples"], r["change_bytes"]), (1, None))

    def test_fapolicyd_groups(self):
        t = ("----\ntype=PATH msg=audit(1) : item=0 name=/database/data/ibdata1 inode=1\n"
             "type=SYSCALL msg=audit(1) : arch=x86_64 syscall=openat success=no exe=/usr/sbin/mariadbd\n"
             "----\ntype=PATH msg=audit(2) : item=0 name=/tmp/x.sh\ntype=SYSCALL msg=audit(2) : exe=/usr/bin/bash\n"
             "----\ntype=PATH msg=audit(3) : item=0 name=/tmp/x.sh\ntype=SYSCALL msg=audit(3) : exe=/usr/bin/bash\n")
        g = nm.fapolicyd_groups(t, ["mariadbd", "/database"])
        self.assertEqual([(x["exe"], x["path"], x["count"], x["critical"]) for x in g],
                         [("/usr/sbin/mariadbd", "/database/data/ibdata1", 1, True), ("/usr/bin/bash", "/tmp/x.sh", 2, False)])
        self.assertTrue(nm.matches("/usr/lib/jvm/java-17-openjdk/bin/java", "java"))
        self.assertTrue(nm.matches("/glide/nodes/prod_16000/lib/x.jar", "/glide"))
        self.assertFalse(nm.matches("/glider/x", "/glide"))

    def test_subject_is_the_node(self):
        f = nf.sn_node_findings([node(units=[unit(active="failed")])], nf.sn_node_cluster([crow("node03:prod_16000")], {"now": NOW}),
                                {"cluster_read": True, "inventory": ["node01.yoursite.mil"]})
        self.assertEqual(sorted({x["subject"] for x in f}), ["node01", "node01.yoursite.mil", "node03:prod_16000"])

    def test_status_line(self):
        line = nf.sn_node_status([node(units=[unit(), unit("snc_test.service", "test", active="failed")])],
                                 [nf.sn_node_cluster([crow("node01:prod_16000")], {"now": NOW})[0]],
                                 {"cluster_read": True, "outside": ["node12.yoursite.mil"]})
        self.assertEqual(line, "Nodes: node01 snc_prod running, node01 snc_test FAILED; hosts read: 1 of 1; cluster: 1 of 1 online; "
                               "not in this run's Limit: node12.yoursite.mil")


class _Recorder(BaseHTTPRequestHandler):
    """The second host: notes every request and its Authorization header."""
    got = []

    def do_GET(self):
        self.got.append((self.path, self.headers.get("Authorization")))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"<title>Log in</title>")

    def log_message(self, *a):
        pass


def _redirector(to):
    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", to + self.path)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass
    return Redirect


class StatsRedirect(unittest.TestCase):
    """A stats page that answers 302 to another address: not followed, the login never sent there."""
    def setUp(self):
        _Recorder.got = []
        self.other = HTTPServer(("127.0.0.1", 0), _Recorder)
        self.to = "http://127.0.0.1:%d/idp/login" % self.other.server_port
        self.node = HTTPServer(("127.0.0.1", 0), _redirector(self.to))
        for srv in (self.other, self.node):
            threading.Thread(target=srv.serve_forever, daemon=True).start()

    def tearDown(self):
        for srv in (self.node, self.other):
            srv.shutdown()
            srv.server_close()

    def test_basic_login_not_sent_on(self):
        url = "http://127.0.0.1:%d/stats.do" % self.node.server_port
        st = nm.read_stats(1, ["/stats.do"], 1, 0, 5, [], url, auth="basic", login=("svc_aap", "S3cret-pw!"))
        self.assertEqual(_Recorder.got, [])
        self.assertFalse(st["up"])
        self.assertEqual(st["status"], 302)
        self.assertIn("HTTP 302", st["why"])
        self.assertIn(self.to + "/stats.do (not followed)", st["why"])
        self.assertNotIn("S3cret", json.dumps(st))

    def test_auto_redirect_asks_for_the_login_but_still_not_followed(self):
        st = nm.read_stats(self.node.server_port, ["/stats.do"], 1, 0, 5, [], login=("svc_aap", "S3cret-pw!"))
        self.assertEqual(_Recorder.got, [])
        self.assertFalse(st["up"])
        self.assertIn("http://127.0.0.1:%d/stats.do (with the login) -> 302 sent to %s/stats.do (not followed)"
                      % (self.node.server_port, self.to), st["tried"])

    def test_fetch(self):
        code, body = nm.fetch("http://127.0.0.1:%d/stats.do" % self.node.server_port, "", 5, ("svc_aap", "S3cret-pw!"))
        self.assertEqual((code, body), (302, "sent to %s/stats.do (not followed)" % self.to))
        self.assertEqual(_Recorder.got, [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
