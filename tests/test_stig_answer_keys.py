#!/usr/bin/env python3
"""1.75.0: STIG answer keys - the fixed patterns (on DISA's own RHEL 9 check texts), the <AnswerKey> block, the rules a key
may be for, ACT's draft, the report; and the fixed read-only check (PowerShell's parser, with pwsh) on the codes it must
allow and the tricks it must refuse.
  python tests/test_stig_answer_keys.py"""
from __future__ import absolute_import, division, print_function

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
sys.path.insert(0, os.path.join(REPO, "plugins", "module_utils"))
sys.path.insert(0, HERE)
import ansible.module_utils  # noqa: E402,F401
import stig_answer_filters as a  # noqa: E402

spec = importlib.util.spec_from_file_location("site_stig_answer_host", os.path.join(REPO, "roles", "check_stig", "library", "site_stig_answer_host.py"))
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)
PWSH = host.find_pwsh("")

# DISA's own RHEL 9 V2R7 check texts (public), word for word
T_OWNER = ('Verify the ownership of the "/etc/group-" file with the following command:\n\n$ sudo stat -c "%U %n" /etc/group- \n\n'
           'root /etc/group- \n\nIf "/etc/group-" file does not have an owner of "root", this is a finding.')
T_MODE = ('Verify the "/var/log/messages" file has mode "0640" or less permissive with the following command:\n\n'
          '$ sudo stat -c "%a %n" /var/log/messages\n\n640 /var/log/messages\n\nIf "/var/log/messages" does not have a mode of "0640" '
          'or less permissive, this is a finding.')
T_SYSCTL = ('Verify RHEL 9 prevents the loading of a new kernel for later execution.\n\nCheck the status of the kernel.kexec_load_disabled '
            'kernel parameter with the following command:\n\n$ sudo sysctl kernel.kexec_load_disabled\n\nkernel.kexec_load_disabled = 1\n\n'
            'If "kernel.kexec_load_disabled" is not set to "1" or is missing, this is a finding.')
T_PKG_GONE = ('Verify that the nfs-utils package is not installed with the following command:\n\n$ dnf list --installed nfs-utils\n\n'
              'Error: No matching Packages to list\n\nIf the "nfs-utils" package is installed, this is a finding.')
T_PKG = ('Verify that RHEL 9 fapolicyd package is installed with the following command:\n\n$ dnf list --installed fapolicyd\n\n'
         'Example output:\n\nfapolicyd.x86_64          1.1-103.el9_0\n\nIf the "fapolicyd" package is not installed, this is a finding.')
T_MIN = ('Verify RHEL 9 enforces 24 hours as the minimum password lifetime for new user accounts.\n\nCheck for the value of '
         '"PASS_MIN_DAYS" in "/etc/login.defs" with the following command: \n\n$ grep -i pass_min_days /etc/login.defs\n\nPASS_MIN_DAYS 1\n\n'
         'If the "PASS_MIN_DAYS" parameter value is not "1" or greater, or is commented out, this is a finding.')
T_ASK = ('Verify RHEL 9 takes the appropriate action when an audit processing failure occurs.\n\n$ sudo grep disk_error_action '
         '/etc/audit/auditd.conf\n\ndisk_error_action = HALT\n\nIf the value of the "disk_error_action" option is not "SYSLOG", "SINGLE", '
         'or "HALT", or the line is commented out, ask the system administrator (SA) to indicate how the system takes appropriate action '
         'when an audit process failure occurs. If there is no evidence of appropriate action, this is a finding.')
T_RELEASE = ('Verify the version or RHEL 9 is vendor supported with the following command:\n\n$ cat /etc/redhat-release \n\n'
             'Red Hat Enterprise Linux release 9.6 (Plow)\n\nIf the installed version of RHEL 9 is not supported, this is a finding.')
T_WW = ('Verify RHEL 9 world writable directories are owned by root, a system account, or an application account with the following '
        'command:\n\n$ sudo find / -xdev -type d -perm -0002 -uid +999 -exec stat -c "%U, %u, %A, %n" {} \\; 2>/dev/null\n\nIf there '
        'is output that indicates world-writable directories are owned by any account other than root or an approved system account, '
        'this is a finding.')
T_NONE = 'Verify the system is not using a document. Interview the ISSO.'
# RHEL 10 V1R3 (and RHEL 9 V2R10, RHEL 8 V2R9 the same): excerpts
T_OPENSSL = ('Verify the RHEL 10 OpenSSL library is configured to use only ciphers employing FIPS 140-3-approved algorithms with the '
             'following command:\n\n$ grep -i opensslcnf.config /etc/pki/tls/openssl.cnf\n.include = /etc/crypto-policies/back-ends/'
             'opensslcnf.config\n\nIf the "opensslcnf.config" is not defined in the "/etc/pki/tls/openssl.cnf" file, this is a finding.')
