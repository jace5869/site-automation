#!/usr/bin/env python3
"""Tests for the outage AAR and SOP Word documents: the record made from the survey (plugins/filter/incident_doc_filters.py),
the two layouts, ACT's prompt and its answer held to what was typed (tidied text that drops a number, a masked word, fewer
steps; suggestions only in their own section), the email's body, the survey's questions; the Word writer
(roles/incident_docs/library/site_docx.py: a .docx whose every part is well-formed XML, the logo, the banner, numbered
lists that each start at 1, characters XML cannot hold left out); and the survey writer
(roles/incident_docs/library/site_aap_survey_add.py) against the fake AAP of tests/test_email_question.py.
Needs ansible-core (the modules' imports): python3 tests/test_incident_docs.py"""
import importlib.util
import json
import os
import re
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from xml.dom import minidom

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
sys.path.insert(0, os.path.join(REPO, "roles", "incident_docs", "library"))
_spec = importlib.util.spec_from_file_location("ansible.module_utils.site_aap", os.path.join(REPO, "plugins", "module_utils", "site_aap.py"))
_aap = importlib.util.module_from_spec(_spec)
sys.modules["ansible.module_utils.site_aap"] = _aap
_spec.loader.exec_module(_aap)
import incident_doc_filters as f  # noqa: E402
import site_aap_survey_add as sv  # noqa: E402
import site_docx as d  # noqa: E402
from test_email_question import FakeAap, _jt  # noqa: E402

TYPED = {"kind": "aar", "name": "Web portal outage", "start": "10/27/2026 10:28 AM", "impact_level": "major",
         "impact": "because it is affecting users enterprise-wide.", "detection": "Monitoring alert\r\nCustomer reports\r\n",
         "actions": "- Opened a bridge\n- Failed over to 192.0.2.30\n\n", "root_cause": "A PHP patch (CVE-2026-0001) broke sessions.",
         "recovery": "Rolled the package back.", "successes": "", "improvements": "Test patches in staging first",
         "tasks": "Add a session alert - monitoring team", "make_sop": "Yes", "sop_title": "Web portal returns 503",
         "sop_troubleshoot": "1. Check the status page\n2. Read the error log", "sop_fix": "Step 1: Restart the app pool\nStep 2: Fail over",
         "by": "jdoe", "date": "2026-11-04"}


def png(path, w=120, h=40):
    raw = b"".join(b"\x00" + b"\x80\x80\x80" * w for _ in range(h))

    def chunk(t, data):
        return struct.pack(">I", len(data)) + t + data + struct.pack(">I", zlib.crc32(t + data) & 0xffffffff)
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw))
                 + chunk(b"IEND", b""))


def texts(blocks):
    out = []
    for b in blocks:
        out += [str(b.get(k)) for k in ("text", "label") if b.get(k)] + [str(x) for x in b.get("lines") or b.get("items") or [] if not isinstance(x, dict)]
        out += [str(x.get("text")) for x in b.get("items") or [] if isinstance(x, dict)]
    return out


