#!/usr/bin/env python3
"""1.116.0: the VM outage watch's scheduled path is frozen at 1.118.4 (what the user runs). The review is built on the watch's
own conditions and never changes how a scheduled run reads vCenter, judges a VM, builds its time line, or opens and clears
tickets. Changing what is pinned here is a watch release of its own (with its own tests), not part of the review.
  python3 tests/test_watch_frozen.py"""
import copy
import hashlib
import importlib.util
import os
import sys
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "plugins", "filter"), os.path.join(ROOT, "tests")]
FROZEN = {   # sha256 of 1.118.4 (084e5931)
    "roles/vmware_vm/library/site_vmware_watch.py": "d2e47963d3e749b5813869970aab4edb2f7dc4c620b5d8de2810577570dba001",
    "plugins/filter/vmware_watch_filters.py": "53e81f37e68ce543ef43a07fdb677252f8797bb82b89e63de705e9d207daa979",
    # 1.118.4's plugins/filter/outage_timeline_filters.py, word for word: a scheduled run's time line is made by it
    "plugins/module_utils/outage_timeline_1184.py": "245830afda1069456ca5301ae551de68bbbf13b3ce9ad95aa792d8e7e6c3cdb1",
    # 1.118.4's roles/vmware_vm/tasks/watch.yml, the reference the task comparison below reads
    "tests/fixtures/watch_1184.yml": "66889c040b95d9749d8fcb768c9e82e1941698ee30b6cfc10668b799d11c25bd",
}
# The tasks of 1.118.4's watch.yml that differ at 1.116.0, each with why a scheduled run (vm_watch_review false) does exactly
# what it did. Any other difference fails.
ALLOWED = {
    "VMware | what is down or hung (the suspects checked {{ vm_watch_confirm_checks }} time(s), {{ vm_watch_confirm_seconds }} s apart)":
        "no_log: vm_watch_review - false on a scheduled run",
    "VMware | vCenter's events of the window": "scheduled read keeps the original arguments and outer rescue; the review reads separately",
    "VMware | the tickets of the window (ServiceNow, GET only; skipped without the ServiceNow API credential)":
        "review: omit unless a review; no_log: false unless a review",
    "VMware | the time line": "scheduled read and options match the fixture, plus review=false to select the frozen filter",
    "VMware | the time line was not made (the watch goes on without it)": "the message is 1.118.4's unless a review",
    "VMware | the AAR draft's facts (vm_outage_review.aar)": "inside the review-only AAR block",
    "VMware | a temporary folder for the AAR draft": "renamed inside the review-only AAR block",
    "VMware | the AAR draft - {{ _rd_file.filename | default('not made') }}": "inside the review-only AAR block",
    "VMware | the report": "the report info adds attachment keys only for a review",
    "VMware | outage report": "every new value is guarded by _vm_w_review, or is '' / false, which site_email treats as unset",
}


def _flat(tasks, out):
    for t in tasks or []:
        out.append(t)
        for k in ("block", "rescue", "always"):
            if k in t:
                _flat(t[k], out)
    return out


def _tasks(path):
    with open(os.path.join(ROOT, path)) as fh:
        return {t["name"]: t for t in _flat(yaml.safe_load(fh), [])}


def _render_context(review):
    from ansible.plugins.filter.core import FilterModule
    from jinja2 import StrictUndefined
    from jinja2.nativetypes import NativeEnvironment
    from site_filters import name_list

    env = NativeEnvironment(undefined=StrictUndefined)
    core = FilterModule().filters()
    env.filters.update(combine=core["combine"], bool=core["bool"], name_list=name_list,
                       outage_timeline=lambda events, tickets, opts: (events, tickets, opts))
    context = dict(vm_watch_review=review, _vm_w_review=review, _vm_w_win=dict(begin=100, end=2400),
                   vm_watch_review_from="", vm_watch_review_low_seconds=120, vm_watch_review_min_seconds=60,
                   vm_watch_review_skip_blips=True, vm_watch_report_on=["critical"], awx_job_id=42,
                   _vm_w_ev=dict(events=[{"key": 1}], capped=["vc.example.mil"]), _vm_w_tk=dict(tickets=[{"number": "INC0001"}]),
                   _vm_w_aar_file={}, _vm_w_aar_error="", _vm_w_aar={})
    return env, context


