#!/usr/bin/env python3
"""Unit tests for the fapolicyd fix job (playbooks/fapolicyd_fix.yml): the read
(roles/fapolicyd_fix/library/site_fapolicyd_fix.py - the package's modes, fapolicyd.conf, who can read a trust file,
the fix for one), the plan from the approved lines, the way back and the report (plugins/filter/fapolicyd_filters.py).
    python3 tests/test_fapolicyd_fix.py"""
import os
import stat
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
sys.path.insert(0, os.path.join(HERE, "..", "roles", "fapolicyd_fix", "library"))
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("ansible.module_utils.site_fapolicyd",
                                               os.path.join(HERE, "..", "plugins", "module_utils", "site_fapolicyd.py"))
_sf = importlib.util.module_from_spec(_spec)
sys.modules["ansible.module_utils.site_fapolicyd"] = _sf
_spec.loader.exec_module(_sf)
import fapolicyd_filters as ff  # noqa: E402
import site_fapolicyd_fix as fx  # noqa: E402

# rpm -q --qf '[%{FILEMODES:perms} %{FILEUSERNAME} %{FILEGROUPNAME} %{FILENAMES}\n]' fapolicyd, as RHEL 8 and 9 print it
RPM = """drwxr-x--- root fapolicyd /etc/fapolicyd
-rw-r--r-- root fapolicyd /etc/fapolicyd/compiled.rules
-rw-r--r-- root fapolicyd /etc/fapolicyd/fapolicyd.conf
-rw-r--r-- root fapolicyd /etc/fapolicyd/fapolicyd.trust
drwxr-x--- root fapolicyd /etc/fapolicyd/rules.d
-rw-r--r-- root fapolicyd /etc/fapolicyd/rules.d/*
drwxr-x--- root fapolicyd /etc/fapolicyd/trust.d
-rwxr-xr-x root root /usr/sbin/fapolicyd"""


class Stat(object):
    def __init__(self, mode, uid, gid):
        self.st_mode, self.st_uid, self.st_gid = mode, uid, gid


class ReadTest(unittest.TestCase):
    def test_package_reference(self):
        self.assertEqual(fx.perms_mode("-rw-r--r--"), 0o644)
        self.assertEqual(fx.perms_mode("drwxr-x---"), 0o750)
        ref = fx.rpm_reference(RPM)
        self.assertEqual(ref, {"dir": ("root", "fapolicyd", 0o750), "trust": ("root", "fapolicyd", 0o644),
                               "trust.d": ("root", "fapolicyd", 0o750)})
        self.assertEqual(fx.rpm_reference(""), {})

    def test_conf(self):
        c = fx.conf_values("# comment\npermissive = 0\nuid = fapolicyd\ngid = fapolicyd\ndb_max_size = 180  # mine\n\n")
        self.assertEqual((c["uid"], c["db_max_size"], c["permissive"]), ("fapolicyd", "180", "0"))

    def test_readable(self):
        uid, gids = 990, [985]
        self.assertFalse(fx.readable(0o600, 0, 0, False, uid, gids))       # root:root 0600: the screenshot's trust files
        self.assertTrue(fx.readable(0o640, 0, 985, False, uid, gids))      # root:fapolicyd 0640
        self.assertTrue(fx.readable(0o644, 0, 0, False, uid, gids))
        self.assertFalse(fx.readable(0o740, 0, 985, True, uid, gids))      # a folder needs r and x
        self.assertTrue(fx.readable(0o750, 0, 985, True, uid, gids))
        self.assertTrue(fx.readable(0o400, 990, 0, False, uid, gids))      # its own

    def test_access_fix(self):
        uid, gids = 990, [985]
        ref = ("root", "fapolicyd", 0o644)
        # root:root 0600 - unreadable: to the package's own (names as this host has them; 985 may not be fapolicyd here)
        f = fx.access_fix("/etc/fapolicyd/trust.d/vendor-app", Stat(stat.S_IFREG | 0o600, 0, 0), ref, uid, gids, {})
        self.assertEqual(f["kind"], "access")
        self.assertTrue(f["line"].startswith("trust file access: /etc/fapolicyd/trust.d/vendor-app root:"))
        self.assertTrue(f["line"].endswith(" 0600 -> root:fapolicyd 0644"))
        self.assertIn("cannot read it", f["why"])
        # what fapolicyd said about it is in the why
        f = fx.access_fix("/etc/fapolicyd/trust.d/vendor-app", Stat(stat.S_IFREG | 0o600, 0, 0), ref, uid, gids,
                          {"/etc/fapolicyd/trust.d/vendor-app": "Permission denied"})
        self.assertIn("fapolicyd says: Cannot read it (Permission denied)", f["why"])
        # readable and only root writes: nothing to fix
        self.assertIsNone(fx.access_fix("/x", Stat(stat.S_IFREG | 0o644, 0, 0), ref, uid, gids, {}))
        self.assertIsNone(fx.access_fix("/x", Stat(stat.S_IFREG | 0o640, 0, 985), ref, uid, gids, {}))
        # group-writable: anyone in that group could trust a program
        f = fx.access_fix("/x", Stat(stat.S_IFREG | 0o664, 0, 0), ref, uid, gids, {})
        self.assertIn("writable by its group", f["why"])
        # a folder fapolicyd cannot open
        f = fx.access_fix("/etc/fapolicyd/trust.d", Stat(stat.S_IFDIR | 0o700, 0, 0), ("root", "fapolicyd", 0o750), uid, gids, {})
        self.assertIn("cannot open the folder", f["why"])
        self.assertTrue(f["line"].endswith(" 0700 -> root:fapolicyd 0750"))


