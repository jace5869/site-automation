#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Read what is down or hung right now in vCenter, for the VM outage watch (read-only). pyVmomi only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vmware_watch
short_description: Read what is down or hung right now in vCenter (read-only)
description:
  - Every host's connection state, maintenance mode and vSphere HA agent state; every datastore's
    accessibility; the VMs waiting on a question; the HA and storage (all paths down, permanent device
    loss) events of the last I(window_minutes).
  - The VMs that look hung - powered on, VMware Tools installed, up at least I(min_up_minutes), but
    no guest heartbeat (red), or Tools not running with a gray heartbeat. Only those suspects are
    probed from this node (ping, and a TCP connect to I(probe_port): auto = 22, or 3389 for a Windows
    guest) and read again I(confirm_checks) times, I(confirm_seconds) apart. A connection made or
    refused is an answer (the guest's network stack is alive); a timeout is no answer. A VM is hung
    only when BOTH signals - no heartbeat, no answer on the network - held in every check AND, with
    I(traffic), vCenter's real-time statistics show it sent fewer than I(traffic_packets) network
    packets since the first check (a hung guest sends none; one that sends is alive - a firewall
    between this node and the guest, not a hang). A VM with a task running in vCenter (snapshot,
    migration, clone ...) is not judged.
  - vCenter's address and account come from VMWARE_HOST, VMWARE_USER, VMWARE_PASSWORD (and
    VMWARE_PORT), as a "VMware vCenter" credential in AAP gives them; vcenters adds more vCenters
    (the same account). A read-only vCenter role is enough. Changes nothing.
