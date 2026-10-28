#!/usr/bin/env python3
"""The VM right-sizing report: the pure parts of roles/vmware_vm/library/site_vmware_rightsize.py (vCenter's samples
summed up) and the suggestions and report (plugins/filter/vmware_rightsize_filters.py). Needs ansible-core importable, not
pyVmomi.
    python tests/test_vm_rightsize.py"""
import datetime
import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "roles", "vmware_vm", "library"))
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
_spec = importlib.util.spec_from_file_location("ansible.module_utils.site_vcenters",
                                               os.path.join(REPO, "roles", "vmware_vm", "module_utils", "site_vcenters.py"))
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ansible.module_utils.site_vcenters"] = _mod
_spec.loader.exec_module(_mod)

import site_vmware_rightsize as rs  # noqa: E402
import vmware_rightsize_filters as f  # noqa: E402


def vm(name, cpus=4, mem_gb=16, cpu=20.0, mem=20.0, days=30.0, win=False, ready=0.2, balloon=None, swapped=None, limit=-1,
       year_cpu=None, year_mem=None, disks=None, hot=False, vc="vc01.example.mil", cluster="CL1"):
    return {"name": name, "vcenter": vc, "datacenter": "DC1", "cluster": cluster, "esxi_host": "esx01.example.mil",
            "guest_os": "Microsoft Windows Server 2022 (64-bit)" if win else "Red Hat Enterprise Linux 9 (64-bit)", "windows": win,
            "cpus": cpus, "memory_mb": int(mem_gb * 1024), "cpu_limit_mhz": -1, "mem_limit_mb": limit, "cpu_hot_add": hot,
            "mem_hot_add": hot, "disks": disks or [],
            "stats": {"interval": 7200, "samples": int(days * 12), "days": days, "cpu_avg": cpu / 2, "cpu_p95": cpu, "cpu_max": cpu * 1.5,
                      "ready_avg": ready / 2, "ready_p95": ready, "mem_avg": mem / 2, "mem_p95": mem, "mem_max": mem * 1.2,
                      "consumed_mb_avg": mem_gb * 900, "balloon_mb_max": balloon, "balloon_mb_avg": None, "swapped_mb_max": swapped,
                      "year_cpu_day": year_cpu, "year_cpu_date": "2026-03-31" if year_cpu is not None else "",
                      "year_mem_day": year_mem, "year_mem_date": "2026-03-31" if year_mem is not None else ""}}


def size(v, **opts):
    return f.rightsize(v, opts)


class Module(unittest.TestCase):
    def test_percentile(self):
        self.assertEqual(rs.percentile(list(range(1, 101)), 95), 95)
        self.assertEqual(rs.percentile([5], 95), 5)
        self.assertIsNone(rs.percentile([], 95))

    def test_summarize_vcenter_units(self):
        # cpu.usage and mem.usage in hundredths of a percent; -1 = no sample; cpu.ready in ms per 2-hour sample, all vCPUs
        s = rs.summarize({"cpu": [1000, 2000, -1, 3000], "mem": [500, 700, 600], "ready": [144000, 0],
                          "consumed": [1048576], "balloon": [0, 204800]}, 7200, 2)
        self.assertEqual((s["cpu_avg"], s["cpu_p95"], s["cpu_max"]), (20.0, 30.0, 30.0))
        self.assertEqual((s["mem_p95"], s["mem_max"]), (7.0, 7.0))
        self.assertEqual(s["ready_p95"], 1.0)                   # 144 s of 7200 s = 2%, over 2 vCPUs = 1% each
        self.assertEqual((s["consumed_mb_avg"], s["balloon_mb_max"]), (1024, 200))
        self.assertIsNone(s["swapped_mb_max"])                  # level 2 only: not there
        self.assertEqual((s["samples"], s["days"]), (3, 0.2))

    def test_busiest_day(self):
        t = [datetime.datetime(2026, 3, d, tzinfo=datetime.timezone.utc) for d in (30, 31)]
        self.assertEqual(rs.busiest_day([4000, 7850], t), (78.5, "2026-03-31"))
        self.assertEqual(rs.busiest_day([], []), (None, ""))


