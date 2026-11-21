#!/usr/bin/env python3
"""The workflow gate (1.67.0): playbooks/workflow_gate.yml goes on only when the check handed on a plan; workflows.yml puts
it between every check and its approval; the setup job's spec check takes a node's extra_data.
Runs ansible-playbook (ANSIBLE_PLAYBOOK for another ansible-core).
    python3 tests/test_workflow_gate.py"""
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from unittest.mock import Mock

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
sys.path.insert(0, os.path.join(REPO, "roles", "aap_workflow_gates", "library"))
import site_aap_workflow_gates as G  # noqa: E402

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
        """No approval hangs straight off a check's failure: a check -> approval goes through the gate (failure, then
        success). An approval after a plan job's success (the plan fails on an error, so the approval never comes) needs none."""
        sp = self.spec()
        self.assertEqual(S.check_spec(sp), [])
        gate = [t for t in sp["templates"] if t["name"] == "Workflow gate - has a plan"]
        self.assertEqual([(t["playbook"], t["credentials"], t["prompts"]) for t in gate], [("playbooks/workflow_gate.yml", [], {"variables": True})])
        gated = 0
        for w in sp["workflows"]:
            nodes = dict((n["id"], n) for n in w["nodes"])
            for n in w["nodes"]:
                if not n.get("approval"):
                    continue
                parents = [(p["id"], link) for p in nodes.values() for link in ("success", "failure", "always") if n["id"] in (p.get(link) or [])]
                self.assertTrue(parents, w["name"])
                for pid, link in parents:
                    g = nodes[pid]
                    self.assertNotEqual(link, "failure", "%s: approval %s straight after a failure - put the gate between" % (w["name"], n["id"]))
                    self.assertNotEqual(link, "always", "%s: approval %s after an always link" % (w["name"], n["id"]))
                    if g.get("template") == "Workflow gate - has a plan":
                        gated += 1
                        self.assertTrue(str((g.get("extra_data") or {}).get("workflow_gate_var") or ""), w["name"])
                        into = [link_ for p in nodes.values() for link_ in ("success", "failure", "always") if g["id"] in (p.get(link_) or [])]
                        # a check that fails on purpose reaches its gate on failure (37, 40); a plan step that ends green
                        # with its plan (49-51) on success - one link either way, never always
                        self.assertIn(into, (["failure"], ["success"]), w["name"])
        self.assertGreaterEqual(gated, 1)

    def test_extra_data_checked(self):
        sp = {"templates": [{"name": "T", "playbook": "playbooks/t.yml"}],
              "workflows": [{"name": "W", "nodes": [{"id": "a", "template": "T", "extra_data": ["x"]},
                                                     {"id": "b", "approval": {"name": "ok?", "timeout": 60}, "extra_data": {"x": 1}}]}]}
        self.assertEqual(S.check_spec(sp), ["workflow 'W', node a: extra_data is a mapping, on a template's node",
                                            "workflow 'W', node b: extra_data is a mapping, on a template's node"])


