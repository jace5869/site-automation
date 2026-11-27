# -*- coding: utf-8 -*-
"""Findings with stable ids, for the site portal's buttons (1.67.0 part C, docs/SITE_PORTAL.md, "From a finding to a fix").

A report job keeps its findings as the job artifact site_findings - {host: [entry]} - and the portal's fix jobs read
them back by reference (roles/finding_ref): the report job's id and the finding's id, never a value a person typed.

  entry = {id, kind, host, title, severity, data}
  id    = <kind>:<host>:<key>  - only [A-Za-z0-9_.:/@-], at most 200; stable across runs: the key is the natural one
          (/var, httpd, SV-257777r925318_rule), or h + 16 hex of its sha256 when it holds other characters or is long
  data  = what a fix needs (numbers, names), each string at most 300 characters

Kinds: disk_full, disk_inodes, service_down, fapolicyd_deny, stig_rule_fail, stig_fixed_open, stig_no_asset; every other
finding of a health check keeps a generic kind - its old id's prefix, - made _ (mount_full, cert, eventlog ...).
Each host keeps at most max_per_kind entries of a kind (the worst first); the rest are counted (site_findings_capped).

The buttons: an action of the site portal (roles/site_portal_stack/files/actions.yml) with kinds: [...] is offered on
each finding of those kinds (and, with when:, only when the finding's data match), as a link to the portal's confirm page
with target, report_job and finding_id - the link never launches anything. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import hashlib
import re

try:
    from urllib.parse import quote, urlencode
except ImportError:                                  # pragma: no cover - Python 2
    from urllib import quote, urlencode

ID_CHARS = re.compile(r"^[A-Za-z0-9_.:/@-]+$")
KEY_MAX = 120
ID_MAX = 200
DATA_MAX = 300
SEVERITIES = ("critical", "warning", "low", "info")
SEV_ORDER = {"critical": 0, "warning": 1, "low": 2, "info": 3}
NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
CAT = {"high": "CAT I", "medium": "CAT II", "low": "CAT III"}
# an old health-check id's prefix -> its kind
OLD_KIND = {"disk": "disk_full", "disk-inodes": "disk_inodes", "unit-failed": "service_down", "unit-down": "service_down",
            "services": "service_down"}


def finding_key(raw):
    """A key as it may stand in an id: as it is when it is short and of the allowed characters, else h + 16 hex."""
    raw = str(raw if raw is not None else "")
    if raw and len(raw) <= KEY_MAX and ID_CHARS.match(raw):
        return raw
    return "h" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def finding_id(kind, host, key):
    """<kind>:<host>:<key> - a host name of other characters (never in an inventory name) is hashed like a key."""
    host = str(host or "")
    h = host if host and len(host) <= 70 and ID_CHARS.match(host) and ":" not in host else finding_key(host)
    return "%s:%s:%s" % (kind, h, finding_key(key))


def _clip(data):
    """data with short values: strings cut to DATA_MAX, numbers kept, anything else as text."""
    out = {}
    for k, v in (data or {}).items():
        if v is None:
            continue
        if isinstance(v, bool) or isinstance(v, (int, float)):
            out[str(k)] = v
        else:
            out[str(k)] = str(v)[:DATA_MAX]
    return out


def entry(kind, host, key, title, severity, data=None):
    sev = severity if severity in SEVERITIES else "warning"
    return {"id": finding_id(kind, host, key), "kind": kind, "host": str(host), "title": str(title or "")[:DATA_MAX],
            "severity": sev, "data": _clip(data)}


def _pct(text):
    m = re.search(r"(\d+(?:\.\d+)?)% full", str(text or ""))
    return float(m.group(1)) if m else None


def health_entries(f, host, os_):
    """One health-check finding -> its entries (most one; fapolicyd:denials one per denied program and path too)."""
    fid = str(f.get("id") or "")
    prefix, _, rest = fid.partition(":")
    d = dict(f.get("data") or {}) if isinstance(f.get("data"), dict) else {}
    sev, title = f.get("severity", "warning"), f.get("summary", "")
    look = str(f.get("hint") or "")
    base = {"look": look} if look else {}
    out = []
    kind = OLD_KIND.get(prefix)
    if kind == "disk_full":
        mount = d.get("mount") or rest
        pct = d.get("pct", _pct(title))
        out.append(entry(kind, host, mount, title, sev, dict(base, mount=mount, pct=pct, warn=d.get("warn"), crit=d.get("crit"),
                                                             size_b=d.get("size_b"), avail_b=d.get("avail_b"), os=os_)))
    elif kind == "disk_inodes":
        m = re.search(r"used (\d+)% of its inodes", str(title))
        out.append(entry(kind, host, rest, title, sev, dict(base, mount=rest, pct=d.get("pct", float(m.group(1)) if m else None))))
    elif kind == "service_down":
        if prefix == "services" and rest.startswith("crash:"):          # a Windows service that keeps stopping: its own kind
            out.append(entry("service_crash", host, rest[6:], title, sev, dict(base, service=rest[6:], os=os_)))
        else:
            state = d.get("state") or {"unit-failed": "failed", "unit-down": "down"}.get(prefix) or (
                "missing" if "not installed" in str(title) else "stopped")
            out.append(entry(kind, host, rest, title, sev, dict(base, service=rest, state=state, os=os_)))
    else:
        generic = re.sub(r"[^a-z0-9_]", "_", prefix.lower()) or "finding"
        out.append(entry(generic, host, rest or prefix, title, sev, base))
    if fid == "fapolicyd:denials":
        for den in d.get("denials") or []:
            prog, path = str(den.get("program") or ""), str(den.get("path") or "")
            out.append(entry("fapolicyd_deny", host, prog + "\n" + path,
                             "fapolicyd denied %s opening %s (%s time(s))" % (prog, path or "-", den.get("count", "?")),
                             "warning", {"program": prog, "path": path, "count": den.get("count")}))
    return out


def _cap(entries, max_per_kind):
    """The worst max_per_kind of each kind, in a stable order -> (kept, {kind: left out})."""
    by = {}
    for e in entries:
        by.setdefault(e["kind"], []).append(e)
    kept, capped = [], {}
    for kind in sorted(by):
        es = sorted(by[kind], key=lambda e: (SEV_ORDER.get(e["severity"], 9), e["id"]))
        seen, uniq = set(), []
        for e in es:                       # one entry per id (a finding reported twice)
            if e["id"] not in seen:
                seen.add(e["id"])
                uniq.append(e)
        kept += uniq[:int(max_per_kind)]
        if len(uniq) > int(max_per_kind):
            capped[kind] = len(uniq) - int(max_per_kind)
    return kept, capped


def os_of(text):
    """linux or windows, from an OS name or a STIG's name."""
    return "windows" if "windows" in str(text or "").lower() else "linux"


