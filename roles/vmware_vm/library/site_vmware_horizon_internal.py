#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Read the internal VMs Horizon's instant clones leave in vCenter (read-only): cp-template-*, cp-replica-*,
cp-parent-*, the ClonePrep folders, and the golden image VMs they were made from. pyVmomi only. The report
and every decision about them is in plugins/filter/horizon_internal_filters.py."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vmware_horizon_internal
short_description: Read Horizon's instant-clone internal VMs from vCenter (read-only)
description:
  - Finds the internal VMs of Horizon's instant clones in every vCenter of the job - a VM named cp-template-,
    cp-replica- or cp-parent- (any case), a VM in one of Horizon's ClonePrep folders, and a VM another internal VM
    names as its template or replica (its template or replica UUID setting, cloneprep.*.uuid, is that VM's instance
    UUID) - with its host, datastores, space used, power and connection state, when it was made, and
    its cloneprep.* settings (advanced settings, config.extraConfig).
  - The golden image VMs are read too - each VM whose instance UUID an internal VM's cloneprep.master.metadata
    names, and each VM whose ID is in golden_ids (the parent VM IDs Horizon gives for its pools) - with their
    snapshots, so the report can tell which pool still uses an internal VM.
  - vCenter's address and account come from VMWARE_HOST, VMWARE_USER, VMWARE_PASSWORD (and VMWARE_PORT), as a
    "VMware vCenter" credential in AAP gives them; vcenters adds more vCenters (the same account). A vCenter that
    cannot be read does not stop the others. Changes nothing.
