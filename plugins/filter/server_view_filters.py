# -*- coding: utf-8 -*-
"""1.139.0: Server-first report view and shared severity / per-host counting (plugins/filter/server_view_filters.py).

Turns per-host findings (site_findings / hostvars), health metadata (site_health), missing hosts with
reasons ({host: reason} or legacy [host, ...]), capped counts (site_findings_capped), and report-specific
data (STIG, fapolicyd, sw_inventory, SolarWinds, cert_inventory, AD, STIG Manager, and chrome-only
ONTAP / UCS / Netmap) into section dicts for report_servers.html.j2 across atlas, brief, ops and glass.

Design rules (plan-reports-by-server.md + vdi-design-check.txt):
- One severity normalizer for all five vocabularies + cert/network/storage words (expired, down, degraded, etc.).
- Per-host counts are computed once here so the fleet summary, flat Findings table and server view agree.
- At a glance stays on top of every report.
- Criticals stand out (never colour alone); low has its own count column and NEVER makes a host amber.
- Not-reached / unchecked / uncertain hosts have status 'unknown' (never warning, never green).
- hint is a read-only command (gets a Copy button); fix is prose for a person (What to do, NEVER a Copy button).
- A capped host explicitly says "and N more of <kind>, not listed" (and "and N more not listed" pill).
- Standard library only (plus plugins/module_utils/site_time.py and plugins/filter/stig_text_filters.py).
"""
from __future__ import absolute_import, division, print_function

import fnmatch
import importlib.util
import json
import os
import re
import shlex
from functools import lru_cache

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
CAT_LABELS = {"high": "CAT I", "medium": "CAT II", "low": "CAT III"}

# What kind of check a finding comes from (matches site_filters.CHECK_KIND)
CHECK_KIND = {
    "certs": "Certificate", "net_certs": "Certificate", "vmware_certs": "Certificate", "trust": "Trust store",
    "disk": "Storage", "mounts": "Mounts", "services": "Service", "containers": "Containers",
    "mariadb": "Database", "auditd": "Audit", "fapolicyd": "fapolicyd", "fapolicyd_deny": "fapolicyd",
    "performance": "Performance", "selinux": "SELinux", "accounts": "Accounts", "time": "Time",
    "patching": "Patching", "network": "Network", "netperf": "Network", "logging": "Logging",
    "stig": "STIG", "stig_rule": "STIG", "poam": "POA&M", "reachability": "Reachability",
    "servicenow": "ServiceNow", "servicenow_node": "ServiceNow node", "sso": "SSO",
    "config": "Configuration", "stigman": "STIG Manager", "ad": "Active Directory",
    "replication": "Replication", "dcdiag": "dcdiag", "service": "Service", "sysvol": "SYSVOL",
    "gpo": "GPO", "fsmo": "FSMO", "dns": "DNS", "kerberos": "Kerberos", "certificate": "Certificate",
    "events": "Events", "sites": "Sites", "privileged": "Privileged groups", "backup": "Backup",
}

# 1.139.0: Five severity vocabularies + cert/infra words -> canonical set (critical | warning | low | info | ok | unknown)
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
    # Certificates, storage, network & reachability words (vdi-design-check item 8)
    "expired": "critical",
    "expiring": "warning",
    "down": "critical",
    "offline": "critical",
    "faulted": "critical",
    "degraded": "warning",
    "at risk": "warning",
    "online": "ok",
    "up": "ok",
    "not read": "unknown",
    "not checked": "unknown",
    "not certain": "unknown",
    "uncertain": "unknown",
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
_SV_RE = re.compile(r"\bSV-\d+")
_STIGMAN_HOST_MISSING_RE = re.compile(r"^stigman:host:(.+)$")
_DEFAULT_KIND_CAPS = {"fapolicyd_deny": 10}


