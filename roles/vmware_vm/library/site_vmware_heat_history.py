#!/usr/bin/python
# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vmware_heat_history
short_description: Each cluster's daily CPU and memory use from vCenter's own statistics (read-only)
description:
  - For the cluster heat grid (roles/vmware_vm/tasks/heat.yml, vm_heat_vcenter_history). When the template's own history
    has fewer than vm_heat_days days, the missing days of each cluster's CPU (cpu.usagemhz.average) and memory
    (mem.consumed.average) are read from vCenter's daily statistics (the 1-day interval) - the same reading the capacity
    planning uses (ansible.module_utils.site_vcenter_stats), one batched read for all the clusters asked for.
  - The values come back as vCenter keeps them (MHz, bytes); the heat grid turns them into percentages of the cluster's
    capacity now. Changes nothing, and never needs more than a read-only role.
options:
  clusters: {description: "The clusters wanted, each as vcenter/datacenter/cluster (the heat grid's own key).", type: list, elements: str, required: true}
  days: {description: Days of daily statistics to read., type: int, default: 30}
  vcenters: {description: More vCenters to read besides the credential's (same account; name or name:port)., type: list, elements: str, default: []}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
  now: {description: The time to measure from (seconds since 1970; tests only)., type: float}
author: site automation
'''

RETURN = r'''
history:
  description: "{cluster key: {cpu: [[seconds, MHz]], mem: [[seconds, bytes]]}}, oldest first. A cluster vCenter has no samples for is not there."
  type: dict
  returned: success
notes:
  description: Why nothing came for some or all clusters (the daily interval turned off, no such counter, the read failed).
  type: list
  returned: success
'''

import datetime
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_vcenter_stats import HAS_PYVMOMI, datacenter_of, perf_series, props
from ansible.module_utils.site_vcenters import each_vcenter

if HAS_PYVMOMI:
    from pyVmomi import vim


def read(content, p, now, host):
    """{key: {cpu, mem}} and the notes, for this vCenter's clusters that are in p['clusters']."""
    wanted = set(p["clusters"])
    dcache = {}
    for m, (o, pr) in props(content, vim.Datacenter, ["name"]).items():
        dcache[m] = pr.get("name", "")
    clus = props(content, vim.ClusterComputeResource, ["name"])
    specs, keys = {}, {}
    for m, (o, pr) in clus.items():
        key = "/".join([host, datacenter_of(o, dcache), pr.get("name", "")])
        if key not in wanted:
            continue
        keys[m] = key
        specs[("c", m, "cpu")] = (o, "cpu.usagemhz.average")
        specs[("c", m, "mem")] = (o, "mem.consumed.average")
    hist, note = perf_series(content, specs, p["days"], now)
    out = {}
    for (_, m, what), s in hist.items():
        if s:
            out.setdefault(keys[m], {})[what] = s
    return {"history": out, "notes": [note] if note else []}


def main():
    module = AnsibleModule(
        argument_spec=dict(clusters=dict(type="list", elements="str", required=True), days=dict(type="int", default=30),
                           vcenters=dict(type="list", elements="str", default=[]), validate_certs=dict(type="bool", default=True),
                           now=dict(type="float")),
        supports_check_mode=True,
    )
    if not HAS_PYVMOMI:
        module.fail_json(msg="pyVmomi is not installed in the execution environment (the vmware.vmware collection needs it too)")
    p = module.params
    now = p["now"] or time.time()
    done, statuses = each_vcenter(module, lambda si, content, name: read(content, p, now, name), p["vcenters"],
                                  p["validate_certs"], "the clusters' statistics")
    res = {"vcenters": statuses, "read_at": datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "history": {}, "notes": []}
    for name, part in done:
        res["history"].update(part["history"])
        res["notes"] += [("%s: %s" % (name, n)) if len(done) > 1 else n for n in part["notes"]]
    module.exit_json(changed=False, **res)


if __name__ == "__main__":
    main()