def _stig_entries(res, host, severity_map=None):
    """One stig_compare result (check_stig_result) -> stig_rule_fail, stig_fixed_open, stig_no_asset entries.
    1.138.0: honours configured severity_map (check_stig_severity) or stig_compare's rec['level'] instead of hard-coding high->critical."""
    r = res or {}
    sm = r.get("stigman") or {}
    stig = r.get("benchmark") or ""
    os_ = os_of(r.get("os") or stig or r.get("platform"))
    smap = severity_map if isinstance(severity_map, dict) else {}
    out = []

    def common(rec):
        return {"rule": rec.get("key", ""), "stigid": rec.get("stigid", ""), "cat": CAT.get(rec.get("severity"), ""),
                "title": rec.get("title", ""), "stig": stig, "collection": sm.get("collection", ""), "asset": sm.get("asset", ""),
                "os": os_}
    for group in ("drift", "new", "disagree", "unreviewed"):
        for rec in r.get(group) or []:
            d = dict(common(rec), group=group, stigman=rec.get("sm") or "")
            raw_s = rec.get("severity")
            mapped = str(smap.get(raw_s) or rec.get("level") or "").strip().lower()
            sev = mapped if mapped in ("critical", "warning", "low", "info") else ("critical" if raw_s == "high" else "warning")
            out.append(entry("stig_rule_fail", host, rec.get("key", ""), "%s fails: %s" % (rec.get("key", ""), rec.get("title", "")), sev, d))
    for rec in r.get("fixed_open") or []:
        out.append(entry("stig_fixed_open", host, rec.get("key", ""),
                         "%s passes on the host, still Open in STIG Manager" % rec.get("key", ""), "low", common(rec)))
    if sm.get("status") == "not_found":
        out.append(entry("stig_no_asset", host, sm.get("collection") or "-", "not in STIG Manager: " + (sm.get("why") or "no asset"),
                         "warning", {"collection": sm.get("collection", ""), "asset": sm.get("asset") or host, "stig": stig,
                                     "why": sm.get("why", ""), "os": os_}))
    return out


