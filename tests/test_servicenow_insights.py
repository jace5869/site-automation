#!/usr/bin/env python3
"""Unit tests for the ticket insights: the pure parts of roles/servicenow_insights/library/site_servicenow_insights.py (the window,
the query, a record), plugins/filter/servicenow_insights_filters.py (close notes graded, the masking and its way back, ACT's
questions and answers merged, the model on a made-up site - tests/servicenow/insights_seed.py -, the email's report, the
workbook, the links between pages) and plugins/filter/servicenow_insights_page.py (the page: every section, no placeholder,
no unescaped text). Needs ansible-core importable.
    python3 tests/test_servicenow_insights.py"""
import copy
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
sys.path.insert(0, os.path.join(HERE, "..", "roles", "servicenow_insights", "library"))
sys.path.insert(0, os.path.join(HERE, "servicenow"))
import servicenow_insights_filters as F  # noqa: E402
import servicenow_insights_page as P  # noqa: E402
import site_servicenow_insights as rd  # noqa: E402
import servicenow_insights_history as H  # noqa: E402
import snmock  # noqa: E402

NOW = datetime.datetime(2026, 11, 1, 6, 0, tzinfo=rd.UTC)
OPTS = {"bare_chars": 20, "bare_phrases": ["fixed", "done", "resolved", "resolved per user", "see work notes", "rebooted", "n/a",
                                            "user confirmed", "completed"], "act": False, "kb_min": 50, "hierarchy_source": "names"}
_SITE = {}
_SITES = {}
_SURVEYS = {}
HIER_OPTS = dict(OPTS, directorates=["CODEX"], suffix_directorates=["NOVA"])                      # the names (OPTS: hierarchy_source names)
PARENTS_OPTS = dict(OPTS, hierarchy_source="parents")


def site(sites=False, surveys=False):
    """The made-up site (insights_seed.py), read as the module reads it - once. sites: its groups named as three sites with
    directorates (--sites, 0.97.0); surveys: two surveys, one retired, on different scales (--surveys, 1.16.0)."""
    _SITE = _SURVEYS if surveys else (_SITES if sites else globals()["_SITE"])
    if not _SITE:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "seed.json")
            subprocess.check_call([sys.executable, os.path.join(HERE, "servicenow", "insights_seed.py"), path, "0.4"] + (["--sites"] if sites else [])
                                  + (["--surveys"] if surveys else []),
                                  stdout=subprocess.DEVNULL)
            with open(path) as fh:
                seed = json.load(fh)
        win = rd.window(6, 0, "2026-10-31", True, "", NOW)
        since, until = rd.sn_time(win["prev_since"]), rd.sn_time(win["until"])
        items, audit, members, notes, gtree = [], {}, {}, {}, {}
        slas, states, first, survey, paused, running = {}, {}, {}, [], {}, {}   # 1.7.0: as the module reads them
        questions = {}                                                         # 1.16.0
        for r in seed:
            t = r.get("_table")
            if t == "task_sla":
                if r["stage"] != "cancelled":
                    x_ = {"name": r["sla"], "stage": r["stage"], "breached": r["has_breached"] == "true", "percent": float(r["business_percentage"]),
                          "paused_at": r.get("pause_time", ""), "running": r["stage"] in ("in_progress", "paused"),
                          "left_s": rd.duration(r.get("business_time_left")), "planned_end": ""}
                    (running if x_["running"] else slas).setdefault(r["task"], []).append(x_)
                    if r["stage"] == "paused":
                        paused[r["task"]] = r["pause_time"]
                continue
            if t == "asmt_metric_result":
                try:
                    survey.append([r["instance.trigger_id"], r["metric"], float(r["actual_value"]), r["instance.taken_on"]])
                except ValueError:
                    continue
                st = (r.get("metric.metric_type.state") or "").lower()
                questions.setdefault(r["metric"], {"name": r.get("metric.name") or r["metric"], "survey": r.get("metric.metric_type.name", ""),
                                                   "state": "retired" if "retired" in st or r.get("metric.metric_type.active") == "false" else
                                                   ("active" if st or r.get("metric.metric_type.active") else ""),
                                                   "datatype": r.get("metric.datatype", ""), "min": float(r["metric.min"]) if r.get("metric.min") else None,
                                                   "max": float(r["metric.max"]) if r.get("metric.max") else None, "template": ""})
                continue
            if t == "metric_instance":
                if r["definition"] == "Incident State Duration":
                    states.setdefault(r["id"], []).append([r["value"], r["start"], r["end"]])
                elif r["definition"] == "Assigned to":
                    first[r["id"]] = min(first.get(r["id"], r["start"]), r["start"])
                continue
            if t == "sys_user_group":
                gtree[r["sys_id"]] = {"name": r["name"], "parent": r["parent"], "active": r["active"] != "false", "type": r["type"]}
                continue
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
            it = rd.normalize(dict((k, {"value": v, "display_value": {"1": "New", "2": "In Progress", "3": "On Hold", "6": "Resolved", "7": "Closed"}.get(v, v)
                                                                  if (t, k) == ("incident", "state") else v})
                                   for k, v in r.items() if not k.startswith("_")), t)
            if rd.keep(it, since, until):
                items.append(it)
        _SITE["r"] = {"ok": True, "window": win, "items": items, "audit": audit, "members": members, "groups": [], "url": "https://sn.example.mil",
                      "group_tree": gtree,
                      "tables": [{"table": "incident", "read": 10, "kept": 10, "more": False, "error": ""}],
                      "sla": {"source": "task_sla", "asked": "auto", "error": "", "note": "", "records": slas, "paused": paused, "paused_error": "",
                              "open": running, "open_cut": []},
                      "first_assigned": first, "first_assigned_error": "", "states": states, "states_error": "", "survey": survey, "survey_error": "",
                      "survey_questions": questions}
        _SITE["mask"] = F.servicenow_insights_mask(_SITE["r"], OPTS)
        _SITE["work_notes"] = {"work_notes": dict((k, sorted(v, reverse=True)) for k, v in notes.items()), "work_notes_error": ""}
        _SITE["model"] = F.servicenow_insights_model(_SITE["r"], None, _SITE["mask"], HIER_OPTS if sites else OPTS)
    return _SITE


def page_links(m, rel=""):
    """Every page's link as the role makes them (web_links): the All page, each site's, directorate's and team's."""
    links = dict((p["key"], rel + "/".join(p["path"]) + "/latest.html") for p in m["pages"])
    links["all"] = rel + "latest.html"
    return links


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
                            "^ORresolved_at>=2026-04-30 00:00:00^opened_at<=2026-11-02 00:00:00^ORDERBYDESCopened_at")
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

    def test_at_a_sentence_end_and_with_an_underscore(self):
        """1.23.0: a host, an FQDN or an ID before a full stop, and a name with "_", are masked - each text on its own."""
        for text, real in (("Rebooted web01.", "web01"), ("Restarted app02.corp.example.mil.", "app02.corp.example.mil"),
                           ("EDIPI 1234567890.", "1234567890"), ("Cleared queue on SQL_PROD01", "SQL_PROD01"),
                           ("srv01_backup is full.", "srv01_backup")):
            m = F.servicenow_insights_mask({"items": [{"table": "incident", "sys_id": "1", "short_description": text}]}, OPTS)
            out = F.apply_mask(text, m)
            self.assertNotIn(real, out, text)
            self.assertEqual(F.unmask(out, m), text)
        # a version, a ticket and a word with a full stop inside stay as they are
        text = "Updated to win11. See INC0040001. Use x86_64."
        m = F.servicenow_insights_mask({"items": [{"table": "incident", "sys_id": "1", "short_description": text}]}, OPTS)
        self.assertEqual(F.apply_mask(text, m), text)

    def test_a_last_name_that_is_a_word(self):
        """1.23.0: a last name from the people fields is masked whatever the word."""
        for name in ("Alex Smith", "Sam Page", "Jordan Young"):
            last = name.split()[1]
            r = {"items": [{"table": "incident", "assigned_to": name, "short_description": "Called %s about the printer" % last}]}
            m = F.servicenow_insights_mask(r, OPTS)
            self.assertNotIn(last, F.apply_mask(r["items"][0]["short_description"], m))

    def test_a_short_or_word_like_last_name_only_as_a_name(self):
        """1.23.0 b: "Do" and "Page" masked where written as a name, "do" and "page" kept (and kept in desc_key); the
        full name in any case; "Last, First" masks the last name; a suffix is not a last name."""
        r = {"items": [{"table": "incident", "caller": "Tran Do", "assigned_to": "Pat Page", "opened_by": "Smith, Kim",
                        "resolved_by": "Mary Ann Jones Jr.", "short_description": "page file full - what do I do"}]}
        m = F.servicenow_insights_mask(r, OPTS)
        out = F.apply_mask("Do called; what do I do with the page file? pat page and TRAN DO agree. Smith wrote, Jones Jr. too", m)
        for real in ("Do called", "pat page", "TRAN DO", "Smith", "Jones"):
            self.assertNotIn(real, out)
        for kept in ("what do I do", "the page file", "Jr."):
            self.assertIn(kept, out)
        self.assertEqual(F.desc_key("page file full - what do I do", m), "page file full what do i do")
        self.assertNotIn("Jr.", [p[0] for p in m["pairs"]])
        self.assertEqual(F.servicenow_insights_mask_more(m, {"work_notes": {}}, OPTS)["exact"], m["exact"])

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


class HandoffPaths(unittest.TestCase):
    """0.97.0: the hand-off paths from sys_audit, else the "Assignment Group" metric (metric_instance) - GET only."""

    class Api(object):
        url = "https://sn.example.mil"

        def __init__(self, deny=()):
            self.calls, self.deny = [], deny

        def get(self, path, params):
            self.calls.append((path, params))
            table = path.rsplit("/", 1)[1]
            if table in self.deny:
                raise rd.SnError("GET %s: HTTP 403: Operation Failed: ACL Exception: no read access to %s" % (path, table))
            if table == "sys_audit":
                return {"result": [{"documentkey": "a", "oldvalue": "g1", "newvalue": "g2", "sys_created_on": "2026-10-01 10:00:00"}]}
            return {"result": [{"id": "a", "value": "Desk", "start": "2026-10-01 09:00:00"}, {"id": "a", "value": "g2", "start": "2026-10-01 10:00:00"},
                               {"id": "a", "value": "Desk", "start": "2026-10-01 12:00:00"}]}

    def test_sources(self):
        names = {"g1": "Desk", "g2": "Windows"}
        api = self.Api()
        self.assertEqual(rd.read_paths(api, ["a"], names), ({"a": [["2026-10-01 10:00:00", "Desk", "Windows"]]}, "sys_audit", "", ""))
        api = self.Api(deny=("sys_audit",))
        paths, src, err, note = rd.read_paths(api, ["a"], names)
        self.assertEqual((src, err), ("metric_instance", ""))
        self.assertEqual(paths["a"], [["2026-10-01 09:00:00", "", "Desk"], ["2026-10-01 10:00:00", "Desk", "Windows"], ["2026-10-01 12:00:00", "Windows", "Desk"]])
        self.assertIn("sys_audit: GET api/now/table/sys_audit: HTTP 403", note)
        q = api.calls[-1][1]
        self.assertEqual(q["sysparm_query"], "definition.name=Assignment Group^table=incident^idINa^ORDERBYstart")
        self.assertEqual(q["sysparm_fields"], "id,value,start,end")
        paths, src, err, note = rd.read_paths(self.Api(deny=("sys_audit", "metric_instance")), ["a"], names)
        self.assertEqual((paths, src), ({}, ""))
        self.assertIn("sys_audit: ", err)
        self.assertIn("metric_instance: ", err)
        api = self.Api()
        self.assertEqual(rd.read_paths(api, ["a"], names, "metric")[1], "metric_instance")
        self.assertEqual([c[0] for c in api.calls], ["api/now/table/metric_instance"])                # metric: sys_audit never asked
        self.assertEqual(rd.read_paths(self.Api(deny=("sys_audit",)), ["a"], names, "audit")[1:3][0], "")
        self.assertEqual(rd.read_paths(self.Api(), ["a"], names, "none"), ({}, "", "", ""))
        m = {"audit_error": "sys_audit: GET x: HTTP 403; metric_instance: GET y: HTTP 403", "audit_note": ""}
        self.assertIn("sys_audit and metric_instance (the hand-off paths): sys_audit: GET x: HTTP 403; metric_instance", "\n".join(F.not_read_lines(m)))
        self.assertIn("the hand-off paths: sys_audit: x - the paths from metric_instance instead",
                      "\n".join(F.not_read_lines({"audit_note": "sys_audit: x - the paths from metric_instance instead"})))


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
        self.assertEqual(st["awards"][0]["what"], "Most closed")                 # the change tasks read (1.2.0): closed
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
        links = page_links(m)
        h = P.servicenow_insights_page(m, "all", {"links": links, "generated": "2026-11-01 06:05 UTC", "job": "7"})
        for sid in ("glance", "teams", "keepingup", "severity", "issues", "problems", "handoffs", "requests", "changes", "notes", "allstars", "kb", "method"):
            self.assertIn('id="%s"' % sid, h)
        self.assertIn('href="ops/other/windows/latest.html"', h)
        self.assertIn("https://sn.example.mil/nav_to.do?uri=incident.do%3Fsysparm_query%3Dnumber%3DINC", h)
        self.assertIn("<!--site-report-logo-->", h)
        self.assertIn("AAP job 7", h)
        self.assertNotRegex(h, r"\b(PERSON|HOST|PHONE)-\d+\b")
        self.assertEqual(h.count("<section"), h.count("</section>"))
        self.assertEqual(h.count("<details"), h.count("</details>"))
        # the script: loaded from the website's site-insights.js and carried in the page (site_report_web writes the file)
        self.assertIn('<script src="site-insights.js"></script><script id="site-report-js" data-file="site-insights.js">', h)
        self.assertIn("if(window.siteInsights)return;", h)
        # without any script: every page is a link - the menu in the bar (a details menu) and the side bar's tree; the charts'
        # values are <title>s (0.97.0: sites, directorates, teams)
        self.assertIn('<details class="picker" id="picker"><summary title="Go to a site, directorate or team (t)"', h)
        self.assertIn('<span class="pk-l">View</span><span class="pk-v">All assignment groups</span>', h)
        self.assertIn('<li class="lv0 all"><a href="latest.html" data-n="all assignment groups all assignment groups" data-c="" '
                      'title="All assignment groups" aria-current="page">All assignment groups</a></li>', h)
        self.assertIn('<li class="lv3"><a href="ops/other/windows/latest.html" data-n="ops - windows windows" data-c="ops other" title="Ops - Windows">Windows</a></li>', h)
        self.assertIn('<details class="tn lv1" open><summary><a class="team lv1" href="ops/latest.html"', h)
        self.assertIn('<a class="team lv3" href="ops/other/windows/latest.html" data-n="ops - windows windows" data-c="ops other" title="Ops - Windows">Windows</a>', h)
        self.assertIn('<input class="tf js" id="teamq" type="search"', h)
        self.assertIn('<nav class="crumbs" aria-label="Where this page is"><b>All</b></nav>', h)
        self.assertNotIn('class="teamnav"', h)
        self.assertNotIn('id="grp"', h)
        self.assertRegex(h, r'<rect class="hit"[^>]*data-t="[^"]*"><title>Week of ')
        self.assertRegex(h, r'<rect [^>]*fill="var\(--heat\)"[^>]*><title>Mon ')
        # the close notes after the All-stars, folded; the issues say they open; the paths show short group names with the full one as a title
        self.assertLess(h.index('id="allstars"'), h.index('id="notes"'))
        self.assertIn('<details class="notes"><summary>', h)
        self.assertIn("Click an issue to open its fix action", h)
        self.assertIn('<span class="hint">Fix <span class="caret">›</span></span>', h)
        self.assertIn('<a class="hop glink" href="ops/other/service-desk/latest.html" title="Ops - Service Desk">Service Desk</a>', h)   # a group: its page
        self.assertRegex(h, r'<a class="glink" href="ops/other/[a-z-]+/latest.html"><text [^>]*class="lbl">')         # ... in a chart's bars too
        self.assertIn('mostly <a class="glink" href="ops/other/', h)                                                  # ... and an issue's main group
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
        self.assertEqual(h.count("<script"), 3)                  # the file's tag, the page's own copy and its rows (pp-data), nothing else
        self.assertEqual(h.count('<script type="application/json" id="pp-data">'), 1)
        self.assertIn('<a class="team lv0" href="../latest.html" data-n="all assignment groups all assignment groups" data-c="" title="All assignment groups">'
                      'All assignment groups</a>', h)                     # the site's page, from a team's: a plain link
        self.assertIn('<span class="pk-v">Other · VDI</span>', h)                             # the picker says where
        self.assertIn('<nav class="crumbs" aria-label="Where this page is"><a href="../latest.html">All</a>', h)

    def test_empty_site(self):
        win = rd.window(1, 0, "2026-10-31", True, "", NOW)
        m = F.servicenow_insights_model({"ok": True, "window": win, "items": [], "tables": []}, None, None, OPTS)
        h = P.servicenow_insights_page(m, "all", {})
        self.assertIn("No incidents in the period.", h)
        self.assertEqual(F.servicenow_insights_doc(m, "all", OPTS)["status"], "info")

    def test_ticket_links(self):
        self.assertEqual(P.tk("CHG0031001", "https://sn/"), '<a class="tk" target="_blank" rel="noopener" '
                                                            'href="https://sn/nav_to.do?uri=change_request.do%3Fsysparm_query%3Dnumber%3DCHG0031001">CHG0031001</a>')
        # 1.18.0: where they go - the API's instance, the address people use, or none
        B = P.ticket_base
        self.assertEqual(B({"url": "https://api.example.mil"}, {}), "https://api.example.mil")
        self.assertEqual(B({"url": "https://api.example.mil"}, {"ticket_base": "sn.example.mil/"}), "https://sn.example.mil")
        self.assertEqual(B({"url": "https://api.example.mil"}, {"ticket_links": False}), "")
        self.assertEqual(B({"url": "https://api.example.mil"}, {"ticket_base": "javascript:alert(1)"}), "")
        self.assertEqual(B({"url": "https://api.example.mil"}, {"ticket_base": 'https://sn.example.mil/"><b>'}), "")
        # 1.30.0: the scheme in any case
        self.assertEqual(B({"url": "https://api.example.mil"}, {"ticket_base": "HTTPS://SN.EXAMPLE.MIL"}), "https://SN.EXAMPLE.MIL")
        self.assertEqual(B({"url": "Http://sn:8080/"}, {}), "http://sn:8080")
        self.assertEqual(B({"url": "https://api.example.mil"}, {"ticket_base": "JAVASCRIPT:alert(1)"}), "")
        m = site()["model"]
        h = P.servicenow_insights_page(m, "all", {"ticket_base": "https://sn.example.mil:8443/"})
        self.assertIn('href="https://sn.example.mil:8443/nav_to.do?uri=incident.do%3Fsysparm_query%3Dnumber%3DINC', h)
        h = P.servicenow_insights_page(m, "all", {"ticket_links": False})
        self.assertNotIn("nav_to.do", h)
        self.assertNotIn('class="tk"', h)
        self.assertIn('<span class="mono">INC', h)
        self.assertIn("mono", P.tk("XYZ1", "https://sn"))
        self.assertTrue(re.search("—", P.tk("", "https://sn")))

    def test_own_prefixes(self):
        # 1.30.0: an instance numbering its incidents XINC... - the table learned from the records read
        r = json.loads(json.dumps(site()["r"]))
        for it in r["items"]:
            if it["table"] == "incident":
                it["number"] = "X" + it["number"]
        m = F.servicenow_insights_model(r, None, F.servicenow_insights_mask(r, OPTS), OPTS)
        self.assertEqual(m["prefixes"].get("XINC"), "incident")
        h = P.servicenow_insights_page(m, "all", {})
        self.assertIn('href="https://sn.example.mil/nav_to.do?uri=incident.do%3Fsysparm_query%3Dnumber%3DXINC', h)
        self.assertNotIn('<span class="mono">XINC', h)
        # a prefix no record carries and no setting names: the number alone; the setting names it
        self.assertEqual(P.tk("XPRB0001", "https://sn", m["prefixes"]), '<span class="mono">XPRB0001</span>')
        pre = F.prefix_map(r["items"], {"ticket_prefixes": {"xprb": "problem"}})
        self.assertIn('href="https://sn/nav_to.do?uri=problem.do%3Fsysparm_query%3Dnumber%3DXPRB0001"', P.tk("XPRB0001", "https://sn", pre))
        self.assertEqual(F.prefix_map([{"table": "incident", "number": "XINC1"}], {"ticket_prefixes": {"XINC": "problem"}}), {"XINC": "incident"})
        self.assertIn("CHG0031001", P.tk("CHG0031001", "https://sn", pre))
        # the setting checked: letters, a table name
        self.assertEqual(F.servicenow_insights_prefix_errors({"XINC": "incident", "xy": "u_table_2"}), [])
        self.assertEqual(F.servicenow_insights_prefix_errors({"X1": "incident", "XINC": "incident.do?x", "Y": ""}),
                         ["X1: incident", "XINC: incident.do?x", "Y: "])
        self.assertEqual(F.servicenow_insights_prefix_errors(["XINC"]), ["['XINC']"])
        self.assertEqual(F.servicenow_insights_prefix_errors(None), [])



class TeamOrder(unittest.TestCase):
    """0.96.0: the teams A to Z (the user: "not in alphabetical order") - case and spaces ignored, numbers as numbers -
    under their prefix only where that is clean; All assignment groups first."""

    def test_natural_and_case(self):
        names = ["ops - Windows", " Net - Edge 10", "Net - Edge 2", "net - core", "Ops - linux"]
        self.assertEqual(sorted(names, key=P.natural_key), ["net - core", "Net - Edge 2", " Net - Edge 10", "Ops - linux", "ops - Windows"])
        self.assertEqual(sorted(["Team 10", "team 2", "Team 1"], key=P.natural_key), ["Team 1", "team 2", "Team 10"])



