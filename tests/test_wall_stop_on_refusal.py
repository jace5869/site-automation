#!/usr/bin/env python3
"""1.130.0 STOP ON REFUSAL - the wall's login protection, on a fake clock (wall + monotonic) and a fake transport that
records every credentialed send (a vCenter Login, every ONTAP GET). Every case of Neon's round-4 list, the round-4 repro
journal cases, Codex round 5 #1-#9 and #24-#26, and Neon round 5 #1-#10, rewritten to the rules:
  a definite refusal (refused login, any 401 / 403, NoPermission, a new session refused at once) stops EVERY login to
  every source until the credentials change or the deploy's reset; an ambiguous outcome after the credential left pauses
  that source one window (a restart meanwhile waits one window); one collector per data volume (a lock, or no logins);
  any doubt about the journal stops every login.

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

    def coll(self, *srcs, reset=""):
        return W.Collector(list(srcs), seconds=60, auth_retry=WINDOW, clock=self.c.wall, mono=self.c.mono, state_file=self.path, reset=reset)

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


def refuse(net, host, what):
    """a hook: the given host's Login / first GET refused"""
    def h(hh, w):
        if hh == host and w in ("Login", "/api/cluster"):
            return (500, [], fault("InvalidLogin", "Cannot complete login due to an incorrect user name or password.")) if w == "Login" else (401, [], b"{}")
    return h


class Lifecycle(Base):
    """Neon r4 1-7; Codex r5 #4 / Neon r5 #3 (no lock = no logins)"""

    def test_n01_clean_vcenter_login(self):
        col = self.coll(self.vc())
        col.cycle()
        self.c.step(60)
        col.cycle()
        self.assertEqual(self.wire.logins(), 1)
        j = self.journal()
        self.assertEqual((j["v"], j["stopped"], j["inflight"]), (3, False, []))
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        raw = open(self.path, "rb").read()
        self.assertNotIn(b"pass", raw)
        self.assertNotIn(b'"A"', raw)

    def test_n02_write_ahead_before_the_send(self):
        for make in (self.vc, self.ontap):
            with self.subTest(make.__name__):
                self.setUp()
                seen = []
                self.wire.hook = lambda host, what: seen.append(self.journal()["inflight"]) if what in ("Login", "/api/cluster") else None
                src = make()
                col = self.coll(src)
                col.cycle()
                self.assertEqual(seen[0], [self.h(src)])
                self.assertEqual(self.journal()["inflight"], [])

    def test_n03_ontap_paged_poll(self):
        self.wire.add_on("on1")
        self.wire.on["on1"]["pages"]["/api/storage/aggregates"] = 2
        seen = []
        self.wire.hook = lambda host, what: seen.append(list(self.journal()["inflight"])) or None
        src = self.ontap()
        col = self.coll(src)
        col.cycle()
        self.assertEqual(self.wire.logins(), 7)
        self.assertTrue(all(x == [self.h(src)] for x in seen))
        self.assertEqual(self.journal()["inflight"], [])

    def test_n04_no_lock_no_logins(self):
        """a folder that cannot be written (the lock cannot be made) - and a lock file that cannot be opened: login off"""
        os.chmod(self.d, 0o500)
        col = self.coll(self.vc(), self.ontap())
        self.run_for(col, 600)
        self.assertEqual(self.wire.logins(), 0)
        self.assertIn("login off", list(col.view()["sources"].values())[0]["why"])
        self.setUp()
        lock = os.path.join(self.d, "wall-collector.lock")
        open(lock, "w").close()
        os.chmod(lock, 0)                                      # Neon r5 #3 / Codex r5 #4: the lock itself not openable
        col = self.coll(self.vc())
        self.run_for(col, 600)
        self.assertEqual(self.wire.logins(), 0)

    def test_n05_fsync_of_the_temp_file_fails(self):
        col = self.coll(self.vc())
        real = os.fsync

        def fs(fd):
            if os.path.isdir("/proc/self/fd/%d" % fd):
                return real(fd)
            raise OSError("fsync failed")
        with mock.patch.object(os, "fsync", fs):
            col.cycle()
        self.assertEqual(self.wire.logins(), 0)
        self.assertEqual([x for x in os.listdir(self.d) if x.endswith(".tmp")], [])

    def test_n06_fsync_of_the_folder_fails(self):
        col = self.coll(self.vc())
        real = os.fsync

        def fs(fd):
            if os.path.isdir("/proc/self/fd/%d" % fd):
                raise OSError("fsync of the folder failed")
            return real(fd)
        with mock.patch.object(os, "fsync", fs):
            col.cycle()
        self.assertEqual(self.wire.logins(), 0)

    def test_n07_outcome_not_written_then_restart_waits(self):
        src = self.vc()
        col = self.coll(src)
        real = col._change
        col._change = lambda m: False if col.journal["inflight"] else real(m)   # the clear after the login cannot be written
        col.cycle()
        self.assertEqual(self.journal()["inflight"], [self.h(src)])
        col.stop()
        col.lock_fd and os.close(col.lock_fd)
        col2 = self.coll(self.vc())                            # the restart: one window from now
        self.run_for(col2, WINDOW - 60)
        self.assertEqual(self.wire.logins(), 1)
        self.run_for(col2, 120)
        self.assertEqual(self.wire.logins(), 2)

    def test_two_collectors_one_volume(self):
        """Codex r5 #3, #4, #6; Neon r5 #1, #2, #4, #9: a second collector on the volume never logs in"""
        c1 = self.coll(self.vc("vc1"))
        c2 = self.coll(self.vc("vc1"), self.ontap())
        self.run_for(c2, 600)
        self.assertEqual(self.wire.logins(), 0)
        self.assertIn("login off", list(c2.view()["sources"].values())[0]["why"])
        c1.cycle()
        self.assertEqual(self.wire.logins(), 1)


