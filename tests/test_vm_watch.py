#!/usr/bin/env python3
"""Unit tests for plugins/filter/vmware_watch_filters.py, the VM outage watch (standard library only):
python3 tests/test_vm_watch.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "filter"))
from site_filters import site_correlation_id  # noqa: E402
import vmware_watch_filters as w  # noqa: E402

VC = "vc-a.example.mil"


def host_moid(name):
    return "host-%d" % int(name.split(".")[0][3:])


def host(name, **kw):
    h = {"name": name, "moid": host_moid(name), "cluster": "CL1", "datacenter": "DC1", "vcenter": VC, "connection": "connected",
         "maintenance": False, "standby": "none", "ha_state": "connectedToMaster"}
    h.update(kw)
    return h


def suspect(name, verdict, probe="no answer", **kw):
    c = {"time": "2026-10-23T10:00:00Z", "reason": "no guest heartbeat (red)", "busy": "", "probe": probe, "address": "192.0.2.20"}
    v = {"name": name, "moid": "vm-" + name, "host_moid": "host-1", "vcenter": VC, "datacenter": "DC1", "cluster": "CL1", "esxi_host": "esx01.example.mil",
         "hostname": name + ".example.mil", "ip": "192.0.2.20", "ips": ["192.0.2.20"], "checks": [c, dict(c, time="2026-10-23T10:02:00Z")],
         "verdict": verdict}
    v.update(kw)
    return v


def data(**kw):
    d = {"vcenter": VC, "vcenters": [{"name": VC, "ok": True, "error": "", "note": ""}], "read_at": "2026-10-23T10:02:00Z",
         "window_minutes": 15, "hosts": [host("esx01.example.mil")], "datastores": [{"name": "DS1", "moid": "datastore-1", "accessible": True, "maintenance": "normal",
                                                                                      "vcenter": VC, "datacenter": "DC1"}],
         "questions": [], "suspects": [], "events": [], "vms_checked": 40, "vms_skipped": 0, "not_probed": [],
         "probe": {"on": True, "port": 0, "timeout": 3}, "confirm": {"checks": 2, "seconds": 120}}
    d.update(kw)
    return d


def lost(name, conn="inaccessible", power="poweredOff", dss=("ds-07",), **kw):
    v = {"name": name, "moid": "vm-" + name, "vcenter": VC, "datacenter": "DC1", "cluster": "CL1", "esxi_host": "esx01.example.mil",
         "hostname": "", "ip": "", "ips": [], "power": power, "connection": conn, "datastores": list(dss),
         "host_moid": "host-1", "datastore_moids": [{"DS1": "datastore-1", "ds-07": "datastore-7"}[ds] for ds in dss], "not_judged": ""}
    v.update(kw)
    return v


class Lost1124(unittest.TestCase):
    """1.112.4: inaccessible, invalid and orphaned VMs are critical, whatever their power, under their datastore; a skipped
    one is listed "not judged", a protected one marked; a disconnected host lists its VMs; the counts in How it judged."""

    def d(self):
        return data(hosts=[host("esx01.example.mil"), host("esx02.example.mil", connection="disconnected",
                                                               vms=[{"name": "web01", "moid": "vm-web01"}])],
                    datastores=[{"name": "DS1", "moid": "datastore-1", "accessible": True, "maintenance": "normal", "vcenter": VC, "datacenter": "DC1"},
                                {"name": "ds-07", "moid": "datastore-7", "accessible": False, "maintenance": "normal", "vcenter": VC, "datacenter": "DC1"}],
                    vms_read=60, vms_checked=50,
                    vms_lost=[lost("app01"), lost("app02", power=""), lost("db01", dss=("DS1", "ds-07")), lost("old01", conn="orphaned"),
                              lost("test01", not_judged="vm_watch_skip_vms"), lost("aap01")],
                    vms_disconnected=[dict(lost("web01", conn="disconnected", power="poweredOn"), esxi_host="esx02.example.mil")])

    def test_1_conditions(self):
        cs = w.vm_watch_conditions(self.d(), {"protected_names": ["aap*"]})
        order = [(c["kind"], c["object"]) for c in cs]
        i = order.index(("datastore", "ds-07"))
        self.assertEqual(order[i + 1:i + 6], [("lost", "aap01"), ("lost", "app01"), ("lost", "app02"), ("lost", "db01"), ("lost", "old01")])
        by = dict((c["object"], c) for c in cs if c["kind"] == "lost")
        self.assertEqual(by["app01"]["severity"], "critical")
        self.assertEqual(by["app01"]["summary"], "VM app01 is inaccessible in vCenter: its files cannot be reached (datastore ds-07)")
        self.assertIn("orphaned in vCenter", by["old01"]["summary"])
        self.assertIn("Do not remove the VM from the inventory", by["app01"]["todo"])
        self.assertEqual(by["app02"]["detail"], "power unknown")
        self.assertEqual((by["test01"]["severity"], by["test01"]["not_judged"]), ("info", "vm_watch_skip_vms"))
        self.assertTrue(by["test01"]["summary"].endswith("- not judged (vm_watch_skip_vms)"))
        self.assertTrue(by["aap01"]["protected"] and by["aap01"]["summary"].endswith("- protected"))
        ds = next(c for c in cs if c["kind"] == "datastore")
        self.assertIn("vCenter shows 6 VMs on it as inaccessible: aap01, app01, app02, db01, old01, test01", ds["summary"])
        h = next(c for c in cs if c["kind"] == "host")
        self.assertTrue(h["summary"].endswith("; 1 VM potentially affected: web01"), h["summary"])
        self.assertFalse(any(c["object"] == "web01" and c["kind"] != "host" for c in cs))          # under its host, not a row of its own

    def test_2_never_an_action_and_counted(self):
        d = self.d()
        cs = w.vm_watch_conditions(d)
        a = w.vm_watch_actions(cs, {"max_actions": 5})
        self.assertEqual(a, {"would_restart": [], "protected": [], "over_limit": []})
        rep = w.vm_watch_report(d, cs, a)
        how = next(s for s in rep["sections"] if s["title"] == "How it judged")
        self.assertIn("VMs read: 60. Judged (powered on, connected): 50. Listed as inaccessible, invalid or orphaned: 6 (1 of them not "
                      "judged: vm_watch_skip_vms / vm_watch_skip_folders). Potentially affected on down hosts: 1.", how["lines"])
        rows = next(s for s in rep["sections"] if s["title"] == "Down or hung now")["rows"]
        self.assertTrue(any(r[1] == "vm app01" for r in rows))
        self.assertEqual(rep["status"], "critical")

    def test_3_an_older_read_unchanged(self):
        rep = w.vm_watch_report(data(), [], {})
        how = next(s for s in rep["sections"] if s["title"] == "How it judged")
        self.assertFalse(any(l.startswith("VMs read") for l in how["lines"]))


def apd(key, t, host_, dev, typ="esx.problem.storage.apd.start"):
    return {"key": key, "time": t, "type": typ, "host": host_, "host_moid": host_moid(host_), "vcenter": VC, "args": {},
            "message": "Device %s has %s the All Paths Down state." % (dev, "exited" if "clear" in typ else "entered")}


class Fold1124(unittest.TestCase):
    """1.112.4 (the user): a datastore lost on several hosts is ONE line, first, the headline - its hosts' "all paths down"
    lines fold into it (its detail); a device lost on 2+ hosts is one line; one host's path problem stays as it is."""

    def d(self):
        hs = [host(h) for h in ("esx02.example.mil", "esx04.example.mil", "esx05.example.mil", "esx06.example.mil")]
        dss = [{"name": "ds-07", "moid": "datastore-7", "accessible": False, "maintenance": "normal", "vcenter": VC, "datacenter": "DC1", "devices": ["naa.600a07"]},
               {"name": "ds-08", "moid": "datastore-8", "accessible": True, "maintenance": "normal", "vcenter": VC, "datacenter": "DC1", "devices": ["naa.600a08"]},
               {"name": "ds-09", "moid": "datastore-9", "accessible": True, "maintenance": "normal", "vcenter": VC, "datacenter": "DC1", "devices": ["naa.600a09"]}]
        ev = [apd(1, "2026-11-20T22:09:00Z", "esx02.example.mil", "naa.600a07"), apd(2, "2026-11-20T22:09:30Z", "esx04.example.mil", "naa.600a07"),
              apd(3, "2026-11-20T22:10:00Z", "esx05.example.mil", "naa.600a07"),
              apd(4, "2026-11-20T22:11:00Z", "esx02.example.mil", "naa.600a08"), apd(5, "2026-11-20T22:11:00Z", "esx04.example.mil", "naa.600a08"),
              apd(6, "2026-11-20T22:12:00Z", "esx06.example.mil", "naa.600a09")]
        return data(hosts=hs, datastores=dss, events=ev, vms_read=30, vms_lost=[lost("app01"), lost("app02")])

    def test_1_the_lost_datastore_is_one_line_and_first(self):
        cs = w.vm_watch_conditions(self.d())
        first = cs[0]
        self.assertEqual((first["kind"], first["object"]), ("datastore", "ds-07"))
        self.assertEqual(first["summary"], "Datastore ds-07 NOT ACCESSIBLE on 3 hosts (esx02, esx04, esx05): all paths down since "
                                           "2026-11-20T22:09:00Z - 2 VMs inaccessible: app01, app02")
        self.assertEqual(len(first["paths"]), 3)
        self.assertTrue(first["paths"][0].startswith("esx02.example.mil: all paths down on host esx02.example.mil (device naa.600a07)"))
        storage = [c for c in cs if c["kind"] == "storage"]
        self.assertEqual([c["object"] for c in storage], ["esx06.example.mil"])          # one host, no lost datastore: as it was
        self.assertEqual(first["id"], "vm-watch:datastore:%s:datastore-7" % VC)                         # the ticket stays the datastore's

    def test_2_a_device_on_two_hosts_is_one_line(self):
        cs = w.vm_watch_conditions(self.d())
        c = next(c for c in cs if c["object"] == "ds-08")
        self.assertEqual(c["severity"], "critical")
        self.assertTrue(c["summary"].startswith("Datastore ds-08 lost its paths on 2 hosts (esx02, esx04): all paths down since"))
        self.assertFalse(any(x["kind"] == "storage" and "naa.600a08" in x["summary"] for x in cs))

    def test_3_the_headline(self):
        d = self.d()
        cs = w.vm_watch_conditions(d)
        rep = w.vm_watch_report(d, cs, w.vm_watch_actions(cs))
        self.assertTrue(rep["title"].startswith("Datastore ds-07 NOT ACCESSIBLE on 3 hosts (esx02, esx04, esx05) - VM outage watch: "), rep["title"])
        row = next(s for s in rep["sections"] if s["title"] == "Down or hung now")["rows"][0]
        self.assertIn("Per host: esx02.example.mil: all paths down", row[3])

    def test_4_cleared_on_every_host_is_a_warning(self):
        ev = [apd(1, "2026-11-20T22:09:00Z", "esx02.example.mil", "naa.600a08"), apd(2, "2026-11-20T22:09:00Z", "esx04.example.mil", "naa.600a08"),
              apd(3, "2026-11-20T22:20:00Z", "esx02.example.mil", "naa.600a08", "esx.clear.storage.apd.exit"),
              apd(4, "2026-11-20T22:20:00Z", "esx04.example.mil", "naa.600a08", "esx.clear.storage.apd.exit")]
        cs = w.vm_watch_conditions(dict(self.d(), events=ev))
        c = next(c for c in cs if c["object"] == "ds-08")
        self.assertEqual(c["severity"], "warning")
        self.assertTrue(c["summary"].endswith("cleared since on every host"))


