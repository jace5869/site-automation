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
        items, audit, members, notes = [], {}, {}, {}
        for r in seed:
            t = r.get("_table")
            if t == "sys_journal_field":
                notes.setdefault(r["element_id"], []).append([r["sys_created_on"], r["value"]])
                continue
            if t == "sys_audit":
                audit.setdefault(r["documentkey"], []).append([r["sys_created_on"], r["oldvalue"], r["newvalue"]])
                continue
            if t == "sys_user_grmember":
                members.setdefault(r["group"], []).append(r["user"])
                continue
            r = dict(r, active="false" if r.get("state") in (("6", "7") if t == "incident" else ("3", "4", "7")) else "true")
            it = rd.normalize(dict((k, {"value": v, "display_value": v}) for k, v in r.items() if not k.startswith("_")), t)
            if rd.keep(it, since, until):
                items.append(it)
        _SITE["r"] = {"ok": True, "window": win, "items": items, "audit": audit, "members": members, "groups": [], "url": "https://sn.example.mil",
                      "tables": [{"table": "incident", "read": 10, "kept": 10, "more": False, "error": ""}]}
        _SITE["mask"] = F.servicenow_insights_mask(_SITE["r"], OPTS)
        _SITE["work_notes"] = {"work_notes": dict((k, sorted(v, reverse=True)) for k, v in notes.items()), "work_notes_error": ""}
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