options:
  datacenter: {description: Only this datacenter., type: str}
  vcenters: {description: More vCenters to read besides the credential's (same account; name or name:port)., type: list, elements: str, default: []}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
  window_minutes: {description: How far back to read HA and storage events, in minutes. 0 = no events., type: float, default: 15}
  min_up_minutes: {description: A VM powered on for less than this is not judged (it may still be starting)., type: float, default: 15}
  skip_vms: {description: VM names never judged (* and ? allowed, any case)., type: list, elements: str, default: []}
  skip_folders: {description: VM folder paths never judged (e.g. /DC1/vm/Lab*)., type: list, elements: str, default: []}
  probe: {description: Probe the suspects from this node (false = the heartbeat alone; nothing is then hung)., type: bool, default: true}
  probe_port: {description: "Also try a TCP connect to this port: auto = 22, 3389 for a Windows guest; a number; 0 = ping only.", type: str, default: auto}
  traffic: {description: "A hung verdict also needs vCenter's real-time statistics to show (almost) no network packets sent by the VM.", type: bool, default: true}
  traffic_packets: {description: Packets sent since the first check from which a VM is alive (not hung)., type: int, default: 1}
  probe_timeout: {description: Seconds per probe., type: float, default: 3}
  max_suspects: {description: Probe and confirm at most this many suspects per run (the rest are listed as not probed)., type: int, default: 50}
  confirm_checks: {description: How many times a suspect is checked in this run (1 = once, no waiting)., type: int, default: 2}
  confirm_seconds: {description: Seconds between those checks., type: float, default: 120}
  event_types: {description: The HA and storage event types read (vCenter's event type names)., type: list, elements: str}
  now: {description: The time to measure from (seconds since 1970; tests only)., type: float}
author: site automation
'''

RETURN = r'''
hosts:
  description: Every host, with its state.
  type: list
  returned: success
  sample: [{name: esx01.example.mil, moid: host-12, cluster: CL1, datacenter: DC1, vcenter: vc01.example.mil,
            connection: connected, maintenance: false, standby: none, ha_state: connectedToMaster}]
datastores:
  description: Every datastore, with whether it is accessible.
  type: list
  returned: success
questions:
  description: The VMs waiting on a question in vCenter.
  type: list
  returned: success
suspects:
  description: "The VMs that looked hung, each with every check and its verdict (hung, one-signal, unprobed, busy, recovered); traffic: what vCenter's statistics showed of a VM the probe found silent ({packets, minutes} or {note}), alive_by: why it is not hung after all."
  type: list
  returned: success
events:
  description: The HA and storage events of the window, newest first.
  type: list
  returned: success
'''

import datetime
import fnmatch
import socket
import subprocess
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_vcenters import Skip, connect, disconnect, each_vcenter

try:
    from pyVmomi import vim, vmodl
    HAS_PYVMOMI = True
except ImportError:
    HAS_PYVMOMI = False

EVENT_TYPES = [
    # vSphere HA
    "com.vmware.vc.ha.VmRestartedByHAEvent", "VmRestartedOnAlternateHostEvent", "VmFailoverFailed",
    "VmDasBeingResetEvent", "VmDasBeingResetWithScreenshotEvent", "DasHostFailedEvent", "DasHostIsolatedEvent",
    "com.vmware.vc.HA.DasHostFailedEvent", "com.vmware.vc.HA.DasHostIsolatedEvent",
    "com.vmware.vc.HA.ClusterFailoverActionInitiatedEvent",
    # storage: all paths down, permanent device loss, a VMFS datastore's heartbeat (and their clear events)
    "esx.problem.storage.apd.start", "esx.problem.storage.apd.timeout", "esx.clear.storage.apd.exit",
    "esx.problem.scsi.device.state.permanentloss", "esx.problem.scsi.device.state.permanentloss.noopens",
    "esx.clear.scsi.device.state.permanentloss.deviceonline",
    "esx.problem.vmfs.heartbeat.timedout", "esx.problem.vmfs.heartbeat.recovered"]
# the vCenter tasks during which a VM is not judged (it may stun or lose its network for a while)
BUSY_TASKS = ("snapshot", "consolidate", "migrate", "relocate", "clone", "vmotion", "upgradetools", "upgradevm",
              "reconfig", "poweron", "reboot", "reset", "shutdown", "standby", "suspend")
VM_PROPS = ["name", "config.template", "runtime.powerState", "runtime.connectionState", "runtime.host", "runtime.bootTime",
            "runtime.question", "guest.hostName", "guest.ipAddress", "guest.net", "guest.toolsStatus",
            "guest.toolsRunningStatus", "guestHeartbeatStatus", "config.guestFullName"]
RECHECK_PROPS = ["runtime.powerState", "runtime.bootTime", "guest.ipAddress", "guest.net", "guest.toolsStatus",
                 "guest.toolsRunningStatus", "guestHeartbeatStatus", "recentTask"]


def _iso(t):
    if t is None:
        return ""
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- the pure part (tests/test_vmware_modules.py) ---------------------------------------------------
def suspect_reason(v, min_up_minutes=15):
    """Why a VM looks hung by vCenter's own signals ('' = it does not): powered on, VMware Tools
    installed, up at least min_up_minutes (a VM still starting has no heartbeat yet), and no heartbeat
    (red) - or Tools not running and the heartbeat gray. A VM without Tools is never judged."""
    if v.get("power") != "poweredOn" or v.get("tools") in ("", "toolsNotInstalled"):
        return ""
    if v.get("up_minutes") is not None and float(v["up_minutes"]) < float(min_up_minutes):
        return ""
    hb = v.get("heartbeat") or ""
    not_running = v.get("tools") == "toolsNotRunning" or v.get("tools_running") == "guestToolsNotRunning"
    if hb == "red":
        return "no guest heartbeat (red)"
    if hb == "gray" and not_running:
        return "VMware Tools stopped answering (heartbeat gray)"
    return ""


def busy_task(tasks):
    """The first vCenter task running (or queued) on the VM that may stun it or cut its network:
    'snapshot (VirtualMachine.createSnapshot)', or '' when there is none. tasks: [{id, state}]."""
    for t in tasks or []:
        if str(t.get("state") or "") not in ("running", "queued"):
            continue
        tid = str(t.get("id") or "")
        low = tid.lower()
        for word in BUSY_TASKS:
            if word in low:
                return "%s (%s)" % (word, tid)
    return ""


def probe_candidates(v):
    """The addresses to probe a VM on, best first: its IP addresses as VMware Tools last reported them,
    then its guest host name and its VM name (looked up in DNS: Tools that stopped often leaves no IP)."""
    out = []
    for a in [v.get("ip")] + list(v.get("ips") or []) + [v.get("hostname"), v.get("name")]:
        a = str(a or "").strip()
        if a and a not in out and " " not in a and not a.startswith("fe80:"):
            out.append(a)
    return out


def verdict(checks):
    """One suspect's verdict from its checks (each {reason, busy, probe}), oldest first:
      busy       a task ran in vCenter during a check: not judged this run
      recovered  in one check it did not look hung any more (heartbeat back, restarted, powered off)
      hung       both signals in every check: no heartbeat AND no answer on the network
      one-signal no heartbeat in every check, but it answered on the network (Tools died, the OS did not)
      unprobed   no heartbeat in every check, but it could not be probed (no address, no probe here)"""
    if not checks:
        return "recovered"
    if any(c.get("busy") for c in checks):
        return "busy"
    if any(not c.get("reason") for c in checks):
        return "recovered"
    probes = [c.get("probe") or "" for c in checks]
    if all(p == "no answer" for p in probes):
        return "hung"
    if any(p == "answers" for p in probes):
        return "one-signal"
    return "unprobed"


def name_skipped(v, skip_vms, skip_folders):
    """Why a VM is never judged ('' = it is judged): a name in skip_vms or a folder in skip_folders."""
    n, f = str(v.get("name") or "").lower(), str(v.get("folder") or "").lower()
    if any(fnmatch.fnmatchcase(n, str(p).lower()) for p in skip_vms or []):
        return "vm_watch_skip_vms"
    if any(fnmatch.fnmatchcase(f, str(p).lower()) for p in skip_folders or []):
        return "vm_watch_skip_folders"
    return ""


# ---- the probe (from this node: the AAP execution environment) --------------------------------------
def _resolve(name):
    try:
        return sorted(set(i[4][0] for i in socket.getaddrinfo(name, None, proto=socket.IPPROTO_TCP)))
    except (OSError, UnicodeError):
        return []


def _ping(addr, timeout):
    """'answers', 'no answer', or 'unavailable' (no ping here, or not allowed to)."""
    cmd = ["ping", "-6" if ":" in addr else "-4", "-c", "1", "-W", str(max(1, int(round(timeout)))), addr]
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout + 5)
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    if r.returncode == 0:
        return "answers"
    if r.returncode == 1:
        return "no answer"
    return "unavailable"


def _tcp(addr, port, timeout):
    try:
        s = socket.create_connection((addr, port), timeout=timeout)
        s.close()
        return "answers"
    except ConnectionRefusedError:       # the guest's network stack said no: it is alive
        return "answers"
    except OSError:
        return "no answer"


def port_for(v, setting):
    """vm_watch_probe_port for this VM: auto = 3389 for a Windows guest, else 22; a number as it is; 0 = none."""
    if str(setting).strip().lower() in ("auto", ""):
        return 3389 if "windows" in str(v.get("guest_os") or "").lower() else 22
    try:
        return max(0, int(setting))
    except (TypeError, ValueError):
        return 0


def alive_by_traffic(traffic, min_packets=1):
    """vCenter's statistics of a VM the probe found silent -> why it is alive after all ('' = they do not show it):
    it sent min_packets or more since the first check. A hung guest sends nothing."""
    n = (traffic or {}).get("packets")
    if n is None or n < max(1, int(min_packets)):
        return ""
    return "it sent %d network packet%s in the last %s minutes (vCenter's real-time statistics)" % (
        n, "" if n == 1 else "s", ("%.1f" % traffic.get("minutes", 0)).rstrip("0").rstrip("."))


def probe(v, port=0, timeout=3.0):
    """Probe one VM -> {'probe': answers / no answer / unavailable / no address, 'address': what was probed,
    'how': ping / tcp:<port>}. Its addresses in order; the first one that answers decides."""
    tried, how = [], ["ping"] + (["tcp:%d" % port] if port else [])
    result = "no address"
    for cand in probe_candidates(v):
        addrs = [cand] if _is_ip(cand) else _resolve(cand)[:2]
        for a in addrs:
            if a in tried:
                continue
            tried.append(a)
            got = [_ping(a, timeout)] + ([_tcp(a, port, timeout)] if port else [])
            if "answers" in got:
                return {"probe": "answers", "address": a, "how": "/".join(how)}
            if "no answer" in got:
                result = "no answer"
            elif result == "no address":
                result = "unavailable"
        if len(tried) >= 3:
            break
    return {"probe": result, "address": ", ".join(tried), "how": "/".join(how)}


def _is_ip(s):
    for fam in (socket.AF_INET, socket.AF_INET6):
        try:
            socket.inet_pton(fam, s)
            return True
        except (OSError, ValueError):
            pass
    return False


# ---- vCenter ------------------------------------------------------------------------------------
def _props(content, root, objtype, paths):
    view = content.viewManager.CreateContainerView(root, [objtype], True)
    try:
        spec = vmodl.query.PropertyCollector.FilterSpec(
            objectSet=[vmodl.query.PropertyCollector.ObjectSpec(
                obj=view, skip=True,
                selectSet=[vmodl.query.PropertyCollector.TraversalSpec(name="v", path="view", skip=False, type=vim.view.ContainerView)])],
            propSet=[vmodl.query.PropertyCollector.PropertySpec(type=objtype, pathSet=paths)])
        return {o.obj._moId: (o.obj, {p.name: p.val for p in o.propSet}) for o in content.propertyCollector.RetrieveContents([spec]) or []}
    finally:
        view.Destroy()


def _objs_props(content, objs, objtype, paths):
    """{moid: {path: value}} of these objects (one round trip); an object gone since is left out."""
    if not objs:
        return {}
    spec = vmodl.query.PropertyCollector.FilterSpec(
        objectSet=[vmodl.query.PropertyCollector.ObjectSpec(obj=o) for o in objs],
        propSet=[vmodl.query.PropertyCollector.PropertySpec(type=objtype, pathSet=paths)])
    try:
        res = content.propertyCollector.RetrieveContents([spec]) or []
    except vmodl.fault.ManagedObjectNotFound:
        res = []
        for o in objs:
            try:
                res += content.propertyCollector.RetrieveContents([vmodl.query.PropertyCollector.FilterSpec(
                    objectSet=[vmodl.query.PropertyCollector.ObjectSpec(obj=o)],
                    propSet=[vmodl.query.PropertyCollector.PropertySpec(type=objtype, pathSet=paths)])]) or []
            except vmodl.fault.ManagedObjectNotFound:
                pass
    return {o.obj._moId: {p.name: p.val for p in o.propSet} for o in res}


def _datacenter_of(obj, cache):
    chain, x = [], obj
    while x is not None and x._moId not in cache and not isinstance(x, vim.Datacenter):
        chain.append(x._moId)
        try:
            x = x.parent
        except Exception:
            x = None
    name = cache.get(x._moId, "") if x is not None and x._moId in cache else (x.name if isinstance(x, vim.Datacenter) else "")
    for m in chain:
        cache[m] = name
    return name


def _folder(obj, cache):
    """'/DC1/vm/Linux' for a VM, from its parents (cached per parent folder)."""
    parent = getattr(obj, "parent", None)
    key = parent._moId if parent is not None else ""
    if key not in cache:
        names, x = [], parent
        while x is not None and not isinstance(x, vim.Datacenter):
            names.append(x.name)
            x = getattr(x, "parent", None)
        dc = x.name if x is not None else ""
        cache[key] = "/" + "/".join(([dc] if dc else []) + list(reversed(names)))
    return cache[key]


def _guest(pr, now):
    boot = pr.get("runtime.bootTime")
    if boot is not None and boot.tzinfo is None:
        boot = boot.replace(tzinfo=datetime.timezone.utc)
    ips = [pr.get("guest.ipAddress")] + [ip for n in pr.get("guest.net") or [] for ip in (getattr(n, "ipAddress", None) or [])]
    return {"power": str(pr.get("runtime.powerState") or ""), "ip": pr.get("guest.ipAddress") or "",
            "ips": sorted(set(str(i) for i in ips if i)), "tools": str(pr.get("guest.toolsStatus") or ""),
            "tools_running": str(pr.get("guest.toolsRunningStatus") or ""), "heartbeat": str(pr.get("guestHeartbeatStatus") or ""),
            "boot_time": _iso(boot) if boot else "", "up_minutes": int(max(now - boot.timestamp(), 0) // 60) if boot else None,
            "guest_os": str(pr.get("config.guestFullName") or "")}


def _tasks(content, refs):
    """[{id, state}] of these tasks (their descriptionId, e.g. VirtualMachine.createSnapshot)."""
    out = []
    got = _objs_props(content, refs, vim.Task, ["info.descriptionId", "info.state"])
    for m in got:
        out.append({"id": str(got[m].get("info.descriptionId") or ""), "state": str(got[m].get("info.state") or "")})
    return out


def _traffic(content, vms, since):
    """The network packets these VMs sent since `since`, from vCenter's real-time statistics (20-second samples,
    counter net.packetsTx.summation, all its adapters) -> ({moid: {packets, minutes}}, note)."""
    pm = content.perfManager
    cid = None
    for c in pm.perfCounter or []:
        if c.groupInfo.key == "net" and c.nameInfo.key == "packetsTx" and str(c.rollupType) == "summation":
            cid = c.key
            break
    if cid is None:
        return {}, "vCenter has no counter net.packetsTx.summation"
    specs = [vim.PerformanceManager.QuerySpec(entity=vim.VirtualMachine(v["moid"], content.rootFolder._stub), intervalId=20,
                                              startTime=since, metricId=[vim.PerformanceManager.MetricId(counterId=cid, instance="")])
             for v in vms]
    out = {}
    for em in pm.QueryPerf(querySpec=specs) or []:
        vals = [x for s in em.value or [] if s.id.instance == "" for x in s.value or [] if x is not None and x >= 0]
        if vals:
            out[em.entity._moId] = {"packets": int(sum(vals)), "minutes": round(len(em.sampleInfo or vals) * 20 / 60.0, 1)}
    return out, ""


def _events(content, root, begin, types, cap=2000):
    spec = vim.event.EventFilterSpec(time=vim.event.EventFilterSpec.ByTime(beginTime=begin), eventTypeId=list(types))
    if root is not None:
        spec.entity = vim.event.EventFilterSpec.ByEntity(entity=root, recursion="all")
    found = {}
    col = content.eventManager.CreateCollectorForEvents(spec)
    try:
        col.SetCollectorPageSize(500)
        batch = list(col.latestPage or [])
        while batch and len(found) < cap:
            for e in batch:
                found.setdefault(e.key, e)
            nxt = col.ReadPreviousEvents(500) or []
            if not nxt or all(e.key in found for e in nxt):
                break
            batch = nxt
    finally:
        try:
            col.DestroyCollector()
        except Exception:
            pass
    out = []
    for e in sorted(found.values(), key=lambda x: x.createdTime, reverse=True)[:cap]:
        args = {}
        for a in getattr(e, "arguments", None) or []:
            args[str(getattr(a, "key", ""))] = str(getattr(a, "value", ""))
        out.append({"key": e.key, "time": _iso(e.createdTime), "type": getattr(e, "eventTypeId", "") or type(e).__name__.split(".")[-1],
                    "message": " ".join(str(e.fullFormattedMessage or getattr(e, "message", "") or "").split()), "args": args,
                    "host": e.host.name if e.host else "", "vm": e.vm.name if e.vm else "",
                    "cluster": e.computeResource.name if e.computeResource else "",
                    "datacenter": e.datacenter.name if e.datacenter else ""})
    return out


def _read(content, p, now, vcenter):
    res = {"hosts": [], "datastores": [], "questions": [], "suspects": [], "events": [], "vms_checked": 0, "vms_skipped": 0,
           "vms_not_judged": []}
    root = content.rootFolder
    dcs = _props(content, root, vim.Datacenter, ["name"])
    if p["datacenter"] and p["datacenter"] not in [v[1].get("name") for v in dcs.values()]:
        raise Skip("vCenter %s has no datacenter named %s" % (vcenter, p["datacenter"]))
    dc_cache = {m: v[1].get("name", "") for m, v in dcs.items()}

    def in_dc(dc):
        return not p["datacenter"] or dc == p["datacenter"]

    clusters = {m: pr.get("name", "") for m, (o, pr) in _props(content, root, vim.ClusterComputeResource, ["name"]).items()}
    paths = ["name", "parent", "runtime.connectionState", "runtime.inMaintenanceMode", "runtime.standbyMode", "runtime.dasHostState"]
    try:
        hosts = _props(content, root, vim.HostSystem, paths)
    except vmodl.query.InvalidProperty:
        hosts = _props(content, root, vim.HostSystem, [x for x in paths if x not in ("runtime.standbyMode", "runtime.dasHostState")])
    host_of = {}
    for m, (o, pr) in hosts.items():
        parent = pr.get("parent")
        cl = clusters.get(parent._moId, "") if isinstance(parent, vim.ClusterComputeResource) else ""
        dc = _datacenter_of(parent, dc_cache) if parent is not None else ""
        das = pr.get("runtime.dasHostState")
        rec = {"name": pr.get("name", ""), "moid": m, "cluster": cl, "datacenter": dc, "vcenter": vcenter,
               "connection": str(pr.get("runtime.connectionState") or ""), "maintenance": bool(pr.get("runtime.inMaintenanceMode")),
               "standby": str(pr.get("runtime.standbyMode") or ""), "ha_state": str(getattr(das, "state", "") or "") if das is not None else ""}
        host_of[m] = rec
        if in_dc(dc):
            res["hosts"].append(rec)
    for m, (o, pr) in _props(content, root, vim.Datastore, ["name", "summary.accessible", "summary.maintenanceMode", "parent"]).items():
        dc = _datacenter_of(o, dc_cache)
        if in_dc(dc):
            res["datastores"].append({"name": pr.get("name", ""), "moid": m, "datacenter": dc, "vcenter": vcenter,
                                      "accessible": bool(pr.get("summary.accessible", True)),
                                      "maintenance": str(pr.get("summary.maintenanceMode") or "normal")})

    folders = {}
    vms = _props(content, root, vim.VirtualMachine, VM_PROPS)
    suspects = []
    for m, (o, pr) in vms.items():
        if pr.get("config.template"):
            continue
        h = host_of.get(getattr(pr.get("runtime.host"), "_moId", None), {})
        folder = _folder(o, folders)
        dc = h.get("datacenter") or (folder.split("/")[1] if folder.count("/") > 1 else "")
        if not in_dc(dc):
            continue
        v = {"name": pr.get("name", ""), "moid": m, "vcenter": vcenter, "datacenter": dc, "folder": folder,
             "cluster": h.get("cluster", ""), "esxi_host": h.get("name", ""), "hostname": pr.get("guest.hostName") or ""}
        v.update(_guest(pr, now))
        q = pr.get("runtime.question")
        if q is not None:
            res["questions"].append(dict(v, question=" ".join(str(getattr(q, "text", "") or "").split()), question_id=str(getattr(q, "id", "") or "")))
        if v["power"] != "poweredOn":
            continue
        skip = name_skipped(v, p["skip_vms"], p["skip_folders"])
        if skip:
            res["vms_skipped"] += 1
            continue
        res["vms_checked"] += 1
        conn = str(pr.get("runtime.connectionState") or "connected")
        if conn != "connected":                 # its host is not responding: the host's condition covers it
            continue
        reason = suspect_reason(v, p["min_up_minutes"])
        if reason:
            suspects.append((o, v, reason))
    # only the suspects: their running tasks (one round trip)
    tasks_of = _objs_props(content, [o for o, _, _ in suspects], vim.VirtualMachine, ["recentTask"])
    for o, v, reason in suspects:
        v["checks"] = [{"time": _iso(datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc)), "reason": reason,
                        "heartbeat": v["heartbeat"], "tools_running": v["tools_running"],
                        "busy": busy_task(_tasks(content, tasks_of.get(o._moId, {}).get("recentTask") or []))}]
        res["suspects"].append(v)

    if p["window_minutes"] and p["window_minutes"] > 0:
        begin = datetime.datetime.fromtimestamp(now - p["window_minutes"] * 60, tz=datetime.timezone.utc)
        ev_root = None
        if p["datacenter"]:
            ev_root = [o for m, (o, pr) in dcs.items() if pr.get("name") == p["datacenter"]][0]
        for e in _events(content, ev_root, begin, p["event_types"]):
            e["vcenter"] = vcenter
            res["events"].append(e)
    return res


def _recheck(content, vcenter, suspects, p, now):
    """Read the suspects of this vCenter again: heartbeat, Tools, boot time, running tasks."""
    mine = [v for v in suspects if v["vcenter"] == vcenter]
    if not mine:
        return {}
    got = _objs_props(content, [vim.VirtualMachine(v["moid"], content.rootFolder._stub) for v in mine], vim.VirtualMachine, RECHECK_PROPS)
    out = {}
    for v in mine:
        pr = got.get(v["moid"])
        t = _iso(datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc))
        if pr is None:
            out[v["moid"]] = {"time": t, "reason": "", "gone": True, "busy": "", "heartbeat": "", "tools_running": ""}
            continue
        g = _guest(pr, now)
        g.update(name=v["name"])
        out[v["moid"]] = {"time": t, "reason": suspect_reason(g, p["min_up_minutes"]), "heartbeat": g["heartbeat"],
                          "tools_running": g["tools_running"], "busy": busy_task(_tasks(content, pr.get("recentTask") or [])),
                          "ip": g["ip"], "ips": g["ips"]}
    return out


def main():
    module = AnsibleModule(
        argument_spec=dict(datacenter=dict(type="str"), validate_certs=dict(type="bool", default=True),
                           vcenters=dict(type="list", elements="str", default=[]),
                           window_minutes=dict(type="float", default=15), min_up_minutes=dict(type="float", default=15),
                           skip_vms=dict(type="list", elements="str", default=[]),
                           skip_folders=dict(type="list", elements="str", default=[]),
                           probe=dict(type="bool", default=True), probe_port=dict(type="str", default="auto"),
                           traffic=dict(type="bool", default=True), traffic_packets=dict(type="int", default=1),
                           probe_timeout=dict(type="float", default=3), max_suspects=dict(type="int", default=50),
                           confirm_checks=dict(type="int", default=2), confirm_seconds=dict(type="float", default=120),
                           event_types=dict(type="list", elements="str"), now=dict(type="float")),
        supports_check_mode=True,
    )
    if not HAS_PYVMOMI:
        module.fail_json(msg="pyVmomi is not installed in the execution environment (the vmware.vmware collection needs it too)")
    p = module.params
    if p["event_types"] is None:
        p["event_types"] = EVENT_TYPES
    checks = max(1, int(p["confirm_checks"]))
    now = p["now"] or time.time()
    started = time.time()
    done, statuses = each_vcenter(module, lambda si, content, name: _read(content, p, now, name), p["vcenters"],
                                  p["validate_certs"], "what is down or hung")
    res = {"vcenter": ", ".join(name for name, _ in done), "vcenters": statuses, "window_minutes": p["window_minutes"],
           "read_at": _iso(datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc)),
           "hosts": [], "datastores": [], "questions": [], "suspects": [], "events": [], "vms_checked": 0, "vms_skipped": 0,
           "not_probed": [], "probe": {"on": p["probe"], "port": str(p["probe_port"]), "timeout": p["probe_timeout"]},
           "confirm": {"checks": checks, "seconds": p["confirm_seconds"]}}
    for _, part in done:
        for k in ("hosts", "datastores", "questions", "suspects", "events"):
            res[k] += part[k]
        res["vms_checked"] += part["vms_checked"]
        res["vms_skipped"] += part["vms_skipped"]
    res["events"].sort(key=lambda x: x["time"], reverse=True)

    # the suspects not busy: probe them, and check them again (only them), confirm_seconds apart
    judged = [v for v in res["suspects"] if not v["checks"][0]["busy"]]
    over = judged[max(0, int(p["max_suspects"])):]
    judged = judged[:max(0, int(p["max_suspects"]))]
    for v in over:
        v["checks"][0]["probe"] = "not probed (over vm_watch_max_suspects)"
        res["not_probed"].append(v["name"])

    def probe_all(vs):
        for v in vs:
            c = v["checks"][-1]
            if not c.get("reason") or c.get("busy"):
                continue
            c.update(probe(v, port_for(v, p["probe_port"]), p["probe_timeout"]) if p["probe"]
                     else {"probe": "unavailable", "address": "", "how": "off"})
    probe_all(judged)
    for _ in range(checks - 1):
        live = [v for v in judged if v["checks"][-1].get("reason") and not v["checks"][-1].get("busy")]
        if not live:
            break
        time.sleep(max(0.0, float(p["confirm_seconds"])))
        t = now + (time.time() - started)
        again, unread = {}, {}
        for name in sorted(set(v["vcenter"] for v in live)):
            try:
                si = connect(name, p["validate_certs"])
            except RuntimeError as e:
                unread[name] = str(e)
                continue
            try:
                again.update(_recheck(si.RetrieveContent(), name, live, p, t))
            except Exception as e:              # noqa: BLE001
                unread[name] = "reading the suspects again failed: %s" % e
            finally:
                disconnect(si)
        for v in live:
            if v["vcenter"] in unread:           # cannot tell: not judged this run (never hung on half the evidence)
                v["checks"].append({"time": "", "reason": v["checks"][-1]["reason"], "busy": "vCenter could not be read again (%s)"
                                    % unread[v["vcenter"]]})
                continue
            c = again.get(v["moid"]) or {"time": "", "reason": "", "gone": True, "busy": ""}
            if c.get("ip") or c.get("ips"):
                v["ip"], v["ips"] = c.get("ip") or v["ip"], sorted(set(v["ips"]) | set(c.get("ips") or []))
            for k in ("ip", "ips"):
                c.pop(k, None)
            v["checks"].append(c)
        probe_all(live)
    for v in res["suspects"]:
        v["verdict"] = verdict(v["checks"]) if v["name"] not in res["not_probed"] else "unprobed"
    # the third signal, for the silent ones: did vCenter see the VM send anything since the first check?
    silent = [v for v in res["suspects"] if v["verdict"] == "hung"]
    if silent and p["traffic"]:
        since = datetime.datetime.fromtimestamp(now - 60, tz=datetime.timezone.utc)
        for name in sorted(set(v["vcenter"] for v in silent)):
            mine = [v for v in silent if v["vcenter"] == name]
            try:
                si = connect(name, p["validate_certs"])
            except RuntimeError as e:
                for v in mine:
                    v["traffic"] = {"packets": None, "note": "vCenter could not be read for its statistics: %s" % e}
                continue
            try:
                got, note = _traffic(si.RetrieveContent(), mine, since)
            except Exception as e:              # noqa: BLE001
                got, note = {}, "its statistics could not be read: %s" % e
            finally:
                disconnect(si)
            for v in mine:
                v["traffic"] = got.get(v["moid"]) or {"packets": None, "note": note or "vCenter has no real-time statistics of it"}
                why = alive_by_traffic(v["traffic"], p["traffic_packets"])
                if why:
                    v["verdict"], v["alive_by"] = "one-signal", why
    res["traffic"] = {"on": p["traffic"], "packets": p["traffic_packets"]}
    module.exit_json(changed=False, **res)


if __name__ == "__main__":
    main()
