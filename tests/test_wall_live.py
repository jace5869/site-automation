#!/usr/bin/env python3
"""The ops-center wall's live reads (roles/site_portal_stack/files/wall_live.py): the vCenter SOAP reader against a stub
that speaks enough vim25 (one login kept, a re-login only when the session is gone, paging by token), the ONTAP REST reader
against a stub (fields= on every request, paging, 401), the collector (a refused login stops the source for auth_retry,
one log line per episode; no two polls of one source at once; the history window), the read-only lists (only allowed
SOAP methods and GET, refused before any network I/O; a static check of the source) - and vcsim, VMware's simulator, when
podman and its image are here (tests/wall/vcsim_demo.py). Placeholders only.
  python3 tests/test_wall_live.py"""
import base64
import http.server
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FILES = os.path.join(ROOT, "roles", "site_portal_stack", "files")
sys.path[:0] = [FILES, os.path.join(HERE, "wall")]
import wall_live as W  # noqa: E402
import vcsim_demo  # noqa: E402

SOAPNS = "http://schemas.xmlsoap.org/soap/envelope/"


def cert_dir():
    d = tempfile.mkdtemp()
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=127.0.0.1",
                    "-keyout", os.path.join(d, "k.pem"), "-out", os.path.join(d, "c.pem")],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return d


CERTS = None


def serve(handler):
    """an HTTPS stub on a free port -> (server, port)"""
    global CERTS
    if CERTS is None:
        CERTS = cert_dir()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(os.path.join(CERTS, "c.pem"), os.path.join(CERTS, "k.pem"))
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


# ---- a vim25 stub ---------------------------------------------------------------------------------------------------------
def objects():
    """2 hosts (one not responding) in cluster CL01, 2 datastores, 3 VMs - as (type, id, [(path, xsi type, inner xml, attrs)])"""
    mor = lambda t, i: '<ManagedObjectReference type="%s">%s</ManagedObjectReference>' % (t, i)      # noqa: E731
    h = lambda i, n, st, cpu, mem, vms, dss: ("HostSystem", i, [                                      # noqa: E731
        ("name", "xsd:string", n, ""), ("parent", "ManagedObjectReference", "domain-c1", ' type="ClusterComputeResource"'),
        ("runtime.connectionState", "HostSystemConnectionState", st, ""), ("runtime.powerState", "HostSystemPowerState", "poweredOn", ""),
        ("runtime.inMaintenanceMode", "xsd:boolean", "false", ""),
        ("summary.quickStats.overallCpuUsage", "xsd:int", str(cpu), ""), ("summary.quickStats.overallMemoryUsage", "xsd:int", str(mem), ""),
        ("summary.hardware.cpuMhz", "xsd:int", "2000", ""), ("summary.hardware.numCpuCores", "xsd:short", "10", ""),
        ("summary.hardware.memorySize", "xsd:long", str(100 * 1048576), ""),
        ("vm", "ArrayOfManagedObjectReference", "".join(mor("VirtualMachine", v) for v in vms), ""),
        ("datastore", "ArrayOfManagedObjectReference", "".join(mor("Datastore", d) for d in dss), "")])
    ds = lambda i, n, cap, free, acc: ("Datastore", i, [                                              # noqa: E731
        ("name", "xsd:string", n, ""), ("summary.capacity", "xsd:long", str(cap), ""), ("summary.freeSpace", "xsd:long", str(free), ""),
        ("summary.accessible", "xsd:boolean", acc, ""), ("summary.type", "xsd:string", "VMFS", ""),
        ("host", "ArrayOfDatastoreHostMount",
         '<DatastoreHostMount><key type="HostSystem">host-1</key><mountInfo><accessible>true</accessible></mountInfo></DatastoreHostMount>'
         '<DatastoreHostMount><key type="HostSystem">host-2</key></DatastoreHostMount>', ""),
        ("vm", "ArrayOfManagedObjectReference", mor("VirtualMachine", "vm-1"), "")])
    vm = lambda i, n, host, pw, cpu, mem: ("VirtualMachine", i, [                                     # noqa: E731
        ("name", "xsd:string", n, ""), ("runtime.host", "ManagedObjectReference", host, ' type="HostSystem"'),
        ("runtime.powerState", "VirtualMachinePowerState", pw, ""), ("summary.quickStats.overallCpuUsage", "xsd:int", str(cpu), ""),
        ("summary.runtime.maxCpuUsage", "xsd:int", "4000", ""), ("summary.quickStats.guestMemoryUsage", "xsd:int", str(mem), ""),
        ("summary.config.memorySizeMB", "xsd:int", "8192", ""), ("guestHeartbeatStatus", "ManagedEntityStatus", "green", "")])
    return [h("host-1", "esx01.corp.example.mil", "connected", 18000, 50, ["vm-1", "vm-2"], ["ds-1", "ds-2"]),
            h("host-2", "esx02.corp.example.mil", "notResponding", 0, 0, ["vm-3"], ["ds-1"]),
            ("ClusterComputeResource", "domain-c1", [("name", "xsd:string", "SITA01-CL01", ""),
                                                     ("host", "ArrayOfManagedObjectReference", mor("HostSystem", "host-1") + mor("HostSystem", "host-2"), "")]),
            ds("ds-1", "ds-sita-07", 1000, 70, "true"), ds("ds-2", "ds-sita-08", 1000, 900, "false"),
            vm("vm-1", "app07", "host-1", "poweredOn", 3880, 4096), vm("vm-2", "db02", "host-1", "poweredOff", 0, 0),
            vm("vm-3", "web03", "host-2", "poweredOn", 400, 1024)]