class Tickets1124(unittest.TestCase):
    """1.112.4: tickets fold to their root - a big storage loss with 20 VMs is 1 ticket; a VM no lost datastore or down host
    explains is its own."""

    def test_1_twenty_vms_one_ticket(self):
        lost20 = [lost("app%02d" % i) for i in range(1, 21)]
        hung_here = suspect("db01", "hung", datastores=["ds-07"], datastore_moids=["datastore-7"])
        hung_else = suspect("web09", "hung", datastores=["DS1"], datastore_moids=["datastore-1"])
        orphan = lost("old01", conn="orphaned", dss=("DS1",))                       # its datastore is fine: no root
        on_down_host = lost("app99", dss=("DS1",), esxi_host="esx02.example.mil", host_moid="host-2")
        d = data(hosts=[host("esx01.example.mil"), host("esx02.example.mil", connection="notResponding")],
                 datastores=[{"name": "DS1", "moid": "datastore-1", "accessible": True, "maintenance": "normal", "vcenter": VC, "datacenter": "DC1"},
                             {"name": "ds-07", "moid": "datastore-7", "accessible": False, "maintenance": "normal", "vcenter": VC, "datacenter": "DC1"}],
                 vms_read=80, vms_lost=lost20 + [orphan, on_down_host], suspects=[hung_here, hung_else])
        cs = w.vm_watch_conditions(d)
        self.assertEqual(len([c for c in cs if c["kind"] in ("lost", "hung")]), 24)    # the report still has every VM
        f = w.vm_watch_health(d, cs)[VC]["findings"]
        by = dict((x["id"], x) for x in f)
        self.assertEqual(sorted(by), ["vm-watch:datastore:ds-07", "vm-watch:host:esx02.example.mil", "vm-watch:hung:web09",
                                      "vm-watch:lost:%s:vm-old01" % VC])
        ds = by["vm-watch:datastore:ds-07"]["summary"]
        self.assertIn("; 21 VMs: app01 [vm-app01] (inaccessible), app02 [vm-app02] (inaccessible)", ds)
        self.assertIn("app20 [vm-app20] (inaccessible), db01 [vm-db01] (hung) [DC1]", ds)
        self.assertIn("app20 [vm-app20] (inaccessible)", ds)
        self.assertIn("1 VM: app99 [vm-app99] (inaccessible)", by["vm-watch:host:esx02.example.mil"]["summary"])

    def test_2_the_summary_changes_with_the_vms(self):
        def summary(n):
            d = data(datastores=[{"name": "ds-07", "moid": "datastore-7", "accessible": False, "maintenance": "normal", "vcenter": VC, "datacenter": "DC1"}],
                     vms_lost=[lost("app%02d" % i) for i in range(n)])
            return w.vm_watch_health(d, w.vm_watch_conditions(d))[VC]["findings"][0]["summary"]
        self.assertNotEqual(summary(3), summary(4))                                     # the ticket is noted as it changes


