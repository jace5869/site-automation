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
from unittest.mock import Mock, patch

import yaml
from jinja2.nativetypes import NativeEnvironment

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

sys.path.insert(0, os.path.join(REPO, "roles", "aap_templates", "library"))
import site_aap_describe as D
import site_aap_template_workflows as W
sys.path.insert(0, os.path.join(REPO, "roles", "aap_setup_history", "library"))
import site_aap_setup_history as H

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
    key = "1/workflow/Example"

    def reconcile(self, have, known=None, check=False, overwrite=False, mark=S.MARK):
        api = Mock()
        api.call.return_value = (200, {"results": [dict(self.want, id=7, organization=1, description=have)]})
        sync = site_aap_workflows.Sync(api, {"known": known, "overwrite": overwrite}, check, mark, "portal_setup_overwrite")
        sync.org_id = 1
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
        sync.org_id = 1
        parts = [{"id": "example-step"}]
        write_parts = Mock(return_value=parts)
        result = sync.reconcile("workflow", "workflow_job_templates", "Example", self.want, extra_write=write_parts)
        self.assertEqual(result, (7, "create"))
        api.call.assert_called_with("POST", "workflow_job_templates/", self.want)
        write_parts.assert_any_call(7, True)
        self.assertEqual(sync.out, {self.key: site_aap_workflows.fingerprint(dict(self.want, extra=parts))})

    def test_known_map_allows_catalog_update_and_preserves_other_entries(self):
        previous = self.fp("Previous description")
        known = {self.key: previous, "2/workflow/Example": "0123456789ab"}
        action, out, writes, _ = self.reconcile("Previous description", known)
        self.assertEqual(action, "update")
        self.assertEqual(writes, [("PATCH", "workflow_job_templates/7/", {"description": self.want["description"]})])
        self.assertEqual(out, {self.key: self.fp(self.want["description"]), "2/workflow/Example": "0123456789ab"})
        self.assertEqual(known[self.key], previous)

    def test_filtered_run_returns_the_whole_previous_map(self):
        known = {self.key: self.fp(self.want["description"]), "2/workflow/Example": "0123456789ab"}
        module = Mock(check_mode=False, params={
            "organization": "Example org", "project": "Example project", "only": ["Example job"],
            "workflows": [dict(self.want, nodes=[{"id": "step"}])], "templates": [], "uses": {},
            "known": known, "labels": [], "ca_path": "", "timeout": 30,
        })
        with patch.object(W, "AnsibleModule", return_value=module), patch.object(W, "Api") as ctor, patch.object(W, "connection"):
            api = ctor.return_value
            api.probe.return_value = ""
            api.call.side_effect = [(200, {"results": [{"id": 1, "name": "Example org"}]}),
                                    (200, {"results": [{"id": 2, "name": "Example project", "organization": 1}]})]
            W.main()
        module.exit_json.assert_called_once_with(changed=False, results=[], problems=[], fingerprints=known, org_id=1)

    def test_hand_edit_keeps_the_recorded_fingerprint_until_overwrite(self):
        known = {self.key: self.fp("Previous description")}
        action, out, writes, _ = self.reconcile("Edited by hand", known)
        self.assertEqual((action, out, writes), ("hand-edited", known, []))
        action, out, writes, _ = self.reconcile("Edited by hand", out, overwrite=True)
        self.assertEqual((action, out), ("update", {self.key: self.fp(self.want["description"])}))
        self.assertEqual(writes, [("PATCH", "workflow_job_templates/7/", {"description": self.want["description"]})])

    def test_unproven_ownership_even_when_fields_match(self):
        desc = self.want["description"]
        for known in (None, {}, {"2/workflow/Example": self.fp(desc)}, {"workflow/Example": self.fp(desc)}):
            for have in (desc, "Edited by hand"):
                action, out, writes, why = self.reconcile(have, known)
                self.assertEqual((action, out, writes), ("not ours", known or {}, []))
                self.assertIn("no fingerprint", why)

    def test_same_name_in_two_organizations(self):
        live = dict(self.want, organization=1, id=7)
        api = Mock()
        api.call.side_effect = [(200, {"results": [live]}), (201, {"id": 8})]
        first = {self.key: self.fp(self.want["description"])}
        sync = site_aap_workflows.Sync(api, {"known": first, "overwrite": False}, False, S.MARK, "portal_setup_overwrite")
        sync.org_id = 2
        other = dict(self.want, description="Second organization")
        self.assertEqual(sync.reconcile("workflow", "workflow_job_templates", "Example", other), (8, "create"))
        self.assertEqual([r["action"] for r in sync.results], ["create"])
        self.assertEqual(sync.out, {self.key: first[self.key], "2/workflow/Example": self.fp(other["description"])})
        # Switching back to the first organization compares against its own baseline.
        sync = site_aap_workflows.Sync(api, {"known": sync.out, "overwrite": False}, False, S.MARK, "portal_setup_overwrite")
        sync.org_id = 1
        api.call.side_effect = [(200, {"results": [live, dict(other, organization=2, id=8)]}), (200, {})]
        updated = dict(self.want, description="Updated first organization")
        self.assertEqual(sync.reconcile("workflow", "workflow_job_templates", "Example", updated), (7, "update"))
        self.assertEqual([r["action"] for r in sync.results], ["update"])
        self.assertEqual(sync.out, {self.key: self.fp(updated["description"]), "2/workflow/Example": self.fp(other["description"])})

    def test_literal_edit_to_json_description_is_kept(self):
        original = '{"example": 1}'
        self.assertEqual(self.reconcile('{"example":1}', {self.key: self.fp(original)})[0], "hand-edited")

    def test_second_reconcile_uses_this_runs_fingerprint(self):
        api = Mock()
        api.call.side_effect = [(200, {"results": []}), (201, {"id": 7}),
                                (200, {"results": [dict(self.want, organization=1, id=7)]}), (200, {})]
        sync = site_aap_workflows.Sync(api, {"overwrite": False}, False, S.MARK, "portal_setup_overwrite")
        sync.org_id = 1
        sync.reconcile("workflow", "workflow_job_templates", "Example", self.want)
        updated = dict(self.want, description="Second pass")
        self.assertEqual(sync.reconcile("workflow", "workflow_job_templates", "Example", updated), (7, "update"))
        self.assertEqual(sync.out, {self.key: self.fp("Second pass")})

    def test_legacy_literal_block_hashes_its_final_newline(self):
        self.want = yaml.safe_load("name: Example\ndescription: |\n  First line\n  Second line\n")
        desc = self.want["description"]
        self.assertTrue(desc.endswith("\n"))
        # Use the old writer's serialization, independently of the current fingerprint function.
        import hashlib
        fp = hashlib.sha256(json.dumps(dict(self.want, extra=""), sort_keys=True).encode()).hexdigest()[:12]
        for job, mark in (("portal actions", S.MARK), ("job templates", W.MARK)):
            suffix = " [set up by AAP - set up %s, fingerprint %s]" % (job, fp)
            action, out, writes, _ = self.reconcile(desc + suffix, mark=mark)
            self.assertEqual((action, out, writes), ("update", {self.key: fp},
                             [("PATCH", "workflow_job_templates/7/", {"description": desc})]))
            self.assertEqual(self.reconcile(desc.rstrip() + suffix, mark=mark)[:3], ("hand-edited", {}, []))

    def test_old_marker_migration_on_check_and_run(self):
        desc = self.want["description"]
        fp = self.fp(desc)
        marker = " [set up by AAP - set up portal actions, fingerprint %s]" % fp
        for known in (None, {}, {"2/workflow/Example": fp}, {self.key: self.fp("Previous description")}):
            with self.subTest(known=known):
                migrated = dict(known or {})
                migrated[self.key] = fp
                self.assertEqual(self.reconcile(desc + marker, known, check=True),
                                 ("update", migrated, [], "would remove the old fingerprint text"))
                self.assertEqual(self.reconcile(desc + marker, known),
                                 ("update", migrated, [("PATCH", "workflow_job_templates/7/", {"description": desc})],
                                  "removed the old fingerprint text"))
                self.assertEqual(self.reconcile("Edited by hand" + marker, known)[:3], ("hand-edited", known or {}, []))


