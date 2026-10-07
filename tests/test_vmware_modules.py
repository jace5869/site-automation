#!/usr/bin/env python3
"""Unit tests for the pure parts of roles/vmware_vm/library (needs ansible-core importable, not
pyVmomi or a vCenter): python3 tests/test_vmware_modules.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "roles", "vmware_vm", "library"))
import site_vmware_snapshots as m  # noqa: E402
import site_vmware_alarms as al  # noqa: E402

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
        d = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "..", "roles", "vmware_vm", "defaults", "main.yml")))
        self.assertEqual(d["vm_alarm_login_event_types"], al.LOGIN_EVENTS)
        self.assertEqual(d["vm_alarm_connection_event_types"], al.CONNECTION_EVENTS)
        self.assertEqual(d["vm_alarm_vm_event_types"], al.VM_EVENTS)


if __name__ == "__main__":
    unittest.main()
