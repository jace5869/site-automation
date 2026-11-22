"""Exercise the trial through its WinRM JSON contract, using only placeholders."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "roles/check_stig/files/win_ak_try.ps1"
sys.path.insert(0, str(SCRIPT.parents[3]))
from plugins.module_utils import stig_answer_win as patterns

PWSH = shutil.which("pwsh")
ALLOWED = ["Write-Verbose", "Get-Content", "Test-Path", "Get-Item", "Get-ChildItem"]


@unittest.skipUnless(PWSH, "pwsh is not on PATH")
class Trial(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.work = self.root / "site-ak-trial folder"

    def run_codes(self, codes, *, fixed=False, leftover=(), extra_env=None, inline=False, **settings):
        cfg = dict(allowed=ALLOWED, work_dir=str(self.work), timeout=5, trial=True, read_roots=[str(self.root)],
                   codes=[dict(key=f"SV-{i}r1_rule", code=c, fixed=fixed) for i, c in enumerate(codes, 1)])
        cfg.update(settings)
        if cfg["trial"] is None:
            del cfg["trial"]
        env = dict(os.environ, SITE_CHECK_CONFIG=json.dumps(cfg),
                   PATH=str(self.root) + os.pathsep + os.environ["PATH"])
        env.update(extra_env or {})
        invocation = ["-Command", "& {\n" + SCRIPT.read_text() + "\n}"] if inline else ["-File", str(SCRIPT)]
        run = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", *invocation],
                             env=env, capture_output=True, text=True, timeout=240, check=True)
        self.assertEqual(run.stderr, "")
        lines = run.stdout.splitlines()
        self.assertEqual(len(lines), 1, run.stdout)
        prefix = "###SITE-JSON### "
        self.assertTrue(lines[0].startswith(prefix))
        report = json.loads(lines[0][len(prefix):])
        self.assertEqual(report["findings"], [])
        facts = report["facts"]
        self.assertCountEqual(facts["leftover"], leftover)
        self.facts = facts
        self.assertEqual(self.work.exists(), bool(leftover))
        expected = set(cfg["allowed"]) - {"Get-WindowsOptionalFeature", *facts["ran_in"]["proxies"]}
        self.assertCountEqual(facts["ran_in"]["commands"], expected)
        self.assertEqual(facts["ran_in"]["mode"], "ConstrainedLanguage")
        self.assertTrue(facts["ran_in"]["powershell"])
        results = facts["results"]
        self.assertEqual([r["key"] for r in results], [c["key"] for c in cfg["codes"]])
        for row in results:
            self.assertGreaterEqual(row.pop("seconds"), 0)
        return results

    @unittest.skipIf(os.name == "nt", "inline command exceeds the Windows command-line length limit")
    def test_inline_worker(self):
        temp = self.root / "system temp"
        temp.mkdir()
        code = (f"foreach ($dir in (Get-ChildItem -LiteralPath '{temp}')) {{ "
                "if ($dir.Name -like 'site-ak-w-*') { Write-Verbose $dir.Name } }; $true")
        row, = self.run_codes([code], inline=True, extra_env={"TMPDIR": str(temp)})
        self.assertIs(row["valid"], True, row)
        self.assertIs(row["ran"], True, row)
        self.assertIs(row["result"], True, row)
        folder, = row["verbose"]
        self.assertRegex(folder, r"^site-ak-w-[0-9a-f-]{36}$")
        self.assertFalse((temp / folder).exists())

    def test_session_facts_and_default_roots(self):
        program_data = self.root / "data"
        self.run_codes(["$true"], read_roots=None,
                       extra_env={"SystemRoot": str(self.root), "ProgramData": str(program_data)})
        self.assertIs(self.facts["clean"], True)
        context = self.facts["ran_in"]
        self.assertIs(context["clean"], True)
        self.assertCountEqual(context["read_roots"], map(str, (self.work, self.root, program_data / "site-automation")))
        self.assertEqual(context["proxies"], ["secedit.exe", "Remove-Item", "auditpol.exe", "Get-SiteValueKind"])

    def test_results_and_repeatability(self):
        codes = ["Write-Verbose 'read: example'; return $true", "return $false", "'True'",
                 "if (", "1..45 | ForEach-Object { Write-Verbose ('x' * 220) }; $true"]
        allowed = ALLOWED + ["ForEach-Object"]
        rows = self.run_codes(codes, allowed=allowed)
        self.assertEqual(rows, self.run_codes(codes, allowed=allowed))
        self.assertIs(rows[0]["result"], True)
        self.assertEqual(rows[0]["verbose"], ["read: example"])
        self.assertIs(rows[1]["result"], False)
        self.assertTrue(rows[2]["valid"] and rows[2]["ran"])
        self.assertIsNone(rows[2]["result"])
        self.assertIn("no boolean", rows[2]["why"])
        self.assertFalse(rows[3]["valid"] or rows[3]["ran"])
        self.assertTrue(rows[3]["why"])
        self.assertEqual(rows[4]["verbose"], ["x" * 220] * 39 + ["[cut]"])

    def test_command_inventory_and_filesystem_reads(self):
        allowed = ALLOWED + ["Get-Command"]
        fixture = self.root / "example"
        fixture.write_text("example")
        code = f"""$file = Get-Item -LiteralPath '{fixture}'
