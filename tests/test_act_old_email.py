#!/usr/bin/env python3
"""Old copies of ACT (1.53.0) in the email: roles/site_email's four HTML looks (standard, command, portal, glass) and the
plain text show the "ACT old copies" section - each host's line, once, last; nothing when no host had anything to say; the
same section a report already carries is not added again. Skipped where 1.53.0 is not merged (no report_act_old).
python3 tests/test_act_old_email.py"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
import site_filters  # noqa: E402

LINES = ["web01.example.mil: /usr/local/bin/act was an old copy (ACT 0.4.2): linked to /opt/act/act (ACT 0.6.25), the old one kept at "
         "/opt/act/old/act.20261113T070000Z.1a2b3c4d.",
         "web01.example.mil: user jdoe's saved model is gemini-4-argon: `act` uses it when typed; the jobs pass their own."]
REPORT = {"title": "Health check: 1 host", "status": "ok", "summary": [{"label": "Hosts", "value": 1}],
          "sections": [{"title": "Findings", "columns": ["Host", "Check"], "rows": [["web01.example.mil", "ok"]]}]}
LOOKS = ("report.html.j2", "report_command.html.j2", "report_portal.html.j2", "report_glass.html.j2")


@unittest.skipUnless(hasattr(site_filters, "report_act_old"), "1.53.0 (the old copies of ACT) is not in this tree")
class OldCopiesInTheEmail(unittest.TestCase):
    def render(self, tpl, act_old, report=REPORT):
        import jinja2
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(HERE, "..", "roles", "site_email", "templates")), autoescape=False)
        env.filters["report_cell_html"] = site_filters.report_cell_html
        return env.get_template(tpl).render(_report=report, _act_old=act_old, _email_footer="AAP job 9", _email_logo_cid="",
                                            _email_runs=[], report_email_header_text="Site automation report")

    def test_every_html_look(self):
        for tpl in LOOKS:
            h = self.render(tpl, LINES)
            self.assertEqual(h.count("ACT old copies"), 1, tpl)
            self.assertIn("was an old copy (ACT 0.4.2): linked to /opt/act/act (ACT 0.6.25)", h, tpl)
            self.assertIn("user jdoe&#39;s saved model" if "&#39;" in h else "user jdoe's saved model", h, tpl)
            self.assertLess(h.index("Findings"), h.index("ACT old copies"), tpl)      # last
            self.assertNotIn("ACT old copies", self.render(tpl, []), tpl)

    def test_not_twice(self):
        carried = site_filters.report_act_old(REPORT, LINES)
        for tpl in LOOKS:
            self.assertEqual(self.render(tpl, LINES, carried).count("ACT old copies"), 1, tpl)

    def test_the_plain_text(self):
        text = site_filters.report_text(site_filters.report_act_old(REPORT, LINES))
        self.assertEqual(text.count("ACT old copies"), 1)
        self.assertIn("- " + LINES[0], text)
        self.assertIn("- " + LINES[1], text)
        self.assertLess(text.index("Findings"), text.index("ACT old copies"))
        self.assertNotIn("ACT old copies", site_filters.report_text(site_filters.report_act_old(REPORT, [])))


if __name__ == "__main__":
    unittest.main()
