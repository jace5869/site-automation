#!/usr/bin/env python3
"""1.92.0, the site portal's workflows (roles/site_portal_stack/files/site_portal.py, static/portal.js; the setup jobs' shared
plugins/module_utils/site_aap_workflows.py):
- the Actions page shows a workflow, not its parts: a labelled step of a portal workflow (found or built in) has no card of
  its own, unless it also has portal-alone; playbooks/stigman_update.yml and workflow_gate.yml never have one; the line
  "Hidden as workflow steps: N" names them and their workflows;
- AAP - set up portal actions never labels a step, and both setup jobs' Check says which steps carry the label (said,
  never removed);
- the approvals every viewer shares: about 3 AAP calls a cycle whatever the number waiting or looking (the perf test);
- the job page of a workflow waiting for approval: the steps in run order, Plan ready for a check that failed on purpose,
  the decision box with the check's plan (an approver) or the reason (anyone else); the plan's summary as text only.
Standard library only, with the stand-in AAP (tests/site_portal/fake_aap.py): python3 tests/test_portal_workflows.py"""
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FILES = os.path.join(ROOT, "roles", "site_portal_stack", "files")
sys.path.insert(0, os.path.join(ROOT, "plugins", "filter"))
sys.path.insert(0, os.path.join(ROOT, "plugins", "module_utils"))
sys.path.insert(0, FILES)
sys.path.insert(0, os.path.join(HERE, "site_portal"))
import ansible.module_utils  # noqa: E402
import site_aap  # noqa: E402
sys.modules.setdefault("ansible.module_utils.site_aap", site_aap)
ansible.module_utils.site_aap = site_aap
import site_aap_workflows as W  # noqa: E402
sys.modules.setdefault("ansible.module_utils.site_aap_workflows", W)
ansible.module_utils.site_aap_workflows = W
sys.path.insert(0, os.path.join(ROOT, "roles", "aap_portal_setup", "library"))
import site_aap_portal_setup as S  # noqa: E402
import site_portal as sp  # noqa: E402
import fake_aap  # noqa: E402

JDOE = "CN=DOE.JANE.Q.1234567890,OU=CONTRACTOR,OU=PKI,O=U.S. Government,C=US"
APPR = "CN=APPROVER.ALEX.B.3456789012,OU=USA,OU=PKI,O=U.S. Government,C=US"
ACTIONS = {
    "restart": {"title": "Restart a service", "area": "Disk & services", "summary": "Restarts a service after an approval.",
                "aap": {"type": "workflow", "name": "Service - restart"}, "approval": True,
                "vars": {"target": {"kind": "inventory_host"}, "finding_id": {"kind": "text", "max": 120, "pattern": "[a-z_:]{1,120}"}},
                "limit_from": "target"},
    "builtin_flow": {"title": "A built-in workflow", "area": "Other", "summary": "Runs a step that also carries the label.",
                     "aap": {"type": "workflow", "name": "Built-in flow"}, "approval": False, "vars": {}},
}
PLAN = {"title": "Service restart - what the check planned", "counts": {"changes": 2, "hosts": 1, "critical": 1, "warning": 1},
        "items": [{"host": "web01.example.mil", "what": "Restart crond (down since 05:52)", "level": "critical"},
                  {"host": "web01.example.mil", "what": "Clear the failed state of crond.service", "level": "warning"}]}


def cert(dn):
    return {"X-SSL-Client-Verify": "SUCCESS", "X-SSL-Client-S-DN": dn}


def gets():
    """The GETs the portal sent the stand-in AAP so far."""
    with open(fake_aap.LOG) as fh:
        return [json.loads(x) for x in fh if '"GET"' in x]


