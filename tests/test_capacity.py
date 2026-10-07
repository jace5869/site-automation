#!/usr/bin/env python3
"""Unit tests for vSphere capacity planning (plugins/filter/capacity_filters.py and the pure parts of
roles/vmware_vm/library/site_vmware_capacity.py), on fixed numbers: python3 tests/test_capacity.py"""
import json
import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
sys.path.insert(0, os.path.join(HERE, "..", "roles", "vmware_vm", "library"))
import capacity_filters as cf  # noqa: E402
import vmware_alarm_filters as va  # noqa: E402
import site_vmware_capacity as sc  # noqa: E402

GB, TB, DAY = 1024 ** 3, 1024 ** 4, 86400
NOW = 1790000000          # read_at below
READ = "2026-09-21T14:13:20Z"


def line(start, per_day, days=30, end=NOW):
    return [[end - (days - i) * DAY, start + per_day * i] for i in range(days + 1)]


def host(name, mem_gb, used_gb, cores=32, mhz=2500, used_mhz=20000, conn="connected", maint=False, vms=10):
    return {"name": name, "connection": conn, "maintenance": maint, "cores": cores, "cpu_mhz": cores * mhz, "mem_bytes": mem_gb * GB,
            "cpu_used_mhz": used_mhz, "mem_used_bytes": used_gb * GB, "vms_on": vms}


DATA = {"vcenter": "vc01", "read_at": READ, "history_days": 90, "events_days": 30, "notes": [],
        "clusters": [{"name": "PROD", "datacenter": "DC1", "standalone": False, "ha": {"enabled": True, "kind": "hosts", "hosts": 1},
                      "hosts": [host("esx1", 512, 400), host("esx2", 512, 400), host("esx3", 512, 400), host("esx4", 512, 0, conn="notResponding")],
                      "vms_on": 30, "vms_off": 2, "vcpus_on": 120, "mem_configured_on": 1500 * GB, "added": 6, "removed": 1,
                      "history": {"mem": line(1100 * GB, 4 * GB), "cpu": line(50000, 100)}},
                     {"name": "LAB", "datacenter": "DC1", "standalone": False, "ha": {"enabled": False},
                      "hosts": [host("lab1", 256, 50, used_mhz=5000), host("lab2", 256, 50, used_mhz=5000)],
                      "vms_on": 10, "vms_off": 0, "vcpus_on": 20, "mem_configured_on": 200 * GB, "added": 0, "removed": 0,
                      "history": {"mem": line(100 * GB, 0), "cpu": line(10000, -10)}}],
        "datastores": [{"name": "ds_prod", "datacenter": "DC1", "clusters": ["PROD"], "hosts": 4, "shared": True, "pod": "", "type": "VMFS",
                        "accessible": True, "capacity": 10 * TB, "free": 2 * TB, "uncommitted": 4 * TB, "history": line(7.7 * TB, 10 * GB)},
                       {"name": "ds_lab", "datacenter": "DC1", "clusters": ["LAB"], "hosts": 2, "shared": True, "pod": "", "type": "NFS",
                        "accessible": True, "capacity": 10 * TB, "free": 9 * TB, "uncommitted": 0, "history": line(1 * TB, 0)},
                       {"name": "local1", "datacenter": "DC1", "clusters": ["LAB"], "hosts": 1, "shared": False, "capacity": TB, "free": 0,
                        "uncommitted": 0, "history": []}]}
OPTS = {"trend_days": 30, "min_samples": 7, "events_days": 30, "failover_hosts": 1, "mem_target_pct": 80, "cpu_target_pct": 80,
        "vcpu_per_core_max": 4, "storage_warn_pct": 85, "storage_crit_pct": 90, "runway_warn_days": 90, "runway_crit_days": 30, "max_rows": 100}


class Trend(unittest.TestCase):
    def test_straight_line(self):
        t = cf.trend(line(100.0, 2.0), 30, NOW, 7)
        self.assertAlmostEqual(t["slope"], 2.0, places=6)
        self.assertEqual((t["r2"], t["n"], t["span"]), (1.0, 31, 30.0))

    def test_window_and_too_little(self):
        self.assertEqual(cf.trend(line(0, 1.0, days=90), 30, NOW, 7)["n"], 31)        # only the last 30 days
        self.assertIsNone(cf.trend(line(0, 1.0, days=4), 30, NOW, 7))                 # 5 samples < 7
        self.assertIsNone(cf.trend([[NOW, 1], [NOW - DAY, 2]] * 5, 30, NOW, 7))       # under 3 days
        self.assertIsNone(cf.trend([], 30, NOW))
        noisy = cf.trend([[NOW - i * DAY, (i % 2) * 100.0] for i in range(20)], 30, NOW, 7)
        self.assertLess(noisy["r2"], 0.1)

    def test_days_until_and_texts(self):
        self.assertEqual(cf.days_until(50, 5, 100), 10)
        self.assertEqual(cf.days_until(100, 5, 100), 0)
        self.assertIsNone(cf.days_until(50, 0, 100))
        self.assertIsNone(cf.days_until(50, -1, 100))
        self.assertIsNone(cf.days_until(0, 5, 0))                                     # no capacity: no runway
        self.assertEqual(cf.runway_text(None), "not growing")
        self.assertEqual(cf.runway_text(0), "reached")
        self.assertTrue(cf.runway_text(10).startswith("10 days ("))
        self.assertEqual(cf.fit_text({"r2": 0.91, "span": 30.0}), "good (R2 0.91, 30 days)")


