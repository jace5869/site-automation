#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Read vCenter's triggered alarms, configuration issues, host state and recent events (read-only).
pyVmomi only (the vmware.vmware collection needs it anyway; it has no alarm or event module)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vmware_alarms
short_description: Read vCenter's triggered alarms, configuration issues, host state and recent events (read-only)
description:
  - The triggered alarms (red and yellow, as the vSphere client shows them) on vCenter itself, the
    datacenters, clusters and hosts - or the object types listed in I(types) - each with where it is,
    since when, and whether someone acknowledged it.
  - The configuration issues vCenter shows on those objects (e.g. "SSH is enabled", HA warnings).
  - Every host's connection state, maintenance mode, ESXi version and hardware.
  - The events of the last I(hours) hours: every warning and error event, plus failed logins, host
    connection events and VM power / guest / HA events whatever their category. Each is classified
    (group login / connection / vm / other) with a severity (critical / warning / info).
  - vCenter's address and account come from VMWARE_HOST, VMWARE_USER, VMWARE_PASSWORD (and
    VMWARE_PORT), as a "VMware vCenter" credential in AAP gives them. A read-only vCenter role is
    enough. Changes nothing.
options:
  datacenter: {description: Only this datacenter (vCenter-level alarms are always included)., type: str}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
  types:
    description: The object types whose alarms and configuration issues are read - vcenter, datacenter,
      cluster, host, and optionally datastore, vm, network.
    type: list
    elements: str
    default: [vcenter, datacenter, cluster, host]
  hours: {description: How far back to read events, in hours. 0 = no events., type: float, default: 24}
  max_events: {description: Read at most this many events (the newest)., type: int, default: 5000}
  login_event_types: {description: Event types that are failed logins., type: list, elements: str}
  connection_event_types: {description: Event types about a host losing its connection to vCenter., type: list, elements: str}
  vm_event_types: {description: Event types about a VM stopping, restarting or failing over., type: list, elements: str}
  now: {description: The time to measure from (seconds since 1970; tests only)., type: float}
author: site automation
'''

RETURN = r'''
alarms:
  description: Triggered alarms, most severe first.
  type: list
  returned: success
  sample: [{entity_type: host, entity: esx01.example.mil, moid: host-12, datacenter: DC1, cluster: CL1,
            alarm: "Host connection and power state", alarm_description: "Default alarm ...", status: red,
            severity: critical, time: "2026-10-05T03:52:47Z", acknowledged: false, acknowledged_by: "", acknowledged_time: ""}]
config_issues:
  description: Configuration issues on those objects.
  type: list
  returned: success
hosts:
  description: Every host (of the datacenter) with its state.
  type: list
  returned: success
  sample: [{name: esx01.example.mil, moid: host-12, cluster: CL1, datacenter: DC1, connection: connected,
            power: poweredOn, maintenance: false, version: 8.0.2, build: "21997540", vendor: Dell Inc., model: PowerEdge R750, status: green}]
events:
  description: The events of the window, newest first, each classified.
  type: list
  returned: success
  sample: [{key: 4711, time: "2026-10-05T03:52:47Z", type: BadUsernameSessionEvent, group: login, severity: warning,
            category: warning, message: "Cannot login svc_scan@10.1.2.3", user: "", login_user: svc_scan, ip: 10.1.2.3,
            host: esx01.example.mil, vm: "", cluster: CL1, datacenter: DC1}]
