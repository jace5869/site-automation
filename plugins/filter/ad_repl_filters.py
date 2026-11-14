# -*- coding: utf-8 -*-
"""The replication matrix (1.63.0): a section of the AD health report (roles/ad_report), in admin and reader mode.

Input: the report's repl (plugins/filter/ad_filters.py _repl_admin / _repl_reader) - every DC AD knows with its site, and
every inbound link a DC's own replication state gave: {to, frm, nc, last_success, last_attempt, last_failure, failures,
error}. A pair (from -> to) is its worst partition's link.

Status of a link (the Inbound replication table's, with Low before amber):
  critical   no success for longer than the tombstone lifetime; failing and no success for ad_repl_crit_hours; or no
             success for ad_repl_crit_hours
  warning    failing but a success within ad_repl_crit_hours; never replicated; no success for ad_repl_warn_hours
  low        no success for ad_repl_low_hours (one site link schedule missed: worth knowing, nothing to do yet)
  ok         the rest

Web (atlas, brief): the worst pair as a card, DC x DC (grouped by site; site x site past ad_repl_matrix_max_dcs) with the
age of the last success in each cell and a hover with the pair, partition, last success, last failure and error; the
links that need a look; each site's worst inbound age over ad_repl_trend_days (a Trend column, from this template's
earlier runs' ad_repl artifact). Email: the links that need a look and the numbers. Workbook: every link, and each site's
worst age by day.
"""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import calendar
import datetime
import importlib.util
import os
import time

# ad_filters' helpers (the error words, the partition names, the times): loaded as it loads site_time
_spec = importlib.util.spec_from_file_location("ad_filters_for_repl", os.path.join(os.path.dirname(os.path.abspath(__file__)), "ad_filters.py"))
_ad = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ad)

RANK = {"critical": 0, "warning": 1, "unknown": 2, "low": 3, "ok": 4}
WORD = {"critical": "Critical", "warning": "Warning", "low": "Low", "ok": "OK", "unknown": "Not read"}
SPARK_W, SPARK_H, SPARK_PAD = 120, 30, 4         # the Trend column's line, as the other reports' (1.52.0, 1.58.0, 1.61.0)
NO_SITE = "(no site)"


def _epoch(v):
    t = _ad.parse_time(v)
    return calendar.timegm(t.utctimetuple()) if t else None


def age_short(hours):
    """A cell's age: 4 m, 52 m, 3 h, 2 d; never."""
    if hours is None:
        return "never"
    if hours < 1:
        return "%d m" % max(0, int(round(hours * 60)))
    if hours < 48:
        return "%d h" % int(hours)
    return "%d d" % int(hours / 24)


def link_status(age_h, fails, o):
    """(status, why) of one link: see the module's text."""
    low, warn, crit, tsl = o["low"], o["warn"], o["crit"], o["tsl"]
    if age_h is not None and tsl and age_h / 24.0 >= tsl:
        return "critical", "no success for longer than the tombstone lifetime (%d days)" % tsl
    if fails:
        return ("critical" if age_h is None or age_h >= crit else "warning"), "%d failure(s) in a row" % fails
    if age_h is None:
        return "warning", "never replicated"
    if age_h >= crit:
        return "critical", "no success for %s" % _ad.age_text(age_h)
    if age_h >= warn:
        return "warning", "no success for %s" % _ad.age_text(age_h)
    if low and age_h >= low:
        return "low", "no success for %s (a schedule missed)" % _ad.age_text(age_h)
    return "ok", ""


def _err(code):
    code = str(code or "").strip()
    if not code or code == "0":
        return ""
    return "%s - %s" % (code, _ad.REPL_ERRORS[code]) if code in _ad.REPL_ERRORS else code


