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


# a podman that runs nothing: it logs each call, knows the images in $FAKE_IMAGES (one a line), and pull adds one
FAKE_PODMAN = r"""#!/bin/sh
echo "$*" >> "$FAKE_LOG"
case "$1 $2" in
  "--version "*) echo "podman version 5.2.0" ;;
  "container exists") [ -n "$FAKE_CT" ]; exit $? ;;
  "ps -a") echo "$FAKE_CT" ;;
  "image exists") grep -qxF "$3" "$FAKE_IMAGES"; exit $? ;;
  "pull "*) for a; do last="$a"; done; echo "$last" >> "$FAKE_IMAGES" ;;
  "exec -i") cat >/dev/null ;;
esac
exit 0
"""


class TheImage(unittest.TestCase):
    """1.50.0: the image is taken from this host. Missing: stops before anything is changed, with the steps."""

    def run_script(self, images, **env):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        bindir = os.path.join(d, "bin")
        os.mkdir(bindir)
        with open(os.path.join(bindir, "podman"), "w") as f:
            f.write(FAKE_PODMAN)
        os.chmod(os.path.join(bindir, "podman"), 0o755)
        with open(os.path.join(d, "images"), "w") as f:
            f.write("".join(i + "\n" for i in images))
        e = dict(os.environ, PATH=bindir + os.pathsep + os.environ["PATH"], FAKE_LOG=os.path.join(d, "log"),
                 FAKE_IMAGES=os.path.join(d, "images"), ALLOW_NON_ROOT="1", SKIP_SYSTEMD="1", SKIP_FIREWALL="1",
                 PORT="1", DATA_DIR=os.path.join(d, "data"), ENV_FILE=os.path.join(d, "report-db.env"),
                 NAME="sa-test-db")
        for k in ("IMAGE", "UPSTREAM", "PULL"):
            e.pop(k, None)
        e.update(env)
        r = subprocess.run(["bash", SCRIPT], capture_output=True, text=True, env=e, timeout=120, stdin=subprocess.DEVNULL)
        log = read(e["FAKE_LOG"]) if os.path.exists(e["FAKE_LOG"]) else ""
        return r, log, d

    def test_the_defaults(self):
        s = read(SCRIPT)
        self.assertIn('IMAGE="${IMAGE:-localhost/mysql:8.4}"', s)
        self.assertIn('UPSTREAM="${UPSTREAM:-docker.io/library/mysql:8.4}"', s)
        self.assertNotRegex(s, r"podman pull [^\n]*tls-verify=false")
        unit = s[s.index("[Container]"):s.index("[Service]")]
        self.assertIn("Image=$UNIT_IMAGE\n${PULL_LINE}PublishPort=", unit)
        self.assertRegex(s, r'podman run -d --name "\$NAME" --pull=never')

    def test_pull_never_in_the_unit_from_podman_4_6(self):
        # Quadlet in podman 4.4 and 4.5 refuses a unit with a key it does not know (Pull= came in 4.6)
        s = read(SCRIPT)
        i = s.index('    PULL_LINE=""')
        code = s[i:s.index("\n", s.index("then PULL_LINE=", i)) + 1]
        for ver, want in (("4.4.1", ""), ("4.5.2", ""), ("4.6.0", "Pull=never\n"), ("5.2.0", "Pull=never\n")):
            r = subprocess.run(["bash", "-c", "podver=%s\n%s printf '%%s' \"$PULL_LINE\"" % (ver, code)],
                               capture_output=True, text=True)
            self.assertEqual(r.stdout, want, (ver, r.stderr))

    def test_missing_stops_with_the_steps(self):
        r, log, d = self.run_script([])
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        err = r.stderr
        self.assertIn("the image localhost/mysql:8.4 is not on this host. Nothing was changed.", err)
        self.assertIn("podman pull docker.io/library/mysql:8.4", err)
        self.assertIn("podman save -o mysql-8.4.tar docker.io/library/mysql:8.4", err)
        self.assertIn("podman load -i mysql-8.4.tar", err)
        self.assertIn("podman tag docker.io/library/mysql:8.4 localhost/mysql:8.4", err)
        # nothing made, nothing pulled, nothing run
        self.assertFalse(os.path.exists(os.path.join(d, "data")))
        self.assertFalse(os.path.exists(os.path.join(d, "report-db.env")))
        self.assertFalse(os.path.exists(os.path.join(d, "report-db.container.env")))
        self.assertNotRegex(log, r"(?m)^(pull|run|create|start) ")

    def test_an_enclave_name(self):
        r, log, d = self.run_script([], IMAGE="registry.enclave-a.example.mil/mysql:8.4")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("podman tag docker.io/library/mysql:8.4 registry.enclave-a.example.mil/mysql:8.4", r.stderr)
        self.assertNotRegex(log, r"(?m)^pull ")

    def test_here_it_works(self):
        r, log, d = self.run_script(["localhost/mysql:8.4"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ok: on this host", r.stdout)
        self.assertNotRegex(log, r"(?m)^pull ")
        run = [ln for ln in log.splitlines() if ln.startswith("run ")]
        self.assertEqual(len(run), 1, log)
        self.assertIn("--pull=never", run[0])
        self.assertTrue(run[0].endswith(" localhost/mysql:8.4"), run[0])
        self.assertTrue(os.path.isdir(os.path.join(d, "data")))

    def test_pull_only_when_asked(self):
        r, log, d = self.run_script([], PULL="1", IMAGE="registry.enclave-a.example.mil/mysql:8.4")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("pull --tls-verify=true registry.enclave-a.example.mil/mysql:8.4", log)
        run = [ln for ln in log.splitlines() if ln.startswith("run ")]
        self.assertTrue(run and run[0].endswith(" registry.enclave-a.example.mil/mysql:8.4"), log)

    def test_a_running_install_keeps_its_image(self):
        # a container from before 1.50.0 on the pulled image: kept, no image check, nothing pulled, run or stopped
        r, log, d = self.run_script([], FAKE_CT="docker.io/library/mysql:8.4")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("runs docker.io/library/mysql:8.4 - kept (IMAGE is localhost/mysql:8.4", r.stdout)
        self.assertNotRegex(log, r"(?m)^(pull|run|stop|rm|image) ")

    def test_an_old_unit_is_kept_and_the_switch_said(self):
        s = read(SCRIPT)
        step = s[s.index('elif [ -f "/etc/containers/systemd/$NAME.container" ]'):s.index("else\n  say \"6. Start at boot\"")]
        step = "if false; then :\n" + step + "fi\n"
        with tempfile.TemporaryDirectory() as d:
            q = os.path.join(d, "report-db.container")
            unit = "[Container]\nContainerName=report-db\nImage=docker.io/library/mysql:8.4\n"
            with open(q, "w") as f:
                f.write(unit)
            code = ('say() { echo "== $*"; }; ok() { echo "ok: $*"; }; NAME=report-db; IMAGE=localhost/mysql:8.4; '
                    'SKIP_SYSTEMD=0\n' + step.replace("/etc/containers/systemd", d))
            r = subprocess.run(["bash", "-c", code], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("the unit names docker.io/library/mysql:8.4, not localhost/mysql:8.4 - kept, nothing restarted", r.stdout)
            self.assertIn("podman tag docker.io/library/mysql:8.4 localhost/mysql:8.4", r.stdout)
            self.assertIn("Pull=never", r.stdout)
            self.assertEqual(read(q), unit)                                    # the unit itself is not touched
            self.assertNotIn("systemctl restart", s[s.index("# ---- 6."):s.index("# ---- 7.")].replace(
                "systemctl restart %s   (the database is down", ""))


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