'''

import datetime
import os
import re
import time

from ansible.module_utils.basic import AnsibleModule

try:
    from pyVim.connect import Disconnect, SmartConnect
    from pyVmomi import vim, vmodl
    HAS_PYVMOMI = True
except ImportError:
    HAS_PYVMOMI = False

LOGIN_EVENTS = ["BadUsernameSessionEvent", "com.vmware.sso.LoginFailure", "esx.audit.account.locked",
                "esx.audit.account.loginfailures"]
CONNECTION_EVENTS = ["HostConnectionLostEvent", "HostNotRespondingEvent", "HostDisconnectedEvent",
                     "HostReconnectionFailedEvent", "HostShutdownEvent", "HostCnxFailedEvent",
                     "HostCnxFailedAccountFailedEvent", "HostCnxFailedNetworkErrorEvent",
                     "HostCnxFailedNoConnectionEvent", "HostCnxFailedTimeoutEvent",
                     "HostCnxFailedBadUsernameEvent", "HostCnxFailedNoAccessEvent"]
VM_EVENTS = ["VmGuestShutdownEvent", "VmGuestRebootEvent", "VmPoweredOffEvent", "VmResettingEvent",
             "VmFailoverFailed", "VmDasBeingResetEvent", "VmDasBeingResetWithScreenshotEvent",
             "VmRestartedOnAlternateHostEvent", "com.vmware.vc.ha.VmRestartedByHAEvent",
             "VmFailedToPowerOnEvent", "VmDisconnectedEvent"]
# VM events that mean something went wrong, whoever started them
VM_CRITICAL = {"VmFailoverFailed", "VmFailedToPowerOnEvent"}
VM_WARNING = {"VmDasBeingResetEvent", "VmDasBeingResetWithScreenshotEvent", "VmRestartedOnAlternateHostEvent",
              "com.vmware.vc.ha.VmRestartedByHAEvent", "VmDisconnectedEvent"}
CONNECTION_INFO = {"HostShutdownEvent"}      # someone shut the host down: worth seeing, not an outage by itself
STATUS = {"red": "critical", "yellow": "warning", "gray": "unknown", "green": "ok"}
RANK = {"critical": 0, "warning": 1, "unknown": 2, "info": 3, "ok": 4}


def _iso(t):
    if t is None:
        return ""
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def category_of(type_id, info_category="", severity=""):
    """An event's category (error / warning / info / user): vCenter's own description of the type,
    else an extended event's severity, else a guess from the type's name."""
    for c in (info_category, severity):
        c = str(c or "").lower()
        if c in ("error", "warning", "info", "user"):
            return c
    t = str(type_id or "")
    if t.endswith("ErrorEvent") or "Failed" in t or t.endswith("LostEvent") or "NotResponding" in t:
        return "error"
    if t.endswith("WarningEvent"):
        return "warning"
    return "info"


def classify(type_id, category, user="", login_types=None, connection_types=None, vm_types=None):
    """(group, severity) of one event. group: login / connection / vm / other (None = not wanted:
    an info event of no listed type)."""
    t = str(type_id or "")
    if t in (login_types if login_types is not None else LOGIN_EVENTS):
        return "login", "warning"
    if t in (connection_types if connection_types is not None else CONNECTION_EVENTS):
        return "connection", "info" if t in CONNECTION_INFO else "critical"
    if t in (vm_types if vm_types is not None else VM_EVENTS):
        if t in VM_CRITICAL:
            return "vm", "critical"
        if t in VM_WARNING or (t == "VmPoweredOffEvent" and not user):    # powered off and nobody asked
            return "vm", "warning"
        return "vm", "info"
    if category == "error":
        return "other", "critical"
    if category == "warning":
        return "other", "warning"
    return None, None


def login_parts(message, user="", ip="", args=None):
    """(user name, source address) of a failed login: from the event's fields or arguments, else its
    message ("Cannot login user@10.1.2.3")."""
    args = args or {}
    u = str(args.get("userName") or args.get("user") or user or "")
    a = str(args.get("client") or args.get("ipAddress") or args.get("ip") or ip or "")
    if not (u and a):
        m = re.search(r"(?:login|log in)\s+(\S+)@(\S+?)(?:[\s:,]|$)", str(message or ""), re.I)
        if m:
            u, a = u or m.group(1), a or m.group(2)
    return u, a


def event_record(e, login_types=None, connection_types=None, vm_types=None):
    """One event (a plain dict: key, time, type, category, message, user, ip, args, host, vm, cluster,
    datacenter) -> the record the module returns, or None when it is not wanted."""
    t = e.get("type") or ""
    cat = category_of(t, e.get("category"), e.get("severity"))
    group, sev = classify(t, cat, e.get("user"), login_types, connection_types, vm_types)
    if group is None:
        return None
    msg = " ".join(str(e.get("message") or "").split())
    rec = {"key": e.get("key"), "time": e.get("time") or "", "type": t, "group": group, "severity": sev,
           "category": cat, "message": msg, "user": e.get("user") or "", "login_user": "", "ip": "",
           "host": e.get("host") or "", "vm": e.get("vm") or "", "cluster": e.get("cluster") or "",
           "datacenter": e.get("datacenter") or ""}
    if group == "login":
        rec["login_user"], rec["ip"] = login_parts(msg, e.get("login_user") or "", e.get("ip"), e.get("args"))
        if not msg:
            rec["message"] = "Cannot login %s@%s" % (rec["login_user"] or "?", rec["ip"] or "?")
    if not rec["message"]:
        what = {"HostConnectionLostEvent": "Host %s lost its connection to vCenter",
                "HostNotRespondingEvent": "Host %s is not responding",
                "HostDisconnectedEvent": "Host %s disconnected"}.get(t)
        rec["message"] = (what % rec["host"]) if what else t
    return rec


def alarm_records(states, names, defs, types):
    """Triggered alarm states (plain dicts: key, kind, moid, alarm, status, time, acknowledged,
    acknowledged_by, acknowledged_time) -> one record each, deduplicated on key, only the wanted
    object types, most severe and newest first. names: moid -> {name, cluster, datacenter};
    defs: alarm moid -> {name, description}."""
    out, seen = [], set()
    for s in states or []:
        if s.get("key") in seen or s.get("kind") not in types or s.get("status") == "green":
            continue
        seen.add(s.get("key"))
        n = names.get(s.get("moid")) or {}
        d = defs.get(s.get("alarm")) or {}
        out.append({"entity_type": s["kind"], "entity": n.get("name") or s.get("moid") or "", "moid": s.get("moid") or "",
                    "datacenter": n.get("datacenter") or "", "cluster": n.get("cluster") or "",
                    "alarm": d.get("name") or s.get("alarm") or "", "alarm_description": d.get("description") or "",
                    "status": s.get("status") or "", "severity": STATUS.get(s.get("status"), "unknown"),
                    "time": s.get("time") or "", "acknowledged": bool(s.get("acknowledged")),
                    "acknowledged_by": s.get("acknowledged_by") or "", "acknowledged_time": s.get("acknowledged_time") or ""})
    out.sort(key=lambda a: a["time"], reverse=True)
    out.sort(key=lambda a: RANK.get(a["severity"], 9))
    return out


# ---- vCenter ------------------------------------------------------------------------------------
def _kind(obj, root):
    if obj is None:
        return ""
    if root is not None and obj._moId == root._moId:
        return "vcenter"
    for cls, k in ((vim.Datacenter, "datacenter"), (vim.ClusterComputeResource, "cluster"), (vim.ComputeResource, "host"),
                   (vim.HostSystem, "host"), (vim.StoragePod, "datastore"), (vim.Datastore, "datastore"),
                   (vim.VirtualMachine, "vm"), (vim.Network, "network"), (vim.DistributedVirtualSwitch, "network"),
                   (vim.Folder, "folder")):
        if isinstance(obj, cls):
            return k
    return "other"


def _props(content, root, objtype, paths):
    """{moid: (object, {path: value})} for every object of objtype under root (one round trip)."""
    view = content.viewManager.CreateContainerView(root, [objtype], True)
    try:
        spec = vmodl.query.PropertyCollector.FilterSpec(
            objectSet=[vmodl.query.PropertyCollector.ObjectSpec(
                obj=view, skip=True,
                selectSet=[vmodl.query.PropertyCollector.TraversalSpec(name="v", path="view", skip=False, type=vim.view.ContainerView)])],
            propSet=[vmodl.query.PropertyCollector.PropertySpec(type=objtype, pathSet=paths)])
        return {o.obj._moId: (o.obj, {p.name: p.val for p in o.propSet}) for o in content.propertyCollector.RetrieveContents([spec]) or []}
    finally:
        view.Destroy()


def _datacenter_of(obj, cache):
    """The datacenter name above obj (walking its parents; cached per object)."""
    chain, x = [], obj
    while x is not None and x._moId not in cache and not isinstance(x, vim.Datacenter):
        chain.append(x._moId)
        try:
            x = x.parent
        except Exception:
            x = None
    name = cache.get(x._moId, "") if x is not None and x._moId in cache else (x.name if isinstance(x, vim.Datacenter) else "")
    for m in chain:
        cache[m] = name
    return name


def _issues(kind, name, moid, cluster, dc, issues):
    out = []
    for e in issues or []:
        out.append({"entity_type": kind, "entity": name, "moid": moid, "cluster": cluster, "datacenter": dc,
                    "message": " ".join(str(getattr(e, "fullFormattedMessage", "") or type(e).__name__.split(".")[-1]).split()),
                    "time": _iso(getattr(e, "createdTime", None)), "type": getattr(e, "eventTypeId", "") or type(e).__name__.split(".")[-1]})
    return out


def _read_events(content, root, begin, cap, type_ids):
    """The events since begin - under root (a datacenter), or all of them (root None: that includes
    the ones of no object, like failed vCenter logins) - every warning / error one plus the given
    types, newest first, at most cap, deduplicated."""
    em = content.eventManager
    try:
        info = {d.key: d.category for d in em.description.eventInfo or []}
    except Exception:
        info = {}
    found = {}
    for kw in ({"category": ["error", "warning"]}, {"eventTypeId": list(type_ids)}):
        if root is not None:
            kw["entity"] = vim.event.EventFilterSpec.ByEntity(entity=root, recursion="all")
        spec = vim.event.EventFilterSpec(time=vim.event.EventFilterSpec.ByTime(beginTime=begin), **kw)
        col = em.CreateCollectorForEvents(spec)
        try:
            col.SetCollectorPageSize(1000)
            batch, n = list(col.latestPage or []), 0
            while batch and n < cap:
                for e in batch:
                    if e.key not in found:
                        found[e.key] = e
                        n += 1
                if n >= cap:
                    break
                nxt = col.ReadPreviousEvents(1000) or []
                if not nxt or all(e.key in found for e in nxt):     # the end (some servers repeat the last page)
                    break
                batch = nxt
        finally:
            try:
                col.DestroyCollector()
            except Exception:
                pass
    capped = len(found) >= cap
    out = []
    for e in sorted(found.values(), key=lambda x: x.createdTime, reverse=True)[:cap]:
        t = getattr(e, "eventTypeId", "") or type(e).__name__.split(".")[-1]
        args = {}
        for a in getattr(e, "arguments", None) or []:
            args[str(getattr(a, "key", ""))] = getattr(a, "value", "")
        out.append({"key": e.key, "time": _iso(e.createdTime), "type": t,
                    "category": info.get(t) or info.get(type(e).__name__.split(".")[-1]) or "",
                    "severity": getattr(e, "severity", "") or "", "message": e.fullFormattedMessage or getattr(e, "message", "") or "",
                    "user": e.userName or "", "login_user": e.userName if t == "BadUsernameSessionEvent" else "",
                    "ip": getattr(e, "ipAddress", "") or "", "args": args,
                    "host": e.host.name if e.host else "", "vm": e.vm.name if e.vm else "",
                    "cluster": e.computeResource.name if e.computeResource else "",
                    "cluster_moid": e.computeResource.computeResource._moId if e.computeResource and e.computeResource.computeResource else "",
                    "datacenter": e.datacenter.name if e.datacenter else ""})
    return out, capped


def main():
    module = AnsibleModule(
        argument_spec=dict(datacenter=dict(type="str"), validate_certs=dict(type="bool", default=True),
                           types=dict(type="list", elements="str", default=["vcenter", "datacenter", "cluster", "host"]),
                           hours=dict(type="float", default=24), max_events=dict(type="int", default=5000),
                           login_event_types=dict(type="list", elements="str"),
                           connection_event_types=dict(type="list", elements="str"),
                           vm_event_types=dict(type="list", elements="str"),
                           now=dict(type="float")),
        supports_check_mode=True,
    )
    if not HAS_PYVMOMI:
        module.fail_json(msg="pyVmomi is not installed in the execution environment (the vmware.vmware collection needs it too)")
    host = os.environ.get("VMWARE_HOST")
    if not host:
        module.fail_json(msg='No vCenter to talk to: attach a credential of type "VMware vCenter" to this job template.')
    p = module.params
    types = [str(t).strip().lower() for t in p["types"]]
    bad = [t for t in types if t not in ("vcenter", "datacenter", "cluster", "host", "datastore", "vm", "network")]
    if bad:
        module.fail_json(msg="types: unknown object type(s) %s - use vcenter, datacenter, cluster, host, datastore, vm, network" % ", ".join(bad))
    login_t = p["login_event_types"] if p["login_event_types"] is not None else LOGIN_EVENTS
    conn_t = p["connection_event_types"] if p["connection_event_types"] is not None else CONNECTION_EVENTS
    vm_t = p["vm_event_types"] if p["vm_event_types"] is not None else VM_EVENTS
    now = p["now"] or time.time()
    try:
        si = SmartConnect(host=host, user=os.environ.get("VMWARE_USER", ""), pwd=os.environ.get("VMWARE_PASSWORD", ""),
                          port=int(os.environ.get("VMWARE_PORT") or 443),
                          disableSslCertValidation=not p["validate_certs"])
    except vim.fault.InvalidLogin:
        module.fail_json(msg="vCenter %s refused the login (the VMware vCenter credential)" % host)
    except Exception as e:
        module.fail_json(msg="Could not connect to vCenter %s: %s" % (host, e))
    res = {"vcenter": host, "window_hours": p["hours"], "read_at": _iso(datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc)),
           "alarms": [], "config_issues": [], "hosts": [], "events": [], "events_capped": False}
    try:
        content = si.RetrieveContent()
        root = content.rootFolder
        dcs = _props(content, root, vim.Datacenter, ["name", "configIssue"])
        if p["datacenter"] and p["datacenter"] not in [v[1].get("name") for v in dcs.values()]:
            module.fail_json(msg="vCenter %s has no datacenter named %s" % (host, p["datacenter"]))
        dc_cache = {m: v[1].get("name", "") for m, v in dcs.items()}
        names = {root._moId: {"name": host, "cluster": "", "datacenter": ""}}
        for m, (_, pr) in dcs.items():
            names[m] = {"name": pr.get("name", ""), "cluster": "", "datacenter": pr.get("name", "")}
        clusters = _props(content, root, vim.ClusterComputeResource, ["name", "configIssue"])
        for m, (o, pr) in clusters.items():
            names[m] = {"name": pr.get("name", ""), "cluster": pr.get("name", ""), "datacenter": _datacenter_of(o, dc_cache)}
        hosts = _props(content, root, vim.HostSystem,
                       ["name", "parent", "configIssue", "overallStatus", "runtime.connectionState", "runtime.powerState",
                        "runtime.inMaintenanceMode", "summary.config.product.version", "summary.config.product.build",
                        "hardware.systemInfo.vendor", "hardware.systemInfo.model"])
        for m, (o, pr) in hosts.items():
            parent = pr.get("parent")
            cl = names.get(parent._moId, {}).get("name", "") if isinstance(parent, vim.ClusterComputeResource) else ""
            dc = _datacenter_of(parent, dc_cache) if parent is not None else ""
            names[m] = {"name": pr.get("name", ""), "cluster": cl, "datacenter": dc}
            if parent is not None and not isinstance(parent, vim.ClusterComputeResource):
                names[parent._moId] = names[m]       # a standalone host's compute resource
            if p["datacenter"] and dc != p["datacenter"]:
                continue
            res["hosts"].append({"name": pr.get("name", ""), "moid": m, "cluster": cl, "datacenter": dc,
                                 "connection": str(pr.get("runtime.connectionState") or ""), "power": str(pr.get("runtime.powerState") or ""),
                                 "maintenance": bool(pr.get("runtime.inMaintenanceMode")),
                                 "version": pr.get("summary.config.product.version") or "", "build": pr.get("summary.config.product.build") or "",
                                 "vendor": pr.get("hardware.systemInfo.vendor") or "", "model": pr.get("hardware.systemInfo.model") or "",
                                 "status": str(pr.get("overallStatus") or "")})
        res["hosts"].sort(key=lambda h: (h["datacenter"], h["cluster"], h["name"]))

        def in_dc(n):
            return not p["datacenter"] or n.get("datacenter") == p["datacenter"]

        # configuration issues
        issues = []
        if "vcenter" in types:
            try:
                issues += _issues("vcenter", host, root._moId, "", "", root.configIssue)
            except Exception:
                pass
        for kind, objs in (("datacenter", dcs), ("cluster", clusters), ("host", hosts)):
            if kind in types:
                for m, (_, pr) in objs.items():
                    n = names.get(m, {})
                    if in_dc(n):
                        issues += _issues(kind, n.get("name", ""), m, n.get("cluster", "") if kind == "host" else "",
                                          n.get("datacenter", ""), pr.get("configIssue"))
        res["config_issues"] = sorted(issues, key=lambda i: (i["entity_type"], i["entity"], i["message"]))

        # triggered alarms: rootFolder carries every alarm below it; each state names its own object
        states, alarm_refs, others = [], {}, {}
        for s in root.triggeredAlarmState or []:
            ent = s.entity
            kind = _kind(ent, root)
            if kind not in types:
                continue
            m = ent._moId
            if m not in names:              # a VM, datastore or network (asked for in types): read its name
                if m not in others:
                    try:
                        others[m] = {"name": ent.name, "cluster": "", "datacenter": _datacenter_of(ent, dc_cache)}
                    except Exception:
                        others[m] = {"name": m, "cluster": "", "datacenter": ""}
                names[m] = others[m]
            if kind != "vcenter" and not in_dc(names[m]):
                continue
            alarm_refs[s.alarm._moId] = s.alarm
            states.append({"key": s.key, "kind": kind, "moid": m, "alarm": s.alarm._moId, "status": str(s.overallStatus),
                           "time": _iso(s.time), "acknowledged": bool(s.acknowledged), "acknowledged_by": s.acknowledgedByUser or "",
                           "acknowledged_time": _iso(s.acknowledgedTime)})
        defs = {}
        if alarm_refs:
            spec = vmodl.query.PropertyCollector.FilterSpec(
                objectSet=[vmodl.query.PropertyCollector.ObjectSpec(obj=a) for a in alarm_refs.values()],
                propSet=[vmodl.query.PropertyCollector.PropertySpec(type=vim.alarm.Alarm, pathSet=["info.name", "info.description"])])
            for o in content.propertyCollector.RetrieveContents([spec]) or []:
                pr = {x.name: x.val for x in o.propSet}
                defs[o.obj._moId] = {"name": pr.get("info.name", ""), "description": pr.get("info.description", "")}
        res["alarms"] = alarm_records(states, names, defs, types)

        # events
        if p["hours"] and p["hours"] > 0 and p["max_events"] > 0:
            begin = datetime.datetime.fromtimestamp(now - p["hours"] * 3600, tz=datetime.timezone.utc)
            ev_root = None
            if p["datacenter"]:
                ev_root = [o for m, (o, pr) in dcs.items() if pr.get("name") == p["datacenter"]][0]
            raw, res["events_capped"] = _read_events(content, ev_root, begin, p["max_events"], list(login_t) + list(conn_t) + list(vm_t))
            by_host = {n.get("name"): n for n in names.values()}
            for e in raw:
                # the event names the compute resource: a cluster, or a standalone host's own one (no cluster)
                cm = e.pop("cluster_moid", "")
                if cm and cm not in clusters:
                    e["cluster"] = ""
                if not cm and e["host"]:
                    e["cluster"] = by_host.get(e["host"], {}).get("cluster", "")
                r = event_record(e, login_t, conn_t, vm_t)
                if r:
                    res["events"].append(r)
    except Exception as e:
        module.fail_json(msg="Reading alarms and events from vCenter %s failed: %s" % (host, e))
    finally:
        try:
            Disconnect(si)
        except Exception:
            pass
    module.exit_json(changed=False, **res)


if __name__ == "__main__":
    main()
