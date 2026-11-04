#!/usr/bin/env python3
"""Tests for "Evaluate-STIG - update" (playbooks/evaluate_stig_update.yml, roles/evaluate_stig_update): the zip read on
the AAP side (its version, its RHEL STIG content, the newest of several, a zip that is not Evaluate-STIG's), each
host's Evaluate-STIG read (update, up to date, newer, not installed, version unknown, a scan running), and the
update itself on a folder of a test's own - the answer files carried over, the old folder kept and pruned, a quick
run that fails putting the old one back, a zip that does not match. Then the playbook against localhost: Check
changes nothing, then it updates, then it is up to date. Invented Evaluate-STIG zips only.
Needs ansible-core: python3 tests/test_evaluate_stig_update.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(REPO, "roles", "evaluate_stig_update", "library"))
import site_evaluate_stig as m  # noqa: E402

BASH_OK = "#!/bin/bash\necho RHEL8\necho RHEL9\n"


def make_zip(path, version, rhel8="V2R9", top="Evaluate-STIG/", bash=BASH_OK, extra=None, src=False):
    """An invented Evaluate-STIG zip: its script with the version, the bash script, STIG content, a stock answer file."""
    script = ("Src/Evaluate-STIG/" if src else "") + "Evaluate-STIG.ps1"
    files = {script: "<#\n# # Name:        Evaluate-STIG (test)\n# # Version:     %s\n#>\n#requires -version 5.1\n" % version,
             "Evaluate-STIG_Bash.sh": bash,
             "StigContent/U_RHEL_8_STIG_%s_Manual-xccdf.xml" % rhel8: "<x/>",
             "StigContent/U_RHEL_9_STIG_V2R4_Manual-xccdf.xml": "<x/>",
             "AnswerFiles/RHEL8_Answers.xml": "<stock/>",
             "Preferences.xml": "<prefs version='%s'/>" % version}
    files.update(extra or {})
    with zipfile.ZipFile(path, "w") as z:
        for name, text in files.items():
            info = zipfile.ZipInfo(top + name)
            info.external_attr = (0o755 if name.endswith(".sh") else 0o644) << 16
            z.writestr(info, text)
    return path


def params(**kw):
    p = {"dest": "", "zip": "", "want": "", "sha256": "", "force": False, "install_missing": False, "keep": ["AnswerFiles"],
         "backups": 1, "smoke": "auto", "smoke_timeout": 60, "pwsh": "", "version_regex": m.VERSION_RE, "proc": "/nonexistent"}
    p.update(kw)
    return p


class Parts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def installed(self, version, answers=None):
        """A host's Evaluate-STIG of this version in tmp/opt/Evaluate-STIG, with the site's own answer files."""
        z = make_zip(os.path.join(self.tmp, "old.zip"), version, rhel8="V2R8")
        root = os.path.join(self.tmp, "opt", "Evaluate-STIG")
        os.makedirs(root)
        m.unpack(z, "Evaluate-STIG/", root)
        for name, text in (answers or {}).items():
            with open(os.path.join(root, "AnswerFiles", name), "w") as fh:
                fh.write(text)
        os.remove(z)
        return root

    def test_versions(self):
        self.assertTrue(m.newer("1.2507.0", "1.2504.1"))
        self.assertFalse(m.newer("1.2504", "1.2504.0"))
        self.assertEqual(m.vtuple("1.2504.0"), m.vtuple("1.2504"))
        self.assertIsNone(m.newer("1.2507", "unknown"))
        self.assertEqual(m.version_in('$EvaluateStigVersion = "1.2507.0"'), "1.2507.0")
        header = "<#\n# # Version:     1.2604.0\n#>\n#requires -version 5.1\n -OutputPayload <\"Version\"|\"ESVersion\">\n"
        self.assertEqual(m.version_in(header), "1.2604.0")                 # the header, not "-version 5.1"
        self.assertEqual(m.version_in("#requires -version 5.1\n"), "")
        self.assertEqual(m.content_of(["U_RHEL_8_STIG_V2R8_Manual-xccdf.xml", "a/U_RHEL_8_STIG_V2R9_Manual-xccdf.xml",
                                       "U_RHEL_9_V2R4_STIG.xml", "other.xml"]), {"RHEL 8": "V2R9", "RHEL 9": "V2R4"})

    def test_the_zip(self):
        z = make_zip(os.path.join(self.tmp, "Evaluate-STIG_1.2507.0.zip"), "1.2507.0")
        i = m.zip_info(z)
        self.assertEqual((i["error"], i["version"], i["from"], i["prefix"], i["script"]),
                         ("", "1.2507.0", "Evaluate-STIG.ps1", "Evaluate-STIG/", "Evaluate-STIG.ps1"))
        self.assertEqual(i["content"], {"RHEL 8": "V2R9", "RHEL 9": "V2R4"})
        # several: the newest version, the others named
        make_zip(os.path.join(self.tmp, "Evaluate-STIG_1.2504.0.zip"), "1.2504.0")
        best, others = m.pick_zip(self.tmp)
        self.assertEqual((best["version"], others), ("1.2507.0", ["Evaluate-STIG_1.2504.0.zip"]))
        # no version in the script: the file name
        os.makedirs(os.path.join(self.tmp, "x"))
        n = make_zip(os.path.join(self.tmp, "x", "Evaluate-STIG_1.2510.0.zip"), "1.2510.0")
        self.assertEqual(m.zip_info(n, r"NoSuchVersion(\d)")["from"], "the file name")
        # not Evaluate-STIG's; nothing there
        bad = os.path.join(self.tmp, "bad.zip")
        with zipfile.ZipFile(bad, "w") as zz:
            zz.writestr("readme.txt", "x")
        self.assertIn("no Evaluate-STIG.ps1", m.zip_info(bad)["error"])
        os.makedirs(os.path.join(self.tmp, "empty"))
        self.assertIn("no .zip", m.pick_zip(os.path.join(self.tmp, "empty"))[0]["error"])
        evil = os.path.join(self.tmp, "evil.zip")
        with zipfile.ZipFile(evil, "w") as zz:
            zz.writestr("../etc/x", "x")
            zz.writestr("Evaluate-STIG.ps1", "x")
        self.assertIn("outside its folder", m.zip_info(evil)["error"])

    def test_a_zip_with_files_beside_its_folder(self):
        """The real zip's layout: the Evaluate-STIG folder and files beside it - only the folder goes on the host."""
        z = make_zip(os.path.join(self.tmp, "Evaluate-STIG_1.2610.1.zip"), "1.2610.1")
        with zipfile.ZipFile(z, "a") as zz:
            zz.writestr("Evaluate-STIG_1.2610.1.zip.sha256", "abc")
            zz.writestr("Docs/Evaluate-STIG User Guide.pdf", "pdf")
        i = m.zip_info(z)
        self.assertEqual((i["error"], i["prefix"], i["script"]), ("", "Evaluate-STIG/", "Evaluate-STIG.ps1"))
        self.assertEqual(i["left_out"], ["Evaluate-STIG_1.2610.1.zip.sha256", "Docs/Evaluate-STIG User Guide.pdf"])
        root = os.path.join(self.tmp, "software", "scripts", "Evaluate-STIG")
        r = m.install(params(dest=root, zip=z, want="1.2610.1", install_missing=True))
        self.assertEqual(r["error"], "")
        self.assertTrue(os.path.isfile(os.path.join(root, "Evaluate-STIG.ps1")))
        self.assertFalse(os.path.exists(os.path.join(root, "Docs")))
        self.assertFalse(os.path.exists(os.path.join(root, "Evaluate-STIG")))

    def test_root_of(self):
        self.assertEqual(m.root_of("/opt/Evaluate-STIG/Src/Evaluate-STIG"), "/opt/Evaluate-STIG")
        self.assertEqual(m.root_of("/opt/Evaluate-STIG/Evaluate-STIG_Bash.sh"), "/opt/Evaluate-STIG")
        self.assertEqual(m.root_of("/opt/Evaluate-STIG/"), "/opt/Evaluate-STIG")

    def test_inspect(self):
        root = self.installed("1.2504.0")
        r = m.inspect(root, "1.2507.0", proc="/nonexistent")
        self.assertEqual((r["decision"], r["version"], r["content"]["RHEL 8"]), ("update", "1.2504.0", "V2R8"))
        self.assertEqual(m.inspect(root, "1.2504.0", proc="/nonexistent")["decision"], "current")
        self.assertEqual(m.inspect(root, "1.2501.0", proc="/nonexistent")["decision"], "newer")
        self.assertEqual(m.inspect(root, "1.2501.0", force=True, proc="/nonexistent")["decision"], "update")
        self.assertEqual(m.inspect(os.path.join(self.tmp, "none"), "1.2507.0", proc="/nonexistent")["decision"], "missing")
        self.assertEqual(m.inspect(os.path.join(self.tmp, "none"), "1.2507.0", install_missing=True, proc="/nonexistent")["decision"], "update")
        with open(os.path.join(root, "Evaluate-STIG.ps1"), "w") as fh:
            fh.write("# no version here\n")
        self.assertEqual(m.inspect(root, "1.2507.0", proc="/nonexistent")["decision"], "unknown")
        # a scan running now: left for later
        proc = os.path.join(self.tmp, "proc")
        os.makedirs(os.path.join(proc, "4242"))
        with open(os.path.join(proc, "4242", "cmdline"), "wb") as fh:
            fh.write(b"/bin/bash\0/opt/Evaluate-STIG/Evaluate-STIG_Bash.sh\0--Output\0CKLB")
        r = m.inspect(root, "1.2507.0", force=True, proc=proc)
        self.assertEqual((r["decision"], r["running"]), ("running", [4242]))

    def test_install_carries_answers_keeps_and_prunes(self):
        root = self.installed("1.2504.0", {"SITE_RHEL8.xml": "<mine/>", "RHEL8_Answers.xml": "<changed by us/>"})
        z = make_zip(os.path.join(self.tmp, "new.zip"), "1.2507.0")
        r = m.install(params(dest=root, zip=z, want="1.2507.0", sha256=m._sha256(z)))
        self.assertEqual(r["error"], "")
        self.assertTrue(r["changed"])
        self.assertEqual((r["version_before"], r["version"]), ("1.2504.0", "1.2507.0"))
        self.assertEqual((r["content_before"]["RHEL 8"], r["content"]["RHEL 8"]), ("V2R8", "V2R9"))
        self.assertEqual(r["previous"], root + ".previous-1.2504.0")
        self.assertEqual(r["kept"], [os.path.join("AnswerFiles", "SITE_RHEL8.xml")])
        self.assertEqual(r["replaced"], [os.path.join("AnswerFiles", "RHEL8_Answers.xml")])
        self.assertIn("Preferences.xml differs", r["preferences"])
        self.assertIn("Evaluate-STIG_Bash.sh --ListSupportedProducts ran (2 lines)", r["smoke"])
        self.assertEqual(open(os.path.join(root, "AnswerFiles", "SITE_RHEL8.xml")).read(), "<mine/>")
        self.assertEqual(oct(os.stat(os.path.join(root, "Evaluate-STIG_Bash.sh")).st_mode & 0o777), "0o755")
        self.assertTrue(os.path.isdir(r["previous"]))
        self.assertFalse([n for n in os.listdir(os.path.dirname(root)) if ".new-" in n])
        # the next update: one earlier folder kept, the oldest gone
        z2 = make_zip(os.path.join(self.tmp, "newer.zip"), "1.2510.0")
        r2 = m.install(params(dest=root, zip=z2, want="1.2510.0"))
        self.assertEqual(r2["pruned"], ["Evaluate-STIG.previous-1.2504.0"])
        self.assertEqual(sorted(n for n in os.listdir(os.path.dirname(root))), ["Evaluate-STIG", "Evaluate-STIG.previous-1.2507.0"])
        # same version again: nothing
        self.assertEqual(m.install(params(dest=root, zip=z2, want="1.2510.0"))["decision"], "current")

    def test_a_quick_run_that_fails_puts_the_old_one_back(self):
        root = self.installed("1.2504.0")
        z = make_zip(os.path.join(self.tmp, "new.zip"), "1.2507.0", bash="#!/bin/bash\necho broken >&2\nexit 3\n")
        r = m.install(params(dest=root, zip=z, want="1.2507.0"))
        self.assertFalse(r["changed"])
        self.assertIn("did not run", r["error"])
        self.assertIn("the old one is back in place", r["error"])
        self.assertEqual(m.inspect(root, "1.2507.0", proc="/nonexistent")["version"], "1.2504.0")
        self.assertEqual(sorted(os.listdir(os.path.dirname(root))), ["Evaluate-STIG"])

    def test_refused_before_anything_changes(self):
        root = self.installed("1.2504.0")
        z = make_zip(os.path.join(self.tmp, "new.zip"), "1.2507.0")
        r = m.install(params(dest=root, zip=z, want="1.2507.0", sha256="0" * 64))
        self.assertIn("does not match", r["error"])
        r = m.install(params(dest=root, zip=z, want="1.2509.0"))               # the zip is not the version read
        self.assertIn("says 1.2507.0, not 1.2509.0", r["error"])
        self.assertEqual(m.inspect(root, "1.2504.0", proc="/nonexistent")["version"], "1.2504.0")
        self.assertEqual(sorted(os.listdir(os.path.dirname(root))), ["Evaluate-STIG"])

    def test_src_layout(self):
        z = make_zip(os.path.join(self.tmp, "src.zip"), "1.2507.0", src=True)
        root = os.path.join(self.tmp, "opt", "Evaluate-STIG")
        r = m.install(params(dest=root + "/Src/Evaluate-STIG", zip=z, want="1.2507.0", install_missing=True))
        self.assertEqual(r["error"], "")
        self.assertTrue(os.path.isfile(os.path.join(root, "Src", "Evaluate-STIG", "Evaluate-STIG.ps1")))
        self.assertEqual(r["previous"], "")


