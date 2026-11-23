#!/usr/bin/env python3
"""1.107.0: every workflow of the catalog (aap/job_templates.yml, from aap/job_descriptions.yml) has its nodes - or is in an
explained skip list - as docs/WORKFLOWS_AND_SCHEDULES.md and the feature guides describe them, and "AAP - set up job
templates" (roles/aap_templates/library/site_aap_template_workflows.py, plugins/module_utils/site_aap_workflows.py) can
build each one: every step a catalog template, every link to a node of it, no cycle, an approval with a name and a
timeout, the gate's variable the one its check hands on and its approved step reads, a node's Job type and Variables only
on a template that takes them; the builder's node-by-node differences, its survey written once, its step checks.
    python3 tests/test_catalog_workflows.py"""
import collections
import glob
import os
import re
import sys
import unittest
from unittest.mock import Mock

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "module_utils"))
import ansible.module_utils  # noqa: E402
import site_aap  # noqa: E402
sys.modules.setdefault("ansible.module_utils.site_aap", site_aap)
ansible.module_utils.site_aap = site_aap
import site_aap_workflows as SW  # noqa: E402
sys.modules.setdefault("ansible.module_utils.site_aap_workflows", SW)
ansible.module_utils.site_aap_workflows = SW
sys.path.insert(0, os.path.join(REPO, "roles", "aap_portal_setup", "library"))
import site_aap_portal_setup as S  # noqa: E402
sys.path.insert(0, os.path.join(REPO, "roles", "aap_templates", "library"))
import site_aap_template_workflows as W  # noqa: E402

with open(os.path.join(REPO, "aap", "job_templates.yml")) as _f:
    CAT = yaml.safe_load(_f)
TPL = dict((t["name"], t) for t in CAT["job_templates"])
WFS = CAT["workflows"]
GATE = "Workflow gate - has a plan"
# The catalog's workflows left without nodes, and why - nothing else
REPLACED = set(n for w in WFS for n in w.get("replaces") or [])
# Approvals whose guide gives no timeout on purpose (it waits for the host to come back)
NO_TIMEOUT = {("Horizon - host maintenance (workflow)", "back")}
# Templates whose node gives Variables they take from the survey the guides tell you to make (docs/SETUP_AAP.md step 6,
# ADDING_ACT.md step 3, WINDOWS.md) - never Prompt on launch for Variables (anyone could then set any variable)
SURVEY_TEMPLATES = {"Health check": {"health_checks", "use_act", "site_act_level"}, "Troubleshoot": {"ts_area", "use_act", "site_act_level"},
                    "Windows health check": {"health_checks"}, "VM - deploy from ServiceNow ticket": {"vm_deploy_phase"}}


def docs_text():
    out = []
    for p in sorted(glob.glob(os.path.join(REPO, "docs", "*.md"))):
        with open(p, encoding="utf-8") as f:
            out.append(" ".join(f.read().split()))
    return "\n".join(out)


def role_text(playbook):
    """A playbook and the roles it names, as text (what it sets and reads)."""
    path = os.path.join(REPO, playbook)
    with open(path) as f:
        src = f.read()
    texts = [src]
    for r in sorted(set(re.findall(r"(?:name|role): ([a-z_]+)\s*\n", src))):
        for dp, _d, fns in os.walk(os.path.join(REPO, "roles", r)):
            for fn in fns:
                if fn.endswith((".yml", ".py", ".j2")):
                    with open(os.path.join(dp, fn), encoding="utf-8", errors="replace") as f:
                        texts.append(f.read())
    return "\n".join(texts)


