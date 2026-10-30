#!/usr/bin/env python3
"""Unit tests for the pure parts of roles/vmware_vm/library (needs ansible-core importable, not
pyVmomi or a vCenter): python3 tests/test_vmware_modules.py"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "roles", "vmware_vm", "library"))
# the modules import their shared vCenter code as Ansible does: from ansible.module_utils.site_vcenters
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("ansible.module_utils.site_vcenters", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "roles", "vmware_vm", "module_utils", "site_vcenters.py"))
site_vcenters = importlib.util.module_from_spec(_spec)
sys.modules["ansible.module_utils.site_vcenters"] = site_vcenters
_spec.loader.exec_module(site_vcenters)
import site_vmware_snapshots as m  # noqa: E402
import site_vmware_alarms as al  # noqa: E402
import site_vmware_esxi_security as es  # noqa: E402
import site_vmware_health as hl  # noqa: E402
import site_vmware_vm_created as vc  # noqa: E402
import site_vmware_certs as vcert  # noqa: E402
import site_vmware_watch as wt  # noqa: E402

G = 1024 ** 3


class SnapshotSizes(unittest.TestCase):
    # files: 1 base disk, 2 = delta after s1, 3 = delta after s2 (running state), 10/11 state files, 12 memory of s2
    FILES = {1: 100 * G, 2: 3 * G, 3: 5 * G, 10: 1024, 11: 2048, 12: 4 * G}

    def test_chain_of_two(self):
        layouts = {"s1": {"data": 10, "memory": -1, "disks": {2000: [[1]]}},
                   "s2": {"data": 11, "memory": 12, "disks": {2000: [[1], [2]]}}}
        running = {2000: [[1], [2], [3]]}
        sizes = m.snapshot_sizes(self.FILES, layouts, running, {None: ["s1"], "s1": ["s2"], "s2": []}, "s2")
        self.assertEqual(sizes["s1"], 1024 + 3 * G)          # its state file + the delta written after it (until s2)
        self.assertEqual(sizes["s2"], 2048 + 4 * G + 5 * G)  # state + memory + the delta of the running state

    def test_reverted_branch(self):
        # running on s1 again (reverted), s2 is a leaf off the path: it keeps only its own files
        layouts = {"s1": {"data": 10, "memory": -1, "disks": {2000: [[1]]}},
                   "s2": {"data": 11, "memory": -1, "disks": {2000: [[1], [2]]}}}
        running = {2000: [[1], [3]]}
        sizes = m.snapshot_sizes(self.FILES, layouts, running, {None: ["s1"], "s1": ["s2"], "s2": []}, "s1")
        self.assertEqual(sizes["s1"], 1024 + 5 * G)
        self.assertEqual(sizes["s2"], 2048)

    def test_two_disks_and_missing_files(self):
        layouts = {"s1": {"data": 99, "memory": None, "disks": {2000: [[1]], 2001: [[1]]}}}
        running = {2000: [[1], [2]], 2001: [[1], [3, 2]]}
        sizes = m.snapshot_sizes(self.FILES, layouts, running, {None: ["s1"], "s1": []}, "s1")
        self.assertEqual(sizes["s1"], 3 * G + 5 * G + 3 * G)   # unknown state file key counts 0

    def test_no_layout(self):
        self.assertEqual(m.snapshot_sizes({}, {}, {}, {}, None), {})


class SnapshotCreators(unittest.TestCase):
    def test_matching(self):
        snaps = [("s1", 10_000), ("s2", 10_030), ("s3", 50_000), ("s4", 90_000)]
        events = [(9_990, "CORP\\alice"), (10_025, "svc_aap"), (49_000, "CORP\\bob"), (95_000, "late")]
        self.assertEqual(m.snapshot_creators(snaps, events),
                         {"s1": "CORP\\alice", "s2": "svc_aap", "s3": "CORP\\bob", "s4": ""})

    def test_each_event_once_and_no_events(self):
        self.assertEqual(m.snapshot_creators([("a", 100), ("b", 101)], [(99, "u")]), {"a": "u", "b": ""})
        self.assertEqual(m.snapshot_creators([("a", 100)], []), {"a": ""})


class VmCreated(unittest.TestCase):
    DAY = 86400.0
    NOW = 1760000000.0
    SINCE = NOW - 7 * 86400

    def ev(self, t, typ, moid, name, user="CORP\\jdoe", source=""):
        return {"type": typ, "time": self.NOW - t * self.DAY, "user": user, "moid": moid, "name": name, "source": source, "vcenter": "vc1"}

    def test_the_week_by_creation_date_and_events(self):
        vms = [{"moid": "vm-1", "name": "web01", "created": self.NOW - 2 * self.DAY, "template": False,
                "annotation": "Deployed from rhel9-gold for INC0012345 by AAP job 42 (operator1), 2026-10-09 UTC.", "vcenter": "vc1"},
               {"moid": "vm-2", "name": "old01", "created": self.NOW - 30 * self.DAY, "template": False, "annotation": "", "vcenter": "vc1"},
               {"moid": "vm-3", "name": "tpl", "created": self.NOW - 1 * self.DAY, "template": True, "annotation": "", "vcenter": "vc1"},
               {"moid": "vm-4", "name": "db01", "created": None, "template": False, "annotation": "", "vcenter": "vc1"}]
        events = [self.ev(2, "VmCreatedEvent", "vm-1", "web01", "VSPHERE.LOCAL\\svc_aap"),
                  self.ev(2, "VmDeployedEvent", "vm-1", "web01", "VSPHERE.LOCAL\\svc_aap", "rhel9-gold"),
                  self.ev(3, "VmClonedEvent", "vm-4", "db01", source="db00"),
                  self.ev(5, "VmCreatedEvent", "vm-9", "scratch01"), self.ev(4, "VmRemovedEvent", "vm-9", "scratch01"),
                  self.ev(9, "VmCreatedEvent", "vm-8", "gone-before")]
        rows = vc.created_rows(vms, events, self.SINCE, self.NOW)
        self.assertEqual([r["vm"] for r in rows], ["scratch01", "db01", "web01"])      # oldest first; not old01, the template, gone-before
        web = rows[2]
        self.assertEqual((web["how"], web["by"], web["ticket"], web["template"], web["aap"], web["deleted"]),
                         ("deployed from template rhel9-gold", "VSPHERE.LOCAL\\svc_aap", "INC0012345", "rhel9-gold", True, False))
        db = rows[1]   # no creation date (before vSphere 6.7): its event's time
        self.assertEqual((db["how"], db["by"], db["aap"]), ("cloned from db00", "CORP\\jdoe", False))
        self.assertTrue(db["created"].endswith("Z"))
        gone = rows[0]
        self.assertEqual((gone["deleted"], gone["how"], gone["removed"] != ""), (True, "created new", True))

    def test_the_ticket_and_template_from_the_notes(self):
        new = "CTASK0010009 - built 2026-10-20\nTemplate: Windows 2022 gold\nAAP job 42 (operator1)\nService: SQL Server"
        self.assertEqual(vc.aap_note(new), ("CTASK0010009", "Windows 2022 gold"))
        self.assertEqual(vc.aap_note("CTASK0010009 - building 2026-10-20"), ("CTASK0010009", ""))   # no Template line (edited)
        self.assertEqual(vc.aap_note("Patched 2026-10-20\n" + new), ("CTASK0010009", "Windows 2022 gold"))  # a line added on top
        old = "Deployed from rhel9-gold for INC0012345 by AAP job 42 (operator1), 2026-10-09 UTC.\n[site-automation ticket=INC0012345 state=built]"
        self.assertEqual(vc.aap_note(old), ("INC0012345", "rhel9-gold"))
        for other in ("", "Web server - built by hand", "db - built 2026-10-20 by Bob", "Template: x"):
            self.assertIsNone(vc.aap_note(other), other)
        rows = vc.created_rows([{"moid": "vm-1", "name": "sql01", "created": self.NOW - self.DAY, "template": False, "annotation": new}], [],
                               self.SINCE, self.NOW)
        self.assertEqual((rows[0]["ticket"], rows[0]["template"], rows[0]["aap"]), ("CTASK0010009", "Windows 2022 gold", True))

    def test_no_events_a_vm_still_counts_by_its_date(self):
        rows = vc.created_rows([{"moid": "vm-1", "name": "a", "created": self.NOW - self.DAY, "template": False, "annotation": ""}], [],
                               self.SINCE, self.NOW)
        self.assertEqual((len(rows), rows[0]["by"], rows[0]["how"]), (1, "", ""))
        self.assertEqual(vc.created_rows([], [], self.SINCE, self.NOW), [])


class AlarmEvents(unittest.TestCase):
    def test_category(self):
        self.assertEqual(al.category_of("X", "warning"), "warning")
        self.assertEqual(al.category_of("com.vmware.x", "", "error"), "error")       # an extended event's severity
        self.assertEqual(al.category_of("GeneralHostErrorEvent"), "error")            # vCenter did not say: from the name
        self.assertEqual(al.category_of("GeneralHostWarningEvent"), "warning")
        self.assertEqual(al.category_of("HostConnectionLostEvent"), "error")
        self.assertEqual(al.category_of("VmPoweredOnEvent"), "info")

    def test_classify(self):
        self.assertEqual(al.classify("BadUsernameSessionEvent", "info"), ("login", "warning"))
        self.assertEqual(al.classify("HostNotRespondingEvent", "error"), ("connection", "critical"))
        self.assertEqual(al.classify("HostShutdownEvent", "info"), ("connection", "info"))
        self.assertEqual(al.classify("VmGuestShutdownEvent", "info", "CORP\\admin"), ("vm", "info"))
        self.assertEqual(al.classify("VmPoweredOffEvent", "info", ""), ("vm", "warning"))      # nobody asked
        self.assertEqual(al.classify("VmPoweredOffEvent", "info", "CORP\\admin"), ("vm", "info"))
        self.assertEqual(al.classify("VmFailoverFailed", "error"), ("vm", "critical"))
        self.assertEqual(al.classify("com.vmware.vc.ha.VmRestartedByHAEvent", "warning"), ("vm", "warning"))
        self.assertEqual(al.classify("SomethingErrorEvent", "error"), ("other", "critical"))
        self.assertEqual(al.classify("SomethingEvent", "warning"), ("other", "warning"))
        self.assertEqual(al.classify("VmPoweredOnEvent", "info"), (None, None))                # not wanted
        self.assertEqual(al.classify("BadUsernameSessionEvent", "info", login_types=[]), (None, None))   # list emptied

    def test_login_parts(self):
        self.assertEqual(al.login_parts("Cannot login svc_scan@192.0.2.3"), ("svc_scan", "192.0.2.3"))
        self.assertEqual(al.login_parts("", "u1", "192.0.2.1"), ("u1", "192.0.2.1"))
        self.assertEqual(al.login_parts("Login failed", args={"userName": "a@vsphere.local", "client": "192.0.2.9"}),
                         ("a@vsphere.local", "192.0.2.9"))
        self.assertEqual(al.login_parts("no address here"), ("", ""))

    def test_event_record(self):
        r = al.event_record({"key": 1, "type": "BadUsernameSessionEvent", "message": "", "login_user": "svc", "ip": "192.0.2.99",
                             "host": "esx01", "time": "2026-10-05T03:52:47Z"})
        self.assertEqual((r["group"], r["severity"], r["message"], r["login_user"], r["ip"]),
                         ("login", "warning", "Cannot login svc@192.0.2.99", "svc", "192.0.2.99"))
        r = al.event_record({"key": 2, "type": "HostConnectionLostEvent", "host": "esx02", "message": None})
        self.assertEqual((r["group"], r["severity"], r["message"]), ("connection", "critical", "Host esx02 lost its connection to vCenter"))
        self.assertIsNone(al.event_record({"key": 3, "type": "VmPoweredOnEvent", "category": "info"}))
        r = al.event_record({"key": 4, "type": "esx.problem.storage.connectivity.lost", "severity": "error", "message": "Lost  connectivity\n to x"})
        self.assertEqual((r["group"], r["severity"], r["message"]), ("other", "critical", "Lost connectivity to x"))

    def test_alarm_records(self):
        names = {"host-1": {"name": "esx01", "cluster": "CL1", "datacenter": "DC1"}, "group-d1": {"name": "vc", "cluster": "", "datacenter": ""}}
        defs = {"alarm-1": {"name": "Host connection and power state", "description": "d"}, "alarm-2": {"name": "vSphere Health", "description": ""}}
        states = [{"key": "k1", "kind": "host", "moid": "host-1", "alarm": "alarm-1", "status": "yellow", "time": "2026-10-05T01:00:00Z"},
                  {"key": "k1", "kind": "host", "moid": "host-1", "alarm": "alarm-1", "status": "yellow", "time": "2026-10-05T01:00:00Z"},
                  {"key": "k2", "kind": "vcenter", "moid": "group-d1", "alarm": "alarm-2", "status": "red", "time": "2026-10-04T01:00:00Z",
                   "acknowledged": True, "acknowledged_by": "CORP\\op"},
                  {"key": "k3", "kind": "vm", "moid": "vm-9", "alarm": "alarm-1", "status": "red", "time": "2026-10-05T02:00:00Z"},
                  {"key": "k4", "kind": "host", "moid": "host-1", "alarm": "alarm-1", "status": "green", "time": ""}]
        out = al.alarm_records(states, names, defs, ["vcenter", "datacenter", "cluster", "host"])
        self.assertEqual([(a["entity"], a["severity"], a["alarm"]) for a in out],       # deduplicated, no VM, no green, red first
                         [("vc", "critical", "vSphere Health"), ("esx01", "warning", "Host connection and power state")])
        self.assertEqual((out[1]["cluster"], out[1]["datacenter"], out[0]["acknowledged"], out[0]["acknowledged_by"]),
                         ("CL1", "DC1", True, "CORP\\op"))
        self.assertEqual(len(al.alarm_records(states, names, defs, ["vm"])), 1)


class AlarmDefaultsMatchModule(unittest.TestCase):
    def test_event_type_lists(self):
        import yaml
        with open(os.path.join(os.path.dirname(__file__), "..", "roles", "vmware_vm", "defaults", "main.yml")) as fh:
            d = yaml.safe_load(fh)
        self.assertEqual(d["vm_alarm_login_event_types"], al.LOGIN_EVENTS)
        self.assertEqual(d["vm_alarm_connection_event_types"], al.CONNECTION_EVENTS)
        self.assertEqual(d["vm_alarm_vm_event_types"], al.VM_EVENTS)


class FakeHost(object):
    """An ESXi host for secure_host(): its state, and every call that would change it."""
    MUTATING = ("stop_service", "start_service", "set_policy", "set_option", "set_lockdown", "set_exceptions")

    def __init__(self, ssh=(True, "on"), opts=None, lockdown="disabled", exceptions=None, fail=None):
        self.svc = {"TSM-SSH": ssh, "TSM": (False, "off")}
        self.opts = dict(opts if opts is not None else {"UserVars.ESXiShellTimeOut": 0, "UserVars.ESXiShellInteractiveTimeOut": 0})
        self.mode, self.exc, self.calls, self.fail = lockdown, list(exceptions or []), [], fail or {}

    def _call(self, name, *a):
        self.calls.append((name,) + a)
        if name in self.fail:
            raise self.fail[name]

    def services(self):
        return dict(self.svc)

    def stop_service(self, k):
        self._call("stop_service", k)
        self.svc[k] = (False, self.svc[k][1])

    def start_service(self, k):
        self._call("start_service", k)
        self.svc[k] = (True, self.svc[k][1])

    def set_policy(self, k, p):
        self._call("set_policy", k, p)
        self.svc[k] = (self.svc[k][0], p)

    def option(self, k):
        if k not in self.opts:
            raise KeyError(k)
        return self.opts[k]

    def set_option(self, k, v):
        self._call("set_option", k, v)
        self.opts[k] = int(v)

    def lockdown(self):
        return self.mode

    def set_lockdown(self, m):
        self._call("set_lockdown", m)
        self.mode = m

    def exceptions(self):
        return list(self.exc)

    def set_exceptions(self, users):
        self._call("set_exceptions", list(users))
        self.exc = list(users)


class NoPermission(Exception):
    privilegeId = "Host.Config.Settings"


WANT = {"services": {"TSM-SSH": "disabled"}, "labels": {"TSM-SSH": "SSH", "TSM": "ESXi Shell"},
        "settings": {"UserVars.ESXiShellTimeOut": 600, "UserVars.ESXiShellInteractiveTimeOut": "600"},
        "lockdown": "normal", "exception_users": ["svc_scan", "svc_mon"]}


class EsxiSecurity(unittest.TestCase):
    def test_dry_run_changes_nothing(self):
        h = FakeHost(exceptions=["svc_mon"])
        r = es.secure_host(h, WANT, check_mode=True)
        self.assertEqual(h.calls, [])                                    # not one change
        self.assertEqual(r["changes"], ["SSH stopped and set to start manually", "UserVars.ESXiShellInteractiveTimeOut 0 -> 600",
                                        "UserVars.ESXiShellTimeOut 0 -> 600", "lockdown exception users added: svc_scan",
                                        "lockdown disabled -> normal"])
        self.assertEqual(r["before"]["SSH"], "running, starts with the host")

    def test_apply_in_order_then_nothing_to_do(self):
        h = FakeHost(exceptions=["SVC_MON"])
        r = es.secure_host(h, WANT)
        self.assertEqual(r["errors"], [])
        names = [c[0] for c in h.calls]
        self.assertEqual(names[:2], ["stop_service", "set_policy"])
        self.assertLess(names.index("set_exceptions"), names.index("set_lockdown"))        # exceptions BEFORE lockdown
        self.assertIn(("set_exceptions", ["SVC_MON", "svc_scan"]), h.calls)                  # added, never removed (any case)
        self.assertEqual((h.svc["TSM-SSH"], h.mode, h.opts["UserVars.ESXiShellTimeOut"]), ((False, "off"), "normal", 600))
        h.calls = []
        again = es.secure_host(h, WANT)
        self.assertEqual((h.calls, again["changes"], again["errors"]), ([], [], []))         # a second run: nothing

    def test_strict_is_left_alone(self):
        h = FakeHost(ssh=(False, "off"), opts={"UserVars.ESXiShellTimeOut": 600, "UserVars.ESXiShellInteractiveTimeOut": 600},
                     lockdown="strict", exceptions=["svc_scan", "svc_mon"])
        r = es.secure_host(h, WANT)
        self.assertEqual((h.calls, r["changes"]), ([], []))
        self.assertEqual(r["notes"], ["lockdown is strict: left as it is (stricter than normal)"])

    def test_missing_privilege_is_named_and_the_rest_still_runs(self):
        h = FakeHost(fail={"set_option": NoPermission()})
        r = es.secure_host(h, dict(WANT, exception_users=[]))
        self.assertEqual(r["errors"][0], "UserVars.ESXiShellInteractiveTimeOut: the vCenter account lacks the privilege "
                                         "Host.Config.Settings on this host")
        self.assertEqual(h.mode, "normal")                                                 # lockdown still set
        self.assertEqual(h.svc["TSM-SSH"], (False, "off"))

    def test_unknown_setting_and_failed_exceptions(self):
        h = FakeHost(fail={"set_exceptions": RuntimeError("boom")})
        r = es.secure_host(h, dict(WANT, settings={"UserVars.NoSuch": 1}))
        self.assertIn("UserVars.NoSuch: the host has no such setting", r["errors"])
        self.assertTrue(any(e.startswith("lockdown exception users: RuntimeError: boom - lockdown left disabled") for e in r["errors"]))
        self.assertEqual(h.mode, "disabled")                       # not locked: the exception users could not be added first

    def test_enable_and_disable_lockdown(self):
        h = FakeHost(ssh=(False, "off"), lockdown="normal")
        r = es.secure_host(h, {"services": {"TSM-SSH": "enabled"}, "labels": {"TSM-SSH": "SSH"}, "lockdown": "disabled"})
        self.assertEqual([c[0] for c in h.calls], ["set_policy", "start_service", "set_lockdown"])
        self.assertEqual(r["changes"], ["SSH started and set to start with the host", "lockdown normal -> disabled"])

    def test_ssh_and_shell_both_off(self):
        h = FakeHost()
        h.svc["TSM"] = (True, "on")
        r = es.secure_host(h, {"services": {"TSM-SSH": "disabled", "TSM": "disabled"}, "labels": {"TSM-SSH": "SSH", "TSM": "ESXi Shell"}})
        self.assertEqual((h.svc["TSM-SSH"], h.svc["TSM"]), ((False, "off"), (False, "off")))
        self.assertEqual(sorted(r["changes"]), ["ESXi Shell stopped and set to start manually", "SSH stopped and set to start manually"])
        self.assertEqual(r["before"]["ESXi Shell"], "running, starts with the host")

    def test_same_value_and_faults(self):
        self.assertTrue(es.same_value(600, "600"))
        self.assertFalse(es.same_value(0, 600))
        self.assertTrue(es.same_value(True, "true"))
        self.assertFalse(es.same_value(600, "ten"))
        self.assertTrue(es.same_value("info", "info"))
        self.assertEqual(es.fault_text(RuntimeError("x  y")), "RuntimeError: x y")

    def test_pick_hosts(self):
        hs = [{"name": "esx01.corp.example.mil", "cluster": "PROD", "datacenter": "DC1"}, {"name": "esx02.corp.example.mil", "cluster": "LAB", "datacenter": "DC1"},
              {"name": "esx03.corp.example.mil", "cluster": "PROD", "datacenter": "DC2"}]
        self.assertEqual([h["name"] for h in es.pick_hosts(hs)[0]], ["esx01.corp.example.mil", "esx02.corp.example.mil", "esx03.corp.example.mil"])
        self.assertEqual([h["name"] for h in es.pick_hosts(hs, names=["ESX0[12]"])[0]], ["esx01.corp.example.mil", "esx02.corp.example.mil"])
        self.assertEqual([h["name"] for h in es.pick_hosts(hs, clusters=["prod"], datacenter="DC1")[0]], ["esx01.corp.example.mil"])
        picked, excluded = es.pick_hosts(hs, exclude=["esx02"])
        self.assertEqual(([h["name"] for h in picked], [h["name"] for h in excluded]),
                         (["esx01.corp.example.mil", "esx03.corp.example.mil"], ["esx02.corp.example.mil"]))


class _NS(object):
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FailoverLevelAdmissionControlPolicy(_NS):
    pass


class FailoverResourcesAdmissionControlPolicy(_NS):
    pass


class HealthFacts(unittest.TestCase):
    def test_ha(self):
        off = _NS(dasConfig=_NS(enabled=False))
        self.assertEqual(hl.ha_facts(off, None)["enabled"], False)
        self.assertEqual(hl.ha_facts(None, None)["enabled"], False)
        lvl = _NS(dasConfig=_NS(enabled=True, hostMonitoring="enabled", admissionControlEnabled=True,
                                admissionControlPolicy=FailoverLevelAdmissionControlPolicy(failoverLevel=1)))
        f = hl.ha_facts(lvl, _NS(admissionControlInfo=_NS(currentFailoverLevel=0)))
        self.assertEqual((f["admission"], f["admission_hosts"], f["failover_level_now"], f["host_monitoring"]), ("hosts", 1, 0, "enabled"))
        # older servers: the level from the summary
        self.assertEqual(hl.ha_facts(lvl, _NS(admissionControlInfo=None, currentFailoverLevel=2))["failover_level_now"], 2)
        pct = _NS(dasConfig=_NS(enabled=True, hostMonitoring="disabled", admissionControlEnabled=True,
                                admissionControlPolicy=FailoverResourcesAdmissionControlPolicy(cpuFailoverResourcesPercent=25,
                                                                                               memoryFailoverResourcesPercent=30)))
        f = hl.ha_facts(pct, _NS(admissionControlInfo=_NS(currentCpuFailoverResourcesPercent=60, currentMemoryFailoverResourcesPercent=20)))
        self.assertEqual((f["admission"], f["admission_cpu_pct"], f["admission_mem_pct"], f["cpu_left_pct"], f["mem_left_pct"]),
                         ("percent", 25, 30, 60, 20))
        noadm = _NS(dasConfig=_NS(enabled=True, admissionControlEnabled=False, admissionControlPolicy=None))
        self.assertEqual(hl.ha_facts(noadm, None)["admission"], "off")

    def test_drs(self):
        f = hl.drs_facts(_NS(drsConfig=_NS(enabled=True, defaultVmBehavior="manual")), _NS(drsScore=88), [1, 2], [_NS(faultsByVm=[1, 2, 3])])
        self.assertEqual(f, {"enabled": True, "behavior": "manual", "recommendations": 2, "faults": 3, "score": 88})
        self.assertEqual(hl.drs_facts(_NS(drsConfig=None), None, None, None),
                         {"enabled": False, "behavior": "", "recommendations": 0, "faults": 0, "score": None})

    def test_sensors(self):
        st = lambda k: _NS(key=k)
        rt = _NS(systemHealthInfo=_NS(numericSensorInfo=[
                     _NS(name="VMware Rollup Health State", healthState=st("red"), sensorType="system"),
                     _NS(name="Power  Supply 2", healthState=st("red"), sensorType="power"),
                     _NS(name="Fan 1", healthState=st("unknown"), sensorType="fan"),
                     _NS(name="Fan 3", healthState=st("Yellow"), sensorType="fan")]),
                 hardwareStatusInfo=_NS(memoryStatusInfo=[_NS(name="DIMM A1", status=st("Red"))], cpuStatusInfo=[_NS(name="CPU 0", status=st("Green"))],
                                        storageStatusInfo=None))
        self.assertEqual(hl.bad_sensors(rt), [{"name": "Power Supply 2", "state": "red", "type": "power"},
                                              {"name": "Fan 3", "state": "yellow", "type": "fan"},
                                              {"name": "DIMM A1", "state": "red", "type": "memory"}])
        # only the rollup is red: it is kept, so the fault is not lost
        only = _NS(systemHealthInfo=_NS(numericSensorInfo=[_NS(name="VMware Rollup Health State", healthState=st("red"), sensorType="system")]),
                   hardwareStatusInfo=None)
        self.assertEqual([x["name"] for x in hl.bad_sensors(only)], ["VMware Rollup Health State"])
        self.assertEqual(hl.bad_sensors(None), [])

    def test_ntp(self):
        svc = _NS(service=[_NS(key="TSM-SSH", running=True), _NS(key="ntpd", running=False)])
        dti = _NS(ntpConfig=_NS(server=["ntp1.example.mil", " ", "ntp2.example.mil"]), systemClockProtocol="ntp")
        self.assertEqual(hl.ntp_facts(dti, svc), (["ntp1.example.mil", "ntp2.example.mil"], "ntp", False))
        self.assertEqual(hl.ntp_facts(_NS(ntpConfig=_NS(server=None)), None), ([], "", None))     # none set; services not said
        self.assertEqual(hl.ntp_facts(None, _NS(service=[])), (None, "", None))                   # vCenter would not say (vcsim)

    def test_clock_offset(self):
        import datetime as dt
        ticks = iter([1000.0, 1000.4])                  # the call took 0.4 s: the host read at 1000.2
        at = dt.datetime.fromtimestamp(1090.2, tz=dt.timezone.utc)
        self.assertEqual(hl.clock_offset(lambda: at, 0.0, clock=lambda: next(ticks)), 90.0)
        ticks = iter([1000.0, 1000.0])                  # vCenter itself 30 s ahead of this machine: 60 s against vCenter
        self.assertEqual(hl.clock_offset(lambda: at.replace(tzinfo=None) - dt.timedelta(seconds=0.2), 30.0,
                                         clock=lambda: next(ticks)), 60.0)

        def refused():
            raise RuntimeError("managed object not found: HostDateTimeSystem")
        self.assertIsNone(hl.clock_offset(refused))
        self.assertIsNone(hl.clock_offset(lambda: None))

    def test_down_since(self):
        """The newest event of the host losing vCenter; none or an error: no time."""
        import datetime as dt
        saved = getattr(hl, "vim", None)
        spec = lambda **kw: kw                          # noqa: E731
        spec.ByEntity = lambda **kw: kw
        hl.vim = _NS(event=_NS(EventFilterSpec=spec))
        try:
            t = lambda h: dt.datetime(2026, 10, 25, h, 3, 12, tzinfo=dt.timezone.utc)     # noqa: E731
            asked = []
            em = _NS(QueryEvents=lambda s: asked.append(s) or [_NS(createdTime=t(9), fullFormattedMessage="Host esx01 lost  its connection"),
                                                               _NS(createdTime=t(14), fullFormattedMessage="Host esx01 is not responding")])
            self.assertEqual(hl.down_since(_NS(eventManager=em), "HOST"), ("2026-10-25T14:03:12Z", "Host esx01 is not responding"))
            self.assertEqual((asked[0]["entity"], asked[0]["eventTypeId"]), ({"entity": "HOST", "recursion": "self"}, hl.DOWN_EVENTS))
            self.assertEqual(hl.down_since(_NS(eventManager=_NS(QueryEvents=lambda s: [])), "HOST"), ("", ""))

            def boom(s):
                raise RuntimeError("no permission")
            self.assertEqual(hl.down_since(_NS(eventManager=_NS(QueryEvents=boom)), "HOST"), ("", ""))
        finally:
            if saved is None:
                del hl.vim
            else:
                hl.vim = saved


def _self_signed(cn, days=30, sans=(), extra=()):
    """A key and a self-signed certificate (PEM) for the tests."""
    import datetime as dt
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)] + [x509.NameAttribute(o, v) for o, v in extra])
    now = dt.datetime.now(dt.timezone.utc)
    b = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(1)
         .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=days, hours=1)))
    if sans:
        b = b.add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in sans]), critical=False)
    cert = b.sign(key, hashes.SHA256())
    return (key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()),
            cert.public_bytes(serialization.Encoding.PEM), cert.public_bytes(serialization.Encoding.DER))


class _FakeVcenterApi(object):
    """vCenter's REST API, just the session and tls-csr, over HTTPS in this process. mode: api (vCenter 7+),
    rest (6.7: /api is 404), forbidden (tls-csr 403), none (no certificate API: 404)."""

    def __init__(self, mode="api"):
        import http.server
        import ssl
        import tempfile
        import threading
        self.mode, self.calls = mode, []
        key, pem, _ = _self_signed("localhost", 5, ["localhost"])
        d = tempfile.mkdtemp()
        for f, data in (("k.pem", key), ("c.pem", pem)):
            with open(os.path.join(d, f), "wb") as fh:
                fh.write(data)
        api = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                api.calls.append(("POST", self.path, body, self.headers.get("vmware-api-session-id")))
                old = api.mode == "rest"
                if self.path == ("/rest/com/vmware/cis/session" if old else "/api/session"):
                    ok = self.headers.get("Authorization") == "Basic " + __import__("base64").b64encode(b"user:pass").decode()
                    return self._send(200, {"value": "tok1"} if old else "tok1") if ok else self._send(401, {})
                if self.path == ("/rest" if old else "/api") + vcert.CSR_PATH and api.mode != "none":
                    if self.headers.get("vmware-api-session-id") != "tok1":
                        return self._send(401, {})
                    if api.mode == "forbidden":
                        return self._send(403, {"error_type": "UNAUTHORIZED", "messages": [{"default_message": "Permission denied."}]})
                    spec = body["spec"] if old else body
                    _, _, _ = spec["common_name"], spec["organization"], spec["subject_alt_name"]
                    csr = "-----BEGIN CERTIFICATE REQUEST-----\nfor %s\n-----END CERTIFICATE REQUEST-----\n" % spec["common_name"]
                    return self._send(200, {"value": {"csr": csr}} if old else {"csr": csr})
                self._send(404, {})

            def do_DELETE(self):
                api.calls.append(("DELETE", self.path, None, self.headers.get("vmware-api-session-id")))
                self._send(200, {})

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(os.path.join(d, "c.pem"), os.path.join(d, "k.pem"))
        self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class VmwareCerts(unittest.TestCase):
    """site_vmware_certs: the certificate read from the TLS handshake, vCenter's CSR call."""

    def setUp(self):
        os.environ.update(VMWARE_USER="user", VMWARE_PASSWORD="pass")

    def test_cert_record(self):
        from cryptography.x509.oid import NameOID
        _, _, der = _self_signed("vc01.example.mil", 12, ["vc01.example.mil", "vc01"],
                                 [(NameOID.ORGANIZATIONAL_UNIT_NAME, "PKI"), (NameOID.ORGANIZATION_NAME, "Example"), (NameOID.COUNTRY_NAME, "US")])
        r = vcert.cert_record(der)
        self.assertEqual(r["days"], 12)
        self.assertEqual(r["sans"], ["vc01.example.mil", "vc01"])
        self.assertTrue(r["self_signed"])
        self.assertTrue(r["subject"].startswith("C=US,O=Example,OU=PKI,CN=vc01.example.mil") or "CN=vc01.example.mil" in r["subject"])
        spec = vcert.csr_spec(r, "vc01.example.mil", {"locality": "Norfolk", "key_size": "3072", "country": ""})
        self.assertEqual((spec["common_name"], spec["organization"], spec["organization_unit"], spec["country"], spec["locality"],
                          spec["state_or_province"], spec["key_size"]), ("vc01.example.mil", "Example", "PKI", "US", "Norfolk", "", 3072))
        self.assertEqual(spec["subject_alt_name"], ["vc01.example.mil", "vc01"])
        self.assertEqual(vcert.csr_spec({"parts": [], "sans": []}, "vc02")["subject_alt_name"], ["vc02"])


    def test_cert_record_inventory_fields(self):
        """The same fields as the Linux and Windows reports (tests/fixtures/pki): fingerprints, serial, key, tier."""
        import base64 as b64
        pki = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "pki")
        want = {"root": ("root", "RSA 3072", "07D72068104FB45E5366A7556CDE5CF5F171342F", "0F965ECAB20EF6EA9F895AA1C292DCD86E14B1D3"),
                "int": ("intermediate", "EC P-256", "176D43333E4A3290F6E056CBD9DD0C96FB182F6F", "1001"),
                "web": ("ca-signed", "RSA 2048", "487B8E272CBC7625EE18207F58559BD961FD0561", "2002"),
                "self": ("self-signed", "EC P-384", "938C3203826F24460951E54C049918E8A6CF5FAF", "293F2F1A9421CDD6DDD13E31C835E8A6D3371238")}
        for name, (tier, key, sha1, serial) in want.items():
            with open(os.path.join(pki, name + ".pem")) as f:
                der = b64.b64decode("".join(x for x in f.read().splitlines() if not x.startswith("-----")))
            r = vcert.cert_record(der)
            self.assertEqual((r["tier"], r["key"], r["sha1"], r["serial"]), (tier, key, sha1, serial), name)
            self.assertEqual(len(r["sha256"]), 64)
            self.assertTrue(r["sig_alg"], name)
        r = vcert.cert_record(der)
        self.assertEqual(r["sha256"], "EEA658E2F979C700DA680A77500100D5CDAF0184EB1CCE7416A3E45645D08833")
    def test_dn(self):
        self.assertEqual(vcert.dn_pairs("CN=a, OU=b\\, c, O=d"), [("CN", "a"), ("OU", "b, c"), ("O", "d")])
        self.assertTrue(vcert.same_dn("CN=esx01, O=Example", "o=Example,CN=ESX01"))
        self.assertFalse(vcert.same_dn("CN=esx01", "CN=CA, DC=vsphere"))
        self.assertFalse(vcert.same_dn("", ""))

    def test_tls_der_reads_any_certificate(self):
        api = _FakeVcenterApi()
        try:
            r = vcert.cert_record(vcert.tls_der("127.0.0.1", api.port, 5))
            self.assertEqual((r["subject"], r["days"]), ("CN=localhost", 5))
        finally:
            api.close()

    def _csr(self, mode):
        api = _FakeVcenterApi(mode)
        try:
            a = vcert.Api("127.0.0.1", api.port, False, 5)
            a.login("vc01")
            try:
                return a.tls_csr("vc01", {"common_name": "vc01.example.mil", "organization": "", "subject_alt_name": ["vc01.example.mil"]}), api.calls
            finally:
                a.logout()
        finally:
            api.close()

    def test_vcenter_csr_api(self):
        csr, calls = self._csr("api")
        self.assertIn("for vc01.example.mil", csr)
        self.assertEqual([c[:2] for c in calls], [("POST", "/api/session"), ("POST", "/api" + vcert.CSR_PATH), ("DELETE", "/api/session")])
        self.assertEqual(calls[1][2]["common_name"], "vc01.example.mil")      # /api: the body is the spec itself

    def test_vcenter_csr_rest_67(self):
        csr, calls = self._csr("rest")
        self.assertIn("for vc01.example.mil", csr)
        self.assertEqual(calls[-2][1], "/rest" + vcert.CSR_PATH)
        self.assertEqual(calls[-2][2]["spec"]["common_name"], "vc01.example.mil")   # /rest: {"spec": ...}
        self.assertEqual(calls[-1][:2], ("DELETE", "/rest/com/vmware/cis/session"))

    def test_vcenter_csr_refused_and_missing(self):
        with self.assertRaisesRegex(RuntimeError, r"refused to make the CSR \(HTTP 403: Permission denied\.\).*CAAdmins"):
            self._csr("forbidden")
        with self.assertRaisesRegex(RuntimeError, r"has no certificate API .*HTTP 404.*6\.7 Update 2"):
            self._csr("none")
        os.environ["VMWARE_PASSWORD"] = "wrong"
        with self.assertRaisesRegex(RuntimeError, r"refused the login to its API \(HTTP 401\)"):
            self._csr("api")


