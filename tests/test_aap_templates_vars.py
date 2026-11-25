#!/usr/bin/env python3
"""AAP - set up job templates: a catalog template's fixed Variables (1.83.1, P2-1). A template in AAP whose
fixed settings are missing or changed differs (never "already right"); bringing it right sets only those keys, the rest of
its Variables kept. tests/portal_setup/run_aap_templates_test.sh runs it against a stand-in AAP.
    python3 tests/test_aap_templates_vars.py"""
import json
import os
import sys
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "roles", "aap_templates", "library"))
sys.path.insert(0, os.path.join(REPO, "plugins", "module_utils"))
import ansible.module_utils  # noqa: E402
import site_aap  # noqa: E402
sys.modules.setdefault("ansible.module_utils.site_aap", site_aap)
ansible.module_utils.site_aap = site_aap
import site_aap_workflows  # noqa: E402 - 1.92.0: site_aap_templates takes NEVER_ALONE from it
sys.modules.setdefault("ansible.module_utils.site_aap_workflows", site_aap_workflows)
ansible.module_utils.site_aap_workflows = site_aap_workflows
import site_aap_templates as T  # noqa: E402

FIXED = {"check_stig_update_workflow": "true", "check_stig_update_plan_var": "stigman_update_plan_windows"}


class ReadVariables(unittest.TestCase):
    def test_yaml_top_level_keys_only(self):
        text = ('---\ncheck_stig_update_workflow: "true"\n# check_stig_update_plan_var: stigman_update_plan\nsite_list:\n'
                '  - a\nother: 5  # a note\nquoted: \'x y\'\n')
        got = T.vars_of(text)
        self.assertEqual(got["check_stig_update_workflow"], "true")
        self.assertNotIn("check_stig_update_plan_var", got)      # a comment is not a setting
        self.assertEqual(got["other"], "5")
        self.assertEqual(got["quoted"], "x y")

    def test_a_boolean_reads_as_the_catalog_writes_it(self):
        self.assertEqual(T.vars_of("check_stig_update_workflow: true\n")["check_stig_update_workflow"], "true")
        self.assertEqual(T.vars_of('{"check_stig_update_workflow": true}')["check_stig_update_workflow"], "true")

    def test_empty_and_none(self):
        self.assertEqual(T.vars_of(""), {})
        self.assertEqual(T.vars_of(None), {})


class SetVariables(unittest.TestCase):
    def check(self, text):
        out = T.set_vars(text, FIXED)
        d = yaml.safe_load(out) or {}
        for k, v in FIXED.items():
            self.assertEqual(d.get(k), v, out)
        return out, d

    def test_a_missing_key_added_the_person_settings_and_comments_kept(self):
        out, d = self.check('---\ncheck_stig_update_workflow: "true"\n# a person\'s note\nsite_note: mine\n')
        self.assertEqual(d["site_note"], "mine")
        self.assertIn("# a person's note", out)
        self.assertEqual(out.count("check_stig_update_workflow"), 1)

    def test_a_changed_key_replaced_once_with_its_block(self):
        out, d = self.check("---\ncheck_stig_update_plan_var:\n  - x\n  - y\nz: 1\ncheck_stig_update_workflow: false\n")
        self.assertEqual(d["z"], 1)
        self.assertNotIn("- x", out)

    def test_json_stays_json(self):
        out, d = self.check('{"a": 1, "check_stig_update_workflow": "false"}')
        self.assertEqual(json.loads(out)["a"], 1)

    def test_empty(self):
        out, d = self.check("")
        self.assertTrue(out.startswith("---\n"))

    def test_read_back_is_right(self):
        out = T.set_vars("x: 1\n", FIXED)
        have = T.vars_of(out)
        self.assertEqual([k for k in sorted(FIXED) if have.get(k) != T._flat(FIXED[k])], [])



