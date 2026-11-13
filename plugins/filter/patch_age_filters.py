# -*- coding: utf-8 -*-
"""Patch age (1.61.0): days since each host's last update, by host group - a section of the Linux and Windows health
reports (the patching check: roles/check_patching, roles/win_check_patching) and of the VMware version report (ESXi).

A host: {host, group, last ('YYYY-MM-DD' or ''), days (int, or None: no data), at_least (bool: days is only a floor),
restart (bool; None = not known), late_after (late when days is over it), crit_from (critical from this many days; 0 = none)}.

Status of a host:
  late        days over late_after (critical from crit_from: Windows' win_check_patching_crit_days)
  not known   at_least and not late: ESXi whose build did not change in the history - the last update is at least
              that old, maybe older (Low; never counted as on time)
  due soon    late within patch_age_due_days (Low)
  on time     the rest
  no data     the check could not read the last update

History: each run publishes patch_age (per group: hosts with data, on time); "% on time, 12 weeks" is the first run of
the last 12 weeks -> this run. On the website (atlas, brief) a Trend column beside it draws every run of those weeks
as a line with a hover (int-11; two points at least, else no line); the email keeps the number. ESXi has no read-only "last update" date in vCenter: patch_age_esxi works it out from
the version report's own runs (when the host's build changed) and the host's boot time (an ESXi update needs a restart).
"""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import calendar
import re
import time

WEEKS = 12
SPARK_W, SPARK_H, SPARK_PAD = 120, 30, 4         # the Trend column's line, as the other reports' (1.52.0, 1.58.0)
WORD = {"critical": "Late", "late": "Late", "unknown": "Not known yet", "due": "Due soon", "ontime": "On time", "nodata": "No data"}
LEVEL = {"critical": "critical", "late": "warning", "unknown": "low", "due": "low", "ontime": "ok", "nodata": ""}
RANK = {"critical": 0, "late": 1, "unknown": 2, "due": 3, "nodata": 4, "ontime": 5}


def _int(x, d=0):
    try:
        return int(x)
    except (TypeError, ValueError):
        return d


def _epoch(text):
    """'2026-10-21T06:01:12.5Z' (AAP's finished) or an epoch -> epoch seconds; None when it is neither."""
    if isinstance(text, (int, float)):
        return int(text)
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)", str(text or ""))
    if not m:
        return None
    return calendar.timegm(tuple(int(x) for x in m.groups()) + (0, 0, 0))


def _date(epoch):
    return time.strftime("%Y-%m-%d", time.gmtime(epoch)) if epoch else ""


def _yn(v, unknown="-"):
    """A restart pending: yes / no; not known (ESXi: vCenter does not say) '-'."""
    return unknown if v is None else ("yes" if v else "no")


def host_status(h, due_days=7):
    days = h.get("days")
    if days is None:
        return "nodata"
    days, late = _int(days), _int(h.get("late_after"), 35)
    if days > late:
        crit = _int(h.get("crit_from"))
        return "critical" if crit and days >= crit else "late"
    if h.get("at_least"):
        return "unknown"
    if days > late - _int(due_days, 7):
        return "due"
    return "ontime"


def patch_age_group(group_names, groups=None):
    """The host's group: the first of patch_age_groups it is in; without patch_age_groups its first inventory group;
    else Other."""
    mine = [str(g) for g in group_names or [] if str(g) not in ("all", "ungrouped")]
    for g in groups or []:
        if str(g) in mine:
            return str(g)
    if groups:
        return "Other"
    return mine[0] if mine else "Other"


def _mode(values, default):
    vals = [v for v in values if v is not None]
    return max(set(vals), key=vals.count) if vals else default


def _pct(on, n):
    return int(round(100.0 * on / n)) if n else None


def _first_pct(group, runs, now):
    """The group's % on time in the first run of the last 12 weeks that had it; None without one."""
    since = now - WEEKS * 7 * 86400
    for r in runs or []:
        t = _epoch((r or {}).get("finished"))
        g = (((r or {}).get("artifacts") or {}).get("patch_age") or {}).get("groups") or {}
        if t is None or t < since or group not in g:
            continue
        try:
            p = _pct(int(g[group][1]), int(g[group][0]))
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if p is not None:
            return p
    return None