class Hierarchy(unittest.TestCase):
    """0.97.0: every group's site, directorate and team from its name (made-up names), the pages rolled up at each level,
    the tree, the breadcrumbs."""

    def test_rules(self):
        o = {"directorates": ["CODEX"], "hierarchy_map": {"SITEB -  odd one": {"site": "SITEA", "directorate": "J6", "team": "Odd"}},
             "directorate_alias": {"j602": "J60"}, "hierarchy_source": "names", "suffix_directorates": ["NOVA"]}
        got = dict((n, F.hier_place(n, o)) for n in [
            "SITEA - Ops - Platforms - NOVA", "SITE-C - Ops - Platforms - nOvA", "SITEA - J63 - Platforms - NOVA", "SITEA - j63 - Windows",
            "SITEA - J602 - Help", "SITE-C - codex - Linux", "SITEB - Network", "SITEB - Odd One", "Change management", "SITEA - A6",
            "SITE-C - Post-Desk - Tier 2"])
        self.assertEqual(got["SITEA - Ops - Platforms - NOVA"], {"site": "SITEA", "dir": "NOVA", "team": "Ops - Platforms", "rule": "suffix", "under": ""})
        self.assertEqual(got["SITE-C - Ops - Platforms - nOvA"]["site"], "SITE-C")             # a hyphen is not a split
        self.assertEqual(got["SITE-C - Ops - Platforms - nOvA"]["dir"], "NOVA")               # any case
        self.assertEqual(got["SITEA - J63 - Platforms - NOVA"]["dir"], "NOVA")                # a suffix wins over a staff code
        self.assertEqual(F.hier_place("SITEA - Ops - Platforms - NOVA", dict(o, suffix_directorates=[]))["dir"], "Other")   # only when set
        self.assertEqual(got["SITEA - j63 - Windows"], {"site": "SITEA", "dir": "J63", "team": "Windows", "rule": "code", "under": ""})
        self.assertEqual(got["SITEA - J602 - Help"]["dir"], "J60")                              # an alias merges it
        self.assertEqual(got["SITE-C - codex - Linux"], {"site": "SITE-C", "dir": "CODEX", "team": "Linux", "rule": "named", "under": ""})
        self.assertEqual(got["SITEB - Network"], {"site": "SITEB", "dir": "Other", "team": "Network", "rule": "other", "under": ""})
        self.assertEqual(got["SITEB - Odd One"], {"site": "SITEA", "dir": "J6", "team": "Odd", "rule": "map", "under": ""})   # the map wins
        self.assertEqual(got["Change management"], {"site": "Other", "dir": "Other", "team": "Change management", "rule": "other", "under": ""})
        self.assertEqual(got["SITEA - A6"], {"site": "SITEA", "dir": "A6", "team": "A6", "rule": "code", "under": ""})
        self.assertEqual(got["SITE-C - Post-Desk - Tier 2"]["dir"], "Other")

    def test_parents_tags_unknown(self):
        o = {"directorates": ["CODEX"], "directorate_tags": {"COMPONENTX": "N6"}, "hierarchy_source": "names", "suffix_directorates": ["NOVA"]}
        place = F.hier_place
        self.assertEqual(place("SITEA - Ops - Mobility", o, ["SITEA - Mobile Wing - NOVA", "SITEA"])["dir"], "NOVA")   # a parent's NOVA
        self.assertEqual(place("SITEA - Ops - Mobility", o, ["SITEA - Mobile Wing - NOVA", "SITEA"])["rule"], "parent-suffix")
        self.assertEqual(place("SITEA - COMPONENTX Help Desk", o)["dir"], "N6")                     # a tag: a whole word
        self.assertEqual(place("SITEA - COMPONENTXY Help", o)["dir"], "Other")
        self.assertEqual(place("SITEA - Field Desk", o, ["SITEA - J633 Network Ops", "SITEA"])["dir"], "J633")   # the nearest parent's code
        self.assertEqual(place("SITEA - J633 Field Desk", o)["dir"], "J633")                         # a code in the name's words
        self.assertEqual(place("SITEB - IPv6 lab", o)["dir"], "Other")                               # not a staff code
        self.assertEqual(place("SITEB - Desk", o, ["SITEB - Client Support", "SITEB"]),
                         {"site": "SITEB", "dir": "Client Support", "team": "Desk", "rule": "parent", "under": ""})
        self.assertEqual(place("Field Desk", o, ["SITEB - Operations", "SITEB Root"])["site"], "SITEB Root")   # the site: the root's first part
        self.assertEqual(place("SITEA - Lone", o, ["SITEA"])["dir"], "Other")                           # the root is not a directorate
        self.assertEqual(F.group_chain("a", {"1": {"name": "a", "parent": "2"}, "2": {"name": "b", "parent": "3"}, "3": {"name": "c", "parent": "2"}}), ["b", "c"])
        known = {"known_groups_only": True, "hierarchy_map": {"SITEA - Desk": {"site": "SITEA", "directorate": "J6"}}, "hierarchy_source": "names"}
        self.assertEqual(place("SITEA - Desk", known)["dir"], "J6")
        self.assertEqual((place("SITEA - New Desk", known)["site"], place("SITEA - New Desk", known)["dir"]), ("SITEA", "Unknown"))
        self.assertEqual((place("SITEZ - New Desk", known)["site"], place("SITEZ - New Desk", known)["dir"]), ("Unknown", "Unknown"))

    def test_parents(self):
        """hierarchy_source parents (the default): ServiceNow's group tree - the root the site, the parent below it the
        directorate, a team under a team "under" it; no way up to a root: Unknown; the tree not read: the names' sites."""
        place = F.hier_place
        roots = {"SITEA - J6", "SITEB"}
        self.assertEqual(place("SITEA - J6 - Field Ops", {}, ["SITEA - J633 Network", "SITEA - J6"], roots),
                         {"site": "SITEA", "dir": "J633 Network", "team": "J6 - Field Ops", "rule": "parent", "under": ""})
        self.assertEqual(place("SITEA - Radio Shop", {}, ["SITEA - Field Ops", "SITEA - J633 Network", "SITEA - J6"], roots)["under"], "Field Ops")
        self.assertEqual(place("SITEA - Radio Shop", {}, ["SITEA - Field Ops", "SITEA - J633 Network", "SITEA - J6"], roots)["dir"], "J633 Network")
        self.assertEqual(place("SITEA - Front Desk", {}, ["SITEA - J6"], roots)["dir"], "J6")             # right under the root: the root
        self.assertEqual(place("SITEB - Front Desk", {}, ["SITEB"], roots)["dir"], "(site direct)")
        self.assertEqual(place("SITEB - Lost", {}, [], roots)["site"], "SITEB")                          # no parent: Unknown, its site by name
        self.assertEqual(place("SITEB - Lost", {}, [], roots)["dir"], "Unknown")
        self.assertEqual(place("SITEZ - Lost", {}, ["SITEZ - Loop"], roots)["site"], "Unknown")          # a chain that stops short
        self.assertEqual(place("SITEA - x", {"site_roots": ["SITEA - J633 Network"]}, ["SITEA - Field Ops", "SITEA - J633 Network", "SITEA - J6"], roots)["dir"],
                         "Field Ops")                                                                     # site_roots: the root named
        self.assertEqual(place("SITEA - x", {}, None, roots, tree_ok=False), {"site": "SITEA", "dir": "(unknown)", "team": "x", "rule": "names-fallback", "under": ""})
        self.assertEqual(place("SITEA - x", {"hierarchy_map": {"SITEA - x": {"site": "S", "directorate": "D"}}}, [], roots)["dir"], "D")   # the map: both modes
        x = site(sites=True)
        m = F.servicenow_insights_model(x["r"], None, x["mask"], PARENTS_OPTS)
        self.assertEqual([n["label"] for n in m["tree"]], ["SITEA", "SITEB", "Unknown"])
        self.assertEqual([[d["label"] for d in n["dirs"]] for n in m["tree"]], [["(site direct)", "J6 Staff Division"], ["(site direct)", "Client Support"], ["Unknown"]])
        self.assertEqual(m["hier"]["SITE-C - CODEX - Linux"]["rule"], "unknown")                          # its parents loop: no root
        self.assertEqual(m["hier"]["SITEA - J6 - Service Desk"]["team"], "J6 - Service Desk")
        self.assertEqual(m["hier_unknown"][0][0], "SITE-C - CODEX - Linux")
        self.assertEqual(sum(m["scopes"]["site:" + n["label"]]["counts"]["incidents"] for n in m["tree"]), m["scopes"]["all"]["counts"]["incidents"])
        m4 = F.servicenow_insights_model(dict(x["r"], group_tree={}, group_tree_error="GET x: HTTP 403"), None, x["mask"], PARENTS_OPTS)
        self.assertEqual(set(h["dir"] for h in m4["hier"].values()), {"(unknown)"})
        self.assertEqual(m4["tree"][0]["label"], "SITE-C")

    def test_split(self):
        """The split (servicenow_insights_split_label / _pattern): every page's figures for the matching groups and for the
        rest, worked out at build time; the switch links the three pages (a made-up label)."""
        x = site(sites=True)
        o = dict(PARENTS_OPTS, split_label="BLUE", split_pattern="*- nova")
        m = F.servicenow_insights_model(x["r"], None, x["mask"], o)
        self.assertEqual(m["split"], {"label": "BLUE", "groups": ["SITEB - Ops - VDI - nova"]})
        for k in ("all", "site:SITEB", "dir:SITEB/(site direct)", "site:SITEA"):
            for c_ in ("incidents", "requests", "resolved"):
                self.assertEqual(m["variants"]["in"][k]["counts"][c_] + m["variants"]["out"][k]["counts"][c_], m["scopes"][k]["counts"][c_], (k, c_))
        self.assertEqual(m["variants"]["in"]["site:SITEA"]["counts"]["incidents"], 0)
        self.assertEqual(sorted(m["variants"]["in"]), sorted(m["scopes"]))                    # the same pages, the same tree
        links = page_links(m)
        vl = {"": "latest.html", "in": "latest-blue.html", "out": "latest-non-blue.html"}
        h = P.servicenow_insights_page(m, "all", {"links": links, "variant": "in", "variant_links": vl})
        self.assertIn('<nav class="split" aria-label="Which groups"><a href="latest.html">All</a><a href="latest-blue.html" aria-current="page">BLUE</a>'
                      '<a href="latest-non-blue.html">non-BLUE</a></nav>', h)
        n_in = m["variants"]["in"]["all"]["counts"]["incidents"] + m["variants"]["in"]["all"]["counts"]["requests"]
        self.assertIn('<span class="eyebrow">Tickets</span><span class="v">%s' % "{:,}".format(n_in), h)
        self.assertNotIn('class="split"', P.servicenow_insights_page(m, "all", {"links": links}))      # no links given: no switch
        xl = F.servicenow_insights_xlsx(m, x["r"], None, x["mask"], o)
        self.assertEqual(xl[0]["columns"][10], "BLUE")
        self.assertEqual(sorted(set(r_[10] for r_ in xl[0]["rows"])), ["no", "yes"])
        w = {"all": {"page": "t/2026_job-1.html", "latest": "t/latest.html"}, "pages": {"site:SITEA": {"page": "t/sitea/latest.html"}},
             "variants": {"in": {"all": {"page": "t/latest-blue.html"}, "site:SITEA": {"page": "t/sitea/latest-blue.html"}},
                          "out": {"all": {"page": "t/latest-non-blue.html"}, "site:SITEA": {"page": "t/sitea/latest-non-blue.html"}}}}
        self.assertEqual(F.variant_links(w, "site:SITEA", "t/sitea/latest-blue.html"), {"": "latest.html", "in": "latest-blue.html", "out": "latest-non-blue.html"})
        self.assertEqual(F.web_links(w, "t/sitea/latest-blue.html", "in"), {"site:SITEA": "latest-blue.html", "all": "../latest-blue.html"})

    def test_folded_groups(self):
        """Excluded, inactive or of a type that is not a team: no page, the tickets still at every level."""
        x = site(sites=True)
        r = json.loads(json.dumps(x["r"]))
        for v in r["group_tree"].values():
            if v["name"] == "SITEA - j63 - Windows":
                v["type"] = "Approvers, IDEA"
        o = dict(HIER_OPTS, exclude_groups=["*linux*"], skip_group_types=["approver"], known_groups_only=False)
        m = F.servicenow_insights_model(r, None, x["mask"], o)
        self.assertNotIn("SITEA - j63 - Windows", m["teams"])
        self.assertNotIn("SITE-C - CODEX - Linux", m["teams"])
        why = dict((g, w) for g, w, _ in m["hier_folded"])
        self.assertEqual(why["SITEA - j63 - Windows"], "group type Approvers, IDEA")
        self.assertEqual(why["SITE-C - CODEX - Linux"], "excluded (servicenow_insights_exclude_groups)")
        self.assertEqual(m["scopes"]["all"]["counts"], x["model"]["scopes"]["all"]["counts"])              # every ticket still counts
        self.assertEqual(m["scopes"]["dir:SITEA/J63"]["counts"], x["model"]["scopes"]["dir:SITEA/J63"]["counts"])
        m2 = F.servicenow_insights_model(r, None, x["mask"], dict(o, inactive_teams=True, exclude_groups=[], skip_group_types=[]))
        self.assertIn("SITEB - Network", m2["teams"])
        m3 = F.servicenow_insights_model(r, None, x["mask"], dict(HIER_OPTS, known_groups_only=True,
                                                                  hierarchy_map={"SITEA - J6 - Service Desk": {"site": "SITEA", "directorate": "J6"}}))
        self.assertEqual([g for g, _ in m3["hier_unknown"]][:2], ["SITE-C - CODEX - Linux", "SITEA - j63 - Windows"])
        self.assertTrue(all(c_ > 0 for _, c_ in m3["hier_unknown"]))
        self.assertIn("dir:SITEA/Unknown", m3["scopes"])
        m4 = F.servicenow_insights_model(dict(r, group_tree={}, group_tree_error="GET x: HTTP 403"), None, x["mask"], HIER_OPTS)
        self.assertEqual(m4["hier"]["SITEB - Network"]["dir"], "Other")                                   # refused: the names
        self.assertIn("sys_user_group (the groups' parents, for the sites and directorates): GET x: HTTP 403", "\n".join(F.not_read_lines(m4)))

    def test_rolled_up_once_each(self):
        x = site(sites=True)
        m = x["model"]
        sc = m["scopes"]
        self.assertEqual([n["label"] for n in m["tree"]], ["SITE-C", "SITEA", "SITEB"])
        self.assertEqual([[d["label"] for d in n["dirs"]] for n in m["tree"]], [["CODEX"], ["J6", "J63"], ["Client Support", "NOVA"]])
        self.assertEqual([t["label"] for t in m["tree"][2]["dirs"][1]["teams"]], ["Ops - VDI"])
        self.assertEqual(m["tree"][2]["dirs"][0]["teams"], [])               # its one team inactive: no page, its tickets in Client Support
        self.assertIn(["SITEB - Network", "inactive in ServiceNow", "SITEB / Client Support"], m["hier_folded"])
        self.assertGreater(sc["dir:SITEB/Client Support"]["counts"]["incidents"], 0)
        self.assertEqual(m["hier"]["SITEB - Network"]["by"], "ServiceNow parent")
        self.assertEqual(m["hier"]["SITE-C - CODEX - Linux"]["dir"], "CODEX")          # its parents loop back: stopped, then the name
        self.assertEqual(m["placed_by"], {"ServiceNow parent": 1, "name rule": 4})
        # every ticket once at every level: the sites add up to All, a site's directorates to the site, a directorate's teams to it
        for k in ("incidents", "requests", "resolved"):
            self.assertEqual(sum(sc["site:" + n["label"]]["counts"][k] for n in m["tree"]), sc["all"]["counts"][k], k)
            self.assertEqual(sum(sc[d["key"]]["counts"][k] for d in m["tree"][1]["dirs"]), sc["site:SITEA"]["counts"][k], k)
            self.assertEqual(sum(sc[t["key"]]["counts"][k] for t in m["tree"][1]["dirs"][0]["teams"]), sc["dir:SITEA/J6"]["counts"][k], k)
        self.assertEqual(sum(sc["site:" + n["label"]]["backlog"][0] for n in m["tree"]), sc["all"]["backlog"][0])
        # a ticket passed between groups counts where it ended: the hand-off paths of a site name its groups
        self.assertTrue(all(any(g.startswith("SITEA") for g in e["path"]) for e in sc["site:SITEA"]["handoffs"]["paths"]))
        self.assertEqual(sc["all"]["side"]["kind"], "Sites")
        self.assertEqual(sc["site:SITEA"]["side"]["kind"], "Directorates")
        self.assertEqual(sc["dir:SITEA/J6"]["side"]["kind"], "Teams")
        self.assertEqual([p["path"] for p in m["pages"] if p["key"] == "SITEB - Ops - VDI - nova"], [["siteb", "nova", "ops-vdi"]])
        self.assertEqual(m["hier_other"], [])
        ids = m["scope_ids"]
        self.assertEqual((ids["all"], ids["site:SITE-C"], ids["dir:SITE-C/CODEX"]), ("all", "S1", "D1"))
        ev = F.servicenow_insights_summary_evidence(m)
        self.assertIn("SCOPE S2: site SITEA", ev)
        self.assertIn("SCOPE D2: directorate J6", ev)
        merged = F.servicenow_insights_summary_merge(m, {"items": [{"id": "D2", "lines": ["J6 point."]}]})
        self.assertEqual(merged["scopes"]["dir:SITEA/J6"]["summary"], ["J6 point."])
        xl = F.servicenow_insights_xlsx(m, x["r"], None, x["mask"], HIER_OPTS)
        inc = xl[0]
        self.assertEqual(inc["columns"][7:11], ["Site", "Directorate", "Placed by", "Assignment group"])
        self.assertIn(["SITEB", "NOVA", "name rule", "SITEB - Ops - VDI - nova"], [r_[7:11] for r_ in inc["rows"]])
        self.assertIn("Sites and directorates", [sh["name"] for sh in xl])

    def test_the_pages(self):
        m = site(sites=True)["model"]
        links = page_links(m)
        h = P.servicenow_insights_page(m, "all", {"links": links})
        self.assertIn("Sites side by side", h)
        self.assertIn('<a class="teamlink" href="site-c/latest.html" title="SITE-C">SITE-C</a>', h)
        pick = h.split('id="picklist"')[1].split("</ul>")[0]
        self.assertEqual(re.findall(r'<li class="lv(\d)[^"]*"><a [^>]*title="([^"]+)"', pick)[:7], [
            ("0", "All assignment groups"), ("1", "SITE-C"), ("2", "SITE-C - CODEX"), ("3", "SITE-C - CODEX - Linux"), ("1", "SITEA"),
            ("2", "SITEA - J6"), ("3", "SITEA - J6 - Service Desk")])
        self.assertIn('data-n="siteb - nova nova" data-c="siteb ops - vdi siteb - ops - vdi - nova"', pick)   # a directorate's branch: found by a team
        # a directorate's page, from its folder: the breadcrumbs, its teams side by side, the menu says where
        rel = dict((k, "../../" + v) for k, v in links.items())
        d = P.servicenow_insights_page(m, "dir:SITEA/J63", {"links": rel})
        self.assertIn('<nav class="crumbs" aria-label="Where this page is"><a href="../../latest.html">All</a><span class="sep" aria-hidden="true">›</span>'
                      '<a href="../../sitea/latest.html">SITEA</a><span class="sep" aria-hidden="true">›</span><b>J63</b></nav>', d)
        self.assertIn("Teams side by side", d)
        self.assertIn('<span class="pk-v">SITEA · J63</span>', d)
        self.assertIn('aria-current="page">J63</a>', d)
        s_ = P.servicenow_insights_page(m, "site:SITEB", {"links": links})
        self.assertIn("Directorates side by side", s_)
        self.assertLess(s_.index('title="Client Support">Client Support</a>'), s_.index('title="NOVA">NOVA</a>'))   # A to Z
        t = P.servicenow_insights_page(m, "SITEB - Ops - VDI - nova", {"links": links})
        self.assertIn('<b>Ops - VDI</b></nav>', t)
        self.assertNotIn("side by side</h2>", t)


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


class YourSlos(unittest.TestCase):
    """1.7.0: met or missed from task_sla (Response and Resolution by the SLA's name; no SLA = No SLA), else the targets in
    business hours (07:30-16:30 Mon-Fri, holidays, a shift past midnight) with a response target (opened to first assigned)."""
    BH = {"days": ["mon", "tue", "wed", "thu", "fri"], "start": "07:30", "end": "16:30", "holidays": ["2026-12-25"]}
    U = datetime.timezone.utc

    def dt(self, *a):
        return datetime.datetime(*a, tzinfo=self.U)

    def test_business_hours(self):
        sp = F.business_hours_spec(self.BH)
        self.assertEqual(F.business_hours(self.dt(2026, 11, 2, 7, 0), self.dt(2026, 11, 2, 17, 0), sp, self.U), 9.0)       # Monday, the core hours
        self.assertEqual(F.business_hours(self.dt(2026, 11, 6, 15, 30), self.dt(2026, 11, 9, 8, 30), sp, self.U), 2.0)     # Fri 1 h, Mon 1 h
        self.assertEqual(F.business_hours(self.dt(2026, 12, 24, 16, 0), self.dt(2026, 12, 28, 7, 30), sp, self.U), 0.5)    # Christmas off
        self.assertEqual(F.business_hours(self.dt(2026, 11, 7, 9, 0), self.dt(2026, 11, 8, 9, 0), sp, self.U), 0.0)        # a weekend
        self.assertEqual(F.business_hours(self.dt(2026, 11, 2, 7, 0), self.dt(2026, 11, 2, 9, 0), None, self.U), 2.0)      # {}: calendar
        night = F.business_hours_spec({"days": ["mon"], "start": "22:00", "end": "06:00"})
        self.assertEqual(F.business_hours(self.dt(2026, 11, 2, 20, 0), self.dt(2026, 11, 3, 20, 0), night, self.U), 8.0)  # past midnight
        try:
            from zoneinfo import ZoneInfo
            ny = ZoneInfo("America/New_York")                                                                            # report_time_zone
            self.assertEqual(F.business_hours(self.dt(2026, 11, 2, 12, 30), self.dt(2026, 11, 2, 21, 30), sp, ny), 9.0)
        except Exception:                # noqa: BLE001 - no zone database
            pass
        for bad in ({"start": "7.30"}, {"days": ["funday"]}, {"holidays": ["Dec 25"]}, ["mon"]):
            with self.assertRaises(ValueError):
                F.business_hours_spec(bad)
        self.assertEqual(F.business_days(self.dt(2026, 11, 6, 10, 0), self.dt(2026, 11, 16, 10, 0), sp, self.U), 6)          # Mon-Fri, Mon
        self.assertEqual(F.business_hours_label(self.BH, "UTC"), "Mon-Fri 07:30-16:30 (UTC), 1 holiday")

    def test_targets_old_and_new(self):
        o = {"sla_targets": {"incident": {"1": {"response": 0.5, "resolution": 9}, "2": 16, "5": "none"}}}
        it = lambda p: {"table": "incident", "priority_value": p}                    # noqa: E731
        self.assertEqual((F.sla_target(it("1"), o), F.response_target(it("1"), o)), (9.0, 0.5))
        self.assertEqual((F.sla_target(it("2"), o), F.response_target(it("2"), o)), (16.0, None))        # a number: the resolution
        self.assertEqual(F.sla_target(it("5"), o), None)

    def test_classifier(self):
        self.assertEqual([F.sla_kind(n) for n in ("P1 Incident Response", "P1 Incident Resolution", "Resolve within 3 days", "Customer Update")],
                         ["response", "resolution", "resolution", "other"])
        self.assertEqual(F.sla_kind("First touch", {"sla_response_match": ["first touch"]}), "response")

    def test_task_sla_response_resolution_no_slo(self):
        r = {"sla": {"source": "task_sla", "records": {
            "a": [{"name": "P2 Incident Response", "breached": True}, {"name": "P2 Incident Resolution", "breached": False},
                  {"name": "Customer Update", "breached": True}],
            "b": [{"name": "P3 Incident Resolution", "breached": True}]}}}
        xs = [{"table": "incident", "sys_id": k, "opened_at": "2026-10-01 08:00:00", "done_at": "2026-10-02 08:00:00", "made_sla": True} for k in "abc"]
        self.assertEqual(F.apply_slas(xs, r, {}), "task_sla")
        self.assertEqual([(x["_resp"], x["_resol"], x["made_sla"], x["_no_slo"]) for x in xs],
                         [(False, True, True, False), (None, False, False, False), (None, None, None, True)])
        self.assertIn(["Customer Update", "other"], xs[0]["_slas"])

    def test_targets_in_business_hours_with_a_response(self):
        o = {"sla_targets": {"incident": {"3": {"response": 1, "resolution": 9}, "5": None}}, "business_hours": self.BH}
        r = {"sla": {"source": "targets"}, "first_assigned": {"a": "2026-11-02 08:00:00", "b": "2026-11-02 10:00:00"}}
        xs = [{"table": "incident", "sys_id": "a", "priority_value": "3", "opened_at": "2026-11-02 06:00:00", "done_at": "2026-11-03 07:00:00"},
              {"table": "incident", "sys_id": "b", "priority_value": "3", "opened_at": "2026-11-02 08:00:00", "done_at": "2026-11-03 16:00:00"},
              {"table": "incident", "sys_id": "c", "priority_value": "5", "opened_at": "2026-11-02 08:00:00", "done_at": "2026-11-03 16:00:00"}]
        F.apply_slas(xs, r, o)
        self.assertEqual([(x["_resp"], x["_resol"], x["_no_slo"]) for x in xs], [(True, True, False), (False, False, False), (None, None, True)])

    def test_never_says_slo(self):
        """1.16.0: the user's word is SLA - no page (both looks, every scope), email, workbook or job line says SLO."""
        word = re.compile(r"\bslos?\b", re.I)
        for x in (site(), site(sites=True)):
            m = x["model"]
            for look in ("atlas", "brief"):
                for k in ["all"] + [p_["key"] for p_ in m["pages"]]:
                    h = P.servicenow_insights_page(m, k, dict(OPTS, look=look))
                    self.assertFalse(word.findall(re.sub(r'<script type="application/json"[^>]*>.*?</script>', "", h, flags=re.S)), (look, k))
            for k in ["all"] + [p_["key"] for p_ in m["pages"]]:
                self.assertFalse(word.findall(json.dumps(F.servicenow_insights_doc(m, k, OPTS))), k)
            self.assertFalse(word.findall(json.dumps(F.servicenow_insights_xlsx(m, x["r"], None, x["mask"], OPTS), default=str)))
            self.assertFalse(word.findall(" ".join(F.not_read_lines(m))))

    def test_the_site(self):
        m = site()["model"]
        s = m["scopes"]["all"]
        self.assertEqual(m["slo"]["source"], "task_sla")
        self.assertTrue(80 < s["resp"][0] < 100 and 50 < s["sla"][0] < 100, (s["resp"], s["sla"]))
        self.assertGreater(s["no_slo"][0], 0)                              # P5 and requested items without an SLA
        kinds = dict(((nm, k) for nm, k, c in m["slo"]["definitions"]))
        self.assertEqual((kinds["P4 Incident Response"], kinds["P4 Incident Resolution"], kinds["Customer Update Reminder"]),
                         ("response", "resolution", "other"))
        p4 = next(p for p in s["prio"] if p["value"] == "4")
        self.assertEqual(p4["label"], "4 - Low")
        self.assertIsNotNone(p4["resp"])
        p5 = next(p for p in s["prio"] if p["value"] == "5")
        self.assertEqual((p5["label"], p5["sla"]), ("5 - Exempt", None))
        html = P.servicenow_insights_page(m, "all", OPTS)
        for t in ("Response met", "Resolution met", "No SLA", "Your SLAs", "P4 Incident Response", "ServiceNow&#x27;s SLAs (task_sla)"):
            self.assertIn(t, html)
        doc = F.servicenow_insights_doc(m, "all", OPTS)
        self.assertEqual([x["label"] for x in doc["summary"]][4:6], ["Response met", "Resolution met"])


