# -*- coding: utf-8 -*-
"""1.97.0: SolarWinds in the health checks, troubleshoot and the VMware alarms reports (docs/SOLARWINDS_REPORTS.md).

The sections, tiles and lines made from what roles/solarwinds_report/library/site_host_swis.py read (the report
database's SolarWinds snapshot, and Log Analyzer's syslog). Read only, and never a finding: the sections have their own
colours, the report's counts, status and failed hosts stay the job's own.

swr_health(read, findings_by_host, opts)    -> {sections (the email: one line per section), web_sections, lines}
swr_troubleshoot(read, opts)                -> the same, the sections first on the page
swr_alarms(read, alarms_read, opts)         -> {section, tile, given, alerts, lines} for the VMware alarms report
swr_alarm_evidence(swr, problems)           -> the SolarWinds lines of ACT's evidence
swr_act_report(doc, swr, problems)          -> the ACT analysis' report with its SolarWinds tile and section"""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import fnmatch
import re

SEV = {0: "info", 1: "warning", 2: "critical", 3: "critical", 4: "info"}          # Orion's alert severities
SEV_WORD = {"critical": "Critical", "warning": "Warning", "info": "Info"}
NODE_ST = {"Down": "critical", "Critical": "critical", "Warning": "warning", "Unknown": "info", "Up": "ok"}
RANK = {"critical": 0, "warning": 1, "low": 2, "info": 3, "ok": 4, "": 5}
SNAP = "From the SolarWinds snapshot of %s UTC (%s)."
NOT_READ = "What could not be read (the report is complete apart from this):"


def _worst(sts):
    sts = [s for s in sts if s]
    return min(sts, key=lambda s: RANK.get(s, 5)) if sts else ""


def _sev(a):
    try:
        return SEV.get(int(a.get("severity")), "info")
    except (TypeError, ValueError):
        return "info"


def _n(x):
    return "{:,}".format(int(x or 0))


def _p(n, one, many=None):
    return "%s %s" % (_n(n), one if int(n or 0) == 1 else (many or one + "s"))


