#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Read the network drop and error counters of every ESXi host (by physical NIC) and every powered-on
VM (by virtual NIC) from vCenter's performance statistics (read-only). Raw counts only:
plugins/filter/vmware_net_drops_filters.py decides what is too many.

Where the counts come from: vCenter keeps net.droppedRx/Tx and net.errorsRx/Tx for the past day (the
5-minute interval) only when that interval's statistics level is 2 or more - the default is 1. Then
the past day is read (by NIC from level 3). At level 1 the last hour of the hosts' real-time
statistics is read instead (ESXi keeps every counter for an hour), and the note says so."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vmware_net_drops
short_description: Network drops and errors of every ESXi host and powered-on VM, from vCenter's statistics (read-only)
description:
  - For every connected ESXi host - packets dropped and errors, received and sent, in total and by physical NIC.
  - For every powered-on VM (not a template) on a connected host - the same, by virtual NIC (its label and network).
  - The past day of vCenter's 5-minute statistics when their level is 2 or more (by NIC from level 3);
    otherwise the last hour of the hosts' real-time statistics. Each vCenter says which, and why.
  - vCenter's address and account come from VMWARE_HOST, VMWARE_USER, VMWARE_PASSWORD (and VMWARE_PORT);
    vcenters adds more vCenters (the same account). A read-only vCenter role is enough. Changes nothing.