T_AUDIT = ('Verify RHEL 10 generates audit records for successful and unsuccessful uses of the "passwd" command with the following '
           "command:\n\n$ sudo auditctl -l | grep '/usr/bin/passwd'\n\n-a always,exit -F arch=b32 -F path=/usr/bin/passwd -F perm=x "
           "-F key=privileged-passwd\n-a always,exit -F arch=b64 -F path=/usr/bin/passwd -F perm=x -F key=privileged-passwd\n\n"
           "If the command does not return these lines, or the lines are commented out, this is a finding.")
T_RELEASE10 = ('Verify RHEL 10 is a vendor-supported version with the following command:\n\n$ cat /etc/redhat-release\n'
               'Red Hat Enterprise Linux release 10.0 (Coughlan)\n\nIf the installed version of RHEL 10 is not supported, this is a finding.')


class Patterns(unittest.TestCase):
    def test_each(self):
        cases = [(T_OWNER, "/usr/bin/stat -c '%U' '/etc/group-'", "-eq 'root'"),
                 (T_MODE, "/usr/bin/stat -c '%a' '/var/log/messages'", "foreach ($i in 0..3)"),
                 (T_SYSCTL, "/usr/sbin/sysctl -n 'kernel.kexec_load_disabled'", "-eq '1'"),
                 (T_PKG_GONE, "/usr/bin/rpm -q 'nfs-utils'", "return ($rc -eq 1 -and"),
                 (T_PKG, "/usr/bin/rpm -q 'fapolicyd'", "return ($rc -eq 0)"),
                 (T_MIN, "Get-Content -LiteralPath '/etc/login.defs'", "[int]$m.Groups[1].Value -ge 1")]
        for text, run, test in cases:
            with self.subTest(run=run):
                d = a.stig_answer_draft(text)
                self.assertEqual(d["kind"], "pattern")
                self.assertIn(run, d["code"])
                self.assertIn(test, d["code"])
                self.assertIn("Write-Verbose", d["code"])
                self.assertTrue(d["true"].startswith(a.PREFIX) and d["false"].startswith(a.PREFIX))

    def test_rhel_10(self):
        d = a.stig_answer_draft(T_OPENSSL)
        self.assertIn("'^\\s*\\.include\\s*=?\\s*/etc/crypto\\-policies/back\\-ends/opensslcnf\\.config\\s*$'", d["code"])
        self.assertIn("return ($l.Count -ge 1)", d["code"])
        d = a.stig_answer_draft(T_AUDIT)
        self.assertIn("& /usr/sbin/auditctl -l 2>&1", d["code"])
        self.assertIn("'-a always,exit -F arch=b64 -F path=/usr/bin/passwd -F perm=x -F key=privileged-passwd'", d["code"])
        self.assertEqual(a.stig_answer_draft(T_RELEASE10, {"min_release": "9.4"})["example"],
                         "answer_keys_values: {min_release: ['9.4', '10.0']}")                    # quoted: 8.10 is no 8.1
        d = a.stig_answer_draft(T_RELEASE10, {"min_release": "8.10, 9.4 10.0"})
        self.assertIn("return ($have.Major -eq 10 -and $have -ge [version]'10.0')", d["code"])
        self.assertEqual(d["uses"], {"min_release": "10.0"})
        self.assertIn("$have.Major -eq 9", a.stig_answer_draft(T_RELEASE, {"min_release": ["8.10", "9.4", "10.0"]})["code"])

    def test_a_persons_answer_is_no_pattern(self):
        """'ask the system administrator': a person decides - no pattern (ACT may draft it, or by hand)."""
        self.assertIsNone(a.stig_answer_draft(T_ASK))
        self.assertIsNone(a.stig_answer_draft(T_NONE))

    def test_a_list_of_values(self):
        d = a.stig_answer_draft(T_ASK.replace("ask the system administrator (SA) to indicate how", "and"))
        self.assertIn("@('SYSLOG', 'SINGLE', 'HALT') -contains $m.Groups[1].Value", d["code"])

    def test_site_values(self):
        self.assertEqual(a.stig_answer_draft(T_RELEASE)["needs"], "min_release")
        d = a.stig_answer_draft(T_RELEASE, {"min_release": "9.4"})
        self.assertIn("-ge [version]'9.4'", d["code"])
        self.assertEqual(d["uses"], {"min_release": "9.4"})
        self.assertEqual(a.stig_answer_draft(T_WW)["needs"], "world_writable_owners")
        d = a.stig_answer_draft(T_WW, {"world_writable_owners": ["svc_app1", "svc_backup"]})
        self.assertIn("-printf '%u %p\\n'", d["code"])
        self.assertNotIn("-exec", "\n".join(x for x in d["code"].split("\n") if not x.startswith("#")))
        self.assertIn("$ok = @('svc_app1', 'svc_backup')", d["code"])

    def test_disa_corpus_in_the_repo(self):
        """tests/stig/disa_texts.py (1.73.0): the RHEL texts there - every pattern code a fixed shape."""
        import importlib
        sys.path.insert(0, os.path.join(HERE, "stig"))
        dt = importlib.import_module("disa_texts")
        for stig, rule, which, text, cmd in dt.TEXTS:
            d = a.stig_answer_draft(text, {"min_release": "9.4"}) if stig.startswith("RHEL") and which == "check" else None
            if d and d["kind"] == "pattern":
                self.assertRegex(d["code"].split("\n")[-1], r"^return \(")


