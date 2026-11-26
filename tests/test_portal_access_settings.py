#!/usr/bin/env python3
"""Run the deploy's access preflight and settings mapping with inventory overrides."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROLE = Path(__file__).resolve().parents[1] / "roles/site_portal_stack"
PLAYBOOK = os.environ.get("ANSIBLE_PLAYBOOK") or shutil.which("ansible-playbook")
ALIASES = {
    "portal_run_groups": "portal_requester_groups",
    "portal_runners": "portal_requesters",
    "portal_approve_groups": "portal_approver_groups",
    "portal_view_groups": "portal_reader_groups",
    "portal_viewers": "portal_readers",
}


@unittest.skipUnless(PLAYBOOK, "no ansible-playbook")
class DeployAccess(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        role = self.root / "roles/probe"
        (role / "tasks").mkdir(parents=True)
        (role / "defaults").symlink_to(ROLE / "defaults")
        tasks = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
        preflight = next(t for t in tasks if t["name"] == "Preflight | check renamed access lists")
        card_rules = next(t for t in tasks if t["name"] == "Preflight | resolve AD card matches")
        lists = next(t for t in tasks if t["name"] == "Settings | check the access lists")
        settings = next(t["vars"]["_settings"] for t in tasks if "_settings" in t.get("vars", {}))
        env = {old.upper(): settings[old.upper()] for old in ALIASES.values()}
        probe = [preflight, card_rules, lists, {"when": "expected is defined", "ansible.builtin.assert": {"that": "container_settings == expected"},
                             "vars": {"container_settings": env}},
                 {"when": "expected_rules is defined", "ansible.builtin.assert": {"that": "container_rules == expected_rules"},
                  "vars": {"container_rules": settings["PORTAL_AD_CARD_MATCHES"]}}]
        (role / "tasks/main.yml").write_text(yaml.safe_dump(probe))
        (self.root / "probe.yml").write_text("- hosts: all\n  gather_facts: false\n  roles: [probe]\n")

    def deploy(self, hosts):
        (self.root / "hosts.yml").write_text(yaml.safe_dump({"all": {"hosts": hosts}}))
        return subprocess.run([PLAYBOOK, "-i", "hosts.yml", "probe.yml"], cwd=self.root, capture_output=True,
                              text=True, env=dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCAL_TEMP=str(self.root / "tmp")))

    def test_defaults_old_new_and_equal(self):
        new = {key: ["Example " + key] for key in ALIASES}
        old = {alias: new[key] for key, alias in ALIASES.items()}
        expected = {alias.upper(): value for alias, value in old.items()}
        run = self.deploy({"defaults": {"expected": dict.fromkeys(expected, [])},
                           "old": dict(old, expected=expected), "new": dict(new, expected=expected),
                           "equal": dict(old, **new, expected=expected)})
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_card_rules_defaults_and_overrides(self):
        defaults = [{"attribute": "userPrincipalName", "match": "{edipi}@mil"},
                    {"attribute": "sAMAccountName", "match": "{edipi}.ctr"},
                    {"attribute": "sAMAccountName", "match": "{edipi}.civ"},
                    {"attribute": "sAMAccountName", "match": "{edipi}.mil"},
                    {"attribute": "userPrincipalName", "match": "{edipi}{digits6}@mil"}]
        custom = [{"attribute": "sAMAccountName", "match": "{edipi}.ctr"}]
        run = self.deploy({
            "defaults": {"expected_rules": defaults},
            "pattern_only": {"portal_ad_card_match": "{edipi}{digits6}@mil", "expected_rules": [defaults[4]]},
            "legacy": {"portal_ad_card_attribute": "sAMAccountName", "portal_ad_card_match": "{edipi}.ctr", "expected_rules": custom},
            "custom": {"portal_ad_card_matches": custom, "expected_rules": custom},
            "empty": {"portal_ad_card_matches": [], "expected_rules": []},
            # Codex 1.133.0 #2: either 1.132.0 setting alone is ONE rule, the other at its 1.132.0 default - never the five
            "match_only_default": {"portal_ad_card_match": "{edipi}@mil", "expected_rules": [defaults[0]]},
            "attribute_only_default": {"portal_ad_card_attribute": "userPrincipalName", "expected_rules": [defaults[0], defaults[4]]},
            "attribute_only_sam": {"portal_ad_card_attribute": "sAMAccountName", "portal_ad_card_match": "{edipi}.civ",
                                   "expected_rules": [defaults[2]]},
            "both_defaults": {"portal_ad_card_attribute": "userPrincipalName", "portal_ad_card_match": "{edipi}@mil",
                              "expected_rules": [defaults[0]]},
        })
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_codex_1330s_2_a_132_setting_with_the_list_stops_deploy(self):
        run = self.deploy({"legacy_defaults": {"portal_ad_card_attribute": "userPrincipalName", "portal_ad_card_match": "{edipi}@mil",
                                               "portal_ad_card_matches": [{"attribute": "sAMAccountName", "match": "{edipi}.ctr"}]},
                           "match_default": {"portal_ad_card_match": "{edipi}@mil", "portal_ad_card_matches": []}})
        self.assertNotEqual(run.returncode, 0)
        for host in ("legacy_defaults", "match_default"):
            self.assertRegex(run.stdout, rf"fatal: \[{host}\].*portal_ad_card_matches.*portal_ad_card_attribute or portal_ad_card_match")

    def test_codex_1330s_r2_a_blank_132_card_setting_stops_deploy(self):
        run = self.deploy({"match_false": {"portal_ad_card_match": False}, "match_blank": {"portal_ad_card_match": " "},
                           "attr_null": {"portal_ad_card_attribute": None}, "attr_empty": {"portal_ad_card_attribute": ""},
                           "ok": {"portal_ad_card_match": "{edipi}@mil"}})
        self.assertNotEqual(run.returncode, 0)
        for host in ("match_false", "match_blank", "attr_null", "attr_empty"):
            self.assertRegex(run.stdout, rf"fatal: \[{host}\].*is set but empty \(false, null or blank\)\. Remove the line")
        self.assertNotRegex(run.stdout, r"(failed|fatal): \[ok\]")

    def test_codex_1330s_16_false_or_null_in_the_wall_lists_stops_deploy(self):
        tasks = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
        that = next(t for t in tasks if "portal_wall_feedback_groups is sequence and portal_wall_feedback_groups is not string"
                    in (t.get("ansible.builtin.assert") or {}).get("that", []))["ansible.builtin.assert"]["that"]
        self.assertIn("portal_wall_feedback_groups | reject('string') | list | length == 0", that)
        self.assertIn("portal_wall_feedback_readers | reject('string') | list | length == 0", that)

    def test_conflicting_card_rules_and_unsafe_patterns_stop_deploy(self):
        run = self.deploy({
            "old_attribute": {"portal_ad_card_attribute": "sAMAccountName", "portal_ad_card_matches": []},
            "old_pattern": {"portal_ad_card_match": "{cn}", "portal_ad_card_matches": [{"attribute": "sAMAccountName", "match": "{cn}"}]},
            "unsafe": {"portal_ad_card_matches": [{"attribute": "sAMAccountName", "match": "{edipi}"},
                                                  {"attribute": "userPrincipalName", "match": "*@mil"}]},
            "unsafe_legacy": {"portal_ad_card_match": "*@mil"},
        })
        self.assertNotEqual(run.returncode, 0)
        for host in ("old_attribute", "old_pattern"):
            self.assertRegex(run.stdout, rf"fatal: \[{host}\].*portal_ad_card_matches.*portal_ad_card_attribute or portal_ad_card_match")
        for host in ("unsafe", "unsafe_legacy"):
            self.assertRegex(run.stdout, rf"failed: \[{host}\].*containing \{{edipi\}} or \{{cn\}}")

    def test_neon_r132_3_wildcard_after_the_person_stops_deploy(self):
        run = self.deploy({
            "edipi_star": {"portal_ad_card_matches": [{"attribute": "userPrincipalName", "match": "{edipi}*@mil"}]},
            "cn_q": {"portal_ad_card_matches": [{"attribute": "sAMAccountName", "match": "{cn}?"}]},
            "legacy_star": {"portal_ad_card_match": "{edipi}*@mil"},
            "alt": {"portal_ad_card_matches": [{"attribute": "altSecurityIdentities", "match": "<S>CN={cn}"}]},
            "legacy_alt": {"portal_ad_card_attribute": "altSecurityIdentities", "portal_ad_card_match": "{cn}"},
            "employee_id": {"portal_ad_card_matches": [{"attribute": "employee ID", "match": "{edipi}"}]},
            "before": {"portal_ad_card_matches": [{"attribute": "userPrincipalName", "match": "*{edipi}@mil"}]},
            "piv_star": {"portal_ad_card_matches": [{"attribute": "userPrincipalName", "match": "{edipi}{digits6}*@mil"}]},
            "dot_star": {"portal_ad_card_matches": [{"attribute": "userPrincipalName", "match": "{edipi}.*@mil"}]},    # Codex r2 #3
            "piv": {"portal_ad_card_matches": [{"attribute": "userPrincipalName", "match": "{edipi}{digits6}@mil"}],
                    "expected_rules": [{"attribute": "userPrincipalName", "match": "{edipi}{digits6}@mil"}]},
            "subset": {"portal_ad_card_matches": [{"attribute": "samaccountname", "match": "{edipi}.CIV"}],
                       "expected_rules": [{"attribute": "samaccountname", "match": "{edipi}.CIV"}]},
            "not_a_form": {"portal_ad_card_matches": [{"attribute": "userPrincipalName", "match": "x.{edipi}@mil"}]},
            "adm": {"portal_ad_card_matches": [{"attribute": "sAMAccountName", "match": "{edipi}.adm"}]},     # Codex r3 #1
            "sam_only": {"portal_ad_card_attribute": "sAMAccountName"},
        })
        self.assertNotEqual(run.returncode, 0)
        for host in ("edipi_star", "cn_q", "legacy_star", "before", "piv_star", "dot_star"):
            self.assertRegex(run.stdout, rf"failed: \[{host}\].*would match other accounts of the same person or other people - list the exact forms \(for example userPrincipalName \{{edipi\}}@mil, sAMAccountName \{{edipi\}}.ctr\)")
        for host in ("alt", "legacy_alt", "employee_id", "not_a_form", "adm", "sam_only"):     # the five forms only
            self.assertRegex(run.stdout, rf"failed: \[{host}\].*is not a card\s+form")
        self.assertNotRegex(run.stdout, r"(failed|fatal): \[(subset|piv)\]")

    def test_codex_r132_5_a_blank_entry_stops_deploy(self):
        run = self.deploy({"runners": {"portal_runners": [" "]}, "requesters": {"portal_requesters": [""]},
                           "run_groups": {"portal_run_groups": ["Ops", "  "]}, "approvers": {"portal_approvers": [" "]},
                           "ok": {"portal_runners": ["DOE.JANE.Q.1234567890"]}})
        self.assertNotEqual(run.returncode, 0)
        for host, setting in (("runners", "portal_runners"), ("requesters", "portal_runners"), ("run_groups", "portal_run_groups"),
                              ("approvers", "portal_approvers")):
            self.assertRegex(run.stdout, rf"failed: \[{host}\].*{setting} must be a list of nonblank strings")
        self.assertNotRegex(run.stdout, r"(failed|fatal): \[ok\]")

    def test_codex_r2_12_a_false_group_list_stops_deploy(self):
        run = self.deploy({"run_false": {"portal_run_groups": False}, "old_false": {"portal_requester_groups": False},
                           "approve_false": {"portal_approve_groups": False}, "run_null": {"portal_run_groups": None},
                           "runners_null": {"portal_runners": None}, "approvers_null": {"portal_approvers": None},
                           "list_ok": {"portal_run_groups": ["Ops"]}})
        self.assertNotEqual(run.returncode, 0)
        for host, setting in (("run_false", "portal_run_groups"), ("old_false", "portal_run_groups"), ("approve_false", "portal_approve_groups"),
                              ("run_null", "portal_run_groups"), ("runners_null", "portal_runners"), ("approvers_null", "portal_approvers")):
            self.assertRegex(run.stdout, rf"failed: \[{host}\].*{setting} must be a list of nonblank strings")
        self.assertNotRegex(run.stdout, r"(failed|fatal): \[list_ok\]")

    def test_different_lists_fail_even_with_an_explicit_empty_list(self):
        hosts = {key: {key: ["Other group"], alias: ["Example group"]} for key, alias in ALIASES.items()}
        hosts["explicit_empty"] = {"portal_runners": [], "portal_requesters": ["Example card"]}
        run = self.deploy(hosts)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("explicit_empty", run.stdout)
        # the failed host lines (ansible-core 2.19+ also prints the first one as an [ERROR] line)
        self.assertEqual(len([ln for ln in run.stdout.splitlines() if ln.startswith("failed: [")
                              and "portal_runners and portal_requesters are both set and differ" in ln]), 2, run.stdout)
        for key, alias in ALIASES.items():
            with self.subTest(key=key):
                self.assertIn(f"{key} and {alias} are both set and differ", run.stdout, run.stderr)


if __name__ == "__main__":
    unittest.main()
