#!/usr/bin/env python3
"""A stand-in Keycloak token endpoint and STIG Manager API for the STIG drift tests (plain HTTP on 127.0.0.1).
  stigman_mock.py PORT DATA.json LOG
DATA.json: {"secret": "...", "collections": [{"collectionId": "1", "name": "..."}],
            "assets": {"<collectionId>": [{"assetId": "11", "name": "...", "fqdn": "", "stigs": [{"benchmarkId": ..., "revisionStr": ...}]}]},
            "reviews": {"<assetId>": [{"ruleId": ..., "result": ..., "status": {"label": ...}, "rule": {...}}]}}
Re-read on every request, so a test can change it between runs. Every request is written to LOG (method, path)."""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

PORT, DATA, LOG = int(sys.argv[1]), sys.argv[2], sys.argv[3]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body):
        b = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def note(self):
        with open(LOG, "a") as f:
            f.write("%s %s\n" % (self.command, self.path))

    def do_POST(self):
        self.note()
        d = json.load(open(DATA))
        form = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
        if self.path != "/token":
            return self.send(405, {"error": "this test API is read-only"})
        if form.get("grant_type") != ["client_credentials"] or form.get("client_secret") != [d.get("secret")]:
            return self.send(401, {"error": "unauthorized_client"})
        self.send(200, {"access_token": "tok-" + form["client_id"][0], "token_type": "Bearer"})

    def do_GET(self):
        self.note()
        d = json.load(open(DATA))
        if self.headers.get("Authorization") != "Bearer tok-" + d.get("client", "autostig"):
            return self.send(401, {"error": "no token"})
        u = urlparse(self.path)
        q = parse_qs(u.query)
        parts = u.path.split("/")
        if u.path == "/api/collections":
            return self.send(200, d["collections"])
        if u.path == "/api/assets":
            if "collectionId" not in q:
                return self.send(422, {"error": "collectionId is required"})
            rows = [a for a in d["assets"].get(q["collectionId"][0], [])
                    if "benchmarkId" not in q or q["benchmarkId"][0] in [s["benchmarkId"] for s in a.get("stigs", [])]]
            col = [c for c in d["collections"] if c["collectionId"] == q["collectionId"][0]][0]
            return self.send(200, [dict(a, collection={"collectionId": col["collectionId"], "name": col["name"]}) for a in rows])
        if len(parts) == 6 and parts[1:3] == ["api", "collections"] and parts[4] == "reviews":
            return self.send(200, d["reviews"].get(parts[5], []))
        self.send(404, {"error": "not here: " + u.path})

    do_PUT = do_PATCH = do_DELETE = do_POST


HTTPServer(("127.0.0.1", PORT), H).serve_forever()
