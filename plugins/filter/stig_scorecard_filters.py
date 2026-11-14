# -*- coding: utf-8 -*-
"""The STIG compliance scorecard (1.58.0): a part of the "STIG Manager - checklist import" report
(playbooks/stigman_ckl_import.yml with stigman_ckl_import_mode "import and scorecard" or "scorecard"). Each run reads every
collection's open findings once (roles/stigman_ckl_import/library/site_stig_scorecard.py) and keeps a compact point
(stig_scorecard_point: the job's artifact stig_scorecard, and a row per collection in the report database). The change
and the trend are this run's point with the earlier runs' points - from AAP's job history and from the report database
(stig_scorecard_runs: one point per run). stig_scorecard: the scorecard's tiles, cards and tables, the website's version
with a sparkline per collection, the workbook's sheets and the lines for the job's log. stig_scorecard_join: the
scorecard first, then the checklist import's report as it is.

Only what was read. A finding is a rule open on an asset. "Since last week" compares this run with the run of 6 to 8
days ago, rule by rule: closed is the hosts a rule is open on fewer, opened the hosts it is open on more. A rule closed
on one host and opened on another in the same week counts as neither. Fewer than 2 weekly points: no trend line."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import re

DAY = 86400.0
HOUR = 3600.0
HALF = 12 * HOUR                # a week's window ends half a day after "N weeks ago, to the minute": a run a little late counts
SPARK_W, SPARK_H, SPARK_PAD = 110, 30, 4
CATS = (1, 2, 3)
CAT_WORD = {1: "CAT I", 2: "CAT II", 3: "CAT III"}
TIP_IDS = 10                    # a hover lists this many rule ids, then "N more"
XLSX_CELL = 30000               # a workbook cell holds 32767 characters
WORST = {"critical": 4, "warning": 3, "low": 2, "ok": 1, "info": 0, "": 0}


# ---- small helpers --------------------------------------------------------------------------------
def parse_time(v):
    """'2026-11-13T06:00:05Z' (or with an offset, or seconds since 1970) -> seconds since 1970, or None."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) or re.match(r"^\d+(\.\d+)?$", str(v)):
        return float(v)
    s = str(v).strip().replace(" ", "T").replace("Z", "+00:00")
    s = re.sub(r"\.\d+", "", s)
    s = re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", s)
    try:
        t = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)).timestamp()


def _utc(t):
    return datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc)


def day_word(t):
    """6 Nov."""
    d = _utc(t)
    return "%d %s" % (d.day, d.strftime("%b"))


def _n(v):
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def _count(n, word, many=None):
    """1 host, 2 hosts."""
    return "%d %s" % (n, word if n == 1 else (many or word + "s"))


def _open(c):
    return _n(c.get("cat1")) + _n(c.get("cat2")) + _n(c.get("cat3"))


def _rules(c):
    """A point's collection -> {SV-number: (category, hosts open on)}."""
    out = {}
    for cat in CATS:
        d = c.get("r%d" % cat)
        for sv, n in (d.items() if isinstance(d, dict) else []):
            if _n(n):
                out[str(sv)] = (cat, _n(n) + out.get(str(sv), (cat, 0))[1])
    return out


def _short(name):
    return str(name or "").strip().lower().split(".")[0]


# ---- the point a run keeps, and the runs ------------------------------------------------------------------
def stig_scorecard_point(read):
    """What site_stig_scorecard read -> the point this run keeps (the artifact stig_scorecard, the report database's
    rows): {v: 1, read_at, collections: [{id, name, assets, cat1, cat2, cat3, r1: {SV-number: hosts}, r2, r3}]} - the
    collections that were read; one that could not be read has no point this run."""
    cols = []
    for c in (read or {}).get("collections") or []:
        if not isinstance(c, dict) or c.get("error"):
            continue
        rec = {"id": str(c.get("id") or ""), "name": str(c.get("name") or ""), "assets": _n(c.get("assets")),
               "cat1": _n(c.get("cat1")), "cat2": _n(c.get("cat2")), "cat3": _n(c.get("cat3")), "r1": {}, "r2": {}, "r3": {}}
        for r in c.get("rules") or []:
            if isinstance(r, dict) and r.get("sv") and r.get("cat") in CATS and _n(r.get("count")):
                d = rec["r%d" % r["cat"]]
                d[str(r["sv"])] = d.get(str(r["sv"]), 0) + _n(r["count"])
        cols.append(rec)
    return {"v": 1, "read_at": str((read or {}).get("read_at") or ""), "collections": cols}


