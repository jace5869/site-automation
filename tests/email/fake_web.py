#!/usr/bin/env python3
"""Fake report website for tests/email/run_web_test.sh: GET, PUT, DELETE and MKCOL (WebDAV) into a folder, like
nginx with dav_methods PUT DELETE MKCOL. A PUT into a folder that is not there answers 409 (make it with MKCOL first).
Login: FAKE_WEB_TOKEN (Authorization: Bearer ...) or FAKE_WEB_BASIC (user:password); neither = no login.
Each request appended to FOLDER/../web.log as "METHOD path status".
Usage: fake_web.py PORT FOLDER"""
import base64
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import unquote

ROOT = os.path.abspath(sys.argv[2])
LOG = os.path.join(os.path.dirname(ROOT), "web.log")
TOKEN = os.environ.get("FAKE_WEB_TOKEN", "")
BASIC = os.environ.get("FAKE_WEB_BASIC", "")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _answer(self, code, body=b""):
        with open(LOG, "a") as f:
            f.write("%s %s %d\n" % (self.command, self.path, code))
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _path(self):
        rel = unquote(self.path.split("?")[0]).lstrip("/")
        path = os.path.abspath(os.path.join(ROOT, rel))
        return path if path == ROOT or path.startswith(ROOT + os.sep) else None

    def _allowed(self):
        auth = self.headers.get("Authorization", "")
        if TOKEN:
            return auth == "Bearer " + TOKEN
        if BASIC:
            return auth == "Basic " + base64.b64encode(BASIC.encode()).decode()
        return True

    def do_GET(self):
        path = self._path()
        if path is None or not os.path.isfile(path):
            return self._answer(404)
        with open(path, "rb") as f:
            self._answer(200, f.read())

    def do_PUT(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if not self._allowed():
            return self._answer(401)
        path = self._path()
        if path is None:
            return self._answer(403)
        if not os.path.isdir(os.path.dirname(path)):
            return self._answer(409)
        with open(path, "wb") as f:
            f.write(body)
        self._answer(201)

    def do_DELETE(self):
        if not self._allowed():
            return self._answer(401)
        path = self._path()
        if path is None or path == ROOT:
            return self._answer(403)
        if not os.path.isfile(path):
            return self._answer(404)
        os.remove(path)
        self._answer(204)

    def do_MKCOL(self):
        if not self._allowed():
            return self._answer(401)
        path = self._path()
        if path is None:
            return self._answer(403)
        if os.path.isdir(path):
            return self._answer(405)
        if not os.path.isdir(os.path.dirname(path.rstrip(os.sep))):
            return self._answer(409)
        os.mkdir(path)
        self._answer(201)


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