class Record(unittest.TestCase):
    def test_typed_text_cleaned(self):
        r = f.incident_record(TYPED)
        self.assertEqual(r["detection"], "Monitoring alert\nCustomer reports")         # CRLF from a browser
        self.assertEqual(r["impact_level"], "MAJOR")
        self.assertTrue(r["make_sop"])
        self.assertEqual(f._lines(r["actions"]), ["Opened a bridge", "Failed over to 192.0.2.30"])
        self.assertEqual(f._lines(r["sop_fix"]), ["Restart the app pool", "Fail over"])

    def test_markers_never_eat_an_address_or_a_time(self):
        self.assertEqual(f._lines("192.0.2.3 is down\n10:30 began\n-v flag\n* a\n2) b\n\u2022 c"),
                         ["192.0.2.3 is down", "10:30 began", "-v flag", "a", "b", "c"])

    def test_sop_job_is_always_an_sop(self):
        r = f.incident_record({"kind": "sop", "sop_fix": "a"})
        self.assertEqual((r["kind"], r["make_sop"]), ("sop", True))

    def test_problems(self):
        self.assertEqual(f.incident_problems(f.incident_record(TYPED)), [])
        self.assertIn("start", f.incident_problems(f.incident_record({"kind": "aar"}))[0])
        self.assertIn("no steps", f.incident_problems(f.incident_record({"kind": "sop", "sop_title": "x"}))[0])

    def test_sop_asked_without_steps_is_left_out_and_said(self):
        r = f.incident_record(dict(TYPED, sop_troubleshoot="", sop_fix=" \n"))
        self.assertIn("No SOP", f.incident_sop_skipped(r))
        self.assertFalse(f.incident_sop_on(r))
        self.assertTrue(f.incident_sop_on(f.incident_record(TYPED)))
        self.assertFalse(f.incident_sop_on(f.incident_record(dict(TYPED, make_sop="No"))))

    def test_file_names(self):
        r = f.incident_record(TYPED)
        self.assertEqual(f.incident_file_name(r, "aar"), "AAR-Web-portal-outage.docx")
        self.assertEqual(f.incident_file_name(r, "sop"), "SOP-Web-portal-returns-503.docx")
        self.assertEqual(f.incident_file_name(f.incident_record({"start": "x"}), "aar"), "AAR.docx")
        self.assertEqual(f.incident_file_name(r, "aar", {"aar": "After action"}), "After-action-Web-portal-outage.docx")
        self.assertEqual(f.incident_file_name(dict(r, name="a/b\\c:*?"), "aar"), "AAR-a-b-c.docx")


class Layout(unittest.TestCase):
    def test_aar_sections_in_order(self):
        doc = f.incident_aar_docx(f.incident_record(TYPED), {"banner": "BANNER", "instructions": ["Save it.", "Send it."]})
        heads = [b["text"] for b in doc["blocks"] if b["type"] == "heading"]
        self.assertEqual(heads, ["OVERVIEW", "Event Response", "Reason for Outage (RFO) & Recovery", "Lessons Learned & Action Plan"])
        t = texts(doc["blocks"])
        self.assertIn("The operational impact is MAJOR because it is affecting users enterprise-wide.", t)
        self.assertIn("Start: 10/27/2026 10:28 AM", t)
        self.assertEqual(doc["banner"], "BANNER")
        self.assertEqual([b for b in doc["blocks"] if b["type"] == "steps"][-1]["items"], ["Save it.", "Send it."])
        succ = [b for b in doc["blocks"] if b.get("label") == "Successes:"][0]
        self.assertEqual(succ["lines"], [])                                  # empty: the writer says "Not given."

    def test_banner_colour(self):
        self.assertEqual(f.incident_banner_color("CONTROLLED UNCLASSIFIED INFORMATION (CUI)"), "502B85")    # not UNCLASSIFIED's green
        self.assertEqual(f.incident_banner_color("UNCLASSIFIED//FOUO"), "007A33")
        self.assertEqual(f.incident_banner_color("SECRET//NOFORN"), "C8102E")
        self.assertEqual(f.incident_banner_color("TOP SECRET//SCI"), "FF8C00")
        self.assertEqual(f.incident_banner_color("CONFIDENTIAL"), "0033A0")
        self.assertEqual(f.incident_banner_color("Internal use"), "555555")
        self.assertEqual(f.incident_banner_color("CUI", "#00aa00"), "00AA00")
        self.assertEqual(f.incident_aar_docx({}, {"banner": "CUI"})["banner_color"], "502B85")

    def test_impact_wording(self):
        self.assertEqual(f._impact_text({"impact_level": "MAJOR", "impact": ""}), "The operational impact is MAJOR.")
        self.assertEqual(f._impact_text({"impact_level": "", "impact": "users"}), "users")

    def test_suggestions_go_where_they_belong(self):
        sugg = [{"for": "improvements", "text": "A", "why": ""}, {"for": "sop_fix", "text": "B", "why": "b"}]
        r = f.incident_record(TYPED)
        with_sop = texts(f.incident_aar_docx(r, {"sop_file": "SOP-x.docx"}, sugg)["blocks"])
        self.assertIn("A", with_sop)
        self.assertNotIn("B (b)", with_sop)                                    # the SOP's steps go in the SOP
        self.assertIn("B (b)", texts(f.incident_sop_docx(r, {}, sugg)["blocks"]))
        self.assertIn("B (b)", texts(f.incident_aar_docx(r, {}, sugg)["blocks"]))      # no SOP: here
        self.assertNotIn("From ACT - suggestions to review", texts(f.incident_aar_docx(r, {}, [])["blocks"]))

    def test_sop(self):
        doc = f.incident_sop_docx(f.incident_record(TYPED), {"status": "Draft"})
        heads = [b["text"] for b in doc["blocks"] if b["type"] == "heading"]
        self.assertEqual(heads, ["Purpose and when to use", "Troubleshooting steps", "Fix steps", "Revision history"])
        steps = [b["items"] for b in doc["blocks"] if b["type"] == "steps"]
        self.assertEqual(steps, [["Check the status page", "Read the error log"], ["Restart the app pool", "Fail over"]])
        self.assertIn(["Status", "Draft"], doc["blocks"][2]["rows"])
        self.assertIn("Its root cause was: A PHP patch (CVE-2026-0001) broke sessions.", texts(doc["blocks"]))


