"""1.77.0h: how much of a host's text the STIG answer keys reports show (answer_keys_evidence), and the call sites the
second Codex pass on the release commit found. The email and its workbook never carry a host's text; the website shows
it redacted, at most 20 lines and 2000 characters a rule (web, the default) or not at all (none)."""
import json
import os
import re
import sys
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "plugins", "filter"), os.path.join(REPO, "plugins", "module_utils"),
                os.path.join(REPO, "roles", "check_stig", "library"), os.path.join(HERE, "stig")]
import ansible.module_utils  # noqa: E402 - the repository's module_utils as Ansible gives them (ansible.cfg)
ansible.module_utils.__path__.append(os.path.join(REPO, "plugins", "module_utils"))
import site_redact as X  # noqa: E402
import stig_answer_filters as A  # noqa: E402
import stig_answer_try_filters as T  # noqa: E402
import stig_answer_cross_filters as C  # noqa: E402
import make_cklb as M  # noqa: E402

SECRET = "BlueCanary"
# a host's text, long: 40 lines, a secret in the first (redacted) and words that must not reach the email
HOST_TEXT = "token=%s\n" % SECRET + "\n".join("hostline%02d read from the host" % i for i in range(40))


def _all_text(x):
    return json.dumps(x, ensure_ascii=False)


def _report_rows():
    """stig_answer_report's rows: one ready (the trial read a host's text), one not drafted (the trial's error)."""
    ready = {"v": "V-1", "sv": "SV-1", "title": "t1", "cat": "CAT II", "sm": "", "es": "Open", "expected": "Open", "by": "pattern",
             "block": "<AnswerKey/>", "uses": {}, "gate": {"ok": True, "why": [], "used": ["Get-Content"]},
             "trial": {"ok": True, "valid": True, "read": HOST_TEXT, "seconds": 1}, "check_text": "check", "why": ""}
    nod = {"v": "V-2", "sv": "SV-2", "title": "t2", "cat": "CAT II", "block": "", "gate": {"ok": True}, "trial": {},
           "why": "Not drafted - the trial on the host failed: hostline-error %s." % X.redact("token=" + SECRET),   # as stig_answer_rows makes it
           "why_mail": "Not drafted - the trial on the host failed."}
    return [ready, nod]


def _try_rows():
    af = M.AF_COMMENT % "NotAFinding"
    a = {"scanned": True, "items": [{"v": "V-1", "key": "SV-1", "result": "fail", "severity": "medium", "title": "t"}]}
    b = {"scanned": True, "items": [{"v": "V-1", "key": "SV-1", "result": "pass", "severity": "medium", "title": "t",
                                     "comments": af + "\n" + HOST_TEXT,
                                     "details": "ValidationCode output:\n" + HOST_TEXT + "\n----------\nmore"}]}
    return T.stig_answer_try_rows([{"host": "h1", "without": a, "with": b},
                                   {"host": "h2", "without": {"scanned": False, "why": "hostline-error from Evaluate-STIG"}, "with": {}}])


def _cross_rules(decides="hostline07 read from the host"):
    row = {"v": "V-1", "sv": "SV-1", "cat": "CAT II", "title": "t", "stig": "RHEL 9", "host": "h1", "es": "Open",
           "sm": {"counted": False, "word": "none", "short": ""}, "code": "NF", "code_why": "", "act": {"verdict": "Open", "why": "hostline why"},
           "act_note": "", "evidence_from": "DISA's", "rep": "h1", "code_read": "hostline-read",
           "evidence": C._ev_text([{"cmd": "/usr/bin/cat /etc/x", "rc": 0, "out": HOST_TEXT}]),   # as stig_answer_cross_join makes it
           "ran": [{"cmd": "/usr/bin/cat /etc/x", "rc": "0", "lines": 41, "out": X.redact(HOST_TEXT)}]}
    row["act"]["decides"] = decides
    return C.stig_answer_cross_rules([row])