$children = @(Get-ChildItem -LiteralPath '{self.root}')
$text = Get-Content -LiteralPath $file.FullName
foreach ($command in (Get-Command)) {{ Write-Verbose $command.Name }}
return ($children.Name -contains 'example' -and $text -eq 'example')"""
        row, = self.run_codes([code], allowed=allowed)
        self.assertIs(row["result"], True, row)
        self.assertCountEqual(row["verbose"], allowed + ["auditpol.exe", "Get-SiteValueKind"])

    def test_type_operators_require_allowed_literals(self):
        good = ["1 -is [int]", "1 -isnot [string]", "[long]1"]
        bad = ["1 -as [int]", "1 -as 'int'", "1 -is 'int'", "$t = 'string'; 1 -isnot $t",
               "1 -is [double]", "[double]1"]
        rows = self.run_codes(good + bad, trial=False)
        self.assertEqual([r["valid"] for r in rows], [True] * len(good) + [False] * len(bad))

    def test_child_listing_is_nonrecursive(self):
        listing = f"Get-ChildItem -LiteralPath '{self.root}'"
        rows = self.run_codes([listing, listing + " -Recurse"], trial=False)
        self.assertEqual([r["valid"] for r in rows], [True, False])
        proxy = SCRIPT.read_text().split("$readFunction = {", 1)[1].split("$akCommand =", 1)[0]
        self.assertNotIn("$Recurse", proxy)

    def test_timeout_and_validation_only(self):
        code = "while ($true) {}"
        loop, after = self.run_codes([code, "$true"], timeout=1)
        self.assertEqual(loop["why"], "timed out")
        self.assertTrue(loop["valid"] and loop["ran"])
        self.assertIsNone(loop["result"])
        self.assertIs(after["result"], True)
        row, = self.run_codes([code], trial=None)
        self.assertTrue(row["valid"])
        self.assertFalse(row["ran"])

    def test_write_proxies_reserved_for_fixed_patterns(self):
        export = "secedit.exe /export /areas USER_RIGHTS /cfg $f /log $l"
        remove = "Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue"
        for row in self.run_codes([export, remove], trial=False):
            self.assertFalse(row["valid"] or row["ran"])
            self.assertIn("reserved for the fixed patterns", row["why"])

    def test_output_count(self):
        row, = self.run_codes(["17; return $false"])
        self.assertTrue(row["valid"] and row["ran"])
        self.assertIsNone(row["result"])
        self.assertIn("no boolean", row["why"])
        self.assertIn("2", row["why"])

    def test_whole_verbose_records(self):
        b, c = self.run_codes([
            'Write-Verbose ("x" * 198 + "SECRET`nline"); $true',
            "Write-Verbose ('x' * 4001); $true"])
        self.assertEqual(b["verbose"], ["x" * 198 + "SECRET\nline"])
        self.assertEqual(c["verbose"], ["[cut]"])

    def test_registry_kind(self):
        call = "Get-SiteValueKind -LiteralPath $path -Name $name"
        rows = self.run_codes([f"$path = '{path}'; $name = '{name}'; $kind = {call}; return ($kind -eq 'DWord')"
                               for path, name in [(r"HKLM:\Example", "Value"), ("env:TEMP", "Value"),
                                                  (r"HKLM:\..", ""), (r"HKLM:\A", "x"*256)]]
                              + [call + " extra"], extra_env={"SITE_AK_TEST": "1", "SITE_AK_TEST_KIND": "DWord"})
        self.assertIs(rows[0]["result"], True, rows[0])
        for row in rows[1:4]:
            self.assertTrue(row["refused"], row)
        self.assertFalse(rows[-1]["valid"])
        if os.name != "nt":
            code = r"try { $v = Get-SiteValueKind -LiteralPath 'HKLM:\Example' -Name 'Value'; return ($v -eq 'DWord') } catch { return $false }"
            row, = self.run_codes([code], extra_env={"SITE_AK_TEST": "0", "SITE_AK_TEST_KIND": "DWord"})
            self.assertIs(row["result"], False, row)

    def test_read_guards(self):
        link = self.root / "link"
        link.symlink_to(self.root.parent, target_is_directory=True)
        commands = ALLOWED[1:] + ["Get-ItemProperty"]
        paths = ["//server/file", r"\\server\file", "https://host/path", str(self.root / "../outside"),
                 str(self.root) + "-other", "env:PATH", "Variable:ExecutionContext", str(link / "outside"),
                 r"\\.\pipe\example", r"\\?\C:\example", r"\\.\C:\example", r"HKCU:\Software",
                 r"Microsoft.PowerShell.Core\FileSystem::example", "Env:SITE_AK_TEMP_INF",
                 "Environment::TEMP", "Function:test", "Alias:test", "Cert:example", "WSMan:example"]
        cases = [(commands[i % len(commands)], path) for i, path in enumerate(paths)]
        for dynamic in [False, True]:
            codes = [(f"$p = '{path}'; {cmd} -Path $p" if dynamic else f"{cmd} -Path '{path}'")
                     for cmd, path in cases]
            for case, row in zip(cases, self.run_codes(codes, allowed=ALLOWED + ["Get-ItemProperty"])):
                with self.subTest(case=case, dynamic=dynamic):
                    self.assertEqual(row["valid"], dynamic)
                    self.assertTrue(row["refused"])

    def test_configured_roots_are_exhaustive(self):
        denied, feature = self.run_codes(
            ["Test-Path -LiteralPath $env:SITE_AK_TEMP_INF", "Get-WindowsOptionalFeature -Online"],
            read_roots=[], allowed=ALLOWED + ["Get-WindowsOptionalFeature"])
        self.assertEqual(self.facts["ran_in"]["read_roots"], [])
        self.assertTrue(denied["valid"] and denied["refused"])
        self.assertFalse(feature["valid"])

    def test_only_bound_environment_variables(self):
        read, *bad = self.run_codes([
            'Write-Verbose "$env:TEMP|${env:SystemRoot}|$env:SITE_AK_TEMP_INF"; $true',
            "Get-ChildItem Env:", "(Get-Item Variable:akPath).Value", "$env:PATH",
            "Get-Item Env:TEMP", "Get-Item Env:SystemRoot",
        ], extra_env={"TEMP": "plain-temp", "SystemRoot": "plain-root"})
        self.assertEqual(read["verbose"], [f"plain-temp|plain-root|{self.work / 'policy.inf'}"])
        self.assertTrue(all(not r["valid"] for r in bad), bad)

    def test_runspace_providers(self):
        inspect = (". $env:TRIAL_SCRIPT > $null; "
                   "$rs = [runspacefactory]::CreateRunspace((New-TrialState $false)); "
                   "try { $rs.Open(); $rs.SessionStateProxy.Provider.GetAll().Name } finally { $rs.Dispose() }")
        providers = subprocess.check_output([PWSH, "-NoProfile", "-NonInteractive", "-Command", inspect],
                                            env=dict(os.environ, SITE_CHECK_CONFIG='{"codes":[]}',
                                                     TRIAL_SCRIPT=str(SCRIPT)), text=True, timeout=10).splitlines()
        self.assertIn("FileSystem", providers)
        self.assertFalse({"Environment", "Variable"}.intersection(providers), providers)

    def test_size_limit_and_pipeline_binding(self):
        path = self.root / "bounded"
        path.write_bytes(b"a" * (1024 * 1024))
        code = f"$p = '{path}'; $text = Get-Content -LiteralPath $p -Raw; return ($text.Length -eq 1048576)"
        ok, = self.run_codes([code])
        self.assertIs(ok["result"], True, ok)
        with path.open("ab") as f:
            f.write(b"b")
        big, piped = self.run_codes([code, f"'{path}' | Get-Content -LiteralPath '{path}'"])
        self.assertTrue(big["valid"] and big["refused"], big)
        self.assertIsNone(big["result"])
        self.assertFalse(piped["valid"] or piped["ran"])

    def test_static_proxy_bindings_from_real_patterns(self):
        rules = json.loads((SCRIPT.parents[3] / "tests/fixtures/stig_win/rules.json").read_text())
        fixtures = {r["v"]: patterns.draft(r)["code"] for r in rules if r["v"] in {"V-205764", "V-205834"}}
        rights, audit = fixtures["V-205764"], fixtures["V-205834"]
        guid = "{0CCE9249-69AE-11D9-BED3-505054503030}"
        call = "auditpol.exe /get ('/subcategory:' + $name) /r"
        bad = [
            rights.replace("$f = $env:SITE_AK_TEMP_INF", r"$f = 'C:\Windows\win.ini'"),
            rights.replace("Join-Path $env:TEMP ('site-ak-' + [guid]::NewGuid().ToString('N') + '.inf')", "'other'"),
            rights.replace("$l = $f + '.log'", "$l = $env:TEMP"),
            audit.replace(f"'{guid}'", "$env:TEMP"),
            audit.replace("$csv = ", "$name += 'extra'; $csv = "),
            call, 'auditpol.exe /get "/category:*" /r', "auditpol.exe /get $name /r",
            'auditpol.exe /get "/subcategory:$name" /r', "auditpol.exe /get ('/category:' + '*') /r",
            "auditpol.exe /get '/subcategory:{invalid}' /r", "auditpol.exe /get /subcategory:Name /r",
            "$name = 'Example'; foreach ($name in @('Example')) { " + call + " }",
        ]
        good = [rights, audit, f'auditpol.exe /get "/subcategory:{guid}" /r',
                "auditpol.exe /get '/subcategory:Example Name' /r"]
        rows = self.run_codes(good + bad, allowed=list(patterns.ALLOWED), fixed=True, trial=False)
        for code, row in zip(good + bad, rows):
            with self.subTest(code=code):
                self.assertFalse(row["ran"])
                self.assertEqual(row["valid"], code in good, row)
        act, read = self.run_codes([rights, audit], allowed=list(patterns.ALLOWED), trial=False)
        self.assertFalse(act["valid"])
        self.assertTrue(read["valid"], read)

    def test_methods_and_property_assignment_rejected_before_trial(self):
        method, write = self.run_codes(["$key.GetValueKind($name)", "$x.Prop = 'value'"], trial=False)
        self.assertEqual(method["why"], "method is not allowed")
        self.assertEqual(write["why"], "only local variables can be assigned")
        self.assertFalse(any(row["valid"] or row["ran"] for row in (method, write)))

    def test_the_registry_kind_fallback_is_for_fixed_patterns_and_only_that_text(self):
        """The fixed REG_SZ pattern's fallback for Evaluate-STIG's own session (the trial uses the proxy and never runs it); nothing else with a method."""
        fb = "$path = 'HKLM:\\SOFTWARE\\Example'; $name = 'Value'; $kind = (Get-Item -LiteralPath $path).GetValueKind($name).ToString(); return $true"
        fixed_ok, = self.run_codes([fb], fixed=True, trial=False)
        self.assertTrue(fixed_ok["valid"], fixed_ok["why"])
        act, = self.run_codes([fb], fixed=False, trial=False)
        self.assertEqual((act["valid"], act["why"]), (False, "method is not allowed"))
        other, = self.run_codes(["$path = 'HKLM:\\X'; $name = 'V'; $kind = (Get-Item -LiteralPath $path).GetValueKind('Other').ToString(); return $true"], fixed=True, trial=False)
        self.assertFalse(other["valid"])
        bad, = self.run_codes(["$path = 'HKLM:\\X'; $x = (Get-Item -LiteralPath $path).DeleteValue($name); return $true"], fixed=True, trial=False)
        self.assertFalse(bad["valid"])

    @unittest.skipIf(os.name == "nt", "PID probe uses a POSIX native command stand-in")
    def test_noncooperative_worker_is_gone_at_timeout(self):
        pidfile = self.root / "worker.pid"
        probe = self.root / "auditpol.exe"
        probe.write_text(f'#!/bin/sh\necho "$PPID" > "{pidfile}"\n')
        probe.chmod(0o700)
        for body in ("while ($true) {}", "('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa!' -match '(a+)+$')"):
            with self.subTest(body=body):
                start = time.monotonic()
                row, = self.run_codes(["auditpol.exe /get '/subcategory:Example' /r; " + body], timeout=3)
                self.assertLess(time.monotonic() - start, 25)          # the timeout is 2 s; a loaded machine starts a process slowly
                self.assertEqual(row["why"], "timed out")
                with self.assertRaises(ProcessLookupError):
                    os.kill(int(pidfile.read_text()), 0)
                pidfile.unlink()

    @unittest.skipUnless(sys.platform.startswith("linux"), "uses procfs to inspect a native descendant")
    def test_timeout_stops_a_native_descendant(self):
        probe = self.root / "auditpol.exe"
        pidfile = self.root / "native.pid"
        probe.write_text(f'#!/bin/sh\necho "$$" > "{pidfile}"\nexec sleep 30\n')
        probe.chmod(0o700)
        row, = self.run_codes(["auditpol.exe /get '/subcategory:Example' /r"], timeout=1)
        self.assertEqual(row["why"], "timed out")
        pid = int(pidfile.read_text())
        try:
            # An exited orphan can remain in procfs until init reaps it.
            status = Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1]
        except FileNotFoundError:
            return
        if not status.startswith("Z "):
            os.kill(pid, 9)
            self.fail("the native descendant survived the worker timeout")

    def test_work_folder_ownership(self):
        existing = self.root / "site-ak-existing"
        existing.mkdir()
        fixture = existing / "keep"
        fixture.write_text("example")
        row, = self.run_codes(["$true"], work_dir=str(existing))
        self.assertFalse(row["ran"])
        self.assertEqual(fixture.read_text(), "example")
        row, = self.run_codes(["Write-Verbose $env:SITE_AK_TEMP_INF; $true"], work_dir="")
        self.assertIs(row["result"], True)
        default_folder = Path(row["verbose"][0]).parent
        self.assertRegex(default_folder.name, r"^site-ak-[0-9a-f-]{36}$")
        self.assertFalse(default_folder.exists())

    def test_write_attempts(self):
        cases = [
            "Set-Content FILE value", "'x' | Out-File FILE", "New-Item FILE",
            "Remove-Item -LiteralPath FILE -Force -ErrorAction SilentlyContinue", "'x' > FILE",
            "'x' >> FILE", '"$([IO.File]::WriteAllText(FILE, \'x\'))"', "Add-Type 'class Example {}'",
            "Invoke-Expression 'New-Item FILE'", "& 'something' FILE", ". FILE",
            "Start-Process something FILE", "$c = 'Set-Content'; & $c FILE value",
            '@"\n$(Set-Content FILE value)\n"@', "&('Set-' + 'Content') FILE value",
            "1 | ForEach-Object { Set-Content FILE x }", "$env:SITE_AK_TEMP_INF = FILE",
            "$item = Get-Item FILE; $item.IsReadOnly = $true",
        ]
        paths = [self.root / f"example-{i}" for i in range(len(cases))]
        codes = [case.replace("FILE", "'" + str(path) + "'") for case, path in zip(cases, paths)]
        for case, row, path in zip(cases, self.run_codes(codes, allowed=ALLOWED + ["ForEach-Object"]), paths):
            with self.subTest(case=case):
                self.assertFalse(path.exists())
                self.assertTrue(not row["valid"] or row["refused"])

    @unittest.skipIf(os.name == "nt", "fake native commands use POSIX executable scripts")
    def test_native_and_cleanup_proxies(self):
        for name in ("secedit", "auditpol"):
            exe = self.root / (name + ".exe")
            exe.write_text("#!/bin/sh\n" + f'echo called >> "{self.root / name}"\n' +
                           ('echo example > "$5"; echo logged > "$7"\n' if name == "secedit" else "echo example\n"))
            exe.chmod(0o700)
        target = self.root / "keep"
        target.write_text("keep")
        bind = "$f = $env:SITE_AK_TEMP_INF; $l = $f + '.log'; "
        other = f"$f = '{target}'; "
        export = "secedit.exe /export /areas USER_RIGHTS /cfg $f /log $l"
        remove = "Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue"
        audit = "auditpol.exe /get '/subcategory:Example Name' /r"
        bad = [other + "$l = $env:SITE_AK_TEMP_INF + '.log'; " + export,
               other.replace("$f", "$l") + remove.replace("$f", "$l"),
               export.removesuffix(" /log $l"), bind + "$l = 'wrong'; " + export, bind + export + " extra", bind + export.replace("/export", "/EXPORT"),
               bind + export.replace("/export", "/configure"), other + remove, remove + " extra",
               audit.replace("/get", "/set"), audit.replace("/get", "/GET"), audit + " extra",
               audit.replace("/subcategory:", "/category:")]
        for code, row in zip(bad, self.run_codes(bad, fixed=True)):
            with self.subTest(code=code):
                self.assertTrue(row["refused"], row)
        for name in ("secedit", "auditpol"):
            self.assertFalse((self.root / name).exists())
        self.assertEqual(target.read_text(), "keep")
        code = (bind + export + "; $read = Get-Content -LiteralPath $f; " + remove +
                "; " + remove.replace("$f", "$l") + "; return ($read -eq 'example' -and !(Test-Path -LiteralPath $f))")
        audit_guid = ("$name = '{0CCE9249-69AE-11D9-BED3-505054503030}'; "
                      "$read = auditpol.exe /get ('/subcategory:' + $name) /r; return ($read -eq 'example')")
        for row in self.run_codes([code], fixed=True) + self.run_codes([
                f"$read = {audit}; return ($read -eq 'example')", audit_guid]):
            self.assertEqual(row["refused"], [])
            self.assertIs(row["result"], True, row)
        self.assertEqual((self.root / "secedit").read_text().splitlines(), ["called"])
        self.assertEqual((self.root / "auditpol").read_text().splitlines(), ["called", "called"])

        self.assertEqual(target.read_text(), "keep")

    @unittest.skipIf(os.name == "nt", "fake native commands use a POSIX executable script")
    def test_per_draft_cleanup(self):
        exe = self.root / "secedit.exe"
        exe.write_text('#!/bin/sh\ntouch "$5" "$7"\nmkdir "$(dirname "$5")/scratch"\ntouch "$(dirname "$5")/scratch/file"\n')
        exe.chmod(0o700)
        bind = "$f = $env:SITE_AK_TEMP_INF; $l = $f + '.log'; "
        export = "secedit.exe /export /areas USER_RIGHTS /cfg $f /log $l"
        dirty, clean = self.run_codes([bind + export + "; $true",
                                       "return (@(Get-ChildItem -Force -LiteralPath '.').Count -eq 0)"], fixed=True)
        self.assertTrue(self.facts["clean"] and self.facts["ran_in"]["clean"])
        self.assertIs(dirty["result"], True, dirty)
        self.assertIs(clean["result"], True)

    @unittest.skipUnless(os.name == "posix" and os.geteuid() != 0, "requires POSIX directory permissions")
    def test_failed_cleanup_blocks_the_next_draft(self):
        scratch = self.work / "scratch"
        probe = self.root / "auditpol.exe"
        probe.write_text('#!/bin/sh\ncd "$(dirname "$SITE_AK_TEMP_INF")" || exit 1\nmkdir scratch && touch scratch/file && chmod 500 scratch\n')
        probe.chmod(0o700)
        try:
            dirty, skipped = self.run_codes(["auditpol.exe /get '/subcategory:Example' /r; $true", "$true"],
                                            leftover=["scratch", "scratch/file"])
            self.assertFalse(self.facts["clean"] or self.facts["ran_in"]["clean"])
            self.assertIsNone(dirty["result"])
            self.assertIn("scratch/file", dirty["why"])
            self.assertFalse(skipped["ran"])
        finally:
            if scratch.exists():
                scratch.chmod(0o700)


if __name__ == "__main__":
    unittest.main()
