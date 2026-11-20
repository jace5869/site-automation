#!/usr/bin/env python3
"""1.79.13: Linux - auditd tuning for containers (plugins/filter/auditd_tune_filters.py) - the read, what is too small or
waiting for a reboot, the changes to approve, the approved lines picked per host, the workflow's plan and the report.
The tasks themselves: tests/auditd/run_auditd_tuning_test.sh (two containers, stand-in commands).
    python3 tests/test_auditd_tune.py"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "filter"))
import auditd_tune_filters as a  # noqa: E402


def read(enabled="1", limit="320", lost="0", rules_b="320", bwt="", q="1200", ov="IGNORE", e="", cmd="", kernels=None,
         podman="no", crules="", rootless=()):
    lines = ["k_enabled %s" % enabled, "k_backlog_limit %s" % limit, "k_backlog 3", "k_lost %s" % lost,
             "q_depth %s" % q, "overflow_action %s" % ov, "flush INCREMENTAL_ASYNC", "freq 50",
             "rules_b %s" % rules_b, "rules_bwt %s" % bwt, "rules_e %s" % e, "rules_file 10-base-config.rules",
             "cmdline_backlog %s" % cmd, "podman %s" % podman]
    for k, v in (kernels if kernels is not None else {"/boot/vmlinuz-a": "none", "/boot/vmlinuz-b": "8192"}).items():
        lines.append("kernel %s %s" % (k, v))
    if crules:
        lines.append("containers_rules yes")
    lines += ["rootless %s" % u for u in rootless]
    return "\n".join(lines) + "\n"


class State(unittest.TestCase):
    def test_too_small_everything_to_approve(self):
        st = a.auditd_tune_state(read(lost="17"))
        self.assertEqual([c["id"] for c in st["changes"]], ["backlog", "kernel", "queue"])
        self.assertIn("now 320 and not set", st["changes"][0]["text"])
        self.assertIn("on 1 of 2 kernel(s) without it or lower", st["changes"][1]["text"])
        self.assertEqual(st["changes"][1]["kernels"], ["/boot/vmlinuz-a"])
        texts = [f["text"] for f in st["findings"]]
        self.assertIn("the kernel's audit backlog_limit is 320 - too small for containers (want 8192)", texts)
        self.assertIn("17 audit record(s) LOST since boot (backlog 3 of limit 320)", texts)
        self.assertFalse(st["immutable"])

    def test_set_but_waiting_for_the_reboot(self):
        st = a.auditd_tune_state(read(enabled="2", rules_b="8192", bwt="60000", q="8192", ov="SYSLOG",
                                      kernels={"/boot/vmlinuz-a": "8192"}, cmd=""))
        self.assertEqual(st["changes"], [])
        self.assertTrue(st["immutable"])
        self.assertTrue(any(w.startswith("backlog_limit 8192 / backlog_wait_time 60000 in the rules file are set; take effect at the next "
                                         "reboot - immutable (-e 2): now backlog_limit 320") for w in st["waiting"]), st["waiting"])
        self.assertTrue(any("is set for every kernel; takes effect at the next reboot: the running kernel has none" in w for w in st["waiting"]))

    def test_set_not_loaded_when_not_immutable(self):
        st = a.auditd_tune_state(read(rules_b="8192", bwt="60000"))
        self.assertIn("in the rules file are not loaded yet: now backlog_limit 320", st["findings"][0]["text"])

    def test_locked_and_pending(self):
        """(Codex) -e 2 in the rules but enabled 1: not locked - pending (the next load locks it)."""
        st = a.auditd_tune_state(read(enabled="1", e="2"))
        self.assertEqual((st["locked"], st["pending"]), (False, True))
        self.assertTrue(any("the next augenrules --load (or reboot) locks it" in f["text"] for f in st["findings"]))
        st = a.auditd_tune_state(read(enabled="2", e="2"))
        self.assertEqual((st["locked"], st["pending"]), (True, False))

    def test_never_lowered(self):
        """(Codex) a value already above its minimum is kept: -b 32768, q_depth 32768."""
        st = a.auditd_tune_state(read(rules_b="32768", bwt="", q="32768", ov="IGNORE"))
        by = dict((c["id"], c) for c in st["changes"])
        self.assertEqual((by["backlog"]["b"], by["backlog"]["w"], by["queue"]["q"]), (32768, 60000, 32768))
        self.assertIn("-b 32768", by["backlog"]["text"])

    def test_live_wait_time_and_grubby_error(self):
        st = a.auditd_tune_state(read(enabled="2", limit="8192", rules_b="8192", bwt="60000", q="8192", ov="SYSLOG",
                                      kernels={"/boot/vmlinuz-a": "8192"}, cmd="8192").replace("k_backlog 3", "k_backlog 3\nk_backlog_wait_time 0"))
        self.assertTrue(any("take effect at the next reboot" in w and "backlog_wait_time 0" in w for w in st["waiting"]), st)
        st = a.auditd_tune_state(read(kernels={}) + "grubby_error grubby: no boot entries\n")
        self.assertTrue(any("the boot entries could not be read (grubby --info=ALL): grubby: no boot entries" in f["text"] for f in st["findings"]))

    def test_all_right(self):
        st = a.auditd_tune_state(read(limit="8192", rules_b="8192", bwt="60000", q="8192", ov="SYSLOG",
                                      kernels={"/boot/vmlinuz-a": "8192"}, cmd="8192"))
        self.assertEqual((st["changes"], st["findings"], st["waiting"]), ([], [], []))

    def test_containers_only_when_asked_and_there(self):
        root = "/var/lib/containers/storage/overlay"
        self.assertNotIn("containers", [c["id"] for c in a.auditd_tune_state(read(podman="yes") + "storage_root yes\n")["changes"]])
        st = a.auditd_tune_state(read(podman="yes", rootless=["svc"]) + "storage_root yes\n", {"containers": True, "users": ["svc", "ghost"]})
        c = [x for x in st["changes"] if x["id"] == "containers"][0]
        self.assertEqual(c["dirs"], [root, "/home/svc/.local/share/containers/storage/overlay"])
        self.assertIn("ISSO approval needed", c["text"])
        # (Codex) only paths that are there; one excluded already is not asked again, each checked on its own
        self.assertNotIn("containers", [x["id"] for x in a.auditd_tune_state(read(podman="yes"), {"containers": True})["changes"]])
        st = a.auditd_tune_state(read(podman="yes", rootless=["svc"]) + "storage_root yes\nexcluded %s\n" % root,
                                 {"containers": True, "users": ["svc"]})
        self.assertEqual([x for x in st["changes"] if x["id"] == "containers"][0]["dirs"], ["/home/svc/.local/share/containers/storage/overlay"])

    def test_settings_and_not_read(self):
        st = a.auditd_tune_state(read(limit="4096", rules_b="4096", bwt="60000", kernels={"/boot/vmlinuz-a": "4096"}), {"backlog": 4096})
        self.assertNotIn("backlog", [c["id"] for c in st["changes"]])
        bad = a.auditd_tune_state("error auditctl -s: Operation not permitted\n")
        self.assertFalse(bad["read"])
        self.assertEqual(bad["changes"], [])


class Pick(unittest.TestCase):
    def test_lines_ids_and_hosts(self):
        ch = a.auditd_tune_state(read())["changes"]
        self.assertEqual(a.auditd_tune_pick(["backlog", "queue"], ch, "h1"), ["backlog", "queue"])
        self.assertEqual(a.auditd_tune_pick([ch[1]["text"]], ch, "h1"), ["kernel"])                 # the report's line
        self.assertEqual(a.auditd_tune_pick(["h1: kernel", "h2: queue"], ch, "h1"), ["kernel"])     # another host's: not this one's
        self.assertEqual(a.auditd_tune_pick(["h2: " + ch[0]["text"]], ch, "h1"), [])
        self.assertEqual(a.auditd_tune_pick(["containers"], ch, "h1"), [])                         # not proposed here: never made
        self.assertEqual(a.auditd_tune_pick([], ch, "h1"), [])


class Report(unittest.TestCase):
    def test_workflow_and_report(self):
        hv = [{"_atu": a.auditd_tune_state(read(lost="5"))}, {"_atu": a.auditd_tune_state(read(enabled="2", rules_b="8192", bwt="60000",
                                                                                           q="8192", ov="SYSLOG", kernels={"/boot/vmlinuz-a": "8192"}))},
              {"_atu": a.auditd_tune_state(read()), "_atu_apply": {"state": "rolled back", "did": ["queue: put back"]}}]
        w = a.auditd_tune_workflow(hv, ["h1", "h2", "h3"])
        self.assertEqual((sorted(w["plan"]), w["count"]), (["h1", "h3"], 6))
        r = a.auditd_tune_report(hv, ["h1", "h2", "h3"])
        self.assertEqual(r["status"], "critical")                                            # a rolled-back host
        self.assertEqual(r["sections"][0]["rows"][1][-1], "waiting for a reboot")
        self.assertEqual(r["sections"][0]["rows"][2][-1], "rolled back")
        todo = [s for s in r["sections"] if s["title"].startswith("Changes to approve")][0]
        self.assertTrue(todo["lines"][0].startswith("h1: backlog: -b 8192"))
        self.assertIn("never changed", r["sections"][-1]["lines"][1])
        self.assertIn("Never a reboot", r["sections"][-1]["lines"][1])


if __name__ == "__main__":
    unittest.main(verbosity=1)
