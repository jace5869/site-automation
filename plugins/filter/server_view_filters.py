# -*- coding: utf-8 -*-
"""1.138.0: Server-first report view and shared severity / per-host counting (plugins/filter/server_view_filters.py).

Turns per-host findings (site_findings / hostvars), health metadata (site_health), missing hosts with
reasons ({host: reason} or legacy [host, ...]), and capped counts (site_findings_capped) into one
section dict for report_servers.html.j2 across atlas, brief and ops.

Design rules (plan-reports-by-server.md):
- One severity normalizer for all five vocabularies (common four, STIG high/medium/low, CAT I/II/III,
  aap_health PASS/WARN/FAIL/INFO/SKIP, UCS critical/major/minor).
- Per-host counts are computed once here so the fleet summary, the flat Findings table and the server
  view never disagree.
- low has its own count column and must NEVER make a host amber (warning).
- hint is a read-only command (gets a Copy button); fix is prose for a person (never run, no Copy button).
- A capped host explicitly says "and N more of <kind>, not listed" so the list never implies completeness.
- Standard library only (plus plugins/module_utils/site_time.py).
"""
from __future__ import absolute_import, division, print_function

import importlib.util
import json
import os
import re

__metaclass__ = type


SEVERITIES = ("critical", "warning", "low", "info")
STATUS_ORDER = {"critical": 0, "warning": 1, "unknown": 2, "low": 3, "info": 4, "ok": 5}
SEV_ORDER = {"critical": 0, "warning": 1, "low": 2, "info": 3, "unknown": 4, "ok": 5}
STATUS_LABELS = {
    "critical": "Critical",
    "warning": "Warning",
    "low": "Low",
    "info": "Info",
    "unknown": "Not reached",
    "ok": "OK",
}
SEV_LABELS = {
    "critical": "Critical",
    "warning": "Warning",
    "low": "Low",
    "info": "Info",
    "unknown": "Unknown",
    "ok": "OK",
}

# What kind of check a finding comes from (matches site_filters.CHECK_KIND)
CHECK_KIND = {
    "certs": "Certificate", "net_certs": "Certificate", "vmware_certs": "Certificate", "trust": "Trust store",
    "disk": "Storage", "mounts": "Mounts", "services": "Service", "containers": "Containers",
    "mariadb": "Database", "auditd": "Audit", "fapolicyd": "fapolicyd", "performance": "Performance",
    "selinux": "SELinux", "accounts": "Accounts", "time": "Time", "patching": "Patching",
    "network": "Network", "netperf": "Network", "logging": "Logging", "stig": "STIG",
    "poam": "POA&M", "reachability": "Reachability", "servicenow": "ServiceNow",
    "servicenow_node": "ServiceNow node", "sso": "SSO", "config": "Configuration",
}

# 1.138.0: Five severity vocabularies -> one canonical set (critical | warning | low | info | ok | unknown)
_SEV_MAP = {
    # Common four + ok / unknown
    "critical": "critical",
    "warning": "warning",
    "low": "low",
    "info": "info",
    "ok": "ok",
    "unknown": "unknown",
    # STIG (high / medium / low)
    "high": "critical",
    "medium": "warning",
    "med": "warning",
    # CAT I / II / III
    "cat i": "critical",
    "cat 1": "critical",
    "cat1": "critical",
    "cati": "critical",
    "cat ii": "warning",
    "cat 2": "warning",
    "cat2": "warning",
    "catii": "warning",
    "cat iii": "low",
    "cat 3": "low",
    "cat3": "low",
    "catiii": "low",
    # aap_health (PASS / WARN / FAIL / INFO / SKIP)
    "fail": "critical",
    "failed": "critical",
    "warn": "warning",
    "pass": "ok",
    "passed": "ok",
    "skip": "unknown",
    "skipped": "unknown",
    # UCS (critical / major / minor)
    "major": "warning",
    "minor": "low",
    "cleared": "ok",
    # Common aliases
    "crit": "critical",
    "error": "critical",
    "err": "critical",
    "fatal": "critical",
    "severe": "critical",
    "moderate": "warning",
    "notice": "info",
    "informational": "info",
    "healthy": "ok",
    "normal": "ok",
    "success": "ok",
    "compliant": "ok",
    "unreached": "unknown",
    "unreachable": "unknown",
}

