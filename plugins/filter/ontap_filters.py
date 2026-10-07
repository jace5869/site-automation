# -*- coding: utf-8 -*-
"""Filters for the NetApp ONTAP health report (roles/ontap_report): what
roles/ontap_report/library/site_ontap_collect.py read from each cluster, as a report in the layout
roles/site_email renders - problems first, every row coloured by severity."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import re

RANK = {"critical": 0, "warning": 1, "unknown": 2, "info": 3, "ok": 4, "": 5}


# ---- small helpers --------------------------------------------------------------------------------
def g(d, path, default=None):
    """d['a']['b'] for path 'a.b' (None-safe, also for lists of dicts: the first element)."""
    cur = d
    for part in path.split("."):
        if isinstance(cur, list):
            cur = cur[0] if cur else None
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur if cur is not None else default


def human_bytes(n):
    """1536 -> '1.5 KB' (binary units, as ONTAP shows them). None -> '?'."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(n) < 1024 or unit == "PB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return "?"


def pct(used, size):
    try:
        size = float(size)
        return round(100.0 * float(used) / size, 1) if size > 0 else None
    except (TypeError, ValueError):
        return None


def uptime_text(seconds):
    """93784 -> '1d 02:03' (days, hours:minutes)."""
    try:
        s = int(float(seconds))
    except (TypeError, ValueError):
        return "?"
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    return "%dd %02d:%02d" % (d, h, s // 60)


def duration_seconds(v):
    """A lag or duration in any of ONTAP's forms -> seconds (None when unknown): ISO 8601
    ('P1DT2H3M4S', 'PT3600S'), 'h:mm:ss' or 'd:hh:mm:ss' (the CLI), '1d 2h 3m', or a number."""
    if v is None or v == "" or v == "-":
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip()
    m = re.match(r"^P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$", s, re.I)
    if m and any(m.groups()):
        w, d, h, mi, se = [float(x) if x else 0 for x in m.groups()]
        return int(w * 604800 + d * 86400 + h * 3600 + mi * 60 + se)
    if re.match(r"^\d+(:\d+){1,3}$", s):
        parts = [int(x) for x in s.split(":")]
        while len(parts) < 4:
            parts.insert(0, 0)
        d, h, mi, se = parts
        return d * 86400 + h * 3600 + mi * 60 + se
    total, found = 0, False
    for num, unit in re.findall(r"(\d+)\s*([dhms])", s, re.I):
        total += int(num) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit.lower()]
        found = True
    if found:
        return total
    try:
        return int(float(s))
    except ValueError:
        return None


