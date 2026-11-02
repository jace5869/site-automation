#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Read every ESXi host's hardware and software as vCenter knows them (read-only), for the hardware inventory
(playbooks/vmware_hardware_report.yml). pyVmomi only (the vmware.vmware collection needs it anyway)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vmware_hardware
short_description: Read every ESXi host's hardware and software - model, serial, BIOS, CPUs, NICs, HBAs, VIBs (read-only)
description:
  - For every ESXi host of the credential's vCenter and of vcenters - datacenter, cluster, the server's vendor, model,
    serial number (service tag), asset tag, BIOS version and date, CPUs, memory, ESXi version and build, image profile,
    its physical NICs (driver, speed, MAC, PCI slot) and storage adapters (model, driver), the software components its
    hardware status shows (CIM: drivers and firmware the server's vendor reports, with their versions) and, with vibs,
    every installed VIB (name, version, vendor). From vCenter's API with a read-only account: nothing is run on a host.
  - A host that is not connected keeps what vCenter last knew; what cannot be read for one host is said in its note.
  - vCenter's address and account come from VMWARE_HOST, VMWARE_USER, VMWARE_PASSWORD (and VMWARE_PORT), as a
    "VMware vCenter" credential in AAP gives them; vcenters adds more vCenters (the same account). Changes nothing.
options:
  datacenter: {description: Only this datacenter's hosts., type: str}
  vcenters: {description: More vCenters to read besides the credential's (same account; name or name:port)., type: list, elements: str, default: []}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
  vibs: {description: Also read every installed VIB of each host (one call per host)., type: bool, default: true}
author: site automation
'''

RETURN = r'''
hosts:
  description: One entry per ESXi host.
  type: list
  returned: success
  sample: [{name: esx01.example.mil, vcenter: vc01.example.mil, datacenter: DC1, cluster: PROD-CL01, vendor: Dell Inc.,
            model: PowerEdge R750, serial: ABC1234, asset_tag: "", bios: 1.10.2, bios_date: "2026-03-01", cpu_model: Intel(R) Xeon(R) Gold 6338,
            cpu_sockets: 2, cpu_cores: 64, cpu_threads: 128, memory_gb: 1024, esxi: 8.0.3, build: "24022510",
            product: VMware ESXi 8.0.3 build-24022510, image_profile: "(Updated) DEL-ESXi_803...", state: connected,
            maintenance: false, nics: [{device: vmnic0, driver: bnxtnet, speed_mb: 25000, mac: "...", pci: "0000:31:00.0"}],
            hbas: [{device: vmhba0, model: "...", driver: lsi_mr3, kind: block}], components: [{name: "...", state: green}],
            vibs: [{name: bnxtnet, version: "...", vendor: BCM}], note: ""}]
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

PROPS = ["name", "parent", "summary.config.product", "summary.hardware", "hardware.systemInfo", "hardware.biosInfo",
         "runtime.connectionState", "runtime.inMaintenanceMode", "config.network.pnic",
         "config.storageDevice.hostBusAdapter", "runtime.healthSystemRuntime.systemHealthInfo.numericSensorInfo",
         "configManager.imageConfigManager"]
SERIAL_KEYS = ("SerialNumberTag", "ServiceTag", "EnclosureSerialNumberTag")
GB = 1024.0 ** 3


def _s(x):
    return "" if x is None else str(x).strip()


def _where(parent, seen):
    """(datacenter, cluster) of a host's parent (a cluster, or a standalone host's own compute resource: no cluster)."""
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


def _ids(system_info):
    """(serial, asset tag) from the host's system info: serialNumber (vSphere 6.7 and later), else the identifiers the
    server's firmware gives (service tag, serial number tag, enclosure serial)."""
    serial, asset, found = _s(getattr(system_info, "serialNumber", "")), "", {}
    for i in getattr(system_info, "otherIdentifyingInfo", None) or []:
        key = _s(getattr(getattr(i, "identifierType", None), "key", ""))
        val = _s(getattr(i, "identifierValue", ""))
        if val and key not in found:
            found[key] = val
    if not serial:
        serial = next((found[k] for k in SERIAL_KEYS if found.get(k)), "")
    asset = found.get("AssetTag", "")
    return serial, asset if asset.lower() not in ("unknown", "not specified", "none", "no asset tag", "no asset information") else ""


HBA_KINDS = {"FibreChannelHba": "Fibre Channel", "FibreChannelOverEthernetHba": "FCoE", "InternetScsiHba": "iSCSI",
             "BlockHba": "local / RAID", "SerialAttachedHba": "SAS", "ParallelScsiHba": "SCSI", "PcieHba": "NVMe (PCIe)",
             "RdmaHba": "RDMA", "TcpHba": "NVMe/TCP"}


def _kind(adapter):
    """What a storage adapter is: Fibre Channel, iSCSI, local / RAID, SAS, NVMe ..."""
    name = type(adapter).__name__.split(".")[-1]
    return HBA_KINDS.get(name, name[:-3] if name.endswith("Hba") else name)


def _date(d):
    try:
        return d.strftime("%Y-%m-%d") if d else ""
    except Exception:                           # noqa: BLE001
        return _s(d)[:10]


def _software(icm, vibs):
    """(image profile, [vibs], note) from the host's image config manager - nothing run on the host."""
    profile, out, note = "", [], ""
    if icm is None:
        return profile, out, note
    try:
        prof = icm.HostImageConfigGetProfile()
        profile = _s(getattr(prof, "name", ""))
    except Exception as e:                      # noqa: BLE001
        note = "image profile not read (%s)" % (_s(getattr(e, "msg", "")) or type(e).__name__)
    if vibs:
        try:
            for v in icm.FetchSoftwarePackages() or []:
                out.append({"name": _s(getattr(v, "name", "")), "version": _s(getattr(v, "version", "")),
                            "vendor": _s(getattr(v, "vendor", "")), "summary": _s(getattr(v, "summary", ""))[:120],
                            "installed": _date(getattr(v, "installDate", None))})
        except Exception as e:                  # noqa: BLE001
            note = ((note + "; ") if note else "") + "VIBs not read (%s)" % (_s(getattr(e, "msg", "")) or type(e).__name__)
    return profile, sorted(out, key=lambda v: v["name"].lower()), note


def _read(content, p, vcenter):
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
            hw, si, bios = pr.get("summary.hardware"), pr.get("hardware.systemInfo"), pr.get("hardware.biosInfo")
            prod = pr.get("summary.config.product")
            serial, asset = _ids(si)
            state = _s(pr.get("runtime.connectionState"))
            icm = pr.get("configManager.imageConfigManager") if state == "connected" else None
            profile, vibs, note = _software(icm, p["vibs"])
            if state and state != "connected":
                note = "not connected (%s): what vCenter last knew" % state
            sensors = pr.get("runtime.healthSystemRuntime.systemHealthInfo.numericSensorInfo") or []
            out.append({
                "name": _s(pr.get("name")), "vcenter": vcenter, "datacenter": dc, "cluster": cluster,
                "vendor": _s(getattr(si, "vendor", "") or getattr(hw, "vendor", "")),
                "model": _s(getattr(si, "model", "") or getattr(hw, "model", "")),
                "serial": serial, "asset_tag": asset,
                "bios": _s(getattr(bios, "biosVersion", "")), "bios_date": _date(getattr(bios, "releaseDate", None)),
                "bios_vendor": _s(getattr(bios, "vendor", "")),
                "cpu_model": " ".join(_s(getattr(hw, "cpuModel", "")).split()),
                "cpu_sockets": int(getattr(hw, "numCpuPkgs", 0) or 0), "cpu_cores": int(getattr(hw, "numCpuCores", 0) or 0),
                "cpu_threads": int(getattr(hw, "numCpuThreads", 0) or 0),
                "memory_gb": int(round((getattr(hw, "memorySize", 0) or 0) / GB)),
                "esxi": _s(getattr(prod, "version", "")), "build": _s(getattr(prod, "build", "")),
                "product": _s(getattr(prod, "fullName", "")), "image_profile": profile,
                "state": state, "maintenance": bool(pr.get("runtime.inMaintenanceMode")),
                "nics": [{"device": _s(n.device), "driver": _s(getattr(n, "driver", "")),
                          "speed_mb": int(getattr(getattr(n, "linkSpeed", None), "speedMb", 0) or 0),
                          "mac": _s(getattr(n, "mac", "")), "pci": _s(getattr(n, "pci", ""))}
                         for n in pr.get("config.network.pnic") or []],
                "hbas": [{"device": _s(getattr(a, "device", "")), "model": " ".join(_s(getattr(a, "model", "")).split()),
                          "driver": _s(getattr(a, "driver", "")), "status": _s(getattr(a, "status", "")),
                          "kind": _kind(a)}
                         for a in pr.get("config.storageDevice.hostBusAdapter") or []],
                "components": [{"name": " ".join(_s(getattr(c, "name", "")).split()),
                                "state": _s(getattr(getattr(c, "healthState", None), "key", ""))}
                               for c in sensors if _s(getattr(c, "sensorType", "")).lower() == "software components"],
                "vibs": vibs, "note": note})
    finally:
        view.Destroy()
    return out


def main():
    module = AnsibleModule(
        argument_spec=dict(datacenter=dict(type="str"), validate_certs=dict(type="bool", default=True),
                           vcenters=dict(type="list", elements="str", default=[]), vibs=dict(type="bool", default=True)),
        supports_check_mode=True,
    )
    if not HAS_PYVMOMI:
        module.fail_json(msg="pyVmomi is not installed in the execution environment (the vmware.vmware collection needs it too)")
    p = module.params
    done, statuses = each_vcenter(module, lambda si, content, name: _read(content, p, name), p["vcenters"],
                                  p["validate_certs"], "the hosts' hardware")
    module.exit_json(changed=False, hosts=[h for _, hs in done for h in hs], vcenters=statuses)


if __name__ == "__main__":
    main()
