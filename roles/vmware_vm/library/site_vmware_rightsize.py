#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Read what the VM right-sizing report needs from vCenter (read-only): every powered-on VM with its vCPUs, memory,
limits and guest disks (VMware Tools), and vCenter's own statistics of it - CPU used and ready, active memory,
ballooned and swapped memory - over the last days (the past-month interval: 2-hour averages) and its busiest day of the
last year (the past-year interval: daily averages). The numbers are summed up here (average, 95th percentile, peak);
the suggestions are plugins/filter/vmware_rightsize_filters.py's. pyVmomi only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vmware_rightsize
short_description: Read every VM's size and its usage history from vCenter's statistics (read-only)
description:
  - Every powered-on VM (not templates) of the credential's vCenter and of vcenters - name, where it is, guest OS,
    vCPUs, memory, CPU and memory limits, its guest disks as VMware Tools reports them - and its usage from vCenter's
    own statistics, kept at statistics level 1 (vCenter's default) - cpu.usage, cpu.ready, mem.usage (active
    memory), mem.consumed, mem.vmmemctl (ballooned) - and mem.swapped where level 2 keeps it.
  - The last I(days) days from the past-month interval (2-hour averages, kept 30 days), else the past-week one (30
    minutes, 7 days); the busiest day of the last I(year_days) days from the past-year interval (daily averages).
  - Returns each VM's numbers summed up (average, 95th percentile, peak), not the samples.
  - vCenter's address and account come from VMWARE_HOST, VMWARE_USER, VMWARE_PASSWORD (and VMWARE_PORT); vcenters
    adds more vCenters (the same account). A read-only vCenter role is enough. Changes nothing.
