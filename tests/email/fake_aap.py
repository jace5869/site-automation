#!/usr/bin/env python3
"""A fake AAP API for the report website's copy job (playbooks/report_web_copy.yml, roles/site_email site_report_copy):
the jobs in a JSON file - [{id, job_template, name, status, job_type, finished, artifacts}], read again on every request,
so a test can add jobs while it runs - listed as AAP lists them (finished__gt, status__in, job_type, job_template,
id__lt, order_by, page_size, page; the list leaves the artifacts out, each job's own page has them).
Login: basic audit / pw, or a token (Bearer t0ken). API path /api/controller/v2/ (AAP 2.5 and later).
Usage: fake_aap.py PORT JOBS_JSON"""
import base64
import json
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, JOBS = int(sys.argv[1]), sys.argv[2]


def jobs():
    try:
        with open(JOBS) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        base = "/api/controller/v2/"
        if not u.path.startswith(base):
            return self._send(404, {"detail": "Not found."})
        a = self.headers.get("Authorization", "")
        if a != "Bearer t0ken" and not (a.startswith("Basic ") and base64.b64decode(a[6:]).decode() == "audit:pw"):
            return self._send(401, {"detail": "Authentication credentials were not provided."})
        rest = u.path[len(base):].strip("/").split("/")
        if rest == ["me"]:
            return self._send(200, {"results": [{"id": 1, "username": "audit"}]})
        if rest[0] != "jobs":
            return self._send(404, {"detail": "Not found."})
        js = jobs()
        if len(rest) == 2:
            found = [j for j in js if str(j["id"]) == rest[1]]
            return self._send(200, found[0]) if found else self._send(404, {"detail": "Not found."})
        js = [j for j in js if (not q.get("job_type") or j.get("job_type", "run") == q["job_type"])
              and (not q.get("job_template") or str(j["job_template"]) == q["job_template"])
              and (not q.get("id__lt") or j["id"] < int(q["id__lt"]))
              and (not q.get("status__in") or j["status"] in q["status__in"].split(","))
              and (not q.get("finished__gt") or j["finished"] > q["finished__gt"])]
        js.sort(key=lambda j: -j["id"] if q.get("order_by") == "-id" else (j["finished"], j["id"]))
        size, page = int(q.get("page_size", 25)), int(q.get("page", 1))
        nxt = (base + "jobs/?" + urllib.parse.urlencode(dict(q, page=page + 1))) if len(js) > size * page else None
        return self._send(200, {"count": len(js), "next": nxt, "results": [
            {k: v for k, v in j.items() if k != "artifacts"} for j in js[size * (page - 1):size * page]]})


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
