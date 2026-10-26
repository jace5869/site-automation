#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Read the health of every datacenter, cluster and host of vCenter (read-only): HA, DRS, vCLS
retreat mode, host connection, HA agent, hardware sensors, usage, configuration issues and triggered
alarms; each host's clock against vCenter's and its NTP setting; when a host not connected lost vCenter.
Raw facts only: plugins/filter/vmware_health_filters.py decides what is healthy.
pyVmomi only (the vmware.vmware collection needs it anyway)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vmware_health
short_description: Read the health of every datacenter, cluster and host (read-only)
description:
  - For every datacenter - its status, configuration issues and triggered alarms.
  - For every cluster - its status, vSphere HA (on or off, host monitoring, admission control and the
    failover capacity left), DRS (on or off, automation level, recommendations waiting, faults),
    whether vSphere Cluster Services (vCLS) is in retreat mode, configuration issues and triggered alarms.
  - For every host (in a cluster or standalone) - connection, power, maintenance mode, the HA agent's
    state, the hardware sensors and hardware status that are not green, reboot required, CPU and
    memory used, uptime, ESXi version, configuration issues and triggered alarms; its clock against
    vCenter's, its NTP servers and whether the NTP service runs (I(clock)); for a host not connected,
    vCenter's newest event of it losing the connection (when the outage began, as the vSphere Client's
    Events list shows it).
  - vCenter's address and account come from VMWARE_HOST, VMWARE_USER, VMWARE_PASSWORD (and
    VMWARE_PORT), as a "VMware vCenter" credential in AAP gives them; vcenters adds more vCenters
    (the same account). A read-only vCenter role is enough. Changes nothing.
