#!/usr/bin/env python3
"""AAP - set up portal actions, 1.68.1: the scorecard's schedule text, the Variables of a template read as AAP gives them,
and the check template of workflow 40 found in workflows.yml. The whole job is tests/portal_setup/run_scorecard_setup_test.sh.
    python3 tests/test_portal_setup_scorecard.py"""
import os
import sys
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "roles", "aap_portal_setup", "library"))
sys.path.insert(0, os.path.join(REPO, "plugins", "module_utils"))
import ansible.module_utils  # noqa: E402
import site_aap  # noqa: E402
sys.modules.setdefault("ansible.module_utils.site_aap", site_aap)
ansible.module_utils.site_aap = site_aap
import site_aap_workflows  # noqa: E402 - 1.83.0: the workflow parts both setup jobs share
sys.modules.setdefault("ansible.module_utils.site_aap_workflows", site_aap_workflows)
ansible.module_utils.site_aap_workflows = site_aap_workflows
import site_aap_portal_setup as S  # noqa: E402


class When(unittest.TestCase):
    def test_weekly(self):
        self.assertEqual(S.rrule_of("weekly Mon 06:00", ""), "DTSTART;TZID=UTC:20260105T060000 RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=MO")
        self.assertEqual(S.rrule_of("Weekly friday 7:30", "Europe/Berlin"),
                         "DTSTART;TZID=Europe/Berlin:20260105T073000 RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=FR")

    def test_daily(self):
        self.assertEqual(S.rrule_of("daily 23:59", "UTC"), "DTSTART;TZID=UTC:20260105T235900 RRULE:FREQ=DAILY;INTERVAL=1")

    def test_not_understood(self):
        for bad in ("", "sometimes", "weekly 06:00", "weekly Mon 25:00", "daily Mon 06:00", "weekly Xyz 06:00", "weekly Mon 6"):
            with self.assertRaises(ValueError, msg=bad):
                S.rrule_of(bad, "")


class Vars(unittest.TestCase):
    def test_json_yaml_and_empty(self):
        self.assertEqual(S.parse_vars('{"a": 1}'), {"a": 1})
        self.assertEqual(S.parse_vars("a: 1\nb: [x]\n"), {"a": 1, "b": ["x"]})
        self.assertEqual(S.parse_vars(""), {})
        self.assertEqual(S.parse_vars(None), {})
        self.assertEqual(S.parse_vars("---"), {})

    def test_unreadable(self):
        self.assertIsNone(S.parse_vars("[1, 2]"))
        self.assertIsNone(S.parse_vars("a: [unclosed"))


class Source(unittest.TestCase):
    def test_workflow_40s_check(self):
        with open(os.path.join(REPO, "roles", "site_portal_stack", "files", "workflows.yml")) as fh:
            spec = yaml.safe_load(fh)
        name = S.source_template(spec)
        self.assertEqual(name, "STIG Manager - import checklists - check")
        self.assertIn(name, [t["name"] for t in spec["templates"]])

    def test_none(self):
        self.assertEqual(S.source_template({"workflows": [{"number": 41, "nodes": []}]}), "")


class Split(unittest.TestCase):
    def test_two_kinds_resolving_to_one_credential_match_aap(self):
        setup = S.Setup(None, {"credentials": {"example": "Example", "other": "Example"}}, False)
        setup.by_name = lambda kind, name: [{"id": 7}]
        self.assertEqual(setup.cred_ids(["example", "other"]), ([7], ""))


    def test_new_check_template_split_after_scorecard_creation(self):
        from unittest.mock import Mock
        rows = []
        def call(method, path, body=None):
            if method == "GET":
                return 200, {"results": [] if "credentials/" in path else rows}
            if method == "POST" and path == "job_templates/":
                rows.append(dict(body, id=7, organization=1))
                return 201, {"id": 7}
            if method == "PATCH" and path == "job_templates/7/":
                rows[0].update(body)
                return 200, rows[0]
            self.fail((method, path))
        api = Mock()
        api.call.side_effect = call
        setup = S.Setup(api, {"overwrite": False, "credentials": {}}, False)
        setup.org_id = 1
        spec = {"name": "Example check", "playbook": "example.yml"}
        refs = {"project": 2, "inventories": {"default": 3}}
        oid, action = setup.template(spec, refs, with_action=True)
        setup.apply_split(spec, action, refs, oid)
        self.assertEqual([r["action"] for r in setup.results], ["create", "update"])
        self.assertEqual(S.parse_vars(rows[0]["extra_vars"]), {S.MODE: "import"})


class CopyEE(unittest.TestCase):
    """Codex 1.68.1: the copied template keeps the check template's execution environment - none (null, inherited from
    the project or organization) stays none; the setup job's own is used only when AAP did not say."""
    def want(self, copy):
        st = S.Setup(None, {}, False)
        st.all = lambda path: []
        got = {}
        st.reconcile = lambda kind, api, name, want, read, write: (got.update(want), (5, "create"))[1]
        st.template({"name": "STIG - weekly scorecard", "playbook": "stigman_ckl_import.yml"},
                    {"project": 1, "ee": 9, "inventories": {}}, copy=dict({"id": 3, "project": 1, "inventory": 4}, **copy))
        return got

    def test_inherited_stays_inherited(self):
        self.assertNotIn("execution_environment", self.want({"execution_environment": None}))

    def test_its_own_kept(self):
        self.assertEqual(self.want({"execution_environment": 7})["execution_environment"], 7)

    def test_not_said_the_setup_jobs(self):
        self.assertEqual(self.want({})["execution_environment"], 9)


if __name__ == "__main__":
    unittest.main()