class Areas(unittest.TestCase):
    """One section per platform: the rename skip keeps Network = the Network EE only; an old catalog area's label is named, never taken off."""

    def test_the_rename_skip_is_the_network_ee_only(self):
        import site_aap_rename as R
        for f in (T.areas_of, R.areas_of):
            self.assertEqual(f({"area": "Network", "ee": "all"}), set())                     # Network map, SolarWinds, Cisco UCS: not skipped
            self.assertEqual(f({"area": "Network", "ee": "network"}), {"network"})
            self.assertEqual(f({"area": "VMware", "ee": "all"}), {"vmware"})
            self.assertEqual(f({"area": "Tickets & incidents", "ee": "network"}), {"tickets & incidents", "network"})
            # skip_areas: the area before one section per platform still counts (a site's [Reports] keeps Log analysis out)
            self.assertEqual(f({"area": "Linux", "skip_areas": ["Reports", "Linux"], "ee": "all"}), {"reports", "linux"})
            self.assertEqual(f({"area": "Network", "skip_areas": ["Reports", "Network"], "ee": "all"}), {"reports"})

    def test_the_catalog_keeps_the_old_areas(self):
        import site_aap_rename as R
        with open(os.path.join(REPO, "aap", "job_templates.yml")) as fh:
            cat = dict((e["name"], e) for e in yaml.safe_load(fh)["job_templates"])
        self.assertEqual(cat["Log analysis"]["skip_areas"], ["Reports", "Linux"])
        self.assertIn("reports", T.areas_of(cat["Log analysis"]))
        self.assertIn("reports", R.areas_of(cat["Log analysis"]))
        self.assertIn("disk & services", T.areas_of(cat["Disk - clean up one mount"]))
        self.assertIn("vmware", T.areas_of(cat["Horizon - health report"]))
        self.assertEqual(T.areas_of(cat["Network map"]), {"reports"})                           # Network: only the Network EE
        self.assertNotIn("skip_areas", cat["Health check"])                                    # the same area: none

    def test_a_stale_label_is_never_an_already_right_row(self):
        """A run stopped before a template was looked at: its stale label is said on its own, never as a row ('already right')."""
        s = T.Templates.__new__(T.Templates)
        s.results, s.left_off = [], {}
        s.stale_labels = {"Health check": ["stale portal-area label portal-area:Reports on Health check - remove it by hand"]}

        def boom():
            raise RuntimeError("AAP refused a write")
        s.run_all = boom
        with self.assertRaises(RuntimeError):
            s.run()
        self.assertEqual(s.results, [])
        src = open(os.path.join(REPO, "roles", "aap_templates", "library", "site_aap_templates.py")).read()
        self.assertIn("stale_labels=[x for _, lines in sorted(", src)

    def test_the_old_area_labels(self):
        have = {"portal", "portal-area:Reports", "portal-area:Linux", "portal-area:Custom", "portal-area:disk & services", "Reports"}
        self.assertEqual(T.stale_area_labels(have, "portal-area:", "Linux"), ["portal-area:Reports", "portal-area:disk & services"])
        self.assertEqual(T.stale_area_labels({"portal-area:Linux"}, "portal-area:", "Linux"), [])          # its area now: never named
        self.assertEqual(T.stale_area_labels({"portal-area:VMware"}, "portal-area:", "Horizon"), ["portal-area:VMware"])
        self.assertEqual(T.stale_area_labels({"portal-area:Horizon"}, "portal-area:", "VMware"), [])       # not an old catalog area
        self.assertEqual(T.stale_area_labels(have, "", "Linux"), [])
        self.assertEqual(T.stale_area_labels(have, "portal-area:", ""), [])
        src = open(os.path.join(REPO, "roles", "aap_templates", "library", "site_aap_templates.py")).read()
        self.assertIn("remove it by hand", src)
        with open(os.path.join(REPO, "roles", "aap_templates", "tasks", "main.yml")) as fh:
            self.assertIn("_at.stale_labels", fh.read())


