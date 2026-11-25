#!/usr/bin/env python3
"""Usage alarms across the watch, email decisions and both timelines. Run with unittest."""
import unittest

from test_vm_watch import VC, data, host, suspect, w
import outage_timeline_filters as timeline
import vmware_watch_email_filters as mail

END = 3600
CPU = "Virtual machine CPU utilization"


def state(vm, color, at, alarm=CPU):
    return dict(vcenter=VC, entity_type="vm", entity=vm, entity_moid="id-" + vm, alarm=alarm, status=color, time=at)


def change(vm, was, color, at, alarm=CPU):
    row = state(vm, color, at, alarm)
    del row["status"]
    return dict(row, type="AlarmStatusChangedEvent", to=color, **{"from": was})


def watch(events=(), states=(), **kw):
    return data(events=list(events), alarms=list(states), read_at=END, window_minutes=30, **kw)


def views(d, **opts):
    for review in (False, True):
        yield timeline.outage_timeline(d["events"], [], dict(opts, review=review, begin=1800, end=END,
                                                             read_at=d["read_at"], alarms=d["alarms"]))


class Usage(unittest.TestCase):
    def test_hidden_inputs_are_identical_to_a_clean_run(self):
        clean = watch()
        for events, states in [
            ([change("example-vm", "green", "yellow", 1900), change("example-vm", "yellow", "green", 1920)], []),
            ([], [state("example-vm", "yellow", 2000)]),
            ([change("example-vm", "yellow", "red", 2000), change("example-vm", "red", "green", 2300)], []),
            ([change("example-vm", "green", "red", 2000)], []),
            ([change("example-vm", "red", "green", 3400)], []),
            ([], [state("example-vm", "red", t) for t in ("", "unknown", END + 60)]),
            # Most of this red interval is before the selected window.
            ([change("example-vm", "green", "red", 100), change("example-vm", "red", "green", 2100)], []),
            # Red for two 10-minute intervals, separated by yellow.
            ([change("example-vm", "green", "red", 1900), change("example-vm", "red", "yellow", 2500),
              change("example-vm", "yellow", "red", 2600), change("example-vm", "red", "green", 3200)], []),
        ]:
            with self.subTest(events=events, states=states):
                d = watch(events, states)
                cs = w.vm_watch_conditions(d)
                self.assertEqual(cs, [])
                self.assertEqual(w.vm_watch_artifact(d, cs)["critical"], 0)
                self.assertEqual(w.vm_watch_report(d, cs)["status"], "ok")
                self.assertEqual(list(views(d)), list(views(clean)))

    def test_sustained_red_group_in_every_output(self):
        # One triggered state, one event pair, one raise confirmed by a current state with no time.
        events = [change("example-b", "yellow", "red", 2100), change("example-b", "red", "yellow", 3300),
                  change("example-c", "yellow", "red", 2300)]
        states = [state("example-a", "red", 2400), state("example-c", "red", "")]
        d = watch(events, states)
        cs = w.vm_watch_conditions(d)
        text = "3 VMs: CPU usage red 20+ min (example-a, example-b, example-c)"
        self.assertEqual([(c["severity"], c["summary"]) for c in cs], [("critical", text)])
        self.assertEqual(w.vm_watch_artifact(d, cs)["critical"], 1)
        self.assertEqual(len(w.vm_watch_health(d, cs)[VC]["findings"]), 1)
        self.assertIn(text, str(w.vm_watch_report(d, cs)["sections"]))
        decision = mail.vm_watch_email_floor(cs, [], dict(now=END, report_on=["critical"]))
        self.assertTrue(decision["send"] and decision["report"])
        for view in views(d):
            self.assertEqual(len(view["sheet"]["rows"]), 1)
            for key in ("sections", "web_sections", "sheet", "lines"):
                self.assertIn(text, str(view[key]), key)

    def test_report_and_fail_conditions_use_the_filtered_watch(self):
        from test_watch_frozen import _render_context, _render_fact, _tasks

        env, ctx = _render_context(False)
        tasks = _tasks("roles/vmware_vm/tasks/watch.yml")
        decide = tasks["VMware | phase 2's decision (made, not carried out)"]
        assertion = tasks["VMware | nothing down or hung (vm_watch_fail_on)"]["ansible.builtin.assert"]["that"]
        ctx.update(vm_watch_fail_on=["critical"], vm_watch_report_on=["critical", "warning"])
        for status, start, show, report, fail in [
            ("yellow", 1800, False, False, False), ("red", 3300, False, False, False),
            ("red", 2400, False, True, True), ("red", None, False, False, False),
            ("yellow", 1800, True, True, False),
        ]:
            with self.subTest(status=status, start=start, show=show):
                d = watch(states=[state("example-vm", status, start)])
                ctx["_vm_w_cond"] = w.vm_watch_conditions(d, dict(usage_show_yellow=show))
                self.assertEqual(_render_fact(decide, "_vm_w_report_it", env, ctx), report)
                self.assertEqual(env.compile_expression(assertion)(**ctx), not fail)

    def test_independent_vm_alarm_and_vcenter_pairs(self):
        left = change("example-a", "green", "red", 2000)
        right = change("example-a", "red", "green", 3400)
        d = watch([left, dict(right, vcenter="vc-b.example.mil"), dict(right, entity_moid="id-b"),
                   dict(right, alarm="Host CPU usage"), dict(right, **{"from": "yellow"})])
        self.assertEqual(w.vm_watch_conditions(d), [])
        for view in views(d):
            self.assertEqual(view["sheet"]["rows"], [])

    def test_capped_read_never_pairs_across_a_gap(self):
        d = watch([change("example-vm", "green", "red", 1000), change("example-vm", "red", "green", 3000)])
        for view in views(d):
            self.assertIn("red 20+ min", str(view["sheet"]))
        for view in views(d, capped=[VC]):
            self.assertFalse(view["sheet"]["rows"])

    def test_reader_passes_trigger_time_and_separate_alarm_event_stream(self):
        import os
        from datetime import datetime, timezone
        from types import SimpleNamespace as NS
        from unittest.mock import Mock, patch
        import ansible.module_utils
        from test_vmware_modules import wt

        entity = NS(_moId="vm-1", _wsdlName="VirtualMachine")
        alarm = NS(_moId="alarm-1")
        active = NS(entity=entity, alarm=alarm, overallStatus="red", time=datetime.fromtimestamp(2400, timezone.utc))
        root = NS(triggeredAlarmState=[active])
        fields = {"info.name": {"alarm-1": {"info.name": CPU}}, "name": {"vm-1": {"name": "example-active"}}}
        def props(content, objects, kind, paths):
            return fields.get(paths[0], {}) if objects else {}

        events = [change("example-paired", "green", "red", 2100), change("example-paired", "red", "green", 3300)]
        read_events = Mock(side_effect=[[], events])
        utils = os.path.join(os.path.dirname(__file__), "..", "plugins", "module_utils")
        params = dict(datacenter="", window_minutes=30, event_types=wt.EVENT_TYPES, usage_alarms=["CPU usage"])
        with patch.object(ansible.module_utils, "__path__", [*ansible.module_utils.__path__, utils]), \
                patch.object(wt, "vim", create=True), patch.object(wt, "_props", return_value={}), \
                patch.object(wt, "_objs_props", side_effect=props), patch.object(wt, "_events", read_events):
            result = wt._read(NS(rootFolder=root), params, END, VC)
        self.assertEqual([c.args[3] for c in read_events.call_args_list], [wt.EVENT_TYPES, ["AlarmStatusChangedEvent"]])
        cs = w.vm_watch_conditions(dict(result, read_at=END, window_minutes=30))
        self.assertEqual([c["summary"] for c in cs], ["2 VMs: CPU usage red 20+ min (example-active, example-paired)"])

    def test_exact_window_boundary_custom_threshold_and_yellow_opt_in(self):
        d = watch([change("example-vm", "green", "yellow", 1900), change("example-vm", "yellow", "red", 2700)],
                  [state("example-vm", "red", 1900)])
        # The time in yellow cannot make red qualify sooner, even if the trigger predates the promotion.
        opts = dict(usage_alarms=["CpU UsAgE"], usage_min_minutes=16, usage_show_yellow=True)
        cs = w.vm_watch_conditions(d, opts)
        self.assertEqual([(c["severity"], c["summary"]) for c in cs], [("warning", "1 VM: CpU UsAgE yellow (example-vm)")])
        for view in views(d, **opts):
            self.assertIn(cs[0]["summary"], str(view["sheet"]))
            self.assertNotIn("red 15+", str(view))
        self.assertIn("red 15+ min", w.vm_watch_conditions(d)[0]["summary"])
        self.assertEqual(w.vm_watch_conditions(d, dict(usage_alarms=[])), [])

    def test_live_usage_cannot_supply_minutes_to_a_shorter_review(self):
        d = watch(states=[state("example-vm", "red", 1800, alarm="Virtual machine memory usage")])
        triggers = w.vm_watch_conditions(d)
        self.assertEqual([c["summary"] for c in triggers], ["1 VM: memory usage red 30+ min (example-vm)"])
        for review in (False, True):
            opts = dict(begin=3300, end=END, read_at=END, review=review)
            hidden = timeline.outage_timeline([], [], dict(opts, alarms=d["alarms"], triggers=triggers))
            self.assertEqual(hidden, timeline.outage_timeline([], [], opts))

    def test_other_conditions_and_email_ids_do_not_change(self):
        hidden = [change("example-spike", "green", "yellow", 2000), change("example-spike", "yellow", "green", 2050)]
        events = [dict(time=2200, type="VmFailoverFailed", vm="example-ha", vcenter=VC),
                  dict(time=2300, type="esx.problem.net.connectivity.lost", host="example-host", vcenter=VC)]
        inventory = dict(hosts=[host("esx01.example.mil", connection="notResponding")],
                         suspects=[suspect("example-hung", "hung"), suspect("example-tools", "one-signal")])
        before, after = watch(events, **inventory), watch(events + hidden, **inventory)
        cs = w.vm_watch_conditions(before)
        self.assertEqual(cs, w.vm_watch_conditions(after))
        self.assertEqual({c["kind"] for c in cs}, {"host", "hung", "no-heartbeat", "event"})
        self.assertEqual(list(views(after)), list(views(before)))
        opts = dict(now=END, min_minutes=30, report_on=["warning", "critical"])
        for baseline in (cs, []):
            state_before = mail.vm_watch_email_floor(baseline, [], opts)["state"]
            runs = [dict(artifacts=dict(vm_watch_email_state=state_before))]
            changed = w.vm_watch_conditions(after if baseline else watch(hidden))
            result = mail.vm_watch_email_floor(changed, runs, dict(opts, now=END + 300))
            self.assertFalse(result["send"] or result["held"])
            self.assertEqual(result["state"]["ids"], state_before["ids"])


