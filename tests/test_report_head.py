#!/usr/bin/env python3
"""Unit tests for the head of a report (1.82.0, plugins/module_utils/report_head.py): the title as a name alone, the counts, the scope,
the short email subject - on the rules (no "report" in the name, 2 to 4 counts, never a 0 unless all are 0, plural words, a subject of 60
characters at most), on every report fixture of the tests, and on the three web looks and four email looks that draw it.
    python3 tests/test_report_head.py"""
import glob
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "module_utils"))
import report_head as H  # noqa: E402
import site_filters as sf  # noqa: E402
import stig_filters as stf  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "report_fit")


def load(path):
    with open(path) as fh:
        return json.load(fh)


def fixtures():
    for f in sorted(glob.glob(os.path.join(FIX, "*.json"))):
        d = load(f)
        if isinstance(d, dict) and d.get("title") and "its tables" not in d["title"] and d["title"] != "T":
            yield os.path.basename(f), d


class ReviewRound(unittest.TestCase):
    """Review of 1.82.0: a count the title only carried in a later group, or not at all, is a chip and in the subject."""

    def test_hosts_that_did_not_report_are_a_count_never_zero_findings(self):
        d = sf.findings_report({"web01": []}, "Health check", missing=["web02", "web03"])
        h = H.report_head(d)
        self.assertEqual([c["text"] for c in h["counts"]], ["2 did not report"])
        self.assertIn("2 did not report", h["subject"])
        self.assertNotIn("0 findings", h["subject"] + h["counts_text"])
        d = sf.findings_report({"web01": [{"severity": "critical", "check": "disk", "finding": "full"}]}, "Health check", missing=["web02"])
        self.assertEqual([c["text"] for c in H.report_head(d)["counts"]], ["1 critical", "1 did not report"])
        d = sf.findings_report({"web01": []}, "Health check")                      # nothing found, everyone reported: the zero chip says so
        h = H.report_head(d)
        self.assertEqual([c["text"] for c in h["counts"]], ["no findings"])

    def test_the_stig_update_chips_have_their_nouns(self):
        items = [{"state": "written", "host": "h%d" % i} for i in range(3)] + [{"state": "error", "host": "x"}] * 11
        h = H.report_head(stf.stig_update_report(items))
        self.assertEqual([c["text"] for c in h["counts"]], ["11 not written", "3 reviews written"])
        self.assertNotIn("11 not,", h["subject"] + ",")
        self.assertEqual(h["name"], "STIG Manager update")
        h = H.report_head(stf.stig_update_report([{"state": "would write", "host": "h"}], True))
        self.assertEqual([c["text"] for c in h["counts"]], ["1 review would be written"])
        self.assertEqual(h["scope"], ["Check: nothing written"])