class Catalog(unittest.TestCase):
    def test_every_workflow_has_nodes_or_a_reason(self):
        built, left, deferred = [], [], []
        for w in WFS:
            if w.get("nodes"):
                built.append(w["name"])
                self.assertNotIn("made_by", w, w["name"])
                self.assertNotIn(w["name"], REPLACED, w["name"])
            elif w.get("deferred"):
                left.append(w["name"])
                deferred.append(w["name"])
            elif w.get("made_by") == "AAP - set up portal actions":
                left.append(w["name"])               # the site portal's own: that job makes it from workflows.yml
            else:
                self.assertIn(w["name"], REPLACED, "%s: no nodes, not the portal's, not replaced - say why" % w["name"])
                left.append(w["name"])
        self.assertEqual(len(WFS), 53)
        self.assertEqual(len(built), 41)
        self.assertEqual(deferred, ["VM - deploy from ServiceNow ticket (workflow)"])
        self.assertEqual(sorted(REPLACED & set(left)), ["STIG Manager updated, with an approval",
                                                         "STIG Manager updated, with an approval (Windows)"])
        with open(os.path.join(REPO, "roles", "site_portal_stack", "files", "workflows.yml")) as f:
            portal = set(x["name"] for x in yaml.safe_load(f)["workflows"])
        self.assertEqual(set(w["name"] for w in WFS if w.get("made_by")), portal)

    def test_the_spec_checker_takes_them(self):
        """The portal setup's own checker (ids, links to a node of it, either a template or an approval, a named approval)."""
        spec = {"templates": [{"name": t["name"], "playbook": t["playbook"]} for t in CAT["job_templates"]],
                "workflows": [dict(w, number=i) for i, w in enumerate(WFS) if w.get("nodes")]}
        self.assertEqual(S.check_spec(spec), [])

    def test_every_step_a_catalog_template_and_its_playbooks(self):
        for w in (w for w in WFS if w.get("nodes")):
            got = collections.Counter()
            for n in w["nodes"]:
                if n.get("template"):
                    self.assertIn(n["template"], TPL, "%s: node %s" % (w["name"], n["id"]))
                    self.assertNotIn("made_by", TPL[n["template"]] if n["template"] not in (GATE, "STIG Manager - update") else {},
                                     "%s: %s is the portal job's" % (w["name"], n["template"]))
                    got[TPL[n["template"]]["playbook"]] += 1
            # the gate once in playbooks however many gates (the drift scan's two)
            gate = "playbooks/workflow_gate.yml"
            self.assertEqual(+collections.Counter(dict(got, **{gate: 0})), +collections.Counter(dict(collections.Counter(w["playbooks"]), **{gate: 0})),
                             "%s: its steps are not its playbooks" % w["name"])
            self.assertEqual(gate in got, gate in w["playbooks"], w["name"])

    def test_links_and_no_cycle(self):
        for w in (w for w in WFS if w.get("nodes")):
            ids = [n["id"] for n in w["nodes"]]
            self.assertEqual(len(ids), len(set(ids)), w["name"])
            kids = dict((n["id"], [c for k in ("success", "failure", "always") for c in n.get(k) or []]) for n in w["nodes"])
            for i, cs in kids.items():
                for c in cs:
                    self.assertIn(c, ids, "%s: %s -> %s" % (w["name"], i, c))
                self.assertEqual(len(cs), len(set(cs)), "%s: node %s links one node twice" % (w["name"], i))
            state = {}

            def visit(i, path):
                self.assertNotEqual(state.get(i), 1, "%s: a cycle through %s" % (w["name"], " -> ".join(path + [i])))
                if state.get(i) == 2:
                    return
                state[i] = 1
                for c in kids[i]:
                    visit(c, path + [i])
                state[i] = 2
            for i in ids:
                visit(i, [])
            roots = [i for i in ids if not any(i in cs for cs in kids.values())]
            self.assertTrue(roots, w["name"])
            for r in roots:                       # a root runs at launch: never an approval, never the gate or an update
                n = next(x for x in w["nodes"] if x["id"] == r)
                self.assertTrue(n.get("template"), "%s: approval %s at the start" % (w["name"], r))
                self.assertNotIn(n["template"], (GATE, "STIG Manager - update"), w["name"])

    def test_approvals(self):
        names = docs_text()
        for w in (w for w in WFS if w.get("nodes")):
            for n in w["nodes"]:
                a = n.get("approval")
                if not a:
                    continue
                self.assertTrue(str(a.get("name") or "").strip(), w["name"])
                self.assertIsInstance(a.get("timeout"), int, w["name"])
                if (w["name"], n["id"]) in NO_TIMEOUT:
                    self.assertEqual(a["timeout"], 0)
                else:
                    self.assertGreater(a["timeout"], 0, "%s: approval %s needs a timeout" % (w["name"], n["id"]))
                if not w.get("portal"):           # the STIG jobs' own (1.83.0, docs/STIG_JOBS.md): named by the catalog
                    self.assertTrue(" ".join(a["name"].split()) in names, "%s: approval name not in the docs: %s" % (w["name"], a["name"]))
                    if a.get("description"):
                        self.assertTrue(" ".join(a["description"].split()) in names, "%s: approval description not in the docs" % w["name"])
                # an approval never follows a failure or an always link straight from a check: the gate is between
                parents = [(p, k) for p in w["nodes"] for k in ("success", "failure", "always") if n["id"] in (p.get(k) or [])]
                self.assertTrue(parents, w["name"])
                for p, k in parents:
                    if (w["name"], k) == ("Fix with approval (ACT)", "failure"):
                        continue                  # ACT's fix: no gate (its check hands on an analysis for every host)
                    self.assertEqual(k, "success", "%s: approval %s after a %s link" % (w["name"], n["id"], k))
                # only an approved step after it (on success = approved)
                for k in ("failure", "always"):
                    self.assertFalse(n.get(k), "%s: approval %s has a %s link" % (w["name"], n["id"], k))

    def test_the_gates(self):
        """The gate's variable is what its check hands on (set_stats) and what the approved step reads; its check reaches it
        on failure (a check that fails on purpose)."""
        docs = docs_text()
        n_gates = 0
        for w in (w for w in WFS if w.get("nodes")):
            nodes = dict((n["id"], n) for n in w["nodes"])
            for g in (n for n in w["nodes"] if n.get("template") == GATE):
                n_gates += 1
                var = (g.get("extra_data") or {}).get("workflow_gate_var")
                self.assertTrue(var and re.match(r"^[a-z_]+$", var), w["name"])
                self.assertEqual(set(g["extra_data"]), {"workflow_gate_var"}, w["name"])
                parents = [(p, k) for p in w["nodes"] for k in ("success", "failure", "always") if g["id"] in (p.get(k) or [])]
                self.assertEqual(len(parents), 1, w["name"])
                check, link = parents[0]
                self.assertEqual(link, "failure", "%s: the gate after its check's failure" % w["name"])
                src = role_text(TPL[check["template"]]["playbook"])
                self.assertTrue(re.search(r"\b%s\b" % var, src), "%s: %s does not hand on %s" % (w["name"], check["template"], var))
                self.assertEqual(g.get("success"), [g["success"][0]], w["name"])
                appr = nodes[g["success"][0]]
                self.assertTrue(appr.get("approval"), "%s: the gate leads to an approval" % w["name"])
                for after in appr.get("success") or []:
                    t = nodes[after].get("template")
                    if t and t != GATE:
                        self.assertTrue(var in role_text(TPL[t]["playbook"]), "%s: %s does not read %s" % (w["name"], t, var))
                self.assertTrue("`%s`" % var in docs or "workflow_gate_var: %s" % var in docs, "%s: the docs do not name the gate's %s" % (w["name"], var))
        self.assertGreaterEqual(n_gates, 9)

    def test_a_node_gives_only_what_its_template_takes(self):
        for w in (w for w in WFS if w.get("nodes")):
            for n in (n for n in w["nodes"] if n.get("template")):
                t = TPL[n["template"]]
                if n.get("job_type"):
                    self.assertIn(n["job_type"], ("check", "run"), w["name"])
                    self.assertTrue(t.get("ask_job_type"), "%s: %s does not prompt for Job type" % (w["name"], t["name"]))
                if n.get("diff_mode"):
                    self.assertTrue(t.get("ask_diff_mode"), "%s: %s does not prompt for Show changes" % (w["name"], t["name"]))
                keys = set((n.get("extra_data") or {}).keys())
                if keys and not t.get("ask_variables"):
                    self.assertIn(t["name"], SURVEY_TEMPLATES, "%s: %s takes no Variables" % (w["name"], t["name"]))
                    self.assertLessEqual(keys, SURVEY_TEMPLATES[t["name"]], w["name"])
                if w.get("prompts", {}).get("limit") is not True:
                    continue
            self.assertLessEqual(set(w.get("prompts") or {}), {"limit", "variables"}, w["name"])

    def test_controller_steps_never_ask_for_a_limit(self):
        """A workflow's Limit reaches every step that asks for one: the controller-side steps must not (SETUP_AAP.md)."""
        for name in ("ServiceNow tickets", "POA&M status", GATE, "STIG Manager - update"):
            self.assertFalse(TPL[name].get("ask_limit"), name)

    def test_surveys(self):
        for w in (w for w in WFS if w.get("survey")):
            vs = [q["variable"] for q in w["survey"]]
            self.assertEqual(len(vs), len(set(vs)), w["name"])
            for q in w["survey"]:
                self.assertTrue(q.get("question_name") and re.match(r"^[a-z_]+$", q["variable"]), w["name"])
                self.assertIn(q["type"], ("text", "textarea", "integer", "multiplechoice", "multiselect"), w["name"])
                self.assertIn("required", q, w["name"])
                if q["type"] in ("multiplechoice", "multiselect"):
                    self.assertTrue(q.get("choices"), w["name"])
                    if q.get("default"):
                        self.assertIn(q["default"], q["choices"], w["name"])

    def test_portal_label_only_on_the_stig_jobs(self):
        self.assertEqual(sorted(w["name"] for w in WFS if w.get("portal")), ["STIG - drift scan and upload", "STIG - full scan and upload"])

    def test_generated(self):
        """aap/job_templates.yml is what scripts/gen_job_catalog.py writes from aap/job_descriptions.yml."""
        sys.path.insert(0, os.path.join(REPO, "scripts"))
        import gen_job_catalog as G
        with open(os.path.join(REPO, "aap", "job_templates.yml")) as f:
            self.assertEqual(G.render(G.build()), f.read())


