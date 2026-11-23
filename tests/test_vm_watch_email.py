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

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
import vmware_watch_filters as w  # noqa: E402

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
                  CONTROLLER_PASSWORD="goodpw")
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
        d, out = self.run_watch(2101, [cond("esx01")], T0, env={"CONTROLLER_HOST": "", "CONTROLLER_USERNAME": "", "CONTROLLER_PASSWORD": ""})
        self.assertEqual((d["send"], d["kind"]), (True, "unknown"))
        self.assertIn("Email on every report this run: the last email could not be read from AAP - no AAP connection", out)


if __name__ == "__main__":
    unittest.main()
