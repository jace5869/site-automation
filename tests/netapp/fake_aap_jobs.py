#!/usr/bin/env python3
"""A fake AAP (automation controller) API for the capacity trends report's history (site_ontap_capacity_history;
tests/test_ontap_capacity.py, tests/netapp/run_capacity_test.sh): a job template's jobs with their artifacts. HTTP,
standard library only.

  fake_aap_jobs.py PORT JOBS.json LOG        (JOBS.json: tests/netapp/capacity_fixture.py jobs ...)

Login: Authorization: Bearer goodtoken (the user "auditor"). Like AAP: api/controller/v2/me/, jobs/?job_template=&id__lt=
&status__in=&order_by=-id&page_size=&page= (pages with "next"; the list leaves the artifacts out), jobs/ID/ (with them).
Every request is written to LOG; any method but GET is HTTP 405.
FAKE_AAP_FORBID=1: the jobs list answers 403. FAKE_AAP_HIDE=1: the list is empty and a job's page 404 (a user with no role
on the template). FAKE_AAP_CUT=ID: that job's page is cut off once (a large answer), then whole."""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BASE = "/api/controller/v2/"


def handler(jobs, log, env=None):
    env = os.environ if env is None else env
    cut = set(int(x) for x in str(env.get("FAKE_AAP_CUT", "")).split(",") if x.strip())

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, code, obj, short=False):
            out = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out[:len(out) // 2] if short else out)

        def refuse(self):
            with open(log, "a") as fh:
                fh.write("%s %s\n" % (self.command, self.path))
            self.reply(405, {"detail": "Method not allowed."})

        do_POST = do_PATCH = do_PUT = do_DELETE = refuse

        def do_GET(self):
            with open(log, "a") as fh:
                fh.write("GET %s\n" % self.path)
            if self.headers.get("Authorization") != "Bearer goodtoken":
                return self.reply(401, {"detail": "Authentication credentials were not provided."})
            u = urlparse(self.path)
            if not u.path.startswith(BASE):
                return self.reply(404, {"detail": "Not found."})
            path, q = u.path[len(BASE):], dict((k, v[0]) for k, v in parse_qs(u.query).items())
            if path == "me/":
                return self.reply(200, {"count": 1, "results": [{"id": 5, "username": "auditor"}]})
            if path == "jobs/":
                if env.get("FAKE_AAP_FORBID"):
                    return self.reply(403, {"detail": "You do not have permission to perform this action."})
                rows = [] if env.get("FAKE_AAP_HIDE") else [j for j in jobs if (
                    str(j["job_template"]) == q.get("job_template", str(j["job_template"]))
                    and j["id"] < int(q.get("id__lt", 10 ** 12))
                    and ("status__in" not in q or j["status"] in q["status__in"].split(",")))]
                rows.sort(key=lambda j: j["id"], reverse=q.get("order_by") == "-id")
                size, page = int(q.get("page_size", 25)), int(q.get("page", 1))
                part = rows[(page - 1) * size:page * size]
                if page > 1 and not part:
                    return self.reply(404, {"detail": "Invalid page."})
                return self.reply(200, {"count": len(rows), "next": "page=%d" % (page + 1) if page * size < len(rows) else None,
                                        "results": [dict((k, v) for k, v in j.items() if k != "artifacts") for j in part]})
            if path.startswith("jobs/") and path[5:].strip("/").isdigit():
                i = int(path[5:].strip("/"))
                j = next((j for j in jobs if j["id"] == i), None)
                if j is None or (env.get("FAKE_AAP_HIDE")):
                    return self.reply(404, {"detail": "Not found."})
                if i in cut:
                    cut.discard(i)
                    return self.reply(200, j, short=True)
                return self.reply(200, j)
            return self.reply(404, {"detail": "Not found."})
    return H


def serve(port, jobs, log, env=None):
    return ThreadingHTTPServer(("127.0.0.1", port), handler(jobs, log, env))


if __name__ == "__main__":
    serve(int(sys.argv[1]), json.load(open(sys.argv[2])), sys.argv[3]).serve_forever()
