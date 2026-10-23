#!/usr/bin/env python3
"""Tests for the "Also email the report to" question (playbooks/aap_email_question.yml,
roles/aap_email_question/library/site_aap_question.py): the survey changed in memory; the module against a fake
AAP in this process (job templates found by playbook over several pages, workflows - one inside another -, a
survey that is turned off, one the user may not change); the playbook itself against that fake; and that
aap_email_question_playbooks names every playbook that can email a report.
Needs ansible-core: python3 tests/test_email_question.py"""
import copy
import glob
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(REPO, "roles", "aap_email_question", "library"))
_spec = importlib.util.spec_from_file_location("ansible.module_utils.site_aap", os.path.join(REPO, "plugins", "module_utils", "site_aap.py"))
_aap = importlib.util.module_from_spec(_spec)
sys.modules["ansible.module_utils.site_aap"] = _aap
_spec.loader.exec_module(_aap)
import site_aap_question as m  # noqa: E402

Q = m.make_question({"variable": "report_email_also", "question_name": "Also email the report to", "question_description": "Optional."})


def q(var, kind="text", default=""):
    return {"variable": var, "type": kind, "question_name": var, "question_description": "", "required": False, "default": default}


class Survey(unittest.TestCase):
    def test_the_question(self):
        self.assertEqual(Q, {"question_name": "Also email the report to", "question_description": "Optional.", "required": False, "type": "textarea",
                             "variable": "report_email_also", "min": 0, "max": 4096, "default": "", "choices": ""})

    def test_no_survey_one_is_made(self):
        new, what, warn = m.add_question({}, Q)
        self.assertEqual((new, what, warn), ({"name": "", "description": "", "spec": [Q]}, "created", []))

    def test_added_at_the_end_the_rest_kept(self):
        spec = {"name": "Mine", "description": "d", "spec": [q("vm_names"), q("pw", "password", "$encrypted$")]}
        new, what, warn = m.add_question(spec, Q)
        self.assertEqual(what, "added")
        self.assertEqual(new["spec"][:2], spec["spec"])                    # a password's "$encrypted$" goes back as it came: AAP keeps it
        self.assertEqual(new["spec"][2], Q)
        self.assertEqual((new["name"], new["description"]), ("Mine", "d"))
        self.assertEqual(len(spec["spec"]), 2)                             # the survey read is not changed

    def test_already_there_left_as_it_is(self):
        mine = dict(Q, question_name="Email it to (extra)")
        self.assertEqual(m.add_question({"spec": [mine]}, Q)[:2], (None, "there"))

    def test_turned_off_stays_off_and_says_so(self):
        new, what, warn = m.add_question({"spec": [q("vm_names")]}, Q, enabled=False)
        self.assertEqual(what, "added")
        self.assertIn("was left off", warn[0])
        self.assertIn("not asked until you turn the survey on", m.add_question({"spec": [Q]}, Q, enabled=False)[2][0])

    def test_report_email_to_question_pointed_out(self):
        warn = m.add_question({"spec": [q("report_email_to")]}, Q, warn_variables=["report_email_to"])[2]
        self.assertIn("its survey asks report_email_to too: what is typed there replaces the template's usual recipients", warn[0])

    def test_remove(self):
        self.assertEqual(m.remove_question({"spec": [q("a"), Q]}, "report_email_also")[1], "removed")
        self.assertEqual(m.remove_question({"spec": [q("a"), Q]}, "report_email_also")[0]["spec"], [q("a")])
        self.assertEqual(m.remove_question({"spec": [Q]}, "report_email_also"), ({}, "emptied"))
        self.assertEqual(m.remove_question({"spec": [q("a")]}, "report_email_also"), (None, "absent"))
        self.assertEqual(m.remove_question({}, "report_email_also"), (None, "absent"))


