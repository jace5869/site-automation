# -*- coding: utf-8 -*-
"""Filters for the VM outage watch (roles/vmware_vm/tasks/watch.yml). Input: what
roles/vmware_vm/library/site_vmware_watch.py returns. Output: the conditions found (vm_watch_conditions),
the report in the layout roles/site_email renders (vm_watch_report), the artifacts (vm_watch_artifact)
and the check results the ServiceNow tickets job reads (vm_watch_health).

What counts:
  critical  a host not responding or disconnected (not in maintenance mode); a host's HA agent isolated,
            partitioned, down or in error; a datastore not accessible; all paths down or a permanent device
            loss in the window that did not clear; HA could not restart a VM, or found a host failed or
            isolated; a VM waiting on a question; a VM hung (no heartbeat AND no answer on the network, in
            every check of the run); a VM vCenter shows as inaccessible, invalid or orphaned (1.112.4: whatever its
            power state; listed under its datastore)
  warning   a VM HA restarted or reset in the window; a storage problem of the window that cleared since;
            a VM with no heartbeat that still answers on the network, or could not be probed (one signal
            only: never called hung); a host whose HA agent is unreachable or failed to uninitialize
  info      (report only) a suspect skipped this run - a vCenter task was running on it, or it recovered
            during the run
Nothing here restarts anything. Phase 1 (watch only): the hung VMs that phase 2 WOULD restart are listed,
so false positives can be counted before any action is turned on."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import fnmatch
import re
import importlib.util
import os

# the times a report shows, in report_time_zone (1.54.0): plugins/module_utils/site_time.py, loaded as site_filters.py does
_spec = importlib.util.spec_from_file_location(
    "site_time", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "module_utils", "site_time.py"))
_tm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tm)

RANK = {"critical": 0, "warning": 1, "info": 2, "ok": 3, "": 4}
HA_CRITICAL = {"networkIsolated", "networkPartitionedFromMaster", "hostDown", "initializationError"}
HA_WARNING = {"fdmUnreachable", "uninitializationError"}
EVENT_SEVERITY = {
    "VmFailoverFailed": "critical", "DasHostFailedEvent": "critical", "DasHostIsolatedEvent": "critical",
    "com.vmware.vc.HA.DasHostFailedEvent": "critical", "com.vmware.vc.HA.DasHostIsolatedEvent": "critical",
    "com.vmware.vc.ha.VmRestartedByHAEvent": "warning", "VmRestartedOnAlternateHostEvent": "warning",
    "VmDasBeingResetEvent": "warning", "VmDasBeingResetWithScreenshotEvent": "warning",
    "com.vmware.vc.HA.ClusterFailoverActionInitiatedEvent": "warning"}
EVENT_LABEL = {
    "VmFailoverFailed": "HA could not restart the VM", "DasHostFailedEvent": "HA found the host failed",
    "DasHostIsolatedEvent": "HA found the host isolated", "com.vmware.vc.HA.DasHostFailedEvent": "HA found the host failed",
    "com.vmware.vc.HA.DasHostIsolatedEvent": "HA found the host isolated",
    "com.vmware.vc.ha.VmRestartedByHAEvent": "HA restarted the VM", "VmRestartedOnAlternateHostEvent": "HA restarted the VM on another host",
    "VmDasBeingResetEvent": "HA VM monitoring reset the VM (no heartbeat)",
    "VmDasBeingResetWithScreenshotEvent": "HA VM monitoring reset the VM (no heartbeat)",
    "com.vmware.vc.HA.ClusterFailoverActionInitiatedEvent": "HA started a failover in the cluster"}
# storage problems and the events that clear them
STORAGE = {"esx.problem.storage.apd.start": ("all paths down", ["esx.clear.storage.apd.exit"]),
           "esx.problem.storage.apd.timeout": ("all paths down, timed out (I/O to it now fails)", ["esx.clear.storage.apd.exit"]),
           "esx.problem.scsi.device.state.permanentloss": ("permanent device loss", ["esx.clear.scsi.device.state.permanentloss.deviceonline"]),
           "esx.problem.scsi.device.state.permanentloss.noopens": ("permanent device loss", ["esx.clear.scsi.device.state.permanentloss.deviceonline"]),
           "esx.problem.vmfs.heartbeat.timedout": ("VMFS heartbeat timed out", ["esx.problem.vmfs.heartbeat.recovered"])}
DEVICE_RE = re.compile(r"\b(naa\.[0-9a-fA-F]+\b|eui\.[0-9a-fA-F]+\b|t10\.\S+|mpx\.\S+)")


def _id(value):
    """A host name without its domain, in lower case; an IP address as it is (as site_filters.host_ids)."""
    s = str(value or "").strip().lower()
    if not s:
        return ""
    if re.fullmatch(r"[0-9.]+", s) or (":" in s and re.fullmatch(r"[0-9a-f:.]+", s)):
        return s
    return s.split(".")[0]


def _names(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [w for w in re.split(r"[\s,]+", value) if w]
    return [str(w).strip() for w in value if str(w).strip()]


def _matches(name, patterns):
    n = str(name or "").lower()
    return any(fnmatch.fnmatchcase(n, str(p).lower()) for p in _names(patterns))


def _device_raw(e):
    """The device as vCenter spells it (naa.600A0980...): the ticket id keeps this spelling (1.112.4c)."""
    for v in list((e.get("args") or {}).values()) + [e.get("message", "")]:
        m = DEVICE_RE.search(str(v or ""))
        if m:
            return m.group(1)
    return ""


def _device(e):
    """The device for matching: naa / eui in lower case (an extent and an event may differ in case)."""
    dev = _device_raw(e)
    return dev.lower() if dev.startswith(("naa.", "eui.")) else dev


def _cond(sev, kind, otype, obj, x, summary, todo, detail=""):
    return {"id": "vm-watch:%s:%s" % (kind, obj), "severity": sev, "kind": kind, "object_type": otype, "object": obj,
            "vcenter": x.get("vcenter", ""), "datacenter": x.get("datacenter", ""), "cluster": x.get("cluster", ""),
            "summary": summary, "detail": detail, "todo": todo}


LOST_WORD = {"inaccessible": "inaccessible in vCenter: its files cannot be reached",
             "invalid": "invalid in vCenter: its configuration cannot be read (its files cannot be reached)",
             "orphaned": "orphaned in vCenter: its host no longer has it registered"}


def _shown_names(names, most=8):
    names = [n for n in names if n]
    return ", ".join(names[:most]) + ((" and %d more" % (len(names) - most)) if len(names) > most else "")


def _inventory_id(kind, row):
    return "vm-watch:%s:%s:%s" % (kind, row["vcenter"], row["moid"])


def _ticket_id(c, d):
    """1.112.4: the id ServiceNow keys its incident on (site_correlation_id hashes it with the vCenter, the finding's "host").
    The ids tickets already carry stay as they were, so an open incident is not resolved and opened again at the upgrade:
    vm-watch:host:<name>, vm-watch:storage:<host>:<device>, vm-watch:datastore:<name> - the last one for path loss AND for
    not accessible (one incident as it worsens), with :<moid> only when two datastores of one vCenter share the name.
    New in 1.112.4, nothing open under them yet: vm-watch:lost:<vcenter>:<moid>, vm-watch:paths:<device>."""
    kind = c.get("kind")
    if kind == "host":
        return "vm-watch:host:%s" % c["object"]
    if kind == "storage":
        return "vm-watch:storage:%s:%s" % (c.get("host") or c["object"], c.get("device_raw") or c.get("device") or c.get("event_type") or "")
    if kind == "datastore" and c.get("object_type") == "datastore":
        return "vm-watch:datastore:%s" % c["object"]          # 1.112.4c: always by name; two of one name share it (merged)
    if kind == "datastore":
        return "vm-watch:paths:%s" % c["object"]
    return c["id"]


def _member(vm):
    return "%s [%s]" % (vm["name"], vm["moid"])


def _protected(v, o):
    """1.112.4: a VM of the protected list (vm_watch_actions' rule: the AAP server, vmware_protected_groups' hosts by name or
    address, vmware_protected_vms / vm_watch_protect patterns) - listed like any other, marked."""
    ids = set(_id(x) for x in [v.get("name"), v.get("hostname"), v.get("ip")] + list(v.get("ips") or []) if x)
    return bool(ids & set(_id(i) for i in o.get("protected_ids") or [] if i)) or _matches(v.get("name"), o.get("protected_names") or [])


def vm_watch_conditions(data, opts=None):
    """Everything the watch found, most severe first. opts: one_signal_severity (warning); protected_ids, protected_names
    (1.112.4: a lost VM of the protected list is marked protected)."""
    d, o = data or {}, opts or {}
    one = o.get("one_signal_severity") or "warning"
    lost = d.get("vms_lost") or []
    out = []
    for s in d.get("vcenters") or []:
        if s.get("error"):
            out.append(dict(_cond("critical", "vcenter", "vcenter", s.get("name", ""), {"vcenter": s.get("name", "")},
                                  "vCenter %s could not be read: %s" % (s.get("name", ""), s.get("error", "")),
                                  "Check that vCenter answers (its web client, its services in the appliance's management page on port 5480), "
                                  "and the VMware vCenter credential."), vcenter=s.get("name", "")))
    for h in d.get("hosts") or []:
        if h.get("maintenance") or h.get("standby") in ("in", "entering"):
            continue
        conn = h.get("connection") or ""
        if conn in ("notResponding", "disconnected"):
            vms = sorted(h.get("vms") or [], key=lambda v: (v["name"], v["moid"]))
            listed = "; %d VM%s potentially affected: %s" % (len(vms), "" if len(vms) == 1 else "s",
                                                              _shown_names([v["name"] for v in vms])) if vms else ""
            c = _cond("critical", "host", "host", h["name"], h,
                      "ESXi host %s is %s%s: its VMs may be down (vSphere HA restarts them on other hosts when it is on)%s"
                      % (h["name"], "not responding" if conn == "notResponding" else "disconnected",
                         " (cluster %s)" % h["cluster"] if h.get("cluster") else "", listed),
                      "Check the host's management network and its console (iDRAC / iLO): is it up? Reconnect it in the vSphere "
                      "Client (Connection > Connect) once it answers. Do not restart its VMs elsewhere by hand while HA is working.")
            if h.get("moid"):
                c.update(id=_inventory_id("host", h), moid=h["moid"])
            if vms:
                c["membership"] = "Potentially affected VMs: " + ", ".join(_member(v) for v in vms)
            out.append(c)
            continue
        ha = h.get("ha_state") or ""
        if ha in HA_CRITICAL or ha in HA_WARNING:
            out.append(_cond("critical" if ha in HA_CRITICAL else "warning", "ha", "host", h["name"], h,
                             "the vSphere HA agent of host %s is %s" % (h["name"], ha),
                             "Isolated or partitioned: check the host's management network (and the isolation address). "
                             "Otherwise: Reconfigure for vSphere HA on the host in the vSphere Client."))
    for ds in d.get("datastores") or []:
        if not ds.get("accessible", True) and ds.get("maintenance") in ("normal", "", None):
            c = _cond("critical", "datastore", "datastore", ds["name"], ds,
                      "datastore %s is not accessible: the VMs on it are hung or down" % ds["name"],
                      "Check the storage array and the paths (Storage > Devices > Paths on a host); rescan the storage adapters.")
            c["inaccessible"] = True
            c["members"] = list(ds.get("vms") or [])          # 1.112.4c: every VM with files on it (the read), whatever its guest
            if ds.get("moid"):
                c.update(id=_inventory_id("datastore", ds), moid=ds["moid"])
            out.append(c)
    # storage events: a problem with no clear event after it (same host, same device when both say) stays critical
    evs = sorted(d.get("events") or [], key=lambda e: (e.get("time", ""), e.get("key") or 0))      # oldest first; one second: by key
    seen = {}
    for i, e in enumerate(evs):
        if e.get("type") not in STORAGE:
            continue
        what, clears = STORAGE[e["type"]]
        dev = _device(e)
        cleared = next((c for c in evs[i + 1:] if c.get("type") in clears and c.get("vcenter") == e.get("vcenter")
                        and (c.get("host_moid") or c.get("host")) == (e.get("host_moid") or e.get("host"))
                        and (not dev or not _device(c) or _device(c) == dev)), None)
        host = e.get("host") or "?"
        c = _cond("warning" if cleared else "critical", "storage", "host", host, e,
                  "%s on host %s%s at %s%s" % (what, e.get("host") or "?", (" (device %s)" % dev) if dev else "", e.get("time", ""),
                                             (", cleared at %s" % cleared.get("time", "")) if cleared else ", not cleared"),
                  "Check the array and the SAN / storage network for the device; the VMs on its datastore may be hung.", e.get("message", ""))
        c.update(device=dev, device_raw=_device_raw(e), host=host, host_moid=e.get("host_moid") or "", since=e.get("time", ""), what=what,
                 event_type=e["type"])
        key = (c["vcenter"], c["host_moid"] or host, dev or e["type"])
        if c["host_moid"]:
            c["id"] = "vm-watch:storage:%s:%s:%s" % key
        else:
            c["id"] = "vm-watch:storage:%s:%s" % key[1:]
        prev = seen.get(key)
        if prev is None or RANK[c["severity"]] < RANK[prev["severity"]]:
            seen[key] = c
    out += _fold_paths(seen, out, d)
    seen = {}
    for e in evs:
        sev = EVENT_SEVERITY.get(e.get("type"))
        if not sev:
            continue
        obj, otype = (e.get("vm"), "vm") if e.get("vm") else (e.get("host") or e.get("cluster") or "?", "host" if e.get("host") else "cluster")
        c = _cond(sev, "event", otype, obj, e, "%s %s: %s (%s)" % ("VM" if otype == "vm" else otype, obj, EVENT_LABEL.get(e["type"], e["type"]),
                                                                    e.get("time", "")),
                  _event_todo(e["type"], otype), e.get("message", ""))
        c["id"] = "vm-watch:event:%s:%s" % (e["type"], obj)
        seen[c["id"]] = c                      # the newest of the window wins (evs is oldest first)
    out += list(seen.values())
    for q in d.get("questions") or []:
        out.append(_cond("critical", "question", "vm", q["name"], q,
                         "VM %s is waiting for an answer in vCenter: %s" % (q["name"], q.get("question") or "(no text)"),
                         "Answer it in the vSphere Client (the VM > Guest OS > Answer Question): until then the VM does not run."))
    # 1.112.4: a VM vCenter cannot reach (inaccessible, invalid, orphaned), whatever its power state - before, the power check
    # skipped it without a word. Never removed from the inventory: it comes back when its files can be reached
    for v in lost:
        conn = v.get("connection") or "inaccessible"
        dss = list(v.get("datastores") or [])
        c = _cond("info" if v.get("not_judged") else "critical", "lost", "vm", v.get("name", ""), v,
                  "VM %s is %s%s%s%s" % (v.get("name", ""), LOST_WORD.get(conn, conn),
                                         (" (datastore %s)" % ", ".join(dss)) if dss else "",
                                         (" - not judged (%s)" % v["not_judged"]) if v.get("not_judged") else "",
                                         " - protected" if _protected(v, o) else ""),
                  "Check the datastore (Storage in the vSphere Client: accessible?) and its paths (a host > Storage > Devices > "
                  "Paths); rescan the storage adapters. Do not remove the VM from the inventory: it comes back when its files "
                  "can be reached.", "power %s" % (v.get("power") or "unknown"))
        c["id"] = _inventory_id("lost", v)
        c.update({"vm": _vm_ids(v), "connection": conn, "datastores": dss, "protected": _protected(v, o),
                  "not_judged": v.get("not_judged") or ""})
        out.append(c)
    for v in d.get("suspects") or []:
        signals = _signals(v)
        if v.get("verdict") == "hung":
            c = _cond("critical", "hung", "vm", v["name"], v,
                      "VM %s is hung: %s" % (v["name"], signals),
                      "Open its console in the vSphere Client. Frozen or a crash screen: restart it (guest restart first, a reset "
                      "only if that does nothing). Note the time for its owner.")
        elif v.get("verdict") in ("one-signal", "unprobed"):
            c = _cond(one, "no-heartbeat", "vm", v["name"], v,
                      "VM %s has no guest heartbeat, but %s: not called hung (one signal only)"
                      % (v["name"], _alive_word(v) if v["verdict"] == "one-signal" else "it could not be probed (%s)" % _last_probe(v)),
                      "VMware Tools stopped or hung in a guest that still runs: restart the Tools service in the guest "
                      "(vmtoolsd / VMware Tools), or update Tools.")
        else:
            continue
        c.update({"vm": _vm_ids(v), "signals": signals, "detail": "checked %s" % _span(v), "datastores": list(v.get("datastores") or [])})
        out.append(c)
    out.sort(key=lambda c: (RANK.get(c["severity"], 9), not c.get("inaccessible"), c["kind"], c["object"]))
    # 1.112.4: the report and the tickets use the same root.
    roots = {(c["kind"], c["vcenter"], c["moid"]): c for c in out
             if c["kind"] in ("host", "datastore") and c.get("moid") and c["severity"] == "critical"}
    under = {}
    for c in out:
        matches = _vm_roots(c, roots)
        if matches:
            c["root_id"] = matches[0]["id"]           # one VM row, even with two down datastores
            for root in matches:
                under.setdefault(root["id"], []).append(c)
    grouped = []
    for c in out:
        if "root_id" in c:
            continue
        children = sorted(under.get(c["id"], []), key=lambda x: (RANK.get(x["severity"], 9), x["object"], x["vm"]["moid"]))
        # 1.112.4c: a VM on an inaccessible datastore with no finding of its own (no Tools, its host connected) is listed too
        known = set(x["vm"]["moid"] for x in children)
        quiet = sorted((v for v in c.get("members") or [] if v.get("moid") not in known), key=lambda v: (v["name"], v["moid"]))
        if children or quiet:
            names = (["%s (%s)" % (_member(x["vm"]), x.get("connection") or VM_WORD[x["kind"]]) for x in children]
                     + ["%s (potentially affected)" % _member(v) for v in quiet])
            listed = "%d VM%s: %s" % (len(names), "" if len(names) == 1 else "s", ", ".join(names))
            c["membership"] = ". ".join(filter(None, [c.get("membership"), listed]))
            mine = [x["object"] for x in children if x["kind"] == "lost"]
            if c.get("inaccessible") and mine:
                count = "%d VM%s" % (len(mine), "" if len(mine) == 1 else "s")
                if c.get("paths"):
                    c["summary"] += " - %s inaccessible: %s" % (count, _shown_names(sorted(mine)))
                else:
                    c["summary"] += "; vCenter shows %s on it as inaccessible: %s" % (count, _shown_names(sorted(mine)))
        if c.get("membership"):
            c["detail"] = ". ".join(filter(None, [c["detail"], c["membership"]]))
        grouped.append(c)
        grouped.extend(x for x in children if x["root_id"] == c["id"])
    return grouped


def _short(name):
    return str(name or "").split(".")[0]


def _fold_paths(seen, out, d):
    """1.112.4: fold a confirmed outage, or a shared device lost on at least two hosts."""
    datastores = {_inventory_id("datastore", ds): ds for ds in d.get("datastores") or [] if ds.get("moid")}
    extents = {}
    for ds_id, ds in datastores.items():
        for dev in ds.get("devices") or []:
            extents.setdefault((ds["vcenter"], dev.lower()), []).append(ds_id)
    inaccessible = {c["id"]: c for c in out if c.get("inaccessible")}
    devices = {}
    for key, c in seen.items():
        dev = c["device"]
        if dev and not dev.startswith("mpx."):
            devices.setdefault((c["vcenter"], dev), []).append(key)

    groups = {}
    for (vc, dev), keys in devices.items():
        matches = extents.get((vc, dev.lower()), [])
        if len(matches) > 1:
            continue
        group = matches[0] if matches else "vm-watch:paths:%s:%s" % (vc, dev)
        if group not in inaccessible:
            if not dev.startswith(("naa.", "eui.")):
                continue
            # 1.112.4: each extent must qualify before it joins a datastore ticket.
            active = [k for k in keys if seen[k]["severity"] == "critical"]
            if len({seen[k]["host_moid"] for k in active or keys} - {""}) < 2:
                continue
            keys = [k for k in keys if seen[k]["host_moid"]]
        groups.setdefault(group, []).extend(keys)

    left = dict(seen)
    folded = []
    for group, keys in groups.items():
        ds = datastores.get(group)
        root = inaccessible.get(group)
        cs = sorted((left.pop(k) for k in keys), key=lambda c: (c["since"], c["host"], c["device"]))
        active = [c for c in cs if c["severity"] == "critical"]
        first = (active or cs)[0]
        affected = cs if root is not None else (active or cs)
        hosts = {c["host_moid"] or c["host"]: c["host"] for c in affected}
        names = sorted(hosts.values())
        where = "on %d host%s (%s)" % (len(hosts), "" if len(hosts) == 1 else "s", ", ".join(_short(h) for h in names))
        if root is None:
            kind, name = ("datastore", ds["name"]) if ds else ("device", first["device"])
            head = "%s %s lost its paths %s" % (kind.title(), name, where)
            root = _cond("critical" if active else "warning", "datastore", kind, name, ds or first, "", first["todo"])
            root["id"] = group
            if ds:
                root["moid"] = ds["moid"]
            root["path_host_moids"] = sorted({c["host_moid"] for c in active})
            if len({c["device"] for c in cs}) == 1:
                root["device"] = first["device"]
            folded.append(root)
        else:
            head = "Datastore %s NOT ACCESSIBLE %s" % (ds["name"], where)
        paths = ["%s: %s" % (c["host"], c["summary"]) for c in cs]
        root["folded"] = root.get("folded", []) + cs           # 1.112.4c: each host's path ticket lives on (vm_watch_health)
        root.update(headline=head, hosts=names, paths=paths, since=first["since"], detail="Per host: " + "; ".join(paths),
                    summary="%s: %s since %s" % (head, first["what"], first["since"]))
        if not active and not root.get("inaccessible"):
            root["summary"] += ", cleared since on every host"
    return folded + list(left.values())


def _event_todo(type_id, otype):
    if type_id == "VmFailoverFailed":
        return "HA could not restart it: power it on by hand on a host with room, and check the cluster's HA admission control."
    if otype == "vm":
        return ("Check in the guest why it stopped (its own logs) and that its services are back; check the host it ran on "
                "(its condition here, its events in vCenter).")
    if "Failover" in type_id:
        return "HA is restarting VMs: find the failed or isolated host (its condition here) and check the VMs come back."
    return "Check the host's management network and its console (iDRAC / iLO); see the host's condition."


def _alive_word(v):
    """Why a VM with no heartbeat is not hung: it answered the probe, or - no answer to it (a firewall between AAP and
    the guest, most often) - vCenter saw it send network packets. No numbers: a ticket's summary stays the same."""
    if v.get("alive_by"):
        return "it still sends network traffic (vCenter's real-time statistics), though the probe got %s" % _last_probe(v)
    return "it answers on the network"


def _last_probe(v):
    c = (v.get("checks") or [{}])[-1]
    return "%s%s" % (c.get("probe") or "not probed", (" at " + c["address"]) if c.get("address") else "")


def _signals(v):
    """'no guest heartbeat (red); no answer at 192.0.2.20, in 2 check(s)' - no times: a ticket's summary stays the same."""
    cs = v.get("checks") or []
    first = cs[0] if cs else {}
    tr = v.get("traffic") or {}
    sent = "; vCenter saw no network packets sent" if tr.get("packets") == 0 else (
        "; vCenter's statistics not read (%s)" % tr["note"]) if tr.get("note") else ""
    return "%s; %s, in %d check(s)%s" % (first.get("reason") or "no heartbeat", _last_probe(v), len(cs), sent)


def _span(v):
    cs = v.get("checks") or []
    return ("%s to %s" % (cs[0].get("time", ""), cs[-1].get("time", ""))) if len(cs) > 1 else (cs[0].get("time", "") if cs else "")


def _vm_ids(v):
    return {"name": v.get("name", ""), "moid": v.get("moid", ""), "vcenter": v.get("vcenter", ""), "esxi_host": v.get("esxi_host", ""),
            "host_moid": v.get("host_moid", ""), "datastore_moids": v.get("datastore_moids") or [],
            "hostname": v.get("hostname", ""), "ips": sorted(set([v.get("ip", "")] + list(v.get("ips") or [])) - {""})}


def vm_watch_not_judged(data):
    """The suspects skipped this run (a task running on it, recovered during the run): report rows."""
    out = []
    for v in (data or {}).get("suspects") or []:
        if v.get("verdict") == "busy":
            busy = next((c.get("busy") for c in v.get("checks") or [] if c.get("busy")), "")
            why = busy if busy.startswith("vCenter could not") else "a vCenter task was running on it: %s" % busy
        elif v.get("verdict") == "recovered":
            why = "it looked hung (%s), then recovered during the run" % ((v.get("checks") or [{}])[0].get("reason") or "")
        else:
            continue
        out.append({"name": v.get("name", ""), "vcenter": v.get("vcenter", ""), "datacenter": v.get("datacenter", ""),
                    "esxi_host": v.get("esxi_host", ""), "why": why})
    return out


def vm_watch_actions(conditions, opts=None):
    """Phase 2's decision, made but NOT carried out: each hung VM -> would restart / protected (ticket only) /
    over vm_watch_max_actions. opts: protected_ids (the AAP server's and the protected groups' host names and
    IP addresses), protected_names (vmware_protected_vms + vm_watch_protect patterns), aap_known (the
    inventory says which server is AAP), max_actions.
    -> {'would_restart': [...], 'protected': [...], 'over_limit': [...]}, each {vm, vcenter, moid, esxi_host, why}"""
    o = opts or {}
    pids = set(_id(i) for i in o.get("protected_ids") or [] if i)
    names = o.get("protected_names") or []
    cap = int(o.get("max_actions", 5))
    out = {"would_restart": [], "protected": [], "over_limit": []}
    for c in conditions or []:
        if c.get("kind") != "hung":
            continue
        v = c.get("vm") or {}
        row = {"vm": v.get("name", ""), "vcenter": v.get("vcenter", ""), "moid": v.get("moid", ""), "esxi_host": v.get("esxi_host", ""),
               "why": "%s (%s)" % (c.get("signals", ""), c.get("detail", ""))}
        ids = set(_id(x) for x in [v.get("name"), v.get("hostname")] + list(v.get("ips") or []) if x)
        if not o.get("aap_known", True):
            out["protected"].append(dict(row, why="the inventory does not say which VM is the AAP server (group aap or "
                                                  "vmware_protected_vms): nothing would be restarted"))
        elif ids & pids:
            out["protected"].append(dict(row, why="the AAP server, or a host of vmware_protected_groups"))
        elif _matches(v.get("name"), names):
            out["protected"].append(dict(row, why="vmware_protected_vms / vm_watch_protect"))
        elif len(out["would_restart"]) >= cap:
            out["over_limit"].append(dict(row, why="over vm_watch_max_actions (%d): this many hung VMs at once means something "
                                                   "bigger - a host, the storage or the network" % cap))
        else:
            out["would_restart"].append(row)
    return out


def _where(c, multi):
    dc = c.get("datacenter") or ""
    if multi and c.get("vcenter"):
        dc = "%s / %s" % (str(c["vcenter"]).split(".")[0], dc) if dc else str(c["vcenter"]).split(".")[0]
    return " / ".join(x for x in (c.get("cluster"), dc) if x)


def _counts(d):
    return "%d host(s), %d datastore(s), %d powered-on VM(s)" % (len(d.get("hosts") or []), len(d.get("datastores") or []),
                                                                  int(d.get("vms_checked") or 0))


def vm_watch_clean_line(data, conditions=None):
    """The one line of a run with nothing to report."""
    d = data or {}
    vcs = [s.get("name") for s in d.get("vcenters") or [] if s.get("ok")]
    return "No outage: %s checked (vCenter%s %s)." % (_counts(d), "s" if len(vcs) > 1 else "", ", ".join(vcs) or d.get("vcenter", ""))


def vm_watch_report(data, conditions, actions=None, opts=None):
    """The report (roles/site_email layout): what is down or hung, what phase 2 would do, what was not judged."""
    d, cs, a, o = data or {}, conditions or [], actions or {}, opts or {}
    multi = len([s for s in d.get("vcenters") or [] if s.get("ok")]) > 1
    crit = [c for c in cs if c["severity"] == "critical"]
    warn = [c for c in cs if c["severity"] == "warning"]
    hung = [c for c in cs if c["kind"] == "hung"]
    title = "VM outage watch: %s" % (", ".join(x for x in ("%d critical" % len(crit) if crit else "", "%d warning" % len(warn) if warn else "") if x)
                                     or "nothing down")
    lead = next((c for c in cs if c["kind"] == "datastore" and c["severity"] == "critical"), None)
    if lead is not None:                         # 1.112.4: a lost datastore is the headline (the email's subject, the page's title)
        title = "%s - %s" % (lead.get("headline") or "Datastore %s NOT ACCESSIBLE" % lead["object"], title)
    status = "critical" if crit else "warning" if warn else "ok"
    conf = d.get("confirm") or {}
    pr = d.get("probe") or {}
    tr = d.get("traffic") or {}
    read = "%s UTC" % d.get("read_at", "")           # as vCenter gives it, 2026-10-23T10:02:00Z; in report_time_zone (1.54.0)
    tz = _tm.zone(o.get("time_zone"))[0]
    if tz is not None and _tm.local(str(d.get("read_at") or "")[:16], tz) != str(d.get("read_at") or "")[:16]:
        read = _tm.local(str(d.get("read_at"))[:16], tz)
    subtitle = ("Read %s: %s; HA and storage events of the last %s minutes. A VM is hung only when it has no heartbeat AND "
                "does not answer on the network, in each of %s check(s) %s seconds apart%s. Watch only: nothing was restarted."
                % (read, _counts(d), _num(d.get("window_minutes")), conf.get("checks", 1), _num(conf.get("seconds", 0)),
                   " AND vCenter saw it send no network packets" if tr.get("on") else ""))
    summary = [{"label": "Critical", "value": len(crit), "status": "critical" if crit else "ok"},
               {"label": "Warning", "value": len(warn), "status": "warning" if warn else "ok"},
               {"label": "Hung VMs", "value": len(hung), "status": "critical" if hung else "ok"},
               {"label": "Phase 2 would restart", "value": len(a.get("would_restart") or []), "status": "info"}]
    sections = []
    if cs:
        sections.append({"title": "Down or hung now", "status": status,
                         "columns": ["Severity", "Object", "Where", "Problem", "What to do"],
                         "rows": [[c["severity"], "%s %s" % (c["object_type"], c["object"]), _where(c, multi),
                                   c["summary"] + (". " + c["detail"] if c["kind"] in ("host", "datastore") and c.get("detail") else ""), c["todo"]] for c in cs],
                         "row_status": [c["severity"] for c in cs], "cell_status": [[c["severity"], "", "", "", ""] for c in cs]})
    if hung:
        wr = a.get("would_restart") or []
        sections.append({"title": "Would be restarted in phase 2 (nothing was done)", "status": "info",
                         "text": "Phase 2 is not built yet. This is what it would restart (a guest restart first), so you can check every "
                                 "one by hand now and count the false alarms before any action is turned on.",
                         "columns": ["VM", "vCenter", "Host", "Why"],
                         "rows": [[r["vm"], r["vcenter"], r["esxi_host"], r["why"]] for r in wr] or [["(none)", "", "", ""]]})
        held = (a.get("protected") or []) + (a.get("over_limit") or [])
        if held:
            sections.append({"title": "Protected: ticket only", "status": "warning",
                             "columns": ["VM", "vCenter", "Host", "Why it would not be restarted"],
                             "rows": [[r["vm"], r["vcenter"], r["esxi_host"], r["why"]] for r in held],
                             "row_status": ["warning"] * len(held)})
    nj = vm_watch_not_judged(d)
    if nj:
        sections.append({"title": "Not judged this run", "columns": ["VM", "vCenter", "Host", "Why"],
                         "rows": [[x["name"], x["vcenter"], x["esxi_host"], x["why"]] for x in nj]})
    lines = ["Probe from the AAP node: %s%s, %s s per address." % ("ping" if pr.get("on", True) else "off",
                                                                     (" and TCP port %s" % pr["port"]) if pr.get("port") else "",
                                                                     _num(pr.get("timeout", 3)))]
    if d.get("not_probed"):
        lines.append("Not probed (over vm_watch_max_suspects): %s." % ", ".join(d["not_probed"]))
    if int(d.get("vms_skipped") or 0):
        lines.append("%d VM(s) skipped by vm_watch_skip_vms / vm_watch_skip_folders / site_exclude_hosts." % int(d["vms_skipped"]))
    if "vms_read" in d:                               # 1.112.4: what was read, what was judged, what was listed
        lost = d.get("vms_lost") or []
        down_vms = sum(len(h.get("vms") or []) for h in d.get("hosts") or []
                       if not h.get("maintenance") and h.get("standby") not in ("in", "entering"))
        lines.append("VMs read: %d. Judged (powered on, connected): %d. Listed as inaccessible, invalid or orphaned: %d%s. "
                     "Potentially affected on down hosts: %d." % (
                         int(d.get("vms_read") or 0), int(d.get("vms_checked") or 0), len(lost),
                         (" (%d of them not judged: vm_watch_skip_vms / vm_watch_skip_folders)" % len([v for v in lost if v.get("not_judged")]))
                         if any(v.get("not_judged") for v in lost) else "", down_vms))
    for st in d.get("vcenters") or []:                 # 1.118.4: what vCenter did not give, and what that means for the tickets
        if st.get("incomplete"):
            lines.append("Not read completely on %s this run, so no open ticket of it is cleared: %s." % (st.get("name", ""), st["incomplete"]))
    sections.append({"title": "How it judged", "lines": lines})
    return {"title": title, "status": status, "subtitle": subtitle, "summary": summary, "sections": sections,
            "footer": "Read-only: the watch changed nothing."}


def _num(v):
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else str(f)
    except (TypeError, ValueError):
        return str(v or "")


VM_KINDS = ("lost", "hung", "no-heartbeat")
VM_WORD = {"lost": "inaccessible", "hung": "hung", "no-heartbeat": "no heartbeat"}


def _vm_roots(c, roots):
    """1.112.4: affected datastores first, then the VM's down host."""
    if c["kind"] not in VM_KINDS:
        return []
    v = c["vm"]
    vc, host = v["vcenter"], v.get("host_moid")
    down, paths = [], []
    for moid in v["datastore_moids"]:
        ds = roots.get(("datastore", vc, moid))
        if ds and ds.get("inaccessible"):
            down.append(ds)
        elif ds and host in ds.get("path_host_moids", []):
            paths.append(ds)
    h = roots.get(("host", vc, host))
    return down + paths + ([h] if h else [])


def vm_watch_health(data, conditions, opts=None):
    """The check results the ServiceNow tickets job reads (roles/servicenow/tasks/tickets.yml), one 'host' per
    vCenter: {vcenter: {status, checks: [vm_watch], findings, reach: false, ...}}. Each condition is a finding
    (check vm_watch), so a ticket per condition, noted (or resolved) once the watch no longer finds it.
    A vCenter not read lists vm_watch as failed: its open tickets are left alone.
    1.112.4: one ticket per root - a datastore that is NOT accessible, or a host that is down - with the full VM list in its
    summary (the incident's description and repeat work notes). 1.112.4c, so that nothing reads as cleared while it lasts:
    - a VM (or a host's path) a root covers is still a finding, at severity "info" ("covered by ..."): the tickets job opens
      no ticket for it, but an incident already open under its id stays open (it clears only when no finding has its id);
    - a path loss on a datastore that is still accessible is grouped in the report only: each host's path is its own ticket
      as long as that host's paths are down, whether 1, 2 or more hosts;
    - a datastore's ticket id is its name, always; two datastores of one name down in one vCenter share that ticket."""
    d, o = data or {}, opts or {}
    conditions = list(conditions or [])
    roots = dict(((c["kind"], c["vcenter"], c["moid"]), c) for c in conditions
                 if c.get("moid") and c["severity"] == "critical" and (c.get("inaccessible") or c["kind"] == "host"))
    out = {}
    for s in d.get("vcenters") or []:
        name = s.get("name", "")
        if s.get("note", "").startswith("the same vCenter"):
            continue
        out[name] = {"status": "healthy", "checks": ["vm_watch"],
                     "checks_failed": ["vm_watch"] if s.get("error") or s.get("incomplete") else [],    # 1.118.4: incomplete clears nothing
                     "findings": [], "act": {}, "time": d.get("read_at", ""), "job": str(o.get("job", "")), "reach": False}
    by_id = {}

    def add(c, severity=None, covered=None):
        vc = c.get("vcenter") or ""
        h = out.setdefault(vc, {"status": "healthy", "checks": ["vm_watch"], "checks_failed": [], "findings": [], "act": {},
                                "time": d.get("read_at", ""), "job": str(o.get("job", "")), "reach": False})
        fid = _ticket_id(c, d)
        listed = "; " + c["membership"] if c.get("membership") and not covered else ""
        where = (" [%s]" % _where(c, False)) if _where(c, False) else ""
        text = ("covered by %s: " % covered if covered else "") + c["summary"] + listed + where
        f = by_id.get((vc, fid))
        if f is not None:                                     # two datastores of one name, down: one ticket, both said
            f["summary"] += " | " + text
            if RANK.get(c["severity"], 9) < RANK.get(f["severity"], 9):
                f["severity"] = c["severity"]
            return
        f = {"check": "vm_watch", "id": fid, "severity": severity or c["severity"], "summary": text, "hint": c.get("todo", "")}
        by_id[(vc, fid)] = f
        h["findings"].append(f)
        h["status"] = "findings"

    for c in conditions:
        mine = [r for r in _vm_roots(c, roots) if r.get("inaccessible") or r["kind"] == "host"]
        if mine:                                              # under a root: its id stays alive, no ticket of its own
            add(c, "info", _ticket_id(mine[0], d))
            continue
        if c.get("kind") == "datastore" and not c.get("inaccessible"):
            for p in c.get("folded") or []:                  # path loss only: each host's path is its own ticket
                add(p)
            continue
        add(c)
        for p in c.get("folded") or []:                      # an inaccessible datastore: its hosts' paths covered by it
            add(p, "info", _ticket_id(c, d))
    return out


def vm_watch_artifact(data, conditions, actions=None):
    """The job artifact vm_watch: the conditions, the counts, and what phase 2 would restart."""
    d, cs, a = data or {}, conditions or [], actions or {}
    return {"read_at": d.get("read_at", ""), "vcenters": [s.get("name") for s in d.get("vcenters") or [] if s.get("ok")],
            "checked": {"hosts": len(d.get("hosts") or []), "datastores": len(d.get("datastores") or []), "vms": int(d.get("vms_checked") or 0)},
            "critical": len([c for c in cs if c["severity"] == "critical"]), "warning": len([c for c in cs if c["severity"] == "warning"]),
            "conditions": [{k: c.get(k) for k in ("id", "severity", "kind", "object_type", "object", "vcenter", "datacenter", "cluster", "summary")}
                           for c in cs],
            "would_restart": a.get("would_restart") or [], "protected": a.get("protected") or [], "over_limit": a.get("over_limit") or [],
            "not_judged": vm_watch_not_judged(d)}


# ---- 1.112.5: the watch's email only when something changes (vm_watch_email_on_change) ----
EMAIL_STATE = "vm_watch_email_state"


def vm_watch_email_ids(conditions, report_on=None):
    """{id: severity} of the conditions an email is about: those of vm_watch_report_on, folded to their root as the tickets
    are (vm_watch_health) - a VM under a lost datastore or a down host is that datastore's or host's id, not its own.
    Each id is its vCenter's (id@vcenter): two vCenters may each have a datastore1 (Codex review)."""
    want = [str(x) for x in (report_on if report_on is not None else ["critical"])]
    conditions = [c for c in conditions or [] if isinstance(c, dict) and c.get("id")]
    roots = dict(((c.get("vcenter") or "", c["object_type"], c["object"]), c) for c in conditions
                 if c.get("severity") == "critical" and ((c.get("kind") == "datastore" and c.get("object_type") == "datastore")
                                                         or c.get("kind") == "host"))

    def root(c):
        if c.get("kind") not in VM_KINDS:
            return None
        vc = c.get("vcenter") or ""
        for ds in c.get("datastores") or []:
            if (vc, "datastore", ds) in roots:
                return roots[(vc, "datastore", ds)]
        return roots.get((vc, "host", (c.get("vm") or {}).get("esxi_host") or ""))

    def key(c):
        return "%s@%s" % (c["id"], c["vcenter"]) if c.get("vcenter") else c["id"]

    out = {}
    for c in conditions:
        if c.get("severity") not in want:
            continue
        r = root(c)
        cid, sev = (key(r), r["severity"]) if r is not None else (key(c), c["severity"])
        if cid not in out or RANK.get(sev, 9) < RANK.get(out[cid], 9):
            out[cid] = sev
    return out


def _email_state(runs):
    """The newest run's vm_watch_email_state (runs oldest first, as site_report_history returns them), or None."""
    for r in reversed(list(runs or [])):
        st = ((r or {}).get("artifacts") or {}).get(EMAIL_STATE) if isinstance(r, dict) else None
        if isinstance(st, dict) and isinstance(st.get("ids", {}), dict):
            return st
    return None


def _utc(ts):
    import datetime
    return datetime.datetime.fromtimestamp(float(ts), datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _shown_id(i):
    i, _, vc = str(i).partition("@")
    i = i[len("vm-watch:"):] if i.startswith("vm-watch:") else i
    return "%s in %s" % (i, vc) if vc else i


# conditions made from an event of the window (HA, storage, paths): they age out after vm_watch_window_minutes - no recovery
WINDOW_IDS = ("vm-watch:event:", "vm-watch:storage:", "vm-watch:paths:")


def _windowed(i):
    return str(i).startswith(WINDOW_IDS)


def vm_watch_email_decision(conditions, runs=None, opts=None):
    """Does this run of the watch email? (1.112.5) Only when the conditions of vm_watch_report_on changed since the last
    email - a new one, a worse severity, one cleared - plus a reminder every every_hours (0 = none) while they last, and
    one 'recovered' email when all have cleared. The last email's state is this template's newest run's job artifact
    vm_watch_email_state ({sent_at, sent_ts, ids: {id: severity}, review_ts}), from runs (site_report_history, oldest
    first). Pure: opts = {now (epoch s), on_change, every_hours, report_on, review (asked), scheduled, why (why AAP could
    not be read, '' = it was), unread (a vCenter was not read: nothing missing counts as cleared), time_zone}.
    -> {send, kind (first, change, reminder, recovered, review, always, unknown, quiet, none), review (made this run),
        report (make the report: something to report, a review, or the recovered email), why (the job output's line when
        no email; '' otherwise), lines (what to print), changed (the email's lines: new, worse, cleared), state (to keep)}"""
    o = opts or {}
    now = float(o.get("now") or 0)
    every = float(o.get("every_hours") or 0)
    tz = _tm.zone(o.get("time_zone"))[0]
    ids = vm_watch_email_ids(conditions, o.get("report_on"))
    asked = bool(o.get("review"))
    why = str(o.get("why") or "")
    prev = None if why else _email_state(runs)
    prev_ids = dict((prev or {}).get("ids") or {})

    def shown(ts):
        return _tm.local(_utc(ts), tz) if tz is not None else _utc(ts)

    lines = []
    review = asked
    review_ts = float((prev or {}).get("review_ts") or 0)
    if asked and o.get("scheduled") and o.get("on_change", True):
        hours = every if every > 0 else 4.0
        lines.append("vm_watch_review on a schedule: a review every %s h at most - launch it by hand for an AAR" % _num(hours))
        if review_ts and now - review_ts < hours * 3600:
            review = False
            lines.append("No review this run: the last was at %s, the next from %s." % (shown(review_ts), shown(review_ts + hours * 3600)))
    if review:
        review_ts = now

    keep = [prev]

    def out(send, kind, state, why_line="", changed=None):
        if state is not None:
            state = dict(state, review_ts=review_ts)
        # carry: the state to keep when this email does not go out after all (no recipient, a skipped blip) - Codex review
        carry = dict(keep[0], review_ts=review_ts) if keep[0] else None
        return {"send": bool(send), "kind": kind, "review": review, "report": bool(ids) or review or kind == "recovered",
                "why": why_line, "lines": lines, "changed": changed or [], "state": state, "carry": carry if send else state}

    sent = {"sent_at": _utc(now), "sent_ts": now, "ids": ids}
    if not o.get("on_change", True):
        return out(bool(ids) or review, "always", None)
    if why:
        if ids or review:
            lines.append("Email on every report this run: the last email could not be read from AAP - %s." % why.rstrip("."))
        # nothing sent: nothing kept either - the next run reads the last state AAP has (its recovered email is not lost)
        return out(bool(ids) or review, "unknown", sent if (ids or review) else None)
    unread = bool(o.get("unread"))
    new = sorted(i for i in ids if i not in prev_ids)
    worse = sorted(i for i in ids if i in prev_ids and RANK.get(ids[i], 9) < RANK.get(prev_ids[i], 9))
    # a window's event that ages out is no recovery; nothing counts as cleared while a vCenter (or a suspect) was not read -
    # and what could not be seen stays in the state until a full read settles it (Codex review)
    cleared = [] if unread else sorted(i for i in prev_ids if i not in ids and not _windowed(i))
    if unread:
        seen, ids = ids, dict(prev_ids)
        ids.update(seen)
        sent["ids"] = ids
    # the events that aged out leave the state without an email, so the same event again is new (Codex review)
    prev = dict(prev, ids=dict((i, v) for i, v in prev_ids.items() if unread or i in ids or not _windowed(i))) if prev else prev
    keep[0] = prev
    changed = (["New: %s (%s)" % (_shown_id(i), ids[i]) for i in new]
               + ["Worse: %s (%s, was %s)" % (_shown_id(i), ids[i], prev_ids[i]) for i in worse]
               + ["Cleared: %s (was %s)" % (_shown_id(i), prev_ids[i]) for i in cleared])
    if review:
        return out(True, "review", sent, changed=changed)
    if prev is None:
        return out(bool(ids), "first" if ids else "none", sent if ids else {"sent_at": "", "sent_ts": 0, "ids": {}})
    if not ids:
        if prev_ids and not unread:
            return out(True, "recovered", dict(sent, ids={}),
                       changed=["Cleared: %s (was %s)" % (_shown_id(i), prev_ids[i]) for i in sorted(prev_ids)])
        return out(False, "none", prev)
    if new or worse or cleared:
        return out(True, "change", sent, changed=changed)
    last = float(prev.get("sent_ts") or 0)
    if every > 0 and now - last >= every * 3600:
        return out(True, "reminder", sent, changed=["Reminder: no change since the email of %s" % shown(last)])
    nxt = (" - next reminder at %s" % shown(last + every * 3600)) if every > 0 else " - no reminder"
    return out(False, "quiet", prev,
               "nothing changed since the email of %s (vm_watch_email_every_hours: %s%s)." % (shown(last), _num(every), nxt))


class FilterModule(object):
    def filters(self):
        return {"vm_watch_conditions": vm_watch_conditions, "vm_watch_actions": vm_watch_actions, "vm_watch_report": vm_watch_report,
                "vm_watch_health": vm_watch_health, "vm_watch_artifact": vm_watch_artifact, "vm_watch_clean_line": vm_watch_clean_line,
                "vm_watch_not_judged": vm_watch_not_judged, "vm_watch_email_ids": vm_watch_email_ids,
                "vm_watch_email_decision": vm_watch_email_decision}