class SyncFingerprints(unittest.TestCase):
    want = {"name": "Example", "description": "Catalog description"}
    key = "workflow/Example"

    def reconcile(self, have, known=None, check=False, overwrite=False):
        api = Mock()
        api.call.return_value = (200, {"results": [dict(self.want, id=7, description=have)]})
        sync = site_aap_workflows.Sync(api, {"known": known, "overwrite": overwrite}, check, S.MARK, "portal_setup_overwrite")
        oid, action = sync.reconcile("workflow", "workflow_job_templates", "Example", self.want)
        self.assertEqual(oid, 7)
        writes = [c.args for c in api.call.call_args_list if c.args[0] != "GET"]
        return action, sync.out, writes, sync.results[-1]["why"]

    def fp(self, description):
        return site_aap_workflows.fingerprint(dict(self.want, description=description, extra=None))

    def test_create_records_the_workflow_parts_in_the_map(self):
        api = Mock()
        api.call.side_effect = [(200, {"results": []}), (201, {"id": 7})]
        sync = site_aap_workflows.Sync(api, {"overwrite": False}, False, S.MARK, "portal_setup_overwrite")
        parts = [{"id": "example-step"}]
        write_parts = Mock(return_value=parts)
        result = sync.reconcile("workflow", "workflow_job_templates", "Example", self.want, extra_write=write_parts)
        self.assertEqual(result, (7, "create"))
        api.call.assert_called_with("POST", "workflow_job_templates/", self.want)
        write_parts.assert_any_call(7, True)
        self.assertEqual(sync.out, {self.key: site_aap_workflows.fingerprint(dict(self.want, extra=parts))})

    def test_known_map_allows_catalog_update_and_preserves_other_entries(self):
        previous = self.fp("Previous description")
        known = {self.key: previous, "workflow/Other": "0123456789ab"}
        action, out, writes, _ = self.reconcile("Previous description", known)
        self.assertEqual(action, "update")
        self.assertEqual(writes, [("PATCH", "workflow_job_templates/7/", self.want)])
        self.assertEqual(out, {self.key: self.fp(self.want["description"]), "workflow/Other": "0123456789ab"})
        self.assertEqual(known[self.key], previous)

    def test_hand_edit_keeps_the_recorded_fingerprint_until_overwrite(self):
        known = {self.key: self.fp("Previous description")}
        action, out, writes, _ = self.reconcile("Edited by hand", known)
        self.assertEqual((action, out, writes), ("hand-edited", known, []))
        action, out, writes, _ = self.reconcile("Edited by hand", out, overwrite=True)
        self.assertEqual((action, out), ("update", {self.key: self.fp(self.want["description"])}))
        self.assertEqual(writes, [("PATCH", "workflow_job_templates/7/", self.want)])

    def test_without_history_only_an_exact_match_is_adopted(self):
        desc = self.want["description"]
        self.assertEqual(self.reconcile(desc)[:3], ("unchanged", {self.key: self.fp(desc)}, []))
        self.assertEqual(self.reconcile("Edited by hand")[:3], ("hand-edited", {}, []))
        self.assertEqual(self.reconcile("Edited by hand", known={})[:3], ("not ours", {}, []))

    def test_old_marker_migration_on_check_and_run(self):
        desc = self.want["description"]
        fp = self.fp(desc)
        marker = " [set up by AAP - set up portal actions, fingerprint %s]" % fp
        self.assertEqual(self.reconcile(desc + marker, check=True),
                         ("update", {self.key: fp}, [], "would remove the old fingerprint text"))
        self.assertEqual(self.reconcile(desc + marker),
                         ("update", {self.key: fp}, [("PATCH", "workflow_job_templates/7/", {"description": desc})],
                          "removed the old fingerprint text"))
        self.assertEqual(self.reconcile("Edited by hand" + marker)[:3], ("hand-edited", {self.key: fp}, []))


class AddGates(unittest.TestCase):
    """AAP - add workflow gates (part I): its table of checks is the doc's, the gate template it makes is the setup job's."""

    def test_the_gate_template_carries_no_fingerprint_text(self):
        # 1.93.0: its description is written as it is; the setup job judges it by comparing, or by its artifact
        self.assertFalse(hasattr(G, "MARK_TEXT"))
        self.assertFalse(hasattr(G, "fingerprint"))
        with open(os.path.join(REPO, "roles", "aap_workflow_gates", "library", "site_aap_workflow_gates.py")) as f:
            self.assertNotIn("fingerprint %s", f.read())

    def test_the_checks_are_the_docs(self):
        with open(os.path.join(REPO, "docs", "WORKFLOWS_AND_SCHEDULES.md")) as f:
            doc = f.read()
        sec = doc[doc.index("## The gate"):doc.index("## Linux workflows")]
        for pb, var in G.CHECKS.items():
            self.assertIn("`%s`" % var, sec, pb)
            self.assertTrue(os.path.exists(os.path.join(REPO, "playbooks", pb)), pb)
            with open(os.path.join(REPO, "playbooks", pb)) as f:
                src = f.read()
            roles = re.findall(r"name: ([a-z_]+)\n", src)
            texts = [src]
            for r in roles:
                for dp, _d, fns in os.walk(os.path.join(REPO, "roles", r)):
                    for fn in fns:
                        if fn.endswith(".yml"):
                            with open(os.path.join(dp, fn)) as f:
                                texts.append(f.read())
            self.assertTrue(any(re.search(r"set_stats:\s*\n\s*data:\s*\n(\s+[a-z_]+:.*\n)*?\s+%s:" % var, x) for x in texts),
                            "%s does not set_stats %s" % (pb, var))
        self.assertIn(G.GATE_PLAYBOOK, os.listdir(os.path.join(REPO, "playbooks")))

    def test_which_workflows(self):
        w = {"id": 7, "name": "Linux - fapolicyd (approval)"}
        self.assertTrue(G.wanted([], w))
        self.assertTrue(G.wanted(["linux - FAPOLICYD*"], w))
        self.assertTrue(G.wanted([7], w) and G.wanted(["7"], w))
        self.assertFalse(G.wanted(["Linux - fapolicyd"], w))           # a name is the whole name
        self.assertEqual(G.playbook_file("playbooks/sub/x.yml"), "x.yml")


if __name__ == "__main__":
    unittest.main()
