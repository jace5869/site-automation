#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Read every datastore from vCenter (read-only): capacity, free space, provisioning, state.
pyVmomi only (the vmware.vmware collection needs it anyway, and has no datastore module)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vmware_datastores
short_description: Read vCenter's datastores (read-only)
description:
  - Lists every datastore vCenter knows (or those of one datacenter) with capacity, free space,
    provisioned space, type, accessibility, maintenance mode and how many hosts and VMs use it.
  - vCenter's address and account come from VMWARE_HOST, VMWARE_USER, VMWARE_PASSWORD (and
    VMWARE_PORT), as a "VMware vCenter" credential in AAP gives them; vcenters adds more vCenters
    (the same account). Changes nothing.
options:
  datacenter: {description: Only this datacenter's datastores., type: str}
  vcenters: {description: More vCenters to read besides the credential's (same account; name or name:port)., type: list, elements: str, default: []}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
  timeout: {description: Seconds to wait for vCenter., type: int, default: 60}
author: site automation
'''

RETURN = r'''
datastores:
  description: One entry per datastore, in vCenter's order.
  type: list
  returned: success
  sample: [{name: ds01, vcenter: vc01.example.mil, datacenter: DC1, cluster: "", type: VMFS, capacity_gb: 4096.0, free_gb: 512.3,
            used_pct: 87.5, provisioned_pct: 112.4, accessible: true, maintenance: normal, hosts: 4, vms: 37}]
vcenters:
  description: Each vCenter and whether it was read (ok), or why not (error).
  type: list
  returned: success
'''

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_vcenters import each_vcenter

try:
    from pyVmomi import vim, vmodl
    HAS_PYVMOMI = True
except ImportError:
    HAS_PYVMOMI = False

GB = 1024.0 ** 3


def _where(obj):
    """(datacenter name, datastore cluster name) of a datastore, from its parents."""
    cluster, x = "", getattr(obj, "parent", None)
    while x is not None and not isinstance(x, vim.Datacenter):
        if isinstance(x, vim.StoragePod) and not cluster:
            cluster = x.name
        x = getattr(x, "parent", None)
    return (x.name if x is not None else ""), cluster


def _read(content, p, vcenter):
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.Datastore], True)
    props = ["name", "summary.capacity", "summary.freeSpace", "summary.uncommitted", "summary.type",
             "summary.accessible", "summary.maintenanceMode", "host", "vm"]
    spec = vmodl.query.PropertyCollector.FilterSpec(
        objectSet=[vmodl.query.PropertyCollector.ObjectSpec(
            obj=view, skip=True,
            selectSet=[vmodl.query.PropertyCollector.TraversalSpec(name="v", path="view", skip=False, type=vim.view.ContainerView)])],
        propSet=[vmodl.query.PropertyCollector.PropertySpec(type=vim.Datastore, pathSet=props)])
    out = []
    for o in content.propertyCollector.RetrieveContents([spec]):
        pr = {x.name: x.val for x in o.propSet}
        dc, cluster = _where(o.obj)
        if p["datacenter"] and dc != p["datacenter"]:
            continue
        cap = float(pr.get("summary.capacity") or 0)
        free = float(pr.get("summary.freeSpace") or 0)
        unc = float(pr.get("summary.uncommitted") or 0)
        out.append({"name": pr.get("name", ""), "vcenter": vcenter, "datacenter": dc, "cluster": cluster, "type": pr.get("summary.type") or "",
                    "capacity_gb": round(cap / GB, 1), "free_gb": round(free / GB, 1),
                    "used_pct": round(100 * (cap - free) / cap, 1) if cap else 0.0,
                    "provisioned_pct": round(100 * (cap - free + unc) / cap, 1) if cap else 0.0,
                    "accessible": bool(pr.get("summary.accessible")),
                    "maintenance": pr.get("summary.maintenanceMode") or "normal",
                    "hosts": len(pr.get("host") or []), "vms": len(pr.get("vm") or [])})
    view.Destroy()
    return out


def main():
    module = AnsibleModule(
        argument_spec=dict(datacenter=dict(type="str"), validate_certs=dict(type="bool", default=True),
                           vcenters=dict(type="list", elements="str", default=[]),
                           timeout=dict(type="int", default=60)),
        supports_check_mode=True,
    )
    if not HAS_PYVMOMI:
        module.fail_json(msg="pyVmomi is not installed in the execution environment (the vmware.vmware collection needs it too)")
    p = module.params
    done, statuses = each_vcenter(module, lambda si, content, name: _read(content, p, name), p["vcenters"],
                                  p["validate_certs"], "the datastores", p["timeout"])
    module.exit_json(changed=False, datastores=[x for _, part in done for x in part], vcenters=statuses)


if __name__ == "__main__":
    main()
