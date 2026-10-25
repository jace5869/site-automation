#!/usr/bin/env python3
"""Unit tests for the hosts' and VMs' network drops (plugins/filter/vmware_net_drops_filters.py and the pure parts of
roles/vmware_vm/library/site_vmware_net_drops.py), on fixed numbers: python3 tests/test_vm_net_drops.py"""
import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
sys.path.insert(0, os.path.join(HERE, "..", "roles", "vmware_vm", "library"))
# the modules import their shared vCenter code as Ansible does: from ansible.module_utils.site_vcenters
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("ansible.module_utils.site_vcenters", os.path.join(
    HERE, "..", "roles", "vmware_vm", "module_utils", "site_vcenters.py"))
site_vcenters = importlib.util.module_from_spec(_spec)
sys.modules["ansible.module_utils.site_vcenters"] = site_vcenters
_spec.loader.exec_module(site_vcenters)
import vmware_net_drops_filters as nf  # noqa: E402
import site_vmware_net_drops as nd  # noqa: E402

KEYS = {460: "rx_dropped", 461: "tx_dropped", 468: "rx_errors", 469: "tx_errors"}


def series(counter, instance, values):
    return types.SimpleNamespace(id=types.SimpleNamespace(counterId=counter, instance=instance), value=values)


def result(moid, *values):
    return types.SimpleNamespace(entity=types.SimpleNamespace(_moId=moid), value=list(values))


def row(kind, name, hours=24.0, rx=0, tx=0, erx=0, etx=0, nics=None, host="", cluster="CL1"):
    return {"kind": kind, "name": name, "datacenter": "DC1", "cluster": cluster, "host": host, "hours": hours,
            "rx_dropped": rx, "tx_dropped": tx, "rx_errors": erx, "tx_errors": etx, "nics": nics or []}


def read(rows, source="past_day", hours=24.0, level=2, note="", vcenter="vc01.example.mil"):
    return {"vcenter": vcenter, "source": source, "interval": 300 if source == "past_day" else 20, "level": level,
            "hours": hours, "by_nic": level >= 3, "note": note, "hosts_read": 4, "vms_read": 40, "failed": 0, "rows": rows}


class Source(unittest.TestCase):
    def test_level_2_reads_the_past_day_totals(self):
        src, iv, lvl, hours, by_nic, note = nd.source([(300, 2, True, 86400), (1800, 1, True, 604800)], 24)
        self.assertEqual((src, iv, lvl, hours, by_nic), ("past_day", 300, 2, 24.0, False))
        self.assertIn("by NIC needs level 3", note)

    def test_level_3_reads_by_nic_and_hours_capped(self):
        src, iv, lvl, hours, by_nic, note = nd.source([(300, 3, True, 86400)], 48)
        self.assertEqual((src, hours, by_nic, note), ("past_day", 24.0, True, ""))

    def test_level_1_falls_back_to_the_last_hour(self):
        src, iv, lvl, hours, by_nic, note = nd.source([(300, 1, True, 86400)], 24)
        self.assertEqual((src, iv, hours, by_nic), ("last_hour", 20, 1.0, True))
        self.assertIn("at level 1 (these counters need 2)", note)
        self.assertIn("5-minute interval to level 2", note)

    def test_five_minute_interval_off(self):
        self.assertIn("turned off", nd.source([(300, 4, False, 86400)], 24)[5])
        self.assertEqual(nd.source([(300, 4, False, 86400)], 24)[0], "last_hour")
        self.assertIn("no 5-minute", nd.source([(1800, 2, True, 604800)], 24)[5])


class Counting(unittest.TestCase):
    def test_minus_one_is_no_sample(self):
        self.assertEqual(nd.totals([5, -1, 3, None, 0], 20), (8, 60))

    def test_entity_total_and_nics(self):
        res = [result("host-1", series(460, "", [1, 2, 3]), series(460, "vmnic0", [1, 2, 0]), series(460, "vmnic1", [0, 0, 3]),
                      series(468, "vmnic1", [0, 1, -1])),
               result("vm-7", series(461, "4000", [10, 10]), series(461, "4001", [5, -1]))]
        out = nd.add_results(res, KEYS, 20)
        self.assertEqual(out["host-1"][""], {"rx_dropped": 6})       # vCenter's own total wins
        self.assertEqual(out["host-1"]["vmnic1"], {"rx_dropped": 3, "rx_errors": 1})
        self.assertEqual(out["host-1"]["_seconds"], 60)
        # no total from vCenter (level 3 by NIC only): the NICs are added up
        self.assertEqual(out["vm-7"][""], {"rx_dropped": 0, "tx_dropped": 25, "rx_errors": 0, "tx_errors": 0})

    def test_wildcard_echo_ignored(self):
        out = nd.add_results([result("vm-1", series(460, "*", []))], KEYS, 300)
        self.assertEqual(out["vm-1"][""], {"rx_dropped": 0, "tx_dropped": 0, "rx_errors": 0, "tx_errors": 0})