def read(triggered, names, fault=None, events=()):
    """wt._read on a fake vCenter: triggered alarm states, {alarm moid: definition name}; fault raised by the definitions read."""
    import os
    from types import SimpleNamespace as NS
    from unittest.mock import Mock, patch
    import ansible.module_utils
    from test_vmware_modules import wt

    labels = {s.entity._moId: {"name": "example-" + s.entity._moId} for s in triggered if getattr(s, "entity", None)}

    def props(content, objects, kind, paths):
        if paths[0] == "info.name":
            if fault is not None:
                raise fault
            return dict((m, {"info.name": n}) for m, n in names.items())
        return labels if objects else {}

    utils = os.path.join(os.path.dirname(__file__), "..", "plugins", "module_utils")
    params = dict(datacenter="", window_minutes=30, event_types=wt.EVENT_TYPES, usage_alarms=["CPU usage", "memory usage"])
    with patch.object(ansible.module_utils, "__path__", [*ansible.module_utils.__path__, utils]), \
            patch.object(wt, "vim", create=True), patch.object(wt, "vmodl", NS(MethodFault=Fault), create=True), \
            patch.object(wt, "_props", return_value={}), patch.object(wt, "_objs_props", side_effect=props), \
            patch.object(wt, "_events", Mock(side_effect=[[], list(events)])):
        return wt._read(NS(rootFolder=NS(triggeredAlarmState=list(triggered))), params, END, VC), wt


