# -*- coding: utf-8 -*-
"""The outage review's time line (1.66.0): what vCenter and ServiceNow recorded around an outage, in order, on four
lanes - vCenter alarms, host events, ServiceNow, ACT - with the three numbers a review asks for: the first signal, the
time from that signal to the ticket, and how long a host was down. The VM outage watch makes it (roles/vmware_vm,
tasks/watch.yml: vm_watch_review, vm_watch_timeline) from what site_vmware_outage_events and site_outage_tickets
read. Pure functions: no network, no clock (the stretch of time is given), so tests/test_outage_timeline.py proves
every rule. The events table is the source: the chart only places its rows on a time axis."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import importlib.util
import json
import os
import re

try:                                       # 1.112.0 (Neon P1): ticket and vCenter text stays text - never templated again
    from ansible.utils.unsafe_proxy import wrap_var as _wrap
    _OLD = type(_wrap("x")) is not str     # ansible-core before 2.19; later ones never template a filter's result
except ImportError:                        # the unit tests, without Ansible
    _OLD = False


def _unsafe(v):
    return _wrap(v) if _OLD else v

_spec = importlib.util.spec_from_file_location(
    "site_time", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "module_utils", "site_time.py"))
_tm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tm)
_spec = importlib.util.spec_from_file_location(
    "site_link", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "module_utils", "site_link.py"))
_lk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_lk)

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
    "VmDasBeingResetEvent": ("vcenter", "warning", "HA reset a VM without a heartbeat", "", ""),
    "VmDasBeingResetWithScreenshotEvent": ("vcenter", "warning", "HA reset a VM without a heartbeat", "", ""),
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
ALARM = {"red": ("critical", "problem"), "yellow": ("warning", "problem"), "green": ("ok", "clear"), "gray": ("info", "")}
RESTART_GROUP_S = 300      # HA restarts of one cluster within this many seconds are one event, "HA restarted 14 VMs"
SAME_S = 60                # the same event at the same place again within this many seconds: one row, "x3"
STEPS_MIN = (1, 2, 5, 10, 15, 30, 60, 120, 180, 360, 720, 1440, 2880, 10080)
TRACK_PX = 580.0           # the narrowest track the marks must fit (the 800 px window)
ROWS = 3
LEAD_S = 1800              # 1.90.0: the lead-up looks back at most this far before the first critical event
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
    """The stretch of time a review reads -> {begin, end (seconds since 1970), error}. Without start: the last hours
    before now. start and end (vm_watch_review_from, vm_watch_review_to) are times in report_time_zone, as
    2026-11-12 01:00; end alone is an error; start alone runs for hours, at most until now."""
    now = float(now)
    try:
        hours = float(hours)
    except (TypeError, ValueError):
        hours = 6.0
    hours = min(max(hours, 0.25), 24.0 * 31)
    tz, _note = _tm.zone(time_zone)

    def at(text, name):
        m = re.match(r"^(\d{4})-(\d\d)-(\d\d)(?:[ T](\d\d):(\d\d)(?::(\d\d))?)?$", str(text).strip())
        if not m:
            raise ValueError("%s is not a time like 2026-11-12 01:00: %s" % (name, text))
        try:
            return datetime.datetime(*[int(x or 0) for x in m.groups()], tzinfo=tz or UTC).timestamp()
        except ValueError:
            raise ValueError("%s is not a time like 2026-11-12 01:00: %s" % (name, text))

    try:
        if not str(start or "").strip():
            if str(end or "").strip():
                raise ValueError("vm_watch_review_to needs vm_watch_review_from (the start of the window)")
            return {"begin": now - hours * 3600, "end": now, "error": ""}
        b = at(start, "vm_watch_review_from")
        e = at(end, "vm_watch_review_to") if str(end or "").strip() else min(now, b + hours * 3600)
    except ValueError as x:
        return {"begin": now - hours * 3600, "end": now, "error": str(x)}
    if e <= b:
        return {"begin": now - hours * 3600, "end": now,
                "error": "vm_watch_review_to is not after vm_watch_review_from (or vm_watch_review_from is not in the past)"}
    return {"begin": b, "end": e, "error": ""}


# ---- the events -------------------------------------------------------------------------------------------

def _vcenter_events(events):
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
        where = host or vm or cluster or str(e.get("datacenter") or "") or str(e.get("vcenter") or "")
        if typ == "AlarmStatusChangedEvent":
            alarm, to = str(e.get("alarm") or "an alarm"), str(e.get("to") or "").lower()
            level, role = ALARM.get(to, ("info", ""))
            label = "Alarm %s: %s" % ({"red": "red", "yellow": "yellow", "green": "back to green", "gray": "gray"}.get(to, to or "changed"), alarm)
            where = _short(e.get("entity")) or where           # the Where label: short (1.90.2: the VM's identity keeps the raw entity)
            kind = "alarm:" + alarm
        elif not label:
            label = message[:120] or typ
        out.append({"t": t, "lane": lane, "level": level, "label": label, "where": where, "kind": kind, "role": role,
                    "type": typ, "message": message, "vcenter": str(e.get("vcenter") or ""), "vm": vm, "cluster": cluster,
                    "host": host, "entity": str(e.get("entity") or "").strip(), "entity_type": str(e.get("entity_type") or "").lower(),
                    "n": 1, "names": []})
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
        g = open_.get((e["vcenter"], e["cluster"]))
        if g is not None and (e["t"] - g["last"]).total_seconds() <= RESTART_GROUP_S:
            g["last"] = e["t"]
            if e["vm"] and e["vm"] not in g["names"]:
                g["names"].append(e["vm"])
            if (e["host"], e["vm"]) not in g["members"]:
                g["members"].append((e["host"], e["vm"]))      # 1.90.2: each restart's own host and VM, for the page's filters
            g["n"] += 1
            continue
        g = dict(e, last=e["t"], names=[e["vm"]] if e["vm"] else [], where=e["cluster"] or e["where"], members=[(e["host"], e["vm"])])
        open_[(e["vcenter"], e["cluster"])] = g
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
    for i, e in enumerate(evs):
        if e["role"] not in ("problem", "down"):
            continue
        want = "clear" if e["role"] == "problem" else "up"
        for j in range(i + 1, len(evs)):
            c = evs[j]
            if j in gone or c["role"] != want or c["kind"] != e["kind"] or c["where"] != e["where"] or c.get("vcenter", "") != e.get("vcenter", ""):
                continue
            took = (c["t"] - e["t"]).total_seconds()
            if low_seconds > 0 and took <= low_seconds:
                e["level"], e["label"] = "low", "%s, back after %s" % (e["label"], _span(took))
                e["flap"] = True
                gone.add(j)
            break
    kept = [e for i, e in enumerate(evs) if i not in gone]
    for e in kept:
        dk = (e.get("vcenter", ""), e["where"])               # the same name on two vCenters is two hosts
        if e["role"] == "down" and not e.get("flap") and dk not in open_down:
            open_down[dk] = e["t"]
        elif e["role"] == "up" and dk in open_down:
            downs.append((e["where"], open_down.pop(dk), e["t"], dk[0]))
    downs += [(h[1], t, None, h[0]) for h, t in open_down.items()]
    downs.sort(key=lambda d: d[1])
    return kept, downs


def _same(evs):
    """The same event at the same place again within a minute: one row with a count."""
    out, last = [], {}
    for e in evs:
        k = (e["lane"], e["level"], e["label"], e["where"], e.get("vcenter", ""), e["kind"])
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


def _ticket_host(short):
    m = re.match(r"^\s*(?:\[[^\]]*\]\s*)?([^\s:]+):", str(short or ""))
    return _short(m.group(1)) if m else ""


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


def _ticket_events(tickets, begin, end, instance="", table=""):
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
            if t is None or not text or not begin <= t <= end or text.startswith("Still failing at"):
                continue
            if text.startswith("The automated check passes again"):
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
    return (e.get("vcenter", ""), e["kind"], e["where"])


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
            r = {"vm": e.get("vm") or "", "where": e["where"], "alarm": e["kind"][6:], "level": e["level"],
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


# ---- the review -------------------------------------------------------------------------------------------

def outage_timeline(events, tickets=None, opts=None):
    """What vCenter (events: site_vmware_outage_events) and ServiceNow (tickets: site_outage_tickets) recorded between
    opts.begin and opts.end -> {title, numbers, boxes, cards, sections (an email), web_sections (the website: with the
    chart), sheet (the workbook), lines (the job's output), aar}. opts: begin, end, instance and table (the ServiceNow address and table, for the links to the incidents), time_zone, low_seconds, review,
    tickets_asked, tickets_error, notes_error, capped, report_on, min_severity, job."""
    o = dict(opts or {})
    begin, end = _time(o.get("begin")), _time(o.get("end"))
    if begin is None or end is None or end <= begin:
        return {}
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
    all_vc = _vcenter_events(events)
    begin_asked = begin
    pre = _pre_raises(all_vc, begin, end)
    if pre:
        begin = min(e["t"] for e in pre).replace(second=0, microsecond=0)
    days = loc(begin).date() != loc(end).date()

    def hm(t, sec=False):
        return loc(t).strftime(("%d %b " if days else "") + ("%H:%M:%S" if sec else "%H:%M"))

    def full(t):
        return loc(t).strftime("%Y-%m-%d %H:%M ") + zname(t)

    vc = [e for e in all_vc if begin <= e["t"] <= end and (e["t"] >= begin_asked or any(e is p for p in pre))]
    runs, unmatched = _alarm_runs(vc)                       # before a quick clear is folded into its raise
    vc, restarted = _restarts(vc)
    vc, downs = _pairs(vc, low_seconds)
    instance = o.get("instance") or ""
    tk, before, causes = _ticket_events(tickets, begin_asked, end, instance, o.get("table") or "")
    evs = _same(sorted(vc + tk, key=lambda e: (e["t"], [x[0] for x in LANES].index(e["lane"]))))
    for i, e in enumerate(evs):
        e["num"] = i + 1
        e["what"] = e["label"] + ((" (x%d)" % e["n"]) if e["n"] > 1 else "")
    subject = None

    # the numbers
    signals = [e for e in evs if e["lane"] in ("vcenter", "host") and e["level"] in BAD]
    first = signals[0] if signals else None
    first_bad = next((e for e in signals if e["level"] in ("critical", "warning")), None)
    tickets_opened = [e for e in evs if e["role"] == "ticket"]
    # the outage's ticket: the first one opened after the first signal (one added by number may be older)
    opened = next((e for e in tickets_opened if first and e["t"] >= first["t"]), tickets_opened[0] if tickets_opened else None)
    resolved = [e for e in evs if e["role"] == "resolved"]
    to_ticket = (opened["t"] - first["t"]).total_seconds() if first and opened and opened["t"] >= first["t"] else None
    # 1.112.0 (Codex P1): the root-cause text of the outage's own ticket - the one measured, else one about a host of the outage's
    # signals; another ticket of the window (another host's disk) never gives it
    own_hosts = set(e["where"] for e in signals if e["where"])
    own_tickets = [e["ticket"] for e in ([opened] if opened else []) + tickets_opened if e["where"] in own_hosts or
                   (e is opened and not e["where"])]          # a ticket that names no host: only the measured one
    cause = next((causes[n] for n in own_tickets if n in causes), "")
    still = [d for d in downs if d[2] is None]
    cleared_runs = [r for r in runs if r["cleared"] is not None]
    open_alarms = [r for r in runs if r["cleared"] is None]
    fallback = set((r["vcenter"], r["vm"] or r["where"]) for r in cleared_runs if r["vm"] or r["where"])
    subject = _relate(evs, downs, fallback, set((r["vcenter"], "alarm:" + r["alarm"], r["where"]) for r in cleared_runs))
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
    spans = [(shown(d), d[1], d[2] or end, d[2] is None) for d in downs]
    longest = max(spans, key=lambda d: (d[2] - d[1]).total_seconds()) if spans else None
    down_s = (longest[2] - longest[1]).total_seconds() if longest else 0
    hosts_down = sorted({d[0] for d in spans})
    last_bad = max([e["t"] for e in evs if e["lane"] in ("vcenter", "host") and e["level"] in ("critical", "warning")] or [begin])
    goods = [e for e in evs if e["lane"] in ("vcenter", "host") and e["level"] == "ok" and e["t"] >= last_bad]
    back = None if still or not signals else (goods[-1] if goods else None)
    # 1.79.12: everything had already cleared at the review - the alarms that raised it went back to normal and nothing is down
    # (alarms only: a host that was lost, an HA restart or a storage problem is the window's own story, not this one)
    live = [c for c in (o.get("triggers") or []) if isinstance(c, dict) and c.get("summary")]      # what the watch found now: it comes first
    cleared_only = (bool(cleared_runs) and not open_alarms and not spans and not live
                    and all(str(e.get("kind") or "").startswith("alarm:") for e in signals))
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
    if not trig and standing:
        for e in sorted(standing, key=lambda x: x["t"])[:6]:
            trig.append("CRITICAL: %s on %s: red since %s, still red at the end of the window" % (e["kind"][6:], e["where"] or "?", hm(e["t"], True)))
    if not trig and (cleared_runs or (spans and not still)):
        for h, a_, b_, _hv in [d for d in downs if d[2] is not None][:3]:
            trig.append("%s: not responding %s to %s (%s), back before the review" % (h, hm(a_, True), hm(b_, True), _span((b_ - a_).total_seconds())))
        for r in sorted(cleared_runs, key=lambda x: x["raised"])[:6]:
            trig.append("%s on %s: raised %s, cleared %s (%s later)" % (r["alarm"], r["vm"] or r["where"] or "?", hm(r["raised"], True), hm(r["cleared"], True),
                                                                        _span((r["cleared"] - r["raised"]).total_seconds())))
        trig.insert(0, "Nothing was down when the watch read vCenter: what raised it had already cleared.")
    if not trig:
        trig = ["Nothing triggered it: the review was asked for (vm_watch_review) and the watch found nothing down or hung when it read vCenter."]
    numbers = {
        "events": len(evs), "first_signal": first["t"].strftime("%Y-%m-%dT%H:%M:%SZ") if first else "",
        "ticket": opened["ticket"] if opened else "", "signal_to_ticket_s": int(to_ticket) if to_ticket is not None else -1,
        "down_s": int(down_s), "hosts_down": hosts_down, "still_down": sorted(shown(d) for d in still), "ha_restarted": restarted,
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
        parts = []
        if spans:
            parts.append("%s down%s" % (_span(down_s), ", still down" if still else ""))
        if to_ticket is not None:
            parts.append("ticket after %s" % _span(to_ticket))
        elif head and o.get("tickets_asked") and not o.get("tickets_error") and not before:
            parts.append("no ticket")
        lead = ("%s: %s" % (head["where"], _lower_first(head["label"])) if head["where"] else head["label"]) if head else \
            "%d event%s, none a problem" % (len(evs), "" if len(evs) == 1 else "s")
        title = "Outage review: %s%s" % (_cut(lead, 90), ("; " + ", ".join(parts)) if parts else "")
        h_counts = ([[_span(down_s), "down, still down" if still else "down", "critical"]] if spans else []) + \
                   ([[_span(to_ticket), "to the ticket", "warning" if to_ticket > 3600 else "ok"]] if to_ticket is not None else
                    ([["", "no ticket", "warning"]] if "no ticket" in parts else []))
        h_scope = [_cut(lead, 120)]
        h_subject = "Outage review: %s" % _cut(lead, 40)
        if cleared_only:
            title = "Outage review: cleared before the review - %s: %s, raised %s, cleared %s%s" % (
                _cut(c_text or "alarms", 40), _cut(", ".join(c_alarms[:2]), 60), hm(r_from), hm(r_to), " (a short usage blip, Low)" if blip else "")

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
                      "next": "Row %d of the events. %s lane." % (first["num"], LANE[first["lane"]])})
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

    # the chart (the website)
    z0, z1 = _zoom(evs, downs, begin, end, tz)
    _step, tks = _ticks(z0, z1, tz)
    ticks = []
    for i, t in enumerate(tks):
        text = t.strftime("%H:%M")
        if days and (i == 0 or t.date() != tks[i - 1].date()):
            text = t.strftime("%d %b %H:%M")
        ticks.append({"at": _pct(t, z0, z1), "text": text, "wide": len(tks) > 5 and i % 2 == 1})
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
    if evs:
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
            "text": "" if evs else chart["none"]}
    card_section = {"title": "The outage in short", "cards": cards} if cards else None
    email = [s for s in (card_section, table, aar_section) if s]
    web = [s for s in (card_section, line if evs else None, web_table, aar_section) if s]
    sheet = {"name": "Timeline",
             "columns": ["#", "Time (UTC)", "Time (%s)" % (o.get("time_zone") or "UTC"), "Source", "Where", "What happened", "Level", "Count",
                         "Ticket", "vCenter", "Event type", "Message"],
             "rows": [[e["num"], e["t"].strftime("%Y-%m-%d %H:%M:%S"), loc(e["t"]).strftime("%Y-%m-%d %H:%M:%S ") + zname(e["t"]),
                       LANE[e["lane"]], e["where"], e["label"], WORD[e["level"]], e.get("count") or e["n"], e.get("ticket", ""),
                       e["vcenter"], e["type"], (", ".join(e["names"]) + ": " if e["names"] else "") + e.get("message", "")] for e in evs]}
    out = ["Outage time line, %s: %d event(s)%s%s%s" % (
        window_text, len(evs), ("; first signal %s" % hm(first["t"], True)) if first else "",
        ("; " + down_word) if down_word else "", ("; ticket after %s" % _span(to_ticket)) if to_ticket is not None else "")]
    out.insert(1, "Triggered by: " + " | ".join(trig))
    out += ["  %2d  %s  %-14s %-16s %s  [%s]" % (e["num"], hm(e["t"], True), LANE[e["lane"]], e["where"], e["what"], WORD[e["level"]])
            for e in evs[:60]]
    if len(evs) > 60:
        out.append("  ... and %d more event(s): the report's table and the workbook have every one." % (len(evs) - 60))
    if not o.get("tickets_asked"):
        out.append("ServiceNow was not read: attach the ServiceNow API credential to the job template. " + ATTACH)
    out += notes

    # 1.112.0: the AAR draft - what the review knows, for the after action report (incident_doc_filters: incident_review_fill,
    # incident_aar_docx). The review run writes it as "AAR (Word)", and the AAR job reads it back from the job's artifact
    # (vm_outage_review.aar). Facts only: the root cause is never in it; ACT's causes go beside it as suggestions.
    draft = {}
    if first:
        start = first_bad or first

        def stamp(t, sec=False):               # 1.112.0 (Neon P1): always the day, the time and the time zone
            return loc(t).strftime("%d %b %H:%M" + (":%S" if sec else "")) + " " + zname(t)
        where_name = (head or start)["where"] or (hosts_down[0] if hosts_down else "")
        impact = []
        for h, a_, b_ in [(shown(d), d[1], d[2]) for d in downs]:
            impact.append("%s down %s (%s to %s)" % (h, _span(((b_ or end) - a_).total_seconds()), stamp(a_),
                                                     stamp(b_) if b_ else "still down at the end of the window"))
        if restarted:
            impact.append("vSphere HA restarted %d VM%s" % (restarted, "" if restarted == 1 else "s"))
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
            elif e["lane"] == "servicenow" and e["level"] == "info":
                actions.append(at + e["what"])
            elif e["role"] == "resolved":
                recovery.append((e["t"], at + e["what"]))
            elif e["lane"] == "host" and e["role"] == "up":
                recovery.append((e["t"], at + e["what"] + ((" (" + e["where"] + ")") if e["where"] else "")))
        if back:
            recovery.append((back["t"], "%s - all back to normal: %s" % (stamp(back["t"]), back["what"])))
        recovery = [x[1] for x in sorted(recovery, key=lambda x: x[0])]
        # the milestones always (Neon P2: a burst of critical events never pushes them out), then the critical events, at most 15
        key_ids = set(id(x) for x in (first, start, back, opened) if x is not None)
        must = [e for e in evs if id(e) in key_ids or e["role"] in ("restart", "resolved", "up")][:15]
        more = [e for e in evs if e["level"] == "critical" and not any(e is m for m in must)][:max(0, 15 - len(must))]
        key = [e for e in evs if any(e is x for x in must + more)]

        def row(e):
            return [loc(e["t"]).strftime("%d %b %H:%M:%S") + ("" if len(zones) == 1 else " " + zname(e["t"])), LANE[e["lane"]],
                    WORD[e["level"]], e["what"], e["where"]]
        tk_rows = []
        for k in tickets or []:
            num = str((k or {}).get("number") or "") if isinstance(k, dict) else ""
            if not num or not any(e.get("ticket") == num for e in evs):
                continue
            op, rs = _time(k.get("opened")), _time(k.get("resolved")) or _time(k.get("closed"))
            tk_rows.append([num, stamp(op) if op else "", stamp(rs) if rs else "not yet",
                            _cut(k.get("short"), 120), _cut(k.get("close_notes"), 200)])
        # 1.112.0 (Codex P1): "still down" only for the stretch whose minutes are shown; another one still open is said apart
        time_down = "no host lost in the window"
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
                             ["Time down", (_span(down_s) + (", still down" if longest and longest[3] else "")) if spans else "0", time_down]],
                 "key": [row(e) for e in key], "events": [row(e) for e in evs[:DRAFT_EVENTS]], "events_total": len(evs),
                 "tickets": tk_rows, "ticket_hint": cause, "follow_up": follow[0][len("Follow-up: "):] if follow else ""}
    return _unsafe({"title": title, "head_counts": h_counts, "draft": draft, "head_scope": h_scope, "head_subject": h_subject, "numbers": numbers, "boxes": boxes, "cards": cards, "sections": email, "web_sections": web, "sheet": sheet,
            "lines": out, "aar": aar, "window": window_text, "events": len(evs), "act_in": act_in,
            "skip": bool(blip and o.get("skip_blips")),
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
    after its first section. An empty timeline: the document as it is."""
    if not timeline or not doc:
        return doc
    out = dict(doc)
    mine = list(timeline["web_sections" if web else "sections"])
    own = list(doc.get("sections") or [])
    if review:
        out["title"] = timeline["title"]
        for k in ("head_counts", "head_scope", "head_subject"):       # 1.82.0
            if timeline.get(k) is not None:
                out[k] = timeline[k]
        out["summary"] = list(timeline.get("boxes") or [])
        out["subtitle"] = "%s Review of %s. Now: %s" % (timeline.get("trigger_line", "").rstrip(".") + "." if timeline.get("trigger_line") else "",
                                                       timeline.get("window", ""), doc.get("title", ""))
        out["subtitle"] = out["subtitle"].strip()
        out["sections"] = _with_act(mine + own, timeline.get("act") or {}, web)
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
    if not doc or not isinstance(info, dict):
        return doc
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
    if not lines:
        return out
    secs = []
    for sec in out.get("sections") or []:
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
                "outage_review_aar": outage_review_aar}