def stig_scorecard_runs(current, aap=None, db=None, opts=None):
    """This run's point with the earlier runs' - from AAP's job history (site_stig_scorecard_history) and from the report
    database (site_stig_scorecard_db) - as one list, oldest first: one point per run. A run both hold (the same job, or
    the same minute read) counts once: AAP's first, then the database's. Only the window's (stigman_scorecard_weeks),
    never one after this run. -> {runs: [...], counts: {aap, db, both, used}}"""
    o = opts or {}
    now = parse_time((current or {}).get("read_at")) or 0
    since = now - (max(2, _n(o.get("weeks", 8)) or 8) * 7 + 1) * DAY
    jobs, minutes, out = {}, {}, []                     # a job ID, a minute -> where its run came from
    counts = {"aap": 0, "db": 0, "both": 0}

    def take(run, src):
        t = parse_time((run or {}).get("read_at")) if isinstance(run, dict) else None
        if t is None or not isinstance(run.get("collections"), list):
            return
        job = str(run.get("job") or "")
        job = "" if job == "0" else job
        first = jobs.get(job) if job and job in jobs else minutes.get(int(t // 60))
        if t > now or t < since:
            return
        if first is not None:                           # held already: "both" is a run AAP and the database each gave
            if src == "db" and first == "aap":
                counts["both"] += 1
            return
        if src:
            counts[src] += 1
        if job:
            jobs[job] = src
        minutes[int(t // 60)] = src
        out.append(dict(run, _t=t))

    if current:
        take(dict(current, job=str((o.get("job") or "")) or current.get("job") or ""), "")
    for run in aap or []:
        take(run, "aap")
    for run in db or []:
        take(run, "db")
    out.sort(key=lambda r: r["_t"])
    return {"runs": out, "counts": dict(counts, used=len(out))}


# ---- the weeks, the change since last week ----------------------------------------------------------------
def weekly(runs, now, weeks):
    """One point a week: the last run of each of the weeks ending now (a week's window ends half a day after "N weeks
    ago", so a run a little late still counts for its week). -> [(the week's last day, run or None)], oldest first, from
    the first week that has a run."""
    out = []
    for k in range(max(1, weeks) - 1, -1, -1):
        end = now - k * 7 * DAY + (HALF if k else 0)
        inside = [r for r in runs if now - (k + 1) * 7 * DAY + HALF < r["_t"] <= end]
        out.append((now - k * 7 * DAY, inside[-1] if inside else None))
    while len(out) > 1 and out[0][1] is None:
        out.pop(0)
    return out


def baseline(runs, now):
    """The run "last week" is compared with: the one nearest 7 days before this run, 6 to 8 days ago. None: no such run."""
    near = [r for r in runs if 6 * DAY - HOUR <= now - r["_t"] <= 8 * DAY + HOUR]
    return min(near, key=lambda r: abs(now - r["_t"] - 7 * DAY)) if near else None


def _find(run, c):
    """A collection in another run's point: by its id, else by its name."""
    cols = [x for x in (run or {}).get("collections") or [] if isinstance(x, dict)]
    for x in cols:
        if str(x.get("id")) == str(c.get("id")):
            return x
    for x in cols:
        if str(x.get("name", "")).lower() == str(c.get("name", "")).lower():
            return x
    return None


def change(before, now):
    """Two points of one collection -> {closed, opened, closed_rules: [(SV, category, hosts fewer)], opened_rules}: rule
    by rule, the hosts it is open on fewer (closed) and more (opened), the largest first."""
    a, b = _rules(before or {}), _rules(now or {})
    closed, opened = [], []
    for sv in set(a) | set(b):
        was, cat = a.get(sv, (0, 0))[1], (b.get(sv) or a.get(sv))[0]
        n = b.get(sv, (0, 0))[1]
        if n < was:
            closed.append((sv, cat, was - n))
        elif n > was:
            opened.append((sv, cat, n - was))
    closed.sort(key=lambda x: (-x[2], x[1], x[0]))
    opened.sort(key=lambda x: (-x[2], x[1], x[0]))
    return {"closed": sum(x[2] for x in closed), "opened": sum(x[2] for x in opened), "closed_rules": closed, "opened_rules": opened}


def _ids(rules, word):
    """The hover's lines: 'Closed 19:', a rule and its hosts each, the first TIP_IDS and 'N more'."""
    if not rules:
        return ["%s: none" % word]
    out = ["%s %d:" % (word, sum(x[2] for x in rules))]
    out += ["  %s on %s" % (sv, _count(n, "host")) for sv, cat, n in rules[:TIP_IDS]]
    if len(rules) > TIP_IDS:
        out.append("  %d more (the workbook lists every rule)" % (len(rules) - TIP_IDS))
    return out


def spark(points, heads, name):
    """[(column, open, cat1, cat2, cat3)] -> the sparkline's geometry (the atlas and brief looks draw it as an inline SVG):
    {w, h, points ('x,y x,y'), last [x, y], lines, mark, label (the values in words: aria-label and title), data_points
    (per point: x, y, label - the week - and text; the hover's data)}. x by week, so a week without a run is a gap."""
    if len(points) < 2:
        return {}
    vals = [p[1] for p in points]
    lo, hi = min(vals), max(vals)
    if hi == lo:
        lo, hi = lo - 1.0, hi + 1.0
    inner_w, inner_h = SPARK_W - 2.0 * SPARK_PAD, SPARK_H - 2.0 * SPARK_PAD
    span = float(max(1, len(heads) - 1))
    pts, data = [], []
    for i, (col, v, a, b, c) in enumerate(points):
        x = round(SPARK_PAD + col / span * inner_w, 1)
        y = round(SPARK_PAD + (1.0 - (v - lo) / float(hi - lo)) * inner_h, 1)
        pts.append((x, y))
        text = "%d open: CAT I %d, CAT II %d, CAT III %d" % (v, a, b, c)
        if i:
            ch = v - points[i - 1][1]
            text += "; %s since the point before" % ("no change" if not ch else "%+d" % ch)
        data.append({"x": x, "y": y, "label": "Week to %s" % heads[col], "text": text})
    label = "%s, open findings: %d in the week to %s, %d now; by week: %s" % (
        name, vals[0], heads[points[0][0]], vals[-1], ", ".join(str(v) for v in vals))
    return {"w": SPARK_W, "h": SPARK_H, "points": " ".join("%s,%s" % q for q in pts), "last": list(pts[-1]), "lines": [],
            "mark": None, "label": label, "name": name, "data_points": data}


# ---- fixed on the host, still Open ---------------------------------------------------------------------
def fixed_rows(fixed, read):
    """What the STIG drift reports' newest jobs found fixed on the host and Open in STIG Manager (site_stig_scorecard_history:
    rows [host, collection, asset, SV-number, category, title, scanned]) -> those still Open now, in a collection this run
    read: by collection (its name), rule and asset (its name, or its short name)."""
    now = {}
    for c in (read or {}).get("collections") or []:
        if isinstance(c, dict) and not c.get("error"):
            now[str(c.get("name", "")).lower()] = dict((str(r.get("sv")), set(_short(a) for a in r.get("assets") or []))
                                                       for r in c.get("rules") or [] if isinstance(r, dict))
    out = []
    for row in (fixed or {}).get("rows") or []:
        if not isinstance(row, (list, tuple)) or len(row) < 7:
            continue
        host, col, asset, sv = [str(x or "") for x in row[:4]]
        still = now.get(col.lower(), {}).get(sv)
        if still is None or not ({_short(asset), _short(host)} & still):
            continue
        out.append([host, col, sv, str(row[4] or ""), " ".join(str(row[5] or "").split()), str(row[6] or "")])
    out.sort(key=lambda r: (r[1].lower(), r[0].lower(), r[2]))
    return out


# ---- the scorecard ---------------------------------------------------------------------------------------
def stig_scorecard(read, point, runs, opts=None):
    """The scorecard. read: site_stig_scorecard's result; point: stig_scorecard_point's; runs: stig_scorecard_runs' runs
    (this run's among them). opts: weeks (stigman_scorecard_weeks), top (stigman_scorecard_top), cat2_warn
    (stigman_scorecard_cat2_warn), fixed (site_stig_scorecard_history's), history (lines: where the earlier points came
    from), api (STIG Manager's address), list_max. -> {ok, error, title, status, subtitle, tiles, sections (the email's and
    the other looks'), web (the same with the sparkline column), notes (the last section: how it is counted), xlsx, lines,
    counts}"""
    o = opts or {}
    read = read or {}
    n_weeks = max(2, _n(o.get("weeks", 8)) or 8)
    top_n = max(1, _n(o.get("top", 5)) or 5)
    cat2_warn = _n(o.get("cat2_warn"))
    list_max = max(1, _n(o.get("list_max", 200)) or 200)
    host = re.sub(r"^https?://([^/:]+).*$", r"\1", str(o.get("api") or ""))
    if not read.get("ok"):
        why = str(read.get("error") or "STIG Manager was not read")
        return {"ok": False, "error": why, "title": "STIG compliance: not read - %s" % why[:120], "status": "critical", "subtitle": "",
                "tiles": [], "sections": [{"title": "STIG compliance scorecard: not read", "status": "critical", "lines": [
                    why, "Nothing was written. The same \"STIG Manager API\" credential as the checklist import is used, read-only."]}],
                "web": [{"title": "STIG compliance scorecard: not read", "status": "critical", "lines": [why]}], "notes": None, "xlsx": [], "lines": ["STIG compliance: not read - %s" % why], "counts": {}}

    now = parse_time((point or {}).get("read_at")) or parse_time(read.get("read_at")) or 0
    runs = sorted([dict(r, _t=r.get("_t") or parse_time(r.get("read_at")) or 0) for r in runs or [] if isinstance(r, dict)],
                  key=lambda r: r["_t"])
    runs = [r for r in runs if r["_t"] <= now]
    base = baseline(runs, now)
    wk = weekly(runs, now, n_weeks)
    heads = [day_word(t) for t, r in wk]
    n_points = len([1 for t, r in wk if r is not None])
    trend = n_points >= 2
    cols = [c for c in read.get("collections") or [] if isinstance(c, dict)]
    good = [c for c in cols if not c.get("error")]
    bad = [c for c in cols if c.get("error")]
    by_id = dict((str(c.get("id")), c) for c in (point or {}).get("collections") or [] if isinstance(c, dict))

    # ---- by collection
    rows, web_rows, rst, cst, sparks, tips, numbers, deltas, sheet_delta = [], [], [], [], [], [], [], [], []
    week_open = []                                   # the workbook's line per row: open findings each week, None a week without a run
    tot = {"cat1": 0, "cat2": 0, "cat3": 0, "assets": 0, "closed": 0, "opened": 0}
    for c in good:
        p = by_id.get(str(c.get("id"))) or {}
        for k in ("cat1", "cat2", "cat3", "assets"):
            tot[k] += _n(c.get(k))
        was = _find(base, c) if base else None
        ch = change(was, p) if was is not None else None
        if ch:
            tot["closed"] += ch["closed"]
            tot["opened"] += ch["opened"]
            deltas.append((c["name"], ch))
            for word, key in (("Closed", "closed_rules"), ("Opened", "opened_rules")):
                wr, nr = _rules(was), _rules(p)
                sheet_delta += [[c["name"], sv, CAT_WORD[cat], word, wr.get(sv, (0, 0))[1], nr.get(sv, (0, 0))[1]] for sv, cat, n in ch[key]]
        if ch is None:
            since = "New here" if base else "-"
            tip = ("%s was not in the run of %s." % (c["name"], day_word(base["_t"]))) if base else "No run 6 to 8 days ago to compare with."
        else:
            word, icon = (("Worse", u"▲") if ch["opened"] > ch["closed"] else
                          ("Better", u"▼") if ch["closed"] > ch["opened"] else ("Same", u"▬"))
            since = u"%s %s\n%d closed, %s" % (icon, word, ch["closed"], ("%d opened" % ch["opened"]) if ch["opened"] else "none opened")
            tip = "\n".join(["%s since %s" % (c["name"], day_word(base["_t"]))] + _ids(ch["closed_rules"], "Closed")
                            + _ids(ch["opened_rules"], "Opened"))
        series = []
        for i, (t, r) in enumerate(wk):
            x = p if (r is not None and r["_t"] == now) else (_find(r, c) if r is not None else None)
            if x is not None:
                series.append((i, _open(x), _n(x.get("cat1")), _n(x.get("cat2")), _n(x.get("cat3"))))
        sp = spark(series, heads, c["name"]) if trend else {}
        at = dict((s[0], s[1]) for s in series)
        numbers.append([c["name"]] + [at.get(i, "-") for i in range(len(wk))])
        week_open.append([at.get(i) for i in range(len(wk))] if trend else [])
        worse = bool(ch and ch["opened"] > ch["closed"])
        over2 = bool(cat2_warn and _n(c.get("cat2")) >= cat2_warn)
        st = "critical" if _n(c.get("cat1")) else ("warning" if worse or over2 else "")
        st_word = "CAT I open" if st == "critical" else ("Worse" if worse else ("CAT II over %d" % cat2_warn if over2 else "OK"))
        st_tip = {"CAT I open": "CAT I open: at least one CAT I finding is open in this collection. It stays red until fixed.",
                  "Worse": "Worse: more findings opened than closed since last week.",
                  "OK": "OK: no CAT I open, and no worse than last week."}.get(
                      st_word, "%d or more CAT II findings are open (stigman_scorecard_cat2_warn)." % cat2_warn)
        head = [c["name"], _n(c.get("assets")), _n(c.get("cat1")), _n(c.get("cat2")), _n(c.get("cat3")), since]
        text = (u"%d → %d" % (series[0][1], series[-1][1])) if len(series) > 1 else "from next week"
        rows.append(head + [text, st_word])
        web_rows.append(head + [sp.get("label", "") if sp else "trend starts next week", st_word])
        rst.append(st)
        cst.append(["", "", "critical" if _n(c.get("cat1")) else "", "", "", "", "", ""])
        sparks.append(sp)
        tips.append(["", "", "", "", "", tip, "", st_tip])
    for c in bad:
        line = [c.get("name") or c.get("id"), "-", "-", "-", "-", "-", "-", "Not read"]
        rows.append(line)
        web_rows.append(list(line))
        rst.append("warning")
        cst.append([""] * 8)
        sparks.append({})
        week_open.append([])
        tips.append([""] * 7 + [str(c.get("error") or "")])
    missing = [str(m) for m in read.get("missing") or []]
    open_col = "Open, %d weeks" % len(wk) if trend else "Open"
    columns = ["Collection", "Assets", "CAT I open", "CAT II open", "CAT III open", "Since last week", open_col, "Status"]
    titles = ["", "The assets in the collection", "Rule and asset pairs of severity high", "Severity medium", "Severity low",
              "This run against the run of 6 to 8 days ago, rule by rule",
              "Open findings (CAT I, II and III), one point a week", ""]
    notes_col = (["Not read: %s - %s" % (c.get("name") or c.get("id"), c.get("error")) for c in bad]
                 + (["Not there, or the client cannot read it (stigman_scorecard_collections): %s." % ", ".join(missing)] if missing else [])
                 + ([] if trend else ["The trend starts next week: a line needs two weekly points, and this run is the first."])
                 + ([] if base else ["No run 6 to 8 days ago to compare with: \"Since last week\" starts with the run a week from now."]))
    sec = {"title": "By collection (%d)" % len(cols), "status": _worst_of(rst), "columns": columns, "column_titles": titles,
           "kinds": ["wrap", "num", "num", "num", "num", "wrap", "", ""], "rows": rows, "row_status": rst, "cell_status": cst,
           "tips": tips, "wrap": True, "keep_case": True}
    if notes_col:
        sec["lines"] = notes_col
    web_sec = dict(sec, rows=web_rows)
    if trend:
        web_sec.update(kinds=["wrap", "num", "num", "num", "num", "wrap", "spark", ""], sparks=sparks)
    if not cols:
        sec = {"title": "By collection (0)", "status": "warning",
               "lines": ["The client reads no collection%s. Give it a grant in the collections (docs/CKL_IMPORT.md)."
                         % (" of stigman_scorecard_collections" if missing else "")] + notes_col[len(bad):len(bad) + (1 if missing else 0)]}
        web_sec = dict(sec)

    # ---- the rules open on the most hosts
    allr = {}
    for c in good:
        for r in c.get("rules") or []:
            if not isinstance(r, dict) or not r.get("sv"):
                continue
            a = allr.setdefault(r["sv"], {"sv": r["sv"], "cat": r.get("cat") or 2, "version": r.get("version") or "", "title": r.get("title") or "",
                                          "hosts": 0, "where": [], "names": []})
            a["cat"] = min(a["cat"], r.get("cat") or 2)
            a["hosts"] += _n(r.get("count"))
            a["where"].append((c["name"], _n(r.get("count"))))
            a["names"] += [x for x in r.get("assets") or [] if x not in a["names"]]
    ranked = sorted(allr.values(), key=lambda a: (-a["hosts"], a["cat"], a["sv"]))
    top = ranked[:top_n]

    def rule_word(a):
        return "%s (%s)" % (a["sv"], a["version"]) if a["version"] else a["sv"]

    def host_tip(a):
        return "%s is open on %s\n%s%s\nThe workbook lists every host." % (
            a["sv"], _count(a["hosts"], "host"), ", ".join(a["names"][:12]),
            " and %d more" % (len(a["names"]) - 12) if len(a["names"]) > 12 else "")

    top_sec = {"title": "Top %d rules open across hosts" % top_n if len(ranked) > top_n else "The rules open across hosts (%d)" % len(ranked),
               "status": "critical" if any(a["cat"] == 1 for a in top) else ""}
    if top:
        top_sec.update({"text": "The rules open on the most hosts, in every collection read. Fixing one of these closes the most findings.",
                        "columns": ["Rule", "Category", "What it checks", "Hosts open", "Where"],
                        "kinds": ["wrap", "", "wrap", "num", "wrap"],
                        "rows": [[rule_word(a), CAT_WORD[a["cat"]], a["title"], a["hosts"],
                                  ", ".join("%s (%d)" % w for w in sorted(a["where"], key=lambda w: (-w[1], w[0].lower())))] for a in top],
                        "row_status": ["critical" if a["cat"] == 1 else "" for a in top],
                        "cell_status": [["", "critical" if a["cat"] == 1 else "", "", "", ""] for a in top],
                        "tips": [["", "CAT I: the most severe category. Fix these first." if a["cat"] == 1 else "", "", host_tip(a), ""] for a in top],
                        "wrap": True})
        if not top_sec["status"]:
            top_sec.pop("status")
    else:
        top_sec = {"title": "The rules open across hosts (0)", "status": "ok", "lines": ["No finding is open in the collections read."]}

    # ---- fixed on the host, still Open in STIG Manager (1.56.0's level "low", with the word Low; info when there are none)
    fx = o.get("fixed") if isinstance(o.get("fixed"), dict) else {}
    fixed = fixed_rows(fx, read)
    scans = fx.get("jobs") or []
    fix_text = ("The scan says these pass; STIG Manager still has them Open. Nothing to fix on the hosts - the checklist is "
                "behind. The next checklist import (workflow 40) closes them.")
    fix_sec = {"title": "Fixed on the host, still Open in STIG Manager (%d)" % len(fixed), "status": "info"}
    if fixed:
        fix_sec.update({"status": "low", "text": fix_text, "columns": ["Host", "Collection", "Rule", "Category", "What it checks", "Scan passed", "Status"],
                        "column_titles": ["", "", "", "", "", "The day of the scan that found the rule passing", ""],
                        "kinds": ["wrap", "wrap", "", "", "wrap", "", ""],
                        "rows": [[r[0], r[1], r[2], r[3], r[4], day_word(parse_time(r[5])) if parse_time(r[5]) else r[5], "Low"]
                                 for r in fixed[:list_max]],
                        "cell_status": [["", "", "", "", "", "", "low"]] * min(len(fixed), list_max),
                        "row_status": ["low"] * min(len(fixed), list_max),
                        "tips": [["", "", "%s passes in the scan of %s; STIG Manager still has it Open." % (r[2], r[5]), "", "", "",
                                  "Low: fixed on the host, still Open in STIG Manager. Nothing to do on the host; the next "
                                  "checklist import closes it."] for r in fixed[:list_max]],
                        "wrap": True})
        if len(fixed) > list_max:
            fix_sec["lines"] = ["%d more are in the workbook." % (len(fixed) - list_max)]
    elif scans:
        fix_sec["lines"] = ["None: every rule the last scans (%s, %s) found passing is closed in STIG Manager too."
                            % (_count(len(scans), "job"), _count(sum(_n(j.get("assets")) for j in scans), "asset"))]
    else:
        why = "; ".join(str(w) for w in fx.get("why") or [] if w)
        fix_sec["lines"] = ["No scan results to compare with: no STIG drift report job of the last days kept its results "
                            "(stigman_scorecard_drift_templates, stigman_scorecard_drift_days)%s." % (" - " + why if why else ""),
                            "This part fills once a STIG drift report (workflow 37's check) has run on 1.58.0 or later."]

    # ---- the tiles and the cards
    c1 = tot["cat1"]
    with1 = sorted([c for c in good if _n(c.get("cat1"))], key=lambda c: (-_n(c["cat1"]), c["name"].lower()))
    since_word = "since %s" % day_word(base["_t"]) if base else ""
    tiles = [
        {"label": "CAT I open", "value": c1, "status": "critical" if c1 else "ok",
         "tip": ("CAT I: the most severe category. %s." % ", ".join("%d on %s" % (_n(c["cat1"]), c["name"]) for c in with1[:4]))
         if c1 else "CAT I: the most severe category. None open."},
        {"label": "CAT II open", "value": tot["cat2"], "status": "warning" if tot["cat2"] else "ok",
         "tip": "CAT II: medium severity. %d open across %s." % (tot["cat2"], _count(len(good), "collection"))},
        {"label": "CAT III open", "value": tot["cat3"],
         "tip": "CAT III: low severity. %d open across %s." % (tot["cat3"], _count(len(good), "collection"))},
        {"label": "Closed this week", "value": tot["closed"] if base else "-", "status": "ok" if base and tot["closed"] else "",
         "note": since_word or "no run last week",
         "tip": ("Hosts a rule was Open on on %s and is not now." % day_word(base["_t"])) if base else "No run 6 to 8 days ago to compare with."},
        {"label": "Opened this week", "value": tot["opened"] if base else "-",
         "status": "warning" if base and tot["opened"] > tot["closed"] else "", "note": since_word or "no run last week",
         "tip": ("Hosts a rule was not Open on on %s and is now." % day_word(base["_t"])) if base else "No run 6 to 8 days ago to compare with."},
        {"label": "Fixed on host, still open", "value": len(fixed) if (fixed or scans) else "-", "status": "low" if fixed else ("ok" if scans else ""), "note": "Low",
         "tip": "Low: fixed on the host, still Open in STIG Manager. Nothing to do on the host; the next checklist import closes it."}]
    for t in tiles:
        if not t.get("status"):
            t.pop("status", None)

    cards = []
    if c1:
        first = sorted([a for a in allr.values() if a["cat"] == 1], key=lambda a: (-a["hosts"], a["sv"]))
        cards.append({"status": "critical", "chip": "CAT I first", "meta": _count(len(with1), "collection"),
                      "title": "%d CAT I on %s" % (c1, _count(len(with1), "collection")),
                      "subject": ", ".join("%d on %s" % (_n(c["cat1"]), c["name"]) for c in with1[:4])
                                 + (" and %d more" % (len(with1) - 4) if len(with1) > 4 else ""),
                      "next": ("Start with %s: %s. Open on %s." % (rule_word(first[0]), (first[0]["title"] or "no title").rstrip(". "), _count(first[0]["hosts"], "host")))
                      if first else "The CAT I rows in \"By collection\"."})
    else:
        cards.append({"status": "ok", "chip": "CAT I first", "title": "No CAT I open",
                      "subject": "%s, %s read." % (_count(len(good), "collection"), _count(tot["assets"], "asset"))})
    if base:
        better = sorted([d for d in deltas if d[1]["closed"] > d[1]["opened"]], key=lambda d: d[1]["opened"] - d[1]["closed"])
        worse_l = sorted([d for d in deltas if d[1]["opened"] > d[1]["closed"]], key=lambda d: d[1]["closed"] - d[1]["opened"])
        said = []
        if better:
            said.append("%s improved most (%d closed, %d opened)." % (better[0][0], better[0][1]["closed"], better[0][1]["opened"]))
        if worse_l:
            said.append("%s got worse most (%d opened, %d closed)." % (worse_l[0][0], worse_l[0][1]["opened"], worse_l[0][1]["closed"]))
        cards.append({"status": "warning" if tot["opened"] > tot["closed"] else "ok", "chip": "This week", "meta": since_word,
                      "title": "%d closed, %d opened" % (tot["closed"], tot["opened"]),
                      "subject": " ".join(said) or "No collection changed."})
    else:
        cards.append({"status": "info", "chip": "This week", "title": "No run last week to compare with",
                      "subject": "The change needs a run 6 to 8 days ago. It starts with the run a week from now."})
    if fixed:
        cards.append({"status": "low", "chip": "Paperwork, not hosts", "meta": "Low", "title": "%d fixed, still open" % len(fixed),
                      "subject": "The hosts pass; STIG Manager has not caught up.",
                      "next": "The next checklist import (workflow 40) closes them."})
    else:
        cards.append({"status": "ok" if scans else "info", "chip": "Paperwork, not hosts", "meta": "Low",
                      "title": "Nothing fixed and still open" if scans else "No scan results to compare with",
                      "subject": "STIG Manager agrees with the last scans." if scans else "It fills once a STIG drift report has run."})
    card_sec = {"title": "Start here", "status": cards[0]["status"] if c1 else _worst_of([c["status"] for c in cards[:2]]) or "ok", "cards": cards}

    # ---- the numbers behind the lines
    num_sec = None
    if trend:
        num_sec = {"title": "The numbers behind the lines (open findings per week)",
                   "text": "One point a week: the last run of each week, the weeks ending on this run's weekday. A dash: no run that week.",
                   "columns": ["Collection"] + heads, "kinds": ["wrap"] + ["num"] * len(heads), "rows": numbers, "wrap": True}

    status = "critical" if c1 else ("warning" if "warning" in rst else "ok")
    if base:
        title = "STIG compliance: %d CAT I open; %s closed this week" % (c1, _count(tot["closed"], "finding"))
    else:
        title = "STIG compliance: %d CAT I open; %s open" % (c1, _count(c1 + tot["cat2"] + tot["cat3"], "finding"))
    subtitle = "Read %s UTC%s; %s, %s%s" % (
        _utc(now).strftime("%Y-%m-%d %H:%M"), " from STIG Manager at %s" % host if host else "", _count(len(good), "collection"),
        _count(tot["assets"], "asset"), "; compared with %s" % day_word(base["_t"]) if base else "")
    notes = {"title": "How the scorecard counts", "collapsed": True, "lines": [
        "A finding is a rule Open on an asset, as STIG Manager has it now. Nothing is written to STIG Manager.",
        "Since last week: this run against the run of 6 to 8 days ago, rule by rule. Closed is the hosts a rule is open on fewer, "
        "opened the hosts it is open on more.",
        "A rule closed on one host and opened on another in the same week counts as neither.",
        "A collection that was not in last week's run is \"New here\" and is not in the week's totals.",
        "Fixed on the host, still Open: the STIG drift reports' last scans against this run's Open findings."]
        + ["Compared with the run of %s UTC." % _utc(base["_t"]).strftime("%Y-%m-%d %H:%M") if base else "No run 6 to 8 days ago was found."]
        + [str(x) for x in o.get("history") or [] if x]}

    sections = [card_sec, sec, top_sec, fix_sec] + ([num_sec] if num_sec else [])
    web = [card_sec, web_sec, top_sec, fix_sec] + ([num_sec] if num_sec else [])

    # ---- the workbook
    def cut(names):
        text = ", ".join(names)
        return text if len(text) <= XLSX_CELL else text[:XLSX_CELL].rsplit(", ", 1)[0] + " ... (%d in all)" % len(names)

    xlsx = [{"name": "By collection", "columns": columns[:6] + ["Open now", "Status"],
             "rows": [r[:5] + [r[5].replace("\n", ": ") if isinstance(r[5], str) else r[5]] +
                      [(r[2] + r[3] + r[4]) if isinstance(r[2], int) else "-", r[7]] for r in rows],
             # 1.62.0: the page's line as Excel's own - all open findings a week (CAT I is mostly 0: a flat line says little)
             "sparklines": [{"column": "Trend", "points": week_open}]},
            {"name": "Rules across hosts", "columns": ["Rule", "STIG ID", "Category", "What it checks", "Hosts open", "Where"],
             "rows": [[a["sv"], a["version"], CAT_WORD[a["cat"]], a["title"], a["hosts"],
                       ", ".join("%s (%d)" % w for w in sorted(a["where"], key=lambda w: (-w[1], w[0].lower())))] for a in ranked]},
            {"name": "Open rules by collection", "columns": ["Collection", "Rule", "STIG ID", "Category", "What it checks", "Hosts open", "Open on"],
             "rows": [[c["name"], r.get("sv", ""), r.get("version", ""), CAT_WORD.get(r.get("cat"), ""), r.get("title", ""), _n(r.get("count")),
                       cut(r.get("assets") or [])] for c in good for r in c.get("rules") or [] if isinstance(r, dict)]},
            {"name": "Since last week", "columns": ["Collection", "Rule", "Category", "Change", "Hosts open before", "Hosts open now"],
             "rows": sheet_delta},
            {"name": "Fixed on host, still open", "columns": ["Host", "Collection", "Rule", "Category", "What it checks", "Scan (UTC)"],
             "rows": fixed}]
    if num_sec:
        xlsx.append({"name": "Open per week", "columns": num_sec["columns"], "rows": numbers})

    lines = [title] + ["%s: %s%s" % (t["label"], t["value"], " (%s)" % t["note"] if t.get("note") else "") for t in tiles]
    lines += ["%s: %s" % (r[0], ("CAT I %s, CAT II %s, CAT III %s, %s" % (r[2], r[3], r[4], str(r[5]).replace("\n", ": ")))) for r in rows[:len(good)]]
    lines += notes_col
    lines += [str(x) for x in o.get("history") or [] if x]        # where the earlier points came from, in the job output too
    return {"ok": True, "error": "", "title": title, "status": status, "subtitle": subtitle, "tiles": tiles, "sections": sections, "web": web,
            "notes": notes, "xlsx": xlsx, "lines": lines,
            "counts": dict(tot, collections=len(good), not_read=len(bad), fixed=len(fixed), weekly_points=n_points,
                           compared=bool(base), rules=len(ranked))}


def _worst_of(statuses):
    best = ""
    for s in statuses:
        if WORST.get(s, 0) > WORST.get(best, 0):
            best = s
    return best


# ---- the scorecard and the checklist import, one report --------------------------------------------------
def _sheets(report):
    """The import report's tables as sheets (as report_xlsx_sheets does for a report on its own)."""
    return [{"name": s.get("title") or "Table", "columns": list(s["columns"]), "rows": list(s.get("rows") or [])}
            for s in (report or {}).get("sections") or [] if isinstance(s, dict) and s.get("columns")]


def stig_scorecard_join(card, imp=None, mode="import and scorecard", src="", empty=False):
    """One report of the scorecard (stig_scorecard's result) and the checklist import's (stig_ckl_report's): the
    scorecard's tiles and sections first, then the import's as they are - nothing of it left out. mode "scorecard": the
    scorecard alone. empty: the drop folder had no checklist this run - one line instead of the import's report.
    -> {report, web, xlsx}"""
    card = card or {}
    with_import = mode != "scorecard" and bool(imp) and not empty
    tail = [card["notes"]] if card.get("notes") else []
    status, title, subtitle = card.get("status") or "info", card.get("title") or "STIG compliance", card.get("subtitle") or ""
    tiles = list(card.get("tiles") or [])
    extra = []
    if with_import:
        if WORST.get(imp.get("status"), 0) > WORST.get(status, 0):
            status = imp["status"]
        title = "%s - %s" % (title, imp.get("title") or "Checklist import")
        subtitle = ". ".join(x for x in (subtitle, imp.get("subtitle") or "") if x)
        tiles += list(imp.get("summary") or [])
        extra = list(imp.get("sections") or [])
    elif mode != "scorecard":
        extra = [{"title": "Checklist import", "status": "ok",
                  "lines": ["No checklists to import this run (%s)" % (src or "the drop folder")]}]
    base = {"title": title, "status": status, "subtitle": subtitle, "summary": tiles}
    if imp and imp.get("footer") and with_import:
        base["footer"] = imp["footer"]
    report = dict(base, sections=list(card.get("sections") or []) + extra + tail)
    web = _scorecard_web(base, list(card["web"]) + extra + tail) if card.get("web") else {}
    return {"report": report, "web": web, "xlsx": list(card.get("xlsx") or []) + (_sheets(imp) if with_import else [])}


def _scorecard_web(base, sections):
    """The website's document: the report with the sparkline column. A function of its own, so tests/report_fit_docs.py
    keeps it (it keeps what a filter's function returns) and tests/test_report_fit.py measures the tables with their lines."""
    return dict(base, sections=sections)


class FilterModule(object):
    def filters(self):
        return {"stig_scorecard_point": stig_scorecard_point, "stig_scorecard_runs": stig_scorecard_runs,
                "stig_scorecard": stig_scorecard, "stig_scorecard_join": stig_scorecard_join}