def host(**kw):
    h = {"state": "read", "error": "", "active": "failed", "version": "fapolicyd-1.3.2-1.el8.x86_64", "journal": [], "by_hand": [],
         "db": {"current": "50", "auto": False, "need_mb": 120, "trust_mb": 64.5, "rpm_files": 30000, "evidence": []},
         "fixes": [{"kind": "access", "path": "/etc/fapolicyd/trust.d/vendor-app", "from": "root:root 0600", "to": "root:fapolicyd 0644",
                    "owner": "root", "group": "fapolicyd", "mode": "0644", "why": "the fapolicyd user cannot read it",
                    "line": "trust file access: /etc/fapolicyd/trust.d/vendor-app root:root 0600 -> root:fapolicyd 0644"},
                   {"kind": "label", "path": "/etc/fapolicyd/trust.d/vendor-app", "from": "a_u:object_r:user_tmp_t:s0",
                    "to": "system_u:object_r:fapolicyd_config_t:s0", "why": "label",
                    "line": "SELinux label: /etc/fapolicyd/trust.d/vendor-app a_u:object_r:user_tmp_t:s0 -> system_u:object_r:fapolicyd_config_t:s0"},
                   {"kind": "db", "from": "50", "to": "200", "auto": False, "why": "fapolicyd says: db_max_size needs to be increased",
                    "line": "db_max_size: 50 -> 200"},
                   {"kind": "permissive", "from": "0", "to": "1", "why": "start it permissive", "line": "permissive: 0 -> 1"},
                   {"kind": "restart", "from": "failed", "to": "active", "why": "down", "line": "restart fapolicyd (it is failed)"}]}
    h.update(kw)
    return h


class DbTest(unittest.TestCase):
    def test_db_fix(self):
        says = ["db_max_size needs to be increased"]
        # fapolicyd said it: a number on 1.3 (twice now at least), auto on 1.4
        self.assertEqual(fx.db_fix("50", False, says, 10, "failed")["line"], "db_max_size: 50 -> 100")
        self.assertEqual(fx.db_fix("100", True, says, 10, "active")["line"], "db_max_size: 100 -> auto")
        # running, nothing said, the estimate over db_max_size (a RHEL 8 server's RPM files): no fix
        self.assertIsNone(fx.db_fix("50", False, [], 120, "active"))
        # down, the estimate over it: a fix, with the numbers in the why
        f = fx.db_fix("", False, [], 120, "failed", 1.5, 64.5, 30000)
        self.assertEqual(f["line"], "db_max_size: unset -> 200")
        self.assertIn("64.5 MB of trust files", f["why"])
        # auto already: nothing to propose
        self.assertIsNone(fx.db_fix("auto", True, says, 500, "failed"))


