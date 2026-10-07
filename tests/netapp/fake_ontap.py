#!/usr/bin/env python3
"""A fake ONTAP REST API for tests/netapp/run_netapp_test.sh (HTTP, standard library only).

  fake_ontap.py PORT FIXTURE.json LOG

FIXTURE: {"user": "...", "password": "...", "fail": {"/api/path": 500}, "paths": {"/api/path":
{"fields": [allowed field names], "records": [...], "supports_ignore": false (= refuses
ignore_unknown_fields, like ONTAP 9.10)}, ...}}. Like ONTAP: a field it does not know in
fields= is HTTP 400 (fields=* always works); pages of max_records with _links.next; a wrong login is
HTTP 401; any method but GET is HTTP 405 and is written to LOG (the tests check nothing but GET came)."""
import base64
import http.server
import json
import sys
from urllib.parse import parse_qs, urlencode, urlparse

port, fixture, log = int(sys.argv[1]), json.load(open(sys.argv[2])), sys.argv[3]


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

    do_POST = do_PATCH = do_PUT = do_DELETE = refuse

    def do_GET(self):
        with open(log, "a") as fh:
            fh.write("GET %s\n" % self.path)
        want = "Basic " + base64.b64encode(("%s:%s" % (fixture["user"], fixture["password"])).encode()).decode()
        if self.headers.get("Authorization") != want:
            return self.reply(401, {"error": {"message": "not authorized", "code": "6"}})
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path in fixture.get("fail", {}):
            return self.reply(fixture["fail"][u.path], {"error": {"message": "internal error", "code": "1"}})
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
        size, start = int(q.get("max_records", "10000")), int(q.get("start", "0"))
        page = recs[start:start + size]
        body = {"records": page, "num_records": len(page)}
        if start + size < len(recs):
            nq = dict(q, start=str(start + size))
            body["_links"] = {"next": {"href": u.path + "?" + urlencode(nq, safe="*,.")}}
        self.reply(200, body)


http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
