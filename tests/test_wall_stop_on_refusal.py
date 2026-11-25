#!/usr/bin/env python3
"""1.130.0 STOP ON ANY DOUBT - the wall's login protection, on a fake clock and a fake transport that records every
credentialed send (a vCenter Login, every ONTAP GET). Neon's round-4 cases, the round-4 repro journal cases, Codex round 5
#1-#9 / #24-#26, Neon round 5 #1-#10 and Codex round 6 #1-#7 / #13-#15, to the rules:
  ANY outcome after the credential was sent that is not a confirmed success (a refusal, 401 / 403, NoPermission, a
  timeout, a lost or odd answer, a 5xx) stops EVERY login to every source; the cycle ends at once (no Logout, no further
  source); a poll still marked at the start is a stop; any doubt about the journal is a stop; only a NEW reset token from
  Site portal - deploy starts the logins again, once; one collector per data volume (a lock, or no logins).

Run: python3 tests/test_wall_stop_on_refusal.py"""
import http.client
import json
import os
import shutil
import socket
import ssl
import sys
import tempfile
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "roles", "site_portal_stack", "files"))
import wall_live as W  # noqa: E402

WINDOW = 900
SOAP = "http://schemas.xmlsoap.org/soap/envelope/"


class Clocks(object):
    def __init__(self, wall=1000.0, mono=100.0):
        self.w, self.m = wall, mono

    def wall(self):
        return self.w

    def mono(self):
        return self.m

    def step(self, s):
        self.w += s
        self.m += s


def env(body):
    return ('<?xml version="1.0"?><soapenv:Envelope xmlns:soapenv="%s" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<soapenv:Body>%s</soapenv:Body></soapenv:Envelope>' % (SOAP, body)).encode()


def fault(kind, text="fault"):
    return env('<soapenv:Fault><faultcode>ServerFaultCode</faultcode><faultstring>%s</faultstring><detail><%sFault xmlns="urn:vim25" '
               'xsi:type="%s"/></detail></soapenv:Fault>' % (text, kind, kind))


class Net(object):
    """the fake transport: per host, a script; every credential-bearing send recorded"""

    def __init__(self, clocks):
        self.clocks = clocks
        self.sent = []                     # (host, what) for every Login / ONTAP GET that left
        self.calls = []                    # every request (host, method or path)
        self.vc = {}                       # host -> dict(state)
        self.on = {}                       # host -> dict(state)
        self.connect_error = {}            # host -> exception class/instance raised by connect()
        self.hook = None                   # hook(host, what) -> may raise / return a (status, headers, body) to answer instead

    def add_vc(self, host, password="pass", **kw):
        self.vc[host] = dict({"password": password, "session": "", "n": 0, "drop": False, "cookie": True, "key": True}, **kw)

    def add_on(self, host, password="pw", **kw):
        self.on[host] = dict({"password": password, "version": {"full": "NetApp Release 9.14.1", "generation": 9, "major": 14},
                              "pages": {}}, **kw)

    def logins(self, host=None):
        return len([x for x in self.sent if host is None or x[0] == host])

    def conn(self):
        wire = self

        class Conn(object):
            def __init__(self, host, port=443, timeout=None, context=None):
                self.host, self.req = host, None

            def connect(self):
                e = wire.connect_error.get(self.host)
                if e:
                    raise e if isinstance(e, BaseException) else e("no connection")

            def request(self, method, path, body=None, headers=None):
                self.req = (method, path, body or b"", dict(headers or {}))

            def getresponse(self):
                method, path, body, headers = self.req
                if self.host in wire.vc:
                    return wire.vim(self.host, body, headers)
                return wire.ontap(self.host, path, headers)

            def close(self):
                pass
        return Conn

    def vim(self, host, body, headers):
        v = self.vc[host]
        b = body.decode() if isinstance(body, bytes) else body
        m = b.split("<soapenv:Body><", 1)[1].split(" ", 1)[0].split(">", 1)[0]
        self.calls.append((host, m))
        if m == "Login":
            self.sent.append((host, "Login"))
        if self.hook:
            r = self.hook(host, m)
            if r is not None:
                return Resp(*r)
        cookie = headers.get("Cookie", "")
        if m == "RetrieveServiceContent":
            return Resp(200, [], env('<RetrieveServiceContentResponse xmlns="urn:vim25"><returnval><rootFolder type="Folder">g</rootFolder>'
                                     '<propertyCollector type="PropertyCollector">pc</propertyCollector><viewManager type="ViewManager">vm'
                                     '</viewManager><sessionManager type="SessionManager">sm</sessionManager></returnval>'
                                     '</RetrieveServiceContentResponse>'))
        if m == "Login":
            if "<password>%s</password>" % v["password"] not in b:
                return Resp(500, [], fault("InvalidLogin", "Cannot complete login due to an incorrect user name or password."))
            v["n"] += 1
            v["session"] = 'vmware_soap_session="s%d"' % v["n"]
            hdr = [("Set-Cookie", v["session"] + "; Path=/")] if v["cookie"] else []
            return Resp(200, hdr, env('<LoginResponse xmlns="urn:vim25"><returnval><key>%s</key></returnval></LoginResponse>'
                                      % ("s%d" % v["n"] if v["key"] else "")))
        if m == "Logout":
            v["session"] = ""
            return Resp(200, [], env('<LogoutResponse xmlns="urn:vim25"/>'))
        if not cookie or cookie != v["session"] or v["drop"]:
            v["drop"] = False
            v["session"] = ""
            return Resp(500, [], fault("NotAuthenticated", "The session is not authenticated."))
        if m == "CreateContainerView":
            return Resp(200, [], env('<CreateContainerViewResponse xmlns="urn:vim25"><returnval type="ContainerView">view-1</returnval>'
                                     '</CreateContainerViewResponse>'))
        return Resp(200, [], env('<RetrievePropertiesExResponse xmlns="urn:vim25"></RetrievePropertiesExResponse>'))

    def ontap(self, host, path, headers):
        o = self.on[host]
        self.calls.append((host, path.split("?")[0]))
        self.sent.append((host, path.split("?")[0]))
        if self.hook:
            r = self.hook(host, path.split("?")[0])
            if r is not None:
                return Resp(*r)
        import base64
        auth = base64.b64decode(headers.get("Authorization", "Basic ").split(" ", 1)[1] or b"").decode()
        if auth.split(":", 1)[-1] != o["password"]:
            return Resp(401, [], b'{"error": {"message": "not authorized"}}')
        p = path.split("?")[0]
        if p == "/api/cluster":
            return Resp(200, [], json.dumps({"name": "c1", "version": o["version"]}).encode())
        pages = o["pages"].get(p, 1)
        n = int(path.split("&start=")[1]) if "&start=" in path else 0
        d = {"records": [], "num_records": 0}
        if n + 1 < pages:
            d["_links"] = {"next": {"href": "%s?fields=x&start=%d" % (p, n + 1)}}
        return Resp(200, [], json.dumps(d).encode())


