#!/usr/bin/env python3
"""The VM inventory report (plugins/filter/vmware_inventory_filters.py), the Excel workbook every report's email may
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
                         ["vc01 - DC1 (no folder)", "vc01 - DC1 / Linux", "vc01 - DC1 / Linux / DB", "vc02 - DC2 / Apps"])
        linux = d["sections"][1]
        self.assertEqual(linux["columns"], ["VM", "Power", "ESXi host", "Cluster", "Guest OS", "IP address", "vCPUs", "Memory (GB)"])
        self.assertEqual([r[0] for r in linux["rows"]], ["web01", "web02"])            # by name
        self.assertEqual(linux["rows"][0][2], "esx02.example.mil")                     # the host it runs on
        self.assertEqual(linux["rows"][0][7], "6")
        self.assertEqual(d["sections"][2]["rows"][0][1:6:4], ["off", "-"])             # no IP: a dash

    def test_templates_flat_and_cut(self):
        d = inv.vm_inventory_doc(VMS, READ, {"templates": True, "by_folder": False, "max_rows": 3})
        self.assertEqual(d["title"], "VM inventory: 6 VMs in 2 vCenters")
        self.assertEqual(len(d["sections"]), 1)
        self.assertEqual(d["sections"][0]["columns"][:3], ["vCenter", "Folder", "VM"])
        self.assertEqual(len(d["sections"][0]["rows"]), 3)
        self.assertIn("(template)", " ".join(str(c) for r in inv.vm_inventory_doc(VMS, READ, {"templates": True})["sections"][3]["rows"]
                                              for c in r))
        cut = inv.vm_inventory_doc(VMS, READ, {"max_rows": 2})
        self.assertEqual(sum(len(s.get("rows") or []) for s in cut["sections"]), 2)
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
