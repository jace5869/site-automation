#!/usr/bin/env python3
"""Unit tests for the Horizon golden image check (playbooks/horizon_image_check.yml): every decision of
plugins/filter/horizon_image_filters.py - the vCenter side's checks, the reading of the guest script's answer (recorded
JSON, as roles/horizon/files/horizon_image.ps1 prints it) and the guest side's checks, the report and its READY / NOT
READY - and the pure parts of roles/vmware_vm/library/site_vmware_horizon_image.py (devices, snapshots, the name
match; fake vSphere objects, no pyVmomi). The module part needs ansible-core importable; the filters need the
standard library only.
    python3 tests/test_horizon_image.py"""
import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
import horizon_image_filters as f  # noqa: E402

# what horizon_image.ps1 printed on a golden image that is ready (recorded; names and addresses are placeholders)
GOOD_FACTS = {
    "errors": [],
    "os": {"caption": "Microsoft Windows 11 Enterprise", "version": "10.0.22631", "build": "22631"},
    "computer": {"name": "W11-GOLD", "dns_name": "w11-gold", "domain": "corp.example.mil", "part_of_domain": True, "workgroup": ""},
    "programs": [
        {"name": "VMware Tools", "version": "12.4.0.23259341", "publisher": "VMware, Inc.", "install_date": "20261001", "key": "{T}"},
        {"name": "Omnissa Horizon Agent", "version": "8.14.0.25172", "publisher": "Omnissa, LLC", "install_date": "20261002", "key": "{A}"},
        {"name": "Omnissa Horizon Agent Direct-Connection Plugin", "version": "8.14.0.25172", "install_date": "20261002", "key": "{D}"},
        {"name": "Microsoft FSLogix Apps", "version": "2.9.8884.27471", "publisher": "Microsoft Corporation", "install_date": "20261003", "key": "{F}"},
        {"name": "Microsoft FSLogix Apps RuleEditor", "version": "2.9.8884.27471", "install_date": "20261003", "key": "{R}"}],
    "horizon_keys": [{"path": "HKLM\\SOFTWARE\\Omnissa\\Horizon", "values": {"ProductVersion": "8.14.0"}, "agent": True}],
    "services": [
        {"name": "vmware-viewcomposer-ga", "display_name": "Omnissa Horizon Instant Clone Agent", "state": "Running", "start_mode": "Auto"},
        {"name": "WSNM", "display_name": "Omnissa Horizon View Agent", "state": "Running", "start_mode": "Auto"},
        {"name": "frxsvc", "display_name": "FSLogix Apps Services", "state": "Running", "start_mode": "Auto"},
        {"name": "wuauserv", "display_name": "Windows Update", "state": "Stopped", "start_mode": "Manual"},
        {"name": "TrustedInstaller", "display_name": "Windows Modules Installer", "state": "Stopped", "start_mode": "Manual"}],
    "busy": [],
    "licensing": [{"name": "Windows(R), Enterprise edition", "description": "Windows(R) Operating System, VOLUME_KMSCLIENT channel",
                   "channel": "Volume:GVLK", "license_status": 1, "ad_activation": "", "kms": "", "kms_found": "kmshost.corp.example.mil:1688"}],
    "nics": [{"description": "vmxnet3 Ethernet Adapter", "dhcp": True, "ips": ["192.0.2.50", "fe80::1"]}],
    "reboot": {"cbs": False, "windows_update": False, "file_renames": 0, "rename": False, "domain_join": False}}
GOOD_LINE = "###SITE-JSON### " + json.dumps({"findings": [], "facts": GOOD_FACTS})

GOOD_VM = {"name": "W11-Gold", "moid": "vm-42", "vcenter": "vc01.example.mil", "datacenter": "DC1", "folder": "/DC1/vm/Gold",
           "esxi_host": "esx01.example.mil", "template": False, "power": "poweredOn", "hw_version": "vmx-19", "firmware": "efi",
           "secure_boot": True, "guest_os": "Microsoft Windows 11 (64-bit)", "guest_id": "windows11_64Guest",
           "guest_hostname": "w11-gold.corp.example.mil", "cpus": 2, "cores_per_socket": 2, "memory_mb": 8192,
           "disks": [{"label": "Hard disk 1", "size_gb": 64.0, "thin": True, "datastore": "DS01"}],
           "nics": [{"label": "Network adapter 1", "type": "VirtualVmxnet3", "network": "VDI-VLAN20", "connected": True, "start_connected": True}],
           "cdroms": [{"label": "CD/DVD drive 1", "backing": "client", "file": "", "connected": False, "start_connected": False}],
           "floppies": [], "serials": [], "parallels": [], "vtpm": False,
           "tools_running": "guestToolsRunning", "tools_version": "12352", "tools_status": "guestToolsCurrent", "tools": "toolsOk",
           "snapshots": {"count": 2, "current": "v2026-11", "current_depth": 2, "max_depth": 2, "chain": ["v2026-10", "v2026-11"],
                         "current_created": "2026-11-01T12:00:00Z", "current_state": "poweredOff"}}
VC_OK = [{"name": "vc01.example.mil", "ok": True, "error": "", "note": ""}]
OPTS = {"snapshot_warn": 5, "hw_min": 14, "fslogix": True, "guest": True}


def vc(*vms, **kw):
    return {"ran": True, "why": "", "name": kw.get("name", "W11-Gold"), "vms": list(vms), "vcenters": kw.get("vcenters", VC_OK)}


def guest(**changes):
    d = copy.deepcopy(GOOD_FACTS)
    for k, v in changes.items():
        d[k] = v
    return {"ran": True, "host": "w11-gold.corp.example.mil", "why": "", "data": d}


def vm(**changes):
    d = copy.deepcopy(GOOD_VM)
    d.update(changes)
    return d


def rows_by(rows):
    """{check: (result, found, fix)} - the last row of a check wins."""
    return dict((r["check"], (r["result"], r["found"], r["fix"])) for r in rows)


