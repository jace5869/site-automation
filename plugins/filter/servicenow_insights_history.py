# -*- coding: utf-8 -*-
"""Ticket insights' history (1.13.0, docs/REPORT_DATABASE.md): the figures of one run as rows for the report database -
one run row, one row per org node (every depth, All, and the split's halves) and its figures as metric / value pairs -
and the 12-month trend drawn from the rows read back. FIGURES ONLY: counts, percentages, hours. No caller, no short
description, no close note, no person; the names kept are the org nodes' (the assignment groups and their levels) and
the hot CIs'. The SQL is the module's (roles/servicenow_insights/library/site_insights_db.py)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import re

SCHEMA_VERSION = 1
# the node row's typed columns (the long table has them too, so a query needs only one of them)
NODE_COLUMNS = ("incidents", "requests", "resolved", "backlog", "sla_met_pct", "median_resolve_h")
# what the page's trend draws, in this order: (metric, label, kind) - kind pct (one chart) or count (the other)
TREND = (("sla_met_pct", "SLO met", "pct"), ("response_met_pct", "Response met", "pct"), ("fcr_pct", "First contact", "pct"),
         ("incidents", "Incidents", "count"), ("backlog", "Open at the end", "count"),
         ("median_resolve_h", "Median time to resolve (h)", "hours"), ("reopen_pct", "Reopened", "pct"))
TREND_METRICS = [t[0] for t in TREND]
MONTHS = 12


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _first(v):
    return v[0] if isinstance(v, (list, tuple)) and v else None


def _get(d, *path):
    for p in path:
        if isinstance(d, dict):
            d = d.get(p)
        elif isinstance(d, (list, tuple)) and isinstance(p, int) and -len(d) <= p < len(d):
            d = d[p]
        else:
            return None
    return d


def scope_figures(s):
    """One scope's figures: [(metric, label, value)] - label '' for a single figure, else the bucket, outcome,
    category or CI it counts. Only numbers that were worked out; None (not measured) is left out."""
    s = s or {}
    out = []

    def put(metric, value, label=""):
        v = _num(value)
        if v is not None:
            out.append((metric, str(label)[:190], v))

    c = s.get("counts") or {}
    for k in ("incidents", "requests", "changes", "problems", "resolved"):
        put(k, c.get(k))
    put("backlog", _first(s.get("backlog")))
    age = s.get("age") or []
    put("open_old", age[3] if len(age) > 3 else None)                  # older than servicenow_insights_old_days
    put("sla_met_pct", _first(s.get("sla")))
    put("response_met_pct", _first(s.get("resp")))
    put("median_resolve_h", _first(s.get("median_h")))
    put("reopen_pct", _first(s.get("reopen")))
    r7 = s.get("reopen7") or {}
    if r7.get("measured"):
        put("reopen7_pct", r7.get("pct"))
    put("fcr_pct", _get(s, "fcr", "now"))
    put("fcr_desk_pct", _get(s, "fcr", "desk", 0))
    put("handoff_pct", _get(s, "handoffs", "pct"))
    put("pingpong_pct", _get(s, "pingpong", "pct"))
    put("hops_over_pct", _get(s, "hops", "done_pct"))
    put("hops_open", _get(s, "hops", "open"))
    put("hold_open", _get(s, "hold", "open"))
    put("hold_no_reason", _get(s, "hold", "no_reason"))
    put("at_risk", _get(s, "at_risk", "count"))
    put("breached", _get(s, "at_risk", "breached"))
    if _get(s, "matrix", "on"):
        put("matrix_off_pct", _get(s, "matrix", "pct"))
    put("closure_late", _get(s, "closure", "count"))
    put("changes_failed", _get(s, "changes", "failed"))
    put("changes_backed_out", _get(s, "changes", "backed_out"))
    put("ctasks_overdue", _get(s, "ctasks", "overdue"))
    q = s.get("quality") or []
    for i, k in enumerate(("notes_good_pct", "notes_thin_pct", "notes_bare_pct")):
        put(k, q[i] if len(q) > i else None)
    for k in ("other", "no_ci", "short"):
        put("filed_%s_pct" % k, _get(s, "quality_t", k))
    put("after_hours_pct", _get(s, "after", "pct"))
    sv = s.get("survey") or {}
    if sv.get("n"):
        put("survey_avg", sv.get("avg"))
        put("survey_n", sv.get("n"))
    lc = s.get("lifecycle") or {}
    if lc.get("measured"):
        for k in ("new", "progress", "hold", "resolved"):
            put("state_%s_h" % k, _get(lc, "avg", k))
        put("hold_share_pct", lc.get("hold_share"))
    for name, v in sorted((_get(s, "outcomes", "now") or {}).items()):
        put("outcome", v, name)
    ag = s.get("aging") or {}
    for b, v in zip(ag.get("buckets") or [], ag.get("total") or []):
        put("aging_bdays", v, b)
    for row in (_get(s, "categories", "rows") or [])[:20]:
        put("category", row.get("count"), row.get("name"))
    for row in (_get(s, "hotspots", "cis") or [])[:10]:
        put("hot_ci", row.get("count"), row.get("ci"))
    return out


def period_of(window):
    """The run's period, part of its key: 6m (servicenow_insights_months) or 30d (servicenow_insights_days)."""
    w = window or {}
    if w.get("months"):
        return "%dm" % int(w["months"])
    return "%dd" % int(round(float(w.get("days") or 0)))


