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
        # 1.131.1 (Codex #7): vCLS by its managedBy only - a VM merely named vCLS-... is a workload
        self.assertEqual(sorted(c["vm"] for c in self.active(answer, "vmhung")), ["vCLS-01", "workload"])
        self.assertEqual(sorted(c["obj_name"] for c in self.active(answer, "cpu")), ["vCLS-01", "workload"])

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
        # 1.131.1 (Codex #3, #4): whole-name ignore globs (CONTENT-a and my-Library are workloads now); sharing not read
        # still alerts on the measured fill
        self.assertEqual(sorted(d["n"] for d in answer["storage"]["ds"]), ["CONTENT-a", "my-Library", "unread", "work"])
        self.assertEqual(sorted(c["ds"] for c in self.active(answer, "dsfull")), ["CONTENT-a", "my-Library", "unread", "work"])
        self.assertEqual([c["ds"] for c in self.active(answer, "dsi")], ["local"])
        self.assertEqual([x["reason"] for x in answer["vmware"]["excluded"]], ["local - not watched", "portal_wall_ds_ignore - not watched"])
        self.assertFalse(next(a for a in answer["areas"] if a["id"] == "storage")["complete"])
        stores[-2]["shared"] = None                     # Codex #3: sharing not read - the measured fill still alerts, live
        work = [c for c in self.active(self.read(), "dsfull") if c["ds"] == "work"][0]
        self.assertFalse(work.get("last_known"))
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


