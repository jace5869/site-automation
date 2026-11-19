#!/usr/bin/env python3
"""Unit tests for the site portal (roles/site_portal_stack/files/site_portal.py, plugins/filter/site_portal_filters.py):
the actions file's loader and rules (each wrong entry said; template braces refused in every form), who it is (the
client certificate the reverse proxy's nginx verified), and the service against a stand-in AAP
(tests/site_portal/fake_aap.py) in this process: the confirm page, a launch (the values, portal_requested_by, the
limit), each refusal (a person not listed, a value, a host not in the inventory, an unknown action, the CSRF checks, an
action AAP would not take, too many in an hour), the live job view, a workflow's step, the approvals (an approver and
not), the report website's files (and nothing outside its folder), and the log lines. Also the role's files: the
actions file it ships, the nginx lines, the deploy's syntax on this ansible-core.
Standard library and PyYAML only: python3 tests/test_site_portal.py"""
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FILES = os.path.join(ROOT, "roles", "site_portal_stack", "files")
sys.path.insert(0, os.path.join(ROOT, "plugins", "filter"))
sys.path.insert(0, os.path.join(ROOT, "plugins", "module_utils"))
sys.path.insert(0, FILES)
sys.path.insert(0, os.path.join(HERE, "site_portal"))
import site_portal_filters as rules  # noqa: E402
import site_portal as sp  # noqa: E402
import fake_aap  # noqa: E402

JDOE = "CN=DOE.JANE.Q.1234567890,OU=CONTRACTOR,OU=PKI,O=U.S. Government,C=US"
RROE = "CN=ROE.RICHARD.A.2345678901,OU=USN,OU=PKI,O=U.S. Government,C=US"
APPR = "CN=APPROVER.ALEX.B.3456789012,OU=USA,OU=PKI,O=U.S. Government,C=US"
PPOE = "CN=POE.PAT.C.4567890123,OU=USAF,OU=PKI,O=U.S. Government,C=US"
ACTIONS = {
    "stig_recheck": {"title": "Recheck this system", "aap": {"type": "job", "name": "Linux - recheck one host"},
                     "vars": {"target": {"kind": "inventory_host"}}, "limit_from": "target", "approval": False,
                     "area": "STIG", "summary": "Checks one host again."},
    "ckl_import": {"title": "Import the checklists", "area": "STIG", "summary": "Imports the checklists into STIG Manager.", "aap": {"type": "workflow", "name": "Checklists imported into STIG Manager"},
                   "vars": {"stigman_ckl_import_mode": {"kind": "choice", "values": ["import", "import and scorecard", "scorecard"],
                                                        "default": "import", "label": "What to do"}},
                   "approval": True},
    "act_triage": {"title": "Ask ACT about this finding", "area": "ACT", "summary": "ACT reads one finding and drafts the next step.", "aap": {"type": "workflow", "name": "ACT - triage finding"},
                   "vars": {"target": {"kind": "inventory_host"},
                            "report_job": {"kind": "text", "max": 12, "pattern": "[0-9]{1,12}"},
                            "finding_id": {"kind": "text", "max": 120, "pattern": "(fapolicyd|eventlog):[A-Za-z0-9_.:-]{1,100}"}},
                   "limit_from": "target", "approval": False, "who": ["DOE.JANE.Q.1234567890", "POE.PAT.C.4567890123"],
                   "fixes": False, "kinds": ["fapolicyd", "eventlog"], "button": "Ask ACT"},
    "disk_cleanup": {"title": "Clean up this mount", "area": "Disk & services", "summary": "Removes old files.", "aap": {"type": "job", "name": "Disk - clean up one mount"}, "button": "Clean up",
                     "kinds": ["disk_full"],
                     "vars": {"target": {"kind": "inventory_host"}, "report_job": {"kind": "text", "max": 12, "pattern": "[0-9]{1,12}"},
                              "finding_id": {"kind": "text", "max": 200, "pattern": "[A-Za-z0-9_.:/@-]{1,200}"}},
                     "limit_from": "target"},
    "no_prompts": {"title": "A template that takes nothing", "aap": {"type": "job", "name": "No prompts template"},
                   "vars": {"target": {"kind": "inventory_host"}}, "limit_from": "target"},
    "gone": {"title": "Not in AAP", "aap": {"type": "job", "name": "Nobody made this"}},
}


