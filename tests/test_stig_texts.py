#!/usr/bin/env python3
"""1.73.0: a STIG rule's check text and fix text in every report that lists STIG rules (plugins/filter/stig_text_filters.py),
and what Copy copies: the command lines only - a text with no command has no Copy button, one muted line instead.
  python tests/test_stig_texts.py"""
from __future__ import absolute_import, division, print_function

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
sys.path.insert(0, os.path.join(HERE, "stig"))
sys.path.insert(0, HERE)
import stig_text_filters as t  # noqa: E402
import site_filters as sf  # noqa: E402
import stig_filters  # noqa: E402
import disa_texts  # noqa: E402
sys.path.insert(0, os.path.join(REPO, "roles", "check_stig", "library"))
import ansible.module_utils  # noqa: E402 - the repository's module_utils as Ansible gives them (ansible.cfg)
ansible.module_utils.__path__.append(os.path.join(REPO, "plugins", "module_utils"))
import site_stigman_read as smr  # noqa: E402
import site_stig_scan as scan  # noqa: E402

LOOKS = ("report_atlas.html.j2", "report_brief.html.j2", "report_ops.html.j2")
OTHER = ("report.html.j2", "report_command.html.j2", "report_portal.html.j2", "report_glass.html.j2")


class RealTexts(unittest.TestCase):
    """DISA's own texts (RHEL 8 and 9, Windows Server 2019): Copy copies the commands, never the prose, a file's lines or
    the output the check shows."""
    def test_each_copies_its_commands_only(self):
        for stig, rule, which, text, want in disa_texts.TEXTS:
            with self.subTest(rule=rule, which=which):
                b = t.text_block({which: text})["texts"][0]
                self.assertEqual(b["cmd"], want)
                self.assertEqual(b["button"], ("Copy check" if which == "check" else "Copy fix") if want else "")
                self.assertEqual(b["note"], "" if want else "No command in this text")

    def test_a_copied_line_is_in_the_text(self):
        """Every line copied is a line of the text (its prompt or the quotes around it left off) - nothing made up but a
        Windows check's reg query, from its Registry Hive, Path and Value Name."""
        for stig, rule, which, text, want in disa_texts.TEXTS:
            for line in [x for x in want.split("\n") if x]:
                if line.startswith("reg query "):
                    key = re.match(r'reg query "HKEY_LOCAL_MACHINE\\(.*?)" /v (\S+)$', line)
                    self.assertTrue(key, line)
                    self.assertIn(key.group(1), text.replace("Registry Path: \\", "Registry Path: "))
                    self.assertIn("Value Name: " + key.group(2), text)
                else:
                    self.assertIn(line, text, rule)

    def test_what_is_never_copied(self):
        got = dict(((r, w), t.commands_of(x, registry=w == "check")) for s, r, w, x, c in disa_texts.TEXTS)
        self.assertNotIn("root /etc/group-", got[("SV-257900", "check")])                      # the output it shows
        self.assertNotIn("/dev/mapper", got[("SV-257844", "check")])
        self.assertEqual(got[("SV-257805", "fix")], "")                                       # a file's lines
        self.assertEqual(got[("SV-258078", "check")], "getenforce")                           # and the file it shows
        self.assertEqual(got[("SV-205702", "check")], "")                                     # a window to open
        self.assertEqual(got[("SV-205849", "check")], "")                                     # Enter "winver.exe": a window
        self.assertEqual(got[("SV-271428", "fix")], "")                                       # a fix's registry value
        self.assertNotIn("$lastLogin", got[("SV-205707", "check")])                           # a script over lines: not a command line

    def test_a_reload_after_each_change_stays(self):
        cmd = dict(((r, w), c) for s, r, w, x, c in disa_texts.TEXTS)[("SV-230504", "fix")].split("\n")
        self.assertEqual(cmd.count("sudo firewall-cmd --reload"), 2)


