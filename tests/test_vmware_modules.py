#!/usr/bin/env python3
"""Unit tests for the pure parts of roles/vmware_vm/library (needs ansible-core importable, not
pyVmomi or a vCenter): python3 tests/test_vmware_modules.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "roles", "vmware_vm", "library"))
import site_vmware_snapshots as m  # noqa: E402
import site_vmware_alarms as al  # noqa: E402
import site_vmware_esxi_security as es  # noqa: E402

G = 1024 ** 3


class SnapshotSizes(unittest.TestCase):
    # files: 1 base disk, 2 = delta after s1, 3 = delta after s2 (running state), 10/11 state files, 12 memory of s2
    FILES = {1: 100 * G, 2: 3 * G, 3: 5 * G, 10: 1024, 11: 2048, 12: 4 * G}

    def test_chain_of_two(self):
        layouts = {"s1": {"data": 10, "memory": -1, "disks": {2000: [[1]]}},
                   "s2": {"data": 11, "memory": 12, "disks": {2000: [[1], [2]]}}}
        running = {2000: [[1], [2], [3]]}
        sizes = m.snapshot_sizes(self.FILES, layouts, running, {None: ["s1"], "s1": ["s2"], "s2": []}, "s2")
        self.assertEqual(sizes["s1"], 1024 + 3 * G)          # its state file + the delta written after it (until s2)
        self.assertEqual(sizes["s2"], 2048 + 4 * G + 5 * G)  # state + memory + the delta of the running state

    def test_reverted_branch(self):
        # running on s1 again (reverted), s2 is a leaf off the path: it keeps only its own files
        layouts = {"s1": {"data": 10, "memory": -1, "disks": {2000: [[1]]}},
                   "s2": {"data": 11, "memory": -1, "disks": {2000: [[1], [2]]}}}
        running = {2000: [[1], [3]]}
        sizes = m.snapshot_sizes(self.FILES, layouts, running, {None: ["s1"], "s1": ["s2"], "s2": []}, "s1")
        self.assertEqual(sizes["s1"], 1024 + 5 * G)
        self.assertEqual(sizes["s2"], 2048)

    def test_two_disks_and_missing_files(self):
        layouts = {"s1": {"data": 99, "memory": None, "disks": {2000: [[1]], 2001: [[1]]}}}
        running = {2000: [[1], [2]], 2001: [[1], [3, 2]]}
        sizes = m.snapshot_sizes(self.FILES, layouts, running, {None: ["s1"], "s1": []}, "s1")
        self.assertEqual(sizes["s1"], 3 * G + 5 * G + 3 * G)   # unknown state file key counts 0

    def test_no_layout(self):
        self.assertEqual(m.snapshot_sizes({}, {}, {}, {}, None), {})


class SnapshotCreators(unittest.TestCase):
    def test_matching(self):
        snaps = [("s1", 10_000), ("s2", 10_030), ("s3", 50_000), ("s4", 90_000)]
        events = [(9_990, "CORP\\alice"), (10_025, "svc_aap"), (49_000, "CORP\\bob"), (95_000, "late")]
        self.assertEqual(m.snapshot_creators(snaps, events),
                         {"s1": "CORP\\alice", "s2": "svc_aap", "s3": "CORP\\bob", "s4": ""})

    def test_each_event_once_and_no_events(self):
        self.assertEqual(m.snapshot_creators([("a", 100), ("b", 101)], [(99, "u")]), {"a": "u", "b": ""})
        self.assertEqual(m.snapshot_creators([("a", 100)], []), {"a": ""})


class AlarmEvents(unittest.TestCase):
    def test_category(self):
        self.assertEqual(al.category_of("X", "warning"), "warning")
        self.assertEqual(al.category_of("com.vmware.x", "", "error"), "error")       # an extended event's severity
        self.assertEqual(al.category_of("GeneralHostErrorEvent"), "error")            # vCenter did not say: from the name
        self.assertEqual(al.category_of("GeneralHostWarningEvent"), "warning")
        self.assertEqual(al.category_of("HostConnectionLostEvent"), "error")
        self.assertEqual(al.category_of("VmPoweredOnEvent"), "info")

    def test_classify(self):
        self.assertEqual(al.classify("BadUsernameSessionEvent", "info"), ("login", "warning"))
        self.assertEqual(al.classify("HostNotRespondingEvent", "error"), ("connection", "critical"))
        self.assertEqual(al.classify("HostShutdownEvent", "info"), ("connection", "info"))
        self.assertEqual(al.classify("VmGuestShutdownEvent", "info", "CORP\\admin"), ("vm", "info"))
        self.assertEqual(al.classify("VmPoweredOffEvent", "info", ""), ("vm", "warning"))      # nobody asked
        self.assertEqual(al.classify("VmPoweredOffEvent", "info", "CORP\\admin"), ("vm", "info"))
        self.assertEqual(al.classify("VmFailoverFailed", "error"), ("vm", "critical"))
        self.assertEqual(al.classify("com.vmware.vc.ha.VmRestartedByHAEvent", "warning"), ("vm", "warning"))
        self.assertEqual(al.classify("SomethingErrorEvent", "error"), ("other", "critical"))
        self.assertEqual(al.classify("SomethingEvent", "warning"), ("other", "warning"))
        self.assertEqual(al.classify("VmPoweredOnEvent", "info"), (None, None))                # not wanted
        self.assertEqual(al.classify("BadUsernameSessionEvent", "info", login_types=[]), (None, None))   # list emptied

    def test_login_parts(self):
        self.assertEqual(al.login_parts("Cannot login svc_scan@10.1.2.3"), ("svc_scan", "10.1.2.3"))
        self.assertEqual(al.login_parts("", "u1", "10.0.0.1"), ("u1", "10.0.0.1"))
        self.assertEqual(al.login_parts("Login failed", args={"userName": "a@vsphere.local", "client": "10.0.0.9"}),
                         ("a@vsphere.local", "10.0.0.9"))
        self.assertEqual(al.login_parts("no address here"), ("", ""))

    def test_event_record(self):
        r = al.event_record({"key": 1, "type": "BadUsernameSessionEvent", "message": "", "login_user": "svc", "ip": "10.9.9.9",
                             "host": "esx01", "time": "2026-10-05T03:52:47Z"})
        self.assertEqual((r["group"], r["severity"], r["message"], r["login_user"], r["ip"]),
                         ("login", "warning", "Cannot login svc@10.9.9.9", "svc", "10.9.9.9"))
        r = al.event_record({"key": 2, "type": "HostConnectionLostEvent", "host": "esx02", "message": None})
        self.assertEqual((r["group"], r["severity"], r["message"]), ("connection", "critical", "Host esx02 lost its connection to vCenter"))
        self.assertIsNone(al.event_record({"key": 3, "type": "VmPoweredOnEvent", "category": "info"}))
        r = al.event_record({"key": 4, "type": "esx.problem.storage.connectivity.lost", "severity": "error", "message": "Lost  connectivity\n to x"})
        self.assertEqual((r["group"], r["severity"], r["message"]), ("other", "critical", "Lost connectivity to x"))

    def test_alarm_records(self):
        names = {"host-1": {"name": "esx01", "cluster": "CL1", "datacenter": "DC1"}, "group-d1": {"name": "vc", "cluster": "", "datacenter": ""}}
        defs = {"alarm-1": {"name": "Host connection and power state", "description": "d"}, "alarm-2": {"name": "vSphere Health", "description": ""}}
        states = [{"key": "k1", "kind": "host", "moid": "host-1", "alarm": "alarm-1", "status": "yellow", "time": "2026-10-05T01:00:00Z"},
                  {"key": "k1", "kind": "host", "moid": "host-1", "alarm": "alarm-1", "status": "yellow", "time": "2026-10-05T01:00:00Z"},
                  {"key": "k2", "kind": "vcenter", "moid": "group-d1", "alarm": "alarm-2", "status": "red", "time": "2026-10-04T01:00:00Z",
                   "acknowledged": True, "acknowledged_by": "CORP\\op"},
                  {"key": "k3", "kind": "vm", "moid": "vm-9", "alarm": "alarm-1", "status": "red", "time": "2026-10-05T02:00:00Z"},
                  {"key": "k4", "kind": "host", "moid": "host-1", "alarm": "alarm-1", "status": "green", "time": ""}]
        out = al.alarm_records(states, names, defs, ["vcenter", "datacenter", "cluster", "host"])
        self.assertEqual([(a["entity"], a["severity"], a["alarm"]) for a in out],       # deduplicated, no VM, no green, red first
                         [("vc", "critical", "vSphere Health"), ("esx01", "warning", "Host connection and power state")])
        self.assertEqual((out[1]["cluster"], out[1]["datacenter"], out[0]["acknowledged"], out[0]["acknowledged_by"]),
                         ("CL1", "DC1", True, "CORP\\op"))
        self.assertEqual(len(al.alarm_records(states, names, defs, ["vm"])), 1)


class AlarmDefaultsMatchModule(unittest.TestCase):
    def test_event_type_lists(self):
        import yaml
        with open(os.path.join(os.path.dirname(__file__), "..", "roles", "vmware_vm", "defaults", "main.yml")) as fh:
            d = yaml.safe_load(fh)
        self.assertEqual(d["vm_alarm_login_event_types"], al.LOGIN_EVENTS)
        self.assertEqual(d["vm_alarm_connection_event_types"], al.CONNECTION_EVENTS)
        self.assertEqual(d["vm_alarm_vm_event_types"], al.VM_EVENTS)


class FakeHost(object):
    """An ESXi host for secure_host(): its state, and every call that would change it."""
    MUTATING = ("stop_service", "start_service", "set_policy", "set_option", "set_lockdown", "set_exceptions")

    def __init__(self, ssh=(True, "on"), opts=None, lockdown="disabled", exceptions=None, fail=None):
        self.svc = {"TSM-SSH": ssh, "TSM": (False, "off")}
        self.opts = dict(opts if opts is not None else {"UserVars.ESXiShellTimeOut": 0, "UserVars.ESXiShellInteractiveTimeOut": 0})
        self.mode, self.exc, self.calls, self.fail = lockdown, list(exceptions or []), [], fail or {}

    def _call(self, name, *a):
        self.calls.append((name,) + a)
        if name in self.fail:
            raise self.fail[name]

    def services(self):
        return dict(self.svc)

    def stop_service(self, k):
        self._call("stop_service", k)
        self.svc[k] = (False, self.svc[k][1])

    def start_service(self, k):
        self._call("start_service", k)
        self.svc[k] = (True, self.svc[k][1])

    def set_policy(self, k, p):
        self._call("set_policy", k, p)
        self.svc[k] = (self.svc[k][0], p)

    def option(self, k):
        if k not in self.opts:
            raise KeyError(k)
        return self.opts[k]

    def set_option(self, k, v):
        self._call("set_option", k, v)
        self.opts[k] = int(v)

    def lockdown(self):
        return self.mode

    def set_lockdown(self, m):
        self._call("set_lockdown", m)
        self.mode = m

    def exceptions(self):
        return list(self.exc)

    def set_exceptions(self, users):
        self._call("set_exceptions", list(users))
        self.exc = list(users)


class NoPermission(Exception):
    privilegeId = "Host.Config.Settings"


WANT = {"services": {"TSM-SSH": "disabled"}, "labels": {"TSM-SSH": "SSH", "TSM": "ESXi Shell"},
        "settings": {"UserVars.ESXiShellTimeOut": 600, "UserVars.ESXiShellInteractiveTimeOut": "600"},
        "lockdown": "normal", "exception_users": ["svc_scan", "svc_mon"]}


class EsxiSecurity(unittest.TestCase):
    def test_dry_run_changes_nothing(self):
        h = FakeHost(exceptions=["svc_mon"])
        r = es.secure_host(h, WANT, check_mode=True)
        self.assertEqual(h.calls, [])                                    # not one change
        self.assertEqual(r["changes"], ["SSH stopped and set to start manually", "UserVars.ESXiShellInteractiveTimeOut 0 -> 600",
                                        "UserVars.ESXiShellTimeOut 0 -> 600", "lockdown exception users added: svc_scan",
                                        "lockdown disabled -> normal"])
        self.assertEqual(r["before"]["SSH"], "running, starts with the host")

    def test_apply_in_order_then_nothing_to_do(self):
        h = FakeHost(exceptions=["SVC_MON"])
        r = es.secure_host(h, WANT)
        self.assertEqual(r["errors"], [])
        names = [c[0] for c in h.calls]
        self.assertEqual(names[:2], ["stop_service", "set_policy"])
        self.assertLess(names.index("set_exceptions"), names.index("set_lockdown"))        # exceptions BEFORE lockdown
        self.assertIn(("set_exceptions", ["SVC_MON", "svc_scan"]), h.calls)                  # added, never removed (any case)
        self.assertEqual((h.svc["TSM-SSH"], h.mode, h.opts["UserVars.ESXiShellTimeOut"]), ((False, "off"), "normal", 600))
        h.calls = []
        again = es.secure_host(h, WANT)
        self.assertEqual((h.calls, again["changes"], again["errors"]), ([], [], []))         # a second run: nothing

    def test_strict_is_left_alone(self):
        h = FakeHost(ssh=(False, "off"), opts={"UserVars.ESXiShellTimeOut": 600, "UserVars.ESXiShellInteractiveTimeOut": 600},
                     lockdown="strict", exceptions=["svc_scan", "svc_mon"])
        r = es.secure_host(h, WANT)
        self.assertEqual((h.calls, r["changes"]), ([], []))
        self.assertEqual(r["notes"], ["lockdown is strict: left as it is (stricter than normal)"])

    def test_missing_privilege_is_named_and_the_rest_still_runs(self):
        h = FakeHost(fail={"set_option": NoPermission()})
        r = es.secure_host(h, dict(WANT, exception_users=[]))
        self.assertEqual(r["errors"][0], "UserVars.ESXiShellInteractiveTimeOut: the vCenter account lacks the privilege "
                                         "Host.Config.Settings on this host")
        self.assertEqual(h.mode, "normal")                                                 # lockdown still set
        self.assertEqual(h.svc["TSM-SSH"], (False, "off"))

    def test_unknown_setting_and_failed_exceptions(self):
        h = FakeHost(fail={"set_exceptions": RuntimeError("boom")})
        r = es.secure_host(h, dict(WANT, settings={"UserVars.NoSuch": 1}))
        self.assertIn("UserVars.NoSuch: the host has no such setting", r["errors"])
        self.assertTrue(any(e.startswith("lockdown exception users: RuntimeError: boom - lockdown left disabled") for e in r["errors"]))
        self.assertEqual(h.mode, "disabled")                       # not locked: the exception users could not be added first

    def test_enable_and_disable_lockdown(self):
        h = FakeHost(ssh=(False, "off"), lockdown="normal")
        r = es.secure_host(h, {"services": {"TSM-SSH": "enabled"}, "labels": {"TSM-SSH": "SSH"}, "lockdown": "disabled"})
        self.assertEqual([c[0] for c in h.calls], ["set_policy", "start_service", "set_lockdown"])
        self.assertEqual(r["changes"], ["SSH started and set to start with the host", "lockdown normal -> disabled"])

    def test_ssh_and_shell_both_off(self):
        h = FakeHost()
        h.svc["TSM"] = (True, "on")
        r = es.secure_host(h, {"services": {"TSM-SSH": "disabled", "TSM": "disabled"}, "labels": {"TSM-SSH": "SSH", "TSM": "ESXi Shell"}})
        self.assertEqual((h.svc["TSM-SSH"], h.svc["TSM"]), ((False, "off"), (False, "off")))
        self.assertEqual(sorted(r["changes"]), ["ESXi Shell stopped and set to start manually", "SSH stopped and set to start manually"])
        self.assertEqual(r["before"]["ESXi Shell"], "running, starts with the host")

    def test_same_value_and_faults(self):
        self.assertTrue(es.same_value(600, "600"))
        self.assertFalse(es.same_value(0, 600))
        self.assertTrue(es.same_value(True, "true"))
        self.assertFalse(es.same_value(600, "ten"))
        self.assertTrue(es.same_value("info", "info"))
        self.assertEqual(es.fault_text(RuntimeError("x  y")), "RuntimeError: x y")

    def test_pick_hosts(self):
        hs = [{"name": "esx01.corp.mil", "cluster": "PROD", "datacenter": "DC1"}, {"name": "esx02.corp.mil", "cluster": "LAB", "datacenter": "DC1"},
              {"name": "esx03.corp.mil", "cluster": "PROD", "datacenter": "DC2"}]
        self.assertEqual([h["name"] for h in es.pick_hosts(hs)[0]], ["esx01.corp.mil", "esx02.corp.mil", "esx03.corp.mil"])
        self.assertEqual([h["name"] for h in es.pick_hosts(hs, names=["ESX0[12]"])[0]], ["esx01.corp.mil", "esx02.corp.mil"])
        self.assertEqual([h["name"] for h in es.pick_hosts(hs, clusters=["prod"], datacenter="DC1")[0]], ["esx01.corp.mil"])
        picked, excluded = es.pick_hosts(hs, exclude=["esx02"])
        self.assertEqual(([h["name"] for h in picked], [h["name"] for h in excluded]),
                         (["esx01.corp.mil", "esx03.corp.mil"], ["esx02.corp.mil"]))


if __name__ == "__main__":
    unittest.main()