class Readers(unittest.TestCase):
    def test_versions(self):
        self.assertEqual(f.vtuple("8.14.0.25172"), (8, 14, 0, 25172))
        self.assertEqual(f.vtuple(""), ())
        self.assertEqual(f.horizon_release("8.8.0.20000"), 2212)
        self.assertEqual(f.horizon_release("8.7.0"), 2209)
        self.assertEqual(f.horizon_release("8.14.0"), 2412)
        self.assertEqual(f.horizon_release("2503.1"), 2503)            # a release name as the version
        self.assertEqual(f.horizon_release("7.13.3"), 0)               # Horizon 7: older than any Horizon 8
        self.assertEqual(f.horizon_release("8.99.0"), 9999)            # newer than the table: newer than 2212
        self.assertIsNone(f.horizon_release(""))
        self.assertEqual(f.release_name("8.12.0"), "Horizon 2312")
        self.assertEqual(f.release_name("7.13.3"), "Horizon 7")
        self.assertEqual(f.release_name("8.99"), "a Horizon newer than 2506")
        self.assertEqual(f.release_name("x"), "")

    def test_tools_dates_hw(self):
        self.assertEqual(f.tools_version("12352"), "12.2.0")
        self.assertEqual(f.tools_version(12389), "12.3.5")
        self.assertEqual(f.tools_version("0"), "")
        self.assertEqual(f.tools_version(None), "")
        self.assertEqual(f.install_date("20261001").isoformat(), "2026-10-01")
        self.assertEqual(f.install_date("2026-10-01").isoformat(), "2026-10-01")
        self.assertEqual(f.install_date("10/2/2026").isoformat(), "2026-10-02")
        self.assertIsNone(f.install_date("20261340"))
        self.assertIsNone(f.install_date(""))
        self.assertEqual(f.hw_number("vmx-19"), 19)
        self.assertEqual(f.hw_number(""), 0)

    def test_ps_error(self):
        clixml = '#< CLIXML\r\n<Objs><S S="Error">Access is denied._x000D__x000A_</S><S S="Error">_x001B_[31;1mline 2</S></Objs>'
        self.assertEqual(f.ps_error(clixml), "Access is denied. line 2")
        self.assertEqual(f.ps_error("plain\n text"), "plain text")


class Answers(unittest.TestCase):
    def test_vcenter_result(self):
        r = f.hzi_vcenter_result({}, "W11-Gold", "no credential")
        self.assertEqual((r["ran"], r["why"], r["vms"]), (False, "no credential", []))
        r = f.hzi_vcenter_result({"failed": True, "msg": "vCenter vc01 refused the login"}, "W11-Gold")
        self.assertEqual((r["ran"], r["why"]), (False, "vCenter vc01 refused the login"))
        r = f.hzi_vcenter_result({"changed": False, "vms": [GOOD_VM], "vcenters": VC_OK}, "W11-Gold")
        self.assertEqual((r["ran"], r["why"], len(r["vms"]), r["name"]), (True, "", 1, "W11-Gold"))
        self.assertFalse(f.hzi_vcenter_result({"skipped": True}, "x")["ran"])     # no vms: no answer

    def test_guest_result(self):
        r = f.hzi_guest_result({"unreachable": True, "msg": "ntlm: HTTPSConnectionPool(host='w11-gold', port=5986): timed out\nmore"}, "w11-gold")
        self.assertFalse(r["ran"])
        self.assertTrue(r["why"].startswith("not reached over WinRM: ntlm: "))
        self.assertNotIn("\n", r["why"])
        r = f.hzi_guest_result({"rc": 1, "stdout_lines": ["noise"], "stderr": '<S S="Error">Get-CimInstance: Access denied</S>'})
        self.assertEqual(r["why"], "the PowerShell script gave no result (exit code 1): Get-CimInstance: Access denied")
        r = f.hzi_guest_result({"rc": 0, "stdout_lines": ["###SITE-JSON### {not json"]})
        self.assertIn("is not JSON", r["why"])
        r = f.hzi_guest_result({"rc": 0, "stdout_lines": ["warming up", GOOD_LINE, ""]}, "w11-gold.corp.example.mil")
        self.assertEqual((r["ran"], r["host"], r["why"]), (True, "w11-gold.corp.example.mil", ""))
        self.assertEqual(r["data"]["computer"]["domain"], "corp.example.mil")
        r = f.hzi_guest_result({"rc": 0, "stdout": "x\r\n" + GOOD_LINE + "\r\n"})        # stdout only: read the same
        self.assertTrue(r["ran"])
        self.assertEqual(f.hzi_guest_result({})["why"], "the PowerShell script gave no result (exit code ?): no output")
        # the connection could not start (the rescue keeps the failed result): no rc
        r = f.hzi_guest_result({"failed": True, "msg": "winrm or requests is not installed: No module named 'winrm'"})
        self.assertEqual(r["why"], "could not run the PowerShell script: winrm or requests is not installed: No module named 'winrm'")


