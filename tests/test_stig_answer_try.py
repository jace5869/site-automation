"""1.77.0: STIG answer keys - try my answer file (plugins/filter/stig_answer_try_filters.py, the job's tasks)."""
import os
import sys
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "plugins", "filter"), os.path.join(HERE, "stig")]
import stig_answer_try_filters as T  # noqa: E402
import make_cklb as M  # noqa: E402

AF = M.AF_COMMENT % "NotAFinding"


def run(items, scanned=True, why=""):
    return {"scanned": scanned, "why": why, "items": items}


def item(v, result, comments="", details="", sev="medium", title="t"):
    return {"v": v, "key": "SV-" + v[2:], "result": result, "comments": comments, "details": details, "severity": sev, "title": title}


class Af(unittest.TestCase):
    def test_the_comment_evaluate_stig_writes(self):
        a = T.stig_answer_try_af({"comments": AF, "details": "Compliant."})
        self.assertEqual((a["key"], a["from"], a["to"]), ("DEFAULT", "Open", "NotAFinding"))
        self.assertEqual(a["comment"], "Approved by the site's ISSM: documented in the SSP.")
        self.assertEqual(a["verbose"], "")

    def test_no_answer_file(self):
        self.assertEqual(T.stig_answer_try_af({"comments": "", "details": M.FOUND % "SV-1"})["key"], "")
        self.assertEqual(T.stig_answer_try_af(None)["key"], "")

    def test_what_the_code_read_when_kept(self):
        a = T.stig_answer_try_af({"comments": AF, "details": "ValidationCode output:\nread: root\n----------\nmore"})
        self.assertEqual(a["verbose"], "read: root")

    def test_a_secret_the_code_printed_is_removed(self):
        """1.77.0b (Codex P1): the comment and Write-Verbose text are redacted again here (the module did it on the host)."""
        leak = "root:$6$saltsalt$LEAKabcdefgh:19000:0:99999:7:::\nGRUB2_PASSWORD=grub.pbkdf2.sha512.10000.5EED.BEEF"
        a = T.stig_answer_try_af({"comments": AF + "\n" + leak, "details": "ValidationCode output:\n" + leak + "\n----------\nmore"})
        for f in ("comment", "verbose"):
            self.assertNotIn("LEAK", a[f])
            self.assertNotIn("5EED", a[f])
            self.assertIn("root:$<hash removed>:19000", a[f])
            self.assertIn("grub.pbkdf2.<hash removed>", a[f])

    def test_another_key_name(self):
        c = AF.replace("'DEFAULT'", "'SITEA'")
        self.assertEqual(T.stig_answer_try_af({"comments": c})["key"], "SITEA")


