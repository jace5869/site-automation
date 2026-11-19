"""The cause cards (site_filters.report_cause_cards, roles/site_email/templates/report_causes.html.j2): ACT's causes
(act_causes) shown one card per cause, in the data's order - never ACT's - with "What to do first", a table for the email
and a sheet for the workbook; on the website's looks (atlas, brief, ops) and in the email's. The answers are
test_act_causes.EXAMPLES, the ones the parser is tested on."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
sys.path.insert(0, HERE)
import site_filters as sf  # noqa: E402
import test_act_causes as tac  # noqa: E402


def items():
    out = []
    for i in range(1, 9):
        out.append({"id": "P%d" % i, "level": "error" if i in (1, 2, 6) else ("critical" if i == 8 else "warning"),
                    "title": "line %d" % i, "hosts": ["app01", "app02"] if i < 6 else ["web01"], "count": 100 + i,
                    "examples": [{"time": "11:5%d" % (i % 10), "sort": "2026-11-19T11:5%dZ" % (i % 10), "host": "app01", "message": "msg <%d>" % i}]})
    return out


class Cards(unittest.TestCase):
    def cards(self, example="good"):
        return sf.report_cause_cards(sf.act_causes.parse(tac.EXAMPLES[example], tac.IDS), items(), {"overall": "Do this first."})

    def test_the_datas_order(self):
        r = self.cards()
        # P8 (critical, no cause): its own card, first by level; then the two causes by level and confidence
        got = [(k["n"], k["key"], k["patterns_text"], k["status"], k["unexplained"]) for k in r["cards"]]
        self.assertEqual(got, [(1, "item-p8", "P8", "critical", True), (2, "pulseaudio-selinux", "P1-P5", "critical", False),
                               (3, "ssh-probe", "P6", "critical", False)])
        self.assertTrue(r["cards"][0]["title"].startswith("Not explained: "))
        self.assertEqual(r["noise"], ["P7"])
        self.assertEqual(r["by_item"]["P3"][:2], ["cause-2", "Cause 2"])
        self.assertNotIn("P7", r["by_item"])

    def test_a_card(self):
        k = self.cards()["cards"][1]
        self.assertEqual((k["hosts"], k["lines"], k["conf_text"], k["conf_status"]), (["app01", "app02"], 515, "85% high", "ok"))
        self.assertEqual([e["t"] for e in k["evidence"]], ["11:55", "11:54", "11:53", "11:52", "11:51"])      # newest first, 5 at most
        self.assertEqual(k["more"], 515 - 5)
        self.assertEqual([s["n"] for s in k["steps"]], [1, 2])

    def test_first_email_and_sheet(self):
        r = self.cards()
        self.assertEqual(r["first"]["card"], "cause-2")             # the top card with a step (the unexplained one has none)
        web = r["web"]
        self.assertEqual(web[0]["cause_overall"], ["Do this first."])
        self.assertEqual(web[1]["count_text"], "2 causes · 7 patterns")                 # P7 is noise, said apart
        mail = r["email"]
        self.assertEqual(mail[0]["lines"][-1], "1. Confirm the SELinux denial: sealert -l <id>")
        self.assertEqual(mail[1]["rows"][1][-1], "Confirm the SELinux denial: sealert -l <id>")
        self.assertEqual(r["sheet"]["columns"][:3], ["#", "Cause", "Patterns"])
        self.assertEqual(len(r["sheet"]["rows"]), 1 + 2 + 1)        # a row per step (a card with none: one row)

    def test_an_older_answer(self):
        r = self.cards("old")
        self.assertEqual([k["key"] for k in r["cards"] if not k["unexplained"]], ["p1", "p2"])

    def test_noise_only_and_mixed_batches(self):
        import vmware_alarm_filters as va
        # Codex: an answer that says every pattern is harmless - no card, the harmless list still on the page
        r = sf.report_cause_cards(sf.act_causes.parse({"causes": [], "noise": ["P7"], "noise_why": {"P7": "boot"}}, tac.IDS), items(), {})
        import test_report_fit as T
        h = T.EmailTiles()._env().get_template("report_atlas.html.j2").render(
            _report={"title": "t", "status": "ok", "summary": [], "sections": r["web"]}, _email_footer="f", _page_logo_cid="",
            _page_runs=[], report_email_header_text="x")
        self.assertIn("Harmless (1):</b> P7 - boot", h)
        # Codex: a batch of the older shape beside one of causes - both explained; by_id as the merged cause says
        probs = [{"id": "P%d" % i} for i in range(1, 9)]
        old = va.act_analysis_parse('{"items": [{"id": "P1", "cause": "Disk full", "fix": "Free /var", "confidence": 90}]}', probs[:1])
        new1 = va.act_analysis_parse({"causes": [{"key": "dns", "title": "DNS slow", "patterns": ["P2"], "confidence": 50,
                                                  "steps": [{"do": "Check resolv.conf"}]}]}, probs)
        new2 = va.act_analysis_parse({"causes": [{"key": "dns", "title": "DNS down", "patterns": ["P3"], "confidence": 90,
                                                  "steps": [{"do": "Restart named"}]}]}, probs)
        m = va.act_batches_merge([{"ids": ["P1"], "ran": True, "parsed": old}, {"ids": ["P2"], "ran": True, "parsed": new1},
                                  {"ids": ["P3"], "ran": True, "parsed": new2}])
        self.assertEqual(sorted(p for c in m["causes"] for p in c["patterns"]), ["P1", "P2", "P3"])
        self.assertEqual((m["by_id"]["P2"]["cause"], m["by_id"]["P3"]["cause"]), ("DNS down", "DNS down"))

    def test_a_long_command_never_cut(self):
        import vmware_alarm_filters as va
        heredoc = "cat > /etc/x <<'EOF'\na = 1\nEOF"
        ans = {"causes": [{"key": "k", "title": "T", "patterns": ["P1"], "confidence": 80,
                           "steps": [{"do": "Write the file.", "cmd": heredoc}, {"do": "Check it.", "cmd": "cat /etc/x"}]}]}
        self.assertEqual(sf._step_line({"do": "Write the file.", "cmd": heredoc}),
                         "Write the file (a command of 3 lines: on the report website)")
        r = va.act_analysis_parse(ans, [{"id": "P1"}])["by_id"]["P1"]
        self.assertEqual(r["fix"], "1. Write the file. (a command of 3 lines: in steps) 2. Check it. (cat /etc/x)")
        self.assertEqual(r["steps"][0]["cmd"], heredoc)

    def test_on_every_look(self):
        import test_report_fit as T
        env = T.EmailTiles()._env()
        r = self.cards()
        doc = {"title": "t", "status": "critical", "summary": [], "sections": r["web"]}
        for look in ("atlas", "brief", "ops"):
            h = env.get_template("report_%s.html.j2" % look).render(_report=doc, _email_footer="f", _page_logo_cid="", _page_runs=[],
                                                                  report_email_header_text="x")
            self.assertIn('id="cause-2"', h, look)
            self.assertIn('<div class="code cz-cmd"><pre>sealert -l &lt;id&gt;</pre>', h, look)
            self.assertIn("Not explained: line 8", h, look)
            self.assertIn("Harmless (1):", h, look)
            self.assertIn('.cz-sn{', h, look)                       # its CSS on the page
            self.assertNotIn("msg <1>", h, look)                     # escaped
        mail = {"title": "t", "status": "critical", "summary": [], "sections": r["email"]}
        for look in ("report", "report_command", "report_portal", "report_glass"):
            h = env.get_template("%s.html.j2" % look).render(_report=mail, _email_footer="f", _page_logo_cid="", _page_runs=[],
                                                            report_email_header_text="x")
            self.assertIn("Confirm the SELinux denial: sealert -l &lt;id&gt;", h, look)
            self.assertNotIn('class="cz', h, look)                  # no cards in an email


if __name__ == "__main__":
    unittest.main()