class Fault(Exception):
    """vmodl.MethodFault, as pyVmomi raises it."""


def triggered(moid, color="red", at=2400, alarm="alarm-1"):
    from datetime import datetime, timezone
    from types import SimpleNamespace as NS
    return NS(entity=NS(_moId=moid, _wsdlName="VirtualMachine"), alarm=NS(_moId=alarm), overallStatus=color,
              time=datetime.fromtimestamp(at, timezone.utc))


class CodexReview(unittest.TestCase):
    """1.131.1: the Codex review of the usage tuning (#12-#17)."""

    def recovered(self, result, wt):
        """The last email said the red CPU group; this run's read -> does the email say it recovered?"""
        before = w.vm_watch_conditions(watch(states=[state("example-vm", "red", 2400)]))
        opts = dict(now=END, report_on=["critical"], min_minutes=0)
        runs = [dict(artifacts=dict(vm_watch_email_state=mail.vm_watch_email_floor(before, [], opts)["state"]))]
        d = wt.mark_incomplete(dict(result, vcenters=[{"name": VC, "ok": True, "error": ""}], suspects=[], vms_unread=[],
                                    read_at=END, window_minutes=30))
        unread = any(st.get("incomplete") for st in d["vcenters"])      # watch_email.yml's unread
        cs = w.vm_watch_conditions(d)
        decision = mail.vm_watch_email_floor(cs, runs, dict(opts, now=END + 600, unread=unread))
        return d, cs, decision

    def test_12_failed_alarm_read_is_no_recovery(self):
        result, wt = read([triggered("vm-1")], {"alarm-1": "Virtual machine CPU usage"}, fault=Fault("NoPermission"))
        d, cs, decision = self.recovered(result, wt)
        self.assertIn("usage alarms", d["vcenters"][0].get("incomplete", ""))
        self.assertNotEqual(decision["kind"], "recovered")
        self.assertFalse(decision["changed"])
        self.assertEqual(w.vm_watch_health(d, cs)[VC]["checks_failed"], ["vm_watch"])      # its tickets: none cleared

    def test_13_a_record_without_its_object_keeps_the_rest(self):
        from types import SimpleNamespace as NS
        broken = [NS(entity=None, alarm=NS(_moId="alarm-1"), overallStatus="red", time=None),
                  NS(entity=NS(_moId="vm-2", _wsdlName="VirtualMachine"), alarm=None, overallStatus="red", time=None),
                  NS(entity=None, alarm=None, overallStatus="gray", time=None)]          # not counted: no usage alarm is gray
        result, wt = read(broken + [triggered("vm-1")], {"alarm-1": "Virtual machine CPU usage"})
        self.assertEqual([a["entity_moid"] for a in result["alarms"]], ["vm-1"])
        self.assertIn("2 triggered alarm(s)", str(result["warnings"]))

    def test_13_an_unexpected_error_marks_only_the_alarms_unread(self):
        result, wt = read([triggered("vm-1")], {"alarm-1": "Virtual machine CPU usage"}, fault=KeyError("info.name"))
        self.assertEqual(result["alarms"], [])
        self.assertEqual([u["vcenter"] for u in result["alarms_unread"]], [VC])
        d, _, decision = self.recovered(result, wt)
        self.assertTrue(d["vcenters"][0].get("incomplete"))
        self.assertNotEqual(decision["kind"], "recovered")

    def test_14_whole_alarm_names_only(self):
        other = "CPU usage telemetry unavailable"
        ev = change("example-vm", "green", "red", 3000, alarm=other)
        ordinary, groups = w._usage.usage_window([ev], [state("example-vm", "red", 1800, alarm=other)], END, 1800, END,
                                                 dict(usage_alarms=["CPU usage"]))
        self.assertEqual((ordinary, groups), ([ev], []))
        result, _ = read([triggered("vm-1"), triggered("vm-2", alarm="alarm-2")],
                         {"alarm-1": "Virtual machine CPU usage", "alarm-2": other})
        self.assertEqual([a["alarm"] for a in result["alarms"]], ["Virtual machine CPU usage"])
        for name in ("Host CPU usage", "virtual  machine MEMORY usage"):
            self.assertTrue(w._usage.usage_window([], [state("example-vm", "red", 1800, alarm=name)], END, 1800, END,
                                                  dict(usage_alarms=["CPU usage", "memory usage"]))[1], name)

    def test_15_a_short_window_measures_red_from_its_trigger(self):
        d = data(alarms=[state("example-vm", "red", END - 40 * 60)], read_at=END, window_minutes=5)
        self.assertEqual([c["summary"] for c in w.vm_watch_conditions(d)], ["1 VM: CPU usage red 40+ min (example-vm)"])
        # a red interval that ended is still clipped to the window
        d = data(events=[change("example-vm", "green", "red", END - 40 * 60), change("example-vm", "red", "green", END - 60)],
                 read_at=END, window_minutes=5)
        self.assertEqual(w.vm_watch_conditions(d), [])

    def test_16_no_object_id_never_pairs_by_name(self):
        def anon(row):
            return dict((k, v) for k, v in row.items() if k != "entity_moid")
        d = watch([anon(change("example-vm", "green", "red", 1900)), anon(change("example-vm", "red", "green", 3300))])
        self.assertEqual(w.vm_watch_conditions(d), [])
        # two same-named VMs without ids: two members, never one 20-minute VM
        d = watch(states=[anon(state("example-vm", "red", 2400)), anon(state("example-vm", "red", 2400))])
        self.assertEqual([c["summary"] for c in w.vm_watch_conditions(d)], ["2 VMs: CPU usage red 20+ min (example-vm, example-vm)"])

    def test_17_a_vm_joining_a_group_is_emailed(self):
        one = w.vm_watch_conditions(watch(states=[state("example-a", "red", 2400)]))
        two = w.vm_watch_conditions(watch(states=[state("example-a", "red", 2400), state("example-b", "red", 2400)]))
        self.assertEqual([c["id"] for c in one], [c["id"] for c in two])
        opts = dict(now=END, report_on=["critical"], min_minutes=30)
        first = mail.vm_watch_email_floor(one, [], opts)
        self.assertTrue(first["send"])
        runs = [dict(artifacts=dict(vm_watch_email_state=first["state"]))]
        held = mail.vm_watch_email_floor(two, runs, dict(opts, now=END + 300))
        self.assertTrue(held["held"] and not held["send"])
        runs = [dict(artifacts=dict(vm_watch_email_state=held["state"]))]
        later = mail.vm_watch_email_floor(two, runs, dict(opts, now=END + 2400))
        self.assertTrue(later["send"])
        self.assertEqual(later["kind"], "change")
        self.assertIn("Changed: usage:vm:cpu usage:red in %s (critical, 1 joined, 0 left)" % VC, later["changed"])
        runs = [dict(artifacts=dict(vm_watch_email_state=later["state"]))]
        self.assertFalse(mail.vm_watch_email_floor(two, runs, dict(opts, now=END + 4800))["send"])     # no change: quiet


if __name__ == "__main__":
    unittest.main()
