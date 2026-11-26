#!/usr/bin/env python3
"""1.135.0: one portal with every live page filled with placeholder data, for the looks' screenshots and contact sheet: the
actions and their jobs and approvals (the stand-in AAP, tests/site_portal/fake_aap.py), the Reports index, the Ops center
wall and the Actions page's Ops center (tests/wall/demo.py's collector and NetApp run), the NetApp page (the same run), the
Active Directory page (tests/site_portal/ad_demo.py's report database), the live Network map and its history (the fake
SolarWinds of tests/netmap) and the Admin page (the viewer is a portal admin). Test data only.
  python tests/site_portal/looks_demo.py PORT          prints READY {ids} when it listens
A front adds the card's headers (nginx's job); ?__who=appr is the approver, ?__theme=light|dark the colours; ?look= is the
page's own (the look)."""
import datetime
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

wt = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
port = int(sys.argv[1])
sys.path[:0] = [os.path.join(wt, "plugins", "filter"), os.path.join(wt, "plugins", "module_utils"), os.path.join(wt, "roles/site_portal_stack/files"),
                os.path.join(wt, "roles", "ad_report", "library"), os.path.join(wt, "tests"), os.path.join(wt, "tests", "site_portal"),
                os.path.join(wt, "tests", "wall"), os.path.join(wt, "tests", "netapp")]
import yaml  # noqa: E402
import site_report_db  # noqa: E402
import ansible.module_utils  # noqa: E402
sys.modules.setdefault("ansible.module_utils.site_report_db", site_report_db)
ansible.module_utils.site_report_db = site_report_db
import site_portal as sp  # noqa: E402
import netapp_live as nl  # noqa: E402
import site_ad_db as adb  # noqa: E402
import ad_db_filters as dbf  # noqa: E402
import fake_aap  # noqa: E402
import demo as wall_demo  # noqa: E402  (tests/wall/demo.py)
from test_portal_ad import Lite  # noqa: E402
from test_netmap import Fake, PW  # noqa: E402

JDOE = "CN=DOE.JANE.Q.1234567890,OU=CONTRACTOR,OU=PKI,O=U.S. Government,C=US"
APPR = "CN=SMITH.JOHN.A.1234567891,OU=USA,OU=PKI,O=U.S. Government,C=US"
tmp = tempfile.mkdtemp(prefix="looks-demo-")
real = sys.stdout

# ---- the stand-in AAP: the real actions, a few found templates, runs and approvals (as portal_demo.py)
fake_aap.RUN = 600
os.environ["FAKE_AAP_ALL_TEMPLATES"] = "1"           # the Admin page lists every template the account can see
fake_aap.LOG = os.path.join(tmp, "aap.jsonl")
T = fake_aap.STATE["templates"]
for n, wf, a in (("STIG - recheck one host", 1, 0), ("Disk - clean up (approved)", 1, 1), ("Service - restart", 1, 1), ("Disk - space request", 0, 0),
                 ("Site portal - open ticket", 0, 0), ("STIG - recheck and update STIG Manager", 1, 1), ("STIG - fix one rule", 1, 1),
                 ("STIG - create the asset in STIG Manager", 1, 1)):
    T["workflow_job_templates" if wf else "job_templates"].append({"id": fake_aap.new_id(), "name": n, "ask_variables_on_launch": True,
                                                                  "ask_limit_on_launch": True, "survey_enabled": False, "approval": a})
aap = fake_aap.serve(0, stop=True)
fake_aap.seed_found()
fake_aap.seed_steps()
fake_aap.seed_reports()
with open(os.path.join(wt, "roles/site_portal_stack/files/actions.yml")) as fh:
    doc = yaml.safe_load(fh)
with open(os.path.join(tmp, "actions.json"), "w") as fh:
    json.dump({"actions": doc["actions"]}, fh)