class Units(unittest.TestCase):
    def test_run_order_follows_the_links(self):
        steps = [{"node": 4, "name": "apply"}, {"node": 3, "name": "approval", "success": [4]}, {"node": 2, "name": "gate", "success": [3]},
                 {"node": 1, "name": "check", "failure": [2]}]
        self.assertEqual([s["name"] for s in sp.run_order(steps)], ["check", "gate", "approval", "apply"])
        # side by side (Linux and Windows checks), each with its gate, one approval: a level at a time
        steps = [{"node": 9, "name": "approval", "success": [10]}, {"node": 10, "name": "update"}, {"node": 7, "name": "gate L", "success": [9]},
                 {"node": 8, "name": "gate W", "success": [9]}, {"node": 5, "name": "check L", "failure": [7]}, {"node": 6, "name": "check W", "failure": [8]}]
        self.assertEqual([s["name"] for s in sp.run_order(steps)], ["check L", "check W", "gate L", "gate W", "approval", "update"])
        self.assertEqual([s["name"] for s in sp.run_order([{"name": "a"}, {"name": "b"}])], ["a", "b"])        # no links known: as AAP listed them

    def test_plan_ready_is_a_check_that_failed_on_purpose(self):
        gate_ok = {1: {"node": 1, "type": "job", "status": "failed", "failure": [2]}, 2: {"node": 2, "type": "job", "status": "successful"}}
        self.assertTrue(sp.plan_ready(gate_ok[1], gate_ok))
        gate_bad = {1: dict(gate_ok[1]), 2: {"node": 2, "type": "job", "status": "failed"}}
        self.assertFalse(sp.plan_ready(gate_bad[1], gate_bad))                    # the gate said: no plan - a real failure, red
        self.assertTrue(sp.plan_ready({"type": "job", "status": "failed", "has_plan": True}, {}))      # it left site_approval_summary
        self.assertFalse(sp.plan_ready({"type": "job", "status": "failed"}, {}))
        self.assertFalse(sp.plan_ready({"type": "job", "status": "successful", "has_plan": True}, {}))

    def test_the_summary_is_text_cut_short(self):
        v = {"title": "<b>x</b>", "counts": {"changes": "7", "hosts": 2},
             "items": [{"host": "web01.example.mil", "what": "<script>alert(1)</script> " + "x" * 400, "level": "CRITICAL"},
                       {"host": "web02.example.mil", "what": "two", "level": "oops"}, {"host": "", "what": ""}, "not a dict"]
             + [{"host": "web03.example.mil", "what": "item %d" % i, "level": "warning"} for i in range(60)]}
        p = sp.approval_summary(v)
        self.assertEqual(p["counts"], {"changes": 7, "hosts": 2, "critical": 1, "warning": 46})   # counts given are kept; the rest counted
        self.assertEqual([h["host"] for h in p["hosts"]], ["web01.example.mil", "web02.example.mil", "web03.example.mil"])
        self.assertEqual(p["hosts"][0]["items"][0]["level"], "critical")
        self.assertEqual(p["hosts"][1]["items"][0]["level"], "info")            # an unknown level: info
        self.assertLessEqual(len(p["hosts"][0]["items"][0]["what"]), 240)
        self.assertEqual((len(p["hosts"][2]["items"]), p["hosts"][2]["more"]), (10, 36))     # 50 items kept, 10 a host shown
        h = sp.plan_html(p)
        self.assertNotIn("<script>", h)
        self.assertIn("&lt;script&gt;", h)
        self.assertIn("and 36 more", h)
        self.assertIsNone(sp.approval_summary({"items": []}))
        self.assertIsNone(sp.approval_summary("text"))

    def test_the_closed_line(self):
        self.assertEqual(sp.plan_chips({"changes": 2, "hosts": 1, "critical": 1, "warning": 0}),
                         [("2 changes", "n"), ("1 host", "n"), ("1 critical", "critical")])

    def test_the_checks_artifact(self):
        import approval_summary_filters as A
        import stig_ckl_filters as C
        items = A.site_approval_items({"web01.example.mil": [{"path": "/var/tmp", "files": 12, "days": 10}, "plain line"]}, "warning",
                                      "Delete {files} file(s) in {path}, older than {days} days ({nope})")
        self.assertEqual(items, [{"host": "web01.example.mil", "what": "Delete 12 file(s) in /var/tmp, older than 10 days (?)", "level": "warning"},
                                 {"host": "web01.example.mil", "what": "plain line", "level": "warning"}])
        v = A.site_approval_summary((items + [{"host": "h2", "what": "x\n  y " * 80, "level": "BAD"}, {"what": ""}, None]) * 30, "  A  title ")
        self.assertEqual(v["title"], "A title")
        self.assertEqual(v["counts"], {"changes": 90, "hosts": 2, "critical": 0, "warning": 60})     # the whole counted, 50 items kept
        self.assertEqual(len(v["items"]), 50)
        self.assertTrue(all("\n" not in i["what"] and len(i["what"]) <= 200 for i in v["items"]))
        self.assertEqual(v["items"][2]["level"], "info")
        stig = C.stig_ckl_approval_items([
            {"kind": "create", "asset": "web01.example.mil", "collection": "SITEA Linux", "reviews": 412},
            {"kind": "move", "asset": "db01", "from": "Staging", "collection": "SITEA Linux", "reviews": 3},
            {"kind": "assign", "asset": "db01", "benchmark": "RHEL_9_STIG", "revision": "V2R4"},
            {"asset": "db01", "version": "RHEL-09-211010", "severity": "high", "result": "fail", "was": {"result": "pass"}},
            {"asset": "db01", "version": "RHEL-09-211020", "severity": "medium", "result": "pass"},
            {"asset": "web01.example.mil", "new_asset": True, "result": "pass", "version": "RHEL-09-1"}])
        self.assertEqual([(x["host"], x["level"], x["what"]) for x in stig], [
            ("web01.example.mil", "warning", "Create the asset in SITEA Linux, then write its 412 review(s)"),
            ("db01", "warning", "Move it from Staging to SITEA Linux, then write its 3 review(s)"),
            ("db01", "warning", "Assign RHEL_9_STIG V2R4 to it"),
            ("db01", "critical", "RHEL-09-211010 CAT I: Open (was Not a Finding)"),
            ("db01", "low", "RHEL-09-211020 CAT II: Not a Finding (no review yet)")])            # a new asset's reviews: in its own line

    def test_side_by_side_checks_are_one_summary(self):
        m = sp.merge_summaries([{"title": "Linux", "counts": {"changes": 2, "hosts": 1, "critical": 1}, "items": [{"host": "a", "what": "x"}]},
                                None, {"title": "Windows", "counts": {"changes": 3, "hosts": 2, "warning": 2}, "items": [{"host": "b", "what": "y"}]}])
        self.assertEqual((m["title"], m["counts"], len(m["items"])), ("Linux", {"changes": 5, "hosts": 3, "critical": 1, "warning": 2}, 2))
        self.assertEqual(sp.merge_summaries([None]), None)

    def test_portal_js_keeps_the_decision_across_the_swap(self):
        with open(os.path.join(FILES, "static", "portal.js")) as fh:
            js = fh.read()
        self.assertIn("function decider(row, aid, after)", js)
        self.assertIn('if (oldBox && newBox && oldBox.dataset.hold) newBox.replaceWith(oldBox);', js)
        self.assertIn("(+pane.dataset.wait || 3000)", js)
        self.assertIn('toast("Approval waiting: " + w.title', js)
        # the toasts (the director's notes): none for the job page that is open, once a session, two at a time, held while pointed at
        self.assertIn("if (document.querySelector('#jd[data-job=\"' + w.workflow_job + '\"]') || document.body.dataset.page === \"approvals\") continue;", js)
        self.assertIn('const at = w <= 900 ? "tb" : w >= 1500 ? "tr" : two ? "tl" : "ti";', js)
        self.assertIn('sessionStorage.getItem("site-portal-approvals")', js)
        self.assertIn("const TOAST_SHOWN = 2;", js)
        self.assertIn('more.textContent = "+" + queue.length + " more";', js)
        self.assertIn('t.addEventListener("mouseenter", hold); t.addEventListener("focusin", hold);', js)
        with open(os.path.join(FILES, "static", "portal.css")) as fh:
            css = fh.read()
        self.assertIn("html.toasting main{padding-right:calc(340px + 40px)}", css)
        self.assertNotIn("innerHTML", js)
        self.assertIn("row.replaceChildren(msg);", js)                            # no Sure? left after a decision (the list reloads)
        self.assertIn('a.href = BASE + (href || "jobs/" + id);', js)


