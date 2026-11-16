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
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

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
                     "vars": {"target": {"kind": "inventory_host"}}, "limit_from": "target", "approval": False},
    "ckl_import": {"title": "Import the checklists", "aap": {"type": "workflow", "name": "Checklists imported into STIG Manager"},
                   "vars": {"stigman_ckl_import_mode": {"kind": "choice", "values": ["import", "import and scorecard", "scorecard"],
                                                        "default": "import", "label": "What to do"}},
                   "approval": True},
    "act_triage": {"title": "Ask ACT about this finding", "aap": {"type": "workflow", "name": "ACT - triage finding"},
                   "vars": {"target": {"kind": "inventory_host"},
                            "report_job": {"kind": "text", "max": 12, "pattern": "[0-9]{1,12}"},
                            "finding_id": {"kind": "text", "max": 120, "pattern": "(fapolicyd|eventlog):[A-Za-z0-9_.:-]{1,100}"}},
                   "limit_from": "target", "approval": False, "who": ["DOE.JANE.Q.1234567890", "POE.PAT.C.4567890123"],
                   "fixes": False, "kinds": ["fapolicyd", "eventlog"], "button": "Ask ACT"},
    "disk_cleanup": {"title": "Clean up this mount", "aap": {"type": "job", "name": "Disk - clean up one mount"}, "button": "Clean up",
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
                             "PORTAL_BANNER": {"on": True, "text": "CUI", "bg": "#502b85", "fg": "#ffffff"}})
        sp.sys.stdout = open(os.devnull, "w")          # the journal's copy of each line
        cls.portal = sp.Portal(cls.cfg)
        cls.portal.aap.connect()
        cls.states = cls.portal.aap.resolve(cls.portal.actions)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), sp.make_handler(cls.cfg, cls.portal, os.path.join(FILES, "static")))
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
        self.assertIn("<dt>Started as</dt><dd>DOE.JANE.Q.1234567890</dd>", t)        # the name only, not the variable's
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
        code, raw, _ = self.req("/")
        self.assertIn(b"<th>When</th>", raw)                                             # each time says its zone
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

    def test_deploy_syntax(self):
        ap = os.environ.get("ANSIBLE_PLAYBOOK") or shutil.which("ansible-playbook")
        if not ap:
            self.skipTest("no ansible-playbook")
        p = subprocess.run([ap, "--syntax-check", "-i", "localhost,", "playbooks/site_portal_deploy.yml"], cwd=ROOT,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(p.returncode, 0, p.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=1)