class Rules(unittest.TestCase):
    def test_the_four_reports_of_the_mockup(self):
        d = {"title": "STIG drift report: 3 finding(s) on 2 of 2 host(s)", "status": "critical"}
        h = H.report_head(d)
        self.assertEqual(h["name"], "STIG drift")
        self.assertEqual([c["text"] for c in h["counts"]], ["3 findings"])
        self.assertEqual(h["scope"], ["on 2 of 2 hosts"])
        self.assertEqual(h["subject"], "STIG drift: 3 findings")
        h = H.report_head({"title": "VMware health report: 5 critical, 7 warning(s) - 2 datacenter(s), 4 cluster(s), 8 host(s)",
                           "subtitle": "vCenter vc01.example.mil - read 2026-10-13 06:00 UTC", "status": "critical"})
        self.assertEqual(h["name"], "VMware health")
        self.assertEqual([(c["n"], c["words"], c["status"]) for c in h["counts"]], [(5, "critical", "critical"), (7, "warnings", "warning")])
        self.assertEqual(h["scope"][:3], ["2 datacenters", "4 clusters", "8 hosts"])
        self.assertTrue(h["scope"][3].startswith("vCenter vc01.example.mil"))
        self.assertEqual(h["subject"], "VMware health: 5 critical, 7 warnings")

    def test_no_zero_unless_everything_is_zero(self):
        h = H.report_head({"title": "Health: 0 critical, 2 warning(s), 0 info", "status": "warning"})
        self.assertEqual([c["text"] for c in h["counts"]], ["2 warnings"])
        h = H.report_head({"title": "Health: 0 critical, 0 warning(s)", "status": "ok"})
        self.assertEqual(len(h["counts"]), 1)
        self.assertEqual(h["counts"][0]["status"], "ok")
        self.assertEqual(h["counts"][0]["text"], "no critical")
        h = H.report_head({"title": "fapolicyd denials: no denials since today", "status": "ok"})
        self.assertEqual((h["counts_text"], h["scope"], h["subject"]), ("no denials", ["since today"], "fapolicyd denials: no denials"))

    def test_plural_words(self):
        self.assertEqual(H.plural("1 host(s), 2 host(s), 1 fix(es), 3 fix(es)"), "1 host, 2 hosts, 1 fix, 3 fixes")
        self.assertNotIn("(s)", H.report_head({"title": "X: 1 finding(s) on 1 of 4 host(s)"})["scope_text"])

    def test_the_worst_first_and_at_most_four(self):
        h = H.report_head({"title": "X: 9 ok, 1 warning, 2 critical, 3 info, 4 fine, 5 more", "status": "critical"})
        self.assertLessEqual(len(h["counts"]), 4)
        self.assertEqual(h["counts"][0]["status"], "critical")
        self.assertTrue(any("5 more" in s for s in h["scope"] + [c["text"] for c in h["counts"]]))      # none lost: it moves to the scope

    def test_the_subject_is_short(self):
        h = H.report_head({"title": "A very long report name that goes on: 12 critical findings here, 40 warnings of every kind, 300 more things", "status": "critical"})
        self.assertLessEqual(len(h["subject"]), 60)

    def test_a_report_that_knows_better(self):
        h = H.report_head({"title": "Old: 1 x", "head_name": "New", "head_counts": [[5, "critical", "critical"]], "head_scope": ["7 hosts read"],
                           "head_subject": "New: 5 critical"})
        self.assertEqual((h["name"], h["counts_text"], h["scope"], h["subject"]), ("New", "5 critical", ["7 hosts read"], "New: 5 critical"))

    def test_a_title_with_no_count_keeps_its_words_in_the_subject(self):
        t = "Mail relay whitelist - waiting for approval: add 198.51.100.3 (Payroll app (svc-payroll))"
        h = H.report_head({"title": t})
        self.assertEqual((h["name"], h["subject"]), ("Mail relay whitelist - waiting for approval", t))
        self.assertLessEqual(len(H.report_head({"title": "A: " + "word " * 40})["subject"]), 100)

    def test_the_subject_is_one_line(self):
        h = H.report_head({"title": "X: 1 a", "head_subject": "Line one\r\nBcc: x@example.mil", "head_name": "Na\nme"})
        self.assertEqual((h["subject"], h["name"]), ("Line one Bcc: x@example.mil", "Na me"))
        self.assertNotIn("\n", H.report_head({"title": "A\nB: 2 c\r\nd"})["subject"])

    def test_no_title_and_odd_input(self):
        for d in (None, {}, {"title": ""}, {"title": None}, {"title": "No colon"}, {"title": "A: "}, "x", []):
            h = H.report_head(d)
            self.assertIsInstance(h["name"], str)
            self.assertLessEqual(len(h["subject"]), 60)