class Rows(unittest.TestCase):
    def setUp(self):
        a = run([item("V-1", "notchecked", sev="high"), item("V-2", "fail"), item("V-3", "fail"), item("V-4", "pass"), item("V-5", "fail")])
        b = run([item("V-1", "pass", AF, sev="high"), item("V-2", "fail", M.AF_COMMENT % "Open"), item("V-3", "fail"),
                 item("V-4", "pass"), item("V-6", "fail")])
        self.t = T.stig_answer_try_rows([{"host": "h1", "without": a, "with": b}], ["V-1", "V-2", "V-3", "V-4", "V-5"])
        self.by = dict((r["v"], r) for r in self.t["rows"])

    def test_changed_by_the_key(self):
        r = self.by["V-1"]
        self.assertEqual((r["group"], r["without"], r["with"], r["key"], r["changed"], r["cat"]),
                         ("changed", "Not Reviewed", "Not a Finding", "DEFAULT", True, "CAT I"))
        self.assertIn("Changed by the key", r["what"])
        self.assertEqual(r["verbose"], T.NOT_KEPT)

    def test_a_key_that_left_it_open_needs_a_look(self):
        r = self.by["V-2"]
        self.assertEqual((r["group"], r["key"]), ("look", "DEFAULT"))
        self.assertIn("The key applied and set it Open", r["what"])

    def test_no_key_on_an_open_rule_needs_a_look(self):
        r = self.by["V-3"]
        self.assertEqual((r["group"], r["key"], r["verbose"]), ("look", "", ""))
        self.assertIn("No key applied: Open", r["what"])

    def test_no_key_needed(self):
        self.assertEqual(self.by["V-4"]["group"], "passed")

    def test_not_in_the_second_run(self):
        self.assertEqual(self.by["V-5"]["group"], "look")
        self.assertIn("Not in the second run", self.by["V-5"]["what"])

    def test_only_the_rules_asked_and_needing_a_look_first(self):
        self.assertNotIn("V-6", self.by)
        groups = [r["group"] for r in self.t["rows"]]
        self.assertEqual(groups, sorted(groups, key=["look", "changed", "passed"].index))

    def test_a_host_not_tried(self):
        t = T.stig_answer_try_rows([{"host": "h2", "without": run([], False, "no pwsh"), "with": {}},
                                    {"host": "h3", "without": run([item("V-1", "fail")]), "with": run([], False, "answers unread")}])
        self.assertEqual(t["rows"], [])
        self.assertEqual(t["not_run"], [{"host": "h2", "why": "without answer files: no pwsh",
                                         "why_mail": "Evaluate-STIG did not run without answer files."},
                                        {"host": "h3", "why": "with your answer files: answers unread",
                                         "why_mail": "Evaluate-STIG did not run with your answer files."}])


class Report(unittest.TestCase):
    def setUp(self):
        a = run([item("V-1", "notchecked"), item("V-2", "fail")])
        b = run([item("V-1", "pass", AF), item("V-2", "fail")])
        self.t = T.stig_answer_try_rows([{"host": "h1", "without": a, "with": b},
                                         {"host": "h2", "without": run([], False, "no pwsh"), "with": {}}], ["V-1", "V-2"])
        self.r = T.stig_answer_try_report(self.t, {"key": "SITEA", "source": "\\\\files.example.mil\\stig", "stig": "RHEL_9_STIG V2R10",
                                                   "files": [{"name": "a.xml", "sha256": "ab" * 32, "modified": "2026-11-17 14:02 UTC"}]})

    def test_title_tiles_and_sections_in_order(self):
        w = self.r["web"]
        self.assertEqual(w["title"], "Try my answer file: 1 changed by a key, 1 need a look")
        self.assertEqual(w["status"], "warning")
        self.assertEqual([t["label"] for t in w["summary"]], ["Changed by a key", "Need a look", "Hosts", "Written"])
        self.assertEqual([s["title"] for s in w["sections"]],
                         ["Not tried", "Need a look", "Changed by your answer file", "Your answer files", "How this was made"])
        # the website counts the rows beside the title; the email has the count in the title
        self.assertEqual([s["title"] for s in self.r["report"]["sections"]][:3],
                         ["Not tried (1)", "Need a look (1)", "Changed by your answer file (1)"])
        self.assertIn("answer key SITEA", w["subtitle"])
        self.assertNotIn(" - ", w["subtitle"])

    def test_one_host_one_rule_said_so(self):
        r = T.stig_answer_try_report({"rows": [dict(self.t["rows"][0])], "not_run": []}, {"stig": "RHEL_9_STIG V2R10"})
        self.assertIn("RHEL_9_STIG V2R10, 1 host, 1 rule, answer key DEFAULT.", r["web"]["subtitle"])

    def test_the_web_opens_a_rule_the_email_does_not(self):
        web = self.r["web"]["sections"][2]
        mail = self.r["report"]["sections"][2]
        self.assertEqual(web["kinds"][0], "x")
        self.assertEqual(len(web["details"]), len(web["rows"]))
        self.assertNotIn("details", mail)
        self.assertEqual(len(mail["columns"]), len(mail["rows"][0]))

    def test_the_comment_and_what_the_code_read_in_the_detail(self):
        d = self.r["web"]["sections"][2]["details"][0]
        self.assertIn(["Its comment (as Evaluate-STIG wrote it)", "Approved by the site's ISSM: documented in the SSP."], d[0]["rows"])
        self.assertEqual(d[1]["texts"][0]["text"], T.NOT_KEPT)

    def test_nothing_written_is_said(self):
        how = " ".join(self.r["web"]["sections"][-1]["lines"])
        self.assertIn("Nothing was written to STIG Manager or the share", how)
        self.assertIn("removed after", how)

    def test_no_secret_in_the_page_workbook_or_email(self):
        leak = "olduser:LEAKDESabcdefg:19000:0:::\ntoken=LEAKTOKEN"
        b = run([item("V-1", "pass", AF + "\n" + leak, "ValidationCode output:\n" + leak + "\n----------")])
        t = T.stig_answer_try_rows([{"host": "h1", "without": run([item("V-1", "notchecked")]), "with": b}], ["V-1"])
        r = T.stig_answer_try_report(t, {"key": "SITEA", "stig": "RHEL_9_STIG V2R10"})
        for part in ("report", "web", "xlsx", "lines"):
            self.assertNotIn("LEAK", str(r[part]), part)
        self.assertIn("olduser:<hash removed>:19000", str(r["web"]))

    def test_the_workbook_has_every_row(self):
        self.assertEqual(len(self.r["xlsx"][0]["rows"]), 2)

    def test_reader_text_short(self):
        for s in self.r["web"]["sections"]:
            for line in [s.get("text") or ""] + list(s.get("lines") or []):
                self.assertNotIn(";", line, line)
                self.assertNotIn(" - ", line.replace("Evaluate-STIG", "x"), line)