class Playbook(unittest.TestCase):
    """playbooks/evaluate_stig_update.yml against localhost (no become), on a folder of the test's own."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.zips = os.path.join(self.tmp, "zips")
        os.makedirs(self.zips)
        z = make_zip(os.path.join(self.zips, "Evaluate-STIG_1.2507.0.zip"), "1.2507.0")
        with zipfile.ZipFile(z, "a") as zz:
            zz.writestr("Evaluate-STIG_1.2507.0.zip.sha256", "abc")              # beside the folder, as the real zip has
        old = make_zip(os.path.join(self.tmp, "old.zip"), "1.2504.0", rhel8="V2R8")
        self.root = os.path.join(self.tmp, "opt", "Evaluate-STIG")
        os.makedirs(self.root)
        m.unpack(old, "Evaluate-STIG/", self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_pb(self, *args, **extra):
        ap = os.environ.get("ANSIBLE_PLAYBOOK", "ansible-playbook")
        ev = {"ansible_python_interpreter": sys.executable, "ansible_become": False, "evaluate_stig_update_zip": self.zips,
              "check_stig_evaluate": self.root, "evaluate_stig_update_tmp": self.tmp}
        ev.update(extra)
        env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="False", ANSIBLE_INVENTORY_UNPARSED_WARNING="False")
        return subprocess.run([ap, "playbooks/evaluate_stig_update.yml", "-i", "localhost,", "-c", "local", "-e", json.dumps(ev)] + list(args),
                              cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=600)

    def test_check_then_update_then_up_to_date(self):
        out = self.run_pb("--check")
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn("Evaluate-STIG 1.2507.0 (read from Evaluate-STIG.ps1): Evaluate-STIG_1.2507.0.zip", out.stdout)
        self.assertIn("RHEL STIG content: RHEL 8 V2R9, RHEL 9 V2R4", out.stdout)
        self.assertIn("Unpacked on the hosts: Evaluate-STIG/ (where Evaluate-STIG.ps1 is); not unpacked, beside it: "
                      "Evaluate-STIG_1.2507.0.zip.sha256", out.stdout)
        self.assertIn("localhost: would be updated: 1.2504.0 -> 1.2507.0", out.stdout)
        self.assertEqual(m.inspect(self.root, "", proc="/nonexistent")["version"], "1.2504.0")
        out = self.run_pb()
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn("localhost: updated 1.2504.0 -> 1.2507.0 (RHEL STIG content RHEL 8 V2R8, RHEL 9 V2R4 -> RHEL 8 V2R9, RHEL 9 V2R4)", out.stdout)
        self.assertIn("the old one kept as %s.previous-1.2504.0" % self.root, out.stdout)
        self.assertIn("After the update: run the STIG recheck", out.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "Evaluate-STIG_1.2507.0.zip")))          # the copy, deleted
        out = self.run_pb()
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn("localhost: 1.2507.0: up to date", out.stdout)

    def test_failed_quick_run_fails_the_job(self):
        out = self.run_pb(evaluate_stig_update_smoke="exit 3")
        self.assertNotEqual(out.returncode, 0, out.stdout)
        self.assertIn("NOT UPDATED - the new Evaluate-STIG did not run", out.stdout)
        self.assertEqual(m.inspect(self.root, "", proc="/nonexistent")["version"], "1.2504.0")

    def test_no_folder_no_zip(self):
        out = self.run_pb(check_stig_evaluate="")
        self.assertIn("no Evaluate-STIG folder", out.stdout)
        out = self.run_pb(evaluate_stig_update_zip=os.path.join(self.tmp, "nothing"))
        self.assertIn("is not there (evaluate_stig_update_zip)", out.stdout)


class NotInThePublicRepo(unittest.TestCase):
    @unittest.skipUnless(shutil.which("git") and os.path.exists(os.path.join(REPO, ".git")),
                         "not a git checkout")
    def test_no_evaluate_stig_zip_tracked(self):
        files = subprocess.run(["git", "ls-files"], cwd=REPO, stdout=subprocess.PIPE, universal_newlines=True).stdout.split("\n")
        self.assertEqual([f for f in files if f.lower().endswith(".zip")], [])


if __name__ == "__main__":
    unittest.main()