def _next_step(p):
    """What to do first for a pair, from its error."""
    code = str(p["error"] or "")
    frm, to = p["frm"], p["to"]
    if p["why"].startswith("no success for longer than the tombstone"):
        return ("Do not force replication: remove the lingering objects first (repadmin /removelingeringobjects).",
                "repadmin /showrepl %s /errorsonly" % to)
    if code in ("1722", "1753", "1256", "64"):
        return ("Check the network and firewall between %s and %s (TCP 135 and the RPC ports), then replicate again." % (frm, to),
                "repadmin /replicate %s %s %s" % (to, frm, p["nc_dn"] or p["nc"]))
    if code in ("8524", "1908"):
        return ("Check that %s finds %s in DNS (its _msdcs alias), then replicate again." % (to, frm),
                "repadmin /replicate %s %s %s" % (to, frm, p["nc_dn"] or p["nc"]))
    if code in ("5", "8453", "1396", "-2146893022"):
        return ("Check the two DCs' computer accounts and their clocks (Kerberos), then replicate again.",
                "repadmin /showrepl %s /errorsonly" % to)
    if code in ("8456", "8457"):
        return ("Replication is turned off on one of the two DCs: see why before turning it back on.",
                "repadmin /options %s" % to)
    if p["failures"]:
        return ("See the details on %s." % to, "repadmin /showrepl %s /errorsonly" % to)
    return ("No error: the link may only replicate on a schedule. Check the site link's schedule.", "repadmin /showrepl %s" % to)


def _pairs(repl, now, o, tz):
    """The links folded into pairs (from, to): each its worst partition's link, with how many partitions it carries."""
    by = {}
    for ln in repl.get("links") or []:
        age = _ad.age_hours(ln.get("last_success"), now)
        st, why = link_status(age, int(ln.get("failures") or 0), o)
        x = dict(ln, age=age, st=st, why=why)
        by.setdefault((ln["frm"], ln["to"]), []).append(x)
    pairs = {}
    for k, lst in by.items():
        lst.sort(key=lambda x: (RANK.get(x["st"], 9), -(x["age"] if x["age"] is not None else 1e9)))
        w = lst[0]
        pairs[k] = {"frm": k[0], "to": k[1], "st": w["st"], "why": w["why"], "age": w["age"], "nc": _ad.nc_name(w.get("nc")),
                    "nc_dn": w.get("nc") if str(w.get("nc") or "").upper().startswith(("DC=", "CN=")) else "",
                    "last_success": _ad.time_text(w.get("last_success")), "last_failure": _ad.time_text(w.get("last_failure")),
                    "failures": int(w.get("failures") or 0), "error": str(w.get("error") or ""), "ncs": len(lst)}
        pairs[k]["ls_local"] = _ad._tm.local(pairs[k]["last_success"], tz) if pairs[k]["last_success"] else ""
        pairs[k]["lf_local"] = _ad._tm.local(pairs[k]["last_failure"], tz) if pairs[k]["last_failure"] else ""
    return pairs


def _tip(p, site_of):
    lines = ["%s (%s) → %s (%s)" % (p["frm"], site_of.get(p["frm"]) or NO_SITE, p["to"], site_of.get(p["to"]) or NO_SITE),
             "%s: %s" % (WORD.get(p["st"], p["st"]), p["why"] or "on time"),
             "Partition: %s%s" % (p["nc"], (" (the worst of %d)" % p["ncs"]) if p["ncs"] > 1 else ""),
             "Last success: %s" % (("%s, %s ago" % (p["ls_local"], _ad.age_text(p["age"]))) if p["ls_local"] else "never")]
    if p["failures"] or p["lf_local"]:
        lines.append("Last failure: %s" % (p["lf_local"] or "-"))
    if p["error"]:
        lines.append("Error: %s (%d failure(s))" % (_err(p["error"]), p["failures"]))
    return "\n".join(lines)


