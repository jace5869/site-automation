# -*- coding: utf-8 -*-
"""The version and build of each vCenter and every ESXi host (roles/vmware_vm/library/site_vmware_versions.py) -> the
VMware version report (playbooks/vmware_version_report.yml): vm_versions_doc - each vCenter, the ESXi builds in use, then
every host by vCenter, datacenter and cluster. A report: green, amber only for an ESXi host at a newer version than the
vCenter that manages it (vCenter must be at its hosts' version or newer), red only for a vCenter that could not be read.
Only what vCenter says - no table of update names or support dates, which would go out of date."""
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


def version_key(v):
    """'8.0.3' -> (8, 0, 3, 0): numbers only, padded, so 8.0 and 8.0.0 are the same; '' -> ()."""
    nums = [int(x) for x in re.findall(r"\d+", str(v or ""))[:4]]
    return tuple(nums + [0] * (4 - len(nums))) if nums else ()


def vm_version_newer(host_version, vcenter_version):
    """True when the host's version is newer than its vCenter's (unknown on either side: False)."""
    h, v = version_key(host_version), version_key(vcenter_version)
    return bool(h and v and h > v)


def _vb(version, build):
    return ("%s build %s" % (version, build) if build else version) if version else "-"


def _builds(hosts):
    """[(version, build, [hosts])], the newest first; hosts with no version left out."""
    g = {}
    for h in hosts:
        if h.get("version"):
            g.setdefault((h["version"], h.get("build", "")), []).append(h)
    return sorted(((k[0], k[1], v) for k, v in g.items()),
                  key=lambda x: (version_key(x[0]), int(x[1]) if str(x[1]).isdigit() else 0), reverse=True)


def _mixed(hosts):
    """{(vcenter, datacenter, cluster): number of builds} of the clusters whose hosts run more than one build."""
    g = {}
    for h in hosts:
        if h.get("cluster") and h.get("version"):
            g.setdefault((h.get("vcenter", ""), h.get("datacenter", ""), h["cluster"]), set()).add((h["version"], h.get("build", "")))
    return {k: len(v) for k, v in g.items() if len(v) > 1}


def _state(h):
    s = h.get("state") or ""
    s = "" if s == "connected" else ("not responding" if s == "notResponding" else s)
    return ", ".join(x for x in (s, "maintenance mode" if h.get("maintenance") else "") if x) or "connected"


def vm_versions_doc(vcenter_info, hosts, vcenters=None, opts=None):
    """The report: {title, status, subtitle, summary, sections}."""
    o = _o(opts)
    hosts = list(hosts or [])
    vcs = {i.get("vcenter", ""): i for i in vcenter_info or []}
    bad = [s for s in vcenters or [] if s.get("error")]
    read = [i.get("vcenter", "") for i in vcenter_info or []]
    newer = [h for h in hosts if vm_version_newer(h.get("version"), (vcs.get(h.get("vcenter", "")) or {}).get("version"))]
    unknown = [h for h in hosts if not h.get("version")]
    builds = _builds(hosts)
    versions = sorted(set(h["version"] for h in hosts if h.get("version")), key=version_key, reverse=True)
    mixed = _mixed(hosts)

    sections = []
    if bad:
        sections.append({"title": "vCenters not read", "status": "critical",
                         "lines": [s.get("error") or s.get("name", "") for s in bad],
                         "text": "Their hosts are not in this report: the counts below are of the others."})
    if newer:
        sections.append({"title": "ESXi newer than its vCenter", "status": "warning",
                         "text": "vCenter must be at the version of the hosts it manages or newer: update vCenter before these "
                                 "hosts' next update, and check VMware's interoperability matrix for the builds.",
                         "columns": ["vCenter", "vCenter version", "ESXi host", "Cluster", "ESXi version"],
                         "rows": [[_short(h.get("vcenter")), _vb(vcs[h["vcenter"]].get("version"), vcs[h["vcenter"]].get("build")),
                                   h.get("name", ""), h.get("cluster") or "(standalone)", _vb(h.get("version"), h.get("build"))]
                                  for h in sorted(newer, key=lambda h: (h.get("vcenter", ""), h.get("name", "")))],
                         "row_status": ["warning"] * len(newer)})
    vrows = []
    for name in read:
        i, mine = vcs[name], [h for h in hosts if h.get("vcenter") == name]
        per = ", ".join("%s (%d)" % (v, len([h for h in mine if h.get("version") == v]))
                        for v in sorted(set(h["version"] for h in mine if h.get("version")), key=version_key, reverse=True))
        vrows.append([name, i.get("version") or "-", i.get("build") or "-", i.get("product") or "-", len(mine), per or "-"])
    sections.append({"title": "vCenters", "columns": ["vCenter", "Version", "Build", "Product", "ESXi hosts", "ESXi versions (hosts)"],
                     "rows": vrows})
    if builds:
        sections.append({"title": "ESXi builds in use",
                         "text": "Each version and build, the newest first, and how many hosts run it.",
                         "columns": ["Version", "Build", "Hosts", "Product", "vCenters", "Clusters"],
                         "rows": [[v, b or "-", len(hs), hs[0].get("product") or "-",
                                   ", ".join(sorted(set(_short(h.get("vcenter")) for h in hs))),
                                   ", ".join(sorted(set(h.get("cluster") or "(standalone)" for h in hs)))[:300]]
                                  for v, b, hs in builds]})
    new_ids = set(id(h) for h in newer)
    shown, multi_dc = 0, len(set((h.get("vcenter"), h.get("datacenter")) for h in hosts)) > len(read)
    for name in read:
        mine = sorted([h for h in hosts if h.get("vcenter") == name],
                      key=lambda h: (str(h.get("datacenter", "")).lower(), str(h.get("cluster", "")).lower(), str(h.get("name", "")).lower()))
        if not mine:
            continue
        room = max(o["max_rows"] - shown, 0)
        part = mine[:room]
        rows, rs, cs = [], [], []
        for h in part:
            nw = id(h) in new_ids
            rows.append([h.get("datacenter") or "-", h.get("cluster") or "(standalone)", h.get("name", ""), h.get("version") or "-",
                         h.get("build") or "-", _state(h)])
            rs.append("warning" if nw else "")
            cs.append(["", "", "", "warning" if nw else "", "", "" if h.get("state") in ("connected", None, "") else "warning"])
        lines = ["%s: %d builds - %s" % (k[2] if not multi_dc else "%s/%s" % (k[1], k[2]), n, ", ".join(
                     "%s (%d)" % (_vb(v, b), len(hs)) for v, b, hs in _builds([h for h in mine if (h.get("datacenter"), h.get("cluster")) == k[1:]])))
                 for k, n in sorted(mixed.items()) if k[0] == name]
        sec = {"title": "%s - ESXi hosts" % _short(name), "columns": ["Datacenter", "Cluster", "ESXi host", "Version", "Build", "State"],
               "rows": rows, "row_status": rs, "cell_status": cs}
        text = []
        if lines:
            text.append("Clusters whose hosts run more than one build (an update in progress, or a host left behind): "
                        + "; ".join(lines) + ".")
        if len(mine) > room:
            text.append("%d more not listed (vm_versions_max_rows: %d); the workbook has every host." % (len(mine) - room, o["max_rows"]))
        if text:
            sec["text"] = " ".join(text)
        shown += len(part)
        sections.append(sec)

    title = "VMware versions: %d vCenter%s, %d ESXi host%s on %d build%s" % (
        len(read), "" if len(read) == 1 else "s", len(hosts), "" if len(hosts) == 1 else "s", len(builds), "" if len(builds) == 1 else "s")
    if newer:
        title += " - %d newer than their vCenter" % len(newer)
    if bad:
        title += " - %d vCenter%s not read" % (len(bad), "" if len(bad) == 1 else "s")
    summary = [{"label": "vCenters", "value": len(read)}, {"label": "ESXi hosts", "value": len(hosts)},
               {"label": "ESXi versions", "value": len(versions)}, {"label": "ESXi builds", "value": len(builds)},
               {"label": "Clusters on more than one build", "value": len(mixed)}]
    if newer:
        summary.append({"label": "ESXi newer than its vCenter", "value": len(newer), "status": "warning"})
    if unknown:
        summary.append({"label": "Version not known (not connected)", "value": len(unknown)})
    if bad:
        summary.append({"label": "Not read", "value": len(bad), "status": "critical"})
    return {"title": title, "status": "critical" if bad else ("warning" if newer else "ok"),
            "subtitle": "The version and build of each vCenter and every ESXi host, as vCenter reports them - %s" % (
                ", ".join(_short(n) for n in read) or "no vCenter read"),
            "summary": summary, "sections": sections}