class Suggestions(unittest.TestCase):
    def test_too_big_cpu_and_memory(self):
        s = size(vm("web01", cpus=8, mem_gb=32, cpu=10.0, mem=10.0))
        self.assertEqual(s["verdict"], f.TOO_BIG)
        self.assertEqual(s["cpu"], {"now": 8, "suggested": 4, "change": "down"})     # 0.8 used -> 2, but at most half at once
        self.assertEqual(s["mem"], {"now": 32, "suggested": 16, "change": "down"})   # 3.2 GB active x2, at most half at once
        self.assertEqual((s["freed_vcpus"], s["freed_gb"]), (4, 16))
        self.assertIn("at most 50% fewer at once", s["why"][0])

    def test_cpu_sized_to_target_and_even(self):
        s = size(vm("app01", cpus=16, cpu=40.0))             # 6.4 used / 70% = 9.1 -> 10
        self.assertEqual(s["cpu"]["suggested"], 10)
        self.assertEqual(size(vm("app02", cpus=8, cpu=20.0))["cpu"]["suggested"], 4)   # 1.6 / 0.7 = 2.3 -> 3 -> even 4

    def test_floors(self):
        self.assertEqual(size(vm("tiny", cpus=4, cpu=1.0))["cpu"]["suggested"], 2)            # min_vcpus 2
        self.assertEqual(size(vm("tiny", cpus=4, cpu=1.0), min_vcpus=1, cpu_max_cut_pct=100)["cpu"]["suggested"], 1)
        w = size(vm("win", cpus=2, mem_gb=8, cpu=1.0, mem=5.0, win=True))
        self.assertEqual(w["mem"]["change"], "down")
        self.assertEqual(w["mem"]["suggested"], 4)                                            # Windows at least 4 GB
        self.assertEqual(w["cpu"]["change"], "")                                              # Windows at least 2

    def test_too_small(self):
        s = size(vm("busy", cpus=8, cpu=90.0, ready=1.0))
        self.assertEqual(s["verdict"], f.TOO_SMALL)
        self.assertEqual(s["cpu"], {"now": 8, "suggested": 12, "change": "up"})     # 7.2 / 0.7 = 10.3 -> 11 -> 12
        self.assertIn("Adding vCPUs needs the VM shut down", " ".join(s["notes"]))
        m = size(vm("full", mem_gb=24, mem=95.0, hot=True))
        self.assertEqual(m["mem"], {"now": 24, "suggested": 32, "change": "up"})    # x1.25 = 30 -> 32
        self.assertEqual(m["notes"], [])                                            # hot add on: nothing to say

    def test_mixed_is_too_small(self):
        s = size(vm("java", cpus=4, cpu=10.0, mem=95.0))
        self.assertEqual((s["verdict"], s["cpu"]["change"], s["mem"]["change"]), (f.TOO_SMALL, "down", "up"))

    def test_small_memory_change_not_suggested(self):
        self.assertEqual(size(vm("m", mem_gb=8, mem=40.0))["mem"]["change"], "")        # 3.2 x2 = 6.4 -> 8: no change
        self.assertEqual(size(vm("m", mem_gb=4, mem=10.0))["mem"]["suggested"], 2)     # 4 -> 2: half, 2 GB, the floor
        self.assertEqual(size(vm("m", mem_gb=3, mem=10.0))["mem"]["change"], "")        # 3 -> 2: less than 2 GB
        self.assertEqual(size(vm("m", mem_gb=16, mem=30.0))["mem"]["suggested"], 10)    # 4.8 x2 = 9.6 -> 10: 37% less
        self.assertEqual(size(vm("m", mem_gb=16, mem=40.0))["mem"]["change"], "")       # 12.8 -> 14: only 12% less

    def test_busiest_day_of_the_year_keeps_the_size(self):
        s = size(vm("batch", cpus=8, cpu=18.0, mem=50.0, year_cpu=78.0))
        self.assertEqual(s["verdict"], f.RIGHT)
        self.assertIn("busiest day of the year (2026-03-31, 78% CPU) is sized for", " ".join(s["notes"]))

    def test_not_enough_history(self):
        s = size(vm("new", days=3.0, cpu=1.0))
        self.assertEqual(s["verdict"], f.UNKNOWN)
        self.assertEqual(s["cpu"]["change"], "")
        self.assertIn("3 day(s)", s["why"][0])
        self.assertEqual(size(vm("new", days=3.0), min_days=2)["verdict"], f.TOO_BIG)

    def test_memory_taken_back(self):
        self.assertFalse(size(vm("a", balloon=20))["taken"])                         # a few MB: not worth saying
        s = size(vm("b", balloon=2048))
        self.assertTrue(s["taken"])
        self.assertIn("2048 MB ballooned", s["notes"][0])
        s = size(vm("c", mem_gb=32, limit=16384))
        self.assertIn("a memory limit of 16 GB is set", s["notes"][0])

    def test_ready(self):
        s = size(vm("r", cpus=12, cpu=5.0, ready=9.2))
        self.assertEqual(s["verdict"], f.TOO_BIG)
        self.assertIn("fewer vCPUs usually wait less", " ".join(s["notes"]))

    def test_peaks_above_the_suggestion(self):
        s = size(vm("burst", cpus=8, cpu=10.0, mem=60.0))                         # peak 15% of 8 = 1.2 vCPUs: under 4
        self.assertFalse([n for n in s["notes"] if n.startswith("At its peak")])
        v = vm("burst", cpus=16, mem_gb=64, cpu=10.0, mem=10.0)
        v["stats"]["cpu_max"], v["stats"]["mem_max"] = 90.0, 70.0
        s = size(v)
        self.assertEqual((s["cpu"]["suggested"], s["mem"]["suggested"]), (8, 32))
        self.assertIn("At its peak it used about 14.4 vCPUs, more than 8: short bursts would be slower.", s["notes"])
        self.assertIn("At its peak about 44.8 GB was active, more than 32 GB: check it before giving less.", s["notes"])

    def test_disk(self):
        s = size(vm("d", disks=[{"path": "/", "capacity_gb": 40.0, "free_gb": 20.0}, {"path": "/var", "capacity_gb": 100.0, "free_gb": 5.0}]))
        self.assertEqual((s["disk"]["path"], s["disk"]["used_pct"], s["disk"]["full"]), ("/var", 95.0, True))

    def test_mem_size(self):
        self.assertEqual([f.mem_size(x) for x in (0.2, 2.1, 5, 9.6, 17, 30)], [1, 3, 6, 10, 20, 32])