class _Keep(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


NO_REDIRECT = urllib.request.build_opener(_Keep())


def cert(dn=JDOE, verify="SUCCESS"):
    """The headers the reverse proxy's nginx sets (portal-location.conf.j2)."""
    return {"X-SSL-Client-Verify": verify, "X-SSL-Client-S-DN": dn}


class Loader(unittest.TestCase):
    def test_the_example_actions_load(self):
        a, p = rules.check_actions(ACTIONS)
        self.assertEqual(p, [])
        self.assertEqual(sorted(a), sorted(ACTIONS))
        self.assertEqual(a["ckl_import"]["vars"]["stigman_ckl_import_mode"]["default"], "import")
        self.assertEqual(a["act_triage"]["who"], ["DOE.JANE.Q.1234567890", "POE.PAT.C.4567890123"])
        self.assertIsNone(a["stig_recheck"]["who"])

    def test_the_shipped_file_loads(self):
        import yaml
        with open(os.path.join(FILES, "actions.yml")) as fh:
            doc = yaml.safe_load(fh)
        a, p = rules.check_actions(doc.get("actions"))
        self.assertEqual(p, [], "roles/site_portal_stack/files/actions.yml")

    def bad(self, entry, word):
        _, p = rules.check_actions({"x_y": entry})
        self.assertTrue(p, entry)
        self.assertIn(word, " ".join(p))

    def test_each_wrong_entry_is_said(self):
        ok = {"title": "T", "aap": {"type": "job", "name": "J"}}
        self.bad(dict(ok, title=""), "title is required")
        self.bad(dict(ok, aap={"type": "playbook", "name": "J"}), "aap must be")
        self.bad(dict(ok, aap={"type": "job"}), "aap must be")
        self.bad(dict(ok, vars={"t": {"kind": "number"}}), "kind must be one of")
        self.bad(dict(ok, vars={"t": {"kind": "choice", "values": []}}), "a choice needs values")
        self.bad(dict(ok, vars={"t": {"kind": "text", "max": 0}}), "max must be")
        self.bad(dict(ok, vars={"t": {"kind": "text", "max": 99999}}), "max must be")
        self.bad(dict(ok, vars={"t": {"kind": "text", "pattern": "([a-z"}}), "not a regular expression")
        self.bad(dict(ok, vars={"t": {"kind": "choice", "values": ["a"], "pattern": "a"}}), "pattern is for kind text only")
        self.bad(dict(ok, vars={"portal_requested_by": {"kind": "text"}}), "set by the portal itself")
        self.bad(dict(ok, vars={"bad-name": {"kind": "text"}}), "a var's name")
        self.bad(dict(ok, vars={"t": {"kind": "text"}}, limit_from="t"), "limit_from must name")
        self.bad(dict(ok, limit_from="nothing"), "limit_from must name")
        self.bad(dict(ok, approval="yes"), "approval must be true or false")
        self.bad(dict(ok, who="everyone"), "who must be any")
        self.bad(dict(ok, color="red"), "unknown key")
        self.bad(dict(ok, vars={"t": {"kind": "choice", "values": ["a"], "default": "b"}}), "its default must be one of")
        self.bad(dict(ok, vars={"t": {"kind": "choice", "values": ["ok", "{{ x }}"]}}), "braces")
        _, p = rules.check_actions({"Bad Name": ok})
        self.assertIn("lower case", " ".join(p))
        _, p = rules.check_actions(["not", "a", "dict"])
        self.assertIn("mapping", " ".join(p))

    def test_who(self):
        a, _ = rules.check_actions(ACTIONS)
        self.assertTrue(rules.may_run(a["stig_recheck"], "ANY.ONE", []))
        self.assertFalse(rules.may_run(a["stig_recheck"], "ANY.ONE", ["DOE.JANE.Q.1234567890"]))
        self.assertTrue(rules.may_run(a["act_triage"], "DOE.JANE.Q.1234567890", []))
        self.assertFalse(rules.may_run(a["act_triage"], "ROE.RICHARD.A.2345678901", []))
        b, _ = rules.check_actions({"x": {"title": "T", "aap": {"type": "job", "name": "J"}, "who": "any"}})
        self.assertTrue(rules.may_run(b["x"], "ANY.ONE", ["SOMEONE.ELSE"]))


class Braces(unittest.TestCase):
    def test_template_openers_in_any_form(self):
        for v in ("{{ lookup('pipe','id') }}", "x {% if 1 %}", "{# c #}", "{ {x}}", "{\t%", "{\n#",
                  "｛｛ x", "{​{", "{⁠%", "﹛﹛", "a{{"):
            self.assertTrue(rules.templating(v), repr(v))

    def test_plain_braces_pass(self):
        for v in ("{x}", "}}", "a } { b", "json {\"a\": 1}", "", "web01.example.mil"):
            self.assertEqual(rules.templating(v), "", repr(v))

    def test_values(self):
        a, _ = rules.check_actions(ACTIONS)
        t = a["act_triage"]
        ok = {"target": "web01.example.mil", "report_job": "907", "finding_id": "eventlog:disk-errors"}
        self.assertEqual(rules.check_values(t, ok), (ok, {}))
        v, p = rules.check_values(t, dict(ok, finding_id="eventlog:{{ 7*7 }}"))
        self.assertIn("braces", p["finding_id"])
        v, p = rules.check_values(t, dict(ok, report_job="9x"))
        self.assertEqual(p["report_job"], "is not in the expected form")
        v, p = rules.check_values(t, dict(ok, more="1"))
        self.assertIn("not a var of this action: more", p[""])
        v, p = rules.check_values(t, {"target": "web01.example.mil"})
        self.assertEqual(p, {"report_job": "is required", "finding_id": "is required"})
        v, p = rules.check_values(t, dict(ok, target="web01;id"))
        self.assertIn("not a host name", p["target"])
        v, p = rules.check_values(t, ok, lambda h: False)
        self.assertIn("not a host of the portal's inventory", p["target"])
        v, p = rules.check_values(t, ok, lambda h: None)
        self.assertIn("could not be looked up", p["target"])
        c = a["ckl_import"]
        self.assertEqual(rules.check_values(c, {}), ({"stigman_ckl_import_mode": "import"}, {}))
        v, p = rules.check_values(c, {"stigman_ckl_import_mode": "drop"})
        self.assertIn("must be one of", p["stigman_ckl_import_mode"])
        v, p = rules.check_values(c, "not a dict")
        self.assertIn("mapping", p[""])


class Who(unittest.TestCase):
    def test_who(self):
        self.assertEqual(sp.who(cert())[0], "DOE.JANE.Q.1234567890")
        self.assertIsNone(sp.who(cert(verify="NONE"))[0])
        self.assertIsNone(sp.who(cert(verify="FAILED:certificate has expired"))[0])
        self.assertIsNone(sp.who({})[0])
        self.assertIsNone(sp.who(cert(dn="OU=NO CN,C=US"))[0])
        self.assertIsNone(sp.who(cert(dn="CN={{ x }},C=US"))[0])
        self.assertEqual(sp.who(cert(dn="CN=SMITH\\, JOHN,O=X"))[0], "SMITH, JOHN")


class Live(unittest.TestCase):
    """The service and the stand-in AAP, each on a port of this machine."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        fake_aap.RUN = 1.5
        fake_aap.LOG = os.path.join(cls.tmp, "aap.jsonl")
        cls.aap = fake_aap.serve(0, stop=True)
        reports = os.path.join(cls.tmp, "reports")
        os.makedirs(os.path.join(reports, "linux-health"))
        with open(os.path.join(reports, "index.html"), "w") as fh:
            fh.write("<!doctype html><title>Reports</title><p>the index")
        with open(os.path.join(reports, "linux-health", "index.html"), "w") as fh:
            fh.write("<!doctype html><p>linux health")
        with open(os.path.join(cls.tmp, "secret.txt"), "w") as fh:
            fh.write("outside")
        os.symlink(os.path.join(cls.tmp, "secret.txt"), os.path.join(reports, "link.txt"))
        with open(os.path.join(cls.tmp, "actions.json"), "w") as fh:
            json.dump({"actions": ACTIONS}, fh)
        cls.cfg = sp.Config({"PORTAL_DATA": os.path.join(cls.tmp, "data"), "PORTAL_REPORTS": reports,
                             "PORTAL_ACTIONS": os.path.join(cls.tmp, "actions.json"),
                             "PORTAL_AAP_URL": "http://127.0.0.1:%d" % cls.aap.server_address[1], "PORTAL_AAP_TOKEN": "fake-token",
                             "PORTAL_INVENTORY": "Site hosts", "PORTAL_APPROVERS": '["APPROVER.ALEX.B.3456789012"]',
                             "PORTAL_REQUESTERS": "[]", "PORTAL_LAUNCHES_PER_HOUR": "6", "PORTAL_TIME_ZONE": "America/New_York",
                             "PORTAL_FINDING_PLAYBOOKS": '["health_check.yml", "disk_report.yml", "fapolicyd*.yml"]',
                             "PORTAL_BANNER": {"on": True, "text": "CUI", "bg": "#502b85", "fg": "#ffffff"}})
        sp.sys.stdout = open(os.devnull, "w")          # the journal's copy of each line
        cls.portal = sp.Portal(cls.cfg)
        cls.portal.aap.connect()
        cls.states = cls.portal.aap.resolve(cls.portal.actions)
        cls.srv = sp.PortalServer(("127.0.0.1", 0), sp.make_handler(cls.cfg, cls.portal, os.path.join(FILES, "static")))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.host = "127.0.0.1:%d" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.aap.shutdown()
        sp.sys.stdout = sys.__stdout__
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(self, path, dn=JDOE, method="GET", body=None, headers=None, verify="SUCCESS"):
        h = dict(cert(dn, verify) if dn else {}, **(headers or {}))
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request("http://%s%s" % (self.host, path), data=data, method=method, headers=h)
        try:
            with NO_REDIRECT.open(r, timeout=20) as f:
                raw = f.read()
                return f.status, raw, dict(f.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def post(self, path, body, dn=JDOE, token=None, origin=None, ctype="application/json"):
        cn = sp.who(cert(dn))[0]
        h = {"Content-Type": ctype, "Host": "portal.example.mil",
             "Origin": origin if origin is not None else "https://portal.example.mil",
             "X-Portal-Token": token if token is not None else self.portal.token(cn)}
        code, raw, _ = self.req(path, dn, "POST", body, h)
        return code, json.loads(raw or b"{}")

    def aap_launches(self):
        with open(fake_aap.LOG) as fh:
            return [json.loads(x) for x in fh if '"POST"' in x and "/launch/" in x]

    def log(self, event):
        return [r for r in self.portal.log.read() if r.get("event") == event]

    def test_01_states(self):
        self.assertEqual(self.states["stig_recheck"][1], "")
        self.assertEqual(self.states["ckl_import"][1], "")           # a survey with both questions
        self.assertIn("Prompt on launch for Variables", self.states["no_prompts"][1])
        self.assertIn("is not there", self.states["gone"][1])

    def test_02_no_card_no_page(self):
        code, raw, h = self.req("/", dn=None)
        self.assertEqual(code, 403)
        self.assertIn(b"No client certificate", raw)
        code, _, _ = self.req("/", verify="FAILED:unknown CA")
        self.assertEqual(code, 403)
        code, raw, h = self.req("/")
        self.assertEqual(code, 200)
        self.assertIn(b"Ask ACT about this finding", raw)
        self.assertIn("script-src 'self'", h["Content-Security-Policy"])
        self.assertNotIn(b"<script>", raw)
        code, raw, _ = self.req("/", dn=RROE)
        self.assertNotIn(b"Ask ACT about this finding", raw)                # who: Jane only

    def test_03_confirm_page(self):
        code, raw, _ = self.req("/actions/act_triage?target=web01.example.mil&report_job=907&finding_id=eventlog%3Adisk-errors")
        self.assertEqual(code, 200)
        t = raw.decode()
        self.assertIn('name="portal-token"', t)
        self.assertIn('value="web01.example.mil"', t)
        self.assertIn("eventlog:disk-errors", t)
        self.assertIn("Workflow ACT - triage finding", t)
        self.assertIn("<dt>Started as</dt><dd class=\"mono\">DOE.JANE.Q.1234567890</dd>", t)        # the name only, not the variable's
        self.assertNotIn(' disabled>Run', t)
        code, raw, _ = self.req("/actions/act_triage?target=nothere.example.mil&report_job=907&finding_id=eventlog%3A%7B%7B7%7D%7D")
        t = raw.decode()
        self.assertIn("is not a host of the portal&#x27;s inventory", t)
        self.assertIn("braces that start a template", t)
        self.assertIn('value="eventlog:{{7}}"', t)                       # shown as it came, as text: the field to correct
        self.assertIn('<input id="f-report_job" name="report_job" value="907" maxlength="12"', t)
        self.assertLess(t.index('id="f-target"'), t.index('id="f-report_job"'))   # the file's order
        code, raw, _ = self.req("/actions/act_triage?target=web01.example.mil", dn=RROE)
        self.assertIn(b"Not for you", raw)
        self.assertIn(b" disabled>Run", raw)
        code, raw, _ = self.req("/actions/no_prompts?target=web01.example.mil")
        self.assertIn(b"Off.", raw)
        code, raw, _ = self.req("/actions/nothing_here")
        self.assertEqual(code, 404)

    def test_03b_actions_page_in_the_look(self):
        """1.75.2: the Actions page - cards by area (Other last), each with its title and one line, the Approval badge, the Off
        card muted with its reason - in the report website's look, with no inline script or style (the page's CSP)."""
        code, raw, hdr = self.req("/")
        t = raw.decode()
        self.assertEqual(code, 200)
        self.assertIn('class="card strip"', t)                                       # the strip: AAP, when it was read, Refresh
        self.assertIn("Connected", t)
        self.assertRegex(t, r"AAP: <b>\d+ jobs? found</b>")
        self.assertRegex(t, r"Last refreshed \d\d:\d\d")
        self.assertIn('name="portal-token"', t)
        self.assertEqual(re.findall(r'data-show="(\w*)"', t), ["", "job", "workflow", "failed"])      # the four tiles, links without scripts
        self.assertIn('id="aq" name="q"', t)
        self.assertIn('<h3>Import the checklists</h3></div><div class="meta"><span class="ty">Workflow</span><span class="badge">Approval</span></div><p>Imports the checklists into STIG Manager.</p>', t)
        self.assertIn('<h3>Recheck this system</h3></div><div class="meta"><span class="ty">Job template</span></div><p>Checks one host again.</p>', t)
        self.assertRegex(t, r'class="card tcard off"><div class="top"><span class="ico">.*?<h3>A template that takes nothing</h3>.*?<span>Off: it does not take extra variables')
        self.assertIn('href="/portal/actions/stig_recheck/details" data-details>Details</a>', t)
        self.assertIn('data-run>Run', t)
        self.assertIn('<dialog class="dlg" id="dlg"', t)
        self.assertIn('data-look="atlas"', t)
        self.assertIn('id="theme-auto"', t)
        self.assertIn('id="theme-light"', t)
        self.assertIn('id="theme-dark"', t)
        self.assertIn("DOE.JANE.Q.1234567890", t)                                      # the card's name, as before
        csp = hdr["Content-Security-Policy"]
        self.assertIn("style-src 'self'", csp)
        self.assertNotIn("unsafe-inline", csp)
        for path in ("/", "/actions/act_triage?target=web01.example.mil", "/approvals", "/jobs/1", "/actions/nothing_here"):
            page = self.req(path)[1].decode()
            self.assertIsNone(re.search(r"<style|\sstyle=|\son[a-z]+=|<script(?![^>]*\ssrc=)", page), path)         # strict CSP: none inline

    def test_03c_refused_and_not_here_pages(self):
        code, raw, _ = self.req("/actions/nothing_here")
        t = raw.decode()
        self.assertEqual(code, 404)
        self.assertIn('<div class="note" role="alert">', t)
        self.assertIn("There is no action nothing_here here.", t)
        self.assertIn('class="chip s-info"', t)
        code, raw, _ = self.req("/", dn=None)
        t = raw.decode()
        self.assertEqual(code, 403)
        self.assertIn('class="chip s-critical"', t)
        self.assertIn("Not accepted", t)
        self.assertNotIn("DOE.JANE", t)

    def test_03d_the_other_pages(self):
        t = self.req("/approvals", dn=APPR)[1].decode()
        self.assertIn('id="approvals" data-approver="1"', t)
        self.assertIn('class="note"', t)
        t = sp.job_page(self.cfg, self.portal, "DOE.JANE.Q.1234567890", 77, 0)
        self.assertIn('class="card jl"', t)                                             # the Jobs page: the list, and the pane of the job
        self.assertIn('id="jd"', t)
        js = self.req("/static/portal.js")[1].decode()
        self.assertIn("function homePage", js)
        self.assertIn("approvalsPage", js)
        self.assertNotIn("innerHTML", js)                                               # text only, never HTML from AAP
        self.assertEqual(self.req("/static/portal-theme.js")[0], 200)
        self.assertEqual(self.req("/static/portal.css")[0], 200)

    # ---- 1.75.3: pick a host, then a finding ----------------------------------------------------------------------------
    H1, H3 = "web01.example.mil", "web03.example.mil"

    def seed(self):
        """Report jobs: two of Health check - Linux (the newest failed: it found something), a Disk report with the same /var again, one too old,
        one that ended in error, one of another playbook; findings of two hosts, a wrong kind."""
        now = time.time()
        fa = fake_aap
        fa.STATE["reports"] = []
        e = lambda kind, host, key, title, sev, data=None: {"id": "%s:%s:%s" % (kind, host, key), "kind": kind, "host": host, "title": title,       # noqa: E731
                                                            "severity": sev, "data": data or {"os": "linux"}}
        self.j_new = fa.add_report("Health check - Linux", "health_check.yml", "failed", now - 3600, {
            self.H1: [e("disk_full", self.H1, "/var", "/var is 96% full", "critical"), e("disk_full", self.H1, "/home", "/home is 91% full", "warning"),
                      e("service_down", self.H1, "chronyd", "chronyd is not running", "critical")],
            self.H3: [e("disk_full", self.H3, "/var", "/var is 88% full", "warning"), e("disk_full", self.H3, "/srv", "/srv is 90% full", "warning")]})
        self.j_disk = fa.add_report("Disk report", "disk_report.yml", "successful", now - 90000, {
            self.H1: [e("disk_full", self.H1, "/var", "/var is 96% full", "critical")]})
        self.j_old = fa.add_report("Health check - Linux", "health_check.yml", "failed", now - 2 * 86400, {
            self.H1: [e("disk_full", self.H1, "/var", "/var is 93% full", "warning")]})
        fa.add_report("Health check - Linux", "health_check.yml", "failed", now - 9 * 86400, {self.H1: [e("disk_full", self.H1, "/old", "/old is 99% full", "critical")]})
        fa.add_report("Health check - Linux", "health_check.yml", "error", now - 600, {self.H1: [e("disk_full", self.H1, "/err", "/err is 99% full", "critical")]})
        fa.add_report("Windows health", "win_health_check.yml", "failed", now - 600, {self.H1: [e("disk_full", self.H1, "C:", "C: is 99% full", "critical")]})
        self.portal.aap._rj = self.portal.aap._rt = None             # the 60 second and the 5 minute memories
        self.cfg.finding_playbooks = ["health_check.yml", "disk_report.yml", "fapolicyd*.yml"]

    def fid(self, host, key):
        return "disk_full:%s:%s" % (host, key)

    def test_05a_pick_the_host(self):
        code, raw, _ = self.req("/actions/disk_cleanup")
        t = raw.decode()
        self.assertEqual(code, 200)
        self.assertIn('<select multiple size="8" id="f-hosts" name="hosts"', t)
        self.assertIn('data-picker="hosts"', t)
        self.assertIn('<li class="on"><span class="n">1</span>Host</li>', t)
        self.assertNotIn("report_job", t)                                           # no job id asked
        self.assertNotIn('id="run"', t)
        t = self.req("/actions/stig_recheck")[1].decode()                         # no report_job: the confirm page as before
        self.assertIn('id="run"', t)
        t = self.req("/actions/disk_cleanup?target=%s&report_job=907&finding_id=%s" % (self.H1, self.fid(self.H1, "/var")))[1].decode()
        self.assertIn('id="run"', t)                                                # a report's button: the confirm page, as before
        self.assertIn('value="907"', t)

    def test_05b_findings_newest_first_one_each(self):
        self.seed()
        code, raw, _ = self.req("/actions/disk_cleanup?hosts=" + self.H1)
        t = raw.decode()
        self.assertEqual(code, 200)
        titles = re.findall(r'<span class="t"><b>(.*?)</b>', t)
        self.assertEqual(titles[:2], ["/var is 96% full", "/home is 91% full"])       # newest first; service_down, the old, the error: not here
        self.assertIn('value="%d~%s" checked' % (self.j_new, self.fid(self.H1, "/var")), t)       # the newest of each id; the first is ticked
        self.assertNotIn('value="%d~%s" checked' % (self.j_new, self.fid(self.H1, "/home")), t)
        self.assertIn("Older runs (2)", t)                                         # the Disk report's /var and the 2-day-old /var 93%: folded
        self.assertIn("job %d" % self.j_disk, t)
        self.assertIn("Health check - Linux", t)
        self.assertNotIn("chronyd", t)                                              # another kind
        self.assertNotIn("/old is 99%", t)                                          # past portal_finding_days
        self.assertNotIn("/err is 99%", t)                                          # ended in error
        self.assertNotIn("C: is 99%", t)                                            # a playbook not listed
        self.assertIn('type="checkbox"', t)
        self.assertNotIn('<input type="radio" name="pick', t)
        self.assertIn("What the portal looked at", t)
        self.assertRegex(t, r"Health check - Linux</td><td class=\"num\">2</td>")           # jobs read: the failed and the 2-day-old (not the 9-day, not the error)

    def test_05c_one_pick_goes_to_confirm(self):
        self.seed()
        fid = self.fid(self.H1, "/var")
        code, raw, hdr = self.req("/actions/disk_cleanup?hosts=%s&pick0=%d~%s" % (self.H1, self.j_new, urllib.parse.quote(fid)))
        self.assertEqual(code, 302)
        loc = hdr["Location"]
        self.assertEqual(urllib.parse.urlsplit(loc).path, "/portal/actions/disk_cleanup")
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(loc).query),
                         {"target": [self.H1], "report_job": [str(self.j_new)], "finding_id": [fid]})
        code, raw, _ = self.req(loc[len("/portal"):])
        self.assertIn('id="run"', raw.decode())                                      # the confirm page, its checks as ever
        self.assertEqual(self.req("/actions/disk_cleanup?hosts=%s&pick0=junk" % self.H1)[0], 200)      # a pick that is not one: back to the list

    def test_05d_two_hosts_two_findings(self):
        self.seed()
        q = "hosts=%s,%s&pick0=%d~%s&pick0=%d~%s&pick1=%d~%s&pick1=%d~%s" % (
            self.H1, self.H3, self.j_new, self.fid(self.H1, "/var"), self.j_new, self.fid(self.H1, "/home"),
            self.j_new, self.fid(self.H3, "/var"), self.j_new, self.fid(self.H3, "/srv"))
        code, raw, _ = self.req("/actions/disk_cleanup?" + q, dn=RROE)
        t = raw.decode()
        self.assertEqual(code, 200)
        self.assertIn("Run 4 jobs", t)
        self.assertEqual(len(re.findall(r'<tr data-host="', t)), 4)
        for h, key in ((self.H1, "/var"), (self.H1, "/home"), (self.H3, "/var"), (self.H3, "/srv")):
            self.assertIn('data-host="%s" data-job="%d" data-fid="%s"' % (h, self.j_new, self.fid(h, key)), t)
        self.assertIn('data-hv="target"', t)
        self.assertIn('name="portal-token"', t)
        before = len(self.aap_launches())
        for h, key in ((self.H1, "/var"), (self.H1, "/home"), (self.H3, "/var"), (self.H3, "/srv")):       # what Run does: one launch each, the existing call
            code, r = self.post("/api/actions/disk_cleanup", {"vars": {"target": h, "report_job": str(self.j_new), "finding_id": self.fid(h, key)}}, dn=RROE)
            self.assertEqual(code, 201, r)
        sent = self.aap_launches()[before:]
        self.assertEqual(sorted(x["body"]["extra_vars"]["finding_id"] for x in sent),
                         sorted([self.fid(self.H1, "/var"), self.fid(self.H1, "/home"), self.fid(self.H3, "/var"), self.fid(self.H3, "/srv")]))
        self.assertEqual(sorted(x["body"]["limit"] for x in sent), [self.H1, self.H1, self.H3, self.H3])
        code, r = self.post("/api/actions/disk_cleanup", {"vars": {"target": self.H1, "report_job": str(self.j_new), "finding_id": self.fid(self.H1, "/var")}}, dn=RROE)
        self.assertEqual(code, 409, r)                                              # per pair: running already
        # a host skipped, and the cap
        code, raw, _ = self.req("/actions/disk_cleanup?hosts=%s,%s&pick0=skip&pick0=%d~%s&pick1=%d~%s" % (
            self.H1, self.H3, self.j_new, self.fid(self.H1, "/var"), self.j_new, self.fid(self.H3, "/var")))
        self.assertEqual(code, 302)                                                 # one pair left: the confirm page
        self.assertIn("web03", urllib.parse.parse_qs(urllib.parse.urlsplit(_["Location"]).query)["target"][0])
        self.cfg.pick_max = 3
        try:
            t = self.req("/actions/disk_cleanup?" + q)[1].decode()
        finally:
            self.cfg.pick_max = 10
        self.assertIn("Too many.", t)
        self.assertIn("at most 3 can start from one page", t)
        self.assertNotIn("Run 4 jobs", t)

    def test_05e_nothing_found_and_the_hosts_are_checked(self):
        self.seed()
        code, raw, _ = self.req("/actions/disk_cleanup?hosts=fail01.example.mil")
        t = raw.decode()
        self.assertEqual(code, 200)
        self.assertIn("Nothing found", t)
        self.assertIn("What the portal looked at", t)
        self.assertIn('href="/portal/reports/">Open the reports', t)
        self.assertIn("Pick another host", t)
        t = self.req("/actions/disk_cleanup?hosts=nothere.example.mil")[1].decode()
        self.assertIn("is not a host or a group of the portal&#x27;s inventory", t)
        self.assertIn('name="hosts"', t)                                            # back at step 1, the text kept
        t = self.req("/actions/disk_cleanup?hosts=" + ",".join("h%d.example.mil" % i for i in range(6)))[1].decode()
        self.assertIn("is not a host or a group", t)                                # each pick is checked first (six names the inventory does not have)
        t = self.req("/actions/disk_cleanup?hosts={{7}}")[1].decode()
        self.assertIn("braces that start a template", t)
        fake_aap.AUDITOR = False                                                    # the account cannot read the jobs: said, not hidden
        try:
            self.portal.aap._rj = self.portal.aap._rt = None
            t = self.req("/actions/disk_cleanup?hosts=" + self.H1)[1].decode()
        finally:
            fake_aap.AUDITOR = True
            self.portal.aap._rj = self.portal.aap._rt = None
        self.assertIn("No report job finished in the last 7 days", t)

    def aap_gets(self):
        with open(fake_aap.LOG) as fh:
            return [json.loads(x) for x in fh if '"GET"' in x]

    def test_05g_the_playbook_is_the_path_in_the_project(self):
        """AAP keeps a job's playbook as its path in the project ('playbooks/health_check.yml'): a filter with the bare name finds nothing. The
        stand-in does the same, so a query with the name only fails here; the portal asks with a case-insensitive regular expression that matches the path (and a
        playbook at the project's root)."""
        self.seed()
        fake_aap.add_report("Root report", "disk_report.yml", "failed", time.time() - 600,
                            {self.H1: [{"id": self.fid(self.H1, "/root"), "kind": "disk_full", "host": self.H1, "title": "/ is 97% full",
                                        "severity": "critical", "data": {"os": "linux"}}]}, path="disk_report.yml")
        self.cfg.finding_playbooks = ["health_check.yml", "disk_report.yml"]
        self.portal.aap._rj = self.portal.aap._rt = None
        with urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:%d/api/controller/v2/jobs/?playbook=health_check.yml" % self.aap.server_address[1],
                                                           headers={"Authorization": "Bearer fake-token"})) as f:
            self.assertEqual(json.loads(f.read())["count"], 0)                      # the bare name: nothing, as in AAP
        n = len(self.aap_gets())
        t = self.req("/actions/disk_cleanup?hosts=" + self.H1)[1].decode()
        self.assertIn("/var is 96% full", t)
        self.assertIn("/ is 97% full", t)                                           # a playbook at the project's root is found too
        queries = [x["path"] + "?" + "&".join("%s=%s" % kv for kv in x["query"].items()) for x in self.aap_gets()[n:]]
        self.assertTrue(any(q.startswith("job_templates/?playbook__iregex=") for q in queries), queries)
        self.assertFalse(any("playbook=" in q.replace("playbook__iregex=", "") for q in queries), queries)         # never the bare name

    def test_05h_a_busy_template_hides_no_other(self):
        """The runs are asked for per job template: 30 runs of one report cannot push another report's out of the list."""
        now = time.time()
        fake_aap.STATE["reports"] = []
        for i in range(30):
            fake_aap.add_report("Health check - Linux", "health_check.yml", "successful", now - 100 - i * 60, {self.H1: []})
        fake_aap.add_report("Disk report", "disk_report.yml", "failed", now - 5 * 3600, {self.H1: [
            {"id": self.fid(self.H1, "/srv"), "kind": "disk_full", "host": self.H1, "title": "/srv is 95% full", "severity": "critical", "data": {"os": "linux"}}]})
        self.cfg.finding_playbooks = ["health_check.yml", "disk_report.yml"]
        self.portal.aap._rj = self.portal.aap._rt = None
        t = self.req("/actions/disk_cleanup?hosts=" + self.H1)[1].decode()
        self.assertIn("/srv is 95% full", t)                                        # the Disk report's run is read
        self.assertRegex(t, r"<td>Health check - Linux</td><td class=\"num\">5</td>")        # the busy one: its last 5 only (portal_finding_jobs)
        self.assertRegex(t, r"<td>Disk report</td><td class=\"num\">1</td>")
        jobs = [x for x in self.aap_gets() if x["path"] == "jobs/" and x["query"].get("job_template")]
        self.assertTrue(jobs and all(x["query"].get("page_size") == "5" for x in jobs[-2:]))      # asked per template, as many as are read

    def finding(self, key, title="x"):
        return {self.H1: [{"id": self.fid(self.H1, key), "kind": "disk_full", "host": self.H1, "title": title, "severity": "critical", "data": {"os": "linux"}}]}

    def test_05i_each_job_counts_by_its_own_playbook(self):
        """A template edited to run another playbook still returns its old jobs: each job is checked against the allow-list by its own playbook,
        and labelled with it - not with the template's."""
        now = time.time()
        fake_aap.STATE["reports"] = []
        fake_aap.add_report("Health check - Linux", "health_check.yml", "failed", now - 3600, self.finding("/var", "/var is 96% full"))
        fake_aap.add_report("Health check - Linux", "other_playbook.yml", "failed", now - 600, self.finding("/planted", "/planted is 99% full"))   # same template, not a report
        self.cfg.finding_playbooks = ["health_check.yml"]
        self.portal.aap._rj = self.portal.aap._rt = None
        jobs, _notes = self.portal.aap.report_jobs()
        self.assertEqual([(j["playbook"], j["name"]) for j in jobs], [("health_check.yml", "Health check - Linux")])     # the label is the job's own file
        t = self.req("/actions/disk_cleanup?hosts=" + self.H1)[1].decode()
        self.assertIn("/var is 96% full", t)
        self.assertNotIn("/planted", t)

    def test_05j_every_page_of_the_templates(self):
        """More report templates than one page of AAP's answer (here the server gives 2 at a time): every page is followed before the list is kept."""
        now = time.time()
        fake_aap.STATE["reports"] = []
        for i in range(5):
            fake_aap.add_report("Report %d" % i, "health_check.yml", "failed", now - 600 - i * 60, self.finding("/m%d" % i, "/m%d is 9%d%% full" % (i, i)),
                                path="playbooks/dir%d/health_check.yml" % i)
        fake_aap.PAGE_CAP = 2
        self.cfg.finding_playbooks = ["health_check.yml"]
        self.portal.aap._rj = self.portal.aap._rt = None
        n = len(self.aap_gets())
        try:
            t = self.req("/actions/disk_cleanup?hosts=" + self.H1)[1].decode()
        finally:
            fake_aap.PAGE_CAP = 200
        for i in range(5):
            self.assertIn("/m%d is 9%d%% full" % (i, i), t)
        pages = [x for x in self.aap_gets()[n:] if x["path"] == "job_templates/"]
        self.assertEqual(sorted(x["query"].get("page", "1") for x in pages), ["1", "2", "3"])
        self.assertEqual(self.portal.aap._relative("/api/controller/v2/job_templates/?page=2"), "job_templates/?page=2")
        self.assertEqual(self.portal.aap._relative("/elsewhere/?page=2"), "")

    def test_05k_the_playbook_name_is_matched_without_case(self):
        """The allow-list compares file names without case (as finding_ref does): the lookup is a case-insensitive regex (playbook__iregex)."""
        now = time.time()
        fake_aap.STATE["reports"] = []
        fake_aap.add_report("Health check - Linux", "Health_Check.yml", "failed", now - 600, self.finding("/Case", "/Case is 97% full"))
        self.cfg.finding_playbooks = ["health_check.yml"]
        self.portal.aap._rj = self.portal.aap._rt = None
        n = len(self.aap_gets())
        t = self.req("/actions/disk_cleanup?hosts=" + self.H1)[1].decode()
        self.assertIn("/Case is 97% full", t)
        q = [x["query"] for x in self.aap_gets()[n:] if x["path"] == "job_templates/"]
        self.assertTrue(q and all("playbook__iregex" in x and "playbook__regex" not in x for x in q), q)

    def test_05f_typeahead_and_account_check(self):
        code, raw, _ = self.req("/api/hosts?q=web")
        self.assertEqual((code, json.loads(raw)["hosts"]), (200, [self.H1, self.H3]))
        self.assertEqual(json.loads(self.req("/api/hosts?q=w")[1])["hosts"], [])    # too short
        self.assertEqual(json.loads(self.req("/api/hosts?q=%7B%7B")[1])["hosts"], [])
        self.assertEqual(self.req("/api/hosts?q=web", dn=None)[0], 403)             # a card is needed
        self.seed()
        res = sp.pick_check(self.portal)
        self.assertEqual((res["on"], res["auditor"], res["system_auditor"]), (True, ["Default"], False))
        self.assertEqual(res["unread"], ["fapolicyd*.yml"])                         # no job of it is visible
        fake_aap.AUDITOR = False
        try:
            res = sp.pick_check(self.portal)
        finally:
            fake_aap.AUDITOR = True
        self.assertEqual((res["auditor"], sorted(res["unread"])), ([], ["disk_report.yml", "fapolicyd*.yml", "health_check.yml"]))

    def test_04_launch(self):
        code, r = self.post("/api/actions/stig_recheck", {"vars": {"target": "web01.example.mil"}})
        self.assertEqual(code, 201, r)
        self.assertEqual(r["url"], "/portal/jobs/%d" % r["job_id"])
        sent = self.aap_launches()[-1]["body"]
        self.assertEqual(sent, {"extra_vars": {"target": "web01.example.mil", "portal_requested_by": "DOE.JANE.Q.1234567890"},
                                "limit": "web01.example.mil"})
        last = self.log("launch")[-1]
        self.assertEqual((last["user"], last["action"], last["job_id"], last["values"]),
                         ("DOE.JANE.Q.1234567890", "stig_recheck", r["job_id"], {"target": "web01.example.mil"}))
        self.assertTrue(last["at"].endswith("Z"))
        Live.job_id = r["job_id"]

    def test_05_refusals(self):
        n = len(self.aap_launches())
        code, r = self.post("/api/actions/act_triage", {"vars": {"target": "web01.example.mil", "report_job": "907",
                                                                 "finding_id": "eventlog:disk-errors"}}, dn=RROE)
        self.assertEqual(code, 403)
        self.assertIn("not on the list", r["error"])
        code, r = self.post("/api/actions/stig_recheck", {"vars": {"target": "nothere.example.mil"}})
        self.assertEqual(code, 400)
        self.assertIn("not a host of the portal's inventory", r["error"])
        code, r = self.post("/api/actions/act_triage", {"vars": {"target": "web01.example.mil", "report_job": "907",
                                                                 "finding_id": "eventlog:{% raw %}"}})
        self.assertEqual(code, 400)
        self.assertIn("braces", r["error"])
        code, r = self.post("/api/actions/stig_recheck", {"vars": {"target": "web01.example.mil", "extra_vars": "x"}})
        self.assertEqual(code, 400)
        self.assertIn("not a var of this action", r["error"])
        code, r = self.post("/api/actions/nothing_here", {"vars": {}})
        self.assertEqual(code, 404)
        code, r = self.post("/api/actions/no_prompts", {"vars": {"target": "web01.example.mil"}})
        self.assertEqual(code, 503)
        self.assertIn("Prompt on launch", r["error"])
        self.assertEqual(len(self.aap_launches()), n, "nothing reached AAP")
        self.assertTrue(any(x.get("why") == "values" for x in self.log("refused")))

    def test_06_csrf(self):
        n = len(self.aap_launches())
        body = {"vars": {"target": "web01.example.mil"}}
        self.assertEqual(self.post("/api/actions/stig_recheck", body, token="")[0], 403)
        self.assertEqual(self.post("/api/actions/stig_recheck", body, token="1.abc")[0], 403)
        self.assertEqual(self.post("/api/actions/stig_recheck", body, token=self.portal.token("ROE.RICHARD.A.2345678901"))[0], 403)
        self.assertEqual(self.post("/api/actions/stig_recheck", body, token=self.portal.token("DOE.JANE.Q.1234567890", time.time() - 9 * 3600))[0], 403)
        self.assertEqual(self.post("/api/actions/stig_recheck", body, origin="https://evil.example.mil")[0], 403)
        self.assertEqual(self.post("/api/actions/stig_recheck", body, origin="http://portal.example.mil")[0], 403)
        self.assertEqual(self.post("/api/actions/stig_recheck", body, origin="")[0], 403)      # no Origin, no Referer
        self.assertEqual(self.post("/api/actions/stig_recheck", body, ctype="text/plain")[0], 403)
        code, raw, _ = self.req("/api/actions/stig_recheck", headers={"Content-Type": "application/json"}, method="POST",
                                body=body, verify="NONE")
        self.assertEqual(code, 403)
        self.assertEqual(len(self.aap_launches()), n)
        self.assertTrue(any("CSRF" in (x.get("why") or "") for x in self.log("refused")))

    def test_07_live_job(self):
        jid = getattr(Live, "job_id", None) or self.post("/api/actions/stig_recheck", {"vars": {"target": "web01.example.mil"}})[1]["job_id"]
        code, raw, _ = self.req("/jobs/%d" % jid)
        self.assertEqual(code, 200)
        self.assertIn(b'data-job="%d"' % jid, raw)
        end, v = time.time() + 15, {}
        while time.time() < end:
            code, raw, _ = self.req("/api/jobs/%d" % jid)
            v = json.loads(raw)
            if v.get("done"):
                break
            time.sleep(0.3)
        self.assertEqual(v["status"], "successful", v)
        self.assertEqual(v["requested_by"], "DOE.JANE.Q.1234567890")
        self.assertIn("PLAY RECAP", v["output"])
        self.assertNotIn("\x1b[", v["output"])
        self.assertEqual(v["hosts"][0]["host"], "web01.example.mil")
        self.assertTrue(any(lk["url"].startswith("https://reports.example.mil/") for lk in v["links"]))
        code, _, _ = self.req("/api/jobs/99999")
        self.assertEqual(code, 404)

    def test_08_approvals(self):
        code, r = self.post("/api/actions/ckl_import", {"vars": {}})
        self.assertEqual(code, 201, r)
        sent = self.aap_launches()[-1]["body"]
        self.assertEqual(sent["extra_vars"]["stigman_ckl_import_mode"], "import")
        self.assertNotIn("limit", sent)
        wf = r["job_id"]
        code, raw, _ = self.req("/api/approvals")
        aps = [a for a in json.loads(raw)["approvals"] if a["workflow_job"] == wf]
        self.assertEqual(len(aps), 1)
        self.assertEqual(aps[0]["requested_by"], "DOE.JANE.Q.1234567890")
        aid = aps[0]["id"]
        code, raw, _ = self.req("/approvals")
        self.assertIn(b"as the portal's own account", raw)
        self.assertIn(b'data-approver="0"', raw)
        code, r = self.post("/api/approvals/%d/approve" % aid, {})
        self.assertEqual(code, 403)
        self.assertIn("not on the list of approvers", r["error"])
        code, r = self.post("/api/approvals/%d/approve" % aid, {}, dn=APPR, token="")
        self.assertEqual(code, 403)                                                   # the CSRF check for approvals too
        code, r = self.post("/api/approvals/%d/approve" % aid, {}, dn=APPR)
        self.assertEqual(code, 200, r)
        d = self.log("decision")[-1]
        self.assertEqual((d["user"], d["decision"], d["approval"], d["requested_by"]),
                         ("APPROVER.ALEX.B.3456789012", "approve", aid, "DOE.JANE.Q.1234567890"))
        code, r = self.post("/api/approvals/%d/deny" % aid, {}, dn=APPR)
        self.assertEqual(code, 404)                                                   # decided already
        end, v = time.time() + 15, {}
        while time.time() < end:
            v = json.loads(self.req("/api/jobs/%d" % wf)[1])
            if v.get("done"):
                break
            time.sleep(0.3)
        self.assertEqual(v["status"], "successful", v)
        self.assertEqual([s["type"] for s in v["steps"]], ["workflow_approval", "job"])
        child = v["steps"][1]["job"]
        self.assertEqual(self.req("/api/jobs/%d" % child)[0], 404)                      # not by itself
        code, raw, _ = self.req("/api/jobs/%d?via=%d" % (child, wf))
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(raw)["via"], wf)
        # a deny: the workflow fails
        wf2 = self.post("/api/actions/ckl_import", {"vars": {"stigman_ckl_import_mode": "scorecard"}})[1]["job_id"]
        aid2 = [a for a in json.loads(self.req("/api/approvals")[1])["approvals"] if a["workflow_job"] == wf2][0]["id"]
        self.assertEqual(self.post("/api/approvals/%d/deny" % aid2, {}, dn=APPR)[0], 200)
        self.assertEqual(self.log("decision")[-1]["decision"], "deny")
        self.assertEqual(json.loads(self.req("/api/jobs/%d" % wf2)[1])["status"], "failed")

    def test_09_reports(self):
        code, raw, h = self.req("/reports/")
        self.assertEqual((code, raw.startswith(b"<!doctype html><title>Reports")), (200, True))
        self.assertIn("style-src 'self' 'unsafe-inline'", h["Content-Security-Policy"])
        code, _, h = self.req("/reports/linux-health")
        self.assertEqual((code, h.get("Location")), (301, "/portal/reports/linux-health/"))
        self.assertEqual(self.req("/reports/linux-health/")[0], 200)
        for p in ("/reports/../secret.txt", "/reports/%2e%2e/secret.txt", "/reports/link.txt", "/reports/nothing.html"):
            self.assertEqual(self.req(p)[0], 404, p)
        self.assertEqual(self.req("/reports/", dn=None)[0], 403)

    def test_10_too_many(self):
        self.portal.recent.pop("DOE.JANE.Q.1234567890", None)
        codes = [self.post("/api/actions/stig_recheck", {"vars": {"target": "web03.example.mil"}})[0] for _ in range(7)]
        self.assertEqual(codes, [201] * 6 + [429])
        self.portal.recent.pop("DOE.JANE.Q.1234567890", None)

    def test_12_not_your_own_run(self):
        code, r = self.post("/api/actions/ckl_import", {"vars": {}}, dn=APPR)            # the approver starts one
        self.assertEqual(code, 201, r)
        wf = r["job_id"]
        code, raw, _ = self.req("/api/approvals", dn=APPR)
        a = [x for x in json.loads(raw)["approvals"] if x["workflow_job"] == wf][0]
        self.assertEqual((a["self"], a["may_decide"]), (True, False))
        code, r = self.post("/api/approvals/%d/approve" % a["id"], {}, dn=APPR)
        self.assertEqual(code, 403)
        self.assertIn("You started this run", r["error"])
        self.assertTrue(any(x.get("why", "").startswith("self-approval") for x in self.log("refused")))
        self.cfg.self_approval = True                                                   # portal_allow_self_approval: true
        try:
            code, raw, _ = self.req("/api/approvals", dn=APPR)
            self.assertTrue([x for x in json.loads(raw)["approvals"] if x["workflow_job"] == wf][0]["may_decide"])
            self.assertEqual(self.post("/api/approvals/%d/deny" % a["id"], {}, dn=APPR)[0], 200)
        finally:
            self.cfg.self_approval = False

    def test_13_times_in_report_time_zone(self):
        jid = self.post("/api/actions/stig_recheck", {"vars": {"target": "web03.example.mil"}})[1]["job_id"]
        v = json.loads(self.req("/api/jobs/%d" % jid)[1])
        self.assertRegex(v["requested_at_text"], r"^\d{4}-\d\d-\d\d \d\d:\d\d E[SD]T$")
        code, raw, _ = self.req("/jobs/%d" % jid)
        self.assertIn(b"E", raw)                                                         # the Jobs page: a time says its zone (the same shown())
        self.assertRegex(raw.decode(), r"\d{4}-\d\d-\d\d \d\d:\d\d E[SD]T")
        self.assertEqual(sp.shown(sp.Config({}), "2026-11-15T19:06:30.123456Z"), "2026-11-15 19:06 UTC")
        self.assertEqual(sp.shown(self.cfg, "2026-07-15T19:06:30Z"), "2026-07-15 15:06 EDT")
        self.assertEqual(sp.shown(self.cfg, None), "")

    def test_14_the_reports_banner(self):
        code, raw, _ = self.req("/")
        self.assertIn(b'<div class="banner">CUI</div>', raw)
        code, css, h = self.req("/static/banner.css", dn=None)
        self.assertEqual(css, b".banner{background:#502b85;color:#ffffff}\n")
        off = sp.Config({"PORTAL_BANNER": '{"on": false, "text": "SECRET"}'})
        self.assertNotIn('class="banner"', sp.shell(off, "T", "<h1>x</h1>"))
        bad = sp.Config({"PORTAL_BANNER": {"on": True, "text": "X", "bg": "red;}*{x:y", "fg": "#fff"}})
        self.assertEqual((bad.banner["bg"], bad.banner["fg"]), ("#4d4d4d", "#ffffff"))      # never CSS from a setting as is

    def test_15_who_may_see_a_job(self):
        jid = self.post("/api/actions/act_triage", {"vars": {"target": "web01.example.mil", "report_job": "907",
                                                             "finding_id": "eventlog:disk-errors"}})[1]["job_id"]
        nothing = json.loads(self.req("/api/jobs/999999")[1])["error"].replace("999999", "%d" % jid)
        for dn in (RROE,):                                       # not on act_triage's who, not an approver, not its starter
            code, raw, _ = self.req("/api/jobs/%d" % jid, dn=dn)
            self.assertEqual((code, json.loads(raw)["error"]), (404, nothing))      # as a job that is not there
            self.assertEqual(self.req("/jobs/%d" % jid, dn=dn)[0], 404)
        for dn in (JDOE, APPR, PPOE):                            # the starter, an approver, a person its who allows
            self.assertEqual(self.req("/api/jobs/%d" % jid, dn=dn)[0], 200, dn)
            self.assertEqual(self.req("/jobs/%d" % jid, dn=dn)[0], 200, dn)
        end = time.time() + 15
        while time.time() < end and not json.loads(self.req("/api/jobs/%d" % jid)[1]).get("done"):
            time.sleep(0.3)
        child = json.loads(self.req("/api/jobs/%d" % jid)[1])["steps"][0]["job"]
        self.assertEqual(self.req("/api/jobs/%d?via=%d" % (child, jid), dn=RROE)[0], 404)       # its steps too
        self.assertEqual(self.req("/jobs/%d?via=%d" % (child, jid), dn=RROE)[0], 404)
        self.assertEqual(self.req("/api/jobs/%d?via=%d" % (child, jid), dn=PPOE)[0], 200)
        other = self.post("/api/actions/stig_recheck", {"vars": {"target": "web03.example.mil"}})[1]["job_id"]
        self.assertEqual(self.req("/api/jobs/%d" % other, dn=RROE)[0], 200)       # stig_recheck: anyone (portal_requesters [])

    def test_16_approvals_by_the_same_rule(self):
        wf = self.post("/api/actions/ckl_import", {"vars": {}})[1]["job_id"]
        seen = lambda dn: [a["workflow_job"] for a in json.loads(self.req("/api/approvals", dn=dn)[1])["approvals"]]  # noqa: E731
        self.assertIn(wf, seen(RROE))                             # ckl_import: anyone may run it, so anyone sees it
        self.portal.actions["ckl_import"]["who"] = ["DOE.JANE.Q.1234567890"]
        try:
            self.assertNotIn(wf, seen(RROE))
            self.assertIn(wf, seen(JDOE))
            self.assertIn(wf, seen(APPR))
        finally:
            self.portal.actions["ckl_import"]["who"] = None

    def test_17_the_post_checks_are_exact(self):
        body = {"vars": {"target": "web03.example.mil"}}
        self.portal.recent.pop("DOE.JANE.Q.1234567890", None)
        self.assertEqual(self.post("/api/actions/stig_recheck", body, ctype="application/json; charset=utf-8")[0], 201)
        for ctype in ("application/jsonx", "application/json-patch+json", "text/plain; application/json"):
            self.assertEqual(self.post("/api/actions/stig_recheck", body, ctype=ctype)[0], 403, ctype)
        for origin in ("https://portal.example.mil/", "https://portal.example.mil/portal/x", "https://user@portal.example.mil",
                       "https://portal.example.mil:8443", "https://portal.example.mil.evil.example.mil"):
            self.assertEqual(self.post("/api/actions/stig_recheck", body, origin=origin)[0], 403, origin)
        self.portal.recent.pop("DOE.JANE.Q.1234567890", None)

    def test_18_not_twice(self):
        """part C: a run of the same action for the same finding still running - the confirm page links to it, Run is off,
        a second launch is refused (409); once it is done, a new one may start."""
        self.portal.recent.pop("DOE.JANE.Q.1234567890", None)
        vals = {"target": "web01.example.mil", "report_job": "907", "finding_id": "fapolicyd:web01.example.mil:denials"}
        code, r = self.post("/api/actions/act_triage", {"vars": vals})
        self.assertEqual(code, 201, r)
        first = r["job_id"]
        q = "/actions/act_triage?target=web01.example.mil&report_job=907&finding_id=fapolicyd%3Aweb01.example.mil%3Adenials"
        t = self.req(q)[1].decode()
        self.assertIn("Running already.", t)
        self.assertIn('href="/portal/jobs/%d"' % first, t)
        self.assertIn(" disabled>Run", t)
        code, r = self.post("/api/actions/act_triage", {"vars": vals})
        self.assertEqual((code, r.get("job_id")), (409, first))
        self.assertIn("is running this already", r["error"])
        other = dict(vals, finding_id="fapolicyd:web01.example.mil:other")
        self.assertEqual(self.post("/api/actions/act_triage", {"vars": other})[0], 201, "another finding: not the same run")
        end = time.time() + 20
        while time.time() < end and not json.loads(self.req("/api/jobs/%d" % first)[1]).get("done"):
            time.sleep(0.3)
        self.assertNotIn("Running already.", self.req(q)[1].decode())
        self.assertEqual(self.post("/api/actions/act_triage", {"vars": vals})[0], 201, "done: a new run may start")
        self.portal.recent.pop("DOE.JANE.Q.1234567890", None)

    def test_18c_the_backlog(self):
        """1.68.2: socketserver listens with a backlog of 5. With 8 connections at once (test_18b) one was now and then
        reset before it was read (a red CI run; 17 resets in 1500 rounds of 8 POSTs at once, none with 128). The portal's
        server holds 128, and it is the one serve() and this test run."""
        self.assertGreaterEqual(sp.PortalServer.request_queue_size, 128)
        self.assertTrue(sp.PortalServer.daemon_threads)
        self.assertIsInstance(self.srv, sp.PortalServer)
        with open(sp.__file__) as fh:
            self.assertIn("httpd = PortalServer((", fh.read())

    def test_18b_not_twice_at_once(self):
        """1.67.0 review: 8 launches of the same action for the same finding at the same moment (report_job 907, 0907,
        00907 - one job) start one run; the others are 409 and use no slot of the hour."""
        import threading as _th
        self.portal.recent.pop("DOE.JANE.Q.1234567890", None)
        fid = "fapolicyd:web01.example.mil:race"
        res, go = [], _th.Event()

        def one(rj):
            go.wait()
            res.append(self.post("/api/actions/act_triage", {"vars": {"target": "web01.example.mil", "report_job": rj, "finding_id": fid}}))
        ts = [_th.Thread(target=one, args=(rj,)) for rj in ("907", "0907", "00907", "907", "0907", "00907", "907", "907")]
        for x in ts:
            x.start()
        go.set()
        for x in ts:
            x.join(30)
        codes = sorted(c for c, _r in res)
        self.assertEqual(codes, [201] + [409] * 7, res)
        first = next(r["job_id"] for c, r in res if c == 201)
        self.assertTrue(all(r.get("job_id") == first for c, r in res if c == 409))
        launched = [r for r in self.portal.log.read("launch") if (r.get("values") or {}).get("finding_id") == fid]
        self.assertEqual([r["values"]["report_job"] for r in launched], ["907"])          # one launch, report_job as a number
        self.assertEqual(len(self.portal.recent.get("DOE.JANE.Q.1234567890", [])), 1)    # the 409s gave their slot back
        end = time.time() + 20
        while time.time() < end and not json.loads(self.req("/api/jobs/%d" % first)[1]).get("done"):
            time.sleep(0.3)
        self.portal.recent.pop("DOE.JANE.Q.1234567890", None)

    def test_19_the_result_and_the_next_action(self):
        """part C: a fix's portal_result at the top of its page (Fixed); an action that only reads (fixes: false, ACT
        triage) is Not checked whatever its artifact says; the action ACT names next, only when it is listed and its kinds
        have this finding's kind."""
        def wait(jid):
            end, v = time.time() + 20, {}
            while time.time() < end:
                v = json.loads(self.req("/api/jobs/%d" % jid)[1])
                if v.get("done"):
                    break
                time.sleep(0.3)
            return v
        self.portal.recent.pop("DOE.JANE.Q.1234567890", None)
        clean = {"target": "web01.example.mil", "report_job": "907", "finding_id": "disk_full:web01.example.mil:/var"}
        v = wait(self.post("/api/actions/disk_cleanup", {"vars": clean})[1]["job_id"])
        self.assertEqual(v["result"], {"fixed": "true", "before": "93% full", "after": "71% full", "summary": "cleaned /var/tmp",
                                       "finding_id": "disk_full:web01.example.mil:/var"})
        self.portal.actions["act_next"] = dict(self.portal.actions["stig_recheck"], name="act_next", title="Allow this",
                                               button="Allow this", kinds=["fapolicyd"])
        try:
            vals = {"target": "web01.example.mil", "report_job": "907", "finding_id": "fapolicyd:web01.example.mil:r19"}
            jid = self.post("/api/actions/act_triage", {"vars": vals})[1]["job_id"]
            # the stand-in AAP takes next_action from the launch's extra vars: put it there as ACT would in its artifact
            fake_aap.STATE["jobs"][[n for n in fake_aap.STATE["jobs"][jid]["nodes"]][-1]]["extra_vars"]["next_action"] = "act_next"
            v = wait(jid)
            self.assertEqual((v["result"]["fixed"], v["result"]["before"], v["result"]["after"], v["result"]["summary"]),
                             ("unknown", "", "", "This action only reads: it changed nothing."),
                             "ACT triage only reads: never Fixed, whatever its artifact says")
            self.assertEqual(v["next"]["url"], "/portal/actions/act_next?finding_id=fapolicyd%3Aweb01.example.mil%3Ar19&report_job=907"
                                               "&target=web01.example.mil")
            self.portal.actions["act_next"]["kinds"] = ["disk_full"]          # not this finding's kind: no button
            self.assertNotIn("next", json.loads(self.req("/api/jobs/%d" % jid)[1]))
        finally:
            self.portal.actions.pop("act_next", None)
            self.portal.recent.pop("DOE.JANE.Q.1234567890", None)

    def test_11_log_file(self):
        path = os.path.join(self.cfg.data, "portal-log.jsonl")
        with open(path) as fh:
            recs = [json.loads(x) for x in fh]
        self.assertTrue(recs)
        for r in recs:
            self.assertNotIn("fake-token", json.dumps(r))
        kinds = set(r["event"] for r in recs)
        self.assertTrue({"launch", "refused", "decision"} <= kinds, kinds)
        self.assertEqual(oct(os.stat(os.path.join(self.cfg.data, "page-key")).st_mode & 0o777), "0o600")

    # ---- 1.68.1: a STIG scorecard link that opens the review (stigman_link_style: open)
    def test_stigman_go_page(self):
        """No sign-in needed (it carries ids only; STIG Manager checks who may read what): the target as data, the script
        that keeps it in the tab, a link to STIG Manager's plain address; no inline script."""
        q = "c=2&b=VMware+Aria+Operations+8.x+Application&a=11&r=SV-257985r1069364_rule"
        code, raw, h = self.req("/stigman/go?" + q, dn=None, verify="NONE")
        page = raw.decode()
        self.assertEqual(code, 200)
        self.assertIn('<meta name="sa-stigman-target" content="%s">' % sp.esc(json.dumps(
            {"a": "11", "b": "VMware Aria Operations 8.x Application", "c": "2", "r": "SV-257985r1069364_rule"}, sort_keys=True)), page)
        self.assertIn('<meta name="sa-stigman-to" content="/">', page)
        self.assertIn('<script src="/portal/static/stigman-go.js"></script>', page)
        self.assertNotIn("<script>", page)
        self.assertIn("script-src 'self'", h["Content-Security-Policy"])
        code, raw, h = self.req("/stigman/go?c=2&b=RHEL_9_STIG", dn=None)              # a collection review, no rule
        self.assertIn('content="{&quot;b&quot;: &quot;RHEL_9_STIG&quot;, &quot;c&quot;: &quot;2&quot;}"', raw.decode())
        code, raw, h = self.req("/stigman/go?c=2", dn=None)                          # 1.69.2: the collection itself
        self.assertEqual(code, 200)
        self.assertIn('content="{&quot;c&quot;: &quot;2&quot;}"', raw.decode())
        self.assertEqual(sp.stigman_target("c=2&a=&r="), {"c": "2"})

    def test_stigman_go_refuses_what_cannot_pass(self):
        """A value that cannot pass, a repeated or unknown one, no collection, or an asset or a rule without its STIG:
        straight to STIG Manager's plain address (302), no page, nothing kept."""
        for q in ("", "c=", "c=2&b=", "c=2&a=11", "c=2&r=SV-257985r1069364_rule", "c=2&a=11&r=SV-1", "b=RHEL_9_STIG", "c=2&c=3", "c=x&b=RHEL_9_STIG", "c=2&b=%3Cscript%3E", "c=2&b=RHEL_9_STIG&a=1%202",
                  "c=2&b=RHEL_9_STIG&r=a%2Fb", "c=2&c=3&b=RHEL_9_STIG", "c=2&b=RHEL_9_STIG&x=1", "c=2&b=RHEL_9_STIG&r=javascript:x",
                  "c=1234567890123&b=RHEL_9_STIG"):
            code, raw, h = self.req("/stigman/go?" + q, dn=None)
            self.assertEqual((code, h.get("Location")), (302, "/"), q)
        self.assertEqual(sp.stigman_target("c=2&b=RHEL_9_STIG&a=&r="), {"c": "2", "b": "RHEL_9_STIG"})
        # 1.68.1b (the tester's break-it pass): more than 10 fields - parse_qs raises; a value with a last line end
        # ("$" matched before it) - both refused, answered with the plain address (not a traceback and no answer)
        many = "c=2&b=RHEL_9_STIG&" + "&".join("x%d=1" % i for i in range(12))
        self.assertIsNone(sp.stigman_target(many))
        for q in (many, "c=12%0A&b=RHEL_9_STIG", "c=2&b=RHEL_9_STIG%0A", "c=2&b=RHEL_9_STIG&a=11%0A", "c=2&b=RHEL_9_STIG&r=SV-1%0A",
                  "c=2%0D&b=RHEL_9_STIG"):
            code, raw, h = self.req("/stigman/go?" + q, dn=None)
            self.assertEqual((code, h.get("Location")), (302, "/"), q)
        sys.path.insert(0, os.path.join(ROOT, "plugins", "filter"))       # the scorecard makes the links with the same rules
        import stig_scorecard_filters as ssf
        self.assertEqual([sp.STIGMAN_GO[k].pattern for k in ("c", "a", "b", "r")],
                         [ssf.GO_ID.pattern, ssf.GO_ID.pattern, ssf.GO_BENCH.pattern, ssf.GO_RULE.pattern])

    def test_stigman_scripts_without_sign_in(self):
        for name in ("stigman-go.js", "stigman-open.js"):
            code, raw, h = self.req("/static/" + name, dn=None)
            self.assertEqual((code, h["Content-Type"]), (200, "text/javascript; charset=utf-8"), name)
            self.assertIn(b"sa_stigman_open", raw)

    def test_stigman_path_setting(self):
        for v, want in (("", "/"), ("/", "/"), ("/stigman", "/stigman/"), ("stigman/", "/stigman/"), ("//evil.example.mil", "/"),
                        ("/a/../b", "/"), ("/x y", "/")):
            self.assertEqual(sp.Config({"PORTAL_STIGMAN_PATH": v}).stigman_path, want, v)


