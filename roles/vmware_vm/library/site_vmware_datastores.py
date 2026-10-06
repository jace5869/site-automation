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
    VMWARE_PORT), as a "VMware vCenter" credential in AAP gives them. Changes nothing.
options:
  datacenter: {description: Only this datacenter's datastores., type: str}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
  timeout: {description: Seconds to wait for vCenter., type: int, default: 60}
author: site automation
'''

RETURN = r'''
datastores:
  description: One entry per datastore, in vCenter's order.
  type: list
  returned: success
  sample: [{name: ds01, datacenter: DC1, cluster: "", type: VMFS, capacity_gb: 4096.0, free_gb: 512.3,
            used_pct: 87.5, provisioned_pct: 112.4, accessible: true, maintenance: normal, hosts: 4, vms: 37}]
'''

import os

from ansible.module_utils.basic import AnsibleModule

try:
    from pyVim.connect import Disconnect, SmartConnect
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


def main():
    module = AnsibleModule(
        argument_spec=dict(datacenter=dict(type="str"), validate_certs=dict(type="bool", default=True),
                           timeout=dict(type="int", default=60)),
        supports_check_mode=True,
    )
    if not HAS_PYVMOMI:
        module.fail_json(msg="pyVmomi is not installed in the execution environment (the vmware.vmware collection needs it too)")
    host = os.environ.get("VMWARE_HOST")
    if not host:
        module.fail_json(msg='No vCenter to talk to: attach a credential of type "VMware vCenter" to this job template.')
    try:
        si = SmartConnect(host=host, user=os.environ.get("VMWARE_USER", ""), pwd=os.environ.get("VMWARE_PASSWORD", ""),
                          port=int(os.environ.get("VMWARE_PORT") or 443),
                          disableSslCertValidation=not module.params["validate_certs"],
                          connectionPoolTimeout=module.params["timeout"])
    except vim.fault.InvalidLogin:
        module.fail_json(msg="vCenter %s refused the login (the VMware vCenter credential)" % host)
    except Exception as e:
        module.fail_json(msg="Could not connect to vCenter %s: %s" % (host, e))
    try:
        content = si.RetrieveContent()
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
            p = {x.name: x.val for x in o.propSet}
            dc, cluster = _where(o.obj)
            if module.params["datacenter"] and dc != module.params["datacenter"]:
                continue
            cap = float(p.get("summary.capacity") or 0)
            free = float(p.get("summary.freeSpace") or 0)
            unc = float(p.get("summary.uncommitted") or 0)
            out.append({"name": p.get("name", ""), "datacenter": dc, "cluster": cluster, "type": p.get("summary.type") or "",
                        "capacity_gb": round(cap / GB, 1), "free_gb": round(free / GB, 1),
                        "used_pct": round(100 * (cap - free) / cap, 1) if cap else 0.0,
                        "provisioned_pct": round(100 * (cap - free + unc) / cap, 1) if cap else 0.0,
                        "accessible": bool(p.get("summary.accessible")),
                        "maintenance": p.get("summary.maintenanceMode") or "normal",
                        "hosts": len(p.get("host") or []), "vms": len(p.get("vm") or [])})
        view.Destroy()
    except Exception as e:
        module.fail_json(msg="Reading the datastores from vCenter %s failed: %s" % (host, e))
    finally:
        try:
            Disconnect(si)
        except Exception:
            pass
    module.exit_json(changed=False, datastores=out)


if __name__ == "__main__":
    main()
