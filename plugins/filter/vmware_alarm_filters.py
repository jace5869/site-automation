# -*- coding: utf-8 -*-
"""Filters for the VMware alarms report and its ACT analysis (roles/vmware_vm: alarms.yml,
alarm_act.yml). Input: what roles/vmware_vm/library/site_vmware_alarms.py returns. Output: reports
in the layout roles/site_email renders (title, summary, sections with columns / rows, and per row
row_status and cell_status for the colours), the list of problems ACT analyzes, its evidence and
prompt, and ACT's answer read back into a table."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import json
import re

RANK = {"critical": 0, "warning": 1, "unknown": 2, "info": 3, "ok": 4, "": 5}
PLURAL = {"vcenter": "vCenter", "datacenter": "datacenters", "cluster": "clusters", "host": "hosts", "datastore": "datastores",
          "vm": "VMs", "network": "networks"}
TYPE_LABEL = {"vcenter": "vCenter", "datacenter": "Datacenter", "cluster": "Cluster", "host": "Host",
              "datastore": "Datastore", "vm": "VM", "network": "Network", "folder": "Folder"}
MARK_BEGIN, MARK_END = "BEGIN_ACT_ANALYSIS", "END_ACT_ANALYSIS"


def _t(iso):
    """'2026-10-05T03:52:47Z' -> '2026-10-05 03:52:47'."""
    return str(iso or "").replace("T", " ").replace("Z", "")


def _hours(h):
    try:
        h = float(h)
    except (TypeError, ValueError):
        return str(h)
    return str(int(h)) if h == int(h) else str(h)


def _sev(s):
    return str(s or "").upper()


def _worst(statuses):
    s = sorted((x for x in statuses if x), key=lambda x: RANK.get(x, 9))
    return s[0] if s else ""


def _where(e, vcenter):
    return e.get("host") or e.get("cluster") or e.get("datacenter") or vcenter or "vCenter"


def vm_alarm_groups(data, opts=None):
    """The events, grouped the way the report shows them:
    logins      failed logins per user, source and server: count, first, last (critical from
                opts.login_critical failures on)
    connection  per host: not connected now, or connection events in the window
    vm          VM events one by one (newest first)
    other       other errors / warnings per event type and object: count, first, last, latest message"""
    d, o = data or {}, opts or {}
    vc = d.get("vcenter") or "vCenter"
    crit_n = int(o.get("login_critical") or 10)
    ev = d.get("events") or []
    logins = {}
    for e in (x for x in ev if x.get("group") == "login"):
        k = (e.get("login_user") or "?", e.get("ip") or "?", _where(e, vc))
        g = logins.setdefault(k, {"user": k[0], "ip": k[1], "where": k[2], "cluster": e.get("cluster", ""),
                                  "datacenter": e.get("datacenter", ""), "count": 0, "first": e.get("time", ""),
                                  "last": e.get("time", ""), "message": e.get("message", "")})
        g["count"] += 1
        g["first"] = min(g["first"], e.get("time", "")) or e.get("time", "")
        if e.get("time", "") >= g["last"]:
            g["last"], g["message"] = e.get("time", ""), e.get("message", "")
    for g in logins.values():
        g["severity"] = "critical" if g["count"] >= crit_n else "warning"
    hosts = {h.get("name"): h for h in d.get("hosts") or []}
    conn = {}
    for h in hosts.values():
        if h.get("connection") not in ("connected", ""):
            conn[h["name"]] = {"host": h["name"], "cluster": h.get("cluster", ""), "datacenter": h.get("datacenter", ""),
                               "state": h.get("connection", ""), "maintenance": bool(h.get("maintenance")),
                               "events": 0, "last": "", "message": "", "worst": "critical"}
    for e in (x for x in ev if x.get("group") == "connection"):
        hn = e.get("host") or "?"
        h = hosts.get(hn, {})
        c = conn.setdefault(hn, {"host": hn, "cluster": e.get("cluster") or h.get("cluster", ""),
                                 "datacenter": e.get("datacenter") or h.get("datacenter", ""),
                                 "state": h.get("connection", "") or "?", "maintenance": bool(h.get("maintenance")),
                                 "events": 0, "last": "", "message": "", "worst": ""})
        c["events"] += 1
        if e.get("time", "") >= c["last"]:
            c["last"], c["message"] = e.get("time", ""), e.get("message", "")
        c["worst"] = _worst([c["worst"], "warning" if e.get("severity") == "critical" else e.get("severity")])
    for c in conn.values():
        # not connected now = critical; lost the connection in the window but back now = warning
        c["severity"] = "critical" if c["state"] not in ("connected", "") else (c["worst"] or "info")
    other = {}
    for e in (x for x in ev if x.get("group") == "other"):
        obj, kind = ((e["vm"], "vm") if e.get("vm") else (e["host"], "host") if e.get("host")
                     else (e["cluster"], "cluster") if e.get("cluster") else (e["datacenter"], "datacenter")
                     if e.get("datacenter") else (vc, "vcenter"))
        k = (e.get("type", ""), obj)
        g = other.setdefault(k, {"type": e.get("type", ""), "object": obj, "object_type": kind, "cluster": e.get("cluster", ""),
                                 "datacenter": e.get("datacenter", ""), "count": 0, "first": e.get("time", ""),
                                 "last": e.get("time", ""), "message": e.get("message", ""), "severity": ""})
        g["count"] += 1
        g["first"] = min(g["first"], e.get("time", "")) or e.get("time", "")
        if e.get("time", "") >= g["last"]:
            g["last"], g["message"] = e.get("time", ""), e.get("message", "")
        g["severity"] = _worst([g["severity"], e.get("severity")])
    by_sev = lambda x: (RANK.get(x.get("severity"), 9), "~" if not x.get("last") else "", x.get("last", ""))
    return {
        "logins": sorted(sorted(logins.values(), key=lambda x: x["last"], reverse=True), key=lambda x: (RANK[x["severity"]], -x["count"])),
        "connection": sorted(sorted(conn.values(), key=lambda x: x["last"], reverse=True), key=lambda x: RANK.get(x["severity"], 9)),
        "vm": sorted([x for x in ev if x.get("group") == "vm"], key=lambda x: x.get("time", ""), reverse=True),
        "other": sorted(sorted(other.values(), key=lambda x: x["last"], reverse=True), key=lambda x: (RANK.get(x["severity"], 9), -x["count"])),
    }


def _table(rows, status_col=0):
    """rows of (status, [cells]) -> rows, row_status, cell_status (the status cell coloured)."""
    out, rs, cs = [], [], []
    for st, cells in rows:
        out.append(cells)
        rs.append(st if st in ("critical", "warning", "info", "unknown") else "")
        c = [""] * len(cells)
        if status_col is not None and st:
            c[status_col] = st
        cs.append(c)
    return out, rs, cs


def _section(title, columns, rows, text="", status="", status_col=0, max_rows=0):
    rows = list(rows)
    more = len(rows) - max_rows if max_rows and len(rows) > max_rows else 0
    if more:
        rows = rows[:max_rows]
    r, rs, cs = _table(rows, status_col)
    if not r:                   # nothing: the text says so, no empty table
        return {"title": title, "text": text} if text else {"title": title, "lines": ["None."]}
    s = {"title": title, "columns": columns, "rows": r, "row_status": rs, "cell_status": cs}
    if more:
        text = (text + " " if text else "") + "Only the first %d are shown (%d more: see the job's artifacts or raise vm_alarm_max_rows)." % (max_rows, more)
    if text:
        s["text"] = text
    if status:
        s["status"] = status
    return s


def vm_alarm_report(data, opts=None):
    """The alarms report (roles/site_email layout)."""
    d, o = data or {}, opts or {}
    vc = d.get("vcenter") or "vCenter"
    hours = _hours(d.get("window_hours", o.get("hours", 24)))
    max_rows = int(o.get("max_rows") or 200)
    g = vm_alarm_groups(d, o)
    alarms = d.get("alarms") or []
    crit = [a for a in alarms if a.get("severity") == "critical"]
    warn = [a for a in alarms if a.get("severity") == "warning"]
    hosts = d.get("hosts") or []
    notconn = [h for h in hosts if h.get("connection") not in ("connected", "")]
    issues = d.get("config_issues") or []
    login_n = sum(x["count"] for x in g["logins"])
    vm_bad = [e for e in g["vm"] if e.get("severity") in ("critical", "warning")]
    other_n = sum(x["count"] for x in g["other"])
    events_on = float(d.get("window_hours") or 0) > 0

    parts = ["%d critical, %d warning alarm(s)" % (len(crit), len(warn)) if alarms else "no triggered alarms"]
    if notconn:
        parts.append("%d host(s) not connected" % len(notconn))
    lost = [c for c in g["connection"] if c["severity"] == "warning"]
    if lost:
        parts.append("%d host(s) lost the connection" % len(lost))
    if login_n:
        parts.append("%d failed login(s)" % login_n)
    if vm_bad:
        parts.append("%d VM event(s) to check" % len(vm_bad))
    title = "VMware alarms report: " + ", ".join(parts) + (" (events: last %s h)" % hours if events_on else "")
    worst = _worst([a.get("severity") for a in alarms] + ["critical" if notconn else ""]
                   + [x["severity"] for x in g["logins"] + g["connection"] + g["other"]]
                   + [e.get("severity") for e in vm_bad] + ["warning" if issues else ""])
    status = worst if worst in ("critical", "warning") else "ok"
    types = o.get("types") or ["vcenter", "datacenter", "cluster", "host"]
    tl = [PLURAL.get(t, t) for t in types]
    subtitle = "vCenter %s - alarms now on %s%s - read %s UTC%s" % (
        vc, ", ".join(tl[:-1]) + " and " + tl[-1] if len(tl) > 1 else tl[0],
        ("; events of the last %s hours" % hours) if events_on else "", _t(d.get("read_at")),
        (" - datacenter " + o["datacenter"]) if o.get("datacenter") else "")
    summary = [
        {"label": "Critical alarms", "value": len(crit), "status": "critical" if crit else "ok"},
        {"label": "Warning alarms", "value": len(warn), "status": "warning" if warn else "ok"},
        {"label": "Hosts not connected", "value": len(notconn), "status": "critical" if notconn else "ok"},
    ]
    if events_on:
        summary += [
            {"label": "Failed logins", "value": login_n, "status": _worst([x["severity"] for x in g["logins"]]) or "ok"},
            {"label": "VM events to check", "value": len(vm_bad), "status": _worst([e.get("severity") for e in vm_bad]) or "ok"},
            {"label": "Other errors / warnings", "value": other_n, "status": _worst([x["severity"] for x in g["other"]]) or "ok"},
        ]
    summary.append({"label": "Configuration issues", "value": len(issues), "status": "warning" if issues else "ok"})

    def ack(a):
        if not a.get("acknowledged"):
            return "no"
        return "yes" + (" - " + a["acknowledged_by"] if a.get("acknowledged_by") else "") + (
            ", " + _t(a["acknowledged_time"]) if a.get("acknowledged_time") else "")

    sections = [_section(
        "Triggered alarms", ["Severity", "Type", "Object", "Alarm", "Since (UTC)", "Acknowledged", "Cluster", "Datacenter"],
        [(a.get("severity"), [_sev(a.get("severity")), TYPE_LABEL.get(a.get("entity_type"), a.get("entity_type")), a.get("entity"),
                              a.get("alarm"), _t(a.get("time")), ack(a), a.get("cluster"), a.get("datacenter")]) for a in alarms],
        "As vCenter shows them now: red = critical, yellow = warning. Acknowledged = someone has seen it in vCenter; "
        "the alarm is still active until its cause is gone." if alarms else "No alarm is triggered.",
        _worst([a.get("severity") for a in alarms]), max_rows=max_rows)]
    sections.append(_section(
        "Host connection", ["Severity", "Host", "State now", "Maintenance", "Connection events", "Last event (UTC)", "Last message",
                            "Cluster", "Datacenter"],
        [(c["severity"], [_sev(c["severity"]), c["host"], c["state"], "yes" if c["maintenance"] else "no", c["events"],
                          _t(c["last"]), c["message"], c["cluster"], c["datacenter"]]) for c in g["connection"]],
        ("Hosts not connected to vCenter now (critical), and hosts that lost the connection in the last %s h but are back (warning)." % hours)
        if g["connection"] else "Every host is connected%s." % (", and none lost its connection in the last %s h" % hours if events_on else ""),
        _worst([c["severity"] for c in g["connection"]]), max_rows=max_rows))
    if events_on:
        sections.append(_section(
            "Failed logins", ["Severity", "User", "From", "Where", "Failures", "First (UTC)", "Last (UTC)"],
            [(x["severity"], [_sev(x["severity"]), x["user"], x["ip"], x["where"], x["count"], _t(x["first"]), _t(x["last"])])
             for x in g["logins"]],
            ("Wrong user name or password, per user, source and server. %d or more = critical: an account being locked "
             "out, a service or scanner with an old password, or someone guessing." % int(o.get("login_critical") or 10))
            if g["logins"] else "No failed login in the last %s h." % hours,
            _worst([x["severity"] for x in g["logins"]]), max_rows=max_rows))
        sections.append(_section(
            "Virtual machine events", ["Severity", "Time (UTC)", "VM", "Event", "Host", "Cluster", "Datacenter", "By"],
            [(e.get("severity"), [_sev(e.get("severity")), _t(e.get("time")), e.get("vm"), e.get("message"), e.get("host"),
                                  e.get("cluster"), e.get("datacenter"), e.get("user") or "-"]) for e in g["vm"]],
            "Guest shutdowns and restarts, power-offs, resets and HA restarts. Info = someone did it (By); warning = HA "
            "restarted or reset it, or it was powered off with no user; critical = a failover or power-on failed."
            if g["vm"] else "No VM was shut down, powered off, reset or restarted by HA in the last %s h." % hours,
            _worst([e.get("severity") for e in vm_bad]), max_rows=max_rows))
        sections.append(_section(
            "Other errors and warnings", ["Severity", "Object", "Event", "Count", "First (UTC)", "Last (UTC)", "Latest message"],
            [(x["severity"], [_sev(x["severity"]), "%s %s" % (TYPE_LABEL.get(x["object_type"], ""), x["object"]), x["type"], x["count"],
                              _t(x["first"]), _t(x["last"]), x["message"]]) for x in g["other"]],
            ("vCenter's error and warning events, the same event on the same object counted once."
             + (" Only the newest %s events were read (vm_alarm_max_events)." % o.get("max_events", "") if d.get("events_capped") else ""))
            if g["other"] else "No other error or warning event in the last %s h." % hours,
            _worst([x["severity"] for x in g["other"]]), max_rows=max_rows))
    sections.append(_section(
        "Configuration issues", ["Severity", "Type", "Object", "Issue", "Cluster", "Datacenter"],
        [("warning", ["WARNING", TYPE_LABEL.get(i.get("entity_type"), i.get("entity_type")), i.get("entity"), i.get("message"),
                      i.get("cluster"), i.get("datacenter")]) for i in issues],
        "What vCenter shows as configuration issues (e.g. SSH or the ESXi shell left on, HA problems)." if issues
        else "No configuration issue.", "warning" if issues else "", max_rows=max_rows))

    al_by = {}
    for a in alarms:
        if a.get("entity_type") == "host":
            al_by.setdefault(a.get("moid"), []).append(a.get("severity"))
    ev_by = {}
    for e in d.get("events") or []:
        if e.get("host") and e.get("severity") in ("critical", "warning"):
            ev_by[e["host"]] = ev_by.get(e["host"], 0) + 1
    rows = []
    for h in hosts:
        sevs = al_by.get(h.get("moid"), [])
        st = ("critical" if h.get("connection") not in ("connected", "") or "critical" in sevs else
              "warning" if "warning" in sevs else "info" if h.get("maintenance") else "")
        cells = [h.get("name"), h.get("cluster"), h.get("datacenter"), h.get("connection"), "yes" if h.get("maintenance") else "no",
                 ("%s (%s)" % (h.get("version"), h.get("build"))) if h.get("version") else "", " ".join(x for x in (h.get("vendor"), h.get("model")) if x),
                 len(sevs), ev_by.get(h.get("name"), 0)]
        rows.append((st, cells))
    s = _section("All hosts", ["Host", "Cluster", "Datacenter", "Connection", "Maintenance", "ESXi", "Hardware", "Alarms",
                               "Errors / warnings (%s h)" % hours if events_on else "Errors / warnings"],
                 rows, "Red = not connected or a critical alarm, amber = a warning alarm, blue = in maintenance mode.",
                 max_rows=0, status_col=None)
    for i, (st, _) in enumerate(rows):            # colour the Connection cell of a host that is not connected
        if "cell_status" in s and hosts[i].get("connection") not in ("connected", ""):
            s["cell_status"][i][3] = "critical"
    sections.append(s)
    return {"title": title, "status": status, "subtitle": subtitle, "summary": summary, "sections": sections,
            "footer": "Read-only."}


def vm_alarm_problems(data, opts=None):
    """What ACT analyzes, most severe first, numbered P1, P2, ...: the critical and warning alarms,
    hosts not connected (or that lost the connection), failed-login groups, VM events to check,
    other error / warning groups, and (opts.config_issues, default true) configuration issues.
    -> {'problems': first opts.max_items (40), 'not_analyzed': the rest}"""
    d, o = data or {}, opts or {}
    vc = d.get("vcenter") or "vCenter"
    g = vm_alarm_groups(d, o)
    out = []
    for a in d.get("alarms") or []:
        if a.get("severity") in ("critical", "warning"):
            out.append({"kind": "alarm", "severity": a["severity"], "object_type": a.get("entity_type", ""), "object": a.get("entity", ""),
                        "cluster": a.get("cluster", ""), "datacenter": a.get("datacenter", ""),
                        "problem": 'alarm "%s"' % a.get("alarm", ""), "detail": a.get("alarm_description", ""),
                        "time": a.get("time", ""), "count": 1})
    for c in g["connection"]:
        if c["severity"] in ("critical", "warning"):
            out.append({"kind": "connection", "severity": c["severity"], "object_type": "host", "object": c["host"],
                        "cluster": c["cluster"], "datacenter": c["datacenter"],
                        "problem": ("host is %s (not connected to vCenter)" % c["state"]) if c["severity"] == "critical"
                        else "host lost its connection to vCenter %d time(s), connected again now" % c["events"],
                        "detail": c["message"], "time": c["last"], "count": c["events"]})
    for x in g["logins"]:
        out.append({"kind": "login", "severity": x["severity"], "object_type": "host" if x["where"] != vc else "vcenter",
                    "object": x["where"], "cluster": x["cluster"], "datacenter": x["datacenter"],
                    "problem": "%d failed login(s) as %s from %s" % (x["count"], x["user"], x["ip"]),
                    "detail": "first %s, last %s: %s" % (_t(x["first"]), _t(x["last"]), x["message"]), "time": x["last"], "count": x["count"]})
    for e in g["vm"]:
        if e.get("severity") in ("critical", "warning"):
            out.append({"kind": "vm", "severity": e["severity"], "object_type": "vm", "object": e.get("vm", ""),
                        "cluster": e.get("cluster", ""), "datacenter": e.get("datacenter", ""),
                        "problem": "VM event %s" % e.get("type", ""), "detail": "%s (host %s, by %s)" % (e.get("message", ""), e.get("host") or "?", e.get("user") or "nobody"),
                        "time": e.get("time", ""), "count": 1})
    for x in g["other"]:
        out.append({"kind": "event", "severity": x["severity"], "object_type": x["object_type"], "object": x["object"],
                    "cluster": x["cluster"], "datacenter": x["datacenter"],
                    "problem": "%s event %s, %d time(s)" % ("error" if x["severity"] == "critical" else "warning", x["type"], x["count"]),
                    "detail": "first %s, last %s: %s" % (_t(x["first"]), _t(x["last"]), x["message"]), "time": x["last"], "count": x["count"]})
    if o.get("config_issues", True):
        for i in d.get("config_issues") or []:
            out.append({"kind": "config", "severity": "warning", "object_type": i.get("entity_type", ""), "object": i.get("entity", ""),
                        "cluster": i.get("cluster", ""), "datacenter": i.get("datacenter", ""),
                        "problem": "configuration issue", "detail": i.get("message", ""), "time": i.get("time", ""), "count": 1})
    out.sort(key=lambda p: RANK.get(p["severity"], 9))       # stable: alarms first within a severity
    cap = int(o.get("max_items") or 40)
    for n, p in enumerate(out, 1):
        p["id"] = "P%d" % n
    return {"problems": out[:cap], "not_analyzed": out[cap:]}


def _obj(p):
    where = " / ".join(x for x in (p.get("cluster"), p.get("datacenter")) if x)
    return "%s %s%s" % (TYPE_LABEL.get(p.get("object_type"), p.get("object_type") or ""), p.get("object", ""),
                        " (%s)" % where if where else "")


def vm_alarm_evidence(data, problems, opts=None):
    """The evidence ACT reads: the problems, the hosts' state and versions, and each involved object's
    recent warning / error events (opts.events_per_object, default 15), within opts.max_chars."""
    d, o = data or {}, opts or {}
    nl = "\n"
    per = int(o.get("events_per_object") or 15)
    lines = ["vCenter %s, read %s UTC. Events: the last %s hours." % (d.get("vcenter", ""), _t(d.get("read_at")), _hours(d.get("window_hours"))),
             "", "PROBLEMS TO ANALYZE"]
    for p in problems or []:
        lines.append("%s [%s] %s - %s" % (p["id"], _sev(p["severity"]), _obj(p), p["problem"]) + (" (last %s UTC)" % _t(p["time"]) if p.get("time") else ""))
        if p.get("detail"):
            lines.append("    " + p["detail"])
    involved = {p.get("object") for p in problems or []}
    lines += ["", "HOSTS (state, ESXi version, hardware)"]
    for h in d.get("hosts") or []:
        lines.append("%s: cluster %s, datacenter %s, connection %s, power %s, maintenance %s, ESXi %s build %s, %s %s, status %s" % (
            h.get("name"), h.get("cluster") or "-", h.get("datacenter") or "-", h.get("connection"), h.get("power"),
            "yes" if h.get("maintenance") else "no", h.get("version") or "?", h.get("build") or "?", h.get("vendor", ""), h.get("model", ""),
            h.get("status", "")))
    issues = d.get("config_issues") or []
    if issues:
        lines += ["", "CONFIGURATION ISSUES"]
        lines += ["%s %s: %s" % (TYPE_LABEL.get(i.get("entity_type"), ""), i.get("entity"), i.get("message")) for i in issues]
    lines += ["", "RECENT EVENTS of the objects above (newest first, up to %d each)" % per]
    by_obj = {}
    for e in d.get("events") or []:
        for k in (e.get("vm"), e.get("host"), e.get("cluster"), e.get("datacenter")):
            if k and k in involved:
                by_obj.setdefault(k, []).append(e)
                break
    for k in sorted(by_obj):
        lines.append("[%s]" % k)
        for e in by_obj[k][:per]:
            lines.append("  %s %s %s%s: %s" % (_t(e.get("time")), _sev(e.get("severity")), e.get("type"),
                                                " user=" + e["user"] if e.get("user") else "", e.get("message")))
    text = nl.join(lines)
    cap = int(o.get("max_chars") or 60000)
    return text if len(text) <= cap else text[:cap] + nl + "[... evidence cut at %d characters ...]" % cap


def vm_alarm_act_task(problems, opts=None):
    """The task (prompt) for ACT."""
    o = opts or {}
    ids = ", ".join(p["id"] for p in problems or [])
    return "\n".join([
        "VMware vCenter alarm analysis for an operations team.",
        "You cannot run any commands in this task, and none are needed: the evidence piped to you was read from vCenter "
        "(its triggered alarms, configuration issues, every host's state and version, and the warning and error events of the "
        "last %s hours). Base every conclusion on that evidence and quote the lines that support it." % _hours(o.get("hours", 24)),
        "",
        "Analyze each problem in the PROBLEMS TO ANALYZE list (%s). For each one give:" % ids,
        "- cause: the most likely cause, in one or two sentences;",
        "- evidence: the evidence lines that support it;",
        "- fix: what a VMware administrator should do - steps in the vSphere Client, or an esxcli / PowerCLI command - and what to "
        "check first. A person applies it; never claim you changed anything;",
        "- confidence: how sure you are that the cause is right, a whole number from 0 to 100: 80 or more = the evidence shows it; "
        "50-79 = likely, other causes possible; under 50 = a guess (then say in the fix what to check to be sure).",
        "Several problems may share one cause (e.g. a host that lost its network shows several alarms): say so in each of them.",
        "Failed logins from one address with one account usually mean a service or scanner with an old password: name the account and the source.",
        "",
        "End your answer with exactly one JSON object between a line %s and a line %s, nothing else between them:" % (MARK_BEGIN, MARK_END),
        MARK_BEGIN,
        '{"overall": "two or three sentences: what to do first, and which problems share a cause",',
        ' "items": [{"id": "P1", "cause": "...", "evidence": "...", "fix": "...", "confidence": 85}]}',
        MARK_END,
        "Include every id. Use plain text inside the JSON strings (no markdown).",
    ] + (["", str(o["extra"])] if o.get("extra") else []))


def _conf(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        n = v * 100 if 0 < v <= 1 and isinstance(v, float) else v
        return max(0, min(100, int(round(n))))
    s = str(v or "").strip().lower()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*%?$", s)
    if m:
        return _conf(float(m.group(1)) if "." in m.group(1) else int(m.group(1)))
    return {"high": 85, "medium": 60, "med": 60, "low": 30}.get(s)


def _strings(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for x in v.values():
            for y in _strings(x):
                yield y
    elif isinstance(v, list):
        for x in v:
            for y in _strings(x):
                yield y


def act_analysis_parse(text, problems=None, depth=0):
    """ACT's answer -> {'parsed': bool, 'overall': str, 'by_id': {P1: {cause, evidence, fix, confidence}},
    'error': why it could not be read}. Reads the JSON between the markers, else a ```json block,
    else the outermost {...}; tolerates trailing commas and a confidence given as 85, "85%", 0.85 or
    high / medium / low."""
    t = str(text or "")
    cands = []
    m = re.search(MARK_BEGIN + r"\s*(.*?)\s*" + MARK_END, t, re.S)
    if m:
        cands.append(m.group(1))
    cands += re.findall(r"```(?:json)?\s*(.*?)```", t, re.S)
    i, j = t.find("{"), t.rfind("}")
    if 0 <= i < j:
        cands.append(t[i:j + 1])
    obj = None
    for c in cands:
        c = c.strip()
        for attempt in (c, re.sub(r",\s*([}\]])", r"\1", c)):
            try:
                v = json.loads(attempt)
            except ValueError:
                continue
            if isinstance(v, list):
                v = {"items": v}
            if isinstance(v, dict) and isinstance(v.get("items"), list):
                obj = v
                break
        if obj is not None:
            break
    if obj is None and depth < 2:
        # the analysis wrapped in another JSON reply (a model answering in an action format): look
        # inside its strings
        for c in reversed(cands):
            try:
                outer = json.loads(c.strip())
            except ValueError:
                continue
            for inner in _strings(outer):
                if MARK_BEGIN in inner or '"items"' in inner:
                    r = act_analysis_parse(inner, problems, depth + 1)
                    if r["parsed"]:
                        return r
    if obj is None:
        return {"parsed": False, "overall": "", "by_id": {},
                "error": "ACT's answer has no readable JSON block" if t.strip() else "ACT gave no answer"}
    known = {p["id"] for p in problems or []}
    by_id = {}
    for it in obj["items"]:
        if not isinstance(it, dict):
            continue
        pid = str(it.get("id") or it.get("problem") or "").strip().upper()
        if re.match(r"^\d+$", pid):
            pid = "P" + pid
        if known and pid not in known:
            continue
        by_id[pid] = {"cause": " ".join(str(it.get("cause") or it.get("likely_cause") or "").split()),
                      "evidence": " ".join(str(it.get("evidence") or "").split()),
                      "fix": " ".join(str(it.get("fix") or it.get("remediation") or "").split()),
                      "confidence": _conf(it.get("confidence"))}
    return {"parsed": True, "overall": " ".join(str(obj.get("overall") or "").split()), "by_id": by_id, "error": ""}


def _conf_status(c):
    if c is None:
        return ""
    return "ok" if c >= 80 else "warning" if c >= 50 else "critical"


def _conf_text(c):
    if c is None:
        return ""
    return "%d%% %s" % (c, "high" if c >= 80 else "medium" if c >= 50 else "low")


def vm_alarm_act_report(problems, parsed=None, opts=None):
    """The ACT analysis report (roles/site_email layout). opts: vcenter, hours, provider, model,
    ran (bool), reason (why ACT did not run or failed), raw (ACT's answer), not_analyzed (list), max_items."""
    ps, pa, o = problems or [], parsed or {}, opts or {}
    by = pa.get("by_id") or {}
    ran = bool(o.get("ran"))
    crit = sum(1 for p in ps if p["severity"] == "critical")
    warn = sum(1 for p in ps if p["severity"] == "warning")
    confs = [by[p["id"]]["confidence"] for p in ps if p["id"] in by and by[p["id"]]["confidence"] is not None]
    if not ran:
        title = "VMware alarms ACT analysis: ACT did not run - %d problem(s) not analyzed" % len(ps)
    elif not pa.get("parsed"):
        title = "VMware alarms ACT analysis: %d problem(s) - ACT's answer is below as text" % len(ps)
    else:
        title = "VMware alarms ACT analysis: likely causes and fixes for %d problem(s)" % len(ps)
    worst = _worst([p["severity"] for p in ps])
    status = "critical" if not ran else (worst if worst in ("critical", "warning") else "info")
    model = " ".join(x for x in (o.get("provider"), o.get("model")) if x)
    subtitle = ("ACT%s read vCenter %s's alarms and the events of the last %s h. Likely causes, not certain ones: check before "
                "you change anything." % (" (" + model + ")" if model else "", o.get("vcenter", ""), _hours(o.get("hours", 24))))
    summary = [{"label": "Problems", "value": len(ps)},
               {"label": "Critical", "value": crit, "status": "critical" if crit else "ok"},
               {"label": "Warning", "value": warn, "status": "warning" if warn else "ok"}]
    if ran and pa.get("parsed"):
        summary += [{"label": "High confidence (80+)", "value": sum(1 for c in confs if c >= 80), "status": "ok"},
                    {"label": "Medium (50-79)", "value": sum(1 for c in confs if 50 <= c < 80), "status": "warning"},
                    {"label": "Low (under 50)", "value": sum(1 for c in confs if c < 50), "status": "critical"}]
    sections = []
    if not ran:
        sections.append({"title": "Why ACT did not run", "status": "critical",
                         "lines": [o.get("reason") or "unknown", "The problems are listed below without an analysis."]})
    elif pa.get("parsed") and pa.get("overall"):
        sections.append({"title": "What to do first", "status": worst if worst in ("critical", "warning") else "", "lines": [pa["overall"]]})
    rows, rs, cs = [], [], []
    for p in ps:
        a = by.get(p["id"]) or {}
        c = a.get("confidence")
        rows.append([p["id"], _sev(p["severity"]), _obj(p), p["problem"] + (" - " + p["detail"] if p.get("detail") else ""),
                     a.get("cause") or ("" if not ran or not pa.get("parsed") else "ACT gave no answer for this one"),
                     a.get("evidence", ""), a.get("fix", ""), _conf_text(c)])
        rs.append(p["severity"] if p["severity"] in ("critical", "warning") else "")
        cs.append(["", p["severity"] if p["severity"] in ("critical", "warning", "info") else "", "", "", "", "", "", _conf_status(c)])
    sections.append({"title": "Likely causes and fixes" if ran and pa.get("parsed") else "Problems",
                     "columns": ["#", "Severity", "Object", "Problem", "Likely cause", "Evidence", "Fix", "Confidence"],
                     "rows": rows, "row_status": rs, "cell_status": cs,
                     "text": "Confidence: ACT's own estimate that the cause is right - green 80+ (the evidence shows it), "
                             "amber 50-79 (likely), red under 50 (a guess: check first)." if ran and pa.get("parsed") else ""})
    if ran and not pa.get("parsed"):
        sections.append({"title": "ACT's answer", "text": pa.get("error", ""),
                         "lines": [x for x in str(o.get("raw") or "").splitlines() if x.strip()] or ["(empty)"]})
    na = o.get("not_analyzed") or []
    if na:
        sections.append({"title": "Not analyzed (over the limit of %s, vm_alarm_act_max_items)" % o.get("max_items", 40),
                         "columns": ["Severity", "Object", "Problem"],
                         "rows": [[_sev(p["severity"]), _obj(p), p["problem"]] for p in na],
                         "row_status": [p["severity"] if p["severity"] in ("critical", "warning") else "" for p in na],
                         "cell_status": [[p["severity"] if p["severity"] in ("critical", "warning") else "", "", ""] for p in na]})
    for s in sections:
        if not s.get("text"):
            s.pop("text", None)
    return {"title": title, "status": status, "subtitle": subtitle, "summary": summary, "sections": sections,
            "footer": "ACT is an AI assistant: it read the evidence only, ran nothing and changed nothing. "
                      "Confidence is its own estimate."}


def vm_alarm_names(data):
    """Every name the evidence may contain (vCenter, datacenters, clusters, hosts and their short
    names, VMs): ACT replaces them with placeholders before anything reaches the model."""
    d = data or {}
    out = {d.get("vcenter", "")}
    for h in d.get("hosts") or []:
        for k in ("name", "cluster", "datacenter"):
            out.add(h.get(k, ""))
        out.add(str(h.get("name", "")).split(".")[0])
    for x in (d.get("alarms") or []) + (d.get("config_issues") or []):
        for k in ("entity", "cluster", "datacenter"):
            out.add(x.get(k, ""))
    for e in d.get("events") or []:
        for k in ("host", "vm", "cluster", "datacenter"):
            out.add(e.get(k, ""))
    return sorted(n for n in out if n and len(str(n)) > 2 and "," not in str(n))


class FilterModule(object):
    def filters(self):
        return {"vm_alarm_groups": vm_alarm_groups, "vm_alarm_report": vm_alarm_report,
                "vm_alarm_problems": vm_alarm_problems, "vm_alarm_evidence": vm_alarm_evidence,
                "vm_alarm_act_task": vm_alarm_act_task, "act_analysis_parse": act_analysis_parse,
                "vm_alarm_act_report": vm_alarm_act_report, "vm_alarm_names": vm_alarm_names}
