# -*- coding: utf-8 -*-
"""Every VM of every vCenter (roles/vmware_vm/library/site_vmware_vms.py) -> the VM inventory report
(playbooks/vm_inventory_report.yml): vm_inventory_doc - the count of VMs, then one table per vCenter and folder: each VM with its power state, ESXi host, cluster, guest OS, IP address, vCPUs and memory.
A report, never a warning: only a vCenter that could not be read is."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

POWER = {"poweredOn": "on", "poweredOff": "off", "suspended": "suspended"}


def _o(opts):
    o = dict({"templates": False, "max_rows": 2000, "by_folder": True}, **(opts or {}))
    o["max_rows"] = int(o["max_rows"])
    return o


def vm_folder_label(folder):
    """'/DC1/vm/Linux/Web' -> 'DC1 / Linux / Web': the folder as vSphere's VMs and Templates view shows it (without the
    datacenter's hidden vm folder); '/DC1/vm' -> 'DC1 (no folder)'."""
    parts = [p for p in str(folder or "").split("/") if p]
    if len(parts) >= 2 and parts[1] == "vm":
        parts = [parts[0]] + parts[2:]
    if not parts:
        return "(no folder)"
    return parts[0] + " (no folder)" if len(parts) == 1 else " / ".join(parts)


def _short(name):
    return str(name or "").split(".")[0] if not str(name or "").replace(".", "").isdigit() else str(name or "")


def _gb(mb):
    try:
        g = float(mb) / 1024
    except (TypeError, ValueError):
        return "-"
    return "%g" % round(g, 1)


def _row(v):
    return [v.get("name", ""), POWER.get(v.get("power"), v.get("power") or "-") + (" (template)" if v.get("template") else ""),
            v.get("esxi_host") or "-", v.get("cluster") or "-", v.get("guest_os") or "-", v.get("ip") or "-",
            v.get("cpus") if v.get("cpus") is not None else "-", _gb(v.get("memory_mb"))]


def vm_inventory_doc(vms, vcenters=None, opts=None):
    """The report: {title, status, subtitle, summary, sections}."""
    o = _o(opts)
    allv = [v for v in vms or [] if o["templates"] or not v.get("template")]
    templates = len([v for v in vms or [] if v.get("template")])
    bad = [s for s in vcenters or [] if s.get("error")]
    read = [s.get("name", "") for s in vcenters or [] if s.get("ok")] or sorted(set(v.get("vcenter", "") for v in allv))
    on = len([v for v in allv if v.get("power") == "poweredOn"])
    off = len([v for v in allv if v.get("power") == "poweredOff"])
    groups = {}
    for v in allv:
        groups.setdefault((v.get("vcenter", ""), vm_folder_label(v.get("folder"))), []).append(v)
    keys = sorted(groups, key=lambda k: (k[0].lower(), k[1].lower()))

    sections = []
    if bad:
        sections.append({"title": "vCenters not read", "status": "critical",
                         "lines": [s.get("error") or s.get("name", "") for s in bad],
                         "text": "Their VMs are not in this report: the counts below are of the others."})
    cols = ["VM", "Power", "ESXi host", "Cluster", "Guest OS", "IP address", "vCPUs", "Memory (GB)"]
    shown = 0
    if o["by_folder"]:
        for k in keys:
            g = sorted(groups[k], key=lambda v: str(v.get("name", "")).lower())
            room = max(o["max_rows"] - shown, 0)
            sec = {"title": "%s - %s" % (_short(k[0]), k[1]),          # the count is beside it (its rows)
                   "columns": cols, "rows": [_row(v) for v in g[:room]]}
            if len(g) > room:
                sec["text"] = "%d more not listed (vm_inventory_max_rows: %d)." % (len(g) - room, o["max_rows"])
            shown += min(len(g), room)
            sections.append(sec)
    else:
        g = sorted(allv, key=lambda v: (str(v.get("vcenter", "")).lower(), vm_folder_label(v.get("folder")).lower(),
                                        str(v.get("name", "")).lower()))
        sections.append({"title": "Every VM", "columns": ["vCenter", "Folder"] + cols,
                         "rows": [[_short(v.get("vcenter")), vm_folder_label(v.get("folder"))] + _row(v) for v in g[:o["max_rows"]]]})
    hosts = len(set((v.get("vcenter"), v.get("esxi_host")) for v in allv if v.get("esxi_host")))
    return {"title": "VM inventory: %d VM%s in %d vCenter%s" % (len(allv), "" if len(allv) == 1 else "s", len(read),
                                                            "" if len(read) == 1 else "s")
                     + (" - %d not read" % len(bad) if bad else ""),
            "status": "critical" if bad else "ok",
            "subtitle": "Every VM by vCenter and folder, with the ESXi host it runs on - %s; templates %s" % (
                ", ".join(_short(n) for n in read), "included" if o["templates"] else "left out (%d)" % templates),
            "summary": [{"label": "VMs", "value": len(allv)}, {"label": "Powered on", "value": on},
                        {"label": "Powered off", "value": off}, {"label": "Folders", "value": len(keys)},
                        {"label": "ESXi hosts", "value": hosts}, {"label": "vCenters", "value": len(read)}]
                       + ([{"label": "Not read", "value": len(bad), "status": "critical"}] if bad else []),
            "sections": sections}


def _num(v, scale=1):
    try:
        x = float(v) / scale
    except (TypeError, ValueError):
        return ""
    return int(x) if x == int(x) else round(x, 1)


def vm_inventory_xlsx(vms, opts=None):
    """The email's workbook (roles/site_email/library/site_xlsx.py): one sheet, every VM on a line - to sort and filter."""
    o = _o(opts)
    allv = sorted([v for v in vms or [] if o["templates"] or not v.get("template")],
                  key=lambda v: (str(v.get("vcenter", "")).lower(), vm_folder_label(v.get("folder")).lower(), str(v.get("name", "")).lower()))
    return [{"name": "VMs",
             "columns": ["vCenter", "Datacenter", "Folder", "VM", "Power", "ESXi host", "Cluster", "Guest OS", "IP address",
                         "All IP addresses", "Guest host name", "vCPUs", "Memory (GB)", "Template"],
             "rows": [[v.get("vcenter", ""), v.get("datacenter", ""), vm_folder_label(v.get("folder")), v.get("name", ""),
                       POWER.get(v.get("power"), v.get("power") or ""), v.get("esxi_host", ""), v.get("cluster", ""),
                       v.get("guest_os", ""), v.get("ip", ""), ", ".join(v.get("ips") or []), v.get("hostname", ""),
                       _num(v.get("cpus")), _num(v.get("memory_mb"), 1024), "yes" if v.get("template") else ""] for v in allv]}]


class FilterModule(object):
    def filters(self):
        return {"vm_inventory_doc": vm_inventory_doc, "vm_inventory_xlsx": vm_inventory_xlsx, "vm_folder_label": vm_folder_label}
