#!/usr/bin/env python3
"""The NetApp ONTAP hardware inventory (plugins/filter/ontap_hardware_filters.py): the report, the job's lines, the
workbook and the artifact, from what the collector reads of the fake cluster (tests/netapp/fixture.py).
    python tests/test_ontap_hardware.py"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))

import ontap_hardware_filters as hw  # noqa: E402

KEYS = {"cluster": "/api/cluster", "nodes": "/api/cluster/nodes", "shelves": "/api/storage/shelves", "disks": "/api/storage/disks",
        "ports": "/api/network/ethernet/ports", "fc_ports": "/api/network/fc/ports"}


def collected():
    """The collector's result for cluster1 (as the fake serves it), one cluster refused, one unreachable."""
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "c1.json")
        subprocess.check_call([sys.executable, os.path.join(HERE, "netapp", "fixture.py"), out])
        with open(out) as f:
            paths = json.load(f)["paths"]
    data = {}
    for k, p in KEYS.items():
        ep = paths[p]
        data[k] = {"records": [ep["single"]] if "single" in ep else ep["records"], "error": "", "reduced": "", "truncated": False}
    data["disks"]["reduced"] = "reduced detail - ONTAP refused a field (HTTP 400)"
    return {"read_at": "2026-11-03T06:00:00Z", "clusters": [
        {"address": "cluster1.example.mil", "reachable": True, "error": "", "data": data},
        {"address": "cluster2.example.mil", "reachable": False, "error": "the ONTAP account was refused (HTTP 401)", "data": {}},
        {"address": "cluster3.example.mil", "reachable": False, "error": "cannot resolve cluster3.example.mil (DNS)", "data": {}}]}


def sec(d, title):
    return next(s for s in d["sections"] if s["title"] == title)


class Report(unittest.TestCase):
    def setUp(self):
        self.c = collected()
        self.d = hw.ontap_hardware_doc(self.c)

    def test_title_status_summary(self):
        self.assertEqual(self.d["title"], "NetApp hardware: 2 controllers, 1 model, 1 shelf, 4 disks - 1 not up - 2 clusters not read")
        self.assertEqual(self.d["status"], "critical")
        s = {x["label"]: x["value"] for x in self.d["summary"]}
        self.assertEqual(s, {"Clusters": 1, "Controllers": 2, "Models": 1, "ONTAP versions": 2, "Shelves": 1, "Disks": 4,
                             "With a serial number": "2 of 2", "Not up": 1, "Shelves not ok": 1, "Clusters not read": 2})
        self.assertEqual([x["title"] for x in self.d["sections"]],
                         ["Clusters not read", "Controllers not up", "Models", "Controllers", "Shelves", "Disks by model", "Not all read"])
        self.assertIn("cluster2.example.mil: the ONTAP account was refused (HTTP 401)", sec(self.d, "Clusters not read")["lines"])

    def test_models_and_controllers(self):
        self.assertEqual(sec(self.d, "Models")["rows"],
                         [["AFF-A400", 2, "9.14.1P3 (1), 9.13.1P8 (1)", "11.10 (1), 11.9 (1)", "BMC 13.10 (1)"]])
        c = sec(self.d, "Controllers")
        row = dict(zip(c["columns"], c["rows"][0]))
        self.assertEqual((row["Controller"], row["Serial"], row["System ID"], row["ONTAP"], row["HA partner"], row["Memory"],
                          row["System firmware"], row["SP / BMC"], row["Flash Cache"], row["Ports"], row["Up"]),
                         ("cluster1-01", "SN0001", "0537000001", "9.14.1P3", "cluster1-02", "128.0 GB", "11.10", "BMC 13.10",
                          "X1970A 1.0 TB", "2x 25G, 3x down; 2x 32G FC", "1d 02:03"))
        self.assertEqual(row["CPU"], "Intel(R) Xeon(R) Silver 4210 (20 CPUs)")
        self.assertEqual(c["row_status"], ["", "warning"])
        n2 = dict(zip(c["columns"], c["rows"][1]))
        self.assertEqual((n2["State"], n2["NVRAM battery"], n2["SP / BMC"]), ("down", "near end of life", "-"))
        self.assertEqual(c["cell_status"][1][c["columns"].index("NVRAM battery")], "warning")
        self.assertEqual(c["cell_status"][0][c["columns"].index("NVRAM battery")], "")

    def test_shelves_and_disks(self):
        sh = sec(self.d, "Shelves")
        self.assertEqual(sh["rows"], [["cluster1", "1.1", "NS224", "SHFMS0001", "iom12f", "0220 (1), 0210 (1)", "nvme", 4, "error"]])
        self.assertEqual(sh["row_status"], ["warning"])
        self.assertEqual(sec(self.d, "Disks by model")["rows"], [["cluster1", "NETAPP", "X357", "SSD", "3.8 TB", 4, 1, "NA02 (2), NA01 (2)"]])
        self.assertEqual(sec(self.d, "Not all read")["lines"], ["cluster1: disks - reduced detail - ONTAP refused a field (HTTP 400)"])

    def test_a_cut_and_nothing_read(self):
        d = hw.ontap_hardware_doc(self.c, {"max_rows": 1})
        self.assertIn("1 more not listed (ontap_hardware_max_rows: 1)", sec(d, "Controllers")["text"])
        none = hw.ontap_hardware_doc({"clusters": [self.c["clusters"][1]]})
        self.assertEqual(none["title"], "NetApp hardware: 0 controllers, 0 models, 0 shelves, 0 disks - 1 cluster not read")
        self.assertIn("no cluster read", none["subtitle"])
        ok = dict(self.c, clusters=[self.c["clusters"][0]])
        for n in ok["clusters"][0]["data"]["nodes"]["records"]:
            n["state"] = "up"
        ok["clusters"][0]["data"]["shelves"]["records"][0]["state"] = "ok"
        self.assertEqual(hw.ontap_hardware_doc(ok)["status"], "ok")


