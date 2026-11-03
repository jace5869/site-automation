# -*- coding: utf-8 -*-
"""What the collector read of each ONTAP cluster's hardware (roles/ontap_report/library/site_ontap_collect.py with
_ontap_hw_queries) -> the NetApp ONTAP hardware inventory (playbooks/ontap_hardware_report.yml): ontap_hardware_doc - the
controller models in use with their ONTAP, firmware and service processor versions, then every controller (model,
serial, system ID, CPUs, memory, firmware, SP / BMC, NVRAM battery, Flash Cache, ports), every shelf (model, serial,
module firmware) and the disks by model (with their firmware versions); ontap_hardware_xlsx - the workbook: clusters,
controllers, shelves, shelf modules, disks, Ethernet and FC ports. A report: green, amber for a controller not up or a
shelf not ok, red for a cluster that could not be read. Only what ONTAP says - no table of current firmware, which would
go out of date."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import re

SP = {"sp": "SP", "bmc": "BMC"}
BATTERY = {"battery_ok": "ok", "battery_fully_charged": "fully charged", "battery_partially_discharged": "partially discharged",
           "battery_fully_discharged": "fully discharged", "battery_not_present": "not present",
           "battery_near_end_of_life": "near end of life", "battery_at_end_of_life": "at end of life",
           "battery_unknown": "unknown", "battery_over_charged": "over charged"}
BATTERY_OK = ("battery_ok", "battery_fully_charged", "")


def _o(opts):
    o = dict({"max_rows": 2000}, **(opts or {}))
    o["max_rows"] = int(o["max_rows"])
    return o


def g(d, path, default=None):
    """d['a']['b'] for path 'a.b' (None-safe)."""
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur if cur is not None else default


def human_bytes(n):
    """1099511627776 -> '1.0 TB' (binary units, as ONTAP shows them); '' when not known."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024 or unit == "PB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return ""


def short_version(v):
    """'NetApp Release 9.14.1P3: Mon Jan 01 2026' -> '9.14.1P3'."""
    return str(v or "").replace("NetApp Release ", "").split(":")[0].strip()