class Email(unittest.TestCase):
    """The email and its workbook: statuses, counts and rule ids - never a host's text."""

    def check(self, doc_mail, xlsx):
        for part in (doc_mail, xlsx):
            t = _all_text(part)
            self.assertNotIn("hostline", t)
            self.assertNotIn(SECRET, t)

    def test_the_answer_keys_report(self):
        for ev in ("web", "none"):
            with self.subTest(evidence=ev):
                d = A.stig_answer_report(_report_rows(), "h1", "DEFAULT", {}, evidence=ev)
                self.check(d["report"], d["xlsx"])
                self.assertIn("Not drafted - the trial on the host failed.", _all_text(d["report"]))

    def test_the_report_of_many_hosts(self):
        groups = [{"label": "RHEL 9", "bench": "b", "rev": "r", "hosts": ["h1"], "rep": "h1", "rows": _report_rows(), "ev": {}}]
        failed = [{"host": "h9", "why": "not reached: hostline ssh said %s" % SECRET,
                   "why_mail": "not reached (the job's output has why)"}]     # as answer_keys.yml sets it
        for ev in ("web", "none"):
            with self.subTest(evidence=ev):
                d = A.stig_answer_report_multi(groups, "DEFAULT", True, failed, [], {}, "", {}, evidence=ev)
                self.check(d["report"], d["xlsx"])
                self.assertIn("not reached", _all_text(d["report"]))

    def test_try_my_answer_file(self):
        for ev in ("web", "none"):
            with self.subTest(evidence=ev):
                d = T.stig_answer_try_report(_try_rows(), {}, ev)
                self.check(d["report"], d["xlsx"])
                self.assertIn("Evaluate-STIG did not run without answer files.", _all_text(d["report"]))

    def test_the_cross_check(self):
        for ev in ("web", "none"):
            with self.subTest(evidence=ev):
                d = C.stig_answer_cross_report(_cross_rules(), 50, [], ev)
                self.check(d["mail"], d["xlsx"])
                self.assertNotIn("ACT's why", d["xlsx"]["columns"])


class Web(unittest.TestCase):
    """The website: a host's text redacted and capped (web), or not shown (none)."""

    def texts(self, doc):
        return _all_text(doc)

    def capped(self, t):
        self.assertIn("hostline00", t)
        self.assertNotIn("hostline25", t)      # past 20 lines
        self.assertIn(X.MORE, t)
        self.assertNotIn(SECRET, t)

    def test_the_answer_keys_report(self):
        self.capped(self.texts(A.stig_answer_report(_report_rows(), "h1", "DEFAULT", {})["web"]))
        t = self.texts(A.stig_answer_report(_report_rows(), "h1", "DEFAULT", {}, evidence="none")["web"])
        self.assertNotIn("hostline", t)
        self.assertIn(X.EVIDENCE_OFF.split(" - ")[0], t)

    def test_the_trial_off(self):
        """1.76.4's answer_keys_trial false (the default): a block not tried shows no read - in either mode, and never in
        the email."""
        rows = _report_rows()
        rows[0] = dict(rows[0], untried=True, trial={"ran": False, "off": True, "ok": False, "valid": None, "read": "",
                                                     "error": "the trial is off (answer_keys_trial)"})
        for ev in ("web", "none"):
            with self.subTest(evidence=ev):
                d = A.stig_answer_report(rows, "h1", "DEFAULT", {}, evidence=ev)
                self.assertNotIn("hostline0", _all_text(d["web"]))       # no read shown for a block not tried
                self.assertNotIn("hostline", _all_text(d["report"]) + _all_text(d["xlsx"]))
                self.assertIn("Not tried", _all_text(d["web"]))

    def test_try_my_answer_file(self):
        self.capped(self.texts(T.stig_answer_try_report(_try_rows(), {})["web"]))
        t = self.texts(T.stig_answer_try_report(_try_rows(), {}, "none")["web"])
        self.assertNotIn("hostline", t)

    def test_the_cross_check(self):
        # 1.88.0 (the director: state and counts only): each command, its exit code and how many lines - and the one line
        # ACT named, redacted; never the rest of the output
        t = self.texts(C.stig_answer_cross_report(_cross_rules(), 50, [])["sections"])
        self.assertIn("/usr/bin/cat /etc/x  (exit 0, 41 lines)", t)
        self.assertIn("hostline07 read from the host", t)
        for i in (0, 6, 8, 39):
            self.assertNotIn("hostline%02d" % i, t)
        self.assertNotIn(SECRET, t)
        self.assertIn("hostline-read", t)                 # the code's own read line in the trial
        # ACT names the secret's line (its answer is redacted, as stig_answer_cross_act_answer does): shown redacted only
        t = self.texts(C.stig_answer_cross_report(_cross_rules(C.stig_answer_cross_act_answer("Open\nw\ntoken=" + SECRET)["decides"]), 50, [])["sections"])
        self.assertNotIn(SECRET, t)
        self.assertIn("token=<removed>", t)
        # a line it names raw never brings the secret either
        self.assertNotIn(SECRET, self.texts(C.stig_answer_cross_report(_cross_rules("token=" + SECRET), 50, [])["sections"]))
        # none: no command, no line, no read (ACT is not asked then; its why in this made-up row is the only host word left)
        t = self.texts(C.stig_answer_cross_report(_cross_rules(), 50, [], "none")["sections"])
        for w in ("hostline0", "/usr/bin/cat", "hostline-read"):
            self.assertNotIn(w, t)

    def test_cap(self):
        self.assertEqual(X.cap("a\nb"), "a\nb")
        self.assertEqual(X.cap("x" * 2001), "x" * 2000 + "\n" + X.MORE)
        self.assertEqual(X.cap("\n".join("l%d" % i for i in range(25))).splitlines()[-1], X.MORE)
        self.assertEqual(len(X.cap("\n".join("l%d" % i for i in range(25))).splitlines()), 21)


