#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Read every VM of every vCenter (read-only): name, ID, where it is, power state, VMware Tools and its
heartbeat - for the pick lists (vm_pick_refresh.yml: the VMs that may be restarted, or only the ones that
look hung) and the restart of picked VMs (each pick checked again, live). pyVmomi only (the vmware.vmware
collection needs it anyway)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vmware_vms
short_description: Read every VM in vCenter, with VMware Tools and its heartbeat (read-only)
description:
  - Lists every VM (templates included, marked) of the credential's vCenter and of vcenters - name, ID
    (moid), vCenter, datacenter, folder, cluster, ESXi host, power state, guest host name and IP
    addresses, guest OS, VMware Tools (installed, running) and the guest heartbeat vCenter shows
    (green, yellow, red; gray = no VMware Tools to tell), its vCPUs and memory, and when it was powered on.
  - vCenter's address and account come from VMWARE_HOST, VMWARE_USER, VMWARE_PASSWORD (and
    VMWARE_PORT), as a "VMware vCenter" credential in AAP gives them; vcenters adds more vCenters
    (the same account). Changes nothing.
options:
  datacenter: {description: Only this datacenter's VMs., type: str}
  vcenters: {description: More vCenters to read besides the credential's (same account; name or name:port)., type: list, elements: str, default: []}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
  now: {description: The time to measure the time up from (seconds since 1970; tests only)., type: float}
author: site automation
'''

RETURN = r'''
vms:
  description: One entry per VM.
  type: list
  returned: success
  sample: [{name: web01, moid: vm-42, vcenter: vc01.example.mil, datacenter: DC1, folder: /DC1/vm/Linux, cluster: PROD-CL01,
            esxi_host: esx01.example.mil, template: false, power: poweredOn, hostname: web01.example.mil, ip: 192.0.2.3,
            ips: [192.0.2.3], guest_os: Red Hat Enterprise Linux 9 (64-bit), tools: toolsOk, tools_running: guestToolsRunning,
            heartbeat: green, cpus: 2, memory_mb: 8192, boot_time: "2026-10-01T12:00:00Z", up_minutes: 27360}]
vcenters:
  description: Each vCenter and whether it was read (ok), or why not (error).
  type: list
  returned: success
'''

import datetime
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_vcenters import each_vcenter

try:
    from pyVmomi import vim, vmodl
    HAS_PYVMOMI = True
except ImportError:
    HAS_PYVMOMI = False

PROPS = ["name", "config.template", "config.guestFullName", "runtime.powerState", "runtime.host", "runtime.bootTime",
         "guest.hostName", "guest.ipAddress", "guest.net", "guest.toolsStatus", "guest.toolsRunningStatus", "guestHeartbeatStatus",
         "config.hardware.numCPU", "config.hardware.memoryMB"]


def _folder(obj):
    """('/DC1/vm/Linux', 'DC1') for a VM, from its parents."""
    names, x = [], getattr(obj, "parent", None)
    while x is not None and not isinstance(x, vim.Datacenter):
        names.append(x.name)
        x = getattr(x, "parent", None)
    dc = x.name if x is not None else ""
    return "/" + "/".join([dc] + list(reversed(names))) if dc else "/" + "/".join(reversed(names)), dc


def _host(host, seen):
    """(ESXi host name, its cluster's name - '' for a standalone host), each host read once."""
    if host is None:
        return "", ""
    if host._moId not in seen:
        try:
            parent = host.parent
            seen[host._moId] = (host.name, parent.name if isinstance(parent, vim.ClusterComputeResource) else "")
        except Exception:
            seen[host._moId] = ("", "")
    return seen[host._moId]


def _read(content, p, now, vcenter):
    out, hosts, folders = [], {}, {}
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
    spec = vmodl.query.PropertyCollector.FilterSpec(
        objectSet=[vmodl.query.PropertyCollector.ObjectSpec(
            obj=view, skip=True,
            selectSet=[vmodl.query.PropertyCollector.TraversalSpec(name="v", path="view", skip=False, type=vim.view.ContainerView)])],
        propSet=[vmodl.query.PropertyCollector.PropertySpec(type=vim.VirtualMachine, pathSet=PROPS)])
    for o in content.propertyCollector.RetrieveContents([spec]):
        pr = {x.name: x.val for x in o.propSet}         # a property vCenter does not have for this VM is left out
        vm = o.obj
        parent = getattr(vm, "parent", None)
        key = parent._moId if parent is not None else ""
        if key not in folders:
            folders[key] = _folder(vm)
        folder, dc = folders[key]
        if p["datacenter"] and dc != p["datacenter"]:
            continue
        esxi, cluster = _host(pr.get("runtime.host"), hosts)
        boot = pr.get("runtime.bootTime")
        if boot is not None and boot.tzinfo is None:
            boot = boot.replace(tzinfo=datetime.timezone.utc)
        ips = [pr.get("guest.ipAddress")] + [ip for n in pr.get("guest.net") or [] for ip in (getattr(n, "ipAddress", None) or [])]
        out.append({"name": pr.get("name", ""), "moid": vm._moId, "vcenter": vcenter, "datacenter": dc, "folder": folder,
                    "cluster": cluster, "esxi_host": esxi, "template": bool(pr.get("config.template")),
                    "power": str(pr.get("runtime.powerState") or ""), "hostname": pr.get("guest.hostName") or "",
                    "ip": pr.get("guest.ipAddress") or "", "ips": sorted(set(str(i) for i in ips if i)),
                    "guest_os": pr.get("config.guestFullName") or "", "tools": str(pr.get("guest.toolsStatus") or ""),
                    "tools_running": str(pr.get("guest.toolsRunningStatus") or ""), "heartbeat": str(pr.get("guestHeartbeatStatus") or ""),
                    "cpus": pr.get("config.hardware.numCPU"), "memory_mb": pr.get("config.hardware.memoryMB"),
                    "boot_time": boot.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if boot else "",
                    "up_minutes": int(max(now - boot.timestamp(), 0) // 60) if boot else None})
    view.Destroy()
    return out


def main():
    module = AnsibleModule(
        argument_spec=dict(datacenter=dict(type="str"), validate_certs=dict(type="bool", default=True),
                           vcenters=dict(type="list", elements="str", default=[]), now=dict(type="float")),
        supports_check_mode=True,
    )
    if not HAS_PYVMOMI:
        module.fail_json(msg="pyVmomi is not installed in the execution environment (the vmware.vmware collection needs it too)")
    p = module.params
    now = p["now"] or time.time()
    done, statuses = each_vcenter(module, lambda si, content, name: _read(content, p, now, name), p["vcenters"],
                                  p["validate_certs"], "the VMs")
    module.exit_json(changed=False, vms=[x for _, part in done for x in part], vcenters=statuses)


if __name__ == "__main__":
    main()
