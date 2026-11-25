#!/usr/bin/env python3
"""Field tuning regressions, using placeholder inventory and recorded property reads. Run with python3 tests/test_wall_tuning.py."""
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import Mock

from test_wall import M, demo
from test_wall_live import Clock, Fake, W, obj_xml


class WallTuning(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.vc = Fake()
        self.storage = Fake(key="ontap:array.example.mil", kind="ontap")
        self.storage.result = dict(cluster="array", nodes=[], volumes=[], luns=[], aggrs=[], snapmirror=[])
        self.collector = W.Collector([self.vc, self.storage], clock=self.clock)
        self.tracker = M.Tracker()
        self.opts = {}

    def read(self):
        self.clock.t += 60
        self.collector.cycle()
        return M.build(self.collector.view(), None, None, self.opts, self.tracker, self.clock())

    @staticmethod
    def active(answer, kind):
        return [c for c in answer["conds"] if c["kind"] == kind and c["end"] is None]

    @staticmethod
    def vm(name, **changes):
        return dict({"name": name, "id": name, "power": "poweredOn", "template": False,
                     "heartbeat": "red", "cpu_pct": 5, "mem_pct": 10}, **changes)

    def test_heartbeat_needs_an_earlier_read_of_that_vm(self):
        vms = [self.vm("app1", tools="guestToolsRunning"), self.vm("app2", heartbeat="green"),
               self.vm("app3"), self.vm("app4", tools_version="guestToolsNotInstalled")]
        self.vc.result = {"vms": vms}
        first = self.read()
        self.assertFalse(self.active(first, "vmhung"))
        # A repeated API read must never learn history from itself.
        second = M.build(self.collector.view(), None, None, {}, self.tracker, self.clock())
        self.assertFalse(self.active(second, "vmhung"))
        vms[0].update(tools="guestToolsNotRunning", heartbeat="gray")
        vms[1]["heartbeat"] = "red"
        bad = self.read()
        self.assertEqual([c["vm"] for c in self.active(bad, "vmhung")], ["app1", "app2"])
        self.assertEqual(bad["vmware"]["heartbeat_unwatched"], 2)
        vms[0].update(tools=None, heartbeat=None)
        vms[1].update(id="replacement", heartbeat="green")  # a different VM with the same name
        self.assertTrue(all(c["last_known"] for c in self.active(self.read(), "vmhung")))
        vms[0]["heartbeat"] = "green"
        self.assertEqual([c["vm"] for c in self.active(self.read(), "vmhung")], ["app2"])

    def test_vcls_templates_and_power_are_checked_before_load_and_heartbeat(self):
        rows = []
        for name, extension, typ in [("vCLS-01", "", ""), ("external", "com.vmware.vim.eam", "cluster-agent"),
                                     ("embedded", "VirtualCenter", "vcls-entity"), ("template", "", ""),
                                     ("off", "", ""), ("workload", "com.vmware.vim.eam", "other-agent")]:
            rows.append(("VirtualMachine", name, {"name": name, "config.managedBy.extensionKey": extension, "config.managedBy.type": typ,
                        "runtime.powerState": "poweredOff" if name == "off" else "poweredOn", "config.template": name == "template",
                        "guestHeartbeatStatus": "green", "summary.quickStats.overallCpuUsage": 99, "summary.runtime.maxCpuUsage": 100}))
        self.vc.result = W.shape(rows)
        self.opts = {"high_minutes": 1}
        self.read()
        for vm in self.vc.result["vms"]:
            vm["heartbeat"] = "red"
        answer = self.read()
        self.assertEqual([c["vm"] for c in self.active(answer, "vmhung")], ["workload"])
        self.assertEqual([c["obj_name"] for c in self.active(answer, "cpu")], ["workload"])

    def test_excluded_objects_do_not_contribute_to_conditions_or_rankings(self):
        self.vc.result = {"hosts": [demo.host("Skip-host", "cluster", 99, 99, [], [], state="disconnected"),
                                    demo.host("keep-host", "cluster", 15, 20, [], [])],
                          "vms": [self.vm("SKIP-vm", cpu_pct=100, tools="guestToolsRunning")],
                          "datastores": [{"name": "skip-store", "shared": True, "accessible": False, "capacity": 100, "free": 0}]}
        self.storage.result.update(aggrs=[{"name": "skip-tier", "size": 100, "used": 99}],
                                   volumes=[{"name": "skip-vol", "svm": "svm", "state": "restricted"}])
        self.opts = {"high_minutes": 1, "exclude": {k: ["sK?p-*"] for k in ("vms", "hosts", "datastores", "volumes", "aggregates")}}
        self.read()
        answer = self.read()
        self.assertFalse(answer["conds"])
        self.assertEqual([c["of"] for c in answer["vmware"]["clusters"]], [1])
        self.assertEqual(answer["storage"]["ds"] + answer["storage"]["aggr"] + answer["vmware"]["hot"], [])
        excluded = answer["vmware"]["excluded"] + answer["storage"]["excluded"]
        self.assertEqual({x["kind"] for x in excluded}, set(self.opts["exclude"]))
        self.assertEqual({x["reason"] for x in excluded}, {"excluded by portal_wall_exclude"})

    def test_local_and_ignored_datastores_keep_the_accessibility_check(self):
        def ds(name, shared):
            return {"name": name, "capacity": 100, "free": 1, "shared": shared, "accessible": True}
        stores = [ds("local", False), ds("Shared-ISO", True), ds("CONTENT-a", True), ds("my-Library", True),
                  ds("work", True), ds("unread", None)]
        stores[0]["accessible"] = False
        self.vc.result = {"datastores": stores}
        answer = self.read()
        self.assertEqual([d["n"] for d in answer["storage"]["ds"]], ["work"])
        self.assertEqual([c["ds"] for c in self.active(answer, "dsfull")], ["work"])
        self.assertEqual([c["ds"] for c in self.active(answer, "dsi")], ["local"])
        self.assertEqual([x["reason"] for x in answer["vmware"]["excluded"]], ["local - not watched"] * 4)
        self.assertFalse(next(a for a in answer["areas"] if a["id"] == "storage")["complete"])
        stores[-2]["shared"] = None
        self.assertTrue(self.active(self.read(), "dsfull")[0]["last_known"])
        self.opts = {"ds_ignore": []}
        self.assertIn("Shared-ISO", [c["ds"] for c in self.active(self.read(), "dsfull")])

    def test_ontap_root_and_ls_require_ontap_evidence(self):
        volumes = [{"name": "vol0", "type": "rw"}, {"name": "svm_root_LS_Mirror01", "type": "dp", "snapmirror": {"is_protected": True}},
                   {"name": "actual-root", "is_svm_root": True}, {"name": "mirror", "type": "ls"},
                   {"name": "matched", "type": "dp"}, {"name": "dp-only", "type": "dp"}]
        for v in volumes:
            v.update(svm={"name": "svm1"}, state="restricted")
        raw = {"aggrs": [{"name": "root-by-name", "space": {"block_storage": {"size": 100, "used": 99}}},
                         {"name": "system", "is_root": True, "space": {"block_storage": {"size": 100, "used": 100}}}],
               "volumes": volumes, "snapmirror": [{"type": "LS", "destination": {"path": "svm1:matched"}}],
               "luns": [{"name": "/vol/matched/lun0", "svm": {"name": svm}, "location": {"volume": {"name": "matched"}},
                         "status": {"state": "offline"}} for svm in ("svm1", "svm2")]}
        source = W.OntapSource("array.example.mil", "reader", "example", verify=False)
        source._read = Mock(return_value=({"name": "array"}, [], raw, {}))
        self.storage.result = source.poll()
        answer = self.read()
        alerts = self.active(answer, "voloff")
        self.assertEqual([c["obj_name"] for c in alerts], ["vol0", "svm_root_LS_Mirror01", "dp-only", "/vol/matched/lun0"])
        self.assertIn("svm2", alerts[-1]["obj"])
        self.assertEqual([c["aggr"] for c in self.active(answer, "aggrfull")], ["root-by-name"])
        self.assertEqual([a["n"] for a in answer["storage"]["aggr"]], ["root-by-name"])

    def test_lag_boundaries_and_recovery(self):
        rel = {"dst": "svm:vol", "state": "in_sync", "healthy": True}
        self.storage.result["snapmirror"] = [rel]
        self.opts = {"snapmirror_lag_warn_hours": 1, "snapmirror_lag_crit_hours": 2}
        for lag, level in [(3600, None), (3600.5, "warn"), (7200, "warn"), (7200.5, "crit")]:
            rel["lag_s"] = lag
            self.assertEqual([c["sev"] for c in self.active(self.read(), "sm")], [level] if level else [])
        for lag in [None, -1, "PT1H", False, float("nan")]:
            rel["lag_s"] = lag
            answer = self.read()
            self.assertFalse(answer["storage"]["ran"]["sm"])
            self.assertTrue(self.active(answer, "sm")[0]["last_known"])
        rel["lag_s"] = 300
        self.assertFalse(self.active(self.read(), "sm"))
        rel.update(healthy=False, lag_s=None)
        self.assertEqual(self.active(self.read(), "sm")[0]["sev"], "warn")

    def test_hardware_severity_alarm_names_and_recovery(self):
        host = demo.host("host1", "cluster", 10, 20, [], [])
        self.vc.result = {"hosts": [host]}
        for status, severity in [("yellow", "warn"), ("red", "crit")]:
            host.update(hardware=status, alarms=["Fan sensor"])
            c, = self.active(self.read(), "hardware")
            self.assertEqual((c["sev"], c["host"]), (severity, "host1"))
            self.assertIn("Fan sensor", c["obj"])
        for unknown in ["gray", None, "unexpected"]:
            host["hardware"] = unknown
            answer = self.read()
            self.assertTrue(self.active(answer, "hardware")[0]["last_known"])
            self.assertEqual(answer["vmware"]["hosts"][0]["st"], "stale")
        host["hardware"] = "green"
        self.assertFalse(self.active(self.read(), "hardware"))


class CollectorReads(unittest.TestCase):
    def test_names_and_authenticated_uuid_merge(self):
        log = []
        sources = W.sources_from_config({"vcenter": {"user": "reader", "password": "example", "verify": False,
                           "hosts": ["VC01", "Alias", "vc01.example.mil", "VC01.EXAMPLE.MIL", "vc01:8443"]}}, log=log.append)
        self.assertEqual([s.name for s in sources], ["alias", "vc01.example.mil", "vc01:8443"])
        self.assertEqual(log[0]["state"], "duplicate short name")
        # Read the UUID via the real login path. The other port represents a distinct instance.
        for source in sources:
            def reply(method, args, s=source):
                uuid = "instance-1" if s.port == 443 else "instance-2"
                if method == "RetrieveServiceContent":
                    content = "<about><instanceUuid>%s</instanceUuid></about>" % uuid
                elif method == "Login":
                    s.cookie = "example"
                    content = "<key>example</key>"
                else:
                    content = "view" if method == "CreateContainerView" else ""
                return ET.fromstring('<%sResponse xmlns="urn:vim25"><returnval>%s</returnval></%sResponse>' % (method, content, method))
            source._send = Mock(side_effect=reply)
        collector = W.Collector(sources, log=log.append)
        collector.cycle()
        collector.cycle()
        self.assertEqual(sorted(collector.view()["sources"]), ["vcenter:vc01.example.mil", "vcenter:vc01:8443"])
        self.assertEqual(sum(call.args[0] == "RetrievePropertiesEx" for call in sources[0]._send.call_args_list), 1)
        self.assertEqual(sources[1].logins, 1)
        self.assertEqual(log[-1]["state"], "duplicate vCenter")

    def test_later_fqdn_read_keeps_the_original_alert_start(self):
        down = demo.host("host1", "cluster", 5, 10, [], [], state="disconnected")
        short = Fake("vcenter:alias", result={"hosts": [down]})
        fqdn = Fake("vcenter:vc.example.mil", result=W.NotSent("not answering"))
        for s in (short, fqdn):
            s.host, s.instance_uuid = s.name, "same-instance"
        clock, tracker = Clock(), M.Tracker()
        collector = W.Collector([short, fqdn], clock=clock)
        collector.cycle()
        first = M.build(collector.view(), None, None, {}, tracker, clock())
        original = next(c for c in first["conds"] if c["kind"] == "host")
        clock.t += 60
        fqdn.result = {"hosts": [down]}
        collector.cycle()
        answer = M.build(collector.view(), None, None, {}, tracker, clock())
        alert, = [c for c in answer["conds"] if c["end"] is None]
        self.assertEqual((alert["id"], alert["start"]), ("host:vc.example.mil:host1", original["start"]))
        self.assertEqual(set(answer["feeds"]), {"vc:vc.example.mil"})

    def test_volume_root_property_requested_only_on_supported_versions(self):
        for major in [6, 7, 14]:
            s = W.OntapSource("array.example.mil", "reader", "example", verify=False)
            def get(path):
                if path.startswith("/api/cluster?"):
                    return {"name": "array", "version": {"generation": 9, "major": major}}
                return {"records": []}
            s._get = Mock(side_effect=get)
            s.poll()
            volume_path, = [call.args[0] for call in s._get.call_args_list if call.args[0].startswith("/api/storage/volumes?")]
            self.assertIn(",type,", volume_path)
            self.assertEqual("is_svm_root" in volume_path, major >= 7)

    def test_alarm_names_are_one_targeted_read(self):
        alarm_state = "<AlarmState><alarm type='Alarm'>alarm-1</alarm><overallStatus>red</overallStatus></AlarmState>"
        host = ("HostSystem", "host-1", [("name", "xsd:string", "host1", ""), ("overallStatus", "ManagedEntityStatus", "red", ""),
                                         ("triggeredAlarmState", "ArrayOfAlarmState", alarm_state, "")])
        alarm = ("Alarm", "alarm-1", [("info.name", "xsd:string", "Fan sensor", "")])
        def response(row):
            return ET.fromstring('<response xmlns="urn:vim25" xmlns:xsi="%s"><returnval>%s</returnval></response>' % (W.NS["xsi"], obj_xml(row)))
        src = W.VcenterSource("vc.example.mil", "reader", "example", verify=False)
        src.sc, src.view = {"propertyCollector": "collector"}, "view"
        src._send = Mock(side_effect=[response(host), response(alarm)])
        h, = W.shape(src._retrieve())["hosts"]
        self.assertEqual((h["hardware"], h["alarms"]), ("red", ["Fan sensor"]))
        first, second = src._send.call_args_list
        for prop in ("overallStatus", "triggeredAlarmState", "guest.toolsRunningStatus", "guest.toolsVersionStatus2",
                     "config.template", "config.managedBy.type", "config.managedBy.extensionKey", "summary.multipleHostAccess"):
            self.assertIn("<pathSet>%s</pathSet>" % prop, first.args[1])
        self.assertEqual(second.args[0], "RetrievePropertiesEx")
        self.assertIn('<obj type="Alarm">alarm-1</obj>', second.args[1])
        self.assertEqual(second.args[1].count("<pathSet>"), 1)
        self.assertIn("<pathSet>info.name</pathSet>", second.args[1])


if __name__ == "__main__":
    unittest.main()