class Setting(unittest.TestCase):
    """answer_keys_evidence: its default, its check, where the tasks pass it, ACT off with none."""

    def read(self, *p):
        with open(os.path.join(REPO, *p)) as f:
            return f.read()

    def test_the_default_and_its_check(self):
        d = yaml.safe_load(self.read("roles", "check_stig", "defaults", "main.yml"))
        self.assertEqual(d["answer_keys_evidence"], "web")
        for f in ("answer_keys.yml", "answer_keys_try.yml"):
            with self.subTest(f=f):
                self.assertIn("answer_keys_evidence | default('web') in ['web', 'none']", self.read("roles", "check_stig", "tasks", f))

    def test_the_tasks_pass_it(self):
        for f, call in (("answer_keys_report.yml", "stig_answer_report_multi"), ("answer_keys_try.yml", "stig_answer_try_report"),
                        ("answer_keys_cross_act.yml", "stig_answer_cross_report")):
            t = self.read("roles", "check_stig", "tasks", f)
            with self.subTest(f=f):
                i = t.index(call)
                self.assertIn("answer_keys_evidence | default('web')", t[i:i + 900])
        t = self.read("roles", "check_stig", "tasks", "answer_keys_cross.yml")
        self.assertIn("and answer_keys_evidence | default('web') != 'none'", t)
        self.assertIn("not asked (answer_keys_evidence: none)", t)
        # with ACT off nothing is read for the cross-check
        self.assertEqual(C.stig_answer_cross_plan([{"rep": "h1", "rows": _report_rows()}], {}, "", False), {})