class Values(unittest.TestCase):
    """answer_keys_values from the template's Variables (a mapping) or a survey (a text)."""
    def test_shapes(self):
        want = {"min_release": "9.4", "world_writable_owners": ["root", "sys", "bin", "appuser"]}
        self.assertEqual(a.stig_answer_values({"min_release": "9.4", "world_writable_owners": ["root", "sys", "bin", "appuser"]}), want)
        self.assertEqual(a.stig_answer_values({"min_release": 9.4, "world_writable_owners": "root, sys bin,appuser"}),
                         {"min_release": "9.4", "world_writable_owners": ["root", "sys", "bin", "appuser"]})
        self.assertEqual(a.stig_answer_values("min_release: '9.4'\nworld_writable_owners: root, sys, bin, appuser"), want)
        self.assertEqual(a.stig_answer_values('{"min_release": "9.4", "world_writable_owners": ["root", "sys", "bin", "appuser"]}'), want)
        self.assertEqual(a.stig_answer_values(""), {})
        self.assertEqual(sorted(a.VALUES), ["min_release", "world_writable_owners"])

    def test_the_line_to_add(self):
        d = a.stig_answer_drafts([dict(v="V-1", sv="SV-1", ok=True, expected="Not_Reviewed", check_text=T_WW)], {})
        self.assertEqual(d["V-1"]["why"], "Not drafted - needs answer_keys_values.world_writable_owners (the accounts the site approved "
                                          "as owners of world-writable directories). Add to the template's Variables: "
                                          "answer_keys_values: {world_writable_owners: [root, sys, bin, appuser]}")

    def test_act_never_makes_one_up(self):
        self.assertEqual(a.stig_answer_act_needs("NEEDS-VALUE ntp_servers - the site's time servers"), ["ntp_servers", "the site's time servers"])
        self.assertEqual(a.stig_answer_act_needs("```powershell\nreturn $true\n```"), [])
        self.assertEqual(a.stig_answer_act_uses("x\nVALUES USED: min_release, made_up", {"min_release": "9.4"}), {"min_release": "9.4"})
        p = [dict(v="V-2", sv="SV-2", title="T", ok=True, expected="Open", check_text=T_ASK)]
        d = a.stig_answer_drafts(p, {}, True, {"V-2": {"code": "", "needs": ["ntp_servers", "the site's time servers"]}})
        self.assertIn("needs answer_keys_values.ntp_servers (the site's time servers): ACT never makes one up", d["V-2"]["why"])
        self.assertIn("use ONLY a value given above", a.stig_answer_act_task(p[0]))


class Rules(unittest.TestCase):
    def test_asked(self):
        self.assertEqual(a.stig_answer_rules("V-257777, SV-257777r1155676_rule SV-257900")["rules"],
                         [{"v": "V-257777", "sv": "SV-257777"}, {"v": "V-257900", "sv": "SV-257900"}])
        self.assertIn("not a rule id", a.stig_answer_rules(["RHEL-09-211010"])["error"])
        self.assertIn("no rule asked", a.stig_answer_rules([])["error"])
        self.assertIn("at most 25", a.stig_answer_rules(["V-%d" % i for i in range(1, 27)])["error"])

    def test_pick(self):
        rules = a.stig_answer_rules(["V-1", "V-2", "V-3", "V-4", "V-5"])["rules"]
        items = [{"key": "SV-1", "result": "fail", "title": "One", "severity": "high"}, {"key": "SV-2", "result": "notchecked"},
                 {"key": "SV-3", "result": "pass"}, {"key": "SV-4", "result": "fail"}]
        sm = {"status": "found", "reviews": {"SV-4": {"result": "pass", "status": "accepted"}}}
        got = dict((p["v"], p) for p in a.stig_answer_pick(rules, items, sm))
        self.assertEqual((got["V-1"]["ok"], got["V-1"]["expected"], got["V-1"]["cat"]), (True, "Open", "CAT I"))
        self.assertEqual((got["V-2"]["ok"], got["V-2"]["expected"]), (True, "Not_Reviewed"))
        self.assertIn("Evaluate-STIG's own check says Not a Finding", got["V-3"]["why"])
        self.assertIn("Not a Finding in STIG Manager (accepted)", got["V-4"]["why"])
        self.assertIn("gave no result for it", got["V-5"]["why"])