class MatrixAndCategories(unittest.TestCase):
    """1.7.0: the priority set by hand (impact x urgency, the 3 x 3 matrix), the categories and the official list."""

    def test_matrix(self):
        self.assertEqual([F.matrix_priority({"impact_value": i, "urgency_value": u}) for i, u in (("1", "1"), ("2", "2"), ("3", "3"), ("1", "3"))],
                         ["1", "3", "5", "3"])
        self.assertEqual(F.matrix_priority({"impact_value": "1", "urgency_value": "1"}, {"priority_matrix": []}), "")
        self.assertEqual(F.matrix_priority({"impact_value": "", "urgency_value": "1"}), "")
        mx = site()["model"]["scopes"]["all"]["matrix"]
        self.assertTrue(mx["on"] and mx["checked"] > 100 and 5 < mx["pct"] < 15, mx)
        self.assertEqual(mx["higher"] + mx["lower"], mx["off"])
        self.assertTrue(mx["by_group"] and mx["by_group"][0]["off"] >= mx["by_group"][-1]["off"])
        self.assertIn("Priority set by hand", P.servicenow_insights_page(site()["model"], "all", OPTS))

    def test_categories_and_the_list(self):
        r = site()["r"]
        m = F.servicenow_insights_model(r, None, site()["mask"], dict(OPTS, categories=["Account", "Hardware", "Software", "Other"]))
        c = m["scopes"]["all"]["categories"]
        names = [x["name"] for x in c["rows"]]
        self.assertIn("Account", names)
        self.assertTrue(c["listed"] and c["off_list"] > 0)
        self.assertIn("Network", [n_ for n_, _ in c["off_names"]])
        self.assertIs(next(x for x in c["rows"] if x["name"] == "Network")["official"], False)
        self.assertIsNotNone(next(x for x in c["rows"] if x["name"] == "Account")["resol"])
        html = P.servicenow_insights_page(m, "all", OPTS)
        self.assertIn("not on the official list", html)
        self.assertIn("in a category not on the official list: Network", html)
        doc = F.servicenow_insights_doc(m, "all", OPTS)
        self.assertTrue(any(x["title"] == "Categories" and "not on the official list" in x["text"] for x in doc["sections"]))
        nolist = site()["model"]["scopes"]["all"]["categories"]
        self.assertFalse(nolist["listed"])
        self.assertEqual(nolist["off_list"], 0)


class ActOnFirst(unittest.TestCase):
    """1.7.0: the 3-hop rule, on hold (by reason, Awaiting Caller over 6 business days, no reason; on hold since from the state
    metric or the SLA's pause), the resolution codes (No Resolution Provided listed, the close note masked), resolved not
    closed after 7 days, the surveys - on the page near the top and in the email's Act on."""

    def test_the_site(self):
        m = site()["model"]
        s = m["scopes"]["all"]
        hp, hd, cd, cl, sv = s["hops"], s["hold"], s["codes"], s["closure"], s["survey"]
        self.assertTrue(hp["open"] > 0 and all(x["hops"] > 4 for x in hp["rows"]))
        self.assertEqual([x["age"] for x in hp["rows"]], sorted([x["age"] for x in hp["rows"]], reverse=True))       # oldest first
        self.assertTrue(hd["open"] > 5 and hd["late_n"] > 0 and hd["no_reason"] >= 0)
        self.assertTrue(all("Caller" in x["reason"] and x["days"] > 6 for x in hd["late"]))
        self.assertEqual(set(hd["sources"]), {"metric", "task_sla"})
        self.assertTrue(cd["review_n"] > 0 and cd["counts"]["Duplicate"] > 0 and cd["counts"]["Known Error"] > 0)
        for x in cd["review_rows"]:
            self.assertNotRegex(x["note"], r"\b(PERSON|HOST|PHONE|EMAIL|IP)-\d+\b")
        self.assertTrue(0 < cl["count"] <= 6 and all(x["days"] > 7 for x in cl["rows"]))
        self.assertTrue(sv["n"] > 0 and 3 <= sv["avg"] <= 5 and sv["metrics"] == ["Overall satisfaction"])
        html = P.servicenow_insights_page(m, "all", OPTS)
        i = html.index('id="acton"')
        self.assertLess(html.index('id="glance"'), i)
        self.assertLess(i, html.index('id="keepingup"'))
        for t in ("Open incidents over 3 hops", "Awaiting Caller over 6 business days", "No Resolution Provided", "Resolved, not closed after 7 days",
                  "On hold by reason", "Resolution codes to look at", 'id="satisfaction"'):
            self.assertIn(t, html)
        doc = F.servicenow_insights_doc(m, "all", OPTS)
        act = next(x for x in doc["sections"] if x["title"] == "Act on")
        self.assertTrue(act["lines"][0].startswith("SLA at risk now (past 75%%, or breached and open): %d" % s["at_risk"]["count"]))
        self.assertIn("Open over 3 hops: %d" % hp["open"], act["lines"])
        self.assertIn("Awaiting Caller over 6 business days: %d" % hd["late_n"], act["lines"])
        joined = "\n".join(act["lines"])                                  # the email leaves the site: masked short descriptions
        self.assertNotRegex(joined, r"\(\d{3}\) \d{3}-\d{4}|\b(PERSON|HOST|PHONE)-\d+\b")
        self.assertTrue(any(x["what"] != x["what_m"] for x in hp["rows"] + hd["rows"]))

    def test_snapshot_only_and_limits(self):
        r = dict(site()["r"], states={}, states_error="none", sla=dict(site()["r"]["sla"], paused={}))
        m = F.servicenow_insights_model(r, None, site()["mask"], dict(OPTS, hop_limit=5, awaiting_caller_days=2))
        hd = m["scopes"]["all"]["hold"]
        self.assertTrue(hd["unknown"] == hd["open"] and hd["late_n"] == 0 and not hd["measured"])
        self.assertEqual(m["scopes"]["all"]["hops"]["open"], sum(1 for x in site()["model"]["scopes"]["all"]["hops"]["rows"] if x["hops"] > 6))
        self.assertIn("not known (no state metric or task_sla pause)", P.servicenow_insights_page(m, "all", OPTS))


class LeadingIndicators(unittest.TestCase):
    """1.7.0 (second addendum): SLA at risk now (worst first, in the email's Act on), the outcome mix and unable to resolve,
    reopened within 7 days (the state metric), first-contact resolution (and the service desk's), every headline's change."""

    def test_the_site(self):
        r, mask = site()["r"], site()["mask"]
        m = F.servicenow_insights_model(r, None, mask, dict(OPTS, service_desk_groups=["Ops - Service Desk"]))
        s = m["scopes"]["all"]
        rk = s["at_risk"]
        self.assertTrue(rk["count"] > 0 and rk["breached"] > 0 and rk["read"])
        self.assertTrue(rk["rows"][0]["breached"])                                                 # worst first
        self.assertTrue(all(x["breached"] or x["pct"] >= 75 for x in rk["rows"]))
        self.assertTrue(all(x["left_h"] == 0 for x in rk["rows"] if x["breached"]))
        oc = s["outcomes"]
        self.assertEqual(oc["names"][0], "Fixed")
        self.assertTrue({"Duplicate", "Known Error", "No Resolution Provided"} <= set(oc["now"]))
        self.assertTrue(0 < oc["unable"][0] < 10 and oc["unable"][1] is not None)
        self.assertTrue(50 < s["fcr"]["now"] <= 100 and s["fcr"]["desk"][0] is not None)
        html = P.servicenow_insights_page(m, "all", OPTS)
        for t in ("SLA at risk now", "How incidents ended", "First-contact resolution", "Unable to resolve", "the service desk"):
            self.assertIn(t, html)
        glance = html[html.index('id="glance"'):html.index('id="acton"')]
        self.assertGreaterEqual(glance.count('class="delta '), 6)                                  # the change against the window before
        doc = F.servicenow_insights_doc(m, "all", OPTS)
        self.assertTrue(any(x["title"] == "How incidents ended" for x in doc["sections"]))
        self.assertEqual(F.servicenow_insights_model(r, None, mask, dict(OPTS, outcome_map={"Duplicate": "Duplicate"}))["scopes"]["all"]["outcomes"]["names"],
                         ["Fixed", "Duplicate"])


class LifeCycle(unittest.TestCase):
    """1.9.0: the time in each state (the state metric; the averages add up to the time to resolve), on hold (share, more
    than twice, by reason, Awaiting Vendor), the backlog's age in business days and the weekly net, ping-pong, ticket
    quality, hot spots, after hours, workload (the time per person only with person_metrics)."""

    def test_helpers(self):
        self.assertEqual([F.state_kind(x) for x in ("New", "In Progress", "On Hold", "Awaiting Info", "Resolved", "Closed", "Canceled")],
                         ["new", "progress", "hold", "hold", "resolved", "", ""])
        sp = F.business_hours_spec({"days": ["mon", "tue", "wed", "thu", "fri"], "start": "07:30", "end": "16:30"})
        U = datetime.timezone.utc
        self.assertTrue(F.in_hours(datetime.datetime(2026, 11, 2, 9, 0, tzinfo=U), sp, U))
        self.assertFalse(F.in_hours(datetime.datetime(2026, 11, 2, 17, 0, tzinfo=U), sp, U))
        self.assertFalse(F.in_hours(datetime.datetime(2026, 11, 7, 9, 0, tzinfo=U), sp, U))               # Saturday
        night = F.business_hours_spec({"days": ["mon"], "start": "22:00", "end": "06:00"})
        self.assertTrue(F.in_hours(datetime.datetime(2026, 11, 3, 5, 0, tzinfo=U), night, U))             # Monday's shift, Tuesday 05:00

    def test_the_site(self):
        s = site()["model"]["scopes"]["all"]
        lc = s["lifecycle"]
        self.assertTrue(lc["measured"] and lc["n"] > 100)
        self.assertTrue(0 < lc["avg"]["new"] < lc["avg"]["progress"] and lc["avg"]["resolved"] > 24)     # resolved to closed: 3 days
        self.assertTrue(5 < lc["hold_share"] < 30 and lc["held_many"] > 0 and lc["vendor_share"] > 0)
        self.assertEqual(round(sum(r_[3] for r_ in lc["by_reason"])), 100)
        ag = s["aging"]
        self.assertEqual(sum(ag["total"]), sum(g["open"] for g in ag["by_group"]))
        self.assertEqual(len(ag["net"]), len(s["weeks"]))
        self.assertEqual(ag["net"], [w["opened"] - w["resolved"] for w in s["weeks"]])
        pp = s["pingpong"]
        self.assertTrue(pp["read"] and pp["count"] > 0 and all(a_ != b_ for a_, b_, c_ in pp["pairs"]))
        q = s["quality_t"]
        self.assertTrue(q["other"] > 0 and q["no_ci"] > 0 and q["short"] > 0)
        hs = s["hotspots"]
        self.assertTrue(hs["cis"][0]["count"] >= hs["cis"][-1]["count"] and hs["cis"][0]["repeats"] > 0 and hs["with_problem"] > 0)
        self.assertTrue(0 < s["after"]["pct"] < 100 and s["after"]["core"].startswith("Mon-Fri 07:30-16:30"))
        wl = s["workload"]
        self.assertTrue(wl["rows"] and all(x["median_h"] is None for x in wl["rows"]))                      # private by default
        m2 = F.servicenow_insights_model(site()["r"], None, site()["mask"], dict(OPTS, person_metrics=True))
        self.assertTrue(any(x["median_h"] is not None for x in m2["scopes"]["all"]["workload"]["rows"]))
        html = P.servicenow_insights_page(site()["model"], "all", OPTS)
        for sid in ("states", "aging", "quality", "afterhours", "workload"):
            self.assertIn('id="%s"' % sid, html)
        self.assertIn("The numbers as a table", html)                                                        # the chart's table view
        self.assertIn("--st4:#eda100", html)
        sec = lambda h: h[h.index('id="workload"'):h.index("</section>", h.index('id="workload"'))]     # noqa: E731
        self.assertNotIn("Median to resolve", sec(html))
        self.assertIn("Median to resolve", sec(P.servicenow_insights_page(m2, "all", OPTS)))

    def test_not_measured(self):
        r = dict(site()["r"], states={}, states_error="none")
        html = P.servicenow_insights_page(F.servicenow_insights_model(r, None, site()["mask"], OPTS), "all", OPTS)
        self.assertIn("this instance has no such metric", html)


class ModuleReads(unittest.TestCase):
    """1.7.0: task_sla, the state metric, the metric Assigned to and the surveys - GET only; refused said, the targets instead."""

    class Api(object):
        def __init__(self, deny=()):
            self.calls, self.deny, self.url = [], deny, "https://sn.example.mil"

        def get(self, path, params):
            self.calls.append((path, params))
            table = path.rsplit("/", 1)[1]
            if table in self.deny:
                raise rd.SnError("GET %s: HTTP 403: ACL Exception" % path)
            v = lambda x: {"value": x, "display_value": x}                       # noqa: E731
            rows = {"task_sla": [{"task": v("i1"), "sla": v("P3 Incident Resolution"), "stage": v("paused"), "has_breached": v("false"),
                                  "business_percentage": v("40"), "pause_time": v("2026-10-20 10:00:00")},
                                 {"task": v("i1"), "sla": v("P3 Incident Response"), "stage": v("cancelled"), "has_breached": v("true")}],
                    "metric_instance": [{"id": v("i1"), "value": v("On Hold"), "start": v("2026-10-21 10:00:00"), "end": v("")}],
                    "asmt_metric_result": [{"instance.trigger_id": v("i1"), "metric": v("Overall satisfaction"), "actual_value": v("4"),
                                            "instance.taken_on": v("2026-10-22 10:00:00")},
                                           {"instance.trigger_id": v("i1"), "metric": v("Comments"), "actual_value": v("ok"),
                                            "instance.taken_on": v("2026-10-22 10:00:00")}]}[table]
            return {"result": rows}

    def test_reads(self):
        api = self.Api()
        got, err = rd.read_task_sla(api, ["i1"])
        self.assertEqual((err, [x["name"] for x in got["i1"]], got["i1"][0]["paused_at"]), ("", ["P3 Incident Resolution"], "2026-10-20 10:00:00"))
        self.assertIn("stage!=cancelled", api.calls[0][1]["sysparm_query"])
        st, err = rd.read_states(api, ["i1"], "Incident State Duration")
        self.assertEqual(st, {"i1": [["On Hold", "2026-10-21 10:00:00", ""]]})
        self.assertIn("definition.name=Incident State Duration", api.calls[-1][1]["sysparm_query"])
        win = rd.window(6, 0, "2026-10-31", True, "", NOW)
        self.assertEqual((rd.duration("1970-01-02 03:30:00"), rd.duration("")), (99000, None))
        sv, qs, err = rd.read_survey(api, win)
        self.assertEqual(sv, [["i1", "Overall satisfaction", 4.0, "2026-10-22 10:00:00"]])
        self.assertEqual(qs["Overall satisfaction"]["name"], "Overall satisfaction")
        self.assertTrue(all(p.startswith("api/now/table/") for p, _ in api.calls))

    def test_refused_task_sla_falls_back_to_the_targets(self):
        win = rd.window(6, 0, "2026-10-31", True, "", NOW)
        items = [{"table": "incident", "sys_id": "i9", "done_at": "2026-10-10 10:00:00", "state": "Resolved", "active": True}]
        sla, first, ferr = rd.read_slas(self.Api(deny=("task_sla",)), items, win, "auto", True)
        self.assertEqual(sla["source"], "targets")
        self.assertIn("task_sla not read", sla["note"])
        self.assertIn("the itil role has it", sla["note"])
        sla, _, _ = rd.read_slas(self.Api(), items, win, "made_sla")
        self.assertEqual((sla["source"], sla["records"]), ("made_sla", {}))


