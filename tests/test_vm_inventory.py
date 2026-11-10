#!/usr/bin/env python3
"""The VM inventory report (plugins/filter/vmware_inventory_filters.py: the VM tables, the VMs without encryption, EFI or
vTPM, the hardware versions and Tools, the website's columns), the Excel workbook every report's email may
attach (roles/site_email/library/site_xlsx.py; report_xlsx_sheets in plugins/filter/site_filters.py).
    python tests/test_vm_inventory.py"""
import io
import os
import re
import sys
import tempfile
import unittest
import zipfile
from xml.dom import minidom

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
sys.path.insert(0, os.path.join(REPO, "roles", "site_email", "library"))

import site_filters as sf  # noqa: E402
import site_xlsx as xl  # noqa: E402
import vmware_inventory_filters as inv  # noqa: E402


def vm(name, vc="vc01.example.mil", folder="/DC1/vm/Linux", power="poweredOn", host="esx01.example.mil", **kw):
    return dict({"name": name, "vcenter": vc, "datacenter": folder.split("/")[1], "folder": folder, "cluster": "CL1",
                 "esxi_host": host, "power": power, "template": False, "guest_os": "Red Hat Enterprise Linux 9 (64-bit)",
                 "ip": "192.0.2.10", "ips": ["192.0.2.10"], "hostname": name + ".example.mil", "cpus": 2, "memory_mb": 6144}, **kw)


VMS = [vm("web02"), vm("web01", host="esx02.example.mil"), vm("db01", folder="/DC1/vm/Linux/DB", power="poweredOff", ip=""),
       vm("root01", folder="/DC1/vm"), vm("app01", vc="vc02.example.mil", folder="/DC2/vm/Apps"),
       vm("tpl", folder="/DC1/vm/Templates", template=True, power="poweredOff")]
READ = [{"name": "vc01.example.mil", "ok": True, "error": ""}, {"name": "vc02.example.mil", "ok": True, "error": ""}]


def folders(d):
    """The VM tables (one per vCenter and folder, or Every VM): the sections after the summary ones."""
    return [s for s in d["sections"] if s["title"] not in ("vCenters not read", "Hardware versions and Tools",
                                                            "VMs without encryption, EFI or vTPM",
                                                            "VMware Tools to upgrade or not running")]


# what site_vmware_vms reads with security (1.11.0), and the hardware and Tools it always reads
SEC = [vm("enc01", encrypted=True, key_provider="provider-a", migrate_encryption="required", firmware="efi", secure_boot=True,
          vtpm=True, hw_version="vmx-21", tools_version="12389", tools_status="guestToolsCurrent", tools_running="guestToolsRunning"),
       vm("bios01", encrypted=False, firmware="bios", secure_boot=False, vtpm=False, hw_version="vmx-15", tools_version="11333",
          tools_status="guestToolsNeedUpgrade", tools_running="guestToolsRunning"),
       vm("efi01", encrypted=False, firmware="efi", secure_boot=False, vtpm=True, hw_version="vmx-21", tools_version="12389",
          tools_status="guestToolsCurrent", tools_running="guestToolsNotRunning"),
       vm("off01", power="poweredOff", encrypted=False, firmware="efi", secure_boot=True, vtpm=False, hw_version="vmx-19",
          tools_status="guestToolsNotInstalled", tools="toolsNotInstalled"),
       vm("old01", encrypted=True, firmware="efi", secure_boot=True, vtpm=None, hw_version="vmx-19", tools_version="10346",
          tools_status="guestToolsTooOld", tools_running="guestToolsRunning"),
       vm("tpl02", template=True, power="poweredOff", encrypted=False, firmware="bios", vtpm=False)]


