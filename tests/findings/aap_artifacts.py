#!/usr/bin/env python3
"""A stand-in AAP for tests/disk/run_disk_report_test.sh (1.67.0 part C): GET /api/controller/v2/me/ and
/api/controller/v2/jobs/<id>/ - one job, whose artifacts are the JSON file given (a report job's custom stats, as AAP
keeps them). Bearer token: made-up-token.  aap_artifacts.py PORT ARTIFACTS.json JOB_ID"""
import http.server
import json
import sys

port, art_file, job = int(sys.argv[1]), sys.argv[2], int(sys.argv[3])


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def answer(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer made-up-token":
            return self.answer(401, {"detail": "Authentication credentials were not provided."})
        if self.path == "/api/controller/v2/me/":
            return self.answer(200, {"results": [{"username": "reader"}]})
        if self.path == "/api/controller/v2/jobs/%d/" % job:
            with open(art_file) as fh:
                return self.answer(200, {"id": job, "name": "Disk space report", "status": "successful",
                                         "finished": "2026-11-16T06:00:00Z", "artifacts": json.load(fh)})
        return self.answer(404, {"detail": "Not found."})


http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
