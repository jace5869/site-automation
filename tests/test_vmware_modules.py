#!/usr/bin/env python3
"""Unit tests for the pure parts of roles/vmware_vm/library (needs ansible-core importable, not
pyVmomi or a vCenter): python3 tests/test_vmware_modules.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "roles", "vmware_vm", "library"))
import site_vmware_snapshots as m  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