class Builder(unittest.TestCase):
    """The builder's parts: a node's Job type and Show changes, the differences said node by node, the survey written once."""

    def test_node_texts_and_diff_lines(self):
        want = [{"id": "a", "template": "Health check", "extra_data": {"health_checks": ["daily"]}, "success": [], "failure": [],
                 "always": ["b"], "approval": None, "converge": False},
                {"id": "b", "template": "ServiceNow tickets", "extra_data": {}, "success": [], "failure": [], "always": [],
                 "approval": None, "converge": False}]
        have = [dict(want[0], id="x1", always=[], success=["x2"]), dict(want[1], id="x2")]
        lines = SW.diff_lines({"(its parts)": [have, want], "ask_limit_on_launch": [True, False]})
        self.assertEqual(lines, ["node missing in AAP: 'Health check' (its Variables: health_checks: [daily]); always -> 'ServiceNow tickets'",
                                 "node in AAP, not the catalog's: 'Health check' (its Variables: health_checks: [daily]); on success -> 'ServiceNow tickets'",
                                 "prompt for Limit: has true, should be false"])
        j = [{"id": "p", "template": "Patch hosts", "job_type": "check", "diff_mode": True, "success": ["q"]},
             {"id": "q", "approval": {"name": "ok?", "timeout": 60}}]
        self.assertEqual(SW.node_texts(j), ["'Patch hosts' as Check, Show changes; on success -> approval 'ok?'",
                                            "approval 'ok?' (timeout 60 s)"])

    def sync(self, check, calls, overwrite=False, out=None):
        api = Mock()
        api.call.side_effect = calls
        s = SW.Sync(api, {"overwrite": overwrite, "known": out}, check, W.MARK, "aap_templates_overwrite")
        s.org_id = 1
        s.results.append({"kind": "workflow", "name": "W", "action": "create"})
        return s, api

    Q = {"question_name": "Q", "variable": "v", "type": "text", "required": True}
    ROW = (200, {"results": [{"id": 7, "name": "W", "organization": 1, "survey_enabled": False}]})

    def test_survey_written_when_there_is_none(self):
        w = {"name": "W", "survey": [self.Q]}
        s, api = self.sync(False, [(200, {}), self.ROW, (200, {}), (200, {})])
        s.survey(7, w)
        self.assertEqual([c.args[:2] for c in api.call.call_args_list],
                         [("GET", "workflow_job_templates/7/survey_spec/"), ("GET", "workflow_job_templates/?name=W&page_size=200"),
                          ("POST", "workflow_job_templates/7/survey_spec/"), ("PATCH", "workflow_job_templates/7/")])
        self.assertEqual(s.results[-1]["survey"], "survey added: v")
        self.assertTrue(s.wrote_survey)                       # review P2: the module says changed

    def test_review_survey_spec_complete(self):
        """Review P1: every question as AAP takes it - question_description, default, choices ('' or a list) - and the
        spec's name and description."""
        w = {"name": "W", "survey": [self.Q, {"question_name": "M", "variable": "m", "type": "multiselect", "choices": ["a", "b"],
                                              "default": ["a", "b"], "required": False},
                                     {"question_name": "N", "variable": "n", "type": "integer", "min": 1, "default": 5, "required": True}]}
        s, api = self.sync(False, [(200, {}), self.ROW, (200, {}), (200, {})])
        s.survey(7, w)
        body = api.call.call_args_list[2].args[2]
        self.assertEqual((body["name"], body["description"]), ("", ""))
        self.assertEqual(body["spec"], [
            {"question_name": "Q", "question_description": "", "variable": "v", "type": "text", "required": True, "default": "", "choices": ""},
            {"question_name": "M", "question_description": "", "variable": "m", "type": "multiselect", "required": False, "default": "a\nb",
             "choices": ["a", "b"]},
            {"question_name": "N", "question_description": "", "variable": "n", "type": "integer", "required": True, "default": 5, "choices": "",
             "min": 1}])

    def test_review_survey_refused_is_said_never_raised(self):
        """Review P1: a survey AAP refuses - said for the workflow, a problem, the run goes on; the next Run writes it."""
        w = {"name": "W", "survey": [self.Q]}
        s, api = self.sync(False, [(200, {}), self.ROW, (400, {"error": "bad"})])
        s.survey(7, w)                                         # never raises
        self.assertEqual(s.results[-1]["action"], "failed")
        self.assertIn("its survey NOT written", s.results[-1]["why"])
        self.assertEqual(s.out.get("1/survey/W"), "000000000000")
        s2, api2 = self.sync(False, [(200, {}), self.ROW, (200, {}), (200, {})], out=dict(s.out))
        s2.survey(7, w, False)                                 # unchanged, no overwrite: written all the same
        self.assertEqual(s2.results[-1]["survey"], "survey added: v")
        self.assertNotIn("1/survey/W", s2.out)

    def test_survey_taken_off_is_not_put_back_without_overwrite(self):
        w = {"name": "W", "survey": [self.Q]}
        s, api = self.sync(False, [(200, {}), self.ROW])
        s.survey(7, w, False)
        self.assertEqual(len(api.call.call_args_list), 2)
        self.assertIn("it has no survey (taken off by hand?): left as it is - aap_templates_overwrite: true writes it", s.results[-1]["survey"])

    def test_survey_there_is_never_changed(self):
        w = {"name": "W", "survey": [self.Q]}
        on = (200, {"results": [{"id": 7, "name": "W", "organization": 1, "survey_enabled": True}]})
        s, api = self.sync(False, [(200, {"spec": [{"variable": "other", "type": "text"}]}), on])
        s.survey(7, w)
        self.assertEqual(len(api.call.call_args_list), 2)
        self.assertIn("its survey is yours, left as it is: it has no question v", s.results[-1]["survey"])

    def test_review_survey_off_said_or_turned_on(self):
        """Review P2: a survey there but switched off - said (no overwrite), turned on with overwrite, changed."""
        w = {"name": "W", "survey": [self.Q]}
        s, api = self.sync(False, [(200, {"spec": [dict(self.Q)]}), self.ROW])
        s.survey(7, w, False)
        self.assertIn("its survey is off", s.results[-1]["survey"])
        self.assertFalse(s.wrote_survey)
        s, api = self.sync(False, [(200, {"spec": [dict(self.Q)]}), self.ROW, (200, {})], overwrite=True)
        s.survey(7, w, True)
        self.assertEqual(api.call.call_args_list[-1].args, ("PATCH", "workflow_job_templates/7/", {"survey_enabled": True}))
        self.assertTrue(s.wrote_survey)

    def test_survey_check_and_new(self):
        w = {"name": "W", "survey": [self.Q]}
        s, api = self.sync(True, [])
        s.survey(-1, w)
        self.assertEqual(s.results[-1]["survey"], "would add its survey: v")
        self.assertFalse(api.call.called)

    def test_review_half_made_workflow_is_ours(self):
        """Review P1: a workflow made in this run whose nodes failed part way - its fingerprint is what AAP has, so the next
        Run makes its nodes again (never left half made, never 'not made by this job')."""
        api = Mock()
        api.call.side_effect = [(200, {"results": []}), (201, {"id": 7})]
        s = SW.Sync(api, {"overwrite": False}, False, W.MARK, "aap_templates_overwrite")
        s.org_id = 1
        want = {"name": "W", "description": "d"}

        def boom(oid, created):
            if oid == "wanted":
                return [{"id": "a"}]
            raise RuntimeError("a link refused")
        with self.assertRaises(RuntimeError):
            s.reconcile("workflow", "workflow_job_templates", "W", want, lambda oid: [{"id": "half"}], boom)
        self.assertEqual(s.out["1/workflow/W"], SW.fingerprint(dict(want, extra=[{"id": "half"}])))

    def wf(self, check=True, fixing=(), planned=()):
        api = Mock()
        p = {"overwrite": False, "fixing": list(fixing), "planned": list(planned), "workflows": [], "templates": CAT["job_templates"],
             "only": [], "labels": [], "uses": {}, "refused": [], "renamed": [], "known": None}
        w = W.Workflows(api, p, check)
        w.org_id, w.project_id = 1, 2
        w.catalog = TPL
        return w, api

    def test_setup_adds_the_handoff_question_to_the_existing_survey(self):
        w, api = self.wf(check=False)
        workflow = next(entry for entry in WFS if entry["name"] == "VM - deploy from ServiceNow ticket (workflow)")
        existing = {"variable": "vm_deploy_ticket", "question_name": "Ticket", "required": True, "type": "text"}
        survey = {"name": "Deploy", "description": "", "spec": [existing]}
        api.call.side_effect = [(200, {"results": [{"id": 7, "organization": 1, "name": workflow["name"], "survey_enabled": True}]}),
                                (200, survey), (200, {})]
        w.one(workflow, {}, {}, {})
        api.call.assert_called()
        method, path, body = api.call.call_args.args
        self.assertEqual((method, path), ("POST", "workflow_job_templates/7/survey_spec/"))
        question = next(q for q in workflow["survey"] if q["variable"] == "vm_deploy_handoff_from")
        self.assertEqual(body, dict(survey, spec=[existing, SW.survey_question(question)]))

    def test_node_why(self):
        w, api = self.wf()
        hc = {"id": 5, "name": "Health check", "ask_variables_on_launch": False, "survey_enabled": True}
        api.call.return_value = (200, {"spec": [{"variable": "health_checks"}]})
        self.assertEqual(w.node_why(hc, TPL["Health check"], {"vars": {"health_checks"}}, False), "")
        why = w.node_why(hc, TPL["Health check"], {"vars": {"health_checks", "use_act"}}, False)
        self.assertIn("'Health check' has no survey question use_act and does not ask for Variables on launch", why)
        self.assertIn("would be made with no survey question health_checks",
                      w.node_why(None, TPL["Health check"], {"vars": {"health_checks"}}, True))
        self.assertEqual(w.node_why(dict(hc, ask_variables_on_launch=True, survey_enabled=False), TPL["Health check"],
                                    {"vars": {"use_act"}}, False), "")
        wp = {"id": 6, "name": "Windows patch", "ask_job_type_on_launch": False}
        self.assertIn("does not ask for Job type on launch", w.node_why(wp, TPL["Windows patch"], {"vars": set(), "job_type": True}, False))
        self.assertEqual(w.node_why(None, TPL["Windows patch"], {"vars": set(), "job_type": True}, True), "")   # made with the catalog's prompt
        w2, _ = self.wf(fixing=["Windows patch"])
        self.assertEqual(w2.node_why(w2.fixed(wp, TPL["Windows patch"]), TPL["Windows patch"], {"vars": set(), "job_type": True}, False), "")
        sm = {"id": 8, "name": "STIG Manager - deploy", "ask_job_type_on_launch": True, "ask_diff_mode_on_launch": False}
        self.assertIn("Show changes", w.node_why(sm, TPL["STIG Manager - deploy"], {"vars": set(), "job_type": True, "diff_mode": True}, False))

    def test_review_fixing_only_what_it_fixes(self):
        """Review P2: Check counts a template as brought right only in the fields the template job fixes."""
        w, api = self.wf()
        w.p["fixing"] = {"Windows patch": ["inventory"]}
        wp = {"id": 6, "name": "Windows patch", "playbook": "playbooks/win_patch.yml", "project": 2, "ask_limit_on_launch": True,
              "ask_job_type_on_launch": False}
        self.assertFalse(w.fixed(wp, TPL["Windows patch"])["ask_job_type_on_launch"])
        w.p["fixing"] = {"Windows patch": ["prompt for Job Type"]}
        self.assertTrue(w.fixed(wp, TPL["Windows patch"])["ask_job_type_on_launch"])
        api.call.return_value = (200, {"results": [dict(wp, organization=1)]})
        w.p["fixing"] = {"Windows patch": ["inventory"]}
        w.cache = {}
        self.assertIn("does not ask for Job type", w.step_why("Windows patch", "Windows patch", {"vars": set(), "job_type": True}))

    def test_review_fixed_variables_checked(self):
        """Review P1: a step without the fixed Variables its node relies on is never wired."""
        w, api = self.wf(check=False)
        hd = {"id": 9, "name": "Horizon - desktops check", "organization": 1, "playbook": "playbooks/horizon_desktops_check.yml",
              "project": 2, "extra_vars": "horizon_desktops_mode: report\n"}
        api.call.return_value = (200, {"results": [hd]})
        self.assertIn("lacks its fixed Variables horizon_desktops_mode: approval", w.step_why("Horizon - desktops check", "Horizon - desktops check"))
        w.cache = {}
        api.call.return_value = (200, {"results": [dict(hd, extra_vars="---\nhorizon_desktops_mode: approval\nmine: 1\n")]})
        self.assertEqual(w.step_why("Horizon - desktops check", "Horizon - desktops check"), "")

    def test_review_renamed_workflow_read_in_check(self):
        """Review P2: Check reads a workflow the renames of this run give the catalog's name under that name."""
        w, api = self.wf()
        w.p["renamed_workflows"] = {"Our cert watch": "Certificate watch"}
        api.call.side_effect = lambda m, path, b=None: (200, {"results": [{"id": 3, "name": "Our cert watch", "organization": 1}]}
                                                         if "Our%20cert%20watch" in path else {"results": []})
        self.assertEqual([r["name"] for r in w.by_name("workflow_job_templates", "Certificate watch")], ["Certificate watch"])
        self.assertEqual(w.by_name("workflow_job_templates", "Our cert watch"), [])

    def test_review_node_texts_tell_apart(self):
        """Review P2: an approval's description is said; one template twice is told apart by how it runs."""
        a = [{"id": "x", "approval": {"name": "ok?", "timeout": 60, "description": "read it"}}]
        b = [{"id": "x", "approval": {"name": "ok?", "timeout": 60, "description": "other"}}]
        self.assertTrue(SW.diff_lines({"(its parts)": [a, b]}))
        nodes = [{"id": "pre", "template": "Health check", "success": ["run"]}, {"id": "dry", "template": "Patch hosts", "job_type": "check"},
                 {"id": "run", "template": "Patch hosts", "job_type": "run"}]
        self.assertIn("'Health check'; on success -> 'Patch hosts' as Run", SW.node_texts(nodes))