class VCenterSide(unittest.TestCase):
    def test_not_run(self):
        rows, v = f.vcenter_checks({"ran": False, "why": 'no "VMware vCenter" credential is attached to the template'})
        self.assertIsNone(v)
        self.assertEqual(rows[0]["result"], "FAIL")
        self.assertIn("Attach the", rows[0]["fix"])
        rows, _ = f.vcenter_checks({"ran": False, "why": "horizon_image_vm is not set, and the Limit names no single host"})
        self.assertIn("Set horizon_image_vm", rows[0]["fix"])
        rows, _ = f.vcenter_checks({"ran": False, "why": "pyVmomi is not installed"})
        self.assertIn("Fix what it says", rows[0]["fix"])

    def test_not_found_and_twice(self):
        unread = VC_OK + [{"name": "vc02.example.mil", "ok": False, "error": "Could not connect to vCenter vc02.example.mil: timed out"}]
        rows, v = f.vcenter_checks(vc(vcenters=unread))
        self.assertIsNone(v)
        self.assertEqual(rows[0]["result"], "FAIL")
        self.assertIn("no VM named W11-Gold in the vCenters read; not read: Could not connect", rows[0]["found"])
        two = VC_OK + [{"name": "vc02.example.mil", "ok": True, "error": ""}]
        rows, v = f.vcenter_checks(vc(vm(), vm(folder="/DC1/vm/Old", vcenter="vc02.example.mil"), vcenters=two))
        self.assertIsNone(v)
        self.assertIn("2 VMs are named W11-Gold: W11-Gold (vc01.example.mil /DC1/vm/Gold), W11-Gold (vc02.example.mil /DC1/vm/Old)", rows[0]["found"])
        self.assertIn("path", rows[0]["fix"])
        rows, v = f.vcenter_checks(vc(vm(), vm(folder="/DC1/vm/Old")))           # one vCenter: no vCenter names
        self.assertEqual(rows[0]["found"], "2 VMs are named W11-Gold: W11-Gold (/DC1/vm/Gold), W11-Gold (/DC1/vm/Old)")
        rows, v = f.vcenter_checks(vc(vm(), vcenters=unread))
        self.assertEqual(rows_by(rows)["vCenters read"][0], "WARN")

    def test_good_vm(self):
        rows, v = f.vcenter_checks(vc(vm()), OPTS)
        self.assertEqual(v["name"], "W11-Gold")
        bad = [r for r in rows if r["result"] in ("WARN", "FAIL")]
        self.assertEqual(bad, [])
        r = rows_by(rows)
        self.assertEqual(r["VM found"][1], "W11-Gold (/DC1/vm/Gold), host esx01.example.mil")
        self.assertEqual(r["VMware Tools"][1], "version 12.2.0 (12352), guestToolsCurrent, running")
        self.assertEqual(r["Network adapters"][1], "Network adapter 1 Vmxnet3 on VDI-VLAN20")
        self.assertEqual(r["CD/DVD drive"][1], "CD/DVD drive 1: client, not connected")
        self.assertEqual(r["Hardware version"][1], "vmx-19 (efi, secure boot on)")
        self.assertEqual(r["Snapshots"][1], "2 snapshot(s); the current one, v2026-11, is 2 deep (v2026-10 > v2026-11), taken 2026-11-01 12:00:00 UTC")
        self.assertEqual(r["Size"], ("INFO", "2 vCPU (2 per socket), 8 GB memory, Hard disk 1 64 GB thin on DS01", ""))
        self.assertEqual(r["Power state"][0], "INFO")
        self.assertIn("snapshot it powered off", r["Power state"][1])
        self.assertEqual(r["vTPM"][0], "PASS")
        for name in ("Floppy drive", "Serial port", "Parallel port"):
            self.assertEqual(r[name], ("PASS", "none", ""))

    def test_bad_devices(self):
        v = vm(nics=[{"label": "Network adapter 1", "type": "VirtualE1000e", "network": "VDI", "connected": True, "start_connected": True},
                     {"label": "Network adapter 2", "type": "VirtualVmxnet3", "network": "VDI", "connected": False, "start_connected": False}],
               cdroms=[{"label": "CD/DVD drive 1", "backing": "iso", "file": "[DS01] iso/win11.iso", "connected": False, "start_connected": True},
                       {"label": "CD/DVD drive 2", "backing": "host", "file": "/vmfs/devices/cdrom/mpx.vmhba0", "connected": True,
                        "start_connected": False}],
               floppies=[{"label": "Floppy drive 1", "backing": "image", "connected": False, "start_connected": False}],
               serials=[{"label": "Serial port 1", "backing": "host", "connected": True, "start_connected": True}],
               parallels=[{"label": "Parallel port 1", "backing": "file", "connected": False, "start_connected": False}],
               vtpm=True, hw_version="vmx-13", firmware="bios", secure_boot=False)
        r = rows_by(f.vcenter_checks(vc(v), OPTS)[0])
        self.assertEqual(r["Network adapters"][:2], ("WARN", "not VMXNET3: Network adapter 1 (E1000e)"))
        self.assertIn("VMXNET3", r["Network adapters"][2])
        self.assertEqual(r["Adapter connected"][:2], ("WARN", "not set to connect at power on: Network adapter 2"))
        self.assertEqual(r["CD/DVD drive"][0], "FAIL")
        self.assertIn("CD/DVD drive 1 connected to an ISO [DS01] iso/win11.iso", r["CD/DVD drive"][1])
        self.assertIn("CD/DVD drive 2 connected to the ESXi host's drive", r["CD/DVD drive"][1])
        self.assertIn("Client Device", r["CD/DVD drive"][2])
        self.assertEqual(r["Floppy drive"][:2], ("WARN", "Floppy drive 1 (image)"))
        self.assertEqual(r["Serial port"][:2], ("FAIL", "Serial port 1 (host, connected)"))
        self.assertIn("no such host device", r["Serial port"][2])
        self.assertEqual(r["Parallel port"][0], "WARN")
        self.assertEqual(r["vTPM"][0], "FAIL")
        self.assertIn("pool", r["vTPM"][2])
        self.assertEqual(r["Hardware version"][:2], ("WARN", "vmx-13 (bios); horizon_image_hw_min is 14"))
        self.assertEqual(rows_by(f.vcenter_checks(vc(v), dict(OPTS, hw_min=0))[0])["Hardware version"][0], "PASS")
        # an ISO left in the drive but not connected is no problem
        r = rows_by(f.vcenter_checks(vc(vm(cdroms=[{"label": "CD/DVD drive 1", "backing": "iso", "file": "[DS01] a.iso"}])), OPTS)[0])
        self.assertEqual(r["CD/DVD drive"], ("PASS", "CD/DVD drive 1: iso, not connected", ""))
        r = rows_by(f.vcenter_checks(vc(vm(nics=[])), OPTS)[0])
        self.assertEqual(r["Network adapters"][:2], ("FAIL", "no network adapter"))
        r = rows_by(f.vcenter_checks(vc(vm(cdroms=[])), OPTS)[0])
        self.assertEqual(r["CD/DVD drive"], ("PASS", "none", ""))

    def test_snapshots(self):
        deep = {"count": 8, "current": "s7", "current_depth": 7, "max_depth": 7, "chain": ["s%d" % i for i in range(1, 8)]}
        r = rows_by(f.vcenter_checks(vc(vm(snapshots=deep)), OPTS)[0])
        self.assertEqual(r["Snapshots"][0], "WARN")
        self.assertIn("8 snapshot(s); the current one, s7, is 7 deep (s1 > s2 > s3 > s4 > s5 > s6 > s7); more than horizon_image_snapshot_warn (5)",
                      r["Snapshots"][1])
        self.assertEqual(rows_by(f.vcenter_checks(vc(vm(snapshots=deep)), dict(OPTS, snapshot_warn=7))[0])["Snapshots"][0], "PASS")
        five = dict(deep, current_depth=5)
        self.assertEqual(rows_by(f.vcenter_checks(vc(vm(snapshots=five)), OPTS)[0])["Snapshots"][0], "PASS")   # > 5 warns, 5 does not
        r = rows_by(f.vcenter_checks(vc(vm(snapshots={"count": 0})), OPTS)[0])
        self.assertEqual(r["Snapshots"][:2], ("PASS", "none yet: take the one to push after this check, the VM powered off"))

    def test_template_power_tools(self):
        r = rows_by(f.vcenter_checks(vc(vm(template=True)), OPTS)[0])
        self.assertEqual(r["Not a template"][0], "FAIL")
        r = rows_by(f.vcenter_checks(vc(vm(power="suspended")), OPTS)[0])
        self.assertEqual(r["Power state"][0], "WARN")
        r = rows_by(f.vcenter_checks(vc(vm(tools="toolsNotInstalled", tools_version="0", tools_running="guestToolsNotRunning")), OPTS)[0])
        self.assertEqual(r["VMware Tools"][:2], ("FAIL", "not installed"))
        # running beats a status that says not installed (vcsim gives that pair)
        r = rows_by(f.vcenter_checks(vc(vm(tools_status="guestToolsNotInstalled", memory_mb=32, cpus=1, cores_per_socket=0)), OPTS)[0])
        self.assertEqual(r["VMware Tools"][0], "PASS")
        self.assertTrue(r["Size"][1].startswith("1 vCPU (1 per socket), 32 MB memory, "))
        r = rows_by(f.vcenter_checks(vc(vm(tools="toolsNotRunning", tools_running="guestToolsNotRunning")), OPTS)[0])
        self.assertEqual(r["VMware Tools"][:2], ("FAIL", "not running (guestToolsNotRunning), version 12.2.0 (12352)"))
        r = rows_by(f.vcenter_checks(vc(vm(tools_status="guestToolsNeedUpgrade")), OPTS)[0])
        self.assertEqual(r["VMware Tools"][0], "WARN")
        self.assertIn("reinstall the Horizon Agent", r["VMware Tools"][2])
        r = rows_by(f.vcenter_checks(vc(vm(power="poweredOff", tools="toolsNotRunning", tools_running="guestToolsNotRunning")), OPTS)[0])
        self.assertEqual(r["VMware Tools"][:2], ("PASS", "version 12.2.0 (12352), guestToolsCurrent (running not checked: the VM is not on)"))
        self.assertIn("poweredOff", r["Power state"][1])


