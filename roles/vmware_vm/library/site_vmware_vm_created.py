#!/usr/bin/python
# -*- coding: utf-8 -*-
"""The VMs created in a time window - by default the last 7 days - read from vCenter (read-only):
when, by whom, how (cloned, deployed from a template, new), the ticket when AAP built it, where it
is and its size - and the ones created and deleted since. pyVmomi only (the vmware.vmware collection
needs it anyway)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vmware_vm_created
short_description: The VMs created in the last days, from vCenter (read-only)
description:
  - Every VM created in the last C(days) days (or in one datacenter): when (the VM's creation date,
    vSphere 6.7 and later; else its creation event), by whom and how (vCenter's events - cloned from a
    VM, deployed from a template, created new), the ticket and template when the AAP deploy built it
    (its notes: "<ticket> - built <date>" and "Template: ...", or from older releases "Deployed
    from ... for <ticket> by ..."), where it is, its size and power state.
  - VMs created in the window and deleted since are listed too, from vCenter's events (vCenter keeps
    them for a limited time, often 30 days).
  - Templates are not VMs here. vCenter's address and account come from VMWARE_HOST, VMWARE_USER,
    VMWARE_PASSWORD (and VMWARE_PORT); vcenters adds more vCenters (the same account). Changes nothing.
options:
  days: {description: The window - the last this many days up to now., type: float, default: 7}
  datacenter: {description: Only this datacenter's VMs., type: str}
  vcenters: {description: More vCenters to read besides the credential's (same account; name or name:port)., type: list, elements: str, default: []}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
  now: {description: The end of the window (seconds since 1970; tests only)., type: float}
author: site automation
'''

RETURN = r'''
created:
  description: One entry per VM created in the window, oldest first.
  type: list
  returned: success
  sample: [{vm: web01, moid: vm-1042, vcenter: vc01.example.mil, datacenter: DC1, created: "2026-10-14T15:02:11Z", by: "VSPHERE.LOCAL\\svc_aap",
            how: "deployed from template rhel9-gold", ticket: INC0012345, template: rhel9-gold, aap: true, deleted: false, removed: "",
            folder: /DC1/vm/Linux, cluster: CL1, host: esx03.example.mil, guest_os: "Red Hat Enterprise Linux 9 (64-bit)",
            cpu: 4, memory_gb: 16, disk_gb: 150, power: poweredOn, ip: 203.0.113.15}]
since:
  description: The start of the window (UTC).
  type: str
  returned: success
until:
  description: Its end (UTC).
  type: str
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

CREATE = ("VmCreatedEvent", "VmClonedEvent", "VmDeployedEvent")
REMOVE = ("VmRemovedEvent",)
# the notes the AAP deploy writes on a VM (roles/vmware_vm/tasks/deploy_build_one.yml, site_vmware_deploy):
# "CTASK0010009 - built 2026-10-20" then "Template: rhel9-gold"; older releases one sentence,
# "Deployed from rhel9-gold for CTASK0010009 by ..."
AAP_STATUS = re.compile(r"^(?P<ticket>\S+) - (?:building|built|failed) \d{4}-\d{2}-\d{2}[ \t]*$", re.M)
AAP_TEMPLATE = re.compile(r"^Template: (?P<template>.+?)[ \t]*$", re.M)
AAP_NOTE = re.compile(r"^Deployed from (?P<template>.+?) for (?P<ticket>\S+) by ")


def aap_note(annotation):
    """The ticket and template the AAP deploy wrote in a VM's notes -> (ticket, template), or None (not built by it)."""
    a = str(annotation or "")
    m = AAP_STATUS.search(a)
    if m:
        t = AAP_TEMPLATE.search(a)
        return m.group("ticket"), t.group("template") if t else ""
    m = AAP_NOTE.match(a)
    return (m.group("ticket"), m.group("template")) if m else None


def _iso(t):
    return datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if t else ""