class Commands(unittest.TestCase):
    def test_windows_quotes(self):
        text = "\n".join(['Open "PowerShell".', 'Enter "Get-ADUser -Filter * | FT Name".', 'Run "gpedit.msc".', 'Run "MMC".',
                          'Run "Net user [username]", where [username] is the account.', '"icacls c:\\"',
                          'Type "q" to quit.', 'Enter "ntdsutil".'])
        self.assertEqual(t.commands_of(text).split("\n"), ["Get-ADUser -Filter * | FT Name", "Net user [username]", "icacls c:\\"])

    def test_registry(self):
        text = "\n".join(["Registry Hive: HKEY_LOCAL_MACHINE", "Registry Path: \\SOFTWARE\\Policies\\X\\", "", "Value Name: A",
                          "Value Name: Two Words", "Registry Path: \\SOFTWARE\\Y\\", "", "Value Type: REG_DWORD"])
        self.assertEqual(t.commands_of(text).split("\n"), ['reg query "HKEY_LOCAL_MACHINE\\SOFTWARE\\Policies\\X" /v A',
                                                           'reg query "HKEY_LOCAL_MACHINE\\SOFTWARE\\Policies\\X" /v "Two Words"',
                                                           'reg query "HKEY_LOCAL_MACHINE\\SOFTWARE\\Y"'])
        self.assertEqual(t.commands_of(text, registry=False), "")

    def test_after_the_following_command(self):
        text = "On server core, run the following command:\n\nSecedit /Export /Areas User_Rights /cfg c:\\path\\f.txt\n\nThen look."
        self.assertEqual(t.commands_of(text), "Secedit /Export /Areas User_Rights /cfg c:\\path\\f.txt")
        self.assertEqual(t.commands_of("Edit the file with the following command:\n\nAdd the line below."), "")

    def test_the_scorecard_uses_the_same(self):
        import stig_scorecard_filters as f
        for s, r, w, x, c in disa_texts.TEXTS:
            self.assertEqual(f._text_block({w: x}), t.text_block({w: x}))


def result(host="rhel01", lists=None, bench="RHEL_9_STIG", rev="V2R7", status="found"):
    """A host's STIG drift result as stig_compare makes it: lists {"drift": [items], "new": [items]} - a drifted rule has a
    Not a Finding in STIG Manager, a new one none; each passed in the scan before."""
    its, reviews = [], {}
    for kind, rows in (lists or {}).items():
        for it in rows:
            its.append(dict(it, result="fail"))
            if kind in ("drift", "disagree"):
                reviews[it["key"]] = {"result": "pass", "status": "accepted", "ts": "2026-10-01"}
    before = dict((it["key"], "2026-11-01") for it in its if not (lists or {}).get("disagree"))
    scan = {"scanned": True, "os": "RHEL 9", "benchmark": "xccdf_org.ssgproject.content_benchmark_RHEL-9", "items": its,
            "previous": {"time": "2026-11-01T00:00:00Z", "items": dict((k, "pass") for k in before)}, "passed_before": before}
    sm = {"status": status, "why": "", "collection": "SITEA", "asset": host, "benchmark": bench if status == "found" else "",
          "revision": rev, "reviews": reviews if status == "found" else {}}
    return stig_filters.stig_compare(scan, sm, host, stigman_used=True, disagree_severity="warning")


def item(key, v=None, **kw):
    return dict({"key": key, "v": v or "V-" + key[3:], "stigid": "RHEL-09-0", "severity": "medium", "title": "Title " + key}, **kw)


TEXTS = {"SV-257900": {"check": "Verify:\n\n$ sudo stat -c \"%U %n\" /etc/group-\n\nroot /etc/group-", "fix": "Change the owner."},
         "SV-257844": {"check": "$ mount | grep /tmp", "fix": ""}}


