#!/usr/bin/env python3
"""Unit tests for the weekly activity report: the pure parts of roles/servicenow_war/library/site_servicenow_war.py (the
week, the query, a record's dates and verdicts, the reads page by page, the lookups) and plugins/filter/servicenow_war_filters.py
(the report, the lines to paste, the workbook, ACT's evidence, question and answer). Needs ansible-core importable.
    python3 tests/test_servicenow_war.py"""
import datetime
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
sys.path.insert(0, os.path.join(HERE, "..", "roles", "servicenow_war", "library"))
import servicenow_war_filters as wf  # noqa: E402
import site_servicenow_war as war  # noqa: E402

NOW = datetime.datetime(2026, 10, 23, 20, 0, tzinfo=war.UTC)       # a Friday evening (UTC)
SINCE, UNTIL = war.sn_time("2026-10-16 20:00:00"), war.sn_time("2026-10-23 20:00:00")


def rec(table, number, opened, closed="", resolved="", updated=None, state=("2", "In Progress"), who=("u2", "Jane Doe"), group="Linux Operations", **more):
    def vd(v, d=None):
        return {"value": v, "display_value": v if d is None else d}
    r = {"sys_id": vd(number.lower()), "number": vd(number), "sys_class_name": vd(table, table.title()), "short_description": vd("Work on " + number),
         "state": vd(state[0], state[1]), "priority": vd("3", "3 - Moderate"), "assigned_to": vd(who[0], who[1]), "assignment_group": vd("g1", group),
         "opened_at": vd(opened), "closed_at": vd(closed), "resolved_at": vd(resolved), "sys_updated_on": vd(updated or closed or resolved or opened),
         "sys_created_on": vd(opened), "close_notes": vd("done"), "active": vd("true" if not (closed or resolved) else "false"),
         "cmdb_ci": vd("ci1", "web01.example.mil"), "opened_by": vd("u1", "AAP Service"), "parent": vd("")}
    for k, v in more.items():
        r[k] = vd(v)
    return r


class Window(unittest.TestCase):
    def test_last_days(self):
        w = war.window("days", 7, now=NOW)
        self.assertEqual((w["since"], w["until"], w["days"]), ("2026-10-16 20:00:00", "2026-10-23 20:00:00", 7.0))
        self.assertEqual(w["label"], "2026-10-16 20:00 to 2026-10-23 20:00 (UTC)")

    def test_last_week_and_this_week(self):
        w = war.window("last-week", now=NOW)
        self.assertEqual((w["since"], w["until"], w["label"]), ("2026-10-12 00:00:00", "2026-10-19 00:00:00", "2026-10-12 to 2026-10-18 (UTC)"))
        w = war.window("this-week", now=NOW)
        self.assertEqual((w["since"], w["until"]), ("2026-10-19 00:00:00", "2026-10-23 20:00:00"))
        w = war.window("last-week", week_start="friday", now=NOW)
        self.assertEqual(w["label"], "2026-10-16 to 2026-10-22 (UTC)")

    def test_dates_through_the_end_of_the_last_day(self):
        w = war.window("days", since="2026-10-19", until="2026-10-23", now=NOW)
        self.assertEqual((w["since"], w["until"], w["period"], w["label"]), ("2026-10-19 00:00:00", "2026-10-24 00:00:00", "dates", "2026-10-19 to 2026-10-23 (UTC)"))
        w = war.window("days", since="2026-10-19 08:00", until="2026-10-23 17:30", now=NOW)
        self.assertEqual(w["label"], "2026-10-19 08:00 to 2026-10-23 17:30 (UTC)")
        w = war.window("days", since="2026-10-19", now=NOW)                 # until empty: now
        self.assertEqual(w["until"], "2026-10-23 20:00:00")

    def test_a_time_zone(self):
        w = war.window("last-week", time_zone="America/New_York", now=NOW)
        self.assertEqual((w["since"], w["until"]), ("2026-10-12 04:00:00", "2026-10-19 04:00:00"))
        self.assertEqual(w["label"], "2026-10-12 to 2026-10-18 (America/New_York)")
        w = war.window("days", 7, time_zone="Mars/Olympus", now=NOW)
        self.assertEqual(w["time_zone"], "UTC")
        self.assertIn("not a time zone", w["note"])

    def test_what_is_refused(self):
        with self.assertRaises(ValueError):
            war.window("days", since="2026-10-23", until="2026-10-20", now=NOW)
        with self.assertRaises(ValueError):
            war.window("fortnight", now=NOW)
        with self.assertRaises(ValueError):
            war.window("days", since="next tuesday", now=NOW)
        with self.assertRaises(ValueError):
            war.window("dates", now=NOW)