class StubApi(object):
    """The few reads of the setup jobs' step check: the labelled workflows and job templates, a workflow's nodes, a template's labels."""
    base = "/api/controller/v2/"

    def __init__(self, wfs, jts):
        self.wfs, self.jts = wfs, jts

    def call(self, method, path, body=None):
        p, _, q = path.partition("?")
        if p == "workflow_job_templates/":
            return 200, {"next": None, "results": [w for w in self.wfs if "labels__name=portal" in q and "portal" in w["labels"]]}
        if p == "job_templates/":
            return 200, {"next": None, "results": [dict(j, summary_fields={"labels": {"count": len(j["labels"]), "results": [{"name": n} for n in j["labels"]]}})
                                                   for j in self.jts if "labels__name=portal" in q and "portal" in j["labels"]]}
        for w in self.wfs:
            if p == "workflow_job_template_nodes/" and "workflow_job_template=%d" % w["id"] in q:
                return 200, {"next": None, "results": [{"summary_fields": {"unified_job_template": {"id": j, "unified_job_type": "job" if j else "workflow_approval"}}}
                                                       for j in w["steps"]]}
        return 404, {"detail": "no"}


class SetupCheck(unittest.TestCase):
    WFS = [{"id": 50, "name": "Linux - fapolicyd - daily", "labels": ["portal"], "steps": [1, 0, 2, 3]},
           {"id": 51, "name": "Not on the portal", "labels": [], "steps": [4]}]
    JTS = [{"id": 1, "name": "fapolicyd - check", "labels": ["portal"], "playbook": "playbooks/fapolicyd.yml"},
           {"id": 2, "name": "Disk - report", "labels": ["portal", "portal-alone"], "playbook": "playbooks/disk_report.yml"},
           {"id": 3, "name": "STIG Manager - update", "labels": ["portal", "portal-alone"], "playbook": "playbooks/stigman_update.yml"},
           {"id": 4, "name": "A step elsewhere", "labels": ["portal"], "playbook": "playbooks/x.yml"},
           {"id": 5, "name": "Gate", "labels": ["portal"], "playbook": "playbooks/workflow_gate.yml"}]

    def sync(self):
        return W.Sync(StubApi(self.WFS, self.JTS), {}, True, S.MARK, "overwrite")

    def test_the_steps_of_the_labelled_workflows(self):
        self.assertEqual(self.sync().portal_steps("portal"), {1: ["Linux - fapolicyd - daily"], 2: ["Linux - fapolicyd - daily"], 3: ["Linux - fapolicyd - daily"]})

    def test_the_check_says_each_one_never_removes(self):
        s = self.sync()
        self.assertEqual(s.step_labels("portal", "portal-alone"), 3)
        said = dict((r["name"], r["why"]) for r in s.results)
        self.assertEqual(set(r["action"] for r in s.results), {"attention"})
        self.assertIn("remove the portal label from fapolicyd - check (a step of Linux - fapolicyd - daily)", said["fapolicyd - check: portal"])
        self.assertIn("it runs playbooks/stigman_update.yml, which does nothing without a workflow's plan", said["STIG Manager - update: portal"])  # portal-alone or not
        self.assertIn("playbooks/workflow_gate.yml", said["Gate: portal"])
        self.assertNotIn("Disk - report: portal", said)                 # portal-alone: a useful report on its own
        self.assertNotIn("A step elsewhere: portal", said)              # a step of a workflow that is not on the portal

    def test_publish_never_labels_a_step(self):
        api = __import__("test_portal_publish").FakeApi(["fapolicyd - check", "Linux - fapolicyd - daily", "STIG Manager - update", "Weekly report"],
                                                        kinds={"Linux - fapolicyd - daily": "workflow_job_templates"})
        next(r for r in api.rows["job_templates"] if r["name"] == "STIG Manager - update")["playbook"] = "playbooks/stigman_update.yml"
        p = {"spec": {}, "actions": {}, "organization": "Default", "project": "P", "execution_environment": "", "inventories": {}, "credentials": {},
             "portal_user": "", "portal_auditor": False, "overwrite": False, "schedules": [], "scorecard": {}, "publish": ["*"], "publish_area": {},
             "unpublish": [], "publish_label": "portal", "area_label": "portal-area:", "publish_max": 50}
        s = S.Setup(api, p, False)
        plan = s.publish_plan()
        steps = {next(r for r in api.rows["job_templates"] if r["name"] == "fapolicyd - check")["id"]: ["Linux - fapolicyd - daily"]}
        s.publish_apply(plan, steps)
        self.assertEqual(api.labels_of("fapolicyd - check"), [])
        self.assertEqual(api.labels_of("STIG Manager - update"), [])
        self.assertEqual(api.labels_of("Linux - fapolicyd - daily"), ["portal"])
        self.assertEqual(api.labels_of("Weekly report"), ["portal"])
        said = dict((r["name"], r) for r in s.results)
        self.assertEqual(said["fapolicyd - check: portal"]["action"], "skipped")
        self.assertIn("a step of Linux - fapolicyd - daily: the workflow carries the label, never its steps", said["fapolicyd - check: portal"]["why"])
        self.assertIn("does nothing without a workflow's plan", said["STIG Manager - update: portal"]["why"])


