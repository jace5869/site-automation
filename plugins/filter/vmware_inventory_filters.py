# -*- coding: utf-8 -*-
"""Every VM of every vCenter (roles/vmware_vm/library/site_vmware_vms.py) -> the VM inventory report
(playbooks/vm_inventory_report.yml): vm_inventory_doc - the count of VMs, the VMs without encryption, EFI or vTPM (1.11.0),
the hardware versions and VMware Tools, then one table per vCenter and folder: each VM with its power state, ESXi host,
cluster, guest OS, IP address, vCPUs and memory - on the website also encryption, firmware, Secure Boot, vTPM, hardware
version and Tools (columns the Columns menu hides; vm_inventory_hidden_columns start hidden).
A report, never a warning: only a vCenter that could not be read is."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import re

POWER = {"poweredOn": "on", "poweredOff": "off", "suspended": "suspended"}
# vCenter's guest.toolsVersionStatus2 -> (in words, the chip): as in vmware_tools_filters.py, in the report's five words
TOOLS = {"guestToolsCurrent": ("current", "ok"), "guestToolsSupportedNew": ("current", "ok"),
         "guestToolsNeedUpgrade": ("upgrade available", "info"), "guestToolsSupportedOld": ("upgrade available", "info"),
         "guestToolsTooOld": ("unsupported", "warning"), "guestToolsTooNew": ("unsupported", "warning"),
         "guestToolsBlacklisted": ("unsupported", "warning"), "guestToolsUnmanaged": ("guest managed", "ok"),
         "guestToolsNotInstalled": ("not installed", "warning")}
TOOLS_ORDER = ["not running", "not installed", "unsupported", "upgrade available", "current", "guest managed"]
# virtual hardware version -> the oldest ESXi that runs it (as in vmware_tools_filters.py)
COMPAT = {4: "ESX 3.x", 7: "ESX 4.x", 8: "ESXi 5.0", 9: "ESXi 5.1", 10: "ESXi 5.5", 11: "ESXi 6.0", 13: "ESXi 6.5",
          14: "ESXi 6.7", 15: "ESXi 6.7 U2", 17: "ESXi 7.0", 18: "ESXi 7.0 U1", 19: "ESXi 7.0 U2", 20: "ESXi 8.0",
          21: "ESXi 8.0 U2", 22: "ESXi 9.0"}
# the website's extra columns of the VM tables: (header, its full name - the header's hover and the Columns menu)
WEB_COLS = [("Enc", "Encrypted"), ("EFI", "Firmware (EFI or BIOS)"), ("SB", "Secure Boot"), ("vTPM", "Virtual TPM"),
            ("HW", "Hardware version"), ("Tools", "VMware Tools status"), ("Tools ver.", "VMware Tools version")]
SEC_COLS = [("Enc", "Encrypted"), ("Firmware", "Firmware (EFI or BIOS)"), ("SB", "Secure Boot"), ("vTPM", "Virtual TPM")]


def _o(opts):
    o = dict({"templates": False, "max_rows": 2000, "by_folder": True, "security": False, "security_skip_templates": True,
              "security_skip_off": False, "web": False, "hidden_columns": []}, **(opts or {}))
    o["max_rows"] = int(o["max_rows"])
    h = o["hidden_columns"]
    # a list, or text with commas ("SB, Tools ver.": a header may have a space)
    o["hidden_columns"] = [str(x).strip().lower() for x in ((h.split(",") if isinstance(h, str) else h) or []) if str(x).strip()]
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


def _row(v, web=False):
    """A VM's line of the VM tables; on the website the ESXi host by its short name (the workbook has it whole)."""
    return [v.get("name", ""), POWER.get(v.get("power"), v.get("power") or "-") + (" (template)" if v.get("template") else ""),
            (_short(v.get("esxi_host")) if web else v.get("esxi_host")) or "-", v.get("cluster") or "-", v.get("guest_os") or "-", v.get("ip") or "-",
            v.get("cpus") if v.get("cpus") is not None else "-", _gb(v.get("memory_mb"))]


def vm_tools_version_text(v):
    """guest.toolsVersion ('12389': major << 10 | minor << 5 | patch) -> '12.3.5' (as vm_tools_version in
    vmware_tools_filters.py); 2147483647 = 'guest managed'; '' or 0 -> ''."""
    try:
        n = int(str(v or "0").strip())
    except ValueError:
        return str(v)
    if n <= 0:
        return ""
    if n == 2147483647:
        return "guest managed"
    return "%d.%d.%d" % (n >> 10, (n >> 5) & 31, n & 31)


