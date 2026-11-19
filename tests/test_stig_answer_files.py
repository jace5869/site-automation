"""1.77.1: which answer files Evaluate-STIG was given, and which answered which rule (site_stig_scan's answer_files;
plugins/filter/stig_answer_file_filters.py).
  python tests/test_stig_answer_files.py"""
from __future__ import absolute_import, division, print_function

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
sys.path.insert(0, os.path.join(REPO, "roles", "check_stig", "library"))
import ansible.module_utils  # noqa: E402 - the repository's module_utils as Ansible gives them (ansible.cfg)
ansible.module_utils.__path__.append(os.path.join(REPO, "plugins", "module_utils"))
import site_stig_scan as scan  # noqa: E402
import stig_answer_file_filters as af  # noqa: E402

AF = ('<?xml version="1.0" encoding="utf-8"?><STIGComments Name="RHEL_9_STIG">'
      '<Vuln ID="V-1"><AnswerKey Name="DEFAULT"><ExpectedStatus>Not_Reviewed</ExpectedStatus><ValidationCode>return $true</ValidationCode>'
      '<ValidTrueStatus>NotAFinding</ValidTrueStatus><ValidTrueComment>Approved by the ISSM.</ValidTrueComment>'
      '<ValidFalseStatus>Open</ValidFalseStatus><ValidFalseComment>No.</ValidFalseComment></AnswerKey>'
      '<AnswerKey Name="web"><ExpectedStatus>Open</ExpectedStatus><ValidTrueStatus>Not_Applicable</ValidTrueStatus></AnswerKey></Vuln>'
      '<Vuln ID="V-2"><AnswerKey Name="DEFAULT"><ExpectedStatus>Open</ExpectedStatus><ValidTrueStatus>NotAFinding</ValidTrueStatus></AnswerKey></Vuln>'
      '</STIGComments>')


def vuln(v, status, comment="", marker=""):
    return ("<VULN>%s<STIG_DATA><VULN_ATTRIBUTE>Vuln_Num</VULN_ATTRIBUTE><ATTRIBUTE_DATA>%s</ATTRIBUTE_DATA></STIG_DATA>"
            "<STIG_DATA><VULN_ATTRIBUTE>Severity</VULN_ATTRIBUTE><ATTRIBUTE_DATA>medium</ATTRIBUTE_DATA></STIG_DATA>"
            "<STATUS>%s</STATUS><COMMENTS>%s</COMMENTS></VULN>" % (marker, v, status, comment))