class Records(unittest.TestCase):
    def test_normalize(self):
        it = war.normalize(rec("incident", "INC1", "2026-10-13 09:00:00", resolved="2026-10-20 15:30:00", state=("6", "Resolved"), close_code="Solved"),
                           "incident", "https://sn.example.mil", ["u_site"])
        self.assertEqual((it["type"], it["done_at"], it["done_how"], it["state"], it["assigned_to"], it["close_code"]),
                         ("Incident", "2026-10-20 15:30:00", "resolved", "Resolved", "Jane Doe", "Solved"))
        self.assertEqual(it["link"], "https://sn.example.mil/nav_to.do?uri=incident.do?sys_id=inc1")
        self.assertEqual(it["extra"], {"u_site": ""})
        # resolved and closed: the resolution is the completion; closed only: closed
        it = war.normalize(rec("incident", "INC2", "2026-10-21 06:00:00", closed="2026-10-23 08:00:00", resolved="2026-10-22 07:45:00"), "incident")
        self.assertEqual((it["done_at"], it["done_how"]), ("2026-10-22 07:45:00", "resolved"))
        it = war.normalize(rec("sc_task", "SCTASK1", "2026-10-17 10:00:00", closed="2026-10-18 12:00:00", request_item="RITM1"), "sc_task")
        self.assertEqual((it["type"], it["done_how"], it["parent"]), ("Catalog task", "closed", "RITM1"))
        it = war.normalize(rec("change_task", "CTASK1", "2026-10-20 09:05:00", change_request="CHG1"), "change_task")
        self.assertEqual((it["type"], it["parent"], it["done_at"]), ("Change task", "CHG1", ""))
        # a class the job does not name: ServiceNow's label; a plain record (no display values) works too
        it = war.normalize({"sys_id": {"value": "x", "display_value": "x"}, "sys_class_name": {"value": "u_ops_task", "display_value": "Ops Task"}}, "task")
        self.assertEqual(it["type"], "Ops Task")
        it = war.normalize({"sys_id": "y", "number": "INC9", "opened_at": "2026-10-20 10:00:00"}, "incident")
        self.assertEqual((it["number"], it["opened_at"], it["type"]), ("INC9", "2026-10-20 10:00:00", "Incident"))

    def test_classify(self):
        def c(**kw):
            return war.classify(war.normalize(rec(**kw), kw["table"]), SINCE, UNTIL)
        v = c(table="incident", number="INC1", opened="2026-10-13 09:00:00", resolved="2026-10-20 15:30:00")       # completed, not new
        self.assertEqual((v["opened"], v["done"], v["open_at_end"], v["touched"], v["days_to_done"]), (False, True, False, False, 7.3))
        v = c(table="incident", number="INC2", opened="2026-10-19 08:00:00", updated="2026-10-22 11:00:00")        # new, still open
        self.assertEqual((v["opened"], v["done"], v["open_at_end"], v["touched"], v["age_days"]), (True, False, True, False, 4.5))
        v = c(table="incident", number="INC3", opened="2026-09-10 07:00:00", updated="2026-10-21 09:15:00")        # old, worked on
        self.assertEqual((v["opened"], v["open_at_end"], v["touched"], v["age_days"]), (False, True, True, 43.5))
        v = c(table="incident", number="INC4", opened="2026-10-21 06:10:00", resolved="2026-10-22 07:45:00", closed="2026-10-23 08:00:00")
        self.assertEqual((v["opened"], v["done"], v["open_at_end"]), (True, True, False))
        self.assertIsNone(c(table="incident", number="INC5", opened="2026-10-06 10:00:00", resolved="2026-10-10 12:00:00", closed="2026-10-17 12:00:00"))  # auto-closed
        self.assertIsNone(c(table="incident", number="INC7", opened="2026-10-23 21:00:00"))                          # after the week
        v = c(table="incident", number="INC8", opened="2026-10-16 19:00:00")                                           # before, untouched
        self.assertEqual((v["opened"], v["open_at_end"], v["touched"]), (False, True, False))
        self.assertIsNone(c(table="incident", number="INC9", opened="2026-10-01 10:00:00", closed="2026-10-02 10:00:00"))  # long done

    def test_the_query(self):
        q = war.table_query(["g1", "g2"], [], SINCE, UNTIL)
        self.assertEqual(q, "assignment_groupINg1,g2^opened_at<=2026-10-24 20:00:00^closed_at>=2026-10-15 20:00:00^ORclosed_atISEMPTY"
                            "^ORsys_updated_on>=2026-10-15 20:00:00^ORDERBYopened_at")
        self.assertTrue(war.table_query(["g1"], ["u1"], SINCE, UNTIL).startswith("assignment_groupINg1^ORassigned_toINu1^opened_at<="))
        self.assertTrue(war.table_query([], ["u1"], SINCE, UNTIL).startswith("assigned_toINu1^opened_at<="))
        self.assertTrue(war.table_query([], [], SINCE, UNTIL, group_names=["Ops - A", "Ops - B"]).startswith("assignment_group.nameINOps - A,Ops - B^opened_at"))


class FakeApi(object):
    """Answers each table's read page by page; refuses tables in deny; groups g1 Linux Operations, users u2/u3."""

    def __init__(self, rows, deny=(), no_groups=False):
        self.rows, self.deny, self.no_groups, self.calls, self.url = rows, set(deny), no_groups, [], "https://sn.example.mil"

    def get(self, path, params):
        self.calls.append((path, dict(params)))
        table = path.rsplit("/", 1)[1]
        if table == "sys_user_group":
            if self.no_groups:
                raise war.SnError("GET %s: HTTP 403: Operation Failed: ACL Exception" % path)
            want = params["sysparm_query"][6:].split(",")
            return {"result": [{"sys_id": "g1", "name": "Linux Operations"}] if "Linux Operations" in want else []}
        if table == "sys_user":
            want = params["sysparm_query"].partition("^OR")[0][11:].split(",")
            return {"result": [u for u in ({"sys_id": "u2", "user_name": "jdoe", "name": "Jane Doe"}, {"sys_id": "u3", "user_name": "bsmith", "name": "Bob Smith"})
                               if u["user_name"] in want or u["name"] in want]}
        if table in self.deny:
            raise war.SnError("GET %s: HTTP 403: Operation Failed: ACL Exception: no read access to %s" % (path, table))
        rows = self.rows.get(table, [])
        o, n = int(params.get("sysparm_offset", 0)), int(params["sysparm_limit"])
        return {"result": rows[o:o + n]}


WEEK = {"incident": [rec("incident", "INC1", "2026-10-13 09:00:00", resolved="2026-10-20 15:30:00", state=("6", "Resolved")),
                     rec("incident", "INC2", "2026-10-19 08:00:00", updated="2026-10-22 11:00:00"),
                     rec("incident", "INC3", "2026-09-10 07:00:00", updated="2026-10-21 09:15:00", who=("u3", "Bob Smith")),
                     rec("incident", "INC5", "2026-10-06 10:00:00", resolved="2026-10-10 12:00:00", closed="2026-10-17 12:00:00", state=("7", "Closed")),
                     rec("incident", "INC7", "2026-10-23 21:00:00")],
        "sc_task": [rec("sc_task", "SCTASK1", "2026-10-17 10:00:00", closed="2026-10-18 12:00:00", state=("3", "Closed Complete"), who=("u3", "Bob Smith"), request_item="RITM1"),
                    rec("sc_task", "SCTASK2", "2026-10-22 14:00:00", state=("1", "Open"), who=("", ""))],
        "change_request": [rec("change_request", "CHG1", "2026-10-05 09:00:00", closed="2026-10-21 18:00:00", state=("3", "Closed"))]}


