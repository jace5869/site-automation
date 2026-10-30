#!/usr/bin/env python3
"""Unit tests for plugins/filter/vmware_watch_filters.py, the VM outage watch (standard library only):
python3 tests/test_vm_watch.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "filter"))
import vmware_watch_filters as w  # noqa: E402

VC = "vc01.example.mil"


def host(name, **kw):
    h = {"name": name, "moid": "host-1", "cluster": "CL1", "datacenter": "DC1", "vcenter": VC, "connection": "connected",
         "maintenance": False, "standby": "none", "ha_state": "connectedToMaster"}
    h.update(kw)
    return h


def suspect(name, verdict, probe="no answer", **kw):
    c = {"time": "2026-10-23T10:00:00Z", "reason": "no guest heartbeat (red)", "busy": "", "probe": probe, "address": "192.0.2.20"}
    v = {"name": name, "moid": "vm-1", "vcenter": VC, "datacenter": "DC1", "cluster": "CL1", "esxi_host": "esx01.example.mil",
         "hostname": name + ".example.mil", "ip": "192.0.2.20", "ips": ["192.0.2.20"], "checks": [c, dict(c, time="2026-10-23T10:02:00Z")],
         "verdict": verdict}
    v.update(kw)
    return v


def data(**kw):
    d = {"vcenter": VC, "vcenters": [{"name": VC, "ok": True, "error": "", "note": ""}], "read_at": "2026-10-23T10:02:00Z",
         "window_minutes": 15, "hosts": [host("esx01.example.mil")], "datastores": [{"name": "DS1", "accessible": True, "maintenance": "normal",
                                                                                      "vcenter": VC, "datacenter": "DC1"}],
         "questions": [], "suspects": [], "events": [], "vms_checked": 40, "vms_skipped": 0, "not_probed": [],
         "probe": {"on": True, "port": 0, "timeout": 3}, "confirm": {"checks": 2, "seconds": 120}}
    d.update(kw)
    return d


class Conditions(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(w.vm_watch_conditions(data()), [])
        self.assertEqual(w.vm_watch_clean_line(data()),
                         "No outage: 1 host(s), 1 datastore(s), 40 powered-on VM(s) checked (vCenter vc01.example.mil).")

    def test_hosts(self):
        d = data(hosts=[host("esx01.example.mil", connection="notResponding"), host("esx02.example.mil", connection="disconnected"),
                        host("esx03.example.mil", connection="notResponding", maintenance=True),
                        host("esx04.example.mil", ha_state="networkIsolated"), host("esx05.example.mil", ha_state="fdmUnreachable"),
                        host("esx06.example.mil", ha_state="networkPartitionedFromMaster", connection="notResponding")])
        cs = {c["object"]: c for c in w.vm_watch_conditions(d)}
        self.assertEqual(cs["esx01.example.mil"]["severity"], "critical")
        self.assertIn("not responding", cs["esx01.example.mil"]["summary"])
        self.assertIn("disconnected", cs["esx02.example.mil"]["summary"])
        self.assertNotIn("esx03.example.mil", cs)                                   # maintenance: on purpose
        self.assertEqual((cs["esx04.example.mil"]["kind"], cs["esx04.example.mil"]["severity"]), ("ha", "critical"))
        self.assertEqual(cs["esx05.example.mil"]["severity"], "warning")
        self.assertEqual(cs["esx06.example.mil"]["kind"], "host")                   # not responding wins: one condition per host
        self.assertEqual(cs["esx01.example.mil"]["id"], "vm-watch:host:esx01.example.mil")

    def test_datastores_and_questions(self):
        d = data(datastores=[{"name": "DS1", "accessible": False, "maintenance": "normal", "vcenter": VC},
                             {"name": "DS2", "accessible": False, "maintenance": "inMaintenance", "vcenter": VC}],
                 questions=[{"name": "db01", "vcenter": VC, "question": "This virtual machine might have been moved or copied."}])
        cs = w.vm_watch_conditions(d)
        self.assertEqual([(c["kind"], c["object"], c["severity"]) for c in cs],
                         [("datastore", "DS1", "critical"), ("question", "db01", "critical")])
        self.assertIn("moved or copied", cs[1]["summary"])

    def test_storage_events_cleared_or_not(self):
        ev = [{"key": 1, "time": "2026-10-23T10:00:00Z", "type": "esx.problem.storage.apd.start", "host": "esx01.example.mil", "vcenter": VC,
               "message": "Device naa.600a0980 has entered the All Paths Down state.", "args": {}},
              {"key": 2, "time": "2026-10-23T10:00:00Z", "type": "esx.problem.storage.apd.start", "host": "esx02.example.mil", "vcenter": VC,
               "message": "Device naa.600a0981 has entered the All Paths Down state.", "args": {}},
              {"key": 3, "time": "2026-10-23T10:01:00Z", "type": "esx.clear.storage.apd.exit", "host": "esx02.example.mil", "vcenter": VC,
               "message": "Device naa.600a0981 has exited the All Paths Down state.", "args": {}},
              # a clear event of ANOTHER device clears nothing
              {"key": 4, "time": "2026-10-23T10:01:00Z", "type": "esx.clear.storage.apd.exit", "host": "esx01.example.mil", "vcenter": VC,
               "message": "Device naa.6000ffff has exited the All Paths Down state.", "args": {}}]
        cs = {c["object"]: c for c in w.vm_watch_conditions(data(events=ev))}
        self.assertEqual(cs["esx01.example.mil"]["severity"], "critical")
        self.assertIn("not cleared", cs["esx01.example.mil"]["summary"])
        self.assertEqual(cs["esx02.example.mil"]["severity"], "warning")
        self.assertEqual(cs["esx01.example.mil"]["id"], "vm-watch:storage:esx01.example.mil:naa.600a0980")

    def test_ha_events(self):
        ev = [{"key": 1, "time": "2026-10-23T10:00:00Z", "type": "com.vmware.vc.ha.VmRestartedByHAEvent", "vm": "web01", "host": "esx02",
               "vcenter": VC, "message": ""},
              {"key": 2, "time": "2026-10-23T10:01:00Z", "type": "VmFailoverFailed", "vm": "db01", "host": "esx01", "vcenter": VC, "message": ""},
              {"key": 3, "time": "2026-10-23T10:01:00Z", "type": "com.vmware.vc.ha.VmRestartedByHAEvent", "vm": "web01", "host": "esx02",
               "vcenter": VC, "message": ""},
              {"key": 4, "time": "2026-10-23T10:01:00Z", "type": "SomethingElse", "vm": "x", "vcenter": VC, "message": ""}]
        cs = w.vm_watch_conditions(data(events=ev))
        self.assertEqual([(c["object"], c["severity"]) for c in cs], [("db01", "critical"), ("web01", "warning")])   # web01 once
        self.assertIn("10:01:00", cs[1]["summary"])                                                                   # the newest
        self.assertIn("power it on by hand", cs[0]["todo"])

    def test_suspects(self):
        d = data(suspects=[suspect("app01", "hung"), suspect("app02", "one-signal", probe="answers"),
                           suspect("app03", "unprobed", probe="no address"), suspect("app04", "busy"), suspect("app05", "recovered")])
        cs = w.vm_watch_conditions(d)
        by = {c["object"]: c for c in cs}
        self.assertEqual(by["app01"]["severity"], "critical")
        self.assertEqual(by["app01"]["summary"], "VM app01 is hung: no guest heartbeat (red); no answer at 192.0.2.20, in 2 check(s)")
        self.assertEqual((by["app02"]["kind"], by["app02"]["severity"]), ("no-heartbeat", "warning"))
        self.assertIn("answers on the network", by["app02"]["summary"])
        self.assertIn("could not be probed", by["app03"]["summary"])
        self.assertNotIn("app04", by)
        self.assertNotIn("app05", by)
        info = w.vm_watch_conditions(d, {"one_signal_severity": "info"})
        self.assertEqual({c["object"]: c["severity"] for c in info}["app02"], "info")
        # silent to the probe, but vCenter saw it send packets: alive, said; or saw none: said with the hung line
        talks = suspect("app07", "one-signal")
        talks.update(alive_by="it sent 412 network packets in the last 7 minutes (vCenter's real-time statistics)",
                     traffic={"packets": 412, "minutes": 7.0})
        quiet = suspect("app08", "hung")
        quiet["traffic"] = {"packets": 0, "minutes": 7.0}
        unread = suspect("app09", "hung")
        unread["traffic"] = {"packets": None, "note": "vCenter has no counter net.packetsTx.summation"}
        by2 = {c["object"]: c for c in w.vm_watch_conditions(data(suspects=[talks, quiet, unread]))}
        self.assertEqual((by2["app07"]["kind"], by2["app07"]["severity"]), ("no-heartbeat", "warning"))
        self.assertIn("it still sends network traffic (vCenter's real-time statistics), though the probe got no answer at 192.0.2.20",
                      by2["app07"]["summary"])
        self.assertNotIn("412", by2["app07"]["summary"])          # no numbers: the ticket's summary stays the same
        self.assertTrue(by2["app08"]["summary"].endswith("in 2 check(s); vCenter saw no network packets sent"))
        self.assertTrue(by2["app09"]["summary"].endswith("; vCenter's statistics not read (vCenter has no counter net.packetsTx.summation)"))
        nj = {x["name"]: x["why"] for x in w.vm_watch_not_judged(d)}
        self.assertIn("a vCenter task was running", nj["app04"])
        self.assertIn("recovered during the run", nj["app05"])
        gone = suspect("app06", "busy")
        gone["checks"][1]["busy"] = "vCenter could not be read again (Could not connect)"
        self.assertEqual(w.vm_watch_not_judged(data(suspects=[gone]))[0]["why"], "vCenter could not be read again (Could not connect)")

    def test_vcenter_not_read(self):
        d = data(vcenters=[{"name": VC, "ok": True, "error": "", "note": ""},
                           {"name": "vc02.example.mil", "ok": False, "error": "Could not connect", "note": ""}])
        cs = w.vm_watch_conditions(d)
        self.assertEqual((cs[0]["kind"], cs[0]["severity"], cs[0]["vcenter"]), ("vcenter", "critical", "vc02.example.mil"))


class Actions(unittest.TestCase):
    def cs(self, *names):
        return w.vm_watch_conditions(data(suspects=[suspect(n, "hung", ip="192.0.2.%d" % (20 + i), ips=["192.0.2.%d" % (20 + i)])
                                                    for i, n in enumerate(names)]))

    def test_would_restart_protected_over_limit(self):
        a = w.vm_watch_actions(self.cs("app01", "aap01", "DC01", "app02", "app03"),
                               {"protected_ids": ["aap01.example.mil"], "protected_names": ["dc*"], "aap_known": True, "max_actions": 2})
        self.assertEqual([r["vm"] for r in a["would_restart"]], ["app01", "app02"])
        self.assertEqual({r["vm"]: r["why"] for r in a["protected"]},
                         {"aap01": "the AAP server, or a host of vmware_protected_groups", "DC01": "vmware_protected_vms / vm_watch_protect"})
        self.assertEqual([r["vm"] for r in a["over_limit"]], ["app03"])
        self.assertIn("checked 2026-10-23T10:00:00Z to 2026-10-23T10:02:00Z", a["would_restart"][0]["why"])

    def test_aap_matched_by_ip(self):
        a = w.vm_watch_actions(self.cs("srv-x"), {"protected_ids": ["192.0.2.20"], "aap_known": True})
        self.assertEqual(len(a["protected"]), 1)

    def test_inventory_without_aap(self):
        a = w.vm_watch_actions(self.cs("app01"), {"aap_known": False})
        self.assertEqual(a["would_restart"], [])
        self.assertIn("does not say which VM is the AAP server", a["protected"][0]["why"])

    def test_only_hung_vms(self):
        cs = w.vm_watch_conditions(data(hosts=[host("esx01", connection="notResponding")],
                                        suspects=[suspect("app02", "one-signal", probe="answers")]))
        self.assertEqual(w.vm_watch_actions(cs, {"aap_known": True}), {"would_restart": [], "protected": [], "over_limit": []})


class ReportAndTickets(unittest.TestCase):
    def test_report(self):
        d = data(suspects=[suspect("app01", "hung"), suspect("app04", "busy")], hosts=[host("esx01", connection="notResponding")])
        cs = w.vm_watch_conditions(d)
        a = w.vm_watch_actions(cs, {"aap_known": True})
        r = w.vm_watch_report(d, cs, a)
        self.assertEqual(r["title"], "VM outage watch: 2 critical")
        self.assertEqual(r["status"], "critical")
        titles = [s["title"] for s in r["sections"]]
        self.assertEqual(titles, ["Down or hung now", "Would be restarted in phase 2 (nothing was done)", "Not judged this run", "How it judged"])
        self.assertIn("Watch only: nothing was restarted.", r["subtitle"])

    def test_health_for_tickets(self):
        d = data(suspects=[suspect("app01", "hung")],
                 vcenters=[{"name": VC, "ok": True, "error": "", "note": ""},
                           {"name": "vc02.example.mil", "ok": False, "error": "Could not connect", "note": ""},
                           {"name": "198.51.100.7", "ok": True, "error": "", "note": "the same vCenter as vc01.example.mil: read once"}])
        h = w.vm_watch_health(d, w.vm_watch_conditions(d), {"job": 42})
        self.assertEqual(sorted(h), [VC, "vc02.example.mil"])                  # the same vCenter twice: once
        self.assertEqual(h[VC]["checks"], ["vm_watch"])
        self.assertFalse(h[VC]["reach"])                                       # never a reachability ticket
        self.assertEqual([f["id"] for f in h[VC]["findings"]], ["vm-watch:hung:app01"])
        self.assertEqual(h[VC]["findings"][0]["check"], "vm_watch")
        self.assertEqual(h["vc02.example.mil"]["checks_failed"], ["vm_watch"])  # not read: its open tickets are left alone
        self.assertEqual(h[VC]["job"], "42")
        clean = w.vm_watch_health(data(), [])
        self.assertEqual((clean[VC]["status"], clean[VC]["findings"]), ("healthy", []))   # a clean run clears the tickets

    def test_artifact(self):
        d = data(suspects=[suspect("app01", "hung")])
        cs = w.vm_watch_conditions(d)
        art = w.vm_watch_artifact(d, cs, w.vm_watch_actions(cs, {"aap_known": True}))
        self.assertEqual((art["critical"], art["warning"], art["checked"]["vms"]), (1, 0, 40))
        self.assertEqual(art["would_restart"][0]["vm"], "app01")


if __name__ == "__main__":
    unittest.main()
