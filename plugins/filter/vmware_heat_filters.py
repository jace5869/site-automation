# -*- coding: utf-8 -*-
"""The cluster heat grid (1.65.0): sections of the VMware health report (roles/vmware_vm/tasks/health.yml).

Input: what site_vmware_health returns (the report's own read), and this template's earlier runs with the job artifact
vm_heat (AAP job history, then the report database). Nothing more is read from vCenter.

A host's status is the health report's own (host_problems): its worst critical or warning problem, else Low (maintenance
mode and the like), else OK. Info (standby, DRS manual) counts as OK here, as on the report's boxes.

Web (atlas, brief): a row per cluster - its hosts as cards (status icon and word, CPU and memory used), each cluster's
CPU and memory used over vm_heat_days (the day's average of the runs, a line each), and what changed since last week
(hosts new to the not-OK list, hosts back to OK, CPU or memory moved by vm_heat_change_pts or more). A host not OK now
that was OK a week ago is marked NEW. Then the hosts that are not OK with how long each has been so, and the numbers
behind the lines. Email: the numbers (no lines, no grid) and a Hosts not OK box. Workbook: the clusters by day with an
Excel sparkline per row (1.62.0).
"""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import calendar
import datetime
import importlib.util
import os
import time

# the health report's helpers (each host's problems, its clusters, the times): loaded as ad_repl_filters loads ad_filters
_spec = importlib.util.spec_from_file_location("vmware_health_for_heat", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                                                      "vmware_health_filters.py"))
_hl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_hl)

RANK = {"critical": 0, "warning": 1, "low": 2, "ok": 3}
WORD = {"critical": "Critical", "warning": "Warning", "low": "Low", "ok": "OK"}
DAY = 86400


