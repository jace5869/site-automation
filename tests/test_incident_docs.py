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
import site_outage_review_read as rr  # noqa: E402
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
        r = f.incident_record(dict(TYPED, sop_troubleshoot="", sop_fix=" \n", actions="", recovery=""))
        self.assertIn("No SOP", f.incident_sop_skipped(r))
        self.assertFalse(f.incident_sop_on(r))
        # its own steps empty, but the AAR has Actions or Recovery: the SOP is made from the AAR
        r = f.incident_record(dict(TYPED, sop_troubleshoot="", sop_fix="", actions=""))
        self.assertEqual(f.incident_sop_skipped(r), "")
        self.assertTrue(f.incident_sop_on(r))
        self.assertFalse(f.incident_sop_on(f.incident_record({"kind": "sop", "actions": "a", "recovery": "b"})))  # the SOP job: its own
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

    def test_banner_auto(self):
        """1.3.0: incident_docs_banner auto = the reports' banner (roles/site_email report_banner) when on, else CUI."""
        on = {"on": True, "text": "SECRET", "bg": "#c8102e"}
        self.assertEqual(f.incident_banner_auto("auto", "", on), {"banner": "SECRET", "banner_color": "C8102E"})
        self.assertEqual(f.incident_banner_auto("auto", "", {"on": False}), {"banner": f.CUI_MARKING, "banner_color": ""})
        self.assertEqual(f.incident_banner_auto(None, "", None)["banner"], "CONTROLLED UNCLASSIFIED INFORMATION (CUI)")
        self.assertEqual(f.incident_banner_auto("MY MARKING", "", on), {"banner": "MY MARKING", "banner_color": ""})     # own text wins
        self.assertEqual(f.incident_banner_auto("", "", on)["banner"], "")                                             # none
        self.assertEqual(f.incident_banner_auto("auto", "00AA00", on)["banner_color"], "00AA00")                       # own colour wins

    def test_one_colour_table_with_the_reports(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("site_banner", os.path.join(REPO, "plugins", "module_utils", "site_banner.py"))
        b = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(b)
        for text in ("SECRET", "TOP SECRET//SCI", "CONFIDENTIAL", "CUI", "UNCLASSIFIED", "CONTROLLED UNCLASSIFIED INFORMATION (CUI)"):
            self.assertEqual("#" + f.incident_banner_color(text).lower(), b.banner({"banner": True, "text": text})["bg"], text)

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
        self.assertEqual(heads, ["Purpose and when to use", "Signs it is happening", "Troubleshooting steps", "Fix steps", "Revision history"])
        steps = [b["items"] for b in doc["blocks"] if b["type"] == "steps"]
        self.assertEqual(steps, [["Check the status page", "Read the error log"], ["Restart the app pool", "Fail over"]])
        self.assertIn(["Status", "Draft"], doc["blocks"][2]["rows"])
        self.assertIn("Its root cause was: A PHP patch (CVE-2026-0001) broke sessions.", texts(doc["blocks"]))
        self.assertIn("Monitoring alert", texts(doc["blocks"]))
        self.assertFalse([b for b in doc["blocks"] if "as written" in str(b.get("text"))])     # typed steps: no "from the AAR" note
        sop_job = f.incident_sop_docx(f.incident_record(dict(TYPED, kind="sop")), {})
        self.assertNotIn("Signs it is happening", [b.get("text") for b in sop_job["blocks"]])

    def test_sop_from_the_aar(self):
        r = f.incident_record(dict(TYPED, sop_troubleshoot="", sop_fix="", sop_title=""))
        self.assertEqual(f.incident_sop_from_aar(r), [("sop_troubleshoot", "actions", "aar"), ("sop_fix", "recovery", "aar")])
        doc = f.incident_sop_docx(r, {})
        steps = [b["items"] for b in doc["blocks"] if b["type"] == "steps"]
        self.assertEqual(steps[:2], [["Opened a bridge", "Failed over to 192.0.2.30"], ["Rolled the package back."]])
        notes = [b["text"] for b in doc["blocks"] if b["type"] == "note"]
        self.assertIn(f.FROM_AAR_NOTE % "Actions", notes)
        self.assertIn(f.FROM_AAR_NOTE % "Recovery", notes)
        self.assertEqual(doc["blocks"][1]["text"], "Troubleshoot and fix: Web portal outage")
        self.assertIn("Actions and Recovery as written", f.incident_sop_what(r))
        self.assertEqual(f.incident_file_name(r, "sop"), "SOP-Web-portal-outage.docx")
        # a typed box wins, box by box
        r2 = f.incident_record(dict(TYPED, sop_troubleshoot=""))
        self.assertEqual(f.incident_sop_from_aar(r2), [("sop_troubleshoot", "actions", "aar")])
        steps = [b["items"] for b in f.incident_sop_docx(r2, {})["blocks"] if b["type"] == "steps"]
        self.assertEqual(steps[1], ["Restart the app pool", "Fail over"])
        self.assertEqual(f.incident_sop_what(f.incident_record(TYPED)), "the SOP")
        # the tidy note: an AAR box the SOP is made from counts; one it does not use does not
        self.assertTrue(f.incident_sop_tidied(r, ["actions"]))
        self.assertFalse(f.incident_sop_tidied(f.incident_record(TYPED), ["actions", "tasks"]))
        self.assertTrue(f.incident_sop_tidied(f.incident_record(TYPED), ["sop_fix"]))


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

    def test_a_changed_number_is_not_kept(self):
        """1.23.0: numbers and addresses compared whole - 10 is not kept by 100, 192.0.2.30 not by 192.0.2.3."""
        r = dict(self.r, actions="Rebooted 10 servers at 10:28\nchecked 192.0.2.30")
        out = f.incident_act_apply(r, self.answer({"tidy": {"actions": "Rebooted 100 servers at 10:28 AM.\nChecked 192.0.2.3."}}),
                                   {"tidy": True})
        self.assertEqual(out["tidied"], [])
        self.assertIn("10, 192.0.2.30", " ".join(out["kept"]))
        out = f.incident_act_apply(r, self.answer({"tidy": {"actions": "Rebooted 10 servers at 10:28.\nChecked 192.0.2.30."}}),
                                   {"tidy": True})
        self.assertEqual(out["tidied"], ["actions"])
        rec = f.incident_record(dict(TYPED, sop_troubleshoot="", sop_fix="", sop_verify=""))
        out = f.incident_act_apply(rec, self.answer({"sop": {"sop_troubleshoot": ["Fail over to 192.0.2.3."]}}), {"sop": True})
        self.assertIn("192.0.2.3, not in the AAR", " ".join(out["kept"]))

    def test_suggestions(self):
        out = f.incident_act_apply(self.r, self.answer({"suggestions": [{"for": "sop_verify", "text": "Log in.", "why": "proof"},
                                                                        {"for": "nonsense", "text": "  Keep\n spares. "}, "bad", {"for": "x"}]}),
                                   {"suggest": True, "max": 3})
        self.assertEqual(out["suggestions"], [{"for": "sop_verify", "text": "Log in.", "why": "proof"},
                                              {"for": "improvements", "text": "Keep spares.", "why": ""}])

    def test_sop_written_from_the_aar(self):
        r = f.incident_record(dict(TYPED, sop_troubleshoot="", sop_fix="", sop_verify=""))
        task = f.incident_act_task(r, {"sop": True})
        self.assertIn("SOP: the SOP is made from this outage", task)
        self.assertIn("sop_troubleshoot, sop_fix, sop_verify", task)
        self.assertNotIn("SOP: the SOP", f.incident_act_task(f.incident_record(TYPED), {"sop": True}))     # all typed: nothing to write
        out = f.incident_act_apply(r, self.answer({"sop": {
            "sop_troubleshoot": ["Open a bridge with the on-call teams.", "Fail over to 192.0.2.30."],
            "sop_fix": ["Roll the PHP package back (CVE-2026-0001)."],
            "sop_verify": ["Log in to the portal."]}}), {"sop": True})
        self.assertEqual(out["sop"], ["sop_troubleshoot", "sop_fix", "sop_verify"])
        rec = out["record"]
        self.assertEqual(f.incident_sop_from_aar(rec), [("sop_troubleshoot", "actions", "act"), ("sop_fix", "recovery", "act"),
                                                        ("sop_verify", "", "act")])
        doc = f.incident_sop_docx(rec, {})
        self.assertEqual([b["items"] for b in doc["blocks"] if b["type"] == "steps"][2], ["Log in to the portal."])
        self.assertEqual([b["text"] for b in doc["blocks"] if b["type"] == "note"].count(f.FROM_ACT_NOTE), 3)
        self.assertIn("written by GenAI", f.incident_sop_what(rec))
        self.assertEqual(r["sop_fix"], "")                                                   # the record given is not changed
        # a host, address or number the AAR does not have: not used - the AAR's lines as written
        out = f.incident_act_apply(r, self.answer({"sop": {"sop_troubleshoot": ["Fail over to 192.0.2.99."],
                                                           "sop_fix": "Restart [REDACTED] service"}}), {"sop": True})
        self.assertEqual(out["sop"], [])
        kept = " ".join(out["kept"])
        self.assertIn("192.0.2.99, not in the AAR", kept)
        self.assertIn("masked word", kept)
        self.assertEqual(f.incident_sop_from_aar(out["record"]), [("sop_troubleshoot", "actions", "aar"), ("sop_fix", "recovery", "aar")])
        # a typed box is never replaced by ACT's steps
        out = f.incident_act_apply(f.incident_record(TYPED), self.answer({"sop": {"sop_fix": ["x"]}}), {"sop": True})
        self.assertEqual(out["record"]["sop_fix"], TYPED["sop_fix"])

    def test_sop_draft_for_the_sop_jobs_form(self):
        # the draft only (Also make an SOP: No): ACT still writes the steps, the SOP job's form gets them
        r = f.incident_record(dict(TYPED, make_sop="No", sop_draft="Yes", sop_title="", sop_troubleshoot="", sop_fix=""))
        self.assertFalse(f.incident_sop_on(r))
        self.assertIn("sop_troubleshoot, sop_fix, sop_verify", f.incident_act_task(r, {"sop": True}))
        draft = f.incident_sop_draft(r)
        self.assertEqual(draft["incident_name"], "Web portal outage")
        self.assertEqual(draft["sop_title"], "Troubleshoot and fix: Web portal outage")
        self.assertEqual(draft["sop_troubleshoot"], "Opened a bridge\nFailed over to 192.0.2.30")
        self.assertEqual(draft["sop_fix"], "Rolled the package back.")
        self.assertEqual(draft["sop_purpose"].split("\n"), ["Use it when this outage's symptoms are seen again:", "Monitoring alert",
                                                             "Customer reports", "Its root cause was: A PHP patch (CVE-2026-0001) broke sessions."])
        self.assertEqual((draft["sop_verify"], draft["sop_escalate"]), ("", ""))                     # every box set: no old draft stays
        out = f.incident_act_apply(r, self.answer({"sop": {"sop_troubleshoot": ["Fail over to 192.0.2.30."], "sop_fix": ["Roll back."],
                                                           "sop_verify": ["Log in."]}}), {"sop": True})
        self.assertEqual(f.incident_sop_draft(out["record"])["sop_verify"], "Log in.")
        self.assertEqual(f.incident_sop_draft(out["record"])["sop_troubleshoot"], "Fail over to 192.0.2.30.")
        # typed boxes as typed; the SOP job never drafts
        self.assertEqual(f.incident_sop_draft(f.incident_record(dict(TYPED, sop_draft="Yes")))["sop_fix"], "Restart the app pool\nFail over")
        self.assertFalse(f.incident_record(dict(TYPED, kind="sop", sop_draft="Yes"))["sop_draft"])
        self.assertEqual(f.incident_sop_draft_empty(), dict((k, "") for k in f.SOP_DRAFT_KEYS))
        e = json.dumps(f.incident_email(r, {"files": [], "draft": ["The form of 'SOP' is filled in"]}))
        self.assertIn("SOP draft", e)

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
        self.assertEqual(names[:5], ["incident_review", "incident_name", "incident_start", "incident_end", "incident_impact_level"])
        for v in ("report_email_also", "incident_make_sop", "sop_fix", "incident_act_tidy", "incident_act_suggest", "incident_act_sop"):
            self.assertIn(v, names)
        q = dict((x["variable"], x) for x in aar)
        self.assertEqual(q["incident_impact_level"]["choices"], ["LOW", "HIGH"])
        self.assertEqual((q["incident_detection"]["type"], q["incident_detection"]["max"]), ("textarea", 5000))
        self.assertTrue(q["report_email_also"]["required"])
        self.assertFalse(q["incident_start"]["required"] or q["incident_review"]["required"])     # 1.112.0: a review can fill the start
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
        for v in typed + ["incident_act_tidy", "incident_act_suggest", "incident_act_sop"]:
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


class FakeAapCredentials(FakeAap):
    """FakeAap with credentials: credentials/?name=, job_templates/N/credentials/ (GET, POST)."""

    def __init__(self):
        FakeAap.__init__(self)
        self.creds = [{"id": 51, "name": "AAP draft writer", "credential_type": 18}, {"id": 52, "name": "Machine", "credential_type": 1},
                      {"id": 53, "name": "AAP admin", "credential_type": 18}]
        self.attached = {}
        self.inputs = {51: {"host": self.url + "/", "username": "svc_aar_draft", "password": "$encrypted$"},
                       52: {"username": "deploy", "ssh_key_data": "$encrypted$"},
                       53: {"host": self.url, "username": "admin", "oauth_token": "$encrypted$"}}
        self.users = [{"id": 7, "username": "svc_aar_draft"}, {"id": 8, "username": "deploy"}]
        self.user_roles = {7: [{"id": 900, "name": "Read"}], 8: []}
        self.refuse_roles = False
        self.sources = {}

    def answer(self, method, rest, qs, body, path):
        if len(rest) == 3 and rest[0] == "credentials" and rest[2] == "input_sources" and method == "GET":
            return self.page(self.sources.get(int(rest[1]), []), qs, path)
        if len(rest) == 2 and rest[0] == "credentials" and method == "GET":                # 1.112.1: its user name (inputs)
            c = next((x for x in self.creds if str(x["id"]) == rest[1]), None)
            return (200, dict(c, inputs=self.inputs.get(c["id"], {}))) if c else (404, {"detail": "Not found."})
        if len(rest) == 2 and rest[0] == "credential_types" and method == "GET":
            return 200, ({"id": 18, "name": "Red Hat Ansible Automation Platform", "namespace": "controller"} if rest[1] == "18"
                         else {"id": 1, "name": "Machine", "namespace": "ssh"})
        if rest == ["users"] and method == "GET":
            return self.page([u for u in self.users if not qs.get("username") or u["username"] == qs["username"]], qs, path)
        if len(rest) == 3 and rest[0] == "users" and rest[2] == "roles":
            have = self.user_roles.setdefault(int(rest[1]), [])
            if method == "GET":
                return self.page(have, qs, path)
            if self.refuse_roles:
                return 403, {"detail": "You do not have permission to perform this action."}
            have.append({"id": body["id"], "name": "Read"})
            self.writes.append((method, "user", int(rest[1]), "roles", body))
            return 204, {}
        if len(rest) == 3 and rest[0] == "job_templates" and rest[2] == "object_roles":
            n = int(rest[1])
            return self.page([{"id": 1000 + n, "name": "Admin"}, {"id": 2000 + n, "name": "Execute"}, {"id": 3000 + n, "name": "Read"}], qs, path)
        if rest == ["credentials"] and method == "GET":
            return self.page([c for c in self.creds if not qs.get("name") or c["name"] == qs["name"]], qs, path)
        if len(rest) == 3 and rest[0] == "job_templates" and rest[2] == "credentials":
            have = self.attached.setdefault(int(rest[1]), [])
            if method == "GET":
                return self.page(have, qs, path)
            have.append(next(c for c in self.creds if c["id"] == body["id"]))
            self.writes.append((method, "job", int(rest[1]), "credentials", body))
            return 204, {}
        return FakeAap.answer(self, method, rest, qs, body, path)


class SurveyWriter(unittest.TestCase):
    def setUp(self):
        self.aap = FakeAapCredentials()
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

    def test_sop_draft_written_and_cleared(self):
        sv.run(self.api, self.p, False)
        self.aap.writes = []
        draft = f.incident_sop_draft(f.incident_record(dict(TYPED, sop_draft="Yes", sop_troubleshoot="", sop_fix="x" * 9000)))
        p = {"surveys": [{"playbook": "incident_sop.yml", "questions": [], "defaults": draft}], "project": "", "state": "present"}
        results, problems, _ = sv.run(self.api, dict(p), True)                           # Check: reads only
        self.assertEqual((problems, self.aap.writes), ([], []))
        self.assertIn("5 default answer(s) written", results[0]["action"])        # verify, escalate: already empty
        results, _, _ = sv.run(self.api, p, False)
        self.assertEqual(results[0]["cut"], ["sop_fix"])
        spec = dict((q["variable"], q) for q in self.aap.specs[("job", 2)]["spec"])
        self.assertEqual(spec["sop_troubleshoot"]["default"], "Opened a bridge\nFailed over to 192.0.2.30")
        self.assertEqual(len(spec["sop_fix"]["default"]), 8000)
        self.assertEqual(self.aap.specs[("job", 1)]["spec"][1]["default"], "")         # the AAR template: untouched
        # cleared only when launched with that draft's outage
        clear = {"surveys": [{"playbook": "incident_sop.yml", "questions": [], "defaults": f.incident_sop_draft_empty(),
                              "match": {"incident_name": "Another outage"}}], "project": "", "state": "present"}
        results, _, _ = sv.run(self.api, clear, False)
        self.assertIn("is not 'Another outage'", results[0]["action"])
        now = dict((q["variable"], q["default"]) for q in self.aap.specs[("job", 2)]["spec"])
        self.assertEqual(now["sop_troubleshoot"], "Opened a bridge\nFailed over to 192.0.2.30")                 # left as it is
        clear["surveys"][0]["match"] = {"incident_name": "Web portal outage"}
        results, _, _ = sv.run(self.api, clear, False)
        self.assertTrue(results[0]["ok"])
        self.assertEqual([q["default"] for q in self.aap.specs[("job", 2)]["spec"] if q["variable"] in f.SOP_DRAFT_KEYS], [""] * 7)

    def test_credential_attached_once(self):
        self.aap.attached[1] = [self.aap.creds[2]]                                        # the AAR template has an AAP credential
        results, problems, _ = sv.run(self.api, dict(self.p, credential="AAP draft writer"), True)
        self.assertEqual(self.aap.writes, [])
        by = dict((r["template"], r["action"]) for r in results)
        self.assertIn("has 'AAP admin' of that credential type already", by["Outage AAR"])
        self.assertIn("credential 'AAP draft writer' attached", by["SOP"])
        sv.run(self.api, dict(self.p, credential="AAP draft writer"), False)
        self.assertEqual([c["name"] for c in self.aap.attached[2]], ["AAP draft writer"])
        self.assertEqual([c["name"] for c in self.aap.attached[1]], ["AAP admin"])
        results, _, _ = sv.run(self.api, dict(self.p, credential="AAP draft writer"), False)
        self.assertIn("credential 'AAP draft writer' already there", dict((r["template"], r["action"]) for r in results)["SOP"])
        self.assertIn("not there", sv.run(self.api, dict(self.p, credential="Nope"), False)[1][0])

    def test_read_role_on_the_outage_watch(self):
        """1.112.1: the draft credential's user gets the Read role on the VM - outage watch templates (the AAR reads their reviews) - once,
        and only when it is sure who that user is (Codex review: an AAP credential, named once, this AAP, no token, the AAR's own)."""
        self.aap.jobs += [_jt(4, "VM - outage watch", "playbooks/vm_outage_watch.yml"), _jt(5, "Restricted", "restricted/vm_outage_watch.yml")]
        p = dict(self.p, credential="AAP draft writer", read_for=["vm_outage_watch.yml"])

        def line(results):
            rows = [r for r in results if r["template"] in ("VM - outage watch", "Restricted")]
            self.assertEqual([r["template"] for r in rows], ["VM - outage watch"])        # the same file name elsewhere: never
            self.assertTrue(rows[0]["ok"])                                                 # never fails the setup job
            return rows[0]

        def grants():
            return [w for w in self.aap.writes if w[1] == "user"]
        results, problems, missing = sv.run(self.api, p, True)                                 # Check: reads only
        self.assertEqual((problems, missing, grants()), ([], [], []))
        row = line(results)
        self.assertEqual((row["changed"], row["action"]), (True, "the Read role given to 'svc_aar_draft' (the AAR reads its reviews)"))
        line(sv.run(self.api, p, False)[0])
        self.assertEqual(grants(), [("POST", "user", 7, "roles", {"id": 3004})])
        row = line(sv.run(self.api, p, False)[0])
        self.assertEqual((row["changed"], row["action"]), (False, "'svc_aar_draft' has the Read role already"))
        self.assertEqual(len(grants()), 1)

        def by_hand(why, **kw):
            self.aap.user_roles[7] = [{"id": 900, "name": "Read"}]
            self.aap.writes = []
            row = line(sv.run(self.api, dict(p, **kw), False)[0])
            self.assertIn("the Read role not given: " + why, row["action"])
            self.assertEqual(grants(), [])
        self.aap.attached = {1: [self.aap.creds[2]]}                                          # the AAR runs with another AAP credential
        by_hand("the AAR template runs with another AAP credential, not 'AAP draft writer'")
        self.aap.attached = {}
        by_hand("'AAP admin' logs in with a token, so its user is not known here", credential="AAP admin")
        self.aap.attached = {}
        by_hand("'Machine' is not a Red Hat Ansible Automation Platform credential", credential="Machine")
        self.aap.attached = {}
        self.aap.inputs[51]["host"] = "https://aap-test.example.mil"
        by_hand("'AAP draft writer' is for another AAP (https://aap-test.example.mil:443)")
        self.aap.inputs[51]["host"] = self.aap.url
        self.aap.creds.append({"id": 54, "name": "AAP draft writer", "credential_type": 18})
        by_hand("more than one credential is named 'AAP draft writer'")
        self.aap.creds.pop()
        self.aap.inputs[51]["host"] = self.aap.url.replace("http://", "https://")              # same host, another port and scheme
        by_hand("'AAP draft writer' is for another AAP")
        self.aap.inputs[51]["host"] = self.aap.url
        self.aap.sources = {51: [{"input_field_name": "password"}]}
        by_hand("'AAP draft writer' takes password from another credential (a secret store)")
        self.aap.sources = {}
        # the outage watch of another project (an organization's own 'Site automation'): never given, said
        self.aap.jobs[0]["project"], self.aap.jobs[3]["project"], self.aap.jobs[4]["project"] = 10, 11, 10
        self.aap.attached = {}
        self.aap.writes = []
        row = line(sv.run(self.api, p, False)[0])
        self.assertIn("the Read role not given: another project than the AAR template's", row["action"])
        self.assertEqual(grants(), [])
        del self.aap.jobs[0]["project"], self.aap.jobs[3]["project"], self.aap.jobs[4]["project"]
        # a credential of the same type left on the AAR whose NAME quotes ours: still "another credential"
        self.aap.creds.append({"id": 55, "name": "Old credential 'AAP draft writer' attached before", "credential_type": 18})
        self.aap.attached = {1: [self.aap.creds[-1]]}
        by_hand("the AAR template runs with another AAP credential")
        self.aap.creds.pop()
        self.aap.attached = {}
        self.aap.users = []
        by_hand("AAP has no user 'svc_aar_draft'")
        self.aap.users = [{"id": 7, "username": "svc_aar_draft"}]
        self.aap.refuse_roles = True
        by_hand("AAP refused it (HTTP 403)")
        self.aap.jobs = self.aap.jobs[:3]
        self.assertEqual(sv.run(self.api, p, False)[2], ["vm_outage_watch.yml"])
        self.assertEqual(sv.run(self.api, dict(p, read_for=[]), False)[2], [])                # without read_for: as before

    def test_pure_parts(self):
        spec = {"spec": [{"variable": "a", "type": "textarea", "default": "", "max": 3}, {"variable": "c", "type": "multiplechoice", "default": "No"}]}
        new, done, cut, missing, why = sv.set_defaults(spec, {"a": "abcdef", "b": "x", "c": "Yes"})
        self.assertEqual((new["spec"][0]["default"], done, cut, missing, why), ("abc", ["a"], ["a"], ["b"], ""))
        self.assertEqual(new["spec"][1]["default"], "No")                                   # a pick list keeps its own
        self.assertIsNone(sv.set_defaults(spec, {"a": "x"}, {"a": "y"})[0])
        q = sv.clean_question({"variable": "a", "type": "textarea", "choices": ["x"], "junk": 1, "max": 9})
        self.assertEqual(q, {"variable": "a", "type": "textarea", "choices": "", "max": 9, "question_name": "a",
                             "question_description": "", "required": False, "default": ""})
        self.assertEqual(sv.remove_questions({"spec": [{"variable": "b"}]}, ["a"]), (None, []))


def _doc_text(desc):
    """All the text of a site_docx description, in order (tables' cells too)."""
    out = []
    for b in desc["blocks"]:
        for k in ("text", "label"):
            if b.get(k):
                out.append(str(b[k]))
        for x in b.get("lines") or []:
            out.append(str(x))
        for x in b.get("items") or []:
            out.append(" ".join(str(v) for v in x.values()) if isinstance(x, dict) else str(x))
        for row in b.get("rows") or []:
            out.append(" | ".join(str(c) for c in row))
    return "\n".join(out)


def _review():
    sys.path.insert(0, HERE)
    import test_outage_timeline as T
    return T.made()


class AarFromReview(unittest.TestCase):
    """1.112.0: the AAR draft of an outage review - the facts from the review, ACT's causes as suggestions, the root cause and
    the lessons learned left to write (red), every event in Appendix A; the same draft for the AAR job (incident_review)."""
    CARDS = {"web": {"causes": [{"title": "A fibre channel path went down", "confidence": 80, "conf_text": "80% high",
                                 "evidence": [{"t": "01:58:02", "h": "esx03", "m": "Lost path redundancy"}],
                                 "steps": [{"do": "Check the switch port.", "cmd": "esxcli storage core path list"}]},
                                {"title": "", "confidence": 10}, {"title": "Unexplained", "unexplained": True}],
                     "cause_overall": ["Check the path first."]}, "judged": ""}

    def draft(self, act=None):
        return f.incident_review_draft(_review()["draft"], act, {"job": "4714", "made": "2026-11-12 02:45 UTC"})

    def test_the_draft(self):
        d = self.draft()
        self.assertEqual((d["job"], d["made"], d["name"]), ("4714", "2026-11-12 02:45 UTC", "esx03 2026-11-12"))
        self.assertEqual(d["start"], "2026-11-12 01:58 CET - Storage path redundancy lost on esx03")
        self.assertEqual(d["impact"], "esx03 down 46 min (12 Nov 02:19 CET to 12 Nov 03:05 CET); vSphere HA restarted 14 VMs")
        self.assertEqual(d["detection"][1], "Ticket INC0012345 opened 12 Nov 03:14 CET, 93 min after the first signal")
        self.assertEqual([x[0] for x in d["numbers"]], ["First signal", "First signal to ticket", "Time down"])
        self.assertEqual(d["recovery"][0], "12 Nov 03:05 CET - Host connected again (esx03)")
        self.assertEqual(d["recovery"], sorted(d["recovery"]))                       # in time order
        self.assertEqual(len(d["events"]), d["events_total"])
        self.assertLessEqual(len(d["key"]), 15)
        self.assertIn(["12 Nov 02:21:05", "vCenter alarms", "Warning", "HA restarted 14 VMs", "cluster1"], d["key"])
        self.assertEqual(f.incident_review_filled({"start": "typed"}, d), ["end", "impact", "detection", "actions", "recovery"])
        self.assertEqual(f.incident_review_filled({}, dict(d, end="", actions=[])), ["start", "impact", "detection", "recovery"])
        self.assertEqual(f.incident_review_filled({}, {}), [])
        self.assertEqual(d["tickets"][0][0], "INC0012345")
        self.assertNotIn("root_cause", d)                                             # never in the draft
        self.assertNotIn("ticket_hint", d)
        self.assertEqual(d["causes"]["note"], "ACT was not asked (vm_outage_act is off for this review).")
        self.assertTrue(d["causes"]["hint"].startswith("ACT in INC0012345: The host lost every path"))
        json.dumps(d)                                                                 # an artifact: plain JSON
        self.assertEqual(f.incident_review_draft({}, None, {}), {})

    def test_the_causes(self):
        c = f.incident_review_causes(self.CARDS)
        self.assertEqual(c["causes"], [{"title": "A fibre channel path went down", "conf": "80% high",
                                        "evidence": ["01:58:02 esx03 Lost path redundancy"],
                                        "checks": ["Check the switch port (esxcli storage core path list)"]}])
        self.assertEqual((c["asked"], c["overall"], c["note"]), (True, "Check the path first.", ""))
        own = f.incident_review_causes({"causes": [{"title": "T", "confidence": 45, "evidence": ["a line"], "steps": [{"do": "x"}]}]})
        self.assertEqual(own["causes"][0]["conf"], "45% low")                        # act_causes' own shape
        self.assertEqual(f.incident_review_causes({"web": None, "judged": "ACT (vm_outage_act): no key."})["note"], "ACT (vm_outage_act): no key.")
        self.assertEqual(f.incident_review_causes(None)["asked"], False)
        self.assertEqual(f.incident_review_causes("junk")["causes"], [])

    def test_fill_typed_wins(self):
        d = self.draft()
        r = f.incident_review_fill(f.incident_record({"kind": "aar", "start": "typed start", "recovery": "typed fix", "name": ""}), d)
        self.assertEqual((r["start"], r["recovery"], r["name"]), ("typed start", "typed fix", "esx03 2026-11-12"))
        self.assertEqual(r["end"], "2026-11-12 03:06 CET - Alarm back to green: Host storage path redundancy")
        self.assertEqual(r["detection"].splitlines(), d["detection"])
        self.assertEqual(r["root_cause"], "")                                         # never from the review
        self.assertEqual(f.incident_problems(r), [])
        self.assertEqual(f.incident_review_fill({"start": ""}, {}), {"start": ""})
        self.assertIn("incident_review", f.incident_problems(f.incident_record({"kind": "aar"}))[0])

    def test_the_document(self):
        d = self.draft(self.CARDS)
        r = f.incident_review_fill(f.incident_record({"kind": "aar"}), d)
        doc = f.incident_aar_docx(r, {"banner": "CUI", "instructions": ["Save it."]}, [], False, d)
        t = _doc_text(doc)
        heads = [b["text"] for b in doc["blocks"] if b.get("type") == "heading"]
        self.assertEqual(heads, ["OVERVIEW", "Event Response", "Reason for Outage (RFO) & Recovery", "Lessons Learned & Action Plan",
                                 "Appendix A - every event of the review"])
        for want in ("Filled from the outage review, AAP job 4714, made 2026-11-12 02:45 UTC", "Key events (from the review",
                     "Suggested - confirm: A fibre channel path went down (ACT, 80% high). Evidence: 01:58:02 esx03 Lost path redundancy. "
                     "Check: Check the switch port (esxcli storage core path list).", "Suggested - confirm: ACT in INC0012345",
                     "To write: the confirmed cause", "To write: what went well.", "To write: each task - owner - due date.",
                     "The review saw: the ticket came 93 min", "To write: the impact level", "Save it.", "INC0012345 | 12 Nov 03:14 CET"):
            self.assertIn(want, t)
        red = [b for b in doc["blocks"] if b.get("color") == f.TO_WRITE]
        self.assertTrue(red and all(str(x).startswith("To write:") for b in red for x in b["items"]))
        self.assertEqual(sum(1 for b in doc["blocks"] if b.get("columns") == ["Task", "Owner", "Due"]), 1)
        self.assertEqual(t.index("Appendix A") < t.index("Save it."), True)          # the site's instructions last
        # typed boxes win, and are not red
        r2 = dict(r, root_cause="A cable.", successes="Fast failover", tasks="Label the cables - ops", impact_level="MAJOR")
        t2 = _doc_text(f.incident_aar_docx(r2, {}, [], False, d))
        for gone in ("To write: the confirmed cause", "To write: what went well.", "To write: each task", "To write: the impact level"):
            self.assertNotIn(gone, t2)
        self.assertIn("Suggested - confirm", t2)                                      # the suggestions stay beside a typed cause
        self.assertIn("A cable.", t2)
        # the AAR without a review is as before
        self.assertEqual(f.incident_aar_docx(r, {}, [], False), f.incident_aar_docx(r, {}, [], False, {}))
        for word in t.lower().split():
            self.assertNotIn(word.strip(".,:;()"), ("purchase", "buy", "cost"))

    def test_written_as_word(self):
        d = self.draft(self.CARDS)
        doc = f.incident_aar_docx(f.incident_review_fill(f.incident_record({"kind": "aar"}), d), {"banner": "CUI"}, [], False, d)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "AAR.docx")
            d_ = __import__("site_docx")
            d_.write_docx(path, doc)
            z = zipfile.ZipFile(path)
            for n in z.namelist():
                if n.endswith(".xml"):
                    minidom.parseString(z.read(n))
            body = z.read("word/document.xml").decode()
            self.assertIn('w:val="C00000"', body)                                      # the red lines
            self.assertIn("Appendix A", body)


