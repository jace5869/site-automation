"""1.83.0, the STIG full scan's Windows part of win_stig.ps1, run with pwsh (no Windows): copy_to 'auto' - a private folder of
the run's own under TEMP, every checklist copied there (copied, copy_dir), in a dry run too; remove - only such a folder
(site-stig-full-* right under TEMP), never another. With the stand-in Evaluate-STIG of tests/windows/stig."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WIN = os.path.join(REPO, "roles", "win_check_stig", "files", "win_stig.ps1")
FAKE = os.path.join(HERE, "windows", "stig", "Evaluate-STIG.ps1")


@unittest.skipUnless(shutil.which("pwsh"), "no pwsh here")
class FullScanFolder(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.temp = os.path.join(self.d, "temp")
        os.makedirs(self.temp)
        es = os.path.join(self.d, "es", "Evaluate-STIG")
        os.makedirs(es)
        shutil.copy(FAKE, es)
        r = lambda sv, st: {"sv": sv, "v": "V-" + sv[3:], "stigid": "WN22-00-" + sv[3:], "sev": "medium", "title": sv, "status": st, "details": ""}
        with open(os.path.join(es, "spec.json"), "w") as f:
            json.dump({"host": "WIN01", "exit": 0, "checklists": [
                {"file": "WIN01_WinServer2022_V2R4", "stig_id": "Windows_Server_2022_STIG", "display_name": "Microsoft Windows Server 2022",
                 "version": "2", "release": "Release: 4 Benchmark Date: 02 Jul 2026", "rules": [r("SV-254238", "pass"), r("SV-254239", "fail")]},
                {"file": "WIN01_MSEdge_V2R2", "stig_id": "MS_Edge_STIG", "display_name": "Microsoft Edge", "version": "2",
                 "release": "Release: 2 Benchmark Date: 02 Jul 2026", "rules": [r("SV-235719", "fail")]}]}, f)
        self.es = es

    def run_ps(self, cfg):
        env = dict(os.environ, SITE_CHECK_CONFIG=json.dumps(cfg), TEMP=self.temp, TMPDIR=self.temp, ProgramData=os.path.join(self.d, "pd"))
        p = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-File", WIN], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=env, timeout=300, cwd=self.d)
        line = [x for x in p.stdout.decode().splitlines() if x.startswith("###SITE-JSON### ")]
        self.assertTrue(line, p.stdout.decode()[-2000:] + p.stderr.decode()[-2000:])
        return json.loads(line[-1][len("###SITE-JSON### "):])["facts"]

    def test_copy_to_auto_then_remove(self):
        facts = self.run_ps({"evaluate": self.es, "run_as": "direct", "dir": os.path.join(self.d, "kept"), "save": False,
                             "timeout": 120, "vulns": [], "history": False, "copy_to": "auto"})
        self.assertTrue(facts["scanned"], facts)
        folder = facts["copy_dir"]
        self.assertEqual(os.path.dirname(folder), self.temp)
        self.assertTrue(os.path.basename(folder).startswith("site-stig-full-"))
        copied = sorted(s["copied"] for s in facts["scans"])
        self.assertEqual(len(copied), 2, facts["scans"])
        for c in copied:
            self.assertEqual(os.path.dirname(c), folder)
            self.assertTrue(os.path.isfile(c), c)
        self.assertFalse(os.path.exists(os.path.join(self.d, "kept")))          # a dry run (save false): nothing kept beside it
        self.assertRegex(facts["copy_token"], "^[0-9a-f]{32}$")
        kept = self.run_ps({"remove": folder})                                          # no owner mark given: left
        self.assertFalse(kept["removed"], kept)
        kept = self.run_ps({"remove": folder, "remove_token": "0" * 32})                # another run's mark: left
        self.assertFalse(kept["removed"], kept)
        self.assertTrue(os.path.isdir(folder))
        gone = self.run_ps({"remove": folder, "remove_token": facts["copy_token"]})
        self.assertTrue(gone["removed"], gone)
        self.assertFalse(os.path.exists(folder))

    def test_remove_only_its_own_folder(self):
        """Codex (1.83.0): never a file of that name, a folder elsewhere, another name, or a link to a folder."""
        hexname = "site-stig-full-" + "0f" * 16
        target = os.path.join(self.d, "keep-target")
        os.makedirs(target)
        open(os.path.join(target, "precious.txt"), "w").close()
        notes = os.path.join(self.temp, "site-stig-full-notes.txt")
        open(notes, "w").close()
        cases = [os.path.join(self.temp, "keep-me"), os.path.join(self.d, hexname), os.path.join(self.temp, "site-stig-full-abc"), notes]
        for p in cases[:3]:
            os.makedirs(p)
        cases.append(os.path.join(self.temp, "site-stig-full-" + "2d" * 16))      # right name, right place, no owner mark
        os.makedirs(cases[-1])
        link = os.path.join(self.temp, "site-stig-full-" + "1e" * 16)
        os.symlink(target, link)
        for p in cases + [link]:
            with self.subTest(path=p):
                facts = self.run_ps({"remove": p, "remove_token": "ab" * 16})
                self.assertFalse(facts["removed"], facts)
                self.assertIn("is not a folder of the STIG full scan", facts["why"])
                self.assertTrue(os.path.lexists(p))
        self.assertTrue(os.path.isfile(os.path.join(target, "precious.txt")))

if __name__ == "__main__":
    unittest.main()