class GuestSide(unittest.TestCase):
    def test_good(self):
        rows = f.guest_checks(guest(), OPTS, GOOD_VM)
        self.assertEqual([r for r in rows if r["result"] in ("WARN", "FAIL")], [])
        r = rows_by(rows)
        self.assertEqual(r["Horizon Agent"][1], "Omnissa Horizon Agent 8.14.0.25172 = Horizon 2412, installed 2026-10-02")
        self.assertEqual(r["Instant Clone Agent"][1], "Omnissa Horizon Instant Clone Agent (vmware-viewcomposer-ga): Auto, Running")
        self.assertEqual(r["Tools before agent"][1], "VMware Tools 12.4.0.23259341 installed 2026-10-01, the Horizon Agent 2026-10-02")
        self.assertEqual(r["Domain joined"][1], "joined to corp.example.mil")
        self.assertEqual(r["Windows activation"][1], "KMS (Volume:GVLK, kmshost.corp.example.mil:1688), licensed")
        self.assertEqual(r["DHCP"][1], "1 adapter(s) on DHCP")
        self.assertEqual(r["Restart pending"][1], "none")
        self.assertEqual(r["Windows Update busy"][0], "PASS")
        self.assertEqual(r["FSLogix"][1], "Microsoft FSLogix Apps 2.9.8884.27471, service frxsvc Running")
        self.assertEqual(r["Windows"], ("INFO", "Microsoft Windows 11 Enterprise 10.0.22631 (build 22631) on w11-gold.corp.example.mil", ""))
        self.assertNotIn("Same VM", r)

    def test_not_run(self):
        rows = f.guest_checks({"ran": False, "host": "w11-gold", "why": "not reached over WinRM: timed out"}, OPTS, vm(power="poweredOff"))
        self.assertEqual(rows[0]["result"], "FAIL")
        self.assertEqual(rows[0]["found"], "not run: not reached over WinRM: timed out (vCenter says the VM is powered off: power it on for the guest checks)")
        self.assertIn("Limit", rows[0]["fix"])
        rows = f.guest_checks({}, OPTS, None)
        self.assertIn("no Windows host ran it", rows[0]["found"])
        rows = f.guest_checks({"ran": False, "why": "horizon_image_guest is false: the vCenter side only"}, dict(OPTS, guest=False), None)
        self.assertEqual(rows[0]["result"], "INFO")

    def test_same_vm(self):
        r = rows_by(f.guest_checks(guest(computer=dict(GOOD_FACTS["computer"], name="W10-OTHER", dns_name="w10-other")), OPTS, GOOD_VM))
        self.assertEqual(r["Same VM"][0], "FAIL")
        # the NetBIOS name is cut at 15 characters: the DNS name is compared
        long_vm = vm(guest_hostname="win11-golden-image-2026.corp.example.mil")
        g = guest(computer=dict(GOOD_FACTS["computer"], name="WIN11-GOLDEN-IM", dns_name="win11-golden-image-2026"))
        self.assertNotIn("Same VM", rows_by(f.guest_checks(g, OPTS, long_vm)))
        self.assertNotIn("Same VM", rows_by(f.guest_checks(guest(), OPTS, vm(guest_hostname=""))))   # vCenter does not know: not compared

    def test_agent_and_instant_clone(self):
        progs = [p for p in GOOD_FACTS["programs"] if "Horizon" not in p["name"]]
        r = rows_by(f.guest_checks(guest(programs=progs, horizon_keys=[]), OPTS, GOOD_VM))
        self.assertEqual(r["Horizon Agent"][0], "FAIL")
        self.assertEqual(r["Tools before agent"][0], "INFO")
        # the version from the registry when the Uninstall entry is missing (VMware's VDM key)
        r = rows_by(f.guest_checks(guest(programs=progs, horizon_keys=[{"path": "HKLM\\SOFTWARE\\VMware, Inc.\\VMware VDM",
                                                                       "values": {"ProductVersion": "8.6.0"}}]), OPTS, GOOD_VM))
        self.assertEqual(r["Horizon Agent"], ("PASS", "Horizon Agent 8.6.0 = Horizon 2206", ""))
        self.assertEqual(f.agent_version({"horizon_keys": {"path": "p", "values": {"Version": "8.9.0"}}}), ("8.9.0", "p Version"))
        self.assertEqual(f.agent_version({}), ("", ""))
        # VMware's names, a Horizon 7 agent
        vmw = [{"name": "VMware Horizon Agent", "version": "7.13.3.12345", "install_date": "20250101"},
               {"name": "VMware Horizon Client", "version": "8.0", "install_date": "20250101"}]
        self.assertEqual(f.agent_program(vmw)["name"], "VMware Horizon Agent")
        svcs = [s for s in GOOD_FACTS["services"] if s["name"] != "vmware-viewcomposer-ga"]
        r = rows_by(f.guest_checks(guest(services=svcs), OPTS, GOOD_VM))
        self.assertEqual(r["Instant Clone Agent"][0], "FAIL")
        self.assertIn("Modify", r["Instant Clone Agent"][2])
        # found by its display name, another service name
        renamed = svcs + [{"name": "omnissa-ic-ga", "display_name": "Omnissa Horizon Instant Clone Agent", "state": "Running", "start_mode": "Auto"}]
        self.assertEqual(rows_by(f.guest_checks(guest(services=renamed), OPTS, GOOD_VM))["Instant Clone Agent"][0], "PASS")
        off = svcs + [{"name": "vmware-viewcomposer-ga", "display_name": "VMware Horizon Instant Clone Agent", "state": "Stopped", "start_mode": "Disabled"}]
        self.assertEqual(rows_by(f.guest_checks(guest(services=off), OPTS, GOOD_VM))["Instant Clone Agent"][0], "FAIL")
        # one service: PowerShell may hand a single object instead of a list
        one = {"name": "vmware-viewcomposer-ga", "display_name": "X Instant Clone Agent", "state": "Running", "start_mode": "Auto"}
        self.assertEqual(rows_by(f.guest_checks(guest(services=one), OPTS, GOOD_VM))["Instant Clone Agent"][0], "PASS")

    def test_tools_before_agent(self):
        def progs(tools_date, agent_date, tools=True):
            out = [{"name": "Omnissa Horizon Agent", "version": "8.14.0.25172", "install_date": agent_date}]
            return out + ([{"name": "VMware Tools", "version": "12.4.0", "install_date": tools_date}] if tools else [])
        r = rows_by(f.guest_checks(guest(programs=progs("20261005", "20261002")), OPTS, GOOD_VM))
        self.assertEqual(r["Tools before agent"][:2], ("WARN", "VMware Tools 12.4.0 was installed 2026-10-05, after the Horizon Agent (2026-10-02)"))
        self.assertIn("Reinstall", r["Tools before agent"][2])
        r = rows_by(f.guest_checks(guest(programs=progs("20261002", "20261002")), OPTS, GOOD_VM))
        self.assertEqual(r["Tools before agent"][0], "PASS")
        self.assertIn("the same day: the dates cannot tell the order", r["Tools before agent"][1])
        r = rows_by(f.guest_checks(guest(programs=progs("", "20261002")), OPTS, GOOD_VM))
        self.assertEqual(r["Tools before agent"][:2], ("WARN", "an install date is missing (VMware Tools ?, Horizon Agent 2026-10-02): the order is not known"))
        r = rows_by(f.guest_checks(guest(programs=progs("", "20261002", tools=False)), OPTS, GOOD_VM))
        self.assertEqual(r["Tools before agent"][0], "WARN")

    def test_domain_dhcp_reboot_busy(self):
        r = rows_by(f.guest_checks(guest(computer=dict(GOOD_FACTS["computer"], part_of_domain=False, domain="WORKGROUP", workgroup="WORKGROUP")),
                                   OPTS, GOOD_VM))
        self.assertEqual(r["Domain joined"][:2], ("WARN", "not joined to a domain (workgroup WORKGROUP)"))
        r = rows_by(f.guest_checks(guest(nics=[{"description": "vmxnet3 Ethernet Adapter", "dhcp": False, "ips": ["192.0.2.60"]},
                                               {"description": "vmxnet3 #2", "dhcp": True, "ips": ["198.51.100.7"]}]), OPTS, GOOD_VM))
        self.assertEqual(r["DHCP"][:2], ("FAIL", "a fixed address on: vmxnet3 Ethernet Adapter (192.0.2.60)"))
        self.assertEqual(rows_by(f.guest_checks(guest(nics=[]), OPTS, GOOD_VM))["DHCP"][0], "WARN")
        rb = dict(GOOD_FACTS["reboot"], cbs=True, file_renames=3)
        r = rows_by(f.guest_checks(guest(reboot=rb), OPTS, GOOD_VM))
        self.assertEqual(r["Restart pending"][:2], ("FAIL", "a restart is pending: component servicing, 3 file rename(s)"))
        for key, word in (("windows_update", "Windows Update"), ("rename", "a new computer name"), ("domain_join", "a domain join")):
            r = rows_by(f.guest_checks(guest(reboot=dict(GOOD_FACTS["reboot"], **{key: True})), OPTS, GOOD_VM))
            self.assertEqual(r["Restart pending"][:2], ("FAIL", "a restart is pending: " + word))
        r = rows_by(f.guest_checks(guest(reboot=dict(GOOD_FACTS["reboot"], file_renames=2)), OPTS, GOOD_VM))
        self.assertEqual(r["Restart pending"][0], "WARN")
        svcs = [dict(s, state="Running") if s["name"] == "TrustedInstaller" else s for s in GOOD_FACTS["services"]]
        r = rows_by(f.guest_checks(guest(services=svcs, busy=["TiWorker"]), OPTS, GOOD_VM))
        self.assertEqual(r["Windows Update busy"][:2], ("WARN", "at work now: TrustedInstaller running, TiWorker"))
        r = rows_by(f.guest_checks(guest(busy="MoUsoCoreWorker"), OPTS, GOOD_VM))       # one process: a string, not a list
        self.assertEqual(r["Windows Update busy"][:2], ("WARN", "at work now: MoUsoCoreWorker"))

    def test_activation(self):
        kms = GOOD_FACTS["licensing"][0]
        self.assertEqual(f.activation([dict(kms, ad_activation="CN=Windows 11 Enterprise,CN=Activation Objects")], 2412)[:2],
                         ("PASS", "AD-based activation (Volume:GVLK, CN=Windows 11 Enterprise,CN=Activation Objects), licensed"))
        res, found, fix = f.activation([dict(kms, license_status=5)], 2412)
        self.assertEqual((res, found), ("WARN", "KMS (Volume:GVLK, kmshost.corp.example.mil:1688), in notification mode (not activated)"))
        self.assertIn("slmgr /ato", fix)
        mak = {"description": "Windows(R) Operating System, VOLUME_MAK channel", "channel": "Volume:MAK", "license_status": 1}
        self.assertEqual(f.activation([mak], 2209)[0], "WARN")
        self.assertIn("older than 2212", f.activation([mak], 2209)[1])
        self.assertIn("Horizon 7 is older than 2212", f.activation([mak], 0)[1])
        self.assertEqual(f.activation([mak], 2212)[0], "PASS")
        self.assertEqual(f.activation([mak], 9999)[0], "PASS")
        self.assertIn("not known", f.activation([mak], None)[1])
        # the description alone (no ProductKeyChannel on an old Windows)
        self.assertEqual(f.activation([{"description": "Windows(R) Operating System, VOLUME_MAK channel", "license_status": 1}], 2412)[0], "PASS")
        self.assertEqual(f.activation([{"description": "Windows(R) Operating System, RETAIL channel", "channel": "Retail", "license_status": 1}], 2412)[:2],
                         ("WARN", "retail key (Retail), licensed"))
        self.assertEqual(f.activation([{"channel": "OEM:DM", "license_status": 1}], 2412)[1], "OEM key (OEM:DM), licensed")
        self.assertEqual(f.activation([], 2412)[:2], ("WARN", "no Windows product key found"))
        self.assertEqual(f.activation([{"channel": "Volume:CSVLK", "license_status": 1}], 2412)[0], "WARN")
        # the licensed one first when there are two
        self.assertEqual(f.activation([dict(mak, license_status=0), kms], 2209)[0], "PASS")
        # through the guest checks: the agent's release decides MAK
        g = guest(licensing=[mak], programs=[dict(p, version="8.7.0.20000") if p["name"] == "Omnissa Horizon Agent" else p for p in GOOD_FACTS["programs"]])
        self.assertEqual(rows_by(f.guest_checks(g, OPTS, GOOD_VM))["Windows activation"][0], "WARN")

    def test_parts_not_read(self):
        d = copy.deepcopy(GOOD_FACTS)
        for k in ("services", "licensing", "nics", "computer"):
            del d[k]
        d["errors"] = ["licensing: Access denied: SoftwareLicensingProduct", "services: Access denied"]
        r = rows_by(f.guest_checks({"ran": True, "host": "w11-gold", "data": d}, OPTS, GOOD_VM))
        self.assertEqual(r["Windows activation"][:2], ("WARN", "not read (licensing: Access denied: SoftwareLicensingProduct)"))
        self.assertEqual(r["Instant Clone Agent"][:2], ("WARN", "the services were not read (services: Access denied)"))
        self.assertEqual(r["DHCP"][:2], ("WARN", "the network adapters were not read (nics: not read)"))
        self.assertEqual(r["Domain joined"][:2], ("WARN", "not read (computer: not read)"))
        self.assertIn("local administrator", r["DHCP"][2])

    def test_fslogix(self):
        progs = [p for p in GOOD_FACTS["programs"] if "FSLogix" not in p["name"]]
        r = rows_by(f.guest_checks(guest(programs=progs), OPTS, GOOD_VM))
        self.assertEqual(r["FSLogix"][:2], ("WARN", "not installed (but the service frxsvc is there)"))
        r = rows_by(f.guest_checks(guest(programs=progs), dict(OPTS, fslogix=False), GOOD_VM))
        self.assertEqual(r["FSLogix"][0], "INFO")
        self.assertEqual(f.fslogix_program([{"name": "Microsoft FSLogix Apps RuleEditor"}]), None)


