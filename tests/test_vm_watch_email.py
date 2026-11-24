#!/usr/bin/env python3
"""1.112.5, the VM outage watch's email only when something changes: vm_watch_email_decision and vm_watch_email_ids
(plugins/filter/vmware_watch_filters.py), then roles/vmware_vm/tasks/watch_email.yml with ansible-playbook against the
fake AAP (tests/servicenow/aapmock.py, in this process): two runs with the same conditions send one email.
    python3 tests/test_vm_watch_email.py"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
import vmware_watch_filters as w  # noqa: E402
import vmware_watch_email_filters as wf  # noqa: E402
sys.path.insert(0, os.path.join(REPO, "roles", "vmware_vm", "library"))
sys.path.insert(0, os.path.join(REPO, "plugins", "module_utils"))
import site_report_db  # noqa: E402
import ansible.module_utils  # noqa: E402
sys.modules.setdefault("ansible.module_utils.site_report_db", site_report_db)
ansible.module_utils.site_report_db = site_report_db
import site_vm_watch_email_db as edb  # noqa: E402

T0 = 1795413600.0                                     # 2026-11-23 06:00 UTC
H = 3600.0


def cond(obj, sev="critical", kind="host", otype="host", vc="", **kw):
    c = {"id": "vm-watch:%s:%s" % (kind, obj), "severity": sev, "kind": kind, "object_type": otype, "object": obj,
         "vcenter": vc, "summary": "%s %s" % (kind, obj)}
    c.update(kw)
    return c


def vm(name, kind="lost", ds=(), host="", sev="critical", vc=""):
    return cond(name, sev, kind, "vm", vc, datastores=list(ds), vm={"esxi_host": host})


def runs(state, status="failed"):
    return [{"id": 10, "status": "successful", "finished": "", "report": None, "artifacts": {}},
            {"id": 11, "status": status, "finished": "", "report": None, "artifacts": {"vm_watch_email_state": state}}]


def decide(conds, state=None, now=T0, **o):
    opts = {"now": now, "on_change": True, "every_hours": 4, "report_on": ["critical"], "why": "", "time_zone": ""}
    opts.update(o)
    return w.vm_watch_email_decision(conds, runs(state) if state is not None else [], opts)


class Ids1125(unittest.TestCase):
    def test_vms_fold_into_their_lost_datastore_or_down_host(self):
        cs = [cond("ds-07", kind="datastore", otype="datastore"), vm("app01", ds=["ds-07"]), vm("app02", "hung", host="esx02"),
              cond("esx02"), vm("app03", "hung"), cond("esx09", "warning")]
        self.assertEqual(w.vm_watch_email_ids(cs, ["critical"]),
                         {"vm-watch:datastore:ds-07": "critical", "vm-watch:host:esx02": "critical", "vm-watch:hung:app03": "critical"})
        self.assertIn("vm-watch:host:esx09", w.vm_watch_email_ids(cs, ["critical", "warning"]))

    def test_each_vcenter_its_own(self):
        # Codex review: two vCenters may each have a datastore1; a VM folds only under its own vCenter's datastore
        cs = [cond("datastore1", kind="datastore", otype="datastore", vc="vc01"), vm("app01", ds=["datastore1"], vc="vc02")]
        self.assertEqual(w.vm_watch_email_ids(cs), {"vm-watch:datastore:datastore1@vc01": "critical", "vm-watch:lost:app01@vc02": "critical"})
        st = decide([cond("datastore1", kind="datastore", otype="datastore", vc="vc01")])["state"]
        d = decide([cond("datastore1", kind="datastore", otype="datastore", vc="vc01"),
                    cond("datastore1", kind="datastore", otype="datastore", vc="vc02")], st, T0 + 900)
        self.assertEqual((d["send"], d["changed"]), (True, ["New: datastore:datastore1 in vc02 (critical)"]))


class Decision1125(unittest.TestCase):
    def test_first_run_ever_emails(self):
        d = decide([cond("esx01")])
        self.assertEqual((d["send"], d["kind"]), (True, "first"))
        self.assertEqual(d["state"]["ids"], {"vm-watch:host:esx01": "critical"})
        self.assertEqual(d["state"]["sent_at"], "2026-11-23 06:00 UTC")
        self.assertEqual(d["state"]["sent_ts"], T0)

    def test_same_ids_no_email_state_carried(self):
        first = decide([cond("esx01")])["state"]
        d = decide([cond("esx01")], first, T0 + 900, time_zone="Europe/Berlin")
        self.assertEqual((d["send"], d["kind"]), (False, "quiet"))
        self.assertEqual(d["state"]["sent_ts"], T0)                                    # the last email's, carried forward
        self.assertEqual(d["why"], "nothing changed since the email of 2026-11-23 07:00 CET "
                                   "(vm_watch_email_every_hours: 4 - next reminder at 2026-11-23 11:00 CET).")
        self.assertTrue(d["report"])                                                   # the web page and tickets: every run
        d = decide([cond("esx01")], first, T0 + 900, every_hours=0)
        self.assertIn("(vm_watch_email_every_hours: 0 - no reminder)", d["why"])

    def test_new_worse_cleared_email(self):
        st = decide([cond("esx01"), cond("esx02", "warning")], report_on=["critical", "warning"])["state"]
        d = decide([cond("esx01"), cond("esx02", "warning"), cond("esx03")], st, T0 + 900, report_on=["critical", "warning"])
        self.assertEqual((d["send"], d["kind"], d["changed"]), (True, "change", ["New: host:esx03 (critical)"]))
        d = decide([cond("esx01"), cond("esx02")], st, T0 + 900, report_on=["critical", "warning"])
        self.assertEqual(d["changed"], ["Worse: host:esx02 (critical, was warning)"])
        d = decide([cond("esx01")], st, T0 + 900, report_on=["critical", "warning"])
        self.assertEqual((d["send"], d["changed"]), (True, ["Cleared: host:esx02 (was warning)"]))
        self.assertEqual(d["state"]["ids"], {"vm-watch:host:esx01": "critical"})
        d = decide([cond("esx01"), cond("esx02", "info")], st, T0 + 900, report_on=["critical", "warning"])
        self.assertTrue(d["send"])                                                     # better, below vm_watch_report_on: cleared

    def test_more_vms_under_the_same_lost_datastore_no_email(self):
        st = decide([cond("ds-07", kind="datastore", otype="datastore"), vm("app01", ds=["ds-07"])])["state"]
        d = decide([cond("ds-07", kind="datastore", otype="datastore"), vm("app01", ds=["ds-07"]), vm("app02", ds=["ds-07"])],
                   st, T0 + 900)
        self.assertFalse(d["send"])

    def test_reminder_after_n_hours(self):
        st = decide([cond("esx01")])["state"]
        self.assertFalse(decide([cond("esx01")], st, T0 + 4 * H - 60)["send"])
        d = decide([cond("esx01")], st, T0 + 4 * H)
        self.assertEqual((d["send"], d["kind"]), (True, "reminder"))
        self.assertEqual(d["state"]["sent_ts"], T0 + 4 * H)
        self.assertEqual(d["changed"], ["Reminder: no change since the email of 2026-11-23 06:00 UTC"])
        self.assertFalse(decide([cond("esx01")], st, T0 + 40 * H, every_hours=0)["send"])    # 0 = never

    def test_all_cleared_one_recovered_email_then_quiet(self):
        st = decide([cond("esx01")])["state"]
        d = decide([], st, T0 + 900)
        self.assertEqual((d["send"], d["kind"], d["report"]), (True, "recovered", True))
        self.assertEqual(d["changed"], ["Cleared: host:esx01 (was critical)"])
        self.assertEqual(d["state"]["ids"], {})
        d = decide([], d["state"], T0 + 1800)
        self.assertEqual((d["send"], d["kind"], d["report"]), (False, "none", False))
        d = decide([], d["state"], T0 + 9 * H)
        self.assertFalse(d["send"])                                                    # no reminder of nothing

    def test_no_state_readable_emails_with_the_reason(self):
        d = decide([cond("esx01")], why="no AAP connection: attach a credential")
        self.assertEqual((d["send"], d["kind"]), (True, "unknown"))
        self.assertEqual(d["lines"], ["Email on every report this run: the last email could not be read from AAP - "
                                      "no AAP connection: attach a credential."])
        self.assertEqual(decide([], why="x")["lines"], [])                              # a clean run stays quiet
        self.assertIsNone(decide([], why="x")["state"])                                 # and keeps no state over the last one

    def test_off_is_the_old_behaviour(self):
        st = decide([cond("esx01")])["state"]
        d = decide([cond("esx01")], st, T0 + 900, on_change=False)
        self.assertEqual((d["send"], d["state"]), (True, None))

    def test_a_vcenter_not_read_is_no_recovery(self):
        st = decide([cond("esx01"), cond("esx02")])["state"]
        d = decide([], st, T0 + 900, unread=True)
        self.assertEqual((d["send"], d["state"]["ids"]), (False, st["ids"]))
        d = decide([cond("esx01")], st, T0 + 900, unread=True)
        self.assertFalse(d["send"])
        d = decide([cond("esx01"), cond("esx03")], st, T0 + 900, unread=True)
        self.assertTrue(d["send"])                                                     # new still emails
        # Codex review: what could not be seen stays in the state - read again, esx02 still down is no "new"
        self.assertEqual(sorted(d["state"]["ids"]), ["vm-watch:host:esx01", "vm-watch:host:esx02", "vm-watch:host:esx03"])
        self.assertFalse(decide([cond("esx01"), cond("esx02"), cond("esx03")], d["state"], T0 + 1800)["send"])

    def test_a_window_event_ageing_out_is_no_change(self):
        ev = cond("app01", kind="event:VmFailoverFailed")
        st = decide([cond("esx01"), ev, cond("esx01:naa.1", kind="storage")])["state"]
        d = decide([cond("esx01")], st, T0 + 900)
        self.assertFalse(d["send"])
        self.assertEqual(decide([], st, T0 + 900)["kind"], "recovered")              # nothing left at all: recovered
        # Codex review: the aged-out events leave the state, so the same failure again is new
        self.assertEqual(d["state"]["ids"], {"vm-watch:host:esx01": "critical"})
        self.assertEqual(d["state"]["sent_ts"], T0)
        self.assertTrue(decide([cond("esx01"), ev], d["state"], T0 + 1800)["send"])

    def test_carry_when_the_email_does_not_go_out(self):
        # Codex review: no recipient (or a skipped blip) - the state to keep is the last one, not this unsent email
        st = decide([cond("esx01")])["state"]
        d = decide([cond("esx01"), cond("esx02")], st, T0 + 900)
        self.assertTrue(d["send"])
        self.assertEqual(d["carry"]["ids"], st["ids"])
        self.assertEqual(d["carry"]["sent_ts"], T0)
        self.assertIsNone(decide([cond("esx01")])["carry"])                          # no last state: none kept
        q = decide([cond("esx01")], st, T0 + 900)
        self.assertEqual(q["carry"], q["state"])                                     # nothing to send: the same

    def test_review_always_emails(self):
        st = decide([cond("esx01")])["state"]
        d = decide([cond("esx01")], st, T0 + 900, review=True)
        self.assertEqual((d["send"], d["kind"], d["review"]), (True, "review", True))
        self.assertEqual(d["state"]["review_ts"], T0 + 900)

    def test_review_on_a_schedule_every_n_hours(self):
        d = decide([cond("esx01")], None, T0, review=True, scheduled=True)
        self.assertTrue(d["review"])
        self.assertIn("vm_watch_review on a schedule: a review every 4 h at most - launch it by hand for an AAR", d["lines"])
        d2 = decide([cond("esx01")], d["state"], T0 + 900, review=True, scheduled=True)
        self.assertEqual((d2["review"], d2["send"]), (False, False))
        self.assertEqual(d2["state"]["review_ts"], T0)
        self.assertIn("No review this run: the last was at 2026-11-23 06:00 UTC, the next from 2026-11-23 10:00 UTC.", d2["lines"])
        d3 = decide([cond("esx01")], d2["state"], T0 + 4 * H, review=True, scheduled=True)
        self.assertEqual((d3["review"], d3["send"], d3["kind"]), (True, True, "review"))
        self.assertTrue(decide([cond("esx01")], d2["state"], T0 + 900, review=True, scheduled=False)["review"])   # by hand: always


def floor(conds, state=None, now=T0, db=None, **o):
    """1.118.8: the decision with the email limit (vm_watch_email_floor); read_at = the run's start, here = now."""
    opts = {"now": now, "on_change": True, "every_hours": 4, "report_on": ["critical"], "why": "", "time_zone": "",
            "min_minutes": 30, "read_at": "", "db": db or {}}
    opts.update(o)
    return wf.vm_watch_email_floor(conds, runs(state) if state is not None else [], opts)


