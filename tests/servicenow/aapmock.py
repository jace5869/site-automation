#!/usr/bin/env python3
"""Fake AAP (automation controller) API for playbooks/vm_deploy_poll.yml: one workflow job template,
"VM - deploy from ServiceNow ticket (workflow)" (id 42), found by name and started with extra_vars.

Login: a token (Authorization: Bearer goodtoken) or basic auth admin / goodpw.
API path: /api/controller/v2/ (AAP 2.5 and later); AAPMOCK_LEGACY=1: only /api/v2/ (older AAP).
AAPMOCK_NO_SURVEY=1: the workflow has no survey and no Prompt on launch (it takes no variables).
AAPMOCK_LAUNCH_FAIL=1: starting it answers 400 (e.g. an inventory or credential it needs is gone).
Usage: aapmock.py PORT   (each launch appended to $AAPMOCK_LOG as a JSON line)
"""
import base64
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WF = {"id": 42, "name": "VM - deploy from ServiceNow ticket (workflow)"}
LOG = os.environ.get("AAPMOCK_LOG", "aapmock.jsonl")
JOBS = [1000]


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

    def _authed(self):
        a = self.headers.get("Authorization", "")
        if a == "Bearer goodtoken":
            return True
        if a.startswith("Basic "):
            return base64.b64decode(a[6:]).decode() == "admin:goodpw"
        return False

    def _handle(self, method):
        n = int(self.headers.get("Content-Length", "0") or 0)
        body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        url = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(url.query))
        base = "/api/v2/" if os.environ.get("AAPMOCK_LEGACY") == "1" else "/api/controller/v2/"
        if not url.path.startswith(base):
            return self._send(404, {"detail": "Not found."})
        if not self._authed():
            return self._send(401, {"detail": "Authentication credentials were not provided."})
        rest = url.path[len(base):].strip("/").split("/")
        no_survey = os.environ.get("AAPMOCK_NO_SURVEY") == "1"
        if rest == ["workflow_job_templates"] and method == "GET":
            found = [WF] if q.get("name") == WF["name"] else []
            return self._send(200, {"count": len(found), "results": found})
        if rest == ["workflow_job_templates", str(WF["id"]), "launch"]:
            if method == "GET":
                return self._send(200, {"survey_enabled": not no_survey, "ask_variables_on_launch": False,
                                        "variables_needed_to_start": [] if no_survey else ["vm_deploy_ticket"],
                                        "can_start_without_user_input": False})
            if os.environ.get("AAPMOCK_LAUNCH_FAIL") == "1":
                return self._send(400, {"resources_needed_to_start": ["Job Template 'VM - deploy' is missing or undefined."]})
            JOBS[0] += 1
            ev = body.get("extra_vars") or {}
            with open(LOG, "a") as f:
                f.write(json.dumps({"workflow": WF["id"], "extra_vars": ev, "job": JOBS[0], "api": base}) + "\n")
            out = {"workflow_job": JOBS[0], "id": JOBS[0], "url": "%sworkflow_jobs/%d/" % (base, JOBS[0])}
            if no_survey:
                out["ignored_fields"] = {"extra_vars": ev}
            return self._send(201, out)
        return self._send(404, {"detail": "Not found."})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