class SetupHistory(unittest.TestCase):
    def test_pages_successful_runs_until_a_map_even_an_empty_one(self):
        api = Mock()
        rows = [{"id": i} for i in range(98, 84, -1)]
        api.call.side_effect = ([(200, {"results": rows, "next": "next"})] +
                                [(200, {"artifacts": {}})] * len(rows) +
                                [(200, {"results": [{"id": 84}], "next": None}),
                                 (200, {"artifacts": {"aap_setup_fingerprints": {}}})])
        self.assertEqual(H.latest_map(api, 99, 7, []), {})
        lists = [c.args[1] for c in api.call.call_args_list if c.args[1].startswith("jobs/?")]
        self.assertEqual(len(lists), 2)
        self.assertIn("id__lt=85", lists[1])
        for url in lists:
            for field in ("job_template=7", "job_type=run", "status=successful", "order_by=-id"):
                self.assertIn(field, url)

    def test_checks_and_failed_jobs_with_artifacts_are_never_baselines(self):
        api = Mock()
        rows = [{"id": i, "status": "successful", "job_type": "check"} for i in range(98, 82, -1)]
        rows += [{"id": 82, "status": "failed", "job_type": "run"}, {"id": 81, "status": "successful", "job_type": "run"}]
        saved = {"1/template/Example": "0123456789ab"}
        api.call.side_effect = [(200, {"results": rows, "next": None}), (200, {"artifacts": {"aap_setup_fingerprints": saved}})]
        self.assertEqual(H.latest_map(api, 99, 7, []), saved)
        api.call.assert_called_with("GET", "jobs/81/")

    def test_history_exhausted_or_hidden(self):
        api = Mock()
        page = (200, {"results": [], "next": None})
        api.call.side_effect = [page, (200, {"id": 8})]
        self.assertIsNone(H.latest_map(api, 8, 3, []))
        api.call.side_effect = [page, (403, {}), (403, {})]
        with self.assertRaisesRegex(ValueError, "HTTP 403"):
            H.latest_map(api, 8, 3, [])

    def test_failed_read_never_selects_an_older_map(self):
        page = (200, {"results": [{"id": 7}, {"id": 6}], "next": None})
        saved = {"1/workflow/Example": "0123456789ab", "2/workflow/Other": "123456789abc"}
        for prefix, response in (([], page), ([page], (200, {"artifacts": {"aap_setup_fingerprints": saved}}))):
            for recover in (False, True):
                api = Mock()
                errors = []
                replies = prefix + [(503, {}), response if recover else (503, {})]
                if recover and not prefix:
                    replies.append((200, {"artifacts": {"aap_setup_fingerprints": saved}}))
                api.call.side_effect = replies
                if recover:
                    self.assertEqual(H.latest_map(api, 8, 3, errors), saved)
                else:
                    with self.assertRaisesRegex(ValueError, "HTTP 503"):
                        H.latest_map(api, 8, 3, errors)
                self.assertEqual(len(errors), 1)
                self.assertNotIn(("GET", "jobs/6/"), [c.args for c in api.call.call_args_list])

    def test_unreadable_detail_or_invalid_map_does_not_authorize_fallback(self):
        for detail in ({}, {"artifacts": []}, {"artifacts": {"aap_setup_fingerprints": None}},
                       {"artifacts": {"aap_setup_fingerprints": {"1/template/Example": {}}}}):
            api = Mock()
            api.call.side_effect = [(200, {"results": [{"id": 7}, {"id": 6}], "next": None}), (200, detail)]
            with self.assertRaises(ValueError):
                H.latest_map(api, 8, 3, [])
            self.assertEqual(api.call.call_count, 2)

    def test_roles_read_before_writes_with_their_own_tls_settings(self):
        def tasks(role):
            with open(os.path.join(REPO, "roles", role, "tasks", "main.yml")) as f:
                return yaml.safe_load(f)
        for role, ca in (("aap_templates", "aap_templates_ca_path"), ("aap_portal_setup", "portal_setup_aap_ca_path")):
            first = tasks(role)[0]
            self.assertEqual(first["ansible.builtin.include_role"]["name"], "aap_setup_history")
            self.assertEqual(first["vars"]["_setup_ca_path"], "{{ %s }}" % ca)
            if role == "aap_templates":
                self.assertEqual(first["when"], "not aap_templates_describe_only | bool")
            else:
                self.assertNotIn("when", first)
        history = tasks("aap_setup_history")
        self.assertEqual(history[0]["site_aap_setup_history"]["ca_path"], "{{ _setup_ca_path }}")
        self.assertNotIn("when", history[0])
        self.assertEqual(history[1]["ansible.builtin.set_stats"]["data"]["aap_setup_fingerprints"], "{{ _setup_history.fingerprints }}")
        self.assertIn("ansible.builtin.fail", history[2])
        module = Mock(params={"job_id": 8, "template_id": 3, "ca_path": "example-ca.pem"})
        with patch.object(H, "AnsibleModule", return_value=module), patch.object(H, "Api") as ctor, patch.object(H, "connection", return_value={"verify": False}):
            ctor.return_value.probe.return_value = ""
            ctor.return_value.call.side_effect = [(200, {"results": [], "next": None}), (200, {"id": 8})]
            H.main()
            ctor.assert_called_once_with({"verify": False}, "example-ca.pem")