class Review1920(unittest.TestCase):
    """Neon's review of 1.92.0: the setup jobs and the built-in workflows' steps."""
    def test_job_templates_never_labels_a_step(self):
        sys.path.insert(0, os.path.join(ROOT, "roles", "aap_templates", "library"))
        import site_aap_templates as T
        t = T.Templates(None, {"variables_dir": ROOT}, True)
        steps = t.workflow_steps()
        self.assertIn("Disk - clean up (approved)", steps["Disk - clean up one mount"])          # the portal's own workflow
        self.assertIn("STIG - full scan and upload", steps["STIG - full scan - Linux check"])    # the catalog's
        self.assertIn("Service - restart", steps["Service - look (Linux)"])
        src = open(os.path.join(ROOT, "roles", "aap_templates", "library", "site_aap_templates.py")).read()
        self.assertIn("if name in steps_of or str(e.get(\"playbook\") or \"\") in NEVER_ALONE:", src)

    def test_portal_setup_counts_the_built_in_workflows(self):
        src = open(os.path.join(ROOT, "roles", "aap_portal_setup", "library", "site_aap_portal_setup.py")).read()
        self.assertIn("steps = self.portal_steps(label, [r for k, r in plan[\"publish\"] if k == \"workflow_job_templates\"] + builtin)", src)
        self.assertIn('for n in w.get("nodes") or []:', src)                                 # a workflow a Check would only make: its spec