def _series(group, runs, now, now_pct):
    """The group's % on time in every run of the last 12 weeks, oldest first, then this run: [(epoch, %)]."""
    since, out = now - WEEKS * 7 * 86400, []
    for r in runs or []:
        t = _epoch((r or {}).get("finished"))
        g = (((r or {}).get("artifacts") or {}).get("patch_age") or {}).get("groups") or {}
        if t is None or t < since or t >= now or group not in g:
            continue
        try:
            p = _pct(int(g[group][1]), int(g[group][0]))
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if p is not None:
            out.append((t, p))
    out.sort(key=lambda x: x[0])
    if now_pct is not None:
        out.append((now, now_pct))
    return out


def _day(epoch):
    """A run's day for the hover: Mon 2 Nov 2026."""
    g = time.gmtime(epoch)
    return "%s %d %s" % (time.strftime("%a", g), g.tm_mday, time.strftime("%b %Y", g))


def spark(points, now, name):
    """[(epoch, %)] -> the Trend column's line (the atlas and brief looks draw it; site-report.js its hover): {w, h,
    points, last, lines, mark, label, name, data_points}; {} with fewer than 2 points. x by time over the 12 weeks, y
    from the lowest value to the highest."""
    if len(points) < 2:
        return {}
    since = now - WEEKS * 7 * 86400
    vals = [p[1] for p in points]
    lo, hi = min(vals), max(vals)
    if hi == lo:
        lo, hi = lo - 1, hi + 1
    inner_w, inner_h = SPARK_W - 2.0 * SPARK_PAD, SPARK_H - 2.0 * SPARK_PAD
    pts, data = [], []
    for t, v in points:
        x = round(SPARK_PAD + max(0.0, min(1.0, (t - since) / float(now - since))) * inner_w, 1)
        y = round(SPARK_PAD + (1.0 - (v - lo) / float(hi - lo)) * inner_h, 1)
        pts.append((x, y))
        data.append({"x": x, "y": y, "label": _day(t), "text": "%d%% on time" % v})
    label = "%s, %% on time: %d%% on %s to %d%% now, %d runs: %s" % (
        name, vals[0], _date(points[0][0]), vals[-1], len(vals), ", ".join("%d%%" % v for v in vals))
    return {"w": SPARK_W, "h": SPARK_H, "points": " ".join("%s,%s" % q for q in pts), "last": list(pts[-1]), "lines": [],
            "mark": None, "label": label, "name": name, "data_points": data}


def _trend(first, now_pct):
    """'93% -> 90%'; '90%' with no earlier run; '-' when no host of the group has data."""
    if now_pct is None:
        return "-"
    return "%d%% \u2192 %d%%" % (first, now_pct) if first is not None else "%d%%" % now_pct


