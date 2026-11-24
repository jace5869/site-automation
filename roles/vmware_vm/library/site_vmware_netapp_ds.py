#!/usr/bin/python
# -*- coding: utf-8 -*-
"""NetApp - expand a volume (1.123.0), the vCenter side: which VMware datastore sits on a NetApp LUN (by its NAA id,
naa.600a0980 + the LUN's serial) or on an NFS export (the volume's junction path), and - after the volume and LUN grew -
rescan the hosts that see the LUN and expand the VMFS datastore to the LUN's end, or refresh an NFS datastore."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vmware_netapp_ds
short_description: Find the VMware datastore on a NetApp LUN or NFS export; rescan and expand it
description:
  - action=map (read only) - for each key {naa, nfs_path, nfs_ips} the datastore on it, in every vCenter of the job
    (the credential's and vmware_vcenters): {datastore, kind (VMFS or NFS), vcenter, hosts}.
  - action=grow - for one key: rescans the storage of every host that sees the LUN (all HBAs, then VMFS), checks each sees
    the LUN at its new size (lun_bytes), and expands the VMFS datastore over the free space at the LUN's end
    (QueryVmfsDatastoreExpandOptions, ExpandVmfsDatastore); an NFS datastore is refreshed. In check mode nothing is done.
  - vCenter's address and account come from VMWARE_HOST, VMWARE_USER, VMWARE_PASSWORD (the VMware vCenter credential).
    action=grow needs Host.Config.Storage on the hosts and Datastore.Config on the datastore.
options:
  action: {description: map or grow., type: str, required: true, choices: [map, grow]}
  keys: {description: "[{naa, nfs_path, nfs_ips}] - map: every one; grow: the first.", type: list, elements: dict, required: true}
  lun_bytes: {description: grow - the LUN's new size; a host that sees it smaller after its rescan fails the step., type: int, default: 0}
  vcenters: {description: More vCenters to read (vmware_vcenters)., type: list, elements: str, default: []}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
author: site automation
'''

RETURN = r'''
found: {description: "map: one per key, in order - {datastore, kind, vcenter, hosts, capacity, free (bytes, the datastore's own)} or {} when none.", type: list, returned: always}
steps: {description: "grow: [{step, exactly, ok, result}].", type: list, returned: always}
vcenters: {description: The vCenters read and why one could not be., type: list, returned: always}
'''

import socket

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_ontap_expand import SLACK, human, serial_of

try:
    from pyVmomi import vim
    from ansible.module_utils.site_vcenters import Skip, connect, disconnect, each_vcenter    # noqa: F401
    HAS = True
except ImportError:                     # the unit tests' fakes need neither
    vim, HAS = None, False


def _view(content, kind):
    v = content.viewManager.CreateContainerView(content.rootFolder, [kind], True)
    try:
        return list(v.view)
    finally:
        try:
            v.Destroy()
        except Exception:               # noqa: BLE001
            pass


def lun_bytes(lun):
    cap = getattr(lun, "capacity", None)
    return int(cap.block) * int(cap.blockSize) if cap is not None else 0


def scan(content, host_kind, ds_kind):
    """the vCenter: {naa: [(host, its LUN)]}, {naa: datastore}, [(datastore, remote hosts, remote path)] of the NFS ones"""
    seen, vmfs, nfs = {}, {}, []
    for h in _view(content, host_kind):
        try:
            for l_ in h.config.storageDevice.scsiLun or []:
                n = str(getattr(l_, "canonicalName", "") or "").lower()
                if n.startswith("naa."):
                    seen.setdefault(n, []).append((h, l_))
        except AttributeError:          # a disconnected host has no config
            continue
    for ds in _view(content, ds_kind):
        info = ds.info
        if getattr(info, "vmfs", None) is not None:
            for e in info.vmfs.extent or []:
                vmfs.setdefault(str(e.diskName).lower(), ds)
        elif getattr(info, "nas", None) is not None:
            n = info.nas
            hosts = list(getattr(n, "remoteHostNames", None) or []) + ([n.remoteHost] if getattr(n, "remoteHost", None) else [])
            nfs.append((ds, hosts, str(n.remotePath or "")))
    return seen, vmfs, nfs


def same_path(a, b):
    return (a or "").rstrip("/") == (b or "").rstrip("/") and bool((a or "").strip("/"))


def addresses(names):
    """an NFS datastore's server names -> those names and their addresses (an SVM's data address may be given by name)"""
    out = set()
    for n in names or []:
        out.add(n)
        try:
            out.update(a[4][0] for a in socket.getaddrinfo(n, None))
        except (socket.error, UnicodeError):
            pass
    return out


def match(key, seen, vmfs, nfs, resolve=addresses, with_space=False):
    """one key -> {datastore, kind, hosts, ds (the object)} or {}. NFS: the export's path, and - when the SVM's addresses are
    known - one of them as the datastore's server: the same path on another SVM is never taken for it."""
    naa = str(key.get("naa") or "").lower()
    if naa:
        ds = vmfs.get(naa)
        hosts = [h for h, _l in seen.get(naa, [])]
        if not ds and not hosts:
            return {}
        return dict({"datastore": ds.name if ds else "", "kind": "VMFS" if ds else "LUN (no datastore)", "hosts": sorted(h.name for h in hosts),
                     "ds": ds, "serial": serial_of(naa)}, **(space(ds) if with_space else {}))
    path = key.get("nfs_path") or ""
    hits = [(ds, rh) for ds, rh, rp in nfs if same_path(rp, path)]
    ips = set(key.get("nfs_ips") or [])
    if not ips:                                     # the SVM's addresses unknown: the same path on another SVM cannot be told apart
        return {}
    hits = [x for x in hits if ips & resolve(x[1])]
    if len(hits) != 1:
        return {}
    ds = hits[0][0]
    return dict({"datastore": ds.name, "kind": "NFS", "hosts": sorted(getattr(m.key, "name", "") for m in ds.host or []), "ds": ds}, **(space(ds) if with_space else {}))


