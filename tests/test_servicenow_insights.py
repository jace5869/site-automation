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
                                            "user confirmed", "completed"], "act": False, "kb_min": 50, "hierarchy_source": "names"}
_SITE = {}
_SITES = {}
HIER_OPTS = dict(OPTS, directorates=["CODEX"], suffix_directorates=["NOVA"])                      # the names (OPTS: hierarchy_source names)
PARENTS_OPTS = dict(OPTS, hierarchy_source="parents")


def site(sites=False):
    """The made-up site (insights_seed.py), read as the module reads it - once. sites: its groups named as three sites with
    directorates (--sites, 0.97.0)."""
    _SITE = _SITES if sites else globals()["_SITE"]
    if not _SITE:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "seed.json")
            subprocess.check_call([sys.executable, os.path.join(HERE, "servicenow", "insights_seed.py"), path, "0.4"] + (["--sites"] if sites else []),
                                  stdout=subprocess.DEVNULL)
            with open(path) as fh:
                seed = json.load(fh)
        win = rd.window(6, 0, "2026-10-31", True, "", NOW)
        since, until = rd.sn_time(win["prev_since"]), rd.sn_time(win["until"])
        items, audit, members, notes, gtree = [], {}, {}, {}, {}
        for r in seed:
            t = r.get("_table")
            if t == "metric_instance":
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
            it = rd.normalize(dict((k, {"value": v, "display_value": v}) for k, v in r.items() if not k.startswith("_")), t)
            if rd.keep(it, since, until):
                items.append(it)
        _SITE["r"] = {"ok": True, "window": win, "items": items, "audit": audit, "members": members, "groups": [], "url": "https://sn.example.mil",
                      "group_tree": gtree,
                      "tables": [{"table": "incident", "read": 10, "kept": 10, "more": False, "error": ""}]}
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
        links = page_links(m)
        h = P.servicenow_insights_page(m, "all", {"links": links, "generated": "2026-11-01 06:05 UTC", "job": "7"})
        for sid in ("glance", "teams", "keepingup", "severity", "issues", "problems", "handoffs", "requests", "changes", "notes", "allstars", "kb", "method"):
            self.assertIn('id="%s"' % sid, h)
        self.assertIn('href="ops/other/windows/latest.html"', h)
        self.assertIn("https://sn.example.mil/incident.do?sysparm_query=number=INC", h)
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
        self.assertEqual(h.count("<script"), 2)                  # the file's tag and the page's own copy, nothing else
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

if __name__ == "__main__":
    unittest.main()