class Review1124(unittest.TestCase):
    other_vc = "vc-b.example.mil"

    def datastore(self, accessible=True, moid="datastore-1", **kw):
        return dict({"name": "DS1", "vcenter": VC, "moid": moid, "accessible": accessible, "devices": ["naa.1111"]}, **kw)

    def path(self, host_, dev="naa.1111", typ="esx.problem.storage.apd.start", **kw):
        return dict(apd(1, "2026-11-23T10:00:00Z", host_, dev, typ), **kw)

    def test_root_uses_vcenter_and_moid(self):
        down = self.datastore(False)
        other = self.datastore(False, "datastore-2")
        vm = lost("app01", dss=("DS1",), esxi_host="esx01")
        d = data(vcenters=[{"name": vc, "ok": True} for vc in (VC, self.other_vc)],
                 hosts=[host("esx01", connection="disconnected")],
                 datastores=[down, other, self.datastore(moid="datastore-3"), self.datastore(vcenter=self.other_vc)],
                 vms_lost=[vm, dict(vm, name="app02", moid="vm-2", vcenter=self.other_vc),
                           dict(vm, name="app03", moid="vm-3", datastore_moids=["datastore-3"], host_moid="host-2"),
                           dict(vm, name="app04", moid="vm-4", datastore_moids=[]),
                           dict(vm, name="app05", moid="vm-5", datastore_moids=["datastore-2"])])
        cs = w.vm_watch_conditions(d)
        findings = w.vm_watch_health(d, cs)
        self.assertEqual(len(findings[self.other_vc]["findings"]), 1)
        local = {f["id"]: f["summary"] for f in findings[VC]["findings"]}
        self.assertIn("app01", local["vm-watch:datastore:DS1:datastore-1"])
        self.assertIn("app05", local["vm-watch:datastore:DS1:datastore-2"])
        self.assertIn("app04", local["vm-watch:host:esx01"])
        self.assertIn("vm-watch:lost:%s:vm-3" % VC, local)
        rows = w.vm_watch_report(d, cs)["sections"][0]["rows"]
        self.assertEqual([r[1] for r in rows[:4]], ["datastore DS1", "vm app01", "datastore DS1", "vm app05"])
        self.assertEqual(len(rows), 8)
        vm["datastore_moids"].append("datastore-2")
        rows = w.vm_watch_report(d, w.vm_watch_conditions(d))["sections"][0]["rows"]
        self.assertEqual(sum(r[1] == "vm app01" for r in rows), 1)
        self.assertTrue(all("app01" in r[3] for r in rows if r[1] == "datastore DS1"))

    def test_path_and_clear_stay_with_their_inventory(self):
        a = self.path("esx01")
        b = dict(a, vcenter=self.other_vc)
        clear = self.path("esx01", typ="esx.clear.storage.apd.exit", key=2)
        other_extent = self.path("esx02", "naa.2222")
        d = data(datastores=[self.datastore(False), self.datastore(vcenter=self.other_vc),
                             self.datastore(moid="datastore-2", devices=["naa.2222"])],
                 events=[a, b, clear, other_extent])
        cs = w.vm_watch_conditions(d)
        root, = [c for c in cs if c["kind"] == "datastore"]
        self.assertEqual(len(root["paths"]), 1)
        self.assertIn("cleared at", root["paths"][0])
        self.assertEqual({(c["vcenter"], c["device"], c["severity"]) for c in cs if c["kind"] == "storage"},
                         {(VC, "naa.2222", "critical"), (self.other_vc, "naa.1111", "critical")})
        self.assertEqual(len(w.vm_watch_health(d, cs)[self.other_vc]["findings"]), 1)

    def test_accessible_datastore_only_absorbs_vms_on_failed_paths(self):
        vm = suspect("app01", "hung", esxi_host="esx01", datastores=["DS1"], datastore_moids=["datastore-1"])
        d = data(datastores=[self.datastore()], events=[self.path(h) for h in ("esx01", "esx02", "esx03")],
                 suspects=[vm, dict(vm, name="app02", moid="vm-2", host_moid="host-3", esxi_host="esx03"),
                           dict(vm, name="app03", moid="vm-3", host_moid="host-4", esxi_host="esx04")])
        d["events"].append(self.path("esx03", typ="esx.clear.storage.apd.exit", key=2))
        cs = w.vm_watch_conditions(d)
        findings = w.vm_watch_health(d, cs)[VC]["findings"]
        self.assertEqual({f["id"] for f in findings if f["id"].startswith("vm-watch:hung:")},
                         {"vm-watch:hung:app02", "vm-watch:hung:app03"})
        row = w.vm_watch_report(d, cs)["sections"][0]["rows"][0]
        self.assertIn("app01", row[3])
        self.assertNotIn("app02", row[3])
        self.assertNotIn("app03", row[3])

    def test_same_name_lost_vms_get_distinct_tickets(self):
        for moid in ("vm-101", "vm-202"):
            d = data(vms_lost=[lost("app01", conn="orphaned", moid=moid)])
            finding, = w.vm_watch_health(d, w.vm_watch_conditions(d))[VC]["findings"]
            self.assertEqual(finding["id"], "vm-watch:lost:%s:%s" % (VC, moid))
            self.assertIn("VM app01", finding["summary"])

    def test_only_shared_naa_or_eui_folds(self):
        local = "mpx.vmhba0:C0:T0:L0"
        d = data(datastores=[self.datastore(devices=["naa.1111", "naa.2222"])],
                 events=[self.path("esx01"), self.path("esx02", "naa.2222"), self.path("esx01", local), self.path("esx02", local),
                         self.path("esx01", "eui.1111"), self.path("esx02", "eui.1111")])
        cs = w.vm_watch_conditions(d)
        self.assertCountEqual([(c["host"], c["device"]) for c in cs if c["kind"] == "storage"],
                              [("esx01", "naa.1111"), ("esx02", "naa.2222"), ("esx01", local), ("esx02", local)])
        device, = [c for c in cs if c["kind"] == "datastore"]
        self.assertEqual((device["object_type"], device["object"]), ("device", "eui.1111"))
        d["events"] += [self.path("esx02"), self.path("esx01", "naa.2222")]
        ds, = [c for c in w.vm_watch_conditions(d) if c["object_type"] == "datastore"]
        self.assertEqual(len(ds["paths"]), 4)

    def test_datastore_correlation_survives_inaccessibility(self):
        d = data(datastores=[self.datastore()], events=[self.path("esx01"), self.path("esx02")])
        ids = []
        for accessible in (True, False):
            d["datastores"][0]["accessible"] = accessible
            finding, = w.vm_watch_health(d, w.vm_watch_conditions(d))[VC]["findings"]
            ids.append(site_correlation_id(finding["id"], VC, finding["check"]))
        expected = site_correlation_id("vm-watch:datastore:DS1", VC, "vm_watch")      # the legacy id: an open incident stays open
        self.assertEqual(ids, [expected, expected])

    def test_full_vm_lists_in_report_and_ticket_updates(self):
        members = [lost("app%02d" % i, conn="disconnected", esxi_host="esx01", dss=(), moid="vm-%d" % i) for i in range(1, 10)]
        d = data(hosts=[host("esx01", connection="notResponding", vms=members)], vms_disconnected=members,
                 datastores=[self.datastore(False)],
                 vms_lost=[lost("app%02d" % i, dss=("DS1",), host_moid="host-2", moid="vm-%d" % i) for i in range(10, 41)])

        def summaries():
            cs = w.vm_watch_conditions(d)
            rows = {r[1]: r[3] for r in w.vm_watch_report(d, cs)["sections"][0]["rows"]}
            tickets = [f["summary"] for f in w.vm_watch_health(d, cs)[VC]["findings"]]
            self.assertEqual(len(tickets), 2)
            host_ticket, = [s for s in tickets if s.startswith("ESXi host ")]
            ds_ticket, = [s for s in tickets if s.startswith("datastore ")]
            for text in (rows["host esx01"], host_ticket):
                for vm in members:
                    self.assertIn(vm["name"], text)
                self.assertIn("potentially affected", text.lower())
            for text in (rows["datastore DS1"], ds_ticket):
                for vm in d["vms_lost"]:
                    self.assertIn(vm["name"], text)
            return host_ticket, ds_ticket
        before = summaries()
        members[-1]["name"] = "app41"
        d["vms_lost"][-1]["name"] = "app42"
        after = summaries()
        self.assertNotIn("app09", after[0])
        self.assertNotIn("app40", after[1])
        for old, new in zip(before, after):
            self.assertNotEqual(old, new)


