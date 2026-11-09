#!/usr/bin/env python3
"""A stand-in STIG Manager node, Keycloak or ServiceNow instance for the availability and STIG Manager health tests
(plain HTTP on 127.0.0.1).
  api_mock.py PORT KIND STATE.json      KIND: stigman, keycloak or servicenow
STATE.json is re-read on every request, so a test changes it between runs:
  stigman:    {"state": "available", "db": true, "oidc": true, "version": "1.6.18", "migration": 42, "slow_ms": 0,
               "user": 200 (the status of /api/user - 401: the token refused), "appinfo": 200 (403: not an admin),
               "mysql": {"version": "10.11.9-MariaDB", "uptime": 86400, "threads": 12, "max": 151}, "node_uptime": 86400,
               "down": false (true: the port answers 502, like a proxy whose node is gone)}
  keycloak:   {"secret": "s3cret", "down": false}
  servicenow: {"user": "api", "password": "pw", "down": false, "slow_ms": 0, "login": 200}"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

PORT, KIND, STATE = int(sys.argv[1]), sys.argv[2], sys.argv[3]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        b = (json.dumps(body) if ctype == "application/json" else body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def state(self):
        with open(STATE) as f:
            s = json.load(f)
        if s.get("slow_ms"):
            time.sleep(s["slow_ms"] / 1000.0)
        return s

    def do_POST(self):
        s = self.state()
        form = parse_qs(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode())
        if KIND != "keycloak" or s.get("down"):
            return self.send(502, "Bad Gateway", "text/html")
        if not self.path.endswith("/protocol/openid-connect/token"):
            return self.send(404, {"error": "not found"})
        if form.get("client_secret", [""])[0] != s.get("secret"):
            return self.send(401, {"error": "unauthorized_client", "error_description": "Invalid client or Invalid client credentials"})
        self.send(200, {"access_token": "tok-" + form["client_id"][0], "token_type": "Bearer", "expires_in": 300})

    def do_GET(self):
        s = self.state()
        u = urlparse(self.path)
        if s.get("down"):
            return self.send(502, "<html>Bad Gateway</html>", "text/html")
        if KIND == "keycloak":
            if u.path.endswith("/.well-known/openid-configuration"):
                return self.send(200, {"issuer": "http://127.0.0.1:%d/realms/stigman" % PORT})
            return self.send(404, {"error": "not found"})
        if KIND == "servicenow":
            if u.path == "/login.do":
                return self.send(s.get("login", 200), "<html>login</html>", "text/html")
            import base64
            want = "Basic " + base64.b64encode(("%s:%s" % (s["user"], s["password"])).encode()).decode()
            if self.headers.get("Authorization") != want:
                return self.send(401, {"error": {"message": "User Not Authenticated"}})
            if u.path.startswith("/api/now/table/"):
                return self.send(200, {"result": [{"sys_id": "abc"}]})
            return self.send(404, {"error": {"message": "not found"}})
        st = {"currentState": s.get("state", "available"), "since": "2026-11-09T06:00:00.000Z",
              "dependencies": {"db": s.get("db", True), "oidc": s.get("oidc", True)}, "endpoints": {"ui": "/"}}
        if u.path == "/api/op/state":
            return self.send(200, st)
        if st["currentState"] != "available":
            return self.send(503, st)
        if u.path == "/api/op/configuration":
            return self.send(200, {"version": s.get("version", "1.6.18"), "classification": "U", "lastMigration": s.get("migration", 42),
                                   "commit": {"sha": "abc123"}})
        if not (self.headers.get("Authorization") or "").startswith("Bearer tok-"):
            return self.send(401, {"error": "Unauthorized", "detail": "no token"})
        if u.path == "/api/user":
            code = s.get("user", 200)
            return self.send(code, {"username": "healthcheck"} if code == 200 else {"error": "Unauthorized", "detail": "invalid token"})
        if u.path == "/api/collections":
            return self.send(200, [{"collectionId": "1", "name": "A"}, {"collectionId": "2", "name": "B"}])
        if u.path == "/api/op/appinfo":
            code = s.get("appinfo", 200)
            if code != 200:
                return self.send(code, {"error": "Forbidden", "detail": "User has insufficient privilege to complete this request."})
            m = s.get("mysql") or {}
            return self.send(200, {"date": "2026-11-09T06:00:00Z", "schema": "stig-manager-appinfo-v1.0", "version": s.get("version", "1.6.18"),
                                   "collections": {}, "requests": {}, "users": {},
                                   "mysql": {"version": m.get("version", "10.11.9-MariaDB"), "tables": {},
                                             "variables": {"max_connections": str(m.get("max", 151))},
                                             "status": {"Uptime": str(m.get("uptime", 86400)), "Threads_connected": str(m.get("threads", 12)),
                                                        "Max_used_connections": "30", "Aborted_connects": "2"}},
                                   "nodejs": {"version": "22.11.0", "uptime": s.get("node_uptime", 86400),
                                              "memory": {"rss": 314572800, "heapUsed": 104857600, "heapTotal": 157286400}}})
        self.send(404, {"error": "not found"})


HTTPServer(("127.0.0.1", PORT), H).serve_forever()
