#!/usr/bin/env python3
"""1.75.0: STIG answer keys - the fixed patterns (on DISA's own RHEL 9 check texts), the <AnswerKey> block, the rules a key
may be for, ACT's draft, the report; and the fixed read-only check (PowerShell's parser, with pwsh) on the codes it must
allow and the tricks it must refuse.
  python tests/test_stig_answer_keys.py"""
from __future__ import absolute_import, division, print_function

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
sys.path.insert(0, os.path.join(REPO, "plugins", "module_utils"))
sys.path.insert(0, HERE)
import ansible.module_utils  # noqa: E402,F401
ansible.module_utils.__path__.append(os.path.join(REPO, "plugins", "module_utils"))   # as ansible.cfg gives them (site_redact)
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


# 1.79.10: the GRUB superuser rules (DISA's texts, shortened): RHEL 8 UEFI (two files, one of them DISA's /boot/boot/efi),
# RHEL 8 BIOS (no OS account either), RHEL 9 and 10 (grep -A1 in /etc/grub2.cfg); the BIOS password rule is not one of them
T_GRUB8_UEFI = ('Verify that a unique name is set as the "superusers" account.\n\nSearch the unified GRUB 2 configuration file for '
                'the superusers definition:\n# grep -iw "superusers" /boot/grub2/grub.cfg\nset superusers="[someuniquestringhere]"\n'
                'export superusers\n\nIf "superusers" is not defined, or if the defined superuser name is identical to a standard OS '
                'administrative account name (such as "root", "admin", "administrator", "superuser", or "unlock"), this is a finding.\n\n'
                'Note for legacy RHEL 8 deployments: check the legacy UEFI path:\n# grep -iw "superusers" /boot/boot/efi/EFI/redhat/grub.cfg')
T_GRUB8_BIOS = ('Verify that a unique name is set as the "superusers" account:\n\n$ sudo grep -iw "superusers" /boot/grub2/grub.cfg\n'
                'set superusers="[someuniquestringhere]"\nexport superusers\n\nIf "superusers" is identical to any OS account name or is '
                'missing a name, this is a finding.')
T_GRUB9 = ('Verify the RHEL 9 boot loader superuser account has been set with the following command:\n\n$ sudo grep -A1 "superusers" '
           '/etc/grub2.cfg \n\nset superusers="<accountname>"\nexport superusers\npassword_pbkdf2 <accountname> ${GRUB2_PASSWORD}\n \n'
           'Verify <accountname> is not a common name such as root, admin, or administrator.\n\nIf superusers contains easily '
           'guessable usernames, this is a finding.')
T_GRUB8_PW = ('Check to see if an encrypted grub superusers password is set. On systems that use a BIOS, use the following command:\n\n'
              '$ sudo grep -iw grub2_password /boot/grub2/user.cfg\n\nGRUB2_PASSWORD=grub.pbkdf2.sha512.[password_hash]\n\n'
              'If the grub superusers password does not begin with "grub.pbkdf2.sha512", this is a finding.')


# 1.79.10: sshd's settings (RHEL 9's text; RHEL 10 adds sshd -T of the same keyword) and the mount options (shortened)
SSHD_CHAIN = "sudo /usr/sbin/sshd -dd 2>&1 | awk '/filename/ {print $4}' | tr -d '\\r' | tr '\\n' ' ' | xargs sudo grep -iH '^\\s*%s'"
T_SSHD_ROOT = ('Verify RHEL 9 SSH daemon does not allow direct logons to the root account with the following command:\n\n$ '
               + SSHD_CHAIN % "permitrootlogin" + '\n\nPermitRootLogin no\n\nIf the "PermitRootLogin" keyword is set to any value '
               'other than "no", is missing, or is commented out, this is a finding.')
T_SSHD_ALIVE10 = ('Verify the SSH server is configured to time out idle sessions:\n\n$ ' + SSHD_CHAIN % "clientaliveinterval" +
                  '\n/etc/ssh/sshd_config.d/10-stig.conf::ClientAliveInterval 600\n\n$ sudo sshd -T | grep -i clientaliveinterval\n'
                  'clientaliveinterval 600\n\nIf the "ClientAliveInterval" keyword is not set to a value of "600" or less in a drop-in '
                  'that lexicographically precedes 50-redhat.conf, or if no output is returned, this is a finding.')
T_SSHD_REKEY8 = ('Verify the SSH server is configured to force frequent session key renegotiation with the following command:\n\n$ '
                 + SSHD_CHAIN % "rekeylimit" + '\n\nRekeyLimit 1G 1h\n\nIf "RekeyLimit" does not have a maximum data amount and '
                 'maximum time defined, is missing, or is commented out, this is a finding.')
T_SSHD_BANNER = ('Check for the location of the banner file being used with the following command:\n\n$ ' + SSHD_CHAIN % "banner" +
                 '\n/etc/ssh/sshd_config.d/80-bannerPointer.conf:Banner /etc/issue\n\nIf the line is commented out, this is a finding.')
T_MOUNT_OPT = ('Verify "/home" is mounted with the "nodev" option with the following command:\n\n$ mount | grep /home\n\n'
               '/dev/mapper/rhel-home on /home type xfs (rw,nodev,nosuid,noexec,seclabel)\n\nIf the /home file system is mounted '
               'without the "nodev" option, this is a finding.')
T_MOUNT_OWN = ('Verify RHEL 10 uses a separate file system/partition for "/var" with the following command:\n\n$ mount | grep /var\n'
               '/dev/mapper/luks-x on /var type xfs (rw,nodev,relatime,seclabel,attr2)\nNote: Options displayed for mount may differ.\n\n'
               'If a separate entry for "/var" is not in use, this is a finding.')