def created_rows(vms, events, since, until):
    """The VMs created in [since, until). Pure: no vCenter objects, so it can be tested on its own.

    vms:    the VMs there now - [{moid, name, created: seconds since 1970 or None, template: bool,
             annotation, ...anything else, copied}]
    events: [{type, time, user, moid, name, source}] - vCenter's VM creation and removal events
    A VM there now counts by its creation date (else its first creation event); one there no more
    counts by its creation event (deleted: true, removed: when). By whom and how: from the creation
    events of the VM (a deploy from a template, else a clone, else created new)."""
    by_vm = {}
    for e in sorted(events or [], key=lambda x: x.get("time") or 0):
        if not e.get("moid") and not e.get("name"):
            continue
        key = e.get("moid") or ("name:" + str(e.get("name") or ""))
        by_vm.setdefault(key, []).append(e)
    living = set()
    rows = []

    def made(evs):
        ce = [e for e in evs if e.get("type") in CREATE]
        pick = ([e for e in ce if e["type"] == "VmDeployedEvent"] or [e for e in ce if e["type"] == "VmClonedEvent"] or ce or [None])[0]
        how = ""
        if pick is not None:
            how = {"VmDeployedEvent": "deployed from template %s", "VmClonedEvent": "cloned from %s"}.get(pick["type"], "created new")
            how = how % (pick.get("source") or "?") if "%s" in how else how
        user = (pick or {}).get("user") or next((e.get("user") for e in ce if e.get("user")), "")
        return (ce[0]["time"] if ce else None), user, how

    for v in vms or []:
        living.add(v.get("moid"))
        living.add("name:" + str(v.get("name") or ""))
        if v.get("template"):
            continue
        evs = by_vm.get(v.get("moid")) or by_vm.get("name:" + str(v.get("name") or "")) or []
        first, user, how = made(evs)
        t = v.get("created") or first
        if not t or not since <= t < until:
            continue
        m = aap_note(v.get("annotation"))
        row = dict((k, x) for k, x in v.items() if k not in ("created", "template", "annotation"))
        row.update(vm=v.get("name", ""), created=_iso(t), by=user, how=how, ticket=m[0] if m else "",
                   template=m[1] if m else "", aap=bool(m), deleted=False, removed="")
        rows.append((t, row))
    for key, evs in by_vm.items():
        if key in living:
            continue
        first, user, how = made(evs)
        if not first or not since <= first < until:
            continue
        gone = [e for e in evs if e.get("type") in REMOVE and e["time"] >= first]
        last = evs[-1]
        rows.append((first, {"vm": last.get("name") or "", "moid": key if not key.startswith("name:") else "", "created": _iso(first), "by": user,
                             "how": how, "ticket": "", "template": "", "aap": False, "deleted": True,
                             "removed": _iso(gone[-1]["time"]) if gone else "", "vcenter": last.get("vcenter", ""),
                             "datacenter": last.get("datacenter", "")}))
    return [r for _, r in sorted(rows, key=lambda x: x[0])]


def _folder(obj):
    """('/DC1/vm/Linux', 'DC1') for a VM, from its parents."""
    names, x = [], getattr(obj, "parent", None)
    while x is not None and not isinstance(x, vim.Datacenter):
        names.append(x.name)
        x = getattr(x, "parent", None)
    dc = x.name if x is not None else ""
    return "/" + "/".join([dc] + list(reversed(names))) if dc else "/" + "/".join(reversed(names)), dc


def _arg_name(a):
    return getattr(a, "name", "") or "" if a is not None else ""


def _events(content, root, begin, cap=20000):
    """vCenter's VM creation and removal events since begin (under root, a datacenter, or all)."""
    kw = {"eventTypeId": list(CREATE + REMOVE)}
    if root is not None:
        kw["entity"] = vim.event.EventFilterSpec.ByEntity(entity=root, recursion="all")
    col = content.eventManager.CreateCollectorForEvents(vim.event.EventFilterSpec(time=vim.event.EventFilterSpec.ByTime(beginTime=begin), **kw))
    found = {}
    try:
        col.SetCollectorPageSize(1000)
        batch = list(col.latestPage or [])
        while batch and len(found) < cap:
            for e in batch:
                found.setdefault(e.key, e)
            nxt = col.ReadPreviousEvents(1000) or []
            if not nxt or all(e.key in found for e in nxt):     # the end (some servers repeat the last page)
                break
            batch = nxt
    finally:
        try:
            col.DestroyCollector()
        except Exception:
            pass
    out = []
    for e in found.values():
        t = type(e).__name__.split(".")[-1]
        ref = e.vm.vm if e.vm is not None else None
        out.append({"type": t, "time": e.createdTime.timestamp(), "user": e.userName or "",
                    "moid": ref._moId if ref is not None else "", "name": _arg_name(e.vm),
                    "source": _arg_name(getattr(e, "srcTemplate", None)) if t == "VmDeployedEvent" else _arg_name(getattr(e, "sourceVm", None)),
                    "datacenter": _arg_name(e.datacenter)})
    return out