def _dt(hm):
    try:
        return datetime.datetime.strptime(str(hm or "")[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _when(hm, ref):
    """'10:02' on the snapshot's day, else 'Nov 20 10:02'."""
    t = _dt(hm)
    if t is None:
        return ""
    r = _dt(ref)
    return t.strftime("%H:%M") if r is not None and t.date() == r.date() else t.strftime("%b %d %H:%M")


def _ago(hm, ref):
    t, r = _dt(hm), _dt(ref)
    if t is None or r is None:
        return ""
    m = max(0, int((r - t).total_seconds() // 60))
    return "under a minute ago" if m < 1 else "%d min ago" % m if m < 120 else "%d h ago" % (m // 60)


def _snap_text(r):
    """1.97.0 (review P1-5): the snapshot's date, time and age, always."""
    age = r.get("age_text") or ("%d min" % r["age_min"] if r.get("age_min") else "under a minute")
    return SNAP % (r.get("read_at") or "?", "%s old" % age)


def _short(h):
    return str(h or "").split(".")[0] if not re.match(r"^\d+(\.\d+){3}$", str(h or "")) else str(h)


def _pats(pats):
    """solarwinds_report_skip_hosts as a list: a list, or text with commas, spaces or lines (review P1-4: "lab*" is one
    pattern, never its characters)."""
    if isinstance(pats, str):
        pats = re.split(r"[,\s]+", pats)
    return [str(p).strip().lower() for p in pats or [] if str(p or "").strip()]


def _skipped(host, pats, fqdn=""):
    """A pattern with a dot is held against the full name only; one without, against the short name too."""
    for name in (host, fqdn):
        if name:
            h = str(name).lower().rstrip(".")
            for p in _pats(pats):
                if fnmatch.fnmatchcase(h, p) or ("." not in p and fnmatch.fnmatchcase(_short(h).lower(), p)):
                    return True
    return False


def _mini(cols, rows):
    return {"columns": cols, "kinds": ["wrap"] * len(cols), "rows": rows}


def _not_read(lines):
    """1.97.0 (review P2-6): what could not be read, one line each, in a section "Not read" - never counted."""
    return {"title": "Not read", "counted": False, "text": NOT_READ,
            "lines": ["SolarWinds: %s." % str(x).rstrip(".") for x in lines]}


def _not_ok(r, title, off):
    """The one line of a read that cannot be used (a "Not read" section), or None (no credential and solarwinds_report:
    auto: nothing). 1.97.0 (review P1-5): an old snapshot is said with its age, never shown as current."""
    if not r or r.get("skipped"):
        return None
    if not r.get("ok"):
        return _not_read([(r.get("why") or [""])[0] or off or "the SolarWinds snapshot could not be read"])
    return None


def _uncounted(secs):
    """Every SolarWinds section and tile: shown, never counted (review P1-3: the website's counts, the index, the ops
    look's headline and Start here are the job's own)."""
    for sec in secs:
        if isinstance(sec, dict):
            sec["counted"] = False
            for b in sec.get("boxes") or []:
                b["counted"] = False
    return secs


def _apps_down(apps):
    """(down, total, status, names): the SAM applications not up of a host."""
    total = len(apps or [])
    bad = [a for a in apps or [] if a.get("status") in ("Down", "Warning", "Critical")]
    st = "critical" if any(a.get("status") == "Down" for a in bad) else "warning" if bad else ""
    return len(bad), total, st, [a.get("app") or "" for a in bad]


def _vols_over(vols, warn, crit):
    out = []
    for v in vols or []:
        pct = v.get("used_pct")
        if pct is None or float(pct) < warn:
            continue
        out.append(dict(v, level="critical" if float(pct) >= crit else "warning"))
    return out


def _gb(b):
    try:
        return "%.1f GB" % (float(b) / 2 ** 30)
    except (TypeError, ValueError):
        return ""


def _pct(x):
    try:
        return "%d %%" % round(float(x))
    except (TypeError, ValueError):
        return ""


# ------------------------------------------------------------------------------------------------ 2. the health checks
def swr_health(read, findings_by_host=None, opts=None):
    """The health check's three sections (right under Findings), their tile and the email's lines."""
    r, o = read or {}, opts or {}
    fb = findings_by_host or {}
    missing = set(o.get("missing") or [])
    warn, crit = float(o.get("volume_pct") or 90), float(o.get("volume_critical_pct") or 95)
    pats = o.get("skip_hosts") or []
    off = _not_ok(r, "SolarWinds", o.get("explain_off"))
    if off or not r or r.get("skipped"):
        sec = [dict(off, under_findings=True)] if off else []
        return {"sections": sec, "web_sections": sec, "lines": off["lines"] if off else [], "sheets": []}
    ref = r.get("read_at") or ""
    sl = r.get("syslog")
    queried = set(str(x) for x in (sl or {}).get("queried") or []) if sl and "queried" in sl else None
    sl_ok = bool(sl and sl.get("ok"))
    rows, det, rs, cs = [], [], [], []
    alerts_read = r.get("alerts_read", True)
    alerts_n, alert_hosts, alert_sts = 0, 0, []
    disagree, absent, left_out, unsure = [], [], [], []
    apps_bad = vols_bad = 0
    for h in sorted(r.get("hosts") or [], key=lambda h: h["host"]):
        name, short = h["host"], _short(h["host"])
        fs = fb.get(name) or []
        sts = [str(f.get("severity") or "") for f in fs]
        ours_st = _worst([s for s in sts if s in ("critical", "warning")]) or ("low" if fs else "")
        ours = ("did not report" if name in missing else _p(len(fs), "finding") if fs else "green")
        node = h.get("node")
        if not node:
            if _skipped(name, pats, h.get("fqdn")):
                left_out.append(name)
                continue
            if h.get("uncertain"):                       # review 1970e D: unknown, never missing or 0 alerts
                unsure.append((name, h.get("unmatched_why") or ""))
                rows.append(["", short, "?", "?", "?", "?", "?", ours])
                det.append([])
                rs.append("info")
                cs.append(["", "", "info", "", "", "", "", ours_st if ours_st in ("critical", "warning") else ""])
                continue
            absent.append((name, h.get("unmatched_why") or ""))
            rows.append(["", short, "Missing", "-" if alerts_read else "?", "-", "-", "-", ours])
            det.append([])
            rs.append("warning")
            cs.append(["", "", "warning", "", "", "", "", ours_st if ours_st in ("critical", "warning") else ""])
            continue
        al = (h.get("alerts") or []) if alerts_read else []
        a_st = _worst([_sev(a) for a in al])
        if al:
            alerts_n += len(al)
            alert_hosts += 1
            alert_sts.append(a_st)
        down, total, ap_st, _names = _apps_down(h.get("apps"))
        apps_bad += down
        vo = _vols_over(h.get("volumes"), warn, crit)
        vols_bad += len(vo)
        nst = NODE_ST.get(node.get("status"), "info")
        sy = (sl.get("by_node") or {}).get(str(node["node_id"])) if sl_ok else None
        sy_n = (sy or {}).get("count", 0)
        # review P2-2: a node the capped read never asked about is "?", a count the cap may have cut is "N+" (0+ too)
        asked = queried is None or str(node["node_id"]) in queried
        sy_text = ("-" if not sl_ok else "?" if not asked else "%s+" % _n(sy_n) if sl.get("capped") else _n(sy_n))
        vol_text = ", ".join("%s %s" % (v["caption"], _pct(v["used_pct"])) for v in vo[:2]) + (" +%d" % (len(vo) - 2) if len(vo) > 2 else "")
        # review P2-6: SAM or the volumes not read by the snapshot (not absent): "?", never "0 of 0" or "-"
        apps_text = ("%d of %d" % (down, total)) if r.get("sam") else ("?" if r.get("sam") is None else "-")
        rows.append(["", short, node.get("status") or "Unknown", _n(len(al)) if alerts_read else "?", apps_text,
                     vol_text or ("?" if r.get("volumes") is None else "-"), sy_text, ours])
        cs.append(["", "", nst, a_st if al else "", ap_st, _worst([v["level"] for v in vo]), "warning" if sy_n else "",
                   ours_st if ours_st in ("critical", "warning") else ""])
        rs.append(_worst([nst if nst != "ok" else "", a_st if al else "", ap_st, _worst([v["level"] for v in vo]), "warning" if sy_n else "",
                          ours_st if ours_st in ("critical", "warning") else ""]))
        d = []
        if al:
            d.append(_mini(["Severity", "SolarWinds alert", "On", "Since (UTC)"],
                           [[SEV_WORD[_sev(a)], a.get("name") or a.get("message") or "", a.get("on") or short, _when(a.get("since"), ref)]
                            for a in al]))
        bad = [a for a in h.get("apps") or [] if a.get("status") in ("Down", "Warning", "Critical")]
        if bad:
            d.append(_mini(["SAM application", "Status", "Components not up"],
                           [[a.get("app") or "", a.get("status") or "",
                             ", ".join(c.get("name") or "" for c in a.get("components_not_up") or []) or "-"] for a in bad]))
        if vo:
            d.append(_mini(["Volume", "Used", "Free"],
                           [[v["caption"], _pct(v["used_pct"]),
                             _gb((v.get("size_bytes") or 0) - (v.get("used_bytes") or 0)) if v.get("size_bytes") else ""] for v in vo]))
        if sy and sy.get("top"):
            d.append(_mini(["Syslog errors (%d h)" % int(sl.get("hours") or 24), "Count", "Last (UTC)"],
                           [[t["message"], _n(t["count"]), _when(t.get("last"), ref)] for t in sy["top"]]))
        det.append(d)
        problem = al or node.get("status") in ("Down", "Critical", "Warning") or down
        if problem and not fs and name not in missing:
            first = al[0] if al else None
            text = (("%s, since %s." % (first.get("name") or first.get("message"), _when(first.get("since"), ref))) if first else
                    "Node %s in SolarWinds." % (node.get("status") or "").lower() if node.get("status") != "Up" else
                    "%s of its SAM applications not up." % _n(down))
            disagree.append({"status": a_st if al else (nst if nst in ("critical", "warning") else "warning"),
                             "label": SEV_WORD.get(a_st, "Warning") if al else node.get("status") or "Warning", "name": short,
                             "text": text + " Our checks found nothing. Worth a look."})
    n = len(rows)
    # worst first, then by name (as the findings)
    order = sorted(range(n), key=lambda i: (RANK.get(rs[i], 5), rows[i][1]))
    rows, det, rs, cs = [rows[i] for i in order], [det[i] for i in order], [rs[i] for i in order], [cs[i] for i in order]
    web = []
    if n:
        cap = "One row per host: open it for its alerts, applications, volumes and top syslog messages. " + _snap_text(r)
        says = {"title": "SolarWinds says (%s)" % _p(n, "host"), "status": _worst(rs) or "ok", "under_findings": True,
                "columns": ["", "Host", "Node", "Alerts", "Apps down", "Volumes over %d %%" % int(warn),
                            ("Syslog errors (%d h)" % int(sl.get("hours") or 24)) if sl else "Syslog errors", "Our checks"],
                "kinds": ["x", "", "", "", "", "", "", ""], "rows": rows, "details": det, "row_status": rs, "cell_status": cs,
                "text": cap}
        notes = []
        if left_out:
            notes.append("%s of solarwinds_report_skip_hosts left out." % _p(len(left_out), "host"))
        if sl and not sl_ok:
            notes.append("Syslog not read: %s." % str(sl.get("why") or "").rstrip("."))
        elif sl_ok and sl.get("capped"):
            notes.append("Syslog: only the newest %s entries were read (solarwinds_report_syslog_max_rows)." % _n(sl.get("max_rows")))
        if r.get("sam") is False:
            notes.append("No SAM on this SolarWinds: no applications.")
        if unsure:
            notes.append("The counts are of the matched hosts only (%s not certain)." % _p(len(unsure), "host"))
        if notes:
            says["note"] = " ".join(notes)
        if alerts_read and unsure:
            tile = {"value": "%s+" % _n(alerts_n), "status": _worst(alert_sts + ["info"]),
                    "note": "%s not certain" % _p(len(unsure), "host")}
        elif alerts_read:
            tile = {"value": alerts_n, "status": _worst(alert_sts) if alerts_n else "ok",
                    "note": ("on %s" % _p(alert_hosts, "host")) if alerts_n else "none on these hosts"}
        else:
            tile = {"value": "?", "note": "not read", "status": "info"}
            says["status"] = _worst(rs + ["info"])
        says["boxes"] = [dict(tile, label="SolarWinds alerts")]
        web.append(says)
    if disagree:
        web.append({"title": "SolarWinds alerts, our checks are green (%d)" % len(disagree), "status": _worst(d["status"] for d in disagree),
                    "under_findings": True, "glance": disagree,
                    "text": "The two disagree. Look at the host, or at the alert in SolarWinds."})
    if unsure:
        web.append({"title": "Match not certain (%d)" % len(unsure), "status": "info", "under_findings": True,
                    "lines": ["%s: %s" % (a, w) if w else a for a, w in unsure],
                    "text": "The snapshot could not tell which SolarWinds node these hosts are. Their alerts, applications and volumes "
                            "are not counted."})
    if absent:
        web.append({"title": "Not in SolarWinds (%d)" % len(absent), "status": "warning", "under_findings": True,
                    "lines": ["%s: %s" % (a, w) if w else a for a, w in absent],
                    "text": "AAP checks these hosts, but SolarWinds does not monitor them. Add them in SolarWinds, or list them in "
                            "solarwinds_report_skip_hosts."})
    lines = []
    if n:
        bits = ["alerts not read"]
        if alerts_read:
            bits = [_p(alerts_n, "alert") + (" on %s" % _p(alert_hosts, "host") if alerts_n else "")]
            if unsure:
                bits = ["%s+ alerts (%s not certain)" % (_n(alerts_n), _p(len(unsure), "host"))]
        # review 1970f: the uncertain hosts' applications and volumes are not counted either: a floor, "N+"
        if r.get("sam"):
            bits.append(("%s+ applications" % _n(apps_bad) if unsure else _p(apps_bad, "application")) + " not up")
        if r.get("volumes") is None:
            bits.append("volumes not read")
        elif r["volumes"] is True:
            bits.append(("%s+ volumes" % _n(vols_bad) if unsure else _p(vols_bad, "volume")) + " over %d %%" % int(warn))
        lines.append("SolarWinds says (%s): %s." % (_p(n, "host"), ", ".join(bits)))
    if disagree:
        lines.append("SolarWinds alerts, our checks are green: %s." % ", ".join(d["name"] for d in disagree))
    if unsure:
        lines.append("Match not certain: %s." % ", ".join(_short(a) for a, _w in unsure[:10]) + (" +%d" % (len(unsure) - 10) if len(unsure) > 10 else ""))
    if absent:
        lines.append("Not in SolarWinds: %s." % ", ".join(_short(a) for a, _w in absent[:10]) + (" +%d" % (len(absent) - 10) if len(absent) > 10 else ""))
    email = []
    if lines:
        email.append({"title": "SolarWinds", "status": _worst([s.get("status") for s in web]), "under_findings": True,
                      "lines": lines, "text": _snap_text(r) + " The report website has the rows.",
                      "boxes": web[0]["boxes"] if n else []})
    if r.get("not_read"):                                # review P2-6: a part the snapshot could not read, said
        nr = dict(_not_read(r["not_read"]), under_findings=True)
        web.append(nr)
        email.append(dict(nr))
    return {"sections": _uncounted(email), "web_sections": _uncounted(web), "lines": lines, "sheets": sheets_of(web)}


def sheets_of(secs):
    """review P2-5: the SolarWinds tables as the workbook's sheets (report_email_xlsx): a sheet per table, the opening
    column left out; a row's details (its alerts, applications) are on the website."""
    out = []
    for sec in secs or []:
        cols = list(sec.get("columns") or [])
        if not cols:
            continue
        keep = [i for i, c in enumerate(cols) if not (i == 0 and (sec.get("kinds") or [""])[0] == "x")]
        out.append({"name": re.sub(r"[\[\]:*?/\\]", "", sec.get("sheet") or sec.get("title") or "SolarWinds")[:31],
                    "columns": [cols[i] for i in keep], "rows": [[row[i] for i in keep] for row in sec.get("rows") or []]})
    return out


# -------------------------------------------------------------------------------------------------------- 3. troubleshoot
def _port_text(p):
    sp = _speed(p.get("speed"))
    port = "%s %s" % (p.get("switch") or "?", p.get("port") or "?")
    details = []
    if not p.get("status"):
        details.append("status not read")
    elif p["status"] != "Up":
        details.append("the port is %s" % p["status"].lower())
    if p.get("errors") is None:
        details.append("errors not read")
    elif p["errors"]:
        details.append("%s this hour" % _p(p["errors"], "error"))
    elif p.get("discards"):
        details.append("%s this hour" % _p(p["discards"], "discard"))
    elif not details:
        details.append("no errors this hour")
    return port + (", " + sp if sp else "") + ": " + ", ".join(details) + "."


def _speed(bps):
    try:
        b = float(bps)
    except (TypeError, ValueError):
        return ""
    if b <= 0:
        return ""
    for div, unit in ((1e9, "Gb/s"), (1e6, "Mb/s"), (1e3, "kb/s")):
        if b >= div:
            v = b / div
            return ("%d %s" % (v, unit)) if v == int(v) else ("%.1f %s" % (v, unit))
    return "%d b/s" % b


def _port_st(p):
    if p.get("status") in ("Down",):
        return "critical"
    if p.get("status") in ("Warning", "Unknown") or p.get("errors") or p.get("discards"):
        return "warning"
    return "info" if not p.get("status") or p.get("errors") is None else "ok"


EV_WARN = ("down", "alert triggered", "not responding", "failed", "critical", "warning")


def _ev_st(e):
    k = str(e.get("kind") or "").lower()
    return "warning" if any(w in k for w in EV_WARN) and "reset" not in k and "cleared" not in k else ""


def swr_troubleshoot(read, opts=None):
    """troubleshoot's three sections, first on the page (above Start here)."""
    r, o = read or {}, opts or {}
    off = _not_ok(r, "What SolarWinds sees", o.get("explain_off"))
    if off or not r or r.get("skipped"):
        sec = [dict(off, first=True, top=True)] if off else []
        return {"sections": sec, "web_sections": sec, "lines": off["lines"] if off else [], "sheets": []}
    pats = o.get("skip_hosts") or []
    ref = r.get("read_at") or ""
    shown = int(o.get("events_shown") or 20)
    hours = int(o.get("event_hours") or 24)
    hosts = r.get("hosts") or []
    alerts_read = r.get("alerts_read", True)
    ports_read = r.get("ports_read", True)
    events_read = r.get("events")
    port_ev = r.get("port_events_read", True)          # review 1970e E: no topology, no port events: the count is a floor
    multi = len(hosts) > 1
    web, lines = [], []
    for h in hosts:
        short = _short(h["host"])
        sfx = (": " + short) if multi else ""
        node = h.get("node")
        if not node and _skipped(h["host"], pats, h.get("fqdn")):    # review P1-4: never in SolarWinds on purpose - no warning
            web.append({"title": "What SolarWinds sees" + sfx, "status": "info", "first": True, "top": True,
                        "glance": [{"status": "info", "label": "Left out", "name": short,
                                    "text": "In solarwinds_report_skip_hosts: not in SolarWinds on purpose."}]})
            lines.append("%s: left out (solarwinds_report_skip_hosts)." % short)
            continue
        if not node and h.get("uncertain"):              # review 1970e D: unknown, never "not in SolarWinds"
            web.append({"title": "What SolarWinds sees" + sfx, "status": "info", "first": True, "top": True,
                        "glance": [{"status": "info", "label": "Not certain", "name": short,
                                    "text": (h.get("unmatched_why") or "match not certain").capitalize() + ". " + _snap_text(r)}]})
            lines.append("%s: match not certain." % short)
            continue
        if not node:
            web.append({"title": "What SolarWinds sees" + sfx, "status": "warning", "first": True, "top": True,
                        "glance": [{"status": "warning", "label": "Missing", "name": short,
                                    "text": (h.get("unmatched_why") or "SolarWinds does not monitor this host") + ". " + _snap_text(r)}]})
            lines.append("%s: not in SolarWinds." % short)
            continue
        nst = NODE_ST.get(node.get("status"), "info")
        what = ", ".join(x for x in (node.get("ip"), " ".join(y for y in (node.get("vendor"), node.get("model")) if y), node.get("os_version")) if x)
        g = [{"status": nst, "label": node.get("status") or "Unknown", "name": short,
              "text": ("Last poll not read." if not r.get("poll_read", True) else
                       "Polled %s UTC (%s)." % (_when(node.get("last_poll"), ref), _ago(node.get("last_poll"), ref)) if node.get("last_poll")
                       else "Not polled yet.") + ((" " + what + ".") if what else "")}]
        al = (h.get("alerts") or []) if alerts_read else []
        if alerts_read:
            g.append({"status": _worst([_sev(a) for a in al]) if al else "ok",
                      "label": _p(len(al), "alert") if al else "None", "name": "Active alerts",
                      "text": " ".join("%s (%s)." % (a.get("name") or a.get("message"), _when(a.get("since"), ref)) for a in al[:2])
                      + (" %d more." % (len(al) - 2) if len(al) > 2 else "") if al else "No active alert in SolarWinds."})
        else:
            g.append({"status": "info", "label": "Not read", "name": "Active alerts",
                      "text": "The active alerts were not read by the last snapshot."})
        ports = (h.get("ports") or []) if ports_read else []
        if ports:
            p_st = _worst([_port_st(p) for p in ports])
            label = ports[0].get("status") if all(p.get("status") for p in ports) else "Not read"
            g.append({"status": p_st, "label": "Errors" if any(p.get("errors") for p in ports) else label,
                      "name": "Its switch port" if len(ports) == 1 else "Its switch ports", "text": " ".join(_port_text(p) for p in ports[:2])})
        elif not ports_read:
            g.append({"status": "info", "label": "Not read", "name": "Its switch port",
                      "text": "SolarWinds' topology was not read by the last snapshot."})
        else:
            g.append({"status": "info", "label": "None", "name": "Its switch port", "text": "SolarWinds' topology links no switch port to this host."})
        ev = (h.get("events") or []) if events_read else []
        kinds = {}
        for e in ev:
            kinds[e.get("kind") or "Event"] = kinds.get(e.get("kind") or "Event", 0) + 1
        top = sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
        g.append({"status": "info" if ev or not events_read or not port_ev else "ok",
                  "label": (_n(len(ev)) + ("" if port_ev else "+")) if events_read else "Not read",
                  "name": "Events (%d h)" % hours,
                  "text": ((", ".join("%s %s" % (_n(c), k) for k, c in top) + "." if ev else
                            "No event in the last %d h." % hours if port_ev else "No node event in the last %d h." % hours)
                           + ("" if port_ev else " Switch port events not read."))
                  if events_read else "Not read."})
        web.append({"title": "What SolarWinds sees" + sfx, "status": _worst([x["status"] for x in g if x["status"] != "ok"]) or "ok",
                    "first": True, "top": True, "glance": g, "text": _snap_text(r)})
        if ports:
            web.append({"title": ("Its switch port" if len(ports) == 1 else "Its switch ports") + sfx, "first": True, "top": True,
                        "status": _worst([_port_st(p) for p in ports if _port_st(p) != "ok"]) or "ok",
                        "columns": ["Switch", "Port", "Status", "Speed", "In %", "Out %", "Errors (1 h)", "Discards (1 h)"],
                        "rows": [[p.get("switch") or "", p.get("port") or "", p.get("status") or "?", _speed(p.get("speed")) or "-",
                                  _pct(p.get("in_pct")).replace(" %", "") or "-", _pct(p.get("out_pct")).replace(" %", "") or "-",
                                  "?" if p.get("errors") is None else _n(p["errors"]), "-" if p.get("discards") is None else _n(p["discards"])]
                                 for p in ports],
                        "row_status": [_port_st(p) if _port_st(p) != "ok" else "" for p in ports],
                        "cell_status": [["", "", NODE_ST.get(p.get("status"), ""), "", "", "", "warning" if p.get("errors") else "",
                                         "warning" if p.get("discards") else ""] for p in ports],
                        "text": "The port SolarWinds' topology links to this host. Errors and discards are in and out together."})
        if ev:
            def evrow(e):
                return [_when(e.get("time"), ref), e.get("kind") or "", e.get("message") or "",
                        short if e.get("on") == "node" else (e.get("switch") or "switch")]      # review P2-4: its own switch
            web.append({"title": "Its events, last %d h (%s)" % (hours, _n(len(ev))) + sfx, "first": True, "top": True,
                        "columns": ["Time (UTC)", "Kind", "Event", "On"], "rows": [evrow(e) for e in ev[:shown]],
                        "row_status": [_ev_st(e) for e in ev[:shown]],
                        "text": "Newest first. The switch's events are its port's only."})
            if len(ev) > shown:
                web.append({"title": "Older events (%s)" % _n(len(ev) - shown) + sfx, "first": True, "top": True, "collapsed": True,
                            "columns": ["Time (UTC)", "Kind", "Event", "On"], "rows": [evrow(e) for e in ev[shown:]],
                            "row_status": [_ev_st(e) for e in ev[shown:]]})
        lines.append("%s: node %s, %s, %s, %s." % (
            short, (node.get("status") or "unknown").lower(), _p(len(al), "alert") if alerts_read else "alerts not read",
            ("port " + _port_text(ports[0]).rstrip(".")) if ports else ("no switch port known" if ports_read else "topology not read"),
            ("%s in %d h" % (_p(len(ev), "event"), hours) if port_ev else
             "%s+ events in %d h (switch port events not read)" % (_n(len(ev)), hours)) if events_read else "events not read"))
    email = [{"title": "What SolarWinds sees", "first": True, "top": True, "status": _worst([s.get("status") for s in web if s.get("status")]),
              "lines": lines, "text": _snap_text(r) + " The report website has the tables."}] if lines else []
    if r.get("not_read"):
        web.append(_not_read(r["not_read"]))
        email.append(_not_read(r["not_read"]))
    return {"sections": _uncounted(email), "web_sections": _uncounted(web), "lines": lines, "sheets": sheets_of(web)}


# ------------------------------------------------------------------------------------------- 4. the VMware alarms reports
TYPE = {"host": "Host", "vm": "VM"}


def _key(vc, kind, name):
    return (str(vc or "").strip().lower(), str(kind or "host").lower(), str(name or "").strip().lower().rstrip("."))


def _center(row, data):
    default = data.get("vcenter") or ""
    return row.get("vcenter") or (default if "," not in default else "")


def swr_alarms(read, alarms_read=None, opts=None):
    """The alarms report's section "SolarWinds alerts on the same hosts (N)" and its tile; alerts: the rows ACT reads."""
    r, d, o = read or {}, alarms_read or {}, opts or {}
    off = _not_ok(r, "SolarWinds alerts on the same hosts", o.get("explain_off"))
    if r.get("ok") and r.get("alerts_read") is False:
        off = _not_read(r["not_read"])
    if off or not r or r.get("skipped"):
        return {"section": off, "email_section": off, "tile": None, "alerts": [], "lines": off["lines"] if off else [],
                "ok": False, "sheets": []}
    ref = r.get("read_at") or ""
    vc_alarm = {}
    for a in d.get("alarms") or []:
        if a.get("entity_type") in ("host", "vm") and a.get("entity"):
            vc_alarm.setdefault(_key(_center(a, d), a["entity_type"], a["entity"]), []).append(a)
    notconn = dict((_key(_center(h, d), "host", h.get("name")), h) for h in d.get("hosts") or [] if h.get("connection") not in ("connected", ""))
    centers = {}
    for row, kind, name in _alarm_objects(d, include_event_hosts=True):
        vc, kind, name = _key(_center(row, d), kind, name)
        by_name = centers.setdefault(kind, {})
        if name in by_name and by_name[name] != vc:
            by_name[name] = None
        else:
            by_name[name] = vc
    owners = {}
    for h in r.get("hosts") or []:
        if h.get("node"):
            k = _key(h.get("vcenter"), h.get("kind"), h["host"])
            owners.setdefault(h["node"]["node_id"], set()).add(k)
    out = []
    for h in r.get("hosts") or []:
        node = h.get("node")
        if not node:
            continue
        kind = h.get("kind") or "host"
        vc, _, name = _key(h.get("vcenter"), kind, h["host"])
        if not vc:
            vc = centers.get(kind, {}).get(name, "")
        if vc is None or len(owners[node["node_id"]]) > 1:
            continue
        k = _key(vc, kind, name)
        vim = (r.get("vim") or {}).get("%s|%s" % (kind, h["host"])) or (r.get("vim") or {}).get(h["host"]) or {}
        for a in h.get("alerts") or []:
            both = vc_alarm.get(k) or []
            nc = notconn.get(k) if kind == "host" else None
            too = (both[0].get("alarm") if both else ("%s (Host connection)" % str(nc.get("connection") or "").replace("notResponding", "Not responding")
                                                       if nc else "no"))
            out.append({"severity": _sev(a), "type": kind, "key": list(k), "object": h["host"], "alert": a.get("name") or a.get("message") or "",
                        "since": a.get("since") or "", "vim": vim.get("status") or "", "vcenter": too, "message": a.get("message") or "",
                        "node_status": node.get("status") or "", "ports": h.get("ports") or [], "events": h.get("events") or []})
    out.sort(key=lambda x: (RANK.get(x["severity"], 5), x["object"], x["alert"]))
    match = sum(1 for x in out if x["vcenter"] != "no")
    title = "SolarWinds alerts on the same hosts (%d)" % len(out)
    if out:
        sec = {"title": title, "status": _worst([x["severity"] for x in out]),
               "columns": ["Severity", "Object", "SolarWinds alert", "Since (UTC)", "VIM status", "In vCenter too"],
               "rows": [[x["severity"].upper(), "%s %s" % (TYPE.get(x["type"], "Host"), x["object"]), x["alert"], (x["since"] or "")[:16],
                         x["vim"] or "-", x["vcenter"]] for x in out],
               "row_status": [x["severity"] if x["severity"] in ("critical", "warning") else "" for x in out],
               "cell_status": [["", "", "", "", NODE_ST.get(x["vim"], "") if x["vim"] not in ("Up", "") else "", ""] for x in out],
               "text": "SolarWinds' active alerts on this vCenter's hosts and VMs. %s" % _snap_text(r), "sheet": "SolarWinds alerts (%d)" % len(out)}
    else:
        sec = {"title": title, "status": "ok",
               "text": "SolarWinds has no active alert on this vCenter's hosts and VMs. %s" % _snap_text(r)}
    tile = {"label": "SolarWinds alerts", "value": len(out), "status": _worst([x["severity"] for x in out]) or "ok",
            "note": ("%d match a vCenter alarm" % match) if out else "none on these objects", "counted": False}
    lines = ["SolarWinds alerts on the same hosts: %d (%d match a vCenter alarm). %s" % (len(out), match, _snap_text(r))]
    short = {"title": title, "status": sec["status"], "lines": lines[:1], "text": "The report website has the rows."} if out else dict(sec)
    nr = _not_read(r["not_read"]) if r.get("not_read") else None
    _uncounted([sec, short] + ([nr] if nr else []))
    return {"section": sec, "email_section": short, "not_read": nr, "tile": tile, "alerts": out, "lines": lines, "ok": True,
            "centers": centers, "read_at": ref, "age_text": r.get("age_text") or "", "age_min": r.get("age_min"), "sheets": sheets_of([sec])}


def _pkey(p, centers):
    vc, kind, name = _key(p.get("vcenter"), p.get("object_type"), p.get("object"))
    if not vc:
        vc = centers.get(kind, {}).get(name, "")
    return (vc, kind, name)


def swr_alarm_evidence(swr, problems=None, max_chars=8000):
    """The SolarWinds lines of ACT's evidence: the alerts on the problems' own objects (kind and full name) and their
    switch ports' events. Empty when SolarWinds was not read, or no alert is on these objects."""
    s = swr or {}
    if not s.get("ok"):
        return ""
    objs = set(_pkey(p, s.get("centers") or {}) for p in problems or [])
    al = [a for a in s.get("alerts") or [] if tuple(a.get("key") or ()) in objs]
    if not al:
        return ""
    nl = "\n"
    lines = ["", "SOLARWINDS (read only, its snapshot of %s UTC, %s old). Each line is SolarWinds', not vCenter's." % (
        s.get("read_at") or "?", s.get("age_text") or "?")]
    for a in al:
        lines.append("SolarWinds: %s %s alert [%s] %s, since %s UTC. Node %s, VIM status %s.%s" % (
            TYPE.get(a["type"], "Host"), a["object"], a["severity"].upper(), a["alert"], a["since"] or "?", a["node_status"] or "?",
            a["vim"] or "not in VIM", (" Message: " + a["message"]) if a.get("message") and a["message"] != a["alert"] else ""))
    seen = set()
    for a in al:
        if tuple(a["key"]) in seen:
            continue
        seen.add(tuple(a["key"]))
        for p in a.get("ports") or []:
            lines.append("SolarWinds: %s is linked to %s %s (%s, %s)." % (
                a["object"], p.get("switch") or "?", p.get("port") or "?", (p.get("status") or "status not read").lower(),
                "errors not read" if p.get("errors") is None else "%s errors this hour" % _n(p["errors"])))
        for e in [e for e in a.get("events") or [] if e.get("on") == "port"][:10]:
            lines.append("SolarWinds: %s UTC %s port event on %s: %s" % (e.get("time"), a["object"], e.get("switch") or "its switch",
                                                                        e.get("message") or e.get("kind")))
    text = nl.join(lines)
    return text if len(text) <= max_chars else text[:max_chars] + nl + "[... SolarWinds evidence cut at %d characters ...]" % max_chars


def swr_act_report(doc, swr=None, problems=None, email=False):
    """The ACT analysis' report with SolarWinds: a tile (how many problems have a SolarWinds alert on their own object)
    and, folded at the end, what ACT was given from SolarWinds. email (review P2-5): one line instead of the table."""
    out = dict(doc or {})
    s = swr or {}
    if not s.get("ok"):
        if s.get("section"):
            out["sections"] = list(out.get("sections") or []) + [dict(s["section"])]
        return out
    problem_keys = [_pkey(p, s.get("centers") or {}) for p in problems or []]
    objs = set(problem_keys)
    al = [a for a in s.get("alerts") or [] if tuple(a.get("key") or ()) in objs]
    alerted = {tuple(a["key"]) for a in al}
    n = len(alerted)
    probs = sum(1 for k in problem_keys if k in alerted)
    out["summary"] = list(out.get("summary") or []) + [{"label": "With SolarWinds", "value": probs, "status": "info" if probs else "ok",
                                                        "note": "alerts on the same objects" if probs else "no alert on these objects",
                                                        "counted": False}]
    title = "What ACT was given from SolarWinds (%s)" % _p(len(al), "alert")
    given = {"title": title, "collapsed": True, "counted": False,
             "text": "The alerts on the same hosts and VMs, and their switch ports' events. %s ACT reads them next to vCenter's "
                     "alarms." % _snap_text(s)}
    if al and not email:
        given.update(columns=["Object", "SolarWinds alert", "Since (UTC)", "VIM status"],
                     rows=[["%s %s" % (TYPE.get(a["type"], "Host"), a["object"]), a["alert"], (a["since"] or "")[:16], a["vim"] or "-"]
                           for a in al])
    elif al:
        given["lines"] = ["%s on %s. The report website has the rows." % (_p(len(al), "SolarWinds alert"), _p(n, "object"))]
    else:
        given["lines"] = ["No SolarWinds alert on these objects."]
    out["sections"] = list(out.get("sections") or []) + [given]
    return out


def _alarm_objects(d, include_event_hosts=False):
    for h in d.get("hosts") or []:
        if h.get("name"):
            yield h, "host", h["name"]
    for a in d.get("alarms") or []:
        if a.get("entity_type") in ("host", "vm") and a.get("entity"):
            yield a, a["entity_type"], a["entity"]
    for e in d.get("events") or []:
        if include_event_hosts and e.get("host"):
            yield e, "host", e["host"]
        if e.get("vm") and (include_event_hosts or e.get("severity") in ("critical", "warning")):
            yield e, "vm", e["vm"]


def swr_vcenter_objects(alarms_read):
    """Each host and each VM named by an alarm or a warning or critical event, once per vCenter."""
    d = alarms_read or {}
    out, seen = [], set()
    for row, kind, name in _alarm_objects(d):
        vc = _center(row, d)
        key = _key(vc, kind, name)
        if key[2] and key not in seen:
            seen.add(key)
            out.append({"host": name.strip(), "kind": kind, "vcenter": vc})
    return out


def swr_alarm_doc(doc, swr=None, email=False):
    """The alarms report with SolarWinds: its section right under Triggered alarms, its tile after Hosts not connected;
    what could not be read: a Not read section at the end. email: the short section (one line) instead of the table."""
    out = dict(doc or {})
    s = swr or {}
    sec = (s.get("email_section") or s.get("section")) if email else s.get("section")
    if not sec:
        return out
    secs = list(out.get("sections") or [])
    if sec.get("title") == "Not read":
        secs.append(sec)
    else:
        i = next((k + 1 for k, x in enumerate(secs) if x.get("title") == "Triggered alarms"), 0)
        secs.insert(i, sec)
        if s.get("not_read"):
            secs.append(s["not_read"])
    out["sections"] = secs
    if s.get("tile"):
        summ = list(out.get("summary") or [])
        j = next((k + 1 for k, x in enumerate(summ) if x.get("label") == "Hosts not connected"), len(summ))
        summ.insert(j, s["tile"])
        out["summary"] = summ
    return out


class FilterModule(object):
    def filters(self):
        return {"swr_health": swr_health, "swr_troubleshoot": swr_troubleshoot, "swr_alarms": swr_alarms,
                "swr_alarm_evidence": swr_alarm_evidence, "swr_act_report": swr_act_report,
                "swr_vcenter_objects": swr_vcenter_objects, "swr_alarm_doc": swr_alarm_doc, "swr_sheets": sheets_of}
