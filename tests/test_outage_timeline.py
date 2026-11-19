#!/usr/bin/env python3
"""1.66.0 the outage review's time line (plugins/filter/outage_timeline_filters.py): the window a review reads, a
problem and its clear (the flap rule, vm_watch_review_low_seconds), HA's restarts as one event, how long a host was
down, the headline, the tickets, their work notes and the ACT analysis, the three numbers (first signal, signal to
ticket, time down), the tiles and cards, the chart (zoom, ticks, marks that never overlap, the overview), the workbook's
Timeline sheet, the job output, the lines for the after-action report, outage_timeline_doc for a review and for a
normal run, the event types the module reads, the ticket reader (GET only) and the time line in the looks.
    python3 tests/test_outage_timeline.py"""
import ast
import copy
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
sys.path.insert(0, os.path.join(REPO, "roles", "vmware_vm", "library"))
sys.path.insert(0, os.path.join(HERE, "servicenow"))
sys.path.insert(0, HERE)

import outage_timeline_filters as ot  # noqa: E402

H = "esx03.example.mil"
DAY = "2026-11-12T"


def ev(t, typ, host="", vm="", msg="", **kw):
    d = {"key": ev.k, "time": DAY + t + "Z", "type": typ, "message": msg, "args": {}, "host": host, "vm": vm,
         "cluster": "cluster1", "datacenter": "DC1", "vcenter": "vc01.example.mil", "alarm": "", "entity": "", "from": "", "to": ""}
    d.update(kw)
    ev.k += 1
    return d


ev.k = 4700


def alarm(t, name, to, was):
    return ev(t, "AlarmStatusChangedEvent", alarm=name, entity=H, to=to, **{"from": was})


def events():
    """esx03 loses its storage paths at night: a short flap, the redundancy, all paths, the host, HA, and back."""
    return [
        ev("00:41:10", "esx.problem.storage.redundancy.degraded", H, msg="Path redundancy to storage device naa.600a degraded"),
        ev("00:41:50", "esx.clear.storage.redundancy.restored", H, msg="Path redundancy restored"),
        ev("00:58:02", "esx.problem.storage.redundancy.lost", H, msg="Lost path redundancy to storage device naa.600a"),
        alarm("00:58:30", "Host storage path redundancy", "yellow", "green"),
        ev("01:19:04", "esx.problem.storage.apd.start", H, msg="Device naa.600a has entered the All Paths Down state"),
        ev("01:19:40", "HostConnectionLostEvent", H, msg="Host esx03.example.mil is not responding"),
        alarm("01:19:55", "Host connection and power state", "red", "green"),
        ev("01:20:30", "com.vmware.vc.HA.DasHostFailedEvent", H, msg="vSphere HA detected a host failure"),
    ] + [ev("01:21:%02d" % (5 + i * 3), "com.vmware.vc.ha.VmRestartedByHAEvent", "esx01.example.mil", vm="app%02d" % (i + 10))
         for i in range(14)] + [
        ev("02:05:12", "HostConnectedEvent", H, msg="Connected to esx03.example.mil"),
        ev("02:05:40", "esx.clear.storage.apd.exit", H, msg="Device naa.600a has exited the All Paths Down state"),
        ev("02:06:10", "esx.clear.storage.redundancy.restored", H, msg="Path redundancy restored"),
        alarm("02:06:30", "Host connection and power state", "green", "red"),
        alarm("02:06:45", "Host storage path redundancy", "green", "yellow"),
    ]


ACT = ("The host lost every path to its storage device before vCenter lost the host. Check the fabric switch port of the "
       "host's second adapter.")


def tickets():
    return [{"sys_id": "a1", "number": "INC0012345", "short": "[site-health] esx03.example.mil: host not responding",
             "description": "Host: esx03.example.mil\nCheck: vm_watch\nSeverity: critical\nFinding: host not responding\n\n"
                            "ACT (GenAI) analysis - level 2, status ok:\n%s\n" % ACT,
             "opened": DAY + "02:14:00Z", "resolved": DAY + "02:40:00Z", "closed": "",
             "close_notes": "Fibre channel cable of the second adapter reseated; paths back.", "state": "6", "active": True,
             "correlation_id": "site-health:1a2b3c4d:vm_watch",
             "notes": [[DAY + "02:22:00Z", "Still failing at 02:22"], [DAY + "02:25:00Z", "On site, checking the cabling of esx03."],
                       [DAY + "02:35:00Z", "The automated check passes again at 02:35 UTC."]]}]


OPTS = {"begin": "2026-11-11T20:45:00Z", "end": DAY + "02:45:00Z", "time_zone": "Europe/Berlin", "low_seconds": 120,
        "tickets_asked": True, "report_on": ["critical"], "min_severity": "warning"}


def made(evs=None, tks=None, **kw):
    return ot.outage_timeline(events() if evs is None else evs, tickets() if tks is None else tks, dict(OPTS, **kw))


def whats(r):
    return [row[4] for row in r["sections"][1 if r["cards"] else 0]["rows"]]


WATCH = {"title": "VM outage watch: nothing down now", "status": "ok", "subtitle": "vc01.example.mil - 3 hosts, 41 VMs",
         "summary": [{"label": "Hosts down", "value": 0, "status": "ok"}, {"label": "VMs down or hung", "value": 0, "status": "ok"}],
         "sections": [{"title": "Down or hung now", "status": "ok", "lines": ["Nothing is down or hung."]},
                      {"title": "How it judged", "collapsed": True, "lines": ["A host is down when vCenter says it is not responding."]}],
         "footer": "AAP job 4714 - VM - outage watch"}


class Window(unittest.TestCase):
    NOW = 1794451500.0                                # 2026-11-12T02:45:00Z

    def test_the_last_hours(self):
        self.assertEqual(ot.outage_window(self.NOW), {"begin": self.NOW - 6 * 3600, "end": self.NOW, "error": ""})
        self.assertEqual(ot.outage_window(self.NOW, 2)["begin"], self.NOW - 7200)
        self.assertEqual(ot.outage_window(self.NOW, "x")["begin"], self.NOW - 6 * 3600)       # not a number: the default
        self.assertEqual(ot.outage_window(self.NOW, 0)["begin"], self.NOW - 900)              # at least a quarter of an hour
        self.assertEqual(ot.outage_window(self.NOW, 99999)["begin"], self.NOW - 31 * 86400)   # at most a month

    def test_from_and_to_in_the_reports_zone(self):
        w = ot.outage_window(self.NOW, 6, "2026-11-12 01:00", "2026-11-12 03:30", "Europe/Berlin")
        self.assertEqual((w["begin"], w["end"], w["error"]), (self.NOW - 2.75 * 3600, self.NOW - 900, ""))
        w = ot.outage_window(self.NOW, 6, "2026-11-12 00:00", "2026-11-12T02:30:00")          # no zone: UTC
        self.assertEqual((w["begin"], w["end"]), (self.NOW - 2.75 * 3600, self.NOW - 900))
        w = ot.outage_window(self.NOW, 1, "2026-11-11", "", "Europe/Berlin")                  # a day alone: its midnight
        self.assertEqual((w["begin"], w["end"]), (self.NOW - 27.75 * 3600, self.NOW - 26.75 * 3600))
        w = ot.outage_window(self.NOW, 6, "2026-11-12 01:00", "", "Europe/Berlin")            # from alone: at most until now
        self.assertEqual((w["begin"], w["end"]), (self.NOW - 2.75 * 3600, self.NOW))

    def test_what_is_refused(self):
        for start, end, want in (
                ("yesterday", "", "vm_watch_review_from is not a time like 2026-11-12 01:00: yesterday"),
                ("2026-11-31 01:00", "", "vm_watch_review_from is not a time like 2026-11-12 01:00: 2026-11-31 01:00"),
                ("2026-11-12 01:00", "3 am", "vm_watch_review_to is not a time like 2026-11-12 01:00: 3 am"),
                ("", "2026-11-12 03:00", "vm_watch_review_to needs vm_watch_review_from (the start of the window)"),
                ("2026-11-12 03:00", "2026-11-12 01:00",
                 "vm_watch_review_to is not after vm_watch_review_from (or vm_watch_review_from is not in the past)"),
                ("2026-11-13 09:00", "", "vm_watch_review_to is not after vm_watch_review_from (or vm_watch_review_from is not in "
                                         "the past)")):
            w = ot.outage_window(self.NOW, 6, start, end, "Europe/Berlin")
            self.assertEqual(w["error"], want)
            self.assertEqual((w["begin"], w["end"]), (self.NOW - 6 * 3600, self.NOW))          # still a window: nothing breaks


