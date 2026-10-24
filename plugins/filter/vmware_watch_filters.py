# -*- coding: utf-8 -*-
"""Filters for the VM outage watch (roles/vmware_vm/tasks/watch.yml). Input: what
roles/vmware_vm/library/site_vmware_watch.py returns. Output: the conditions found (vm_watch_conditions),
the report in the layout roles/site_email renders (vm_watch_report), the artifacts (vm_watch_artifact)
and the check results the ServiceNow tickets job reads (vm_watch_health).

What counts:
  critical  a host not responding or disconnected (not in maintenance mode); a host's HA agent isolated,
            partitioned, down or in error; a datastore not accessible; all paths down or a permanent device
            loss in the window that did not clear; HA could not restart a VM, or found a host failed or
            isolated; a VM waiting on a question; a VM hung (no heartbeat AND no answer on the network, in
            every check of the run)
  warning   a VM HA restarted or reset in the window; a storage problem of the window that cleared since;
            a VM with no heartbeat that still answers on the network, or could not be probed (one signal
            only: never called hung); a host whose HA agent is unreachable or failed to uninitialize
  info      (report only) a suspect skipped this run - a vCenter task was running on it, or it recovered
            during the run
Nothing here restarts anything. Phase 1 (watch only): the hung VMs that phase 2 WOULD restart are listed,
so false positives can be counted before any action is turned on."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import fnmatch
import re

RANK = {"critical": 0, "warning": 1, "info": 2, "ok": 3, "": 4}
HA_CRITICAL = {"networkIsolated", "networkPartitionedFromMaster", "hostDown", "initializationError"}
HA_WARNING = {"fdmUnreachable", "uninitializationError"}
EVENT_SEVERITY = {
    "VmFailoverFailed": "critical", "DasHostFailedEvent": "critical", "DasHostIsolatedEvent": "critical",
    "com.vmware.vc.HA.DasHostFailedEvent": "critical", "com.vmware.vc.HA.DasHostIsolatedEvent": "critical",
    "com.vmware.vc.ha.VmRestartedByHAEvent": "warning", "VmRestartedOnAlternateHostEvent": "warning",
    "VmDasBeingResetEvent": "warning", "VmDasBeingResetWithScreenshotEvent": "warning",
    "com.vmware.vc.HA.ClusterFailoverActionInitiatedEvent": "warning"}
EVENT_LABEL = {
    "VmFailoverFailed": "HA could not restart the VM", "DasHostFailedEvent": "HA found the host failed",
    "DasHostIsolatedEvent": "HA found the host isolated", "com.vmware.vc.HA.DasHostFailedEvent": "HA found the host failed",
    "com.vmware.vc.HA.DasHostIsolatedEvent": "HA found the host isolated",
    "com.vmware.vc.ha.VmRestartedByHAEvent": "HA restarted the VM", "VmRestartedOnAlternateHostEvent": "HA restarted the VM on another host",
    "VmDasBeingResetEvent": "HA VM monitoring reset the VM (no heartbeat)",
    "VmDasBeingResetWithScreenshotEvent": "HA VM monitoring reset the VM (no heartbeat)",
    "com.vmware.vc.HA.ClusterFailoverActionInitiatedEvent": "HA started a failover in the cluster"}
# storage problems and the events that clear them
STORAGE = {"esx.problem.storage.apd.start": ("all paths down", ["esx.clear.storage.apd.exit"]),
           "esx.problem.storage.apd.timeout": ("all paths down, timed out (I/O to it now fails)", ["esx.clear.storage.apd.exit"]),
           "esx.problem.scsi.device.state.permanentloss": ("permanent device loss", ["esx.clear.scsi.device.state.permanentloss.deviceonline"]),
           "esx.problem.scsi.device.state.permanentloss.noopens": ("permanent device loss", ["esx.clear.scsi.device.state.permanentloss.deviceonline"]),
           "esx.problem.vmfs.heartbeat.timedout": ("VMFS heartbeat timed out", ["esx.problem.vmfs.heartbeat.recovered"])}
DEVICE_RE = re.compile(r"\b(naa\.[0-9a-fA-F]+|eui\.[0-9a-fA-F]+|t10\.\S+|mpx\.\S+)")


def _id(value):
    """A host name without its domain, in lower case; an IP address as it is (as site_filters.host_ids)."""
    s = str(value or "").strip().lower()
    if not s:
        return ""
    if re.fullmatch(r"[0-9.]+", s) or (":" in s and re.fullmatch(r"[0-9a-f:.]+", s)):
        return s
    return s.split(".")[0]


def _names(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [w for w in re.split(r"[\s,]+", value) if w]
    return [str(w).strip() for w in value if str(w).strip()]


def _matches(name, patterns):
    n = str(name or "").lower()
    return any(fnmatch.fnmatchcase(n, str(p).lower()) for p in _names(patterns))


def _device(e):
    for v in list((e.get("args") or {}).values()) + [e.get("message", "")]:
        m = DEVICE_RE.search(str(v or ""))
        if m:
            return m.group(1)
    return ""


def _cond(sev, kind, otype, obj, x, summary, todo, detail=""):
    return {"id": "vm-watch:%s:%s" % (kind, obj), "severity": sev, "kind": kind, "object_type": otype, "object": obj,
            "vcenter": x.get("vcenter", ""), "datacenter": x.get("datacenter", ""), "cluster": x.get("cluster", ""),
            "summary": summary, "detail": detail, "todo": todo}


def vm_watch_conditions(data, opts=None):
    """Everything the watch found, most severe first. opts: one_signal_severity (warning)."""
    d, o = data or {}, opts or {}
    one = o.get("one_signal_severity") or "warning"
    out = []
    for s in d.get("vcenters") or []:
        if s.get("error"):
            out.append(dict(_cond("critical", "vcenter", "vcenter", s.get("name", ""), {"vcenter": s.get("name", "")},
                                  "vCenter %s could not be read: %s" % (s.get("name", ""), s.get("error", "")),
                                  "Check that vCenter answers (its web client, its services in the appliance's management page on port 5480), "
                                  "and the VMware vCenter credential."), vcenter=s.get("name", "")))
    for h in d.get("hosts") or []:
        if h.get("maintenance") or h.get("standby") in ("in", "entering"):
            continue
        conn = h.get("connection") or ""
        if conn in ("notResponding", "disconnected"):
            out.append(_cond("critical", "host", "host", h["name"], h,
                             "ESXi host %s is %s%s: its VMs may be down (vSphere HA restarts them on other hosts when it is on)"
                             % (h["name"], "not responding" if conn == "notResponding" else "disconnected",
                                " (cluster %s)" % h["cluster"] if h.get("cluster") else ""),
                             "Check the host's management network and its console (iDRAC / iLO): is it up? Reconnect it in the vSphere "
                             "Client (Connection > Connect) once it answers. Do not restart its VMs elsewhere by hand while HA is working."))
            continue
        ha = h.get("ha_state") or ""
        if ha in HA_CRITICAL or ha in HA_WARNING:
            out.append(_cond("critical" if ha in HA_CRITICAL else "warning", "ha", "host", h["name"], h,
                             "the vSphere HA agent of host %s is %s" % (h["name"], ha),
                             "Isolated or partitioned: check the host's management network (and the isolation address). "
                             "Otherwise: Reconfigure for vSphere HA on the host in the vSphere Client."))
    for ds in d.get("datastores") or []:
        if not ds.get("accessible", True) and ds.get("maintenance") in ("normal", "", None):
            out.append(_cond("critical", "datastore", "datastore", ds["name"], ds,
                             "datastore %s is not accessible: the VMs on it are hung or down" % ds["name"],
                             "Check the storage array and the paths (Storage > Devices > Paths on a host); rescan the storage adapters."))
    # storage events: a problem with no clear event after it (same host, same device when both say) stays critical
    evs = sorted(d.get("events") or [], key=lambda e: (e.get("time", ""), e.get("key") or 0))      # oldest first; one second: by key
    seen = {}
    for i, e in enumerate(evs):
        if e.get("type") not in STORAGE:
            continue
        what, clears = STORAGE[e["type"]]
        dev = _device(e)
        cleared = next((c for c in evs[i + 1:] if c.get("type") in clears and c.get("host") == e.get("host")
                        and (not dev or not _device(c) or _device(c) == dev)), None)
        key = "%s:%s" % (e.get("host") or "?", dev or e["type"])
        c = _cond("warning" if cleared else "critical", "storage", "host", key, e,
                  "%s on host %s%s at %s%s" % (what, e.get("host") or "?", (" (device %s)" % dev) if dev else "", e.get("time", ""),
                                             (", cleared at %s" % cleared.get("time", "")) if cleared else ", not cleared"),
                  "Check the array and the SAN / storage network for the device; the VMs on its datastore may be hung.", e.get("message", ""))
        c["object"] = e.get("host") or "?"
        prev = seen.get(key)
        if prev is None or RANK[c["severity"]] < RANK[prev["severity"]]:
            seen[key] = c
    out += list(seen.values())
    seen = {}
    for e in evs:
        sev = EVENT_SEVERITY.get(e.get("type"))
        if not sev:
            continue
        obj, otype = (e.get("vm"), "vm") if e.get("vm") else (e.get("host") or e.get("cluster") or "?", "host" if e.get("host") else "cluster")
        c = _cond(sev, "event", otype, obj, e, "%s %s: %s (%s)" % ("VM" if otype == "vm" else otype, obj, EVENT_LABEL.get(e["type"], e["type"]),
                                                                    e.get("time", "")),
                  _event_todo(e["type"], otype), e.get("message", ""))
        c["id"] = "vm-watch:event:%s:%s" % (e["type"], obj)
        seen[c["id"]] = c                      # the newest of the window wins (evs is oldest first)
    out += list(seen.values())
    for q in d.get("questions") or []:
        out.append(_cond("critical", "question", "vm", q["name"], q,
                         "VM %s is waiting for an answer in vCenter: %s" % (q["name"], q.get("question") or "(no text)"),
                         "Answer it in the vSphere Client (the VM > Guest OS > Answer Question): until then the VM does not run."))
    for v in d.get("suspects") or []:
        signals = _signals(v)
        if v.get("verdict") == "hung":
            c = _cond("critical", "hung", "vm", v["name"], v,
                      "VM %s is hung: %s" % (v["name"], signals),
                      "Open its console in the vSphere Client. Frozen or a crash screen: restart it (guest restart first, a reset "
                      "only if that does nothing). Note the time for its owner.")
        elif v.get("verdict") in ("one-signal", "unprobed"):
            c = _cond(one, "no-heartbeat", "vm", v["name"], v,
                      "VM %s has no guest heartbeat, but %s: not called hung (one signal only)"
                      % (v["name"], "it answers on the network" if v["verdict"] == "one-signal" else "it could not be probed (%s)" % _last_probe(v)),
                      "VMware Tools stopped or hung in a guest that still runs: restart the Tools service in the guest "
                      "(vmtoolsd / VMware Tools), or update Tools.")
        else:
            continue
        c.update({"vm": _vm_ids(v), "signals": signals, "detail": "checked %s" % _span(v)})
        out.append(c)
    out.sort(key=lambda c: (RANK.get(c["severity"], 9), c["kind"], c["object"]))
    return out


def _event_todo(type_id, otype):
    if type_id == "VmFailoverFailed":
        return "HA could not restart it: power it on by hand on a host with room, and check the cluster's HA admission control."
    if otype == "vm":
        return ("Check in the guest why it stopped (its own logs) and that its services are back; check the host it ran on "
                "(its condition here, its events in vCenter).")
    if "Failover" in type_id:
        return "HA is restarting VMs: find the failed or isolated host (its condition here) and check the VMs come back."
    return "Check the host's management network and its console (iDRAC / iLO); see the host's condition."


def _last_probe(v):
    c = (v.get("checks") or [{}])[-1]
    return "%s%s" % (c.get("probe") or "not probed", (" at " + c["address"]) if c.get("address") else "")


def _signals(v):
    """'no guest heartbeat (red); no answer at 192.0.2.20, in 2 check(s)' - no times: a ticket's summary stays the same."""
    cs = v.get("checks") or []
    first = cs[0] if cs else {}
    return "%s; %s, in %d check(s)" % (first.get("reason") or "no heartbeat", _last_probe(v), len(cs))


