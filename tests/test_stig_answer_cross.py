"""1.77.0: the cross-check of the STIG answer keys (plugins/filter/stig_answer_cross_filters.py)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "filter"))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "roles", "check_stig", "library"))
import ansible.module_utils  # noqa: E402 - the repository's module_utils as Ansible gives them (ansible.cfg)
ansible.module_utils.__path__.append(os.path.join(os.path.dirname(HERE), "plugins", "module_utils"))
import stig_answer_cross_filters as C  # noqa: E402
import site_stig_answer_reads as R  # noqa: E402

SCAN = "2026-11-16T10:00:00Z"
NOW = "2026-11-18T10:00:00Z"


def sm(result, ts="2026-11-17T10:00:00Z", engine=None):
    return C.stig_answer_cross_review({"result": result, "ts": ts, "engine": engine}, SCAN, NOW)


def row(host, v="V-1", code="NF", act="NF", review=None, stig="RHEL 9 V2R10", es="Open", **kw):
    r = {"v": v, "sv": "SV-" + v[2:], "cat": "CAT II", "title": "t", "stig": stig, "host": host, "es": es,
         "sm": review or sm(""), "code": code, "act": ({"verdict": act, "why": "w"} if act is not None else None)}
    r.update(kw)
    return r


class Review(unittest.TestCase):
    def test_a_person_newer_than_the_scan_counts(self):
        r = sm("pass")
        self.assertEqual((r["counted"], r["short"]), (True, "NF"))
        self.assertEqual(r["word"], "NF (a person, 1 d)")

    def test_older_than_the_scan_is_shown_not_counted(self):
        r = sm("pass", "2026-10-09T10:00:00Z")
        self.assertFalse(r["counted"])
        self.assertEqual(r["word"], "NF (a person, 40 d — older than the scan)")

    def test_the_scans_own_import_is_not_a_second_opinion(self):
        r = sm("fail", engine={"product": "Evaluate-STIG"})
        self.assertFalse(r["counted"])
        self.assertEqual(r["word"], "Open (from the scan, 1 d)")

    def test_no_scan_time_is_shown_not_counted(self):
        """1.77.0b (Codex P2): no review a result engine wrote - no scan time - nothing to say it is newer: not counted."""
        r = C.stig_answer_cross_review({"result": "pass", "ts": "2026-11-17T10:00:00Z"}, "", NOW)
        self.assertFalse(r["counted"])
        self.assertEqual(r["word"], "NF (a person, 1 d — no scan time to compare)")

    def test_same_time_as_the_scan_is_shown_not_counted(self):
        r = sm("fail", SCAN)
        self.assertFalse(r["counted"])
        self.assertEqual(r["word"], "Open (a person, 2 d — same time as the scan)")
        self.assertTrue(sm("fail", "2026-11-16T10:00:01Z")["counted"])           # a second later: newer

    def test_a_fraction_of_a_second_counts(self):
        """1.77.0d (Codex P2): .900Z is newer than .100Z - the fractions are kept, and the times compared in UTC."""
        scan = "2026-11-16T10:00:00.100Z"
        r = C.stig_answer_cross_review({"result": "pass", "ts": "2026-11-16T10:00:00.900Z"}, scan, NOW)
        self.assertEqual((r["counted"], r["word"]), (True, "NF (a person, 1 d)"))
        r = C.stig_answer_cross_review({"result": "pass", "ts": "2026-11-16T10:00:00.1Z"}, scan, NOW)
        self.assertEqual((r["counted"], r["word"]), (False, "NF (a person, 1 d — same time as the scan)"))
        r = C.stig_answer_cross_review({"result": "pass", "ts": "2026-11-16T12:00:00.05+02:00"}, scan, NOW)
        self.assertEqual((r["counted"], r["word"]), (False, "NF (a person, 1 d — older than the scan)"))
        sm = sm_rec({"SV-1": {"result": "fail", "ts": "2026-11-12T12:00:00.9+02:00", "engine": {"product": "Evaluate-STIG"}},
                     "SV-2": {"result": "fail", "ts": "2026-11-12T10:00:00.25Z", "engine": {"product": "Evaluate-STIG"}}})
        self.assertEqual(C.stig_answer_cross_scan_time(sm), "2026-11-12T10:00:00.9Z")
        # an offset no clock has is no time (Codex): not counted, the cross-check goes on
        for bad in ("2026-11-16T10:00:00.9+24:00", "2026-11-16T10:00:00+05:60"):
            r = C.stig_answer_cross_review({"result": "pass", "ts": bad}, scan, NOW)
            self.assertEqual((r["counted"], r["word"]), (False, "NF (a person, age unknown)"))

    def test_no_time_on_the_review_is_not_counted(self):
        r = sm("pass", "")
        self.assertFalse(r["counted"])
        self.assertEqual(r["word"], "NF (a person, age unknown)")

    def test_no_scan_time_never_makes_agree(self):
        """The review that would be the second source: not counted, so the code alone is too little to compare."""
        info = {"h1": {"sm": {"reviews": {"257777": {"result": "pass", "ts": "2026-11-17T10:00:00Z"}}}, "es": {}}}
        self.assertEqual(C.stig_answer_cross_scan_time(info["h1"]["sm"]), "")
        r = C.stig_answer_cross_review(info["h1"]["sm"]["reviews"]["257777"], C.stig_answer_cross_scan_time(info["h1"]["sm"]), NOW)
        self.assertFalse(r["counted"])

    def test_none_and_not_reviewed(self):
        self.assertEqual(sm("")["word"], "none")
        self.assertFalse(sm("notchecked")["counted"])


class Act(unittest.TestCase):
    def test_the_task_has_the_evidence_never_the_code(self):
        t = C.stig_answer_cross_act_task({"v": "V-1", "title": "t", "check_text": "stat -c %U /etc/group-"},
                                         [{"cmd": "/usr/bin/stat -c '%U' /etc/group-", "rc": 0, "out": "root"}], {"min_release": "9.4"})
        self.assertIn("$ /usr/bin/stat -c '%U' /etc/group-   (exit 0)\nroot", t)
        self.assertIn("min_release = 9.4", t)
        for w in ("ValidationCode", "Evaluate-STIG", "AnswerKey"):
            self.assertNotIn(w, t)

    def test_the_task_never_carries_a_secret(self):
        """1.77.0b (Codex P1): the evidence is redacted on the host (site_stig_answer_reads) and again in the task."""
        t = C.stig_answer_cross_act_task({"v": "V-1", "title": "t", "check_text": "grep GRUB2_PASSWORD /boot/grub2/user.cfg"},
                                         [{"cmd": "/usr/bin/grep GRUB2_PASSWORD /boot/grub2/user.cfg", "rc": 0,
                                           "out": "GRUB2_PASSWORD=grub.pbkdf2.sha512.10000.AABB.CCDD\nroot:$6$s$abcdefgh:1:"}])
        self.assertIn("GRUB2_PASSWORD=grub.pbkdf2.<hash removed>", t)
        self.assertIn("root:$<hash removed>:1:", t)
        for w in ("sha512.10000", "abcdefgh"):
            self.assertNotIn(w, t)

    def test_answers(self):
        self.assertEqual(C.stig_answer_cross_act_answer("NF\nroot owns it."), {"verdict": "NF", "why": "root owns it."})
        self.assertEqual(C.stig_answer_cross_act_answer("**Open**\n/srv/drop is not approved")["verdict"], "Open")
        self.assertEqual(C.stig_answer_cross_act_answer("unsure\nno output")["verdict"], "")
        self.assertEqual(C.stig_answer_cross_act_answer(""), {"verdict": "", "why": "no answer"})


class Rules(unittest.TestCase):
    def test_agree_on_every_host(self):
        r = C.stig_answer_cross_rules([row("h1.example.mil"), row("h2.example.mil")])[0]
        self.assertEqual((r["verdict"], r["code"], r["act"]), (C.AGREE, "NF on 2", "NF on 2"))

    def test_one_host_disagrees(self):
        r = C.stig_answer_cross_rules([row("h1.example.mil"), row("h2.example.mil", act="Open")])[0]
        self.assertEqual(r["verdict"], C.LOOK)
        self.assertEqual(r["why"], "code NF, ACT Open on h2")
        self.assertEqual(r["act"], "NF 1 · Open 1")

    def test_a_person_in_stig_manager_counts(self):
        r = C.stig_answer_cross_rules([row("h1", code="Open", act="Open", review=sm("pass"))])[0]
        self.assertEqual(r["verdict"], C.LOOK)
        self.assertIn("STIG Manager NF", r["why"])

    def test_evaluate_stig_open_is_not_a_disagreement(self):
        self.assertEqual(C.stig_answer_cross_rules([row("h1", es="Open")])[0]["verdict"], C.AGREE)

    def test_rhel8_not_tried_is_too_little(self):
        r = C.stig_answer_cross_rules([row("h1", code="", code_why="RHEL 8: no trial")])[0]
        self.assertEqual(r["verdict"], C.THIN)
        self.assertEqual(r["why"], "the code was not tried (RHEL 8: no trial) — ACT alone")

    def test_act_not_asked_past_the_cap(self):
        r = C.stig_answer_cross_rules([row("h1", act=None, act_note=C.CAP)])[0]
        self.assertEqual(r["verdict"], C.THIN)
        self.assertEqual(r["act"], "not asked (the cap)")

    def test_one_host_with_two_sources_is_enough(self):
        # the code runs on the trial host only: the others have Evaluate-STIG alone, and that never blocks Agree
        rs = C.stig_answer_cross_rules([row("h1"), row("h2", code="", act=None), row("h3", code="", act=None)])
        self.assertEqual(rs[0]["verdict"], C.AGREE)
        self.assertEqual((rs[0]["code"], rs[0]["act"]), ("NF", "NF"))

    def test_disagreements_first_in_each_stig(self):
        rs = C.stig_answer_cross_rules([row("h1", v="V-1"), row("h1", v="V-2", act="Open"), row("h1", v="V-3", code="")])
        self.assertEqual([r["verdict"] for r in rs], [C.LOOK, C.THIN, C.AGREE])


class Report(unittest.TestCase):
    def setUp(self):
        rs = C.stig_answer_cross_rules([row("h1", v="V-1"), row("h1", v="V-2", act="Open"),
                                        row("r8", v="V-9", code="", stig="RHEL 8 V2R9", evidence="root", evidence_from="DISA's")])
        self.r = C.stig_answer_cross_report(rs, 50, [{"host": "r8", "cmd": "/usr/bin/stat -c '%U' /etc/group-"}])

    def test_sections_tile_and_mail(self):
        self.assertEqual([s["title"] for s in self.r["sections"]],
                         ["Cross-check by STIG", "Cross-check — RHEL 9 V2R10 (2 rules, 1 need a look)", "Cross-check — RHEL 8 V2R9 (1 rule)"])
        self.assertEqual(self.r["sections"][0]["rows"], [["RHEL 9 V2R10 (1 host)", 2, 1, 1, 0], ["RHEL 8 V2R9 (1 host)", 1, 0, 0, 1]])
        self.assertEqual(self.r["tile"]["value"], "1 need a look")
        self.assertEqual(self.r["tile"]["note"], "1 agree, 1 too little to compare")
        self.assertEqual(len(self.r["mail"][1]["rows"]), 1)

    def test_every_command_and_the_cap_named(self):
        how = "\n".join(self.r["how"])
        self.assertIn("Ran on r8: /usr/bin/stat -c '%U' /etc/group-", how)
        self.assertIn("answer_keys_cross_check_act_max", how)
        self.assertIn("never the code", how)

    def test_the_detail_shows_what_act_was_given(self):
        d = self.r["sections"][2]["details"][0]
        self.assertEqual(d[2]["texts"][0]["text"], "r8 - root")

    def test_reader_text_short(self):
        for s in self.r["sections"]:
            self.assertNotIn(";", s.get("text") or "")
        for line in self.r["how"]:
            self.assertNotIn(";", line)


def group(rows, hosts=("r9a.example.mil", "r9b.example.mil"), label="RHEL 9 V2R10"):
    return {"label": label, "rep": hosts[0], "hosts": list(hosts), "rows": rows}


def ready(v="V-1", valid=True, hosts=("r9a.example.mil", "r9b.example.mil"), calls=None):
    return {"v": v, "sv": "SV-" + v[2:], "cat": "CAT II", "title": "t " + v, "check_text": "$ sudo stat -c \"%U\" /etc/group-",
            "block": "<AnswerKey/>", "hosts": list(hosts), "trial": {"valid": valid}, "gate": {"calls": calls or [["/usr/bin/stat", "-c", "%U", "/etc/group-"]]},
            "uses": {}}


def sm_rec(reviews):
    return {"status": "found", "reviews": reviews}


class Job(unittest.TestCase):
    def test_scan_time_is_the_newest_import(self):
        sm = sm_rec({"SV-1": {"result": "fail", "ts": "2026-11-10T10:00:00Z", "engine": {"product": "Evaluate-STIG"}},
                     "SV-2": {"result": "pass", "ts": "2026-11-12T10:00:00Z", "engine": {"product": "Evaluate-STIG"}},
                     "SV-3": {"result": "pass", "ts": "2026-11-17T10:00:00Z", "engine": None}})
        self.assertEqual(C.stig_answer_cross_scan_time(sm), "2026-11-12T10:00:00Z")
        self.assertEqual(C.stig_answer_cross_scan_time({}), "")

    def test_plan_reads_where_there_is_something_to_compare(self):
        info = {"r9a.example.mil": {"sm": sm_rec({})},
                "r9b.example.mil": {"sm": sm_rec({"SV-1": {"result": "pass", "ts": "2026-11-17T10:00:00Z", "engine": None},
                                                  "SV-9": {"result": "fail", "ts": "2026-11-10T10:00:00Z", "engine": {"product": "Evaluate-STIG"}}})},
                "r9c.example.mil": {"sm": sm_rec({})}}
        g = group([ready("V-1", hosts=("r9a.example.mil", "r9b.example.mil", "r9c.example.mil")),
                   ready("V-2", hosts=("r9a.example.mil", "r9b.example.mil")), {"v": "V-3", "block": ""}])
        p = C.stig_answer_cross_plan([g], info, NOW)
        self.assertEqual([x["id"] for x in p["r9a.example.mil"]], ["V-1", "V-2"])     # the trial host: every rule with a block
        self.assertEqual([x["id"] for x in p["r9b.example.mil"]], ["V-1"])            # a person's review newer than the scan
        self.assertNotIn("r9c.example.mil", p)
        self.assertEqual(p["r9a.example.mil"][0]["calls"], [["/usr/bin/stat", "-c", "%U", "/etc/group-"]])
        self.assertEqual(C.stig_answer_cross_plan([g], info, NOW, act_on=False), {})

    def _reads(self, out_a="root", out_b="root"):
        ev = lambda o: {"reads": {"V-1": {"from": "DISA's", "ran": [{"cmd": "/usr/bin/stat -c %U /etc/group-", "rc": 0, "out": o}]}}}
        return {"r9a.example.mil": ev(out_a), "r9b.example.mil": ev(out_b)}

    def test_hosts_with_the_same_evidence_share_one_call(self):
        a = C.stig_answer_cross_asks([group([ready()])], self._reads())
        self.assertEqual(len(a["asks"]), 1)
        self.assertEqual(a["asks"][0]["hosts"], ["r9a.example.mil", "r9b.example.mil"])
        t = a["asks"][0]["task"]
        self.assertIn("$ /usr/bin/stat -c %U /etc/group-   (exit 0)\nroot", t)
        self.assertNotIn("<AnswerKey", t)
        b = C.stig_answer_cross_asks([group([ready()])], self._reads(out_b="nobody"))
        self.assertEqual(len(b["asks"]), 2)

    def test_the_cap_and_act_off(self):
        a = C.stig_answer_cross_asks([group([ready()])], self._reads(out_b="nobody"), act_max=1)
        self.assertEqual(len(a["asks"]), 1)
        self.assertEqual(a["who"]["RHEL 9 V2R10|V-1|r9b.example.mil"], C.CAP)
        off = C.stig_answer_cross_asks([group([ready()])], self._reads(), act_on=False, act_off_why="ACT is off (site_act_enabled false)")
        self.assertEqual(off["asks"], [])
        self.assertEqual(off["who"]["RHEL 9 V2R10|V-1|r9a.example.mil"], "ACT is off (site_act_enabled false)")

    def test_no_evidence_no_call(self):
        reads = {"r9a.example.mil": {"reads": {"V-1": {"from": "", "ran": [], "why": "no command fits"}}}, "r9b.example.mil": {"failed": "sudo refused"}}
        a = C.stig_answer_cross_asks([group([ready()])], reads)
        self.assertEqual(a["asks"], [])
        self.assertEqual(a["who"]["RHEL 9 V2R10|V-1|r9a.example.mil"], "not asked: no read-only evidence (no command fits)")
        self.assertIn("sudo refused", a["who"]["RHEL 9 V2R10|V-1|r9b.example.mil"])

    def test_join_rules_and_into_the_report(self):
        g = group([ready()])
        info = {"r9a.example.mil": {"sm": sm_rec({}), "es": {"V-1": "Not Reviewed"}},
                "r9b.example.mil": {"sm": sm_rec({"SV-1": {"result": "fail", "ts": "2026-11-17T10:00:00Z", "engine": None},
                                                  # the scan's import (a result engine): the asset's last scan, before the person's review
                                                  "SV-2": {"result": "pass", "ts": SCAN, "engine": {"product": "Evaluate-STIG"}}}),
                                    "es": {"V-1": "Open"}}}
        reads = self._reads(out_b="nobody")
        asks = C.stig_answer_cross_asks([g], reads)
        rows = C.stig_answer_cross_join([g], info, reads, asks, {"x1": "NF\nroot owns it.", "x2": "Open\nnobody owns it."}, NOW)
        self.assertEqual([(x["host"], x["code"], x["act"]["verdict"], x["sm"]["counted"]) for x in rows],
                         [("r9a.example.mil", "NF", "NF", False), ("r9b.example.mil", "", "Open", True)])
        self.assertEqual(rows[1]["code_why"], "tried on r9a only")
        rs = C.stig_answer_cross_rules(rows)
        self.assertEqual(rs[0]["verdict"], C.AGREE)        # r9a: code NF = ACT NF; r9b: STIG Manager Open = ACT Open
        rep = C.stig_answer_cross_report(rs, 50, C.stig_answer_cross_ran(reads))
        self.assertIn("Ran on r9a.example.mil, r9b.example.mil: /usr/bin/stat -c %U /etc/group-", rep["how"])
        doc = {"web": {"title": "STIG answer keys: 1 ready to copy, 0 not drafted - 1 STIG, 2 hosts", "status": "ok", "summary": [{"label": "Ready to copy"}],
                       "sections": [{"title": "RHEL 9 V2R10 - answer keys to copy (1)", "columns": ["", "Rule", "CAT"], "kinds": ["x", "wrap", ""],
                                     "rows": [["", "V-1 (SV-1)", "CAT II"]]}, {"title": "How this was made", "lines": ["a"]}]},
               "report": {"title": "t", "summary": [], "sections": [{"title": "How this was made", "lines": []}]}, "xlsx": [], "lines": ["x"]}
        out = C.stig_answer_cross_into(doc, rep)
        w = out["web"]
        self.assertEqual(w["summary"][0]["label"], "Cross-check")
        self.assertEqual([x["title"] for x in w["sections"]][:2], ["Cross-check by STIG", "Cross-check — RHEL 9 V2R10 (1 rule)"])
        cp = [x for x in w["sections"] if "answer keys to copy" in x["title"]][0]
        self.assertEqual(cp["columns"], ["", "Rule", "Cross-check", "CAT"])
        self.assertEqual(cp["rows"][0], ["", "V-1 (SV-1)", C.AGREE, "CAT II"])
        self.assertEqual(cp["kinds"], ["x", "wrap", "wrap", ""])
        self.assertIn("answer_keys_cross_check_act_max", " ".join(w["sections"][-1]["lines"]))
        self.assertEqual(out["xlsx"][0]["name"], "Cross-check")
        self.assertEqual(out["report"]["sections"][0]["title"], "Cross-check by STIG")

    def test_a_disagreement_names_the_title(self):
        g = group([ready(valid=True)], hosts=("r9a.example.mil",))
        reads = {"r9a.example.mil": self._reads()["r9a.example.mil"]}
        asks = C.stig_answer_cross_asks([g], reads)
        rows = C.stig_answer_cross_join([g], {"r9a.example.mil": {"sm": sm_rec({})}}, reads, asks, {"x1": "Open\nnot root."}, NOW)
        rep = C.stig_answer_cross_report(C.stig_answer_cross_rules(rows), 50, [])
        doc = {"web": {"title": "STIG answer keys: 1 ready to copy, 0 not drafted - 1 STIG, 1 host", "status": "ok", "summary": [], "sections": []},
               "report": {"title": "STIG answer keys: 1 ready to copy, 0 not drafted - 1 STIG, 1 host", "status": "ok", "summary": [], "sections": []}}
        out = C.stig_answer_cross_into(doc, rep)
        self.assertEqual(out["web"]["title"], "STIG answer keys: 1 ready to copy, 1 need a look in the cross-check, 0 not drafted - 1 STIG, 1 host")
        self.assertEqual(out["web"]["status"], "warning")


class Reads(unittest.TestCase):
    """roles/check_stig/library/site_stig_answer_reads.py: DISA's commands, the read-only grammar, the runner."""

    def test_disa_commands_as_literal_argv(self):
        self.assertEqual(R.pipeline_of('sudo stat -c "%U" /etc/group-')[0], [["/usr/bin/stat", "-c", "%U", "/etc/group-"]])
        self.assertEqual(R.pipeline_of("sysctl kernel.dmesg_restrict")[0], [["/usr/sbin/sysctl", "-n", "kernel.dmesg_restrict"]])
        self.assertEqual(R.pipeline_of("sudo auditctl -l | sudo grep /etc/passwd")[0], [["/usr/sbin/auditctl", "-l"], ["/usr/bin/grep", "/etc/passwd"]])
        self.assertEqual(R.pipeline_of("grep -ir permitrootlogin /etc/ssh/sshd_config 2>/dev/null")[0],
                         [["/usr/bin/grep", "-i", "-r", "permitrootlogin", "/etc/ssh/sshd_config"]])

    def test_anything_else_is_not_run(self):
        for ln, why in (("cat /etc/redhat-release", "cat is not on the read-only list"), ("grep x /etc/a; rm -rf /", "a shell operator (;)"),
                        ("echo $(id)", "a substitution"), ("sysctl -n kernel.x > /tmp/out", "a shell operator (>)"),
                        ("find /etc -exec ls {} +", "find: -exec is not allowed"), ("find / -delete", "find: -delete is not allowed"),
                        ("rpm -qa", "rpm: only"), ("systemctl stop sshd", "systemctl: only"), ("auditctl -D", "auditctl: only -l"),
                        ("grep -f /etc/x /etc/y", "grep: the flags"), ("stat /etc/x", "stat: only -c"), ("awk 1 /etc/passwd", "awk is not")):
            st, w = R.pipeline_of(ln)
            self.assertIsNone(st, ln)
            self.assertIn(why, w, ln)

    def test_the_grammar_is_the_gates(self):
        # the same cases site_stig_answer_host's replay_ok takes (and refuses)
        import site_stig_answer_host as H
        for argv in (["/usr/sbin/auditctl", "-l"], ["/usr/bin/systemctl", "is-enabled", "sshd.service"], ["/usr/bin/getent", "passwd", "root"],
                     ["/usr/bin/id", "-u", "root"], ["/usr/bin/stat", "-c", "%U %G", "/etc/x"], ["/usr/bin/sysctl", "-n", "net.ipv4.ip_forward"],
                     ["/usr/bin/systemctl", "stop", "sshd"], ["/usr/sbin/auditctl", "-D"], ["/usr/bin/getent", "hosts"]):
            self.assertEqual(R.gram(argv[0], argv[1:]) == "", H.replay_ok(argv), argv)

    def test_plan_disa_first_else_the_codes(self):
        self.assertEqual(R.plan({"check_text": "$ sudo rpm -q telnet-server", "calls": [["/usr/bin/rpm", "-q", "x"]]})[:2],
                         ("DISA's", [[["/usr/bin/rpm", "-q", "telnet-server"]]]))
        src, todo, refused = R.plan({"check_text": "$ sudo cat /etc/x", "calls": [["/usr/bin/stat", "-c", "%a", "/etc/x"], ["/usr/bin/rm", "-rf", "/"]]})
        self.assertEqual((src, todo), ("the code's", [[["/usr/bin/stat", "-c", "%a", "/etc/x"]]]))
        self.assertEqual(refused, ["sudo cat /etc/x - cat is not on the read-only list"])
        self.assertEqual(R.plan({"check_text": "Verify it by hand.", "calls": []})[:2], ("", []))

    def test_a_stage_writing_much_to_stderr_does_not_stop_the_pipeline(self):
        # 1.77.0f (the review of e908ae06): an earlier stage's stderr was a pipe nobody read - past 64 KiB it blocked
        # and the pipeline ran out of time (rc 124)
        import time
        t = time.time()
        r = R.run_pipeline([[sys.executable, "-c", "import sys; sys.stderr.write('e' * 300000); print('x')"],
                            ["/usr/bin/grep", "-c", "x"]], 8, 4000)
        self.assertEqual((r["rc"], r["out"].strip()), (0, "1"))
        self.assertLess(time.time() - t, 5)
        # the last stage's stderr is its output when it printed nothing else - unless 2>/dev/null followed it
        missing = "/nonexistent-%d/x" % os.getpid()
        st, why = R.pipeline_of("grep -r permitrootlogin %s" % missing)
        self.assertIn("No such file", R.run_pipeline(st, 8, 4000)["out"])
        st, why = R.pipeline_of("grep -r permitrootlogin %s 2>/dev/null" % missing)
        self.assertEqual(st.quiet, frozenset([0]))
        r = R.run_pipeline(st, 8, 4000)
        self.assertEqual((r["out"], r["cmd"]), ("", "/usr/bin/grep -r permitrootlogin %s 2>/dev/null" % missing))
        st, why = R.pipeline_of("auditctl -l 2> /dev/null | grep -c x")
        self.assertEqual((st, st.quiet), ([["/usr/sbin/auditctl", "-l"], ["/usr/bin/grep", "-c", "x"]], frozenset([0])))

    def test_review_of_d5801557_reads(self):
        # 1.77.0h (Codex on d5801557): the command as shown is redacted (a value DISA's grep quotes); the argv runs as is
        missing = "/nonexistent-%d/app.conf" % os.getpid()
        r = R.run_pipeline([["/usr/bin/grep", "-e", "password=BlueCanary", missing]], 8, 4000)
        self.assertNotIn("BlueCanary", r["cmd"])
        self.assertIn("/usr/bin/grep -e password=<removed>", r["cmd"])
        # ... and in "How this was made"
        reads = {"h1": {"reads": {"V-1": {"ran": [{"cmd": "/usr/bin/grep -e password=BlueCanary /etc/app.conf", "rc": 1, "out": ""}]}}}}
        self.assertNotIn("BlueCanary", repr(C.stig_answer_cross_ran(reads)))
        # output past READ_MAX: the line the cut would split is left out, so no start of a URL's password shows
        big = "import sys; sys.stdout.write('ok\\nhttps://u:' + 'A' * (1 << 20) + '@host\\n')"
        r = R.run_pipeline([[sys.executable, "-c", big]], 20, 4000)
        self.assertEqual(r["rc"], 0)
        self.assertNotIn("AAAA", r["out"])
        self.assertTrue(r["out"].startswith("ok"))

    def test_runs_without_a_shell_and_masks_hashes(self):
        r = R.run_pipeline([["/usr/bin/getent", "passwd"], ["/usr/bin/grep", "-c", "^root:"]], 10, 4000)
        self.assertEqual((r["rc"], r["out"].strip()), (0, "1"))
        self.assertEqual(R.mask("root:$6$salt$abcDEF/x.y:19000::"), "root:$<hash removed>:19000::")
        # 1.77.0b (Codex P1): the GRUB hash grep returns from user.cfg - the shared redaction (site_redact)
        self.assertEqual(R.mask("GRUB2_PASSWORD=grub.pbkdf2.sha512.10000.AABB.CCDD"), "GRUB2_PASSWORD=grub.pbkdf2.<hash removed>")
        self.assertEqual(R.mask("ldap_default_authtok = x"), "ldap_default_authtok = <removed>")
        self.assertEqual(R.disa_lines("Check:\n\n$ sudo stat -c \"%a\" /etc/x\n\n644\n$ $ awk x\n# grep a /etc/b"),
                         ['sudo stat -c "%a" /etc/x', "awk x", "grep a /etc/b"])


if __name__ == "__main__":
    unittest.main()