class Act(unittest.TestCase):
    r = f.incident_record(TYPED)

    def answer(self, obj):
        return "Here.\n\nBEGIN_ACT_ANALYSIS\n%s\nEND_ACT_ANALYSIS" % json.dumps(obj)

    def test_prompt_and_evidence(self):
        task = f.incident_act_task(self.r, {"tidy": True, "suggest": True, "max": 5, "extra": "Our monitor is X."})
        for words in ("DATA ONLY", "TIDY:", "SUGGEST:", "at most 5", "EXACTLY as typed", "From this site: Our monitor is X.", "sop_fix"):
            self.assertIn(words, task)
        ev = json.loads(f.incident_act_evidence(self.r))
        self.assertEqual(ev["fields"]["detection"]["text"], "Monitoring alert\nCustomer reports")
        self.assertNotIn("successes", ev["fields"])                        # empty: not sent
        self.assertIn("sop_fix", ev["fields"])
        self.assertNotIn("sop_fix", json.loads(f.incident_act_evidence(dict(self.r, make_sop=False)))["fields"])
        self.assertNotIn("TIDY:", f.incident_act_task(self.r, {"suggest": True}))

    def test_tidy_used_when_it_keeps_everything(self):
        out = f.incident_act_apply(self.r, self.answer({"tidy": {"recovery": "Rolled the package back to the prior version.",
                                                                  "sop_troubleshoot": "Check the status page.\nRead the error log."}}),
                                   {"tidy": True})
        self.assertEqual(out["error"], "")
        self.assertEqual(sorted(out["tidied"]), ["recovery", "sop_troubleshoot"])   # "1." "2." typed are not numbers ACT dropped
        self.assertEqual(out["record"]["recovery"], "Rolled the package back to the prior version.")
        self.assertEqual(self.r["recovery"], "Rolled the package back.")            # the record given is not changed

    def test_tidy_refused(self):
        out = f.incident_act_apply(self.r, self.answer({"tidy": {
            "actions": "Opened a bridge\nFailed over to the spare",                  # the address dropped
            "root_cause": "A PHP patch broke sessions.",                              # the CVE dropped
            "detection": "Monitoring alert\n[REDACTED] reports",                      # a masked word
            "sop_fix": "Restart the app pool and fail over",                          # fewer steps
            "tasks": "x" * 500,                                                       # much longer
            "recovery": 7}}), {"tidy": True})
        self.assertEqual(out["tidied"], [])
        kept = " ".join(out["kept"])
        for words in ("192.0.2.30", "CVE-2026-0001", "masked word", "fewer steps", "much longer", "Recovery: ACT gave no text"):
            self.assertIn(words, kept)
        self.assertEqual(out["record"], self.r)

    def test_suggestions(self):
        out = f.incident_act_apply(self.r, self.answer({"suggestions": [{"for": "sop_verify", "text": "Log in.", "why": "proof"},
                                                                        {"for": "nonsense", "text": "  Keep\n spares. "}, "bad", {"for": "x"}]}),
                                   {"suggest": True, "max": 3})
        self.assertEqual(out["suggestions"], [{"for": "sop_verify", "text": "Log in.", "why": "proof"},
                                              {"for": "improvements", "text": "Keep spares.", "why": ""}])

    def test_unreadable_answer_and_a_wrapped_one(self):
        self.assertIn("no JSON", f.incident_act_apply(self.r, "I cannot help.", {"tidy": True})["error"])
        wrapped = {"message": self.answer({"suggestions": [{"for": "tasks", "text": "T"}]})}
        self.assertEqual(len(f.incident_act_apply(self.r, json.dumps(wrapped), {"suggest": True})["suggestions"]), 1)


