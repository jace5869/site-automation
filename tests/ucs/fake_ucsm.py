#!/usr/bin/env python3
"""A fake UCS Manager XML API for tests/ucs/run_ucs_test.sh (HTTP, standard library only).

  fake_ucsm.py PORT FIXTURE.json LOG

FIXTURE (tests/ucs/fixture.py): {"user", "password", "version", "priv", "classes": {classId: [attributes, ...]},
"fail": {classId: [errorCode, errorDescr]}}. Like UCS Manager: every call is a POST to /nuova with one XML element;
aaaLogin with a wrong user or password answers errorCode 551; a query with a cookie that is not open answers 552; a
class it does not know answers errorCode 103. Every method name (the element's tag) is written to LOG, one per line -
the tests check nothing but logins, logouts and queries came, and that every session was logged out.
FAKE_UCSM_EXPIRE=N: a session runs out after N queries (552), as one does after its refresh period."""
import http.server
import json
import os
import sys
import uuid
import xml.etree.ElementTree as ET

port, fixture, log = int(sys.argv[1]), json.load(open(sys.argv[2])), sys.argv[3]
expire = int(os.environ.get("FAKE_UCSM_EXPIRE", "0"))
sessions = {}


def err(tag, code, descr):
    return '<%s response="yes" errorCode="%s" invocationResult="unidentified-fail" errorDescr="%s" />' % (tag, code, descr)


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, text, code=200):
        out = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/xml")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self):
        with open(log, "a") as fh:
            fh.write("GET %s\n" % self.path)
        self.reply("<html>UCS Manager</html>")

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path != "/nuova":
            return self.reply("not found", 404)
        try:
            e = ET.fromstring(body)
        except ET.ParseError:
            return self.reply('<error response="yes" errorCode="101" errorDescr="malformed request" />')
        with open(log, "a") as fh:
            fh.write("%s %s\n" % (e.tag, e.get("classId", "")))
        if e.tag == "aaaLogin":
            if e.get("inName") != fixture["user"] or e.get("inPassword") != fixture["password"]:
                return self.reply(err("aaaLogin", 551, "Authentication failed"))
            c = "%s/%s" % (len(sessions) + 1, uuid.uuid4())
            sessions[c] = 0
            return self.reply('<aaaLogin cookie="" response="yes" outCookie="%s" outRefreshPeriod="600" outPriv="%s" '
                              'outDomains="" outChannel="noencssl" outEvtChannel="noencssl" outSessionId="" outVersion="%s" '
                              'outName="%s" />' % (c, fixture["priv"], fixture["version"], fixture["user"]))
        if e.tag == "aaaLogout":
            if sessions.pop(e.get("inCookie"), None) is None:
                return self.reply(err("aaaLogout", 555, "Session not found"))
            return self.reply('<aaaLogout cookie="" response="yes" outStatus="success" />')
        c = e.get("cookie")
        if c not in sessions or (expire and sessions[c] >= expire):
            sessions.pop(c, None)
            return self.reply(err(e.tag, 552, "Authorization required"))
        sessions[c] += 1
        if e.tag != "configResolveClass":
            return self.reply(err(e.tag, 101, "not served by the fake"))
        cid = e.get("classId")
        if cid in fixture.get("fail", {}):
            return self.reply(err(e.tag, *fixture["fail"][cid]))
        if cid not in fixture["classes"]:
            return self.reply(err(e.tag, 103, "class %s is not a valid class" % cid))
        out = ET.Element("configResolveClass", {"cookie": c, "response": "yes", "classId": cid})
        cfg = ET.SubElement(out, "outConfigs")
        for o in fixture["classes"][cid]:
            ET.SubElement(cfg, o.get("_class", cid), dict((k, str(v)) for k, v in o.items() if k != "_class"))
        self.reply(ET.tostring(out).decode())


http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
