#!/usr/bin/python
# -*- coding: utf-8 -*-
"""1.116.6: the outage review's fallback read (vCenter, read-only): the VMs on each lost datastore or host now, with
each VM's host, power state and datastores. Used when the report database holds no placement snapshot from before the
outage (site_vm_placement_read); the review then says the list is as of its own read."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vmware_object_vms
short_description: Read the VMs on named datastores and hosts now (read-only)
description:
  - For each item {vcenter, kind (datastore or host), name, moid}, every VM vCenter lists on it now (datastore.vm,
    host.vm), templates left out, with its host, power state and datastores. Matched by moid when given, else by name
    (a host also by its short name). Changes nothing.
  - vCenter's address and account come from VMWARE_HOST, VMWARE_USER, VMWARE_PASSWORD (and VMWARE_PORT); vcenters adds
    more vCenters (the same account).
options:
  items: {description: "What to read: [{vcenter, kind, name, moid}].", type: list, elements: dict, default: []}
  vcenters: {description: More vCenters to read besides the credential's (same account; name or name:port)., type: list, elements: str, default: []}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
  timeout: {description: Seconds to wait for vCenter., type: int, default: 60}
author: site automation
'''

RETURN = r'''
placements: {description: "Each item with as_of (this read; '' = not found), source review and vms [{name, moid, host, cluster, power, datastores}].", type: list}
read_at: {description: When it was read (UTC, ISO 8601)., type: str}
vcenters: {description: Each vCenter and whether it was read (ok), or why not (error)., type: list}
'''

import datetime

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_vcenters import each_vcenter

try:
    from pyVmomi import vim, vmodl
    HAS_PYVMOMI = True
except ImportError:
    HAS_PYVMOMI = False


def _props(content, root, objtype, paths):
    """{moid: (object, {path: value})} of every object of one type (one round trip)."""
    view = content.viewManager.CreateContainerView(root, [objtype], True)
    try:
        spec = vmodl.query.PropertyCollector.FilterSpec(
            objectSet=[vmodl.query.PropertyCollector.ObjectSpec(
                obj=view, skip=True,
                selectSet=[vmodl.query.PropertyCollector.TraversalSpec(name="v", path="view", skip=False, type=vim.view.ContainerView)])],
            propSet=[vmodl.query.PropertyCollector.PropertySpec(type=objtype, pathSet=paths)])
        return {o.obj._moId: (o.obj, {p.name: p.val for p in o.propSet}) for o in content.propertyCollector.RetrieveContents([spec]) or []}
    finally:
        view.Destroy()


def _read(content, items, vcenter, read_at, props=None):
    """Each item of this vCenter {kind: datastore | host, name, moid, ...} with the VMs on it now (datastore.vm, host.vm)."""
    mine = [i for i in items or [] if isinstance(i, dict) and i.get("kind") in ("datastore", "host")
            and i.get("vcenter", "") in ("", vcenter)]
    if not mine:
        return []
    props = props or _props
    read = {"datastore": props(content, content.rootFolder, vim.Datastore, ["name", "vm"]) if any(i["kind"] == "datastore" for i in mine) else {},
            "host": props(content, content.rootFolder, vim.HostSystem, ["name", "vm"])}
    hosts = {m: pr.get("name", "") for m, (_, pr) in read["host"].items()}
    vms = props(content, content.rootFolder, vim.VirtualMachine,
                ["name", "runtime.host", "runtime.powerState", "config.template", "datastore"])
    stores = {m: pr.get("name", "") for m, (_, pr) in read["datastore"].items()}
    out = []
    for item in mine:
        short = lambda n: str(n or "").split(".")[0]          # noqa: E731
        found = [(m, pr) for m, (_, pr) in read[item["kind"]].items()
                 if (m == item.get("moid") if item.get("moid") else
                     pr.get("name") == item.get("name") or item["kind"] == "host" and short(pr.get("name")) == short(item.get("name")))]
        listed = []
        for m, pr in found[:1]:
            for ref in pr.get("vm") or []:
                v = vms.get(getattr(ref, "_moId", ""))
                if not v or v[1].get("config.template"):
                    continue
                vp = v[1]
                listed.append({"name": vp.get("name", ""), "moid": ref._moId,
                               "host": hosts.get(getattr(vp.get("runtime.host"), "_moId", ""), ""), "cluster": "",
                               "power": str(vp.get("runtime.powerState") or ""),
                               "datastores": sorted({stores.get(getattr(d, "_moId", ""), "") for d in vp.get("datastore") or []} - {""})})
        out.append(dict(item, vcenter=vcenter, as_of=read_at if found else "", source="review",
                        vms=sorted(listed, key=lambda v: (v["name"], v["moid"]))))
    return out


def main():
    module = AnsibleModule(
        argument_spec=dict(items=dict(type="list", elements="dict", default=[]), validate_certs=dict(type="bool", default=True),
                           vcenters=dict(type="list", elements="str", default=[]), timeout=dict(type="int", default=60)),
        supports_check_mode=True,
    )
    p = module.params
    read_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not p["items"]:
        module.exit_json(changed=False, placements=[], read_at=read_at, vcenters=[])
    if not HAS_PYVMOMI:
        module.fail_json(msg="pyVmomi is not installed in the execution environment (the vmware.vmware collection needs it too)")
    done, statuses = each_vcenter(module, lambda si, content, name: _read(content, p["items"], name, read_at), p["vcenters"],
                                  p["validate_certs"], "the VMs on the lost datastores and hosts", p["timeout"])
    module.exit_json(changed=False, placements=[x for _, part in done for x in part], read_at=read_at, vcenters=statuses)


if __name__ == "__main__":
    main()