class WorkNotes(unittest.TestCase):
    """0.96.0: ACT reads the top issues' work notes too - read-only from sys_journal_field, masked like everything else."""

    def test_read_get_only_and_refused(self):
        class Api(object):
            url = "https://sn.example.mil"

            def __init__(self, fail=None):
                self.calls, self.fail = [], fail

            def get(self, path, params):
                self.calls.append((path, params))
                if self.fail:
                    raise rd.SnError("GET %s: HTTP 403: Operation Failed: ACL Exception" % path)
                return {"result": [{"element_id": "a", "value": "new " + "x" * 50, "sys_created_on": "2026-10-02 10:00:00"},
                                   {"element_id": "a", "value": "old", "sys_created_on": "2026-10-01 10:00:00"},
                                   {"element_id": "b", "value": "  ", "sys_created_on": "2026-10-01 10:00:00"}]}
        api = Api()
        notes, err = rd.read_work_notes(api, ["a", "b"], per_id=5, chars=10)
        self.assertEqual((notes, err), ({"a": [["2026-10-02 10:00:00", "new xxxxxx"], ["2026-10-01 10:00:00", "old"]]}, ""))
        path, q = api.calls[0]
        self.assertEqual(path, "api/now/table/sys_journal_field")
        self.assertEqual(q["sysparm_query"], "element=work_notes^name=incident^element_idINa,b^ORDERBYDESCsys_created_on")
        self.assertEqual(q["sysparm_fields"], "element_id,value,sys_created_on")             # never who wrote it
        self.assertEqual(rd.read_work_notes(api, ["a"], per_id=1)[0]["a"], [["2026-10-02 10:00:00", "new " + "x" * 50]])
        notes, err = rd.read_work_notes(Api(fail=True), ["a"])
        self.assertEqual(notes, {})
        self.assertIn("HTTP 403", err)
        self.assertIn("the account needs read on sys_journal_field", err)
        src = open(os.path.join(HERE, "..", "roles", "servicenow_insights", "library", "site_servicenow_insights.py")).read()
        self.assertNotRegex(src, r'method="(POST|PUT|PATCH|DELETE)"')                         # a GET-only job

    def test_into_the_fixes_masked(self):
        x = site()
        batches = F.servicenow_insights_fix_batches(x["r"], None, x["mask"], OPTS)
        ids = F.servicenow_insights_fix_ids(batches)
        self.assertTrue(ids and len(ids) == len(set(ids)))
        printer = [i for b in batches for i in b["issues"] if i["name"] == "Hardware / Printer"][0]
        self.assertEqual(printer["close_n"], sum(c for c, _ in printer["notes"]))
        mask = F.servicenow_insights_mask_more(x["mask"], x["work_notes"], OPTS)
        self.assertEqual(mask["pairs"][:0], [])
        old = dict((p[0], p[1]) for p in x["mask"]["pairs"])
        self.assertTrue(all(dict((p[0], p[1]) for p in mask["pairs"]).get(k) == v for k, v in old.items()))   # the earlier ones kept
        o = dict(OPTS, act_work_notes_max=4, act_work_note_chars=60)
        got = F.servicenow_insights_fix_work_notes(batches, x["work_notes"], mask, o)
        pr = [i for b in got for i in b["issues"] if i["name"] == "Hardware / Printer"][0]
        self.assertEqual(len(pr["work_notes"]), 4)
        self.assertTrue(all(len(t) <= 60 for t in pr["work_notes"]))
        self.assertEqual(len(set(pr["work_notes"])), 4)                                     # one of each text
        self.assertGreater(pr["work_tickets"], 0)
        self.assertEqual(batches[0]["issues"][0]["work_notes"], [])                          # the batches given are not changed
        ev = "\n".join(F.servicenow_insights_fix_evidence(b, mask, o) for b in F.servicenow_insights_fix_work_notes(batches, x["work_notes"], mask, OPTS))
        self.assertIn("WORK NOTES - what was tried while the tickets were open, newest first", ev)
        self.assertIn("restarted the spooler on HOST-", ev)
        for real in ("vpn-help.wn@example.mil", "wn-gw-07", "(571) 555-", "prn01-fl"):
            self.assertNotIn(real, ev)                                                       # what only the work notes hold: masked too
        self.assertRegex(ev, r"EMAIL-\d+")
        self.assertEqual(F.servicenow_insights_fix_work_notes(batches, {"work_notes": {}, "work_notes_error": "HTTP 403"}, mask, OPTS)[0]["issues"][0]["work_notes"], [])
        # the work notes' mask is kept apart: its words would change how descriptions group into issues (desc_key)
        tasks = open(os.path.join(HERE, "..", "roles", "servicenow_insights", "tasks", "main.yml")).read()
        self.assertIn("_sni_mask_wn: \"{{ _sni_mask | servicenow_insights_mask_more(_sni_wn, _sni_opts) }}\"", tasks)
        self.assertIn("servicenow_insights_model(_sni_state, _sni_mask, ", tasks)
        self.assertIn("servicenow_insights_finish(_sni_mask_wn | default(_sni_mask))", tasks)

    def test_the_task_the_answer_and_the_page(self):
        t = F.servicenow_insights_fix_task(OPTS)
        self.assertIn("WORK NOTES", t)
        self.assertIn("prefer the steps that resolved tickets", t)
        self.assertIn("workaround", t)
        self.assertIn('"tickets": 14', t)
        batch = {"items": [{"id": "I1"}], "issues": [{"id": "I1", "name": "Printer offline", "count": 30, "bare": 5, "total": 9,
                                                      "notes": [[3, "Restarted spooler"]], "close_n": 3, "work_notes": ["a", "b"], "work_tickets": 2}]}
        raw = {"items": [{"id": "I1", "steps": ["Update the driver."], "source": "tickets", "why": "w", "tickets": "23",
                          "workaround": "Restarting the spooler, again and again.", "kb": {"title": "Print queue jams", "symptoms": "s", "cause": "c",
                                                                                          "steps": ["a"], "tickets": 23}}]}
        fx = F.servicenow_insights_fix_merge(F.servicenow_insights_state(), raw, batch, OPTS)["fixes"]["Printer offline"]
        self.assertEqual((fx["tickets"], fx["workaround"], fx["kb"]["tickets"], fx["read"]),
                         (23, "Restarting the spooler, again and again.", 23, {"close": 3, "work": 2, "work_tickets": 2}))
        nul = dict(raw["items"][0], workaround="null")
        self.assertEqual(F.servicenow_insights_fix_merge(F.servicenow_insights_state(), {"items": [nul]}, batch, OPTS)["fixes"]["Printer offline"]["workaround"], "")
        x = site()
        st = F.servicenow_insights_state()
        st["fixes"]["Hardware / Printer"] = fx
        st = F.servicenow_insights_work_notes_state(st, {"work_notes": {"a": []}, "work_notes_error": ""}, 40)
        m = F.servicenow_insights_model(x["r"], st, x["mask"], OPTS)
        pr = [i for i in m["scopes"]["all"]["issues"] if i["name"] == "Hardware / Printer"][0]
        self.assertIn("kb", pr["tags"])                                                    # a repeated workaround: a KB candidate
        self.assertIn("Print queue jams", [k["title"] for k in m["scopes"]["all"]["kb"]])     # ... its draft though under kb_min
        h = P.servicenow_insights_page(m, "all", {})
        self.assertIn('<div class="from">From 3 close notes and 2 work notes of 2 tickets · worked on 23 tickets</div>', h)
        self.assertIn("<b>Repeated workaround:</b> Restarting the spooler, again and again.", h)
        self.assertIn('<p class="worked">Worked on 23 tickets.</p>', h)
        self.assertNotIn("Work notes were not read", h)
        # refused: said on the page (under the issues and under Not read), the fixes from the close notes
        st2 = F.servicenow_insights_work_notes_state(st, {"failed": False, "work_notes": {}, "work_notes_error": "GET x: HTTP 403 - the account needs read"}, 40)
        m2 = F.servicenow_insights_model(x["r"], dict(st2, asked=1, answered=1), x["mask"], OPTS)
        m2["act"]["ran"] = True
        h2 = P.servicenow_insights_page(m2, "all", {"not_read": F.not_read_lines(m2)})
        self.assertIn('<div class="callout">Work notes were not read (GET x: HTTP 403 - the account needs read): the fixes come from the close notes only.</div>', h2)
        self.assertIn("sys_journal_field (the incidents&#x27; work notes, for ACT&#x27;s fixes): GET x: HTTP 403", h2)


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
        """The leaderboard is the team's members (sys_user_grmember): a member with nothing resolved is on it, a service desk
        person who closed a Windows ticket is not - that ticket counts for the member it was assigned to, or in the team
        total when it was assigned to nobody of the team; who sends the team its tickets says where each opener works."""
        m = site()["model"]
        t = m["scopes"]["Ops - Windows"]
        self.assertLess(t["counts"]["incidents"], m["scopes"]["all"]["counts"]["incidents"])
        st = t["stars"]
        self.assertTrue(st["people"])
        self.assertTrue(st["members_known"] and m["members_read"])
        names = [p["name"] for p in st["people"]]
        self.assertEqual(sorted(names), ["Alex Morgan", "Jordan Kim", "Riley Chen", "Sam Patel"])
        self.assertEqual(next(p for p in st["people"] if p["name"] == "Riley Chen")["resolved"], 0)
        self.assertNotIn("Dana Whitfield", names)
        self.assertEqual(st["awards"][0]["what"], "Most resolved")
        self.assertEqual(st["total"]["resolved"], sum(p["resolved"] for p in st["people"]) + st["outside"]["count"])
        self.assertEqual(st["outside"]["count"], 0)                 # every ticket of the team was assigned to a member
        op = st["openers"]
        self.assertTrue(op and all(set(x) >= {"name", "opened", "from", "member", "end_user"} for x in op))
        self.assertTrue(any(x["from"] == "Ops - Service Desk" and not x["member"] for x in op))     # the desk opens most of them
        self.assertTrue(all("Ops - Windows" in c["groups"] for c in t["problems"]))
        # without the membership: everyone the tickets were assigned to or resolved by
        r2 = dict(site()["r"], members={}, members_error="HTTP 403")
        m2 = F.servicenow_insights_model(r2, None, site()["mask"], OPTS)
        st2 = m2["scopes"]["Ops - Windows"]["stars"]
        self.assertFalse(st2["members_known"] or m2["members_read"])
        self.assertIn("Dana Whitfield", [p["name"] for p in st2["people"]])
        self.assertNotIn("Riley Chen", [p["name"] for p in st2["people"]])
        self.assertIn("sys_user_grmember (the teams' members): HTTP 403", "; ".join(F.not_read_lines(m2)))

    def test_short_names(self):
        self.assertEqual(F.short_names(["Ops - Linux - Contractor", "Ops - Windows - Contractor", "Network - Contractor", "Change management"]),
                         {"Ops - Linux - Contractor": "Linux", "Ops - Windows - Contractor": "Windows", "Network - Contractor": "Network",
                          "Change management": "Change management"})
        # the shortest run of parts no other name holds, nearest the end; a dash without a space on one side splits too
        self.assertEqual(F.short_names(["North - Infra - Desk - Co", "North - Infra - Mail - Co", "North - Apps - Mail - Co", "South - Desk - Co",
                                        "Tier 1- Vendor", "Tier 2- Vendor"]),
                         {"North - Infra - Desk - Co": "Infra - Desk", "North - Infra - Mail - Co": "Infra - Mail", "North - Apps - Mail - Co": "Apps",
                          "South - Desk - Co": "South", "Tier 1- Vendor": "Tier 1", "Tier 2- Vendor": "Tier 2"})
        self.assertEqual(F.short_names(["Ops - Linux", "Ops - Linux", "Ops - Linux - Desk"]), {"Ops - Linux": "Ops - Linux", "Ops - Linux - Desk": "Desk"})
        self.assertEqual(F.short_names(["Service-Desk"]), {"Service-Desk": "Service-Desk"})
        self.assertEqual(site()["model"]["short"]["Ops - Windows"], "Windows")

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
        # the script: loaded from the website's site-insights.js and carried in the page (site_report_web writes the file)
        self.assertIn('<script src="site-insights.js"></script><script id="site-report-js" data-file="site-insights.js">', h)
        self.assertIn("if(window.siteInsights)return;", h)
        # without any script: the teams are links - the picker in the bar (a details menu) and the side bar - short names,
        # the whole one as the title; the charts' values are <title>s
        self.assertIn('<details class="picker" id="picker"><summary title="Switch team (t)"', h)
        self.assertIn('<span class="pk-l">Team</span><span class="pk-v">All assignment groups</span>', h)
        self.assertIn('<li class="all"><a href="latest.html" data-n="all assignment groups all assignment groups" data-g="" title="All assignment groups" '
                      'aria-current="page"><span>All assignment groups</span></a></li>', h)
        self.assertIn('<li><a href="ops-windows/latest.html" data-n="ops - windows windows" data-g="" title="Ops - Windows"><span>Windows</span></a></li>', h)
        self.assertIn('<a class="team" href="ops-windows/latest.html" data-n="ops - windows windows" data-g="" title="Ops - Windows">Windows</a>', h)
        # A to Z everywhere (0.96.0): the picker, the side bar, Teams side by side - not the busiest first
        for where in (h.split('id="picklist"')[1].split("</ul>")[0], h.split('<nav class="toc"')[1].split("</nav>")[0],
                      h.split('id="teams"')[1].split("</table>")[0]):
            got = re.findall(r'href="(ops-[a-z-]+)/latest.html"', where)
            self.assertEqual(got, sorted(got), where[:200])
            self.assertEqual(len(got), 5)
        self.assertIn('<input class="tf js" id="teamq" type="search"', h)
        self.assertNotIn('class="teamnav"', h)
        self.assertNotIn('id="grp"', h)
        self.assertRegex(h, r'<rect class="hit"[^>]*data-t="[^"]*"><title>Week of ')
        self.assertRegex(h, r'<rect [^>]*fill="var\(--heat\)"[^>]*><title>Mon ')
        # the close notes after the All-stars, folded; the issues say they open; the paths show short group names with the full one as a title
        self.assertLess(h.index('id="allstars"'), h.index('id="notes"'))
        self.assertIn('<details class="notes"><summary>', h)
        self.assertIn("Click an issue to open its fix action", h)
        self.assertIn('<span class="hint">Fix <span class="caret">›</span></span>', h)
        self.assertIn('<a class="hop glink" href="ops-service-desk/latest.html" title="Ops - Service Desk">Service Desk</a>', h)   # a group: its page
        self.assertRegex(h, r'<a class="glink" href="ops-[a-z-]+/latest.html"><text [^>]*class="lbl">')         # ... in a chart's bars too
        self.assertIn('mostly <a class="glink" href="ops-', h)                                                  # ... and an issue's main group
        self.assertIn("Who sends this team its tickets", h)
        self.assertIn('<span class="chip">end user</span>', h)

    def test_team_page_and_escaping(self):
        m = json.loads(json.dumps(site()["model"]))
        t = m["scopes"]["Ops - VDI"]
        t["summary"] = ["<script>alert(1)</script> & more"]
        h = P.servicenow_insights_page(m, "Ops - VDI", {"links": {"all": "../latest.html"}})
        self.assertNotIn("<script>alert(1)</script>", h)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; &amp; more", h)
        self.assertNotIn('id="teams"', h)
        self.assertIn('href="../latest.html"', h)
        self.assertEqual(h.count("<script"), 2)                  # the file's tag and the page's own copy, nothing else
        self.assertIn('<a class="team" href="../latest.html" data-n="all assignment groups all assignment groups" data-g="" title="All assignment groups">'
                      'All assignment groups</a>', h)                     # the site's page, from a team's: a plain link
        self.assertIn('<span class="pk-v">VDI</span>', h)                                    # the picker says which team

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