class Job(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(REPO, "roles", "check_stig", "tasks", "answer_keys_try.yml")) as f:
            self.text = f.read()
        self.tasks = yaml.safe_load(self.text)

    def scans(self):
        out = []
        for t in self.tasks:
            for x in t.get("block") or []:
                if "site_stig_scan" in x:
                    out.append(x["site_stig_scan"])
        return out

    def test_two_runs_selected_only_nothing_kept(self):
        s = self.scans()
        self.assertEqual(len(s), 2)
        for x in s:
            self.assertIs(x["selected_only"], True)
            self.assertIs(x["save"], False)
            self.assertIs(x["history"], False)
            self.assertIs(x["rule_texts"], True)
            self.assertEqual(x["evaluate_args"], "{{ _akt_key_args }}")   # only the site's --AnswerKey pair
        self.assertNotIn("answers", s[0])
        self.assertIn("_stig_af_host.path", s[1]["answers"])

    def test_the_answer_files_removed_always(self):
        blk = [t for t in self.tasks if "block" in t][0]
        self.assertIn("answers_done.yml", str(blk["always"]))
        # 1.77.0b (Codex P2): staged inside that block - a copy that fails part way is cleaned up too
        self.assertEqual(blk["block"][0].get("ansible.builtin.include_tasks"), "answers.yml")
        self.assertNotIn("answers.yml", [t.get("ansible.builtin.include_tasks") for t in self.tasks])

    def test_the_drift_scan_and_recheck_stage_inside_their_block_too(self):
        # 1.77.0c (Codex): the same in main.yml and the recheck's scan (1.83.0: recheck_scan.yml, the full scan runs it too)
        for name in ("main.yml", "recheck_scan.yml"):
            with self.subTest(name=name), open(os.path.join(REPO, "roles", "check_stig", "tasks", name)) as f:
                tasks = yaml.safe_load(f)
                blk = [t for t in tasks if "answers_done.yml" in str(t.get("always"))]
                self.assertEqual(len(blk), 1)
                self.assertEqual(blk[0]["block"][0].get("ansible.builtin.include_tasks"), "answers.yml")
                self.assertNotIn("answers.yml", [t.get("ansible.builtin.include_tasks") for t in tasks])

    def test_the_key_args_are_the_answerkey_pair_only(self):
        self.assertIn("['--answerkey', '-answerkey']", self.text)
        self.assertIn("out[-2:]", self.text)


if __name__ == "__main__":
    unittest.main()