def vm_versions_lines(vcenter_info, hosts):
    """For the job output: a line per vCenter - its version and build, and its hosts' builds."""
    out = []
    for i in vcenter_info or []:
        mine = [h for h in hosts or [] if h.get("vcenter") == i.get("vcenter")]
        esxi = ", ".join("%s (%d)" % (_vb(v, b), len(hs)) for v, b, hs in _builds(mine))
        out.append("%s: vCenter %s; %s" % (i.get("vcenter", ""), _vb(i.get("version"), i.get("build")),
                                            "ESXi " + esxi if esxi else "no ESXi hosts"))
    return out


def vm_versions_builds(hosts):
    """For the job's artifact: [{version, build, hosts}], the newest first."""
    return [{"version": v, "build": b, "hosts": len(hs)} for v, b, hs in _builds(hosts or [])]


def vm_versions_xlsx(vcenter_info, hosts, opts=None):
    """The email's workbook (roles/site_email/library/site_xlsx.py): a sheet of vCenters, a sheet of every ESXi host."""
    vcs = {i.get("vcenter", ""): i for i in vcenter_info or []}
    hs = sorted(hosts or [], key=lambda h: (str(h.get("vcenter", "")).lower(), str(h.get("datacenter", "")).lower(),
                                            str(h.get("cluster", "")).lower(), str(h.get("name", "")).lower()))
    return [{"name": "vCenters", "columns": ["vCenter", "Version", "Build", "Product", "API version"],
             "rows": [[i.get("vcenter", ""), i.get("version", ""), i.get("build", ""), i.get("product", ""), i.get("api", "")]
                      for i in vcenter_info or []]},
            {"name": "ESXi hosts",
             "columns": ["vCenter", "Datacenter", "Cluster", "ESXi host", "Version", "Build", "Product", "State", "Maintenance mode",
                         "Newer than its vCenter"],
             "rows": [[h.get("vcenter", ""), h.get("datacenter", ""), h.get("cluster", ""), h.get("name", ""), h.get("version", ""),
                       h.get("build", ""), h.get("product", ""), h.get("state", ""), "yes" if h.get("maintenance") else "",
                       "yes" if vm_version_newer(h.get("version"), (vcs.get(h.get("vcenter", "")) or {}).get("version")) else ""]
                      for h in hs]}]


class FilterModule(object):
    def filters(self):
        return {"vm_versions_doc": vm_versions_doc, "vm_versions_xlsx": vm_versions_xlsx, "vm_versions_lines": vm_versions_lines,
                "vm_versions_builds": vm_versions_builds, "vm_version_newer": vm_version_newer}
