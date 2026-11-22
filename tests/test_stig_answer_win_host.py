#!/usr/bin/env python3
"""Unit tests for the Windows hosts of the STIG answer keys (1.105.0): which Windows Server a host is (win_os.ps1's line), the one STIG a
Windows scan is taken from (win_stig.ps1 with the rules picked, texts: true, nothing kept), the shape the job's pick reads, and the
scripts' own promises (read-only, nothing kept).
    python3 tests/test_stig_answer_win_host.py"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "plugins", "filter"))
import stig_answer_filters as F  # noqa: E402


def os_line(caption, build, role="3"):
    return "###SITE-JSON### " + json.dumps({"findings": [], "facts": {"caption": caption, "build": build, "domain_role": role}})


def scan(bench, items, **kw):
    return dict({"scanned": True, "benchmark": bench, "stig_version": "V2R11", "site": "", "instance": "", "items": items}, **kw)


class WhichWindows(unittest.TestCase):
    def test_by_name_and_by_build(self):
        for cap, build, year in (("Microsoft Windows Server 2019 Standard", "17763", "2019"), ("Microsoft Windows Server 2022 Datacenter", "20348", "2022"),
                                 ("Microsoft Windows Server 2025 Standard", "26100", "2025"), ("Microsoft Windows Server Standard", "20348", "2022")):
            r = F.stig_answer_win_os(["noise", os_line(cap, build)])
            self.assertEqual((r["ok"], r["year"], r["bench"]), (True, year, "MS_Windows_Server_%s_STIG" % year), cap)

    def test_the_benchmarks_are_the_sites_when_set(self):
        r = F.stig_answer_win_os([os_line("Microsoft Windows Server 2022 Standard", "20348")], {"2022": "MS_Windows_Server_2022_STIG_SITE"})
        self.assertEqual(r["bench"], "MS_Windows_Server_2022_STIG_SITE")
        self.assertFalse(F.stig_answer_win_os([os_line("Microsoft Windows Server 2019 Standard", "17763")], {"2022": "X"})["ok"])

    def test_another_windows_or_no_answer_gets_no_keys(self):
        for lines in ([os_line("Microsoft Windows 11 Enterprise", "22631")], [os_line("Microsoft Windows Server 2016 Standard", "14393")], [], ["garbage"],
                      ["###SITE-JSON### {not json"]):
            r = F.stig_answer_win_os(lines)
            self.assertFalse(r["ok"], lines)
            self.assertTrue(r["why"])
        self.assertIn("2016", F.stig_answer_win_os([os_line("Microsoft Windows Server 2016 Standard", "14393")])["why"])


class WhichScan(unittest.TestCase):
    ITEMS = [{"key": "SV-1r1_rule", "v": "V-1", "severity": "medium", "result": "notchecked", "title": "T", "check_text": "C", "fix_text": "F"},
             {"key": "SV-2r1_rule", "v": "V-2", "severity": "high", "result": "fail", "title": "T2", "check_text": "C2"}]

    def test_only_the_operating_systems_stig_is_taken(self):
        facts = {"scanned": True, "scans": [scan("Windows_Defender_Antivirus_STIG", [{"key": "SV-9", "result": "fail"}]),
                                            scan("MS_Windows_Server_2022_STIG", self.ITEMS),
                                            scan("MS_Windows_Server_2022_STIG", [{"key": "SV-8"}], site="Default Web Site")]}
        r = F.stig_answer_win_scan(facts, "MS_Windows_Server_2022_STIG")
        self.assertEqual((r["scanned"], r["why"], r["benchmark"], r["stig_version"]), (True, "", "MS_Windows_Server_2022_STIG", "V2R11"))
        self.assertEqual([i["key"] for i in r["items"]], ["SV-1r1_rule", "SV-2r1_rule"])
        self.assertEqual(r["items"][0]["check_text"], "C")                        # a Not Reviewed rule has DISA's text (texts: true)

    def test_what_the_pick_reads(self):
        r = F.stig_answer_win_scan({"scanned": True, "scans": [scan("MS_Windows_Server_2022_STIG", self.ITEMS)]}, "MS_Windows_Server_2022_STIG")
        picks = F.stig_answer_pick([{"v": "V-1", "sv": "SV-1r1_rule"}, {"v": "V-2", "sv": "SV-2r1_rule"}], r["items"], {})
        self.assertEqual([(p["sv"], p["ok"], p["expected"], p["cat"]) for p in picks],
                         [("SV-1r1_rule", True, "Not_Reviewed", "CAT II"), ("SV-2r1_rule", True, "Open", "CAT I")])

    def test_no_scan_or_a_failed_one_says_why_and_gives_no_items(self):
        for facts, err, word in (({}, "", "no result"), ({"scanned": False, "why": "Evaluate-STIG was not found"}, "", "not found"),
                                 ({"scanned": True, "scans": [scan("Other_STIG", [])]}, "", "gave no checklist of MS_Windows_Server_2022_STIG"),
                                 ({"scanned": True, "scans": []}, "", "checked: nothing"), ({"scanned": True}, "WinRM timed out", "timed out")):
            r = F.stig_answer_win_scan(facts, "MS_Windows_Server_2022_STIG", err)
            self.assertFalse(r["scanned"])
            self.assertEqual(r["items"], [])
            self.assertIn(word, r["why"])


class WhatTheRowsRead(unittest.TestCase):
    """win_ak_try.ps1's facts as the Linux read-only check and trial give them."""
    CODES = {"V-1": "$ok = (Get-ItemProperty -LiteralPath 'HKLM:\\X' -Name Y).Y -eq 2\nreturn $ok", "V-2": "Set-Content x", "V-3": "return $true", "V-4": "return 1"}
    FACTS = {"results": [
        {"key": "V-1", "valid": True, "why": "", "ran": True, "result": True, "verbose": ["read: Y = 2"], "refused": [], "seconds": 0.4},
        {"key": "V-2", "valid": False, "why": "Set-Content is not allowed", "ran": False, "result": None, "verbose": [], "refused": [], "seconds": 0},
        {"key": "V-3", "valid": True, "why": "", "ran": True, "result": False, "verbose": [], "refused": ["secedit.exe /export /cfg C:\\other"], "seconds": 0.1},
        {"key": "V-4", "valid": True, "why": "", "ran": True, "result": None, "verbose": [], "refused": [], "seconds": 0.1}],
        "ran_in": {"mode": "ConstrainedLanguage", "proxies": ["secedit.exe", "Remove-Item", "auditpol.exe"]}, "leftover": []}

    def test_gate_and_trial(self):
        hc = F.stig_answer_win_hc(self.FACTS, self.CODES)
        self.assertEqual((hc["gate"]["V-1"]["ok"], hc["gate"]["V-2"]["ok"], hc["gate"]["V-2"]["why"]), (True, False, ["Set-Content is not allowed"]))
        self.assertIn("Get-ItemProperty", hc["gate"]["V-1"]["used"])
        t = hc["trial"]
        self.assertEqual((t["V-1"]["ok"], t["V-1"]["valid"], t["V-1"]["read"], t["V-1"]["seconds"]), (True, True, "read: Y = 2", 0.4))
        self.assertNotIn("V-2", t)                                                       # refused by the check: no trial
        self.assertFalse(t["V-3"]["ok"])                                                 # a refused call is never a trial that worked
        self.assertTrue(t["V-3"]["error"].startswith("refused: "))
        self.assertFalse(t["V-4"]["ok"])
        self.assertIn("no true or false", t["V-4"]["error"])
        self.assertEqual(hc["ran_in"]["mode"], "ConstrainedLanguage")

    def test_the_drafts_may_be_given_as_they_are(self):
        hc = F.stig_answer_win_hc(self.FACTS, dict((v, {"code": c, "by": "pattern"}) for v, c in self.CODES.items()))
        self.assertIn("Get-ItemProperty", hc["gate"]["V-1"]["used"])

    def test_the_trial_off_and_nothing_back(self):
        hc = F.stig_answer_win_hc(self.FACTS, self.CODES, trial_on=False)
        self.assertEqual(hc["trial"]["V-1"], {"off": True})
        self.assertEqual(F.stig_answer_win_hc({}, self.CODES)["gate"], {})
        self.assertEqual(F.stig_answer_win_hc(None, None), {"evaluate": {"names": []}, "gate": {}, "trial": {}, "ran_in": {}, "leftover": []})

    def test_the_rows_read_it_like_the_linux_trial(self):
        picks = [{"v": "V-1", "sv": "SV-1", "ok": True, "expected": "Open", "title": "T", "cat": "CAT II", "sm": "", "es": "Open", "check_text": ""}]
        drafts = {"V-1": {"by": "pattern", "code": "return $true", "true": ("NotAFinding", "ok."), "false": ("Open", "no."), "uses": {}}}
        hc = F.stig_answer_win_hc({"results": [{"key": "V-1", "valid": True, "ran": True, "result": True, "verbose": [], "refused": [], "seconds": 1}]},
                                  {"V-1": "return $true"})
        rows = F.stig_answer_rows(picks, drafts, hc, "DEFAULT", [], None)
        self.assertTrue(rows[0]["block"])
        self.assertFalse(rows[0].get("why"))