class Floor1188(unittest.TestCase):
    """1.118.8: at most one email per vm_watch_email_min_minutes."""

    def test_a_change_inside_the_window_is_held(self):
        st = floor([cond("esx01")])["state"]
        self.assertEqual(st["read_ts"], T0)
        d = floor([cond("esx01"), cond("esx02")], st, T0 + 600, time_zone="Europe/Berlin")
        self.assertEqual((d["send"], d["held"], d["kind"]), (False, True, "change"))
        self.assertEqual(d["why"], "last one at 07:00, next possible at 07:30 (vm_watch_email_min_minutes: 30)")
        self.assertTrue(d["report"])                                                   # the web page and tickets: every run
        self.assertEqual(d["state"]["ids"], st["ids"])                                 # the last email's, kept
        self.assertEqual([h["line"] for h in d["state"]["held"]], ["New: host:esx02 (critical)"])
        self.assertEqual(d["carry"], d["state"])
        # critical-new too: no exception
        d2 = floor([cond("esx01"), cond("esx02"), cond("ds-07", kind="datastore", otype="datastore")], d["state"], T0 + 1200)
        self.assertFalse(d2["send"])
        self.assertEqual([h["line"] for h in d2["state"]["held"]],
                         ["New: host:esx02 (critical)", "New: datastore:ds-07 (critical)"])

    def test_the_first_run_after_the_window_sends_one_combined_email(self):
        st = floor([cond("esx01")])["state"]
        d = floor([cond("esx01"), cond("esx02"), cond("esx03")], st, T0 + 600)
        d = floor([cond("esx01"), cond("esx03")], d["state"], T0 + 1200)              # esx02 came and went
        self.assertFalse(d["send"])
        d = floor([cond("esx01"), cond("esx03")], d["state"], T0 + 1800)
        self.assertEqual((d["send"], d["kind"]), (True, "change"))
        self.assertEqual(d["changed"], ["New: host:esx03 (critical)", "Earlier (06:10): New: host:esx02 (critical)"])
        self.assertEqual((d["state"]["held"], d["state"]["sent_ts"], d["state"]["read_ts"]), ([], T0 + 1800, T0 + 1800))
        # the next change: held again for 30 minutes from this email
        self.assertFalse(floor([cond("esx01")], d["state"], T0 + 2400)["send"])

    def test_held_changes_that_settled_still_go_after_the_window(self):
        st = floor([cond("esx01")])["state"]
        d = floor([cond("esx01"), cond("app01", kind="event:VmFailoverFailed")], st, T0 + 600)
        self.assertFalse(d["send"])
        d = floor([cond("esx01")], d["state"], T0 + 1500)                              # the event aged out of the window
        self.assertFalse(d["send"])
        d = floor([cond("esx01")], d["state"], T0 + 1800)
        self.assertEqual((d["send"], d["kind"]), (True, "change"))
        self.assertEqual(d["changed"], ["Earlier (06:10): New: event:VmFailoverFailed:app01 (critical)"])
        self.assertFalse(floor([cond("esx01")], d["state"], T0 + 2700)["send"])        # sent once

    def test_recovered_waits_for_the_window(self):
        st = floor([cond("esx01")])["state"]
        d = floor([], st, T0 + 900)
        self.assertEqual((d["send"], d["kind"], d["report"], d["held"]), (False, "recovered", True, True))
        self.assertEqual(d["state"]["ids"], st["ids"])
        self.assertTrue(d["why"].startswith("last one at 06:00, next possible at 06:30"))
        d = floor([], d["state"], T0 + 1800)
        self.assertEqual((d["send"], d["kind"]), (True, "recovered"))
        self.assertEqual(d["changed"], ["Cleared: host:esx01 (was critical)"])
        # back again before the window ended: no Recovered, nothing changed since the last email
        d = floor([], st, T0 + 900)
        d = floor([cond("esx01")], d["state"], T0 + 1800)
        self.assertEqual((d["send"], d["state"]["held"]), (False, []))

    def test_the_run_start_counts_a_late_email_is_no_longer_window(self):
        st = floor([cond("esx01")], read_at="2026-11-23T06:00:05Z", now=T0 + 360)["state"]   # confirm checks: sent 6 min in
        d = floor([cond("esx01"), cond("esx02")], st, T0 + 1800 + 10, read_at="2026-11-23T06:29:20Z")
        self.assertTrue(d["send"])                                                     # 29:15 after: within the grace

    def test_reminder_and_first_and_zero(self):
        self.assertTrue(floor([cond("esx01")])["send"])                               # no email before: the first goes
        st = floor([cond("esx01")])["state"]
        self.assertEqual(floor([cond("esx01")], st, T0 + 4 * H)["kind"], "reminder")
        self.assertTrue(floor([cond("esx01"), cond("esx02")], st, T0 + 60, min_minutes=0)["send"])   # 0 = no limit

    def test_every_report_mode_is_limited_too(self):
        d = floor([cond("esx01")], None, T0, on_change=False)
        self.assertTrue(d["send"])
        d2 = floor([cond("esx01")], d["state"], T0 + 900, on_change=False)
        self.assertEqual((d2["send"], d2["state"]["sent_ts"], d2["state"]["ids"]), (False, T0, d["state"]["ids"]))
        q = floor([], d2["state"], T0 + 1200, on_change=False)
        self.assertEqual((q["send"], q["state"]), (False, d2["state"]))            # a clean run keeps the last email
        d3 = floor([cond("esx01")], q["state"], T0 + 1800, on_change=False)
        self.assertEqual((d3["send"], d3["changed"], d3["state"]["held"]), (True, [], []))   # esx01 is in the report itself

    def test_every_report_mode_keeps_a_flap_inside_the_window(self):
        # Codex review: a second host down and back inside the window still reaches the next email - on a clean run too
        d = floor([cond("esx01")], None, T0, on_change=False)
        d = floor([cond("esx01"), cond("esx02")], d["state"], T0 + 600, on_change=False)
        self.assertFalse(d["send"])
        d = floor([], d["state"], T0 + 1200, on_change=False)
        self.assertFalse(d["send"])
        d = floor([], d["state"], T0 + 1800, on_change=False)
        self.assertEqual((d["send"], d["kind"], d["report"]), (True, "recovered", True))
        self.assertEqual(d["changed"], ["Earlier (06:10): Seen: host:esx01 (critical)", "Earlier (06:10): Seen: host:esx02 (critical)"])
        self.assertEqual(d["state"]["held"], [])
        self.assertFalse(floor([], d["state"], T0 + 3600, on_change=False)["send"])      # once

    def test_manual_review_still_emails(self):
        st = floor([cond("esx01")])["state"]
        d = floor([cond("esx01")], st, T0 + 300, review=True)
        self.assertEqual((d["send"], d["kind"]), (True, "review"))
        self.assertEqual(d["state"]["read_ts"], T0 + 300)
        d = floor([], None, T0 + 300, review=True, why="no AAP connection")       # and with no state at all
        self.assertTrue(d["send"])

    def test_a_review_carries_what_was_held(self):
        # Codex review: a short critical outage held inside the window, over before the review, is in the review's email
        st = floor([cond("esx01")])["state"]
        h = floor([cond("esx01"), cond("esx02")], st, T0 + 600)["state"]
        d = floor([cond("esx01")], h, T0 + 900, review=True)
        self.assertEqual((d["send"], d["kind"]), (True, "review"))
        self.assertIn("Earlier (06:10): New: host:esx02 (critical)", d["changed"])
        self.assertEqual(d["state"]["held"], [])

    def test_a_vcenter_not_read_does_not_hold_back_what_was_seen(self):
        # Codex review: held on vc01, vc02 not read when the window ends - what was seen still goes, not as Recovered
        st = floor([cond("esx01", vc="vc01")])["state"]
        h = floor([cond("esx01", vc="vc01"), cond("esx02", vc="vc01")], st, T0 + 600)["state"]
        d = floor([cond("esx01", vc="vc01")], h, T0 + 1800, unread=True)
        self.assertEqual((d["send"], d["kind"]), (True, "change"))
        self.assertEqual(d["changed"], ["Earlier (06:10): New: host:esx02 in vc01 (critical)"])
        d = floor([], h, T0 + 1800, unread=True)                                        # nothing seen at all now
        self.assertEqual((d["send"], d["kind"]), (True, "change"))                      # never Recovered on a partial read
        self.assertEqual(d["state"]["ids"], st["ids"])

    def test_the_report_database_state_when_aap_cannot_be_read(self):
        st = floor([cond("esx01")])["state"]
        db = {"ok": True, "state": st, "error": ""}
        d = floor([cond("esx01"), cond("esx02")], None, T0 + 600, db=db, why="no AAP connection")
        self.assertEqual((d["send"], d["held"], d["source"]), (False, True, "db"))
        self.assertEqual(d["lines"], [])                                               # no "every report" line
        d = floor([cond("esx01"), cond("esx02")], None, T0 + 1800, db=dict(db, state=d["state"]), why="no AAP connection")
        self.assertEqual((d["send"], d["changed"]), (True, ["New: host:esx02 (critical)"]))
        d = floor([cond("esx01")], None, T0, db={"ok": True, "state": None}, why="no AAP connection")   # no row yet
        self.assertEqual((d["send"], d["kind"]), (True, "first"))

    def test_no_state_at_all_only_critical_in_the_first_15_minutes_of_each_slot(self):
        why = "no AAP connection"
        d = floor([cond("esx01")], None, T0 + 300, why=why, db_why="no Report database credential")
        self.assertEqual((d["send"], d["kind"], d["source"]), (True, "unknown", "none"))
        self.assertEqual(d["lines"], ["No AAP credential or report database: the 30-minute email limit cannot work - attach the "
                                      "Red Hat Ansible Automation Platform credential to VM - outage watch (AAP: no AAP "
                                      "connection; report database: no Report database credential)."])
        d = floor([cond("esx01")], None, T0 + 1200, why=why)
        self.assertEqual((d["send"], d["state"]), (False, None))
        self.assertEqual(d["why"], "the last email is not known, next possible at 06:30 (vm_watch_email_min_minutes: 30)")
        d = floor([cond("esx09", "warning")], None, T0 + 300, why=why, report_on=["critical", "warning"])
        self.assertFalse(d["send"])                                                    # a warning alone: no email
        self.assertEqual(len(floor([], None, T0, why=why)["lines"]), 1)                 # a clean run says it too
        # a 10-minute schedule: at most two of three runs a slot email; a 15-minute one: one
        for step, most in ((600, 2), (900, 1)):
            sent = [floor([cond("esx01")], None, T0 + t, why=why)["send"] for t in range(0, 1800, step)]
            self.assertEqual(sum(sent), most)
        self.assertTrue(floor([cond("esx01")], None, T0 + 60, why=why, min_minutes=0)["send"])   # 0 = as before 1.118.8