class Reads(unittest.TestCase):
    def test_page_by_page_and_the_cap(self):
        api = FakeApi({"incident": [rec("incident", "INC%d" % i, "2026-10-20 10:00:00") for i in range(7)]})
        rows, more = war.read_table(api, "incident", "q", page=3)
        self.assertEqual((len(rows), more), (7, False))
        self.assertEqual([c[1]["sysparm_offset"] for c in api.calls], [0, 3, 6])
        self.assertEqual(api.calls[0][1]["sysparm_display_value"], "all")
        self.assertIn("resolved_at", api.calls[0][1]["sysparm_fields"])
        rows, more = war.read_table(api, "incident", "q", max_records=5, page=3)
        self.assertEqual((len(rows), more), (5, True))
        api = FakeApi({"u_ops_task": [rec("u_ops_task", "OPS1", "2026-10-20 10:00:00")]})
        war.read_table(api, "u_ops_task", "q", ["u_site"])
        self.assertNotIn("resolved_at", api.calls[0][1]["sysparm_fields"])          # a table the job does not know: the common fields
        self.assertIn("u_site", api.calls[0][1]["sysparm_fields"])

    def test_lookups(self):
        api = FakeApi({})
        g, err = war.lookup(api, "sys_user_group", ["Linux Operations", "Linux Ops"], ["name"])
        self.assertEqual((err, [(x["name"], x["found"], x["sys_id"]) for x in g]), ("", [("Linux Operations", True, "g1"), ("Linux Ops", False, "")]))
        u, err = war.lookup(api, "sys_user", ["bsmith", "Jane Doe"], ["user_name", "name"])
        self.assertEqual([x["sys_id"] for x in u], ["u3", "u2"])
        g, err = war.lookup(FakeApi({}, no_groups=True), "sys_user_group", ["Linux Operations"], ["name"])
        self.assertIn("HTTP 403", err)
        self.assertIsNone(g[0]["found"])
        self.assertEqual(war.lookup(api, "sys_user_group", [], ["name"]), ([], ""))

    def test_the_week(self):
        api = FakeApi(WEEK)
        r = war.read_week(api, ["Linux Operations"], [], ["incident", "sc_task", "change_request", "problem_task"], war.window("days", 7, now=NOW))
        self.assertTrue(r["ok"])
        self.assertEqual(r["counts"], {"items": 6, "opened": 3, "done": 3, "cancelled": 0, "open_at_end": 3, "touched": 1, "sla_breached": 0, "sla_at_risk": 0})
        self.assertEqual([x["number"] for x in r["items"] if x["done"]], ["SCTASK1", "INC1", "CHG1"])        # by the date of completion
        self.assertEqual([(t["table"], t["rows"], t["read"]) for t in r["tables"]], [("incident", 3, 5), ("sc_task", 2, 2), ("change_request", 1, 1), ("problem_task", 0, 0)])
        self.assertEqual(r["groups"], [{"name": "Linux Operations", "sys_id": "g1", "found": True}])
        self.assertTrue(api.calls[2][1]["sysparm_query"].startswith("assignment_groupINg1^"))

    def test_every_class_once_the_resolution_from_the_incident_table(self):
        # the task table has no resolved_at: without the incident table's, INC1 (resolved, not closed) would look open and
        # INC5 (resolved before the week, closed by itself in it) completed
        bare = [dict(x, resolved_at={"value": "", "display_value": ""}) for x in WEEK["incident"]]
        api = FakeApi(dict(WEEK, task=bare + WEEK["sc_task"]))
        r = war.read_week(api, ["Linux Operations"], [], ["task", "incident"], war.window("days", 7, now=NOW))
        self.assertEqual(r["counts"], {"items": 5, "opened": 3, "done": 2, "cancelled": 0, "open_at_end": 3, "touched": 1, "sla_breached": 0, "sla_at_risk": 0})
        self.assertEqual([(t["table"], t["rows"], t["error"]) for t in r["tables"]], [("task", 5, ""), ("incident", 0, "")])
        inc1 = next(x for x in r["items"] if x["number"] == "INC1")
        self.assertEqual((inc1["done_at"], inc1["done_how"], inc1["done"]), ("2026-10-20 15:30:00", "resolved", True))
        self.assertEqual(next(c[1]["sysparm_query"] for c in api.calls if c[1].get("sysparm_query", "").startswith("sys_idIN")), "sys_idINinc1,inc2,inc3,inc5,inc7")
        # the incident table refused: said, the records kept with their closed_at
        api = FakeApi(dict(WEEK, task=bare + WEEK["sc_task"]), deny=["incident"])
        r = war.read_week(api, ["Linux Operations"], [], ["task"], war.window("days", 7, now=NOW))
        self.assertTrue(r["ok"])
        self.assertIn("resolved_at not read from incident", r["tables"][0]["error"])
        self.assertEqual(r["counts"]["done"], 2)             # SCTASK1 and INC5 (closed in the week)

    def test_a_table_refused_a_group_missing_the_group_table_unreadable(self):
        r = war.read_week(FakeApi(WEEK, deny=["sc_task"]), ["Linux Operations", "Linux Ops"], [], ["incident", "sc_task"], war.window("days", 7, now=NOW))
        self.assertTrue(r["ok"])
        self.assertEqual([x["found"] for x in r["groups"]], [True, False])
        self.assertIn("no read access to sc_task", r["tables"][1]["error"])
        r = war.read_week(FakeApi(WEEK, deny=["incident", "sc_task"]), ["Linux Operations"], [], ["incident", "sc_task"], war.window("days", 7, now=NOW))
        self.assertFalse(r["ok"])
        self.assertIn("no table could be read", r["error"])
        r = war.read_week(FakeApi(WEEK), ["Linux Ops"], [], ["incident"], war.window("days", 7, now=NOW))
        self.assertEqual((r["ok"], r["error"]), (False, "none of the assignment groups is in ServiceNow: Linux Ops"))
        api = FakeApi(WEEK, no_groups=True)
        r = war.read_week(api, ["Linux Operations"], [], ["incident"], war.window("days", 7, now=NOW))
        self.assertTrue(r["ok"])
        self.assertIn("matched by name instead", r["lookup_note"])
        self.assertTrue([c for c in api.calls if c[0].endswith("/incident")][-1][1]["sysparm_query"].startswith("assignment_group.nameINLinux Operations^"))
        r = war.read_week(FakeApi(WEEK), [], ["bsmith"], ["incident", "sc_task"], war.window("days", 7, now=NOW))
        self.assertEqual(sorted(x["number"] for x in r["items"]), ["INC1", "INC2", "INC3", "SCTASK1", "SCTASK2"])   # the fake does not filter by user: the query does
        r = war.read_week(FakeApi(WEEK), [], [], ["incident"], war.window("days", 7, now=NOW))
        self.assertEqual(r["error"], "no assignment group: set servicenow_war_groups")


SLAS = [{"task": "inc2", "sla": "P2 resolution (2 days)", "stage": "in_progress", "has_breached": "true", "active": "true",
         "business_percentage": "160.2", "planned_end_time": "2026-10-21 08:00:00"},
        {"task": "inc2", "sla": "P2 response (1 hour)", "stage": "cancelled", "has_breached": "true", "business_percentage": "300"},
        {"task": "inc3", "sla": "P4 resolution (30 days)", "stage": "in_progress", "has_breached": "false", "active": "true",
         "business_percentage": "82.5", "percentage": "95", "planned_end_time": "2026-10-25 07:00:00"},
        {"task": "sctask2", "sla": "Catalog task (5 days)", "stage": "paused", "has_breached": "false", "active": "true",
         "business_percentage": "", "percentage": "76", "planned_end_time": "2026-10-29 14:00:00"},
        {"task": "chg1", "sla": "Change (10 days)", "stage": "completed", "has_breached": "true", "active": "false",
         "business_percentage": "130", "planned_end_time": "2026-10-15 09:00:00"},
        {"task": "inc1", "sla": "P3 resolution (5 days)", "stage": "completed", "has_breached": "false", "active": "false", "business_percentage": "60"}]