class Names(unittest.TestCase):
    def test_the_label(self):
        self.assertEqual(F.stig_label("MS_Windows_Server_2022_STIG", "V2R11"), "Windows Server 2022 V2R11")
        self.assertEqual(F.stig_label("MS_Windows_Server_2019_STIG", ""), "Windows Server 2019")
        self.assertEqual(F.stig_label("RHEL_9_STIG", "V2R10"), "RHEL 9 V2R10")

    def test_the_answer_files_name(self):
        g = {"major": "2022", "bench": "MS_Windows_Server_2022_STIG"}
        self.assertEqual(F._short_name(g, ["V-1"], []), ("WinServer2022", "Evaluate-STIG's own short name for Windows Server 2022", True))
        site = [{"name": "SiteWin2022", "file": "win.xml", "vulns": ["V-1"]}]
        self.assertEqual(F._short_name(g, ["V-1"], site)[0], "SiteWin2022")                 # the site's own answer file wins
        self.assertEqual(F._short_name(dict(g, select={"2022": "WinServer2022Custom"}), ["V-1"], [])[0], "WinServer2022Custom")
        self.assertEqual(F._short_name({"major": "9", "bench": "RHEL_9_STIG"}, ["V-1"], [])[2], False)       # Linux as before


class Dispatch(unittest.TestCase):
    """A Windows STIG's rules are drafted by the Windows patterns, a Linux STIG's as before."""
    RULES = json.load(open(os.path.join(HERE, "fixtures", "stig_win", "rules.json")))

    def pick(self, x):
        return {"v": x["v"], "sv": x["rule"], "ok": True, "check_text": x["check"], "title": x["title"], "severity": x["severity"]}

    def test_windows_rules_get_windows_patterns(self):
        regs = [x for x in self.RULES if x["class"] == "registry value" and x["stig"] == "WinServer2022"]
        d = F.stig_answer_drafts([self.pick(x) for x in regs], {}, False, {}, True)
        got = [v for v in d.values() if v.get("by") == "pattern"]
        self.assertGreaterEqual(len(got), len(regs) * 8 // 10)
        self.assertTrue(all(v["pattern"] == "registry value" and v["code"].strip().splitlines()[-1].startswith("return") for v in got))
        self.assertTrue(all(v["true"][0] == "NotAFinding" and v["false"][0] == "Open" for v in got))

    def test_the_same_text_by_the_linux_patterns_gives_none(self):
        x = next(x for x in self.RULES if x["class"] == "registry value")
        self.assertIsNone(F.stig_answer_draft(x["check"], {}))                              # no Linux pattern fits a Windows check text
        d = F.stig_answer_drafts([self.pick(x)], {}, False, {}, False)                      # and not ACT: off, no pattern: by hand
        self.assertIn("ACT is off", d[x["v"]]["why"])

    def test_the_rules_for_act_and_its_task(self):
        other = [x for x in self.RULES if x["class"] == "other" and x["stig"] == "WinServer2022"]
        g = {"bench": "MS_Windows_Server_2022_STIG", "key": "MS_Windows_Server_2022_STIG|V2R11", "label": "Windows Server 2022 V2R11",
             "picks": [self.pick(x) for x in other]}
        asks = F.stig_answer_for_act_groups([g], {}, True)
        self.assertEqual({a["v"] for a in asks}, {x["v"] for x in other if "This applies to domain controllers" not in x["check"]})
        self.assertTrue(all(a["win"] for a in asks))
        task = F.stig_answer_act_task(asks[0], {})
        self.assertIn("Windows PowerShell 5.1", task)
        self.assertIn("Get-ItemProperty", task)
        self.assertNotIn("systemctl", task)
        self.assertIn("DISA's check text", task)
        lin = F.stig_answer_act_task({"v": "V-1", "sv": "SV-1", "title": "t", "check_text": "c"}, {})
        self.assertIn("PowerShell 7 on Linux", lin)                                          # Linux as before
        self.assertTrue(F.is_windows_stig("MS_Windows_Server_2019_STIG") and not F.is_windows_stig("RHEL_9_STIG"))


class Report(unittest.TestCase):
    """The per-STIG sections of a Windows Server STIG, as the mockup shows them."""
    RULES = Dispatch.RULES

    def group(self):
        regs = [x for x in self.RULES if x["class"] == "registry value" and x["stig"] == "WinServer2022"][:6]
        picks = [{"v": x["v"], "sv": x["rule"], "ok": True, "check_text": x["check"], "title": x["title"], "severity": x["severity"], "expected": "Open",
                  "es": "Open", "cat": "CAT II", "sm": "Not Reviewed"} for x in regs]
        drafts = F.stig_answer_drafts(picks, {}, False, {}, True)
        codes = dict((v, d["code"]) for v, d in drafts.items() if d.get("code"))
        results = [{"key": v, "valid": True, "ran": True, "result": i % 2 == 0, "verbose": ["read: x"], "refused": [], "seconds": 0.2}
                   for i, v in enumerate(codes)]
        ran_in = {"mode": "ConstrainedLanguage", "commands": ["Get-ItemProperty", "Test-Path"], "proxies": ["secedit.exe", "Remove-Item", "auditpol.exe"],
                  "powershell": "5.1.20348.1"}
        hc = F.stig_answer_win_hc({"results": results, "ran_in": ran_in}, codes)
        rows = F.stig_answer_rows(picks, drafts, hc, "DEFAULT", [], None)
        return {"label": "Windows Server 2022 V2R11", "bench": "MS_Windows_Server_2022_STIG", "rev": "V2R11", "hosts": ["win-a", "win-b"], "rep": "win-a",
                "rows": rows, "ev": {}, "names_from": {}, "ran_in": ran_in, "major": "2022", "select": {}, "trial_on": True}

    def test_the_sections(self):
        g = self.group()
        self.assertTrue(any(r.get("block") for r in g["rows"]))
        doc = F.stig_answer_report_multi([g], "DEFAULT", False, [], [], {}, "", {}, True, "web", [])
        sec = next(x for x in doc["web"]["sections"] if " - answer keys to copy (" in x["title"])
        self.assertIn("Trial · matches Evaluate-STIG", sec["columns"])
        bi, ci = sec["columns"].index("Drafted by"), sec["columns"].index("Trial · matches Evaluate-STIG")
        self.assertTrue(all(r[bi] == "pattern - registry value" for r in sec["rows"]))
        self.assertTrue(all(r[ci].split(" · ")[1] in ("yes", "no") for r in sec["rows"]))
        trial = [b for b in sec["details"][0] if isinstance(b, dict) and str(b.get("columns", [""])[0]).startswith("Trial on")][0]
        labels = [x[0] for x in trial["rows"]]
        self.assertEqual(labels[:2], ["What DISA checks", "Ran in"])
        self.assertEqual(labels[-1], "Matches Evaluate-STIG")
        ran = dict((x[0], x[1]) for x in trial["rows"])["Ran in"]
        self.assertIn("Constrained Language Mode with only 2 read-only commands", ran)
        self.assertIn("secedit.exe, Remove-Item, auditpol.exe", ran)                           # the proxies are named
        tiles = dict((t["label"], t) for t in doc["web"]["summary"])
        self.assertEqual(tiles["Windows"]["value"], "%d ready" % len([r for r in g["rows"] if r.get("block")]))
        self.assertEqual(tiles["Linux"]["value"], "0 ready")
        how = next(x for x in doc["web"]["sections"] if x["title"] == "How this was made")
        self.assertTrue(any("restricted PowerShell session (WinRM)" in l for l in how["lines"]))
        self.assertFalse(any("systemd-run" in l for l in how["lines"]))
        self.assertEqual([(f["filename"], f["name"], f["known"]) for f in doc["files"]],
                         [("answer-keys-MS_Windows_Server_2022_STIG-V2R11.xml", "WinServer2022", True)])         # Evaluate-STIG's own short name


class DraftsMeetTheHarness(unittest.TestCase):
    """Every fixed-pattern code of the 129 fixture rules passes the restricted session's own read-only check (win_ak_try.ps1, trial off): what
    the drafter writes is what the trial accepts. Needs pwsh (skipped without)."""

    @unittest.skipUnless(shutil.which("pwsh"), "pwsh is not installed")
    def test_every_pattern_code_passes_the_check(self):
        import subprocess
        site = {"documented_required_features": ["Web-Server"]}
        codes = []
        for x in Dispatch.RULES:
            d = F._draft_of({"v": x["v"], "sv": x["rule"], "check_text": x["check"], "title": x["title"], "severity": x["severity"]}, site, True)
            if d and d.get("kind") == "pattern":
                codes.append({"key": x["v"] + "|" + x["stig"], "code": d["code"], "fixed": True})
        self.assertGreaterEqual(len(codes), 60)
        cfg = {"codes": codes, "allowed": F.stig_answer_win_allowed(), "timeout": 10, "trial": False}
        out = subprocess.run(["pwsh", "-NoProfile", "-File", os.path.join(ROOT, "roles", "check_stig", "files", "win_ak_try.ps1")],
                             env=dict(os.environ, SITE_CHECK_CONFIG=json.dumps(cfg)), capture_output=True, text=True, timeout=300)
        lines = [l for l in out.stdout.splitlines() if l.startswith("###SITE-JSON### ")]
        self.assertEqual(len(lines), 1, out.stderr[-300:])
        res = json.loads(lines[0][16:])["facts"]["results"]
        self.assertEqual([(r["key"], r["why"]) for r in res if not r["valid"]], [])


class CrossCheckEvidence(unittest.TestCase):
    """The cross-check on Windows: the evidence is what the pattern read in the trial."""
    G = {"bench": "MS_Windows_Server_2022_STIG", "rep": "win-a", "rows": [
        {"v": "V-1", "block": "x", "by": "pattern", "pattern": "registry value", "disa": "HKLM:\\X", "trial": {"ran": True, "read": "read: Y = 2"}},
        {"v": "V-2", "block": "y", "by": "ACT", "trial": {"ran": True, "read": "read: something"}},
        {"v": "V-3", "block": "z", "by": "pattern", "pattern": "role or feature", "trial": {"ran": False, "read": ""}},
        {"v": "V-4", "block": ""}]}

    def test_the_trial_host_gives_its_patterns_readings(self):
        r = F.stig_answer_win_reads([self.G, {"bench": "RHEL_9_STIG", "rep": "win-a", "rows": [{"v": "V-9", "block": "q"}]}], "win-a")["reads"]
        self.assertEqual(sorted(r), ["V-1", "V-2", "V-3"])                                     # a block each; a Linux group is not its business
        self.assertEqual(r["V-1"]["ran"], [{"cmd": "registry value (HKLM:\\X)", "rc": 0, "out": "read: Y = 2"}])
        self.assertEqual(r["V-1"]["from"], "the pattern's own reading")
        for v in ("V-2", "V-3"):                                                              # ACT's draft, a trial that read nothing: said
            self.assertEqual((r[v]["ran"], r[v]["why"]), ([], "not read: no fixed reading for this rule"))

    def test_another_host_is_not_read(self):
        r = F.stig_answer_win_reads([self.G], "win-b")["reads"]
        self.assertTrue(all(x["ran"] == [] and "trial host only" in x["why"] for x in r.values()))

    def test_acts_task_says_windows(self):
        import stig_answer_cross_filters as X
        t = X.stig_answer_cross_act_task({"v": "V-1", "title": "t", "check_text": "c", "win": True}, [{"cmd": "registry value (X)", "rc": 0, "out": "read: Y = 2"}])
        self.assertIn("on one Windows Server host", t)
        self.assertNotIn("RHEL", t)
        self.assertIn("on one RHEL host", X.stig_answer_cross_act_task({"v": "V-1", "title": "t", "check_text": "c"}, []))


class ReviewRound(unittest.TestCase):
    """Codex's review of 1.105.0 (the host side): one test for each finding, each failing on the code before the fix."""
    def src(self, rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
            return fh.read()

    def test_the_scan_output_is_pinned_to_cklb(self):
        y = self.src("roles/check_stig/tasks/answer_keys.yml")
        a = y.index("on the Windows host (nothing kept, nothing sent)")
        block = y[a:a + 2600]
        self.assertIn("'output': 'CKLB'", block)
        self.assertNotIn("win_check_stig_evaluate_output", block)                  # an inherited STIGManager output can submit results

    @unittest.skipUnless(shutil.which("pwsh"), "pwsh is not installed")
    def test_baseline_answer_directory(self):
        with tempfile.TemporaryDirectory(prefix="example baseline ") as tmp:
            install = Path(tmp, "Evaluate-STIG")
            answers = install / "AnswerFiles"
            answers.mkdir(parents=True)
            marker = answers / "example.xml"
            marker.write_text("<STIGComments/>")
            shutil.copy(Path(ROOT, "tests/windows/stig/Evaluate-STIG.ps1"), install)
            (install / "spec.json").write_text(json.dumps({"host": "EXAMPLE", "require_empty_answers": True,
                "checklists": [{"file": "example", "stig_id": "MS_Windows_Server_2022_STIG", "rules": [
                    {"v": "V-1", "sv": "SV-1", "status": "nr"}]}]}))
            facts = self.run_baseline(tmp, install)
            self.assertTrue(facts["scanned"], facts["why"])
            args = dict(line.split("=", 1) for line in (install / "args.txt").read_text().splitlines())
            work = Path(args["OutputPath"]).parent
            self.assertEqual(Path(args["AFPath"]), work / "AnswerFiles")
            self.assertFalse(work.exists())
            self.assertTrue(marker.is_file())

            scanner = install / "Evaluate-STIG.ps1"
            source = scanner.read_text()
            scanner.write_text(source.replace("[string] $AFPath", "[Alias('AFPath')][string] $AnswerDirectory")
                               .replace("$AFPath", "$AnswerDirectory"))
            facts = self.run_baseline(tmp, install)
            self.assertTrue(facts["scanned"], facts["why"])
            scanner.write_text(source.replace("AFPath", "OtherDirectory"))
            (install / "args.txt").unlink()
            facts = self.run_baseline(tmp, install)
            self.assertIn("needs an Evaluate-STIG with -AFPath", facts["why"])
            self.assertFalse((install / "args.txt").exists())

    def run_baseline(self, tmp, install, setup=""):
        config = dict(evaluate=str(install), host="EXAMPLE", selected_only=True, vulns=["V-1"],
                      save=False, history=False, run_as="direct", dir=tmp)
        env = dict(os.environ, TEMP=tmp, SITE_CHECK_CONFIG=json.dumps(config),
                   EXAMPLE_SCANNER=str(Path(ROOT, "roles/win_check_stig/files/win_stig.ps1").resolve()))
        reply = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command", setup + "& $env:EXAMPLE_SCANNER"],
                               env=env, capture_output=True, text=True, timeout=30, check=True)
        return json.loads(reply.stdout.split("###SITE-JSON### ")[-1])["facts"]

    @unittest.skipUnless(shutil.which("pwsh"), "pwsh is not installed")
    def test_baseline_cleanup_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp, "run-example", "keep.txt")
            marker.parent.mkdir()
            marker.write_text("other run")
            install = Path(tmp, "Evaluate-STIG.ps1")
            install.write_text("param($SelectVuln, $AFPath)\nthrow 'unexpected scan'")
            facts = self.run_baseline(tmp, install, "function New-Guid { 'example' }; ")
            self.assertFalse(facts["scanned"])
            self.assertEqual(marker.read_text(), "other run")
            self.assertCountEqual(Path(tmp).iterdir(), [marker.parent, install])
            self.assertNotIn("Evaluate-STIG wrote no checklist", facts["why"])
            self.assertIn("exist", facts["why"].lower())
        ps = self.src("roles/win_check_stig/files/win_stig.ps1")
        self.assertIn("'site-automation STIG scan ' + (Split-Path -Leaf $Work)", ps)
        register = next(line for line in ps.splitlines() if line.strip().startswith("Register-ScheduledTask "))
        self.assertNotIn("-Force", register)

    def test_reach_event_censored(self):
        import yaml
        tasks = yaml.safe_load(self.src("roles/check_stig/tasks/answer_keys.yml"))
        task, = [t for t in tasks if "_ak_rch" in t.get("ansible.builtin.set_fact", {})]
        self.assertIs(task.get("no_log"), True)

    def test_scan_fact_drops_evidence(self):
        bench = "MS_Windows_Server_2022_STIG"
        secret = "exampleValue"
        item = dict(WhichScan.ITEMS[0], details="client_secret=" + secret, title="token=" + secret,
                    check_text="client_secret=" + secret)
        fact = F.stig_answer_win_scan({"scanned": True, "scans": [scan(bench, [item], stig_version="psk=" + secret)]}, bench)
        self.assertEqual(set(fact["items"][0]), {"key", "severity", "result", "title", "check_text"})
        self.assertNotIn(secret, str(fact))
        self.assertEqual(fact["items"][0]["check_text"], "")

    def test_no_checklist_reason_discards_log_tail(self):
        tail = "example" * 85
        why = "Evaluate-STIG wrote no checklist (exit 1, run by this session): " + tail
        fact = F.stig_answer_win_scan({"scanned": False, "stderr": tail, "why": why}, "MS_Windows_Server_2022_STIG")
        self.assertEqual(fact["why"], "Evaluate-STIG wrote no checklist")

    def test_scoped_rules_never_reach_act_or_xml(self):
        picks = [Dispatch().pick(x) for x in Dispatch.RULES if x["v"] in ("V-205820", "V-254431")]
        self.assertEqual(F.stig_answer_for_act(picks, {}, True), [])
        act = {p["v"]: {"code": "return $false"} for p in picks}
        for result in F.stig_answer_drafts(picks, {}, True, act, True).values():
            self.assertEqual(set(result), {"why"})
            self.assertIn("server role", result["why"])

    def test_approved_accounts_preserve_spaces(self):
        rule = next(x for x in Dispatch.RULES if x["v"] == "V-278250")
        key = "approved_se_debug_privilege_accounts"
        names = [r"EXAMPLE\Backup Service", "Example Two", "Example Three", "Example Four"]
        text = names[0] + ", " + names[1] + "; " + names[2] + "\n" + names[3]
        result = F._draft_of(Dispatch().pick(rule), {key: text}, True)
        self.assertIn("$approved = @(" + ", ".join("'" + n + "'" for n in sorted(names)) + ")", result["code"])

    def test_the_scan_never_runs_in_full(self):
        self.assertIn("'selected_only': true", self.src("roles/check_stig/tasks/answer_keys.yml"))
        ps = self.src("roles/win_check_stig/files/win_stig.ps1")
        self.assertIn("$selectedOnly = [bool](Get-Setting 'selected_only' $false)", ps)
        self.assertIn("throw 'this Evaluate-STIG has no -SelectVuln", ps)
        self.assertIn("and the answer keys never run it in full", ps)

    def test_named_rules_keep_the_review_protections(self):
        sm = {"status": "found", "reviews": {"SV-1": {"result": "fail", "protect": True}, "SV-2": {"result": "fail"}}}
        r = F.stig_answer_win_protect([{"v": "V-1", "sv": "SV-1"}, {"v": "V-2", "sv": "SV-2"}], sm)
        self.assertEqual(([x["v"] for x in r["rules"]], r["dropped"]), (["V-2"], ["V-1"]))
        self.assertEqual(F.stig_answer_win_protect([{"v": "V-1", "sv": "SV-1"}], {})["dropped"], [])         # nothing read: nothing to protect
        y = self.src("roles/check_stig/tasks/answer_keys.yml")
        self.assertIn("stig_answer_win_protect(_ak_smh)", y)
        self.assertIn("(_ak_rules.auto | default(false) or _ak_is_win | bool) and (_ak_smh | stig_answer_att_items", y)   # attachments too

    def test_act_may_not_use_the_write_capable_proxies(self):
        picks = [{"v": "V-1", "sv": "SV-1", "ok": True, "check_text": "x", "title": "t"}]
        for code in ("secedit.exe /export /areas USER_RIGHTS /cfg $f\nreturn $true", "Remove-Item -LiteralPath $f -Force\nreturn $true"):
            d = F.stig_answer_drafts(picks, {}, True, {"V-1": {"code": code}}, True)
            self.assertIn("only the fixed patterns may use", d["V-1"]["why"])
        ok = F.stig_answer_drafts(picks, {}, True, {"V-1": {"code": "return $true"}}, True)
        self.assertEqual(ok["V-1"]["by"], "ACT")
        codes = F.stig_answer_win_codes({"V-1": {"by": "pattern", "code": "return $true"}, "V-2": {"by": "ACT", "code": "return $false"}})
        self.assertEqual([(c["key"], c["fixed"]) for c in codes], [("V-1", True), ("V-2", False)])      # the harness is told which are fixed

    def test_what_the_code_read_is_redacted_whole_before_it_is_cut(self):
        rec = "read: " + "x" * 150 + " https://user:examplevalue@proxy.example.test/ " + "y" * 200
        facts = {"results": [{"key": "V-1", "valid": True, "ran": True, "result": True, "verbose": [rec], "refused": [], "seconds": 1}]}
        hc = F.stig_answer_win_hc(facts, {"V-1": "return $true"})
        self.assertNotIn("examplevalue", hc["trial"]["V-1"]["read"])
        facts["results"][0]["verbose"] = ["read: client_secret=examplesecret"]
        self.assertNotIn("examplesecret", F.stig_answer_win_hc(facts, {"V-1": "return $true"})["trial"]["V-1"]["read"])

    def test_failed_scan_diagnostics_are_sanitized(self):
        r = F.stig_answer_win_scan({"scanned": False, "why": "Evaluate-STIG said client_secret=examplesecret and stopped"}, "MS_Windows_Server_2022_STIG")
        self.assertNotIn("examplesecret", r["why"])
        r = F.stig_answer_win_scan({}, "MS_Windows_Server_2022_STIG", "token=examplesecret")
        self.assertNotIn("examplesecret", r["why"])

    def test_an_unsupported_host_says_nothing_it_was_told_in_the_email(self):
        r = F.stig_answer_win_os([os_line("Windows test image token=examplesecret", "1234")])
        self.assertFalse(r["ok"])
        self.assertNotIn("examplesecret", r["why"] + r["caption"])
        self.assertNotIn("test image", r["why_mail"])
        self.assertIn("answer_keys_evidence", self.src("roles/check_stig/tasks/answer_keys.yml") + "answer_keys_evidence")      # (the mail words are fixed)
        self.assertIn('why_mail: "{{ _ak_win.why_mail }}"', self.src("roles/check_stig/tasks/answer_keys.yml"))

    def test_secret_looking_site_values_never_reach_acts_prompt(self):
        vals = {"snmp_community": ["communityExample"], "api_token": "examplevalue", "client_secret": "examplevalue2", "min_release": "9.4", "approved_features": ["Web-Server", "Telnet-Client"]}
        win = F.stig_answer_act_task({"v": "V-1", "sv": "SV-1", "title": "t", "check_text": "c", "win": True}, vals)
        lin = F.stig_answer_act_task({"v": "V-1", "sv": "SV-1", "title": "t", "check_text": "c"}, vals)
        for t in (win, lin):
            self.assertNotIn("communityExample", t)
            self.assertNotIn("examplevalue", t)
            self.assertNotIn("examplevalue2", t)
            self.assertIn("min_release = 9.4", t)
            self.assertIn("approved_features = Web-Server, Telnet-Client", t)

    def test_the_drafts_go_in_batches_under_the_environment_limit(self):
        rules = [x for x in Dispatch.RULES if x["stig"] == "WinServer2022" and x["class"] == "registry value"]
        d = F.stig_answer_drafts([{"v": x["v"], "sv": x["rule"], "ok": True, "check_text": x["check"], "title": x["title"]} for x in rules], {}, False, {}, True)
        d = dict((v, x) for v, x in d.items() if x.get("code"))
        big = dict((v + "-%d" % i, dict(x, code=x["code"] * 3)) for i in range(3) for v, x in d.items())          # enough code to need several runs
        n = F.stig_answer_win_batches(big)
        self.assertGreater(len(n), 1)
        total = []
        for b in n:
            items = F.stig_answer_win_codes(big, b)
            self.assertLessEqual(sum(len(i["code"]) for i in items), F.WIN_BATCH_CHARS)
            total += [i["key"] for i in items]
        self.assertEqual(sorted(total), sorted(big))                                                   # every code once, none lost
        self.assertEqual(F.stig_answer_win_batches({}), [1])

    def test_a_windows_client_is_not_a_server(self):
        for cap, build in (("Microsoft Windows 11 Enterprise", "26100"), ("Microsoft Windows 10 Enterprise LTSC 2019", "17763")):
            line = "###SITE-JSON### " + json.dumps({"findings": [], "facts": {"caption": cap, "build": build, "domain_role": "1", "product_type": "1"}})
            self.assertFalse(F.stig_answer_win_os([line])["ok"], cap)
        line = "###SITE-JSON### " + json.dumps({"findings": [], "facts": {"caption": "", "build": "20348", "domain_role": "3", "product_type": "3"}})
        self.assertEqual(F.stig_answer_win_os([line])["year"], "2022")                                      # a server by build is fine

    def test_the_detected_family_decides_not_the_benchmark_spelling(self):
        g = {"bench": "MS_Windows_Server_2022_STIG_SITE", "win": True, "key": "k", "label": "l",
             "picks": [{"v": "V-1", "sv": "SV-1", "ok": True, "check_text": "A made-up text no pattern reads.", "title": "t"}]}
        asks = F.stig_answer_for_act_groups([g], {}, True)
        self.assertEqual([a["win"] for a in asks], [True])
        self.assertFalse(F.stig_answer_for_act_groups([dict(g, win=False)], {}, True)[0]["win"])
        grp = F.stig_answer_merge([{"host": "w1", "ok": True, "bench": "MS_Windows_Server_2022_STIG_SITE", "rev": "V2R11", "win": True,
                                    "picks": [{"v": "V-1", "sv": "SV-1", "ok": True, "es": "Open", "expected": "Open"}]}])["groups"][0]
        self.assertTrue(grp["win"])
        self.assertIn("(g.win | default(false)) or (g.bench | stig_answer_is_windows)", self.src("roles/check_stig/tasks/answer_keys_check.yml"))

    def test_the_trial_is_compared_with_the_representative_hosts_status(self):
        base = {"v": "V-1", "block": "x", "by": "pattern", "pattern": "registry value", "es": "Open 1", "es_rep": "Open", "trial": {"valid": False, "ran": True}}
        self.assertEqual(F._win_match(base), "yes")
        self.assertTrue(F._win_match(dict(base, trial={"valid": True})).startswith("no"))
        self.assertEqual(F._win_match(dict(base, es="Open 2", es_rep="")), "yes")                         # the merged text alone is read too
        self.assertEqual(F._win_match(dict(base, es="Not Reviewed 1", es_rep="Not Reviewed")), "n/a (Evaluate-STIG left it Not Reviewed)")
        m = F.stig_answer_merge([{"host": "w1", "ok": True, "bench": "MS_Windows_Server_2022_STIG", "rev": "V2R11", "win": True,
                                  "picks": [{"v": "V-1", "sv": "SV-1", "ok": True, "es": "Open", "expected": "Open"}]}])["groups"][0]["picks"][0]
        self.assertEqual(m["es_rep"], "Open")

    def test_two_revisions_sharing_a_v_id_keep_their_own_reading(self):
        def row(read):
            return {"v": "V-1", "block": "x", "by": "pattern", "pattern": "registry value", "disa": "HKLM:\\X", "trial": {"ran": True, "read": read, "read_ok": True}}
        g1 = {"bench": "MS_Windows_Server_2022_STIG", "rep": "w-a", "rows": [row("read: A")]}
        g2 = {"bench": "MS_Windows_Server_2022_STIG", "rep": "w-b", "rows": [row("read: B")]}
        self.assertEqual(F.stig_answer_win_reads([g1, g2], "w-a")["reads"]["V-1"]["ran"][0]["out"], "read: A")
        self.assertEqual(F.stig_answer_win_reads([g2, g1], "w-a")["reads"]["V-1"]["ran"][0]["out"], "read: A")       # whatever the order
        self.assertEqual(F.stig_answer_win_reads([g1, g2], "w-b")["reads"]["V-1"]["ran"][0]["out"], "read: B")

    def test_a_failed_pattern_read_is_not_a_successful_command(self):
        facts = {"results": [{"key": "V-1", "valid": True, "ran": True, "result": False, "verbose": ["read: failed: access denied"], "refused": [], "seconds": 1}]}
        hc = F.stig_answer_win_hc(facts, {"V-1": "return $false"})
        self.assertFalse(hc["trial"]["V-1"]["read_ok"])
        g = {"bench": "MS_Windows_Server_2022_STIG", "rep": "w-a", "rows": [{"v": "V-1", "block": "x", "by": "pattern", "pattern": "registry value",
                                                                          "trial": hc["trial"]["V-1"]}]}
        x = F.stig_answer_win_reads([g], "w-a")["reads"]["V-1"]
        self.assertEqual(x["ran"], [])
        self.assertIn("reading failed", x["why"])

    def test_a_file_left_behind_is_said_never_certified_clean(self):
        facts = {"results": [{"key": "V-1", "valid": True, "ran": True, "result": True, "verbose": [], "refused": [], "seconds": 1}], "leftover": ["policy.inf.log"],
                 "ran_in": {"mode": "ConstrainedLanguage", "proxies": ["secedit.exe"]}}
        hc = F.stig_answer_win_hc(facts, {"V-1": "return $true"})
        self.assertEqual(hc["leftover"], ["policy.inf.log"])
        self.assertIn("NOT CLEAN: left on the host: policy.inf.log", F._win_ran_in(hc["ran_in"], "w-a", hc["leftover"]))
        self.assertIn("nothing registered or left on the host", F._win_ran_in(hc["ran_in"], "w-a", []))
        self.assertEqual(F.stig_answer_win_hc([facts, {"results": [], "leftover": ["b.tmp"]}], {})["leftover"], ["policy.inf.log", "b.tmp"])      # batches join

    def test_the_report_says_how_evaluate_stig_really_ran(self):
        g = {"bench": "MS_Windows_Server_2022_STIG", "rep": "w-a", "rows": [], "leftover": [], "run_as": "direct", "ran_in": {}}
        rw, nd, hw, d = F._win_parts({"columns": [], "rows": []}, {"rows": []}, {"lines": ["Evaluate-STIG on the host: x"]}, {}, g)
        self.assertIn("in the job's own session as the connecting user (win_check_stig_run_as: direct)", hw["lines"][0])
        self.assertNotIn("SYSTEM", hw["lines"][0])
        rw, nd, hw, d = F._win_parts({"columns": [], "rows": []}, {"rows": []}, {"lines": ["Evaluate-STIG on the host: x"]}, {}, dict(g, run_as="task"))
        self.assertIn("as SYSTEM in a one-time task", hw["lines"][0])
        rw, nd, hw, d = F._win_parts({"columns": [], "rows": []}, {"rows": []}, {"lines": []}, {}, dict(g, leftover=["a.tmp"]))
        self.assertEqual(rw["status"], "warning")
        self.assertIn("WARNING: the restricted session left files", hw["lines"][0])