class EveryFixture(unittest.TestCase):
    def test_every_report_of_the_tests(self):
        seen = 0
        for name, d in fixtures():
            h = H.report_head(d)
            seen += 1
            self.assertTrue(h["name"], name)
            if re.search(r"\breport$", h["name"], re.I):          # "report" goes when counts follow ("STIG drift report: 3 ..."), and is kept when it is half the name ("Disk report") or the title has no counts
                self.assertTrue(len(h["name"].split()) <= 2 or (": " not in d["title"] and not d["title"].endswith(")")), (name, h["name"]))
            self.assertLessEqual(len(h["counts"]), 4, name)
            self.assertLessEqual(len(h["subject"]), 60 if any(c["n"] not in ("", None) for c in h["counts"]) else 100, (name, h["subject"]))
            for text in (h["counts_text"], h["scope_text"], h["subject"]):
                self.assertNotRegex(text, r"\w\((s|es)\)", name)
            zeros = [c for c in h["counts"] if c["n"] in (0, "no")]
            self.assertTrue(not zeros or len(h["counts"]) == 1, (name, h["counts"]))
        self.assertGreater(seen, 60)


class Pages(unittest.TestCase):
    def render(self, template, doc):
        import jinja2
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(HERE, "..", "roles", "site_email", "templates")), autoescape=False)
        env.filters.update(sf.FilterModule().filters())
        return env.get_template(template).render(_report=doc, _email_footer="AAP job 4711", _page_logo_cid="", _page_runs=[],
                                                 report_email_header_text="Site automation report", _email_notice="", _act_old=[])

    def doc(self):
        return load(os.path.join(FIX, "vm_health_report.json"))

    def test_the_web_looks_show_name_counts_scope(self):
        for t in ("report_atlas.html.j2", "report_brief.html.j2", "report_ops.html.j2"):
            h = self.render(t, self.doc())
            self.assertIn("<h1", h, t)
            self.assertRegex(h, r"<h1[^>]*>VMware health</h1>", t)
            self.assertIn('class="hcounts"', h, t)
            self.assertRegex(h, r'<li class="hct s-critical"><svg[^>]*>.*?</svg><b>5</b> <span>critical</span></li>', t)
            self.assertNotIn("VMware health report", h.split("</head>", 1)[1].split("<h1", 1)[0], t)
            self.assertIn("<title>VMware health: 5 critical, 7 warnings</title>", h, t)

    def test_the_emails_stay_plain(self):
        for t in ("report.html.j2", "report_command.html.j2", "report_portal.html.j2", "report_glass.html.j2"):
            h = self.render(t, self.doc())
            self.assertIn("VMware health", h, t)
            self.assertIn("5 critical", h, t)
            self.assertNotIn("hcounts", h, t)
            self.assertNotIn("<ul", h.split("VMware health", 1)[1][:400], t)

    def test_a_long_subtitle_is_a_short_scope_and_the_method_goes_last(self):
        sub = ("1 host(s) read - what fapolicyd blocked since yesterday (ausearch -m FANOTIFY -ts yesterday), the trust entries "
               "and rules to let it through - and what keeps fapolicyd down: its trust files, the service")
        h = H.report_head({"title": "fapolicyd: 2 change(s) to approve, permissive on 1", "subtitle": sub, "status": "warning"})
        self.assertEqual(h["scope"], ["1 host read"])
        self.assertLess(len(h["scope_text"]), 40)
        self.assertNotIn("(s)", h["subject"] + h["scope_text"] + h["method"])
        self.assertIn("ausearch", h["method"])
        h = H.report_head({"title": "fapolicyd: 2 changes", "subtitle": sub, "head_scope": ["1 host", "blocked since yesterday"]})
        self.assertEqual(h["scope_text"], u"1 host \u00b7 blocked since yesterday")
        self.assertIn("ausearch", h["method"])
        self.assertEqual(H.report_head({"title": "X report: 1 critical", "subtitle": "3 hosts"})["method"], "")

    def test_the_text_email(self):
        t = sf.report_text(self.doc())
        self.assertTrue(t.startswith("VMware health\n=============\n5 critical"), t[:80])


if __name__ == "__main__":
    unittest.main()
