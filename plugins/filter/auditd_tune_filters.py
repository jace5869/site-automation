# -*- coding: utf-8 -*-
"""Linux - auditd tuning for containers (1.79.13; playbooks/auditd_tuning.yml, roles/auditd_tune): what one host's audit
read says (the kernel's backlog now, the rules file's, the kernel command line's and grubby's for the next boot,
auditd.conf's queue, immutable or not, containers or not), what is too small or set but waiting for a reboot, the changes
to approve (one line each), the lines a person approved, and ONE report of every host. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type


CHANGES = ("backlog", "queue", "kernel", "containers")


def _int(v, default=None):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def auditd_tune_read(text):
    """The read script's lines ("key value", "kernel <path> <value|none>", "rootless <user>") -> a dict."""
    out = {"kernels": {}, "rootless": [], "rules_files": [], "excluded": []}
    for line in str(text or "").splitlines():
        parts = line.rstrip("\n").split(" ", 1)
        if len(parts) < 2 and parts[0] not in ("",):
            parts.append("")
        k, v = parts[0], parts[1].strip() if len(parts) > 1 else ""
        if k == "kernel":
            p = v.rsplit(" ", 1)
            if len(p) == 2:
                out["kernels"][p[0]] = None if p[1] == "none" else _int(p[1])
        elif k == "rootless":
            if v:
                out["rootless"].append(v)
        elif k == "rules_file":
            if v:
                out["rules_files"].append(v)
        elif k == "excluded":                              # a dir= of an active "-a never,exit" rule
            if v:
                out["excluded"].append(v)
        elif k:
            out[k] = v
    return out