class DescribeOwnership(unittest.TestCase):
    def test_empty_portal_descriptions_in_a_nondefault_organization(self):
        with open(os.path.join(REPO, "roles", "aap_templates", "tasks", "main.yml")) as f:
            tasks = yaml.safe_load(f)
        args = next(t["site_aap_describe"] for block in tasks for t in block.get("block", []) if "site_aap_describe" in t)
        spec = {"templates": [{"name": "Example job"}], "workflows": [{"name": "Example workflow"}]}
        env = NativeEnvironment()
        for scorecard in ("", "Example scorecard"):
            managed = {kind: env.from_string(expr).render(_at_portal_spec=spec, aap_templates_organization="Example org",
                                                          portal_setup_scorecard_template=scorecard)
                       for kind, expr in args["managed"].items()}
            self.assertEqual(managed, {"job": ["Example job"] + ([scorecard] if scorecard else []), "workflow": ["Example workflow"]})
            for overwrite in (False, True):
                api = Mock()
                d = D.Describe(api, {"managed": managed, "workflows": [], "ours": [], "overwrite": overwrite}, False)
                for kind, names in managed.items():
                    for name in names:
                        d.put(kind, {"id": 7, "name": name, "description": ""}, name, "Example description")
                        self.assertEqual(d.results[-1]["action"], "left")
                api.call.assert_not_called()

    def test_contact_line_edits_stay_on_sync_managed_objects(self):
        name = "STIG - full scan and upload"
        text = "** Does: Scan\n** Contact: Owner=Example"
        for overwrite in (False, True):
            api = Mock()
            d = D.Describe(api, {"managed": {"job": ["Portal example"]}, "workflows": [{"name": name, "nodes": [{}]}],
                                 "ours": [], "overwrite": overwrite}, False)
            for kind, obj in (("job", "Portal example"), ("workflow", name)):
                d.put(kind, {"id": 7, "name": obj, "description": text + " edit"}, obj, text)
                self.assertEqual(d.results[-1]["action"], "left")
            api.call.assert_not_called()
        d.p["overwrite"] = False
        d.put("job", {"id": 9, "name": "Other", "description": text + " edit"}, "Other", text)
        self.assertEqual(d.results[-1]["action"], "kept")
        api.call.assert_not_called()