class Refusals(Base):
    """a definite refusal stops EVERY login to every source - and it stays stopped (Neon r4 8, 9, 14, 15, 17, 18, 21-25)"""

    def assertAllStopped(self, col, hours=3):
        n = self.wire.logins()
        self.run_for(col, hours * 3600)
        self.assertEqual(self.wire.logins(), n)
        self.assertTrue(self.stopped())
        self.assertEqual(self.states(col), {"auth"})

    def test_n08_refused_login_stops_everything(self):
        self.wire.add_vc("vc1", password="right")
        col = self.coll(self.vc("vc1", "A", "wrong"), self.vc("vc2", "B"), self.ontap("on1", "C"))
        col.cycle()
        self.assertEqual(self.wire.logins(), 1)                 # vc2 and on1 (other accounts) not tried either
        self.assertAllStopped(col)
        why = list(col.view()["sources"].values())[0]["why"]
        self.assertIn("Logins stopped at", why)
        self.assertIn("vc1 refused A", why)
        self.assertIn("run Site portal - deploy", why)

    def test_n09_vcenter_401_403_on_login(self):
        for status, body in ((401, b"Unauthorized"), (403, b""), (401, b"<soap"), (401, fault("", "Unauthorized"))):
            with self.subTest(status=status, body=body[:20]):
                self.setUp()
                self.wire.hook = lambda h, w, s=status, b=body: (s, [], b) if w == "Login" else None
                col = self.coll(self.vc(), self.vc("vc2"))
                col.cycle()
                self.assertAllStopped(col, 1)
                self.assertEqual(self.wire.logins(), 1)

    def test_n14_no_permission_after_login(self):
        for where in ("CreateContainerView", "RetrievePropertiesEx"):
            with self.subTest(where):
                self.setUp()
                self.wire.hook = lambda h, w, x=where: (500, [], fault("NoPermission", "denied")) if w == x else None
                col = self.coll(self.vc(), self.ontap())
                col.cycle()
                self.assertAllStopped(col, 1)
                self.assertIn(("vc1", "Logout"), self.wire.calls)

    def test_n15_new_session_refused_at_once(self):
        """Neon r5 #5, #6; Codex r5 #2: NotAuthenticated right after a new login (view, first read, after a re-login)"""
        for where in ("CreateContainerView", "RetrievePropertiesEx"):
            with self.subTest(where):
                self.setUp()
                self.wire.hook = lambda h, w, x=where: (500, [], fault("NotAuthenticated", "not authenticated")) if w == x else None
                col = self.coll(self.vc())
                col.cycle()
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

    def test_n17_ontap_401_403_on_any_get(self):
        for path in ("/api/cluster", "/api/cluster/nodes", "/api/storage/luns"):
            for status in (401, 403):
                with self.subTest(path=path, status=status):
                    self.setUp()
                    self.wire.hook = lambda h, w, p=path, s=status: (s, [], b"{}") if w == p else None
                    col = self.coll(self.ontap(), self.vc())
                    col.cycle()
                    self.assertEqual(self.wire.logins("vc1"), 0)            # the vCenter after it: not tried
                    self.assertAllStopped(col, 1)

    def test_n21_any_account_spelling_any_source(self):
        """Codex r5 #9: one directory account under two names - a refusal on one stops the other too (global)"""
        self.wire.add_vc("vc1", password="right")
        col = self.coll(self.vc("vc1", "CORP\\svc01", "wrong"), self.ontap("on1", "ops-monitor@corp.example.mil"))
        col.cycle()
        self.assertEqual(self.wire.logins(), 1)
        self.assertAllStopped(col, 1)

    def test_n22_success_then_refusal_in_one_cycle(self):
        col = self.coll(self.vc(), self.ontap(pw="wrong"))
        col.cycle()
        self.assertEqual(self.wire.logins(), 2)
        self.assertAllStopped(col, 1)

    def test_n23_a_kept_session_is_not_read_while_stopped(self):
        col = self.coll(self.vc(), self.ontap())
        col.cycle()
        self.wire.on["on1"]["password"] = "changed"
        self.c.step(60)
        col.cycle()
        calls = len(self.wire.calls)
        self.run_for(col, 3600)
        self.assertEqual(len(self.wire.calls), calls)

    def test_403_on_a_cookie_read_stops_and_survives_a_restart(self):
        """Codex r5 #8: 403 on a kept session's read - no login was in flight - stopped, persisted, a restart stays stopped"""
        col = self.coll(self.vc())
        col.cycle()
        self.wire.hook = lambda h, w: (403, [], b"") if w == "RetrievePropertiesEx" else None
        self.c.step(60)
        col.cycle()
        self.assertTrue(self.stopped())
        col.stop()
        os.close(col.lock_fd)
        self.wire.hook = None
        col2 = self.coll(self.vc())
        self.run_for(col2, 7200)
        self.assertEqual(self.wire.logins(), 1)

    def test_r4_refused_then_restart_stays_stopped(self):
        """r4 repro p1_2 / Neon r4 20, 28-29, 33-37 (clock steps, reboots): a stop has no deadline - only a new credential ends it"""
        self.wire.add_vc("vc1", password="right")
        col = self.coll(self.vc(pw="wrong"))
        col.cycle()
        col.stop()
        os.close(col.lock_fd)
        for wall, mono in ((1001, 101), (5000, 5), (500, 99999)):          # restart a second later, a clock step, a reboot
            self.c.w, self.c.m = wall, mono
            c = self.coll(self.vc(pw="wrong"))
            self.run_for(c, 4 * 3600)
            self.assertEqual(self.wire.logins(), 1)
            c.stop()
            os.close(c.lock_fd)
        c = self.coll(self.vc(pw="right"))                     # Site portal - deploy with the fixed credential: logins start
        c.cycle()
        self.assertEqual(self.wire.logins(), 2)
        self.assertFalse(self.stopped())

    def test_the_deploy_reset_starts_the_logins(self):
        self.wire.add_vc("vc1", password="right")
        col = self.coll(self.vc(pw="wrong"))
        col.cycle()                                            # refused: stopped
        col.stop()
        os.close(col.lock_fd)
        c = self.coll(self.vc(pw="wrong"), reset="2026-11-25")   # the deploy's reset: logins start (refused again: stopped again)
        c.cycle()
        self.assertEqual(self.wire.logins(), 2)
        c.stop()
        os.close(c.lock_fd)
        c = self.coll(self.vc(pw="wrong"), reset="2026-11-25")   # the same reset value again: still stopped
        self.run_for(c, 3600)
        self.assertEqual(self.wire.logins(), 2)
        c.stop()
        os.close(c.lock_fd)
        self.wire.vc["vc1"]["password"] = "wrong"
        c = self.coll(self.vc(pw="wrong"), reset="2026-11-26")   # a new value: starts; the credential works now
        c.cycle()
        self.assertEqual((self.wire.logins(), self.stopped()), (3, False))