# ---- the report website's index: a few runs
reports = os.path.join(tmp, "reports")
NOW = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, second=0, microsecond=0)
NOW = NOW - datetime.timedelta(minutes=NOW.minute % 30)
idx = []
for key, title, job, mins in (("ad-health", "Active Directory health", 9735, 25), ("health", "Linux health check", 9001, 70), ("netapp", "NetApp ONTAP health", 9002, 130),
                              ("disk", "Disk report", 907, 300), ("certs", "Certificate inventory", 8801, 1400)):
    os.makedirs(os.path.join(reports, key), exist_ok=True)
    page = "%s/%d.html" % (key, job)
    with open(os.path.join(reports, page), "w") as fh:
        fh.write("<!doctype html><title>%s</title><h1>%s</h1><p>Test data only.</p>" % (title, title))
    shutil.copy(os.path.join(reports, page), os.path.join(reports, key, "latest.html"))
    idx.append({"key": key + "/latest.html", "latest": key + "/latest.html", "page": page, "title": title,
                "when": (NOW - datetime.timedelta(minutes=mins)).strftime("%Y-%m-%d %H:%M UTC"), "job": str(job)})
idx[0]["live"] = "ad"
with open(os.path.join(reports, "index.json"), "w") as fh:
    json.dump({"reports": idx}, fh)

# ---- the static files as the deploy lays them out (the portal's, the map's, the AD map's)
static = os.path.join(tmp, "static")
shutil.copytree(os.path.join(wt, "roles/site_portal_stack/files/static"), static)
for role in ("netmap_report", "ad_report"):
    web = os.path.join(wt, "roles", role, "files", "web")
    for n in os.listdir(web):
        if os.path.isfile(os.path.join(web, n)):
            shutil.copy(os.path.join(web, n), static)

# ---- the fake SolarWinds (the live map) - an HTTPS process with its own certificate
swis = Fake(tmp)
sites = {"site f": {"lat": 2.4, "lon": 102.3}, "Site G": {"lat": -31.8, "lon": 147.6}, "SITE H": {"lat": -22.6, "lon": -47.8}}

cfg = sp.Config({
    "PORTAL_DATA": os.path.join(tmp, "data"), "PORTAL_REPORTS": reports, "PORTAL_ACTIONS": os.path.join(tmp, "actions.json"),
    "PORTAL_AAP_URL": "http://127.0.0.1:%d" % aap.server_address[1], "PORTAL_AAP_TOKEN": "fake-token", "PORTAL_INVENTORY": "Site hosts",
    "PORTAL_APPROVERS": json.dumps(["SMITH.JOHN.A.1234567891"]), "PORTAL_ADMINS": json.dumps(["DOE.JANE.Q.1234567890", "SMITH.JOHN.A.1234567891"]),
    "PORTAL_TIME_ZONE": "Europe/Berlin", "PORTAL_THEME": "atlas", "PORTAL_TITLE": "Site portal", "PORTAL_BANNER": {"on": False},
    "PORTAL_FINDING_PLAYBOOKS": '["health_check.yml", "disk_report.yml"]',
    "PORTAL_WALL_OPTS": json.dumps(wall_demo.OPTS), "PORTAL_WALL_TITLE": "Ops center", "PORTAL_WALL_ENCLAVE": "SITEA-UNCLASS",
    "PORTAL_NETMAP": "1", "PORTAL_SWIS_URL": swis.url, "PORTAL_SWIS_VERIFY": "0",
    "PORTAL_SWIS_LOGIN": json.dumps({"user": "svc_swis_ro", "password": PW}),
    "PORTAL_NETMAP_OPTS": {"site_by": "Location", "lat_property": "Lat", "lon_property": "Lon", "properties": ["Region"], "sites": sites,
                           "view": "region", "warn_util": 90}})
cfg.netmap_seconds = 30

# ---- the NetApp page and the wall: the wall demo's NetApp run (SQLite) and collector
DB = wall_demo.netapp_db(11)
_na_init = sp.NetappLive.__init__


def na_init(self, c, log, portal=None, db=None):
    _na_init(self, c, log, portal, db=nl.Database(lambda: DB, mark="?"))


sp.NetappLive.__init__ = na_init
cfg.netapp = True

# ---- the Active Directory page: 7 days of the AD job's runs every 30 min (tests/site_portal/ad_demo.py's data)
DCS = [("DC01", "SITA0101"), ("DC02", "SITA0101"), ("DC03", "SITA0101"), ("DC04", "SITA0101"), ("DC05", "SITB0201"), ("DC06", "SITC0301")]


