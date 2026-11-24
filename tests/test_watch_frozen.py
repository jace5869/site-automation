#!/usr/bin/env python3
"""1.116.0: the VM outage watch's scheduled path is frozen at 1.118.4 (what the user runs). The review is built on the watch's
own conditions and never changes how a scheduled run reads vCenter, judges a VM, builds its time line, or opens and clears
tickets. Changing what is pinned here is a watch release of its own (with its own tests), not part of the review.
  python3 tests/test_watch_frozen.py"""
import copy
import datetime
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
    # 1.116.4: the task files watch.yml includes on every run, as 1.118.4 had them
    "tests/fixtures/sso_read_1184.yml": "93fb2c6f1e3dfc46827d134872d13ea75600b2986d02b6790a4bd81dbea4de0a",
    "tests/fixtures/vcenters_read_1184.yml": "db6a64f3b95abd15dd769a951d6a5b3f814d818e6ed71ebb1fe148511617069c",
    "tests/fixtures/watch_email_1184.yml": "59c67935b401b3c85285e364a7f5293b9366b27c0be1f2d4d5f0d1296baec242",
}
# 1.118.8 (user-requested email limit): the email decision's own files, pinned at 1.118.8. Changing them is a release of
# its own, with tests/test_vm_watch_email.py.
FROZEN_1188 = {
    "plugins/filter/vmware_watch_email_filters.py": "95bfaf2b6e3b8133dd049176c092e7f54b356562e203d5a1de4b9294c870bdac",
    "roles/vmware_vm/tasks/watch_email.yml": "f5c498cacb702dc39c11f4cdad036c7b7d592006bde7b0f4635741ea8bef1c5b",
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
    "VMware | {{ _vm_w | vm_watch_clean_line(_vm_w_cond) if _vm_w_cond | length == 0 else 'below the report threshold (vm_watch_report_on)' }}":
        "only a requested review redacts the debug lines",
    "VMware | single sign-on and OCSP - read": "passes the review-only no_log flag to the shared reader",
    "VMware | phase 2's decision (made, not carried out)": "no_log only on a requested review",
    "VMware | the watch's email state as a job artifact (vm_watch_email_state)": "no_log only on a requested review",
    "VMware | nothing down or hung (vm_watch_fail_on)": "only a requested review masks the failure message",
    "VMware | what was found, and what phase 2 would restart (nothing is restarted)":
        "no_log only on a requested review",
    "VMware | single sign-on and OCSP - what is down now": "no_log only on a requested review",
    "VMware | the result as job artifacts (vm_watch; site_health for the ServiceNow tickets job)":
        "raw machine artifacts; no_log only on a requested review",
    "VMware | every vCenter was read": "only a review passes the redacted read to the final error message",
    "VMware | the AAR draft was not made (the review goes on without it)": "redact a write failure inside the review-only AAR block",
    "VMware | the report": "only a review redacts the finished document; the report info adds attachment keys only for a review",
    "VMware | outage report": "every new value is guarded by _vm_w_review, or is '' / false, which site_email treats as unset",
    # 1.118.8 (user-requested email limit): the report is also made when the limit sends changes it held, on a run with
    # nothing down now (_vm_w_mail.held_send); without held changes the condition is 1.118.4's
    "VMware | the outage report": "1.118.8: or _vm_w_mail.held_send, false unless the email limit sends held changes",
}
# 1.118.8: tasks added to watch.yml that run on a scheduled run - the email state kept in the report database after the
# email went (a failed send stops the job before it). tests/test_vm_watch_email.py covers it.
ADDED_1188_WATCH = {
    "VMware | the watch's email state in the report database (vm_watch_email_db)": "after the email, never before it",
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

    def test_1_email_files_are_1_118_8(self):
        for path, want in FROZEN_1188.items():
            with open(os.path.join(ROOT, path), "rb") as fh:
                self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), want, "%s changed: the watch's email is pinned at 1.118.8" % path)

    def test_2_watch_yml_differs_only_where_allowed(self):
        with open(os.path.join(ROOT, "tests", "fixtures", "watch_1184.yml")) as fh:
            old = _flat(yaml.safe_load(fh), [])
        with open(os.path.join(ROOT, "roles", "vmware_vm", "tasks", "watch.yml")) as fh:
            tasks = yaml.safe_load(fh)
        self.assert_watch_tasks(old, tasks)

    def assert_watch_tasks(self, old, tasks):
        from jinja2 import Environment, nodes

        def requires_review(expr):
            if isinstance(expr, nodes.Filter) and expr.name == "bool":
                return requires_review(expr.node)
            if isinstance(expr, nodes.And):
                return requires_review(expr.left) or requires_review(expr.right)
            if isinstance(expr, nodes.Or):
                return requires_review(expr.left) and requires_review(expr.right)
            return isinstance(expr, nodes.Name) and expr.name in ("_vm_w_review", "vm_watch_review")

        old_names = {t["name"] for t in old}
        env = Environment()

        def check_added(tasks, guarded=False):
            for task in tasks:
                when = task.get("when", [])
                expressions = when if isinstance(when, list) else [when]
                guarded_here = guarded or any(requires_review(env.parse("{{ " + str(expr) + " }}").body[0].nodes[0])
                                              for expr in expressions)
                if task.get("name") not in old_names and task.get("name") not in ADDED_1188_WATCH:
                    self.assertTrue(guarded_here, "missing review guard: %s" % task.get("name", task))
                for branch in ("block", "rescue", "always"):
                    check_added(task.get(branch, []), guarded_here)

        check_added(tasks)
        new = {t.get("name"): t for t in _flat(tasks, [])}
        self.assertEqual(len(new), len(_flat(tasks, [])), "duplicate watch task name")
        strip = lambda t: dict((k, v) for k, v in t.items() if k not in ("block", "rescue", "always"))   # noqa: E731
        changed = set(t.get("name") for t in old if t.get("name") not in new or strip(new[t["name"]]) != strip(t))
        self.assertEqual(sorted(changed - set(ALLOWED)), [], "a scheduled-path task of watch.yml changed")
        # the order of 1.118.4's tasks is kept
        names = [t.get("name") for t in _flat(tasks, []) if t.get("name") in old_names]
        self.assertEqual(names, [t.get("name") for t in old if t.get("name") in names])

    def test_added_task_probe(self):
        with open(os.path.join(ROOT, "tests/fixtures/watch_1184.yml")) as fh:
            old = _flat(yaml.safe_load(fh), [])
        with open(os.path.join(ROOT, "roles/vmware_vm/tasks/watch.yml")) as fh:
            tasks = yaml.safe_load(fh)
        probe = {"name": "unguarded probe", "ansible.builtin.debug": {"msg": "example"}}
        for guard in (None, "not vm_watch_review | bool", "_vm_w_review | bool or other"):
            with self.subTest(guard=guard), self.assertRaisesRegex(AssertionError, "unguarded probe"):
                self.assert_watch_tasks(old, tasks + [dict(probe, when=guard) if guard else probe])

    def test_raw_review_ids_through_throttle_and_artifacts(self):
        import outage_timeline_filters as ot
        from vmware_watch_filters import FilterModule
        from vmware_watch_email_filters import FilterModule as EmailFilters

        tasks = _tasks("roles/vmware_vm/tasks/watch.yml")
        mail = _tasks("roles/vmware_vm/tasks/watch_email.yml")
        env, ctx = _render_context(True)
        env.filters.update(FilterModule().filters(), outage_redact=ot.outage_redact)
        env.filters.update(EmailFilters().filters())
        ctx["_vm_w_mail_db"] = {}                                       # 1.118.8: the database read, skipped (AAP answered)
        vc, host = "vc.example.mil", "esx-token-01"
        cid = "vm-watch:host:" + host
        state = dict(ids={cid + "@" + vc: "critical"}, review_ts=1000, sent_ts=1000)
        raw = dict(hosts=[dict(name=host, vcenter=vc, connection="disconnected")], vcenters=[dict(name=vc, ok=True, error="")])
        ctx.update(_vm_w=raw, _vm_w_actions={}, _vm_prot=dict(ids=[], names=[]),
                   vm_watch_protect=[], vm_watch_one_signal_severity="warning", _vm_now=1100,
                   vm_watch_email_on_change=True, vm_watch_email_every_hours=4,
                   _site_hist_runs=[dict(artifacts=dict(vm_watch_email_state=state))])
        ctx["now"] = lambda: datetime.datetime.fromtimestamp(1100, datetime.timezone.utc)
        conditions = tasks["VMware | what was found, and what phase 2 would restart (nothing is restarted)"]
        ctx["_vm_w_cond"] = _render_fact(conditions, "_vm_w_cond", env, ctx)
        decision = next(t for t in mail.values() if "_vm_w_mail" in t.get("ansible.builtin.set_fact", {}))
        stats = next(t["ansible.builtin.set_stats"]["data"] for t in tasks.values() if "site_health for" in t["name"])
        for scheduled in (True, False):
            with self.subTest(scheduled=scheduled):
                ctx["awx_job_launch_type"] = "scheduled" if scheduled else "manual"
                result = _render_fact(decision, "_vm_w_mail", env, ctx)
                ctx["_vm_w_review"] = result["review"]
                self.assertEqual(result["review"], not scheduled)
                self.assertEqual(result["state"]["ids"], state["ids"])
                self.assertEqual(result["send"], not scheduled)
                health = env.from_string(stats["site_health"]).render(ctx)
                artifact = env.from_string(stats["vm_watch"]).render(ctx)
                self.assertEqual(health[vc]["findings"][0]["id"], cid)
                self.assertEqual(artifact["conditions"][0]["id"], cid)

    def test_raw_sso_read_hidden_on_watch_review(self):
        tasks = _tasks("roles/vmware_vm/tasks/sso_read.yml")
        read = next(t for t in tasks.values() if t.get("register") == "_vm_sso")
        guard = read.get("no_log", False)
        env, ctx = _render_context(True)
        watch = _tasks("roles/vmware_vm/tasks/watch.yml")
        include = next(t for t in watch.values() if t.get("ansible.builtin.include_tasks") == "sso_read.yml")
        for review in (True, False):
            ctx["vm_watch_review"] = review
            for key, expr in include.get("vars", {}).items():
                ctx[key] = env.from_string(expr).render(ctx) if isinstance(expr, str) else expr
            self.assertEqual(env.from_string(str(guard)).render(ctx), review)

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
        self.assertFalse(env.from_string(scheduled.pop("no_log")).render(context))
        self.assertEqual(scheduled, old[name])
        block = "VMware | the outage time line (vm_watch_review, vm_watch_timeline)"
        self.assertIn(new[name], new[block]["block"])
        env.filters["outage_redact"] = lambda _: self.fail("the scheduled rescue evaluated the review branch")
        context["ansible_failed_result"] = dict(msg="example read failure")
        def rescue_message(tasks):
            task, = tasks[block]["rescue"]
            return env.from_string(task["ansible.builtin.debug"]["msg"]).render(context)
        self.assertEqual(rescue_message(new), rescue_message(old))

    def test_watch_output_redaction(self):
        import outage_timeline_filters as ot
        from vmware_watch_filters import FilterModule

        tasks = _tasks("roles/vmware_vm/tasks/watch.yml")
        attachments = _tasks("roles/incident_docs/tasks/review_draft.yml")
        raw = "password=EXAMPLE_VALUE"
        report = tasks["VMware | outage report"]["vars"]
        make = tasks["VMware | the report"]["ansible.builtin.set_fact"]["_vm_w_doc"]
        below = next(t for name, t in tasks.items() if "below the report threshold" in name)
        for review in (False, True):
            env, ctx = _render_context(review)
            env.filters.update(FilterModule().filters())
            env.filters.update(outage_redact=ot._redact, report_text=lambda d: d["title"])
            env.filters["vm_watch_report"] = lambda *args: dict(title=raw, sections=[])
            env.filters["vm_watch_not_judged"] = lambda _: [dict(name="example-vm", why=raw)]
            env.filters["zip"] = zip
            ctx.update(_vm_w=dict(vcenters=[dict(name="vc", error=raw)]), _vm_w_actions={},
                       _vm_w_cond=[dict(summary=raw, severity="warning")], _vm_w_tl={},
                       _vm_w_mail=dict(kind="review", send=False), ansible_failed_result=dict(msg=raw))
            def render(expr):
                return env.from_string(expr).render(ctx)
            ctx["_vm_w_doc"] = render(make)
            expected = "password=<removed>" if review else raw
            for key in ("_vm_vcenters", "_vm_report_subject", "_vm_report_lines"):
                with self.subTest(review=review, key=key):
                    self.assertIn(expected, str(render(report[key])))
            if review:
                ctx["_vm_w_tl"] = dict(closed_window=True, lines=["historical review"])
                self.assertEqual(render(report["_vm_report_lines"]), ["historical review"])
            self.assertIn(expected, str(render(below["ansible.builtin.debug"]["msg"])))
            if review:
                self.assertNotIn("EXAMPLE_VALUE", str(render(below["ansible.builtin.debug"]["msg"])))
                for group, name, key in ((tasks, "VMware | the AAR draft was not made (the review goes on without it)", "_vm_w_aar_error"),
                        (attachments, "Incident docs | the AAR draft was not made (the review goes on without it)", "_rd_error"),
                        (attachments, "Incident docs | record the handoff write error", "_rd_noc_error")):
                    self.assertEqual(render(group[name]["ansible.builtin.set_fact"][key]), expected)

    def test_requested_numbers_reach_the_review_filter(self):
        tasks = _tasks("roles/vmware_vm/tasks/watch.yml")
        env, ctx = _render_context(True)
        ctx.update(_vm_w=dict(hosts=[], datastores=[], vcenters=[], read_at=2400), _site_hist_runs=[],
                   _site_hist_why="", vm_watch_review_object="", vm_watch_review_tickets="inc0001, INC0002",
                   vm_watch_review_noise=True, vm_watch_review_noise_alarms=[], outage_aar_appendix_max=0,
                   outage_escalate_storage="", outage_escalate_network="", outage_escalate_vmware="",
                   outage_escalate_backup="", outage_ops_center="NOC", _vm_w_review_ev=dict(events=[], capped=[], vcenters=[]))
        inventory = tasks["VMware | the watch inventory and recorded roots for this review"]
        ctx["_vm_w_review_opts"] = _render_fact(inventory, "_vm_w_review_opts", env, ctx)
        _, _, opts = _render_fact(tasks["VMware | the time line"], "_vm_w_tl", env, ctx)
        self.assertEqual(opts["review_tickets"], ["inc0001", "INC0002"])

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