class TicketIds1124(unittest.TestCase):
    """1.112.4: the ids tickets already carry stay as they were (no incident resolved and opened again at the upgrade);
    a datastore keeps one id from path loss to not accessible; :<moid> only for two datastores of one name in one vCenter."""

    def ids(self, d):
        return sorted(f["id"] for f in w.vm_watch_health(d, w.vm_watch_conditions(d))[VC]["findings"])

    def ds(self, accessible, moid="datastore-1", name="DS1", devices=("naa.1111",)):
        return {"name": name, "vcenter": VC, "datacenter": "DC1", "moid": moid, "accessible": accessible, "maintenance": "normal",
                "devices": list(devices)}

    def test_legacy_ids(self):
        ev = [apd(1, "2026-11-23T10:00:00Z", "esx01", "naa.1111"), apd(2, "2026-11-23T10:00:00Z", "esx02", "naa.1111"),
              apd(3, "2026-11-23T10:00:00Z", "esx03", "naa.9999")]
        hs = [host("esx01"), host("esx02"), host("esx03"), host("esx09", connection="notResponding")]
        before = self.ids(data(hosts=hs, datastores=[self.ds(True)], events=ev))
        after = self.ids(data(hosts=hs, datastores=[self.ds(False)], events=ev))
        self.assertIn("vm-watch:datastore:DS1", before)                       # path loss on 2 hosts ...
        self.assertIn("vm-watch:datastore:DS1", after)                        # ... then not accessible: the same incident
        for ids in (before, after):
            self.assertIn("vm-watch:host:esx09", ids)                         # as before 1.112.4
            self.assertIn("vm-watch:storage:esx03:naa.9999", ids)            # one host's path: as before 1.112.4

    def test_same_name_in_one_vcenter(self):
        ids = self.ids(data(datastores=[self.ds(False), self.ds(False, "datastore-2", devices=("naa.2222",))]))
        self.assertEqual(ids, ["vm-watch:datastore:DS1:datastore-1", "vm-watch:datastore:DS1:datastore-2"])


class Conditions(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(w.vm_watch_conditions(data()), [])
        self.assertEqual(w.vm_watch_clean_line(data()),
                         "No outage: 1 host(s), 1 datastore(s), 40 powered-on VM(s) checked (vCenter vc-a.example.mil).")

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
        self.assertEqual(cs["esx01.example.mil"]["id"], "vm-watch:host:%s:host-1" % VC)

    def test_datastores_and_questions(self):
        d = data(datastores=[{"name": "DS1", "moid": "datastore-1", "accessible": False, "maintenance": "normal", "vcenter": VC},
                             {"name": "DS2", "moid": "datastore-2", "accessible": False, "maintenance": "inMaintenance", "vcenter": VC}],
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
