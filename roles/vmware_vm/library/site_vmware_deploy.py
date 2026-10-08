#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Deploy one VM from a vCenter template: clone, set CPU and memory, guest customization (host
name, DHCP), power on, and wait for the customization result and an IP address. pyVmomi only (the
vmware.vmware collection needs it anyway), in steps, so each one can be checked and reported."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vmware_deploy
short_description: Deploy a VM from a template (clone, size, customize, power on)
description:
  - Finds the template in the credential's vCenter and the ones in vcenters (one match, or name the
    vCenter), refuses when a VM with the name already exists in any of them, then clones it powered
    off, sets CPU, memory and notes, applies guest customization (Linux - host name, DNS domain,
    every network adapter on DHCP - or a customization specification from vCenter, with its
    computer name set to the host name), powers it on, and waits for vCenter's customization result
    and for an IPv4 address from VMware Tools.
  - Check mode resolves everything (template, cluster, datastore, folder) and builds nothing.
  - vCenter's address and account come from VMWARE_HOST, VMWARE_USER, VMWARE_PASSWORD (and
    VMWARE_PORT), as a "VMware vCenter" credential in AAP gives them.
options:
  name: {description: The new VM's name (also its host name unless hostname is set)., type: str, required: true}
  template: {description: The template's name in vCenter., type: str, required: true}
  vcenter: {description: The vCenter to deploy in (when the template is in several)., type: str, default: ''}
  vcenters: {description: More vCenters besides the credential's (same account)., type: list, elements: str, default: []}
  datacenter: {description: The datacenter; empty = the template's., type: str, default: ''}
  cluster: {description: The cluster (or a standalone host); empty = the template's., type: str, default: ''}
  datastore: {description: A datastore or datastore cluster (the datastore with the most free space); empty = the template's., type: str, default: ''}
  folder: {description: The VM folder, a path below the datacenter's VM folder (Linux/App); empty = the template's., type: str, default: ''}
  cluster_candidates: {description: With no cluster - choose among these (names; * and ?) the one with the most memory left with failover_hosts host(s) down, staying under mem_target_pct. Empty = the template's cluster., type: list, elements: str, default: []}
  datastore_candidates: {description: With no datastore - choose among these (names; * and ?) a shared, accessible datastore of the chosen cluster that stays under datastore_max_used_pct with the VM's full size. Empty = the template's datastore., type: list, elements: str, default: []}
  mem_target_pct: {description: Automatic placement - a cluster's memory used with failover_hosts host(s) down stays under this., type: float, default: 80}
  failover_hosts: {description: Automatic placement - hosts to plan as failed in a cluster., type: int, default: 1}
  datastore_max_used_pct: {description: Automatic placement - a datastore stays under this used % with the VM., type: float, default: 85}
  disks_gb: {description: "Disk sizes in GB: the first grows the template's first disk (never shrinks it), the others are added (thin). Empty = the template's disks.", type: list, elements: int, default: []}
  cpu: {description: vCPUs; 0 = as the template., type: int, default: 0}
  memory_mb: {description: Memory in MB; 0 = as the template., type: int, default: 0}
  annotation: {description: The VM's notes., type: str, default: ''}
  hostname: {description: The guest's host name; empty = name., type: str, default: ''}
  domain: {description: The DNS domain for Linux customization., type: str, default: localdomain}
  customization_spec: {description: A customization specification in vCenter (needed for Windows templates)., type: str, default: ''}
  customize: {description: Apply guest customization at all., type: bool, default: true}
  boot_from_network: {description: The template has no operating system and boots from the network (PXE, e.g. an MECM task sequence) - no guest customization, no waiting for an IP address; the network adapters' MAC addresses are returned (to import the computer in MECM)., type: bool, default: false}
  power_on: {description: Power the VM on., type: bool, default: true}
  wait_customization: {description: Seconds to wait for the customization result., type: int, default: 900}
  wait_ip: {description: Seconds to wait for an IPv4 address (0 = do not wait)., type: int, default: 600}
  task_timeout: {description: Seconds for the clone itself., type: int, default: 3600}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
author: site automation
'''

RETURN = r'''
vm:
  description: What was deployed (or, in check mode, would be).
  type: dict
  returned: always
  sample: {name: app01, moid: vm-1042, vcenter: vc01.example.mil, datacenter: DC1, cluster: CL1, host: esx03.example.mil,
           datastore: ds01, folder: /DC1/vm/Linux, template: rhel9-gold, cpu: 4, memory_gb: 16, power: poweredOn,
           customization: succeeded, hostname: app01, ip: 10.1.20.15, created: true}
notes:
  description: Things to know (a network adapter not connected at power on, no IP yet ...).
  type: list
  returned: always
'''

import fnmatch
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_vcenters import connect, disconnect, vcenter_list

try:
    from pyVmomi import vim, vmodl
    HAS_PYVMOMI = True
except ImportError:
    HAS_PYVMOMI = False


def fault_text(e):
    """A vSphere fault (or any error) as one line. A missing privilege is named: that is what to grant."""
    pid = getattr(e, "privilegeId", None)
    if pid:
        obj = getattr(e, "object", None)
        return "the vCenter account lacks the privilege %s%s" % (pid, (" on %s" % getattr(obj, "_moId", "")) if obj is not None else "")
    msg = getattr(e, "msg", None) or str(e)
    msg = " ".join(str(msg).split())
    name = type(e).__name__.split(".")[-1]
    return "%s: %s" % (name, msg) if msg and name not in msg else (msg or name)


def pick_ipv4(addresses):
    """The address to report: the first IPv4 that is not link-local or loopback ('' when none)."""
    for a in addresses or []:
        a = str(a)
        if a.count(".") == 3 and not a.startswith(("169.254.", "127.")):
            return a
    return ""


def _match(name, patterns):
    return any(fnmatch.fnmatchcase(str(name).lower(), str(p).strip().lower()) for p in patterns or [] if str(p).strip())


def rank_clusters(clusters, need_mem, target_pct=80.0, failover=1):
    """Automatic placement, pure: clusters [{name, hosts: [{mem, used, ok}]}] (bytes; ok = connected and
    not in maintenance) -> ranked [{name, pct_after, fits, why}], the most room first. A cluster's
    memory is its usable hosts' memory minus the biggest `failover` of them (a host down must not
    leave the cluster without room); pct_after = (used + need) / that."""
    out = []
    for c in clusters:
        hs = [h for h in c.get("hosts") or [] if h.get("ok")]
        fail = 0 if len(hs) <= 1 else max(0, int(failover))
        cap = sum(h["mem"] for h in hs) - sum(sorted((h["mem"] for h in hs), reverse=True)[:fail])
        used = sum(h["used"] for h in hs)
        if cap <= 0:
            out.append({"name": c["name"], "pct_after": None, "fits": False, "why": "no usable host (connected, out of maintenance)"})
            continue
        pct = round(100.0 * (used + need_mem) / cap, 1)
        out.append({"name": c["name"], "pct_after": pct, "fits": pct <= float(target_pct),
                    "why": "memory %s%% used with this VM%s (target %s%%)" % (pct, (" and %d host down" % fail) if fail else "", target_pct)})
    return sorted(out, key=lambda x: (not x["fits"], x["pct_after"] if x["pct_after"] is not None else 1e9))


def rank_datastores(datastores, need_bytes, max_pct=85.0):
    """Automatic placement, pure: datastores [{name, capacity, free, ok, shared}] -> ranked [{name,
    pct_after, fits, why}], the most room first. Only accessible, not in maintenance (ok), shared ones
    fit; pct_after counts the VM's full provisioned size (thin disks at their full size)."""
    out = []
    for d in datastores:
        cap, free = float(d.get("capacity") or 0), float(d.get("free") or 0)
        if not d.get("ok") or cap <= 0:
            out.append({"name": d["name"], "pct_after": None, "fits": False, "why": "not accessible, or in maintenance"})
            continue
        pct = round(100.0 * (cap - free + need_bytes) / cap, 1)
        fits = pct <= float(max_pct) and bool(d.get("shared"))
        out.append({"name": d["name"], "pct_after": pct, "fits": fits,
                    "why": "%s%% used with this VM (at most %s%%)%s" % (pct, max_pct, "" if d.get("shared") else ", not shared")})
    return sorted(out, key=lambda x: (not x["fits"], x["pct_after"] if x["pct_after"] is not None else 1e9))


def plan_disks(existing, wanted_gb):
    """existing [(key, kb, controller key, unit)] in order, wanted_gb [GB, ...] -> (grow (key, kb) or None,
    adds [(controller key, unit, kb)], problems). Disk 1 only grows; the added disks go on disk 1's
    controller, on free units (7 is the controller's own)."""
    problems, grow, adds = [], None, []
    if not wanted_gb:
        return None, [], []
    if not existing:
        return None, [], ["the template has no disk to grow"]
    key, kb, ctrl, _ = existing[0]
    want1 = int(wanted_gb[0]) * 1024 * 1024
    if want1 < kb:
        problems.append("disk 1 cannot shrink: the template's is %d GB, the request %d GB" % (kb // 1048576, int(wanted_gb[0])))
    elif want1 > kb:
        grow = (key, want1)
    used = {u for _, _, c, u in existing if c == ctrl}
    free = [u for u in range(16) if u != 7 and u not in used]
    for gb in wanted_gb[1:]:
        if not free:
            problems.append("no free unit on disk 1's controller for another disk")
            break
        adds.append((ctrl, free.pop(0), int(gb) * 1024 * 1024))
    return grow, adds, problems


def is_windows(guest_id):
    return str(guest_id or "").lower().startswith("win")


def _all(content, objtype, root=None):
    v = content.viewManager.CreateContainerView(root or content.rootFolder, [objtype], True)
    try:
        return list(v.view)
    finally:
        v.Destroy()


def _datacenter(obj):
    x = obj
    while x is not None and not isinstance(x, vim.Datacenter):
        x = getattr(x, "parent", None)
    return x


def _folder_path(f):
    names = []
    while f is not None and not isinstance(f, vim.Datacenter):
        names.append(f.name)
        f = getattr(f, "parent", None)
    return "/" + "/".join(([f.name] if f is not None else []) + list(reversed(names)))


def _wait(task, timeout, what):
    end = time.time() + timeout
    while task.info.state not in (vim.TaskInfo.State.success, vim.TaskInfo.State.error):
        if time.time() > end:
            raise RuntimeError("%s did not finish within %d s (it may still be running in vCenter)" % (what, timeout))
        time.sleep(2)
    if task.info.state == vim.TaskInfo.State.error:
        raise task.info.error
    return task.info.result


def _find_folder(dc, path):
    """A VM folder by path below the datacenter's VM folder: 'Linux/App', '/DC1/vm/Linux/App' or 'App'
    (a unique name anywhere below it)."""
    parts = [p for p in str(path).strip("/").split("/") if p]
    if len(parts) >= 2 and parts[0] == dc.name and parts[1] == "vm":
        parts = parts[2:]
    f = dc.vmFolder
    for p in parts:
        nxt = [c for c in f.childEntity if isinstance(c, vim.Folder) and c.name == p]
        if not nxt:
            return None
        f = nxt[0]
    return f


def main():
    module = AnsibleModule(
        argument_spec=dict(name=dict(type="str", required=True), template=dict(type="str", required=True),
                           vcenter=dict(type="str", default=""), vcenters=dict(type="list", elements="str", default=[]),
                           datacenter=dict(type="str", default=""), cluster=dict(type="str", default=""),
                           datastore=dict(type="str", default=""), folder=dict(type="str", default=""),
                           cpu=dict(type="int", default=0), memory_mb=dict(type="int", default=0),
                           annotation=dict(type="str", default=""), hostname=dict(type="str", default=""),
                           domain=dict(type="str", default="localdomain"), customization_spec=dict(type="str", default=""),
                           customize=dict(type="bool", default=True), power_on=dict(type="bool", default=True),
                           boot_from_network=dict(type="bool", default=False),
                           cluster_candidates=dict(type="list", elements="str", default=[]),
                           datastore_candidates=dict(type="list", elements="str", default=[]),
                           mem_target_pct=dict(type="float", default=80), failover_hosts=dict(type="int", default=1),
                           datastore_max_used_pct=dict(type="float", default=85),
                           disks_gb=dict(type="list", elements="int", default=[]),
                           wait_customization=dict(type="int", default=900), wait_ip=dict(type="int", default=600),
                           task_timeout=dict(type="int", default=3600), validate_certs=dict(type="bool", default=True)),
        supports_check_mode=True,
    )
    if not HAS_PYVMOMI:
        module.fail_json(msg="pyVmomi is not installed in the execution environment (the vmware.vmware collection needs it too)")
    p = module.params
    names = vcenter_list(p["vcenters"])
    if not names:
        module.fail_json(msg='No vCenter to talk to: attach a credential of type "VMware vCenter" to this job template.')
    want_vc = str(p["vcenter"] or "").strip()
    if want_vc and want_vc.lower() not in [n.lower() for n in names]:
        module.fail_json(msg="vcenter %s is not one of the vCenters of this job (%s): add it to vmware_vcenters" % (want_vc, ", ".join(names)))
    hostname = p["hostname"] or p["name"]
    vm = {"name": p["name"], "template": p["template"], "created": False}
    notes = []
    sessions = {}
    try:
        # 1. the name is free in every vCenter; the template is in exactly one (or the one named)
        templates = []
        for n in names:
            try:
                si = connect(n, p["validate_certs"])
            except RuntimeError as e:
                module.fail_json(msg="%s - the new VM's name could not be checked there, so nothing was built" % e, vm=vm, notes=notes)
            sessions[n] = si
            content = si.RetrieveContent()
            for v in _all(content, vim.VirtualMachine):
                try:
                    vname, is_t = v.name, bool(v.config and v.config.template)
                except vmodl.fault.ManagedObjectNotFound:
                    continue
                if vname == p["name"]:
                    module.fail_json(msg="a VM named %s already exists in vCenter %s (%s): nothing was built"
                                         % (p["name"], n, "a template" if is_t else _folder_path(v.parent)), vm=vm, notes=notes)
                if vname == p["template"] and is_t and (not want_vc or n.lower() == want_vc.lower()):
                    if not p["datacenter"] or _datacenter(v).name == p["datacenter"]:
                        templates.append((n, v))
        if not templates:
            module.fail_json(msg="no template named %s%s in %s" % (p["template"], (" in datacenter %s" % p["datacenter"]) if p["datacenter"] else "",
                                                                    want_vc or ", ".join(names)), vm=vm, notes=notes)
        if len(templates) > 1:
            module.fail_json(msg="template %s is in %s: name the vCenter (vcenter: ...) or the datacenter in the ticket"
                                 % (p["template"], ", ".join("%s / %s" % (n, _datacenter(t).name) for n, t in templates)), vm=vm, notes=notes)
        vc, tpl = templates[0]
        content = sessions[vc].RetrieveContent()
        dc = _datacenter(tpl)
        win = is_windows(tpl.config.guestId)
        vm.update(vcenter=vc, datacenter=dc.name)

        # 2. where it goes: cluster (resource pool), datastore, folder - the template's unless named
        # what the VM needs: memory, and on the datastore its full provisioned size (disks + swap)
        need_mem = (p["memory_mb"] or tpl.config.hardware.memoryMB) * 1048576
        tdisks = [d for d in tpl.config.hardware.device if isinstance(d, vim.vm.device.VirtualDisk)]
        existing = [(d.key, d.capacityInKB, d.controllerKey, d.unitNumber) for d in tdisks]
        grow, adds, dprob = plan_disks(existing, p["disks_gb"])
        if dprob:
            module.fail_json(msg="; ".join(dprob), vm=vm, notes=notes)
        disk_kb = sum(kb for _, kb, _, _ in existing) + ((grow[1] - existing[0][1]) if grow else 0) + sum(kb for _, _, kb in adds)
        need_disk = disk_kb * 1024 + need_mem
        vm["disks_gb"] = [(grow[1] if grow and i == 0 else kb) // 1048576 for i, (_, kb, _, _) in enumerate(existing)] + [kb // 1048576 for _, _, kb in adds]
        placement = {}
        if p["cluster"]:
            crs = [c for c in _all(content, vim.ComputeResource, dc.hostFolder) if c.name == p["cluster"]]
            if not crs:
                module.fail_json(msg="no cluster named %s in datacenter %s" % (p["cluster"], dc.name), vm=vm, notes=notes)
            cr = crs[0]
        elif p["cluster_candidates"]:
            cands = [c for c in _all(content, vim.ComputeResource, dc.hostFolder) if _match(c.name, p["cluster_candidates"])]
            info = [{"name": c.name, "obj": c, "hosts": [{"mem": h.summary.hardware.memorySize or 0,
                                                            "used": (h.summary.quickStats.overallMemoryUsage or 0) * 1048576,
                                                            "ok": str(h.runtime.connectionState) == "connected" and not h.runtime.inMaintenanceMode}
                                                           for h in (c.host or [])]} for c in cands]
            ranked = rank_clusters(info, need_mem, p["mem_target_pct"], p["failover_hosts"])
            placement["clusters"] = ["%s: %s%s" % (r["name"], r["why"], "" if r["fits"] else " - does not fit") for r in ranked[:5]]
            if not ranked or not ranked[0]["fits"]:
                module.fail_json(msg="no cluster among %s has room for %d GB more memory: %s" % (", ".join(p["cluster_candidates"]), need_mem // 1073741824,
                                 "; ".join(placement.get("clusters") or ["none matches in datacenter " + dc.name])), vm=vm, notes=notes)
            cr = [c["obj"] for c in info if c["name"] == ranked[0]["name"]][0]
            notes.append("cluster chosen: %s" % placement["clusters"][0])
        else:
            host = tpl.runtime.host
            cr = host.parent if host is not None else None
            if cr is None:
                module.fail_json(msg="the template %s has no host: name a cluster" % p["template"], vm=vm, notes=notes)
        pool = cr.resourcePool
        if p["datastore"]:
            dss = [d for d in _all(content, vim.Datastore, dc.datastoreFolder) if d.name == p["datastore"]]
            pods = [d for d in _all(content, vim.StoragePod, dc.datastoreFolder) if d.name == p["datastore"]]
            if dss:
                ds = dss[0]
            elif pods:
                kids = [d for d in pods[0].childEntity if isinstance(d, vim.Datastore) and d.summary.accessible]
                if not kids:
                    module.fail_json(msg="datastore cluster %s has no accessible datastore" % p["datastore"], vm=vm, notes=notes)
                ds = sorted(kids, key=lambda d: d.summary.freeSpace, reverse=True)[0]
                notes.append("datastore cluster %s: put on %s (the most free space)" % (p["datastore"], ds.name))
            else:
                module.fail_json(msg="no datastore or datastore cluster named %s in datacenter %s" % (p["datastore"], dc.name), vm=vm, notes=notes)
        elif p["datastore_candidates"]:
            mine = set(cr.host or [])
            cands = [d for d in _all(content, vim.Datastore, dc.datastoreFolder) if _match(d.name, p["datastore_candidates"])
                     and any(m.key in mine for m in (d.host or []))]
            info = [{"name": d.name, "obj": d, "capacity": d.summary.capacity, "free": d.summary.freeSpace,
                     "ok": bool(d.summary.accessible) and str(d.summary.maintenanceMode or "normal") == "normal",
                     "shared": bool(d.summary.multipleHostAccess) or len(d.host or []) >= 2 or len(mine) == 1} for d in cands]
            ranked = rank_datastores(info, need_disk, p["datastore_max_used_pct"])
            placement["datastores"] = ["%s: %s%s" % (r["name"], r["why"], "" if r["fits"] else " - does not fit") for r in ranked[:5]]
            if not ranked or not ranked[0]["fits"]:
                module.fail_json(msg="no datastore among %s of %s has room for %d GB: %s" % (", ".join(p["datastore_candidates"]), cr.name,
                                 need_disk // 1073741824, "; ".join(placement.get("datastores") or ["none matches, mounted by " + cr.name])),
                                 vm=vm, notes=notes)
            ds = [d["obj"] for d in info if d["name"] == ranked[0]["name"]][0]
            notes.append("datastore chosen: %s" % placement["datastores"][0])
        else:
            ds = tpl.datastore[0] if tpl.datastore else None
            if ds is None:
                module.fail_json(msg="the template %s has no datastore: name one" % p["template"], vm=vm, notes=notes)
        mounted = [m.key for m in (ds.host or [])]
        hosts = list(cr.host or [])
        if hosts and not any(h in mounted for h in hosts):
            module.fail_json(msg="datastore %s is not mounted on any host of %s" % (ds.name, cr.name), vm=vm, notes=notes)
        # a standalone host, or a cluster without DRS, needs the host named: a connected one, not in
        # maintenance, that mounts the datastore, with the most free memory
        target = None
        drs = isinstance(cr, vim.ClusterComputeResource) and bool(getattr(getattr(cr.configurationEx, "drsConfig", None), "enabled", False))
        if not drs:
            ok = [h for h in hosts if h in mounted and str(h.runtime.connectionState) == "connected" and not h.runtime.inMaintenanceMode]
            if not ok:
                module.fail_json(msg="no host of %s is connected, out of maintenance and mounts datastore %s" % (cr.name, ds.name), vm=vm, notes=notes)
            free = lambda h: (h.summary.hardware.memorySize or 0) - (h.summary.quickStats.overallMemoryUsage or 0) * 1024 * 1024
            target = sorted(ok, key=free, reverse=True)[0]
        if p["folder"]:
            folder = _find_folder(dc, p["folder"])
            if folder is None:
                cand = [f for f in _all(content, vim.Folder, dc.vmFolder) if f.name == str(p["folder"]).strip("/").split("/")[-1]]
                if len(cand) == 1:
                    folder = cand[0]
                else:
                    module.fail_json(msg="no VM folder %s in datacenter %s%s" % (p["folder"], dc.name,
                                     " (several have that name: give its path)" if cand else ""), vm=vm, notes=notes)
        else:
            folder = tpl.parent
        if placement:
            vm["placement"] = placement
        vm.update(cluster=cr.name, datastore=ds.name, folder=_folder_path(folder),
                  cpu=p["cpu"] or tpl.config.hardware.numCPU,
                  memory_gb=round((p["memory_mb"] or tpl.config.hardware.memoryMB) / 1024.0, 1), hostname=hostname)

        # 3. the guest customization, checked before anything is built
        nics = [d for d in tpl.config.hardware.device if isinstance(d, vim.vm.device.VirtualEthernetCard)]
        for d in nics:
            if d.connectable is not None and not d.connectable.startConnected:
                notes.append("the template's %s is not set to connect at power on: the VM will have no network until it is"
                             % d.deviceInfo.label)
        if not nics:
            notes.append("the template has no network adapter")
        cspec = None
        pxe = p["boot_from_network"]
        if pxe and len(hostname) > 15:
            module.fail_json(msg="Windows computer names (and MECM's) have 15 characters at most: %s is %d" % (hostname, len(hostname)),
                             vm=vm, notes=notes)
        if p["customize"] and not pxe:
            if p["customization_spec"]:
                try:
                    cspec = content.customizationSpecManager.GetCustomizationSpec(p["customization_spec"]).spec
                except vim.fault.NotFound:
                    module.fail_json(msg="no customization specification named %s in vCenter %s" % (p["customization_spec"], vc), vm=vm, notes=notes)
                ident = cspec.identity
                if isinstance(ident, vim.vm.customization.LinuxPrep):
                    ident.hostName = vim.vm.customization.FixedName(name=hostname)
                elif isinstance(ident, vim.vm.customization.Sysprep):
                    if len(hostname) > 15:
                        module.fail_json(msg="Windows computer names have 15 characters at most: %s is %d" % (hostname, len(hostname)), vm=vm, notes=notes)
                    ident.userData.computerName = vim.vm.customization.FixedName(name=hostname)
                vm["customization_spec"] = p["customization_spec"]
            elif win:
                module.fail_json(msg="template %s is Windows: name a customization specification (customization_spec: ...) - one from "
                                     "vCenter's Policies and Profiles, with the administrator password and domain or workgroup" % p["template"],
                                 vm=vm, notes=notes)
            else:
                cspec = vim.vm.customization.Specification(
                    identity=vim.vm.customization.LinuxPrep(hostName=vim.vm.customization.FixedName(name=hostname),
                                                            domain=p["domain"] or "localdomain", hwClockUTC=True),
                    globalIPSettings=vim.vm.customization.GlobalIPSettings(),
                    nicSettingMap=[vim.vm.customization.AdapterMapping(
                        adapter=vim.vm.customization.IPSettings(ip=vim.vm.customization.DhcpIpGenerator())) for _ in nics])
        vm["customization"] = "planned" if cspec is not None else ("none: boots from the network (PXE)" if pxe else "none")
        if module.check_mode:
            module.exit_json(changed=True, vm=vm, notes=notes)

        # 4. build: clone powered off, size, customize, power on
        spec = vim.vm.CloneSpec(location=vim.vm.RelocateSpec(pool=pool, datastore=ds, host=target), powerOn=False, template=False)
        new = _wait(tpl.CloneVM_Task(folder=folder, name=p["name"], spec=spec), p["task_timeout"], "the clone")
        vm.update(created=True, moid=new._moId)
        cfg = vim.vm.ConfigSpec()
        if p["cpu"]:
            cfg.numCPUs = p["cpu"]
        if p["memory_mb"]:
            cfg.memoryMB = p["memory_mb"]
        if p["annotation"]:
            cfg.annotation = p["annotation"]
        changes = []
        if grow or adds:
            ndisks = {d.key: d for d in new.config.hardware.device if isinstance(d, vim.vm.device.VirtualDisk)}
            if grow:
                d1 = ndisks.get(grow[0]) or sorted(ndisks.values(), key=lambda d: (d.controllerKey, d.unitNumber))[0]
                d1.capacityInKB = grow[1]
                d1.capacityInBytes = grow[1] * 1024
                changes.append(vim.vm.device.VirtualDeviceSpec(operation="edit", device=d1))
            for ctrl, unit, kb in adds:
                changes.append(vim.vm.device.VirtualDeviceSpec(operation="add", fileOperation="create", device=vim.vm.device.VirtualDisk(
                    controllerKey=ctrl, unitNumber=unit, capacityInKB=kb,
                    backing=vim.vm.device.VirtualDisk.FlatVer2BackingInfo(diskMode="persistent", thinProvisioned=True, fileName=""))))
            cfg.deviceChange = changes
            if grow and not pxe:
                notes.append("disk 1 was grown to %d GB: grow its partition and filesystem in the guest (Linux: growpart, then "
                             "xfs_growfs / resize2fs; Windows: Disk Management > Extend Volume)" % (grow[1] // 1048576))
            if adds:
                notes.append("%d disk(s) added (thin): %s GB - partition and format them in the guest" % (
                    len(adds), ", ".join(str(kb // 1048576) for _, _, kb in adds)))
        if p["cpu"] or p["memory_mb"] or p["annotation"] or changes:
            _wait(new.ReconfigVM_Task(spec=cfg), 600, "setting CPU, memory and disks")
        if cspec is not None:
            _wait(new.CustomizeVM_Task(spec=cspec), 600, "the guest customization")
            vm["customization"] = "applied (not yet run)"
        started = time.time()
        if p["power_on"]:
            _wait(new.PowerOnVM_Task(), 600, "power on")
        vm["host"] = new.runtime.host.name if new.runtime.host is not None else ""
        vm["power"] = str(new.runtime.powerState)
        vm["nics"] = [{"label": d.deviceInfo.label, "mac": d.macAddress or ""} for d in new.config.hardware.device
                      if isinstance(d, vim.vm.device.VirtualEthernetCard)]
        vm["mac"] = ", ".join(n["mac"] for n in vm["nics"] if n["mac"])
        if pxe:
            notes.append("booting from the network (PXE): the operating system comes from the deployment server (e.g. an MECM "
                         "task sequence) - MAC %s, computer name %s" % (vm["mac"] or "?", hostname))

        # 5. the result: vCenter's customization events, then an IPv4 address from VMware Tools
        if cspec is not None and p["power_on"]:
            vm["customization"] = "no result within %d s" % p["wait_customization"]
            end = started + p["wait_customization"]
            while time.time() < end:
                evs = content.eventManager.QueryEvents(vim.event.EventFilterSpec(
                    entity=vim.event.EventFilterSpec.ByEntity(entity=new, recursion="self"))) or []
                kinds = {type(e).__name__.split(".")[-1]: e for e in evs}
                if "CustomizationSucceeded" in kinds:
                    vm["customization"] = "succeeded"
                    break
                bad = [k for k in kinds if k.startswith("Customization") and k.endswith(("Failed", "FailedEvent"))]
                if bad:
                    e = kinds[bad[0]]
                    vm["customization"] = "FAILED (%s%s)" % (bad[0], (": " + e.fullFormattedMessage) if getattr(e, "fullFormattedMessage", "") else "")
                    break
                time.sleep(5)
            if vm["customization"].startswith("no result"):
                notes.append("vCenter reported no customization result: VMware Tools (open-vm-tools) must run in the template, and a "
                             "cloud-init template must allow VMware customization")
        if p["power_on"] and p["wait_ip"] > 0 and not pxe:
            end = time.time() + p["wait_ip"]
            ip = ""
            while time.time() < end:
                g = new.guest
                ip = pick_ipv4([a for n in (g.net or []) for a in (n.ipAddress or [])] + [g.ipAddress or ""])
                if ip:
                    break
                time.sleep(5)
            vm["ip"] = ip
            vm["guest_hostname"] = new.guest.hostName or ""
            if not ip:
                notes.append("no IPv4 address within %d s: check DHCP on the network, and that VMware Tools runs" % p["wait_ip"])
            if vm["guest_hostname"] and vm["guest_hostname"].split(".")[0].lower() != hostname.lower():
                notes.append("the guest reports host name %s, not %s: the customization did not apply it" % (vm["guest_hostname"], hostname))
        vm["power"] = str(new.runtime.powerState)
    except Exception as e:                      # noqa: BLE001
        module.fail_json(msg=("The VM %s was created, then: %s" % (p["name"], fault_text(e))) if vm.get("created")
                         else "Nothing was built: %s" % fault_text(e), vm=vm, notes=notes)
    finally:
        for si in sessions.values():
            disconnect(si)
    if vm["customization"].startswith("FAILED"):
        module.fail_json(msg="The VM %s was created and started, but its guest customization %s" % (p["name"], vm["customization"]),
                         changed=True, vm=vm, notes=notes)
    module.exit_json(changed=True, vm=vm, notes=notes)


if __name__ == "__main__":
    main()