def uptime_text(seconds):
    """93784 -> '1d 02:03'; '' when not known."""
    try:
        s = int(float(seconds))
    except (TypeError, ValueError):
        return ""
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    return "%dd %02d:%02d" % (d, h, s // 60)


def _key(v):
    """Version order: '9.14.1P3' after '9.9.1'; anything else by its text."""
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", str(v or ""))]


def _per(items, value):
    """'9.14.1P3 (4), 9.13.1P8 (2)' - each value and how many have it, the newest first."""
    c = {}
    for i in items:
        v = value(i)
        if v:
            c[v] = c.get(v, 0) + 1
    return ", ".join("%s (%d)" % (v, n) for v, n in sorted(c.items(), key=lambda x: _key(x[0]), reverse=True)) or "-"


def _count(items, label):
    """'4x 25G, 2x 1G' - how many of each, in order of appearance."""
    seen = {}
    for i in items:
        k = label(i)
        if k:
            seen[k] = seen.get(k, 0) + 1
    return ", ".join("%dx %s" % (n, k) if n > 1 else k for k, n in seen.items())


def _recs(c, key):
    return ((c.get("data") or {}).get(key) or {}).get("records") or []


def _clusters(collected):
    """Each cluster the collector tried, with _name: its name, or its address when it was not read."""
    out = []
    for c in (collected or {}).get("clusters") or []:
        c = dict(c)
        c["_name"] = g((_recs(c, "cluster") or [{}])[0], "name") or c.get("address", "")
        out.append(c)
    return out


def _nodes(cs):
    return [dict(n, _cluster=c["_name"], _c=c) for c in cs if c.get("reachable", True) for n in _recs(c, "nodes")]


def _shelves(cs):
    return [dict(s, _cluster=c["_name"]) for c in cs if c.get("reachable", True) for s in _recs(c, "shelves")]


def _disks(cs):
    return [dict(d, _cluster=c["_name"]) for c in cs if c.get("reachable", True) for d in _recs(c, "disks")]


def _ontap(n):
    return short_version(g(n, "version.full"))


def _fw(n):
    return str(g(n, "controller.cpu.firmware_release") or "")


def _sp(n):
    """'BMC 13.10' - the service processor's kind and firmware."""
    kind, fw = g(n, "service_processor.type"), g(n, "service_processor.firmware_version")
    if not kind and not fw:
        return ""
    return " ".join(x for x in (SP.get(str(kind or "").lower(), str(kind or "").upper()), str(fw or "")) if x)


def _cpu(n):
    proc, count = g(n, "controller.cpu.processor"), g(n, "controller.cpu.count")
    if not proc and not count:
        return ""
    return "%s (%s CPUs)" % (proc or "CPU", count) if count else str(proc)


def _battery(n):
    b = str(g(n, "nvram.battery_state") or "")
    return BATTERY.get(b, b.replace("battery_", "").replace("_", " "))


def _flash(n):
    return _count(g(n, "controller.flash_cache") or [], lambda f: " ".join(
        x for x in (str(f.get("model") or ""), human_bytes(f.get("capacity"))) if x) or "module")


def _speed_eth(p):
    try:
        mb = int(p.get("speed") or 0)
    except (TypeError, ValueError):
        mb = 0
    return ("%gG" % (mb / 1000.0) if mb >= 1000 else "%dM" % mb) if mb else "down"


def _speed_fc(p):
    s = g(p, "speed.maximum")
    return ("%sG FC" % s) if s not in (None, "", "auto") else "FC"


def _ports(c, node):
    """'4x 25G, 2x 1G, 1x down; 4x 32G FC' - the node's physical Ethernet ports by speed, then its FC ports."""
    eth = sorted([p for p in _recs(c, "ports") if g(p, "node.name") == node and p.get("type", "physical") == "physical"],
                 key=lambda p: -int(p.get("speed") or 0) if str(p.get("speed") or 0).isdigit() else 0)   # the fastest first, down last
    fc = [p for p in _recs(c, "fc_ports") if g(p, "node.name") == node]
    return "; ".join(x for x in (_count(eth, _speed_eth), _count(fc, _speed_fc)) if x)


def _node_up(n):
    return n.get("state", "up") == "up"


def _models(nodes):
    """[(model, [nodes])], the most controllers first."""
    m = {}
    for n in nodes:
        m.setdefault(n.get("model") or "-", []).append(n)
    return sorted(m.items(), key=lambda x: (-len(x[1]), x[0]))


def _shelf_name(s):
    return str(s.get("name") or s.get("id") or s.get("uid") or "")


def _shelf_fw(s):
    return _per(s.get("modules") or [], lambda m: str(m.get("firmware_version") or ""))


def _disk_groups(disks):
    """[((cluster, vendor, model, type, size), [disks])] - by cluster, then the most disks first."""
    gr = {}
    for d in disks:
        gr.setdefault((d["_cluster"], str(d.get("vendor") or "").strip(), str(d.get("model") or "").strip() or "-",
                       str(d.get("type") or "").upper(), human_bytes(d.get("physical_size"))), []).append(d)
    return sorted(gr.items(), key=lambda x: (x[0][0].lower(), -len(x[1]), x[0][1:]))


def _notes(cs):
    """What could not be read, one line per cluster and table."""
    out = []
    for c in cs:
        if not c.get("reachable", True):
            continue
        for k, d in sorted((c.get("data") or {}).items()):
            d = d or {}
            if d.get("error"):
                out.append("%s: could not read %s - %s" % (c["_name"], k, d["error"]))
            elif d.get("reduced"):
                out.append("%s: %s - %s" % (c["_name"], k, d["reduced"]))
            if d.get("truncated"):
                out.append("%s: %s - only the first %d records were read" % (c["_name"], k, len(d.get("records") or [])))
    return out


def ontap_hardware_doc(collected, opts=None):
    """The report: {title, status, subtitle, summary, sections}."""
    o = _o(opts)
    cs = _clusters(collected)
    bad = [c for c in cs if not c.get("reachable", True)]
    ok = [c for c in cs if c.get("reachable", True)]
    nodes, shelves, disks = _nodes(cs), _shelves(cs), _disks(cs)
    down = [n for n in nodes if not _node_up(n)]
    shelf_bad = [s for s in shelves if s.get("state") and s.get("state") != "ok"]
    models = _models(nodes)
    sections = []
    if bad:
        sections.append({"title": "Clusters not read", "status": "critical",
                         "lines": ["%s: %s" % (c.get("address", ""), c.get("error", "")) for c in bad],
                         "text": "Their hardware is not in this report: the counts below are of the others."})
    if down:
        sections.append({"title": "Controllers not up", "status": "warning",
                         "text": "What the cluster last knew of them (the NetApp ONTAP health report says more).",
                         "columns": ["Cluster", "Controller", "Model", "Serial", "State"],
                         "rows": [[n["_cluster"], n.get("name", ""), n.get("model") or "-", n.get("serial_number") or "-",
                                   n.get("state", "")] for n in down], "row_status": ["warning"] * len(down)})
    sections.append({"title": "Models",
                     "text": "Each controller model, how many controllers, and the ONTAP, system firmware and service "
                             "processor (SP / BMC) versions they run (the newest first). One model on several firmware "
                             "versions: some controllers were left behind by an update.",
                     "columns": ["Model", "Controllers", "ONTAP (controllers)", "System firmware (controllers)", "SP / BMC (controllers)"],
                     "rows": [[m, len(ns), _per(ns, _ontap), _per(ns, _fw), _per(ns, _sp)] for m, ns in models]})
    part = nodes[:o["max_rows"]]
    sec = {"title": "Controllers",
           "columns": ["Cluster", "Controller", "Model", "Serial", "System ID", "ONTAP", "HA partner", "CPU", "Memory",
                       "System firmware", "SP / BMC", "NVRAM battery", "Flash Cache", "Ports", "Up", "State"],
           "rows": [[n["_cluster"], n.get("name", ""), n.get("model") or "-", n.get("serial_number") or "-", n.get("system_id") or "-",
                     _ontap(n) or "-", ", ".join(p.get("name", "") for p in g(n, "ha.partners") or []) or "-", _cpu(n) or "-",
                     human_bytes(g(n, "controller.memory_size")) or "-", _fw(n) or "-", _sp(n) or "-", _battery(n) or "-",
                     _flash(n) or "-", _ports(n["_c"], n.get("name")) or "-", uptime_text(n.get("uptime")) or "-",
                     n.get("state") or "-"] for n in part],
           "row_status": ["warning" if not _node_up(n) else "" for n in part],
           "cell_status": [[""] * 11 + ["warning" if str(g(n, "nvram.battery_state") or "") not in BATTERY_OK else ""] + [""] * 4
                           for n in part],
           "text": "The workbook attached has every controller, shelf, shelf module, disk and port."}
    if len(nodes) > len(part):
        sec["text"] += " %d more not listed (ontap_hardware_max_rows: %d)." % (len(nodes) - len(part), o["max_rows"])
    sections.append(sec)
    if shelves:
        part = shelves[:o["max_rows"]]
        sec = {"title": "Shelves",
               "columns": ["Cluster", "Shelf", "Model", "Serial", "Modules", "Module firmware (modules)", "Connection", "Disks", "State"],
               "rows": [[s["_cluster"], _shelf_name(s), s.get("model") or "-", s.get("serial_number") or "-",
                         s.get("module_type") or "-", _shelf_fw(s), s.get("connection_type") or "-",
                         s.get("disk_count") if s.get("disk_count") is not None else "-", s.get("state") or "-"] for s in part],
               "row_status": ["warning" if s in shelf_bad else "" for s in part]}
        if len(shelves) > len(part):
            sec["text"] = "%d more not listed (ontap_hardware_max_rows: %d)." % (len(shelves) - len(part), o["max_rows"])
        sections.append(sec)
    groups = _disk_groups(disks)
    if groups:
        part = groups[:o["max_rows"]]
        sec = {"title": "Disks by model",
               "text": "Each disk model of each cluster, how many, how many are spares, and the firmware versions they run "
                       "(the newest first). One model on several firmware versions: the disk qualification package or a "
                       "disk firmware update did not reach them all.",
               "columns": ["Cluster", "Vendor", "Model", "Type", "Size", "Disks", "Spares", "Firmware (disks)"],
               "rows": [[k[0], k[1] or "-", k[2], k[3] or "-", k[4] or "-", len(ds),
                         len([d for d in ds if d.get("container_type") == "spare"]),
                         _per(ds, lambda d: str(d.get("firmware_version") or ""))] for k, ds in part]}
        if len(groups) > len(part):
            sec["text"] += " %d more not listed (ontap_hardware_max_rows: %d)." % (len(groups) - len(part), o["max_rows"])
        sections.append(sec)
    notes = _notes(cs)
    if notes:
        sections.append({"title": "Not all read", "text": "What could not be read (the rest of each cluster is in).", "lines": notes})
    serials = len([n for n in nodes if n.get("serial_number")])
    title = "NetApp hardware: %d controller%s, %d model%s, %d shel%s, %d disk%s" % (
        len(nodes), "" if len(nodes) == 1 else "s", len(models), "" if len(models) == 1 else "s",
        len(shelves), "f" if len(shelves) == 1 else "ves", len(disks), "" if len(disks) == 1 else "s")
    if down:
        title += " - %d not up" % len(down)
    if bad:
        title += " - %d cluster%s not read" % (len(bad), "" if len(bad) == 1 else "s")
    summary = [{"label": "Clusters", "value": len(ok)}, {"label": "Controllers", "value": len(nodes)},
               {"label": "Models", "value": len(models)},
               {"label": "ONTAP versions", "value": len(set(_ontap(n) for n in nodes if _ontap(n)))},
               {"label": "Shelves", "value": len(shelves)}, {"label": "Disks", "value": len(disks)},
               {"label": "With a serial number", "value": "%d of %d" % (serials, len(nodes))}]
    if down:
        summary.append({"label": "Not up", "value": len(down), "status": "warning"})
    if shelf_bad:
        summary.append({"label": "Shelves not ok", "value": len(shelf_bad), "status": "warning"})
    if bad:
        summary.append({"label": "Clusters not read", "value": len(bad), "status": "critical"})
    return {"title": title, "status": "critical" if bad else ("warning" if down or shelf_bad else "ok"),
            "subtitle": "Every controller, shelf and disk, as ONTAP reports them - %s" % (
                ", ".join(c["_name"] for c in ok) or "no cluster read"),
            "summary": summary, "sections": sections}


def ontap_hardware_xlsx(collected):
    """The email's workbook (roles/site_email/library/site_xlsx.py): clusters, controllers, shelves, shelf modules,
    disks, Ethernet ports, FC ports - a row each."""
    cs = _clusters(collected)
    ok = [c for c in cs if c.get("reachable", True)]
    nodes, shelves, disks = _nodes(cs), _shelves(cs), _disks(cs)
    shelf_of = dict(((s["_cluster"], s.get("uid")), _shelf_name(s)) for s in shelves if s.get("uid"))

    def num(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return ""
    return [
        {"name": "Clusters", "columns": ["Cluster", "Address", "ONTAP", "Location", "Contact", "Controllers", "Shelves", "Disks", "Read"],
         "rows": [[c["_name"], c.get("address", ""), short_version(g((_recs(c, "cluster") or [{}])[0], "version.full")),
                   g((_recs(c, "cluster") or [{}])[0], "location", ""), g((_recs(c, "cluster") or [{}])[0], "contact", ""),
                   len(_recs(c, "nodes")), len(_recs(c, "shelves")), len(_recs(c, "disks")),
                   "yes" if c.get("reachable", True) else "NO: " + c.get("error", "")] for c in cs]},
        {"name": "Controllers",
         "columns": ["Cluster", "Controller", "Model", "Serial", "Vendor serial", "System ID", "ONTAP", "Location", "HA partner",
                     "Board", "CPU", "CPUs", "Memory", "System firmware", "SP / BMC", "SP / BMC firmware", "SP / BMC address",
                     "SP / BMC state", "NVRAM battery", "Flash Cache", "Ports", "Up", "State"],
         "rows": [[n["_cluster"], n.get("name", ""), n.get("model", ""), n.get("serial_number", ""), n.get("vendor_serial_number", ""),
                   n.get("system_id", ""), _ontap(n), n.get("location", ""),
                   ", ".join(p.get("name", "") for p in g(n, "ha.partners") or []), g(n, "controller.board", ""),
                   g(n, "controller.cpu.processor", ""), num(g(n, "controller.cpu.count")), human_bytes(g(n, "controller.memory_size")),
                   _fw(n), SP.get(str(g(n, "service_processor.type", "")).lower(), str(g(n, "service_processor.type", "")).upper()),
                   g(n, "service_processor.firmware_version", ""), g(n, "service_processor.ipv4_interface.address", ""),
                   g(n, "service_processor.state", ""), _battery(n), _flash(n), _ports(n["_c"], n.get("name")),
                   uptime_text(n.get("uptime")), n.get("state", "")] for n in nodes]},
        {"name": "Shelves", "columns": ["Cluster", "Shelf", "Model", "Serial", "Modules", "Module firmware", "Connection", "Internal",
                                        "Disks", "State"],
         "rows": [[s["_cluster"], _shelf_name(s), s.get("model", ""), s.get("serial_number", ""), s.get("module_type", ""),
                   _shelf_fw(s), s.get("connection_type", ""), "yes" if s.get("internal") else "", num(s.get("disk_count")),
                   s.get("state", "")] for s in shelves]},
        {"name": "Shelf modules", "columns": ["Cluster", "Shelf", "Module", "Part number", "Serial", "Firmware", "State"],
         "rows": [[s["_cluster"], _shelf_name(s), str(m.get("id", "")), m.get("part_number", ""), m.get("serial_number", ""),
                   m.get("firmware_version", ""), m.get("state", "")] for s in shelves for m in s.get("modules") or []]},
        {"name": "Disks", "columns": ["Cluster", "Disk", "Owner", "Home", "Shelf", "Bay", "Vendor", "Model", "Serial", "Firmware",
                                      "Type", "Class", "Size", "Usable", "RPM", "Container", "State"],
         "rows": [[d["_cluster"], d.get("name", ""), g(d, "node.name", ""), g(d, "home_node.name", ""),
                   shelf_of.get((d["_cluster"], g(d, "shelf.uid")), ""), num(d.get("bay")), str(d.get("vendor") or "").strip(),
                   str(d.get("model") or "").strip(), d.get("serial_number", ""), d.get("firmware_version", ""), d.get("type", ""),
                   d.get("class", ""), human_bytes(d.get("physical_size")), human_bytes(d.get("usable_size")), num(d.get("rpm")),
                   d.get("container_type", ""), d.get("state", "")] for d in disks]},
        {"name": "Ethernet ports", "columns": ["Cluster", "Controller", "Port", "Type", "Speed", "State", "MAC", "Broadcast domain"],
         "rows": [[c["_name"], g(p, "node.name", ""), p.get("name", ""), p.get("type", ""), _speed_eth(p), p.get("state", ""),
                   p.get("mac_address", ""), g(p, "broadcast_domain.name", "")] for c in ok for p in _recs(c, "ports")]},
        {"name": "FC ports", "columns": ["Cluster", "Controller", "Port", "WWPN", "Protocol", "Speed (max, Gb/s)", "Speed (set)",
                                         "State", "Transceiver", "Transceiver maker", "Transceiver part"],
         "rows": [[c["_name"], g(p, "node.name", ""), p.get("name", ""), p.get("wwpn", ""), p.get("physical_protocol", ""),
                   str(g(p, "speed.maximum", "")), str(g(p, "speed.configured", "")), p.get("state", ""),
                   g(p, "transceiver.form_factor", ""), g(p, "transceiver.manufacturer", ""), g(p, "transceiver.part_number", "")]
                  for c in ok for p in _recs(c, "fc_ports")]}]


def ontap_hardware_lines(collected):
    """For the job output: a line per controller model, a line per cluster, then the clusters not read and what could
    not be read."""
    cs = _clusters(collected)
    nodes = _nodes(cs)
    return (["%s: %d controller(s); ONTAP %s; firmware %s; SP / BMC %s" % (m, len(ns), _per(ns, _ontap), _per(ns, _fw), _per(ns, _sp))
             for m, ns in _models(nodes)]
            + ["%s: %d controller(s), %d shel%s, %d disk(s)" % (c["_name"], len(_recs(c, "nodes")), len(_recs(c, "shelves")),
                                                             "f" if len(_recs(c, "shelves")) == 1 else "ves", len(_recs(c, "disks")))
               for c in cs if c.get("reachable", True)]
            + ["NOT READ - %s: %s" % (c.get("address", ""), c.get("error", "")) for c in cs if not c.get("reachable", True)]
            + ["Not all read - " + x for x in _notes(cs)])


def ontap_hardware_models(collected):
    """For the job's artifact: [{model, controllers, ontap, firmware}]."""
    return [{"model": m, "controllers": len(ns), "ontap": _per(ns, _ontap), "firmware": _per(ns, _fw)}
            for m, ns in _models(_nodes(_clusters(collected)))]


class FilterModule(object):
    def filters(self):
        return {"ontap_hardware_doc": ontap_hardware_doc, "ontap_hardware_xlsx": ontap_hardware_xlsx,
                "ontap_hardware_lines": ontap_hardware_lines, "ontap_hardware_models": ontap_hardware_models}