class PlanTest(unittest.TestCase):
    def test_nothing_approved(self):
        p = ff.fapolicyd_fix_plan(host(), [])
        self.assertEqual((p["apply"], p["changed"], p["restart"], p["update"]), ([], False, False, False))

    def test_approved_in_order_with_the_approvers_own_number(self):
        h = host()
        p = ff.fapolicyd_fix_plan(h, ["restart fapolicyd (it is failed)", "db_max_size:  50 ->  400",
                                      "  " + h["fixes"][0]["line"], h["fixes"][1]["line"]])
        self.assertEqual([f["kind"] for f in p["apply"]], ["access", "label", "db", "restart"])
        self.assertEqual((p["db_to"], p["apply"][2]["line"], p["apply"][2]["proposed"]), ("400", "db_max_size: 50 -> 400", "db_max_size: 50 -> 200"))
        self.assertEqual((p["restart"], p["update"], p["bad"], p["not_here"]), (True, False, [], []))
        p = ff.fapolicyd_fix_plan(h, ["restart fapolicyd (it is failed)", "permissive: 0 -> 1"])
        self.assertEqual(([f["kind"] for f in p["apply"]], p["permissive_to"], p["restart"]), (["permissive", "restart"], "1", True))
        back = ff.fapolicyd_fix_rollback(ff.fapolicyd_fix_plan(h, ["permissive: 0 -> 1", "db_max_size: 50 -> 100"])["apply"], "/c", "/b")
        self.assertEqual(back.count("cp -p /b /c"), 1)

    def test_not_restarted_unless_approved_while_down(self):
        h = host()
        p = ff.fapolicyd_fix_plan(h, [h["fixes"][0]["line"]])
        self.assertEqual((p["restart"], p["update"]), (False, False))

    def test_running_host(self):
        h = host(active="active", fixes=host()["fixes"][:3])
        p = ff.fapolicyd_fix_plan(h, [h["fixes"][0]["line"]])           # trust files only: reloaded, not restarted
        self.assertEqual((p["restart"], p["update"]), (False, True))
        p = ff.fapolicyd_fix_plan(h, [h["fixes"][0]["line"], "db_max_size: 50 -> 100"])   # db_max_size: a restart
        self.assertEqual((p["restart"], p["update"]), (True, False))

    def test_stale_and_bad(self):
        h = host()
        # approved for a state this host is no longer in: not applied, said
        p = ff.fapolicyd_fix_plan(h, ["db_max_size: 180 -> 400", "trust file access: /etc/fapolicyd/trust.d/old root:root 0600 -> root:fapolicyd 0644"])
        self.assertEqual((p["apply"], len(p["not_here"]), p["bad"]), ([], 2, []))
        # a line this job never gives, or a db_max_size it cannot take: nothing changes
        for line in ("rm -rf /", "db_max_size: 50 -> lots", "db_max_size: 50 -> 0", "db_max_size: 50",
                     h["fixes"][0]["line"] + "     (fa01)", "restart fapolicyd (it is failed) (fa01)", "trust file access: /x"):
            p = ff.fapolicyd_fix_plan(h, [h["fixes"][0]["line"], line])
            self.assertEqual(len(p["bad"]), 1, line)
        # auto where this fapolicyd cannot: refused on this host
        p = ff.fapolicyd_fix_plan(h, ["db_max_size: 50 -> auto"])
        self.assertEqual((p["apply"], p["refused"][0]["line"]), ([], "db_max_size: 50 -> auto"))
        h["fixes"][2]["auto"] = True
        self.assertEqual(ff.fapolicyd_fix_plan(h, ["db_max_size: 50 -> auto"])["db_to"], "auto")

    def test_rollback(self):
        h = host()
        p = ff.fapolicyd_fix_plan(h, [f["line"] for f in h["fixes"]])
        back = ff.fapolicyd_fix_rollback(p["apply"], "/etc/fapolicyd/fapolicyd.conf", "/tmp/x.fapolicyd-fix/fapolicyd.conf")
        lines = [x.strip() for x in back.splitlines()]
        self.assertEqual(lines[:2], ["back() {", "rc=0"])
        self.assertEqual(lines[2], "cp -p /tmp/x.fapolicyd-fix/fapolicyd.conf /etc/fapolicyd/fapolicyd.conf || rc=1")    # the last change first
        self.assertEqual(lines[3], "chcon -h a_u:object_r:user_tmp_t:s0 /etc/fapolicyd/trust.d/vendor-app || rc=1")
        self.assertEqual(lines[4:6], ["chown -h root:root /etc/fapolicyd/trust.d/vendor-app || rc=1", "chmod 0600 /etc/fapolicyd/trust.d/vendor-app || rc=1"])
        self.assertEqual(lines[-2:], ["return $rc", "}"])
        odd = ff.fapolicyd_fix_rollback([{"kind": "access", "path": "/etc/fapolicyd/trust.d/a b;rm", "from": "root:root 0600"}])
        self.assertIn("'/etc/fapolicyd/trust.d/a b;rm'", odd)