class Inventory(unittest.TestCase):
    def test_folder_label(self):
        self.assertEqual(inv.vm_folder_label("/DC1/vm/Linux/Web"), "DC1 / Linux / Web")
        self.assertEqual(inv.vm_folder_label("/DC1/vm"), "DC1 (no folder)")
        self.assertEqual(inv.vm_folder_label("/DC1/Other"), "DC1 / Other")             # no hidden vm folder: as it is
        self.assertEqual(inv.vm_folder_label(""), "(no folder)")

    def test_by_vcenter_and_folder_with_the_count(self):
        d = inv.vm_inventory_doc(VMS, READ)
        self.assertEqual(d["title"], "VM inventory: 5 VMs in 2 vCenters")
        self.assertEqual(d["status"], "ok")
        self.assertIn("templates left out (1)", d["subtitle"])
        summary = {s["label"]: s["value"] for s in d["summary"]}
        self.assertEqual(summary, {"VMs": 5, "Powered on": 4, "Powered off": 1, "Folders": 4, "ESXi hosts": 3, "vCenters": 2})   # one per vCenter
        self.assertEqual([s["title"] for s in d["sections"]],
                         ["Hardware versions and Tools", "vc01 - DC1 (no folder)", "vc01 - DC1 / Linux", "vc01 - DC1 / Linux / DB",
                          "vc02 - DC2 / Apps"])                                         # no security: not read, no section
        linux = folders(d)[1]
        self.assertEqual(linux["columns"], ["VM", "Power", "ESXi host", "Cluster", "Guest OS", "IP address", "vCPUs", "Memory (GB)"])
        self.assertEqual([r[0] for r in linux["rows"]], ["web01", "web02"])            # by name
        self.assertEqual(linux["rows"][0][2], "esx02.example.mil")                     # the host it runs on
        self.assertEqual(linux["rows"][0][7], "6")
        self.assertEqual(folders(d)[2]["rows"][0][1:6:4], ["off", "-"])                # no IP: a dash
        self.assertNotIn("column_titles", linux)                                       # the email's: as before

    def test_templates_flat_and_cut(self):
        d = inv.vm_inventory_doc(VMS, READ, {"templates": True, "by_folder": False, "max_rows": 3})
        self.assertEqual(d["title"], "VM inventory: 6 VMs in 2 vCenters")
        self.assertEqual(len(folders(d)), 1)
        self.assertEqual(folders(d)[0]["columns"][:3], ["vCenter", "Folder", "VM"])
        self.assertEqual(len(folders(d)[0]["rows"]), 3)
        self.assertIn("(template)", " ".join(str(c) for r in folders(inv.vm_inventory_doc(VMS, READ, {"templates": True}))[3]["rows"]
                                              for c in r))
        cut = inv.vm_inventory_doc(VMS, READ, {"max_rows": 2})
        self.assertEqual(sum(len(s.get("rows") or []) for s in folders(cut)), 2)
        self.assertIn("more not listed (vm_inventory_max_rows: 2)", " ".join(s.get("text", "") for s in cut["sections"]))

    def test_a_vcenter_not_read(self):
        d = inv.vm_inventory_doc(VMS[:2], [READ[0], {"name": "vc03.example.mil", "ok": False, "error": "Could not connect to vCenter vc03"}])
        self.assertEqual(d["status"], "critical")
        self.assertEqual(d["title"], "VM inventory: 2 VMs in 1 vCenter - 1 not read")
        self.assertEqual(d["sections"][0]["title"], "vCenters not read")

    def test_the_workbook_sheet(self):
        (s,) = inv.vm_inventory_xlsx(VMS)
        self.assertEqual(s["name"], "VMs")
        self.assertEqual([r[3] for r in s["rows"]], ["root01", "web01", "web02", "db01", "app01"])   # vCenter, folder, name
        self.assertEqual(s["rows"][1][11:13], [2, 6])                                  # numbers stay numbers
        self.assertEqual(len(inv.vm_inventory_xlsx(VMS, {"templates": True})[0]["rows"]), 6)
        self.assertNotIn("Encrypted", s["columns"])                                   # security not read: not in it
        self.assertEqual(s["columns"][-6:], ["Hardware version", "Compatible with", "VMware Tools", "Tools version",
                                             "Tools running", "Template"])