class Slas(unittest.TestCase):
    def test_breached_close_and_within(self):
        r = war.read_week(FakeApi(dict(WEEK, task_sla=SLAS)), ["Linux Operations"], [], ["incident", "sc_task", "change_request"],
                          war.window("days", 7, now=NOW))
        st = dict((x["number"], (x["sla_state"], x["sla_name"], x["sla_percent"])) for x in r["items"])
        self.assertEqual(st["INC2"], ("breached", "P2 resolution (2 days)", 160.2))        # the cancelled one left out
        self.assertEqual(st["INC3"], ("at_risk", "P4 resolution (30 days)", 82.5))         # business time first
        self.assertEqual(st["SCTASK2"], ("at_risk", "Catalog task (5 days)", 76.0))        # paused still runs; no business time: the clock's
        self.assertEqual(st["CHG1"], ("breached", "Change (10 days)", 130.0))
        self.assertEqual(st["INC1"], ("ok", "P3 resolution (5 days)", 60.0))
        self.assertEqual(st["SCTASK1"], ("", "", None))                                     # no SLA
        self.assertEqual((r["sla"]["read"], r["sla"]["breached"], r["sla"]["at_risk"]), (True, 2, 2))
        rep = wf.servicenow_war_report(r, {"old_days": 30})
        self.assertNotIn("SLA breached", [t["label"] for t in rep["summary"]])          # the top is the week's results: no SLA tiles
        self.assertEqual([x["title"] for x in rep["sections"]][-2:], ["SLA breached or close to breaching", "Read"])   # at the bottom
        sec = [x for x in rep["sections"] if x["title"] == "SLA breached or close to breaching"][0]
        self.assertEqual([(row[0], row[6], row[8]) for row in sec["rows"]],
                         [("INC2", "160.2%", "breached"), ("INC3", "82.5%", "close to breaching"), ("SCTASK2", "76%", "close to breaching"),
                          ("CHG1", "130%", "breached, completed")])
        self.assertEqual(sec["row_status"], ["critical", "warning", "warning", "critical"])
        lines = wf.servicenow_war_lines(r)
        self.assertTrue(lines["completed"][-1].endswith("(Jane Doe)"), lines["completed"])       # the lines to paste: no SLA words
        self.assertIn("Incident INC3: Work on INC3 - In Progress, open since 2026-09-10, 43 days (Bob Smith)", lines["in_progress"])
        self.assertIn("| SLA breached", wf.servicenow_war_act_evidence(r))
        allx = wf.servicenow_war_xlsx(r)[0]
        row = dict(zip(allx["columns"], allx["rows"][[x[0] for x in allx["rows"]].index("INC3")]))
        self.assertEqual((row["SLA"], row["SLA status"], row["SLA time used %"]), ("P4 resolution (30 days)", "close to breaching", 82.5))

    def test_none_and_unreadable(self):
        rep = wf.servicenow_war_report(result(), {"old_days": 30})
        sec = [x for x in rep["sections"] if x["title"] == "SLA breached or close to breaching"][0]
        self.assertEqual(sec["status"], "ok")
        r = war.read_week(FakeApi(WEEK, deny=["task_sla"]), ["Linux Operations"], [], ["incident"], war.window("days", 7, now=NOW))
        self.assertTrue(r["ok"])
        self.assertEqual((r["sla"]["read"], r["sla"]["error"][:30]), (False, "GET api/now/table/task_sla: HT"))
        rep = wf.servicenow_war_report(r, {"old_days": 30})
        self.assertNotIn("SLA breached", [t["label"] for t in rep["summary"]])
        self.assertTrue(any(x.startswith("task_sla (the SLAs): ") for x in [s for s in rep["sections"] if s["title"] == "Not read"][0]["lines"]))
        r = war.read_week(FakeApi(WEEK), ["Linux Operations"], [], ["incident"], war.window("days", 7, now=NOW), sla=False)
        self.assertFalse(r["sla"]["read"])
        self.assertNotIn("SLA breached or close to breaching", [s["title"] for s in wf.servicenow_war_report(r)["sections"]])


def result(**kw):
    r = war.read_week(FakeApi(WEEK), ["Linux Operations"], [], ["incident", "sc_task", "change_request"], war.window("days", 7, now=NOW))
    r["url"] = "https://sn.example.mil"
    r.update(kw)
    return r


