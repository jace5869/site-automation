#!/usr/bin/env python3
"""Unit tests for the fapolicyd rules job (playbooks/fapolicyd_rules.yml): a block told apart from an audited allow
(plugins/module_utils/site_fapolicyd.py), the clean-up each review finding becomes and the file's new name
(roles/fapolicyd_rules/library/site_fapolicyd_rules.py), the plan from the approved lines, the rules file edit, the way
back and the report (plugins/filter/fapolicyd_filters.py).
    python3 tests/test_fapolicyd_rules.py"""
import importlib.util
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("ansible.module_utils.site_fapolicyd",
                                               os.path.join(HERE, "..", "plugins", "module_utils", "site_fapolicyd.py"))
sf = importlib.util.module_from_spec(_spec)
sys.modules["ansible.module_utils.site_fapolicyd"] = sf
_spec.loader.exec_module(sf)
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
sys.path.insert(0, os.path.join(HERE, "..", "roles", "fapolicyd_rules", "library"))
import fapolicyd_filters as ff  # noqa: E402
import site_fapolicyd_rules as fr  # noqa: E402

RULES = {"5": "deny_audit perm=any pattern=ld_so : all", "13": "deny_audit perm=execute all : all",
         "20": "allow_audit perm=open all : dir=/srv/audited/"}


def ev(resp, fan_info="13", fan_type="rule_info"):
    return {"fan": {"resp": resp, "fan_type": fan_type, "fan_info": fan_info}}


