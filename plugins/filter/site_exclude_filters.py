# -*- coding: utf-8 -*-
"""Leave hosts out of every job (1.67.0 part J): site_exclude_hosts and site_exclude_groups, matched against the
inventory by roles/site_exclude (playbooks/site_exclude.yml, the first play of every playbook that runs on hosts), and
by the VMware and Horizon roles against the VM and ESXi host names they work on.
  site_exclude_match(hosts, names, groups, group_names) -> the hosts left out, sorted
  site_exclude_name(name, names) -> the pattern that leaves this one name out, or ''
  site_exclude_line(hosts, limit=10) -> "Left out by site_exclude_hosts: 3 - a, b, c" (past limit: "... and N more")
A name: the host's whole name, or its short name either way (web01 leaves out web01.example.mil; web01.example.mil
leaves out an inventory host web01), * and ? as wildcards, any case. An address, and a pattern with a wildcard, are never
shortened (*.lab.example.mil never leaves out a host named web10)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import fnmatch
import re

WILD = re.compile(r"[*?\[]")      # a pattern with a wildcard is never shortened (*.lab.example.mil is not *)
IP = re.compile(r"^(\d{1,3}(\.\d{1,3}){3}|[0-9a-f:]*:[0-9a-f:.]*)$", re.I)


def _list(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [x.strip() for x in re.split(r"[,\n]", v) if x.strip()]
    if isinstance(v, dict):
        raise ValueError("a list of names, not a mapping: %r" % (v,))
    return [str(x).strip() for x in v if str(x).strip()]


def _short(name):
    n = str(name)
    return n if IP.match(n) else n.split(".")[0]


def site_exclude_name(name, names):
    """The pattern of names that leaves name out, or '' (none does)."""
    h = str(name or "").strip().lower()
    if not h:
        return ""
    for p in _list(names):
        q = p.lower()
        if fnmatch.fnmatchcase(h, q):
            return p
        if not IP.match(h) and fnmatch.fnmatchcase(_short(h), q):          # web01 leaves out web01.example.mil
            return p
        if "." not in h and not IP.match(q) and not WILD.search(q) and h == _short(q):   # web01.example.mil leaves out web01
            return p
    return ""


def site_exclude_match(hosts, names=None, groups=None, group_names=None):
    """The inventory hosts left out: hosts (groups['all']), names (site_exclude_hosts), groups (the magic var groups),
    group_names (site_exclude_groups: names, * and ?, any case; all and ungrouped never - they would leave out every
    host)."""
    pats = [g.lower() for g in _list(group_names) if g.lower() not in ("all", "ungrouped")]
    by_group = set()
    for g, members in (groups or {}).items():
        if g.lower() in ("all", "ungrouped"):
            continue
        if any(fnmatch.fnmatchcase(g.lower(), p) for p in pats):
            by_group.update(members or [])
    out = [h for h in (hosts or []) if h in by_group or site_exclude_name(h, names)]
    return sorted(set(out), key=lambda x: str(x).lower())


def site_exclude_patterns(names):
    """site_exclude_hosts as wildcard patterns for a list matched by name only (the VMware and Horizon lists: VM, ESXi
    host, desktop names): each name, its short form and the short form with any domain (web01.example.mil -> also web01
    and web01.*); a pattern without a domain also with any domain (db* -> db*.*). An address, and a pattern with a
    domain, stay as they are."""
    out = []
    for p in _list(names):
        forms = [p, p + ".*"] if WILD.search(p) and "." not in p else [p] if IP.match(p) or WILD.search(p) else [p, _short(p), _short(p) + ".*"]
        for f in forms:
            if f not in out:
                out.append(f)
    return out


def site_exclude_line(hosts, limit=10):
    """One muted line for a report: the count and the names, the first limit of them."""
    hs = sorted(set(str(h) for h in hosts or []), key=str.lower)
    if not hs:
        return ""
    shown = ", ".join(hs[:int(limit)]) + (" and %d more" % (len(hs) - int(limit)) if len(hs) > int(limit) else "")
    return "Left out by site_exclude_hosts: %d - %s" % (len(hs), shown)


class FilterModule(object):
    def filters(self):
        return {"site_exclude_match": site_exclude_match, "site_exclude_name": site_exclude_name,
                "site_exclude_line": site_exclude_line, "site_exclude_patterns": site_exclude_patterns}