class ReviewReader(unittest.TestCase):
    """roles/incident_docs/library/site_outage_review_read.py: a job number, latest, and what it says when it cannot."""
    class Api(object):
        base = "api/controller/v2/"

        def __init__(self, jobs, fail=None):
            self.jobs, self.calls, self.fail = jobs, [], dict(fail or {})      # fail: {path: [codes, ...]} answered first

        def call(self, method, path, body=None, base=None):
            self.calls.append((method, path))
            if self.fail.get(path):
                return self.fail[path].pop(0), {"detail": "busy"}
            if path.startswith("jobs/?"):
                return 200, {"results": [dict((k, v) for k, v in j.items() if k != "artifacts") for _, j in sorted(self.jobs.items(), reverse=True)]}
            n = path.split("/")[1]
            j = self.jobs.get(int(n)) if n.isdigit() else None
            return (200, j) if j else (404, {"detail": "Not found."})

    def jobs(self):
        aar = {"v": 1, "title": "Outage review: esx03"}
        return {10: {"id": 10, "name": "VM - outage watch", "playbook": "playbooks/vm_outage_watch.yml",
                     "artifacts": {"vm_outage_review": {"review": True, "aar": aar}}},
                12: {"id": 12, "name": "VM - outage watch", "playbook": "playbooks/vm_outage_watch.yml", "artifacts": {"vm_outage_review": {}}},
                13: {"id": 13, "name": "Patch", "playbook": "playbooks/patch_hosts.yml", "artifacts": {}}}

    def test_read(self):
        api = self.Api(self.jobs())
        self.assertEqual(rr.read(api, "10")["draft"]["title"], "Outage review: esx03")
        got = rr.read(api, " Latest ")
        self.assertEqual((got["ok"], got["job"]), (True, "10"))                     # 12 has no review: skipped
        self.assertTrue(all(m == "GET" for m, _ in api.calls))                       # read only
        self.assertIn("has no outage review to fill from", rr.read(api, "12")["error"])
        self.assertIn("AAP has no job 99", rr.read(api, "99")["error"])
        self.assertIn("give an AAP job number", rr.read(api, "yesterday")["error"])
        self.assertIn("no VM - outage watch job", rr.read(self.Api({}), "latest")["error"])
        none = self.jobs()
        del none[10]
        self.assertIn("no outage review among the newest 1", rr.read(self.Api(none), "latest")["error"])
        self.assertEqual(rr.draft_of({"artifacts": {"vm_outage_review": {"aar": {"v": 0}}}}), {})

    def test_neon_p1_latest_never_falls_back(self):
        """A run latest cannot read stops it, said - never last week's review instead; a lost answer is asked again once."""
        jobs = self.jobs()
        jobs[11] = {"id": 11, "name": "VM - outage watch", "playbook": "playbooks/vm_outage_watch.yml",
                    "artifacts": {"vm_outage_review": {"review": True, "aar": {"v": 1, "title": "today"}}}}
        got = rr.read(self.Api(jobs, {"jobs/12/": [503, 503]}), "latest")
        self.assertFalse(got["ok"])
        self.assertIn("outage watch job 12 could not be read (HTTP 503)", got["error"])
        self.assertIn("latest stops there rather than take an older review", got["error"])
        got = rr.read(self.Api(jobs, {"jobs/11/": [-1]}), "latest")                 # cut off once: asked again
        self.assertEqual((got["ok"], got["draft"]["title"]), (True, "today"))

    def test_neon_p1_latest_pages_past_200(self):
        class Paged(self.Api):
            def call(s, method, path, body=None, base=None):
                s.calls.append((method, path))
                if path.startswith("jobs/?"):
                    start = int(path.split("&start=")[1]) if "&start=" in path else 0
                    ids = list(range(1000, 500, -1))[start:start + 200]
                    nxt = ("/api/controller/v2/jobs/?playbook__endswith=vm_outage_watch.yml&order_by=-id&page_size=200&start=%d"
                           % (start + 200)) if start + 200 < 500 else None
                    return 200, {"next": nxt, "results": [{"id": i, "playbook": "playbooks/vm_outage_watch.yml"} for i in ids]}
                n = int(path.split("/")[1])
                art = {"vm_outage_review": {"aar": {"v": 1, "title": "old"}}} if n == 760 else {}
                return 200, {"id": n, "name": "VM - outage watch", "playbook": "playbooks/vm_outage_watch.yml", "artifacts": art}
        api = Paged({})
        got = rr.read(api, "latest", look_back=300)
        self.assertEqual((got["ok"], got["job"]), (True, "760"))                       # job 760 is the 241st newest
        lists = [p for _, p in api.calls if p.startswith("jobs/?")]
        self.assertEqual(len(lists), 2)
        self.assertTrue(all("page_size=200" in p for p in lists))
        self.assertIn("no outage review among the newest 230", rr.read(Paged({}), "latest", look_back=230)["error"])

    def test_neon_p2_digits_and_project(self):
        self.assertIn("give an AAP job number", rr.read(self.Api(self.jobs()), "²")["error"])
        jobs = self.jobs()
        for j in jobs.values():
            j["summary_fields"] = {"project": {"name": "Other"}}
        self.assertIn("is of project Other, not Site automation", rr.read(self.Api(jobs), "10", project="Site automation")["error"])
        self.assertIn("no VM - outage watch job", rr.read(self.Api(jobs), "latest", project="Site automation")["error"])
        self.assertTrue(rr.read(self.Api(jobs), "latest", project="Other")["ok"])

    def test_neon_p1_review_text_stays_text(self):
        try:
            from ansible.utils.unsafe_proxy import AnsibleUnsafeText, wrap_var
        except ImportError:
            self.skipTest("no ansible-core")
        if type(wrap_var("x")) is str:
            self.skipTest("this ansible-core never templates a filter's result")
        d = {"v": 1, "actions": ["02:30 - {{ lookup('ansible.builtin.env', 'HOME') }}"], "start": "s"}
        r = f.incident_review_fill(f.incident_record({"kind": "aar"}), d)
        self.assertIsInstance(r["actions"], AnsibleUnsafeText)
        doc = f.incident_aar_docx(r, {}, [], False, f.incident_review_draft(d))
        self.assertTrue(all(isinstance(x, AnsibleUnsafeText) for b in doc["blocks"] for x in b.get("lines") or []))

    def test_codex_p1_project_verified(self):
        """With a project set, a draft is used only from a job AAP says is of that project - never one that names none."""
        jobs = self.jobs()
        jobs[11] = {"id": 11, "name": "VM - outage watch", "playbook": "playbooks/vm_outage_watch.yml",
                    "summary_fields": {"project": {"name": "Test"}},
                    "artifacts": {"vm_outage_review": {"aar": {"v": 1, "title": "Test's outage"}}}}
        jobs[10]["summary_fields"] = {"project": {"name": "Prod"}}
        jobs[12]["summary_fields"] = {"project": {"name": "Prod"}}

        class Bare(self.Api):                         # the list names no project; the detail does
            def call(s, method, path, body=None, base=None):
                code, d = ReviewReader.Api.call(s, method, path, body, base)
                if path.startswith("jobs/?"):
                    for j in d["results"]:
                        j.pop("summary_fields", None)
                return code, d
        got = rr.read(Bare(jobs), "latest", project="Prod")
        self.assertEqual((got["ok"], got["job"], got["draft"]["title"]), (True, "10", "Outage review: esx03"))     # Test's 11 skipped
        del jobs[12]["summary_fields"]
        got = rr.read(Bare(jobs), "latest", project="Prod")
        self.assertFalse(got["ok"])
        self.assertIn("outage watch job 12 names no project, so it cannot be checked against Prod", got["error"])
        self.assertIn("names no project", rr.read(self.Api(jobs), "12", project="Prod")["error"])
        self.assertIn("is of project Test, not Prod", rr.read(self.Api(jobs), "11", project="Prod")["error"])
        self.assertIn("job 13 (Patch) is not a VM - outage watch run", rr.read(self.Api(jobs), "13")["error"])
        jobs[13]["artifacts"] = {"vm_outage_review": {"aar": {"v": 1}}}                  # a look-alike artifact
        self.assertFalse(rr.read(self.Api(jobs), "13")["ok"])


class SurveyRelax(unittest.TestCase):
    def test_start_no_longer_required(self):
        spec = {"spec": [{"variable": "incident_start", "required": True}, {"variable": "x", "required": True}]}
        new, done = sv.relax_questions(spec, ["incident_start", "nope"])
        self.assertEqual((done, new["spec"][0]["required"], new["spec"][1]["required"]), (["incident_start"], False, True))
        self.assertEqual(sv.relax_questions(new, ["incident_start"]), (None, []))     # run again: nothing to do


if __name__ == "__main__":
    unittest.main()