class ChangeTasks(unittest.TestCase):
    """1.2.0: change_task read (GET), each team's change tasks - opened, closed by the planned end or after it, open now and
    how old, the time to close - rolled up at every level; counted in the leaderboards (Closed, their own column) and the
    side by side, never in the backlog or the SLAs; the changes' tasks per change and the team a change waited on last."""
    WIN, NWK, LNX = "Ops - Windows", "Ops - Network", "Ops - Linux"

    def r(self, tasks=True, refused=False):
        vd = lambda v: {"value": v, "display_value": v}          # noqa: E731

        def rec(table, **f):
            f.setdefault("sys_id", "s-" + f["number"])
            f.setdefault("active", "false" if f.get("closed_at") else "true")
            return rd.normalize(dict((k, vd(v)) for k, v in f.items()), table)
        chg = [rec("change_request", number="CHG1", assignment_group=self.WIN, opened_at="2026-05-28 09:00:00", work_start="2026-06-01 10:00:00",
                   closed_at="2026-06-03 12:00:00", close_code="successful"),
               rec("change_request", number="CHG2", assignment_group=self.WIN, opened_at="2026-06-05 09:00:00", work_start="2026-06-10 08:00:00",
                   closed_at="2026-06-10 20:00:00", close_code="successful"),
               rec("change_request", number="CHG3", assignment_group=self.LNX, opened_at="2026-07-01 09:00:00", closed_at="2026-07-02 09:00:00",
                   close_code="successful")]
        ct = [rec("change_task", number="CTASK1", change_request="CHG1", assignment_group=self.WIN, assigned_to="Sam Patel", closed_by="Sam Patel",
                  opened_at="2026-05-28 09:00:00", planned_end_date="2026-06-01 14:00:00", closed_at="2026-06-01 12:00:00", close_notes="Done"),
              rec("change_task", number="CTASK2", change_request="CHG1", assignment_group=self.LNX, assigned_to="Avery Stone", closed_by="Avery Stone",
                  opened_at="2026-05-28 09:00:00", planned_end_date="2026-06-01 14:00:00", closed_at="2026-06-01 15:00:00", close_notes="Done"),
              rec("change_task", number="CTASK3", change_request="CHG1", assignment_group=self.NWK, assigned_to="Taylor Grant", closed_by="Taylor Grant",
                  opened_at="2026-05-28 09:00:00", planned_end_date="", closed_at="2026-06-02 15:00:00", close_code="successful",
                  close_notes="Opened the ports on the switch and the firewall; tested from the client subnet."),
              rec("change_task", number="CTASK4", change_request="CHG2", assignment_group=self.NWK, assigned_to="Taylor Grant", closed_by="Taylor Grant",
                  opened_at="2026-06-05 09:00:00", planned_end_date="2026-06-10 20:00:00", closed_at="2026-06-10 18:00:00", close_notes="Done"),
              rec("change_task", number="CTASK5", change_request="CHG4", assignment_group=self.NWK, assigned_to="Robin Shah",
                  opened_at="2026-09-01 09:00:00", planned_end_date="2026-09-02 09:00:00", state="2")]
        inc = [rec("incident", number="INC1", assignment_group=self.NWK, assigned_to="Taylor Grant", resolved_by="Taylor Grant",
                   opened_at="2026-06-20 10:00:00", resolved_at="2026-06-20 12:00:00", closed_at="2026-06-23 12:00:00", made_sla="true",
                   close_notes="Replaced the patch cable and re-enabled the port.")]
        win = rd.window(6, 0, "2026-10-31", True, "", NOW)
        tables = [{"table": t, "read": 1, "kept": 1, "more": False, "error": ""} for t in ("incident", "change_request")]
        tables.append({"table": "change_task", "read": 0, "kept": 0, "more": False,
                       "error": "GET api/now/table/change_task: HTTP 403: Operation Failed: ACL Exception" if refused else ""})
        return {"ok": True, "window": win, "items": inc + chg + (ct if tasks and not refused else []), "audit": {}, "groups": [],
                "members": {self.WIN: ["Sam Patel"], self.NWK: ["Taylor Grant", "Robin Shah"], self.LNX: ["Avery Stone"]},
                "url": "https://sn.example.mil", "group_tree": {}, "tables": tables}

    def model(self, r, **o):
        return F.servicenow_insights_model(r, None, {"pairs": [], "counts": {}}, dict(OPTS, groups=[self.WIN, self.NWK, self.LNX], **o))

    def test_read_get_only(self):
        self.assertIn("change_task", rd.TABLE_FIELDS)
        self.assertEqual(rd.TABLE_FIELDS["change_task"], ["change_request", "planned_start_date", "planned_end_date", "close_code"])
        it = rd.normalize({"sys_id": {"value": "c1", "display_value": "c1"}, "number": {"value": "CTASK9", "display_value": "CTASK9"},
                           "change_request": {"value": "9f1e", "display_value": "CHG0031001"}, "assignment_group": {"value": "g7", "display_value": self.NWK},
                           "planned_end_date": {"value": "2026-06-01 14:00:00", "display_value": "06/01/2026 10:00:00"},
                           "opened_at": {"value": "2026-05-28 09:00:00", "display_value": ""}, "closed_at": {"value": "2026-06-01 15:00:00", "display_value": ""}},
                          "change_task")
        self.assertEqual((it["parent"], it["parent_id"], it["planned_end"], it["done_at"], it["group"]),
                         ("CHG0031001", "9f1e", "2026-06-01 14:00:00", "2026-06-01 15:00:00", self.NWK))
        self.assertTrue(rd.table_query("change_task", ["g7"], [], NOW, NOW).startswith("assignment_groupINg7^opened_at>="))
        import yaml
        with open(os.path.join(HERE, "..", "roles", "servicenow_insights", "defaults", "main.yml")) as fh:
            self.assertIn("change_task", yaml.safe_load(fh)["servicenow_insights_tables"])
        with open(os.path.join(HERE, "..", "roles", "servicenow_insights", "library", "site_servicenow_insights.py")) as fh:
            src = fh.read()
        self.assertIn('"change_request", "change_task", "problem"]', src)
        self.assertNotIn('method="POST"', src)
        self.assertNotIn('method="PATCH"', src)

    def test_the_figures(self):
        m = self.model(self.r())
        a = m["scopes"]["all"]
        self.assertEqual(a["ctasks"], {"opened": 5, "closed": 4, "closed_prev": 0, "open": 1, "age": [0, 0, 0, 1], "on_time": 2, "overdue": 1,
                                       "no_end": 1, "late_open": 1, "median_h": 114.0})
        ch = a["changes"]
        self.assertEqual((ch["count"], ch["tasks"], ch["with_tasks"], ch["per_change"], ch["most"], ch["held_n"]), (3, 4, 2, 2.0, ["CHG1", 3], 2))
        # CHG1: its other tasks closed at 15:00 on 1 June, Network's at 15:00 on 2 June (24 h); CHG2's one task: from the
        # change's work start, 08:00, to 18:00 (10 h)
        self.assertEqual(ch["held_by"], [{"group": self.NWK, "count": 2, "median_h": 17.0}])
        net = m["scopes"][self.NWK]
        self.assertEqual((net["ctasks"]["closed"], net["ctasks"]["open"], net["ctasks"]["no_end"]), (2, 1, 1))
        self.assertEqual(m["scopes"][self.WIN]["ctasks"]["closed"] + m["scopes"][self.LNX]["ctasks"]["closed"] + net["ctasks"]["closed"], 4)
        # their own figures: the backlog, the weeks and the SLAs are the incidents' and requests' as before
        no = self.model(self.r(tasks=False))
        for k in ("backlog", "weeks", "sla", "age", "median_h", "counts"):
            self.assertEqual(a[k], no["scopes"]["all"][k], k)
        row = dict((t["team"], t) for t in a["teams"])[self.NWK]
        self.assertEqual((row["ct_closed"], row["ct_open"]), (2, 1))

    def test_the_leaderboards(self):
        st = self.model(self.r())["scopes"][self.NWK]["stars"]
        tg = next(p for p in st["people"] if p["name"] == "Taylor Grant")
        self.assertEqual((tg["resolved"], tg["ctask"], tg["median_h"]), (3, 2, 2.0))      # the median: the incident only
        self.assertEqual((st["total"]["resolved"], st["total"]["ctask"], st["ctasks"]), (3, 2, True))
        self.assertEqual((st["awards"][0]["what"], st["awards"][0]["stat"]), ("Most closed", "3 tickets closed"))
        h = P.servicenow_insights_page(self.model(self.r()), self.NWK, {})
        self.assertIn("<th", h)
        self.assertRegex(h, r'>Closed<.*?>Change tasks<.*?>Median to resolve<')
        self.assertIn("Change tasks = how many of them were change tasks", h)

    def test_rolled_up_at_every_level(self):
        """Each change task once at every level, however many levels the tree has: a scope's children add up to it."""
        x = site(sites=True)
        m = x["model"]
        sc = m["scopes"]
        self.assertGreater(sc["all"]["ctasks"]["closed"], 0)

        def children(k):
            if k == "all":
                return [n["key"] for n in m["tree"]]
            for n in m["tree"]:
                if n["key"] == k:
                    return [d["key"] for d in n["dirs"]]
            return []
        checked = 0
        for k in [k for k in sc if children(k)]:
            for f in ("opened", "closed", "open", "on_time", "overdue", "no_end", "late_open"):
                self.assertEqual(sum(sc[c]["ctasks"][f] for c in children(k)), sc[k]["ctasks"][f], (k, f))
            checked += 1
        self.assertGreater(checked, 2)

    def test_split(self):
        x = site(sites=True)
        m = F.servicenow_insights_model(x["r"], None, x["mask"], dict(PARENTS_OPTS, split_label="BLUE", split_pattern="*- nova"))
        for k in ("all", "site:SITEB", "site:SITEA"):
            for f in ("closed", "open", "overdue"):
                self.assertEqual(m["variants"]["in"][k]["ctasks"][f] + m["variants"]["out"][k]["ctasks"][f], m["scopes"][k]["ctasks"][f], (k, f))
        self.assertEqual(m["variants"]["in"]["site:SITEA"]["ctasks"]["closed"], 0)
        self.assertGreater(m["variants"]["in"]["all"]["ctasks"]["closed"], 0)
        xl = F.servicenow_insights_xlsx(m, x["r"], None, x["mask"], PARENTS_OPTS)
        sh = next(s_ for s_ in xl if s_["name"] == "Change tasks")
        col = sh["columns"].index("BLUE")
        self.assertEqual(sorted(set(r_[col] for r_ in sh["rows"])), ["no", "yes"])

    def test_email_page_workbook(self):
        r = self.r()
        m = self.model(r)
        doc = F.servicenow_insights_doc(m, "all", OPTS)
        sec = dict((s_["title"], s_) for s_ in doc["sections"])
        tm = sec["Teams side by side"]
        self.assertEqual(tm["columns"][7:9], ["Change tasks closed / open", "Top issue"])         # 1.18.0: one column, the email fits
        self.assertTrue(all(re.match(r"^[\d,]+ / [\d,]+$", row[7]) for row in tm["rows"]), tm["rows"])
        self.assertTrue(all(re.match(r"^[\d,]+ \([\d,]+\)$", row[2]) for row in tm["rows"]), tm["rows"])
        ct = sec["Change tasks"]
        self.assertEqual(ct["lines"][:2], ["Closed: 4 (the window before: 0), median 4.8 days to close",
                                           "  - by the planned end 2, after it 1, with no planned end 1"])
        self.assertEqual(ct["rows"], [[self.NWK, 2, "17.0 h"]])
        h = P.servicenow_insights_page(m, "all", {"links": page_links(m)})
        for t in ("Change tasks closed", "Change tasks open", "Held up by", "Tasks per change", "Open change tasks by age", 'class="subh"'):
            self.assertIn(t, h)
        wb = F.servicenow_insights_xlsx(m, r, None, {"pairs": [], "counts": {}}, OPTS)
        sh = next(s_ for s_ in wb if s_["name"] == "Change tasks")
        self.assertEqual([x[0] for x in sh["rows"]], ["CTASK1", "CTASK2", "CTASK3", "CTASK4", "CTASK5"])
        by = dict((x[0], x[sh["columns"].index("By the planned end")]) for x in sh["rows"])
        self.assertEqual(by, {"CTASK1": "yes", "CTASK2": "no", "CTASK3": "no planned end", "CTASK4": "yes", "CTASK5": "open, past the planned end"})
        self.assertEqual(sh["rows"][2][1], "CHG1")
        self.assertIn("Of them change tasks", next(s_ for s_ in wb if s_["name"] == "People")["columns"])
        self.assertIsNotNone(F.servicenow_insights_artifact(m)["scopes"]["all"]["change_tasks"])

    def test_tables_fit(self):
        """1.2.0: no table scrolls sideways - the headers and long text wrap (no ellipsis, nothing cut), the numbers tight;
        on a phone each row a card, each cell under its column's name."""
        m = self.model(self.r())
        h = P.servicenow_insights_page(m, "all", {"links": page_links(m)})
        th_rule = re.search(r"\nth\{[^}]*\}", P.CSS).group(0)
        self.assertNotIn("nowrap", th_rule)
        self.assertNotIn("text-overflow", re.search(r'<section class="card" id="teams">.*?</section>', h, re.S).group(0))
        self.assertIn("td.wrap{white-space:normal;overflow-wrap:anywhere", P.CSS)
        self.assertIn('.tw thead{display:none}', P.CSS)
        self.assertIn('<td data-l="Top issue" class="wrap">', h)
        self.assertIn('<td data-l="Change tasks closed / open" class="n nw" data-v="2">2 / 1</td>', h)     # one column (1.30.0)
        self.assertNotIn('<td data-l="" ', h)

    def test_refused(self):
        """change_task refused (403): said in Not read, the rest of the report whole, no change task anywhere."""
        m = self.model(self.r(refused=True))
        self.assertIsNone(m["scopes"]["all"]["ctasks"])
        self.assertNotIn("tasks", m["scopes"]["all"]["changes"])
        self.assertIn("change_task: GET api/now/table/change_task: HTTP 403: Operation Failed: ACL Exception - the change tasks are left out",
                      "\n".join(F.not_read_lines(m)))
        doc = F.servicenow_insights_doc(m, "all", OPTS)
        titles = [s_["title"] for s_ in doc["sections"]]
        self.assertNotIn("Change tasks", titles)
        self.assertNotIn("Change tasks closed / open", next(s_ for s_ in doc["sections"] if s_["title"] == "Teams side by side")["columns"])
        h = P.servicenow_insights_page(m, "all", {"links": page_links(m)})
        self.assertNotIn("Change tasks", h)
        self.assertNotIn("Change tasks", [s_["name"] for s_ in F.servicenow_insights_xlsx(m, self.r(refused=True), None, {"pairs": [], "counts": {}}, OPTS)])
        self.assertEqual((m["scopes"][self.NWK]["stars"]["awards"][0]["what"], m["scopes"][self.NWK]["stars"]["awards"][0]["stat"]),
                         ("Most resolved", "1 tickets resolved"))
        self.assertEqual(self.model(self.r(refused=True))["scopes"]["all"]["counts"], self.model(self.r(tasks=False))["scopes"]["all"]["counts"])


class Surveys(unittest.TestCase):
    """1.16.0: never one average across survey questions of different scales. Each question on its own scale (ServiceNow's
    min and max, else as observed), grouped by its survey; a retired survey its own block; questions that are not ratings
    (a 0/1, a 0 to 100) read but not shown as one; the headline - the active survey's overall on one scale - on the tile,
    in the email and the history; each average graded Good / Fair / Low by its share of the scale."""

    @classmethod
    def setUpClass(cls):
        cls.x = site(surveys=True)
        cls.m = cls.x["model"]
        cls.sv = cls.m["scopes"]["all"]["survey"]

    def test_the_questions(self):
        plan = F.survey_questions(self.x["r"]["survey"], self.x["r"]["survey_questions"], OPTS, *rd_window(self.x))
        q = dict(((v["survey"], v["name"]), v) for v in plan["questions"].values())
        self.assertTrue(all(q[("Service desk survey", "Q%d" % i)]["rating"] and q[("Service desk survey", "Q%d" % i)]["scale"] == [1.0, 4.0, "read"]
                            for i in range(1, 5)))
        self.assertTrue(all(q[("Legacy survey", "Q%d" % i)]["rating"] and q[("Legacy survey", "Q%d" % i)]["state"] == "retired" for i in range(1, 5)))
        self.assertFalse(q[("Legacy survey", "Q5")]["rating"])                       # 0 to 100
        self.assertFalse(q[("Service desk survey", "Contacted again")]["rating"])     # yes / no
        self.assertTrue(q[("Service desk survey", "Overall satisfaction")]["rating"])
        named = F.survey_questions(self.x["r"]["survey"], self.x["r"]["survey_questions"], dict(OPTS, survey_metrics=["q5"]), *rd_window(self.x))
        self.assertTrue(next(v for v in named["questions"].values() if v["name"] == "Q5")["rating"])
        # the answers alone, the datatype not read: whole numbers 0 to 10 with 3 or more values
        bare = [["i%d" % i, "k", v, "2026-10-01 10:00:00"] for i, v in enumerate([1, 2, 3, 4, 4])]
        self.assertTrue(F.survey_questions(bare, {}, OPTS, *rd_window(self.x))["questions"]["k"]["rating"])
        for vals in ([0, 1, 1, 0], [12, 30, 45], [1.5, 2.5, 3.5]):
            rows = [["i%d" % i, "k", v, "2026-10-01 10:00:00"] for i, v in enumerate(vals)]
            self.assertFalse(F.survey_questions(rows, {}, OPTS, *rd_window(self.x))["questions"]["k"]["rating"], vals)

    def test_the_headline_and_the_blocks(self):
        sv = self.sv
        # the active survey's questions are on two scales: its most-answered rating question is the headline, never their mean
        self.assertEqual((sv["survey"], sv["state"]), ("Service desk survey", "active"))
        self.assertTrue(sv["label"].startswith("Service desk survey · "), sv["label"])
        self.assertIn(sv["scale"][:2], ([1.0, 4.0], [1.0, 5.0]))
        b = sv["blocks"]
        self.assertEqual([(x["survey"], x["state"]) for x in b], [("Service desk survey", "active"), ("Legacy survey", "retired")])
        self.assertFalse(b[0]["one_scale"])                  # Q1 to Q4 on 1 to 4 and Overall satisfaction on 1 to 5: each on its own
        self.assertTrue(b[1]["one_scale"] and b[1]["overall"])
        self.assertEqual(b[1]["scale"][:2], [1.0, 5.0])
        self.assertEqual(sv["questions_n"], 9)

    def test_the_mixed_mean_nowhere(self):
        w = rd_window(self.x)
        vals = [v for k_, q, v, t_ in self.x["r"]["survey"] if F._in(F._t(t_), w[0], w[1])]
        mixed = "%.2f" % (sum(vals) / len(vals))
        rated = set(round(v, 2) for v in [self.sv["avg"]] + [b["main"]["avg"] for b in self.sv["blocks"]] if v is not None)
        self.assertNotIn(round(sum(vals) / len(vals), 2), rated)
        h = P.servicenow_insights_page(self.m, "all", OPTS)
        self.assertNotIn(">%s" % mixed, h)
        self.assertNotIn(mixed, json.dumps(F.servicenow_insights_doc(self.m, "all", OPTS)))
        import servicenow_insights_history as H
        rows = H.servicenow_insights_db_rows(self.m, {})
        fig = dict((f[2], f[4]) for f in rows["figures"] if f[0] == "all" and f[1] == "")
        self.assertEqual((fig["survey_avg"], fig["survey_scale_max"]), (self.sv["avg"], self.sv["scale"][1]))
        self.assertEqual(fig["survey_questions"], 9)

    def test_the_page(self):
        h = P.servicenow_insights_page(self.m, "all", OPTS)
        sec = re.search(r'<section class="card" id="satisfaction">.*?</section>', h, re.S).group(0)
        self.assertIn('<h3 class="subh">Legacy survey <span class="chip">retired</span></h3>', sec)
        self.assertLess(sec.index("Service desk survey</h3>"), sec.index("Legacy survey <span"))
        self.assertRegex(sec, r'<span class="v">\d\.\d\d of \d <span class="chip (good|warn|crit)">(Good|Fair|Low)</span>')
        self.assertIn("<th class=\"n\" data-sort=\"avg\">Average</th><th data-sort=\"grade\">Grade</th>", sec)
        self.assertIn("Retired in ServiceNow: its answers are history, never blended with the active survey.", sec)
        for tab in re.findall(r"<h3>By month · .*?</table>", sec, re.S):         # every average on its scale, even in a mixed survey
            self.assertFalse(re.findall(r'data-l="Average" class="n" data-v="[\d.]+">\d\.\d\d<', tab), tab[:200])
        self.assertNotIn(">Q5<", sec)
        self.assertNotIn("Contacted again", sec)
        how = re.search(r"<b>The surveys read</b>.*?</details>", h, re.S).group(0)
        self.assertIn("Service desk survey - Q1 - ", how)
        self.assertRegex(how, r"Legacy survey \(retired\) - Q5 - \d+ answers - read, not shown as a rating \(datatype numericscale, up to 100; "
                              r"values \d+ to 100\): name it in servicenow_insights_survey_metrics to show it")
        self.assertIn("1 to 4", how)

    def test_the_email(self):
        doc = F.servicenow_insights_doc(self.m, "all", OPTS)
        t = next(x for x in doc["summary"] if x["label"] == "Satisfaction")
        self.assertRegex(t["value"], r"^\d\.\d\d of \d$")
        self.assertIn(t["status"], ("ok", "warning", "critical"))
        self.assertTrue(t["note"].startswith(self.sv["grade"] + " · "), t["note"])

    def test_the_grades(self):
        g = F.survey_grade
        sc = [1.0, 4.0, "read"]
        self.assertEqual([g(3.4, sc, 5), g(3.39, sc, 5), g(2.8, sc, 5), g(2.79, sc, 5)], ["Good", "Fair", "Fair", "Low"])
        self.assertEqual([g(8.0, [0.0, 10.0, "read"], 9), g(5.99, [0.0, 10.0, "observed"], 9)], ["Good", "Low"])   # a 0-based scale
        self.assertEqual(g(3.9, sc, 4), "")                              # under survey_min_answers (5)
        self.assertEqual(g(3.9, sc, 2, {"survey_min_answers": 2}), "Good")
        self.assertEqual((g(3.9, None, 50), g(3.9, [4.0, 4.0, "observed"], 50)), ("", ""))     # no scale known: no grade
        self.assertEqual(g(3.0, sc, 9, {"survey_grades": {"good": 0.9, "fair": 0.5}}), "Fair")
        few = P.servicenow_insights_page(self.m, "all", dict(OPTS))
        self.assertIn('<span class="mut">—</span>', few)                   # a month or group under 5 answers: no grade

    def test_the_headline_setting(self):
        x = self.x
        def head(name):
            return F.survey_questions(x["r"]["survey"], x["r"]["survey_questions"], dict(OPTS, survey_headline=name), *rd_window(x))
        p = head("Legacy survey")
        self.assertEqual((p["headline"]["label"], p["headline"]["scale"][:2]), ("Legacy survey", [1.0, 5.0]))
        p = head("q2")
        self.assertEqual((p["headline"]["label"], p["headline"]["survey"]), ("Service desk survey · Q2", "Service desk survey"))
        p = head("Nothing  Like It")
        self.assertEqual(p["headline_missing"], "Nothing Like It")           # said back as typed (1.17.0, the tester), spaces made one
        self.assertIn("servicenow_insights_survey_headline: Nothing Like It is not a survey or rating question read - the default instead",
                      F.survey_read_lines(p, {"survey_headline": "Nothing  Like It"}))

    @unittest.skipUnless(any(__import__("shutil").which(b) for b in ("google-chrome", "chromium")), "no headless browser here")
    def test_tables_fit(self):
        import shutil
        b = shutil.which("google-chrome") or shutil.which("chromium")
        h = P.servicenow_insights_page(self.m, "all", OPTS)
        probe = ('<script>addEventListener("load",function(){var s=document.getElementById("satisfaction"),w=[].slice.call(s.querySelectorAll(".tw"))'
                 '.filter(function(t){return t.scrollWidth>t.clientWidth+1}).length;document.title="R "+w+" "+(document.documentElement.scrollWidth>innerWidth+1)})</script></body>')
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "all.html")
            with open(path, "w") as fh:
                fh.write(h.replace("</body>", probe))
            for wd in (1366, 390):
                out = subprocess.run([b, "--headless=new", "--disable-gpu", "--no-sandbox", "--virtual-time-budget=3000", "--window-size=%d,900" % wd,
                                      "--dump-dom", "file://" + path], capture_output=True, text=True, timeout=120).stdout
                self.assertIn("<title>R 0 false</title>", out, wd)


def rd_window(x):
    w = x["r"]["window"]
    return rd.sn_time(w["since"]), rd.sn_time(w["until"])


@unittest.skipUnless(any(__import__("shutil").which(b) for b in ("google-chrome", "chromium")), "no headless browser here")
class TrustedTypes(unittest.TestCase):
    """1.16.0: the page's script under Content-Security-Policy require-trusted-types-for 'script' - no HTML sink: a tooltip
    shown (its <br> and <b> as nodes, its text as text) and a column sorted, with no violation and no error; a from / to
    that can not be shown says why beside the fields."""

    def test_no_sink(self):
        import shutil
        self.assertNotIn("innerHTML", P.SCRIPT)
        self.assertNotIn("insertAdjacentHTML", P.SCRIPT)
        b = shutil.which("google-chrome") or shutil.which("chromium")
        import test_insights_org as O                       # the org map's All page: the Custom view and its from / to
        m = O.model()
        h = P.servicenow_insights_page(m, "all", {"links": page_links(m)})
        self.assertIn('id="cvfrom"', h)
        csp = '<meta http-equiv="Content-Security-Policy" content="require-trusted-types-for \'script\'">'
        probe = ('<script>var V=[];document.addEventListener("securitypolicyviolation",function(e){V.push(e.violatedDirective)});'
                 'addEventListener("error",function(e){V.push(String(e.message))});'
                 'addEventListener("load",function(){setTimeout(function(){var D=document,t=D.querySelector("[data-t]"),out={};'
                 'try{t.dispatchEvent(new MouseEvent("mousemove",{clientX:20,clientY:20,bubbles:true}));var tp=D.getElementById("tip");'
                 'out.tip=!tp.hidden;out.br=tp.querySelectorAll("br").length;out.b=tp.querySelectorAll("b").length;out.text=tp.textContent;'
                 'var th=D.querySelector("th[data-sort]");th.click();th.click();out.arrows=th.querySelectorAll(".ar").length;out.ar=th.textContent;'
                 'var f=D.getElementById("cvfrom"),g=D.getElementById("cvto");if(f){f.value="2026-10-10";g.value="2026-10-01";D.getElementById("cvft").click();'
                 'out.back=D.getElementById("cvftmsg").textContent;f.value="2020-01-01";g.value="2020-01-05";D.getElementById("cvft").click();'
                 'out.out=D.getElementById("cvftmsg").textContent}}catch(e){V.push("thrown: "+e.message)}'
                 'out.v=V;D.title="R "+JSON.stringify(out)},300)})</script>')
        page = h.replace("<head>", "<head>" + csp, 1).replace("</body>", probe + "</body>")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "all.html")
            with open(path, "w") as fh:
                fh.write(page)
            out = subprocess.run([b, "--headless=new", "--disable-gpu", "--no-sandbox", "--virtual-time-budget=4000", "--dump-dom", "file://" + path],
                                 capture_output=True, text=True, timeout=120).stdout
        got = json.loads(re.search(r"<title>R (.*?)</title>", out).group(1).replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))
        self.assertEqual(got["v"], [], got)
        self.assertTrue(got["tip"])
        self.assertGreaterEqual(got["br"] + got["b"], 1, got)
        self.assertNotIn("<", got["text"])
        self.assertEqual(got["arrows"], 1)
        self.assertTrue(got["ar"][-1] in "\u25b2\u25bc", got["ar"])       # one arrow, made as a node
        self.assertEqual(got.get("back"), "From must be before To", got)
        # the window as the fields show it (1.17.0, the tester): its last day, the To field's max - not the day after its end
        lo, hi = re.search(r'id="cvto" min="([\d-]+)" max="([\d-]+)"', h).groups()
        self.assertEqual(got.get("out"), "Outside this report's window: %s to %s" % (lo, hi), got)



class Senders(unittest.TestCase):
    """1.18.0: under Who sends this team its tickets, of how many - "N of M tickets, from 8 of P people"."""

    def test_of_how_many(self):
        m, seen, more = site()["model"], 0, False
        for t in m["teams"]:
            st = m["scopes"][t].get("stars") or {}
            op, of = st.get("openers") or [], st.get("openers_of") or {}
            if not op:
                continue
            self.assertGreaterEqual(of["tickets"], sum(p["opened"] for p in op))
            self.assertGreaterEqual(of["people"], len(op))
            self.assertLessEqual(len(op), 8)
            h = P.servicenow_insights_page(m, t, OPTS)
            line = '<div class="cap of">%s of %s tickets, from %d of %s people.</div>' % (
                "{:,}".format(sum(p["opened"] for p in op)), "{:,}".format(of["tickets"]), len(op), "{:,}".format(of["people"]))
            self.assertIn(line, h)
            self.assertLess(h.index("Who sends this team its tickets"), h.index(line))
            seen += 1
            if len(op) == 8:
                more = True
        self.assertTrue(seen and more)                  # some team with more senders than shown