class Report(unittest.TestCase):
    def test_ready(self):
        out = f.hzi_report(vc(vm()), guest(), OPTS)
        self.assertTrue(out["ready"])
        self.assertEqual(out["verdict"], "READY: W11-Gold can be shut down, snapshotted and pushed")
        rep = out["report"]
        self.assertEqual(rep["title"], "Horizon golden image W11-Gold: READY")
        self.assertEqual(rep["status"], "ok")
        self.assertEqual(rep["subtitle"], "VM W11-Gold; vCenter vc01.example.mil, /DC1/vm/Gold; guest w11-gold.corp.example.mil")
        self.assertEqual([s["title"] for s in rep["sections"]], ["Verdict", "Checks", "Next"])
        checks = rep["sections"][1]
        self.assertEqual(len(checks["rows"]), len(checks["cell_status"]))
        self.assertEqual(set(c[0] for c in checks["cell_status"]), {"ok", "info"})
        self.assertIn("horizon_push_base_vm W11-Gold", rep["sections"][-1]["lines"][2])
        self.assertEqual(out["vm"]["moid"], "vm-42")
        self.assertEqual(out["fail"], 0)

    def test_ready_with_warnings_and_not_ready(self):
        out = f.hzi_report(vc(vm(floppies=[{"label": "Floppy drive 1", "backing": "client"}])), guest(), OPTS)
        self.assertTrue(out["ready"])
        self.assertEqual(out["verdict"], "READY: W11-Gold can be shut down, snapshotted and pushed (1 warning(s) to look at)")
        self.assertEqual(out["report"]["status"], "warning")
        self.assertIn("Look at", [s["title"] for s in out["report"]["sections"]])
        out = f.hzi_report(vc(vm(vtpm=True, floppies=[{"label": "Floppy drive 1"}])), guest(), OPTS)
        self.assertFalse(out["ready"])
        self.assertEqual(out["verdict"], "NOT READY: 1 problem(s) to fix first, 1 warning(s)")
        rep = out["report"]
        self.assertEqual((rep["title"], rep["status"]), ("Horizon golden image W11-Gold: NOT READY", "critical"))
        fix = [s for s in rep["sections"] if s["title"] == "Fix first"][0]
        self.assertTrue(fix["lines"][0].startswith("vCenter - vTPM: the golden image has a vTPM. Fix: Remove the vTPM"))
        self.assertEqual(rep["sections"][-1]["lines"], ["Fix the FAIL rows, then run this check again."])
        self.assertEqual(rep["summary"][0], {"label": "Verdict", "value": "NOT READY", "status": "critical"})

    def test_guest_not_run_or_not_wanted(self):
        out = f.hzi_report(vc(vm()), {"ran": False, "host": "", "why": "no Limit: the template's Limit names the golden VM"}, OPTS)
        self.assertFalse(out["ready"])                 # the guest checks are part of ready
        self.assertIn("Guest checks", [r["check"] for r in out["checks"] if r["result"] == "FAIL"])
        out = f.hzi_report(vc(vm()), {"ran": False, "why": "horizon_image_guest is false: the vCenter side only"}, dict(OPTS, guest=False))
        self.assertTrue(out["ready"])
        self.assertEqual(out["verdict"], "READY: W11-Gold can be shut down, snapshotted and pushed - the guest part was not checked")
        self.assertEqual(out["report"]["status"], "warning")
        self.assertEqual(out["report"]["summary"][-1]["value"], "no")
        # vCenter not read: the name asked for still in the title
        out = f.hzi_report({"ran": False, "why": "no credential", "name": "W11-Gold"}, guest(), OPTS)
        self.assertEqual(out["report"]["title"], "Horizon golden image W11-Gold: NOT READY")
        self.assertEqual(out["vm"], {})

    def test_guest_read_errors_listed(self):
        g = guest(errors=["licensing: Access denied"])
        secs = f.hzi_report(vc(vm()), g, OPTS)["report"]["sections"]
        self.assertEqual([s for s in secs if s["title"] == "Not read in the guest"][0]["lines"], ["licensing: Access denied"])

    def test_filters_registered(self):
        names = f.FilterModule().filters()
        self.assertEqual(sorted(names), ["hzi_guest_result", "hzi_report", "hzi_vcenter_result"])