def _render_fact(task, key, env, context):
    values = dict(context)
    for name, expr in task.get("vars", {}).items():
        values[name] = env.from_string(expr).render(values)
    return env.from_string(task["ansible.builtin.set_fact"][key]).render(values)


class Frozen(unittest.TestCase):
    def test_1_files_are_1_118_4(self):
        for path, want in FROZEN.items():
            with open(os.path.join(ROOT, path), "rb") as fh:
                self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), want, "%s changed: the watch is frozen at 1.118.4" % path)

    def test_2_watch_yml_differs_only_where_allowed(self):
        with open(os.path.join(ROOT, "tests", "fixtures", "watch_1184.yml")) as fh:
            old = _flat(yaml.safe_load(fh), [])
        with open(os.path.join(ROOT, "roles", "vmware_vm", "tasks", "watch.yml")) as fh:
            new = dict((t.get("name"), t) for t in _flat(yaml.safe_load(fh), []))
        strip = lambda t: dict((k, v) for k, v in t.items() if k not in ("block", "rescue", "always"))   # noqa: E731
        changed = set(t.get("name") for t in old if t.get("name") not in new or strip(new[t["name"]]) != strip(t))
        self.assertEqual(sorted(changed - set(ALLOWED)), [], "a scheduled-path task of watch.yml changed")
        # the order of 1.118.4's tasks is kept
        names = [n for n in (t.get("name") for t in _flat(yaml.safe_load(open(os.path.join(ROOT, "roles", "vmware_vm", "tasks", "watch.yml"))), []))
                 if n in set(t.get("name") for t in old)]
        self.assertEqual(names, [t.get("name") for t in old if t.get("name") in names])

    def test_3_a_scheduled_time_line_is_1_118_4s(self):
        import outage_timeline_filters as ot
        spec = importlib.util.spec_from_file_location("ot1184", os.path.join(ROOT, "plugins", "module_utils", "outage_timeline_1184.py"))
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        import test_outage_timeline as tot
        events, tickets, opts = tot.director_outage()
        sched = dict(opts, review=False)
        for evs in (events,
                    # the 1.116.0 check's case: an APD raise, and another device's clear 30 s later - 1.118.4 gives one Low row
                    [dict(time=1000, vcenter="vc01", host="esx01", type="esx.problem.storage.apd.start",
                          message="Device naa.1111 has entered the All Paths Down state"),
                     dict(time=1030, vcenter="vc01", host="esx01", type="esx.clear.storage.apd.exit",
                          message="Device naa.2222 has exited the All Paths Down state")]):
            o = dict(sched) if evs is events else dict(begin=900, end=1200, review=False)
            got = ot.outage_timeline(copy.deepcopy(evs), copy.deepcopy(tickets), copy.deepcopy(o))
            want = old.outage_timeline(copy.deepcopy(evs), copy.deepcopy(tickets), copy.deepcopy(o))
            self.assertEqual(got, want)
            doc = copy.deepcopy(tot.WATCH)
            for web in (False, True):
                self.assertEqual(ot.outage_timeline_doc(copy.deepcopy(doc), got, web, False), old.outage_timeline_doc(copy.deepcopy(doc), want, web, False))
            info = {"file": "", "error": "", "job": "5120", "asked": False, "draft": False}
            self.assertEqual(ot.outage_review_aar(copy.deepcopy(doc), info, True), old.outage_review_aar(copy.deepcopy(doc), info, True))
        self.assertEqual(ot.outage_window(10000, 6, "", "", ""), old.outage_window(10000, 6, "", "", ""))

    def test_scheduled_task_expressions(self):
        old = _tasks("tests/fixtures/watch_1184.yml")
        new = _tasks("roles/vmware_vm/tasks/watch.yml")
        env, context = _render_context(False)  # _vm_w_review_opts and review attachment facts are undefined.
        name = "VMware | the time line"
        events, tickets, opts = _render_fact(old[name], "_vm_w_tl", env, context)
        self.assertEqual(_render_fact(new[name], "_vm_w_tl", env, context), (events, tickets, dict(opts, review=False)))
        name = "VMware | the report"
        self.assertEqual(_render_fact(new[name], "_vm_w_aar_info", env, context),
                         _render_fact(old[name], "_vm_w_aar_info", env, context))
        name = "VMware | vCenter's events of the window"
        scheduled = dict(new[name])
        self.assertTrue(env.compile_expression(scheduled.pop("when"))(**context))
        self.assertEqual(scheduled, old[name])
        block = "VMware | the outage time line (vm_watch_review, vm_watch_timeline)"
        self.assertIn(new[name], new[block]["block"])
        env.filters["outage_redact"] = lambda _: self.fail("the scheduled rescue evaluated the review branch")
        context["ansible_failed_result"] = dict(msg="example read failure")
        def rescue_message(tasks):
            task, = tasks[block]["rescue"]
            return env.from_string(task["ansible.builtin.debug"]["msg"]).render(context)
        self.assertEqual(rescue_message(new), rescue_message(old))

    def test_review_event_read_rescue(self):
        import outage_timeline_filters as ot
        from test_outage_timeline import WATCH

        tasks = _tasks("roles/vmware_vm/tasks/watch.yml")
        window = tasks["VMware | the outage time line (vm_watch_review, vm_watch_timeline)"]["block"]
        review = tasks["VMware | the review's event read"]
        timeline = tasks["VMware | the time line"]
        self.assertLess(window.index(review), window.index(timeline))
        self.assertNotIn("when", timeline)
        env, context = _render_context(True)
        guard = env.compile_expression(review["when"])
        self.assertTrue(guard(**context))
        self.assertFalse(guard(**dict(context, vm_watch_review=False, _vm_w_review=False)))
        read, = review["block"]
        self.assertTrue(read["site_vmware_outage_events"]["review"])
        self.assertTrue(read["site_vmware_outage_events"]["review_storage_events"])
        rescue, = review["rescue"]
        context.update(rescue["ansible.builtin.set_fact"])
        self.assertIn(read["register"], rescue["ansible.builtin.set_fact"])
        condition = dict(kind="datastore", object_type="datastore", object="example-store", vcenter="vc.example.mil",
                         id="vm-watch:datastore:example-store", summary="datastore example-store is not accessible: VM files lost")
        run = dict(finished=2500, artifacts=dict(vm_watch=dict(read_at=700, conditions=[condition])))
        context.update(outage_ops_center="NOC", _vm_w_tk={})
        for runs in ([run], []):
            context["_vm_w_review_opts"] = dict(watch_runs=runs)
            events, tickets, opts = _render_fact(timeline, "_vm_w_tl", env, context)
            self.assertEqual(events, [])
            self.assertTrue(opts["events_not_read"])
            result = ot.outage_timeline(events, tickets, opts)
            draft = result["draft"]
            self.assertEqual([row[0] for row in draft["went"]], ["example-store"] if runs else [])
            self.assertIn("events not read", draft["went_note"])
            for web in (True, False):
                sections = ot.outage_timeline_doc(WATCH, result, web, True)["sections"]
                judged = next(s for s in sections if s["title"] == "How it judged")
                self.assertIn("vCenter events not read.", judged["lines"])
                if not runs:
                    self.assertIn("events not read", sections[0]["lines"][0])


if __name__ == "__main__":
    unittest.main(verbosity=1)