def _span(v):
    cs = v.get("checks") or []
    return ("%s to %s" % (cs[0].get("time", ""), cs[-1].get("time", ""))) if len(cs) > 1 else (cs[0].get("time", "") if cs else "")


def _vm_ids(v):
    return {"name": v.get("name", ""), "moid": v.get("moid", ""), "vcenter": v.get("vcenter", ""), "esxi_host": v.get("esxi_host", ""),
            "hostname": v.get("hostname", ""), "ips": sorted(set([v.get("ip", "")] + list(v.get("ips") or [])) - {""})}


def vm_watch_not_judged(data):
    """The suspects skipped this run (a task running on it, recovered during the run): report rows."""
    out = []
    for v in (data or {}).get("suspects") or []:
        if v.get("verdict") == "busy":
            busy = next((c.get("busy") for c in v.get("checks") or [] if c.get("busy")), "")
            why = busy if busy.startswith("vCenter could not") else "a vCenter task was running on it: %s" % busy
        elif v.get("verdict") == "recovered":
            why = "it looked hung (%s), then recovered during the run" % ((v.get("checks") or [{}])[0].get("reason") or "")
        else:
            continue
        out.append({"name": v.get("name", ""), "vcenter": v.get("vcenter", ""), "datacenter": v.get("datacenter", ""),
                    "esxi_host": v.get("esxi_host", ""), "why": why})
    return out