class Report(unittest.TestCase):
    VMS = [vm("web01", cpus=8, mem_gb=32, cpu=10.0, mem=10.0), vm("busy", cpus=8, cpu=90.0, mem=50.0), vm("ok01", cpus=2, mem_gb=8, cpu=40.0, mem=40.0),
           vm("new", days=2.0), vm("bal", cpus=2, mem_gb=8, cpu=40.0, mem=40.0, balloon=1024, vc="vc02.example.mil"),
           vm("disk", cpus=2, mem_gb=8, cpu=40.0, mem=40.0, disks=[{"path": "C:\\", "capacity_gb": 100.0, "free_gb": 3.0}])]
    RES = {"vcenters": [{"name": "vc01.example.mil", "ok": True, "error": ""}, {"name": "vc02.example.mil", "ok": True, "error": ""}],
           "not_checked": {"off": 3, "templates": 1}, "notes": [], "days": 30, "interval": 7200}

    def test_doc(self):
        sized = f.vm_rightsize(self.VMS)
        self.assertEqual([v["name"] for v in sized][:2], ["busy", "web01"])           # too small first, then too big
        d = f.vm_rightsize_doc(self.RES, sized)
        self.assertEqual(d["title"], "VM right-sizing: 1 too big, 1 too small of 6 VMs")
        self.assertEqual(d["status"], "warning")
        self.assertIn("3 powered off and 1 templates not checked", d["subtitle"])
        self.assertIn("suggestions only, nothing was changed", d["subtitle"])
        summary = {s["label"]: s["value"] for s in d["summary"]}
        self.assertEqual(summary, {"VMs checked": 6, "Too big": 1, "Too small": 1, "About right": 3, "vCPUs to free": 4,
                                   "Memory to free (GB)": 16, "Not enough history": 1, "Memory taken back": 1, "Disks nearly full": 1})
        titles = [s["title"] for s in d["sections"]]
        self.assertEqual(titles, ["Too small", "Memory taken back by the host", "Too big", "Guest disks nearly full", "Not enough history",
                                  "About right", "How the suggestions are worked out"])
        big = d["sections"][2]
        self.assertEqual(big["columns"], f.SIZE_COLS)
        self.assertEqual(big["rows"][0][:3], ["web01", "vc01 / CL1", "8 → 4"])          # two vCenters: the vCenter with the cluster
        self.assertEqual(big["cell_status"][0][2], "ok")
        self.assertEqual(d["sections"][0]["row_status"], ["warning"])
        self.assertEqual(d["sections"][0]["cell_status"][0][2], "warning")
        self.assertNotIn("Memory taken back", " ".join(r[-1] for r in d["sections"][5]["rows"]))   # said in its own table

    def test_cut_and_ok(self):
        sized = f.vm_rightsize([vm("w%02d" % i, cpus=8, mem_gb=32, cpu=10.0, mem=10.0) for i in range(5)])
        d = f.vm_rightsize_doc({"vcenters": [{"name": "vc01.example.mil", "ok": True}], "days": 30}, sized, {"max_rows": 2})
        big = [s for s in d["sections"] if s["title"] == "Too big"][0]
        self.assertEqual(len(big["rows"]), 2)
        self.assertIn("3 more not listed (vm_rightsize_max_rows: 2)", big["text"])
        self.assertEqual(big["rows"][0][1], "CL1")                                    # one vCenter: the cluster alone
        self.assertEqual(d["status"], "ok")
        d = f.vm_rightsize_doc({"vcenters": [], "notes": ["vCenter keeps no past-year statistics"], "days": 30}, sized)
        self.assertEqual((d["sections"][0]["title"], d["status"]), ("What vCenter's statistics could not give", "warning"))

    def test_expected_gaps_stay_green(self):
        sized = f.vm_rightsize([vm("ok01", cpus=2, mem_gb=8, cpu=40.0, mem=40.0)])
        res = {"vcenters": [{"name": "vc01.example.mil", "ok": True}], "days": 30, "notes": [],
               "info": ["swapped memory is kept at statistics level 2 only: not in this vCenter's history"]}
        d = f.vm_rightsize_doc(res, sized)
        self.assertEqual(d["status"], "ok")                                          # a level-2 counter at level 1: expected
        self.assertNotIn("What vCenter's statistics could not give", [x["title"] for x in d["sections"]])
        self.assertIn(res["info"][0], d["sections"][-1]["lines"])                   # said in how it is worked out

    def test_xlsx(self):
        sheets = f.vm_rightsize_xlsx(f.vm_rightsize(self.VMS))
        self.assertEqual([s["name"] for s in sheets], ["Right-sizing", "Guest disks"])
        cols = sheets[0]["columns"]
        row = [r for r in sheets[0]["rows"] if r[4] == "web01"][0]
        self.assertEqual((row[cols.index("Verdict")], row[cols.index("vCPUs")], row[cols.index("Suggested vCPUs")],
                          row[cols.index("Suggested memory (GB)")]), (f.TOO_BIG, 8, 4, 16))
        self.assertEqual(len(row), len(cols))
        self.assertEqual(sheets[1]["rows"], [["vc01.example.mil", "disk", "C:\\", 100.0, 3.0, 97.0]])

    def test_change_artifact(self):
        v = f.vm_rightsize([vm("web01", cpus=8, mem_gb=32, cpu=10.0, mem=10.0)])[0]
        self.assertEqual(f.vm_rightsize_change(v), {"vcenter": "vc01.example.mil", "name": "web01", "verdict": f.TOO_BIG,
                                                    "vcpus": [8, 4], "memory_gb": [32, 16]})


