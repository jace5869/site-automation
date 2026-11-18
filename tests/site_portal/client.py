#!/usr/bin/env python3
"""A browser with (or without) a card, for tests/site_portal/run_portal_test.sh: the portal through the stand-in nginx.
  client.py BASE KEY|none STEP [ARGS...]   -> one JSON line on stdout
KEY: the client certificate's files without .pem / .key. Steps:
  get PATH                       {code, location, body (first 600 characters), csp}
  token PATH                     the confirm page's token (meta portal-token)
  launch NAME JSON [ORIGIN]      the confirm page first (its token), then POST /portal/api/actions/NAME {"vars": JSON}
  launch_raw NAME JSON TOKEN ORIGIN   a POST with that token and Origin (CSRF checks)
  decide ID approve|deny         the approvals page's token, then POST
  wait ID [VIA]                  /portal/api/jobs/ID until done (90 s)
"""
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

base, key, step, args = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
ctx = ssl._create_unverified_context()             # the test server certificate
if key != "none":
    ctx.load_cert_chain(key + ".pem", key + ".key")
host = urllib.parse.urlsplit(base).netloc


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


op = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=ctx))


def call(path, data=None, headers=None):
    r = urllib.request.Request(base + path, data=data, headers=headers or {})
    try:
        resp = op.open(r, timeout=60)
    except urllib.error.HTTPError as e:
        resp = e
    except (urllib.error.URLError, ssl.SSLError, ConnectionError) as e:
        return 0, {}, str(e)
    with resp:
        return getattr(resp, "status", None) or resp.code, dict(resp.headers), resp.read().decode("utf-8", "replace")


def token(path):
    code, _, page = call(path)
    m = re.search(r'<meta name="portal-token" content="([^"]+)"', page)
    return m.group(1) if m else ""


def post(path, body, tok, origin):
    h = {"Content-Type": "application/json", "X-Portal-Token": tok}
    if origin:
        h["Origin"] = origin
    code, _, b = call(path, json.dumps(body).encode(), h)
    try:
        return {"code": code, "body": json.loads(b)}
    except ValueError:
        return {"code": code, "body": b[:300]}


if step == "get":
    code, h, b = call(args[0], headers={"X-SSL-Client-S-DN": "CN=FORGED.NAME.9999999999", "X-SSL-Client-Verify": "SUCCESS"})
    out = {"code": code, "location": h.get("Location", ""), "body": b[:12000], "csp": h.get("Content-Security-Policy", "")}
    if args[0].startswith("/portal/api/"):
        try:
            out["json"] = json.loads(b)
        except ValueError:
            pass
elif step == "token":
    out = {"token": token(args[0])}
elif step == "launch":
    vals = json.loads(args[1])
    tok = token("/portal/actions/%s?%s" % (args[0], urllib.parse.urlencode(vals)))
    out = post("/portal/api/actions/" + args[0], {"vars": vals}, tok, args[2] if len(args) > 2 else "https://" + host)
elif step == "launch_raw":
    out = post("/portal/api/actions/" + args[0], {"vars": json.loads(args[1])}, args[2], args[3])
elif step == "decide":
    tok = token("/portal/approvals")
    out = post("/portal/api/approvals/%s/%s" % (args[0], args[1]), {}, tok, "https://" + host)
elif step == "wait":
    via = ("?via=" + args[1]) if len(args) > 1 else ""
    end, out = time.time() + 90, {}
    while time.time() < end:
        code, _, b = call("/portal/api/jobs/%s%s" % (args[0], via))
        try:
            out = dict(json.loads(b), code=code)
        except ValueError:
            out = {"code": code, "body": b[:300]}
        if code != 200 or out.get("done"):
            break
        time.sleep(1)
else:
    out = {"error": "no step " + step}
print(json.dumps(out))