def vm_watch_actions(conditions, opts=None):
    """Phase 2's decision, made but NOT carried out: each hung VM -> would restart / protected (ticket only) /
    over vm_watch_max_actions. opts: protected_ids (the AAP server's and the protected groups' host names and
    IP addresses), protected_names (vmware_protected_vms + vm_watch_protect patterns), aap_known (the
    inventory says which server is AAP), max_actions.
    -> {'would_restart': [...], 'protected': [...], 'over_limit': [...]}, each {vm, vcenter, moid, esxi_host, why}"""
    o = opts or {}
    pids = set(_id(i) for i in o.get("protected_ids") or [] if i)
    names = o.get("protected_names") or []
    cap = int(o.get("max_actions", 5))
    out = {"would_restart": [], "protected": [], "over_limit": []}
    for c in conditions or []:
        if c.get("kind") != "hung":
            continue
        v = c.get("vm") or {}
        row = {"vm": v.get("name", ""), "vcenter": v.get("vcenter", ""), "moid": v.get("moid", ""), "esxi_host": v.get("esxi_host", ""),
               "why": "%s (%s)" % (c.get("signals", ""), c.get("detail", ""))}
        ids = set(_id(x) for x in [v.get("name"), v.get("hostname")] + list(v.get("ips") or []) if x)
        if not o.get("aap_known", True):
            out["protected"].append(dict(row, why="the inventory does not say which VM is the AAP server (group aap or "
                                                  "vmware_protected_vms): nothing would be restarted"))
        elif ids & pids:
            out["protected"].append(dict(row, why="the AAP server, or a host of vmware_protected_groups"))
        elif _matches(v.get("name"), names):
            out["protected"].append(dict(row, why="vmware_protected_vms / vm_watch_protect"))
        elif len(out["would_restart"]) >= cap:
            out["over_limit"].append(dict(row, why="over vm_watch_max_actions (%d): this many hung VMs at once means something "
                                                   "bigger - a host, the storage or the network" % cap))
        else:
            out["would_restart"].append(row)
    return out