# ---- the guest script itself: made-up golden images (tests/windows/run_horizon_image.ps1), read as the job reads it -----

PWSH = os.environ.get("PWSH", shutil.which("pwsh") or "")
RUNNER = os.path.join(HERE, "windows", "run_horizon_image.ps1")


class Script(unittest.TestCase):
    """roles/horizon/files/horizon_image.ps1 with its Windows commands replaced by a scenario's data, normal and in
    Constrained Language Mode. Needs PowerShell 7 (pwsh); PWSH= (empty) skips it."""

    def answer(self, scenario, clm):
        if not PWSH or not os.path.exists(PWSH):
            self.skipTest("no pwsh")
        p = subprocess.run([PWSH, "-NoProfile", "-File", RUNNER, "-Scenario", scenario] + (["-Clm"] if clm else []),
                           capture_output=True, text=True, timeout=180)
        g = f.hzi_guest_result({"rc": p.returncode, "stdout_lines": p.stdout.splitlines(), "stderr": p.stderr}, "w11-gold.corp.example.mil")
        self.assertTrue(g["ran"], "%s: %s\n%s" % (scenario, g["why"], p.stdout[-2000:] + p.stderr[-2000:]))
        return g

    def test_ready(self):
        for clm in (False, True):
            with self.subTest(constrained=clm):
                g = self.answer("ready", clm)
                d = g["data"]
                self.assertEqual(d["errors"], [])
                self.assertEqual(d["horizon_keys"], [{"path": "HKLM\\SOFTWARE\\Omnissa\\Horizon", "agent": True,
                                                      "values": {"ProductVersion": "8.14.0", "BuildNumber": "25172"}}])
                self.assertEqual(sorted(p["name"] for p in d["programs"]),
                                 ["Microsoft FSLogix Apps", "Microsoft FSLogix Apps RuleEditor", "Omnissa Horizon Agent", "VMware Tools"])
                self.assertEqual(d["reboot"], {"cbs": False, "windows_update": False, "file_renames": 0, "rename": False, "domain_join": False})
                rows = f.guest_checks(g, OPTS, GOOD_VM)
                self.assertEqual([(r["check"], r["found"]) for r in rows if r["result"] in ("WARN", "FAIL")], [])
                r = rows_by(rows)
                self.assertEqual(r["Horizon Agent"][1], "Omnissa Horizon Agent 8.14.0.25172 = Horizon 2412, installed 2026-10-02")
                self.assertEqual(r["Windows activation"][1], "KMS (Volume:GVLK, kmshost.corp.example.mil), licensed")
                self.assertEqual(r["DHCP"][1], "1 adapter(s) on DHCP")
                self.assertEqual(r["Windows"][1], "Microsoft Windows 11 Enterprise 10.0.22631 (build 22631) on w11-gold.corp.example.mil")

    def test_not_ready(self):
        for clm in (False, True):
            with self.subTest(constrained=clm):
                g = self.answer("not_ready", clm)
                self.assertEqual(g["data"]["horizon_keys"], [{"path": "HKLM\\SOFTWARE\\VMware, Inc.\\VMware VDM", "agent": False,
                                                              "values": {"ProductVersion": "8.7.0"}}])
                r = rows_by(f.guest_checks(g, OPTS, GOOD_VM))
                self.assertEqual(r["Horizon Agent"], ("PASS", "VMware Horizon Agent 8.7.0.20616018 = Horizon 2209, installed 2026-01-15", ""))
                self.assertEqual(r["Instant Clone Agent"][0], "FAIL")
                self.assertEqual(r["Tools before agent"][:2], ("WARN", "VMware Tools 12.5.0.24276846 was installed 2026-10-20, after the Horizon Agent (2026-01-15)"))
                self.assertEqual(r["Domain joined"][:2], ("WARN", "not joined to a domain (workgroup WORKGROUP)"))
                self.assertEqual(r["Windows activation"][:2], ("WARN", "MAK (Volume:MAK), licensed; Horizon 2209 is older than 2212"))
                self.assertEqual(r["DHCP"][:2], ("FAIL", "a fixed address on: vmxnet3 Ethernet Adapter (192.0.2.60)"))
                self.assertEqual(r["Restart pending"][:2], ("FAIL", "a restart is pending: component servicing, 2 file rename(s)"))
                self.assertEqual(r["Windows Update busy"][:2], ("WARN", "at work now: TrustedInstaller running, wuauserv running, TiWorker"))
                self.assertEqual(r["FSLogix"][:2], ("WARN", "not installed"))

    def test_a_part_refused(self):
        for clm in (False, True):
            with self.subTest(constrained=clm):
                g = self.answer("cim_denied", clm)
                self.assertEqual(g["data"]["errors"], ["licensing: Access denied: SoftwareLicensingProduct"])
                r = rows_by(f.guest_checks(g, OPTS, GOOD_VM))
                self.assertEqual(r["Windows activation"][:2], ("WARN", "not read (licensing: Access denied: SoftwareLicensingProduct)"))
                self.assertEqual(r["Horizon Agent"][0], "PASS")