T_MOUNT_ANY = ("Verify:\n\n$ sudo mount | grep '^/dev\\S* on /\\S' | grep --invert-match 'nodev'\n\nIf any output is produced, this is a finding.")


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

    def test_grub_superusers(self):
        """1.79.10: the superuser's name in GRUB's files - DISA's files, its common names, the site's own list."""
        d = a.stig_answer_draft(T_GRUB8_UEFI)
        self.assertEqual(d["kind"], "pattern")
        for f in ("/boot/grub2/grub.cfg", "/boot/efi/EFI/redhat/grub.cfg"):
            self.assertIn("Test-Path -LiteralPath '%s' -PathType Leaf" % f, d["code"])
        self.assertNotIn("/boot/boot/", d["code"].split("\n", 1)[1])                 # DISA's typo only in the comment line
        self.assertIn("$no = @('root', 'admin', 'administrator', 'superuser', 'unlock')", d["code"])
        self.assertNotIn("/etc/passwd", d["code"])
        self.assertIn("return ($sets.Count -ge 1 -and $unread -eq 0 -and $names.Count -ge 1 -and $bad.Count -eq 0)", d["code"])
        self.assertEqual(d["uses"], {})
        d = a.stig_answer_draft(T_GRUB8_UEFI, {"grub_superusers_not_allowed": "grubadmin, ROOT bootadmin"})
        self.assertIn("'unlock', 'grubadmin', 'bootadmin')", d["code"])
        self.assertEqual(d["uses"], {"grub_superusers_not_allowed": "grubadmin, ROOT, bootadmin"})
        # Codex (1.79.10): "not identical to any OS account" needs the directory service's accounts too - no pattern
        self.assertIsNone(a.stig_answer_draft(T_GRUB8_BIOS))
        d = a.stig_answer_draft(T_GRUB9)
        self.assertIn("Test-Path -LiteralPath '/etc/grub2.cfg' -PathType Leaf", d["code"])
        self.assertIsNone(a.stig_answer_draft(T_GRUB8_PW))
        self.assertIsNone(a.stig_answer_draft(T_GRUB9.replace("/etc/grub2.cfg", "/tmp/grub.cfg")))   # not GRUB's own file

    def test_sshd_settings_and_mounts(self):
        """1.79.10: sshd's settings as sshd reads them, a mount's options - and the texts no pattern takes."""
        d = a.stig_answer_draft(T_SSHD_ROOT)
        self.assertEqual(d["kind"], "pattern")
        self.assertIn("Select-String -LiteralPath '/etc/ssh/sshd_config'", d["code"])
        self.assertIn("Select-String -Path '/etc/ssh/sshd_config.d/*.conf'", d["code"])
        self.assertIn("-eq 'no'", d["code"])
        d = a.stig_answer_draft(T_SSHD_ALIVE10)                           # RHEL 10: the sshd -T line beside it
        self.assertIn("[int]$v -ge 1 -and [int]$v -le 600", d["code"])
        self.assertIn("$v -match '^[1-9][0-9]*[KMG]?\\s+[1-9][0-9]*[smhdwSMHDW]?$'", a.stig_answer_draft(T_SSHD_REKEY8)["code"])   # RHEL 8: any values but 0
        self.assertIn("$src[0] -cnotmatch '^[0-4][0-9]-'", d["code"])     # RHEL 10: from a drop-in before 50-redhat.conf
        self.assertNotIn("$src[0]", a.stig_answer_draft(T_SSHD_ROOT)["code"])
        # Codex (1.79.10): an example value the criterion calls a finding is not taken as the wanted one
        self.assertIsNone(a.stig_answer_draft(T_SSHD_ROOT.replace("PermitRootLogin no", "PermitRootLogin yes")))
        self.assertIsNone(a.stig_answer_draft(T_SSHD_BANNER))             # a file the site chooses: no pattern
        self.assertIsNone(a.stig_answer_draft(T_SSHD_ROOT.replace("permitrootlogin'", "ciphers'")))   # a keyword not on the list
        d = a.stig_answer_draft(T_MOUNT_OPT)
        self.assertIn("-ceq '/home'", d["code"])
        self.assertIn("-cnotcontains 'nodev'", d["code"])
        self.assertIn("return ($m.Count -ge 1 -and $root.Count -ge 1 -and $same.Count -eq 0)", a.stig_answer_draft(T_MOUNT_OWN)["code"])
        # Codex (1.79.10): an option named without quotes is still an option check, not "mounted at all"
        d = a.stig_answer_draft(T_MOUNT_OPT.replace('without the "nodev" option', "without the nodev option"))
        self.assertIn("-cnotcontains 'nodev'", d["code"])
        d = a.stig_answer_draft(T_MOUNT_OWN.replace("this is a finding.", "or the nodev option is missing, this is a finding."))
        self.assertIn("-cnotcontains 'nodev'", d["code"])
        self.assertIsNone(a.stig_answer_draft(T_MOUNT_ANY))

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
        self.assertEqual(a.stig_answer_values("grub_superusers_not_allowed: grubadmin, bootadmin"),       # 1.79.10, optional
                         {"grub_superusers_not_allowed": ["grubadmin", "bootadmin"]})

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
        self.assertIn("at most 150", a.stig_answer_rules(["V-%d" % i for i in range(1, 152)])["error"])
        self.assertIn("at most 3", a.stig_answer_rules(["V-1", "V-2", "V-3", "V-4"], most=3)["error"])
        self.assertEqual(a.stig_answer_rules([], True), {"rules": [], "error": "", "auto": True})     # 1.76.0: none - the automatic pick

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
        hr = {"gate": {"V-1": {"ok": True, "used": ["/usr/bin/stat"]}, "V-2": {"ok": False, "why": ["it runs iex (not one of the allowed commands)"]}},
              "trial": {"V-1": {"ok": True, "valid": False, "read": "read: apache", "seconds": 0.1}}}
        rows = dict((r["v"], r) for r in a.stig_answer_rows(picks, d, hr, "DEFAULT"))
        self.assertIn('<AnswerKey Name="DEFAULT">', rows["V-1"]["block"])
        self.assertIn("refused the draft ACT wrote: it runs iex", rows["V-2"]["why"])
        doc = a.stig_answer_report(list(rows.values()), "rhel01.example.mil", "DEFAULT", {"found": True, "version": "1.2607.0"})
        self.assertEqual(doc["web"]["title"], "STIG answer keys: 1 ready to copy, 2 not drafted")
        web = doc["web"]["sections"][0]
        self.assertEqual(web["details"][0][0]["texts"][0]["button"], "Copy block")
        self.assertIn(["Status it would set", "Open - the host fails it now: this key keeps it Open"], web["details"][0][1]["rows"])
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
                             (T_AUDIT, {}), (T_RELEASE10, {"min_release": ["9.4", "10.0"]}), (T_GRUB8_UEFI, {}),
                             (T_GRUB8_UEFI, {"grub_superusers_not_allowed": ["grubadmin"]}), (T_GRUB9, {}), (T_SSHD_ROOT, {}),
                             (T_SSHD_ALIVE10, {}), (T_SSHD_REKEY8, {}), (T_MOUNT_OPT, {}), (T_MOUNT_OWN, {})):
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

    def test_matches_and_string_tests(self):
        """1.79.10 (a real run: V-244521's draft refused): $Matches[n] and $Matches['name'] after a -match are strings, so
        are a string method's answer and an element of a string's Split(); [string]::IsNullOrWhiteSpace / IsNullOrEmpty
        only test. A value not a string for certain is still refused."""
        good = [
            # the draft's own shapes
            "$l = 'set superusers=\"admin7\"'; if ($l -match 'superusers=\"([^\"]*)\"') { $val = $Matches[1].Trim(); "
            "if ([string]::IsNullOrWhiteSpace($val)) { return $false }; return ($Matches[1].Trim().ToLower() -ne 'root') }; return $false",
            "if ('a=B c' -match '^a=(?<v>\\S+)') { return ($Matches['v'].ToLower() -eq 'b') }; return $false",
            "if ('x: a,b' -notmatch 'q') { }; if ('x: a,b' -match ': (.*)$') { return ($Matches[1].Split(',')[0].Trim() -eq 'a') }; return $false",
            "Get-Content /etc/x | Where-Object { $_ -match '^u=(\\w+)' } | ForEach-Object { $Matches[1].ToUpper() }",
            "$x = 5; return ([string]::IsNullOrEmpty(\"$x\".Trim()) -or ([string]$x).Split('.')[0].PadLeft(2, '0') -eq '05')",
            "if ('a=B' -match '=(.*)$') { return (($Matches[1] -replace 'B', 'c').ToUpper() -eq 'C') }; return $false"]
        for code in good:
            with self.subTest(code=code):
                g = self.run_ps(self.gate, code)
                self.assertTrue(g["ok"], g)
        bad = {"$Matches = @(Get-Item /tmp/x); 'a' -match 'a'; $Matches[0].Replace('/tmp/a', '/tmp/b')": "not a string for certain",
               "foreach ($Matches in @(Get-Item /tmp/x)) { 'a' -match 'a'; $Matches[0].Replace('/tmp/a', '/tmp/b') }": "not a string for certain",
               "$null = $Matches[1].Replace('/tmp/a', '/tmp/b'); return ('a' -match 'a')": "not a string for certain",
               "$x = @(Get-Item /tmp/x); 'a' -match 'a'; $x[0].Replace('/tmp/a', '/tmp/b')": "not a string for certain",
               "'a' -match 'a'; $i = 1; $Matches[$i].Trim()": "not a string for certain",
               "(Get-Item /tmp/x).Replace('/tmp/a', '/tmp/b').Trim()": "not a string for certain",
               "$f = Get-Item /tmp/x; $f.Name.Trim()": "not a string for certain",
               "'a' -match 'a'; $Matches.Remove(0)": "the method Remove()",
               "[string]::Format('{0}', 1)": "it calls [string]::Format", "[string]::Concat('a', 'b')": "it calls [string]::Concat",
               "$f = @(Get-Item /tmp/x); ($f -replace 'a', 'b').Replace('/tmp/a', '/tmp/b')": "not a string for certain",
               # Codex (1.79.10): another scope's $Matches, an index picking several, a list of strings
               "$null = 'a' -match 'a'; $local:Matches = @(42); return $Matches[0].Equals(42)": "not a string for certain",
               "$null = 'a' -match 'a'; $Matches[0] = 42; return $Matches[0].Equals(42)": "it assigns to $Matches[0]",
               "$null = 'a' -match 'a'; $variable:Matches[0].Trim()": "not a string for certain",
               "return ('a,b'.Split(',')[0,1].Contains('a'))": "not a string for certain",
               "return (('a,b' -split ',')[0..1].Contains('a'))": "not a string for certain",
               "$null = 'a' -match 'a'; return ($Matches[0,1].Contains('a'))": "not a string for certain",
               "return (([string[]]('a','b') -replace 'a','x').Contains('x'))": "not a string for certain",
               "return (([string[]]('a','b')).Contains('x'))": "not a string for certain"}
        for code, why in bad.items():
            with self.subTest(code=code):
                g = self.run_ps(self.gate, code)
                self.assertFalse(g["ok"], g)
                self.assertIn(why, " ".join(g["why"]))

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

    def test_the_trial(self):
        """The trial's wrapper (in the module: always inside the sandbox; here only safe codes, without it)."""
        def trial(code):
            p = subprocess.run(host.trial_command(PWSH, code), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
            return json.loads(p.stdout.decode().strip().splitlines()[-1])
        r = trial("Write-Verbose 'read: x'\nreturn (1 -lt 2)")
        self.assertEqual((r["ok"], r["values"], r["kinds"], r["read"]), (True, [True], ["Boolean"], "read: x"))
        r = trial("'a'\nreturn $true")
        self.assertEqual(r["values"], ["a", True])                  # two things on its output: not offered (the module)

    def test_grub_superusers_answers(self):
        """1.79.10: the GRUB superuser codes on files of each shape (here: GRUB's files and /etc/passwd moved to a folder)."""
        def run(text, files, passwd="root:x:0:0::/root:/bin/bash\nbob:x:1000:1000::/home/bob:/bin/bash\n", values=None):
            d = tempfile.mkdtemp(dir=self.d)
            code = a.stig_answer_draft(text, values or {})["code"]
            for name, body in list(files.items()) + [("/etc/passwd", passwd)]:
                code = code.replace(a._q(name), a._q(os.path.join(d, name.strip("/").replace("/", "_"))))
                if body is not None:
                    with open(os.path.join(d, name.strip("/").replace("/", "_")), "w") as fh:
                        fh.write(body)
            p = subprocess.run(host.trial_command(PWSH, code), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
            return json.loads(p.stdout.decode().strip().splitlines()[-1])
        cfg = '### BEGIN /etc/grub.d/01_users ###\nif [ -f ${prefix}/user.cfg ]; then\n  source ${prefix}/user.cfg\n' \
              '  if [ -n "${GRUB2_PASSWORD}" ]; then\n    set superusers="%s"\n    export superusers\n  fi\nfi\n'
        g, e = "/boot/grub2/grub.cfg", "/boot/efi/EFI/redhat/grub.cfg"
        cases = [("a unique name", T_GRUB8_UEFI, {g: cfg % "bootkeeper7", e: None}, None, True),
                 ("root (the default)", T_GRUB8_UEFI, {g: cfg % "root", e: None}, None, False),
                 ("Admin, case ignored", T_GRUB8_UEFI, {g: cfg % "Admin", e: None}, None, False),
                 ("one of a list is root", T_GRUB8_UEFI, {g: cfg % "bootkeeper7 root", e: None}, None, False),
                 ("the legacy UEFI file has root", T_GRUB8_UEFI, {g: cfg % "bootkeeper7", e: cfg % "root"}, None, False),
                 ("only the legacy UEFI file, unique", T_GRUB8_UEFI, {g: None, e: cfg % "bootkeeper7"}, None, True),
                 ("no file", T_GRUB8_UEFI, {g: None, e: None}, None, False),
                 ("not set", T_GRUB8_UEFI, {g: "set default=0\n#set superusers=\"x7\"\n", e: None}, None, False),
                 ("set empty", T_GRUB8_UEFI, {g: cfg % "", e: None}, None, False),
                 ("the site's own list", T_GRUB8_UEFI, {g: cfg % "grubadmin", e: None}, {"grub_superusers_not_allowed": "grubadmin"}, False),
                 # Codex (1.79.10): a name from a variable, an unset after the set, a set without "set"
                 ("a name from a variable", T_GRUB8_UEFI, {g: "set bootadmin=root\nset superusers=\"$bootadmin\"\n", e: None}, None, False),
                 ("unset after the set", T_GRUB8_UEFI, {g: cfg % "bootkeeper7" + "unset superusers\n", e: None}, None, False),
                 ("superusers= without set", T_GRUB8_UEFI, {g: cfg % "bootkeeper7" + "superusers=root\n", e: None}, None, False),
                 ("RHEL 9: unique, unquoted", T_GRUB9, {"/etc/grub2.cfg": "set superusers=bootkeeper7\n"}, None, True),
                 ("RHEL 9: administrator", T_GRUB9, {"/etc/grub2.cfg": cfg % "administrator"}, None, False)]
        for name, text, files, values, want in cases:
            with self.subTest(case=name):
                r = run(text, files, values=values)
                self.assertEqual((r["ok"], r["values"]), (True, [want]), r)
                self.assertTrue(r["read"].startswith("read: "), r)

    def run_on(self, code, files):
        """A pattern's code with its files moved to a folder here (files: {its path: text, or None for not there})."""
        d = tempfile.mkdtemp(dir=self.d)
        for name, body in files.items():
            local = os.path.join(d, name.strip("/").replace("/", "_").replace("*", "x"))
            if name.endswith("/*.conf"):                    # a drop-in folder: {name: text}
                os.mkdir(local)
                for fn, txt in (body or {}).items():
                    with open(os.path.join(local, fn), "w") as fh:
                        fh.write(txt)
                code = code.replace(a._q(name), a._q(local + "/*.conf"))
                continue
            code = code.replace(a._q(name), a._q(local))
            if body is not None:
                with open(local, "w") as fh:
                    fh.write(body)
        p = subprocess.run(host.trial_command(PWSH, code), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        return json.loads(p.stdout.decode().strip().splitlines()[-1])

    def test_sshd_setting_answers(self):
        """1.79.10: sshd's first value - sshd_config, its drop-ins in name order where it includes them, up to Match."""
        m, dd = "/etc/ssh/sshd_config", "/etc/ssh/sshd_config.d/*.conf"
        inc = "Include /etc/ssh/sshd_config.d/*.conf\n"
        root = a.stig_answer_draft(T_SSHD_ROOT)["code"]
        cases = [("a drop-in says no", root, inc, {"10-stig.conf": "PermitRootLogin no\n", "50-redhat.conf": "UsePAM yes\n"}, True),
                 ("10-stig no before 50-redhat yes", root, inc, {"10-stig.conf": "PermitRootLogin no\n", "50-redhat.conf": "PermitRootLogin yes\n"}, True),
                 ("10 yes before 50 no", root, inc, {"10-a.conf": "PermitRootLogin yes\n", "50-b.conf": "PermitRootLogin no\n"}, False),
                 ("main before its Include", root, "PermitRootLogin no\n" + inc, {"10-a.conf": "PermitRootLogin yes\n"}, True),
                 ("drop-ins not included", root, "UsePAM yes\n", {"10-a.conf": "PermitRootLogin no\n"}, False),
                 ("only inside a Match block", root, inc + "Match User bob\n  PermitRootLogin no\n", {}, False),
                 # Codex (1.79.10): a Match block's value, a quoted keyword, an Include in other letters, the crypto
                 # policy file setting it, two names one in capitals
                 ("no, then yes in a Match block", root, inc + "PermitRootLogin no\nMatch User root\n  PermitRootLogin yes\n", {}, False),
                 ("no, and no in a Match block", root, inc + "PermitRootLogin no\nMatch User root\n  PermitRootLogin no\n", {}, True),
                 ("a quoted keyword first", root, inc + "\"PermitRootLogin\" yes\nPermitRootLogin no\n", {}, False),
                 ("a quoted value", root, inc + "PermitRootLogin \"no\"\n", {}, True),
                 ("an Include in capitals", root, "Include /etc/SSH/sshd_config.d/*.conf\nPermitRootLogin yes\n", {"10-stig.conf": "PermitRootLogin no\n"}, False),
                 ("10-A.conf yes, 10-a.conf no", root, inc, {"10-A.conf": "UsePAM yes\nPermitRootLogin yes\n", "10-a.conf": "PermitRootLogin no\n"}, False),
                 ("not set", root, inc, {"50-redhat.conf": "UsePAM yes\n"}, False),
                 ("commented out", root, inc + "#PermitRootLogin no\n", {}, False),
                 ("written with = and in capitals", root, inc + "permitrootlogin=NO\n", {}, True),
                 ("names not in order for certain, values differ", root, inc, {"stig.conf": "PermitRootLogin no\n", "redhat.conf": "PermitRootLogin yes\n"}, False),
                 ("names not in order for certain, values agree", root, inc, {"stig.conf": "PermitRootLogin no\n", "zz.conf": "PermitRootLogin no\n"}, True),
                 ("another Include", root, inc + "Include /etc/other/*.conf\nPermitRootLogin no\n", {}, False),
                 ("an Include in a drop-in", root, inc, {"10-a.conf": "Include /etc/x.conf\nPermitRootLogin no\n"}, False),
                 ("the crypto policy Include", root, inc, {"50-redhat.conf": "Include /etc/crypto-policies/back-ends/opensshserver.config\nPermitRootLogin no\n"}, True)]
        alive = a.stig_answer_draft(T_SSHD_ALIVE10)["code"]
        rekey = a.stig_answer_draft(T_SSHD_REKEY8)["code"]
        cases += [("600 in 10-stig.conf", alive, inc, {"10-stig.conf": "ClientAliveInterval 600\n"}, True),
                  ("900", alive, inc, {"10-stig.conf": "ClientAliveInterval 900\n"}, False),
                  ("0 turns it off", alive, inc, {"10-stig.conf": "ClientAliveInterval 0\n"}, False),
                  # Codex (1.79.10): RHEL 10 wants it in a drop-in before 50-redhat.conf
                  ("600 only in sshd_config", alive, inc + "ClientAliveInterval 600\n", {}, False),
                  ("600 in 60-stig.conf", alive, inc, {"60-stig.conf": "ClientAliveInterval 600\n"}, False),
                  ("600 in stig.conf (no order)", alive, inc, {"stig.conf": "ClientAliveInterval 600\n"}, False),
                  ("RekeyLimit 512M 30m", rekey, "RekeyLimit 512M 30m\n", {}, True), ("RekeyLimit default", rekey, "RekeyLimit default none\n", {}, False),
                  ("RekeyLimit 1G 0 (no time)", rekey, "RekeyLimit 1G 0\n", {}, False)]
        cpf = "/etc/crypto-policies/back-ends/opensshserver.config"
        for name, code, main, drop, want in cases:
            with self.subTest(case=name):
                r = self.run_on(code, {m: main, dd: drop, cpf: "Ciphers aes256-ctr\n"})
                self.assertEqual((r["ok"], r["values"]), (True, [want]), r)
                self.assertTrue(r["read"].startswith("read: "), r)
        with self.subTest(case="the crypto policy file sets it"):
            r = self.run_on(root, {m: inc, dd: {"50-redhat.conf": "Include /etc/crypto-policies/back-ends/opensshserver.config\nPermitRootLogin no\n"},
                                   cpf: "PermitRootLogin yes\n"})
            self.assertEqual((r["ok"], r["values"]), (True, [False]), r)

    def test_mount_answers(self):
        """1.79.10: the mount table's own entry for the mount point, exactly."""
        opt, own = a.stig_answer_draft(T_MOUNT_OPT)["code"], a.stig_answer_draft(T_MOUNT_OWN)["code"]
        home = "/dev/mapper/rhel-home /home xfs rw,%s,relatime 0 0\n"
        cases = [("nodev", opt, home % "nosuid,nodev", True), ("without nodev", opt, home % "nosuid", False),
                 ("not mounted", opt, "/dev/sda1 / xfs rw 0 0\n", False),
                 ("nodevx is not nodev", opt, home % "nodevx", False),
                 ("/var its own", own, "/dev/sda1 / xfs rw 0 0\n/dev/sda2 /var xfs rw 0 0\n", True),
                 ("only /var/lib/nfs (DISA's grep /var finds it)", own, "/dev/sda1 / xfs rw 0 0\nsunrpc /var/lib/nfs/rpc_pipefs rpc_pipefs rw 0 0\n", False),
                 # Codex (1.79.10): a bind mount of a directory of / is not a file system of its own
                 ("/var a bind mount from /", own, "/dev/vda1 / xfs rw 0 0\n/dev/vda1 /var xfs rw 0 0\n", False)]
        for name, code, table, want in cases:
            with self.subTest(case=name):
                r = self.run_on(code, {"/proc/self/mounts": table})
                self.assertEqual((r["ok"], r["values"]), (True, [want]), r)

    def test_the_sandbox(self):
        """1.76.0: the sandbox's command line - the whole file system read-only, its own /tmp, /var/tmp, /dev/shm, no
        socket of any family, no IP, only the right to read; as root a dynamic user (CAP_DAC_READ_SEARCH ambient), never
        CAP_AUDIT_CONTROL; the host's network namespace (net.* read is the host's). No systemd-run: no sandbox, said why."""
        c = host.sandbox_cmd("/usr/bin/systemd-run", 60, {"/usr/bin/systemctl": "/w/replay-systemctl"}, 252)
        props = [c[i + 1] for i, x in enumerate(c) if x == "-p"]
        for want in ("ProtectSystem=strict", "ReadOnlyPaths=/", "ProtectHome=read-only", "PrivateTmp=yes", "PrivateDevices=yes",
                     "TemporaryFileSystem=/dev/shm", "ProtectProc=invisible", "SystemCallFilter=~@privileged @debug", "NoNewPrivileges=yes", "ProtectKernelTunables=yes", "ProtectControlGroups=yes",
                     "RestrictAddressFamilies=AF_UNIX", "RestrictAddressFamilies=~AF_UNIX", "IPAddressDeny=any",
                     "CapabilityBoundingSet=CAP_DAC_READ_SEARCH", "RuntimeMaxSec=60",
                     "BindReadOnlyPaths=/w/replay-systemctl:/usr/bin/systemctl"):
            self.assertIn(want, props)
        # 1.76.1c: no IPC namespace of its own (systemd may skip it; as root ProtectProc's /proc showed the files hidden)
        self.assertNotIn("PrivateIPC=yes", props)
        self.assertFalse([x for x in props if x.startswith("InaccessiblePaths=-/proc/sys")])
        self.assertEqual(props.index("RestrictAddressFamilies=~AF_UNIX"), props.index("RestrictAddressFamilies=AF_UNIX") + 1)
        self.assertNotIn("PrivateNetwork=yes", props)
        # 1.76.0 (Codex, round 3): /tmp, /var/tmp and /dev/shm are the sandbox's own, never the host's; not
        # ProcSubset=pid (it hides /proc/sys and /proc/cmdline); a PID namespace of its own from systemd 257
        self.assertNotIn("ReadOnlyPaths=/dev/shm", props)
        self.assertNotIn("ProcSubset=pid", props)
        self.assertNotIn("PrivatePIDs=yes", props)
        v = host.sandbox_cmd("/usr/bin/systemd-run", 60, version=257)
        # 1.76.1: systemd 239 (RHEL 8) - the six settings it lacks left out, the same properties another way; as root a
        # dynamic user (239 refuses a numeric User= with no passwd entry - tried on the real 239)
        p239 = [x for i, x in enumerate(host.sandbox_cmd("/usr/bin/systemd-run", 60, version=239)) if i and host.sandbox_cmd("/usr/bin/systemd-run", 60, version=239)[i - 1] == "-p"]
        for gone in ("PrivateIPC=yes", "ProtectProc=invisible", "ProtectClock=yes", "ProtectKernelLogs=yes", "RestrictSUIDSGID=yes",
                     "ProtectHostname=yes", "LockPersonality=yes", "PrivatePIDs=yes"):
            self.assertNotIn(gone, p239)
        for want in ("SystemCallFilter=~" + " ".join(host.IPC_CALLS), "SystemCallFilter=~@clock sethostname setdomainname",
                     "SystemCallFilter=~personality", "SystemCallErrorNumber=EPERM",
                     "PrivateTmp=yes", "RestrictAddressFamilies=~AF_UNIX", "CapabilityBoundingSet=CAP_DAC_READ_SEARCH"):
            self.assertIn(want, p239)
        # 1.76.1c: fail closed where systemd may skip a setting - a mount it insists on (no namespace: no run), and the
        # IPC calls, the clock and the host name refused by the system call filter with every profile
        for want in ("TemporaryFileSystem=/run/user:ro", "SystemCallFilter=~" + " ".join(host.IPC_CALLS),
                     "SystemCallFilter=~@clock sethostname setdomainname", "SystemCallErrorNumber=EPERM"):
            self.assertIn(want, props)
        self.assertFalse([x for x in p239 if x.startswith("InaccessiblePaths=-/proc/sys")])     # the host's IPC files, read as they are
        self.assertEqual((host.profile_of(239), host.profile_of(247), host.profile_of(248)), ("239", "239", "full"))
        self.assertIn("PrivatePIDs=yes", [v[i + 1] for i, x in enumerate(v) if x == "-p"])
        self.assertFalse([x for x in props if "AUDIT" in x])
        real = os.geteuid
        try:
            os.geteuid = lambda: 0
            r = host.sandbox_cmd("/usr/bin/systemd-run", 60, version=252)
            r239 = host.sandbox_cmd("/usr/bin/systemd-run", 60, version=239)
        finally:
            os.geteuid = real
        pr = [r[i + 1] for i, x in enumerate(r) if x == "-p"]
        self.assertNotIn("--user", r)
        # 1.76.1c: a dynamic user on every systemd - a numeric User= with no passwd entry is refused before 257 (exit
        # 217 on the real 239 and 255); never nobody (shared with host daemons)
        p2 = [r239[i + 1] for i, x in enumerate(r239) if x == "-p"]
        for q in (pr, p2):
            self.assertIn("DynamicUser=yes", q)
            self.assertFalse([x for x in q if x.startswith(("User=", "Group="))])
        self.assertIn("AmbientCapabilities=CAP_DAC_READ_SEARCH", pr)
        self.assertFalse([x for x in pr if "AUDIT" in x])
        self.assertEqual(host.find_sandbox("/nonexistent/systemd-run"), "")
        self.assertEqual(host.find_sandbox("/usr/bin/env"), "")        # only a program named systemd-run
        self.assertEqual(host.sandbox_probe(""), "systemd-run was not found")
        # 1.76.1c: as a user other than root below systemd 257 (no PID namespace of its own): refused, said why
        if os.geteuid() != 0 and host.find_pwsh(""):
            self.assertIn("needs systemd 257 or later (here 255)", host.sandbox_probe("/usr/bin/systemd-run", version=255))

    def test_the_replay(self):
        """1.76.0: only the check's own command lines are run outside the sandbox; the shim gives each its answer, any
        other command line 'not captured' (exit 2)."""
        for ok in (["/usr/sbin/auditctl", "-l"], ["/usr/bin/systemctl", "is-enabled", "sshd.service"],
                   ["/usr/bin/systemctl", "show", "-p", "ActiveState", "auditd"]):
            self.assertTrue(host.replay_ok(ok), ok)
        for bad in (["/usr/sbin/auditctl", "-D"], ["/usr/sbin/auditctl", "-e", "0"], ["/usr/bin/systemctl", "stop", "sshd"],
                    ["/usr/bin/systemctl", "is-active", "-H", "x"], ["/usr/bin/systemctl", "show", "-p", "A;B", "x"],
                    ["/usr/bin/systemctl", "is-active", "--user", "x"], ["/usr/bin/rpm", "-q", "bash"]):
            self.assertFalse(host.replay_ok(bad), bad)
        # 1.76.0 (Codex): what depends on a namespace, a socket or NSS is replayed - an IPC setting, getent, id, a user or
        # group name of stat or find (then all of that program's calls); the rest runs inside
        rf = host.replays_for([["/usr/sbin/sysctl", "-n", "kernel.shmmax"], ["/usr/sbin/sysctl", "-n", "net.ipv4.ip_forward"],
                               ["/usr/bin/rpm", "-q", "bash"], ["/usr/bin/getent", "passwd", "root"], ["/usr/bin/id", "-u", "root"],
                               ["/usr/bin/stat", "-c", "%a", "/etc/shadow"]])
        self.assertEqual(rf, [["/usr/sbin/sysctl", "-n", "kernel.shmmax"], ["/usr/sbin/sysctl", "-n", "net.ipv4.ip_forward"],
                              ["/usr/bin/getent", "passwd", "root"], ["/usr/bin/id", "-u", "root"]])
        self.assertEqual(host.replays_for([["/usr/sbin/sysctl", "-n", "net.ipv4.ip_forward"], ["/usr/bin/stat", "-c", "%a", "/x"]]), [])
        self.assertEqual(len(host.replays_for([["/usr/bin/stat", "-c", "%U", "/x"], ["/usr/bin/stat", "-c", "%a", "/x"]])), 2)
        self.assertEqual(len(host.replays_for([["/usr/bin/find", "/", "-xdev", "-nouser", "-print"]])), 1)
        self.assertEqual(len(host.replays_for([["/usr/bin/find", "/", "-type", "d", "-printf", "%u %p\\n"]])), 1)
        self.assertEqual(len(host.replays_for([["/usr/bin/find", "/", "-type", "d", "-perm", "-0002", "-print"]])), 1)   # / holds /tmp
        for ok in (["/usr/bin/getent", "passwd", "root"], ["/usr/bin/id", "-u", "-n", "root"], ["/usr/sbin/sysctl", "-n", "kernel.sem"],
                   ["/usr/bin/stat", "-c", "%U %n", "/etc/shadow"], ["/usr/bin/find", "/home", "-nouser", "-print"]):
            self.assertTrue(host.replay_ok(ok), ok)
        for bad in (["/usr/bin/getent", "hosts", "x"], ["/usr/sbin/sysctl", "-w", "kernel.sem=1"], ["/usr/bin/find", "/", "-delete"],
                    ["/usr/bin/stat", "--printf", "%U", "/x"], ["/usr/bin/id", "-Z"]):
            self.assertFalse(host.replay_ok(bad), bad)
        # 1.76.0 (Codex, round 3): /tmp, /var/tmp, /dev/shm are the sandbox's own - find and stat there replayed, any other
        # read not tried (grep there too: outside it would read a host FIFO)
        self.assertEqual(len(host.replays_for([["/usr/bin/find", "/", "-xdev", "-type", "d", "-perm", "-0002", "-print"]])), 1)
        self.assertEqual(len(host.replays_for([["/usr/bin/find", "/var", "-perm", "-0002", "-print"]])), 1)
        self.assertEqual(len(host.replays_for([["/usr/bin/stat", "-c", "%a", "/tmp"]])), 1)
        self.assertEqual(host.replays_for([["/usr/bin/find", "/etc", "-perm", "-0002", "-print"]]), [])
        # 1.76.1 (Codex): per command, from the syntax tree - the read-only check reports each cmdlet's paths; only a
        # literal one, outside the sandbox's own places after . .. // and the host's symlinks, is read inside
        link = tempfile.mkdtemp(dir=os.path.expanduser("~"), prefix=".site-ak-link-")
        os.symlink("/tmp", os.path.join(link, "to-tmp"))
        try:
            def blind_of(code):
                g = self.run_ps(self.gate, code)
                gp = g.get("paths") or []
                return host.blind(code, [list(c) for c in g.get("calls") or []], [gp] if isinstance(gp, dict) else gp)
            for code, word in (("$l = Get-Content /tmp/x; return $true", "cannot show /tmp"), ("return (Test-Path '/var/tmp')", "cannot show /var/tmp"),
                               ("$a = Get-ChildItem /dev/shm; return $true", "cannot show /dev as"),
                               ("$a = Get-ChildItem / -Recurse -File; return $true", "cannot show /tmp"),
                               ("$a = Get-ChildItem -Path '/var' -Recurse; return $true", "cannot show /var/tmp"),
                               ("$s = & /usr/bin/stat -c '%a' '/etc/x'; return (Test-Path /tmp/x)", "cannot show /tmp"),       # one line, two commands
                               ("return (Test-Path '/var/../tmp/x')", "cannot show /tmp"), ("return (Test-Path '//tmp//x')", "cannot show /tmp"),
                               ("return (Test-Path '%s/to-tmp/x')" % link, "cannot show /tmp"),                                   # a symlink into /tmp
                               ("$p = '/etc/x'; return (Test-Path $p)", "not a plain literal"),
                               ("return (Test-Path ('/et' + 'c/x'))", "not a plain literal"),
                               ("$a = Get-ChildItem; return $true", "not a plain literal"),
                               ("$l = Get-Content /proc/1/environ; return $true", "process"),
                               ("$l = Select-String -Pattern x -Path '/proc/self/status'; return $true", "process"),
                               # 1.76.1b (Codex): a path piped in is validated as the cmdlet's own; other input piped in is not tried
                               ("return ('/tmp/x' | Test-Path)", "cannot show /tmp"), ("return (@('/etc/a', '/tmp/b') | Test-Path)", "cannot show /tmp"),
                               ("$p = '/etc/x'; return ($p | Test-Path)", "not a plain literal"),
                               ("$a = Get-ChildItem /etc/ssh | Get-Content; return $true", "not a plain literal"),
                               ("$a = Get-ChildItem /etc/ssh | Select-String 'x'; return $true", "not a plain literal"),
                               ("$a = Get-Item /etc/hosts | Get-Item; return $true", "not a plain literal")):
                with self.subTest(code=code):
                    self.assertIn(word, blind_of(code))
            for code in ("$o = @(& /usr/bin/find /tmp -perm -0002 -print 2>&1); return $true", "$a = Get-ChildItem /etc -Recurse; return $true",
                         "$l = Get-Content /etc/tmpfiles.d/x.conf; return $true", "$l = Get-Content /var/tmpx; return $true",
                         "$l = Get-Content /proc/sys/kernel/x; return $true", "$l = Get-Content /proc/cmdline; return $true",
                         "$l = Get-Content -LiteralPath '/etc/ssh/sshd_config' | Where-Object { $_ -match 'x' }; return $true",
                         "$l = '/etc/x' | Get-Content; return $true", "$a = Get-Content /etc/x | Select-String 'x'; return $true",
                         "$a = '/etc/y' | Get-Item; return $true", "$a = ('/etc/z') | Get-ChildItem; return $true"):
                with self.subTest(code=code):
                    self.assertEqual(blind_of(code), "")
        finally:
            shutil.rmtree(link, ignore_errors=True)
        self.assertIn("cannot show /tmp", host.blind("x", [["/usr/bin/grep", "-r", "-l", "a", "/"]]))
        self.assertIn("cannot show /tmp", host.blind("x", [["/usr/bin/grep", "-l", "a", "/tmp/x"]]))
        self.assertEqual(host.blind("x", [["/usr/bin/grep", "-l", "a", "/etc/x"]]), "")
        # what the sandbox shows as its own: not tried
        for code, word in (("Get-Content /proc/mounts", "mount table"), ("Get-Content /proc/self/mountinfo", "mount table"),
                           ("Get-Content /etc/mtab", "mount table"), ("Get-Content /proc/sys/kernel/shmmax", "IPC"),
                           ("Get-ChildItem /dev/mqueue", "IPC"), ("(Get-Item /etc/x).User -eq 'root'", "user or group"),
                           ("Get-Item /etc/x | Select-Object -ExpandProperty Group", "user or group")):
            self.assertIn(word, host.blind(code), code)
        self.assertEqual(host.blind("$o = @(& /usr/bin/stat -c '%U' '/etc/x' 2>$null); return ($o[0] -eq 'root')"), "")
        g = self.run_ps(self.gate, "$a = @(& /usr/sbin/auditctl -l 2>&1); $e = & /usr/bin/systemctl is-enabled auditd; return $true")
        self.assertEqual(g["calls"], [["/usr/sbin/auditctl", "-l"], ["/usr/bin/systemctl", "is-enabled", "auditd"]])
        d = tempfile.mkdtemp()
        try:
            binds = host.write_shims(d, {("/usr/bin/systemctl", "is-active", "auditd"): (b"active\n", b"", 0),
                                         ("/usr/bin/systemctl", "is-enabled", "x"): (b"", b"No such unit 'x'\n", 1)})
            shim = binds.get("/usr/bin/systemctl")
            self.assertTrue(shim and os.access(shim, os.X_OK))
            run = lambda *a: subprocess.run(["/bin/sh", shim] + list(a), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            r = run("is-active", "auditd")
            self.assertEqual((r.returncode, r.stdout), (0, b"active\n"))
            r = run("is-enabled", "x")
            self.assertEqual((r.returncode, r.stderr), (1, b"No such unit 'x'\n"))
            r = run("stop", "auditd")
            self.assertEqual(r.returncode, 2)
            self.assertIn(b"not captured", r.stderr)
            r = run("is-active", "auditd", "x")
            self.assertEqual(r.returncode, 2)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class ManyHosts(unittest.TestCase):
    """1.76.0: the automatic pick, many hosts grouped by STIG, Copy all, the report."""
    SM = {"status": "found", "collectionId": "1", "assetId": "12",
          "rules": {"SV-1": {"v": "V-1", "severity": "high"}, "SV-2": {"v": "V-2", "severity": "medium"}, "SV-3": {"v": "V-3", "severity": "low"},
                    "SV-4": {"v": "V-4", "severity": "medium"}, "SV-5": {"v": "V-5", "severity": "medium"}, "SV-6": {"v": "V-6", "severity": "medium"},
                    "SV-7": {"v": "V-7", "severity": "medium"}},
          "reviews": {"SV-2": {"result": "fail", "rule": "SV-2r1_rule"}, "SV-3": {"result": "pass"}, "SV-4": {"result": "fail", "protect": "RA"},
                      "SV-5": {"result": "notchecked", "rule": "SV-5r1_rule"}, "SV-6": {"result": "notapplicable"}, "SV-7": {"result": "informational"}}}

    def test_the_automatic_pick(self):
        r = a.stig_answer_auto(self.SM, "both")
        self.assertEqual([x["v"] for x in r["rules"]], ["V-1", "V-2", "V-5"])         # CAT I first; never pass, N/A, RA
        # the default: Not Reviewed only - where Evaluate-STIG cannot decide, a key pays off
        self.assertEqual([x["v"] for x in a.stig_answer_auto(self.SM)["rules"]], ["V-1", "V-5"])
        self.assertEqual(r["protected"], ["V-4"])
        self.assertEqual([x["v"] for x in a.stig_answer_auto(self.SM, "open")["rules"]], ["V-2"])
        self.assertEqual([x["v"] for x in a.stig_answer_auto(self.SM, "not_reviewed")["rules"]], ["V-1", "V-5"])
        self.assertEqual([x["v"] for x in a.stig_answer_auto(self.SM, "both", "CAT I")["rules"]], ["V-1"])
        self.assertEqual([x["v"] for x in a.stig_answer_auto(self.SM, "both", [2, 3])["rules"]], ["V-2", "V-5"])
        c = a.stig_answer_auto(self.SM, "both", most=2)
        self.assertEqual((len(c["rules"]), c["left_out"]), (2, 1))
        self.assertIn("STIG Manager: no asset", a.stig_answer_auto({"status": "not_found", "why": "no asset"})["why"])

    def test_attachments_never_picked(self):
        rules = a.stig_answer_auto(self.SM, "both")["rules"]
        items = a.stig_answer_att_items(self.SM, rules)
        self.assertEqual(items, [{"collectionId": "1", "assetId": "12", "ruleId": "SV-2r1_rule"}, {"collectionId": "1", "assetId": "12", "ruleId": "SV-5r1_rule"}])
        d = a.stig_answer_drop_att(rules, self.SM, {"reviews": [{"ruleId": "SV-2r1_rule", "attachments": 1}, {"ruleId": "SV-5r1_rule", "attachments": 0}]})
        self.assertEqual(([x["v"] for x in d["rules"]], d["dropped"]), (["V-1", "V-5"], ["V-2"]))
        self.assertEqual(a.stig_answer_drop_att(rules, self.SM, {"error": "no token"})["rules"], [])     # not readable: none picked

    def test_many_hosts_grouped_by_stig(self):
        def pk(v, es, ok, exp):
            return {"v": v, "sv": "S" + v, "title": "t", "cat": "CAT II", "es": es, "ok": ok, "expected": exp, "why": "" if ok else "x", "check_text": T_OWNER}
        hosts = [{"host": "h8", "ok": True, "bench": "RHEL_8_STIG", "rev": "V2R9", "picks": [pk("V-230221", "Open", True, "Open"), pk("V-257777", "", False, "")]},
                 {"host": "h9a", "ok": True, "bench": "RHEL_9_STIG", "rev": "V2R10", "picks": [pk("V-230221", "", False, ""), pk("V-257777", "Open", True, "Open")]},
                 {"host": "h9b", "ok": True, "bench": "RHEL_9_STIG", "rev": "V2R10", "picks": [pk("V-230221", "", False, ""), pk("V-257777", "Not Reviewed", True, "Not_Reviewed")]},
                 {"host": "h9c", "ok": True, "bench": "RHEL_9_STIG", "rev": "V2R10", "picks": [pk("V-230221", "", False, ""), pk("V-257777", "Not Reviewed", True, "Not_Reviewed")]},
                 {"host": "dead", "ok": False, "why": "not reached"}]
        m = a.stig_answer_merge(hosts)
        self.assertEqual([(g["label"], g["hosts"], g["rep"]) for g in m["groups"]], [("RHEL 8 V2R9", ["h8"], "h8"), ("RHEL 9 V2R10", ["h9a", "h9b", "h9c"], "h9a")])
        p9 = m["groups"][1]["picks"][0]
        self.assertEqual((p9["v"], p9["expected"], p9["hosts"], p9["es"]), ("V-257777", "Not_Reviewed", ["h9a", "h9b", "h9c"], "Not Reviewed 2, Open 1"))
        self.assertIn("Not Reviewed on 2 hosts, Open on 1 host - the key applies where Evaluate-STIG says Not_Reviewed", p9["expected_note"])
        self.assertEqual(m["failed"], [{"host": "dead", "why": "not reached", "why_mail": "it did not work (the job's output has why)"}])
        self.assertEqual(m["none"], [])
        hosts.append({"host": "h10", "ok": True, "bench": "RHEL_10_STIG", "rev": "V1R3", "picks": [pk("V-999", "", False, "")]})
        self.assertEqual(a.stig_answer_merge(hosts)["none"], ["V-999"])          # in none of the hosts' STIGs
        # the report: a section per STIG, Copy all with every block in its own <Vuln ID>, a sheet per STIG
        groups = []
        for g in m["groups"]:
            d = a.stig_answer_drafts(g["picks"], {}, False)
            rows = a.stig_answer_rows(g["picks"], d, {"gate": dict((v, {"ok": True, "used": []}) for v in d),
                                                      "trial": dict((v, {"ran": True, "ok": True, "valid": True}) for v in d)}, "SITEA")
            groups.append(dict(g, rows=rows))
        doc = a.stig_answer_report_multi(groups, "SITEA", False, m["failed"], ["V-999"], {"RHEL 9 V2R10": 3}, "answer_keys_pick both", {"RHEL 9 V2R10": ["V-1"]})
        self.assertEqual(doc["web"]["title"], "STIG answer keys: 2 ready to copy, 1 not drafted - 2 STIGs, 4 hosts")
        titles = [s["title"] for s in doc["web"]["sections"]]
        for t in ("RHEL 8 V2R9 - answer keys to copy (1)", "RHEL 9 V2R10 - answer keys to copy (1)", "RHEL 9 V2R10 - the answer file (1 block)",
                  "Hosts that did not work (1)", "In none of the hosts' STIGs (1)", "Already in your answer file (1)",
                  "Left out: more than answer_keys_max_rules", "How this was made"):
            self.assertIn(t, titles)
        allx = [s for s in doc["web"]["sections"] if s["title"] == "RHEL 9 V2R10 - the answer file (1 block)"][0]["details"][0][0]["texts"][0]
        self.assertEqual(allx["button"], "Copy the answer file for RHEL 9 V2R10")
        self.assertIn('  <Vuln ID="V-257777">\n    <AnswerKey Name="SITEA">', allx["cmd"])
        self.assertTrue(allx["cmd"].rstrip().endswith("</STIGComments>"))       # 1.79.4: the whole file
        sec9 = [s for s in doc["web"]["sections"] if s["title"] == "RHEL 9 V2R10 - answer keys to copy (1)"][0]
        self.assertIn("Hosts", sec9["columns"])
        self.assertEqual(len(sec9["columns"]), len(sec9["kinds"]))
        self.assertEqual([x["name"] for x in doc["xlsx"]], ["RHEL 8 V2R9", "RHEL 9 V2R10", "Not drafted"])
        self.assertIn("Picked: automatically from STIG Manager (answer_keys_rules empty) - answer_keys_pick both.", doc["web"]["sections"][-1]["lines"])

    def test_the_cap_over_the_union(self):
        """1.76.0b (Codex): host A has 150 CAT III, host B a CAT I (disjoint): the CAT I rule survives the cap, the
        omitted count is exact."""
        a3 = [{"v": "V-%d" % (1000 + i), "sv": "SV-%d" % (1000 + i), "cat": 3} for i in range(150)]
        b1 = [{"v": "V-9", "sv": "SV-9", "cat": 1}, {"v": "V-1000", "sv": "SV-1000", "cat": 3}]          # one shared with A
        c = a.stig_answer_cap([{"key": "RHEL_9_STIG|V2R10", "rules": a3}, {"key": "RHEL_9_STIG|V2R10", "rules": b1},
                               {"key": "RHEL_8_STIG|V2R9", "rules": b1}], 150)
        k = c["RHEL_9_STIG|V2R10"]
        self.assertEqual(k["allowed"][0], "SV-9")
        self.assertEqual((len(k["allowed"]), k["left_out"]), (150, 1))          # union 151 - 150
        self.assertEqual(c["RHEL_8_STIG|V2R9"], {"allowed": ["SV-9", "SV-1000"], "left_out": 0})
        self.assertEqual(a.stig_answer_auto(self.SM, "both", most=0)["rules"][0], {"v": "V-1", "sv": "SV-1", "cat": 1})

    def test_one_draft_per_stig_revision(self):
        """1.76.0b (Codex): a rule id two revisions share - each its own text, its own ACT draft (benchmark|revision|rule)."""
        g7 = {"key": "RHEL_9_STIG|V2R7", "label": "RHEL 9 V2R7", "picks": [{"v": "V-2", "sv": "SV-2", "ok": True, "check_text": T_ASK}]}
        g10 = {"key": "RHEL_9_STIG|V2R10", "label": "RHEL 9 V2R10", "picks": [{"v": "V-2", "sv": "SV-2", "ok": True, "check_text": T_ASK + " (V2R10)"}]}
        fa = a.stig_answer_for_act_groups([g7, g10], {})
        self.assertEqual([x["ak"] for x in fa], ["RHEL_9_STIG|V2R7|V-2", "RHEL_9_STIG|V2R10|V-2"])
        self.assertTrue(fa[1]["check_text"].endswith("(V2R10)"))
        act = {"RHEL_9_STIG|V2R7|V-2": {"code": "return $false"}, "RHEL_9_STIG|V2R10|V-2": {"code": "return $true"}}
        self.assertEqual(a.stig_answer_act_of(act, g10), {"V-2": {"code": "return $true"}})
        self.assertEqual(a.stig_answer_act_of(act, g7), {"V-2": {"code": "return $false"}})
        self.assertEqual(a.stig_answer_for_act_groups([g7], {}, False), [])

    def test_a_trial_host_that_failed(self):
        """1.76.0b (Codex): its STIG's blocks are not tried, said why."""
        picks = [dict(v="V-1", sv="SV-1", title="Owner", ok=True, expected="Open", check_text=T_OWNER, es="Open")]
        d = a.stig_answer_drafts(picks, {}, False)
        r = a.stig_answer_rows(picks, d, {"failed_host": "sandbox /bin/true is not systemd-run"}, "DEFAULT")[0]
        self.assertEqual(r["why"], "Not drafted - not tried: the trial host failed (sandbox /bin/true is not systemd-run).")
        self.assertEqual(r["block"], "")

    def test_already_in_your_answer_file(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "RHEL9.xml"), "w") as f:
                f.write('<STIGComments Name="RHEL9"><Vuln ID="V-1"><AnswerKey Name="SITEA"/></Vuln><Vuln ID="V-2"><AnswerKey Name="OTHER"/></Vuln></STIGComments>')
            with open(os.path.join(d, "bad.xml"), "w") as f:
                f.write("<not xml")
            self.assertEqual(a.stig_answer_in_files(d, "SITEA"), {"V-1": "RHEL9.xml"})
            self.assertEqual(a.stig_answer_in_files(os.path.join(d, "none"), "SITEA"), {})
        finally:
            shutil.rmtree(d, ignore_errors=True)


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
            self.assertEqual(host.expected_check(d), {"found": True, "where": "Modules/M/M.psm1:2", "note": ""})
            self.assertEqual(host.es_folder(os.path.join(d, "Evaluate-STIG.ps1")), d)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class Pwsh(unittest.TestCase):
    """1.76.1: the pwsh the check and the trial run - the given one or the first on the PATH, through its links, only
    root-owned and writable by root alone; How this was made names it."""
    def test_found_and_trusted(self):
        found = host.find_pwsh("")
        if found:
            self.assertEqual(found, os.path.realpath(found))
            self.assertEqual(host.pwsh_trusted(found), "")
        else:
            # 1.76.1c: the host's own pwsh may sit below a folder another user can write (CI's runner: /opt is the
            # runner user's) - then it is refused, and said why; never a pwsh found that is not trusted
            on = shutil.which("pwsh")
            if on:
                self.assertTrue(host.PWSH_WHY, "a pwsh on the PATH refused with no reason")
                self.assertTrue(any(("not owned by root" in w or "can be written by others" in w) for w in host.PWSH_WHY), host.PWSH_WHY)
        d = tempfile.mkdtemp()
        try:
            mine = os.path.join(d, "pwsh")
            with open(mine, "w") as f:
                f.write("#!/bin/sh\n")
            os.chmod(mine, 0o755)
            if os.geteuid() != 0:
                self.assertIn("not owned by root", host.pwsh_trusted(mine))
                self.assertEqual(host.find_pwsh(mine), "")
                self.assertIn("not owned by root", host.PWSH_WHY[0])
        finally:
            shutil.rmtree(d, ignore_errors=True)
        doc = a.stig_answer_report([], "h", "DEFAULT", {"pwsh": "/opt/microsoft/powershell/7/pwsh"})
        self.assertIn("The read-only check and the trial ran with /opt/microsoft/powershell/7/pwsh.", doc["web"]["sections"][-1]["lines"])


class PwshSecure(unittest.TestCase):
    """1.76.1b (Codex): pwsh and every folder above it root's and writable by root alone - a root-owned pwsh in a 0777
    folder is refused (it could be swapped before it runs, as root); one under /usr/local/bin (root 0755) is not."""
    class St(object):
        def __init__(self, uid, mode):
            self.st_uid, self.st_mode = uid, mode

    def fake(self, tree):
        def stat(p):
            if p not in tree:
                raise OSError(2, "no such file", p)
            return self.St(*tree[p])
        return stat

    def test_folders_above(self):
        ok = {"/": (0, 0o40755), "/usr": (0, 0o40755), "/usr/local": (0, 0o40755), "/usr/local/bin": (0, 0o40755), "/usr/local/bin/pwsh": (0, 0o100755)}
        self.assertEqual(host.pwsh_trusted("/usr/local/bin/pwsh", self.fake(ok)), "")
        bad = dict(ok, **{"/opt": (0, 0o41777), "/opt/pwsh": (0, 0o100755)})
        self.assertIn("the folder /opt (above /opt/pwsh) can be written by others than root", host.pwsh_trusted("/opt/pwsh", self.fake(bad)))
        mine = dict(ok, **{"/home": (0, 0o40755), "/home/u": (1000, 0o40700), "/home/u/pwsh": (0, 0o100755)})
        self.assertIn("the folder /home/u (above /home/u/pwsh) is not owned by root", host.pwsh_trusted("/home/u/pwsh", self.fake(mine)))
        self.assertIn("can be written by others", host.pwsh_trusted("/usr/local/bin/pwsh", self.fake(dict(ok, **{"/usr/local/bin/pwsh": (0, 0o100775)}))))

    def test_changed_before_it_runs(self):
        # 1.76.1c: on a file of the test's own (not the host's pwsh, which may not be trusted here): its identity kept,
        # then as if swapped since it was checked - not run
        d = tempfile.mkdtemp()
        saved = dict(host.PWSH_ID)
        try:
            p = os.path.join(d, "pwsh")
            with open(p, "w") as f:
                f.write("#!/bin/sh\nexit 0\n")
            os.chmod(p, 0o755)
            host.PWSH_ID[p] = host._identity(p)
            self.assertTrue(host.pwsh_same(p))
            with open(p, "a") as f:
                f.write("# swapped\n")
            self.assertFalse(host.pwsh_same(p))
            host.PWSH_ID[p] = (0, 0, 0, 0, 0, 0)
            r, err, _ = host.run_ps(p, "/nonexistent.ps1", "/nonexistent", 10)
            self.assertIsNone(r)
            self.assertIn("changed since it was checked - not run", err)
            self.assertFalse(host.pwsh_same(os.path.join(d, "never-checked")))
        finally:
            host.PWSH_ID.clear()
            host.PWSH_ID.update(saved)
            shutil.rmtree(d, ignore_errors=True)


class HiddenPlaces(unittest.TestCase):
    """1.76.1d (Codex): every place the sandbox's own settings hide or replace is the sandbox's, not the host's - a code
    that reads one (or walks down from above one) is not tried, find and stat of one are replayed: /run/user
    (TemporaryFileSystem), the dynamic user's /var/lib/private, /var/cache/private, /var/log/private (DynamicUser - and
    a service's /var/lib/NAME that links into them), the kernel's modules (ProtectKernelModules), /dev (PrivateDevices,
    but its pseudo-devices)."""
    def tp(self, path, rec=False):
        return host.blind("", [], [{"cmd": "Test-Path", "path": path, "literal": True, "recurse": rec}])

    def test_run_user(self):
        self.assertIn("/run/user (hidden in the sandbox)", self.tp("/run/user/1000/x"))
        self.assertIn("/run/user", self.tp("/run", True))                 # walking down from above it
        self.assertIn("/run/user", self.tp("/run/*"))                      # a wildcard walks down too
        self.assertEqual(self.tp("/run/sshd.pid"), "")
        self.assertEqual(self.tp("/run/userx"), "")
        sc = ["/usr/bin/stat", "-c", "%a", "/run/user/1000"]
        self.assertEqual(host.replays_for([sc]), [sc])
        self.assertIn("/run/user", host.blind("", [["/usr/bin/grep", "-r", "x", "/run"]], []))

    def test_dynamic_users_private(self):
        for d in ("/var/lib/private", "/var/cache/private", "/var/log/private"):
            self.assertIn("a dynamic user's private folder", self.tp(d + "/foo/state"))
            sc = ["/usr/bin/stat", "-c", "%a", d]
            self.assertEqual(host.replays_for([sc]), [sc])
        self.assertIn("private folder", self.tp("/var/lib", True))
        self.assertIn("private folder", self.tp("/var/log", True))
        self.assertEqual(self.tp("/var/lib/foo"), "")
        self.assertEqual(self.tp("/var/log/messages"), "")
        # a service's state folder that links into /var/lib/private: through the host's link, the same place
        lk = tempfile.mkdtemp(dir=os.path.expanduser("~"), prefix=".site-ak-link-")
        try:
            os.symlink("/var/lib/private/site-ak-svc", os.path.join(lk, "svc"))
            self.assertIn("a dynamic user's private folder", self.tp(os.path.join(lk, "svc", "state")))
        finally:
            shutil.rmtree(lk, ignore_errors=True)
        fc = ["/usr/bin/find", "/var/lib", "-perm", "-0002"]
        self.assertEqual(host.replays_for([fc]), [fc])

    def test_modules_and_devices(self):
        self.assertIn("the kernel's modules", self.tp("/usr/lib/modules/6.8.0/modules.dep"))
        self.assertIn("the kernel's modules", self.tp("/lib/modules/6.8.0"))
        self.assertIn("/dev (the sandbox has its own", self.tp("/dev/sda"))
        self.assertIn("/dev (the sandbox has its own", self.tp("/dev/shm/x"))
        self.assertIn("/dev (the sandbox has its own", self.tp("/dev/mqueue/x"))
        self.assertIn("/dev", self.tp("/dev", True))
        for same in ("/dev/null", "/dev/urandom", "/dev/zero", "/usr/lib/modprobe.d/x.conf", "/usr/lib", "/devx"):
            self.assertEqual(self.tp(same), "", same)
        fc = ["/usr/bin/find", "/dev", "-type", "b"]
        self.assertEqual(host.replays_for([fc]), [fc])


class EveryMount(unittest.TestCase):
    """1.76.1f: every mount point of the host read-only in the sandbox by name (ReadOnlyPaths=/ alone left /run/lock - a
    tmpfs of its own - writable with ProtectKernelTunables on systemd 255, and CI's runner a 777 folder on / on 259),
    no host mount after it starts (MountFlags=private: an automount fired inside mounted writable), automounts fired
    before within one budget, and no sandbox at all when the mounts cannot be read or named - never a partial list."""
    INFO = (b"22 1 0:21 / /proc rw,nosuid shared:5 - proc proc rw\n"
            b"28 1 252:1 / / rw,relatime shared:1 - ext4 /dev/vda1 rw\n"
            b"30 28 0:25 / /run rw,nosuid shared:12 - tmpfs tmpfs rw\n"
            b"31 30 0:26 / /run/lock rw,nosuid shared:13 - tmpfs tmpfs rw\n"
            b"32 30 0:27 / /run/user/1000 rw,nosuid shared:14 - tmpfs tmpfs rw\n"
            b"33 28 0:28 / /opt rw,relatime shared:15 - autofs systemd-1 rw,fd=5,direct\n"
            b"34 33 7:0 / /opt rw,relatime shared:16 - ext4 /dev/loop0 rw\n"
            b"35 28 7:1 / /var/log/audit rw,nosuid shared:17 master:3 - ext4 /dev/loop1 rw\n"
            b"36 28 0:29 / /tmp rw shared:18 - tmpfs tmpfs rw\n"
            b"37 28 0:30 / /var/lib/containers/storage/overlay/ab/merged rw - overlay overlay rw\n"
            b"38 28 0:31 / /net rw - autofs systemd-1 rw,fd=7,indirect\n")

    def with_info(self, info, fn):
        import builtins
        import io
        real = builtins.open

        def fake(path, *a, **k):
            if path == "/proc/self/mountinfo":
                return io.BytesIO(info) if "b" in (a[0] if a else k.get("mode", "r")) else io.StringIO(info.decode())
            return real(path, *a, **k)
        builtins.open = fake
        try:
            return fn()
        finally:
            builtins.open = real

    def test_the_mount_points(self):
        got = self.with_info(self.INFO, host.mounts)
        self.assertIn(("/opt", "autofs", "rw,fd=5,direct"), got)
        self.assertIn(("/var/log/audit", "ext4", "rw"), got)                # an optional field (master:3) before the -
        mps = self.with_info(self.INFO, host.probe_mounts)
        self.assertEqual(mps, ["/", "/run", "/run/lock", "/opt", "/var/log/audit", "/var/lib/containers/storage/overlay/ab/merged"])
        c = self.with_info(self.INFO, lambda: host.sandbox_cmd("/usr/bin/systemd-run", 60, version=255))
        props = [c[i + 1] for i, x in enumerate(c) if x == "-p"]
        for want in ("MountFlags=private", "ReadOnlyPaths=/", "ReadOnlyPaths=-/", "ReadOnlyPaths=-/run/lock", "ReadOnlyPaths=-/opt",
                     "ReadOnlyPaths=-/var/log/audit", "ReadOnlyPaths=-/var/lib/containers/storage/overlay/ab/merged"):
            self.assertIn(want, props)                                       # visible: protected whatever its name
        for not_ in ("ReadOnlyPaths=-/tmp", "ReadOnlyPaths=-/proc", "ReadOnlyPaths=-/run/user/1000", "ReadOnlyPaths=-/net"):
            self.assertNotIn(not_, props)

    def test_names_left_to_the_probe(self):
        # 1.76.1g (review): a name systemd cannot be given, one not UTF-8, or more than MOUNTS_MAX: left out of the list by
        # name - never a refusal for that; the probe reads the mount table inside and refuses only a writable mount
        info = (self.INFO + b"39 28 7:2 / /srv/my\\040data rw - ext4 /dev/loop2 rw\n"
                + b"40 28 7:3 / /srv/it\xe2\x80\x99s rw - ext4 /dev/loop3 rw\n" + b"41 28 7:4 / /srv/\xff rw - ext4 /dev/loop4 rw\n")
        mps = self.with_info(info, host.probe_mounts)
        self.assertNotIn("/srv/my data", mps)
        self.assertIn("/srv/it\u2019s", mps)                                 # a typographic quote is no quote to systemd
        self.assertEqual(len([m for m in mps if m.startswith("/srv/")]), 1)
        many = self.INFO + b"".join(b"%d 28 7:9 / /m/%d rw - ext4 /dev/x rw\n" % (100 + i, i) for i in range(1100))
        self.assertEqual(len(self.with_info(many, host.probe_mounts)), host.MOUNTS_MAX)
        with self.assertRaises(host.MountsError):
            self.with_info(b"garbage line\n", host.probe_mounts)

    @unittest.skipUnless(shutil.which("pwsh"), "no pwsh here")
    def test_the_probe_reads_the_mount_table(self):
        # the probe's own mount-table rule, run on a mount table given to it: a writable host mount is named, the
        # sandbox's own places, its cgroup, an automount point, the kernel's privileged interfaces and what it cannot
        # reach are not; a file is never exempt; /dev/pts (the host's devpts) is named
        import base64
        import subprocess
        d = tempfile.mkdtemp(dir=os.path.expanduser("~"), prefix=".site-ak-mi-")
        try:
            sp = os.path.join(d, "a b")
            os.makedirs(sp)
            huge, mq = os.path.join(d, "huge"), os.path.join(d, "mq")
            os.makedirs(huge)
            os.makedirs(mq)
            fil = os.path.join(d, "hosts")
            with open(fil, "w") as f:
                f.write("x")
            lines = ["1 0 8:1 / / rw - ext4 /dev/sda1 rw", "2 1 8:1 / /etc/hosts ro - ext4 /dev/sda1 rw",
                     "3 1 8:1 / %s rw - ext4 /dev/sda1 rw" % fil, "4 1 0:5 / /net rw - autofs systemd-1 rw",
                     "5 1 0:6 / /dev/pts rw - devpts devpts rw", "6 1 0:7 / /sys/fs/selinux rw - selinuxfs selinuxfs rw",
                     "7 1 0:8 / /sys rw - sysfs sysfs rw", "8 1 0:9 / /proc rw - proc proc rw", "9 8 0:9 / /proc/sys rw - proc proc rw",
                     "10 1 0:10 / /tmp rw - tmpfs tmpfs rw", "11 1 8:2 / /nonexistent-site-ak rw - ext4 /dev/sdb rw",
                     "12 1 8:3 / %s rw - ext4 /dev/sdc rw" % sp.replace(" ", "\\040"), "13 1 8:4 / /boot ro - ext4 /dev/sdd rw",
                     # 1.76.1h: hugetlbfs and mqueue a user can write - named; a name not UTF-8 - named, never "cannot reach"
                     "14 1 0:11 / %s rw - hugetlbfs hugetlbfs rw" % huge, "15 1 0:12 / %s rw - mqueue mqueue rw" % mq]
            mi = os.path.join(d, "mountinfo")
            with open(mi, "wb") as f:
                f.write(("\n".join(lines) + "\n").encode() + b"16 1 8:5 / /srv/\xff rw - ext4 /dev/sde rw\n")
            script = host.PROBE_PS1.replace("@IPC@", "@()")
            enc = base64.b64encode(script.encode("utf-16-le")).decode()
            r = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
                               input=json.dumps({"ro": [], "jobpid": 1, "mountinfo": mi}).encode(), stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=120)
            got = json.loads(r.stdout.decode().strip().splitlines()[-1])["rwmounts"]
            got = [got] if isinstance(got, str) else got
            self.assertEqual(sorted(got), sorted(["/", fil, "/dev/pts", "/sys", "/proc/sys", sp, huge, mq, "/srv/\ufffd"]))
            with open(mi, "ab") as f:
                f.write(b"17 1 8:6 / /x rw ext4 no-separator\n")                # a line not understood: refused
            r = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
                               input=json.dumps({"ro": [], "jobpid": 1, "mountinfo": mi}).encode(), stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=120)
            got = json.loads(r.stdout.decode().strip().splitlines()[-1])["rwmounts"]
            self.assertEqual(([got] if isinstance(got, str) else got)[0], "(a mount table line not understood)")
            r = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
                               input=json.dumps({"ro": [], "jobpid": 1, "mountinfo": os.path.join(d, "none")}).encode(),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
            got = json.loads(r.stdout.decode().strip().splitlines()[-1])["rwmounts"]
            self.assertEqual([got] if isinstance(got, str) else got, ["(the mount table could not be read)"])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_automounts_fired_within_one_budget(self):
        # 1.76.1g: each touch a process with a hard deadline, never waited on (a stat stuck on a dead NFS server cannot
        # hold the job); the indirect map not touched; what is not mounted after: not tried
        state = {"n": 0, "killed": [], "started": []}
        real_popen, real_m = host.subprocess.Popen, host.mounts

        def mounts():
            state["n"] += 1
            base = [("/", "ext4", "rw"), ("/opt", "autofs", "rw,direct"), ("/net", "autofs", "rw,indirect"), ("/slow", "autofs", "rw,direct")]
            return base + ([("/opt", "ext4", "rw")] if state["n"] > 1 else [])          # /opt mounts; /slow never does

        class P(object):
            def __init__(self, cmd, **k):
                self.cmd = cmd
                state["started"].append(cmd[2])

            def poll(self):
                return 0 if self.cmd[2] == "/opt/." else None                         # /slow hangs

            def kill(self):
                state["killed"].append(self.cmd[2])

            def wait(self, *a, **k):
                raise AssertionError("never waited on")
        host.mounts = mounts
        host.subprocess.Popen = P
        t = time.time()
        try:
            self.assertEqual(host.fire_automounts(budget=1), ["/opt"])
            self.assertLess(time.time() - t, 3)
            self.assertEqual(state["started"], ["/opt/.", "/slow/."])
            self.assertEqual(state["killed"], ["/slow/."])
            self.assertEqual(sorted(host.AUTOFS_HIDDEN), ["/net", "/slow"])
            self.assertIn("automount the sandbox cannot show", host.blind("", [], [{"cmd": "Get-Content", "path": "/net/srv/x", "literal": True}]))
        finally:
            host.subprocess.Popen, host.mounts = real_popen, real_m
            del host.AUTOFS_HIDDEN[:]

    @unittest.skipUnless(shutil.which("pwsh"), "no pwsh here")
    def test_writable_means_a_write_that_works(self):
        # 1.76.1h (CI on Ubuntu 26.04): never test -w (the Rust coreutils read permission bits: a 777 folder on a
        # read-only mount was "writable"); a new file of a random name is tried and removed, a file opened for writing
        import base64
        import subprocess
        self.assertNotIn("/usr/bin/test", host.PROBE_PS1)
        d = tempfile.mkdtemp(dir=os.path.expanduser("~"), prefix=".site-ak-w-")
        try:
            wd, rd, fil = os.path.join(d, "w"), os.path.join(d, "r"), os.path.join(d, "f")
            os.makedirs(wd)
            os.makedirs(rd)
            with open(fil, "w") as f:
                f.write("x")
            os.chmod(rd, 0o555)
            enc = base64.b64encode(host.PROBE_PS1.replace("@IPC@", "@()").encode("utf-16-le")).decode()
            r = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
                               input=json.dumps({"ro": [wd, rd, fil, os.path.join(d, "none")], "jobpid": 1}).encode(),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
            got = json.loads(r.stdout.decode().strip().splitlines()[-1])["writable"]
            got = [got] if isinstance(got, str) else got
            self.assertEqual(sorted(got), sorted([wd, fil] + ([rd] if os.geteuid() == 0 else [])))
            self.assertEqual(os.listdir(wd), [])                               # the file it made is gone
            with open(fil) as f:
                self.assertEqual(f.read(), "x")                                # nothing written
        finally:
            os.chmod(os.path.join(d, "r"), 0o755)
            shutil.rmtree(d, ignore_errors=True)

    def test_a_mount_table_not_read_is_no_trial(self):
        # 1.76.1h: refresh_autofs and run_trial: a MountsError is a refusal, said - never an old list, never a traceback
        real_m, real_cmd, real_id = host.mounts, host.sandbox_cmd, host.PWSH_ID

        def boom(*a, **k):
            raise host.MountsError("the mount table could not be read")
        host.mounts = boom
        host.sandbox_cmd = boom
        host.AUTOFS_HIDDEN[:] = ["/old"]
        try:
            self.assertFalse(host.refresh_autofs())
            host.PWSH_ID = None
            r, err, _ = host.run_trial("/usr/bin/pwsh", "/usr/bin/systemd-run", "1", 5, version=255)
            self.assertIsNone(r)
            self.assertTrue(err.startswith("no sandbox (the mount table could not be read)"), err)
        finally:
            host.mounts, host.sandbox_cmd, host.PWSH_ID = real_m, real_cmd, real_id
            del host.AUTOFS_HIDDEN[:]

    def test_refresh_before_each_code(self):
        # an automount that expired after the probe: not tried for the next code (the list is the mount table now)
        real_m = host.mounts
        host.mounts = lambda: [("/", "ext4", "rw"), ("/opt", "autofs", "rw,direct")]
        try:
            host.refresh_autofs()
            self.assertEqual(host.AUTOFS_HIDDEN, ["/opt"])
            host.mounts = lambda: [("/", "ext4", "rw"), ("/opt", "autofs", "rw,direct"), ("/opt", "ext4", "rw")]
            host.refresh_autofs()
            self.assertEqual(host.AUTOFS_HIDDEN, [])
        finally:
            host.mounts = real_m
            del host.AUTOFS_HIDDEN[:]


class PathsMoved(unittest.TestCase):
    """1.76.1b: the realpaths a code reads, before and after its trial - a link moved in between: the result is dropped."""
    def test_a_link_that_moves(self):
        d = tempfile.mkdtemp(dir=os.path.expanduser("~"), prefix=".site-ak-move-")
        try:
            os.makedirs(os.path.join(d, "a"))
            os.makedirs(os.path.join(d, "b"))
            ln = os.path.join(d, "now")
            os.symlink(os.path.join(d, "a"), ln)
            paths = [{"cmd": "Get-Content", "path": ln + "/x", "literal": True}, {"cmd": "Test-Path", "path": "$p", "literal": False}]
            calls = [["/usr/bin/stat", "-c", "%a", ln + "/y"], ["/usr/sbin/sysctl", "-n", "kernel.x"]]
            before = host.path_snapshot(paths, calls)
            self.assertEqual(sorted(before), sorted([ln + "/x", ln + "/y"]))
            self.assertEqual(host.path_moved(before, host.path_snapshot(paths, calls)), [])
            os.remove(ln)
            os.symlink(os.path.join(d, "b"), ln)
            moved = host.path_moved(before, host.path_snapshot(paths, calls))
            self.assertEqual(len(moved), 2)
            self.assertIn("%s/x: %s/a/x -> %s/b/x" % (ln, d, d), moved)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        picks = [dict(v="V-1", sv="SV-1", title="Owner", ok=True, expected="Open", check_text=T_OWNER, es="Open")]
        dr = a.stig_answer_drafts(picks, {}, False)
        r = a.stig_answer_rows(picks, dr, {"gate": {"V-1": {"ok": True, "used": []}},
                                           "trial": {"V-1": {"ran": False, "error": "a path changed during the trial (/x: /a -> /b)"}}}, "DEFAULT")[0]
        self.assertEqual(r["why"], "Not drafted - not tried: a path changed during the trial (/x: /a -> /b).")


class TrialOff(unittest.TestCase):
    """1.76.4: answer_keys_trial false (the default) - the read-only check only: no probe, no systemd-run, no automount
    fired; each block that passed the check is offered, marked not tried; true: as before."""

    @unittest.skipUnless(shutil.which("pwsh"), "no pwsh here")
    def test_the_module_runs_no_sandbox(self):
        def never(*a, **k):
            raise AssertionError("the sandbox was touched with the trial off")
        got = {}

        class Module(object):                   # AnsibleModule as the module uses it (its arguments' defaults applied)
            check_mode = False

            def __init__(self, argument_spec=None, **k):
                self.params = dict((n, o.get("default")) for n, o in argument_spec.items())
                self.params.update(pwsh=shutil.which("pwsh"), trial=False, codes=[
                    {"id": "V-1", "code": "$o = @(Get-Content -LiteralPath /etc/hostname); return ($o.Count -gt 0)"},
                    {"id": "V-2", "code": "Remove-Item -LiteralPath /etc/hostname; return $true"}])

            def exit_json(self, **r):
                got.update(r)
                raise SystemExit(0)

            def fail_json(self, **r):
                got.update(r, failed=True)
                raise SystemExit(1)
        names = ("find_sandbox", "sandbox_probe", "fire_automounts", "systemd_version", "run_trial", "refresh_autofs", "sandbox_cmd")
        saved = dict((n, getattr(host, n)) for n in names + ("AnsibleModule",))
        for n in names:
            setattr(host, n, never)
        host.AnsibleModule = Module
        try:
            with self.assertRaises(SystemExit):
                host.main()
        finally:
            for n, f in saved.items():
                setattr(host, n, f)
        self.assertFalse(got.get("failed"), got)
        self.assertTrue(got["gate"]["V-1"]["ok"], got["gate"]["V-1"])
        self.assertEqual(got["trial"]["V-1"], {"ran": False, "off": True, "ok": False, "valid": None, "read": "", "seconds": 0,
                                               "error": "the trial is off (answer_keys_trial)"})
        self.assertFalse(got["gate"]["V-2"]["ok"])                      # the check still refuses a write
        self.assertNotIn("V-2", got.get("trial") or {})

    def rows(self, trial):
        picks = [dict(v="V-1", sv="SV-1", title="Owner", ok=True, expected="Open", check_text=T_OWNER, es="Open")]
        dr = a.stig_answer_drafts(picks, {}, False)
        return a.stig_answer_rows(picks, dr, {"gate": {"V-1": {"ok": True, "used": []}}, "trial": {"V-1": trial}}, "DEFAULT")

    def test_offered_marked_not_tried(self):
        r = self.rows({"ran": False, "off": True, "error": "the trial is off (answer_keys_trial)"})[0]
        self.assertTrue(r["untried"])
        self.assertIn("<!-- not tried: the trial is off (answer_keys_trial) - read it before you use it, try it on a test host -->",
                      r["block"])
        self.assertIn("<ExpectedStatus>Open</ExpectedStatus>", r["block"])                 # as drafted
        doc = a.stig_answer_report([r], "h1", "DEFAULT")
        tile = [t for t in doc["web"]["summary"] if t["label"] == "Trial"][0]
        self.assertEqual((tile["value"], tile["status"]), ("off", "warning"))
        sec = doc["web"]["sections"][0]
        self.assertIn("The trial is off (answer_keys_trial): this block was not tried on a host", sec["text"])
        self.assertIn("not tried (off)", sec["rows"][0])
        self.assertIn("(not tried - the trial is off)", sec["details"][0][0]["texts"][0]["label"])
        self.assertEqual(doc["xlsx"][0]["rows"][0][2], "not tried")
        self.assertTrue(any(x.startswith("The trial is off (answer_keys_trial false, the default)") for x in doc["web"]["sections"][-1]["lines"]))
        self.assertIn("read-only check on h1", doc["web"]["subtitle"])
        self.assertIn("V-1 ready (not tried):", "\n".join(doc["lines"]))
        multi = a.stig_answer_report_multi([{"label": "RHEL 9 V2R10", "hosts": ["h1"], "rep": "h1", "rows": [r]}], "DEFAULT")
        self.assertEqual([t["value"] for t in multi["web"]["summary"] if t["label"] == "Trial"], ["off"])
        self.assertEqual(multi["xlsx"][0]["rows"][0][4], "not tried")
        self.assertIn("RHEL 9 V2R10 (1 host): h1; read-only check (the trial is off) on h1.", multi["web"]["sections"][-1]["lines"])

    def test_the_setting_decides_not_the_rows(self):
        # 1.76.4 (Codex): every draft refused with the trial off - no row "not tried", the report still says off
        picks = [dict(v="V-1", sv="SV-1", title="Owner", ok=True, expected="Open", check_text=T_OWNER, es="Open")]
        dr = a.stig_answer_drafts(picks, {}, False)
        r = a.stig_answer_rows(picks, dr, {"gate": {"V-1": {"ok": False, "why": ["a write"], "used": []}}}, "DEFAULT")[0]
        doc = a.stig_answer_report([r], "h1", "DEFAULT", {"pwsh": "/usr/bin/pwsh"}, trial_on=False)
        self.assertEqual([(t["value"], t["note"]) for t in doc["web"]["summary"] if t["label"] == "Trial"], [("off", "answer_keys_trial")])
        how = doc["web"]["sections"][-1]["lines"]
        self.assertIn("The read-only check ran with /usr/bin/pwsh.", how)
        self.assertFalse([x for x in how if "and the trial ran" in x or x.startswith("The trial ran")])
        multi = a.stig_answer_report_multi([{"label": "RHEL 9 V2R10", "hosts": ["h1"], "rep": "h1", "rows": [r]}], "DEFAULT", trial_on=False)
        self.assertEqual([t["value"] for t in multi["web"]["summary"] if t["label"] == "Trial"], ["off"])
        self.assertIn("RHEL 9 V2R10 (1 host): h1; read-only check (the trial is off) on h1.", multi["web"]["sections"][-1]["lines"])

    def test_each_stig_its_own_setting(self):
        # 1.76.4 (Codex): one STIG's host with the trial on, another's off - each group says its own, the tile "off for 1 of 2"
        on = self.rows({"ran": True, "ok": True, "valid": True, "read": "x", "seconds": 1})[0]
        off = self.rows({"ran": False, "off": True, "error": "the trial is off (answer_keys_trial)"})[0]
        m = a.stig_answer_report_multi([{"label": "RHEL 9 V2R10", "hosts": ["h9"], "rep": "h9", "rows": [on], "trial_on": True},
                                        {"label": "RHEL 8 V2R9", "hosts": ["h8"], "rep": "h8", "rows": [off], "trial_on": False}],
                                       "DEFAULT", trial_on=False)
        how = m["web"]["sections"][-1]["lines"]
        self.assertIn("RHEL 9 V2R10 (1 host): h9; trial and read-only check on h9.", how)
        self.assertIn("RHEL 8 V2R9 (1 host): h8; read-only check (the trial is off) on h8.", how)
        self.assertEqual([t["value"] for t in m["web"]["summary"] if t["label"] == "Trial"], ["off for 1 of 2 STIGs"])
        # each STIG's own trial paragraph under its label, never in the shared text (Codex)
        self.assertTrue([x for x in how if x.startswith("RHEL 8 V2R9: The trial is off")])
        self.assertTrue([x for x in how if x.startswith("RHEL 9 V2R10: The trial ran each code")])
        self.assertFalse([x for x in how if x.startswith(("The trial is off", "The trial ran each code"))])

    def test_on_as_before(self):
        r = self.rows({"ran": True, "ok": True, "valid": True, "read": "x", "seconds": 1})[0]
        self.assertFalse(r.get("untried"))
        self.assertNotIn("not tried", r["block"])
        doc = a.stig_answer_report([r], "h1", "DEFAULT")
        self.assertEqual([t["value"] for t in doc["web"]["summary"] if t["label"] == "Trial"], ["1 true, 0 false"])
        self.assertEqual(self.rows({"ran": False, "error": "no sandbox (x)"})[0]["why"], "Not drafted - not tried: no sandbox (x).")


class EvaluateWider(unittest.TestCase):
    """1.76.0 (a real host: Evaluate-STIG 1.2610.1, no template found; the compare only in its GUI script): the template
    deeper or as a schema; the compare in the engine first - a GUI script is never proof for the command line runs."""
    def test_schema_and_gui(self):
        d = tempfile.mkdtemp()
        try:
            sch = os.path.join(d, "Src", "Evaluate-STIG", "Modules", "Master_Functions", "Schema")
            os.makedirs(sch)
            with open(os.path.join(sch, "AnswerFileSchema.xsd"), "w") as f:
                f.write('<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="Vuln"><xs:complexType><xs:sequence>'
                        '<xs:element name="AnswerKey"><xs:complexType><xs:sequence><xs:element name="ExpectedStatus"/>'
                        '<xs:element name="ValidationCode"/><xs:element name="ValidTrueStatus"/><xs:element name="ValidTrueComment"/>'
                        '<xs:element name="ValidFalseStatus"/><xs:element name="ValidFalseComment"/></xs:sequence></xs:complexType>'
                        '</xs:element></xs:sequence></xs:complexType></xs:element></xs:schema>')
            path, names = host.template_names(d)
            self.assertTrue(path.endswith("AnswerFileSchema.xsd"))
            self.assertEqual(names, ["ExpectedStatus", "ValidationCode", "ValidTrueStatus", "ValidTrueComment", "ValidFalseStatus", "ValidFalseComment"])
            with open(os.path.join(d, "Src", "Evaluate-STIG", "Evaluate-STIG_GUI.ps1"), "w") as f:
                f.write("x\nif ($a.ExpectedStatus -eq $s) { }\n")
            e = host.expected_check(d)
            self.assertEqual((e["found"], e["where"]), (False, "Src/Evaluate-STIG/Evaluate-STIG_GUI.ps1:2"))
            self.assertIn("only in the GUI script", e["note"])
            with open(os.path.join(d, "Src", "Evaluate-STIG", "Modules", "Master_Functions", "Master_Functions.psm1"), "w") as f:
                f.write("\nif ($Answer.ExpectedStatus -eq $Status) { }\n")
            self.assertEqual(host.expected_check(d), {"found": True, "where": "Src/Evaluate-STIG/Modules/Master_Functions/Master_Functions.psm1:2", "note": ""})
        finally:
            shutil.rmtree(d, ignore_errors=True)


class AnswerFileXml(unittest.TestCase):
    """1.79.4: one whole Evaluate-STIG answer file per STIG (benchmark and release) - its Name, its name, it parses."""

    @staticmethod
    def row(v, code="return $true", act=False, untried=False, key="SITEA"):
        blk = a.stig_answer_block(key, "Not_Reviewed", code, ("NotAFinding", "ok"), ("Open", "not ok"), act=act, untried=untried)
        return {"v": v, "sv": "S" + v, "title": "t", "block": blk, "by": "ACT" if act else "pattern", "untried": untried}

    def group(self, bench, rev, rows, select=None, major=None):
        m = __import__("re").match(r"^RHEL_(\d+)_STIG$", bench)
        return {"label": a.stig_label(bench, rev), "bench": bench, "rev": rev, "hosts": ["h1"], "rep": "h1", "rows": rows,
                "select": select or {}, "major": (m.group(1) if m else "") if major is None else major}   # the host's, as the job gives it

    def parse(self, f):
        import xml.etree.ElementTree as ET
        root = ET.fromstring(f["xml"].encode("utf-8"))
        self.assertTrue(f["xml"].startswith('<?xml version="1.0" encoding="utf-8"?>\n'))
        self.assertEqual(root.tag, "STIGComments")
        return root

    def test_two_groups_two_files(self):
        gs = [self.group("RHEL_8_STIG", "V2R9", [self.row("V-230221")], {"8": "RHEL8"}),
              self.group("RHEL_9_STIG", "V2R10", [self.row("V-257777"), self.row("V-258999", act=True)], {9: "RHEL9"})]
        fs = a.stig_answer_xml_files(gs, "SITEA")
        self.assertEqual([f["filename"] for f in fs], ["answer-keys-RHEL_8_STIG-V2R9.xml", "answer-keys-RHEL_9_STIG-V2R10.xml"])
        r8, r9 = self.parse(fs[0]), self.parse(fs[1])
        self.assertEqual((r8.get("Name"), r9.get("Name")), ("RHEL8", "RHEL9"))   # the key as a string or a number
        self.assertEqual([v.get("ID") for v in r9], ["V-257777", "V-258999"])
        self.assertEqual([k.get("Name") for v in r9 for k in v], ["SITEA", "SITEA"])
        self.assertIn("drafted by ACT - read it before you use it", fs[1]["xml"])  # the ACT mark kept
        self.assertIn("from check_stig_evaluate_select", fs[1]["xml"])
        self.assertEqual(fs[1]["blocks"], 2)

    def test_one_bench_two_revs_two_files(self):
        gs = [self.group("RHEL_9_STIG", "V2R7", [self.row("V-257777", "Test-Path '/etc/hosts'")], {"9": "RHEL9"}),
              self.group("RHEL_9_STIG", "V2R10", [self.row("V-257777", "Test-Path '/etc/hostname'")], {"9": "RHEL9"}),
              self.group("RHEL_9_STIG", "V2R11", [self.row("V-257777", act=True)], {"9": "RHEL9"})]
        gs[2]["rows"][0]["block"] = ""                                             # no ready block: no file
        fs = a.stig_answer_xml_files(gs, "SITEA")
        self.assertEqual([f["filename"] for f in fs], ["answer-keys-RHEL_9_STIG-V2R7.xml", "answer-keys-RHEL_9_STIG-V2R10.xml"])
        self.assertIn("/etc/hosts'", fs[0]["xml"])
        self.assertNotIn("/etc/hostname", fs[0]["xml"])
        self.assertIn("/etc/hostname", fs[1]["xml"])

    def test_the_name_from_a_site_file(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "RHEL9_SITEA.xml"), "w") as f:
                f.write('<?xml version="1.0"?>\n<STIGComments Name="RHEL9-site"><Vuln ID="V-257777"><AnswerKey Name="SITEA"/></Vuln>'
                        '<Vuln ID="V-258000"/></STIGComments>\n')
            with open(os.path.join(d, "RHEL8.xml"), "w") as f:
                f.write('<STIGComments Name="RHEL8-site"><Vuln ID="V-230221"/></STIGComments>')
            with open(os.path.join(d, "broken.xml"), "w") as f:
                f.write("<STIGComments Name=")
            with open(os.path.join(d, "other.xml"), "w") as f:
                f.write('<Checklist Name="x"><Vuln ID="V-257777"/></Checklist>')
            site = a.stig_answer_file_stigs(d)
        finally:
            shutil.rmtree(d)
        self.assertEqual([(x["file"], x["name"]) for x in site], [("RHEL8.xml", "RHEL8-site"), ("RHEL9_SITEA.xml", "RHEL9-site")])
        # the site's own file wins over check_stig_evaluate_select; a rule already answered (left out) still counts
        g = self.group("RHEL_9_STIG", "V2R10", [self.row("V-258999")], {"9": "RHEL9"})
        f = a.stig_answer_xml_files([g], "SITEA", site, {"RHEL 9 V2R10": ["V-257777"]})[0]
        self.assertEqual((self.parse(f).get("Name"), f["known"]), ("RHEL9-site", True))
        self.assertIn("STIGComments Name: from your answer file RHEL9_SITEA.xml.", f["xml"])
        # two files of the site with other Names and as many of its rules: neither - then the setting
        two = site + [{"file": "RHEL9_B.xml", "name": "RHEL9-b", "vulns": ["V-257777"]}]
        f2 = a.stig_answer_xml_files([g], "SITEA", two, {"RHEL 9 V2R10": ["V-257777"]})[0]
        self.assertEqual(f2["name"], "RHEL9")
        # the host's own RHEL version picks check_stig_evaluate_select's name, as for the scan
        f4 = a.stig_answer_xml_files([dict(g, select={"8": "RHEL8", "9": "RHEL9"}, major="8")], "SITEA", [], {})[0]
        self.assertEqual(f4["name"], "RHEL8")
        f3 = a.stig_answer_xml_files([dict(g, select={})], "SITEA", two, {"RHEL 9 V2R10": ["V-257777"]})[0]
        self.assertFalse(f3["known"])
        self.assertIn("your answer files name it two ways", f3["name_from"])

    def test_the_name_fallback_said(self):
        g = self.group("RHEL_10_STIG", "V1R3", [self.row("V-281111")])
        f = a.stig_answer_xml_files([g], "SITEA")[0]
        self.assertEqual((self.parse(f).get("Name"), f["known"]), ("RHEL_10_STIG", False))
        self.assertIn("<!-- CHECK THE NAME: RHEL_10_STIG is not known - the benchmark id;", f["xml"])
        doc = a.stig_answer_report_multi([g], "SITEA", False)
        sec = [s for s in doc["web"]["sections"] if s["title"] == "RHEL 10 V1R3 - the answer file (1 block)"][0]
        self.assertEqual(sec["status"], "warning")
        self.assertIn("CHECK IT", sec["text"])
        mail = [s for s in doc["report"]["sections"] if s["title"] == "Answer files attached (1)"][0]
        self.assertEqual(mail["status"], "warning")
        self.assertIn("RHEL_10_STIG - CHECK IT", mail["rows"][0][2])
        self.assertTrue(any("CHECK THE NAME" in x for x in doc["lines"]))

    def test_codex_round(self):
        """1.79.4 (Codex): a Name that is not a plain short name never used nor shown; rule ids V-<digits> only; an
        encoding Python cannot read skipped; no host version - no name from the setting; a comment of XML characters
        only; a file that would not parse never offered, said."""
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "a.xml"), "w") as f:
                f.write('<STIGComments Name="password=hunter2xyz"><Vuln ID="V-257777"/><Vuln ID="x y"/></STIGComments>')
            with open(os.path.join(d, "b.xml"), "wb") as f:
                f.write(b'<?xml version="1.0" encoding="x-no-such"?><STIGComments Name="B"><Vuln ID="V-1"/></STIGComments>')
            with open(os.path.join(d, "c.xml"), "w") as f:
                f.write('<STIGComments Name="RHEL9"><Vuln ID="V-258999"/><Vuln ID="V-1 or 1"/></STIGComments>')
            site = a.stig_answer_file_stigs(d)
        finally:
            shutil.rmtree(d)
        self.assertEqual([(x["file"], x["name"], x["rejected"], x["vulns"]) for x in site],
                         [("a.xml", "", True, ["V-257777"]), ("c.xml", "RHEL9", False, ["V-258999"])])
        g = self.group("RHEL_9_STIG", "V2R10", [self.row("V-257777")], {})
        f = a.stig_answer_xml_files([g], "SITEA", site)[0]
        self.assertFalse(f["known"])
        self.assertIn("1 of your answer files for it has a Name that is not a plain short name (not shown)", f["name_from"])
        self.assertNotIn("hunter2", f["xml"] + f["name_from"])
        # no host version known: the setting is not used, the benchmark id said
        f2 = a.stig_answer_xml_files([dict(g, select={"9": "RHEL9"}, major="")], "SITEA")[0]
        self.assertEqual((f2["name"], f2["known"]), ("RHEL_9_STIG", False))
        # a file name with a form feed in the comment: still parses
        site2 = [{"file": "RHEL9\x0cx.xml", "name": "RHEL9", "rejected": False, "vulns": ["V-257777"]}]
        f3 = a.stig_answer_xml_files([g], "SITEA", site2)[0]
        self.assertEqual(f3["error"], "")
        self.parse(f3)
        # a code with a character XML does not allow: no file, said
        bad = self.row("V-257777", "return $true \x01")
        doc = a.stig_answer_report_multi([self.group("RHEL_9_STIG", "V2R10", [bad], {"9": "RHEL9"})], "SITEA", False)
        self.assertEqual(doc["files"], [])
        self.assertIn("RHEL 9 V2R10 - no answer file", [x["title"] for x in doc["web"]["sections"]])
        self.assertTrue(any(x.startswith("  NO answer file answer-keys-RHEL_9_STIG-V2R10.xml: it does not parse") for x in doc["lines"]))

    def test_xml_escaping(self):
        code = 'if ($a -lt 2 -and "x" -ne \'<&>\') { return $true }\n$h = @"\nline\n"@\nreturn $false'
        r = self.row("V-257777", code, untried=True, key='K"<&')
        g = self.group("RHEL_9_STIG", "V2R10", [r], {"9": 'RH"EL<9&'})
        f = a.stig_answer_xml_files([g], 'K"<&')[0]
        root = self.parse(f)
        self.assertEqual(root.get("Name"), 'RH"EL<9&')
        ak = root[0][0]
        self.assertEqual(ak.get("Name"), 'K"<&')
        import xml.etree.ElementTree as ET
        # 1.79.4 (Codex P1): the code as the block has it, to the character - its closing line's spaces too
        self.assertEqual(ak.find("ValidationCode").text, ET.fromstring(r["block"]).find("ValidationCode").text)
        self.assertEqual(ak.find("ValidationCode").text.strip(), code)
        self.assertIn('\n"@\n', f["xml"])                                       # "@ still starts its line
        self.assertIn("not tried: the trial is off", f["xml"])                     # the trial-off mark kept
        # a file name of safe characters only; a comment never holds "--"
        g2 = self.group("A STIG/../x--y", "V1 R2", [self.row("V-1")])
        f2 = a.stig_answer_xml_files([g2], "SITEA")[0]
        self.assertEqual(f2["filename"], "answer-keys-A_STIG_.._x--y-V1_R2.xml")
        self.parse(f2)
        for c in __import__("re").findall(r"<!--(.*?)-->", f2["xml"], __import__("re").S):
            self.assertNotIn("--", c)

    def test_the_email_attachments(self):
        """the report task's _report_attachments: one .xml per STIG from the job's own folder - never a zip."""
        import jinja2
        import yaml
        with open(os.path.join(REPO, "roles", "check_stig", "tasks", "answer_keys_report.yml")) as fh:
            tasks = yaml.safe_load(fh)
        blk = [t for t in tasks if "block" in t and any(k.endswith("include_role") for x in t["block"] for k in x)][0]
        expr = [x for x in blk["block"] if any(k.endswith("include_role") for k in x)][0]["vars"]["_report_attachments"]
        self.assertTrue(any(k.endswith("copy") for k in blk["block"][0]))         # 1.79.4 (Codex): the writes inside the block
        files = [{"filename": "answer-keys-RHEL_8_STIG-V2R9.xml"}, {"filename": "answer-keys-RHEL_9_STIG-V2R10.xml"}]
        out = jinja2.Environment().from_string(expr).render(_ak_doc={"files": files}, _ak_xml_dir={"path": "/tmp/x"}).strip()
        self.assertEqual(eval(out), [{"path": "/tmp/x/answer-keys-RHEL_8_STIG-V2R9.xml", "filename": "answer-keys-RHEL_8_STIG-V2R9.xml"},
                                     {"path": "/tmp/x/answer-keys-RHEL_9_STIG-V2R10.xml", "filename": "answer-keys-RHEL_9_STIG-V2R10.xml"}])
        self.assertEqual(jinja2.Environment().from_string(expr).render(_ak_doc={"files": files}, _ak_xml_dir={}).strip(), "[]")
        self.assertIn("always", blk)                                                # the folder removed after, whatever happens
        doc = a.stig_answer_report_multi([self.group("RHEL_9_STIG", "V2R10", [self.row("V-257777")], {"9": "RHEL9"})], "SITEA", False)
        self.assertEqual([f["filename"] for f in doc["files"]], ["answer-keys-RHEL_9_STIG-V2R10.xml"])
        self.assertIn("  answer file answer-keys-RHEL_9_STIG-V2R10.xml: STIGComments Name RHEL9 (check_stig_evaluate_select "
                      "(the name this run gave Evaluate-STIG))", doc["lines"])


if __name__ == "__main__":
    unittest.main()