class Reports(unittest.TestCase):
    def test_the_rules_to_read(self):
        res = [result(lists={"drift": [item("SV-257900")], "new": [item("SV-257844"), item("SV-257900")]}),
               result("rhel02", {"disagree": [item("SV-258078", check_text="c", fix_text="f")]}),       # Evaluate-STIG: its own
               result("rhel03", {"drift": [item("SV-1")]}, status="not_found")]                            # no STIG Manager id
        self.assertEqual(t.stig_text_wanted(res), [{"benchmark": "RHEL_9_STIG", "revision": "V2R7", "rules": ["SV-257844", "SV-257900"]}])
        got = t.stig_texts_of(res, {"SV-257900": {"check": "a", "fix": ""}, "SV-9": {"check": "", "fix": ""}})
        self.assertEqual(sorted(got), ["SV-257900", "SV-258078", "V-257900", "V-258078"])
        self.assertEqual(got["V-258078"], {"check": "c", "fix": "f"})

    def test_an_older_stig_keeps_its_own_pair(self):
        """RHEL 7's V-71849 is SV-86473: the group id the table shows finds the rule's text through the scan's own pair."""
        got = t.stig_texts_of([result(lists={"drift": [item("SV-86473", v="V-71849")]})], {"SV-86473": {"check": "x", "fix": ""}})
        self.assertIs(got["V-71849"], got["SV-86473"])
        self.assertNotIn("V-86473", got)

    def drift(self):
        res = [result(lists={"drift": [item("SV-257900")], "new": [item("SV-257844"), item("SV-258000")]}),
               result("rhel02", {"drift": [item("SV-257900")]})]
        texts = t.stig_texts_of(res, TEXTS)
        return stigma(res), texts

    def test_the_website(self):
        secs, texts = self.drift()
        web = t.stig_texts_sections(secs, texts, True)
        d = [s for s in web if s["title"].startswith("1. Drifted")][0]
        self.assertEqual(d["kinds"][0], "x")
        self.assertEqual(d["columns"][:3], ["", "Host", "STIG rule"])
        self.assertEqual([x["texts"][0]["cmd"] for x in d["details"]], ['sudo stat -c "%U %n" /etc/group-'] * 2)
        n = [s for s in web if s["title"].startswith("2. New")][0]
        self.assertIsNone(n["details"][1])                       # SV-258000: no text - no block, never an empty one
        self.assertEqual(n["details"][0]["texts"][0]["label"], "Check text")
        self.assertEqual(len(n["details"][0]["texts"]), 1)       # its fix text is empty: left off
        every = [s for s in web if s["title"].startswith("Every host")]
        self.assertTrue(every and "details" not in every[0])    # no rule column: as it was

    def test_the_email_each_rule_once(self):
        secs, texts = self.drift()
        mail = t.stig_texts_sections(secs, texts, False)
        d = [s for s in mail if s["title"].startswith("1. Drifted")][0]
        self.assertEqual(d["columns"][0], "Host")              # no open buttons in an email
        self.assertEqual([x["rule"] for x in d["rule_texts"]], ["V-257900 (RHEL-09-0)"])
        self.assertEqual(d["row_rule"], [0, 0])
        n = [s for s in mail if s["title"].startswith("2. New")][0]
        self.assertEqual(n["row_rule"], [0, -1])

    def test_no_texts_no_change(self):
        secs, texts = self.drift()
        self.assertEqual(t.stig_texts_sections(secs, {}, True), secs)
        self.assertEqual(t.stig_texts_sections(secs, {"SV-1": {"check": "x"}}, False), secs)

    def test_the_workbook_in_full(self):
        secs, texts = self.drift()
        long = dict(texts, **{"V-257900": {"check": "$ sudo x\n" + "y " * 3000, "fix": "z"}})
        mail = t.stig_texts_sections(secs, long, False)
        sheets = sf.report_xlsx_sheets({"sections": mail})
        d = [s for s in sheets if s["name"].startswith("1. Drifted")][0]
        self.assertEqual(d["columns"][-2:], ["Check text", "Fix text"])
        self.assertEqual(d["rows"][0][-2], long["V-257900"]["check"].strip())     # whole (its last space off)
        self.assertEqual(d["rows"][0][-1], "z")

    def test_the_text_email(self):
        secs, texts = self.drift()
        txt = sf.report_text({"title": "STIG drift report", "sections": t.stig_texts_sections(secs, texts, False)})
        self.assertIn("Check text and fix text (1 rule):", txt)
        self.assertIn('  Command: sudo stat -c "%U %n" /etc/group-', txt)
        self.assertIn("  No command in this text", txt)            # its fix text


def stigma(res):
    return stig_filters.stig_report_sections(res)