class Pick(unittest.TestCase):
    def test_jobs_by_playbook_file_name_and_project(self):
        jobs = [{"id": 1, "playbook": "playbooks/cert_report.yml", "summary_fields": {"project": {"name": "site"}}},
                {"id": 2, "playbook": "cert_report.yml", "summary_fields": {"project": {"name": "other"}}},
                {"id": 3, "playbook": "playbooks\\health_check.yml"}, {"id": 4, "playbook": "playbooks/patch_hosts.yml"}]
        self.assertEqual([j["id"] for j in m.pick_jobs(jobs, ["cert_report.yml", "playbooks/health_check.yml"])], [1, 2, 3])
        self.assertEqual([j["id"] for j in m.pick_jobs(jobs, ["cert_report.yml"], "site")], [1])
        self.assertEqual(m.pick_jobs(jobs, []), [])

    def test_workflows_any_depth(self):
        nodes = {10: [(1, "job"), (None, "workflow_approval")], 11: [(10, "workflow_job")], 12: [(11, "workflow_job")],
                 13: [(4, "job")], 14: [(13, "workflow_job")]}
        self.assertEqual(m.pick_workflows(nodes, [1]), {10, 11, 12})
        self.assertEqual(m.pick_workflows(nodes, [], [13]), {13, 14})
        self.assertEqual(m.pick_workflows(nodes, [99]), set())


def _jt(i, name, playbook, project="Site automation", enabled=False, edit=True):
    return {"id": i, "name": name, "playbook": playbook, "survey_enabled": enabled,
            "summary_fields": {"project": {"name": project}, "user_capabilities": {"edit": edit}}}


def _wf(i, name, enabled=False, edit=True):
    return {"id": i, "name": name, "survey_enabled": enabled, "summary_fields": {"user_capabilities": {"edit": edit}}}


def _node(ujt, kind):
    return {"unified_job_template": ujt, "summary_fields": {"unified_job_template": {"id": ujt, "unified_job_type": kind}} if ujt else {}}


