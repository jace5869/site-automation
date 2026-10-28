#!/usr/bin/env python3
"""The VMware version report: the report and workbook of plugins/filter/vmware_version_filters.py, and the module
roles/vmware_vm/library/site_vmware_versions.py importable. Needs ansible-core importable, not pyVmomi.
    python tests/test_vm_versions.py"""
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

import site_vmware_versions as m  # noqa: E402,F401
import vmware_version_filters as f  # noqa: E402

VC = [{"vcenter": "vc01.example.mil", "product": "VMware vCenter Server 8.0.3 build-24322831", "version": "8.0.3",
       "build": "24322831", "api": "8.0.3"},
      {"vcenter": "vc02.example.mil", "product": "VMware vCenter Server 8.0.2 build-22617221", "version": "8.0.2",
       "build": "22617221", "api": "8.0.2"}]


def host(name, version="8.0.3", build="24022510", vc="vc01.example.mil", cluster="PROD-CL01", dc="DC1", state="connected",
         maintenance=False):
    return {"name": name, "vcenter": vc, "datacenter": dc, "cluster": cluster, "version": version, "build": build,
            "product": "VMware ESXi %s build-%s" % (version, build) if version else "", "state": state, "maintenance": maintenance}


HOSTS = [host("esx01.example.mil"), host("esx02.example.mil"), host("esx03.example.mil", build="24280767"),
         host("esx10.example.mil", vc="vc02.example.mil", cluster="DMZ-CL01", dc="DC2"),
         host("esx11.example.mil", version="8.0.2", build="22380479", vc="vc02.example.mil", cluster="DMZ-CL01", dc="DC2"),
         host("esx12.example.mil", version="", build="", vc="vc02.example.mil", cluster="", dc="DC2", state="notResponding")]
OK = [{"name": "vc01.example.mil", "ok": True, "error": ""}, {"name": "vc02.example.mil", "ok": True, "error": ""}]


class Versions(unittest.TestCase):
    def test_version_key_and_newer(self):
        self.assertEqual(f.version_key("8.0"), f.version_key("8.0.0"))
        self.assertFalse(f.vm_version_newer("8.0.0", "8.0"))             # the same version, written shorter: never newer
        self.assertTrue(f.vm_version_newer("8.0.3", "8.0.2"))
        self.assertTrue(f.vm_version_newer("8.0.2", "7.0.3"))
        self.assertFalse(f.vm_version_newer("7.0.3", "8.0.1"))
        self.assertFalse(f.vm_version_newer("", "8.0.1"))                # a host with no version (not connected): unknown
        self.assertFalse(f.vm_version_newer("8.0.1", ""))

    def test_builds_newest_first(self):
        self.assertEqual(f.vm_versions_builds(HOSTS), [{"version": "8.0.3", "build": "24280767", "hosts": 1},
                                                       {"version": "8.0.3", "build": "24022510", "hosts": 3},
                                                       {"version": "8.0.2", "build": "22380479", "hosts": 1}])


class Report(unittest.TestCase):
    def test_host_newer_than_its_vcenter_is_amber(self):
        d = f.vm_versions_doc(VC, HOSTS, OK)
        self.assertEqual(d["status"], "warning")
        self.assertEqual(d["title"], "VMware versions: 2 vCenters, 6 ESXi hosts on 3 builds - 1 newer than their vCenter")
        s = {x["title"]: x for x in d["sections"]}
        self.assertEqual([r[2] for r in s["ESXi newer than its vCenter"]["rows"]], ["esx10.example.mil"])   # 8.0.3 on vCenter 8.0.2
        self.assertEqual(s["vCenters"]["rows"][0], ["vc01.example.mil", "8.0.3", "24322831",
                                                    "VMware vCenter Server 8.0.3 build-24322831", 3, "8.0.3 (3)"])
        self.assertEqual(s["vCenters"]["rows"][1][5], "8.0.3 (1), 8.0.2 (1)")
        self.assertEqual([r[:3] for r in s["ESXi builds in use"]["rows"]], [["8.0.3", "24280767", 1], ["8.0.3", "24022510", 3],
                                                                            ["8.0.2", "22380479", 1]])

    def test_mixed_cluster_said_not_coloured(self):
        d = f.vm_versions_doc(VC[:1], HOSTS[:3], OK[:1])
        self.assertEqual(d["status"], "ok")                               # an update in progress is not a warning
        sec = [x for x in d["sections"] if x["title"] == "vc01 - ESXi hosts"][0]
        self.assertIn("PROD-CL01: 2 builds - 8.0.3 build 24280767 (1), 8.0.3 build 24022510 (2)", sec["text"])
        self.assertEqual({x["label"]: x["value"] for x in d["summary"]}["Clusters on more than one build"], 1)

    def test_host_not_connected(self):
        d = f.vm_versions_doc(VC, HOSTS, OK)
        sec = [x for x in d["sections"] if x["title"] == "vc02 - ESXi hosts"][0]
        row = [r for r in sec["rows"] if r[2] == "esx12.example.mil"][0]
        self.assertEqual(row, ["DC2", "(standalone)", "esx12.example.mil", "-", "-", "not responding"])
        self.assertEqual({x["label"]: x["value"] for x in d["summary"]}["Version not known (not connected)"], 1)

    def test_vcenter_not_read_is_red(self):
        st = OK[:1] + [{"name": "vc02.example.mil", "ok": False, "error": "Could not connect to vCenter vc02.example.mil: timed out"}]
        d = f.vm_versions_doc(VC[:1], HOSTS[:3], st)
        self.assertEqual(d["status"], "critical")
        self.assertTrue(d["title"].endswith(" - 1 vCenter not read"), d["title"])
        self.assertEqual(d["sections"][0]["lines"], ["Could not connect to vCenter vc02.example.mil: timed out"])

    def test_max_rows(self):
        d = f.vm_versions_doc(VC, HOSTS, OK, {"max_rows": 2})
        secs = [x for x in d["sections"] if x["title"].endswith("ESXi hosts")]
        self.assertEqual(len(secs[0]["rows"]), 2)
        self.assertIn("1 more not listed (vm_versions_max_rows: 2)", secs[0]["text"])

    def test_lines_and_workbook(self):
        self.assertEqual(f.vm_versions_lines(VC, HOSTS)[0],
                         "vc01.example.mil: vCenter 8.0.3 build 24322831; ESXi 8.0.3 build 24280767 (1), 8.0.3 build 24022510 (2)")
        self.assertEqual(f.vm_versions_lines(VC[:1], []), ["vc01.example.mil: vCenter 8.0.3 build 24322831; no ESXi hosts"])
        self.assertEqual((f._short("vc01.example.mil"), f._short("192.0.2.10:8443")), ("vc01", "192.0.2.10:8443"))
        wb = f.vm_versions_xlsx(VC, HOSTS)
        self.assertEqual([s["name"] for s in wb], ["vCenters", "ESXi hosts"])
        self.assertEqual(len(wb[1]["rows"]), 6)
        self.assertEqual([r[3] for r in wb[1]["rows"] if r[9] == "yes"], ["esx10.example.mil"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
