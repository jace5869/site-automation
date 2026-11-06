#!/usr/bin/env python3
"""Unit tests for the ticket insights: the pure parts of roles/servicenow_insights/library/site_servicenow_insights.py (the window,
the query, a record), plugins/filter/servicenow_insights_filters.py (close notes graded, the masking and its way back, ACT's
questions and answers merged, the model on a made-up site - tests/servicenow/insights_seed.py -, the email's report, the
workbook, the links between pages) and plugins/filter/servicenow_insights_page.py (the page: every section, no placeholder,
no unescaped text). Needs ansible-core importable.
    python3 tests/test_servicenow_insights.py"""
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
sys.path.insert(0, os.path.join(HERE, "..", "roles", "servicenow_insights", "library"))
import servicenow_insights_filters as F  # noqa: E402
import servicenow_insights_page as P  # noqa: E402
import site_servicenow_insights as rd  # noqa: E402

NOW = datetime.datetime(2026, 11, 1, 6, 0, tzinfo=rd.UTC)
OPTS = {"bare_chars": 20, "bare_phrases": ["fixed", "done", "resolved", "resolved per user", "see work notes", "rebooted", "n/a",
                                            "user confirmed", "completed"], "act": False, "kb_min": 50}
_SITE = {}


def site():
    """The made-up site (insights_seed.py), read as the module reads it - once."""
    if not _SITE:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "seed.json")
            subprocess.check_call([sys.executable, os.path.join(HERE, "servicenow", "insights_seed.py"), path, "0.4"], stdout=subprocess.DEVNULL)
            with open(path) as fh:
                seed = json.load(fh)
        win = rd.window(6, 0, "2026-10-31", True, "", NOW)
        since, until = rd.sn_time(win["prev_since"]), rd.sn_time(win["until"])
        items, audit = [], {}
        for r in seed:
            t = r.get("_table")
            if t == "sys_audit":
                audit.setdefault(r["documentkey"], []).append([r["sys_created_on"], r["oldvalue"], r["newvalue"]])
                continue
            r = dict(r, active="false" if r.get("state") in (("6", "7") if t == "incident" else ("3", "4", "7")) else "true")
            it = rd.normalize(dict((k, {"value": v, "display_value": v}) for k, v in r.items() if not k.startswith("_")), t)
            if rd.keep(it, since, until):
                items.append(it)
        _SITE["r"] = {"ok": True, "window": win, "items": items, "audit": audit, "groups": [], "url": "https://sn.example.mil",
                      "tables": [{"table": "incident", "read": 10, "kept": 10, "more": False, "error": ""}]}
        _SITE["mask"] = F.servicenow_insights_mask(_SITE["r"], OPTS)
        _SITE["model"] = F.servicenow_insights_model(_SITE["r"], None, _SITE["mask"], OPTS)
    return _SITE


class Window(unittest.TestCase):
    def test_months_to_a_day_and_the_months_before(self):
        w = rd.window(6, 0, "2026-10-31", True, "", NOW)
        self.assertEqual((w["since"], w["until"], w["prev_since"]), ("2026-05-01 00:00:00", "2026-11-01 00:00:00", "2025-11-01 00:00:00"))
        self.assertEqual((w["label"], w["prev_label"], w["months"]), ("2026-05-01 to 2026-10-31", "2025-11-01 to 2026-04-30", 6))

    def test_days_win_and_now(self):
        w = rd.window(6, 60, "2026-10-31", True, "", NOW)
        self.assertEqual((w["label"], w["months"]), ("2026-09-02 to 2026-10-31", 0))
        w = rd.window(1, 0, "", False, "", NOW)
        self.assertEqual((w["since"], w["until"], w["prev_since"]), ("2026-10-02 00:00:00", "2026-11-01 06:00:00", "2026-10-02 00:00:00"))

    def test_month_end(self):
        self.assertEqual(rd.months_back(datetime.datetime(2026, 3, 31), 1), datetime.datetime(2026, 2, 28))

    def test_bad(self):
        with self.assertRaises(ValueError):
            rd.window(0, 0, "", True, "", NOW)
        with self.assertRaises(ValueError):
            rd.window(6, 0, "31/10/2026", True, "", NOW)