class Ambiguous(Base):
    """an unclear outcome after the credential left: that source waits one window (Neon r4 10-13, 16-20)"""

    def assertPaused(self, col, host, sent_at, logins=1):
        self.until_mono(col, sent_at + WINDOW)
        self.assertEqual(self.wire.logins(host), logins)
        self.c.w += sent_at + WINDOW - self.c.m
        self.c.m = sent_at + WINDOW
        self.wire.hook = None
        col.cycle()
        self.assertGreater(self.wire.logins(host), logins)      # after the window: tried again
        self.assertFalse(self.stopped())

    def test_n10_n12_n13_odd_answers_to_login(self):
        cases = [(500, fault("SystemError", "internal")), (500, fault("TooManyLogins", "too many")), (500, fault("RuntimeFault", "Anmeldung")),
                 (500, b"oops"), (502, b""), (503, b""), (504, b""), (429, b""), (302, b""), (200, b"<soapenv:Env"), (200, env("")),
                 (200, env('<OtherResponse xmlns="urn:vim25"><returnval><key>k</key></returnval></OtherResponse>'))]
        for status, body in cases:
            with self.subTest(status=status, body=body[:30]):
                self.setUp()
                self.wire.hook = lambda h, w, s=status, b=body: (s, [], b) if h == "vc1" and w == "Login" else None
                col = self.coll(self.vc("vc1"), self.vc("vc2"))
                col.cycle()
                self.assertEqual(self.wire.logins("vc2"), 1)            # another source: not paused
                self.assertPaused(col, "vc1", 100)
        for case in ("cookie", "key"):
            with self.subTest(case):
                self.setUp()
                self.wire.add_vc("vc1", cookie=case != "cookie", key=case != "key")
                col = self.coll(self.vc())
                col.cycle()
                self.wire.vc["vc1"].update(cookie=True, key=True)
                self.assertPaused(col, "vc1", 100)

    def test_n11_errors_after_the_login_left(self):
        for err in (socket.timeout("timed out"), ConnectionResetError("reset"), http.client.IncompleteRead(b"x"), ssl.SSLError("bad record")):
            with self.subTest(err=type(err).__name__):
                self.setUp()

                def hook(h, w, e=err):
                    if w == "Login":
                        self.c.step(30)
                        raise e
                self.wire.hook = hook
                col = self.coll(self.vc())
                col.cycle()
                self.assertPaused(col, "vc1", 130)                  # one window from the outcome

    def test_n16_no_connection_sends_nothing(self):
        for err in (ConnectionRefusedError("refused"), socket.gaierror("no name"), ssl.SSLCertVerificationError("bad cert")):
            with self.subTest(err=type(err).__name__):
                self.setUp()
                v1, v2 = self.vc("vc1"), self.vc("vc2")
                self.wire.connect_error["vc1"] = err
                col = self.coll(v1, v2)
                col.cycle()
                self.assertEqual((self.wire.logins("vc1"), self.wire.logins("vc2")), (0, 1))
                self.assertEqual(self.journal()["inflight"], [])
                del self.wire.connect_error["vc1"]
                self.c.step(60)
                col.cycle()                                    # no pause: tried again the next cycle
                self.assertEqual(self.wire.logins("vc1"), 1)

    def test_n17_n18_ontap_failures_that_are_not_refusals(self):
        cases = [(500, b"{}"), (503, b"{}"), (429, b"{}"), (302, b""), (200, b"{not json"), (200, b"null"), (200, b'{"error": {"message": "x"}}'),
                 socket.timeout("t"), ConnectionResetError("r")]
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
                    col = self.coll(self.ontap())
                    col.cycle()
                    n = self.wire.logins("on1")
                    self.assertNotIn(("on1", "/api/storage/volumes"), self.wire.calls)   # the poll stopped at the failed GET
                    self.assertPaused(col, "on1", 100, logins=n)

    def test_n19_ontap_old_version_one_nodes_get(self):
        self.wire.add_on("on1", version={"full": "NetApp Release 9.7P1", "generation": 9, "major": 7})
        col = self.coll(self.ontap())
        col.cycle()
        self.assertEqual(len([p for h, p in self.wire.calls if p == "/api/cluster/nodes"]), 1)
        self.assertEqual(self.journal()["inflight"], [])

    def test_a_restart_during_a_pause_waits_one_window_from_then(self):
        self.wire.hook = lambda h, w: (_ for _ in ()).throw(socket.timeout("t")) if w == "Login" else None
        col = self.coll(self.vc())
        col.cycle()
        self.wire.hook = None
        self.c.step(600)
        col.stop()
        os.close(col.lock_fd)
        c2 = self.coll(self.vc())
        self.run_for(c2, WINDOW - 60)
        self.assertEqual(self.wire.logins(), 1)
        c2.stop()
        os.close(c2.lock_fd)
        c3 = self.coll(self.vc())                              # a second restart inside that window: from then again (never early)
        self.run_for(c3, WINDOW - 60)
        self.assertEqual(self.wire.logins(), 1)
        self.run_for(c3, 120)
        self.assertEqual(self.wire.logins(), 2)

    def test_clock_steps_never_shorten_a_pause(self):
        """Neon r4 33, 34: the pause is on the monotonic clock"""
        self.wire.hook = lambda h, w: (_ for _ in ()).throw(socket.timeout("t")) if w == "Login" and h == "vc1" else None
        col = self.coll(self.vc("vc1"), self.vc("vc2", "B"))
        col.cycle()
        self.wire.hook = None
        for jump in (+5000, -3000):
            self.c.w += jump
            self.c.m += 60
            col.cycle()
        self.assertEqual(self.wire.logins("vc1"), 1)


