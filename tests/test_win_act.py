#!/usr/bin/env python3
"""ACT for Windows (roles/win_act): a junction or symbolic link at win_act_dir is refused before anything is changed.
The install, the removal and the state check each look for a reparse point at the folder before the first takeown,
icacls, file write, Remove-Item or Get-Acl. Where PowerShell 7 (pwsh) is installed, the scripts also run against a
symbolic link here: the install and the removal fail with a plain message and leave the link's target as it was, the
state check says the folder is a link.
Run: python tests/test_win_act.py"""
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROLE = os.path.join(REPO, "roles", "win_act")
SCRIPTS = {"install": os.path.join(ROLE, "templates", "win_act_install.ps1.j2"),
           "remove": os.path.join(ROLE, "files", "win_act_remove.ps1"),
           "state": os.path.join(ROLE, "files", "win_act_state.ps1")}
# what acts on the folder (or reads it through a link): none of it before the check
ACTS = re.compile(r"takeown|icacls|WriteAllBytes|Remove-Item|Move-Item|Get-Acl|Get-FileHash|New-Item")
PWSH = shutil.which("pwsh")


def code_lines(path):
    with open(path, encoding="utf-8") as fh:
        return [ln for ln in fh.read().splitlines() if not ln.lstrip().startswith("#")]


def render_install(text):
    """The installer as roles/win_act builds it, with a tiny act.ps1 inside."""
    b64 = base64.b64encode(b"'act'\n").decode()
    return re.sub(r"\{% for .*?%\}.*?\{% endfor %\}\n", "\n".join(re.findall(".{1,76}", b64)) + "\n", text, flags=re.S)


class TheOrder(unittest.TestCase):
    def test_the_link_check_comes_first(self):
        for name, path in SCRIPTS.items():
            lines = code_lines(path)
            check = next((i for i, ln in enumerate(lines) if "ReparsePoint" in ln), None)
            self.assertIsNotNone(check, "%s: no reparse point check" % name)
            first = next((i for i, ln in enumerate(lines) if ACTS.search(ln)), None)
            self.assertIsNotNone(first, name)
            self.assertLess(check, first, "%s: %r comes before the link check" % (name, lines[first]))
            got = next(ln for ln in lines if "Get-Item -LiteralPath $dir" in ln)
            self.assertIn("-Force", got, name)                 # a hidden link too


@unittest.skipUnless(PWSH, "pwsh not installed")
class WithPwsh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sa-win-act-")
        self.target = os.path.join(self.tmp, "someone-elses")
        os.makedirs(self.target)
        with open(os.path.join(self.target, "keep.txt"), "w", encoding="utf-8") as fh:
            fh.write("kept")
        self.link = os.path.join(self.tmp, "act")
        os.symlink(self.target, self.link)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_ps(self, script, cfg):
        env = dict(os.environ, SITE_CHECK_CONFIG=json.dumps(cfg), NO_COLOR="1")
        return subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", script], capture_output=True, text=True,
                              env=env, timeout=120)

    def test_remove_refuses_a_link(self):
        r = self.run_ps(SCRIPTS["remove"], {"dir": self.link})
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("is a link", r.stdout + r.stderr)
        self.assertTrue(os.path.islink(self.link))
        self.assertTrue(os.path.exists(os.path.join(self.target, "keep.txt")))
        # a plain folder is still removed
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        r = self.run_ps(SCRIPTS["remove"], {"dir": plain})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(plain))

    def test_install_refuses_a_link(self):
        script = os.path.join(self.tmp, "install.ps1")
        with open(SCRIPTS["install"], encoding="utf-8") as fh:
            text = render_install(fh.read())
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(text)
        r = self.run_ps(script, {"dir": self.link, "sha256": "00"})
        self.assertNotEqual(r.returncode, 0, r.stdout)
        out = r.stdout + r.stderr
        self.assertIn("is a link", out)
        self.assertNotIn("takeown", out)                       # stopped before it
        self.assertEqual(sorted(os.listdir(self.target)), ["keep.txt"])

    def test_state_says_it_is_a_link(self):
        r = self.run_ps(SCRIPTS["state"], {"dir": self.link, "sha256": "00"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        line = next(ln for ln in r.stdout.splitlines() if ln.startswith("###SITE-JSON### "))
        res = json.loads(line.split(" ", 1)[1])
        self.assertFalse(res["ok"])
        self.assertFalse(res["dir_ok"])
        self.assertTrue(any("is a link" in p for p in res["problems"]), res["problems"])


if __name__ == "__main__":
    unittest.main()
