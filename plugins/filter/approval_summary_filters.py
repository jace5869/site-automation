# -*- coding: utf-8 -*-
"""1.92.0: what a workflow's check plans to change, in a few lines for the person who approves it - the job artifact
site_approval_summary, written by roles/site_approval_summary for every check of ours that waits for an approval. The site
portal shows it on the Approvals page and on the job page ("What it will change": count chips, then each host's items).

    {title, counts: {changes, hosts, critical, warning}, items: [{host, what, level}]}

items are cut to the first 50 (counts keep the whole), what to one line of 200 characters; level is critical, warning,
low or info (anything else is info). Only these fields: no command output, no secret - each check gives words of its own."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

LEVELS = ("critical", "warning", "low", "info")
ITEMS_MAX = 50
WHAT_MAX = 200


def _line(v, n):
    t = " ".join(str(v if v is not None else "").split())
    return t if len(t) <= n else t[:n - 3].rstrip() + "..."


def site_approval_summary(items, title="", items_max=ITEMS_MAX):
    """[{host, what, level}] (a dict that is not one, or one without what, is left out) -> site_approval_summary."""
    rows = []
    for it in items or []:
        if not isinstance(it, dict) or not _line(it.get("what"), WHAT_MAX):
            continue
        lv = str(it.get("level") or "info").strip().lower()
        rows.append({"host": _line(it.get("host") or "", 120), "what": _line(it.get("what"), WHAT_MAX), "level": lv if lv in LEVELS else "info"})
    return {"title": _line(title, 160),
            "counts": {"changes": len(rows), "hosts": len(set(r["host"] for r in rows if r["host"])),
                       "critical": len([r for r in rows if r["level"] == "critical"]),
                       "warning": len([r for r in rows if r["level"] == "warning"])},
            "items": rows[:max(0, int(items_max))]}


class _Keep(dict):
    def __missing__(self, key):
        return "?"


def site_approval_items(plan, level="warning", fmt=""):
    """A plan as the checks hand it on - {host: [line, ...]} - -> items for site_approval_summary, host by host in the plan's
    order. A line is text, or a dict: its what and level, or (fmt) its fields in words - fmt "Delete {files} old file(s) in
    {path}" (a field it does not have is ?)."""
    out = []
    for host, lines in (plan or {}).items() if isinstance(plan, dict) else []:
        for ln in lines if isinstance(lines, (list, tuple)) else [lines]:
            if isinstance(ln, dict):
                what = fmt.format_map(_Keep(ln)) if fmt else str(ln.get("what") or "")
                out.append({"host": host, "what": what, "level": ln.get("level") or level})
            elif str(ln or "").strip():
                out.append({"host": host, "what": str(ln), "level": level})
    return out


class FilterModule(object):
    def filters(self):
        return {"site_approval_summary": site_approval_summary, "site_approval_items": site_approval_items}