def _where(c, multi):
    dc = c.get("datacenter") or ""
    if multi and c.get("vcenter"):
        dc = "%s / %s" % (str(c["vcenter"]).split(".")[0], dc) if dc else str(c["vcenter"]).split(".")[0]
    return " / ".join(x for x in (c.get("cluster"), dc) if x)


def _counts(d):
    return "%d host(s), %d datastore(s), %d powered-on VM(s)" % (len(d.get("hosts") or []), len(d.get("datastores") or []),
                                                                  int(d.get("vms_checked") or 0))


def vm_watch_clean_line(data, conditions=None):
    """The one line of a run with nothing to report."""
    d = data or {}
    vcs = [s.get("name") for s in d.get("vcenters") or [] if s.get("ok")]
    return "No outage: %s checked (vCenter%s %s)." % (_counts(d), "s" if len(vcs) > 1 else "", ", ".join(vcs) or d.get("vcenter", ""))


def vm_watch_report(data, conditions, actions=None, opts=None):
    """The report (roles/site_email layout): what is down or hung, what phase 2 would do, what was not judged."""
    d, cs, a, o = data or {}, conditions or [], actions or {}, opts or {}
    multi = len([s for s in d.get("vcenters") or [] if s.get("ok")]) > 1
    crit = [c for c in cs if c["severity"] == "critical"]
    warn = [c for c in cs if c["severity"] == "warning"]
    hung = [c for c in cs if c["kind"] == "hung"]
    title = "VM outage watch: %s" % (", ".join(x for x in ("%d critical" % len(crit) if crit else "", "%d warning" % len(warn) if warn else "") if x)
                                     or "nothing down")
    status = "critical" if crit else "warning" if warn else "ok"
    conf = d.get("confirm") or {}
    pr = d.get("probe") or {}
    subtitle = ("Read %s UTC: %s; HA and storage events of the last %s minutes. A VM is hung only when it has no heartbeat AND "
                "does not answer on the network, in each of %s check(s) %s seconds apart. Watch only: nothing was restarted."
                % (d.get("read_at", ""), _counts(d), _num(d.get("window_minutes")), conf.get("checks", 1), _num(conf.get("seconds", 0))))
    summary = [{"label": "Critical", "value": len(crit), "status": "critical" if crit else "ok"},
               {"label": "Warning", "value": len(warn), "status": "warning" if warn else "ok"},
               {"label": "Hung VMs", "value": len(hung), "status": "critical" if hung else "ok"},
               {"label": "Phase 2 would restart", "value": len(a.get("would_restart") or []), "status": "info"}]
    sections = []
    if cs:
        sections.append({"title": "Down or hung now", "status": status,
                         "columns": ["Severity", "Object", "Where", "Problem", "What to do"],
                         "rows": [[c["severity"], "%s %s" % (c["object_type"], c["object"]), _where(c, multi), c["summary"], c["todo"]] for c in cs],
                         "row_status": [c["severity"] for c in cs], "cell_status": [[c["severity"], "", "", "", ""] for c in cs]})
    if hung:
        wr = a.get("would_restart") or []
        sections.append({"title": "Would be restarted in phase 2 (nothing was done)", "status": "info",
                         "text": "Phase 2 is not built yet. This is what it would restart (a guest restart first), so you can check every "
                                 "one by hand now and count the false alarms before any action is turned on.",
                         "columns": ["VM", "vCenter", "Host", "Why"],
                         "rows": [[r["vm"], r["vcenter"], r["esxi_host"], r["why"]] for r in wr] or [["(none)", "", "", ""]]})
        held = (a.get("protected") or []) + (a.get("over_limit") or [])
        if held:
            sections.append({"title": "Protected: ticket only", "status": "warning",
                             "columns": ["VM", "vCenter", "Host", "Why it would not be restarted"],
                             "rows": [[r["vm"], r["vcenter"], r["esxi_host"], r["why"]] for r in held],
                             "row_status": ["warning"] * len(held)})
    nj = vm_watch_not_judged(d)
    if nj:
        sections.append({"title": "Not judged this run", "columns": ["VM", "vCenter", "Host", "Why"],
                         "rows": [[x["name"], x["vcenter"], x["esxi_host"], x["why"]] for x in nj]})
    lines = ["Probe from the AAP node: %s%s, %s s per address." % ("ping" if pr.get("on", True) else "off",
                                                                     (" and TCP port %s" % pr["port"]) if pr.get("port") else "",
                                                                     _num(pr.get("timeout", 3)))]
    if d.get("not_probed"):
        lines.append("Not probed (over vm_watch_max_suspects): %s." % ", ".join(d["not_probed"]))
    if int(d.get("vms_skipped") or 0):
        lines.append("%d VM(s) skipped by vm_watch_skip_vms / vm_watch_skip_folders." % int(d["vms_skipped"]))
    sections.append({"title": "How it judged", "lines": lines})
    return {"title": title, "status": status, "subtitle": subtitle, "summary": summary, "sections": sections,
            "footer": "Read-only: the watch changed nothing."}