class FakeCursor(object):
    def __init__(self, conn):
        self.conn, self.one = conn, None

    def execute(self, sql, args=None):
        self.conn.sql.append(sql)
        if sql.startswith("SELECT table_name"):
            name = args[0] if args else "schema_version"
            self.one = (name,) if name in self.conn.tables else None
        elif sql.startswith("CREATE TABLE IF NOT EXISTS vm_watch_email_state"):
            self.conn.tables.add("vm_watch_email_state")
        elif sql.startswith("CREATE TABLE IF NOT EXISTS schema_version"):
            self.conn.tables.add("schema_version")
        elif sql.startswith("SELECT version"):
            self.one = None
        elif sql.startswith("SELECT state"):
            row = self.conn.rows.get(args[0])
            self.one = (row[2],) if row else None
        elif sql.startswith("INSERT INTO vm_watch_email_state"):
            if self.conn.select_only:
                raise RuntimeError("INSERT command denied to user 'reader'")
            self.conn.pending[args[0]] = args
        return 0

    def fetchone(self):
        return self.one


class FakeConn(object):
    def __init__(self, tables=(), select_only=False):
        self.tables, self.rows, self.sql, self.pending, self.select_only = set(tables), {}, [], {}, select_only

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.rows.update(self.pending)
        self.pending = {}

    def rollback(self):
        self.pending = {}