class Reader(unittest.TestCase):
    def test_query(self):
        s, u = rd.sn_time("2026-05-01 00:00:00"), rd.sn_time("2026-11-01 00:00:00")
        q = rd.table_query("incident", ["g5"], [], s, u)
        self.assertEqual(q, "assignment_groupINg5^opened_at>=2026-04-30 00:00:00^ORclosed_at>=2026-04-30 00:00:00^ORactive=true"
                            "^ORresolved_at>=2026-04-30 00:00:00^opened_at<=2026-11-02 00:00:00^ORDERBYopened_at")
        self.assertNotIn("resolved_at", rd.table_query("sc_req_item", [], [], s, u))
        self.assertTrue(rd.table_query("change_request", [], ["Ops - Linux"], s, u).startswith("assignment_group.nameINOps - Linux^"))

    def test_record(self):
        vd = lambda v, d=None: {"value": v, "display_value": v if d is None else d}       # noqa: E731
        it = rd.normalize({"sys_id": vd("x1"), "number": vd("INC1"), "priority": vd("2", "2 - High"), "assignment_group": vd("g5", "Ops - Windows"),
                           "opened_at": vd("2026-06-01 10:00:00"), "resolved_at": vd("2026-06-01 12:00:00"), "closed_at": vd("2026-06-04 12:00:00"),
                           "reassignment_count": vd("3"), "made_sla": vd("false"), "caller_id": vd("u9", "Ruth Klein")}, "incident")
        self.assertEqual((it["priority_value"], it["group"], it["group_id"], it["done_at"], it["reassignment_count"], it["made_sla"], it["caller"]),
                         ("2", "Ops - Windows", "g5", "2026-06-01 12:00:00", 3, False, "Ruth Klein"))
        self.assertEqual(rd._prio("1 - Critical"), "1")


class Notes(unittest.TestCase):
    def test_grades(self):
        self.assertEqual(F.grade("Fixed.", OPTS), "bare")
        self.assertEqual(F.grade("Resolved per user!", OPTS), "bare")
        self.assertEqual(F.grade("", OPTS), "bare")
        self.assertEqual(F.grade("The user says the problem is gone now, closing", OPTS), "thin")
        self.assertEqual(F.grade("Restarted the Print Spooler on prn01 and deleted the stuck job.", OPTS), "good")


class Masking(unittest.TestCase):
    def test_nothing_real_reaches_the_model_and_it_comes_back(self):
        r = {"items": [{"table": "incident", "assigned_to": "Dana Whitfield", "caller": "Ruth Klein", "cmdb_ci": "app-db03",
                        "short_description": "Ruth Klein cannot print on prn01-fl2, call (703) 555-0142 or rklein@example.mil, IP 198.51.100.4, ID 1234567890",
                        "close_notes": "Whitfield, Dana fixed it on wks-1042.corp.example.mil (see INC0040001, KB5034441, win11)"}]}
        m = F.servicenow_insights_mask(r, OPTS)
        text = r["items"][0]["short_description"] + " / " + r["items"][0]["close_notes"]
        masked = F.apply_mask(text, m)
        for real in ("Ruth Klein", "Whitfield", "Dana", "prn01-fl2", "(703) 555-0142", "rklein@example.mil", "198.51.100.4", "1234567890", "wks-1042"):
            self.assertNotIn(real, masked)
        for kept in ("INC0040001", "KB5034441", "win11", "cannot print"):
            self.assertIn(kept, masked)
        self.assertEqual(F.unmask(masked, m), text)
        self.assertTrue(set(m["counts"]) >= {"PERSON", "HOST", "PHONE", "EMAIL", "IP", "ID"})

    def test_keep_and_more_names(self):
        r = {"items": [{"table": "incident", "short_description": "Project Falcon down on sw7x"}]}
        m = F.servicenow_insights_mask(r, dict(OPTS, mask_names=["Project Falcon"], mask_keep=["sw7x"]))
        out = F.apply_mask(r["items"][0]["short_description"], m)
        self.assertNotIn("Falcon", out)
        self.assertIn("sw7x", out)

    def test_the_mask_is_not_changed(self):
        m = {"pairs": [["Ruth Klein", "PERSON-1"]], "counts": {"PERSON": 1}}
        F.apply_mask("Ruth Klein", m)
        self.assertEqual(sorted(m), ["counts", "pairs"])
        json.dumps(m)

    def test_description_key(self):
        m = {"pairs": [["jsmith", "PERSON-1"], ["bdoe", "PERSON-2"]]}
        self.assertEqual(F.desc_key("Account locked out - jsmith", m), F.desc_key("Account locked out - bdoe 2", m))