class Email(unittest.TestCase):
    def test_body_names_the_files_not_the_text(self):
        r = f.incident_record(TYPED)
        e = f.incident_email(r, {"files": [{"name": "AAR.docx", "what": "the after action report"}], "act": ["Wording tidied: recovery"],
                                 "notes": []})
        self.assertEqual(e["title"], "Outage AAR: Web portal outage")
        self.assertEqual(e["status"], "info")
        body = json.dumps(e)
        self.assertIn("AAR.docx - the after action report", body)
        self.assertNotIn("PHP", body)                                        # the documents are attachments


class Survey(unittest.TestCase):
    def test_questions(self):
        aar = f.incident_survey("aar", {"levels": ["low", "high"], "most": 5000})
        names = [q["variable"] for q in aar]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(names[:4], ["incident_name", "incident_start", "incident_end", "incident_impact_level"])
        for v in ("report_email_also", "incident_make_sop", "sop_fix", "incident_act_tidy", "incident_act_suggest"):
            self.assertIn(v, names)
        q = dict((x["variable"], x) for x in aar)
        self.assertEqual(q["incident_impact_level"]["choices"], ["LOW", "HIGH"])
        self.assertEqual((q["incident_detection"]["type"], q["incident_detection"]["max"]), ("textarea", 5000))
        self.assertTrue(q["report_email_also"]["required"] and q["incident_start"]["required"])
        self.assertEqual(q["incident_make_sop"]["default"], "No")
        sop = dict((x["variable"], x) for x in f.incident_survey("sop"))
        self.assertTrue(sop["sop_title"]["required"])
        self.assertNotIn("incident_start", sop)
        # every answer the role reads (the defaults' "What was typed" block) is asked by one of the surveys, and the
        # ACT switches too
        with open(os.path.join(REPO, "roles", "incident_docs", "defaults", "main.yml")) as fh:
            block = fh.read().split("# What was typed:", 1)[1].split("\n\n", 1)[0]
        typed = re.findall(r"^([a-z_]+):", block, re.M)
        self.assertGreater(len(typed), 15)
        for v in typed + ["incident_act_tidy", "incident_act_suggest"]:
            self.assertTrue(v in q or v in sop, v)