def findings_artifact(findings, host, os_="linux", max_per_kind=50, stig=None, stig_severity=None):
    """One host's site_findings (a health, fapolicyd or STIG drift job) -> {entries, capped}: what the job artifact
    site_findings keeps for it. stig: the host's check_stig_result (a dict, or a list: a host with several STIGs)."""
    es = []
    for f in findings or []:
        if isinstance(f, dict) and f.get("id"):
            es += health_entries(f, host, os_)
    for res in (stig if isinstance(stig, list) else [stig] if stig else []):
        es += _stig_entries(res, host, stig_severity)
    kept, capped = _cap(es, max_per_kind)
    return {"entries": kept, "capped": capped}


def ckl_findings_artifact(no_asset, fixed, max_per_kind=50):
    """The checklist import's (scorecard's) findings -> {hosts: {host: [entry]}, capped: {host: {kind: n}}}.
    no_asset: [{host, collection, stig, why}] - a checklist whose host has no asset in STIG Manager;
    fixed: [{host, rule, stigid, cat, title, stig, collection, asset}] - fixed on the host, still Open."""
    by = {}
    for n in no_asset or []:
        h = str(n.get("host") or n.get("asset") or "")          # the import's plan: the asset it would create, by the host's name
        stig = n.get("stig") or ", ".join(n.get("benchmarks") or [n.get("benchmark") or ""])
        if h:
            by.setdefault(h, []).append(entry("stig_no_asset", h, n.get("collection") or "-", "not in STIG Manager: " + (n.get("why") or "no asset"),
                                              "warning", {"collection": n.get("collection", ""), "asset": n.get("asset") or h,
                                                          "stig": stig, "why": n.get("why", ""), "os": n.get("os") or os_of(stig)}))
    for x in fixed or []:
        h = str(x.get("host") or "")
        if h and x.get("rule"):
            d = dict((k, x.get(k, "")) for k in ("rule", "stigid", "cat", "title", "stig", "collection", "asset"))
            d["os"] = x.get("os") or os_of(x.get("stig"))
            by.setdefault(h, []).append(entry("stig_fixed_open", h, x["rule"], "%s passes on the host, still Open in STIG Manager" % x["rule"],
                                              "low", d))
    hosts, capped = {}, {}
    for h, es in by.items():
        kept, cap = _cap(es, max_per_kind)
        hosts[h] = kept
        if cap:
            capped[h] = cap
    return {"hosts": hosts, "capped": capped}


# ---- the buttons ---------------------------------------------------------------------------------------------------
def _matches(action, e):
    kinds = action.get("kinds") or []
    if "any" not in kinds and e.get("kind") not in kinds:         # "any": every kind of finding (open_ticket)
        return False
    for field, want in (action.get("when") or {}).items():
        have = (e.get("data") or {}).get(field)
        allowed = want if isinstance(want, list) else [want]
        if str(have) not in [str(x) for x in allowed]:
            return False
    return True


def finding_buttons(e, actions, base, job):
    """The buttons of one finding: [{label, url}] - each action whose kinds (and when) match, by its name; [] without
    report_portal_url or a job (outside AAP). The link carries target, report_job and finding_id only."""
    base = str(base or "").strip().rstrip("/")
    job = str(job or "").strip()
    if not e or not re.match(r"https?://[^\s\"'<>]+\Z", base) or not job.isdigit() or not ID_CHARS.match(str(e.get("id") or "")):
        return []
    out = []
    for name in sorted(actions or {}):
        a = (actions or {})[name] or {}
        if not NAME.match(str(name)) or not isinstance(a, dict) or not _matches(a, e):
            continue
        q = urlencode([("finding_id", e["id"]), ("report_job", job), ("target", e.get("host", ""))])
        out.append({"label": str(a.get("button") or a.get("title") or name)[:40], "url": "%s/actions/%s?%s" % (base, quote(name), q),
                    "action": name})
    return out


