#!/usr/bin/env python3
"""Fake vCenter (vcsim) helper for tests/vmware/run_vmware_test.sh.

  vcsim.py setup   give the simulator's VMs what the tests need
  vcsim.py state   print every VM as JSON: power, snapshots (name, description), notes, adapters

Setup (vcsim's default inventory: DC0_H0_VM0/1, DC0_C0_RP0_VM0/1; port groups DC0_DVPG0/1, VM Network):
  DC0_H0_VM0      the AAP server: guest host name aap01.example.mil, IP 10.0.0.5, Tools running, a
                  snapshot "aap-snap" (the snapshot cleanup must never delete it)
  DC0_H0_VM1      web01: Tools running, EFI, a second adapter (VM Network), a snapshot
                  "baseline" (vcsim leaves out the snapshot property of a VM with none, where vCenter
                  returns it empty - the snapshot module reads it); its host gets port group VLAN200
  DC0_C0_RP0_VM0  Tools NOT running, EFI without secure boot
  DC0_C0_RP0_VM1  BIOS, Tools running, a snapshot "baseline" taken by CORP\\jdoe (a posted task event)
  dup (x2)        two VMs with the same name in different folders
vcsim's special extraConfig keys "SET.<property>" set read-only properties (guest info). vcsim does not
keep secure boot (bootOptions.efiSecureBootEnabled is always unset): "EFI with secure boot on" is covered
by tests/test_filters.py instead."""
import datetime
import json
import os
import sys
import time

from pyVim.connect import SmartConnect
from pyVmomi import vim

port = int(os.environ.get("VMWARE_PORT", "18989"))
for _ in range(60):
    try:
        si = SmartConnect(host=os.environ.get("VMWARE_HOST", "127.0.0.1"), port=port, user="user", pwd="pass",
                          disableSslCertValidation=True)
        break
    except Exception:
        time.sleep(0.5)
else:
    sys.exit("vcsim not reachable")
content = si.RetrieveContent()


def WaitForTask(task):
    """pyVim's WaitForTask trips over vcsim's task names; poll the state instead."""
    for _ in range(200):
        if task.info.state in ("success", "error"):
            if task.info.state == "error":
                raise RuntimeError(task.info.error.msg)
            return
        time.sleep(0.05)
    raise RuntimeError("task timed out")


def vms():
    return content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True).view


def vm(name):
    return [v for v in vms() if v.name == name][0]


def setx(v, **kv):
    WaitForTask(v.ReconfigVM_Task(vim.vm.ConfigSpec(
        extraConfig=[vim.option.OptionValue(key="SET." + k.replace("__", "."), value=val) for k, val in kv.items()])))


