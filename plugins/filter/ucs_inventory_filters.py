# -*- coding: utf-8 -*-
"""What the collector read of each UCS Manager domain (roles/ucs_report/library/site_ucs_collect.py with _ucs_classes)
-> the Cisco UCS inventory (playbooks/ucs_inventory_report.yml): ucs_inventory_doc - the domains (UCS Manager version,
HA), the fabric interconnects (model, serial, role, firmware), the chassis with their IO modules, the server models in
use with their BIOS, CIMC and adapter firmware, every server (model, serial, service profile, CPUs, memory, BIOS, CIMC,
adapters, storage controllers), the firmware in use by component and model, and UCS Manager's critical and major
faults; ucs_inventory_xlsx - the workbook: domains, fabric interconnects, chassis, IO modules, servers, adapters, CPUs,
DIMMs, storage controllers, power supplies, every running firmware, service profiles, faults. A report: green, amber
for a component that is not operable, HA not ready or a critical fault, red for a domain that could not be read. Only
what UCS Manager says - no table of current firmware, which would go out of date."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import re

OK_OPERABILITY = ("operable", "")
ABSENT = ("missing", "empty", "missing-slave", "not-supported")
SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2, "warning": 3, "info": 4, "condition": 5, "cleared": 6}
FW_PREFER = ("system", "unspecified", "", "boot-loader")    # a component's firmware: the running system image first


def _o(opts):
    o = dict({"max_rows": 2000, "severities": ["critical", "major"]}, **(opts or {}))
    o["max_rows"] = int(o["max_rows"])
    o["severities"] = [str(s).lower() for s in (o["severities"] or [])]
    return o


def _key(v):
    """Version order: '4.2(3d)' after '4.2(1f)', '4.10' after '4.9'; anything else by its text."""
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", str(v or ""))]


def _per(items, value):
    """'4.2(3d) (12), 4.2(1f) (2)' - each value and how many have it, the newest first."""
    c = {}
    for i in items:
        v = value(i)
        if v:
            c[v] = c.get(v, 0) + 1
    return ", ".join("%s (%d)" % (v, n) for v, n in sorted(c.items(), key=lambda x: _key(x[0]), reverse=True)) or "-"


def _count(items, label):
    """'2x UCSB-MLOM-40G-04, 1x UCSB-VIC-M84-4P' - how many of each, in order of appearance."""
    seen = {}
    for i in items:
        k = label(i)
        if k:
            seen[k] = seen.get(k, 0) + 1
    return ", ".join("%dx %s" % (n, k) if n > 1 else k for k, n in seen.items())


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _s(v):
    v = "" if v is None else str(v).strip()
    return "" if v in ("unspecified", "N/A", "NA") else v


def _short(dn):
    """'sys/chassis-1/blade-3' -> 'chassis-1/blade-3'."""
    dn = str(dn or "")
    return dn[4:] if dn.startswith("sys/") else dn


def _parent(dn):
    return str(dn or "").rsplit("/", 1)[0]


def _gb(mb):
    """Memory in MB (as UCS Manager gives it) -> GB."""
    n = _int(mb)
    return int(round(n / 1024.0)) if n else 0


class Domain(object):
    """One domain the collector tried: its objects by class and by DN, and its firmware by owner."""

    def __init__(self, d):
        self.raw = d
        self.address = d.get("address", "")
        self.reachable = d.get("reachable", True)
        self.error = d.get("error", "")
        self.data = d.get("data") or {}
        top = (self.objs("topSystem") or [{}])[0]
        self.name = _s(top.get("name")) or self.address
        self.top = top
        self.by_dn = {}
        for k in self.data:
            for o in self.objs(k):
                if o.get("dn"):
                    self.by_dn[o["dn"]] = o
        self.fw = {}                     # (owner dn, 'mgmt' | 'bios' | '') -> [firmwareRunning]
        for f in self.objs("firmwareRunning"):
            owner, sub = self.fw_owner(f.get("dn", ""))
            self.fw.setdefault((owner, sub), []).append(f)

    def objs(self, cls):
        return ((self.data.get(cls) or {}).get("objects")) or []

    @staticmethod
    def fw_owner(dn):
        """'sys/chassis-1/blade-1/mgmt/fw-system' -> ('sys/chassis-1/blade-1', 'mgmt'); '.../bios/fw-boot-loader' ->
        (server, 'bios'); '.../storage-SAS-1/fw-system' -> (the controller, '')."""
        parts = str(dn).split("/")[:-1]
        sub = ""
        if parts and parts[-1] in ("mgmt", "bios"):
            sub = parts.pop()
        return "/".join(parts), sub

    def version(self, owner, sub="mgmt", deployment=None):
        """The running firmware version of a component ('' when not read)."""
        rows = self.fw.get((owner, sub)) or []
        if deployment is not None:
            rows = [r for r in rows if r.get("deployment", "") == deployment]
        rows = sorted(rows, key=lambda r: FW_PREFER.index(r.get("deployment", "")) if r.get("deployment", "") in FW_PREFER else 9)
        for r in rows:
            v = _s(r.get("version"))
            if v:
                return v
        return ""

    def under(self, cls, dn):
        """The objects of a class inside a component (a server's CPUs, adapters ...)."""
        return [o for o in self.objs(cls) if str(o.get("dn", "")).startswith(dn + "/")]

    def ucsm(self):
        return self.version("sys", "mgmt", "system") or _s(self.raw.get("version"))

    def notes(self):
        out = []
        for k, d in sorted(self.data.items()):
            d = d or {}
            if d.get("error"):
                out.append("%s: could not read %s - %s" % (self.name, k, d["error"]))
            if d.get("truncated"):
                out.append("%s: %s - only the first %d objects were kept" % (self.name, k, len(d.get("objects") or [])))
        return out


def _domains(collected):
    return [Domain(d) for d in (collected or {}).get("domains") or []]


def _servers(ds):
    """Every blade and rack server: dict(object, _d=domain, _kind=blade|rack)."""
    out = []
    for d in ds:
        if not d.reachable:
            continue
        for cls, kind in (("computeBlade", "blade"), ("computeRackUnit", "rack")):
            for o in d.objs(cls):
                out.append(dict(o, _d=d, _kind=kind))
    return sorted(out, key=lambda s: (s["_d"].name.lower(), s["_kind"], _key(s.get("dn"))))


def _fis(ds):
    return [dict(o, _d=d) for d in ds if d.reachable for o in sorted(d.objs("networkElement"), key=lambda x: x.get("id", ""))]


def _chassis(ds):
    return [dict(o, _d=d) for d in ds if d.reachable for o in sorted(d.objs("equipmentChassis"), key=lambda x: _key(x.get("dn")))]


def _ioms(ds):
    return [dict(o, _d=d) for d in ds if d.reachable for o in sorted(d.objs("equipmentIOCard"), key=lambda x: _key(x.get("dn")))]


def _present(o):
    return _s(o.get("presence")) not in ABSENT


def _bad(o):
    return _present(o) and _s(o.get("operability")) not in OK_OPERABILITY


def _sp(s):
    """The service profile on a server: its name, else what assignedToDn says."""
    d, dn = s["_d"], _s(s.get("assignedToDn"))
    if not dn:
        return ""
    sp = d.by_dn.get(dn) or {}
    return _s(sp.get("name")) or dn.rsplit("/", 1)[-1].replace("ls-", "", 1)


def _cpu(s):
    cpus = [c for c in s["_d"].under("processorUnit", s.get("dn", "")) if _present(c)]
    model = " ".join(_s((cpus or [{}])[0].get("model")).split())
    n, cores = _int(s.get("numOfCpus")) or len(cpus), _int(s.get("numOfCores"))
    if not n and not model:
        return ""
    return "%s x %s%s" % (n or "?", model or "CPU", ", %d cores" % cores if cores else "")


def _dimms(s):
    """'16x 32 GB' - the server's DIMMs by size."""
    ms = [m for m in s["_d"].under("memoryUnit", s.get("dn", "")) if _present(m) and _int(m.get("capacity"))]
    return _count(ms, lambda m: "%d GB" % _gb(m.get("capacity")))


def _adapters(s):
    d = s["_d"]
    return [dict(a, _fw=d.version(a.get("dn", ""))) for a in sorted(d.under("adaptorUnit", s.get("dn", "")), key=lambda a: _key(a.get("dn")))]


def _adapter_text(s):
    return ", ".join(" ".join(x for x in (_s(a.get("model")) or "adapter", "(%s)" % a["_fw"] if a["_fw"] else "") if x)
                     for a in _adapters(s))


def _storage(s):
    d = s["_d"]
    return [dict(c, _fw=d.version(c.get("dn", ""), "")) for c in d.under("storageController", s.get("dn", "")) if _present(c)]


def _storage_text(s):
    return _count(_storage(s), lambda c: " ".join(x for x in (_s(c.get("model")) or _s(c.get("type")) or "controller",
                                                               "(%s)" % c["_fw"] if c["_fw"] else "") if x))


def _bios(s):
    return s["_d"].version(s.get("dn", ""), "bios")


def _cimc(s):
    return s["_d"].version(s.get("dn", ""), "mgmt", "system") or s["_d"].version(s.get("dn", ""), "mgmt")


def _where(s):
    """'chassis-1/blade-3' or 'rack-unit-5'."""
    return _short(s.get("dn"))


def _label(s):
    return _s(s.get("usrLbl")) or _s(s.get("name"))


def _state(o):
    """'ok', or what is wrong: operability, then the operational state."""
    op, st = _s(o.get("operability")), _s(o.get("operState"))
    if op and op not in OK_OPERABILITY:
        return op
    return st or op or "-"


def _role(fi):
    d = fi["_d"]
    me = d.by_dn.get("sys/mgmt-entity-%s" % fi.get("id", "")) or {}
    lead, st = _s(me.get("leadership")), _s(me.get("state"))
    return " ".join(x for x in (lead, "(%s)" % st if st and st != "up" else "") if x)


def _ha(d):
    """'ready' / 'not ready: reason' / 'standalone' for a domain."""
    if _s(d.top.get("mode")) == "stand-alone":
        return "standalone"
    ents = d.objs("mgmtEntity")
    if not ents:
        return ""
    ready = [e for e in ents if _s(e.get("haReady")) in ("yes", "true")]
    if len(ready) == len(ents):
        return "ready"
    why = "; ".join(_s(e.get("haFailureReason")) for e in ents if _s(e.get("haFailureReason")))
    return "not ready" + (": " + why if why else "")


def _iom_text(ch):
    d = ch["_d"]
    ioms = [i for i in d.under("equipmentIOCard", ch.get("dn", "")) if _present(i)]
    return ", ".join("%s: %s%s" % (_s(i.get("switchId")) or _s(i.get("id")), _s(i.get("model")) or "IOM",
                                   " (%s)" % d.version(i.get("dn", "")) if d.version(i.get("dn", "")) else "")
                     for i in sorted(ioms, key=lambda i: _s(i.get("switchId")) or _s(i.get("id"))))


def _psus(o):
    ps = [p for p in o["_d"].under("equipmentPsu", o.get("dn", "")) if _present(p)]
    bad = [p for p in ps if _bad(p)]
    return ("%d" % len(ps) + (" (%d not operable)" % len(bad) if bad else "")) if ps else "-"


def _faults(ds, sev=None):
    out = [dict(f, _d=d) for d in ds if d.reachable for f in d.objs("faultInst")
           if _s(f.get("severity")).lower() != "cleared" and (sev is None or _s(f.get("severity")).lower() in sev)]
    return sorted(out, key=lambda f: (SEVERITY_ORDER.get(_s(f.get("severity")).lower(), 9), f["_d"].name.lower(),
                                      str(f.get("created", ""))), reverse=False)


def _fault_where(f):
    """'sys/chassis-1/blade-1/fault-F0283' -> 'chassis-1/blade-1'."""
    dn = str(f.get("dn", ""))
    return _short(_parent(dn)) if "/fault-" in dn else _short(dn)


def _models(servers):
    """[(model, [servers])], the most servers first."""
    m = {}
    for s in servers:
        m.setdefault(_s(s.get("model")) or "-", []).append(s)
    return sorted(m.items(), key=lambda x: (-len(x[1]), x[0]))


def _kind_of(d, owner, sub, f):
    """What a running firmware belongs to, and that component's model: ('BIOS', 'UCSB-B200-M5')."""
    o = d.by_dn.get(owner) or {}
    cls, model = o.get("_class", ""), _s(o.get("model"))
    if owner == "sys":
        return "UCS Manager", ""
    if sub == "bios":
        return "BIOS", model
    if cls == "networkElement":
        return ("FI kernel" if f.get("deployment") == "kernel" else "FI system"), model
    if cls in ("computeBlade", "computeRackUnit"):
        return "CIMC", model
    if cls == "equipmentIOCard":
        return "IO module", model
    if cls == "adaptorUnit":
        return "Adapter", model
    if cls == "storageController":
        return "Storage controller", model
    if owner.endswith("/boardController"):
        return "Board controller", _s((d.by_dn.get(_parent(owner)) or {}).get("model"))
    if cls == "equipmentPsu":
        return "Power supply", model
    t = _s(f.get("type"))
    return (t.replace("-", " ").capitalize() if t else "Other"), model


def _fw_in_use(ds):
    """[((kind, model), [versions])] - each component kind and model, the versions it runs (system images; the BIOS)."""
    order = ["UCS Manager", "FI system", "FI kernel", "IO module", "BIOS", "CIMC", "Board controller", "Adapter",
             "Storage controller", "Power supply"]
    g = {}
    for d in ds:
        if not d.reachable:
            continue
        for (owner, sub), rows in d.fw.items():
            if sub != "bios":
                rows = [r for r in rows if r.get("deployment", "") in ("system", "kernel", "unspecified", "")]
            seen = set()
            for r in sorted(rows, key=lambda r: FW_PREFER.index(r.get("deployment", "")) if r.get("deployment", "") in FW_PREFER else 9):
                kind, model = _kind_of(d, owner, sub, r)
                if (kind, model) in seen or not _s(r.get("version")):
                    continue
                seen.add((kind, model))
                g.setdefault((kind, model or "-"), []).append(_s(r.get("version")))
    return sorted(g.items(), key=lambda x: (order.index(x[0][0]) if x[0][0] in order else 99, x[0][0], -len(x[1]), x[0][1]))


def _not_operable(ds):
    """[(domain, component, kind, model, serial, state)] - every present component that is not operable."""
    out = []
    for d in ds:
        if not d.reachable:
            continue
        for cls, kind in (("networkElement", "fabric interconnect"), ("equipmentChassis", "chassis"),
                          ("equipmentIOCard", "IO module"), ("equipmentFex", "fabric extender"), ("computeBlade", "blade"),
                          ("computeRackUnit", "rack server"), ("adaptorUnit", "adapter"), ("equipmentPsu", "power supply"),
                          ("storageController", "storage controller"), ("memoryUnit", "DIMM"), ("processorUnit", "CPU")):
            for o in d.objs(cls):
                if _bad(o):
                    out.append([d.name, _short(o.get("dn")), kind, _s(o.get("model")) or "-", _s(o.get("serial")) or "-",
                                _s(o.get("operability"))])
        ha = _ha(d)
        if ha.startswith("not ready"):
            out.append([d.name, "UCS Manager", "HA (the two fabric interconnects)", "-", "-", ha])
    return out


def ucs_inventory_doc(collected, opts=None):
    """The report: {title, status, subtitle, summary, sections}."""
    o = _o(opts)
    ds = _domains(collected)
    bad = [d for d in ds if not d.reachable]
    ok = [d for d in ds if d.reachable]
    servers, fis, chassis = _servers(ds), _fis(ds), _chassis(ds)
    models = _models(servers)
    broken = _not_operable(ds)
    faults = _faults(ds, o["severities"]) if o["severities"] else []
    crit = [f for f in _faults(ds, ["critical"])]
    sections = []
    if bad:
        sections.append({"title": "Domains not read", "status": "critical",
                         "lines": ["%s: %s" % (d.address, d.error) for d in bad],
                         "text": "Their hardware is not in this report: the counts below are of the others."})
    if broken:
        sections.append({"title": "Not operable", "status": "warning",
                         "text": "Components UCS Manager does not call operable (and HA not ready): what to look at first.",
                         "columns": ["Domain", "Component", "Kind", "Model", "Serial", "Operability"],
                         "rows": broken, "row_status": ["warning"] * len(broken)})
    sections.append({"title": "Domains",
                     "columns": ["Domain", "Address", "UCS Manager", "HA", "Fabric interconnects", "Chassis", "Servers",
                                 "Service profiles", "Critical faults", "Major faults", "The account's privileges"],
                     "rows": [[d.name, d.address, d.ucsm() or "-", _ha(d) or "-",
                               _count(d.objs("networkElement"), lambda f: _s(f.get("model")) or "FI") or "-",
                               len(d.objs("equipmentChassis")), len(d.objs("computeBlade")) + len(d.objs("computeRackUnit")),
                               len([p for p in d.objs("lsServer") if _s(p.get("type")) in ("instance", "")]),
                               len([f for f in d.objs("faultInst") if _s(f.get("severity")) == "critical"]),
                               len([f for f in d.objs("faultInst") if _s(f.get("severity")) == "major"]),
                               _s(d.raw.get("priv")) or "-"] for d in ok],
                     "row_status": ["warning" if _ha(d).startswith("not ready") else "" for d in ok]})
    if fis:
        sections.append({"title": "Fabric interconnects",
                         "columns": ["Domain", "FI", "Model", "Serial", "Role", "Management IP", "System firmware", "Kernel",
                                     "Memory GB", "Power supplies", "Operability"],
                         "rows": [[f["_d"].name, _s(f.get("id")) or _short(f.get("dn")), _s(f.get("model")) or "-",
                                   _s(f.get("serial")) or "-", _role(f) or "-", _s(f.get("oobIfIp")) or "-",
                                   f["_d"].version(f.get("dn", ""), "mgmt", "system") or "-",
                                   f["_d"].version(f.get("dn", ""), "mgmt", "kernel") or "-", _gb(f.get("totalMemory")) or "-",
                                   _psus(f), _s(f.get("operability")) or "-"] for f in fis],
                         "row_status": ["warning" if _bad(f) else "" for f in fis]})
    if chassis:
        part = chassis[:o["max_rows"]]
        sections.append({"title": "Chassis",
                         "columns": ["Domain", "Chassis", "Model", "Serial", "IO modules (firmware)", "Blades", "Power supplies",
                                     "Power", "Operability"],
                         "rows": [[c["_d"].name, _short(c.get("dn")), _s(c.get("model")) or "-", _s(c.get("serial")) or "-",
                                   _iom_text(c) or "-", len(c["_d"].under("computeBlade", c.get("dn", ""))), _psus(c),
                                   _s(c.get("power")) or "-", _s(c.get("operability")) or "-"] for c in part],
                         "row_status": ["warning" if _bad(c) else "" for c in part]})
    sections.append({"title": "Server models",
                     "text": "Each server model, how many servers, and the BIOS and CIMC versions they run (the newest first). "
                             "One model on several versions: some servers were left behind by a firmware update (a host "
                             "firmware package not applied).",
                     "columns": ["Model", "Servers", "BIOS (servers)", "CIMC (servers)", "Adapters (firmware)"],
                     "rows": [[m, len(ss), _per(ss, _bios), _per(ss, _cimc),
                               _per([a for s in ss for a in _adapters(s)],
                                    lambda a: " ".join(x for x in (_s(a.get("model")), a["_fw"]) if x))] for m, ss in models]})
    part = servers[:o["max_rows"]]
    sec = {"title": "Servers",
           "columns": ["Domain", "Server", "Name", "Service profile", "Model", "Serial", "CPU", "Memory GB", "BIOS", "CIMC",
                       "Adapters (firmware)", "Storage controllers", "Power", "State"],
           "rows": [[s["_d"].name, _where(s), _label(s) or "-", _sp(s) or "-", _s(s.get("model")) or "-",
                     _s(s.get("serial")) or "-", _cpu(s) or "-", _gb(s.get("totalMemory")) or "-", _bios(s) or "-",
                     _cimc(s) or "-", _adapter_text(s) or "-", _storage_text(s) or "-", _s(s.get("operPower")) or "-",
                     _state(s)] for s in part],
           "row_status": ["warning" if _bad(s) else "" for s in part],
           "text": "The workbook attached has every fabric interconnect, chassis, IO module, server, adapter, CPU, DIMM, "
                   "storage controller, power supply, running firmware, service profile and fault."}
    if len(servers) > len(part):
        sec["text"] += " %d more not listed (ucs_inventory_max_rows: %d)." % (len(servers) - len(part), o["max_rows"])
    sections.append(sec)
    fw = _fw_in_use(ds)
    if fw:
        sections.append({"title": "Firmware in use",
                         "text": "Each component and model, and the firmware versions running on them (how many of each, "
                                 "the newest first). Several versions of one component and model: an update did not reach "
                                 "them all.",
                         "columns": ["Component", "Model", "Count", "Versions (count)"],
                         "rows": [[k, m, len(vs), _per(vs, lambda v: v)] for (k, m), vs in fw]})
    if faults:
        part = faults[:o["max_rows"]]
        sec = {"title": "Faults (%s)" % ", ".join(o["severities"]),
               "text": "UCS Manager's own fault list, the most severe first. Acknowledged: someone in UCS Manager marked it "
                       "seen.",
               "columns": ["Domain", "Severity", "Code", "Component", "Description", "Since", "Acknowledged"],
               "rows": [[f["_d"].name, _s(f.get("severity")), _s(f.get("code")), _fault_where(f), _s(f.get("descr")),
                         _s(f.get("created"))[:19].replace("T", " "), "yes" if _s(f.get("ack")) == "yes" else ""] for f in part],
               "row_status": ["warning" if _s(f.get("severity")) == "critical" else "" for f in part]}
        if len(faults) > len(part):
            sec["text"] += " %d more not listed (ucs_inventory_max_rows: %d)." % (len(faults) - len(part), o["max_rows"])
        sections.append(sec)
    notes = [n for d in ok for n in d.notes()]
    if notes:
        sections.append({"title": "Not all read", "text": "What could not be read (the rest of each domain is in).", "lines": notes})
    serials = len([s for s in servers if _s(s.get("serial"))])
    title = "Cisco UCS: %d server%s, %d model%s, %d domain%s, %d fabric interconnect%s" % (
        len(servers), "" if len(servers) == 1 else "s", len(models), "" if len(models) == 1 else "s",
        len(ok), "" if len(ok) == 1 else "s", len(fis), "" if len(fis) == 1 else "s")
    if broken:
        title += " - %d not operable" % len(broken)
    if crit:
        title += " - %d critical fault%s" % (len(crit), "" if len(crit) == 1 else "s")
    if bad:
        title += " - %d domain%s not read" % (len(bad), "" if len(bad) == 1 else "s")
    summary = [{"label": "Domains", "value": len(ok)}, {"label": "Fabric interconnects", "value": len(fis)},
               {"label": "Chassis", "value": len(chassis)},
               {"label": "Servers", "value": len(servers)}, {"label": "Models", "value": len(models)},
               {"label": "BIOS versions", "value": len(set((_s(s.get("model")), _bios(s)) for s in servers if _bios(s)))},
               {"label": "With a serial number", "value": "%d of %d" % (serials, len(servers))}]
    if broken:
        summary.append({"label": "Not operable", "value": len(broken), "status": "warning"})
    if crit:
        summary.append({"label": "Critical faults", "value": len(crit), "status": "warning"})
    if bad:
        summary.append({"label": "Domains not read", "value": len(bad), "status": "critical"})
    return {"title": title, "status": "critical" if bad else ("warning" if broken or crit else "ok"),
            "subtitle": "Every fabric interconnect, chassis and server, as UCS Manager reports them - %s" % (
                ", ".join(d.name for d in ok) or "no domain read"),
            "summary": summary, "sections": sections}


def ucs_inventory_xlsx(collected):
    """The email's workbook (roles/site_email/library/site_xlsx.py): a sheet per kind of component, a row each."""
    ds = _domains(collected)
    ok = [d for d in ds if d.reachable]
    servers = _servers(ds)

    def num(v):
        return _int(v) if _s(v) and str(v).replace(".", "", 1).isdigit() else ""

    def rows(cls, cols, fn=None):
        return [[d.name] + (fn(d, o) if fn else [_s(o.get(c)) for c in cols]) for d in ok
                for o in sorted(d.objs(cls), key=lambda x: _key(x.get("dn")))]

    def srv(o):
        return _short(_parent(o.get("dn", ""))).replace("/board", "") if o.get("dn") else ""

    def owner_server(d, dn):
        """The server a component is in ('chassis-1/blade-1'), or ''."""
        for p in sorted(d.objs("computeBlade") + d.objs("computeRackUnit"), key=lambda x: -len(x.get("dn", ""))):
            if str(dn).startswith(p.get("dn", "") + "/"):
                return _short(p.get("dn"))
        return ""
    return [
        {"name": "Domains", "columns": ["Domain", "Address", "UCS Manager", "Mode", "HA", "Up time", "Fabric interconnects",
                                        "Chassis", "Servers", "Service profiles", "Faults (not cleared)", "The account's privileges",
                                        "Read"],
         "rows": [[d.name, d.address, d.ucsm(), _s(d.top.get("mode")), _ha(d), _s(d.top.get("systemUpTime")),
                   len(d.objs("networkElement")), len(d.objs("equipmentChassis")),
                   len(d.objs("computeBlade")) + len(d.objs("computeRackUnit")), len(d.objs("lsServer")),
                   len([f for f in d.objs("faultInst") if _s(f.get("severity")) != "cleared"]), _s(d.raw.get("priv")),
                   "yes" if d.reachable else "NO: " + d.error] for d in ds]},
        {"name": "Fabric interconnects",
         "columns": ["Domain", "FI", "Model", "Serial", "Vendor", "Revision", "Role", "Management IP", "Netmask", "Gateway", "MAC",
                     "System firmware", "Kernel", "Memory GB", "Operability", "Thermal"],
         "rows": [[f["_d"].name, _s(f.get("id")), _s(f.get("model")), _s(f.get("serial")), _s(f.get("vendor")), _s(f.get("revision")),
                   _role(f), _s(f.get("oobIfIp")), _s(f.get("oobIfMask")), _s(f.get("oobIfGw")), _s(f.get("oobIfMac")),
                   f["_d"].version(f.get("dn", ""), "mgmt", "system"), f["_d"].version(f.get("dn", ""), "mgmt", "kernel"),
                   _gb(f.get("totalMemory")) or "", _s(f.get("operability")), _s(f.get("thermal"))] for f in _fis(ds)]},
        {"name": "Chassis", "columns": ["Domain", "Chassis", "Model", "Serial", "Vendor", "Part number", "Name", "IO modules",
                                        "Blades", "Power supplies", "Power", "Thermal", "Operability", "State"],
         "rows": [[c["_d"].name, _short(c.get("dn")), _s(c.get("model")), _s(c.get("serial")), _s(c.get("vendor")),
                   _s(c.get("partNumber")), _s(c.get("usrLbl")), _iom_text(c), len(c["_d"].under("computeBlade", c.get("dn", ""))),
                   _psus(c), _s(c.get("power")), _s(c.get("thermal")), _s(c.get("operability")), _s(c.get("operState"))]
                  for c in _chassis(ds)]},
        {"name": "IO modules", "columns": ["Domain", "IO module", "Fabric", "Side", "Model", "Serial", "Firmware", "Operability",
                                           "State"],
         "rows": [[i["_d"].name, _short(i.get("dn")), _s(i.get("switchId")), _s(i.get("side")), _s(i.get("model")),
                   _s(i.get("serial")), i["_d"].version(i.get("dn", "")), _s(i.get("operability")), _s(i.get("operState"))]
                  for i in _ioms(ds)]
                 + [[d.name, _short(x.get("dn")), _s(x.get("switchId")), "FEX", _s(x.get("model")), _s(x.get("serial")),
                     "", _s(x.get("operability")), _s(x.get("operState"))] for d in ok for x in d.objs("equipmentFex")]},
        {"name": "Servers",
         "columns": ["Domain", "Server", "Kind", "Name", "Service profile", "Association", "Model", "Serial", "Vendor", "Part number",
                     "UUID", "Asset tag", "CPUs", "CPU model", "Cores", "Threads", "Memory GB", "DIMMs", "Memory speed", "BIOS",
                     "CIMC", "Board controller", "Adapters", "Storage controllers", "Power", "Operability", "State"],
         "rows": [[s["_d"].name, _where(s), s["_kind"], _label(s), _sp(s), _s(s.get("association")), _s(s.get("model")),
                   _s(s.get("serial")), _s(s.get("vendor")), _s(s.get("partNumber")), _s(s.get("uuid")), _s(s.get("assetTag")),
                   num(s.get("numOfCpus")),
                   " ".join(_s((s["_d"].under("processorUnit", s.get("dn", "")) or [{}])[0].get("model")).split()),
                   num(s.get("numOfCores")), num(s.get("numOfThreads")), _gb(s.get("totalMemory")) or "", _dimms(s),
                   _s(s.get("memorySpeed")), _bios(s), _cimc(s),
                   s["_d"].version(s.get("dn", "") + "/boardController"), _adapter_text(s), _storage_text(s),
                   _s(s.get("operPower")), _s(s.get("operability")), _s(s.get("operState"))] for s in servers]},
        {"name": "Adapters", "columns": ["Domain", "Server", "Adapter", "Model", "Serial", "Vendor", "PCI slot", "Firmware",
                                         "Operability"],
         "rows": [[s["_d"].name, _where(s), _s(a.get("id")) or _short(a.get("dn")), _s(a.get("model")), _s(a.get("serial")),
                   _s(a.get("vendor")), _s(a.get("pciSlot")), a["_fw"], _s(a.get("operability"))]
                  for s in servers for a in _adapters(s)]},
        {"name": "CPUs", "columns": ["Domain", "Server", "Socket", "Model", "Cores", "Threads", "Speed (GHz)", "Operability"],
         "rows": [[d.name, owner_server(d, c.get("dn", "")) or srv(c), _s(c.get("socketDesignation")) or _s(c.get("id")),
                   " ".join(_s(c.get("model")).split()), num(c.get("cores")), num(c.get("threads")), _s(c.get("speed")),
                   _s(c.get("operability"))]
                  for d in ok for c in sorted(d.objs("processorUnit"), key=lambda x: _key(x.get("dn"))) if _present(c)]},
        {"name": "Memory", "columns": ["Domain", "Server", "DIMM", "Size GB", "Model", "Serial", "Vendor", "Type", "Clock",
                                       "Operability"],
         "rows": [[d.name, owner_server(d, m.get("dn", "")), _s(m.get("location")) or _s(m.get("id")), _gb(m.get("capacity")) or "",
                   _s(m.get("model")), _s(m.get("serial")), _s(m.get("vendor")), _s(m.get("type")), _s(m.get("clock")),
                   _s(m.get("operability"))]
                  for d in ok for m in sorted(d.objs("memoryUnit"), key=lambda x: _key(x.get("dn")))
                  if _present(m) and _int(m.get("capacity"))]},
        {"name": "Storage controllers", "columns": ["Domain", "Server", "Controller", "Type", "Model", "Serial", "Vendor",
                                                    "Firmware", "Operability"],
         "rows": [[d.name, owner_server(d, c.get("dn", "")), _short(c.get("dn")).rsplit("/", 1)[-1], _s(c.get("type")),
                   _s(c.get("model")), _s(c.get("serial")), _s(c.get("vendor")), d.version(c.get("dn", ""), ""),
                   _s(c.get("operability"))]
                  for d in ok for c in sorted(d.objs("storageController"), key=lambda x: _key(x.get("dn"))) if _present(c)]},
        {"name": "Power supplies", "columns": ["Domain", "In", "Power supply", "Model", "Serial", "Vendor", "Firmware", "Watts",
                                               "Operability", "Power"],
         "rows": [[d.name, _short(_parent(p.get("dn", ""))), _short(p.get("dn")).rsplit("/", 1)[-1], _s(p.get("model")),
                   _s(p.get("serial")), _s(p.get("vendor")), _s(p.get("psuFirmwareVersion")) or d.version(p.get("dn", ""), ""),
                   _s(p.get("psuWattage")), _s(p.get("operability")), _s(p.get("power"))]
                  for d in ok for p in sorted(d.objs("equipmentPsu"), key=lambda x: _key(x.get("dn"))) if _present(p)]},
        {"name": "Firmware", "columns": ["Domain", "Component", "Kind", "Model", "Type", "Deployment", "Version", "Package"],
         "rows": [[d.name, _short(d.fw_owner(f.get("dn", ""))[0]) or f.get("dn", ""),
                   _kind_of(d, d.fw_owner(f.get("dn", ""))[0], d.fw_owner(f.get("dn", ""))[1], f)[0],
                   _kind_of(d, d.fw_owner(f.get("dn", ""))[0], d.fw_owner(f.get("dn", ""))[1], f)[1],
                   _s(f.get("type")), _s(f.get("deployment")), _s(f.get("version")), _s(f.get("packageVersion"))]
                  for d in ok for f in sorted(d.objs("firmwareRunning"), key=lambda x: _key(x.get("dn")))]},
        {"name": "Service profiles", "columns": ["Domain", "Service profile", "Organization", "Type", "Template", "Server",
                                                 "Association", "State"],
         "rows": [[d.name, _s(p.get("name")), _parent(p.get("dn", "")), _s(p.get("type")),
                   (_s(p.get("operSrcTemplName")) or _s(p.get("srcTemplName"))).rsplit("/", 1)[-1].replace("ls-", "", 1),
                   _short(p.get("pnDn")), _s(p.get("assocState")),
                   _s(p.get("operState"))] for d in ok for p in sorted(d.objs("lsServer"), key=lambda x: _key(x.get("dn")))]},
        {"name": "Faults", "columns": ["Domain", "Severity", "Code", "Component", "Description", "Cause", "Since", "Last change",
                                       "Times", "Acknowledged"],
         "rows": [[f["_d"].name, _s(f.get("severity")), _s(f.get("code")), _fault_where(f), _s(f.get("descr")),
                   _s(f.get("cause")), _s(f.get("created"))[:19].replace("T", " "),
                   _s(f.get("lastTransition"))[:19].replace("T", " "), num(f.get("occur")),
                   "yes" if _s(f.get("ack")) == "yes" else ""] for f in _faults(ds)]}]


def ucs_inventory_lines(collected):
    """For the job output: a line per server model, a line per domain, then the domains not read and what could not be
    read."""
    ds = _domains(collected)
    servers = _servers(ds)
    return (["%s: %d server(s); BIOS %s; CIMC %s" % (m, len(ss), _per(ss, _bios), _per(ss, _cimc)) for m, ss in _models(servers)]
            + ["%s: UCS Manager %s; %s; %d chassis, %d server(s)" % (
                d.name, d.ucsm() or "?", _count(d.objs("networkElement"), lambda f: _s(f.get("model")) or "FI") or "no FI read",
                len(d.objs("equipmentChassis")), len(d.objs("computeBlade")) + len(d.objs("computeRackUnit")))
               for d in ds if d.reachable]
            + ["NOT READ - %s: %s" % (d.address, d.error) for d in ds if not d.reachable]
            + ["Not all read - " + n for d in ds if d.reachable for n in d.notes()])


def ucs_inventory_models(collected):
    """For the job's artifact: [{model, servers, bios, cimc}]."""
    return [{"model": m, "servers": len(ss), "bios": _per(ss, _bios), "cimc": _per(ss, _cimc)}
            for m, ss in _models(_servers(_domains(collected)))]


def ucs_inventory_raw(collected, first=3):
    """For ucs_inventory_raw: per domain and class, how many objects and the first few as UCS Manager gave them."""
    out = {}
    for d in (collected or {}).get("domains") or []:
        out[d.get("address", "")] = dict((k, {"count": len((v or {}).get("objects") or []), "error": (v or {}).get("error", ""),
                                               "first": ((v or {}).get("objects") or [])[:int(first)]})
                                         for k, v in sorted((d.get("data") or {}).items()))
    return out


class FilterModule(object):
    def filters(self):
        return {"ucs_inventory_doc": ucs_inventory_doc, "ucs_inventory_xlsx": ucs_inventory_xlsx,
                "ucs_inventory_lines": ucs_inventory_lines, "ucs_inventory_models": ucs_inventory_models,
                "ucs_inventory_raw": ucs_inventory_raw}