class FakeAap(object):
    """Job templates 1-7 (two pages), workflows 20-23, their steps and surveys; every write is logged."""

    def __init__(self):
        self.jobs = [_jt(1, "Certificate report", "playbooks/cert_report.yml"),
                     _jt(2, "Health check", "playbooks/health_check.yml", enabled=True),
                     _jt(3, "Patch", "playbooks/patch_hosts.yml"),
                     _jt(4, "Snapshot report", "playbooks/vm_snapshot_report.yml"),
                     _jt(5, "Old cert report", "playbooks/cert_report.yml", project="Old project"),
                     _jt(6, "Read only report", "playbooks/poam_status.yml", edit=False),
                     _jt(7, "Datastore report", "playbooks/vm_datastore_report.yml", enabled=False)]
        self.wfs = [_wf(20, "Certificate watch", enabled=True), _wf(21, "Nightly (outer)"), _wf(22, "Patching"), _wf(23, "Unrelated")]
        self.nodes = {20: [_node(1, "job"), _node(None, "workflow_approval"), _node(3, "job")], 21: [_node(20, "workflow_job")],
                      22: [_node(3, "job")], 23: []}
        self.specs = {("job", 1): {}, ("job", 2): {"name": "", "description": "", "spec": [q("health_checks", "multiselect", "all")]},
                      ("job", 3): {}, ("job", 4): {"name": "", "description": "", "spec": [q("report_email_to")]}, ("job", 5): {},
                      ("job", 6): {}, ("job", 7): {"name": "", "description": "", "spec": [q("vm_snapshot_max_age_days", "integer", 30)]},
                      ("workflow", 20): {"name": "", "description": "", "spec": [dict(Q)]}, ("workflow", 21): {}, ("workflow", 22): {},
                      ("workflow", 23): {}}
        self.writes = []
        self.page_size = 3
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj=None):
                data = json.dumps(obj if obj is not None else {}).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _do(self, method):
                n = int(self.headers.get("Content-Length", "0") or 0)
                body = json.loads(self.rfile.read(n) or b"null") if n else None
                url = urllib.parse.urlparse(self.path)
                qs = dict(urllib.parse.parse_qsl(url.query))
                base = "/api/controller/v2/"
                if not url.path.startswith(base):
                    return self._send(404, {"detail": "Not found."})
                if self.headers.get("Authorization") != "Bearer goodtoken":
                    return self._send(401, {"detail": "Authentication credentials were not provided."})
                rest = url.path[len(base):].strip("/").split("/")
                code, obj = fake.answer(method, rest, qs, body, base + "/".join(rest) + "/")
                return self._send(code, obj)

            def do_GET(self):
                self._do("GET")

            def do_POST(self):
                self._do("POST")

            def do_PATCH(self):
                self._do("PATCH")

            def do_DELETE(self):
                self._do("DELETE")

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d" % self.srv.server_port

    def page(self, items, qs, path):
        p = int(qs.get("page", "1"))
        size = self.page_size
        chunk = items[(p - 1) * size:p * size]
        nxt = ("%s?page=%d&page_size=%s" % (path, p + 1, qs.get("page_size", "25"))) if p * size < len(items) else None
        return 200, {"count": len(items), "next": nxt, "results": copy.deepcopy(chunk)}

    def answer(self, method, rest, qs, body, path):
        kinds = {"job_templates": ("job", self.jobs), "workflow_job_templates": ("workflow", self.wfs)}
        if rest == ["me"]:
            return 200, {"results": [{"username": "admin"}]}
        if not rest or rest[0] not in kinds:
            return 404, {"detail": "Not found."}
        kind, recs = kinds[rest[0]]
        if len(rest) == 1 and method == "GET":
            return self.page(recs, qs, path)
        rec = next((x for x in recs if str(x["id"]) == rest[1]), None)
        if rec is None:
            return 404, {"detail": "Not found."}
        if rest[2:] == ["workflow_nodes"] and kind == "workflow":
            return self.page(self.nodes[rec["id"]], qs, path)
        if method != "GET" and not rec["summary_fields"]["user_capabilities"]["edit"]:
            return 403, {"detail": "You do not have permission to perform this action."}
        if rest[2:] == ["survey_spec"]:
            if method == "GET":
                return 200, copy.deepcopy(self.specs[(kind, rec["id"])])
            if method == "POST":
                for x in (body or {}).get("spec") or []:
                    if x.get("type") in ("text", "textarea") and not isinstance(x.get("max"), int):
                        return 400, {"error": "max must be a number"}
                if "name" not in (body or {}) or "description" not in (body or {}):
                    return 400, {"error": "'name' missing from survey spec."}
                self.specs[(kind, rec["id"])] = body
            elif method == "DELETE":
                self.specs[(kind, rec["id"])] = {}
            self.writes.append((method, kind, rec["id"], "survey_spec", body))
            return (204 if method == "DELETE" else 200), {}
        if rest[2:] == [] and method == "PATCH":
            rec.update(body)
            self.writes.append((method, kind, rec["id"], "", body))
            return 200, rec
        return 404, {"detail": "Not found."}

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def params(**kw):
    p = {"question": {"variable": "report_email_also", "question_name": "Also email the report to", "question_description": "Optional."},
         "playbooks": ["cert_report.yml", "health_check.yml", "vm_snapshot_report.yml", "poam_status.yml", "vm_datastore_report.yml"],
         "templates": [], "exclude": [], "workflows": True, "project": "", "state": "present", "warn_variables": ["report_email_to"]}
    p.update(kw)
    return p


