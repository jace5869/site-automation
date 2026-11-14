#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Read vCenter's events of one stretch of time, for the outage review's time line (read-only). pyVmomi only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vmware_outage_events
short_description: Read vCenter's events of one stretch of time, for the outage review's time line (read-only)
description:
  - The events between I(begin) and I(end) that tell an outage - a host lost and back, vSphere HA (a host failed or
    isolated, VMs restarted), storage (all paths down, a device lost, a path's redundancy, a VMFS heartbeat), the
    uplinks' redundancy, maintenance mode, and every alarm that changed its colour - oldest first. The outage review
    of the VM outage watch (playbooks/vm_outage_watch.yml, vm_watch_review) draws them on one time line.
  - vCenter's address and account come from VMWARE_HOST, VMWARE_USER, VMWARE_PASSWORD (and VMWARE_PORT), as a
    "VMware vCenter" credential in AAP gives them; vcenters adds more vCenters (the same account). A read-only
    vCenter role is enough. Changes nothing.
options:
  datacenter: {description: Only this datacenter., type: str}
  vcenters: {description: More vCenters to read besides the credential's (same account; name or name:port)., type: list, elements: str, default: []}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
  begin: {description: The start of the stretch (seconds since 1970)., type: float, required: true}
  end: {description: Its end (seconds since 1970)., type: float, required: true}
  event_types: {description: "The event types read (vCenter's event type names); none = the built-in list.", type: list, elements: str}
  max_events: {description: At most this many events per vCenter (the newest are kept)., type: int, default: 2000}
author: site automation
'''

RETURN = r'''
events:
  description: The events, oldest first.
  type: list
  returned: success
  sample: [{key: 4711, time: "2026-11-12T01:19:04Z", type: HostConnectionLostEvent, message: Host esx03 is not responding,
            host: esx03.example.mil, vm: "", cluster: cluster1, datacenter: DC1, vcenter: vc01.example.mil, args: {},
            alarm: "", entity: "", from: "", to: ""}]
capped:
  description: The vCenters that had more events than max_events (the oldest were left out).
  type: list
  returned: success
vcenters:
  description: Each vCenter asked, and whether it was read.
  type: list
  returned: success
'''

import datetime

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_vcenters import each_vcenter

try:
    from pyVmomi import vim
    HAS_PYVMOMI = True
except ImportError:
    HAS_PYVMOMI = False

# the same list as plugins/filter/outage_timeline_filters.py knows a level and a word for (tests/test_outage_timeline.py
# holds the two together)
EVENT_TYPES = [
    # a host and vCenter
    "HostConnectionLostEvent", "HostDisconnectedEvent", "HostConnectedEvent", "HostReconnectionFailedEvent",
    "HostShutdownEvent", "EnteredMaintenanceModeEvent", "ExitMaintenanceModeEvent",
    # vSphere HA
    "com.vmware.vc.ha.VmRestartedByHAEvent", "VmRestartedOnAlternateHostEvent", "VmFailoverFailed",
    "VmDasBeingResetEvent", "VmDasBeingResetWithScreenshotEvent", "DasHostFailedEvent", "DasHostIsolatedEvent",
    "com.vmware.vc.HA.DasHostFailedEvent", "com.vmware.vc.HA.DasHostIsolatedEvent",
    "com.vmware.vc.HA.ClusterFailoverActionInitiatedEvent",
    # storage
    "esx.problem.storage.apd.start", "esx.problem.storage.apd.timeout", "esx.clear.storage.apd.exit",
    "esx.problem.scsi.device.state.permanentloss", "esx.problem.scsi.device.state.permanentloss.noopens",
    "esx.clear.scsi.device.state.permanentloss.deviceonline",
    "esx.problem.vmfs.heartbeat.timedout", "esx.problem.vmfs.heartbeat.unrecoverable", "esx.problem.vmfs.heartbeat.recovered",
    "esx.problem.storage.redundancy.lost", "esx.problem.storage.redundancy.degraded", "esx.clear.storage.redundancy.restored",
    "esx.problem.storage.connectivity.lost", "esx.clear.storage.connectivity.restored",
    # the uplinks
    "esx.problem.net.redundancy.lost", "esx.problem.net.redundancy.degraded", "esx.clear.net.redundancy.restored",
    "esx.problem.net.connectivity.lost", "esx.clear.net.connectivity.restored",
    # every alarm that changed its colour
    "AlarmStatusChangedEvent"]


def _iso(t):
    if t is None:
        return ""
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _name(arg):
    return str(getattr(arg, "name", "") or "") if arg is not None else ""


def record(e):
    """One event as the time line reads it."""
    args = {}
    for a in getattr(e, "arguments", None) or []:
        args[str(getattr(a, "key", ""))] = str(getattr(a, "value", ""))
    return {"key": e.key, "time": _iso(e.createdTime), "type": getattr(e, "eventTypeId", "") or type(e).__name__.split(".")[-1],
            "message": " ".join(str(getattr(e, "fullFormattedMessage", "") or getattr(e, "message", "") or "").split()), "args": args,
            "host": _name(getattr(e, "host", None)), "vm": _name(getattr(e, "vm", None)),
            "cluster": _name(getattr(e, "computeResource", None)), "datacenter": _name(getattr(e, "datacenter", None)),
            "alarm": _name(getattr(e, "alarm", None)), "entity": _name(getattr(e, "entity", None)),
            "from": str(getattr(e, "from", "") or ""), "to": str(getattr(e, "to", "") or "")}


def _events(content, root, begin, end, types, cap):
    """-> (the events between begin and end, oldest first; True when there were more than cap)."""
    spec = vim.event.EventFilterSpec(time=vim.event.EventFilterSpec.ByTime(beginTime=begin, endTime=end), eventTypeId=list(types))
    if root is not None:
        spec.entity = vim.event.EventFilterSpec.ByEntity(entity=root, recursion="all")
    found, more = {}, False
    col = content.eventManager.CreateCollectorForEvents(spec)
    try:
        col.SetCollectorPageSize(500)
        batch = list(col.latestPage or [])
        while batch:
            for e in batch:
                found.setdefault(e.key, e)
            if len(found) > cap:
                more = True
                break
            nxt = col.ReadPreviousEvents(500) or []
            if not nxt or all(e.key in found for e in nxt):
                break
            batch = nxt
    finally:
        try:
            col.DestroyCollector()
        except Exception:                       # noqa: BLE001
            pass
    # a simulator (and an old vCenter) may ignore the end time: keep to the stretch here too
    keep = [e for e in found.values() if e.createdTime is not None and _iso(begin) <= _iso(e.createdTime) <= _iso(end)]
    keep.sort(key=lambda x: (x.createdTime, x.key))
    return [record(e) for e in keep[-cap:]], more or len(keep) > cap


def _read(content, p, name):
    root = None
    if p["datacenter"]:
        dcs = [d for d in content.rootFolder.childEntity if isinstance(d, vim.Datacenter) and d.name == p["datacenter"]]
        if not dcs:
            return {"events": [], "capped": False}
        root = dcs[0]
    begin = datetime.datetime.fromtimestamp(p["begin"], tz=datetime.timezone.utc)
    end = datetime.datetime.fromtimestamp(p["end"], tz=datetime.timezone.utc)
    events, capped = _events(content, root, begin, end, p["event_types"], max(1, int(p["max_events"])))
    for e in events:
        e["vcenter"] = name
    return {"events": events, "capped": capped}


def main():
    module = AnsibleModule(
        argument_spec=dict(datacenter=dict(type="str"), validate_certs=dict(type="bool", default=True),
                           vcenters=dict(type="list", elements="str", default=[]),
                           begin=dict(type="float", required=True), end=dict(type="float", required=True),
                           event_types=dict(type="list", elements="str"), max_events=dict(type="int", default=2000)),
        supports_check_mode=True,
    )
    if not HAS_PYVMOMI:
        module.fail_json(msg="pyVmomi is not installed in the execution environment (the vmware.vmware collection needs it too)")
    p = module.params
    if not p["event_types"]:
        p["event_types"] = EVENT_TYPES
    if p["end"] <= p["begin"]:
        module.fail_json(msg="The outage review's stretch of time ends before it starts (vm_watch_review_from, vm_watch_review_to).")
    done, statuses = each_vcenter(module, lambda si, content, name: _read(content, p, name), p["vcenters"],
                                  p["validate_certs"], "the events of the outage review")
    events, capped = [], []
    for name, part in done:
        events += part["events"]
        if part["capped"]:
            capped.append(name)
    events.sort(key=lambda x: (x["time"], x["key"]))
    module.exit_json(changed=False, events=events, capped=capped, vcenters=statuses,
                     begin=_iso(datetime.datetime.fromtimestamp(p["begin"], tz=datetime.timezone.utc)),
                     end=_iso(datetime.datetime.fromtimestamp(p["end"], tz=datetime.timezone.utc)))


if __name__ == "__main__":
    main()
