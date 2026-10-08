#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Read every VM snapshot from vCenter (read-only): name, created, age, size, description, where the
VM is. pyVmomi only (the vmware.vmware collection needs it anyway)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vmware_snapshots
short_description: Read the snapshots of every VM in vCenter (read-only)
description:
  - Lists every snapshot of every VM (or the VMs of one datacenter): VM, folder, snapshot name,
    description, when it was taken and by whom, its age in days, and the disk space it holds.
  - The size of a snapshot is the space it keeps on the datastore - its memory and state files
    plus the delta disks written after it was taken (as the vSphere client shows it).
  - vCenter's address and account come from VMWARE_HOST, VMWARE_USER, VMWARE_PASSWORD (and
    VMWARE_PORT), as a "VMware vCenter" credential in AAP gives them; vcenters adds more vCenters
    (the same account). Changes nothing.
options:
  datacenter: {description: Only this datacenter's VMs., type: str}
  vcenters: {description: More vCenters to read besides the credential's (same account; name or name:port)., type: list, elements: str, default: []}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
  now: {description: The time to measure ages from (seconds since 1970; tests only)., type: float}
  taken_by:
    description: Find who took each snapshot in vCenter's events (one query per VM with snapshots). Empty when
      vCenter no longer has the event (it keeps them for a limited time, often 30 days).
    type: bool
    default: true
author: site automation
'''

RETURN = r'''
snapshots:
  description: One entry per snapshot.
  type: list
  returned: success
  sample: [{vm: web01, moid: vm-42, vcenter: vc01.example.mil, datacenter: DC1, folder: /DC1/vm/Linux, hostname: web01.example.mil, ip: 10.1.2.3,
            power: poweredOn, name: before-patch,
            id: 7, description: "CHG123", taken_by: "CORP\\jdoe", created: "2026-10-01T12:00:00Z", age_days: 4.8, size_gb: 12.4,
            quiesced: false, memory: false, current: true, depth: 0}]
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

GB = 1024.0 ** 3


def snapshot_sizes(files, layouts, disks, children, current):
    """Bytes each snapshot holds. Pure: no vCenter objects, so it can be tested on its own.

    files:    {file key: size in bytes}                         (VM layoutEx.file)
    layouts:  {snapshot: {"data": key, "memory": key,           (VM layoutEx.snapshot)
                          "disks": {disk key: [[file keys] per chain unit]}}}
    disks:    {disk key: [[file keys] per chain unit]}           (VM layoutEx.disk: the running state)
    children: {snapshot: [child snapshots]}                      (the snapshot tree)
    current:  the snapshot the VM runs on, or None

    A snapshot holds its own files (state .vmsn, memory .vmem) and the delta disk written after it
    was taken: the chain unit right after its own chain, in the next state - the child on the way
    to the running state, or the running state itself."""
    def on_path(s):     # is s the current snapshot or one of its ancestors?
        return s == current or any(on_path(c) for c in children.get(s, []))

    out = {}
    for s, lay in layouts.items():
        size = sum(files.get(k, 0) for k in (lay.get("data"), lay.get("memory")) if k is not None and k >= 0)
        kids = children.get(s, [])
        nxt = None
        if s == current:
            nxt = disks
        else:
            path = [c for c in kids if on_path(c)]
            pick = path[0] if path else (kids[0] if kids else None)
            if pick is not None and pick in layouts:
                nxt = layouts[pick].get("disks", {})
        for dk, chain in (lay.get("disks") or {}).items():
            after = (nxt or {}).get(dk) or []
            if len(after) > len(chain):
                size += sum(files.get(k, 0) for k in after[len(chain)])
        out[s] = size
    return out


def snapshot_creators(snaps, events, before_s=1800, after_s=120):
    """Who took each snapshot. Pure: no vCenter objects, so it can be tested on its own.

    snaps:  [(snapshot key, taken at: seconds since 1970)]
    events: [(seconds since 1970, user)] - vCenter's "create snapshot" task events for that VM
    A snapshot gets the user of the closest event from up to before_s seconds before it to after_s
    seconds after it (the task is queued a moment before the snapshot exists); each event is used
    once. No event (vCenter keeps them for a limited time, often 30 days) -> ''."""
    out, used = {}, set()
    for key, t in sorted(snaps, key=lambda x: x[1]):
        best = None
        for i, (et, user) in enumerate(events):
            d = t - et
            if i not in used and -after_s <= d <= before_s and (best is None or abs(d) < abs(best[0])):
                best = (d, user, i)
        if best is not None:
            used.add(best[2])
        out[key] = best[1] if best else ""
    return out


def _creators(content, vm, nodes):
    """{snapshot key: user} from vCenter's task events for this VM ('' when not found)."""
    times = [(n.snapshot._moId, n.createTime.timestamp()) for n, _ in nodes]
    try:
        begin = datetime.datetime.fromtimestamp(min(t for _, t in times) - 3600, tz=datetime.timezone.utc)
        spec = vim.event.EventFilterSpec(entity=vim.event.EventFilterSpec.ByEntity(entity=vm, recursion="self"),
                                         eventTypeId=["TaskEvent"], time=vim.event.EventFilterSpec.ByTime(beginTime=begin))
        events = [(e.createdTime.timestamp(), e.userName or "") for e in content.eventManager.QueryEvents(spec) or []
                  if str(getattr(e.info, "descriptionId", "")).startswith("VirtualMachine.createSnapshot")]
    except Exception:
        events = []
    return snapshot_creators(times, events)


def _folder(obj):
    """('/DC1/vm/Linux', 'DC1') for a VM, from its parents."""
    names, x = [], getattr(obj, "parent", None)
    while x is not None and not isinstance(x, vim.Datacenter):
        names.append(x.name)
        x = getattr(x, "parent", None)
    dc = x.name if x is not None else ""
    return "/" + "/".join([dc] + list(reversed(names))) if dc else "/" + "/".join(reversed(names)), dc


def _walk(nodes, parent, depth, out, children):
    for n in nodes or []:
        key = n.snapshot._moId
        out.append((n, depth))
        children.setdefault(parent, []).append(key)
        children.setdefault(key, [])
        _walk(n.childSnapshotList, key, depth + 1, out, children)


def _read(content, p, now, vcenter):
    out = []
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
    spec = vmodl.query.PropertyCollector.FilterSpec(
        objectSet=[vmodl.query.PropertyCollector.ObjectSpec(
            obj=view, skip=True,
            selectSet=[vmodl.query.PropertyCollector.TraversalSpec(name="v", path="view", skip=False, type=vim.view.ContainerView)])],
        propSet=[vmodl.query.PropertyCollector.PropertySpec(type=vim.VirtualMachine,
                                                             pathSet=["name", "snapshot", "runtime.powerState",
                                                                      "guest.hostName", "guest.ipAddress"])])
    for o in content.propertyCollector.RetrieveContents([spec]):
        pr = {x.name: x.val for x in o.propSet}
        info = pr.get("snapshot")
        if not info or not info.rootSnapshotList:
            continue
        vm = o.obj
        folder, dc = _folder(vm)
        if p["datacenter"] and dc != p["datacenter"]:
            continue
        nodes, children = [], {}
        _walk(info.rootSnapshotList, None, 0, nodes, children)
        current = info.currentSnapshot._moId if info.currentSnapshot is not None else None
        sizes, layouts = {}, {}
        try:
            le = vm.layoutEx
            files = {f.key: int(f.size or 0) for f in (le.file or [])}
            layouts = {s.key._moId: {"data": s.dataKey, "memory": getattr(s, "memoryKey", -1),
                                     "disks": {d.key: [list(u.fileKey) for u in d.chain] for d in (s.disk or [])}}
                       for s in (le.snapshot or [])}
            running = {d.key: [list(u.fileKey) for u in d.chain] for d in (le.disk or [])}
            sizes = snapshot_sizes(files, layouts, running, children, current)
        except Exception:
            sizes = {}          # no layout (an inaccessible VM): sizes unknown, the rest is still right
        who = _creators(content, vm, nodes) if p["taken_by"] else {}
        for n, depth in nodes:
            key = n.snapshot._moId
            created = n.createTime
            if created.tzinfo is None:
                created = created.replace(tzinfo=datetime.timezone.utc)
            age = (now - created.timestamp()) / 86400.0
            out.append({"vm": pr.get("name", ""), "moid": vm._moId, "vcenter": vcenter, "datacenter": dc, "folder": folder,
                        "hostname": pr.get("guest.hostName") or "", "ip": pr.get("guest.ipAddress") or "",
                        "power": str(pr.get("runtime.powerState") or ""), "name": n.name, "id": int(n.id),
                        "description": n.description or "", "taken_by": who.get(key, ""),
                        "created": created.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "age_days": round(max(age, 0.0), 1),
                        "size_gb": round(sizes[key] / GB, 2) if key in sizes else None,
                        "quiesced": bool(n.quiesced),
                        "memory": (layouts.get(key, {}).get("memory") if layouts.get(key, {}).get("memory") is not None else -1) >= 0,
                        "current": key == current, "depth": depth})
    view.Destroy()
    return out


def main():
    module = AnsibleModule(
        argument_spec=dict(datacenter=dict(type="str"), validate_certs=dict(type="bool", default=True),
                           vcenters=dict(type="list", elements="str", default=[]),
                           now=dict(type="float"), taken_by=dict(type="bool", default=True)),
        supports_check_mode=True,
    )
    if not HAS_PYVMOMI:
        module.fail_json(msg="pyVmomi is not installed in the execution environment (the vmware.vmware collection needs it too)")
    p = module.params
    now = p["now"] or time.time()
    done, statuses = each_vcenter(module, lambda si, content, name: _read(content, p, now, name), p["vcenters"],
                                  p["validate_certs"], "the snapshots")
    module.exit_json(changed=False, snapshots=[x for _, part in done for x in part], vcenters=statuses)


if __name__ == "__main__":
    main()