class Numbers(unittest.TestCase):
    def setUp(self):
        self.N = cf.capacity_numbers(DATA, OPTS)
        self.prod = self.N["clusters"][0]
        self.lab = self.N["clusters"][1]

    def test_n_plus_one(self):
        p = self.prod
        self.assertEqual((p["hosts_usable"], p["hosts_total"], p["failover_hosts"]), (3, 4, 1))   # esx4 not responding
        self.assertEqual((p["mem"], p["mem_n1"], p["mem_used"]), (1536 * GB, 1024 * GB, 1200 * GB))
        self.assertEqual((p["mem_pct"], p["mem_n1_pct"]), (78.1, 117.2))
        self.assertEqual(p["vcpu_ratio"], round(120 / 64.0, 2))                                  # cores left with one host failed
        self.assertEqual(p["mem_days"], 0)                                                       # past the target already
        self.assertEqual(p["status"], "critical")
        self.assertEqual(p["status_cells"]["n1"], "critical")
        self.assertEqual(p["status_cells"]["hosts"], "warning")

    def test_hosts_recommended(self):
        p = self.prod
        # memory: 1200 GB used must be <= 80 % of what is left: 1500 GB needed, 1024 GB left -> 476 GB -> 1 host of 512 GB
        self.assertEqual(p["hosts_now"], 1)
        # in 12 months at +4 GB a day: 1200 + 4 * 365 GB -> 3324 GB needed -> 5 hosts
        self.assertEqual(p["hosts_later"], 5)
        self.assertEqual(p["mem_avg_after"], 58.6)
        self.assertEqual(cf.hosts_text(p), "1 now (memory 59% on average then); 5 within 12 months")
        self.assertEqual(cf.hosts_text(self.lab), "none within 12 months")
        self.assertEqual(cf.hosts_for(100, 100, 50, 0.8), 1)
        self.assertEqual(cf.hosts_for(10, 100, 50, 0.8), 0)
        self.assertIsNone(cf.hosts_for(10, 100, 0, 0.8))
        ov = self.N["overall"]
        self.assertEqual((ov["hosts_now"], ov["hosts_later"]), (1, 5))

    def test_room_and_limit(self):
        l = self.lab
        # memory: 256 GB left after one host fails, 80 % = 204.8 GB, 100 GB used, 10 GB used per VM -> 10 more
        self.assertEqual(l["fits"]["memory"], 10)
        self.assertEqual(l["fits"]["vCPUs per core"], 54)                                       # (32 * 4 - 20) / 2
        self.assertEqual((l["fit"], l["limit"]), (10, "memory"))
        self.assertIsNone(l["mem_days"])                                                         # flat: not growing
        self.assertEqual(l["net_per_month"], 0)
        self.assertEqual(self.prod["net_per_month"], 5.0)

    def test_storage_and_overall(self):
        ds = {d["name"]: d for d in self.N["datastores"]}
        self.assertEqual(ds["ds_prod"]["pct"], 80.0)
        self.assertEqual(ds["ds_prod"]["prov_pct"], 120.0)
        self.assertEqual(ds["ds_prod"]["days_warn"], 51)          # (8.5 TB - 8 TB) / 10 GB a day
        self.assertEqual(ds["ds_prod"]["days_crit"], 102)
        self.assertEqual(ds["ds_prod"]["status"], "ok")
        self.assertFalse(ds["local1"]["shared"])
        ov = self.N["overall"]
        self.assertEqual(ov["storage"]["capacity"], 20 * TB)      # the local datastore is not counted
        self.assertEqual(ov["hosts"], 6)
        self.assertEqual(ov["vms_on"], 40)
        self.assertEqual(self.lab["storage"]["datastores"], 1)


