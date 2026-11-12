#!/usr/bin/env python3
"""A fake node stats page: python3 statsmock.py PORT KIND [key=value ...] - KIND xml (/xmlstats.do), html (/stats.do;
/xmlstats.do 404), 403 (both refused). key=value fills the template's @KEY@ (MEMUSED, SEMQ, ...); SEMQ_SEQ=1,0 gives
the semaphore queue per read in turn (the last one kept). Like a real node's connector, lower case options:
tls=CERT.pem - HTTPS on the port, and a plain HTTP request answered 400 with an empty body (Tomcat's TLS connector);
host=NAME - only a request whose Host is NAME is answered, others 400 (a node that wants its own name);
auth=USER:PASSWORD - basic auth, else 401; bind=ADDRESS - listens there, not on 127.0.0.1 (another address of this
host: what a load balancer's address would be); ui=USER:PASSWORD - without that login the instance's normal web page
(HTTP 200, no login form on it), with it the stats; ui=never - that page whatever is sent;
form=USER:PASSWORD - an SSO instance with a local login page: the stats pages without a session answer 302 to
/logout_redirect.do; GET /side_door.do gives a login form with a hidden sysparm_ck (another one on every GET, good for
one POST, tied to the cookie it came with); POST /login.do with them answers 302 to /login_redirect.do and the session's
cookies, anything wrong 302 back to /login.do; GET /logout.do ends the session. formmode= sso (side_door.do goes to the
SSO page too: no login form), other (the POST answers a page: one more step is asked), norole (logged in, the stats
pages still refused), nologout (logout.do answers 500); log=FILE - a line per request (never the password)."""
import base64
import os
import secrets
import socket
import ssl
import sys
from urllib.parse import parse_qs
from http.server import BaseHTTPRequestHandler
try:
    from http.server import ThreadingHTTPServer
except ImportError:                        # Python 3.6 (the node tests are run there too: a RHEL 8 node's Python)
    from http.server import HTTPServer
    from socketserver import ThreadingMixIn

    class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

here = os.path.dirname(os.path.abspath(__file__))
port, kind = int(sys.argv[1]), sys.argv[2]
vals = {"MEMUSED": "1200.0", "ACTIVE": "3", "SEMAVAIL": "14", "SEMUSE": "2", "SEMQ": "0", "SCHEDQ": "1"}
opts = {}
for kv in sys.argv[3:]:
    k, v = kv.split("=", 1)
    if k in ("tls", "host", "auth", "bind", "ui", "form", "formmode", "log"):
        opts[k] = v
    else:
        vals[k.upper()] = v
seq = [x for x in vals.pop("SEMQ_SEQ", "").split(",") if x]
reads = [0]
ctx = None
if opts.get("tls"):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(opts["tls"])


cks, sessions = {}, set()                  # the pre-login cookie -> its sysparm_ck; the sessions logged in


