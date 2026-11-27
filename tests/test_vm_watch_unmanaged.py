#!/usr/bin/env python3
"""SolarWinds maintenance at the live watch and email decision boundaries. All reads are local fakes."""
import datetime
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

import ansible.module_utils
import yaml

ROOT = Path(__file__).resolve().parents[1]
ansible.module_utils.__path__.append(str(ROOT / "plugins/module_utils"))
from ansible.module_utils import vm_watch_unmanaged as sw
from test_watch_frozen import _render_context, _render_fact
from test_vm_watch import data, suspect, w
from vmware_watch_email_filters import vm_watch_email_floor

VM = "SITEA"
VC = "vc.corp.example.mil"
NOW = datetime.datetime(2026, 11, 27, 12, tzinfo=datetime.timezone.utc).timestamp()
NODE = dict(Caption="sitea", DNS="SITEA.corp.example.mil", SysName="", IPAddress="192.0.2.20",
            UnManaged=True, UnManageFrom="2026-11-27T11:00:00Z", UnManageUntil="2026-11-27T14:00:00+01:00", Status=2)
SETTINGS = dict(enabled=True, url="orion.corp.example.mil", validate_certs=True, timeout=10, ca_path="")


sys.path.insert(0, str(ROOT / "roles/vmware_vm/library"))
ansible.module_utils.__path__.append(str(ROOT / "roles/vmware_vm/module_utils"))
import site_vmware_watch as watch