class TeamOrder(unittest.TestCase):
    """0.96.0: the teams A to Z (the user: "not in alphabetical order") - case and spaces ignored, numbers as numbers -
    under their prefix only where that is clean; All assignment groups first."""

    def test_natural_and_case(self):
        names = ["ops - Windows", " Net - Edge 10", "Net - Edge 2", "net - core", "Ops - linux"]
        self.assertEqual(sorted(names, key=P.natural_key), ["net - core", "Net - Edge 2", " Net - Edge 10", "Ops - linux", "ops - Windows"])
        self.assertEqual(sorted(["Team 10", "team 2", "Team 1"], key=P.natural_key), ["Team 1", "team 2", "Team 10"])

    def test_groups_only_when_clean(self):
        self.assertEqual(P.team_groups(["Ops - Windows", "ops - Linux", "Net - Core", "Net - Edge"]),
                         [("Net", ["Net - Core", "Net - Edge"]), ("ops", ["ops - Linux", "Ops - Windows"])])
        self.assertEqual(P.team_groups(["Ops - Windows", "Ops - Linux", "Service Desk"]), [("", ["Ops - Linux", "Ops - Windows", "Service Desk"])])
        self.assertEqual(P.team_groups(["Ops - Windows", "Ops - Linux"]), [("", ["Ops - Linux", "Ops - Windows"])])      # one prefix: none
        self.assertEqual(P.team_groups(["A - x", "B - y"]), [("", ["A - x", "B - y"])])                                  # none holds two

    def test_grouped_page(self):
        m = json.loads(json.dumps(site()["model"]))
        m["teams"] = ["Net - Core", "Ops - Windows", "Net - Edge", "Ops - Linux"]
        links = dict((t, F._slug(t) + "/latest.html") for t in m["teams"])
        links["all"] = "latest.html"
        h = P.servicenow_insights_page(m, "all", {"links": links})
        pick = h.split('id="picklist"')[1].split("</ul>")[0]
        self.assertTrue(pick.split('>', 1)[1].startswith('<li class="all">'))                               # All assignment groups first
        self.assertEqual(re.findall(r'<li class="tg" aria-hidden="true">([^<]+)</li>', pick), ["Net", "Ops"])
        self.assertEqual(re.findall(r'title="([^"]+)"><span>', pick), ["Net - Core", "Net - Edge", "Ops - Linux", "Ops - Windows"])
        self.assertIn('data-g="Net" title="Net - Core"><span>Core</span>', pick)              # under its prefix: the rest of the name
        self.assertIn('<div class="tg" aria-hidden="true">Ops</div><a class="team" href="ops-linux/latest.html"', h)


    def test_site_dept_team_suffix_shape(self):
        """Names shaped "<SITE> - <Dept> - <Team> - <SUFFIX>" (made up): grouped by the site, the shared suffix dropped from
        what is shown, the same team name under two sites told apart by its heading (and the picker's label)."""
        names = ["HQX - Ops - Mobility - CORE", "HQX - Ops - ASD - CORE", "HQY - Service Desk - CORE", "HQY - RITM Staging",
                 "HQX - RITM Staging", "HQY - Ops - Mobility - CORE", "HQX - Network - CORE"]
        self.assertEqual([g for g, _ in P.team_groups(names)], ["HQX", "HQY"])
        self.assertEqual(P.shared_suffix(names), "core")
        self.assertEqual(P.shared_suffix(["A - x - CORE", "A - y", "B - z"]), "")                    # one name: not shared
        m = json.loads(json.dumps(site()["model"]))
        m["teams"] = names
        team = "HQY - Ops - Mobility - CORE"
        m["scopes"][team] = dict(m["scopes"]["Ops - VDI"], team=team, name=team)
        links = dict((t, F._slug(t) + "/latest.html") for t in names)
        links["all"] = "../latest.html"
        h = P.servicenow_insights_page(m, team, {"links": links})
        pick = h.split('id="picklist"')[1].split("</ul>")[0]
        got = re.findall(r'<li class="tg" aria-hidden="true">([^<]+)</li>|title="([^"]+)"(?: aria-current="page")?><span>([^<]+)</span>', pick)
        self.assertEqual([x[0] or "%s=%s" % (x[1], x[2]) for x in got], [
            "All assignment groups=All assignment groups", "HQX", "HQX - Network - CORE=Network", "HQX - Ops - ASD - CORE=Ops - ASD",
            "HQX - Ops - Mobility - CORE=Ops - Mobility", "HQX - RITM Staging=RITM Staging", "HQY", "HQY - Ops - Mobility - CORE=Ops - Mobility",
            "HQY - RITM Staging=RITM Staging", "HQY - Service Desk - CORE=Service Desk"])
        self.assertIn('<span class="pk-v">HQY · Ops - Mobility</span>', h)                          # which site's, in the bar
        self.assertIn('data-n="hqy - ops - mobility - core ops - mobility" data-g="HQY"', h)          # the filter finds it by either
        self.assertNotIn(">CORE<", h)