class Act(unittest.TestCase):
    def test_items_evidence_task_section(self):
        sized = f.vm_rightsize(Report.VMS)
        items = f.vm_rightsize_items(sized, {"max_items": 40})
        self.assertEqual([(i["id"], i["name"]) for i in items], [("P1", "busy"), ("P2", "web01")])
        ev = f.vm_rightsize_evidence(items, sized)
        self.assertIn("P2 VM web01", ev)
        self.assertIn("vCPUs 8 -> 4", ev)
        task = f.vm_rightsize_act_task(items, {"extra": "Site rule."})
        self.assertIn("For each item (P1, P2)", task)
        self.assertIn("BEGIN_ACT_ANALYSIS", task)
        self.assertTrue(task.endswith("Site rule."))
        parsed = {"parsed": True, "overall": "Shrink the web tier first.",
                  "by_id": {"P1": {"recommendation": "Agree: 12 vCPUs.", "reasoning": "90%.", "confidence": 85},
                            "P2": {"recommendation": "Adjust: keep 24 GB, it is a cache.", "reasoning": "x", "confidence": 55}}}
        secs = f.vm_rightsize_act_section(items, sized, parsed, {"ran": True})
        self.assertEqual([s["title"] for s in secs], ["GenAI (ACT): what to change first", "ACT's review of each suggestion"])
        self.assertIn("1 of 2 agreed", secs[1]["text"])
        self.assertEqual(secs[1]["rows"][1][3], "vCPUs 8 → 4; memory 32 → 16 GB")
        self.assertEqual([c[4] for c in secs[1]["cell_status"]], ["ok", "warning"])
        self.assertEqual(f.vm_rightsize_act_section(items, sized, {}, {"ran": False, "reason": "no key"})[0]["lines"],
                         ["ACT did not run: no key"])

    def test_names_masked(self):
        names = f.vm_rightsize_names([vm("web01")])
        for n in ("web01", "vc01.example.mil", "vc01", "esx01.example.mil", "esx01", "CL1", "DC1"):
            self.assertIn(n, names)


if __name__ == "__main__":
    unittest.main(verbosity=1)