@unittest.skipUnless(__import__("shutil").which("node"), "no node here")
class InsightsSort(unittest.TestCase):
    """1.18.0: the page's sort key is the website's (site_report.js 1.20.0): a lone number as that number, IPv4 by its
    octets, versions and host2 before host10, numbers before text, an empty cell or a lone dash last both ways."""

    def key_js(self):
        a = P.SCRIPT.index("/*sortkey")
        return P.SCRIPT[a:P.SCRIPT.index("/*sortkey end*/")]

    def order(self, vals, asc=True):
        js = self.key_js() + ("var v=%s;v.sort(function(a,b){return scmp(skey(a),skey(b),%s)});console.log(JSON.stringify(v))" % (json.dumps(vals), "true" if asc else "false"))
        return json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)

    def test_the_key(self):
        self.assertEqual(self.order(["192.0.2.10", "192.0.2.9", "198.51.100.1", "192.0.2.100"]), ["192.0.2.9", "192.0.2.10", "192.0.2.100", "198.51.100.1"])
        self.assertEqual(self.order(["8.0.10", "8.0.2"]), ["8.0.2", "8.0.10"])
        self.assertEqual(self.order(["host10", "host2", "Host1"]), ["Host1", "host2", "host10"])
        self.assertEqual(self.order(["1,200", "950", "-3", "12.5%", "2.5%"]), ["-3", "2.5%", "12.5%", "950", "1,200"])
        self.assertEqual(self.order(["b", "", "—", "3", "a", "-"]), ["3", "a", "b", "", "—", "-"])
        self.assertEqual(self.order(["b", "", "-", "3", "a"], asc=False), ["b", "a", "3", "", "-"])      # empty last either way

    @unittest.skipUnless(any(__import__("shutil").which(b) for b in ("google-chrome", "chromium")), "no headless browser here")
    def test_the_first_click(self):
        """A first click sorts ascending; on a column already in order, descending - so the click always shows."""
        import shutil
        b = shutil.which("google-chrome") or shutil.which("chromium")
        rows = [("192.0.2.10", "3"), ("192.0.2.9", "1"), ("—", "2"), ("198.51.100.1", "4")]
        body = "".join("<tr><td>%s</td><td>%s</td></tr>" % rw for rw in rows)
        page = ('<!DOCTYPE html><html><head><meta http-equiv="Content-Security-Policy" content="require-trusted-types-for \'script\'"></head><body>'
                '<table><thead><tr><th data-sort="ip">IP</th><th data-sort="n">N</th></tr></thead><tbody>%s</tbody></table><div id="tip" hidden></div>'
                '<script>%s</script><script>addEventListener("load",function(){setTimeout(function(){var D=document,out={},'
                'col=function(i){return [].map.call(D.querySelectorAll("tbody tr"),function(r){return r.cells[i].textContent})};'
                'var h=D.querySelectorAll("th");h[0].click();out.a=col(0);h[0].click();out.d=col(0);h[1].click();out.n1=col(1);'
                'D.title="R "+JSON.stringify(out)},200)})</script></body></html>') % (body, P.SCRIPT)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.html")
            with open(path, "w") as fh:
                fh.write(page)
            out = subprocess.run([b, "--headless=new", "--disable-gpu", "--no-sandbox", "--virtual-time-budget=3000", "--dump-dom", "file://" + path],
                                 capture_output=True, text=True, timeout=120).stdout
        got = json.loads(re.search(r"<title>R (.*?)</title>", out).group(1).replace("&quot;", '"'))
        self.assertEqual(got["a"], ["192.0.2.9", "192.0.2.10", "198.51.100.1", "—"])
        self.assertEqual(got["d"], ["198.51.100.1", "192.0.2.10", "192.0.2.9", "—"])          # the dash last both ways
        # N is 4 3 1 2 after IP descending: not in order, so the first click sorts it ascending
        self.assertEqual(got["n1"], ["1", "2", "3", "4"])


@unittest.skipUnless(any(__import__("shutil").which(b) for b in ("google-chrome", "chromium")), "no headless browser here")
class EmailFits(unittest.TestCase):
    """1.18.0: the insights email fits a window 800 px wide in the standard, command and portal looks - the standard look's
    tiles four to a row (as portal and glass), the teams' table in 10 columns at most. (glass: its cells' padding, every report.)"""

    def test_800(self):
        import shutil
        import jinja2
        b = shutil.which("google-chrome") or shutil.which("chromium")
        x = site()
        doc = F.servicenow_insights_doc(x["model"], "all", OPTS)
        self.assertGreater(len(doc["summary"]), 5)
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(HERE, "..", "roles", "site_email", "templates")), autoescape=False)
        probe = '<script>addEventListener("load",function(){document.title="R "+document.documentElement.scrollWidth+" "+innerWidth})</script></body>'
        with tempfile.TemporaryDirectory() as d:
            for tpl in ("report.html.j2", "report_command.html.j2", "report_portal.html.j2"):
                h = env.get_template(tpl).render(_report=doc, _email_footer="AAP job 9", _email_logo_cid="", _email_runs=[],
                                                 report_email_header_text="Site automation report")
                path = os.path.join(d, tpl + ".html")
                with open(path, "w") as fh:
                    fh.write(h.replace("</body>", probe))
                out = subprocess.run([b, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars", "--virtual-time-budget=3000",
                                      "--window-size=800,900", "--dump-dom", "file://" + path], capture_output=True, text=True, timeout=120).stdout
                self.assertIn("<title>R 800 800</title>", out, tpl)

    def test_tiles_and_lines(self):
        """1.30.0: the standard look's boxes four equal columns (the 4th took the rest of the width); portal's "3.32 of 4" on one
        line; an item under another ("  - INC...") indented without its dash, in every email look."""
        import shutil
        import jinja2
        b = shutil.which("google-chrome") or shutil.which("chromium")
        doc = F.servicenow_insights_doc(site(surveys=True)["model"], "all", OPTS)
        self.assertTrue(any(re.match(r"^\d\.\d\d of \d$", str(t["value"])) for t in doc["summary"]), doc["summary"])
        doc["sections"].append({"title": "Act on", "lines": ["SLA at risk: 1", "  - INC0041200 P2, 80% used"]})
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(HERE, "..", "roles", "site_email", "templates")), autoescape=False)
        probe = ('<script>addEventListener("load",function(){var t=[].slice.call(document.querySelectorAll("td[width=\'25%\'] > table"))'
                 '.map(function(x){return Math.round(x.getBoundingClientRect().width)});var v=[].slice.call(document.querySelectorAll("div"))'
                 '.filter(function(d){return /^\\d\\.\\d\\d of \\d$/.test(d.textContent)})[0];var lh=parseFloat(getComputedStyle(v).fontSize)*1.6;'
                 'document.title="R "+(new Set(t).size)+" "+t.length+" "+(v.getBoundingClientRect().height<lh)})</script></body>')
        with tempfile.TemporaryDirectory() as d:
            for tpl in ("report.html.j2", "report_command.html.j2", "report_portal.html.j2", "report_glass.html.j2"):
                h = env.get_template(tpl).render(_report=doc, _email_footer="AAP job 9", _email_logo_cid="", _email_runs=[],
                                                 report_email_header_text="Site automation report")
                self.assertIn(">INC0041200 P2, 80% used</li>", h, tpl)
                self.assertNotIn("- INC0041200", h, tpl)
                if tpl not in ("report.html.j2", "report_portal.html.j2"):
                    continue
                path = os.path.join(d, tpl + ".html")
                with open(path, "w") as fh:
                    fh.write(h.replace("</body>", probe))
                out = subprocess.run([b, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars", "--virtual-time-budget=3000",
                                      "--window-size=800,900", "--dump-dom", "file://" + path], capture_output=True, text=True, timeout=120).stdout
                got = re.search(r"<title>R (\d+) (\d+) (\w+)</title>", out)
                self.assertTrue(got, (tpl, out[-300:]))
                if tpl == "report.html.j2":
                    self.assertEqual(got.group(1), "1", (tpl, "the boxes are not one width"))
                    self.assertEqual(int(got.group(2)) % 4, 0, tpl)
                self.assertEqual(got.group(3), "true", (tpl, "the satisfaction value wraps"))


@unittest.skipUnless(any(__import__("shutil").which(b) for b in ("google-chrome", "chromium")), "no headless browser here")
class PageFits(unittest.TestCase):
    """1.30.0: every table of every page fits its box at 1280 px with DejaVu (the fonts a Linux browser falls back to, wider
    than Segoe UI) in both looks - Teams / Directorates / Sites side by side (Open now (was), Change tasks closed / open in one
    column each, the backlog's chip under its line when short of room), Failed or backed out, the Leaderboard."""

    PROBE = ('<script>addEventListener("load",function(){setTimeout(function(){var o=[];[].slice.call(document.querySelectorAll(".tw"))'
             '.forEach(function(t){var d=t.scrollWidth-t.clientWidth;if(d>1){var s=t.closest("section");var h=t.closest(".fig");'
             'o.push((s?s.id:"")+"/"+(h&&h.querySelector("h3")?h.querySelector("h3").textContent:"")+" +"+d)}});'
             'o.push("page "+(document.documentElement.scrollWidth-innerWidth));document.title="R "+o.join("; ")},300)})</script></body>')
    FONTS = '<style>*{font-family:"DejaVu Sans" !important}code,pre,.num{font-family:"DejaVu Sans Mono" !important}</style></head>'

    def test_1280(self):
        import shutil
        b = shutil.which("google-chrome") or shutil.which("chromium")
        with tempfile.TemporaryDirectory() as d:
            for nm, x, o in (("flat", site(), OPTS), ("sites", site(sites=True), HIER_OPTS)):
                m = x["model"]
                keys = ["all"] + [p["key"] for p in m["pages"] if p["level"] != "team"][:3] + [p["key"] for p in m["pages"] if p["level"] == "team"][:2]
                for look in ("", "brief"):
                    for k in keys:
                        h = P.servicenow_insights_page(m, k, dict(o, look=look))
                        path = os.path.join(d, "p.html")
                        with open(path, "w") as fh:
                            fh.write(h.replace("</head>", self.FONTS, 1).replace("</body>", self.PROBE))
                        out = subprocess.run([b, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars", "--virtual-time-budget=4000",
                                              "--window-size=1280,900", "--dump-dom", "file://" + path], capture_output=True, text=True, timeout=120).stdout
                        self.assertIn("<title>R page 0</title>", out, (nm, k, look or "atlas", re.findall(r"<title>(.*?)</title>", out)))

    def test_the_merged_columns(self):
        m = site()["model"]
        h = P.servicenow_insights_page(m, "all", OPTS)
        self.assertIn("Open now (was)", h)
        self.assertNotIn(">Change tasks open<", h)
        self.assertRegex(h, r'<b>[\d,]+</b> <span class="mut">\([\d,]+\)</span>')


class PagePeriods(unittest.TestCase):
    """1.39.0: the period bar on the All page and on every unit's and team's page - the page's own rows, the window as
    written until a period is chosen; At a glance and the opened and resolved chart worked out by the Custom view's script
    (the same figs()), the rest marked whole window; the period in the address (#p=1m), Back to the one before; under
    require-trusted-types-for 'script'; it fits at 1280 px and on a phone."""

    PROBE = ('<script>var V=[];document.addEventListener("securitypolicyviolation",function(e){V.push(e.violatedDirective)});'
             'addEventListener("error",function(e){V.push(String(e.message))});'
             'function tv(k){var t=document.querySelector("#glance .tile[data-pp="+k+"]");return [].slice.call(t.querySelectorAll(".v")).filter(function(x){return !x.hidden})'
             '.map(function(x){return x.firstChild.textContent}).join("|")}'
             'addEventListener("load",function(){setTimeout(function(){var D=document,o={};try{o.h0=location.hash;o.t0=tv("opened");o.b0=tv("backlog");'
             'o.say=!D.getElementById("ppsay").hidden;o.whole=[].slice.call(D.querySelectorAll("#glance .tile[data-pp=\'-\'] .chip.ppw")).filter(function(x){return !x.hidden}).length;'
             'o.notes=[].slice.call(D.querySelectorAll("section.card .sh .chip.ppw")).filter(function(x){return !x.hidden}).length;o.chart=!!D.querySelector("#ppflow .cvsvg");'
             'D.querySelector("#ppbar button.cvpd[data-name=\'3m\']").click();o.h3=location.hash;o.t3=tv("opened");history.back();'
             'setTimeout(function(){o.hb=location.hash;o.tb=tv("opened");D.querySelector("#ppbar button.cvpd[data-name=\'all\']").click();o.ta=tv("opened");o.ha=location.hash;'
             'o.v=V;D.title="R "+JSON.stringify(o)},300)}catch(e){V.push("thrown: "+e.message);o.v=V;D.title="R "+JSON.stringify(o)}},300)})</script></body>')
    CSP = '<head><meta http-equiv="Content-Security-Policy" content="require-trusted-types-for \'script\'">'

    def chrome(self, h, frag="", width=1280, probe=None):
        import shutil
        b = shutil.which("google-chrome") or shutil.which("chromium")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.html")
            with open(path, "w") as fh:
                fh.write(h.replace("<head>", self.CSP, 1).replace("</head>", PageFits.FONTS, 1).replace("</body>", probe or self.PROBE))
            out = subprocess.run([b, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars", "--virtual-time-budget=6000",
                                  "--window-size=%d,900" % width, "--dump-dom", "file://" + path + frag], capture_output=True, text=True, timeout=120).stdout
        t = re.search(r"<title>R (.*?)</title>", out)
        self.assertTrue(t, out[-500:])
        return t.group(1).replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")

    def want(self, m, key, name):
        """The figures custom_figures works out from the page's own rows for a period: incidents and requests opened, open at its end."""
        d, _ = P.page_rows(m, m["scopes"][key])
        p = [x for x in d["periods"] if x["name"] == name][0]
        f = F.custom_figures(d, range(len(d["groups"])), (p["a"], p["b"]))
        return "{:,}".format(f["incidents"] + f["requests"]), "{:,}".format(f["backlog"][0])

    def test_rows_of_each_page(self):
        m = site()["model"]
        h = P.servicenow_insights_page(m, "all", OPTS)
        self.assertIn('<div class="cvres ppbar" id="ppbar">', h)
        self.assertIn('<select id="pppsel">', h)
        self.assertIn('<script type="application/json" id="pp-data">', h)
        self.assertLess(h.index('id="ppbar"'), h.index('id="glance"'))
        for k in ("opened", "backlog", "median", "sla", "-"):
            self.assertIn('data-pp="%s"' % k, h)
        team = m["teams"][0]
        d, _ = P.page_rows(m, m["scopes"][team])
        self.assertEqual(d["groups"], [team])
        self.assertEqual(d["n"], sum(1 for g in m["rows"]["full"]["cols"]["g"] if m["rows"]["full"]["groups"][g] == team))
        self.assertIn('id="pp-data"', P.servicenow_insights_page(m, team, OPTS))
        # off, and too many rows for a page: said, no rows carried
        self.assertNotIn('id="ppbar"', P.servicenow_insights_page(m, "all", dict(OPTS, page_periods=False)))
        x = site()
        m2 = F.servicenow_insights_model(x["r"], None, x["mask"], dict(OPTS, custom_view_max_rows=50))
        h2 = P.servicenow_insights_page(m2, "all", OPTS)
        self.assertNotIn('id="pp-data"', h2)
        self.assertRegex(h2, r'<p class="ppnone mut">Periods: not on this page - [\d,]+ tickets are more than servicenow_insights_custom_view_max_rows \(50\)\.</p>')
        # the window before cut for a page that fits without it: the window's rows, the periods that need it said
        n_t = P.page_rows(m, m["scopes"][team])[0]["n"]
        m3 = F.servicenow_insights_model(x["r"], None, x["mask"], dict(OPTS, custom_view_max_rows=n_t - 1))
        d3, _ = P.page_rows(m3, m3["scopes"][team])
        self.assertLess(d3["n"], n_t)
        self.assertFalse(any(o_ is not None and o_ < 0 for o_ in d3["cols"]["d"]))
        self.assertIn("the window before is over servicenow_insights_custom_view_max_rows", d3["left_line"])

    def test_the_split_and_the_map(self):
        x = site(sites=True)
        m = F.servicenow_insights_model(x["r"], None, x["mask"], dict(PARENTS_OPTS, split_label="BLUE", split_pattern="*- nova"))
        sp = set(m["split"]["groups"])
        mv = F.servicenow_insights_variant(m, "in")
        din, _ = P.page_rows(mv, mv["scopes"]["all"], "in")
        dout, _ = P.page_rows(F.servicenow_insights_variant(m, "out"), F.servicenow_insights_variant(m, "out")["scopes"]["all"], "out")
        self.assertTrue(din["groups"] and set(din["groups"]) <= sp)
        self.assertFalse(set(dout["groups"]) & sp)
        self.assertEqual(din["n"] + dout["n"], m["rows"]["full"]["n"])
        # the org map's All page: the Custom view's rows serve the bar too - carried once
        import test_insights_org as O
        mo = O.model()
        h = P.servicenow_insights_page(mo, "all", {"links": page_links(mo)})
        self.assertIn('id="ppbar"', h)
        self.assertIn('id="cv-data"', h)
        self.assertNotIn('id="pp-data"', h)

    @unittest.skipUnless(any(__import__("shutil").which(b) for b in ("google-chrome", "chromium")), "no headless browser here")
    def test_in_a_browser(self):
        m = site()["model"]
        team = m["teams"][0]
        for key in ("all", team):
            got = json.loads(self.chrome(P.servicenow_insights_page(m, key, OPTS), "#p=1m"))
            self.assertEqual(got["v"], [], (key, got))
            self.assertEqual(got["h0"], "#p=1m")
            self.assertEqual((got["t0"], got["b0"]), self.want(m, key, "1m"), (key, got))
            self.assertTrue(got["say"] and got["chart"])
            self.assertEqual(got["whole"], 4, got)                 # reopened, changes, hand-offs, close notes: the window's, said so
            self.assertGreater(got["notes"], 10)
            self.assertEqual((got["h3"], got["t3"]), ("#p=3m", self.want(m, key, "3m")[0]))
            self.assertEqual((got["hb"], got["tb"]), ("#p=1m", got["t0"]))        # Back
            w = re.search(r'<div class="tile" data-pp="opened"><span class="eyebrow">Tickets</span><span class="v">([\d,]+)', P.servicenow_insights_page(m, key, OPTS)).group(1)
            self.assertEqual((got["ta"], got["ha"]), (w, ""))               # the window: the page as written
        # without a hash: the window, nothing marked
        got = json.loads(self.chrome(P.servicenow_insights_page(m, "all", OPTS)))
        self.assertEqual((got["h0"], got["say"], got["whole"], got["notes"]), ("", False, 0, 0))

    @unittest.skipUnless(any(__import__("shutil").which(b) for b in ("google-chrome", "chromium")), "no headless browser here")
    def test_it_fits(self):
        m = site()["model"]
        for key in ("all", m["teams"][0]):
            for look in ("", "brief"):
                h = P.servicenow_insights_page(m, key, dict(OPTS, look=look))
                for width in (1280, 390):
                    got = self.chrome(h, "#p=1m", width, PageFits.PROBE)
                    out = [x for x in got.split("; ") if width == 1280 or x.startswith("page")]
                    self.assertEqual(out, ["page 0"], (key, look or "atlas", width, got))


class TicketStore(unittest.TestCase):
    """1.17.0: the ticket store - a run that reads in full only the tickets that changed gives the same answer as a full
    read (the items, the follow-up reads, the window, the cut), over new, changed, reopened, deleted, moved, closed then
    updated, a text edited without sys_updated_on moving and stale rows; the rows never hold text, people or the CI."""

    class Api(object):
        url = "https://sn.example.mil"

        def __init__(self, recs):
            self.recs, self.calls = recs, []

        def get(self, path, params):
            parts = path.split("/")
            q, table = params.get("sysparm_query", ""), parts[3]
            self.calls.append((table, params.get("sysparm_fields", ""), q))
            rows = [x for x in self.recs if (x.get("_table") or "incident") == table and snmock.matches(x, q)]
            if parts[2] == "stats":
                return {"result": {"stats": {"count": str(len(rows))}}}
            if "ORDERBYDESCopened_at" in q:
                rows.sort(key=lambda x: x.get("opened_at") or "", reverse=True)
            show = snmock.show_all if params.get("sysparm_display_value") == "all" else (lambda x: x)
            rows = [snmock.pick(show(x), params.get("sysparm_fields")) for x in rows]
            off, lim = int(params.get("sysparm_offset", 0)), int(params.get("sysparm_limit", 10000))
            return {"result": rows[off:off + lim]}

    TABLES = ["incident", "sc_req_item", "sc_task", "change_request", "change_task", "problem"]

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run([sys.executable, os.path.join(HERE, "servicenow", "insights_seed.py"), os.path.join(d, "s.json"), "0.25"],
                           check=True, capture_output=True)
            with open(os.path.join(d, "s.json")) as f:
                cls.recs = json.load(f)
        cls.win = rd.window(6, 0, "2026-10-31", True, "", rd.datetime.datetime(2026, 11, 1, 6, 0, tzinfo=rd.UTC))

    def read(self, recs, store=None, cap=20000, groups=None):
        api = self.Api(recs)
        return rd.read_all(api, groups or [], self.TABLES, self.win, cap, first_assigned=True, store=store), api

    def rows(self, res, store):
        return dict((x["s"], {"u": x["u"], "c": x["c"], "i": x["i"], "f": x["f"]}) for x in rd.store_save(res, store))

    def changed(self):
        recs = copy.deepcopy(self.recs)
        inc = [x for x in recs if (x.get("_table") or "incident") == "incident" and str(x.get("opened_at", "")) >= "2026-06"]
        closed = [x for x in inc if x.get("state") == "7"]
        bump = "2026-10-31 23:00:00"
        inc[0].update(short_description="Changed text now", priority="1", sys_updated_on=bump)
        closed[0].update(state="2", active="true", closed_at="", resolved_at="", reopen_count="1", sys_updated_on=bump)
        other = [x for x in inc if x.get("assignment_group") != inc[5].get("assignment_group")][0]["assignment_group"]
        inc[5].update(assignment_group=other, sys_updated_on=bump)
        recs.remove(inc[7])
        closed[1].update(close_notes="Replaced the token, then confirmed with the user.", sys_updated_on=bump)
        closed[2]["short_description"] = "Quietly edited"
        recs.append(dict(copy.deepcopy(inc[2]), sys_id="s-INC9999999", number="INC9999999", opened_at="2026-10-30 10:00:00", sys_updated_on=bump))
        return recs, closed[3]["sys_id"]

    def same(self, a, b):
        for k in sorted(set(a) | set(b)):
            if k != "store":
                self.assertEqual(json.dumps(a.get(k)), json.dumps(b.get(k)), k)

    def test_incremental_is_full(self):
        st = rd.Reuse({})
        first, _ = self.read(self.recs, st)
        rows = self.rows(first, st)
        self.assertEqual(len(rows), len(first["items"]))
        recs, stale = self.changed()
        rows[stale]["u"] = "2020-01-01 00:00:00"            # a database restored from an older backup
        rows[stale]["i"]["priority"] = "STALE"
        full, af = self.read(recs)
        st2 = rd.Reuse(rows)
        inc, ai = self.read(recs, st2)
        self.same(full, inc)
        self.assertGreater(len(st2.fresh), len(full["items"]) * 0.9)
        self.assertNotIn(stale, st2.fresh)
        self.assertLess(len(ai.calls), len(af.calls))
        self.assertTrue(any("sys_idIN" in q for _t, _f, q in ai.calls))            # the changed ones, by sys_id
        light = [f for t, f, q in ai.calls if t == "incident" and "ORDERBYDESC" in q]
        self.assertTrue(light and all("reopen_count" not in f and "sys_updated_on" in f and "assignment_group" in f for f in light), light)

    def test_a_renamed_group(self):
        """1.17.0b: a group renamed in ServiceNow between two runs, its tickets untouched (sys_updated_on the same): the run
        that reuses the store shows the new name everywhere, as a full read - one team, not two. The store keeps the
        group's sys_id, never its name; the hand-off paths as sys_audit keeps them (sys_ids), named each run."""
        recs = copy.deepcopy(self.recs)
        for x in recs:
            if x.get("_table") == "sys_audit":
                for f in ("oldvalue", "newvalue"):
                    x[f] = next((g["sys_id"] for g in snmock.GROUPS if g["name"] == x[f]), x[f])
        saved = copy.deepcopy(snmock.GROUPS)
        try:
            st = rd.Reuse({})
            first, _ = self.read(recs, st)
            rows = self.rows(first, st)
            self.assertIn("Ops - Windows", set(it["group"] for it in first["items"]))
            self.assertNotIn('"Ops - Windows"', json.dumps(rows))
            snmock.rename({"Ops - Windows": "Ops - Windows Servers"}, recs)
            full, _ = self.read(recs)
            st2 = rd.Reuse(rows)
            inc, _ = self.read(recs, st2)
            self.same(full, inc)
            self.assertGreater(len(st2.fresh), len(full["items"]) * 0.9)
            names = set(it["group"] for it in inc["items"])
            self.assertIn("Ops - Windows Servers", names)
            self.assertNotIn("Ops - Windows", names)
            self.assertIn("Ops - Windows Servers", json.dumps(inc["audit"]))
            self.assertNotIn('"Ops - Windows"', json.dumps(inc["audit"]))
        finally:
            snmock.GROUPS[:] = saved

    def test_the_rows_hold_no_text_people_or_ci(self):
        st = rd.Reuse({})
        res, _ = self.read(self.recs, st)
        for r in rd.store_save(res, st):
            self.assertFalse(set(r["i"]) & set(rd.LIGHT_KEYS), r["i"])
            blob = json.dumps(r)
            kept = set(json.dumps(v) for v in r["i"].values())           # a request's description may be its catalog item's name
            for it in [x for x in res["items"] if x["sys_id"] == r["s"]]:
                for k in ("short_description", "close_notes", "assigned_to", "caller", "cmdb_ci"):
                    if len(str(it.get(k) or "")) > 12 and json.dumps(it[k]) not in kept:      # a whole value, as JSON writes it
                        self.assertNotIn(json.dumps(it[k]), blob, (k, it[k]))

    def test_open_tickets_follow_ups_read_again(self):
        st = rd.Reuse({})
        res, _ = self.read(self.recs, st)
        rows = self.rows(res, st)
        st2 = rd.Reuse(rows)
        again, api = self.read(self.recs, st2)
        self.same(res, again)
        asked = " ".join(q for t, _f, q in api.calls if t == "task_sla")
        opened = [it["sys_id"] for it in again["items"] if rd.is_open(it) and it["table"] == "incident" and not it.get("done_at")]
        self.assertTrue(opened and all(s in asked for s in opened[:5]))               # the open ones always
        self.assertFalse(st2.closed & set(opened))

    def test_a_cut_run_keeps_nothing(self):
        st = rd.Reuse({})
        with tempfile.TemporaryDirectory() as d:
            store, why = rd.store_load(os.path.join(d, "none.json"))
            self.assertEqual(why, "the database holds no tickets yet")
            res, _ = self.read(self.recs, store, cap=30)
            self.assertTrue(res["cut"])                                       # the window itself cut
            out = os.path.join(d, "out.json")
            got = rd.store_finish(res, store, why, out)["store"]
            self.assertEqual(got["not_saved"], "the window's records were cut (servicenow_insights_max_records)")
            self.assertFalse(os.path.exists(out))                             # nothing of a cut run kept
            self.assertEqual((got["mode"], got["saved"]), ("full", 0))
            st = rd.Reuse({})
            whole, _ = self.read(self.recs, st)
            self.assertEqual(rd.store_finish(whole, st, why, out)["store"]["saved"], len(whole["items"]))
            with open(out) as f:
                kept = json.load(f)
            self.assertEqual(len(kept["rows"]), len(whole["items"]))
            self.assertEqual(kept["seen"], sorted(it["sys_id"] for it in whole["items"]))     # 1.17.0b: for the prune
        self.assertEqual(rd.store_load("/nonexistent", full_refresh=True)[1], "servicenow_insights_full_refresh")

    def test_rows_of_another_format_read_again(self):
        st = rd.Reuse({})
        res, _ = self.read(self.recs, st)
        rows = self.rows(res, st)
        for v in rows.values():
            v["c"] = "0"
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "held.json")
            with open(p, "w") as f:
                json.dump({"rows": rows}, f)
            store, why = rd.store_load(p)
        self.assertEqual((store.rows, why), ({}, "the database holds no tickets yet"))

    def test_the_line(self):
        L = H.servicenow_insights_store_line
        r = {"store": {"mode": "incremental", "why": "", "from_sn": 4, "from_db": 2249, "saved": 4, "not_saved": ""}}
        self.assertEqual(L(r, {"ok": True, "store": {"saved": 4, "seconds": 0.4}})["line"],
                         "Tickets: 2,253 - 4 read from ServiceNow (new or changed), 2,249 from the report database; 4 written to the report database in 0 s.")
        r = {"store": {"mode": "full", "why": "the database holds no tickets yet", "from_sn": 9, "from_db": 0, "saved": 0,
                       "not_saved": "the records were cut (servicenow_insights_max_records)"}}
        self.assertEqual(L(r)["page"], "9 tickets - all read from ServiceNow - a full read (the database holds no tickets yet); nothing written: "
                                       "the records were cut (servicenow_insights_max_records).")
        self.assertEqual(L({}), {"line": "", "page": ""})
        self.assertIn("Check mode: nothing written", L(r, {}, True)["line"])