# 1.116.4: the task files watch.yml includes on every run. Each task that differs from 1.118.4 is listed with why a
# scheduled run (no review) does exactly what it did; every differing value is rendered for a scheduled run and must
# equal 1.118.4's (an absent no_log is false).
INCLUDED = {
    "roles/vmware_vm/tasks/sso_read.yml": ("tests/fixtures/sso_read_1184.yml", {
        "VMware | single sign-on - services, STS certificate, identity providers, a test login - and the OCSP responders":
            "no_log from _vm_sso_review, which only a review sets: false on a scheduled run"}),
    "roles/vmware_vm/tasks/vcenters_read.yml": ("tests/fixtures/vcenters_read_1184.yml", {
        "VMware | every vCenter was read": "the errors are masked only when _vm_redact_errors (a review) is set"}),
    "roles/vmware_vm/tasks/watch_email.yml": ("tests/fixtures/watch_email_1184.yml", {
        "VMware | the watch's last email, from AAP (vm_watch_email_state)": "apply: no_log from vm_watch_review",
        "VMware | does this run email (vm_watch_email_on_change, vm_watch_email_every_hours)": "no_log from vm_watch_review",
        "VMware | the watch's email": "the lines are masked only on a review"}),
}
# 1.118.8 (user-requested: "can't get 50 emails"): the watch's email gets a limit, at most one per
# vm_watch_email_min_minutes, and keeps its state in the report database when AAP cannot be read. These tasks of
# watch_email.yml are new or change on a scheduled run on purpose; tests/test_vm_watch_email.py covers them. What the watch
# finds (site_vmware_watch.py, vmware_watch_filters.py) stays 1.118.4's; watch.yml's two 1.118.8 changes are in ALLOWED
# and ADDED_1188_WATCH.
CHANGED_1188 = {
    "roles/vmware_vm/tasks/watch_email.yml": {
        "added": {
            "VMware | the report database for the watch's email state (vm_watch_email_db)": "the database host, or '' when off",
            "VMware | the watch's last email, from the report database (AAP not readable)": "read only when AAP could not be read",
        },
        "changed": {
            "VMware | the watch's last email, from AAP (vm_watch_email_state)": "also read for the limit (vm_watch_email_min_minutes)",
            "VMware | does this run email (vm_watch_email_on_change, vm_watch_email_every_hours)":
                "vm_watch_email_floor around 1.112.5's vm_watch_email_decision: the limit and the database state",
        },
    },
}