class Database1188(unittest.TestCase):
    """1.118.8: the email state in the report database (site_vm_watch_email_db), for when AAP cannot be read."""

    def test_made_on_the_first_run_then_written_and_read_back(self):
        conn = FakeConn()
        self.assertEqual(edb.schema(conn, True), (True, True))
        self.assertIn("vm_watch_email_state", conn.tables)
        self.assertIsNone(edb.read(conn, 71))                                          # no row yet: the first email goes
        st = floor([cond("esx01")])["state"]
        edb.write(conn, 71, 2001, st)
        self.assertEqual(edb.read(conn, 71), json.loads(json.dumps(st)))
        self.assertIsNone(edb.read(conn, 72))                                          # one row per template
        self.assertEqual(edb.schema(conn, True), (True, False))
        dry = FakeConn()
        self.assertEqual(edb.schema(dry, False), (False, False))                      # create false (a dry run): not made
        self.assertEqual(dry.tables, set())
        self.assertFalse(any(x.startswith("CREATE") for x in dry.sql))

    def test_the_read_makes_no_table_in_a_dry_run(self):
        with open(os.path.join(REPO, "roles", "vmware_vm", "tasks", "watch_email.yml")) as fh:
            tasks = yaml.safe_load(fh)
        read = next(t for t in tasks if (t.get("site_vm_watch_email_db") or {}).get("mode") == "read")
        self.assertEqual(read["site_vm_watch_email_db"]["create"], "{{ not ansible_check_mode }}")
        self.assertFalse(any((t.get("site_vm_watch_email_db") or {}).get("mode") == "write" for t in tasks))

    def write_task(self):
        with open(os.path.join(REPO, "roles", "vmware_vm", "tasks", "watch.yml")) as fh:
            tasks = yaml.safe_load(fh)
        names = [t.get("name") for t in tasks]
        write = next(t for t in tasks if (t.get("site_vm_watch_email_db") or {}).get("mode") == "write")
        return tasks, names, write

    def test_the_write_is_after_the_email(self):
        # Codex review (P1): written after site_mail - a send that fails stops the job first, the last state stays
        tasks, names, write = self.write_task()
        self.assertGreater(names.index(write["name"]), names.index("VMware | the review's AAR draft and the outage report"))
        self.assertIn("not ansible_check_mode", write["when"])

    def test_the_state_as_sent_only_when_site_mail_answered(self):
        import jinja2
        from ansible.plugins.filter.core import FilterModule
        from ansible.plugins.test.core import TestModule
        _, _, write = self.write_task()
        env = jinja2.Environment()
        env.filters.update(FilterModule().filters())
        env.tests.update(TestModule().tests())
        sent, carry = {"sent_ts": 2}, {"sent_ts": 1}
        base = {"_vm_w_mail": {"send": True, "state": sent, "carry": carry}, "_vm_w_mail_out": True}

        def keep(**ctx):
            c = dict(base, **ctx)
            c["_vm_w_mail_went"] = env.from_string(write["vars"]["_vm_w_mail_went"]).render(c).strip() == "True"
            return env.from_string(write["vars"]["_vm_w_mail_dbkeep"]).render(c)
        self.assertEqual(keep(_email_sent={"changed": False, "msg": "sent"}), str(sent))
        self.assertEqual(keep(_email_sent={"skipped": True}), str(carry))               # not sent: the last state
        self.assertEqual(keep(), str(carry))                                            # no send at all (a blip)
        self.assertEqual(keep(_email_sent={"msg": "sent"}, _vm_w_mail_out=False), str(carry))   # no recipient
        self.assertEqual(keep(_vm_w_mail={"send": False, "state": carry, "carry": carry}), str(carry))

    def test_a_write_is_checked_on_the_read(self):
        # Codex review: a SELECT-only login reads the table but could never keep the state - not "ok"
        conn = FakeConn(["vm_watch_email_state"])
        edb.write(conn, 71, 1, {"ids": {}})
        self.assertTrue(edb.writable(conn, 71))
        self.assertEqual(json.loads(conn.rows[71][2]), {"ids": {}})                    # rolled back: the row as it was
        self.assertTrue(edb.writable(conn, 72))
        self.assertNotIn(72, conn.rows)                                                 # the probe row is not kept
        ro = FakeConn(["vm_watch_email_state"], select_only=True)
        with self.assertRaises(RuntimeError):
            edb.writable(ro, 71)
        # such a database counts as no state: criticals only, in the first 15 minutes of each slot
        d = floor([cond("esx01")], None, T0 + 1200, db={"ok": False, "error": "the login cannot write"}, why="no AAP connection")
        self.assertEqual((d["send"], d["source"]), (False, "none"))
        self.assertIn("report database: the login cannot write", d["lines"][-1])

    def test_a_bounded_read_timeout(self):
        import inspect
        src = inspect.getsource(edb.main)
        self.assertIn('read_timeout=dict(type="int", default=20)', src)

    def test_a_row_that_is_not_a_state_is_none(self):
        conn = FakeConn(["vm_watch_email_state"])
        conn.rows[71] = (71, 1, "not json")
        self.assertIsNone(edb.read(conn, 71))

    def test_the_statement_in_the_doc_is_the_modules(self):
        with open(os.path.join(REPO, "docs", "REPORT_DATABASE.md")) as fh:
            doc = fh.read()
        for part in edb.ddl().split("\n\n"):
            self.assertIn(part, doc, "docs/REPORT_DATABASE.md: the statement differs from site_vm_watch_email_db.ddl()")

    def test_the_settings_are_in_the_defaults_and_the_docs(self):
        for path in ("roles/vmware_vm/defaults/main.yml", "docs/VARIABLES_REFERENCE.md", "docs/VM_OUTAGE_WATCH.md"):
            with open(os.path.join(REPO, path)) as fh:
                text = fh.read()
            for name in ("vm_watch_email_min_minutes", "vm_watch_email_db"):
                self.assertIn(name, text, "%s: %s" % (path, name))