def _hw(v):
    """'vmx-21' -> '21'; '-' when not read."""
    m = re.match(r"^(?:vmx-?)?(\d+)$", str(v.get("hw_version") or "").strip(), re.I)
    return m.group(1) if m else "-"


def _compat(hw):
    n = int(hw) if str(hw).isdigit() else None
    if n is None:
        return ""
    if n in COMPAT:
        return COMPAT[n] + " and later"
    older = [k for k in COMPAT if k < n]
    return (COMPAT[max(older)] + " or later") if older and n < max(COMPAT) else ""


def _tools(v):
    """(the Tools' status in the report's words - current, upgrade available, unsupported, not installed, not running,
    guest managed -, its chip): vCenter's verdict on the version; a powered-on VM whose Tools do not run: not running."""
    st = v.get("tools_status") or ""
    if not st and v.get("tools") == "toolsNotInstalled":
        st = "guestToolsNotInstalled"
    if not st and v.get("tools") == "toolsOld":
        st = "guestToolsNeedUpgrade"
    words, chip = TOOLS.get(st, ("-", ""))
    on = v.get("power") == "poweredOn"
    if on and st != "guestToolsNotInstalled" and v.get("tools_running") == "guestToolsNotRunning":
        return "not running", "warning"
    if not on and chip == "warning":
        chip = ""                          # off: nothing to fix while it is off (its status still said)
    return words, chip


def _sec(v):
    """[(text, chip)] for Encrypted, Firmware, Secure Boot, vTPM - each 'ok' or 'warning' (missing), never colour alone."""
    fw = str(v.get("firmware") or "").lower()
    tpm = v.get("vtpm")
    return [("yes", "ok") if v.get("encrypted") else ("no", "warning"),
            ("EFI", "ok") if fw == "efi" else (("BIOS", "warning") if fw == "bios" else ("-", "")),
            ("on", "ok") if v.get("secure_boot") else ("off", "warning"),
            ("?", "") if tpm is None else (("yes", "ok") if tpm else ("no", "warning"))]


def _missing(v):
    """What it lacks of encryption, EFI and a vTPM (a vTPM not known is not counted)."""
    return [k for k, bad in (("encryption", not v.get("encrypted")), ("EFI", str(v.get("firmware") or "").lower() == "bios"),
                             ("vTPM", v.get("vtpm") is False)) if bad]


def _cols(pairs, web):
    """(columns, their full names): the short headers on the website, the full ones in the email."""
    return [s if web else f for s, f in pairs], [f for s, f in pairs]


def _hidden(o, pairs):
    """The website's columns that start hidden (vm_inventory_hidden_columns: the header or the full name)."""
    return [s for s, f in pairs if s.lower() in o["hidden_columns"] or f.lower() in o["hidden_columns"]]


def _order(vms):
    return sorted(vms, key=lambda v: (str(v.get("vcenter", "")).lower(), vm_folder_label(v.get("folder")).lower(),
                                      str(v.get("name", "")).lower()))


