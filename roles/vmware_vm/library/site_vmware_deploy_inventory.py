#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Read what the VM deploy's settings name (read-only): per vCenter and datacenter, the clusters and
standalone hosts (with their datastores and networks), the templates, the VM folders, the datastore
clusters and the customization specifications - exactly as vm_deploy_* and a ticket must write them."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vmware_deploy_inventory
short_description: Read vCenter's clusters, templates, datastores, folders and networks for the VM deploy (read-only)
description:
  - For every datacenter of every vCenter (the credential's and vcenters), lists the clusters and
    standalone hosts with their hosts, memory use, datastores (free space, shared or local) and
    networks; the templates with where they are and what they have (guest OS, CPU, memory, disks,
    networks); the VM folders as paths below the datacenter; the datastore clusters; and the
    customization specifications.
  - The names are the ones the VM deploy's settings and a ticket use (vm_deploy_name_rules,
    vm_deploy_templates, cluster:, datacenter:, vcenter:, folder:). Changes nothing.
options:
  datacenter: {description: Only this datacenter., type: str}
  vcenters: {description: More vCenters to read besides the credential's (same account; name or name:port)., type: list, elements: str, default: []}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
  timeout: {description: Seconds to wait for vCenter., type: int, default: 60}
author: site automation
'''

RETURN = r'''
datacenters:
  description: One entry per datacenter, per vCenter.
  type: list
  returned: success
  sample: [{vcenter: vc01.example.mil, name: DC1, folders: [Linux, Linux/App], datastore_clusters: [],
            clusters: [{name: CL1, kind: cluster, hosts: 4, hosts_ok: 4, drs: true, ha: true, memory_gb: 1024.0, memory_used_pct: 61.2,
                        datastores: [{name: ds01, capacity_gb: 4096.0, free_gb: 900.5, used_pct: 78.0, shared: true, ok: true}],
                        networks: [VLAN100-Staging]}],
            templates: [{name: win2022-pxe, folder: Templates, cluster: CL1, host: esx01, guest: Microsoft Windows Server 2022 (64-bit),
                         windows: true, cpu: 2, memory_gb: 8.0, disks_gb: [100], networks: [VLAN100-Staging], datastores: [ds01], firmware: efi}]}]
customization_specs:
  description: The customization specifications of each vCenter ([] when the account may not read them).
  type: list
  returned: success
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


def _view(content, root, kind):
    v = content.viewManager.CreateContainerView(root, [kind], True)
    try:
        return list(v.view)
    finally:
        v.Destroy()


def _folders(root, prefix=""):
    """Every VM folder below the datacenter's VM folder, as the deploy's folder: setting writes it."""
    out = []
    for c in root.childEntity:
        if isinstance(c, vim.Folder):
            path = prefix + c.name
            out.append(path)
            out += _folders(c, path + "/")
    return out


def _folder_of(obj, dc):
    names, f = [], obj.parent
    while f is not None and f != dc.vmFolder and not isinstance(f, vim.Datacenter):
        names.append(f.name)
        f = getattr(f, "parent", None)
    return "/".join(reversed(names))


def _ds(d, hosts_in):
    """A datastore of a cluster: free space, and shared as the deploy's automatic placement counts it
    (several hosts mount it, or the cluster is one host)."""
    try:
        s = d.summary
        cap, free = float(s.capacity or 0), float(s.freeSpace or 0)
        mounted = len(d.host or [])
        return {"name": d.name, "capacity_gb": round(cap / GB, 1), "free_gb": round(free / GB, 1),
                "used_pct": round(100 * (cap - free) / cap, 1) if cap else 0.0,
                "shared": bool(s.multipleHostAccess) or mounted >= 2 or hosts_in == 1,
                "ok": bool(s.accessible) and (s.maintenanceMode or "normal") == "normal"}
    except vmodl.fault.ManagedObjectNotFound:
        return None


def _compute(cr):
    hosts = list(cr.host or [])
    ok = [h for h in hosts if str(h.runtime.connectionState) == "connected" and not h.runtime.inMaintenanceMode]
    mem = sum(float(h.summary.hardware.memorySize or 0) for h in hosts if h.summary.hardware)
    used = sum(float(h.summary.quickStats.overallMemoryUsage or 0) * 1048576 for h in ok)
    cluster = isinstance(cr, vim.ClusterComputeResource)
    cfg = cr.configurationEx if cluster else None
    dss = [x for x in (_ds(d, len(hosts)) for d in (cr.datastore or [])) if x]
    return {"name": cr.name, "kind": "cluster" if cluster else "standalone host",
            "hosts": len(hosts), "hosts_ok": len(ok),
            "drs": bool(cfg and cfg.drsConfig and cfg.drsConfig.enabled),
            "ha": bool(cfg and cfg.dasConfig and cfg.dasConfig.enabled),
            "memory_gb": round(mem / GB, 1), "memory_used_pct": round(100 * used / mem, 1) if mem else 0.0,
            "datastores": sorted(dss, key=lambda x: (not x["shared"], -x["free_gb"])),
            "networks": sorted({n.name for n in (cr.network or [])})}


def _template(v, dc):
    cfg = v.config
    hw = cfg.hardware
    disks = [int(round(d.capacityInKB / 1048576.0)) for d in hw.device if isinstance(d, vim.vm.device.VirtualDisk)]
    nets = sorted(n.name for n in (v.network or []))
    host = v.runtime.host
    guest = cfg.guestFullName or cfg.guestId or ""
    return {"name": v.name, "folder": _folder_of(v, dc),
            "cluster": host.parent.name if host is not None and host.parent is not None else "",
            "host": host.name if host is not None else "",
            "guest": guest, "windows": str(cfg.guestId or "").lower().startswith("win"),
            "cpu": hw.numCPU, "memory_gb": round(hw.memoryMB / 1024.0, 1), "disks_gb": disks,
            "networks": nets, "datastores": sorted(d.name for d in (v.datastore or [])),
            "firmware": cfg.firmware or ""}


def _read(content, p, vcenter):
    out = []
    for dc in _view(content, content.rootFolder, vim.Datacenter):
        if p["datacenter"] and dc.name != p["datacenter"]:
            continue
        crs = [c for c in _view(content, dc.hostFolder, vim.ComputeResource)]
        templates = []
        for v in _view(content, dc.vmFolder, vim.VirtualMachine):
            try:
                if v.config is not None and v.config.template:
                    templates.append(_template(v, dc))
            except vmodl.fault.ManagedObjectNotFound:
                continue
        out.append({"vcenter": vcenter, "name": dc.name,
                    "clusters": sorted((_compute(c) for c in crs), key=lambda x: (x["kind"] != "cluster", x["name"])),
                    "templates": sorted(templates, key=lambda x: x["name"]),
                    "folders": sorted(_folders(dc.vmFolder)),
                    "datastore_clusters": sorted(x.name for x in _view(content, dc.datastoreFolder, vim.StoragePod))})
    try:
        specs = sorted(i.name for i in (content.customizationSpecManager.info or [])) if content.customizationSpecManager else []
    except (vim.fault.NoPermission, vmodl.fault.SecurityError, vmodl.MethodFault):
        specs = []
    return {"datacenters": out, "specs": {"vcenter": vcenter, "names": specs}}


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
                                  p["validate_certs"], "the clusters, templates, datastores and folders", p["timeout"])
    module.exit_json(changed=False, datacenters=[x for _, part in done for x in part["datacenters"]],
                     customization_specs=[part["specs"] for _, part in done], vcenters=statuses)


if __name__ == "__main__":
    main()