def space(ds):
    """a datastore's own capacity and free space (bytes; read only) - how full a LUN's datastore is, as VMware sees it.
    The map only (never on the grow path); any fault reading them: {} - the list then judges the LUN by its blocks written."""
    try:
        sm = getattr(ds, "summary", None) if ds is not None else None
        cap, free = getattr(sm, "capacity", None), getattr(sm, "freeSpace", None)
        if cap is None or free is None:
            return {}
        return {"capacity": int(cap), "free": int(free)}
    except Exception:  # pylint: disable=broad-except   # a vCenter fault on a property read, a value not a number
        return {}


def grow_vmfs(found, naa, want, expand=True, where="", scan=True, change=True):
    """rescan each host that sees the LUN, check the new size (scan), expand the datastore over this LUN's free space (change;
    expand=False: another vCenter expanded it - refresh it here) -> steps. main() scans every vCenter first, and changes
    only when every one passed."""
    steps, hosts = [], {}
    for h, _l in found["seen"]:
        hosts[h.name] = h
    bad, small = [], []
    for name in sorted(hosts) if scan else []:
        ss = hosts[name].configManager.storageSystem
        try:
            ss.RescanAllHba()
            ss.RescanVmfs()
        except Exception as e:                      # noqa: BLE001
            bad.append("%s: %s" % (name, getattr(e, "msg", "") or e))
            continue
        now = [lun_bytes(x) for x in ss.storageDeviceInfo.scsiLun or [] if str(x.canonicalName).lower() == naa]
        if not now or now[0] < want - SLACK:
            small.append("%s sees %s" % (name, human(now[0]) if now else "no such LUN"))
        elif now[0] > want + SLACK:                 # grown by someone else since: VMFS is not stretched over space this run did not add
            small.append("%s sees %s, more than this run made it (%s)" % (name, human(now[0]), human(want)))
    ok = bool(hosts) and not bad and not small
    if scan:
        steps.append({"step": "Rescan the hosts that see the LUN", "exactly": "%s on %d host(s): %s%s" % (naa, len(hosts), ", ".join(
        n.split(".")[0] for n in sorted(hosts)), where), "ok": ok, "result": ("Done - %d of %d hosts see %s" % (len(hosts), len(hosts), human(want)))
        if ok else "Failed - " + ("; ".join(bad + small) or "no connected host sees the LUN")})
    if not ok or not change:
        return steps
    ds = found["ds"]
    if ds is None:                                  # this vCenter only sees the LUN: its rescan was the whole job here
        return steps
    before = int(ds.summary.capacity or 0)
    mounted = [getattr(m.key, "name", "") for m in ds.host or []]
    via = ([n for n in sorted(hosts) if n in mounted] or sorted(hosts))[0]
    if not expand:
        try:
            ds.RefreshDatastore()
            ok, res = True, "Done - %s shows %s" % (ds.name, human(ds.summary.capacity))
        except Exception as e:                      # noqa: BLE001
            ok, res = False, "Failed - %s" % (getattr(e, "msg", "") or e)
        steps.append({"step": "Refresh the VMFS datastore", "exactly": "%s (expanded in another vCenter)%s" % (ds.name, where), "ok": ok, "result": res})
        return steps
    try:
        dss = hosts[via].configManager.datastoreSystem
        opts = [o for o in dss.QueryVmfsDatastoreExpandOptions(datastore=ds) or []
                if str(getattr(getattr(o.spec, "extent", None), "diskName", "") or "").lower() == naa]
        single = len(getattr(getattr(ds.info, "vmfs", None), "extent", None) or []) == 1
        if not opts and single and before >= want * 0.985:  # one extent, ~1 % less than its LUN: it already uses all of it
            res, ok = "Nothing to do - %s is %s, the whole LUN" % (ds.name, human(before)), True
        elif not opts:
            res, ok = "Failed - vCenter offers no way to expand it on %s (no free space after the datastore on this LUN)" % naa, False
        else:
            dss.ExpandVmfsDatastore(datastore=ds, spec=opts[0].spec)
            try:
                ds.RefreshDatastore()
            except Exception:                       # noqa: BLE001 - the capacity is read anyway
                pass
            after = int(ds.summary.capacity or 0)
            ok = after > before
            res = ("Done - %s is %s (was %s)" % (ds.name, human(after), human(before))) if ok else \
                "vCenter said done, but %s is still %s" % (ds.name, human(after))
    except Exception as e:                          # noqa: BLE001
        ok, res = False, "Failed - %s" % (getattr(e, "msg", "") or e)
    steps.append({"step": "Expand the VMFS datastore", "exactly": "%s to the LUN's end, through %s%s" % (ds.name, via.split(".")[0], where),
                  "ok": ok, "result": res})
    return steps


