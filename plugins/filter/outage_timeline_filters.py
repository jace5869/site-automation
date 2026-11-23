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


def outage_redact(value):
    return _unsafe(_redact(value))


def _load(name, folder):
    spec = importlib.util.spec_from_file_location(name, os.path.join(os.path.dirname(__file__), folder, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_redact = _load("site_redact", "../module_utils").redact_all
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
STORY_LIMIT = 15
DRAFT_EVENTS = 300         # 1.112.0: the AAR draft holds at most this many events (its Appendix A; the artifact stays small)
ATTACH = "Attach the ServiceNow API credential to the outage watch template to see and link its incidents (read only)."


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
        message = " ".join(str(e.get("message") or "").split())
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
            label = message[:120] or typ
        out.append({"t": t, "lane": lane, "level": level, "label": label, "where": where, "kind": kind, "role": role,
                    "type": typ, "message": message, "vcenter": str(e.get("vcenter") or ""), "vm": vm, "cluster": cluster,
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
    t = " ".join(str(text or "").split())
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


def _review_ticket_numbers(tickets, incident, opts):
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


def _review_roots(opts, events, begin, end):
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

    def add(vc, kind, name, moid="", condition=None, source="", when=None):
        current_name = inventory_names.get((vc, kind, moid), name)
        if not vc or not name or wanted and wanted not in (name, current_name):
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
        if when:
            observations[key].add(when)
            ticket_names[key].add(name)
        return key

    read_runs = 0
    recorded_vms, recorded_members = set(), defaultdict(set)
    for run in opts.get("watch_runs") or []:
        artifact = (run.get("artifacts") or {}).get("vm_watch") or {}
        when = _time(artifact.get("read_at") or run.get("finished"))
        if not artifact or not when or not begin <= when <= end:
            continue
        read_runs += 1
        stores, vm_findings = defaultdict(set), []
        for c in artifact.get("conditions") or []:
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
    for c in opts["triggers"]:
        if c.get("vcenter") and _watch_root(c):
            add(c["vcenter"], c["kind"], c["object"], _condition_moid(c), c, when=_time(opts.get("read_at")) or end)

    def event_object(e, kind):
        name = e["host_full"] if kind == "host" else e[kind]
        moid = e[kind + "_moid"]
        if _scope(e) == kind and e["entity"]:
            name, moid = e["entity"] or name, e["entity_moid"] or moid
        return name or inventory_names.get((e["vcenter"], kind, moid)), moid

    window = [e for e in events if begin <= e["t"] <= end]
    unnamed_loss = False
    if not roots:
        for e in window:
            kind = _event_loss(e)
            if kind:
                name, moid = event_object(e, kind)
                if not name:
                    unnamed_loss = True
                    continue
                source = "from vCenter's event %s at %s" % (e["type"], e["t"].strftime("%Y-%m-%d %H:%M:%S UTC"))
                key = add(e["vcenter"], kind, name, moid, source=source)
                if key:
                    e["root_event"] = True
    if not roots:
        for c in opts["triggers"]:
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
        if not roots:
            for (vc, kind, name), candidates in inventory.items():
                if name == wanted:
                    for moid in candidates:
                        add(vc, kind, name, moid)
    for key, c in roots.items():
        if recorded_members[key]:
            c["members"] = list(c.get("members") or []) + [dict(name=name, moid=moid)
                for name, moid in sorted(recorded_members[key])]
        c.update(ticket_names=sorted(ticket_names[key]), observations=sorted(observations[key]))
    empty_read = {} if roots else dict(runs=read_runs, object=wanted, unnamed_loss=unnamed_loss,
        partial=bool(opts.get("events_not_read") or opts.get("capped") or any(vc.get("error") for vc in opts.get("event_vcenters") or [])))
    if empty_read and opts.get("events_not_read"):
        empty_read["events_not_read"] = True
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
        elif name and (dc or len(sites[vc, name]) == 1):
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
    hosts = [dict(h, name=label(h.get("vcenter", ""), "host", h.get("moid"), h["name"])) for h in opts.get("hosts") or []]
    _review_clusters(events + hosts + triggers)
    host_aliases = defaultdict(set)
    for (vc, _), names_seen in full_hosts.items():
        for fqdn in names_seen:
            host_aliases[vc, label(vc, "host", None, fqdn)].add(fqdn)
    recorded = {(vc, label(vc, "vm", moid, name)) for vc, name, moid in opts["recorded_vms"]}
    return dict(opts, recorded_vms=recorded, host_aliases=host_aliases, triggers=triggers[:n], roots=triggers[n:], hosts=hosts)


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
            outages[e["vcenter"], e["host"], e["datastore"] or e["device"]].append(e["t"])
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
                    for t in outages[first["vcenter"], first["host"], first["datastore"] or first["device"]])
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


def _finding_object(c):
    return c.get("vcenter", ""), c.get("object_type"), c.get("object")


def _vm_key(e):
    name = e["vm"] or (e["entity"] if _scope(e) == "vm" else "")
    return (e["vcenter"], name) if name else None


def _root_history(events, opts):
    roots = {_finding_object(c): c for c in opts["roots"]}
    current = {_finding_object(c): c for c in opts["triggers"]
               if _finding_object(c) in roots and (c.get("inaccessible") or c.get("kind") == "host")}
    path_spans = defaultdict(list)
    infrastructure = [e for e in events if not _vm_key(e) and
                      ((e["vcenter"], "datastore", e["datastore"]) in roots or _scope(e) == "host")]
    # A watch read establishes that the host was down at that instant. Only a loss event
    # (or an explicit "since") can date the onset. Keep observations out of the event export.
    observed_starts = set()
    for obj, c in roots.items():
        if obj[1] == "host":
            since = _time(current.get(obj, {}).get("since"))
            for at in sorted(set(c["observations"]) | ({since} if since else set())):
                infrastructure.append(dict(t=at, observed_host=obj, onset_known=at == since))
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
        since = _time(current.get(obj, {}).get("since"))
        if since and not spans[obj]:
            spans[obj].append([since, None])
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
    affected = set(members) | {(obj[0], obj[2]) for _, obj, _, _ in vm_chain}
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
                affected=affected, lost_files=lost_files, on_down_host=on_down_host, source_hosts=source_hosts,
                history=history, path_spans=path_spans, observed_starts=observed_starts, chain=chain, starts=starts | vm_starts, facts=facts, events=selected,
                hosts=hosts, ticket_ids=ids)


def _history(events, path_spans=None, observed_starts=None, object_type=None):
    """Each object's down stretches. A clear must close its own condition."""
    opened, spans, chain = defaultdict(set), defaultdict(list), []
    restarts, closed, episode_starts = set(), set(), set()
    for e in events:
        if "observed_host" in e:
            obj = e["observed_host"]
            if not opened[obj]:
                spans[obj].append([e["t"], None])
                if not e["onset_known"]:
                    observed_starts.add((obj, e["t"]))
            opened[obj].difference_update([k for k in opened[obj] if k[0].startswith("alarm:")])
            opened[obj].add(("conn", "", ""))
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
            active.add(condition)
        chain.append((e, obj, kind, clear))
    return spans, chain, episode_starts


def outage_events_csv(sheet):
    """1.116.0: one source row per event, even when the review folds it."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_redact(sheet["columns"]))
    for row in sheet["rows"]:
        writer.writerow(["'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v for v in _redact(row)])
    return _unsafe(buf.getvalue())


def _cluster_spans(hosts, spans, current, observed_starts):
    members = defaultdict(set)
    for h in hosts:
        # the cluster's MoID when the read gives it, else the cluster within its vCenter and datacenter (1.118.4's watch read
        # gives cluster and datacenter, no cluster MoID)
        key = h.get("cluster_key") or ((h.get("vcenter", ""), h.get("datacenter", ""), h["cluster"]) if h.get("cluster") else None)
        if key and h.get("cluster") and h.get("name"):
            members[key, h["cluster"]].add((h.get("vcenter", ""), "host", h["name"]))
    found = []
    for (_, name), group in sorted(members.items()):
        changes = defaultdict(int)
        observations = set()
        for obj in group:
            for a, b in spans.get(obj, []):
                changes[a] += 1
                if (obj, a) in observed_starts:
                    observations.add(a)
                if b:
                    changes[b] -= 1
        n, since = 0, None
        for at, delta in sorted(changes.items()):
            n += delta
            if n == len(group) and since is None:
                since = at
            elif n < len(group) and since:
                found.append((name, None if since in observations else since, at, False))
                since = None
        live = group <= current.keys()
        if since or live:
            found.append((name, None if since in observations else since, None, live))
    return found


ISSUE_STATES = ("Down now", "Inaccessible in vCenter", "At risk", "Not judged", "Recovered")
PART_COLUMNS = (["Object", "What", "First signal", "Down for", "Now"],
                ["Ran on", "Datastore", "VMs", "What happened", "Now"],
                ["Severity", "Object", "What", "Since", "Down for"], ["What", "How", "Back at", "Down for"])
NOISE_COLUMNS = ["Alarm", "VMs", "Changes", "When", "Verdict"]


def _review_parts(incident, opts, end, stamp):
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

    def issue(kind, group_name, state, name, what, since, items):
        return dict(kind=kind, group=group_name, state=state, name=name, what=what, since=since, n=len(items), items=items)

    issues, went, recovered, down = [], [], [], []
    changed = [e for e in clean if e["level"] == "critical"]
    start = next((e["t"] for e in changed), next((e["t"] for e in clean if e["level"] in BAD), None))
    vm_groups, affected_rows, destinations = defaultdict(list), defaultdict(list), defaultdict(set)
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
        since = sig[-1][0]["t"] if sig else None
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
        since_text = stamp(since) if since else "unknown"
        item = dict(name=vm, state=state, what=word, host=host or "not read", datastore=", ".join(ds) or "not read",
                    protected=bool(p.get("protected") or c.get("protected")), since=since_text)
        vm_items[key] = (item, ds)
        vm_groups[group(vc, info, host), state, word].append(item)
        if key not in affected:
            continue
        if state == "Recovered" and back:
            origin = next((a for a, b in history.get((vc, "host", host), []) if a <= back and (not b or b >= back)), None)
            if ((vc, "host", host), origin) in observed_starts:
                origin = None
            origin = origin or (history[obj][-1][0] if history.get(obj) and history[obj][-1][1] == back else None)
            recovered.append((back, [display(vc, vm), word, stamp(back), duration(origin, back)]))
        now_word = word if state != "Recovered" else "not read"
        if len(incident["roots"]) > 1 and incident["vm_roots"][key]:
            how += " (reviewed: %s)" % ", ".join(obj[2] for obj in sorted(incident["vm_roots"][key]))
        row_key = (vc, item["host"], item["datastore"], how, now_word)
        affected_rows[row_key].append(vm)
        if restart and restart["host"]:
            destinations[row_key].add(restart["host"])
    if affected and all((vc, "vm", vm) in current for vc, vm in affected):
        unread = ""
    for (where, state, word), items in vm_groups.items():
        times = list(dict.fromkeys(v["since"] for v in items))
        issues.append(issue("vm", where, state, _count(len(items), "VM"), word, times[0] if len(times) == 1 else "See individual times", items))
    host_keys = incident["hosts"]
    ds_items = defaultdict(list)
    back_times, down_times = [], []
    for obj in stores + hosts:
        vc, typ, name = obj
        c = current.get(obj)
        intervals = history.get(obj) or [(None, None)]
        a, b = intervals[-1]
        if (obj, a) in observed_starts:
            a = None
        live = c is not None
        if live and b:
            a, b = None, None  # the historical stretch closed before this observation
        state = "Down now" if live else "Recovered" if b else "Not judged"
        observed = live or a or incident["roots"].get(obj, {}).get("observations")
        hs = sorted(ds_hosts.get(obj, ()))
        what = ("Datastore, lost on " + ", ".join(hs) if hs else "Datastore, not accessible") if typ == "datastore" else (
            "ESXi host, not responding" if typ == "host" else "Lost storage paths")
        now_word = "DOWN NOW" if live else "Back " + stamp(b) if b else "recovery not recorded"
        if not observed:
            what, now_word = OBJECT_WORD[typ], "no loss recorded"
        length = duration(a, read if live else b)
        shown = display(vc, name)
        went.append([shown, what, stamp(a) if a else "unknown", length, now_word])
        if live:
            down_times.append((a, shown, length))
        # Each host regains access independently. A datastore alarm can also close
        # the aggregate outage after the last path has cleared.
        clearances = [(lost, back, "datastore back on " + h) for h in hs
                      for lost, back in incident["path_spans"].get((obj, h), ()) if back]
        path_ends = {back for _, back, _ in clearances}
        for lost, back in intervals:
            if back and back not in path_ends:
                how = next((e["label"] for e, k, clear in signals[obj] if clear and e["t"] == back and k.startswith("alarm:")),
                           "host connected again" if typ == "host" else "access restored")
                clearances.append((lost, back, how))
        for lost, back, how in clearances:
            recovered.append((back, [shown, how, stamp(back), duration(None if (obj, lost) in observed_starts else lost, back)]))
            back_times.append(back)
        since_text = ("since " + stamp(a) if a and live else now_word) + " · " + length
        if typ == "datastore":
            items = [item for key, (item, ds) in vm_items.items()
                     if (obj in lost_files[key] if live else key[0] == vc and name in ds)]
            items.sort(key=lambda v: (ISSUE_STATES.index(v["state"]), v["name"]))
            issues.append(issue("ds", group(vc, meta[obj], hs[0] if hs else ""), state, name, what, since_text, items))
            for h in hs:
                paths = incident["path_spans"].get((obj, h))
                if paths and paths[-1][1]:
                    lost, back = paths[-1]
                    ds_items[vc, h].append(dict(name=name, state="Recovered", what="Back " + stamp(back),
                                                since=stamp(back) + " · " + duration(lost, back)))
                else:
                    ds_items[vc, h].append(dict(name=name, state=state, what=now_word, since=since_text))
    for vc, h in sorted(host_keys):
        obj = (vc, "host", h)
        details = ds_items[vc, h]
        host_members = {v["name"] for v in current.get(obj, {}).get("members") or []}
        items = [item for (center, vm), (item, _) in vm_items.items()
                 if center == vc and (vm in host_members if obj in current else item["host"] == h)]
        at_risk = [d["name"] for d in details if d["state"] == "Down now"]
        interval = (history.get(obj) or [(None, None)])[-1]
        state = "Down now" if obj in current else "At risk" if at_risk else "Recovered" if interval[1] or details and all(
            d["state"] == "Recovered" for d in details) else "Not judged"
        why = ", ".join(at_risk) + " not accessible" if at_risk else "host connected again" if interval[1] else "storage paths"
        at = interval[1] or (None if (obj, interval[0]) in observed_starts else interval[0])
        since = stamp(at) if at else "unknown"
        if state == "Down now":
            why = current[obj].get("summary") or "host not responding"
        elif state == "At risk":
            since = next(d["since"] for d in details if d["state"] == "Down now")
        host_info = meta.get(obj) or next((e for e, _, _, _ in chain if e["vcenter"] == vc and e["host"] == h), {})
        issues.append(issue("host", group(vc, host_info, h), state, h, why, since, details + items))
    many_centers = len({c.get("vcenter") for c in opts["triggers"]} | {o[0] for o in incident["roots"]}) > 1
    for c in opts["triggers"]:
        obj = _finding_object(c)
        since = _time(c.get("since"))
        name = " ".join(filter(None, [OBJECT_WORD.get(obj[1], obj[1]), obj[2]])) or "Watch"
        if many_centers and obj[0]:
            name += " (%s)" % obj[0]
        detail = c.get("summary") or c.get("kind", "")
        if obj[1] == "vm" and (obj[0], obj[2]) in vm_items:
            files = vm_items[obj[0], obj[2]][0]["datastore"]
            if files != "not read" and "(datastore %s)" % files not in detail:
                detail += " (datastore %s)" % files
        down.append([(c.get("severity") or "info").title(), name, detail,
                     stamp(since) if since else "unknown", duration(since, read)])
    issues.sort(key=lambda v: (v["group"], ISSUE_STATES.index(v["state"]), v["name"]))
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
    counts = _count(vm_count, "VM") + ", " + _count(host_count, "host")
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
    still = [x[1] for x in down_times]
    if still:
        title += "; %s still down" % ", ".join(still)
    first = next((e for e in clean if e["level"] in BAD), None)
    note = "All in %s (%s)." % (scope, site) if not multi else "Across %s." % site
    if not stores and not hosts:
        note = "No host or datastore loss recorded." + (" " + note if affected else "")
    if first:
        note += " First signal %s: %s on %s." % (stamp(first["t"]), first["label"], first["where"])
    connected = sorted(h for vc, h in host_keys if (vc, "host", h) not in hosts)
    if connected:
        note += " Storage paths were lost on %s." % ", ".join(connected)
    for cluster, lost, back, live in _cluster_spans(opts.get("hosts") or [], history, current, observed_starts):
        note += " Cluster %s lost all hosts%s%s." % (cluster, " at " + stamp(lost) if lost else "",
            " for " + duration(lost, back) if back else " and is still down" if live else " (recovery not recorded)")
    reviewed = ", ".join("%s %s (%s)%s" % (kind, name, vc, " " + c["root_source"] if c["root_source"] else "")
                         for (vc, kind, name), c in sorted(incident["roots"].items()))
    reason = "named by vm_watch_review_object" if opts.get("review_object") else "from the watch's conditions of this run and of the runs in the window"
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
    aff_rows = []
    for row_key, vms in sorted(affected_rows.items()):
        vc, h, ds, how, nw = row_key
        if multi:
            h = "%s / %s" % (vc, h)
        if destinations[row_key]:
            how += " on " + ", ".join(sorted(destinations[row_key]))
        aff_rows.append([h, ds, "%d: %s" % (len(vms), ", ".join(vms)), how, nw])
    observations = sorted(at for _, at in observed_starts if at <= end)
    unknown_start = bool(observations and (not start or observations[0] < start))
    if unknown_start:
        start = observations[0]
    last_signal = max(observations + [e["t"] for e in clean if e["level"] in BAD + ("ok",)], default=None)
    open_observation = any(history[obj][-1] == [at, None] for obj, at in observed_starts)
    end_recovery = recovered[-1][0] if recovered and not open_observation else None
    return dict(observed_starts=observed_starts, unknown_start=unknown_start, open_observation=open_observation,
                title=title if meta or affected else "", basename=kind + " " + subject, head_name=kind + ", " + scope,
                went=went, went_note=note, affected=aff_rows, affected_note=count_line, now=down, at_risk=[],
                recovered=[row for _, row in recovered], issues=issues, affected_keys=affected, host_count=host_count,
                first_signal=first["t"] if first else None,
                last_signal=last_signal,
                start=start, finish=end_recovery, first_back=next((e["t"] for e in clean if start and e["t"] >= start and
                e["role"] in ("up", "clear") and e["lane"] == "host"), min(back_times, default=end_recovery)), still=still, unread=unread,
                down_times=down_times, infra_count=", ".join(_count(len(xs), word) for xs, word in ((hosts, "host"), (stores, "datastore"))),
                now_count=_count(len(down), "condition") if down else "nothing")



def _event_groups(events, start, first_back, finish, stamp, key=False):
    groups, latest = [], {}
    for e in sorted(events, key=lambda row: row["t"]):
        t = e["t"]
        phase = ("Lead-up" if start and t < start else "After" if finish and t > finish + datetime.timedelta(minutes=5)
                 else "Recovery" if first_back and t >= first_back else "Outage")
        identity = (phase, e["vcenter"], e["lane"], e["level"], e["label"])
        g = latest.get(identity)
        if not g or not key and (t - g["first"]).total_seconds() > 180:
            g = dict(first=t, last=t, phase=phase, label=e["label"], n=0, level=e["level"], objects=set(), type=e["type"], lane=e["lane"])
            latest[identity] = g
            groups.append(g)
        g["last"], g["n"] = t, g["n"] + 1
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
    rows = []
    for g in groups:
        when = stamp(g["first"])
        if g["last"] != g["first"]:
            end_stamp = stamp(g["last"])
            day, clock, zone = end_stamp.rsplit(" ", 2)
            first_day, _, first_zone = when.rsplit(" ", 2)
            when += " to " + (clock if (day, zone) == (first_day, first_zone) else end_stamp)
        what = g["label"] + (" (x%d)" % g["n"] if g["n"] > 1 else "")
        rows.append(dict(phase=g["phase"], n=g["n"], row=[when, what, ", ".join(sorted(g["objects"])), WORD[g["level"]]]))
    return rows


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


def _story_episode(events, parts, history, loc):
    """Fold developments from one episode. A later return to the same condition starts a new one."""
    by_type = defaultdict(list)
    for e in events:
        by_type[e["type"]].append(e)
    storage = by_type["esx.problem.storage.apd.start"]
    returns = by_type["esx.clear.storage.apd.exit"]
    timeouts = by_type["esx.problem.storage.apd.timeout"]
    failures = [e for e in events if e["type"].endswith("DasHostFailedEvent")]
    disconnected = {(e["vcenter"], e["host"]) for e in by_type["HostConnectionLostEvent"] + by_type["HostDisconnectedEvent"]}
    def path_key(e):
        e = e.get("opening", e)
        return e["vcenter"], e.get("host"), e.get("datastore"), (e.get("device") or "").lower()

    apd_paths = {path_key(e) for e in storage}
    restored_paths = {(path_key(e), e["t"]) for e in returns}
    host_signals = defaultdict(set)
    for e in events:
        if e.get("host"):
            host_signals[e["vcenter"], e["host"]].add(e["kind"])
    groups = {}
    connected = set()
    for e in events:
        typ, kind = e["type"], e["kind"]
        if e["lane"] in ("servicenow", "act") and e["role"] not in ("ticket", "resolved"):
            continue
        if kind.startswith("alarm:"):
            kinds = host_signals[e["vcenter"], e.get("host") or _short(e.get("entity"))]
            alarm = kind[6:].lower()
            if (("connection and power" in alarm or "not responding" in alarm) and "conn" in kinds or
                    ("storage" in alarm or "datastore" in alarm) and kinds & {"sred", "sconn", "apd", "hb", "pdl"}
                    and any(w in alarm for w in ("connect", "path", "access", "redundancy"))):
                continue
        if path_key(e) in apd_paths and typ in ("esx.problem.storage.apd.timeout", "esx.problem.vmfs.heartbeat.timedout"):
            continue
        if (path_key(e), e["t"]) in restored_paths and typ in ("esx.clear.storage.connectivity.restored", "esx.problem.vmfs.heartbeat.recovered",
                                "esx.clear.storage.redundancy.restored"):
            continue
        if typ.endswith("DasHostFailedEvent") and (e["vcenter"], e["host"]) in disconnected:
            continue
        key = e["label"]
        host = (e["vcenter"], e.get("host"))
        if e["role"] == "down":
            connected.discard(host)
        if e["role"] == "up":
            if host in connected:
                continue
            connected.add(host)
            key = (key, e["t"])
        elif e["role"] == "restart":
            key = (key, e.get("cluster_key"))
        elif e["role"] == "clear" and typ.startswith("esx."):
            key = (key, e["t"], e.get("opening", e)["t"])
        elif typ.startswith("VmDasBeingReset"):
            key = (key, bool(parts["first_back"] and e["t"] >= parts["first_back"]))
        groups.setdefault((e["vcenter"], e.get("root_group"), key), []).append(e)

    rows = []
    for group in groups.values():
        e = group[0]
        t, typ, level, sentence = e["t"], e["type"], e["level"], e["label"]
        hosts = _objects(group, "host")
        ds = _objects(group, "datastore")
        vms = _objects(group, "vm")
        hc = _count(len({x["host"] for x in group if x.get("host")}), "host")
        vc = _count(len({x["vm"] for x in group if x.get("vm")}), "VM")
        objs = _objects(group, "datastore", "host", "vm") or _objects(group, "where")
        duration, extra, after = "", None, ""
        if e["kind"] == "sred" and ds and hosts and e["role"] == "problem":
            sentence = "Path redundancy to %s %s on %s" % (ds, "degraded" if typ.endswith("degraded") else "lost", hosts)
            if ", back after " in e["label"]:
                sentence += ", back after " + e["label"].split(", back after ")[1]
            objs = hosts
        elif e["kind"] == "sconn" and e["role"] == "problem" and ds:
            sentence = "%s lost on %s: the connection to storage dropped" % (_count(len({x["datastore"] for x in group}), "datastore"), hc)
        elif typ.endswith(".apd.start"):
            sentence = "All paths down (APD)" + (" on " + hc if hosts else "")
            group_paths = {path_key(x) for x in group}
            timeout = next((x for x in timeouts if path_key(x) in group_paths and x["t"] >= t), None)
            if timeout:
                sentence += "; I/O fails from "
                extra, after = _moment(timeout["t"], loc), " (APD timeout)"
            objs = ds or hosts
            same_returns = [x for x in returns if path_key(x) in group_paths and x["t"] >= t]
            if same_returns and {path_key(x) for x in same_returns} == group_paths:
                duration = _span((same_returns[-1]["t"] - t).total_seconds()) + " for " + (_objects(same_returns, "datastore") or hosts)
            missing = set(x["datastore"] for x in group) & set(parts["still"])
            if missing:
                duration = "; ".join(filter(None, [duration, _compact(missing) + " still down"]))
        elif typ in ("HostConnectionLostEvent", "HostDisconnectedEvent", "HostConnectedEvent"):
            sentence = hosts + (" connected again" if e["role"] == "up" else " not responding" if typ == "HostConnectionLostEvent" else " disconnected")
            failed = next((x for x in failures if x["vcenter"] == e["vcenter"] and x["host"] in {h["host"] for h in group} and x["t"] >= t), None)
            if failed and e["role"] != "up":
                sentence += "; HA declared a host failure at "
                extra = _moment(failed["t"], loc)
            durations = {}
            for x in group:
                for a, b in history.get((x["vcenter"], "host", x["host"]), []):
                    if b and a <= x["t"] <= b and ((x["vcenter"], "host", x["host"]), a) not in parts["observed_starts"]:
                        durations[x["host"]] = _span((b - a).total_seconds())
            duration = "; ".join(h + " " + length for h, length in durations.items()) if len(durations) > 1 else next(iter(durations.values()), "")
        elif typ.startswith("VmDasBeingReset"):
            again = bool(parts["first_back"] and t >= parts["first_back"] and any(
                x.get("vm") == e.get("vm") and x["t"] < parts["first_back"] for x in by_type[typ]))
            sentence = "HA reset %s%s%s" % (vc, " on " + hosts if hosts else "", " again" if again else "")
            objs = vms
        elif e["role"] == "restart":
            sentence, objs = "HA restarted %s%s" % (vc, " on " + hosts if hosts else ""), vms
        elif typ == "VmFailoverFailed":
            sentence, objs = "HA could not restart " + vc, vms
        elif typ.endswith(".apd.exit"):
            sentence = (ds or "Storage paths") + " back" + (" on " + hc if hosts else "")
            onset = e.get("opening")
            duration = "APD " + _span((t - onset["t"]).total_seconds()) if onset else ""
            objs = hosts
        elif e["kind"] == "pdl" and e["role"] == "problem":
            sentence = (ds or "Storage device") + " lost for good (permanent device loss, PDL)" + (" on " + hc if hosts else "")
            objs = hosts
        elif e["role"] == "ticket":
            level = "info"
            onset = parts["first_signal"]
            if onset and t >= onset:
                sentence += ", %s after the first signal" % _span((t - onset).total_seconds())
        rows.append(dict(t=t, level=level, text=sentence, extra=extra, after=after, objects=objs, duration=duration,
                         ticket=e["lane"] == "servicenow", role=e["role"]))
    return rows


def _story(events, parts, history, read, loc, episode_starts):
    rows, episode = [], []
    observations = [dict(t=t, observed_host=obj) for obj, t in sorted(parts["observed_starts"])
                    if t < read or obj[2] not in parts["still"]]
    for e in sorted(events + observations, key=lambda e: e["t"]):
        if "observed_host" in e:
            rows.extend(_story_episode(episode, parts, history, loc))
            episode = []
            host = e["observed_host"][2]
            rows.append(dict(t=e["t"], level="critical", text="Watch observed " + host + " not responding",
                             objects=host, duration="onset unknown", extra=None, after="", ticket=False, role="observation"))
            continue
        if id(e) in episode_starts:
            rows.extend(_story_episode(episode, parts, history, loc))
            episode = []
        episode.append(e)
    rows.extend(_story_episode(episode, parts, history, loc))
    rows.sort(key=lambda row: row["t"])
    start = parts["start"] or parts["first_signal"]
    finish = parts["last_signal"] if start else None
    window = {}
    if start and finish:
        duration = "onset unknown" if parts["unknown_start"] else _span((finish - start).total_seconds())
        window = dict(start=_moment(start, loc), end=_moment(finish, loc), duration=duration, still=_compact(parts["still"]))
    if parts["still"]:
        detail = _compact(parts["still"])
        duration = next((x[2] for x in sorted(parts["down_times"], key=lambda x: x[0] or read) if x[0]), "")
        rows.append(dict(t=read, level="down", text="Still down at the review: " + detail, objects=detail,
                         duration=duration + " so far" if duration else "", extra=None, after="", ticket=False, role="observation"))
    # Preserve both window boundaries, the lead-up and the observation when a busier chain exceeds the budget.
    if len(rows) > STORY_LIMIT:
        keep = {0, len(rows) - 1}
        for boundary in (start, finish):
            if boundary:
                closest = min(range(len(rows)), key=lambda i: abs((rows[i]["t"] - boundary).total_seconds()))
                keep.add(closest)
        for role in ("restart", "ticket", "up", "clear", "resolved"):
            milestone = next((i for i, r in enumerate(rows) if r["role"] == role), None)
            if milestone is not None:
                keep.add(milestone)
        for i in sorted(range(len(rows)), key=lambda i: -RANK.get(rows[i]["level"], 0)):
            if len(keep) >= STORY_LIMIT:
                break
            keep.add(i)
        rows = [rows[i] for i in sorted(keep)]
    day = ""
    for row in rows:
        t = row.pop("t")
        level = row["level"]
        role = row.pop("role")
        is_ticket = row.pop("ticket")
        row["word"] = {"ok": "Recovered", "down": "Down now", "info": "Ticket" if is_ticket else "Info"}.get(level, WORD.get(level))
        if role == "resolved":
            row["word"] = "Resolved"
        elif level == "ok" and row["text"].startswith("Alarm "):
            row["word"] = "Alarm cleared"
        row["time"] = _moment(t, loc)
        divider = row["time"]["day"]
        row["divider"] = (divider + (" - the review" if level == "down" else "")) if day != divider or level == "down" else ""
        row["window"] = bool(window and start <= t <= finish and level != "down")
        day = divider
    return dict(rows=rows, window=window), start, finish


def _usage_or_gray(e, alarms):
    return e["kind"].startswith("alarm:") and (
        _usage_alarm(e["kind"][6:], alarms) or any(str(e.get(k)).lower() in ("gray", "grey") for k in ("from", "to")))


def _chart_lane(e):
    if e["lane"] in ("servicenow", "act"):
        return "tickets"
    if e["type"].startswith(("esx.problem.storage", "esx.clear.storage", "esx.problem.scsi",
                              "esx.clear.scsi", "esx.problem.vmfs")) or any(s in e["kind"].lower() for s in ("storage", "datastore")):
        return "storage"
    return "hosts" if e["lane"] == "host" or _scope(e) == "host" else "vms"


def _chart_view(events, related, affected, alarms, left, right, start, finish, still, loc, everything):
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
        if _usage_or_gray(e, alarms):
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


def _pattern_cards(events, current, opts, read, loc):
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
        cards.append((first, dict(title=pattern["title"], means=pattern["means"], causes=pattern["causes"],
                                  checks=pattern["checks"], seen=seen, when=when, escalate=escalate)))
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
    if review:
        events, tickets, o = _redact(events), _redact(tickets), _redact(o)
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
        o["roots"], empty_read, o["recorded_vms"] = _review_roots(o, all_vc, begin, end)
        o = _review_inventory(o, all_vc)
        empty_reason = _review_text.no_root_reason(empty_read)
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
        tk = [e for e in tk if not e.get("repeat")]
        alarms = [str(a).lower() for a in o.get("noise_alarms", ["CPU usage", "memory usage", "disk usage"]) if a]
        narrative, noise = _review_rows(vc, low_seconds, alarms, o["roots"])
        if o.get("noise", True):
            vc = narrative
        else:
            noise = []
        incident = _root_history(narrative, o)
        history, chain = incident["history"], incident["chain"]
        facts, story_events = incident["facts"], incident["events"]
        selected_ids = {id(e) for e in story_events}
        if pre and incident["roots"]:
            begin = min([begin_asked] + [e["t"].replace(second=0, microsecond=0) for e in pre if id(e) in selected_ids])
            days = loc(begin).date() != loc(end).date()
        narrative_ids = {id(e) for e in narrative}
        # Keep the matching closure of a root signal in the event appendix even after the host reconnected.
        appendix_ids, pending = selected_ids.copy(), set()
        for e in narrative:
            key = _pair_key(e)
            if e["role"] in ("problem", "down"):
                if id(e) in selected_ids:
                    pending.add(key)
                else:
                    pending.discard(key)
            elif e["role"] in ("up", "clear") and key in pending:
                appendix_ids.add(id(e))
                pending.remove(key)
        appendix = [e for e in vc if id(e) in appendix_ids
                    or not o.get("noise", True) and id(e) not in narrative_ids]
        vc = story_events
        if chain or incident["current"]:
            alarm_events = story_events
        stamp_review = lambda t: loc(t).strftime("%d %b %H:%M:%S %Z")
        parts = _review_parts(incident, o, end, stamp_review)
        general = parts["title"]
        vm_count, host_count = len(parts["affected_keys"]), parts["host_count"]
        start_at, finish_at = parts["start"], parts["finish"]
        noise_rows = _noise_rows(noise, raw, parts["affected_keys"], start_at, finish_at or end, begin, end, stamp_review)
        vm_keys = incident["affected"]
        own_numbers = _review_ticket_numbers(tickets, incident, o)

        def related_event(e):
            if e.get("ticket"):
                return e["ticket"] in own_numbers
            return id(e) in selected_ids

        tk = [e for e in tk if related_event(e)]
        groups = _event_groups(appendix + tk, start_at, parts["first_back"], finish_at, stamp_review)
        key_events = story_events + [e for e in tk if e["role"] in ("ticket", "resolved")]
        key_rows = [g["row"] for g in _event_groups(key_events, start_at, parts["first_back"], finish_at, stamp_review, key=True)]
        before = [n for n in before if n in own_numbers]
        read = _time(o.get("read_at")) or end
        story, window_start, window_end = _story(story_events + tk, parts, history, read, loc, incident["starts"])
        # A capacity alarm on a reviewed store during its loss can supply a NOC card from the noise bars.
        usage_raises = [e for e in raw if e["kind"].startswith("alarm:") and e["role"] == "problem"
                        and _usage_alarm(e["kind"][6:], alarms)
                        and any(a <= e["t"] and (not b or e["t"] <= b)
                                for a, b in history.get((e["vcenter"], "datastore", e["datastore"]), ()))
                        and not any(str(e.get(k)).lower() in ("gray", "grey") for k in ("from", "to"))]
        patterns = _pattern_cards(sorted(story_events + usage_raises, key=lambda e: e["t"]), incident["current"], o, read, loc)
        first_signal = parts["first_signal"] or window_start or begin
        left = max(begin, first_signal - datetime.timedelta(minutes=12))
        right = min(end, (window_end or end) + datetime.timedelta(minutes=18))
        views = [_chart_view(story_events + tk, related_event, vm_keys, alarms, left, max(right, left + datetime.timedelta(seconds=1)),
                             window_start, window_end, _compact(parts["still"]), loc, False),
                 _chart_view(raw, related_event, vm_keys, alarms, begin, end, window_start, window_end, _compact(parts["still"]), loc, True)]
    runs, unmatched = _alarm_runs(alarm_events)
    if review:
        for r in runs:
            r["usage"] = _usage_alarm(r["alarm"], alarms)
    vc, restarted = _restarts(vc)
    if review and not o.get("noise", True):
        recorded = {id(e) for e, obj, _, clear in chain if clear and obj[1] in ("host", "datastore")}
        vc, _ = _pairs([dict(e, recorded_clear=id(e) in recorded) for e in vc], low_seconds)
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
        # The detailed display may fold or expand the same source event. Keep its
        # row number for navigation, and take its severity and label from the scoped chain.
        def source(e):
            return e["vcenter"], e.get("key"), e["t"], e["type"], e.get("host"), e.get("vm"), e.get("entity")
        displayed = {source(e): e for e in evs if e["lane"] in ("vcenter", "host")}
        signals = [dict(e, num=displayed.get(source(e), {}).get("num"), what=e["label"]) for e in facts if e["level"] in BAD]
    first = signals[0] if signals else None
    first_bad = next((e for e in signals if e["level"] in ("critical", "warning")), None)
    tickets_opened = [e for e in evs if e["role"] == "ticket" and (not review or e["ticket"] in own_numbers)]
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
    unknown_onset = {d for d in downs if ((d[3], "host", d[0]), d[1]) in incident["observed_starts"]} if review else set()
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
    spans = [(shown(d), d[1], d[2] or end, d[2] is None) for d in downs if d not in unknown_duration and d not in unknown_onset]
    longest = max(spans, key=lambda d: (d[2] - d[1]).total_seconds()) if spans else None
    down_s = (longest[2] - longest[1]).total_seconds() if longest else 0
    hosts_down = sorted({shown(d) for d in downs})
    last_bad = max([e["t"] for e in evs if e["lane"] in ("vcenter", "host") and e["level"] in ("critical", "warning")] or [begin])
    goods = [e for e in evs if e["lane"] in ("vcenter", "host") and e["level"] == "ok" and e["t"] >= last_bad]
    back = None if still or not signals else (goods[-1] if goods else None)
    if review and general:
        back = next((e for e in evs if e["t"] == parts["finish"] and e["level"] == "ok"), None)
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
    if review and not trig and incident["current"]:
        trig = [r[0] + ": " + r[1] + " " + r[2] for r in parts["now"][:8]]
    if not trig and standing:
        for e in sorted(standing, key=lambda x: x["t"])[:6]:
            trig.append("CRITICAL: %s on %s: red since %s, still red at the end of the window" % (e["kind"][6:], e["where"] or "?", hm(e["t"], True)))
    if review and not trig and not general and noise:
        trig = ["The review was asked for. Alarm changes and short flaps are counted below."]
    if not trig and review and parts["open_observation"]:
        trig.append("The watch recorded a host loss whose recovery time is unknown.")
    if not trig and (cleared_runs or (spans and not still)):
        for h, a_, b_, _hv in [d for d in downs if d[2] is not None and d not in unknown_onset][:3]:
            trig.append("%s: not responding %s to %s (%s), back before the review" % (h, hm(a_, True), hm(b_, True), _span((b_ - a_).total_seconds())))
        for r in sorted((r for r in cleared_runs if not review or not r["usage"]), key=lambda x: x["raised"])[:6]:
            trig.append("%s on %s: raised %s, cleared %s (%s later)" % (r["alarm"], r["vm"] or r["where"] or "?", hm(r["raised"], True), hm(r["cleared"], True),
                                                                        _span((r["cleared"] - r["raised"]).total_seconds())))
        trig.insert(0, "Nothing was down when the watch read vCenter: what raised it had already cleared.")
    if not trig:
        trig = ["Nothing triggered it: the review was asked for (vm_watch_review) and the watch found nothing down or hung when it read vCenter."]
    numbers = {
        "events": len(evs), "first_signal": first["t"].strftime("%Y-%m-%dT%H:%M:%SZ") if first else "",
        "ticket": opened["ticket"] if opened else "", "signal_to_ticket_s": int(to_ticket) if to_ticket is not None else -1,
        "down_s": -1 if unknown_onset and not spans else int(down_s), "hosts_down": hosts_down, "still_down": sorted(shown(d) for d in still), "ha_restarted": restarted,
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
                 ("%s%s" % (opened["ticket"], (", resolved %s" % hm(resolved[-1]["t"])) if resolved else ", not resolved in the window"))
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
                      "next": ("Row %d of the events. " % first["num"] if first["num"] else "") + LANE[first["lane"]] + " lane."})
    if to_ticket is not None:
        cards.append({"status": "warning" if to_ticket > 900 else "ok", "chip": "First signal to ticket", "meta": _span(to_ticket),
                      "title": "%s opened at %s %s" % (opened["ticket"], hm(opened["t"]), zname(opened["t"])), "subject": opened["where"],
                      "next": "Row %d of the events." % opened["num"]})
        if opened.get("url"):
            cards[-1].update(url=opened["url"], url_text=opened["ticket"])
    elif first:
        why = (str(o.get("tickets_error")) if o.get("tickets_error") else "No ticket of the health workflows was opened in the window."
               if o.get("tickets_asked") else "ServiceNow was not read: attach the ServiceNow API credential to the job template. " + ATTACH)
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
                              at=loc(e["t"]).strftime("%Y-%m-%d %H:%M:%S ") + zname(e["t"]), msg=_cut(e.get("message") or "", 200),
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
    rows = [[e["num"], hm(e["t"], True) + ("" if len(zones) == 1 else " " + zname(e["t"])), LANE[e["lane"]], e["where"], e["what"],
             WORD[e["level"]]] for e in evs]
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
    table = {"title": "The events", "columns": ["#", tcol, "Source", "Where", "What happened", "Level"], "rows": rows,
             "kinds": ["num", "", "", "", "wrap", ""],
             "row_status": [e["level"] if e["level"] in BAD else "" for e in evs],
             "cell_status": [["", "", "", "", "", e["level"]] for e in evs],
             "text": " ".join(["Every mark of the time line is one row here, by its number.",
                               "A problem that cleared within %s is one Low row (vm_watch_review_low_seconds)." % _span(low_seconds)
                               if low_seconds > 0 else
                               "A problem and its clear are a row each (vm_watch_review_low_seconds is 0)."] + notes)}
    if evs and any(e.get("url") for e in evs):
        table["cell_links"] = {"column": 4, "urls": [e.get("url") or "" for e in evs], "title": "Open the incident in ServiceNow", "https_only": True}
    if not evs:
        table = {"title": "The events", "status": "ok", "lines": ["No outage event between %s." % window_text] + notes}

    # the website's table: the same rows with a "Related" column, and the data the page's script folds and filters by (the rows stay
    # complete without it); the email keeps the table above
    web_table = table
    if review and evs:
        web_table = dict(table, row_tags=[{"id": "ot-row-%d" % e["num"], "data": {}} for e in evs])
    elif evs:
        folds = dict((i, idx) for i, idx in _fold(evs))
        heads = {}
        for head_i, idx in folds.items():
            for j in idx[1:]:
                heads[j] = head_i
        known = bool(subject["names"])
        tags = []
        for i, e in enumerate(evs):
            d = {"num": str(e["num"]), "cls": e["cls"], "rel": "1" if e["related"] else "0"}
            if i in folds:
                span = [evs[j] for j in folds[i]]
                d["fold"] = "%d|%s|%s|%s" % (len(span), loc(span[0]["t"]).strftime("%H:%M"), loc(span[-1]["t"]).strftime("%H:%M"),
                                           ", ".join(loc(x["t"]).strftime("%H:%M:%S") for x in span[:60]))
            if i in heads:
                d["rep"] = "1"
                d["head"] = "ot-row-%d" % evs[heads[i]]["num"]          # the row it is folded into (the script resolves it by id, whatever the sort)
            tags.append({"id": "ot-row-%d" % e["num"], "data": d})
        cols = list(table["columns"]) + (["Related"] if known else [])
        urls = [e.get("url") or "" for e in evs]
        web_table = dict(table, columns=cols, row_tags=tags, outage_filter=True,
                         **({"cell_links": {"column": 4, "urls": urls, "title": "Open the incident in ServiceNow", "https_only": True}} if any(urls) else {}),
                         rows=[r + (["Related" if e["related"] else ""] if known else []) for r, e in zip(table["rows"], evs)],
                         kinds=list(table["kinds"]) + (["" ] if known else []),
                         cell_status=[c + (["info" if e["related"] else ""] if known else []) for c, e in zip(table["cell_status"], evs)],
                         text=table["text"] + (" Related: about %s or its host, cluster, datastores." % subject["text"] if known else
                                                (" " + subject["note"] if subject["note"] else "")))

    # for the after-action report
    aar = []
    if first:
        start = first_bad or first
        aar.append("Start (incident_start): %s - %s%s" % (full(start["t"]), start["what"], (" on " + start["where"]) if start["where"] else ""))
        aar.append("End (incident_end): %s" % (("%s - %s" % (full(back["t"]), back["what"])) if back else
                                               "not yet - %s still down at the end of the window" % ", ".join(sorted(shown(d) for d in still))
                                               if still else "not seen in the window - take it from the ticket or from vCenter"))
        det = "first signal at %s (%s: %s)" % (hm(first["t"]), LANE[first["lane"]], first["what"])
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
    aar_section = {"title": "For the after-action report", "lines": aar,
                   "text": "Lines for the survey of the after-action report (Docs - incident documents). Each names its setting."} if aar else None

    line = {"title": "What happened, in order", "outage": chart,
            "text": "" if evs else chart.get("none", "")}
    card_section = {"title": "The outage in short", "cards": cards} if cards else None
    email = [s for s in (card_section, table, aar_section) if s]
    web = [s for s in (card_section, line if evs else None, web_table, aar_section) if s]
    sheet = {"name": "Timeline",
             "columns": ["#", "Time (UTC)", "Time (%s)" % (o.get("time_zone") or "UTC"), "Source", "Where", "What happened", "Level", "Count",
                         "Ticket", "vCenter", "Event type", "Message"], "rows": []}
    if review:
        sheet["columns"] += ["Host", "Datastore", "VM", "Cluster", "Entity", "Event key", "From", "To"]
    for n, e in enumerate(raw if review else evs, 1):
        msg = e.get("message", "")
        if not review and e.get("names"):
            msg = ", ".join(e["names"]) + ": " + msg
        row = [n, e["t"].strftime("%Y-%m-%d %H:%M:%S"), loc(e["t"]).strftime("%Y-%m-%d %H:%M:%S ") + zname(e["t"]),
               LANE[e["lane"]], e.get("source_names", e)["where"], e["label"], WORD[e["level"]], 1 if review else e.get("count") or e["n"],
               e.get("ticket", ""), e["vcenter"], e["type"], msg]
        if review:
            row.extend(e.get("source_names", {}).get(k, e.get(k, "")) for k in ("host", "datastore", "vm", "cluster", "entity", "key", "from", "to"))
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
    if not o.get("tickets_asked"):
        out.append("ServiceNow was not read: attach the ServiceNow API credential to the job template. " + ATTACH)
    out += notes

    # 1.112.0: the AAR draft - what the review knows, for the after action report (incident_doc_filters: incident_review_fill,
    # incident_aar_docx). The review run writes it as "AAR (Word)", and the AAR job reads it back from the job's artifact
    # (vm_outage_review.aar). Facts only: the root cause is never in it; ACT's causes go beside it as suggestions.
    draft = {}

    def stamp(t, sec=False):
        return loc(t).strftime("%d %b %H:%M" + (":%S" if sec else "")) + " " + zname(t)

    ticket_numbers = own_numbers if review else {e["ticket"] for e in evs if e.get("ticket")}
    tk_rows = []
    for k in tickets or []:
        if not isinstance(k, dict) or k.get("number") not in ticket_numbers:
            continue
        op, rs = _time(k.get("opened")), _time(k.get("resolved")) or _time(k.get("closed"))
        tk_rows.append([k["number"], stamp(op) if op else "", stamp(rs) if rs else "not yet",
                        _cut(k.get("short"), 120), _cut(k.get("close_notes"), 200)])
    if first:
        start = first_bad or first
        where_name = (head or start)["where"] or (hosts_down[0] if hosts_down else "")
        impact = []
        for h, a_, b_ in [(shown(d), d[1], d[2]) for d in downs if d not in unknown_duration and d not in unknown_onset]:
            impact.append("%s down %s (%s to %s)" % (h, _span(((b_ or end) - a_).total_seconds()), stamp(a_),
                                                     stamp(b_) if b_ else "still down at the end of the window"))
        if restarted:
            impact.append("vSphere HA restarted %d VM%s" % (restarted, "" if restarted == 1 else "s"))
        for d in sorted(unknown_duration - unknown_onset, key=lambda x: x[1]):
            impact.append("%s lost connection %s (recovery time unknown)" % (shown(d), stamp(d[1])))
        impact.extend("%s observed down %s (onset unknown)" % (shown(d), stamp(d[1]))
                      for d in sorted(unknown_onset, key=lambda d: d[1]))
        if not impact and review and general:
            impact.append(general)
        if not impact:
            impact.append("no host was lost: %s%s" % (_lower_first(start["what"]), (" on " + start["where"]) if start["where"] else ""))
        detection = ["First signal %s: %s%s (%s)" % (stamp(first["t"], True), first["what"],
                                                       (" on " + first["where"]) if first["where"] else "", LANE[first["lane"]])]
        if to_ticket is not None:
            detection.append("Ticket %s opened %s, %s after the first signal" % (opened["ticket"], stamp(opened["t"]), _span(to_ticket)))
        elif o.get("tickets_asked") and not o.get("tickets_error"):
            detection.append("No ticket of the health workflows in the window")
        actions, recovery = [], []
        for e in evs:
            at = stamp(e["t"]) + " - "
            if e["role"] == "restart":
                actions.append(at + e["label"] + ((" (" + e["where"] + ")") if e["where"] else ""))
            elif review and e["type"].startswith("VmDasBeingReset"):
                actions.append(at + "HA reset " + (e.get("vm") or "a VM"))
            elif review and e.get("ticket") and e["ticket"] not in own_numbers:
                continue
            elif e["lane"] == "servicenow" and e["level"] == "info":
                actions.append(at + e["what"])
            elif e["role"] == "resolved":
                recovery.append((e["t"], at + e["what"]))
            elif e["lane"] == "host" and e["role"] == "up":
                recovery.append((e["t"], at + e["what"] + ((" (" + e["where"] + ")") if e["where"] else "")))
        if back:
            recovery.append((back["t"], "%s - all back to normal: %s" % (stamp(back["t"]), back["what"])))
        recovery = [x[1] for x in sorted(recovery, key=lambda x: x[0])]
        if not review:
            key_ids = {id(x) for x in (first, start, back, opened) if x is not None}
            must = [e for e in evs if id(e) in key_ids or e["role"] in ("restart", "resolved", "up")][:15]
            more = [e for e in evs if e["level"] == "critical" and e not in must][:15 - len(must)]
            key_ids = {id(e) for e in must + more}

            def old_row(e):
                return [loc(e["t"]).strftime("%d %b %H:%M:%S") + ("" if len(zones) == 1 else " " + zname(e["t"])),
                        LANE[e["lane"]], WORD[e["level"]], e["what"], e["where"]]
        # 1.112.0 (Codex P1): "still down" only for the stretch whose minutes are shown; another one still open is said apart
        time_down = "onset unknown" if unknown_onset else "recovery time unknown" if unknown_duration else "no host lost in the window"
        if longest:
            time_down = "%s down %s (%s to %s)" % (longest[0], _span(down_s), stamp(longest[1]),
                                                  "still down at the end of the window" if longest[3] else stamp(longest[2]))
            for h, a_, _b, open_ in spans:
                if open_ and (h, a_) != (longest[0], longest[1]):
                    time_down += "; %s down%s since %s, still down at the end of the window (%s)" % (
                        h, " again" if h == longest[0] else "", stamp(a_), _span((end - a_).total_seconds()))
        follow = [a for a in aar if a.startswith("Follow-up: ")]
        draft = {"v": 1, "title": title, "window": window_text, "time_column": tcol,
                 "name": ("%s %s" % (where_name, loc(start["t"]).strftime("%Y-%m-%d"))).strip(),
                 "start": "%s - %s%s" % (full(start["t"]), start["what"], (" on " + start["where"]) if start["where"] else ""),
                 "end": ("%s - %s" % (full(back["t"]), back["what"])) if back else (
                     ("not yet: %s still down at the end of the window (%s)" % (", ".join(sorted(shown(d) for d in still)), full(end)))
                     if still else ""),
                 "impact": "; ".join(impact), "detection": detection, "actions": actions[:30], "recovery": recovery[:30],
                 "numbers": [["First signal", stamp(first["t"], True), "%s%s (%s)" % (
                                 first["what"], (" on " + first["where"]) if first["where"] else "", LANE[first["lane"]])],
                             ["First signal to ticket", _span(to_ticket) if to_ticket is not None else "-",
                              ("%s opened %s" % (opened["ticket"], stamp(opened["t"]))) if to_ticket is not None else "no ticket measured"],
                             ["Time down", (_span(down_s) + (", still down" if longest and longest[3] else "")) if spans else "unknown" if unknown_duration or unknown_onset else "0", time_down]],
                 "tickets": tk_rows, "ticket_hint": cause, "follow_up": follow[0][len("Follow-up: "):] if follow else ""}
        if not review:
            draft.update(key=[old_row(e) for e in evs if id(e) in key_ids], events=[old_row(e) for e in evs[:DRAFT_EVENTS]],
                         events_total=len(evs))
    if review:
        date = loc(parts["start"] or (first["t"] if first else _time(o.get("read_at")) or end)).strftime("%Y-%m-%d")
        name = (parts["basename"] if general else "Outage review") + " " + date
        stem = ("outage-review-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"))[:95].rstrip("-")
        filename = stem + ".csv"
        note = "The source timeline contains %d events, including noise." % len(raw)
        if o.get("capped"):
            note += " The vCenter read reached its event cap."
        limit = max(0, int(o.get("appendix_max", 40)))
        risk_line = "At risk: " + ", ".join(parts["at_risk"]) if parts["at_risk"] else ""
        read_line = "The watch's read at the review, %s." % stamp_review(_time(o.get("read_at")) or end)
        measured_ticket = next((k for k in tickets or [] if isinstance(k, dict) and opened and k.get("number") == opened.get("ticket")), {})
        if measured_ticket.get("active") and not (measured_ticket.get("resolved") or measured_ticket.get("closed")):
            read_line += " Ticket %s is still open%s." % (measured_ticket["number"], " (on hold)" if str(measured_ticket.get("state")) == "3" else "")
        if parts["unread"]:
            read_line += " " + parts["unread"] + "."
        labels = ["1. What went down", "2. What it affected: " + _count(vm_count, "VM"), "3. Down now", "4. Recovered"]
        summary = [dict(title=label, columns=list(columns), rows=parts[k]) for label, columns, k in zip(
            labels, PART_COLUMNS, ("went", "affected", "now", "recovered"))]
        summary[0].update(status="critical" if parts["went"] else "info", lines=[parts["went_note"]],
                          cell_status=[["", "", "", "", "critical" if r[4] == "DOWN NOW" else "ok" if r[4].startswith("Back") else ""] for r in parts["went"]])
        summary[1].update(text=parts["affected_note"], status="warning" if vm_count else "info",
                          lines=[parts["unread"]] if parts["unread"] else [])
        now_severity = parts["now"][0][0].lower() if parts["now"] else "ok"
        summary[2].update(text=read_line, status=now_severity, row_status=[r[0].lower() for r in parts["now"]],
                          lines=([risk_line] if risk_line else []) + ([] if parts["now"] or parts["unread"] else ["Nothing is down now."]))
        summary[3].update(status="ok", lines=[] if parts["recovered"] else ["No recovery recorded."])
        story_section = dict(kind="story", title="The outage, in order", story=story, issue_count=len(story["rows"]))
        noc_section = dict(kind="noc", title="What this usually means (for the %s)" % ops_center,
                           ops_center=ops_center, patterns=patterns, issue_count=len(patterns))
        chart_section = dict(kind="review_chart", title="All events (%d) - chart" % len(raw), issue_count=len(raw), views=views, collapsed=True)
        noise_lines = ["%s: %s changes on %s, %s; %s." % (alarm, n, who, when, verdict)
                       for alarm, who, n, when, verdict in noise_rows]
        noise_section = {"title": "Noise: summed up, not rows", "lines": noise_lines}
        sections = [story_section] + summary + ([card_section] if card_section else [])
        email = sections + ([noise_section] if noise_rows else [])
        issues = parts["issues"]
        issue_count = sum(v["n"] if v["kind"] == "vm" else 1 for v in issues)
        web = [story_section, {"kind": "by_kind", "title": "Issues by type", "issue_count": issue_count, "issues": issues}] + summary
        web.extend(s for s in (noc_section, chart_section, card_section) if s)
        web.append(dict(web_table, title="The events", collapsed=True, text=note))
        if noise_rows:
            web.append(noise_section)
        if empty_reason:
            for sections in (email, web):
                sections.insert(0, dict(title="Review findings", top=True, lines=[empty_reason]))
        if aar_section or general or noise or empty_reason:
            email.append(aar_section or {"title": "For the after-action report"})
            web.append(email[-1])
        draft.update(v=2, name=name, title=title, empty_read=empty_read, outage_ops_center=ops_center,
                     start=draft.get("start", ""), end=draft.get("end", ""),
                     impact=general or title, actions=draft.get("actions", []), detection=draft.get("detection", []),
                     tickets=tk_rows, ticket_hint=cause,
                     key=key_rows, story=story, patterns=patterns, window=window_text, groups=groups[:limit], appendix_max=limit, group_total=len(groups), events=[], events_total=len(raw),
                     noise=noise_rows, noise_columns=NOISE_COLUMNS, full_list=note, read_note=read_line, unread=parts["unread"],
                     part_columns=PART_COLUMNS, recovery=["%s - %s: %s (down %s)" % (r[2], r[0], r[1], r[3]) for r in parts["recovered"]])
        draft.update({k: parts[k] for k in ("went", "affected", "now", "recovered", "at_risk", "went_note", "affected_note")})
        if parts["unknown_start"]:
            draft["start"] = "unknown (watch observed the host down at %s)" % full(parts["start"])
        elif parts["start"]:
            e = next((e for e in facts if e["t"] == parts["start"]), None)
            if e:
                draft["start"] = "%s - %s" % (full(e["t"]), e["label"])
        if parts["still"]:
            draft["end"] = "not yet: %s still down at the review" % ", ".join(parts["still"])
        elif back:
            draft["end"] = full(back["t"])
        else:
            draft["end"] = ""
        summary_rows = [[labels[0]] + row[:3] + [" / ".join(row[3:])] for row in parts["went"]]
        summary_rows.append([labels[0], "", parts["went_note"], "", ""])
        summary_rows.append([labels[1], "", parts["affected_note"], "", ""])
        summary_rows.extend([[labels[1], row[0] + " / " + row[1]] + row[2:] for row in parts["affected"]])
        summary_rows.extend([[labels[2]] + row[1:] for row in parts["now"]])
        summary_rows.extend([[labels[2], "", msg, "", ""] for msg in (risk_line, parts["unread"]) if msg])
        summary_rows.extend([[labels[3]] + row for row in parts["recovered"]])
        sheets = [sheet, dict(name="Outage summary", columns=["Part", "Object", "What", "When", "Down for / Now"], rows=summary_rows),
                  dict(name="Noise", columns=NOISE_COLUMNS, rows=noise_rows)]
        boxes = [box("Went down", parts["infra_count"], "critical" if parts["went"] else "info", ", ".join(r[0] for r in parts["went"])),
                 box("VMs affected", vm_count, "warning" if vm_count else "ok", parts["affected_note"]),
                 box("Down now", parts["now_count"], now_severity,
                     parts["now"][0][1] + " since " + parts["now"][0][3] if parts["now"] else read_line),
                 box("Recovered", loc(parts["finish"]).strftime("%H:%M") if parts["finish"] else "-", "ok",
                     "Last recorded recovery " + stamp_review(parts["finish"]) if parts["finish"] else
                     "Recovery not recorded for the latest host loss." if parts["open_observation"] else "No recovery recorded."),
                 boxes[-1], box("Noise", len(noise), "info", "Changes on %s, summed up by scope" % _count(
                     len({(e["vcenter"], e["vm"] or e["entity"]) for e in noise if _scope(e) == "vm"}), "VM"))]
        if general:
            h_subject, h_scope = "Outage review: " + parts["head_name"], [general.partition(" - ")[2]]
        h_counts = [[length, obj + " still down", "critical"] for t, obj, length in parts["down_times"] if t][:1]
        if down_s:
            h_counts.append([_span(down_s), "hosts down", "warning"])
        if to_ticket is not None:
            h_counts.append([_span(to_ticket), "to the ticket", "ok"])
        out[2:2] = ([empty_reason] if empty_reason else []) + [parts["went_note"]] + [s["title"] + ": " + " | ".join(", ".join(row) for row in s["rows"]) for s in summary] + noise_lines + [note]
    return _unsafe({"title": title, "head_counts": h_counts, "draft": draft, "head_scope": h_scope, "head_subject": h_subject, "numbers": numbers, "boxes": boxes, "cards": cards, "sections": email, "web_sections": web, "sheet": sheet,
            "head_name": parts["head_name"] if review and general else None, "issues": issues if review else [], "root_judgments": judged,
            "sheets": sheets if review else [sheet], "workbook_stem": stem if review else "", "events_file": filename if review else "", "lines": out, "aar": aar, "window": window_text, "events": len(evs), "act_in": act_in,
            "skip": bool(blip and o.get("skip_blips") and not unread_centers),
            "cleared_before_review": cleared_only, "blip": blip,
            "alarm_window": [r_from.strftime("%Y-%m-%dT%H:%M:%SZ"), r_to.strftime("%Y-%m-%dT%H:%M:%SZ")] if cleared_runs else [],
            "trigger": trig, "trigger_line": "Triggered by: " + trig[0] + ((" (and %d more)" % (len(trig) - 1)) if len(trig) > 1 else "")})


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


def outage_timeline_doc(doc, timeline, web=False, review=False):
    """The watch's report with the time line. A review (vm_watch_review): its title and its tiles, the cards, the time
    line, the events and the lines for the after-action report, then the watch's own sections as they are. A normal
    run that found something down (vm_watch_timeline): the watch's report as it is, with the time line and the events
    after its first section."""
    if not review:                           # 1.116.0: a scheduled run is 1.118.4's, word for word
        return _v1184.outage_timeline_doc(doc, timeline, web, review)
    if review:
        doc = _redact(doc)
    if not doc or not timeline:
        return _unsafe(doc)
    out = dict(doc)
    own = list(doc.get("sections") or [])
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
        if any(s.get("title") == "3. Down now" for s in mine):
            own = [s for s in own if s.get("title") != "Down or hung now" and not s.get("title", "").startswith("VMs with files on ")]
        if timeline.get("root_judgments"):
            at = next((i for i, s in enumerate(own) if s.get("title") == "How it judged"), len(own))
            section = own[at] if at < len(own) else {"title": "How it judged"}
            own[at:at + 1] = [dict(section, lines=list(section.get("lines") or []) + timeline["root_judgments"])]
        out["sections"] = _with_act(mine + own, timeline.get("act") or {}, web)
        out["outage_ops_center"] = (timeline.get("draft") or {}).get("outage_ops_center", "NOC")
        out["status_prefix"] = "Now: "                      # 1.90.0: the verdict is the watch's state now, not the outage's
    else:
        mine = [s for s in mine if s.get("title") in ("What happened, in order", "The events")]
        out["sections"] = own[:1] + mine + own[1:]
    return _unsafe(out)


def outage_review_aar(doc, info=None, web=False):
    """1.112.0: the review's page and email with its AAR draft. info: file (the Word file's name, '' = none), error (why it
    was not made), job (this AAP job's number), asked (a review with a draft, vm_watch_review_docx on). The web page gets
    head_file - "Download AAR (Word)" in its head (report_head_file.html.j2) -, and "For the after-action report" says
    where the draft is and how the AAR job fills from it; the lines to paste stay, for an AAR job without its AAP credential."""
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
        if web and csv_file and sec.get("title") == "The events":
            sec = dict(sec, download_file=csv_file)
        if web and sec.get("kind") == "noc":
            sec = dict(sec, noc_file=noc_file or "", noc_error=("The %s handoff was not made: %s" % (ops_center, noc_error)) if noc_error else "")
        if sec.get("title") == "For the after-action report":
            sec = dict(sec, lines=lines + (["Without the AAP credential on the AAR job, paste these into its survey instead:"] +
                                           list(sec.get("lines") or []) if sec.get("lines") else []),
                       text="The after action report, filled from this review. ACT's causes are in it as suggestions, never as the root cause.")
        secs.append(sec)
    out["sections"] = secs
    return _unsafe(out)


class FilterModule(object):
    def filters(self):
        return {"outage_timeline": outage_timeline, "outage_timeline_doc": outage_timeline_doc, "outage_window": outage_window,
                "outage_review_aar": outage_review_aar, "outage_events_csv": outage_events_csv, "outage_redact": outage_redact}