options:
  datacenter: {description: Only this datacenter., type: str}
  vcenters: {description: More vCenters to read besides the credential's (same account; name or name:port)., type: list, elements: str, default: []}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
  hours: {description: Hours to read when the past day's statistics are kept (at most what vCenter keeps)., type: int, default: 24}
  vms: {description: Read the powered-on VMs too (false = the hosts only)., type: bool, default: true}
  batch: {description: Hosts or VMs per statistics query (vCenter limits a query of its stored statistics to 64 metrics by default)., type: int, default: 8}
  now: {description: The time to measure from (seconds since 1970; tests only)., type: float}
author: site automation
'''

RETURN = r'''
vcenters:
  description: Every vCenter read (as each_vcenter returns it - name, ok, error, note).
  type: list
  returned: always
read:
  description: What was read from each vCenter that could be read.
  type: list
  returned: success
  sample: [{vcenter: vc01.example.mil, source: past_day, interval: 300, level: 2, hours: 24.0, by_nic: false,
            note: "", hosts_read: 8, vms_read: 214, failed: 0,
            rows: [{kind: host, name: esx01.example.mil, datacenter: DC1, cluster: CL1, host: "", hours: 24.0,
                    rx_dropped: 5120, tx_dropped: 0, rx_errors: 0, tx_errors: 0, nics: []}]}]
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

# counter name (group.name.rollup) -> the key in the result
COUNTERS = {"net.droppedRx.summation": "rx_dropped", "net.droppedTx.summation": "tx_dropped",
            "net.errorsRx.summation": "rx_errors", "net.errorsTx.summation": "tx_errors"}
STATS = ("rx_dropped", "tx_dropped", "rx_errors", "tx_errors")
REALTIME = 20                                   # seconds: ESXi's real-time sampling, kept an hour
MAX_ROWS = 2000                                 # hosts and VMs with drops or errors, per vCenter


def _props(content, root, objtype, paths):
    """[(object, {path: value})] for every object of objtype under root (one round trip)."""
    view = content.viewManager.CreateContainerView(root, [objtype], True)
    try:
        spec = vmodl.query.PropertyCollector.FilterSpec(
            objectSet=[vmodl.query.PropertyCollector.ObjectSpec(
                obj=view, skip=True,
                selectSet=[vmodl.query.PropertyCollector.TraversalSpec(name="v", path="view", skip=False, type=vim.view.ContainerView)])],
            propSet=[vmodl.query.PropertyCollector.PropertySpec(type=objtype, pathSet=paths)])
        return [(o.obj, {p.name: p.val for p in o.propSet}) for o in content.propertyCollector.RetrieveContents([spec]) or []]
    finally:
        view.Destroy()


def source(intervals, hours):
    """vCenter's historical intervals [(seconds, level, enabled, length seconds)] -> (source, interval, level,
    hours, by_nic, note). The drop and error counters are statistics level 2 (by NIC: 3)."""
    five = [i for i in intervals if int(i[0]) == 300]
    lvl = int(five[0][1]) if five else 0
    if five and five[0][2] and lvl >= 2:
        h = min(float(hours), float(five[0][3] or 86400) / 3600)
        return ("past_day", 300, lvl, h, lvl >= 3,
                "" if lvl >= 3 else "statistics level %d: the totals of each host and VM (by NIC needs level 3)" % lvl)
    why = ("its 5-minute statistics are turned off" if five and not five[0][2] else
           "it has no 5-minute statistics interval" if not five else
           "its 5-minute statistics are at level %d (these counters need 2)" % lvl)
    return ("last_hour", REALTIME, lvl, 1.0, True,
            "the last hour only: vCenter keeps no drop counts for the past day - %s. To report the whole day: vSphere Client > "
            "the vCenter > Configure > Settings > General > Edit > Statistics: the 5-minute interval to level 2" % why)


def totals(values, interval):
    """The values of one series (-1 = no sample) -> (sum, seconds covered)."""
    good = [int(v) for v in values or [] if v is not None and v >= 0]
    return sum(good), len(good) * interval


def add_results(results, ids, interval):
    """QueryPerf's results -> {entity moid: {"": {stat: n}, nic: {stat: n}, "_seconds": s}}. The instance ""
    is the entity's total; without it the NICs are added up."""
    out = {}
    for r in results or []:
        e = out.setdefault(r.entity._moId, {"_seconds": 0})
        for s in r.value or []:
            stat = ids.get(s.id.counterId)
            if stat is None:
                continue
            n, secs = totals(s.value, interval)
            inst = str(s.id.instance or "")
            if inst == "*":
                continue
            e.setdefault(inst, {})[stat] = e.get(inst, {}).get(stat, 0) + n
            e["_seconds"] = max(e["_seconds"], secs)
    for e in out.values():
        if "" not in e:
            e[""] = {k: sum(v.get(k, 0) for i, v in e.items() if i != "_seconds") for k in STATS}
    return out


def _query(pm, ents, metric_ids, interval, start, batch):
    """QueryPerf in batches; a batch vCenter refuses is asked again one entity at a time.
    -> (results, entities that could not be read)."""
    res, failed = [], 0
    for i in range(0, len(ents), max(1, batch)):
        part = ents[i:i + max(1, batch)]
        specs = [vim.PerformanceManager.QuerySpec(entity=e, metricId=metric_ids, intervalId=interval, startTime=start) for e in part]
        try:
            res += pm.QueryPerf(querySpec=specs) or []
            continue
        except Exception:                       # noqa: BLE001
            pass
        for s in specs:
            try:
                res += pm.QueryPerf(querySpec=[s]) or []
            except Exception:                   # noqa: BLE001
                failed += 1
    return res, failed


def _vm_nics(vm):
    """{device key as a string: 'Network adapter 1 (VM Network)'}; {} when the VM's hardware cannot be read."""
    try:
        devs = vm.config.hardware.device or []
    except Exception:                           # noqa: BLE001
        return {}
    out = {}
    for d in devs:
        if isinstance(d, vim.vm.device.VirtualEthernetCard):
            info = getattr(d, "deviceInfo", None)
            label = getattr(info, "label", "") or "NIC %s" % d.key
            net = getattr(getattr(d, "backing", None), "deviceName", "") or ""
            out[str(d.key)] = "%s (%s)" % (label, net) if net else label
    return out


def _read(content, p, now, host):
    pm = content.perfManager
    intervals = [(i.samplingPeriod, i.level, i.enabled, i.length) for i in pm.historicalInterval or []]
    src, interval, level, hours, by_nic, note = source(intervals, p["hours"])
    keys = {}
    for c in pm.perfCounter or []:
        name = "%s.%s.%s" % (c.groupInfo.key, c.nameInfo.key, c.rollupType)
        if name in COUNTERS:
            keys[c.key] = COUNTERS[name]
    missing = [n for n, k in COUNTERS.items() if k not in keys.values()]
    out = {"vcenter": host, "source": src, "interval": interval, "level": level, "hours": round(hours, 2), "by_nic": by_nic,
           "note": note, "hosts_read": 0, "vms_read": 0, "failed": 0, "rows": []}
    if missing:
        out["note"] = "vCenter has no counter %s: nothing read" % ", ".join(missing)
        return out
    dcs = _props(content, content.rootFolder, vim.Datacenter, ["name", "hostFolder", "vmFolder"])
    if p["datacenter"] and p["datacenter"] not in [pr.get("name") for _, pr in dcs]:
        raise Skip("vCenter %s has no datacenter named %s" % (host, p["datacenter"]))
    ents, about = [], {}
    for _, dpr in dcs:
        dc = dpr.get("name", "")
        if p["datacenter"] and dc != p["datacenter"]:
            continue
        up = {}
        for o, pr in _props(content, dpr["hostFolder"], vim.HostSystem, ["name", "parent", "runtime.connectionState"]):
            parent = pr.get("parent")
            cl = parent.name if isinstance(parent, vim.ClusterComputeResource) else ""
            if str(pr.get("runtime.connectionState")) != "connected":
                continue
            up[o._moId] = (pr.get("name", ""), cl)
            ents.append(o)
            about[o._moId] = {"kind": "host", "name": pr.get("name", ""), "datacenter": dc, "cluster": cl, "host": "", "obj": o}
        if not p["vms"]:
            continue
        for o, pr in _props(content, dpr["vmFolder"], vim.VirtualMachine, ["name", "runtime.powerState", "runtime.host", "config.template"]):
            h = pr.get("runtime.host")
            if str(pr.get("runtime.powerState")) != "poweredOn" or pr.get("config.template") or h is None or h._moId not in up:
                continue
            ents.append(o)
            about[o._moId] = {"kind": "vm", "name": pr.get("name", ""), "datacenter": dc, "cluster": up[h._moId][1],
                              "host": up[h._moId][0], "obj": o}
    out["hosts_read"] = len([a for a in about.values() if a["kind"] == "host"])
    out["vms_read"] = len(about) - out["hosts_read"]
    start = datetime.datetime.fromtimestamp(now - hours * 3600, tz=datetime.timezone.utc)
    mids = [vim.PerformanceManager.MetricId(counterId=k, instance="*") for k in keys]
    # vCenter's own limit on stored statistics queries does not apply to real-time ones
    res, out["failed"] = _query(pm, ents, mids, interval, start, p["batch"] if src == "past_day" else 50)
    for moid, e in add_results(res, keys, interval).items():
        tot = e[""]
        if not any(tot.get(k, 0) for k in STATS) or moid not in about or len(out["rows"]) >= MAX_ROWS:
            continue
        a = about[moid]
        names = _vm_nics(a["obj"]) if a["kind"] == "vm" else {}
        nics = [dict({"name": names.get(i, i)}, **{k: v.get(k, 0) for k in STATS})
                for i, v in sorted(e.items()) if i not in ("", "_seconds") and any(v.get(k, 0) for k in STATS)]
        row = {k: a[k] for k in ("kind", "name", "datacenter", "cluster", "host")}
        row.update({k: int(tot.get(k, 0)) for k in STATS})
        row.update({"hours": round(e["_seconds"] / 3600.0, 2) or round(hours, 2), "nics": nics})
        out["rows"].append(row)
    out["rows"].sort(key=lambda r: (r["datacenter"], r["kind"] != "host", r["name"]))
    return out


def main():
    module = AnsibleModule(
        argument_spec=dict(datacenter=dict(type="str"), validate_certs=dict(type="bool", default=True),
                           vcenters=dict(type="list", elements="str", default=[]), hours=dict(type="int", default=24),
                           vms=dict(type="bool", default=True), batch=dict(type="int", default=8), now=dict(type="float")),
        supports_check_mode=True,
    )
    if not HAS_PYVMOMI:
        module.fail_json(msg="pyVmomi is not installed in the execution environment (the vmware.vmware collection needs it too)")
    p = module.params
    now = p["now"] or time.time()
    done, statuses = each_vcenter(module, lambda si, content, name: _read(content, p, now, name),
                                  p["vcenters"], p["validate_certs"], "the network drop and error counters")
    module.exit_json(changed=False, vcenters=statuses, read=[part for _, part in done])


if __name__ == "__main__":
    main()