class On1990(unittest.TestCase):
    """1.92.0 carried onto 1.99.0's discovery (batch E): a template's details are read in parallel and kept until they change -
    its labels, playbook and a workflow's steps are kept with them, so a pass from the kept reads still hides the steps; a step
    read that fails marks the pass incomplete; a built-in workflow AAP says is not there is looked up again only after
    RESOLVE_BACKOFF (one that got no answer: next pass, and the pass is incomplete)."""

    class Api(object):
        base, user = "/api/controller/v2/", "portal"

        def __init__(self, route):
            self.route, self.paths = route, []

        def call(self, method, path, body=None):
            self.paths.append(path)
            return self.route(method, path, body)

        def probe(self):
            return ""

    @staticmethod
    def page(rows):
        return 200, {"count": len(rows), "next": None, "results": rows}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = sp.Config({"PORTAL_DATA": self.tmp, "PORTAL_AAP_URL": "https://controller.example.test"})
        lab = lambda *n: {"count": len(n), "results": [{"name": x} for x in n]}       # noqa: E731
        self.rows = [{"type": "job_template", "id": 1, "name": "fapolicyd - check", "playbook": "playbooks/fapolicyd.yml"},
                     {"type": "job_template", "id": 2, "name": "Disk - report", "playbook": "playbooks/disk.yml"},
                     {"type": "workflow_job_template", "id": 3, "name": "Linux - fapolicyd - daily"}]
        for r, labels in zip(self.rows, (lab("portal"), lab("portal"), lab("portal"))):
            r.update(modified="2026-11-21T00:00:00Z", survey_enabled=False, summary_fields={"labels": labels, "user_capabilities": {"start": True}})
        self.nodes = {3: [{"id": 31, "unified_job_template": 1, "summary_fields": {"unified_job_template": {"id": 1, "unified_job_type": "job"}}}]}
        self.down = set()                               # paths that get no answer (503)
        self.api = self.Api(self.route)
        self.aap = sp.Aap(self.cfg, api=self.api)

    def route(self, method, path, body):
        if any(path.startswith(d) for d in self.down):
            return 503, {}
        if path.startswith("unified_job_templates/?"):
            return self.page(self.rows)
        for r in self.rows:
            if path == "%ss/%d/" % (r["type"], r["id"]):
                return 200, r
        if "?role_level=" in path:
            return self.page([r for r in self.rows if path.startswith(r["type"] + "s/")])
        if path.endswith("/launch/"):
            return 200, {}
        m = re.match(r"workflow_job_templates/(\d+)/workflow_nodes/", path)
        if m:
            return self.page(self.nodes.get(int(m.group(1)), []))
        if path.startswith("workflow_job_templates/?name="):
            return self.page([])                        # a built-in workflow that is not in AAP
        return 404, {}

    def test_kept_reads_keep_the_steps_hidden(self):
        first = self.aap.discover({})
        self.assertEqual(first["hidden"], [("fapolicyd - check", "a step of Linux - fapolicyd - daily")])
        self.assertEqual(sorted(first["actions"]), ["jt_2", "wf_3"])
        self.api.paths.clear()
        again = self.aap.discover({})                   # 1.99.0: the one list, every detail kept
        self.assertEqual(self.api.paths, ["unified_job_templates/?type=job_template,workflow_job_template&order_by=name&page_size=200"])
        self.assertEqual((again["hidden"], sorted(again["actions"])), (first["hidden"], ["jt_2", "wf_3"]))
        self.assertFalse(again["incomplete"])

    def test_a_step_read_that_fails_marks_the_pass_incomplete(self):
        self.down.add("workflow_job_templates/3/workflow_nodes/")
        got = self.aap.discover({})
        self.assertTrue(got["incomplete"])              # the last whole list stays (the portal keeps it)
        self.assertNotIn("wf_3", got["actions"])

    def test_a_missing_built_in_is_looked_up_again_only_after_the_back_off(self):
        from unittest import mock
        builtin = {"flow": {"title": "A built-in workflow", "aap": {"type": "workflow", "name": "Built-in flow"}, "vars": {}, "limit_from": ""}}
        asks = lambda: len([p for p in self.api.paths if p.startswith("workflow_job_templates/?name=")])      # noqa: E731
        with mock.patch.object(sp.time, "monotonic", return_value=1000.0) as clock:
            got = self.aap.discover(builtin)
            self.assertEqual(asks(), 1)
            self.assertFalse(got["incomplete"])         # "not there" is an answer
            self.aap.discover(builtin)
            self.assertEqual(asks(), 1)                 # not on every pass
            clock.return_value = 1000.0 + sp.Aap.RESOLVE_BACKOFF
            self.aap.discover(builtin)
            self.assertEqual(asks(), 2)
            self.down.add("workflow_job_templates/?name=")
            clock.return_value += 2 * sp.Aap.RESOLVE_BACKOFF
            self.assertTrue(self.aap.discover(builtin)["incomplete"])          # no answer: incomplete, and asked again next pass
            self.down.clear()
            self.assertFalse(self.aap.discover(builtin)["incomplete"])
            self.assertEqual(asks(), 4)


