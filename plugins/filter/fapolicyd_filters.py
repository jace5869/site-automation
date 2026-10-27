# -*- coding: utf-8 -*-
"""The report of playbooks/fapolicyd_denials.yml: every host's fapolicyd denials and ACT's analysis of them,
from what ACT's playbook (vendor/act/ansible/playbooks/fapolicyd_denials.yml) left in each host's variables -
_fa_rpm, _fa_active, _fa_auditd, _fa_count, _fa_paths, _fa_exes, _fa_findings and act_triage_result (only where
ACT ran: a host with denials or a problem). One report (roles/site_email's _report) for the whole job.
And playbooks/fapolicyd_containers.yml's: the denials containers caused (roles/fapolicyd_containers: _fc, _fc_apply),
the allow rules to approve, the rules added - with the checks of a rule asked for and the rules file's new list."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import re

ORDER = {"critical": 0, "warning": 1, "ok": 2}
NOTE = ("ACT's proposals (a trust entry, a narrow rule) are security-control changes: route them through your "
        "security approval (e.g. the ISSO) before anyone applies them.")


def _get(hv, key, default=None):
    try:
        v = hv[key] if key in hv else default
    except Exception:            # a variable that does not template is as good as missing
        return default
    return default if v is None else v


def _out(reg):
    return str(_get(reg, "stdout", "") if hasattr(reg, "__contains__") else "").strip()


def host_result(name, hv):
    """One host -> {host, state: 'not read' | 'not installed' | 'read', status, ...}."""
    rpm = _get(hv, "_fa_rpm")
    if not hasattr(rpm, "__contains__") or "rc" not in rpm:
        return {"host": name, "state": "not read", "status": "warning"}
    if rpm["rc"] != 0:
        return {"host": name, "state": "not installed", "status": "ok"}
    active, auditd = _out(_get(hv, "_fa_active", {})) or "not running", _out(_get(hv, "_fa_auditd", {})) or "not running"
    try:
        count = int(_get(hv, "_fa_count", 0) or 0)
    except (TypeError, ValueError):
        count = 0
    status = "critical" if active != "active" else ("warning" if count or auditd != "active" else "ok")
    act = _get(hv, "act_triage_result")
    act = dict(act) if hasattr(act, "keys") else None
    return {"host": name, "state": "read", "status": status, "active": active, "auditd": auditd, "count": count,
            "paths": list(_get(hv, "_fa_paths", []) or []), "exes": list(_get(hv, "_fa_exes", []) or []),
            "findings": list(_get(hv, "_fa_findings", []) or []), "act": act}


def fapolicyd_report(hostvars_list, names, since="today", title=""):
    """[hostvars of each host], [their names], the ausearch -ts keyword, the report title (site_report_title)
    -> roles/site_email's _report."""
    hosts = [host_result(n, hv) for n, hv in zip(names, hostvars_list)]
    read = [h for h in hosts if h["state"] == "read"]
    unread = [h["host"] for h in hosts if h["state"] == "not read"]
    missing = [h["host"] for h in hosts if h["state"] == "not installed"]
    problem = sorted([h for h in read if h["status"] != "ok"], key=lambda h: (ORDER[h["status"]], -h["count"], h["host"]))
    quiet = [h["host"] for h in read if h["status"] == "ok"]
    denied = [h for h in read if h["count"]]
    down = [h for h in read if h["active"] != "active"]
    total = sum(h["count"] for h in read)

    status = "critical" if down else ("warning" if problem or unread else "ok")
    head = []
    if denied:
        head.append("%d host(s) with denials" % len(denied))
    if down:
        head.append("fapolicyd not running on %d" % len(down))
    if [h for h in read if h["auditd"] != "active"]:
        head.append("auditd not running on %d" % len([h for h in read if h["auditd"] != "active"]))
    report = {
        "title": "%s: %s" % (title or "fapolicyd denials", ", ".join(head) if head else "no denials since %s" % since),
        "subtitle": "%d host(s) read - denials since %s (the audit log: ausearch -m FANOTIFY -ts %s)" % (len(read), since, since),
        "status": status,
        "summary": [{"label": "Denials", "value": total, "status": "warning" if total else "ok"},
                    {"label": "Hosts with denials", "value": len(denied), "status": "warning" if denied else "ok"},
                    {"label": "fapolicyd not running", "value": len(down), "status": "critical" if down else "ok"},
                    {"label": "Hosts read", "value": len(read)}]
                   + ([{"label": "Not read", "value": len(unread), "status": "warning"}] if unread else []),
        "sections": [],
        "footer": NOTE,
    }
    secs = report["sections"]
    if problem:
        rows, row_status, cell_status = [], [], []
        for h in problem:
            rows.append([h["host"], h["active"], h["auditd"], h["count"], ", ".join(h["paths"]) or "-", ", ".join(h["exes"]) or "-"])
            row_status.append(h["status"])
            cell_status.append(["", "ok" if h["active"] == "active" else "critical", "ok" if h["auditd"] == "active" else "warning",
                                "warning" if h["count"] else "", "", ""])
        secs.append({"title": "Hosts with denials or a problem", "status": status if status != "ok" else "warning",
                     "text": "What fapolicyd blocked since %s: the files denied and the programs that tried. A denial is "
                             "fapolicyd doing its job, or a legitimate program missing from the trust database - ACT's "
                             "analysis below says which. auditd not running = denials are not recorded." % since,
                     "columns": ["Host", "fapolicyd", "auditd", "Denials", "Files denied", "Programs"],
                     "rows": rows, "row_status": row_status, "cell_status": cell_status})
        for h in problem:
            a = h["act"]
            lines = list(h["findings"])
            if a is None:
                text = "ACT did not run on this host (a Check run, or the host failed before it): the findings only."
            elif str(a.get("status")) == "error":
                text = "ACT could not analyse it: %s" % (a.get("stop_reason") or a.get("summary") or "no reason given")
            else:
                text = str(a.get("summary") or a.get("stop_reason") or "(ACT gave no summary)")
            for d in (a or {}).get("denied") or []:
                if isinstance(d, dict) and d.get("command"):
                    lines.append("ACT proposes (for your security approval): %s" % d["command"])
            secs.append({"title": "%s: ACT's analysis" % h["host"], "status": h["status"], "text": text, "lines": lines})
    if quiet:
        secs.append({"title": "No denials", "status": "ok", "lines": ["%d host(s): fapolicyd running, auditd running, nothing "
                                                                        "denied since %s - %s" % (len(quiet), since, ", ".join(sorted(quiet)))]})
    if missing:
        secs.append({"title": "fapolicyd not installed", "lines": ["%d host(s), not looked at: %s" % (len(missing), ", ".join(sorted(missing)))]})
    if unread:
        secs.append({"title": "Not read", "status": "warning",
                     "lines": ["%d host(s) could not be reached or failed before their denials were read: %s"
                               % (len(unread), ", ".join(sorted(unread)))]})
    return report


