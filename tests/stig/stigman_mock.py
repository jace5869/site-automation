#!/usr/bin/env python3
"""A stand-in Keycloak token endpoint and STIG Manager API for the STIG drift tests (plain HTTP on 127.0.0.1).
  stigman_mock.py PORT DATA.json LOG
DATA.json: {"secret": "...", "collections": [{"collectionId": "1", "name": "..."}],
            "assets": {"<collectionId>": [{"assetId": "11", "name": "...", "fqdn": "", "stigs": [{"benchmarkId": ..., "revisionStr": ...}]}]},
            "reviews": {"<assetId>": [{"ruleId": ..., "result": ..., "status": {"label": ...}, "rule": {...}, "benchmarkId": optional,
                                       "revision": optional - "V2R8": a review of that earlier revision's rule only (its check
                                       changed), served only with rules=all (projection=stigs: its revision, not
                                       the default), as STIG Manager does}]},
            "rules": {"<benchmarkId>": [{"ruleId": ..., "groupId": ..., "version": ..., "severity": ..., "title": ...}]} (every revision),
            "writers": ["<client>", ...] (optional: the clients that may write reviews - none: read-only),
            "managers": ["<client>", ...] (optional: the clients that may create assets - the Manage grant; none: no one),
            "grants": {"<client>": {"<collectionId>": roleId}} (optional: GET /api/user's collectionGrants - 1 Restricted, 2 Full,
                      3 Manage, 4 Owner - and what writes are allowed; else managers 3, others 2 in every collection;
                      GET /api/collections lists only those the client has a grant in, as STIG Manager),
            "latest": {"<benchmarkId>": "V2R8"} (optional: the newest revision; else V2R7),
            "titles": {"<benchmarkId>": "..."} (optional: the STIGs' titles in GET /api/stigs - the installed STIGs are the rules' keys),
            "reject_fail_without_comment": true (optional: a finding without a comment is rejected, as a collection may say),
            "collection_grants": {"<collectionId>": [{"grantId", "roleId", "user": {...} or "userGroup": {...}, "acl": {"defaultAccess",
                                  "acl": [...]}}]} (optional: GET /api/collections/C/grants and .../grants/G/acl - Manage only)}
An asset may carry "labels": [{"labelId", "name"}]; GET /api/assets gives each its statusStats.maxTs (its reviews' latest ts).
1.26.0: PATCH /api/assets/A {"collectionId"} moves it (Manage in both; a name in the destination: a duplicate error, as
STIG Manager); PUT /api/assets/A/stigs/B assigns a STIG (Manage; at the collection's revision).
1.58.0, the scorecard's reads: GET /api/collections/C/findings?aggregator=ruleId&projection=assets (the reviews with the
result fail, a row per rule: ruleId, title, severity, version, assetCount, assets) and
GET /api/collections/C/metrics/summary/collection (assets: how many) - "no_metrics": true answers that one 404 (an older
STIG Manager), "no_findings": ["<collectionId>"] answers the findings of those 403.
Re-read on every request, so a test can change it between runs. Every request is written to LOG (method, path); a write's
body to LOG.writes, one JSON line each. Writes (playbooks/stigman_update.yml): GET /api/collections/C/reviews/A/RULE and
POST /api/collections/C/reviews/A only - DATA.json is updated as STIG Manager would; POST /api/assets (a new asset, with its
STIGs at the collection's revision; a name there already: 422)."""
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

    def role(self, d, cid):
        """The client's grant in a collection (roleId)."""
        c = self.client(d)
        g = d.get("grants", {}).get(c)
        if g is not None:
            return int(g.get(cid, 0))
        return 3 if c in d.get("managers", []) else 2

    def do_POST(self):
        self.note()
        d = json.load(open(DATA))
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        parts = urlparse(self.path).path.split("/")
        if len(parts) == 6 and parts[1:3] == ["api", "collections"] and parts[4] == "reviews":
            return self.write(d, parts[3], parts[5], raw)
        if parts[1:] == ["api", "assets"]:
            return self.create(d, raw)
        form = parse_qs(raw)
        if self.path != "/token":
            return self.send(405, {"error": "this test API is read-only"})
        if form.get("grant_type") != ["client_credentials"] or form.get("client_secret") != [d.get("secret")]:
            return self.send(401, {"error": "unauthorized_client"})
        self.send(200, {"access_token": "tok-" + form["client_id"][0], "token_type": "Bearer"})

    def stats(self, d, a):
        """The asset as GET /api/assets gives it: its labels, and statusStats.maxTs - its reviews' latest time."""
        ts = [r.get("ts") or "" for r in d["reviews"].get(a["assetId"], []) if r.get("ts")]
        return dict(a, labels=a.get("labels") or [], statusStats={"maxTs": max(ts) if ts else None})

    def revision(self, d, cid, bench):
        """The collection's revision of a STIG (its assets'), else the newest."""
        for a in d["assets"].get(cid, []):
            for st in a.get("stigs", []):
                if st["benchmarkId"] == bench:
                    return st["revisionStr"]
        return d.get("latest", {}).get(bench, "V2R7")

    def create(self, d, raw):
        """POST /api/assets: only for a client with the Manage grant (managers); description required, a name unique."""
        b = json.loads(raw)
        if self.client(d) not in d.get("managers", []) or self.role(d, str(b.get("collectionId"))) < 3:
            return self.send(403, {"error": "User has insufficient privilege to complete this request."})
        if "description" not in b or not b.get("name") or not b.get("collectionId"):
            return self.send(400, {"error": "request/body must have required property 'description'"})
        mine = d["assets"].setdefault(b["collectionId"], [])
        if any(a["name"].lower() == b["name"].lower() for a in mine):
            return self.send(422, {"error": "Unprocessable Entity.", "detail": [{"failure": "name exists"}]})
        with open(LOG + ".writes", "a") as f:
            f.write(json.dumps({"asset": b}) + "\n")
        new = {"assetId": str(100 + sum(len(v) for v in d["assets"].values())), "name": b["name"], "fqdn": b.get("fqdn"),
               "description": b.get("description") or "", "ip": b.get("ip") or "", "mac": b.get("mac") or "",
               "stigs": [{"benchmarkId": x, "revisionStr": self.revision(d, b["collectionId"], x)} for x in b.get("stigs") or []]}
        mine.append(new)
        json.dump(d, open(DATA, "w"))
        self.send(201, new)

    def write(self, d, cid, aid, raw):
        """POST /api/collections/C/reviews/A: each review merged as STIG Manager does (null detail or comment keeps it)."""
        if self.client(d) not in d.get("writers", []) or self.role(d, cid) < 1:
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
            # as STIG Manager: only the collections the client has a grant in (1.26.0: a route to one it cannot read)
            return self.send(200, [c for c in d["collections"] if self.role(d, c["collectionId"]) > 0])
        if u.path == "/api/user":
            return self.send(200, {"userId": "1", "username": self.client(d), "collectionGrants": [
                {"roleId": self.role(d, c["collectionId"]), "collection": {"collectionId": c["collectionId"], "name": c["name"]}}
                for c in d["collections"] if self.role(d, c["collectionId"]) > 0]})
        if u.path == "/api/stigs":
            return self.send(200, [{"benchmarkId": b, "title": d.get("titles", {}).get(b, b), "lastRevisionStr": d.get("latest", {}).get(b, "V2R7"),
                                    "ruleCount": len(rows)} for b, rows in sorted(d.get("rules", {}).items())])
        if len(parts) == 4 and parts[1:3] == ["api", "assets"]:
            cid, a = self.find(d, parts[3])
            if a is None:
                return self.send(404, {"error": "no asset " + parts[3]})
            col = [c for c in d["collections"] if c["collectionId"] == cid][0]
            return self.send(200, dict(self.stats(d, a), collection={"collectionId": cid, "name": col["name"]}))
        if len(parts) in (5, 7) and parts[1:3] == ["api", "collections"] and parts[4] == "grants":
            if self.role(d, parts[3]) < 3:
                return self.send(403, {"error": "User has insufficient privilege to complete this request."})
            rows = d.get("collection_grants", {}).get(parts[3], [])
            if len(parts) == 5:
                return self.send(200, [dict((k, v) for k, v in g.items() if k != "acl") for g in rows])
            g = [g for g in rows if str(g.get("grantId")) == parts[5]]
            return self.send(200, g[0].get("acl") or {"defaultAccess": "none", "acl": []}) if g else self.send(404, {"error": "no grant"})
        if u.path == "/api/assets":
            if "collectionId" not in q:
                return self.send(422, {"error": "collectionId is required"})
            rows = [a for a in d["assets"].get(q["collectionId"][0], [])
                    if "benchmarkId" not in q or q["benchmarkId"][0] in [s["benchmarkId"] for s in a.get("stigs", [])]]
            col = [c for c in d["collections"] if c["collectionId"] == q["collectionId"][0]][0]
            return self.send(200, [dict(self.stats(d, a), collection={"collectionId": col["collectionId"], "name": col["name"]}) for a in rows])
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
            # a review that names its benchmark is only in that STIG's reviews (the hosts with several STIGs); one of an
            # earlier revision only with rules=all (the default: default-mapped - the revision in use)
            every = q.get("rules", ["default-mapped"])[0] == "all"
            rows = [r for r in d["reviews"].get(parts[5], [])
                    if ("benchmarkId" not in q or r.get("benchmarkId", q["benchmarkId"][0]) == q["benchmarkId"][0]) and (every or not r.get("revision"))]
            if "stigs" in q.get("projection", []):
                rows = [dict(r, stigs=[{"benchmarkId": r.get("benchmarkId") or (q.get("benchmarkId") or [""])[0],
                                        "revisionStr": r.get("revision") or "", "isDefault": not r.get("revision")}]) for r in rows]
            return self.send(200, [dict((k, v) for k, v in r.items() if k != "revision") for r in rows])
        if len(parts) == 5 and parts[1:3] == ["api", "collections"] and parts[4] == "findings":
            if self.role(d, parts[3]) < 1 or parts[3] in d.get("no_findings", []):
                return self.send(403, {"error": "User has insufficient privilege to complete this request."})
            if q.get("aggregator") != ["ruleId"]:
                return self.send(400, {"error": "this test API aggregates findings by ruleId only"})
            rows = {}
            for a in d["assets"].get(parts[3], []):
                for r in d["reviews"].get(a["assetId"], []):
                    if r.get("result") != "fail" or r.get("revision"):
                        continue
                    rule = r.get("rule") or {}
                    f = rows.setdefault(r["ruleId"], {"ruleId": r["ruleId"], "title": rule.get("title", ""), "severity": rule.get("severity", "medium"),
                                                      "version": rule.get("version", ""), "assetCount": 0, "assets": []})
                    f["assetCount"] += 1
                    f["assets"].append({"assetId": a["assetId"], "name": a["name"]})
            if "assets" not in q.get("projection", []):
                for f in rows.values():
                    del f["assets"]
            return self.send(200, [rows[k] for k in sorted(rows)])
        if len(parts) == 7 and parts[1:3] == ["api", "collections"] and parts[4:] == ["metrics", "summary", "collection"]:
            if d.get("no_metrics"):
                return self.send(404, {"error": "not here: " + u.path})
            col = [c for c in d["collections"] if c["collectionId"] == parts[3]]
            if not col or self.role(d, parts[3]) < 1:
                return self.send(403, {"error": "User has insufficient privilege to complete this request."})
            return self.send(200, {"collectionId": parts[3], "name": col[0]["name"], "assets": len(d["assets"].get(parts[3], [])),
                                   "metrics": {"findings": {}}})
        if len(parts) == 5 and parts[1:3] == ["api", "collections"] and parts[4] == "stigs":
            benches = sorted(set(st["benchmarkId"] for a in d["assets"].get(parts[3], []) for st in a.get("stigs", [])))
            return self.send(200, [{"benchmarkId": b, "revisionStr": self.revision(d, parts[3], b)} for b in benches])
        if len(parts) == 6 and parts[1:3] == ["api", "stigs"] and parts[4:] == ["revisions", "latest"]:
            if parts[3] not in d.get("rules", {}):
                return self.send(404, {"error": "no STIG " + parts[3]})
            return self.send(200, {"benchmarkId": parts[3], "revisionStr": d.get("latest", {}).get(parts[3], "V2R7")})
        if len(parts) == 7 and parts[1:3] == ["api", "stigs"] and parts[4] == "revisions" and parts[6] == "rules":
            if parts[3] not in d.get("rules", {}):
                return self.send(404, {"error": "no STIG " + parts[3]})
            return self.send(200, d["rules"][parts[3]])
        self.send(404, {"error": "not here: " + u.path})

    def refuse(self):
        self.note()
        self.send(405, {"error": "this test API takes no %s" % self.command})

    do_DELETE = refuse

    def find(self, d, aid):
        """(collectionId, the asset) or (None, None)."""
        for cid, rows in d["assets"].items():
            for a in rows:
                if a["assetId"] == aid:
                    return cid, a
        return None, None

    def do_PATCH(self):
        """PATCH /api/assets/A {"collectionId": X}: the asset moved, its reviews, STIGs and labels with it (Manage in both)."""
        self.note()
        d = json.load(open(DATA))
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        parts = urlparse(self.path).path.split("/")
        if not self.client(d) or len(parts) != 4 or parts[1:3] != ["api", "assets"]:
            return self.send(405, {"error": "this test API takes no PATCH there"})
        b = json.loads(raw)
        cid, a = self.find(d, parts[3])
        if a is None:
            return self.send(404, {"error": "no asset " + parts[3]})
        to = str(b.get("collectionId") or cid)
        if set(b) - {"collectionId"}:
            return self.send(400, {"error": "this test API moves assets only"})
        if self.role(d, cid) < 3:
            return self.send(403, {"error": "User has insufficient privilege to complete this request.", "detail": "Insufficient access to this asset's collection."})
        if self.role(d, to) < 3:
            return self.send(403, {"error": "User has insufficient privilege to complete this request.",
                                   "detail": "User has insufficient privilege in destination collection to transfer this asset."})
        if any(x["name"].lower() == a["name"].lower() for x in d["assets"].get(to, [])) and to != cid:
            return self.send(500, {"error": "Duplicate entry '%s-%s-1' for key 'asset.INDEX_NAME_COLLECTION_ENABLED'" % (a["name"], to), "code": "ER_DUP_ENTRY"})
        with open(LOG + ".writes", "a") as f:
            f.write(json.dumps({"move": a["assetId"], "from": cid, "to": to}) + "\n")
        d["assets"][cid].remove(a)
        d["assets"].setdefault(to, []).append(a)
        json.dump(d, open(DATA, "w"))
        col = [c for c in d["collections"] if c["collectionId"] == to][0]
        self.send(200, dict(a, collection={"collectionId": to, "name": col["name"]}))

    def do_PUT(self):
        """PUT /api/assets/A/stigs/B: the STIG assigned to the asset (Manage), at its collection's revision."""
        self.note()
        d = json.load(open(DATA))
        parts = urlparse(self.path).path.split("/")
        if not self.client(d) or len(parts) != 6 or parts[1:3] != ["api", "assets"] or parts[4] != "stigs":
            return self.send(405, {"error": "this test API takes no PUT there"})
        cid, a = self.find(d, parts[3])
        if a is None:
            return self.send(404, {"error": "no asset " + parts[3]})
        if self.role(d, cid) < 3:
            return self.send(403, {"error": "User has insufficient privilege to complete this request."})
        if parts[5] not in d.get("rules", {}):
            return self.send(404, {"error": "no STIG " + parts[5]})
        if parts[5] not in [x["benchmarkId"] for x in a.get("stigs", [])]:
            with open(LOG + ".writes", "a") as f:
                f.write(json.dumps({"assign": parts[5], "asset": a["assetId"]}) + "\n")
            a.setdefault("stigs", []).append({"benchmarkId": parts[5], "revisionStr": self.revision(d, cid, parts[5])})
            json.dump(d, open(DATA, "w"))
        self.send(200, a["stigs"])


HTTPServer(("127.0.0.1", PORT), H).serve_forever()