class CallSites(unittest.TestCase):
    """1.77.0h (Codex, second pass on 8cae9b62): what a module or task took or cut before it was redacted."""

    def read(self, *p):
        with open(os.path.join(REPO, *p)) as f:
            return f.read()

    def test_the_host_modules(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("h", os.path.join(REPO, "roles", "check_stig", "library", "site_stig_answer_host.py"))
        h = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(h)
        # a replayed output cut at a line's end (what the trial reads may be shown)
        self.assertEqual(h._whole_lines(b"ok\nhttps://u:" + b"A" * 100 + b"@host\n", 50), b"ok\n")
        self.assertEqual(h._whole_lines(b"short\n", 50), b"short\n")
        # a refused path: redacted, then cut to 80
        why = h.blind("x", [], [{"cmd": "Get-Content", "path": "https://u:" + "A" * 120 + "@host", "literal": True}])
        self.assertNotIn("AAAA", why)
        why = h.blind("x", [], [{"cmd": "Get-Content", "path": "$p" + "token=" + SECRET, "literal": False}])
        self.assertNotIn(SECRET, why)
        src = self.read("roles", "check_stig", "library", "site_stig_answer_host.py")
        self.assertIn('g["why"] = [redact(str(w), backstop=False)[:300] for w in g["why"]]', src)

    def test_answer_keys_and_engines(self):
        import site_stig_scan as S
        st = {"rules": [{"v": "V-1", "rule": "R", "comments": "Evaluate-STIG answer file for key 'token=%s'" % SECRET, "details": ""}]}
        self.assertNotIn(SECRET, _all_text(S.answered_from_files(st)))
        import site_stigman_read as R
        self.assertNotIn(SECRET, _all_text(R.engine_of({"product": "scanner token=" + SECRET, "type": "scan"})))
        self.assertEqual(R.engine_of({"product": "Evaluate-STIG", "type": "script"}), {"product": "Evaluate-STIG", "type": "script"})

    def test_acts_reason(self):
        t = self.read("roles", "check_stig", "tasks", "answer_keys_cross_act.yml")
        self.assertIn("(a.reason | default('') | string | site_redact | truncate(200))", t)

    def test_redact_all(self):
        self.assertEqual(X.redact_all({"password": 1, "https://u:%s@host" % SECRET: "failed", "token": None, "cache_credentials": False}),
                         {"password": X.MARK, "https://u:<removed>@host": "failed", "token": None, "cache_credentials": False})


class ThirdPass(unittest.TestCase):
    """1.77.0i (Codex, the last pass on 569031bc, scoped to call sites and exposure): a host's or a service's words that
    still reached the email, the workbook, a task result or ACT."""

    def read(self, *p):
        with open(os.path.join(REPO, *p)) as f:
            return f.read()

    def _doc(self):
        how = {"title": "How this was made", "lines": ["base"]}
        part = {"title": "STIG answer keys: 1 ready to copy, 0 not drafted", "status": "ok", "summary": [], "sections": [how]}
        return {"web": dict(part), "report": dict(part), "xlsx": [], "lines": []}

    def test_the_commands_as_run_stay_off_the_email(self):
        ran = [{"host": "h1", "cmd": "/usr/bin/grep x /etc/globbed-customer.conf"}]
        for ev in ("web", "none"):
            with self.subTest(evidence=ev):
                cr = C.stig_answer_cross_report(_cross_rules(), 50, ran, ev)
                out = C.stig_answer_cross_into(self._doc(), cr)
                self.assertNotIn("globbed-customer", _all_text(out["report"]))
                self.assertIn("The job ran 1 read-only command for the evidence.", _all_text(out["report"]))
                self.assertEqual("globbed-customer" in _all_text(out["web"]), ev == "web")

    def _joined(self, reads):
        groups = [{"label": "RHEL 9", "rep": "h1", "rows": _report_rows()}]
        asks = C.stig_answer_cross_asks(groups, reads, 50, True)
        return C.stig_answer_cross_rules(C.stig_answer_cross_join(groups, {"h1": {}}, reads, asks, {}))

    def test_the_workbook_says_a_status_for_a_read_that_failed(self):
        for reads, word in (({"h1": {"failed": "connection reset by hostline-proxy"}}, "not asked: the reads failed"),
                            ({"h1": {"reads": {"V-1": {"why": "hostline refused"}}}}, "not asked: no read-only evidence")):
            with self.subTest(word=word):
                cr = C.stig_answer_cross_report(self._joined(reads), 50, [])
                self.assertNotIn("hostline", _all_text(cr["xlsx"]))
                self.assertIn(word, _all_text(cr["xlsx"]))
                self.assertIn("hostline", _all_text(cr["sections"]))       # the website keeps why, redacted

    def test_a_result_stig_manager_does_not_define(self):
        w = C.stig_answer_cross_review({"result": "error token=" + SECRET})["word"]
        self.assertTrue(w.startswith("unknown result ("), w)
        self.assertNotIn("token", w)
        self.assertTrue(C.stig_answer_cross_review({"result": "fixed"})["word"].startswith("Fixed ("))
        self.assertEqual(C.stig_answer_cross_review({"result": "fail"})["short"], "Open")

    def test_a_gate_refusal_has_a_reason_for_the_email(self):
        picks = [{"v": "V-1", "sv": "SV-1", "ok": True, "expected": "Open", "title": "t"}]
        drafts = {"V-1": {"by": "pattern", "code": "x", "true": ("NotAFinding", "ok"), "false": ("Open", "no")}}
        host = {"gate": {"V-1": {"ok": False, "why": ["parser stopped at hostline-marker token=" + SECRET]}}}
        r = A.stig_answer_rows(picks, drafts, host)[0]
        self.assertEqual(r["why_mail"], "Not drafted - the read-only check refused the draft. Write this one by hand.")
        self.assertNotIn(SECRET, r["why"])
        d = A.stig_answer_report([r], "h1", "DEFAULT", {}, evidence="none")
        self.assertNotIn("hostline", _all_text(d["report"]) + _all_text(d["xlsx"]) + _all_text(d["web"]))

    def test_a_host_that_failed_has_a_reason_for_the_email(self):
        m = A.stig_answer_merge([{"host": "h9", "ok": False, "why": "permission denied opening /srv/hostline-a"}])
        groups = [{"label": "RHEL 9", "bench": "b", "rev": "r", "hosts": ["h1"], "rep": "h1", "rows": _report_rows(), "ev": {}}]
        for ev in ("web", "none"):
            with self.subTest(evidence=ev):
                d = A.stig_answer_report_multi(groups, "DEFAULT", True, m["failed"], [], {}, "", {}, evidence=ev)
                self.assertNotIn("hostline-a", _all_text(d["report"]))
                self.assertIn("it did not work (the job's output has why)", _all_text(d["report"]))
                self.assertEqual("hostline-a" in _all_text(d["web"]), ev == "web")
        # a failed host whose producer set no why_mail: the fixed words, never its message
        d = A.stig_answer_report_multi(groups, "DEFAULT", True, [{"host": "h8", "why": "opening /srv/hostline-b"}], [], {}, "", {},
                                       evidence="none")
        self.assertNotIn("hostline-b", _all_text(d["report"]) + _all_text(d["web"]))
        t = self.read("roles", "check_stig", "tasks", "answer_keys.yml")
        self.assertEqual(t.count("why_mail: "), 4)       # not reached, a Windows that is no Windows Server 2019, 2022 or 2025 (1.105.0), and both rescues

    def test_a_key_named_in_a_comment(self):
        for key, shown in (("local tenant hostline", "another key"), ("default", "DEFAULT")):
            with self.subTest(key=key):
                tried = _try_rows()
                tried["rows"][0]["key"] = key
                d = T.stig_answer_try_report(tried, {}, "none")
                self.assertNotIn("hostline", _all_text(d["xlsx"]) + _all_text(d["web"]))
                self.assertIn(shown, _all_text(d["xlsx"]))
        tried = _try_rows()
        tried["rows"][0]["key"] = "SITEKEY"
        self.assertIn("SITEKEY", _all_text(T.stig_answer_try_report(tried, {"key": "SITEKEY"})["xlsx"]))

    def test_the_rule_text_act_reads(self):
        rule = {"v": "V-1", "sv": "SV-1", "title": "t token=" + SECRET,
                "check_text": "Verify the password minimum length:\n$ grep minlen /etc/security/pwquality.conf\ninspect token=" + SECRET}
        for task in (C.stig_answer_cross_act_task(rule, []), A.stig_answer_act_task(rule)):
            self.assertNotIn(SECRET, task)
            self.assertIn("Verify the password minimum length:\n$ grep minlen /etc/security/pwquality.conf", task)

    def test_review_excerpts(self):
        import site_stigman_read as R
        line = "Set by the STIG recheck (site-automation), approved in AAP: token=%s - Open." % SECRET
        rec = R.reviews_of([{"ruleId": "SV-1r1_rule", "result": "fail", "comment": line}])["SV-1"]
        self.assertNotIn(SECRET, rec["ours"])
        self.assertEqual(rec["ours_said"], R.what_it_said(line))

    def test_the_scans_items(self):
        import site_stig_scan as S
        st = {"rules": [{"rule": "SV-1r1_rule", "v": "V-1", "stigid": "x", "severity": "medium", "title": "check token=" + SECRET,
                         "result": "fail", "details": "", "comments": "", "check_text": "inspect token=" + SECRET,
                         "fix_text": "set token=" + SECRET}]}
        self.assertNotIn(SECRET, _all_text(S.checklist_items(st)))
        self.assertNotIn(SECRET, _all_text(S.checklist_items(st, texts=True)))

    def test_a_try_scan_that_failed(self):
        tried = T.stig_answer_try_rows([{"host": "h3", "without": {"unreachable": True, "msg": "banner exchange: token=" + SECRET},
                                         "with": {}}])
        self.assertNotIn(SECRET, _all_text(tried))
        self.assertIn("banner exchange", _all_text(tried))
        t = self.read("roles", "check_stig", "tasks", "answer_keys_try.yml")
        for reg in ("register: _akt_without", "register: _akt_with\n"):
            i = t.index(reg)
            self.assertIn("no_log: true", t[i:i + 400])

    def test_acts_reply_and_the_probe(self):
        t = self.read("roles", "check_stig", "tasks", "answer_keys_cross_act.yml")
        self.assertIn("(a.raw | default('') | string | site_redact) if a.ran", t)
        self.assertIn("    apply:\n      no_log: true", t)
        src = self.read("roles", "check_stig", "library", "site_stig_answer_host.py")
        self.assertIn('redact("; ".join(str(x) for x in said), backstop=False)[:160]', src)


if __name__ == "__main__":
    unittest.main()