class Journal(Base):
    """any doubt about the journal stops every login (r4 repro p2_1, Neon r4 38-43, Codex r5 #7, #24)"""

    def make_lock(self):
        open(os.path.join(self.d, "wall-collector.lock"), "w").close()

    def payloads(self):
        good = json.loads(json.dumps({"v": 3, "salt": "0" * 32, "stopped": False, "fp": "", "at": None, "why": "", "inflight": [], "reset": ""}))
        j = lambda **kw: json.dumps(dict(good, **kw)).encode()   # noqa: E731
        return [b"", b"\xff\xfe\x00\x80", b"{not json", b"[]", b"null", b"123", b'"s"', json.dumps(dict(good, v=2)).encode(),
                json.dumps(dict(good, v=True)).encode(), b'{"v": 3}', json.dumps(dict(good, extra=1)).encode(),
                j(stopped=None), j(stopped="no"), j(at=float("nan")).replace(b"NaN", b"NaN"), b'{"v": 3, "at": 1e400}',
                j(at=-5), j(at="1900"), j(inflight={}), j(inflight=[1]), j(salt=""), j(fp=None),
                ('{"v": 3, "salt": "%s", "stopped": false, "fp": "", "at": %s, "why": "", "inflight": [], "reset": ""}' % ("0" * 32, "9" * 401)).encode(),
                b"[" * 100000 + b"]" * 100000, b'{"v": 3, "v": 3}', b" " * (70 * 1024) + b"{}"]

    def test_every_doubtful_journal_stops_every_login(self):
        for raw in self.payloads() + ["missing", "unreadable", "directory"]:
            with self.subTest(raw=raw if isinstance(raw, str) else raw[:50]):
                self.setUp()
                self.make_lock()                               # the collector ran here before
                if raw == "unreadable":
                    self.raw(b"{}")
                    os.chmod(self.path, 0)
                elif raw == "directory":
                    os.mkdir(self.path)
                elif raw != "missing":
                    self.raw(raw)
                before = open(self.path, "rb").read() if isinstance(raw, bytes) else None
                col = self.coll(self.vc(), self.ontap())       # never an exception (the portal starts)
                self.run_for(col, 2 * 3600)
                self.assertEqual(self.wire.logins(), 0)
                self.assertIn("Site portal - deploy", list(col.view()["sources"].values())[0]["why"])
                if before is not None:
                    self.assertEqual(open(self.path, "rb").read(), before)

    def test_the_reset_ends_a_doubtful_journal(self):
        self.make_lock()
        self.raw(b"{not json")
        col = self.coll(self.vc(), reset="r1")
        col.cycle()
        self.assertEqual(self.wire.logins(), 1)
        self.assertEqual(self.journal()["reset"], "r1")

    def test_first_use_only_without_a_lock_file(self):
        col = self.coll(self.vc())                             # no journal, no lock: first use
        col.cycle()
        self.assertEqual(self.wire.logins(), 1)
        col.stop()
        os.close(col.lock_fd)
        os.unlink(self.path)                                   # Codex r5 #7: the journal gone, the lock still there
        c2 = self.coll(self.vc())
        self.run_for(c2, 3600)
        self.assertEqual(self.wire.logins(), 1)

    def test_a_failed_read_while_running_changes_nothing(self):
        """r4 repro p2_1 failed read: the collector keeps its journal in memory; a write it cannot make sends nothing"""
        col = self.coll(self.vc())
        with mock.patch.object(W.Collector, "_save", lambda me, d: False):
            col.cycle()
        self.assertEqual(self.wire.logins(), 0)