def auditd_tune_state(text, opts=None):
    """One host: {seen, findings [{severity, text}], changes [{id, text, ...the values to write}], waiting [text], locked,
    pending} from its read and the settings (opts: backlog, wait, q_depth, containers, users). A value already at or
    above its minimum is kept (never lowered); locked = the running kernel is immutable (auditctl -s: enabled 2);
    pending = -e 2 is in the rules but not loaded yet (the next load locks it)."""
    o = dict({"backlog": 8192, "wait": 60000, "q_depth": 8192, "containers": False, "users": []}, **(opts or {}))
    r = auditd_tune_read(text)
    want_b, want_w, want_q = int(o["backlog"]), int(o["wait"]), int(o["q_depth"])
    now_b, now_w, lost = _int(r.get("k_backlog_limit")), _int(r.get("k_backlog_wait_time")), _int(r.get("k_lost"), 0)
    rules_b, rules_w = _int(r.get("rules_b")), _int(r.get("rules_bwt"))
    cmd_b = _int(r.get("cmdline_backlog"))
    q, ov = _int(r.get("q_depth")), str(r.get("overflow_action") or "")
    locked = r.get("k_enabled") == "2"
    pending = r.get("rules_e") == "2" and not locked
    kernels = r.get("kernels") or {}
    findings, changes, waiting = [], [], []
    if r.get("error"):
        return {"seen": r, "findings": [{"severity": "critical", "text": "not read: " + r["error"]}], "changes": [], "waiting": [],
                "immutable": locked, "locked": locked, "pending": pending, "read": False}
    # the backlog: the rules file (what augenrules loads) and the kernel now - each value kept when it is enough
    if rules_b is None or rules_b < want_b or rules_w is None or rules_w < want_w:
        nb = max(rules_b or 0, want_b)
        nw = max(rules_w or 0, want_w)
        changes.append({"id": "backlog", "b": nb, "w": nw,
                        "text": "backlog: -b %d and --backlog_wait_time %d in /etc/audit/rules.d/10-base-config.rules (now %s and %s)"
                        % (nb, nw, rules_b if rules_b is not None else "not set", rules_w if rules_w is not None else "not set")})
    rules_ok = rules_b is not None and rules_b >= want_b and rules_w is not None and rules_w >= want_w
    live_low = (now_b is not None and now_b < want_b) or (now_w is not None and now_w < want_w)
    if live_low:
        now_text = "backlog_limit %s, backlog_wait_time %s" % (now_b if now_b is not None else "?", now_w if now_w is not None else "?")
        if rules_ok:
            if locked:
                waiting.append("backlog_limit %d / backlog_wait_time %d in the rules file are set; take effect at the next reboot - "
                               "immutable (-e 2): now %s" % (rules_b, rules_w, now_text))
            else:
                findings.append({"severity": "warning", "text": "backlog_limit %d / backlog_wait_time %d in the rules file are not loaded "
                                 "yet: now %s (augenrules --load%s)" % (rules_b, rules_w, now_text,
                                                                         "; the rules' -e 2 then locks them" if pending else "")})
        elif now_b is not None and now_b < want_b:
            findings.append({"severity": "warning", "text": "the kernel's audit backlog_limit is %d - too small for containers (want %d)"
                             % (now_b, want_b)})
    # the kernel command line (the boot-time backlog, before auditd starts): only the entries without it or lower
    if r.get("grubby_error"):
        findings.append({"severity": "warning", "text": "the boot entries could not be read (grubby --info=ALL): %s" % r["grubby_error"]})
    low = sorted(k for k, v in kernels.items() if v is None or v < want_b)
    if low:
        changes.append({"id": "kernel", "kernels": low,
                        "text": "kernel: audit_backlog_limit=%d on %d of %d kernel(s) without it or lower (grubby; at the next reboot)"
                        % (want_b, len(low), len(kernels))})
    elif kernels and (cmd_b is None or cmd_b < want_b):
        waiting.append("audit_backlog_limit=%d or more is set for every kernel; takes effect at the next reboot: the running kernel has %s"
                       % (want_b, cmd_b if cmd_b is not None else "none"))
    # auditd's queue to its plugins - a deep enough queue kept
    if q is None or q < want_q or ov.upper() != "SYSLOG":
        nq = max(q or 0, want_q)
        changes.append({"id": "queue", "q": nq,
                        "text": "queue: q_depth %d and overflow_action SYSLOG in /etc/audit/auditd.conf, auditd restarted (now %s and %s)"
                        % (nq, q if q is not None else "not set", ov or "not set")})
    if lost and lost > 0:
        findings.append({"severity": "warning", "text": "%d audit record(s) LOST since boot (backlog %s of limit %s)"
                         % (lost, r.get("k_backlog") or "?", now_b if now_b is not None else "?")})
    if pending:
        findings.append({"severity": "info", "text": "the rules end in -e 2 but the kernel is not locked yet: the next augenrules --load "
                         "(or reboot) locks it"})
    # the containers' storage, never audited (optional, ISSO): only the paths there, only the ones not excluded yet
    if o.get("containers"):
        want = (["/var/lib/containers/storage/overlay"] if r.get("storage_root") == "yes" else []) + \
            ["/home/%s/.local/share/containers/storage/overlay" % u for u in (o.get("users") or []) if u in (r.get("rootless") or [])]
        missing = [d for d in want if d not in (r.get("excluded") or [])]
        if missing:
            changes.append({"id": "containers", "dirs": missing,
                            "text": "containers: -a never,exit for %s in 05-containers.rules - ISSO approval needed" % ", ".join(missing)})
    return {"seen": r, "findings": findings, "changes": changes, "waiting": waiting, "immutable": locked, "locked": locked,
            "pending": pending, "read": True}


def auditd_tune_pick(approved, changes, host):
    """The changes approved for this host: an entry is a change's text, its id (backlog, queue, kernel, containers), or
    either after "<host>: " - other hosts' entries are not this host's."""
    picked = []
    for c in changes or []:
        for a in approved or []:
            s = str(a).strip()
            if ":" in s and s.split(":", 1)[0].strip() == host:
                s = s.split(":", 1)[1].strip()
            elif ":" in s and s.split(":", 1)[0].strip() not in CHANGES and not s.startswith(c["id"] + ":"):
                continue                                   # another host's entry
            if s == c["text"] or s == c["id"]:
                picked.append(c["id"])
                break
    return [i for i in CHANGES if i in picked]


def auditd_tune_workflow(hosts_vars, hosts):
    """The workflow's check: each host's change texts -> {plan: {host: [text]}, count}."""
    plan = {}
    for h, hv in zip(hosts or [], hosts_vars or []):
        st = (hv or {}).get("_atu") or {}
        if st.get("changes"):
            plan[h] = [c["text"] for c in st["changes"]]
    return {"plan": plan, "count": sum(len(v) for v in plan.values())}