class ActAnswers(unittest.TestCase):
    batch = [{"id": "D1", "key": "printer offline", "text": "Printer offline", "count": 9, "category": "Hardware"},
             {"id": "D2", "key": "please install visio", "text": "Please install Visio", "count": 4, "category": "Software"}]

    def test_grouping_carried_and_capped(self):
        st = F.servicenow_insights_state()
        raw = ("Sure.\nBEGIN_ACT_ANALYSIS\n" + json.dumps({"new_issues": [{"name": "Printer offline", "about": "a", "kind": "incident"},
                                                                          {"name": "Software install", "kind": "request", "self_service": True}],
                                                           "items": [{"id": "D1", "issue": "printer  OFFLINE"}, {"id": "D2", "issue": "Software install"}]})
               + "\nEND_ACT_ANALYSIS")
        st = F.servicenow_insights_classify_merge(st, raw, self.batch, OPTS)
        self.assertEqual(st["classes"], {"printer offline": "Printer offline", "please install visio": "Software install"})
        self.assertEqual([x["kind"] for x in st["issues"]], ["incident", "request"])
        self.assertTrue(st["issues"][1]["self_service"])
        self.assertIn("Printer offline", F.servicenow_insights_classify_evidence(self.batch, st, OPTS))
        st2 = F.servicenow_insights_classify_merge(st, {"items": [{"id": "D1", "issue": "Something new"}]}, self.batch, dict(OPTS, act_max_issues=2))
        self.assertEqual(st2["classes"]["printer offline"], "Other / unclear")           # past the cap; a dict answer (2.16) read too

    def test_no_answer(self):
        st = F.servicenow_insights_classify_merge(F.servicenow_insights_state(), "", self.batch, OPTS, ran=False, reason="no key")
        self.assertEqual((st["answered"], st["classes"]), (0, {}))
        self.assertIn("no key", st["errors"][0])
        st = F.servicenow_insights_classify_merge(F.servicenow_insights_state(), "just words", self.batch, OPTS)
        self.assertIn("no readable JSON", st["errors"][0])

    def test_fixes(self):
        batch = {"items": [{"id": "I1"}], "issues": [{"id": "I1", "name": "Printer offline", "count": 80, "bare": 5, "total": 9, "notes": [[3, "Restarted spooler"]]}]}
        self.assertIn("ISSUE I1: Printer offline - 80 incidents", F.servicenow_insights_fix_evidence(batch))
        raw = {"items": [{"id": "I1", "steps": ["**Restart** the spooler.", "2. Delete the stuck job."], "support": [3, "x"], "source": "suggested",
                          "why": "w", "disagree": "true", "kb": {"title": "Clear a print queue", "symptoms": "s", "cause": "c", "steps": ["a"]}}]}
        st = F.servicenow_insights_fix_merge(F.servicenow_insights_state(), raw, batch, OPTS)
        fx = st["fixes"]["Printer offline"]
        self.assertEqual((fx["steps"], fx["support"], fx["source"], fx["disagree"], fx["kb"]["title"]),
                         (["Restart the spooler.", "Delete the stuck job."], [3, 0], "ai", True, "Clear a print queue"))

    def test_task_asks_for_the_markers(self):
        for t in (F.servicenow_insights_classify_task(OPTS), F.servicenow_insights_fix_task(OPTS)):
            self.assertIn("BEGIN_ACT_ANALYSIS", t)
            self.assertIn("PERSON-3", t)