class AddGates(unittest.TestCase):
    """AAP - add workflow gates (part I): its table of checks is the doc's, the gate template it makes is the setup job's."""

    def test_gate_and_portal_create_the_same_description(self):
        gate = {"name": "Example gate", "playbook": "playbooks/workflow_gate.yml", "description": "Short line",
                "prompts": {"variables": True}}
        catalog = {"job": {gate["name"]: {"does": "Check the plan"}}, "owner": "Example team"}
        for describe, expected in ((catalog, "** Does: Check the plan\n** Contact: Owner=Example team"), ({}, "Short line")):
            api = Mock()
            api.call.return_value = (201, {"id": 7})
            gates = G.Gates(api, {"gate": gate, "checks": {}, "project": "Example", "inventory": "Example",
                                  "execution_environment": "", "describe": describe}, False)
            gates.all = Mock(return_value=[])
            gates.one = Mock(return_value={"id": 2})
            self.assertEqual(gates.gate_template(), 7)
            gate_body = api.call.call_args.args[2]
            setup = S.Setup(api, {"describe": describe, "credentials": {}, "overwrite": False}, False)
            setup.org_id = 1
            setup.by_name = Mock(return_value=[])
            setup.template(gate, {"project": 2, "inventories": {"default": 2}})
            self.assertEqual(api.call.call_args.args[2], gate_body)
            self.assertEqual(gate_body["description"], expected)

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