class Codex1311(WallTuning):
    """1.131.1: Codex's review of the wall tuning - one test per wall finding (each fails before its fix)"""

    def test_01_a_short_name_is_never_dropped_before_it_is_read(self):
        sources = W.sources_from_config({"vcenter": {"user": "reader", "password": "example", "verify": False,
                                                     "hosts": ["vc1", "vc1.other.example.mil"]}})
        self.assertEqual([s.name for s in sources], ["vc1", "vc1.other.example.mil"])

    def test_02_one_instance_uuid_with_another_inventory_is_kept(self):
        a = Fake("vcenter:vc-a.example.mil", result={"hosts": [demo.host("host-a", "c", 5, 5, [], [])]})
        b = Fake("vcenter:vc-b.example.mil", result={"hosts": [demo.host("host-b", "c", 5, 5, [], [])]})
        for s, h in ((a, "host-a"), (b, "host-b")):
            s.host, s.instance_uuid = s.name, "cloned-uuid"
            s.result["hosts"][0]["id"] = h
        collector = W.Collector([a, b], clock=self.clock)
        collector.cycle()
        collector.cycle()
        self.assertEqual(sorted(collector.view()["sources"]), ["vcenter:vc-a.example.mil", "vcenter:vc-b.example.mil"])
        # the service content (instanceUuid) is read again at every login: a repointed name is not its predecessor
        src = W.VcenterSource("vc.example.mil", "reader", "example", verify=False)
        uuids = iter(["first", "second"])

        def reply(method, args):
            if method == "Login":
                src.cookie = "example"
            content = ("<about><instanceUuid>%s</instanceUuid></about><sessionManager>sm</sessionManager>" % next(uuids)
                       if method == "RetrieveServiceContent" else "<key>k</key>" if method == "Login"
                       else "view" if method == "CreateContainerView" else "")
            return ET.fromstring('<%sResponse xmlns="urn:vim25"><returnval>%s</returnval></%sResponse>' % (method, content, method))
        src._send = Mock(side_effect=reply)
        src._login()
        src._login()
        self.assertEqual(src.instance_uuid, "second")

    def ds_answer(self, **ds):
        self.vc.result = {"datastores": [dict({"name": "work", "capacity": 100, "free": 0, "accessible": True}, **ds)]}
        return self.read()

    def test_03_sharing_not_read_still_alerts_on_the_fill(self):
        answer = self.ds_answer(shared=None, hosts=["h1", "h2"])
        self.assertEqual([c["ds"] for c in self.active(answer, "dsfull")], ["work"])
        self.assertEqual([d["n"] for d in answer["storage"]["ds"]], ["work"])

    def test_04_the_default_ignore_globs_are_whole_names(self):
        answer = self.ds_answer(name="prod-isolated", shared=True)
        self.assertEqual([c["ds"] for c in self.active(answer, "dsfull")], ["prod-isolated"])
        self.assertFalse(self.active(self.ds_answer(name="ISO-images", shared=True), "dsfull") and
                         [c for c in self.active(self.read(), "dsfull") if c["ds"] == "ISO-images"])

    def test_05_a_hang_across_a_portal_restart_stays_watched(self):
        import os
        import shutil
        import tempfile
        d = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        self.addCleanup(shutil.rmtree, d, True)
        state = os.path.join(d, "wall-backoff.json")
        import secrets
        import test_netapp_live  # noqa: F401  (its paths: the portal's modules)
        import site_portal as sp
        sp.write_wall_token(d, secrets.token_hex(16))             # as Site portal - deploy starts the journal
        vm = self.vm("app1", tools="guestToolsRunning", heartbeat="green")
        vc = Fake(result={"vms": [vm]})
        first = W.Collector([vc], clock=self.clock, state_file=state)
        first.cycle()
        self.assertTrue(os.path.exists(os.path.join(d, "wall-tools-seen.json")))
        vm.update(tools="guestToolsNotRunning", heartbeat="red")
        first.stop()
        again = W.Collector([vc], clock=self.clock, state_file=state)      # the portal restarted
        self.clock.t += 60
        again.cycle()
        answer = M.build(again.view(), None, None, {}, M.Tracker(), self.clock())
        self.assertEqual([c["vm"] for c in self.active(answer, "vmhung")], ["app1"])

    def test_06_a_later_read_never_unwatches_a_known_vm(self):
        vm = self.vm("app1", tools="guestToolsRunning", heartbeat="green")
        self.vc.result = {"vms": [vm]}
        self.read()
        vm.update(tools="guestToolsNotRunning", heartbeat="red", template=None, tools_version="guestToolsNotInstalled")
        self.assertEqual([c["vm"] for c in self.active(self.read(), "vmhung")], ["app1"])

    def test_07_a_vm_named_vcls_without_the_vcls_identity_is_watched(self):
        vm = self.vm("vCLS-payroll", tools="guestToolsRunning", heartbeat="green")
        self.vc.result = {"vms": [vm]}
        self.read()
        vm.update(tools="guestToolsNotRunning", heartbeat="red")
        self.assertEqual([c["vm"] for c in self.active(self.read(), "vmhung")], ["vCLS-payroll"])

    def test_08_an_unnamed_svm_never_carries_a_root_exclusion(self):
        raw = {"aggrs": [], "snapmirror": [],
               "volumes": [{"name": "data", "is_svm_root": True, "state": "online"}],
               "luns": [{"name": "/vol/data/lun0", "location": {"volume": {"name": "data"}}, "status": {"state": "offline"}}]}
        source = W.OntapSource("array.example.mil", "reader", "example", verify=False)
        source._read = Mock(return_value=({"name": "array"}, [], raw, {}))
        self.storage.result = source.poll()
        self.assertEqual([c["obj_name"] for c in self.active(self.read(), "voloff")], ["/vol/data/lun0"])

    def test_09_a_last_known_snapmirror_condition_stays_on_the_panel(self):
        self.storage.result["snapmirror"] = [{"dst": "svm:vol", "state": "in_sync", "healthy": True, "lag_s": 10 ** 6}]
        self.assertTrue(self.active(self.read(), "sm"))
        self.storage.result["snapmirror"] = []
        answer = self.read()
        self.assertTrue(self.active(answer, "sm"))
        self.assertEqual([b["dst"] for b in answer["storage"]["smBroken"]], ["svm:vol"])

    def test_10_an_alarm_name_that_cannot_be_read_keeps_the_inventory(self):
        alarm_state = "<AlarmState><alarm type='Alarm'>alarm-1</alarm><overallStatus>red</overallStatus></AlarmState>"
        host = ("HostSystem", "host-1", [("name", "xsd:string", "host1", ""), ("runtime.connectionState", "HostSystemConnectionState", "disconnected", ""),
                                         ("triggeredAlarmState", "ArrayOfAlarmState", alarm_state, "")])

        def response(row):
            return ET.fromstring('<response xmlns="urn:vim25" xmlns:xsi="%s"><returnval>%s</returnval></response>' % (W.NS["xsi"], obj_xml(row)))
        src = W.VcenterSource("vc.example.mil", "reader", "example", verify=False)
        src.sc, src.view = {"propertyCollector": "collector"}, "view"
        src._send = Mock(side_effect=[response(host), TimeoutError("timed out")])
        h, = W.shape(src._retrieve())["hosts"]
        self.assertEqual((h["name"], h["state"]), ("host1", "disconnected"))
        self.assertEqual(h["triggered"], [[None, "red"]])

    def test_11_host_hardware_only_from_hardware_alarms(self):
        host = demo.host("host1", "cluster", 10, 20, [], [])
        host.pop("hardware", None)
        self.vc.result = {"hosts": [host]}
        host["triggered"] = [["Host CPU usage", "red"]]                     # a usage alarm is not hardware
        self.assertFalse(self.active(self.read(), "hardware"))
        host["triggered"] = [["Host hardware fan status", "yellow"]]
        self.assertEqual([c["sev"] for c in self.active(self.read(), "hardware")], ["warn"])
        host["triggered"] = [[None, "red"]]                                 # a name not read: grey, never green
        answer = self.read()
        self.assertTrue(self.active(answer, "hardware")[0]["last_known"])
        host["triggered"] = []
        self.assertFalse(self.active(self.read(), "hardware"))


    def test_field_a_short_credential_name_and_its_fqdn_are_one_vcenter(self):
        import site_portal_filters as F
        line = F.site_portal_wall_summary({"vc_on": True, "vc_host": "vc01", "vcenters": ["vc01.corp.example.mil", "vc02.corp.example.mil"]})
        self.assertIn("VMware on (vCenter vc01 is vc01.corp.example.mil: read once)", line)
        self.assertNotIn("read once", F.site_portal_wall_summary({"vc_on": True, "vc_host": "vc01", "vcenters": ["vc01.corp.example.mil:8443"]}))
        log = []
        short = Fake("vcenter:vc01", result={"hosts": [dict(demo.host("h1", "c", 5, 5, [], []), id="host-1")]})
        full = Fake("vcenter:vc01.corp.example.mil", result={"hosts": [dict(demo.host("h1", "c", 5, 5, [], []), id="host-1")]})
        for s in (short, full):
            s.host, s.instance_uuid = s.name, "one-uuid"
        collector = W.Collector([short, full], clock=self.clock, log=log.append)
        collector.cycle()
        collector.cycle()
        self.assertEqual(sorted(collector.view()["sources"]), ["vcenter:vc01.corp.example.mil"])
        self.assertIn("vCenter vc01 is vc01.corp.example.mil: read once (same instanceUuid)", [x.get("why") for x in log])
        other = Fake("vcenter:vc02", result=dict(short.result))
        other.host, other.instance_uuid = "vc02", "another-uuid"                 # never merged: another instanceUuid
        c2 = W.Collector([other, full], clock=self.clock)
        c2.cycle()
        self.assertEqual(sorted(c2.view()["sources"]), ["vcenter:vc01.corp.example.mil", "vcenter:vc02"])


    def test_field_vms_without_tools_never_grey_vmware(self):
        self.vc.result = {"hosts": [dict(demo.host("h1", "c", 5, 5, [], []), id="host-1")],
                          "vms": [self.vm("appliance", tools_version="guestToolsNotInstalled", tools="guestToolsNotRunning", heartbeat="gray"),
                                  self.vm("legacy", tools="guestToolsNotRunning", heartbeat="gray", id="vm-9")]}
        for _ in range(3):
            answer = self.read()
        vmware = next(a for a in answer["areas"] if a["id"] == "vmware")
        self.assertTrue(vmware["complete"], vmware.get("incomplete"))
        self.assertFalse(self.active(answer, "vmhung"))
        self.assertEqual(answer["vmware"]["heartbeat_unwatched"], 2)

    def test_field_a_status_words_icon_is_text_sized_everywhere(self):
        import os
        css = open(os.path.join(os.path.dirname(M.__file__), "static", "wall-page.css")).read()
        self.assertIn(".word svg{width:1em;height:1em;flex:none;vertical-align:-0.125em}", css)
        self.assertIn(".sK .word svg{width:15px;height:15px;flex:none}", css)          # Cockpit keeps its 15 px


for _n in [n for n in vars(WallTuning) if n.startswith("test_")]:
    setattr(Codex1311, _n, None)                    # (it shares WallTuning's setup; only its own tests run there)


class CollectorReads(unittest.TestCase):
    def test_names_and_authenticated_uuid_merge(self):
        log = []
        sources = W.sources_from_config({"vcenter": {"user": "reader", "password": "example", "verify": False,
                           "hosts": ["VC01", "Alias", "vc01.example.mil", "VC01.EXAMPLE.MIL", "vc01:8443"]}}, log=log.append)
        # 1.131.1 (Codex #1): no source dropped by its name before it was read - the short one is merged at run time only
        self.assertEqual([s.name for s in sources], ["vc01", "alias", "vc01.example.mil", "vc01:8443"])
        self.assertFalse([x for x in log if x.get("state") == "duplicate short name"])
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
        self.assertEqual(h["triggered"], [["Fan sensor", "red"]])
        self.assertEqual(M.hardware_of(dict(h), M.opts_of({"hw_alarms": ["fan sensor"]})), "red")       # whole name, any case
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