# ---- playbooks/fapolicyd_containers.yml ----------------------------------------------------------------
RULE = re.compile(r"^(allow|allow_audit|allow_syslog|allow_log) perm=(open|execute|any) (.+?) : (.+)$")
SUBJ = ("all", "auid", "uid", "gid", "sessionid", "pid", "ppid", "trust", "comm", "exe", "dir", "ftype", "device")
OBJ = ("all", "path", "dir", "device", "ftype", "trust", "sha256hash")
CNOTE = ("A rule added here lets that program use that file on the host, inside a container or not: approve each one "
         "through your security approval (e.g. the ISSO) before it is listed in fapolicyd_container_rules.")


def _attrs(text, allowed):
    """'exe=/x dir=/y/' -> '' when every attribute is known and holds a value a rule can, else why not."""
    parts = text.split()
    if parts == ["all"]:
        return ""
    for a in parts:
        k, eq, v = a.partition("=")
        if not eq or k not in allowed or k == "all" or not v or '"' in v or "'" in v:
            return "%r is not an attribute fapolicyd knows here" % a
        if k in ("path", "dir", "exe") and not v.startswith("/"):
            return "%s must be a full path" % k
        if k == "dir" and not v.endswith("/"):
            return "dir must end with /"
    return ""


def fapolicyd_rule_problem(rule):
    """One rule asked for (fapolicyd_container_rules) -> '' when it may be written, else why not. Only allow rules,
    with known attributes; never 'all : all', never a folder as wide as / ."""
    if "\n" in str(rule or "").strip() or "\r" in str(rule or ""):
        return "one rule per item: it holds a line break"
    m = RULE.match(" ".join(str(rule or "").split()))
    if not m:
        return "not an allow rule as fapolicyd writes them: allow perm=open|execute|any SUBJECT : OBJECT"
    why = _attrs(m.group(3), SUBJ) or _attrs(m.group(4), OBJ)
    if why:
        return why
    if m.group(3) == "all" and m.group(4) == "all":
        return "all : all would turn fapolicyd off"
    if re.search(r"(^| )dir=/( |$)", m.group(4)):
        return "dir=/ is every file"
    return ""


