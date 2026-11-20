#!/usr/bin/env python3
"""1.85.0: the site portal's live Network map on the stand-in AAP (tests/site_portal/fake_aap.py) and a fake SolarWinds
(tests/netmap/fake_swis.py, started by the caller), behind a front that adds the card's headers (nginx's job) - for the
screenshots (tests/test_netmap.py starts the same pieces itself). Test data only; SWIS_URL is https.
  python tests/site_portal/netmap_demo.py PORT SWIS_URL [SECONDS]     prints READY when it listens
SECONDS: how often the portal reads SolarWinds (the service's own floor is 30 s; a test asks sooner)."""
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

wt = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
port, swis = int(sys.argv[1]), sys.argv[2]
seconds = int(sys.argv[3]) if len(sys.argv) > 3 else 30
sys.path[:0] = [os.path.join(wt, "plugins", "filter"), os.path.join(wt, "plugins", "module_utils"), os.path.join(wt, "roles/site_portal_stack/files"),
                os.path.join(wt, "tests", "site_portal")]
import yaml  # noqa: E402
import site_portal as sp  # noqa: E402
import fake_aap  # noqa: E402

JDOE = "CN=DOE.JANE.Q.1234567890,OU=CONTRACTOR,OU=PKI,O=U.S. Government,C=US"
tmp = tempfile.mkdtemp()
fake_aap.LOG = os.path.join(tmp, "aap.jsonl")
aap = fake_aap.serve(0, stop=True)
with open(os.path.join(wt, "roles/site_portal_stack/files/actions.yml")) as fh:
    doc = yaml.safe_load(fh)
with open(os.path.join(tmp, "actions.json"), "w") as fh:
    json.dump({"actions": doc["actions"]}, fh)
static = os.path.join(tmp, "static")          # as the deploy lays it out: the portal's files and the map's
shutil.copytree(os.path.join(wt, "roles/site_portal_stack/files/static"), static)
for n in os.listdir(os.path.join(wt, "roles/netmap_report/files/web")):
    shutil.copy(os.path.join(wt, "roles/netmap_report/files/web", n), static)
os.makedirs(os.path.join(tmp, "reports"))
sites = {"site f": {"lat": 2.4, "lon": 102.3}, "Site G": {"lat": -31.8, "lon": 147.6}, "SITE H": {"lat": -22.6, "lon": -47.8}}
cfg = sp.Config({"PORTAL_DATA": os.path.join(tmp, "data"), "PORTAL_REPORTS": os.path.join(tmp, "reports"), "PORTAL_ACTIONS": os.path.join(tmp, "actions.json"),
                 "PORTAL_AAP_URL": "http://127.0.0.1:%d" % aap.server_address[1], "PORTAL_AAP_TOKEN": "fake-token", "PORTAL_TIME_ZONE": "UTC",
                 "PORTAL_THEME": os.environ.get("PORTAL_DEMO_LOOK", "atlas"), "PORTAL_TITLE": "Site automation", "PORTAL_BANNER": {"on": False},
                 "PORTAL_MOTION": os.environ.get("PORTAL_DEMO_MOTION", "auto"),
                 "PORTAL_NETMAP": os.environ.get("PORTAL_DEMO_NETMAP", "1"), "PORTAL_SWIS_URL": swis, "PORTAL_SWIS_VERIFY": "0",
                 "PORTAL_SWIS_LOGIN": json.dumps({"user": "svc_swis_ro", "password": os.environ.get("PORTAL_DEMO_SWIS_PASSWORD", 'ro&"<test>9')}),
                 "PORTAL_NETMAP_OPTS": {"site_by": "Location", "lat_property": "Lat", "lon_property": "Lon", "properties": ["Region"],
                                        "sites": sites, "view": os.environ.get("PORTAL_DEMO_VIEW", "globe"), "warn_util": 90}})
cfg.netmap_seconds = seconds                  # a test's pace (the service's floor is 30 s)
real = sys.stdout
sys.stdout = open(os.devnull, "w")
portal = sp.Portal(cfg)
portal.aap.connect()
portal.aap.resolve(portal.actions)
srv = sp.PortalServer(("127.0.0.1", 0), sp.make_handler(cfg, portal, static))
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % srv.server_address[1]


class Front(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path
        if path == "/__log":                   # the portal's own log (what it said about SolarWinds)
            body = open(os.path.join(tmp, "data", "portal-log.jsonl"), "rb").read() if os.path.exists(os.path.join(tmp, "data", "portal-log.jsonl")) else b""
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        h = {"X-SSL-Client-Verify": "SUCCESS", "X-SSL-Client-S-DN": JDOE, "Host": "portal.example.mil"}
        for k in ("If-None-Match", "Accept"):
            if self.headers.get(k):
                h[k] = self.headers[k]
        sub = path[len("/portal"):] if path.startswith("/portal") else path
        try:
            r = urllib.request.urlopen(urllib.request.Request(base + sub, headers=h), timeout=30)
            code, hd, body = r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            code, hd, body = e.code, e.headers, e.read()
        self.send_response(code)
        for k in ("Content-Type", "Content-Security-Policy", "ETag", "Cache-Control", "X-Content-Type-Options"):
            if hd.get(k):
                # PORTAL_DEMO_TT=1: the portal's own policy plus Trusted Types (no HTML from strings in the map's files)
                v = hd[k] + ("; require-trusted-types-for 'script'" if k == "Content-Security-Policy" and os.environ.get("PORTAL_DEMO_TT") else "")
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


fr = ThreadingHTTPServer(("127.0.0.1", port), Front)
real.write("READY\n")
real.flush()
fr.serve_forever()
