#!/usr/bin/env python3
"""1.75.2: the portal on the real actions.yml with the stand-in AAP (tests/site_portal/fake_aap.py), and a front that adds the card's
headers (nginx's job) and a colours choice - for looking at the pages (screenshots) and for tests/test_portal_fit.py. Test data only.
  python tests/site_portal/portal_demo.py LOOK PORT     prints READY {ids} when it listens
  ?__theme=light|dark picks the colours; ?__who=appr is an approver; /__fit?w=1280,800&look=ops is the fit page (iframes, measured)."""
import json, os, sys, tempfile, threading, urllib.request, urllib.error, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

wt = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
look, port = sys.argv[1], int(sys.argv[2])
sys.path[:0] = [os.path.join(wt, "plugins", "filter"), os.path.join(wt, "plugins", "module_utils"), os.path.join(wt, "roles/site_portal_stack/files"),
                os.path.join(wt, "tests", "site_portal")]
import yaml
import site_portal as sp
import site_portal_filters as rules
import fake_aap

JDOE = "CN=DOE.JANE.Q.1234567890,OU=CONTRACTOR,OU=PKI,O=U.S. Government,C=US"
APPR = "CN=SMITH.JOHN.A.1234567891,OU=USA,OU=PKI,O=U.S. Government,C=US"
fake_aap.RUN = 600          # a long run: the live pages keep asking
tmp = tempfile.mkdtemp()
fake_aap.LOG = os.path.join(tmp, "aap.jsonl")
T = fake_aap.STATE["templates"]
def tpl(name, wf, appr):
    d = {"id": fake_aap.new_id(), "name": name, "ask_variables_on_launch": True, "ask_limit_on_launch": True, "survey_enabled": False, "approval": appr}
    T["workflow_job_templates" if wf else "job_templates"].append(d)
for n, wf, a in (("STIG - recheck one host", 1, 0), ("Disk - clean up (approved)", 1, 1), ("Service - restart", 1, 1), ("Disk - space request", 0, 0),
                 ("Site portal - open ticket", 0, 0), ("STIG - recheck and update STIG Manager", 1, 1), ("STIG - fix one rule", 1, 1),
                 ("STIG - create the asset in STIG Manager", 1, 1)):
    tpl(n, wf, a)                      # "fapolicyd - allow this" is left out: its card shows Off
aap = fake_aap.serve(0, stop=True)
fake_aap.seed_reports()                      # 1.75.3: report jobs 9001 and 9002, for the pick pages
with open(os.path.join(wt, "roles/site_portal_stack/files/actions.yml")) as fh:
    doc = yaml.safe_load(fh)
with open(os.path.join(tmp, "actions.json"), "w") as fh:
    json.dump({"actions": doc["actions"]}, fh)
reports = os.path.join(tmp, "reports"); os.makedirs(reports)
cfg = sp.Config({"PORTAL_DATA": os.path.join(tmp, "data"), "PORTAL_REPORTS": reports, "PORTAL_ACTIONS": os.path.join(tmp, "actions.json"),
                 "PORTAL_AAP_URL": "http://127.0.0.1:%d" % aap.server_address[1], "PORTAL_AAP_TOKEN": "fake-token", "PORTAL_INVENTORY": "Site hosts",
                 "PORTAL_APPROVERS": '["SMITH.JOHN.A.1234567891"]', "PORTAL_TIME_ZONE": "UTC", "PORTAL_THEME": look, "PORTAL_FINDING_PLAYBOOKS": '["health_check.yml", "disk_report.yml"]', "PORTAL_TITLE": "Site automation",
                 "PORTAL_BANNER": {"on": False}})
real = sys.stdout
sys.stdout = open(os.devnull, "w")
portal = sp.Portal(cfg)
portal.aap.connect()
portal.aap.resolve(portal.actions)
srv = sp.PortalServer(("127.0.0.1", 0), sp.make_handler(cfg, portal, os.path.join(wt, "roles/site_portal_stack/files/static")))
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
# some history: a recheck (done soon), a clean-up waiting for approval, a restart waiting
ids = {}
_r = call("/api/actions/stig_recheck", body={"vars": {"target": "web01.example.mil"}}); print(_r, file=sys.stderr); ids["recheck"] = _r["job_id"]
ids["clean"] = call("/api/actions/disk_cleanup", body={"vars": {"target": "web01.example.mil", "report_job": "907", "finding_id": "disk_full:/var"}})["job_id"]
ids["restart"] = call("/api/actions/service_restart", body={"vars": {"target": "web03.example.mil", "report_job": "907", "finding_id": "service_down:chronyd"}})["job_id"]
ST = fake_aap.STATE
for wf in (ids["recheck"], ids["clean"]):                    # done: the approval given, the run's seconds over
    for n in ST["jobs"][wf].get("nodes") or []:
        if n in ST["approvals"]:
            ST["approvals"][n]["status"] = "successful"; ST["jobs"][n]["status"] = "successful"; ST["jobs"][n]["decided"] = time.time() - 650
    ST["jobs"][wf]["created"] -= 700
    for n in ST["jobs"][wf].get("nodes") or []:
        ST["jobs"][n]["created"] -= 700
with open(os.path.join(tmp, "ids.json"), "w") as fh:
    json.dump(ids, fh)