class Report(unittest.TestCase):
    def test_the_report(self):
        rep = wf.servicenow_war_report(result(), {"old_days": 30})
        self.assertEqual(rep["title"], "Weekly activity report for Linux Operations: 3 completed, 3 opened, 3 open at the end (2026-10-16 20:00 to 2026-10-23 20:00 (UTC))")
        self.assertEqual(rep["status"], "info")
        self.assertEqual([t["label"] + "=" + str(t["value"]) for t in rep["summary"]],
                         ["Completed=3", "Opened=3", "Open at the end=3", "Older than 30 days=1", "Worked on, still open=1", "Cancelled=0"])
        secs = dict((s["title"], s) for s in rep["sections"])
        # the results, the WAR from the tables (ACT's version goes right after it), then the SLAs and what was read
        self.assertEqual(list(secs), ["The week in numbers", "Completed this week", "Opened this week", "Open at the end of the week", "By person",
                                      "For the WAR (from the tables): completed", "For the WAR (from the tables): in progress",
                                      "SLA breached or close to breaching", "Read"])
        act = [{"title": "For the WAR (written by ACT): summary"}]
        self.assertEqual([x["title"] for x in wf.servicenow_war_add_act(rep, act)["sections"]][5:],
                         ["For the WAR (from the tables): completed", "For the WAR (from the tables): in progress",
                          "For the WAR (written by ACT): summary", "SLA breached or close to breaching", "Read"])
        done = secs["Completed this week"]
        self.assertEqual([r[1] for r in done["rows"]], ["SCTASK1", "INC1", "CHG1"])
        self.assertEqual(done["rows"][1][:3], ["2026-10-20 15:30", "INC1", "Incident"])
        self.assertEqual(done["rows"][1][5:9], ["Resolved", "3 - Moderate", "2026-10-13", "7.3"])
        opn = secs["Open at the end of the week"]
        self.assertEqual([r[1] for r in opn["rows"]], ["INC3", "INC2", "SCTASK2"])                      # the oldest first
        self.assertEqual(opn["row_status"], ["warning", "", ""])
        self.assertEqual([r[-1] for r in opn["rows"]], ["worked on", "new", "new"])
        self.assertEqual(opn["rows"][2][4], "(unassigned)")
        self.assertEqual(secs["By person"]["rows"][0], ["Jane Doe", 2, 1, 1, 0, 0])
        self.assertEqual(secs["The week in numbers"]["rows"][-1], ["Total", 3, 3, 0, 3, 1, 1])
        self.assertEqual(secs["For the WAR (from the tables): completed"]["lines"][1], "Incident INC1: Work on INC1 - resolved 2026-10-20 (Jane Doe)")
        self.assertEqual(secs["For the WAR (from the tables): in progress"]["lines"][0],
                         "Incident INC3: Work on INC3 - In Progress, open since 2026-09-10, 43 days (Bob Smith)")
        self.assertTrue(secs["For the WAR (from the tables): completed"]["text"].startswith("One line per task"))
        self.assertTrue(secs["For the WAR (from the tables): in progress"]["lines"][-1].endswith(" (unassigned)"))     # not ((unassigned))
        self.assertTrue(rep["subtitle"].startswith("Assignment group: Linux Operations \u00b7 The week: 2026-10-16 20:00"), rep["subtitle"])

    def test_names_as_text_and_the_team_folder(self):
        self.assertEqual(wf.servicenow_war_name_list("ABC - Ops - IT - Linux"), ["ABC - Ops - IT - Linux"])   # one group, not six
        self.assertEqual(wf.servicenow_war_name_list("Ops - A\nOps - B; Ops - C,Ops - D\n"), ["Ops - A", "Ops - B", "Ops - C", "Ops - D"])
        self.assertEqual(wf.servicenow_war_name_list(["Jane Doe", " ", "Bob Smith "]), ["Jane Doe", "Bob Smith"])
        self.assertEqual(wf.servicenow_war_name_list(None), [])
        self.assertEqual(wf.servicenow_war_web_folder("ABC - Ops - IT - Linux"), "abc-ops-it-linux")
        self.assertEqual(wf.servicenow_war_web_folder(["Windows Operations", "Linux Operations"]), "windows-operations-linux-operations")
        self.assertEqual(wf.servicenow_war_web_folder(""), "all")

    def test_a_group_name_with_dashes_leads_the_title(self):
        r = result()
        r["groups"] = [{"name": "ABC - Ops - IT - Linux", "sys_id": "g1", "found": True}]
        rep = wf.servicenow_war_report(r, {"old_days": 30})
        self.assertTrue(rep["title"].startswith("Weekly activity report for ABC - Ops - IT - Linux: 3 completed, "), rep["title"])
        self.assertTrue(rep["subtitle"].startswith("Assignment group: ABC - Ops - IT - Linux \u00b7 "), rep["subtitle"])

    def test_two_versions_with_act(self):
        rep = wf.servicenow_war_report(result(), {"old_days": 30, "act": True})
        titles = [s["title"] for s in rep["sections"]]
        i = titles.index("For the WAR (from the tables): completed")
        self.assertEqual(titles[i:i + 2], ["For the WAR (from the tables): completed", "For the WAR (from the tables): in progress"])
        self.assertTrue(rep["sections"][i]["text"].startswith("Two versions of the WAR, to choose from: this one, one line per task from the tables, "
                                                               "and ACT's"), rep["sections"][i]["text"])
        self.assertIn("Dates in UTC", rep["footer"])

    def test_a_time_zone_in_the_report(self):
        r = result(window=war.window("days", 7, time_zone="America/New_York", now=NOW))
        rep = wf.servicenow_war_report(r)
        done = dict((s["title"], s) for s in rep["sections"])["Completed this week"]
        self.assertEqual(done["rows"][1][0], "2026-10-20 11:30")
        self.assertEqual(wf.servicenow_war_lines(r)["completed"][1], "Incident INC1: Work on INC1 - resolved 2026-10-20 (Jane Doe)")

    def test_what_was_not_read(self):
        r = result(tables=[{"table": "incident", "rows": 3, "read": 5, "error": "", "more": True},
                           {"table": "problem_task", "rows": 0, "read": 0, "error": "GET api/now/table/problem_task: HTTP 403: ACL", "more": False}],
                   groups=[{"name": "Linux Operations", "sys_id": "g1", "found": True}, {"name": "Linux Ops", "sys_id": "", "found": False}],
                   lookup_note="")
        rep = wf.servicenow_war_report(r)
        self.assertEqual(rep["status"], "warning")
        self.assertEqual(rep["summary"][-1], {"label": "Not read", "value": 1, "status": "critical"})
        nr = dict((s["title"], s) for s in rep["sections"])["Not read"]
        self.assertEqual(nr["lines"], ["problem_task: GET api/now/table/problem_task: HTTP 403: ACL",
                                       "incident: more than the first 5 records - raise servicenow_war_max_records, or narrow the week",
                                       "assignment group Linux Ops is not in ServiceNow (its exact name, as the group list shows it)"])

    def test_not_run_and_nothing(self):
        rep = wf.servicenow_war_report({"ok": False, "error": "GET api/now/table/sys_user_group: HTTP 401: User Not Authenticated", "window": {"label": "x"}})
        self.assertEqual((rep["status"], rep["sections"][0]["title"]), ("critical", "Not run"))
        self.assertIn("HTTP 401", rep["title"])
        r = result(items=[], counts={})
        rep = wf.servicenow_war_report(r)
        self.assertEqual([s["title"] for s in rep["sections"]], ["The week in numbers", "Result", "Read"])
        self.assertIn("Nothing was opened, completed or open for Linux Operations", rep["sections"][1]["lines"][0])

    def test_the_rows_cut(self):
        rep = wf.servicenow_war_report(result(), {"max_rows": 2, "notes_chars": 2})
        done = dict((s["title"], s) for s in rep["sections"])["Completed this week"]
        self.assertEqual(len(done["rows"]), 2)
        self.assertIn("The first 2 of 3 are shown", done["text"])
        self.assertEqual(done["rows"][0][-1], "do")        # the notes cut short

    def test_the_workbook(self):
        sheets = wf.servicenow_war_xlsx(result())
        self.assertEqual([(s["name"], len(s["rows"])) for s in sheets],
                         [("All items", 6), ("Completed", 3), ("Opened", 3), ("Open at the end", 3), ("Cancelled", 0), ("By person", 3), ("By type", 3)])
        allx = sheets[0]
        row = dict(zip(allx["columns"], allx["rows"][[r[0] for r in allx["rows"]].index("INC1")]))
        self.assertEqual((row["Completed"], row["How"], row["Completed this week"], row["Cancelled this week"], row["Open at the end"], row["Days to complete"]),
                         ("2026-10-20 15:30", "resolved", "yes", "no", "no", 7.3))
        self.assertTrue(row["Link"].endswith("incident.do?sys_id=inc1"))
        self.assertEqual(sheets[5]["rows"][0], ["Jane Doe", 2, 1, 1, 0, 0])
        self.assertEqual(sheets[6]["rows"], [["Catalog task", 1, 2, 1, 0, 0], ["Change", 1, 0, 0, 0, 0], ["Incident", 1, 1, 2, 1, 0]])