def setup():
    aap = vm("DC0_H0_VM0")
    setx(aap, guest__hostName="aap01.example.mil", guest__ipAddress="10.0.0.5", guest__toolsRunningStatus="guestToolsRunning")
    WaitForTask(aap.CreateSnapshot_Task(name="aap-snap", description="", memory=False, quiesce=False))
    web = vm("DC0_H0_VM1")
    setx(web, guest__hostName="web01.example.mil", guest__ipAddress="10.0.0.20", guest__toolsRunningStatus="guestToolsRunning")
    net = [n for n in content.viewManager.CreateContainerView(content.rootFolder, [vim.Network], True).view if n.name == "VM Network"][0]
    nic = vim.vm.device.VirtualVmxnet3(key=-1, backing=vim.vm.device.VirtualEthernetCard.NetworkBackingInfo(network=net, deviceName=net.name))
    WaitForTask(web.ReconfigVM_Task(vim.vm.ConfigSpec(
        firmware="efi", deviceChange=[vim.vm.device.VirtualDeviceSpec(operation="add", device=nic)])))
    WaitForTask(web.CreateSnapshot_Task(name="baseline", description="test fixture", memory=False, quiesce=False))
    host = [h for h in content.viewManager.CreateContainerView(content.rootFolder, [vim.HostSystem], True).view if h.name == "DC0_H0"][0]
    host.configManager.networkSystem.AddPortGroup(vim.host.PortGroup.Specification(
        name="VLAN200", vlanId=200, vswitchName="vSwitch0", policy=vim.host.NetworkPolicy()))
    nt = vm("DC0_C0_RP0_VM0")
    setx(nt, guest__toolsRunningStatus="guestToolsNotRunning")
    WaitForTask(nt.ReconfigVM_Task(vim.vm.ConfigSpec(firmware="efi", bootOptions=vim.vm.BootOptions(efiSecureBootEnabled=False))))
    bios = vm("DC0_C0_RP0_VM1")
    setx(bios, guest__toolsRunningStatus="guestToolsRunning")
    WaitForTask(bios.ReconfigVM_Task(vim.vm.ConfigSpec(firmware="bios")))
    WaitForTask(bios.CreateSnapshot_Task(name="baseline", description="test fixture", memory=False, quiesce=False))
    # vcsim logs no task events: post the one vCenter writes for a snapshot, as the user who took it
    # (vcsim records the session's user, as vCenter does)
    as_jdoe = SmartConnect(host=os.environ.get("VMWARE_HOST", "127.0.0.1"), port=port, user="CORP\\jdoe", pwd="pass",
                           disableSslCertValidation=True).RetrieveContent()
    bios_j = [v for v in as_jdoe.viewManager.CreateContainerView(as_jdoe.rootFolder, [vim.VirtualMachine], True).view
              if v.name == "DC0_C0_RP0_VM1"][0]
    now = datetime.datetime.now(datetime.timezone.utc)
    info = vim.TaskInfo(key="task-fixture", task=vim.Task("task-fixture"), descriptionId="VirtualMachine.createSnapshot",
                        entityName=bios_j.name, state="success", cancelled=False, cancelable=False, queueTime=now,
                        reason=vim.TaskReasonUser(userName="CORP\\jdoe"))
    as_jdoe.eventManager.PostEvent(vim.event.TaskEvent(key=0, chainId=0, createdTime=now, userName="CORP\\jdoe", info=info,
                                                       vm=vim.event.VmEventArgument(name=bios_j.name, vm=bios_j)))
    dc = content.rootFolder.childEntity[0]
    sub = dc.vmFolder.CreateFolder("other")
    for folder in (dc.vmFolder, sub):
        WaitForTask(bios.CloneVM_Task(folder=folder, name="dup", spec=vim.vm.CloneSpec(location=vim.vm.RelocateSpec(), powerOn=False)))


def snaps(tree):
    out = []
    for s in tree or []:
        out.append({"name": s.name, "description": s.description, "id": s.id})
        out += snaps(s.childSnapshotList)
    return out


def _snapshot_tree(v):
    try:
        return v.snapshot.rootSnapshotList if v.snapshot else []
    except AttributeError:      # no snapshot at all
        return []


def state():
    out = {}
    for v in vms():
        nics = []
        for d in v.config.hardware.device:
            if isinstance(d, vim.vm.device.VirtualEthernetCard):
                b = d.backing
                net = b.port.portgroupKey if isinstance(b, vim.vm.device.VirtualEthernetCard.DistributedVirtualPortBackingInfo) else b.deviceName
                nics.append({"label": d.deviceInfo.label, "network": net, "type": type(d).__name__})
        pgs = {p.key: p.name for p in content.viewManager.CreateContainerView(content.rootFolder, [vim.dvs.DistributedVirtualPortgroup], True).view}
        for n in nics:
            n["network"] = pgs.get(n["network"], n["network"])
        out.setdefault(v.name, []).append({"moid": v._moId, "power": v.runtime.powerState, "annotation": v.config.annotation or "",
                                           "devices": len(v.config.hardware.device), "nics": nics,
                                           "snapshots": snaps(_snapshot_tree(v))})
    print(json.dumps(out, sort_keys=True))


{"setup": setup, "state": state}[sys.argv[1]]()