def _num(v):
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else str(f)
    except (TypeError, ValueError):
        return str(v or "")


def vm_watch_health(data, conditions, opts=None):
    """The check results the ServiceNow tickets job reads (roles/servicenow/tasks/tickets.yml), one 'host' per
    vCenter: {vcenter: {status, checks: [vm_watch], findings, reach: false, ...}}. Each condition is a finding
    (check vm_watch), so a ticket per condition, noted (or resolved) once the watch no longer finds it.
    A vCenter not read lists vm_watch as failed: its open tickets are left alone."""
    d, o = data or {}, opts or {}
    out = {}
    for s in d.get("vcenters") or []:
        name = s.get("name", "")
        if s.get("note", "").startswith("the same vCenter"):
            continue
        out[name] = {"status": "healthy", "checks": ["vm_watch"], "checks_failed": ["vm_watch"] if s.get("error") else [],
                     "findings": [], "act": {}, "time": d.get("read_at", ""), "job": str(o.get("job", "")), "reach": False}
    for c in conditions or []:
        vc = c.get("vcenter") or ""
        h = out.setdefault(vc, {"status": "healthy", "checks": ["vm_watch"], "checks_failed": [], "findings": [], "act": {},
                                "time": d.get("read_at", ""), "job": str(o.get("job", "")), "reach": False})
        h["findings"].append({"check": "vm_watch", "id": c["id"], "severity": c["severity"],
                              "summary": "%s%s" % (c["summary"], (" [%s]" % _where(c, False)) if _where(c, False) else ""),
                              "hint": c.get("todo", "")})
        h["status"] = "findings"
    return out