class Live(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        fake_aap.RUN = 1.5
        fake_aap.LOG = os.path.join(cls.tmp, "aap.jsonl")
        cls.aap = fake_aap.serve(0, stop=True)
        T = fake_aap.STATE["templates"]
        T["workflow_job_templates"].append({"id": fake_aap.new_id(), "name": "Service - restart", "ask_variables_on_launch": True, "ask_limit_on_launch": True,
                                            "survey_enabled": False, "approval": True, "plan": True,
                                            "arts": {"site_report_url": "https://reports.example.mil/service-watch/job-plan.html", "site_approval_summary": PLAN}})
        cls.seeded = fake_aap.seed_steps()
        bstep = fake_aap.add_template("job_templates", "Built-in step", ["portal"])
        fake_aap.STATE["templates"]["job_templates"][-1]["playbook"] = "playbooks/x.yml"
        T["workflow_job_templates"].append({"id": fake_aap.new_id(), "name": "Built-in flow", "ask_variables_on_launch": True, "ask_limit_on_launch": False,
                                            "survey_enabled": False, "labels": None,
                                            "nodes": [{"id": 7700, "unified_job_template": bstep,
                                                       "summary_fields": {"unified_job_template": {"id": bstep, "name": "Built-in step", "unified_job_type": "job"}}}]})
        cls.bstep = bstep
        with open(os.path.join(cls.tmp, "actions.json"), "w") as fh:
            json.dump({"actions": ACTIONS}, fh)
        reports = os.path.join(cls.tmp, "reports")
        os.makedirs(reports)
        cls.cfg = sp.Config({"PORTAL_DATA": os.path.join(cls.tmp, "data"), "PORTAL_REPORTS": reports, "PORTAL_ACTIONS": os.path.join(cls.tmp, "actions.json"),
                             "PORTAL_AAP_URL": "http://127.0.0.1:%d" % cls.aap.server_address[1], "PORTAL_AAP_TOKEN": "fake-token",
                             "PORTAL_INVENTORY": "Site hosts", "PORTAL_APPROVERS": '["APPROVER.ALEX.B.3456789012"]', "PORTAL_REQUESTERS": "[]",
                             "PORTAL_TIME_ZONE": "UTC", "PORTAL_BANNER": {"on": False}})
        sp.sys.stdout = open(os.devnull, "w")
        cls.portal = sp.Portal(cls.cfg)
        cls.portal.aap.connect()
        cls.portal.aap.resolve(cls.portal.actions)
        cls.portal.refresh(force=True)
        cls.srv = sp.PortalServer(("127.0.0.1", 0), sp.make_handler(cls.cfg, cls.portal, os.path.join(FILES, "static")))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.host = "127.0.0.1:%d" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.aap.shutdown()
        sp.sys.stdout = sys.__stdout__
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(self, path, dn=JDOE):
        r = urllib.request.Request("http://%s%s" % (self.host, path), headers=cert(dn))
        with urllib.request.urlopen(r, timeout=20) as f:
            return f.read().decode()

    def launch(self, target, finding="service_down:crond"):
        cn = sp.who(cert(JDOE))[0]
        h = dict(cert(JDOE), **{"Content-Type": "application/json", "Host": "portal.example.mil", "Origin": "https://portal.example.mil",
                                "X-Portal-Token": self.portal.token(cn)})
        body = json.dumps({"vars": {"target": target, "finding_id": finding}}).encode()
        r = urllib.request.Request("http://%s/api/actions/restart" % self.host, data=body, method="POST", headers=h)
        with urllib.request.urlopen(r, timeout=20) as f:
            return json.loads(f.read())["job_id"]

    # ---- the Actions page: the workflow, not its parts
    def test_1_steps_are_hidden_with_their_reason(self):
        found = self.portal.found
        cards = set(a["aap"]["name"] for a in self.portal.actions.values())
        self.assertIn("Linux - fapolicyd - daily", cards)
        self.assertNotIn("fapolicyd - check", cards)                   # a step of a portal workflow
        self.assertNotIn("STIG Manager - update", cards)               # never alone, its portal-alone label or not
        self.assertIn("Disk - report (step)", cards)                   # portal-alone: a useful report on its own
        self.assertNotIn("Built-in step", cards)                       # a step of a built-in workflow too
        hidden = dict(found["hidden"])
        self.assertEqual(hidden["fapolicyd - check"], "a step of Linux - fapolicyd - daily")
        self.assertEqual(hidden["Built-in step"], "a step of A built-in workflow")
        self.assertIn("never on its own: it runs playbooks/stigman_update.yml", hidden["STIG Manager - update"])
        self.assertNotIn("fapolicyd - check", [n for n, _w in found["skipped"]])
        h = self.req("/")
        self.assertIn("Hidden as workflow steps: <b>3</b>", h)
        self.assertIn("<b>fapolicyd - check</b> - a step of Linux - fapolicyd - daily", h)
        self.assertIn("the label <code>portal-alone</code> too", h)

    # ---- the approvals every viewer shares
    def test_2_about_three_calls_a_cycle_whatever_the_number(self):
        T = fake_aap.STATE["templates"]["workflow_job_templates"]
        tid = next(t["id"] for t in T if t["name"] == "Service - restart")
        made = []
        for i in range(30):                             # 30 waiting, each with its check job (its report and plan)
            wid, cid, aid = fake_aap.new_id(), fake_aap.new_id(), fake_aap.new_id()
            fake_aap.STATE["jobs"][cid] = {"id": cid, "type": "job", "name": "check", "created": time.time() - 60, "force": "failed",
                                           "arts": {"site_report_url": "https://reports.example.mil/x/%d.html" % i, "site_approval_summary": PLAN}}
            fake_aap.STATE["jobs"][aid] = {"id": aid, "type": "workflow_approval", "name": "Approve", "created": time.time(), "status": "pending"}
            fake_aap.STATE["jobs"][wid] = {"id": wid, "type": "workflow_job", "name": "Service - restart", "created": time.time(), "nodes": [cid, aid],
                                           "template": tid, "launch_type": "scheduled", "schedule": "Nightly"}
            fake_aap.STATE["approvals"][aid] = {"id": aid, "name": "Approve", "status": "pending", "created": time.time(), "workflow": wid}
            made.append((wid, cid, aid))
        try:
            self.portal.pend, self.portal.wf_info = None, {}
            n0 = len(gets())
            code, d = self.portal.approvals(sp.who(cert(APPR))[0])
            cold = gets()[n0:]
            self.assertEqual(code, 200)
            self.assertGreaterEqual(len(d["approvals"]), 30)
            self.assertLessEqual(len(cold), 4, [g["path"] for g in cold])          # the list, the workflow jobs, their nodes, the check jobs
            a = next(x for x in d["approvals"] if x["workflow_job"] == made[0][0])
            self.assertEqual(a["plan"]["counts"]["changes"], 2)
            self.assertEqual(a["report"]["url"], "https://reports.example.mil/x/0.html")
            n1 = len(gets())                            # more viewers and their badges, within the 10 s: no call at all
            for dn in (APPR, JDOE, "CN=ROE.RICHARD.A.2345678901,OU=USN,OU=PKI,O=U.S. Government,C=US"):
                self.portal.approvals(sp.who(cert(dn))[0])
                self.portal.summaries.clear()
                self.portal.summary(sp.who(cert(dn))[0])
            self.assertEqual([g["path"] for g in gets()[n1:] if "approval" in g["path"] or "workflow" in g["path"]], [])
            self.portal.pend = (time.time() - sp.Portal.PENDING_TTL - 1, self.portal.pend[1])           # the next cycle: the list alone
            n2 = len(gets())
            self.portal.approvals(sp.who(cert(APPR))[0])
            self.assertEqual([g["path"] for g in gets()[n2:]], ["workflow_approvals/"])
        finally:
            for wid, cid, aid in made:
                for k in (wid, cid, aid):
                    fake_aap.STATE["jobs"].pop(k, None)
                fake_aap.STATE["approvals"].pop(aid, None)
            self.portal.pend = None

    def test_4_a_decision_reads_aap_again(self):
        wid = self.launch("web03.example.mil", "service_down:atd")
        aid = next(a for a in fake_aap.STATE["approvals"].values() if a["workflow"] == wid)["id"]
        cn = sp.who(cert(APPR))[0]
        self.assertTrue(self.portal.approval_of(wid, cn))                       # in the shared read
        fake_aap.STATE["approvals"][aid]["status"] = "successful"                # decided in AAP 2 s ago - the shared read does not know
        fake_aap.STATE["jobs"][aid]["status"] = "successful"
        code, r = self.portal.decide(cn, "192.0.2.10", aid, "approve")
        self.assertEqual(code, 404)
        self.assertEqual(r["error"], "Approval %d is not pending any more (decided already, or timed out)." % aid)
        self.assertFalse([x for x in gets() if x["path"] == "workflow_approvals/%d/approve/" % aid])   # nothing sent to AAP
        # and one that timed out since the shared read is not offered either
        wid2 = self.launch("web03.example.mil", "service_down:sshd")
        a2 = next(a for a in fake_aap.STATE["approvals"].values() if a["workflow"] == wid2)
        a2["expires"] = time.time() + 1.5                                        # times out within the shared 10 s
        self.portal.pend = None
        self.assertTrue(self.portal.approval_of(wid2, cn))
        time.sleep(1.7)
        self.assertIsNone(self.portal.approval_of(wid2, cn))                    # the same shared read: left out by the time it says

    def test_5_the_job_page_can_post(self):
        wid = self.launch("web01.example.mil", "service_down:chronyd")
        self.assertIn('<meta name="portal-token" content="', self.req("/jobs/%d" % wid, dn=APPR))           # Approve / Deny post with it
        s = self.portal.summary(sp.who(cert(APPR))[0])
        mine = [w for w in s["waiting"] if w["workflow_job"] == wid]
        self.assertEqual(mine[0]["portal"], True)                                 # its job page opens; not started here: Approvals

    def test_6_a_built_in_workflow_not_resolved_yet(self):
        aap = self.portal.aap
        name = "builtin_flow"
        keep = aap.ids.pop(name, None)                                            # AAP was not there when the portal started
        try:
            got = aap.discover(self.portal.builtin)
            self.assertIn(("Built-in step", "a step of A built-in workflow"), got["hidden"])
            self.assertTrue(aap.ids.get(name, (None, ""))[0])                     # resolved in the pass
        finally:
            if keep and name not in aap.ids:
                aap.ids[name] = keep

    # ---- the job page of a workflow waiting for approval
    def test_3_the_job_page_waits_with_the_plan(self):
        wid = self.launch("web01.example.mil")
        h = self.req("/jobs/%d" % wid, dn=APPR)
        self.assertIn('id="decide"', h)
        self.assertIn("<b>Waiting for your decision</b> - the check planned 2 changes on 1 host.", h)
        self.assertRegex(h, r'<div class="dbtns" data-aid="\d+"><button type="button" class="pri" data-d="approve">Approve</button>')
        self.assertIn('href="https://reports.example.mil/service-watch/job-plan.html" target="_blank" rel="noopener">Open the full report', h)
        self.assertIn("<span class=\"t\">What it will change</span>", h)
        self.assertIn("Restart crond (down since 05:52)", h)
        self.assertIn('data-wait="10000"', h)
        self.assertIn("refreshes every 10 s while it waits", h)
        # the steps in run order (AAP lists them apply, approval, gate, check), the check Plan ready - never a red Failed
        names = [x for x in ("Service - restart - check", "Workflow gate - has a plan", "Approve the changes", "Service - restart - apply")]
        self.assertEqual(sorted(names, key=h.index), names)
        self.assertIn('<span class="chip s-plan">', h)
        self.assertIn("Stopped on purpose: 2 changes wait for a decision", h)
        self.assertIn("The plan needs an approval", h)
        self.assertNotIn("s-critical\">" + sp.icon("bad") + "Failed", h)
        # the one who started it is no approver: the plan, the reason by its setting's name, the way to Approvals - no buttons
        h = self.req("/jobs/%d" % wid)
        self.assertIn("<b>Waiting for an approver</b>", h)
        self.assertIn("<b>You are not in <code>portal_approvers</code></b> - only the approvers decide.", h)
        self.assertIn('<a href="%sapprovals">Approvals</a>' % self.cfg.path, h)
        self.assertNotIn('data-d="approve"', h)
        self.assertIn("What it will change", h)
        # the Approvals page's data: the plan and the report for every viewer who sees it
        cn = sp.who(cert(JDOE))[0]
        a = self.portal.approval_of(wid, cn)
        self.assertEqual((a["why"], a["may_decide"]), ("approvers", False))
        self.assertEqual(a["plan"]["hosts"][0]["items"][0], {"what": "Restart crond (down since 05:52)", "level": "critical"})


if __name__ == "__main__":
    unittest.main(verbosity=1)