class BlockTest(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(sf.block_kind(ev("deny"), RULES, False), "denied")
        self.assertEqual(sf.block_kind(ev("2"), RULES, False), "denied")                        # raw, not ausearch -i
        # permissive: fapolicyd answers allow and keeps the audit bit - a deny rule's number makes it a would-be block
        self.assertEqual(sf.block_kind(ev("allow", "13"), RULES, True), "would deny")
        self.assertEqual(sf.block_kind(ev("1", "13"), RULES, True), "would deny")
        self.assertEqual(sf.block_kind(ev("allow", "20"), RULES, True), "")                     # an allow_audit rule's allow
        self.assertEqual(sf.block_kind(ev("allow", "20"), RULES, False), "")
        # no rule info (a RHEL 8 kernel): any audited allow while permissive, none while enforcing
        self.assertEqual(sf.block_kind(ev("allow", "", ""), RULES, True), "would deny")
        self.assertEqual(sf.block_kind(ev("allow", "", ""), RULES, False), "")
        # a number the rules no longer have (they changed since): as without rule info
        self.assertEqual(sf.block_kind(ev("allow", "99"), RULES, True), "would deny")

    def test_rule_number_hex_or_decimal(self):
        # the kernel writes fan_info in hex; ausearch -i of audit 3.1+ writes it in decimal (fan_type=rule_info), older leaves both
        self.assertEqual(sf.fan_rule({"fan_type": "rule_info", "fan_info": "16"}), "16")
        self.assertEqual(sf.fan_rule({"fan_type": "1", "fan_info": "10"}), "16")
        self.assertEqual(sf.fan_rule({"fan_type": "0", "fan_info": "0"}), "")
        self.assertEqual(sf.block_kind({"fan": {"resp": "allow", "fan_type": "1", "fan_info": "D"}}, RULES, True), "would deny")   # 0xD = 13

    def test_rule_list(self):
        out = "-> %languages=application/x-bytecode.python\n1. allow perm=any uid=0 : dir=/var/tmp/\n13. deny_audit perm=execute all : all\n"
        self.assertEqual(sf.rule_list(out), {"1": "allow perm=any uid=0 : dir=/var/tmp/", "13": "deny_audit perm=execute all : all"})

    def test_event_time(self):
        self.assertIsNotNone(fr.event_epoch("10/30/2026 08:00:01"))
        self.assertIsNone(fr.event_epoch("yesterday"))


class CleanupTest(unittest.TestCase):
    def test_what_each_finding_becomes(self):
        files = [("50-local.rules", "allow perm=open all : dir=/srv/data/\nallow perm=open all : dir=/srv/data/\n"
                                    "allow perm=execute all : dir=/home/*/bin/\nallow perm=execute all : path=/usr/bin/ls\n"
                                    "allow perm=any all : dir=/usr/lib/\nallow perm=any all : all\nallow perm=execute all : dir=/home/\n"
                                    "allow perm=any exe=/usr/bin/java : dir=/usr/lib/\n")]
        out = sf.review_rules(files, lambda p: "coreutils-8.32" if p == "/usr/bin/ls" else "")
        got = dict(((f["line"], f["rule"]), fr.cleanup_of(f)) for f in out)
        self.assertEqual(got[(2, "allow perm=open all : dir=/srv/data/")], ("remove", fr.SAME))           # the later repeat
        self.assertEqual(got[(3, "allow perm=execute all : dir=/home/*/bin/")], ("remove", fr.SAME))      # matches nothing
        self.assertEqual(got[(4, "allow perm=execute all : path=/usr/bin/ls")], ("remove", fr.SAME))      # RPM-owned
        # a system folder for any program: its text names RPM-owned files too - it blocks more, not the same
        self.assertEqual(got[(5, "allow perm=any all : dir=/usr/lib/")], ("remove", fr.STRICTER))
        self.assertEqual(got[(6, "allow perm=any all : all")], ("remove", fr.STRICTER))
        self.assertEqual(got[(7, "allow perm=execute all : dir=/home/")], ("remove", fr.STRICTER))
        self.assertIsNone(got[(8, "allow perm=any exe=/usr/bin/java : dir=/usr/lib/")])                   # a person decides
        self.assertEqual(got[(0, "")], ("rename", fr.LOOSER))                                              # sorts after 41

    def test_rename_with_deny_rules_blocks_more(self):
        d = tempfile.mkdtemp()
        try:
            for x in ("rules.d", "trust.d"):
                os.mkdir(os.path.join(d, x))
            with open(os.path.join(d, "rules.d", "50-mixed.rules"), "w") as f:
                f.write("allow perm=open all : dir=/srv/x/\ndeny_audit perm=execute all : dir=/srv/y/\n")
            with open(os.path.join(d, "rules.d", "51-allow.rules"), "w") as f:
                f.write("allow perm=open all : dir=/srv/z/\n")
            got = dict((c["file"], (c["effect"], c["stricter"])) for c in self.read_changes(d) if c["kind"] == "rename")
            self.assertEqual(got["50-mixed.rules"], (fr.RENAMED_DENY, True))
            self.assertEqual(got["51-allow.rules"], (fr.LOOSER, False))
        finally:
            import shutil
            shutil.rmtree(d)

    @staticmethod
    def read_changes(d):
        class M(object):
            params = {"since": "today", "dir": d, "own_rules": "36-site-approved.rules", "own_trust": "site-approved", "never_edit": [],
                      "labels": "", "programs": [], "paths": [], "user_dirs": ["/home/"], "hash_max_mb": 1, "max_groups": 10, "timeout": 5}

            @staticmethod
            def get_bin_path(name):
                return None
        with open(os.path.join(d, "fapolicyd.conf"), "w") as f:
            f.write("permissive = 0\n")
        saved = fr.run
        fr.run = lambda args, t: ((0, "fapolicyd-1.4.5", "") if args[:2] == ["rpm", "-q"] and args[-1] == "fapolicyd" and len(args) == 3
                                  else (0, "active", "") if args[0] == "systemctl" else (1, "", "<no matches>"))
        try:
            return fr.Reader(M()).read()["changes"]
        finally:
            fr.run = saved

    def test_renamed(self):
        self.assertEqual(fr.renamed("99-local.rules", set()), "39-local.rules")
        self.assertEqual(fr.renamed("local.rules", set(["39-local.rules"])), "38-local.rules")

    def test_trusted_lines(self):
        with tempfile.TemporaryDirectory() as d:
            os.mkdir(os.path.join(d, "trust.d"))
            with open(os.path.join(d, "fapolicyd.trust"), "w") as f:
                f.write("# AUTOGENERATED FILE VERSION 3\n# /home/user/my-ls 1 ab\n/opt/a 10 aa\n")
            with open(os.path.join(d, "trust.d", "app"), "w") as f:
                f.write("/opt/b 20 bb\n")
            t = fr.trusted_lines(d)
        self.assertEqual((t["/opt/a"][:2], t["/opt/b"][:2]), (("10", "aa"), ("20", "bb")))
        self.assertNotIn("/home/user/my-ls", t)


SHA = "a" * 64


def host(**kw):
    h = {"state": "read", "error": "", "active": "active", "permissive": False, "version": "fapolicyd-1.4.5", "by_hand": [], "blocks": [],
         "own_rules": "/etc/fapolicyd/rules.d/36-site-approved.rules", "own_rules_exists": False,
         "own_trust": "/etc/fapolicyd/trust.d/site-approved", "own_trust_exists": True,
         "changes": [{"kind": "trust", "path": "/opt/app/tool", "size": 51, "sha256": SHA, "line": "trust file: /opt/app/tool size 51 sha256 " + SHA},
                     {"kind": "rule", "rule": "allow perm=any all : dir=/var/lib/containers/storage/overlay/",
                      "line": "allow rule: allow perm=any all : dir=/var/lib/containers/storage/overlay/"},
                     {"kind": "remove", "file": "50-local.rules", "rule": "allow perm=open all : dir=/srv/data/", "stricter": False,
                      "line": "remove rule: 50-local.rules: allow perm=open all : dir=/srv/data/", "effect": fr.SAME},
                     {"kind": "remove", "file": "50-local.rules", "rule": "allow perm=any all : dir=/usr/lib/", "stricter": True,
                      "line": "remove rule: 50-local.rules: allow perm=any all : dir=/usr/lib/", "effect": fr.STRICTER},
                     {"kind": "rename", "file": "50-local.rules", "to": "39-local.rules", "line": "rename rules file: 50-local.rules -> 39-local.rules"},
                     {"kind": "permissive", "from": "0", "to": "1", "line": "permissive: 0 -> 1"}]}
    h.update(kw)
    return h


class PlanTest(unittest.TestCase):
    def test_order_and_action(self):
        h = host()
        p = ff.fapolicyd_rules_job_plan(h, [c["line"] for c in h["changes"] if c["kind"] != "permissive"])
        self.assertEqual([c["kind"] for c in p["apply"]], ["trust", "rule", "remove", "rename"])
        self.assertEqual(p["action"], "rules")
        self.assertEqual(p["remove"], {"50-local.rules": ["allow perm=open all : dir=/srv/data/"]})
        self.assertEqual(p["refused"][0]["line"], "remove rule: 50-local.rules: allow perm=any all : dir=/usr/lib/")   # enforcing
        self.assertEqual(p["trust_lines"], ["/opt/app/tool 51 " + SHA])
        self.assertEqual(ff.fapolicyd_rules_job_plan(h, [h["changes"][0]["line"]])["action"], "update")
        self.assertEqual(ff.fapolicyd_rules_job_plan(h, [h["changes"][0]["line"], "permissive: 0 -> 1"])["action"], "restart")

    def test_stricter_not_with_enforcing_in_the_same_run(self):
        line = "remove rule: 50-local.rules: allow perm=any all : dir=/usr/lib/"
        h = host(permissive=True)
        h["changes"].append({"kind": "permissive", "from": "1", "to": "0", "line": "permissive: 1 -> 0"})
        p = ff.fapolicyd_rules_job_plan(h, [line, "permissive: 1 -> 0"])
        self.assertEqual(([c["kind"] for c in p["apply"]], p["refused"][0]["line"]), (["permissive"], line))
        self.assertIn("enforce in a later run", p["refused"][0]["why"])

    def test_stricter_while_permissive_or_asked(self):
        line = "remove rule: 50-local.rules: allow perm=any all : dir=/usr/lib/"
        self.assertEqual(len(ff.fapolicyd_rules_job_plan(host(permissive=True), [line])["apply"]), 1)
        self.assertEqual(len(ff.fapolicyd_rules_job_plan(host(), [line], True)["apply"]), 1)

    def test_bad_and_not_here(self):
        h = host()
        for line in ("rm -rf /", h["changes"][0]["line"] + " (web01)", "trust file: /opt/app/tool", "remove rule: 50-local.rules:"):
            self.assertEqual(len(ff.fapolicyd_rules_job_plan(h, [line])["bad"]), 1, line)
        p = ff.fapolicyd_rules_job_plan(h, ["trust file: /opt/app/tool size 52 sha256 " + SHA])      # the file changed since
        self.assertEqual((p["apply"], p["not_here"]), ([], ["trust file: /opt/app/tool size 52 sha256 " + SHA]))

    def test_edit(self):
        text = "# site\nallow  perm=open all : dir=/srv/data/\nallow perm=open all : dir=/srv/data/\nallow perm=any all : all\n"
        out = ff.fapolicyd_rules_job_edit(text, ["allow perm=open all : dir=/srv/data/"], "approved 2026-10-30")
        self.assertEqual(out.splitlines(), ["# site", "allow  perm=open all : dir=/srv/data/",
                                            "# removed by site-automation (fapolicyd rules job, approved 2026-10-30): allow perm=open all : dir=/srv/data/",
                                            "allow perm=any all : all"])

    def test_new_file(self):
        out = ff.fapolicyd_rules_job_file(["allow perm=open all : dir=/srv/a/"], ["allow perm=open all : dir=/srv/a/", "allow perm=any all : dir=/x/"])
        self.assertEqual([x for x in out.splitlines() if not x.startswith("#")], ["allow perm=open all : dir=/srv/a/", "allow perm=any all : dir=/x/"])

    def test_backups_and_way_back_agree(self):
        h = host(permissive=True)
        p = ff.fapolicyd_rules_job_plan(h, [c["line"] for c in h["changes"] if c["kind"] != "permissive"] + ["permissive: 0 -> 1"])
        names = [n for _, n in ff.fapolicyd_rules_job_backups(p, h)]
        self.assertEqual(names, ["own.trust", "rules.50-local.rules", "fapolicyd.conf"])     # own.rules did not exist
        back = ff.fapolicyd_rules_job_rollback(p, h, "/tmp/x")
        for n in names:
            self.assertIn("/tmp/x/" + n, back)
        self.assertIn("rm -f /etc/fapolicyd/rules.d/36-site-approved.rules || rc=1", back)    # it did not exist: taken out
        self.assertIn("mv -f /etc/fapolicyd/rules.d/39-local.rules /etc/fapolicyd/rules.d/50-local.rules", back)
        self.assertLess(back.index("mv -f"), back.index("cp -p /tmp/x/rules.50-local.rules"))   # renamed back before restored


class WorkflowTest(unittest.TestCase):
    def test_plan_per_host_and_held(self):
        enforcing = host()
        quiet = host(permissive=True, changes=[{"kind": "permissive", "from": "1", "to": "0", "line": "permissive: 1 -> 0"}])
        busy = host(permissive=True, blocks=[{"kind": "would deny", "count": 2}],
                    changes=[enforcing["changes"][0], {"kind": "permissive", "from": "1", "to": "0", "line": "permissive: 1 -> 0"}])
        w = ff.fapolicyd_rules_job_workflow([{"_fr": enforcing}, {"_fr": quiet}, {"_fr": busy}, {}], ["a", "q", "b", "gone"])
        self.assertEqual(w["plan"]["a"], [c["line"] for c in enforcing["changes"] if not c.get("stricter") and c["kind"] != "permissive"])
        self.assertEqual(w["plan"]["q"], ["permissive: 1 -> 0"])                 # a quiet day: enforcing proposed
        self.assertEqual(w["plan"]["b"], [enforcing["changes"][0]["line"]])      # would-be blocks: their change first
        held = dict(((h, l), why) for h, l, why in w["held"])
        self.assertIn("a person decides", held[("a", "permissive: 0 -> 1")])
        self.assertIn("only while permissive", held[("a", "remove rule: 50-local.rules: allow perm=any all : dir=/usr/lib/")])
        self.assertIn("2 would-be block(s)", held[("b", "permissive: 1 -> 0")])
        self.assertNotIn("gone", w["plan"])
        # permissive, a stricter clean-up to make: it goes in, enforcing waits
        p = host(permissive=True, changes=[enforcing["changes"][3], {"kind": "permissive", "from": "1", "to": "0", "line": "permissive: 1 -> 0"}])
        w = ff.fapolicyd_rules_job_workflow([{"_fr": p}], ["p"])
        self.assertEqual((w["plan"]["p"], w["held"][0][1]), ([enforcing["changes"][3]["line"]], "permissive: 1 -> 0"))


class ReportTest(unittest.TestCase):
    def test_report(self):
        blocks = [{"kind": "denied", "exe": "/usr/bin/bash", "path": "/opt/app/tool", "perm": "execute", "count": 2, "last": "x",
                   "rule_no": "13", "rule": "deny_audit perm=execute all : all", "where": "the host", "change": "trust file: ..."}]
        r = ff.fapolicyd_rules_job_report([{"_fr": host(blocks=blocks)}, {"_fr": host(permissive=True, changes=[], blocks=[
            dict(blocks[0], kind="would deny", count=3)])}, {"_fr": {"state": "not installed"}}, {}], ["a", "b", "c", "gone"])
        s = {x["title"]: x for x in r["sections"]}
        self.assertEqual([(x["label"], x["value"]) for x in r["summary"]][:2], [("Blocked", 2), ("Would be blocked (permissive)", 3)])
        self.assertIn("permissive on 1", r["title"])
        self.assertEqual(r["status"], "critical")
        self.assertEqual(s["Changes to approve"]["lines"][0], "trust file: /opt/app/tool size 51 sha256 " + SHA)
        self.assertEqual(s["What fapolicyd blocked"]["rows"][1][1], "would be (permissive)")
        self.assertIn("c", s["fapolicyd not installed"]["lines"][0])


if __name__ == "__main__":
    unittest.main()