class Writer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.logo = os.path.join(self.tmp, "logo.png")
        png(self.logo)

    def tearDown(self):
        for n in os.listdir(self.tmp):
            os.remove(os.path.join(self.tmp, n))
        os.rmdir(self.tmp)

    def write(self, doc):
        path = os.path.join(self.tmp, "out.docx")
        logo, notes = d.write_docx(path, doc)
        z = zipfile.ZipFile(path)
        parts = dict((n, z.read(n)) for n in z.namelist())
        for n, data in parts.items():
            if n.endswith((".xml", ".rels")):
                minidom.parseString(data)                                     # every part is well-formed XML
        return logo, notes, parts

    def test_aar_file(self):
        doc = f.incident_aar_docx(f.incident_record(dict(TYPED, detection="bell\x07 and nul\x00 and \ufffe ok")),
                                  {"banner": "MARKING", "logo": self.logo, "logo_height": 32, "instructions": ["One.", "Two."]})
        logo, notes, parts = self.write(doc)
        self.assertEqual((logo, notes), ([96, 32], []))
        body = parts["word/document.xml"].decode()
        self.assertIn("bell and nul and  ok", body)
        self.assertIn("Reason for Outage (RFO) &amp; Recovery", body)
        self.assertIn('r:embed="rIdLogo"', body)
        self.assertIn("word/media/logo.png", parts)
        self.assertIn("MARKING", parts["word/header1.xml"].decode())
        self.assertIn('<w:b/><w:color w:val="555555"/>', parts["word/header1.xml"].decode())      # bold, its colour
        self.assertIn("MARKING", parts["word/footer1.xml"].decode())
        self.assertIn('w:headerReference', body)
        types = parts["[Content_Types].xml"].decode()
        for part in ("/word/document.xml", "/word/numbering.xml", "/word/header1.xml"):
            self.assertIn('PartName="%s"' % part, types)

    def test_each_numbered_list_starts_at_one(self):
        logo, notes, parts = self.write({"blocks": [{"type": "steps", "items": ["a", "b"]}, {"type": "steps", "items": "c\nd"},
                                                    {"type": "bullets", "items": ["e"]}]})
        body, numbering = parts["word/document.xml"].decode(), parts["word/numbering.xml"].decode()
        self.assertEqual(re.findall(r'<w:numId w:val="(\d+)"/>', body), ["2", "2", "3", "3", "1"])
        self.assertEqual(numbering.count("<w:startOverride w:val=\"1\"/>"), 2)

    def test_no_banner_no_logo(self):
        logo, notes, parts = self.write({"banner": "", "logo": os.path.join(self.tmp, "none.png"), "blocks": [{"type": "para", "text": "x"}]})
        self.assertEqual(logo, [])
        self.assertIn("not there", notes[0])
        self.assertNotIn("word/header1.xml", parts)
        self.assertNotIn("headerReference", parts["word/document.xml"].decode())

    def test_image_sizes(self):
        gif = os.path.join(self.tmp, "a.gif")
        with open(gif, "wb") as fh:
            fh.write(b"GIF89a" + struct.pack("<HH", 30, 20) + b"\x00" * 20)
        jpg = os.path.join(self.tmp, "a.jpg")
        with open(jpg, "wb") as fh:
            fh.write(b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
                     + b"\xff\xc0" + struct.pack(">HBHH", 17, 8, 50, 70) + b"\x00" * 10)
        self.assertEqual(d.image_size(gif), ("gif", 30, 20))
        self.assertEqual(d.image_size(jpg), ("jpeg", 70, 50))
        self.assertEqual(d.image_size(self.logo), ("png", 120, 40))
        self.assertIsNone(d.image_size(os.path.join(self.tmp, "none")))

    def test_table_widths(self):
        logo, notes, parts = self.write({"blocks": [{"type": "table", "columns": ["A", "B"], "rows": [["1", "2\n3"]], "widths": [1, 3],
                                                     "bold_first": True}]})
        body = parts["word/document.xml"].decode()
        self.assertEqual(re.findall(r'<w:gridCol w:w="(\d+)"/>', body), ["2520", "7560"])
        self.assertIn("<w:tblHeader/>", body)