class AgainstFakeAap(unittest.TestCase):
    def setUp(self):
        self.aap = FakeAap()
        self.api = m.Api(m.connection({"CONTROLLER_HOST": self.aap.url, "CONTROLLER_OAUTH_TOKEN": "goodtoken"}), no_credential=m.NO_CREDENTIAL)
        self.assertEqual(self.api.probe(), "")

    def tearDown(self):
        self.aap.close()

    def by_name(self, results):
        return dict((r["template"], r) for r in results)

    def test_adds_where_it_should(self):
        results, problems = m.run(self.api, params(), False)
        self.assertEqual(problems, [])
        r = self.by_name(results)
        # job templates first, then workflows; Patch (not a report) and Unrelated / Patching (no report in them) left out
        self.assertEqual([x["template"] for x in results], ["Certificate report", "Datastore report", "Health check", "Old cert report",
                                                            "Read only report", "Snapshot report", "Certificate watch", "Nightly (outer)"])
        self.assertEqual(r["Certificate report"]["action"], "added (a new survey, turned on)")
        self.assertEqual(self.aap.specs[("job", 1)]["spec"][0]["variable"], "report_email_also")
        self.assertTrue(self.aap.jobs[0]["survey_enabled"])
        self.assertEqual(r["Health check"]["action"], "added")
        self.assertEqual([x["variable"] for x in self.aap.specs[("job", 2)]["spec"]], ["health_checks", "report_email_also"])
        self.assertEqual(r["Datastore report"]["action"], "added")
        self.assertFalse(self.aap.jobs[6]["survey_enabled"])                     # turned off: stays off
        self.assertIn("was left off", r["Datastore report"]["warnings"][0])
        self.assertIn("its survey asks report_email_to too", r["Snapshot report"]["warnings"][0])
        self.assertEqual(r["Certificate watch"]["action"], "already there")
        self.assertFalse(r["Certificate watch"]["changed"])
        self.assertEqual(r["Nightly (outer)"]["action"], "added (a new survey, turned on)")   # it runs Certificate watch
        self.assertFalse(r["Read only report"]["ok"])
        self.assertIn("may not change this job template", r["Read only report"]["error"])
        self.assertNotIn(("job", 3), [(w[1], w[2]) for w in self.aap.writes])
        again, _ = m.run(self.api, params(), False)
        self.assertEqual([x["template"] for x in again if x["changed"]], [])    # a second run changes nothing
        self.assertEqual(self.by_name(again)["Certificate report"]["action"], "already there")

    def test_check_mode_only_reads(self):
        results, _ = m.run(self.api, params(), True)
        self.assertEqual(self.aap.writes, [])
        self.assertTrue(self.by_name(results)["Certificate report"]["changed"])

    def test_project_exclude_templates_no_workflows(self):
        results, problems = m.run(self.api, params(project="Site automation", exclude=["Health check", "7"], workflows=False,
                                                   templates=["Patching", "Nope"]), False)
        self.assertEqual(problems, ["templates: no job template or workflow Nope that the AAP user can see"])
        self.assertEqual([x["template"] for x in results], ["Certificate report", "Read only report", "Snapshot report", "Patching"])

    def test_absent_takes_it_out(self):
        m.run(self.api, params(), False)
        results, _ = m.run(self.api, params(state="absent"), False)
        r = self.by_name(results)
        self.assertEqual(r["Certificate report"]["action"], "removed (no question left: survey removed, turned off)")
        self.assertEqual(self.aap.specs[("job", 1)], {})
        self.assertFalse(self.aap.jobs[0]["survey_enabled"])
        self.assertEqual(r["Health check"]["action"], "removed")
        self.assertEqual([x["variable"] for x in self.aap.specs[("job", 2)]["spec"]], ["health_checks"])
        self.assertTrue(self.aap.jobs[1]["survey_enabled"])
        self.assertEqual(r["Certificate watch"]["action"], "removed (no question left: survey removed, turned off)")

    def test_no_credential(self):
        self.assertIn("the Admin role on the report templates", m.Api(m.connection({}), no_credential=m.NO_CREDENTIAL).probe())

    def test_the_playbook(self):
        """playbooks/aap_email_question.yml against the fake: what it prints, and that it fails on a template it could not change."""
        ap = os.environ.get("ANSIBLE_PLAYBOOK", "ansible-playbook")
        env = dict(os.environ, CONTROLLER_HOST=self.aap.url, CONTROLLER_OAUTH_TOKEN="goodtoken", ANSIBLE_NOCOLOR="1",
                   ANSIBLE_LOCALHOST_WARNING="False", ANSIBLE_INVENTORY_UNPARSED_WARNING="False")
        extra = json.dumps({"aap_email_question_playbooks": "cert_report.yml, poam_status.yml",
                            "ansible_python_interpreter": sys.executable})
        run = lambda *a: subprocess.run([ap, "playbooks/aap_email_question.yml", "-i", "localhost,", "-e", extra] + list(a),
                                        cwd=REPO, env=env, capture_output=True, text=True, timeout=300)
        out = run("--check")
        self.assertNotEqual(out.returncode, 0, out.stdout)                           # Read only report: cannot be changed
        self.assertIn("job template 1 'Certificate report': would be added (a new survey, turned on)", out.stdout)
        self.assertIn("job template 6 'Read only report': FAILED: the AAP user may not change", out.stdout)
        self.assertIn("workflow 21 'Nightly (outer)': would be added (a new survey, turned on)", out.stdout)
        self.assertEqual(self.aap.writes, [])
        extra = json.dumps({"aap_email_question_playbooks": ["cert_report.yml"], "aap_email_question_exclude": "Old cert report",
                            "ansible_python_interpreter": sys.executable})
        out = run()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("job template 1 'Certificate report': added (a new survey, turned on)", out.stdout)
        self.assertIn("workflow 20 'Certificate watch': already there", out.stdout)
        self.assertNotIn("Old cert report", out.stdout)


