#!/usr/bin/env python3
"""The VMware hardware inventory: the report and workbook of plugins/filter/vmware_hardware_filters.py, and the module
roles/vmware_vm/library/site_vmware_hardware.py importable (its serial and asset tag reading, the adapter kinds). Needs
ansible-core importable, not pyVmomi.
    python tests/test_vm_hardware.py"""
import importlib.util
import os
import sys
import types
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

import site_vmware_hardware as m  # noqa: E402
import vmware_hardware_filters as f  # noqa: E402

OK = [{"name": "vc01.example.mil", "ok": True, "error": ""}]


def host(name, model="PowerEdge R750", bios="1.10.2", serial="ABC1234", cluster="PROD-CL01", state="connected", **kw):
    h = {"name": name, "vcenter": "vc01.example.mil", "datacenter": "DC1", "cluster": cluster, "vendor": "Dell Inc.",
         "model": model, "serial": serial, "asset_tag": "", "bios": bios, "bios_date": "2026-03-01",
         "cpu_model": "Intel(R) Xeon(R) Gold 6338", "cpu_sockets": 2, "cpu_cores": 64, "cpu_threads": 128, "memory_gb": 1024,
         "esxi": "8.0.3", "build": "24022510", "product": "VMware ESXi 8.0.3 build-24022510", "image_profile": "DEL-ESXi-803",
         "state": state, "maintenance": False,
         "nics": [{"device": "vmnic0", "driver": "bnxtnet", "speed_mb": 25000, "mac": "00:00:5e:00:53:01", "pci": "0000:31:00.0"},
                  {"device": "vmnic1", "driver": "bnxtnet", "speed_mb": 25000, "mac": "00:00:5e:00:53:02", "pci": "0000:31:00.1"},
                  {"device": "vmnic2", "driver": "igbn", "speed_mb": 0, "mac": "00:00:5e:00:53:03", "pci": "0000:01:00.0"}],
         "hbas": [{"device": "vmhba0", "model": "PERC H755", "driver": "lsi_mr3", "status": "online", "kind": "local / RAID"},
                  {"device": "vmhba1", "model": "LPe35002", "driver": "lpfc", "status": "online", "kind": "Fibre Channel"}],
         "components": [{"name": "Dell Inc. iDRAC 7.00.00.00", "state": "green"}],
         "vibs": [{"name": "bnxtnet", "version": "230.1.111-1OEM", "vendor": "BCM", "installed": "2026-03-02", "summary": ""}],
         "note": ""}
    h.update(kw)
    return h


HOSTS = [host("esx01.example.mil"), host("esx02.example.mil", bios="1.9.0"),
         host("esx03.example.mil", model="ProLiant DL380 Gen11", serial="", cluster="DEV-CL01"),
         host("esx04.example.mil", state="notResponding", note="not connected (notResponding): what vCenter last knew")]


class Module(unittest.TestCase):
    def test_serial_and_asset_tag(self):
        def info(**kw):
            others = [types.SimpleNamespace(identifierType=types.SimpleNamespace(key=k), identifierValue=v) for k, v in kw.items()]
            return types.SimpleNamespace(serialNumber="", otherIdentifyingInfo=others)
        self.assertEqual(m._ids(info(ServiceTag="ABC1234", AssetTag="No Asset Tag")), ("ABC1234", ""))
        self.assertEqual(m._ids(info(EnclosureSerialNumberTag="ENC9", AssetTag="IT-0042")), ("ENC9", "IT-0042"))
        self.assertEqual(m._ids(types.SimpleNamespace(serialNumber="SN77", otherIdentifyingInfo=[])), ("SN77", ""))
        self.assertEqual(m._ids(None), ("", ""))

    def test_adapter_kinds(self):
        def a(name):
            return type(name, (), {})()
        self.assertEqual(m._kind(a("FibreChannelHba")), "Fibre Channel")
        self.assertEqual(m._kind(a("InternetScsiHba")), "iSCSI")
        self.assertEqual(m._kind(a("SomethingNewHba")), "SomethingNew")


class Report(unittest.TestCase):
    def test_report(self):
        d = f.vm_hardware_doc(HOSTS, OK)
        self.assertEqual(d["title"], "VMware hardware: 4 ESXi hosts, 2 models - 1 not connected")
        self.assertEqual(d["status"], "warning")
        models = next(s for s in d["sections"] if s["title"] == "Models")
        self.assertEqual(models["rows"][0][:4], ["Dell Inc.", "PowerEdge R750", 3, "1.10.2 (2), 1.9.0 (1)"])
        hosts = next(s for s in d["sections"] if s["title"] == "ESXi hosts")
        row = hosts["rows"][0]
        self.assertIn("2x bnxtnet 25G, igbn", row)
        self.assertIn("lsi_mr3 (local / RAID), lpfc (Fibre Channel)", row)
        self.assertIn("1.10.2 (2026-03-01)", row)
        self.assertIn("2 x Intel(R) Xeon(R) Gold 6338, 64 cores", row)
        self.assertEqual(hosts["row_status"], ["", "", "", "warning"])
        summary = {s["label"]: s["value"] for s in d["summary"]}
        self.assertEqual(summary["With a serial number"], "3 of 4")
        self.assertEqual(summary["BIOS versions"], 3)

    def test_vcenter_not_read_and_rows_cut(self):
        d = f.vm_hardware_doc(HOSTS, OK + [{"name": "vc02.example.mil", "ok": False, "error": "login refused"}], {"max_rows": 2})
        self.assertEqual(d["status"], "critical")
        self.assertEqual(d["sections"][0]["title"], "vCenters not read")
        hosts = next(s for s in d["sections"] if s["title"] == "ESXi hosts")
        self.assertEqual(len(hosts["rows"]), 2)
        self.assertIn("2 more not listed (vm_hardware_max_rows: 2)", hosts["text"])

    def test_workbook_and_lines(self):
        x = {s["name"]: s for s in f.vm_hardware_xlsx(HOSTS)}
        self.assertEqual(list(x), ["ESXi hosts", "NICs", "Storage adapters", "Software components", "VIBs"])
        self.assertEqual(len(x["NICs"]["rows"]), 12)
        self.assertIn("down", x["NICs"]["rows"][2])
        self.assertEqual(x["VIBs"]["rows"][0][3:6], ["bnxtnet", "230.1.111-1OEM", "BCM"])
        self.assertTrue(all(len(r) == len(s["columns"]) for s in x.values() for r in s["rows"]))
        self.assertEqual(f.vm_hardware_lines(HOSTS)[0],
                         "Dell Inc. PowerEdge R750: 3 host(s); BIOS 1.10.2 (2), 1.9.0 (1); ESXi 8.0.3 build 24022510 (3)")
        self.assertEqual(f.vm_hardware_models(HOSTS)[1]["model"], "ProLiant DL380 Gen11")
        self.assertEqual(f.vm_hardware_lines(HOSTS)[-1],
                         "Not all read - esx04.example.mil: not connected (notResponding): what vCenter last knew")


if __name__ == "__main__":
    unittest.main()