def _matrix(dcs, pairs, o, site_of):
    """The matrix the atlas and brief looks draw (report_matrix.html.j2): DC x DC by site, or site x site past max_dcs."""
    sites = sorted(set(d["site"] or NO_SITE for d in dcs), key=lambda s: (s == NO_SITE, s.lower()))
    order = sorted(dcs, key=lambda d: (sites.index(d["site"] or NO_SITE), d["name"]))
    by_site = dict((s, [d for d in order if (d["site"] or NO_SITE) == s]) for s in sites)
    legend = [{"st": "ok", "text": "up to %s without success" % _ad.age_text(o["low"] or o["warn"])},
              {"st": "low", "text": "%s (a schedule missed)" % _ad.age_text(o["low"])} if o["low"] else None,
              {"st": "warning", "text": "%s, or failing" % _ad.age_text(o["warn"])},
              {"st": "critical", "text": "%s, or failing that long" % _ad.age_text(o["crit"])},
              {"st": "unknown", "text": "the DC's replication state was not read"}]
    legend = [x for x in legend if x]
    if len(order) <= o["max_dcs"]:
        cols = [{"name": d["name"], "site": d["site"] or NO_SITE} for d in order]
        rows = []
        for d in order:
            s = d["site"] or NO_SITE
            cells = []
            for c in order:
                to = next(x for x in order if x["name"] == c["name"])
                if c["name"] == d["name"]:
                    cells.append({"st": "self", "text": "", "tip": ""})
                elif not to["read"]:
                    cells.append({"st": "unknown", "text": "?", "tip": "%s → %s\nNot read: %s" % (d["name"], c["name"], to["why"] or "no data")})
                elif (d["name"], c["name"]) in pairs:
                    p = pairs[(d["name"], c["name"])]
                    cells.append({"st": p["st"], "text": age_short(p["age"]), "tip": _tip(p, site_of)})
                else:
                    cells.append({"st": "none", "text": "", "tip": "%s → %s\nNot replication partners" % (d["name"], c["name"])})
            rows.append({"name": d["name"], "site": s, "first": by_site[s][0] is d, "span": len(by_site[s]), "cells": cells})
        return {"mode": "dc", "groups": [{"name": s, "n": len(by_site[s])} for s in sites], "cols": cols, "rows": rows,
                "legend": legend, "axis": "From ↓ To →"}
    # site x site: each cell the worst pair from a DC of the row's site to a DC of the column's
    rows = []
    for a in sites:
        cells = []
        for b in sites:
            ps = [p for (f, t), p in pairs.items() if (site_of.get(f) or NO_SITE) == a and (site_of.get(t) or NO_SITE) == b]
            unread = [d["name"] for d in by_site[b] if not d["read"]]
            if not ps:
                cells.append({"st": "unknown" if unread else "none", "text": "?" if unread else "",
                              "tip": "%s → %s\n%s" % (a, b, ("Not read: " + ", ".join(unread)) if unread else "No replication link")})
                continue
            ps.sort(key=lambda p: (RANK.get(p["st"], 9), -(p["age"] if p["age"] is not None else 1e9)))
            w = ps[0]
            late = len([p for p in ps if p["st"] != "ok"])
            tip = "%s → %s: %d link(s), %d late\nWorst: %s" % (a, b, len(ps), late, _tip(w, site_of))
            if unread:
                tip += "\nNot read: " + ", ".join(unread)
            cells.append({"st": w["st"], "text": age_short(w["age"]), "tip": tip})
        rows.append({"name": "%s (%d)" % (a, len(by_site[a])), "site": "", "first": False, "span": 1, "cells": cells})
    return {"mode": "site", "groups": [], "cols": [{"name": "%s (%d)" % (s, len(by_site[s])), "site": s} for s in sites],
            "rows": rows, "legend": legend, "axis": "From site ↓ To site →",
            "note": "%d DCs: more than ad_repl_matrix_max_dcs (%d), so each cell is a site's worst link to another site." % (len(order), o["max_dcs"])}


def _day(epoch):
    g = time.gmtime(epoch)
    return "%s %d %s" % (time.strftime("%a", g), g.tm_mday, time.strftime("%b %Y", g))


def _series(site, runs, now_e, now_h, days):
    """[(epoch, hours)]: each day's worst from the earlier runs' ad_repl artifacts, then this run."""
    since = now_e - days * 86400
    per_day = {}
    for r in runs or []:
        a = (r.get("artifacts") or {}).get("ad_repl") or {}
        t = _epoch(a.get("read_at")) or _epoch(r.get("finished"))
        v = (a.get("sites") or {}).get(site)
        if t is None or v is None or t < since or t >= now_e:
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        d = time.strftime("%Y-%m-%d", time.gmtime(t))
        if d not in per_day or v > per_day[d][1]:
            per_day[d] = (t, v)
    today = time.strftime("%Y-%m-%d", time.gmtime(now_e))
    earlier = per_day.pop(today, None)                 # today's point is this run's, at the worst of today's runs
    pts = sorted(per_day.values())
    if now_h is not None:
        pts.append((now_e, max(now_h, earlier[1]) if earlier else now_h))
    return pts


