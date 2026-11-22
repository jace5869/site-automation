#!/usr/bin/env python3
"""Unit tests for the Windows STIG answer-key drafter (plugins/module_utils/stig_answer_win.py, 1.105.0): the kind of each DISA
check text, and - over tests/fixtures/stig_win/rules.json (real DISA rules of Windows Server 2019, 2022 and 2025) - that every
pattern's code only reads, says what it read, and is never a false Not a Finding.
    python3 tests/test_stig_answer_win.py"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "module_utils"))
import stig_answer_win as W  # noqa: E402

with open(os.path.join(HERE, "fixtures", "stig_win", "rules.json")) as fh:
    RULES = json.load(fh)


class Kinds(unittest.TestCase):
    def test_the_fixture_has_every_kind_of_the_three_stigs(self):
        self.assertEqual(set(x["stig"] for x in RULES), {"WinServer2019", "WinServer2022", "WinServer2025"})
        self.assertEqual(set(x["class"] for x in RULES), set(W.KINDS))

    def test_each_check_text_is_classified(self):
        for x in RULES:
            self.assertEqual(W.classify(x["check"]), x["class"], x["v"])
        self.assertEqual(W.classify(""), "other")
        self.assertEqual(W.classify(None), "other")


EXPORT = "secedit.exe /export /areas USER_RIGHTS /cfg $f /log $l"
REMOVE = "Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue"
REMOVE_LOG = "Remove-Item -LiteralPath $l -Force -ErrorAction SilentlyContinue"
FALLBACK = r"""if (-not ($f -and $f -match '^(?:[a-z]:[\\/]|/)(?:[^\\/:]+[\\/])*site-ak-[^\\/:]*[\\/][^\\/:]+\.inf\z' -and -not (Test-Path -LiteralPath $f -ErrorAction Stop) -and -not (Test-Path -LiteralPath ($f + '.log') -ErrorAction Stop))) {
        $f = Join-Path $env:TEMP ('site-ak-' + [guid]::NewGuid().ToString('N') + '.inf')
    }
    $l = $f + '.log'"""


# Complete scripts, including error handling, as embedded in ValidationCode.
EXPECTED = {
    "V-205764": r"""try {
    $right = 'SeSystemEnvironmentPrivilege'
    $f = $env:SITE_AK_TEMP_INF
    """ + FALLBACK + r"""
    try {
        $null = $(
            secedit.exe /export /areas USER_RIGHTS /cfg $f /log $l
        )
        if ($LASTEXITCODE -ne 0) { return ($false) }
        $lines = @(Get-Content -LiteralPath $f -ErrorAction Stop)
        $members = ''
        $section = ''
        $found = $false
        $policy = $false
        foreach ($line in $lines) {
            $line = $line -replace '^\s+|\s+$', ''
            if ($line -match '^\[') { $section = $line }
            if (-not $section -and $line -match '^Se[A-Za-z]+\s*=') { $policy = $true }
            if ($section -and $section -ne '[Privilege Rights]') { continue }
            if ($section -eq '[Privilege Rights]') { $policy = $true }
            if ($line -match ('^' + $right + '\b')) {
                if ($found -or $line -notmatch ('^' + $right + '\s*=\s*(.*)$')) { return ($false) }
                $members = $line -replace ('^' + $right + '\s*=\s*'), ''
                $found = $true
            }
        }
        Write-Verbose ('read: ' + $right + ' = ' + $members)
        if (-not $policy) { return ($false) }
        if ($members) {
            foreach ($entry in ($members -split ',')) {
                $value = $entry -replace '^\s+|\s+$', ''
                $sid = $null
                if (-not $value) { return ($false) }
                if ($value -in @('Administrators', 'BUILTIN\Administrators', 'S-1-5-32-544', '*S-1-5-32-544')) { $sid = 'S-1-5-32-544' }
                if (-not $sid) { return ($false) }
            }
        }
    } finally {
        Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $l -Force -ErrorAction SilentlyContinue
    }
    $ok = $true
} catch {
    Write-Verbose ('read: failed: ' + $_)
    $ok = $false
}
return ($ok)""",
    "V-254498": r"""try {
    $right = 'SeCreatePermanentPrivilege'
    $f = $env:SITE_AK_TEMP_INF
    """ + FALLBACK + r"""
    try {
        $null = $(
            secedit.exe /export /areas USER_RIGHTS /cfg $f /log $l
        )
        if ($LASTEXITCODE -ne 0) { return ($false) }
        $lines = @(Get-Content -LiteralPath $f -ErrorAction Stop)
        $members = ''
        $section = ''
        $found = $false
        $policy = $false
        foreach ($line in $lines) {
            $line = $line -replace '^\s+|\s+$', ''
            if ($line -match '^\[') { $section = $line }
            if (-not $section -and $line -match '^Se[A-Za-z]+\s*=') { $policy = $true }
            if ($section -and $section -ne '[Privilege Rights]') { continue }
            if ($section -eq '[Privilege Rights]') { $policy = $true }
            if ($line -match ('^' + $right + '\b')) {
                if ($found -or $line -notmatch ('^' + $right + '\s*=\s*(.*)$')) { return ($false) }
                $members = $line -replace ('^' + $right + '\s*=\s*'), ''
                $found = $true
            }
        }
        Write-Verbose ('read: ' + $right + ' = ' + $members)
        if (-not $policy) { return ($false) }
    } finally {
        Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $l -Force -ErrorAction SilentlyContinue
    }
    $ok = $members -eq ''
} catch {
    Write-Verbose ('read: failed: ' + $_)
    $ok = $false
}
return ($ok)""",
    "V-205820": r"""try {
    $path = 'HKLM:\SYSTEM\CurrentControlSet\Services\NTDS\Parameters'
    $name = 'LDAPServerIntegrity'
    $item = Get-ItemProperty -LiteralPath $path -Name $name -ErrorAction Stop
    $value = $item.$name
    Write-Verbose ('read: ' + $path + ' ' + $name + ' = ' + $value)
    if ($value -isnot [int]) { return ($false) }
    $n = [long]$value -band 4294967295
    $ok = $n -eq [long]'2'
} catch {
    Write-Verbose ('read: failed: ' + $_)
    $ok = $false
}
return ($ok)""",
    "V-205834": r"""try {
    $override = (Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\Lsa' -Name 'SCENoApplyLegacyAuditPolicy' -ErrorAction Stop).SCENoApplyLegacyAuditPolicy
    Write-Verbose ('read: audit policy override = ' + $override)
    if ($override -isnot [int] -or $override -ne 1) { return ($false) }
    $name = '{0CCE9249-69AE-11D9-BED3-505054503030}'
    $csv = auditpol.exe /get ('/subcategory:' + $name) /r
    if ($LASTEXITCODE -ne 0) { return ($false) }
    $rows = @($csv | ConvertFrom-Csv -ErrorAction Stop -WarningAction Stop)
    if ($rows.Count -ne 1) { return ($false) }
    $columns = @($rows[0].PSObject.Properties)
    if ($columns.Count -lt 5 -or $columns[3].Value -ne $name) { return ($false) }
    $setting = $columns[4].Value
    Write-Verbose ('read: ' + $name + ' = ' + $setting)
    $ok = $setting -eq 'Success and Failure' -or $setting -eq 'Success'
} catch {
    Write-Verbose ('read: failed: ' + $_)
    $ok = $false
}
return ($ok)""",
    "V-205698": r"""try {
    $name = 'Telnet-Client'
    $rows = @(Get-WindowsFeature -Name $name -ErrorAction Stop)
    if ($rows.Count -ne 1 -or $rows[0].Name -ne $name) { return ($false) }
    $feature = $rows[0]
    Write-Verbose ('read: ' + $name + ' = ' + $feature.InstallState)
    $ok = $feature.Installed -is [bool] -and -not $feature.Installed -and $feature.InstallState -in @('Available', 'Removed')
} catch {
    Write-Verbose ('read: failed: ' + $_)
    $ok = $false
}
return ($ok)""",
    "V-254456": r"""try {
    $path = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
    $name = 'InactivityTimeoutSecs'
    $item = Get-ItemProperty -LiteralPath $path -Name $name -ErrorAction Stop
    $value = $item.$name
    Write-Verbose ('read: ' + $path + ' ' + $name + ' = ' + $value)
    if ($value -isnot [int]) { return ($false) }
    $n = [long]$value -band 4294967295
    $ok = $n -gt 0 -and $n -le [long]'900'
} catch {
    Write-Verbose ('read: failed: ' + $_)
    $ok = $false
}
return ($ok)""",
}


BY_ID = {rule["v"]: rule for rule in RULES}
FEATURES = {"documented_required_features": ["Example-Feature"]}
SITE = dict(FEATURES, approved_se_security_privilege_accounts=[r"EXAMPLE\Example Auditors"],
            approved_se_network_logon_right_accounts=[], approved_se_interactive_logon_right_accounts=[],
            approved_se_increase_base_priority_privilege_accounts=[], approved_se_debug_privilege_accounts=[])


def generic_check(v):
    """Exercise the body of a role-specific fixture as a synthetic, unscoped check."""
    return {"check": BY_ID[v]["check"].split("\n", 1)[1].lstrip()}


class Drafts(unittest.TestCase):
    def test_role_specific_rules_declined(self):
        for v in ("V-205820", "V-254431", "V-254408", "V-254421"):
            self.assertEqual(W.draft(BY_ID[v], SITE), {"kind": "role_scoped"}, v)

    def test_fixture_contract_and_safety(self):
        for rule in RULES:
            for values in ({}, SITE):
                with self.subTest(rule=rule["v"], values=values):
                    result = W.draft(rule, values)
                    self.assertEqual(result, W.draft(dict(rule), dict(values)))
                    if result == {"kind": "role_scoped"}:
                        self.assertRegex(rule["check"], r"(?m)^This applies to (domain controllers|member servers)")
                        continue
                    if rule["class"] == "other":
                        self.assertIsNone(result)
                    if result is None:
                        continue
                    self.assertIs(result["fixed"], True)
                    if result["kind"] == "needs":
                        self.assertEqual(set(result), {"fixed", "kind", "needs", "example", "why"})
                        self.assertTrue(all(result.values()))
                        continue
                    self.assertEqual(set(result), {"fixed", "kind", "pattern", "code", "reads", "true", "false", "disa", "uses"})
                    self.assertEqual(result["kind"], "pattern")
                    self.assertEqual(result["pattern"], rule["class"])
                    self.assertTrue(all(result[field] for field in ("true", "false", "disa")))
                    code = result["code"]
                    commands = W.commands_in(code)
                    self.assertTrue(commands <= W.ALLOWED.keys())
                    self.assertEqual(result["reads"], [[c, W.ALLOWED[c]] for c in sorted(commands)])
                    self.assertEqual(W.forbidden_forms(code), [])
                    self.assertNotRegex(code.replace(REMOVE, "").replace(REMOVE_LOG, ""), r"(?i)\b(?:Set-|New-|Remove-|Out-File|Add-|Invoke-|Start-|Stop-|Restart-)| > |>>")
                    self.assertNotRegex(code, r"\?\.|\?\?|\s\?\s")
                    self.assertIn("Write-Verbose ('read: ", code)
                    self.assertTrue(code.endswith("return ($ok)"))
                    self.assertIn("} catch {", code)
                    self.assertIn("$ok = $false", code)
                    if "auditpol.exe" in commands:
                        calls = re.findall(r"auditpol\.exe[^\n]*", code)
                        self.assertEqual(len(calls), 1)
                        guid = r"\{[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}"
                        self.assertRegex(calls[0], r"^auditpol\.exe /get (?:\('/subcategory:' \+ \$name\)|"
                                                   r"/subcategory:" + guid + r") /r$")

    def test_pattern_coverage(self):
        for kind, expected in zip(W.KINDS, (30, 21, 10, 11, 0)):
            group = [rule for rule in RULES if rule["class"] == kind]
            count = sum((W.draft(rule, SITE) or {}).get("kind") == "pattern" for rule in group)
            with self.subTest(kind=kind):
                self.assertEqual(count, expected)

    def test_exact_scripts(self):
        for v, expected in EXPECTED.items():
            with self.subTest(rule=v):
                self.assertEqual(W.draft(generic_check(v) if v == "V-205820" else BY_ID[v])["code"], expected)

    def test_unconsumed_text_never_generates_code(self):
        for rule in RULES:
            check = rule["check"]
            for change in (check + "\nValue Name: SecondValue", "Unknown condition.\n" + check):
                with self.subTest(rule=rule["v"]):
                    self.assertNotIn("code", W.draft({"check": change}, SITE) or {})

    def test_reject_unmodelled_fields(self):
        for v, old, replacements in (
                ("V-205820", "LDAPServerIntegrity", ["", "(Default)", "One, Two"]),
                ("V-205820", "REG_DWORD", ["REG_MULTI_SZ", ""]),
                ("V-205820", "0x00000002 (2)", ["", "0x00000002 (1)", "1 or probably 2", "1 (or less"]),
                ("V-205834", "Membership - Success", ["Membership -", "Membership - Sometimes"]),
                ("V-205834", "Logon/Logoff", ["System", "Unknown Category"]),
                ("V-205764", "Administrators", ["", "Unknown Group"]),
                ("V-205698", "Telnet-Client", ["", "*", "One, Two"]),
                ("V-205698", '"Installed", this is a finding', ['"Unknown", this is a finding'])):
            for replacement in replacements:
                with self.subTest(rule=v, replacement=replacement):
                    check = (generic_check(v) if v == "V-205820" else BY_ID[v])["check"]
                    self.assertIn(old, check)
                    self.assertIsNone(W.draft({"check": check.replace(old, replacement)}))

    def test_registry_literals(self):
        check = generic_check("V-205820")["check"].replace("REG_DWORD", "REG_SZ").replace("0x00000002 (2)", "Example's")
        code = W.draft({"check": check.replace("LDAPServerIntegrity", "Example'Name")})["code"]
        self.assertIn("$name = 'Example''Name'", code)
        self.assertIn("$value -ceq 'Example''s'", code)

    def test_ranges_alternatives_and_missing(self):
        rule = generic_check("V-205820")
        for spec, expression in (("3 or less", "$n -le [long]'3'"), ("any of 0, 1, or 8", "@([long]'0', [long]'1', [long]'8') -contains $n")):
            code = W.draft({"check": rule["check"].replace("0x00000002 (2)", spec)})["code"]
            self.assertIn(expression, code)
        self.assertNotIn("return ($true)", W.draft(rule)["code"])
        code = W.draft(BY_ID["V-254363"])["code"]
        self.assertIn("Get-ChildItem -LiteralPath $parent -ErrorAction Stop", code)
        self.assertIn("if ($names -notcontains $name)", code)
        self.assertNotIn("return ($true)", code.split("catch {")[1])

    def test_stricter_audit_setting(self):
        rule = generic_check("V-254408")
        for requested in ("Success", "Failure", "Success and Failure"):
            code = W.draft({"check": rule["check"].replace(" - Success", " - " + requested)})["code"]
            comparison = "$ok = $setting -eq 'Success and Failure'"
            if requested != "Success and Failure":
                comparison += " -or $setting -eq '%s'" % requested
            self.assertIn(comparison + "\n", code)

    def test_documented_roles_need_site_evidence(self):
        for v in ("V-205677", "V-278011"):
            for value in (None, {}, {"documented_required_features": ["*"]}):
                self.assertEqual(W.draft(BY_ID[v], value)["kind"], "needs")
            self.assertEqual(W.draft(BY_ID[v], SITE)["uses"], FEATURES)

    def test_documented_user_right_exceptions(self):
        for v in ("V-205643", "V-205676", "V-254504", "V-278250", "V-278243"):
            with self.subTest(v=v):
                need = W.draft(BY_ID[v])
                self.assertEqual(need["kind"], "needs")
                key = need["needs"]
                self.assertRegex(key, r"^[a-z]+(?:_[a-z]+)*$")
                for bad in (None, {}, "Example", ["*"], [""]):
                    self.assertEqual(W.draft(BY_ID[v], {key: bad}), need)
                code = W.draft(BY_ID[v], {key: [r"EXAMPLE\Example's Account"]})["code"]
                self.assertIn(r"'EXAMPLE\Example''s Account'", code)
        self.assertIn("Auditors", W.draft(BY_ID["V-205643"])["why"])

    def test_unverified_domain_or_profile(self):
        for v in ("V-254437", "V-278186", "V-205924"):
            with self.subTest(v=v):
                self.assertNotIn("code", W.draft(BY_ID[v], SITE) or {})
        for hive in ("HKCU", "HKCU:", "HKEY_USERS"):
            check = BY_ID["V-205924"]["check"].replace("HKEY_CURRENT_USER", hive)
            self.assertIsNone(W.draft({"check": check}))

    def test_unmodelled_applicability(self):
        for v in ("V-254360", "V-278016", "V-205684", "V-278025", "V-278023", "V-254485", "V-278237"):
            with self.subTest(v=v):
                self.assertIsNone(W.draft(BY_ID[v], SITE))

    def test_explicit_user_right_directions(self):
        check = BY_ID["V-205764"]["check"]
        for header, comparison in (
                ("Only the following {group} may be granted the {right}", "$true"),
                ("Exactly the following {group} must be granted the {right}", "$seen.Count -eq 1"),
                ("The {right} must include the following {group}", "$seen.Count -eq 1"),
                ("Review the following {group} for the {right}", None)):
            changed = re.sub(r"If any (accounts or groups|SIDs) other than the following are granted the "
                             r'("[^"]+" user right), this is a finding',
                             lambda m: header.format(group=m[1], right=m[2]), check)
            draft = W.draft({"check": changed})
            if comparison is None:
                self.assertIsNone(draft)
            else:
                self.assertIn("$ok = " + comparison, draft["code"])
                self.assertEqual("if (-not $sid)" in draft["code"], "must include" not in header)
        check = generic_check("V-254421")["check"]
        changed = re.sub(r'If the following (accounts or groups|SIDs) are not defined for the ("[^"]+" user right), '
                         r'this is a finding', r'The \2 must deny the following \1', check)
        self.assertEqual(W.draft({"check": changed}), W.draft(generic_check("V-254421")))
        self.assertIsNone(W.draft({"check": changed.replace("must deny", "must include", 1)}))

    def test_audit_guid_table_and_unknown_subcategory(self):
        check = BY_ID["V-205834"]["check"]
        for subcategory, fragment in (("Detailed Tracking >> Process Creation", "2B"),
                                      ("Account Logon >> Kerberos Authentication Service", "42"),
                                      ("Logon/Logoff >> Group Membership", "49")):
            draft = W.draft({"check": check.replace("Logon/Logoff >> Group Membership", subcategory)})
            self.assertIn("$name = '{0CCE92" + fragment + "-69AE-11D9-BED3-505054503030}'", draft["code"])
            self.assertIn("Non-English Windows", draft["false"])
        self.assertIsNone(W.draft({"check": check.replace("Group Membership", "Unknown Subcategory")}))

    @unittest.skipUnless(shutil.which("pwsh"), "pwsh unavailable")
    def test_registry_kind_proxy_and_evaluate_fallback(self):
        code = W.draft(generic_check("V-278182"))["code"]
        self.assertIn("Get-SiteValueKind", W.commands_in(code))
        script = r"""
function Get-ItemProperty { [pscustomobject]@{RestrictRemoteSAM = 'O:BAG:BAD:(A;;RC;;;BA)'} }
function Read-Rule {
CODE_BLOCK
}
function Get-Item {
    $key = [pscustomobject]@{}
    $key | Add-Member ScriptMethod GetValueKind { param($name) $testKind }
    $key
}
$testKind = 'String'
if (!(Read-Rule)) { throw 'Evaluate fallback' }
$testKind = 'ExpandString'
if (Read-Rule) { throw 'Evaluate ExpandString' }
function Get-SiteValueKind {
    param($LiteralPath, $Name)
    if ($LiteralPath -cne 'HKLM:\SYSTEM\CurrentControlSet\Control\Lsa' -or $Name -cne 'RestrictRemoteSAM') { throw 'arguments' }
    $testKind
}
$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'
function Read-TrialRule {
CODE_BLOCK
}
$testKind = 'String'
if (!(Read-TrialRule)) { throw 'trial proxy' }
$testKind = 'ExpandString'
if (Read-TrialRule) { throw 'trial ExpandString' }
"""
        self.run_pwsh(script.replace("CODE_BLOCK", code))

    @unittest.skipUnless(shutil.which("pwsh"), "pwsh unavailable")
    def test_audit_csv_positions_and_localized_setting(self):
        script = r"""
function Get-ItemProperty { [pscustomobject]@{SCENoApplyLegacyAuditPolicy = 1} }
function auditpol.exe {
    if (($args -join ' ') -cne ('/get /subcategory:' + $guid + ' /r')) { throw 'arguments' }
    $global:LASTEXITCODE = 0
    'Host,Ziel,Kategorie,Kennung,Einschluss,Ausschluss'
    'Example,System,"Example, localized",' + $rowGuid + ',' + $label + ',Success'
}
$guid = '{0CCE9249-69AE-11D9-BED3-505054503030}'
$rowGuid = $guid
$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'
function Read-Rule {
CODE_BLOCK
}
foreach ($label in @('Success', 'Success and Failure')) {
    if (!(Read-Rule)) { throw 'inclusion setting' }
}
foreach ($label in @('Failure', 'Erfolg', '3', '')) {
    if (Read-Rule) { throw 'unknown or insufficient inclusion setting' }
}
$rowGuid = '{0CCE9210-69AE-11D9-BED3-505054503030}'
$label = 'Success'
if (Read-Rule) { throw 'wrong subcategory' }
"""
        self.run_pwsh(script.replace("CODE_BLOCK", W.draft(BY_ID["V-205834"])["code"]))

    def run_pwsh(self, script):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "example.ps1")
            with open(path, "w") as fh:
                fh.write("$ErrorActionPreference = 'Stop'\n" + script)
            proc = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File", path],
                                  capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_user_right_coverage_and_cleanup(self):
        counts = {}
        for rule in RULES:
            if rule["class"] != "user right":
                continue
            result = W.draft(rule, SITE)
            if result and result.get("code"):
                code = result["code"]
                self.assertIn("$f = $env:SITE_AK_TEMP_INF\n    " + FALLBACK, code)
                self.assertIn(EXPORT, [line.strip() for line in code.splitlines()])
                self.assertIn("} finally {\n        " + REMOVE + "\n", code)
                counts[rule["stig"]] = counts.get(rule["stig"], 0) + 1
        self.assertEqual(counts, {"WinServer2019": 3, "WinServer2022": 3, "WinServer2025": 4})

    def test_export_command_forms(self):
        for command in (EXPORT, REMOVE, REMOVE_LOG):
            self.assertEqual(W.forbidden_forms(command), [])
            self.assertEqual(W.commands_in(command), {command.split()[0]})
            for bad in (command + " -Other", re.sub(r"\$[fl]", "$other", command), command + "; " + command,
                        command + " | Out-String", command.lower(), "& " + command):
                self.assertTrue(W.forbidden_forms(bad), bad)
        self.assertTrue(W.forbidden_forms("secedit.exe /export /areas USER_RIGHTS /cfg $f"))
        self.assertEqual(W.forbidden_forms("# Remove-Item skipped\n'not secedit.exe'"), [])

    def test_incomplete_or_inconsistent_right(self):
        check = BY_ID["V-205764"]["check"]
        for changed in (check + "\n" + BY_ID["V-254498"]["check"],
                        check.replace("Modify firmware environment values", "Example right"),
                        check.replace("- Administrators", "- Administrators and others"),
                        check.replace("SeSystemEnvironmentPrivilege", "SeDebugPrivilege"),
                        check.replace("S-1-5-32-544 (Administrators)", ""),
                        check.replace("S-1-5-32-544", "S-1-5-32-545")):
            self.assertIsNone(W.draft({"check": changed}))

    def test_principal_sids(self):
        for name, sid in {
                "Administrators": "S-1-5-32-544", "Local Service": "S-1-5-19", "Network Service": "S-1-5-20",
                "Service": "S-1-5-6", "Users": "S-1-5-32-545", "Guests": "S-1-5-32-546",
                "Remote Desktop Users": "S-1-5-32-555", "Backup Operators": "S-1-5-32-551",
                "Local account": "S-1-5-113", "Local account and member of Administrators group": "S-1-5-114",
                "Domain Admins": "S-1-5-domain-512", "Domain Controllers": "S-1-5-domain-516",
                "Enterprise Admins": "S-1-5-root domain-519"}.items():
            self.assertEqual(W._SIDS[name], sid)

    @unittest.skipUnless(shutil.which("pwsh"), "pwsh unavailable")
    def test_exported_member_sets(self):
        with tempfile.TemporaryDirectory(prefix="site-ak-") as directory:
            env = dict(os.environ, SITE_AK_TEMP_INF=os.path.join(directory, "example.inf"), TEMP=directory)
            script = [r"""$ErrorActionPreference = 'Stop'
function secedit.exe {
    if ($args.Count -ne 7 -or ($args[0..3] -join ' ') -cne '/export /areas USER_RIGHTS /cfg' -or
        $args[5] -cne '/log' -or $args[6] -cne ($args[4] + '.log')) { throw 'unexpected export' }
    if ($fallback) {
        if ($args[4] -ceq $env:SITE_AK_TEMP_INF -or $args[4] -notmatch 'site-ak-[0-9a-f]{32}\.inf$') { throw 'unsafe path' }
    } elseif ($args[4] -cne $env:SITE_AK_TEMP_INF) { throw 'unexpected path' }
    $script:exportPath = $args[4]
    Set-Content -LiteralPath $args[4] -Value @('[Privilege Rights]', $sample)
    Set-Content -LiteralPath $args[6] -Value 'example log'
    $global:LASTEXITCODE = $exitCode
    'export output'
}
$exitCode = 0
$fallback = $false"""]
            def run_case(v, sample, expected):
                path = os.path.join(directory, v + ".ps1")
                with open(path, "w") as fh:
                    rule = generic_check(v) if v in ("V-254418", "V-254421") else BY_ID[v]
                    fh.write(W.draft(rule, SITE)["code"])
                script.extend(["$sample = " + W._q(sample), "$result = . " + W._q(path),
                               "if ($result -isnot [bool] -or $result -ne $%s) { throw 'case %s' }"
                               % (str(expected).lower(), len(script)),
                               "if ((Test-Path -LiteralPath $exportPath) -or (Test-Path -LiteralPath ($exportPath + '.log'))) "
                               "{ throw 'export or log remains' }"])
            network = "SeNetworkLogonRight = "
            exact = r"*S-1-5-9, NT AUTHORITY\Authenticated Users, Administrators, *S-1-5-32-544"
            for members, expected in ((exact, True), (exact + ",Guests", False), ("Administrators", True),
                                      (exact + ",*S-1-5-123", False), (exact + ",", False)):
                run_case("V-254418", network + members, expected)
            for sample, expected in (("SeCreatePermanentPrivilege = ", True), (network + exact, True),
                                     ("SeCreatePermanentPrivilege = Example", False)):
                run_case("V-254498", sample, expected)
            deny = "SeDenyNetworkLogonRight = "
            for members, expected in (("Guests,Example", True), ("Example", False), ("", False)):
                run_case("V-254421", deny + members, expected)
            for member, expected in ((r"EXAMPLE\Example Auditors", True), (r"OTHER\Example Auditors", False)):
                run_case("V-205643", "SeSecurityPrivilege = " + member, expected)
            script.extend(["$fallback = $true", "Set-Content -LiteralPath $env:SITE_AK_TEMP_INF -Value 'original'"])
            run_case("V-254498", "", True)
            script.extend(["if ((Get-Content -LiteralPath $env:SITE_AK_TEMP_INF) -cne 'original') { throw 'lost file' }",
                           "$env:SITE_AK_TEMP_INF += '.inf'", "Set-Content -LiteralPath ($env:SITE_AK_TEMP_INF + '.log') -Value 'original'"])
            run_case("V-254498", "", True)
            script.extend(["if ((Get-Content -LiteralPath ($env:SITE_AK_TEMP_INF + '.log')) -cne 'original') { throw 'lost log' }",
                           "$env:SITE_AK_TEMP_INF = Join-Path $env:TEMP 'outside/example.inf'"])
            run_case("V-254498", "", True)
            script.append("$exitCode = 1")
            run_case("V-254498", "", False)
            runner = os.path.join(directory, "trial.ps1")
            with open(runner, "w") as fh:
                fh.write("\n".join(script))
            proc = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File", runner], env=env,
                                  capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_inventory_excludes_quoted_data_and_comments(self):
        code = """Get-Item 'Example''s Set-Thing' # Start-Thing
<# <# [Fake]::Write() #> Set-Thing #>
Get-Content "quoted.exe"
@'
Remove-Thing
'@
[Example.Type] :: Read() | Example-Tool.EXE | Write-Verbose"""
        self.assertEqual(W.commands_in(code),
                         {"Get-Item", "Get-Content", "[Example.Type]::Read", "Example-Tool.EXE", "Write-Verbose"})


if __name__ == "__main__":
    unittest.main()