class StoreTables(unittest.TestCase):
    """1.17.0b: a 1.16.0 database whose writer cannot make the three new tables (no CREATE grant, or create false): the
    figures go on (read and write), the store and the ACT cache are off with one warning naming them."""

    class Conn(object):
        def __init__(self, have, deny=False):
            self.have, self.deny, self.sql = set(have), deny, []

        def cursor(self):
            return self

        def execute(self, sql, args=None):
            self.sql.append(sql)
            self.last = sql
            if sql.startswith("CREATE"):
                if self.deny:
                    raise Exception(1142, "CREATE command denied to user 'w'@'%' for table 'insights_ticket'")
                self.have.add(sql.split()[5] if "IF NOT EXISTS" in sql else sql.split()[2])

        def fetchall(self):
            return [(x,) for x in self.have]

        def fetchone(self):
            return (1,)

        def commit(self):
            pass

    def setUp(self):
        import site_insights_db as D
        self.D = D
        self.old = [o for o in D.OBJECTS if o not in D.STORE_TABLES]          # a 1.16.0 database

    def test_no_create_grant(self):
        for ro in (False, True):                                               # write; read, tickets_load, act_load
            c = self.Conn(self.old, deny=True)
            v, made, store = self.D.schema(c, True, ro)
            self.assertEqual((v, made), (1, False))
            self.assertEqual(store["missing"], list(self.D.STORE_TABLES))
            self.assertIn("CREATE command denied", store["error"])
            say = self.D.store_tables_say(store, True)
            self.assertIn("insights_ticket, insights_act_issue and insights_act_class", say)
            self.assertIn("grant the writer CREATE", say)
            self.assertIn("the figures are written as before", say)

    def test_create_false(self):
        for ro in (False, True):
            c = self.Conn(self.old)
            v, made, store = self.D.schema(c, False, ro)
            self.assertEqual((v, made, store["missing"]), (1, False, list(self.D.STORE_TABLES)))
            self.assertFalse(any(x.startswith("CREATE") for x in c.sql))
            self.assertIn("(servicenow_insights_db_create: false)", self.D.store_tables_say(store, False))

    def test_made_when_allowed(self):
        c = self.Conn(self.old)
        v, made, store = self.D.schema(c, True)
        self.assertEqual((v, made, store), (1, True, {"missing": [], "error": ""}))
        self.assertEqual(sum(1 for x in c.sql if x.startswith("CREATE")), 3)
        # a database without the figures' tables: they are made first, as before; create false is still an error
        c = self.Conn([])
        self.assertEqual(self.D.schema(c, True)[:2], (1, True))
        with self.assertRaises(RuntimeError):
            self.D.schema(self.Conn([]), False)
        self.assertEqual(self.D.schema(self.Conn([]), False, True)[:2], (0, False))


class ActCache(unittest.TestCase):
    """1.17.0: the ACT cache - each description's issue kept by its key's hash; the issues by ACT's short name, masked
    (the one text the database holds); only new descriptions asked; another model or prompt, another cache."""

    MASK = {"pairs": [["Dana Whitfield", "PERSON-1"], ["Whitfield", "PERSON-2"], ["prn01-fl3", "HOST-1"]], "counts": {}}

    def test_the_label_is_masked(self):
        L = F.act_label_for_store
        for name, want in (("Account locked out / password reset", "Account locked out / password reset"),
                           ("Windows 11 upgrade fails", "Windows 11 upgrade fails"), ("Wi-Fi drops on floor 3", "Wi-Fi drops on floor 3"),
                           ("VPN drops for Dana Whitfield at 198.51.100.9 on wks-1001", "VPN drops for [person] at [ip] on [host]"),
                           ("Printer prn01-fl3 jams", "Printer [host] jams"), ("Mail to jo.smith@example.mil bounces", "Mail to [email] bounces"),
                           ("Call (703) 555-0101", "Call [phone]"), ("PERSON-1's account", "[person]'s account")):
            self.assertEqual(L(name, self.MASK), want, name)
        self.assertEqual(len(L("x" * 200, self.MASK)), 80)

    def test_rows_and_back(self):
        x = site()
        st = dict(F.servicenow_insights_state(), issues=[{"name": "Printer offline", "about": "a", "kind": "incident", "self_service": False},
                                                         {"name": "VPN drops for Dana Whitfield", "about": "", "kind": "incident", "self_service": True}])
        keys = sorted(set(F.desc_key(it.get("short_description")) for it in x["r"]["items"] if it["table"] == "incident"))[:6]
        st["classes"] = dict((k, "Printer offline" if i % 2 else "VPN drops for Dana Whitfield") for i, k in enumerate(keys) if k)
        rows = F.servicenow_insights_act_cache_rows(st, self.MASK)
        self.assertEqual([i["label"] for i in rows["issues"]], ["Printer offline", "VPN drops for [person]"])
        self.assertTrue(all(len(h) == 64 and not set(h) - set("0123456789abcdef") for h, _n in rows["classes"]))
        self.assertNotIn(keys[1], json.dumps(rows))                                  # the words never, the hash only
        cached = {"issues": rows["issues"], "classes": dict((h, n) for h, n in rows["classes"])}
        back = F.servicenow_insights_act_cached(F.servicenow_insights_state(), x["r"], None, cached, OPTS)
        self.assertEqual([i["name"] for i in back["issues"]], ["Printer offline", "VPN drops for [person]"])
        self.assertTrue(back["cached"] >= 1)
        left = F.servicenow_insights_descriptions(x["r"], None, OPTS, back)
        self.assertFalse(set(d["key"] for d in left) & set(back["classes"]))           # those placed are not asked
        self.assertEqual(len(left) + len(back["classes"]), len(F.servicenow_insights_descriptions(x["r"], None, OPTS)))
        again = F.servicenow_insights_act_cache_rows(back, self.MASK)                  # an issue keeps its number
        self.assertEqual([(i["no"], i["label"]) for i in again["issues"]], [(i["no"], i["label"]) for i in rows["issues"]])

    def test_the_key(self):
        K = F.servicenow_insights_act_cache_key
        a = K(OPTS, "genai", "model-a")
        self.assertNotEqual(a, K(OPTS, "genai", "model-b"))
        self.assertNotEqual(a, K(dict(OPTS, act_max_issues=12), "genai", "model-a"))       # another prompt
        self.assertEqual(a, K(dict(OPTS), "genai", "model-a"))


