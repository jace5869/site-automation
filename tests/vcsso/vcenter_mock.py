#!/usr/bin/env python3
"""A stand-in vCenter 8.0 REST API for the single sign-on tests (HTTPS on 127.0.0.1, a throwaway certificate):
POST/DELETE /api/session, GET /api/vcenter/services, /api/appliance/services,
/api/vcenter/certificate-management/vcenter/signing-certificate, /api/vcenter/identity/providers.
  vcenter_mock.py PORT CERT KEY DATA.json LOG
DATA.json (re-read on every request): {"users": {"name": "password"}, "forbidden": [names that log in but get 403],
  "services": {...}, "appliance": {...}, "signing": {...} or null (404), "providers": [...] or null (404)}"""
import base64
import json
import ssl
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT, CERT, KEY, DATA, LOG = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
SESSIONS = {}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body):
        b = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def note(self, who=""):
        with open(LOG, "a") as f:
            f.write("%s %s %s\n" % (self.command, self.path, who))

    def do_POST(self):
        d = json.load(open(DATA))
        if self.path != "/api/session":
            self.note()
            return self.send(405, {"error_type": "NOT_ALLOWED"})
        if d.get("no_api"):
            self.note()
            return self.send(404, {"error_type": "NOT_FOUND"})
        auth = self.headers.get("Authorization", "")
        user, _, pw = base64.b64decode(auth[6:]).decode().partition(":") if auth.startswith("Basic ") else ("", "", "")
        self.note(user)
        if d["users"].get(user) != pw or not user:
            return self.send(401, {"error_type": "UNAUTHENTICATED"})
        if user in d.get("forbidden", []):
            return self.send(403, {"error_type": "UNAUTHORIZED"})
        tok = "tok-%d" % (len(SESSIONS) + 1)
        SESSIONS[tok] = user
        self.send(201, tok)

    def do_DELETE(self):
        self.note(SESSIONS.get(self.headers.get("vmware-api-session-id", ""), ""))
        SESSIONS.pop(self.headers.get("vmware-api-session-id", ""), None)
        self.send(204, None)

    def do_GET(self):
        d = json.load(open(DATA))
        self.note(SESSIONS.get(self.headers.get("vmware-api-session-id", ""), ""))
        if self.headers.get("vmware-api-session-id") not in SESSIONS:
            return self.send(401, {"error_type": "UNAUTHENTICATED"})
        what = {"/api/vcenter/services": d.get("services"), "/api/appliance/services": d.get("appliance"),
                "/api/vcenter/certificate-management/vcenter/signing-certificate": d.get("signing"),
                "/api/vcenter/identity/providers": d.get("providers")}
        if self.path not in what or what[self.path] is None:
            return self.send(404, {"error_type": "NOT_FOUND"})
        self.send(200, what[self.path])


srv = HTTPServer(("127.0.0.1", PORT), H)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(CERT, KEY)
srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
srv.serve_forever()
