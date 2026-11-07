#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Read one VM - a Horizon instant-clone golden image - in every vCenter of the job (read-only), for the golden image
check (playbooks/horizon_image_check.yml): its virtual devices, hardware version, snapshots, power state and VMware
Tools. The decisions are made in plugins/filter/horizon_image_filters.py. pyVmomi only (the vmware.vmware collection
needs it anyway)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vmware_horizon_image
short_description: Read a Horizon golden image VM's devices, hardware, snapshots and VMware Tools (read-only)
description:
  - Looks for the VM named name in the credential's vCenter and in vcenters (every VM of that name is returned, so a
    name found twice is seen). For each - vCenter, datacenter, folder, ESXi host, template or not, power state,
    hardware version, firmware and secure boot, guest OS, vCPUs, memory, its disks, network adapters (type, connected),
    CD/DVD drives (what they are connected to), floppy drives, serial and parallel ports, a vTPM, the snapshots (how
    many, the current one and how deep its chain is) and VMware Tools (running, version, status).
  - vCenter's address and account come from VMWARE_HOST, VMWARE_USER, VMWARE_PASSWORD (and VMWARE_PORT), as a
    "VMware vCenter" credential in AAP gives them; vcenters adds more vCenters (the same account). Changes nothing.
options:
  name: {description: The VM's name as vCenter shows it - or its path (/DC1/vm/Gold/W11-Gold)., type: str, required: true}
  datacenter: {description: Only this datacenter's VMs., type: str}
  vcenters: {description: More vCenters to read besides the credential's (same account; name or name:port)., type: list, elements: str, default: []}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
author: site automation
'''

RETURN = r'''
vms:
  description: Every VM of that name (none, one, or more - the report says a name found twice is a problem).
  type: list
  returned: success
  sample: [{name: W11-Gold, moid: vm-42, vcenter: vc01.example.mil, datacenter: DC1, folder: /DC1/vm/Gold, esxi_host: esx01.example.mil,
            template: false, power: poweredOn, hw_version: vmx-19, firmware: efi, secure_boot: true, guest_os: Microsoft Windows 11 (64-bit),
            guest_id: windows11_64Guest, guest_hostname: w11-gold.corp.example.mil, cpus: 2, cores_per_socket: 2, memory_mb: 8192,
            disks: [{label: Hard disk 1, size_gb: 64.0, thin: true, datastore: DS01}],
            nics: [{label: Network adapter 1, type: VirtualVmxnet3, network: VDI-VLAN20, connected: true, start_connected: true}],
            cdroms: [{label: CD/DVD drive 1, backing: client, file: "", connected: false, start_connected: false}],
            floppies: [], serials: [], parallels: [], vtpm: false,
            tools_running: guestToolsRunning, tools_version: "12352", tools_status: guestToolsCurrent, tools: toolsOk,
            snapshots: {count: 2, current: v2026-11, current_depth: 2, max_depth: 2, chain: [v2026-10, v2026-11],
                        current_created: "2026-11-01T12:00:00Z", current_state: poweredOff}}]
    # a CD/DVD drive's backing: iso (an ISO file), host (the ESXi host's drive), client (the vSphere Client's), none;
    # a floppy's: image, host, client; a serial or parallel port's: file, host (a physical port of the host), pipe, network
vcenters:
  description: Each vCenter and whether it was read (ok), or why not (error).
  type: list
  returned: success
'''

import datetime
import re

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_vcenters import each_vcenter

try:
    from pyVmomi import vim, vmodl
    HAS_PYVMOMI = True
except ImportError:
    HAS_PYVMOMI = False

PROPS = ["name", "config.template", "config.version", "config.firmware", "config.bootOptions", "config.guestFullName",
         "config.guestId", "config.hardware.numCPU", "config.hardware.numCoresPerSocket", "config.hardware.memoryMB",
         "config.hardware.device", "runtime.powerState", "runtime.host", "guest.toolsRunningStatus", "guest.toolsVersion",
         "guest.toolsVersionStatus2", "guest.toolsStatus", "guest.hostName", "snapshot"]
NIC_TYPES = ("VirtualVmxnet3", "VirtualVmxnet3Vrdma", "VirtualVmxnet2", "VirtualVmxnet", "VirtualE1000", "VirtualE1000e",
             "VirtualPCNet32", "VirtualSriovEthernetCard", "VirtualEthernetCard")
# the backing of a drive or port, by its type's last name -> what the report calls it
CD_BACKING = {"IsoBackingInfo": "iso", "AtapiBackingInfo": "host", "PassthroughBackingInfo": "host",
              "RemoteAtapiBackingInfo": "client", "RemotePassthroughBackingInfo": "client"}
FLOPPY_BACKING = {"ImageBackingInfo": "image", "DeviceBackingInfo": "host", "RemoteDeviceBackingInfo": "client"}
PORT_BACKING = {"FileBackingInfo": "file", "DeviceBackingInfo": "host", "PipeBackingInfo": "pipe", "URIBackingInfo": "network"}


def _s(x):
    return "" if x is None else str(x).strip()


def kind(obj):
    """The last part of an object's type name: vim.vm.device.VirtualVmxnet3 -> VirtualVmxnet3 (no pyVmomi class needed,
    so an older pyVmomi without VirtualTPM still reads the rest)."""
    return type(obj).__name__.split(".")[-1]


def _label(d):
    return _s(getattr(getattr(d, "deviceInfo", None), "label", "")) or kind(d)


def _conn(d):
    c = getattr(d, "connectable", None)
    return bool(getattr(c, "connected", False)), bool(getattr(c, "startConnected", False))


def devices(devs):
    """The VM's virtual devices that the checks look at -> {disks, nics, cdroms, floppies, serials, parallels, vtpm}."""
    out = {"disks": [], "nics": [], "cdroms": [], "floppies": [], "serials": [], "parallels": [], "vtpm": False}
    for d in devs or []:
        k, b = kind(d), getattr(d, "backing", None)
        bk = kind(b) if b is not None else ""
        if k == "VirtualDisk":
            f = _s(getattr(b, "fileName", ""))
            m = re.match(r"^\[([^\]]*)\]", f)
            out["disks"].append({"label": _label(d), "size_gb": round(float(getattr(d, "capacityInKB", 0) or 0) / 1024 / 1024, 1),
                                 "thin": bool(getattr(b, "thinProvisioned", False)), "datastore": m.group(1) if m else ""})
        elif k in NIC_TYPES or (hasattr(d, "macAddress") and hasattr(d, "addressType")):
            net = _s(getattr(b, "deviceName", "")) or _s(getattr(getattr(b, "port", None), "portgroupKey", "")) \
                or _s(getattr(b, "opaqueNetworkId", ""))
            on, start = _conn(d)
            out["nics"].append({"label": _label(d), "type": k, "network": net, "connected": on, "start_connected": start})
        elif k == "VirtualCdrom":
            on, start = _conn(d)
            out["cdroms"].append({"label": _label(d), "backing": CD_BACKING.get(bk, "none" if not bk else bk),
                                  "file": _s(getattr(b, "fileName", "")) or _s(getattr(b, "deviceName", "")),
                                  "connected": on, "start_connected": start})
        elif k in ("VirtualFloppy", "VirtualSerialPort", "VirtualParallelPort"):
            on, start = _conn(d)
            table = FLOPPY_BACKING if k == "VirtualFloppy" else PORT_BACKING
            key = {"VirtualFloppy": "floppies", "VirtualSerialPort": "serials", "VirtualParallelPort": "parallels"}[k]
            out[key].append({"label": _label(d), "backing": table.get(bk, "none" if not bk else bk),
                             "connected": on, "start_connected": start})
        elif k == "VirtualTPM":
            out["vtpm"] = True
    return out


def _iso(t):
    if t is None:
        return ""
    try:
        if t.tzinfo is None:
            t = t.replace(tzinfo=datetime.timezone.utc)
        return t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:                           # noqa: BLE001
        return _s(t)


def _moid(x):
    return getattr(x, "_moId", None) or _s(x)


def snapshots(info):
    """A VM's snapshot property -> {count, current, current_depth, max_depth, chain (root to the current one),
    current_created, current_state}. A VM with none: count 0 (vCenter leaves the property out, or gives it empty)."""
    out = {"count": 0, "current": "", "current_depth": 0, "max_depth": 0, "chain": [], "current_created": "", "current_state": ""}
    if info is None:
        return out
    cur = _moid(getattr(info, "currentSnapshot", None)) if getattr(info, "currentSnapshot", None) is not None else None

    def walk(nodes, path):
        for n in nodes or []:
            here = path + [_s(getattr(n, "name", ""))]
            out["count"] += 1
            out["max_depth"] = max(out["max_depth"], len(here))
            if cur is not None and _moid(getattr(n, "snapshot", None)) == cur:
                out.update(current=here[-1], current_depth=len(here), chain=here,
                           current_created=_iso(getattr(n, "createTime", None)), current_state=_s(getattr(n, "state", "")))
            walk(getattr(n, "childSnapshotList", None), here)
    walk(getattr(info, "rootSnapshotList", None), [])
    return out


def _folder(obj):
    """('/DC1/vm/Gold', 'DC1') for a VM, from its parents."""
    names, x = [], getattr(obj, "parent", None)
    while x is not None and not isinstance(x, vim.Datacenter):
        names.append(x.name)
        x = getattr(x, "parent", None)
    dc = x.name if x is not None else ""
    return "/" + "/".join([dc] + list(reversed(names))) if dc else "/" + "/".join(reversed(names)), dc


def matches(name, vm_name, folder):
    """Is this the VM asked for? A name with a / is a path (/DC1/vm/Gold/W11-Gold), else the VM's name."""
    want = _s(name)
    if "/" in want:
        return (folder.rstrip("/") + "/" + vm_name).lower() == ("/" + want.strip("/")).lower()
    return vm_name == want


def vm_record(pr, name, moid, vcenter, folder, dc):
    """One VM's properties (as the property collector gave them) -> the record the filters read."""
    boot = pr.get("config.bootOptions")
    host = pr.get("runtime.host")
    rec = {"name": name, "moid": moid, "vcenter": vcenter, "datacenter": dc, "folder": folder,
           "esxi_host": _s(getattr(host, "name", "")) if host is not None else "",
           "template": bool(pr.get("config.template")), "power": _s(pr.get("runtime.powerState")),
           "hw_version": _s(pr.get("config.version")), "firmware": _s(pr.get("config.firmware")),
           "secure_boot": bool(getattr(boot, "efiSecureBootEnabled", False)) if boot is not None else False,
           "guest_os": _s(pr.get("config.guestFullName")), "guest_id": _s(pr.get("config.guestId")),
           "guest_hostname": _s(pr.get("guest.hostName")),
           "cpus": int(pr.get("config.hardware.numCPU") or 0), "cores_per_socket": int(pr.get("config.hardware.numCoresPerSocket") or 0),
           "memory_mb": int(pr.get("config.hardware.memoryMB") or 0),
           "tools_running": _s(pr.get("guest.toolsRunningStatus")), "tools_version": _s(pr.get("guest.toolsVersion")),
           "tools_status": _s(pr.get("guest.toolsVersionStatus2")), "tools": _s(pr.get("guest.toolsStatus")),
           "snapshots": snapshots(pr.get("snapshot"))}
    rec.update(devices(pr.get("config.hardware.device")))
    return rec


def _collect(content, objs, props):
    """{object: {property: value}} for the objects (a container view when objs is a view)."""
    PC = vmodl.query.PropertyCollector
    if isinstance(objs, vim.view.ContainerView):
        os_ = [PC.ObjectSpec(obj=objs, skip=True,
                             selectSet=[PC.TraversalSpec(name="v", path="view", skip=False, type=vim.view.ContainerView)])]
    else:
        os_ = [PC.ObjectSpec(obj=o, skip=False) for o in objs]
    if not os_:
        return []
    spec = PC.FilterSpec(objectSet=os_, propSet=[PC.PropertySpec(type=vim.VirtualMachine, pathSet=props)])
    return [(o.obj, {x.name: x.val for x in o.propSet}) for o in content.propertyCollector.RetrieveContents([spec])]


def _read(content, p, vcenter):
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
    try:
        names = _collect(content, view, ["name"])
    finally:
        view.Destroy()
    want = _s(p["name"])
    short = want.rsplit("/", 1)[-1]
    found, folders = [], {}
    for vm, pr in names:
        vm_name = _s(pr.get("name"))
        if vm_name != short and not ("/" not in want and vm_name.lower() == want.lower()):
            continue
        parent = getattr(vm, "parent", None)
        key = parent._moId if parent is not None else ""
        if key not in folders:
            folders[key] = _folder(vm)
        folder, dc = folders[key]
        if p["datacenter"] and dc != p["datacenter"]:
            continue
        found.append((vm, vm_name, folder, dc))
    exact = [x for x in found if matches(want, x[1], x[2])]
    # the exact name first; the same name in another case only when none has it exactly
    use = exact or [x for x in found if "/" not in want]
    full = dict((vm._moId, pr) for vm, pr in _collect(content, [x[0] for x in use], PROPS))
    return [vm_record(full.get(vm._moId, {}), vm_name, vm._moId, vcenter, folder, dc) for vm, vm_name, folder, dc in use]


def main():
    module = AnsibleModule(
        argument_spec=dict(name=dict(type="str", required=True), datacenter=dict(type="str"),
                           validate_certs=dict(type="bool", default=True), vcenters=dict(type="list", elements="str", default=[])),
        supports_check_mode=True,
    )
    if not HAS_PYVMOMI:
        module.fail_json(msg="pyVmomi is not installed in the execution environment (the vmware.vmware collection needs it too)")
    p = module.params
    if not _s(p["name"]):
        module.fail_json(msg="No VM name: set horizon_image_vm to the golden image VM's name as vCenter shows it")
    done, statuses = each_vcenter(module, lambda si, content, name: _read(content, p, name), p["vcenters"],
                                  p["validate_certs"], "the golden image VM")
    module.exit_json(changed=False, vms=[v for _, vs in done for v in vs], vcenters=statuses)


if __name__ == "__main__":
    main()