def _leaves(d, path=()):
    if isinstance(d, dict):
        for k, v in d.items():
            if k not in ("block", "rescue", "always"):
                yield from _leaves(v, path + (k,))
    else:
        yield path, d


class Included1164(unittest.TestCase):
    def _env(self):
        import jinja2
        from ansible.plugins.filter.core import FilterModule
        env = jinja2.Environment(undefined=jinja2.StrictUndefined)
        core = FilterModule().filters()
        env.filters.update(bool=core["bool"], combine=core["combine"])
        env.filters["outage_redact"] = lambda *_: self.fail("a scheduled run called outage_redact")
        return env

    def _render(self, env, value, ctx):
        if not isinstance(value, str) or "{" not in value:
            return value
        out = env.from_string(value).render(ctx)
        return {"True": True, "False": False}.get(out.strip(), out)

    def test_included_files_differ_only_where_allowed_and_render_as_1_118_4(self):
        ctx = dict(vm_watch_review=False, vm_watch_email_on_change=True, _vm_w_mail=dict(lines=["password=EXAMPLE_VALUE"]),
                   _vm_vcenters=[dict(name="vc", error="password=EXAMPLE_VALUE")])
        env = self._env()
        for path, (fixture, allowed) in INCLUDED.items():
            with open(os.path.join(ROOT, fixture)) as fh:
                old = dict((t.get("name"), t) for t in _flat(yaml.safe_load(fh), []))
            with open(os.path.join(ROOT, path)) as fh:
                new = dict((t.get("name"), t) for t in _flat(yaml.safe_load(fh), []))
            c1188 = CHANGED_1188.get(path, {})
            self.assertEqual(sorted(set(new) - set(c1188.get("added", {}))), sorted(old), "%s: a task added, removed or renamed" % path)
            for name, task in new.items():
                if name in c1188.get("added", {}) or name in c1188.get("changed", {}):
                    continue
                was = dict(_leaves(old[name]))
                now = dict(_leaves(task))
                differ = sorted(k for k in set(was) | set(now) if was.get(k) != now.get(k))
                if not differ:
                    continue
                self.assertIn(name, allowed, "%s: %s changed" % (path, name))
                for key in differ:
                    want = self._render(env, was.get(key, False if key[-1] == "no_log" else None), ctx)
                    got = self._render(env, now.get(key, False if key[-1] == "no_log" else None), ctx)
                    self.assertEqual(got, want, "%s: %s %s renders differently on a scheduled run" % (path, name, "/".join(key)))

    def test_an_unguarded_change_fails(self):
        env = self._env()
        self.assertTrue(self._render(env, "{{ true }}", {}))           # what an unguarded no_log would give: not 1.118.4's
        self.assertFalse(self._render(env, "{{ vm_watch_review | bool }}", dict(vm_watch_review=False)))

if __name__ == "__main__":
    unittest.main(verbosity=1)