class Act(unittest.TestCase):
    def test_evidence_and_names(self):
        ev = wf.servicenow_war_act_evidence(result())
        self.assertIn("COMPLETED IN THE WEEK (3):", ev)
        self.assertIn("INC1 | Incident | Work on INC1 | resolved 2026-10-20 | priority 3 - Moderate | Jane Doe | close notes: done", ev)
        self.assertIn("INC3 | Incident | Work on INC3 | In Progress | open 43 days | this week: worked on", ev)
        self.assertEqual(wf.servicenow_war_names(result()), ["AAP Service", "Bob Smith", "Jane Doe", "Linux Operations", "web01.example.mil"])
        task = wf.servicenow_war_act_task({"extra": "Say ma'am."})
        self.assertIn("BEGIN_ACT_ANALYSIS", task)
        self.assertTrue(task.endswith("Say ma'am."))

    def test_the_answer(self):
        raw = ('Here it is.\nBEGIN_ACT_ANALYSIS\n{"overall": "A steady week.", "items": [{"id": "accomplishments", "lines": ["Fixed mail (INC1)", "Patched web (CHG1)"]},'
               ' {"id": "in_progress", "lines": ["NFS hangs (PTASK1)"]}, {"id": "issues", "lines": []},]}\nEND_ACT_ANALYSIS\n')
        pa = wf.servicenow_war_act_parse(raw)
        self.assertEqual((pa["parsed"], pa["overall"], pa["accomplishments"], pa["in_progress"], pa["issues"]),
                         (True, "A steady week.", ["Fixed mail (INC1)", "Patched web (CHG1)"], ["NFS hangs (PTASK1)"], []))
        pa = wf.servicenow_war_act_parse('```json\n{"overall": "x", "accomplishments": "one line", "in_progress": [], "issues": ["old INC3"]}\n```')
        self.assertEqual((pa["accomplishments"], pa["issues"]), (["one line"], ["old INC3"]))
        pa = wf.servicenow_war_act_parse('{"overall": "**A busy week.**", "accomplishments": ["- **Fixed** mail (INC1)\\n2. Patched `web` (CHG1)", '
                                         '"- Fixed mail (INC1)", {"text": "* Closed the request (SCTASK1)"}], "issues": "1) Old INC3 - 43 days"}')
        self.assertEqual((pa["overall"], pa["accomplishments"], pa["issues"]),
                         ("A busy week.", ["Fixed mail (INC1)", "Patched web (CHG1)", "Closed the request (SCTASK1)"], ["Old INC3 - 43 days"]))
        self.assertEqual(wf.servicenow_war_act_parse("I am ready. What would you like?")["error"], "ACT's answer has no readable JSON block")
        self.assertEqual(wf.servicenow_war_act_parse("")["error"], "ACT gave no answer")
        secs = wf.servicenow_war_act_section(raw, {"ran": True, "provider": "genai", "model": "gemini-3.8-flash"})
        self.assertEqual([s["title"] for s in secs], ["For the WAR (written by ACT): summary", "For the WAR (written by ACT): accomplishments",
                                                      "For the WAR (written by ACT): in progress"])
        self.assertEqual(secs[0]["lines"], ["A steady week."])
        self.assertTrue(secs[0]["text"].startswith("The other version of the WAR: written by GenAI (genai gemini-3.8-flash)"))
        secs = wf.servicenow_war_act_section("", {"ran": False, "reason": "no key"})
        self.assertEqual((secs[0]["status"], secs[0]["lines"]), ("warning", ["ACT did not run: no key"]))
        secs = wf.servicenow_war_act_section("just words", {"ran": True})
        self.assertEqual((secs[0]["status"], secs[0]["lines"]), ("warning", ["just words"]))

    def test_the_answer_mangled(self):
        # a raw line break inside a string (strict JSON refuses it)
        pa = wf.servicenow_war_act_parse('{"overall": "A\nsteady week.", "accomplishments": ["Fixed mail (INC1)"], "in_progress": [], "issues": []}')
        self.assertEqual((pa["parsed"], pa["form"], pa["overall"], pa["accomplishments"]), (True, "json", "A steady week.", ["Fixed mail (INC1)"]))
        # curly quotes and a comment
        pa = wf.servicenow_war_act_parse('“overall”: “x”'.join(["{", ", “accomplishments”: [“Fixed mail (INC1)”], // done\n"
                                                                                     "“in_progress”: [], “issues”: []}"]))
        self.assertEqual((pa["parsed"], pa["accomplishments"]), (True, ["Fixed mail (INC1)"]))
        # cut short at the model's output limit: the END marker never came, the partial bullet is lost, the rest is read
        cut = ('BEGIN_ACT_ANALYSIS\n{"overall": "A steady week.", "items": [{"id": "accomplishments", "lines": ["Fixed mail (INC1)", "Patched web (CHG1)", '
               '"Half a bul')
        pa = wf.servicenow_war_act_parse(cut)
        self.assertEqual((pa["parsed"], pa["form"], pa["overall"], pa["accomplishments"], pa["in_progress"]),
                         (True, "json-cut", "A steady week.", ["Fixed mail (INC1)", "Patched web (CHG1)"], []))
        pa = wf.servicenow_war_act_parse('{"overall": "x", "accomplishments": ["Fixed mail (INC1)"], "in_progress": ["NFS (PTASK1)"], "issues":')
        self.assertEqual((pa["form"], pa["accomplishments"], pa["in_progress"], pa["issues"]), ("json-cut", ["Fixed mail (INC1)"], ["NFS (PTASK1)"], []))
        secs = wf.servicenow_war_act_section(cut, {"ran": True, "provider": "genai"})
        self.assertIn("cut short at the model's output limit", secs[0]["text"])
        self.assertIn('ACT_MAX_TOKENS: "8192"', secs[0]["text"])
        # no JSON at all: headings and bullets
        prose = ("Summary:\nA steady week with two fixes.\n\n**Accomplishments**\n- Fixed mail (INC1)\n- Patched web (CHG1)\n\n### In progress\n"
                 "1. NFS hangs (PTASK1)\n\nWhat deserves attention:\n- Old INC3, 43 days\n")
        pa = wf.servicenow_war_act_parse(prose)
        self.assertEqual((pa["parsed"], pa["form"], pa["overall"], pa["accomplishments"], pa["in_progress"], pa["issues"]),
                         (True, "prose", "A steady week with two fixes.", ["Fixed mail (INC1)", "Patched web (CHG1)"], ["NFS hangs (PTASK1)"], ["Old INC3, 43 days"]))
        secs = wf.servicenow_war_act_section(prose, {"ran": True})
        self.assertIn("sorted into these sections by their headings", secs[0]["text"])
        self.assertEqual([s["title"] for s in secs][-1], "For the WAR (written by ACT): what deserves attention")
        # words with no headings: shown as they came
        self.assertEqual(wf.servicenow_war_act_parse("Here is the week.\nIt went well.")["error"], "ACT's answer has no readable JSON block")
        # the case seen at a site: ACT's own action record - the model asked to count the words of a bullet with Python
        raw = ("{'command': 'python3 -c \"\\nb = \\'Closed twelve change tasks that were cancelled (CTASK0026485, CTASK0026486).\\'\\nprint(len(b.split()))\\n\"', "
               "'thought': 'Check word count of the cancelled tasks bullet.', 'action': 'run'}")
        pa = wf.servicenow_war_act_parse(raw)
        self.assertEqual((pa["parsed"], pa["form"], pa["action"]["thought"]), (False, "action", "Check word count of the cancelled tasks bullet."))
        self.assertTrue(pa["error"].startswith("ACT tried to run a command instead of answering (evidence mode runs none): python3 -c"), pa["error"])
        secs = wf.servicenow_war_act_section(raw, {"ran": True})
        self.assertEqual((secs[0]["status"], secs[0]["lines"][1]), ("warning", "Its reason: Check word count of the cancelled tasks bullet."))
        self.assertTrue(secs[0]["lines"][0].startswith("It wanted to run: python3 -c"))
        self.assertIn("asked to run a command", secs[0]["text"])
        # the case seen at a site next: the answer wrapped in a reply object, reaching the job as a dict or its Python repr
        inner = ('BEGIN_ACT_ANALYSIS\n{"overall": "The team completed 10 tasks.", "items": [{"id": "accomplishments", "lines": '
                 '["Upgraded five cluster hosts to the team\'s build (CHG1)."]}, {"id": "in_progress", "lines": ["NFS (PTASK1)"]}]}\nEND_ACT_ANALYSIS')
        for wrapped in ({"message": inner}, str({"message": inner}), str({"action": "finish", "message": inner, "done": True}),
                        '{"message": %s, "done": true}' % __import__("json").dumps(inner)):
            pa = wf.servicenow_war_act_parse(wrapped)
            self.assertEqual((pa["parsed"], pa["form"], pa["overall"], pa["accomplishments"], pa["in_progress"]),
                             (True, "json", "The team completed 10 tasks.", ["Upgraded five cluster hosts to the team's build (CHG1)."], ["NFS (PTASK1)"]),
                             repr(wrapped)[:60])
        secs = wf.servicenow_war_act_section(str({"message": inner}), {"ran": True})
        self.assertEqual(secs[1]["lines"], ["Upgraded five cluster hosts to the team's build (CHG1)."])
        # unreadable inside the wrapper: its text shown line by line, not the repr
        secs = wf.servicenow_war_act_section(str({"message": "Here is the week.\nIt went well."}), {"ran": True})
        self.assertEqual(secs[0]["lines"], ["Here is the week.", "It went well."])
        self.assertEqual(wf._answer_text(str({"action": "run", "command": "ls"})), str({"action": "run", "command": "ls"}))   # an action: left as it is
        task = wf.servicenow_war_act_task()
        self.assertIn("never count words with a command or a tool", task)
        self.assertIn("answer in ONE step", task)