def duration_text(seconds):
    if seconds is None:
        return "?"
    d, s = divmod(int(seconds), 86400)
    h, s = divmod(s, 3600)
    return ("%dd %02dh %02dm" % (d, h, s // 60)) if d else ("%dh %02dm" % (h, s // 60))


def parse_time(v):
    """An ONTAP time ('2026-10-07T10:22:33-04:00', '...Z', or seconds since 1970) -> aware datetime or None."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) or re.match(r"^\d+(\.\d+)?$", str(v)):
        return datetime.datetime.fromtimestamp(float(v), tz=datetime.timezone.utc)
    s = str(v).strip().replace("Z", "+00:00")
    s = re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", s)
    try:
        t = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)


def time_text(v):
    t = parse_time(v)
    return t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M") if t else (str(v) if v else "")


def level(value, warn, crit, higher_is_worse=True):
    """'critical' / 'warning' / 'ok' for a number against two thresholds."""
    if value is None:
        return "unknown"
    if higher_is_worse:
        return "critical" if value >= crit else ("warning" if value >= warn else "ok")
    return "critical" if value <= crit else ("warning" if value <= warn else "ok")


def worst(statuses):
    s = sorted((x for x in statuses if x), key=lambda x: RANK.get(x, 9))
    return s[0] if s else ""


def short_version(v):
    """'NetApp Release 9.14.1P3: Mon Jan 01 2026' -> '9.14.1P3'."""
    return str(v or "?").replace("NetApp Release ", "").split(":")[0].strip() or "?"


def as_bool(v):
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    return str(v).strip().lower() in ("true", "yes", "1")


ALERT_SEV = {"fatal": "critical", "critical": "critical", "major": "critical", "minor": "warning", "degraded": "warning",
             "warning": "warning", "information": "info", "informational": "info", "other": "info", "unknown": "unknown"}
EMS_SEV = {"emergency": "critical", "alert": "critical", "error": "warning"}
BATTERY = {"battery_ok": "ok", "battery_fully_charged": "ok", "battery_partially_discharged": "warning",
           "battery_near_end_of_life": "warning", "battery_over_charged": "warning", "battery_unknown": "unknown",
           "battery_fully_discharged": "critical", "battery_at_end_of_life": "critical", "battery_not_present": "critical"}
SECTION_KEYS = {"nodes": ["nodes", "failover"], "health": ["subsystems"], "alerts": ["alerts"], "ems": ["ems"],
                "hardware": ["nodes", "shelves"], "disks": ["disks"], "lifs": ["lifs", "fc_lifs"], "ports": ["ports", "fc_ports"],
                "port_errors": ["ports", "nic_counters"], "aggregates": ["aggregates"], "volumes": ["volumes"],
                "snapmirror": ["snapmirror_cli", "snapmirror"], "peers": ["cluster_peers", "svm_peers"], "svms": ["svms"],
                "cifs": ["cifs", "cifs_domains"], "luns": ["luns"], "certificates": ["certificates"]}


class _Report(object):
    def __init__(self, opts):
        self.o = opts
        self.max = int(opts.get("max_rows") or 50)
        self.sections, self.problems = [], []

    def problem(self, cluster, area, obj, severity, text):
        if severity in ("critical", "warning"):
            self.problems.append({"cluster": cluster, "area": area, "object": obj, "severity": severity, "text": text})

    def table(self, title, about, columns, rows, none_text, notes=None, keep_order=False):
        """rows: [(cells, cell_statuses, row_status)]. Problems first (stable), capped at max_rows."""
        if not keep_order:
            rows = sorted(rows, key=lambda r: RANK.get(r[2], 9))
        more = len(rows) - self.max if len(rows) > self.max else 0
        shown = rows[:self.max]
        text = about
        if more:
            text += " The first %d of %d are shown; the job's artifacts (ontap_health) have all." % (self.max, len(rows))
        s = {"title": title}
        if shown:
            s.update({"columns": columns, "rows": [r[0] for r in shown],
                      "cell_status": [[x or "" for x in r[1]] for r in shown],
                      "row_status": [r[2] if r[2] in ("critical", "warning", "info", "unknown") else "" for r in shown]})
            s["text"] = text
        else:
            s["text"] = (about + " " + none_text).strip()
        st = worst([r[2] for r in rows if r[2] in ("critical", "warning")])
        if st:
            s["status"] = st
        if notes:
            s["lines"] = notes
        self.sections.append(s)


def _recs(c, key):
    return ((c.get("data") or {}).get(key) or {}).get("records") or []


def _notes(cs, keys):
    """What could not be read for a section, one line per cluster and query."""
    out = []
    for c in cs:
        if not c.get("reachable", True):
            continue
        for k in keys:
            d = (c.get("data") or {}).get(k) or {}
            if d.get("error"):
                out.append("%s: could not read %s - %s" % (c["_name"], k, d["error"]))
            elif d.get("reduced"):
                out.append("%s: %s - %s" % (c["_name"], k, d["reduced"]))
            if d.get("truncated"):
                out.append("%s: %s - only the first %d records were read" % (c["_name"], k, len(d.get("records") or [])))
    return out


def _row(cells, statuses=None, status=None):
    statuses = list(statuses or [""] * len(cells))
    statuses += [""] * (len(cells) - len(statuses))
    return (["" if x is None else x for x in cells], statuses, status if status is not None else worst(statuses))


def _pct_text(p):
    return "?" if p is None else ("%.0f%%" % p if p >= 10 else "%.1f%%" % p)


def ontap_report(collected, opts=None):
    """The ONTAP health report (roles/site_email layout) and the problems found:
    -> {'report': {...}, 'problems': [{cluster, area, object, severity, text}], 'clusters': [...]}"""
    o = opts or {}
    col = collected or {}
    R = _Report(o)
    now = parse_time(col.get("read_at")) or datetime.datetime.now(datetime.timezone.utc)
    cs = []
    for c in col.get("clusters") or []:
        c = dict(c)
        name = g((_recs(c, "cluster") or [{}])[0], "name") or c.get("address")
        c["_name"] = name
        cs.append(c)
    up = [c for c in cs if c.get("reachable", True)]

    # ---- 1. clusters ----------------------------------------------------------------------------
    rows, summaries = [], []
    for c in cs:
        if not c.get("reachable", True):
            rows.append(_row([c["address"], c["address"], "?", "UNREACHABLE", "", "", "", "", c.get("error", "")],
                             ["", "", "", "critical"]))
            R.problem(c["address"], "cluster", c["address"], "critical", "cannot be read: " + c.get("error", ""))
            summaries.append({"cluster": c["address"], "reachable": False, "error": c.get("error", "")})
            continue
        info = (_recs(c, "cluster") or [{}])[0]
        nodes = _recs(c, "nodes")
        n_up = sum(1 for n in nodes if n.get("state") == "up")
        subs = _recs(c, "subsystems")
        bad = [s for s in subs if str(s.get("health", "")).lower() not in ("ok", "")]
        health = ("DEGRADED" if bad else "OK") if subs else "?"
        aggrs = _recs(c, "aggregates")
        size = sum(g(a, "space.block_storage.size", 0) or 0 for a in aggrs)
        used = sum(g(a, "space.block_storage.used", 0) or 0 for a in aggrs)
        p = pct(used, size)
        ntp = [x.get("server") for x in _recs(c, "ntp") if x.get("server")]
        ntp_st = "" if ntp or (c["data"].get("ntp") or {}).get("error") else "warning"
        rows.append(_row([c["_name"], c["address"], short_version(g(info, "version.full")), health, "%d of %d" % (n_up, len(nodes)),
                          ("%s of %s (%s)" % (human_bytes(used), human_bytes(size), _pct_text(p))) if size else "?",
                          ", ".join(ntp) or "none", info.get("location") or "", ""],
                         ["", "", "", "warning" if bad else ("ok" if subs else ""), "critical" if n_up < len(nodes) else "ok",
                          level(p, float(o.get("aggr_warn_pct", 85)), float(o.get("aggr_crit_pct", 90))) if size else "",
                          ntp_st, ""]))
        if ntp_st:
            R.problem(c["_name"], "time", "NTP", "warning", "no NTP server configured")
        summaries.append({"cluster": c["_name"], "reachable": True, "version": g(info, "version.full", ""), "health": health,
                          "nodes_up": n_up, "nodes": len(nodes)})
    R.table("Clusters", "Each cluster: ONTAP version, system health, nodes up, space used on its aggregates, NTP servers.",
            ["Cluster", "Address", "ONTAP", "Health", "Nodes up", "Aggregate space used", "NTP servers", "Location", "Notes"],
            rows, "", keep_order=True)

    # ---- 2. nodes ----------------------------------------------------------------------------------
    rows = []
    for c in up:
        fo = {r.get("node"): r for r in _recs(c, "failover")}
        at = ((c.get("data") or {}).get("nodes") or {}).get("at")
        for n in _recs(c, "nodes"):
            st = n.get("state", "?")
            s_st = "ok" if st == "up" else "critical"
            f = fo.get(n.get("name"), {})
            poss = as_bool(f.get("possible")) if f else None
            tko = g(n, "ha.takeover.state")
            if poss is None and tko:
                poss = None if tko == "not_attempted" else (False if tko in ("not_possible", "failed") else None)
            poss_txt = "yes" if poss else ("no" if poss is False else (tko or "?"))
            spst = g(n, "service_processor.state")
            bat = g(n, "nvram.battery_state")
            drift = None
            t = parse_time(n.get("date"))
            if t and at:
                drift = (t - datetime.datetime.fromtimestamp(float(at), tz=datetime.timezone.utc)).total_seconds()
            d_st = level(abs(drift), float(o.get("drift_warn", 60)), float(o.get("drift_crit", 300))) if drift is not None else ""
            cells = [c["_name"], n.get("name"), st.upper(), uptime_text(n.get("uptime")) if st == "up" or n.get("uptime") else "-",
                     n.get("model", ""), g(n, "version.full", ""),
                     poss_txt + (" (%s)" % f.get("state_description") if f.get("state_description") and poss is False else ""),
                     spst or "?", (bat or "?").replace("battery_", "").replace("_", " "),
                     ("%+d s" % drift) if drift is not None else "?"]
            sts = ["", "", s_st, "", "", "", "ok" if poss else ("warning" if poss is False else ""),
                   "ok" if spst == "online" else ("warning" if spst else ""), BATTERY.get(bat, "") if bat else "", d_st]
            rows.append(_row(cells, sts))
            for label, sv, txt in (("state", s_st, "node is " + st), ("takeover", sts[6], "storage failover not possible"),
                                   ("service processor", sts[7], "service processor " + str(spst)),
                                   ("NVRAM battery", sts[8], "NVRAM battery " + str(bat)),
                                   ("time", d_st, "clock %+d s off the AAP server's" % (drift or 0))):
                R.problem(c["_name"], "node", n.get("name"), sv, txt)
    R.table("Nodes", "State (green up, red down), uptime (days hours:minutes), model, version, whether the partner could take "
                     "over, service processor, NVRAM battery, and the node's clock against the AAP server's.",
            ["Cluster", "Node", "State", "Uptime", "Model", "ONTAP", "Takeover possible", "Service processor", "NVRAM battery",
             "Clock vs AAP"], rows, "No node was read.", _notes(cs, SECTION_KEYS["nodes"]))

    # ---- 3. system health subsystems + 4. alerts ----------------------------------------------------
    rows = []
    for c in up:
        for s in _recs(c, "subsystems"):
            h = str(s.get("health", "")).lower()
            if h in ("ok", ""):
                continue
            stt = "warning" if h == "degraded" else "unknown"
            rows.append(_row([c["_name"], s.get("subsystem"), h.upper(), s.get("outstanding_alert_count", "")], ["", "", stt, ""]))
            R.problem(c["_name"], "health", s.get("subsystem"), stt, "subsystem " + h)
    R.table("System health", "ONTAP's health monitors by subsystem (system health subsystem show). Only the ones not OK.",
            ["Cluster", "Subsystem", "Health", "Open alerts"], rows, "Every subsystem is OK.", _notes(cs, SECTION_KEYS["health"]))
    rows = []
    for c in up:
        for a in _recs(c, "alerts"):
            sev = ALERT_SEV.get(str(a.get("perceived_severity", "")).lower(), "warning")
            rows.append(_row([c["_name"], a.get("node", ""), str(a.get("perceived_severity", "?")).upper(), a.get("subsystem") or a.get("monitor", ""),
                              a.get("alert_id", ""), a.get("alerting_resource", ""), time_text(a.get("indication_time")),
                              a.get("probable_cause", ""), a.get("corrective_actions", "")], ["", "", sev]))
            R.problem(c["_name"], "health alert", a.get("node", ""), sev, "%s: %s" % (a.get("alert_id", ""), a.get("probable_cause", "")))
    R.table("Health alerts", "What ONTAP's health monitors raised (system health alert show), with the probable cause and what to do.",
            ["Cluster", "Node", "Severity", "Subsystem", "Alert", "Resource", "Since (UTC)", "Probable cause", "Corrective action"],
            rows, "No health alert.", _notes(cs, SECTION_KEYS["alerts"]))

    # ---- 5. EMS error events -------------------------------------------------------------------------
    hours = float(o.get("ems_hours", 24))
    since = now - datetime.timedelta(hours=hours)
    groups = {}
    for c in up:
        for e in _recs(c, "ems"):
            t = parse_time(e.get("time"))
            sev = str(g(e, "message.severity", "")).lower()
            if (t and t < since) or sev not in EMS_SEV:
                continue
            k = (c["_name"], g(e, "node.name", ""), g(e, "message.name", ""))
            x = groups.setdefault(k, {"n": 0, "last": None, "msg": "", "sev": sev})
            x["n"] += 1
            if t and (x["last"] is None or t >= x["last"]):
                x["last"], x["msg"] = t, e.get("log_message", "")
            if RANK.get(EMS_SEV[sev], 9) < RANK.get(EMS_SEV[x["sev"]], 9):
                x["sev"] = sev
    rows = []
    for (cl, node, name), x in sorted(groups.items(), key=lambda kv: -kv[1]["n"]):
        sv = EMS_SEV[x["sev"]]
        rows.append(_row([cl, node, x["sev"].upper(), name, x["n"], x["last"].strftime("%Y-%m-%d %H:%M") if x["last"] else "?", x["msg"]],
                         ["", "", sv]))
        R.problem(cl, "event", node, sv, "%s x%d: %s" % (name, x["n"], x["msg"][:200]))
    R.table("Error events (last %s h)" % _hours(hours), "ONTAP's event log (EMS): emergency, alert and error events, the same "
            "event on the same node counted once, most frequent first.",
            ["Cluster", "Node", "Severity", "Event", "Count", "Last (UTC)", "Last message"], rows,
            "No such event.", _notes(cs, SECTION_KEYS["ems"]))

    # ---- 6. controllers and shelves --------------------------------------------------------------------
    rows = []
    for c in up:
        for n in _recs(c, "nodes"):
            frus = g(n, "controller.frus") or []
            bad = ["%s %s" % (f.get("type", ""), f.get("id", "")) for f in frus if str(f.get("state", "ok")) != "ok"]
            fp = g(n, "controller.failed_power_supply.count")
            ff = g(n, "controller.failed_fan.count")
            ot = g(n, "controller.over_temperature")
            probs = bad + (["%s failed power suppl%s" % (fp, "y" if fp == 1 else "ies")] if fp and not any(f.startswith("psu") for f in bad) else []) + \
                (["%s failed fan(s)" % ff] if ff and not any(f.startswith("fan") for f in bad) else []) + (["over temperature"] if ot == "over" else [])
            msgs = [m for m in (g(n, "controller.failed_power_supply.message.message"), g(n, "controller.failed_fan.message.message"))
                    if m and not any(str(m).startswith(b.split(" ", 1)[-1]) for b in bad)]
            st = "critical" if probs else ("ok" if frus or fp is not None else "")
            rows.append(_row([c["_name"], "controller " + str(n.get("name")), n.get("model", ""),
                              "ERROR" if probs else ("OK" if st else "?"),
                              "%d failed" % fp if fp else ("OK" if fp is not None else "?"),
                              "%d failed" % ff if ff else ("OK" if ff is not None else "?"),
                              "OVER" if ot == "over" else (ot or "?"),
                              "; ".join(probs + msgs) or ("%d FRUs OK" % len(frus) if frus else "")],
                             ["", "", "", st, "critical" if fp else ("ok" if fp is not None else ""),
                              "critical" if ff else ("ok" if ff is not None else ""), "critical" if ot == "over" else ""]))
            if probs:
                R.problem(c["_name"], "hardware", n.get("name"), "critical", "; ".join(probs + msgs))
        for s in _recs(c, "shelves"):
            frus = s.get("frus") or []
            psus = [f for f in frus if f.get("type") == "psu"]
            mods = [f for f in frus if f.get("type") == "module"]
            fans = s.get("fans") or []
            temps = s.get("temperature_sensors") or []

            def okn(items):
                bad_ = [i for i in items if str(i.get("state", "ok")) != "ok"]
                return bad_, ("%d of %d OK" % (len(items) - len(bad_), len(items))) if items else "?"
            bp, tp = okn(psus)
            bm, tm = okn(mods)
            bf, tf = okn(fans)
            bt, _ = okn(temps)
            hot = max([t.get("temperature") for t in temps if isinstance(t.get("temperature"), (int, float))] or [None]) if temps else None
            errs = [g(e, "reason.message") or str(e) for e in (s.get("errors") or [])]
            probs = (["PSU %s" % f.get("id") for f in bp] + ["module %s" % f.get("id") for f in bm] +
                     ["fan %s" % f.get("id") for f in bf] + ["temperature sensor %s" % t.get("id") for t in bt] + errs)
            st = "critical" if (s.get("state") == "error" or bp or bm or bf or bt) else ("warning" if errs else "ok")
            rows.append(_row([c["_name"], "shelf " + str(s.get("name") or s.get("id")), s.get("model", ""), str(s.get("state", "?")).upper(),
                              tp, tf, ("%s C max" % hot) if hot is not None else "?", ("problems: " + "; ".join(probs)) if probs else
                              ("modules " + tm if mods else "")],
                             ["", "", "", st, "critical" if bp else ("ok" if psus else ""), "critical" if bf else ("ok" if fans else ""),
                              "critical" if bt else ("ok" if temps else "")]))
            if probs or s.get("state") == "error":
                R.problem(c["_name"], "hardware", "shelf " + str(s.get("name") or s.get("id")), st, "; ".join(probs) or "shelf in error")
    R.table("Hardware: controllers and shelves", "Each controller and disk shelf: power supplies, fans, temperature and any failed "
            "part (FRU). Problems first.", ["Cluster", "Component", "Model", "State", "Power supplies", "Fans", "Temperature", "Details"],
            rows, "No controller or shelf was read.", _notes(cs, SECTION_KEYS["hardware"]))

    # ---- 7. disks + 8. spares --------------------------------------------------------------------------
    rows, spares = [], []
    for c in up:
        shelf_names = {s.get("uid"): s.get("name") or s.get("id") for s in _recs(c, "shelves")}
        per_node = {}
        for d in _recs(c, "disks"):
            node = g(d, "home_node.name") or g(d, "node.name") or "?"
            pn = per_node.setdefault(node, {"spare": 0, "shared": 0, "total": 0})
            pn["total"] += 1
            ct, st = d.get("container_type", ""), d.get("state", "")
            pn["spare"] += ct == "spare"
            pn["shared"] += ct == "shared"
            if st == "broken" or ct == "broken":
                sv = "critical"
            elif st in ("reconstructing", "copy", "pending", "maintenance", "unfail") or ct in ("maintenance", "unknown", "unsupported"):
                sv = "warning"
            elif ct == "unassigned":
                sv = "info"
            else:
                continue
            rows.append(_row([c["_name"], d.get("name"), st.upper() or "?", ct, node, shelf_names.get(g(d, "shelf.uid"), ""),
                              d.get("bay", ""), d.get("model", ""), d.get("serial_number", "")], ["", "", sv]))
            R.problem(c["_name"], "disk", d.get("name"), sv, "disk %s (%s)" % (st, ct))
        mn = int(o.get("min_spares", 1))
        for node, pn in sorted(per_node.items()):
            if pn["shared"]:
                sv, note = "info", "partitioned disks (ADP): spare partitions are not counted here - storage aggregate show-spare-disks"
            elif pn["spare"] == 0 and mn > 0:
                sv, note = "critical", "no spare disk: a failed disk cannot be rebuilt at once"
            elif pn["spare"] < mn:
                sv, note = "warning", "fewer spares than ontap_min_spares (%d)" % mn
            else:
                sv, note = "ok", ""
            spares.append(_row([c["_name"], node, pn["spare"], pn["shared"], pn["total"], note], ["", "", sv]))
            R.problem(c["_name"], "spares", node, sv, note)
    R.table("Disks: failed, rebuilding or unassigned", "Disks that are broken (red), rebuilding, copying or in maintenance "
            "(amber), or not assigned to a node (blue).", ["Cluster", "Disk", "State", "Container", "Node", "Shelf", "Bay", "Model",
                                                          "Serial"], rows, "No failed or rebuilding disk.", _notes(cs, SECTION_KEYS["disks"]))
    R.table("Spare disks", "Spare disks per node (by home node): ONTAP rebuilds a failed disk onto a spare.",
            ["Cluster", "Node", "Spares", "Partitioned (shared)", "Disks", "Note"], spares, "No disk was read.")

    # ---- 9. LIFs + 10. ports + 11. port errors ---------------------------------------------------------
    rows = []
    for c in up:
        for kind, key in (("IP", "lifs"), ("FC", "fc_lifs")):
            for l in _recs(c, key):
                en, st, home = as_bool(l.get("enabled")), l.get("state", ""), as_bool(g(l, "location.is_home"))
                if en is False:
                    sv, what = "info", "disabled"
                elif st and st != "up":
                    sv, what = "critical", "down"
                elif home is False:
                    sv, what = "warning", "not on its home port"
                else:
                    continue
                rows.append(_row([c["_name"], g(l, "svm.name", ""), l.get("name"), kind, st.upper() or "?",
                                  "%s:%s" % (g(l, "location.home_node.name", "?"), g(l, "location.home_port.name", "?")),
                                  "%s:%s" % (g(l, "location.node.name", "?"), g(l, "location.port.name", "?")),
                                  g(l, "ip.address", ""), ", ".join(l.get("services") or []) if kind == "IP" else ""],
                                 ["", "", "", "", "critical" if what == "down" else ("ok" if st == "up" else ""),
                                  "warning" if what.startswith("not on") else "", "warning" if what.startswith("not on") else ""], sv))
                R.problem(c["_name"], "network", "%s %s" % (g(l, "svm.name", ""), l.get("name")), sv, "LIF " + what)
    R.table("Network interfaces (LIFs): down, not at home, or disabled", "Data and management LIFs (IP and FC) that are down "
            "(red), not on their home port (amber), or disabled (blue).", ["Cluster", "SVM", "LIF", "Type", "State", "Home",
                                                                            "Now on", "Address", "Services"],
            rows, "Every LIF is up and at home.", _notes(cs, SECTION_KEYS["lifs"]))
    rows = []
    for c in up:
        for p in _recs(c, "ports"):
            if as_bool(p.get("enabled")) is False or p.get("state") == "up":
                continue
            used = bool(g(p, "broadcast_domain.name"))
            sv = "critical" if used else "info"
            rows.append(_row([c["_name"], g(p, "node.name", ""), p.get("name"), "Ethernet " + str(p.get("type", "")),
                              str(p.get("state", "?")).upper(), g(p, "broadcast_domain.name", "") or "none (not in use)", ""],
                             ["", "", "", "", sv]))
            R.problem(c["_name"], "network", "%s %s" % (g(p, "node.name", ""), p.get("name")), sv, "port " + str(p.get("state")))
        for p in _recs(c, "fc_ports"):
            st = p.get("state", "")
            if as_bool(p.get("enabled")) is False or st == "online":
                continue
            sv = "info" if st == "link_not_connected" else "critical"
            rows.append(_row([c["_name"], g(p, "node.name", ""), p.get("name"), "FC", st.upper().replace("_", " "), "",
                              "speed %s" % g(p, "speed.configured", "?")], ["", "", "", "", sv]))
            R.problem(c["_name"], "network", "%s %s" % (g(p, "node.name", ""), p.get("name")), sv, "FC port " + st)
    R.table("Ports down", "Enabled Ethernet and FC ports that are not up. Red: a port in a broadcast domain (meant to be used) or "
            "an FC port that lost its link. Blue: a port with no cable or not in use.",
            ["Cluster", "Node", "Port", "Type", "State", "Broadcast domain", "Notes"], rows, "Every enabled port is up.",
            _notes(cs, ["ports", "fc_ports"]))
    rows = []
    ew, ec = float(o.get("port_errors_warn", 1)), float(o.get("port_errors_crit", 10000))
    for c in up:
        crc = {}
        for r in _recs(c, "nic_counters"):
            cnt = {x.get("name"): x.get("value") for x in (r.get("counters") or []) if isinstance(x, dict)}
            props = {x.get("name"): x.get("value") for x in (r.get("properties") or []) if isinstance(x, dict)}
            node = props.get("node.name") or props.get("node") or ""
            port = props.get("name") or props.get("port") or ""
            if not (node and port):
                parts = str(r.get("id", "")).split(":")
                node, port = (parts[0], parts[-1]) if len(parts) >= 2 else ("", str(r.get("id", "")))
            crc[(node, port)] = cnt.get("receive_crc_errors")
        for p in _recs(c, "ports"):
            if p.get("type") not in (None, "physical"):
                continue
            rx = g(p, "statistics.device.receive_raw.errors")
            k = (g(p, "node.name", ""), p.get("name"))
            cr = crc.get(k)
            worst_n = max([x for x in (rx, cr) if isinstance(x, (int, float))] or [0])
            if not worst_n:
                continue
            sv = level(worst_n, ew, ec)
            rows.append(_row([c["_name"], k[0], k[1], rx if rx is not None else "?", cr if cr is not None else "?",
                              g(p, "statistics.device.receive_raw.discards", "?"), g(p, "statistics.device.transmit_raw.errors", "?"),
                              g(p, "statistics.device.link_down_count_raw", "?")],
                             ["", "", "", level(rx, ew, ec) if isinstance(rx, (int, float)) else "",
                              level(cr, ew, ec) if isinstance(cr, (int, float)) else ""], sv))
            R.problem(c["_name"], "network", "%s %s" % k, sv, "port errors: %s receive, %s CRC" % (rx, cr))
    notes = _notes(cs, ["ports"]) + ["%s: CRC counters need ONTAP 9.11 or later (%s)" % (c["_name"], (c["data"].get("nic_counters") or {}).get("error"))
                                     for c in up if (c["data"].get("nic_counters") or {}).get("error")]
    R.table("Port errors (CRC)", "Physical Ethernet ports with receive errors (bad frames, CRC errors among them) or CRC errors, "
            "counted since the node booted. Rising numbers mean a bad cable, SFP or switch port.",
            ["Cluster", "Node", "Port", "Receive errors", "CRC errors", "Receive discards", "Transmit errors", "Link down count"],
            rows, "No port has errors.", notes)

    # ---- 12. aggregates + 13. not home ---------------------------------------------------------------
    aw, ac = float(o.get("aggr_warn_pct", 85)), float(o.get("aggr_crit_pct", 90))
    rows, nothome = [], []
    for c in up:
        for a in sorted(_recs(c, "aggregates"), key=lambda a: -(pct(g(a, "space.block_storage.used"), g(a, "space.block_storage.size")) or 0)):
            size, used, av = g(a, "space.block_storage.size"), g(a, "space.block_storage.used"), g(a, "space.block_storage.available")
            p = pct(used, size)
            node, home = g(a, "node.name", ""), g(a, "home_node.name", "")
            stt = "ok" if a.get("state") == "online" else "critical"
            ps = level(p, aw, ac)
            away = bool(home and node and node != home)
            rows.append(_row([c["_name"], a.get("name"), node, home, str(a.get("state", "?")).upper(), human_bytes(size), human_bytes(used),
                              human_bytes(av), _pct_text(p), g(a, "block_storage.primary.raid_type", ""), g(a, "block_storage.primary.disk_count", "")],
                             ["", "", "warning" if away else "", "", stt, "", "", "", ps]))
            R.problem(c["_name"], "capacity", "aggregate " + str(a.get("name")), ps, "aggregate %s used" % _pct_text(p))
            R.problem(c["_name"], "aggregate", a.get("name"), "critical" if stt == "critical" else "", "aggregate " + str(a.get("state")))
            if away:
                nothome.append(_row([c["_name"], a.get("name"), node, home], ["", "", "warning", ""]))
                R.problem(c["_name"], "aggregate", a.get("name"), "warning", "on %s, home node %s" % (node, home))
    R.table("Aggregates", "Every data aggregate, fullest first: the node it is on, size, used, available and used %% (amber from "
            "%s%%, red from %s%%). Root aggregates are not listed (ONTAP's REST API hides them)." % (_hours(aw), _hours(ac)),
            ["Cluster", "Aggregate", "Node", "Home node", "State", "Size", "Used", "Available", "Used %", "RAID", "Disks"],
            rows, "No aggregate was read.", _notes(cs, SECTION_KEYS["aggregates"]), keep_order=True)
    R.table("Aggregates not on their home node", "After a takeover or a manual move an aggregate can stay on the partner node: "
            "give it back (storage failover giveback) when the node is healthy.", ["Cluster", "Aggregate", "Now on", "Home node"],
            nothome, "Every aggregate is on its home node.")

    # ---- 14. volumes + 15. inodes + 16. snapshots -------------------------------------------------------
    vw, vc = float(o.get("volume_warn_pct", 85)), float(o.get("volume_crit_pct", 90))
    iw, ic = float(o.get("inode_warn_pct", 85)), float(o.get("inode_crit_pct", 90))
    sw, sc = float(o.get("snap_warn_pct", 100)), float(o.get("snap_crit_pct", 150))
    vrows, irows, srows = [], [], []
    for c in up:
        for v in _recs(c, "volumes"):
            if as_bool(v.get("is_svm_root")):
                continue
            size, av, used = g(v, "space.size"), g(v, "space.available"), g(v, "space.used")
            p = g(v, "space.percent_used")
            p = float(p) if isinstance(p, (int, float)) else pct(used, size)
            state = v.get("state", "?")
            ps = level(p, vw, vc)
            sst = "ok" if state == "online" else "critical"
            aggr = ", ".join(x.get("name", "") for x in (v.get("aggregates") or []) if isinstance(x, dict))
            name = "%s / %s" % (g(v, "svm.name", ""), v.get("name"))
            if ps in ("warning", "critical") or sst == "critical":
                vrows.append(_row([c["_name"], g(v, "svm.name", ""), v.get("name"), aggr, state.upper(), v.get("type", ""), human_bytes(size),
                                   human_bytes(av), _pct_text(p)], ["", "", "", "", sst, "", "", "", ps if p is not None else ""]))
                R.problem(c["_name"], "capacity", "volume " + name, worst([ps, sst]),
                          ("volume %s used" % _pct_text(p)) if sst == "ok" else "volume " + state)
            fu, fm = g(v, "files.used"), g(v, "files.maximum")
            fp_ = pct(fu, fm)
            fs = level(fp_, iw, ic)
            if fs in ("warning", "critical"):
                irows.append(_row([c["_name"], g(v, "svm.name", ""), v.get("name"), fu, fm, _pct_text(fp_)], ["", "", "", "", "", fs]))
                R.problem(c["_name"], "inodes", "volume " + name, fs, "inodes %s used" % _pct_text(fp_))
            su = g(v, "space.snapshot.used")
            if su:
                rp = g(v, "space.snapshot.space_used_percent")
                rs = level(float(rp), sw, sc) if isinstance(rp, (int, float)) and (g(v, "space.snapshot.reserve_percent") or 0) > 0 else ""
                srows.append((su, _row([c["_name"], g(v, "svm.name", ""), v.get("name"), v.get("type", ""), human_bytes(size),
                                        v.get("snapshot_count", "?"), ("%s%%" % g(v, "space.snapshot.reserve_percent")) if g(v, "space.snapshot.reserve_percent") is not None else "?",
                                        human_bytes(su), ("%s%%" % rp) if rp is not None and rs else "-", _pct_text(pct(su, size))],
                                       ["", "", "", "", "", "", "", "", rs])))
                if rs in ("warning", "critical"):
                    R.problem(c["_name"], "snapshots", "volume " + name, rs, "snapshots use %s%% of their reserve" % rp)
    R.table("Volumes %s%% full or more, or not online" % _hours(vw), "Volumes at or above %s%% used (amber) or %s%% (red), and "
            "volumes offline or restricted (red), with the aggregate they are on." % (_hours(vw), _hours(vc)),
            ["Cluster", "SVM", "Volume", "Aggregate", "State", "Type", "Size", "Available", "Used %"],
            sorted(vrows, key=lambda r: -float(str(r[0][8]).rstrip("%") or 0) if str(r[0][8]).rstrip("%").replace(".", "").isdigit() else 0),
            "Every volume is online and below %s%%." % _hours(vw), _notes(cs, SECTION_KEYS["volumes"]))
    R.table("Inodes (files) %s%% used or more" % _hours(iw), "Volumes running out of inodes: when they are all used, no new file "
            "can be created, whatever the free space (volume modify -files).", ["Cluster", "SVM", "Volume", "Files used",
                                                                                "Files maximum", "Used %"],
            irows, "No volume is running out of inodes.")
    top = int(o.get("snapshot_top", 25))
    srows = [r for _, r in sorted(srows, key=lambda x: -x[0])]
    flagged = [r for r in srows if r[2] in ("warning", "critical")]
    srows = flagged + [r for r in srows if r not in flagged][:max(0, top - len(flagged))]
    R.table("Snapshot space", "The volumes whose snapshots hold the most space (top %d), and any whose snapshots use more than "
            "their reserve (amber from %s%% of the reserve: they spill into the volume's space)." % (top, _hours(sw)),
            ["Cluster", "SVM", "Volume", "Type", "Volume size", "Snapshots", "Reserve", "Snapshot used", "% of reserve", "% of volume"],
            srows, "No snapshot space used.", keep_order=True)

    # ---- 17. SnapMirror + 18. peers ---------------------------------------------------------------
    lw, lc = float(o.get("lag_warn_hours", 24)) * 3600, float(o.get("lag_crit_hours", 48)) * 3600
    rows, fine = [], 0
    for c in up:
        recs = _recs(c, "snapmirror_cli")
        cli = bool(recs)
        if not cli:
            recs = _recs(c, "snapmirror")
        for r in recs:
            if cli:
                src, dst, state, status = r.get("source_path"), r.get("destination_path"), r.get("state", ""), r.get("status", "")
                healthy, lag = as_bool(r.get("healthy")), duration_seconds(r.get("lag_time"))
                last, prog, upd = r.get("last_transfer_end_timestamp"), r.get("total_progress"), r.get("progress_last_updated")
                reason = r.get("unhealthy_reason") or r.get("last_transfer_error") or ""
                policy = r.get("policy", "")
            else:
                src, dst, state = g(r, "source.path"), g(r, "destination.path"), r.get("state", "")
                status = g(r, "transfer.state", "") or ""
                healthy, lag = as_bool(r.get("healthy")), duration_seconds(r.get("lag_time"))
                last, prog, upd = g(r, "transfer.end_time"), g(r, "transfer.bytes_transferred"), None
                reason = "; ".join(x.get("message", "") for x in (r.get("unhealthy_reason") or []) if isinstance(x, dict))
                policy = g(r, "policy.name", "")
            state_l, status_l = str(state).lower(), str(status).lower()
            transferring = "transferring" in status_l
            h_st = "ok" if healthy else ("critical" if healthy is False else "")
            l_st = level(lag, lw, lc) if lag is not None else ""
            m_st = ("warning" if state_l in ("broken_off", "broken-off", "paused", "quiesced", "uninitialized", "out_of_sync") else
                    ("ok" if state_l in ("snapmirrored", "in_sync") else ""))
            sv = worst([h_st, l_st, m_st]) or ("info" if transferring else "")
            if sv not in ("critical", "warning") and not transferring:
                fine += 1
                continue
            if sv in ("", "ok"):
                sv = "info"
            rows.append(_row([c["_name"], src, dst, str(state).replace("_", " "), str(status).replace("_", " "),
                              "yes" if healthy else ("NO" if healthy is False else "?"), duration_text(lag) if lag is not None else "?",
                              time_text(last), human_bytes(prog) if prog not in (None, "") else "", time_text(upd), policy, reason],
                             ["", "", "", m_st, "info" if transferring else "", h_st, l_st], sv))
            R.problem(c["_name"], "snapmirror", dst, sv, "; ".join(x for x in ("unhealthy" if healthy is False else "",
                                                                                ("lag " + duration_text(lag)) if l_st in ("warning", "critical") else "",
                                                                                state_l if m_st == "warning" else "", reason) if x))
    R.table("SnapMirror", "Relationships (read on each destination cluster) that are unhealthy (red), lagging (amber from %s h, "
            "red from %s h since the last good transfer), broken off, paused or uninitialized, and the transfers running now (blue): "
            "progress is the data moved so far." % (_hours(lw / 3600), _hours(lc / 3600)),
            ["Cluster", "Source", "Destination", "Mirror state", "Status", "Healthy", "Lag", "Last transfer (UTC)", "Progress",
             "Progress updated", "Policy", "Reason / last error"], rows,
            "No relationship needs attention.", _notes(cs, ["snapmirror_cli"] if any(_recs(c, "snapmirror_cli") for c in up) else SECTION_KEYS["snapmirror"])
            + (["%d healthy, up-to-date relationship(s) are not listed." % fine] if fine else []))
    rows = []
    for c in up:
        for p in _recs(c, "cluster_peers"):
            st, au = g(p, "status.state", ""), g(p, "authentication.state", "")
            sv = "ok" if st == "available" else ("warning" if st in ("partial", "pending") else "critical")
            av = "ok" if au in ("ok", "") else "warning"
            if sv == "ok" and av == "ok":
                continue
            rows.append(_row([c["_name"], "cluster peer", p.get("name"), st, au], ["", "", "", sv, av]))
            R.problem(c["_name"], "peer", p.get("name"), worst([sv, av]), "cluster peer %s, authentication %s" % (st, au))
        for p in _recs(c, "svm_peers"):
            st = p.get("state", "")
            if st == "peered":
                continue
            sv = "warning" if st in ("initiated", "pending", "initializing", "suspended") else "critical"
            rows.append(_row([c["_name"], "SVM peer", "%s -> %s:%s" % (g(p, "svm.name", ""), g(p, "peer.cluster.name", ""), g(p, "peer.svm.name", "")),
                              st, ""], ["", "", "", sv]))
            R.problem(c["_name"], "peer", g(p, "svm.name", ""), sv, "SVM peer " + st)
    R.table("Cluster and SVM peers", "The peer relationships SnapMirror needs: cluster peers not available, SVM peers not peered.",
            ["Cluster", "Kind", "Peer", "State", "Authentication"], rows, "Every peer is available.", _notes(cs, SECTION_KEYS["peers"]))

    # ---- 19. SVMs + 20. CIFS + 21. LUNs -------------------------------------------------------------
    rows = []
    for c in up:
        for s in _recs(c, "svms"):
            st, sub = s.get("state", ""), s.get("subtype", "")
            if st == "running":
                continue
            sv = "info" if sub == "dp_destination" and st == "stopped" else ("warning" if st in ("starting", "stopping", "initializing") else "critical")
            rows.append(_row([c["_name"], s.get("name"), st.upper(), sub, "a DR destination: stopped by design" if sv == "info" else ""], ["", "", sv]))
            R.problem(c["_name"], "svm", s.get("name"), sv, "SVM " + st)
    R.table("SVMs not running", "Storage VMs that are stopped or starting. A stopped DR destination (dp_destination) is normal (blue).",
            ["Cluster", "SVM", "State", "Subtype", "Note"], rows, "Every SVM is running.", _notes(cs, SECTION_KEYS["svms"]))
    rows = []
    for c in up:
        doms = {g(d, "svm.name"): d.get("discovered_servers") or [] for d in _recs(c, "cifs_domains")}
        for s in _recs(c, "cifs"):
            svm = g(s, "svm.name", "")
            en = as_bool(s.get("enabled"))
            dcs = [d for d in doms.get(svm, []) if str(d.get("server_type", "")).lower() in ("ms_dc", "")] or doms.get(svm, [])
            ok = [d for d in dcs if str(d.get("state", "")).lower() == "ok"]
            badd = ["%s (%s)" % (d.get("server_name") or d.get("server_ip"), d.get("state")) for d in dcs if d not in ok]
            dst = ("ok" if ok and not badd else ("critical" if dcs and not ok else ("warning" if badd else ""))) if svm in doms else ""
            est = "ok" if en else ("critical" if en is False else "")
            rows.append(_row([c["_name"], svm, s.get("name"), g(s, "ad_domain.fqdn", ""), "yes" if en else ("NO" if en is False else "?"),
                              ("%d of %d OK" % (len(ok), len(dcs))) if svm in doms else "?", "; ".join(badd)],
                             ["", "", "", "", est, dst]))
            R.problem(c["_name"], "cifs", svm, worst([est, dst]), "CIFS server %s; domain controllers %s" % ("enabled" if en else "DISABLED", "; ".join(badd) or "ok"))
    R.table("CIFS (SMB) servers", "Each SVM's CIFS server: enabled or not (red when stopped), and the domain controllers ONTAP "
            "discovered for it and whether it reaches them (cifs domains; ONTAP 9.10+).",
            ["Cluster", "SVM", "CIFS server", "Domain", "Enabled", "Domain controllers", "Not OK"], rows, "No CIFS server.",
            _notes(cs, SECTION_KEYS["cifs"]))
    rows = []
    for c in up:
        for l in _recs(c, "luns"):
            st, mp = g(l, "status.state", ""), as_bool(g(l, "status.mapped"))
            sv = "critical" if st and st != "online" else ("warning" if mp is False else "")
            if not sv:
                continue
            rows.append(_row([c["_name"], g(l, "svm.name", ""), l.get("name"), g(l, "location.volume.name", ""), st.upper(),
                              "yes" if mp else "NO", human_bytes(g(l, "space.size")), human_bytes(g(l, "space.used")), l.get("os_type", "")],
                             ["", "", "", "", "critical" if st != "online" else "ok", "warning" if mp is False else ""]))
            R.problem(c["_name"], "lun", l.get("name"), sv, "LUN %s%s" % (st, ", not mapped" if mp is False else ""))
    R.table("LUNs: unmapped or not online", "LUNs no host can see (not mapped to an igroup: amber - forgotten, or waiting to be "
            "deleted?) and LUNs not online (red).", ["Cluster", "SVM", "LUN", "Volume", "State", "Mapped", "Size", "Used", "OS type"],
            rows, "Every LUN is online and mapped.", _notes(cs, SECTION_KEYS["luns"]))

    # ---- 22. certificates --------------------------------------------------------------------------
    cw, cc = float(o.get("cert_warn_days", 60)), float(o.get("cert_crit_days", 14))
    rows = []
    for c in up:
        for x in _recs(c, "certificates"):
            t = parse_time(x.get("expiry_time"))
            if not t:
                continue
            days = (t - now).total_seconds() / 86400.0
            if days > cw:
                continue
            ca = str(x.get("type", "")).endswith("_ca")
            sv = level(days, cw, cc, higher_is_worse=False)
            if ca and sv == "critical" and days >= 0:
                sv = "warning"
            rows.append(_row([c["_name"], g(x, "svm.name") or x.get("scope", ""), x.get("name") or "", x.get("common_name", ""), x.get("type", ""),
                              time_text(x.get("expiry_time")), "EXPIRED" if days < 0 else int(days)], ["", "", "", "", "", "", sv]))
            R.problem(c["_name"], "certificate", x.get("common_name") or x.get("name"), sv,
                      "certificate %s" % ("EXPIRED" if days < 0 else "expires in %d day(s)" % int(days)))
    R.table("Certificates expiring", "Certificates that expire within %s days (amber) or %s days (red), or have expired. CA "
            "certificates are amber at most until they expire." % (_hours(cw), _hours(cc)),
            ["Cluster", "SVM / scope", "Name", "Common name", "Type", "Expires (UTC)", "Days left"], rows,
            "No certificate expires within %s days." % _hours(cw), _notes(cs, SECTION_KEYS["certificates"]))

    # ---- title, summary --------------------------------------------------------------------------------
    crit = [p for p in R.problems if p["severity"] == "critical"]
    warn = [p for p in R.problems if p["severity"] == "warning"]
    n_down = sum(1 for p in R.problems if p["area"] == "node" and p["text"].startswith("node is"))
    count = lambda area, sev=None: sum(1 for p in R.problems if p["area"] == area and (sev is None or p["severity"] == sev))
    if crit or warn:
        title = "NetApp ONTAP health report: %d critical, %d warning on %d cluster(s)" % (len(crit), len(warn), len(cs))
    else:
        title = "NetApp ONTAP health report: all %d cluster(s) healthy" % len(cs)
    status = "critical" if crit else ("warning" if warn else "ok")
    vers = ", ".join("%s (%s)" % (c["_name"], short_version(g((_recs(c, "cluster") or [{}])[0], "version.full"))) for c in up)
    summary = [{"label": "Clusters read", "value": "%d of %d" % (len(up), len(cs)), "status": "ok" if len(up) == len(cs) else "critical"},
               {"label": "Nodes down", "value": n_down, "status": "critical" if n_down else "ok"},
               {"label": "Health alerts", "value": count("health alert"), "status": worst([p["severity"] for p in R.problems if p["area"] == "health alert"]) or "ok"},
               {"label": "Hardware / disk problems", "value": count("hardware") + count("disk", "critical"),
                "status": "critical" if count("hardware") + count("disk", "critical") else "ok"},
               {"label": "Volumes %s%%+" % _hours(vw), "value": sum(1 for p in R.problems if p["area"] == "capacity" and p["object"].startswith("volume")),
                "status": worst([p["severity"] for p in R.problems if p["area"] == "capacity" and p["object"].startswith("volume")]) or "ok"},
               {"label": "Aggregates %s%%+" % _hours(aw), "value": sum(1 for p in R.problems if p["area"] == "capacity" and p["object"].startswith("aggregate")),
                "status": worst([p["severity"] for p in R.problems if p["area"] == "capacity" and p["object"].startswith("aggregate")]) or "ok"},
               {"label": "SnapMirror problems", "value": count("snapmirror"), "status": worst([p["severity"] for p in R.problems if p["area"] == "snapmirror"]) or "ok"},
               {"label": "Network down", "value": sum(1 for p in R.problems if p["area"] == "network" and p["severity"] == "critical"),
                "status": "critical" if any(p["area"] == "network" and p["severity"] == "critical" for p in R.problems) else "ok"},
               {"label": "Certificates expiring", "value": count("certificate"), "status": worst([p["severity"] for p in R.problems if p["area"] == "certificate"]) or "ok"}]
    report = {"title": title, "status": status,
              "subtitle": "Read %s UTC%s" % (now.strftime("%Y-%m-%d %H:%M"), (" - " + vers) if vers else ""),
              "summary": summary, "sections": R.sections}
    return {"report": report, "problems": R.problems, "clusters": summaries,
            "critical": len(crit), "warning": len(warn)}


def _hours(h):
    try:
        h = float(h)
    except (TypeError, ValueError):
        return str(h)
    return str(int(h)) if h == int(h) else str(h)


class FilterModule(object):
    def filters(self):
        return {"ontap_report": ontap_report, "ontap_uptime": uptime_text, "ontap_duration_seconds": duration_seconds}