class OutageWatch(unittest.TestCase):
    """site_vmware_watch: who is a suspect, a task that makes it busy, where it is probed, the verdict."""
    VM = {"name": "app01", "power": "poweredOn", "tools": "toolsOk", "tools_running": "guestToolsRunning", "heartbeat": "red",
          "up_minutes": 600, "ip": "192.0.2.20", "ips": ["192.0.2.20", "fe80::1"], "hostname": "app01.example.mil"}

    def test_suspect_reason(self):
        self.assertEqual(wt.suspect_reason(self.VM), "no guest heartbeat (red)")
        gray = dict(self.VM, heartbeat="gray", tools="toolsNotRunning", tools_running="guestToolsNotRunning")
        self.assertEqual(wt.suspect_reason(gray), "VMware Tools stopped answering (heartbeat gray)")
        self.assertEqual(wt.suspect_reason(dict(self.VM, heartbeat="green")), "")
        self.assertEqual(wt.suspect_reason(dict(self.VM, heartbeat="gray")), "")           # gray but Tools say running: not judged
        self.assertEqual(wt.suspect_reason(dict(self.VM, heartbeat="yellow")), "")
        self.assertEqual(wt.suspect_reason(dict(self.VM, tools="toolsNotInstalled")), "")  # no Tools: never judged
        self.assertEqual(wt.suspect_reason(dict(self.VM, tools="")), "")
        self.assertEqual(wt.suspect_reason(dict(self.VM, power="poweredOff")), "")
        self.assertEqual(wt.suspect_reason(dict(self.VM, up_minutes=5)), "")               # still starting
        self.assertEqual(wt.suspect_reason(dict(self.VM, up_minutes=5), min_up_minutes=0), "no guest heartbeat (red)")
        self.assertEqual(wt.suspect_reason(dict(self.VM, up_minutes=None)), "no guest heartbeat (red)")

    def test_busy_task(self):
        self.assertEqual(wt.busy_task([{"id": "VirtualMachine.createSnapshot", "state": "running"}]),
                         "snapshot (VirtualMachine.createSnapshot)")
        self.assertEqual(wt.busy_task([{"id": "Drm.ExecuteVMotionLRO", "state": "queued"}]), "vmotion (Drm.ExecuteVMotionLRO)")
        self.assertEqual(wt.busy_task([{"id": "VirtualMachine.createSnapshot", "state": "success"},
                                       {"id": "VirtualMachine.migrate", "state": "error"}]), "")
        self.assertEqual(wt.busy_task([{"id": "VirtualMachine.acquireTicket", "state": "running"}]), "")
        self.assertEqual(wt.busy_task(None), "")

    def test_probe_candidates(self):
        self.assertEqual(wt.probe_candidates(self.VM), ["192.0.2.20", "app01.example.mil", "app01"])
        # Tools that stopped often leave no IP: the guest host name and the VM name (DNS)
        self.assertEqual(wt.probe_candidates({"name": "app 01", "ip": "", "ips": []}), [])
        self.assertEqual(wt.probe_candidates({"name": "app01", "ip": "", "ips": [], "hostname": ""}), ["app01"])

    def test_verdict(self):
        hung = {"reason": "no guest heartbeat (red)", "busy": "", "probe": "no answer"}
        self.assertEqual(wt.verdict([hung, hung]), "hung")
        self.assertEqual(wt.verdict([hung, dict(hung, probe="answers")]), "one-signal")
        self.assertEqual(wt.verdict([hung, dict(hung, reason="")]), "recovered")         # heartbeat back in the 2nd check
        self.assertEqual(wt.verdict([hung, dict(hung, busy="snapshot (x)")]), "busy")
        self.assertEqual(wt.verdict([dict(hung, probe="no address")]), "unprobed")
        self.assertEqual(wt.verdict([hung, dict(hung, probe="unavailable")]), "unprobed")  # one check could not probe: not hung
        self.assertEqual(wt.verdict([]), "recovered")

    def test_name_skipped(self):
        v = {"name": "LAB-01", "folder": "/DC1/vm/Lab/web"}
        self.assertEqual(wt.name_skipped(v, ["lab-*"], []), "vm_watch_skip_vms")
        self.assertEqual(wt.name_skipped(v, [], ["/dc1/vm/lab*"]), "vm_watch_skip_folders")
        self.assertEqual(wt.name_skipped(v, ["web*"], ["/DC2/*"]), "")

    def test_probe_without_address(self):
        self.assertEqual(wt.probe({"name": "", "ip": "", "ips": []})["probe"], "no address")

    def test_port_by_guest_os(self):
        self.assertEqual(wt.port_for(dict(self.VM, guest_os="Red Hat Enterprise Linux 9 (64-bit)"), "auto"), 22)
        self.assertEqual(wt.port_for(dict(self.VM, guest_os="Microsoft Windows Server 2022 (64-bit)"), "auto"), 3389)
        self.assertEqual(wt.port_for(self.VM, ""), 22)
        self.assertEqual(wt.port_for(self.VM, "5985"), 5985)
        self.assertEqual((wt.port_for(self.VM, 0), wt.port_for(self.VM, "0"), wt.port_for(self.VM, "x")), (0, 0, 0))

    def test_tcp_refused_is_alive_a_timeout_is_not(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()                                          # nothing listens: refused - the guest's stack answered
        self.assertEqual(wt._tcp("127.0.0.1", port, 1), "answers")

    def test_alive_by_traffic(self):
        self.assertEqual(wt.alive_by_traffic({"packets": 0, "minutes": 5.0}), "")
        self.assertEqual(wt.alive_by_traffic({"packets": None, "note": "x"}), "")
        self.assertEqual(wt.alive_by_traffic(None), "")
        self.assertEqual(wt.alive_by_traffic({"packets": 1, "minutes": 5.0}),
                         "it sent 1 network packet in the last 5 minutes (vCenter's real-time statistics)")
        self.assertEqual(wt.alive_by_traffic({"packets": 412, "minutes": 7.3}),
                         "it sent 412 network packets in the last 7.3 minutes (vCenter's real-time statistics)")
        self.assertEqual(wt.alive_by_traffic({"packets": 5, "minutes": 5.0}, 10), "")


if __name__ == "__main__":
    unittest.main()
