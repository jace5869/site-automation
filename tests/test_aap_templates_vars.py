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
            self.assertIn("r.stale | default([])", fh.read())


if __name__ == "__main__":
    unittest.main()