class Blocks(unittest.TestCase):
    def test_the_block(self):
        b = a.stig_answer_block("SITEA", "Open", "$o = 1 -lt 2\nreturn $o", ("NotAFinding", "ok <yes>"), ("Open", "no & no"))
        self.assertEqual(b.split("\n"), ['<AnswerKey Name="SITEA">', "  <ExpectedStatus>Open</ExpectedStatus>", "  <ValidationCode>",
                                         "$o = 1 -lt 2", "return $o", "  </ValidationCode>", "  <ValidTrueStatus>NotAFinding</ValidTrueStatus>",
                                         "  <ValidTrueComment>ok &lt;yes&gt;</ValidTrueComment>", "  <ValidFalseStatus>Open</ValidFalseStatus>",
                                         "  <ValidFalseComment>no &amp; no</ValidFalseComment>", "</AnswerKey>"])
        import xml.etree.ElementTree as ET
        self.assertEqual(ET.fromstring(b).find("ValidationCode").text.strip(), "$o = 1 -lt 2\nreturn $o")
        act = a.stig_answer_block("K", "Open", "return $true", ("NotAFinding", "x"), ("Open", "y"), act=True)
        self.assertIn("<!-- drafted by ACT - read it before you use it -->", act)

    def test_the_template_names(self):
        n = a.stig_answer_names(["validationcode", "ExpectedStatus", "ValidTrueStatus", "ValidTrueComment", "ValidFalseStatus", "ValidFalseComment"])
        self.assertEqual((n["from"], n["names"][0]), ("template", "validationcode"))
        self.assertIn("<validationcode>", a.stig_answer_block("K", "Open", "return $true", ("NotAFinding", "x"), ("Open", "y"), n["names"]))
        d = a.stig_answer_names(["ExpectedStatus", "ValidationCode"])
        self.assertEqual((d["from"], d["names"]), ("documented", a.DOCUMENTED))
        self.assertIn("the template lacks ValidTrueStatus", d["why"])

    def test_statuses_and_words(self):
        self.assertEqual(a.stig_answer_check_words(("NotAFinding", "fine"), ("Open", "fix it")), "")
        self.assertIn("is not NotAFinding", a.stig_answer_check_words(("Not_Reviewed", "x"), ("Open", "y")))
        self.assertIn("says POA&M", a.stig_answer_check_words(("NotAFinding", "see the POA&M"), ("Open", "y")))