class Crashes(Base):
    """Codex r5 #24-#26: nothing about the wall stops the portal or the collector"""

    def test_24_huge_numbers_and_deep_nesting(self):
        for raw in (('{"v": 3, "salt": "%s", "stopped": false, "fp": "", "at": %s, "why": "", "inflight": [], "reset": ""}' % ("0" * 32, "9" * 401)).encode(),
                    b"[" * 200000 + b"]" * 200000):
            self.setUp()
            open(os.path.join(self.d, "wall-collector.lock"), "w").close()
            self.raw(raw)
            col = self.coll(self.vc())
            self.assertTrue(col.stopped)

    def test_25_a_repeated_page_token_ends_the_read(self):
        page = env('<RetrievePropertiesExResponse xmlns="urn:vim25"><returnval><token>T</token><objects><obj type="HostSystem">h1</obj>'
                   '</objects></returnval></RetrievePropertiesExResponse>')
        cont = env('<ContinueRetrievePropertiesExResponse xmlns="urn:vim25"><returnval><token>T</token><objects><obj type="HostSystem">h2</obj>'
                   '</objects></returnval></ContinueRetrievePropertiesExResponse>')
        self.wire.hook = lambda h, w: (200, [], page) if w == "RetrievePropertiesEx" else (200, [], cont) if w == "ContinueRetrievePropertiesEx" else None
        col = self.coll(self.vc(), self.ontap())
        col.cycle()                                            # returns: the vCenter read failed, the next source still read
        self.assertEqual(len([c for c in self.wire.calls if c[1] == "ContinueRetrievePropertiesEx"]), 1)
        self.assertEqual(col.view()["sources"]["ontap:on1"]["state"], "ok")
        st = col.view()["sources"]["vcenter:vc1"]
        self.assertIn("repeated a page token", st["why"])
        cont2 = lambda n: env('<ContinueRetrievePropertiesExResponse xmlns="urn:vim25"><returnval><token>T%d</token></returnval>'   # noqa: E731
                              '</ContinueRetrievePropertiesExResponse>' % n)
        k = {"n": 0}

        def many(h, w):
            if w == "RetrievePropertiesEx":
                return 200, [], page
            if w == "ContinueRetrievePropertiesEx":
                k["n"] += 1
                return 200, [], cont2(k["n"])
        self.wire.hook = many
        self.c.step(60)
        col.cycle()
        self.assertLessEqual(k["n"], W.PAGES_MAX)

    def test_25b_a_continuation_without_an_answer_fails_the_read(self):
        page = env('<RetrievePropertiesExResponse xmlns="urn:vim25"><returnval><token>T</token></returnval></RetrievePropertiesExResponse>')
        col = self.coll(self.vc())
        col.cycle()                                            # a good first read (the session kept)
        self.wire.hook = lambda h, w: (200, [], page) if w == "RetrievePropertiesEx" else (200, [], env('<Other xmlns="urn:vim25"/>')) if w == "ContinueRetrievePropertiesEx" else None
        self.c.step(60)
        col.cycle()
        st = col.view()["sources"]["vcenter:vc1"]
        self.assertEqual(st["state"], "error")
        self.assertIn("not whole", st["why"])

    def test_26_a_tls_bundle_that_cannot_be_read(self):
        srcs = W.sources_from_config({"vcenter": {"user": "A", "password": "p", "hosts": ["vc01.corp.example.mil"]},
                                      "ontap": {"user": "A", "password": "p", "clusters": ["192.0.2.10"]}}, cafile="/nonexistent/bundle.pem")
        self.assertEqual([type(s).__name__ for s in srcs], ["BrokenSource", "BrokenSource"])
        col = self.coll(*srcs)
        col.cycle()
        self.assertEqual(self.wire.logins(), 0)
        self.assertIn("not configured correctly", col.view()["sources"]["vcenter:vc01.corp.example.mil"]["why"])

    def test_ontap_num_records_mismatch_is_a_failed_collection(self):
        s = self.ontap()
        s._get = lambda path: {"records": [], "num_records": 1}
        with self.assertRaises(W.CollectionFailed):
            s.records("/api/cluster/nodes", "state")


class Shutdown(Base):
    """Neon r5 #10: no credentialed send after stop()"""

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

    def test_n27_a_second_cycle_waits_for_the_first(self):
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

    def test_n26_one_ontap_source_per_address(self):
        srcs = W.sources_from_config({"ontap": {"user": "A", "password": "pw", "clusters": ["192.0.2.10", "https://192.0.2.10:443"]}})
        self.assertEqual(len(srcs), 1)


if __name__ == "__main__":
    unittest.main()