class Security(unittest.TestCase):
    """1.11.0: the VMs without encryption, EFI or vTPM; the hardware versions and Tools; the website's columns."""
    OPTS = {"security": True}

    def section(self, d, title):
        return [s for s in d["sections"] if s["title"] == title][0]

    def test_the_section_most_missing_first_and_its_counts(self):
        d = inv.vm_inventory_doc(SEC, READ, self.OPTS)
        s = self.section(d, "VMs without encryption, EFI or vTPM")
        self.assertEqual(s["columns"], ["VM", "Encrypted", "Firmware (EFI or BIOS)", "Secure Boot", "Virtual TPM", "vCenter",
                                        "Folder", "Power", "Guest OS", "Hardware version"])     # the email's: full names
        # bios01 lacks all three, off01 encryption and vTPM, efi01 encryption; enc01 has all, old01's vTPM not known
        self.assertEqual([r[0] for r in s["rows"]], ["bios01", "off01", "efi01"])
        self.assertEqual(s["rows"][0][1:5], ["no", "BIOS", "off", "no"])
        self.assertEqual(s["cell_status"][0][1:5], ["warning"] * 4)                   # a word and its chip, never colour alone
        self.assertEqual(s["rows"][2][1:5], ["no", "EFI", "off", "yes"])
        self.assertEqual(s["cell_status"][2][1:5], ["warning", "ok", "warning", "ok"])
        self.assertEqual(s["rows"][0][-1], "15")                                       # vmx-15 -> 15
        tiles = {t["label"]: t for t in d["summary"]}
        self.assertEqual([tiles[k]["value"] for k in ("Not encrypted", "Without EFI", "Without vTPM")], [3, 1, 2])
        self.assertEqual(tiles["Not encrypted"]["status"], "info")
        self.assertEqual(d["status"], "ok")                                            # a report, never a warning

    def test_skip_off_and_templates(self):
        d = inv.vm_inventory_doc(SEC, READ, dict(self.OPTS, templates=True))
        rows = self.section(d, "VMs without encryption, EFI or vTPM")["rows"]
        self.assertNotIn("tpl02", [r[0] for r in rows])                                # vm_inventory_security_skip_templates
        d = inv.vm_inventory_doc(SEC, READ, dict(self.OPTS, templates=True, security_skip_templates=False, security_skip_off=True))
        rows = self.section(d, "VMs without encryption, EFI or vTPM")["rows"]
        self.assertEqual([r[0] for r in rows], ["bios01", "efi01"])                    # powered off: left out (tpl02 too)
        self.assertEqual({t["label"]: t["value"] for t in d["summary"]}["Without vTPM"], 1)
        all_ok = inv.vm_inventory_doc(SEC[:1], READ, self.OPTS)
        s = self.section(all_ok, "VMs without encryption, EFI or vTPM")
        self.assertEqual((s["rows"], s["text"]), ([], "Every VM has VM Encryption, EFI firmware and a virtual TPM."))
        self.assertNotIn("status", {t["label"]: t for t in all_ok["summary"]}["Not encrypted"])

    def test_hardware_versions_and_tools(self):
        d = inv.vm_inventory_doc(SEC, READ, self.OPTS)
        s = self.section(d, "Hardware versions and Tools")
        self.assertEqual(s["rows"], [["Hardware version 21 - ESXi 8.0 U2 and later", 2, 2], ["Hardware version 19 - ESXi 7.0 U2 and later", 2, 1],
                                     ["Hardware version 15 - ESXi 6.7 U2 and later", 1, 1],
                                     ["VMware Tools not running", 1, 1], ["VMware Tools not installed", 1, 0],
                                     ["VMware Tools unsupported", 1, 1], ["VMware Tools upgrade available", 1, 1],
                                     ["VMware Tools current", 1, 1]])
        self.assertEqual([c[0] for c in s["cell_status"][3:]], ["warning", "", "warning", "info", "ok"])   # off, not installed: no chip
        fix = self.section(d, "VMware Tools to upgrade or not running")
        self.assertEqual([(r[0], r[1], r[2]) for r in fix["rows"]],                    # powered on only, the worst first
                         [("efi01", "not running", "12.3.5"), ("old01", "unsupported", "10.3.10"), ("bios01", "upgrade available", "11.2.5")])
        self.assertEqual(inv.vm_tools_version_text("2147483647"), "guest managed")
        self.assertEqual(inv.vm_tools_version_text(""), "")

    def test_the_website_columns(self):
        web = inv.vm_inventory_doc(SEC, READ, dict(self.OPTS, web=True, hidden_columns=["SB", "VMware Tools version"]))
        email = inv.vm_inventory_doc(SEC, READ, self.OPTS)
        t = folders(web)[0]
        self.assertEqual(t["columns"], ["VM", "Power", "ESXi host", "Cluster", "Guest OS", "IP address", "CPU", "GB",
                                        "Enc", "EFI", "SB", "vTPM", "HW", "Tools", "Tools ver."])
        self.assertEqual(t["column_titles"][6:], ["vCPUs", "Memory (GB)", "Encrypted", "Firmware (EFI or BIOS)", "Secure Boot",
                                                  "Virtual TPM", "Hardware version", "VMware Tools status", "VMware Tools version"])
        self.assertEqual(t["hidden_columns"], ["SB", "Tools ver."])                    # by header or full name
        self.assertTrue(t["compact"] and t["keep_case"])
        row = dict(zip(t["columns"], t["rows"][[r[0] for r in t["rows"]].index("bios01")]))
        self.assertEqual([row[k] for k in ("ESXi host", "Enc", "EFI", "SB", "vTPM", "HW", "Tools", "Tools ver.")],
                         ["esx01", "no", "BIOS", "off", "no", "15", "upgrade available", "11.2.5"])   # the host short
        self.assertEqual(folders(email)[0]["columns"], ["VM", "Power", "ESXi host", "Cluster", "Guest OS", "IP address", "vCPUs",
                                                        "Memory (GB)"])                # the email: the lean list
        self.assertEqual(self.section(web, "VMs without encryption, EFI or vTPM")["columns"][:5], ["VM", "Enc", "Firmware", "SB", "vTPM"])
        # security not read: no Enc / SB / vTPM, the firmware still (always read)
        plain = folders(inv.vm_inventory_doc(SEC, READ, {"web": True, "hidden_columns": "SB, Tools ver., cluster"}))[0]
        self.assertEqual(plain["columns"][8:], ["EFI", "HW", "Tools", "Tools ver."])
        self.assertEqual(plain["hidden_columns"], ["Cluster", "Tools ver."])           # a setting as text; any column; SB not there
        flat = folders(inv.vm_inventory_doc(SEC, READ, dict(self.OPTS, web=True, by_folder=False)))[0]
        self.assertEqual(flat["column_titles"][:3], ["vCenter", "Folder", "VM"])
        self.assertEqual(len(flat["cell_status"][0]), len(flat["columns"]))

    def test_the_workbook_columns(self):
        (s,) = inv.vm_inventory_xlsx(SEC, self.OPTS)
        row = dict(zip(s["columns"], s["rows"][[r[3] for r in s["rows"]].index("enc01")]))
        self.assertEqual([row[k] for k in ("Encrypted", "Key provider", "Encrypted vMotion", "Firmware", "Secure Boot", "vTPM",
                                           "Hardware version", "Compatible with", "VMware Tools", "Tools version", "Tools running")],
                         ["yes", "provider-a", "required", "EFI", "yes", "yes", "21", "ESXi 8.0 U2 and later", "current", "12.3.5", "yes"])
        old = dict(zip(s["columns"], s["rows"][[r[3] for r in s["rows"]].index("old01")]))
        self.assertEqual(old["vTPM"], "")                                              # not known: empty, not "no"