_TIME_MOD = []


def _site_time():
    """Load plugins/module_utils/site_time.py by path (Ansible loads filter plugins by path)."""
    if not _TIME_MOD:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "module_utils", "site_time.py")
        spec = importlib.util.spec_from_file_location("site_time_for_server_view", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _TIME_MOD.append(mod)
    return _TIME_MOD[0]


def normalize_severity(raw, default="warning"):
    """1.138.0: Normalize any of the repo's five severity vocabularies into
    critical | warning | low | info | ok | unknown.

    Vocabularies handled:
    - Common four: critical, warning, low, info (+ ok, unknown)
    - STIG: high, medium, low
    - CAT: CAT I / II / III (and CAT 1 / 2 / 3)
    - aap_health: PASS, WARN, FAIL, INFO, SKIP
    - UCS: critical, major, minor
    """
    if raw is None:
        return default
    key = re.sub(r"[\s_-]+", " ", str(raw).strip().lower()).strip()
    if not key:
        return default
    return _SEV_MAP.get(key, default)


def missing_map(missing):
    """1.138.0: Normalize missing hosts from {host: reason}, [host, ...], or [{host, reason}, ...]
    into {host: reason_str}. Keeps the legacy list form working."""
    if not missing:
        return {}
    out = {}
    if isinstance(missing, dict):
        for h, reason in missing.items():
            name = str(h or "").strip()
            if name:
                out[name] = " ".join(str(reason or "").split()).strip()
        return out
    for item in missing:
        if isinstance(item, dict):
            name = str(item.get("host") or item.get("name") or "").strip()
            reason = " ".join(str(item.get("reason") or "").split()).strip()
        else:
            name = str(item or "").strip()
            reason = ""
        if name:
            out[name] = reason
    return out


def missing_lines(missing):
    """1.138.0: Sorted missing-host lines: 'host: reason' when reason is present, else 'host'."""
    mm = missing_map(missing)
    return [("%s: %s" % (h, mm[h])) if mm[h] else h for h in sorted(mm)]


def _host_status(counts):
    """Host status from its severity counts. low and info NEVER make a host amber (warning)."""
    if counts["critical"] > 0:
        return "critical"
    if counts["warning"] > 0:
        return "warning"
    if counts["low"] > 0:
        return "low"
    if counts["info"] > 0:
        return "info"
    return "ok"


def host_counts(by_host, missing=None):
    """1.138.0: Single source of truth for per-host and fleet finding counts.

    Returns:
      {
        "hosts": {host: {"critical": int, "warning": int, "low": int, "info": int,
                         "total": int, "actionable": bool, "status": str, "checks": [str]}},
        "missing": {host: reason},
        "totals": {"hosts": int, "missing": int, "total_hosts": int, "healthy": int,
                   "clear": int, "with_findings": int, "actionable": int,
                   "critical_hosts": int, "warning_hosts": int, "low_hosts": int, "info_hosts": int,
                   "critical": int, "warning": int, "low": int, "info": int, "findings": int}
      }
    Note: 'healthy' matches findings_report semantics (reported hosts with no critical/warning finding);
    'clear' is reported hosts with zero findings of any severity.
    """
    by_host = by_host or {}
    mm = missing_map(missing)
    hosts_out = {}
    tot_c = tot_w = tot_l = tot_i = 0
    actionable_hosts = with_any = clear_hosts = 0
    crit_hosts = warn_hosts = low_hosts = info_hosts = 0

    for h in sorted(by_host):
        fs = by_host.get(h) or []
        c = {"critical": 0, "warning": 0, "low": 0, "info": 0}
        checks = []
        seen_chk = set()
        for f in fs:
            if not isinstance(f, dict):
                continue
            sev = normalize_severity(f.get("severity"), default="warning")
            if sev in c:
                c[sev] += 1
            elif sev == "unknown":
                c["warning"] += 1
            chk = str(f.get("check") or f.get("kind") or "").strip()
            if chk and chk not in seen_chk:
                seen_chk.add(chk)
                checks.append(chk)
        total = c["critical"] + c["warning"] + c["low"] + c["info"]
        actionable = (c["critical"] + c["warning"]) > 0
        st = _host_status(c)
        hosts_out[h] = {
            "critical": c["critical"],
            "warning": c["warning"],
            "low": c["low"],
            "info": c["info"],
            "total": total,
            "actionable": actionable,
            "status": st,
            "checks": checks,
        }
        tot_c += c["critical"]
        tot_w += c["warning"]
        tot_l += c["low"]
        tot_i += c["info"]
        if total > 0:
            with_any += 1
        else:
            clear_hosts += 1
        if actionable:
            actionable_hosts += 1
        if st == "critical":
            crit_hosts += 1
        elif st == "warning":
            warn_hosts += 1
        elif st == "low":
            low_hosts += 1
        elif st == "info":
            info_hosts += 1

    # Drop any missing entry for a host that actually reported
    miss_clean = dict((h, r) for h, r in mm.items() if h not in hosts_out)
    reported = len(hosts_out)
    totals = {
        "hosts": reported,
        "missing": len(miss_clean),
        "total_hosts": reported + len(miss_clean),
        "healthy": reported - actionable_hosts,
        "clear": clear_hosts,
        "with_findings": with_any,
        "actionable": actionable_hosts,
        "critical_hosts": crit_hosts,
        "warning_hosts": warn_hosts,
        "low_hosts": low_hosts,
        "info_hosts": info_hosts,
        "critical": tot_c,
        "warning": tot_w,
        "low": tot_l,
        "info": tot_i,
        "findings": tot_c + tot_w + tot_l + tot_i,
    }
    return {"hosts": hosts_out, "missing": miss_clean, "totals": totals}


def fleet_summary_lines(by_host, missing=None):
    """1.138.0: Fleet summary lines for roles/site_findings/tasks/report.yml (replaces inline Jinja counting)."""
    hc = host_counts(by_host, missing)
    hosts = hc["hosts"]
    miss = hc["missing"]
    tot = hc["totals"]
    out = []
    head = "%d healthy, %d with findings" % (tot["clear"], tot["with_findings"])
    if miss:
        head += ", %d did not report (unreachable or failed early)" % len(miss)
    out.append(head)
    for h in sorted(hosts):
        info = hosts[h]
        if info["total"] == 0:
            continue
        parts = ["%d critical" % info["critical"], "%d warning" % info["warning"]]
        if info["low"]:
            parts.append("%d low" % info["low"])
        if info["info"]:
            parts.append("%d info" % info["info"])
        checks = ", ".join(info["checks"])
        out.append("%s: %s (%s)" % (h, ", ".join(parts), checks))
    for h in sorted(miss):
        reason = miss[h]
        if reason:
            if reason.lower().startswith("skipped"):
                out.append("%s: %s" % (h, reason))
            else:
                out.append("%s: NOT REACHED - %s" % (h, reason))
        else:
            out.append("%s: NO REPORT (unreachable, or the job failed on it before the checks ran)" % h)
    return out


def _fmt_checked(raw, tz):
    """Format an ISO/UTC timestamp into display text in report_time_zone (or UTC when unset)."""
    st = str(raw or "").strip()
    if not st:
        return ""
    tm = _site_time()
    m = tm.STORED.match(st)
    if not m:
        return st
    g = m.groups()
    base = "%s-%s-%s %s:%s UTC" % (g[0], g[1], g[2], g[3], g[4])
    return tm.local(base, tz) if tz is not None else base


def _meter_mask(flags, max_segs=24):
    """Build a compact boolean meter list (1 segment per server up to max_segs, proportional above)."""
    n = len(flags)
    if n == 0:
        return []
    if n <= max_segs:
        return [bool(x) for x in flags]
    on = sum(1 for x in flags if x)
    lit = int(round(float(on) * max_segs / n))
    if on > 0 and lit == 0:
        lit = 1
    return [i < lit for i in range(max_segs)]


def _normalize_finding(host, f, ask=None, tz=None):
    """Turn one raw finding dict (health finding or site_findings artifact entry) into a display dict.
    hint = read-only command (gets Copy); fix = prose for a person (never run, no Copy)."""
    raw_sev = f.get("severity")
    sev = normalize_severity(raw_sev, default="warning")
    if sev not in SEVERITIES:
        sev = "info" if sev == "ok" else "warning"
    chk_raw = str(f.get("check") or f.get("kind") or "").strip()
    chk_label = CHECK_KIND.get(chk_raw, chk_raw.replace("_", " ") if chk_raw else "")
    title = str(f.get("summary") or f.get("title") or chk_label or f.get("id") or "").strip()
    evidence = str(f.get("note") or f.get("evidence") or "").strip()
    fid = str(f.get("id") or "").strip()
    if not evidence and fid and fid != title:
        evidence = "%s · %s" % (chk_raw or chk_label, fid) if (chk_raw or chk_label) else fid
    # fix is prose (What to do), NEVER gets a Copy button.
    fix = str(f.get("fix") or "").strip()
    # hint is a read-only command a person may paste; gets Copy only when non-empty.
    hint = str(f.get("hint") or "").strip()
    since_raw = str(f.get("since") or f.get("time") or "").strip()
    since = _fmt_checked(since_raw, tz) if since_raw else ""
    buttons = []
    if callable(ask):
        try:
            buttons = list(ask(host, f) or [])
        except Exception:
            buttons = []
    elif isinstance(f.get("buttons"), list):
        buttons = list(f["buttons"])
    return {
        "severity": sev,
        "severity_label": SEV_LABELS.get(sev, sev.capitalize()),
        "check": chk_raw,
        "check_label": chk_label,
        "id": fid,
        "title": title,
        "evidence": evidence,
        "since": since,
        "fix": fix,
        "hint": hint,
        "buttons": buttons,
    }


def _capped_items(cap_map):
    """Normalize one host's site_findings_capped ({kind: n}) into explicit 'and N more of <kind>, not listed' items."""
    if not isinstance(cap_map, dict):
        return []
    items = []
    for k in sorted(cap_map):
        try:
            n = int(cap_map[k] or 0)
        except (TypeError, ValueError):
            continue
        if n > 0:
            label = str(k).strip()
            items.append({
                "kind": label,
                "count": n,
                "text": "and %d more of %s, not listed" % (n, label),
            })
    return items


def server_view(by_host, health=None, missing=None, capped=None, opts=None):
    """1.138.0: Build the server-first report section from per-host findings, health, missing and capped data.

    Arguments:
      by_host: {host: [finding_dict, ...]}
      health:  optional {host: {status, checks, checks_failed, time, ip, teams, ...}}
      missing: optional {host: reason} or [host, ...]
      capped:  optional {host: {kind: n}} (site_findings_capped)
      opts:    optional {title, zone, ips, portal, teams, now}

    Returns one section dict for report_servers.html.j2 (and compatible with report_atlas/brief/ops):
      {title, status, server_view: True, rail: True, counts, glance, tiles, columns, column_titles,
       kinds, rows, row_status, row_tags, details, servers, summary_text}
    """
    by_host = by_host or {}
    health = health if isinstance(health, dict) else {}
    capped = capped if isinstance(capped, dict) else {}
    o = opts if isinstance(opts, dict) else {}

    tm = _site_time()
    tz, _ = tm.zone(o.get("zone") or "")
    checked_col = tm.column("Checked (UTC)", tz)
    ips = o.get("ips") if isinstance(o.get("ips"), dict) else {}
    ask = o.get("portal") if callable(o.get("portal")) else None
    teams = o.get("teams") if isinstance(o.get("teams"), dict) and (o.get("teams").get("names") or []) else None
    tmap = (teams.get("map") or {}) if teams else {}
    tother = (teams.get("other") or "Unassigned") if teams else "Unassigned"
    default_now = str(o.get("now") or "").strip()

    hc = host_counts(by_host, missing)
    hosts_c = hc["hosts"]
    miss_c = hc["missing"]
    totals = hc["totals"]

    servers = []
    total_capped = 0

    for h in sorted(hosts_c):
        cnt = hosts_c[h]
        hmeta = health.get(h) if isinstance(health.get(h), dict) else {}
        ip = str(ips.get(h) or hmeta.get("ip") or "").strip()
        raw_time = str(hmeta.get("time") or default_now).strip()
        checked = _fmt_checked(raw_time, tz)
        checks_run = list(dict.fromkeys(str(c) for c in (hmeta.get("checks") or cnt["checks"]) if str(c).strip()))

        raw_fs = [f for f in (by_host.get(h) or []) if isinstance(f, dict)]
        norm_fs = [_normalize_finding(h, f, ask=ask, tz=tz) for f in raw_fs]
        norm_fs.sort(key=lambda x: (SEV_ORDER.get(x["severity"], 9), x["check"], x["title"].lower()))

        cap_list = _capped_items(capped.get(h))
        cap_n = sum(x["count"] for x in cap_list)
        total_capped += cap_n

        st = cnt["status"]
        if norm_fs:
            worst = norm_fs[0]["title"]
            if cap_n:
                worst = "%s (+%d more, not listed)" % (worst, cap_n)
        elif cap_n:
            worst = "%d more finding(s), not listed" % cap_n
        else:
            worst = "No findings"

        servers.append({
            "host": h,
            "ip": ip,
            "status": st,
            "status_label": STATUS_LABELS.get(st, st.capitalize()),
            "missing": False,
            "reason": "",
            "counts": {
                "critical": cnt["critical"],
                "warning": cnt["warning"],
                "low": cnt["low"],
                "info": cnt["info"],
                "total": cnt["total"],
            },
            "worst": worst,
            "checked": checked,
            "checked_iso": raw_time,
            "checks": checks_run,
            "findings": norm_fs,
            "capped": cap_list,
            "capped_total": cap_n,
            "capped_text": "; ".join(x["text"] for x in cap_list),
        })

    for h in sorted(miss_c):
        reason = miss_c[h]
        hmeta = health.get(h) if isinstance(health.get(h), dict) else {}
        ip = str(ips.get(h) or hmeta.get("ip") or "").strip()
        worst = reason if reason else "Unreachable, or failed before checks ran"
        servers.append({
            "host": h,
            "ip": ip,
            "status": "unknown",
            "status_label": STATUS_LABELS["unknown"],
            "missing": True,
            "reason": reason,
            "counts": {"critical": 0, "warning": 0, "low": 0, "info": 0, "total": 0},
            "worst": worst,
            "checked": "-",
            "checked_iso": "",
            "checks": [],
            "findings": [],
            "capped": [],
            "capped_total": 0,
            "capped_text": "",
        })

    # Worst first: critical -> warning -> not reached (unknown) -> low -> info -> ok, then by counts and host name
    servers.sort(key=lambda s: (
        STATUS_ORDER.get(s["status"], 9),
        -s["counts"]["critical"],
        -s["counts"]["warning"],
        -s["counts"]["low"],
        -s["counts"]["info"],
        s["host"].lower(),
    ))

    # Build table arrays + glance + details + row_tags (emitting data.sev so report_pop.js sevChips() wakes up)
    columns = ["", "Server", "Status", "Crit", "Warn", "Low", "Info", "Worst finding", checked_col]
    column_titles = ["Open", "Server", "Status", "Critical", "Warning", "Low", "Info", "Worst finding", checked_col]
    kinds = ["x", "wrap", "nw", "num", "num", "num", "num", "long", "nw"]
    rows, row_status, row_tags, details, glance = [], [], [], [], []

    for idx, s in enumerate(servers):
        rid = "srv-%d" % idx
        c = s["counts"]
        rows.append([
            "",
            s["host"],
            s["status_label"],
            "-" if s["missing"] else c["critical"],
            "-" if s["missing"] else c["warning"],
            "-" if s["missing"] else c["low"],
            "-" if s["missing"] else c["info"],
            s["worst"],
            s["checked"] or "-",
        ])
        row_status.append(s["status"])
        tag_data = {"sev": s["status"], "host": s["host"]}
        if teams:
            tag_data["team"] = json.dumps(tmap.get(s["host"]) or [tother], separators=(",", ":"))
        row_tags.append({"id": rid, "data": tag_data})
        details.append({
            "host": s["host"],
            "ip": s["ip"],
            "status": s["status"],
            "status_label": s["status_label"],
            "missing": s["missing"],
            "reason": s["reason"],
            "checked": s["checked"],
            "checks": s["checks"],
            "findings": s["findings"],
            "capped": s["capped"],
            "capped_text": s["capped_text"],
        })
        if s["missing"]:
            g_text = s["worst"]
        elif c["total"]:
            g_parts = []
            for k, lbl in (("critical", "critical"), ("warning", "warning"), ("low", "low"), ("info", "info")):
                if c[k]:
                    g_parts.append("%d %s" % (c[k], lbl))
            if s["capped_total"]:
                g_parts.append("+%d not listed" % s["capped_total"])
            g_text = ", ".join(g_parts) + " — " + s["findings"][0]["title"]
        else:
            g_text = "No findings"
        glance.append({
            "status": s["status"],
            "label": s["status_label"],
            "name": s["host"],
            "href": "#" + rid,
            "text": g_text,
        })

    # At a glance tiles (4-up grid matching reports-by-server.html, reusing existing look tokens)
    rep_mask = _meter_mask([not s["missing"] for s in servers])
    find_mask = _meter_mask([s["counts"]["total"] > 0 for s in servers])
    crit_mask = _meter_mask([s["status"] == "critical" for s in servers])
    clear_mask = _meter_mask([s["status"] == "ok" for s in servers])

    checked_note = "%d of %d reporting" % (totals["hosts"], totals["total_hosts"])
    if totals["missing"]:
        checked_note += " · %d not reached" % totals["missing"]
    findings_note = "Across %d server%s" % (totals["with_findings"], "" if totals["with_findings"] == 1 else "s")
    if total_capped:
        findings_note += " · +%d not listed" % total_capped
    findings_tone = "warning" if (totals["critical"] or totals["warning"]) else ("low" if totals["low"] else ("info" if totals["info"] else "ok"))

    tiles = [
        {
            "label": "Servers checked",
            "value": totals["hosts"],
            "note": checked_note,
            "status": "warning" if totals["missing"] else "ok",
            "icon": "server",
            "mask": rep_mask,
        },
        {
            "label": "Findings",
            "value": totals["findings"],
            "note": findings_note,
            "status": findings_tone,
            "icon": "findings",
            "mask": find_mask,
        },
        {
            "label": "Critical servers",
            "value": totals["critical_hosts"],
            "note": "Require action" if totals["critical_hosts"] else "None",
            "status": "critical" if totals["critical_hosts"] else "ok",
            "icon": "critical",
            "mask": crit_mask,
        },
        {
            "label": "Clear servers",
            "value": totals["clear"],
            "note": "No findings",
            "status": "ok",
            "icon": "ok",
            "mask": clear_mask,
        },
    ]

    if totals["critical"] or totals["critical_hosts"]:
        sec_status = "critical"
    elif totals["warning"] or totals["missing"]:
        sec_status = "warning"
    elif totals["low"]:
        sec_status = "low"
    elif totals["info"]:
        sec_status = "info"
    else:
        sec_status = "ok"

    sum_parts = []
    if totals["critical_hosts"]:
        sum_parts.append("%d critical" % totals["critical_hosts"])
    if totals["warning_hosts"]:
        sum_parts.append("%d warning" % totals["warning_hosts"])
    if totals["missing"]:
        sum_parts.append("%d not reached" % totals["missing"])
    if totals["low_hosts"]:
        sum_parts.append("%d low" % totals["low_hosts"])
    if totals["info_hosts"]:
        sum_parts.append("%d info" % totals["info_hosts"])
    if totals["clear"]:
        sum_parts.append("%d ok" % totals["clear"])
    summary_text = "%d server%s: %s" % (
        totals["total_hosts"],
        "" if totals["total_hosts"] == 1 else "s",
        ", ".join(sum_parts) if sum_parts else "none",
    )

    return {
        "title": str(o.get("title") or "Servers"),
        "status": sec_status,
        "server_view": True,
        "rail": True,
        "counts": dict(totals, capped=total_capped),
        "glance": glance,
        "tiles": tiles,
        "columns": columns,
        "column_titles": column_titles,
        "kinds": kinds,
        "rows": rows,
        "row_status": row_status,
        "row_tags": row_tags,
        "details": details,
        "servers": servers,
        "summary_text": summary_text,
    }


class FilterModule(object):
    def filters(self):
        return {
            "normalize_severity": normalize_severity,
            "server_view": server_view,
            "host_counts": host_counts,
            "fleet_summary_lines": fleet_summary_lines,
        }
