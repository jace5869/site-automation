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
import os
import re

_spec = importlib.util.spec_from_file_location(
    "site_time", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "module_utils", "site_time.py"))
_tm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tm)

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
            where = _short(e.get("entity")) or where
            kind = "alarm:" + alarm
        elif not label:
            label = message[:120] or typ
        out.append({"t": t, "lane": lane, "level": level, "label": label, "where": where, "kind": kind, "role": role,
                    "type": typ, "message": message, "vcenter": str(e.get("vcenter") or ""), "vm": vm, "cluster": cluster,
                    "host": host, "n": 1, "names": []})
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
            g["n"] += 1
            continue
        g = dict(e, last=e["t"], names=[e["vm"]] if e["vm"] else [], where=e["cluster"] or e["where"])
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
    (the clear is not a row of its own); low_seconds 0 = never. -> (events, the hosts' down stretches [(host, from, to or None)])."""
    gone, downs, open_down = set(), [], {}
    for i, e in enumerate(evs):
        if e["role"] not in ("problem", "down"):
            continue
        want = "clear" if e["role"] == "problem" else "up"
        for j in range(i + 1, len(evs)):
            c = evs[j]
            if j in gone or c["role"] != want or c["kind"] != e["kind"] or c["where"] != e["where"]:
                continue
            took = (c["t"] - e["t"]).total_seconds()
            if low_seconds > 0 and took <= low_seconds:
                e["level"], e["label"] = "low", "%s, back after %s" % (e["label"], _span(took))
                e["flap"] = True
                gone.add(j)
            break
    kept = [e for i, e in enumerate(evs) if i not in gone]
    for e in kept:
        if e["role"] == "down" and not e.get("flap") and e["where"] not in open_down:
            open_down[e["where"]] = e["t"]
        elif e["role"] == "up" and e["where"] in open_down:
            downs.append((e["where"], open_down.pop(e["where"]), e["t"]))
    downs += [(h, t, None) for h, t in open_down.items()]
    downs.sort(key=lambda d: d[1])
    return kept, downs


def _same(evs):
    """The same event at the same place again within a minute: one row with a count."""
    out, last = [], {}
    for e in evs:
        k = (e["lane"], e["level"], e["label"], e["where"])
        p = last.get(k)
        if p is not None and (e["t"] - p["seen"]).total_seconds() <= SAME_S and not e.get("count"):
            p["n"] += 1
            p["seen"] = e["t"]
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


def _ticket_events(tickets, begin, end):
    """-> (events, tickets opened before the stretch and still open in it, the first root-cause text found)."""
    out, before, cause = [], [], ""
    for k in tickets or []:
        if not isinstance(k, dict):
            continue
        num, short = str(k.get("number") or "ticket"), str(k.get("short") or "")
        where, desc = _ticket_host(short), str(k.get("description") or "")
        m = re.search(r"^\s*Severity:\s*(\w+)", desc, re.M | re.I)
        sev = (m.group(1).lower() if m else "warning")
        sev = sev if sev in ("critical", "warning", "low") else "warning"
        opened = _time(k.get("opened"))
        base = {"where": where, "kind": "", "role": "", "type": "", "vcenter": "", "n": 1, "names": [], "ticket": num}
        act = _act_block(desc)
        if opened is not None and begin <= opened <= end:
            out.append(dict(base, t=opened, lane="servicenow", level=sev, label="Ticket %s opened" % num, message=short, role="ticket"))
            if act:
                out.append(dict(base, t=opened, lane="act", level="info", label="ACT analysis in %s: %s" % (num, _cut(act)), message=act))
        elif opened is not None and opened < begin:
            before.append(num)
        if act and not cause:
            cause = "ACT in %s: %s" % (num, _cut(act, 300))
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
                cause = cause or "ACT in %s: %s" % (num, _cut(text, 300))
            else:
                out.append(dict(base, t=t, lane="servicenow", level="info", label="Work note in %s: %s" % (num, _cut(text, 120)), message=text))
        done, how = _time(k.get("resolved")), "resolved"
        if done is None:
            done, how = _time(k.get("closed")), "closed"
        if done is not None and begin <= done <= end:
            notes = str(k.get("close_notes") or "").strip()
            out.append(dict(base, t=done, lane="servicenow", level="ok", role="resolved",
                            label="Ticket %s %s%s" % (num, how, (": " + _cut(notes, 120)) if notes else ""), message=notes or short))
            if notes and not cause.startswith("ACT"):
                cause = cause or "Close notes of %s: %s" % (num, _cut(notes, 300))
    return out, before, cause


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
            if RANK[m["level"]] > RANK[p["level"]]:
                p["level"] = m["level"]
            ends[p["row"]] = max(ends[p["row"]], p["x"] + (30 + 7.5 * len(p["label"])) * 50.0 / TRACK_PX)
    return placed


# ---- the review -------------------------------------------------------------------------------------------

def outage_timeline(events, tickets=None, opts=None):
    """What vCenter (events: site_vmware_outage_events) and ServiceNow (tickets: site_outage_tickets) recorded between
    opts.begin and opts.end -> {title, numbers, boxes, cards, sections (an email), web_sections (the website: with the
    chart), sheet (the workbook), lines (the job's output), aar}. opts: begin, end, time_zone, low_seconds, review,
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

    days = loc(begin).date() != loc(end).date()

    def hm(t, sec=False):
        return loc(t).strftime(("%d %b " if days else "") + ("%H:%M:%S" if sec else "%H:%M"))

    def full(t):
        return loc(t).strftime("%Y-%m-%d %H:%M ") + zname(t)

    vc = [e for e in _vcenter_events(events) if begin <= e["t"] <= end]
    vc, restarted = _restarts(vc)
    vc, downs = _pairs(vc, low_seconds)
    tk, before, cause = _ticket_events(tickets, begin, end)
    evs = _same(sorted(vc + tk, key=lambda e: (e["t"], [x[0] for x in LANES].index(e["lane"]))))
    for i, e in enumerate(evs):
        e["num"] = i + 1
        e["what"] = e["label"] + ((" (x%d)" % e["n"]) if e["n"] > 1 else "")

    # the numbers
    signals = [e for e in evs if e["lane"] in ("vcenter", "host") and e["level"] in BAD]
    first = signals[0] if signals else None
    first_bad = next((e for e in signals if e["level"] in ("critical", "warning")), None)
    tickets_opened = [e for e in evs if e["role"] == "ticket"]
    # the outage's ticket: the first one opened after the first signal (one added by number may be older)
    opened = next((e for e in tickets_opened if first and e["t"] >= first["t"]), tickets_opened[0] if tickets_opened else None)
    resolved = [e for e in evs if e["role"] == "resolved"]
    to_ticket = (opened["t"] - first["t"]).total_seconds() if first and opened and opened["t"] >= first["t"] else None
    still = [d for d in downs if d[2] is None]
    spans = [(h, a, b or end, b is None) for h, a, b in downs]
    longest = max(spans, key=lambda d: (d[2] - d[1]).total_seconds()) if spans else None
    down_s = (longest[2] - longest[1]).total_seconds() if longest else 0
    hosts_down = sorted({d[0] for d in spans})
    last_bad = max([e["t"] for e in evs if e["lane"] in ("vcenter", "host") and e["level"] in ("critical", "warning")] or [begin])
    goods = [e for e in evs if e["lane"] in ("vcenter", "host") and e["level"] == "ok" and e["t"] >= last_bad]
    back = None if still or not signals else (goods[-1] if goods else None)
    numbers = {
        "events": len(evs), "first_signal": first["t"].strftime("%Y-%m-%dT%H:%M:%SZ") if first else "",
        "ticket": opened["ticket"] if opened else "", "signal_to_ticket_s": int(to_ticket) if to_ticket is not None else -1,
        "down_s": int(down_s), "hosts_down": hosts_down, "still_down": sorted(d[0] for d in still), "ha_restarted": restarted,
        "back": back["t"].strftime("%Y-%m-%dT%H:%M:%SZ") if back else ""}

    window_text = "%s to %s" % (full(begin), full(end) if days else loc(end).strftime("%H:%M ") + zname(end))
    length = _span((end - begin).total_seconds())
    down_word = ("%s down: %s" % (hosts_down[0], _span(down_s)) if len(hosts_down) == 1 else
                 "%d hosts down: up to %s" % (len(hosts_down), _span(down_s))) if spans else ""

    # the title
    # the headline: the first problem - or a critical one in the same minute (one cause often posts several events)
    head = first_bad or first
    if first_bad is not None:
        near = [e for e in signals if e["level"] == "critical" and (e["t"] - first_bad["t"]).total_seconds() <= SAME_S]
        # the host's own event says more than the alarm about it
        head = next((e for e in near if e["lane"] == "host"), near[0] if near and first_bad["level"] != "critical" else head)
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

    # tiles
    def box(label, value, status, text):
        return {"label": label, "value": value, "status": status, "note": text}

    boxes = [box("Window", length, "info", window_text),
             box("Events", len(evs), "info", "%d on the vCenter and host lanes" % len([e for e in evs if e["lane"] in ("vcenter", "host")])),
             box("Host down", _span(down_s) if spans else "0", "critical" if spans else "ok",
                 ("%s%s" % (", ".join(hosts_down[:3]) + (" and %d more" % (len(hosts_down) - 3) if len(hosts_down) > 3 else ""),
                            "; still down at the end of the window" if still else "")) if spans else "no host lost in the window"),
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
    if first:
        cards.append({"status": first["level"], "chip": "First signal", "meta": "%s %s" % (hm(first["t"], True), zname(first["t"])),
                      "title": first["what"], "subject": first["where"],
                      "next": "Row %d of the events. %s lane." % (first["num"], LANE[first["lane"]])})
    if to_ticket is not None:
        cards.append({"status": "warning" if to_ticket > 900 else "ok", "chip": "First signal to ticket", "meta": _span(to_ticket),
                      "title": "%s opened at %s %s" % (opened["ticket"], hm(opened["t"]), zname(opened["t"])), "subject": opened["where"],
                      "next": "Row %d of the events." % opened["num"]})
    elif first:
        why = (str(o.get("tickets_error")) if o.get("tickets_error") else "No ticket of the health workflows was opened in the window."
               if o.get("tickets_asked") else "ServiceNow was not read: attach the ServiceNow API credential to the job template.")
        cards.append({"status": "info", "chip": "First signal to ticket", "meta": "-", "title": "No ticket to measure", "next": why})
    if first:
        if still:
            cards.append({"status": "critical", "chip": "Back to normal", "meta": "not yet",
                          "title": "%s still down at the end of the window" % ", ".join(sorted(d[0] for d in still)[:3]),
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
    lanes = []
    for key, name in LANES:
        mine = [e for e in evs if e["lane"] == key]
        marks = _place([{"at": _pct(e["t"], z0, z1), "level": e["level"], "label": str(e["num"]), "nums": [e["num"]],
                         "tip": "%d  %s %s  %s\n%s%s" % (e["num"], hm(e["t"], True), zname(e["t"]), WORD[e["level"]], e["what"],
                                                         (" - " + e["where"]) if e["where"] else "")} for e in mine])
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
    whole = (end - begin).total_seconds()
    overview = None
    if evs and (z1 - z0).total_seconds() < 0.6 * whole:
        overview = {"from": _pct(z0, begin, end), "to": _pct(z1, begin, end), "begin": hm(begin), "end": hm(end),
                    "marks": [{"at": _pct(e["t"], begin, end), "level": e["level"]} for e in evs],
                    "text": "The whole window, %s. The box is the part shown below: %s to %s." % (window_text, hm(z0), hm(z1))}
    chart = {"label": "Outage time line, %s to %s %s" % (hm(z0), hm(z1), zname(z1)), "lanes": lanes, "ticks": ticks, "bands": bands,
             "overview": overview, "zone": zname(z0),
             "legend": [{"level": k, "word": WORD[k]} for k in ("critical", "warning", "low", "info", "ok")],
             "band_word": down_word,
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
    if not evs:
        table = {"title": "The events", "status": "ok", "lines": ["No outage event between %s." % window_text] + notes}

    # for the after-action report
    aar = []
    if first:
        start = first_bad or first
        aar.append("Start (incident_start): %s - %s%s" % (full(start["t"]), start["what"], (" on " + start["where"]) if start["where"] else ""))
        aar.append("End (incident_end): %s" % (("%s - %s" % (full(back["t"]), back["what"])) if back else
                                               "not yet - %s still down at the end of the window" % ", ".join(sorted(d[0] for d in still))
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
    web = [s for s in (card_section, line if evs else None, table, aar_section) if s]
    sheet = {"name": "Timeline",
             "columns": ["#", "Time (UTC)", "Time (%s)" % (o.get("time_zone") or "UTC"), "Source", "Where", "What happened", "Level", "Count",
                         "Ticket", "vCenter", "Event type", "Message"],
             "rows": [[e["num"], e["t"].strftime("%Y-%m-%d %H:%M:%S"), loc(e["t"]).strftime("%Y-%m-%d %H:%M:%S ") + zname(e["t"]),
                       LANE[e["lane"]], e["where"], e["label"], WORD[e["level"]], e.get("count") or e["n"], e.get("ticket", ""),
                       e["vcenter"], e["type"], (", ".join(e["names"]) + ": " if e["names"] else "") + e.get("message", "")] for e in evs]}
    out = ["Outage time line, %s: %d event(s)%s%s%s" % (
        window_text, len(evs), ("; first signal %s" % hm(first["t"], True)) if first else "",
        ("; " + down_word) if down_word else "", ("; ticket after %s" % _span(to_ticket)) if to_ticket is not None else "")]
    out += ["  %2d  %s  %-14s %-16s %s  [%s]" % (e["num"], hm(e["t"], True), LANE[e["lane"]], e["where"], e["what"], WORD[e["level"]])
            for e in evs[:60]]
    if len(evs) > 60:
        out.append("  ... and %d more event(s): the report's table and the workbook have every one." % (len(evs) - 60))
    if not o.get("tickets_asked"):
        out.append("ServiceNow was not read: attach the ServiceNow API credential to the job template.")
    out += notes
    return {"title": title, "numbers": numbers, "boxes": boxes, "cards": cards, "sections": email, "web_sections": web, "sheet": sheet,
            "lines": out, "aar": aar, "window": window_text, "events": len(evs)}


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
        out["summary"] = list(timeline.get("boxes") or [])
        out["subtitle"] = "Review of %s. Now: %s" % (timeline.get("window", ""), doc.get("title", ""))
        out["sections"] = mine + own
    else:
        mine = [s for s in mine if s.get("title") in ("What happened, in order", "The events")]
        out["sections"] = own[:1] + mine + own[1:]
    return out


class FilterModule(object):
    def filters(self):
        return {"outage_timeline": outage_timeline, "outage_timeline_doc": outage_timeline_doc, "outage_window": outage_window}