def _spark(points, now_e, days, warn, name):
    """[(epoch, hours)] -> the Trend column's line ({} with fewer than 2 points): y from 0 to the larger of the worst
    value and ad_repl_warn_hours, whose level is the dashed line - a flat line low down is healthy."""
    if len(points) < 2:
        return {}
    since = now_e - days * 86400
    hi = max([p[1] for p in points] + [warn]) * 1.05 or 1.0
    iw, ih = SPARK_W - 2.0 * SPARK_PAD, SPARK_H - 2.0 * SPARK_PAD
    pts, data = [], []
    for t, v in points:
        x = round(SPARK_PAD + max(0.0, min(1.0, (t - since) / float(now_e - since))) * iw, 1)
        y = round(SPARK_PAD + (1.0 - v / hi) * ih, 1)
        pts.append((x, y))
        data.append({"x": x, "y": y, "label": _day(t), "text": "worst %s" % _ad.age_text(v)})
    label = "%s, the worst replication age by day: %s" % (name, ", ".join(_ad.age_text(v) for _, v in points))
    return {"w": SPARK_W, "h": SPARK_H, "points": " ".join("%s,%s" % q for q in pts), "last": list(pts[-1]),
            "lines": [round(SPARK_PAD + (1.0 - warn / hi) * ih, 1)], "mark": None, "label": label, "name": name, "data_points": data}