class ReportTest(unittest.TestCase):
    def test_read_only(self):
        r = ff.fapolicyd_fix_report([{"_ff": host(by_hand=["/etc/fapolicyd/trust.d/big-list: a large trust file, 64.5 MB"])},
                                     {"_ff": host(active="active", fixes=[])}, {"_ff": {"state": "not installed"}}, {}],
                                    ["fa01", "fa02", "fa04", "gone01"])
        s = {x["title"]: x for x in r["sections"]}
        self.assertEqual(r["status"], "critical")
        self.assertIn("5 fix(es) to approve on 1 host(s)", r["title"])
        self.assertIn("fapolicyd not running on 1", r["title"])
        self.assertEqual(s["Fixes to approve"]["lines"][0], "trust file access: /etc/fapolicyd/trust.d/vendor-app root:root 0600 -> root:fapolicyd 0644")
        self.assertTrue(s["Fixes to approve"]["lines"][-1].startswith("restart fapolicyd"))
        self.assertIn("big-list", s["For a person"]["lines"][0])
        self.assertEqual([x[0] for x in s["The hosts"]["rows"]], ["fa01", "fa02"])
        self.assertIn("fa04", s["fapolicyd not installed"]["lines"][0])
        self.assertIn("gone01", s["Not read"]["lines"][0])

    def test_fixed(self):
        h = host()
        p = ff.fapolicyd_fix_plan(h, [h["fixes"][0]["line"], "db_max_size: 50 -> 400", "restart fapolicyd (it is failed)",
                                      "permissive: 0 -> 1"])
        ap = {"state": "changed", "active_after": "active", "error": "", "applied": p["apply"], "not_here": [], "refused": [], "bad": []}
        r = ff.fapolicyd_fix_report([{"_ff": h, "_ff_apply": ap}], ["fa01"])
        s = {x["title"]: x for x in r["sections"]}
        row = s["What this run fixed"]["rows"][0]
        self.assertEqual((row[1], row[3]), ("changed", "failed -> active"))
        self.assertIn("db_max_size: 50 -> 400", row[2])
        # what was made is no longer to approve; the label, not approved, still is
        self.assertEqual(s["Fixes to approve"]["lines"], [h["fixes"][1]["line"]])
        self.assertNotIn("fapolicyd not running", r["title"])

    def test_approved_made_nowhere(self):
        h = host(active="active", fixes=[])
        ap = {"state": "unchanged", "error": "", "applied": [], "refused": [], "bad": [],
              "not_here": ["restart fapolicyd (it is failed)"]}
        r = ff.fapolicyd_fix_report([{"_ff": h, "_ff_apply": ap}, {"_ff": h, "_ff_apply": dict(ap)}], ["fa01", "fa02"])
        s = {x["title"]: x for x in r["sections"]}
        self.assertEqual(s["Approved, made on no host"]["lines"], ["restart fapolicyd (it is failed)"])

    def test_rolled_back(self):
        h = host(active="active")
        ap = {"state": "rolled back", "active_after": "active", "error": "fapolicyd failed after the fix: everything put back, fapolicyd active",
              "applied": [], "not_here": [], "refused": [], "bad": [], "journal": ["db_max_size needs to be increased"]}
        r = ff.fapolicyd_fix_report([{"_ff": h, "_ff_apply": ap}], ["fa03"], check=False)
        s = {x["title"]: x for x in r["sections"]}
        self.assertEqual(r["status"], "critical")
        self.assertIn("not fixed on 1 host(s)", r["title"])
        self.assertEqual(s["fa03: fapolicyd's journal"]["lines"], ["db_max_size needs to be increased"])


if __name__ == "__main__":
    unittest.main()