class Act(unittest.TestCase):
    def test_the_task_and_the_code(self):
        task = a.stig_answer_act_task({"v": "V-1", "sv": "SV-1", "title": "T", "check_text": T_ASK}, {"min_release": "9.4"})
        self.assertIn("DISA's check text:", task)
        self.assertIn("min_release = 9.4", task)
        self.assertIn("```powershell", task)
        self.assertEqual(a.stig_answer_act_code("Text\n```powershell\nreturn $true\n```\nWhat it checks."), "return $true")
        self.assertEqual(a.stig_answer_act_code("no code here"), "")

    def test_drafts_and_rows(self):
        picks = [dict(v="V-1", sv="SV-1", title="Owner", ok=True, expected="Open", check_text=T_OWNER, es="Open"),
                 dict(v="V-2", sv="SV-2", title="Asked", ok=True, expected="Not_Reviewed", check_text=T_ASK, es="Not Reviewed"),
                 dict(v="V-3", sv="SV-3", title="Release", ok=True, expected="Not_Reviewed", check_text=T_RELEASE, es="Not Reviewed")]
        self.assertEqual([p["v"] for p in a.stig_answer_for_act(picks)], ["V-2"])
        d = a.stig_answer_drafts(picks, {}, True, {"V-2": {"code": "return $true"}})
        self.assertEqual((d["V-1"]["by"], d["V-2"]["by"]), ("pattern", "ACT"))
        self.assertIn("needs answer_keys_values.min_release", d["V-3"]["why"])
        self.assertTrue(d["V-2"]["true"][1].startswith(a.PREFIX_ACT))
        off = a.stig_answer_drafts(picks, {}, False)
        self.assertIn("ACT is off (answer_keys_act false)", off["V-2"]["why"])
        hr = {"gate": {"V-1": {"ok": True, "used": ["/usr/bin/stat"]}, "V-2": {"ok": False, "why": ["it runs iex (not one of the allowed commands)"]}}}
        rows = dict((r["v"], r) for r in a.stig_answer_rows(picks, d, hr, "DEFAULT"))
        self.assertIn('<AnswerKey Name="DEFAULT">', rows["V-1"]["block"])
        self.assertIn("refused the draft ACT wrote: it runs iex", rows["V-2"]["why"])
        doc = a.stig_answer_report(list(rows.values()), "rhel01.example.mil", "DEFAULT", {"found": True, "version": "1.2607.0"})
        self.assertEqual(doc["web"]["title"], "STIG answer keys: 1 ready to copy, 2 not drafted")
        web = doc["web"]["sections"][0]
        self.assertEqual(web["details"][0][0]["texts"][0]["button"], "Copy block")
        # 1.75.0: no trial - every block "not tried", what it would set for true and for false, try it on a test host first
        self.assertIn(["Trial", a.NOT_TRIED], web["details"][0][1]["rows"])
        self.assertIn(["When the code returns true", "NotAFinding (ValidTrueStatus)"], web["details"][0][1]["rows"])
        self.assertIn(["When it returns false", "Open (ValidFalseStatus) - the rule stays Open"], web["details"][0][1]["rows"])
        self.assertEqual(web["rows"][0][-2:], [a.NOT_TRIED_SHORT, a.WOULD_SET])
        self.assertTrue(web["text"].startswith("Try each block on a test host first"))
        self.assertTrue(doc["report"]["sections"][0]["text"].startswith("Try each block on a test host first"))
        self.assertTrue(doc["web"]["sections"][2]["lines"][0].startswith("Try each block on a test host first."))
        self.assertEqual([t["value"] for t in doc["web"]["summary"] if t["label"] == "Trial"], ["not tried"])
        self.assertIn("Not tried: try each block on a test host first (the trial comes in 1.75.1).", doc["lines"])
        self.assertEqual(doc["xlsx"][0]["rows"][0][2:4], [a.NOT_TRIED_SHORT, a.WOULD_SET])
        self.assertIn(["Its ExpectedStatus is Open", a.OPEN_NOTE], web["details"][0][1]["rows"])
        self.assertNotIn("details", doc["report"]["sections"][0])
        self.assertIn("Nothing was written to STIG Manager, the share or the host.", doc["web"]["sections"][2]["lines"][-1])
        self.assertEqual(doc["xlsx"][0]["rows"][0][-1], rows["V-1"]["block"])