def source_of(url):
    """The ServiceNow instance, part of the run's key: its host name (https://sn.example.mil -> sn.example.mil)."""
    m = re.match(r"^(?:[a-z]+://)?([^/:?#]+)", str(url or "").strip(), re.I)
    return (m.group(1).lower() if m else "") or "servicenow"


def _rows_of(model, run):
    """One run's node and figure rows from a model: every scope (All, each site, unit and team, any depth) and the
    split's halves (variant in / out)."""
    m = model or {}
    nodes, figures = [], []
    halves = [("", m.get("scopes") or {})] + sorted((m.get("variants") or {}).items())
    for variant, scopes in halves:
        for key in sorted(scopes):
            s = scopes[key] or {}
            figs = scope_figures(s)
            one = dict((k, v) for k, lab, v in figs if not lab)
            nodes.append([key, variant, str(s.get("label") or s.get("name") or key)[:190], s.get("level") or "",
                          str(s.get("parent") or ""), len(s.get("path") or [])] + [one.get(k) for k in NODE_COLUMNS])
            figures.extend([key, variant, k, lab, v] for k, lab, v in figs)
    return {"run": run, "nodes": nodes, "figures": figures}


def servicenow_insights_db_rows(model, opts=None):
    """The window's own run: {'run': {source, period, window_start, window_end, month, partial, schema}, 'nodes': [[node,
    variant, label, level, parent, depth (0 = All), incidents, requests, resolved, backlog, sla_met_pct,
    median_resolve_h]], 'figures': [[node, variant, metric, label, value]]}."""
    m = model or {}
    o = opts or {}
    w = m.get("window") or {}
    run = {"source": str(o.get("db_source") or "").strip() or source_of(m.get("url")), "period": period_of(w),
           "window_start": w.get("since") or "", "window_end": w.get("until") or "", "month": month_of(w.get("until_local") or w.get("until")),
           "partial": False, "schema": SCHEMA_VERSION}
    return _rows_of(m, run)


# ---- calendar months (the trend's points): each complete month inside the window, and this month so far -------------

_MODEL = []


def _model_module():
    """servicenow_insights_filters (the model), loaded from beside this file - filter plugins are not a package."""
    if not _MODEL:
        import importlib.util
        import os
        here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "servicenow_insights_filters.py")
        spec = importlib.util.spec_from_file_location("servicenow_insights_filters_for_history", here)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MODEL.append(mod)
    return _MODEL[0]


def month_windows(window):
    """The calendar months of a window, local time (report_time_zone): [(month, a, b, partial)] with a and b aware UTC
    datetimes - each month wholly inside it, and the window's last month when it ends inside it (this month so far,
    partial). A month cut at the window's start is left out: its first days were not read as a whole."""
    F = _model_module()
    w = window or {}
    tz = F._tz(w.get("time_zone"))
    since, until = F._t(w.get("since")), F._t(w.get("until"))
    if since is None or until is None:
        return []
    out = []
    first = F._month(since.astimezone(tz).date())
    if since.astimezone(tz) > F._midnight(first, tz):
        first = F._month(first, 1)
    d = first
    while True:
        a = F._midnight(d, tz)
        b = F._midnight(F._month(d, 1), tz)
        if a >= until:
            break
        if b <= until:
            out.append(("%04d-%02d" % (d.year, d.month), a.astimezone(F.UTC), b.astimezone(F.UTC), False))
        else:
            out.append(("%04d-%02d" % (d.year, d.month), a.astimezone(F.UTC), until, True))
            break
        d = F._month(d, 1)
    return out