def add_buttons_column(section, buttons, header="Actions"):
    """A section's table with one more column of buttons (kind buttons: up to two shown, the rest under More): buttons[i]
    are row i's ([] = none). The section is changed and returned; with no button at all it is left as it was."""
    if not any(buttons or []):
        return section
    section["columns"] = list(section["columns"]) + [header]
    section["rows"] = [list(r) + ["; ".join(b["label"] for b in buttons[i])] for i, r in enumerate(section["rows"])]
    n = len(section["columns"]) - 1
    kinds = list(section.get("kinds") or [])
    section["kinds"] = kinds[:n] + [""] * (n - len(kinds[:n])) + ["buttons"]
    section["buttons"] = [list(b) for b in buttons]
    for key in ("cell_status", "tips"):
        if section.get(key):
            section[key] = [list(c) + [""] for c in section[key]]
    if section.get("column_titles"):
        section["column_titles"] = list(section["column_titles"]) + [""]
    return section


def findings_actions_section(hosts, actions, base, job, title="What you can do from here", limit=200, kinds=None):
    """A compact section for a report whose tables have no room for a column of buttons (STIG drift, the scorecard, the
    fapolicyd job): each finding that has a button - Host, Finding, Severity, its buttons. hosts: {host: [entry]}; kinds:
    only these kinds (None = all). None when no finding has one."""
    rows, buttons = [], []
    for h in sorted(hosts or {}):
        for e in sorted(hosts[h] or [], key=lambda x: (SEV_ORDER.get(x.get("severity"), 9), x.get("id", ""))):
            if kinds is not None and e.get("kind") not in kinds:
                continue
            b = finding_buttons(e, actions, base, job)
            if b:
                rows.append([h, e.get("title", ""), str(e.get("severity", "")).upper()])
                buttons.append(b)
    if not rows:
        return None
    more = len(rows) - int(limit)
    sec = {"title": title, "columns": ["Host", "Finding", "Severity"], "kinds": ["", "wrap", ""], "rows": rows[:int(limit)],
           "text": "Each button opens the site portal's confirm page for that finding: you check and press Run there."
                   + (" %d more finding(s) are left out here." % more if more > 0 else "")}
    return add_buttons_column(sec, buttons[:int(limit)])


def fapolicyd_block_entries(blocks, host, max_per_kind=50):
    """The fapolicyd job's blocks of one host (site_fapolicyd_rules: {kind denied | would, exe, path, perm, count, rule_no,
    where}) -> {entries, capped}: one fapolicyd_deny each, as the health check's (the same id for the same program and path)."""
    es = []
    for g in blocks or []:
        if not isinstance(g, dict):
            continue
        prog, path = str(g.get("exe") or ""), str(g.get("path") or "")
        denied = g.get("kind") == "denied"
        es.append(entry("fapolicyd_deny", host, prog + "\n" + path,
                        "fapolicyd %s %s opening %s (%s time(s))" % ("denied" if denied else "would deny", prog or "?", path or "?",
                                                                     g.get("count", "?")),
                        "warning" if denied else "low",
                        {"program": prog, "path": path, "count": g.get("count"), "perm": g.get("perm"), "rule_no": g.get("rule_no"),
                         "mode": "denied" if denied else "permissive", "where": g.get("where")}))
    kept, capped = _cap(es, max_per_kind)
    return {"entries": kept, "capped": capped}


def fapolicyd_denials_data(lines):
    """The fapolicyd check's top denials ("<program> -> <path> x<count>") -> [{program, path, count}]."""
    out = []
    for line in lines or []:
        m = re.match(r"^(.*?) -> (.*?)(?: x(\d+))?$", str(line).strip())
        if m:
            out.append({"program": m.group(1)[:DATA_MAX], "path": m.group(2)[:DATA_MAX], "count": int(m.group(3)) if m.group(3) else None})
    return out


class FilterModule(object):
    def filters(self):
        return {"findings_artifact": findings_artifact, "ckl_findings_artifact": ckl_findings_artifact,
                "finding_buttons": finding_buttons, "findings_actions_section": findings_actions_section,
                "finding_key": finding_key, "finding_id": finding_id,
                "fapolicyd_denials_data": fapolicyd_denials_data, "fapolicyd_block_entries": fapolicyd_block_entries}