@unittest.skipUnless(PWSH, "pwsh is not here")
class ReadOnlyCheck(unittest.TestCase):
    """The fixed read-only check, as on the host: PowerShell's parser on the code's syntax tree."""
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        cls.gate = os.path.join(cls.d, "gate.ps1")
        with open(cls.gate, "w") as f:
            f.write(host.GATE_PS1)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def run_ps(self, script, code):
        f = os.path.join(self.d, "code.ps1")
        with open(f, "w") as fh:
            fh.write(code)
        r, err, s = host.run_ps(PWSH, script, f, 60)
        self.assertIsNotNone(r, err)
        return r

    def test_the_patterns_codes_pass(self):
        for text, values in ((T_OWNER, {}), (T_MODE, {}), (T_SYSCTL, {}), (T_PKG_GONE, {}), (T_PKG, {}), (T_MIN, {}),
                             (T_RELEASE, {"min_release": "9.4"}), (T_WW, {"world_writable_owners": ["svc_app1"]}), (T_OPENSSL, {}),
                             (T_AUDIT, {}), (T_RELEASE10, {"min_release": ["9.4", "10.0"]})):
            code = a.stig_answer_draft(text, values)["code"]
            g = self.run_ps(self.gate, code)
            self.assertTrue(g["ok"], (code, g))

    def test_refused(self):
        """Each of these is refused - the tricks a draft (ACT's) could try."""
        bad = {"iex 'Remove-Item /tmp/x'": "it runs iex", "sc /tmp/x 'a'": "it runs sc",
               "$c = 'Remove-Item'; & $c /tmp/x": "from an expression or a variable", "Inv`oke-Expression 'x'": "backtick",
               "& /usr/bin/pwsh -EncodedCommand ZQBjAGgAbwA=": "it runs /usr/bin/pwsh (not one of the allowed",
               "[System.IO.File]::WriteAllText('/tmp/x', 'a')": "the type [System.IO.File]",
               "Invoke-WebRequest http://example.mil/x": "it runs Invoke-WebRequest", "Get-Item /tmp/x | ForEach-Object Delete": "a literal script block only",
               "Get-Item /tmp/x | ForEach-Object -MemberName Delete": "a literal script block only", "(Get-Item /tmp/x).Delete()": "the method Delete()",
               "Set-Content /tmp/x 'a'": "it runs Set-Content", "Get-Content /etc/hosts > /tmp/x": "it writes to /tmp/x",
               "$env:PATH = '/tmp'": "it assigns to $env:PATH", "& /usr/bin/find /tmp -delete": "find: -delete is not allowed",
               "& /usr/sbin/sysctl -w kernel.x=1": "sysctl", "& /usr/bin/rpm -e telnet": "rpm: only -q PACKAGE",
               "& /usr/bin/rpm -q --eval '%{lua: os.execute(\"x\")}'": "rpm: only -q PACKAGE", "& /usr/bin/systemctl restart sshd": "systemctl restart",
               "function f { 1 }": "it uses FunctionDefinition", "[scriptblock]::Create('x').Invoke()": "[scriptblock]",
               "& { Remove-Item /tmp/x }": "from an expression", ". /tmp/x.ps1": "a command with Dot", "Start-Process /bin/sh": "Start-Process",
               "$a = @('-delete'); & /usr/bin/find / @a": "not a fixed word", "& stat /etc": "it runs stat",
               "New-Object Net.WebClient": "New-Object", "$ExecutionContext.InvokeCommand.InvokeScript('x')": "InvokeScript",
               "& /usr/sbin/auditctl -D": "auditctl: only -l", "& /usr/sbin/auditctl -e 0": "auditctl: only -l",
               # 1.75.0b (Codex P1, both verified to write as root before): a member name in a variable, a file's own method
               "$m = 'Delete'; Get-Item '/tmp/victim' | ForEach-Object $m; return $true": "a literal script block only",
               "$null = (Get-Item '/tmp/source').Replace('/tmp/target', '/tmp/backup'); return $true": "not a string for certain",
               "Get-Item /tmp/x | ForEach-Object -Process $sb": "a literal script block only",
               "Get-Item /tmp/x | Where-Object Delete": "a literal script block only",
               "$f = Get-Item /tmp/x; $f.Delete()": "the method Delete()", "$f = Get-Item /tmp/x; $f.MoveTo('/tmp/y')": "the method MoveTo()",
               "$f = Get-Item /tmp/x; $f.CopyTo('/tmp/y', $true)": "the method CopyTo()", "$d = Get-Item /tmp; $d.CreateSubdirectory('z')": "the method CreateSubdirectory()",
               "$f = Get-Item /tmp/x; $f.Create()": "the method Create()", "$f = Get-Item /tmp/x; $f.OpenWrite()": "the method OpenWrite()",
               "$f = Get-Item /tmp/x; $f.Encrypt()": "the method Encrypt()", "$f = Get-Item /tmp/x; $f.Refresh()": "the method Refresh()",
               "$f = Get-Item /tmp/x; $f.Replace('/tmp/a', '/tmp/b')": "not a string for certain",
               "$f = Get-Item /tmp/x; $f.Trim()": "not a string for certain",
               "$f = Get-Item /tmp/x; $f.PSObject.Methods": "it uses $f.PSObject",
               "$t = [type]'System.IO.File'; $t.InvokeMember('Delete', 'InvokeMethod', $null, $null, @('/tmp/x'))": "the type [type]",
               "Get-Item /tmp/x | Get-Member": "it runs Get-Member", "[System.IO.FileInfo]::new('/tmp/x').Delete()": "[System.IO.FileInfo]",
               "[IO.File]::Delete('/tmp/x')": "[IO.File]", "Add-Type -TypeDefinition 'public class X {}'": "it runs Add-Type",
               "[math]::Max(1, 2)": "[math]", "[convert]::ToInt32('7', 8)": "[convert]", "$s = [scriptblock]::Create('1')": "[scriptblock]",
               "$e = $ExecutionContext.SessionState": "$ExecutionContext", "$x = [System.Environment]::MachineName": "[System.Environment]",
               # 1.75.0c (Codex P1 x3, verified: they changed a file's mode, ran a shell as root, loaded sysctl settings)
               "(Get-Item /tmp/x).Attributes++": "++ and -- on a plain local variable only",
               "$f = Get-Item /tmp/x; $f.Attributes++": "++ and -- on a plain local variable only",
               "$f = Get-Item /tmp/x; --$f.Attributes": "++ and -- on a plain local variable only",
               "$f = Get-Item /tmp/x; $f.Attributes += 1": "it assigns to $f.Attributes (a plain local variable only)",
               "$f = Get-Item /tmp/x; $f.Attributes = 'ReadOnly'": "it assigns to $f.Attributes (a plain local variable only)",
               "$a = @(1); $a[0]++": "++ and -- on a plain local variable only", "[ref]$r = 0": "the type [ref]",
               "$global:n++": "++ and -- on a plain local variable only", "$script:n = 1": "it assigns to $script:n",
               "& /usr/bin/rpm -q -D 'p %(touch /tmp/x)' -E '%p'": "rpm: only -q PACKAGE",
               "& /usr/bin/rpm -q --define 'p %(touch /tmp/x)' bash": "rpm: only -q PACKAGE",
               "& /usr/bin/rpm -q --eval '%(touch /tmp/x)'": "rpm: only -q PACKAGE",
               "& /usr/bin/rpm -q -E '%(touch /tmp/x)'": "rpm: only -q PACKAGE",
               "& /usr/bin/rpm -q --eval-lua 'os.execute(1)' bash": "rpm: only -q PACKAGE",
               "& /usr/bin/rpm -q --macros /tmp/m bash": "rpm: only -q PACKAGE", "& /usr/bin/rpm -q --rcfile /tmp/r bash": "rpm: only -q PACKAGE",
               "& /usr/bin/rpm -qa": "rpm: only -q PACKAGE", "& /usr/bin/rpm -q '%(touch /tmp/x)'": "rpm: only -q PACKAGE",
               "& /usr/bin/rpm -q --qf '%{lua:os.execute(1)}' bash": "rpm: only -q PACKAGE",
               "& /usr/bin/rpm -q --qf '%(touch /tmp/x)' bash": "rpm: only -q PACKAGE",
               "& /usr/bin/rpm -q --% -D x": "rpm: only -q PACKAGE", "& /usr/bin/rpm -q -- -D": "rpm: only -q PACKAGE",
               "& /usr/sbin/sysctl -n -p/tmp/x.conf": "write a space between its arguments",
               "& /usr/sbin/sysctl -n -p /tmp/x.conf": "sysctl: only -n KEY", "& /usr/sbin/sysctl -n -f /tmp/x.conf": "sysctl: only -n KEY",
               "& /usr/sbin/sysctl -n --load /tmp/x.conf": "sysctl: only -n KEY", "& /usr/sbin/sysctl -n --system": "sysctl: only -n KEY",
               "& /usr/sbin/sysctl -n --load=/tmp/x.conf": "sysctl: only -n KEY", "& /usr/sbin/sysctl -np /tmp/x.conf": "sysctl: only -n KEY",
               "& /usr/sbin/sysctl -n kernel.x=1": "sysctl: only -n KEY", "& /usr/sbin/sysctl -n -- -p": "sysctl: only -n KEY",
               "& /usr/bin/sysctl -p": "sysctl: only -n KEY",
               "& /usr/sbin/sysctl -n'-p/tmp/x'": "sysctl: only -n KEY",
               "& /usr/bin/grep -f /tmp/p /etc/x": "grep:", "& /usr/bin/grep -iE x /etc/x": "grep:",
               "& /usr/bin/find / -name 'x' -exec /bin/rm '{}' '+'": "find: -exec is not allowed",
               "& /usr/bin/find / -name x*": "quote it", "& /usr/bin/stat --printf '%U' /etc/x": "stat: only -c FORMAT",
               "& /usr/bin/stat -c '%U' -- /etc/x": "stat: only -c FORMAT", "& /usr/bin/systemctl is-failed sshd": "systemctl: only",
               "& /usr/bin/getent hosts example.mil": "getent: only", "& /usr/bin/id -Z": "id: only",
               "$x = @{}": "Hashtable", "exit 0": "ExitStatement", "throw 'x'": "ThrowStatement", "while ($true) { }": "WhileStatement",
               "Get-Content /etc/x -OutVariable global:o": "is given -OutVariable", "Get-ChildItem / -PipelineVariable p": "is given -PipelineVariable",
               "$s = { 1 }": "a script block not given to ForEach-Object", "'a' -replace 'a', { 'b' }": "a script block not given to ForEach-Object",
               "Get-Content /etc/x | Sort-Object { $_.Length }": "a script block not given to ForEach-Object",
               '"$((Get-Item /tmp/x).Delete())"': "the method Delete()", "$p = @{Path = '/tmp/x'}; Get-Content @p": "it uses @p",
               "$y = 'a' -as 'IO.FileInfo'": "with a type that is not written as [type]", "1 && 2": "PipelineChain",
               "$x = $true ? 1 : 2": "TernaryExpression"}
        for code, why in bad.items():
            with self.subTest(code=code):
                g = self.run_ps(self.gate, code)
                self.assertFalse(g["ok"])
                self.assertIn(why, " ".join(g["why"]))

    def test_string_methods_on_strings_only(self):
        """1.75.0b: a string's reading methods pass only where the value is a string for certain."""
        for code in ("return ('a b'.Replace('a', 'c') -eq 'c b')", "$x = 3; return (([string]$x).Trim() -eq '3')",
                     "$l = @('a', 'b'); return (($l -join ',').StartsWith('a'))", "return ((('a b' -split ' ')[0]).ToUpper() -eq 'A')",
                     "$m = [regex]::Match('release 9.4', 'release (\\d+)'); return ($m.Success -and [version]'9.4' -ge [version]'9.0')"):
            with self.subTest(code=code):
                g = self.run_ps(self.gate, code)
                self.assertTrue(g["ok"], g)

    def test_allowed(self):
        """1.75.0c: what the allow-lists keep - the forms the patterns and a careful draft use."""
        for code in ("$x = 0; $x++; $x--; ++$x; $local:y = 1; return ($x -eq 1)", "$null = & /usr/bin/rpm -q bash 2>&1",
                     "$v = @(& /usr/bin/rpm -q --qf '%{VERSION}-%{RELEASE}' openssl 2>&1); return ($LASTEXITCODE -eq 0)",
                     "$o = @(& /usr/sbin/sysctl -n net.ipv4.ip_forward 2>&1); return ($o[0] -eq '0')",
                     "$o = @(& /usr/bin/grep -i -E '^\\s*PermitRootLogin' /etc/ssh/sshd_config 2>&1); return ($o.Count -eq 1)",
                     "$o = @(& /usr/bin/grep -s -e '-a always' /etc/audit/rules.d/audit.rules); return ($o.Count -gt 0)",
                     "$o = @(& /usr/bin/find / -xdev '(' -type f -o -type d ')' -perm -0002 -nouser -print 2>&1); return ($o.Count -eq 0)",
                     "$o = @(& /usr/bin/find /home -maxdepth 2 -type f -name '.[^.]*' -print 2>&1); return ($o.Count -eq 0)",
                     "$o = @(& /usr/bin/stat -c '%a %U %n' /etc/shadow /etc/ssh/*_key 2>&1); return ($o.Count -gt 0)",
                     "$s = & /usr/bin/systemctl show -p ActiveState auditd; return ($s -eq 'ActiveState=active')",
                     "$o = & /usr/bin/getent passwd root; $i = & /usr/bin/id -u -n root; return ($i -eq 'root')",
                     "try { $l = Get-Content -LiteralPath /etc/x -ErrorAction Stop } catch { return $false }; return ($l.Count -gt 0)",
                     "foreach ($l in (Get-Content /etc/x)) { if ($l -match '^x') { return $true } }; return $false"):
            with self.subTest(code=code):
                g = self.run_ps(self.gate, code)
                self.assertTrue(g["ok"], g)

    def test_no_trial(self):
        """1.75.0: no code is run on the host - no trial, no sandbox, no systemd-run (they come in 1.75.1): the module only
        parses each code with the read-only check, and has no setting that could turn a trial on."""
        src = open(host.__file__).read()
        for word in ("systemd-run", "TRIAL_PS1", "EncodedCommand\", \"", "trial=dict", "[scriptblock]::Create($code)"):
            self.assertNotIn(word, src)
        self.assertEqual(sorted(k for k in ("trial", "timeout", "sandbox") if '%s=dict(' % k in src), [])
        defaults = open(os.path.join(REPO, "roles", "check_stig", "defaults", "main.yml")).read()
        self.assertNotIn("answer_keys_trial", defaults)


