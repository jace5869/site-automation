#!/usr/bin/env python3
"""The ESXi network check: the pure parts of roles/vmware_vm/library/site_vmware_hostnet.py (which pings, esxcli's answer,
a fault in words) and its report (plugins/filter/vmware_hostnet_filters.py). Needs ansible-core importable, not pyVmomi.
    python tests/test_esxi_network.py"""
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

import site_vmware_hostnet as hn  # noqa: E402
import vmware_hostnet_filters as rf  # noqa: E402

# esxcli network diag ping's answer, as an ESXi host sends it through ExecuteSoap (6.5 and later: microseconds, and *MS)
ANSWER = """<obj xmlns:vim25="urn:vim25" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns="urn:vim25"
 versionId="5.0" xsi:type="VimEsxCLInetworkdiagpingResult"><Summary><Duplicated>0</Duplicated><HostAddr>192.0.2.12</HostAddr>
<PacketLost>0</PacketLost><Recieved>3</Recieved><RoundtripAvg>212</RoundtripAvg><RoundtripAvgMS>0</RoundtripAvgMS>
<RoundtripMax>250</RoundtripMax><RoundtripMaxMS>0</RoundtripMaxMS><RoundtripMin>180</RoundtripMin><RoundtripMinMS>0</RoundtripMinMS>
<Transmitted>3</Transmitted></Summary><Trace><Detail></Detail><Dup>false</Dup><Host>192.0.2.12</Host><ICMPSeq>0</ICMPSeq>
<ReceivedBytes>8980</ReceivedBytes><RoundtripTime>180</RoundtripTime><TTL>64</TTL></Trace></obj>"""


def host(name, cluster="CL1", state="ok", vmks=None):
    return {"name": name, "cluster": cluster, "datacenter": "DC1", "state": state, "vmks": vmks or []}


def vmk(dev, ip, services, mtu=9000, netstack="vmotion", switch="DSwitch", switch_mtu=9000):
    return {"device": dev, "ip": ip, "mask": "255.255.255.0", "mtu": mtu, "netstack": netstack, "portgroup": "pg", "switch": switch,
            "switch_mtu": switch_mtu, "services": services}


class Module(unittest.TestCase):
    def test_services_of_each_vmk(self):
        self.assertEqual(hn.vnic_services([("vmotion", ["vmotion.key-vim.host.VirtualNic-vmk1"]),
                                           ("management", ["management.key-vim.host.VirtualNic-vmk0"]),
                                           ("vsan", ["vsan.key-vim.host.VirtualNic-vmk1"]), ("faultToleranceLogging", [])]),
                         {"vmk1": ["vmotion", "vsan"], "vmk0": ["management"]})

    def test_ping_size(self):
        self.assertEqual([hn.ping_size(x) for x in (9000, 1500, None, 60)], [8972, 1472, 1472, 56])

    def test_plan(self):
        hosts = [host("esx01", vmks=[vmk("vmk0", "198.51.100.1", ["management"], 1500, "defaultTcpipStack"),
                                     vmk("vmk1", "192.0.2.11", ["vmotion"])]),
                 host("esx02", vmks=[vmk("vmk1", "192.0.2.12", ["vmotion"], 1500)]),
                 host("esx03", state="in maintenance mode", vmks=[vmk("vmk1", "192.0.2.13", ["vmotion"])]),
                 host("esx09", cluster="", vmks=[vmk("vmk1", "192.0.2.19", ["vmotion"])])]
        pings, cut = hn.plan(hosts, ["vmotion"], [{"name": "NFS", "host": "203.0.113.5"}], 100)
        self.assertEqual(cut, 0)
        between = [(p["from"], p["to"], p["size"], p["df"], p["netstack"]) for p in pings if p["service"] == "vmotion"]
        self.assertEqual(between, [("esx01", "esx02", 8972, True, "vmotion"), ("esx02", "esx01", 1472, True, "vmotion")])
        targets = [(p["from"], p["vmk"], p["size"], p["df"]) for p in pings if p["service"] == "target"]
        self.assertEqual(targets, [("esx01", "vmk0", 56, False), ("esx02", "vmk1", 56, False), ("esx09", "vmk1", 56, False)])
        self.assertEqual(hn.plan(hosts, ["vmotion"], [], 1), ([dict(pings[0])], 1))

    def test_answer(self):
        self.assertEqual(hn.parse_ping(ANSWER), {"sent": 3, "received": 3, "loss_pct": 0, "rtt_min_ms": 0.18, "rtt_avg_ms": 0.212,
                                                 "rtt_max_ms": 0.25})
        old = ("<obj><Summary><Transmitted>3</Transmitted><Received>2</Received><RoundtripAvg>4</RoundtripAvg>"
               "<RoundtripMin>3</RoundtripMin><RoundtripMax>5</RoundtripMax></Summary></obj>")     # before 6.5: milliseconds
        self.assertEqual(hn.parse_ping(old), {"sent": 3, "received": 2, "loss_pct": 33, "rtt_min_ms": 3.0, "rtt_avg_ms": 4.0,
                                              "rtt_max_ms": 5.0})
        none = "<obj><Summary><Transmitted>3</Transmitted><Recieved>0</Recieved><PacketLost>100</PacketLost><RoundtripAvg>0</RoundtripAvg></Summary></obj>"
        self.assertEqual(hn.parse_ping(none)["rtt_avg_ms"], None)
        with self.assertRaises(ValueError):
            hn.parse_ping("<obj/>")

    def test_faults_in_words(self):
        soap = ('<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"><soapenv:Body><soapenv:Fault>'
                '<faultcode>ServerFaultCode</faultcode><faultstring>Permission to perform this operation was denied.</faultstring>'
                '<detail><NoPermissionFault xmlns="urn:vim25" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
                'xsi:type="NoPermission"><object type="HostSystem">host-21</object><privilegeId>Host.Cim.CimInteraction</privilegeId>'
                '</NoPermissionFault></detail></soapenv:Fault></soapenv:Body></soapenv:Envelope>')
        self.assertIn("lacks the privilege Host.Cim.CimInteraction", hn.soap_fault(soap))
        detail = ('<LocalizedMethodFault><fault xmlns:_XMLSchema-instance="http://www.w3.org/2001/XMLSchema-instance" '
                  '_XMLSchema-instance:type="NotFound"></fault><localizedMessage>The object or item referred to could not be found.'
                  '</localizedMessage></LocalizedMethodFault>')
        self.assertEqual(hn.soap_fault(detail), "NotFound: The object or item referred to could not be found.")
        self.assertEqual(hn.soap_fault("not xml <"), "not xml <")

    def test_name_and_datastore_id(self):
        self.assertTrue(hn.name_matches("esx01.example.mil", ["esx0*"]))
        self.assertTrue(hn.name_matches("esx01.example.mil", ["ESX01"]))
        self.assertEqual(hn._ds_id("ds:///vmfs/volumes/5f1e2a3b-11aa22bb-33cc-001122334455/"), "5f1e2a3b-11aa22bb-33cc-001122334455")