class SurveyWriter(unittest.TestCase):
    def setUp(self):
        self.aap = FakeAap()
        self.aap.jobs = [_jt(1, "Outage AAR", "playbooks/incident_aar.yml"), _jt(2, "SOP", "playbooks/incident_sop.yml", enabled=True),
                         _jt(3, "Other", "playbooks/cert_report.yml")]
        mine = {"variable": "incident_start", "type": "text", "question_name": "When it began (mine)", "question_description": "",
                "required": True, "default": "", "min": 0, "max": 40}
        self.aap.specs = {("job", 1): {"name": "", "description": "", "spec": [mine]}, ("job", 2): {}, ("job", 3): {}}
        self.api = sv.Api(sv.connection({"CONTROLLER_HOST": self.aap.url, "CONTROLLER_OAUTH_TOKEN": "goodtoken"}), no_credential=sv.NO_CREDENTIAL)
        self.assertEqual(self.api.probe(), "")
        self.p = {"surveys": [{"playbook": "incident_aar.yml", "questions": f.incident_survey("aar")},
                              {"playbook": "playbooks/incident_sop.yml", "questions": f.incident_survey("sop")}],
                  "project": "", "state": "present"}

    def tearDown(self):
        self.aap.close()

    def test_added_in_order_mine_kept_turned_on(self):
        results, problems, missing = sv.run(self.api, self.p, False)
        self.assertEqual((problems, missing), ([], []))
        by = dict((r["template"], r) for r in results)
        self.assertEqual(sorted(by), ["Outage AAR", "SOP"])
        spec = self.aap.specs[("job", 1)]["spec"]
        self.assertEqual(spec[0]["question_name"], "When it began (mine)")          # left as it is
        self.assertEqual([q["variable"] for q in spec[1:]], [q["variable"] for q in f.incident_survey("aar") if q["variable"] != "incident_start"])
        self.assertIn("survey turned on", by["Outage AAR"]["action"])
        self.assertTrue(self.aap.jobs[0]["survey_enabled"])
        self.assertEqual(self.aap.specs[("job", 3)], {})                               # another playbook's template: untouched
        again, _, _ = sv.run(self.api, self.p, False)
        self.assertEqual([r["action"] for r in again], ["all questions already there"] * 2)

    def test_check_mode_only_reads_and_missing_template(self):
        self.aap.jobs = self.aap.jobs[:1]
        results, problems, missing = sv.run(self.api, self.p, True)
        self.assertEqual(missing, ["incident_sop.yml"])
        self.assertTrue(results[0]["changed"])
        self.assertEqual(self.aap.writes, [])

    def test_absent(self):
        sv.run(self.api, self.p, False)
        results, _, _ = sv.run(self.api, dict(self.p, state="absent"), False)
        self.assertTrue(all(r["ok"] for r in results))
        self.assertEqual(self.aap.specs[("job", 1)], {})
        self.assertFalse(self.aap.jobs[1]["survey_enabled"])

    def test_the_playbook(self):
        """playbooks/incident_docs_survey.yml against the fake: Check only reads; then the questions are written."""
        import subprocess
        ap = os.environ.get("ANSIBLE_PLAYBOOK", "ansible-playbook")
        env = dict(os.environ, CONTROLLER_HOST=self.aap.url, CONTROLLER_OAUTH_TOKEN="goodtoken", ANSIBLE_NOCOLOR="1",
                   ANSIBLE_LOCALHOST_WARNING="False", ANSIBLE_INVENTORY_UNPARSED_WARNING="False")
        extra = json.dumps({"ansible_python_interpreter": sys.executable, "incident_impact_levels": ["SEV1", "SEV2"]})

        def run(*a):
            return subprocess.run([ap, "playbooks/incident_docs_survey.yml", "-i", "localhost,", "-e", extra] + list(a),
                                  cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=300)
        out = run("--check")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("job template 1 'Outage AAR': would be: added %d question(s), survey turned on" % (len(f.incident_survey("aar")) - 1), out.stdout)
        self.assertEqual(self.aap.writes, [])
        out = run()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("job template 2 'SOP': added %d question(s)" % len(f.incident_survey("sop")), out.stdout)
        levels = [q for q in self.aap.specs[("job", 1)]["spec"] if q["variable"] == "incident_impact_level"][0]
        self.assertEqual(levels["choices"], ["SEV1", "SEV2"])

    def test_pure_parts(self):
        q = sv.clean_question({"variable": "a", "type": "textarea", "choices": ["x"], "junk": 1, "max": 9})
        self.assertEqual(q, {"variable": "a", "type": "textarea", "choices": "", "max": 9, "question_name": "a",
                             "question_description": "", "required": False, "default": ""})
        self.assertEqual(sv.remove_questions({"spec": [{"variable": "b"}]}, ["a"]), (None, []))


if __name__ == "__main__":
    unittest.main()
