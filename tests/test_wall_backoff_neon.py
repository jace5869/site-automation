#!/usr/bin/env python3
"""Neon's round-4 back-off case list (~/relpub/review-wall/neon-backoff, section 3): one test per numbered case, on a fake
clock (wall, monotonic, boot id) and a fake transport that records every credential-bearing send (a vCenter Login, every
ONTAP GET). Where the coordinator's later rule differs from Neon's expectation, the test says so (case 18).

Run: python3 tests/test_wall_backoff_neon.py"""
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

    def coll(self, *srcs, boot="b1"):
        return W.Collector(list(srcs), seconds=60, auth_retry=WINDOW, clock=self.c.wall, mono=self.c.mono, boot_id=boot, state_file=self.path)

    def journal(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                return json.load(fh)["accounts"]
        except FileNotFoundError:
            return None

    def h(self, user="A"):
        return W.Collector._hkey(("account", W.account(user)))

    def seed(self, accounts, raw=None):
        with open(self.path, "wb") as fh:
            fh.write(raw if raw is not None else json.dumps({"v": 2, "accounts": accounts}).encode())

    def bo(self, until, mono, boot="b1"):
        return {"until": until, "mono": mono, "boot": boot}

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


class WriteAheadLifecycle(Base):
    def test_01_clean_vcenter_login(self):
        col = self.coll(self.vc())
        col.cycle()
        self.c.step(60)
        col.cycle()
        self.assertEqual(self.wire.logins(), 1)
        self.assertEqual(self.journal(), {})
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertNotIn(b'"A"', open(self.path, "rb").read())

    def test_02_write_ahead_before_the_send(self):
        for make in (self.vc, self.ontap):
            with self.subTest(make.__name__):
                seen = []
                self.wire.hook = lambda host, what: seen.append(self.journal()) if what in ("Login", "/api/cluster") else None
                col = self.coll(make())
                col.cycle()
                self.assertEqual(list(seen[0].values()), [{"attempt": 1000.0}])
                self.assertEqual(self.journal(), {})
                self.wire.hook = None
                os.unlink(self.path)

    def test_03_ontap_paged_poll(self):
        self.wire.add_on("on1")
        self.wire.on["on1"]["pages"]["/api/storage/aggregates"] = 2
        seen = []
        self.wire.hook = lambda host, what: seen.append(self.journal()) or None
        col = self.coll(self.ontap())
        col.cycle()
        self.assertEqual(self.wire.logins(), 7)                 # cluster, nodes, aggregates x2, volumes, luns, snapmirror
        self.assertTrue(all(list(j.values()) == [{"attempt": 1000.0}] for j in seen))
        self.assertEqual(self.journal(), {})

    def test_04_write_ahead_fails_read_only_folder(self):
        for make in (self.vc, self.ontap):
            with self.subTest(make.__name__):
                os.chmod(self.d, 0o500)
                col = self.coll(make())
                for _ in range(3):
                    col.cycle()
                    self.c.step(60)
                self.assertEqual(self.wire.logins(), 0)
                self.assertFalse(os.path.exists(self.path))
                self.assertEqual(list(col.view()["sources"].values())[0]["state"], "paused")
                os.chmod(self.d, 0o700)

    def test_05_fsync_of_the_temp_file_fails(self):
        col = self.coll(self.vc())
        real = os.fsync

        def fs(fd):
            if os.path.isdir("/proc/self/fd/%d" % fd):
                return real(fd)
            raise OSError("fsync failed")
        with mock.patch.object(os, "fsync", fs):
            col.cycle()
        self.assertEqual(self.wire.logins(), 0)
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual([x for x in os.listdir(self.d) if x.endswith(".tmp")], [])

    def test_06_fsync_of_the_folder_fails(self):
        col = self.coll(self.vc())
        real = os.fsync

        def fs(fd):
            if os.path.isdir("/proc/self/fd/%d" % fd):
                raise OSError("fsync of the folder failed")
            return real(fd)
        with mock.patch.object(os, "fsync", fs):
            col.cycle()
        self.assertEqual(self.wire.logins(), 0)
        self.assertEqual(list(self.journal().values()), [{"attempt": 1000.0}])

    def test_07_success_outcome_not_written_keeps_the_account_held(self):
        col = self.coll(self.vc("vc1"), self.vc("vc2"))
        real = col._outcome
        col._outcome = lambda cred, mu: False if mu is None else real(cred, mu)
        col.cycle()
        self.c.step(60)
        col.cycle()
        self.assertEqual((self.wire.logins("vc1"), self.wire.logins("vc2")), (1, 0))
        self.assertEqual(list(self.journal().values()), [{"attempt": 1000.0}])


class FailureOutcomes(Base):
    def run_case(self, src, hook, expect_until, expect_mono, logins=1):
        self.wire.hook = hook
        col = self.coll(src)
        col.cycle()
        self.wire.hook = None
        self.c.step(60)
        col.cycle()
        self.assertEqual(self.wire.logins(), logins)
        self.assertHeld(list(self.journal().values())[0], expect_until, expect_mono)
        return col

    def test_08_invalid_login_after_30_s(self):
        def slow(host, what):
            if what == "Login":
                self.c.step(30)
        self.wire.add_vc("vc1", password="right")
        col = self.coll(self.vc(pw="wrong"))
        self.wire.hook = slow
        col.cycle()
        self.wire.hook = None
        self.assertHeld(list(self.journal().values())[0], 1930, 1030)
        self.until_mono(col, 1030)
        self.assertEqual(self.wire.logins(), 1)
        self.wire.vc["vc1"]["password"] = "wrong"
        self.c.w += 1030 - self.c.m
        self.c.m = 1030
        col.cycle()
        self.assertEqual(self.wire.logins(), 2)

    def test_09_vcenter_401_403(self):
        for status, body in ((401, b"Unauthorized"), (403, b""), (401, b"<soap"), (401, fault("", "Unauthorized"))):
            with self.subTest(status=status, body=body[:20]):
                self.setUp()
                self.run_case(self.vc(), lambda h, w, s=status, b=body: (s, [], b) if w == "Login" else None, 1900, 1000)

    def test_10_vcenter_non_auth_faults_on_login(self):
        for kind, text in (("SystemError", "internal"), ("TooManyLogins", "too many"), ("RuntimeFault", "Anmeldung fehlgeschlagen")):
            with self.subTest(kind=kind):
                self.setUp()
                self.run_case(self.vc(), lambda h, w, k=kind, t=text: (500, [], fault(k, t)) if w == "Login" else None, 1900, 1000)

    def test_11_vcenter_errors_after_the_login_left(self):
        for err in (socket.timeout("timed out"), ConnectionResetError("reset"), http.client.IncompleteRead(b"x"), ssl.SSLError("bad record")):
            with self.subTest(err=type(err).__name__):
                self.setUp()

                def hook(h, w, e=err):
                    if w == "Login":
                        self.c.step(30)
                        raise e
                self.run_case(self.vc(), hook, 1930, 1030)

    def test_12_vcenter_odd_answers_to_login(self):
        for status, body in ((500, b"oops"), (502, b""), (503, b""), (504, b""), (429, b""), (302, b""), (200, b"<soapenv:Env"),
                             (200, env(""))):
            with self.subTest(status=status, body=body[:20]):
                self.setUp()
                self.run_case(self.vc(), lambda h, w, s=status, b=body: (s, [], b) if w == "Login" else None, 1900, 1000)

    def test_13_vcenter_login_without_cookie_key_or_loginresponse(self):
        for case in ("cookie", "key", "tag"):
            with self.subTest(case):
                self.setUp()
                self.wire.add_vc("vc1", cookie=case != "cookie", key=case != "key")
                hook = (lambda h, w: (200, [("Set-Cookie", 'vmware_soap_session="x"')], env('<OtherResponse xmlns="urn:vim25"><returnval>'
                                                                                           '<key>k</key></returnval></OtherResponse>'))
                        if w == "Login" else None) if case == "tag" else None
                self.run_case(self.vc(), hook, 1900, 1000)

    def test_14_vcenter_no_permission_after_login(self):
        for where in ("CreateContainerView", "RetrievePropertiesEx"):
            with self.subTest(where):
                self.setUp()
                self.run_case(self.vc(), lambda h, w, x=where: (500, [], fault("NoPermission", "denied")) if w == x else None, 1900, 1000)
                self.assertIn(("vc1", "Logout"), self.wire.calls)
        self.setUp()                                         # the view gone, made again: NoPermission
        col = self.coll(self.vc())
        col.cycle()
        state = {"n": 0}

        def hook(h, w):
            if w == "RetrievePropertiesEx" and not state["n"]:
                state["n"] = 1
                return 500, [], fault("ManagedObjectNotFound", "gone")
            if w == "CreateContainerView" and state["n"]:
                return 500, [], fault("NoPermission", "denied")
        self.wire.hook = hook
        self.c.step(60)
        col.cycle()
        self.wire.hook = None
        self.assertHeld(list(self.journal().values())[0], 1960, 1060)
        self.c.step(60)
        col.cycle()
        self.assertEqual(self.wire.logins(), 1)

    def test_15_fresh_session_dropped_at_once(self):
        def hook(h, w):
            if w == "RetrievePropertiesEx":
                return 500, [], fault("NotAuthenticated", "The session is not authenticated.")
        self.run_case(self.vc(), hook, 1900, 1000)

    def test_16_no_connection_sends_nothing(self):
        for err in (ConnectionRefusedError("refused"), socket.gaierror("no name"), ssl.SSLCertVerificationError("bad cert")):
            with self.subTest(err=type(err).__name__):
                self.setUp()
                v1, v2 = self.vc("vc1"), self.vc("vc2")
                self.wire.connect_error["vc1"] = err
                col = self.coll(v1, v2)
                col.cycle()
                self.assertEqual((self.wire.logins("vc1"), self.wire.logins("vc2")), (0, 1))
                self.assertEqual(self.journal(), {})
        self.setUp()                                         # RetrieveServiceContent times out: before any login
        v1, v2 = self.vc("vc1"), self.vc("vc2")
        self.wire.hook = lambda h, w: (_ for _ in ()).throw(socket.timeout("t")) if h == "vc1" and w == "RetrieveServiceContent" else None
        col = self.coll(v1, v2)
        col.cycle()
        self.assertEqual((self.wire.logins("vc1"), self.wire.logins("vc2")), (0, 1))
        self.setUp()                                         # contrast: an error after the login left
        v1, v2 = self.vc("vc1"), self.vc("vc2")
        self.wire.hook = lambda h, w: (_ for _ in ()).throw(ConnectionResetError("r")) if h == "vc1" and w == "Login" else None
        col = self.coll(v1, v2)
        col.cycle()
        self.assertEqual((self.wire.logins("vc1"), self.wire.logins("vc2")), (1, 0))
        self.assertHeld(list(self.journal().values())[0], 1900, 1000)

    def test_17_ontap_first_get_fails(self):
        cases = [(401, b'{"error": {}}'), (403, b""), (401, b"cut"), (500, b"{}"), (503, b"{}"), (429, b"{}"), (302, b""), (200, b"{not json"),
                 socket.timeout("t"), ConnectionResetError("r")]
        for case in cases:
            with self.subTest(case=str(case)[:30]):
                self.setUp()

                def hook(h, w, x=case):
                    if w == "/api/cluster":
                        self.c.step(20)
                        if isinstance(x, BaseException):
                            raise x
                        return x[0], [], x[1]
                self.run_case(self.ontap(), hook, 1920, 1020)

    def test_18_ontap_mid_poll_failure(self):
        # a 401 mid-poll: a refusal, held one window
        self.setUp()

        def h401(h, w):
            if w == "/api/cluster/nodes":
                self.c.step(15)
                return 401, [], b"{}"
        self.run_case(self.ontap(), h401, 1915, 1015, logins=2)
        self.assertNotIn(("on1", "/api/storage/volumes"), self.wire.calls)
        # 5xx / timeout after a 200 in the same poll: the coordinator's rule - the credentials were proved, so a plain source
        # error (no hold, the next cycle tries again) - but no further GET in that poll (Neon expected a hold here)
        for case in ((500, b"{}"), (503, b"{}"), socket.timeout("t"), ConnectionResetError("r")):
            with self.subTest(case=str(case)[:30]):
                self.setUp()

                def hook(h, w, x=case):
                    if w == "/api/storage/aggregates":
                        if isinstance(x, BaseException):
                            raise x
                        return x[0], [], x[1]
                col = self.coll(self.ontap())
                self.wire.hook = hook
                col.cycle()
                self.wire.hook = None
                self.assertEqual(self.wire.logins(), 3)                     # cluster, nodes, aggregates - then stopped
                self.assertNotIn(("on1", "/api/storage/volumes"), self.wire.calls)
                self.assertEqual(self.journal(), {})
                self.assertEqual(list(col.view()["sources"].values())[0]["state"], "error")

    def test_19_ontap_old_version_no_blind_retry(self):
        self.wire.add_on("on1", version={"full": "NetApp Release 9.7P1", "generation": 9, "major": 7})
        col = self.coll(self.ontap())
        col.cycle()
        nodes = [p for h, p in self.wire.calls if p == "/api/cluster/nodes"]
        self.assertEqual(len(nodes), 1)
        self.assertEqual(self.journal(), {})
        self.setUp()
        self.run_case(self.ontap(), lambda h, w: (400, [], b"<html>proxy</html>") if w == "/api/cluster" else None, 1900, 1000)

    def test_20_failure_outcome_not_written_then_restart(self):
        self.wire.add_vc("vc1", password="right")
        col = self.coll(self.vc(pw="wrong"))
        real = col._write

        def w(mutate, n=[0]):
            n[0] += 1
            return real(mutate) if n[0] == 1 else False          # the attempt written; the deadline cannot be
        col._write = w

        def slow(h, x):
            if x == "Login":
                self.c.step(30)
        self.wire.hook = slow
        col.cycle()
        self.wire.hook = None
        self.until_mono(col, 1030)
        self.assertEqual(self.wire.logins(), 1)
        self.assertEqual(list(self.journal().values()), [{"attempt": 1000.0}])
        self.c.w, self.c.m = 1031, 131                           # the restart (a writable folder)
        col2 = self.coll(W.VcenterSource("vc1", "A", "wrong", verify=False))
        self.until_mono(col2, 1031)
        self.assertEqual(self.wire.logins(), 1)
        self.c.w += 1031 - self.c.m
        self.c.m = 1031
        col2.cycle()
        self.assertEqual(self.wire.logins(), 2)


class SharedAccounts(Base):
    def test_21_one_account_many_spellings(self):
        self.wire.add_vc("vc1", password="right")
        srcs = [self.vc("vc1", "CORP\\svc-wall", "wrong"), self.vc("vc2", "svc-wall@corp.example.mil"), self.vc("vc3", " SVC-WALL "),
                self.vc("vc4", "CORP\\ svc-wall "), self.ontap("on1", "svc-wall @corp.example.mil")]
        col = self.coll(*srcs)
        col.cycle()
        for _ in range(14):
            self.c.step(60)
            col.cycle()
        self.assertEqual(self.wire.logins(), 1)
        self.assertEqual(set(x["state"] for x in col.view()["sources"].values()), {"auth"})
        self.assertEqual(list(self.journal()), [self.h("svc-wall")])

    def test_22_success_then_refusal_in_one_cycle(self):
        on = self.ontap(pw="wrong")

        def hook(h, w):
            if h == "on1":
                self.c.step(10)
        self.wire.hook = hook
        col = self.coll(self.vc(), on)
        col.cycle()
        self.wire.hook = None
        self.c.step(60)
        col.cycle()
        self.assertEqual(self.wire.logins(), 2)
        self.assertHeld(list(self.journal().values())[0], 1910, 1010)

    def test_23_a_kept_vcenter_session_never_ends_a_back_off(self):
        vc, on = self.vc(), self.ontap()
        col = self.coll(vc, on)
        col.cycle()
        self.c.step(60)
        self.wire.on["on1"]["password"] = "changed"
        col.cycle()
        n = self.wire.logins()
        self.wire.vc["vc1"]["drop"] = True
        calls = len(self.wire.calls)
        for _ in range(13):
            self.c.step(60)
            col.cycle()
        self.assertEqual(self.wire.logins(), n)
        self.assertEqual(len(self.wire.calls), calls)            # not even a read on the kept session while the account is held
        self.assertTrue(list(self.journal().values())[0]["until"])

    def test_24_vcenter_session_timeout(self):
        col = self.coll(self.vc())
        col.cycle()
        self.c.step(60)
        col.cycle()
        self.wire.vc["vc1"]["drop"] = True
        self.c.step(60)
        col.cycle()
        self.assertEqual(self.wire.logins(), 2)
        self.assertEqual(self.journal(), {})
        self.wire.vc["vc1"].update(drop=True, password="changed")
        self.c.step(60)
        col.cycle()
        self.assertEqual(self.wire.logins(), 3)
        self.assertTrue(list(self.journal().values())[0]["until"])
        self.until_mono(col, self.c.m + WINDOW)
        self.assertEqual(self.wire.logins(), 3)
        self.setUp()
        col = self.coll(self.vc())
        col.cycle()
        os.chmod(self.d, 0o500)
        self.wire.vc["vc1"]["drop"] = True
        self.c.step(60)
        col.cycle()
        self.assertEqual(self.wire.logins(), 1)

    def test_25_other_accounts_are_not_held(self):
        self.wire.add_vc("vc1", password="right")
        col = self.coll(self.vc("vc1", "A", "wrong"), self.vc("vc2", "B"))
        col.cycle()
        self.c.step(60)
        col.cycle()
        self.assertEqual((self.wire.logins("vc1"), self.wire.logins("vc2")), (1, 1))
        self.assertEqual(list(self.journal()), [self.h("A")])

    def test_26_one_ontap_source_per_address(self):
        srcs = W.sources_from_config({"ontap": {"user": "A", "password": "pw", "clusters": ["192.0.2.10", "https://192.0.2.10:443"]}})
        self.assertEqual(len(srcs), 1)
        self.wire.add_on("192.0.2.10")
        self.wire.hook = lambda h, w: (500, [], b"{}")
        col = self.coll(*srcs)
        col.cycle()
        self.assertEqual(self.wire.logins(), 1)
        self.assertHeld(list(self.journal().values())[0], 1900, 1000)

    def test_27_a_second_cycle_waits_for_the_first(self):
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


class Restarts(Base):
    def test_28_restart_keeps_the_deadline(self):
        self.seed({self.h(): self.bo(1900, 1000)})
        self.c.w, self.c.m = 1300, 400
        col = self.coll(self.vc())
        self.until_mono(col, 1000)
        self.assertEqual(self.wire.logins(), 0)
        self.c.w += 1000 - self.c.m
        self.c.m = 1000
        col.cycle()
        self.assertEqual(self.wire.logins(), 1)
        self.assertEqual(self.journal(), {})

    def test_29_restart_at_the_deadline(self):
        self.seed({self.h(): self.bo(1900, 1000)})
        self.c.w, self.c.m = 1900, 1000
        col = self.coll(self.vc())
        col.cycle()
        self.assertEqual(self.wire.logins(), 1)
        self.assertEqual(self.journal(), {})

    def test_30_attempt_without_outcome_holds_a_window_from_now(self):
        self.seed({self.h(): {"attempt": 900.0}})
        self.c.w, self.c.m = 5000, 500
        col = self.coll(self.vc())
        self.until_mono(col, 1400)
        self.assertEqual(self.wire.logins(), 0)
        self.c.w += 1400 - self.c.m
        self.c.m = 1400
        col.cycle()
        self.assertEqual(self.wire.logins(), 1)
        self.assertEqual(self.journal(), {})

    def test_31_attempt_for_a_clean_for_b(self):
        self.seed({self.h("A"): {"attempt": 990.0}})
        col = self.coll(self.vc("vc1", "A"), self.vc("vc2", "B"))
        col.cycle()
        self.assertEqual((self.wire.logins("vc1"), self.wire.logins("vc2")), (0, 1))
        self.assertEqual(list(self.journal()), [self.h("A")])

    def test_32_two_restarts(self):
        self.seed({self.h(): {"attempt": 990.0}})
        self.coll(self.vc())
        self.c.step(300)
        col = self.coll(self.vc())
        self.until_mono(col, 1000)
        self.assertEqual(self.wire.logins(), 0)


class ClockJumps(Base):
    def refuse(self, *extra):
        self.wire.add_vc("vc1", password="right")
        col = self.coll(self.vc("vc1", "A", "wrong"), *extra)
        col.cycle()
        self.wire.vc["vc1"]["password"] = "wrong"
        return col

    def test_33_wall_forward_while_running(self):
        col = self.refuse()
        self.c.m, self.c.w = 110, 5000
        for m in range(110, 1000, 60):
            self.c.w += m - self.c.m
            self.c.m = m
            col.cycle()
        self.c.w += 999 - self.c.m
        self.c.m = 999
        col.cycle()
        self.assertEqual(self.wire.logins(), 1)
        self.c.m, self.c.w = 1000, self.c.w + 1
        col.cycle()
        self.assertEqual(self.wire.logins(), 2)

    def test_34_wall_back_while_running(self):
        self.c.w = 2000
        col = self.refuse(self.vc("vc2", "B"))
        self.c.m, self.c.w = 110, 1000
        self.until_mono(col, 1000)
        self.assertEqual((self.wire.logins("vc1"), self.wire.logins("vc2")), (1, 1))
        self.c.w += 1000 - self.c.m
        self.c.m = 1000
        col.cycle()
        self.assertEqual(self.wire.logins("vc1"), 2)

    def test_35_wall_forward_across_restart(self):
        self.seed({self.h(): self.bo(1900, 1000)})
        self.c.w, self.c.m = 5000, 110
        col = self.coll(self.vc())
        self.until_mono(col, 1000)
        self.assertEqual(self.wire.logins(), 0)
        self.c.w += 1000 - self.c.m
        self.c.m = 1000
        col.cycle()
        self.assertEqual(self.wire.logins(), 1)

    def test_36_wall_back_then_forward_across_restart(self):
        self.seed({self.h(): self.bo(2900, 1000)})
        self.c.w, self.c.m = 1500, 110
        col = self.coll(self.vc())
        self.c.w, self.c.m = 2500, 120
        self.until_mono(col, 1000)
        self.assertEqual(self.wire.logins(), 0)

    def test_37_wall_back_across_a_reboot(self):
        self.seed({self.h(): self.bo(2900, 1000, "b1")})
        self.c.w, self.c.m = 1500, 10
        col = self.coll(self.vc(), boot="b2")
        self.until_mono(col, 910)
        self.assertEqual(self.wire.logins(), 0)
        self.c.w += 910 - self.c.m
        self.c.m = 910
        col.cycle()
        self.assertEqual(self.wire.logins(), 1)


class Journal(Base):
    def payloads(self):
        h = self.h()
        j = lambda a: json.dumps({"v": 2, "accounts": a}).encode()     # noqa: E731
        return [b"", b"\xff\xfe\x00\x80", b"{not json", b"[]", b"null", b"123", b'"s"',
                b'{"v": 3, "accounts": {}}', b'{"v": 2.0, "accounts": {}}', b'{"v": true, "accounts": {}}', b'{"accounts": {}}',
                b'{"v": 2, "accounts": []}', b'{"v": 2, "accounts": {}, "extra": 1}',
                ('{"v": 2, "accounts": {"%s": {"until": 1900, "until": 1}}}' % h).encode(),
                j({h: {}}), j({h: {"until": None}}), j({h: {"attempt": None}}), j({h: {"until": True}}), j({h: {"until": "1900"}}),
                j({h: {"until": []}}), ('{"v": 2, "accounts": {"%s": {"until": NaN}}}' % h).encode(),
                ('{"v": 2, "accounts": {"%s": {"until": Infinity}}}' % h).encode(), ('{"v": 2, "accounts": {"%s": {"until": -Infinity}}}' % h).encode(),
                j({h: {"until": 0}}), j({h: {"until": -50}}), j({h: {"attempt": False}}), j({h: {"attempt": ""}}),
                j({h: {"until": 1500, "attempt": 1}}), j({h: {"until": 1500, "extra": 1}}), j({"unknown": {}})]

    def test_38_every_invalid_payload_holds_one_window(self):
        for raw in self.payloads() + ["unreadable", "directory"]:
            with self.subTest(raw=raw if isinstance(raw, str) else raw[:60]):
                self.setUp()
                if raw == "unreadable":
                    self.seed({}, b'{"v": 2, "accounts": {}}')
                    os.chmod(self.path, 0)
                elif raw == "directory":
                    os.mkdir(self.path)
                else:
                    self.seed(None, raw)
                before = None if raw in ("unreadable", "directory") else open(self.path, "rb").read()
                col = self.coll(self.vc())
                self.until_mono(col, 1000)
                self.assertEqual(self.wire.logins(), 0)
                if before is not None:
                    self.assertEqual(open(self.path, "rb").read(), before)
        self.setUp()                                          # a far deadline: that account one window, the others free
        self.seed({self.h("A"): self.bo(1000 + 10 ** 7, 1000, "b2")})
        col = self.coll(self.vc("vc1", "A"), self.vc("vc2", "B"))
        self.until_mono(col, 1000)
        self.assertEqual((self.wire.logins("vc1"), self.wire.logins("vc2")), (0, 1))

    def test_39_the_quarantine_ends(self):
        self.seed(None, b"{not json")
        col = self.coll(self.vc())
        self.until_mono(col, 1000)
        self.assertEqual(self.wire.logins(), 0)
        self.c.w += 1000 - self.c.m
        self.c.m = 1000
        col.cycle()
        self.assertEqual(self.wire.logins(), 1)
        self.assertEqual(self.journal(), {})
        self.setUp()                                          # changed meanwhile to another bad file: quarantined again
        self.seed(None, b"{not json")
        col = self.coll(self.vc())
        self.seed(None, b"{bad again!")
        self.until_mono(col, 1000)
        self.c.w += 1000 - self.c.m
        self.c.m = 1000
        col.cycle()
        self.assertEqual(self.wire.logins(), 0)

    def test_40_a_file_that_breaks_while_running(self):
        for breakage in ("chmod", b"\xff\xfe", b"{bad", "empty-entry"):
            with self.subTest(breakage=str(breakage)):
                self.setUp()
                self.seed({self.h(): self.bo(1900, 1000)})
                col = self.coll(self.vc())
                self.c.w, self.c.m = 1900, 1000
                if breakage == "chmod":
                    os.chmod(self.path, 0)
                elif breakage == "empty-entry":
                    self.seed({self.h(): {}})
                else:
                    self.seed(None, breakage)
                os.chmod(self.path, 0o600) if breakage != "chmod" else None
                before = os.stat(self.path).st_mtime_ns
                raw = None if breakage == "chmod" else open(self.path, "rb").read()
                col.cycle()
                self.assertEqual(self.wire.logins(), 0)
                self.assertEqual(os.stat(self.path).st_mtime_ns, before)
                if raw is not None:
                    self.assertEqual(open(self.path, "rb").read(), raw)

    def test_41_read_failure_keeps_the_other_accounts(self):
        self.seed({self.h("A"): self.bo(1900, 1000)})
        col = self.coll(self.vc("vc1", "A"), self.vc("vc2", "B"))
        good = open(self.path, "rb").read()
        self.c.step(100)
        self.seed(None, b"\xff\xfe")
        col.cycle()
        self.assertEqual(self.wire.logins("vc2"), 0)
        self.assertEqual(open(self.path, "rb").read(), b"\xff\xfe")
        self.c.step(60)
        self.seed(None, good)
        col.cycle()
        self.assertEqual(self.wire.logins("vc2"), 1)
        self.assertIn(self.h("A"), self.journal())

    def test_42_read_failure_at_the_outcome_keeps_both(self):
        self.seed({self.h("A"): self.bo(1900, 1000)})
        on = self.ontap("on1", "B")
        col = self.coll(self.vc("vc1", "A"), on)
        self.c.step(100)
        state = {"broken": False}

        def hook(h, w):
            if h == "on1" and w == "/api/snapmirror/relationships":
                state["broken"] = True
        self.wire.hook = hook
        real = W.Collector._read
        with mock.patch.object(W.Collector, "_read", lambda me: ({}, "cannot be read") if state["broken"] else real(me)):
            col.cycle()
            n = self.wire.logins("on1")
            self.c.step(60)
            col.cycle()
            self.assertEqual(self.wire.logins("on1"), n)
        j = self.journal()
        self.assertIn(self.h("A"), j)
        self.assertEqual(j[self.h("B")], {"attempt": 1100.0})

    def test_43_another_process(self):
        c1 = self.coll(self.vc("vc1"))
        c2 = self.coll(self.vc("vc1"))
        inside, go = threading.Event(), threading.Event()

        def hook(h, w):
            if w == "Login" and not inside.is_set():
                inside.set()
                go.wait(5)
        self.wire.hook = hook
        t = threading.Thread(target=c1.cycle)
        t.start()
        inside.wait(5)
        c2.cycle()                                            # c1's attempt is on disk: c2 sends nothing
        self.assertEqual(self.wire.logins(), 1)
        self.seed({self.h(): self.bo(2500, 1600)})            # meanwhile someone writes a deadline
        go.set()
        t.join(5)
        self.assertHeld(self.journal()[self.h()], 2500, 1600)  # c1's success never erases it


if __name__ == "__main__":
    unittest.main()