class Parts(unittest.TestCase):
    def test_a_length_of_time(self):
        self.assertEqual([ot._span(s) for s in (0, 40, 89, 90, 2732, 7200, 7500, 100000, 3 * 86400 + 4 * 3600)],
                         ["0 s", "40 s", "89 s", "2 min", "46 min", "2 h", "2 h 05 min", "27 h 47 min", "3 d 4 h"])

    def test_a_host_as_a_reader_says_it(self):
        self.assertEqual([ot._short(x) for x in ("esx03.example.mil", "esx03", "192.0.2.14", "2001:db8::14", "", None)],
                         ["esx03", "esx03", "192.0.2.14", "2001:db8::14", "", ""])
        self.assertEqual(ot._ticket_host("[site-health] esx03.example.mil: host not responding"), "esx03")
        self.assertEqual(ot._ticket_host("printer broken"), "")

    def test_the_act_analysis_of_a_description(self):
        self.assertEqual(ot._act_block(tickets()[0]["description"]), ACT)
        self.assertEqual(ot._act_block("Host: esx03\nFinding: x"), "")
        self.assertEqual(ot._act_block("ACT analysis:\n\nline one\nline two\n\nmore below"), "line one line two")

    def test_the_title_keeps_a_short_name_capital(self):
        self.assertEqual(ot._lower_first("Host not responding"), "host not responding")
        self.assertEqual(ot._lower_first("HA restarted 3 VMs"), "HA restarted 3 VMs")

    def test_the_types_are_the_ones_the_module_reads(self):
        with open(os.path.join(REPO, "roles", "vmware_vm", "library", "site_vmware_outage_events.py")) as f:
            src = f.read()
        node = next(n for n in ast.parse(src).body if isinstance(n, ast.Assign) and n.targets[0].id == "EVENT_TYPES")
        types = ast.literal_eval(node.value)
        self.assertEqual(len(types), len(set(types)))
        self.assertEqual(sorted(types), sorted(ot.TYPES))
        for typ, (lane, level, label, kind, role) in ot.TYPES.items():
            self.assertIn(lane, ot.LANE, typ)
            self.assertIn(level, ot.WORD, typ)
            self.assertTrue(label, typ)
            self.assertIn(role, ("", "down", "up", "problem", "clear", "restart"), typ)
            self.assertEqual(bool(kind), role in ("down", "up", "problem", "clear") or typ == "AlarmStatusChangedEvent", typ)
        for kind in set(v[3] for v in ot.TYPES.values()) - {"", "alarm"}:                     # every problem can clear
            roles = set(v[4] for v in ot.TYPES.values() if v[3] == kind)
            self.assertIn(roles, ({"problem", "clear"}, {"down", "up"}), kind)


class Review(unittest.TestCase):
    def test_the_night_of_the_fixture(self):
        r = made()
        self.assertEqual(r["title"], "Outage review: esx03: storage path redundancy lost; 46 min down, ticket after 93 min")
        self.assertEqual(r["numbers"], {
            "events": 18, "first_signal": DAY + "00:41:10Z", "ticket": "INC0012345", "signal_to_ticket_s": 5570, "down_s": 2732,
            "hosts_down": ["esx03"], "still_down": [], "ha_restarted": 14, "back": DAY + "02:06:45Z"})
        self.assertEqual(r["window"], "2026-11-11 21:45 CET to 2026-11-12 03:45 CET")
        self.assertEqual(whats(r), [
            "Storage path redundancy degraded, back after 40 s", "Storage path redundancy lost",
            "Alarm yellow: Host storage path redundancy", "All paths down to a storage device", "Host not responding",
            "Alarm red: Host connection and power state", "HA: host failed", "HA restarted 14 VMs", "Host connected again",
            "Storage paths are back", "Storage path redundancy is back", "Alarm back to green: Host connection and power state",
            "Alarm back to green: Host storage path redundancy", "Ticket INC0012345 opened", "ACT analysis in INC0012345: " + ACT,
            "Work note in INC0012345: On site, checking the cabling of esx03.", "INC0012345: the check passes again",
            "Ticket INC0012345 resolved: Fibre channel cable of the second adapter reseated; paths back."])
        t = r["sections"][1]
        self.assertEqual(t["columns"], ["#", "Time (CET)", "Source", "Where", "What happened", "Level"])
        self.assertEqual(t["rows"][0], [1, "12 Nov 01:41:10", "Host events", "esx03", whats(r)[0], "Low"])
        self.assertEqual(t["rows"][7][:4], [8, "12 Nov 02:21:05", "vCenter alarms", "cluster1"])
        self.assertEqual([x[2] for x in t["rows"][13:]], ["ServiceNow", "ACT", "ServiceNow", "ServiceNow", "ServiceNow"])
        self.assertEqual(t["row_status"][:8], ["low", "warning", "warning", "critical", "critical", "critical", "critical", "warning"])
        self.assertEqual(t["row_status"][8:13], [""] * 5)                    # status never by colour alone: the word is a cell
        self.assertEqual([c[5] for c in t["cell_status"]][:2], ["low", "warning"])
        self.assertIn("A problem that cleared within 2 min is one Low row (vm_watch_review_low_seconds).", t["text"])

    def test_the_flap_rule(self):
        e = [ev("01:00:00", "esx.problem.net.redundancy.lost", H), ev("01:02:00", "esx.clear.net.redundancy.restored", H),
             ev("01:10:00", "esx.problem.net.redundancy.lost", H), ev("01:12:01", "esx.clear.net.redundancy.restored", H),
             ev("01:20:00", "esx.problem.net.redundancy.lost", "esx04.example.mil"),
             ev("01:20:30", "esx.clear.storage.redundancy.restored", "esx04.example.mil")]     # another kind: no pair
        r = made(e, [])
        self.assertEqual(whats(r), ["Uplink redundancy lost, back after 2 min", "Uplink redundancy lost", "Uplink redundancy is back",
                                    "Uplink redundancy lost", "Storage path redundancy is back"])
        self.assertEqual([x[5] for x in r["sections"][1]["rows"]], ["Low", "Warning", "Back to normal", "Warning", "Back to normal"])
        r = made(e, [], low_seconds=0)                                        # 0: the rule is off, a row each
        self.assertEqual(len(whats(r)), 6)
        self.assertNotIn("Low", [x[5] for x in r["sections"][1]["rows"]])
        self.assertIn("A problem and its clear are a row each (vm_watch_review_low_seconds is 0).", r["sections"][1]["text"])
        r = made(e, [], low_seconds="x")                                      # not a number: the default
        self.assertEqual(len(whats(r)), 5)
        self.assertEqual(len(whats(made(e, [], low_seconds=600))), 4)

    def test_a_host_that_flapped_was_not_down(self):
        e = [ev("01:00:00", "HostConnectionLostEvent", H), ev("01:00:20", "HostConnectedEvent", H)]
        r = made(e, [])
        self.assertEqual(whats(r), ["Host not responding, back after 20 s"])
        self.assertEqual((r["numbers"]["down_s"], r["numbers"]["hosts_down"]), (0, []))
        self.assertEqual(r["boxes"][2]["value"], "0")
        self.assertEqual(r["boxes"][2]["status"], "ok")
        r = made(e, [], low_seconds=0)
        self.assertEqual((r["numbers"]["down_s"], r["numbers"]["hosts_down"]), (20, ["esx03"]))
        self.assertEqual(r["title"], "Outage review: esx03: host not responding; 20 s down, no ticket")

    def test_ha_restarts_are_one_event_per_cluster(self):
        e = [ev("01:21:%02d" % i, "com.vmware.vc.ha.VmRestartedByHAEvent", vm="app%02d" % i) for i in range(3)]
        e += [ev("01:21:30", "VmRestartedOnAlternateHostEvent", vm="app00"),                   # the same VM again: counted once
              ev("01:22:00", "com.vmware.vc.ha.VmRestartedByHAEvent", vm="db01", cluster="cluster3"),
              ev("01:40:00", "com.vmware.vc.ha.VmRestartedByHAEvent", vm="app09")]             # over five minutes later: a new event
        r = made(e, [])
        self.assertEqual(whats(r), ["HA restarted 3 VMs", "HA restarted 1 VM", "HA restarted 1 VM"])
        self.assertEqual([x[3] for x in r["sections"][1]["rows"]], ["cluster1", "cluster3", "cluster1"])
        self.assertEqual(r["numbers"]["ha_restarted"], 5)
        self.assertEqual(r["title"], "Outage review: cluster1: HA restarted 3 VMs; no ticket")
        row = r["sheet"]["rows"][0]
        self.assertEqual((row[7], row[11]), (3, "app00, app01, app02: "))

    def test_still_down_at_the_end(self):
        e = [x for x in events() if x["time"] < DAY + "02:00:00Z"]
        r = made(e, [])
        self.assertEqual(r["title"], "Outage review: esx03: storage path redundancy lost; 85 min down, still down, no ticket")
        self.assertEqual(r["numbers"]["still_down"], ["esx03"])
        self.assertEqual(r["numbers"]["back"], "")
        self.assertEqual(r["boxes"][2]["note"], "esx03; still down at the end of the window")
        self.assertEqual((r["cards"][2]["status"], r["cards"][2]["meta"], r["cards"][2]["title"]),
                         ("critical", "not yet", "esx03 still down at the end of the window"))
        band = r["web_sections"][1]["outage"]["bands"][0]
        self.assertEqual(band["text"], "esx03 down: 85 min, still down")
        self.assertEqual(r["aar"][1], "End (incident_end): not yet - esx03 still down at the end of the window")

    def test_two_hosts(self):
        e = [ev("01:00:00", "HostConnectionLostEvent", H), ev("01:01:00", "HostConnectionLostEvent", "esx04.example.mil"),
             ev("01:30:00", "HostConnectedEvent", "esx04.example.mil"), ev("02:00:00", "HostConnectedEvent", H)]
        r = made(e, [])
        self.assertEqual(r["numbers"]["hosts_down"], ["esx03", "esx04"])
        self.assertEqual(r["numbers"]["down_s"], 3600)                        # the longest
        chart = r["web_sections"][1]["outage"]
        self.assertEqual(chart["band_word"], "2 hosts down: up to 60 min")
        self.assertEqual([b["text"] for b in chart["bands"]], ["esx03 down: 60 min", "esx04 down: 29 min"])

    def test_the_headline(self):
        # an alarm and the host's own event in the same minute: the host's event says more
        e = [alarm("01:19:30", "Host connection and power state", "red", "green"), ev("01:19:40", "HostConnectionLostEvent", H)]
        self.assertTrue(made(e, [])["title"].startswith("Outage review: esx03: host not responding;"))
        # a warning first, the critical one over a minute later: the first problem leads
        e = [ev("01:00:00", "esx.problem.storage.redundancy.lost", H), ev("01:19:40", "HostConnectionLostEvent", H)]
        self.assertTrue(made(e, [])["title"].startswith("Outage review: esx03: storage path redundancy lost;"))
        # only a flap: it leads, as the Low it is
        e = [ev("01:00:00", "esx.problem.storage.redundancy.lost", H), ev("01:00:30", "esx.clear.storage.redundancy.restored", H)]
        self.assertEqual(made(e, [])["title"], "Outage review: esx03: storage path redundancy lost, back after 30 s; no ticket")
        # nothing a problem
        e = [ev("01:00:00", "EnteredMaintenanceModeEvent", H), ev("01:30:00", "ExitMaintenanceModeEvent", H)]
        r = made(e, [])
        self.assertEqual(r["title"], "Outage review: 2 events, none a problem")
        self.assertEqual(r["cards"], [])
        self.assertEqual(r["aar"], [])
        self.assertEqual([s["title"] for s in r["web_sections"]], ["What happened, in order", "The events"])

    def test_an_event_the_filter_does_not_know_and_a_bad_one(self):
        e = [ev("01:00:00", "esx.problem.something.new", H, msg="  Something   new happened "), ev("01:01:00", "vc.only", msg=""),
             {"time": "not a time", "type": "HostConnectionLostEvent"}, "text", None,
             ev("09:00:00", "HostConnectionLostEvent", H)]                    # after the window
        r = made(e, [])
        self.assertEqual([x[2:5] for x in r["sections"][0]["rows"]],
                         [["Host events", "esx03", "Something new happened"], ["vCenter alarms", "cluster1", "vc.only"]])
        self.assertEqual(ot.outage_timeline(e, [], {"begin": "x", "end": OPTS["end"]}), {})
        self.assertEqual(ot.outage_timeline(e, [], dict(OPTS, begin=OPTS["end"])), {})

    def test_the_same_event_again_within_a_minute(self):
        e = [ev("01:00:%02d" % s, "esx.problem.vmfs.heartbeat.unrecoverable", H) for s in (0, 20, 50)]
        e.append(ev("01:03:00", "esx.problem.vmfs.heartbeat.unrecoverable", H))
        r = made(e, [])
        self.assertEqual(whats(r), ["Datastore heartbeat lost for good (x3)", "Datastore heartbeat lost for good"])
        self.assertEqual(r["sheet"]["rows"][0][5:8], ["Datastore heartbeat lost for good", "Critical", 3])


