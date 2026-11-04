#!/usr/bin/env python3
"""The Cisco UCS inventory (plugins/filter/ucs_inventory_filters.py, roles/ucs_report/library/site_ucs_collect.py): the
report, the job's lines, the workbook and the artifact from what the collector reads of the fake domain
(tests/ucs/fixture.py); the collector's XML (escaped, read methods only), its answers and its login hint.
    python tests/test_ucs_inventory.py"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))

import ucs_inventory_filters as ucs  # noqa: E402


def fixture():
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "d1.json")
        subprocess.check_call([sys.executable, os.path.join(HERE, "ucs", "fixture.py"), out])
        with open(out) as f:
            return json.load(f)


def collected():
    """The collector's result for the fake domain (as it serves it), one domain refused, one unreachable."""
    fx = fixture()
    data = dict((k, {"objects": [dict(o, _class=k) for o in v], "error": "", "truncated": False}) for k, v in fx["classes"].items())
    data["equipmentFex"] = {"objects": [], "error": "error 103: class equipmentFex is not a valid class", "truncated": False}
    return {"read_at": "2026-11-03T06:00:00Z", "domains": [
        {"address": "ucs01.example.mil", "reachable": True, "error": "", "version": "4.2(3d)", "priv": "read-only", "data": data},
        {"address": "ucs02.example.mil", "reachable": False, "error": "UCS Manager refused the login (error 551: Authentication failed)",
         "data": {}},
        {"address": "ucs03.example.mil", "reachable": False, "error": "timed out - no answer within 60 s", "data": {}}]}


def sec(d, title):
    return next(s for s in d["sections"] if s["title"] == title)


def rows(s):
    return [dict(zip(s["columns"], r)) for r in s["rows"]]


