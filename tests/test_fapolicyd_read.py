#!/usr/bin/env python3
"""Unit tests for how the fapolicyd jobs read (plugins/module_utils/site_fapolicyd.py): the audit log, what makes a
denial a container's, the rule proposed for it, the rules files' review; and ACT's answer in the denials report
(plugins/filter/fapolicyd_filters.py).
    python3 tests/test_fapolicyd_read.py"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("ansible.module_utils.site_fapolicyd",
                                               os.path.join(HERE, "..", "plugins", "module_utils", "site_fapolicyd.py"))
_sf = importlib.util.module_from_spec(_spec)
sys.modules["ansible.module_utils.site_fapolicyd"] = _sf
_spec.loader.exec_module(_sf)
import fapolicyd_filters as ff  # noqa: E402
fc = _sf

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
        path, obj, _ = fc.event_path(e)
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

    def test_a_file_on_the_host_is_known_by_its_inode(self):
        """The audit log's path is the container's: only a file that is on the host at that path, with the record's
        inode and device, gets a rule of its own (a bind mount)."""
        me = os.path.abspath(__file__)
        st = os.stat(me)
        rec = {"inode": str(st.st_ino), "dev": "%02x:%02x" % (os.major(st.st_dev), os.minor(st.st_dev))}
        self.assertTrue(fc.same_file(me, rec))
        self.assertTrue(fc.same_file(me, {"inode": str(st.st_ino)}))                      # no device in the record
        self.assertFalse(fc.same_file(me, {"inode": str(st.st_ino + 1), "dev": rec["dev"]}))
        self.assertFalse(fc.same_file(me, {"inode": str(st.st_ino), "dev": "00:01"}))     # another device
        self.assertFalse(fc.same_file("/usr/lib/libapp.so.1", {"inode": "40"}))           # not on the host
        self.assertFalse(fc.same_file("bin/tool", {"inode": "60"}))

    def test_storage_rule(self):
        graph, rootless = fc.storage_conf('# podman\n[storage]\ndriver = "overlay"\ngraphroot = "/var/lib/containers/storage"\n')
        self.assertEqual((graph, rootless), ("/var/lib/containers/storage", ""))
        self.assertEqual(fc.storage_conf('graphroot = "/data/containers/storage/"  # moved\nrootless_storage_path = "$HOME/.local/share/containers/storage"'),
                         ("/data/containers/storage", "$HOME/.local/share/containers/storage"))
        rule, note = fc.storage_rule("/usr/lib/libapp.so.1", "/app/server", None, graph, rootless)
        self.assertEqual(rule, "allow perm=any all : dir=/var/lib/containers/storage/overlay/")
        self.assertIn("no host path of its own", note)
        self.assertEqual(fc.storage_rule("/var/lib/containers/storage/volumes/data/_data/x.sh", "/usr/bin/sh", None, graph, rootless)[0],
                         "allow perm=any all : dir=/var/lib/containers/storage/volumes/")
        self.assertEqual(fc.storage_rule("/run/containers/storage/overlay-containers/x/userdata/y", "/usr/bin/crun", None, graph, rootless)[0],
                         "allow perm=any all : dir=/run/containers/storage/")
        self.assertEqual(fc.storage_rule("/usr/bin/x", "/var/lib/docker/overlay2/a/merged/usr/bin/x", None, graph, rootless)[0],
                         "allow perm=any all : dir=/var/lib/docker/overlay2/")
        # a user's denial: their own storage when it exists on the host (here it does not: root's)
        user = {"name": "alice", "uid": 1001, "home": "/home/alice"}
        self.assertEqual(fc.storage_rule("/app/x", "/app/x", user, graph, rootless)[0], "allow perm=any all : dir=/var/lib/containers/storage/overlay/")
        here = {"name": "tester", "uid": 1001, "home": os.path.dirname(os.path.abspath(__file__))}   # a folder that exists ...
        rule, note = fc.storage_rule("/app/x", "/app/x", here, graph, "$HOME")                      # ... as the rootless storage
        self.assertEqual(rule, "allow perm=any all : dir=%s/overlay/" % here["home"])
        self.assertIn("tester's own storage", note)
        self.assertIn("STIG", note)


REVIEW_FILES = [
    ("35-site-containers.rules", "# Managed by site-automation\nallow perm=any all : dir=/var/lib/containers/storage/overlay/\n"),
    ("41-shared-obj.rules", "allow perm=open all : ftype=application/x-sharedlib trust=1\ndeny_audit perm=open all : ftype=application/x-sharedlib\n"),
    ("99-local.rules", "\n".join([
        "# Docker Compose binaries",
        "allow perm=any all : path=/usr/bin/docker-compose",
        "allow perm=any all : dir=/home/*/.local/lib/python*/site-packages/",
        "allow perm=any all : dir=/usr/lib/",
        "allow perm=any all : dir=/var/lib/containers/storage/",
        "allow perm=any all : dir=/var/lib/containers/storage/",
        "allow perm=any all : dir=/home/alice/.local/share/containers/storage/",
        "allow perm=open exe=/usr/bin/python3.9 : dir=/usr/lib/python3.9/site-packages/",
        "allow perm=any all : path=/usr/bin/crun",
        "allow perm=any all : all",
        "allow something",
        ""])),
]


class Review(unittest.TestCase):
    def test_what_to_tighten(self):
        owned = lambda p: "podman-4.9.4-1.el9.x86_64" if p == "/usr/bin/crun" else ""
        out = fc.review_rules(REVIEW_FILES, owned)
        by = {}
        for f in out:
            by.setdefault((f["file"], f["line"]), []).append((f["status"], f["what"]))
        self.assertNotIn(("35-site-containers.rules", 2), by)                             # the job's own storage rule: fine
        self.assertFalse(any(k[0] == "41-shared-obj.rules" for k in by))                   # fapolicyd's own files: not reviewed
        self.assertEqual(by[("99-local.rules", 3)][0][0], "warning")
        self.assertIn("no wildcards", by[("99-local.rules", 3)][0][1])
        self.assertIn("system folder", by[("99-local.rules", 4)][0][1])
        self.assertEqual(by[("99-local.rules", 4)][0][0], "warning")
        self.assertNotIn(("99-local.rules", 5), by)                                         # root's storage for any program: the job's own kind of rule
        self.assertIn("repeats line 5 of 99-local.rules", by[("99-local.rules", 6)][0][1])
        self.assertIn("any user who can write there", by[("99-local.rules", 7)][0][1])
        self.assertEqual(by[("99-local.rules", 8)], [("info", "untrusted files under a system folder, for that program; RPM-owned files there are trusted already")])
        self.assertIn("RPM-owned (podman-4.9.4-1.el9.x86_64)", by[("99-local.rules", 9)][0][1])
        self.assertEqual(by[("99-local.rules", 10)][0][0], "critical")
        self.assertIn("not a rule fapolicyd reads", by[("99-local.rules", 11)][0][1])
        self.assertIn("sorts after 41-shared-obj.rules", by[("99-local.rules", 0)][0][1])   # the file itself
        self.assertNotIn(("35-site-containers.rules", 0), by)
        self.assertEqual(fc.review_rules([], owned), [])
        self.assertEqual(fc.review_rules([("30-site.rules", "allow perm=execute exe=/usr/bin/crun : path=/srv/app/server\n")], owned), [])


# the fapolicyd denials report: ACT's Markdown answer by its headings, a line per paragraph, item and command
MD = """There are **no blocked accesses** in the evidence.
---
### Service Failure Triage (`fapolicyd` is failed)
While the exact failure message
is not in the output, two causes:
1. **Unreadable trust files:** `fapolicyd` runs as `uid = fapolicyd`.
2. **Missing journal output:** run these read-only commands:
   - `systemctl status fapolicyd.service`
   - `journalctl -u fapolicyd.service -n 100`
