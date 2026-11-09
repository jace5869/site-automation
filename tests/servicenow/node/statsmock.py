#!/usr/bin/env python3
"""A fake node stats page: python3 statsmock.py PORT KIND [key=value ...] - KIND xml (/xmlstats.do), html (/stats.do;
/xmlstats.do 404), 403 (both refused). key=value fills the template's @KEY@ (MEMUSED, SEMQ, ...); SEMQ_SEQ=1,0 gives
the semaphore queue per read in turn (the last one kept). Like a real node's connector, lower case options:
tls=CERT.pem - HTTPS on the port, and a plain HTTP request answered 400 with an empty body (Tomcat's TLS connector);
host=NAME - only a request whose Host is NAME is answered, others 400 (a node that wants its own name);
auth=USER:PASSWORD - basic auth, else 401."""
import base64
import os
import socket
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

here = os.path.dirname(os.path.abspath(__file__))
port, kind = int(sys.argv[1]), sys.argv[2]
vals = {"MEMUSED": "1200.0", "ACTIVE": "3", "SEMAVAIL": "14", "SEMUSE": "2", "SEMQ": "0", "SCHEDQ": "1"}
opts = {}
for kv in sys.argv[3:]:
    k, v = kv.split("=", 1)
    if k in ("tls", "host", "auth"):
        opts[k] = v
    else:
        vals[k.upper()] = v
seq = [x for x in vals.pop("SEMQ_SEQ", "").split(",") if x]
reads = [0]
ctx = None
if opts.get("tls"):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(opts["tls"])


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def bare(self, code, extra=None):
        self.send_response(code)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if ctx is not None and not isinstance(self.connection, ssl.SSLSocket):
            self.bare(400)                # plain HTTP to a TLS connector
            return
        if opts.get("host") and (self.headers.get("Host") or "").split(":")[0] != opts["host"]:
            self.bare(400)
            return
        if opts.get("auth") and self.headers.get("Authorization") != "Basic " + base64.b64encode(opts["auth"].encode()).decode():
            self.bare(401, {"WWW-Authenticate": 'Basic realm="ServiceNow"'})
            return
        if kind == "403":
            self.send_response(403)
            self.end_headers()
            return
        want = {"xml": "/xmlstats.do", "html": "/stats.do"}[kind]
        if path != want:
            self.send_response(404)
            self.end_headers()
            return
        v = dict(vals)
        if seq:
            v["SEMQ"] = seq[min(reads[0], len(seq) - 1)]
        reads[0] += 1
        body = open(os.path.join(here, "xmlstats.xml" if kind == "xml" else "stats.html")).read()
        for k, x in v.items():
            body = body.replace("@%s@" % k, x)
        self.send_response(200)
        self.send_header("Content-Type", "text/xml" if kind == "xml" else "text/html")
        self.end_headers()
        self.wfile.write(body.encode())


class Server(ThreadingHTTPServer):
    def get_request(self):
        sock, addr = self.socket.accept()
        if ctx is not None:
            sock.settimeout(5)
            try:
                first = sock.recv(1, socket.MSG_PEEK)
            except OSError:
                first = b""
            if first == b"\x16":            # a TLS handshake: HTTPS; anything else is answered as plain HTTP
                sock = ctx.wrap_socket(sock, server_side=True)
        return sock, addr


Server(("127.0.0.1", port), H).serve_forever()