options:
  datacenter: {description: Only this datacenter's VMs., type: str}
  vcenters: {description: More vCenters to read besides the credential's (same account; name or name:port)., type: list, elements: str, default: []}
  clusters: {description: Only the VMs of these clusters (names; * and ? allowed)., type: list, elements: str, default: []}
  vms: {description: Only these VMs (names; * and ? allowed)., type: list, elements: str, default: []}
  exclude: {description: Not these VMs (names; * and ? allowed)., type: list, elements: str, default: []}
  days: {description: Days of history (at most 30 - what vCenter keeps of 2-hour averages)., type: int, default: 30}
  year_days: {description: Days to look back for the busiest day (daily averages; 0 = none)., type: int, default: 365}
  batch: {description: VMs per statistics query (vCenter refuses large queries - vpxd.stats.maxQueryMetrics)., type: int, default: 10}
  workers: {description: Queries at a time., type: int, default: 4}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
  now: {description: The time to measure from (seconds since 1970; tests only)., type: float}
author: site automation
'''

RETURN = r'''
vms:
  description: One per powered-on VM, with its numbers (stats) - percentages of what the VM has.
  type: list
  returned: success
  sample: [{name: web01, vcenter: vc01.example.mil, datacenter: DC1, cluster: PROD, esxi_host: esx01.example.mil,
            folder: /DC1/vm/Linux, guest_os: Red Hat Enterprise Linux 9 (64-bit), windows: false, cpus: 4, memory_mb: 16384,
            cpu_limit_mhz: -1, mem_limit_mb: -1, disks: [{path: /, capacity_gb: 50.0, free_gb: 21.3}],
            stats: {interval: 7200, days: 30.0, samples: 360, cpu_avg: 8.1, cpu_p95: 21.4, cpu_max: 55.0, ready_avg: 0.4,
                    ready_p95: 1.1, mem_avg: 9.0, mem_p95: 14.2, mem_max: 31.0, consumed_mb_avg: 15800, balloon_mb_max: 0,
                    swapped_mb_max: null, year_cpu_day: 30.2, year_cpu_date: "2026-03-31", year_mem_day: 18.0,
                    year_mem_date: "2026-03-31"}}]
not_checked: {description: "VMs not checked: {off, templates}.", type: dict, returned: success}
vcenters: {description: Each vCenter and whether it was read (ok), or why not (error)., type: list, returned: success}
notes: {description: "What vCenter's statistics could not give: a gap in the report (intervals off, a level-1 counter missing, VMs not read).", type: list, returned: success}
info: {description: "What is expected and only said (a level-2 counter not collected, guest counters vCenter offers).", type: list, returned: success}
'''

import concurrent.futures
import datetime
import fnmatch
import math
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_vcenters import each_vcenter

try:
    from pyVmomi import vim, vmodl
    HAS_PYVMOMI = True
except ImportError:
    HAS_PYVMOMI = False

PROPS = ["name", "config.template", "config.guestFullName", "config.guestId", "runtime.powerState", "runtime.host",
         "runtime.bootTime", "guest.hostName", "guest.ipAddress", "guest.disk", "config.hardware.numCPU",
         "config.hardware.numCoresPerSocket", "config.hardware.memoryMB", "config.cpuAllocation.limit",
         "config.memoryAllocation.limit", "config.cpuHotAddEnabled", "config.memoryHotAddEnabled"]
# what is read, and how: (counter, rollup) -> our name. Percent counters come in hundredths.
COUNTERS = {"cpu.usage.average": "cpu", "cpu.ready.summation": "ready", "mem.usage.average": "mem",
            "mem.consumed.average": "consumed", "mem.vmmemctl.average": "balloon", "mem.swapped.average": "swapped"}
YEAR = ("cpu.usage.average", "mem.usage.average")


def percentile(values, p):
    """The p-th percentile (nearest rank) of values; None when there are none."""
    v = sorted(x for x in values or [] if x is not None)
    if not v:
        return None
    k = max(int(math.ceil(p / 100.0 * len(v))) - 1, 0)
    return v[min(k, len(v) - 1)]


def _r(x, n=1):
    return None if x is None else round(float(x), n)


def summarize(samples, interval, cpus):
    """{our name: [values]} of one VM (vCenter's units: hundredths of a percent, milliseconds, KB) -> its stats."""
    def good(k):
        return [float(x) for x in samples.get(k) or [] if x is not None and x >= 0]
    cpu, mem = [x / 100.0 for x in good("cpu")], [x / 100.0 for x in good("mem")]
    # cpu.ready: milliseconds the VM's vCPUs waited for a physical CPU in each sample, all vCPUs together -> % per vCPU
    ready = [x / (interval * 1000.0) * 100.0 / max(int(cpus or 1), 1) for x in good("ready")]
    consumed, balloon, swapped = good("consumed"), good("balloon"), good("swapped")
    n = max(len(cpu), len(mem))
    return {"interval": interval, "samples": n, "days": _r(n * interval / 86400.0),
            "cpu_avg": _r(sum(cpu) / len(cpu)) if cpu else None, "cpu_p95": _r(percentile(cpu, 95)), "cpu_max": _r(max(cpu)) if cpu else None,
            "ready_avg": _r(sum(ready) / len(ready), 2) if ready else None, "ready_p95": _r(percentile(ready, 95), 2),
            "mem_avg": _r(sum(mem) / len(mem)) if mem else None, "mem_p95": _r(percentile(mem, 95)), "mem_max": _r(max(mem)) if mem else None,
            "consumed_mb_avg": _r(sum(consumed) / len(consumed) / 1024.0, 0) if consumed else None,
            "balloon_mb_max": _r(max(balloon) / 1024.0, 0) if balloon else None,
            "balloon_mb_avg": _r(sum(balloon) / len(balloon) / 1024.0, 0) if balloon else None,
            "swapped_mb_max": _r(max(swapped) / 1024.0, 0) if swapped else None}


def busiest_day(values, times):
    """Daily averages (hundredths of a percent) with their dates -> (the highest in %, its date 'YYYY-MM-DD')."""
    best = None
    for t, v in zip(times, values or []):
        if v is None or v < 0 or t is None:
            continue
        if best is None or v > best[0]:
            best = (v, t)
    if best is None:
        return None, ""
    return _r(best[0] / 100.0), best[1].strftime("%Y-%m-%d")


def _match(name, patterns):
    return any(fnmatch.fnmatchcase(str(name).lower(), str(p).strip().lower()) for p in patterns or [] if str(p).strip())


def _folder(obj):
    names, x = [], getattr(obj, "parent", None)
    while x is not None and not isinstance(x, vim.Datacenter):
        names.append(x.name)
        x = getattr(x, "parent", None)
    dc = x.name if x is not None else ""
    return "/" + "/".join([dc] + list(reversed(names))) if dc else "/" + "/".join(reversed(names)), dc


def _host(host, seen):
    if host is None:
        return "", ""
    if host._moId not in seen:
        try:
            parent = host.parent
            seen[host._moId] = (host.name, parent.name if isinstance(parent, vim.ClusterComputeResource) else "")
        except Exception:                       # noqa: BLE001
            seen[host._moId] = ("", "")
    return seen[host._moId]


def _list(content, p):
    """The powered-on VMs to check -> ([(vm object, entry)], {off, templates, left_out})."""
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
    try:
        spec = vmodl.query.PropertyCollector.FilterSpec(
            objectSet=[vmodl.query.PropertyCollector.ObjectSpec(
                obj=view, skip=True,
                selectSet=[vmodl.query.PropertyCollector.TraversalSpec(name="v", path="view", skip=False, type=vim.view.ContainerView)])],
            propSet=[vmodl.query.PropertyCollector.PropertySpec(type=vim.VirtualMachine, pathSet=PROPS)])
        got = content.propertyCollector.RetrieveContents([spec]) or []
    finally:
        view.Destroy()
    out, hosts, folders, skipped = [], {}, {}, {"off": 0, "templates": 0}
    for o in got:
        pr = {x.name: x.val for x in o.propSet}
        vm, name = o.obj, pr.get("name", "")
        parent = getattr(vm, "parent", None)
        key = parent._moId if parent is not None else ""
        if key not in folders:
            folders[key] = _folder(vm)
        folder, dc = folders[key]
        if p["datacenter"] and dc != p["datacenter"]:
            continue
        if p["vms"] and not _match(name, p["vms"]) or _match(name, p["exclude"]):
            continue
        esxi, cluster = _host(pr.get("runtime.host"), hosts)
        if p["clusters"] and not _match(cluster, p["clusters"]):
            continue
        if pr.get("config.template"):
            skipped["templates"] += 1
            continue
        if str(pr.get("runtime.powerState") or "") != "poweredOn":
            skipped["off"] += 1
            continue
        boot = pr.get("runtime.bootTime")
        if boot is not None and boot.tzinfo is None:
            boot = boot.replace(tzinfo=datetime.timezone.utc)
        guest = "%s %s" % (pr.get("config.guestId") or "", pr.get("config.guestFullName") or "")
        disks = [{"path": str(getattr(d, "diskPath", "") or ""), "capacity_gb": _r((getattr(d, "capacity", 0) or 0) / 1024.0 ** 3),
                  "free_gb": _r((getattr(d, "freeSpace", 0) or 0) / 1024.0 ** 3)}
                 for d in pr.get("guest.disk") or [] if (getattr(d, "capacity", 0) or 0) > 0]
        out.append((vm, {"name": name, "moid": vm._moId, "datacenter": dc, "folder": folder, "cluster": cluster, "esxi_host": esxi,
                         "guest_os": pr.get("config.guestFullName") or "", "windows": "win" in guest.lower(),
                         "hostname": pr.get("guest.hostName") or "", "ip": pr.get("guest.ipAddress") or "",
                         "cpus": int(pr.get("config.hardware.numCPU") or 0),
                         "cores_per_socket": int(pr.get("config.hardware.numCoresPerSocket") or 1),
                         "memory_mb": int(pr.get("config.hardware.memoryMB") or 0),
                         "cpu_limit_mhz": int(pr["config.cpuAllocation.limit"]) if pr.get("config.cpuAllocation.limit") is not None else -1,
                         "mem_limit_mb": int(pr["config.memoryAllocation.limit"]) if pr.get("config.memoryAllocation.limit") is not None else -1,
                         "cpu_hot_add": bool(pr.get("config.cpuHotAddEnabled")), "mem_hot_add": bool(pr.get("config.memoryHotAddEnabled")),
                         "boot_time": boot.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if boot else "",
                         "disks": sorted(disks, key=lambda d: d["path"])}))
    return out, skipped


def _query(pm, specs):
    """One QueryPerf; when vCenter refuses the batch, each VM on its own. -> (results, VMs that could not be read, error)."""
    try:
        return pm.QueryPerf(querySpec=specs) or [], 0, ""
    except Exception as e:                      # noqa: BLE001
        if len(specs) == 1:
            return [], 1, str(getattr(e, "msg", "") or e)
        res, bad, err = [], 0, str(getattr(e, "msg", "") or e)
        for s in specs:
            r, b, _ = _query(pm, [s])
            res += r
            bad += b
        return res, bad, err if bad else ""


def _series(results, ids):
    """QueryPerf's results -> {moid: ({our name: [values]}, [sample times])}."""
    out = {}
    for r in results or []:
        times = [s.timestamp for s in (r.sampleInfo or [])]
        vals = {}
        for s in r.value or []:
            k = ids.get(s.id.counterId)
            if k is not None and str(s.id.instance or "") == "":
                vals[k] = list(s.value or [])
        out[r.entity._moId] = (vals, times)
    return out


def _read(content, p, now, vcenter):
    vms, skipped = _list(content, p)
    notes, info = [], []
    pm = content.perfManager
    counters = {}
    for c in pm.perfCounter or []:
        counters["%s.%s.%s" % (c.groupInfo.key, c.nameInfo.key, c.rollupType)] = c
    intervals = {i.samplingPeriod: i for i in pm.historicalInterval or [] if i.enabled}
    days = max(min(int(p["days"]), 30), 1)
    interval = 7200 if 7200 in intervals else (1800 if 1800 in intervals else None)
    if interval == 1800 and days > 7:
        notes.append("vCenter keeps no past-month statistics (2-hour averages): the last 7 days only (30-minute averages)")
        days = 7
    if interval is None:
        notes.append("vCenter's past-week and past-month statistics intervals are turned off: no history")
    ids = {counters[n].key: k for n, k in COUNTERS.items() if n in counters}
    level = {k: getattr(counters[n], "level", 1) for n, k in COUNTERS.items() if n in counters}
    missing = [n for n in COUNTERS if n not in counters and n != "mem.swapped.average"]
    if missing:
        notes.append("vCenter has no %s counter" % ", ".join(missing))
    metric = [vim.PerformanceManager.MetricId(counterId=c, instance="") for c in ids]
    yids = {counters[n].key: COUNTERS[n] for n in YEAR if n in counters}
    ymetric = [vim.PerformanceManager.MetricId(counterId=c, instance="") for c in yids]
    start = datetime.datetime.fromtimestamp(now - days * 86400, tz=datetime.timezone.utc)
    ystart = datetime.datetime.fromtimestamp(now - p["year_days"] * 86400, tz=datetime.timezone.utc)
    do_year = p["year_days"] > 0 and 86400 in intervals and ymetric

    jobs = []
    size = max(int(p["batch"]), 1)
    for i in range(0, len(vms), size):
        part = [v for v, _ in vms[i:i + size]]
        if interval is not None and metric:
            jobs.append(("days", [vim.PerformanceManager.QuerySpec(entity=v, metricId=metric, intervalId=interval, startTime=start)
                                  for v in part]))
        if do_year:
            jobs.append(("year", [vim.PerformanceManager.QuerySpec(entity=v, metricId=ymetric, intervalId=86400, startTime=ystart)
                                  for v in part]))
    got = {"days": {}, "year": {}}
    unread, errors = 0, []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(int(p["workers"]), 1)) as pool:
        for kind, (res, bad, err) in zip([k for k, _ in jobs], pool.map(lambda j: _query(pm, j[1]), jobs)):
            got[kind].update(_series(res, ids if kind == "days" else yids))
            if kind == "days":
                unread += bad
            if err and err not in errors:
                errors.append(err)
    if unread:
        notes.append("the statistics of %d VM(s) could not be read: %s" % (unread, "; ".join(errors[:2])))
    if not do_year and p["year_days"] > 0:
        info.append("vCenter keeps no past-year statistics (daily averages): no busiest day of the year")

    out = []
    for v, e in vms:
        vals, _ = got["days"].get(v._moId, ({}, []))
        st = summarize(vals, interval or 7200, e["cpus"])
        yv, yt = got["year"].get(v._moId, ({}, []))
        st["year_cpu_day"], st["year_cpu_date"] = busiest_day(yv.get("cpu"), yt)
        st["year_mem_day"], st["year_mem_date"] = busiest_day(yv.get("mem"), yt)
        e["stats"], e["vcenter"] = st, vcenter
        out.append(e)
    if any(level.get(k, 1) > 1 for k in ("swapped",)) and not any(x["stats"]["swapped_mb_max"] is not None for x in out):
        info.append("swapped memory is kept at statistics level 2 only: not in this vCenter's history (ballooned memory is)")
    guest = sorted(n for n in counters if n.split(".")[0] == "guest" or ".guest" in n or "estimated" in n)
    if guest:
        info.append("vCenter also offers %s: not used by this report yet" % ", ".join(guest[:12]))
    return {"vms": out, "skipped": skipped, "notes": notes, "info": info, "interval": interval, "days": days}


def main():
    module = AnsibleModule(
        argument_spec=dict(datacenter=dict(type="str"), vcenters=dict(type="list", elements="str", default=[]),
                           clusters=dict(type="list", elements="str", default=[]), vms=dict(type="list", elements="str", default=[]),
                           exclude=dict(type="list", elements="str", default=[]), days=dict(type="int", default=30),
                           year_days=dict(type="int", default=365), batch=dict(type="int", default=10),
                           workers=dict(type="int", default=4), validate_certs=dict(type="bool", default=True), now=dict(type="float")),
        supports_check_mode=True,
    )
    if not HAS_PYVMOMI:
        module.fail_json(msg="pyVmomi is not installed in the execution environment (the vmware.vmware collection needs it too)")
    p = module.params
    now = p["now"] or time.time()
    done, statuses = each_vcenter(module, lambda si, content, name: _read(content, p, now, name), p["vcenters"],
                                  p["validate_certs"], "the VMs' statistics")
    skipped = {"off": 0, "templates": 0}
    notes, info = [], []
    for name, part in done:
        for k in skipped:
            skipped[k] += part["skipped"][k]
        notes += [("%s: %s" % (name, n)) if len(done) > 1 else n for n in part["notes"]]
        info += [("%s: %s" % (name, n)) if len(done) > 1 else n for n in part["info"]]
    module.exit_json(changed=False, vms=[x for _, part in done for x in part["vms"]], not_checked=skipped, notes=notes, info=info,
                     days=min([part["days"] for _, part in done] or [p["days"]]),
                     interval=max([part["interval"] or 0 for _, part in done] or [0]), vcenters=statuses,
                     read_at=datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))


if __name__ == "__main__":
    main()
