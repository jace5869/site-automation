#!/usr/bin/env python3
"""Unit tests for fapolicyd and containers (playbooks/fapolicyd_containers.yml): the audit log read
(roles/fapolicyd_containers/library/site_fapolicyd_containers.py), what makes a denial a container's, the rule
proposed for it, the checks of a rule asked for, the rules file's new list and the report
(plugins/filter/fapolicyd_filters.py).
    python3 tests/test_fapolicyd_containers.py"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
sys.path.insert(0, os.path.join(HERE, "..", "roles", "fapolicyd_containers", "library"))
import fapolicyd_filters as ff  # noqa: E402
import site_fapolicyd_containers as fc  # noqa: E402

# ausearch -m FANOTIFY -i, as RHEL 9 prints it: a container's program denied, a library a container opened, a host
# program (not a container's), and a path relative to its CWD
LOG = """----
type=PROCTITLE msg=audit(10/27/2026 08:00:01.120:501) : proctitle=/app/server
type=PATH msg=audit(10/27/2026 08:00:01.120:501) : item=0 name=/app/server inode=33 dev=00:3e mode=file,755 ouid=root ogid=root rdev=00:00 obj=system_u:object_r:container_file_t:s0:c12,c40 nametype=NORMAL
type=CWD msg=audit(10/27/2026 08:00:01.120:501) : cwd=/
type=SYSCALL msg=audit(10/27/2026 08:00:01.120:501) : arch=x86_64 syscall=execve success=no exit=EPERM ppid=900 pid=901 auid=unset uid=root gid=root comm=crun exe=/usr/bin/crun subj=system_u:system_r:container_runtime_t:s0 key=(null)
type=FANOTIFY msg=audit(10/27/2026 08:00:01.120:501) : resp=deny fan_type=rule_info fan_info=13 subj_trust=yes obj_trust=no
----
type=PATH msg=audit(10/27/2026 08:05:00.000:502) : item=0 name=/app/server inode=33 dev=00:3e mode=file,755 obj=system_u:object_r:container_file_t:s0:c12,c40 nametype=NORMAL
type=SYSCALL msg=audit(10/27/2026 08:05:00.000:502) : arch=x86_64 syscall=execve success=no exit=EPERM ppid=900 pid=905 uid=root comm=crun exe=/usr/bin/crun subj=system_u:system_r:container_runtime_t:s0
type=FANOTIFY msg=audit(10/27/2026 08:05:00.000:502) : resp=deny fan_type=rule_info fan_info=13 subj_trust=yes obj_trust=no
----
type=PATH msg=audit(10/27/2026 08:06:00.000:503) : item=0 name=/usr/lib/libapp.so.1 inode=40 obj=system_u:object_r:container_file_t:s0:c12,c40 nametype=NORMAL
type=SYSCALL msg=audit(10/27/2026 08:06:00.000:503) : arch=x86_64 syscall=openat success=no exit=EPERM ppid=901 pid=910 uid=1001 comm=server exe=/app/server subj=system_u:system_r:container_t:s0:c12,c40
type=FANOTIFY msg=audit(10/27/2026 08:06:00.000:503) : resp=deny fan_type=rule_info fan_info=11 subj_trust=no obj_trust=no
----
type=PATH msg=audit(10/27/2026 08:07:00.000:504) : item=0 name=/opt/tool/run inode=50 obj=unconfined_u:object_r:usr_t:s0 nametype=NORMAL
type=SYSCALL msg=audit(10/27/2026 08:07:00.000:504) : arch=x86_64 syscall=execve success=no exit=EPERM ppid=1 pid=920 uid=root comm=bash exe=/usr/bin/bash subj=unconfined_u:unconfined_r:unconfined_t:s0
type=FANOTIFY msg=audit(10/27/2026 08:07:00.000:504) : resp=deny fan_type=rule_info fan_info=13 subj_trust=yes obj_trust=no
----
type=PATH msg=audit(10/27/2026 08:08:00.000:505) : item=0 name=bin/tool inode=60 obj=system_u:object_r:container_file_t:s0:c1,c2 nametype=NORMAL
type=CWD msg=audit(10/27/2026 08:08:00.000:505) : cwd=/srv/app
type=SYSCALL msg=audit(10/27/2026 08:08:00.000:505) : arch=x86_64 syscall=execve success=no exit=EPERM pid=930 uid=root comm=sh exe=/usr/bin/sh subj=system_u:system_r:container_t:s0:c1,c2
type=FANOTIFY msg=audit(10/27/2026 08:08:00.000:505) : resp=deny fan_type=rule_info fan_info=13 subj_trust=yes obj_trust=no
"""

LABELS = re.compile(r"(^|:)(container_[a-z0-9_]*_t|spc_t)(:|$)")
PROGRAMS = {"podman", "crun", "runc", "conmon"}
PATHS = [re.compile(r"^/var/lib/containers/"), re.compile(r"/\.local/share/containers/")]


class Parse(unittest.TestCase):
    def test_events_and_paths(self):
        ev = fc.parse_events(LOG)
        self.assertEqual(len(ev), 5)
        self.assertEqual(ev[0]["time"], "10/27/2026 08:00:01")
        self.assertEqual(ev[0]["syscall"]["exe"], "/usr/bin/crun")
        self.assertEqual(ev[0]["fan"]["fan_info"], "13")
        self.assertEqual(fc.event_path(ev[0])[0], "/app/server")
        self.assertEqual(fc.event_path(ev[4])[0], "/srv/app/bin/tool")           # relative: made full with its CWD
        self.assertEqual(fc.parse_events("<no matches>\n"), [])

    def test_perm(self):
        self.assertEqual([fc.perm_of(s) for s in ("execve", "execveat", "openat", "")], ["execute", "execute", "open", "any"])


class Container(unittest.TestCase):
    def why(self, e, cg=lambda pid: ""):
        path, obj = fc.event_path(e)
        return fc.why_container(e, path, obj, LABELS, PROGRAMS, PATHS, cg)

    def test_what_makes_it_a_container_s(self):
        ev = fc.parse_events(LOG)
        self.assertEqual(self.why(ev[0])[0], ["container SELinux label", "container program crun"])
        self.assertEqual(self.why(ev[2])[0], ["container SELinux label"])
        self.assertEqual(self.why(ev[3])[0], [])                                   # a host program: not a container's
        self.assertEqual(self.why(ev[3], lambda pid: "abc123def456" if pid == "920" else ""), (["container cgroup"], "abc123def456"))
        e = {"syscall": {"exe": "/usr/bin/bash", "subj": "unconfined_t"}, "paths": [{"item": "0", "name": "/var/lib/containers/storage/overlay/x/merged/bin/a"}],
             "cwd": "", "fan": {"resp": "deny"}, "time": ""}
        self.assertEqual(self.why(e)[0], ["container storage"])


class Propose(unittest.TestCase):
    def test_narrowest(self):
        self.assertEqual(fc.propose("/usr/bin/crun", "/app/server", "execute", "path", True),
                         ("allow perm=execute exe=/usr/bin/crun : path=/app/server", ""))
        self.assertEqual(fc.propose("/app/server", "/opt/app/lib/libx.so", "open", "dir", True),
                         ("allow perm=open exe=/app/server : dir=/opt/app/lib/", ""))

    def test_never_a_system_folder(self):
        rule, note = fc.propose("/app/server", "/usr/lib/libapp.so.1", "open", "dir", True)
        self.assertEqual(rule, "allow perm=open exe=/app/server : path=/usr/lib/libapp.so.1")
        self.assertIn("system folder", note)

    def test_any_program_said(self):
        rule, note = fc.propose("/usr/bin/crun", "/app/server", "execute", "path", False)
        self.assertEqual(rule, "allow perm=execute all : path=/app/server")
        self.assertIn("any program", note)

    def test_no_rule_it_cannot_hold(self):
        self.assertEqual(fc.propose("/usr/bin/sh", "/app/my tool", "execute", "path", True)[0], "")
        self.assertEqual(fc.propose("/usr/bin/sh", "?", "execute", "path", True)[0], "")


class Rules(unittest.TestCase):
    def test_rule_problem(self):
        ok = ["allow perm=execute exe=/usr/bin/crun : path=/app/server", "allow perm=open exe=/app/server : dir=/opt/app/lib/",
              "allow_audit perm=any uid=0 : ftype=application/x-executable", "allow perm=execute exe=/usr/bin/crun : all"]
        for r in ok:
            self.assertEqual(ff.fapolicyd_rule_problem(r), "", r)
        self.assertIn("not an allow rule", ff.fapolicyd_rule_problem("deny perm=any all : all"))
        self.assertIn("turn fapolicyd off", ff.fapolicyd_rule_problem("allow perm=any all : all"))
        self.assertIn("every file", ff.fapolicyd_rule_problem("allow perm=any exe=/x : dir=/"))
        self.assertIn("end with /", ff.fapolicyd_rule_problem("allow perm=any exe=/x : dir=/opt"))
        self.assertIn("full path", ff.fapolicyd_rule_problem("allow perm=any exe=crun : path=/a"))
        self.assertIn("not an attribute", ff.fapolicyd_rule_problem("allow perm=any colour=red : path=/a"))
        self.assertIn("line break", ff.fapolicyd_rule_problem("allow perm=any exe=/x : path=/a\nallow perm=any all : all"))

    def test_plan(self):
        a, b, c = ("allow perm=execute exe=/usr/bin/crun : path=/app/%s" % x for x in "abc")
        p = ff.fapolicyd_rules_plan([a, b], [c, "  " + b.replace(" ", "  ")], [a])
        self.assertEqual((p["rules"], p["added"], p["removed"], p["changed"]), ([b, c], [c], [a], True))
        self.assertFalse(ff.fapolicyd_rules_plan([a], [a], [])["changed"])
        bad = ff.fapolicyd_rules_plan([a], [c, "allow perm=any all : all"], [a])
        self.assertEqual((bad["rules"], bad["added"], bad["removed"], bad["changed"], len(bad["bad"])), ([a], [], [], False, 1))

    def test_file(self):
        text = ff.fapolicyd_rules_file(["allow perm=execute exe=/usr/bin/crun : path=/app/a"])
        self.assertTrue(text.startswith("# Managed by site-automation"))
        self.assertTrue(text.endswith("allow perm=execute exe=/usr/bin/crun : path=/app/a\n"))


class Report(unittest.TestCase):
    def test_report(self):
        g = {"exe": "/usr/bin/crun", "path": "/app/server", "perm": "execute", "count": 2, "last": "10/27/2026 08:05:00", "rule_no": "13",
             "rule": "deny_audit perm=execute all : all", "containers": ["web01"], "why": ["container SELinux label"],
             "proposal": "allow perm=execute exe=/usr/bin/crun : path=/app/server", "note": ""}
        h1 = {"_fc": {"state": "read", "error": "", "active": "active", "auditd": "active", "rules_d": True, "fagenrules": True,
                      "groups": [g], "others": ["/usr/bin/bash -> /opt/tool/run x1"], "counts": {"other": 1}, "managed": []}}
        h2 = dict(h1, _fc=dict(h1["_fc"], groups=[dict(g, count=1)], others=[]))
        h3 = {"_fc": {"state": "read", "error": "", "active": "inactive", "auditd": "active", "rules_d": True, "fagenrules": True,
                      "groups": [], "others": [], "managed": []}}
        r = ff.fapolicyd_containers_report([h1, h2, h3, {"_fc": {"state": "not installed"}}, {}], ["a", "b", "c", "d", "e"], "today")
        self.assertEqual(r["status"], "critical")
        self.assertIn("3 container denial(s) on 2 host(s)", r["title"])
        self.assertIn("fapolicyd not running on 1", r["title"])
        sec = {s["title"]: s for s in r["sections"]}
        self.assertEqual(sec["Container denials"]["rows"][0][:6], ["a", "web01", "/usr/bin/crun", "/app/server", "execute", 2])
        self.assertEqual(sec["Rules to approve"]["lines"], ["allow perm=execute exe=/usr/bin/crun : path=/app/server     (a, b)"])
        self.assertTrue(any("other denial" in x for x in sec["The hosts"]["lines"]))
        self.assertIn("e: could not be reached", sec["Not read"]["lines"][0])
        self.assertNotIn("The rules: what this run changed", sec)

    def test_report_after_a_change(self):
        rule = "allow perm=execute exe=/usr/bin/crun : path=/app/server"
        base = {"state": "read", "error": "", "active": "active", "auditd": "active", "rules_d": True, "fagenrules": True,
                "groups": [], "others": [], "managed": []}
        ok = {"_fc": base, "_fc_apply": {"state": "changed", "added": [rule], "removed": [], "rules": [rule], "bad": [], "error": ""}}
        bad = {"_fc": base, "_fc_apply": {"state": "failed", "added": [], "removed": [], "rules": [], "bad": [],
                                          "error": "fapolicyd still running: rc 3 - the file put back as it was"}}
        r = ff.fapolicyd_containers_report([ok, bad], ["a", "b"], "today")
        sec = {s["title"]: s for s in r["sections"]}["The rules: what this run changed"]
        self.assertEqual([x[:3] for x in sec["rows"]], [["a", "changed", rule], ["b", "failed", "-"]])
        self.assertEqual(r["status"], "critical")
        self.assertIn("the rules could not be changed on 1 host(s)", r["title"])
        r = ff.fapolicyd_containers_report([ok], ["a"], "today", check=True)
        self.assertIn("rules would change on 1 host(s)", r["title"])
        self.assertIn("The rules: what this run would change", [s["title"] for s in r["sections"]])


if __name__ == "__main__":
    unittest.main()