def _read(content, p, since, until, vcenter):
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
    spec = vmodl.query.PropertyCollector.FilterSpec(
        objectSet=[vmodl.query.PropertyCollector.ObjectSpec(
            obj=view, skip=True,
            selectSet=[vmodl.query.PropertyCollector.TraversalSpec(name="v", path="view", skip=False, type=vim.view.ContainerView)])],
        propSet=[vmodl.query.PropertyCollector.PropertySpec(type=vim.VirtualMachine,
                                                             pathSet=["name", "config.template", "config.createDate", "config.annotation"])])
    root = None
    if p["datacenter"]:
        dcs = [d for d in content.viewManager.CreateContainerView(content.rootFolder, [vim.Datacenter], True).view if d.name == p["datacenter"]]
        if not dcs:
            view.Destroy()
            return []
        root = dcs[0]
    events = _events(content, root, datetime.datetime.fromtimestamp(since, tz=datetime.timezone.utc))
    for e in events:
        e["vcenter"] = vcenter
    vms, objs = [], {}
    for o in content.propertyCollector.RetrieveContents([spec]) or []:
        pr = {x.name: x.val for x in o.propSet}
        cd = pr.get("config.createDate")
        if cd is not None and cd.tzinfo is None:
            cd = cd.replace(tzinfo=datetime.timezone.utc)
        created = cd.timestamp() if cd is not None and cd.year > 1971 else None
        vms.append({"moid": o.obj._moId, "name": pr.get("name", ""), "created": created, "template": bool(pr.get("config.template")),
                    "annotation": pr.get("config.annotation") or "", "vcenter": vcenter})
        objs[o.obj._moId] = o.obj
    view.Destroy()
    rows = created_rows(vms, events, since, until)
    out = []
    for r in rows:
        v = objs.get(r.get("moid"))
        if v is not None:
            # the details, for the few VMs of the window only
            try:
                folder, dc = _folder(v)
                if p["datacenter"] and dc != p["datacenter"]:
                    continue
                hw = v.config.hardware
                host = v.runtime.host
                r.update(datacenter=dc, folder=folder, guest_os=v.config.guestFullName or "", cpu=hw.numCPU,
                         memory_gb=round(hw.memoryMB / 1024.0, 1),
                         disk_gb=round(sum(d.capacityInKB for d in hw.device if isinstance(d, vim.vm.device.VirtualDisk)) / 1048576.0, 1),
                         power=str(v.runtime.powerState), host=host.name if host is not None else "",
                         cluster=host.parent.name if host is not None and host.parent is not None else "",
                         ip=(v.guest.ipAddress or "") if v.guest else "")
            except vmodl.fault.ManagedObjectNotFound:
                r.update(deleted=True)          # deleted while being read
        elif p["datacenter"] and r.get("datacenter") and r["datacenter"] != p["datacenter"]:
            continue
        out.append(r)
    return out


def main():
    module = AnsibleModule(
        argument_spec=dict(days=dict(type="float", default=7), datacenter=dict(type="str"), validate_certs=dict(type="bool", default=True),
                           vcenters=dict(type="list", elements="str", default=[]), now=dict(type="float")),
        supports_check_mode=True,
    )
    if not HAS_PYVMOMI:
        module.fail_json(msg="pyVmomi is not installed in the execution environment (the vmware.vmware collection needs it too)")
    p = module.params
    if p["days"] <= 0:
        module.fail_json(msg="days must be more than 0, not %s" % p["days"])
    until = p["now"] or time.time()
    since = until - p["days"] * 86400
    done, statuses = each_vcenter(module, lambda si, content, name: _read(content, p, since, until, name), p["vcenters"],
                                  p["validate_certs"], "the VMs created")
    rows = sorted([x for _, part in done for x in part], key=lambda r: r["created"])
    module.exit_json(changed=False, created=rows, since=_iso(since), until=_iso(until), vcenters=statuses)


if __name__ == "__main__":
    main()