class Scan(unittest.TestCase):
    """The host side: the answer files read (names, ids, keys, statuses - never their comments or code), and each rule
    the checklist says an answer file set - with the file when the checklist names it."""
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_an_answer_file(self):
        p = os.path.join(self.d, "RHEL_9_site.xml")
        with open(p, "w") as f:
            f.write(AF)
        s = scan.answer_file_summary(p)
        self.assertEqual((s["name"], s["stig"], s["keys"], s["error"]), ("RHEL_9_site.xml", "RHEL_9_STIG", ["DEFAULT", "web"], ""))
        self.assertEqual(s["vulns"]["V-1"], {"DEFAULT": ["Not_Reviewed", "NotAFinding", "Open"], "web": ["Open", "Not_Applicable", ""]})
        self.assertEqual(len(s["sha256"]), 64)
        self.assertNotIn("Approved", json.dumps(s))                  # never a comment
        # 1.77.1 (Codex P1): an AnswerKey inside a ValidationCode's markup is no key of the file
        n = os.path.join(self.d, "nested.xml")
        with open(n, "w") as f:
            f.write('<STIGComments Name="S"><Vuln ID="V-2"><AnswerKey Name="REAL"><ExpectedStatus>Open</ExpectedStatus>'
                    '<ValidationCode><AnswerKey Name="FAKE"><ExpectedStatus>NotAFinding</ExpectedStatus></AnswerKey>'
                    '</ValidationCode></AnswerKey></Vuln></STIGComments>')
        ns = scan.answer_file_summary(n)
        os.remove(n)
        self.assertEqual((ns["keys"], list(ns["vulns"]["V-2"])), (["REAL"], ["REAL"]))
        self.assertNotIn("return $true", json.dumps(s))             # never its code
        with open(os.path.join(self.d, "AnswerFile_Template.xml"), "w") as f:
            f.write("<x/>")
        with open(os.path.join(self.d, "RHEL_9_templated_hosts.xml"), "w") as f:
            f.write(AF)
        with open(os.path.join(self.d, "bad.xml"), "w") as f:
            f.write("<STIGComments")
        with open(os.path.join(self.d, "z-bomb.xml"), "w") as f:         # a billion laughs: never parsed
            f.write('<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa">'
                    + "".join('<!ENTITY %s "%s">' % (chr(98 + i), ("&%s;" % chr(97 + i)) * 10) for i in range(9))
                    + ']><STIGComments Name="&j;"/>')
        with open(os.path.join(self.d, "notes.txt"), "w") as f:
            f.write("x")
        got, more, ferr = scan.answer_files_in(self.d)
        self.assertEqual(ferr, "")
        self.assertEqual([x["name"] for x in got], ["RHEL_9_site.xml", "RHEL_9_templated_hosts.xml", "bad.xml", "z-bomb.xml"])
        # not Evaluate-STIG's own template, not a .txt; a name with "template" in it is the site's own
        self.assertTrue(got[2]["error"].startswith("not read: XML error at line "), got[2]["error"])
        self.assertEqual((got[3]["error"], got[3]["stig"]), ("not read: has a DTD", ""))   # never parsed
        self.assertEqual(more, 0)
        self.assertEqual(len(scan.answer_files_in(self.d, max_files=1)[0]), 1)
        self.assertEqual(scan.answer_files_in(self.d, max_files=1)[1], 3)

    def w(self, name, data):
        p = os.path.join(self.d, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_never_parsed_unless_utf8_with_no_dtd(self):
        # 1.77.1c (Codex P1): UTF-16 slipped past a byte check; expat now refuses any DTD, whatever the encoding
        bomb = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><STIGComments Name="&b;"/>'
        self.assertEqual(scan.answer_file_summary(self.w("u16.xml", bomb.encode("utf-16")))["error"], "not read: not UTF-8")
        self.assertEqual(scan.answer_file_summary(self.w("u16le.xml", bomb.encode("utf-16-le")))["error"], "not read: not UTF-8")
        self.assertEqual(scan.answer_file_summary(self.w("l1.xml", b'<?xml version="1.0" encoding="ISO-8859-1"?><STIGComments Name="x"/>'))["error"],
                         "not read: not UTF-8")
        self.assertEqual(scan.answer_file_summary(self.w("xyz.xml", b'<?xml version="1.0" encoding="x-nonesuch"?><STIGComments/>'))["error"],
                         "not read: not UTF-8")
        self.assertEqual(scan.answer_file_summary(self.w("d.xml", AF.replace("<STIGComments", '<!DOCTYPE x><STIGComments', 1).encode()))["error"],
                         "not read: has a DTD")
        self.assertEqual(scan.answer_file_summary(self.w("ok8.xml", AF.encode("utf-8")))["error"], "")
        self.assertEqual(scan.answer_file_summary(self.w("bom8.xml", b"\xef\xbb\xbf" + AF.encode("utf-8")))["error"], "")
        # 1.77.1e (Codex P1): the encoding past the first 512 bytes (whitespace between the declaration's parts) - the
        # parser's own reading of it; a byte that is not UTF-8 with no declaration: an error, never decoded another way
        far = b'<?xml version="1.0"' + b" " * 700 + b'encoding="ISO-8859-1"?><STIGComments Name="x"/>'
        self.assertEqual(scan.answer_file_summary(self.w("far.xml", far))["error"], "not read: not UTF-8")
        self.assertRegex(scan.answer_file_summary(self.w("l1raw.xml", b'<STIGComments Name="caf\xe9"/>'))["error"],
                         r"^not read: XML error at line \d+, col \d+$")

    def test_the_cap_is_on_what_is_read_and_errors_say_where_only(self):
        big = self.w("big.xml", b"<STIGComments>" + b" " * (1024 * 1024 + 10) + b"</STIGComments>")
        self.assertEqual(scan.answer_file_summary(big, max_mb=1)["error"], "larger than 1 MB - not read")
        e = scan.answer_file_summary(self.w("e.xml", b"<STIGComments Name='a'><Vuln ID='V-1'>SECRET-TEXT</STIG>"))["error"]
        self.assertRegex(e, r"^not read: XML error at line \d+, col \d+$")
        self.assertNotIn("SECRET", e)

    def test_a_status_that_is_none_is_invalid_never_echoed(self):
        x = ('<STIGComments Name="S"><Vuln ID="V-1"><AnswerKey Name="DEFAULT"><ExpectedStatus>Open</ExpectedStatus>'
             '<ValidTrueStatus>NotAFinding because the ISSM said so</ValidTrueStatus><ValidFalseStatus><b>Open</b></ValidFalseStatus>'
             '</AnswerKey></Vuln></STIGComments>')
        s_ = scan.answer_file_summary(self.w("st.xml", x.encode()))
        self.assertEqual(s_["vulns"]["V-1"]["DEFAULT"], ["Open", "invalid status", ""])
        self.assertNotIn("ISSM", json.dumps(s_))

    def test_the_folder_not_read_is_said(self):
        if os.geteuid() == 0:
            self.skipTest("root reads any folder")
        d = os.path.join(self.d, "locked")
        os.makedirs(d)
        os.chmod(d, 0)
        try:
            self.assertEqual(scan.answer_files_in(d), ([], 0, "folder not read: Permission denied"))
        finally:
            os.chmod(d, 0o700)

    def test_the_folder_evaluate_stig_read(self):
        self.assertEqual(scan.af_folder("/run/x.stig-answers", [], []), ("/run/x.stig-answers", "staged"))
        self.assertEqual(scan.af_folder("", ["--Output", "CKLB", "--AFPath", "/opt/af"], []), ("/opt/af", "args"))
        # a relative folder is from the folder of the script Evaluate-STIG runs, not this module's
        self.assertEqual(scan.af_folder("", ["-AFPath", "af/site"], ["/opt/Evaluate-STIG/Evaluate-STIG_Bash.sh"]),
                         ("/opt/Evaluate-STIG/af/site", "args"))
        os.makedirs(os.path.join(self.d, "AnswerFiles"))
        es = os.path.join(self.d, "Evaluate-STIG_Bash.sh")
        self.assertEqual(scan.af_folder("", [], [es]), (os.path.join(self.d, "AnswerFiles"), "own"))
        self.assertEqual(scan.af_folder("", [], ["/nowhere/Evaluate-STIG.ps1"]), ("", ""))

    def test_a_ckl_names_the_file(self):
        mk = ("<!--<Evaluate-STIG><AnswerFile>C:\\Evaluate-STIG\\AnswerFiles\\RHEL_9_site.xml</AnswerFile><AFMod>True</AFMod>"
              "<OldStatus>Open</OldStatus><NewStatus>NotAFinding</NewStatus></Evaluate-STIG>-->")
        ckl = ("<CHECKLIST><STIGS><iSTIG><STIG_INFO><SI_DATA><SID_NAME>stigid</SID_NAME><SID_DATA>RHEL_9_STIG</SID_DATA></SI_DATA></STIG_INFO>"
               + vuln("V-1", "NotAFinding", "Evaluate-STIG answer file for Key 'DEFAULT' is changing the Status", mk)
               + vuln("V-2", "NotAFinding", "Evaluate-STIG answer file for Key 'web' is changing the Status")
               + vuln("V-3", "Open") + "</iSTIG></STIGS></CHECKLIST>")
        p = os.path.join(self.d, "h.ckl")
        with open(p, "w") as f:
            f.write(ckl)
        st = scan.read_checklist(p)[0]
        self.assertEqual(st["rules"][0]["af"], {"file": "RHEL_9_site.xml", "mod": True, "old": "Open"})
        got = scan.answered_rules(st)
        self.assertEqual([(r["v"], r["key"], r["file"], r["status"]) for r in got],
                         [("V-1", "DEFAULT", "RHEL_9_site.xml", "pass"), ("V-2", "web", "", "pass")])

    def test_ckl_markers_from_the_parsed_vulns(self):
        # 1.77.1c: "<VULN >" and a VULN inside a comment cannot shift the markers onto other rules
        mk = ("<!--<Evaluate-STIG><AnswerFile>RHEL_9_site.xml</AnswerFile><AFMod>True</AFMod><OldStatus>Open</OldStatus>"
              "</Evaluate-STIG>-->")
        ckl = ("<CHECKLIST><STIGS><iSTIG><STIG_INFO><SI_DATA><SID_NAME>stigid</SID_NAME><SID_DATA>RHEL_9_STIG</SID_DATA></SI_DATA></STIG_INFO>"
               "<!-- an old <VULN>note</VULN> -->"
               + vuln("V-1", "Open").replace("<VULN>", "<VULN >")
               + vuln("V-2", "NotAFinding", "Evaluate-STIG answer file for Key 'DEFAULT' is changing the Status", mk)
               + "</iSTIG></STIGS></CHECKLIST>")
        p = os.path.join(self.d, "h2.ckl")
        with open(p, "w") as f:
            f.write(ckl)
        st = scan.read_checklist(p)[0]
        self.assertEqual([r["af"] for r in st["rules"]], [{}, {"file": "RHEL_9_site.xml", "mod": True, "old": "Open"}])

    def test_answered_means_evaluate_stigs_own_words(self):
        # 1.77.1c: not any prose about answer files; a marker with AFMod false did not set it; the recheck: only its rules
        rules = [{"v": "V-1", "rule": "", "result": "pass", "comments": "No answer file was used for this one.", "af": {}},
                 {"v": "V-2", "rule": "", "result": "pass", "comments": "", "af": {"file": "a.xml", "mod": False}},
                 {"v": "V-3", "rule": "", "result": "pass", "comments": "Evaluate-STIG answer file for Key 'web' is changing", "af": {}},
                 {"v": "V-4", "rule": "", "result": "fail", "comments": "", "af": {"file": "b.xml", "mod": True}, "details": "Answer Key: DEFAULT"}]
        got = scan.answered_rules({"rules": rules})
        self.assertEqual([(r["v"], r["key"], r["file"]) for r in got], [("V-3", "web", ""), ("V-4", "DEFAULT", "b.xml")])
        self.assertEqual([r["v"] for r in scan.answered_rules({"rules": rules}, {"V-4"})], ["V-4"])

    def test_the_older_count_is_the_same_rows(self):
        # 1.77.1e (Codex): the role's "answer files: USED - N rules answered" (answers.used) from the same rows as the
        # Answer files sections - "No answer file was used" is not counted, AFMod false is not, the recheck: its rules
        rules = [{"v": "V-1", "rule": "", "result": "pass", "comments": "No answer file was used for this one.", "af": {}},
                 {"v": "V-2", "rule": "", "result": "pass", "comments": "Evaluate-STIG answer file for Key 'web' is changing", "af": {}},
                 {"v": "V-3", "rule": "", "result": "pass", "comments": "Evaluate-STIG answer file for Key 'x' is changing",
                  "af": {"file": "a.xml", "mod": False}},
                 {"v": "V-4", "rule": "", "result": "pass", "comments": "", "af": {"file": "b.xml", "mod": True}, "details": "Answer Key: DEFAULT"}]
        self.assertEqual(scan.answered_from_files({"rules": rules}), {"used": 2, "keys": ["DEFAULT", "web"], "rules": ["V-2", "V-4"]})
        self.assertEqual(scan.answered_from_files({"rules": rules}, {"V-4"})["rules"], ["V-4"])

    def test_afmod_decides_with_or_without_a_file_name(self):
        # 1.77.1e (Codex): a marker with AFMod and no file is a marker; AFMod false with the words: not answered
        self.assertEqual(scan.af_marker({"AFMod": "True"}), {"file": "", "mod": True, "old": ""})
        self.assertEqual(scan.af_marker({"OldStatus": "Open"}), {})
        rules = [{"v": "V-1", "rule": "", "result": "pass", "comments": "", "af": {"file": "", "mod": True}},
                 {"v": "V-2", "rule": "", "result": "pass", "comments": "Evaluate-STIG answer file for Key 'DEFAULT' is changing",
                  "af": {"file": "", "mod": False}}]
        self.assertEqual([(r["v"], r["file"]) for r in scan.answered_rules({"rules": rules})], [("V-1", "")])

    def test_no_afmod_means_evaluate_stigs_words_decide(self):
        # 1.77.1d: a marker with a file but no AFMod (an older Evaluate-STIG, a CKLB with no afmod): not known - the
        # words decide, and then the file is named; never hidden, never claimed without the words
        self.assertIsNone(scan.af_marker({"AnswerFile": "C:\\ES\\a.xml"})["mod"])
        self.assertIs(scan.af_marker({"AnswerFile": "a.xml", "AFMod": "False"})["mod"], False)
        rules = [{"v": "V-1", "rule": "", "result": "pass", "comments": "Evaluate-STIG answer file for Key 'DEFAULT' is changing",
                  "af": {"file": "a.xml", "mod": None}},
                 {"v": "V-2", "rule": "", "result": "pass", "comments": "", "af": {"file": "a.xml", "mod": None}}]
        self.assertEqual([(r["v"], r["key"], r["file"]) for r in scan.answered_rules({"rules": rules})], [("V-1", "DEFAULT", "a.xml")])

    def test_a_cklb_names_the_file_when_it_has_the_object(self):
        d = {"stigs": [{"stig_id": "RHEL_9_STIG", "rules": [
            {"group_id": "V-1", "status": "not_a_finding", "comments": "Evaluate-STIG answer file for Key 'DEFAULT'",
             "evaluate-stig": {"answer_file": "/opt/af/RHEL_9_site.xml", "afmod": True, "old_status": "Open"}},
            {"group_id": "V-2", "status": "open", "comments": "Evaluate-STIG answer file for Key 'DEFAULT'"},
            {"group_id": "V-3", "status": "open", "comments": ""}]}]}
        p = os.path.join(self.d, "h.cklb")
        with open(p, "w") as f:
            json.dump(d, f)
        got = scan.answered_rules(scan.read_checklist(p)[0])
        self.assertEqual([(r["v"], r["file"], r["status"]) for r in got], [("V-1", "RHEL_9_site.xml", "pass"), ("V-2", "", "fail")])


def f_(name, vulns, n=1, sha=None, err=""):
    return {"name": name, "stig": "RHEL_9_STIG", "keys": sorted(set(k for v in vulns.values() for k in v)), "vulns": vulns,
            "sha256": sha or (name[:1] * 64), "modified": "2026-11-12T09:40:00Z", "in_checklist": n, "error": err}


class Report(unittest.TestCase):
    """The report: Info by default; a Warning only when two files answer the same rule and key differently."""
    def files(self):
        a = f_("a.xml", {"V-1": {"DEFAULT": ["Open", "NotAFinding", ""]}, "V-2": {"DEFAULT": ["Open", "NotAFinding", ""]},
                         "V-3": {"DEFAULT": ["Open", "Open", ""]}})
        b = f_("b.xml", {"V-1": {"DEFAULT": ["Open", "NotAFinding", ""]}, "V-3": {"DEFAULT": ["Open", "NotAFinding", ""]}})
        return a, b

    def test_a_checklists_names_redacted(self):
        # 1.77.1 (Codex P1): a key and a file's name from a checklist (Windows' too) redacted as names before shown
        a, _ = self.files()
        res = [{"host": "win01", "answer_files": {"given": "own", "files": [a], "rules": [
                   {"v": "V-1", "key": "password=Hunter2xyz", "file": "token=abcdef123456.xml", "status": "pass",
                    "severity": "medium", "stigid": ""}]}}]
        r = af.stig_answer_files_report(res, {})
        dump = json.dumps(r)
        self.assertNotIn("Hunter2xyz", dump)
        self.assertNotIn("abcdef123456", dump)

    def test_in_try_my_answer_file(self):
        # 1.77.1: Try my answer file's report - the Answer files sections where its list of your files stood, before How
        # this was made; the note last; the workbook's sheets added; nothing changes when no host said its answer files
        a, b = self.files()
        res = [{"host": "web01", "answer_files": {"given": "staged", "files": [a], "rules": [
                   {"v": "V-1", "key": "DEFAULT", "file": "a.xml", "status": "pass", "severity": "medium", "stigid": ""}]}}]
        rep = af.stig_answer_files_report(res, {})
        secs = [{"title": "Need a look"}, {"title": "Your answer files"}, {"title": "How this was made"}]
        doc = {"web": {"sections": list(secs)}, "report": {"sections": list(secs)}, "xlsx": [{"name": "Try my answer file"}], "lines": ["t"]}
        out = af.stig_answer_files_try(doc, rep)
        titles = [s.get("title") for s in out["web"]["sections"]]
        self.assertEqual(titles[0], "Need a look")
        self.assertNotIn("Your answer files", titles)
        self.assertTrue(titles[1].startswith("Answer files - what Evaluate-STIG was given"))
        self.assertEqual(titles[-2:], ["How this was made", af.NOTE_TITLE])
        self.assertNotIn("Your answer files", [s.get("title") for s in out["report"]["sections"]])
        self.assertEqual(out["xlsx"][0]["name"], "Try my answer file")
        self.assertGreater(len(out["xlsx"]), 1)
        self.assertEqual(out["lines"], ["t", rep["line"]])
        self.assertEqual(af.stig_answer_files_try(doc, af.stig_answer_files_report([], {})), doc)

    def test_named_matched_and_two_files(self):
        a, b = self.files()
        res = [{"host": "web01", "answer_files": {"given": "staged", "files": [a, b], "rules": [
                   {"v": "V-1", "key": "DEFAULT", "file": "b.xml", "status": "pass", "severity": "medium", "stigid": "RHEL-09-1"},
                   {"v": "V-2", "key": "DEFAULT", "file": "", "status": "pass", "severity": "high", "stigid": ""},
                   {"v": "V-3", "key": "DEFAULT", "file": "", "status": "pass", "severity": "low", "stigid": ""}]}}]
        r = af.stig_answer_files_report(res, {"files": [{"name": "a.xml", "modified": "2026-11-01T08:00:00Z"}]})
        files, two, rules, note = r["web"]
        self.assertEqual(files["status"], "info")
        self.assertEqual(files["text"], "2 files from the site share, copied to each host and removed after.")
        self.assertEqual(files["rows"][0], ["a.xml", "RHEL_9_STIG", "3", "DEFAULT", "2026-11-01 08:00", "aaaaaaaaaaaa", "the site share", "1 rule on 1 host"])
        self.assertEqual(two["status"], "warning")                  # V-3 differs
        self.assertEqual([(x[1], x[4]) for x in two["rows"]], [("V-1", "same answer in both"), ("V-3", "differ")])
        self.assertEqual(two["row_status"], ["info", "warning"])
        self.assertEqual(two["rows"][0][5], "b.xml on web01")
        self.assertEqual([(x[4], x[5]) for x in rules["rows"]],
                         [("b.xml · DEFAULT", "named in the checklist"), ("a.xml · DEFAULT", "matched: rule, key and status"),
                          ("b.xml · DEFAULT", "matched: rule, key and status")])          # V-3: only b.xml gives Not a Finding
        self.assertEqual(note["title"], af.NOTE_TITLE)
        self.assertEqual(r["line"], "Answer files: 2 files given (1 STIG), 3 rules answered, 1 conflict, 1 rule in two files, same answer"
                                    " - the report's Answer files sections (website and workbook).")
        self.assertEqual([s["name"] for s in r["sheets"]], ["Answer files", "Answer files - same rule", "Answer files - rules"])
        self.assertEqual(len(r["sheets"][0]["rows"][0][5]), 64)        # the workbook: the whole SHA-256

    def test_info_when_the_same_and_unused(self):
        a, b = self.files()
        b["vulns"]["V-3"]["DEFAULT"] = ["Open", "Open", ""]
        c = f_("c.xml", {"V-9": {"DEFAULT": ["Open", "NotAFinding", ""]}}, n=0)
        d = f_("d.xml", {"V-8": {"DEFAULT": ["Open", "NotAFinding", ""]}}, n=4)
        res = [{"host": "web01", "answer_files": {"given": "own", "files": [a, b, c, d], "rules": []}}]
        r = af.stig_answer_files_report(res)
        self.assertEqual(r["web"][1]["status"], "info")
        self.assertEqual(r["web"][1]["text"], "Both files answer these rules for the same key, with the same answer: keep each rule in one file.")
        ans = dict((x[0], x[7]) for x in r["web"][0]["rows"])
        self.assertEqual(ans["c.xml"], "none - no host of its STIG")
        self.assertEqual(ans["d.xml"], "none on 1 host of its STIG")
        self.assertEqual(r["web"][0]["cell_status"][3][7], "info")       # Info, never a warning
        self.assertEqual(r["web"][0]["text"], "4 files in Evaluate-STIG's own AnswerFiles folder on the hosts.")
        self.assertNotIn("conflict", r["line"])
        self.assertIn("2 rules in two files, same answer", r["line"])

    def test_the_conflicts_file_is_looked_up_by_key(self):
        # 1.77.1c: the same rule in two files for key DEFAULT; the checklist named a file for key web only - not taken
        a = f_("a.xml", {"V-1": {"DEFAULT": ["Open", "NotAFinding", ""], "web": ["Open", "NotAFinding", ""]}})
        b = f_("b.xml", {"V-1": {"DEFAULT": ["Open", "Open", ""]}})
        res = [{"host": "web01", "answer_files": {"given": "staged", "files": [a, b], "rules": [
            {"v": "V-1", "key": "web", "file": "a.xml", "status": "pass"}]}}]
        two = [x for x in af.stig_answer_files_report(res)["web"] if x["title"].endswith("two files")][0]
        self.assertEqual([(x[2], x[5]) for x in two["rows"]], [("DEFAULT", "unknown - no host answered it")])

    def test_a_folder_not_read(self):
        r = af.stig_answer_files_report([{"host": "web01", "answer_files": {"given": "own", "files": [], "rules": [],
                                                                            "error": "folder not read: Permission denied"}}])
        self.assertEqual(r["web"][0]["title"], "Answer files - the folder could not be read")
        self.assertEqual(r["web"][0]["lines"], ["web01: folder not read: Permission denied"])
        self.assertIn("the folder not read on 1 host", r["line"])
        # 1.77.1e (Codex): the workbook says it too
        sh = [x for x in r["sheets"] if x["name"] == "Answer files - folder not read"]
        self.assertEqual(sh[0]["rows"], [["web01", "folder not read: Permission denied"]])

    def test_not_certain(self):
        a, b = self.files()
        res = [{"host": "web01", "answer_files": {"given": "staged", "files": [a, b], "rules": [
            {"v": "V-1", "key": "DEFAULT", "file": "", "status": "pass"}, {"v": "V-7", "key": "DEFAULT", "file": "", "status": "pass"}]}}]
        rows = af.stig_answer_files_report(res)["web"][2]["rows"]
        self.assertEqual(rows[0][4:], ["? · DEFAULT", "in 2 files (a.xml, b.xml) - which one: unknown"])
        self.assertEqual(rows[1][4:], ["? · DEFAULT", "no file here has it - which one: unknown"])

    def test_placed_after_every_host_note_last(self):
        secs = [{"title": "1. Drifted"}, {"title": "Every host - the scan"}, {"title": "What Evaluate-STIG found"}]
        a, b = self.files()
        r = af.stig_answer_files_report([{"host": "h", "answer_files": {"given": "staged", "files": [a], "rules": []}}])
        t = [s["title"] for s in af.stig_answer_files_place(secs, r["web"])]
        self.assertEqual(t, ["1. Drifted", "Every host - the scan", "Answer files - what Evaluate-STIG was given, and what it used",
                             "What Evaluate-STIG found", af.NOTE_TITLE])
        self.assertEqual([s["title"] for s in af.stig_answer_files_place(secs, r["email"])][2], "Answer files")

    def test_none_given_and_no_evaluate_stig(self):
        r = af.stig_answer_files_report([{"host": "h", "answer_files": {"given": "", "files": [], "rules": [], "folder": ""}}])
        self.assertEqual(r["web"][0]["lines"], ["None: no answer file in its folder. Evaluate-STIG set no rule from one."])
        self.assertEqual(af.stig_answer_files_report([{"host": "h", "answer_files": {}}])["web"], [])     # OpenSCAP: nothing


WIN = os.path.join(REPO, "roles", "win_check_stig", "files", "win_stig.ps1")


def win_functions(names):
    """The win_stig.ps1 functions named, and the tables they read - to run them alone with pwsh."""
    src = open(WIN).read()
    out = [l for l in src.splitlines() if l.startswith(("$CKL_RESULT", "$CKL_SEV", "$AF_STATUS"))]
    for n in names:
        i = src.index("function %s(" % n)
        j = src.index("\n}\n", i) + 3
        out.append(src[i:j])
    return "\n".join(out)


@unittest.skipUnless(shutil.which("pwsh"), "no pwsh here")
class Windows(unittest.TestCase):
    """1.77.1c (Codex on the Windows scan): the answer-file parts of win_stig.ps1, run with pwsh - in Constrained Language
    Mode too (no XPath, no .NET path calls there: they never fail a scan)."""
    FUNCS = ["Get-Plain", "Get-XText", "Get-AfStatus", "Get-AfOf", "Get-AfFileOfObject", "Read-AnswerFiles", "New-Rule",
             "Read-Checklist", "Get-AfTaskScript", "Set-AfFolder", "Test-XDtd", "Get-XAttr", "Find-XElems", "Get-XStatus",
             "Test-OnlyWrittenFffd", "Test-XAt", "Find-XEnd", "Test-XName", "Get-XKids"]

    def ps(self, body, clm=False):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "t.ps1")
            with open(p, "w") as f:
                f.write(("$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'\n" if clm else "")
                        + "$ErrorActionPreference = 'Stop'\n" + win_functions(self.FUNCS) + "\n" + body)
            import subprocess
            r = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-File", p], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=120)
            return r.stdout.decode("utf-8", "replace").strip()
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def w(self, name, data):
        with open(os.path.join(self.d, name), "wb") as f:
            f.write(data)

    def test_answer_files_read_safely(self):
        for clm in (False, True):
            with self.subTest(constrained=clm):
                self.check_read_safely(clm)

    def check_read_safely(self, clm):
        # 1.77.1d: in Constrained Language Mode too - cmdlets only: the hash, the reasons, the statuses as in full mode
        bomb = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE l [<!ENTITY a "aaaa">]><STIGComments Name="&a;"/>'
        self.w("a-u16.xml", bomb.encode("utf-16"))
        self.w("b-dtd.xml", b'<?xml version="1.0"?><!DOCTYPE x><STIGComments Name="x"/>')
        self.w("c-bad.xml", b"<STIGComments Name='a'><Vuln ID='V-1'>SECRET-TEXT</STIG>")
        self.w("d-l1.xml", b'<?xml version="1.0" encoding="ISO-8859-1"?><STIGComments Name="x"/>')
        self.w("e-ok.xml", ('<STIGComments Name="S"><Vuln ID="V-1"><AnswerKey Name="DEFAULT"><ExpectedStatus>Open</ExpectedStatus>'
                            '<ValidTrueStatus>NotAFinding because the ISSM said so</ValidTrueStatus><ValidFalseStatus><b>Open</b></ValidFalseStatus>'
                            '</AnswerKey></Vuln></STIGComments>').encode())
        out = json.loads(self.ps("ConvertTo-Json -Depth 8 -Compress -InputObject (Read-AnswerFiles '%s')" % self.d, clm=clm))
        got = dict((f["name"], f) for f in out["files"])
        import hashlib
        with open(os.path.join(self.d, "e-ok.xml"), "rb") as f:
            self.assertEqual(got["e-ok.xml"]["sha256"], hashlib.sha256(f.read()).hexdigest())
        self.assertEqual(got["e-ok.xml"]["stig"], "S")
        self.assertEqual(got["e-ok.xml"]["error"], "")
        self.assertEqual(got["a-u16.xml"]["error"], "not read: not UTF-8")
        self.assertEqual(got["b-dtd.xml"]["error"], "not read: has a DTD")
        self.assertRegex(got["c-bad.xml"]["error"], r"^not read: XML error at line \d+, col \d+$")
        self.assertEqual(got["d-l1.xml"]["error"], "not read: not UTF-8")
        self.assertEqual(got["e-ok.xml"]["vulns"]["V-1"]["DEFAULT"], ["Open", "invalid status", "invalid status"])
        self.assertNotIn("ISSM", json.dumps(out))
        # 1.77.1 (Codex P1): an AnswerKey inside a ValidationCode's markup is no key; no copy written to the temp folder
        self.w("f-nested.xml", ('<STIGComments Name="S"><Vuln ID="V-2"><AnswerKey Name="REAL"><ExpectedStatus>Open</ExpectedStatus>'
                                '<ValidationCode><AnswerKey Name="FAKE"><ExpectedStatus>NotAFinding</ExpectedStatus></AnswerKey>'
                                '</ValidationCode></AnswerKey></Vuln></STIGComments>').encode())
        tmp = tempfile.mkdtemp()
        try:
            out = json.loads(self.ps("$env:TEMP = '%s'; ConvertTo-Json -Depth 8 -Compress -InputObject (Read-AnswerFiles '%s')"
                                     % (tmp, self.d), clm=clm))
            self.assertEqual(os.listdir(tmp), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        got = dict((f["name"], f) for f in out["files"])
        self.assertEqual((got["f-nested.xml"]["keys"], list(got["f-nested.xml"]["vulns"]["V-2"])), (["REAL"], ["REAL"]))
        self.assertNotIn("SECRET", json.dumps(out))

    def test_1771e_files_read_as_written(self):
        # 1.77.1e (Codex): in both modes - a byte not UTF-8 (no declaration), a "<!DOCTYPE" in a comment (no DTD: read),
        # a DTD in the prolog (refused), attributes only (a child <Name> or <ID> is no attribute), STIGComments under a
        # wrapper, an empty file (an XML error), the hash of what was read (one snapshot), a folder given that is not there
        self.w("a-l1.xml", b'<STIGComments Name="caf\xe9"><Vuln ID="V-1"/></STIGComments>')
        self.w("b-cmt.xml", b'<STIGComments Name="S"><!-- <!DOCTYPE x> is text here --><Vuln ID="V-1"><AnswerKey Name="K">'
                            b'<ExpectedStatus>Open</ExpectedStatus></AnswerKey></Vuln></STIGComments>')
        self.w("c-dtd.xml", b'<?xml version="1.0"?>\n<!-- c --><!DOCTYPE STIGComments><STIGComments Name="x"/>')
        self.w("d-attr.xml", b'<STIGComments><Name>FROM-A-CHILD</Name><Vuln><ID>V-9</ID><AnswerKey Name="K"/></Vuln>'
                             b'<Vuln ID="V-2"><AnswerKey><Name>CHILD</Name><ExpectedStatus>Open</ExpectedStatus></AnswerKey></Vuln></STIGComments>')
        self.w("e-wrap.xml", b'<Wrapper><STIGComments Name="W"><Vuln ID="V-3"><AnswerKey Name="DEFAULT"><ExpectedStatus>NotAFinding'
                             b'</ExpectedStatus></AnswerKey></Vuln></STIGComments></Wrapper>')
        self.w("f-empty.xml", b"")
        for clm in (False, True):
            with self.subTest(constrained=clm):
                out = json.loads(self.ps("ConvertTo-Json -Depth 8 -Compress -InputObject (Read-AnswerFiles '%s')" % self.d, clm=clm))
                got = dict((f["name"], f) for f in out["files"])
                self.assertEqual(got["a-l1.xml"]["error"], "not read: not UTF-8")
                self.assertEqual((got["b-cmt.xml"]["error"], got["b-cmt.xml"]["vulns"]), ("", {"V-1": {"K": ["Open", "", ""]}}))
                self.assertEqual(got["c-dtd.xml"]["error"], "not read: has a DTD")
                self.assertEqual((got["d-attr.xml"]["stig"], got["d-attr.xml"]["vulns"]), ("", {"V-2": {"": ["Open", "", ""]}}))
                self.assertNotIn("FROM-A-CHILD", json.dumps(out))
                self.assertEqual((got["e-wrap.xml"]["stig"], got["e-wrap.xml"]["vulns"]), ("W", {"V-3": {"DEFAULT": ["NotAFinding", "", ""]}}))
                self.assertEqual(got["f-empty.xml"]["error"], "not read: XML error at line 1, col 1")
                # the hash is of what was read (a private copy - one snapshot), and the copy is gone after
                import hashlib
                with open(os.path.join(self.d, "e-wrap.xml"), "rb") as fh:
                    self.assertEqual(got["e-wrap.xml"]["sha256"], hashlib.sha256(fh.read()).hexdigest())
                miss = os.path.join(self.d, "nope")
                self.assertEqual(self.ps("[string](Read-AnswerFiles '%s' $true).error; '|' + [string](Read-AnswerFiles '%s').error"
                                         % (miss, miss), clm=clm).splitlines(), ["folder not read: not found or not a folder", "|"])

    def test_1771e_review_two(self):
        # 1.77.1e (Codex, round 2): the prolog scanned once, to the first end of each token - long whitespace never stalls,
        # "-->" and "<!DOCTYPE" in a finding after the root are never looked at; a U+FFFD the file holds as written is
        # read (a byte that is not UTF-8 is not); a child named Attributes / ChildNodes / LocalName shadows nothing
        import time
        self.w("g-fffd.xml", '<STIGComments Name="S"><!-- \ufffd --><Vuln ID="V-1"><AnswerKey Name="K"/></Vuln></STIGComments>'.encode())
        self.w("i-meta.xml", b'<STIGComments xmlns:meta="urn:x" Name="M"><Vuln meta:ID="V-META" ID="V-5"><AnswerKey Name="K"/></Vuln></STIGComments>')
        self.w("a-u16.xml", '<STIGComments Name="U"/>'.encode("utf-16"))
        self.w("h-shadow.xml", b'<STIGComments Name="Real"><Attributes><LocalName>Name</LocalName><Value>child text</Value></Attributes>'
                               b'<ChildNodes/><Vuln ID="V-2"><LocalName/><AnswerKey Name="K"><ExpectedStatus>Open</ExpectedStatus></AnswerKey></Vuln></STIGComments>')
        for clm in (False, True):
            with self.subTest(constrained=clm):
                t = time.time()
                out = self.ps("""
                    $ws = '<?xml version="1.0"?>' + (' ' * 200000) + '<!-- a --><CHECKLIST><X><![CDATA[--> <!DOCTYPE x>]]></X></CHECKLIST>'
                    [string](Test-XDtd $ws)
                    [string](Test-XDtd ('<!-- a --> <?pi x?>' + "`n" + '<!DOCTYPE x><r/>'))
                    [string](Test-XDtd ('<!-- --> <!DOCTYPE x> -->' + '<r/>'))
                    [string](Test-XDtd (('<!--c-->' * 12000) + '<!DOCTYPE x><r/>'))
                    [string](Test-XDtd ('<!-- a-' + [string][char]0x00AD + '-> still --><!DOCTYPE x><r/>'))
                    [string](Test-XDtd ('<?pi a?' + [string][char]0x00AD + '> b ?><!DOCTYPE x><r/>'))
                    ConvertTo-Json -Depth 8 -Compress -InputObject (Read-AnswerFiles '%s')""" % self.d, clm=clm).splitlines()
                self.assertLess(time.time() - t, 60)
                self.assertEqual(out[:6], ["False", "True", "True", "True", "True", "True"])
                got = dict((f["name"], f) for f in json.loads(out[-1])["files"])
                self.assertEqual((got["g-fffd.xml"]["error"], got["g-fffd.xml"]["stig"]), ("", "S"))
                self.assertEqual((got["h-shadow.xml"]["error"], got["h-shadow.xml"]["stig"]), ("", "Real"))
                self.assertEqual(got["h-shadow.xml"]["vulns"], {"V-2": {"K": ["Open", "", ""]}})
                self.assertNotIn("child text", out[-1])
                self.assertEqual(got["i-meta.xml"]["vulns"], {"V-5": {"K": ["", "", ""]}})       # the ID of no namespace
                self.assertEqual(len(got["a-u16.xml"]["sha256"]), 64)                           # kept when not read

    def test_1771e_review_three(self):
        # 1.77.1e (Codex, round 3): elements and attributes by their exact name and no namespace (never meta:STIGComments,
        # an <expectedstatus>, or a name equal only linguistically); no depth cut-off (STIGComments 80 levels down); a
        # U+FFFD across the 64 KiB chunks counted; a comment full of hyphens scanned fast
        import time
        deep = "".join("<w%d>" % i for i in range(80)) + '<STIGComments Name="D"><Vuln ID="V-8"><AnswerKey Name="K"/></Vuln></STIGComments>' + \
            "".join("</w%d>" % i for i in reversed(range(80)))
        self.w("j-deep.xml", deep.encode())
        self.w("k-ns.xml", b'<Wrap xmlns:meta="urn:x"><meta:STIGComments Name="FAKE"><Vuln ID="V-X"/></meta:STIGComments>'
                           b'<STIGComments Name="REAL"><Vuln ID="V-9"><AnswerKey Name="K"><expectedstatus>Open</expectedstatus>'
                           b'</AnswerKey></Vuln></STIGComments></Wrap>')
        self.w("l-ling.xml", '<STIGComments Name="L"><Vuln I\u0640D="V-LING" ID="V-7"><AnswerKey Name="K"/></Vuln></STIGComments>'.encode())
        big = '<STIGComments Name="B"><!-- ' + "x" * (65536 - 30) + '\ufffd\ufffd --><Vuln ID="V-6"><AnswerKey Name="K"/></Vuln></STIGComments>'
        self.w("m-chunk.xml", big.encode())
        for clm in (False, True):
            with self.subTest(constrained=clm):
                t = time.time()
                out = self.ps("""
                    [string](Test-XDtd ('<!--' + ('- ' * 200000) + '--><!DOCTYPE x><r/>'))
                    ConvertTo-Json -Depth 8 -Compress -InputObject (Read-AnswerFiles '%s')""" % self.d, clm=clm).splitlines()
                self.assertLess(time.time() - t, 90)
                self.assertEqual(out[0], "True")
                got = dict((f["name"], f) for f in json.loads(out[-1])["files"])
                self.assertEqual((got["j-deep.xml"]["stig"], list(got["j-deep.xml"]["vulns"])), ("D", ["V-8"]))
                self.assertEqual((got["k-ns.xml"]["stig"], got["k-ns.xml"]["vulns"]), ("REAL", {"V-9": {"K": ["", "", ""]}}))
                self.assertEqual(list(got["l-ling.xml"]["vulns"]), ["V-7"])
                self.assertEqual((got["m-chunk.xml"]["error"], list(got["m-chunk.xml"]["vulns"])), ("", ["V-6"]))

    def test_answered_means_evaluate_stigs_own_words(self):
        out = self.ps("""@(
          (Get-AfOf 'No answer file was used for this one.' '' ''),
          (Get-AfOf "Evaluate-STIG answer file for Key 'web' is changing" '' ''),
          (Get-AfOf '' 'C:\\ES\\AnswerFiles\\a.xml' 'False'),
          (Get-AfOf "Answer Key: DEFAULT" 'C:\\ES\\AnswerFiles\\b.xml' 'True'),
          (Get-AfOf "Evaluate-STIG answer file for Key 'DEFAULT' is changing" 'C:\\ES\\AnswerFiles\\c.xml' ''),
          (Get-AfOf '' 'C:\\ES\\AnswerFiles\\d.xml' ''),
          (Get-AfOf '' '' 'True'),
          (Get-AfOf "Evaluate-STIG answer file for Key 'DEFAULT' is changing" '' 'False')) | ForEach-Object { ConvertTo-Json -Compress -InputObject $_ }""")
        got = [json.loads(x) for x in out.splitlines()]
        self.assertEqual([(g["answered"], g["key"], g["file"]) for g in got],
                         [(False, "", ""), (True, "web", ""), (False, "", ""), (True, "DEFAULT", "b.xml"),
                          (True, "DEFAULT", "c.xml"), (False, "", ""),
                          (True, "", ""), (False, "DEFAULT", "")])              # 1.77.1e: AFMod decides, with or without a file         # 1.77.1d: no AFMod - the words decide

    def test_a_ckl_in_constrained_language_mode(self):
        mk = ("<!--<Evaluate-STIG><AnswerFile>C:\\ES\\AnswerFiles\\RHEL_9_site.xml</AnswerFile><AFMod>True</AFMod></Evaluate-STIG>-->")
        ckl = ("<CHECKLIST><STIGS><iSTIG><STIG_INFO><SI_DATA><SID_NAME>stigid</SID_NAME><SID_DATA>W</SID_DATA></SI_DATA></STIG_INFO>"
               + vuln("V-1", "Open").replace("<VULN>", "<VULN >")
               + vuln("V-2", "NotAFinding", "Evaluate-STIG answer file for Key 'DEFAULT' is changing", mk)
               + "</iSTIG></STIGS></CHECKLIST>")
        self.w("w.ckl", ckl.encode())
        body = ("$s = @(Read-Checklist '%s')[0]; @($s.rules | ForEach-Object { [string]$_.v + '|' + [string]$_.af.answered + '|' + "
                "[string]$_.af.file + '|' + [string]$_.af.key }) -join ';'" % os.path.join(self.d, "w.ckl"))
        want = "V-1|False||;V-2|True|RHEL_9_site.xml|DEFAULT"
        self.assertEqual(self.ps(body), want)
        self.assertEqual(self.ps(body, clm=True), want)                     # no XPath: Constrained Language Mode reads it too
        # 1.77.1e: "<!DOCTYPE" as text in a finding (CDATA) or a comment is no DTD - the checklist is read; a marker with
        # AFMod true and no file: answered, its file not known
        mk2 = "<!--<Evaluate-STIG><AFMod>True</AFMod></Evaluate-STIG>-->"
        txt = ("<CHECKLIST><!-- <!DOCTYPE no> --><STIGS><iSTIG><STIG_INFO><SI_DATA><SID_NAME>stigid</SID_NAME><SID_DATA>W</SID_DATA></SI_DATA>"
               "</STIG_INFO>" + vuln("V-7", "Open", "", mk2).replace("<COMMENTS>", "<FINDING_DETAILS><![CDATA[<!DOCTYPE x>]]></FINDING_DETAILS><COMMENTS>")
               + "</iSTIG></STIGS></CHECKLIST>")
        self.w("t.ckl", txt.encode())
        body2 = ("$s = @(Read-Checklist '%s')[0]; @($s.rules | ForEach-Object { [string]$_.v + '|' + [string]$_.af.answered + '|' + "
                 "[string]$_.af.file }) -join ';'" % os.path.join(self.d, "t.ckl"))
        self.assertEqual(self.ps(body2), "V-7|True|")
        self.assertEqual(self.ps(body2, clm=True), "V-7|True|")
        # 1.77.1d: a checklist with a DTD is not read (as on Linux)
        self.w("d.ckl", ('<?xml version="1.0"?><!DOCTYPE c [<!ENTITY a "aaaa">]>' + ckl).encode())
        self.assertEqual(self.ps("try { Read-Checklist '%s'; 'read' } catch { [string]$_.Exception.Message }" % os.path.join(self.d, "d.ckl")),
                         "checklist not read: has a DTD")

    def test_the_folder_and_the_task(self):
        out = self.ps("""$extra = @('-AFPath', 'rel/af'); $script:afFolder = ''; $script:afGiven = ''; $script:afRead = @{ files = @(); more = 0 }
          Set-AfFolder '' 'own'; [string]$script:afRead.error
          $t = @(@{ script = 'C:/A/Evaluate-STIG.ps1'; output = 'D:/STIG' }, @{ script = 'C:/B/Evaluate-STIG.ps1'; output = 'E:/Out/' })
          'hit=' + (Get-AfTaskScript $t 'e:/out'); 'none=' + (Get-AfTaskScript $t 'F:/Other'); 'deep=' + (Get-AfTaskScript $t 'D:\\STIG\\HOST1\\Checklist'); 'near=' + (Get-AfTaskScript $t 'D:/STIGX'); 'one=' + (Get-AfTaskScript @($t[0]) 'F:/Other')""", clm=True)
        self.assertEqual(out.splitlines(), ["folder not read: a relative -AFPath, and Evaluate-STIG's own folder is not known",
                                            "hit=C:/B/Evaluate-STIG.ps1", "none=", "deep=C:/A/Evaluate-STIG.ps1", "near=",
                                            "one=C:/A/Evaluate-STIG.ps1"])


if __name__ == "__main__":
    unittest.main()
