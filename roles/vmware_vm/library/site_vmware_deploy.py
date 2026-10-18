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
  - The VM carries its ticket in its notes from the clone on ([site-automation ticket=NUMBER
    state=building]; state=built once it is up and its customization result is in, state=failed when
    that failed). Run again for the same ticket, a VM it built is left as it is, one it started and did
    not finish (cloned, not yet powered on) is finished where it is, one whose customization failed is
    refused - the job picks up where it stopped.
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
  existing_ok: {description: "An earlier run built this VM for the ticket (the ticket's own note says so): when a VM with the name exists and its notes name the ticket, it is reported as it is and nothing is built (a ticket of several VMs run again after one failed). Off = an existing name is refused.", type: bool, default: false}
  ticket: {description: "The ticket's number: written in the new VM's notes (the mark a run of the same ticket finds again); with existing_ok, the existing VM's notes must name it.", type: str, default: ''}
  resume: {description: "A VM with the name whose notes carry this ticket's mark: built (state=built) - reported as it is; started by an earlier run and not finished (state=building) - finished where it is; its customization failed (state=failed) - refused. false = only existing_ok (the ticket's note) leaves one as it is.", type: bool, default: true}
  folders_allowed: {description: "The folders a VM may go in (vm_deploy_folders; * and ?): the folder found is checked against them - a path below the VM folder, or a name for a pattern without /.", type: list, elements: str, default: []}
  datastore_exclude: {description: "Datastores never chosen for this VM (names; * and ?) - e.g. the ones kept for SQL servers.", type: list, elements: str, default: []}
  datastore_planned: {description: "GB already planned on datastores by the VMs before this one in the same run ({name: GB}): counted as used, so the VMs of one ticket spread out.", type: dict, default: {}}
  hostname: {description: The guest's host name; empty = name., type: str, default: ''}
  domain: {description: The DNS domain for Linux customization., type: str, default: localdomain}
  customization_spec: {description: A customization specification in vCenter (needed for Windows templates)., type: str, default: ''}
  customize: {description: Apply guest customization at all., type: bool, default: true}
  boot_from_network: {description: The template has no operating system yet and installs it itself - it boots from the network (PXE, e.g. an MECM task sequence) or from its CD / ISO (e.g. a kickstart) - no guest customization, no waiting for an IP address; the network adapters' MAC addresses are returned (to import the computer in MECM)., type: bool, default: false}
  guestinfo: {description: "Values for the guest to read, set as the VM's guestinfo.<key> settings (e.g. {hostname: app01}: a kickstart or a task sequence reads it with VMware Tools - vmtoolsd --cmd 'info-get guestinfo.hostname').", type: dict, default: {}}
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
           customization: succeeded, hostname: app01, ip: 203.0.113.15, created: true}
notes:
  description: Things to know (a network adapter not connected at power on, no IP yet ...).
  type: list
  returned: always
issues:
  description: The notes that mean something did not finish though the VM is built (no IP in time, no customization
    result, the guest's name not applied, no network at power on, guestinfo not set).
  type: list
  returned: success
'''

import fnmatch
import re
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_vcenters import connect, disconnect, vcenter_list

try:
    from pyVmomi import vim, vmodl
    HAS_PYVMOMI = True
except ImportError:
    HAS_PYVMOMI = False


# the privileges a deploy uses, as the vSphere Client shows them in a role (Administration > Roles)
PRIVILEGES = {
    "VirtualMachine.Provisioning.DeployTemplate": "Virtual machine > Provisioning > Deploy template",
    "VirtualMachine.Provisioning.Customize": "Virtual machine > Provisioning > Customize guest",
    "VirtualMachine.Provisioning.ReadCustSpecs": "Virtual machine > Provisioning > Read customization specifications",
    "VirtualMachine.Inventory.CreateFromExisting": "Virtual machine > Inventory > Create from existing",
    "VirtualMachine.Config.CPUCount": "Virtual machine > Change configuration > Change CPU count",
    "VirtualMachine.Config.Memory": "Virtual machine > Change configuration > Change memory",
    "VirtualMachine.Config.Annotation": "Virtual machine > Change configuration > Set annotation",
    "VirtualMachine.Config.AddNewDisk": "Virtual machine > Change configuration > Add new disk",
    "VirtualMachine.Config.DiskExtend": "Virtual machine > Change configuration > Extend virtual disk",
    "VirtualMachine.Config.AdvancedConfig": "Virtual machine > Change configuration > Advanced configuration",
    "VirtualMachine.Interact.PowerOn": "Virtual machine > Interaction > Power on",
    "Resource.AssignVMToPool": "Resource > Assign virtual machine to resource pool",
    "Datastore.AllocateSpace": "Datastore > Allocate space",
    "Network.Assign": "Network > Assign network",
    "Cryptographer.Clone": "Cryptographic operations > Clone",
    "Cryptographer.EncryptNew": "Cryptographic operations > Encrypt new",
    "Cryptographer.RegisterVM": "Cryptographic operations > Register VM",
    "Cryptographer.RegisterHost": "Cryptographic operations > Register host",
    "Cryptographer.AddDisk": "Cryptographic operations > Add disk",
}


def fault_text(e):
    """A vSphere fault (or any error) as one line. A missing privilege is named: that is what to grant -
    as the role editor shows it, on which object (by name), and for an encrypted template why."""
    pid = getattr(e, "privilegeId", None)
    if pid:
        obj = getattr(e, "object", None)
        where = ""
        if obj is not None:
            moid = getattr(obj, "_moId", "")
            try:
                name = obj.name
            except Exception:  # the fault's object may not be readable
                name = ""
            where = " on %s" % (("%s (%s)" % (name, moid)) if name and name != moid else moid)
        ui = PRIVILEGES.get(pid, "")
        moid = getattr(obj, "_moId", "") if obj is not None else ""
        why = (" - the template is encrypted (it has a vTPM, or encrypted disks): its deploy needs the Cryptographic operations "
               "privileges in docs/VMWARE.md" if pid.startswith("Cryptographer.") else
               # every role has System.View: lacking it means no permission on that object at all
               (" - the account has no role on that object at all (every role, Read-only too, includes this one): its permission "
                "is set on a parent without Propagate to children, or the object has its own (e.g. No access)"
                + ("; it is an ESXi host - the deploy reads the hosts of the cluster it builds in, and the host the template is "
                   "registered on: give the role on those clusters with Propagate to children ticked" if moid.startswith("host-") else ""))
               if pid in ("System.View", "System.Read") else "")
        return "the vCenter account lacks the privilege %s%s%s%s" % (pid, (" (%s)" % ui) if ui else "", where, why)
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


def _tnorm(name):
    """A template name as people type it: case, spaces and the spaces around a dash do not count."""
    return re.sub(r"\s*-\s*", "-", re.sub(r"\s+", " ", str(name or "").strip().lower()))


def _match(name, patterns):
    return any(fnmatch.fnmatchcase(str(name).lower(), str(p).strip().lower()) for p in patterns or [] if str(p).strip())


def find_cluster(want, clusters, hosts=(), vcenters=(), datacenter=""):
    """The cluster named `want` among the datacenter's, pure: clusters [names], hosts [(host name, its
    cluster's name)], vcenters [names] -> (the cluster's name or None, a note or the error). The exact
    name first; else the one written in other case (when only one is). Not found: what the name is
    instead (a vCenter, an ESXi host, the datacenter) and the clusters there are."""
    w = str(want or "").strip()
    if w in clusters:
        return w, ""
    same = sorted({c for c in clusters if c.lower() == w.lower()})
    if len(same) == 1:
        return same[0], "cluster %s is written %r in vCenter" % (w, same[0])
    def same_host(a, b):                       # a name, with or without its domain (and a vCenter's :port)
        a, b = str(a or "").strip().lower(), str(b or "").strip().lower()
        b = b.split(":")[0] if b.count(":") == 1 else b
        if not a or not b:
            return False
        named = not re.match(r"^[\d.:]+$", b)   # an IP address only as it is
        return a == b or (named and a.split(".")[0] == b.split(".")[0])
    lw = w.lower()
    why = ""
    if any(same_host(lw, v) for v in vcenters or []):
        why = "%s is a vCenter, not a cluster" % w
    elif lw == str(datacenter or "").lower():
        why = "%s is the datacenter, not a cluster" % w
    else:
        on = [c for h, c in hosts or [] if same_host(lw, h)]
        if on:
            why = "%s is an ESXi host%s, not a cluster" % (w, (" in cluster %s" % on[0]) if on[0] else "")
    there = ("the clusters there: %s" % ", ".join(sorted(clusters, key=str.lower))) if clusters else "it has no cluster"
    msg = "no cluster named %s in datacenter %s%s - %s. The job's output shows where the cluster came from: " \
          "'cluster: ...   (from ...)' (a ticket line, vm_deploy_defaults, vm_deploy_sites or vm_deploy_name_rules)" \
          % (w, datacenter, (": " + why) if why else "", there)
    return None, msg


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


def new_disks(adds):
    """The added disks, pure: adds [(controller key, unit, kb)] (plan_disks) -> [{key, controllerKey, unitNumber,
    capacityInKB}]. Each new device gets its own temporary key, negative (-101, -102 ...): vCenter refuses two
    new devices with the same key ('Cannot add multiple devices using the same device key') - left unset, every
    one is 0."""
    return [{"key": -101 - i, "controllerKey": ctrl, "unitNumber": unit, "capacityInKB": kb} for i, (ctrl, unit, kb) in enumerate(adds)]


MARK = re.compile(r"\[site-automation ticket=(\S+) state=(building|built|failed)\]")


def with_mark(annotation, ticket, state):
    """The VM's notes with the ticket's mark at the end (any earlier mark replaced)."""
    text = MARK.sub("", str(annotation or "")).rstrip()
    return ((text + "\n") if text else "") + "[site-automation ticket=%s state=%s]" % (ticket, state)


def read_mark(annotation, ticket):
    """What a VM's notes say about the ticket -> 'built' / 'building' / 'failed' (its mark: built and checked; cloned,
    not finished; built, its customization failed), 'legacy' (the notes an older release wrote: 'Deployed from
    TEMPLATE for NUMBER by ...' - never the number written anywhere else), '' (another ticket's, or none). The
    number exactly, never a longer one that starts with it."""
    t, a = str(ticket or "").strip().lower(), str(annotation or "")
    if not t:
        return ""
    for num, state in MARK.findall(a):
        if num.lower() == t:
            return state
    if MARK.search(a):
        return ""
    return "legacy" if re.match(r"deployed from \S.* for %s by " % re.escape(t), a.strip().lower()) else ""


def plan_resume(template_disks, vm_disks, wanted_gb):
    """A VM an earlier run cloned and did not finish: the disks still to do -> plan_disks' (grow, adds, problems)
    against the VM's own disks. The disks it has beyond the template's are the ones added already."""
    if not wanted_gb:
        return None, [], []
    done = max(0, len(vm_disks) - len(template_disks))
    return plan_disks(vm_disks, [wanted_gb[0]] + list(wanted_gb[1 + done:]))


def folder_ok(path, name, patterns):
    """The folder found is one of patterns (vm_deploy_folders): its path below the VM folder ('Linux/App'), or its
    name for a pattern without '/'. No patterns: any."""
    pats = [str(x).strip().strip("/") for x in patterns or [] if str(x).strip()]
    if not pats:
        return True
    rel = str(path or "").strip("/")
    return any(fnmatch.fnmatchcase(rel.lower(), x.lower()) or ("/" not in x and fnmatch.fnmatchcase(str(name).lower(), x.lower()))
               for x in pats)


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


def _existing(vc, v, template):
    """What a VM built earlier is, read from vCenter (the result of a run that builds nothing)."""
    host = v.runtime.host
    hw = v.config.hardware
    nics = [d for d in hw.device if isinstance(d, vim.vm.device.VirtualEthernetCard)]
    return {"name": v.name, "existing": True, "created": False, "template": template, "vcenter": vc, "datacenter": getattr(_datacenter(v), "name", ""),
            "cluster": host.parent.name if host is not None and host.parent is not None else "", "host": host.name if host is not None else "",
            "datastore": ", ".join(d.name for d in v.datastore or []), "folder": _folder_path(v.parent), "cpu": hw.numCPU,
            "memory_gb": round(hw.memoryMB / 1024.0, 1),
            "disks_gb": [d.capacityInKB // 1048576 for d in hw.device if isinstance(d, vim.vm.device.VirtualDisk)],
            "power": str(v.runtime.powerState), "mac": ", ".join(d.macAddress for d in nics if d.macAddress),
            "ip": pick_ipv4([a for n in (v.guest.net or []) for a in (n.ipAddress or [])] + [v.guest.ipAddress or ""]) if v.guest else "",
            "customization": "-"}


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
                           existing_ok=dict(type="bool", default=False), ticket=dict(type="str", default=""),
                           resume=dict(type="bool", default=True), folders_allowed=dict(type="list", elements="str", default=[]),
                           datastore_exclude=dict(type="list", elements="str", default=[]),
                           datastore_planned=dict(type="dict", default={}),
                           domain=dict(type="str", default="localdomain"), customization_spec=dict(type="str", default=""),
                           customize=dict(type="bool", default=True), power_on=dict(type="bool", default=True),
                           boot_from_network=dict(type="bool", default=False), guestinfo=dict(type="dict", default={}),
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
    issues = []                                 # the notes that mean something did not finish (the VM is built)

    def issue(text):
        notes.append(text)
        issues.append(text)
    sessions = {}
    again, again_vc = None, ""                  # a VM an earlier run of this ticket started and did not finish
    try:
        # 1. the name is free in every vCenter - or it is this ticket's VM (its mark); the template is in exactly one
        templates, loose = [], []
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
                st = read_mark(v.config.annotation, p["ticket"]) if vname == p["name"] and not is_t else ""
                on = st and str(v.runtime.powerState) == "poweredOn"
                if not p["resume"]:                 # off: only the ticket's own note says it was built (as before)
                    st = "built" if st and p["existing_ok"] else ""
                if st == "failed":
                    module.fail_json(msg="a VM named %s is in vCenter %s: an earlier run of this ticket built it, and its guest "
                                         "customization failed - check it (vCenter > the VM > Monitor > Events), then delete it "
                                         "and run the job again" % (p["name"], n), vm=vm, notes=notes)
                if st == "built" or (st == "legacy" and (p["existing_ok"] or on)):
                    module.exit_json(changed=False, vm=_existing(n, v, p["template"]),
                                     notes=["built by an earlier run for this ticket (%s): left as it is"
                                            % ("the VM's notes say so" if st == "built" else "the VM's notes name the ticket")], issues=[])
                if st in ("building", "legacy") and p["resume"]:
                    again, again_vc = v, n
                    continue
                if vname == p["name"]:
                    module.fail_json(msg="a VM named %s already exists in vCenter %s (%s): nothing was built"
                                         % (p["name"], n, "a template" if is_t else _folder_path(v.parent)), vm=vm, notes=notes)
                if is_t and _tnorm(vname) == _tnorm(p["template"]) and (not want_vc or n.lower() == want_vc.lower()):
                    if not p["datacenter"] or _datacenter(v).name == p["datacenter"]:
                        (templates if vname == p["template"] else loose).append((n, v))
        # the exact name first; else the one written in other case or spacing (when only one is)
        if not templates and len({t.name for _, t in loose}) == 1:
            templates = loose
            notes.append("template %s is written %r in vCenter" % (p["template"], loose[0][1].name))
        if not templates:
            module.fail_json(msg="no template named %s%s in %s" % (p["template"], (" in datacenter %s" % p["datacenter"]) if p["datacenter"] else "",
                                                                    want_vc or ", ".join(names)), vm=vm, notes=notes)
        if len(templates) > 1:
            module.fail_json(msg="template %s is in %s: name the vCenter (vcenter: ...) or the datacenter in the ticket"
                                 % (p["template"], ", ".join("%s / %s" % (n, _datacenter(t).name) for n, t in templates)), vm=vm, notes=notes)
        vc, tpl = templates[0]
        if again is not None and again_vc.lower() != vc.lower():
            module.fail_json(msg="the VM %s an earlier run started for this ticket is in vCenter %s, its template in %s: nothing was done"
                                 % (p["name"], again_vc, vc), vm=vm, notes=notes)
        vm["template"] = tpl.name
        content = sessions[vc].RetrieveContent()
        dc = _datacenter(tpl)
        win = is_windows(tpl.config.guestId)
        vm.update(vcenter=vc, datacenter=dc.name)

        # 2. where it goes: cluster (resource pool), datastore, folder - the template's unless named
        # what the VM needs: memory, and on the datastore its full provisioned size (disks + swap)
        need_mem = (p["memory_mb"] or tpl.config.hardware.memoryMB) * 1048576
        tdisks = [d for d in tpl.config.hardware.device if isinstance(d, vim.vm.device.VirtualDisk)]
        existing = [(d.key, d.capacityInKB, d.controllerKey, d.unitNumber) for d in tdisks]
        if again is not None:
            vdisks = [(d.key, d.capacityInKB, d.controllerKey, d.unitNumber) for d in again.config.hardware.device
                      if isinstance(d, vim.vm.device.VirtualDisk)]
            grow, adds, dprob = plan_resume(existing, vdisks, p["disks_gb"])
            existing = vdisks
        else:
            grow, adds, dprob = plan_disks(existing, p["disks_gb"])
        if dprob:
            module.fail_json(msg="; ".join(dprob), vm=vm, notes=notes)
        disk_kb = sum(kb for _, kb, _, _ in existing) + ((grow[1] - existing[0][1]) if grow else 0) + sum(kb for _, _, kb in adds)
        need_disk = disk_kb * 1024 + need_mem
        vm["disks_gb"] = [(grow[1] if grow and i == 0 else kb) // 1048576 for i, (_, kb, _, _) in enumerate(existing)] + [kb // 1048576 for _, _, kb in adds]
        placement = {}
        target = None
        if again is not None:                   # an earlier run's VM: finished where it is
            host = again.runtime.host
            cr = host.parent if host is not None else None
            if cr is None or not again.datastore:
                module.fail_json(msg="the VM %s an earlier run started has no host or datastore in vCenter: delete it and run the job again"
                                     % p["name"], vm=vm, notes=notes)
            pool, ds, folder = again.resourcePool or cr.resourcePool, again.datastore[0], again.parent
            notes.append("picked up %s where an earlier run of this ticket stopped (it was cloned, not finished): finishing it in "
                         "place - cluster %s, datastore %s" % (p["name"], cr.name, ds.name))
            vm["resumed"] = True
        else:
            if p["cluster"]:
                allcr = _all(content, vim.ComputeResource, dc.hostFolder)
                found, why = find_cluster(p["cluster"], [c.name for c in allcr],
                                          [(h.name, c.name if isinstance(c, vim.ClusterComputeResource) else "") for c in allcr for h in (c.host or [])],
                                          names, dc.name)
                if not found:
                    module.fail_json(msg=why, vm=vm, notes=notes)
                if why:
                    notes.append(why)
                cr = [c for c in allcr if c.name == found][0]
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
            else:
                ds = tpl.datastore[0] if tpl.datastore else None
            excl = p["datastore_exclude"]
            if p["datastore"] and ds is not None and _match(ds.name, excl):
                module.fail_json(msg="datastore %s is kept for other VMs (vm_deploy_datastore_rules): %s may not go on it"
                                     % (ds.name, p["name"]), vm=vm, notes=notes)
            ds_cands = p["datastore_candidates"]
            if not p["datastore"] and not ds_cands and ds is not None and _match(ds.name, excl):
                # the template's own datastore is kept for other VMs (e.g. SQL servers): choose among the cluster's others
                ds_cands = ["*"]
                notes.append("the template's datastore %s is kept for other VMs (vm_deploy_datastore_rules): chose among %s's others"
                             % (ds.name, cr.name))
            if not p["datastore"] and not ds_cands and ds is not None and cr.host and not any(m.key in set(cr.host) for m in (ds.host or [])):
                # the template's datastore is not on the chosen cluster (a name rule or a site picked
                # another one): choose among that cluster's own datastores, by the same rules
                ds_cands = ["*"]
                notes.append("the template's datastore %s is not on %s: chose among %s's own datastores" % (ds.name, cr.name, cr.name))
            if not p["datastore"] and ds_cands:
                mine = set(cr.host or [])
                cands = [d for d in _all(content, vim.Datastore, dc.datastoreFolder) if _match(d.name, ds_cands)
                         and not _match(d.name, excl) and any(m.key in mine for m in (d.host or []))]
                # the space the VMs before this one in the same run take there counts as used: they spread out
                planned = dict((str(k).lower(), float(v or 0) * 1073741824) for k, v in (p["datastore_planned"] or {}).items())
                info = [{"name": d.name, "obj": d, "capacity": d.summary.capacity,
                         "free": (d.summary.freeSpace or 0) - planned.get(d.name.lower(), 0),
                         "ok": bool(d.summary.accessible) and str(d.summary.maintenanceMode or "normal") == "normal",
                         "shared": bool(d.summary.multipleHostAccess) or len(d.host or []) >= 2 or len(mine) == 1} for d in cands]
                ranked = rank_datastores(info, need_disk, p["datastore_max_used_pct"])
                placement["datastores"] = ["%s: %s%s" % (r["name"], r["why"], "" if r["fits"] else " - does not fit") for r in ranked[:5]]
                if not ranked or not ranked[0]["fits"]:
                    module.fail_json(msg="no datastore among %s%s of %s has room for %d GB: %s" % (
                                     ", ".join(ds_cands), (" (not %s)" % ", ".join(excl)) if excl else "", cr.name,
                                     need_disk // 1073741824, "; ".join(placement.get("datastores") or ["none matches, mounted by " + cr.name])),
                                     vm=vm, notes=notes)
                ds = [d["obj"] for d in info if d["name"] == ranked[0]["name"]][0]
                notes.append("datastore chosen: %s" % placement["datastores"][0])
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
                bare = "/" not in str(p["folder"]).strip("/")
                if folder is None and bare:             # a name alone: the one folder of that name anywhere below
                    cand = [f for f in _all(content, vim.Folder, dc.vmFolder) if f.name == str(p["folder"]).strip("/")]
                    if len(cand) == 1:
                        folder = cand[0]
                    else:
                        module.fail_json(msg="no VM folder %s in datacenter %s%s" % (p["folder"], dc.name,
                                         " (several have that name: give its path)" if cand else ""), vm=vm, notes=notes)
                elif folder is None:                    # a path is that path - never another folder of the same name
                    module.fail_json(msg="no VM folder %s in datacenter %s (a path is looked up as it is, from the VM folder down)"
                                         % (p["folder"], dc.name), vm=vm, notes=notes)
                rel = _folder_path(folder)[len("/%s/vm" % dc.name):].strip("/")
                if not folder_ok(rel, folder.name, p["folders_allowed"]):
                    module.fail_json(msg="VM folder %s (found for %s) is not one of vm_deploy_folders (%s): nothing was built"
                                         % (rel, p["folder"], ", ".join(p["folders_allowed"])), vm=vm, notes=notes)
            else:
                folder = tpl.parent
                rel = _folder_path(folder)[len("/%s/vm" % dc.name):].strip("/")
                if not folder_ok(rel, folder.name, p["folders_allowed"]):
                    module.fail_json(msg="no folder given, and the template's folder %s is not one of vm_deploy_folders (%s): give one - "
                                         "the ticket's folder: line, its site or vm_deploy_defaults" % (rel or "/", ", ".join(p["folders_allowed"])),
                                     vm=vm, notes=notes)
        if placement:
            vm["placement"] = placement
        vm["need_gb"] = 0 if again is not None else round(need_disk / 1073741824.0, 1)
        vm.update(cluster=cr.name, datastore=ds.name, folder=_folder_path(folder),
                  cpu=p["cpu"] or tpl.config.hardware.numCPU,
                  memory_gb=round((p["memory_mb"] or tpl.config.hardware.memoryMB) / 1024.0, 1), hostname=hostname)

        # 3. the guest customization, checked before anything is built
        nics = [d for d in tpl.config.hardware.device if isinstance(d, vim.vm.device.VirtualEthernetCard)]
        for d in nics:
            if d.connectable is not None and not d.connectable.startConnected:
                issue("the template's %s is not set to connect at power on: the VM will have no network until it is"
                      % d.deviceInfo.label)
        if not nics:
            issue("the template has no network adapter")
        cspec = None
        pxe = p["boot_from_network"]
        if pxe and win and len(hostname) > 15:
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
                # two different fixes: an empty template (MECM network boot) needs none, an installed Windows needs Sysprep
                empty = getattr(getattr(tpl.config, "tools", None), "toolsVersion", None) == 0
                try:
                    wspecs = sorted(i.name for i in (content.customizationSpecManager.info or []) if str(i.type).lower() == "windows")
                    have = ("Windows specifications in vCenter %s: %s" % (vc, ", ".join(wspecs))) if wspecs else \
                        ("vCenter %s has no Windows specification yet" % vc)
                except Exception:  # the account may not read them
                    have = "vCenter > Policies and Profiles > VM Customization Specifications"
                module.fail_json(msg="template %s is Windows and has no customization specification.%s If it is an empty template "
                                     "that installs Windows from the network (an MECM task sequence), list it in vm_deploy_pxe_templates "
                                     "on the deploy template: no customization then. If Windows is installed in it, it needs a Sysprep "
                                     "specification (administrator password, domain or workgroup): vm_deploy_template_specs on the deploy "
                                     "template, e.g. {\"%s\": SPEC-NAME}, or a ticket's customization_spec: line (%s)"
                                     % (p["template"], " VMware Tools never ran in it, so it looks empty." if empty else "",
                                        p["template"], have), vm=vm, notes=notes)
            else:
                cspec = vim.vm.customization.Specification(
                    identity=vim.vm.customization.LinuxPrep(hostName=vim.vm.customization.FixedName(name=hostname),
                                                            domain=p["domain"] or "localdomain", hwClockUTC=True),
                    globalIPSettings=vim.vm.customization.GlobalIPSettings(),
                    nicSettingMap=[vim.vm.customization.AdapterMapping(
                        adapter=vim.vm.customization.IPSettings(ip=vim.vm.customization.DhcpIpGenerator())) for _ in nics])
        vm["customization"] = "planned" if cspec is not None else ("none: the template installs its operating system (network boot or CD)" if pxe else "none")
        if module.check_mode:
            module.exit_json(changed=True, vm=vm, notes=notes, issues=issues)

        # 4. build: clone powered off (the ticket's mark in its notes from the start), size, customize, power on -
        # or finish the VM an earlier run of this ticket cloned and did not finish
        mark = (lambda state: with_mark(p["annotation"], p["ticket"], state)) if p["ticket"].strip() else (lambda state: p["annotation"])
        if again is None:
            spec = vim.vm.CloneSpec(location=vim.vm.RelocateSpec(pool=pool, datastore=ds, host=target), powerOn=False, template=False,
                                    config=vim.vm.ConfigSpec(annotation=mark("building")) if p["ticket"].strip() else None)
            new = _wait(tpl.CloneVM_Task(folder=folder, name=p["name"], spec=spec), p["task_timeout"], "the clone")
        else:
            new = again
        vm.update(created=True, moid=new._moId)
        info = {}
        if str(new.runtime.powerState) == "poweredOn":       # an earlier run got as far as the power on: its result is read
            notes.append("it was already powered on by the earlier run: its result is read as it is")
        else:
            cfg = vim.vm.ConfigSpec()
            if p["cpu"]:
                cfg.numCPUs = p["cpu"]
            if p["memory_mb"]:
                cfg.memoryMB = p["memory_mb"]
            if p["annotation"] or p["ticket"].strip():
                cfg.annotation = mark("building")
            changes = []
            if grow or adds:
                ndisks = {d.key: d for d in new.config.hardware.device if isinstance(d, vim.vm.device.VirtualDisk)}
                if grow:
                    d1 = ndisks.get(grow[0]) or sorted(ndisks.values(), key=lambda d: (d.controllerKey, d.unitNumber))[0]
                    d1.capacityInKB = grow[1]
                    d1.capacityInBytes = grow[1] * 1024
                    changes.append(vim.vm.device.VirtualDeviceSpec(operation="edit", device=d1))
                for nd in new_disks(adds):
                    changes.append(vim.vm.device.VirtualDeviceSpec(operation="add", fileOperation="create", device=vim.vm.device.VirtualDisk(
                        key=nd["key"], controllerKey=nd["controllerKey"], unitNumber=nd["unitNumber"], capacityInKB=nd["capacityInKB"],
                        backing=vim.vm.device.VirtualDisk.FlatVer2BackingInfo(diskMode="persistent", thinProvisioned=True, fileName=""))))
                cfg.deviceChange = changes
                if grow and not pxe:
                    notes.append("disk 1 was grown to %d GB: grow its partition and filesystem in the guest (Linux: growpart, then "
                                 "xfs_growfs / resize2fs; Windows: Disk Management > Extend Volume)" % (grow[1] // 1048576))
                if adds:
                    notes.append("%d disk(s) added (thin): %s GB - partition and format them in the guest" % (
                        len(adds), ", ".join(str(kb // 1048576) for _, _, kb in adds)))
            if p["cpu"] or p["memory_mb"] or p["annotation"] or p["ticket"].strip() or changes:
                _wait(new.ReconfigVM_Task(spec=cfg), 600, "setting CPU, memory and disks")
            # guestinfo on its own: a role without Advanced configuration loses only this, not the VM's size
            info = dict((str(k), str(v)) for k, v in (p["guestinfo"] or {}).items() if v not in (None, ""))
            if info:
                try:
                    _wait(new.ReconfigVM_Task(spec=vim.vm.ConfigSpec(extraConfig=[
                        vim.option.OptionValue(key="guestinfo." + k, value=v) for k, v in sorted(info.items())])), 600, "setting guestinfo")
                    vm["guestinfo"] = info
                except (vmodl.MethodFault, RuntimeError) as e:
                    info = {}
                    issue("guestinfo (the name for the installer) was not set: %s - grant the vCenter role Virtual machine > "
                          "Change configuration > Advanced configuration; the name is on the ticket" % fault_text(e))
            if cspec is not None:
                _wait(new.CustomizeVM_Task(spec=cspec), 600, "the guest customization")
                vm["customization"] = "applied (not yet run)"
        started = time.time()
        if p["power_on"] and str(new.runtime.powerState) != "poweredOn":
            _wait(new.PowerOnVM_Task(), 600, "power on")
        vm["host"] = new.runtime.host.name if new.runtime.host is not None else ""
        vm["power"] = str(new.runtime.powerState)
        vm["nics"] = [{"label": d.deviceInfo.label, "mac": d.macAddress or ""} for d in new.config.hardware.device
                      if isinstance(d, vim.vm.device.VirtualEthernetCard)]
        vm["mac"] = ", ".join(n["mac"] for n in vm["nics"] if n["mac"])
        if pxe:
            notes.append("no operating system yet: it installs from the network (e.g. an MECM task sequence) or its CD (e.g. a "
                         "kickstart) - MAC %s, computer name %s%s" % (vm["mac"] or "?", hostname,
                                                               " (in the VM's guestinfo.hostname)" if info.get("hostname") else ""))

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
                issue("vCenter reported no customization result: VMware Tools (open-vm-tools) must run in the template, and a "
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
                issue("no IPv4 address within %d s: check DHCP on the network, and that VMware Tools runs" % p["wait_ip"])
            if vm["guest_hostname"] and vm["guest_hostname"].split(".")[0].lower() != hostname.lower():
                issue("the guest reports host name %s, not %s: the customization did not apply it" % (vm["guest_hostname"], hostname))
        vm["power"] = str(new.runtime.powerState)
        # the result is known: built (a run of this ticket again leaves it) - or its customization failed (refused then)
        if p["ticket"].strip():
            done = "failed" if vm["customization"].startswith("FAILED") else "built"
            try:
                _wait(new.ReconfigVM_Task(spec=vim.vm.ConfigSpec(annotation=mark(done))), 600, "marking the VM " + done)
            except (vmodl.MethodFault, RuntimeError) as e:
                issue("the VM's notes could not be marked %s (%s): a run of this ticket again would finish it again" % (done, fault_text(e)))
    except Exception as e:                      # noqa: BLE001
        module.fail_json(msg=("The VM %s was created, then: %s" % (p["name"], fault_text(e))) if vm.get("created")
                         else "Nothing was built: %s" % fault_text(e), vm=vm, notes=notes)
    finally:
        for si in sessions.values():
            disconnect(si)
    if vm["customization"].startswith("FAILED"):
        module.fail_json(msg="The VM %s was created and started, but its guest customization %s" % (p["name"], vm["customization"]),
                         changed=True, vm=vm, notes=notes)
    module.exit_json(changed=True, vm=vm, notes=notes, issues=issues)


if __name__ == "__main__":
    main()