# ---- every playbook that can email a report is in aap_email_question_playbooks ----------------------------------
GATE = re.compile(r"when:.*report_email_to \| default\(\[\]\) \| length > 0")


def _load(path):
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except (IOError, OSError, yaml.YAMLError):
        return None


def _role_file(role, tasks_from):
    tf = tasks_from or "main"
    return os.path.join(REPO, "roles", role, "tasks", tf if tf.endswith((".yml", ".yaml")) else tf + ".yml")


def _visit(path, seen, role=None):
    path = os.path.normpath(path)
    if path in seen:
        return
    seen.add(path)
    if role:
        for d in (_load(os.path.join(REPO, "roles", role, "meta", "main.yml")) or {}).get("dependencies") or []:
            n = d if isinstance(d, str) else (d.get("role") or d.get("name"))
            _visit(_role_file(n, None), seen, n)
    _walk(_load(path), path, seen)


def _walk(tasks, here, seen):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        for k in ("block", "rescue", "always", "pre_tasks", "tasks", "post_tasks"):
            if isinstance(t.get(k), list):
                _walk(t[k], here, seen)
        for k in ("roles",):
            for r in t.get(k) or []:
                n = r if isinstance(r, str) else (r.get("role") or r.get("name"))
                _visit(_role_file(n, None), seen, n)
        for k, a in t.items():
            short = k.split(".")[-1]
            if short in ("include_role", "import_role") and isinstance(a, dict) and "{{" not in str(a.get("name")) + str(a.get("tasks_from")):
                _visit(_role_file(a.get("name"), a.get("tasks_from")), seen, a.get("name"))
            elif short in ("include_tasks", "import_tasks"):
                f = a.get("file") if isinstance(a, dict) else a
                if f and "{{" not in str(f):
                    _visit(os.path.join(os.path.dirname(here), f), seen)


def emailing_playbooks():
    gates = set()
    for p in glob.glob(os.path.join(REPO, "roles", "*", "tasks", "*.yml")):
        with open(p) as fh:
            if GATE.search(fh.read()):
                gates.add(os.path.normpath(p))
    gates.add(os.path.normpath(os.path.join(REPO, "roles", "site_email", "tasks", "main.yml")))
    out = []
    for pb in sorted(glob.glob(os.path.join(REPO, "playbooks", "*.yml"))):
        seen = set()
        _walk(_load(pb), pb, seen)
        if seen & gates:
            out.append(os.path.basename(pb))
    return out


class PlaybookList(unittest.TestCase):
    def test_every_emailing_playbook_is_listed(self):
        listed = (_load(os.path.join(REPO, "roles", "aap_email_question", "defaults", "main.yml")) or {})["aap_email_question_playbooks"]
        found = emailing_playbooks()
        self.assertGreater(len(found), 40)
        self.assertIn("cert_report.yml", found)
        self.assertNotIn("patch_hosts.yml", found)
        self.assertEqual(sorted(set(found) - set(listed)), [], "can email, not in aap_email_question_playbooks")
        self.assertEqual(sorted(set(listed) - set(found)), [], "in aap_email_question_playbooks, but never emails")

    def test_every_gate_takes_report_email_also(self):
        for p in glob.glob(os.path.join(REPO, "roles", "*", "tasks", "*.yml")):
            with open(p) as fh:
                lines = fh.read().splitlines()
            for line in lines:
                if "report_email_to | default([]) | length" in line and "when:" in line:
                    self.assertIn("report_email_also | default([]) | length", line, "%s: %s" % (os.path.relpath(p, REPO), line.strip()))


if __name__ == "__main__":
    unittest.main()