def read_back(path):
    z = zipfile.ZipFile(path)
    assert z.testzip() is None
    parts = {n: z.read(n).decode("utf-8") for n in z.namelist()}
    for n, text in parts.items():
        minidom.parseString(text.encode("utf-8"))                                     # every part is well-formed XML
    return parts


class Workbook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_sheets_cells_and_names(self):
        path = os.path.join(self.tmp, "w.xlsx")
        done = xl.write_xlsx(path, [
            {"name": "VMs", "columns": ["Name", "vCPUs", "Note"], "rows": [["a <&> b", 4, " lead"], ["bell\x07", 2.5, None], ["x", True, ""]]},
            {"name": "a/b:c*?[x] a sheet name longer than thirty-one", "columns": ["A"], "rows": [[1]]},
            {"name": "vms", "columns": [], "rows": []}])
        self.assertEqual(done, [["VMs", 3], ["a-b-c---x- a sheet name longer", 1], ["vms (2)", 0]])
        p = read_back(path)
        self.assertEqual(sorted(n for n in p if n.startswith("xl/worksheets/")),
                         ["xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml", "xl/worksheets/sheet3.xml"])
        s1 = p["xl/worksheets/sheet1.xml"]
        self.assertIn('<c r="A2" t="inlineStr"><is><t>a &lt;&amp;&gt; b</t></is></c>', s1)
        self.assertIn('<c r="B2"><v>4</v></c>', s1)
        self.assertIn('<c r="B3"><v>2.5</v></c>', s1)
        self.assertIn("<t>bell</t>", s1)                                                # a control character dropped
        self.assertIn('<t xml:space="preserve"> lead</t>', s1)
        self.assertIn('<t>yes</t>', s1)
        self.assertIn('<c r="A1" s="1" t="inlineStr">', s1)                             # the bold header
        self.assertIn('state="frozen"', s1)
        self.assertIn('<autoFilter ref="A1:C4"/>', s1)
        self.assertNotIn("C3", s1)                                                      # an empty cell is left out
        self.assertIn('_xlnm._FilterDatabase', p["xl/workbook.xml"])
        self.assertNotIn("autoFilter", p["xl/worksheets/sheet3.xml"])

    def test_columns(self):
        self.assertEqual([xl.col_name(i) for i in (0, 25, 26, 51, 52, 701, 702)], ["A", "Z", "AA", "AZ", "BA", "ZZ", "AAA"])

    def test_report_tables_as_sheets(self):
        rep = {"title": "T", "sections": [{"title": "Needs attention", "columns": ["Host"], "rows": [["web01"]]},
                                          {"title": "Notes", "lines": ["one"]}, {"title": "All", "columns": ["Host", "OK"], "rows": []}]}
        self.assertEqual([s["name"] for s in sf.report_xlsx_sheets(rep)], ["Needs attention", "All"])
        self.assertEqual(sf.report_xlsx_sheets({"title": "T", "sections": [{"title": "Notes", "lines": ["one", "two"]}]}),
                         [{"name": "Report", "columns": ["T"], "rows": [["Notes"], ["one"], ["two"]]}])
        path = os.path.join(self.tmp, "r.xlsx")
        xl.write_xlsx(path, sf.report_xlsx_sheets(rep))
        self.assertTrue(re.search(r'<sheet name="Needs attention" sheetId="1"', read_back(path)["xl/workbook.xml"]))


if __name__ == "__main__":
    unittest.main(verbosity=1)
