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
  disks:
    description: Also read each VM's virtual disks (size, datastore, thin), the guest's drives as VMware Tools reports
      them (size and free space), and the datastore space it uses (vdisks, volumes, storage_used_gb,
      storage_provisioned_gb). For VM - ask a question.
    type: bool
    default: false
  events_days:
    description: Also read vCenter's events of the last this many days - one query for every VM - for each VM's last
      restart (power on, guest restart, reset, HA), last power off and last vMotion (last_restart, last_power_off,
      last_vmotion). 0 = none. vCenter keeps events for a limited time (often 30 days). For VM - ask a question.
    type: float
    default: 0
  max_events:
    description: Read at most this many of those events (the newest).
    type: int
    default: 50000
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
            heartbeat: green, cpus: 2, memory_mb: 8192, hw_version: vmx-21, firmware: efi, tools_version: "12352", boot_time: "2026-10-01T12:00:00Z", up_minutes: 27360}]
    # with disks: vdisks [{label: Hard disk 1, size_gb: 100.0, datastore: DS01, file: "[DS01] web01/web01.vmdk", thin: true}],
    #   volumes [{path: "C:\\", capacity_gb: 99.4, free_gb: 12.1}], storage_used_gb: 54.2, storage_provisioned_gb: 108.0
    # with events_days: last_restart {time, kind (power on, guest restart, reset, HA restart), by}, last_power_off {time, kind, by},
    #   last_vmotion {time, kind (vMotion, DRS vMotion, relocate), from_host, to_host, by} - {} when none in the window
events:
  description: With events_days, one per vCenter read - its name, the days read, how many events, and whether
    max_events cut it short (capped).
  type: list
  returned: success
vcenters:
  description: Each vCenter and whether it was read (ok), or why not (error).
  type: list
  returned: success