class Cancelled(unittest.TestCase):
    """Cancelled is not completed; only the week's open work is listed (servicenow_war_open_scope)."""
    EXTRA = [rec("incident", "INC6", "2026-10-07 10:00:00", closed="2026-10-19 09:00:00", state=("8", "Canceled")),
             rec("incident", "INC8", "2026-07-20 08:00:00", updated="2026-08-01 09:00:00")]                   # the backlog: old, untouched

    def week(self):
        return dict(WEEK, incident=WEEK["incident"] + self.EXTRA)

    def test_the_verdict(self):
        def c(states=None, **kw):
            return war.classify(war.normalize(rec(**kw), kw["table"]), SINCE, UNTIL, states)
        v = c(table="incident", number="INC6", opened="2026-10-07 10:00:00", closed="2026-10-19 09:00:00", state=("8", "Canceled"))
        self.assertEqual((v["opened"], v["done"], v["cancelled"], v["is_cancelled"], v["open_at_end"], v["done_at"]), (False, False, True, True, False, "2026-10-19 09:00:00"))
        v = c(states=[], table="incident", number="INC6", opened="2026-10-07 10:00:00", closed="2026-10-19 09:00:00", state=("8", "Canceled"))
        self.assertEqual((v["done"], v["cancelled"]), (True, False))                                              # [] = every closed task counts
        v = c(table="sc_task", number="SCTASK3", opened="2026-10-09 14:00:00", closed="2026-10-17 11:00:00", state=("7", "closed  skipped"))
        self.assertEqual((v["done"], v["cancelled"]), (False, True))                                              # any case, spacing aside
        v = c(table="sc_task", number="SCTASK4", opened="2026-10-09 14:00:00", closed="2026-10-17 11:00:00", state=("3", "Closed Complete"), close_code="Cancelled by requester")
        self.assertEqual((v["done"], v["cancelled"]), (False, True))                                              # the close code says so
        v = c(states=["Withdrawn"], table="sc_task", number="SCTASK5", opened="2026-10-09 14:00:00", closed="2026-10-17 11:00:00", state=("9", "Withdrawn"))
        self.assertEqual((v["done"], v["cancelled"]), (False, True))                                              # the instance's own word
        self.assertIsNone(c(table="incident", number="INC6", opened="2026-10-01 10:00:00", closed="2026-10-10 09:00:00", state=("8", "Canceled")))  # before the week
        v = c(table="incident", number="INC6", opened="2026-10-20 10:00:00", closed="2026-10-21 09:00:00", state=("8", "Canceled"))
        self.assertEqual((v["opened"], v["done"], v["cancelled"]), (True, False, True))                           # opened and cancelled in the week

    def test_the_week_and_the_report(self):
        r = war.read_week(FakeApi(self.week()), ["Linux Operations"], [], ["incident", "sc_task", "change_request"], war.window("days", 7, now=NOW))
        self.assertEqual(r["counts"], {"items": 8, "opened": 3, "done": 3, "cancelled": 1, "open_at_end": 4, "touched": 1, "sla_breached": 0, "sla_at_risk": 0})
        rep = wf.servicenow_war_report(r, {"old_days": 30})
        self.assertTrue(rep["title"].startswith("Weekly activity report for Linux Operations: 3 completed, 3 opened, 4 open at the end"), rep["title"])
        self.assertEqual([t["label"] + "=" + str(t["value"]) for t in rep["summary"]],
                         ["Completed=3", "Opened=3", "Open at the end=4", "Older than 30 days=2", "Worked on, still open=1", "Cancelled=1"])
        secs = dict((s["title"], s) for s in rep["sections"])
        self.assertEqual(list(secs)[-3:], ["SLA breached or close to breaching", "Cancelled this week", "Read"])     # at the bottom
        self.assertEqual([x[1] for x in secs["Completed this week"]["rows"]], ["SCTASK1", "INC1", "CHG1"])
        canc = secs["Cancelled this week"]
        self.assertEqual(canc["rows"][0][:6], ["2026-10-19 09:00", "INC6", "Incident", "Work on INC6", "Jane Doe", "Canceled"])
        self.assertIn("Closed in the week as canceled - not counted as completed", canc["text"])
        self.assertEqual(secs["The week in numbers"]["rows"][-1], ["Total", 3, 3, 1, 4, 2, 1])
        self.assertEqual(secs["By person"]["rows"][0], ["Jane Doe", 2, 1, 2, 0, 1])           # INC8, the backlog, is hers too
        # the week's open work listed, the untouched backlog counted
        opn = secs["Open at the end of the week"]
        self.assertEqual([x[1] for x in opn["rows"]], ["INC3", "INC2", "SCTASK2"])
        self.assertIn("1 more open task(s) were not touched this week - the backlog", opn["text"])
        lines = wf.servicenow_war_lines(r)
        self.assertEqual([x.split(":")[0] for x in lines["completed"]], ["Catalog task SCTASK1", "Incident INC1", "Change CHG1"])      # not INC6
        self.assertEqual([x.split(":")[0] for x in lines["in_progress"]], ["Incident INC3", "Incident INC2", "Catalog task SCTASK2"])   # not INC8
        ev = wf.servicenow_war_act_evidence(r)
        self.assertIn("OPEN AT THE END OF THE WEEK (3 listed):", ev)
        self.assertIn("and 1 more open task(s) not touched this week (the backlog; the oldest open 95 days)", ev)
        self.assertIn("CANCELLED IN THE WEEK (1) - not accomplishments, not in progress:\nINC6 | Incident | Work on INC6 | Canceled 2026-10-19 | Jane Doe", ev)
        self.assertNotIn("INC8", ev)
        # servicenow_war_open_scope: all - every open task, the oldest first
        rep = wf.servicenow_war_report(r, {"old_days": 30, "open_scope": "all"})
        opn = dict((s["title"], s) for s in rep["sections"])["Open at the end of the week"]
        self.assertEqual([x[1] for x in opn["rows"]], ["INC8", "INC3", "INC2", "SCTASK2"])
        self.assertNotIn("backlog", opn["text"])
        self.assertEqual(wf.servicenow_war_lines(r, {"open_scope": "all"})["in_progress"][0].split(":")[0], "Incident INC8")
        self.assertIn("OPEN AT THE END OF THE WEEK (4):", wf.servicenow_war_act_evidence(r, {"open_scope": "all"}))
        # the workbook has everything: the Cancelled sheet, the backlog on Open at the end
        sheets = wf.servicenow_war_xlsx(r)
        self.assertEqual([(s["name"], len(s["rows"])) for s in sheets],
                         [("All items", 8), ("Completed", 3), ("Opened", 3), ("Open at the end", 4), ("Cancelled", 1), ("By person", 3), ("By type", 3)])
        allx = sheets[0]
        row = dict(zip(allx["columns"], allx["rows"][[x[0] for x in allx["rows"]].index("INC6")]))
        self.assertEqual((row["Completed this week"], row["Cancelled this week"], row["Completed"], row["State"]), ("no", "yes", "2026-10-19 09:00", "Canceled"))
        self.assertEqual(sheets[4]["rows"][0][:2], ["INC6", "Incident"])
        # nothing but the backlog open: said in words
        # opened and cancelled in the week: only under Cancelled - not in Opened, its tile, the numbers, By person, the workbook's Opened
        both = dict(WEEK, incident=WEEK["incident"] + [rec("incident", "INC9", "2026-10-20 10:00:00", closed="2026-10-21 09:00:00", state=("8", "Canceled"))])
        r = war.read_week(FakeApi(both), ["Linux Operations"], [], ["incident", "sc_task", "change_request"], war.window("days", 7, now=NOW))
        self.assertEqual((r["counts"]["opened"], r["counts"]["cancelled"]), (3, 1))
        rep = wf.servicenow_war_report(r, {"old_days": 30})
        self.assertIn(": 3 completed, 3 opened,", rep["title"])
        self.assertEqual([t["value"] for t in rep["summary"] if t["label"] in ("Opened", "Cancelled")], [3, 1])
        secs = dict((s["title"], s) for s in rep["sections"])
        self.assertNotIn("INC9", [x[1] for x in secs["Opened this week"]["rows"]])
        self.assertIn("Opened and cancelled in the week: only under Cancelled this week (1).", secs["Opened this week"]["text"])
        self.assertEqual(secs["The week in numbers"]["rows"][-1][:4], ["Total", 3, 3, 1])
        self.assertEqual([x[1] for x in secs["Cancelled this week"]["rows"]], ["INC9"])
        self.assertEqual(len(dict((s["name"], s) for s in wf.servicenow_war_xlsx(r))["Opened"]["rows"]), 3)
        self.assertNotIn("INC9 |", wf.servicenow_war_act_evidence(r).split("CANCELLED IN THE WEEK")[0])
        quiet = {"incident": [self.EXTRA[1], WEEK["incident"][0]]}
        rep = wf.servicenow_war_report(war.read_week(FakeApi(quiet), ["Linux Operations"], [], ["incident"], war.window("days", 7, now=NOW)))
        opn = dict((s["title"], s) for s in rep["sections"])["Open at the end of the week"]
        self.assertTrue(opn["lines"][0].startswith("No open task was new this week or worked on in it. 1 more open task(s)"), opn["lines"])


if __name__ == "__main__":
    unittest.main()