def _month_r(r, a, b, partial, margin_days):
    """The read (site_servicenow_insights' result) as if it were one month's: the window [a, b), nothing to compare, and
    only the records that touch it (opened before it ends, not done before it starts less margin_days - what the
    repeats and reopen checks look back at)."""
    F = _model_module()
    w = dict((r or {}).get("window") or {})
    tz = F._tz(w.get("time_zone"))
    cut = a - datetime.timedelta(days=margin_days)
    items = []
    for it in (r or {}).get("items") or []:
        op = F._t(it.get("opened_at"))
        if op is not None and op >= b:
            continue
        ends = [F._t(it.get(k)) for k in ("done_at", "closed_at")]
        ends = [x for x in ends if x is not None]
        if ends and max(ends) < cut:
            continue
        items.append(it)
    la, lb = a.astimezone(tz), b.astimezone(tz)
    w.update({"since": a.strftime("%Y-%m-%d %H:%M:%S"), "until": b.strftime("%Y-%m-%d %H:%M:%S"), "prev_since": a.strftime("%Y-%m-%d %H:%M:%S"),
              "compare": False, "since_local": la.strftime("%Y-%m-%d %H:%M"), "until_local": lb.strftime("%Y-%m-%d %H:%M"),
              "days": round((b - a).total_seconds() / 86400.0, 1), "months": 0 if partial else 1, "prev_label": "",
              "label": "%s to %s" % (la.strftime("%Y-%m-%d"), (lb - datetime.timedelta(seconds=1)).strftime("%Y-%m-%d"))})
    return dict(r, window=w, items=items)


def _margin(opts):
    o = opts or {}
    return max(31.0, float(o.get("repeat_days") or 14), float(o.get("close_after_days") or 7), float(o.get("problem_days") or 0),
               float(o.get("repeat_ci_days") or 7))


def month_run(r, month, opts=None, mask=None):
    """One calendar month's run (period month): its figures worked out by the same model as the report, over that month.
    month: one of month_windows' entries, or 'YYYY-MM'."""
    F = _model_module()
    o = dict(opts or {}, act=False)
    if not isinstance(month, (list, tuple)):
        hit = [x for x in month_windows((r or {}).get("window")) if x[0] == str(month)]
        if not hit:
            raise ValueError("%s is not a month of the window read" % month)
        month = hit[0]
    mo, a, b, partial = month
    rm = _month_r(r, a, b, partial, _margin(o))
    model = F.servicenow_insights_model(rm, None, mask, o)
    mb = F._midnight(F._month(a.astimezone(F._tz(rm["window"].get("time_zone"))).date(), 1), F._tz(rm["window"].get("time_zone")))
    run = {"source": str(o.get("db_source") or "").strip() or source_of((r or {}).get("url")), "period": "month",
           "window_start": a.strftime("%Y-%m-%d %H:%M:%S"), "window_end": mb.astimezone(F.UTC).strftime("%Y-%m-%d %H:%M:%S"),
           "month": mo, "partial": bool(partial), "data_end": b.strftime("%Y-%m-%d %H:%M:%S"), "schema": SCHEMA_VERSION}
    out = _rows_of(model, run)
    c = (model.get("scopes") or {}).get("all", {}).get("counts") or {}
    out["tickets"] = {"incidents": c.get("incidents") or 0, "requests": c.get("requests") or 0, "items": len(rm["items"])}
    return out


def servicenow_insights_db_runs(r, model, opts=None, mask=None):
    """Everything one run writes: {'runs': [the window's own run, then each calendar month of the window (the month so far
    partial)]}. A month is keyed by its month and node, so the newest run replaces it - a weekly, a monthly or a
    3-month window fill the same points, never counted twice."""
    runs = [servicenow_insights_db_rows(model, opts)]
    for mw in month_windows((r or {}).get("window")):
        runs.append(month_run(r, mw, opts, mask))
    return {"runs": runs}


