# -*- coding: utf-8 -*-
"""vCenter's own daily statistics (the 1-day interval), read once for every module that needs them: the capacity planning
(roles/vmware_vm/library/site_vmware_capacity.py - 90 days of cluster CPU and memory, the datastores' used space) and the
cluster heat grid's lines (site_vmware_heat_history.py - the days earlier runs have not saved yet). Read only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime

try:
    from pyVmomi import vim, vmodl
    HAS_PYVMOMI = True
except ImportError:
    HAS_PYVMOMI = False

UNIT = {"kiloBytes": 1024, "megaBytes": 1024 ** 2, "gigaBytes": 1024 ** 3, "teraBytes": 1024 ** 4, "bytes": 1,
        "megaHertz": 1, "kiloBytesPerSecond": 1024}


def series(sample_times, values, factor=1):
    """vCenter's values and their timestamps -> [[seconds since 1970, value * factor]], without the
    -1 vCenter writes for a missing sample, oldest first."""
    out = []
    for t, v in zip(sample_times, values or []):
        if v is None or v < 0 or t is None:
            continue
        out.append([int(t), float(v) * factor])
    return sorted(out)


def perf_series(content, specs_by_key, history_days, now):
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


def props(content, objtype, paths, root=None):
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


def datacenter_of(obj, cache):
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
