#!/usr/bin/env python3
"""The workflow gate (1.67.0): playbooks/workflow_gate.yml goes on only when the check handed on a plan; workflows.yml puts
it between every check and its approval; the setup job's spec check takes a node's extra_data.
Runs ansible-playbook (ANSIBLE_PLAYBOOK for another ansible-core).
    python3 tests/test_workflow_gate.py"""
import json
import os
import shutil
import subprocess
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
import site_aap_portal_setup as S  # noqa: E402

NOT_A_PLAN = "The check failed for another reason - open the check job; nothing to approve."


class Gate(unittest.TestCase):
    """The gate's playbook: a non-empty list or mapping goes on; nothing, empty or a word ends the workflow."""

    def run_gate(self, extra):
        ap = os.environ.get("ANSIBLE_PLAYBOOK") or shutil.which("ansible-playbook")
        if not ap:
            self.skipTest("no ansible-playbook")
        env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_STDOUT_CALLBACK="default", ANSIBLE_ROLES_PATH="roles",
                   ANSIBLE_LOCALHOST_WARNING="False")
        p = subprocess.run([ap, "-i", "localhost,", "playbooks/workflow_gate.yml", "-e", json.dumps(extra)], cwd=REPO, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        return p.returncode, p.stdout

    def test_a_plan_goes_on(self):
        for var, plan in (("stigman_update_plan", [{"asset": "web01", "rule": "SV-1"}]), ("fapolicyd_workflow_plan", {"h1": ["start"]}),
                          ("cert_install_plan", [{"ritm": "RITM0001"}])):
            rc, out = self.run_gate({"workflow_gate_var": var, var: plan})
            self.assertEqual(rc, 0, out[-800:])
            self.assertIn("The check handed on a plan: 1 item(s) in %s - on to the approval." % var, out)

    def test_no_plan_ends_the_workflow(self):
        for extra in ({"workflow_gate_var": "stigman_update_plan"}, {"workflow_gate_var": "stigman_update_plan", "stigman_update_plan": []},
                      {"workflow_gate_var": "fapolicyd_workflow_plan", "fapolicyd_workflow_plan": {}},
                      {"workflow_gate_var": "stigman_update_plan", "stigman_update_plan": "x"}):
            rc, out = self.run_gate(extra)
            self.assertNotEqual(rc, 0, extra)
            self.assertIn(NOT_A_PLAN, " ".join(out.split()), extra)

    def test_no_variable_named(self):
        for extra in ({}, {"workflow_gate_var": "not a name"}):
            rc, out = self.run_gate(extra)
            self.assertNotEqual(rc, 0)
            self.assertIn("Set workflow_gate_var to the check's artifact", " ".join(out.split()))


class Spec(unittest.TestCase):
    """workflows.yml: every approval node's parent is the gate, on success, from the check's failure."""

    def spec(self):
        with open(os.path.join(REPO, "roles", "site_portal_stack", "files", "workflows.yml")) as f:
            return yaml.safe_load(f)

    def test_every_approval_behind_the_gate(self):
        sp = self.spec()
        self.assertEqual(S.check_spec(sp), [])
        gate = [t for t in sp["templates"] if t["name"] == "Workflow gate - has a plan"]
        self.assertEqual([(t["playbook"], t["credentials"], t["prompts"]) for t in gate], [("playbooks/workflow_gate.yml", [], {"variables": True})])
        seen = 0
        for w in sp["workflows"]:
            nodes = dict((n["id"], n) for n in w["nodes"])
            for n in w["nodes"]:
                if not n.get("approval"):
                    continue
                seen += 1
                parents = [(p["id"], link) for p in nodes.values() for link in ("success", "failure", "always") if n["id"] in (p.get(link) or [])]
                self.assertEqual(len(parents), 1, (w["name"], parents))
                g, link = nodes[parents[0][0]], parents[0][1]
                self.assertEqual((g.get("template"), link), ("Workflow gate - has a plan", "success"), w["name"])
                self.assertTrue(str((g.get("extra_data") or {}).get("workflow_gate_var") or ""), w["name"])
                into = [(p["id"], link_) for p in nodes.values() for link_ in ("success", "failure", "always") if g["id"] in (p.get(link_) or [])]
                self.assertEqual([x[1] for x in into], ["failure"], w["name"])
        self.assertGreaterEqual(seen, 1)

    def test_extra_data_checked(self):
        sp = {"templates": [{"name": "T", "playbook": "playbooks/t.yml"}],
              "workflows": [{"name": "W", "nodes": [{"id": "a", "template": "T", "extra_data": ["x"]},
                                                     {"id": "b", "approval": {"name": "ok?", "timeout": 60}, "extra_data": {"x": 1}}]}]}
        self.assertEqual(S.check_spec(sp), ["workflow 'W', node a: extra_data is a mapping, on a template's node",
                                            "workflow 'W', node b: extra_data is a mapping, on a template's node"])


if __name__ == "__main__":
    unittest.main()