def grow_nfs(found):
    ds = found["ds"]
    try:
        ds.RefreshDatastoreStorageInfo()
        ok, res = True, "Done - %s shows %s" % (ds.name, human(ds.summary.capacity))
    except Exception as e:                          # noqa: BLE001
        ok, res = False, "Failed - %s" % (getattr(e, "msg", "") or e)
    return [{"step": "Refresh the NFS datastore in vCenter", "exactly": ds.name, "ok": ok, "result": res}]


def main():
    m = AnsibleModule(argument_spec=dict(action=dict(type="str", required=True, choices=["map", "grow"]),
                                         keys=dict(type="list", elements="dict", required=True), lun_bytes=dict(type="int", default=0),
                                         vcenters=dict(type="list", elements="str", default=[]), validate_certs=dict(type="bool", default=True)),
                      supports_check_mode=True)
    if not HAS:
        m.fail_json(msg="pyVmomi is not in this execution environment")
    p = m.params
    keys = p["keys"] if p["action"] == "map" else p["keys"][:1]
    grown = []                          # the datastore expanded in the first vCenter that has it; rescanned (and refreshed) in every one

    phase = ["scan"]                     # grow: every vCenter rescanned and checked first; the change only when all passed

    def read(si, content, name):
        seen, vmfs, nfs = scan(content, vim.HostSystem, vim.Datastore)
        out = []
        for k in keys:
            f = match(k, seen, vmfs, nfs, with_space=p["action"] == "map")      # the space: the list's only
            if f:
                f["vcenter"] = name
                f["seen"] = seen.get(str(k.get("naa") or "").lower(), [])
            out.append(f)
        if p["action"] == "grow" and out[0] and not m.check_mode:
            f = out[0]
            where = " (vCenter %s)" % name
            if keys[0].get("naa") and phase[0] == "scan":
                f["steps"] = grow_vmfs(f, str(keys[0]["naa"]).lower(), p["lun_bytes"], where=where, change=False)
            elif keys[0].get("naa"):
                f["steps"] = grow_vmfs(f, str(keys[0]["naa"]).lower(), p["lun_bytes"], expand=not (grown and f.get("ds")), where=where,
                                       scan=False)
                if f.get("ds"):
                    grown.append(name)
            elif phase[0] == "change":
                f["steps"] = grow_nfs(f)
        return [{k: v for k, v in f.items() if k not in ("ds", "seen")} for f in out]

    done, statuses = each_vcenter(m, read, extra=p["vcenters"], validate_certs=p["validate_certs"], what="the datastores")
    found, steps = [{} for _k in keys], []
    if p["action"] == "grow":
        steps += [{"step": "Read vCenter %s" % s["name"], "exactly": "the hosts that see the LUN there are not rescanned", "ok": False,
                   "result": "Failed - %s" % s["error"]} for s in statuses if s.get("error")]
    for _name, res in done:
        for i, f in enumerate(res):
            if f and i == 0:
                steps += f.pop("steps", [])
            if f and (not found[i] or (f.get("datastore") and not found[i].get("datastore"))):
                found[i] = f                # the vCenter that has the datastore, else the first that sees the LUN
    if p["action"] == "grow" and not m.check_mode and found[0] and all(s["ok"] for s in steps):
        phase[0] = "change"
        again, _st = each_vcenter(m, read, extra=p["vcenters"], validate_certs=p["validate_certs"], what="the datastores")
        for _name, res in again:
            if res and res[0]:
                steps += res[0].get("steps", [])
    m.exit_json(changed=any(s["ok"] for s in steps), found=found, steps=steps, vcenters=statuses)


if __name__ == "__main__":
    main()
