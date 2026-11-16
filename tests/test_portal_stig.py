#!/usr/bin/env python3
"""The site portal's STIG actions (1.67.0, workflows 46-48): which rules ACT may propose a fix for (stig_fix_gate: CAT II
and III only, never what portal_stig_fix_never names, never a fix that restarts the system), a second look at ACT's
proposed commands (stig_fix_commands_never), ACT's task, fixed / still there / not checked after the recheck
(stig_fix_result), one rule's text from STIG Manager (site_stigman_rule), and the recheck of one rule alone
(stig_only_rules, check_stig_recheck_rules).
  python3 tests/test_portal_stig.py"""
import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
import stig_filters as sf  # noqa: E402
import stig_fix_filters as ff  # noqa: E402
import yaml  # noqa: E402

TMOUT = {"cat": "CAT II", "title": "RHEL 9 must automatically exit interactive sessions after 10 minutes of inactivity.",
         "check": "$ sudo grep -i tmout /etc/profile /etc/profile.d/*.sh", "fix": "Add to /etc/profile.d/tmout.sh:\n\nTMOUT=600\nreadonly TMOUT"}


class Gate(unittest.TestCase):
    def test_a_cat_ii_rule_with_sudo_in_its_commands(self):
        self.assertEqual(ff.stig_fix_gate(TMOUT), {"ok": True, "cat": "II", "why": ""})
        self.assertTrue(ff.stig_fix_gate(dict(TMOUT, cat="CAT III", fix="$ sudo dnf -y install tmux"))["ok"])
        self.assertTrue(ff.stig_fix_gate({"severity": "medium", "title": "x"})["ok"])          # severity medium = CAT II

    def test_cat_i_and_unknown(self):
        self.assertEqual(ff.stig_fix_gate(dict(TMOUT, cat="CAT I"))["why"],
                         "it is CAT I: only CAT II and III rules are fixed this way (portal_stig_fix_cats)")
        self.assertIn("its category is not known", ff.stig_fix_gate({"title": "x"})["why"])
        self.assertFalse(ff.stig_fix_gate(TMOUT, {"cats": ["CAT III"]})["ok"])

    def test_never_touch(self):
        cases = {
            "accounts": "RHEL 9 must disable account identifiers after 35 days of inactivity.",
            "authentication": "RHEL 9 must require authentication to access single-user mode.",
            "PAM": "RHEL 9 must configure the use of the pam_faillock.so module in /etc/pam.d/system-auth.",
            "sudo": "RHEL 9 must require re-entry of credentials: remove NOPASSWD from /etc/sudoers.",
            "SSH authentication": "The RHEL 9 SSH daemon must be configured with PermitRootLogin no.",
            "crypto and FIPS": "RHEL 9 must enable FIPS mode.",
            "audit configuration": "RHEL 9 must audit all uses of privileged commands (/etc/audit/rules.d).",
            "SELinux mode": "RHEL 9 must use a Linux Security Module configured to enforce limits: SELinux mode enforcing.",
            "firewall default policy": "RHEL 9 must configure a firewall to deny by default (default zone drop).",
        }
        for name, title in cases.items():
            g = ff.stig_fix_gate(dict(TMOUT, title=title))
            self.assertFalse(g["ok"], name)
            self.assertIn("it is about %s" % name, g["why"], (name, g))
        self.assertEqual(ff.stig_fix_gate(dict(TMOUT, fix="Edit the file, then reboot the system."))["why"],
                         "its fix text says to restart the system, which a person does by hand")

    def test_the_commands_too(self):
        self.assertEqual(ff.stig_fix_commands_never(["printf 'TMOUT=600\\n' > /etc/profile.d/tmout.sh"]), "")
        self.assertIn("PAM", ff.stig_fix_commands_never(["sed -i 's/x/y/' /etc/pam.d/system-auth"]))
        self.assertIn("restarts the system", ff.stig_fix_commands_never(["systemctl reboot"]))

    def test_the_defaults_are_the_filters(self):
        d = yaml.safe_load(open(os.path.join(REPO, "roles", "portal_stig", "defaults", "main.yml")))
        self.assertEqual(d["portal_stig_fix_never"], ff.NEVER)
        self.assertEqual(d["portal_stig_fix_cats"], ["CAT II", "CAT III"])
        self.assertEqual(ff.stig_fix_gate(dict(TMOUT, title="FIPS"), {"never": d["portal_stig_fix_never"]})["ok"], False)