def vm_inventory_doc(vms, vcenters=None, opts=None):
    """The report: {title, status, subtitle, summary, sections}. opts web: the website's (more columns, short headers)."""
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
    web = bool(o["web"])

    sections = []
    if bad:
        sections.append({"title": "vCenters not read", "status": "critical",
                         "lines": [s.get("error") or s.get("name", "") for s in bad],
                         "text": "Their VMs are not in this report: the counts below are of the others."})

    # ---- the VMs without encryption, EFI or vTPM (vm_inventory_security) ----
    sec_tiles = []
    if o["security"]:
        pool = [v for v in allv if not (o["security_skip_templates"] and v.get("template"))
                and not (o["security_skip_off"] and v.get("power") != "poweredOn")]
        lack = [v for v in pool if _missing(v)]
        lack = sorted(_order(lack), key=lambda v: (-len(_missing(v)), not _sec(v)[2][0] == "off"))
        part = lack[:o["max_rows"]]
        heads, full = _cols([("VM", "VM")] + SEC_COLS + [("vCenter", "vCenter"), ("Folder", "Folder"), ("Power", "Power"),
                                                          ("Guest OS", "Guest OS"), ("HW", "Hardware version")], web)
        sec = {"title": "VMs without encryption, EFI or vTPM",
               "text": "Each VM missing VM Encryption (a key from a key provider), EFI firmware or a virtual TPM - the most "
                       "missing first. Secure Boot needs EFI. %s%s" % (
                           "Powered-off VMs left out (vm_inventory_security_skip_off). " if o["security_skip_off"] else "",
                           "Templates left out. " if o["templates"] and o["security_skip_templates"] else ""),
               "columns": heads,
               "rows": [[v.get("name", "")] + [t for t, _ in _sec(v)]
                        + [_short(v.get("vcenter")), vm_folder_label(v.get("folder")),
                           POWER.get(v.get("power"), v.get("power") or "-"), v.get("guest_os") or "-", _hw(v)] for v in part],
               "cell_status": [[""] + [c for _, c in _sec(v)] + ["", "", "", "", ""] for v in part]}
        if web:
            sec.update({"column_titles": full, "keep_case": True, "compact": True})
        sec["text"] = sec["text"].strip()
        if not lack:
            sec["text"] = "Every VM has VM Encryption, EFI firmware and a virtual TPM."
        elif len(lack) > len(part):
            sec["text"] += " %d more not listed (vm_inventory_max_rows: %d)." % (len(lack) - len(part), o["max_rows"])
        sections.append(sec)
        for label, n in (("Not encrypted", len([v for v in pool if not v.get("encrypted")])),
                         ("Without EFI", len([v for v in pool if str(v.get("firmware") or "").lower() == "bios"])),
                         ("Without vTPM", len([v for v in pool if v.get("vtpm") is False]))):
            sec_tiles.append(dict({"label": label, "value": n}, **({"status": "info"} if n else {})))

    # ---- hardware versions and VMware Tools ----
    hws = {}
    for v in allv:
        hws.setdefault(_hw(v), []).append(v)
    rows, cst = [], []
    for hw in sorted(hws, key=lambda h: (not h.isdigit(), -int(h) if h.isdigit() else 0)):
        vs = hws[hw]
        rows.append(["Hardware version %s%s" % (hw, (" - " + _compat(hw)) if _compat(hw) else "") if hw != "-"
                     else "Hardware version not read", len(vs), len([v for v in vs if v.get("power") == "poweredOn"])])
        cst.append(["", "", ""])
    tls = {}
    for v in allv:
        tls.setdefault(_tools(v)[0], []).append(v)
    for w in sorted(tls, key=lambda w: (TOOLS_ORDER.index(w) if w in TOOLS_ORDER else 99, w)):
        vs = tls[w]
        chip = [_tools(v)[1] for v in vs]
        rows.append(["VMware Tools %s" % (w if w != "-" else "not known"), len(vs),
                     len([v for v in vs if v.get("power") == "poweredOn"])])
        cst.append([next((c for c in ("warning", "info", "ok") if c in chip), ""), "", ""])
    if allv:
        sections.append({"title": "Hardware versions and Tools",
                         "text": "How many VMs have each virtual hardware version (and the oldest ESXi that runs it), and "
                                 "what vCenter says of each VM's VMware Tools for the host it runs on. Guest managed: "
                                 "open-vm-tools, updated with the guest's OS.",
                         "columns": ["Hardware version or Tools", "VMs", "Powered on"], "rows": rows, "cell_status": cst,
                         "compact": web})
    fix = [v for v in _order(allv) if v.get("power") == "poweredOn"
           and _tools(v)[0] in ("upgrade available", "unsupported", "not running", "not installed")]
    if fix:
        fix = sorted(fix, key=lambda v: TOOLS_ORDER.index(_tools(v)[0]))
        part = fix[:o["max_rows"]]
        heads, full = _cols([("VM", "VM"), ("Tools", "VMware Tools status"), ("Tools ver.", "VMware Tools version"),
                             ("vCenter", "vCenter"), ("Folder", "Folder"), ("Guest OS", "Guest OS"), ("HW", "Hardware version")], web)
        sec = {"title": "VMware Tools to upgrade or not running",
               "text": "Powered-on VMs whose VMware Tools need an upgrade, are not supported on their host, not installed "
                       "or not running.",
               "columns": heads,
               "rows": [[v.get("name", ""), _tools(v)[0], vm_tools_version_text(v.get("tools_version")) or "-",
                         _short(v.get("vcenter")), vm_folder_label(v.get("folder")), v.get("guest_os") or "-", _hw(v)] for v in part],
               "cell_status": [["", _tools(v)[1], "", "", "", "", ""] for v in part]}
        if web:
            sec.update({"column_titles": full, "compact": True})
        if len(fix) > len(part):
            sec["text"] += " %d more not listed (vm_inventory_max_rows: %d)." % (len(fix) - len(part), o["max_rows"])
        sections.append(sec)

    # ---- every VM, by vCenter and folder ----
    base = [(c, c) for c in ["VM", "Power", "ESXi host", "Cluster", "Guest OS", "IP address"]] + (
        [("CPU", "vCPUs"), ("GB", "Memory (GB)")] if web else [("vCPUs", "vCPUs"), ("Memory (GB)", "Memory (GB)")])
    extra = [p for p in WEB_COLS if o["security"] or p[0] not in ("Enc", "SB", "vTPM")] if web else []
    cols, full = _cols(base + extra, True)
    hidden = _hidden(o, base + extra) if web else []

    def row(v):
        x = []
        if extra:
            sec = dict(zip(("Enc", "EFI", "SB", "vTPM"), _sec(v)))
            tl = _tools(v)
            val = {"HW": (_hw(v), ""), "Tools": tl, "Tools ver.": (vm_tools_version_text(v.get("tools_version")) or "-", "")}
            x = [sec.get(s) or val[s] for s, _ in extra]
        return _row(v, web) + [t for t, _ in x], [""] * len(base) + [c for _, c in x]

    def table(sec, vs, lead=None):
        made = [row(v) for v in vs]
        sec["rows"] = [(lead(v) if lead else []) + r for v, (r, _) in zip(vs, made)]
        if web:
            sec.update({"keep_case": True, "compact": True})
            sec["column_titles"] = (["vCenter", "Folder"] if lead else []) + full
            if hidden:
                sec["hidden_columns"] = hidden
        if extra:
            sec["cell_status"] = [([""] * 2 if lead else []) + c for _, c in made]
        return sec
    shown = 0
    if o["by_folder"]:
        for k in keys:
            g = sorted(groups[k], key=lambda v: str(v.get("name", "")).lower())
            room = max(o["max_rows"] - shown, 0)
            sec = table({"title": "%s - %s" % (_short(k[0]), k[1]),          # the count is beside it (its rows)
                         "columns": cols}, g[:room])
            if len(g) > room:
                sec["text"] = "%d more not listed (vm_inventory_max_rows: %d)." % (len(g) - room, o["max_rows"])
            shown += min(len(g), room)
            sections.append(sec)
    else:
        g = _order(allv)
        sections.append(table({"title": "Every VM", "columns": ["vCenter", "Folder"] + cols}, g[:o["max_rows"]],
                              lambda v: [_short(v.get("vcenter")), vm_folder_label(v.get("folder"))]))
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
                       + sec_tiles
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
    allv = _order([v for v in vms or [] if o["templates"] or not v.get("template")])

    def yn(x):
        return "" if x is None else ("yes" if x else "no")
    sec = o["security"]
    return [{"name": "VMs",
             "columns": ["vCenter", "Datacenter", "Folder", "VM", "Power", "ESXi host", "Cluster", "Guest OS", "IP address",
                         "All IP addresses", "Guest host name", "vCPUs", "Memory (GB)"]
                        + (["Encrypted", "Key provider", "Encrypted vMotion"] if sec else []) + ["Firmware"]
                        + (["Secure Boot", "vTPM"] if sec else [])
                        + ["Hardware version", "Compatible with", "VMware Tools", "Tools version", "Tools running", "Template"],
             "rows": [[v.get("vcenter", ""), v.get("datacenter", ""), vm_folder_label(v.get("folder")), v.get("name", ""),
                       POWER.get(v.get("power"), v.get("power") or ""), v.get("esxi_host", ""), v.get("cluster", ""),
                       v.get("guest_os", ""), v.get("ip", ""), ", ".join(v.get("ips") or []), v.get("hostname", ""),
                       _num(v.get("cpus")), _num(v.get("memory_mb"), 1024)]
                      + ([yn(v.get("encrypted")), v.get("key_provider", ""), v.get("migrate_encryption", "")] if sec else [])
                      + [{"efi": "EFI", "bios": "BIOS"}.get(str(v.get("firmware") or "").lower(), v.get("firmware", ""))]
                      + ([yn(v.get("secure_boot")) if str(v.get("firmware") or "").lower() == "efi" else "no",
                          yn(v.get("vtpm"))] if sec else [])
                      + [_hw(v) if _hw(v) != "-" else "", _compat(_hw(v)), _tools(v)[0] if _tools(v)[0] != "-" else "",
                         vm_tools_version_text(v.get("tools_version")),
                         {"guestToolsRunning": "yes", "guestToolsNotRunning": "no"}.get(v.get("tools_running"), v.get("tools_running", "")),
                         "yes" if v.get("template") else ""] for v in allv]}]


class FilterModule(object):
    def filters(self):
        return {"vm_inventory_doc": vm_inventory_doc, "vm_inventory_xlsx": vm_inventory_xlsx, "vm_folder_label": vm_folder_label}
