#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Read each vCenter's version and build, and every ESXi host's (read-only), for the version report
(playbooks/vmware_version_report.yml). pyVmomi only (the vmware.vmware collection needs it anyway)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vmware_versions
short_description: Read the version and build of each vCenter and every ESXi host (read-only)
description:
  - For the credential's vCenter and each of vcenters - its product name, version and build as vCenter says them - and
    for every ESXi host it manages - datacenter, cluster (none for a standalone host), version, build, the product name,
    connection state and maintenance mode. A host that is not connected may have no version: it is said empty.
  - vCenter's address and account come from VMWARE_HOST, VMWARE_USER, VMWARE_PASSWORD (and VMWARE_PORT), as a
    "VMware vCenter" credential in AAP gives them; vcenters adds more vCenters (the same account). Changes nothing.
options:
  datacenter: {description: Only this datacenter's hosts., type: str}
  vcenters: {description: More vCenters to read besides the credential's (same account; name or name:port)., type: list, elements: str, default: []}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
author: site automation
'''

RETURN = r'''
vcenter_info:
  description: One entry per vCenter read.
  type: list
  returned: success
  sample: [{vcenter: vc01.example.mil, product: VMware vCenter Server 8.0.3 build-24322831, version: 8.0.3, build: "24322831",
            api: 8.0.3}]
hosts:
  description: One entry per ESXi host.
  type: list
  returned: success
  sample: [{name: esx01.example.mil, vcenter: vc01.example.mil, datacenter: DC1, cluster: PROD-CL01,
            product: VMware ESXi 8.0.3 build-24022510, version: 8.0.3, build: "24022510", state: connected, maintenance: false}]
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

PROPS = ["name", "parent", "summary.config.product", "runtime.connectionState", "runtime.inMaintenanceMode"]


def _s(x):
    return "" if x is None else str(x)


def _where(parent, seen):
    """(datacenter, cluster) of a host's parent (a cluster, or a standalone host's own compute resource: no cluster),
    each parent read once."""
    if parent is None:
        return "", ""
    if parent._moId not in seen:
        try:
            x, cluster = parent, parent.name if isinstance(parent, vim.ClusterComputeResource) else ""
            while x is not None and not isinstance(x, vim.Datacenter):
                x = getattr(x, "parent", None)
            seen[parent._moId] = (x.name if x is not None else "", cluster)
        except Exception:                       # noqa: BLE001
            seen[parent._moId] = ("", "")
    return seen[parent._moId]


def _read(content, p, vcenter):
    a = content.about
    info = {"vcenter": vcenter, "product": _s(getattr(a, "fullName", "")), "version": _s(getattr(a, "version", "")),
            "build": _s(getattr(a, "build", "")), "api": _s(getattr(a, "apiVersion", ""))}
    out, seen = [], {}
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.HostSystem], True)
    spec = vmodl.query.PropertyCollector.FilterSpec(
        objectSet=[vmodl.query.PropertyCollector.ObjectSpec(
            obj=view, skip=True,
            selectSet=[vmodl.query.PropertyCollector.TraversalSpec(name="v", path="view", skip=False, type=vim.view.ContainerView)])],
        propSet=[vmodl.query.PropertyCollector.PropertySpec(type=vim.HostSystem, pathSet=PROPS)])
    try:
        for o in content.propertyCollector.RetrieveContents([spec]):
            pr = {x.name: x.val for x in o.propSet}     # a property vCenter does not have for this host is left out
            dc, cluster = _where(pr.get("parent"), seen)
            if p["datacenter"] and dc != p["datacenter"]:
                continue
            prod = pr.get("summary.config.product")
            out.append({"name": _s(pr.get("name")), "vcenter": vcenter, "datacenter": dc, "cluster": cluster,
                        "product": _s(getattr(prod, "fullName", "")), "version": _s(getattr(prod, "version", "")),
                        "build": _s(getattr(prod, "build", "")), "state": _s(pr.get("runtime.connectionState")),
                        "maintenance": bool(pr.get("runtime.inMaintenanceMode"))})
    finally:
        view.Destroy()
    return info, out


def main():
    module = AnsibleModule(
        argument_spec=dict(datacenter=dict(type="str"), validate_certs=dict(type="bool", default=True),
                           vcenters=dict(type="list", elements="str", default=[])),
        supports_check_mode=True,
    )
    if not HAS_PYVMOMI:
        module.fail_json(msg="pyVmomi is not installed in the execution environment (the vmware.vmware collection needs it too)")
    p = module.params
    done, statuses = each_vcenter(module, lambda si, content, name: _read(content, p, name), p["vcenters"],
                                  p["validate_certs"], "the versions")
    module.exit_json(changed=False, vcenter_info=[info for _, (info, _h) in done],
                     hosts=[h for _, (_i, hs) in done for h in hs], vcenters=statuses)


if __name__ == "__main__":
    main()