class Report(unittest.TestCase):
    def setUp(self):
        self.c = collected()
        self.d = ucs.ucs_inventory_doc(self.c)

    def test_title_status_summary(self):
        self.assertEqual(self.d["title"], "Cisco UCS: 4 servers, 3 models, 1 domain, 2 fabric interconnects - 2 not operable - "
                                          "1 critical fault - 2 domains not read")
        self.assertEqual(self.d["status"], "critical")
        s = dict((x["label"], x["value"]) for x in self.d["summary"])
        self.assertEqual(s, {"Domains": 1, "Fabric interconnects": 2, "Chassis": 1, "Servers": 4, "Models": 3, "BIOS versions": 4,
                             "With a serial number": "4 of 4", "Not operable": 2, "Critical faults": 1, "Domains not read": 2})
        self.assertEqual([x["title"] for x in self.d["sections"]],
                         ["Domains not read", "Not operable", "Domains", "Fabric interconnects", "Chassis", "Server models",
                          "Servers", "Firmware in use", "Faults (critical, major)", "Not all read"])
        self.assertIn("ucs02.example.mil: UCS Manager refused the login (error 551: Authentication failed)",
                      sec(self.d, "Domains not read")["lines"])

    def test_domain_and_fis(self):
        dom = rows(sec(self.d, "Domains"))[0]
        self.assertEqual((dom["Domain"], dom["UCS Manager"], dom["HA"], dom["Fabric interconnects"], dom["Servers"],
                          dom["Service profiles"], dom["Critical faults"], dom["Major faults"], dom["The account's privileges"]),
                         ("UCS-DC1", "4.2(3d)", "ready", "2x UCS-FI-6454", 4, 2, 1, 1, "read-only"))
        a, b = rows(sec(self.d, "Fabric interconnects"))
        self.assertEqual((a["FI"], a["Serial"], a["Role"], a["Management IP"], a["System firmware"], a["Kernel"], a["Memory GB"]),
                         ("A", "FDO0000000A", "primary", "192.0.2.11", "9.3(5)I42(3d)", "9.3(5)I42(3d)", 64))
        self.assertEqual(b["Role"], "subordinate")

    def test_chassis_and_not_operable(self):
        ch = rows(sec(self.d, "Chassis"))[0]
        self.assertEqual((ch["IO modules (firmware)"], ch["Blades"], ch["Power supplies"]),
                         ("A: UCS-IOM-2408 (4.2(3d)), B: UCS-IOM-2408 (4.2(3d))", 3, "4 (1 not operable)"))
        self.assertEqual(sec(self.d, "Not operable")["rows"],
                         [["UCS-DC1", "chassis-1/blade-3", "blade", "UCSB-B200-M6", "FLM0000003", "degraded"],
                          ["UCS-DC1", "chassis-1/psu-4", "power supply", "UCSB-PSU-2500ACDV", "DTM0000004", "inoperable"]])

    def test_models_servers_firmware(self):
        self.assertEqual(sec(self.d, "Server models")["rows"][0],
                         ["UCSB-B200-M5", 2, "B200M5.4.2.3b.0.0613230245 (1), B200M5.4.2.1c.0.0405220734 (1)",
                          "4.2(3d) (1), 4.2(1f) (1)", "UCSB-MLOM-40G-04 5.2(3a) (1), UCSB-MLOM-40G-04 5.2(1b) (1)"])
        s = sec(self.d, "Servers")
        b1 = rows(s)[0]
        self.assertEqual((b1["Server"], b1["Service profile"], b1["CPU"], b1["Memory GB"], b1["BIOS"], b1["CIMC"],
                          b1["Adapters (firmware)"], b1["Storage controllers"], b1["State"]),
                         ("chassis-1/blade-1", "ESX01", "2 x Intel(R) Xeon(R) Gold 6248 CPU @ 2.50GHz, 40 cores", 384,
                          "B200M5.4.2.3b.0.0613230245", "4.2(3d)", "UCSB-MLOM-40G-04 (5.2(3a))",
                          "Cisco 12G Modular RAID controller with 2GB cache (51.10.0-3612)", "ok"))
        self.assertEqual(s["row_status"], ["", "", "warning", ""])
        self.assertEqual(rows(s)[3]["Server"], "rack-unit-1")
        fw = dict(((r[0], r[1]), r[3]) for r in sec(self.d, "Firmware in use")["rows"])
        self.assertEqual(fw[("UCS Manager", "-")], "4.2(3d) (1)")
        self.assertEqual(fw[("CIMC", "UCSB-B200-M5")], "4.2(3d) (1), 4.2(1f) (1)")
        self.assertEqual(fw[("Adapter", "UCSB-MLOM-40G-04")], "5.2(3a) (2), 5.2(1b) (1)")   # the system image, not the boot loader
        self.assertEqual(fw[("Storage controller", "Cisco 12G Modular RAID controller with 2GB cache")], "51.10.0-3612 (1)")
        self.assertEqual(fw[("Board controller", "UCSB-B200-M5")], "14.0 (2)")
        self.assertEqual([r[0] for r in sec(self.d, "Firmware in use")["rows"]][:4], ["UCS Manager", "FI system", "FI kernel", "IO module"])

    def test_faults_and_notes(self):
        f = sec(self.d, "Faults (critical, major)")
        self.assertEqual([r[1:4] for r in f["rows"]], [["critical", "F0369", "chassis-1/psu-4"], ["major", "F0283", "chassis-1/blade-3"]])
        self.assertEqual(f["row_status"], ["warning", ""])
        self.assertEqual(sec(self.d, "Not all read")["lines"],
                         ["UCS-DC1: could not read equipmentFex - error 103: class equipmentFex is not a valid class"])
        none = ucs.ucs_inventory_doc(self.c, {"severities": []})
        self.assertFalse([s for s in none["sections"] if s["title"].startswith("Faults")])

    def test_faults_newest_first(self):
        """The newest Since first whatever the severity, one without a time last; the cut keeps the newest; the
        workbook's Faults sheet in the same order."""
        c = collected()
        data = c["domains"][0]["data"]
        for f in data["faultInst"]["objects"]:
            f["created"] = {"F0369": "2026-10-20T08:00:00.000", "F0283": "2026-11-02T09:30:00.000"}.get(f["code"], f["created"])
        data["faultInst"]["objects"].append({"dn": "sys/chassis-1/blade-1/fault-F0999", "code": "F0999", "severity": "major",
                                             "ack": "no", "descr": "no time", "cause": "x", "created": "", "occur": "1"})
        f = sec(ucs.ucs_inventory_doc(c), "Faults (critical, major)")
        self.assertEqual([r[2] for r in f["rows"]], ["F0283", "F0369", "F0999"])
        self.assertEqual([r[5] for r in f["rows"]], ["2026-11-02 09:30:00", "2026-10-20 08:00:00", ""])
        self.assertEqual(f["row_status"], ["", "warning", ""])
        self.assertIn("the newest first", f["text"])
        cut = sec(ucs.ucs_inventory_doc(c, {"max_rows": 1}), "Faults (critical, major)")
        self.assertEqual([r[2] for r in cut["rows"]], ["F0283"])
        book = {s["name"]: s for s in ucs.ucs_inventory_xlsx(c)}
        self.assertEqual([r[2] for r in book["Faults"]["rows"]], ["F0283", "F0727", "F0369", "F0999"])   # minor too

    def test_healthy_is_green_and_ha_not_ready_is_amber(self):
        c = collected()
        c["domains"] = c["domains"][:1]
        data = c["domains"][0]["data"]
        for k in data:
            for o in data[k]["objects"]:
                if o.get("operability") in ("degraded", "inoperable"):
                    o["operability"] = "operable"
        data["faultInst"]["objects"] = [x for x in data["faultInst"]["objects"] if x["severity"] != "critical"]
        self.assertEqual(ucs.ucs_inventory_doc(c)["status"], "ok")
        data["mgmtEntity"]["objects"][1].update(haReady="no", haFailureReason="chassis 1 not reachable from B")
        d = ucs.ucs_inventory_doc(c)
        self.assertEqual(d["status"], "warning")
        self.assertEqual(sec(d, "Not operable")["rows"][0][-1], "not ready: chassis 1 not reachable from B")

    def test_missing_attributes_never_crash(self):
        c = {"domains": [{"address": "ucs09.example.mil", "reachable": True, "data": {
            "computeBlade": {"objects": [{"dn": "sys/chassis-1/blade-1"}]}, "firmwareRunning": {"objects": [{"dn": "x"}, {}]},
            "networkElement": {"objects": [{}]}, "equipmentChassis": {"objects": [{"dn": "sys/chassis-1"}]}}}]}
        d = ucs.ucs_inventory_doc(c)
        self.assertEqual(rows(sec(d, "Servers"))[0]["Model"], "-")
        self.assertEqual(d["title"], "Cisco UCS: 1 server, 1 model, 1 domain, 1 fabric interconnect")
        ucs.ucs_inventory_xlsx(c)
        ucs.ucs_inventory_lines(c)
        self.assertEqual(ucs.ucs_inventory_doc({"domains": []})["subtitle"],
                         "Every fabric interconnect, chassis and server, as UCS Manager reports them - no domain read")