```text
uid = fapolicyd gid = fapolicyd
```
### Findings
**Finding 1: Incorrect ownership**
* **ROOT CAUSE:** three trust files are 0600."""


class ActAnswerTest(unittest.TestCase):
    def test_blocks(self):
        b = ff.act_md_blocks(MD)
        self.assertEqual([h for h, _ in b], ["", "Service Failure Triage (fapolicyd is failed)", "Findings"])
        self.assertEqual(b[0][1], ["There are no blocked accesses in the evidence."])
        self.assertEqual(b[1][1], ["While the exact failure message is not in the output, two causes:",
                                   "1. Unreadable trust files: fapolicyd runs as uid = fapolicyd.",
                                   "2. Missing journal output: run these read-only commands:",
                                   "systemctl status fapolicyd.service", "journalctl -u fapolicyd.service -n 100",
                                   "uid = fapolicyd gid = fapolicyd"])
        self.assertEqual(b[2][1], ["Finding 1: Incorrect ownership", "ROOT CAUSE: three trust files are 0600."])
        self.assertEqual(ff.act_md_blocks("one line"), [("", ["one line"])])
        self.assertEqual(ff.act_md_blocks(""), [])

    def test_report(self):
        hv = {"_fa_rpm": {"rc": 0}, "_fa_active": {"stdout": "failed"}, "_fa_auditd": {"stdout": "active"}, "_fa_count": "0",
              "_fa_paths": [], "_fa_exes": [], "_fa_findings": ["fapolicyd is failed"],
              "act_triage_result": {"status": "done", "summary": MD,
                                    "denied": [{"command": "chmod 0644 /etc/fapolicyd/trust.d/app", "pre_approvable": True}]}}
        r = ff.fapolicyd_report([hv], ["fa01"])
        s = {x["title"]: x for x in r["sections"]}
        first = s["fa01: ACT's analysis"]
        self.assertEqual(first["lines"], ["fapolicyd is failed", "There are no blocked accesses in the evidence.",
                                          "ACT proposes (for your security approval): chmod 0644 /etc/fapolicyd/trust.d/app"])
        self.assertIn("ran nothing", first["text"])
        self.assertIn("1. Unreadable trust files: fapolicyd runs as uid = fapolicyd.", s["fa01: Service Failure Triage (fapolicyd is failed)"]["lines"])
        self.assertEqual(s["fa01: Findings"]["lines"][0], "Finding 1: Incorrect ownership")
        titles = [x["title"] for x in r["sections"]]
        self.assertLess(titles.index("fa01: ACT's analysis"), titles.index("fa01: Findings"))
        self.assertFalse(any("**" in l or "`" in l for x in r["sections"] for l in x.get("lines", [])))

if __name__ == "__main__":
    unittest.main()
