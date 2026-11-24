#!/usr/bin/python
# -*- coding: utf-8 -*-
"""1.116.6: VMware - VM placement snapshot. Reads every VM's host, cluster, datastores and power state from vCenter
(read-only) and keeps them in the report database (plugins/module_utils/site_vm_placement.py): only what changed since
the last run, and one whole snapshot a day. The outage review reads it back for the VMs on a lost datastore or host when
the outage began - also for a past window. Never fails on the database: one warning, and the next run tries again."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vm_placement_snapshot
short_description: Keep every VM's host, cluster, datastores and power state in the report database
description:
  - Reads every VM of each vCenter (templates left out) and writes, per vCenter, the VMs new or changed since the last
    run and those gone, plus one whole snapshot a day (UTC). Deletes what is older than keep_days.
  - Makes the tables vm_placement and vm_placement_run on the first run (create). In check mode it reads and writes
    nothing to the database.
  - vCenter's address and account come from VMWARE_HOST, VMWARE_USER, VMWARE_PASSWORD (and VMWARE_PORT); vcenters adds
    more vCenters (the same account). The database's from the "Report database" credential.
options:
  host: {description: The database server., type: str, required: true}
  port: {description: Its port., type: int, default: 3309}
  database: {description: The database., type: str, required: true}
  user: {description: The account (the writer)., type: str, required: true}
  password: {description: Its password., type: str, required: true}
  tls: {description: Use TLS and check the server's certificate., type: bool, default: false}
  ca_file: {description: The CA file the certificate is checked with., type: str, default: ''}
  connect_timeout: {description: Seconds to wait for the connection., type: int, default: 10}
  create: {description: Make the tables when they are not there., type: bool, default: true}
  keep_days: {description: Delete rows older than this many days (0 = never)., type: int, default: 400}
  job: {description: This job's ID (0 outside AAP)., type: int, default: 0}
  vcenters: {description: More vCenters to read besides the credential's (same account)., type: list, elements: str, default: []}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
  timeout: {description: Seconds to wait for vCenter., type: int, default: 60}
author: site automation
'''

RETURN = r'''
ok: {description: The database answered and the run was kept (in check mode, read only)., type: bool}
error: {description: Why not, when ok is false., type: str}
read_at: {description: When vCenter was read (UTC)., type: str}
runs: {description: "Per vCenter: {vcenter, vms, whole, kept}.", type: list}
pruned: {description: Rows deleted (older than keep_days)., type: int}
created: {description: The tables were made this run., type: bool}
vcenters: {description: Each vCenter and whether it was read., type: list}
'''

import datetime

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_vcenters import each_vcenter
from ansible.module_utils import site_report_db as db
from ansible.module_utils import site_vm_placement as vp

try:
    from pyVmomi import vim, vmodl
    HAS_PYVMOMI = True
except ImportError:
    HAS_PYVMOMI = False


def _props(content, objtype, paths):
    view = content.viewManager.CreateContainerView(content.rootFolder, [objtype], True)
    try:
        spec = vmodl.query.PropertyCollector.FilterSpec(
            objectSet=[vmodl.query.PropertyCollector.ObjectSpec(
                obj=view, skip=True,
                selectSet=[vmodl.query.PropertyCollector.TraversalSpec(name="v", path="view", skip=False, type=vim.view.ContainerView)])],
            propSet=[vmodl.query.PropertyCollector.PropertySpec(type=objtype, pathSet=paths)])
        return {o.obj._moId: (o.obj, {p.name: p.val for p in o.propSet}) for o in content.propertyCollector.RetrieveContents([spec]) or []}
    finally:
        view.Destroy()


def placements(props):
    """[{moid, name, host, host_moid, cluster, datastores, datastore_moids, power}] from the property reads
    {kind: {moid: (obj, props)}} - the whole vCenter, always (a datacenter filter would mark the others' VMs gone);
    pure, so a test hands it stand-ins."""
    clusters = {m: pr.get("name", "") for m, (_, pr) in props["cluster"].items()}
    stores = {m: pr.get("name", "") for m, (_, pr) in props["datastore"].items()}
    hosts = {m: (pr.get("name", ""), clusters.get(getattr(pr.get("parent"), "_moId", ""), "")) for m, (_, pr) in props["host"].items()}
    out = []
    for m, (_, pr) in props["vm"].items():
        if pr.get("config.template"):
            continue
        host_moid = getattr(pr.get("runtime.host"), "_moId", "")
        host, cluster = hosts.get(host_moid, ("", ""))
        pairs = sorted({(stores.get(getattr(d, "_moId", ""), ""), getattr(d, "_moId", "")) for d in pr.get("datastore") or []}
                       - {("", "")})
        out.append({"moid": m, "name": pr.get("name", ""), "host": host, "host_moid": host_moid, "cluster": cluster,
                    "power": str(pr.get("runtime.powerState") or ""),
                    "datastores": [n for n, _ in pairs], "datastore_moids": [i for _, i in pairs]})
    return sorted(out, key=lambda v: (v["name"], v["moid"]))


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, microsecond=0)


def _read(content):
    """(when this vCenter's read ended, its placements): each vCenter is stamped with its own read (1.116.6 review)."""
    props = {"cluster": _props(content, vim.ClusterComputeResource, ["name"]), "datastore": _props(content, vim.Datastore, ["name"]),
             "host": _props(content, vim.HostSystem, ["name", "parent"]),
             "vm": _props(content, vim.VirtualMachine, ["name", "runtime.host", "runtime.powerState", "datastore", "config.template"])}
    return _now(), placements(props)


def keep(conn, reads, now, p, check_mode):
    """Write each vCenter's run at its own read time (not in check mode) -> (runs, pruned, created). reads: [(vcenter,
    (read_at, vms))]; now: the job's time, for the pruning."""
    cur = conn.cursor()
    there, created = vp.schema(cur, p["create"] and not check_mode)
    if not there:
        raise RuntimeError("the tables vm_placement and vm_placement_run are not there%s - make them once, docs/REPORT_DATABASE.md"
                           % (" (check mode: nothing made)" if check_mode and p["create"] else " (vm_placement_db_create: false)"))
    runs = []
    for vcenter, (read_at, vms) in reads:
        if check_mode:
            runs.append({"vcenter": vcenter, "vms": len(vms), "whole": vp.need_full(cur, vcenter, read_at), "kept": 0,
                         "read_at": vp.iso(read_at)})
            continue
        whole, kept = vp.write(cur, vcenter, read_at, vms, p["job"])
        runs.append({"vcenter": vcenter, "vms": len(vms), "whole": whole, "kept": kept, "read_at": vp.iso(read_at)})
    pruned = 0 if check_mode else vp.prune(cur, now, p["keep_days"])
    if not check_mode:
        conn.commit()
    return runs, pruned, created


def main():
    module = AnsibleModule(
        argument_spec=dict(db.ARGS, create=dict(type="bool", default=True), keep_days=dict(type="int", default=400),
                           job=dict(type="int", default=0),
                           vcenters=dict(type="list", elements="str", default=[]), validate_certs=dict(type="bool", default=True),
                           timeout=dict(type="int", default=60)),
        supports_check_mode=True)
    p = module.params
    if not HAS_PYVMOMI:
        module.fail_json(msg="pyVmomi is not installed in the execution environment (the vmware.vmware collection needs it too)")
    now = _now()
    done, statuses = each_vcenter(module, lambda si, content, name: _read(content), p["vcenters"],
                                  p["validate_certs"], "the VMs' placement", p["timeout"])
    where = "%s:%s/%s" % (p["host"], p["port"], p["database"])
    res = dict(changed=False, ok=False, error="", read_at=vp.iso(now), runs=[{"vcenter": n, "vms": len(v[1]), "whole": False, "kept": 0,
               "read_at": vp.iso(v[0])} for n, v in done], pruned=0, created=False, vcenters=statuses, where=where)
    if not db.HAS_PYMYSQL:
        res["error"] = db.NO_PYMYSQL
    else:
        conn = None
        try:
            conn, _ = db.connect(p)
            res["runs"], res["pruned"], res["created"] = keep(conn, done, _now(), p, module.check_mode)
            res["ok"] = True
            res["changed"] = not module.check_mode and bool(sum(r["kept"] for r in res["runs"]) or res["pruned"] or res["created"])
        except Exception as e:      # noqa: BLE001 - unreachable, refused, a grant missing: one line, the next run tries again
            try:
                if conn is not None:
                    conn.rollback()
            except Exception:       # noqa: BLE001
                pass
            res["error"] = db.explain(e, p)
        finally:
            try:
                if conn is not None:
                    conn.close()
            except Exception:       # noqa: BLE001
                pass
    if res["error"]:
        module.warn("The report database %s: %s - this run's placement was not kept; the next run tries again" % (where, res["error"]))
    module.exit_json(**res)


if __name__ == "__main__":
    main()