class Review(unittest.TestCase):
    """Neon's review of 1.107.0: the catalog's side."""

    def test_describe_built_decides(self):
        """P2: given the map of what the job owns, a STIG job (portal) made by hand is described, not left."""
        import site_aap_describe as D
        d = D.Describe(Mock(), {"workflows": WFS, "managed": {}, "built": []}, True)
        self.assertNotIn("STIG - full scan and upload", d.built)
        d = D.Describe(Mock(), {"workflows": WFS, "managed": {}, "built": ["STIG - full scan and upload"]}, True)
        self.assertIn("STIG - full scan and upload", d.built)
        d = D.Describe(Mock(), {"workflows": WFS, "managed": {}, "built": None}, True)
        self.assertIn("Daily health", d.built)

    def test_no_prompt_for_any_variable(self):
        """P1: a workflow's steps get their answers from a survey question, never Prompt on launch for Variables."""
        self.assertFalse(TPL["VM - deploy from ServiceNow ticket"].get("ask_variables"))
        deploy = next(w for w in WFS if w["name"] == "VM - deploy from change (no touch)")
        self.assertEqual([q["variable"] for q in deploy["survey"]], ["vm_deploy_ticket"])
        self.assertEqual(deploy["nodes"], [{"id": "deploy", "template": "VM - deploy from ServiceNow ticket",
                                          "extra_data": {"vm_deploy_phase": "no_touch"}}])
        for w in WFS:
            self.assertFalse((w.get("prompts") or {}).get("variables"), w["name"])
        net = next(w for w in WFS if w["name"] == "Network config load (workflow)")
        self.assertEqual([q["variable"] for q in net["survey"]],
                         ["net_config_change", "net_config_load_mode", "net_config_only", "net_config_lines", "net_config_restore_ref"])

    def test_template_surveys_cover_every_node(self):
        """The job adds the survey questions every node's answers need (the 13 workflows of Health check ...)."""
        for w in (w for w in WFS if w.get("nodes")):
            for n in (n for n in w["nodes"] if n.get("template") and n.get("extra_data") and n["template"] != GATE):
                have = [q["variable"] for q in TPL[n["template"]].get("survey") or []]
                self.assertLessEqual(set(n["extra_data"]), set(have), "%s: %s" % (w["name"], n["template"]))

    def test_fapolicyd_approved_reads_the_check_window(self):
        """P1: the approved step reads the window the check read (fapolicyd_since: yesterday)."""
        self.assertEqual(TPL["fapolicyd - approved"].get("extra_vars"), {"fapolicyd_since": "yesterday"})
        self.assertEqual(TPL["fapolicyd - check"]["extra_vars"]["fapolicyd_since"], "yesterday")

    def test_with_sudo_is_needed(self):
        """P2: "Machine (with sudo)" - the Machine credential is needed, never optional."""
        for name in ("fapolicyd", "fapolicyd - check", "fapolicyd - approved", "Linux - auditd tuning", "Linux - auditd tuning - check"):
            self.assertIn("machine", TPL[name]["credentials"], name)