class Pages(unittest.TestCase):
    def env(self):
        from test_report_fit import EmailTiles
        return EmailTiles()._env()

    def doc(self, web):
        res = [result(lists={"drift": [item("SV-257900")], "new": [item("SV-257844")]})]
        return {"title": "STIG drift report", "status": "warning", "summary": [],
                "sections": t.stig_texts_sections(stigma(res), t.stig_texts_of(res, TEXTS), web)}

    def test_the_website_looks(self):
        for tpl in LOOKS:
            h = self.env().get_template(tpl).render(_report=self.doc(True), _email_footer="AAP job 9", report_email_header_text="x")
            self.assertIn('<button class="copy" type="button" hidden title="Copies the command" data-copy="sudo stat -c &#34;%U %n&#34; '
                          '/etc/group-">Copy check</button>', h.replace("&quot;", "&#34;"), tpl)
            self.assertRegex(h, r'<h3 class="rth">Fix text</h3><div class="code nc"><pre>Change the owner\.</pre></div>'
                                r'<p class="rtn">No command in this text</p>')
            self.assertIn('<button class="xb" type="button" aria-expanded="false"', h)
            self.assertIn("@media print{.rtx pre{max-height:none;overflow:visible}.rtn{display:none}}", h)

    def test_the_email_and_the_other_looks(self):
        for tpl in OTHER:
            for web in (False, True):
                v = {"_page_runs": []} if web else {}
                h = self.env().get_template(tpl).render(_report=self.doc(False), _email_footer="AAP job 9", report_email_header_text="x", **v)
                self.assertIn("Check text and fix text (1 rule)", h, tpl)
                self.assertIn("<summary", h)
                self.assertIn("Change the owner.", h)
                self.assertIn("No command in this text", h)
                self.assertNotIn('class="copy"', h)                  # no button where no script runs
                self.assertIn("/etc/group-</div>", h.replace("&#34;", '"').replace("&quot;", '"'))

    def test_the_email_keeps_the_first_part(self):
        long = {"V-257900": {"check": "$ sudo x\n" + "word " * 400, "fix": "f"}}
        sec = {"rule_texts": [{"rule": "V-257900", "title": "T", "texts": t.text_block(long["V-257900"])["texts"]}]}
        mail, web = sf.report_rule_texts_html(sec), sf.report_rule_texts_html(sec, web=True)
        self.assertIn("The whole text: the report website, the workbook.", mail)
        self.assertNotIn("The whole text", web)
        self.assertLess(len(mail), len(web))
        many = {"rule_texts": [{"rule": "V-%d" % i, "title": "", "texts": t.text_block({"fix": "x"})["texts"]} for i in range(45)]}
        self.assertIn("5 more rules: their texts are on the report website and in the workbook.", sf.report_rule_texts_html(many))
        self.assertNotIn("more rules", sf.report_rule_texts_html(many, web=True))


class Client(object):
    """STIG Manager's answers to GET /stigs/B/revisions/R/rules?projection=check&projection=fix."""
    def __init__(self, rules, fail=None):
        self.rules, self.fail, self.asked = rules, fail, []

    def get(self, path, **q):
        self.asked.append((path, q))
        if self.fail:
            raise RuntimeError(self.fail)
        return self.rules.get(path, [])


class ReadTexts(unittest.TestCase):
    def test_one_read_a_revision_the_rules_asked_kept(self):
        rows = [{"ruleId": "SV-257900r1_rule", "check": {"system": "C-1", "content": "Verify:\r\n\r\n\r\n$ sudo x  "}, "fix": {"fixref": "F", "text": "Fix it."}},
                {"ruleId": "SV-257901r1_rule", "check": {"content": "other"}, "fix": {"text": ""}},
                {"ruleId": "SV-257902r1_rule", "checks": [{"content": "a"}, {"content": "b"}], "fixes": []}]
        c = Client({"/stigs/RHEL_9_STIG/revisions/V2R7/rules": rows, "/stigs/RHEL_9_STIG/revisions/latest/rules": rows})
        out, info = smr.read_texts([{"benchmark": "RHEL_9_STIG", "revision": "V2R7", "rules": ["SV-257900", "SV-257902", "SV-9"]},
                                    {"benchmark": "RHEL_9_STIG", "revision": "", "rules": ["SV-257900"]}], c)
        self.assertEqual(out, {"SV-257900": {"check": "Verify:\n\n$ sudo x", "fix": "Fix it."}, "SV-257902": {"check": "a\n\nb", "fix": ""}})
        self.assertEqual(c.asked[0], ("/stigs/RHEL_9_STIG/revisions/V2R7/rules", {"projection": ["check", "fix"]}))
        self.assertEqual(c.asked[1][0], "/stigs/RHEL_9_STIG/revisions/latest/rules")
        self.assertEqual(info, {"asked": 3, "read": 2, "error": ""})

    def test_no_scope(self):
        out, info = smr.read_texts([{"benchmark": "RHEL_9_STIG", "revision": "V2R7", "rules": ["SV-1"]},
                                    {"benchmark": "RHEL_8_STIG", "revision": "V2R6", "rules": ["SV-2"]}],
                                   Client({}, fail="HTTP 403 from https://stigman.example.mil/api/stigs: no"))
        self.assertEqual(out, {})
        self.assertIn("the client needs the scope stig-manager:stig:read", info["error"])