def _epoch(iso):
    s = str(iso or "").strip().rstrip("Z").replace(" ", "T")[:19]
    try:
        return calendar.timegm(datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").timetuple())
    except ValueError:
        return None


def _date(epoch):
    return time.strftime("%Y-%m-%d", time.gmtime(epoch))


def _ago(days):
    days = int(days)
    return "new today" if days < 1 else ("same for %d day%s" % (days, "" if days == 1 else "s"))


def host_status(h, cluster, o):
    """(status, why): the host's worst problem the health report finds (critical, warning, low), else ok."""
    ps = [p for p in _hl.host_problems(h, cluster, o) if p[0] in RANK]
    if not ps:
        return "ok", ""
    ps.sort(key=lambda p: RANK[p[0]])
    return ps[0][0], ps[0][1]


def _key(*parts):
    return "/".join(str(p or "") for p in parts)


def _clusters(data, o):
    """[(key, label, cluster record or None, [(host, status, why, cpu, mem)])]: each cluster, then each datacenter's
    standalone hosts as one row."""
    d = data or {}
    multi = len(_hl._vcs(d)) > 1
    dcs = len(set((x.get("vcenter", ""), x.get("datacenter", "")) for x in list(d.get("clusters") or []) + list(d.get("hosts") or [])))
    _, clusters, _, _, _ = _hl._evaluate(d, o)
    rows = []
    for c, hs, _ in clusters:                  # the datacenter in a cluster's name only when there are several
        rows.append((_key(c.get("vcenter"), c.get("datacenter"), c.get("name")),
                     _hl._where(c, multi, c.get("datacenter", "") if dcs > 1 else "", c.get("name", "")), c, hs))
    alone = {}
    for h in d.get("hosts") or []:
        if h.get("standalone") or not h.get("cluster"):
            alone.setdefault((h.get("vcenter", ""), h.get("datacenter", "")), []).append(h)
    for (vc, dc), hs in sorted(alone.items()):
        rows.append((_key(vc, dc, "(standalone)"), _hl._where({"vcenter": vc}, multi, dc if dcs > 1 else "", "standalone hosts"), None, hs))
    out = []
    for key, label, c, hs in rows:
        hosts = []
        for h in sorted(hs, key=lambda x: x.get("name", "")):
            st, why = host_status(h, c, o)
            up = h.get("connection") == "connected"
            hosts.append((h, st, why, _hl._pct(h.get("cpu_used_mhz"), h.get("cpu_mhz")) if up else None,
                          _hl._pct(h.get("mem_used_mb"), h.get("mem_mb")) if up else None))
        live = [h for h in hs if _hl._up(h)]
        cpu = _hl._pct(sum(h.get("cpu_used_mhz") or 0 for h in live), sum(h.get("cpu_mhz") or 0 for h in live)) if live else None
        mem = _hl._pct(sum(h.get("mem_used_mb") or 0 for h in live), sum(h.get("mem_mb") or 0 for h in live)) if live else None
        out.append({"key": key, "label": label, "hosts": hosts, "cpu": cpu, "mem": mem})
    return out


def _runs(runs, now_e, days):
    """The earlier runs' vm_heat artifacts in the window, oldest first: [(epoch, artifact)]."""
    out = []
    for r in runs or []:
        a = (r.get("artifacts") or {}).get("vm_heat") or {}
        t = _epoch(a.get("read_at")) or _epoch(r.get("finished"))
        if t is None or t >= now_e or t < now_e - days * DAY or not isinstance(a.get("clusters"), dict):
            continue
        out.append((t, a))
    out.sort(key=lambda x: x[0])
    seen, uniq = set(), []
    for t, a in out:                      # AAP's history and the database may both have a run
        if t not in seen:
            seen.add(t)
            uniq.append((t, a))
    return uniq


def _week_ago(runs, now_e):
    """The run nearest to 7 days before now, between 5 and 10 days back (None: none)."""
    best = None
    for t, a in runs:
        age = (now_e - t) / float(DAY)
        if 5 <= age <= 10 and (best is None or abs(age - 7) < abs((now_e - best[0]) / float(DAY) - 7)):
            best = (t, a)
    return best


def _series(key, idx, runs, now_e, now_v):
    """[(epoch, %)]: each day's average of the runs (CPU idx 0, memory 1), this run's value as today's."""
    days = {}
    for t, a in runs:
        v = (a.get("clusters") or {}).get(key)
        if not isinstance(v, (list, tuple)) or len(v) <= idx or v[idx] is None:
            continue
        days.setdefault(_date(t), []).append((t, float(v[idx])))
    days.pop(_date(now_e), None)          # today's point is this run's
    pts = [(vs[-1][0], round(sum(x for _, x in vs) / len(vs), 1)) for _, vs in sorted(days.items())]
    if now_v is not None:
        pts.append((now_e, round(float(now_v), 1)))
    return pts


def _since(hkey, st, runs, now_e):
    """How long the host has been in this status: back through the runs while it was not OK at this status."""
    first = now_e
    for t, a in reversed(runs):
        if (a.get("hosts") or {}).get(hkey) != st:
            break
        first = t
    if not runs:
        return "-"
    return _ago((now_e - first) / float(DAY))


def vm_heat_view(data, runs=None, opts=None):
    """The heat grid's sections. -> {web_sections, sections (email), boxes (email and web), web_boxes, sheets, artifact
    (vm_heat), lines, counts}; {} without a host."""
    o = _hl._opts(opts)
    oo = opts or {}
    d = data or {}
    if not d.get("hosts"):
        return {}
    now_e = _epoch(d.get("read_at")) or int(time.time())
    days = int(oo.get("days", 30) or 30)
    pts_change = float(oo.get("change_pts", 5) or 5)
    tz = o["tz"]
    rs = _runs(runs, now_e, days)
    week = _week_ago(rs, now_e)
    wk_hosts = (week[1].get("hosts") or {}) if week else {}
    cls = _clusters(d, o)

    grid, not_ok, nums, sheet_rows, lines = [], [], [], [], []
    art = {"read_at": d.get("read_at"), "clusters": {}, "hosts": {}}
    counts = {"critical": 0, "warning": 0, "low": 0, "ok": 0, "new": 0, "back": 0}
    wk_counts = {"critical": 0, "warning": 0, "low": 0}
    for st in wk_hosts.values():
        if st in wk_counts:
            wk_counts[st] += 1
    for c in cls:
        art["clusters"][c["key"]] = [c["cpu"], c["mem"]]
        cards, changes = [], []
        for h, st, why, cpu, mem in c["hosts"]:
            hkey = _key(h.get("vcenter"), h.get("name"))
            if st != "ok":
                art["hosts"][hkey] = st          # the hosts not OK only: a host not there was OK (or not in the report)
            counts[st] += 1
            was = wk_hosts.get(hkey, "ok") if week else None
            new = bool(week) and st != "ok" and was == "ok"
            if new:
                counts["new"] += 1
                changes.append("%s new %s" % (h.get("name", "").split(".")[0], WORD[st].lower()))
            elif week and st != "ok" and was not in ("ok", st):
                changes.append("%s %s (was %s)" % (h.get("name", "").split(".")[0], WORD[st].lower(), WORD.get(was, was).lower()))
            conn = h.get("connection", "")
            if conn not in ("connected", ""):
                body = _hl.CONN_LABEL.get(conn, conn)
            elif h.get("maintenance"):
                body = "maintenance"
            elif cpu is None and mem is None:
                body = "no data"
            else:
                body = ""
            tip = "\n".join([h.get("name", ""), "%s%s" % (WORD[st], (": " + why) if why else ""),
                             "CPU %s used, memory %s used" % (_hl._pct_text(cpu), _hl._pct_text(mem)) if not body else body]
                            + (["New since last week (it was OK)"] if new else []))
            cards.append({"name": h.get("name", "").split(".")[0], "st": st, "cpu": _hl._pct_text(cpu), "mem": _hl._pct_text(mem),
                          "body": body, "new": new, "tip": tip})
            if st != "ok":
                not_ok.append((RANK[st], c["label"], h.get("name", ""), st, why, "new (was OK)" if new else _since(hkey, st, rs, now_e)))
        if week:
            here = set(_key(h.get("vcenter"), h.get("name")) for h, _, _, _, _ in c["hosts"])
            for hk, was in sorted(wk_hosts.items()):
                if hk in here and was in ("critical", "warning", "low") and hk not in art["hosts"]:
                    counts["back"] += 1
                    changes.append("%s back to OK (was %s)" % (hk.split("/")[-1].split(".")[0], WORD[was].lower()))
        cpu_s = _series(c["key"], 0, rs, now_e, c["cpu"])
        mem_s = _series(c["key"], 1, rs, now_e, c["mem"])
        if week:
            wv = (week[1].get("clusters") or {}).get(c["key"]) or [None, None]
            for label, now_v, then in (("CPU", c["cpu"], wv[0]), ("memory", c["mem"], wv[1] if len(wv) > 1 else None)):
                if now_v is not None and then is not None and abs(now_v - then) >= pts_change:
                    changes.append("%s %d%% to %d%%" % (label, round(then), round(now_v)))
        change = "; ".join(changes) if changes else ("no change" if week else "")
        grid.append({"name": c["label"], "n": len(c["hosts"]), "cpu": _hl._pct_text(c["cpu"]), "mem": _hl._pct_text(c["mem"]),
                     "cpu_points": [[t, v] for t, v in cpu_s], "mem_points": [[t, v] for t, v in mem_s], "cards": cards,
                     "change": change, "status": min([x[1] for x in c["hosts"]] or ["ok"], key=lambda s: RANK[s])})
        first_c = cpu_s[0][1] if len(cpu_s) > 1 else None
        first_m = mem_s[0][1] if len(mem_s) > 1 else None

        def delta(a, b):
            return "" if a is None or b is None else "%+d" % (round(b) - round(a))
        nums.append([c["label"], _hl._pct_text(first_c), _hl._pct_text(c["cpu"]), delta(first_c, c["cpu"]),
                     _hl._pct_text(first_m), _hl._pct_text(c["mem"]), delta(first_m, c["mem"]), len(cpu_s)])
        for what, s, now_v in (("CPU", cpu_s, c["cpu"]), ("Memory", mem_s, c["mem"])):
            sheet_rows.append((c["label"], what, now_v, dict((_date(t), v) for t, v in s)))
        if change:
            lines.append("%s: %s" % (c["label"], change))

    n_bad = counts["critical"] + counts["warning"] + counts["low"]
    worst = "critical" if counts["critical"] else ("warning" if counts["warning"] else ("low" if counts["low"] else "ok"))
    hist_why = str(oo.get("history_why") or "")
    n_days = len(set(_date(t) for t, _ in rs)) + 1
    head = "%d host(s): %s." % (sum(counts[k] for k in ("critical", "warning", "low", "ok")),
                                ", ".join("%d %s" % (counts[k], WORD[k] if k != "ok" else "OK") for k in ("critical", "warning", "low", "ok")))
    if week:
        head += " A week ago (%s): %d critical, %d warning, %d Low." % (_date(week[0]), wk_counts["critical"], wk_counts["warning"], wk_counts["low"])
    else:
        head += " No run about a week ago: nothing marked new yet."
    text = "Every ESXi host as a card, a row per cluster. Each cluster's CPU and memory: the day's average over %d days." % days
    g_lines = [head]
    if hist_why:
        g_lines.append("History: %s." % hist_why)
    elif n_days < 2:
        g_lines.append("One day of history so far: the lines start with the next runs.")
    grid_sec = {"title": "Every host, by cluster", "text": text, "lines": g_lines,
                "heat": {"clusters": grid, "days": days, "legend": [{"st": k} for k in ("critical", "warning", "low", "ok")]}}
    if worst in ("critical", "warning"):
        grid_sec["status"] = worst

    not_ok.sort(key=lambda x: (x[0], x[1], x[2]))
    top = int(oo.get("max_rows", 50) or 50)
    rows = [[x[1], x[2], WORD[x[3]], x[4], x[5]] for x in not_ok[:top]]
    bad_sec = {"title": "Hosts that are not OK", "text": ("Worst first, and how long each has been so (this template's earlier "
                                                           "runs)." if rs else "Worst first.") + (" The first %d of %d." % (top, len(not_ok)) if len(not_ok) > top else "")}
    if rows:
        bad_sec.update(columns=["Cluster", "Host", "Status", "Why", "Since"], rows=rows, row_status=[x[3] for x in not_ok[:top]],
                       cell_status=[["", "", x[3], "", ""] for x in not_ok[:top]])
        if worst in ("critical", "warning"):
            bad_sec["status"] = worst
    else:
        bad_sec["text"] = "Every host is OK."

    num_sec = {"title": "Clusters: CPU and memory over %d days" % days,
               "text": "Each cluster's CPU and memory used, the first day of the %d and now (the hosts running VMs). Change: in "
                       "percentage points. %s" % (days, "" if any(n[7] > 1 for n in nums) else "No earlier day yet: the first "
                                                  "columns fill in with the next runs."),
               "columns": ["Cluster", "CPU %d days ago" % days, "CPU now", "Change", "Memory %d days ago" % days, "Memory now", "Change"],
               "rows": [n[:7] for n in nums]}

    # the boxes: hosts not OK (and a week ago), the cluster that moved most
    note = ""
    if week:
        wk_bad = sum(wk_counts.values())
        note = "a week ago %d: +%d new, -%d back to OK" % (wk_bad, counts["new"], counts["back"])
    boxes = [dict({"label": "Hosts not OK", "value": n_bad, "status": worst if worst != "ok" else "ok"}, **({"note": note} if note else {}))]
    moved = []
    for n in nums:
        for i in (3, 6):
            if n[i]:
                moved.append((abs(int(n[i])), n[0], "CPU" if i == 3 else "memory", n[i - 2], n[i - 1]))
    if moved:
        moved.sort(reverse=True)
        m = moved[0]
        if m[0] >= pts_change:
            boxes.append({"label": "Fastest change", "value": m[1], "status": "info",
                          "note": "%s %s to %s in %d days" % (m[2], m[3], m[4], days)})

    # the workbook: a row per cluster and measure, a column per day, the line in Excel
    dates = sorted(set(dt for r in sheet_rows for dt in r[3]))
    sheet = {"name": "Clusters by day", "columns": ["Cluster", "Measure", "Now %", "Trend"] + dates,
             "rows": [[r[0], r[1], r[2], ""] + [r[3].get(dt) for dt in dates] for r in sheet_rows]}
    if len(dates) > 1:
        sheet["sparklines"] = [{"column": "Trend", "from": [dates[0], dates[-1]]}]

    lines = [head] + lines
    return {"web_sections": [grid_sec, bad_sec, num_sec], "sections": [num_sec], "boxes": boxes, "sheets": [sheet],
            "artifact": art, "lines": lines, "counts": dict(counts, not_ok=n_bad, week=bool(week), runs=len(rs))}


def vm_heat_insert(report, sections, boxes=None, before="Needs attention"):
    """The report with the heat grid's sections before the one titled before (else first), and its boxes after the
    report's own."""
    rep = dict(report or {})
    secs = list(rep.get("sections") or [])
    at = next((i for i, s in enumerate(secs) if isinstance(s, dict) and s.get("title") == before), 0)
    rep["sections"] = secs[:at] + list(sections or []) + secs[at:]
    if boxes:
        rep["summary"] = list(rep.get("summary") or []) + list(boxes)
    return rep


class FilterModule(object):
    def filters(self):
        return {"vm_heat_view": vm_heat_view, "vm_heat_insert": vm_heat_insert}