def _site_time():
    """Load plugins/module_utils/site_time.py by path (Ansible loads filter plugins by path)."""
    if not _TIME_MOD:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "module_utils", "site_time.py")
        spec = importlib.util.spec_from_file_location("site_time_for_server_view", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _TIME_MOD.append(mod)
    return _TIME_MOD[0]


@lru_cache(maxsize=None)
def _report_filter(name):
    path = os.path.join(os.path.dirname(__file__), name + ".py")
    spec = importlib.util.spec_from_file_location(name + "_for_server_view", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def normalize_severity(raw, default="warning"):
    """1.139.0: Normalize any of the repo's five severity vocabularies (plus cert/infra words) into
    critical | warning | low | info | ok | unknown.

    Vocabularies handled:
    - Common four: critical, warning, low, info (+ ok, unknown)
    - STIG: high, medium, low
    - CAT: CAT I / II / III (and CAT 1 / 2 / 3)
    - aap_health: PASS, WARN, FAIL, INFO, SKIP
    - UCS: critical, major, minor
    - Cert / infra: expired, expiring, down, offline, degraded, not read, not certain
    """
    if raw is None:
        return default
    key = re.sub(r"[\s_-]+", " ", str(raw).strip().lower()).strip()
    if not key:
        return default
    return _SEV_MAP.get(key, default)


def missing_map(missing):
    """1.139.0: Normalize missing hosts from {host: reason}, [host, ...], or [{host, reason}, ...]
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
            reason = " ".join(str(item.get("reason") or item.get("why") or item.get("error") or "").split()).strip()
        else:
            name = str(item or "").strip()
            reason = ""
        if name:
            out[name] = reason
    return out


def missing_lines(missing):
    """1.139.0: Case-insensitively sorted missing-host lines: 'host: reason' when reason is present, else 'host'."""
    mm = missing_map(missing)
    return [("%s: %s" % (h, mm[h])) if mm[h] else h for h in sorted(mm, key=str.lower)]


def _host_status(counts, unknown_n=0):
    """Host status from its severity counts. low and info NEVER make a host amber (warning);
    unknown stays unknown rather than warning."""
    if counts["critical"] > 0:
        return "critical"
    if counts["warning"] > 0:
        return "warning"
    if unknown_n > 0:
        return "unknown"
    if counts["low"] > 0:
        return "low"
    if counts["info"] > 0:
        return "info"
    return "ok"


def host_counts(by_host, missing=None):
    """1.139.0: Single source of truth for per-host and fleet finding counts.

    Returns:
      {
        "hosts": {host: {"critical": int, "warning": int, "low": int, "info": int,
                         "total": int, "actionable": bool, "status": str, "checks": [str]}},
        "missing": {host: reason},
        "totals": {"hosts": int, "missing": int, "total_hosts": int, "healthy": int,
                   "clear": int, "with_findings": int, "actionable": int,
                   "critical_hosts": int, "warning_hosts": int, "low_hosts": int, "info_hosts": int,
                   "unknown_hosts": int,
                   "critical": int, "warning": int, "low": int, "info": int, "findings": int}
      }
    Note: 'healthy' matches findings_report semantics (reported hosts with no critical/warning finding);
    'clear' is reported hosts with zero findings of any severity. unknown never increments warning.
    """
    by_host = by_host or {}
    mm = missing_map(missing)
    hosts_out = {}
    tot_c = tot_w = tot_l = tot_i = 0
    actionable_hosts = with_any = clear_hosts = 0
    crit_hosts = warn_hosts = low_hosts = info_hosts = unk_hosts = 0

    for h in sorted(by_host, key=str.lower):
        fs = by_host.get(h) or []
        c = {"critical": 0, "warning": 0, "low": 0, "info": 0}
        unk_n = 0
        checks = []
        seen_chk = set()
        for f in fs:
            if not isinstance(f, dict):
                continue
            sev = normalize_severity(f.get("severity"), default="warning")
            if sev in c:
                c[sev] += 1
            elif sev == "unknown":
                # 1.139.0 (vdi-design-check #20): unknown never increments warning
                unk_n += 1
            chk = str(f.get("check") or f.get("kind") or "").strip()
            if chk and chk not in seen_chk:
                seen_chk.add(chk)
                checks.append(chk)
        total = c["critical"] + c["warning"] + c["low"] + c["info"]
        actionable = (c["critical"] + c["warning"]) > 0
        st = _host_status(c, unk_n)
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
        elif unk_n == 0:
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
        elif st == "unknown":
            unk_hosts += 1

    # Drop any missing entry for a host that actually reported (case-insensitive)
    reported_lower = set(k.lower() for k in hosts_out)
    miss_clean = dict((h, r) for h, r in mm.items() if h not in hosts_out and h.lower() not in reported_lower)
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
        "unknown_hosts": unk_hosts,
        "critical": tot_c,
        "warning": tot_w,
        "low": tot_l,
        "info": tot_i,
        "findings": tot_c + tot_w + tot_l + tot_i,
    }
    return {"hosts": hosts_out, "missing": miss_clean, "totals": totals}


def fleet_summary_lines(by_host, missing=None):
    """1.139.0: Fleet summary lines for roles/site_findings/tasks/report.yml (replaces inline Jinja counting)."""
    hc = host_counts(by_host, missing)
    hosts = hc["hosts"]
    miss = hc["missing"]
    tot = hc["totals"]
    out = []
    head = "%d healthy, %d with findings" % (tot["clear"], tot["with_findings"])
    if miss:
        head += ", %d did not report (unreachable or failed early)" % len(miss)
    out.append(head)
    for h in sorted(hosts, key=str.lower):
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
    for h in sorted(miss, key=str.lower):
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
    return getattr(tm, "local")(base, tz) if tz is not None else base


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


def _clip_prose(text, limit=600):
    """Trim long DISA prose cleanly at a word boundary for 'What to do' lines."""
    s = " ".join(str(text or "").split()).strip()
    if len(s) <= limit:
        return s
    return s[:limit].rsplit(" ", 1)[0].rstrip() + " ..."


def _copyable_hint(text):
    """Offer Copy for recognized diagnostic commands. Keep other operator guidance as text."""
    if not text or any(c in text for c in "`$(){}\\"):
        return False
    # 1.139.0: Copy hints may be pasted into cmd.exe, where single quotes do not protect redirection.
    if any(c in text for c in "<>^%!"):
        return False
    for command in re.split(r"\s*(?:&&|\||;|\n)\s*", text):
        if "&" in command:
            return False
        try:
            args = shlex.split(command)
        except ValueError:
            return False
        if not args:
            return False
        name = args[0].lower()
        if name in ("df", "du", "cat", "echo", "head", "tail", "ls", "stat", "grep",
                    "get-computerinfo", "get-content", "get-item", "get-childitem", "get-service"):
            continue
        if name == "sort" and all(a in ("-h", "-n", "-r", "-u") for a in args[1:]):
            continue
        if name == "systemctl" and args[1:2] in (["status"], ["is-active"], ["is-enabled"], ["show"]):
            continue
        if name == "fapolicyd-cli" and args[1:] in (["--check-trustdb"], ["--list"]):
            continue
        if name == "ausearch" and re.fullmatch(r"ausearch -m FANOTIFY -ts [\w-]+", command):
            continue
        if name == "reg" and args[1:2] == ["query"]:
            continue
        return False
    return True


def _normalize_finding(host, f, ask=None, tz=None):
    """Turn one raw finding dict (health finding or site_findings artifact entry) into a display dict.
    hint = read-only command (gets Copy); fix = prose for a person (never run, no Copy)."""
    raw_sev = f.get("severity")
    sev = normalize_severity(raw_sev, default="warning")
    if sev not in SEVERITIES:
        sev = "info" if sev == "ok" else ("unknown" if sev == "unknown" else "warning")
    chk_raw = str(f.get("check") or f.get("kind") or "").strip()
    chk_label = str(f.get("check_label") or CHECK_KIND.get(chk_raw, chk_raw.replace("_", " ") if chk_raw else "")).strip()
    title = str(f.get("server_summary") or f.get("summary") or f.get("title") or chk_label or f.get("id") or "").strip()
    evidence = str(f.get("note") or f.get("evidence") or "").strip()
    fid = str(f.get("id") or "").strip()
    if not evidence and fid and fid != title:
        evidence = "%s · %s" % (chk_raw or chk_label, fid) if (chk_raw or chk_label) else fid
    # fix is prose (What to do), NEVER gets a Copy button.
    fix = str(f.get("fix") or "").strip()
    # hint is a read-only command a person may paste; gets Copy only when non-empty.
    hint = str(f.get("hint") or "").strip()
    if hint and not _copyable_hint(hint):
        evidence = " · ".join(x for x in (evidence, hint) if x)
        hint = ""
    since_raw = str(f.get("since") or f.get("time") or "").strip()
    since = _fmt_checked(since_raw, tz) if since_raw else ""
    buttons = []
    if isinstance(f.get("buttons"), list) and f["buttons"]:
        buttons = list(f["buttons"])
    elif callable(ask):
        try:
            buttons = list(ask(host, f) or [])
        except Exception:
            buttons = []
    return {
        "severity": sev,
        "severity_label": SEV_LABELS.get(sev, sev.capitalize()),
        "check": chk_raw,
        "kind": str(f.get("kind") or chk_raw or "finding").strip(),
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
    for k in sorted(cap_map, key=str.lower):
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


def _regroup_by_finding_host(by_host, missing, opts):
    """1.139.0 (vdi-design-check #5 / step 14): When a report runs on one play host (such as stigman_health
    on localhost) and findings carry their own 'host' field, regroup findings by each finding's host and
    surface known_hosts (healthy nodes) + unread stigman hosts as missing."""
    known = [str(x).strip() for x in (opts.get("known_hosts") or []) if str(x).strip()]
    want = bool(opts.get("group_by_finding_host"))
    if not want:
        return by_host, missing, {}

    new_by = {}
    new_miss = missing_map(missing)
    unread = {}
    display_names = {k.lower(): k for k in known}
    known_lower = set(display_names)
    for kh in known:
        if kh.lower() not in set(m.lower() for m in new_miss):
            new_by.setdefault(kh, [])

    for outer_host, fs in by_host.items():
        leftover = []
        for f in fs or []:
            if not isinstance(f, dict):
                continue
            fid = str(f.get("id") or "")
            m_miss = _STIGMAN_HOST_MISSING_RE.match(fid)
            fhost = str(f.get("host") or f.get("subject") or "").strip()
            if m_miss:
                mh = fhost or m_miss.group(1).strip()
                if mh:
                    mh = display_names.setdefault(mh.lower(), mh)
                    new_miss[mh] = str(f.get("summary") or "host was not read").strip()
                    unread[mh] = new_miss[mh]
                continue
            if fhost:
                fhost = display_names.setdefault(fhost.lower(), fhost)
                new_by.setdefault(fhost, []).append(f)
            else:
                leftover.append(f)
        if leftover or outer_host.lower() in known_lower:
            new_by.setdefault(display_names.get(outer_host.lower(), outer_host), []).extend(leftover)
    # 1.139.0: An unread host check can coexist with findings from the API.
    for h in unread:
        if not new_by.get(h):
            new_by.pop(h, None)
    return new_by, new_miss, unread


def _cap_host_findings(norm_fs, raw_cap, max_per_kind, kind_caps, artifact_kinds):
    """1.139.0 (vdi-design-check #2 & #13): Cap rendered findings per kind (worst first) and merge omitted
    counts with site_findings_capped without double-counting."""
    cap_merged = {}
    if isinstance(raw_cap, dict):
        for k, v in raw_cap.items():
            try:
                iv = int(v or 0)
            except (TypeError, ValueError):
                continue
            if iv > 0:
                cap_merged[str(k).strip()] = iv

    if not norm_fs:
        return norm_fs, cap_merged

    kept = []
    seen_by_kind = {}
    omitted_by_kind = {}
    for f in norm_fs:
        fk = f.get("kind") or f.get("check") or "finding"
        limit = kind_caps.get(fk, max_per_kind)
        if limit is not None and limit > 0:
            n = seen_by_kind.get(fk, 0)
            if n >= limit:
                omitted_by_kind[fk] = omitted_by_kind.get(fk, 0) + 1
                continue
            seen_by_kind[fk] = n + 1
        kept.append(f)

    for fk, om in omitted_by_kind.items():
        prior = cap_merged.get(fk, 0)
        cap_merged[fk] = prior + om if fk in artifact_kinds else max(prior, om)
    return kept, cap_merged


def server_view(by_host, health=None, missing=None, capped=None, opts=None):
    """1.139.0: Build the server-first report section from per-host findings, health, missing and capped data.

    Arguments:
      by_host: {host: [finding_dict, ...]}
      health:  optional {host: {status, checks, checks_failed, time, ip, teams, ...}}
      missing: optional {host: reason} or [host, ...]
      capped:  optional {host: {kind: n}} (site_findings_capped)
      opts:    optional {title, zone, ips, portal, teams, now, max_per_kind, kind_caps,
                         known_hosts, group_by_finding_host, checklists, checklist_breakdown,
                         host_buttons, host_meta, list_only, meta_col_label, note, counted}

    Returns one section dict for report_servers.html.j2 (compatible with atlas, brief, ops and glass).
    """
    by_host = by_host or {}
    health = health if isinstance(health, dict) else {}
    capped = capped if isinstance(capped, dict) else {}
    o = opts if isinstance(opts, dict) else {}

    # 1.139.0 (vdi-design-check #5 / step 14): regroup single-play-host findings by finding['host']
    by_host, missing, unread = _regroup_by_finding_host(by_host, missing, o)

    tm = _site_time()
    tz, _ = tm.zone(o.get("zone") or "")
    checked_col = tm.column(str(o.get("checked_col") or "Checked (UTC)"), tz)
    ips = o.get("ips") if isinstance(o.get("ips"), dict) else {}
    ask = o.get("portal") if callable(o.get("portal")) else None
    teams = o.get("teams") if isinstance(o.get("teams"), dict) and (o.get("teams").get("names") or []) else None
    tmap = (teams.get("map") or {}) if teams else {}
    tother = (teams.get("other") or "Unassigned") if teams else "Unassigned"
    default_now = str(o.get("now") or "").strip()

    max_per_kind = o.get("max_per_kind")
    try:
        max_per_kind = int(max_per_kind) if max_per_kind is not None else None
    except (TypeError, ValueError):
        max_per_kind = None
    kind_caps = dict(_DEFAULT_KIND_CAPS)
    if isinstance(o.get("kind_caps"), dict):
        for k, v in o["kind_caps"].items():
            try:
                kind_caps[str(k)] = int(v)
            except (TypeError, ValueError):
                pass

    checklists_map = o.get("checklists") if isinstance(o.get("checklists"), dict) else {}
    breakdown_map = o.get("checklist_breakdown") if isinstance(o.get("checklist_breakdown"), dict) else {}
    host_buttons_map = o.get("host_buttons") if isinstance(o.get("host_buttons"), dict) else {}
    host_meta_map = o.get("host_meta") if isinstance(o.get("host_meta"), dict) else {}

    hc = host_counts(by_host, missing)
    hosts_c = hc["hosts"]
    miss_c = hc["missing"]
    totals = dict(hc["totals"])

    # 1.139.0 (vdi-design-check #18): join health strictly on inventory host; surface unmatched entries as 'other'
    other_health = sorted([k for k in health if k not in hosts_c and k not in miss_c], key=str.lower)

    servers = []
    total_capped = 0
    unchecked_hosts = 0

    for h in sorted(hosts_c, key=str.lower):
        cnt = hosts_c[h]
        hmeta = health.get(h) if isinstance(health.get(h), dict) else {}
        ip = str(ips.get(h) or hmeta.get("ip") or "").strip()
        raw_time = str(hmeta.get("time") or default_now).strip()
        checked = _fmt_checked(raw_time, tz)
        # 1.139.0: The attempted checks include failures. Only successful checks establish coverage.
        failed = set(hmeta.get("checks_failed") or [])
        checks_run = list(dict.fromkeys(str(c) for c in hmeta.get("checks", cnt["checks"]) or []
                                        if c not in failed and str(c).strip()))
        reason = unread.get(h, "")
        if not reason and ("checks" in hmeta or failed) and not checks_run:
            reason = "No checks completed successfully." if failed else "No checks ran on this host."

        raw_fs = [f for f in (by_host.get(h) or []) if isinstance(f, dict)]
        norm_fs = [_normalize_finding(h, f, ask=ask, tz=tz) for f in raw_fs]
        norm_fs.sort(key=lambda x: (SEV_ORDER.get(x["severity"], 9), x["check"], x["title"].lower()))

        # 1.139.0 (vdi-design-check #2 & #13): cap displayed findings per kind & merge with site_findings_capped
        norm_fs, cap_merged = _cap_host_findings(norm_fs, capped.get(h), max_per_kind, kind_caps, o.get("artifact_kinds", ()))
        cap_list = _capped_items(cap_merged)
        cap_n = sum(x["count"] for x in cap_list)
        total_capped += cap_n

        # 1.139.0 (vdi-design-check #4 & #22): ignore site_health.status; empty site_checks_run -> Not checked (unknown)
        st = cnt["status"]
        st_label = STATUS_LABELS.get(st, st.capitalize())
        unchecked = bool(reason)
        if unchecked:
            checked = "-"
            unchecked_hosts += 1
            if st == "ok":
                st = "unknown"
                st_label = "Not checked"
                totals["clear"] -= 1
                totals["healthy"] -= 1
                totals["unknown_hosts"] += 1

        if unchecked and not norm_fs and not cap_n:
            worst = reason.rstrip(".")
        elif norm_fs:
            worst = norm_fs[0]["title"]
            if cap_n:
                worst += " (+%d more, not listed)" % cap_n
        elif cap_n:
            worst = "%d more finding(s), not listed" % cap_n
        else:
            worst = "No findings"

        servers.append({
            "host": h,
            "ip": ip,
            "meta": str(host_meta_map.get(h) or hmeta.get("label") or "").strip(),
            "status": st,
            "status_label": st_label,
            "missing": False,
            "unchecked": unchecked,
            "reason": reason,
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
            "checklists": list(checklists_map.get(h) or []),
            "checklist_breakdown": list(breakdown_map.get(h) or []),
            "host_buttons": list(host_buttons_map.get(h) or []),
            "findings": norm_fs,
            "capped": cap_list,
            "capped_total": cap_n,
            "capped_text": "; ".join(x["text"] for x in cap_list),
        })

    for h in sorted(miss_c, key=str.lower):
        reason = miss_c[h]
        hmeta = health.get(h) if isinstance(health.get(h), dict) else {}
        ip = str(ips.get(h) or hmeta.get("ip") or "").strip()
        worst = reason if reason else "Unreachable, or failed before checks ran"
        servers.append({
            "host": h,
            "ip": ip,
            "meta": str(host_meta_map.get(h) or hmeta.get("label") or "").strip(),
            "status": "unknown",
            "status_label": STATUS_LABELS["unknown"],
            "missing": True,
            "unchecked": False,
            "reason": reason,
            "counts": {"critical": 0, "warning": 0, "low": 0, "info": 0, "total": 0},
            "worst": worst,
            "checked": "-",
            "checked_iso": "",
            "checks": [],
            "checklists": [],
            "checklist_breakdown": [],
            "host_buttons": [],
            "findings": [],
            "capped": [],
            "capped_total": 0,
            "capped_text": "",
        })

    # Worst first: critical -> warning -> not reached/checked (unknown) -> low -> info -> ok, then counts and host name
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
        hide_nums = s["missing"] or (s["unchecked"] and not s["findings"])
        rows.append([
            "",
            s["host"],
            s["status_label"],
            "-" if hide_nums else c["critical"],
            "-" if hide_nums else c["warning"],
            "-" if hide_nums else c["low"],
            "-" if hide_nums else c["info"],
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
        if s["missing"] or s["unchecked"]:
            g_text = s["worst"]
        elif c["total"]:
            g_parts = []
            for k, lbl in (("critical", "critical"), ("warning", "warning"), ("low", "low"), ("info", "info")):
                if c[k]:
                    g_parts.append("%d %s" % (c[k], lbl))
            if s["capped_total"]:
                g_parts.append("+%d not listed" % s["capped_total"])
            g_text = ", ".join(g_parts) + " — " + (s["findings"][0]["title"] if s["findings"] else s["worst"])
        else:
            g_text = "No findings"
        glance.append({
            "status": s["status"],
            "label": s["status_label"],
            "name": s["host"],
            "href": "#" + rid,
            "text": g_text,
        })

    # At a glance tiles (4-up grid matching reports-by-server-all.html)
    none_checked = totals["hosts"] == unchecked_hosts
    rep_mask = _meter_mask([not s["missing"] and not s["unchecked"] for s in servers])
    find_mask = _meter_mask([s["counts"]["total"] > 0 for s in servers])
    crit_mask = _meter_mask([s["status"] == "critical" for s in servers])
    clear_mask = _meter_mask([s["status"] == "ok" for s in servers])

    if none_checked and not totals["findings"]:
        # 1.139.0 (vdi-design-check #1): no host reported -> show "N not checked" tiles, NEVER zero green counts
        not_chk = totals["missing"] + unchecked_hosts
        tiles = [
            {
                "label": "Not checked",
                "value": not_chk,
                "note": "0 of %d reporting" % totals["total_hosts"],
                "status": "unknown",
                "icon": "unknown",
                "mask": rep_mask,
            },
            {
                "label": "Findings",
                "value": "-",
                "note": "%d not checked" % not_chk,
                "status": "unknown",
                "icon": "findings",
                "mask": [],
            },
            {
                "label": "Critical servers",
                "value": "-",
                "note": "No hosts checked",
                "status": "unknown",
                "icon": "critical",
                "mask": [],
            },
            {
                "label": "Clear servers",
                "value": "-",
                "note": "No hosts checked",
                "status": "unknown",
                "icon": "ok",
                "mask": [],
            },
        ]
    else:
        checked_n = totals["hosts"] - unchecked_hosts
        checked_note = "%d of %d reporting" % (checked_n, totals["total_hosts"])
        if totals["missing"]:
            checked_note += " · %d not reached" % totals["missing"]
        if unchecked_hosts:
            checked_note += " · %d not checked" % unchecked_hosts
        findings_note = "Across %d server%s" % (totals["with_findings"], "" if totals["with_findings"] == 1 else "s")
        if total_capped:
            findings_note += " · +%d not listed" % total_capped
        findings_tone = "critical" if totals["critical"] else ("warning" if totals["warning"] else ("low" if totals["low"] else ("info" if totals["info"] else "ok")))

        tiles = [
            {
                "label": "Servers checked",
                "value": checked_n,
                "note": checked_note,
                "status": "unknown" if (totals["missing"] or unchecked_hosts) else "ok",
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

    # 1.139.0 (vdi-design-check #20): not-reached / unchecked gives status 'unknown', never 'warning'
    if totals["critical"] or totals["critical_hosts"]:
        sec_status = "critical"
    elif totals["warning"] or totals["warning_hosts"]:
        sec_status = "warning"
    elif none_checked or totals["missing"] or unchecked_hosts or totals.get("unknown_hosts"):
        sec_status = "unknown"
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
    if unchecked_hosts:
        sum_parts.append("%d not checked" % unchecked_hosts)
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
    extra_note = str(o.get("note") or "").strip()
    if other_health:
        oh_note = "Other health entries (not in play host list): %s" % ", ".join(other_health[:10])
        if len(other_health) > 10:
            oh_note += " and %d more" % (len(other_health) - 10)
        extra_note = (extra_note + " " + oh_note).strip() if extra_note else oh_note

    sec = {
        "title": str(o.get("title") or "Servers"),
        "status": sec_status,
        "server_view": True,
        "rail": True,
        "counts": dict(totals, capped=total_capped, unchecked=unchecked_hosts),
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
    if extra_note:
        sec["note"] = extra_note
    if other_health:
        sec["other_health"] = other_health
    if o.get("counted") is False:
        sec["counted"] = False
        for t in sec["tiles"]:
            t["counted"] = False
    return sec


# ---- Step 9: STIG drift (check_stig / win_check_stig) ----------------------------------------------

def _stig_checklist_name(c):
    """Short human label for one stig_compare result's checklist."""
    lbl = str(c.get("label") or "").strip()
    host = str(c.get("host") or "").strip()
    if lbl and host and lbl.lower().startswith(host.lower() + " - "):
        lbl = lbl[len(host) + 3:].strip()
    if lbl:
        return lbl
    key = str(c.get("key") or "").strip()
    if key:
        return key
    bench = str(c.get("benchmark") or "").strip()
    if bench:
        bench = re.sub(r"^xccdf_[^_]+_benchmark_", "", bench)
        return bench.replace("_", " ")[:40]
    return "STIG"


def _stig_rule_text(rec, texts):
    """Look up DISA {check, fix} text for one rule from _stig_texts or the scan item itself."""
    texts = texts if isinstance(texts, dict) else {}
    key = str(rec.get("key") or rec.get("rule") or "")
    m = _SV_RE.search(key)
    sv = m.group(0) if m else key
    v = str(rec.get("v") or "")
    found = texts.get(sv) or texts.get(v) or texts.get(key) or {}
    chk = str(found.get("check") or rec.get("check_text") or "").strip()
    fix = str(found.get("fix") or rec.get("fix_text") or "").strip()
    return chk, fix


def stig_server_view(results, missing=None, texts=None, opts=None, findings_by_host=None):
    """1.139.0 (Step 9 + vdi-design-check #9, #10, #11, #12): Fold multi-checklist STIG drift results
    into one row per server with checklist pills, per-checklist breakdown, and per-rule expander cards
    (DISA check command -> read-only hint with Copy; DISA fix text -> plain 'What to do' prose, no Copy)."""
    o = opts if isinstance(opts, dict) else {}
    texts = texts if isinstance(texts, dict) else {}
    smap = o.get("severity_map") if isinstance(o.get("severity_map"), dict) else {}
    disagree_sev = str(o.get("disagree_severity") or "low").strip().lower()
    if disagree_sev not in ("critical", "warning", "low", "none"):
        disagree_sev = "low"
    portal_url = str(o.get("portal_url") or "").strip().rstrip("/")
    max_per_host = int(o.get("max_per_host") or 50)

    # Fold all checklists per host (case-insensitive)
    grouped = {}
    display_name = {}
    for c in results or []:
        if not isinstance(c, dict):
            continue
        h = str(c.get("host") or "").strip()
        if not h:
            continue
        hk = h.lower()
        display_name.setdefault(hk, h)
        grouped.setdefault(hk, []).append(c)

    st_mod = None
    if texts or any((r.get("check_text") for c in (results or []) if isinstance(c, dict)
                     for g in ("drift", "new", "disagree") for r in (c.get(g) or []) if isinstance(r, dict))):
        try:
            st_mod = _report_filter("stig_text_filters")
        except Exception:
            st_mod = None

    by_host = {}
    checklists_map = {}
    breakdown_map = {}
    host_buttons = {}
    health = dict(o.get("health") or {})

    for hk in sorted(grouped):
        h = display_name[hk]
        cs = grouped[hk]
        fs = []
        cl_pills = []
        cl_break = []
        has_actionable = False

        for c in cs:
            cl_name = _stig_checklist_name(c)
            if cl_name not in cl_pills:
                cl_pills.append(cl_name)

            drift = c.get("drift") or []
            new = c.get("new") or []
            disagree = c.get("disagree") or []
            fixed_open = c.get("fixed_open") or []
            scanned = bool(c.get("scanned"))

            start = len(fs)
            # Non-aggregate host/checklist findings (not-scanned, answers-unused, stigman missing)
            for raw_f in c.get("findings") or []:
                if not isinstance(raw_f, dict):
                    continue
                fid = str(raw_f.get("id") or "")
                if fid.startswith(("stig:drift", "stig:new", "stig:disagree")):
                    continue
                fs.append(dict(raw_f, check="stig", kind="stig_meta", check_label=cl_name))

            def _add_rules(rule_list, group_label, fixed_sev=None):
                for rec in rule_list:
                    if not isinstance(rec, dict):
                        continue
                    rkey = str(rec.get("key") or rec.get("stigid") or "").strip()
                    rtitle = str(rec.get("title") or rkey).strip()
                    raw_s = str(rec.get("severity") or "").strip().lower()
                    cat = CAT_LABELS.get(raw_s, raw_s.upper() if raw_s else "")
                    if fixed_sev is not None:
                        sev = fixed_sev
                    else:
                        mapped = str(smap.get(raw_s) or rec.get("level") or "").strip().lower()
                        sev = normalize_severity(mapped or raw_s, default="warning")
                    chk_txt, fix_txt = _stig_rule_text(rec, texts)
                    cmd = ""
                    if chk_txt and st_mod is not None:
                        try:
                            cmd = str(st_mod.commands_of(chk_txt, registry=True) or "").strip()
                        except Exception:
                            cmd = ""
                    fix_prose = _clip_prose(fix_txt, 600) if fix_txt else ""
                    if not fix_prose and group_label == "fixed on host, still Open in STIG Manager":
                        fix_prose = "Update the review in STIG Manager to Not a Finding (this job never writes to STIG Manager)."
                    ev_parts = [p for p in (cl_name, cat, group_label, str(rec.get("stigid") or "").strip()) if p]
                    fs.append({
                        "check": "stig",
                        "kind": "stig_rule",
                        "check_label": "%s · %s" % (cl_name, cat) if cat else cl_name,
                        "id": "%s:%s" % (cl_name, rkey),
                        "severity": sev,
                        "summary": "%s: %s (%s)" % (rkey, rtitle, group_label) if rkey and rkey not in rtitle else "%s (%s)" % (rtitle, group_label),
                        "note": " · ".join(ev_parts),
                        "fix": fix_prose,
                        "hint": cmd,
                    })

            _add_rules(drift, "drifted from checklist")
            _add_rules(new, "new failure")
            if disagree_sev != "none":
                _add_rules(disagree, "disagrees with checklist", fixed_sev=disagree_sev)
            _add_rules(fixed_open, "fixed on host, still Open in STIG Manager", fixed_sev="low")

            # Checklist breakdown badge status
            cl_st = min((normalize_severity(f["severity"]) for f in fs[start:]),
                        key=SEV_ORDER.get, default="ok" if scanned else "unknown")
            if cl_st in ("critical", "warning"):
                has_actionable = True
            cl_break.append({
                "name": cl_name,
                "status": cl_st,
                "scanned": scanned,
                "drift": len(drift),
                "new": len(new),
                "disagree": len(disagree),
                "fixed_open": len(fixed_open),
            })

            # Preserve scan timestamp in health metadata when available
            if c.get("checklist_time") and h not in health:
                health[h] = {"time": c.get("checklist_time"), "checks": ["stig"]}

        by_host[h] = fs
        checklists_map[h] = cl_pills
        breakdown_map[h] = cl_break
        if has_actionable and portal_url:
            link = _report_filter("portal_filters").portal_link(portal_url, "stig_recheck", {"target": h})
            if link:
                host_buttons[h] = [{"label": "Recheck host", "url": link}]

    # 1.139.0: Replace STIG aggregates only for hosts with checklist results.
    for host, findings in (findings_by_host or {}).items():
        hk = host.lower()
        bucket = by_host.setdefault(display_name.get(hk, host), [])
        bucket.extend(f for f in findings or [] if hk not in grouped or f.get("check") != "stig")

    sv_opts = dict(o)
    sv_opts.setdefault("title", "Servers")
    sv_opts["checklists"] = checklists_map
    sv_opts["checklist_breakdown"] = breakdown_map
    sv_opts["host_buttons"] = host_buttons
    sv_opts["kind_caps"] = dict(sv_opts.get("kind_caps") or {}, stig_rule=max_per_host)
    return server_view(by_host, health=health, missing=missing, opts=sv_opts)


# ---- Step 10: fapolicyd (fapolicyd.yml / fapolicyd_denials.yml) ------------------------------------

def fapolicyd_server_view(hostvars_list, names, opts=None):
    """1.139.0 (Step 10 + vdi-design-check #13): One row per server for fapolicyd / fapolicyd_denials.
    - Permissive 'would deny' entries are low (never amber).
    - Denials capped per host (default 10) with 'and N more of fapolicyd_deny, not listed'.
    - Read-only ausearch / fapolicyd-cli / rpm hints get Copy; proposed allow rules are prose only (no Copy).
    - Portal buttons folded into the host's finding cards."""
    o = opts if isinstance(opts, dict) else {}
    since = shlex.quote(str(o.get("since") or "today").strip())
    max_denials = int(o.get("max_denials") or 10)
    portal = o.get("portal") if isinstance(o.get("portal"), dict) else None

    # Optional portal button builder from finding_filters
    btn_fn = None
    if portal and portal.get("url") and str(portal.get("job") or "").isdigit() and portal.get("actions"):
        ff_mod = _report_filter("finding_filters")
        btn_fn = lambda e: ff_mod.finding_buttons(e, portal["actions"], portal["url"], str(portal["job"]))

    by_host = {}
    health = {}
    missing = {}
    capped = {}
    skipped = []

    for hv, name in zip(hostvars_list or [], names or []):
        h = str(name or "").strip()
        if not h:
            continue
        hv = hv if hasattr(hv, "get") else {}
        un = str(hv.get("site_host_unreached") or "").strip()
        if un:
            missing[h] = un
            continue
        if hv.get("site_host_skipped"):
            skipped.append(h)
            continue

        ff = hv.get("_ff") if isinstance(hv.get("_ff"), dict) else {}
        fr = hv.get("_fr") if isinstance(hv.get("_fr"), dict) else {}
        art = hv.get("_fa_sf") if isinstance(hv.get("_fa_sf"), dict) else {}
        act = hv.get("act_triage_result") if isinstance(hv.get("act_triage_result"), dict) else {}

        # Map artifact entries by path/program so portal buttons attach inside each finding card
        art_by_key = {}
        for e in art.get("entries") or []:
            if isinstance(e, dict):
                d = e.get("data") or {}
                k = (str(d.get("program") or ""), str(d.get("path") or ""))
                art_by_key[k] = e
        if art.get("capped"):
            capped[h] = dict(art["capped"])

        fs = []
        mode = str(ff.get("mode") or fr.get("mode") or "").strip().lower()
        permissive = mode == "permissive"

        if ff.get("error") or fr.get("error") or "not read" in (ff.get("state"), fr.get("state")):
            missing[h] = ff.get("error") or fr.get("error") or "fapolicyd was not read"
            continue
        blocks = fr.get("blocks") or []
        if not ff and not fr:
            reader = _report_filter("fapolicyd_filters")
            host = reader.host_result(h, hv)
            if host["state"] == "not read":
                missing[h] = "No fapolicyd result for this host"
                continue
            if host["state"] == "read":
                blocks = [dict(group, exe=exe, path=path) for (exe, path), group in reader._groups(hv).items()]
                if host["count"] and not blocks:
                    fs.append({"check": "fapolicyd", "severity": "warning",
                               "summary": "%d denials in the audit log" % host["count"]})
                for service in ("active", "auditd"):
                    if host[service] != "active":
                        fs.append({"check": "fapolicyd", "severity": "critical" if service == "active" else "warning",
                                   "summary": "%s is %s" % ("fapolicyd" if service == "active" else service, host[service])})
        # Service / health issues
        # 1.139.0: The denial reader also reports service activity.
        st_ff = str(ff.get("active") or fr.get("active") or ff.get("state") or "").strip().lower()
        if st_ff in ("down", "failed", "still down", "inactive"):
            fs.append({
                "check": "fapolicyd",
                "kind": "fapolicyd_health",
                "id": "fapolicyd:service",
                "severity": "critical",
                "summary": "fapolicyd is %s%s" % (st_ff, (": " + str(ff.get("error") or "")) if ff.get("error") else ""),
                "note": "Check fapolicyd service status and trust database integrity.",
                "fix": "Resolve the trust or configuration fault and verify fapolicyd starts cleanly.",
                "hint": "systemctl status fapolicyd --no-pager && fapolicyd-cli --check-trustdb",
            })
        for prob in ff.get("problems") or []:
            ptxt = str(prob.get("summary") or prob.get("what") or prob) if isinstance(prob, dict) else str(prob)
            if ptxt.strip():
                fs.append({
                    "check": "fapolicyd",
                    "kind": "fapolicyd_health",
                    "id": "fapolicyd:prob",
                    "severity": normalize_severity(prob.get("severity") if isinstance(prob, dict) else "warning", default="warning"),
                    "summary": ptxt.strip(),
                    "fix": "Review fapolicyd health configuration and route changes through security approval.",
                    "hint": "fapolicyd-cli --check-trustdb",
                })

        # Denials / blocks (permissive 'would deny' -> low, NEVER amber!)
        if not blocks and act.get("denials"):
            blocks = act.get("denials") or []
        for idx, b in enumerate(blocks):
            if not isinstance(b, dict):
                continue
            prog = str(b.get("exe") or b.get("program") or b.get("comm") or "?").strip()
            path = str(b.get("path") or b.get("obj") or b.get("file") or "?").strip()
            cnt_b = int(b.get("count") or 1)
            b_perm = b.get("kind") in ("would", "would deny") or bool(b.get("permissive")) or permissive or ("would" in str(b.get("action") or "").lower())
            sev = "low" if b_perm else normalize_severity(b.get("severity") or "warning", default="warning")
            verb = "would deny" if b_perm else "denied"
            prop = str(b.get("proposal") or b.get("rule") or b.get("line") or "").strip()
            fix_txt = ("Review and approve change via fapolicyd workflow (do not apply unapproved rules): %s" % prop) if prop else (
                "Verify whether %s legitimately requires %s; if approved by the ISSO, allow via the fapolicyd workflow." % (prog, path)
            )
            e_match = art_by_key.get((prog, path))
            btns = btn_fn(e_match) if (btn_fn and e_match) else []
            ev = "%s -> %s (%d event%s%s)" % (prog, path, cnt_b, "" if cnt_b == 1 else "s", ", permissive" if b_perm else "")
            hint_cmd = "ausearch -m FANOTIFY -ts %s | tail -n 20" % since
            fs.append({
                "check": "fapolicyd",
                "kind": "fapolicyd_deny",
                "id": "fapolicyd:deny:%d" % idx,
                "severity": sev,
                "summary": "fapolicyd %s %s accessing %s" % (verb, os.path.basename(prog) or prog, path),
                "note": ev,
                "fix": fix_txt,
                "hint": hint_cmd,
                "buttons": btns,
            })

        # ACT triage error on fapolicyd_denials playbook
        if act and str(act.get("status") or "").lower() == "error":
            fs.append({
                "check": "fapolicyd",
                "kind": "fapolicyd_act",
                "id": "fapolicyd:act-error",
                "severity": "warning",
                "summary": "ACT triage error: %s" % (act.get("stop_reason") or act.get("summary") or "see job output"),
                "fix": "Inspect the ACT job output and re-run once resolved.",
                "hint": "",
            })

        ip = str((hv.get("ansible_default_ipv4") or {}).get("address") or hv.get("ansible_host") or "").strip()
        by_host[h] = fs
        health[h] = {"checks": ["fapolicyd"], "ip": ip, "time": str(o.get("now") or "")}

    sv_opts = dict(o)
    sv_opts.setdefault("title", "Servers")
    sv_opts["kind_caps"] = dict(sv_opts.get("kind_caps") or {}, fapolicyd_deny=max_denials)
    if skipped:
        sk_note = "%d left out on purpose." % len(skipped)
        sv_opts["note"] = (str(sv_opts.get("note") or "") + " " + sk_note).strip()
    sec = server_view(by_host, health=health, missing=missing, capped=capped, opts=sv_opts)
    if skipped:
        sec["summary_text"] += " · %d left out on purpose" % len(skipped)
    return sec


# ---- Step 11: List-only server views (sw_inventory, solarwinds_report) -----------------------------

def _list_only_section(title, servers_in, tiles, opts=None):
    """Build a list-only server_view section (no Crit/Warn/Low/Info columns: sw_inventory, SolarWinds)."""
    o = opts if isinstance(opts, dict) else {}
    tm = _site_time()
    tz, _ = tm.zone(o.get("zone") or "")
    checked_col = tm.column(str(o.get("checked_col") or "Read (UTC)"), tz)
    meta_col = str(o.get("meta_col_label") or "Details")
    summary_col = str(o.get("summary_col_label") or "Summary")

    # Sort: Not read / Not certain (unknown) -> changed/alerted -> ok, then host.lower()
    servers = list(servers_in)
    servers.sort(key=lambda s: (
        0 if s["status"] == "unknown" else (1 if s.get("changed") else 2),
        s["host"].lower(),
    ))

    columns = ["", "Server", "Status", meta_col, summary_col, checked_col]
    column_titles = ["Open", "Server", "Status", meta_col, summary_col, checked_col]
    kinds = ["x", "wrap", "nw", "mid", "long", "nw"]
    rows, row_status, row_tags, details, glance = [], [], [], [], []

    for idx, s in enumerate(servers):
        rid = "srv-%d" % idx
        rows.append(["", s["host"], s["status_label"], s.get("meta") or "-", s["worst"], s["checked"] or "-"])
        row_status.append(s["status"])
        tag_data = {"sev": s["status"], "host": s["host"]}
        if s.get("changed"):
            tag_data["changed"] = "1"
        row_tags.append({"id": rid, "data": tag_data})
        details.append({
            "host": s["host"],
            "status": s["status"],
            "status_label": s["status_label"],
            "missing": s["missing"],
            "reason": s["reason"],
            "findings": s["findings"],
            "capped": s["capped"],
            "capped_text": s["capped_text"],
        })
        glance.append({
            "status": s["status"],
            "label": s["status_label"],
            "name": s["host"],
            "href": "#" + rid,
            "text": s["worst"],
        })

    sec_status = str(o.get("status") or ("unknown" if any(s["status"] == "unknown" for s in servers) else "ok"))
    sec = {
        "title": title,
        "status": sec_status,
        "server_view": True,
        "list_only": True,
        "meta_col_label": meta_col,
        "summary_col_label": summary_col,
        "changed_chip_label": str(o.get("changed_chip_label") or "Changed"),
        "rail": True,
        "counts": dict(o.get("counts") or {}),
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
        "summary_text": str(o.get("summary_text") or ""),
    }
    if o.get("note"):
        sec["note"] = str(o["note"])
    if o.get("counted") is False:
        sec["counted"] = False
        for t in sec["tiles"]:
            t["counted"] = False
    return sec


def sw_inventory_server_view(data, opts=None):
    """1.139.0 (Step 11 + vdi-design-check #14): List-only server view for Linux software inventory.
    No Crit/Warn/Low/Info counts; Read / Not read; package changes in expander; 'N left out on purpose'."""
    o = opts if isinstance(opts, dict) else {}
    d = data.get("server_view_input") if isinstance(data, dict) and isinstance(data.get("server_view_input"), dict) else (data if isinstance(data, dict) else {})
    tm = _site_time()
    tz, _ = tm.zone(o.get("zone") or "")
    hosts_in = d.get("hosts") or []
    not_read = d.get("not_read") or []
    skipped = d.get("skipped") or d.get("skipped_hosts") or []
    chg_map = d.get("changes_by_host") if isinstance(d.get("changes_by_host"), dict) else {}
    first_seen = set(d.get("first_seen") or [])
    default_read_at = str(d.get("read_at") or "").strip()
    max_changes = int(o.get("max_changes") or 30)

    servers = []
    total_pkgs = 0
    changed_hosts = 0

    for h in hosts_in:
        if not isinstance(h, dict):
            continue
        name = str(h.get("host") or "").strip()
        if not name:
            continue
        pkgs = int(h.get("packages") or 0)
        total_pkgs += pkgs
        os_str = str(h.get("os") or "").strip()
        kern = str(h.get("kernel") or "").strip()
        meta = " · ".join(p for p in (os_str, kern) if p) or ("%d packages" % pkgs)
        raw_t = str(h.get("read_at") or default_read_at).strip()
        checked = _fmt_checked(raw_t, tz)
        chgs = chg_map.get(name) or []
        is_chg = len(chgs) > 0
        if is_chg:
            changed_hosts += 1
        fs = []
        cap_list = []
        cap_n = 0
        if chgs:
            shown = chgs[:max_changes]
            if len(chgs) > max_changes:
                cap_n = len(chgs) - max_changes
                cap_list = [{"kind": "package_change", "count": cap_n, "text": "and %d more of package_change, not listed" % cap_n}]
            added = sum(1 for c in chgs if isinstance(c, dict) and str(c.get("change") or c.get("action") or "").lower() in ("added", "installed", "add"))
            removed = sum(1 for c in chgs if isinstance(c, dict) and str(c.get("change") or c.get("action") or "").lower() in ("removed", "erased", "remove"))
            updated = len(chgs) - added - removed
            cparts = []
            if added:
                cparts.append("+%d added" % added)
            if updated:
                cparts.append("%d updated" % updated)
            if removed:
                cparts.append("-%d removed" % removed)
            worst = ", ".join(cparts) if cparts else ("%d package change(s)" % len(chgs))
            if cap_n:
                worst += " (+%d more, not listed)" % cap_n
            for c in shown:
                if isinstance(c, dict):
                    act = str(c.get("change") or c.get("action") or "changed").strip()
                    pkg = str(c.get("name") or c.get("package") or "").strip()
                    frm = str(c.get("old_evr") or c.get("before") or c.get("old") or "").strip()
                    to = str(c.get("new_evr") or c.get("after") or c.get("new") or c.get("version") or "").strip()
                    ev = "%s -> %s" % (frm, to) if (frm and to) else (to or frm)
                    fs.append({
                        "severity": "info", "severity_label": act.capitalize(), "check": "sw_inventory",
                        "kind": "package_change", "check_label": "Package", "id": pkg,
                        "title": "%s: %s" % (act.capitalize(), pkg) if pkg else act.capitalize(),
                        "evidence": ev, "since": "", "fix": "", "hint": "", "buttons": [],
                    })
                else:
                    fs.append({
                        "severity": "info", "severity_label": "Changed", "check": "sw_inventory",
                        "kind": "package_change", "check_label": "Package", "id": "",
                        "title": str(c), "evidence": "", "since": "", "fix": "", "hint": "", "buttons": [],
                    })
        elif name in first_seen:
            worst = "First inventory (%d packages)" % pkgs
        else:
            worst = "No package changes (%d packages)" % pkgs
        if h.get("repo_note"):
            fs.append({
                "severity": "info", "severity_label": "Info", "check": "sw_inventory",
                "kind": "repo_note", "check_label": "Repositories", "id": "repo",
                "title": "Repository note", "evidence": str(h["repo_note"]), "since": "", "fix": "", "hint": "", "buttons": [],
            })

        servers.append({
            "host": name, "ip": "", "meta": meta, "status": "ok", "status_label": "Read",
            "missing": False, "unchecked": False, "changed": is_chg, "reason": "",
            "counts": {"critical": 0, "warning": 0, "low": 0, "info": len(fs), "total": len(fs)},
            "worst": worst, "checked": checked, "checked_iso": raw_t, "checks": ["sw_inventory"],
            "checklists": [], "checklist_breakdown": [], "host_buttons": [],
            "findings": fs, "capped": cap_list, "capped_total": cap_n,
            "capped_text": "; ".join(x["text"] for x in cap_list),
        })

    for b in not_read:
        if not isinstance(b, dict):
            continue
        name = str(b.get("host") or "").strip()
        if not name:
            continue
        reason = str(b.get("reason") or "Not read").strip()
        servers.append({
            "host": name, "ip": "", "meta": "-", "status": "unknown", "status_label": "Not read",
            "missing": True, "unchecked": False, "changed": False, "reason": reason,
            "counts": {"critical": 0, "warning": 0, "low": 0, "info": 0, "total": 0},
            "worst": reason, "checked": "-", "checked_iso": "", "checks": [],
            "checklists": [], "checklist_breakdown": [], "host_buttons": [],
            "findings": [], "capped": [], "capped_total": 0, "capped_text": "",
        })

    n_read = len([s for s in servers if not s["missing"]])
    n_bad = len([s for s in servers if s["missing"]])
    n_skip = len(skipped)
    n_tot = n_read + n_bad

    read_note = "%d of %d targets" % (n_read, n_tot)
    if n_skip:
        read_note += " · %d left out on purpose" % n_skip
    tiles = [
        {"label": "Hosts read", "value": n_read, "note": read_note,
         "status": "unknown" if (n_read == 0 and n_bad > 0) else "ok",
         "icon": "server", "mask": _meter_mask([not s["missing"] for s in servers])},
        {"label": "Not read", "value": n_bad, "note": "Unreachable or failed" if n_bad else "None",
         "status": "unknown" if n_bad else "ok",
         "icon": "unknown", "mask": _meter_mask([s["missing"] for s in servers])},
        {"label": "Hosts changed", "value": changed_hosts, "note": "Since last baseline" if changed_hosts else "No changes",
         "status": "info" if changed_hosts else "ok",
         "icon": "findings", "mask": _meter_mask([s.get("changed") for s in servers])},
        {"label": "Total packages", "value": total_pkgs, "note": "Across %d host%s" % (n_read, "" if n_read == 1 else "s"),
         "status": "info" if n_read else "unknown", "icon": "ok", "mask": []},
    ]

    parts = ["%d read" % n_read]
    if n_bad:
        parts.append("%d not read" % n_bad)
    if changed_hosts:
        parts.append("%d changed" % changed_hosts)
    if n_skip:
        parts.append("%d left out on purpose" % n_skip)
    summary_text = "%d server%s: %s" % (n_tot, "" if n_tot == 1 else "s", ", ".join(parts))
    note = ("%d host%s left out on purpose (skipped)." % (n_skip, "" if n_skip == 1 else "s")) if n_skip else ""

    return _list_only_section(
        str(o.get("title") or "Servers"),
        servers,
        tiles,
        opts={
            "zone": o.get("zone") or "",
            "checked_col": "Read (UTC)",
            "meta_col_label": "OS / Kernel",
            "summary_col_label": "Changes / packages",
            "changed_chip_label": "Changed",
            "summary_text": summary_text,
            "note": note,
            "status": "unknown" if n_bad else "ok",
            "counts": {"read": n_read, "not_read": n_bad, "changed": changed_hosts, "skipped": n_skip, "packages": total_pkgs},
        },
    )


def _swr_skipped(host, patterns):
    """Match solarwinds_report_skip_hosts patterns (same rules as solarwinds_report_filters._skipped)."""
    if not patterns:
        return False
    if isinstance(patterns, str):
        pats = [p.strip() for p in re.split(r"[,\s]+", patterns) if p.strip()]
    else:
        pats = [str(p).strip() for p in patterns if str(p).strip()]
    hl = str(host or "").lower()
    short = hl.split(".", 1)[0]
    for p in pats:
        pl = p.lower()
        if pl == "*":
            continue
        if "." in pl:
            if fnmatch.fnmatchcase(hl, pl):
                return True
        else:
            if fnmatch.fnmatchcase(hl, pl) or fnmatch.fnmatchcase(short, pl):
                return True
    return False


def swr_server_view(read, findings_by_host=None, opts=None):
    """1.139.0 (Step 11 + vdi-design-check #15): List-only SolarWinds server view (counted: False).
    - Read / Not read / Not certain (uncertain hosts get 'Not certain', status 'unknown', NEVER green,
      and are kept OUT of the matched/read counts).
    - No Crit/Warn/Low/Info columns."""
    o = opts if isinstance(opts, dict) else {}
    r = read if isinstance(read, dict) else {}
    tm = _site_time()
    tz, _ = tm.zone(o.get("zone") or "")
    skip_pats = o.get("skip_hosts") or []
    snap_at = str(r.get("read_at") or "").strip()
    age_min = r.get("age_min")
    snap_disp = _fmt_checked(snap_at, tz) if snap_at else "-"
    vol_pct = float(o.get("volume_pct") or 90)
    syslog_by = ((r.get("syslog") or {}).get("by_node") or {}) if isinstance(r.get("syslog"), dict) else {}

    servers = []
    matched_n = 0
    uncertain_n = 0
    not_in_n = 0
    skipped_n = 0
    alert_hosts = 0
    total_alerts = 0
    seen_hosts = set()

    for h in r.get("hosts") or []:
        if not isinstance(h, dict):
            continue
        name = str(h.get("host") or "").strip()
        if not name:
            continue
        if not h.get("node") and _report_filter("solarwinds_report_filters")._skipped(name, skip_pats, h.get("fqdn")):
            skipped_n += 1
            continue
        seen_hosts.add(name.lower())

        # 1.139.0 (vdi-design-check #15): uncertain host -> "Not certain", status "unknown", NEVER green, OUT of matched count
        if h.get("uncertain"):
            uncertain_n += 1
            cands = h.get("candidates") or []
            cand_names = [str(c.get("name") or c.get("dns") or c.get("ip") or c) if isinstance(c, dict) else str(c) for c in cands]
            ev = ("Candidate Orion nodes: " + ", ".join(cand_names)) if cand_names else "Multiple Orion nodes share this short name; set an FQDN or ansible_host to disambiguate."
            ev = h.get("unmatched_why") or ev
            servers.append({
                "host": name, "ip": str(h.get("ip") or ""), "meta": "Match not certain",
                "status": "unknown", "status_label": "Not certain",
                "missing": False, "unchecked": True, "changed": False,
                "reason": ev,
                "counts": {"critical": 0, "warning": 0, "low": 0, "info": 0, "total": 0},
                "worst": "Not certain: %d Orion candidates" % len(cand_names) if cand_names else "Not certain (ambiguous Orion match)",
                "checked": snap_disp, "checked_iso": snap_at, "checks": [],
                "checklists": [], "checklist_breakdown": [], "host_buttons": [],
                "findings": [{
                    "severity": "unknown", "severity_label": "Not certain", "check": "solarwinds",
                    "kind": "solarwinds_uncertain", "check_label": "SolarWinds", "id": "swr:uncertain",
                    "title": "Match to SolarWinds Orion is not certain",
                    "evidence": ev, "since": "", "fix": "Give this host an FQDN in inventory (or ansible_host) so it matches one Orion node unambiguously.",
                    "hint": "", "buttons": [],
                }],
                "capped": [], "capped_total": 0, "capped_text": "",
            })
            continue

        node = h.get("node")
        if not isinstance(node, dict):
            not_in_n += 1
            servers.append({
                "host": name, "ip": str(h.get("ip") or ""), "meta": "Not in Orion",
                "status": "unknown", "status_label": "Not read",
                "missing": True, "unchecked": False, "changed": False,
                "reason": "Host not found in the SolarWinds snapshot.",
                "counts": {"critical": 0, "warning": 0, "low": 0, "info": 0, "total": 0},
                "worst": "Not in SolarWinds snapshot",
                "checked": snap_disp, "checked_iso": snap_at, "checks": [],
                "checklists": [], "checklist_breakdown": [], "host_buttons": [],
                "findings": [], "capped": [], "capped_total": 0, "capped_text": "",
            })
            continue

        matched_n += 1
        nname = str(node.get("name") or name)
        nstat = str(node.get("status") or "Up")
        nid = node.get("node_id")
        alerts = (h.get("alerts") or []) if r.get("alerts_read", True) else []
        apps = [a for a in (h.get("apps") or []) if isinstance(a, dict) and a.get("status") in ("Down", "Warning", "Critical")]
        vols = [v for v in (h.get("volumes") or []) if isinstance(v, dict) and float(v.get("used_pct") or v.get("pct") or 0) >= vol_pct]
        sys_info = syslog_by.get(nid) or syslog_by.get(str(nid)) or {}
        sys_n = int(sys_info.get("count") or 0)

        fs = []
        for al in alerts:
            if not isinstance(al, dict):
                continue
            al_sev = _report_filter("solarwinds_report_filters")._sev(al)
            fs.append({
                "severity": al_sev, "severity_label": SEV_LABELS[al_sev], "check": "solarwinds",
                "kind": "swr_alert", "check_label": "Orion alert", "id": "",
                "title": str(al.get("name") or "SolarWinds alert"),
                "evidence": str(al.get("entity_caption") or al.get("message") or ""),
                "since": str(al.get("since") or ""), "fix": "", "hint": "", "buttons": [],
            })
        for ap in apps:
            fs.append({
                "severity": "warning", "severity_label": "SAM", "check": "solarwinds",
                "kind": "swr_app", "check_label": "SAM application", "id": "",
                "title": "%s is %s" % (ap.get("app") or ap.get("name") or "Application", ap.get("status") or "Down"),
                "evidence": ", ".join(str(c.get("name") or "") for c in (ap.get("components_not_up") or [])),
                "since": "", "fix": "", "hint": "", "buttons": [],
            })
        for vl in vols:
            fs.append({
                "severity": "warning", "severity_label": "Volume", "check": "solarwinds",
                "kind": "swr_vol", "check_label": "Volume", "id": "",
                "title": "%s at %.0f%%" % (vl.get("caption") or vl.get("mount") or "Volume", float(vl.get("used_pct") or vl.get("pct") or 0)),
                "evidence": "", "since": "", "fix": "", "hint": "", "buttons": [],
            })
        if sys_n:
            top_msgs = "; ".join(str(t.get("message") or "")[:90] for t in (sys_info.get("top") or [])[:3])
            fs.append({
                "severity": "info", "severity_label": "Syslog", "check": "solarwinds",
                "kind": "swr_syslog", "check_label": "Log Analyzer", "id": "",
                "title": "%d syslog error%s" % (sys_n, "" if sys_n == 1 else "s"),
                "evidence": top_msgs, "since": "", "fix": "", "hint": "", "buttons": [],
            })

        total_alerts += len(alerts)
        has_flag = bool(alerts or apps or vols or nstat.lower() not in ("up", "ok"))
        if has_flag:
            alert_hosts += 1
        sum_bits = []
        if alerts:
            sum_bits.append("%d alert%s" % (len(alerts), "" if len(alerts) == 1 else "s"))
        if apps:
            sum_bits.append("%d app%s down" % (len(apps), "" if len(apps) == 1 else "s"))
        if vols:
            sum_bits.append("%d volume%s >= %.0f%%" % (len(vols), "" if len(vols) == 1 else "s", vol_pct))
        if sys_n:
            sum_bits.append("%d syslog" % sys_n)
        worst = ", ".join(sum_bits) if sum_bits else "Orion node %s · %s" % (nstat, "no alerts" if r.get("alerts_read", True) else "alerts not read")

        servers.append({
            "host": name, "ip": str(node.get("ip") or h.get("ip") or ""),
            "meta": "%s (%s)" % (nname, nstat),
            "status": "ok", "status_label": "Read",
            "missing": False, "unchecked": False, "changed": has_flag, "reason": "",
            "counts": {"critical": 0, "warning": 0, "low": 0, "info": len(fs), "total": len(fs)},
            "worst": worst, "checked": snap_disp, "checked_iso": snap_at, "checks": ["solarwinds"],
            "checklists": [], "checklist_breakdown": [], "host_buttons": [],
            "findings": fs, "capped": [], "capped_total": 0, "capped_text": "",
        })

    for mh, mreason in missing_map(o.get("missing")).items():
        if mh.lower() in seen_hosts or _swr_skipped(mh, skip_pats):
            continue
        not_in_n += 1
        servers.append({
            "host": mh, "ip": "", "meta": "-", "status": "unknown", "status_label": "Not read",
            "missing": True, "unchecked": False, "changed": False,
            "reason": mreason or "Host did not report in this run.",
            "counts": {"critical": 0, "warning": 0, "low": 0, "info": 0, "total": 0},
            "worst": mreason or "Did not report",
            "checked": "-", "checked_iso": "", "checks": [],
            "checklists": [], "checklist_breakdown": [], "host_buttons": [],
            "findings": [], "capped": [], "capped_total": 0, "capped_text": "",
        })

    age_text = ("%d min old" % int(age_min)) if age_min is not None else (snap_disp or "Not read")
    match_note = "Matched unambiguously"
    if uncertain_n:
        match_note += " · %d not certain (excluded)" % uncertain_n
    tiles = [
        {"label": "Matched in Orion", "value": matched_n, "note": match_note,
         "status": "ok" if matched_n else "unknown", "icon": "server",
         "mask": _meter_mask([s["status"] == "ok" for s in servers])},
        {"label": "Not in Orion", "value": not_in_n, "note": "Missing or not read" if not_in_n else "None",
         "status": "unknown" if not_in_n else "ok", "icon": "unknown",
         "mask": _meter_mask([s["missing"] for s in servers])},
        {"label": "Not certain" if uncertain_n else "Orion alerts",
         "value": uncertain_n if uncertain_n else (total_alerts if r.get("alerts_read", True) else "-"),
         "note": "Excluded from counts · ambiguous match" if uncertain_n else ("Across %d host%s" % (alert_hosts, "" if alert_hosts == 1 else "s")),
         "status": "unknown" if uncertain_n or not r.get("alerts_read", True) else ("warning" if total_alerts else "ok"),
         "icon": "unknown" if uncertain_n else "findings",
         "mask": _meter_mask([s["status_label"] == "Not certain" for s in servers]) if uncertain_n else _meter_mask([s.get("changed") for s in servers])},
        {"label": "Snapshot", "value": age_text, "note": str(r.get("orion") or "SolarWinds snapshot"),
         "status": "info" if r.get("ok", True) else "unknown", "icon": "ok", "mask": []},
    ]

    parts = ["%d read" % matched_n]
    if not_in_n:
        parts.append("%d not read" % not_in_n)
    if uncertain_n:
        parts.append("%d not certain" % uncertain_n)
    if skipped_n:
        parts.append("%d left out on purpose" % skipped_n)
    summary_text = "%d host%s: %s" % (len(servers), "" if len(servers) == 1 else "s", ", ".join(parts))

    return _list_only_section(
        str(o.get("title") or "SolarWinds by server"),
        servers,
        tiles,
        opts={
            "zone": o.get("zone") or "",
            "checked_col": "Snapshot (UTC)",
            "meta_col_label": "Orion node",
            "summary_col_label": "SolarWinds summary",
            "changed_chip_label": "With alerts",
            "summary_text": summary_text,
            "note": ("%d host%s left out on purpose (solarwinds_report_skip_hosts)." % (skipped_n, "" if skipped_n == 1 else "s")) if skipped_n else "",
            "status": "unknown" if (not_in_n or uncertain_n) else "ok",
            "counted": False,
            "counts": {"matched": matched_n, "not_read": not_in_n, "uncertain": uncertain_n, "alerts": total_alerts, "skipped": skipped_n},
        },
    )


# ---- Step 12: Certificate inventory host rollup ----------------------------------------------------

def _cert_sev(days, raw_status, warn_days, notice_days, crit_days):
    """Map one certificate's days/status to canonical severity (expired -> critical)."""
    # 1.139.0: Either the reported status or the expiry window can raise severity.
    status = normalize_severity(raw_status, default="")
    try:
        d = int(days)
    except (TypeError, ValueError):
        return status or "unknown"
    if d < 0 or d <= crit_days:
        expiry = "critical"
    elif d <= warn_days:
        expiry = "warning"
    elif d <= notice_days:
        expiry = "info"
    else:
        expiry = "ok"
    return min(status or expiry, expiry, key=SEV_ORDER.get)


def cert_host_rollup(certs, opts=None):
    """1.139.0 (Step 12 + vdi-design-check #8): Build the host rollup section directly from a certificate
    record list ({host, days, expires, subject, where, status}) + optional missing hosts.
    One line per host ('N certificates, worst D days'), clicking a host filters the certificate table below."""
    o = opts if isinstance(opts, dict) else {}
    warn_days = int(o.get("warn_days") if o.get("warn_days") is not None else 30)
    notice_days = int(o.get("notice_days") if o.get("notice_days") is not None else 60)
    crit_days = int(o.get("crit_days") if o.get("crit_days") is not None else 7)
    miss = missing_map(o.get("missing"))

    by_host = {}
    display = {}
    total_certs = 0
    for c in certs or []:
        if not isinstance(c, dict):
            continue
        h = str(c.get("host") or "").strip()
        if not h:
            continue
        hk = h.lower()
        display.setdefault(hk, h)
        by_host.setdefault(hk, []).append(c)
        total_certs += 1

    missing_keys = {m.lower() for m in miss}
    for h in o.get("read_hosts") or []:
        if h and h.lower() not in missing_keys:
            display.setdefault(h.lower(), h)
            by_host.setdefault(h.lower(), [])
    rollup_hosts = []
    crit_hosts = warn_hosts = ok_hosts = unk_hosts = 0

    for hk in sorted(by_host):
        h = display[hk]
        cs = by_host[hk]
        worst_st = "ok"
        worst_days = None
        exp_n = crit_n = warn_n = 0
        for c in cs:
            d_raw = c.get("days")
            try:
                d_int = int(d_raw) if d_raw is not None and str(d_raw).strip() != "" else None
            except (TypeError, ValueError):
                d_int = None
            if d_int is not None and d_int <= -99999:
                d_int = None
            st = _cert_sev(d_int, c.get("status"), warn_days, notice_days, crit_days)
            if SEV_ORDER.get(st, 9) < SEV_ORDER.get(worst_st, 9):
                worst_st = st
            if d_int is not None and (worst_days is None or d_int < worst_days):
                worst_days = d_int
            if d_int is not None and d_int < 0:
                exp_n += 1
            if st == "critical":
                crit_n += 1
            elif st == "warning":
                warn_n += 1

        if worst_st == "critical":
            crit_hosts += 1
        elif worst_st == "warning":
            warn_hosts += 1
        elif worst_st == "unknown":
            unk_hosts += 1
        else:
            ok_hosts += 1

        if worst_days is None:
            worst_txt = "expiry unknown"
        elif worst_days < 0:
            worst_txt = "expired %d day%s ago" % (abs(worst_days), "" if abs(worst_days) == 1 else "s")
        else:
            worst_txt = "worst %d day%s" % (worst_days, "" if worst_days == 1 else "s")
        n = len(cs)
        lbl = "Expired" if exp_n else STATUS_LABELS.get(worst_st, worst_st.capitalize())
        rollup_hosts.append({
            "host": h,
            "host_key": hk,
            "status": worst_st,
            "status_label": lbl,
            "count": n,
            "expired": exp_n,
            "critical": crit_n,
            "warning": warn_n,
            "worst_days": worst_days,
            "summary": "%d certificate%s, %s" % (n, "" if n == 1 else "s", worst_txt) if n else "No certificates found",
        })

    for mh, mreason in sorted(miss.items(), key=lambda kv: kv[0].lower()):
        if mh.lower() in by_host:
            continue
        unk_hosts += 1
        rollup_hosts.append({
            "host": mh,
            "host_key": mh.lower(),
            "status": "unknown",
            "status_label": "Not checked",
            "count": 0,
            "expired": 0,
            "critical": 0,
            "warning": 0,
            "worst_days": None,
            "summary": ("Not checked: %s" % mreason) if mreason else "Not checked (did not report)",
        })

    rollup_hosts.sort(key=lambda x: (
        STATUS_ORDER.get(x["status"], 9),
        x["worst_days"] if x["worst_days"] is not None else 999999,
        x["host"].lower(),
    ))

    n_hosts = len(rollup_hosts)
    none_checked = not by_host
    if none_checked:
        tiles = [
            {"label": "Not checked", "value": unk_hosts, "note": "0 hosts read", "status": "unknown", "icon": "unknown", "mask": []},
            {"label": "Certificates", "value": "-", "note": "No hosts checked", "status": "unknown", "icon": "findings", "mask": []},
            {"label": "Expired / critical", "value": "-", "note": "No hosts checked", "status": "unknown", "icon": "critical", "mask": []},
            {"label": "Expiring soon", "value": "-", "note": "No hosts checked", "status": "unknown", "icon": "warning", "mask": []},
        ]
    else:
        tiles = [
            {"label": "Hosts", "value": len(by_host),
             "note": "%d certificate%s" % (total_certs, "" if total_certs == 1 else "s") + ((" · %d not checked" % unk_hosts) if unk_hosts else ""),
             "status": "unknown" if unk_hosts else "ok", "icon": "server",
             "mask": _meter_mask([x["status"] != "unknown" for x in rollup_hosts])},
            {"label": "Expired / critical", "value": crit_hosts,
             "note": "Within %d days or expired" % crit_days if crit_hosts else "None",
             "status": "critical" if crit_hosts else "ok", "icon": "critical",
             "mask": _meter_mask([x["status"] == "critical" for x in rollup_hosts])},
            {"label": "Expiring soon", "value": warn_hosts,
             "note": "Within %d days" % warn_days if warn_hosts else "None",
             "status": "warning" if warn_hosts else "ok", "icon": "warning",
             "mask": _meter_mask([x["status"] == "warning" for x in rollup_hosts])},
            {"label": "Healthy hosts", "value": ok_hosts,
             "note": "Beyond %d days" % warn_days,
             "status": "ok", "icon": "ok",
             "mask": _meter_mask([x["status"] in ("ok", "info") for x in rollup_hosts])},
        ]

    sec_status = "critical" if crit_hosts else ("warning" if warn_hosts else ("unknown" if unk_hosts or none_checked else "ok"))
    return {
        "title": str(o.get("title") or "Hosts by certificate status"),
        "status": sec_status,
        "cert_rollup": True,
        "rail": True,
        "tiles": tiles,
        "rollup_hosts": rollup_hosts,
        "counts": {"hosts": n_hosts, "certs": total_certs, "critical_hosts": crit_hosts, "warning_hosts": warn_hosts, "ok_hosts": ok_hosts, "unknown_hosts": unk_hosts},
        "summary_text": "%d host%s, %d certificate%s — click a host to filter the certificate table below" % (
            n_hosts, "" if n_hosts == 1 else "s", total_certs, "" if total_certs == 1 else "s"),
    }


def cert_table_tag(section, warn_days=30, notice_days=60, crit_days=7):
    """1.139.0 (Step 12): Attach data-host and data-sev row_tags to a certificate table section so
    cert_host_rollup clicks and severity chips filter it cleanly."""
    if not isinstance(section, dict) or not section.get("rows") or not section.get("columns"):
        return section
    out = dict(section)
    cols = [str(c).strip().lower() for c in section["columns"]]
    host_idx = next((i for i, c in enumerate(cols) if c in ("host", "device", "server", "vcenter / host")), -1)
    days_idx = next((i for i, c in enumerate(cols) if c.startswith("days")), -1)
    if host_idx < 0 or days_idx < 0:
        return section
    existing = section.get("row_tags") or []
    tags = []
    for ri, row in enumerate(section["rows"]):
        prev = dict(existing[ri]) if ri < len(existing) and isinstance(existing[ri], dict) else {}
        data = dict(prev.get("data") or {})
        if host_idx >= 0 and host_idx < len(row):
            data["host"] = str(row[host_idx] or "").strip().lower()
        d_val = None
        if days_idx >= 0 and days_idx < len(row):
            m = re.search(r"-?\d+", str(row[days_idx]))
            if m:
                d_val = int(m.group(0))
        data["sev"] = _cert_sev(d_val, "", int(warn_days), int(notice_days), int(crit_days))
        prev["data"] = data
        tags.append(prev)
    out["row_tags"] = tags
    out["cert_table"] = True
    return out


def cert_web_doc(doc, certs, opts=None):
    """1.139.0 (Step 12): Wrap a certificate report doc for the web view: prepend cert_host_rollup on top
    and tag certificate table sections with data-host + data-sev."""
    if not isinstance(doc, dict):
        return doc
    o = opts if isinstance(opts, dict) else {}
    warn_d = int(o.get("warn_days") if o.get("warn_days") is not None else 30)
    notice_d = int(o.get("notice_days") if o.get("notice_days") is not None else 60)
    crit_d = int(o.get("crit_days") if o.get("crit_days") is not None else 7)
    rollup = cert_host_rollup(certs, o)
    new_secs = []
    for s in doc.get("sections") or []:
        if isinstance(s, dict) and s.get("columns") and any(str(c).strip().lower().startswith("days") for c in s["columns"]):
            new_secs.append(cert_table_tag(s, warn_d, notice_d, crit_d))
        else:
            new_secs.append(s)
    return dict(doc, sections=[rollup] + new_secs)


# ---- Step 13: Active Directory (ad_report) ---------------------------------------------------------

def ad_server_view(ad_result, opts=None):
    """1.139.0 (Step 13 + vdi-design-check #6, #7): Build the AD server-first sections:
    1. Domain controllers keyed by lower-case inventory host (two spellings never duplicate a DC; dc is label only),
       with DC-scoped critical/warning AND low_problems folded under their DC (low never turns a DC amber).
    2. Separate 'Domain & forest objects' section for non-DC objects (site links, NCs, GPOs, domain settings)."""
    o = opts if isinstance(opts, dict) else {}
    ad = ad_result if isinstance(ad_result, dict) else {}
    dcs = ad.get("dcs") or []
    problems = list(ad.get("problems") or []) + list(ad.get("low_problems") or [])

    # 1.139.0 (vdi-design-check #7): canonical key = lower-case inventory host; alias map prevents duplicates
    alias = {}
    canonical = {}
    by_dc = {}
    dc_health = {}
    dc_missing = {}
    dc_meta = {}

    for d in dcs:
        if not isinstance(d, dict):
            continue
        inv_host = str(d.get("host") or d.get("fqdn") or d.get("dc") or "").strip()
        if not inv_host:
            continue
        key = inv_host.lower()
        if key not in canonical:
            canonical[key] = key
        display_host = canonical[key]
        dc_short = str(d.get("dc") or "").strip()
        fqdn = str(d.get("fqdn") or "").strip()
        for a in (inv_host, fqdn, dc_short, inv_host.split(".", 1)[0], fqdn.split(".", 1)[0]):
            al = a.strip().lower()
            if al:
                domain = str(d.get("domain") or "").lower()
                alias[(domain, al)] = display_host
                alias[al] = display_host if alias.get(al, display_host) == display_host else None
        if dc_short and dc_short.lower() != display_host.lower():
            dc_meta[display_host] = "DC %s%s" % (dc_short, (" · " + str(d["domain"])) if d.get("domain") else "")
        elif d.get("domain"):
            dc_meta[display_host] = str(d["domain"])

        if d.get("read", True):
            by_dc.setdefault(display_host, [])
            dc_health[display_host] = {"checks": ["ad"], "label": dc_meta.get(display_host, ""), "time": str(o.get("now") or "")}
        else:
            dc_missing[display_host] = str(d.get("error") or "DC did not respond").strip()

    domain_objs = []
    for p in problems:
        if not isinstance(p, dict):
            continue
        obj = str(p.get("object") or "").strip()
        area = str(p.get("area") or "ad").strip()
        dom = str(p.get("domain") or "").strip()
        sev = normalize_severity(p.get("severity"), default="warning")
        txt = str(p.get("text") or "").strip()

        matched_dc = alias.get((dom.lower(), obj.lower())) or alias.get(obj.lower())

        if matched_dc and matched_dc not in dc_missing:
            by_dc.setdefault(matched_dc, []).append({
                "check": area,
                "kind": area,
                "id": "ad:%s:%s" % (area, obj or matched_dc),
                "severity": sev,
                "summary": txt or ("%s issue on %s" % (area, obj)),
                "note": " · ".join(x for x in (dom, obj) if x),
                "fix": "",
                "hint": "",
            })
        else:
            domain_objs.append({"domain": dom, "area": area, "object": obj or dom or "Domain", "severity": sev, "text": txt})

    sv_opts = dict(o)
    sv_opts.setdefault("title", "Domain controllers")
    sv_opts["host_meta"] = dc_meta
    dc_sec = server_view(by_dc, health=dc_health, missing=dc_missing, opts=sv_opts)
    out = [dc_sec]

    if domain_objs:
        domain_objs.sort(key=lambda x: (SEV_ORDER.get(x["severity"], 9), x["domain"].lower(), x["area"].lower(), x["object"].lower()))
        rows, r_st, r_tags = [], [], []
        for x in domain_objs:
            rows.append([x["severity"].upper(), x["domain"] or "-", x["area"], x["object"], x["text"]])
            r_st.append(x["severity"] if x["severity"] in ("critical", "warning", "low", "info") else "")
            r_tags.append({"data": {"sev": x["severity"]}})
        d_st = "critical" if any(x["severity"] == "critical" for x in domain_objs) else (
            "warning" if any(x["severity"] == "warning" for x in domain_objs) else (
                "low" if any(x["severity"] == "low" for x in domain_objs) else "info"
            )
        )
        out.append({
            "title": "Domain & forest objects",
            "status": d_st,
            "text": "Findings scoped to the domain, forest, site links, naming contexts or GPOs (not a single DC).",
            "columns": ["Severity", "Domain", "Area", "Object", "Finding"],
            "rows": rows,
            "row_status": r_st,
            "row_tags": r_tags,
        })
    return out


def ad_web_doc(doc, ad_result, opts=None):
    """1.139.0 (Step 13): Prepend AD server view (DCs + non-DC domain objects) above the detailed AD tables."""
    if not isinstance(doc, dict):
        return doc
    secs = ad_server_view(ad_result, opts)
    return dict(doc, sections=secs + list(doc.get("sections") or []))


# ---- Chrome-only reports (ONTAP, UCS, Netmap) ------------------------------------------------------

def _row_sev(sec, ri):
    """Derive canonical severity for row ri of a native report table (ONTAP, UCS, Netmap)."""
    rs = sec.get("row_status") or []
    if ri < len(rs) and rs[ri]:
        return normalize_severity(rs[ri], default="ok")
    cs_all = sec.get("cell_status") or []
    if ri < len(cs_all) and isinstance(cs_all[ri], list):
        best = "ok"
        for cell_st in cs_all[ri]:
            if cell_st:
                st = normalize_severity(cell_st, default="ok")
                if SEV_ORDER.get(st, 9) < SEV_ORDER.get(best, 9):
                    best = st
        if best != "ok":
            return best
    return "ok"


def chrome_report_view(doc, opts=None):
    """1.139.0: Chrome-only enhancement for native non-server reports (ONTAP, UCS, Netmap).
    Keeps EVERY native section, column, key and row intact; adds a top 4-tile 'At a glance' chrome
    section (chrome_view: True) and normalized row_tags[i].data.sev on native tables so unified
    search, severity chips and sidebar counts/select work across all four looks."""
    if not isinstance(doc, dict):
        return doc
    o = opts if isinstance(opts, dict) else {}
    kind_label = str(o.get("kind_label") or "").strip()

    # Build up to 4 At a glance tiles from doc.summary (never drops native keys)
    raw_sum = doc.get("summary") or []
    tiles = []
    for t in raw_sum[:4]:
        if not isinstance(t, dict):
            continue
        tst = normalize_severity(t.get("status"), default="info") if t.get("status") else "info"
        tiles.append({
            "label": str(t.get("label") or ""),
            "value": "" if t.get("value") is None else t.get("value"),
            "note": str(t.get("note") or ""),
            "status": tst,
            "icon": tst if tst in ("critical", "warning", "ok", "unknown") else "findings",
            "mask": [],
        })
    # Keep any extra summary boxes beyond 4 in tiles_kept so no figure is lost
    tiles_kept = [t for t in raw_sum[4:] if isinstance(t, dict)]

    # Tag native table rows with data.sev without overwriting existing row_tags keys
    new_secs = []
    all_sevs = set()
    for s in doc.get("sections") or []:
        if not isinstance(s, dict):
            new_secs.append(s)
            continue
        if s.get("columns") and s.get("rows"):
            s2 = dict(s)
            existing = s.get("row_tags") or []
            tags = []
            for ri in range(len(s["rows"])):
                prev = dict(existing[ri]) if ri < len(existing) and isinstance(existing[ri], dict) else {}
                data = dict(prev.get("data") or {})
                if not data.get("sev"):
                    data["sev"] = _row_sev(s, ri)
                all_sevs.add(data["sev"])
                prev["data"] = data
                tags.append(prev)
            s2["row_tags"] = tags
            new_secs.append(s2)
        else:
            new_secs.append(s)

    chrome_sec = {
        "title": "At a glance",
        "status": normalize_severity(doc.get("status"), default="info"),
        "chrome_view": True,
        "rail": False,
        "kind_label": kind_label,
        "tiles": tiles,
        "tiles_kept": tiles_kept,
        "statuses": [k for k in ("critical", "warning", "low", "info", "unknown", "ok") if k in all_sevs],
    }
    return dict(doc, sections=[chrome_sec] + new_secs)


class FilterModule(object):
    def filters(self):
        return {
            "normalize_severity": normalize_severity,
            "server_view": server_view,
            "host_counts": host_counts,
            "fleet_summary_lines": fleet_summary_lines,
            "stig_server_view": stig_server_view,
            "fapolicyd_server_view": fapolicyd_server_view,
            "sw_inventory_server_view": sw_inventory_server_view,
            "swr_server_view": swr_server_view,
            "cert_host_rollup": cert_host_rollup,
            "cert_table_tag": cert_table_tag,
            "cert_web_doc": cert_web_doc,
            "ad_server_view": ad_server_view,
            "ad_web_doc": ad_web_doc,
            "chrome_report_view": chrome_report_view,
        }