class Output(unittest.TestCase):
    def test_lines_workbook_models_raw(self):
        c = collected()
        lines = ucs.ucs_inventory_lines(c)
        self.assertEqual(lines[0], "UCSB-B200-M5: 2 server(s); BIOS B200M5.4.2.3b.0.0613230245 (1), B200M5.4.2.1c.0.0405220734 (1); "
                                   "CIMC 4.2(3d) (1), 4.2(1f) (1)")
        self.assertIn("UCS-DC1: UCS Manager 4.2(3d); 2x UCS-FI-6454; 1 chassis, 4 server(s)", lines)
        self.assertIn("NOT READ - ucs03.example.mil: timed out - no answer within 60 s", lines)
        book = dict((s["name"], s) for s in ucs.ucs_inventory_xlsx(c))
        self.assertEqual(list(book), ["Domains", "Fabric interconnects", "Chassis", "IO modules", "Servers", "Adapters", "CPUs",
                                      "Memory", "Storage controllers", "Power supplies", "Firmware", "Service profiles", "Faults"])
        self.assertEqual([r[-1] for r in book["Domains"]["rows"]],
                         ["yes", "NO: UCS Manager refused the login (error 551: Authentication failed)",
                          "NO: timed out - no answer within 60 s"])
        self.assertEqual(len(book["Memory"]["rows"]), 8)                      # the empty slots left out
        self.assertEqual(len(book["Power supplies"]["rows"]), 6)              # the missing one left out
        self.assertEqual(len(book["Firmware"]["rows"]), len(fixture()["classes"]["firmwareRunning"]))
        self.assertEqual(len(book["Faults"]["rows"]), 3)                      # not the cleared one
        mem = rows(book["Memory"])[0]
        self.assertEqual((mem["Server"], mem["DIMM"], mem["Size GB"]), ("chassis-1/blade-1", "DIMM_A1", 32))
        sp = rows(book["Service profiles"])[0]
        self.assertEqual((sp["Service profile"], sp["Template"], sp["Server"]), ("ESX01", "ESX-TEMPLATE", "chassis-1/blade-1"))
        srv = rows(book["Servers"])[0]
        self.assertEqual((srv["DIMMs"], srv["Board controller"], srv["Threads"]), ("2x 32 GB", "14.0", 80))
        self.assertEqual(ucs.ucs_inventory_models(c)[0], {"model": "UCSB-B200-M5", "servers": 2,
                                                          "bios": "B200M5.4.2.3b.0.0613230245 (1), B200M5.4.2.1c.0.0405220734 (1)",
                                                          "cimc": "4.2(3d) (1), 4.2(1f) (1)"})
        raw = ucs.ucs_inventory_raw(c)
        self.assertEqual(raw["ucs01.example.mil"]["computeBlade"]["count"], 3)
        self.assertEqual(len(raw["ucs01.example.mil"]["firmwareRunning"]["first"]), 3)