def servicenow_insights_backfill_months(r):
    """The complete calendar months a backfill read holds, oldest first ('YYYY-MM')."""
    return [mo for mo, _a, _b, partial in month_windows((r or {}).get("window")) if not partial]


def _parse(ts):
    if isinstance(ts, datetime.datetime):
        return ts
    t = str(ts or "").strip().replace("T", " ")
    for fmt, n in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)):
        try:
            return datetime.datetime.strptime(t[:n], fmt)
        except ValueError:
            pass
    return None


def month_of(window_end):
    """The month a window's figures stand for: the month of its last day, local time (a window to 2026-11-01 00:00 is
    October's)."""
    t = _parse(window_end)
    if t is None:
        return ""
    return (t - datetime.timedelta(seconds=1)).strftime("%Y-%m")


def _months_back(last, n):
    y, mo = int(last[:4]), int(last[5:7])
    out = []
    for _ in range(n):
        out.append("%04d-%02d" % (y, mo))
        mo -= 1
        if mo == 0:
            y, mo = y - 1, 12
    return out[::-1]


def servicenow_insights_trend(model, history=None, opts=None, runs=None):
    """The model with its trend (model.trend): for each node and half, a point per calendar month - the months the
    database holds and the ones this run worked out (runs: servicenow_insights_db_runs; this run's win), the last
    trend_months (12) up to this run's last month. history: the module's read ({'ok', 'rows': [[month, window_end, node,
    variant, metric, value, partial]], 'error', 'months_kept'}), or None (the database is off: no trend). The page draws
    it (servicenow_insights_page, section Trends): the month so far (partial) dashed."""
    m = dict(model or {})
    if history is None:
        return m
    h = history or {}
    mine = [x for x in (runs or {}).get("runs") or [] if (x.get("run") or {}).get("period") == "month"]
    last = max([x["run"]["month"] for x in mine] + [str(row[0]) for row in h.get("rows") or [] if row] or [""])
    months = _months_back(last, int((opts or {}).get("trend_months") or MONTHS)) if last else []
    per, partial = {}, set()     # (variant, node) -> month -> {metric: value}; the months that are partial
    have_mine = set(x["run"]["month"] for x in mine)
    for row in h.get("rows") or []:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        mo, _we, node, variant, metric, value = row[:6]
        if mo in months and mo not in have_mine and metric in TREND_METRICS:
            per.setdefault((variant or "", node), {}).setdefault(mo, {})[metric] = _num(value)
            if len(row) > 6 and row[6]:
                partial.add(mo)
    for x in mine:
        mo = x["run"]["month"]
        if mo not in months:
            continue
        if x["run"].get("partial"):
            partial.add(mo)
        for node, variant, metric, lab, value in x["figures"]:
            if metric in TREND_METRICS and not lab:
                per.setdefault((variant, node), {}).setdefault(mo, {})[metric] = value
    # the months shown: from the first that has a figure (a short history starts where it starts)
    used = [mo for mo in months if any(mo in slot for slot in per.values())]
    shown = months[months.index(used[0]):] if used else []
    nodes = {}
    for (variant, node), slot in per.items():
        nodes[(variant + "|" if variant else "") + node] = [[mo] + [slot[mo].get(k) if mo in slot else None for k in TREND_METRICS]
                                                             for mo in shown]
    m["trend"] = {"on": True, "ok": bool(h.get("ok")), "error": str(h.get("error") or ""), "months": shown,
                  "partial": sorted(partial & set(shown)), "metrics": [list(t) for t in TREND], "nodes": nodes,
                  "kept": int(h.get("months_kept") or 0), "full": int((opts or {}).get("trend_months") or MONTHS)}
    return m


class FilterModule(object):
    def filters(self):
        return {"servicenow_insights_db_rows": servicenow_insights_db_rows, "servicenow_insights_db_runs": servicenow_insights_db_runs,
                "servicenow_insights_month_run": month_run, "servicenow_insights_backfill_months": servicenow_insights_backfill_months,
                "servicenow_insights_trend": servicenow_insights_trend}