class Model(unittest.TestCase):
    def test_the_site(self):
        m = site()["model"]
        a = m["scopes"]["all"]
        self.assertEqual(m["teams"][:2], ["Ops - Service Desk", "Ops - Windows"])
        self.assertGreater(a["counts"]["incidents"], 400)
        self.assertGreater(a["counts"]["incidents_prev"], 300)
        self.assertEqual(sum(a["age"]), a["backlog"][0])
        self.assertFalse(m["reopen_used"])                       # the seed never reopens: came back counted instead
        self.assertLess(a["came_back"][0], 15)
        self.assertEqual(a["issues"][0]["rank"], 1)
        self.assertEqual(a["issues"][0]["fix"]["source"], "tix")
        self.assertTrue(all(len(w) == len(a["weeks"]) for w in [i["weekly"] for i in a["issues"]]))
        self.assertEqual((len(a["weeks"]), a["weeks"][0]["start"]), (25, "2026-05-04"))     # the whole weeks: Monday 4 May to Sunday 25 October
        self.assertTrue(any(p["path"][0] == "Ops - Service Desk" for p in a["handoffs"]["paths"]))
        self.assertTrue(any(x["outcome"] == "Failed" for x in a["changes"]["rows"]))
        self.assertTrue(any(f[1] == "link" for x in a["changes"]["rows"] for f in x["followed"]))
        self.assertTrue(any(x["priority"] == "P1" for x in a["majors"]))
        self.assertTrue(a["summary"])
        json.dumps(m)

    def test_a_team(self):
        m = site()["model"]
        t = m["scopes"]["Ops - Windows"]
        self.assertLess(t["counts"]["incidents"], m["scopes"]["all"]["counts"]["incidents"])
        st = t["stars"]
        self.assertTrue(st["people"])
        self.assertEqual(st["awards"][0]["what"], "Most resolved")
        self.assertEqual(st["total"]["resolved"], sum(p["resolved"] for p in st["people"]) if not st["more"] else st["total"]["resolved"])
        self.assertTrue(all("Ops - Windows" in c["groups"] for c in t["problems"]))

    def test_the_movement(self):
        rows = F._movement([{"key": "a", "cur": 10, "prev": 1}, {"key": "b", "cur": 5, "prev": 9}, {"key": "c", "cur": 1, "prev": 0}], 10)
        self.assertEqual([(x["key"], x["mv"]["kind"], x["mv"]["text"]) for x in rows], [("a", "up", "▲ 1"), ("b", "down", "▼ 1"), ("c", "new", "New")])

    def test_summaries_merged_and_names_back(self):
        m = site()["model"]
        mask = {"pairs": [["Ops - Windows host", "HOST-1"]]}
        out = F.servicenow_insights_summary_merge(m, {"items": [{"id": "all", "lines": ["Look at HOST-1."]}, {"id": "T2", "lines": ["Windows."]}]}, mask)
        self.assertEqual((out["scopes"]["all"]["summary"], out["scopes"]["Ops - Windows"]["summary"], out["summaries_by"]),
                         (["Look at Ops - Windows host."], ["Windows."], "act"))
        self.assertIn("SCOPE T1: Ops - Service Desk", F.servicenow_insights_summary_evidence(m))

    def test_doc_and_workbook(self):
        s = site()
        doc = F.servicenow_insights_doc(s["model"], "all", OPTS)
        self.assertTrue(doc["title"].startswith("Ticket insights for All assignment groups: "))
        self.assertIn("Teams side by side", [x["title"] for x in doc["sections"]])
        self.assertNotIn("Teams side by side", [x["title"] for x in F.servicenow_insights_doc(s["model"], "Ops - VDI", OPTS)["sections"]])
        wb = F.servicenow_insights_xlsx(s["model"], s["r"], None, s["mask"], OPTS)
        self.assertEqual([x["name"] for x in wb][:3], ["Incidents", "Issues", "Requests"])
        self.assertEqual(len(wb[0]["rows"]), s["model"]["scopes"]["all"]["counts"]["incidents"])

    def test_links(self):
        web = {"all": {"page": "ti/2026_job-1.html", "latest": "ti/latest.html"},
               "teams": {"Ops - VDI": {"page": "ti/ops-vdi/2026_job-1.html", "latest": "ti/ops-vdi/latest.html"},
                         "Ops - Linux": {"page": "ti/ops-linux/2026_job-1.html", "latest": ""}}}
        self.assertEqual(F.web_links(web, "ti/2026_job-1.html"), {"Ops - VDI": "ops-vdi/latest.html", "Ops - Linux": "ops-linux/2026_job-1.html", "all": "latest.html"})
        self.assertEqual(F.web_links(web, "ti/ops-vdi/2026_job-1.html")["all"], "../latest.html")