class Maintenance(unittest.TestCase):
    def read(self, nodes=(), now=NOW, enabled=True, credential=True, error=None, events=(), suspects=None, inventory=()):
        raw = data(_watch_inventory=list(inventory), suspects=[suspect(VM, "hung", vcenter=VC)] if suspects is None else suspects, questions=[], vms_lost=[], events=list(events))
        opener = Mock()
        opener.open.side_effect = error
        opener.open.return_value = io.BytesIO(json.dumps(dict(results=nodes)).encode())
        env = dict(SOLARWINDS_USERNAME="SITEA", SOLARWINDS_PASSWORD="example") if credential else {}
        with patch.dict(os.environ, env, clear=True), patch.object(sw.site_swis, "build_opener", return_value=opener):
            sw.suppress(raw, dict(SETTINGS, enabled=enabled), now)
        if enabled and credential:
            opener.open.assert_called_once()
            req = opener.open.call_args.args[0]
            self.assertEqual(req.full_url, "https://orion.corp.example.mil:17774" + sw.site_swis.QUERY_PATH)
            self.assertEqual(json.loads(req.data)["query"], "SELECT Caption, DNS, SysName, IPAddress, UnManaged, "
                             "UnManageFrom, UnManageUntil, Status FROM Orion.Nodes")
        else:
            opener.open.assert_not_called()
        return raw

    def test_each_exact_match_suppresses(self):
        for field, value in (("Caption", "sItEa"), ("DNS", "sitea.corp.example.mil"),
                             ("SysName", "SiTeA.corp.example.mil"), ("IPAddress", "192.0.2.20")):
            node = dict(NODE, Caption="other", DNS="", SysName="", IPAddress="")
            node[field] = value
            with self.subTest(field=field):
                raw = self.read([node])
                self.assertEqual(w.vm_watch_conditions(raw), [])
                self.assertEqual(raw["solarwinds_lines"], [
                    "VM SITEA - not mailed: unmanaged in SolarWinds until %s (node %s)" % (node["UnManageUntil"], node["Caption"])])

    def test_same_vm_ha_event_is_quiet_other_vm_still_alerts(self):
        event = dict(type="VmFailoverFailed", vm=VM, vcenter=VC, _watch_vm_moid="vm-" + VM)
        other = dict(event, vm="SITEA-2", _watch_vm_moid="vm-2")
        raw = self.read([NODE], events=[event, other])
        self.assertEqual([c["object"] for c in w.vm_watch_conditions(raw)], ["SITEA-2"])

    def test_event_only_vm_uses_inventory(self):
        event = dict(type="VmFailoverFailed", vm=VM, vcenter=VC, _watch_vm_moid="vm-1")
        vm = dict(name=VM, moid="vm-1", vcenter=VC, ip=NODE["IPAddress"])
        self.assertFalse(w.vm_watch_conditions(self.read([NODE], suspects=[], inventory=[vm], events=[event])))

    def test_status_and_window(self):
        node = dict(NODE, Status=9, UnManageFrom="2026-11-27T12:00:00", UnManageUntil="2026-11-27T13:00:00+0000")
        self.assertFalse(w.vm_watch_conditions(self.read([node])))
        for override in (dict(UnManageUntil="2026-11-27T12:00:00Z"), dict(UnManageFrom="2026-11-27T12:00:00.0000001Z"),
                         dict(UnManaged=False), dict(UnManageFrom=None), dict(UnManageUntil=None)):
            with self.subTest(override=override):
                self.assertTrue(w.vm_watch_conditions(self.read([dict(node, **override)])))

    def test_reverse_match_includes_healthy_inventory_and_ambiguous_vms(self):
        other = suspect(VM, "hung", vcenter="vc2.corp.example.mil", ip="192.0.2.99")
        for nodes in ([NODE], [NODE, dict(NODE, Caption="other", DNS="", IPAddress=other["ip"])]):
            self.assertTrue(w.vm_watch_conditions(self.read(nodes, inventory=[other])))

    def test_secondary_ip_and_non_hostname_do_not_match(self):
        vm = suspect("192", "hung", vcenter=VC, ip="192.0.2.99", ips=[NODE["IPAddress"]])
        for dns in ("192.0.2.55", "192..invalid", ".corp.example.mil"):
            self.assertTrue(w.vm_watch_conditions(self.read([dict(NODE, DNS=dns)], suspects=[vm])))

    def test_probe_slot_goes_to_managed_vm(self):
        vms = [suspect(VM, "hung", vcenter=VC), suspect("SITEA-DOWN", "hung", vcenter=VC, ip="192.0.2.99")]
        part = data(suspects=vms, vms_lost=[], vms_unread=[], vms_disconnected=[], alarms=[], vms_read=2)
        module = Mock(params=dict(event_types=[], confirm_checks=1, now=NOW, vcenters=[], validate_certs=True,
                                  window_minutes=15, probe=True, probe_port="0", probe_timeout=1, confirm_seconds=0,
                                  max_suspects=1, traffic=False, traffic_packets=1, solarwinds=SETTINGS))
        with patch.object(watch, "AnsibleModule", return_value=module), patch.object(watch, "HAS_PYVMOMI", True), \
                patch.object(watch, "each_vcenter", return_value=([(VC, part)], [])), \
                patch.object(sw.site_host_swis, "client_of") as client, patch.object(watch, "probe") as probe:
            client.return_value.query.return_value = [NODE]
            probe.return_value = {"probe": "no answer"}
            watch.main()
        self.assertEqual([call.args[0]["name"] for call in probe.call_args_list], ["SITEA-DOWN"])
        raw = module.exit_json.call_args.kwargs
        self.assertEqual(raw["not_probed"], [])
        self.assertEqual([(c["object"], c["severity"]) for c in w.vm_watch_conditions(raw)], [("SITEA-DOWN", "critical")])

    def test_ambiguous_or_unmatched_alerts(self):
        managed = dict(NODE, UnManaged=False, Caption="other", DNS="", IPAddress="192.0.2.20")
        for nodes in ([NODE, managed], [], [dict(managed, Caption="SITEA.corp.example.mil", IPAddress="")]):
            self.assertTrue(w.vm_watch_conditions(self.read(nodes)))

    def test_unavailable_or_disabled_alerts(self):
        for opts in (dict(error=TimeoutError()), dict(credential=False), dict(enabled=False)):
            raw = self.read([NODE], **opts)
            self.assertTrue(w.vm_watch_conditions(raw))
            if opts.get("enabled", True):
                self.assertEqual(len(raw["solarwinds_lines"]), 1)
                self.assertIn("timeout" if "error" in opts else "credential", raw["solarwinds_lines"][0])
            else:
                self.assertNotIn("solarwinds_lines", raw)

    def test_maintenance_keeps_held_critical_alert_until_due(self):
        raw = self.read([NODE])
        cid = "vm-watch:hung:SITEA@" + VC
        held = dict(line="New: hung:SITEA in %s (critical)" % VC, id=cid, severity="critical", at=NOW-300)
        previous = dict(ids={cid: "critical"}, sent_ts=NOW-900, held=[held])
        opts = dict(unmanaged_ids=raw["unmanaged_email_ids"], min_minutes=30, now=NOW)
        result = vm_watch_email_floor([], [dict(artifacts=dict(vm_watch_email_state=previous))], opts)
        self.assertFalse(result["send"])
        self.assertEqual(result["state"]["held"], [held])
        result = vm_watch_email_floor([], [dict(artifacts=dict(vm_watch_email_state=result["state"]))], dict(opts, now=NOW+900))
        self.assertTrue(result["send"])
        self.assertTrue(any(held["line"] in line for line in result["changed"]))

    def test_mail_and_review_through_maintenance(self):
        env, ctx = _render_context(True)
        ctx["now"] = lambda: datetime.datetime.fromtimestamp(NOW, datetime.timezone.utc)
        env.filters.update(vm_watch_email_floor=vm_watch_email_floor)
        tasks = yaml.safe_load((ROOT / "roles/vmware_vm/tasks/watch_email.yml").read_text())
        task = next(t for t in tasks if "_vm_w_mail" in t.get("ansible.builtin.set_fact", {}))
        cid = "vm-watch:hung:SITEA@" + VC
        previous = dict(ids={cid: "critical"}, sent_ts=NOW-3600, held=[dict(line="New: hung:SITEA in %s (critical)" % VC)])
        other = suspect(VM, "recovered", moid="vm-2", vcenter=VC, ip="192.0.2.99")
        raw = self.read([dict(NODE, Caption="other", DNS="")], inventory=[other])
        ctx.update(_vm_w=raw, _vm_w_report_it=False, vm_watch_review=True,
                   vm_watch_email_on_change=True, vm_watch_email_every_hours=4, vm_watch_report_on=["critical"],
                   _vm_w_mail_db={}, _vm_w_cond=[], _vm_now=NOW,
                   _site_hist_runs=[dict(artifacts=dict(vm_watch_email_state=previous))])
        result = _render_fact(task, "_vm_w_mail", env, ctx)
        self.assertTrue(result["review"] and result["send"])
        self.assertTrue(any(previous["held"][0]["line"] in line for line in result["changed"]))
        self.assertNotIn(cid, raw["unmanaged_email_ids"])


if __name__ == "__main__":
    unittest.main()
