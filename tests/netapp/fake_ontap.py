#!/usr/bin/env python3
"""A fake ONTAP REST API for tests/netapp/run_netapp_test.sh (HTTP, standard library only).

  fake_ontap.py PORT FIXTURE.json LOG

FIXTURE: {"user": "...", "password": "...", "fail": {"/api/path": 500}, "paths": {"/api/path":
{"fields": [allowed field names], "records": [...], "supports_ignore": false (= refuses
ignore_unknown_fields, like ONTAP 9.10)}, ...}}. Like ONTAP: a field it does not know in
fields= is HTTP 400 (fields=* always works); pages of max_records with _links.next; a wrong login is
HTTP 401; any method but GET is HTTP 405 and is written to LOG (the tests check nothing but GET came).
"writable": true (1.123.0, NetApp - expand a volume): PATCH of /api/storage/volumes/UUID and /api/storage/luns/UUID changes
that record (size, comment; space.size) and answers 202 with a job, /api/cluster/jobs/UUID answers success ("job_state":
"failure" fails them); a record whose fields are asked by name=, svm.name=, uuid= ... is filtered by them, like ONTAP; every
request is written to LOG with its body. "grow_on_read": {"uuid": bytes} - the next read of that volume after its first
answers that much bigger (someone grew it between the plan and the change).
FAKE_ONTAP_SLOW=/api/path=SECONDS[,...]: that path answers only after SECONDS (a busy cluster)."""
import base64
import http.server
import json
import os
import sys
import time
from urllib.parse import parse_qs, urlencode, urlparse

port, fixture, log = int(sys.argv[1]), json.load(open(sys.argv[2])), sys.argv[3]
slow = dict((x.split("=")[0], float(x.split("=")[1])) for x in os.environ.get("FAKE_ONTAP_SLOW", "").split(",") if "=" in x)


def known(field, allowed):
    """A requested field is known when it, a parent or a child of it is allowed (ONTAP accepts
    both 'space' and 'space.size')."""
    return field == "*" or any(a == field or a.startswith(field + ".") or field.startswith(a + ".") for a in allowed)


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        out = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def refuse(self):
        with open(log, "a") as fh:
            fh.write("%s %s\n" % (self.command, self.path))
        self.reply(405, {"error": {"message": "method not allowed", "code": "4"}})

    do_POST = do_PUT = do_DELETE = refuse

    def do_PATCH(self):
        if not fixture.get("writable"):
            return self.refuse()
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        with open(log, "a") as fh:
            fh.write("PATCH %s %s\n" % (self.path, body))
        want = "Basic " + base64.b64encode(("%s:%s" % (fixture["user"], fixture["password"])).encode()).decode()
        if self.headers.get("Authorization") != want:
            return self.reply(401, {"error": {"message": "not authorized", "code": "6"}})
        u = urlparse(self.path)
        base, _, uuid = u.path.rpartition("/")
        recs = [r for r in fixture["paths"].get(base, {}).get("records", []) if r.get("uuid") == uuid]
        if not recs:
            return self.reply(404, {"error": {"message": "entry doesn't exist", "code": "4"}})
        if fixture.get("patch_fail", {}).get(base):
            return self.reply(400, {"error": {"message": fixture["patch_fail"][base], "code": "917927"}})
        b = json.loads(body or "{}")
        r = recs[0]
        if "size" in b:
            r.setdefault("space", {})["size"] = b["size"]
        if "comment" in b:
            r["comment"] = b["comment"]
        if "space" in b:
            r.setdefault("space", {}).update(b["space"])
        if "autosize" in b:
            r.setdefault("autosize", {}).update(b["autosize"])
        self.reply(202, {"job": {"uuid": "job-" + uuid, "_links": {"self": {"href": "/api/cluster/jobs/job-" + uuid}}}})

    def do_GET(self):
        with open(log, "a") as fh:
            fh.write("GET %s\n" % self.path)
        want = "Basic " + base64.b64encode(("%s:%s" % (fixture["user"], fixture["password"])).encode()).decode()
        if self.headers.get("Authorization") != want:
            return self.reply(401, {"error": {"message": "not authorized", "code": "6"}})
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path in slow:
            time.sleep(slow[u.path])
        if u.path in fixture.get("fail", {}):
            return self.reply(fixture["fail"][u.path], {"error": {"message": "internal error", "code": "1"}})
        if u.path.startswith("/api/cluster/jobs/") and fixture.get("writable"):
            return self.reply(200, {"uuid": u.path.rsplit("/", 1)[1], "state": fixture.get("job_state", "success"), "message": "fake job"})
        ep = fixture["paths"].get(u.path)
        if ep is None:
            return self.reply(404, {"error": {"message": "API not found", "code": "3"}})
        ignore = q.get("ignore_unknown_fields") == "true"
        if ignore and not ep.get("supports_ignore", True):         # like ONTAP 9.10: no such parameter
            return self.reply(400, {"error": {"message": 'Unexpected argument "ignore_unknown_fields".', "code": "262179"}})
        for f in [x for x in q.get("fields", "").split(",") if x]:
            if not ignore and not known(f, ep["fields"]):
                return self.reply(400, {"error": {"message": 'The value "%s" is invalid for field "fields"' % f,
                                                  "code": "262197", "target": "fields"}})
        if "single" in ep:
            return self.reply(200, ep["single"])
        recs = ep["records"]
        if fixture.get("writable"):
            def at(r, k):
                for part in k.split("."):
                    r = (r or {}).get(part) if isinstance(r, dict) else None
                return r
            for k, v in q.items():
                if k not in ("fields", "max_records", "start", "ignore_unknown_fields", "return_records"):
                    recs = [r for r in recs if str(at(r, k)) == v]
            for r in recs:
                more = fixture.get("grow_on_read", {}).get(r.get("uuid"))
                if more and u.path == "/api/storage/volumes":
                    seen = fixture.setdefault("_seen", {})
                    seen[r["uuid"]] = seen.get(r["uuid"], 0) + 1
                    if seen[r["uuid"]] == fixture.get("grow_on_read_at", 2):
                        r["space"]["size"] += more
        size, start = int(q.get("max_records", "10000")), int(q.get("start", "0"))
        page = recs[start:start + size]
        body = {"records": page, "num_records": len(page)}
        if start + size < len(recs):
            nq = dict(q, start=str(start + size))
            body["_links"] = {"next": {"href": u.path + "?" + urlencode(nq, safe="*,.")}}
        self.reply(200, body)


http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
