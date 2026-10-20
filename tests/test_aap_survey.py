#!/usr/bin/env python3
"""Unit tests for roles/vmware_vm/library/site_aap_survey.py (the pick lists write their choices into AAP
surveys): the survey changed in memory, then the module against the fake AAP (tests/servicenow/aapmock.py)
in this process. Needs ansible-core (the module's imports): python3 tests/test_aap_survey.py"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "roles", "vmware_vm", "library"))
_spec = importlib.util.spec_from_file_location("ansible.module_utils.site_aap", os.path.join(
    HERE, "..", "roles", "vmware_vm", "module_utils", "site_aap.py"))
_aap = importlib.util.module_from_spec(_spec)
sys.modules["ansible.module_utils.site_aap"] = _aap
_spec.loader.exec_module(_aap)
import site_aap_survey as m  # noqa: E402


def q(var="vm_snapshot_picks", kind="multiselect", choices=None, default="", desc="Pick"):
    out = {"variable": var, "type": kind, "question_name": var, "question_description": desc, "required": True, "default": default}
    if choices is not None:
        out["choices"] = choices
    return out


class SetChoices(unittest.TestCase):
    def test_replaces_keeps_the_rest(self):
        spec = {"name": "", "description": "", "spec": [q(choices=["a", "b"], default="b"), q("vm_power_mode", "multiplechoice", ["guest", "hard"])]}
        new, err, changes = m.set_choices(spec, "vm_snapshot_picks", ["b", "c\nd", "b", " "], note="of 2026-10-20 10:00 UTC: 2")
        self.assertEqual(err, "")
        self.assertEqual(new["spec"][0]["choices"], ["b", "c d"])            # one line each, once each
        self.assertEqual(new["spec"][0]["default"], "b")                     # still offered: kept
        self.assertEqual(new["spec"][0]["question_description"], "Pick [pick list of 2026-10-20 10:00 UTC: 2]")
        self.assertEqual(new["spec"][1], spec["spec"][1])
        self.assertEqual(spec["spec"][0]["choices"], ["a", "b"])             # the original untouched
        self.assertEqual(changes, ["2 choice(s)"])
        again, _, ch = m.set_choices(new, "vm_snapshot_picks", ["b", "c d"], note="of 2026-10-20 10:30 UTC: 2")
        self.assertEqual(again["spec"][0]["question_description"], "Pick [pick list of 2026-10-20 10:30 UTC: 2]")   # the note replaced
        self.assertEqual(ch, ["note"])
        self.assertEqual(m.set_choices(again, "vm_snapshot_picks", ["b", "c d"], note="of 2026-10-20 10:30 UTC: 2")[2], [])

    def test_text_choices_and_defaults(self):
        spec = {"spec": [q(choices="old one\nold two", default="old two\nold one")]}
        new, _, changes = m.set_choices(spec, "vm_snapshot_picks", ["old one", "new"])
        self.assertEqual((new["spec"][0]["choices"], new["spec"][0]["default"]), ("old one\nnew", "old one"))   # text stays text
        self.assertIn("default kept to the choices still offered", changes)
        new, _, changes = m.set_choices(spec, "vm_snapshot_picks", ["new"])
        self.assertEqual(new["spec"][0]["default"], "")
        self.assertIn("default cleared", changes)

    def test_single_select_made_multiple_and_empty(self):
        spec = {"spec": [q(kind="multiplechoice", choices=["x"], default="x")]}
        new, _, changes = m.set_choices(spec, "vm_snapshot_picks", [], empty_choice="(nothing to pick: none)")
        self.assertEqual((new["spec"][0]["type"], new["spec"][0]["choices"], new["spec"][0]["default"]),
                         ("multiselect", ["(nothing to pick: none)"], ""))
        self.assertEqual(changes[0], "made multiple select")
        new, _, _ = m.set_choices(spec, "vm_snapshot_picks", ["y"], multiselect=False)
        self.assertEqual(new["spec"][0]["type"], "multiplechoice")
        self.assertIn("AAP needs at least one choice", m.set_choices(spec, "vm_snapshot_picks", [])[1])
        new, _, _ = m.set_choices({"spec": [q()]}, "vm_snapshot_picks", ["a"])           # no choices yet: a list
        self.assertEqual(new["spec"][0]["choices"], ["a"])

    def test_refused(self):
        spec = {"spec": [q(kind="textarea"), q("other")]}
        self.assertIn("the question vm_snapshot_picks is a textarea question: make it Multiple choice (multiple select)",
                      m.set_choices(spec, "vm_snapshot_picks", ["a"])[1])
        self.assertIn("its survey has no question whose variable is nope (its questions: vm_snapshot_picks, other)",
                      m.set_choices(spec, "nope", ["a"])[1])


class AgainstFakeAap(unittest.TestCase):
    def setUp(self):
        os.environ.pop("AAPMOCK_SURVEY_FAIL", None)
        self.tmp = tempfile.mkdtemp()
        spec = importlib.util.spec_from_file_location("aapmock_%d" % id(self), os.path.join(HERE, "servicenow", "aapmock.py"))
        self.mock = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mock)                  # fresh surveys for each test
        self.mock.SURVEY_LOG = os.path.join(self.tmp, "survey.jsonl")
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), self.mock.H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        env = {"CONTROLLER_HOST": "http://127.0.0.1:%d" % self.srv.server_port, "CONTROLLER_USERNAME": "admin", "CONTROLLER_PASSWORD": "goodpw"}
        self.api = m.Api(m.connection(env), no_credential=m.NO_CREDENTIAL)
        self.assertEqual(self.api.probe(), "")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def log(self):
        if not os.path.exists(self.mock.SURVEY_LOG):
            return []
        with open(self.mock.SURVEY_LOG) as f:
            return [json.loads(x) for x in f]

    def one(self, template, kind="", question="vm_snapshot_picks", choices=("a [vc/vm-1/1]",), check=False, **kw):
        return m.one(self.api, dict({"template": template, "kind": kind, "question": question, "choices": list(choices)}, **kw), check)

    def test_job_and_workflow(self):
        r = self.one("VM - delete picked snapshots")
        self.assertEqual((r["ok"], r["kind"], r["id"], r["choices"], r["changed"]), (True, "job", 51, 1, True))
        self.assertIn("default cleared", r["changes"])
        r = self.one("VM - delete picked snapshots (workflow)", note="of now: 1")
        self.assertEqual((r["ok"], r["kind"], r["id"]), (True, "workflow", 61))
        self.assertEqual([(x["kind"], x["id"]) for x in self.log()], [("job", 51), ("workflow", 61)])
        q0 = self.log()[0]["spec"]["spec"][0]
        self.assertEqual((q0["choices"], q0["default"], q0["type"]), ("a [vc/vm-1/1]", "", "multiselect"))   # text, as AAP had it
        self.assertEqual(self.one("61", kind="workflow", note="of now: 1")["changed"], False)              # by ID: nothing new
        r = self.one("VM - restart picked VMs", question="vm_reboot_picks")
        self.assertEqual(self.log()[-1]["spec"]["spec"][0]["type"], "multiselect")
        self.assertEqual(self.log()[-1]["spec"]["spec"][1]["variable"], "vm_power_mode")                  # the other question kept
        self.assertIn("made multiple select", r["changes"])

    def test_dry_run_writes_nothing(self):
        r = self.one("VM - delete picked snapshots", check=True)
        self.assertEqual((r["ok"], r["changed"]), (True, True))
        self.assertEqual(self.log(), [])

    def test_refused(self):
        self.assertIn("is a textarea question", self.one("Text question")["error"])
        self.assertIn("it has no survey: add one", self.one("No survey")["error"])
        self.assertIn("no job template or workflow job template Nope that the AAP user can see", self.one("Nope")["error"])
        self.assertIn("2 templates are named Both (job template 56, workflow job template 62)", self.one("Both", question="vm_reboot_picks")["error"])
        self.assertEqual(self.one("Both", kind="workflow", question="vm_reboot_picks")["id"], 62)
        self.assertIn("the AAP user may not change job template Read only", self.one("Read only", question="vm_reboot_picks")["error"])
        self.assertIn("kind is jobs", self.one("x", kind="jobs")["error"])
        r = self.one("Survey off", question="vm_reboot_picks")
        self.assertTrue(r["ok"])
        self.assertIn("the survey of job template Survey off is turned off", r["warnings"][0])
        os.environ["AAPMOCK_SURVEY_FAIL"] = "1"
        self.assertIn("AAP refused the new survey (HTTP 400): error: Survey question 1", self.one("VM - delete picked snapshots")["error"])

    def test_no_credential(self):
        self.assertIn("the Admin role on the templates", m.Api(m.connection({}), no_credential=m.NO_CREDENTIAL).probe())


if __name__ == "__main__":
    unittest.main()