class Review2(unittest.TestCase):
    """Codex's last pass on 7bd7f354."""

    def test_dry_runs_before_an_approval_are_check_templates(self):
        """A step before an approval that runs the same playbook as a step after it is a dry run: a Check template itself,
        never a node's Job type (AAP ignores that once the template stops prompting, and the load would go out first)."""
        n_dry = 0
        for w in (w for w in WFS if w.get("nodes")):
            nodes = dict((n["id"], n) for n in w["nodes"])
            kids = dict((n["id"], [c for k in ("success", "failure", "always") for c in n.get(k) or []]) for n in w["nodes"])

            def below(i, seen=None):
                seen = set() if seen is None else seen
                for c in kids[i]:
                    if c not in seen:
                        seen.add(c)
                        below(c, seen)
                return seen
            for n in w["nodes"]:
                self.assertNotIn("job_type", n, "%s: node %s sets a Job type - use a Check template" % (w["name"], n["id"]))
                if not n.get("template"):
                    continue
                after_appr = [c for c in below(n["id"]) if nodes[c].get("approval")]
                for a in after_appr:
                    # what the approval lets run: the steps straight after it
                    later = [nodes[c].get("template") for c in nodes[a].get("success") or [] if nodes[c].get("template")]
                    e = TPL[n["template"]]
                    if e.get("job_type") == "check" and any(TPL[t]["playbook"] == e["playbook"] for t in later):
                        n_dry += 1                 # a dry-run template: Check itself
                        continue
                    # a check by its fixed Variables (auditd_tuning_workflow, fapolicyd_workflow) is
                    # read-only whatever the Job type; the pick templates plan inside a workflow by design (docs/VMWARE.md)
                    if e.get("extra_vars") or n["template"] in ("VM - delete picked snapshots", "VM - restart picked VMs"):
                        continue
                    if any(TPL[t]["playbook"] == e["playbook"] for t in later):
                        n_dry += 1
                        self.assertEqual(TPL[n["template"]].get("job_type"), "check", "%s: %s before the approval" % (w["name"], n["template"]))
        self.assertGreaterEqual(n_dry, 5)
        self.assertTrue(TPL["STIG Manager - deploy (dry run)"].get("diff_mode"))

    def test_approval_workflow_has_optional_handoff_question(self):
        w = next(w for w in WFS if w["name"] == "VM - deploy from ServiceNow ticket (workflow)")
        self.assertIn("survey", w)
        q = next(q for q in w["survey"] if q["variable"] == "vm_deploy_handoff_from")
        self.assertEqual((q["type"], q["required"]), ("text", False))

    def test_vm_deploy_workflow_is_deferred(self):
        w = next(w for w in WFS if w["name"] == "VM - deploy from ServiceNow ticket (workflow)")
        self.assertNotIn("nodes", w)
        self.assertEqual(w["deferred"], "left by hand for now: the build's binding to what was approved is being reviewed")
        # the deferred build binding left nothing behind in the deploy tasks (no git history needed: CI and zips have none)
        with open(os.path.join(REPO, "roles/vmware_vm/tasks/deploy.yml")) as f:
            deploy = f.read()
        for word in ("vm_deploy_proposal", "awx_workflow_job_id"):
            self.assertNotIn(word, deploy)

    def test_no_workflow_survey_overrides_what_its_steps_fix(self):
        for w in (w for w in WFS if w.get("nodes")):
            fixed = set((w.get("extra_vars") or {}).keys())
            for n in w["nodes"]:
                fixed.update((n.get("extra_data") or {}).keys())
                fixed.update((TPL.get(n.get("template")) or {}).get("extra_vars", {}).keys() if n.get("template") else ())
            self.assertFalse(set(q["variable"] for q in w.get("survey") or []) & fixed, w["name"])

    def test_the_look_after_the_deploy_never_heals(self):
        w = next(w for w in WFS if w["name"] == "STIG Manager deploy with checks")
        watch = next(n for n in w["nodes"] if n["id"] == "watch")
        self.assertEqual(TPL[watch["template"]]["extra_vars"], {"watch_mode": "approval"})

    def test_a_limit_that_leaves_out_localhost(self):
        for lim, miss in (("", False), ("localhost", False), ("web01,localhost", False), ("all", True), ("web01", True),
                          ("web01:localhost", True), ("localhost,*", True), ("web01, localhost", False),
                          ("linux:!localhost", True), ("stigman", True), ("all:&web01", True), ("localhost:&web01", True)):
            self.assertEqual(SW.limit_misses_localhost(lim), miss, lim)
        api = Mock()
        p = {"overwrite": False, "fixing": {}, "planned": [], "workflows": [], "templates": CAT["job_templates"], "only": [], "labels": [],
             "uses": {}, "refused": [], "renamed": [], "known": None}
        w = W.Workflows(api, p, False)
        w.org_id, w.project_id, w.catalog = 1, 2, TPL
        gate = {"id": 3, "name": GATE, "organization": 1, "playbook": "playbooks/workflow_gate.yml", "project": 2, "limit": "web01",
                "ask_variables_on_launch": True}
        api.call.return_value = (200, {"results": [gate]})
        self.assertIn("leaves out localhost", w.step_why(GATE, GATE))

    def test_a_node_answer_its_question_does_not_offer(self):
        api = Mock()
        p = {"overwrite": False, "fixing": {}, "planned": [], "workflows": [], "templates": CAT["job_templates"], "only": [], "labels": [],
             "uses": {}, "refused": [], "renamed": [], "known": None}
        w = W.Workflows(api, p, True)
        w.org_id, w.project_id, w.catalog = 1, 2, TPL
        api.call.return_value = (200, {"spec": [{"variable": "health_checks", "type": "multiselect", "choices": "daily\nweekly\ndisk"}]})
        hc = {"id": 5, "name": "Health check", "survey_enabled": True}
        need = {"vars": {"health_checks"}, "answers": {"health_checks": [["disk", "mounts"]]}}
        self.assertIn("does not offer mounts", w.node_why(hc, TPL["Health check"], need, False))
        self.assertEqual(w.node_why(hc, TPL["Health check"], {"vars": {"health_checks"}, "answers": {"health_checks": [["disk"]]}}, False), "")