class Task(unittest.TestCase):
    def test_the_task(self):
        t = ff.stig_fix_task(dict(TMOUT, id="SV-257883r1044925_rule", version="RHEL-09-412035", cat="II"), {"host": "web01.example.mil"})
        self.assertIn("Rule SV-257883r1044925_rule (RHEL-09-412035), CAT II", t)
        self.assertIn("TMOUT=600", t)
        self.assertIn("Propose the EXACT commands", t)
        self.assertIn("the read-only command that shows the rule passes", t)
        self.assertIn("propose no command and say why", t)


FIND = {"id": "stig_rule_fail:web01.example.mil:SV-257883r1044925_rule", "kind": "stig_rule_fail", "host": "web01.example.mil",
        "data": {"rule": "SV-257883r1044925_rule", "stigman": "Open"}}


class Result(unittest.TestCase):
    def test_fixed_still_there_not_checked(self):
        r = ff.stig_fix_result([{"fixed": [{"rule_id": "SV-257883r1044925_rule", "v": "V-257883"}]}], FIND)
        self.assertEqual((r["fixed"], r["before"], r["after"]), (True, "Open", "passes now"))
        self.assertEqual(ff.stig_fix_result([{"now_na": [{"v": "V-257883"}]}], FIND)["after"], "not applicable now")
        r = ff.stig_fix_result([{"still_open": [{"rule_id": "SV-257883r1044925_rule"}]}], FIND)
        self.assertEqual((r["fixed"], r["after"]), (False, "fails"))
        self.assertEqual(ff.stig_fix_result([{"answered": [{"v": "V-257883", "now": "fail"}]}], FIND)["fixed"], False)
        self.assertEqual(ff.stig_fix_result([{"answered": [{"v": "V-257883", "now": "pass"}]}], FIND)["fixed"], True)
        r = ff.stig_fix_result([{"fixed": [{"v": "V-1"}], "why": ""}], FIND)
        self.assertEqual(r["fixed"], "unknown")
        self.assertIn("does not have this rule Open or Not Reviewed", r["summary"])

    def test_a_new_asset(self):
        f = {"kind": "stig_no_asset", "data": {"collection": "RHEL Servers", "asset": "web01"}}
        r = ff.stig_fix_result([{"create": {"name": "web01", "collection": "RHEL Servers"}}], f)
        self.assertEqual((r["fixed"], r["after"]), ("unknown", "planned"))
        self.assertEqual(ff.stig_fix_result([{"why": "web01 is in Lab"}], f)["summary"], "Not planned: web01 is in Lab.")


class OneRule(unittest.TestCase):
    def test_only_that_rule(self):
        self.assertEqual(sf.stig_only_rules(["V-257779", "V-257883", "V-258176"], ["SV-257883r1044925_rule"]), ["V-257883"])
        self.assertEqual(sf.stig_only_rules(["V-257779"], []), ["V-257779"])
        self.assertEqual(sf.stig_only_rules(["V-257779"], ["V-1"]), [])
        d = open(os.path.join(REPO, "roles", "check_stig", "defaults", "main.yml")).read()
        self.assertIn("check_stig_recheck_rules: []", d)
        self.assertIn("stig_only_rules(check_stig_recheck_rules)", open(os.path.join(REPO, "roles", "check_stig", "tasks", "recheck_read.yml")).read())


def _module():
    import ansible.module_utils.basic  # noqa: F401 - the module's imports
    spec = importlib.util.spec_from_file_location("site_stigman_rule", os.path.join(REPO, "roles", "portal_stig", "library", "site_stigman_rule.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RuleText(unittest.TestCase):
    def test_stig_managers_answer(self):
        m = _module()
        r = m.rule_of({"ruleId": "SV-1r2_rule", "version": "RHEL-09-1", "title": "T", "severity": "medium",
                       "detail": {"vulnDiscussion": "why"}, "checks": [{"checkId": "C", "content": "check it"}],
                       "fixes": [{"fixId": "F", "text": "fix it"}, {"fixId": "G", "text": "and this"}]}, "SV-1r2_rule")
        self.assertEqual(r, {"id": "SV-1r2_rule", "version": "RHEL-09-1", "title": "T", "severity": "medium", "check": "check it",
                             "fix": "fix it\n\nand this", "discussion": "why"})
        self.assertEqual(m.rule_of({}, "SV-9")["fix"], "")
        self.assertTrue(m.rule_of({"fixes": [{"text": "x" * 7000}]}, "SV-9")["fix"].endswith("[... cut]"))


if __name__ == "__main__":
    unittest.main()