def obj_xml(o):
    t, i, props = o
    return '<objects><obj type="%s">%s</obj>%s</objects>' % (t, i, "".join(
        '<propSet><name>%s</name><val xsi:type="%s"%s>%s</val></propSet>' % (n, xt, attrs, inner) for n, xt, inner, attrs in props))


class Vim(object):
    """the stub's state: every method seen, the logins, the valid session, faults on demand"""

    def __init__(self, password="pass"):
        self.password, self.methods, self.logins, self.session, self.n = password, [], 0, "", 0
        self.drop_next = False                    # the next RetrievePropertiesEx answers NotAuthenticated (the session is gone)
        self.view_fault = ""                      # CreateContainerView answers this fault, its xsi:type as given (e.g. vim25:NoPermission)
        self.http401 = False                      # every call answers a plain HTTP 401 (a proxy in front of vCenter)
        self.login401_soap = False                # Login answers HTTP 401 with a SOAP fault and no typed detail (a proxy)


def vim_handler(vim):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            vim.methods.append("HTTP GET")
            self.send_error(405)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            call = ET.fromstring(body).find("{%s}Body" % SOAPNS)[0]
            m = call.tag.split("}")[-1]
            vim.methods.append(m)
            if vim.http401:
                self.send_response(401)
                self.send_header("Content-Length", "12")
                self.end_headers()
                self.wfile.write(b"Unauthorized")
                return
            cookie = (self.headers.get("Cookie") or "").strip()
            extra = {}
            if m == "RetrieveServiceContent":
                out = ("<returnval><rootFolder type=\"Folder\">group-d1</rootFolder><propertyCollector type=\"PropertyCollector\">propertyCollector"
                       "</propertyCollector><viewManager type=\"ViewManager\">ViewManager</viewManager><sessionManager type=\"SessionManager\">"
                       "SessionManager</sessionManager></returnval>")
            elif m == "Login" and vim.login401_soap:
                return self.send(401, '<soapenv:Fault><faultcode>ServerFaultCode</faultcode><faultstring>Unauthorized</faultstring></soapenv:Fault>')
            elif m == "Login":
                if call.findtext("{urn:vim25}password") != vim.password:
                    return self.fault("InvalidLogin", "Cannot complete login due to an incorrect user name or password.")
                vim.logins += 1
                vim.n += 1
                vim.session = 'vmware_soap_session="s%d"' % vim.n
                extra["Set-Cookie"] = vim.session + "; Path=/; HttpOnly; Secure;"
                out = "<returnval><key>s%d</key><userName>wall-reader</userName></returnval>" % vim.n
            elif cookie != vim.session or not vim.session:
                return self.fault("NotAuthenticated", "The session is not authenticated.")
            elif m == "CreateContainerView" and vim.view_fault:
                return self.send(500, '<soapenv:Fault><faultcode>ServerFaultCode</faultcode><faultstring>Permission to perform this operation was '
                                      'denied.</faultstring><detail><NoPermissionFault xmlns="urn:vim25" xmlns:vim25="urn:vim25" xsi:type="%s">'
                                      '</NoPermissionFault></detail></soapenv:Fault>' % vim.view_fault)
            elif m == "CreateContainerView":
                out = '<returnval type="ContainerView">session[%s]view-1</returnval>' % vim.n
            elif m in ("RetrievePropertiesEx", "ContinueRetrievePropertiesEx"):
                if vim.drop_next:
                    vim.drop_next, vim.session = False, ""
                    return self.fault("NotAuthenticated", "The session is not authenticated.")
                objs = objects()
                first = m == "RetrievePropertiesEx"
                part = objs[:4] if first else objs[4:]
                out = "<returnval>%s%s</returnval>" % ("<token>tok1</token>" if first else "", "".join(obj_xml(o) for o in part))
            elif m == "Logout":
                vim.session, out = "", ""
            else:
                return self.fault("MethodNotFound", m)
            self.send(200, '<%sResponse xmlns="urn:vim25">%s</%sResponse>' % (m, out, m), extra)

        def fault(self, kind, text):
            self.send(500, '<soapenv:Fault><faultcode>ServerFaultCode</faultcode><faultstring>%s</faultstring><detail><%sFault xmlns="urn:vim25" '
                           'xsi:type="%s"></%sFault></detail></soapenv:Fault>' % (text, kind, kind, kind))

        def send(self, code, inner, extra=None):
            b = ('<?xml version="1.0" encoding="UTF-8"?><soapenv:Envelope xmlns:soapenv="%s" xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
                 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><soapenv:Body>%s</soapenv:Body></soapenv:Envelope>' % (SOAPNS, inner)).encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(b)
    return H


def vim_stub(test, password="pass"):
    vim = Vim(password)
    srv, port = serve(vim_handler(vim))
    test.addCleanup(srv.server_close)
    test.addCleanup(srv.shutdown)
    return vim, port


class Vcenter(unittest.TestCase):
    def src(self, port, pw="pass"):
        return W.VcenterSource("127.0.0.1", "wall-reader", pw, verify=False, port=port)

    def test_shape(self):
        _vim, port = vim_stub(self)
        snap = self.src(port).poll()
        h = dict((x["name"], x) for x in snap["hosts"])
        self.assertEqual(h["esx01.corp.example.mil"]["cluster"], "SITA01-CL01")
        self.assertEqual((h["esx01.corp.example.mil"]["cpu_pct"], h["esx01.corp.example.mil"]["mem_pct"]), (90.0, 50.0))
        self.assertEqual(h["esx01.corp.example.mil"]["vms"], ["app07", "db02"])
        self.assertEqual(h["esx01.corp.example.mil"]["datastores"], ["ds-sita-07", "ds-sita-08"])
        self.assertEqual(h["esx02.corp.example.mil"]["state"], "notResponding")
        self.assertIsNone(h["esx02.corp.example.mil"]["cpu_pct"])                     # a host not answering: no load shown
        self.assertEqual(snap["clusters"], [{"name": "SITA01-CL01", "hosts": ["esx01.corp.example.mil", "esx02.corp.example.mil"]}])
        d = dict((x["name"], x) for x in snap["datastores"])
        self.assertEqual((d["ds-sita-07"]["capacity"], d["ds-sita-07"]["free"], d["ds-sita-07"]["accessible"], d["ds-sita-07"]["vms"]),
                         (1000, 70, True, 1))
        self.assertFalse(d["ds-sita-08"]["accessible"])
        self.assertEqual(d["ds-sita-07"]["hosts"], ["esx01.corp.example.mil", "esx02.corp.example.mil"])
        v = dict((x["name"], x) for x in snap["vms"])
        a = v["app07"]
        self.assertEqual((a["cpu_pct"], a["mem_pct"], a["host"], a["heartbeat"]), (97.0, 50.0, "esx01.corp.example.mil", "green"))
        self.assertIsNone(v["db02"]["cpu_pct"])                                       # powered off: no load

    def test_one_login_kept_and_one_relogin_when_the_session_is_gone(self):
        vim, port = vim_stub(self)
        s = self.src(port)
        for _ in range(4):
            s.poll()
        self.assertEqual((vim.logins, s.logins), (1, 1))
        self.assertEqual(vim.methods.count("RetrievePropertiesEx"), 4)
        self.assertEqual(vim.methods.count("ContinueRetrievePropertiesEx"), 4)        # the second page, by its token
        self.assertEqual(vim.methods.count("CreateContainerView"), 1)
        vim.drop_next = True
        s.poll()
        self.assertEqual(vim.logins, 2)
        s.poll()
        self.assertEqual(vim.logins, 2)
        s.close()
        self.assertEqual(vim.methods[-1], "Logout")
        self.assertTrue(set(vim.methods) <= set(W.VIM_ALLOWED), vim.methods)

    def test_a_bad_password_is_an_auth_error(self):
        vim, port = vim_stub(self, password="right")
        with self.assertRaises(W.AuthError):
            self.src(port, "wrong").poll()
        self.assertEqual(vim.methods.count("Login"), 1)

    def test_a_relogin_refused_is_an_auth_error(self):
        vim, port = vim_stub(self)
        s = self.src(port)
        s.poll()
        vim.password, vim.drop_next = "changed", True
        with self.assertRaises(W.AuthError):
            s.poll()
        self.assertEqual(vim.methods.count("Login"), 2)                               # one refused re-login, no other

    def test_other_methods_are_refused_before_any_io(self):
        s = W.VcenterSource("192.0.2.1", "u", "p", port=9)
        for m in ("PowerOffVM_Task", "Destroy_Task", "ReconfigVM_Task", "TerminateSession", "retrievepropertiesex"):
            with self.assertRaises(W.NotAllowed):
                s._send(m, "")


# ---- an ONTAP stub -------------------------------------------------------------------------------------------------------
class Ontap(object):
    def __init__(self, password="pw"):
        self.password, self.verbs, self.paths = password, [], []


def ontap_handler(o):
    recs = {
        "/api/storage/aggregates": [{"name": "sita01_aggr%d" % i, "node": {"name": "sita01-n1"},
                                     "space": {"block_storage": {"size": 100, "used": 50 + i}}} for i in range(3)],
        "/api/storage/volumes": [{"name": "vol_ds07", "svm": {"name": "svm-sita01"}, "state": "online", "space": {"size": 200, "used": 150,
                                  "snapshot": {"reserve_size": 10, "used": 3}}, "aggregates": [{"name": "sita01_aggr1"}], "autosize": {"mode": "off"}},
                                 {"name": "vol_off", "svm": {"name": "svm-sita01"}, "state": "offline", "space": {"size": 1, "used": 0},
                                  "autosize": {"mode": "grow"}}],
        "/api/storage/luns": [{"name": "/vol/vol_ds07/lun_ds07", "svm": {"name": "svm-sita01"}, "location": {"volume": {"name": "vol_ds07"}},
                               "space": {"size": 190, "used": 120}, "status": {"state": "online", "container_state": "online"}}],
        "/api/snapmirror/relationships": [{"source": {"path": "svm-sita01:vol_app07"}, "destination": {"path": "svm-sitb02:vol_app07_dr"},
                                           "state": "broken_off", "healthy": False, "lag_time": "PT26H3M",
                                           "unhealthy_reason": [{"message": "Transfer failed."}]}],
        "/api/cluster/nodes": [{"name": "sita01-n1", "state": "up", "ha": {"takeover_check": {"takeover_possible": True}}},
                               {"name": "sita01-n2", "state": "down"}],
    }

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.go("GET")

        def do_PATCH(self):
            self.go("PATCH")

        def do_POST(self):
            self.go("POST")

        def go(self, verb):
            o.verbs.append(verb)
            o.paths.append(self.path)
            auth = base64.b64decode((self.headers.get("Authorization") or "Basic ").split(" ", 1)[1] or b"").decode()
            if auth != "wall-reader:" + o.password:
                return self.out(401, {"error": {"message": "not authorized"}})
            u = urllib.parse.urlsplit(self.path)
            q = urllib.parse.parse_qs(u.query)
            if u.path == "/api/cluster":
                return self.out(200, {"name": "cluster-sita01", "version": {"full": "NetApp Release 9.14.1"}})
            if u.path not in recs:
                return self.out(404, {"error": {"message": "no " + u.path}})
            r = recs[u.path]
            start = int((q.get("start") or ["0"])[0])
            end = start + 2                                   # pages of 2 (the stub ignores max_records)
            d = {"records": r[start:end], "num_records": len(r[start:end])}
            if end < len(r):
                d["_links"] = {"next": {"href": "%s?%s&start=%d" % (u.path, urllib.parse.urlencode({"fields": q["fields"][0]}), end)}}
            self.out(200, d)

        def out(self, code, d):
            b = json.dumps(d).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
    return H


class OntapRest(unittest.TestCase):
    def test_poll_pages_and_fields(self):
        o = Ontap()
        srv, port = serve(ontap_handler(o))
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        snap = W.OntapSource("127.0.0.1:%d" % port, "wall-reader", "pw", verify=False).poll()
        self.assertEqual((snap["cluster"], snap["version"]), ("cluster-sita01", "NetApp Release 9.14.1"))
        self.assertEqual([a["name"] for a in snap["aggrs"]], ["sita01_aggr0", "sita01_aggr1", "sita01_aggr2"])   # two pages
        self.assertEqual(snap["nodes"], [{"name": "sita01-n1", "state": "up", "takeover": True}, {"name": "sita01-n2", "state": "down", "takeover": None}])
        v = snap["volumes"][0]
        self.assertEqual((v["aggr"], v["autosize"], v["snap_reserve"], v["used"]), ("sita01_aggr1", False, 10, 150))
        self.assertEqual(snap["volumes"][1]["state"], "offline")
        self.assertEqual(snap["luns"][0]["name"], "lun_ds07")
        self.assertEqual(snap["luns"][0]["volume"], "vol_ds07")
        sm = snap["snapmirror"][0]
        self.assertEqual((sm["healthy"], sm["lag_s"], sm["why"], sm["state"]), (False, 26 * 3600 + 180, "Transfer failed.", "broken_off"))
        self.assertTrue(all("fields=" in p for p in o.paths), o.paths)
        self.assertEqual(set(o.verbs), {"GET"})
        self.assertTrue(any("is_constituent=false" in p for p in o.paths if p.startswith("/api/storage/volumes")))

    def test_401_is_an_auth_error_after_one_request(self):
        o = Ontap()
        srv, port = serve(ontap_handler(o))
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        with self.assertRaises(W.AuthError):
            W.OntapSource("127.0.0.1:%d" % port, "wall-reader", "bad", verify=False).poll()
        self.assertEqual(len(o.paths), 1)

    def test_only_get(self):
        s = W.OntapSource("192.0.2.1", "u", "p")
        for verb in ("PATCH", "POST", "DELETE", "PUT", "get"):
            with self.assertRaises(W.NotAllowed):
                s._http(verb, "/api/storage/volumes")

    def test_iso_seconds(self):
        self.assertEqual(W.iso_seconds("P1DT2H"), 93600)
        self.assertEqual(W.iso_seconds("PT30.5S"), 30)
        self.assertIsNone(W.iso_seconds(""))
        self.assertIsNone(W.iso_seconds("soon"))


class ReadOnlySource(unittest.TestCase):
    """a static check of wall_live.py: one network sender each, and no method or verb outside the lists"""

    def test_source(self):
        with open(os.path.join(FILES, "wall_live.py")) as fh:
            src = fh.read()
        for bad in ("urlopen", "urllib.request", "Request(", "socket.", "requests"):
            self.assertNotIn(bad, src)
        self.assertEqual(src.count("HTTPSConnection("), 2)
        self.assertEqual(src.count(".request("), 2)
        funcs = re.split(r"\n    def |\ndef ", src)
        homes = [f.split("(")[0] for f in funcs if "HTTPSConnection(" in f]
        self.assertEqual(homes, ["_send", "_http"])
        self.assertEqual(re.findall(r'_send\("(\w+)"', src) and set(re.findall(r'_send\("(\w+)"', src)) - set(W.VIM_ALLOWED), set())
        self.assertIn('conn.request("POST", "/sdk"', src)                       # SOAP's own POST, only in _send
        self.assertEqual(W.HTTP_ALLOWED, ("GET",))
        for m in ("_Task", "Destroy", "Power", "Reconfig", "Terminate"):
            self.assertNotIn(m, "".join(W.VIM_ALLOWED).replace("DestroyView", ""))


# ---- the collector -------------------------------------------------------------------------------------------------------
class Fake(object):
    def __init__(self, key="vcenter:vc01.corp.example.mil", kind="vcenter", result=None, delay=0.0):
        self.key, self.kind, self.name = key, kind, key.split(":", 1)[1]
        self.result, self.delay, self.calls = result, delay, 0

    def poll(self):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result or {"hosts": [], "vms": [], "datastores": [], "clusters": []}

    def close(self):
        pass


class Clock(object):
    def __init__(self):
        self.t = 1000000.0

    def __call__(self):
        return self.t


class CollectorTest(unittest.TestCase):
    def make(self, *srcs, **kw):
        logs = []
        clock = Clock()
        c = W.Collector(list(srcs), seconds=60, auth_retry=kw.get("auth_retry", 900), log=logs.append, keep_seconds=kw.get("keep", 3900), clock=clock)
        return c, clock, logs

    def step(self, c, clock, secs=60):
        c.cycle()
        c.wait(10)
        clock.t += secs

    def test_auth_backoff(self):
        f = Fake(result=W.AuthError(W.AUTH_WHY))
        c, clock, logs = self.make(f)
        for _ in range(14):                                   # 14 minutes: tried once
            self.step(c, clock)
        self.assertEqual(f.calls, 1)
        s = c.view()["sources"][f.key]
        self.assertEqual((s["state"], s["why"]), ("auth", "login failed - check the credential"))
        self.assertEqual(len(logs), 1)
        self.assertIn("login refused", json.dumps(logs[0]))
        for _ in range(2):                                    # 15 min passed: once more, still refused: no second log line
            self.step(c, clock)
        self.assertEqual(f.calls, 2)
        self.assertEqual(len(logs), 1)
        f.result = None
        for _ in range(14):                                   # within the next 15 min: not tried
            self.step(c, clock)
        self.assertEqual(f.calls, 2)
        self.step(c, clock)                                   # 15 min after the last try: tried, read
        self.assertEqual(f.calls, 3)
        self.assertEqual(c.view()["sources"][f.key]["state"], "ok")
        self.assertEqual(logs[-1]["state"], "read again")
        self.assertEqual(len(logs), 2)

    def test_last_good_snapshot_kept_while_failing(self):
        f = Fake(result={"hosts": [{"name": "esx01", "cpu_pct": 10.0, "mem_pct": 20.0}], "vms": []})
        c, clock, _logs = self.make(f)
        self.step(c, clock)
        f.result = RuntimeError("timed out")
        self.step(c, clock)
        s = c.view()["sources"][f.key]
        self.assertEqual((s["state"], s["why"]), ("error", "timed out"))
        self.assertEqual(s["snap"]["hosts"][0]["name"], "esx01")
        self.assertIsNotNone(s["fail_since"])

    def test_error_logged_once_and_recovery_once(self):
        f = Fake(result=ConnectionRefusedError("connection refused"))
        c, clock, logs = self.make(f)
        for _ in range(5):
            self.step(c, clock)
        self.assertEqual(f.calls, 5)                          # not a login: tried at every cycle
        self.assertEqual(len(logs), 1)
        f.result = None
        self.step(c, clock)
        self.step(c, clock)
        self.assertEqual([x["state"] for x in logs], ["not read", "read again"])

    def test_no_overlap(self):
        slow = Fake(delay=0.6)
        quick = Fake(key="ontap:192.0.2.10", kind="ontap")
        c, clock, _logs = self.make(slow, quick)
        c.cycle()
        time.sleep(0.1)
        c.cycle()                                             # the slow one still reads: skipped
        c.wait(10)
        self.assertEqual((slow.calls, quick.calls), (1, 2))
        self.assertEqual(c.view()["sources"][slow.key]["skipped"], 1)
        c.cycle()
        c.wait(10)
        self.assertEqual(slow.calls, 2)

    def test_history_window(self):
        snap = {"hosts": [{"name": "esx01", "cpu_pct": 95.0, "mem_pct": None}],
                "vms": [{"name": "app07", "power": "poweredOn", "cpu_pct": 97.0, "mem_pct": 40.0},
                        {"name": "db02", "power": "poweredOff", "cpu_pct": None, "mem_pct": None}]}
        f = Fake(result=snap)
        c, clock, _logs = self.make(f, keep=600)
        for _ in range(15):
            self.step(c, clock)
        h = c.view()["history"]
        k = (f.key, "host", "esx01", "cpu")
        self.assertEqual(len(h[k]), 11)                       # 600 s at one a minute: this and the 10 before
        self.assertNotIn((f.key, "host", "esx01", "mem"), h)
        self.assertIn((f.key, "vm", "app07", "mem"), h)
        self.assertNotIn((f.key, "vm", "db02", "cpu"), h)
        self.assertTrue(all(a[0] < b[0] for a, b in zip(h[k], h[k][1:])))
        f.result = {"hosts": [], "vms": []}                   # gone from vCenter: its samples age out
        for _ in range(12):
            self.step(c, clock)
        self.assertEqual(c.view()["history"], {})

    def test_ontap_polls_keep_no_history(self):
        f = Fake(key="ontap:192.0.2.10", kind="ontap", result={"hosts": [{"name": "x", "cpu_pct": 1.0}]})
        c, clock, _logs = self.make(f)
        self.step(c, clock)
        self.assertEqual(c.view()["history"], {})

    def test_collector_with_the_vim_stub(self):
        vim, port = vim_stub(self)
        s = W.VcenterSource("127.0.0.1", "wall-reader", "pass", verify=False, port=port)
        c, clock, logs = self.make(s)
        for _ in range(3):
            self.step(c, clock)
        self.assertEqual(vim.logins, 1)
        self.assertEqual(c.view()["sources"][s.key]["state"], "ok")
        self.assertIn((s.key, "vm", "app07", "cpu"), c.view()["history"])
        vim.password, vim.drop_next = "changed", True        # the account's password changed: one refused re-login, then quiet
        for _ in range(10):
            self.step(c, clock)
        self.assertEqual(vim.methods.count("Login"), 2)
        self.assertEqual(c.view()["sources"][s.key]["state"], "auth")
        self.assertEqual(len(logs), 1)
        self.assertTrue(set(vim.methods) <= set(W.VIM_ALLOWED))

    def test_sources_from_config(self):
        out = W.sources_from_config({"vcenter": {"user": "u", "password": "p", "hosts": ["vc01.corp.example.mil", "VC01.corp.example.mil",
                                                                                        "vc02.corp.example.mil:8443"], "verify": False},
                                     "ontap": {"user": "u", "password": "p", "clusters": ["192.0.2.10"]}})
        self.assertEqual([s.key for s in out], ["vcenter:vc01.corp.example.mil", "vcenter:vc02.corp.example.mil:8443", "ontap:192.0.2.10"])
        self.assertEqual(out[1].port, 8443)
        self.assertEqual(W.sources_from_config({"vcenter": {"user": "u", "hosts": ["vc01"]}}), [])      # no password: none


class SharedAccount(unittest.TestCase):
    """1.129.0 review: the site's shared accounts - one refused login backs off every source of that account; after the start
    and after each back-off one source tries first and the others wait for its answer: exactly one bad login per window"""

    def make(self, srcs, auth_retry=900):
        logs = []
        clock = Clock()
        return W.Collector(srcs, seconds=60, auth_retry=auth_retry, log=logs.append, clock=clock), clock, logs

    def step(self, c, clock):
        c.cycle()
        c.wait(10)
        time.sleep(0.05)
        c.wait(10)                                             # the kicked ones too
        clock.t += 60

    def test_three_vcenters_one_bad_login_per_window(self):
        stubs = [vim_stub(self) for _ in range(3)]
        srcs = [W.VcenterSource("127.0.0.1", "wall-reader", "old", verify=False, port=p) for _v, p in stubs]
        c, clock, logs = self.make(srcs)
        for _ in range(14):                                     # 14 minutes: inside the window
            self.step(c, clock)
        self.assertEqual(sum(v.methods.count("Login") for v, _p in stubs), 1)
        self.assertEqual(set(x["state"] for x in c.view()["sources"].values()), {"auth"})
        self.assertEqual(len(logs), 1)
        for _ in range(16):                                     # past 15 minutes: one more try, still one source
            self.step(c, clock)
        self.assertEqual(sum(v.methods.count("Login") for v, _p in stubs), 2)
        for v, _p in stubs:
            v.password = "old"                                  # the account fixed: the next window's probe, then the others at once
        for _ in range(16):
            self.step(c, clock)
        self.assertEqual([v.logins for v, _p in stubs], [1, 1, 1])
        self.assertEqual(set(x["state"] for x in c.view()["sources"].values()), {"ok"})

    def test_a_vcenter_that_does_not_answer_passes_the_probe_on(self):
        stubs = [vim_stub(self) for _ in range(2)]
        dead = W.VcenterSource("127.0.0.1", "wall-reader", "pass", verify=False, port=closed_port(), timeout=2)
        srcs = [dead] + [W.VcenterSource("127.0.0.1", "wall-reader", "pass", verify=False, port=p) for _v, p in stubs]
        c, clock, _logs = self.make(srcs)
        self.step(c, clock)                                     # the first probe: no answer (not a refusal)
        self.assertEqual([v.logins for v, _p in stubs], [0, 0])
        self.step(c, clock)                                     # the next source probes, then the rest at once
        self.assertEqual([v.logins for v, _p in stubs], [1, 1])
        self.assertEqual(c.view()["sources"][dead.key]["state"], "error")

    def test_other_accounts_are_not_held(self):
        (v1, p1), (v2, p2) = vim_stub(self), vim_stub(self)
        bad = W.VcenterSource("127.0.0.1", "wall-reader", "old", verify=False, port=p1)
        other = W.VcenterSource("127.0.0.1", "other-reader", "pass", verify=False, port=p2)
        c, clock, _logs = self.make([bad, other])
        for _ in range(3):
            self.step(c, clock)
        self.assertEqual((v1.methods.count("Login"), v2.logins), (1, 1))
        self.assertEqual(c.view()["sources"][other.key]["state"], "ok")

    def test_two_ontap_clusters_one_bad_request_per_window(self):
        a, b = Ontap("right"), Ontap("right")
        sa, pa = serve(ontap_handler(a))
        sb, pb = serve(ontap_handler(b))
        for x in (sa, sb):
            self.addCleanup(x.server_close)
            self.addCleanup(x.shutdown)
        srcs = [W.OntapSource("127.0.0.1:%d" % p, "wall-reader", "wrong", verify=False) for p in (pa, pb)]
        c, clock, _logs = self.make(srcs)
        for _ in range(10):
            self.step(c, clock)
        self.assertEqual(len(a.paths) + len(b.paths), 1)


class Faults(unittest.TestCase):
    """1.129.0 review: permission faults and proxies' 401 are refusals (backed off); no session left behind"""

    def test_no_permission_on_the_view_logs_out_and_is_an_auth_error(self):
        vim, port = vim_stub(self)
        vim.view_fault = "vim25:NoPermission"                  # a namespace prefix on the fault's type
        s = W.VcenterSource("127.0.0.1", "wall-reader", "pass", verify=False, port=port)
        with self.assertRaises(W.AuthError):
            s.poll()
        self.assertEqual(vim.methods[-1], "Logout")              # the session ended, not left open
        self.assertEqual((s.cookie, vim.session), ("", ""))
        c = W.Collector([s], seconds=60, auth_retry=900, clock=Clock())
        for _ in range(5):
            c.cycle()
            c.wait(10)
        self.assertEqual(vim.methods.count("Login"), 2)          # the one above and one more - then backed off, no login a minute

    def test_a_plain_401_is_an_auth_error(self):
        vim, port = vim_stub(self)
        vim.http401 = True
        s = W.VcenterSource("127.0.0.1", "wall-reader", "pass", verify=False, port=port)
        with self.assertRaises(W.AuthError):
            s.poll()

    def test_bad_entries_are_left_out_and_said(self):
        logs = []
        out = W.sources_from_config({"vcenter": {"user": "u", "password": "p", "hosts": [
            "https://vc03.corp.example.mil/sdk", "[2001:db8::1]:443", "vc04.corp.example.mil:bad", "ftp://vc05.corp.example.mil",
            "u@vc06.corp.example.mil", "vc07.corp.example.mil:70000", "vc08.corp.example.mil:8443"]},
            "ontap": {"user": "u", "password": "p", "clusters": ["192.0.2.10:bad", "https://192.0.2.11"]}}, log=logs.append)
        self.assertEqual([x.key for x in out], ["vcenter:vc03.corp.example.mil", "vcenter:2001:db8::1", "vcenter:vc08.corp.example.mil:8443",
                                               "ontap:192.0.2.11"])
        self.assertEqual(out[0].port, 443)
        self.assertEqual(len(logs), 5)
        self.assertTrue(all(x["state"] == "left out" for x in logs))


class Codex(unittest.TestCase):
    """Codex 1.129.0 pre-release review: one back-off per account across vCenter and ONTAP, kept over a restart; the deadline
    from the refusal; a 401 on Login whatever its body"""

    def test_one_account_across_vcenter_and_ontap(self):
        self.assertEqual({W.account("CORP\\svc-wall"), W.account("svc-wall@corp.example.mil"), W.account("SVC-Wall")}, {"svc-wall"})
        vim, vp = vim_stub(self)
        o = Ontap("right")
        so, op = serve(ontap_handler(o))
        self.addCleanup(so.server_close)
        self.addCleanup(so.shutdown)
        srcs = [W.VcenterSource("127.0.0.1", "CORP\\svc-wall", "old", verify=False, port=vp),
                W.OntapSource("127.0.0.1:%d" % op, "svc-wall@corp.example.mil", "old", verify=False)]
        c, clock, _logs = SharedAccount.make(self, srcs)
        for _ in range(10):
            SharedAccount.step(self, c, clock)
        self.assertEqual(vim.methods.count("Login") + len(o.paths), 1)              # one bad login for the one account
        self.assertEqual(set(x["state"] for x in c.view()["sources"].values()), {"auth"})

    def test_the_back_off_outlives_a_restart(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        state = os.path.join(d, "wall-backoff.json")
        vim, vp = vim_stub(self)
        mk = lambda: W.VcenterSource("127.0.0.1", "svc-wall", "old", verify=False, port=vp)    # noqa: E731
        clock = Clock()
        c = W.Collector([mk()], seconds=60, auth_retry=900, clock=clock, state_file=state)
        c.cycle()
        c.wait(10)
        self.assertEqual(vim.methods.count("Login"), 1)
        self.assertEqual(oct(os.stat(state).st_mode & 0o777), "0o600")
        self.assertNotIn("svc-wall", open(state).read())                          # the account's key hashed
        clock.t += 300                                                            # the portal restarts 5 minutes later
        c2 = W.Collector([mk()], seconds=60, auth_retry=900, clock=clock, state_file=state)
        self.assertEqual(list(c2.view()["sources"].values())[0]["state"], "auth")
        for _ in range(9):                                                        # to 14 minutes after the refusal: no login
            c2.cycle()
            c2.wait(10)
            clock.t += 60
        self.assertEqual(vim.methods.count("Login"), 1)
        clock.t += 120
        c2.cycle()
        c2.wait(10)
        self.assertEqual(vim.methods.count("Login"), 2)                           # after the window: one try

    def test_the_deadline_is_from_the_refusal(self):
        clock = Clock()

        class Slow(Fake):
            def poll(self):
                self.calls += 1
                clock.t += 840                                                    # it waited 14 min behind another read
                raise W.AuthError(W.AUTH_WHY)
        f = Slow()
        c = W.Collector([f], seconds=60, auth_retry=900, clock=clock)
        start = clock.t
        c.cycle()
        c.wait(10)
        self.assertEqual(c.view()["sources"][f.key]["next_try"], start + 840 + 900)

    def test_a_401_on_login_is_a_refusal_whatever_the_body(self):
        vim, port = vim_stub(self)
        vim.login401_soap = True
        with self.assertRaises(W.AuthError):
            W.VcenterSource("127.0.0.1", "u", "p", verify=False, port=port).poll()


def closed_port():
    import socket
    sk = socket.socket()
    sk.bind(("127.0.0.1", 0))
    p = sk.getsockname()[1]
    sk.close()
    return p


# ---- vcsim -------------------------------------------------------------------------------------------------------------
@unittest.skipUnless(vcsim_demo.available(), "no podman or no %s here" % vcsim_demo.IMAGE)
class Vcsim(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url, cls.user, cls.pw, cls.name = vcsim_demo.start()
        cls.port = int(cls.url.rsplit(":", 1)[1].split("/")[0])

    @classmethod
    def tearDownClass(cls):
        vcsim_demo.stop(cls.name)

    def record(self, s):
        seen = []
        orig = s._send

        def send(method, args=""):
            seen.append(method)
            return orig(method, args)
        s._send = send
        return seen

    def test_one_login_over_cycles_and_the_shape(self):
        s = W.VcenterSource("127.0.0.1", self.user, self.pw, verify=False, port=self.port)
        seen = self.record(s)
        c = W.Collector([s], seconds=60)
        for _ in range(4):
            c.cycle()
            c.wait(60)
        st = c.view()["sources"][s.key]
        self.assertEqual(st["state"], "ok", st["why"])
        self.assertEqual(seen.count("Login"), 1)
        snap = st["snap"]
        for k in ("hosts", "clusters", "datastores", "vms"):
            self.assertTrue(snap[k], k)
        self.assertTrue(any(h["cpu_pct"] is not None for h in snap["hosts"]))
        self.assertTrue(any(d["capacity"] > 0 for d in snap["datastores"]))
        self.assertTrue(any(v["host"] for v in snap["vms"]))
        self.assertTrue(any(h["cluster"] for h in snap["hosts"]))
        try:                                                  # the session ended by an administrator: one re-login
            from pyVim.connect import SmartConnect, Disconnect
        except ImportError:
            self.skipTest("no pyVmomi to end the session in vcsim")
        si = SmartConnect(host="127.0.0.1", port=self.port, user=self.user, pwd=self.pw, disableSslCertValidation=True)
        self.addCleanup(Disconnect, si)
        si.RetrieveContent().sessionManager.TerminateSession([s.session_key])
        c.cycle()
        c.wait(60)
        self.assertEqual(seen.count("Login"), 2)
        s.close()
        self.assertTrue(set(seen) <= set(W.VIM_ALLOWED), seen)

    def test_bad_password(self):
        s = W.VcenterSource("127.0.0.1", self.user, "wrong-one", verify=False, port=self.port)
        seen = self.record(s)
        logs = []
        clock = Clock()
        c = W.Collector([s], seconds=60, auth_retry=900, log=logs.append, clock=clock)
        for _ in range(5):
            c.cycle()
            c.wait(60)
            clock.t += 60
        self.assertEqual(c.view()["sources"][s.key]["state"], "auth")
        self.assertEqual(seen.count("Login"), 1)
        self.assertEqual(len(logs), 1)

    def test_a_host_disconnected(self):
        try:
            from pyVim.connect import SmartConnect, Disconnect
            from pyVmomi import vim
        except ImportError:
            self.skipTest("no pyVmomi to disconnect a host in vcsim")
        si = SmartConnect(host="127.0.0.1", port=self.port, user=self.user, pwd=self.pw, disableSslCertValidation=True)
        self.addCleanup(Disconnect, si)
        ct = si.RetrieveContent()
        hosts = ct.viewManager.CreateContainerView(ct.rootFolder, [vim.HostSystem], True).view
        host = [h for h in hosts if isinstance(h.parent, vim.ClusterComputeResource)][0]
        name = host.name
        task = host.DisconnectHost_Task()
        for _ in range(100):
            if task.info.state in ("success", "error"):
                break
            time.sleep(0.05)
        if task.info.state != "success":
            self.skipTest("vcsim did not disconnect the host: %s" % task.info.state)
        self.addCleanup(lambda: host.ReconnectHost_Task())
        snap = W.VcenterSource("127.0.0.1", self.user, self.pw, verify=False, port=self.port).poll()
        h = [x for x in snap["hosts"] if x["name"] == name][0]
        self.assertNotEqual(h["state"], "connected")
        self.assertIsNone(h["cpu_pct"])


def tearDownModule():
    if CERTS:
        shutil.rmtree(CERTS, True)


if __name__ == "__main__":
    unittest.main(verbosity=1)