class Resp(object):
    def __init__(self, status, headers, body):
        self.status, self._h, self._b = status, headers, body

    def read(self):
        return self._b

    def getheaders(self):
        return self._h


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        self.addCleanup(self._clean, self.d)
        self.path = os.path.join(self.d, "wall-backoff.json")
        self.c = Clocks()
        self.wire = Net(self.c)
        self.deploy()                                          # Site portal - deploy: its token (an empty volume waits for one)
        p = mock.patch.object(http.client, "HTTPSConnection", self.wire.conn())
        p.start()
        self.addCleanup(p.stop)

    @staticmethod
    def _clean(d):
        if not os.path.isdir(d):
            return
        os.chmod(d, 0o700)
        for root, dirs, files in os.walk(d):
            for x in dirs + files:
                try:
                    os.chmod(os.path.join(root, x), 0o700)
                except OSError:
                    pass
        shutil.rmtree(d, True)

    def vc(self, host="vc1", user="A", pw="pass"):
        if host not in self.wire.vc:
            self.wire.add_vc(host)
        return W.VcenterSource(host, user, pw, verify=False)

    def ontap(self, host="on1", user="A", pw="pw"):
        if host not in self.wire.on:
            self.wire.add_on(host)
        return W.OntapSource(host, user, pw, verify=False)

    def coll(self, *srcs, log=None):
        return W.Collector(list(srcs), seconds=60, auth_retry=WINDOW, clock=self.c.wall, mono=self.c.mono, state_file=self.path, log=log)

    def deploy(self, token=None):
        """Site portal - deploy: a new reset token in the data volume (the real writer: a missing or doubtful journal gets a
        clear start too); a token that is not 32 hex is written raw (the writer refuses it)"""
        import secrets
        import test_netapp_live  # noqa: F401  (its paths: the portal's modules)
        import site_portal as sp
        tok = token if token is not None else secrets.token_hex(16)
        try:
            sp.write_wall_token(self.d, tok)
        except ValueError:
            with open(os.path.join(self.d, "wall-reset-token"), "wb") as fh:
                fh.write(tok.encode("utf-8") if isinstance(tok, str) else tok)
        return tok

    def restart(self, col, *srcs):
        col.stop()
        if col.lock_fd is not None:
            col.lock_fd is not None and os.close(col.lock_fd)
        return self.coll(*srcs)

    def journal(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return None

    def h(self, src):
        return W.Collector._hkey(src.key)

    def raw(self, b):
        with open(self.path, "wb") as fh:
            fh.write(b)

    def states(self, col):
        return set(x["state"] for x in col.view()["sources"].values())

    def run_for(self, col, seconds, step=60):
        end = self.c.m + seconds
        while self.c.m + step <= end:
            self.c.step(step)
            col.cycle()

    def until_mono(self, col, at, step=1.0):
        """cycles from now until mono reaches `at` (exclusive), 60 s apart, then exactly at `at - step`"""
        while self.c.m + 60 < at:
            self.c.step(60)
            col.cycle()
        self.c.w += (at - step) - self.c.m
        self.c.m = at - step
        col.cycle()

    def assertHeld(self, entry, until, mono):
        self.assertEqual((entry["until"], entry["mono"]), (until, mono))


    def stopped(self):
        j = self.journal()
        return bool(j and j["stopped"])



    def assertAllStopped(self, col, hours=3):
        n = self.wire.logins()
        self.run_for(col, hours * 3600)
        self.assertEqual(self.wire.logins(), n, "a credentialed send while stopped")
        self.assertTrue(col.stopped)
        self.assertEqual(self.states(col), {"auth"})


class Lifecycle(Base):
    """Neon r4 1-7; Codex r5 #3, #4, #6; Neon r5 #1-#4, #9 (one collector per volume)"""

    def test_n01_clean_vcenter_login(self):
        col = self.coll(self.vc())
        col.cycle()
        self.c.step(60)
        col.cycle()
        self.assertEqual(self.wire.logins(), 1)
        j = self.journal()
        self.assertEqual((j["v"], j["stopped"], j["inflight"]), (5, False, []))
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        raw = open(self.path, "rb").read()
        self.assertNotIn(b"pass", raw)

    def test_n02_written_before_every_poll(self):
        for make in (self.vc, self.ontap):
            with self.subTest(make.__name__):
                self.setUp()
                seen = []
                self.wire.hook = lambda host, what: seen.append(self.journal()["inflight"]) or None
                src = make()
                col = self.coll(src)
                col.cycle()
                self.assertTrue(seen and all(x == [self.h(src)] for x in seen))
                self.assertEqual(self.journal()["inflight"], [])

    def test_n03_ontap_paged_poll(self):
        self.wire.add_on("on1")
        self.wire.on["on1"]["pages"]["/api/storage/aggregates"] = 2
        col = self.coll(self.ontap())
        col.cycle()
        self.assertEqual(self.wire.logins(), 7)
        self.assertEqual(self.journal()["inflight"], [])

    def test_n04_no_lock_no_logins(self):
        """Neon r5 #3 / Codex r5 #4: the lock cannot be made or opened - no logins"""
        os.chmod(self.d, 0o500)
        col = self.coll(self.vc(), self.ontap())
        self.run_for(col, 600)
        self.assertEqual(self.wire.logins(), 0)
        self.assertIn("login off", list(col.view()["sources"].values())[0]["why"])
        self.setUp()
        lock = os.path.join(self.d, "wall-collector.lock")
        open(lock, "w").close()
        os.chmod(lock, 0)
        col = self.coll(self.vc())
        self.run_for(col, 600)
        self.assertEqual(self.wire.logins(), 0)

    def test_n05_n06_the_mark_cannot_be_written(self):
        for where in ("file", "folder"):
            with self.subTest(where):
                self.setUp()
                col = self.coll(self.vc())
                real = os.fsync

                def fs(fd, w=where):
                    isdir = os.path.isdir("/proc/self/fd/%d" % fd)
                    if (w == "folder") == isdir:
                        raise OSError("fsync failed")
                    return real(fd)
                with mock.patch.object(os, "fsync", fs):
                    col.cycle()
                self.assertEqual(self.wire.logins(), 0)
                self.assertEqual([x for x in os.listdir(self.d) if x.endswith(".tmp")], [])

    def test_n07_a_mark_left_at_the_start_is_a_stop(self):
        """Codex r6 #3 (d): a poll still marked at the start - its outcome is not known - stops every login"""
        src = self.vc()
        col = self.coll(src)
        real = col._change
        col._change = lambda m: False if col.journal["inflight"] else real(m)   # the clear after the poll cannot be written
        col.cycle()
        self.assertEqual(self.journal()["inflight"], [self.h(src)])
        col2 = self.restart(col, self.vc())
        self.assertAllStopped(col2)
        self.assertEqual(self.wire.logins(), 1)

    def test_two_collectors_one_volume(self):
        c1 = self.coll(self.vc("vc1"))
        c2 = self.coll(self.vc("vc1"), self.ontap())
        self.run_for(c2, 600)
        self.assertEqual(self.wire.logins(), 0)
        self.assertIn("login off", list(c2.view()["sources"].values())[0]["why"])
        c1.cycle()
        self.assertEqual(self.wire.logins(), 1)


class AnyDoubtStopsEverything(Base):
    """ANY outcome after the credential left that is not a confirmed success stops every login (Codex r6 #1, #2; Neon r4
    8-18; Codex r5 #1, #2, #8, #9; Neon r5 #5-#7)"""

    def test_r6_1_a_proxy_502_never_repeats_the_password(self):
        self.wire.hook = lambda h, w: (502, [], b"") if w == "/api/cluster" else None
        col = self.coll(self.ontap("on1"), self.ontap("on2"))
        col.cycle()
        self.assertEqual(self.wire.logins(), 1)                 # the second cluster is not tried
        self.assertAllStopped(col, 1)

    def test_r6_2_no_logout_and_no_further_source_after_a_stop(self):
        for status, body in ((403, b""), (502, b"")):
            with self.subTest(status=status):
                self.setUp()
                self.wire.hook = lambda h, w, s=status, b=body: (s, [], b) if w == "CreateContainerView" else None
                col = self.coll(self.vc(), self.ontap())
                col.cycle()
                self.assertNotIn(("vc1", "Logout"), self.wire.calls)
                self.assertEqual(self.wire.logins("on1"), 0)
                self.assertAllStopped(col, 1)
                col.stop()                                     # not even on shutdown
                self.assertNotIn(("vc1", "Logout"), self.wire.calls)
        self.setUp()                                           # NoPermission on RetrieveServiceContent
        self.wire.hook = lambda h, w: (500, [], fault("NoPermission", "denied")) if w == "RetrieveServiceContent" else None
        col = self.coll(self.vc(), self.ontap())
        col.cycle()
        self.assertEqual(self.wire.logins(), 0)
        self.assertAllStopped(col, 1)

    def test_refusals_and_unclear_answers_on_login(self):
        cases = [(500, fault("InvalidLogin", "bad password")), (401, b"Unauthorized"), (403, b""), (401, fault("", "Unauthorized")),
                 (500, fault("SystemError", "internal")), (500, fault("TooManyLogins", "too many")), (500, fault("RuntimeFault", "Anmeldung")),
                 (500, b"oops"), (502, b""), (503, b""), (504, b""), (429, b""), (302, b""), (200, b"<soapenv:Env"), (200, env("")),
                 (200, env('<OtherResponse xmlns="urn:vim25"><returnval><key>k</key></returnval></OtherResponse>'))]
        for status, body in cases:
            with self.subTest(status=status, body=body[:30]):
                self.setUp()
                self.wire.hook = lambda h, w, s=status, b=body: (s, [], b) if h == "vc1" and w == "Login" else None
                col = self.coll(self.vc("vc1"), self.vc("vc2", "B"), self.ontap("on1", "C"))
                col.cycle()
                self.assertEqual(self.wire.logins(), 1)          # vc2 and on1 (other accounts) never tried
                self.assertTrue(self.stopped())
                self.assertAllStopped(col, 1)

    def test_errors_after_the_login_left(self):
        for err in (socket.timeout("timed out"), ConnectionResetError("reset"), http.client.IncompleteRead(b"x"), ssl.SSLError("bad record")):
            with self.subTest(err=type(err).__name__):
                self.setUp()
                self.wire.hook = lambda h, w, e=err: (_ for _ in ()).throw(e) if w == "Login" else None
                col = self.coll(self.vc(), self.ontap())
                col.cycle()
                self.assertEqual(self.wire.logins("on1"), 0)
                self.assertAllStopped(col, 1)

    def test_login_without_cookie_or_key(self):
        for case in ("cookie", "key"):
            with self.subTest(case):
                self.setUp()
                self.wire.add_vc("vc1", cookie=case != "cookie", key=case != "key")
                col = self.coll(self.vc())
                col.cycle()
                self.assertAllStopped(col, 1)

    def test_no_permission_or_new_session_refused(self):
        """Neon r5 #5, #6; Codex r5 #2"""
        for where, kind in (("CreateContainerView", "NoPermission"), ("RetrievePropertiesEx", "NoPermission"),
                            ("CreateContainerView", "NotAuthenticated"), ("RetrievePropertiesEx", "NotAuthenticated")):
            with self.subTest(where=where, kind=kind):
                self.setUp()
                self.wire.hook = lambda h, w, x=where, k=kind: (500, [], fault(k, "no")) if w == x else None
                col = self.coll(self.vc(), self.ontap())
                col.cycle()
                self.assertEqual(self.wire.logins("on1"), 0)
                self.assertAllStopped(col, 1)
        self.setUp()                                           # the kept session expires; the re-login's first read refused
        col = self.coll(self.vc())
        col.cycle()
        self.wire.vc["vc1"]["drop"] = True
        state = {"n": 0}

        def after_relogin(h, w):
            if w == "Login":
                state["n"] += 1
            if w == "RetrievePropertiesEx" and state["n"]:
                return 500, [], fault("NotAuthenticated", "not authenticated")
        self.wire.hook = after_relogin
        self.c.step(60)
        col.cycle()
        self.assertEqual(self.wire.logins(), 2)
        self.assertAllStopped(col, 1)

    def test_ontap_any_failed_get(self):
        """every ONTAP GET carries the password: any failure stops (Neon r5 #7: an error in a 200, null)"""
        cases = [(401, b"{}"), (403, b"{}"), (500, b"{}"), (503, b"{}"), (429, b"{}"), (302, b""), (400, b"<html>proxy</html>"), (200, b"{not json"),
                 (200, b"null"), (200, b'{"error": {"message": "x"}}'), socket.timeout("t"), ConnectionResetError("r")]
        for path in ("/api/cluster", "/api/storage/aggregates"):
            for case in cases:
                with self.subTest(path=path, case=str(case)[:30]):
                    self.setUp()

                    def hook(h, w, x=case, p=path):
                        if w == p and h == "on1":
                            if isinstance(x, BaseException):
                                raise x
                            return x[0], [], x[1]
                    self.wire.hook = hook
                    col = self.coll(self.ontap("on1"), self.vc())
                    col.cycle()
                    self.assertNotIn(("on1", "/api/storage/volumes"), self.wire.calls)
                    self.assertEqual(self.wire.logins("vc1"), 0)
                    self.assertAllStopped(col, 1)

    def test_no_connection_sends_nothing(self):
        for err in (ConnectionRefusedError("refused"), socket.gaierror("no name"), ssl.SSLCertVerificationError("bad cert")):
            with self.subTest(err=type(err).__name__):
                self.setUp()
                self.wire.connect_error["vc1"] = err
                col = self.coll(self.vc("vc1"), self.vc("vc2"))
                col.cycle()
                self.assertEqual((self.wire.logins("vc1"), self.wire.logins("vc2")), (0, 1))
                self.assertEqual(self.journal()["inflight"], [])
                self.assertFalse(col.stopped)
                del self.wire.connect_error["vc1"]
                self.c.step(60)
                col.cycle()
                self.assertEqual(self.wire.logins("vc1"), 1)

    def test_old_ontap_one_nodes_get(self):
        self.wire.add_on("on1", version={"full": "NetApp Release 9.7P1", "generation": 9, "major": 7})
        col = self.coll(self.ontap())
        col.cycle()
        self.assertEqual(len([p for h, p in self.wire.calls if p == "/api/cluster/nodes"]), 1)
        self.assertFalse(col.stopped)

    def test_any_spelling_any_source(self):
        """Codex r5 #9: one directory account under two names - stopped globally"""
        self.wire.add_vc("vc1", password="right")
        col = self.coll(self.vc("vc1", "CORP\\svc01", "wrong"), self.ontap("on1", "ops-monitor@corp.example.mil"))
        col.cycle()
        self.assertEqual(self.wire.logins(), 1)
        self.assertAllStopped(col, 1)

    def test_a_kept_session_is_not_read_while_stopped(self):
        col = self.coll(self.vc(), self.ontap())
        col.cycle()
        self.wire.on["on1"]["password"] = "changed"
        self.c.step(60)
        col.cycle()
        calls = len(self.wire.calls)
        self.run_for(col, 3600)
        self.assertEqual(len(self.wire.calls), calls)

    def test_r6_3_a_stop_that_cannot_be_written_survives_a_restart(self):
        """Codex r6 #3 and r5 #8: refused (on a login, and on a kept session's read), the stop not written - the mark stops the restart"""
        for case in ("login", "kept"):
            with self.subTest(case):
                self.setUp()
                col = self.coll(self.vc())
                if case == "kept":
                    col.cycle()
                    self.wire.hook = lambda h, w: (403, [], b"") if w == "RetrievePropertiesEx" else None
                else:
                    self.wire.add_vc("vc1", password="other")
                real = col._change

                def no_stop(m):
                    probe = {"v": 5, "stopped": False, "at": None, "why": "", "token": "", "inflight": [self.h(col.sources[0])]}
                    m(probe)
                    return False if probe["stopped"] else real(m)
                col._change = no_stop
                self.c.step(60)
                col.cycle()
                self.assertTrue(col.stopped)
                n = self.wire.logins()
                self.wire.hook = None
                col2 = self.restart(col, self.vc())
                self.assertAllStopped(col2, 2)
                self.assertEqual(self.wire.logins(), n)


class NeonR6(Base):
    """Neon r6 #8, #9"""

    def test_n8_a_lock_held_by_an_old_portal_is_tried_again(self):
        old = self.coll(self.vc("vc1"))
        new = self.coll(self.vc("vc1"))                        # the redeploy's portal while the old one still stops
        new.cycle()
        self.assertEqual(self.wire.logins(), 0)
        old.stop()                                             # the old one ends: its lock released
        self.c.step(60)
        new.cycle()
        self.assertEqual(self.wire.logins(), 1)

    def test_n9_407_namespaced_faults_and_errors_before_counts(self):
        for status in (407,):
            with self.subTest(status=status):
                self.setUp()
                self.wire.hook = lambda h, w, st=status: (st, [], b"") if w in ("Login", "/api/cluster") else None
                col = self.coll(self.vc(), self.ontap())
                col.cycle()
                self.assertEqual(self.wire.logins(), 1)
                self.assertTrue(col.stopped)
        self.setUp()
        nsfault = env('<soapenv:Fault><faultcode>ServerFaultCode</faultcode><faultstring>denied</faultstring><soapenv:detail>'
                      '<SecurityErrorFault xmlns="urn:vim25" xsi:type="SecurityError"/></soapenv:detail></soapenv:Fault>')
        self.wire.hook = lambda h, w: (500, [], nsfault) if w == "RetrieveServiceContent" else None
        col = self.coll(self.vc(), self.ontap())
        col.cycle()
        self.assertEqual(self.wire.logins(), 0)
        self.assertTrue(col.stopped)
        s = self.ontap()
        s._get = lambda path: {"records": [], "num_records": 3, "errors": [{"message": "x"}]}
        with self.assertRaises(RuntimeError):                  # the error wins over the count: the poll stops
            s.records("/api/cluster/nodes", "state")


class R7(Base):
    """Codex r7 #2, #4, #5, #18; Neon r7 #1-#3 - the bar: no account gets a second failed authentication until a person deploys"""

    def test_r7_4_a_token_from_a_healthy_deploy_never_ends_a_later_stop(self):
        col = self.coll(self.vc())
        col.cycle()                                            # healthy
        self.deploy()                                          # a deploy while healthy: its token is just there
        self.wire.vc["vc1"].update(password="rotated", drop=True)
        self.c.step(60)
        col.cycle()                                            # the session ends; the re-login refused: stopped with that token
        n = self.wire.logins()
        self.run_for(col, 3 * 3600)
        self.assertEqual(self.wire.logins(), n)

    def test_neon_r7_2_one_deploy_one_failed_login(self):
        """the deploy writes its token BEFORE the service restarts: a refusal at the first poll is recorded with it"""
        col = self.coll(self.vc())
        col.cycle()
        col.stop()
        self.deploy()                                          # the deploy (a bad credential this time): token first ...
        self.wire.vc["vc1"]["password"] = "rotated"
        col = self.coll(self.vc())                             # ... then the restart
        self.run_for(col, 3 * 3600)
        self.assertEqual(self.wire.logins(), 2)               # the first run's login + ONE refused - never a second
        self.assertTrue(col.stopped)

    def test_neon_r7_3_no_separate_history_to_lose(self):
        col = self.coll(self.vc(pw="wrong"))
        self.wire.add_vc("vc1", password="right")
        col.cycle()
        col.stop()
        for junk in ("wall-token-used",):                      # a file of the old design: never read
            open(os.path.join(self.d, junk), "w").write("garbage")
        col = self.coll(self.vc(pw="wrong"))
        self.run_for(col, 3600)
        self.assertEqual(self.wire.logins(), 1)

    def test_neon_r7_4_a_login_that_left_counts_whatever_fails_after(self):
        """Login sent and answered, then the view's own connection cannot open: the password left - stopped, no Login every 60 s"""
        col = self.coll(self.vc(), self.ontap())

        def hook(h, w):
            if w == "Login":
                self.wire.connect_error["vc1"] = ConnectionRefusedError("refused")   # the next connection (the view) cannot open
        self.wire.hook = hook
        col.cycle()
        self.wire.hook = None
        self.wire.connect_error.pop("vc1", None)
        self.run_for(col, 3600)
        per_account = {h: self.wire.logins(h) for h in ("vc1", "on1")}
        self.assertEqual(per_account, {"vc1": 1, "on1": 0})
        self.assertTrue(col.stopped)

    def test_neon_r7_4_no_connection_for_the_login_itself_sent_nothing(self):
        self.wire.add_vc("vc1")
        col = self.coll(self.vc())
        state = {"n": 0}
        real = self.wire.conn()

        class Conn(real):
            def connect(me):
                if me.host == "vc1":
                    state["n"] += 1
                    if state["n"] == 2:                        # RetrieveServiceContent opens; the Login's connection does not
                        raise ConnectionRefusedError("refused")
                return real.connect(me)
        with mock.patch.object(http.client, "HTTPSConnection", Conn):
            col.cycle()
        self.assertEqual(self.wire.logins("vc1"), 0)
        self.assertFalse(col.stopped)

    def test_r7_18_a_source_name_that_cannot_be_encoded(self):
        class Odd(object):                                     # a name that cannot be encoded; nothing sent
            kind, key, name, login_sent, secret = "ontap", "ontap:\ud800bad", "\ud800bad", False, None

            def poll(self):
                raise W.NotSent("no connection")

            def close(self):
                pass
        bad, good = Odd(), self.vc()
        col = self.coll(bad, good)
        col.cycle()                                            # never raises; the good source is read
        self.assertEqual(col.view()["sources"]["vcenter:vc1"]["state"], "ok")

    def test_r7_2_a_fault_text_never_triggers_a_new_login(self):
        col = self.coll(self.vc())
        col.cycle()
        self.wire.hook = lambda h, w: (500, [], fault("SystemError", "the session is not authenticated (sort of)")) if w == "RetrievePropertiesEx" else None
        self.c.step(60)
        col.cycle()
        self.assertEqual(self.wire.logins(), 1)               # no new password login on a text match


class Resume(Base):
    """only a NEW token from Site portal - deploy starts stopped logins - once (Codex r6 #4, #5, #6; r5 #5)"""

    def stop(self):
        self.wire.add_vc("vc1", password="right")
        col = self.coll(self.vc(pw="wrong"))
        col.cycle()
        self.assertTrue(col.stopped)
        return col

    def test_a_new_token_starts_once_without_a_restart(self):
        col = self.stop()
        self.run_for(col, 600)
        self.assertEqual(self.wire.logins(), 1)
        self.deploy()
        self.c.step(60)
        col.cycle()                                            # the next cycle sees it: tried once - refused again: stopped
        self.assertEqual(self.wire.logins(), 2)
        self.assertAllStopped(col, 2)                          # the same token never starts them twice

    def test_restarts_never_replay_a_token(self):
        self.deploy()
        col = self.stop()
        for _ in range(3):
            col = self.restart(col, self.vc(pw="wrong"))
            self.run_for(col, 3600)
        self.assertEqual(self.wire.logins(), 1)
        self.wire.vc["vc1"]["password"] = "wrong"              # the fixed credential, and a deploy
        self.deploy()
        col = self.restart(col, self.vc(pw="wrong"))
        col.cycle()
        self.assertEqual((self.wire.logins(), col.stopped), (2, None))

    def test_r6_4_a_new_source_or_a_swapped_password_is_not_a_reset(self):
        col = self.stop()
        col = self.restart(col, self.vc(pw="wrong"), self.ontap("on2"))   # a cluster added (once a "new fingerprint")
        self.run_for(col, 3600)
        self.assertEqual(self.wire.logins(), 1)

    def test_r6_5_only_a_strict_32_hex_token(self):
        for tok in ("界" * 150, "A" * 32, "0" * 31, "0" * 33, "g" * 32, "", "0" * 32 + " extra", "x" * 300):
            with self.subTest(tok=tok[:12]):
                self.setUp()
                col = self.stop()
                self.deploy(tok)
                self.run_for(col, 600)
                self.assertEqual(self.wire.logins(), 1)

    def test_r6_5_the_clear_record_cannot_be_written(self):
        col = self.stop()
        self.deploy()
        with mock.patch.object(W.Collector, "_save", lambda me, d: False):
            self.run_for(col, 600)
        self.assertEqual(self.wire.logins(), 1)

    def test_r6_6_a_damaged_stop_record_stays_stopped(self):
        col = self.stop()
        col.stop()
        col.lock_fd is not None and os.close(col.lock_fd)
        j = self.journal()
        for damage in ({"why": ""}, {"at": None}, {"fp": ""}, {"stopped": "yes"}, {"inflight": None}):
            with self.subTest(damage=damage):
                self.raw(json.dumps(dict(j, **damage)).encode())
                c = self.coll(self.vc(pw="wrong"))
                self.run_for(c, 1800)
                self.assertEqual(self.wire.logins(), 1)
                c.stop()
                c.lock_fd is not None and os.close(c.lock_fd)

    def test_r6_7_one_wall_host_per_site(self):
        t = open(os.path.join(os.path.dirname(HERE), "roles", "site_portal_stack", "tasks", "main.yml"), encoding="utf-8").read()
        self.assertIn("the wall's collector runs on one portal host only", t)
        self.assertIn("(_wall_hosts | int) <= 1", t)
        self.assertIn("--wall-token", t)


class Journal(Base):
    """any doubt about the journal is a stop (r4 repro p2_1, Neon r4 38-43, Codex r5 #7, #24, Codex r6 #14)"""

    def make_lock(self):
        open(os.path.join(self.d, "wall-collector.lock"), "w").close()
        for n in ("wall-reset-token", "wall-backoff.json"):
            if os.path.exists(os.path.join(self.d, n)):
                os.unlink(os.path.join(self.d, n))           # no deploy since: the doubt stays a stop

    def payloads(self):
        good = {"v": 5, "stopped": False, "at": None, "why": "", "token": "", "inflight": []}
        j = lambda **kw: json.dumps(dict(good, **kw)).encode()   # noqa: E731
        return [b"", b"\xff\xfe\x00\x80", b"{not json", b"[]", b"null", b"123", b'"s"', j(v=4), j(v=True), b'{"v": 5}', j(extra=1),
                j(stopped=None), j(stopped="no"), b'{"v": 5, "stopped": false, "at": NaN, "why": "", "token": "", "inflight": []}',
                b'{"v": 5, "at": 1e400}', j(at=-5), j(at="1900"), j(inflight={}), j(inflight=[1]), j(token=None),
                ('{"v": 5, "stopped": false, "at": %s, "why": "", "token": "", "inflight": []}' % ("9" * 401)).encode(),
                b"[" * 100000 + b"]" * 100000, b'{"v": 5, "v": 5}', b" " * (70 * 1024) + b"{}",
                # Codex r7 #6: records that contradict themselves
                j(at=1000.0), j(why="refused"), j(token="0" * 32), j(stopped=True), j(stopped=True, at=1000.0, why="x", token="nothex"),
                j(stopped=True, at=1000.0, why="", token="")]

    def test_every_doubtful_journal_stops_every_login(self):
        for raw in self.payloads() + ["missing", "unreadable", "directory", "fifo", "symlink"]:
            with self.subTest(raw=raw if isinstance(raw, str) else raw[:50]):
                self.setUp()
                self.make_lock()
                if raw == "unreadable":
                    self.raw(b"{}")
                    os.chmod(self.path, 0)
                elif raw == "directory":
                    os.mkdir(self.path)
                elif raw == "fifo":                            # Codex r6 #14: never a hang
                    os.mkfifo(self.path)
                elif raw == "symlink":
                    good = os.path.join(self.d, "elsewhere.json")
                    open(good, "w").write(json.dumps({"v": 5, "stopped": False, "at": None, "why": "", "token": "", "inflight": []}))
                    os.symlink(good, self.path)
                elif raw != "missing":
                    self.raw(raw)
                before = open(self.path, "rb").read() if isinstance(raw, bytes) else None
                col = self.coll(self.vc(), self.ontap())
                self.run_for(col, 2 * 3600)
                self.assertEqual(self.wire.logins(), 0)
                self.assertIn("Site portal - deploy", list(col.view()["sources"].values())[0]["why"])
                if before is not None:
                    self.assertEqual(open(self.path, "rb").read(), before)

    def test_a_new_token_ends_a_doubtful_journal(self):
        self.make_lock()
        self.raw(b"{not json")
        col = self.coll(self.vc())
        self.deploy()
        self.c.step(60)
        col.cycle()
        self.assertEqual(self.wire.logins(), 1)
        self.assertFalse(self.journal()["stopped"])

    def test_an_empty_volume_waits_for_the_deploys_token(self):
        """Neon r6 #2 / Codex r5 #7: no journal (a new, emptied or restored volume) is never "first use" - it waits for a token"""
        os.unlink(os.path.join(self.d, "wall-reset-token"))
        os.unlink(self.path)                                   # an empty volume: no token, no journal
        open(os.path.join(self.d, "wall-collector.lock"), "w").close()
        col = self.coll(self.vc())
        self.run_for(col, 3600)
        self.assertEqual(self.wire.logins(), 0)
        self.deploy()
        self.c.step(60)
        col.cycle()
        self.assertEqual(self.wire.logins(), 1)
        col.stop()
        os.unlink(self.path)                                   # the journal gone later: stopped again (the token is spent)
        col = self.coll(self.vc())
        self.run_for(col, 3600)
        self.assertEqual(self.wire.logins(), 1)

    def test_r6_14_a_fifo_as_the_token_or_the_lock(self):
        self.deploy()
        col = self.coll(self.vc())
        col.cycle()
        col.stop()
        col.lock_fd is not None and os.close(col.lock_fd)
        os.unlink(os.path.join(self.d, "wall-reset-token"))
        os.mkfifo(os.path.join(self.d, "wall-reset-token"))
        self.assertEqual(self.coll(self.vc())._token(), W.UNREADABLE)        # returns at once
        self.setUp()
        os.mkfifo(os.path.join(self.d, "wall-collector.lock"))
        col = self.coll(self.vc())
        self.run_for(col, 600)
        self.assertEqual(self.wire.logins(), 0)


class Crashes(Base):
    """nothing about the wall stops the portal or the collector (Codex r5 #24-#26, r6 #13, #15)"""

    def test_r5_24_huge_numbers_and_deep_nesting(self):
        for raw in (('{"v": 4, "stopped": false, "at": %s, "why": "", "inflight": []}' % ("9" * 401)).encode(), b"[" * 200000 + b"]" * 200000):
            self.setUp()
            open(os.path.join(self.d, "wall-collector.lock"), "w").close()
            self.raw(raw)
            col = self.coll(self.vc())
            self.assertTrue(col.stopped)

    def test_r5_25_a_repeated_page_token_ends_the_read(self):
        page = env('<RetrievePropertiesExResponse xmlns="urn:vim25"><returnval><token>T</token><objects><obj type="HostSystem">h1</obj>'
                   '</objects></returnval></RetrievePropertiesExResponse>')
        cont = env('<ContinueRetrievePropertiesExResponse xmlns="urn:vim25"><returnval><token>T</token></returnval></ContinueRetrievePropertiesExResponse>')
        col = self.coll(self.vc(), self.ontap())
        col.cycle()                                            # a good first cycle: the session kept
        self.wire.hook = lambda h, w: (200, [], page) if w == "RetrievePropertiesEx" else (200, [], cont) if w == "ContinueRetrievePropertiesEx" else None
        self.c.step(60)
        col.cycle()
        st = col.view()["sources"]["vcenter:vc1"]
        self.assertIn("repeated a page token", st["why"])
        self.assertEqual(col.view()["sources"]["ontap:on1"]["state"], "ok")
        k = {"n": 0}

        def many(h, w):
            if w == "RetrievePropertiesEx":
                return 200, [], page
            if w == "ContinueRetrievePropertiesEx":
                k["n"] += 1
                return 200, [], env('<ContinueRetrievePropertiesExResponse xmlns="urn:vim25"><returnval><token>T%d</token></returnval>'
                                    '</ContinueRetrievePropertiesExResponse>' % k["n"])
        self.wire.hook = many
        self.c.step(60)
        col.cycle()
        self.assertLessEqual(k["n"], W.PAGES_MAX)

    def test_r5_25b_a_continuation_without_an_answer_fails_the_read(self):
        page = env('<RetrievePropertiesExResponse xmlns="urn:vim25"><returnval><token>T</token></returnval></RetrievePropertiesExResponse>')
        col = self.coll(self.vc())
        col.cycle()
        self.wire.hook = lambda h, w: (200, [], page) if w == "RetrievePropertiesEx" else (200, [], env('<Other xmlns="urn:vim25"/>')) if w == "ContinueRetrievePropertiesEx" else None
        self.c.step(60)
        col.cycle()
        st = col.view()["sources"]["vcenter:vc1"]
        self.assertEqual(st["state"], "error")
        self.assertIn("not whole", st["why"])

    def test_r5_26_a_tls_bundle_that_cannot_be_read(self):
        srcs = W.sources_from_config({"vcenter": {"user": "A", "password": "p", "hosts": ["vc01.corp.example.mil"]},
                                      "ontap": {"user": "A", "password": "p", "clusters": ["192.0.2.10"]}}, cafile="/nonexistent/bundle.pem")
        self.assertEqual([type(s).__name__ for s in srcs], ["BrokenSource", "BrokenSource"])
        col = self.coll(*srcs)
        col.cycle()
        self.assertEqual(self.wire.logins(), 0)
        self.assertIn("not configured correctly", col.view()["sources"]["vcenter:vc01.corp.example.mil"]["why"])

    def test_r6_13_a_failing_log_never_stops_the_collector(self):
        def bad_log(d):
            raise OSError(28, "No space left on device")
        self.wire.add_vc("vc1", password="right")
        col = self.coll(self.vc(pw="wrong"), log=bad_log)
        col.cycle()                                            # a stop - logged - no exception
        self.assertTrue(col.stopped)
        os.chmod(self.d, 0o500)
        self.setUp()
        os.chmod(self.d, 0o500)
        col = self.coll(self.vc(), log=bad_log)                # login off - logged - no exception
        col.cycle()
        import test_netapp_live  # noqa: F401  (its paths: the portal's modules)
        import site_portal as sp
        portal_log = mock.Mock()
        portal_log.add.side_effect = OSError(28, "No space left on device")
        cfg = mock.Mock(wall_opts={"high_minutes": 15, "seconds": 60}, wall_auth_retry=900, data=self.d, wall_logins={})
        with mock.patch.object(sp, "wall_sources", lambda c, *a, **kw: [self.vc()]):
            w = sp.WallLive(cfg, portal_log, None)             # never raises
        w.collector and w.collector.stop()

    def test_r6_15_bad_wall_settings_never_crash_the_portal(self):
        import test_netapp_live as T
        for env_ in ({"PORTAL_WALL": "0", "PORTAL_WALL_AUTH_RETRY_SECONDS": "15m"}, {"PORTAL_WALL_AUTH_RETRY_SECONDS": "15m"},
                     {"PORTAL_WALL_OPTS": '{"seconds": 1e400}'}, {"PORTAL_WALL_OPTS": "{not json"}, {"PORTAL_WALL_AUTH_RETRY_SECONDS": "9" * 400}):
            with self.subTest(env=env_):
                cfg = T.cfg_for(tempfile.mkdtemp(dir=os.environ.get("TMPDIR")), **env_)
                self.assertTrue(hasattr(cfg, "wall_opts"))
                self.assertTrue(cfg.ad_seconds)                    # the rest of the settings still read
                if "15m" in str(env_.values()) or "not json" in str(env_.values()):
                    self.assertFalse(cfg.wall)                     # not readable: the wall off, said why
                    self.assertIn("could not be read", cfg.wall_why)
                else:
                    self.assertEqual((cfg.wall_opts["seconds"], cfg.wall_auth_retry <= 86400), (60, True))   # out of range: the default / the cap

    def test_ontap_num_records_mismatch_is_a_failed_collection(self):
        s = self.ontap()
        s._get = lambda path: {"records": [], "num_records": 1}
        with self.assertRaises(W.CollectionFailed):
            s.records("/api/cluster/nodes", "state")


class Shutdown(Base):
    """Neon r5 #10: no credentialed send after stop(); cycles one at a time"""

    def test_stop_during_an_ontap_poll(self):
        col = self.coll(self.ontap())
        self.wire.hook = lambda h, w: col.stop_ev.set() if w == "/api/cluster" else None
        col.cycle()
        self.assertEqual(self.wire.logins(), 1)

    def test_stop_before_a_vcenter_login(self):
        col = self.coll(self.vc())
        self.wire.hook = lambda h, w: col.stop_ev.set() if w == "RetrieveServiceContent" else None
        col.cycle()
        self.assertEqual(self.wire.logins(), 0)

    def test_a_second_cycle_waits_for_the_first(self):
        self.wire.add_vc("vc1", password="right")
        go, inside = threading.Event(), threading.Event()

        def hook(h, w):
            if w == "Login" and h == "vc1":
                inside.set()
                go.wait(5)
        self.wire.hook = hook
        col = self.coll(self.vc("vc1", pw="wrong"), self.vc("vc2"))
        t1 = threading.Thread(target=col.cycle)
        t1.start()
        inside.wait(5)
        t2 = threading.Thread(target=col.cycle)
        t2.start()
        go.set()
        t1.join(5)
        t2.join(5)
        self.assertEqual(self.wire.logins(), 1)

    def test_one_ontap_source_per_address(self):
        srcs = W.sources_from_config({"ontap": {"user": "A", "password": "pw", "clusters": ["192.0.2.10", "https://192.0.2.10:443"]}})
        self.assertEqual(len(srcs), 1)



class NeonR8(unittest.TestCase):
    """Neon r8: a token with a trailing newline is no token; the deploy settles the journal before it publishes the token."""

    def test_neon_r8_1_a_token_with_a_newline_is_doubt(self):
        good = "a" * 32
        self.assertTrue(W.TOKEN_RX.match(good))
        for bad in (good + "\n", good + " ", "A" * 32, good[:-1], good + "0"):
            self.assertFalse(W.TOKEN_RX.match(bad), repr(bad))
            rec = {"v": 5, "stopped": True, "at": 1700000000, "why": "refused", "token": bad, "inflight": []}
            with self.assertRaises(ValueError):
                W._parse_journal(json.dumps(rec).encode("ascii"))

    def test_neon_r8_2_the_journal_first_the_token_last(self):
        import test_netapp_live  # noqa: F401  (its paths: the portal's modules)
        import site_portal as sp
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        with open(os.path.join(d, "wall-backoff.json"), "w") as fh:
            fh.write("{not json")                                         # in doubt: the writer gives it a clear start
        order, real = [], os.replace

        def spy(src, dst):
            order.append(os.path.basename(dst))
            return real(src, dst)
        os.replace = spy
        try:
            sp.write_wall_token(d, "b" * 32)
        finally:
            os.replace = real
        self.assertEqual(order, ["wall-backoff.json", "wall-reset-token"])
        with self.assertRaises(ValueError):
            sp.write_wall_token(d, "b" * 32 + "x")

if __name__ == "__main__":
    unittest.main()