class Page(unittest.TestCase):
    def test_site_page(self):
        m = site()["model"]
        links = dict((t, F._slug(t) + "/latest.html") for t in m["teams"])
        links["all"] = "latest.html"
        h = P.servicenow_insights_page(m, "all", {"links": links, "generated": "2026-11-01 06:05 UTC", "job": "7"})
        for sid in ("glance", "teams", "keepingup", "severity", "issues", "problems", "handoffs", "requests", "changes", "notes", "allstars", "kb", "method"):
            self.assertIn('id="%s"' % sid, h)
        self.assertIn('href="ops-windows/latest.html"', h)
        self.assertIn("https://sn.example.mil/incident.do?sysparm_query=number=INC", h)
        self.assertIn("<!--site-report-logo-->", h)
        self.assertIn("AAP job 7", h)
        self.assertNotRegex(h, r"\b(PERSON|HOST|PHONE)-\d+\b")
        self.assertEqual(h.count("<section"), h.count("</section>"))
        self.assertEqual(h.count("<details"), h.count("</details>"))

    def test_team_page_and_escaping(self):
        m = json.loads(json.dumps(site()["model"]))
        t = m["scopes"]["Ops - VDI"]
        t["summary"] = ["<script>alert(1)</script> & more"]
        h = P.servicenow_insights_page(m, "Ops - VDI", {"links": {"all": "../latest.html"}})
        self.assertNotIn("<script>alert(1)</script>", h)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; &amp; more", h)
        self.assertNotIn('id="teams"', h)
        self.assertIn('href="../latest.html"', h)
        self.assertEqual(h.count("<script>"), 1)

    def test_empty_site(self):
        win = rd.window(1, 0, "2026-10-31", True, "", NOW)
        m = F.servicenow_insights_model({"ok": True, "window": win, "items": [], "tables": []}, None, None, OPTS)
        h = P.servicenow_insights_page(m, "all", {})
        self.assertIn("No incidents in the period.", h)
        self.assertEqual(F.servicenow_insights_doc(m, "all", OPTS)["status"], "info")

    def test_ticket_links(self):
        self.assertEqual(P.tk("CHG0031001", "https://sn/"), '<a class="tk" target="_blank" rel="noopener" '
                                                            'href="https://sn/change_request.do?sysparm_query=number=CHG0031001">CHG0031001</a>')
        self.assertIn("mono", P.tk("XYZ1", "https://sn"))
        self.assertTrue(re.search("—", P.tk("", "https://sn")))


if __name__ == "__main__":
    unittest.main()