class Tickets(unittest.TestCase):
    def test_signal_to_ticket_and_who_resolved(self):
        r = made()
        self.assertEqual([(b["label"], b["value"], b["status"]) for b in r["boxes"]], [
            ("Window", "6 h", "info"), ("Events", 18, "info"), ("Host down", "46 min", "critical"),
            ("VMs restarted by HA", 14, "warning"), ("Signal to ticket", "93 min", "warning")])
        self.assertEqual(r["boxes"][1]["note"], "13 on the vCenter and host lanes")
        self.assertEqual(r["boxes"][4]["note"], "INC0012345, resolved 12 Nov 03:40")
        self.assertEqual([(c["chip"], c["status"], c["meta"]) for c in r["cards"]], [
            ("First signal", "low", "12 Nov 01:41:10 CET"), ("First signal to ticket", "warning", "93 min"),
            ("Back to normal", "ok", "12 Nov 03:06:45 CET")])
        self.assertEqual(r["cards"][0]["next"], "Row 1 of the events. Host events lane.")
        self.assertEqual(r["cards"][1]["title"], "INC0012345 opened at 12 Nov 03:14 CET")
        self.assertEqual(r["cards"][2]["next"], "Row 13 of the events, 86 min after the first signal.")

    def test_a_quick_ticket_is_ok(self):
        t = tickets()
        t[0].update(opened=DAY + "00:50:00Z", resolved="", closed=DAY + "02:30:00Z", close_notes="")
        r = made(None, t)
        self.assertEqual((r["boxes"][4]["value"], r["boxes"][4]["status"]), ("9 min", "ok"))
        self.assertIn("Ticket INC0012345 closed", whats(r))
        self.assertEqual(len(r["aar"]), 4)                                    # no follow-up line: the ticket came in time

    def test_work_notes(self):
        t = tickets()
        t[0]["notes"] += [[DAY + "02:36:00Z", "ACT (GenAI) follow-up: the path count is back to four."], ["bad", "x"], ["", ""],
                          "text", [DAY + "02:37:00Z", "  "], [DAY + "05:00:00Z", "after the window"]]
        w = whats(made(None, t))
        self.assertNotIn("Still failing", " ".join(w))                        # the watch's own repeat note: left out
        self.assertIn("ACT note in INC0012345: ACT (GenAI) follow-up: the path count is back to four.", w)
        self.assertNotIn("after the window", " ".join(w))
        self.assertEqual(len(w), 19)

    def test_a_ticket_added_by_number_before_the_first_signal(self):
        old = dict(tickets()[0], sys_id="b2", number="INC0012300", short="Fabric switch firmware", description="",
                   opened="2026-11-11T22:00:00Z", resolved="", close_notes="", notes=[])
        r = made(None, [old] + tickets())
        self.assertEqual(r["numbers"]["ticket"], "INC0012345")                # the outage's ticket: the first after the signal
        self.assertEqual(r["numbers"]["signal_to_ticket_s"], 5570)
        self.assertEqual(whats(r)[0], "Ticket INC0012300 opened")
        r = made(None, [old])                                                 # only the old one: nothing to measure
        self.assertEqual(r["numbers"]["signal_to_ticket_s"], -1)
        self.assertEqual((r["boxes"][4]["value"], r["boxes"][4]["note"]), ("-", "ticket opened before the first signal"))
        self.assertTrue(r["title"].endswith("46 min down, no ticket"))

    def test_a_ticket_open_before_the_window(self):
        t = tickets()
        t[0]["opened"] = "2026-11-11T08:00:00Z"
        r = made(None, t)
        self.assertEqual(r["boxes"][4]["note"], "ticket INC0012345 was open before the window")
        self.assertTrue(r["title"].endswith("46 min down"))                   # not "no ticket": there is one
        self.assertIn("Open before the window: INC0012345.", r["lines"])
        self.assertIn("Open before the window: INC0012345.", r["sections"][1]["text"])

    def test_servicenow_not_read(self):
        r = made(None, [], tickets_asked=False)
        self.assertEqual(r["lines"][-1], "ServiceNow was not read: attach the ServiceNow API credential to the job template. "
                                         "Attach the ServiceNow API credential to the outage watch template to see and link its incidents (read only).")
        self.assertEqual(r["cards"][1]["next"], r["lines"][-1])
        self.assertTrue(r["title"].endswith("46 min down"))                   # nobody looked: not "no ticket"
        lanes = r["web_sections"][1]["outage"]["lanes"]
        self.assertEqual([x["sub"] for x in lanes[2:]], ["not read: attach the ServiceNow API credential"] * 2)
        r = made(None, [], tickets_error="not read: GET api/now/table/incident: HTTP 401")
        self.assertIn("ServiceNow: not read: GET api/now/table/incident: HTTP 401.", r["lines"])
        self.assertEqual(r["boxes"][4]["note"], "not read: GET api/now/table/incident: HTTP 401")
        self.assertTrue(r["title"].endswith("46 min down"))
        r = made(None, None, notes_error="GET api/now/table/sys_journal_field: HTTP 403", capped=["vc01.example.mil"])
        self.assertIn("ServiceNow work notes: GET api/now/table/sys_journal_field: HTTP 403.", r["lines"])
        self.assertIn("vCenter had more events than the review reads (vc01.example.mil): the oldest are left out. Shorten the "
                      "window with vm_watch_review_hours.", r["lines"])


