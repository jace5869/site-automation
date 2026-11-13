# -*- coding: utf-8 -*-
"""Every VM of every vCenter (roles/vmware_vm/library/site_vmware_vms.py) -> the VMware Tools inventory
(playbooks/vm_tools_report.yml): vm_tools_doc - the VMs whose Tools need a look, the Tools versions, hardware versions
and guest OSes in use with how many VMs each, then one table per vCenter and folder: each VM with its guest OS, Tools
version and status and hardware version; vm_tools_xlsx - the workbook. Only what vCenter says: whether a VM's Tools are
current is vCenter's own answer for the host it runs on (guest.toolsVersionStatus2), no list of versions kept here.
Amber when a powered-on VM has no Tools, Tools not running or a version vCenter does not support; red only for a
vCenter that could not be read."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import re

POWER = {"poweredOn": "on", "poweredOff": "off", "suspended": "suspended"}
# vCenter's guest.toolsVersionStatus2 -> (in words, how it looks: '' fine, low an upgrade waits (1.56.0: was info), warning to look at)
STATUS = {
    "guestToolsCurrent": ("current", ""),
    "guestToolsNeedUpgrade": ("upgrade available", "low"),
    "guestToolsSupportedOld": ("upgrade available (older, supported)", "low"),
    "guestToolsSupportedNew": ("newer than the host's (supported)", ""),
    "guestToolsTooOld": ("too old (not supported)", "warning"),
    "guestToolsTooNew": ("too new for the host (not supported)", "warning"),
    "guestToolsBlacklisted": ("a version with a known problem (upgrade)", "warning"),
    "guestToolsUnmanaged": ("guest managed (updated by the OS)", ""),
    "guestToolsNotInstalled": ("not installed", "warning"),
}
INSTALL = {"guestToolsTypeOpenVMTools": "open-vm-tools", "guestToolsTypeMSI": "MSI (Windows)", "guestToolsTypeTar": "tar",
           "guestToolsTypeOSP": "OSP", "guestToolsTypeUnknown": ""}
# virtual hardware version -> the oldest ESXi that runs it
COMPAT = {4: "ESX 3.x", 7: "ESX 4.x", 8: "ESXi 5.0", 9: "ESXi 5.1", 10: "ESXi 5.5", 11: "ESXi 6.0", 13: "ESXi 6.5",
          14: "ESXi 6.7", 15: "ESXi 6.7 U2", 17: "ESXi 7.0", 18: "ESXi 7.0 U1", 19: "ESXi 7.0 U2", 20: "ESXi 8.0",
          21: "ESXi 8.0 U2", 22: "ESXi 9.0"}


def _o(opts):
    o = dict({"templates": False, "max_rows": 2000, "by_folder": True}, **(opts or {}))
    o["max_rows"] = int(o["max_rows"])
    return o


def _short(name):
    """'vc01.example.mil' -> 'vc01'; an IP address as it is."""
    n = str(name or "")
    return n if n.split(":")[0].replace(".", "").isdigit() else n.split(".")[0]


def _folder(folder):
    """'/DC1/vm/Linux/Web' -> 'DC1 / Linux / Web' (as vm_folder_label in vmware_inventory_filters.py)."""
    parts = [p for p in str(folder or "").split("/") if p]
    if len(parts) >= 2 and parts[1] == "vm":
        parts = [parts[0]] + parts[2:]
    if not parts:
        return "(no folder)"
    return parts[0] + " (no folder)" if len(parts) == 1 else " / ".join(parts)


def _key(v):
    """Version order: '12.10.0' after '12.9.1'; anything else by its text."""
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", str(v or ""))]


def vm_tools_version(v):
    """vCenter's guest.toolsVersion ('12352': major << 10 | minor << 5 | patch) -> '12.2.0'; 2147483647 = 'guest managed'
    (open-vm-tools whose version the OS keeps); '' or 0 -> '' (none)."""
    try:
        n = int(str(v or "0").strip())
    except ValueError:
        return str(v)
    if n <= 0:
        return ""
    if n == 2147483647:
        return "guest managed"
    return "%d.%d.%d" % (n >> 10, (n >> 5) & 31, n & 31)


def vm_hw_number(v):
    """'vmx-21' -> 21 (None: not read)."""
    m = re.match(r"^(?:vmx-?)?(\d+)$", str(v or "").strip(), re.I)
    return int(m.group(1)) if m else None


def vm_hw_compat(v):
    """'vmx-21' -> 'ESXi 8.0 U2 and later'; '' when not known."""
    n = vm_hw_number(v)
    if n is None:
        return ""
    if n in COMPAT:
        return COMPAT[n] + " and later"
    older = [k for k in COMPAT if k < n]
    return (COMPAT[max(older)] + " or later") if older and n < max(COMPAT) else ""


def _state(v):
    """(the Tools' status in words, its look) - vCenter's verdict on the version, and for a powered-on VM whether Tools runs."""
    st = v.get("tools_status") or ""
    if not st and v.get("tools") == "toolsNotInstalled":
        st = "guestToolsNotInstalled"
    words, look = STATUS.get(st, (st or "?", ""))
    if not st and v.get("tools") == "toolsOld":
        words, look = "upgrade available", "low"
    on = v.get("power") == "poweredOn"
    if not on and look == "warning":
        look = ""                                   # a VM that is off: nothing to fix while it is off (its status still said)
    if on and st != "guestToolsNotInstalled" and v.get("tools_running") == "guestToolsNotRunning":
        words, look = words + "; NOT RUNNING", "warning"
    return words, look


def _version(v):
    t = vm_tools_version(v.get("tools_version"))
    return t or ("-" if v.get("tools_status") == "guestToolsNotInstalled" or v.get("tools") == "toolsNotInstalled" else "?")


def _hw(v):
    return v.get("hw_version") or "-"


def _os(v):
    """The guest OS as VMware Tools reports it, else as the VM is configured."""
    return v.get("guest_os_running") or v.get("guest_os") or "-"


def _power(v):
    return POWER.get(v.get("power"), v.get("power") or "-") + (" (template)" if v.get("template") else "")


def _order(vms):
    return sorted(vms, key=lambda v: (str(v.get("vcenter", "")).lower(), _folder(v.get("folder")).lower(), str(v.get("name", "")).lower()))


def _per(vms, value, newest_first=True):
    """'12.3.5 (40), 12.1.0 (3)' - each value and how many VMs have it."""
    g = {}
    for v in vms:
        x = value(v)
        if x:
            g[x] = g.get(x, 0) + 1
    return ", ".join("%s (%d)" % (x, n) for x, n in sorted(g.items(), key=lambda i: _key(i[0]), reverse=newest_first)) or "-"


def _group(vms, value):
    """[(value, [vms])] - the newest version first, '-' (none) last."""
    g = {}
    for v in vms:
        g.setdefault(value(v), []).append(v)
    return sorted(g.items(), key=lambda i: (i[0] in ("-", "?", ""), _neg(i[0])))


def _neg(v):
    """A sort key putting higher versions first ('12.10.0' before '12.9.1'; text at the same places as in _key)."""
    return [(-int(x)) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", str(v or ""))]


def vm_tools_doc(vms, vcenters=None, opts=None):
    """The report: {title, status, subtitle, summary, sections}."""
    o = _o(opts)
    allv = _order([v for v in vms or [] if o["templates"] or not v.get("template")])
    templates = len([v for v in vms or [] if v.get("template")])
    bad = [s for s in vcenters or [] if s.get("error")]
    read = [s.get("name", "") for s in vcenters or [] if not s.get("error")] or sorted(set(v.get("vcenter", "") for v in allv))
    states = [(v, _state(v)) for v in allv]
    look = [v for v, (w, lk) in states if lk == "warning"]
    upgrade = [v for v, (w, lk) in states if lk == "low"]
    current = [v for v in allv if v.get("tools_status") == "guestToolsCurrent"]
    none = [v for v in allv if v.get("tools_status") == "guestToolsNotInstalled" or (not v.get("tools_status") and v.get("tools") == "toolsNotInstalled")]
    none_ids = set(id(v) for v in none)
    not_running = [v for v in allv if v.get("power") == "poweredOn" and id(v) not in none_ids and v.get("tools_running") == "guestToolsNotRunning"]
    managed = [v for v in allv if v.get("tools_status") == "guestToolsUnmanaged"]
    unsupported = [v for v in allv if v.get("tools_status") in ("guestToolsTooOld", "guestToolsTooNew", "guestToolsBlacklisted")]

    sections = []
    if bad:
        sections.append({"title": "vCenters not read", "status": "critical",
                         "lines": [s.get("error") or s.get("name", "") for s in bad],
                         "text": "Their VMs are not in this report: the counts below are of the others."})
    if look:
        part = look[:o["max_rows"]]
        sec = {"title": "To look at", "status": "warning",
               "text": "Powered-on VMs with no VMware Tools, Tools not running, or a Tools version vCenter does not support "
                       "on the host the VM runs on.",
               "columns": ["vCenter", "Folder", "VM", "Power", "Guest OS", "VMware Tools", "Tools status", "Hardware version"],
               "rows": [[_short(v.get("vcenter")), _folder(v.get("folder")), v.get("name", ""), _power(v), _os(v), _version(v),
                         _state(v)[0], _hw(v)] for v in part],
               "row_status": ["warning"] * len(part)}
        if len(look) > len(part):
            sec["text"] += " %d more not listed (vm_tools_max_rows: %d)." % (len(look) - len(part), o["max_rows"])
        sections.append(sec)

    def statuses(vs):
        return _per(vs, lambda v: _state(v)[0], newest_first=False)
    rows = []
    for ver, vs in _group(allv, _version):
        rows.append([ver if ver != "-" else "(not installed)", len(vs), len([v for v in vs if v.get("power") == "poweredOn"]),
                     statuses(vs), _per(vs, lambda v: INSTALL.get(v.get("tools_install_type"), v.get("tools_install_type") or ""))])
    sections.append({"title": "VMware Tools versions",
                     "text": "Each Tools version, how many VMs run it, and what vCenter says of it for the host each VM runs "
                             "on (current, upgrade available ...). Guest managed: open-vm-tools, updated with the guest's OS "
                             "(dnf, apt), not by vCenter.",
                     "columns": ["VMware Tools", "VMs", "Powered on", "Status (VMs)", "Installed as (VMs)"], "rows": rows})
    sections.append({"title": "Hardware versions",
                     "text": "Each virtual hardware version (VM compatibility), the oldest ESXi that runs it, and how many VMs.",
                     "columns": ["Hardware version", "Compatible with", "VMs", "Powered on"],
                     "rows": [[hw, vm_hw_compat(hw) or "-", len(vs), len([v for v in vs if v.get("power") == "poweredOn"])]
                              for hw, vs in _group(allv, _hw)]})
    oses = {}
    for v in allv:
        oses.setdefault(_os(v), []).append(v)
    sections.append({"title": "Guest OS",
                     "text": "Each guest OS (as VMware Tools reports it, else as the VM is configured), with the Tools and "
                             "hardware versions its VMs have.",
                     "columns": ["Guest OS", "VMs", "VMware Tools (VMs)", "Hardware versions (VMs)"],
                     "rows": [[os_, len(vs), _per(vs, _version), _per(vs, _hw)]
                              for os_, vs in sorted(oses.items(), key=lambda i: (-len(i[1]), i[0].lower()))]})

    cols = ["VM", "Power", "Guest OS", "VMware Tools", "Tools status", "Hardware version"]

    def row(v):
        w, lk = _state(v)
        return [v.get("name", ""), _power(v), _os(v), _version(v), "%s - Low" % w if lk == "low" else w, _hw(v)]

    def cells(v):
        lk = _state(v)[1]
        return ["", "", "", "", lk if lk in ("warning", "low") else "", ""]
    shown = 0
    folders = {}
    for v in allv:
        folders.setdefault((v.get("vcenter", ""), _folder(v.get("folder"))), []).append(v)
    if o["by_folder"]:
        for k in sorted(folders, key=lambda k: (k[0].lower(), k[1].lower())):
            g = folders[k]
            room = max(o["max_rows"] - shown, 0)
            sec = {"title": "%s - %s" % (_short(k[0]), k[1]), "columns": cols, "rows": [row(v) for v in g[:room]],
                   "cell_status": [cells(v) for v in g[:room]]}
            if len(g) > room:
                sec["text"] = "%d more not listed (vm_tools_max_rows: %d)." % (len(g) - room, o["max_rows"])
            shown += min(len(g), room)
            sections.append(sec)
    else:
        part = allv[:o["max_rows"]]
        sec = {"title": "Every VM", "columns": ["vCenter", "Folder"] + cols,
               "rows": [[_short(v.get("vcenter")), _folder(v.get("folder"))] + row(v) for v in part],
               "cell_status": [["", ""] + cells(v) for v in part]}
        if len(allv) > len(part):
            sec["text"] = "%d more not listed (vm_tools_max_rows: %d)." % (len(allv) - len(part), o["max_rows"])
        sections.append(sec)

    parts = []
    if upgrade:
        parts.append("%d to upgrade" % len(upgrade))
    if none:
        parts.append("%d without Tools" % len(none))
    if not_running:
        parts.append("%d not running" % len(not_running))
    if unsupported:
        parts.append("%d not supported" % len(unsupported))
    title = "VMware Tools: %d VM%s - %s" % (len(allv), "" if len(allv) == 1 else "s", ", ".join(parts) or "all current")
    if bad:
        title += " - %d vCenter%s not read" % (len(bad), "" if len(bad) == 1 else "s")
    summary = [{"label": "VMs", "value": len(allv)}, {"label": "Tools current", "value": len(current)},
               dict({"label": "Upgrade available", "value": len(upgrade)}, **({"status": "low", "note": "Low"} if upgrade else {"status": ""})),
               {"label": "Guest managed", "value": len(managed)},
               {"label": "No Tools", "value": len(none), "status": "warning" if set(id(v) for v in look) & none_ids else ""},
               {"label": "Not running (on)", "value": len(not_running), "status": "warning" if not_running else ""},
               {"label": "Not supported", "value": len(unsupported), "status": "warning" if unsupported else ""},
               {"label": "Tools versions", "value": len([k for k, _ in _group(allv, _version) if k not in ("-", "?")])},
               {"label": "Hardware versions", "value": len([k for k, _ in _group(allv, _hw) if k != "-"])}]
    summary = [s for s in summary if s["value"] or s["label"] in ("VMs", "Tools current")]     # a count of none: left out
    for s in summary:
        if not s.get("status"):
            s.pop("status", None)
    if bad:
        summary.append({"label": "vCenters not read", "value": len(bad), "status": "critical"})
    return {"title": title, "status": "critical" if bad else ("warning" if look else "ok"),
            "subtitle": "Every VM's VMware Tools, hardware version and guest OS, by vCenter and folder - %s; templates %s" % (
                ", ".join(_short(n) for n in read) or "no vCenter read",
                "included" if o["templates"] else "left out (%d)" % templates),
            "summary": summary, "sections": sections}


def vm_tools_xlsx(vms, opts=None):
    """The email's workbook (roles/site_email/library/site_xlsx.py): every VM on a line, then the Tools and hardware
    versions with their counts."""
    o = _o(opts)
    allv = _order([v for v in vms or [] if o["templates"] or not v.get("template")])
    return [
        {"name": "VMs",
         "columns": ["vCenter", "Datacenter", "Folder", "VM", "Power", "Guest OS (from VMware Tools)", "Guest OS (configured)",
                     "VMware Tools", "Tools status", "Tools status (vCenter)", "Tools running", "Installed as", "Heartbeat",
                     "Hardware version", "Compatible with", "Firmware", "ESXi host", "Cluster", "IP address", "Template"],
         "rows": [[v.get("vcenter", ""), v.get("datacenter", ""), _folder(v.get("folder")), v.get("name", ""),
                   POWER.get(v.get("power"), v.get("power") or ""), v.get("guest_os_running", ""), v.get("guest_os", ""),
                   vm_tools_version(v.get("tools_version")), _state(v)[0], v.get("tools_status") or v.get("tools", ""),
                   {"guestToolsRunning": "yes", "guestToolsNotRunning": "no"}.get(v.get("tools_running"), v.get("tools_running", "")),
                   INSTALL.get(v.get("tools_install_type"), v.get("tools_install_type", "")), v.get("heartbeat", ""),
                   v.get("hw_version", ""), vm_hw_compat(v.get("hw_version")), v.get("firmware", ""), v.get("esxi_host", ""),
                   v.get("cluster", ""), v.get("ip", ""), "yes" if v.get("template") else ""] for v in allv]},
        {"name": "Tools versions", "columns": ["VMware Tools", "VMs", "Powered on", "Status (VMs)"],
         "rows": [[ver if ver != "-" else "(not installed)", len(vs), len([v for v in vs if v.get("power") == "poweredOn"]),
                   _per(vs, lambda v: _state(v)[0], newest_first=False)] for ver, vs in _group(allv, _version)]},
        {"name": "Hardware versions", "columns": ["Hardware version", "Compatible with", "VMs", "Powered on"],
         "rows": [[hw, vm_hw_compat(hw), len(vs), len([v for v in vs if v.get("power") == "poweredOn"])]
                  for hw, vs in _group(allv, _hw)]}]


def vm_tools_lines(vms, opts=None):
    """For the job output: a line per Tools version and per hardware version, then each VM to look at."""
    o = _o(opts)
    allv = _order([v for v in vms or [] if o["templates"] or not v.get("template")])
    return (["VMware Tools %s: %d VM(s) - %s" % (ver if ver != "-" else "(not installed)", len(vs),
                                                _per(vs, lambda v: _state(v)[0], newest_first=False))
             for ver, vs in _group(allv, _version)]
            + ["Hardware %s (%s): %d VM(s)" % (hw, vm_hw_compat(hw) or "?", len(vs)) for hw, vs in _group(allv, _hw)]
            + ["To look at - %s (%s): VMware Tools %s, %s" % (v.get("name", ""), _short(v.get("vcenter")), _version(v), _state(v)[0])
               for v in allv if _state(v)[1] == "warning"][:200])


def vm_tools_versions(vms, opts=None):
    """For the job's artifact: {'tools': {version: VMs}, 'hardware': {version: VMs}}."""
    o = _o(opts)
    allv = [v for v in vms or [] if o["templates"] or not v.get("template")]
    return {"tools": dict((ver, len(vs)) for ver, vs in _group(allv, _version)),
            "hardware": dict((hw, len(vs)) for hw, vs in _group(allv, _hw))}


class FilterModule(object):
    def filters(self):
        return {"vm_tools_doc": vm_tools_doc, "vm_tools_xlsx": vm_tools_xlsx, "vm_tools_lines": vm_tools_lines,
                "vm_tools_versions": vm_tools_versions, "vm_tools_version": vm_tools_version, "vm_hw_compat": vm_hw_compat}