def ad_repl_view(repl, runs=None, opts=None):
    """The replication matrix's sections. -> {sections (email), web_sections (atlas, brief), sheets (workbook), artifact
    (ad_repl, for the next runs' trend), lines (the job log), status, counts}; {} without a repl (AD not read)."""
    o = opts or {}
    r = repl or {}
    if not r.get("dcs"):
        return {}
    now = _ad.parse_time(r.get("now")) or datetime.datetime.now(datetime.timezone.utc)
    now_e = calendar.timegm(now.utctimetuple())
    tz = _ad._tm.zone(o.get("time_zone"))[0]
    oo = {"low": float(o.get("low_hours", 4) or 0), "warn": float(o.get("warn_hours", 6)), "crit": float(o.get("crit_hours", 24)),
          "tsl": float(r.get("tombstone_days") or 0), "max_dcs": int(o.get("max_dcs", 12))}
    days, top = int(o.get("trend_days", 14)), int(o.get("max_rows", 50))
    dcs = r["dcs"]
    site_of = dict((d["name"], d["site"]) for d in dcs)
    pairs = _pairs(r, now, oo, tz)
    read = [d for d in dcs if d["read"]]
    unread = [d for d in dcs if not d["read"]]
    count = dict((k, len([p for p in pairs.values() if p["st"] == k])) for k in ("ok", "low", "warning", "critical"))
    late = sorted([p for p in pairs.values() if p["st"] != "ok"],
                  key=lambda p: (RANK.get(p["st"], 9), -(p["age"] if p["age"] is not None else 1e9), p["frm"], p["to"]))
    status = "critical" if count["critical"] else ("warning" if count["warning"] else ("low" if count["low"] else "ok"))
    if not pairs:
        status = "unknown"
    sites = sorted(set(d["site"] or NO_SITE for d in dcs), key=lambda s: (s == NO_SITE, s.lower()))

    # the numbers
    nums = "%d link(s) between %d DCs in %d site(s): %d on time, %d Low, %d warning, %d critical." % (
        len(pairs), len(dcs), len(sites), count["ok"], count["low"], count["warning"], count["critical"])
    if unread:
        nums += " Not read: %s." % ", ".join("%s (%s)" % (d["name"], d["why"] or "no data") for d in unread[:10])

    # the links that need a look (web and email)
    cols = ["From", "To", "Partition", "Last success", "Last error", "Status"]
    rows, rst = [], []
    for p in late[:top]:
        rows.append(["%s (%s)" % (p["frm"], site_of.get(p["frm"]) or NO_SITE), "%s (%s)" % (p["to"], site_of.get(p["to"]) or NO_SITE),
                     p["nc"], ("%s ago" % _ad.age_text(p["age"])) if p["age"] is not None else "never",
                     ("%s (%d failure(s))" % (_err(p["error"]), p["failures"])) if p["error"] else (p["why"] if p["st"] != "ok" else ""),
                     WORD[p["st"]]])
        rst.append(p["st"])
    about = "Each pair of DCs that replicate, the worst partition, worst first. %s" % nums
    if len(late) > top:
        about += " The first %d of %d are shown: the workbook has every link." % (top, len(late))
    links_sec = {"title": "Replication links that need a look", "text": about}
    if rows:
        links_sec.update(columns=cols, rows=rows, row_status=rst, cell_status=[[""] * 5 + [s] for s in rst])
    elif pairs:
        links_sec["text"] = "Every link replicates on time. %s" % nums
    else:
        links_sec["text"] = "No replication link was read. %s" % nums
    if status in ("critical", "warning"):
        links_sec["status"] = status

    # each site's worst inbound age now, and its trend
    now_site = {}
    st_site = {}
    for s in sites:
        ps = [p for p in pairs.values() if (site_of.get(p["to"]) or NO_SITE) == s]
        ages = [p["age"] for p in ps if p["age"] is not None]
        now_site[s] = round(max(ages), 2) if ages else None
        st_site[s] = min([p["st"] for p in ps], key=lambda x: RANK.get(x, 9)) if ps else "unknown"
    t_rows, t_rst, sparks, t_lines, by_day = [], [], [], [], {}
    for s in sites:
        pts = _series(s, runs, now_e, now_site[s], days)
        sp = _spark(pts, now_e, days, oo["warn"], s)
        week = [v for t, v in pts if now_e - 8 * 86400 <= t <= now_e - 6 * 86400]
        then = ("%s → %s" % (_ad.age_text(week[0]), _ad.age_text(now_site[s]))) if week and now_site[s] is not None else ""
        since = then or ("%d day(s) of history: no line yet" % len(pts) if len(pts) < 2 else "")
        t_rows.append([s, sp.get("label", ""), age_short(now_site[s]) if now_site[s] is not None else "-", since, WORD.get(st_site[s], "")])
        t_rst.append(st_site[s] if st_site[s] != "ok" else "ok")
        sparks.append(sp)
        t_lines.append("%s: worst now %s%s." % (s, _ad.age_text(now_site[s]) if now_site[s] is not None else "not read",
                                                 (", a week ago %s" % _ad.age_text(week[0])) if week else ""))
        for t, v in pts:
            by_day.setdefault(s, {})[time.strftime("%Y-%m-%d", time.gmtime(t))] = round(v, 2)
    hist_why = str(o.get("history_why") or "")
    t_text = ("The worst inbound replication age of each site, by day, over %d days: this template's earlier runs (AAP job "
              "history), then this run. The dashed line is %s (ad_repl_warn_hours): a flat line under it is healthy." % (days, _ad.age_text(oo["warn"])))
    if hist_why:
        t_text += " No history: %s." % hist_why
    trend_sec = {"title": "Replication trend: worst age per site", "text": t_text,
                 "columns": ["Site", "Worst age, %d days" % days, "Now", "Since last week", "Status"], "kinds": ["", "spark", "", "", ""],
                 "rows": t_rows, "row_status": [x if x in ("critical", "warning", "low", "unknown") else "" for x in t_rst],
                 "cell_status": [[""] * 4 + [x] for x in t_rst], "sparks": sparks}

    # the matrix and its callouts (web)
    cards = []
    if late:
        w = late[0]
        back = pairs.get((w["to"], w["frm"]))
        both = back is not None and back["st"] != "ok"
        nxt, code = _next_step(w)
        cards.append({"status": w["st"], "chip": WORD[w["st"]], "meta": "Worst pair",
                      "title": "%s %s %s: %s" % (w["frm"], "↔" if both else "→", w["to"], age_short(w["age"])),
                      "subject": "%s → %s, %s%s%s" % (site_of.get(w["frm"]) or NO_SITE, site_of.get(w["to"]) or NO_SITE, w["nc"],
                                                          (", last success " + w["ls_local"]) if w["ls_local"] else ", never replicated",
                                                          (", error " + _err(w["error"])) if w["error"] else ""),
                      "next": nxt, "code": code})
        rest = [p for p in late[1:] if not (both and p["frm"] == w["to"] and p["to"] == w["frm"])]
        if rest:
            a = rest[0]
            cards.append({"status": a["st"], "chip": WORD[a["st"]], "meta": "Also late (%d)" % len(rest),
                          "title": "%s → %s: %s" % (a["frm"], a["to"], age_short(a["age"])),
                          "subject": (_err(a["error"]) or a["why"]), "next": _next_step(a)[0]})
    if pairs:
        cards.append({"status": "ok" if count["ok"] == len(pairs) else "info", "chip": "On time", "meta": "Everything else",
                      "title": "%d of %d links on time" % (count["ok"], len(pairs)), "subject": "",
                      "next": "Under %s without a success: OK. Lows: %d." % (_ad.age_text(oo["low"] or oo["warn"]), count["low"])})
    mx = _matrix(dcs, pairs, oo, site_of)
    m_text = ("Each cell: how long ago the row's DC last replicated to the column's DC (its worst partition). Grouped by AD "
              "site. Hover a cell for the partition, last success, last failure and error.")
    if mx.get("note"):
        m_text += " " + mx["note"]
    matrix_sec = {"title": "Replication matrix", "text": m_text, "cards": cards, "matrix": mx}
    if status in ("critical", "warning"):
        matrix_sec["status"] = status

    email_links = dict(links_sec, lines=t_lines + (["Trend: " + hist_why] if hist_why else []))

    # the workbook: every link (each partition), and each site's worst by day
    sheet_rows = []
    for ln in sorted(r.get("links") or [], key=lambda x: (x["frm"], x["to"], str(x.get("nc")))):
        age = _ad.age_hours(ln.get("last_success"), now)
        st, why = link_status(age, int(ln.get("failures") or 0), oo)
        sheet_rows.append([ln["frm"], site_of.get(ln["frm"]) or "", ln["to"], site_of.get(ln["to"]) or "", _ad.nc_name(ln.get("nc")),
                           _ad._tm.local(_ad.time_text(ln.get("last_success")), tz) if ln.get("last_success") else "never",
                           round(age, 2) if age is not None else "", int(ln.get("failures") or 0),
                           _ad._tm.local(_ad.time_text(ln.get("last_failure")), tz) if ln.get("last_failure") else "",
                           _err(ln.get("error")), WORD[st], why])
    dates = sorted(set(d for v in by_day.values() for d in v))
    sheets = [{"name": "Replication", "columns": ["From", "From site", "To", "To site", "Partition", "Last success", "Hours since",
                                                  "Failures", "Last failure", "Last error", "Status", "Why"], "rows": sheet_rows},
              {"name": "Replication by site", "columns": ["Site"] + dates,
               "rows": [[s] + [by_day.get(s, {}).get(d, "") for d in dates] for s in sites]}]

    lines = [nums] + ["%s → %s: %s (%s)" % (p["frm"], p["to"], WORD[p["st"]], p["why"]) for p in late[:10]] + t_lines
    return {"sections": [email_links], "web_sections": [matrix_sec, links_sec, trend_sec], "sheets": sheets,
            "artifact": {"read_at": r.get("now"), "sites": now_site, "status": st_site},
            "lines": lines, "status": status, "counts": dict(count, links=len(pairs), dcs=len(dcs), read=len(read))}


def ad_repl_insert(report, sections, before="Inbound replication"):
    """The report with sections put before the one titled before (else at the end)."""
    rep = dict(report or {})
    secs = list(rep.get("sections") or [])
    at = next((i for i, s in enumerate(secs) if isinstance(s, dict) and s.get("title") == before), len(secs))
    rep["sections"] = secs[:at] + list(sections or []) + secs[at:]
    return rep


class FilterModule(object):
    def filters(self):
        return {"ad_repl_view": ad_repl_view, "ad_repl_insert": ad_repl_insert}
