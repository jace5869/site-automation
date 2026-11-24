# -*- coding: utf-8 -*-
"""The outage review's time line (1.66.0): what vCenter and ServiceNow recorded around an outage, in order, on four
lanes - vCenter alarms, host events, ServiceNow, ACT - with the three numbers a review asks for: the first signal, the
time from that signal to the ticket, and how long a host was down. The VM outage watch makes it (roles/vmware_vm,
tasks/watch.yml: vm_watch_review, vm_watch_timeline) from what site_vmware_outage_events and site_outage_tickets
read. Pure functions: no network, no clock (the stretch of time is given), so tests/test_outage_timeline.py proves
every rule. The events table is the source: the chart only places its rows on a time axis."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import csv
import datetime
import io
from collections import defaultdict
import importlib.util
import json
import os
import re

try:                                       # 1.112.0 (Neon P1): ticket and vCenter text stays text - never templated again
    from ansible.utils.unsafe_proxy import wrap_var as _wrap
    _OLD = type(_wrap("x")) is not str     # ansible-core before 2.19; later ones never template a filter's result
except ImportError:                        # the unit tests, without Ansible
    _OLD = False



# 1.116.0: the scheduled watch is frozen at 1.118.4. A run that is not a review (vm_watch_timeline on a scheduled run) gets
# 1.118.4's own time line, word for word: plugins/module_utils/outage_timeline_1184.py is that release's
# outage_timeline_filters.py, byte-identical (tests/test_watch_frozen.py pins its sha256). Only a review uses the code below.
_fspec = importlib.util.spec_from_file_location(
    "outage_timeline_1184", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "module_utils", "outage_timeline_1184.py"))
_v1184 = importlib.util.module_from_spec(_fspec)
_fspec.loader.exec_module(_v1184)

def _unsafe(v):
    return _wrap(v) if _OLD else v


def _redact(value):
    return _site_redact.redact_all(value, review=True)


def outage_redact(value):
    return _unsafe(_redact(value))


def _load(name, folder):
    spec = importlib.util.spec_from_file_location(name, os.path.join(os.path.dirname(__file__), folder, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_context = _load("site_outage_context", "../module_utils")
_site_redact = _load("site_redact", "../module_utils")
_review_text = _load("outage_review_text", "../module_utils")
_tm = _load("site_time", "../module_utils")
_lk = _load("site_link", "../module_utils")
_watch = _load("vmware_watch_filters", ".")
_correlation_id = _load("site_filters", ".").site_correlation_id

UTC = datetime.timezone.utc
LANES = [("vcenter", "vCenter alarms"), ("host", "Host events"), ("servicenow", "ServiceNow"), ("act", "ACT")]
LANE = dict(LANES)
WORD = {"critical": "Critical", "warning": "Warning", "low": "Low", "info": "Note", "ok": "Back to normal"}
RANK = {"critical": 4, "warning": 3, "low": 2, "info": 1, "ok": 0}
BAD = ("critical", "warning", "low")

# vCenter's event type -> (lane, level, what happened, kind, role). A problem and its clear share a kind: the pair makes
# a "back after ..." (a short one is one Low event); down and up bound the time a host was down. The types are the list
# roles/vmware_vm/library/site_vmware_outage_events.py reads (the test holds the two together).
TYPES = {
    "VmPoweredOffEvent": ("vcenter", "warning", "VM powered off", "power", "problem"),
    "VmPoweredOnEvent": ("vcenter", "ok", "VM powered on", "power", "clear"),
    "HostConnectionLostEvent": ("host", "critical", "Host not responding", "conn", "down"),
    "HostDisconnectedEvent": ("host", "warning", "Host disconnected from vCenter", "conn", "down"),
    "HostConnectedEvent": ("host", "ok", "Host connected again", "conn", "up"),
    "HostReconnectionFailedEvent": ("host", "critical", "Reconnecting to the host failed", "", ""),
    "HostShutdownEvent": ("host", "info", "Host shut down on request", "", ""),
    "EnteredMaintenanceModeEvent": ("host", "info", "Host entered maintenance mode", "", ""),
    "ExitMaintenanceModeEvent": ("host", "info", "Host left maintenance mode", "", ""),
    "com.vmware.vc.ha.VmRestartedByHAEvent": ("vcenter", "warning", "HA restarted a VM", "", "restart"),
    "VmRestartedOnAlternateHostEvent": ("vcenter", "warning", "HA restarted a VM", "", "restart"),
    "VmFailoverFailed": ("vcenter", "critical", "HA could not restart a VM", "", ""),
    "VmDasBeingResetEvent": ("vcenter", "warning", "HA reset a VM", "", ""),
    "VmDasBeingResetWithScreenshotEvent": ("vcenter", "warning", "HA reset a VM", "", ""),
    "DasHostFailedEvent": ("host", "critical", "HA: host failed", "conn", "down"),
    "com.vmware.vc.HA.DasHostFailedEvent": ("host", "critical", "HA: host failed", "conn", "down"),
    "DasHostIsolatedEvent": ("host", "critical", "HA: host isolated from the network", "", ""),
    "com.vmware.vc.HA.DasHostIsolatedEvent": ("host", "critical", "HA: host isolated from the network", "", ""),
    "com.vmware.vc.HA.ClusterFailoverActionInitiatedEvent": ("vcenter", "warning", "HA started a failover", "", ""),
    "esx.problem.storage.apd.start": ("host", "critical", "All paths down to a storage device", "apd", "problem"),
    "esx.problem.storage.apd.timeout": ("host", "critical", "All paths down: timed out, I/O fails now", "", ""),
    "esx.clear.storage.apd.exit": ("host", "ok", "Storage paths are back", "apd", "clear"),
    "esx.problem.scsi.device.state.permanentloss": ("host", "critical", "Storage device lost for good", "pdl", "problem"),
    "esx.problem.scsi.device.state.permanentloss.noopens": ("host", "critical", "Storage device lost for good", "pdl", "problem"),
    "esx.clear.scsi.device.state.permanentloss.deviceonline": ("host", "ok", "Storage device is online again", "pdl", "clear"),
    "esx.problem.vmfs.nfs.volume.no.space": ("host", "critical", "NFS datastore out of space", "", ""),
    "esx.problem.vmfs.nfs.server.disconnect": ("host", "critical", "NFS datastore disconnected", "nfs", "problem"),
    "esx.clear.vmfs.nfs.server.restored": ("host", "ok", "NFS datastore connection restored", "nfs", "clear"),
    "esx.problem.vmfs.nfs.server.restored": ("host", "ok", "NFS datastore connection restored", "nfs", "clear"),
    "esx.problem.vmfs.heartbeat.timedout": ("host", "warning", "Datastore heartbeat timed out", "hb", "problem"),
    "esx.problem.vmfs.heartbeat.unrecoverable": ("host", "critical", "Datastore heartbeat lost for good", "", ""),
    "esx.problem.vmfs.heartbeat.recovered": ("host", "ok", "Datastore heartbeat is back", "hb", "clear"),
    "esx.problem.storage.redundancy.lost": ("host", "warning", "Storage path redundancy lost", "sred", "problem"),
    "esx.problem.storage.redundancy.degraded": ("host", "warning", "Storage path redundancy degraded", "sred", "problem"),
    "esx.clear.storage.redundancy.restored": ("host", "ok", "Storage path redundancy is back", "sred", "clear"),
    "esx.problem.storage.connectivity.lost": ("host", "critical", "Lost the connection to a storage device", "sconn", "problem"),
    "esx.clear.storage.connectivity.restored": ("host", "ok", "The connection to the storage device is back", "sconn", "clear"),
    "esx.problem.net.redundancy.lost": ("host", "warning", "Uplink redundancy lost", "nred", "problem"),
    "esx.problem.net.redundancy.degraded": ("host", "warning", "Uplink redundancy degraded", "nred", "problem"),
    "esx.clear.net.redundancy.restored": ("host", "ok", "Uplink redundancy is back", "nred", "clear"),
    "esx.problem.net.connectivity.lost": ("host", "critical", "Network connection lost", "nconn", "problem"),
    "esx.clear.net.connectivity.restored": ("host", "ok", "Network connection is back", "nconn", "clear"),
    "AlarmStatusChangedEvent": ("vcenter", "info", "Alarm changed", "alarm", ""),
}
ALARM = {"red": ("critical", "problem"), "yellow": ("warning", "problem"), "green": ("ok", "clear"), "normal": ("ok", "clear"), "gray": ("info", "")}
RESTART_GROUP_S = 300      # HA restarts of one cluster within this many seconds are one event, "HA restarted 14 VMs"
SAME_S = 60                # the same event at the same place again within this many seconds: one row, "x3"
STEPS_MIN = (1, 2, 5, 10, 15, 30, 60, 120, 180, 360, 720, 1440, 2880, 10080)
TRACK_PX = 580.0           # the narrowest track the marks must fit (the 800 px window)
ROWS = 3
LEAD_S = 1800              # 1.90.0: the lead-up looks back at most this far before the first critical event
RAW_TITLE = "Every event, grouped (each single event is in the workbook)"
ATTACH = "Attach the ServiceNow API credential to the outage watch template to see and link its incidents (read only)."
TICKETS_NOT_READ = "ServiceNow was not read: attach the ServiceNow API credential to the job template. " + ATTACH


def _short(name):
    """A host as a reader says it: esx03.example.mil -> esx03 (an address stays whole)."""
    n = str(name or "").strip()
    return n if re.match(r"^[\d.]+$|^[0-9a-fA-F:]+$", n) else n.split(".")[0]


def _time(v):
    """Seconds since 1970, or a time as the modules return it (2026-11-12T01:19:04Z), or ServiceNow's -> UTC datetime."""
    if isinstance(v, datetime.datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return datetime.datetime.fromtimestamp(float(v), tz=UTC)
    t = str(v or "").strip()
    if re.match(r"^\d+(\.\d+)?$", t):
        return datetime.datetime.fromtimestamp(float(t), tz=UTC)
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[ T](\d\d):(\d\d)(?::(\d\d))?", t)
    if not m:
        return None
    try:
        return datetime.datetime(*[int(x or 0) for x in m.groups()], tzinfo=UTC)
    except ValueError:
        return None


def _span(seconds):
    """A length of time as a reader says it: 40 s, 46 min, 2 h 05 min, 3 d 4 h."""
    s = int(round(max(0, seconds)))
    if s < 90:
        return "%d s" % s
    m = int(round(s / 60.0))
    if m < 120:
        return "%d min" % m
    if m < 2880:
        return "%d h %02d min" % (m // 60, m % 60) if m % 60 else "%d h" % (m // 60)
    return "%d d %d h" % (m // 1440, (m % 1440) // 60)


def outage_window(now, hours=6, start="", end="", time_zone=""):
    return _v1184.outage_window(now, hours, start, end, time_zone)     # 1.116.0: the same window as 1.118.4, for every run


def _vcenter_events(events, datastores=None):
    out = []
    for e in events or []:
        if not isinstance(e, dict):
            continue
        t = _time(e.get("time"))
        if t is None:
            continue
        typ = str(e.get("type") or "")
        lane, level, label, kind, role = TYPES.get(typ, ("vcenter" if not e.get("host") else "host", "info", "", "", ""))
        host, vm, cluster = _short(e.get("host")), str(e.get("vm") or ""), str(e.get("cluster") or "")
        source_message = str(e.get("message") or "")
        message = " ".join(source_message.split())
        display_message = " ".join(_site_redact.redact(source_message).split())
        args = e.get("args") or {}
        ds = str(e.get("datastore") or next((args[k] for k in ("datastore", "datastoreName", "volumeName", "dsName") if args.get(k)), ""))
        if not ds and typ == "esx.problem.vmfs.nfs.volume.no.space":
            ds = str(args.get("1") or "")  # EventEx names the NFS volume in its first argument (Broadcom KB 428302).
        if not ds and typ.startswith(("esx.problem", "esx.clear")):
            volume = re.search(r"\bvolume [\w:-]+ \(([^)]+)\)", message, re.I)
            named = re.search(r"(?:affected )?datastores?:\s*([^,)]+?)(?:\.(?:\s|$)|\)|$)", message, re.I)
            ds = volume.group(1) if volume else named.group(1).strip() if named else ""
        if not ds and typ.startswith(("esx.problem.vmfs.heartbeat.", "esx.clear.vmfs.heartbeat.")):
            volume = re.search(r"(?:lost|restored) access to volume (.+?)(?: due to |\.$|$)", message, re.I)
            ds = volume.group(1) if volume else ""
        if not ds and (e.get("entity_type") == "datastore" or str(e.get("alarm") or "").lower().startswith("datastore ")):
            ds = str(e.get("entity") or "")
        device = str(e.get("device") or "")
        if not device and typ.startswith(("esx.problem", "esx.clear")):
            found = re.search(r"\b(?:naa\.[0-9a-f]+|eui\.[0-9a-f]+|t10\.\S+|mpx\.\S+)", " ".join(str(v) for v in args.values()) + " " + message, re.I)
            device = found.group(0) if found else ""
        where = host or vm or cluster or str(e.get("datacenter") or "") or str(e.get("vcenter") or "")
        if typ == "AlarmStatusChangedEvent":
            alarm, to = str(e.get("alarm") or "an alarm"), str(e.get("to") or "").lower()
            level, role = ALARM.get(to, ("info", ""))
            label = "Alarm %s: %s" % ({"red": "red", "yellow": "yellow", "green": "back to green", "gray": "gray"}.get(to, to or "changed"), alarm)
            where = _short(e.get("entity")) or where           # the Where label: short (1.90.2: the VM's identity keeps the raw entity)
            kind = "alarm:" + alarm
        elif typ in ("DatastoreInaccessibleEvent", "DatastoreAccessibleEvent"):
            lost = typ == "DatastoreInaccessibleEvent"
            kind, role, level = "datastore-access", "problem" if lost else "clear", "critical" if lost else "ok"
            label = "Datastore inaccessible" if lost else "Datastore accessible again"
            where = ds or where
        elif kind == "power":
            where = vm or where
        elif not label:
            label = display_message[:120] or typ
        out.append({"t": t, "lane": lane, "level": level, "label": label, "where": where, "kind": kind, "role": role,
                    "type": typ, "message": message, "display_message": display_message, "vcenter": str(e.get("vcenter") or ""), "vm": vm, "cluster": cluster,
                    "host": host, "host_full": str(e.get("host") or ""), "datastore": ds, "device": device, "datacenter": str(e.get("datacenter") or ""), "entity": str(e.get("entity") or "").strip(), "entity_type": str(e.get("entity_type") or "").lower(),
                    "device_raw": _watch._device_raw(e) if typ in _watch.STORAGE else "",
                    "cluster_moid": e.get("cluster_moid") or (e.get("entity_moid") if e.get("entity_type") == "cluster" else ""),
                    "host_moid": e.get("host_moid", ""), "vm_moid": e.get("vm_moid", ""),
                    "datastore_moid": e.get("datastore_moid") or (e.get("entity_moid") if e.get("entity_type") == "datastore" else ""),
                    "entity_moid": e.get("entity_moid", ""),
                    "key": e.get("key", ""), "from": e.get("from", ""), "to": e.get("to", ""), "n": 1, "names": []})
    # APD events often identify only the device. Resolve it from this vCenter's named path events.
    def device_key(e):
        host = "" if e["device"].lower().startswith(("naa.", "eui.")) else e["host_moid"] or e["host"]
        return e["vcenter"], host, e["device"].lower()
    extents = defaultdict(set)
    for ds in datastores or []:
        for device in ds.get("devices") or []:
            extents[ds.get("vcenter", ""), device.lower()].add((ds["name"], ds.get("moid", "")))
    devices = defaultdict(set)
    for e in out:
        if e["device"] and e["datastore"]:
            devices[device_key(e)].add((e["datastore"], e["datastore_moid"]))
    for e in out:
        names = extents.get((e["vcenter"], e["device"].lower())) or devices.get(device_key(e))
        if names and len(names) == 1:
            name, moid = next(iter(names))
            if e["datastore"] in ("", name) and (not e["datastore_moid"] or e["datastore_moid"] == moid):
                e["datastore"], e["datastore_moid"] = name, moid
    out.sort(key=lambda x: x["t"])
    return out



def _restarts(evs):
    """HA's restarts of one cluster close together -> one event, 'HA restarted 14 VMs'. -> (events, VMs restarted)."""
    out, open_, vms = [], {}, set()
    for e in evs:
        if e["role"] != "restart":
            out.append(e)
            continue
        vms.add((e["vcenter"], e["vm"] or id(e)))
        cluster = e.get("cluster_key") or (e["vcenter"], id(e))
        g = open_.get(cluster)
        if g is not None and (e["t"] - g["last"]).total_seconds() <= RESTART_GROUP_S:
            g["last"] = e["t"]
            if e["vm"] and e["vm"] not in g["names"]:
                g["names"].append(e["vm"])
            if (e["host"], e["vm"]) not in g["members"]:
                g["members"].append((e["host"], e["vm"]))      # 1.90.2: each restart's own host and VM, for the page's filters
            g["n"] += 1
            continue
        g = dict(e, last=e["t"], names=[e["vm"]] if e["vm"] else [], where=e["cluster"] or e["where"], members=[(e["host"], e["vm"])])
        open_[cluster] = g
        out.append(g)
    for e in out:
        if e["role"] == "restart":
            n = len(e["names"]) or e["n"]
            e["label"] = "HA restarted %d VM%s" % (n, "" if n == 1 else "s")
            e["count"] = n
            e["n"] = 1
    return out, len(vms)


def _pairs(evs, low_seconds):
    """A problem and its clear at the same place: cleared within low_seconds -> one Low event, '..., back after 40 s'
    (the clear is not a row of its own); low_seconds 0 = never. -> (events, the hosts' down stretches [(host, from, to or None, vCenter)])."""
    gone, downs, open_down = set(), [], {}
    host_ids, full_names = defaultdict(set), defaultdict(set)
    for e in evs:
        key = (e.get("vcenter"), e.get("host"))
        if e.get("host_moid"):
            host_ids[key].add(e["host_moid"])
        if "." in (e.get("host_full") or ""):
            full_names[key].add(e["host_full"])
    def host_key(e):
        key = (e.get("vcenter"), e.get("host"))
        if e.get("host_moid"):
            return e["host_moid"]
        if len(host_ids[key]) > 1 or len(full_names[key]) > 1:
            return None
        return next(iter(host_ids[key]), e.get("host") or e["where"])

    for i, e in enumerate(evs):
        if low_seconds <= 0 or e["role"] not in ("problem", "down"):
            continue
        want = "clear" if e["role"] == "problem" else "up"
        for j in range(i + 1, len(evs)):
            c = evs[j]
            if j in gone or c["role"] != want or c["kind"] != e["kind"] or c.get("vcenter") != e.get("vcenter"):
                continue
            if not host_key(e) or host_key(e) != host_key(c):
                continue
            if e["role"] != "down" and e["where"] != c["where"]:
                continue
            if e.get("device") and c.get("device") and e["device"] != c["device"]:
                continue
            took = (c["t"] - e["t"]).total_seconds()
            if low_seconds > 0 and took <= low_seconds and not c.get("recorded_clear"):
                e["level"], e["label"] = "low", "%s, back after %s" % (e["label"], _span(took))
                e["flap"] = True
                gone.add(j)
            break
    kept = [e for i, e in enumerate(evs) if i not in gone]
    for e in kept:
        dk = (e.get("vcenter", ""), host_key(e) or id(e))
        if e["role"] == "down" and not e.get("flap") and dk not in open_down:
            open_down[dk] = (e["where"], e["t"])
        elif e["role"] == "up" and dk in open_down:
            name, start = open_down.pop(dk)
            downs.append((name, start, e["t"], dk[0]))
    downs += [(name, start, None, vc) for (vc, _), (name, start) in open_down.items()]
    downs.sort(key=lambda d: d[1])
    return kept, downs


def _same(evs):
    """The same event at the same place again within a minute: one row with a count."""
    out, last = [], {}
    for e in evs:
        k = (e["lane"], e["level"], e["label"], e["where"], e.get("vcenter", ""), e["kind"])
        if e["role"] == "up" or e["role"] == "clear" and e["type"].startswith("esx."):
            k += (e["t"],)
        p = last.get(k)
        if p is not None and (e["t"] - p["seen"]).total_seconds() <= SAME_S and not e.get("count"):
            p["n"] += 1
            p["seen"] = e["t"]
            if e.get("vm") and e["vm"] != p.get("vm") and e["vm"] not in p.setdefault("vms", []):
                p["vms"].append(e["vm"])                # 1.90.2: the page's VM filter matches each VM a merged row stands for
            m, first = (e.get("host") or "", e.get("vm") or ""), (p.get("host") or "", p.get("vm") or "")
            if m != first and m not in p.get("members", [first]):
                p.setdefault("members", [first]).append(m)     # and each host and VM pair (only when there are several)
            continue
        e["seen"] = e["t"]
        last[k] = e
        out.append(e)
    return out


def _ticket_host(short, shorten=True):
    m = re.match(r"^\s*(?:\[[^\]]*\]\s*)?([^\s:]+):", str(short or ""))
    return (_short(m.group(1)) if shorten else m.group(1)) if m else ""


def _act_block(description):
    """The ACT analysis a health ticket's description carries -> its text ('' = none)."""
    lines, out, on = str(description or "").splitlines(), [], False
    for line in lines:
        if not on and re.match(r"^\s*ACT\b.*analysis", line, re.I):
            on = True
            continue
        if on:
            if not line.strip() and out:
                break
            if line.strip():
                out.append(line.strip())
    return " ".join(out)


def _cut(text, n=160):
    t = " ".join(_redact(str(text or "")).split())
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


def _lower_first(text):
    """'Host not responding' -> 'host not responding'; 'HA restarted 3 VMs' stays."""
    return text[:1].lower() + text[1:] if text[1:2].islower() else text


def _incident_ticket_ids(triggers, events, prefix):
    """Use the watch writer's identities, before names were shortened for the review."""
    ids = set()

    def add(vc, finding_id):
        if vc and finding_id:
            ids.add(_correlation_id(finding_id, vc, "vm_watch", prefix))

    for c in triggers:
        if c.get("ticket_id"):
            add(c.get("vcenter"), c.get("ticket_id"))
        for name in c.get("ticket_names") or []:
            add(c["vcenter"], _watch._ticket_id(dict(c, object=name), {}))
        for path in c.get("folded") or []:
            add(path.get("vcenter") or c.get("vcenter"), _watch._ticket_id(path, {}))
    for e in events:
        if e["role"] in ("clear", "up"):
            continue
        signal = _change(e)
        scope = signal[0][1] if signal else ""
        vc = e["vcenter"]
        source = e.get("source_names", e)
        host = e["host_full"] or source["entity"]
        if scope == "host" and host:
            add(vc, _watch._ticket_id(dict(kind="host", object=host), {}))
        if scope == "datastore":
            ds = source["datastore"] or source["entity"]
            add(vc, _watch._ticket_id(dict(kind="datastore", object_type="datastore", object=ds), {}))
        if e["type"] in _watch.STORAGE and host:
            add(vc, _watch._ticket_id(dict(kind="storage", object=host, device=e["device_raw"] or e["device"], event_type=e["type"]), {}))
        if e["type"] in _watch.EVENT_SEVERITY:
            name = source["vm"] or host or e["cluster"]
            add(vc, "vm-watch:event:%s:%s" % (e["type"], name))
    return ids


def _correlated_ticket_numbers(tickets, incident, opts):
    # Legacy IDs end after the check. The title identifies the object within that vCenter.
    prefix = opts.get("correlation_prefix") or "site-health"
    legacy_ids = defaultdict(set)
    for vc, name in incident["hosts"] | incident["affected"]:
        cid = _correlation_id("", vc, "vm_watch", prefix).rsplit(":", 1)[0]
        legacy_ids[cid].add(name)
        legacy_ids[cid].update(opts["host_aliases"].get((vc, name), ()))
    result = set()
    for k in tickets or []:
        if not isinstance(k, dict):
            continue
        cid = str(k.get("correlation_id") or "")
        legacy = _ticket_host(k.get("short"), shorten=False) in legacy_ids.get(cid, ())
        if cid in incident["ticket_ids"] or legacy:
            result.add(k.get("number"))
    return result


def _ticket_events(tickets, begin, end, instance="", table="", repeats=False):
    """-> (events, tickets opened before the stretch and still open in it, {ticket: its root-cause text} - an ACT analysis
    first, else its close notes; only for tickets of the stretch (1.112.0: the outage's own is picked later)). Each event of a ticket carries
    its link ("url", when the instance is known)."""
    out, before, causes = [], [], {}
    for k in tickets or []:
        if not isinstance(k, dict):
            continue
        num, short = str(k.get("number") or "ticket"), str(k.get("short") or "")
        where, desc = _ticket_host(short), str(k.get("description") or "")
        m = re.search(r"^\s*Severity:\s*(\w+)", desc, re.M | re.I)
        sev = (m.group(1).lower() if m else "warning")
        sev = sev if sev in ("critical", "warning", "low") else "warning"
        opened = _time(k.get("opened"))
        base = {"where": where, "kind": "", "role": "", "type": "", "vcenter": "", "n": 1, "names": [], "ticket": num,
                "url": _ticket_url(instance, k.get("sys_id"), table)}
        act = _act_block(desc)
        if opened is not None and begin <= opened <= end:
            out.append(dict(base, t=opened, lane="servicenow", level=sev, label="Ticket %s opened" % num, message=short, role="ticket"))
            if act:
                out.append(dict(base, t=opened, lane="act", level="info", label="ACT analysis in %s: %s" % (num, _cut(act)), message=act))
                # 1.112.0 (Neon P1): only a ticket of this window - never an older ticket's analysis of another outage
                causes.setdefault(num, "ACT in %s: %s" % (num, _cut(act, 300)))
        elif opened is not None and opened < begin:
            before.append(num)
        for note in k.get("notes") or []:
            if not isinstance(note, (list, tuple)) or len(note) < 2:
                continue
            t, text = _time(note[0]), " ".join(str(note[1] or "").split())
            if t is None or not text or not begin <= t <= end or (not repeats and text.startswith("Still failing at")):
                continue
            if text.startswith("Still failing at"):
                out.append(dict(base, t=t, lane="servicenow", level="info", label="Work note in %s: %s" % (num, text), message=text, repeat=True))
            elif text.startswith("The automated check passes again"):
                out.append(dict(base, t=t, lane="servicenow", level="ok", label="%s: the check passes again" % num, message=text))
            elif re.match(r"^ACT\b", text):
                out.append(dict(base, t=t, lane="act", level="info", label="ACT note in %s: %s" % (num, _cut(text)), message=text))
                if not str(causes.get(num, "")).startswith("ACT"):
                    causes[num] = "ACT in %s: %s" % (num, _cut(text, 300))
            else:
                out.append(dict(base, t=t, lane="servicenow", level="info", label="Work note in %s: %s" % (num, _cut(text, 120)), message=text))
        done, how = _time(k.get("resolved")), "resolved"
        if done is None:
            done, how = _time(k.get("closed")), "closed"
        if done is not None and begin <= done <= end:
            notes = str(k.get("close_notes") or "").strip()
            out.append(dict(base, t=done, lane="servicenow", level="ok", role="resolved",
                            label="Ticket %s %s%s" % (num, how, (": " + _cut(notes, 120)) if notes else ""), message=notes or short))
            if notes:
                causes.setdefault(num, "Close notes of %s: %s" % (num, _cut(notes, 300)))
    return out, before, causes


# ---- the chart --------------------------------------------------------------------------------------------

def _ticks(z0, z1, tz, most=8):
    """Round times between z0 and z1 in the zone -> (step in minutes, [datetime])."""
    span = (z1 - z0).total_seconds() / 60.0
    step = next((s for s in STEPS_MIN if span / s <= most), STEPS_MIN[-1])
    loc = z0.astimezone(tz or UTC)
    day = loc.replace(hour=0, minute=0, second=0, microsecond=0)
    t = day + datetime.timedelta(minutes=int((loc - day).total_seconds() // 60 // step) * step)
    out = []
    while t <= z1 + datetime.timedelta(seconds=1) and len(out) < 40:
        if t >= z0 - datetime.timedelta(seconds=1):
            out.append(t)
        t = (t.astimezone(UTC) + datetime.timedelta(minutes=step)).astimezone(tz or UTC)
    return step, out


def _zoom(evs, downs, begin, end, tz):
    """The part of the stretch the chart shows: from the first event to the last, with room, on round times."""
    if not evs:
        return begin, end
    first = min(e["t"] for e in evs)
    last = max([e["t"] for e in evs] + [d[2] or end for d in downs])
    pad = max((last - first).total_seconds() * 0.08, 300)
    z0, z1 = first - datetime.timedelta(seconds=pad), last + datetime.timedelta(seconds=pad)
    step, _t = _ticks(z0, z1, tz)
    step_s = step * 60
    off = (z0.astimezone(tz or UTC).utcoffset() or datetime.timedelta(0)).total_seconds()
    z0 = datetime.datetime.fromtimestamp(((z0.timestamp() + off) // step_s) * step_s - off, tz=UTC)
    z1 = datetime.datetime.fromtimestamp(-((-(z1.timestamp() + off)) // step_s) * step_s - off, tz=UTC)
    return max(z0, begin), min(z1, end)


def _pct(t, z0, z1):
    return round(min(100.0, max(0.0, (t - z0).total_seconds() * 100.0 / max(1.0, (z1 - z0).total_seconds()))), 2)


def _place(marks):
    """Marks of one lane on up to three rows, none over another at the narrowest window; one with no room joins the
    mark before it ('3+1': its tip lists both)."""
    ends, placed = [-100.0] * ROWS, []
    for m in marks:
        w = (30 + 7.5 * len(m["label"])) * 100.0 / TRACK_PX
        at = min(max(m["at"], w / 2), 100 - w / 2)
        for r in range(ROWS):
            if at - w / 2 >= ends[r] + 0.6:
                m.update(row=r, x=round(at, 2))
                ends[r] = at + w / 2
                placed.append(m)
                break
        else:
            p = min(placed, key=lambda q: abs(q["x"] - at))
            p["more"] = p.get("more", 0) + 1
            p["label"] = "%s+%d" % (p["nums"][0], p["more"])
            p["nums"].append(m["nums"][0])
            p["tip"] += "\n" + m["tip"]
            p["ev"] += m["ev"]
            if RANK[m["level"]] > RANK[p["level"]]:
                p["level"] = m["level"]
            ends[p["row"]] = max(ends[p["row"]], p["x"] + (30 + 7.5 * len(p["label"])) * 50.0 / TRACK_PX)
    return placed


def _ticket_url(instance, sys_id, table="incident"):
    """A link to a ticket in ServiceNow: https://<instance>/nav_to.do?uri=<table>.do%3Fsys_id%3D<sys_id> - table is servicenow_table
    ('incident' when empty); only an https instance (no user, no path), a plain table name and a plain sys_id; '' for anything
    else (the number then shows without a link). The address passes the shared check (module_utils/site_link)."""
    inst, sid = str(instance or "").strip().rstrip("/"), str(sys_id or "").strip()
    tbl = str(table or "").strip() or "incident"
    if not re.match(r"^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$", inst) or not re.match(r"^[A-Za-z0-9]{1,64}$", sid) \
            or not re.match(r"^[a-z][a-z0-9_]{0,79}$", tbl):
        return ""
    return _lk.safe_link("%s/nav_to.do?uri=%s.do%%3Fsys_id%%3D%s" % (inst, tbl, sid))


# ---- what an event is about, and whether it is about the outage (the fine-tuning of the time line) --------------------

CLASSES = [("host", "Host"), ("ha", "HA"), ("storage", "Datastore and storage"), ("network", "Network"),
           ("vm", "VM power and heartbeat"), ("usage", "CPU and memory usage"), ("snapshot", "Snapshots"), ("other", "Other")]
CLASS_NAME = dict(CLASSES)
_STORAGE = ("datastore", "storage", "scsi", "vmfs", "apd", "pdl", "multipath", "path redundancy", "lun", "disk", "naa.")
_NETWORK = ("network", "uplink", "port group", "portgroup", "vswitch", "vmnic", "esx.problem.net", "esx.clear.net", "nic ")
_HA = ("vsphere ha", "ha ", ".ha.", "dashost", "vmrestartedby", "vmrestartedonalternate", "vmfailover", "vmdasbeing", "clusterfailover", "failover")
USAGE_ALARM = re.compile(r"\b(?:cpu|memory)\s+(?:usage|utili[sz]ation)\b", re.I)         # a usage alarm by its own name (not "Host memory status")
_USAGE = ("cpu", "memory", "usage", "utilization", "balloon", "swap")


def _classify(e):
    """The kind of an event, by its vCenter type and its words: host, ha, storage, network, vm, usage, snapshot, other."""
    text = (" ".join([str(e.get("type") or ""), str(e.get("label") or ""), str(e.get("message") or "")]) + " ").lower()
    if "snapshot" in text:
        return "snapshot"
    if any(w in text for w in _NETWORK):
        return "network"
    if any(w in text for w in _STORAGE):
        return "storage"
    if any(w in text for w in _HA):
        return "ha"
    if any(w in text for w in _USAGE):
        return "usage"
    if "virtual machine" not in text and (str(e.get("type") or "").startswith(("Host", "EnteredMaintenance", "ExitMaintenance")) or "host" in text):
        return "host"
    if e.get("vm") or any(w in text for w in ("virtual machine", "heartbeat", "power", "guest", "vmware tools")):
        return "vm"
    return "other"


def _key(e, name):
    """A name as one vCenter knows it: (vCenter, name). Two vCenters can hold VMs and hosts with the same name."""
    return (str(e.get("vcenter") or ""), name)


def _has(keys, e, name):
    """Is `name` (as e's vCenter knows it) in keys? An event without a vCenter (a ticket) matches on the name alone."""
    if not name:
        return False
    vc = str(e.get("vcenter") or "")
    return _key(e, name) in keys or (not vc and any(k[1] == name for k in keys))


def _about(e):
    """The names an event is about, each with its vCenter: where it happened, its VM, the VMs HA restarted."""
    return set(_key(e, x) for x in [e.get("where"), e.get("vm")] + list(e.get("names") or []) if x)


VM_ALARM = re.compile(r"\bvirtual machine\b|^vm\b", re.I)
HOST_ALARM = re.compile(r"\bhost\b|^esxi?\b", re.I)


def _entity_words(e):
    """1.90.2: what an alarm's entity is by the alarm's own name, when vCenter did not say (no entity_type) -> "vm", "host" or ""
    ("Virtual machine CPU usage", "vSphere HA virtual machine failover failed": a VM; "Host connection and power state", "vSphere HA
    host status": a host; "vSphere HA failover in progress", "Datastore usage on disk", "License inventory monitoring": neither)."""
    kind = str(e.get("kind") or "")
    if not kind.startswith("alarm:"):
        return ""
    name = kind[6:]
    return "vm" if VM_ALARM.search(name) else "host" if HOST_ALARM.search(name) else ""


def _idents(evs):
    """1.90.2: the hosts and VMs of the review, each as its vCenter knows it (_key), and what each alarm's entity is: vCenter's own
    entity_type first; else a name the review knows as a VM or as a host (not both) on that vCenter; else the alarm's name. A VM or host
    found so is known to the next alarm on the same entity too. -> (hosts, VMs, {id(event): "vm" | "host"})."""
    hosts = set(_key(e, _short(e["host"])) for e in evs if e.get("host"))
    vms = set(_key(e, x) for e in evs for x in [e.get("vm")] + list(e.get("names") or []) + list(e.get("vms") or []) if x)
    for e in evs:
        for h, v in e.get("members") or ():
            if h:
                hosts.add(_key(e, _short(h)))
            if v:
                vms.add(_key(e, v))

    def what(e):
        ent, t = str(e.get("entity") or ""), str(e.get("entity_type") or "")
        if not ent or not str(e.get("kind") or "").startswith("alarm:"):
            return ""
        if t:
            return t if t in ("vm", "host") else ""
        is_vm, is_host = _key(e, ent) in vms, _key(e, _short(ent)) in hosts
        if is_vm != is_host:
            return "vm" if is_vm else "host"
        return _entity_words(e)
    kinds = {}
    for e in evs:                                           # first: what vCenter or the alarm's name says
        kinds[id(e)] = what(e)
        if kinds[id(e)] == "vm":
            vms.add(_key(e, e["entity"]))
        elif kinds[id(e)] == "host":
            hosts.add(_key(e, _short(e["entity"])))
    for e in evs:                                           # then: an entity an earlier or later alarm showed to be a VM or a host
        if not kinds[id(e)]:
            kinds[id(e)] = what(e)
    return hosts, vms, kinds


def _relate(evs, downs, fallback=None, cleared=None):
    """The outage's subject, worked out from the events themselves, and which events are about it -> {names, text, note}; each event gets
    "cls" (its kind) and "related". The subject: the hosts that went down and the VMs with a critical event of their own; related: the
    subject, the ESXi host of a subject VM, their cluster, and the datastores and port groups that events of their host name. A snapshot is
    related only when it is on a subject VM itself (a snapshot commit can stun a VM). Names are told apart by their vCenter.
    1.90.0: when nothing went down and no VM has a critical event, the subject is what has a critical event of its own (an ESXi host's
    red alarm, a VM's red alarm known by its entity only) - an alarm that went back to normal in the window (cleared: its
    _alarm_key) aside; then (fallback) the VMs of the alarms that cleared."""
    for e in evs:
        e["cls"] = _classify(e)
    down_names = set(d[0] for d in downs)
    hosts = set(_key(e, e["where"]) for e in evs if e["role"] == "down" and not e.get("flap") and e["where"] in down_names)
    vms = set(_key(e, e["vm"]) for e in evs if e["lane"] in ("vcenter", "host") and e["level"] == "critical" and e.get("vm") and e["role"] != "restart")
    vms |= set(_key(e, e["where"]) for e in evs if e["cls"] == "vm" and e["level"] == "critical" and e["lane"] in ("vcenter", "host")
               and str(e.get("kind") or "").startswith("alarm:") and e["where"] and e["where"] not in down_names)
    if not hosts and not vms:                               # 1.90.0: nothing went down and no VM has a critical event: what has one of its own
        for e in evs:                                       # (a host's red alarm, a VM's red alarm known by its entity only)
            if e["lane"] in ("vcenter", "host") and e["level"] == "critical" and e["role"] not in ("restart", "clear") and e.get("where") \
                    and _alarm_key(e) not in (cleared or ()):
                (hosts if e["where"] == e.get("host") or (e.get("entity_type") or _entity_words(e)) == "host" else vms).add(_key(e, e["where"]))
    if not hosts and not vms and fallback:                  # an outage that had cleared by the review: the VMs of the alarms that cleared
        vms = set(fallback)
    if not hosts and not vms:
        for e in evs:
            e["related"] = False
        return {"names": [], "keys": set(), "text": "", "note": "The subject of the outage could not be worked out (no host went down and no VM has a critical event of its own), "
                                                  "so nothing is marked as related."}
    related = set(hosts) | set(vms)
    for e in evs:                                           # the ESXi host of a subject VM, and the cluster of the subject
        if (_has(vms, e, e.get("vm")) or _has(vms, e, e.get("where"))) and e.get("host"):
            related.add(_key(e, e["host"]))
    for e in evs:
        if (_has(related, e, e.get("host")) or _has(vms, e, e.get("vm"))) and e.get("cluster"):
            related.add(_key(e, e["cluster"]))
    for e in evs:                                           # datastores and port groups named by events of a related host
        if e["cls"] in ("storage", "network") and (_has(related, e, e.get("host")) or _has(related, e, e.get("where"))):
            ent = _short(e.get("entity"))
            if ent and ent != e.get("host"):
                related.add(_key(e, ent))
    for e in evs:
        if e["cls"] == "snapshot":
            e["related"] = bool(_has(vms, e, e.get("vm")) or _has(vms, e, e.get("where")))
        else:
            e["related"] = bool(any(_has(related, e, n[1]) for n in _about(e)))
    names = sorted(set(k[1] for k in hosts | vms))
    return {"names": names, "keys": hosts | vms, "text": ", ".join(names[:4]) + (" and %d more" % (len(names) - 4) if len(names) > 4 else ""), "note": ""}


def _alarm_key(e):
    return (e.get("vcenter", ""), e["kind"], e.get("entity") or e["where"])


def _pre_raises(allv, begin, end):
    """The raise (yellow or red) before the window of every alarm that goes back to normal inside it: the alarm was abnormal when the
    window began (its first raise since the last clear is the one kept, yellow to red or not) and a clear follows in the window. The
    outage review reads these back vm_watch_review_lookback_hours; without them a clear is all the window shows and nothing says
    what it was a clear of."""
    started, clears = {}, set()
    for e in allv:                                              # oldest first
        if not str(e.get("kind") or "").startswith("alarm:"):
            continue
        k = _alarm_key(e)
        if e["t"] < begin:
            if e["role"] == "problem":
                started.setdefault(k, e)                       # the start of the uninterrupted run: a later yellow or red does not restart it
            elif e["role"] == "clear":
                started.pop(k, None)
        elif e["t"] <= end and e["role"] == "clear":
            clears.add(k)
    return [e for k, e in started.items() if k in clears]


def _alarm_runs(evs):
    """Each alarm's raise and what came of it -> ([{vm, where, alarm, level, usage, raised, cleared (None: still raised)}], the clears of
    alarms whose raise was not seen), oldest raise first."""
    open_, runs, unmatched = {}, [], []
    for e in sorted(evs, key=lambda x: x["t"]):
        if not str(e.get("kind") or "").startswith("alarm:"):
            continue
        k = _alarm_key(e)
        if e["role"] == "problem" and k not in open_:
            r = {"key": k, "vm": e.get("vm") or "", "where": e["where"], "alarm": e["kind"][6:], "level": e["level"],
                 "usage": bool(USAGE_ALARM.search(e["kind"][6:])), "raised": e["t"], "cleared": None, "vcenter": e.get("vcenter", "")}
            open_[k] = r
            runs.append(r)
        elif e["role"] == "clear":
            if k in open_:
                open_.pop(k)["cleared"] = e["t"]
            else:
                unmatched.append(e)                             # a clear whose raise was not read: how long it was raised is not known
    return runs, unmatched


def _fold(evs):
    """Identical events one after another (the same lane, what, where, level, vCenter, kind and related status - and, 1.90.2, the same host
    and VMs, so the page's host / VM filter never keeps a folded row while hiding its head) -> [(first index, [indexes])] for the runs of two or more."""
    runs, i = [], 0
    while i < len(evs):
        j = i
        k = lambda e: (e["lane"], e["label"], e["where"], e["level"], e.get("vcenter", ""), e.get("kind", ""), e.get("cls", ""), bool(e.get("related")),          # noqa: E731
                       e.get("host") or "", e.get("vm") or "", tuple(e.get("names") or ()), tuple(e.get("vms") or ()),
                       tuple(e["members"]) if len(e.get("members") or ()) > 1 else ())                     # 1.90.2: as the page filters
        while j + 1 < len(evs) and k(evs[j + 1]) == k(evs[i]):
            j += 1
        if j > i:
            runs.append((i, list(range(i, j + 1))))
        i = j + 1
    return runs


# 1.116.0: a review counts the objects named by the outage and by the watch's current read.
DISK_USAGE = re.compile(r"\bdisk (?:space )?(?:usage|utili[sz]ation)\b|\busage on disk\b", re.I)
DATASTORE_LOSS = re.compile(r"\b(?:inaccessible|not accessible|all paths down|permanent device loss|apd|pdl)\b")
CONNECTION_LOSS = re.compile(r"\b(?:disconnected|(?:lost|loss of) (?:datastore )?(?:access|connectivity|connection)|"
                             r"(?:access|connectivity|connection) (?:lost|loss))\b")
DOWN_KIND = {"host": "not responding", "datastore": "not accessible", "hung": "hung",
             "no-heartbeat": "no heartbeat", "lost": "Inaccessible in vCenter", "question": "waiting for an answer", "powered-off": "powered off", "storage": "storage paths unavailable"}
DOWN_VM_KINDS = {"lost", "hung", "question", "powered-off"}
OBJECT_WORD = {"host": "Host", "datastore": "Datastore", "vm": "VM", "path": "Storage on host"}


def _review_triggers(opts):
    # The frozen question condition drops the VM MoID and datastore names. Its inventory row retains them.
    questions = defaultdict(list)

    def location(row, name):
        return row.get("vcenter"), name, row.get("datacenter", ""), row.get("cluster", "")

    for q in opts.get("questions") or []:
        questions[location(q, q["name"])].append(q)
    triggers = []
    for c in opts.get("triggers") or []:
        if not isinstance(c, dict):
            continue
        if c.get("kind") == "question" and not (c.get("vm") or c.get("moid") or c.get("vm_moid")):
            candidates = questions[location(c, c["object"])]
            for q in candidates:
                if len(candidates) == 1 or c.get("summary", "").endswith(": " + (q.get("question") or "(no text)")):
                    c = dict(c, vm=q, datastores=q.get("datastores") or [])
                    candidates.remove(q)
                    break
        triggers.append(c)
    return triggers


def _vm_datastores(vc, vm, files, names):
    """The watch sorts names and IDs independently. Only pair them when the inventory or a single pair identifies them."""
    refs = vm.get("datastore_moids") or []
    if len(refs) == len(files) == 1:
        return [(files[0], refs[0])]
    resolved = [(names.get((vc, "datastore", moid)), moid) for moid in refs]
    found = {name for name, _ in resolved if name}
    return ([(name or moid, moid) for name, moid in resolved if name or not files]
            + [(name, "") for name in files if name not in found])


def _watch_root(c):
    kind, name = c.get("kind"), c.get("object")
    if not name or kind != c.get("object_type"):
        return False
    # vm_watch_artifact keeps the summary, but drops the inaccessible flag.
    return kind == "host" or kind == "datastore" and (c.get("inaccessible") or
        (c.get("summary") or "").startswith(("datastore %s is not accessible:" % name,
                                             "Datastore %s NOT ACCESSIBLE on " % name)))


def _condition_moid(c):
    if c.get("moid"):
        return c["moid"]
    prefix = "vm-watch:%s:" % c["kind"]
    cid = c.get("id") or ""
    if cid == prefix + c["object"]:  # A legacy name can itself contain a colon.
        return ""
    for qualifier in sorted((c["vcenter"], c["object"]), key=len, reverse=True):
        start = prefix + qualifier + ":"
        if cid.startswith(start):
            return cid[len(start):]
    return ""


def _event_loss(e):
    if e["kind"].startswith("alarm:") and any(str(e.get(k)).lower() in ("gray", "grey") for k in ("from", "to")):
        return None
    if e["role"] == "clear" or _vm_key(e):
        return None
    if e["kind"] in ("apd", "pdl", "sconn", "hb", "nfs", "datastore-access") or e["type"] in (
            "esx.problem.storage.apd.timeout", "esx.problem.vmfs.heartbeat.unrecoverable"):
        return "datastore"
    signal = _change(e)
    if not signal or signal[2]:
        return None
    obj, kind, _ = signal
    if obj[1] == "host" and (e["role"] == "down" or kind.startswith("alarm:")):
        return "host"
    if obj[1] == "datastore" and kind.startswith("alarm:"):
        return "datastore"
    return None


def _ambiguous_host(wanted, hosts, conditions, events):
    """Count distinct hosts sharing this DNS label, joining a name-only reference to a known ID when possible."""
    by_name = defaultdict(set)

    def record(vc, name, moid):
        if name and _short(name).casefold() == wanted.casefold():
            ids = by_name[vc, name.casefold()]
            if moid:
                ids.add(moid)

    for h in hosts:
        record(h.get("vcenter"), h["name"], h.get("moid"))
    for c in conditions:
        if c.get("kind") == "host" and c.get("object") and c.get("vcenter"):
            record(c["vcenter"], c["object"], _condition_moid(c))
    for e in events:
        record(e["vcenter"], e["host_full"], e["host_moid"])
        if _scope(e) == "host":
            record(e["vcenter"], e["entity"], e["entity_moid"] or e["host_moid"])
    identities = defaultdict(set)
    for (vc, name), ids in by_name.items():
        identities[vc].update(ids)
        if not ids and "." in name:
            identities[vc].add("name:" + name)
    return sum(max(1, len(keys)) for keys in identities.values()) > 1


def _review_roots(opts, events, begin, end, read_in_window):
    """Select scoped roots before applying display labels. Keep an unresolved name apart from every inventory ID."""
    roots, inventory, inventory_names = {}, defaultdict(dict), {}
    wanted = str(opts.get("review_object") or "").strip()
    ticket_names, observations = defaultdict(set), defaultdict(set)
    for kind, rows in (("host", opts.get("hosts")), ("datastore", opts.get("datastores"))):
        for obj in rows or []:
            vc, moid = obj.get("vcenter", ""), obj.get("moid")
            inventory[vc, kind, obj["name"]][moid] = obj
            if moid:
                inventory_names[vc, kind, moid] = obj["name"]

    ambiguous_host = False

    def matches(kind, name):
        if not wanted:
            return True
        if kind == "host":
            return not ambiguous_host and wanted.casefold() in (name.casefold(), _short(name).casefold())
        return name == wanted

    def add(vc, kind, name, moid="", condition=None, source="", when=None):
        current_name = inventory_names.get((vc, kind, moid), name)
        if not vc or not name or not (matches(kind, name) or matches(kind, current_name)):
            return
        candidates = inventory[vc, kind, current_name]
        if not moid and len(candidates) == 1:
            moid = next(iter(candidates))
        key = vc, kind, moid or "name:" + name
        if key not in roots or condition:
            obj = candidates.get(moid, {}) if moid else {}
            roots[key] = dict(condition or obj)
            roots[key].update(object=name, object_type=kind, kind=kind, vcenter=vc, moid=moid,
                              name_only=not moid, root_source=source)
            if obj.get("vms"):
                roots[key].setdefault("members", obj["vms"])
            # 1.116.6: the inventory listed this datastore's VMs (the watch reads them while it is not accessible)
            roots[key]["listed"] = bool(obj.get("vms")) or "members" in (condition or {})
        if when:
            observations[key].add(when)
            ticket_names[key].add(name)
        return key

    runs = []
    recorded_vms, recorded_members, recorded_times = set(), defaultdict(set), defaultdict(set)
    for run in opts.get("watch_runs") or []:
        artifact = (run.get("artifacts") or {}).get("vm_watch") or {}
        when = _time(artifact.get("read_at") or run.get("finished"))
        if not artifact or not when or not begin <= when <= end:
            continue
        runs.append((when, artifact.get("conditions") or []))
    window = [e for e in events if begin <= e["t"] <= end]
    window_triggers = opts["triggers"] if read_in_window else []
    if wanted and "." not in wanted:
        conditions = window_triggers + [c for _, rows in runs for c in rows]
        ambiguous_host = _ambiguous_host(wanted, opts.get("hosts") or [], conditions, window)

    for when, conditions in runs:
        stores, vm_findings = defaultdict(set), []
        for c in conditions:
            vc, name = c.get("vcenter"), c.get("object")
            if not vc or not name:
                continue
            if _watch_root(c):
                key = add(vc, c["kind"], name, _condition_moid(c), c, when=when)
                if key and c["kind"] == "datastore":
                    stores[vc, name].add(key)
            elif c.get("object_type") == "vm" and c.get("kind") in DOWN_KIND:
                identity = vc, name, _condition_moid(c)
                recorded_vms.add(identity)
                recorded_times[identity].add(when)
                if c["kind"] == "lost":
                    vm_findings.append((identity, c.get("summary") or ""))
        # The compact lost-VM finding keeps its datastore list only in the summary.
        for (vc, vm, moid), summary in vm_findings:
            placement = re.search(r" \(datastore (.+?)\)(?: - (?:protected|not judged)|$)", summary)
            if placement:
                files = placement[1]
                for ds in ([files] if (vc, files) in stores else files.split(", ")):
                    keys = stores.get((vc, ds), ())
                    if len(keys) == 1:
                        recorded_members[next(iter(keys))].add((vm, moid))
    for c in window_triggers:
        if c.get("vcenter") and _watch_root(c):
            add(c["vcenter"], c["kind"], c["object"], _condition_moid(c), c, when=_time(opts.get("read_at")) or end)

    def event_object(e, kind):
        name = e["host_full"] if kind == "host" else e[kind]
        moid = e[kind + "_moid"]
        if _scope(e) == kind and e["entity"]:
            name, moid = e["entity"] or name, e["entity_moid"] or moid
        return name or inventory_names.get((e["vcenter"], kind, moid)), moid

    unnamed_loss = False
    if not roots:
        for e in window:
            kind = _event_loss(e)
            if kind:
                name, moid = event_object(e, kind)
                if not name:
                    unnamed_loss = True
                    continue
                source = "from vCenter's event " + e["type"]
                key = add(e["vcenter"], kind, name, moid, source=source)
                if key:
                    e["root_event"] = True
    if not roots:
        for c in window_triggers:
            if not c.get("vcenter") or c.get("object_type") != "vm" or c.get("kind") not in DOWN_VM_KINDS:
                continue
            vm = c.get("vm") or {}
            files = c.get("datastores") or vm.get("datastores") or []
            source = "from the datastores of VM %s down now" % c["object"]
            for name, moid in _vm_datastores(c["vcenter"], vm, files, inventory_names):
                add(c["vcenter"], "datastore", name, moid, source=source)
    if wanted and not roots:
        # Explicit scope also permits examining an object without a loss finding.
        for e in window:
            for kind in ("host", "datastore"):
                name, moid = event_object(e, kind)
                add(e["vcenter"], kind, name, moid)
        if not roots and read_in_window:
            for (vc, kind, name), candidates in inventory.items():
                if matches(kind, name):
                    for moid in candidates:
                        add(vc, kind, name, moid)
    for key, c in roots.items():
        if recorded_members[key]:
            c["members"] = list(c.get("members") or []) + [dict(name=name, moid=moid)
                for name, moid in sorted(recorded_members[key])]
        c.update(ticket_names=sorted(ticket_names[key]), observations=sorted(observations[key]))
    empty_read = {} if roots else dict(runs=len(runs), object=wanted, unnamed_loss=unnamed_loss,
        partial=bool(opts.get("events_not_read") or opts.get("capped") or any(vc.get("error") for vc in opts.get("event_vcenters") or [])))
    if empty_read and ambiguous_host:
        empty_read["ambiguous_host"] = True
    if empty_read and opts.get("events_not_read"):
        empty_read["events_not_read"] = True
    opts["recorded_vm_times"] = recorded_times      # 1.116.6 (review): when the watch saw each VM down
    return list(roots.values()), empty_read, recorded_vms


def _review_clusters(rows):
    """Resolve name-only references within a datacenter, declining ambiguous aliases."""
    sites, refs = defaultdict(set), defaultdict(set)
    for r in rows:
        vc, name, dc = r.get("vcenter"), r.get("cluster"), r.get("datacenter")
        if name and dc:
            sites[vc, name].add(dc)
        if name and r.get("cluster_moid"):
            refs[vc, name, dc or ""].add(r["cluster_moid"])
    for r in rows:
        vc, name, dc = r.get("vcenter"), r.get("cluster"), r.get("datacenter")
        moid = r.get("cluster_moid")
        if moid:
            r["cluster_key"] = (vc, moid)
        elif name and (dc or len(sites[vc, name]) <= 1):
            dc = dc or next(iter(sites[vc, name]), "")
            candidates = refs[vc, name, dc]
            if len(sites[vc, name]) <= 1:
                candidates = candidates | refs[vc, name, ""]
            if len(candidates) == 1:
                r["cluster_key"] = (vc, next(iter(candidates)))
            elif not candidates:
                r["cluster_key"] = (vc, dc, name)


def _review_inventory(opts, events):
    """Join current inventory by vCenter and MoID. Historical events can have just a name.
    Give colliding inventory names distinct review labels so a name-only event cannot select either object."""
    names, ids = {}, defaultdict(set)
    full_hosts = defaultdict(set)

    def register(vc, kind, moid, name):
        if kind == "host" and name and _short(name) != name:
            full_hosts[vc, _short(name)].add(name)
            if moid:
                ids[vc, kind, name].add(moid)
        if moid and name:
            name = _short(name) if kind == "host" else name
            names.setdefault((vc, kind, moid), name)
            ids[vc, kind, name].add(moid)

    def label(vc, kind, moid, name):
        if kind == "host" and name:
            short = _short(name)
            if name != short and not moid:
                exact = ids[vc, kind, name]
                if len(exact) == 1:
                    moid = next(iter(exact))
                elif len(full_hosts[vc, short]) > 1:
                    return name
            if len(full_hosts[vc, short]) > 1 and not moid:
                return short
            name = short
        name = name or moid or ""
        if not moid and len(ids[vc, kind, name]) == 1:
            moid = next(iter(ids[vc, kind, name]))
        name = names.get((vc, kind, moid), name)
        collision = len(ids[vc, kind, name]) > 1 or kind == "host" and len(full_hosts[vc, name]) > 1
        return "%s [%s]" % (name, moid) if moid and collision else name

    def condition_moid(c):
        kind = c.get("object_type")
        return c.get("moid") or c.get((kind or "") + "_moid") or (c.get("vm") or {}).get("moid")

    for vc, name, moid in opts["recorded_vms"]:
        register(vc, "vm", moid, name)
    vms = list(opts.get("questions") or [])
    for kind, rows in (("host", opts.get("hosts")), ("datastore", opts.get("datastores"))):
        for r in rows or []:
            vms.extend(dict(v, vcenter=r.get("vcenter", "")) for v in r.get("vms") or [])
            register(r.get("vcenter", ""), kind, r.get("moid"), r["name"])
    host_members = {(h.get("vcenter"), h.get("moid")): h.get("vms") or [] for h in opts.get("hosts") or []}
    conditions = opts["triggers"] + opts["roots"]
    for c in conditions:
        vc = c.get("vcenter", "")
        kind = c.get("object_type")
        identity = "name:" + c["object"] if c.get("name_only") else condition_moid(c)
        register(vc, kind, identity, c.get("object"))
        if kind == "host":
            for name in c.get("ticket_names") or []:
                register(vc, kind, condition_moid(c), name)
        # 1.116.1: retain the root's qualified path host names for the SolarWinds lookup.
        for name in c.get("hosts") or []:
            register(vc, "host", None, name)
        for path in c.get("folded") or []:
            register(vc, "host", path.get("host_moid"), path.get("host"))
        for path in c.get("path_devices") or []:
            register(vc, "host", path.get("host_moid"), path["host"])
        vms.extend(dict(v, vcenter=vc) for v in c.get("members") or [])
        if c.get("vm"):
            vms.append(dict(c["vm"], vcenter=vc, datastores=c.get("datastores") or c["vm"].get("datastores", [])))
    for v in vms:
        vc = v.get("vcenter", "")
        register(vc, "vm", v.get("moid"), v.get("name"))
        register(vc, "host", v.get("host_moid"), v.get("esxi_host") or v.get("host"))
        stores, refs = v.get("datastores") or [], v.get("datastore_moids") or []
        if len(stores) == len(refs) == 1:
            register(vc, "datastore", refs[0], stores[0])
    for e in events:
        for kind in ("host", "vm", "datastore"):
            register(e["vcenter"], kind, e[kind + "_moid"], e["host_full"] if kind == "host" else e[kind])
        register(e["vcenter"], e["entity_type"], e["entity_moid"], e["entity"])

    def vm_row(v, vc):
        host = label(vc, "host", v.get("host_moid"), v.get("esxi_host") or v.get("host"))
        files = v.get("datastores") or []
        files = sorted({label(vc, "datastore", moid, name) for name, moid in _vm_datastores(vc, v, files, names)})
        return dict(v, vcenter=vc, name=label(vc, "vm", v.get("moid"), v["name"]), esxi_host=host, datastores=files)

    unmatched = {_finding_object(c) for c in opts["roots"] if c["name_only"]}
    triggers = []
    for original in conditions:
        c = dict(original)
        if c.get("id") and c.get("object"):
            c["ticket_id"] = _watch._ticket_id(original, {})  # writer uses inventory names, before review labels
        vc, kind = c.get("vcenter", ""), c.get("object_type")
        if c.get("object") and (condition_moid(c) or _finding_object(c) not in unmatched):
            c["object"] = label(vc, kind, condition_moid(c), c["object"])
        if kind == "host" and c.get("kind") == "host" and not c.get("members"):
            c["members"] = host_members.get((vc, c.get("moid")), [])
        if "members" in c:
            c["members"] = [vm_row(v, vc) for v in c["members"]]
            if kind == "host":
                for v in c["members"]:
                    v["esxi_host"] = c["object"]
        if c.get("folded"):
            c["folded"] = [dict(path, host_label=label(vc, "host", path.get("host_moid"), path.get("host")))
                           for path in c["folded"]]
        if c.get("hosts"):
            c["hosts"] = [label(vc, "host", None, h) for h in c["hosts"]]
        if c.get("host_moids") and not c.get("paths"):
            c["hosts"] = [label(vc, "host", moid, moid) for moid in c["host_moids"]]
        if c.get("vm"):
            c["vm"] = vm_row(dict(c["vm"], datastores=c.get("datastores") or c["vm"].get("datastores", [])), vc)
            c["datastores"] = c["vm"]["datastores"]
        triggers.append(c)
    identified = {(c["vcenter"], c["kind"], c["moid"]) for c in opts["roots"] if c["moid"]}

    def event_label(vc, kind, moid, name):
        if (vc, kind, name) in unmatched and (vc, kind, moid) not in identified:
            return name
        return label(vc, kind, moid, name)

    for e in events:
        vc = e["vcenter"]
        original = {k: e[k] for k in ("where", "host", "datastore", "vm", "entity")}
        for kind in ("host", "vm", "datastore"):
            e[kind] = event_label(vc, kind, e[kind + "_moid"], e["host_full"] if kind == "host" else e[kind])
        entity_kind = e["entity_type"] or _scope(e)
        if e["entity"] and entity_kind in ("host", "datastore", "vm"):
            e["entity"] = event_label(vc, entity_kind, e["entity_moid"] or e[entity_kind + "_moid"], e["entity"])
        elif e["entity_moid"]:
            e["entity"] = label(vc, entity_kind, e["entity_moid"], e["entity"])
        if e["kind"] == "power":
            e["where"] = e["vm"] or e["where"]
        elif original["host"] and original["where"] == original["host"]:
            e["where"] = e["host"]
        if any(e[k] != val for k, val in original.items()):
            e["source_names"] = original  # the workbook and CSV show the source event
    _match_path_clears(events)
    n = len(opts["triggers"])
    hosts = [dict(h, full_name=h["name"], name=label(h.get("vcenter", ""), "host", h.get("moid"), h["name"]))
             for h in opts.get("hosts") or []]
    _review_clusters(events + hosts + triggers)
    host_aliases = defaultdict(set)
    for (vc, _), names_seen in full_hosts.items():
        for fqdn in names_seen:
            host_aliases[vc, label(vc, "host", None, fqdn)].add(fqdn)
    recorded = {(vc, label(vc, "vm", moid, name)) for vc, name, moid in opts["recorded_vms"]}
    recorded_times = defaultdict(set)
    for (vc, name, moid), times in (opts.get("recorded_vm_times") or {}).items():
        recorded_times[vc, label(vc, "vm", moid, name)].update(times)
    return dict(opts, recorded_vms=recorded, recorded_vm_times=recorded_times, host_aliases=host_aliases, triggers=triggers[:n], roots=triggers[n:], hosts=hosts)


def _scope(e):
    alarm = e["kind"][6:] if e["kind"].startswith("alarm:") else ""
    return (e.get("entity_type") or ("datastore" if "datastore" in alarm.lower() else _entity_words(e))
            or ("vm" if e.get("vm") else "host" if e.get("host") else "object"))


def _usage_alarm(name, alarms):
    name = name.lower()
    # vCenter uses both "usage" and "utilization" in its stock alarm names.
    normalized = re.sub(r"utili[sz]ation", "usage", name)
    return any(a in normalized for a in alarms) or ("disk usage" in alarms and bool(DISK_USAGE.search(name)))


def _change(e):
    """An outage signal -> (object key, condition, clear). Host storage trouble need not mean the host went down."""
    typ, kind, role = e["type"], e["kind"], e["role"]
    alarm = kind[6:].lower() if kind.startswith("alarm:") else ""
    scope, name = "", ""
    if alarm:
        if role not in ("problem", "clear"):
            return None
        scope = _scope(e)
        if scope == "datastore" and (DATASTORE_LOSS.search(alarm) or "redundancy" not in alarm and CONNECTION_LOSS.search(alarm)):
            name = e["entity"] or e["datastore"]
        elif scope == "host" and re.search(r"connection and power|not responding|disconnected|host isolation", alarm):
            name = e["entity"] or e["host"]
        elif scope == "vm" and re.search(r"heartbeat|failover failed|power state|hung", alarm):
            name = e["entity"] or e["vm"]
        else:
            return None
    elif kind in ("datastore-access", "nfs"):
        scope, name = "datastore", e["datastore"]
    elif kind == "conn" or "DasHostIsolated" in typ or typ == "HostReconnectionFailedEvent":
        scope, name, kind = "host", e["host"], "conn"
    elif kind == "power" or role == "restart" or typ in ("VmFailoverFailed", "VmDasBeingResetEvent", "VmDasBeingResetWithScreenshotEvent"):
        scope, name = "vm", e["vm"]
        kind = kind or ("restart" if role == "restart" else "HA restart failed" if typ == "VmFailoverFailed" else "HA reset")
    elif typ == "esx.problem.vmfs.nfs.volume.no.space":
        return None  # a failed allocation does not establish that the datastore became inaccessible
    elif kind == "sred":
        return None  # redundancy is a lead-up signal, not datastore unavailability
    elif e.get("opening"):
        signal = _change(e["opening"])
        return (signal[0], signal[1], True) if signal else None
    elif typ.startswith(("esx.problem.storage", "esx.clear.storage", "esx.problem.scsi", "esx.clear.scsi", "esx.problem.vmfs")):
        name = e["datastore"] or e["host"]
        scope = "datastore" if e["datastore"] else "path"
        kind = kind or ("apd" if ".apd." in typ else "hb" if ".heartbeat." in typ else typ)
    if scope == "path" and e["device"]:
        name = "%s (%s)" % (name, e["device"].lower())
    if name and not e.get("unmatched_clear"):
        return (e["vcenter"], scope, name), kind, role in ("up", "clear")
    return None


def _match_path_clears(events):
    """vCenter may omit both device and datastore on a clear. Resolve the open condition on this host first."""
    opened = defaultdict(dict)
    for e in events:
        if not e["type"].startswith("esx."):
            continue
        kind = e["kind"] or ("apd" if ".apd." in e["type"] else "hb" if ".heartbeat." in e["type"] else "")
        if not kind:
            continue
        paths = opened[e["vcenter"], e["host"], kind]
        key = (e["datastore"], e["device"].lower())
        if e["type"].startswith("esx.clear.") or e["role"] == "clear":
            matches = [k for k in paths if all(not a or not b or a == b for a, b in zip(key, k))]
            if len(matches) == 1:
                source = paths.pop(matches[0])
                e["opening"] = source
                if not e["datastore"] and source["datastore"]:
                    e.setdefault("source_names", {k: e[k] for k in ("where", "host", "datastore", "vm", "entity")})
                    e["datastore"] = source["datastore"]
                    e["datastore_moid"] = source["datastore_moid"]
                e["device"] = e["device"] or source["device"]
            else:
                e["unmatched_clear"] = True
        else:
            paths.setdefault(key, e)


def _pair_key(e):
    e = e.get("opening", e)
    if e["kind"].startswith("alarm:"):
        return _alarm_key(e) + (_scope(e),)
    if e["kind"] == "power":
        return (e["vcenter"], "power", e["vm"])
    return (e["vcenter"], e["kind"], e["host"] or e["where"], e["datastore"], e["device"].lower())


def _review_rows(events, seconds, alarms, roots):
    """Partition source changes once, before timeline folding. An alarm clear consumes its raise."""
    pending, hidden, noise = {}, {}, []
    clean = []
    observed = {_finding_object(c): c["observations"] for c in roots}
    outages = defaultdict(list)
    last_reconnect = {}
    for e in events:
        if e["level"] == "critical" and e["type"].startswith("esx.problem.storage"):
            outages[e["vcenter"], e["host"], e["datastore"] or e["device"].lower()].append(e["t"])
    for e in events:
        alarm = e["kind"].startswith("alarm:")
        k = _pair_key(e)
        colors = {str(e.get(c) or "").lower() for c in ("from", "to")}
        if alarm and ({"gray", "grey"} & colors or _usage_alarm(e["kind"][6:], alarms)):
            pending.pop(k, None)
            noise.append(e)
            continue
        if e["role"] in ("problem", "down"):
            pending.setdefault(k, []).append(e)
        elif e["role"] in ("clear", "up"):
            raises = pending.pop(k, [])
            if e["role"] == "up":
                host = (e["vcenter"], "host", e["host"])
                previous = last_reconnect.get(host)
                last_reconnect[host] = e["t"]
                if any((not previous or previous < at) and at <= e["t"] for at in observed.get(host, ())):
                    clean.append(e)
                    continue
            if alarm and not raises:
                noise.append(e)
                continue
            if raises and (e["t"] - raises[0]["t"]).total_seconds() <= seconds and seconds:
                # A brief loss of storage redundancy on the same path as the ensuing
                # outage is its lead-up signal (one Low row in the director's example).
                first = raises[0]
                change = _change(first)
                if first.get("root_event") or change and any(first["t"] <= at <= e["t"] for at in observed.get(change[0], ())):
                    clean.append(e)  # Keep the evidence for a reviewed root.
                    continue
                precursor = e["kind"] == "sred" and any(0 < (t - e["t"]).total_seconds() <= LEAD_S
                    for t in outages[first["vcenter"], first["host"], first["datastore"] or first["device"].lower()])
                if precursor:
                    hidden[id(first)] = dict(first, level="low", label="%s, back after %s" % (
                        first["label"], _span((e["t"] - first["t"]).total_seconds())))
                    hidden[id(e)] = None
                else:
                    noise.extend(dict(row, flap_label=raises[0]["label"]) for row in raises + [e])
                    hidden.update((id(row), None) for row in raises)
                    continue
        clean.append(e)
    return [hidden.get(id(e), e) for e in clean if hidden.get(id(e), e) is not None], noise


def _noise_rows(noise, raw, affected, start, finish, begin, end, stamp):
    groups, last = defaultdict(list), {}
    def identity(e):
        return (e["vcenter"], e.get("vm") or e.get("entity") or e["where"])
    for e in raw:
        last[identity(e), e["kind"]] = str(e.get("to") or e["role"]).lower()
    for e in noise:
        gray = any(str(e.get(k)).lower() in ("gray", "grey") for k in ("to", "from"))
        alarm = "Gray (vCenter could not judge the alarm)" if gray else (e["kind"][6:] if e["kind"].startswith("alarm:") else e.get("flap_label", e["label"]))
        groups[identity(e) in affected, alarm or e["label"]].append(e)
    inside = max(0, (finish - start).total_seconds() / 3600) if start else 0
    outside = max(0, (end - begin).total_seconds() / 3600 - inside)
    rows = []
    for (own, alarm), events in sorted(groups.items(), key=lambda item: (not item[0][0], item[0][1])):
        es = sorted(events, key=lambda e: e["t"])
        objects = {identity(e) for e in es}
        count = sum(1 for e in es if start and start <= e["t"] <= finish)
        hot = count / inside if inside else 0
        cold = (len(es) - count) / outside if outside else 0
        if own and inside and outside and count and hot > cold:
            verdict = "rose with the outage: these are the affected VMs"
        elif own:
            verdict = "%d during the outage, %d outside it on affected VMs" % (count, len(es) - count)
        elif outside and count >= 10 and hot > 3 * cold:
            verdict = "rose with the outage (%.0f an hour during it, %.0f before and after) - same time, not a cause" % (hot, cold)
        elif not start or not inside or not outside:
            verdict = "no outage rate comparison available"
        elif len(es) < 10 or not count or not (es[0]["t"] < start and es[-1]["t"] > finish):
            verdict = "not part of the outage: %d during the outage, %d outside it" % (count, len(es) - count)
        else:
            verdict = "not part of the outage: it ran all evening (%.0f an hour before and after the outage, %.0f during it)" % (cold, hot)
        states = defaultdict(set)
        for e in es:
            state = last[identity(e), e["kind"]]
            if state in ("gray", "grey", "red", "yellow", "problem", "down"):
                states["gray" if state in ("gray", "grey") else "raised"].add(identity(e)[1])
        state = "; ".join("still %s: %s" % (k, ", ".join(sorted(v))) for k, v in sorted(states.items())) or "all back to normal"
        rows.append([alarm, "%s (%s)" % (_count(len(objects), "VM" if all(_scope(e) == "vm" for e in es) else "object"), "affected" if own else "outside the outage"), len(es),
                     "%s to %s" % (stamp(es[0]["t"]), stamp(es[-1]["t"])), state + " - " + verdict])
    return rows


def _listed(names, limit=10):
    names = sorted(names)
    return ", ".join(names[:limit]) + (" and %d more" % (len(names) - limit) if len(names) > limit else "")


def _count(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


NAMES_SHOWN = 8            # 1.116.6: a row names this many objects, then "+N more" (the workbook and the CSV keep every name)
STORAGE_KINDS = ("path", "datastore", "redundancy")      # 1.116.6: lost and back again; a loss for good (PDL) is not
BURST_S = 60               # 1.116.6: an APD and its datastore loss (or their recovery) this close together are one line


def _shown(names, limit=NAMES_SHOWN):
    """Every name up to limit + 1, else the first limit and "+N more" (never "+1 more")."""
    names = list(names)
    if len(names) <= limit + 1:
        return ", ".join(names)
    return ", ".join(names[:limit]) + " +%d more" % (len(names) - limit)


def _down_by(when):
    return "down by %s (the watch's run)" % when


def _finding_object(c):
    return c.get("vcenter", ""), c.get("object_type"), c.get("object")


def _vm_key(e):
    name = e["vm"] or (e["entity"] if _scope(e) == "vm" else "")
    return (e["vcenter"], name) if name else None


def _vms_with_events(raw, incident, alarms):
    """1.116.6: tier 2 of What it affected - the VMs whose own events (power, heartbeat, a VM alarm, lost access, HA) fall
    inside a root's loss and touch that root: its datastore, a host that lost it, or the lost host. Usage alarms and gray
    are noise, never a signal. {(vcenter, vm): (first event, root)} - never counted as down."""
    out = {}
    roots = [(obj, intervals) for obj, intervals in incident["history"].items() if obj[1] in ("datastore", "host") and intervals]
    for e in raw:
        if e.get("ticket") or not e.get("vcenter"):
            continue
        key = _vm_key(e)
        if not key or key in out or key in incident["affected"]:
            continue
        if e["kind"].startswith("alarm:") and (_usage_alarm(e["kind"][6:], alarms) or
                                               any(str(e.get(k)).lower() in ("gray", "grey") for k in ("from", "to"))):
            continue
        for obj, intervals in roots:
            if obj[0] != e["vcenter"] or not any(a <= e["t"] and (not b or e["t"] <= b) for a, b in intervals):
                continue
            hosts = incident["paths"].get(obj, set()) | ({obj[2]} if obj[1] == "host" else set())
            if (obj[1] == "datastore" and e.get("datastore") == obj[2]) or (e.get("host") and e["host"] in hosts):
                out[key] = (e, obj)
                break
    return out


def _root_history(events, opts):
    roots = {_finding_object(c): c for c in opts["roots"]}
    current = {_finding_object(c): c for c in opts["triggers"]
               if _finding_object(c) in roots and (c.get("inaccessible") or c.get("kind") == "host")}
    path_spans = defaultdict(list)
    infrastructure = [e for e in events if not _vm_key(e) and
                      ((e["vcenter"], "datastore", e["datastore"]) in roots or _scope(e) == "host")]
    # 1.116.1: count a span from the first recorded loss or watch observation.
    # Keep its provenance for the reports and keep observations out of the event export.
    observed_starts = set()
    for obj, c in roots.items():
        since = _time(current.get(obj, {}).get("since"))
        for at in sorted(set(c["observations"]) | ({since} if since else set())):
            infrastructure.append(dict(t=at, observed_root=obj, onset_known=at == since))
    infrastructure.sort(key=lambda e: e["t"])
    spans, chain, starts = _history(infrastructure, path_spans, observed_starts)
    paths = {obj: set() for obj in roots}
    host_spans = defaultdict(list)
    for obj, c in roots.items():
        if obj[1] == "host":
            paths[obj].add(obj[2])
        for path in c.get("folded") or []:
            host = path["host_label"]
            if host:
                paths[obj].add(host)
                since = _time(path.get("since"))
                if since and path.get("severity") == "critical" and not path_spans.get((obj, host)):
                    path_spans[obj, host].append([since, None])
        for host in c.get("hosts") or []:
            paths[obj].add(host)
    def during(intervals, t):
        return any(a <= t and (not b or t <= b) for a, b in intervals)

    def overlap(left, right):
        return any((not b or c <= b) and (not d or a <= d) for a, b in left for c, d in right)

    groups = {obj: {obj} for obj in roots}
    per_host = defaultdict(list)
    for (obj, host), intervals in path_spans.items():
        if obj in roots:
            paths[obj].add(host)
            host_spans[obj[0], host].extend(intervals)
            for other, earlier in per_host[obj[0], host]:
                if overlap(earlier, intervals):
                    joined = groups[obj] | groups[other]
                    for root in joined:
                        groups[root] = joined
            per_host[obj[0], host].append((obj, intervals))

    history = {obj: spans[obj] for obj in roots}
    for (vc, host), intervals in host_spans.items():
        obj = vc, "host", host
        if obj not in roots:
            related = [span for span in spans[obj] if overlap([span], intervals)]
            if related:
                history[obj] = related
    chain = [row for row in chain if during(history.get(row[1], ()), row[0]["t"])]
    cluster_spans = defaultdict(list)
    clusters = {(h.get("vcenter"), h["name"]): h.get("cluster_key") for h in opts.get("hosts") or []}
    observed_clusters = defaultdict(list)
    for e, obj, _, _ in chain:
        if obj[1] == "host" and e.get("cluster_key"):
            observed_clusters[obj].append((e["t"], e["cluster_key"]))
    for obj, intervals in history.items():
        if obj[1] == "host":
            for span in intervals:
                cluster = next((cl for t, cl in observed_clusters[obj] if during([span], t)),
                               roots.get(obj, {}).get("cluster_key") or clusters.get((obj[0], obj[2])))
                if cluster:
                    cluster_spans[cluster].append((span, obj))
    locations, members = {}, defaultdict(set)
    for obj, c in roots.items():
        for v in c.get("members") or []:
            key = obj[0], v["name"]
            members[key].add(obj)
            locations.setdefault(key, {}).update(v)
            if obj[1] == "host":
                locations[key]["esxi_host"] = obj[2]
    watch_vms = {}
    for c in opts["triggers"]:
        if c.get("object_type") == "vm" and (c.get("vm") or c.get("kind") in DOWN_KIND):
            key = c.get("vcenter"), c["object"]
            watch_vms[key] = c
            v = c.get("vm") or {}
            for obj in roots:
                if obj[0] == key[0] and (obj[2] == v.get("esxi_host") if obj[1] == "host" else obj[2] in (c.get("datastores") or [])):
                    members[key].add(obj)
            if members.get(key):
                locations.setdefault(key, {}).update(v)
    lost_files = defaultdict(set)
    on_down_host = set()
    for key, objects in members.items():
        lost_files[key].update(obj for obj in objects if obj[1] == "datastore")
        if any(obj[1] == "host" and obj in current for obj in objects):
            on_down_host.add(key)
        locations[key]["datastores"] = sorted({obj[2] for obj in lost_files[key]} | set(locations[key].get("datastores") or []))

    vm_roots = defaultdict(set, {key: set(objects) for key, objects in members.items()})
    signals = {id(e) for e, _, _, _ in chain}
    selected, facts, vm_events, source_hosts = [], [], [], {}
    leads = defaultdict(list)
    for e, obj, _, clear in chain:
        if obj[1] == "datastore" and not clear:
            leads[obj, e["host"]].append(e["t"])
    for e in events:
        vc, t = e["vcenter"], e["t"]
        host = e["host"] or (e["entity"] if _scope(e) == "host" else "")
        ds = vc, "datastore", e["datastore"]
        host_obj = vc, "host", host
        key = _vm_key(e)
        active_host = during(history.get(host_obj, ()), t) or during(host_spans.get((vc, host), ()), t)
        if key:
            if e["role"] == "restart":
                # The event's host is the destination. Only the cluster and time identify the outage.
                source = [obj for span, obj in cluster_spans.get(e.get("cluster_key"), ()) if during([span], t)]
                if not source:
                    continue
                related = {obj for obj in source if obj in roots}
                for obj in source:
                    related.update(store for store, intervals in per_host[obj[0], obj[2]]
                                   if overlap(intervals, [span for span in history[obj] if during([span], t)]))
            else:
                related = {obj for obj in members.get(key, ()) if during(history.get(obj, ()), t)}
                if host_obj in roots and during(history[host_obj], t):
                    related.add(host_obj)
                if ds[2]:
                    intervals = path_spans.get((ds, host), ()) if host and paths.get(ds) else history.get(ds, ())
                    if ds in roots and during(intervals, t):
                        related.add(ds)
                else:
                    related.update(store for store, intervals in per_host[vc, host] if during(intervals, t))
                if not related:
                    continue
            if not _change(e) and key not in members:
                continue
            vm_roots[key].update(related)
            vm_events.append(e)
            if host and e["role"] != "restart":
                source_hosts.setdefault(key, host)
        else:
            lead = e["kind"] == "sred" and any(0 <= (at - t).total_seconds() <= LEAD_S for at in leads[ds, host])
            if id(e) in signals or lead:
                facts.append(e)
            elif e.get("unmatched_clear") or (_change(e) and e["role"] in ("up", "clear")):
                continue
            elif not (during(history.get(ds, ()), t) if ds[2] else active_host):
                continue
        if ds in roots:
            e["root_group"] = min(groups[ds])
        selected.append(e)
    # Power and alarm events can describe an affected VM. Only the watch or an HA action identifies one.
    known_vms = set(members) | set(watch_vms) | opts["recorded_vms"] | {_vm_key(e) for e in vm_events
                if e["role"] == "restart" or e["type"] in ("VmFailoverFailed", "VmDasBeingResetEvent", "VmDasBeingResetWithScreenshotEvent")}
    vm_events = [e for e in vm_events if _vm_key(e) in known_vms]
    selected = [e for e in selected if not _vm_key(e) or _vm_key(e) in known_vms]
    vm_history, vm_chain, vm_starts = _history(vm_events, object_type="vm")
    history.update(vm_history)
    chain = sorted(chain + vm_chain, key=lambda row: row[0]["t"])
    facts = sorted(facts + [e for e, _, _, _ in vm_chain], key=lambda e: e["t"])
    # 1.116.6: a VM the inventory lists on a lost datastore, with no signal of its own (no event, no watch finding, not on a
    # down host), has files there - it may have hung or failed, but nothing confirms it. It is named apart, never counted.
    signalled = {(obj[0], obj[2]) for _, obj, _, _ in vm_chain}
    files_on = defaultdict(set)
    for key, objects in members.items():
        # 1.116.6 (review): an earlier watch finding confirms the VM only when it falls in a lost datastore's outage
        seen = (opts.get("recorded_vm_times") or {}).get(key)
        recorded = key in opts["recorded_vms"] and (seen is None or any(
            during(history.get(obj, ()), t) for obj in objects for t in seen))
        if (key in signalled or key in watch_vms or recorded
                or any(obj[1] != "datastore" for obj in objects)):
            continue
        for obj in objects:
            files_on[obj].add(key[1])
    unconfirmed = {(obj[0], vm) for obj, vms in files_on.items() for vm in vms}
    affected = (set(members) - unconfirmed) | signalled
    for key, c in watch_vms.items():
        location = locations.setdefault(key, {})
        for field, value in (c.get("vm") or {}).items():
            location.setdefault(field, value)
        if key in affected:
            current[_finding_object(c)] = c
    hosts = {(obj[0], h) for obj, names in paths.items() for h in names}
    findings = list(roots.values()) + list(current.values())
    ids = _incident_ticket_ids(findings, selected, opts.get("correlation_prefix") or "site-health")
    return dict(roots=roots, vm_roots=vm_roots, paths=paths, current=current, watch_vms=watch_vms, locations=locations,
                affected=affected, files_on=files_on, lost_files=lost_files, on_down_host=on_down_host, source_hosts=source_hosts,
                history=history, path_spans=path_spans, observed_starts=observed_starts, chain=chain, starts=starts | vm_starts, facts=facts, events=selected,
                hosts=hosts, ticket_ids=ids)


def _history(events, path_spans=None, observed_starts=None, object_type=None):
    """Each object's down stretches. A clear must close its own condition."""
    opened, spans, chain = defaultdict(set), defaultdict(list), []
    restarts, closed, episode_starts = set(), set(), set()
    for e in events:
        if "observed_root" in e:
            obj = e["observed_root"]
            if not opened[obj]:
                spans[obj].append([e["t"], None])
                if not e["onset_known"]:
                    observed_starts.add((obj, e["t"]))
            if obj[1] == "host":
                opened[obj].difference_update([k for k in opened[obj] if k[0].startswith("alarm:")])
                opened[obj].add(("conn", "", ""))
            elif not opened[obj]:
                opened[obj].add(("watch", "", ""))  # no individual datastore path identified
            continue
        signal = _change(e)
        if not signal:
            continue
        obj, kind, clear = signal
        if object_type and obj[1] != object_type:
            continue
        active = opened[obj]
        path = e["type"].startswith(("esx.problem", "esx.clear"))
        condition = (kind, e["host"], e.get("opening", e)["device"].lower()) if path else (kind, "", "")
        host_path = path and e["host"] and path_spans is not None
        if obj[1] == "host" and kind.startswith("alarm:") and ("conn", "", "") in active and not clear:
            # The host already has a direct connection-loss event. Its alarm is corroboration.
            continue
        if clear:
            closes = {condition} & active
            if kind == "power":
                closes.update(k for k in active if k[0] in ("HA restart failed", "HA reset"))
            if not closes:
                if kind == "power" and obj in restarts:
                    chain.append((e, obj, kind, clear))
                    restarts.discard(obj)
                continue
            if kind == "power":
                restarts.discard(obj)
            closed.update((obj, k) for k in closes)
            active.difference_update(closes)
            if host_path and not any(k[1] == e["host"] for k in active):
                path_spans[obj, e["host"]][-1][1] = e["t"]
            if not active:
                spans[obj][-1][1] = e["t"]
        elif kind == "restart":
            restarts.add(obj)
        else:
            if (obj, condition) in closed:
                closed.remove((obj, condition))
                episode_starts.add(id(e))
            if not active:
                if spans[obj]:
                    episode_starts.add(id(e))
                spans[obj].append([e["t"], None])
            if host_path and not any(k[1] == e["host"] for k in active):
                path_spans[obj, e["host"]].append([e["t"], None])
            active.discard(("watch", "", ""))
            active.add(condition)
        chain.append((e, obj, kind, clear))
    return spans, chain, episode_starts


def _context_scope(incident, opts, start, finish):
    """1.116.1: only the watch roots and their path hosts in the dated outage story."""
    if not incident["roots"] or not start or not finish or start > finish:
        return {}
    inventory = {(h.get("vcenter"), h["name"]): h for h in opts.get("hosts") or []}
    sites = {c.get("datacenter") for c in incident["roots"].values()}
    sites.update(e.get("datacenter") for e, obj, _, _ in incident["chain"] if obj in incident["roots"])
    hosts = []
    for key in sorted(incident["hosts"]):  # down hosts and the hosts of the rooted datastore paths
        host = inventory.get(key, {})
        sites.add(host.get("datacenter"))
        aliases = opts["host_aliases"].get(key, ())
        name = next(iter(aliases)) if len(aliases) == 1 else host.get("full_name") or key[1]
        site = host.get("datacenter") or incident["roots"].get((key[0], "host", key[1]), {}).get("datacenter")
        hosts.append(dict(vcenter=key[0], host=key[1], name=name, site=site or ""))
    return dict(begin=start.timestamp(), end=finish.timestamp(), hosts=hosts, sites=sorted(filter(None, sites)))


def outage_events_csv(sheet):
    """1.116.0: one source row per event, even when the review folds it."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_redact(sheet["columns"]))
    for row in sheet["rows"]:
        writer.writerow(["'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v for v in _redact(row)])
    return _unsafe(buf.getvalue())


def _cluster_spans(hosts, spans, current, observed_starts, begin, end, read):
    members = defaultdict(set)
    connected = set()
    for h in hosts:
        # the cluster's MoID when the read gives it, else the cluster within its vCenter and datacenter (1.118.4's watch read
        # gives cluster and datacenter, no cluster MoID)
        key = h.get("cluster_key") or ((h.get("vcenter", ""), h.get("datacenter", ""), h["cluster"]) if h.get("cluster") else None)
        if key and h.get("cluster") and h.get("name"):
            obj = (h.get("vcenter", ""), "host", h["name"])
            members[key, h["cluster"]].add(obj)
            if h.get("connection") == "connected" and read:
                connected.add(obj)
    found = []
    for (_, name), group in sorted(members.items()):
        changes = defaultdict(int)
        observations = set()
        for obj in group:
            for a, b in spans.get(obj, []):
                if obj in connected and a <= read and (not b or read < b):
                    b = read
                if a > end or b and b <= begin:
                    continue
                changes[max(a, begin)] += 1
                if (obj, a) in observed_starts:
                    observations.add(a)
                if b and b <= end:
                    changes[b] -= 1
        n, since = 0, None
        for at, delta in sorted(changes.items()):
            n += delta
            if n == len(group) and since is None:
                since = at
            elif n < len(group) and since:
                found.append((name, since, at, False, since in observations))
                since = None
        live = bool(read and begin <= read <= end and group <= current.keys() and group.isdisjoint(connected))
        if since:
            found.append((name, since, None, live, since in observations))
    return found


ISSUE_STATES = ("Down now", "Inaccessible in vCenter", "At risk", "Not judged", "Recovered")
PART_COLUMNS = (["Object", "What", "Down for", "Now"],
                ["Ran on", "Datastore", "VMs", "What happened", "Now"],
                ["Severity", "Object", "What", "Since", "Down for"], ["What", "How", "Down for"])
NOISE_COLUMNS = ["Alarm", "VMs", "Changes", "When", "Verdict"]
PLACEMENT_COLUMNS = ["Lost object", "Kind", "VM", "Host", "Power", "Datastores", "As of (UTC)", "Source"]


def _review_parts(incident, opts, begin, end, stamp):
    clean, history, chain = incident["facts"], incident["history"], incident["chain"]
    current, locations = incident["current"], incident["locations"]
    read = _time(opts.get("read_at")) or end
    inventory = {(h.get("vcenter", ""), _short(h["name"])): h for h in opts.get("hosts") or []}
    signals = defaultdict(list)
    meta, ds_hosts = dict(incident["roots"]), incident["paths"]
    for e, obj, kind, clear in chain:
        signals[obj].append((e, kind, clear))
        meta[obj] = e
    for obj, c in current.items():
        meta[obj] = dict(meta.get(obj, {}), **{k: v for k, v in c.items() if v})
    stores = sorted(o for o in meta if o[1] == "datastore")
    hosts = sorted(o for o in meta if o[1] == "host")
    affected, lost_files = incident["affected"], incident["lost_files"]
    on_down_host, ran_on = incident["on_down_host"], incident["source_hosts"]
    unread = "VM power state not read" if affected else ""
    observed_starts = incident["observed_starts"]
    multi = len({obj[0] for obj in incident["roots"]}) > 1

    def display(vc, name):
        return "%s (%s)" % (name, vc) if multi else name

    def group(vc, data, host=""):
        h = inventory.get((vc, host), {})
        dc, cl = data.get("datacenter") or h.get("datacenter"), data.get("cluster") or h.get("cluster")
        return " · ".join(x for x in (vc if multi and dc and dc != vc else "", dc or vc, "cluster " + cl if cl else "") if x) or "Location unknown"

    def duration(a, b):
        return _span((b - a).total_seconds()) if a and b and b >= a else "unknown"

    def loss_time(obj, at):
        if not at:
            return "time not recorded"
        return _down_by(stamp(at)) if (obj, at) in observed_starts else stamp(at)

    def issue(obj, kind, group_name, state, what, n=0):
        return dict(object_key=obj, kind=kind, group=group_name, state=state, name=obj[2], what=what, n=n, items=[])

    issues, went, recovered, down = [], [], [], []
    changed = [e for e in clean if e["level"] == "critical"]
    start = next((e["t"] for e in changed), next((e["t"] for e in clean if e["level"] in BAD), None))
    affected_rows, destinations = defaultdict(list), defaultdict(set)
    vm_items, outcomes = {}, defaultdict(set)
    for key in sorted(affected | set(incident["watch_vms"])):
        vc, vm = key
        obj = (vc, "vm", vm)
        p, c = locations.get(key, {}), incident["watch_vms"].get(key, {})
        sig = signals[obj]
        info = dict(meta.get(obj, {}), **p)
        host = ran_on.get(key) or _short(p.get("esxi_host") or (c.get("vm") or {}).get("esxi_host"))
        ds = sorted(set(p.get("datastores") or c.get("datastores") or [e["datastore"] for e, _, _ in sig if e["datastore"]]))
        restart = next((e for e, k, _ in reversed(sig) if k == "restart"), None)
        reset = next((e for e, k, _ in reversed(sig) if k == "HA reset"), None)
        failure = next((e for e, k, _ in reversed(sig) if k == "HA restart failed"), None)
        back = history[obj][-1][1] if history.get(obj) else None
        if restart and (not back or back < restart["t"]):
            last, condition, clear = sig[-1]
            back = last["t"] if condition == "power" and clear else None
        if c and c.get("kind") == "lost":
            state, word = "Inaccessible in vCenter", c.get("connection") or "inaccessible"
        elif c.get("kind") == "no-heartbeat":
            state, word = "Not judged", DOWN_KIND[c["kind"]]
        elif c:
            state, word = "Down now", DOWN_KIND.get(c["kind"], c["kind"])
            if c["kind"] == "question" and c.get("summary"):
                word = c["summary"]
        elif any(obj in current for obj in lost_files[key]):
            state, word = "Not judged", "on the lost datastore (state not read)"
        elif key in on_down_host:
            state, word = "Not judged", "on the down host (state not read)"
        elif back:
            state, word = "Recovered", "powered on" if sig[-1][1] == "power" else sig[-1][0]["label"]
        else:
            state, word = "Not judged", "current state not read"
        how = word
        if restart:
            how = "restarted by HA"
        elif reset:
            how = "reset by HA"
        elif failure:
            how = "HA could not restart it"
        if restart:
            outcomes["restart"].add(key)
        elif reset:
            outcomes["reset"].add(key)
        item = dict(name=vm, state=state, what=word, host=host or "not read", datastore=", ".join(ds) or "not read",
                    protected=bool(p.get("protected") or c.get("protected")))
        vm_items[key] = (item, ds)
        issues.append(dict(issue(obj, "vm", group(vc, info, host), state, word, 1), **item))
        if key not in affected:
            continue
        if state == "Recovered" and back:
            origin = next((a for a, b in history.get((vc, "host", host), []) if a <= back and (not b or b >= back)), None)
            origin = origin or (history[obj][-1][0] if history.get(obj) and history[obj][-1][1] == back else None)
            recovered.append((back, [display(vc, vm), word, duration(origin, back)]))
        now_word = word if state != "Recovered" else "not read"
        if len(incident["roots"]) > 1 and incident["vm_roots"][key]:
            how += " (reviewed: %s)" % ", ".join(obj[2] for obj in sorted(incident["vm_roots"][key]))
        row_key = (vc, item["host"], item["datastore"], how, now_word)
        affected_rows[row_key].append(vm)
        if restart and restart["host"]:
            destinations[row_key].add(restart["host"])
    if affected and all((vc, "vm", vm) in current for vc, vm in affected):
        unread = ""
    host_keys = incident["hosts"]
    ds_items = defaultdict(list)
    back_times, down_times = [], []
    for obj in stores + hosts:
        vc, typ, name = obj
        c = current.get(obj)
        intervals = history.get(obj) or [(None, None)]
        a, b = intervals[-1]
        live = c is not None
        if live and b:
            a, b = None, None  # the historical stretch closed before this observation
        state = "Down now" if live else "Recovered" if b else "Not judged"
        observed = live or a or incident["roots"].get(obj, {}).get("observations")
        hs = sorted(ds_hosts.get(obj, ()))
        what = ("Datastore, lost on " + _shown(hs) if hs else "Datastore, not accessible") if typ == "datastore" else (
            "ESXi host, not responding" if typ == "host" else "Lost storage paths")
        now_word = "DOWN NOW" if live else "Back" if b else "recovery not recorded"
        if not observed:
            what, now_word = OBJECT_WORD[typ], "no loss recorded"
        length = duration(a, read if live else b)
        shown = display(vc, name)
        went.append([shown, what, length, now_word])
        if live:
            down_times.append((a, shown, length))
        # Report only the latest closed episode. Paths can clear independently.
        back_times.extend(end_at for _, end_at in intervals if end_at)
        if b and not live:
            restored_hosts = [h for h in hs if any(end_at and a <= end_at <= b
                              for _, end_at in incident["path_spans"].get((obj, h), []))]
            how = ("datastore back on " + _shown(restored_hosts) if restored_hosts else
                   "host connected again" if typ == "host" else "access restored")
            recovered.append((b, [shown, how, length]))
        if typ == "datastore":
            members = sum(obj in lost_files[key] if live else key[0] == vc and name in ds
                          for key, (_, ds) in vm_items.items())
            issues.append(issue(obj, "ds", group(vc, meta[obj], hs[0] if hs else ""), state, what, members))
            for h in hs:
                paths = incident["path_spans"].get((obj, h))
                ds_items[vc, h].append("Recovered" if paths and paths[-1][1] else state)
    for vc, h in sorted(host_keys):
        obj = (vc, "host", h)
        details = ds_items[vc, h]
        interval = (history.get(obj) or [(None, None)])[-1]
        state = ("Down now" if obj in current else "At risk" if "Down now" in details else
                 "Recovered" if interval[1] or details and all(d == "Recovered" for d in details) else "Not judged")
        why = ("host not responding" if state == "Down now" else "datastore not accessible" if state == "At risk"
               else "host connected again" if interval[1] else "storage paths")
        host_info = meta.get(obj) or next((e for e, _, _, _ in chain if e["vcenter"] == vc and e["host"] == h), {})
        issues.append(issue(obj, "host", group(vc, host_info, h), state, why))
    many_centers = len({c.get("vcenter") for c in opts["down_now"]} | {o[0] for o in incident["roots"]}) > 1
    for c in opts["down_now"]:
        obj = _finding_object(c)
        since = _time(c.get("since"))
        # 1.116.1: use only this root's still-open loss. A closed episode cannot date a new finding.
        if not since and obj in incident["roots"] and c.get("kind") == obj[1] and history.get(obj):
            lost, back = history[obj][-1]
            # The current read alone tells us it is down now, not how long it has been down.
            if not back and ((obj, lost) not in observed_starts or lost < read):
                since = lost
        name = " ".join(filter(None, [OBJECT_WORD.get(obj[1], obj[1]), obj[2]])) or "Watch"
        if many_centers and obj[0]:
            name += " (%s)" % obj[0]
        detail = c.get("summary") or c.get("kind", "")
        if obj[1] == "vm" and (obj[0], obj[2]) in vm_items:
            files = vm_items[obj[0], obj[2]][0]["datastore"]
            if files != "not read" and "(datastore %s)" % files not in detail:
                detail += " (datastore %s)" % files
        down.append([(c.get("severity") or "info").title(), name, detail,
                     loss_time(obj, since), duration(since, read)])
    issues.sort(key=lambda v: (ISSUE_STATES.index(v["state"]), v["group"], v["name"]))
    down.sort(key=lambda r: (-RANK.get(r[0].lower(), 0), r[1]))
    recovered.sort(key=lambda r: (r[0], r[1][0]))
    where = [e for e, _, _, _ in chain] + list(meta.values()) + [inventory[k] for k in host_keys if k in inventory]
    where.extend(locations[k] for k in affected if k in locations)
    clusters = sorted({v["cluster"] for v in where if v.get("cluster")} | {cl for v in where for cl in v.get("clusters", [])})
    sites = sorted({v["datacenter"] for v in where if v.get("datacenter")})
    site = ", ".join(sites) or ", ".join(sorted({vc for vc, _, _ in meta if vc})) or "site unknown"
    scope = ("cluster " + clusters[0]) if len(clusters) == 1 else "site " + site
    root_hosts = [obj for obj in hosts if obj in incident["roots"]]
    kind = "Storage outage" if stores else "Host outage" if root_hosts else "Outage review"
    vm_count, host_count = len(affected), len(host_keys)
    counts = (_count(vm_count, "VM") if vm_count or not stores else "no VM confirmed") + ", " + _count(host_count, "host")
    if stores:
        names = ", ".join(o[2] for o in stores)
        what = ("datastore %s not accessible" % names if len(stores) == 1 else "%s not accessible (%s)" % (_count(len(stores), "datastore"), names))
    elif hosts:
        what = _count(len(root_hosts), "host") + " not responding"
    else:
        what = next(iter(affected))[1] if vm_count == 1 else _count(vm_count, "VM")
    subject = (stores[0][2] if len(stores) == 1 else what if not stores and not hosts
               else clusters[0] if len(clusters) == 1 else site)
    title = "%s - %s: %s" % (kind, what, counts)
    if stores and all(not history.get(obj) and not incident["roots"][obj]["observations"] for obj in stores):
        kind = "Outage review"
        title = "Outage review - datastore%s %s: %s" % ("s" if len(stores) != 1 else "", names, counts)
    if root_hosts and stores:
        title += "; %s down" % _count(len(root_hosts), "host")
    if opts.get("review_object") and not chain and not current and not any(r.get("ticket_names") for r in incident["roots"].values()):
        title = "Outage review - %s: no loss recorded" % opts["review_object"]
    if multi:
        title += " (%s)" % _count(len({o[0] for o in incident["roots"]}), "vCenter")
    still_keys = [obj for obj in stores + hosts if obj in current]
    still_keys.extend((vc, "vm", name) for (vc, name), c in incident["watch_vms"].items()
                      if c.get("kind") in DOWN_VM_KINDS and ((vc, name) in affected or not opts.get("review_object")))
    still = [display(vc, name) for vc, _, name in still_keys]
    if still:
        title += "; %s still down" % ", ".join(still)
    first = next((e for e in clean if e["level"] in BAD), None)
    note = "All in %s (%s)." % (scope, site) if not multi else "Across %s." % site
    if not stores and not hosts:
        note = "No host or datastore loss recorded." + (" " + note if affected else "")
    connected = sorted(h for vc, h in host_keys if (vc, "host", h) not in hosts)
    # 1.116.6: hosts the What went down row already names as "lost on" are not listed again
    if len(stores) == 1:
        connected = [h for h in connected if h not in ds_hosts.get(stores[0], ())]
    if connected:
        note += " Storage paths were lost on %s." % _shown(connected)
    for cluster, lost, back, live, observed in _cluster_spans(
            opts.get("hosts") or [], history, current, observed_starts, begin, end, _time(opts.get("read_at"))):
        note += " Cluster %s lost all hosts%s." % (cluster,
            " for " + duration(lost, back) if back else " and is still down" if live else " (recovery not recorded)")
    reviewed = ", ".join("%s %s (%s)%s" % (kind, name, vc, " " + c["root_source"] if c["root_source"] else "")
                         for (vc, kind, name), c in sorted(incident["roots"].items()))
    reason = "named by vm_watch_review_object" if opts.get("review_object") else "from the watch's conditions in the window"
    if not opts.get("review_object") and any(c["root_source"] for c in incident["roots"].values()):
        reason = ""
    note = ("Reviewed: %s%s. " % (reviewed, " - " + reason if reason else "") if reviewed else
            "No matching host or inaccessible datastore in the watch's conditions. ") + note
    if not reviewed and opts.get("review_object"):
        note += " Requested object: %s." % opts["review_object"]
    if opts.get("history_why"):
        note += " Watch history unavailable: " + opts["history_why"]
    if opts.get("events_not_read"):
        note += " vCenter events not read."
    count_line = "%s: %d restarted by HA, %d reset by HA." % (_count(vm_count, "VM"), len(outcomes["restart"]), len(outcomes["reset"]))
    if not vm_count and stores:
        count_line = "No VM confirmed down: the watch and HA recorded none."
    # 1.116.6: the VMs with files on a lost datastore (or that ran on a lost host), apart from the confirmed ones: from the
    # placement history as of the outage's start (VMware - VM placement snapshot), else the review's own vCenter read,
    # else the watch's members, else "not read". Never counted as down.
    files_lines, files_unread, files_rows, files_tile = [], [], [], []
    placed = {}
    for pl in opts.get("placements") or []:
        if isinstance(pl, dict) and pl.get("as_of"):
            placed.setdefault((pl.get("vcenter", ""), pl.get("kind"), pl.get("name")), pl)
            if pl.get("kind") == "host":            # the review labels a host by its short name
                placed.setdefault((pl.get("vcenter", ""), "host", _short(pl.get("name"))), pl)
    confirmed_vms = {key for key in affected}
    tier2 = incident.get("vm_event_tier") or {}
    events_lines, events_rows = [], []
    if tier2:
        shown = _shown(["%s (%s %s)" % (vm, stamp(e["t"]).split()[2], e["label"]) for (vc, vm), (e, _) in
                        sorted(tier2.items(), key=lambda kv: (kv[1][0]["t"], kv[0][1]))])
        events_lines.append("%s had events of their own during the outage (not proof they were down): %s" % (
            _count(len(tier2), "VM"), shown))
        events_rows = [[display(vc, vm), e["label"], stamp(e["t"]), display(obj[0], obj[2])]
                       for (vc, vm), (e, obj) in sorted(tier2.items(), key=lambda kv: (kv[1][0]["t"], kv[0][1]))]
    confirmed_vms |= set(tier2)

    def power(v):
        return {"poweredOn": "on", "poweredOff": "off", "suspended": "suspended"}.get(v.get("power"), v.get("power") or "power not read")

    for obj in stores + hosts:
        vc, typ, name = obj
        pl = placed.get((vc, typ, name)) or placed.get((vc, typ, re.sub(r" \[[^\]]+\]$", "", name)))
        if not pl:
            continue
        listed = [v for v in pl.get("vms") or [] if (vc, v.get("name")) not in confirmed_vms]
        when = ("as of the review's read, %s" if pl.get("source") == "review" else "as of %s, the placement snapshot") % stamp(_time(pl["as_of"]))
        shown = _shown(["%s (%s, %s)" % (v["name"], _short(v.get("host")) or "host not read", power(v)) for v in listed])
        if typ == "datastore":
            verb = "had" if pl.get("source") != "review" else "has" if len(listed) == 1 else "have"
            line = ("%s %s files on %s (%s; may have hung or failed, not proof they were down): %s" % (
                _count(len(listed), "VM"), verb, display(vc, name), when, shown) if listed else
                "No other VM had files on %s (%s)." % (display(vc, name), when))
            files_tile.append("%d with files on %s" % (len(listed), name))
        else:
            line = ("%s ran on %s (%s; not proof they were down): %s" % (_count(len(listed), "VM"), display(vc, name), when, shown)
                    if listed else "No other VM ran on %s (%s)." % (display(vc, name), when))
            files_tile.append("%d on %s" % (len(listed), name))
        files_lines.append(line)
        files_rows.extend([display(vc, name), typ, v["name"], v.get("host", ""), power(v), ", ".join(v.get("datastores") or []),
                           pl["as_of"], "review read" if pl.get("source") == "review" else "placement snapshot"] for v in listed)
    for obj in stores:
        vc, _, name = obj
        if (vc, "datastore", name) in placed or \
                (vc, "datastore", re.sub(r" \[[^\]]+\]$", "", name)) in placed:
            continue
        names = sorted(n for n in incident["files_on"].get(obj, ()) if (vc, n) not in tier2)
        if names:
            files_lines.append("%s %s files on %s (may have hung or failed): %s" % (
                _count(len(names), "VM"), "has" if len(names) == 1 else "have", display(vc, name), _shown(names)))
        elif obj not in current and history.get(obj) and not incident["roots"].get(obj, {}).get("listed"):
            confirmed = any(key[0] == vc and name in ds for key, (_, ds) in vm_items.items() if key in affected)
            files_unread.append("%sVMs on %s: not read (the watch lists a datastore's VMs only while it is not accessible)." % (
                "Other " if confirmed else "", display(vc, name)))
            files_lines.append(files_unread[-1])
    aff_rows = []
    for row_key, vms in sorted(affected_rows.items()):
        vc, h, ds, how, nw = row_key
        if multi:
            h = "%s / %s" % (vc, h)
        if destinations[row_key]:
            how += " on " + ", ".join(sorted(destinations[row_key]))
        aff_rows.append([h, ds, "%d: %s" % (len(vms), ", ".join(vms)), how, nw])
    watch_reads = [(at, obj) for obj, c in incident["roots"].items() for at in c["observations"] if begin <= at <= end]
    seen = {at for at, _ in watch_reads}
    first_watch = min(watch_reads, default=None)
    if first and first_watch and first["t"] <= first_watch[0]:
        first_watch = None
    opened = {(obj, a) for obj, a in observed_starts}

    def by_observation(obj, at):
        # 1.116.1: a repeat counts as a signal only inside a span the watch opened (no loss event dates it); a root an event
        # took down and the watch keeps seeing down does not stretch the outage window to the review
        return any((obj, a) in opened and a <= at and (not b or at <= b) for a, b in history.get(obj, []))
    observations = {at for obj, c in incident["roots"].items() for at in c["observations"]
                    if begin <= at <= end and ((obj, at) in opened or by_observation(obj, at))}
    since_times = (_time(c.get("since")) for c in incident["roots"].values())
    watch_dates = observations | {at for at in since_times if at and begin <= at <= end}
    event_start = start
    start = min(watch_dates | ({event_start} if event_start else set()), default=None)
    observed_start = any(at == start for _, at in observed_starts) and start != event_start
    last_signal = max(watch_dates | {e["t"] for e in clean if e["level"] in BAD + ("ok",)}, default=None)
    open_observation = any(at <= end and history[obj][-1] == [at, None] for obj, at in observed_starts)
    end_recovery = max(back_times + [t for t, _ in recovered], default=None) if not open_observation else None
    return dict(observed_starts=observed_starts, observed_start=observed_start, open_observation=open_observation,
                title=title if meta or affected else "", basename=kind + " " + subject, head_name=kind + ", " + scope,
                went=went, went_note=note, affected=aff_rows, affected_note=count_line, files_lines=events_lines + files_lines, files_unread=files_unread,
                files_rows=files_rows, files_tile=(["%d with events" % len(tier2)] if tier2 else []) + files_tile,
                events_rows=events_rows, now=down, at_risk=[],
                recovered=[row for _, row in recovered], issues=issues, affected_keys=affected, host_count=host_count,
                first_watch=first_watch, first_signal=first_watch[0] if first_watch else first["t"] if first else None,
                last_signal=last_signal, still_keys=still_keys,
                # the context read (What else saw it) covers every in-window watch observation of a root (1.116.1)
                last_seen=max(seen | ({last_signal} if last_signal else set()), default=None),
                start=start, finish=end_recovery, first_back=next((e["t"] for e in clean if start and e["t"] >= start and
                e["role"] in ("up", "clear") and e["lane"] == "host"), min(back_times, default=end_recovery)), still=still, unread=unread,
                down_times=down_times, infra_count=", ".join(_count(len(xs), word) for xs, word in ((hosts, "host"), (stores, "datastore"))),
                now_count=_count(len(down), "condition") if down else "nothing")



def _event_groups(events, start, first_back, finish, stamp, key=False, zone=""):
    groups, latest = [], {}
    for e in sorted(events, key=lambda row: row["t"]):
        t = e["t"]
        phase = ("Lead-up" if start and t < start else "After" if finish and t > finish + datetime.timedelta(minutes=5)
                 else "Recovery" if first_back and t >= first_back else "Outage")
        identity = (phase, e["vcenter"], e["lane"], e["level"], e["label"])
        g = latest.get(identity)
        if not g or not key and (t - g["first"]).total_seconds() > 180:
            g = dict(first=t, last=t, phase=phase, label=e["label"], n=0, level=e["level"], objects=set(), type=e["type"], lane=e["lane"],
                     sources=[], url=e.get("url", ""), vcenter=e["vcenter"], hosts=set(), stores=set())
            latest[identity] = g
            groups.append(g)
        g["sources"].append(e["source_row"])
        g["last"], g["n"] = t, g["n"] + 1
        if e.get("host"):
            g["hosts"].add(e["host"])
        if e.get("datastore"):
            g["stores"].add(e["datastore"])
        g["objects"].update(e[k] for k in ("datastore", "host", "vm", "ticket") if e.get(k))
        if e.get("entity") and _short(e["entity"]) != e.get("host"):
            g["objects"].add(e["entity"])
        if not g["objects"] and e.get("where"):
            g["objects"].add(e["where"])
    if key:
        groups = [g for i, g in enumerate(groups) if i == 0 or g["level"] != "info" or g["label"].startswith("Ticket ")]
        host_objects = set().union(*(g["objects"] for g in groups if g["lane"] == "host"))
        for drop in (lambda g: g["type"] == "AlarmStatusChangedEvent" and bool(host_objects & g["objects"]), lambda g: g["type"] in ("esx.clear.storage.redundancy.restored",
                    "esx.clear.storage.connectivity.restored"), lambda g: g["type"].endswith(".apd.timeout"),
                    lambda g: g["type"].endswith("DasHostFailedEvent")):
            for g in list(reversed(groups)):
                if len(groups) <= 15:
                    break
                if drop(g):
                    groups.remove(g)
        groups = groups[:15]
    else:
        groups = _paired_groups(groups)
    rows = []
    # 1.116.6: a window on one day writes the clock alone (the day is in Order of events and the workbook)
    one_day = not key and len({stamp(t)[:6] for g in groups for t in (g["first"], g["last"])}) == 1
    for g in groups:
        when = stamp(g["first"])
        if g["last"] != g["first"]:
            end_stamp = stamp(g["last"])
            if key:
                day, clock, zone = end_stamp.rsplit(" ", 2)
                first_day, _, first_zone = when.rsplit(" ", 2)
                when += " to " + (clock if (day, zone) == (first_day, first_zone) else end_stamp)
            else:
                # 1.116.6: "23 Nov 07:08:41-07:08:42 CET" (the zone left to the column title when the window has one zone)
                (fd, fz), (ld, lz) = when.rsplit(" ", 1), end_stamp.rsplit(" ", 1)
                when = (fd + "-" + ld[-8:] + " " + fz) if (fd[:-8], fz) == (ld[:-8], lz) else when + " to " + end_stamp
        if not key and zone:
            when = when.replace(" " + zone, "")
        if one_day:
            when = when[7:]
        if g.get("pair"):
            what = g["label"] + ": " + _shown(sorted(g["hosts"])) + ("; " + g["then"] if g["then"] else "") + \
                " (%d events)" % g["n"]
            objects = ", ".join(sorted(g["stores"]))
        else:
            what = g["label"] + (" (x%d)" % g["n"] if g["n"] > 1 else "")
            objects = ", ".join(sorted(g["objects"])) if key else _shown(sorted(g["objects"]))
        rows.append(dict(phase=g["phase"], n=g["n"], sources=g["sources"], url=g["url"], level=g["level"],
                         row=[when, what, objects, WORD[g["level"]]]))
    return rows


PAIRS = {False: ("esx.problem.storage.apd.start", "esx.problem.vmfs.heartbeat.timedout"),
         True: ("esx.clear.storage.apd.exit", "esx.problem.vmfs.heartbeat.recovered")}


def _paired_groups(groups):
    """1.116.6: the same hosts' APD and datastore heartbeat loss in one burst are one row, as their return is, with the
    path redundancy and storage alarm those hosts reported with it. Pairs are found first, then the rows are written,
    so the order of the groups (which of the two came first) does not matter."""
    burst = datetime.timedelta(seconds=BURST_S)
    merged, used = {}, set()
    for i, a in enumerate(groups):
        for back, (apd, heartbeat) in PAIRS.items():
            if i in used or a["type"] != apd or not a["hosts"]:
                continue
            def crossed(b):
                start, stop = min(a["first"], b["first"]), max(a["last"], b["last"])
                return any(o["vcenter"] == a["vcenter"] and o["type"] in PAIRS[not back] and start < o["first"] < stop
                           for o in groups)
            j = next((j for j, b in enumerate(groups) if j not in used and j != i and b["type"] == heartbeat
                      and b["phase"] == a["phase"] and b["vcenter"] == a["vcenter"] and b["hosts"] == a["hosts"]
                      and b["stores"] and abs(b["first"] - a["first"]) <= burst and not crossed(b)), None)
            if j is None:
                continue
            used.update((i, j))
            members, follow = [a, groups[j]], []
            first, core_end = min(a["first"], groups[j]["first"]), max(a["last"], groups[j]["last"])
            # What the same hosts reported in the next seconds about the same loss: path redundancy, a storage alarm.
            for k, f in enumerate(groups):
                if k in used or f["phase"] != a["phase"] or f["vcenter"] != a["vcenter"]:
                    continue
                if not (f["hosts"] and f["hosts"] <= a["hosts"] and first <= f["first"] <= core_end + burst):
                    continue
                alarm = f["type"] == "AlarmStatusChangedEvent" and STORAGE_ALARM.search(f["label"])
                if alarm and (f["level"] == "ok") != back or alarm and f["level"] not in BAD + ("ok",):
                    continue           # a storage alarm going the other way (or gray) keeps its own row
                if f["type"] in FOLLOWERS[back] or alarm:
                    used.add(k)
                    members.append(f)
                    word = ("alarms " + ("green" if back else "red" if f["level"] == "critical" else "yellow") if alarm
                            else FOLLOWERS[back][f["type"]])
                    if word not in follow:
                        follow.append(word)
            stores = sorted(set().union(*(m["stores"] for m in members[:2])))
            what = ("datastore " if len(stores) == 1 else "datastores ") + ", ".join(stores)
            hosts = _count(len(a["hosts"]), "host")
            label = what[0].upper() + what[1:] + " back on " + hosts if back else "All paths down (APD) and %s lost on %s" % (what, hosts)
            n = sum(m["n"] for m in members)
            merged[min(i, j)] = dict(a, pair=True, label=label, then="; ".join(follow), n=n, first=first, last=core_end,
                                     sources=sorted(x for m in members for x in m["sources"]),
                                     level=max((m["level"] for m in members), key=RANK.get),
                                     objects=set().union(*(m["objects"] for m in members)))
    return [merged[i] if i in merged else g for i, g in enumerate(groups) if i in merged or i not in used]


FOLLOWERS = {False: {"esx.problem.storage.redundancy.lost": "path redundancy lost too",
                     "esx.problem.storage.redundancy.degraded": "path redundancy degraded too",
                     "esx.problem.storage.apd.timeout": "I/O failed after the APD timeout",
                     "esx.problem.storage.connectivity.lost": "storage connection lost too"},
             True: {"esx.clear.storage.redundancy.restored": "path redundancy back too",
                    "esx.clear.storage.connectivity.restored": "storage connection back too"}}
STORAGE_ALARM = re.compile(r"connect to storage|storage connectivity|all paths down", re.I)


# ---- the review -------------------------------------------------------------------------------------------

def _compact(names, limit=8):
    """Abbreviate a common hyphenated stem. Only contiguous numbered names form a range."""
    names = sorted(set(filter(None, names)))
    if not names:
        return ""
    parts = [name.rpartition("-") for name in names]
    if len(names) > 1 and parts[0][1] and len({p[0] for p in parts}) == 1:
        tails = [p[2] for p in parts]
        matches = [re.fullmatch(r"(\D*)(\d+)", tail) for tail in tails]
        if len(names) > 3 and all(matches) and len({m[1] for m in matches}) == 1:
            numbers = [int(m[2]) for m in matches]
            if numbers == list(range(numbers[0], numbers[0] + len(numbers))):
                return "%s to -%s (%d)" % (names[0], tails[-1], len(names))
        names = names[:1] + ["-" + tail for tail in tails[1:]]
    return ", ".join(names[:limit]) + (" and %d more" % (len(names) - limit) if len(names) > limit else "")


def _moment(t, loc):
    local = loc(t)
    return dict(clock=local.strftime("%H:%M:%S"), day=local.strftime("%a %d %b"),
                iso=t.astimezone(UTC).isoformat(), zone=local.strftime("%Z"),
                tip="%s (%s UTC)" % (local.strftime("%a %d %b %Y, %H:%M:%S %Z"), t.astimezone(UTC).strftime("%H:%M:%S")))


def _objects(events, *keys):
    return "; ".join(filter(None, (_compact(e.get(k) for e in events) for k in keys)))


def _story_changes(e):
    """The object and direction a signal describes. Storage can affect both a host path and a named store."""
    typ, kind = e["type"], e["kind"]
    back = e["role"] in ("clear", "up")
    host, ds, vm = (e.get(k) for k in ("host", "datastore", "vm"))
    change = _change(e)
    if kind.startswith("alarm:"):
        if change:
            obj = change[0]
            return [(obj[1], obj[2], back)]
        if _scope(e) == "host" and "storage path redundancy" in kind.lower():
            return [("redundancy", e.get("host") or e["entity"], back)]
        return [("alarm", e.get("entity") or e["where"], back)]
    if kind == "pdl" and not back:
        return [("pdl", host, False)]
    if kind == "sred":
        return [("redundancy", host, back)]
    if kind in ("apd", "sconn") or typ.endswith(".apd.timeout"):
        changes = [("path", host, back)] if host else []
        if ds and (not host or not typ.endswith(".timeout")):
            changes.append(("datastore", ds, back))
        return changes or [(e["label"], e["device"] or e["where"], back)]
    if kind == "hb" and not ds:
        return [(e["label"], e["device"] or host or e["where"], back)]
    if kind == "hb" or kind in ("datastore-access", "nfs"):
        return [("datastore", ds, back)]
    if typ.startswith("VmDasBeingReset"):
        return [("reset", vm, False)]
    if typ == "VmFailoverFailed":
        return [("failed_restart", vm, False)]
    if e["role"] == "restart":
        return [("restart", vm, False)]
    if change:
        return [(change[0][1], change[0][2], back)]
    return [(e["label"], vm or ds or host or e["where"], back)]


def _story(events, parts, incident, read, loc, card_matches):
    groups, latest = [], {}
    references = {}
    paths_at = defaultdict(list)

    def clock(t):
        return loc(t).strftime("%H:%M:%S")

    gap = datetime.timedelta(minutes=15)
    burst = datetime.timedelta(seconds=BURST_S)
    turned = {}

    def add(kind, name, back, e):
        detail = e.get("datastore") if kind == "pdl" else e.get("to") if kind == "alarm" else None
        key = (e["vcenter"], kind, back, detail)
        g = latest.get(key)
        # 1.116.6: a change the other way in between (paths back, then lost again) starts a new line.
        family = "storage" if kind in STORAGE_KINDS else kind
        opposite = turned.get((e["vcenter"], family, not back)) if kind not in ("alarm", "pdl") else None
        if not g or e["t"] - g["last"] > gap or (opposite and opposite > g["last"]):
            g = dict(kind=kind, back=back, events=[], names={}, last=e["t"])
            groups.append(g)
            latest[key] = g
        g["events"].append(e)
        if name:
            g["names"].setdefault(name, e["t"])
        g["last"] = e["t"]
        turned[e["vcenter"], family, back] = e["t"]

    def single(e, text, level):
        return dict(t=e["t"], span=clock(e["t"]), level=level, text=text, names=[], suffix="", card="",
                    source_rows=[e["source_row"]] if "source_row" in e else [])
    rows = []
    # A path returning on one host does not close an outage on the other hosts.
    closed = {obj: {b for _, b in spans if b} for obj, spans in incident["history"].items()}
    changes, storage = [], defaultdict(list)
    for e in sorted(events, key=lambda e: e["t"]):
        if e.get("ticket"):
            if e["role"] == "ticket":
                text = e["label"]
                onset = parts["first_signal"]
                if onset and e["t"] >= onset:
                    text += ", %s after the first signal" % _span((e["t"] - onset).total_seconds())
                rows.append(dict(single(e, text, "info"), word="Ticket"))
            elif e["label"].startswith("Work note in "):
                rows.append(single(e, "Work note: " + _redact(e["message"]), "info"))
            continue
        for kind, name, back in _story_changes(e):
            ends = closed.get((e["vcenter"], kind, name), ())
            if back and kind == "vm" and e["kind"].startswith("alarm:") and e["t"] not in ends:
                kind = "alarm"
            elif back and kind in ("host", "datastore") and e["kind"].startswith("alarm:"):
                # A green alarm can corroborate a recovery, but another condition can still be open.
                if not any(abs(end - e["t"]) <= gap for end in ends):
                    kind = "alarm"
            elif kind == "datastore" and back and e["t"] not in ends:
                continue
            changes.append((e, kind, name, back))
            if kind == "datastore" and e["host"]:
                storage[e["vcenter"], e["host"], back].append(e["t"])
            if kind == "path":
                paths_at[e["vcenter"], e["host"], back].append(e["t"])

    # Attach a host storage alarm to its datastore line before measuring gaps on either line.
    for e, kind, name, back in changes:
        if kind == "alarm" and re.search(r"connect to storage|storage connectivity|all paths down", e["kind"], re.I):
            if any(abs(at - e["t"]) <= gap for at in storage[e["vcenter"], e["host"], back]):
                kind, name = "datastore", None
            elif any(abs(at - e["t"]) <= gap for at in paths_at[e["vcenter"], e["host"], back]):
                kind, name = "path", e["host"]          # 1.116.6: a green storage alarm joins its host's paths back
        add(kind, name, back, e)
    path_groups = [g for g in groups if g["kind"] == "path"]

    # 1.116.6: the same hosts' APD and datastore loss (or their return) in one burst are one line,
    # and the path redundancy those hosts lost (or got back) with it is part of that line.
    turns = defaultdict(list)          # every storage change, by vCenter: a pair never crosses a change the other way
    for g in groups:
        if g["kind"] in STORAGE_KINDS:
            turns[g["events"][0]["vcenter"]].extend((x["t"], g["back"]) for x in g["events"])

    def unbroken(vc, back, a, b):
        a, b = min(a, b), max(a, b)
        return not any(a < t < b and direction != back for t, direction in turns[vc])

    def near(g, p, before=burst):
        first, vc = g["events"][0]["t"], p["events"][0]["vcenter"]
        span = (min(first, p["events"][0]["t"]), max(g["last"], p["last"]))
        return (g["back"] == p["back"] and g["events"][0]["vcenter"] == vc
                and p["events"][0]["t"] - before <= first <= p["last"] + burst and unbroken(vc, p["back"], *span))

    def hosts_of(g):
        return {x["host"] for x in g["events"] if x.get("host") and not x["kind"].startswith("alarm:")}
    for p in path_groups:
        hosts = set(p["names"])
        for g in groups:
            if g.get("into") or g is p or not hosts:
                continue
            # 1.116.6 (review): a loss pairs only on the same hosts, so a host with APD alone keeps its own paths line.
            # A datastore's return is recorded once, at the end of its outage: it pairs with the hosts' paths back.
            same = hosts_of(g) <= hosts if p["back"] else hosts_of(g) == hosts
            if g["kind"] == "datastore" and near(g, p) and g["names"] and same:
                g["into"], p["stores"] = p, p.get("stores", []) + [g]
    for p in path_groups:
        if not p.get("stores"):
            continue                   # only a paired line prints the redundancy that came with it
        for g in groups:
            if not g.get("into") and g["kind"] == "redundancy" and near(g, p, datetime.timedelta(0)) and \
                    set(g["names"]) <= set(p["names"]):
                g["into"], p["redundancy"] = p, g

    reset_before = set()
    for g in groups:
        if g.get("into"):
            continue
        joined = g.get("stores", []) + ([g["redundancy"]] if g.get("redundancy") else [])
        core = None
        if joined:
            # The line's time is the loss (or return) itself; the alarms and redundancy that came with it only add words.
            core = sorted(x["t"] for x in g["events"] + [x for d in g.get("stores", []) for x in d["events"]]
                          if not x["kind"].startswith("alarm:"))
            g = dict(g, events=sorted(g["events"] + [x for j in joined for x in j["events"]], key=lambda x: x["t"]),
                     last=max([g["last"]] + [j["last"] for j in joined]))
        kind, back, es = g["kind"], g["back"], g["events"]
        e, names = es[0], sorted(g["names"])
        first, last = (core[0], core[-1]) if core else (e["t"], g["last"])
        span = clock(first) + ("-" + clock(last) if clock(first) != clock(last) else "")
        n = len(names)
        stores = sorted({x["datastore"] for x in es if x.get("datastore")})
        hosts = sorted({x["host"] for x in es if x.get("host")})
        suffix = ""
        cards = [card_matches[id(x)] for x in es if id(x) in card_matches]
        card = next(iter(cards), "")
        if kind == "path" and g.get("stores"):
            # 1.116.6: "All paths down (APD) and datastore X lost on 5 hosts" / "Datastore X back on 8 hosts"
            lost = sorted({name for d in g["stores"] for name in d["names"]})
            what = ("datastore " if len(lost) == 1 else "datastores ") + ", ".join(lost)
            if back:
                prefix = what[0].upper() + what[1:] + " back on " + _count(n, "host")
            elif any(x["kind"] == "apd" for x in es):
                prefix = "All paths down (APD) and %s lost on %s" % (what, _count(n, "host"))
                card = next((card_matches[id(x)] for x in es if x["kind"] == "apd" and id(x) in card_matches), card)
                timeouts = [x["t"] for x in es if x["type"].endswith(".apd.timeout")]
                if timeouts:
                    suffix = " - I/O failed from " + clock(min(timeouts))
            else:
                prefix = "Storage paths down and %s not accessible on %s" % (what, _count(n, "host"))
            if g.get("redundancy"):
                suffix += "; path redundancy " + ("back" if back else "lost") + " too"
        elif kind in ("redundancy", "path"):
            prefix = ("Storage path redundancy " + ("restored" if back else "lost") if kind == "redundancy" else
                      "Storage paths " + ("restored" if back else "down")) + " on " + _count(n, "host")
            if kind == "redundancy" and stores:
                suffix = " (to %s)" % ", ".join(stores)
        elif kind == "datastore":
            prefix = _count(n, "datastore") + (" accessible again" if back else " not accessible")
            if any(x["kind"] == "apd" for x in es) and not back:
                suffix = " - all paths down (APD)"
                card = next((card_matches[id(x)] for x in es if x["kind"] == "apd" and id(x) in card_matches), card)
                timeouts = [x["t"] for p in path_groups if p["back"] == back for x in p["events"]
                            if x["type"].endswith(".apd.timeout") and x["vcenter"] == e["vcenter"]
                            and x["datastore"] in names and first <= x["t"] <= last + gap]
                if timeouts:
                    suffix += ", I/O failed from " + clock(min(timeouts))
        elif kind == "pdl":
            devices = sorted({x["device"] for x in es if x.get("device")})
            if stores and hosts:
                prefix = "Datastore %s lost for good (PDL) on %s" % (", ".join(stores), _count(len(hosts), "host"))
            elif devices or stores:
                names = stores or devices
                prefix = _count(len(names), "datastore" if stores else "device") + " lost for good (PDL)"
                if hosts:
                    suffix = " (on %s)" % ", ".join(hosts)
            else:
                prefix = "Storage device lost for good (PDL)" + (" on " + _count(n, "host") if hosts else "")
        elif kind == "host":
            prefix = _count(n, "host") + (" connected again" if back else " not responding")
            failed = sorted({x["host"] for x in es if x["type"].endswith("DasHostFailedEvent")})
            if failed:
                suffix = " - HA declared %s failed" % ("both" if n == 2 and names == failed else ", ".join(failed))
            if back and n > 1 and clock(first) != clock(last):
                names = ["%s (%s)" % (name, clock(g["names"][name])) for name in names]
        elif kind in ("vm", "reset", "restart", "failed_restart"):
            label = {"vm": "back up" if back else "reported down", "reset": "reset by HA",
                     "restart": "restarted by HA", "failed_restart": "HA could not restart"}[kind]
            if kind == "reset":
                keys = {(e["vcenter"], name) for name in names}
                if keys and keys <= reset_before:
                    label += " again"
                reset_before.update(keys)
            prefix = _count(n, "VM") + " " + label
            if kind == "failed_restart":
                stores = sorted(set(stores) | {obj[2] for name in names for obj in incident["lost_files"][e["vcenter"], name]})
            places = hosts if kind == "restart" else stores if kind == "failed_restart" else []
            if places:
                suffix = " (on %s)" % ", ".join(places)
        elif kind == "alarm":
            prefix = "Alarms " + ("green" if back else e["to"]) + " on " + _count(n, "object")
            suffix = " (" + ", ".join(sorted({x["kind"][6:] for x in es})) + ")"
        else:
            prefix = kind + " on " + _count(n, "object")
        colors = {x["to"] for x in es if x["kind"].startswith("alarm:")}
        if colors and kind != "alarm":
            suffix += "; alarms " + ", ".join(sorted(colors))
        row = dict(t=first, span=span, text="%s: " % prefix, names=names, suffix=suffix,
                   card=card, source_rows=[x["source_row"] for x in es],
                   level=max((x["level"] for x in es), key=RANK.get), permanent=kind == "pdl")
        scope = {"reset": "vm", "restart": "vm", "failed_restart": "vm", "pdl": "host" if hosts else "datastore",
                 "path": "host", "redundancy": "host"}.get(kind, kind)
        for name in g["names"]:
            references.setdefault((e["vcenter"], scope, name), row)
        for d in g.get("stores", []):
            for name in d["names"]:
                references.setdefault((e["vcenter"], "datastore", name), row)
        if kind == "pdl":
            for name in stores:
                references.setdefault((e["vcenter"], "datastore", name), row)
        rows.append(row)
    for obj, at in sorted(parts["observed_starts"]):
        if at < read or obj not in parts["still_keys"]:
            row = single({"t": at}, obj[2] + " " + _down_by(clock(at)), "critical")
            if obj not in references or at < references[obj]["t"]:
                references[obj] = row
            rows.append(row)
    # Within a displayed minute keep the restoration lines together before the remaining permanent losses.
    rows.sort(key=lambda r: (r["t"].replace(second=0, microsecond=0), r.get("permanent", False), r["t"]))
    if parts["still"]:
        row = dict(single({"t": read}, "Still down: ", "down"), names=parts["still"], suffix=" - see Down now")
        for obj in parts["still_keys"]:
            references.setdefault(obj, row)
        rows.append(row)
    start = parts["start"] or parts["first_signal"]
    finish = parts["last_signal"] if start else None
    window = {}
    if start and finish:
        window = dict(start=_moment(start, loc), end=_moment(finish, loc), duration=_span((finish - start).total_seconds()),
                      still=", ".join(parts["still"]))
    day = ""
    for i, row in enumerate(rows, 1):
        t = row.pop("t")
        row.pop("permanent", None)
        level = row["level"]
        row.setdefault("word", {"ok": "Recovered", "down": "Down now"}.get(level, WORD.get(level)))
        row.update(time=_moment(t, loc), anchor="outage-change-%d" % i)
        divider = row["time"]["day"]
        row["divider"] = (divider + (" - the review" if level == "down" else "")) if day != divider or level == "down" else ""
        row["window"] = bool(window and start <= t <= finish and level != "down")
        day = divider
    for issue in parts["issues"]:
        obj = issue.pop("object_key")
        row = references.get(obj)
        if not row and obj[1] == "vm":
            row = next((references[root] for root in sorted(incident["vm_roots"].get((obj[0], obj[2]), ()))
                        if root in references), None)
        issue["anchor"] = row["anchor"] if row else ""
        issue["since"] = "in Order of events at " + row["time"]["clock"][:5] if row else ""
    return dict(rows=rows, window=window, grouped=True), start, finish


def _usage_or_gray(e, alarms):
    return e["kind"].startswith("alarm:") and (
        _usage_alarm(e["kind"][6:], alarms) or any(str(e.get(k)).lower() in ("gray", "grey") for k in ("from", "to")))


def _chart_lane(e):
    if e["lane"] in ("servicenow", "act"):
        return "tickets"
    if e["type"].startswith(("esx.problem.storage", "esx.clear.storage", "esx.problem.scsi",
                              "esx.clear.scsi", "esx.problem.vmfs", "esx.clear.vmfs")) or any(s in e["kind"].lower() for s in ("storage", "datastore")):
        return "storage"
    return "hosts" if e["lane"] == "host" or _scope(e) == "host" else "vms"


def _chart_view(events, related, affected, alarms, left, right, start, finish, still, loc, everything, summarize_noise=True):
    """Build each view on its own scale, with local calendar ticks and accessible mark descriptions."""
    def pos(t):
        return _pct(t, left, right)

    span = (right - left).total_seconds()
    step = next((m for m in STEPS_MIN if m >= max(10 if not everything else 30, span / (60 * 13))), STEPS_MIN[-1])
    # Align ticks to the local clock. Walk in UTC through clock changes and insert each actual local midnight.
    tick = left.replace(second=0, microsecond=0)
    tick += datetime.timedelta(minutes=(-int(loc(tick).utcoffset().total_seconds() / 60 + tick.timestamp() / 60)) % step)
    moments = {left}
    while tick <= right:
        if tick >= left:
            moments.add(tick)
        tick += datetime.timedelta(minutes=step)
    midnight = loc(left).replace(hour=0, minute=0, second=0, microsecond=0)
    while midnight.astimezone(UTC) <= right:
        if midnight.astimezone(UTC) >= left:
            moments.add(midnight.astimezone(UTC))
        midnight += datetime.timedelta(days=1)
    ticks = []
    for t in sorted(moments):
        local = loc(t)
        new_day = (local.hour, local.minute) == (0, 0)
        if ticks and pos(t) - ticks[-1]["x"] < 5:
            if not new_day:
                continue
            if len(ticks) == 1:
                ticks.append(dict(x=pos(t), clock="", date="", midnight=True))
                continue
            ticks.pop()
        ticks.append(dict(x=pos(t), clock=local.strftime("%H:%M"), date=local.strftime("%a %d %b") if not ticks or new_day else "", midnight=new_day))

    def region(t):
        return -1 if start and t < start else 1 if finish and t > finish else 0

    bins, groups = defaultdict(list), defaultdict(list)
    latest = {}
    for e in sorted(events, key=lambda e: e["t"]):
        t = e["t"]
        if not left <= t <= right:
            continue
        if summarize_noise and _usage_or_gray(e, alarms):
            if everything:
                bins[t.replace(minute=t.minute // 10 * 10, second=0, microsecond=0)].append(e)
            continue
        lane = _chart_lane(e)
        identity = (lane, e["vcenter"], e["label"], region(t))
        group = latest.get(identity)
        if group is None or (t - group[-1]["t"]).total_seconds() > 120:
            group = latest[identity] = []
            groups[lane].append(group)
        group.append(e)

    lanes = []
    for lane, name in (("storage", "Storage"), ("hosts", "Hosts"), ("vms", "HA and VMs"), ("tickets", "Tickets")):
        marks = []
        last_label, last_region = -100, None
        for group in groups[lane]:
            e = group[0]
            t = e["t"]
            x = pos(t)
            about = any(related(event) for event in group)
            objs = _objects(group, "datastore", "host", "vm") or _objects(group, "where")
            line = "%s %s: %s%s%s" % (loc(t).strftime("%H:%M:%S"), WORD[e["level"]], e["label"],
                                        " (x%d)" % len(group) if len(group) > 1 else "", " - " + objs if objs else "")
            if marks and last_region == region(t) and x - marks[-1]["x"] < 1.8:
                m = marks[-1]
                m["count"] += 1
                m["tip"] += "\n" + line
                m["rel"] |= everything and about
                if RANK[e["level"]] > RANK[m["level"]]:
                    m["level"] = e["level"]
            else:
                label = loc(t).strftime("%H:%M") if x - last_label >= 7.5 else ""
                if label:
                    last_label = x
                marks.append(dict(x=x, count=1, level=e["level"], rel=everything and about,
                                  tip=_moment(t, loc)["tip"] + "\n" + line, label=label))
            last_region = region(t)
        lanes.append(dict(key=lane, name=name, marks=marks, count=len(groups[lane])))
    if everything:
        marks = []
        top = max(map(len, bins.values()), default=1)
        for t, group in sorted(bins.items()):
            names = {(e["vcenter"], e["vm"] or e["entity"]) for e in group}
            n = len(names & affected)
            until = t + datetime.timedelta(minutes=10)
            tip = "%s\nUntil %s: %d usage or gray alarm changes, %d affected VMs." % (
                _moment(t, loc)["tip"], loc(until).strftime("%H:%M %Z"), len(group), n)
            marks.append(dict(x=pos(t), width=(pos(until) - pos(t)) * .92, height=4 + int(22 * len(group) / top), rel=bool(n), tip=tip))
        lanes.append(dict(key="noise", name="Usage and gray alarms", count=sum(map(len, bins.values())), marks=marks))
    box = {}
    if start and finish and start <= right and finish >= left:
        box = dict(x=pos(start), end=pos(finish), width=pos(finish) - pos(start), still=still,
                   label="Outage %s to %s" % (loc(start).strftime("%H:%M"), loc(finish).strftime("%H:%M")))
    return dict(key="all" if everything else "out", ticks=ticks, lanes=lanes, window=box,
                range="%s to %s" % (loc(left).strftime("%a %H:%M"), loc(right).strftime("%a %H:%M")))


def _pattern_cards(events, current, opts, read, loc, card_matches=None):
    import yaml
    path = os.path.join(os.path.dirname(__file__), "../../roles/vmware_vm/files/outage_patterns.yml")
    with open(path, encoding="utf-8") as source:
        patterns = yaml.safe_load(source)
    cards = []
    teams = {"storage": "your storage team", "network": "your network team", "vmware": "your VMware team",
             "backup": "your backup team", "owner": "the VM's owner"}
    for pattern in patterns:
        matches = [e for e in events if e["type"] in pattern["types"] or
                   (e["kind"].startswith("alarm:") and e["role"] == "problem" and
                    any(term in e["kind"][6:].lower() for term in pattern.get("alarms", [])))]
        if card_matches is not None:
            for event in matches:
                card_matches.setdefault(id(event), pattern["title"])
        live = []
        if pattern.get("current"):
            live = [obj[2] for obj, c in current.items() if c["kind"] == pattern["current"]]

        if not matches and not live:
            continue
        first = matches[0]["t"] if matches else read
        last = matches[-1]["t"] if matches else read
        seen_objects = []
        for event in matches:
            obj = dict(event)
            scope = _scope(event)
            if event.get("entity") and not obj.get(scope):
                obj[scope] = event["entity"]
            seen_objects.append(obj)
        seen = _objects(seen_objects, "datastore", "vm", "host", "object") or _objects(matches, "where")
        when = loc(first).strftime("%d %b %H:%M:%S %Z")
        if last > first:
            when += " to " + loc(last).strftime("%d %b %H:%M:%S %Z")
        if live:
            seen = "; ".join(filter(None, [seen, _compact(live) + " at the review (" + loc(read).strftime("%d %b %H:%M %Z") + ")"]))
        escalate = ", then ".join(str(opts.get("outage_escalate_" + team) or "").strip() or teams[team] for team in pattern["esc"])
        # 1.116.6: the AAR's counted form ("ds_shared_01 and 14 hosts"); the names are in Order of events
        counted = []
        for key, word in (("datastore", "datastore"), ("host", "host"), ("vm", "VM")):
            pairs = sorted({(o.get("vcenter", ""), o[key]) for o in seen_objects if o.get(key)})
            if pairs:
                centers = defaultdict(set)
                for vc, name in pairs:
                    centers[name].add(vc)
                names = ["%s (%s)" % (name, vc) if len(centers[name]) > 1 and vc else name for vc, name in pairs]
                counted.append(", ".join(names) if len(names) <= 2 else _count(len(names), word))
        if live:
            counted.append(_count(len(live), "object") + " at the review")
        cards.append((first, dict(title=pattern["title"], means=pattern["means"], causes=pattern["causes"],
                                  checks=pattern["checks"], seen=seen, when=when, escalate=escalate,
                                  seen_short=" and ".join(filter(None, [", ".join(counted[:-1]), counted[-1]])) if counted else seen,
                                  when_short=(loc(first).strftime("%H:%M:%S") + (" to " + loc(last).strftime("%H:%M:%S") if last > first else ""))
                                  if loc(first).date() == loc(last).date() else when)))
    return [card for _, card in sorted(cards, key=lambda pair: pair[0])]


def outage_timeline(events, tickets=None, opts=None):
    """What vCenter (events: site_vmware_outage_events) and ServiceNow (tickets: site_outage_tickets) recorded between
    opts.begin and opts.end -> {title, numbers, boxes, cards, sections (an email), web_sections (the website: with the
    chart), sheet (the workbook), lines (the job's output), aar}. opts: begin, end, instance and table (the ServiceNow address and table, for the links to the incidents), time_zone, low_seconds, review,
    tickets_asked, tickets_error, notes_error, capped, report_on, min_severity, job."""
    o = dict(opts or {})
    review = o.get("review", True)
    if not review:                           # 1.116.0: a scheduled run's time line is 1.118.4's, word for word
        return _v1184.outage_timeline(events, tickets, opts)
    unread_centers = [s["name"] for s in o.get("vcenters") or [] if s.get("incomplete") or s.get("error")]
    o["triggers"] = _review_triggers(o)
    begin, end = _time(o.get("begin")), _time(o.get("end"))
    if begin is None or end is None or end <= begin:
        return _unsafe({})
    tz, _note = _tm.zone(o.get("time_zone") or "")
    try:
        low_seconds = max(0.0, float(o.get("low_seconds", 120)))
    except (TypeError, ValueError):
        low_seconds = 120.0

    def loc(t):
        return t.astimezone(tz or UTC)

    def zname(t):
        return loc(t).strftime("%Z") or "UTC"

    # 1.79.12: an alarm that went back to normal in the window is shown with its raise, whenever it was raised (the read looks back
    # vm_watch_review_lookback_hours): the window then starts at the raise
    all_vc = _vcenter_events(events, o.get("datastores") if review else None)
    if review:
        read = _time(o.get("read_at")) or end
        read_in_window = begin <= read <= end
        o["roots"], empty_read, o["recorded_vms"] = _review_roots(o, all_vc, begin, end, read_in_window)
        o = _review_inventory(o, all_vc)
        if empty_read.get("ambiguous_host"):
            o["triggers"] = []
        if read_in_window:
            o["down_now"] = o["triggers"]
        else:
            # Compare the live read with the window's roots after resolving inventory identities.
            root_objects = {_finding_object(c) for c in o["roots"]}
            o["down_now"] = [c for c in o["triggers"] if _finding_object(c) in root_objects]
            o["triggers"] = []
        ops_center = str(o.get("outage_ops_center") or "").strip() or "NOC"
        by_name = "" if o.get("events_not_read") else " Its event history is read by name."
        judged = ["%s %s (%s): not matched to a current object.%s" %
                  (c["kind"].title(), c["object"], c["vcenter"], by_name) for c in o["roots"] if c["name_only"]]
        if o.get("events_not_read"):
            judged.append("vCenter events not read.")
    begin_asked = begin
    pre = _pre_raises(all_vc, begin, end)
    if pre:
        begin = min(e["t"] for e in pre).replace(second=0, microsecond=0)
    days = loc(begin).date() != loc(end).date()

    def hm(t, sec=False):
        return loc(t).strftime(("%d %b " if days else "") + ("%H:%M:%S" if sec else "%H:%M"))

    def full(t):
        return loc(t).strftime("%Y-%m-%d %H:%M ") + zname(t)

    pre_ids = {id(e) for e in pre}
    vc = [e for e in all_vc if begin <= e["t"] <= end and (e["t"] >= begin_asked or id(e) in pre_ids)]
    alarm_events = vc
    instance = o.get("instance") or ""
    tk, before, causes = _ticket_events(tickets, begin_asked, end, instance, o.get("table") or "", repeats=review)
    if review:
        # 1.116.0: export the source before folding restarts, repeat events or short alarms.
        raw = sorted(vc + tk, key=lambda e: e["t"])
        for n, event in enumerate(raw, 1):
            event["source_row"] = n
        tk = [e for e in tk if not e.get("repeat")]
        alarms = [str(a).lower() for a in o.get("noise_alarms", ["CPU usage", "memory usage", "disk usage"]) if a]
        summarize_noise = o.get("noise", True)
        narrative, noise = _review_rows(vc, low_seconds, alarms, o["roots"])
        if summarize_noise:
            vc = narrative
        incident = _root_history(vc, o)
        history, chain = incident["history"], incident["chain"]
        facts, story_events = incident["facts"], incident["events"]
        if not summarize_noise:
            # The noise summary counts affected objects throughout the window, including after recovery.
            # Use the source row because a short flap's noise records carry an added label.
            folded = {e["source_row"] for e in noise}
            selected = {e["source_row"] for e in story_events}
            for e in vc:
                if e["source_row"] not in folded or e["source_row"] in selected:
                    continue
                vm = _vm_key(e)
                if vm:
                    belongs = vm in incident["affected"]
                else:
                    kind = _scope(e)
                    name = e["entity"] or e.get(kind)
                    belongs = (e["vcenter"], kind, name) in incident["roots"]
                if belongs:
                    story_events.append(e)
            story_events.sort(key=lambda e: e["t"])
            noise = []
        selected_ids = {id(e) for e in story_events}
        if pre and incident["roots"]:
            begin = min([begin_asked] + [e["t"].replace(second=0, microsecond=0) for e in pre if id(e) in selected_ids])
            days = loc(begin).date() != loc(end).date()
        # Keep the matching closure of a root signal in the event appendix even after the host reconnected.
        appendix_ids, pending = selected_ids.copy(), set()
        for e in vc:
            key = _pair_key(e)
            if e["role"] in ("problem", "down"):
                if id(e) in selected_ids:
                    pending.add(key)
                else:
                    pending.discard(key)
            elif e["role"] in ("up", "clear") and key in pending:
                appendix_ids.add(id(e))
                pending.remove(key)
        appendix = [e for e in vc if id(e) in appendix_ids]
        vc = story_events
        if chain or incident["current"]:
            alarm_events = story_events
        stamp_review = lambda t: loc(t).strftime("%d %b %H:%M:%S %Z")
        incident["vm_event_tier"] = _vms_with_events(raw, incident, alarms)
        parts = _review_parts(incident, o, begin, end, stamp_review)  # the window with its look-back
        general = parts["title"]
        vm_count, host_count = len(parts["affected_keys"]), parts["host_count"]
        start_at, finish_at = parts["start"], parts["finish"]
        noise_rows = _noise_rows(noise, raw, parts["affected_keys"], start_at, finish_at or end, begin, end, stamp_review)
        vm_keys = incident["affected"]
        correlated = _correlated_ticket_numbers(tickets, incident, o)
        requested = {str(n).strip().casefold() for n in o.get("review_tickets") or []}
        own_numbers = correlated | {k["number"] for k in tickets or [] if isinstance(k, dict)
                                    and k.get("number") and k["number"].casefold() in requested}

        def related_event(e):
            if e.get("ticket"):
                return e["ticket"] in own_numbers
            return id(e) in selected_ids

        tk = [e for e in tk if related_event(e)]
        zones_seen = {zname(e["t"]) for e in raw}
        groups = _event_groups(appendix + tk, start_at, parts["first_back"], finish_at, stamp_review,
                               zone=next(iter(zones_seen)) if len(zones_seen) == 1 else "")
        key_events = story_events + [e for e in tk if e["role"] in ("ticket", "resolved")]
        key_rows = [g["row"] for g in _event_groups(key_events, start_at, parts["first_back"], finish_at, stamp_review, key=True)]
        before = [n for n in before if n in own_numbers]
        read = _time(o.get("read_at")) or end
        card_matches = {}
        # A capacity alarm on a reviewed store during its loss can supply a pattern card from the noise bars.
        usage_raises = [e for e in raw if id(e) not in selected_ids and e["kind"].startswith("alarm:") and e["role"] == "problem"
                        and _usage_alarm(e["kind"][6:], alarms)
                        and any(a <= e["t"] and (not b or e["t"] <= b)
                                for a, b in history.get((e["vcenter"], "datastore", e["datastore"]), ()))
                        and not any(str(e.get(k)).lower() in ("gray", "grey") for k in ("from", "to"))]
        patterns = _pattern_cards(sorted(story_events + usage_raises, key=lambda e: e["t"]), incident["current"], o, read, loc, card_matches)
        joined = [e for e in appendix if id(e) not in selected_ids and e["kind"].startswith("alarm:") and e["role"] == "clear"]
        story, window_start, window_end = _story(story_events + joined + tk, parts, incident, read, loc, card_matches)
        first_signal = parts["first_signal"] or window_start or begin
        left = max(begin, first_signal - datetime.timedelta(minutes=12))
        right = min(end, (window_end or end) + datetime.timedelta(minutes=18))
        views = [_chart_view(story_events + tk, related_event, vm_keys, alarms, left, max(right, left + datetime.timedelta(seconds=1)),
                             window_start, window_end, _compact(parts["still"]), loc, False, summarize_noise),
                 _chart_view(raw, related_event, vm_keys, alarms, begin, end, window_start, window_end, _compact(parts["still"]), loc, True, summarize_noise)]
    runs, unmatched = _alarm_runs(alarm_events)
    if review:
        for r in runs:
            r["usage"] = _usage_alarm(r["alarm"], alarms)
    vc, restarted = _restarts(vc)
    if review:
        downs = sorted(((name, a, b, vc_name) for (vc_name, typ, name), intervals in history.items() if typ == "host"
                        for a, b in intervals), key=lambda d: d[1])
    else:
        vc, downs = _pairs(vc, low_seconds)
    evs = _same(sorted(vc + tk, key=lambda e: (e["t"], [x[0] for x in LANES].index(e["lane"]))))
    for i, e in enumerate(evs):
        e["num"] = i + 1
        e["what"] = e["label"] + ((" (x%d)" % e["n"]) if e["n"] > 1 else "")
    subject = None

    # the numbers
    signals = [e for e in evs if e["lane"] in ("vcenter", "host") and e["level"] in BAD]
    if review:
        group_number = {source: n for n, g in enumerate(groups, 1) for source in g["sources"]}
        signals = [dict(e, num=group_number[e["source_row"]], what=e["label"]) for e in facts if e["level"] in BAD]
    first = signals[0] if signals else None
    first_source = LANE[first["lane"]] if first else ""
    if review and parts["first_watch"]:
        at, (_, typ, name) = parts["first_watch"]
        label = "Host not responding" if typ == "host" else "Datastore not accessible"
        first = dict(t=at, label=label, what=label, where=name, level="critical", num=None)
        first_source = "the watch's run"
    first_bad = next((e for e in signals if e["level"] in ("critical", "warning")), None)
    tickets_opened = [e for e in evs if e["role"] == "ticket" and (not review or e["ticket"] in own_numbers)]
    if correlated:
        tickets_opened = [e for e in tickets_opened if e["ticket"] in correlated]
    # the outage's ticket: the first one opened after the first signal (one added by number may be older)
    opened = next((e for e in tickets_opened if first and e["t"] >= first["t"]), tickets_opened[0] if tickets_opened else None)
    resolved = [e for e in evs if e["role"] == "resolved" and (not review or opened and e["ticket"] == opened["ticket"])]
    to_ticket = (opened["t"] - first["t"]).total_seconds() if first and opened and opened["t"] >= first["t"] else None
    # 1.112.0 (Codex P1): the root-cause text of the outage's own ticket - the one measured, else one about a host of the outage's
    # signals; another ticket of the window (another host's disk) never gives it
    own_hosts = set(e["where"] for e in signals if e["where"])
    own_tickets = [e["ticket"] for e in ([opened] if opened else []) + tickets_opened if e["where"] in own_hosts or
                   (e is opened and not e["where"])]          # a ticket that names no host: only the measured one
    if review:
        own_tickets = [e["ticket"] for e in ([opened] if opened else []) + tk]
    cause = next((causes[n] for n in own_tickets if n in causes), "")
    current_hosts = {(vc, name) for vc, typ, name in incident["current"] if typ == "host"} if review else set()
    unknown_duration = {d for d in downs if review and d[2] is None and (d[3], d[0]) not in current_hosts}
    still = [d for d in downs if d[2] is None and d not in unknown_duration]
    observed_downs = {d for d in downs if ((d[3], "host", d[0]), d[1]) in incident["observed_starts"]}

    def host_loss(d):
        when = hm(d[1], True)
        return _down_by(when) if d in observed_downs else "not responding " + when

    cleared_runs = [r for r in runs if r["cleared"] is not None]
    open_alarms = [r for r in runs if r["cleared"] is None]
    fallback = set((r["vcenter"], r["vm"] or r["where"]) for r in cleared_runs if r["vm"] or r["where"])
    if review:
        focus = {(vc, name) for vc, _, name in incident["roots"]}
        for e in evs:
            e["cls"], e["related"] = _classify(e), True
        names = sorted({name for _, name in focus})
        subject = {"names": names, "keys": focus, "text": _listed(names, 4), "note": ""}
    else:
        subject = _relate(evs, downs, fallback, set(r["key"] for r in cleared_runs))
    # 1.90.0: the lead-up - from the first problem about the subject itself (a host or VM of it: not its cluster or datastores) in the
    # LEAD_S before its first critical event, to that event
    own = lambda e: bool(_about(e) & subject["keys"])      # noqa: E731
    crit = next((e for e in evs if e["lane"] in ("vcenter", "host") and e["level"] == "critical" and own(e)), None)
    lead_up, lead_note, lead_ns = None, "", set()
    if crit is not None:
        ahead = [e for e in evs if e["lane"] in ("vcenter", "host") and e["level"] in BAD and own(e)
                 and 0 < (crit["t"] - e["t"]).total_seconds() <= LEAD_S]
        word = "alarm" if str(crit.get("kind") or "").startswith("alarm:") else "event"
        if ahead:
            lead_up = (ahead[0]["t"], crit)
            lead_ns = set(e["num"] for e in ahead)          # 1.104.0: the lead-up's own problems (what ACT reads first)
        else:
            lead_note = "No lead-up drawn: nothing about %s came in the %s before its first critical %s (#%d, %s)." % (
                crit["where"] or subject["text"], _span(LEAD_S), word, crit["num"], loc(crit["t"]).strftime("%H:%M"))
    dup = set(w for w, _a, _b, v in downs if len(set(x[3] for x in downs if x[0] == w)) > 1)       # one name on two vCenters: told apart
    shown = lambda d: "%s (%s)" % (d[0], (d[3] or "?").split(".")[0]) if d[0] in dup else d[0]      # noqa: E731
    spans = [(shown(d), d[1], d[2] or end, d[2] is None) for d in downs if d not in unknown_duration and d[1] <= end]
    longest = max(spans, key=lambda d: (d[2] - d[1]).total_seconds()) if spans else None
    down_s = (longest[2] - longest[1]).total_seconds() if longest else 0
    hosts_down = sorted({shown(d) for d in downs})
    last_bad = max([e["t"] for e in evs if e["lane"] in ("vcenter", "host") and e["level"] in ("critical", "warning")] or [begin])
    goods = [e for e in evs if e["lane"] in ("vcenter", "host") and e["level"] == "ok" and e["t"] >= last_bad]
    back = None if still or not signals else (goods[-1] if goods else None)
    if review and general:
        closure = next((e for e, _, _, clear in reversed(chain) if clear and e["t"] == parts["finish"]), None)
        back = dict(closure, what=closure["label"], num=group_number[closure["source_row"]]) if closure else None
    if review and (incident["current"] or any(r[-1][1] is None for r in history.values() if r)):
        back = None
    # 1.79.12: everything had already cleared at the review - the alarms that raised it went back to normal and nothing is down
    # (alarms only: a host that was lost, an HA restart or a storage problem is the window's own story, not this one)
    live = [c for c in (o.get("triggers") or []) if isinstance(c, dict) and c.get("summary")]      # what the watch found now: it comes first
    cleared_only = (bool(cleared_runs) and not open_alarms and not spans and not live
                    and all(str(e.get("kind") or "").startswith("alarm:") for e in signals))
    if review:
        cleared_only = cleared_only and not general and not chain and not any(not e["kind"].startswith("alarm:") for e in raw if e["lane"] in ("host", "vcenter") and e["level"] in BAD)
    try:
        blip_s = max(0.0, float(o.get("min_seconds", 300)))
    except (TypeError, ValueError):
        blip_s = 300.0
    blip = bool(cleared_only and not unmatched and all(r["usage"] for r in cleared_runs) and all((r["cleared"] - r["raised"]).total_seconds() <= blip_s for r in cleared_runs))
    r_from = min(r["raised"] for r in cleared_runs) if cleared_runs else None
    r_to = max(r["cleared"] for r in cleared_runs) if cleared_runs else None
    c_names = sorted(set(r["vm"] or r["where"] for r in cleared_runs if r["vm"] or r["where"]))
    c_text = ", ".join(c_names[:3]) + (" and %d more" % (len(c_names) - 3) if len(c_names) > 3 else "")
    c_alarms = sorted(set(r["alarm"] for r in cleared_runs))
    # what triggered the report, always (the watch's own conditions, else the alarms that had cleared, else the launch)
    trig = []
    for c in (o.get("triggers") or [])[:8]:
        if isinstance(c, dict) and c.get("summary"):
            trig.append("%s: %s" % (str(c.get("severity") or "").upper() or "Found", c["summary"]))
    # 1.90.0: a red alarm still raised at the end of the window is what the review is about - not "already cleared", whatever
    # else (alarms raised and back within a minute) cleared around it
    last_state = {}
    for e in sorted(vc, key=lambda x: x["t"]):
        if str(e.get("kind") or "").startswith("alarm:") and e["role"] in ("problem", "clear"):
            last_state[_alarm_key(e)] = e
    standing = [e for e in last_state.values() if e["role"] == "problem" and e["level"] == "critical"]
    if not read_in_window:
        trig = ["The review was requested for the selected window."]
    if review and not trig and incident["current"]:
        trig = [r[0] + ": " + r[1] + " " + r[2] for r in parts["now"][:8]]
    if not trig and standing:
        for e in sorted(standing, key=lambda x: x["t"])[:6]:
            trig.append("CRITICAL: %s on %s: red since %s, still red at the end of the window" % (e["kind"][6:], e["where"] or "?", hm(e["t"], True)))
    if review and not trig and not general and noise:
        trig = ["The review was asked for. Alarm changes and short flaps are counted below."]
    if not trig and review and parts["open_observation"]:
        trig.append("The watch recorded a loss whose recovery time is unknown.")
    if not trig and (cleared_runs or (spans and not still)):
        for d in [d for d in downs if d[2] is not None][:3]:
            trig.append("%s: %s to %s (%s), back before the review" % (
                d[0], host_loss(d), hm(d[2], True), _span((d[2] - d[1]).total_seconds())))
        for r in sorted((r for r in cleared_runs if not review or not r["usage"]), key=lambda x: x["raised"])[:6]:
            trig.append("%s on %s: raised %s, cleared %s (%s later)" % (r["alarm"], r["vm"] or r["where"] or "?", hm(r["raised"], True), hm(r["cleared"], True),
                                                                        _span((r["cleared"] - r["raised"]).total_seconds())))
        trig.insert(0, "Nothing was down when the watch read vCenter: what raised it had already cleared.")
    if not trig:
        trig = ["Nothing triggered it: the review was asked for (vm_watch_review) and the watch found nothing down or hung when it read vCenter."]
    numbers = {
        "events": len(evs), "first_signal": first["t"].strftime("%Y-%m-%dT%H:%M:%SZ") if first else "",
        "ticket": opened["ticket"] if opened else "", "signal_to_ticket_s": int(to_ticket) if to_ticket is not None else -1,
        "down_s": -1 if unknown_duration & observed_downs and not spans else int(down_s), "hosts_down": hosts_down,
        "still_down": sorted(shown(d) for d in still), "ha_restarted": restarted,
        "back": back["t"].strftime("%Y-%m-%dT%H:%M:%SZ") if back else ""}

    window_text = "%s to %s" % (full(begin), full(end) if days else loc(end).strftime("%H:%M ") + zname(end))
    length = _span((end - begin).total_seconds())
    down_word = ("%s down: %s" % (hosts_down[0], _span(down_s)) if len(hosts_down) == 1 else
                 "%d hosts down: up to %s" % (len(hosts_down), _span(down_s))) if spans else ""

    # the title
    # the headline: the first problem - or a critical one in the same minute (one cause often posts several events)
    # 1.90.0: the first critical event leads when there is one (a warning or a Low before it is the lead-up, not the outage)
    head = first_bad or first
    crit0 = next((e for e in signals if e["level"] == "critical"), None)
    if crit0 is not None:
        near = [e for e in signals if e["level"] == "critical" and 0 <= (e["t"] - crit0["t"]).total_seconds() <= SAME_S]
        # the host's own event says more than the alarm about it
        head = next((e for e in near if e["lane"] == "host"), crit0)
    h_counts, h_scope, h_subject = None, None, None          # 1.82.0: the head of the page (report_head): the name, the times as counts, the cause as the scope
    if not evs:
        title = "Outage review: no outage event in the last %s" % length if not o.get("fixed") else "Outage review: no outage event in the window"
    else:
        headline_parts = []
        if spans:
            headline_parts.append("%s down%s" % (_span(down_s), ", still down" if still else ""))
        if to_ticket is not None:
            headline_parts.append("ticket after %s" % _span(to_ticket))
        elif head and o.get("tickets_asked") and not o.get("tickets_error") and not before:
            headline_parts.append("no ticket")
        lead = ("%s: %s" % (head["where"], _lower_first(head["label"])) if head["where"] else head["label"]) if head else \
            "%d event%s, none a problem" % (len(evs), "" if len(evs) == 1 else "s")
        title = "Outage review: %s%s" % (_cut(lead, 90), ("; " + ", ".join(headline_parts)) if headline_parts else "")
        h_counts = ([[_span(down_s), "down, still down" if still else "down", "critical"]] if spans else []) + \
                   ([[_span(to_ticket), "to the ticket", "warning" if to_ticket > 3600 else "ok"]] if to_ticket is not None else
                    ([["", "no ticket", "warning"]] if "no ticket" in headline_parts else []))
        h_scope = [_cut(lead, 120)]
        h_subject = "Outage review: %s" % _cut(lead, 40)
        if cleared_only:
            title = "Outage review: cleared before the review - %s: %s, raised %s, cleared %s%s" % (
                _cut(c_text or "alarms", 40), _cut(", ".join(c_alarms[:2]), 60), hm(r_from), hm(r_to), " (a short usage blip, Low)" if blip else "")

    if review:
        if general:
            title = general
        elif (raw or o["triggers"] or o.get("review_object")) and not noise:
            title = "Outage review - no matching watch root"
        elif not first and noise:
            title = "Outage review - " + ("a short usage blip, Low" if blip else "folded alarms and short changes")
        elif first and not general:
            title = "Outage review - no confirmed host, datastore or VM outage"
        h_subject, h_scope = title, [window_text]
        numbers.update(still_down=parts["still"], affected_vms=vm_count, affected_hosts=host_count, full_events=len(raw))
    # tiles
    def box(label, value, status, text):
        return {"label": label, "value": value, "status": status, "note": text}

    boxes = [box("Window", length, "info", window_text),
             box("Events", len(evs), "info", "%d on the vCenter and host lanes" % len([e for e in evs if e["lane"] in ("vcenter", "host")])),
             (box("Outage", "Cleared before the review", "ok",
                  "%s: %s to %s (%s)%s" % (c_text, hm(r_from, True), hm(r_to, True), _span((r_to - r_from).total_seconds()),
                                           ". Only usage alarms, each cleared within %s: a blip (Low)" % _span(blip_s) if blip else "")) if cleared_only else
              box("Host down", _span(down_s) if spans else "0", "critical" if spans else "ok",
                 ("%s%s" % (", ".join(hosts_down[:3]) + (" and %d more" % (len(hosts_down) - 3) if len(hosts_down) > 3 else ""),
                            "; still down at the end of the window" if still else "")) if spans else "no host lost in the window")),
             box("VMs restarted by HA", restarted, "warning" if restarted else "ok", "vSphere HA events" if restarted else "none"),
             box("Signal to ticket", _span(to_ticket) if to_ticket is not None else "-",
                 ("warning" if to_ticket > 900 else "ok") if to_ticket is not None else "info",
                 ("%s, %s" % (opened["ticket"], "resolved in the window" if resolved else "not resolved in the window"))
                 if to_ticket is not None else
                 (str(o.get("tickets_error")) if o.get("tickets_error") else
                  "ticket %s was open before the window" % before[0] if before else
                  "ticket opened before the first signal" if opened else "no ticket in the window"))]

    if unknown_duration and not spans:
        boxes[2] = box("Host down", "Unknown", "warning", "Host recovery time unknown: %s" % _listed(hosts_down))
    # cards
    cards = []
    if cleared_only:
        cards.append({"status": "ok", "chip": "Cleared before the review", "meta": "%s to %s %s" % (hm(r_from, True), hm(r_to, True), zname(r_to)),
                      "title": "%s: %s" % (c_text, ", ".join(c_alarms[:2])), "subject": c_text,
                      "next": ("Only usage alarms, each cleared within %s: a blip. Check the guest for a burst (an audit flood, a runaway job) if it repeats. "
                               % _span(blip_s) if blip else "Everything the alarms were about had cleared when the review ran. Read the guest's own log for the stretch above. ")
                              + "The window starts at the first raise (vm_watch_review_lookback_hours)."})
    if first:
        cards.append({"status": first["level"], "chip": "First signal", "meta": "%s %s" % (hm(first["t"], True), zname(first["t"])),
                      "title": first["what"], "subject": first["where"],
                      "next": ("Row %d of the events. " % first["num"] if first["num"] else "") + first_source +
                              (" lane." if first["num"] else ".")})
    if to_ticket is not None:
        cards.append({"status": "warning" if to_ticket > 900 else "ok", "chip": "First signal to ticket", "meta": _span(to_ticket),
                      "title": "%s opened at %s %s" % (opened["ticket"], hm(opened["t"]), zname(opened["t"])), "subject": opened["where"],
                      "next": "Row %d of the events." % group_number[opened["source_row"]]})
        if opened.get("url"):
            cards[-1].update(url=opened["url"], url_text=opened["ticket"])
    elif first:
        why = (str(o.get("tickets_error")) if o.get("tickets_error") else "No ticket of the health workflows was opened in the window."
               if o.get("tickets_asked") else TICKETS_NOT_READ)
        cards.append({"status": "info", "chip": "First signal to ticket", "meta": "-", "title": "No ticket to measure", "next": why})
    if first:
        if still:
            cards.append({"status": "critical", "chip": "Back to normal", "meta": "not yet",
                          "title": "%s still down at the end of the window" % ", ".join(sorted(shown(d) for d in still)[:3]),
                          "next": "Run the review again when the host is back."})
        elif back:
            cards.append({"status": "ok", "chip": "Back to normal", "meta": "%s %s" % (hm(back["t"], True), zname(back["t"])),
                          "title": back["what"], "subject": back["where"],
                          "next": "Row %d of the events%s." % (back["num"], (", %s after the first signal" % _span((back["t"] - first["t"]).total_seconds()))
                                                              if back["t"] >= first["t"] else "")})
        else:
            cards.append({"status": "info", "chip": "Back to normal", "meta": "not seen",
                          "title": "No clearing event after the last problem in the window",
                          "next": "Check the alarms in vCenter, or widen the window with vm_watch_review_hours."})

    if review:
        for card in cards:
            if card.get("next", "").startswith("Row "):
                card["next"] = card["next"].replace("of the events", "of The events on the web page", 1)

    # the chart (the website)
    k_hosts, k_vms, ent_kind = _idents(evs)                                    # 1.90.2: what the page's host and VM filters know
    host_names, vm_names = set(k[1] for k in k_hosts), set(k[1] for k in k_vms)

    def evd(e):
        """One event as a mark's list line (the page's script reads it): n, time, minute, level, what, where, related, kind - and, for an incident
        of ServiceNow, its number and link. 1.90.1: what the page filters by and an opened mark shows - its host, VM, vCenter (short names),
        cluster and alarm, each when known - and its place in the lead-up (in: 1, the critical event that ends it: 2).
        1.90.2: the host also from a ticket or an alarm's entity, the VM also from a VM alarm's entity, the other VMs of a grouped row (vms),
        and the vCenter's full name (the page shows the short one when that is not ambiguous)."""
        d = {"n": e["num"], "t": loc(e["t"]).strftime("%H:%M:%S"), "m": loc(e["t"]).strftime("%H:%M"), "l": e["level"], "w": e["what"], "p": e["where"],
             "r": 1 if e["related"] else 0, "c": e["cls"]}
        if e.get("url"):
            d.update(k=e["ticket"], u=e["url"])
        kind = str(e.get("kind") or "")
        host, vm, ent = _short(e.get("host")), str(e.get("vm") or ""), str(e.get("entity") or "")
        if e["lane"] in ("servicenow", "act") and e["where"]:  # 1.90.2: a ticket's host, as its short description names it - or its VM,
            if e["where"] in vm_names and e["where"] not in host_names:  # when the review knows that name as a VM only
                vm = e["where"]
            else:
                host = e["where"]
        elif not host and ent_kind.get(id(e)) == "host":
            host = _short(ent)                                  # an alarm on a host known only by its entity
        if not vm and ent_kind.get(id(e)) == "vm":
            vm = ent                                            # 1.90.2: a VM's alarm known by its entity only (no vm field), its full name
        vms = [x for x in list(e.get("names") or []) + list(e.get("vms") or []) if x and x != vm]
        mem = sorted(set((_short(h) or host, v) for h, v in e.get("members") or ()))
        for k, v in (("h", host), ("vm", vm or (vms.pop(0) if vms else "")), ("v", str(e.get("vcenter") or "").strip()),
                     ("cl", str(e.get("cluster") or "")), ("a", kind[6:] if kind.startswith("alarm:") else "")):
            if v:
                d[k] = v
        if vms:
            d["vms"] = sorted(set(vms), key=lambda x: (len(x), x))  # 1.90.2: the other VMs of a grouped row (HA's restarts, the same event again)
        if len(mem) > 1:
            d["mem"] = [list(m) for m in mem]                   # and its host and VM pairs: the filters match one pair, the row counts once
        if lead_up is not None and lead_up[0] <= e["t"] <= lead_up[1]["t"]:
            d["lu"] = 2 if e is lead_up[1] else 1
        return d
    # 1.104.0: what ACT reads when asked (vm_outage_act; plugins/filter/outage_act_filters.py): each event as the page knows it
    # lead: one of the lead-up's own problems (the subject's, a warning or worse) - not every event in its time span (Neon)
    act_in = {"events": [dict(evd(e), lane=LANE[e["lane"]], s=e["t"].timestamp(),
                              at=loc(e["t"]).strftime("%Y-%m-%d %H:%M:%S ") + zname(e["t"]), msg=_cut(e.get("display_message", e.get("message")), 200),
                              count=e.get("count") or e["n"], **({"lead": 1} if e["num"] in lead_ns else {})) for e in evs],
              "first": lead_up[1]["num"] if lead_up is not None else (crit["num"] if crit is not None else 0),
              "subject": subject["text"], "window": window_text}
    chart = {}
    if not review:
        z0, z1 = _zoom(evs, downs, begin, end, tz)
        _step, tks = _ticks(z0, z1, tz)
        ticks = []
        for i, t in enumerate(tks):
            text = t.strftime("%H:%M")
            if days and (i == 0 or t.date() != tks[i - 1].date()):
                text = t.strftime("%d %b %H:%M")
            ticks.append({"at": _pct(t, z0, z1), "text": text, "wide": len(tks) > 5 and i % 2 == 1})
        lanes = []
        for key, name in LANES:
            mine = [e for e in evs if e["lane"] == key]
            marks = _place([{"at": _pct(e["t"], z0, z1), "level": e["level"], "label": str(e["num"]), "nums": [e["num"]],
                             "ev": [evd(e)],
                             "tip": "%d  %s %s  %s\n%s%s" % (e["num"], hm(e["t"], True), zname(e["t"]), WORD[e["level"]], e["what"],
                                                             (" - " + e["where"]) if e["where"] else "")} for e in mine])
            for m in marks:
                m["rel"] = 2 if all(x["r"] for x in m["ev"]) else 1 if any(x["r"] for x in m["ev"]) else 0
                m["lead_end"] = any(x.get("lu") == 2 for x in m["ev"])
                m["data"] = json.dumps(m["ev"], ensure_ascii=False, separators=(",", ":"))
            sub = "%d event%s" % (len(mine), "" if len(mine) == 1 else "s") if mine else "nothing"
            if key in ("servicenow", "act"):
                if not o.get("tickets_asked"):
                    sub = "not read: attach the ServiceNow API credential"
                elif o.get("tickets_error"):
                    sub = _cut(o.get("tickets_error"), 70)
                elif o.get("notes_error") and not mine:
                    sub = "work notes not read"
            lanes.append({"key": key, "name": name, "sub": sub, "marks": marks, "rows": max([m["row"] for m in marks] or [0]) + 1})
        bands = []
        for h, a, b, open_ in spans:
            if b < z0 or a > z1:
                continue
            bands.append({"from": _pct(a, z0, z1), "to": _pct(b, z0, z1),
                          "text": "%s down: %s%s" % (h, _span((b - a).total_seconds()), ", still down" if open_ else "")})
        if cleared_only and not (r_to < z0 or r_from > z1):
            bands.append({"from": _pct(r_from, z0, z1), "to": _pct(r_to, z0, z1),
                          "text": "%s: alarms raised %s, cleared %s" % (c_text, hm(r_from), hm(r_to))})
        lead_band = None
        if lead_up is not None:
            l0, lc = lead_up
            lead_band = {"from": _pct(l0, z0, z1), "to": _pct(lc["t"], z0, z1), "num": lc["num"], "span": _span((lc["t"] - l0).total_seconds()),
                         "text": "Lead-up to #%d: %s to %s %s (%s)" % (lc["num"], hm(l0), loc(lc["t"]).strftime("%H:%M") if loc(l0).date() == loc(lc["t"]).date() else hm(lc["t"]), zname(lc["t"]), _span((lc["t"] - l0).total_seconds()))}
        whole = (end - begin).total_seconds()
        overview = None
        if evs and (z1 - z0).total_seconds() < 0.6 * whole:
            overview = {"from": _pct(z0, begin, end), "to": _pct(z1, begin, end), "begin": hm(begin), "end": hm(end),
                        "marks": [{"at": _pct(e["t"], begin, end), "level": e["level"]} for e in evs],
                        "text": "The whole window, %s. The box is the part shown below: %s to %s." % (window_text, hm(z0), hm(z1))}
        chart = {"label": "Outage time line, %s to %s %s" % (hm(z0), hm(z1), zname(z1)), "lanes": lanes, "ticks": ticks, "bands": bands,
                 "overview": overview, "zone": zname(z0),
                 "legend": [{"level": k, "word": WORD[k]} for k in ("critical", "warning", "low", "info", "ok")],
                 "band_word": down_word, "lead": lead_band, "lead_note": lead_note,
                 "subject": subject["text"], "related_note": subject["note"],
                 "legend_rel": ("Outlined: about %s or its host, cluster, datastores" % subject["text"]) if subject["text"] else "",
                 "kinds": [{"key": k, "name": n, "count": len([e for e in evs if e["cls"] == k])} for k, n in CLASSES if any(e["cls"] == k for e in evs)],
                 "none": "" if evs else "No outage event in vCenter between %s. Nothing to draw." % window_text}

    # the events table: the source
    zones = sorted({zname(e["t"]) for e in evs}) or [zname(begin)]
    tcol = "Time (%s)" % zones[0] if len(zones) == 1 else "Time"
    notes = []
    if o.get("capped"):
        notes.append("vCenter had more events than the review reads (%s): the oldest are left out. Shorten the window with "
                     "vm_watch_review_hours." % ", ".join(str(x) for x in o.get("capped")))
    if o.get("tickets_error") and o.get("tickets_asked"):
        notes.append("ServiceNow: %s." % str(o.get("tickets_error")).rstrip("."))
    if o.get("notes_error"):
        notes.append("ServiceNow work notes: %s." % str(o.get("notes_error")).rstrip("."))
    if before:
        notes.append("Open before the window: %s." % ", ".join(before[:5]))
    # for the after-action report
    aar = []
    start_text = ""
    if first:
        start = first_bad or first
        start_text = "%s - %s%s" % (full(start["t"]), start["what"], (" on " + start["where"]) if start["where"] else "")
        aar.append("Start (incident_start): " + start_text)
        aar.append("End (incident_end): %s" % (("%s - %s" % (full(back["t"]), back["what"])) if back else
                                               "not yet - %s still down at the end of the window" % ", ".join(sorted(shown(d) for d in still))
                                               if still else "not seen in the window - take it from the ticket or from vCenter"))
        det = "first signal at %s (%s: %s)" % (hm(first["t"]), first_source, first["what"])
        if to_ticket is not None:
            det += "; ticket %s opened at %s, %s later" % (opened["ticket"], hm(opened["t"]), _span(to_ticket))
        aar.append("Detection (incident_detection): %s" % det)
        aar.append("Root cause (incident_root_cause): %s" % (("check it before you paste - " + cause) if cause else
                                                             "not in the events - write what the fix showed"))
        if to_ticket is not None and to_ticket > 900:
            aar.append("Follow-up: the ticket came %s after the first signal. The watch reports at vm_watch_report_on (now: %s) and a "
                       "ticket opens at servicenow_min_severity (now: %s). Check that the watch's schedule and these two fit what "
                       "this outage showed first." % (_span(to_ticket), ", ".join(str(x) for x in (o.get("report_on") or ["critical"])),
                                                      o.get("min_severity") or "warning"))
    sheet = {"name": "Timeline",
             "columns": ["#", "Time (UTC)", "Time (%s)" % (o.get("time_zone") or "UTC"), "Source", "Where", "What happened", "Level", "Count",
                         "Ticket", "vCenter", "Event type", "Message"], "rows": []}
    if review:
        # 1.116.6: readable in Excel - one time column (the report's zone, ISO), short columns first, the message last
        in_story = {n for row in story["rows"] for n in row.get("source_rows") or ()}
        in_noise = {e["source_row"] for e in noise if "source_row" in e}
        sheet["columns"] = ["#", "Time (%s, ISO)" % (o.get("time_zone") or "UTC"), "Level", "Kind", "Object", "Host", "Datastore", "VM",
                            "What happened", "In the story", "Noise", "vCenter", "Event type", "Event key", "Message"]
        for n, e in enumerate(raw, 1):
            names = e.get("source_names", {})
            host, ds, vm = (names.get(k, e.get(k, "")) or "" for k in ("host", "datastore", "vm"))
            kind = (("ticket" if e["role"] in ("ticket", "resolved") else "note") if e.get("ticket") else
                    "alarm" if e["kind"].startswith("alarm:") else "vm" if vm else
                    "datastore" if ds and e["type"].startswith(("esx.problem.vmfs", "esx.clear.vmfs", "Datastore")) or ds and not host else
                    "host" if host else "note")
            obj = e.get("ticket") or {"vm": vm, "datastore": ds, "host": host}.get(kind) or \
                names.get("entity", e.get("entity")) or names.get("where", e.get("where", ""))
            sheet["rows"].append([n, loc(e["t"]).replace(microsecond=0).isoformat(), WORD[e["level"]], kind, obj, host, ds, vm,
                                  e["label"], "yes" if n in in_story else "no", "yes" if n in in_noise else "no",
                                  e["vcenter"], e["type"], e.get("key", ""), e.get("display_message", e.get("message", ""))])
    for n, e in enumerate([] if review else evs, 1):
        msg = e.get("display_message", e.get("message", ""))
        if not review and e.get("names"):
            msg = ", ".join(e["names"]) + ": " + msg
        row = [n, e["t"].strftime("%Y-%m-%d %H:%M:%S"), loc(e["t"]).strftime("%Y-%m-%d %H:%M:%S ") + zname(e["t"]),
               LANE[e["lane"]], e.get("source_names", e)["where"], e["label"], WORD[e["level"]], 1 if review else e.get("count") or e["n"],
               e.get("ticket", ""), e["vcenter"], e["type"], msg]
        sheet["rows"].append(row)
    out = ["Outage time line, %s: %d event(s)%s%s%s" % (
        window_text, len(evs), ("; first signal %s" % hm(first["t"], True)) if first else "",
        ("; " + down_word) if down_word else "", ("; ticket after %s" % _span(to_ticket)) if to_ticket is not None else "")]
    out.insert(1, "Triggered by: " + " | ".join(trig))
    out += ["  %2d  %s  %-14s %-16s %s  [%s]" % (e["num"], hm(e["t"], True), LANE[e["lane"]], e["where"], e["what"], WORD[e["level"]])
            for e in evs[:60]]
    if len(evs) > 60:
        out.append("  ... and %d more event(s): see the full event list." % (len(evs) - 60) if review else
                   "  ... and %d more event(s): the report's table and the workbook have every one." % (len(evs) - 60))

    # 1.112.0: the AAR draft - what the review knows, for the after action report (incident_doc_filters: incident_review_fill,
    # incident_aar_docx). The review run writes it as "AAR (Word)", and the AAR job reads it back from the job's artifact
    # (vm_outage_review.aar). Facts only: the root cause is never in it; ACT's causes go beside it as suggestions.
    tk_rows = []
    for ticket in tickets or []:
        if not isinstance(ticket, dict) or ticket.get("number") not in own_numbers:
            continue
        resolved_at = _time(ticket.get("resolved")) or _time(ticket.get("closed"))
        tk_rows.append([ticket["number"], loc(resolved_at).strftime("%d %b %H:%M %Z") if resolved_at else "not yet",
                        _cut(ticket.get("short"), 120), _cut(ticket.get("close_notes"), 200)])
    if review:
        date = loc(parts["start"] or (first["t"] if first else _time(o.get("read_at")) or end)).strftime("%Y-%m-%d")
        name = (parts["basename"] if general else "Outage review") + " " + date
        file_label = name if _redact(name) == name else date
        stem = ("outage-review-" + re.sub(r"[^a-z0-9]+", "-", file_label.lower()).strip("-"))[:95].rstrip("-")
        filename = stem + ".csv"
        note = "The source timeline contains %d events, including noise." % len(raw)
        if o.get("capped"):
            note += " The vCenter read reached its event cap."
        limit = max(0, int(o.get("appendix_max", 0)))
        risk_line = "At risk: " + ", ".join(parts["at_risk"]) if parts["at_risk"] else ""
        read_line = "The watch's read at the review, %s." % stamp_review(_time(o.get("read_at")) or end)
        measured_ticket = next((k for k in tickets or [] if isinstance(k, dict) and opened and k.get("number") == opened.get("ticket")), {})
        if measured_ticket.get("active") and not (measured_ticket.get("resolved") or measured_ticket.get("closed")):
            read_line += " Ticket %s is still open%s." % (measured_ticket["number"], " (on hold)" if str(measured_ticket.get("state")) == "3" else "")
        if parts["unread"]:
            read_line += " " + parts["unread"] + "."
        labels = ["What went down", "What it affected: " + (_count(vm_count, "VM") if vm_count or not parts["files_lines"]
                                                             else "no VM confirmed"), "Down now", "Back to normal"]
        summary = [dict(title=label, columns=list(columns), rows=parts[k]) for label, columns, k in zip(
            labels, PART_COLUMNS, ("went", "affected", "now", "recovered"))]
        summary[0].update(status="critical" if parts["went"] else "info", lines=[parts["went_note"]],
                          cell_status=[["", "", "", "critical" if r[3] == "DOWN NOW" else "ok" if r[3] == "Back" else ""] for r in parts["went"]])
        listed_files = len(parts["files_lines"]) > len(parts["files_unread"])
        summary[1].update(text=parts["affected_note"], status="warning" if vm_count or listed_files else "info",
                          lines=([parts["unread"]] if parts["unread"] else []) + parts["files_lines"])
        if not parts["affected"] and parts["files_lines"]:
            summary[1] = {k: v for k, v in summary[1].items() if k != "columns"}   # no empty table, no "None."
        now_severity = parts["now"][0][0].lower() if parts["now"] else "ok"
        summary[2].update(text=read_line, status=now_severity, row_status=[r[0].lower() for r in parts["now"]],
                          lines=([risk_line] if risk_line else []) + ([] if parts["now"] or parts["unread"] else ["Nothing is down now."]))
        summary[3].update(status="ok", lines=[] if parts["recovered"] else ["No recovery recorded."])
        story_section = dict(kind="story", title="Order of events", story=story, issue_count=len(story["rows"]))
        noc_section = dict(kind="noc", title="Likely causes",
                           ops_center=ops_center, patterns=patterns, issue_count=len(patterns))
        chart_section = dict(kind="review_chart", title="All events (%d) - chart" % len(raw), issue_count=len(raw), views=views, collapsed=False)
        noise_lines = ["%s: %s changes on %s, %s; %s." % (alarm, n, who, when, verdict)
                       for alarm, who, n, when, verdict in noise_rows]
        noise_section = {"title": "Noise: summed up, not rows", "lines": noise_lines or ["No noise changes recorded."]}
        issues = parts["issues"]
        issue_section = dict(kind="by_kind", title="Issues by type", issue_count=len(issues), issues=issues)
        # The page shows all appendix groups, including closures outside the narrative.
        # ACT numbers refer to the older display folding. Link those numbers to their source's group.
        act_groups = {str(e["num"]): group_number[e["source_row"]] for e in evs}
        raw_section = dict(kind="raw_events", title=RAW_TITLE, collapsed=True, text=note,
                           columns=[tcol, "What", "Objects", "Level"], lines=list(notes),
                           row_status=[g["level"] if g["level"] in BAD else "" for g in groups],
                           rows=[g["row"] for g in groups],
                           row_tags=[dict(id="ot-row-%d" % n, data={}) for n in range(1, len(groups) + 1)])
        if any(g["url"] for g in groups):
            raw_section["cell_links"] = dict(column=1, urls=[g["url"] for g in groups],
                                             title="Open the incident in ServiceNow", https_only=True)
        went, affected, down, recovered = summary
        sections = [noc_section, went, story_section, chart_section, affected, down, recovered, issue_section]
        if o.get("noise", True):
            sections.append(noise_section)
        email = sections + [raw_section]
        web = list(email)
        measures = [["First signal to ticket", _span(to_ticket) if to_ticket is not None else "Unknown",
                     opened["ticket"] if opened else "No ticket measured"],
                    ["Time down", (_span(down_s) + (", still down" if longest and longest[3] else "")) if spans else
                     "unknown" if unknown_duration else "0", longest[0] if longest else "No measured host loss"]]
        for host, lost, _, ongoing in spans:
            if ongoing and (host, lost) != longest[:2]:
                measures[1][2] += "; %s still down (%s)" % (host, _span((end - lost).total_seconds()))
        detection = []
        if first:
            detection.append("First signal %s: %s%s (%s)" % (stamp_review(first["t"]), first["what"],
                             (" on " + first["where"]) if first["where"] else "", first_source))
            if to_ticket is not None:
                detection.append("Ticket %s opened %s, %s after the first signal" % (
                    opened["ticket"], loc(opened["t"]).strftime("%d %b %H:%M %Z"), _span(to_ticket)))
            elif o.get("tickets_asked") and not o.get("tickets_error") and not opened and not before:
                detection.append("No ticket of the health workflows in the window")
        draft = dict(v=2, what_else=[], name=name, title=title, empty_read=empty_read, outage_ops_center=ops_center,
                     start=start_text, end="", detection=detection, time_column=tcol,
                     follow_up=next((a[len("Follow-up: "):] for a in aar if a.startswith("Follow-up: ")), ""),
                     impact=general or title, actions=[],
                     tickets=tk_rows, ticket_hint=cause, numbers=measures,
                     key=key_rows, story=story, patterns=patterns, window=window_text,
                     groups=[{k: g[k] for k in ("row", "phase", "n")} for g in groups[:limit or None]],
                     appendix_max=limit, group_total=len(groups), events=[], events_total=len(raw),
                     noise=noise_rows, noise_columns=NOISE_COLUMNS, full_list=note, read_note=read_line, unread=parts["unread"],
                     part_columns=PART_COLUMNS, affected_title=affected["title"], recovery=["See Back to normal."])
        draft.update({k: parts[k] for k in ("went", "affected", "now", "recovered", "at_risk", "went_note", "affected_note",
                                            "files_lines", "files_unread")})
        if parts["observed_start"]:
            draft["start"] = _down_by(full(parts["start"]))
        elif parts["start"]:
            e = next((e for e in facts if e["t"] == parts["start"]), None)
            draft["start"] = full(parts["start"]) + (" - " + e["label"] if e else "")
        if parts["still"]:
            draft["end"] = "not yet: %s still down at the review" % ", ".join(parts["still"])
        elif back:
            draft["end"] = full(back["t"])
        else:
            draft["end"] = ""
        summary_rows = [[labels[0], row[0], row[1], "", " / ".join(row[2:])] for row in parts["went"]]
        summary_rows.append([labels[0], "", parts["went_note"], "", ""])
        summary_rows.append([labels[1], "", parts["affected_note"], "", ""])
        summary_rows.extend([labels[1], "", line, "", ""] for line in parts["files_lines"])
        summary_rows.extend([[labels[1], row[0] + " / " + row[1]] + row[2:] for row in parts["affected"]])
        summary_rows.extend([[labels[2]] + row[1:] for row in parts["now"]])
        summary_rows.extend([[labels[2], "", msg, "", ""] for msg in (risk_line, parts["unread"]) if msg])
        summary_rows.extend([[labels[3], row[0], row[1], "", row[2]] for row in parts["recovered"]])
        sheets = [sheet, dict(name="Outage summary", columns=["Part", "Object", "What", "When", "Down for / Now"], rows=summary_rows),
                  dict(name="Noise", columns=NOISE_COLUMNS, rows=noise_rows)]
        if parts["events_rows"]:         # 1.116.6: tier 2 - each VM with events of its own during the outage, its first one
            sheets.append(dict(name="VMs with events", columns=["VM", "First event", "When", "Lost object"], rows=parts["events_rows"]))
        if parts["files_rows"]:          # 1.116.6: every VM on a lost datastore or host (the page and the AAR name 8)
            sheets.append(dict(name="VMs on lost objects", columns=PLACEMENT_COLUMNS, rows=parts["files_rows"]))
        down_note = read_line
        if parts["now"]:
            _, obj, _, when, _ = parts["now"][0]
            down_note = obj + (" · " if when == "time not recorded" else " " if when.startswith("down by ") else " since ") + when
        boxes = [box("Went down", parts["infra_count"], "critical" if parts["went"] else "info", ", ".join(r[0] for r in parts["went"])),
                 box("VMs affected", vm_count, "warning" if vm_count else "info" if parts["files_lines"] else "ok",  # never counted
                     " · ".join([("%s confirmed" % _count(vm_count, "VM")) if vm_count else "no VM confirmed"] + parts["files_tile"])
                     if parts["files_tile"] else " ".join([parts["affected_note"]] + parts["files_lines"][:1])),
                 box("Down now", parts["now_count"], now_severity, down_note),
                 box("Recovered", len(parts["recovered"]), "ok", "Objects with recorded recovery"),
                 boxes[-1], box("Noise", len(noise), "info", "Changes on %s, summed up by scope" % _count(
                     len({(e["vcenter"], e["vm"] or e["entity"]) for e in noise if _scope(e) == "vm"}), "VM"))]
        if general:
            h_subject, h_scope = "Outage review: " + parts["head_name"], [general.partition(" - ")[2]]
        h_counts = [[length, obj + " still down", "critical"] for t, obj, length in parts["down_times"] if t][:1]
        if down_s:
            h_counts.append([_span(down_s), "hosts down", "warning"])
        if to_ticket is not None:
            h_counts.append([_span(to_ticket), "to the ticket", "ok"])
        out[2:2] = [parts["went_note"]] + [s["title"] + ": " + " | ".join(", ".join(row) for row in s["rows"]) for s in summary] + noise_lines + [note]
    display = _redact({"title": title, "head_counts": h_counts, "draft": draft, "head_scope": h_scope, "head_subject": h_subject, "numbers": numbers, "boxes": boxes, "cards": cards, "sections": email, "web_sections": web, "sheet": sheet,
            "head_name": parts["head_name"] if review and general else None, "issues": issues if review else [], "root_judgments": judged,
            "sheets": sheets if review else [sheet], "workbook_stem": stem if review else "", "events_file": filename if review else "", "lines": out, "aar": aar, "window": window_text, "events": len(evs), "act_in": act_in, "act_groups": act_groups,
            "closed_window": not read_in_window, "skip": bool(blip and o.get("skip_blips") and not unread_centers),
            "cleared_before_review": cleared_only, "blip": blip,
            "alarm_window": [r_from.strftime("%Y-%m-%dT%H:%M:%SZ"), r_to.strftime("%Y-%m-%dT%H:%M:%SZ")] if cleared_runs else [],
            "trigger": trig, "trigger_line": "Triggered by: " + trig[0] + ((" (and %d more)" % (len(trig) - 1)) if len(trig) > 1 else "")})
    # These instructions are written by the report. Mask only the external facts they describe.
    if not o.get("tickets_asked"):
        display["lines"].append(TICKETS_NOT_READ)
        for card, shown in zip(cards, display["cards"]):
            if card.get("next") == TICKETS_NOT_READ:
                shown["next"] = TICKETS_NOT_READ
    display["lines"].extend(_redact(notes))
    empty_reason = _review_text.no_root_reason(display["draft"]["empty_read"])
    if empty_reason:
        for key in ("sections", "web_sections"):
            display[key][0]["lines"] = [empty_reason]
        display["lines"].insert(2, empty_reason)
    # The context reader joins inventory names. Keep its private query input outside the display redaction.
    display["context_scope"] = _context_scope(incident, o, window_start, max(filter(None, (window_end, parts.get("last_seen"))), default=None))
    return _unsafe(display)


def outage_review_context(timeline, context=None, reason=""):
    """Attach the same observations to both reports and the saved v2 draft, after the review-only read."""
    out = dict(timeline)
    result = context or _context.unavailable(reason or _context.DB_ERROR)
    cards = result["cards"]
    sites = _redact((out.pop("context_scope", {}) or {}).get("sites") or [])
    extra = [dict(r, group=sites[0] + " · " + r["group"]) if len(sites) == 1 and r["group"] == "SolarWinds (storage path)"
             else r for r in result["issues"]]
    objects = {}
    for entry in extra:
        key = entry["kind"], entry["group"], entry["name"]
        if key not in objects:
            objects[key] = dict(entry, items=list(entry["items"]))
            continue
        obj = objects[key]
        if ISSUE_STATES.index(entry["state"]) < ISSUE_STATES.index(obj["state"]):
            obj.update(state=entry["state"], what=entry["what"])
        obj["items"].extend(item for item in entry["items"] if item not in obj["items"])
        obj["n"] = len(obj["items"])
    extra = sorted(objects.values(), key=lambda r: (ISSUE_STATES.index(r["state"]), r["group"], r["name"]))
    out["issues"] = list(out["issues"]) + extra
    section = dict(title=_context.TITLE, text=result.get("window", ""), cards=cards)
    for key in ("web_sections", "sections"):
        sections = []
        for s in out[key]:
            if s.get("kind") == "by_kind":
                s = dict(s, issues=out["issues"], network_note=result.get("network_note", ""), issue_count=s["issue_count"] + len(extra))
            if s.get("kind") == "raw_events":
                sections.append(section)
            sections.append(s)
        out[key] = sections
    if out["draft"].get("v") == 2:
        out["draft"] = dict(out["draft"], what_else=cards, context_window=result.get("window", ""))
    out["context_judged"] = result["judged"]
    out["lines"] = list(out["lines"]) + [_context.TITLE] + [
        " · ".join(filter(None, (c["chip"], c["meta"], c["title"], c["subject"], c["next"]))) for c in cards]
    return _unsafe(out)


def _with_act(sections, act, web):
    """1.104.0: ACT's suggestions (timeline["act"], outage_act_section) just before How it judged - on the website the cards,
    in the email one section; when ACT was asked and has nothing to show, one line in How it judged says why."""
    add = ([act["web"]] if act.get("web") else []) if web else list(act.get("email") or [])
    at = next((i for i, s in enumerate(sections) if s.get("title") == "How it judged"), len(sections))
    out = sections[:at] + add + sections[at:]
    if act.get("judged") and at < len(sections):
        j = at + len(add)
        out[j] = dict(out[j], lines=list(out[j].get("lines") or []) + [act["judged"]])
    elif act.get("judged"):
        out.append({"title": "How it judged", "lines": [act["judged"]]})
    return out


def _context_judged(sections, line):
    if not line:
        return sections
    return [dict(s, lines=list(s.get("lines") or []) + [line]) if s.get("title") == "How it judged" else s for s in sections]


def outage_timeline_doc(doc, timeline, web=False, review=False):
    """Build a review with the likely causes first and the grouped record last.
    Scheduled reports use the frozen renderer."""
    if not review:                           # 1.116.0: a scheduled run is 1.118.4's, word for word
        return _v1184.outage_timeline_doc(doc, timeline, web, review)
    if review:
        doc = _redact(doc)
    if not doc:
        return _unsafe(doc)
    out = dict(doc)
    own = list(doc.get("sections") or [])
    if not timeline:
        result = _context.unavailable("the outage timeline could not be made")
        at = next((i for i, s in enumerate(own) if s.get("title") == "How it judged"), len(own))
        own.insert(at, dict(title=_context.TITLE, cards=result["cards"]))
        return _unsafe(dict(out, sections=_context_judged(own, result["judged"])))
    mine = list(timeline["web_sections" if web else "sections"])
    if review:
        out["title"] = ("Outage review: " if web and timeline.get("head_name") else "") + timeline["title"]
        for k in ("head_name", "head_counts", "head_scope", "head_subject"):       # 1.82.0
            if timeline.get(k) is not None:
                out[k] = timeline[k]
        out["summary"] = list(timeline.get("boxes") or [])
        out["subtitle"] = "%s Review of %s. Now: %s" % (timeline.get("trigger_line", "").rstrip(".") + "." if timeline.get("trigger_line") else "",
                                                       timeline.get("window", ""), doc.get("title", ""))
        out["subtitle"] = out["subtitle"].strip()
        if timeline.get("closed_window"):
            # The watch document describes today's whole inventory. Only the reviewed roots' Down now rows belong here.
            out["subtitle"] = "Review of %s." % timeline["window"]
            out["status"] = next(s["status"] for s in mine if s["title"] == "Down now")
            own = [{"title": "How it judged", "lines": []}] if timeline.get("context_judged") else []
        if any(s.get("title") == "Down now" for s in mine):
            own = [s for s in own if s.get("title") != "Down or hung now" and not s.get("title", "").startswith("VMs with files on ")]
        if timeline.get("root_judgments"):
            at = next((i for i, s in enumerate(own) if s.get("title") == "How it judged"), len(own))
            section = own[at] if at < len(own) else {"title": "How it judged"}
            own[at:at + 1] = [dict(section, lines=list(section.get("lines") or []) + timeline["root_judgments"])]
        own = _context_judged(own, timeline.get("context_judged"))
        out["act_groups"] = timeline.get("act_groups", {})
        raw_record = [s for s in mine if s.get("kind") == "raw_events"]
        mine = [s for s in mine if s.get("kind") != "raw_events"]
        out["sections"] = _with_act(mine + own, timeline.get("act") or {}, web) + raw_record
        out["outage_ops_center"] = (timeline.get("draft") or {}).get("outage_ops_center", "NOC")
        out["review_order"] = True     # 1.116.4: the review's own section order (the shared looks keep it; a neutral key)
        out["status_prefix"] = "Now: "                      # 1.90.0: the verdict is the watch's state now, not the outage's
    else:
        mine = [s for s in mine if s.get("title") in ("What happened, in order", "The events")]
        out["sections"] = own[:1] + mine + own[1:]
    return _unsafe(out)


def outage_review_aar(doc, info=None, web=False):
    """Attach the Word and CSV download information to the review's cards, header and raw record."""
    if not isinstance(info, dict) or not info.get("review"):      # 1.116.0: a scheduled run is 1.118.4's, word for word
        return _v1184.outage_review_aar(doc, info, web)
    if not doc:
        return _unsafe(doc)
    out = dict(doc)
    name, err, job = str(info.get("file") or ""), str(info.get("error") or ""), str(info.get("job") or "")
    if web and info.get("asked") and (name or err):
        out["head_file"] = ({"file": name, "label": "Download AAR (Word)", "note": "A draft to finish: check each line, write the red ones."}
                            if name else {"error": "The AAR (Word) was not made: %s. The page and the email are complete." % err.rstrip("."),
                                          "label": "AAR (Word)", "note": ""})
    lines = []
    if name:
        lines.append(("The AAR draft, %s: Download AAR (Word) at the top of this page, and attached to the email. " if web else
                      "The AAR draft is attached: %s. ") % name
                     + "It is filled from this review: check each line, write the red ones (root cause, lessons learned, tasks).")
    elif err and info.get("asked"):
        lines.append("The AAR draft was not made: %s." % err.rstrip("."))
    if job and info.get("draft"):
        lines.append("Or launch Outage AAR - Word document with Fill from an outage review (incident_review): %s - or latest. "
                     "It makes the same draft with what you type in its survey." % job)
    ops_center = out.get("outage_ops_center") or "NOC"
    noc_file, noc_error = info.get("noc_file"), info.get("noc_error")
    if noc_file and not web:
        lines.append("Hand this to the %s: %s is attached." % (ops_center, noc_file))
    if noc_error:
        lines.append("The %s handoff was not made: %s." % (ops_center, noc_error.rstrip(".")))
    csv_file = info.get("csv_file")
    if csv_file and not web:
        lines.append("Full event list: %s." % csv_file)
    if not lines and not noc_file and not csv_file:
        return _unsafe(out)
    secs = []
    for sec in out.get("sections") or []:
        if sec.get("kind") == "raw_events":
            sec = dict(sec, lines=lines + list(sec.get("lines") or []))
            if web and csv_file:
                sec["download_file"] = csv_file
        if web and sec.get("kind") == "noc":
            sec = dict(sec, noc_file=noc_file or "", noc_error=("The %s handoff was not made: %s" % (ops_center, noc_error)) if noc_error else "")
        secs.append(sec)
    out["sections"] = secs
    return _unsafe(out)


def outage_review_objects(events, opts=None):
    """1.116.6: the review's lost datastores and hosts, each with when its loss began - what roles/vmware_vm/tasks/watch.yml
    asks the placement history (site_vm_placement_read) and vCenter (site_vmware_object_vms) about, before the time line:
    [{vcenter, kind, name, moid, at}]. at: the first loss event of that object in the window, else the watch's first
    observation of it, else the window's start."""
    o = dict(opts or {})
    begin, end = _time(o.get("begin")), _time(o.get("end"))
    if begin is None or end is None or end <= begin:
        return []
    o["triggers"] = _review_triggers(o)
    all_vc = _vcenter_events(events, o.get("datastores"))
    read = _time(o.get("read_at")) or end
    roots = _review_roots(o, all_vc, begin, end, begin <= read <= end)[0]
    out = []
    for c in roots:
        kind, vc, name = c.get("object_type"), c.get("vcenter", ""), c.get("object", "")
        if kind not in ("datastore", "host") or not name:
            continue
        losses = [e["t"] for e in all_vc if begin <= e["t"] <= end and e["vcenter"] == vc and _event_loss(e) == kind
                  and (e["datastore"] == name if kind == "datastore" else name in (e["host"], e["host_full"]) or e["host"] == _short(name))]
        times = losses or [t for t in (_time(x) for x in c.get("observations") or []) if t] or \
            [t for t in [_time(c.get("since"))] if t]
        at = min(times) if times else begin
        out.append(dict(vcenter=vc, kind=kind, name=name, moid=c.get("moid") or "", at=at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")))
    return _unsafe(out)


def vm_placement_lines(result, check_mode=False):
    """1.116.6: the job output of VMware - VM placement snapshot (site_vm_placement_snapshot's result), a line each."""
    r = result or {}
    lines = ["Check: vCenter read, nothing written."] if check_mode else []
    lines += ["%s: %d VMs, %s, %d rows kept" % (x.get("vcenter", ""), int(x.get("vms") or 0),
                                                "whole snapshot" if x.get("whole") else "changes only", int(x.get("kept") or 0))
              for x in r.get("runs") or []]
    if r.get("pruned"):
        lines.append("Older rows deleted: %d" % int(r["pruned"]))
    if r.get("created"):
        lines.append("Tables vm_placement and vm_placement_run made.")
    if r.get("error"):
        lines.append("Not kept: " + str(r["error"]))
    return lines


def outage_placement_missing(items, placements=None):
    """The items the placement history had no snapshot for (as_of empty, or not answered): the review reads vCenter for them."""
    found = {(p.get("vcenter"), p.get("kind"), p.get("name")) for p in placements or [] if isinstance(p, dict) and p.get("as_of")}
    return [i for i in items or [] if (i.get("vcenter"), i.get("kind"), i.get("name")) not in found]


def outage_placements(placements=None, live=None):
    """The history's placements where it had a snapshot, else the review's vCenter read (live: site_vmware_object_vms)."""
    out = [p for p in placements or [] if isinstance(p, dict) and p.get("as_of")]
    have = {(p.get("vcenter"), p.get("kind"), p.get("name")) for p in out}
    for p in (live or {}).get("placements") or [] if isinstance(live, dict) else []:
        if isinstance(p, dict) and p.get("as_of") and (p.get("vcenter"), p.get("kind"), p.get("name")) not in have:
            out.append(p)
    return out


class FilterModule(object):
    def filters(self):
        return {"outage_timeline": outage_timeline, "outage_timeline_doc": outage_timeline_doc, "outage_window": outage_window,
                "outage_review_aar": outage_review_aar, "outage_review_context": outage_review_context,
                "outage_events_csv": outage_events_csv, "outage_redact": outage_redact,
                "outage_review_objects": outage_review_objects, "outage_placement_missing": outage_placement_missing,
                "outage_placements": outage_placements, "vm_placement_lines": vm_placement_lines}