# ---- the module's pure parts: fake vSphere objects named as pyVmomi's types are ------------------------------------

def fake(type_name, **attrs):
    cls = type(type_name, (object,), {})
    o = cls()
    for k, v in attrs.items():
        setattr(o, k, v)
    return o


def info(label):
    return fake("vim.Description", label=label)


def conn(on=False, start=False):
    return fake("vim.vm.device.VirtualDevice.ConnectInfo", connected=on, startConnected=start)


class Module(unittest.TestCase):
    def setUp(self):
        lib = os.path.join(HERE, "..", "roles", "vmware_vm", "library")
        spec = importlib.util.spec_from_file_location("ansible.module_utils.site_vcenters", os.path.join(
            HERE, "..", "roles", "vmware_vm", "module_utils", "site_vcenters.py"))
        try:
            import ansible.module_utils.basic  # noqa: F401
        except ImportError:                    # no ansible-core here: the filters are tested, the module is not
            self.skipTest("ansible-core is not importable")
        mu = importlib.util.module_from_spec(spec)
        sys.modules["ansible.module_utils.site_vcenters"] = mu
        spec.loader.exec_module(mu)
        if lib not in sys.path:
            sys.path.insert(0, lib)
        import site_vmware_horizon_image
        self.m = site_vmware_horizon_image

    def test_devices(self):
        devs = [
            fake("vim.vm.device.VirtualDisk", deviceInfo=info("Hard disk 1"), capacityInKB=64 * 1024 * 1024,
                 backing=fake("vim.vm.device.VirtualDisk.FlatVer2BackingInfo", fileName="[DS01] W11-Gold/W11-Gold.vmdk", thinProvisioned=True)),
            fake("vim.vm.device.VirtualVmxnet3", deviceInfo=info("Network adapter 1"), connectable=conn(True, True),
                 backing=fake("vim.vm.device.VirtualEthernetCard.NetworkBackingInfo", deviceName="VDI-VLAN20")),
            fake("vim.vm.device.VirtualE1000e", deviceInfo=info("Network adapter 2"), connectable=conn(),
                 backing=fake("vim.vm.device.VirtualEthernetCard.DistributedVirtualPortBackingInfo",
                              port=fake("vim.dvs.PortConnection", portgroupKey="dvportgroup-11"))),
            fake("vim.vm.device.VirtualCdrom", deviceInfo=info("CD/DVD drive 1"), connectable=conn(False, True),
                 backing=fake("vim.vm.device.VirtualCdrom.IsoBackingInfo", fileName="[DS01] iso/win11.iso")),
            fake("vim.vm.device.VirtualCdrom", deviceInfo=info("CD/DVD drive 2"), connectable=conn(),
                 backing=fake("vim.vm.device.VirtualCdrom.RemotePassthroughBackingInfo", deviceName="")),
            fake("vim.vm.device.VirtualCdrom", deviceInfo=info("CD/DVD drive 3"), connectable=conn(True),
                 backing=fake("vim.vm.device.VirtualCdrom.AtapiBackingInfo", deviceName="/vmfs/devices/cdrom/mpx.vmhba0")),
            fake("vim.vm.device.VirtualFloppy", deviceInfo=info("Floppy drive 1"), connectable=conn(),
                 backing=fake("vim.vm.device.VirtualFloppy.RemoteDeviceBackingInfo")),
            fake("vim.vm.device.VirtualSerialPort", deviceInfo=info("Serial port 1"), connectable=conn(True, True),
                 backing=fake("vim.vm.device.VirtualSerialPort.DeviceBackingInfo", deviceName="/dev/char/serial/uart0")),
            fake("vim.vm.device.VirtualParallelPort", deviceInfo=info("Parallel port 1"), connectable=conn(),
                 backing=fake("vim.vm.device.VirtualParallelPort.FileBackingInfo", fileName="[DS01] lpt.txt")),
            fake("vim.vm.device.VirtualTPM", deviceInfo=info("Virtual TPM")),
            fake("vim.vm.device.VirtualUSBController", deviceInfo=info("USB controller")),
        ]
        d = self.m.devices(devs)
        self.assertEqual(d["disks"], [{"label": "Hard disk 1", "size_gb": 64.0, "thin": True, "datastore": "DS01"}])
        self.assertEqual([(n["label"], n["type"], n["network"], n["connected"], n["start_connected"]) for n in d["nics"]],
                         [("Network adapter 1", "VirtualVmxnet3", "VDI-VLAN20", True, True),
                          ("Network adapter 2", "VirtualE1000e", "dvportgroup-11", False, False)])
        self.assertEqual([(c["backing"], c["file"], c["connected"], c["start_connected"]) for c in d["cdroms"]],
                         [("iso", "[DS01] iso/win11.iso", False, True), ("client", "", False, False),
                          ("host", "/vmfs/devices/cdrom/mpx.vmhba0", True, False)])
        self.assertEqual([x["backing"] for x in d["floppies"]], ["client"])
        self.assertEqual([(x["backing"], x["connected"]) for x in d["serials"]], [("host", True)])
        self.assertEqual([x["backing"] for x in d["parallels"]], ["file"])
        self.assertTrue(d["vtpm"])
        self.assertEqual(self.m.devices(None)["vtpm"], False)
        # a drive without a backing
        self.assertEqual(self.m.devices([fake("vim.vm.device.VirtualCdrom", deviceInfo=info("CD"))])["cdroms"][0]["backing"], "none")
        # the whole record goes through the filters
        rows, v = f.vcenter_checks(vc(dict(GOOD_VM, **d)), OPTS)
        r = rows_by(rows)
        self.assertEqual((r["CD/DVD drive"][0], r["Serial port"][0], r["Parallel port"][0], r["Floppy drive"][0], r["vTPM"][0]),
                         ("FAIL", "FAIL", "WARN", "WARN", "FAIL"))

    def test_snapshots(self):
        s = {i: fake("vim.vm.Snapshot", _moId="snapshot-%d" % i) for i in range(1, 5)}

        def node(i, name, kids=()):
            return fake("vim.vm.SnapshotTree", snapshot=s[i], name=name, childSnapshotList=list(kids), createTime=None, state="poweredOff")
        tree = [node(1, "base", [node(2, "v1", [node(3, "v2")]), node(4, "side")])]
        out = self.m.snapshots(fake("vim.vm.SnapshotInfo", rootSnapshotList=tree, currentSnapshot=s[3]))
        self.assertEqual((out["count"], out["current"], out["current_depth"], out["max_depth"], out["chain"], out["current_state"]),
                         (4, "v2", 3, 3, ["base", "v1", "v2"], "poweredOff"))
        out = self.m.snapshots(fake("vim.vm.SnapshotInfo", rootSnapshotList=tree, currentSnapshot=s[4]))
        self.assertEqual((out["current"], out["current_depth"], out["chain"]), ("side", 2, ["base", "side"]))
        self.assertEqual(self.m.snapshots(None)["count"], 0)

    def test_name_match(self):
        self.assertTrue(self.m.matches("W11-Gold", "W11-Gold", "/DC1/vm/Gold"))
        self.assertFalse(self.m.matches("W11-Gold", "w11-gold", "/DC1/vm/Gold"))      # exact first (a case-only match is the fallback)
        self.assertTrue(self.m.matches("/DC1/vm/Gold/W11-Gold", "W11-Gold", "/DC1/vm/Gold"))
        self.assertTrue(self.m.matches("DC1/vm/gold/w11-gold", "W11-Gold", "/DC1/vm/Gold"))
        self.assertFalse(self.m.matches("/DC1/vm/Old/W11-Gold", "W11-Gold", "/DC1/vm/Gold"))
        self.assertEqual(self.m.kind(fake("vim.vm.device.VirtualVmxnet3")), "VirtualVmxnet3")


if __name__ == "__main__":
    unittest.main()