class RecordsCut(unittest.TestCase):
    """1.16.0: a table over servicenow_insights_max_records keeps its NEWEST records (opened_at descending), then reads
    the older records that count in the window (open, or done in it): the window whole, the one before cut - no compare,
    said. That read capped too: the window cut - a banner on every page and first in the email, the job's warning, and no
    incomplete month written to the report database."""

    class Api(object):
        """The Table API on a list of incidents (snmock's own query rules), and the Aggregate API's count."""

        def __init__(self, recs, stats=True):
            import snmock
            self.match, self.recs, self.stats, self.url, self.calls = snmock.matches, recs, stats, "https://sn.example.mil", []

        def get(self, path, params):
            self.calls.append((path, dict(params)))
            q = params.get("sysparm_query", "")
            if path == "api/now/stats/incident":
                if not self.stats:
                    raise rd.SnError("GET api/now/stats/incident: 403 no access")
                return {"result": {"stats": {"count": str(sum(1 for r in self.recs if self.match(r, q)))}}}
            if path != "api/now/table/incident":
                return {"result": []}
            rows = sorted([r for r in self.recs if self.match(r, q)], key=lambda r: r["opened_at"], reverse="ORDERBYDESC" in q)
            off, lim = int(params.get("sysparm_offset", 0)), int(params.get("sysparm_limit", 10000))
            return {"result": rows[off:off + lim]}

    @staticmethod
    def recs():
        """Window 2026-05-01 to 2026-11-01, the window before from 2025-11-01: 4 opened each month, from November 2025 to
        October 2026, each closed 3 days later - and one opened in 2025-11 still open (the backlog)."""
        out = []
        for i in range(12):
            y, mo = (2025, 11 + i) if i < 2 else (2026, i - 1)
            for d in (3, 9, 15, 21):
                op = "%04d-%02d-%02d 10:00:00" % (y, mo, d)
                cl = "%04d-%02d-%02d 10:00:00" % (y, mo, d + 3)
                out.append({"sys_id": "s%03d" % len(out), "number": "INC%07d" % len(out), "opened_at": op, "closed_at": cl,
                            "resolved_at": cl, "active": "false", "state": "7", "assignment_group": "Ops - Linux", "priority": "4"})
        out.append({"sys_id": "old", "number": "INC9999999", "opened_at": "2025-11-02 08:00:00", "closed_at": "", "resolved_at": "",
                    "active": "true", "state": "2", "assignment_group": "Ops - Linux", "priority": "3"})
        return out

    def read(self, cap, recs=None, compare=True, stats=True):
        win = rd.window(6, 0, "2026-10-31", compare, "", NOW)
        api = self.Api(recs or self.recs(), stats)
        with mock.patch.object(rd, "read_slas", return_value=({}, {}, "")), mock.patch.object(rd, "read_states", return_value=({}, "")), \
                mock.patch.object(rd, "read_survey", return_value=([], {}, "")):
            return rd.read_all(api, [], ["incident"], win, cap, audit=False, members=False, group_parents=False), api

    def test_whole(self):
        r, api = self.read(1000)
        t = r["tables"][0]
        self.assertEqual((t["read"], t["more"], r["cut"], r.get("compare_cut")), (49, False, [], None))
        self.assertTrue(r["window"]["compare"])
        self.assertIn("^ORDERBYDESCopened_at", api.calls[0][1]["sysparm_query"])

    def test_the_window_before_cut(self):
        r, api = self.read(30)                     # 24 opened in the window, 6 of the 25 before kept
        t = r["tables"][0]
        self.assertTrue(t["more"])
        self.assertFalse(t.get("window_cut"))
        self.assertEqual(t["total"], 49)
        self.assertEqual(t["cut"][:10], "2026-03-15")      # the oldest opened_at kept
        nums = set(it["number"] for it in r["items"])
        self.assertIn("INC9999999", nums)                  # the backlog's old ticket: read by the second read
        self.assertTrue(all(it["opened_at"] >= "2026-05-01" for it in r["items"] if it["number"] != "INC9999999" and it["opened_at"] < "2026-03-15"))
        self.assertEqual(r["cut"], [])
        self.assertEqual(r["compare_cut"], ["incident"])
        self.assertFalse(r["window"]["compare"])
        self.assertEqual(r["window"]["prev_since"], r["window"]["since"])
        older = [c for c in api.calls if "opened_at<=2026-03-15" in c[1].get("sysparm_query", "")]
        self.assertEqual(len(older), 1)
        self.assertIn("active=true^ORclosed_at>=2026-04-30 00:00:00^ORresolved_at>=2026-04-30 00:00:00", older[0][1]["sysparm_query"])

    def test_the_window_cut(self):
        r, _api = self.read(10)
        t = r["tables"][0]
        self.assertTrue(t["window_cut"])
        self.assertEqual(t["read"], 19)                     # 10 + 10, the cut's own record read twice, counted once
        self.assertEqual(t["cut"][:10], "2026-06-09")      # where the second read stopped
        self.assertEqual(r["cut"], [t])
        self.assertFalse(r["window"]["compare"])           # a cut drops the compare, the window cut too
        self.assertNotIn("compare_cut", r)                 # said as the window cut, not as no compare
        line = F.cut_lines(r)
        self.assertEqual(line, ["incident: only the newest 19 of 49 read, back to 2026-06-09. Figures in this report are incomplete: "
                                "raise servicenow_insights_max_records."])
        r2, _ = self.read(10, stats=False)
        self.assertEqual(F.cut_lines(r2), ["incident: only the newest 19 read, back to 2026-06-09, and there are more. Figures in this "
                                           "report are incomplete: raise servicenow_insights_max_records."])

    def test_said_on_every_page_and_first_in_the_email(self):
        r, _ = self.read(10)
        m = F.servicenow_insights_model(r, None, {}, OPTS)
        self.assertEqual(len(m["cut"]), 1)
        h = P.servicenow_insights_page(m, "all", OPTS)
        self.assertIn('<div class="callout cutbar" role="alert"><b>Records cut: some figures are incomplete.</b>', h)
        self.assertLess(h.index("cutbar"), h.index('id="glance"') if 'id="glance"' in h else len(h))
        for k in [p_["key"] for p_ in m["pages"]]:
            self.assertIn("cutbar", P.servicenow_insights_page(m, k, OPTS), k)
        doc = F.servicenow_insights_doc(m, "all", OPTS)
        self.assertEqual(doc["sections"][0]["title"], "Records cut: some figures are incomplete")
        self.assertEqual(doc["sections"][0]["lines"], m["cut"])
        self.assertFalse([x for x in F.not_read_lines(m) if x.startswith("incident:")])     # said once, in the banner

    def test_no_compare_said(self):
        r, _ = self.read(30)
        m = F.servicenow_insights_model(r, None, {}, OPTS)
        self.assertEqual(m["cut"], [])
        self.assertIn("no compare this run. The window's figures are whole.", m["compare_cut"])
        h = P.servicenow_insights_page(m, "all", OPTS)
        self.assertIn("<b>No compare this run.</b>", h)
        self.assertNotIn("role=\"alert\"", h)
        self.assertEqual(F.servicenow_insights_doc(m, "all", OPTS)["sections"][0]["title"], "No compare this run")
        self.assertIn("the window before cut at 2026-03-15: no compare", " ".join(F.not_read_lines(m)))

    def test_never_an_incomplete_month_in_the_database(self):
        """The tester's finding: a ticket opened before the cut and resolved in a later month is missing from that month too,
        so a window cut writes nothing - no run, no month, no figure."""
        import servicenow_insights_history as H
        r, _ = self.read(10)
        m = F.servicenow_insights_model(r, None, {}, OPTS)
        runs = H.servicenow_insights_db_runs(r, m, OPTS)["runs"]
        self.assertTrue(runs and all(x.get("cut") for x in runs))
        self.assertEqual(H.servicenow_insights_backfill_months(r), [])
        self.assertEqual(H.servicenow_insights_backfill_months(r, True), ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"])
        st = H.servicenow_insights_db_status({"on": True, "host": "db", "port": 3309, "database": "site_reports", "read": {"ok": True},
                                              "write": {"ok": True, "months": [], "cut": ["the window", "2026-05"], "written": {"runs": 0}, "held": []}})
        self.assertFalse(st["written"])
        self.assertIn("NOT written - the records were cut (servicenow_insights_max_records): an incomplete run is never written;", st["line"])
        self.assertEqual(st["page"], "Not written this run: the records were cut (servicenow_insights_max_records). An incomplete run is never written.")
        r0, _ = self.read(1000)
        self.assertFalse([x for x in H.servicenow_insights_db_runs(r0, F.servicenow_insights_model(r0, None, {}, OPTS), OPTS)["runs"] if x.get("cut")])
        r1, _ = self.read(30)                                # the window before cut only: the window's runs written
        self.assertFalse([x for x in H.servicenow_insights_db_runs(r1, F.servicenow_insights_model(r1, None, {}, OPTS), OPTS)["runs"] if x.get("cut")])

    def test_the_testers_august(self):
        """The tester's repro: 30 incidents opened 2026-03-03 to 03-09, resolved 2026-08-14, and newer ones after them. Whole,
        August has them; a cap that cuts the window (its second read too) leaves them out - and nothing is written."""
        import servicenow_insights_history as H
        recs = [{"sys_id": "m%02d" % i, "number": "INC1%06d" % i, "opened_at": "2026-03-0%d 10:%02d:00" % (3 + i % 7, i), "closed_at": "2026-08-14 10:00:00",
                 "resolved_at": "2026-08-14 10:00:00", "active": "false", "state": "7", "assignment_group": "Ops - Linux", "priority": "4"} for i in range(30)]
        recs += [{"sys_id": "n%03d" % i, "number": "INC2%06d" % i, "opened_at": "2026-%02d-%02d 09:00:00" % (5 + i // 25, 1 + i % 25),
                  "closed_at": "2026-%02d-%02d 12:00:00" % (5 + i // 25, 1 + i % 25), "resolved_at": "2026-%02d-%02d 12:00:00" % (5 + i // 25, 1 + i % 25),
                  "active": "false", "state": "7", "assignment_group": "Ops - Linux", "priority": "4"} for i in range(150)]
        whole, _ = self.read(1000, recs)
        aug = lambda r: sum(1 for it in r["items"] if it["number"].startswith("INC1"))   # noqa: E731
        self.assertEqual(aug(whole), 30)
        cut, _ = self.read(60, recs)
        self.assertTrue(cut["tables"][0]["window_cut"])
        self.assertLess(aug(cut), 30)
        m = F.servicenow_insights_model(cut, None, {}, OPTS)
        self.assertTrue(all(x.get("cut") for x in H.servicenow_insights_db_runs(cut, m, OPTS)["runs"]))

    def test_ties_at_the_cut(self):
        """The tester's second finding: 20 incidents opened in the same second, the cap falling among them - every one read
        (the second read takes the cut's own second, opened_at<=); more in one second than the cap - a window cut, said."""
        recs = [{"sys_id": "t%02d" % i, "number": "INC3%06d" % i, "opened_at": "2026-08-01 10:00:00", "closed_at": "2026-08-02 10:00:00",
                 "resolved_at": "2026-08-02 10:00:00", "active": "false", "state": "7", "assignment_group": "Ops - Linux", "priority": "4"} for i in range(20)]
        recs += [{"sys_id": "u%02d" % i, "number": "INC4%06d" % i, "opened_at": "2026-09-%02d 10:00:00" % (1 + i), "closed_at": "2026-09-%02d 12:00:00" % (1 + i),
                  "resolved_at": "2026-09-%02d 12:00:00" % (1 + i), "active": "false", "state": "7", "assignment_group": "Ops - Linux", "priority": "4"} for i in range(25)]
        r, _ = self.read(30, recs)                          # 25 newer, then 5 of the 20 tied: the second read takes all 20
        t = r["tables"][0]
        self.assertTrue(t["more"])
        self.assertFalse(t.get("window_cut"))
        self.assertEqual(len([it for it in r["items"] if it["number"].startswith("INC3")]), 20)
        r, _ = self.read(10, recs)                          # 20 in one second, a cap of 10: never silent
        self.assertTrue(r["tables"][0]["window_cut"])
        self.assertTrue(F.cut_lines(r))

    def test_exactly_at_the_cap(self):
        """The tester's third finding: a table of exactly max_records is not cut - one more than the cap is asked for -
        and so for the second read."""
        recs = self.recs()                                   # 49
        for cap, more in ((48, True), (49, False), (50, False)):         # cap - 1, cap, cap + 1
            r, api = self.read(cap, recs)
            self.assertEqual(r["tables"][0]["more"], more, cap)
            self.assertEqual(api.calls[0][1]["sysparm_limit"], min(1000, cap + 1))
        # the second read at its own edge: 10 in October fill the first read; K in June (the window's older ones) and the cut's
        # own record make the second read K + 1 - K = 9: exactly the cap, whole; K = 10: one more, the window cut
        def recs2(k):
            mk = lambda i, op, cl: {"sys_id": "s%03d" % i, "number": "INC6%06d" % i, "opened_at": op, "closed_at": cl, "resolved_at": cl,   # noqa: E731
                                    "active": "false", "state": "7", "assignment_group": "Ops - Linux", "priority": "4"}
            out = [mk(i, "2026-10-%02d 10:00:00" % (1 + i), "2026-10-%02d 12:00:00" % (1 + i)) for i in range(10)]
            out += [mk(100 + i, "2026-06-%02d 10:00:00" % (1 + i), "2026-06-%02d 12:00:00" % (1 + i)) for i in range(k)]
            return out + [mk(200 + i, "2026-01-%02d 10:00:00" % (1 + i), "2026-01-%02d 12:00:00" % (1 + i)) for i in range(5)]
        for k, cut in ((9, False), (10, True)):
            r, _ = self.read(10, recs2(k))
            self.assertTrue(r["tables"][0]["more"])
            self.assertEqual(bool(r["tables"][0].get("window_cut")), cut, k)

    def test_the_window_edge(self):
        """Records exactly on the window's first second: opened at it counts as opened in the window, resolved at it as
        resolved in it - and one second before, in the window before."""
        e = [{"sys_id": "e1", "number": "INC5000001", "opened_at": "2026-05-01 00:00:00", "closed_at": "2026-05-02 00:00:00", "resolved_at": "2026-05-02 00:00:00",
              "active": "false", "state": "7", "assignment_group": "Ops - Linux", "priority": "4"},
             {"sys_id": "e2", "number": "INC5000002", "opened_at": "2026-04-20 00:00:00", "closed_at": "2026-05-01 00:00:00", "resolved_at": "2026-05-01 00:00:00",
              "active": "false", "state": "7", "assignment_group": "Ops - Linux", "priority": "4"},
             {"sys_id": "e3", "number": "INC5000003", "opened_at": "2026-04-30 23:59:59", "closed_at": "2026-04-30 23:59:59", "resolved_at": "2026-04-30 23:59:59",
              "active": "false", "state": "7", "assignment_group": "Ops - Linux", "priority": "4"}]
        r, _ = self.read(1000, e)
        m = F.servicenow_insights_model(r, None, {}, OPTS)
        c = m["scopes"]["all"]["counts"]
        self.assertEqual((c["incidents"], c["resolved"]), (1, 2))          # e1 opened at the first second; e1 and e2 resolved in the window
        self.assertEqual(c["incidents_prev"], 2)                           # e2 and e3 opened in the window before

    def test_the_module_writes_none_of_them(self):
        import site_insights_db as D

        class Cur(object):
            def __init__(self):
                self.sql = []

            def execute(self, sql, args=None):
                self.sql.append(sql)

        runs = {"runs": [{"run": {"period": "6m", "month": "2026-10"}, "cut": True}, {"run": {"period": "month", "month": "2026-05"}, "cut": True}]}
        cur = Cur()
        conn = mock.Mock()
        conn.cursor.return_value = cur
        with mock.patch.object(D, "held", return_value=[]):
            out = D.write(conn, {"rows": runs, "job": "1"})
        self.assertEqual(out["cut"], ["the window", "2026-05"])
        self.assertEqual(out["written"]["runs"], 0)
        self.assertFalse([x for x in cur.sql if x.lstrip().upper().startswith(("INSERT", "DELETE"))])


class PyMySQLLoginBug(unittest.TestCase):
    """1.16.0: PyMySQL 1.2.1 to 1.2.3 raise AttributeError ('NoneType' ... 'is_auth_switch_request') on the first
    caching_sha2_password login over plain TCP after the server's cache is empty: one more try, never more, then the
    plain-words error naming 1.2.4."""

    class Fake:
        VERSION_STRING = "1.2.3"

        def __init__(self, fails):
            self.fails, self.calls = fails, []

        def connect(self, **kw):
            self.calls.append(kw)
            if len(self.calls) <= self.fails:
                raise AttributeError("'NoneType' object has no attribute 'is_auth_switch_request'")
            return "conn"

    def go(self, fails):
        import site_insights_db as D
        fake = self.Fake(fails)
        with mock.patch.object(D, "pymysql", fake, create=True):        # create: CI's Python has no PyMySQL
            try:
                return D.connect_plain({"host": "db"}), fake
            except RuntimeError as e:
                return e, fake

    def test_first_login_fails_second_works(self):
        got, fake = self.go(1)
        self.assertEqual(got, "conn")
        self.assertEqual(len(fake.calls), 2)
        self.assertTrue(all(c.get("ssl_disabled") is True for c in fake.calls))

    def test_twice_said_once(self):
        got, fake = self.go(5)
        self.assertEqual(len(fake.calls), 2)                     # never more than one retry
        self.assertIsInstance(got, RuntimeError)
        for w in ("PyMySQL 1.2.3 has a bug", "1.2.1 to 1.2.3", "Launch again", "PyMySQL 1.2.4 or later", "is_auth_switch_request"):
            self.assertIn(w, str(got))

    def test_no_bug_one_login(self):
        got, fake = self.go(0)
        self.assertEqual((got, len(fake.calls)), ("conn", 1))

    def test_tls_path_untouched(self):
        import site_insights_db as D
        src = open(D.__file__).read()
        self.assertIn("conn = pymysql.connect(ssl=ctx, **kw)", src)
        self.assertEqual(src.count("conn = connect_plain(kw)"), 1)


class History(unittest.TestCase):
    """1.13.0: the report database - the rows of a run (every org node, the split's halves; figures only), each calendar
    month of the window worked out by the report's own model (the month so far partial), the schema (tables and views,
    no DROP needed), a partial month never over a complete one, the backfill's months, the trend (this run's months win,
    a short history starts where it starts) and the page's Trends section."""

    @classmethod
    def setUpClass(cls):
        import servicenow_insights_history as H
        import site_insights_db as D
        cls.H, cls.D = H, D
        cls.x = site(sites=True)
        cls.o = dict(PARENTS_OPTS, split_label="BLUE", split_pattern="*- nova")
        cls.m = F.servicenow_insights_model(cls.x["r"], None, cls.x["mask"], cls.o)
        cls.rows = H.servicenow_insights_db_rows(cls.m, {})
        cls.runs = H.servicenow_insights_db_runs(cls.x["r"], cls.m, cls.o)

    def hist(self, months, value=90.0, partial=()):
        """Stored months as the module reads them back: [month, window_end, node, variant, metric, value, partial]."""
        return {"ok": True, "months_kept": len(months), "rows": [[mo, mo + "-28 00:00:00", node, v, k, value + i, 1 if mo in partial else 0]
                                                                  for i, mo in enumerate(months) for node, v in (("all", ""), ("site:SITEA", ""), ("all", "in"))
                                                                  for k in ("sla_met_pct", "incidents")]}

    def test_every_node_and_half(self):
        r = self.rows
        self.assertEqual(r["run"], {"source": "sn.example.mil", "period": "6m", "window_start": "2026-05-01 00:00:00",
                                    "window_end": "2026-11-01 00:00:00", "month": "2026-10", "partial": False, "schema": 1})
        keys = set((n_[1], n_[0]) for n_ in r["nodes"])
        self.assertEqual(keys, set(("", k) for k in self.m["scopes"]) | set((v, k) for v in ("in", "out") for k in self.m["variants"][v]))
        al = [n_ for n_ in r["nodes"] if n_[:2] == ["all", ""]][0]
        s = self.m["scopes"]["all"]
        self.assertEqual(al[2:6], ["All assignment groups", "all", "", 0])
        self.assertEqual(al[6:], [s["counts"]["incidents"], s["counts"]["requests"], s["counts"]["resolved"], s["backlog"][0], s["sla"][0], s["median_h"][0]])
        self.assertEqual(max(n_[5] for n_ in r["nodes"]), 3)                                              # site 1, unit 2, team 3
        figs = dict(((f[0], f[1], f[2], f[3]), f[4]) for f in r["figures"])
        self.assertEqual(figs[("all", "", "incidents", "")], s["counts"]["incidents"])
        self.assertEqual(figs[("all", "in", "incidents", "")] + figs[("all", "out", "incidents", "")], s["counts"]["incidents"])
        self.assertIn(("all", "", "outcome", "Fixed"), figs)
        self.assertTrue(any(k[2] == "hot_ci" for k in figs))
        self.assertTrue(all(isinstance(f[4], float) for f in r["figures"]))

    def test_figures_only(self):
        """No caller, person, description or close note: only the org nodes' and the hot CIs' names, and numbers."""
        import insights_seed as seed
        text = json.dumps(self.runs)
        for who in [p_ for v in seed.PEOPLE.values() for p_ in v] + list(seed.CALLERS[:20]):
            self.assertNotIn(who, text)
        for it in self.x["r"]["items"][:300]:
            d = str(it.get("short_description") or "")
            if len(d) > 25:
                self.assertNotIn(d, text)
        names = set(f[2] for x in self.runs["runs"] for f in x["figures"])
        known = sum((list(v) for _n, v in self.D.FIGURE_SETS), []) + ["outcome", "aging_bdays", "category", "hot_ci",
                                                                      "survey_scale_max", "survey_questions"]    # 1.16.0: figures, in no view
        self.assertEqual(sorted(names - set(known)), [])

    def test_the_calendar_months(self):
        H = self.H
        runs = self.runs["runs"]
        self.assertEqual(runs[0]["run"]["period"], "6m")                                   # the window's own, first
        months = [x["run"] for x in runs[1:]]
        self.assertEqual([x["month"] for x in months], ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"])
        self.assertEqual(set((x["period"], x["partial"]) for x in months), {("month", False)})
        self.assertEqual((months[-1]["window_start"], months[-1]["window_end"]), ("2026-10-01 00:00:00", "2026-11-01 00:00:00"))
        inc = [dict(((f[0], f[1], f[2]), f[4]) for f in x["figures"] if not f[3]) for x in runs[1:]]
        s = self.m["scopes"]["all"]["counts"]
        self.assertEqual(sum(f[("all", "", "incidents")] for f in inc), s["incidents"])     # each incident in one month, never twice
        self.assertEqual(sum(f[("all", "", "requests")] for f in inc), s["requests"])
        self.assertEqual(inc[-1][("all", "", "backlog")], self.m["scopes"]["all"]["backlog"][0])   # open at October's end = at the window's
        # a window that ends inside a month: that month so far, partial, keyed by the whole month; a month cut at the start: left out
        w = dict(self.x["r"]["window"], since="2026-05-11 00:00:00", until="2026-10-20 00:00:00", time_zone="America/New_York")
        mw = H.month_windows(w)
        self.assertEqual([(x[0], x[3]) for x in mw], [("2026-06", False), ("2026-07", False), ("2026-08", False), ("2026-09", False), ("2026-10", True)])
        self.assertEqual(mw[0][1].strftime("%Y-%m-%d %H:%M"), "2026-06-01 04:00")                     # local midnight
        part = H.month_run(dict(self.x["r"], window=dict(self.x["r"]["window"], until="2026-10-20 00:00:00")), "2026-10", self.o)
        self.assertEqual((part["run"]["partial"], part["run"]["window_end"], part["run"]["data_end"]), (True, "2026-11-01 00:00:00", "2026-10-20 00:00:00"))
        self.assertEqual(H.servicenow_insights_backfill_months({"window": w}), ["2026-06", "2026-07", "2026-08", "2026-09"])

    def test_a_month_reads_only_what_touches_it(self):
        """The month's records are trimmed (opened before it ends, not done long before it starts): the figures the same."""
        H = self.H
        a = H.month_run(self.x["r"], "2026-07", self.o)
        keep = H._margin
        try:
            H._margin = lambda o: 100000.0
            b = H.month_run(self.x["r"], "2026-07", self.o)
        finally:
            H._margin = keep
        self.assertLess(a["tickets"]["items"], b["tickets"]["items"])
        self.assertEqual(sorted(a["figures"]), sorted(b["figures"]))

    def test_the_schema_and_the_writes(self):
        D = self.D
        self.assertEqual(len(D.OBJECTS), len(D.CREATE) + len(D.VIEWS))
        for name, sql in zip(D.OBJECTS, D.CREATE + D.VIEWS):
            self.assertIn(" %s " % name, sql)
            self.assertNotIn("OR REPLACE", sql)                       # made only when missing: no DROP grant
        self.assertIn("UNIQUE KEY run_key (source, period, window_end)", D.CREATE[1])
        self.assertIn("PRIMARY KEY (run_id, variant, node, metric, label)", D.CREATE[3])
        svc = [v for v in D.VIEWS if "insights_v_service" in v][0]
        for col in ("r.window_start AS period_start", "r.window_end AS period_end", "r.partial", "n.depth", "AS sla_met_pct", "AS median_resolve_h"):
            self.assertIn(col, svc)
        self.assertIn("PARTITION BY r.source, r.period, r.month ORDER BY r.window_end DESC", [v for v in D.VIEWS if "month_run" in v][0])

        class Cur(object):                    # what the module sends: a month kept complete, a partial one replaced
            def __init__(self, have):
                self.have, self.sql, self.lastrowid = have, [], 7

            def execute(self, sql, args=None):
                self.sql.append(sql)

            def executemany(self, sql, args):
                self.sql.append(sql)

            def fetchone(self):
                return self.have
        month = self.runs["runs"][-1]
        part = dict(month, run=dict(month["run"], partial=True))
        c = Cur((3, 0))
        self.assertEqual(D.write_run(c, part, "9"), (False, True, 0, 0, 0))                  # complete there: kept
        self.assertEqual(len(c.sql), 1)
        c = Cur((3, 1))
        replaced, skipped, nn, nf, _rid = D.write_run(c, month, "9")
        self.assertEqual((replaced, skipped, nn, nf), (True, False, len(month["nodes"]), len(month["figures"])))
        self.assertTrue(c.sql[1].startswith("UPDATE insights_run SET") and c.sql[2].startswith("DELETE FROM insights_figure"))
        c = Cur(None)
        self.assertEqual(D.write_run(c, part, "9")[:2], (False, False))
        self.assertIn("INSERT INTO insights_run", c.sql[1])

    def test_the_doc_has_the_statements(self):
        """docs/REPORT_DATABASE.md's "Making the tables by hand" is site_insights_db.ddl() word for word: every table and
        view the first run makes, and schema_version's row."""
        with open(os.path.join(HERE, "..", "docs", "REPORT_DATABASE.md")) as f:
            doc = f.read()
        mark = "<!-- the statements: site_insights_db.ddl(), word for word"
        block = doc[doc.index(mark):doc.index("<!-- end of the statements -->")]
        self.assertEqual("\n\n".join(re.findall(r"```sql\n(.*?)\n```", block, re.S)), self.D.ddl())
        for name in self.D.OBJECTS:
            self.assertEqual(self.D.ddl().count(" %s " % name) >= 1, True, name)
        self.assertIn("servicenow_insights_db_create: false", doc)
        self.assertEqual(self.D.pretty(self.D.CREATE[0]), "CREATE TABLE IF NOT EXISTS schema_version (\n  component VARCHAR(64) NOT NULL PRIMARY KEY,\n"
                         "  version INT NOT NULL,\n  applied_at DATETIME NOT NULL\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;")

    def test_the_trend(self):
        H = self.H
        # the database: Nov 2025 to Feb 2026 (January missing) and an older October; this run: May to October 2026
        t = H.servicenow_insights_trend(self.m, self.hist(["2025-11", "2025-12", "2026-02", "2026-10"]), {}, self.runs)["trend"]
        self.assertEqual(t["months"], ["2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07",
                                       "2026-08", "2026-09", "2026-10"])
        pts = t["nodes"]["all"]
        i = H.TREND_METRICS.index("sla_met_pct") + 1
        self.assertEqual([p[i] for p in pts[:5]], [90.0, 91.0, None, 92.0, None])
        mine = dict((x["run"]["month"], dict(((f[0], f[1], f[2]), f[4]) for f in x["figures"] if not f[3])) for x in self.runs["runs"][1:])
        self.assertEqual(pts[-1][i], mine["2026-10"][("all", "", "sla_met_pct")])      # this run's October, not the stored one
        self.assertEqual(t["nodes"]["in|all"][0][i], 90.0)
        self.assertEqual(t["partial"], [])
        # a short history: the months start where the figures start; the month so far flagged
        t = H.servicenow_insights_trend(self.m, self.hist(["2026-09", "2026-10"], partial=["2026-10"]), {})["trend"]
        self.assertEqual((t["months"], t["partial"]), (["2026-09", "2026-10"], ["2026-10"]))
        self.assertEqual(H.servicenow_insights_trend(self.m, {"ok": True, "rows": []}, {})["trend"]["months"], [])
        self.assertNotIn("trend", H.servicenow_insights_trend(self.m, None, {}))      # off: nothing
        off = H.servicenow_insights_trend(self.m, {"ok": False, "error": "Connection refused"}, {}, self.runs)["trend"]
        self.assertEqual((off["ok"], off["error"]), (False, "Connection refused"))

    def test_the_page(self):
        H = self.H
        links = page_links(self.m)
        m = H.servicenow_insights_trend(self.m, self.hist(["2026-06", "2026-07", "2026-09", "2026-10"], partial=["2026-10"]), {})
        h = P.servicenow_insights_page(m, "all", {"links": links, "look": "brief"})
        sec = h[h.index('<section class="card" id="trend"'):]
        sec = sec[:sec.index("</section>")]
        self.assertIn('<a href="#trend">Trends', h)
        self.assertLess(h.index('id="glance"'), h.index('id="trend"'))
        self.assertEqual(sec.count("<svg"), 2)                                              # two scales, never two axes on one
        self.assertIn('<div class="legend"><span><i style="background:var(--st1)"></i>SLA met</span>', sec)
        self.assertIn("<summary>The months as a table</summary>", sec)
        self.assertIn('data-t="Sep 2026&lt;br&gt;SLA met &lt;b&gt;92.0%', sec)                  # each month's values on hover
        self.assertIn("Aug 2026&lt;br&gt;SLA met &lt;b&gt;no run", sec)                         # a month without one: a gap
        self.assertIn('stroke-dasharray="5 4"', sec)                                          # into the month so far
        self.assertIn("Oct 2026 (so far)", sec)
        self.assertIn("4 months so far · each point a calendar month · dashed: the month so far", sec)
        self.assertEqual(sec.count("<tr>") - 1, 4)
        self.assertIn('id="trend"', P.servicenow_insights_page(m, "site:SITEA", {"links": links}))
        twelve = H.servicenow_insights_trend(self.m, self.hist(["2025-%02d" % k for k in range(11, 13)] + ["2026-%02d" % k for k in range(1, 11)]), {})
        self.assertIn("the last 12 months · each point a calendar month", P.servicenow_insights_page(twelve, "all", {"links": links}))
        one = P.servicenow_insights_page(H.servicenow_insights_trend(self.m, self.hist(["2026-10"]), {}), "all", {"links": links})
        sec1 = one[one.index('id="trend"'):]
        self.assertIn("1 month so far: the line starts next month.", sec1)
        self.assertIn('<span class="eyebrow">SLA met</span><span class="v">90.0%</span>', sec1)
        self.assertNotIn("<svg", sec1[:sec1.index("</section>")])
        none = P.servicenow_insights_page(H.servicenow_insights_trend(self.m, {"ok": True, "rows": []}, {}), "all", {"links": links})
        self.assertNotIn('id="trend"', none)                                                   # no month: nothing
        bad = P.servicenow_insights_page(H.servicenow_insights_trend(self.m, {"ok": False, "error": "Can't connect <x>"}, {}), "all", {"links": links})
        self.assertIn("The report database was not reached this run: Can&#x27;t connect &lt;x&gt;.", bad)
        self.assertNotIn('id="trend"', P.servicenow_insights_page(self.m, "all", {"links": links}))

    def test_act_off_said_once(self):
        """1.13.0: ACT is off by default - the page says so once, with how to turn it on; not when it ran."""
        with open(os.path.join(HERE, "..", "roles", "servicenow_insights", "defaults", "main.yml")) as f:
            self.assertIn("\nservicenow_insights_act: false\n", f.read())
        h = P.servicenow_insights_page(self.m, "all", {"links": page_links(self.m)})
        self.assertEqual(h.count("ACT is off: the issues are grouped by category, the fixes come from the close notes."), 1)
        self.assertIn("<code>servicenow_insights_act: true</code>", h)
        on = dict(self.m, act=dict(self.m["act"], on=True, ran=True))
        self.assertNotIn("ACT is off", P.servicenow_insights_page(on, "all", {"links": page_links(self.m)}))

    def test_the_tasks(self):
        """Read before the pages (the trend is on them), written before them too since 1.15.0 (each page says it was
        written); not written when not read; Check writes nothing; the backfill before the normal read, ending the play; the
        check (1.15.0) before the ServiceNow credential is asked for, ending the play."""
        def read(*p):
            with open(os.path.join(HERE, "..", *p)) as f:
                return f.read()
        main = read("roles", "servicenow_insights", "tasks", "main.yml")
        self.assertLess(main.index("include_tasks: db.yml"), main.index("servicenow_insights_page('all'"))
        self.assertLess(main.index("include_tasks: db.yml"), main.index("include_tasks: db_write.yml"))
        self.assertLess(main.index("include_tasks: db_write.yml"), main.index("servicenow_insights_db_status"))
        self.assertLess(main.index("servicenow_insights_db_status"), main.index("servicenow_insights_page(_sni_team"))
        self.assertLess(main.index("include_tasks: db_check.yml"), main.index("the ServiceNow API credential is attached"))
        self.assertLess(main.index("(_sni_t | servicenow_insights_timing).line"), main.index("msg: \"{{ _sni_db_say.line }}\""))
        self.assertEqual(main.count("'db': _sni_db_say if servicenow_insights_db | bool else {}"), 3)   # every page's opts
        chk = read("roles", "servicenow_insights", "tasks", "db_check.yml")
        self.assertIn("mode: check", chk)
        self.assertIn("create: false", chk)
        self.assertIn("meta: end_play", chk)
        self.assertLess(main.index("the settings the report uses"), main.index("backfill.yml"))
        self.assertLess(main.index("backfill.yml"), main.index("Insights | read the window"))
        db = read("roles", "servicenow_insights", "tasks", "db.yml") + read("roles", "servicenow_insights", "tasks", "db_write.yml")
        self.assertIn("when: _sni_hist.ok | default(false) | bool and not ansible_check_mode", db)
        self.assertIn("lookup('ansible.builtin.env', 'REPORT_DB_PASSWORD')", db)
        bf = read("roles", "servicenow_insights", "tasks", "backfill.yml")
        for t in ("compare: false", "'month_start'", "servicenow_insights_month_run(_sni_bf_month, _sni_opts)", "required: true",
                  "ansible.builtin.meta: end_play"):
            self.assertIn(t, bf)
        self.assertNotIn("site_act", bf)
        self.assertNotIn("site_email", bf)
        import yaml
        ct = yaml.safe_load(read("aap", "credential_types", "report_database.yml"))
        self.assertEqual(sorted(ct["injectors"]["env"]), ["REPORT_DB_CA_FILE", "REPORT_DB_HOST", "REPORT_DB_NAME", "REPORT_DB_PASSWORD",
                                                         "REPORT_DB_PORT", "REPORT_DB_TLS", "REPORT_DB_USER"])
        fields = dict((f["id"], f) for f in ct["inputs"]["fields"])
        self.assertEqual((fields["port"]["default"], fields["database"]["default"], fields["username"]["default"], fields["use_tls"]["default"]),
                         ("3309", "site_reports", "report_writer", False))
        self.assertTrue(fields["password"]["secret"])
        w = rd.window(3, 0, "month_start", False, "UTC", datetime.datetime(2026, 11, 10, 9, 0, tzinfo=rd.UTC))
        self.assertEqual((w["since"], w["until"]), ("2026-08-01 00:00:00", "2026-11-01 00:00:00"))

class PagesInOneGo(unittest.TestCase):
    """1.14.0: every site's, unit's and team's page made in one filter call (servicenow_insights_pages) - the same page,
    report and links the per-page run made - written by site_report_web's pages, many per call; the run's time by phase
    (servicenow_insights_timing) in the job output and How this was made."""

    @classmethod
    def setUpClass(cls):
        x = site(sites=True)
        cls.o = dict(PARENTS_OPTS, split_label="BLUE", split_pattern="*- nova")
        cls.m = F.servicenow_insights_model(x["r"], None, x["mask"], cls.o)
        pages = dict((p["key"], {"page": "t/%s/latest.html" % "/".join(p["path"]), "latest": "", "index": "", "level": p["level"]})
                     for p in cls.m["pages"])
        variants = dict((v, dict((k, dict(f, page=f["page"].replace(".html", "-%s.html" % v))) for k, f in
                                 list(pages.items()) + [("all", {"page": "t/latest.html"})])) for v in ("in", "out"))
        cls.web = {"pages": pages, "teams": pages, "variants": variants, "all": {"page": "t/latest.html", "latest": "t/latest.html"},
                   "jobs": [{"key": k, "v": ""} for k in pages] + [{"key": k, "v": v} for v in variants for k in variants[v]]}

    def test_the_same_pages(self):
        jobs = self.web["jobs"]
        base = dict(self.o, generated="2026-11-01 06:00 UTC", job="7")
        got = P.servicenow_insights_pages(self.m, jobs, self.web, base, {"doc_opts": self.o, "logo_img": "<img x>", "act_off": False})
        self.assertEqual(len(got), len(jobs))
        for j, g in zip(jobs, got):
            f = (self.web["variants"][j["v"]] if j["v"] else self.web["pages"])[j["key"]]
            want = P.servicenow_insights_page(self.m, j["key"], dict(base, links=F.web_links(self.web, f["page"], j["v"]), variant=j["v"],
                                                                     variant_links=F.variant_links(self.web, j["key"], f["page"])))
            self.assertEqual(g["page"], f["page"])
            self.assertEqual(g["html"], want.replace("<!--site-report-logo-->", "<img x>"))
            self.assertEqual(g["report"], F.servicenow_insights_doc(F.servicenow_insights_variant(self.m, j["v"]), j["key"], self.o))
            self.assertEqual(g["runs"], [])
        team = [g for j, g in zip(jobs, got) if j["v"] == "in"][0]
        self.assertIn('aria-current="page">BLUE</a>', team["html"])                       # a split page knows its half
        off = P.servicenow_insights_pages(self.m, jobs[:1], self.web, base, {"doc_opts": self.o, "act_off": True,
                                                                             "runs_past": [], "runs_n": 5, "runs_top": 36})[0]
        self.assertEqual(off["report"]["sections"][-1]["title"], "ACT")
        self.assertEqual(len(off["runs"]), 1)                                              # this run's bar
        self.assertEqual([len(c) for c in P.servicenow_insights_chunks(list(range(85)), 40)], [40, 40, 5])
        self.assertEqual(P.servicenow_insights_chunks([], 40), [])

    def test_written_in_one_call(self):
        """site_report_web's pages: each page and its JSON (its own report and runs, the page's address), no index, no
        main page; the pages' script once beside them."""
        d = tempfile.mkdtemp()
        base = dict(self.o, generated="2026-11-01 06:00 UTC")
        pages = P.servicenow_insights_pages(self.m, self.web["jobs"][:3], self.web, base, {"doc_opts": self.o})
        args = {"dest": d, "url": "https://web.example.mil/r", "files": {"page": "", "latest": "", "index": ""}, "pages": pages,
                "entry": {"key": ""}, "data": {"job": "7", "template": "t", "banner": {}}, "theme": "brief", "retries": 0}
        argf = os.path.join(tempfile.mkdtemp(), "args.json")          # as Ansible hands them over: a file
        with open(argf, "w") as f:
            json.dump({"ANSIBLE_MODULE_ARGS": args}, f)
        # the module run as Ansible runs it: its arguments in a file, the repo's module_utils as ansible.cfg gives them
        boot = ("import importlib.util, os, runpy, sys\n"
                "args, module, utils = sys.argv[1:4]\n"
                "for n in ('site_aap', 'site_report_page', 'site_banner'):\n"
                "    sp = importlib.util.spec_from_file_location('ansible.module_utils.' + n, os.path.join(utils, n + '.py'))\n"
                "    m = importlib.util.module_from_spec(sp)\n"
                "    sys.modules['ansible.module_utils.' + n] = m\n"
                "    sp.loader.exec_module(m)\n"
                "sys.argv = ['m', args]\n"
                "runpy.run_path(module, run_name='__main__')\n")
        out = subprocess.run([sys.executable, "-c", boot, argf, os.path.join(HERE, "..", "roles", "site_email", "library", "site_report_web.py"),
                              os.path.join(HERE, "..", "plugins", "module_utils")], capture_output=True, text=True)
        res = json.loads(out.stdout)
        self.assertTrue(res.get("published"), out.stdout[-500:] + out.stderr[-500:])
        for pg in pages:
            with open(os.path.join(d, pg["page"])) as f:
                h = f.read()
            self.assertIn("<title>", h)
            self.assertIn("site-insights.js", h)
            with open(os.path.join(d, pg["page"][:-5] + ".json")) as f:
                doc = json.load(f)
            self.assertEqual((doc["page"], doc["url"], doc["latest"], doc["index"]), (pg["page"], "https://web.example.mil/r/" + pg["page"], "", ""))
            self.assertEqual((doc["report"]["title"], doc["job"]), (pg["report"]["title"], "7"))
        self.assertTrue(os.path.exists(os.path.join(d, "site-insights.js")))
        self.assertFalse(os.path.exists(os.path.join(d, "index.html")))
        self.assertEqual(len([w for w in res["written"] if w.endswith(".html")]), 3)

    def test_the_time(self):
        t = {"start": 0, "read": 600, "act_a": 900, "model": 960, "act_b": 1020, "finish": 1030, "db": 1040}
        r = F.servicenow_insights_timing(t)
        self.assertEqual(r["rows"], [["ServiceNow reads", 600.0], ["ACT", 360.0], ["The report worked out", 70.0],
                                     ["The report database (read and written)", 10.0]])
        self.assertEqual(r["line"], "Time: ServiceNow reads 10.0 min, ACT 6.0 min, The report worked out 70 s, "
                                    "The report database (read and written) 10 s - 17.3 min in all")
        r = F.servicenow_insights_timing(dict(t, pages=1640, end=1650))
        self.assertEqual(r["rows"][-1:], [["Pages and email", 600.0]])
        self.assertEqual(r["total"], 1650.0)
        self.assertEqual(F.servicenow_insights_timing({})["line"], "Time: not measured")
        h = P.servicenow_insights_page(self.m, "all", {"timing": F.servicenow_insights_timing(t)["rows"]})
        self.assertIn("<b>Time this run</b> <span class=\"mut\">17.3 min before the pages</span>", h)
        self.assertIn("<dt>ACT</dt><dd>6.0 min</dd>", h)
        self.assertNotIn("Time this run", P.servicenow_insights_page(self.m, "all", {}))

    def test_the_tasks(self):
        def read(*p):
            with open(os.path.join(HERE, "..", *p)) as f:
                return f.read()
        main = read("roles", "servicenow_insights", "tasks", "main.yml")
        self.assertLess(main.index("servicenow_insights_page('all'"), main.index("tasks_from: pages.yml"))        # after the All page
        self.assertIn("report_web_copy | default(false) | bool and report_web_host | default('', true)", main)
        # the leads' emails after the pages, linking them; the lead's address beats report_email_to (an extra variable too)
        self.assertLess(main.index("tasks_from: pages.yml"), main.index("each team lead's email (servicenow_insights_email_leads, servicenow_insights_team_email_to), linking"))
        self.assertEqual(main.count("_report_email_to_override:"), 2)
        self.assertNotIn("    report_email_to:", main)
        self.assertIn("_report_web_url: >-", main)
        email = read("roles", "site_email", "tasks", "main.yml")
        self.assertIn("_report_email_to_override | email_recipients([], report_email_also_domains) if _report_email_to_override is defined", email)
        self.assertIn("{{ _report_web_url if _report_web_url | default('') | length > 0 and not ansible_check_mode else", email)
        for cp in ("'start'", "'read'", "'act_a'", "'model'", "'act_b'", "'finish'", "'db'", "'pages'", "'end'"):
            self.assertIn("combine({%s: now().timestamp()})" % cp, main)
        pages = read("roles", "site_email", "tasks", "pages.yml")
        self.assertIn("servicenow_insights_pages(_pages_chunk", pages)
        self.assertIn("not _web_copy | default(false) | bool and not _web_win | default(false) | bool", pages)

class TeamLeads(unittest.TestCase):
    """1.15.0: each group's manager in ServiceNow (sys_user_group.manager, dot-walked in the groups' own read) - "Group manager:
    NAME" on the team's page and email (servicenow_insights_show_lead), the team's page emailed to the lead
    (servicenow_insights_email_leads: names, patterns, all; servicenow_insights_team_email_to wins); never in the history."""

    class Api(object):
        def __init__(self, groups, deny_manager=False, hide_person=False):
            self.calls, self.groups, self.deny, self.hide, self.url = [], groups, deny_manager, hide_person, "https://sn.example.mil"

        def get(self, path, params):
            self.calls.append(params["sysparm_fields"])
            if self.deny and "manager" in params["sysparm_fields"]:
                raise rd.SnError("GET %s: HTTP 403: ACL Exception: manager" % path)
            v = lambda x: {"value": x, "display_value": x}                       # noqa: E731
            want = params["sysparm_query"][8:].split(",")
            out = []
            for g in self.groups:
                if g["sys_id"] not in want:
                    continue
                row = dict((k, v(g.get(k, ""))) for k in ("sys_id", "name", "parent", "active", "type"))
                if "manager" in params["sysparm_fields"]:
                    m = g.get("m") or {}
                    row.update({"manager": v(m.get("id", "")), "manager.name": v("" if self.hide else m.get("name", "")),
                                "manager.active": v(m.get("active", "true"))})
                    if "manager.email" in params["sysparm_fields"]:       # only the fields asked, as ServiceNow
                        row["manager.email"] = v("" if self.hide else m.get("email", ""))
                out.append(row)
            return {"result": out}

    GROUPS = [{"sys_id": "g1", "name": "Ops - Windows", "parent": "g0", "active": "true", "m": {"id": "u1", "name": "Pat Lee", "email": "pat.lee@example.mil"}},
              {"sys_id": "g2", "name": "Ops - Network", "parent": "g0", "active": "true", "m": {"id": "u2", "name": "Sam Ray", "email": "", "active": "true"}},
              {"sys_id": "g3", "name": "Ops - Linux", "parent": "g0", "active": "true", "m": {"id": "u3", "name": "Kim Old", "email": "kim@example.mil",
                                                                                               "active": "false"}},
              {"sys_id": "g0", "name": "Ops", "parent": "", "active": "true", "m": {"id": "u9", "name": "Top Boss", "email": "boss@example.mil"}}]

    def test_read_with_the_groups(self):
        api = self.Api(self.GROUPS)
        mgr = {}
        tree, err = rd.read_group_tree(api, ["g1", "g2", "g3"], managers=mgr)
        self.assertEqual((err, sorted(tree)), ("", ["g0", "g1", "g2", "g3"]))
        self.assertEqual(len(api.calls), 2)                                        # the two levels, no call per group
        self.assertTrue(all(c.endswith(",manager,manager.name,manager.active,manager.email") for c in api.calls), api.calls)
        got, err = rd.manager_names(mgr, ["g1", "g2", "g3"], {"g1": "Ops - Windows", "g2": "Ops - Network", "g3": "Ops - Linux"})
        self.assertEqual(err, "")
        self.assertEqual(got, {"Ops - Windows": {"name": "Pat Lee", "email": "pat.lee@example.mil", "active": True},
                               "Ops - Network": {"name": "Sam Ray", "email": "", "active": True},
                               "Ops - Linux": {"name": "Kim Old", "email": "kim@example.mil", "active": False}})   # the parent's not asked
        # without the managers: never a person's field
        api = self.Api(self.GROUPS)
        rd.read_group_tree(api, ["g1"])
        self.assertTrue(all("manager" not in c for c in api.calls))
        # 1.18.0: the name without the email - nobody's leads to email
        api, mgr = self.Api(self.GROUPS), {}
        rd.read_group_tree(api, ["g1"], managers=mgr, emails=False)
        self.assertTrue(api.calls and all("manager.name" in c and "email" not in c for c in api.calls), api.calls)
        got, err = rd.manager_names(mgr, ["g1"], {"g1": "Ops - Windows"})
        self.assertEqual((got, err), ({"Ops - Windows": {"name": "Pat Lee", "email": "", "active": True}}, ""))

    def test_not_readable(self):
        api = self.Api(self.GROUPS, deny_manager=True)                            # the manager refused: the groups still read
        mgr = {}
        tree, err = rd.read_group_tree(api, ["g1"], managers=mgr)
        self.assertEqual((err, sorted(tree)), ("", ["g0", "g1"]))
        got, err = rd.manager_names(mgr, ["g1"], {"g1": "Ops - Windows"})
        self.assertEqual(got, {})
        self.assertIn("HTTP 403", err)
        self.assertIn("the groups were read without them", err)
        mgr = {}
        rd.read_group_tree(self.Api(self.GROUPS, hide_person=True), ["g1"], managers=mgr)   # sys_user hidden: names empty
        got, err = rd.manager_names(mgr, ["g1"], {"g1": "Ops - Windows"})
        self.assertEqual(got, {})
        self.assertIn("needs read on sys_user", err)

    def model(self, managers=None, err="", **o):
        r = dict(site()["r"], managers=managers if managers is not None else self.leads(), managers_error=err)
        return F.servicenow_insights_model(r, None, site()["mask"], dict(OPTS, **o))

    def leads(self):
        teams = [p["key"] for p in site()["model"]["pages"] if p["level"] == "team"]
        self.__class__.teams = teams
        return {teams[0]: {"name": "Pat Lee", "email": "pat.lee@example.mil", "active": True},
                teams[1]: {"name": "Kim Old", "email": "kim@example.mil", "active": False},
                teams[2]: {"name": "Sam Ray", "email": "", "active": True}}

    def test_on_the_page_and_email(self):
        m = self.model()
        t0, t1 = self.teams[0], self.teams[1]
        h = P.servicenow_insights_page(m, t0, OPTS)
        self.assertIn('<div class="sub lead">Group manager: <b>Pat Lee</b></div>', h)
        self.assertLess(h.index("<h1>"), h.index("Group manager:"))
        self.assertLess(h.index("Group manager:"), h.index("</header>"))              # near the title
        self.assertIn("Group manager: <b>Kim Old (inactive in ServiceNow)</b>", P.servicenow_insights_page(m, t1, OPTS))
        doc = F.servicenow_insights_doc(m, t0, OPTS)
        self.assertTrue(doc["subtitle"].endswith(" · Group manager: Pat Lee"), doc["subtitle"])
        nolead = [t for t in self.teams if t not in self.leads()][0]                # no manager: nothing
        self.assertNotIn("Group manager", P.servicenow_insights_page(m, nolead, OPTS))
        self.assertNotIn("Group manager", F.servicenow_insights_doc(m, nolead, OPTS)["subtitle"])
        for k in [p["key"] for p in m["pages"] if p["level"] != "team"] + ["all"]:  # the sites' and directorates' pages: nothing
            self.assertNotIn("Group manager", P.servicenow_insights_page(m, k, OPTS), k)
        off = self.model(show_lead=False)
        self.assertNotIn("Group manager", P.servicenow_insights_page(off, t0, OPTS))
        self.assertNotIn("Group manager", F.servicenow_insights_doc(off, t0, OPTS)["subtitle"])

    def test_split_and_map(self):
        x = site(sites=True)
        teams = [p["key"] for p in x["model"]["pages"] if p["level"] == "team"]
        r = dict(x["r"], managers={teams[0]: {"name": "Pat Lee", "email": "p@example.mil", "active": True}})
        m = F.servicenow_insights_model(r, None, x["mask"], dict(PARENTS_OPTS, split_label="BLUE", split_pattern="*"))
        self.assertIn("Group manager: <b>Pat Lee</b>", P.servicenow_insights_page(m, teams[0], dict(PARENTS_OPTS, variant="in")))
        omap = dict(OPTS, hierarchy_source="map", hierarchy_map={teams[0]: ["SITEA", "Ops"]})
        m = F.servicenow_insights_model(r, None, x["mask"], omap)
        tp = [p for p in m["pages"] if p["level"] == "team" and p["key"] == teams[0]]
        self.assertTrue(tp)
        self.assertIn("Group manager: <b>Pat Lee</b>", P.servicenow_insights_page(m, teams[0], omap))
        for p in m["pages"]:
            if p["level"] != "team":
                self.assertNotIn("Group manager", P.servicenow_insights_page(m, p["key"], omap), p["key"])

    def test_not_read_said_once(self):
        m = self.model(managers={}, err="GET api/now/table/sys_user_group: HTTP 403: ACL - the groups were read without them")
        nr = [x for x in F.not_read_lines(m) if "team leads" in x]
        self.assertEqual(len(nr), 1)
        self.assertIn("sys_user_group.manager (the team leads): GET", nr[0])
        h = P.servicenow_insights_page(m, "all", dict(OPTS, not_read=F.not_read_lines(m)))
        self.assertEqual(h.count("the team leads"), 1)
        got = F.servicenow_insights_lead_mail(m, "all", {})
        self.assertEqual(got["to"], {})
        self.assertEqual(len(got["lines"]), 1)
        self.assertIn("Team leads not emailed: the managers were not read", got["lines"][0])

    def test_who_is_emailed(self):
        m = self.model()
        t = self.teams
        self.assertEqual(F.servicenow_insights_lead_mail(m, [], {}), {"to": {}, "lines": []})       # the default: nobody
        got = F.servicenow_insights_lead_mail(m, "all", {})
        self.assertEqual(got["to"], {t[0]: ["pat.lee@example.mil"]})
        self.assertIn("%s: not emailed - the manager Kim Old is inactive" % t[1], got["lines"])
        self.assertIn("%s: not emailed - the manager Sam Ray has no email address" % t[2], got["lines"])
        self.assertIn("%s: not emailed - no manager in ServiceNow" % t[3], got["lines"])
        pat = t[0][:3].upper() + "*"                                               # a pattern, any case
        got = F.servicenow_insights_lead_mail(m, [pat], {})
        self.assertIn(t[0], got["to"])
        self.assertEqual(F.servicenow_insights_lead_mail(m, [t[0].lower()], {})["to"], {t[0]: ["pat.lee@example.mil"]})
        # named in both: servicenow_insights_team_email_to wins; one of its teams the pattern does not name: kept
        got = F.servicenow_insights_lead_mail(m, [t[0], t[1]], {t[0]: ["other@example.mil"], t[4]: "x@example.mil"})
        self.assertEqual(got["to"], {t[0]: ["other@example.mil"], t[4]: ["x@example.mil"]})
        self.assertEqual(got["lines"], ["%s: not emailed - the manager Kim Old is inactive" % t[1]])
        got = F.servicenow_insights_lead_mail(m, ["No such team*"], {})
        self.assertEqual(got["lines"], ["Team leads: servicenow_insights_email_leads matches no team page (no such team*)"])
        # the shown line off: the emails still go
        self.assertEqual(F.servicenow_insights_lead_mail(self.model(show_lead=False), "all", {})["to"], {t[0]: ["pat.lee@example.mil"]})

    def test_the_workbook_column(self):
        """1.18.0: Manager on the Teams sheet, as the team page shows it; empty without one or with the line off. The site's
        list of teams on the page has no Manager column: it does not fit at 1280 px (the line stays on each team's page)."""
        x, m = site(), self.model()
        t = self.teams
        def sheet(mm, o):
            wb = F.servicenow_insights_xlsx(mm, x["r"], None, x["mask"], dict(OPTS, **o))
            sh = [w for w in wb if w["name"] == "Teams"][0]
            i = sh["columns"].index("Manager")
            self.assertEqual(sh["columns"][i - 1], "Team")
            return dict((row[i - 1], row[i]) for row in sh["rows"])
        got = sheet(m, {})
        self.assertEqual(got[t[0]], "Pat Lee")
        self.assertEqual(got[t[1]], "Kim Old (inactive in ServiceNow)")
        self.assertEqual(got[t[3]], "")
        self.assertEqual(set(sheet(self.model(show_lead=False), {"show_lead": False}).values()), {""})
        self.assertNotIn("<th>Manager</th>", P.servicenow_insights_page(m, "all", OPTS))

    def test_never_in_the_history(self):
        import servicenow_insights_history as H
        m = self.model()
        rows = json.dumps(H.servicenow_insights_db_rows(m, OPTS))
        for nm in ("Pat Lee", "Kim Old", "Sam Ray", "pat.lee@example.mil"):
            self.assertNotIn(nm, rows)
        self.assertNotIn("Pat Lee", json.dumps(F.servicenow_insights_artifact(m)))

    def test_the_tasks(self):
        with open(os.path.join(HERE, "..", "roles", "servicenow_insights", "tasks", "main.yml")) as f:
            main = f.read()
        self.assertIn("managers: \"{{ servicenow_insights_show_lead | bool or servicenow_insights_email_leads", main)
        self.assertIn("manager_email: \"{{ servicenow_insights_email_leads | default([], true) | length > 0 }}\"", main)    # 1.18.0
        self.assertEqual(main.count("_sni_leads.to[_sni_team] | default([])"), 3)  # both ways: the per-page loop and after pages.yml
        self.assertNotIn("servicenow_insights_team_email_to[", main)
        self.assertLess(main.index("servicenow_insights_lead_mail("), main.index("tasks_from: pages.yml"))


class IsItWriting(unittest.TestCase):
    """1.15.0: the report database's one line - written (read back) or why not - in the job and on the page; the check's lines."""

    W = {"ok": True, "run_id": 41, "months": ["2026-10", "2026-11 (so far)"], "written": {"runs": 3, "nodes": 30, "figures": 4210},
         "held": [{"source": "sn.example.mil", "period": "month", "runs": 13, "months": 13, "complete": 12, "first": "2025-11", "last": "2026-11", "newest": "x"},
                  {"source": "sn.example.mil", "period": "6m", "runs": 25, "months": 13, "complete": 13, "first": "2025-11", "last": "2026-10", "newest": "x"},
                  {"source": "other.example.mil", "period": "month", "runs": 4, "months": 4, "complete": 4, "first": "2024-01", "last": "2024-04", "newest": "x"}]}
    BASE = {"on": True, "host": "db.example.mil", "port": 3309, "database": "site_reports", "check": False, "read": {"ok": True},
            "source": "sn.example.mil", "not_read": ""}

    def setUp(self):
        import servicenow_insights_history as H
        self.H = H

    def test_written(self):
        r = self.H.servicenow_insights_db_status(dict(self.BASE, write=self.W))
        self.assertTrue(r["written"])
        self.assertEqual(r["line"], "Report database db.example.mil:3309/site_reports: written - run 41, 2 months (2026-10, 2026-11 (so far)), "
                                    "4,210 figures; the database now holds 38 runs, first month 2025-11, last month 2026-11")
        self.assertNotIn("db.example.mil", r["page"])                               # the page: without the host
        self.assertIn("The database holds 38 runs of this instance, months 2025-11 to 2026-11.", r["page"])

    def test_why_not(self):
        S = self.H.servicenow_insights_db_status
        self.assertEqual(S(dict(self.BASE, on=False))["line"], "Report database db.example.mil:3309/site_reports: NOT written - servicenow_insights_db is false")
        self.assertEqual(S(dict(self.BASE, host=""))["line"], "Report database: NOT written - no Report database credential on the job template")
        self.assertEqual(S(dict(self.BASE, check=True, write={"skipped": True}))["line"].split(" - ", 1)[1], "Check mode")
        self.assertEqual(S(dict(self.BASE, read={"ok": False, "error": "Can't connect (MySQL error 2003)"}, write={"skipped": True}))["line"].split(" - ", 1)[1],
                         "not reached or refused: Can't connect (MySQL error 2003)")
        self.assertEqual(S(dict(self.BASE, write={"ok": False, "error": "INSERT command denied"}))["line"].split(" - ", 1)[1], "refused: INSERT command denied")
        self.assertEqual(S(dict(self.BASE, not_read="ServiceNow was not read"))["page"], "Not written this run: ServiceNow was not read.")

    def test_on_the_page(self):
        m = site()["model"]
        r = self.H.servicenow_insights_db_status(dict(self.BASE, write=self.W))
        h = P.servicenow_insights_page(m, "all", dict(OPTS, db=r))
        self.assertIn('<p class="dbline"><b>Report database.</b> Written this run: run 41', h)
        self.assertLess(h.index('id="method"'), h.index("Report database.</b>"))  # under How this was made
        no = self.H.servicenow_insights_db_status(dict(self.BASE, host=""))
        self.assertIn('<div class="callout"><b>Report database.</b> Not written this run: no Report database credential', P.servicenow_insights_page(m, "all", dict(OPTS, db=no)))
        self.assertNotIn("Report database.</b>", P.servicenow_insights_page(m, "all", OPTS))

    def test_the_check(self):
        L = self.H.servicenow_insights_db_check_lines
        res = {"ok": True, "version": "8.4.3", "tls": "none", "account": {"name": "report_reader@%", "can_write": False}, "schema_version": 1,
               "objects": {"present": ["a"] * 12, "missing": []}, "held": self.W["held"][:1]}
        got = L(res, "db.example.mil:3309/site_reports")
        self.assertEqual(got[0], "Report database db.example.mil:3309/site_reports: reached - MySQL 8.4.3, TLS none, as report_reader@% (reads only)")
        self.assertEqual(got[1], "Tables and views: all 12 there, schema version 1")
        self.assertEqual(got[2], "Held: sn.example.mil, period month - 13 runs, 13 months (12 complete), 2025-11 to 2026-11, newest written x")
        got = L(dict(res, objects={"present": [], "missing": ["insights_run"]}, held=[]), "h")
        self.assertEqual(got[1:], ["Tables and views: 0 there, missing insights_run", "Held: nothing yet - no run has been written"])
        self.assertEqual(L({"ok": False, "error": "Access denied"}, "h"), ["Report database h: NOT reached - Access denied"])


if __name__ == "__main__":
    unittest.main()