'''

import datetime
import re
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
         "config.hardware.numCPU", "config.hardware.memoryMB", "config.version", "config.firmware", "guest.toolsVersion"]


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


GB = 1024.0 ** 3
# the events of a VM's last restart, power off and vMotion: {type: (what, kind)}
EVENTS = {"VmPoweredOnEvent": ("restart", "power on"), "VmGuestRebootEvent": ("restart", "guest restart"),
          "VmResettingEvent": ("restart", "reset"), "VmRestartedOnAlternateHostEvent": ("restart", "HA restart"),
          "com.vmware.vc.ha.VmRestartedByHAEvent": ("restart", "HA restart"),
          "VmPoweredOffEvent": ("power_off", "power off"), "VmGuestShutdownEvent": ("power_off", "guest shutdown"),
          "VmMigratedEvent": ("vmotion", "vMotion"), "DrsVmMigratedEvent": ("vmotion", "DRS vMotion"),
          "VmRelocatedEvent": ("vmotion", "relocate")}


def _iso(t):
    if t is None:
        return ""
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _disks(pr):
    """(virtual disks, the guest's drives, datastore space used and provisioned in GB) from the VM's properties."""
    vd = []
    for d in pr.get("config.hardware.device") or []:
        if not isinstance(d, vim.vm.device.VirtualDisk):
            continue
        f = str(getattr(d.backing, "fileName", "") or "")
        m = re.match(r"^\[([^\]]*)\]", f)
        vd.append({"label": getattr(d.deviceInfo, "label", "") or "", "size_gb": round(float(d.capacityInKB or 0) / 1024 / 1024, 1),
                   "datastore": m.group(1) if m else "", "file": f, "thin": bool(getattr(d.backing, "thinProvisioned", False))})
    vols = [{"path": str(g.diskPath or ""), "capacity_gb": round(float(g.capacity or 0) / GB, 1),
             "free_gb": round(float(g.freeSpace or 0) / GB, 1)} for g in pr.get("guest.disk") or []]
    st = pr.get("summary.storage")
    used = float(getattr(st, "committed", 0) or 0) if st is not None else None
    unc = float(getattr(st, "uncommitted", 0) or 0) if st is not None else 0
    return vd, vols, (round(used / GB, 1) if used is not None else None), (round((used + unc) / GB, 1) if used is not None else None)


def _vm_events(content, p, now):
    """{vm moid: {'restart': {...}, 'power_off': {...}, 'vmotion': {...}}} - the newest of each, from one query of the
    last events_days days - and what was read."""
    begin = datetime.datetime.fromtimestamp(now - p["events_days"] * 86400, tz=datetime.timezone.utc)
    spec = vim.event.EventFilterSpec(time=vim.event.EventFilterSpec.ByTime(beginTime=begin), eventTypeId=list(EVENTS))
    seen, last, n = set(), {}, 0
    col = content.eventManager.CreateCollectorForEvents(spec)
    try:
        col.SetCollectorPageSize(1000)
        batch = list(col.latestPage or [])
        while batch and n < p["max_events"]:
            fresh = [e for e in batch if e.key not in seen]
            if not fresh:
                break
            for e in fresh:
                seen.add(e.key)
                n += 1
                t = getattr(e, "eventTypeId", "") or type(e).__name__.split(".")[-1]
                what, kind = EVENTS.get(t, ("", ""))
                vm = getattr(getattr(e, "vm", None), "vm", None)
                if not what or vm is None:
                    continue
                rec = {"time": _iso(e.createdTime), "kind": kind, "by": "DRS" if t == "DrsVmMigratedEvent" else str(e.userName or "")}
                if what == "vmotion":
                    rec["from_host"] = str(getattr(getattr(e, "sourceHost", None), "name", "") or "")
                    rec["to_host"] = str(getattr(getattr(e, "host", None), "name", "") or "")
                old = last.setdefault(vm._moId, {}).get(what)
                if old is None or rec["time"] > old["time"]:
                    last[vm._moId][what] = rec
            batch = col.ReadPreviousEvents(1000) or []
    finally:
        try:
            col.DestroyCollector()
        except Exception:
            pass
    return last, {"days": p["events_days"], "read": n, "capped": n >= p["max_events"]}


def _read(content, p, now, vcenter):
    out, hosts, folders = [], {}, {}
    props = PROPS + (["config.hardware.device", "guest.disk", "summary.storage"] if p["disks"] else [])
    evs, ev_info = _vm_events(content, p, now) if p["events_days"] > 0 else ({}, {})
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
    spec = vmodl.query.PropertyCollector.FilterSpec(
        objectSet=[vmodl.query.PropertyCollector.ObjectSpec(
            obj=view, skip=True,
            selectSet=[vmodl.query.PropertyCollector.TraversalSpec(name="v", path="view", skip=False, type=vim.view.ContainerView)])],
        propSet=[vmodl.query.PropertyCollector.PropertySpec(type=vim.VirtualMachine, pathSet=props)])
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
                    "hw_version": str(pr.get("config.version") or ""), "firmware": str(pr.get("config.firmware") or ""),
                    "tools_version": str(pr.get("guest.toolsVersion") or ""),
                    "boot_time": boot.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if boot else "",
                    "up_minutes": int(max(now - boot.timestamp(), 0) // 60) if boot else None})
        if p["disks"]:
            vd, vols, used, prov = _disks(pr)
            out[-1].update({"vdisks": vd, "volumes": vols, "storage_used_gb": used, "storage_provisioned_gb": prov})
        if p["events_days"] > 0:
            e = evs.get(vm._moId, {})
            out[-1].update({"last_restart": e.get("restart", {}), "last_power_off": e.get("power_off", {}), "last_vmotion": e.get("vmotion", {})})
    view.Destroy()
    return out, ev_info


def main():
    module = AnsibleModule(
        argument_spec=dict(datacenter=dict(type="str"), validate_certs=dict(type="bool", default=True),
                           vcenters=dict(type="list", elements="str", default=[]), now=dict(type="float"),
                           disks=dict(type="bool", default=False), events_days=dict(type="float", default=0),
                           max_events=dict(type="int", default=50000)),
        supports_check_mode=True,
    )
    if not HAS_PYVMOMI:
        module.fail_json(msg="pyVmomi is not installed in the execution environment (the vmware.vmware collection needs it too)")
    p = module.params
    now = p["now"] or time.time()
    done, statuses = each_vcenter(module, lambda si, content, name: _read(content, p, now, name), p["vcenters"],
                                  p["validate_certs"], "the VMs")
    module.exit_json(changed=False, vms=[x for _, part in done for x in part[0]], vcenters=statuses,
                     events=[dict(part[1], vcenter=name) for name, part in done if part[1]])


if __name__ == "__main__":
    main()