class WindowsInventory(unittest.TestCase):
    """Run the role's planner and writer against an in-memory AAP."""
    def setUp(self):
        self.catalog = [{"name": "Restart", "playbook": "playbooks/win_restart_schedule.yml"},
                        {"name": "Report", "playbook": "playbooks/report.yml", "credentials": ["machine_windows"]},
                        {"name": "Linux", "playbook": "playbooks/health_check.yml", "optional_credentials": ["machine_windows"]}]
        self.params = dict(catalog=self.catalog, organization="Site", project="Project", inventory="Site hosts",
                           inventory_windows="Windows hosts", ee={}, credentials={"machine_windows": "Windows machine"},
                           label_portal="none", only=[], overwrite=False, create_alias=False)
        self.templates = []
        self.writes = []
        self.rows = {"organizations": [{"id": 1, "name": "Site"}, {"id": 2, "name": "Other"}],
                     "projects": [{"id": 3, "name": "Project", "organization": 1}],
                     "inventories": [{"id": 4, "name": "Site hosts", "organization": 1},
                                     {"id": 5, "name": "Windows hosts", "organization": 1},
                                     {"id": 6, "name": "Windows hosts", "organization": 2}],
                     "credentials": [{"id": 7, "name": "Windows machine", "credential_type": 1, "organization": 1}],
                     "job_templates": self.templates}

    def call(self, method, path, body=None):
        path = path.split("?")[0].strip("/")
        if method != "GET":
            self.writes.append((method, path, body))
            if path == "job_templates":
                row = dict(body, id=10 + len(self.templates))
                self.templates.append(row)
                return 201, row
            if method == "PATCH":
                next(t for t in self.templates if t["id"] == int(path.split("/")[1])).update(body)
            return 204, {}
        if path == "projects/3/playbooks":
            return 200, {"results": [e["playbook"] for e in self.catalog]}
        if path.endswith("/credentials"):
            return 200, {"results": self.rows["credentials"]}
        return 200, {"results": self.rows[path]}

    def setup_templates(self, check=False, **params):
        role = T.Templates(self, dict(self.params, **params), check)
        role.run()
        self.assertEqual(role.problems, [])
        self.warnings = role.warnings
        return role.results

    def test_create_and_organization(self):
        self.setup_templates(inventory_windows_organization="Other")
        self.assertEqual([r["inventory"] for r in self.templates], [6, 6, 4])

    def test_portal_access_uses_the_windows_inventory_in_its_own_organization(self):
        user = {"id": 20, "username": "svc_portal"}
        self.rows.update({"users": [user], "inventories/5/access_list": [user], "inventories/6/access_list": []})
        params = dict(portal_user="svc_portal", inventory_windows_organization="Other")
        for check in (True, False):
            self.setup_templates(check=check, **params)
            self.assertEqual(self.warnings, ["Windows templates: the portal's account svc_portal cannot read inventory "
                                             "Windows hosts - their cards stay hidden; give it Read on that inventory"])
            if check:
                self.assertEqual(self.writes, [])
        self.writes.clear()
        self.rows["inventories/6/access_list"] = [user]
        self.setup_templates(**params)
        self.assertEqual(self.writes, [])
        self.assertEqual(self.warnings, [])

    def test_unknown_account_is_reported_once_without_blocking_setup(self):
        self.rows["users"] = []
        self.setup_templates(portal_user="unknown")
        self.assertEqual(len(self.templates), 3)
        self.assertEqual(len(self.warnings), 1)
        self.assertIn("not checked", self.warnings[0])
        self.setup_templates(portal_user="unknown", inventory_windows="")
        self.assertEqual(self.warnings, [])

    def test_check_run_and_repeat(self):
        self.setup_templates(inventory_windows="")
        self.templates[-1]["inventory"] = 6      # keep a Linux template's site-specific inventory
        self.writes.clear()
        rows = self.setup_templates(check=True)
        self.assertEqual(self.writes, [])
        self.assertEqual([r["inventory"] for r in self.templates], [4, 4, 6])
        self.assertEqual([r["inventory_change"] for r in rows if "inventory_change" in r],
                         ["would set inventory of Restart from Site hosts to Windows hosts",
                          "would set inventory of Report from Site hosts to Windows hosts"])
        self.setup_templates()
        self.assertEqual(self.writes, [("PATCH", "job_templates/10", {"inventory": 5}),
                                       ("PATCH", "job_templates/11", {"inventory": 5})])
        self.assertEqual([r["inventory"] for r in self.templates], [5, 5, 6])
        self.writes.clear()
        self.setup_templates()
        self.assertEqual(self.writes, [])

    def test_empty_setting_preserves_existing_inventory(self):
        self.setup_templates()
        self.writes.clear()
        self.setup_templates(inventory_windows="")
        self.assertEqual(self.writes, [])

    def test_a_linux_job_with_a_windows_catalog_name(self):
        self.setup_templates(inventory_windows="")
        self.templates[0]["playbook"] = "playbooks/health_check.yml"
        self.writes.clear()
        self.setup_templates(only=["Restart"])
        self.assertEqual(self.writes, [])

    def test_only_windows_can_be_created_without_general_inventory(self):
        self.setup_templates(only=["Restart"], inventory="")
        self.assertEqual(self.templates[0]["inventory"], 5)


if __name__ == "__main__":
    unittest.main()