options:
  vcenters: {description: More vCenters to read besides the credential's (same account; name or name:port)., type: list, elements: str, default: []}
  golden_ids: {description: "VM IDs (vm-123) to read as golden image VMs, in every vCenter - Horizon's parent_vm_id.", type: list, elements: str, default: []}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
  now: {description: The time to measure ages from (seconds since 1970; tests only)., type: float}
author: site automation
'''

RETURN = r'''
internal:
  description: One entry per internal VM.
  type: list
  returned: success
  sample: [{vcenter: vc01.example.mil, moid: vm-77, name: cp-replica-2c1f..., uuid: 5012..., folder: /DC1/vm/ClonePrepReplicaVmFolder,
            folder_name: ClonePrepReplicaVmFolder, found_by: [name, folder], power: poweredOff, connection: connected,
            host: esx01.example.mil, host_maintenance: false, cluster: CL01, datastores: [DS01], committed_gb: 41.2,
            created: "2026-10-01T12:00:00Z", age_hours: 860.5, vmx: "[DS01] 2c1f.../2c1f....vmx",
            cloneprep: {cloneprep.master.metadata: "...", cloneprep.client.uuid: "..."}}]
golden:
  description: The golden image VMs found (by instance UUID or ID), each with its snapshots.
  type: list
  returned: success
  sample: [{vcenter: vc01.example.mil, moid: vm-12, uuid: 5012..., name: W11-Gold, path: /DC1/vm/Gold/W11-Gold,
            snapshots: [{id: 3, moid: snapshot-40, name: v2026-11, path: /v2026-10/v2026-11}]}]
vcenters:
  description: Each vCenter and whether it was read (ok), or why not (error).
  type: list
  returned: success
'''

import datetime
import re
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_vcenters import each_vcenter

try:
    from urllib.parse import unquote
except ImportError:                             # Python 2 (never on an execution environment)
    from urllib import unquote

try:
    from pyVmomi import vim, vmodl
    HAS_PYVMOMI = True
except ImportError:
    HAS_PYVMOMI = False

GB = 1024.0 ** 3
# the names of Horizon's internal VMs (ControlUp, Omnissa's euc-samples get-horizon-ic-pool-info.ps1)
NAME_RE = re.compile(r"^cp-(template|replica|parent)-", re.I)
# the folders Horizon makes for them when an image is published (Horizon's documentation:
# ClonePrepInternalTemplateFolder, ClonePrepParentVmFolder, ClonePrepReplicaVmFolder, ClonePrepResyncVmFolder)
FOLDER_RE = re.compile(r"^cloneprep\w*folder$", re.I)
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
# the settings that name another internal VM by its instance UUID (euc-samples get-horizon-ic-pool-info.ps1)
REF_KEYS = ("cloneprep.internal.template.uuid", "cloneprep.replica.uuid")   # public-ok: settings, not hosts
MASTER_KEY = "cloneprep.master.metadata"


def candidate(name, folder_name):
    """Why a VM is an internal VM ([] = it is not): its name, its folder. Pure."""
    why = []
    if NAME_RE.match(str(name or "")):
        why.append("name")
    if FOLDER_RE.match(str(folder_name or "")):
        why.append("folder")
    return why


def uuids_in(text):
    """The UUIDs in a setting's value, lower case, in order, each once; the value may be URL-encoded
    (cloneprep.master.metadata is: key%3Dvalue%2C...). Pure."""
    t = str(text or "")
    for _ in range(3):
        u = unquote(t)
        if u == t:
            break
        t = u
    out = []
    for x in UUID_RE.findall(t):
        if x.lower() not in out:
            out.append(x.lower())
    return out


def snapshot_paths(nodes, parent=""):
    """A VM's snapshot tree -> [{id, moid, name, path}] ('/v2026-10/v2026-11'). nodes: [(id, moid, name, children)]. Pure."""
    out = []
    for sid, moid, name, kids in nodes or []:
        path = "%s/%s" % (parent, name)
        out.append({"id": sid, "moid": moid, "name": name, "path": path})
        out += snapshot_paths(kids, path)
    return out


def _tree(nodes):
    return [(int(n.id), n.snapshot._moId, n.name, _tree(n.childSnapshotList)) for n in nodes or []]


def _iso(t):
    if t is None:
        return "", None
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), t.timestamp()


def _collect(content, typ, paths, objs=None, optional=()):
    """{moid: {property: value}} of every object of type typ (a container view) or of objs. A property this
    vCenter does not know (an optional one, e.g. config.createDate before 6.7) is left out and asked again
    without it."""
    pc = vmodl.query.PropertyCollector
    view = None
    if objs is None:
        view = content.viewManager.CreateContainerView(content.rootFolder, [typ], True)
        oset = [pc.ObjectSpec(obj=view, skip=True, selectSet=[pc.TraversalSpec(name="v", path="view", skip=False,
                                                                                    type=vim.view.ContainerView)])]
    else:
        oset = [pc.ObjectSpec(obj=o, skip=False) for o in objs]
    try:
        if not oset:
            return {}
        try:
            res = content.propertyCollector.RetrieveContents([pc.FilterSpec(objectSet=oset, propSet=[pc.PropertySpec(type=typ, pathSet=paths)])])
        except vmodl.query.InvalidProperty:
            if not optional:
                raise
            keep = [p for p in paths if p not in optional]
            res = content.propertyCollector.RetrieveContents([pc.FilterSpec(objectSet=oset, propSet=[pc.PropertySpec(type=typ, pathSet=keep)])])
        out = {}
        for o in res or []:
            pr = {x.name: x.val for x in o.propSet}
            pr["_obj"] = o.obj
            out[o.obj._moId] = pr
        return out
    finally:
        if view is not None:
            view.Destroy()


def _path(moid, parents, names, root):
    """'/DC1/vm/Gold' for a folder (or a VM's parent), from the parent map; the root folder is left out."""
    out, x, n = [], moid, 0
    while x and x != root and n < 64:
        out.append(names.get(x, x))
        x = parents.get(x)
        n += 1
    return "/" + "/".join(reversed(out))


def _read(content, p, now, vcenter):
    root = content.rootFolder._moId
    fold = _collect(content, vim.Folder, ["name", "parent"])
    dcs = _collect(content, vim.Datacenter, ["name", "parent"])
    names = dict((k, v.get("name", "")) for k, v in list(fold.items()) + list(dcs.items()))
    parents = dict((k, v["parent"]._moId if v.get("parent") is not None else None) for k, v in list(fold.items()) + list(dcs.items()))
    allvm = _collect(content, vim.VirtualMachine, ["name", "parent", "config.instanceUuid"])
    by_uuid = {}
    for k, v in allvm.items():
        u = str(v.get("config.instanceUuid") or "").lower()
        if u:
            by_uuid.setdefault(u, k)

    def folder_of(v):
        par = v.get("parent")
        return par._moId if par is not None else None

    found = {}
    for k, v in allvm.items():
        why = candidate(v.get("name"), names.get(folder_of(v)))
        if why:
            found[k] = why
    detail = {}
    props = ["name", "config.extraConfig", "config.uuid", "config.createDate", "runtime.host", "runtime.powerState",
             "runtime.connectionState", "datastore", "storage.perDatastoreUsage", "summary.storage.committed",
             "summary.config.vmPathName"]
    todo = list(found)
    for _ in range(4):                          # internal VMs named by others (a template or replica not named cp-)
        if not todo:
            break
        got = _collect(content, vim.VirtualMachine, props, [allvm[k]["_obj"] for k in todo], optional=("config.createDate",))
        detail.update(got)
        todo = []
        for k in got:
            for o in got[k].get("config.extraConfig") or []:
                if str(o.key).lower() in REF_KEYS:
                    for u in uuids_in(o.value):
                        ref = by_uuid.get(u)
                        if ref and ref not in found:
                            found[ref] = ["referenced"]
                            todo.append(ref)
    # a golden image VM is never an internal VM, even when a setting names it
    masters = set(u for d in detail.values() for o in d.get("config.extraConfig") or []
                  if str(o.key).lower() == MASTER_KEY for u in uuids_in(o.value))
    for k in [k for k, why in found.items() if why == ["referenced"]]:
        if str(allvm[k].get("config.instanceUuid") or "").lower() in masters:
            del found[k]
    hosts = _collect(content, vim.HostSystem, ["name", "runtime.inMaintenanceMode", "parent"]) if found else {}
    clusters = _collect(content, vim.ClusterComputeResource, ["name"]) if found else {}
    dss = _collect(content, vim.Datastore, ["name"]) if found else {}

    out, golden_uuids = [], []
    for k in sorted(found, key=lambda x: str(allvm[x].get("name", "")).lower()):
        d = detail.get(k, {})
        cp = {}
        for o in d.get("config.extraConfig") or []:
            if str(o.key).lower().startswith("cloneprep."):
                cp[str(o.key).lower()] = str(o.value if o.value is not None else "")
        for u in uuids_in(cp.get(MASTER_KEY, "")):
            if u not in golden_uuids:
                golden_uuids.append(u)
        h = d.get("runtime.host")
        hp = hosts.get(h._moId, {}) if h is not None else {}
        hpar = hp.get("parent")
        used = [int(x.committed or 0) for x in d.get("storage.perDatastoreUsage") or []]
        committed = sum(used) if used else int(d.get("summary.storage.committed") or 0)
        created, ts = _iso(d.get("config.createDate"))
        fk = folder_of(allvm[k])
        out.append({"vcenter": vcenter, "moid": k, "name": str(allvm[k].get("name") or ""),
                    "uuid": str(allvm[k].get("config.instanceUuid") or "").lower(), "bios_uuid": str(d.get("config.uuid") or "").lower(),
                    "folder": _path(fk, parents, names, root), "folder_name": names.get(fk, ""), "found_by": found[k],
                    "power": str(d.get("runtime.powerState") or ""), "connection": str(d.get("runtime.connectionState") or ""),
                    "host": hp.get("name", ""), "host_maintenance": bool(hp.get("runtime.inMaintenanceMode")),
                    "cluster": clusters.get(hpar._moId, {}).get("name", "") if hpar is not None else "",
                    "datastores": sorted(dss.get(x._moId, {}).get("name", x._moId) for x in d.get("datastore") or []),
                    "committed_gb": round(committed / GB, 2), "created": created,
                    "age_hours": round(max(now - ts, 0) / 3600.0, 1) if ts else None,
                    "vmx": str(d.get("summary.config.vmPathName") or ""), "cloneprep": cp})

    # the golden image VMs: by the instance UUID an internal VM names, and by the IDs Horizon gives
    gold = [by_uuid[u] for u in golden_uuids if u in by_uuid and by_uuid[u] not in found]
    gold += [i for i in p["golden_ids"] if i in allvm and i not in found and i not in gold]
    snaps = _collect(content, vim.VirtualMachine, ["snapshot"], [allvm[k]["_obj"] for k in gold]) if gold else {}
    golden = []
    for k in gold:
        s = snaps.get(k, {}).get("snapshot")
        golden.append({"vcenter": vcenter, "moid": k, "uuid": str(allvm[k].get("config.instanceUuid") or "").lower(),
                       "name": str(allvm[k].get("name") or ""),
                       "path": _path(folder_of(allvm[k]), parents, names, root).rstrip("/") + "/" + str(allvm[k].get("name") or ""),
                       "snapshots": snapshot_paths(_tree(s.rootSnapshotList)) if s is not None else []})
    return {"internal": out, "golden": golden}


def main():
    module = AnsibleModule(
        argument_spec=dict(vcenters=dict(type="list", elements="str", default=[]),
                           golden_ids=dict(type="list", elements="str", default=[]),
                           validate_certs=dict(type="bool", default=True), now=dict(type="float")),
        supports_check_mode=True,
    )
    if not HAS_PYVMOMI:
        module.fail_json(msg="pyVmomi is not installed in the execution environment (the vmware.vmware collection needs it too)")
    p = module.params
    p["golden_ids"] = [str(x).strip() for x in p["golden_ids"] or [] if str(x).strip()]
    now = p["now"] or time.time()
    done, statuses = each_vcenter(module, lambda si, content, name: _read(content, p, now, name), p["vcenters"],
                                  p["validate_certs"], "the Horizon internal VMs")
    module.exit_json(changed=False, internal=[x for _, part in done for x in part["internal"]],
                     golden=[x for _, part in done for x in part["golden"]], vcenters=statuses)


if __name__ == "__main__":
    main()