def state_of(dc, k):
    if dc == "DC03" and k < 6:
        return "critical", "dcdiag: Replications failed (8524, DNS lookup failure)"
    if dc == "DC05" and k in (1, 2, 9, 10, 11, 30):
        return "warning", "Time: 2.4 s off the PDC emulator"
    if dc == "DC06" and k in (20, 21):
        return None, "WinRM did not answer"
    return "ok", ""


lite = Lite()
for k in range(335, -1, -1):
    t = NOW - datetime.timedelta(minutes=30 * k)
    iso = lambda h, t=t: (t - datetime.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")   # noqa: E731
    probs, dcs = [], []
    for dc, _site in DCS:
        st, text = state_of(dc, k)
        dcs.append({"dc": dc, "domain": "corp.test", "read": st is not None, "error": text if st is None else ""})
        if st not in (None, "ok"):
            probs.append({"domain": "corp.test", "object": dc, "severity": st, "text": text})
    crit = sum(1 for p in probs if p["severity"] == "critical") + sum(1 for d in dcs if not d["read"])
    ad = {"report": {"status": "critical" if crit else ("warning" if probs else "ok")}, "critical": crit, "warning": len(probs) - crit,
          "problems": probs, "dcs": dcs,
          "repl": {"now": iso(0), "dcs": [{"name": dc, "site": s} for dc, s in DCS],
                   "links": [{"frm": "DC01", "to": "DC06", "nc": "corp.test", "last_success": iso(31.5) if k < 63 else iso(0.2),
                              "failures": 3 if k < 63 else 0, "error": "8524 (DNS lookup failure)" if k < 63 else "", "last_failure": iso(0.1)},
                             {"frm": "DC02", "to": "DC06", "nc": "Configuration", "last_success": iso(9.2), "failures": 0},
                             {"frm": "DC05", "to": "DC02", "nc": "DomainDnsZones", "last_success": iso(4.6), "failures": 0},
                             {"frm": "DC02", "to": "DC01", "nc": "corp.test", "last_success": iso(0.1), "failures": 0}]}}
    live = {"kind": "ad", "dcs": [{"dc": dc, "ip": ""} for dc, _s in DCS], "problems": probs, "opts": {"cpu_warn": 90, "mem_warn": 90, "days": 30}}
    adb.write(lite, t, 9400 + (335 - k), 12, dbf.ad_db_payload(ad, live, {}))
lite.commit()


def ad_swis(live):
    def d(name):
        down = name == "DC04"
        return {"dc": name, "domain": "corp.test", "ip": "", "matched": "name", "status": "Down" if down else "Up", "status_text": "",
                "cpu": None if down else 14, "mem": None if down else 41, "alerts": [], "apps": {}, "last_down": None}
    return {"ok": True, "source": "swis-live", "read_at": NOW.strftime("%Y-%m-%d %H:%M"), "age_min": 0, "why": [], "sam": False,
            "sam_why": "SAM's applications are not on this Orion", "events": False, "dcs": [d(x["dc"]) for x in live["dcs"]]}


_ad_init = sp.AdLive.__init__


def ad_init(self, c, log, aap=None, source=None, db=None):
    _ad_init(self, c, log, aap, source=ad_swis, db=sp.AdDb(c, connect=lambda: lite, mark="?"))
    self.sched = {"12": (1e12, {"next": (NOW + datetime.timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")})}


sp.AdLive.__init__ = ad_init
cfg.ad = True

# ---- the map's history: a week of reads of one WAN link that went down and came back (tests/test_netmap_history.py's fixture)
from test_netmap_history import fixture as nh_fixture  # noqa: E402
_nh_init = sp.NetmapHistoryLive.__init__
_NH = "UUUUUWWUUUUDDDUUUUUHHUUUUUUUUWUUUUUUUUUUUU" * 6


class _History(object):
    def read(self, now):
        return nh_fixture(_NH, base=int(now) - (len(_NH) - 1) * 1800 - 120, age=120)      # the newest read 2 min ago


def nh_init(self, c, db=None):
    _nh_init(self, c, _History())


sp.NetmapHistoryLive.__init__ = nh_init

sys.stdout = open(os.devnull, "w")
portal = sp.Portal(cfg)
portal.aap.connect()
portal.aap.resolve(portal.actions)
portal.refresh(force=True)
wall = sp.WallLive(cfg, portal.log, sp.NetappLive(cfg, portal.log, portal), collector=wall_demo.FakeCollector(lambda: wall_demo.view(vc2_state="auth")))
wall.model(time.time() - 3 * 3600)              # a first read 3 h ago: durations read like a running wall
srv = sp.PortalServer(("127.0.0.1", 0), sp.make_handler(cfg, portal, static, wall=wall))
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % srv.server_address[1]


def call(path, dn=JDOE, body=None):
    cn = sp.who({"X-SSL-Client-Verify": "SUCCESS", "X-SSL-Client-S-DN": dn})[0]
    h = {"X-SSL-Client-Verify": "SUCCESS", "X-SSL-Client-S-DN": dn, "Host": "portal.example.mil", "Origin": "https://portal.example.mil",
         "Content-Type": "application/json", "X-Portal-Token": portal.token(cn)}
    r = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, method="POST" if body is not None else "GET", headers=h)
    try:
        return json.loads(urllib.request.urlopen(r, timeout=20).read() or b"{}")
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}")


ids = {}
ids["recheck"] = call("/api/actions/stig_recheck", body={"vars": {"target": "web01.example.mil"}}).get("job_id")
ids["clean"] = call("/api/actions/disk_cleanup", body={"vars": {"target": "web01.example.mil", "report_job": "907", "finding_id": "disk_full:/var"}}).get("job_id")
ids["restart"] = call("/api/actions/service_restart", body={"vars": {"target": "web03.example.mil", "report_job": "907", "finding_id": "service_down:chronyd"}}).get("job_id")
ids["live"] = call("/api/actions/stig_recheck", body={"vars": {"target": "web03.example.mil"}}).get("job_id")
ST = fake_aap.STATE
for wf in (ids["recheck"],):
    if wf in ST["jobs"]:
        for n in ST["jobs"][wf].get("nodes") or []:
            if n in ST["approvals"]:
                ST["approvals"][n]["status"] = "successful"
                ST["jobs"][n]["status"] = "successful"
        ST["jobs"][wf]["created"] -= 700
        for n in ST["jobs"][wf].get("nodes") or []:
            ST["jobs"][n]["created"] -= 700


class Front(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _go(self, method):
        path, _, q = self.path.partition("?")
        theme, rest = "", []
        for kv in q.split("&"):
            if kv.startswith("__theme="):
                theme = kv[8:]
            elif kv and not kv.startswith("__who="):
                rest.append(kv)
        who = APPR if "__who=appr" in (self.path + (self.headers.get("Referer") or "")) else JDOE
        sub = path[len("/portal"):] or "/" if path.startswith("/portal") else path
        host = base.split("://", 1)[1]
        h = {"X-SSL-Client-Verify": "SUCCESS", "X-SSL-Client-S-DN": who, "Host": host}
        for k in ("If-None-Match", "Accept", "Content-Type", "X-Portal-Token"):
            if self.headers.get(k):
                h[k] = self.headers[k]
        data = None
        if method == "POST":
            data = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            h["Origin"] = "https://" + host
        try:
            r = urllib.request.urlopen(urllib.request.Request(base + sub + (("?" + "&".join(rest)) if rest else ""), data=data, method=method, headers=h), timeout=30)
            code, hd, body = r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            code, hd, body = e.code, e.headers, e.read()
        if theme and "text/html" in (hd.get("Content-Type") or ""):
            body = body.replace(b'<html lang="en"', b'<html data-theme="' + theme.encode() + b'" lang="en"', 1)
        self.send_response(code)
        for k in ("Content-Type", "Content-Security-Policy", "ETag", "Cache-Control", "Location"):
            if hd.get(k):
                self.send_header(k, hd[k])
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._go("GET")

    def do_POST(self):
        self._go("POST")


front = ThreadingHTTPServer(("127.0.0.1", port), Front)
real.write("READY %s\n" % json.dumps(ids))
real.flush()
front.serve_forever()