class Evaluate(unittest.TestCase):
    def test_version_template_and_compare(self):
        d = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(d, "AnswerFiles"))
            os.makedirs(os.path.join(d, "Modules", "M"))
            with open(os.path.join(d, "Evaluate-STIG.ps1"), "w") as f:
                f.write('$EvaluateStigVersion = "1.2607.0"\n')
            with open(os.path.join(d, "AnswerFiles", "AnswerFile_Template.xml"), "w") as f:
                f.write('<STIGComments Name="x"><Vuln ID="V-1"><AnswerKey Name="DEFAULT"><ExpectedStatus/><ValidationCode/>'
                        '<ValidTrueStatus/><ValidTrueComment/><ValidFalseStatus/><ValidFalseComment/></AnswerKey></Vuln></STIGComments>')
            with open(os.path.join(d, "Modules", "M", "M.psm1"), "w") as f:
                f.write("# x\nif ($A.ExpectedStatus -eq $Status) { 1 }\n")
            self.assertEqual(host.es_version(d), "1.2607.0")
            self.assertEqual(host.template_names(d)[1], a.DOCUMENTED)
            self.assertEqual(host.expected_check(d), {"found": True, "where": "Modules/M/M.psm1:2"})
            self.assertEqual(host.es_folder(os.path.join(d, "Evaluate-STIG.ps1")), d)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