class Plain(unittest.TestCase):
    """1.78.0: a running job in plain words - the event lines, the cause classes, what a job has done so far, the "Now" line."""

    def e(self, ev, host="web01", task="Disk | Checking free space", changed=False, res=None, counter=1):
        return {"counter": counter, "event": ev, "host_name": host, "changed": changed, "event_data": {"task": task, "host": host, "res": res or {}}}

    def test_the_lines(self):
        self.assertEqual(sp.event_line(self.e("runner_on_ok")), {"k": "ok", "host": "web01", "text": "Checking free space"})
        self.assertEqual(sp.event_line(self.e("runner_on_ok", changed=True))["text"], "Checking free space (changed something)")
        self.assertEqual(sp.event_line(self.e("runner_on_failed", res={"msg": "boom"})), {"k": "bad", "host": "web01", "text": "Checking free space: failed"})
        self.assertEqual(sp.event_line(self.e("runner_on_failed", res={"msg": "Connection timed out"}))["text"], "Checking free space: timed out")
        self.assertEqual(sp.event_line(self.e("runner_on_unreachable", res={"msg": "ssh: connect to host web01 port 22"})),
                         {"k": "unreach", "host": "web01", "text": "could not connect"})
        self.assertEqual(sp.event_line(self.e("runner_on_skipped"))["text"], "Checking free space (skipped)")
        self.assertIsNone(sp.event_line(self.e("playbook_on_task_start")))
        self.assertIsNone(sp.event_line(self.e("runner_on_ok", host="")))
        self.assertIsNone(sp.event_line(self.e("runner_on_ok", task="")))

    def test_a_failed_line_never_carries_the_modules_message(self):
        for res in ({"msg": "password=Hunter2 token=abc"}, {"msg": "x" * 500}, {"msg": "Authorization: Bearer abc.def"}):
            line = sp.event_line(self.e("runner_on_failed", res=res))
            self.assertEqual(line["text"], "Checking free space: failed")
            self.assertNotIn("Hunter2", json.dumps(line))

    def test_the_task_name(self):
        self.assertEqual(sp.task_what("Disk | Checking free space"), "Checking free space")
        self.assertEqual(sp.task_what("Verify | AAP | what"), "AAP | what")
        self.assertEqual(sp.task_what("No area here"), "No area here")
        self.assertEqual(sp.task_what("  A  |   spaced   out "), "spaced out")
        self.assertEqual(len(sp.task_what("A | " + "x" * 300)), 80)

    def test_what_a_job_has_done(self):
        p = sp.Progress()
        p.feed([{"counter": 1, "event": "playbook_on_play_start", "event_data": {}},
                {"counter": 2, "event": "playbook_on_task_start", "event_data": {"task": "Disk | Checking free space"}},
                self.e("runner_on_ok", "web01", counter=3), self.e("runner_on_ok", "web02", counter=4),
                {"counter": 5, "event": "playbook_on_task_start", "event_data": {"task": "Time | Checking the clock"}},
                self.e("runner_on_failed", "web02", "Time | Checking the clock", counter=6, res={"msg": "x"}),
                self.e("runner_on_unreachable", "web03", "Time | Checking the clock", counter=7)])
        v = p.view(12, False)
        self.assertEqual((v["step"], v["what"], p.counter), (2, "Checking the clock", 7))
        self.assertEqual([(h["host"], h["state"]) for h in v["hosts"]], [("web01", "running"), ("web02", "failed"), ("web03", "unreachable")])
        self.assertEqual((v["hosts_done"], v["hosts_n"]), (2, 3))
        self.assertEqual([x["host"] for x in v["lines"]], ["web03", "web02", "web02", "web01"])         # newest first
        self.assertEqual(v["first_bad"], {"host": "web02", "what": "Checking the clock", "cause": "failed"})
        p.feed([{"counter": 8, "event": "playbook_on_stats", "event_data": {}}])
        self.assertEqual([h["state"] for h in p.view(12, True)["hosts"]], ["done", "failed", "unreachable"])
        q = sp.Progress()
        q.feed([self.e("runner_on_ok", "h%d" % i, counter=i + 1) for i in range(20)])
        self.assertEqual(len(q.view(None, False)["lines"]), 8)                                          # the last eight only

    def test_the_now_line_and_the_bar(self):
        self.assertEqual(sp.now_line(4, 12, "Checking free space", "web01"), "Step 4 of about 12 - Checking free space on web01")
        self.assertEqual(sp.now_line(4, None, "Checking free space", ""), "Step 4 - Checking free space")
        self.assertEqual(sp.now_line(14, 12, "More than guessed", "web01"), "Step 14 - More than guessed on web01")        # past the estimate: no "about"
        self.assertEqual((sp.pct(4, 12, False), sp.pct(4, None, False), sp.pct(11, 12, True), sp.pct(30, 12, False)), (33, None, 100, 95))