def playbook():
    ap = os.environ.get("ANSIBLE_PLAYBOOK")
    if ap:
        return ap
    near = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return near if os.path.exists(near) else shutil.which("ansible-playbook")


class Playbook1125(unittest.TestCase):
    """watch_email.yml with ansible-playbook: the state of run 1 is in AAP as its job's artifact; run 2 (same conditions)
    sends no email; run 3 (a new condition) does."""

    @classmethod
    def setUpClass(cls):
        cls.ap = playbook()
        if not cls.ap:
            raise unittest.SkipTest("ansible-playbook not found")
        from http.server import ThreadingHTTPServer
        for k in ("AAPMOCK_LEGACY", "AAPMOCK_JOBS_FORBID", "AAPMOCK_JOBS_HIDE", "AAPMOCK_JOB_CUT"):
            os.environ.pop(k, None)
        spec = importlib.util.spec_from_file_location("aapmock_watch", os.path.join(HERE, "servicenow", "aapmock.py"))
        cls.mock = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mock)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), cls.mock.H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.tmp = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_watch(self, job, conds, now, env=None, more=None):
        pb = os.path.join(self.tmp, "play.yml")
        with open(pb, "w") as fh:
            fh.write("- hosts: localhost\n  gather_facts: false\n  tasks:\n"
                     "    - ansible.builtin.include_role:\n        name: vmware_vm\n        tasks_from: watch_email.yml\n"
                     "    - ansible.builtin.copy:\n        content: \"{{ _vm_w_mail | to_json }}\"\n        dest: %s\n"
                     % os.path.join(self.tmp, "mail.json"))
        ev = dict(os.environ, ANSIBLE_CONFIG=os.path.join(REPO, "ansible.cfg"), ANSIBLE_ROLES_PATH=os.path.join(REPO, "roles"),
                  ANSIBLE_FILTER_PLUGINS=os.path.join(REPO, "plugins", "filter"), ANSIBLE_NOCOLOR="1",
                  ANSIBLE_LOCALHOST_WARNING="False", ANSIBLE_INVENTORY_UNPARSED_WARNING="False",
                  ANSIBLE_DEPRECATION_WARNINGS="False", ANSIBLE_STDOUT_CALLBACK="default",
                  CONTROLLER_HOST="http://127.0.0.1:%d" % self.srv.server_port, CONTROLLER_USERNAME="admin",
                  CONTROLLER_PASSWORD="goodpw", REPORT_DB_HOST="")
        ev.update(env or {})
        extra = {"ansible_python_interpreter": sys.executable, "awx_job_id": job, "awx_job_template_id": 71,
                 "_vm_w_cond": conds, "_vm_w": {"vcenters": [{"name": "vc01.example.mil", "ok": True, "error": ""}]}, "_vm_now": now}
        extra.update(more or {})
        r = subprocess.run([self.ap, "-i", "localhost,", "-c", "local", pb, "-e", json.dumps(extra)], cwd=REPO, env=ev,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout)
        with open(os.path.join(self.tmp, "mail.json")) as fh:
            return json.load(fh), r.stdout

    def test_two_runs_one_email_then_a_change(self):
        c1 = [cond("esx01"), cond("ds-07", kind="datastore", otype="datastore")]
        d1, _ = self.run_watch(2001, c1, T0)
        self.assertEqual((d1["send"], d1["kind"]), (True, "first"))                    # template 71's runs have no state yet
        # run 1 failed (the watch fails on critical by design): its artifact is still read
        self.mock.JOBS_71.append({"id": 2001, "job_template": 71, "status": "failed", "finished": "2026-11-23T06:01:00Z",
                                  "artifacts": {"vm_watch_email_state": d1["state"]}})
        d2, out = self.run_watch(2002, c1, T0 + 900)
        self.assertEqual((d2["send"], d2["kind"]), (False, "quiet"), out)
        self.assertEqual(sum(1 for d in (d1, d2) if d["send"]), 1)
        self.assertIn("nothing changed since the email of 2026-11-23 06:00 UTC", d2["why"])
        self.mock.JOBS_71.append({"id": 2002, "job_template": 71, "status": "failed", "finished": "2026-11-23T06:16:00Z",
                                  "artifacts": {"vm_watch_email_state": d2["state"]}})
        d3, _ = self.run_watch(2003, c1 + [cond("esx05")], T0 + 1800)
        self.assertEqual((d3["send"], d3["changed"]), (True, ["New: host:esx05 (critical)"]))
        # Codex review: report_history: false (an extra var, the strip of past runs off) still reads the state
        d4, out = self.run_watch(2003, c1, T0 + 1800, more={"report_history": False})
        self.assertEqual((d4["send"], d4["kind"]), (False, "quiet"), out)

    def test_no_aap_credential_emails_and_says_why(self):
        # 1.118.8: no AAP and no report database - a critical is emailed in the first 15 minutes of each 30-minute slot only
        env = {"CONTROLLER_HOST": "", "CONTROLLER_USERNAME": "", "CONTROLLER_PASSWORD": "", "REPORT_DB_HOST": ""}
        d, out = self.run_watch(2101, [cond("esx01")], T0, env=env)
        self.assertEqual((d["send"], d["kind"], d["source"]), (True, "unknown", "none"))
        self.assertIn("No AAP credential or report database: the 30-minute email limit cannot work - attach the Red Hat "
                      "Ansible Automation Platform credential to VM - outage watch (AAP: no AAP connection", out)
        self.assertIn("report database: no Report database credential)", out)
        d, out = self.run_watch(2102, [cond("esx01")], T0 + 1200, env=env)
        self.assertFalse(d["send"])

    def test_a_change_inside_30_minutes_is_held(self):
        # 1.118.8, through the task file: the state of run 1 in AAP; run 2, ten minutes later with a new condition, is held
        c1 = [cond("esx07")]
        d1, _ = self.run_watch(2201, c1, T0)
        self.assertTrue(d1["send"])
        job = {"id": 2201, "job_template": 71, "status": "failed", "finished": "2026-11-23T06:01:00Z",
               "artifacts": {"vm_watch_email_state": d1["state"]}}
        self.mock.JOBS_71.append(job)
        try:
            d2, out = self.run_watch(2202, c1 + [cond("esx08")], T0 + 600)
            self.assertEqual((d2["send"], d2["held"], d2["source"]), (False, True, "aap"), out)
            self.assertEqual(d2["why"], "last one at 06:00, next possible at 06:30 (vm_watch_email_min_minutes: 30)")
            self.assertEqual(d2["state"]["ids"], d1["state"]["ids"])
        finally:
            self.mock.JOBS_71.remove(job)


if __name__ == "__main__":
    unittest.main()