class Front(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def fit_page(self, q):
        """Every page at each width, light and dark, in frames of this site; the script measures what overflows and writes it as JSON."""
        widths = [int(x) for x in (dict(kv.split("=", 1) for kv in q.split("&") if "=" in kv).get("w") or "1280,800").split(",")]
        long_id = "disk_full:/var/lib/containers/storage/overlay/0123456789abcdef0123456789abcdef0123456789abcdef/merged/very-long-name"
        pages = [("home", "/portal/"), ("confirm", "/portal/actions/disk_cleanup?target=web01.example.mil&report_job=907&finding_id=" + long_id),
                 ("job-done", "/portal/jobs/%d" % ids["clean"]), ("job-waiting", "/portal/jobs/%d" % ids["restart"]),
                 ("approvals", "/portal/approvals?__who=appr"), ("refused", "/portal/actions/nothing_here"),
                 ("pick-host", "/portal/actions/disk_cleanup"), ("pick-findings", "/portal/actions/disk_cleanup?hosts=web01.example.mil"),
                 ("pick-many", "/portal/actions/disk_cleanup?hosts=web01.example.mil,web03.example.mil&pick0=9001~disk_full:web01.example.mil:/var"
                               "&pick0=9001~disk_full:web01.example.mil:/home&pick1=9001~disk_full:web03.example.mil:/srv"),
                 ("pick-none", "/portal/actions/disk_cleanup?hosts=fail01.example.mil"),
                 ("pick-two-hosts", "/portal/actions/disk_cleanup?hosts=web01.example.mil,web03.example.mil")]
        frames = [(n, u, th, w) for w in widths for th in ("light", "dark") for n, u in pages]
        html_ = ["<!doctype html><meta charset=utf-8><body>"]
        for i, (n, u, th, w) in enumerate(frames):
            html_.append('<iframe id="f%d" data-n="%s" data-th="%s" data-w="%d" src="%s%s__theme=%s" width="%d" height="1500"></iframe>'
                         % (i, n, th, w, u, "&" if "?" in u else "?", th, w))
        html_.append('<pre id="fitres">waiting</pre><script src="/__fit.js"></script>')
        return "".join(html_).encode()

    FIT_JS = b"""
(function () {
  var fr = Array.prototype.slice.call(document.querySelectorAll("iframe")), done = 0, res = [];
  function measure(f) {
    var d = f.contentDocument, w = +f.dataset.w, bad = [];
    var de = d.documentElement;
    if (de.scrollWidth > w + 1) bad.push("page scrolls sideways: " + de.scrollWidth + " > " + w);
    Array.prototype.forEach.call(d.querySelectorAll(".tw, pre, .card, .tile, .act, .note, .panel, .field, .btns, .appbar-in"), function (e) {
      if (e.scrollWidth > e.clientWidth + 1) bad.push((e.className || e.tagName) + " overflows: " + e.scrollWidth + " > " + e.clientWidth);
    });
    var cs = d.defaultView.getComputedStyle(d.documentElement);
    return {page: f.dataset.n, theme: f.dataset.th, width: w, bad: bad, bg: cs.getPropertyValue("color-scheme"),
            look: d.documentElement.getAttribute("data-look"), h1: (d.querySelector("h1") || {}).textContent || ""};
  }
  var t = setInterval(function () {      // every frame drawn (its page loaded): a moment more for the live pages' first answer
    var ready = fr.every(function (f) { var d = f.contentDocument; return d && d.readyState === "complete" && d.querySelector("h1"); });
    if (!ready) return;
    clearInterval(t);
    setTimeout(function () { document.getElementById("fitres").textContent = "FIT" + JSON.stringify(fr.map(measure)) + "END"; }, 2500);
  }, 200);
})();
"""

    def do_GET(self):
        path, _, q = self.path.partition("?")
        if path == "/__fit":
            body = self.fit_page(q)
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(body))); self.end_headers()
            return self.wfile.write(body)
        if path == "/__fit.js":
            self.send_response(200); self.send_header("Content-Type", "text/javascript"); self.send_header("Content-Length", str(len(self.FIT_JS))); self.end_headers()
            return self.wfile.write(self.FIT_JS)
        path = path[len("/portal"):] or "/" if path.startswith("/portal") else path
        theme, rest = "", []
        for kv in q.split("&"):
            if kv.startswith("__theme="): theme = kv[8:]
            elif kv: rest.append(kv)
        who = APPR if "__who=appr" in (self.path + (self.headers.get("Referer") or "")) else JDOE
        rest = [x for x in rest if not x.startswith("__who=")]
        up = base + path + (("?" + "&".join(rest)) if rest else "")
        r = urllib.request.Request(up, headers={"X-SSL-Client-Verify": "SUCCESS", "X-SSL-Client-S-DN": who, "Host": "portal.example.mil"})
        try:
            resp = urllib.request.urlopen(r, timeout=20); code, body, hdr = resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as e:
            code, body, hdr = e.code, e.read(), e.headers
        ct = hdr.get("Content-Type", "")
        if theme and "text/html" in ct:
            body = body.replace(b'<html lang="en"', b'<html data-theme="' + theme.encode() + b'" lang="en"', 1)
        self.send_response(code)
        for k in ("Content-Type", "Content-Security-Policy"):
            if hdr.get(k): self.send_header(k, hdr[k].replace("frame-ancestors 'none'", "frame-ancestors 'self'"))   # the fit page frames them
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
front = ThreadingHTTPServer(("127.0.0.1", port), Front)
print("READY", json.dumps(ids), file=real, flush=True)
front.serve_forever()