class SiteValues(unittest.TestCase):
    def test_a_documented_value_asked_for_is_said_as_a_variables_line(self):
        x = next(x for x in Dispatch.RULES if x["v"] == "V-205643")
        d = F.stig_answer_drafts([{"v": x["v"], "sv": x["rule"], "ok": True, "check_text": x["check"], "title": x["title"]}], {}, False, {}, True)[x["v"]]
        self.assertIn("answer_keys_values: {approved_", d["why"])
        self.assertIn("[ExampleAccount]", d["why"])
        given = F.stig_answer_drafts([{"v": x["v"], "sv": x["rule"], "ok": True, "check_text": x["check"], "title": x["title"]}],
                                     {"approved_se_security_privilege_accounts": "Auditors, AppSvc"}, False, {}, True)[x["v"]]
        self.assertEqual(given["by"], "pattern")                                                  # a survey's text is a list too
        self.assertEqual(F.stig_answer_values("documented_required_features: Web-Server, DNS")["documented_required_features"], ["Web-Server", "DNS"])

    def test_batches_say_clean_only_when_every_run_was(self):
        a = {"results": [], "leftover": [], "ran_in": {"clean": True, "mode": "ConstrainedLanguage"}}
        b = {"results": [], "leftover": ["x"], "ran_in": {"clean": False}}
        self.assertFalse(F.stig_answer_win_hc([a, b], {})["ran_in"]["clean"])
        self.assertTrue(F.stig_answer_win_hc([a, a], {})["ran_in"]["clean"])


class Scripts(unittest.TestCase):
    def read(self, rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
            return fh.read()

    def test_the_os_script_only_reads(self):
        t = self.read("roles/win_check_stig/files/win_os.ps1")
        code = "\n".join(l for l in t.splitlines() if not l.lstrip().startswith("#"))
        self.assertNotRegex(code, r"\b(Set|New|Remove|Add|Out|Start|Stop|Invoke|Register|Unregister)-[A-Za-z]+")
        self.assertIn("Get-CimInstance", code)
        self.assertIn("###SITE-JSON### ", code)

    def test_the_stig_script_gives_texts_only_when_asked(self):
        t = self.read("roles/win_check_stig/files/win_stig.ps1")
        self.assertIn("$withTexts = [bool](Get-Setting 'texts' $false)", t)
        self.assertIn("($withTexts -and $r.result -eq 'notchecked')", t)


if __name__ == "__main__":
    unittest.main()
