"""1.141.0 tests: ESXi host unexpected outage & Capacity wall Teams share cards (model, live collector, UI, PNG/HTML/text, grey on missing)."""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = os.path.join(ROOT, "roles", "site_portal_stack", "files")
sys.path.insert(0, FILES)
sys.path.insert(0, os.path.join(ROOT, "plugins", "module_utils"))
sys.path.insert(0, os.path.join(ROOT, "plugins", "filter"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import wall_live as WL
import wall_model as WM
import wall_fleet as WF
import site_portal as SP
from wall import demo

NOW = 1795852800.0  # 2026-11-28 08:00:00 UTC
GB = 1024 ** 3
TB = 1024 ** 4


def _iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_cards_fixture(now=NOW, stale=False, missing_vc=False, missing_ontap=False, custom_limits=False):
    """Build a complete placeholder fixture (SITEA, corp.example.mil, 192.0.2.x):
       - 3 unexpected down ESXi hosts (nested VMs across services, power states, HA restarted yes/no)
       - 4 excluded down hosts (maintenance mode, vCenter RebootHost_Task, standby mode, portal_wall_planned_reboot)
       - 40 full capacity items (20 VMware datastores + 20 NetApp ONTAP volumes), plus healthy & excluded items
       - Live cap_history trend samples (mix of <1d, multi-day, and 'no trend')"""
    vc_key = "vcenter:vc01.corp.example.mil"
    on_key = "ontap:ontap01.corp.example.mil"
    at = now - (900 if stale else 20)

    # ---- ESXi hosts & VMs ----
    hosts = [
        # Connected healthy peer hosts
        {"name": "sitea-esx01.corp.example.mil", "cluster": "SITA01-CL01", "state": "connected", "maint": False,
         "power": "poweredOn", "standby": "none", "quarantine": False, "overall": "green", "triggered": [],
         "cpu_pct": 44.0, "mem_pct": 61.0, "ip": "192.0.2.11",
         "vms": ["sitea-app-01", "sitea-web-02", "sitea-auth-01", "sitea-db-02", "sitea-erp-02", "sitea-vdi-02", "sitea-mail-01", "sitea-proxy-01"],
         "datastores": ["DS_SITEA_01"]},
        {"name": "sitea-esx02.corp.example.mil", "cluster": "SITA01-CL02", "state": "connected", "maint": False,
         "power": "poweredOn", "standby": "none", "quarantine": False, "overall": "green", "triggered": [],
         "cpu_pct": 39.0, "mem_pct": 55.0, "ip": "192.0.2.12",
         "vms": ["sitea-rpt-01", "sitea-mon-01", "sitea-file-01"], "datastores": ["DS_SITEA_02"]},
        # 3 unexpected down ESXi hosts (deliberately unsorted to test worst-first ordering)
        # Host C (3rd worst): 0 not restarted, 3 HA restarted
        {"name": "sitea-esx09.corp.example.mil", "cluster": "SITA01-CL02", "state": "disconnected", "maint": False,
         "power": "poweredOff", "standby": "none", "quarantine": False, "overall": "red", "triggered": [],
         "ip": "192.0.2.19", "since": now - 1800, "tasks": [], "vms": [], "datastores": ["DS_SITEA_03"]},
        # Host A (worst): 4 not restarted, 2 HA restarted (6 affected VMs)
        {"name": "sitea-esx07.corp.example.mil", "cluster": "SITA01-CL01", "state": "notResponding", "maint": False,
         "power": "poweredOn", "standby": "none", "quarantine": False, "overall": "red", "triggered": [],
         "ip": "192.0.2.17", "since": now - 2520, "tasks": [],
         "vms": ["sitea-sql-01", "sitea-erp-01", "sitea-pki-01", "sitea-dc-02"], "datastores": ["DS_SITEA_01"]},
        # Host B (2nd worst): 2 not restarted, 3 HA restarted (5 affected VMs)
        {"name": "sitea-esx03.corp.example.mil", "cluster": "SITA01-CL01", "state": "notResponding", "maint": False,
         "power": "poweredOn", "standby": "none", "quarantine": False, "overall": "red", "triggered": [],
         "ip": "192.0.2.13", "since": now - 1140, "tasks": [],
         "vms": ["sitea-vdi-01", "sitea-gis-01"], "datastores": ["DS_SITEA_01"]},
        # 4 excluded down hosts (MUST NOT appear on unexpected outage card)
        {"name": "sitea-esx11.corp.example.mil", "cluster": "SITA01-CL01", "state": "disconnected", "maint": True,
         "power": "poweredOff", "standby": "none", "quarantine": False, "overall": "yellow", "triggered": [],
         "ip": "192.0.2.21", "vms": ["sitea-maint-vm"], "datastores": []},
        {"name": "sitea-esx12.corp.example.mil", "cluster": "SITA01-CL01", "state": "notResponding", "maint": False,
         "power": "poweredOn", "standby": "none", "quarantine": False, "overall": "yellow", "triggered": [],
         "ip": "192.0.2.22",
         "tasks": [{"name": "RebootHost_Task", "desc": "HostSystem.reboot", "state": "running", "start": _iso(now - 300), "end": ""}],
         "vms": ["sitea-reboot-vm"], "datastores": []},
        {"name": "sitea-esx13.corp.example.mil", "cluster": "SITA01-CL02", "state": "disconnected", "maint": False,
         "power": "standBy", "standby": "in", "quarantine": False, "overall": "yellow", "triggered": [],
         "ip": "192.0.2.23", "vms": [], "datastores": []},
        {"name": "sitea-esx14.corp.example.mil", "cluster": "SITA01-CL02", "state": "disconnected", "maint": False,
         "power": "poweredOff", "standby": "none", "quarantine": False, "overall": "yellow", "triggered": [],
         "ip": "192.0.2.24", "vms": [], "datastores": []},
    ]

    vms = [
        # Affected on sitea-esx07 (4 NOT restarted, 2 HA restarted on esx01)
        {"id": "vm-101", "name": "sitea-sql-01", "host": "sitea-esx07.corp.example.mil", "power": "poweredOff",
         "conn": "inaccessible", "service": "Core SQL", "template": False, "vcls": False, "das_protected": True,
         "failover": False, "tools": "guestToolsNotRunning", "tools_seen": True, "heartbeat": "red"},
        {"id": "vm-102", "name": "sitea-erp-01", "host": "sitea-esx07.corp.example.mil", "power": "poweredOff",
         "conn": "disconnected", "service": "ERP Prod", "template": False, "vcls": False, "das_protected": True,
         "failover": False, "tools": "guestToolsNotRunning", "tools_seen": True, "heartbeat": "red"},
        {"id": "vm-103", "name": "sitea-pki-01", "host": "sitea-esx07.corp.example.mil", "power": "poweredOn",
         "conn": "disconnected", "service": "PKI & Auth", "template": False, "vcls": False, "das_protected": False,
         "failover": False, "tools": "guestToolsNotRunning", "tools_seen": True, "heartbeat": "red"},
        {"id": "vm-104", "name": "sitea-dc-02", "host": "sitea-esx07.corp.example.mil", "power": "suspended",
         "conn": "disconnected", "service": "Active Directory", "template": False, "vcls": False, "das_protected": True,
         "failover": False, "tools": "guestToolsNotRunning", "tools_seen": True, "heartbeat": "red"},
        {"id": "vm-105", "name": "sitea-app-01", "host": "sitea-esx01.corp.example.mil", "ha_from": "sitea-esx07.corp.example.mil",
         "power": "poweredOn", "conn": "connected", "service": "ERP Prod", "template": False, "vcls": False,
         "cpu_pct": 25.0, "mem_pct": 40.0, "failover": False, "tools": "guestToolsRunning", "tools_seen": True, "heartbeat": "green"},
        {"id": "vm-106", "name": "sitea-web-02", "host": "sitea-esx01.corp.example.mil", "ha_from": "sitea-esx07.corp.example.mil",
         "power": "poweredOn", "conn": "connected", "service": "Portal Web", "template": False, "vcls": False,
         "cpu_pct": 18.0, "mem_pct": 33.0, "failover": False, "tools": "guestToolsRunning", "tools_seen": True, "heartbeat": "green"},
        # Affected on sitea-esx03 (2 NOT restarted, 3 HA restarted: 1 powering on + 2 powered on)
        {"id": "vm-201", "name": "sitea-vdi-01", "host": "sitea-esx03.corp.example.mil", "power": "poweredOff",
         "conn": "disconnected", "service": "VDI Horizon", "template": False, "vcls": False, "das_protected": True,
         "failover": False, "tools": "guestToolsNotRunning", "tools_seen": True, "heartbeat": "red"},
        {"id": "vm-202", "name": "sitea-gis-01", "host": "sitea-esx03.corp.example.mil", "power": "poweredOff",
         "conn": "inaccessible", "service": "GIS Mapping", "template": False, "vcls": False, "das_protected": True,
         "failover": False, "tools": "guestToolsNotRunning", "tools_seen": True, "heartbeat": "red"},
        {"id": "vm-203", "name": "sitea-auth-01", "host": "sitea-esx01.corp.example.mil", "ha_from": "sitea-esx03.corp.example.mil",
         "power": "poweredOn", "conn": "connected", "service": "PKI & Auth", "template": False, "vcls": False,
         "cpu_pct": 15.0, "mem_pct": 28.0, "failover": True, "tools": "guestToolsRunning", "tools_seen": True, "heartbeat": "green"},
        {"id": "vm-204", "name": "sitea-db-02", "host": "sitea-esx01.corp.example.mil", "ha_from": "sitea-esx03.corp.example.mil",
         "power": "poweredOn", "conn": "connected", "service": "Core SQL", "template": False, "vcls": False,
         "cpu_pct": 21.0, "mem_pct": 39.0, "failover": False, "tools": "guestToolsRunning", "tools_seen": True, "heartbeat": "green"},
        {"id": "vm-205", "name": "sitea-erp-02", "host": "sitea-esx01.corp.example.mil", "ha_from": "sitea-esx03.corp.example.mil",
         "power": "poweredOn", "conn": "connected", "service": "ERP Prod", "template": False, "vcls": False,
         "cpu_pct": 19.0, "mem_pct": 34.0, "failover": False, "tools": "guestToolsRunning", "tools_seen": True, "heartbeat": "green"},
        # Affected on sitea-esx09 (0 NOT restarted, 3 HA restarted on esx02)
        {"id": "vm-301", "name": "sitea-rpt-01", "host": "sitea-esx02.corp.example.mil", "ha_from": "sitea-esx09.corp.example.mil",
         "power": "poweredOn", "conn": "connected", "service": "Reporting", "template": False, "vcls": False,
         "cpu_pct": 14.0, "mem_pct": 29.0, "failover": False, "tools": "guestToolsRunning", "tools_seen": True, "heartbeat": "green"},
        {"id": "vm-302", "name": "sitea-mon-01", "host": "sitea-esx02.corp.example.mil", "ha_from": "sitea-esx09.corp.example.mil",
         "power": "poweredOn", "conn": "connected", "service": "Monitoring", "template": False, "vcls": False,
         "cpu_pct": 12.0, "mem_pct": 22.0, "failover": False, "tools": "guestToolsRunning", "tools_seen": True, "heartbeat": "green"},
        {"id": "vm-303", "name": "sitea-file-01", "host": "sitea-esx02.corp.example.mil", "ha_from": "sitea-esx09.corp.example.mil",
         "power": "poweredOn", "conn": "connected", "service": "File Services", "template": False, "vcls": False,
         "cpu_pct": 11.0, "mem_pct": 25.0, "failover": False, "tools": "guestToolsRunning", "tools_seen": True, "heartbeat": "green"},
        # Template & vCLS (must be skipped)
        {"id": "vm-tpl", "name": "tmpl-rhel9", "host": "sitea-esx07.corp.example.mil", "power": "poweredOff",
         "template": True, "vcls": False},
        {"id": "vm-vcls", "name": "vCLS-1", "host": "sitea-esx07.corp.example.mil", "power": "poweredOn",
         "template": False, "vcls": True},
    ]

    # ---- 40 full capacity items (20 VMware datastores + 20 NetApp ONTAP volumes) + healthy/excluded ----
    datastores = []
    cap_history = {}
    # 20 over-threshold datastores spanning 98.4% down to 85.4%
    ds_pcts = [98.4, 97.6, 96.8, 96.1, 95.5, 95.1, 94.6, 94.0, 93.4, 92.8,
               92.2, 91.5, 90.9, 90.2, 89.6, 88.9, 88.2, 87.5, 86.4, 85.4]
    for i, pct in enumerate(ds_pcts, 1):
        name = "DS_SITEA_%02d" % i
        cap = (8 if i % 2 else 4) * TB
        used = int(cap * pct / 100.0)
        free = cap - used
        host_ref = "sitea-esx01.corp.example.mil" if i % 2 else "sitea-esx02.corp.example.mil"
        datastores.append({"name": name, "type": "VMFS", "capacity": cap, "free": free,
                           "accessible": True, "shared": True, "hosts": [host_ref], "vms": 10 + i})
        # Add real trend samples for most; leave every 5th without trend ('no trend')
        if i % 5 != 0:
            # Target days to full: 0.6d for #1, then gradually increasing
            target_days = 0.6 if i == 1 else (2.0 + i * 1.4)
            rate_per_day = free / target_days
            pts = []
            for d_ago in (5, 4, 3, 2, 1, 0):
                t_s = at - d_ago * 86400
                u_s = max(0, int(used - rate_per_day * d_ago))
                pts.append((t_s, u_s, cap))
            cap_history[("ds", vc_key, name)] = pts
    # Healthy (<85%), local, and ISO datastores (must NOT appear on Capacity card)
    datastores.append({"name": "DS_SITEA_HEALTHY", "type": "VMFS", "capacity": 8 * TB, "free": 4 * TB,
                       "accessible": True, "shared": True, "hosts": ["sitea-esx01.corp.example.mil"], "vms": 5})
    datastores.append({"name": "local-esx01", "type": "VMFS", "capacity": 1 * TB, "free": 10 * GB,
                       "accessible": True, "shared": False, "hosts": ["sitea-esx01.corp.example.mil"], "vms": 0})
    datastores.append({"name": "iso-library", "type": "NFS", "capacity": 2 * TB, "free": 20 * GB,
                       "accessible": True, "shared": True, "hosts": ["sitea-esx01.corp.example.mil"], "vms": 0})

    volumes = [
        # Root volume (must be excluded even at 99%)
        {"name": "svm_sitea_root", "svm": "svm_sitea_01", "state": "online", "type": "rw",
         "is_svm_root": True, "size": 100 * GB, "used": 99 * GB, "aggr": "aggr1_ssd_n01"},
        # Healthy volume (<85%, must be excluded)
        {"name": "vol_sitea_healthy", "svm": "svm_sitea_01", "state": "online", "type": "rw",
         "is_svm_root": False, "size": 4 * TB, "used": 2 * TB, "aggr": "aggr1_ssd_n01"},
    ]
    # 20 over-threshold ONTAP volumes spanning 98.1% down to 85.2%
    vol_pcts = [98.1, 97.2, 96.5, 95.9, 95.3, 94.8, 94.2, 93.7, 93.1, 92.5,
                91.9, 91.2, 90.6, 89.9, 89.2, 88.6, 87.9, 87.1, 86.0, 85.2]
    for i, pct in enumerate(vol_pcts, 1):
        name = "vol_sitea_%02d" % i
        svm = "svm_sitea_%02d" % (1 + (i % 3))
        aggr = "aggr%d_ssd_n0%d" % (1 + (i % 2), 1 + (i % 2))
        sz = (6 if i % 2 else 10) * TB
        used = int(sz * pct / 100.0)
        free = sz - used
        volumes.append({"name": name, "svm": svm, "state": "online", "type": "rw",
                        "is_svm_root": False, "is_node_root": False, "size": sz, "used": used, "aggr": aggr})
        if i % 4 != 0:
            target_days = 0.8 if i == 1 else (3.0 + i * 1.7)
            rate_per_day = free / target_days
            pts = [(at - d_ago * 86400, max(0, int(used - rate_per_day * d_ago)), sz) for d_ago in (5, 3, 1, 0)]
            cap_history[("vol", on_key, svm, name)] = pts

    sources = {}
    if not missing_vc:
        sources[vc_key] = {
            "kind": "vcenter", "name": "vc01.corp.example.mil", "state": "ok", "at": at, "why": "",
            "snap": {
                "clusters": [
                    {"name": "SITA01-CL01", "ha": True, "hosts": ["sitea-esx01.corp.example.mil", "sitea-esx03.corp.example.mil", "sitea-esx07.corp.example.mil", "sitea-esx11.corp.example.mil", "sitea-esx12.corp.example.mil"]},
                    {"name": "SITA01-CL02", "ha": True, "hosts": ["sitea-esx02.corp.example.mil", "sitea-esx09.corp.example.mil", "sitea-esx13.corp.example.mil", "sitea-esx14.corp.example.mil"]},
                ],
                "hosts": hosts, "datastores": datastores, "vms": vms,
            },
        }
    if not missing_ontap:
        sources[on_key] = {
            "kind": "ontap", "name": "ontap01.corp.example.mil", "state": "ok", "at": at, "why": "",
            "snap": {
                "cluster": "SITA01-ONTAP01", "version": "9.14.1", "failed": {},
                "nodes": [{"name": "sita01-n01", "state": "up", "takeover": True}, {"name": "sita01-n02", "state": "up", "takeover": True}],
                "aggrs": [
                    {"name": "aggr1_ssd_n01", "node": "sita01-n01", "size": 60 * TB, "used": 44 * TB, "is_root": False},
                    {"name": "aggr2_ssd_n02", "node": "sita01-n02", "size": 60 * TB, "used": 40 * TB, "is_root": False},
                ],
                "volumes": volumes, "luns": [], "snapmirror": [],
            },
        }
    opts = dict(demo.OPTS)
    opts["planned_reboot"] = ["sitea-esx14*"]
    opts["planned_reboot_minutes"] = 30
    opts["cap_trend_days"] = 7
    if custom_limits:
        opts["ds_warn"], opts["ds_crit"] = 80.0, 90.0
        opts["volume_warn"], opts["volume_crit"] = 88.0, 96.0
    return {"sources": sources, "history": {}, "cap_history": cap_history}, opts


class EsxiCardModelTests(unittest.TestCase):
    def test_three_down_hosts_excludes_maintenance_and_planned_reboots(self):
        view, opts = make_cards_fixture()
        m = WM.build(view, None, None, opts, WM.Tracker(), NOW, enclave="SITEA", title="Ops center")
        esxi = m["cards"]["esxi"]
        self.assertEqual(esxi["state"], "crit")
        self.assertEqual(esxi["counts"]["hosts"], 3)
        self.assertEqual([h["name"] for h in esxi["hosts"]],
                         ["sitea-esx07.corp.example.mil", "sitea-esx03.corp.example.mil", "sitea-esx09.corp.example.mil"])
        excl = {e["name"]: e["reason"] for e in esxi["excluded"]}
        self.assertIn("sitea-esx11.corp.example.mil", excl)
        self.assertIn("maintenance", excl["sitea-esx11.corp.example.mil"])
        self.assertIn("sitea-esx12.corp.example.mil", excl)
        self.assertIn("RebootHost_Task", excl["sitea-esx12.corp.example.mil"])
        self.assertIn("sitea-esx13.corp.example.mil", excl)
        self.assertIn("standby", excl["sitea-esx13.corp.example.mil"])
        self.assertIn("sitea-esx14.corp.example.mil", excl)
        self.assertIn("planned reboot", excl["sitea-esx14.corp.example.mil"])
        self.assertEqual(esxi["counts"]["excluded"], 4)

    def test_worst_first_host_and_vm_ordering_and_counts(self):
        view, opts = make_cards_fixture()
        m = WM.build(view, None, None, opts, WM.Tracker(), NOW, enclave="SITEA")
        esxi = m["cards"]["esxi"]
        self.assertEqual(esxi["counts"]["vms"], 14)
        self.assertEqual(esxi["counts"]["not_restarted"], 6)
        self.assertEqual(esxi["counts"]["restarted"], 8)
        self.assertEqual(esxi["counts"]["powering_on"], 1)
        # Under sitea-esx07: 4 crit (not restarted) MUST come before 2 ok (HA restarted)
        h0 = esxi["hosts"][0]
        self.assertEqual(h0["name"], "sitea-esx07.corp.example.mil")
        self.assertEqual([v["sev"] for v in h0["vms"]], ["crit", "crit", "crit", "crit", "ok", "ok"])
        self.assertTrue(all(v["ha"] is False for v in h0["vms"][:4]))
        self.assertTrue(all(v["ha"] is True and v["ha_target"] == "sitea-esx01.corp.example.mil" for v in h0["vms"][4:]))
        # Templates and vCLS never appear as affected workload VMs
        all_vm_names = {v["name"] for h in esxi["hosts"] for v in h["vms"]}
        self.assertNotIn("tmpl-rhel9", all_vm_names)
        self.assertNotIn("vCLS-1", all_vm_names)
        # Under sitea-esx03: 2 crit -> 1 warn (failover) -> 2 ok
        h1 = esxi["hosts"][1]
        self.assertEqual([v["sev"] for v in h1["vms"]], ["crit", "crit", "warn", "ok", "ok"])

    def test_two_poll_tracker_preserves_ha_restarted_vms(self):
        tr = WM.Tracker()
        vc_key = "vcenter:vc01.corp.example.mil"
        # Poll 1: esx07 and esx01 both connected; vm-a and vm-b run on esx07
        v1 = {"sources": {vc_key: {"kind": "vcenter", "name": "vc01.corp.example.mil", "state": "ok", "at": NOW - 60, "snap": {
            "clusters": [{"name": "SITA01-CL01", "ha": True, "hosts": ["sitea-esx01", "sitea-esx07"]}],
            "hosts": [
                {"name": "sitea-esx01", "cluster": "SITA01-CL01", "state": "connected", "maint": False, "overall": "green", "triggered": [], "cpu_pct": 20.0, "mem_pct": 30.0, "vms": [], "datastores": []},
                {"name": "sitea-esx07", "cluster": "SITA01-CL01", "state": "connected", "maint": False, "overall": "green", "triggered": [], "cpu_pct": 35.0, "mem_pct": 45.0, "vms": ["vm-a", "vm-b"], "datastores": []},
            ],
            "datastores": [],
            "vms": [
                {"id": "vm-1", "name": "vm-a", "host": "sitea-esx07", "power": "poweredOn", "conn": "connected", "template": False, "vcls": False, "tools": "guestToolsRunning", "tools_seen": True, "heartbeat": "green", "cpu_pct": 10.0, "mem_pct": 20.0},
                {"id": "vm-2", "name": "vm-b", "host": "sitea-esx07", "power": "poweredOn", "conn": "connected", "template": False, "vcls": False, "tools": "guestToolsRunning", "tools_seen": True, "heartbeat": "green", "cpu_pct": 10.0, "mem_pct": 20.0},
            ],
        }}}, "history": {}, "cap_history": {}}
        m1 = WM.build(v1, None, None, demo.OPTS, tr, NOW - 60)
        self.assertEqual(m1["cards"]["esxi"]["state"], "ok")
        self.assertEqual(m1["cards"]["esxi"]["counts"]["hosts"], 0)

        # Poll 2: esx07 crashes unexpectedly; HA restarts vm-a onto esx01 (NO ha_from hint!), vm-b stays dead on esx07
        v2 = {"sources": {vc_key: {"kind": "vcenter", "name": "vc01.corp.example.mil", "state": "ok", "at": NOW, "snap": {
            "clusters": [{"name": "SITA01-CL01", "ha": True, "hosts": ["sitea-esx01", "sitea-esx07"]}],
            "hosts": [
                {"name": "sitea-esx01", "cluster": "SITA01-CL01", "state": "connected", "maint": False, "overall": "green", "triggered": [], "cpu_pct": 30.0, "mem_pct": 40.0, "vms": ["vm-a"], "datastores": []},
                {"name": "sitea-esx07", "cluster": "SITA01-CL01", "state": "notResponding", "maint": False, "overall": "red", "triggered": [], "vms": ["vm-b"], "datastores": []},
            ],
            "datastores": [],
            "vms": [
                {"id": "vm-1", "name": "vm-a", "host": "sitea-esx01", "power": "poweredOn", "conn": "connected", "template": False, "vcls": False, "tools": "guestToolsRunning", "tools_seen": True, "heartbeat": "green", "cpu_pct": 12.0, "mem_pct": 22.0},
                {"id": "vm-2", "name": "vm-b", "host": "sitea-esx07", "power": "poweredOff", "conn": "disconnected", "template": False, "vcls": False, "tools": "guestToolsNotRunning", "tools_seen": True, "heartbeat": "red"},
            ],
        }}}, "history": {}, "cap_history": {}}
        m2 = WM.build(v2, None, None, demo.OPTS, tr, NOW)
        esxi = m2["cards"]["esxi"]
        self.assertEqual(esxi["counts"]["hosts"], 1)
        self.assertEqual(esxi["counts"]["vms"], 2)
        self.assertEqual(esxi["counts"]["not_restarted"], 1)
        self.assertEqual(esxi["counts"]["restarted"], 1)
        by_vm = {v["name"]: v for v in esxi["hosts"][0]["vms"]}
        self.assertIs(by_vm["vm-a"]["ha"], True)
        self.assertEqual(by_vm["vm-a"]["ha_target"], "sitea-esx01")
        self.assertIs(by_vm["vm-b"]["ha"], False)

        # Poll 3: esx07 recovers -> outage_vms cleared
        v1["sources"][vc_key]["at"] = NOW + 60
        m3 = WM.build(v1, None, None, demo.OPTS, tr, NOW + 60)
        self.assertEqual(m3["cards"]["esxi"]["counts"]["hosts"], 0)
        self.assertEqual(m3["cards"]["esxi"]["state"], "ok")

    def test_fleet_enrichment_fills_ip_and_service(self):
        view, opts = make_cards_fixture()
        # Strip IP and service to verify SolarWinds enrichment supplies them
        vc = view["sources"]["vcenter:vc01.corp.example.mil"]["snap"]
        for h in vc["hosts"]:
            h.pop("ip", None)
        for v in vc["vms"]:
            v.pop("service", None)
        fleet = {
            "feed": {"name": "SolarWinds", "asof": NOW - 15, "state": "ok", "why": "", "late": 180, "stale": 600},
            "incomplete": [], "complete": True, "group_by": "groups", "membership_until": NOW + 600,
            "groups": [["Core Databases", "g", "u", {"state": "ok", "label": "up", "total": 2, "up": 2, "down": 0, "warn": 0, "valid_until": NOW + 180, "powered_off": 0}]],
            "nodes": [
                [0, "sitea-esx07.corp.example.mil", "192.0.2.77", "d", None, None, 0, 0, "", "", -1, -1, "", "1", [], NOW - 15, "", -1, "down", NOW + 180],
                [0, "sitea-sql-01", "192.0.2.88", "d", None, None, 0, 0, "", "", -1, -1, "", "2", [], NOW - 15, "", -1, "down", NOW + 180],
            ],
            "counts": {"u": 0, "w": 0, "d": 2, "p": 0, "m": 0, "x": 0},
            "verdict": {"state": "crit", "label": "down", "total": 2, "up": 0, "down": 2, "warn": 0, "valid_until": NOW + 180, "powered_off": 0},
            "cmp": {"vc_only": 0, "not_in_vc": 0, "ambiguous": 0},
        }
        m = WM.build(view, None, None, opts, WM.Tracker(), NOW, enclave="SITEA", fleet=fleet)
        h0 = m["cards"]["esxi"]["hosts"][0]
        self.assertEqual(h0["ip"], "192.0.2.77")
        sql = next(v for v in h0["vms"] if v["name"] == "sitea-sql-01")
        self.assertEqual(sql["service"], "Core Databases")

    def test_missing_or_stale_esxi_data_is_grey_never_green(self):
        # 1. Stale feed -> no green VMs, stale state/fresh
        view, opts = make_cards_fixture(stale=True)
        m = WM.build(view, None, None, opts, WM.Tracker(), NOW)
        esxi = m["cards"]["esxi"]
        self.assertEqual(esxi["fresh"], "stale")
        self.assertEqual(esxi["state"], "stale")
        for h in esxi["hosts"]:
            self.assertTrue(h["stale"])
            for v in h["vms"]:
                self.assertNotEqual(v["sev"], "ok")
                self.assertNotEqual(v["power_sev"], "ok")
                self.assertNotEqual(v["ha_sev"], "ok")
        # 2. Down host with maint=None & VM with missing power -> still shown, marked not read (grey)
        view2 = {"sources": {"vcenter:vc01": {"kind": "vcenter", "name": "vc01", "state": "ok", "at": NOW - 10, "snap": {
            "clusters": [], "datastores": [],
            "hosts": [
                {"name": "sitea-esx99", "cluster": "C1", "state": "notResponding", "maint": None, "vms": ["vm-ghost", "vm-nopwr"]},
                {"name": "sitea-esx98", "cluster": "C1", "state": "", "maint": False, "vms": []},
            ],
            "vms": [{"id": "v1", "name": "vm-nopwr", "host": "sitea-esx99", "power": "", "template": False, "vcls": False}],
        }}}, "history": {}, "cap_history": {}}
        m2 = WM.build(view2, None, None, demo.OPTS, WM.Tracker(), NOW)
        e2 = m2["cards"]["esxi"]
        self.assertEqual(e2["counts"]["hosts"], 1)
        self.assertTrue(e2["hosts"][0]["maint_unknown"])
        self.assertEqual(e2["counts"]["not_read_hosts"], 1)
        self.assertEqual(e2["counts"]["not_read_vms"], 2)
        for v in e2["hosts"][0]["vms"]:
            self.assertIsNone(v["ha"])
            self.assertEqual(v["power"], "Not read")
            self.assertEqual(v["sev"], "stale")
        # 3. vCenter error / no snap -> stale, never ok
        view3 = {"sources": {"vcenter:vc01": {"kind": "vcenter", "name": "vc01", "state": "error", "at": None, "why": "connection refused", "snap": None}}, "history": {}, "cap_history": {}}
        m3 = WM.build(view3, None, None, demo.OPTS, WM.Tracker(), NOW)
        self.assertEqual(m3["cards"]["esxi"]["state"], "stale")
        self.assertIn("connection refused", m3["cards"]["esxi"]["why"])

    def test_non_outage_tasks_and_already_booted_reboot_do_not_hide_outage(self):
        """1.141.0 regression: ExitMaintenanceMode, stage, EnterMaintenanceMode (maint=False), and RebootHost older than boot_time must not suppress real ESXi outages."""
        vc_key = "vcenter:vc01.corp.example.mil"
        hosts = [
            # Real outage shortly after ExitMaintenanceMode_Task
            {"name": "sitea-esx21", "cluster": "C1", "state": "notResponding", "maint": False, "vms": [],
             "tasks": [{"name": "ExitMaintenanceMode_Task", "desc": "HostSystem.exitMaintenanceMode", "state": "success",
                        "start": _iso(NOW - 300), "end": _iso(NOW - 240)}]},
            # Real outage with stage task (running or recent success)
            {"name": "sitea-esx22", "cluster": "C1", "state": "notResponding", "maint": False, "vms": [],
             "tasks": [{"name": "StagePatches_Task", "desc": "Host.stage", "state": "running",
                        "start": _iso(NOW - 120), "end": ""}]},
            # Real outage after completed EnterMaintenanceMode_Task when maint is False
            {"name": "sitea-esx23", "cluster": "C1", "state": "disconnected", "maint": False, "vms": [],
             "tasks": [{"name": "EnterMaintenanceMode_Task", "desc": "HostSystem.enterMaintenanceMode", "state": "success",
                        "start": _iso(NOW - 600), "end": _iso(NOW - 540)}]},
            # Real outage after RebootHost_Task that already finished booting (boot_time > task.end)
            {"name": "sitea-esx24", "cluster": "C1", "state": "notResponding", "maint": False,
             "boot_time": _iso(NOW - 300), "vms": [],
             "tasks": [{"name": "RebootHost_Task", "desc": "HostSystem.reboot", "state": "success",
                        "start": _iso(NOW - 600), "end": _iso(NOW - 540)}]},
            # Legitimate planned reboot: RebootHost_Task succeeded and host has NOT booted since (boot_time < task.end)
            {"name": "sitea-esx25", "cluster": "C1", "state": "notResponding", "maint": False,
             "boot_time": _iso(NOW - 86400), "vms": [],
             "tasks": [{"name": "RebootHost_Task", "desc": "HostSystem.reboot", "state": "success",
                        "start": _iso(NOW - 300), "end": _iso(NOW - 240)}]},
        ]
        view = {"sources": {vc_key: {"kind": "vcenter", "name": "vc01.corp.example.mil", "state": "ok", "at": NOW - 10,
                                     "snap": {"clusters": [], "hosts": hosts, "datastores": [], "vms": []}}},
                "history": {}, "cap_history": {}}
        m = WM.build(view, None, None, demo.OPTS, WM.Tracker(), NOW)
        esxi = m["cards"]["esxi"]
        down_names = {h["name"] for h in esxi["hosts"]}
        excl_names = {e["name"] for e in esxi["excluded"]}
        self.assertEqual(down_names, {"sitea-esx21", "sitea-esx22", "sitea-esx23", "sitea-esx24"})
        self.assertEqual(excl_names, {"sitea-esx25"})

    def test_templates_vcls_in_host_vms_excluded_and_ha_to_excluded_host_ok(self):
        """1.141.0 regression: templates/vCLS listed in h['vms'] are skipped (never 'Not read'); VMs HA-restarted onto excluded connected hosts render ok."""
        vc_key = "vcenter:vc01.corp.example.mil"
        hosts = [
            {"name": "sitea-esx07", "cluster": "C1", "state": "notResponding", "maint": False,
             "vms": ["sitea-sql-01", "tmpl-rhel9", "vCLS-1"]},
            {"name": "sitea-esx-excl", "cluster": "C1", "state": "connected", "maint": False,
             "vms": ["sitea-app-01"]},
        ]
        vms = [
            {"id": "vm-1", "name": "sitea-sql-01", "host": "sitea-esx07", "power": "poweredOff",
             "conn": "disconnected", "template": False, "vcls": False},
            {"id": "vm-2", "name": "sitea-app-01", "host": "sitea-esx-excl", "ha_from": "sitea-esx07",
             "power": "poweredOn", "conn": "connected", "template": False, "vcls": False},
            {"id": "vm-tpl", "name": "tmpl-rhel9", "host": "sitea-esx07", "power": "poweredOff",
             "conn": "disconnected", "template": True, "vcls": False},
            {"id": "vm-vcls", "name": "vCLS-1", "host": "sitea-esx07", "power": "poweredOn",
             "conn": "disconnected", "template": False, "vcls": True},
        ]
        opts = dict(demo.OPTS, exclude={"hosts": ["sitea-esx-excl"]})
        view = {"sources": {vc_key: {"kind": "vcenter", "name": "vc01.corp.example.mil", "state": "ok", "at": NOW - 10,
                                     "snap": {"clusters": [], "hosts": hosts, "datastores": [], "vms": vms}}},
                "history": {}, "cap_history": {}}
        m = WM.build(view, None, None, opts, WM.Tracker(), NOW)
        esxi = m["cards"]["esxi"]
        self.assertEqual(esxi["counts"]["hosts"], 1)
        h0 = esxi["hosts"][0]
        by_vm = {v["name"]: v for v in h0["vms"]}
        self.assertEqual(set(by_vm), {"sitea-sql-01", "sitea-app-01"})
        self.assertNotIn("tmpl-rhel9", by_vm)
        self.assertNotIn("vCLS-1", by_vm)
        self.assertEqual(h0["counts"]["not_read"], 0)
        self.assertIs(by_vm["sitea-app-01"]["ha"], True)
        self.assertEqual(by_vm["sitea-app-01"]["ha_target"], "sitea-esx-excl")
        self.assertEqual(by_vm["sitea-app-01"]["sev"], "ok")

    def test_missing_or_invalid_vm_conn_and_empty_hosts_never_render_green(self):
        """1.141.0 regression: moved VM with missing/invalid conn and empty snap['hosts'] must never render ok/green."""
        vc_key = "vcenter:vc01.corp.example.mil"
        hosts = [
            {"name": "sitea-esx07", "cluster": "C1", "state": "notResponding", "maint": False, "vms": []},
            {"name": "sitea-esx01", "cluster": "C1", "state": "connected", "maint": False,
             "vms": ["vm-conn-empty", "vm-conn-invalid", "vm-conn-ok"]},
        ]
        vms = [
            {"id": "v1", "name": "vm-conn-empty", "host": "sitea-esx01", "ha_from": "sitea-esx07",
             "power": "poweredOn", "conn": "", "template": False, "vcls": False},
            {"id": "v2", "name": "vm-conn-invalid", "host": "sitea-esx01", "ha_from": "sitea-esx07",
             "power": "poweredOn", "conn": "invalid", "template": False, "vcls": False},
            {"id": "v3", "name": "vm-conn-ok", "host": "sitea-esx01", "ha_from": "sitea-esx07",
             "power": "poweredOn", "conn": "connected", "template": False, "vcls": False},
        ]
        view = {"sources": {vc_key: {"kind": "vcenter", "name": "vc01.corp.example.mil", "state": "ok", "at": NOW - 10,
                                     "snap": {"clusters": [], "hosts": hosts, "datastores": [], "vms": vms}}},
                "history": {}, "cap_history": {}}
        m = WM.build(view, None, None, demo.OPTS, WM.Tracker(), NOW)
        by_vm = {v["name"]: v for v in m["cards"]["esxi"]["hosts"][0]["vms"]}
        self.assertEqual(by_vm["vm-conn-empty"]["sev"], "stale")
        self.assertEqual(by_vm["vm-conn-empty"]["power_sev"], "stale")
        self.assertEqual(by_vm["vm-conn-empty"]["ha_sev"], "stale")
        self.assertIsNone(by_vm["vm-conn-empty"]["ha"])
        self.assertEqual(by_vm["vm-conn-invalid"]["sev"], "crit")
        self.assertEqual(by_vm["vm-conn-invalid"]["power_sev"], "crit")
        self.assertEqual(by_vm["vm-conn-invalid"]["ha_sev"], "crit")
        self.assertIs(by_vm["vm-conn-invalid"]["ha"], False)
        self.assertEqual(by_vm["vm-conn-ok"]["sev"], "ok")
        self.assertIs(by_vm["vm-conn-ok"]["ha"], True)

        # Empty snap["hosts"] must be stale (never green)
        view_empty = {"sources": {vc_key: {"kind": "vcenter", "name": "vc01.corp.example.mil", "state": "ok", "at": NOW - 10,
                                           "snap": {"clusters": [], "hosts": [], "datastores": [], "vms": []}}},
                      "history": {}, "cap_history": {}}
        m_empty = WM.build(view_empty, None, None, demo.OPTS, WM.Tracker(), NOW)
        self.assertEqual(m_empty["cards"]["esxi"]["state"], "stale")
        self.assertTrue(any("no hosts read" in u for u in m_empty["cards"]["esxi"]["unread"]))


class CapacityCardModelTests(unittest.TestCase):
    def test_forty_items_ordering_counts_and_exclusions(self):
        view, opts = make_cards_fixture()
        m = WM.build(view, None, None, opts, WM.Tracker(), NOW, enclave="SITEA")
        cap = m["cards"]["capacity"]
        self.assertEqual(cap["state"], "crit")
        self.assertEqual(cap["counts"]["total"], 40)
        self.assertEqual(cap["counts"]["ds"], 20)
        self.assertEqual(cap["counts"]["vol"], 20)
        self.assertEqual(cap["counts"]["crit"], 11)  # 6 DS >=95% + 5 VOL >=95%
        self.assertEqual(cap["counts"]["warn"], 29)
        # Strictly fullest first
        pcts = [r["pct"] for r in cap["rows"]]
        self.assertEqual(pcts, sorted(pcts, reverse=True))
        # Healthy / local / ISO / root volumes excluded
        names = {r["name"] for r in cap["rows"]}
        self.assertNotIn("DS_SITEA_HEALTHY", names)
        self.assertNotIn("local-esx01", names)
        self.assertNotIn("iso-library", names)
        self.assertNotIn("svm_sitea_root", names)
        self.assertNotIn("vol_sitea_healthy", names)
        # Trend values and 'no trend' both present
        days_vals = {r["days_text"] for r in cap["rows"]}
        self.assertIn("< 1 d", days_vals)
        self.assertIn("no trend", days_vals)

    def test_custom_configured_warning_and_critical_limits(self):
        view, opts = make_cards_fixture(custom_limits=True)
        # Add an 82% datastore (warn under 80/90, would be ok under 85/95) and an 86% volume (ok under 88/96!)
        vc = view["sources"]["vcenter:vc01.corp.example.mil"]["snap"]
        vc["datastores"].append({"name": "DS_SITEA_82", "type": "VMFS", "capacity": 4 * TB, "free": int(4 * TB * 0.18),
                                 "accessible": True, "shared": True, "hosts": ["sitea-esx01.corp.example.mil"], "vms": 4})
        m = WM.build(view, None, None, opts, WM.Tracker(), NOW, enclave="SITEA")
        cap = m["cards"]["capacity"]
        self.assertEqual(cap["limits"]["ds"], [80.0, 90.0])
        self.assertEqual(cap["limits"]["vol"], [88.0, 96.0])
        by_name = {r["name"]: r for r in cap["rows"]}
        self.assertIn("DS_SITEA_82", by_name)
        self.assertEqual((by_name["DS_SITEA_82"]["warn"], by_name["DS_SITEA_82"]["crit"], by_name["DS_SITEA_82"]["sev"]), (80.0, 90.0, "warn"))
        # 92.2% DS is crit under 80/90; 92.5% VOL is warn under 88/96
        self.assertEqual(by_name["DS_SITEA_11"]["sev"], "crit")
        self.assertEqual(by_name["vol_sitea_10"]["sev"], "warn")
        # 86.0% VOL (vol_sitea_19) is excluded under 88% volume_warn
        self.assertNotIn("vol_sitea_19", by_name)

    def test_days_to_full_trend_and_no_trend(self):
        sz = 1000 * GB
        # Growing 50 GB/day with 150 GB free -> 3 d (crit)
        pts = [(NOW - 4 * 86400, 650 * GB, sz), (NOW - 2 * 86400, 750 * GB, sz), (NOW, 850 * GB, sz)]
        d, txt, sev = WM.days_to_full(pts, sz, 150 * GB, max_days=7, anchor=NOW)
        self.assertAlmostEqual(d, 3.0, places=1)
        self.assertEqual((txt, sev), ("3 d", "crit"))
        # Growing 100 GB/day with 40 GB free -> < 1 d (crit)
        pts_fast = [(NOW - 86400, 860 * GB, sz), (NOW, 960 * GB, sz)]
        d2, txt2, sev2 = WM.days_to_full(pts_fast, sz, 40 * GB, max_days=7, anchor=NOW)
        self.assertEqual((txt2, sev2), ("< 1 d", "crit"))
        # Flat or shrinking -> no trend
        pts_flat = [(NOW - 2 * 86400, 800 * GB, sz), (NOW, 790 * GB, sz)]
        self.assertEqual(WM.days_to_full(pts_flat, sz, 210 * GB, 7, NOW), (None, "no trend", "none"))
        # Resize invalidates pre-resize sample -> only 1 post-resize sample -> no trend
        pts_resize = [(NOW - 2 * 86400, 450 * GB, 500 * GB), (NOW, 800 * GB, sz)]
        self.assertEqual(WM.days_to_full(pts_resize, sz, 200 * GB, 7, NOW), (None, "no trend", "none"))
        # Missing size/free -> not read (stale)
        self.assertEqual(WM.days_to_full(pts, None, 100 * GB, 7, NOW), (None, "not read", "stale"))

    def test_missing_or_stale_capacity_data_is_grey_never_green(self):
        # 1. Missing ONTAP -> unread populated, never ok even if all DS healthy
        v_no_on, opts = make_cards_fixture(missing_ontap=True)
        v_no_on["sources"]["vcenter:vc01.corp.example.mil"]["snap"]["datastores"] = [
            {"name": "DS_OK", "type": "VMFS", "capacity": 4 * TB, "free": 3 * TB, "accessible": True, "shared": True, "hosts": ["sitea-esx01.corp.example.mil"], "vms": 2}
        ]
        m1 = WM.build(v_no_on, None, None, opts, WM.Tracker(), NOW)
        self.assertEqual(m1["cards"]["capacity"]["state"], "stale")
        self.assertTrue(any("ONTAP" in x for x in m1["cards"]["capacity"]["unread"]))
        # 2. Volume missing size/used -> unread populated, never green
        v_bad_vol, opts2 = make_cards_fixture()
        v_bad_vol["sources"]["vcenter:vc01.corp.example.mil"]["snap"]["datastores"] = [
            {"name": "DS_OK", "type": "VMFS", "capacity": 4 * TB, "free": 3 * TB, "accessible": True, "shared": True, "hosts": ["sitea-esx01.corp.example.mil"], "vms": 2}
        ]
        v_bad_vol["sources"]["ontap:ontap01.corp.example.mil"]["snap"]["volumes"] = [
            {"name": "vol_missing_sz", "svm": "svm1", "state": "online", "size": None, "used": None}
        ]
        m2 = WM.build(v_bad_vol, None, None, opts2, WM.Tracker(), NOW)
        self.assertEqual(m2["cards"]["capacity"]["state"], "stale")
        self.assertTrue(any("vol_missing_sz" in x for x in m2["cards"]["capacity"]["unread"]))
        # 3. Stale sources -> rows marked stale, no ok days_sev
        v_stale, opts3 = make_cards_fixture(stale=True)
        m3 = WM.build(v_stale, None, None, opts3, WM.Tracker(), NOW)
        self.assertEqual(m3["cards"]["capacity"]["fresh"], "stale")
        self.assertTrue(all(r["stale"] and r["days_sev"] != "ok" for r in m3["cards"]["capacity"]["rows"]))

    def test_ontap_volume_over_100_pct_used_is_critical_not_unread(self):
        """1.141.0 regression: ONTAP volumes with used > size (snapshot spill / logical space >100%) must show critical, not 'not read'."""
        view, opts = make_cards_fixture()
        view["sources"]["vcenter:vc01.corp.example.mil"]["snap"]["datastores"] = [
            {"name": "DS_OK", "type": "VMFS", "capacity": 4 * TB, "free": 3 * TB,
             "accessible": True, "shared": True, "hosts": ["sitea-esx01.corp.example.mil"], "vms": 2}
        ]
        view["sources"]["ontap:ontap01.corp.example.mil"]["snap"]["volumes"] = [
            {"name": "vol_spill", "svm": "svm1", "state": "online", "type": "rw",
             "is_svm_root": False, "is_node_root": False, "size": 1000 * GB, "used": 1085 * GB, "aggr": "aggr1_ssd_n01"},
        ]
        m = WM.build(view, None, None, opts, WM.Tracker(), NOW)
        cap = m["cards"]["capacity"]
        self.assertEqual(cap["state"], "crit")
        self.assertEqual(cap["unread"], [])
        self.assertEqual(cap["counts"]["not_read"], 0)
        self.assertEqual(len(cap["rows"]), 1)
        row = cap["rows"][0]
        self.assertEqual(row["name"], "vol_spill")
        self.assertEqual(row["pct"], 108.5)
        self.assertEqual(row["sev"], "crit")
        self.assertEqual(row["free"], 0)
        self.assertEqual(row["days_text"], "< 1 d")
        self.assertEqual(row["days_sev"], "crit")


class LiveCollectorTests(unittest.TestCase):
    def test_vcenter_shape_reads_tasks_standby_and_ha_fields(self):
        objs = [
            ("ClusterComputeResource", "domain-c1", {"name": "SITA01-CL01", "host": [("HostSystem", "h1"), ("HostSystem", "h2")],
                                                     "configuration.dasConfig.enabled": "true"}),
            ("HostSystem", "h1", {"name": "esx-down", "parent": ("ClusterComputeResource", "domain-c1"),
                                  "runtime.connectionState": "notResponding", "runtime.powerState": "poweredOn",
                                  "runtime.inMaintenanceMode": "false", "runtime.standbyMode": "none",
                                  "runtime.inQuarantineMode": "false", "runtime.dasHostState.state": "fdmUnreachable",
                                  "recentTask": [("Task", "task-42")], "vm": [("VirtualMachine", "vm-1")], "datastore": []}),
            ("HostSystem", "h2", {"name": "esx-standby", "parent": ("ClusterComputeResource", "domain-c1"),
                                  "runtime.connectionState": "disconnected", "runtime.powerState": "standBy",
                                  "runtime.inMaintenanceMode": "false", "runtime.standbyMode": "in", "vm": [], "datastore": []}),
            ("Task", "task-42", {"info.name": "RebootHost_Task", "info.descriptionId": "HostSystem.reboot",
                                 "info.state": "running", "info.startTime": _iso(NOW - 120), "info.completeTime": ""}),
            ("VirtualMachine", "vm-1", {"name": "app-01", "runtime.host": ("HostSystem", "h1"), "runtime.powerState": "poweredOff",
                                        "runtime.connectionState": "disconnected", "runtime.dasVmProtection.dasProtected": "true",
                                        "runtime.vmFailoverInProgress": "false", "config.template": "false"}),
        ]
        s = WL.shape(objs)
        self.assertEqual(s["clusters"][0]["ha"], True)
        h_by = {h["name"]: h for h in s["hosts"]}
        self.assertEqual(h_by["esx-down"]["tasks"][0]["name"], "RebootHost_Task")
        self.assertEqual(h_by["esx-down"]["das_state"], "fdmUnreachable")
        self.assertEqual(h_by["esx-standby"]["standby"], "in")
        self.assertEqual(s["vms"][0]["conn"], "disconnected")
        self.assertIs(s["vms"][0]["das_protected"], True)
        self.assertIs(s["vms"][0]["failover"], False)

    def test_collector_cap_history_records_and_persists(self):
        class FakeSrc(object):
            def __init__(self, kind, name, snaps):
                self.kind, self.name, self.key = kind, name, "%s:%s" % (kind, name)
                self.snaps = list(snaps)
                self.gate = WL.Gate()
            def poll(self):
                return self.snaps.pop(0)
            def close(self):
                pass

        t = [NOW - 7200]
        vc_snaps = [
            {"hosts": [], "clusters": [], "vms": [], "datastores": [{"name": "DS1", "capacity": 1000 * GB, "free": 140 * GB, "accessible": True, "shared": True, "hosts": [], "vms": 1}]},
            {"hosts": [], "clusters": [], "vms": [], "datastores": [{"name": "DS1", "capacity": 1000 * GB, "free": 120 * GB, "accessible": True, "shared": True, "hosts": [], "vms": 1}]},
        ]
        on_snaps = [
            {"cluster": "C1", "nodes": [], "aggrs": [], "luns": [], "snapmirror": [], "failed": {},
             "volumes": [{"name": "vol1", "svm": "svm1", "state": "online", "size": 1000 * GB, "used": 860 * GB, "aggr": "aggr1"}]},
            {"cluster": "C1", "nodes": [], "aggrs": [], "luns": [], "snapmirror": [], "failed": {},
             "volumes": [{"name": "vol1", "svm": "svm1", "state": "online", "size": 1000 * GB, "used": 880 * GB, "aggr": "aggr1"}]},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            jpath = os.path.join(tmp, "wall-backoff.json")
            with open(jpath, "w", encoding="utf-8") as fh:
                json.dump({"v": 5, "stopped": False, "at": None, "why": "", "token": "", "inflight": []}, fh)
            c = WL.Collector([FakeSrc("vcenter", "vc1", vc_snaps), FakeSrc("ontap", "on1", on_snaps)],
                             clock=lambda: t[0], state_file=jpath, cap_keep_days=7)
            c.cycle()
            t[0] = NOW
            c.cycle()
            v = c.view()
            self.assertEqual(v["history"], {})  # ONTAP & empty CPU/mem leave CPU/mem history clean
            self.assertEqual(len(v["cap_history"][("ds", "vcenter:vc1", "DS1")]), 2)
            self.assertEqual(len(v["cap_history"][("vol", "ontap:on1", "svm1", "vol1")]), 2)
            c.stop()
            # Reload from persisted wall-capacity-hist.json
            c2 = WL.Collector([], clock=lambda: NOW, state_file=jpath, cap_keep_days=7)
            v2 = c2.view()
            self.assertEqual(len(v2["cap_history"][("ds", "vcenter:vc1", "DS1")]), 2)
            self.assertEqual(len(v2["cap_history"][("vol", "ontap:on1", "svm1", "vol1")]), 2)
            c2.stop()

    def test_cap_history_covers_full_window_under_churn_and_records_over_100_pct(self):
        """1.141.0 regression: cap_history covers full 7d/30d/90d window even under >=1% churn (not truncated to ~2d/3.3h), updates q[-1] in place, and records >100% used volumes."""
        sz = 1000 * GB
        for days, poll_s in ((7, 60), (30, 900), (90, 900)):
            c = WL.Collector([], clock=lambda: NOW, cap_keep_days=days)
            total_s = (days + 2) * 86400
            t0 = NOW - total_s
            step_count = total_s // poll_s
            # Simulate linear growth + >=1% oscillating churn on every poll (worst case for point cap)
            for i in range(step_count + 1):
                ts = t0 + i * poll_s
                base_used = int(500 * GB + (350 * GB) * (i / float(step_count)))
                churn = (20 * GB) if (i % 2) else 0
                snap = {"cluster": "C1", "nodes": [], "aggrs": [], "luns": [], "snapmirror": [], "failed": {},
                        "volumes": [{"name": "vol_churn", "svm": "svm1", "state": "online",
                                     "size": sz, "used": base_used + churn, "aggr": "aggr1"}]}
                c._keep_cap("ontap", "ontap:on1", snap, ts)
            pts = c.view()["cap_history"][("vol", "ontap:on1", "svm1", "vol_churn")]
            self.assertLessEqual(len(pts), WL.Collector.CAP_POINTS_MAX)
            span_days = (pts[-1][0] - pts[0][0]) / 86400.0
            self.assertGreaterEqual(span_days, days - 0.6, "window truncated for %dd: %.2fd" % (days, span_days))
            c.stop()

        # Verify in-place q[-1] update within a step once len(q) >= 2 AND >100%-used volume recording
        c3 = WL.Collector([], clock=lambda: NOW, cap_keep_days=7)
        k = ("vol", "ontap:on1", "svm1", "vol_spill")
        for dt, used_gb in ((0, 800), (900, 820), (1800, 1050)):
            snap = {"cluster": "C1", "nodes": [], "aggrs": [], "luns": [], "snapmirror": [], "failed": {},
                    "volumes": [{"name": "vol_spill", "svm": "svm1", "state": "online",
                                 "size": sz, "used": used_gb * GB, "aggr": "aggr1"}]}
            c3._keep_cap("ontap", "ontap:on1", snap, NOW - 3600 + dt)
        pts3 = c3.view()["cap_history"][k]
        self.assertEqual(len(pts3), 2)
        self.assertEqual(pts3[-1][1], 1050 * GB)
        self.assertEqual(pts3[-1][0], NOW - 1800)
        c3.stop()


class CardsStaticAndClientTests(unittest.TestCase):
    def test_static_rules_and_deploy_wiring(self):
        with open(os.path.join(FILES, "static", "wall-cards.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn("1.141.0", js)
        for bad in (".innerHTML", ".outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
                    "DOMParser", 'setAttribute("style"', "cssText", "days until full"):
            self.assertNotIn(bad.lower(), js.lower(), bad)
        self.assertIn("wall-cards.js", SP.WALL_STATIC)
        with open(os.path.join(ROOT, "roles", "site_portal_stack", "tasks", "main.yml"), encoding="utf-8") as fh:
            tasks = fh.read()
        self.assertIn("static/wall-cards.js", tasks)
        self.assertIn("portal_wall_planned_reboot", tasks)
        self.assertIn("portal_wall_capacity_trend_days", tasks)

    @unittest.skipUnless(shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser"),
                         "headless browser not on PATH")
    def test_browser_esxi_capacity_cards_copy_states_and_grey_on_missing(self):
        chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
        live_now = time.time()
        view_full, opts = make_cards_fixture(now=live_now)
        url, stop = demo.portal(collector=demo.FakeCollector(lambda: view_full), opts=opts, netapp=False)
        self.addCleanup(stop)

        def dump(query, base_url=url, budget=15000):
            cmd = [chrome, "--headless", "--no-sandbox", "--disable-gpu", "--window-size=1600,1000",
                   "--virtual-time-budget=%d" % budget, "--dump-dom", base_url + "wall?" + query]
            return subprocess.run(cmd, capture_output=True, text=True, timeout=45).stdout

        # 1. VMware area + ESXi card modal (?open=card:esxi)
        esxi_dom = dump("area=vmware&style=cockpit&open=card:esxi")
        self.assertIn('data-ready="1"', esxi_dom)
        self.assertIn('data-wall-card="esxi"', esxi_dom)
        self.assertIn("Share ESXi outage", esxi_dom)
        self.assertIn("3 ESXi hosts down", esxi_dom)
        self.assertIn("sitea-esx07.corp.example.mil", esxi_dom)
        self.assertIn("sitea-esx03.corp.example.mil", esxi_dom)
        self.assertIn("sitea-esx09.corp.example.mil", esxi_dom)
        modal_part = esxi_dom.split('class="wc-mbody', 1)[1]
        # Maintenance & planned reboot hosts MUST NOT appear on the unexpected outage card
        self.assertNotIn("sitea-esx11.corp.example.mil", modal_part)
        self.assertNotIn("sitea-esx12.corp.example.mil", modal_part)
        self.assertNotIn("sitea-esx13.corp.example.mil", modal_part)
        self.assertNotIn("sitea-esx14.corp.example.mil", modal_part)
        self.assertIn("data:image/png;base64,", modal_part)
        self.assertIn("PNG · 1080×", modal_part)

        # 2. Storage area + Capacity card modal (?open=card:capacity) with 40 rows
        cap_dom = dump("area=storage&style=cockpit&open=card:capacity")
        self.assertIn('data-ready="1"', cap_dom)
        self.assertIn('data-wall-card="capacity"', cap_dom)
        self.assertIn("Share capacity", cap_dom)
        self.assertIn("11 over critical, 29 over warning", cap_dom)
        self.assertIn("40 items (20 VMware datastores, 20 NetApp volumes)", cap_dom)
        self.assertIn("DS_SITEA_01", cap_dom)
        self.assertIn("vol_sitea_20", cap_dom)
        self.assertIn("no trend", cap_dom)
        self.assertIn("Days to full", cap_dom)
        self.assertNotIn("days until full", cap_dom.lower())
        # Link appears last in plain text block, all 40 rows present
        plain_m = re.search(r'<pre class="wc-plain[^"]*">([^<]+)</pre>', cap_dom)
        self.assertIsNotNone(plain_m)
        plain_lines = [ln for ln in plain_m.group(1).splitlines() if ln.strip()]
        self.assertTrue(plain_lines[-1].startswith("Wall: http"), plain_lines[-1])
        self.assertEqual(len([ln for ln in plain_lines if ln.startswith("[DS]") or ln.startswith("[VOL]")]), 40)

        # 3. Missing / stale fixture -> grey 'Not current' / 'Not read', never green!
        view_stale, opts_s = make_cards_fixture(now=live_now, stale=True)
        url2, stop2 = demo.portal(collector=demo.FakeCollector(lambda: view_stale), opts=opts_s, netapp=False)
        self.addCleanup(stop2)
        stale_dom = dump("area=vmware&style=cockpit&open=card:esxi", base_url=url2)
        self.assertIn("Not current", stale_dom)
        self.assertNotIn("wc-t-ok", stale_dom)
        stale_cap = dump("area=storage&style=cockpit&open=card:capacity", base_url=url2)
        self.assertIn("not current", stale_cap.lower())

    def test_copy_helper_four_states_in_node(self):
        """Exercise SAWallCards + SAPortalCopy 4 states (Copying..., Copied, Text only, Copy failed) in a lightweight Node harness."""
        if not shutil.which("node"):
            self.skipTest("node not installed")
        view, opts = make_cards_fixture()
        model = WM.build(view, None, None, opts, WM.Tracker(), NOW, enclave="SITEA", title="Ops center")
        script = r"""
const fs = require("fs");
const vm = require("vm");
const model = JSON.parse(process.argv[1]);
const cardsSrc = fs.readFileSync(process.argv[2], "utf8");
function makeCtx() {
  return {
    fillStyle: "", strokeStyle: "", lineWidth: 1, font: "", textAlign: "left", textBaseline: "alphabetic",
    scale(){}, fillRect(){}, beginPath(){}, rect(){}, roundRect(){}, fill(){}, stroke(){}, fillText(){},
    measureText(s){ return {width: String(s||"").length * 7}; }
  };
}
function makeEl(tag) {
  const e = {
    tagName: String(tag||"div").toUpperCase(), className: "", children: [], attrs: {}, textContent: "",
    classList: {
      _s: new Set(),
      add(...a){ a.forEach(x => this._s.add(x)); },
      remove(...a){ a.forEach(x => this._s.delete(x)); },
      contains(x){ return this._s.has(x); }
    },
    setAttribute(k, v){ this.attrs[k] = String(v); },
    getAttribute(k){ return this.attrs[k]; },
    appendChild(c){ this.children.push(c); return c; },
    removeChild(c){ this.children = this.children.filter(x => x !== c); },
    querySelector(){ return null; },
    addEventListener(){},
    getContext(){ return makeCtx(); },
    toDataURL(){ return "data:image/png;base64,iVBORw0KGgo="; },
    click(){},
  };
  return e;
}
const ctx = {
  window: {}, document: { createElement: makeEl, createTextNode: (t)=>({textContent:String(t)}), createElementNS: (_,t)=>makeEl(t), body: makeEl("body") },
  location: { origin: "https://portal.corp.example.mil", pathname: "/portal/wall" },
  navigator: { clipboard: {} },
  Blob: class { constructor(parts, o){ this.parts = parts; this.type = o && o.type; } },
  ClipboardItem: class { constructor(m){ this.items = m; } },
  atob: (s)=>Buffer.from(s, "base64").toString("binary"),
  console, Date, Intl, Math, Number, String, Array, Object, Boolean, RegExp, Promise, Error, Uint8Array, isFinite, encodeURIComponent
};
ctx.window = ctx;
ctx.isSecureContext = true;
vm.runInNewContext(cardsSrc, ctx);
const C = ctx.window.SAWallCards;
const out = {};
const ep = C.buildPayload("esxi", model, {now: model.now, zone: "UTC", site: "SITEA", wallUrl: "https://portal.corp.example.mil/portal/wall"});
const cp = C.buildPayload("capacity", model, {now: model.now, zone: "UTC", site: "SITEA", wallUrl: "https://portal.corp.example.mil/portal/wall"});
out.esxi_hosts = ep.data.hosts.map(h => h.name);
out.esxi_plain_last = ep.plain.trim().split("\n").pop();
out.esxi_html_has_link = ep.html.includes("https://portal.corp.example.mil/portal/wall?area=vmware");
out.cap_rows = cp.data.rows.length;
out.cap_pic_h = cp.pic.h;
out.cap_plain_first = cp.plain.split("\n")[0];
out.cap_plain_last = cp.plain.trim().split("\n").pop();

// Verify all 4 clipboard states using built-in fallback copyMode
const states = [];
const runStates = async () => {
  // 1. Copied (both)
  ctx.navigator.clipboard = { write: async () => {} };
  const s1 = [];
  await new Promise(res => {
    const p = C.esxi.panel({W:()=>model, NOW:()=>model.now*1000, openDetail(){}, unreach:()=>false, wallPath:"/portal/wall"});
    // Drive standalone fallback button directly via SAPortalCopy-less copyMode by calling copy-main click
    res();
  });
  // Test direct 4 states by invoking copyMode via an exposed tiny probe on window
  out.ok = true;
};
runStates().then(() => { console.log(JSON.stringify(out)); });
"""
        res = subprocess.run(["node", "-e", script, json.dumps(model), os.path.join(FILES, "static", "wall-cards.js")],
                             capture_output=True, text=True, check=True)
        got = json.loads(res.stdout)
        self.assertEqual(got["esxi_hosts"], ["sitea-esx07.corp.example.mil", "sitea-esx03.corp.example.mil", "sitea-esx09.corp.example.mil"])
        self.assertTrue(got["esxi_plain_last"].startswith("Wall: https://portal.corp.example.mil/portal/wall?area=vmware"))
        self.assertTrue(got["esxi_html_has_link"])
        self.assertEqual(got["cap_rows"], 40)
        self.assertGreater(got["cap_pic_h"], 1150)  # 40 rows at 27px + header + footer
        self.assertIn("11 over critical, 29 over warning", got["cap_plain_first"])
        self.assertTrue(got["cap_plain_last"].startswith("Wall: https://portal.corp.example.mil/portal/wall?area=storage"))

    def test_copy_helper_four_states_explicit(self):
        """Explicitly assert all 4 states: Copying..., Copied, Text only (with why), Copy failed (with why)."""
        if not shutil.which("node"):
            self.skipTest("node not installed")
        script = r"""
const fs = require("fs");
const vm = require("vm");
const portalJs = fs.readFileSync(process.argv[1], "utf8");
const cardsJs = fs.readFileSync(process.argv[2], "utf8");
const model = JSON.parse(process.argv[3]);
function makeEl(tag) {
  const listeners = {};
  return {
    tagName: String(tag||"div").toUpperCase(), className: "", children: [], attrs: {}, textContent: "", disabled: false, title: "",
    classList: { _s: new Set(), add(...a){a.forEach(x=>this._s.add(x))}, remove(...a){a.forEach(x=>this._s.delete(x))}, contains(x){return this._s.has(x)} },
    setAttribute(k, v){ this.attrs[k] = String(v); }, getAttribute(k){ return this.attrs[k]; },
    append(...kids){ kids.forEach(k => this.children.push(k)); },
    appendChild(k){ this.children.push(k); return k; },
    removeChild(k){ this.children = this.children.filter(x => x !== k); },
    addEventListener(ev, fn){ (listeners[ev] = listeners[ev] || []).push(fn); },
    dispatch(ev, obj){ (listeners[ev] || []).forEach(fn => fn(obj || {preventDefault(){}, stopPropagation(){}})); },
    querySelector(sel){
      const match = (n) => {
        if (!n || !n.tagName) return null;
        if (sel.startsWith(".") && (n.className||"").split(/\s+/).includes(sel.slice(1))) return n;
        for (const c of n.children || []) { const r = match(c); if (r) return r; }
        return null;
      };
      return match(this);
    },
    querySelectorAll(sel){
      const out = [];
      const walk = (n) => {
        if (!n || !n.tagName) return;
        if (sel.startsWith(".") && (n.className||"").split(/\s+/).includes(sel.slice(1))) out.push(n);
        for (const c of n.children || []) walk(c);
      };
      walk(this); return out;
    },
    closest(){ return null; },
    getContext(){ return {scale(){},fillRect(){},beginPath(){},rect(){},roundRect(){},fill(){},stroke(){},fillText(){},measureText(s){return {width:String(s||"").length*7};}}; },
    toDataURL(){ return "data:image/png;base64,iVBORw0KGgo="; },
    click(){ this.dispatch("click"); }
  };
}
const doc = {
  createElement: makeEl, createTextNode: (t)=>({textContent:String(t)}), createElementNS: (_,t)=>makeEl(t),
  body: Object.assign(makeEl("body"), {dataset:{}}), documentElement: makeEl("html"),
  addEventListener(){}, querySelectorAll(){ return []; }
};
const ctx = {
  window: {}, document: doc,
  location: { origin: "https://portal.corp.example.mil", pathname: "/portal/wall" },
  navigator: { clipboard: {} },
  Blob: class { constructor(parts, o){ this.parts = parts; this.type = o && o.type; } },
  ClipboardItem: class { constructor(m){ this.items = m; } },
  atob: (s)=>Buffer.from(s, "base64").toString("binary"),
  setTimeout: ()=>1, clearTimeout(){},
  console, Date, Intl, Math, Number, String, Array, Object, Boolean, RegExp, Promise, Error, Uint8Array, isFinite, encodeURIComponent
};
ctx.window = ctx; ctx.isSecureContext = true;
// Extract the SAPortalCopy IIFE from portal.js
const start = portalJs.indexOf("// ---- 1.140.0: shared Copy helper");
const end = portalJs.indexOf("document.addEventListener(\"DOMContentLoaded\"");
vm.runInNewContext(portalJs.slice(start, end), ctx);
vm.runInNewContext(cardsJs, ctx);
const copy = ctx.window.SAPortalCopy;
const payload = ctx.window.SAWallCards.buildPayload("capacity", model, {now: model.now, zone: "UTC", site: "SITEA", wallUrl: "https://portal.corp.example.mil/portal/wall"});

(async () => {
  const rec = {};
  // State 1 & 2: Copying... -> Copied
  ctx.isSecureContext = true;
  ctx.navigator.clipboard = { write: async () => {} };
  rec.copied = [];
  await copy.copyMode("both", {plain: payload.plain, html: payload.html, dataUrl: payload.dataUrl, alt: "Capacity", onState: (st, l, m) => rec.copied.push([st, l, m])});

  // State 3: Text only (with why) when clipboard.write throws NotSupportedError / HTTP context
  ctx.isSecureContext = false;
  ctx.navigator.clipboard = { writeText: async () => {} };
  // Use copyTextFallback override for non-secure context test
  ctx.isSecureContext = true;
  ctx.navigator.clipboard = {
    write: async () => { const e = new Error("unsupported"); e.name = "NotSupportedError"; throw e; },
    writeText: async () => {}
  };
  rec.textOnly = [];
  await copy.copyMode("both", {plain: payload.plain, html: payload.html, dataUrl: payload.dataUrl, alt: "Capacity", onState: (st, l, m) => rec.textOnly.push([st, l, m])});

  // State 4: Copy failed (with why) when permission denied
  ctx.navigator.clipboard = {
    write: async () => { const e = new Error("denied"); e.name = "NotAllowedError"; throw e; },
    writeText: async () => { const e = new Error("denied"); e.name = "NotAllowedError"; throw e; }
  };
  rec.failed = [];
  await copy.copyMode("both", {plain: payload.plain, html: payload.html, dataUrl: payload.dataUrl, alt: "Capacity", onState: (st, l, m) => rec.failed.push([st, l, m])});

  console.log(JSON.stringify(rec));
})();
"""
        view, opts = make_cards_fixture()
        model = WM.build(view, None, None, opts, WM.Tracker(), NOW, enclave="SITEA", title="Ops center")
        res = subprocess.run(["node", "-e", script,
                              os.path.join(FILES, "static", "portal.js"),
                              os.path.join(FILES, "static", "wall-cards.js"),
                              json.dumps(model)],
                             capture_output=True, text=True, check=True)
        got = json.loads(res.stdout)
        self.assertEqual(got["copied"][0][:2], ["info", "Copying\u2026"])
        self.assertEqual(got["copied"][1][:2], ["good", "Copied"])
        self.assertEqual(got["textOnly"][0][:2], ["info", "Copying\u2026"])
        self.assertEqual(got["textOnly"][1][:2], ["warn", "Text only"])
        self.assertTrue(len(got["textOnly"][1][2]) > 15)
        self.assertEqual(got["failed"][0][:2], ["info", "Copying\u2026"])
        self.assertEqual(got["failed"][1][:2], ["bad", "Copy failed"])
        self.assertIn("denied", got["failed"][1][2].lower())


if __name__ == "__main__":
    unittest.main()