class BriefPage(unittest.TestCase):
    """0.92.0: the website's brief look (report_web_theme: brief - opts look) - the brief palette, the ruled figures and
    the verdict at the top; any other look leaves the page as it was."""

    def test_brief(self):
        m = site()["model"]
        h = P.servicenow_insights_page(m, "all", {"look": "brief", "generated": "now", "job": "7"})
        self.assertIn(P.BRIEF_CSS, h)
        self.assertRegex(h, r'<div class="brief-hero"><div class="eyebrow">Ticket insights &middot; All assignment groups</div>'
                            r'<div class="verdict c-(warn|good)"><svg viewBox')
        self.assertRegex(h, r"<span>(Falling behind|Keeping up)</span></div><p class=\"say\">Resolved <b>[\d,]+</b> of <b>[\d,]+</b> opened")
        self.assertLess(h.index('class="brief-hero"'), h.index('id="glance"'))
        self.assertEqual(h.count("<section"), h.count("</section>"))

    def test_calm_sizes(self):
        """0.94.0: Keeping up / Falling behind a chip, not a poster word; the figures at a reading size; the logo larger."""
        self.assertIn(".brief-hero .verdict{display:inline-flex;", P.BRIEF_CSS)
        for v, unit in re.findall(r"font-size:(?:clamp\([^;]*?,\s*)?([\d.]+)(rem|px)", P.BRIEF_CSS):
            self.assertLessEqual(float(v) * (16 if unit == "rem" else 1), 34, (v, unit))
        self.assertIn(".logoimg{height:52px}", P.BRIEF_CSS)

    def test_other_looks_unchanged(self):
        m = site()["model"]
        for look in (None, "atlas", "portal"):
            h = P.servicenow_insights_page(m, "all", {"look": look, "generated": "now", "job": "7"} if look else {"generated": "now", "job": "7"})
            self.assertNotIn("brief-hero", h)
            self.assertNotIn("Segoe UI Variable Display", h)

    def test_the_readers_light_or_dark(self):
        """The page takes the light / dark choice made on the website's other pages (the same key)."""
        self.assertIn('localStorage.getItem("site-report-theme")', P.SCRIPT)