class Chart(unittest.TestCase):
    def test_zoom_ticks_band_and_overview(self):
        c = made()["web_sections"][1]["outage"]
        self.assertEqual(c["label"], "Outage time line, 12 Nov 01:30 to 12 Nov 03:45 CET")
        self.assertEqual([t["text"] for t in c["ticks"]], ["12 Nov 01:30", "02:00", "02:30", "03:00", "03:30"])
        self.assertEqual(c["ticks"][0]["at"], 0.0)
        self.assertEqual(c["bands"], [{"from": 36.79, "to": 70.52, "text": "esx03 down: 46 min"}])
        self.assertEqual(c["band_word"], "esx03 down: 46 min")
        self.assertEqual((c["overview"]["from"], c["overview"]["to"]), (62.5, 100.0))
        self.assertEqual(len(c["overview"]["marks"]), 18)
        self.assertEqual((c["overview"]["begin"], c["overview"]["end"]), ("11 Nov 21:45", "12 Nov 03:45"))
        self.assertEqual([x["word"] for x in c["legend"]], ["Critical", "Warning", "Low", "Note", "Back to normal"])
        self.assertEqual(c["zone"], "CET")

    def test_no_overview_when_the_events_fill_the_window(self):
        c = made(begin=DAY + "00:00:00Z")["web_sections"][1]["outage"]
        self.assertIsNone(c["overview"])
        self.assertEqual(c["label"], "Outage time line, 01:30 to 03:45 CET")  # one day: no date in the times

    def test_lanes_and_marks(self):
        c = made()["web_sections"][1]["outage"]
        self.assertEqual([(x["key"], x["name"], x["sub"], len(x["marks"]), x["rows"]) for x in c["lanes"]], [
            ("vcenter", "vCenter alarms", "5 events", 5, 2), ("host", "Host events", "8 events", 8, 3),
            ("servicenow", "ServiceNow", "4 events", 4, 2), ("act", "ACT", "1 event", 1, 1)])
        nums = sorted(n for x in c["lanes"] for m in x["marks"] for n in m["nums"])
        self.assertEqual(nums, list(range(1, 19)))                            # every row of the events is on the chart
        m = c["lanes"][1]["marks"][0]
        self.assertEqual(m["tip"], "1  12 Nov 01:41:10 CET  Low\nStorage path redundancy degraded, back after 40 s - esx03")
        self.assertEqual((m["label"], m["level"], m["row"]), ("1", "low", 0))

    def assert_no_overlap(self, lanes):
        for lane in lanes:
            self.assertLessEqual(lane["rows"], ot.ROWS)
            for row in range(ot.ROWS):
                at = -1.0
                for m in sorted((m for m in lane["marks"] if m["row"] == row), key=lambda m: m["x"]):
                    w = (30 + 7.5 * len(m["label"])) * 100.0 / ot.TRACK_PX
                    self.assertGreaterEqual(m["x"] - w / 2, at, (lane["key"], m["label"]))
                    self.assertGreaterEqual(m["x"] - w / 2, 0)
                    self.assertLessEqual(m["x"] + w / 2, 100.0001)
                    at = m["x"] + w / 2

    def test_marks_never_overlap(self):
        self.assert_no_overlap(made()["web_sections"][1]["outage"]["lanes"])
        # a storm: 40 different problems of one host within four minutes
        kinds = [t for t, v in ot.TYPES.items() if v[0] == "host" and v[4] in ("problem", "")]
        e = [ev("01:%02d:%02d" % (i // 10, i % 10 * 6), kinds[i % len(kinds)], "esx%02d.example.mil" % (i % 5 + 3)) for i in range(40)]
        e += [ev("02:30:00", "HostConnectionLostEvent", H)]
        c = made(e, [])["web_sections"][1]["outage"]
        self.assert_no_overlap(c["lanes"])
        host = c["lanes"][1]
        self.assertLess(len(host["marks"]), 41)                               # some joined the mark before them
        joined = [m for m in host["marks"] if m.get("more")]
        self.assertTrue(joined)
        for m in joined:
            self.assertEqual(m["label"], "%d+%d" % (m["nums"][0], m["more"]))
            self.assertEqual(len(m["nums"]), m["more"] + 1)
            self.assertEqual(m["tip"].count("\n"), 2 * len(m["nums"]) - 1)    # its tip lists each
        self.assertEqual(sorted(n for m in host["marks"] for n in m["nums"]), list(range(1, 42)))
        self.assertEqual(max(ot.RANK[m["level"]] for m in host["marks"]), 4)

    def test_ticks_on_round_times(self):
        import datetime
        tz = ot._tm.zone("Europe/Berlin")[0]
        z0 = datetime.datetime(2026, 11, 11, 20, 45, tzinfo=ot.UTC)
        for hours, step, n in ((0.5, 5, 7), (6, 60, 6), (48, 360, 8), (24 * 31, 10080, 4)):
            got_step, ticks = ot._ticks(z0, z0 + datetime.timedelta(hours=hours), tz)
            self.assertEqual((got_step, len(ticks)), (step, n), hours)
            self.assertTrue(all(z0 <= t <= z0 + datetime.timedelta(hours=hours) for t in ticks))


class Outputs(unittest.TestCase):
    def test_the_sheet(self):
        s = made()["sheet"]
        self.assertEqual(s["name"], "Timeline")
        self.assertEqual(s["columns"], ["#", "Time (UTC)", "Time (Europe/Berlin)", "Source", "Where", "What happened", "Level",
                                        "Count", "Ticket", "vCenter", "Event type", "Message"])
        self.assertEqual(len(s["rows"]), 18)
        self.assertEqual(s["rows"][0], [1, "2026-11-12 00:41:10", "2026-11-12 01:41:10 CET", "Host events", "esx03",
                                        "Storage path redundancy degraded, back after 40 s", "Low", 1, "", "vc01.example.mil",
                                        "esx.problem.storage.redundancy.degraded", "Path redundancy to storage device naa.600a degraded"])
        self.assertEqual(s["rows"][7][7], 14)
        self.assertTrue(s["rows"][7][11].startswith("app10, app11, "))
        self.assertEqual(s["rows"][14][8:12], ["INC0012345", "", "", ACT])    # the whole analysis, not the cut one
        self.assertEqual(made(time_zone="")["sheet"]["columns"][2], "Time (UTC)")

    def test_the_job_output(self):
        out = made()["lines"]
        self.assertEqual(out[0], "Outage time line, 2026-11-11 21:45 CET to 2026-11-12 03:45 CET: 18 event(s); first signal "
                                 "12 Nov 01:41:10; esx03 down: 46 min; ticket after 93 min")
        self.assertEqual(out[1], "   1  12 Nov 01:41:10  Host events    esx03            Storage path redundancy degraded, "
                                 "back after 40 s  [Low]")
        self.assertEqual(len(out), 19)
        e = [ev("01:%02d:00" % i, "VmFailoverFailed", vm="app%02d" % i) for i in range(59)]
        e += [ev("02:%02d:00" % i, "VmFailoverFailed", vm="db%02d" % i) for i in range(6)]
        out = made(e, [])["lines"]
        self.assertEqual(len(out), 62)
        self.assertEqual(out[-1], "  ... and 5 more event(s): the report's table and the workbook have every one.")

    def test_for_the_after_action_report(self):
        a = made()["aar"]
        self.assertEqual(a[0], "Start (incident_start): 2026-11-12 01:58 CET - Storage path redundancy lost on esx03")
        self.assertEqual(a[1], "End (incident_end): 2026-11-12 03:06 CET - Alarm back to green: Host storage path redundancy")
        self.assertEqual(a[2], "Detection (incident_detection): first signal at 12 Nov 01:41 (Host events: Storage path redundancy "
                               "degraded, back after 40 s); ticket INC0012345 opened at 12 Nov 03:14, 93 min later")
        self.assertEqual(a[3], "Root cause (incident_root_cause): check it before you paste - ACT in INC0012345: " + ACT)
        self.assertTrue(a[4].startswith("Follow-up: the ticket came 93 min after the first signal. The watch reports at "
                                        "vm_watch_report_on (now: critical) and a ticket opens at servicenow_min_severity (now: warning)."))
        t = tickets()
        t[0]["description"] = "Host: esx03.example.mil"
        self.assertEqual(made(None, t)["aar"][3], "Root cause (incident_root_cause): check it before you paste - Close notes of "
                                                  "INC0012345: Fibre channel cable of the second adapter reseated; paths back.")
        self.assertEqual(made(None, [])["aar"][3], "Root cause (incident_root_cause): not in the events - write what the fix showed")
        for text in " ".join(a + [x for b in made()["boxes"] for x in (b["label"], str(b["note"]))]).lower().split():
            self.assertNotIn(text.strip(".,:;()"), ("purchase", "buy", "cost"))

    def test_nothing_in_the_window(self):
        r = made([], [])
        self.assertEqual(r["title"], "Outage review: no outage event in the last 6 h")
        self.assertEqual(made([], [], fixed=True)["title"], "Outage review: no outage event in the window")
        self.assertEqual(r["numbers"], {"events": 0, "first_signal": "", "ticket": "", "signal_to_ticket_s": -1, "down_s": 0,
                                        "hosts_down": [], "still_down": [], "ha_restarted": 0, "back": ""})
        self.assertEqual(r["cards"], [])
        self.assertEqual(r["web_sections"], r["sections"])                    # no chart of nothing
        self.assertEqual(r["sections"], [{"title": "The events", "status": "ok",
                                          "lines": ["No outage event between 2026-11-11 21:45 CET to 2026-11-12 03:45 CET."]}])
        self.assertEqual(r["sheet"]["rows"], [])
        self.assertEqual([(b["value"], b["status"]) for b in r["boxes"][1:4]], [(0, "info"), ("0", "ok"), (0, "ok")])


class Doc(unittest.TestCase):
    def test_a_review(self):
        r = made()
        for web in (False, True):
            d = ot.outage_timeline_doc(copy.deepcopy(WATCH), r, web, True)
            self.assertEqual(d["title"], r["title"])
            self.assertEqual(d["subtitle"], "Review of 2026-11-11 21:45 CET to 2026-11-12 03:45 CET. Now: VM outage watch: nothing down now")
            self.assertEqual(d["summary"], r["boxes"])
            self.assertEqual((d["status"], d["footer"]), ("ok", WATCH["footer"]))   # the watch's own status: what is down now
            want = ["The outage in short"] + (["What happened, in order"] if web else []) + [
                "The events", "For the after-action report", "Down or hung now", "How it judged"]
            self.assertEqual([s["title"] for s in d["sections"]], want)

    def test_a_normal_run(self):
        r = made()
        d = ot.outage_timeline_doc(copy.deepcopy(WATCH), r, True, False)
        self.assertEqual((d["title"], d["summary"], d["subtitle"]), (WATCH["title"], WATCH["summary"], WATCH["subtitle"]))
        self.assertEqual([s["title"] for s in d["sections"]], ["Down or hung now", "What happened, in order", "The events", "How it judged"])
        d = ot.outage_timeline_doc(copy.deepcopy(WATCH), r, False, False)
        self.assertEqual([s["title"] for s in d["sections"]], ["Down or hung now", "The events", "How it judged"])

    def test_nothing_to_add(self):
        self.assertEqual(ot.outage_timeline_doc(WATCH, {}, True, True), WATCH)
        self.assertEqual(ot.outage_timeline_doc(WATCH, None), WATCH)
        self.assertEqual(ot.outage_timeline_doc({}, made()), {})
        before = copy.deepcopy(WATCH)
        ot.outage_timeline_doc(WATCH, made(), True, True)
        self.assertEqual(WATCH, before)                                       # the watch's own document is not changed


class TicketReader(unittest.TestCase):
    """roles/vmware_vm/library/site_outage_tickets.py against the fake ServiceNow's records (needs ansible-core importable)."""

    B, E = 1794430000.0, 1794451500.0                 # 2026-11-11 20:46:40 to 2026-11-12 02:45:00 UTC

    class Api(object):
        def __init__(self, recs, refuse=()):
            self.recs, self.refuse, self.calls = recs, refuse, []

        def get(self, path, params):
            import snmock
            table = path.split("/")[3]
            self.calls.append((table, params.get("sysparm_query", "")))
            if table in self.refuse:
                raise TicketReader.m.SnError("GET %s: HTTP 403: User Not Authorized" % path)
            rows = [x for x in self.recs if (x.get("_table") or "incident") == table and snmock.matches(x, params.get("sysparm_query", ""))]
            return [snmock.pick(x, params.get("sysparm_fields")) for x in rows][:int(params.get("sysparm_limit", 10000))]

    @classmethod
    def setUpClass(cls):
        try:
            import site_outage_tickets
        except ImportError as e:
            raise unittest.SkipTest("ansible-core is not importable here: %s" % e)
        cls.m = site_outage_tickets

    def recs(self):
        def inc(sys_id, number, opened, updated, corr, **kw):
            return dict({"sys_id": sys_id, "number": number, "short_description": "[site-health] esx03.example.mil:  host not responding",
                         "description": "Severity: critical", "opened_at": opened, "sys_created_on": opened, "sys_updated_on": updated,
                         "resolved_at": "", "closed_at": "", "close_notes": "", "state": "2", "active": "true", "correlation_id": corr}, **kw)

        def note(sys_id, when, text):
            return {"_table": "sys_journal_field", "element": "work_notes", "name": "incident", "element_id": sys_id, "value": text,
                    "sys_created_on": when}
        return [inc("a1", "INC0012345", "2026-11-12 02:14:00", "2026-11-12 02:40:00", "site-health:1a2b3c4d:vm_watch",
                    resolved_at="2026-11-12 02:40:00", state="6", close_notes=" cable reseated "),
                inc("a0", "INC0012001", "2026-11-01 08:00:00", "2026-11-02 08:00:00", "site-health:0000:certs"),   # long resolved
                inc("a3", "INC0012400", "2026-11-12 09:00:00", "2026-11-12 09:00:00", "site-health:ffff:vm_watch"),  # after the stretch
                inc("b1", "INC0012300", "2026-11-10 10:00:00", "2026-11-10 10:00:00", "", short_description="Fabric switch firmware"),
                inc("c1", "INC0012350", "2026-11-12 01:00:00", "2026-11-12 01:00:00", "", short_description="printer"),  # a person's
                note("a1", "2026-11-12 02:25:00", "On site."), note("a1", "2026-11-12 02:22:00", "Still failing at 02:22"),
                note("a1", "bad", "x"), note("a1", "2026-11-12 02:30:00", "  "), note("zz", "2026-11-12 02:30:00", "another ticket's")]

    def test_the_tickets_of_the_stretch_and_one_by_number(self):
        m, api = self.m, self.Api(self.recs())
        got = m.read_tickets(api, "incident", "site-health", [" INC0012300 ", "", None], self.B, self.E, 200)
        self.assertEqual([t["number"] for t in got], ["INC0012300", "INC0012345"])          # oldest first; the others left out
        self.assertEqual(got[1], {"sys_id": "a1", "number": "INC0012345", "short": "[site-health] esx03.example.mil: host not responding",
                                  "description": "Severity: critical", "opened": "2026-11-12T02:14:00Z", "resolved": "2026-11-12T02:40:00Z",
                                  "closed": "", "close_notes": "cable reseated", "state": "6", "active": True,
                                  "correlation_id": "site-health:1a2b3c4d:vm_watch", "notes": []})
        self.assertEqual(api.calls[0], ("incident", "correlation_idSTARTSWITHsite-health:^sys_created_on<=2026-11-12 02:45:00"
                                                    "^sys_updated_on>=2026-11-11 20:46:40^ORDERBYsys_created_on"))
        self.assertEqual(api.calls[1], ("incident", "numberININC0012300"))
        self.assertEqual(m.read_notes(api, "incident", got), "")
        self.assertEqual(got[1]["notes"], [["2026-11-12T02:22:00Z", "Still failing at 02:22"], ["2026-11-12T02:25:00Z", "On site."]])
        self.assertEqual(got[0]["notes"], [])
        self.assertEqual(api.calls[2][0], "sys_journal_field")
        self.assertIn("element_idINa1,b1", api.calls[2][1])
        api = self.Api(self.recs())
        self.assertEqual([t["number"] for t in m.read_tickets(api, "incident", "", [], self.B, self.E, 200)], [])
        self.assertEqual(api.calls, [])                                       # no prefix, no number: nothing asked
        self.assertEqual(len(m.read_tickets(self.Api(self.recs()), "incident", "site-health", ["INC0012300"], self.B, self.E, 1)), 1)

    def test_work_notes_refused(self):
        m, api = self.m, self.Api(self.recs(), refuse=("sys_journal_field",))
        got = m.read_tickets(api, "incident", "site-health", [], self.B, self.E, 200)
        self.assertEqual(m.read_notes(api, "incident", got), "GET api/now/table/sys_journal_field: HTTP 403: User Not Authorized - "
                                                             "the account needs read on sys_journal_field (the work notes)")
        self.assertEqual(got[0]["notes"], [])
        self.assertEqual(m.read_notes(api, "incident", []), "")               # no ticket: not asked
        self.assertEqual(len(api.calls), 2)

    def test_the_small_parts(self):
        m = self.m
        self.assertEqual([m.base_url(x) for x in ("sn.example.mil", "https://sn.example.mil/", "http://127.0.0.1:8080")],
                         ["https://sn.example.mil", "https://sn.example.mil", "http://127.0.0.1:8080"])
        self.assertEqual(m.sn_text(self.E), "2026-11-12 02:45:00")
        self.assertEqual([m.iso(x) for x in ("2026-11-12 02:14:00", "", None, "soon")], ["2026-11-12T02:14:00Z", "", "", ""])
        with open(m.__file__.replace(".pyc", ".py")) as f:
            src = f.read()
        self.assertEqual(src.count('method="GET"'), 1)                        # GET only: it changes nothing in ServiceNow
        for word in ("POST", "PATCH", "PUT", "DELETE"):
            self.assertNotIn(word, src)


class Looks(unittest.TestCase):
    def test_the_time_line_in_atlas_and_brief(self):
        import test_report_fit as T
        env, r = T.EmailTiles()._env(), made()
        for look in ("atlas", "brief", "ops"):
            h = env.get_template("report_%s.html.j2" % look).render(
                _report=ot.outage_timeline_doc(copy.deepcopy(WATCH), r, True, True), _email_footer="AAP job 4714", _page_logo_cid="",
                _page_runs=[], report_email_header_text="x")
            self.assertEqual(h.count('<div class="ot" role="group"'), 1)
            self.assertIn('aria-label="Outage time line, 12 Nov 01:30 to 12 Nov 03:45 CET"', h)
            self.assertEqual(h.count('<div class="ot-row">'), 4)
            self.assertEqual(h.count('role="listitem"'), 18)
            # a mark: its tip for the hover (1.57.0), the same as its title, read out with its level; no script in the section
            self.assertIn('data-tip="5  12 Nov 02:19:40 CET  Critical\nHost not responding - esx03"', h)
            self.assertIn('aria-label="5  12 Nov 02:19:40 CET  Critical, Host not responding - esx03"', h)
            part = h.split('class="ot"')[1].split('class="ot-axis"')[0]
            self.assertNotIn("<script", part)
            self.assertIn("esx03 down: 46 min", part)
            # status never by colour alone: the legend says each level's word, a mark carries its icon and its row's number
            leg = h.split('class="ot-legend"')[1].split('class="ot"')[0]
            for word in ("Critical", "Warning", "Low", "Note", "Back to normal", "Shaded: esx03 down: 46 min"):
                self.assertIn(word, leg)
            self.assertIn('class="ot-over"', h)
            for title in ("The outage in short", "The events", "For the after-action report", "Down or hung now"):
                self.assertIn(title, h)
        for look in ("", "_command", "_portal", "_glass"):                     # the email (the standard look) and the other looks
            h = env.get_template("report%s.html.j2" % look).render(
                _report=ot.outage_timeline_doc(copy.deepcopy(WATCH), r, False, True), _email_footer="AAP job 4714", _page_logo_cid="",
                _page_runs=[], report_email_header_text="x")
            self.assertNotIn('class="ot"', h)
            self.assertNotIn("What happened, in order", h)
            for text in ("Outage review: esx03: storage path redundancy lost; 46 min down, ticket after 93 min", "First signal to ticket",
                         "HA restarted 14 VMs", "Signal to ticket", "93 min", "Root cause (incident_root_cause)"):
                self.assertIn(text, h, look)

    def long_night(self):
        e = events() + [ev("01:3%d:00" % i, "esx.problem.vmfs.heartbeat.timedout", "esx-production-cluster-node-%02d.corp.example.mil" % (i + 3),
                           msg="Lost access to volume 5f1b (SITEA-production-datastore-%02d) due to connectivity issues" % i) for i in range(6)]
        t = tickets()
        t[0]["notes"].append([DAY + "02:30:00Z", "Checked the fabric switch: " + "port 0/14 logs a loss of signal since 01:19 UTC; " * 4])
        return ot.outage_timeline(e, t, OPTS)

    def test_report_fit_document(self):
        # tests/report_fit_docs.py --named keeps these two: the review's page (long host names, a long work note) ...
        web = ot.outage_timeline_doc(copy.deepcopy(WATCH), self.long_night(), True, True)
        self.assertEqual(web["sections"][1]["title"], "What happened, in order")
        self.assertEqual(len(web["sections"][2]["rows"]), 25)

    def test_report_fit_email(self):
        # ... and its email
        email = ot.outage_timeline_doc(copy.deepcopy(WATCH), self.long_night(), False, True)
        self.assertEqual(len(email["sections"][1]["rows"]), 25)


class Fine(unittest.TestCase):
    """The fine-tuning of the time line: what an event is about (its kind), which events are about the outage (the related set), the
    marks' lists of events, the website's events table (a Related column, folded repeats)."""
    H5 = "esx05.example.mil"

    def evs(self):
        return [
            ev("00:50:02", "AlarmStatusChangedEvent", self.H5, alarm="Datastore usage on disk", entity="ds-archive", to="yellow", **{"from": "green"}),
            ev("00:52:30", "esx.problem.net.redundancy.degraded", self.H5, msg="Uplink redundancy degraded on vmnic3"),
            alarm("01:05:00", "Virtual machine CPU usage", "yellow", "green"),
            ev("01:19:40", "HostConnectionLostEvent", H, msg="Host esx03.example.mil is not responding"),
            alarm("01:19:55", "Host connection and power state", "red", "green"),
            ev("01:20:10", "AlarmStatusChangedEvent", H, vm="vm-app01", alarm="Virtual machine heartbeat", entity="vm-app01", to="red", **{"from": "green"}),
            ev("01:20:20", "AlarmStatusChangedEvent", H, alarm="Datastore accessibility", entity="ds-prod-01", to="red", **{"from": "green"}),
            ev("01:30:00", "AlarmStatusChangedEvent", "esx01.example.mil", vm="vm-db02", alarm="Virtual machine snapshot consolidation needed",
               entity="vm-db02", to="yellow", **{"from": "green"}),
            ev("01:34:00", "AlarmStatusChangedEvent", "esx01.example.mil", vm="vm-app01", alarm="Virtual machine snapshot consolidation needed",
               entity="vm-app01", to="yellow", **{"from": "green"}),
            ev("02:05:12", "HostConnectedEvent", H, msg="Connected to esx03.example.mil"),
        ]

    def made_(self, evs=None):
        return made(evs if evs is not None else self.evs(), [])

    def table(self, r):
        return next(s for s in r["web_sections"] if s.get("title") == "The events")

    def chart(self, r):
        return next(s for s in r["web_sections"] if s.get("outage"))["outage"]

    def by_where(self, r):
        t = self.table(r)
        return dict(((row[3], row[4]), (tag["data"]["cls"], tag["data"]["rel"])) for row, tag in zip(t["rows"], t["row_tags"]))

    def test_the_kinds(self):
        cases = [({"type": "HostConnectionLostEvent", "label": "Host not responding"}, "host"),
                 ({"type": "com.vmware.vc.ha.VmRestartedByHAEvent", "label": "HA restarted a VM"}, "ha"),
                 ({"type": "esx.problem.storage.apd.start", "label": "All paths down to a storage device"}, "storage"),
                 ({"type": "esx.problem.vmfs.heartbeat.timedout", "label": "Datastore heartbeat timed out"}, "storage"),
                 ({"type": "esx.problem.net.redundancy.lost", "label": "Uplink redundancy lost"}, "network"),
                 ({"type": "AlarmStatusChangedEvent", "label": "Alarm red: Virtual machine heartbeat", "vm": "vm-app01"}, "vm"),
                 ({"type": "AlarmStatusChangedEvent", "label": "Alarm yellow: Virtual machine CPU usage"}, "usage"),
                 ({"type": "AlarmStatusChangedEvent", "label": "Alarm yellow: Host memory usage"}, "usage"),
                 ({"type": "AlarmStatusChangedEvent", "label": "Alarm red: Virtual machine snapshot consolidation needed"}, "snapshot"),
                 ({"type": "AlarmStatusChangedEvent", "label": "Alarm red: Host connection and power state"}, "host"),
                 ({"type": "AlarmStatusChangedEvent", "label": "Alarm red: Datastore accessibility"}, "storage"),
                 ({"type": "x", "label": "Ticket INC0012345 opened"}, "other")]
        for e, want in cases:
            self.assertEqual(ot._classify(e), want, e)
        self.assertEqual([k for k, _n in ot.CLASSES], ["host", "ha", "storage", "network", "vm", "usage", "snapshot", "other"])

    def test_the_related_set(self):
        r = self.made_()
        rel = self.by_where(r)
        self.assertEqual(self.chart(r)["subject"], "esx03, vm-app01")                              # the host that went down, the VM with a critical event
        self.assertEqual(rel[("esx03", "Host not responding")], ("host", "1"))
        self.assertEqual(rel[("vm-app01", "Alarm red: Virtual machine heartbeat")], ("vm", "1"))
        self.assertEqual(rel[("ds-prod-01", "Alarm red: Datastore accessibility")], ("storage", "1"))   # a datastore of a related host
        self.assertEqual(rel[("ds-archive", "Alarm yellow: Datastore usage on disk")][1], "0")           # another host's
        self.assertEqual(rel[("esx05", "Uplink redundancy degraded")], ("network", "0"))
        self.assertEqual(rel[("vm-db02", "Alarm yellow: Virtual machine snapshot consolidation needed")], ("snapshot", "0"))   # a snapshot on another VM
        self.assertEqual(rel[("vm-app01", "Alarm yellow: Virtual machine snapshot consolidation needed")], ("snapshot", "1"))  # on the outage VM itself
        self.assertEqual(rel[("esx03", "Host connected again")][1], "1")

    def test_a_vm_and_its_host_and_cluster(self):
        evs = [ev("01:20:10", "AlarmStatusChangedEvent", "esx02.example.mil", vm="vm-app01", alarm="Virtual machine heartbeat", entity="vm-app01",
                  to="red", **{"from": "green"}),
               ev("01:21:00", "HostShutdownEvent", "esx02.example.mil", msg="Host shut down"),
               ev("01:22:00", "HostShutdownEvent", "esx09.example.mil", msg="Host shut down", cluster="cluster2"),
               ev("01:23:00", "com.vmware.vc.HA.ClusterFailoverActionInitiatedEvent", "", msg="failover", cluster="cluster1")]
        evs[3]["host"] = ""
        r = made(evs, [])
        self.assertEqual(self.chart(r)["subject"], "vm-app01")
        t = self.table(r)
        wheres = dict((row[3] + "/" + row[4], tag["data"]["rel"]) for row, tag in zip(t["rows"], t["row_tags"]))
        self.assertEqual(wheres["esx02/Host shut down on request"], "1")                           # the VM's ESXi host
        self.assertEqual(wheres["esx09/Host shut down on request"], "0")
        self.assertEqual(wheres["cluster1/HA started a failover"], "1")                            # its cluster (an event about the cluster itself)

    def test_no_subject_no_marking(self):
        r = made([ev("01:00:00", "esx.problem.net.redundancy.lost", self.H5, msg="Uplink redundancy lost")], [])
        ch = self.chart(r)
        self.assertEqual(ch["subject"], "")
        self.assertIn("could not be worked out", ch["related_note"])
        self.assertEqual(ch["legend_rel"], "")
        t = self.table(r)
        self.assertNotIn("Related", t["columns"])                                                  # the table as it is, with the one muted line
        self.assertIn("could not be worked out", t["text"])
        self.assertTrue(all(tag["data"]["rel"] == "0" for tag in t["row_tags"]))

    def test_the_marks_carry_their_events(self):
        r = made(self.evs()[:2] + self.evs()[3:], [])
        ch = self.chart(r)
        marks = [m for g in ch["lanes"] for m in g["marks"]]
        for m in marks:
            self.assertEqual([x["n"] for x in m["ev"]], m["nums"])
            self.assertEqual(json.loads(m["data"]), m["ev"])
            self.assertIn(m["rel"], (0, 1, 2))
        host = next(m for m in marks if "Host not responding" in m["tip"])
        self.assertEqual((host["rel"], host["ev"][0]["l"], host["ev"][0]["t"], host["ev"][0]["m"]), (2, "critical", "02:19:40", "02:19"))   # local time, no date
        other = next(m for m in marks if "Uplink" in m["tip"])
        self.assertEqual(other["rel"], 0)
        self.assertEqual(sum(k["count"] for k in ch["kinds"]), r["events"])                         # every event has exactly one kind

    def test_a_grouped_mark_lists_every_event(self):
        r = made(self.long_dense(), [])
        marks = [m for g in self.chart(r)["lanes"] for m in g["marks"]]
        grouped = [m for m in marks if "+" in m["label"]]
        self.assertTrue(grouped)
        for m in grouped:
            self.assertEqual(len(m["ev"]), len(m["nums"]))
            self.assertEqual(m["tip"].count("\n") + 1, 2 * len(m["ev"]))

    def long_dense(self):
        return [ev("01:2%d:%02d" % (i // 60, i % 60), "com.vmware.vc.ha.VmRestartedByHAEvent", "esx01.example.mil", vm="vm-app%02d" % i) for i in range(30)] + \
               [alarm("01:20:%02d" % i, "Host storage path redundancy %d" % i, "red", "green") for i in range(10)]

    def test_repeats_fold(self):
        evs = [alarm("01:%02d:00" % (5 + 4 * i), "Virtual machine CPU usage", "yellow", "green") for i in range(4)] + [
            ev("01:30:00", "HostConnectionLostEvent", H, msg="x"), alarm("01:35:00", "Virtual machine CPU usage", "yellow", "green")]
        r = made(evs, [])
        t = self.table(r)
        d = [tag["data"] for tag in t["row_tags"]]
        self.assertEqual(d[0]["fold"].split("|")[:3], ["4", "02:05", "02:17"])                       # the first row of the four: x4, 02:05-02:17
        self.assertEqual(d[0]["fold"].split("|")[3], "02:05:00, 02:09:00, 02:13:00, 02:17:00")       # its times, for its title
        self.assertEqual([("rep" in x) for x in d], [False, True, True, True, False, False])         # the other three are repeats; the last is not "in a row"
        self.assertNotIn("fold", d[4])
        self.assertEqual(ot._fold([{"lane": "vcenter", "label": "a", "where": "x", "level": "low"}] * 2 +
                                  [{"lane": "vcenter", "label": "b", "where": "x", "level": "low"}]), [(0, [0, 1])])
        self.assertEqual(ot._fold([]), [])

    def test_the_row_ids_and_the_email(self):
        r = self.made_()
        t = self.table(r)
        self.assertEqual([tag["id"] for tag in t["row_tags"]][:2], ["ot-row-1", "ot-row-2"])
        email = next(x for x in r["sections"] if x.get("title") == "The events")
        self.assertNotIn("Related", email["columns"])                                              # the email's table as it was
        self.assertNotIn("row_tags", email)

    def test_the_page(self):
        import test_report_fit as T
        env, r = T.EmailTiles()._env(), self.made_()
        for look in ("atlas", "brief", "ops"):
            h = env.get_template("report_%s.html.j2" % look).render(
                _report=ot.outage_timeline_doc(copy.deepcopy(WATCH), r, True, True), _email_footer="AAP job 4714", _page_logo_cid="",
                _page_runs=[], report_email_header_text="x")
            self.assertIn("Outlined: about esx03, vm-app01 or its host, cluster, datastores", h, look)
            self.assertIn('data-ot-ctl hidden', h)
            self.assertIn('name="ot-show" value="rel"', h)
            self.assertIn('name="ot-rep"', h)
            self.assertEqual(h.count('name="ot-kind"'), len(self.chart(r)["kinds"]))
            self.assertIn('class="ot-m ot-critical r', h)
            self.assertIn(" ot-rel", h)
            self.assertIn(" ot-dim", h)
            self.assertIn('data-ev="', h)
            self.assertIn('id="ot-row-1"', h, look)
            self.assertIn('data-cls="host"', h, look)
            self.assertIn("<th", h)
            self.assertIn(">Related<", h, look)
            self.assertNotIn("<script>", h.split('class="ot-ctl"')[1].split('class="ot-over"')[0] if 'class="ot-over"' in h else "")
        none = made([ev("01:00:00", "esx.problem.net.redundancy.lost", self.H5, msg="Uplink redundancy lost")], [])
        h = env.get_template("report_atlas.html.j2").render(
            _report=ot.outage_timeline_doc(copy.deepcopy(WATCH), none, True, True), _email_footer="AAP job 4714", _page_logo_cid="",
            _page_runs=[], report_email_header_text="x")
        self.assertNotIn("Outlined:", h)
        self.assertNotIn('name="ot-show"', h)
        self.assertNotIn(" ot-dim", h)
        self.assertIn("could not be worked out", h)



class Links(unittest.TestCase):
    """The incidents of ServiceNow linked: https://<instance>/nav_to.do?uri=incident.do%3Fsys_id%3D<sys_id>, only for an https instance."""
    INST = "https://dev.service-now.example"
    URL = INST + "/nav_to.do?uri=incident.do%3Fsys_id%3Da1"

    def web_table(self, r):
        return next(x for x in r["web_sections"] if x.get("title") == "The events")

    def test_the_link_is_built(self):
        self.assertEqual(ot._ticket_url(self.INST, "a1"), self.URL)
        self.assertEqual(ot._ticket_url(self.INST + "/", "a1"), self.URL)                       # a slash at the end
        self.assertEqual(ot._ticket_url("https://dev.service-now.example:8443", "0a1b"),
                         "https://dev.service-now.example:8443/nav_to.do?uri=incident.do%3Fsys_id%3D0a1b")

    def test_no_link_without_a_safe_instance_and_a_plain_sys_id(self):
        for inst in ("", None, "http://dev.service-now.example", "dev.service-now.example", "https://user:pw@dev.service-now.example",
                     "https://dev.service-now.example/evil", "https://dev.service-now.example?x=1", "javascript:alert(1)", "https://a b.example"):
            self.assertEqual(ot._ticket_url(inst, "a1"), "", inst)
        for sid in ("", None, "a1&x=<script>", "a1\"onmouseover=\"x", "a 1", "../a1", "a" * 65):
            self.assertEqual(ot._ticket_url(self.INST, sid), "", sid)

    def test_where_it_shows(self):
        r = made(instance=self.INST)
        t = self.web_table(r)
        urls = t["cell_links"]["urls"]
        self.assertEqual((t["cell_links"]["column"], len(urls)), (4, len(t["rows"])))
        self.assertEqual(t["columns"][4], "What happened")
        rows = [(row[2], row[4], u) for row, u in zip(t["rows"], urls)]
        for lane, what, u in rows:
            self.assertEqual(bool(u), lane in ("ServiceNow", "ACT"), (lane, what))                # every row of an incident, no other
        self.assertTrue(all(u in ("", self.URL) for u in urls))
        email = next(x for x in r["sections"] if x.get("title") == "The events")
        self.assertEqual(email["cell_links"]["urls"], urls)                                          # the email's table links the same
        card = next(c for c in r["cards"] if c["chip"] == "First signal to ticket")
        self.assertEqual((card["url"], card["url_text"]), (self.URL, "INC0012345"))
        self.assertTrue(card["title"].startswith("INC0012345 opened at"))
        marks = [m for g in next(x for x in r["web_sections"] if x.get("outage"))["outage"]["lanes"] if g["key"] in ("servicenow", "act") for m in g["marks"]]
        ev = [e for m in marks for e in m["ev"]]
        self.assertTrue(ev and all(e["u"] == self.URL and e["k"] == "INC0012345" for e in ev))
        vc = [e for g in next(x for x in r["web_sections"] if x.get("outage"))["outage"]["lanes"] if g["key"] == "vcenter" for m in g["marks"] for e in m["ev"]]
        self.assertTrue(vc and not any("u" in e for e in vc))

    def test_without_an_instance_the_number_is_plain(self):
        for kw in ({}, {"instance": "http://dev.service-now.example"}):
            r = made(**kw)
            t = self.web_table(r)
            self.assertNotIn("cell_links", t)
            self.assertNotIn("url", next(c for c in r["cards"] if c["chip"] == "First signal to ticket"))
            self.assertNotIn("cell_links", next(x for x in r["sections"] if x.get("title") == "The events"))
            marks = [m for g in next(x for x in r["web_sections"] if x.get("outage"))["outage"]["lanes"] for m in g["marks"]]
            self.assertFalse(any("u" in e for m in marks for e in m["ev"]))

    def test_the_pages_and_the_email(self):
        import test_report_fit as T
        env, r = T.EmailTiles()._env(), made(instance=self.INST)
        for look in ("atlas", "brief", "ops"):
            h = env.get_template("report_%s.html.j2" % look).render(
                _report=ot.outage_timeline_doc(copy.deepcopy(WATCH), r, True, True), _email_footer="AAP job 4714", _page_logo_cid="",
                _page_runs=[], report_email_header_text="x")
            self.assertIn('href="%s"' % self.URL, h, look)
            self.assertIn('<a href="%s" target="_blank" rel="noopener noreferrer">INC0012345</a> opened at' % self.URL, h, look)   # the card
            self.assertNotIn("<script>" + self.INST, h)
        em = env.get_template("report.html.j2").render(
            _report=ot.outage_timeline_doc(copy.deepcopy(WATCH), r, False, True), _email_footer="AAP job 4714", _page_logo_cid="", _page_runs=[],
            report_email_header_text="x")
        self.assertIn('href="%s"' % self.URL.replace("&", "&amp;"), em)                           # plain <a> in the email's table
        self.assertIn('<a href="%s"' % self.URL, em)
        self.assertIn(">INC0012345</a> opened at", em)                                              # and in the card
        r2 = made()
        em2 = env.get_template("report.html.j2").render(
            _report=ot.outage_timeline_doc(copy.deepcopy(WATCH), r2, False, True), _email_footer="AAP job 4714", _page_logo_cid="", _page_runs=[],
            report_email_header_text="x")
        self.assertNotIn("nav_to.do", em2)

    def test_the_reader_returns_the_instance(self):
        sys.path.insert(0, os.path.join(REPO, "roles", "vmware_vm", "library"))
        try:
            import site_outage_tickets as m
        except ImportError as e:
            self.skipTest("ansible-core not importable: %s" % e)
        self.assertEqual(m.instance_url("dev.service-now.example"), "https://dev.service-now.example")
        self.assertEqual(m.instance_url("https://dev.service-now.example/"), "https://dev.service-now.example")
        self.assertEqual(m.instance_url("https://user:pw@dev.service-now.example:8443/x?y=1"), "https://dev.service-now.example:8443")   # never the user
        self.assertEqual(m.instance_url("http://dev.service-now.example"), "")
        self.assertEqual(m.instance_url(""), "")
        self.assertEqual(m.instance_url("https://dev.service-now.example:99999"), "")


class Codex2(unittest.TestCase):
    """The review of c8acac4b: data keys and links checked before they are written, the same-named VMs of two vCenters, the table of the link."""
    INST = "https://dev.service-now.example"

    def env(self):
        import test_report_fit as T
        return T.EmailTiles()._env()

    def doc(self, mutate, web=True):
        r = made(instance=self.INST)
        d = ot.outage_timeline_doc(copy.deepcopy(WATCH), r, web, True)
        mutate(d)
        return d

    def render(self, tpl, d):
        return self.env().get_template(tpl).render(_report=d, _email_footer="AAP job 4714", _page_logo_cid="", _page_runs=[], report_email_header_text="x")

    # 1: the names of data attributes
    def test_1_data_names_are_checked(self):
        import site_filters as sf
        got = sf.safe_data({"num": 3, "cls": "host", "x onload=alert(1) y": "1", "A": "1", "1a": "1", "a" * 40: "1", "ok-1": None, "q\"": "v", 5: "v"})
        self.assertEqual(got, {"num": "3", "cls": "host", "ok-1": ""})
        self.assertEqual(sf.safe_data(None), {})
        self.assertEqual(sf.safe_data("x"), {})
        self.assertEqual(sf.safe_data({"name\n": "1", "ok": "1"}), {"ok": "1"})                  # "$" would let a last line end through

    def test_1_the_pages_drop_a_bad_name(self):
        def bad(d):
            for s in d["sections"] if False else []:
                pass
            for sec in d.get("web_sections", d.get("sections", [])):
                if sec.get("row_tags"):
                    sec["row_tags"][0]["data"]['x onmouseover=alert(1) y'] = "1"
                    sec["row_tags"][0]["data"]["good-name"] = "fine"
        for tpl in ("report_atlas.html.j2", "report_brief.html.j2", "report_ops.html.j2"):
            h = self.render(tpl, self.doc(bad))
            self.assertNotIn("onmouseover", h, tpl)
            self.assertIn('data-good-name="fine"', h, tpl)

    # 2: one check for every link
    def test_2_the_shared_check(self):
        import site_filters as sf
        ok = "https://dev.service-now.example/nav_to.do?uri=incident.do%3Fsys_id%3Da1"
        self.assertEqual(sf.safe_link(ok), ok)
        self.assertEqual(sf.safe_link("https://dev.service-now.example:8443/x"), "https://dev.service-now.example:8443/x")
        for u in ("", None, "http://a.example/x", "javascript:alert(1)", "//a.example/x", "https://user@a.example/x", "https://user:pw@a.example/x",
                  "https:///x", "https://", "https://a.example/ x", "https://a.example/x\ny", "https://a.example/\"onclick=\"x", "https://a.example:99999/x",
                  "https://a.example/<b>", "ftp://a.example/", "data:text/html,x", "https://a.example/" + "a" * 2100):
            self.assertEqual(sf.safe_link(u), "", u)

    def test_2_a_bad_link_shows_as_text(self):
        bad = "http://evil.example/x"
        def card(d):
            for c in d.get("cards", []):
                if c.get("url"):
                    c["url"] = bad
        def cell(d):
            for sec in d.get("web_sections", d.get("sections", [])):
                if sec.get("cell_links"):
                    sec["cell_links"]["urls"] = [bad if u else "" for u in sec["cell_links"]["urls"]]
        for tpl in ("report_atlas.html.j2", "report_brief.html.j2", "report_ops.html.j2"):
            h = self.render(tpl, self.doc(card))
            self.assertNotIn(bad, h, tpl)
            self.assertIn("INC0012345", h)
        for web, tpl in ((False, "report.html.j2"),):
            h = self.render(tpl, self.doc(lambda d: (card(d), cell(d)), web))
            self.assertNotIn(bad, h, tpl)
            self.assertIn("INC0012345", h)
        import site_filters as sf
        html = sf.report_cards_html([{"title": "INC0012345 opened at 02:14", "url": bad, "url_text": "INC0012345", "chip": "x", "status": "ok"}])
        self.assertNotIn("evil", html)
        self.assertIn("INC0012345", html)

    def test_2_the_web_looks_table_links_pass_the_check_too(self):
        bad, good = "http://evil.example/x", "https://dev.service-now.example/nav_to.do?uri=incident.do%3Fsys_id%3Da1"
        def cell(d):
            for sec in d.get("web_sections", d.get("sections", [])):
                if sec.get("cell_links"):
                    sec["cell_links"]["urls"] = [bad if i % 2 else good for i, u in enumerate(sec["cell_links"]["urls"])]
        for tpl in ("report_atlas.html.j2", "report_brief.html.j2", "report_ops.html.j2"):
            h = self.render(tpl, self.doc(cell))
            self.assertNotIn(bad, h, tpl)
            self.assertIn('href="%s"' % good, h, tpl)
            self.assertNotIn("javascript:", h)

    # 7: two vCenters, the same names
    def test_7_same_names_in_two_vcenters(self):
        a, b = "vc01.example.mil", "vc02.example.mil"
        evs = [ev("01:19:50", "AlarmStatusChangedEvent", "esx01.example.mil", vm="vm-app01", alarm="Virtual machine heartbeat", entity="vm-app01",
                  to="red", vcenter=a, **{"from": "green"}),
               ev("01:19:51", "AlarmStatusChangedEvent", "esx09.example.mil", vm="vm-app01", alarm="Virtual machine heartbeat", entity="vm-app01",
                  to="yellow", vcenter=b, **{"from": "green"})]
        r = made(evs, [])
        t = next(s for s in r["web_sections"] if s.get("title") == "The events")
        by = [(row[3], tag["data"]["rel"]) for row, tag in zip(t["rows"], t["row_tags"])]
        self.assertEqual(by, [("vm-app01", "1"), ("vm-app01", "0")])   # the other vCenter's VM of the same name is not the subject
        self.assertEqual(ot._relate(copy.deepcopy(ot._vcenter_events(evs)), [])["names"], ["vm-app01"])   # shown by name, once

    def test_7_a_name_is_told_apart_by_its_vcenter(self):
        a = {"vcenter": "vc01", "where": "esx03", "vm": "", "names": []}
        b = {"vcenter": "vc02", "where": "esx03", "vm": "", "names": []}
        t = {"vcenter": "", "where": "esx03", "vm": "", "names": []}
        keys = {("vc01", "esx03")}
        self.assertTrue(ot._has(keys, a, "esx03"))
        self.assertFalse(ot._has(keys, b, "esx03"))                                  # the same name in another vCenter
        self.assertTrue(ot._has(keys, t, "esx03"))                                   # a ticket has no vCenter: the name alone
        self.assertFalse(ot._has(keys, t, ""))
        self.assertEqual(ot._about(a), {("vc01", "esx03")})

    def test_7_only_the_subject_vcenter_is_related(self):
        a, b = "vc01.example.mil", "vc02.example.mil"
        evs = [ev("01:19:40", "HostConnectionLostEvent", H, msg="Host esx03.example.mil is not responding", vcenter=a),
               ev("01:20:00", "AlarmStatusChangedEvent", "esx07.example.mil", vm="vm-web", alarm="Virtual machine heartbeat", entity="vm-web",
                  to="red", vcenter=a, **{"from": "green"}),
               ev("01:21:00", "esx.problem.storage.apd.start", H, msg="Device naa.1 has entered the All Paths Down state", vcenter=b)]
        r = made(evs, [])
        t = next(s for s in r["web_sections"] if s.get("title") == "The events")
        by = dict((row[2] + "|" + row[4], tag["data"]["rel"]) for row, tag in zip(t["rows"], t["row_tags"]))
        apd = [v for k, v in by.items() if "storage" in k.lower() or "paths down" in k.lower()]
        self.assertEqual(apd, ["0"])                                                  # esx03 of vc02 did not go down: its event is not about this outage

    # 8: the table of the link
    def test_8_the_table_of_the_link(self):
        self.assertEqual(ot._ticket_url(self.INST, "a1", "incident"), self.INST + "/nav_to.do?uri=incident.do%3Fsys_id%3Da1")
        self.assertEqual(ot._ticket_url(self.INST, "a1", ""), self.INST + "/nav_to.do?uri=incident.do%3Fsys_id%3Da1")
        self.assertEqual(ot._ticket_url(self.INST, "a1", "u_site_ticket"), self.INST + "/nav_to.do?uri=u_site_ticket.do%3Fsys_id%3Da1")
        for t in ("x/y", "a.b", "../incident", "Incident", "1abc", "a b", "a&b=1", "a" * 81, "inc;x"):
            self.assertEqual(ot._ticket_url(self.INST, "a1", t), "", t)

    def test_8_the_review_uses_the_table(self):
        r = made(instance=self.INST, table="change_request")
        card = next(c for c in r["cards"] if c["chip"] == "First signal to ticket")
        self.assertIn("uri=change_request.do%3Fsys_id%3Da1", card["url"])
        r = made(instance=self.INST, table="a/b")
        self.assertNotIn("url", next(c for c in r["cards"] if c["chip"] == "First signal to ticket"))
        with open(os.path.join(REPO, "roles", "vmware_vm", "tasks", "watch.yml")) as f:
            y = f.read()
        self.assertIn("'table': servicenow_table | default('incident', true) | string", y)



if __name__ == "__main__":
    unittest.main()