class Found(unittest.TestCase):
    """1.78.0: the actions AAP itself offers - a template the account may Execute that carries the label."""
    SECRET = "Hunter2-not-a-real-password"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        fake_aap.RUN = 1.5
        fake_aap.LOG = os.path.join(cls.tmp, "aap.jsonl")
        cls.keep = {k: list(v) for k, v in fake_aap.STATE["templates"].items()}
        cls.aap = fake_aap.serve(0, stop=True)
        spec = [
            {"variable": "ticket", "question_name": "Ticket number", "type": "text", "required": True, "min": 3, "max": 12},
            {"variable": "why", "question_name": "Why", "type": "textarea", "required": False, "default": "routine"},
            {"variable": "count", "question_name": "How many", "type": "integer", "required": True, "min": 1, "max": 5, "default": 2},
            {"variable": "ratio", "question_name": "Ratio", "type": "float", "required": False, "min": 0, "max": 1},
            {"variable": "env", "question_name": "Environment", "type": "multiplechoice", "required": True, "choices": "dev\ntest\nprod", "default": "dev"},
            {"variable": "tags", "question_name": "Tags", "type": "multiselect", "required": False, "choices": "a\nb\nc", "default": "a\nb"},
            {"variable": "pw", "question_name": "Secret", "type": "password", "required": True, "max": 40},
        ]
        who_spec = [{"variable": "portal_requested_by", "question_name": "Started by", "type": "text", "required": False}]
        fake_aap.add_template("job_templates", "Knows who started it", ["portal"], who_spec)
        cls.t_full = fake_aap.add_template("job_templates", "Health check - Linux", ["portal", "portal-area:STIG"], spec, limit=True,
                                           description="Checks   one host\nand more")
        fake_aap.add_template("job_templates", "Executable but not labelled", ["other"])
        fake_aap.add_template("job_templates", "Labelled but not executable", ["portal"], execute=False)
        fake_aap.add_template("workflow_job_templates", "Weekly cleanup", ["portal"])
        fake_aap.add_template("workflow_job_templates", "Approved by label", ["portal", "portal-approval"])
        fake_aap.add_template("job_templates", "Job marked for approval", ["portal", "portal-approval"])
        fake_aap.add_template("job_templates", "Odd survey", ["portal"], [{"variable": "x", "type": "file"}])
        fake_aap.add_template("job_templates", "Same as a built-in", ["portal"], tid=7777, many_labels=0)
        fake_aap.STATE["templates"]["job_templates"][-1]["name"] = "Linux - recheck one host"
        fake_aap.add_template("job_templates", "Needs an inventory", ["portal"], needs_inventory=True)
        fake_aap.add_template("job_templates", "Late label", [], many_labels=12)
        fake_aap.STATE["templates"]["job_templates"][-1]["labels"].append("portal")
        with open(os.path.join(cls.tmp, "actions.json"), "w") as fh:
            json.dump({"actions": ACTIONS}, fh)
        cls.cfg = sp.Config({"PORTAL_DATA": os.path.join(cls.tmp, "data"), "PORTAL_REPORTS": cls.tmp,
                             "PORTAL_ACTIONS": os.path.join(cls.tmp, "actions.json"),
                             "PORTAL_AAP_URL": "http://127.0.0.1:%d" % cls.aap.server_address[1], "PORTAL_AAP_TOKEN": "fake-token",
                             "PORTAL_INVENTORY": "Site hosts", "PORTAL_REQUESTERS": "[]", "PORTAL_LAUNCHES_PER_HOUR": "50",
                             "PORTAL_APPROVAL_REQUIRED": '["Weekly cleanup"]'})
        sp.sys.stdout = open(os.devnull, "w")
        cls.portal = sp.Portal(cls.cfg)
        cls.portal.aap.connect()
        cls.portal.refresh(force=True)
        cls.cn = "DOE.JANE.Q.1234567890"
        cls.raw_launch = cls.portal.launch

        def launch(cn, ip, name, sent, sig=None, _o=cls.raw_launch):         # a found template's launch needs its form's signature: the tests send the card's own
            a = cls.portal.actions.get(name)
            if sig is None and a and a.get("discovered") and not getattr(cls, "no_sig", False):
                sig = a["sig"]
            return _o(cn, ip, name, sent, sig)
        cls.portal.launch = launch
        cls.srv = sp.PortalServer(("127.0.0.1", 0), sp.make_handler(cls.cfg, cls.portal, os.path.join(FILES, "static")))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.host = "127.0.0.1:%d" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.aap.shutdown()
        fake_aap.STATE["templates"] = cls.keep
        sp.sys.stdout = sys.__stdout__
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def nm(self, title):
        """The action's name (jt_<AAP id> or wf_<AAP id>) of the template with this title."""
        return next((k for k, a in self.portal.actions.items() if a["title"] == title), "gone")

    def gets(self):
        with open(fake_aap.LOG) as fh:
            return [json.loads(x) for x in fh if "execute_role" in x]

    def test_01_label_and_execute(self):
        found = self.portal.found
        self.assertEqual(sorted(a["title"] for a in found["actions"].values()),
                         ["Approved by label", "Health check - Linux", "Knows who started it", "Late label", "Weekly cleanup"])       # not the unlabelled, not the one it may not run
        why = dict(found["skipped"])
        self.assertEqual(why["Executable but not labelled"], "no label portal")
        self.assertNotIn("Labelled but not executable", why)                              # AAP never listed it
        self.assertIn("survey type file is not handled", why["Odd survey"])
        self.assertIn("inventory", why["Needs an inventory"])
        self.assertIn("Late label", [a["title"] for a in self.portal.actions.values()])  # the label past the 10 a row carries

    def test_02_card(self):
        a = next(a for a in self.portal.actions.values() if a["title"] == "Health check - Linux")
        self.assertEqual((a["area"], a["summary"], a["approval"]), ("STIG", "Checks one host and more", False))
        self.assertRegex(a["name"], r"^jt_[0-9]+$")
        w = next(a for a in self.portal.actions.values() if a["title"] == "Weekly cleanup")
        self.assertEqual((w["area"], w["approval"], w["aap"]["type"]), ("", True, "workflow"))     # area Other; approval from portal_approval_required
        lab = next(a for a in self.portal.actions.values() if a["title"] == "Approved by label")
        self.assertTrue(lab["approval"])                                                             # approval from the label, on a workflow
        self.assertEqual(self.nm("Job marked for approval"), "gone")                                 # a job template starts at once: nothing could hold it
        self.assertIn("starts at once", dict(self.portal.found["skipped"])["Job marked for approval"])

    def test_03_survey_to_form(self):
        a = self.portal.actions[self.nm("Health check - Linux")]
        self.assertEqual(list(a["vars"]), ["portal_limit", "ticket", "why", "count", "ratio", "env", "tags", "pw"])
        v = a["vars"]
        self.assertEqual((v["ticket"]["kind"], v["ticket"]["min"], v["ticket"]["max"], v["why"]["multiline"]), ("text", 3, 12, True))
        self.assertEqual((v["count"]["kind"], v["count"]["default"], v["env"]["kind"], v["tags"]["kind"], v["tags"]["default"]),
                         ("integer", 2, "choice", "multichoice", ["a", "b"]))
        self.assertEqual((v["pw"]["kind"], v["pw"]["required"], v["pw"]["default"]), ("password", True, None))
        html_ = sp.action_page(self.cfg, self.portal, self.cn, self.nm("Health check - Linux"), {})
        for needle in ('type="password"', 'type="number"', 'type="checkbox" name="tags"', "<textarea", "Ticket number *", 'data-picker="portal_limit"'):
            self.assertIn(needle, html_)
        self.assertNotIn("is required", html_)                                           # an empty form says nothing yet

    def test_04_rules_on_the_server(self):
        a = self.portal.actions[self.nm("Health check - Linux")]
        ok = {"portal_limit": "web01.example.mil, web03.example.mil", "ticket": "INC001", "count": "3", "env": "test", "tags": ["c", "a"], "pw": "x"}
        val, p = rules.check_values(a, ok, self.portal.aap.host_known)
        self.assertEqual(p, {})
        self.assertEqual((val["count"], val["tags"], val["portal_limit"], val["why"]), (3, ["a", "c"], ["web01.example.mil", "web03.example.mil"], "routine"))
        for bad, need in ((dict(ok, count="9"), "count"), (dict(ok, count="2.5"), "count"), (dict(ok, ticket="ab"), "ticket"),
                          (dict(ok, ticket="x" * 13), "ticket"), (dict(ok, env="staging"), "env"), (dict(ok, tags=["z"]), "tags"),
                          (dict(ok, ratio="1.5"), "ratio"), (dict(ok, ratio="nan"), "ratio"), (dict(ok, pw=""), "pw"), (dict(ok, pw="y" * 41), "pw"),
                          (dict(ok, portal_limit="web01.example.mil,x,y,z,w,v"), "portal_limit"), (dict(ok, portal_limit="nohost.example.mil"), "portal_limit"),
                          (dict(ok, ticket="{{ 7*7 }}"), "ticket"), (dict(ok, nothing_asked="1"), "")):
            self.assertIn(need, rules.check_values(a, bad, self.portal.aap.host_known)[1], bad)

    def test_05_launch_sends_only_the_survey(self):
        code, out = self.portal.launch(self.cn, "192.0.2.5", self.nm("Health check - Linux"),
                                       {"portal_limit": ["web01.example.mil", "web03.example.mil"], "ticket": "INC002", "count": 4, "env": "prod", "pw": self.SECRET})
        self.assertEqual(code, 201, out)
        with open(fake_aap.LOG) as fh:
            sent = [json.loads(x) for x in fh if '"POST"' in x and "/launch/" in x][-1]["body"]
        self.assertEqual(sent["limit"], "web01.example.mil,web03.example.mil")
        self.assertEqual(sorted(sent["extra_vars"]), ["count", "env", "pw", "tags", "ticket", "why"])     # no portal_requested_by, no portal_limit
        self.assertEqual((sent["extra_vars"]["count"], sent["extra_vars"]["pw"]), (4, self.SECRET))

    def test_05b_started_by_the_card(self):
        a = self.portal.actions[self.nm("Knows who started it")]
        self.assertTrue(a["started_by"])
        self.assertNotIn("portal_requested_by", a["vars"])                               # not shown, not editable
        self.assertIn("Started by", sp.action_page(self.cfg, self.portal, self.cn, self.nm("Knows who started it"), {}))
        self.assertFalse(self.portal.actions[self.nm("Health check - Linux")]["started_by"])
        code, out = self.portal.launch(self.cn, "192.0.2.5", self.nm("Knows who started it"), {"portal_requested_by": "SMITH.JOHN.A.1234567891"})
        self.assertEqual(code, 201, out)
        with open(fake_aap.LOG) as fh:
            sent = [json.loads(x) for x in fh if '"POST"' in x and "/launch/" in x][-1]["body"]
        self.assertEqual(sent["extra_vars"], {"portal_requested_by": self.cn})           # the posted value ignored, the login put in

    def test_06_password_never_shown(self):
        self.portal.launch(self.cn, "192.0.2.5", self.nm("Health check - Linux"), {"portal_limit": "web01.example.mil", "ticket": "INC003", "env": "dev", "pw": self.SECRET})
        self.portal.launch(self.cn, "192.0.2.5", self.nm("Health check - Linux"), {"portal_limit": "web01.example.mil", "ticket": "INC004", "env": "nope", "pw": self.SECRET})
        with open(self.portal.log.path) as fh:
            self.assertNotIn(self.SECRET, fh.read())
        rec = self.portal.log.read("launch")[-1]
        self.assertEqual(rec["values"]["pw"], rules.HIDDEN)
        jid = int(rec["job_id"])
        code, view = self.portal.job(jid, None, self.cn)
        self.assertNotIn(self.SECRET, json.dumps(view))
        page = sp.action_page(self.cfg, self.portal, self.cn, self.nm("Health check - Linux"), {"pw": [self.SECRET], "ticket": ["INC9"], "env": ["nope"]})
        self.assertNotIn(self.SECRET, page)
        self.assertIn("INC9", page)                                                       # the others are filled in; the password never
        self.assertNotIn(self.SECRET, json.dumps(self.portal.approvals(self.cn)))

    def test_07_built_in_wins(self):
        self.assertEqual([c for c in self.portal.found["clashes"]], [("Linux - recheck one host", "stig_recheck")])
        self.assertEqual(sum(1 for a in self.portal.actions.values() if a["aap"]["name"] == "Linux - recheck one host"), 1)
        self.assertFalse(self.portal.actions["stig_recheck"].get("discovered"))

    def test_08_cache_and_paging(self):
        self.portal.refresh(force=True)
        n = len(self.gets())
        self.portal.refresh()
        self.portal.refresh()
        self.assertEqual(len(self.gets()), n)                                            # kept: no new asking
        self.portal.found_at -= self.cfg.discover_seconds + 1
        self.portal.refresh()
        self.assertGreater(len(self.gets()), n)                                          # kept for portal_discover_seconds only
        fake_aap.PAGE_CAP = 2
        try:
            before = len(self.gets())
            self.portal.refresh(force=True)
            self.assertGreaterEqual(len(self.gets()) - before, 4)   # 3 pages of 2 job templates, 1 of workflows                        # every page of both lists, followed
            self.assertEqual(len(self.portal.found["actions"]), 5)
            self.assertEqual(self.portal.found["seen"], 10)
        finally:
            fake_aap.PAGE_CAP = 200

    def test_09_execute_or_label_removed(self):
        tpl = next(t for t in fake_aap.STATE["templates"]["workflow_job_templates"] if t["name"] == "Weekly cleanup")
        wk = self.nm("Weekly cleanup")
        tpl["execute"] = False
        self.portal.refresh(force=True)
        self.assertNotIn(wk, self.portal.actions)
        code, out = self.portal.launch(self.cn, "192.0.2.5", wk, {})
        self.assertEqual(code, 404)
        tpl["execute"], tpl["labels"] = True, []
        self.portal.refresh(force=True)
        self.assertNotIn(wk, self.portal.actions)
        tpl["labels"] = ["portal"]
        self.portal.refresh(force=True)
        self.assertIn(wk, self.portal.actions)

    def test_10_aap_down_keeps_what_was_found(self):
        keep = self.portal.aap.api.base
        self.portal.aap.api.base = "api/nowhere/v2/"
        try:
            self.portal.refresh(force=True)
        finally:
            self.portal.aap.api.base = keep
        self.assertIn(self.nm("Health check - Linux"), self.portal.actions)
        self.assertTrue(self.portal.found["error"])

    def test_11_deploy_check(self):
        self.portal.refresh(force=True)
        res = sp.discover_check(self.portal)
        self.assertEqual(sorted(x["title"] for x in res["shown"]), ["Approved by label", "Health check - Linux", "Knows who started it", "Late label", "Weekly cleanup"])
        self.assertTrue(any("Odd survey" == n and "not handled" in w for n, w in res["skipped"]))
        self.assertEqual(res["clashes"], [["Linux - recheck one host", "stig_recheck"]])
        self.assertGreaterEqual(res["unlabelled"], 1)
        self.assertNotIn(self.SECRET, json.dumps(res))

    def test_12_off_without_a_label(self):
        cfg = sp.Config(dict(self.cfg.env, PORTAL_LABEL=""))
        self.assertEqual(cfg.label, "")                                                   # an empty label is off, not the default
        self.assertEqual(sp.Config(dict(self.cfg.env)).label if "PORTAL_LABEL" not in self.cfg.env else "portal", "portal")
        p = sp.Portal(cfg, aap=self.portal.aap, log=sp.Log(""))
        p.refresh(force=True)
        self.assertEqual(p.found["actions"], {})

    # ---- Codex's review of 1780-wip
    def test_13_no_inventory_no_limit(self):
        cfg = sp.Config(dict(self.cfg.env, PORTAL_INVENTORY="x"))
        cfg.inventory = ""
        p = sp.Portal(cfg, aap=sp.Aap(cfg), log=sp.Log(""))
        p.aap.connect()
        p.refresh(force=True)
        self.assertNotIn("Health check - Linux", [a["title"] for a in p.actions.values()])          # it asks for a Limit
        self.assertIn("Weekly cleanup", [a["title"] for a in p.actions.values()])                  # it does not
        why = dict(p.found["skipped"])
        self.assertIn("needs portal_inventory for its Limit", why["Health check - Linux"])
        a = self.portal.actions[self.nm("Health check - Linux")]
        v, pr = rules.check_values(a, {"portal_limit": "web01.example.mil", "ticket": "INC1", "env": "dev", "pw": "x"}, None)
        self.assertIn("portal_inventory is not set", pr["portal_limit"])                           # and the rule refuses too

    def test_14_the_id_never_moves(self):
        a1 = fake_aap.add_template("job_templates", "A B", ["portal"])
        a2 = fake_aap.add_template("job_templates", "A-B", ["portal"])
        self.portal.refresh(force=True)
        n1, n2 = self.nm("A B"), self.nm("A-B")
        self.assertEqual((n1, n2), ("jt_%d" % a1, "jt_%d" % a2))
        tpl = next(t for t in fake_aap.STATE["templates"]["job_templates"] if t["id"] == a1)
        tpl["labels"] = []
        self.portal.refresh(force=True)
        self.assertNotIn(n1, self.portal.actions)
        self.assertEqual(self.nm("A-B"), n2)                                                      # the other did not take the first one's id

    def test_15_changed_since_the_form(self):
        tid = fake_aap.add_template("job_templates", "Will change", ["portal"], [{"variable": "n", "type": "integer", "required": False}])
        self.portal.refresh(force=True)
        name = self.nm("Will change")
        tpl = next(t for t in fake_aap.STATE["templates"]["job_templates"] if t["id"] == tid)
        tpl["spec"] = [{"variable": "n", "type": "text", "required": False}]                      # the survey changed after the form was drawn
        code, out = self.portal.launch(self.cn, "192.0.2.5", name, {"n": "3"})
        self.assertEqual(code, 409, out)
        self.assertIn("changed", out["error"])
        tpl["name"] = "Another job entirely"                                                      # the id now names another template
        code, out = self.portal.launch(self.cn, "192.0.2.5", name, {})
        self.assertEqual(code, 409, out)
        self.assertEqual(self.nm("Another job entirely"), name)                                   # the card follows AAP: the same id, now the new title

    def test_16_numbers_never_raise(self):
        a = self.portal.actions[self.nm("Health check - Linux")]
        ok = {"portal_limit": "web01.example.mil", "ticket": "INC1", "env": "dev", "pw": "x"}
        for bad in ("1e309", "nan", "inf", "-inf", "1" * 400, 1e309, float("nan"), [1], {"a": 1}, True, "0x10"):
            for var in ("count", "ratio"):
                v, pr = rules.check_values(a, dict(ok, **{var: bad}), self.portal.aap.host_known)
                if var == "count" or not isinstance(bad, str) or bad in ("nan", "inf", "-inf", "1e309"):
                    self.assertIn(var, pr, (var, bad))

    def test_17_hosts_must_be_text(self):
        a = self.portal.actions[self.nm("Health check - Linux")]
        ok = {"ticket": "INC1", "env": "dev", "pw": "x"}
        for bad in ([123], [None], [["a"]], 5, {"a": 1}, [True]):
            v, pr = rules.check_values(a, dict(ok, portal_limit=bad), self.portal.aap.host_known)
            self.assertEqual(pr.get("portal_limit"), "must be host names", bad)

    def test_18_an_incomplete_read_keeps_the_cache(self):
        before = dict(self.portal.found["actions"])
        keep = self.portal.aap.PAGES_MAX
        self.portal.aap.PAGES_MAX = 1
        fake_aap.PAGE_CAP = 2
        try:
            self.portal.refresh(force=True)
        finally:
            self.portal.aap.PAGES_MAX = keep
            fake_aap.PAGE_CAP = 200
        self.assertIn("could not read every template", self.portal.found["error"])
        self.assertEqual(sorted(self.portal.actions), sorted(set(self.portal.actions) | set(before)))
        self.assertIn(self.nm("Health check - Linux"), self.portal.actions)                        # what was found is kept
        self.portal.refresh(force=True)
        self.assertEqual(self.portal.found["error"], "")

    # ---- the shared picker: hosts and groups of the inventory
    def test_19_groups(self):
        hosts, why = self.portal.aap.expand(["web_servers"])
        self.assertEqual((hosts, why), (["web01.example.mil", "web03.example.mil"], ""))
        hosts, why = self.portal.aap.expand(["web01.example.mil", "web_servers", "failing"])
        self.assertEqual(hosts, ["web01.example.mil", "web03.example.mil", "fail01.example.mil"])      # unique, in order
        self.assertIn("not a host or a group", self.portal.aap.expand(["nothere.example.mil"])[1])
        self.assertIn("not a host or a group", self.portal.aap.expand(["web_servers", "web01.example.mil;id"])[1])
        a = self.portal.actions[self.nm("Health check - Linux")]
        ok = {"ticket": "INC1", "env": "dev", "pw": "x"}
        val, pr = rules.check_values(a, dict(ok, portal_limit=["web_servers"]), self.portal.aap.host_known, self.portal.aap.expand)
        self.assertEqual((pr, val["portal_limit"]), ({}, ["web_servers"]))                              # a group goes as its name
        val, pr = rules.check_values(a, dict(ok, portal_limit="nothere.example.mil"), self.portal.aap.host_known, self.portal.aap.expand)
        self.assertIn("not a host or a group", pr["portal_limit"])
        small = dict(a, vars=dict(a["vars"], portal_limit=dict(a["vars"]["portal_limit"], max=2)))
        val, pr = rules.check_values(small, dict(ok, portal_limit=["web_servers", "failing"]), None, self.portal.aap.expand)
        self.assertIn("takes 2 hosts at most (that is 3", pr["portal_limit"])                           # groups are counted by their hosts
        code, out = self.portal.launch(self.cn, "192.0.2.5", self.nm("Health check - Linux"), dict(ok, portal_limit=["web_servers", "failing"]))
        self.assertEqual(code, 201, out)
        with open(fake_aap.LOG) as fh:
            sent = [json.loads(x) for x in fh if '"POST"' in x and "/launch/" in x][-1]["body"]
        self.assertEqual(sent["limit"], "web01.example.mil,web03.example.mil,fail01.example.mil")      # the hosts the checks counted, never a group name

    def test_20_the_list(self):
        r = self.portal.aap.inventory_items("")
        self.assertEqual([(i["name"], i["kind"], i["count"]) for i in r["items"]],
                         [("fail01.example.mil", "host", 1), ("failing", "group", 1), ("web01.example.mil", "host", 1), ("web03.example.mil", "host", 1),
                          ("web_servers", "group", 2)])
        self.assertEqual([i["name"] for i in self.portal.aap.inventory_items("WEB_")["items"]], ["web_servers"])      # substring, any case
        r = self.portal.aap.inventory_items("", 2)
        self.assertEqual((len(r["items"]), r["matches"]), (2, 5))                                                        # the cap, and how many there are
        self.assertEqual(self.portal.aap.inventory_items("zzz")["items"], [])

    def test_21_findings_flow_expands_groups(self):
        a = dict(self.portal.actions["disk_cleanup"]) if "disk_cleanup" in self.portal.actions else None
        self.assertIsNotNone(a)
        step = sp.picker_route(self.cfg, self.portal, self.cn, "disk_cleanup", {"hosts": ["web_servers"]})
        self.assertEqual(step[0], "page")
        self.assertIn("web01.example.mil", step[1])                                                                       # the group's hosts, one section each
        self.assertIn("web03.example.mil", step[1])
        step = sp.picker_route(self.cfg, self.portal, self.cn, "disk_cleanup", {"hosts": ["web_servers", "failing", "web01.example.mil"]})
        self.assertEqual(step[0], "page")
        self.assertNotIn("Too many", step[1])                                                                             # 3 hosts: fine
        big = fake_aap.STATE["inventories"][0]
        big["hosts"] += ["h%02d.example.mil" % n for n in range(10)]
        big["groups"]["many"] = ["h%02d.example.mil" % n for n in range(10)]
        try:
            self.portal.aap.inventory_listing(force=True)
            step = sp.picker_route(self.cfg, self.portal, self.cn, "disk_cleanup", {"hosts": ["many"]})
            self.assertIn("Too many hosts.", step[1])                                                                      # 10 hosts at 5 at most
            self.assertIn("that is 10", step[1])
            step = sp.picker_route(self.cfg, self.portal, self.cn, "disk_cleanup", {"hosts": ["nothere.example.mil"]})
            self.assertIn("not a host or a group", step[1])
        finally:
            big["hosts"] = big["hosts"][:3]
            del big["groups"]["many"]
            self.portal.aap.inventory_listing(force=True)

    def _form(self, name, fields, token=None, origin="https://portal.example.mil"):
        data = urllib.parse.urlencode(fields, doseq=True).encode()
        h = dict(cert(JDOE), **{"Content-Type": "application/x-www-form-urlencoded", "Host": "portal.example.mil"})
        if origin:
            h["Origin"] = origin
        r = urllib.request.Request("http://%s/actions/%s" % (self.host, name), data=data, method="POST", headers=h)
        try:
            with NO_REDIRECT.open(r, timeout=20) as f:
                return f.status, f.read().decode(), dict(f.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(), dict(e.headers)

    def test_22_the_form_posts_without_scripts(self):
        name = self.nm("Health check - Linux")
        tok = self.portal.token(self.cn)
        ok = {"portal-token": tok, "portal_limit": ["web_servers"], "ticket": "INC77", "count": "2", "env": "dev", "pw": self.SECRET}
        code, body, h = self._form(name, ok)
        self.assertEqual(code, 303, body[:300])
        self.assertRegex(h["Location"], r"^/portal/jobs/\d+$")
        code, body, _ = self._form(name, dict(ok, portal_limit=["nothere.example.mil"]))                      # an unknown host: refused, nothing started
        self.assertEqual(code, 400)
        self.assertIn("not a host or a group", body)
        self.assertNotIn(self.SECRET, body)                                                                    # and the password is never sent back
        self.assertEqual(self._form(name, dict(ok, **{"portal-token": "1.bad"}))[0], 403)                                  # the page's token
        self.assertEqual(self._form(name, ok, origin="https://elsewhere.example.net")[0], 403)                # and the origin
        self.assertEqual(self._form("jt_999999", ok)[0], 404)
        with open(self.portal.log.path) as fh:
            self.assertNotIn(self.SECRET, fh.read())

    def test_23_the_page_has_the_picker_and_its_script_file_only(self):
        html_ = sp.action_page(self.cfg, self.portal, self.cn, self.nm("Health check - Linux"), {"portal_limit": ["web_servers"]})
        self.assertIn('<option value="web_servers" selected>web_servers (2 hosts)</option>', html_)            # the plain multi-select: it works without scripts
        self.assertIn('data-src="/portal/api/inventory"', html_)
        self.assertNotIn("<script>", html_)
        self.assertNotIn(" style=", html_)

    # ---- Codex's review of 9056ab88
    def test_24_the_form_carries_its_signature(self):
        name = self.nm("Health check - Linux")
        a = self.portal.actions[name]
        ok = {"portal_limit": ["web01.example.mil"], "ticket": "INC5", "env": "dev", "pw": "x"}
        code, out = self.portal.launch(self.cn, "192.0.2.5", name, ok, "0" * 24)
        self.assertEqual(code, 409, out)                                                   # drawn from another version of the template
        code, out = self.portal.launch(self.cn, "192.0.2.5", name, ok, a["sig"])
        self.assertEqual(code, 201, out)
        self.assertIn('name="portal-sig" value="%s"' % a["sig"], sp.action_page(self.cfg, self.portal, self.cn, name, {}))

    def test_25_an_incomplete_group_is_never_counted_short(self):
        inv = fake_aap.STATE["inventories"][0]
        self.portal.aap.inventory_listing(force=True)
        self.portal.aap.listing["bad"] = {"web_servers"}
        self.portal.aap.listing["groups"].pop("web_servers")
        hosts, why = self.portal.aap.expand(["web_servers"])
        self.assertIn("could not be read completely", why)
        self.assertNotIn("web_servers", [i["name"] for i in self.portal.aap.inventory_items("")["items"] if i["kind"] == "group"])
        self.portal.aap.inventory_listing(force=True)

    def test_26_the_label_is_checked_at_launch(self):
        tid = fake_aap.add_template("job_templates", "Label goes", ["portal"])
        self.portal.refresh(force=True)
        name = self.nm("Label goes")
        tpl = next(t for t in fake_aap.STATE["templates"]["job_templates"] if t["id"] == tid)
        tpl["labels"] = []
        self.portal.found_at = time.time()                                                  # the cards are still cached
        code, out = self.portal.launch(self.cn, "192.0.2.5", name, {})
        self.assertEqual(code, 409, out)
        self.assertIn("label", self.portal.log.read("refused")[-1]["why"])

    def test_27_survey_values_are_sent_as_checked(self):
        spec = [{"variable": "code", "type": "text", "required": True}, {"variable": "report_job", "type": "text", "required": False}]
        tid = fake_aap.add_template("job_templates", "Keeps zeros", ["portal"], spec)
        self.portal.refresh(force=True)
        code, out = self.portal.launch(self.cn, "192.0.2.5", self.nm("Keeps zeros"), {"code": "007", "report_job": "0007"})
        self.assertEqual(code, 201, out)
        with open(fake_aap.LOG) as fh:
            sent = [json.loads(x) for x in fh if '"POST"' in x and "/launch/" in x][-1]["body"]
        self.assertEqual(sent["extra_vars"], {"code": "007", "report_job": "0007"})

    def test_28_requested_by_question_must_take_a_name(self):
        for q in ({"type": "integer"}, {"type": "text", "max": 10}, {"type": "text", "min": 50}):
            fake_aap.add_template("job_templates", "Who %s" % json.dumps(q), ["portal"], [dict({"variable": "portal_requested_by"}, **q)])
        fake_aap.add_template("job_templates", "Who fine", ["portal"], [{"variable": "portal_requested_by", "type": "text", "max": 300}])
        self.portal.refresh(force=True)
        self.assertEqual(self.nm("Who fine") != "gone", True)
        why = [w for n, w in self.portal.found["skipped"] if n.startswith("Who {")]
        self.assertEqual(len(why), 3)
        self.assertTrue(all("portal_requested_by" in w for w in why))

    def test_29_float_bounds_as_text(self):
        v, sk = rules.survey_vars({"spec": [{"variable": "r", "type": "float", "min": "2.0", "max": "3.5"}]})
        self.assertEqual((v["r"]["min"], v["r"]["max"]), (2, 3.5))
        self.assertEqual(rules._num("nan"), None)
        self.assertEqual(rules._num("1e999"), None)

    def test_30_no_script_form_edges(self):
        name = self.nm("Health check - Linux")
        tok = self.portal.token(self.cn)
        base = {"portal-token": tok, "portal_limit": ["web01.example.mil"], "ticket": "INC8", "count": "2", "env": "dev", "pw": "x"}
        code, body, h = self._form(name, dict(base, **{"tags": []}))                                           # nothing ticked: tags is [] (not the default)
        self.assertEqual(code, 303, body[:200])
        with open(fake_aap.LOG) as fh:
            sent = [json.loads(x) for x in fh if '"POST"' in x and "/launch/" in x][-1]["body"]
        self.assertEqual(sent["extra_vars"]["tags"], [])
        many = dict(base, portal_limit=["x%d.example.mil" % i for i in range(150)])
        code, body, _ = self._form(name, many)
        self.assertEqual(code, 400)                                                                            # a field error, not a dropped connection
        self.assertIn("not a host or a group", body)

    # ---- Codex's second review
    def test_31_empty_groups_and_odd_members(self):
        inv = fake_aap.STATE["inventories"][0]
        inv["groups"]["empty_one"] = []
        inv["groups"]["odd_one"] = ["web01.example.mil", "web*"]
        try:
            self.portal.aap.inventory_listing(force=True)
            name = self.nm("Health check - Linux")
            ok = {"ticket": "INC1", "env": "dev", "pw": "x"}
            code, out = self.portal.launch(self.cn, "192.0.2.5", name, dict(ok, portal_limit=["empty_one"]))
            self.assertEqual(code, 400, out)
            self.assertIn("stands for no host", out["error"])
            code, out = self.portal.launch(self.cn, "192.0.2.5", name, dict(ok, portal_limit=["odd_one"]))
            self.assertEqual(code, 400, out)
            self.assertIn("cannot be used in a Limit", out["error"])
        finally:
            del inv["groups"]["empty_one"], inv["groups"]["odd_one"]
            self.portal.aap.inventory_listing(force=True)

    def test_32_the_final_expansion_is_checked_against_the_cap(self):
        name = self.nm("Health check - Linux")
        a = self.portal.actions[name]
        inv = fake_aap.STATE["inventories"][0]
        self.portal.aap.inventory_listing(force=True)
        inv["groups"]["web_servers"] = ["web01.example.mil", "web03.example.mil", "fail01.example.mil"]     # the group grew after the list was read
        try:
            small = dict(a, vars=dict(a["vars"], portal_limit=dict(a["vars"]["portal_limit"], max=2)))
            hosts, why = self.portal.limit_hosts(small, {"portal_limit": ["web_servers"]})
            self.assertIn("takes 2 hosts at most (that is 3)", why)
            hosts, why = self.portal.limit_hosts(a, {"portal_limit": ["web_servers"]})
            self.assertEqual((len(hosts), why), (3, ""))
        finally:
            inv["groups"]["web_servers"] = ["web01.example.mil", "web03.example.mil"]
            self.portal.aap.inventory_listing(force=True)

    def test_33_the_template_must_run_on_the_portal_inventory(self):
        fake_aap.add_template("job_templates", "Other inventory", ["portal"], None, limit=True, inventory=99)
        fake_aap.add_template("job_templates", "Asks inventory", ["portal"], None, limit=True)
        fake_aap.STATE["templates"]["job_templates"][-1]["ask_inventory"] = True
        self.portal.refresh(force=True)
        why = dict(self.portal.found["skipped"])
        self.assertIn("its inventory is not portal_inventory", why["Other inventory"])
        self.assertIn("its inventory is not portal_inventory", why["Asks inventory"])
        self.assertEqual(self.nm("Other inventory"), "gone")

    def test_34_an_old_listing_checks_nothing(self):
        self.portal.aap.inventory_listing(force=True)
        self.portal.aap.listing["at"] -= 10 * self.cfg.discover_seconds + 400
        keep = self.portal.aap.api.base
        self.portal.aap.api.base = "api/nowhere/v2/"                                             # AAP cannot be read now: the old list stays for the picker
        try:
            self.assertTrue(self.portal.aap.inventory_items("")["items"])
            hosts, why = self.portal.aap.expand(["web_servers"])
        finally:
            self.portal.aap.api.base = keep
        self.assertIn("could not be checked", why)                                                # but it checks nothing
        self.portal.aap.inventory_listing(force=True)

    def test_35_the_signature_is_required(self):
        name = self.nm("Health check - Linux")
        ok = {"portal_limit": ["web01.example.mil"], "ticket": "INC9", "env": "dev", "pw": "x"}
        code, out = self.raw_launch(self.cn, "192.0.2.5", name, ok)
        self.assertEqual(code, 409, out)                                                          # none is as bad as a wrong one
        self.assertIn("changed in AAP", out["error"])
        code, out = self.raw_launch(self.cn, "192.0.2.5", name, ok, self.portal.actions[name]["sig"])
        self.assertEqual(code, 201, out)

    def test_36_a_form_posted_after_a_restart(self):
        name = self.nm("Health check - Linux")
        sig = self.portal.actions[name]["sig"]
        self.portal.actions = dict((k, a) for k, a in self.portal.actions.items() if not a.get("discovered"))     # as after a restart: nothing found yet
        self.portal.found_at = 0.0
        tok = self.portal.token(self.cn)
        code, body, h = self._form(name, {"portal-token": tok, "portal-sig": sig, "portal_limit": ["web01.example.mil"], "ticket": "INC12", "count": "2",
                                          "env": "dev", "pw": "x"})
        self.assertEqual(code, 303, body[:200])

    def test_37_unreadable_launch_settings_skip_the_card(self):
        tid = fake_aap.add_template("job_templates", "No launch page", ["portal"])
        tpl = next(t for t in fake_aap.STATE["templates"]["job_templates"] if t["id"] == tid)
        tpl["no_launch_page"] = True
        self.portal.refresh(force=True)
        self.assertIn("launch settings could not be read", dict(self.portal.found["skipped"]).get("No launch page", ""))

    def test_38_a_next_link_that_cannot_be_followed_is_an_error(self):
        real = self.portal.aap.call

        def odd(method, path, body=None):
            code, data = real(method, path, body)
            if code == 200 and "next" in data and "role_level" in path:
                data = dict(data, next="https://elsewhere.example.net/other/?page=2")
            return code, data
        self.portal.aap.call = odd
        fake_aap.PAGE_CAP = 2
        try:
            self.portal.refresh(force=True)
        finally:
            self.portal.aap.call = real
            fake_aap.PAGE_CAP = 200
        self.assertIn("could not read every template", self.portal.found["error"])
        self.portal.refresh(force=True)

    def test_39_portal_limit_is_only_reserved_for_found_templates(self):
        acts, problems = rules.check_actions({"x": {"title": "X", "aap": {"type": "job", "name": "X"}, "vars": {"portal_limit": {"kind": "text"}}}})
        self.assertEqual(problems, [])
        self.assertIn("portal_limit", acts["x"]["vars"])
        v, sk = rules.survey_vars({"spec": [{"variable": "portal_limit", "type": "text"}]})
        self.assertTrue(sk)

    def test_40_the_requester_question_minimum(self):
        self.assertTrue(rules.takes_name({"type": "text", "min": 1, "max": 300}))
        self.assertFalse(rules.takes_name({"type": "text", "min": 2}))

    # ---- Codex's third review
    def test_41_a_workflow_with_a_limit_is_left_out(self):
        fake_aap.add_template("workflow_job_templates", "Workflow with Limit", ["portal"], None, limit=True)
        self.portal.refresh(force=True)
        self.assertEqual(self.nm("Workflow with Limit"), "gone")
        self.assertIn("workflow's Limit", dict(self.portal.found["skipped"])["Workflow with Limit"])

    def test_42_a_cleared_optional_text_is_an_answer(self):
        a = self.portal.actions[self.nm("Health check - Linux")]
        ok = {"portal_limit": "web01.example.mil", "ticket": "INC3", "env": "dev", "pw": "x"}
        self.assertEqual(rules.check_values(a, ok, None, self.portal.aap.expand)[0]["why"], "routine")                 # left out: the default
        self.assertEqual(rules.check_values(a, dict(ok, why=""), None, self.portal.aap.expand)[0]["why"], "")           # cleared: empty
        v, p = rules.check_values(a, dict(ok, ticket=""), None, self.portal.aap.expand)
        self.assertIn("ticket", p)                                                                                       # a required one is still required

    def test_43_whole_bounds_stay_exact(self):
        v, sk = rules.survey_vars({"spec": [{"variable": "n", "type": "integer", "min": "9007199254740993", "max": 9007199254740999}]})
        self.assertEqual(v["n"]["min"], 9007199254740993)
        self.assertIn("n", rules.check_values({"vars": v}, {"n": "9007199254740992"})[1])
        self.assertEqual(rules.check_values({"vars": v}, {"n": "9007199254740993"})[1], {})

    def test_44_the_first_discovery_is_waited_for(self):
        p = sp.Portal(self.cfg, aap=sp.Aap(self.cfg), log=sp.Log(""))
        p.aap.connect()
        p.found_at = time.time()                                                                                         # as when another request has begun to ask
        self.assertFalse(p.found_ready)
        p.refresh()
        self.assertTrue(p.found_ready)
        self.assertIn("Health check - Linux", [a["title"] for a in p.actions.values()])

    def test_45_the_inventory_prompt_is_in_the_signature(self):
        self.assertNotEqual(rules.template_sig("A", True, None, 3, False), rules.template_sig("A", True, None, 3, True))

    # ---- Codex's fourth review
    def test_46_the_groups_are_read_again_at_launch(self):
        inv = fake_aap.STATE["inventories"][0]
        name = self.nm("Health check - Linux")
        self.portal.aap.inventory_listing(force=True)
        inv["groups"]["web_servers"] = ["web01.example.mil"]                                      # web03 left the group after the picker's list was read
        try:
            code, out = self.portal.launch(self.cn, "192.0.2.5", name, {"portal_limit": ["web_servers"], "ticket": "INC4", "env": "dev", "pw": "x"})
            self.assertEqual(code, 201, out)
            with open(fake_aap.LOG) as fh:
                sent = [json.loads(x) for x in fh if '"POST"' in x and "/launch/" in x][-1]["body"]
            self.assertEqual(sent["limit"], "web01.example.mil")                                  # the members as they are now
        finally:
            inv["groups"]["web_servers"] = ["web01.example.mil", "web03.example.mil"]
            self.portal.aap.inventory_listing(force=True)

    def test_47_a_changed_playbook_is_not_the_form_the_person_saw(self):
        tid = fake_aap.add_template("job_templates", "Playbook moves", ["portal"])
        self.portal.refresh(force=True)
        name = self.nm("Playbook moves")
        tpl = next(t for t in fake_aap.STATE["templates"]["job_templates"] if t["id"] == tid)
        tpl["playbook"] = "something_else.yml"
        code, out = self.portal.launch(self.cn, "192.0.2.5", name, {})
        self.assertEqual(code, 409, out)

    # ---- Codex's fifth review
    def test_48_label_and_approval_marks_at_launch(self):
        tid = fake_aap.add_template("workflow_job_templates", "Mark moves", ["portal", "portal-approval"])
        jid = fake_aap.add_template("job_templates", "Job gets marked", ["portal"])
        self.portal.refresh(force=True)
        wf, jt = self.nm("Mark moves"), self.nm("Job gets marked")
        w = next(t for t in fake_aap.STATE["templates"]["workflow_job_templates"] if t["id"] == tid)
        j = next(t for t in fake_aap.STATE["templates"]["job_templates"] if t["id"] == jid)
        j["labels"] = ["portal", "portal-approval"]                                                 # the mark is put on a job template after the form opened
        self.assertEqual(self.portal.launch(self.cn, "192.0.2.5", jt, {})[0], 409)                  # refused at once, and the cards follow AAP
        self.assertEqual(self.nm("Job gets marked"), "gone")
        self.portal.refresh(force=True)
        w["labels"] = ["portal"]                                                                    # the approval mark is taken off a workflow after the form opened
        self.portal.found_at = time.time()
        self.assertEqual(self.portal.launch(self.cn, "192.0.2.5", wf, {})[0], 409)

    def test_49_half_a_label_list_is_not_the_list(self):
        fake_aap.add_template("job_templates", "Many labels", ["portal"], many_labels=12)
        real = self.portal.aap.call

        def broken(method, path, body=None):
            if path.endswith("/labels/?page_size=200"):
                return 500, {"detail": "boom"}
            return real(method, path, body)
        self.portal.aap.call = broken
        try:
            self.portal.refresh(force=True)
        finally:
            self.portal.aap.call = real
        self.assertIn("labels could not be read completely", dict(self.portal.found["skipped"]).get("Many labels", ""))
        self.portal.refresh(force=True)

    def test_50_a_group_is_resolved_by_its_name_now(self):
        inv = fake_aap.STATE["inventories"][0]
        self.portal.aap.inventory_listing(force=True)
        inv["groups"]["renamed_to"] = inv["groups"].pop("failing")                                  # the group's old name is gone
        try:
            hosts, why = self.portal.aap.expand_now(["failing"])
            self.assertIn("not a host or a group", why)
        finally:
            inv["groups"]["failing"] = inv["groups"].pop("renamed_to")
            self.portal.aap.inventory_listing(force=True)

    def test_51_a_signature_on_a_local_action_is_refused(self):
        code, out = self.raw_launch(self.cn, "192.0.2.5", "no_prompts", {}, "a" * 24)
        self.assertEqual(code, 409, out)

    def test_52_a_saved_password_need_not_be_typed_again(self):
        v, sk = rules.survey_vars({"spec": [{"variable": "pw", "type": "password", "required": True, "default": "$encrypted$"}]})
        self.assertFalse(v["pw"]["required"])
        self.assertEqual(rules.check_values({"vars": v}, {"pw": ""})[0], {})                       # not sent: AAP uses the one it saved
        self.assertIsNone(v["pw"]["default"])

    # ---- Codex's sixth review
    def test_53_changed_workflow_steps_are_not_the_form_the_person_saw(self):
        tid = fake_aap.add_template("workflow_job_templates", "Steps move", ["portal", "portal-approval"])
        w = next(t for t in fake_aap.STATE["templates"]["workflow_job_templates"] if t["id"] == tid)
        w["nodes"] = [{"id": 1, "unified_job_template": 40, "success_nodes": [2], "failure_nodes": [], "always_nodes": []},
                      {"id": 2, "unified_job_template": 44, "success_nodes": [], "failure_nodes": [], "always_nodes": []}]
        self.portal.refresh(force=True)
        name = self.nm("Steps move")
        self.assertIn(name, self.portal.actions)
        w["nodes"] = w["nodes"][1:]                                                                  # a step was taken out
        code, out = self.portal.launch(self.cn, "192.0.2.5", name, {})
        self.assertEqual(code, 409, out)

    def test_54_a_form_from_before_the_approval_mark_moved(self):
        tid = fake_aap.add_template("workflow_job_templates", "Mark by refresh", ["portal", "portal-approval"])
        self.portal.refresh(force=True)
        name = self.nm("Mark by refresh")
        old_sig = self.portal.actions[name]["sig"]
        w = next(t for t in fake_aap.STATE["templates"]["workflow_job_templates"] if t["id"] == tid)
        w["labels"] = ["portal"]
        self.portal.refresh(force=True)                                                              # the cards follow AAP before the form is sent
        code, out = self.raw_launch(self.cn, "192.0.2.5", name, {}, old_sig)
        self.assertEqual(code, 409, out)

    def test_55_a_host_that_became_a_group_name(self):
        inv = fake_aap.STATE["inventories"][0]
        self.portal.aap.inventory_listing(force=True)
        inv["groups"]["web01.example.mil"] = ["web03.example.mil"]                                    # a group made after the list was read
        try:
            hosts, why = self.portal.aap.expand_now(["web_servers"])
            self.assertIn("also a group's name", why)
        finally:
            del inv["groups"]["web01.example.mil"]
            self.portal.aap.inventory_listing(force=True)

    def test_56_a_page_of_an_action_taken_off(self):
        html_ = sp.action_page(self.cfg, self.portal, self.cn, "jt_999999", {})
        self.assertIn("There is no action jt_999999 here.", html_)

    # ---- 1.78.0: a job followed live
    def run_job(self, hosts, wait=True):
        name = self.nm("Health check - Linux")
        code, out = self.portal.launch(self.cn, "192.0.2.5", name, {"portal_limit": hosts, "ticket": "INC5", "env": "dev", "pw": "Hunter2-not-a-real-password"})
        self.assertEqual(code, 201, out)
        if wait:
            time.sleep(fake_aap.RUN + 0.4)
        return out["job_id"]

    def test_60_progress_of_a_finished_job_and_the_estimate(self):
        inv = fake_aap.STATE["inventories"][0]
        inv["hosts"].append("down01.example.mil")
        try:
            self.portal.aap.inventory_listing(force=True)
            first = self.run_job(["web01.example.mil"])
            code, p = self.portal.progress(first, self.cn)
            self.assertEqual(code, 200, p)
            self.assertEqual((p["done"], p["pct"], p["now"], p["hosts_line"]), (True, 100, "", "1 of 1 hosts done"))
            self.assertEqual([h["state"] for h in p["hosts"]], ["done"])
            self.assertEqual(len(p["lines"]), 8)
            # the second run of the template: its estimate is the first run's 12 tasks
            second = self.run_job(["fail01.example.mil", "down01.example.mil", "web03.example.mil"], wait=False)
            time.sleep(0.5)
            code, p = self.portal.progress(second, self.cn)
            self.assertEqual(code, 200, p)
            self.assertFalse(p["done"])
            self.assertRegex(p["now"], r"^Step [0-9]+ of about 12 - ")
            self.assertTrue(0 < p["pct"] < 100)
            time.sleep(fake_aap.RUN)
            code, p = self.portal.progress(second, self.cn)
            self.assertEqual((p["done"], p["pct"]), (True, 100))
            self.assertEqual(sorted((h["host"], h["state"]) for h in p["hosts"]),
                             [("down01.example.mil", "unreachable"), ("fail01.example.mil", "failed"), ("web03.example.mil", "done")])
            self.assertEqual(p["hosts_line"], "3 of 3 hosts done \u00b7 1 failed \u00b7 1 unreachable")
            self.assertEqual(p["result_line"], "Checking the services on down01.example.mil (could not connect)")   # the first to stop: what failed, on which host
            blob = json.dumps(p)
            for secret in ("Hunter2", "password", "INC5", "extra_vars", "event_data"):
                self.assertNotIn(secret, blob)                                                               # no answer, no module message, no raw event
        finally:
            inv["hosts"].remove("down01.example.mil")
            self.portal.aap.inventory_listing(force=True)

    def test_61_only_the_people_who_may_see_the_job(self):
        jid = self.run_job(["web01.example.mil"])
        self.assertEqual(self.portal.progress(jid, "ROE.RICHARD.A.2345678901")[0], 200)                       # portal_requesters is empty: any card may
        self.portal.cfg.requesters = ["DOE.JANE.Q.1234567890"]
        try:
            self.assertEqual(self.portal.progress(jid, "ROE.RICHARD.A.2345678901")[0], 404)                   # not listed: not even said to exist
            self.assertEqual(self.portal.progress(jid, self.cn)[0], 200)
            self.assertEqual(self.portal.progress(99999, self.cn)[0], 404)                                    # a job the portal did not start
        finally:
            self.portal.cfg.requesters = []

    def test_62_the_rate_limit(self):
        keep = self.portal.POLLS_PER_MINUTE
        self.portal.POLLS_PER_MINUTE = 3
        try:
            self.portal.polls.pop("RATE.TEST.A.1", None)
            got = [self.portal.poll_ok("RATE.TEST.A.1") for _ in range(5)]
            self.assertEqual(got, [True, True, True, False, False])
            self.assertTrue(self.portal.poll_ok("RATE.TEST.B.2"))                                             # another person has their own
        finally:
            self.portal.POLLS_PER_MINUTE = keep

    # ---- 1.78.0: the Jobs page
    def get(self, path, dn=JDOE):
        r = urllib.request.Request("http://%s%s" % (self.host, path), headers=cert(dn))
        try:
            with NO_REDIRECT.open(r, timeout=20) as f:
                return f.status, f.read().decode(), dict(f.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(), dict(e.headers)

    def test_63_the_jobs_page_of_a_job_that_ran(self):
        jid = self.run_job(["web01.example.mil", "fail01.example.mil"])
        code, h, _ = self.get("/jobs/%d" % jid)
        self.assertEqual(code, 200)
        for needle in ('class="card jl"', 'id="jd"', 'data-done="1"', "My jobs", "Search job, job id or who started it", "Hosts", "What just happened",
                       "Job ID", "Requested by", "DOE.JANE.Q.1234567890", "Answers - what the run was started with", "(hidden)", "Technical output: available after the next update.",
                       'class="chip s-ok"', "2 of 2 hosts done"):
            self.assertIn(needle, h, needle)
        self.assertNotIn("Hunter2", h)                                                              # a password: never
        self.assertNotIn("refreshes every 3 s", h)                                                  # it is over: nothing to refresh
        self.assertIn("<b>fail01.example.mil</b> failed</span>", h)                                 # its chip: a task ran and failed
        self.assertNotIn("14 minutes off", h)                                                       # never the module's own words
        self.assertIn('href="/portal/jobs/%d"' % jid, h)                                            # Refresh without scripts

    def test_64_a_running_job_and_the_pane(self):
        jid = self.run_job(["web01.example.mil", "web03.example.mil"], wait=False)
        time.sleep(0.6)
        code, h, _ = self.get("/jobs/%d?pane=1" % jid)
        self.assertEqual(code, 200)
        self.assertTrue(h.startswith('<section class="card jd" id="jd"'), h[:80])                    # the pane only: no page around it
        self.assertIn('data-done="0"', h)
        self.assertIn("<b>Now</b> \u00b7 Step ", h)
        self.assertIn("<progress", h)
        self.assertIn("<b>Running</b>", h)
        self.assertIn("refreshes every 3 s while it runs", h)
        time.sleep(fake_aap.RUN + 0.3)
        code, h, _ = self.get("/jobs/%d?pane=1" % jid)
        self.assertIn('data-done="1"', h)                                                          # it says it is done: the script stops asking
        self.assertNotIn("<progress", h)
        self.assertIn("<b>Succeeded</b>", h)

    def test_65_the_list_and_who_may_see_all(self):
        self.run_job(["web01.example.mil"])
        code, h, _ = self.get("/jobs")
        self.assertEqual(code, 200)
        self.assertIn('class="on" href="/portal/jobs?scope=mine"', h)
        self.assertNotIn("All jobs", h)                                                             # not an approver
        code, h, _ = self.get("/jobs?scope=all")
        self.assertIn('data-scope="mine"', h)                                                       # asked for all: gets their own
        self.portal.cfg.approvers = ["APPROVER.ALEX.B.3456789012"]
        try:
            code, h, _ = self.get("/jobs?scope=all", dn=APPR)
            self.assertIn('data-scope="all"', h)
            self.assertIn("All jobs", h)
            code, frag, _ = self.get("/jobs?list=1&scope=all", dn=APPR)
            self.assertTrue(frag.startswith('<aside class="card jl"'))
        finally:
            self.portal.cfg.approvers = []
        code, h, _ = self.get("/jobs/999999")
        self.assertEqual(code, 404)

    def test_66_the_progress_call(self):
        jid = self.run_job(["web01.example.mil"])
        code, raw, _ = self.get("/api/jobs/%d/progress" % jid)
        d = json.loads(raw)
        self.assertEqual((code, d["done"], d["pct"]), (200, True, 100))
        self.assertEqual(self.get("/api/jobs/%d/progress" % jid, dn=RROE)[0], 200)
        self.portal.cfg.requesters = ["DOE.JANE.Q.1234567890"]
        try:
            self.assertEqual(self.get("/api/jobs/%d/progress" % jid, dn=RROE)[0], 403)                     # not on the list of people: refused
            self.portal.cfg.requesters = ["DOE.JANE.Q.1234567890", "ROE.RICHARD.A.2345678901"]
            self.assertEqual(self.get("/api/jobs/%d/progress" % jid, dn=RROE)[0], 200)                     # on the list: may see what the action allows (as every job page)
            self.assertEqual(self.get("/api/jobs/999999/progress", dn=RROE)[0], 404)                       # a job the portal did not start: not even said to exist
        finally:
            self.portal.cfg.requesters = []
        keep = self.portal.POLLS_PER_MINUTE
        self.portal.POLLS_PER_MINUTE = 2
        self.portal.polls.pop("DOE.JANE.Q.1234567890", None)
        try:
            codes = [self.get("/api/jobs/%d/progress" % jid)[0] for _ in range(4)]
            self.assertEqual(codes, [200, 200, 429, 429])                                          # a person's calls are limited
        finally:
            self.portal.POLLS_PER_MINUTE = keep
            self.portal.polls.clear()

    def test_67_the_script_stops_asking_when_done(self):
        js = open(os.path.join(FILES, "static", "portal.js")).read()
        self.assertIn('pane.dataset.done !== "0"', js)
        self.assertIn("document.hidden", js)                                                        # not while the page is hidden
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("<script>", self.get("/jobs")[1])

    # ---- 1.78.0: the Actions page - cards, Details, the Run dialog, Refresh
    def test_70_cards_with_the_last_run_and_labels(self):
        name = self.nm("Health check - Linux")
        self.run_job(["web01.example.mil"])
        self.portal.last_at = 0
        code, h, _ = self.get("/")
        self.assertEqual(code, 200)
        card = h.split("<h3>Health check - Linux</h3>")[1].split("</article>")[0]
        self.assertIn('<span class="ty">Job template</span>', card)
        self.assertIn("Last run", card)
        self.assertIn("Succeeded", card)
        self.assertIn('href="/portal/actions/%s/details" data-details>Details' % name, card)
        self.assertIn('href="/portal/actions/%s" data-run>Run' % name, card)
        self.assertNotIn('class="badge">Approval', card)                                              # a job template: nothing holds it
        self.assertNotIn('<span class="tg">portal</span>', h)                                         # the portal's own labels are not shown
        self.assertIn('<span class="tg">extra-0</span>', h.split("<h3>Late label</h3>")[1].split("</article>")[0])

    def test_71_the_tiles_and_the_search_without_scripts(self):
        code, h, _ = self.get("/?show=workflow")
        self.assertIn(" hidden", h.split('id="agrid"')[1].split("</li>")[0])                           # the first card (a job template) is hidden
        self.assertRegex(h, r'id="acount" aria-live="polite">\d+ of \d+<')
        shown = re.search(r'id="acount" aria-live="polite">(\d+) of (\d+)<', h)
        self.assertLess(int(shown.group(1)), int(shown.group(2)))
        code, h, _ = self.get("/?q=nothing-matches-this")
        self.assertIn(">0 of ", h)
        code, h, _ = self.get("/?q=health+check")
        self.assertRegex(h, r'<li data-wf="0" data-failed="0" data-q="[^"]*health check[^"]*">')

    def test_72_details(self):
        name = self.nm("Health check - Linux")
        code, frag, _ = self.get("/actions/%s/details?embed=1" % name)
        self.assertEqual(code, 200)
        self.assertTrue(frag.startswith('<div class="dbody">'))
        for needle in ("Health check - Linux", "Job template", "Hosts (1 to 50 hosts or groups)", "Ticket number *", "Multiple choice", "Password", "Last 5 runs", "data-run>Run"):
            self.assertIn(needle, frag, needle)
        for secret in ("routine", "Hunter2", "dev", "prod"):
            self.assertNotIn(">" + secret + "<", frag)                                               # names and types only: no default, no choice
        code, page, _ = self.get("/actions/%s/details" % name)
        self.assertIn('<html', page)                                                                # without scripts: a page
        self.assertEqual(self.get("/actions/nothing/details")[0], 404)
        self.assertEqual(self.get("/actions/%s/details" % name, dn=RROE)[0], 200)

    def test_73_the_run_dialog_content(self):
        name = self.nm("Health check - Linux")
        code, frag, _ = self.get("/actions/%s?embed=1" % name)
        self.assertEqual(code, 200)
        for needle in ("This form is made from the template&#x27;s survey in AAP.", 'type="password"', "Launch job", "data-close>Cancel",
                       "The portal records you (<b>DOE.JANE.Q.1234567890</b>) as the person who asked for it.", 'name="portal-sig"', 'data-picker="portal_limit"'):
            self.assertIn(needle, frag, needle)
        self.assertNotIn("<html", frag)
        code, frag2, _ = self.get("/actions/disk_cleanup?embed=1")
        self.assertNotIn('<div class="dbody">', frag2)                                              # an action that picks a host, then a finding: its own steps, no dialog

    def test_74_refresh(self):
        tok = self.portal.token(self.cn)
        keep = self.portal.found_at
        code, body, h = self._post_form("/refresh", {"portal-token": tok})
        self.assertEqual((code, h.get("Location")), (303, "/portal/"))
        self.assertGreater(self.portal.found_at, keep)                                              # AAP read again now
        self.assertEqual(self._post_form("/refresh", {"portal-token": "1.bad"})[0], 403)
        self.assertEqual(self._post_form("/refresh", {})[0], 403)

    def _post_form(self, path, fields, origin="https://portal.example.mil"):
        data = urllib.parse.urlencode(fields, doseq=True).encode()
        hd = dict(cert(JDOE), **{"Content-Type": "application/x-www-form-urlencoded", "Host": "portal.example.mil", "Origin": origin})
        r = urllib.request.Request("http://%s%s" % (self.host, path), data=data, method="POST", headers=hd)
        try:
            with NO_REDIRECT.open(r, timeout=20) as f:
                return f.status, f.read().decode(), dict(f.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(), dict(e.headers)


class Look(unittest.TestCase):
    """1.75.2: the portal's pages follow report_web_theme (ops, atlas, brief) with the report website's own colours."""

    def test_the_look_is_the_websites(self):
        for want, given in (("ops", "ops"), ("brief", "BRIEF"), ("atlas", "atlas"), ("atlas", ""), ("atlas", "command"), ("atlas", "x\"><b>")):
            cfg = sp.Config({"PORTAL_THEME": given})
            self.assertEqual(cfg.look, want, given)
            self.assertIn('<html lang="en" data-look="%s">' % want, sp.shell(cfg, "T", "<p>x</p>"))
        self.assertEqual(sp.Config({}).look, "atlas")                                   # report_web_theme's default

    def test_portal_css_is_made_from_the_report_palettes(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import gen_portal_css as g
        have = open(os.path.join(FILES, "static", "portal.css")).read()
        self.assertEqual(have, g.build(), "scripts/gen_portal_css.py")
        for look in ("atlas", "brief", "ops"):
            light, dark = g.palette(look)
            self.assertIn(':root[data-look="%s"]{%s}' % (look, light), have)
            self.assertIn(':root[data-look="%s"][data-theme="dark"]{%s}' % (look, dark), have)
            self.assertIn(':root[data-look="%s"]:has(#theme-dark:checked){%s}' % (look, dark), have)     # the switch works without the script
            self.assertIn("--card:", light)
            self.assertIn("--critical:", dark)
        self.assertNotIn("@@", have)
        self.assertIsNone(re.search(r"url\(|@import|https?://", have))               # offline: nothing outside

    def test_every_real_action_has_its_line_and_area(self):
        import yaml
        with open(os.path.join(FILES, "actions.yml")) as fh:
            doc = yaml.safe_load(fh)
        acts, problems = rules.check_actions(doc["actions"])
        self.assertEqual(problems, [])
        self.assertEqual(sorted(acts), sorted(["stig_recheck", "ckl_import", "act_triage", "disk_cleanup", "service_restart", "fapolicyd_allow",
                                               "disk_space_request", "open_ticket", "stig_recheck_update", "stig_fix_rule", "stigman_create_asset"]))
        for n, a in acts.items():
            self.assertTrue(a["summary"] and len(a["summary"]) <= 160, n)
            self.assertTrue(a["area"], n)

        class Aap(object):
            ids = {}

        class Log(object):
            def read(self, kind=None):
                return []

        class Fake(object):
            actions, aap, log = acts, Aap(), Log()
            found, found_at, last_at, LAST_TTL, last = {"error": ""}, 0, 0, 60, {}
            catalog = sp.Portal.catalog

            def mine(self, cn):
                return list(acts.values())

            def token(self, cn):
                return "tok"
        Aap.ready, Aap.resolve, Aap.last_info = "", lambda self, a: {}, lambda self, t: {}
        cfg = sp.Config({"PORTAL_THEME": "ops"})
        t = sp.home_page(cfg, Fake(), "DOE.JANE.Q.1234567890")
        self.assertEqual(len(re.findall(r'<article class="card tcard', t)), 11)                          # exactly the file's actions
        for a in acts.values():
            self.assertIn(html.escape(a["title"]), t)
        self.assertEqual(t.count('<span class="badge">Approval</span>'), len([a for a in acts.values() if a["approval"] and a["aap"]["type"] == "workflow"]))
        a = dict(acts["stig_recheck"], area="", summary="")                              # no line: "No description"
        Fake.actions, Fake.mine = {"x": a}, lambda self, cn: [a]
        t = sp.home_page(cfg, Fake(), "DOE.JANE.Q.1234567890")
        self.assertIn('<p class="none">No description</p>', t)
        self.assertNotIn("<p></p>", t)

    def test_area_and_summary_are_checked(self):
        base = {"title": "T", "aap": {"type": "job", "name": "N"}}
        _, problems = rules.check_actions({"a": dict(base, summary="x" * 161, area="y" * 41)})
        self.assertEqual(len(problems), 2)
        _, problems = rules.check_actions({"a": dict(base, summary="{{ 7 }}", area="{% x %}")})
        self.assertEqual(len(problems), 2)
        ok, problems = rules.check_actions({"a": dict(base, summary="One line.", area="STIG")})
        self.assertEqual((problems, ok["a"]["summary"], ok["a"]["area"]), ([], "One line.", "STIG"))


class Own(unittest.TestCase):
    """1.75.3: portal_actions_extra, portal_actions_off, portal_setup_templates_extra and portal_setup_workflows_extra - merged with the built-in
    ones, checked by the same rules, a clash said with the setting's name. The user's example: Health check - Linux on one host."""

    EXAMPLE = {"health_one_host": {"title": "Health check of one host", "summary": "Runs the Linux health check on one host.", "area": "Health",
                                   "aap": {"type": "job", "name": "Health check - Linux (portal)"}, "vars": {"target": {"kind": "inventory_host", "label": "Host"}},
                                   "limit_from": "target", "approval": False, "fixes": False}}
    TEMPLATE = {"name": "Health check - Linux (portal)", "playbook": "playbooks/health_check.yml", "credentials": ["machine"],
                "prompts": {"variables": True, "limit": True}, "description": "The Linux health check on one host, for the portal"}

    def builtin(self):
        import yaml
        with open(os.path.join(FILES, "actions.yml")) as fh:
            return yaml.safe_load(fh)["actions"]

    def test_merge_actions(self):
        base = self.builtin()
        m = rules.site_portal_actions_merge(base, self.EXAMPLE, [])
        self.assertEqual(m["problems"], [])
        self.assertEqual(list(m["actions"])[-1], "health_one_host")
        self.assertEqual(len(m["actions"]), len(base) + 1)
        acts, problems = rules.check_actions(m["actions"])                           # checked like the built-ins
        self.assertEqual((problems, acts["health_one_host"]["area"], acts["health_one_host"]["limit_from"]), ([], "Health", "target"))
        m = rules.site_portal_actions_merge(base, {"stig_recheck": {"title": "x", "aap": {"type": "job", "name": "n"}}}, [])
        self.assertEqual(len(m["problems"]), 1)
        self.assertIn("portal_actions_extra: 'stig_recheck' is already an action here", m["problems"][0])
        m = rules.site_portal_actions_merge(base, {"stig_recheck": self.EXAMPLE["health_one_host"]}, ["stig_recheck"])            # off, then replaced
        self.assertEqual((m["problems"], m["actions"]["stig_recheck"]["title"]), ([], "Health check of one host"))
        m = rules.site_portal_actions_merge(base, {}, ["open_ticket", "act_triage"])
        self.assertEqual((m["problems"], "open_ticket" in m["actions"], "act_triage" in m["actions"], len(m["actions"])), ([], False, False, len(base) - 2))
        m = rules.site_portal_actions_merge(base, {}, ["no_such"])
        self.assertIn("portal_actions_off: 'no_such' is not one of the actions", m["problems"][0])
        self.assertEqual(len(rules.site_portal_actions_merge(base, [1], "x")["problems"]), 2)          # a list for the extras; a name for off ('x' is no action)
        self.assertEqual(rules.site_portal_actions_merge(base)["actions"], base)                                                  # nothing set: as it was
        bad = dict(self.EXAMPLE["health_one_host"], area="y" * 41)
        _, problems = rules.check_actions(rules.site_portal_actions_merge(base, {"mine": bad}, [])["actions"])
        self.assertTrue(any("area must be a short name" in x for x in problems))                         # the same rules as the built-ins

    def test_merge_setup_lists(self):
        import yaml
        with open(os.path.join(FILES, "workflows.yml")) as fh:
            spec = yaml.safe_load(fh)
        m = rules.site_portal_spec_merge(spec, [self.TEMPLATE], [])
        self.assertEqual(m["problems"], [])
        self.assertEqual(m["spec"]["templates"][-1]["name"], "Health check - Linux (portal)")
        self.assertEqual(len(m["spec"]["templates"]), len(spec["templates"]) + 1)
        self.assertEqual(len(m["spec"]["workflows"]), len(spec["workflows"]))
        clash = rules.site_portal_spec_merge(spec, [dict(self.TEMPLATE, name=spec["templates"][0]["name"])], [])
        self.assertIn("portal_setup_templates_extra: %r is already one of the templates here" % spec["templates"][0]["name"], clash["problems"][0])
        w = spec["workflows"][0]
        clash = rules.site_portal_spec_merge(spec, [], [{"name": "Mine", "number": w["number"], "nodes": []}])
        self.assertIn("has the number %s" % w["number"], clash["problems"][0])
        self.assertEqual(len(rules.site_portal_spec_merge(spec, "x", {"a": 1})["problems"]), 2)
        lib = os.path.join(ROOT, "roles", "aap_portal_setup", "library", "site_aap_portal_setup.py")
        import importlib.util
        import ansible.module_utils
        ansible.module_utils.__path__.append(os.path.join(ROOT, "plugins", "module_utils"))
        sp_ = importlib.util.spec_from_file_location("site_aap_portal_setup", lib)
        mod = importlib.util.module_from_spec(sp_)
        sp_.loader.exec_module(mod)
        self.assertEqual(mod.check_spec(m["spec"]), [])                              # the setup module's own check: the merged spec is right
        bad = rules.site_portal_spec_merge(spec, [dict(self.TEMPLATE, playbook="")], [])
        self.assertTrue(mod.check_spec(bad["spec"]))                                 # ... and a wrong extra is refused by it

    def test_the_findings_rule_is_the_buttons_rule(self):
        import finding_filters as ff
        acts = self.builtin()
        for name in ("disk_cleanup", "stig_fix_rule", "stigman_create_asset", "open_ticket", "act_triage"):
            a = rules.check_actions({name: acts[name]})[0][name]
            for e in ({"kind": "disk_full", "data": {"os": "linux"}}, {"kind": "disk_full", "data": {"os": "windows"}}, {"kind": "stig_rule_fail", "data": {"cat": "CAT I", "os": "linux"}},
                      {"kind": "stig_rule_fail", "data": {"cat": "CAT II", "os": "linux"}}, {"kind": "stig_no_asset", "data": {"os": "linux"}}, {"kind": "eventlog", "data": {}},
                      {"kind": "other", "data": {}}):
                self.assertEqual(sp.finding_fits(a, e), ff._matches(a, e), (name, e))

    def test_config_of_the_pick(self):
        c = sp.Config({"PORTAL_FINDING_DAYS": "30", "PORTAL_FINDING_JOBS": "0", "PORTAL_PICK_MAX_LAUNCHES": "999",
                       "PORTAL_FINDING_PLAYBOOKS": '["health_check.yml", "fapolicyd*.yml", "../x", "a b"]'})
        self.assertEqual((c.finding_days, c.finding_jobs, c.pick_max, c.finding_playbooks), (14, 1, 50, ["health_check.yml", "fapolicyd*.yml"]))
        c = sp.Config({})
        self.assertEqual((c.finding_days, c.finding_jobs, c.pick_max, c.finding_playbooks), (7, 5, 10, []))

    def test_the_deploy_defaults_match_finding_ref(self):
        import yaml
        with open(os.path.join(ROOT, "roles", "finding_ref", "defaults", "main.yml")) as fh:
            fr = yaml.safe_load(fh)["finding_ref_report_playbooks"]
        with open(os.path.join(ROOT, "roles", "site_portal_stack", "defaults", "main.yml")) as fh:
            txt = fh.read()
        for pb in fr:
            self.assertIn("'%s'" % pb, txt)                                          # the portal reads the report jobs finding_ref takes


class RoleFiles(unittest.TestCase):
    def test_nginx_lines(self):
        import jinja2
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(ROOT, "roles", "site_portal_stack", "templates")),
                                 keep_trailing_newline=True)
        import re
        env.filters["regex_replace"] = lambda s, a, b: re.sub(a, b, s)
        env.filters["bool"] = bool
        t = env.get_template("nginx-portal.conf.j2").render(portal_path="/portal/", _portal_resolvers=["192.0.2.1"],
                                                            _portal_net_name="stigman-net")
        for line in ("location /portal/ {", "proxy_set_header X-SSL-Client-Verify $ssl_client_verify;",
                     "proxy_set_header X-SSL-Client-S-DN   $ssl_client_s_dn;", "resolver 192.0.2.1 valid=30s",
                     "rewrite ^/portal/(.*)$ /$1 break;", "client_max_body_size 64k;", "return 301 /portal/;"):
            self.assertIn(line, t)
        s = env.get_template("nginx-portal-server.conf.j2").render(portal_path="/portal/", _portal_resolvers=["192.0.2.9"],
                                                                   _portal_net_name="site-portal", portal_server_name="portal.example.mil",
                                                                   portal_verify_depth=3, _portal_has_crl=True)
        for line in ("ssl_verify_client       on;", "ssl_crl                 /etc/site-portal-tls/crl.pem;",
                     "    location /portal/ {", "        proxy_pass $site_portal_app;"):
            self.assertIn(line, s)

    def test_nginx_stigman_links(self):
        """1.68.1: the optional lines - STIG Manager's page (/ and /index.html only) with the portal's script added, the page
        as text (no gzip from STIG Manager there), the versions tested; and how to take them out again."""
        import jinja2
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(ROOT, "roles", "site_portal_stack", "templates")),
                                 keep_trailing_newline=True)
        t = env.get_template("nginx-stigman-links.conf.j2").render(portal_path="/portal/", portal_stigman_path="/",
                                                                   portal_stigman_upstream="http://stigman-api:54000",
                                                                   portal_stigman_open_versions=["1.6.16", "1.6.17", "1.6.18"])
        tag = ("sub_filter '</head>' '<script src=\"/portal/static/stigman-open.js\" data-versions=\"1.6.16 1.6.17 1.6.18\" defer>"
               "</script></head>';")
        self.assertEqual(t.count(tag), 2)
        for line in ("location = / {", "location = /index.html {", "proxy_pass http://stigman-api:54000;", 'proxy_set_header Accept-Encoding   "";',
                     "sub_filter_types text/html;", "sub_filter_once on;", "# REMOVE: delete these two location blocks"):
            self.assertIn(line, t)
        self.assertNotIn("location / {", t)                     # STIG Manager's own location / stays as it is
        import yaml
        with open(os.path.join(ROOT, "roles", "site_portal_stack", "defaults", "main.yml")) as fh:
            d = yaml.safe_load(fh)
        self.assertEqual((d["portal_stigman_path"], d["portal_stigman_open_versions"]), ("/", ["1.6.16", "1.6.17", "1.6.18"]))

    def test_deploy_syntax(self):
        ap = os.environ.get("ANSIBLE_PLAYBOOK") or shutil.which("ansible-playbook")
        if not ap:
            self.skipTest("no ansible-playbook")
        p = subprocess.run([ap, "--syntax-check", "-i", "localhost,", "playbooks/site_portal_deploy.yml"], cwd=ROOT,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(p.returncode, 0, p.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=1)
