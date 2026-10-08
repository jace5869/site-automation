# -*- coding: utf-8 -*-
"""Filters for the ESXi security settings job (roles/vmware_vm/tasks/esxi_security.yml): what
roles/vmware_vm/library/site_vmware_esxi_security.py returns, as a report in the layout
roles/site_email renders (rows coloured by result)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

def _dc(h, multi):
    """The Datacenter cell: 'DC1', or with several vCenters 'vc02 / DC1'."""
    vc = str(h.get("vcenter") or "")
    host = vc.split(":")[0]
    short = vc if host.replace(".", "").isdigit() else host.split(".")[0] + vc[len(host):]
    return ("%s / %s" % (short, h.get("datacenter", ""))) if multi and vc else h.get("datacenter", "")


ROW = {"failed": "critical", "changed": "warning", "skipped": "unknown", "excluded": "", "ok": ""}
CELL = {"failed": "critical", "changed": "warning", "skipped": "unknown", "excluded": "", "ok": "ok"}


def esxi_wanted(opts):
    """The wanted settings in one line: 'SSH disabled; UserVars.ESXiShellTimeOut = 600; lockdown normal'."""
    o = opts or {}
    parts = []
    for key, label in (("ssh", "SSH"), ("shell", "ESXi Shell")):
        if o.get(key):
            parts.append("%s %s" % (label, o[key]))
    parts += ["%s = %s" % (k, v) for k, v in sorted((o.get("settings") or {}).items())]
    if o.get("lockdown"):
        parts.append("lockdown " + o["lockdown"])
    if o.get("exception_users"):
        parts.append("exception users " + ", ".join(o["exception_users"]))
    return "; ".join(parts) or "nothing"


def esxi_security_report(hosts, opts=None):
    """The job's result as a report: failed first, then changed, skipped, and every host."""
    o, hs = opts or {}, hosts or []
    dry = bool(o.get("check_mode"))
    multi = bool(o.get("multi"))
    by = {k: [h for h in hs if h.get("status") == k] for k in ("failed", "changed", "ok", "skipped", "excluded")}
    acted = len(hs) - len(by["excluded"])
    verb = "would change" if dry else "changed"
    if by["failed"] or by["changed"]:
        title = "ESXi security settings%s: %d of %d host(s) %s" % (" (dry run)" if dry else "", len(by["changed"]), acted, verb)
        if by["failed"]:
            title += ", %d failed" % len(by["failed"])
    else:
        title = "ESXi security settings%s: all %d host(s) already set" % (" (dry run)" if dry else "", acted - len(by["skipped"]))
    if by["skipped"]:
        title += ", %d skipped (not connected)" % len(by["skipped"])
    status = "critical" if by["failed"] else ("warning" if by["changed"] else "ok")
    sections = []
    if by["failed"]:
        sections.append({"title": "Failed", "status": "critical", "columns": ["Host", "Cluster", "Error"],
                         "rows": [[h["name"], h.get("cluster", ""), "; ".join(h.get("errors") or [])] for h in by["failed"]],
                         "row_status": ["critical"] * len(by["failed"]), "cell_status": [["critical", "", ""]] * len(by["failed"]),
                         "text": "These hosts are not (fully) set. A missing privilege is named: grant it to the vCenter account "
                                 "(docs/VMWARE.md, ESXi security settings)."})
    if by["changed"]:
        sections.append({"title": "Would change" if dry else "Changed", "status": "warning", "columns": ["Host", "Cluster", "What"],
                         "rows": [[h["name"], h.get("cluster", ""), "; ".join(h.get("changes") or [])] for h in by["changed"]],
                         "row_status": ["warning"] * len(by["changed"]), "cell_status": [["warning", "", ""]] * len(by["changed"]),
                         "text": ("A real run would change these." if dry else
                                  "These had drifted (someone turned SSH on, changed a timeout or left lockdown) and were set again.")})
    if by["skipped"] or by["excluded"]:
        rows = [[h["name"], h.get("cluster", ""), "; ".join(h.get("notes") or [])] for h in by["skipped"] + by["excluded"]]
        sections.append({"title": "Not touched", "columns": ["Host", "Cluster", "Why"], "rows": rows})
    keys = []
    for h in hs:
        for k in (h.get("before") or {}):
            if k not in keys:
                keys.append(k)
    rows, rs, cs = [], [], []
    for h in hs:
        st = h.get("status", "")
        notes = (h.get("changes") or []) + (h.get("notes") or []) + (h.get("errors") or [])
        rows.append([h["name"], h.get("cluster", ""), _dc(h, multi), st.upper()]
                    + [h.get("before", {}).get(k, "") for k in keys] + ["; ".join(notes)])
        rs.append(ROW.get(st, ""))
        cs.append(["", "", "", CELL.get(st, "")] + [""] * (len(keys) + 1))
    sections.append({"title": "All hosts", "columns": ["Host", "Cluster", "Datacenter", "Result"] + ["%s (before)" % k for k in keys]
                     + ["Notes"], "rows": rows, "row_status": rs, "cell_status": cs,
                     "text": "The values before this run. Result OK = already set; CHANGED = set by this run%s." % (
                         " (would be)" if dry else "")})
    return {"title": title, "status": status,
            "subtitle": "Wanted: %s%s" % (esxi_wanted(o), " - DRY RUN: nothing was changed" if dry else ""),
            "summary": [{"label": "Hosts", "value": acted},
                        {"label": "Would change" if dry else "Changed", "value": len(by["changed"]), "status": "warning" if by["changed"] else "ok"},
                        {"label": "Failed", "value": len(by["failed"]), "status": "critical" if by["failed"] else "ok"},
                        {"label": "Already set", "value": len(by["ok"]), "status": "ok"},
                        {"label": "Skipped (not connected)", "value": len(by["skipped"]), "status": "warning" if by["skipped"] else "ok"}]
                       + ([{"label": "Excluded", "value": len(by["excluded"])}] if by["excluded"] else []),
            "sections": sections}


class FilterModule(object):
    def filters(self):
        return {"esxi_security_report": esxi_security_report, "esxi_wanted": esxi_wanted}