options:
  datacenter: {description: Only this datacenter (vCenter-level alarms are always included)., type: str}
  vcenters: {description: More vCenters to read besides the credential's (same account; name or name:port)., type: list, elements: str, default: []}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
  sensors: {description: Read the hosts' hardware sensors and hardware status., type: bool, default: true}
  clock: {description: "Read each connected host's clock (against vCenter's) and NTP setting, and vCenter's clock (against this machine's).", type: bool, default: true}
  now: {description: The time to measure from (seconds since 1970; tests only)., type: float}
author: site automation
'''

RETURN = r'''
datacenters:
  description: Every datacenter.
  type: list
  returned: success
  sample: [{vcenter: vc01.example.mil, name: DC1, moid: datacenter-3, status: green, config_issues: [], alarms: []}]
clusters:
  description: Every cluster.
  type: list
  returned: success
  sample: [{vcenter: vc01.example.mil, datacenter: DC1, name: CL1, moid: domain-c8, status: green,
            ha: {enabled: true, host_monitoring: enabled, admission: hosts, admission_hosts: 1, admission_cpu_pct: null,
                 admission_mem_pct: null, failover_level_now: 1, cpu_left_pct: null, mem_left_pct: null},
            drs: {enabled: true, behavior: fullyAutomated, recommendations: 0, faults: 0, score: 98},
            vcls_retreat: false, evc: intel-icelake, hosts: 4, effective_hosts: 4, config_issues: [], alarms: []}]
hosts:
  description: Every host, in a cluster or standalone (cluster empty).
  type: list
  returned: success
  sample: [{vcenter: vc01.example.mil, datacenter: DC1, cluster: CL1, name: esx01.example.mil, moid: host-12,
            connection: connected, power: poweredOn, maintenance: false, standby: none, status: green,
            ha_state: connectedToMaster, cpu_used_mhz: 9000, cpu_mhz: 64000, mem_used_mb: 300000, mem_mb: 524288,
            uptime_days: 41.2, reboot_required: false, version: 8.0.2, build: "21997540", vendor: Dell Inc.,
            model: PowerEdge R750, sensors_bad: [{name: "Power Supply 2", state: red, type: power}],
            clock_offset_s: -0.4, ntp_servers: [ntp1.example.mil], clock_protocol: ntp, ntp_running: true,
            down_since: "", down_event: "", config_issues: ["SSH for the host has been enabled"], alarms: []}]
vcenter_info:
  description: Every vCenter read - its version and the alarms on vCenter itself.
  type: list
  returned: success
'''

import datetime
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_vcenters import Skip, each_vcenter

try:
    from pyVmomi import vim, vmodl
    HAS_PYVMOMI = True
except ImportError:
    HAS_PYVMOMI = False

VCLS_PREFIX = "config.vcls.clusters."
# a host's events of losing vCenter: the newest says when its outage began
DOWN_EVENTS = ["HostConnectionLostEvent", "HostNotRespondingEvent", "HostDisconnectedEvent", "HostShutdownEvent"]


def _iso(t):
    if t is None:
        return ""
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _s(v):
    """An enum or string value as a plain string ('' for None)."""
    return "" if v is None else str(v)


def ha_facts(cfg, summary):
    """A cluster's vSphere HA: on or off, host monitoring, admission control (what is reserved) and
    what is left now. cfg: configurationEx; summary: the cluster's summary."""
    das = getattr(cfg, "dasConfig", None) if cfg is not None else None
    out = {"enabled": bool(das is not None and das.enabled), "host_monitoring": "", "admission": "",
           "admission_hosts": 0, "admission_cpu_pct": None, "admission_mem_pct": None,
           "failover_level_now": None, "cpu_left_pct": None, "mem_left_pct": None}
    if not out["enabled"]:
        return out
    out["host_monitoring"] = _s(getattr(das, "hostMonitoring", ""))
    p = getattr(das, "admissionControlPolicy", None)
    if not getattr(das, "admissionControlEnabled", False):
        out["admission"] = "off"
    elif p is not None:
        name = type(p).__name__
        if "FailoverLevel" in name:
            out.update(admission="hosts", admission_hosts=int(getattr(p, "failoverLevel", 0) or 0))
        elif "FailoverResources" in name:
            out.update(admission="percent", admission_cpu_pct=getattr(p, "cpuFailoverResourcesPercent", None),
                       admission_mem_pct=getattr(p, "memoryFailoverResourcesPercent", None),
                       admission_hosts=int(getattr(p, "failoverLevel", 0) or 0))
        elif "FailoverHost" in name:
            out.update(admission="dedicated hosts", admission_hosts=len(getattr(p, "failoverHosts", None) or []))
        else:
            out["admission"] = "on"
    else:
        out["admission"] = "on"
    info = getattr(summary, "admissionControlInfo", None) if summary is not None else None
    if info is not None:
        if hasattr(info, "currentFailoverLevel"):
            out["failover_level_now"] = getattr(info, "currentFailoverLevel", None)
        if hasattr(info, "currentCpuFailoverResourcesPercent"):
            out["cpu_left_pct"] = getattr(info, "currentCpuFailoverResourcesPercent", None)
            out["mem_left_pct"] = getattr(info, "currentMemoryFailoverResourcesPercent", None)
    if out["failover_level_now"] is None and summary is not None and out["admission"] == "hosts":
        lvl = getattr(summary, "currentFailoverLevel", None)
        out["failover_level_now"] = int(lvl) if lvl is not None else None
    return out


def drs_facts(cfg, summary, recommendations, faults):
    drs = getattr(cfg, "drsConfig", None) if cfg is not None else None
    score = getattr(summary, "drsScore", None) if summary is not None else None
    return {"enabled": bool(drs is not None and drs.enabled),
            "behavior": _s(getattr(drs, "defaultVmBehavior", "")) if drs is not None and drs.enabled else "",
            "recommendations": len(recommendations or []),
            "faults": sum(len(getattr(f, "faultsByVm", None) or []) or 1 for f in faults or []),
            "score": int(score) if score is not None else None}


def bad_sensors(health_runtime):
    """The hardware sensors and hardware status elements that are red or yellow (unknown and green
    left out): [{name, state, type}]. 'VMware Rollup Health State' is left out when another one
    says why."""
    out = []
    if health_runtime is None:
        return out
    shi = getattr(health_runtime, "systemHealthInfo", None)
    for s in (getattr(shi, "numericSensorInfo", None) or []) if shi is not None else []:
        st = str(getattr(getattr(s, "healthState", None), "key", "") or "").lower()
        if st in ("red", "yellow"):
            out.append({"name": " ".join(str(s.name or "").split()), "state": st, "type": _s(getattr(s, "sensorType", ""))})
    hw = getattr(health_runtime, "hardwareStatusInfo", None)
    if hw is not None:
        for kind, items in (("memory", hw.memoryStatusInfo), ("cpu", hw.cpuStatusInfo), ("storage", hw.storageStatusInfo)):
            for e in items or []:
                st = str(getattr(getattr(e, "status", None), "key", "") or "").lower()
                if st in ("red", "yellow"):
                    out.append({"name": " ".join(str(e.name or "").split()), "state": st, "type": kind})
    named = [x for x in out if "rollup" not in x["name"].lower()]
    out = named or out
    seen, uniq = set(), []
    for x in out:
        k = (x["name"], x["state"])
        if k not in seen:
            seen.add(k)
            uniq.append(x)
    return uniq


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
    chain, x = [], obj
    while x is not None and x._moId not in cache and not isinstance(x, vim.Datacenter):
        chain.append(x._moId)
        try:
            x = x.parent
        except Exception:                       # noqa: BLE001
            x = None
    name = cache.get(x._moId, "") if x is not None and x._moId in cache else (x.name if isinstance(x, vim.Datacenter) else "")
    for m in chain:
        cache[m] = name
    return name


def _issues(issues):
    return [" ".join(str(getattr(e, "fullFormattedMessage", "") or type(e).__name__.split(".")[-1]).split()) for e in issues or []]


def ntp_facts(dt_info, service_info):
    """(NTP servers - None when vCenter did not say, the time protocol ntp / ptp / '', the NTP service
    running - None when not said) of one host, from its config.dateTimeInfo and config.service."""
    servers, proto, running = None, "", None
    if dt_info is not None:
        ntp = getattr(dt_info, "ntpConfig", None)
        servers = [str(x).strip() for x in (getattr(ntp, "server", None) or []) if str(x).strip()]
        proto = _s(getattr(dt_info, "systemClockProtocol", "")).lower()
    for x in (getattr(service_info, "service", None) or []) if service_info is not None else []:
        if getattr(x, "key", "") == "ntpd":
            running = bool(getattr(x, "running", False))
    return servers, proto, running


def clock_offset(query, base=0.0, clock=time.time):
    """Seconds the clock query() reads is ahead of this machine's (behind: negative) minus base, or None
    when it would not say. Read halfway through the call, so the round trip does not count."""
    try:
        t0 = clock()
        t = query()
        t1 = clock()
    except Exception:                           # noqa: BLE001 - vcsim, an old host, no privilege: not read
        return None
    if t is None:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return round(t.timestamp() - (t0 + t1) / 2 - base, 1)


def down_since(content, host_obj):
    """(time, message) of vCenter's newest event of the host losing it (DOWN_EVENTS), or ('', '')."""
    try:
        evs = content.eventManager.QueryEvents(vim.event.EventFilterSpec(
            entity=vim.event.EventFilterSpec.ByEntity(entity=host_obj, recursion="self"), eventTypeId=DOWN_EVENTS)) or []
    except Exception:                           # noqa: BLE001 - no events to read: the report has no time
        return "", ""
    if not evs:
        return "", ""
    e = max(evs, key=lambda x: x.createdTime)
    return _iso(e.createdTime), " ".join(str(getattr(e, "fullFormattedMessage", "") or "").split())


def _retreat(content):
    """{cluster moid: True/False} from vCenter's advanced settings config.vcls.clusters.<moid>.enabled
    (false = retreat mode), or None when vCenter would not say (no permission). No setting at all =
    no cluster in retreat mode."""
    try:
        opts = content.setting.QueryOptions(VCLS_PREFIX) or []
    except vim.fault.InvalidName:
        return {}
    except Exception:                           # noqa: BLE001
        return None
    out = {}
    for o in opts:
        k = str(getattr(o, "key", "") or "")
        if k.startswith(VCLS_PREFIX) and k.endswith(".enabled"):
            out[k[len(VCLS_PREFIX):-len(".enabled")]] = str(getattr(o, "value", "")).strip().lower() == "false"
    return out


def _read(si, content, p, now, host):
    root = content.rootFolder
    res = {"datacenters": [], "clusters": [], "hosts": [], "vcenter_info": []}
    dcs = _props(content, root, vim.Datacenter, ["name", "overallStatus", "configIssue"])
    if p["datacenter"] and p["datacenter"] not in [v[1].get("name") for v in dcs.values()]:
        raise Skip("vCenter %s has no datacenter named %s" % (host, p["datacenter"]))
    dc_cache = {m: v[1].get("name", "") for m, v in dcs.items()}
    clusters = _props(content, root, vim.ClusterComputeResource,
                      ["name", "overallStatus", "configIssue", "configurationEx", "summary", "recommendation", "drsFault"])
    paths = ["name", "parent", "overallStatus", "configIssue", "runtime.connectionState", "runtime.powerState",
             "runtime.inMaintenanceMode", "runtime.standbyMode", "runtime.dasHostState", "runtime.bootTime",
             "summary.quickStats", "summary.hardware", "summary.rebootRequired", "summary.config.product"]
    if p["sensors"]:
        paths.append("runtime.healthSystemRuntime")
    try:
        hosts = _props(content, root, vim.HostSystem, paths)
    except vmodl.query.InvalidProperty:         # a server without one of these: read without the optional ones
        hosts = _props(content, root, vim.HostSystem, [x for x in paths if x not in ("runtime.standbyMode", "runtime.dasHostState")])
    retreat = _retreat(content)
    clocks, vc_off = {}, None
    if p["clock"]:
        vc_off = clock_offset(si.CurrentTime)
        try:
            clocks = _props(content, root, vim.HostSystem, ["configManager.dateTimeSystem", "config.dateTimeInfo", "config.service"])
        except Exception:                       # noqa: BLE001 - not readable: the clocks are not judged
            clocks = {}

    def in_dc(dc):
        return not p["datacenter"] or dc == p["datacenter"]

    # triggered alarms: rootFolder carries every alarm below it; each state names its own object
    states, refs = [], {}
    for s in root.triggeredAlarmState or []:
        if str(s.overallStatus) == "green":
            continue
        refs[s.alarm._moId] = s.alarm
        states.append(s)
    names = {}
    if refs:
        spec = vmodl.query.PropertyCollector.FilterSpec(
            objectSet=[vmodl.query.PropertyCollector.ObjectSpec(obj=a) for a in refs.values()],
            propSet=[vmodl.query.PropertyCollector.PropertySpec(type=vim.alarm.Alarm, pathSet=["info.name"])])
        for o in content.propertyCollector.RetrieveContents([spec]) or []:
            names[o.obj._moId] = {x.name: x.val for x in o.propSet}.get("info.name", "")
    alarms_of = {}
    for s in states:
        alarms_of.setdefault(s.entity._moId, []).append({
            "alarm": names.get(s.alarm._moId) or s.alarm._moId, "status": str(s.overallStatus), "time": _iso(s.time),
            "acknowledged": bool(s.acknowledged)})

    about = content.about
    res["vcenter_info"].append({"vcenter": host, "version": _s(getattr(about, "version", "")), "build": _s(getattr(about, "build", "")),
                                "name": _s(getattr(about, "fullName", "")), "alarms": alarms_of.get(root._moId, []),
                                "vcls_readable": retreat is not None, "clock_offset_s": vc_off})

    for m, (o, pr) in dcs.items():
        if not in_dc(pr.get("name", "")):
            continue
        res["datacenters"].append({"name": pr.get("name", ""), "moid": m, "status": _s(pr.get("overallStatus")),
                                   "config_issues": _issues(pr.get("configIssue")), "alarms": alarms_of.get(m, [])})

    cl_name = {}
    for m, (o, pr) in clusters.items():
        dc = _datacenter_of(o, dc_cache)
        cl_name[m] = pr.get("name", "")
        if not in_dc(dc):
            continue
        cfg, summ = pr.get("configurationEx"), pr.get("summary")
        evc = getattr(summ, "currentEVCModeKey", None) if summ is not None else None
        res["clusters"].append({
            "name": pr.get("name", ""), "moid": m, "datacenter": dc, "status": _s(pr.get("overallStatus")),
            "ha": ha_facts(cfg, summ), "drs": drs_facts(cfg, summ, pr.get("recommendation"), pr.get("drsFault")),
            "vcls_retreat": None if retreat is None else bool(retreat.get(m, False)),
            "evc": _s(evc), "hosts": int(getattr(summ, "numHosts", 0) or 0) if summ is not None else 0,
            "effective_hosts": int(getattr(summ, "numEffectiveHosts", 0) or 0) if summ is not None else 0,
            "config_issues": _issues(pr.get("configIssue")), "alarms": alarms_of.get(m, [])})

    for m, (o, pr) in hosts.items():
        parent = pr.get("parent")
        cl = cl_name.get(parent._moId, "") if isinstance(parent, vim.ClusterComputeResource) else ""
        dc = _datacenter_of(parent, dc_cache) if parent is not None else ""
        if not in_dc(dc):
            continue
        qs, hw, prod = pr.get("summary.quickStats"), pr.get("summary.hardware"), pr.get("summary.config.product")
        das = pr.get("runtime.dasHostState")
        cores = int(getattr(hw, "numCpuCores", 0) or 0) if hw is not None else 0
        mhz = int(getattr(hw, "cpuMhz", 0) or 0) if hw is not None else 0
        mem = int(getattr(hw, "memorySize", 0) or 0) if hw is not None else 0
        boot = pr.get("runtime.bootTime")
        up = getattr(qs, "uptime", None) if qs is not None else None
        if not up and boot is not None:
            b = boot if boot.tzinfo else boot.replace(tzinfo=datetime.timezone.utc)
            up = now - b.timestamp()
        conn = _s(pr.get("runtime.connectionState"))
        own_alarms = list(alarms_of.get(m, []))
        if parent is not None and not isinstance(parent, vim.ClusterComputeResource):
            own_alarms += alarms_of.get(parent._moId, [])     # a standalone host's compute resource
        # a disconnected host keeps its last numbers: they are not shown as current
        live = conn == "connected"
        ck = {"clock_offset_s": None, "ntp_servers": None, "clock_protocol": "", "ntp_running": None}
        if live and m in clocks:
            cp = clocks[m][1]
            servers, proto, running = ntp_facts(cp.get("config.dateTimeInfo"), cp.get("config.service"))
            dts = cp.get("configManager.dateTimeSystem")
            ck.update(clock_offset_s=clock_offset(dts.QueryDateTime, vc_off or 0.0) if dts is not None else None,
                      ntp_servers=servers, clock_protocol=proto, ntp_running=running)
        since, why = down_since(content, o) if conn not in ("connected", "") else ("", "")
        res["hosts"].append({
            "name": pr.get("name", ""), "moid": m, "cluster": cl, "standalone": not isinstance(parent, vim.ClusterComputeResource),
            "datacenter": dc, "connection": conn, "power": _s(pr.get("runtime.powerState")),
            "maintenance": bool(pr.get("runtime.inMaintenanceMode")), "standby": _s(pr.get("runtime.standbyMode")),
            "status": _s(pr.get("overallStatus")), "ha_state": _s(getattr(das, "state", "")) if das is not None else "",
            "cpu_used_mhz": int(getattr(qs, "overallCpuUsage", 0) or 0) if live and qs is not None else None,
            "cpu_mhz": cores * mhz, "mem_used_mb": int(getattr(qs, "overallMemoryUsage", 0) or 0) if live and qs is not None else None,
            "mem_mb": mem // 1048576, "uptime_days": round(float(up) / 86400, 1) if live and up else None,
            "reboot_required": bool(pr.get("summary.rebootRequired")),
            "version": _s(getattr(prod, "version", "")) if prod is not None else "", "build": _s(getattr(prod, "build", "")) if prod is not None else "",
            "vendor": _s(getattr(hw, "vendor", "")) if hw is not None else "", "model": _s(getattr(hw, "model", "")) if hw is not None else "",
            "sensors_bad": bad_sensors(pr.get("runtime.healthSystemRuntime")) if p["sensors"] and live else [],
            "down_since": since, "down_event": why,
            "config_issues": _issues(pr.get("configIssue")), "alarms": own_alarms})
        res["hosts"][-1].update(ck)

    for k in ("datacenters", "clusters", "hosts"):
        res[k].sort(key=lambda x: (x.get("datacenter", x.get("name", "")), x.get("cluster", ""), x.get("name", "")))
        for r in res[k]:
            r["vcenter"] = host
    return res


def main():
    module = AnsibleModule(
        argument_spec=dict(datacenter=dict(type="str"), validate_certs=dict(type="bool", default=True),
                           vcenters=dict(type="list", elements="str", default=[]), sensors=dict(type="bool", default=True),
                           clock=dict(type="bool", default=True), now=dict(type="float")),
        supports_check_mode=True,
    )
    if not HAS_PYVMOMI:
        module.fail_json(msg="pyVmomi is not installed in the execution environment (the vmware.vmware collection needs it too)")
    p = module.params
    now = p["now"] or time.time()
    done, statuses = each_vcenter(module, lambda si, content, name: _read(si, content, p, now, name),
                                  p["vcenters"], p["validate_certs"], "the health of datacenters, clusters and hosts")
    res = {"vcenter": ", ".join(name for name, _ in done), "vcenters": statuses,
           "read_at": _iso(datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc)),
           "datacenters": [], "clusters": [], "hosts": [], "vcenter_info": []}
    for _, part in done:
        for k in ("datacenters", "clusters", "hosts", "vcenter_info"):
            res[k] += part[k]
    module.exit_json(changed=False, **res)


if __name__ == "__main__":
    main()
