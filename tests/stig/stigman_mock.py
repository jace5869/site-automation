#!/usr/bin/env python3
"""A stand-in Keycloak token endpoint and STIG Manager API for the STIG drift tests (plain HTTP on 127.0.0.1).
  stigman_mock.py PORT DATA.json LOG
DATA.json: {"secret": "...", "collections": [{"collectionId": "1", "name": "..."}],
            "assets": {"<collectionId>": [{"assetId": "11", "name": "...", "fqdn": "", "stigs": [{"benchmarkId": ..., "revisionStr": ...}]}]},
            "reviews": {"<assetId>": [{"ruleId": ..., "result": ..., "status": {"label": ...}, "rule": {...}, "benchmarkId": optional}]},
            "rules": {"<benchmarkId>": [{"ruleId": ..., "groupId": ..., "version": ..., "severity": ..., "title": ...}]} (every revision),
            "writers": ["<client>", ...] (optional: the clients that may write reviews - none: read-only),
            "reject_fail_without_comment": true (optional: a finding without a comment is rejected, as a collection may say)}
Re-read on every request, so a test can change it between runs. Every request is written to LOG (method, path); a write's
body to LOG.writes, one JSON line each. Writes (playbooks/stigman_update.yml): GET /api/collections/C/reviews/A/RULE and
POST /api/collections/C/reviews/A only - DATA.json is updated as STIG Manager would."""
import json
import sys
import time
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

    def client(self, d):
        """The client the request's token is of, or None."""
        tok = (self.headers.get("Authorization") or "")[len("Bearer tok-"):]
        return tok if tok and (tok == d.get("client", "autostig") or tok in d.get("writers", [])) else None

    def do_POST(self):
        self.note()
        d = json.load(open(DATA))
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        parts = urlparse(self.path).path.split("/")
        if len(parts) == 6 and parts[1:3] == ["api", "collections"] and parts[4] == "reviews":
            return self.write(d, parts[3], parts[5], raw)
        form = parse_qs(raw)
        if self.path != "/token":
            return self.send(405, {"error": "this test API is read-only"})
        if form.get("grant_type") != ["client_credentials"] or form.get("client_secret") != [d.get("secret")]:
            return self.send(401, {"error": "unauthorized_client"})
        self.send(200, {"access_token": "tok-" + form["client_id"][0], "token_type": "Bearer"})

    def write(self, d, cid, aid, raw):
        """POST /api/collections/C/reviews/A: each review merged as STIG Manager does (null detail or comment keeps it)."""
        if self.client(d) not in d.get("writers", []):
            return self.send(403, {"error": "Forbidden", "detail": "the token's scope or grant does not allow writing reviews"})
        if not any(a["assetId"] == aid for a in d["assets"].get(cid, [])):
            return self.send(403, {"error": "Forbidden", "detail": "asset %s is not in collection %s" % (aid, cid)})
        body = json.loads(raw)
        with open(LOG + ".writes", "a") as f:
            f.write(json.dumps({"collection": cid, "asset": aid, "reviews": body}) + "\n")
        mine = d["reviews"].setdefault(aid, [])
        rules = dict((r["ruleId"], r) for rows in d.get("rules", {}).values() for r in rows)
        affected, rejected = {"inserted": 0, "updated": 0}, []
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for b in body:
            old = next((r for r in mine if b["ruleId"] in [r["ruleId"]] + r.get("ruleIds", [])), None)
            comment = b.get("comment") if b.get("comment") is not None else (old or {}).get("comment", "")
            if d.get("reject_fail_without_comment") and b["result"] == "fail" and not comment:
                rejected.append({"ruleId": b["ruleId"], "reason": "comment is required for findings"})
                continue
            new = {"ruleId": b["ruleId"], "ruleIds": [b["ruleId"]], "result": b["result"], "status": {"label": b.get("status") or "saved"},
                   "detail": b.get("detail") if b.get("detail") is not None else (old or {}).get("detail", ""), "comment": comment,
                   "resultEngine": b.get("resultEngine"), "ts": now,
                   "rule": (old or {}).get("rule") or {k: rules.get(b["ruleId"], {}).get(k, "") for k in ("ruleId", "version", "severity", "title")}}
            if old:
                old.clear(); old.update(new); affected["updated"] += 1
            else:
                mine.append(new); affected["inserted"] += 1
        json.dump(d, open(DATA, "w"))
        self.send(200, {"affected": affected, "rejected": rejected})

    def do_GET(self):
        self.note()
        d = json.load(open(DATA))
        if not self.client(d):
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
        if len(parts) in (9, 10) and parts[1:3] == ["api", "collections"] and parts[4] == "reviews" and parts[7:9] == ["metadata", "keys"]:
            # a review's metadata (STIG Manager keeps a review's attachments there: the key "artifacts"); no review: 404
            r = next((r for r in d["reviews"].get(parts[5], []) if parts[6] in [r["ruleId"]] + r.get("ruleIds", [])), None)
            if not r:
                return self.send(404, {"error": "metadata keys not found"})
            md = r.get("metadata") or {}
            if len(parts) == 9:
                return self.send(200, sorted(md))
            return self.send(200, md[parts[9]]) if parts[9] in md else self.send(404, {"error": "metadata key not found"})
        if len(parts) == 7 and parts[1:3] == ["api", "collections"] and parts[4] == "reviews":
            r = next((r for r in d["reviews"].get(parts[5], []) if parts[6] in [r["ruleId"]] + r.get("ruleIds", [])), None)
            if not r:
                self.send_response(204)
                self.end_headers()
                return
            return self.send(200, r)
        if len(parts) == 6 and parts[1:3] == ["api", "collections"] and parts[4] == "reviews":
            # a review that names its benchmark is only in that STIG's reviews (the hosts with several STIGs)
            return self.send(200, [r for r in d["reviews"].get(parts[5], [])
                                   if "benchmarkId" not in q or r.get("benchmarkId", q["benchmarkId"][0]) == q["benchmarkId"][0]])
        if len(parts) == 7 and parts[1:3] == ["api", "stigs"] and parts[4] == "revisions" and parts[6] == "rules":
            if parts[3] not in d.get("rules", {}):
                return self.send(404, {"error": "no STIG " + parts[3]})
            return self.send(200, d["rules"][parts[3]])
        self.send(404, {"error": "not here: " + u.path})

    def refuse(self):
        self.note()
        self.send(405, {"error": "this test API takes no %s" % self.command})

    do_PUT = do_PATCH = do_DELETE = refuse


HTTPServer(("127.0.0.1", PORT), H).serve_forever()