def note(line):
    if opts.get("log"):
        with open(opts["log"], "a") as f:
            f.write(line + "\n")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def cookies(self):
        return dict(c.strip().split("=", 1) for c in (self.headers.get("Cookie") or "").split(";") if "=" in c)

    def here(self):
        return "%s://%s" % ("https" if ctx is not None else "http", self.headers.get("Host") or "127.0.0.1:%d" % port)

    def page(self, text):
        body = text.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def refused(self):
        if ctx is not None and not isinstance(self.connection, ssl.SSLSocket):
            self.bare(400)                # plain HTTP to a TLS connector
            return True
        if opts.get("host") and (self.headers.get("Host") or "").split(":")[0] != opts["host"]:
            self.bare(400)
            return True
        return False

    def form_get(self, path):
        """The local login page's part of a GET -> True when it was answered here."""
        mode, jar = opts.get("formmode", ""), self.cookies()
        if path == "/side_door.do":
            note("GET /side_door.do")
            if mode == "sso":
                self.bare(302, {"Location": self.here() + "/login_with_sso.do"})
                return True
            pre, ck = "pre" + secrets.token_hex(8), secrets.token_hex(36)
            cks[pre] = ck
            body = ('<html><head><title>Log in | ServiceNow</title></head><body><form id="loginPage" action="login.do" '
                    'method="post"><input type="hidden" name="sysparm_ck" id="sysparm_ck" value="%s"></input>'
                    '<input name="user_name" id="user_name" type="text"/><input name="user_password" id="user_password" '
                    'type="password"/></form></body></html>' % ck).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Set-Cookie", "JSESSIONID=%s; Path=/; HttpOnly; Secure" % pre)
            self.send_header("Set-Cookie", "glide_user_route=glide.%s; Path=/; HttpOnly" % secrets.token_hex(4))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return True
        if path == "/logout.do":
            known = jar.get("JSESSIONID") in sessions
            note("GET /logout.do session=%s" % ("yes" if known else "no"))
            if mode == "nologout":
                self.bare(500)
                return True
            sessions.discard(jar.get("JSESSIONID"))
            self.bare(302, {"Location": self.here() + "/logout_redirect.do?sysparm_url=navpage.do&sysparm_token=t0ken-in-a-query"})
            return True
        ok = jar.get("JSESSIONID") in sessions and jar.get("glide_session_store") and mode != "norole"
        note("GET %s session=%s" % (path, "yes" if jar.get("JSESSIONID") in sessions else "no"))
        if not ok:
            self.bare(302, {"Location": self.here() + "/logout_redirect.do?sysparm_url=navpage.do&sysparm_token=t0ken-in-a-query"})
            return True
        return False

    def do_POST(self):
        path = self.path.split("?")[0]
        if self.refused():
            return
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()).items()}
        if not opts.get("form") or path != "/login.do":
            self.bare(404)
            return
        pre = self.cookies().get("JSESSIONID")
        ck_ok = bool(pre) and cks.pop(pre, None) == form.get("sysparm_ck")
        good = (ck_ok and form.get("sys_action") == "sysverb_login"
                and "%s:%s" % (form.get("user_name"), form.get("user_password")) == opts["form"])
        note("POST /login.do user=%s ck=%s -> %s" % (form.get("user_name"), "ok" if ck_ok else "bad", "in" if good else "refused"))
        if good and opts.get("formmode") == "other":
            self.page("<html><head><title>ServiceNow</title></head><body>Enter the 6 digit code</body></html>")
            return
        if not good:
            self.bare(302, {"Location": self.here() + "/login.do"})
            return
        sid = "sess" + secrets.token_hex(8)
        sessions.add(sid)
        self.send_response(302)
        self.send_header("Location", self.here() + "/login_redirect.do?sysparm_stack=no&sysparm_token=t0ken-in-a-query")
        self.send_header("Set-Cookie", "JSESSIONID=%s; Path=/; HttpOnly; Secure" % sid)
        self.send_header("Set-Cookie", "glide_session_store=%s; Path=/; HttpOnly" % secrets.token_hex(16))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def bare(self, code, extra=None):
        self.send_response(code)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if self.refused():
            return
        if opts.get("form") and self.form_get(path):
            return
        if opts.get("auth") and self.headers.get("Authorization") != "Basic " + base64.b64encode(opts["auth"].encode()).decode():
            self.bare(401, {"WWW-Authenticate": 'Basic realm="ServiceNow"'})
            return
        if opts.get("ui") and self.headers.get("Authorization") != "Basic " + base64.b64encode(opts["ui"].encode()).decode():
            page = (b'<!DOCTYPE html><html lang="en" class=" ltr " data-doctype="true" dir="ltr">'
                    b"<head><title>ServiceNow</title></head><body><div id=\"sso\"></div></body></html>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
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


Server((opts.get("bind") or "127.0.0.1", port), H).serve_forever()