class Review3(unittest.TestCase):
    """Codex's narrow check of c1973d1f."""

    def wf(self, check=True):
        api = Mock()
        p = {"overwrite": False, "fixing": {}, "planned": [], "workflows": [], "templates": CAT["job_templates"], "only": [], "labels": [],
             "uses": {}, "refused": [], "renamed": [], "known": None}
        w = W.Workflows(api, p, check)
        w.org_id, w.project_id, w.catalog = 1, 2, TPL
        return w, api

    def test_answer_types(self):
        mc = {"type": "multiplechoice", "choices": ["disk", "mounts", "services"]}
        ms = {"type": "multiselect", "choices": "daily\nweekly\ndisk"}
        self.assertEqual(W.answer_why(mc, ["disk", "mounts"]), "takes one choice")
        self.assertEqual(W.answer_why(mc, "disk"), "")
        self.assertEqual(W.answer_why({"type": "multiselect", "choices": ["a"]}, "a"), "takes a list")
        self.assertEqual(W.answer_why(ms, ["disk", "mounts"]), "does not offer mounts")
        self.assertEqual(W.answer_why({"type": "integer", "min": 1}, 0), "takes 1 to None")
        self.assertEqual(W.answer_why({"type": "integer"}, "5"), "takes a number")
        self.assertEqual(W.answer_why({"type": "text", "max": 3}, "abcd"), "takes None to 3 characters")
        self.assertEqual(W.answer_why({"type": "text"}, ["a"]), "takes text")

    def test_choices_checked_with_variables_prompted_too(self):
        w, api = self.wf()
        api.call.return_value = (200, {"spec": [{"variable": "health_checks", "type": "multiselect", "choices": ["daily", "weekly", "disk"]}]})
        hc = {"id": 5, "name": "Health check", "survey_enabled": True, "ask_variables_on_launch": True}
        why = w.node_why(hc, TPL["Health check"], {"vars": {"health_checks"}, "answers": {"health_checks": [["disk", "mounts"]]}}, False)
        self.assertIn("does not offer mounts", why)

    def test_a_step_after_the_approval_must_be_run(self):
        w, api = self.wf(check=False)
        ph = {"id": 4, "name": "Patch hosts", "organization": 1, "playbook": "playbooks/patch_hosts.yml", "project": 2, "job_type": "check",
              "ask_limit_on_launch": True}
        api.call.return_value = (200, {"results": [ph]})
        self.assertIn("the step must be Job type Run", w.step_why("Patch hosts", "Patch hosts"))
        w.cache = {}
        api.call.return_value = (200, {"results": [dict(ph, job_type="run")]})
        self.assertEqual(w.step_why("Patch hosts", "Patch hosts"), "")

    def test_dry_run_templates_refuse_a_run(self):
        """Each dry-run template carries site_dry_run_only, is made from the template it previews, and its playbook
        imports the guard (playbooks/workflow_dry_run.yml) before anything else acts."""
        for name in [n for n in TPL if n.endswith("(dry run)")]:
            e = TPL[name]
            self.assertEqual((e.get("extra_vars"), e.get("job_type"), e.get("copy_of")),
                             ({"site_dry_run_only": "true"}, "check", name[:-len(" (dry run)")]), name)
            with open(os.path.join(REPO, e["playbook"])) as f:
                src = f.read()
            plays = re.findall(r"^- name: .*$", src, re.M)
            self.assertIn("workflow_dry_run.yml", src, name)
            self.assertEqual(plays[1], "- name: Workflow dry run - a dry-run template runs as Check only", name)

    def test_after_an_approval_no_ai_fixes(self):
        """The health checks of the workflows with an approval report only (use_act: no) - ACT's self-heal never runs on
        a failure path the approval did not cover."""
        for wname in ("Patch with checks", "Patch with a dry run first", "STIG Manager deploy with checks"):
            w = next(x for x in WFS if x["name"] == wname)
            for n in (n for n in w["nodes"] if n.get("template") == "Health check"):
                self.assertEqual((n.get("extra_data") or {}).get("use_act"), "no", "%s: %s" % (wname, n["id"]))


if __name__ == "__main__":
    unittest.main()