def auditd_tune_report(hosts_vars, hosts, check=False, workflow=False):
    """ONE report of every host (roles/site_email's _report)."""
    rows, rstat, todo, wait = [], [], [], []
    n_lost = n_change = n_wait = n_bad = 0
    for h, hv in zip(hosts or [], hosts_vars or []):
        st = (hv or {}).get("_atu") or {}
        ap = (hv or {}).get("_atu_apply") or {}
        v = st.get("seen") or {}
        if not st:
            continue
        if not st.get("read", True):
            n_bad += 1
            rows.append([h, "-", "-", "-", "-", "-", "not read: " + str(v.get("error") or "?")])
            rstat.append("critical")
            continue
        state = ap.get("state") or ("changes to approve" if st["changes"] else "waiting for a reboot" if st["waiting"] else "ok")
        if ap.get("state") in ("failed", "rolled back"):
            n_bad += 1
        if st["changes"]:
            n_change += 1
        if st["waiting"]:
            n_wait += 1
        lost = _int(v.get("k_lost"), 0) or 0
        if lost:
            n_lost += 1
        kern = sorted(set("none" if x is None else str(x) for x in (v.get("kernels") or {}).values())) or ["?"]
        rows.append([h, "%s / %s" % (v.get("k_backlog_limit") or "?", v.get("rules_b") or "not set"),
                     "/".join(kern) + (" (running: %s)" % (v.get("cmdline_backlog") or "none")),
                     "%s, %s" % (v.get("q_depth") or "not set", v.get("overflow_action") or "not set"),
                     str(lost), "yes" if st.get("locked") else ("pending (-e 2 not loaded)" if st.get("pending") else "no"), state])
        rstat.append("critical" if ap.get("state") in ("failed", "rolled back") else "warning" if (st["changes"] or lost or st["findings"]) else "ok")
        todo += ["%s: %s" % (h, c["text"]) for c in st["changes"]]
        wait += ["%s: %s" % (h, w) for w in st["waiting"]]
        for f in st["findings"]:
            wait.append("%s: %s" % (h, f["text"]))
        for d in ap.get("did") or []:
            wait.append("%s: %s" % (h, d))
    status = "critical" if n_bad else "warning" if (n_change or n_lost) else "ok"
    secs = [{"title": "The hosts", "columns": ["Host", "Backlog limit (now / rules)", "Kernel arg (boot)", "Queue (q_depth, overflow)",
                                                "Lost", "Immutable", "State"],
             "kinds": ["", "wrap", "wrap", "wrap", "num", "", "wrap"], "rows": rows, "row_status": rstat}]
    if todo:
        secs.append({"title": "Changes to approve (%d)" % len(todo), "status": "warning",
                     "text": ("The workflow's approval step: approve to make them." if workflow else
                              "Approve them as auditd_tuning_approved (each line as it is, or its first word: backlog, queue, kernel, "
                              "containers). The containers line needs your ISSO's approval first.") +
                             " Nothing is rebooted; with immutable rules (-e 2) the rules apply at the next reboot.",
                     "lines": todo})
    if wait:
        secs.append({"title": "Notes", "lines": wait})
    secs.append({"title": "How this was made", "lines": [
        "Read: auditctl -s, /proc/cmdline, grubby --info=ALL, /etc/audit/auditd.conf, /etc/audit/rules.d. Read only%s." %
        (" (Check)" if check else ""),
        "A change is made only when approved: each file backed up first; auditd restarted (service auditd restart) only for "
        "auditd.conf - a failed restart puts the file back. Never a reboot; -e, -f and the STIG rules are never changed."]})
    return {"title": "Linux - auditd tuning: %d host(s), %d with changes to approve, %d waiting for a reboot" % (len(rows), n_change, n_wait),
            "status": status,
            "summary": [{"label": "Hosts", "value": len(rows)},
                        {"label": "Changes to approve", "value": n_change, "status": "warning" if n_change else "ok"},
                        {"label": "Waiting for a reboot", "value": n_wait},
                        {"label": "Records lost", "value": n_lost, "status": "warning" if n_lost else "ok"}],
            "sections": secs}


class FilterModule(object):
    def filters(self):
        return {"auditd_tune_read": auditd_tune_read, "auditd_tune_state": auditd_tune_state, "auditd_tune_pick": auditd_tune_pick,
                "auditd_tune_workflow": auditd_tune_workflow, "auditd_tune_report": auditd_tune_report}