def ping(frm, to, got=3, df=True, small=None, avg=0.2, error="", svc="vmotion", size=8972):
    return {"cluster": "CL1", "service": svc, "from": frm, "vmk": "vmk1", "netstack": "vmotion", "to": to, "to_ip": "192.0.2.1",
            "size": size, "df": df, "sent": 3, "received": got, "loss_pct": round(100 * (3 - got) / 3.0),
            "rtt_avg_ms": avg if got else None, "rtt_max_ms": avg if got else None, "error": error, "small": small}


def read(hosts, pings=(), latency=(), note=""):
    return {"vcenters": [{"name": "vc01.example.mil", "ok": True, "error": ""}],
            "read": [{"vcenter": "vc01.example.mil", "hosts": hosts, "pings": list(pings), "latency": list(latency), "cut": 0,
                      "note": note}]}


H3 = [host(n, vmks=[vmk("vmk1", "192.0.2.1%d" % i, ["vmotion"])]) for i, n in enumerate(("esx01", "esx02", "esx03"))]


class Report(unittest.TestCase):
    def problems(self, doc):
        sec = [s for s in doc["sections"] if s["title"] in ("Needs attention", "Notes")]
        return [(r[0], r[1], r[2]) for r in sec[0]["rows"]] if sec else []

    def test_all_through(self):
        pings = [ping(a["name"], b["name"]) for a in H3 for b in H3 if a is not b]
        doc = rf.esxi_net_doc(read(H3, pings))
        self.assertEqual(doc["status"], "ok")
        self.assertEqual(doc["title"], "ESXi network check: all 6 pings through")
        self.assertEqual(self.problems(doc), [])

    def test_one_host_with_a_smaller_mtu_on_its_path_is_one_row(self):
        small = {"sent": 3, "received": 3, "loss_pct": 0, "rtt_avg_ms": 0.2, "error": ""}
        pings = [ping(a["name"], b["name"], 0, small=small) if "esx03" in (a["name"], b["name"]) else ping(a["name"], b["name"])
                 for a in H3 for b in H3 if a is not b]
        doc = rf.esxi_net_doc(read(H3, pings))
        self.assertEqual(doc["status"], "critical")
        (p,) = self.problems(doc)
        self.assertEqual(p[:2], ("CRITICAL", "esx03"))
        self.assertIn("to and from it fail with the other hosts of CL1 (esx01, esx02; 4 pings)", p[2])
        self.assertIn("an MTU smaller", p[2])
        pings_sec = [s for s in doc["sections"] if s["title"].startswith("vMotion pings")][0]
        self.assertEqual(pings_sec["row_status"][:4], ["critical"] * 4)                 # the worst first

    def test_no_answer_slow_lost_and_could_not(self):
        pings = [ping("esx01", "esx02", 0), ping("esx02", "esx01", 2), ping("esx01", "esx03", avg=7.5), ping("esx03", "esx01", avg=200),
                 ping("esx02", "esx03", error="the vCenter account lacks the privilege Host.Cim.CimInteraction on this host")]
        rows = self.problems(rf.esxi_net_doc(read(H3, pings)))
        text = " | ".join(r[2] for r in rows)
        self.assertIn("no answer", text)
        self.assertIn("1 of 3 pings lost", text)
        self.assertIn("round trip 7.5 ms", text)
        self.assertIn("round trip 200 ms (from 150 ms is too slow)", text)
        self.assertIn("could not ping", text)
        self.assertEqual([r[0] for r in rows], ["CRITICAL", "CRITICAL", "WARNING", "WARNING", "WARNING"])

    def test_configuration(self):
        hosts = [host("esx01", vmks=[vmk("vmk1", "192.0.2.11", ["vmotion"])]),
                 host("esx02", vmks=[vmk("vmk1", "192.0.2.12", ["vmotion"], 1500)]),
                 host("esx03", vmks=[vmk("vmk1", "198.51.100.13", [], 9000, switch="vSwitch1", switch_mtu=1500)]),
                 host("esx11", cluster="CL2", vmks=[vmk("vmk0", "192.0.2.21", ["management"])]),
                 host("esx12", cluster="CL2", vmks=[vmk("vmk0", "192.0.2.22", ["management"])]),
                 host("esx13", cluster="CL2", state="in maintenance mode")]
        doc = rf.esxi_net_doc(read(hosts))
        text = " | ".join("%s %s %s" % r for r in self.problems(doc))
        self.assertIn("cluster CL2 no host has a VMkernel adapter with vMotion on", text)
        self.assertIn("esx03 no VMkernel adapter with vMotion on, while 2 other host(s) of cluster CL1 have one", text)
        self.assertIn("the vMotion adapters are on different MTUs: 1500 on esx02 vmk1; 9000 on esx01 vmk1", text)
        self.assertIn("esx03 vmk1 has MTU 9000, its switch vSwitch1 only 1500", text)
        self.assertIn("Hosts not checked", [s["title"] for s in doc["sections"]])
        none = [s for s in doc["sections"] if s["title"].startswith("vMotion pings")][0]
        self.assertIn("None: no two connected hosts", none["lines"][0])

    def test_storage_latency(self):
        lat = [{"host": h, "datastore": "DS2", "read_avg_ms": r, "read_max_ms": 80, "write_avg_ms": 10, "write_max_ms": 30}
               for h, r in (("esx01", 25), ("esx02", 60))]
        lat += [{"host": "esx03", "datastore": "DS3", "read_avg_ms": 22, "read_max_ms": 40, "write_avg_ms": 3, "write_max_ms": 9},
                {"host": "esx01", "datastore": "DS1", "read_avg_ms": 1, "read_max_ms": 4, "write_avg_ms": 2, "write_max_ms": 5}]
        doc = rf.esxi_net_doc(read(H3, latency=lat))
        rows = self.problems(doc)
        self.assertIn(("CRITICAL", "DS2, from 2 host(s)", "storage latency 25 to 60 ms average (esx01, esx02)"), rows)
        self.assertIn(("WARNING", "esx03 / DS3", "storage latency 22 ms average (read 22, write 3; at most 40 / 9)"), rows)
        self.assertEqual({s["label"]: s["value"] for s in doc["summary"]}["Slow datastores"], 2)
        lsec = [s for s in doc["sections"] if s["title"].startswith("Storage latency")][0]
        self.assertEqual([r[2] for r in lsec["rows"]], ["DS2", "DS2", "DS3", "DS1"])    # the worst first

    def test_no_privilege_said_once(self):
        doc = rf.esxi_net_doc(read(H3, note="no ping ran: the vCenter account lacks the privilege Host.Cim.CimInteraction"))
        (p,) = [r for r in self.problems(doc)]
        self.assertEqual(p[1], "vCenter vc01")
        sec = [s for s in doc["sections"] if s["title"] in ("Needs attention",)][0]
        self.assertIn("CIM interaction", sec["rows"][0][-1])


if __name__ == "__main__":
    unittest.main(verbosity=1)