def fapolicyd_rules_plan(managed, add, remove):
    """The rules file's rules now, the ones asked for, the ones to take out -> {rules (the file's new list, in
    order), added, removed, bad [{rule, why}], changed}. One bad rule asked for: nothing is changed."""
    norm = lambda r: " ".join(str(r or "").split())
    now = [norm(r) for r in managed or [] if norm(r)]
    bad = [{"rule": norm(r), "why": fapolicyd_rule_problem(r)} for r in add or [] if norm(r) and fapolicyd_rule_problem(r)]
    add = [norm(r) for r in add or [] if norm(r)]
    remove = set(norm(r) for r in remove or [] if norm(r))
    if bad:
        return {"rules": now, "added": [], "removed": [], "bad": bad, "changed": False}
    rules = [r for r in now if r not in remove]
    added = []
    for r in add:
        if r not in rules and r not in remove:
            rules.append(r)
            added.append(r)
    removed = [r for r in now if r in remove]
    return {"rules": rules, "added": added, "removed": removed, "bad": [], "changed": rules != now}


def fapolicyd_rules_file(rules, playbook="playbooks/fapolicyd_containers.yml"):
    head = ("# Managed by site-automation (%s): the container rules approved for this host.\n"
            "# Do not edit: list them in fapolicyd_container_rules (fapolicyd_container_rules_remove) and run the job.\n" % playbook)
    return head + "".join("%s\n" % r for r in rules)


def _container_host(name, hv):
    fc = _get(hv, "_fc")
    if not hasattr(fc, "keys") or "state" not in fc:
        return {"host": name, "state": "not read", "error": ""}
    h = dict(fc)
    h["host"] = name
    ap = _get(hv, "_fc_apply")
    h["apply"] = dict(ap) if hasattr(ap, "keys") else None
    return h


