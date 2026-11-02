# -*- coding: utf-8 -*-
"""Every ESXi host's hardware and software (roles/vmware_vm/library/site_vmware_hardware.py) -> the VMware hardware
inventory (playbooks/vmware_hardware_report.yml): vm_hardware_doc - the models in use with their BIOS versions and ESXi
builds, then every host (vendor, model, serial, BIOS, CPUs, memory, ESXi, NICs, storage adapters); vm_hardware_xlsx -
the workbook: hosts, NICs, storage adapters, the software components the hardware status shows (drivers and firmware
with their versions) and every VIB. A report: green, amber for a host not connected (what vCenter last knew), red for a
vCenter that could not be read. Only what vCenter says - no table of current firmware, which would go out of date."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import re


def _o(opts):
    o = dict({"max_rows": 2000}, **(opts or {}))
    o["max_rows"] = int(o["max_rows"])
    return o


def _short(name):
    """'vc01.example.mil' -> 'vc01'; an IP address (with or without its port) as it is."""
    n = str(name or "")
    return n if n.split(":")[0].replace(".", "").isdigit() else n.split(".")[0]


def _key(v):
    """Version order: '1.10.2' after '1.9.0'; anything else by its text."""
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", str(v or ""))]


def _esxi(h):
    return ("%s build %s" % (h.get("esxi"), h.get("build")) if h.get("build") else h.get("esxi")) if h.get("esxi") else "-"


def _bios(h):
    return ("%s (%s)" % (h.get("bios"), h.get("bios_date")) if h.get("bios_date") else h.get("bios")) if h.get("bios") else "-"


def _cpu(h):
    if not h.get("cpu_model") and not h.get("cpu_sockets"):
        return "-"
    return "%s x %s, %d cores" % (h.get("cpu_sockets") or "?", h.get("cpu_model") or "CPU", int(h.get("cpu_cores") or 0))


def _speed(mb):
    mb = int(mb or 0)
    return "" if not mb else ("%dG" % (mb // 1000) if mb >= 1000 else "%dM" % mb)


def _count(items, label):
    """'2x bnxtnet 25G, 2x igbn' - how many of each, in order of appearance."""
    seen = {}
    for i in items:
        k = label(i)
        if k:
            seen[k] = seen.get(k, 0) + 1
    return ", ".join("%dx %s" % (n, k) if n > 1 else k for k, n in seen.items())


def _nics(h):
    return _count(h.get("nics") or [], lambda n: " ".join(x for x in (n.get("driver"), _speed(n.get("speed_mb"))) if x))


def _hbas(h):
    return _count(h.get("hbas") or [], lambda a: " ".join(x for x in (a.get("driver"), "(%s)" % a["kind"] if a.get("kind") else "") if x))


def _state(h):
    s = h.get("state") or ""
    s = "" if s == "connected" else ("not responding" if s == "notResponding" else s)
    return ", ".join(x for x in (s, "maintenance mode" if h.get("maintenance") else "") if x) or "connected"


def _order(hosts):
    return sorted(hosts or [], key=lambda h: (str(h.get("vcenter", "")).lower(), str(h.get("datacenter", "")).lower(),
                                              str(h.get("cluster", "")).lower(), str(h.get("name", "")).lower()))


def _models(hosts):
    """[((vendor, model), [hosts])], the most hosts first."""
    g = {}
    for h in hosts:
        g.setdefault((h.get("vendor") or "-", h.get("model") or "-"), []).append(h)
    return sorted(g.items(), key=lambda x: (-len(x[1]), x[0]))


def _per(hosts, value):
    """'1.10.2 (12), 1.9.0 (2)' - each value and how many hosts have it, the newest first."""
    g = {}
    for h in hosts:
        v = value(h)
        if v:
            g[v] = g.get(v, 0) + 1
    return ", ".join("%s (%d)" % (v, n) for v, n in sorted(g.items(), key=lambda x: _key(x[0]), reverse=True)) or "-"


def vm_hardware_doc(hosts, vcenters=None, opts=None):
    """The report: {title, status, subtitle, summary, sections}."""
    o = _o(opts)
    hosts = _order(hosts)
    bad = [s for s in vcenters or [] if s.get("error")]
    read = [s.get("name", "") for s in vcenters or [] if not s.get("error")]
    off = [h for h in hosts if h.get("state") and h.get("state") != "connected"]
    models = _models(hosts)
    noted = [h for h in hosts if h.get("note") and h not in off]
    sections = []
    if bad:
        sections.append({"title": "vCenters not read", "status": "critical",
                         "lines": [s.get("error") or s.get("name", "") for s in bad],
                         "text": "Their hosts are not in this report: the counts below are of the others."})
    if off:
        sections.append({"title": "Hosts not connected", "status": "warning",
                         "text": "What vCenter last knew of them; their software was not read.",
                         "columns": ["vCenter", "ESXi host", "Cluster", "State"],
                         "rows": [[_short(h.get("vcenter")), h.get("name", ""), h.get("cluster") or "(standalone)", _state(h)]
                                  for h in off], "row_status": ["warning"] * len(off)})
    sections.append({"title": "Models",
                     "text": "Each server model, how many hosts, and the BIOS versions and ESXi builds they run (the newest "
                             "first). One model on several BIOS versions: some hosts were left behind by an update.",
                     "columns": ["Vendor", "Model", "Hosts", "BIOS versions (hosts)", "ESXi (hosts)"],
                     "rows": [[v, m, len(hs), _per(hs, lambda h: h.get("bios")), _per(hs, _esxi)] for (v, m), hs in models]})
    part = hosts[:o["max_rows"]]
    sec = {"title": "ESXi hosts",
           "columns": ["vCenter", "Cluster", "ESXi host", "Vendor", "Model", "Serial", "BIOS (date)", "CPU", "Memory GB",
                       "ESXi", "NICs (driver, speed)", "Storage adapters", "State"],
           "rows": [[_short(h.get("vcenter")), h.get("cluster") or "(standalone)", h.get("name", ""), h.get("vendor") or "-",
                     h.get("model") or "-", h.get("serial") or "-", _bios(h), _cpu(h), h.get("memory_gb") or "-", _esxi(h),
                     _nics(h) or "-", _hbas(h) or "-", _state(h)] for h in part],
           "row_status": ["warning" if h in off else "" for h in part]}
    text = ["The workbook attached has every host, NIC, storage adapter, software component (drivers and firmware the "
            "hardware status shows, with versions) and VIB."]
    if len(hosts) > len(part):
        text.append("%d more not listed (vm_hardware_max_rows: %d)." % (len(hosts) - len(part), o["max_rows"]))
    sec["text"] = " ".join(text)
    sections.append(sec)
    if noted:
        sections.append({"title": "Not all read", "text": "What could not be read for these hosts (the rest of each host is in).",
                         "columns": ["ESXi host", "Note"], "rows": [[h.get("name", ""), h.get("note", "")] for h in noted]})
    serials = sum(1 for h in hosts if h.get("serial"))
    title = "VMware hardware: %d ESXi host%s, %d model%s" % (len(hosts), "" if len(hosts) == 1 else "s", len(models),
                                                             "" if len(models) == 1 else "s")
    if off:
        title += " - %d not connected" % len(off)
    if bad:
        title += " - %d vCenter%s not read" % (len(bad), "" if len(bad) == 1 else "s")
    summary = [{"label": "ESXi hosts", "value": len(hosts)}, {"label": "Models", "value": len(models)},
               {"label": "BIOS versions", "value": len(set((h.get("model"), h.get("bios")) for h in hosts if h.get("bios")))},
               {"label": "ESXi builds", "value": len(set(_esxi(h) for h in hosts if h.get("esxi")))},
               {"label": "With a serial number", "value": "%d of %d" % (serials, len(hosts))}]
    if off:
        summary.append({"label": "Not connected", "value": len(off), "status": "warning"})
    if bad:
        summary.append({"label": "vCenters not read", "value": len(bad), "status": "critical"})
    return {"title": title, "status": "critical" if bad else ("warning" if off else "ok"),
            "subtitle": "Every ESXi host's hardware and software, as vCenter reports them - %s" % (
                ", ".join(_short(n) for n in read) or "no vCenter read"),
            "summary": summary, "sections": sections}


def vm_hardware_xlsx(hosts):
    """The email's workbook (roles/site_email/library/site_xlsx.py): hosts, NICs, storage adapters, software
    components, VIBs - a row each."""
    hs = _order(hosts)

    def where(h):
        return [h.get("vcenter", ""), h.get("cluster", ""), h.get("name", "")]
    return [
        {"name": "ESXi hosts",
         "columns": ["vCenter", "Datacenter", "Cluster", "ESXi host", "Vendor", "Model", "Serial", "Asset tag", "BIOS version",
                     "BIOS date", "CPU model", "CPU sockets", "CPU cores", "CPU threads", "Memory GB", "ESXi version", "ESXi build",
                     "Image profile", "NICs", "Storage adapters", "State", "Maintenance mode", "Note"],
         "rows": [[h.get("vcenter", ""), h.get("datacenter", ""), h.get("cluster", ""), h.get("name", ""), h.get("vendor", ""),
                   h.get("model", ""), h.get("serial", ""), h.get("asset_tag", ""), h.get("bios", ""), h.get("bios_date", ""),
                   h.get("cpu_model", ""), h.get("cpu_sockets", ""), h.get("cpu_cores", ""), h.get("cpu_threads", ""),
                   h.get("memory_gb", ""), h.get("esxi", ""), h.get("build", ""), h.get("image_profile", ""), _nics(h),
                   _hbas(h), h.get("state", ""), "yes" if h.get("maintenance") else "", h.get("note", "")] for h in hs]},
        {"name": "NICs", "columns": ["vCenter", "Cluster", "ESXi host", "NIC", "Driver", "Speed", "MAC", "PCI"],
         "rows": [where(h) + [n.get("device", ""), n.get("driver", ""), _speed(n.get("speed_mb")) or "down", n.get("mac", ""),
                              n.get("pci", "")] for h in hs for n in h.get("nics") or []]},
        {"name": "Storage adapters", "columns": ["vCenter", "Cluster", "ESXi host", "Adapter", "Kind", "Model", "Driver", "Status"],
         "rows": [where(h) + [a.get("device", ""), a.get("kind", ""), a.get("model", ""), a.get("driver", ""), a.get("status", "")]
                  for h in hs for a in h.get("hbas") or []]},
        {"name": "Software components", "columns": ["vCenter", "Cluster", "ESXi host", "Component (name and version)", "Health"],
         "rows": [where(h) + [c.get("name", ""), c.get("state", "")] for h in hs for c in h.get("components") or []]},
        {"name": "VIBs", "columns": ["vCenter", "Cluster", "ESXi host", "VIB", "Version", "Vendor", "Installed", "Summary"],
         "rows": [where(h) + [v.get("name", ""), v.get("version", ""), v.get("vendor", ""), v.get("installed", ""),
                              v.get("summary", "")] for h in hs for v in h.get("vibs") or []]}]


def vm_hardware_lines(hosts):
    """For the job output: a line per model - how many hosts, their BIOS versions and ESXi builds - then a line per host
    with something not read (its note)."""
    hosts = _order(hosts)
    return (["%s %s: %d host(s); BIOS %s; ESXi %s" % (v, m, len(hs), _per(hs, lambda h: h.get("bios")), _per(hs, _esxi))
             for (v, m), hs in _models(hosts)]
            + ["Not all read - %s: %s" % (h.get("name", ""), h["note"]) for h in hosts if h.get("note")])


def vm_hardware_models(hosts):
    """For the job's artifact: [{vendor, model, hosts, bios}]."""
    return [{"vendor": v, "model": m, "hosts": len(hs), "bios": _per(hs, lambda h: h.get("bios"))}
            for (v, m), hs in _models(_order(hosts))]


class FilterModule(object):
    def filters(self):
        return {"vm_hardware_doc": vm_hardware_doc, "vm_hardware_xlsx": vm_hardware_xlsx, "vm_hardware_lines": vm_hardware_lines,
                "vm_hardware_models": vm_hardware_models}