def vm_watch_artifact(data, conditions, actions=None):
    """The job artifact vm_watch: the conditions, the counts, and what phase 2 would restart."""
    d, cs, a = data or {}, conditions or [], actions or {}
    return {"read_at": d.get("read_at", ""), "vcenters": [s.get("name") for s in d.get("vcenters") or [] if s.get("ok")],
            "checked": {"hosts": len(d.get("hosts") or []), "datastores": len(d.get("datastores") or []), "vms": int(d.get("vms_checked") or 0)},
            "critical": len([c for c in cs if c["severity"] == "critical"]), "warning": len([c for c in cs if c["severity"] == "warning"]),
            "conditions": [{k: c.get(k) for k in ("id", "severity", "kind", "object_type", "object", "vcenter", "datacenter", "cluster", "summary")}
                           for c in cs],
            "would_restart": a.get("would_restart") or [], "protected": a.get("protected") or [], "over_limit": a.get("over_limit") or [],
            "not_judged": vm_watch_not_judged(d)}


class FilterModule(object):
    def filters(self):
        return {"vm_watch_conditions": vm_watch_conditions, "vm_watch_actions": vm_watch_actions, "vm_watch_report": vm_watch_report,
                "vm_watch_health": vm_watch_health, "vm_watch_artifact": vm_watch_artifact, "vm_watch_clean_line": vm_watch_clean_line,
                "vm_watch_not_judged": vm_watch_not_judged}
