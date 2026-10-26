# -*- coding: utf-8 -*-
"""The report of playbooks/fapolicyd_denials.yml: every host's fapolicyd denials and ACT's analysis of them,
from what ACT's playbook (vendor/act/ansible/playbooks/fapolicyd_denials.yml) left in each host's variables -
_fa_rpm, _fa_active, _fa_auditd, _fa_count, _fa_paths, _fa_exes, _fa_findings and act_triage_result (only where
ACT ran: a host with denials or a problem). One report (roles/site_email's _report) for the whole job."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

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


class FilterModule(object):
    def filters(self):
        return {"fapolicyd_report": fapolicyd_report}