class SlaTargets(unittest.TestCase):
    O = {"sla_targets": {"incident": {"1": 4, "P2": 8, "*": 72}, "sc_req_item": {"*": 120}},
         "sla_items": {"New account": 48}}

    def it(self, table, prio, hours, made=True, item=None):
        o = datetime.datetime(2026, 10, 1, 8, 0, tzinfo=datetime.timezone.utc)
        d = (o + datetime.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S") if hours is not None else ""
        return {"table": table, "priority_value": prio, "opened_at": o.strftime("%Y-%m-%d %H:%M:%S"), "done_at": d,
                "made_sla": made, "cat_item": item}

    def test_by_priority_star_and_catalog_item(self):
        self.assertEqual(F.sla_target(self.it("incident", "1", 1), self.O), 4.0)
        self.assertEqual(F.sla_target(self.it("incident", "2", 1), self.O), 8.0)        # "P2" written so
        self.assertEqual(F.sla_target(self.it("incident", "4", 1), self.O), 72.0)       # "*"
        self.assertEqual(F.sla_target(self.it("sc_req_item", "3", 1, item="New account"), self.O), 48.0)
        self.assertEqual(F.sla_target(self.it("sc_req_item", "3", 1, item="Laptop"), self.O), 120.0)
        self.assertIsNone(F.sla_target(self.it("sc_task", "3", 1), self.O))             # not listed: ServiceNow's flag
        self.assertIsNone(F.sla_target(self.it("incident", "1", 1), {}))

    def test_met_from_the_target_not_the_flag(self):
        xs = [self.it("incident", "1", 3, made=False), self.it("incident", "1", 5, made=True),
              self.it("incident", "1", None, made=True), self.it("sc_task", "1", 500, made=True)]
        F.apply_sla_targets(xs, self.O)
        self.assertEqual([x["made_sla"] for x in xs], [True, False, None, True])
        self.assertEqual(xs[1]["sn_made_sla"], True)
        self.assertEqual(xs[0]["sla_target_h"], 4.0)

    def test_role_defaults_are_targets(self):
        import yaml
        d = yaml.safe_load(open(os.path.join(HERE, "..", "roles", "servicenow_insights", "defaults", "main.yml")))
        self.assertEqual(d["servicenow_insights_sla_targets"]["incident"]["1"], 8)
        self.assertEqual(set(d["servicenow_insights_sla_targets"]), {"incident", "sc_req_item"})

if __name__ == "__main__":
    unittest.main()