def collector():
    """roles/ucs_report/library/site_ucs_collect.py with stand-ins for ansible.module_utils (not needed to test it)."""
    sys.modules.setdefault("ansible", types.ModuleType("ansible"))
    mu = sys.modules.setdefault("ansible.module_utils", types.ModuleType("ansible.module_utils"))
    basic = types.ModuleType("ansible.module_utils.basic")
    basic.AnsibleModule = object
    sys.modules["ansible.module_utils.basic"] = basic
    import urllib.error
    import urllib.request
    for name, mod in (("six", None), ("six.moves", None), ("six.moves.urllib", None), ("six.moves.urllib.error", urllib.error),
                      ("six.moves.urllib.request", urllib.request)):
        sys.modules["ansible.module_utils." + name] = mod or types.ModuleType("ansible.module_utils." + name)
    mu.six = sys.modules["ansible.module_utils.six"]
    import importlib.util
    spec = importlib.util.spec_from_file_location("site_ucs_collect", os.path.join(REPO, "roles", "ucs_report", "library",
                                                                                   "site_ucs_collect.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class FakeOpener(object):
    """Answers the collector's posts from a list; keeps what was sent."""

    def __init__(self, answers):
        self.answers, self.sent = list(answers), []

    def open(self, req, timeout=None):
        self.sent.append(ET.fromstring(req.data))
        a = self.answers.pop(0)
        return types.SimpleNamespace(read=lambda: a.encode())


class Collector(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = collector()

    def client(self, answers):
        op = FakeOpener(answers)
        return self.m.Client("ucs01.example.mil", "svc_ro", 'p&"<w>', None, 5, opener=op), op

    def test_only_read_methods(self):
        self.assertIn(b'inPassword="p&amp;&quot;&lt;w&gt;"', self.m.element("aaaLogin", {"inName": "u", "inPassword": 'p&"<w>'}))
        for bad in ("configConfMo", "configConfMos", "lsInstantiateTemplate", "aaaChangeSelfPassword"):
            with self.assertRaises(ValueError):
                self.m.element(bad, {})

    def test_collect_logs_in_reads_logs_out(self):
        c, op = self.client([
            '<aaaLogin response="yes" outCookie="c1" outVersion="4.2(3d)" outPriv="read-only" />',
            '<configResolveClass response="yes" classId="computeBlade"><outConfigs><computeBlade dn="sys/chassis-1/blade-1" '
            'model="UCSB-B200-M5" /><computeBlade dn="sys/chassis-1/blade-2" /></outConfigs></configResolveClass>',
            '<configResolveClass response="yes" errorCode="103" errorDescr="no such class" />',
            '<aaaLogout response="yes" outStatus="success" />'])
        r = self.m.collect(c, [{"class": "computeBlade", "max": 1}, {"class": "nope"}])
        self.assertTrue(r["reachable"])
        self.assertEqual((r["version"], r["priv"]), ("4.2(3d)", "read-only"))
        self.assertEqual(r["data"]["computeBlade"]["objects"], [{"dn": "sys/chassis-1/blade-1", "model": "UCSB-B200-M5",
                                                                 "_class": "computeBlade"}])
        self.assertTrue(r["data"]["computeBlade"]["truncated"])
        self.assertEqual(r["data"]["nope"]["error"], "error 103: no such class")
        self.assertEqual([e.tag for e in op.sent], ["aaaLogin", "configResolveClass", "configResolveClass", "aaaLogout"])
        self.assertEqual(op.sent[1].get("cookie"), "c1")
        self.assertEqual(op.sent[3].get("inCookie"), "c1")

    def test_refused_login(self):
        c, op = self.client(['<aaaLogin response="yes" errorCode="551" errorDescr="Authentication failed" />'])
        r = self.m.collect(c, [{"class": "computeBlade"}])
        self.assertFalse(r["reachable"])
        self.assertIn("UCS Manager refused the login (error 551: Authentication failed)", r["error"])
        self.assertIn("ucs-DOMAIN\\user", r["error"])
        self.assertEqual([e.tag for e in op.sent], ["aaaLogin"])            # no session: nothing to log out

    def test_not_xml_and_logout_after_failure(self):
        c, op = self.client(['<aaaLogin response="yes" outCookie="c1" />', "<html>a login page",
                             '<aaaLogout response="yes" outStatus="success" />'])
        r = self.m.collect(c, [{"class": "computeBlade"}])
        self.assertFalse(r["reachable"])
        self.assertIn("is not XML", r["error"])
        self.assertEqual(op.sent[-1].tag, "aaaLogout")                     # an open session is always logged out

    def test_explain(self):
        self.assertIn("ucs_ca_path", self.m.explain("[SSL: CERTIFICATE_VERIFY_FAILED] x", "tls", "ucs01.example.mil", 60))
        self.assertIn("SSH on port 22 is not used", self.m.explain("timed out", "timeout", "ucs01.example.mil", 60))


if __name__ == "__main__":
    unittest.main(verbosity=1)
