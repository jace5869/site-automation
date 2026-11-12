#!/usr/bin/env python3
"""Tests for the report database's host side (1.16.0): tools/report-db-setup.sh (bash -n, ShellCheck when installed, the
firewall step: rules only when missing, the reload only then, podman's network rules put back after it, names looked up
to addresses) and docs/REPORT_DATABASE.md saying the same; the EEs' PyMySQL pin and check-ee.sh's line for PyMySQL
1.2.1 to 1.2.3 (the first login after a server restart fails). The script is not run here: it needs root, podman and
firewalld. python3 tests/test_report_db_setup.py"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
SCRIPT = os.path.join(REPO, "tools", "report-db-setup.sh")
DOC = os.path.join(REPO, "docs", "REPORT_DATABASE.md")


def read(p):
    with open(p) as f:
        return f.read()


class SetupScript(unittest.TestCase):

    def test_bash_n(self):
        r = subprocess.run(["bash", "-n", SCRIPT], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_shellcheck(self):
        sc = shutil.which("shellcheck") or shutil.which("shellcheck", path=os.path.dirname(sys.executable))
        if not sc:
            self.skipTest("shellcheck is not installed (pip install shellcheck-py)")
        r = subprocess.run([sc, "-S", "warning", SCRIPT], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_strict_and_executable(self):
        s = read(SCRIPT)
        self.assertTrue(s.startswith("#!/usr/bin/env bash\n"))
        self.assertIn("set -euo pipefail", s)
        self.assertTrue(os.access(SCRIPT, os.X_OK))

    def test_the_firewall_step(self):
        s = read(SCRIPT)
        fw = s[s.index("# ---- 7. firewall"):s.index("# ---- 8.")]
        self.assertIn("--query-rich-rule", fw)                               # a rule only when it is missing
        self.assertIn('if [ "$added" = 1 ]; then', fw)                       # the reload only then
        self.assertLess(fw.index("firewall-cmd --reload"), fw.index("podman network reload --all"))
        self.assertIn("getent ahostsv4", fw)                                 # a name is looked up: firewalld takes addresses
        self.assertIn("source address=$addr", fw)

    def test_never_prunes_or_touches_others(self):
        s = read(SCRIPT)
        self.assertNotRegex(s, r"podman\s+(\w+\s+)?prune")
        for m in re.finditer(r"podman (stop|rm|start) (\S+)", s):
            self.assertEqual(m.group(2), '"$NAME"', m.group(0))


class TheDoc(unittest.TestCase):

    def test_points_at_the_script(self):
        d = read(DOC)
        self.assertIn("tools/report-db-setup.sh", d)
        self.assertNotIn("given to you outside the repository", d)

    def test_the_firewall_by_hand(self):
        d = read(DOC)
        step = d[d.index("2. **The firewall.**"):d.index("3. **The two accounts.**")]
        self.assertLess(step.index("firewall-cmd --reload"), step.index("podman network reload --all"))
        self.assertIn("INVALID_ADDR", step)
        self.assertIn("addresses", step)

    def test_the_errors_table(self):
        d = read(DOC)
        t = d[d.index("## When something goes wrong"):]
        t = t[:t.index("\n## ")]
        self.assertIn("is_auth_switch_request", t)
        self.assertIn("PyMySQL 1.2.4", t)
        self.assertIn("podman network reload --all", t)


class PyMySQLPin(unittest.TestCase):
    """PyMySQL 1.2.1 to 1.2.3 can not be picked for an EE; check-ee.sh says no when the EE has one of them."""

    def test_requirements(self):
        for ee in ("all-in-one", "windows"):
            self.assertRegex(read(os.path.join(REPO, "ee", ee, "requirements.txt")), r"(?m)^PyMySQL>=1\.2\.4\b", ee)

    def check_ee(self, ee, version):
        s = read(os.path.join(REPO, "ee", ee, "check-ee.sh"))
        block = s[s.index("  # PyMySQL 1.2.1 to 1.2.3"):]
        block = block[:block.index("  esac") + len("  esac")]
        with tempfile.TemporaryDirectory() as d:
            py = os.path.join(d, "py")
            with open(py, "w") as f:
                f.write("#!/bin/sh\necho %s\n" % version)
            os.chmod(py, 0o755)
            r = subprocess.run(["bash", "-c", 'yes() { echo "YES $*"; }; no() { echo "NO $*"; }; py=%s\n%s' % (py, block)],
                               capture_output=True, text=True)
        return r.stdout.strip()

    def test_check_ee(self):
        for ee in ("all-in-one", "windows"):
            for v in ("1.2.1", "1.2.2", "1.2.3"):
                out = self.check_ee(ee, v)
                self.assertTrue(out.startswith("NO PyMySQL %s" % v), out)
                self.assertIn("PyMySQL>=1.2.4", out)
            for v in ("1.2.0", "1.2.4", "1.1.1"):
                self.assertTrue(self.check_ee(ee, v).startswith("YES PyMySQL %s" % v), (ee, v))


if __name__ == "__main__":
    unittest.main(verbosity=1)
