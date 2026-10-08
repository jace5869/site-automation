#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Read what vSphere capacity planning needs from vCenter (read-only): clusters with their hosts,
HA admission control and powered-on VMs; datastores and the clusters that use them; vCenter's own
daily statistics (cluster CPU and memory, datastore space) for the trend; VMs added and removed.
pyVmomi only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vmware_capacity
short_description: Read vSphere capacity, usage and its history from vCenter (read-only)
description:
  - Every cluster (or the ones named) - its hosts (cores, CPU, memory, usage now, state), HA admission
    control, the powered-on VMs' vCPUs and memory - and every datastore its hosts use.
  - The history comes from vCenter's own daily statistics (cluster cpu.usagemhz and mem.consumed,
    datastore disk.used), kept a year at statistics level 1. Nothing is stored by this module.
  - VMs added and removed in the last I(events_days) days (vCenter events; vCenter keeps them 30
    days by default).
  - vCenter's address and account come from VMWARE_HOST, VMWARE_USER, VMWARE_PASSWORD (and
    VMWARE_PORT); vcenters adds more vCenters (the same account). A read-only vCenter role is
    enough. Changes nothing.
options:
  datacenter: {description: Only this datacenter., type: str}
  vcenters: {description: More vCenters to read besides the credential's (same account; name or name:port)., type: list, elements: str, default: []}
  clusters: {description: Only these clusters (names; * and ? allowed)., type: list, elements: str, default: []}
  history_days: {description: Days of daily statistics to read. 0 = none., type: int, default: 90}
  events_days: {description: Days of VM add / remove events to count. 0 = none., type: int, default: 30}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
  now: {description: The time to measure from (seconds since 1970; tests only)., type: float}
author: site automation
'''

RETURN = r'''
clusters:
  description: One per cluster (a host outside any cluster is a cluster of its own, standalone true).
  type: list
  returned: success
datastores:
  description: Every datastore of those clusters' hosts, with its daily used-space history.
  type: list
  returned: success
'''

import datetime
import fnmatch
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_vcenters import Skip, each_vcenter

try:
    from pyVmomi import vim, vmodl
    HAS_PYVMOMI = True
except ImportError:
    HAS_PYVMOMI = False

UNIT = {"kiloBytes": 1024, "megaBytes": 1024 ** 2, "gigaBytes": 1024 ** 3, "teraBytes": 1024 ** 4, "bytes": 1,
        "megaHertz": 1, "kiloBytesPerSecond": 1024}
ADDED = ["VmCreatedEvent", "VmClonedEvent", "VmDeployedEvent", "VmRegisteredEvent"]
REMOVED = ["VmRemovedEvent"]


def series(sample_times, values, factor=1):
    """vCenter's values and their timestamps -> [[seconds since 1970, value * factor]], without the
    -1 vCenter writes for a missing sample, oldest first."""
    out = []
    for t, v in zip(sample_times, values or []):
        if v is None or v < 0 or t is None:
            continue
        out.append([int(t), float(v) * factor])
    return sorted(out)


def ha_policy(cfg):
    """A cluster's HA admission control as {enabled, kind, hosts, cpu_pct, mem_pct}."""
    das = getattr(cfg, "dasConfig", None) if cfg is not None else None
    if das is None or not das.enabled:
        return {"enabled": False, "kind": "", "hosts": 0, "cpu_pct": None, "mem_pct": None}
    p = das.admissionControlPolicy
    out = {"enabled": True, "kind": "none", "hosts": 0, "cpu_pct": None, "mem_pct": None}
    if not das.admissionControlEnabled or p is None:
        return out
    name = type(p).__name__
    if "FailoverLevel" in name:
        out.update(kind="hosts", hosts=int(p.failoverLevel or 0))
    elif "FailoverResources" in name:
        out.update(kind="percent", cpu_pct=p.cpuFailoverResourcesPercent, mem_pct=p.memoryFailoverResourcesPercent,
                   hosts=int(getattr(p, "failoverLevel", 0) or 0))
    elif "FailoverHost" in name:
        out.update(kind="dedicated hosts", hosts=len(p.failoverHosts or []))
    return out


def _props(content, objtype, paths, root=None):
    view = content.viewManager.CreateContainerView(root or content.rootFolder, [objtype], True)
    try:
        spec = vmodl.query.PropertyCollector.FilterSpec(
            objectSet=[vmodl.query.PropertyCollector.ObjectSpec(
                obj=view, skip=True,
                selectSet=[vmodl.query.PropertyCollector.TraversalSpec(name="v", path="view", skip=False, type=vim.view.ContainerView)])],
            propSet=[vmodl.query.PropertyCollector.PropertySpec(type=objtype, pathSet=paths)])
        out = {}
        for o in content.propertyCollector.RetrieveContents([spec]) or []:
            out[o.obj._moId] = (o.obj, {p.name: p.val for p in o.propSet})
        return out
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


def _perf(content, specs_by_key, history_days, now):
    """{key: series} from vCenter's daily statistics. specs_by_key: {key: (entity, counter name)}.
    -> (series by key, a note when the statistics could not be read)."""
    if history_days <= 0 or not specs_by_key:
        return {}, ""
    pm = content.perfManager
    try:
        intervals = {i.samplingPeriod: i for i in pm.historicalInterval or []}
        daily = intervals.get(86400)
        if daily is None or not daily.enabled:
            return {}, "vCenter's daily statistics interval is turned off: no history"
        counters = {}
        for c in pm.perfCounter or []:
            counters["%s.%s.%s" % (c.groupInfo.key, c.nameInfo.key, c.rollupType)] = c
    except Exception as e:                      # noqa: BLE001
        return {}, "vCenter's statistics could not be read: %s" % e
    start = datetime.datetime.fromtimestamp(now - history_days * 86400, tz=datetime.timezone.utc)
    out, note, keys = {}, "", list(specs_by_key)
    for i in range(0, len(keys), 50):
        batch, which = [], []
        for k in keys[i:i + 50]:
            ent, cname = specs_by_key[k]
            c = counters.get(cname)
            if c is None:
                note = "vCenter has no %s counter: no history for it" % cname
                continue
            batch.append(vim.PerformanceManager.QuerySpec(entity=ent, metricId=[vim.PerformanceManager.MetricId(counterId=c.key, instance="")],
                                                          intervalId=86400, startTime=start))
            which.append((k, UNIT.get(c.unitInfo.key, 1)))
        if not batch:
            continue
        try:
            res = pm.QueryPerf(querySpec=batch) or []
        except Exception as e:                  # noqa: BLE001
            note = "vCenter's daily statistics could not be read: %s" % e
            continue
        by_ent = {}
        for r in res:
            times = [s.timestamp.timestamp() if s.timestamp else None for s in (r.sampleInfo or [])]
            by_ent.setdefault(r.entity._moId, []).append((times, r.value or []))
        # QueryPerf returns one result per spec, in order, for the entities that have data
        for k, factor in which:
            ent = specs_by_key[k][0]
            got = by_ent.get(ent._moId)
            if not got:
                continue
            times, vals = got.pop(0)
            out[k] = series(times, vals[0].value if vals else [], factor)
    return out, note


def _read(content, p, now, host):
    res = {"clusters": [], "datastores": [], "notes": []}
    dcache = {}
    for m, (o, pr) in _props(content, vim.Datacenter, ["name"]).items():
        dcache[m] = pr.get("name", "")
    crs = _props(content, vim.ComputeResource, ["name", "parent"])
    clus = _props(content, vim.ClusterComputeResource, ["configurationEx"])
    hosts = _props(content, vim.HostSystem, ["name", "parent", "runtime.connectionState", "runtime.inMaintenanceMode",
                                              "summary.hardware.numCpuCores", "summary.hardware.cpuMhz", "summary.hardware.memorySize",
                                              "summary.quickStats.overallCpuUsage", "summary.quickStats.overallMemoryUsage"])
    vms = _props(content, vim.VirtualMachine, ["runtime.powerState", "runtime.host", "config.hardware.numCPU",
                                                "config.hardware.memoryMB", "config.template"])
    dss = _props(content, vim.Datastore, ["name", "parent", "summary.capacity", "summary.freeSpace", "summary.uncommitted",
                                           "summary.type", "summary.accessible", "summary.multipleHostAccess", "host"])
    pods = _props(content, vim.StoragePod, ["name", "childEntity"])
    pod_of = {}
    for m, (o, pr) in pods.items():
        for child in pr.get("childEntity") or []:
            pod_of[child._moId] = pr.get("name", "")

    def wanted(name, dc):
        if p["datacenter"] and dc != p["datacenter"]:
            return False
        return not p["clusters"] or any(fnmatch.fnmatchcase(str(name).lower(), str(c).strip().lower()) for c in p["clusters"] if str(c).strip())

    cl_of_host, clusters = {}, {}
    for m, (o, pr) in crs.items():
        dc = _datacenter_of(o, dcache)
        if not wanted(pr.get("name", ""), dc):
            continue
        cfg = clus.get(m, (None, {}))[1].get("configurationEx")
        clusters[m] = {"name": pr.get("name", ""), "moid": m, "datacenter": dc, "standalone": m not in clus,
                       "ha": ha_policy(cfg), "drs": bool(getattr(getattr(cfg, "drsConfig", None), "enabled", False)) if cfg else False,
                       "hosts": [], "vms_on": 0, "vms_off": 0, "vcpus_on": 0, "mem_configured_on": 0,
                       "added": 0, "removed": 0, "history": {}, "obj": o}
    for m, (o, pr) in hosts.items():
        parent = pr.get("parent")
        c = clusters.get(parent._moId) if parent is not None else None
        if c is None:
            continue
        cl_of_host[m] = parent._moId
        c["hosts"].append({"name": pr.get("name", ""), "moid": m, "connection": str(pr.get("runtime.connectionState") or ""),
                           "maintenance": bool(pr.get("runtime.inMaintenanceMode")),
                           "cores": int(pr.get("summary.hardware.numCpuCores") or 0),
                           "cpu_mhz": int(pr.get("summary.hardware.numCpuCores") or 0) * int(pr.get("summary.hardware.cpuMhz") or 0),
                           "mem_bytes": int(pr.get("summary.hardware.memorySize") or 0),
                           "cpu_used_mhz": int(pr.get("summary.quickStats.overallCpuUsage") or 0),
                           "mem_used_bytes": int(pr.get("summary.quickStats.overallMemoryUsage") or 0) * 1024 * 1024, "vms_on": 0})
    hosts_by = {h["moid"]: h for c in clusters.values() for h in c["hosts"]}
    for m, (o, pr) in vms.items():
        if pr.get("config.template"):
            continue
        hm = pr.get("runtime.host")
        if hm is None or hm._moId not in cl_of_host:
            continue
        c = clusters[cl_of_host[hm._moId]]
        if str(pr.get("runtime.powerState")) == "poweredOn":
            c["vms_on"] += 1
            c["vcpus_on"] += int(pr.get("config.hardware.numCPU") or 0)
            c["mem_configured_on"] += int(pr.get("config.hardware.memoryMB") or 0) * 1024 * 1024
            hosts_by[hm._moId]["vms_on"] += 1
        else:
            c["vms_off"] += 1
    datastores = []
    for m, (o, pr) in dss.items():
        mounts = [x.key._moId for x in (pr.get("host") or []) if getattr(x, "key", None) is not None]
        cl_names = sorted({clusters[cl_of_host[h]]["name"] for h in mounts if h in cl_of_host})
        if not cl_names:
            continue
        cap, free = int(pr.get("summary.capacity") or 0), int(pr.get("summary.freeSpace") or 0)
        datastores.append({"name": pr.get("name", ""), "moid": m, "datacenter": _datacenter_of(o, dcache), "clusters": cl_names,
                           "hosts": len(mounts), "pod": pod_of.get(m, ""), "type": str(pr.get("summary.type") or ""),
                           "accessible": bool(pr.get("summary.accessible")), "shared": bool(pr.get("summary.multipleHostAccess")),
                           "capacity": cap, "free": free, "uncommitted": int(pr.get("summary.uncommitted") or 0), "history": [],
                           "obj": o})

    # vCenter's own history: daily statistics, a year at level 1
    specs = {}
    for m, c in clusters.items():
        if not c["standalone"]:
            specs[("c", m, "cpu")] = (c["obj"], "cpu.usagemhz.average")
            specs[("c", m, "mem")] = (c["obj"], "mem.consumed.average")
    for d in datastores:
        specs[("d", d["moid"], "used")] = (d["obj"], "disk.used.latest")
    hist, note = _perf(content, specs, p["history_days"], now)
    if note:
        res["notes"].append(note)
    for (kind, m, what), s in hist.items():
        if kind == "c":
            clusters[m]["history"][what] = s
    for d in datastores:
        d["history"] = hist.get(("d", d["moid"], "used"), [])

    # VMs added and removed (vCenter events)
    if p["events_days"] > 0 and clusters:
        try:
            em = content.eventManager
            spec = vim.event.EventFilterSpec(eventTypeId=ADDED + REMOVED,
                                             time=vim.event.EventFilterSpec.ByTime(beginTime=datetime.datetime.fromtimestamp(
                                                 now - p["events_days"] * 86400, tz=datetime.timezone.utc)))
            col = em.CreateCollectorForEvents(spec)
            try:
                col.SetCollectorPageSize(1000)
                seen, batch, n = set(), list(col.latestPage or []), 0
                while batch and n < 20000:
                    new = [e for e in batch if e.key not in seen]
                    if not new:
                        break
                    for e in new:
                        seen.add(e.key)
                        n += 1
                        cr = getattr(e, "computeResource", None)
                        cm = cr.computeResource._moId if cr is not None and cr.computeResource is not None else None
                        if cm in clusters:
                            kind = type(e).__name__.split(".")[-1]
                            clusters[cm]["removed" if kind in REMOVED else "added"] += 1
                    batch = col.ReadPreviousEvents(1000) or []
            finally:
                col.DestroyCollector()
        except Exception as e:              # noqa: BLE001
            res["notes"].append("VM add / remove events could not be read: %s" % e)
    for c in clusters.values():
        c.pop("obj", None)
        c["hosts"].sort(key=lambda h: h["name"])
        res["clusters"].append(c)
    for d in datastores:
        d.pop("obj", None)
        res["datastores"].append(d)
    res["clusters"].sort(key=lambda c: (c["datacenter"], c["standalone"], c["name"]))
    res["datastores"].sort(key=lambda d: (d["datacenter"], d["name"]))
    if p["clusters"] and not res["clusters"]:
        raise Skip("No cluster matches %s in vCenter %s" % (", ".join(p["clusters"]), host))
    for x in res["clusters"] + res["datastores"]:
        x["vcenter"] = host
    return res


def main():
    module = AnsibleModule(
        argument_spec=dict(datacenter=dict(type="str"), clusters=dict(type="list", elements="str", default=[]),
                           history_days=dict(type="int", default=90), events_days=dict(type="int", default=30),
                           validate_certs=dict(type="bool", default=True), now=dict(type="float"),
                           vcenters=dict(type="list", elements="str", default=[])),
        supports_check_mode=True,
    )
    if not HAS_PYVMOMI:
        module.fail_json(msg="pyVmomi is not installed in the execution environment (the vmware.vmware collection needs it too)")
    p = module.params
    now = p["now"] or time.time()
    done, statuses = each_vcenter(module, lambda si, content, name: _read(content, p, now, name), p["vcenters"],
                                  p["validate_certs"], "capacity")
    res = {"vcenter": ", ".join(name for name, _ in done), "vcenters": statuses,
           "read_at": datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "history_days": p["history_days"], "events_days": p["events_days"], "clusters": [], "datastores": [], "notes": []}
    for name, part in done:
        res["clusters"] += part["clusters"]
        res["datastores"] += part["datastores"]
        res["notes"] += [("%s: %s" % (name, n)) if len(done) > 1 else n for n in part["notes"]]
    module.exit_json(changed=False, **res)


if __name__ == "__main__":
    main()