def patch_age_sections(hosts, opts=None, runs=None):
    """The section(s) of a report: {sections (email), web_sections (the squares first), boxes, status, artifact, sheet,
    lines (job output), counts}. opts: due_days, what (e.g. 'Linux'), setting (the late setting's name), now (epoch,
    for tests), oldest (10)."""
    o = dict({"due_days": 7, "what": "", "setting": "check_patching_max_age_days", "now": None, "oldest": 10}, **(opts or {}))
    o["setting"] = o["setting"] or "check_patching_max_age_days"
    due = _int(o["due_days"], 7)
    now = _int(o["now"]) or int(time.time())
    hs = []
    for h in hosts or []:
        if not isinstance(h, dict) or not h.get("host"):
            continue
        x = dict(h)
        x["days"] = None if h.get("days") in (None, "") else _int(h.get("days"))
        x["group"] = str(h.get("group") or "Other")
        x["at_least"] = bool(h.get("at_least"))
        x["st"] = host_status(x, due)
        hs.append(x)
    if not hs:
        return {}
    late = _mode([_int(h.get("late_after"), 35) for h in hs], 35)
    crits = [_int(h.get("crit_from")) for h in hs if _int(h.get("crit_from"))]
    top = min(crits) if crits else (60 if late < 60 else 2 * late)
    cuts = [max((late - due) // 2, 1), late - due, late, top]
    groups = []
    for g in sorted(set(h["group"] for h in hs), key=lambda s: (s == "Other", s.lower())):
        mine = sorted([h for h in hs if h["group"] == g], key=lambda h: (h["days"] is None, -(h["days"] or 0), h["host"]))
        n = dict((k, len([h for h in mine if h["st"] == k])) for k in RANK)
        groups.append((g, mine, n))

    def gstatus(n):
        if n["critical"]:
            return "critical", "%d late" % (n["critical"] + n["late"])
        if n["late"]:
            return "warning", "%d late" % n["late"]
        if n["unknown"]:
            return "low", "%d not known yet" % n["unknown"]
        if n["due"]:
            return "low", "%d due soon" % n["due"]
        if n["ontime"]:
            return "ok", "On time"
        return "", "No data"

    tot = dict((k, sum(x[2][k] for x in groups)) for k in RANK)
    unknown_col, nodata_col = tot["unknown"] > 0, tot["nodata"] > 0
    cols = ["Group", "Hosts", "On time", "Due soon", "Late"] + (["Not known yet"] if unknown_col else []) \
        + (["No data"] if nodata_col else []) + ["Oldest (days)", "%% on time, %d weeks" % WEEKS, "Status"]
    rows, rs, cs, art, sparks = [], [], [], {}, []
    for g, mine, n in groups:
        with_data = len(mine) - n["nodata"]
        pct = _pct(n["ontime"], with_data)
        art[g] = [with_data, n["ontime"]]
        known = [h for h in mine if h["days"] is not None]
        oldest = max(h["days"] for h in known) if known else None
        st, word = gstatus(n)
        dash = lambda v: v or "-"
        row = [g, len(mine), dash(n["ontime"]), dash(n["due"]), dash(n["late"] + n["critical"])] \
            + ([dash(n["unknown"])] if unknown_col else []) + ([dash(n["nodata"])] if nodata_col else []) \
            + [("at least %d" % oldest if any(h["at_least"] and h["days"] == oldest for h in known) else oldest) if oldest is not None else "-",
               _trend(_first_pct(g, runs, now), pct), word]
        rows.append(row)
        sparks.append(spark(_series(g, runs, now, pct), now, g))
        rs.append(st if st in ("critical", "warning", "low") else "")
        cs.append([""] * (len(row) - 1) + [st])
    worst = min([LEVEL[h["st"]] for h in hs if LEVEL[h["st"]] in ("critical", "warning", "low")] or ["ok"],
                key=lambda s: {"critical": 0, "warning": 1, "low": 2}.get(s, 3))
    what = (o["what"] + " ") if o["what"] else ""
    rule = "late = no update for %d days or more (%s); due soon (Low) = late within %d days (patch_age_due_days)" % (late + 1, o["setting"], due)
    if crits:
        rule += "; critical from %d days" % min(crits)
    by_group = {"title": "Patch age by group", "status": worst,
                "text": "Days since each %shost's last update, by host group (patch_age_groups): %s." % (what, rule),
                "columns": cols, "rows": rows, "row_status": rs, "cell_status": cs}
    ordered = sorted([h for h in hs if h["days"] is not None], key=lambda h: (-h["days"], RANK[h["st"]], h["host"]))
    top_n = ordered[:_int(o["oldest"], 10)]
    old = {"title": "Patch age: the %d oldest hosts" % len(top_n) if len(top_n) > 1 else "Patch age: the oldest host",
           "status": worst,
           "columns": ["Host", "Group", "Last update", "Days", "Restart pending", "Status"],
           "rows": [[h["host"], h["group"], h.get("last") or ("not known" if h["at_least"] else "-"),
                     ("at least %d" % h["days"]) if h["at_least"] else h["days"], _yn(h.get("restart")),
                     WORD[h["st"]]] for h in top_n],
           "row_status": [LEVEL[h["st"]] if LEVEL[h["st"]] != "ok" else "" for h in top_n],
           "cell_status": [["", "", "", "", "", LEVEL[h["st"]]] for h in top_n]}
    lines = []
    if tot["unknown"]:
        lines.append("Not known yet (Low): the update date is known once the build changes, or after %d days of history." % late)
    nod = [h["host"] for h in hs if h["st"] == "nodata"]
    if nod:
        lines.append("No data (%d): %s - the last update could not be read." % (len(nod), ", ".join(sorted(nod)[:30])
                                                                              + (" and %d more" % (len(nod) - 30) if len(nod) > 30 else "")))
    if all(_first_pct(g, runs, now) is None for g, _m, _n in groups):
        lines.append("%% on time, %d weeks: no earlier run in AAP's job history yet - the trend starts with the next run." % WEEKS)
    if lines:
        old["lines"] = lines
    legend = [("q1", "0-%d days" % cuts[0]), ("q2", "%d-%d days" % (cuts[0] + 1, cuts[1])),
              ("q3", "%d-%d days (due soon)" % (cuts[1] + 1, cuts[2])), ("q4", "%d-%d days (late)" % (cuts[2] + 1, cuts[3])),
              ("q5", "%d+ days (late)" % (cuts[3] + 1)), ("q0", "no data")]

    def q(h):
        d = h["days"]
        if d is None:
            return "q0"
        return "q1" if d <= cuts[0] else "q2" if d <= cuts[1] else "q3" if d <= cuts[2] else "q4" if d <= cuts[3] else "q5"

    def tip(h):
        if h["days"] is None:
            return "%s: no data" % h["host"]
        return "%s: %s%d days%s - %s" % (h["host"], "at least " if h["at_least"] else "", h["days"],
                                         (" (%s)" % h["last"]) if h.get("last") else "", WORD[h["st"]])
    squares = {"title": "Patch age at a glance", "status": worst,
               "text": "One square = one host, oldest first; the darker, the longer since its last update. Hover a square for its host.",
               "squares": {"label": "Days since the last update:", "legend": [{"q": k, "text": t} for k, t in legend],
                           "groups": [{"name": g, "sub": "%d host%s" % (len(mine), "" if len(mine) == 1 else "s"),
                                       "status": gstatus(n)[0], "word": gstatus(n)[1],
                                       "cells": [{"q": q(h), "tip": tip(h)} for h in mine]} for g, mine, n in groups]}}
    oldest_all = ordered[0] if ordered else None
    boxes = [{"label": "Patching late", "value": tot["late"] + tot["critical"],
              "status": ("critical" if tot["critical"] else "warning") if tot["late"] + tot["critical"] else "ok",
              "note": "no update for %d days or more" % (late + 1)},
             {"label": "Patching due soon", "value": tot["due"], "status": "low" if tot["due"] else "ok",
              "note": "late within %d days" % due},
             {"label": "Oldest update", "value": ("%s%d days" % ("at least " if oldest_all["at_least"] else "", oldest_all["days"]))
              if oldest_all else "-", "status": LEVEL[oldest_all["st"]] if oldest_all and LEVEL[oldest_all["st"]] != "ok" else "",
              "note": oldest_all["host"] if oldest_all else "no data"}]
    if tot["unknown"]:
        boxes.append({"label": "Patch date not known yet", "value": tot["unknown"], "status": "low", "note": "build unchanged in the history"})
    sheet = {"name": "Patch age", "columns": ["Host", "Group", "Last update", "Days", "Days is a floor", "Restart pending", "Status",
                                              "Late from (days)"],
             "rows": [[h["host"], h["group"], h.get("last") or "", "" if h["days"] is None else h["days"], "yes" if h["at_least"] else "",
                       _yn(h.get("restart"), ""), WORD[h["st"]], _int(h.get("late_after"), 35) + 1]
                      for h in sorted(hs, key=lambda h: (h["group"].lower(), h["days"] is None, -(h["days"] or 0), h["host"]))]}
    counts = {"hosts": len(hs), "on_time": tot["ontime"], "due": tot["due"], "late": tot["late"] + tot["critical"],
              "not_known": tot["unknown"], "no_data": tot["nodata"]}
    out_lines = ["Patch age: %d host(s) - %d on time, %d due soon, %d late%s%s; oldest %s" % (
        len(hs), tot["ontime"], tot["due"], tot["late"] + tot["critical"],
        (", %d not known yet" % tot["unknown"]) if tot["unknown"] else "", (", %d no data" % tot["nodata"]) if tot["nodata"] else "",
        boxes[2]["value"] + ((" (%s)" % oldest_all["host"]) if oldest_all else ""))]
    out_lines += ["  %s: %s" % (r[0], r[-1]) for r in rows]
    by_group["boxes"] = boxes                    # findings_report adds them to the report's boxes
    web_group = by_group
    if any(sparks):                              # the website: a Trend column after "% on time, 12 weeks" (no line: blank)
        at = len(cols) - 1
        web_group = dict(by_group, columns=cols[:at] + ["Trend"] + cols[at:],
                         kinds=[""] * at + ["spark", ""], sparks=sparks,
                         rows=[r[:at] + [sp.get("label", "")] + r[at:] for r, sp in zip(rows, sparks)],
                         cell_status=[c[:at] + [""] + c[at:] for c in cs])
    return {"sections": [by_group, old], "web_sections": [squares, web_group, old], "boxes": boxes, "status": worst,
            "artifact": dict(counts, groups=art, late_after=late), "sheet": sheet, "lines": out_lines, "counts": counts}


def patch_age_esxi_artifact(hosts):
    """What each version report run keeps for the next ones (in the vm_versions artifact): {host: [build, boot epoch]}."""
    return dict((str(h.get("name")), [str(h.get("build") or ""), _int(h.get("boot")) or None])
                for h in hosts or [] if h.get("name"))


def patch_age_esxi(hosts, runs=None, opts=None):
    """ESXi hosts (site_vmware_versions: name, cluster, vcenter, build, boot) -> patch age hosts. runs: the version
    report's earlier runs, oldest first, each {finished, artifacts: {vm_versions: {esxi_hosts: {host: [build, boot]}}}}.
      - the build changed between two runs: the update date is the boot time the first run with the new build saw
        (when it falls between the two runs), else that run's time;
      - no change in the history: at least since the oldest run with this build, or the earliest boot with it
        (an ESXi update needs a restart, so the last update is no newer than either) - a floor;
      - not connected (no build): no data."""
    o = dict({"now": None, "late_after": 35, "multi_vcenter": None}, **(opts or {}))
    now = _int(o["now"]) or int(time.time())
    pts = []
    for r in runs or []:
        t = _epoch((r or {}).get("finished"))
        e = ((((r or {}).get("artifacts") or {}).get("vm_versions") or {}).get("esxi_hosts")) or {}
        if t is not None and isinstance(e, dict):
            pts.append((t, e))
    pts.sort(key=lambda p: p[0])
    hosts = [h for h in hosts or [] if h.get("name")]
    multi = o["multi_vcenter"] if o["multi_vcenter"] is not None else len(set(h.get("vcenter") for h in hosts)) > 1
    out = []
    for h in hosts:
        name, build = str(h["name"]), str(h.get("build") or "")
        group = (h.get("cluster") or "(standalone)")
        if multi:
            group = "%s / %s" % (str(h.get("vcenter") or "").split(".")[0], group)
        x = {"host": name, "group": group, "late_after": _int(o["late_after"], 35), "crit_from": 0, "restart": None,
             "last": "", "days": None, "at_least": False}
        if not build:
            out.append(x)
            continue
        boot = _int(h.get("boot")) or None
        first_t, boots, changed_at = now, [boot] if boot else [], None
        for t, e in reversed(pts):
            v = e.get(name)
            if not isinstance(v, (list, tuple)) or not v:
                continue                                  # not read that run: the streak goes on
            if str(v[0] or "") != build:
                changed_at = t
                break
            first_t = t
            if len(v) > 1 and _int(v[1]):
                boots.append(_int(v[1]))
        if changed_at is not None:
            seen = [b for b in boots if changed_at < b <= first_t]
            when = min(seen) if seen else first_t
            x.update(last=_date(when), days=max((now - when) // 86400, 0))
        else:
            floor = min([first_t] + boots)
            x.update(days=max((now - floor) // 86400, 0), at_least=True)
        out.append(x)
    return out


class FilterModule(object):
    def filters(self):
        return {"patch_age_sections": patch_age_sections, "patch_age_group": patch_age_group,
                "patch_age_esxi": patch_age_esxi, "patch_age_esxi_artifact": patch_age_esxi_artifact,
                "patch_age_host_status": host_status}