class Problems(unittest.TestCase):
    def test_thresholds_per_hour(self):
        d = {"vcenters": [{"name": "vc01.example.mil", "ok": True, "error": ""}], "read": [read([
            row("host", "esx01", rx=2400),                          # 100 an hour: warning
            row("host", "esx02", rx=2399),                          # just under
            row("vm", "app01", hours=1.0, rx=150, host="esx01"),    # the last hour: 150 an hour
            row("vm", "app02", erx=240, host="esx02"),              # errors 10 an hour
        ])]}
        ps = nf.vm_net_drops_problems(d, {"drops_warn_per_hour": 100, "errors_warn_per_hour": 10})
        self.assertEqual([(p["object_type"], p["object"], p["severity"]) for p in ps],
                         [("host", "esx01", "warning"), ("vm", "app01", "warning"), ("vm", "app02", "warning")])
        self.assertIn("2400 packets dropped in the past 24 hours (2400 received, 0 sent; 100 an hour)", ps[0]["problem"])
        self.assertIn("in the last hour", ps[1]["problem"])
        self.assertIn("240 network errors", ps[2]["problem"])
        self.assertIn("VMXNET3", ps[1]["fix"])
        self.assertIn("SFP", ps[0]["fix"])
        self.assertEqual((ps[0]["datacenter"], ps[0]["cluster"]), ("DC1", "CL1"))

    def test_never_critical(self):
        d = {"read": [read([row("host", "esx01", rx=10 ** 9, erx=10 ** 9)])]}
        self.assertEqual({p["severity"] for p in nf.vm_net_drops_problems(d)}, {"warning"})

    def test_unreadable_vcenter_once(self):
        d = {"vcenters": [{"name": "vc01", "ok": False, "error": "Could not connect"}, {"name": "vc02", "ok": False, "error": "no"}],
             "read": []}
        ps = nf.vm_net_drops_problems(d, {"known_down": ["vc01"]})
        self.assertEqual([p["object"] for p in ps], ["vc02"])
        self.assertEqual(ps[0]["object_type"], "vcenter")

    def test_module_failed_entirely(self):
        ps = nf.vm_net_drops_problems({"failed": True, "msg": "pyVmomi is not installed"})
        self.assertEqual(len(ps), 1)
        self.assertIn("pyVmomi", ps[0]["problem"])


class Doc(unittest.TestCase):
    base = {"title": "VMware health report: 1 warning(s) - 1 datacenter(s)", "status": "warning",
            "sections": [{"title": "Needs attention"}, {"title": "Single sign-on and OCSP"}, {"title": "Datacenters"}]}

    def test_section_after_sso_and_title(self):
        d = {"read": [read([row("host", "esx01", rx=4800, nics=[{"name": "vmnic0", "rx_dropped": 4800, "tx_dropped": 0,
                                                                      "rx_errors": 0, "tx_errors": 0}]),
                            row("vm", "app01", rx=10, host="esx01")])]}
        doc = nf.vm_net_drops_doc(self.base, d)
        self.assertEqual([s["title"] for s in doc["sections"]],
                         ["Needs attention", "Single sign-on and OCSP", "Network drops and errors", "Datacenters"])
        sec = doc["sections"][2]
        self.assertEqual(sec["row_status"], ["warning", "ok"])
        self.assertEqual(sec["rows"][0][1], "host esx01")
        self.assertIn("vmnic0 4800/0 dropped", sec["rows"][0][9])
        self.assertIn("4 host(s) and 40 powered-on VM(s) read", sec["text"])
        self.assertIn("the past 24 hours of vCenter's statistics (level 2)", sec["text"])
        self.assertTrue(doc["title"].endswith(" - network: 1 warning(s)"))
        self.assertEqual(doc["status"], "warning")

    def test_level_1_note_and_nothing_found(self):
        note = nd.source([(300, 1, True, 86400)], 24)[5]
        doc = nf.vm_net_drops_doc({"title": "T", "status": "ok", "sections": [{"title": "Needs attention"}]},
                                  {"read": [read([], source="last_hour", hours=1.0, level=1, note=note)]})
        sec = doc["sections"][1]
        self.assertEqual(sec["lines"], ["No dropped packets and no network errors."])
        self.assertIn("the last hour (the hosts' real-time statistics)", sec["text"])
        self.assertIn("Statistics: the 5-minute interval to level 2", sec["text"])
        self.assertEqual((doc["title"], doc["status"]), ("T", "ok"))

    def test_critical_status_kept(self):
        doc = nf.vm_net_drops_doc({"title": "T", "status": "critical", "sections": []}, {"read": [read([row("host", "h", rx=10 ** 6)])]})
        self.assertEqual(doc["status"], "critical")

    def test_nothing_read_no_section(self):
        self.assertEqual(nf.vm_net_drops_doc({"title": "T", "sections": []}, {})["sections"], [])


if __name__ == "__main__":
    unittest.main()