class Report(unittest.TestCase):
    def test_report_and_items(self):
        r = cf.vm_capacity_report(DATA, OPTS)
        rep = r["report"]
        self.assertEqual(rep["title"], "vSphere capacity planning: 1 of 2 cluster(s) short on CPU or memory")
        self.assertEqual(rep["status"], "critical")
        titles = [s["title"] for s in rep["sections"]]
        self.assertEqual(titles[:5], ["Overall", "Clusters: CPU and memory", "Clusters: storage", "Datastores: runway", "Hosts per cluster"])
        comp = rep["sections"][1]
        self.assertEqual(comp["rows"][0][0], "PROD")
        self.assertEqual(comp["cell_status"][0][5], "critical")                               # memory after host failure
        self.assertEqual(comp["rows"][1][11], "10 (memory)")
        self.assertEqual(comp["rows"][0][12], "1 now (memory 59% on average then); 5 within 12 months")
        self.assertEqual(rep["sections"][0]["rows"][2][6], "1 recommended now, 5 within 12 months")
        self.assertEqual([i["id"] for i in r["items"]], ["P1", "P2", "P3", "P4", "P5"])
        self.assertEqual((r["items"][1]["kind"], r["items"][1]["name"], r["items"][1]["job_days"]), ("cluster", "PROD", 0))
        json.dumps(r["numbers"])                                                               # artifacts must be JSON
        ev = cf.vm_capacity_evidence(r, OPTS)
        self.assertIn("P2 cluster PROD", ev)
        self.assertIn("P4 datastore ds_prod", ev)
        self.assertNotIn("local1", ev)
        task = cf.vm_capacity_act_task(r["items"], OPTS)
        self.assertIn("estimate_days", task)
        self.assertIn("Do not write buy, purchase", task)
        self.assertIn("hosts recommended (average host here: 32 cores, 512.0 GB memory): 1 now, 5 within 12 months", ev)
        self.assertIn("BEGIN_ACT_ANALYSIS", task)
        self.assertIn("esx1", cf.vm_capacity_names(DATA))

    def test_math_vs_genai(self):
        self.assertEqual(cf.compare(100, 110), ("agree", "ok"))
        self.assertEqual(cf.compare(100, 20), ("GenAI 80 days sooner", "warning"))
        self.assertEqual(cf.compare(None, 40), ("only GenAI sees a limit", "warning"))
        self.assertEqual(cf.compare(40, None), ("only the math sees a limit", "warning"))
        self.assertEqual(cf.compare(None, None), ("agree: no limit in sight", "ok"))
        self.assertEqual(cf.compare(15933, None), ("agree: no limit in sight", "ok"))      # 40 years out: no limit
        self.assertEqual(cf.days_text(15933), "10+ years")
        self.assertEqual(cf.days_text(None), "not growing")
        r = cf.vm_capacity_report(DATA, OPTS)
        answer = ("BEGIN_ACT_ANALYSIS\n" + json.dumps({"overall": "Add a host to PROD now.", "items": [
            {"id": "P2", "estimate": "now", "estimate_days": 0, "recommendation": "add one 512 GB host", "reasoning": "117 % after a failure",
             "confidence": 90},
            {"id": "P4", "estimate": "in about 2 months", "estimate_days": "55", "recommendation": "grow ds_prod", "reasoning": "10 GB a day",
             "confidence": 65}]}) + "\nEND_ACT_ANALYSIS")
        parsed = va.act_analysis_parse(answer, r["items"])
        self.assertEqual(parsed["by_id"]["P4"]["estimate_days"], 55)
        self.assertEqual(parsed["by_id"]["P2"]["recommendation"], "add one 512 GB host")
        secs = cf.vm_capacity_act_section(r["items"], parsed, dict(OPTS, ran=True))
        self.assertEqual(secs[0]["lines"], ["Add a host to PROD now."])
        t = secs[1]
        p2 = [row for row in t["rows"] if row[0] == "P2"][0]
        p4 = [row for row in t["rows"] if row[0] == "P4"][0]
        self.assertEqual((p2[2], p2[3], p2[4]), ("0 days (memory)", "0 days", "agree"))
        self.assertEqual((p4[2], p4[3], p4[4]), ("102 days (90% full)", "55 days", "GenAI 47 days sooner"))
        no = cf.vm_capacity_act_section(r["items"], {}, {"ran": False, "reason": "no key"})
        self.assertEqual(no[0]["lines"], ["ACT did not run: no key"])


class ModuleParts(unittest.TestCase):
    def test_series_drops_missing(self):
        self.assertEqual(sc.series([3, 1, 2, 4], [30, 10, -1, 40], 1024), [[1, 10240.0], [3, 30720.0], [4, 40960.0]])

    def test_ha_policy(self):
        ns = types.SimpleNamespace
        off = ns(dasConfig=ns(enabled=False))
        self.assertEqual(sc.ha_policy(off)["enabled"], False)

        class ClusterFailoverLevelAdmissionControlPolicy(object):
            failoverLevel = 2
        cfg = ns(dasConfig=ns(enabled=True, admissionControlEnabled=True, admissionControlPolicy=ClusterFailoverLevelAdmissionControlPolicy()))
        self.assertEqual((sc.ha_policy(cfg)["kind"], sc.ha_policy(cfg)["hosts"]), ("hosts", 2))
        self.assertEqual(sc.ha_policy(None)["enabled"], False)


if __name__ == "__main__":
    unittest.main()