class Checklists(unittest.TestCase):
    """Evaluate-STIG's checklist carries DISA's check and fix text: kept for a fail (the report), not for the rest."""
    CKL = ("<CHECKLIST><STIGS><iSTIG><STIG_INFO><SI_DATA><SID_NAME>stigid</SID_NAME><SID_DATA>RHEL_9_STIG</SID_DATA></SI_DATA></STIG_INFO>"
           "%s</iSTIG></STIGS></CHECKLIST>")
    VULN = ("<VULN>" + "".join("<STIG_DATA><VULN_ATTRIBUTE>%s</VULN_ATTRIBUTE><ATTRIBUTE_DATA>%%(%s)s</ATTRIBUTE_DATA></STIG_DATA>" % (k, k)
                               for k in ("Vuln_Num", "Rule_ID", "Rule_Ver", "Severity", "Rule_Title", "Check_Content", "Fix_Text"))
            + "<STATUS>%(status)s</STATUS><FINDING_DETAILS></FINDING_DETAILS><COMMENTS></COMMENTS></VULN>")

    def write(self, name, body):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), name)
        with open(p, "w") as f:
            f.write(body)
        return p

    def rule(self, n, status):
        return {"Vuln_Num": "V-%d" % n, "Rule_ID": "SV-%dr1_rule" % n, "Rule_Ver": "RHEL-09-%d" % n, "Severity": "medium",
                "Rule_Title": "T", "Check_Content": "$ sudo check %d" % n, "Fix_Text": "Fix %d." % n, "status": status}

    def test_ckl_and_cklb(self):
        import json
        ckl = self.write("a.ckl", self.CKL % (self.VULN % self.rule(1, "Open") + self.VULN % self.rule(2, "NotAFinding")))
        cklb = self.write("a.cklb", json.dumps({"stigs": [{"stig_id": "RHEL_9_STIG", "rules": [
            {"rule_id": "SV-1r1_rule", "group_id": "V-1", "rule_version": "R", "severity": "medium", "rule_title": "T", "status": "open",
             "check_content": "$ sudo check 1", "fix_text": "Fix 1."},
            {"rule_id": "SV-2r1_rule", "group_id": "V-2", "rule_version": "R", "severity": "medium", "rule_title": "T",
             "status": "not_a_finding", "check_content": "$ sudo check 2", "fix_text": "Fix 2."}]}]}))
        for p in (ckl, cklb):
            its = dict((i["key"], i) for i in scan.checklist_items(scan.read_checklist(p)[0]))
            self.assertEqual((its["SV-1"]["check_text"], its["SV-1"]["fix_text"]), ("$ sudo check 1", "Fix 1."), p)
            self.assertNotIn("check_text", its["SV-2"])                   # a pass: not kept (the result stays small)
        res = [stig_filters.stig_compare({"scanned": True, "items": [dict(its["SV-1"])], "previous": {"time": "t", "items": {"SV-1": "pass"}},
                                          "passed_before": {"SV-1": "2026-11-01"}}, {}, "rhel01")]
        self.assertEqual(t.stig_text_wanted(res), [])                      # it has its own: nothing to read
        self.assertEqual(t.stig_texts_of(res)["V-1"]["check"], "$ sudo check 1")


if __name__ == "__main__":
    unittest.main()