def fapolicyd_containers_report(hostvars_list, names, since="today", title="", rules_file="", check=False):
    """[hostvars of each host], [their names], the ausearch -ts keyword, the report title, the rules file, a
    Check run -> roles/site_email's _report: the container denials, the rules to approve, the rules added."""
    hosts = [_container_host(n, hv) for n, hv in zip(names, hostvars_list)]
    read = [h for h in hosts if h["state"] == "read" and not h.get("error")]
    unread = [h for h in hosts if h["state"] == "not read" or (h["state"] == "read" and h.get("error"))]
    missing = [h["host"] for h in hosts if h["state"] == "not installed"]
    denied = [h for h in read if h.get("groups")]
    down = [h for h in read if h.get("active") != "active"]
    applied = [h for h in hosts if h.get("apply")]
    failed = [h for h in applied if h["apply"].get("state") in ("failed", "refused", "not possible")]
    total = sum(sum(g["count"] for g in h["groups"]) for h in denied)

    proposals = {}
    by_hand = []
    for h in denied:
        a = h.get("apply") or {}
        have = a["rules"] if a.get("state") in ("changed", "unchanged", "would change") else (h.get("managed") or [])
        for g in h["groups"]:
            if g.get("proposal") and g["proposal"] not in have:
                proposals.setdefault(g["proposal"], []).append(h["host"])
            elif not g.get("proposal"):
                by_hand.append("%s: %s -> %s - %s" % (h["host"], g["exe"] or "?", g["path"] or "?", g.get("note", "")))

    status = "critical" if down or failed else ("warning" if denied or unread else "ok")
    head = []
    if denied:
        head.append("%d container denial(s) on %d host(s)" % (total, len(denied)))
    if [h for h in applied if h["apply"].get("added") or h["apply"].get("removed")] and not failed:
        head.append(("rules would change on %d host(s)" if check else "rules changed on %d host(s)")
                    % len([h for h in applied if h["apply"].get("added") or h["apply"].get("removed")]))
    if failed:
        head.append("the rules could not be changed on %d host(s)" % len(failed))
    if down:
        head.append("fapolicyd not running on %d" % len(down))
    report = {
        "title": "%s: %s" % (title or "fapolicyd and containers", ", ".join(head) if head else "no container denials since %s" % since),
        "subtitle": "%d host(s) read - fapolicyd denials since %s caused by containers (ausearch -m FANOTIFY -ts %s)%s"
                    % (len(read), since, since, " - Check: nothing changed" if check else ""),
        "status": status,
        "summary": [{"label": "Container denials", "value": total, "status": "warning" if total else "ok"},
                    {"label": "Hosts with them", "value": len(denied), "status": "warning" if denied else "ok"},
                    {"label": "Rules to approve", "value": len(proposals), "status": "warning" if proposals else "ok"}]
                   + ([{"label": "Rules not changed" if failed else "Hosts whose rules changed",
                        "value": len(failed) if failed else len([h for h in applied if h["apply"].get("added") or h["apply"].get("removed")]),
                        "status": "critical" if failed else "ok"}] if applied else [])
                   + [{"label": "fapolicyd not running", "value": len(down), "status": "critical" if down else "ok"},
                      {"label": "Hosts read", "value": len(read)}]
                   + ([{"label": "Not read", "value": len(unread), "status": "warning"}] if unread else []),
        "sections": [],
        "footer": CNOTE,
    }
    secs = report["sections"]
    if applied:
        rows, rs = [], []
        for h in applied:
            a = h["apply"]
            st = a.get("state", "")
            rows.append([h["host"], st, "\n".join(a.get("added") or []) or "-", "\n".join(a.get("removed") or []) or "-",
                         len(a.get("rules") or []), a.get("error") or "; ".join("%s: %s" % (b["rule"], b["why"]) for b in a.get("bad") or [])])
            rs.append("critical" if st in ("failed", "refused", "not possible") else "ok")
        secs.append({"title": "The rules: what this run changed" if not check else "The rules: what this run would change",
                     "status": "critical" if failed else "ok",
                     "text": "The rules asked for (fapolicyd_container_rules, fapolicyd_container_rules_remove), in %s on each host, "
                             "loaded with fagenrules --load. A host where they did not load, or fapolicyd stopped: its "
                             "file put back as it was. One rule that is not a plain allow rule: nothing written anywhere." % (rules_file or "the rules file"),
                     "columns": ["Host", "Result", "Added", "Taken out", "Rules in the file", "Why"],
                     "rows": rows, "row_status": rs})
    if denied:
        rows, rs = [], []
        for h in sorted(denied, key=lambda h: -sum(g["count"] for g in h["groups"])):
            for g in h["groups"]:
                rows.append([h["host"], ", ".join(g.get("containers") or []) or "-", g["exe"] or "?", g["path"] or "?", g["perm"],
                             g["count"], g.get("last", ""), ("%s. %s" % (g["rule_no"], g["rule"])) if g.get("rule") else (g.get("rule_no") or "?"),
                             ", ".join(g.get("why") or [])])
                rs.append("warning")
        secs.append({"title": "Container denials", "status": "warning",
                     "text": "What fapolicyd blocked since %s that a container caused: the program, the file it was denied, how "
                             "often, the rule that denied it, and the container while it still runs. A container's files are not "
                             "in the trust database, which RPM fills from the host's packages." % since,
                     "columns": ["Host", "Container", "Program", "File denied", "Permission", "Times", "Last", "Denied by rule", "Why a container's"],
                     "rows": rows, "row_status": rs})
    if proposals or by_hand:
        lines = ["%s     (%s)" % (r, ", ".join(sorted(hs))) for r, hs in sorted(proposals.items())]
        notes = sorted(set("%s: %s" % (g["proposal"], g["note"]) for h in denied for g in h["groups"]
                           if g.get("proposal") in proposals and g.get("note")))
        secs.append({"title": "Rules to approve", "status": "warning",
                     "text": "The narrowest allow rule for each denial: that program, that file. To add them, after your security "
                             "approval, list them in fapolicyd_container_rules (the template's Variables) and run the job again - "
                             "a Check run first shows what it would change. They are written to %s on every host of the run "
                             "(use a Limit for fewer)." % (rules_file or "the rules file"),
                     "lines": lines + ["Note - %s" % n for n in notes] + ["By hand - %s" % b for b in by_hand]})
    quiet = [h["host"] for h in read if not h.get("groups")]
    lines = []
    for h in read:
        if h.get("active") != "active":
            lines.append("%s: fapolicyd is %s - nothing is allow-listed" % (h["host"], h.get("active")))
        if h.get("permissive"):
            lines.append("%s: fapolicyd is permissive - it logs, it blocks nothing" % h["host"])
        if h.get("auditd") != "active":
            lines.append("%s: auditd is %s - denials are not recorded" % (h["host"], h.get("auditd")))
        if not h.get("rules_d") or not h.get("fagenrules"):
            lines.append("%s: no rules.d folder or fagenrules (fapolicyd before 1.1): rules cannot be added by this job" % h["host"])
        if h.get("others"):
            lines.append("%s: %d other denial(s), not containers' (the fapolicyd denials job): %s"
                         % (h["host"], (h.get("counts") or {}).get("other", 0), "; ".join(h["others"][:3])))
    if lines:
        secs.append({"title": "The hosts", "status": "critical" if down else "warning", "lines": lines})
    if quiet:
        secs.append({"title": "No container denials", "status": "ok",
                     "lines": ["%d host(s), nothing a container caused denied since %s - %s" % (len(quiet), since, ", ".join(sorted(quiet)))]})
    if missing:
        secs.append({"title": "fapolicyd not installed", "lines": ["%d host(s), not looked at: %s" % (len(missing), ", ".join(sorted(missing)))]})
    if unread:
        secs.append({"title": "Not read", "status": "warning",
                     "lines": ["%s: %s" % (h["host"], h.get("error") or "could not be reached, or failed before its denials were read")
                               for h in sorted(unread, key=lambda h: h["host"])]})
    return report


class FilterModule(object):
    def filters(self):
        return {"fapolicyd_report": fapolicyd_report, "fapolicyd_containers_report": fapolicyd_containers_report,
                "fapolicyd_rule_problem": fapolicyd_rule_problem, "fapolicyd_rules_plan": fapolicyd_rules_plan,
                "fapolicyd_rules_file": fapolicyd_rules_file}
