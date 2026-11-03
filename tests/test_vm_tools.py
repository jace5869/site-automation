#!/usr/bin/env python3
"""The VMware Tools inventory (plugins/filter/vmware_tools_filters.py): the report, the job's lines, the workbook and the
artifact; what vCenter's Tools fields read as.
    python tests/test_vm_tools.py"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))

import vmware_tools_filters as tf  # noqa: E402


def vm(name, ver="12389", status="guestToolsCurrent", vc="vc01.example.mil", folder="/DC1/vm/Linux", power="poweredOn",
       hw="vmx-21", **kw):
    return dict({"name": name, "vcenter": vc, "datacenter": folder.split("/")[1], "folder": folder, "cluster": "CL1",
                 "esxi_host": "esx01.example.mil", "power": power, "template": False,
                 "guest_os": "Red Hat Enterprise Linux 9 (64-bit)", "guest_os_running": "Red Hat Enterprise Linux 9.4 (64-bit)",
                 "ip": "192.0.2.10", "tools": "toolsOk", "tools_running": "guestToolsRunning", "tools_version": ver,
                 "tools_status": status, "tools_install_type": "guestToolsTypeOpenVMTools", "heartbeat": "green",
                 "hw_version": hw, "firmware": "efi"}, **kw)


VMS = [vm("web01"), vm("web02", ver="12352", status="guestToolsNeedUpgrade", hw="vmx-19"),
       vm("db01", folder="/DC1/vm/Linux/DB", status="guestToolsUnmanaged"),
       vm("old01", ver="10346", status="guestToolsTooOld", hw="vmx-13", folder="/DC1/vm"),
       vm("bare01", ver="0", status="guestToolsNotInstalled", tools_install_type="", guest_os_running="", tools="toolsNotInstalled"),
       vm("hung01", tools_running="guestToolsNotRunning", tools="toolsNotRunning", vc="vc02.example.mil", folder="/DC2/vm/Apps",
          guest_os="Microsoft Windows Server 2022 (64-bit)", guest_os_running="", tools_install_type="guestToolsTypeMSI"),
       vm("off01", ver="", status="guestToolsNotInstalled", power="poweredOff", guest_os_running=""),
       vm("tpl", folder="/DC1/vm/Templates", template=True, power="poweredOff")]
READ = [{"name": "vc01.example.mil", "ok": True, "error": ""}, {"name": "vc02.example.mil", "ok": True, "error": ""}]


def sec(d, title):
    return next(s for s in d["sections"] if s["title"] == title)


class Versions(unittest.TestCase):
    def test_tools_version_numbers(self):
        self.assertEqual(tf.vm_tools_version("12352"), "12.2.0")             # major << 10 | minor << 5 | patch
        self.assertEqual(tf.vm_tools_version("12389"), "12.3.5")
        self.assertEqual(tf.vm_tools_version("11333"), "11.2.5")
        self.assertEqual(tf.vm_tools_version("2147483647"), "guest managed")
        self.assertEqual(tf.vm_tools_version("0"), "")
        self.assertEqual(tf.vm_tools_version(""), "")
        self.assertEqual(tf.vm_tools_version("abc"), "abc")

    def test_hardware_compatibility(self):
        self.assertEqual(tf.vm_hw_compat("vmx-21"), "ESXi 8.0 U2 and later")
        self.assertEqual(tf.vm_hw_compat("vmx-19"), "ESXi 7.0 U2 and later")
        self.assertEqual(tf.vm_hw_compat("vmx-13"), "ESXi 6.5 and later")
        self.assertEqual(tf.vm_hw_compat("vmx-16"), "ESXi 6.7 U2 or later")         # no ESXi of its own: the one before
        self.assertEqual(tf.vm_hw_compat("vmx-99"), "")                             # newer than this list: not guessed
        self.assertEqual(tf.vm_hw_compat(""), "")


class Report(unittest.TestCase):
    def test_title_summary_and_status(self):
        d = tf.vm_tools_doc(VMS, READ)
        self.assertEqual(d["title"], "VMware Tools: 7 VMs - 1 to upgrade, 2 without Tools, 1 not running, 1 not supported")
        self.assertEqual(d["status"], "warning")
        self.assertIn("templates left out (1)", d["subtitle"])
        s = {x["label"]: x["value"] for x in d["summary"]}
        self.assertEqual(s, {"VMs": 7, "Tools current": 2, "Upgrade available": 1, "Guest managed": 1, "No Tools": 2,
                             "Not running (on)": 1, "Not supported": 1, "Tools versions": 3, "Hardware versions": 3})

    def test_to_look_at_powered_on_only(self):
        d = tf.vm_tools_doc(VMS, READ)
        look = sec(d, "To look at")
        self.assertEqual([r[2] for r in look["rows"]], ["old01", "bare01", "hung01"])
        self.assertEqual(look["rows"][0][5:], ["10.3.10", "too old (not supported)", "vmx-13"])
        self.assertEqual(look["rows"][1][5:7], ["-", "not installed"])
        self.assertEqual(look["rows"][2][4:7], ["Microsoft Windows Server 2022 (64-bit)", "12.3.5", "current; NOT RUNNING"])
        self.assertNotIn("off01", [r[2] for r in look["rows"]])                    # off: nothing to fix while it is off

    def test_versions_hardware_and_os(self):
        d = tf.vm_tools_doc(VMS, READ)
        tools = sec(d, "VMware Tools versions")["rows"]
        self.assertEqual([r[0] for r in tools], ["12.3.5", "12.2.0", "10.3.10", "(not installed)"])   # newest first, none last
        self.assertEqual(tools[0][1:3], [3, 3])
        self.assertIn("guest managed (updated by the OS) (1)", tools[0][3])
        self.assertIn("MSI (Windows) (1)", tools[0][4])
        self.assertEqual(tools[3][1:3], [2, 1])
        hw = sec(d, "Hardware versions")["rows"]
        self.assertEqual(hw[0], ["vmx-21", "ESXi 8.0 U2 and later", 5, 4])
        self.assertEqual([r[0] for r in hw], ["vmx-21", "vmx-19", "vmx-13"])
        oses = sec(d, "Guest OS")["rows"]
        self.assertEqual(oses[0][:2], ["Red Hat Enterprise Linux 9.4 (64-bit)", 4])                  # as Tools reports it
        self.assertIn(["Red Hat Enterprise Linux 9 (64-bit)", 2, "- (2)", "vmx-21 (2)"], oses)       # no Tools: as configured

    def test_by_vcenter_and_folder(self):
        d = tf.vm_tools_doc(VMS, READ)
        titles = [s["title"] for s in d["sections"]][4:]
        self.assertEqual(titles, ["vc01 - DC1 (no folder)", "vc01 - DC1 / Linux", "vc01 - DC1 / Linux / DB", "vc02 - DC2 / Apps"])
        linux = sec(d, "vc01 - DC1 / Linux")
        self.assertEqual(linux["columns"], ["VM", "Power", "Guest OS", "VMware Tools", "Tools status", "Hardware version"])
        self.assertEqual([r[0] for r in linux["rows"]], ["bare01", "off01", "web01", "web02"])
        self.assertEqual(linux["rows"][3][3:], ["12.2.0", "upgrade available", "vmx-19"])
        self.assertEqual(linux["cell_status"][3][4], "info")
        self.assertEqual(linux["cell_status"][0][4], "warning")
        self.assertEqual(linux["cell_status"][1][4], "")
        flat = tf.vm_tools_doc(VMS, READ, {"by_folder": False, "templates": True, "max_rows": 3})
        every = sec(flat, "Every VM")
        self.assertEqual(every["columns"][:3], ["vCenter", "Folder", "VM"])
        self.assertEqual(len(every["rows"]), 3)
        self.assertIn("5 more not listed (vm_tools_max_rows: 3)", every["text"])
        self.assertTrue(flat["title"].startswith("VMware Tools: 8 VMs"))

    def test_all_current_and_a_vcenter_not_read(self):
        d = tf.vm_tools_doc([vm("a"), vm("b", status="guestToolsUnmanaged")], READ)
        self.assertEqual((d["title"], d["status"]), ("VMware Tools: 2 VMs - all current", "ok"))
        self.assertNotIn("To look at", [s["title"] for s in d["sections"]])
        d = tf.vm_tools_doc([vm("a")], [READ[0], {"name": "vc03.example.mil", "ok": False, "error": "Could not connect to vCenter vc03"}])
        self.assertEqual(d["status"], "critical")
        self.assertTrue(d["title"].endswith(" - 1 vCenter not read"))
        self.assertEqual(d["sections"][0]["title"], "vCenters not read")

    def test_older_vcenter_without_the_version_status(self):
        old = vm("x", status="", tools="toolsOld")
        self.assertEqual(tf._state(old), ("upgrade available", "info"))
        none = vm("y", status="", tools_version="", tools="toolsNotInstalled")
        self.assertEqual(tf._state(none), ("not installed", "warning"))


class Output(unittest.TestCase):
    def test_lines_workbook_and_artifact(self):
        lines = tf.vm_tools_lines(VMS)
        self.assertIn("VMware Tools 12.3.5: 3 VM(s) - current (1), current; NOT RUNNING (1), guest managed (updated by the OS) (1)", lines)
        self.assertIn("Hardware vmx-13 (ESXi 6.5 and later): 1 VM(s)", lines)
        self.assertIn("To look at - hung01 (vc02): VMware Tools 12.3.5, current; NOT RUNNING", lines)
        vms, tools, hw = tf.vm_tools_xlsx(VMS)
        self.assertEqual([vms["name"], tools["name"], hw["name"]], ["VMs", "Tools versions", "Hardware versions"])
        self.assertEqual(len(vms["rows"]), 7)
        row = dict(zip(vms["columns"], vms["rows"][-1]))
        self.assertEqual((row["VM"], row["Folder"], row["VMware Tools"], row["Tools running"], row["Installed as"],
                          row["Guest OS (configured)"], row["Compatible with"]),
                         ("hung01", "DC2 / Apps", "12.3.5", "no", "MSI (Windows)", "Microsoft Windows Server 2022 (64-bit)",
                          "ESXi 8.0 U2 and later"))
        self.assertEqual(tf.vm_tools_versions(VMS), {"tools": {"12.3.5": 3, "12.2.0": 1, "10.3.10": 1, "-": 2},
                                                     "hardware": {"vmx-21": 5, "vmx-19": 1, "vmx-13": 1}})


if __name__ == "__main__":
    unittest.main(verbosity=1)