class Output(unittest.TestCase):
    def test_lines_workbook_models(self):
        c = collected()
        lines = hw.ontap_hardware_lines(c)
        self.assertEqual(lines[:2], ["AFF-A400: 2 controller(s); ONTAP 9.14.1P3 (1), 9.13.1P8 (1); firmware 11.10 (1), 11.9 (1); "
                                     "SP / BMC BMC 13.10 (1)", "cluster1: 2 controller(s), 1 shelf, 4 disk(s)"])
        self.assertIn("NOT READ - cluster3.example.mil: cannot resolve cluster3.example.mil (DNS)", lines)
        self.assertIn("Not all read - cluster1: disks - reduced detail - ONTAP refused a field (HTTP 400)", lines)
        book = {s["name"]: s for s in hw.ontap_hardware_xlsx(c)}
        self.assertEqual(list(book), ["Clusters", "Controllers", "Shelves", "Shelf modules", "Disks", "Ethernet ports", "FC ports"])
        self.assertEqual([r[-1] for r in book["Clusters"]["rows"]],
                         ["yes", "NO: the ONTAP account was refused (HTTP 401)", "NO: cannot resolve cluster3.example.mil (DNS)"])
        disk = dict(zip(book["Disks"]["columns"], book["Disks"]["rows"][0]))
        self.assertEqual((disk["Disk"], disk["Shelf"], disk["Bay"], disk["Vendor"], disk["Firmware"], disk["Size"], disk["Container"]),
                         ("1.0.3", "1.1", 3, "NETAPP", "NA01", "3.8 TB", "broken"))
        self.assertEqual(len(book["Shelf modules"]["rows"]), 2)
        self.assertEqual(len(book["Ethernet ports"]["rows"]), 6)
        ctl = dict(zip(book["Controllers"]["columns"], book["Controllers"]["rows"][0]))
        self.assertEqual((ctl["CPUs"], ctl["SP / BMC address"], ctl["SP / BMC firmware"]), (20, "192.0.2.51", "13.10"))
        self.assertEqual(hw.ontap_hardware_models(c), [{"model": "AFF-A400", "controllers": 2, "ontap": "9.14.1P3 (1), 9.13.1P8 (1)",
                                                        "firmware": "11.10 (1), 11.9 (1)"}])


if __name__ == "__main__":
    unittest.main(verbosity=1)
