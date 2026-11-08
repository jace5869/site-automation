#!/usr/bin/env python3
"""The disk space report's tables (plugins/filter/disk_report_filters.py): which rows, their colours, the order, the
volume groups only with a full filesystem or thin pool, the old tmp files.

    python tests/test_disk_report.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
import disk_report_filters as F  # noqa: E402

G = 2 ** 30


def fs(mount, pct, ipct=1, size=10 * G):
    return {"mount": mount, "source": "/dev/mapper/rhel-" + mount.strip("/").replace("/", "_") or "root", "type": "xfs",
            "size": size, "used": size * pct // 100, "avail": size - size * pct // 100, "pct": pct, "ipct": ipct}


def host(name, filesystems, pools=(), vgs=(), tmp=()):
    return {"inventory_hostname": name, "check_disk_inventory": {"filesystems": list(filesystems),
            "lvm": {"pools": list(pools), "vgs": list(vgs)}}, "disk_tmp_plan": list(tmp)}


VG = {"vg": "rhel", "size": 32 * G, "free": 4 * G, "free_pct": 12.5}


class Sections(unittest.TestCase):
    def secs(self, hosts, show_all=False):
        return {s["title"]: s for s in F.disk_report_sections(hosts, 85, 90, show_all)}

    def test_only_the_full_ones_fullest_first_coloured(self):
        s = self.secs([host("b", [fs("/", 86), fs("/boot", 20)]), host("a", [fs("/var", 95), fs("/home", 89, ipct=91)])])
        t = s["Filesystems at or above 85%"]
        self.assertEqual([(r[0], r[1]) for r in t["rows"]], [("a", "/var"), ("a", "/home"), ("b", "/")])
        self.assertEqual(t["row_status"], ["critical", "critical", "warning"])   # /home: its inodes are at 91%
        self.assertEqual(t["cell_status"][1][4], "warning")
        self.assertEqual(t["cell_status"][1][8], "critical")
        self.assertEqual(t["status"], "critical")

    def test_nothing_full(self):
        s = self.secs([host("a", [fs("/", 40)], vgs=[VG])])
        self.assertEqual(s["Filesystems at or above 85%"]["rows"], [])
        self.assertIn("No filesystem is 85% full", s["Filesystems at or above 85%"]["text"])
        self.assertEqual(sorted(s), ["Filesystems at or above 85%"])        # no volume groups without a full one

    def test_every_one_with_show_all(self):
        s = self.secs([host("a", [fs("/", 40), fs("/var", 10)], vgs=[VG])], show_all=True)
        self.assertEqual(len(s["Every filesystem"]["rows"]), 2)
        self.assertEqual(s["Volume groups"]["rows"], [["a", "rhel", "32.0G", "4.0G", "12.5%"]])

    def test_thin_pool_and_lost_snapshot(self):
        pools = [{"vg": "rhel", "lv": "pool", "kind": "thin pool", "invalid": False, "size": 16 * G, "data": 70.0, "meta": 92.5, "origin": ""},
                 {"vg": "rhel", "lv": "snap", "kind": "snapshot", "invalid": True, "size": 2 * G, "data": 100.0, "meta": -1, "origin": "root"},
                 {"vg": "rhel", "lv": "ok", "kind": "thin pool", "invalid": False, "size": G, "data": 10.0, "meta": 5.0, "origin": ""}]
        s = self.secs([host("a", [fs("/", 40)], pools=pools, vgs=[VG])])
        t = s["LVM thin pools and snapshots"]
        self.assertEqual([r[1] for r in t["rows"]], ["rhel/snap", "rhel/pool"])
        self.assertEqual(t["rows"][0][6], "no longer valid (filled up)")
        self.assertEqual(t["cell_status"][1][5], "critical")                 # the metadata
        self.assertIn("Volume groups of those hosts", s)                     # a full thin pool brings its volume groups

    def test_old_tmp_files(self):
        tmp = [{"path": "/tmp", "mount": "/", "days": 10, "files": 120, "bytes": int(1.2 * G), "big": [[G // 2, "/tmp/x.iso"]]}]
        s = self.secs([host("a", [fs("/", 93)], tmp=tmp)])
        self.assertEqual(s["Filesystems at or above 85%"]["rows"][0][9], "1.2G in /tmp (120 files)")
        self.assertEqual(s["Old files in the tmp folders of a full filesystem"]["rows"],
                         [["a", "/tmp", "/", "10 days", 120, "1.2G", "512.0M /tmp/x.iso"]])

    def test_sizes(self):
        self.assertEqual([F._disk_bytes(x) for x in (0, 1023, 1536, 5 * 2 ** 40)], ["0B", "1023B", "1.5K", "5.0T"])


if __name__ == "__main__":
    unittest.main()
