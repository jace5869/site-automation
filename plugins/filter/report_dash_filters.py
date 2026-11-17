"""Dashboards (1.71.0): a report's first screen - tiles, stacked bars, a weekly line, top-N lists - that the page's
filters (Collection, Platform, CAT, Host: the facet keys col, pf, cat, h) drive. Reusable by any report: the STIG
scorecard is the first user; the index and the other reports can follow.

The data is a few small additive tables (facts) and the panels that read them:

    dash = {"v": 1, "weights": {"k1": 10, "k2": 4, "k3": 1}, "names": {"col": "Collection", ...},
            "facts": {name: dash_fact(cols, rows, dicts)}, "panels": [dash_tiles(...), dash_bars(...), ...]}

A fact is {cols: [...], rows: [[...]], dict: {col: [values]}} - a column named in dict holds an index into its list (a
host's name once, not on every row). A filter applies to a fact that has its column: col, pf, cat by equal value, h by
a part of the host's name (any case; a row with h "" - the counts of hosts not listed - never matches a name). A panel
whose fact has no such column says so ("not by host"). Rows may be negative: the "residual" rows that keep a total
equal to the report's own number when the finer counts do not add up to it.

dash_view(dash, sel) -> the panels' values for a selection; dash_finish(dash) puts the view of no selection into each
panel (view) for the page's template. roles/site_email/templates/site_report.js does the same on the page (the [data-dash]
block, its JSON in a script of type application/json); tests/test_report_dash.py holds the two to the same answers.

Other filter files load this one by its path (dash_module() in their file): Ansible loads filter files by path."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import math
from fractions import Fraction

DIMS = ("col", "pf", "cat", "h")
# a panel's span: 12 columns to a row from 1000 px (span 6: two to a row); one to a row under it
DIM_WORD = {"col": "collection", "pf": "platform", "cat": "CAT", "h": "host"}
CORA_W = (10, 4, 1)
SERIES_W, SERIES_H, SERIES_PAD = 600, 150, 6   # the weekly line's drawing (its viewBox; the page scales it)


def _n(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# ---- the builders --------------------------------------------------------------------------------------------------
def dash_fact(cols, rows, dicts=None):
    """A fact table. dicts: the columns to keep as an index into a list of their values (each value once)."""
    cols = list(cols)
    out = {"cols": cols, "rows": []}
    look = {}
    for c in dicts or []:
        out.setdefault("dict", {})[c] = []
        look[c] = {}
    at = dict((c, cols.index(c)) for c in look)
    for r in rows:
        r = list(r)
        for c, j in at.items():
            v = r[j]
            if v not in look[c]:
                look[c][v] = len(out["dict"][c])
                out["dict"][c].append(v)
            r[j] = look[c][v]
        out["rows"].append(r)
    return out


def dash_meta(items):
    """The line beside the filters: {text} as it is, or {t: count, fact, key (the columns that name one), word, words}:
    how many there are for the filters chosen - "2 of 13 assets" (a row without a name counts its n)."""
    return {"t": "meta", "items": [x for x in items if x]}


def dash_tiles(tiles):
    """A row of tiles. Each: {t: sum | pair | cora | fixed, label, ...}:
    sum {fact, col, where, was (a fact of the same columns: the change), cat (1-3: its colour), status ("any": critical
    when not 0, "any-warning"), since (the word after the change)}; pair {fact, cols [a, b], words [a, b]} (This week:
    closed and opened); cora {fact (col, pf, cat, a, d, o), was}; fixed {value, note, status} (not filtered)."""
    return {"t": "tiles", "tiles": list(tiles)}


def dash_bars(title, fact, group, stacks, groups, col="n", more=None, show=8, span=6):
    """Stacked bars: a bar per group (groups: [{k, name, href, link, tip}]), stacked by stacks [[key, word, cat]]; the
    numbers written beside each bar; the largest weight first. more: {label, href} (Explore)."""
    return {"t": "bars", "title": title, "fact": fact, "group": group, "stack": "cat", "stacks": [list(s) for s in stacks],
            "groups": list(groups), "col": col, "more": more or {}, "show": show, "span": span}


def dash_series(title, fact, heads, have, lines, col="n", table_href="", note="", span=6):
    """A line per week on one axis: heads (the weeks' words), have (a run that week), lines [{name, where, cat}]: the
    first is everything shown, the others a part of it (a line the selection already is, or rules out, is left out)."""
    return {"t": "series", "title": title, "fact": fact, "x": "w", "heads": list(heads), "have": [bool(x) for x in have],
            "lines": list(lines), "col": col, "table_href": table_href, "note": note, "span": span}


def dash_top(title, fact, items, columns, show=5, score="weight", more=None, span=6):
    """The top items by weight (n times the CAT's weight) or by n. fact: (i, ... dims, cat, n), i the item's index.
    items: [{label, href, link, linkt, cat, text}]; columns: [{h (heading), v (label | cat | text | n | s | k1 | k2 | k3),
    num, bar}]; more: {label, href} - the count of items with something open is added."""
    return {"t": "top", "title": title, "fact": fact, "items": list(items), "columns": list(columns), "show": show,
            "score": score, "more": more or {}, "span": span}


# ---- the view ------------------------------------------------------------------------------------------------------
def _rows(fact, sel, ignore=()):
    """The rows of a fact the selection keeps (decoded), and the dimensions chosen that the fact cannot filter by."""
    cols = fact.get("cols") or []
    dic = fact.get("dict") or {}
    idx = dict((c, i) for i, c in enumerate(cols))
    notby = [k for k in DIMS if (sel or {}).get(k) and k not in ignore and k not in idx]
    tests = [(idx[k], k, str(sel[k])) for k in DIMS if (sel or {}).get(k) and k not in ignore and k in idx]
    out = []
    for r in fact.get("rows") or []:
        r = [dic[c][r[i]] if c in dic and isinstance(r[i], int) and 0 <= r[i] < len(dic[c]) else r[i] for i, c in enumerate(cols)]
        ok = True
        for i, k, v in tests:
            x = str(r[i] if r[i] is not None else "")
            if (k == "h" and (not x or v.lower() not in x.lower())) or (k != "h" and x != v):
                ok = False
                break
        if ok:
            out.append(dict(zip(cols, r)))
    return out, notby


def _sum(rows, col, where=None):
    return sum(_n(r.get(col)) for r in rows if all(str(r.get(k)) == str(v) for k, v in (where or {}).items()))


def _clash(where, sel):
    """A tile or a line for one CAT (where) when another CAT is chosen: it does not apply."""
    return any((sel or {}).get(k) and str(sel[k]) != str(v) for k, v in (where or {}).items())


def cora_of(a, d, o):
    """STIG Manager's CORA (as stig_scorecard_filters.cora): per CAT the share Open or not assessed, weighted 10, 4, 1 over
    the CATs that have rules; exact at the band edges. None without rules."""
    if not any(_n(x) for x in a):
        return None
    raw = [min(Fraction(1), Fraction(max(0, _n(x) - _n(y)) + _n(z), _n(x))) if _n(x) > 0 else Fraction(0) for x, y, z in zip(a, d, o)]
    weight = sum(w for w, x in zip(CORA_W, a) if _n(x) > 0)
    avg = sum(w * r for w, r, x in zip(CORA_W, raw, a) if _n(x) > 0) / weight if weight else Fraction(0)
    if not any(raw):
        rating = "Very Low"
    elif raw[0] == 0 and raw[1] < Fraction(1, 20) and raw[2] < Fraction(1, 20):
        rating = "Low"
    elif avg >= Fraction(1, 5):
        rating = "Very High"
    elif avg >= Fraction(1, 10):
        rating = "High"
    else:
        rating = "Moderate"
    p10 = int(math.floor(avg * 1000 + Fraction(1, 2)))      # tenths of a percent, half up (site-report.js does the same)
    needs = []
    if rating not in ("Low", "Very Low"):
        if raw[0] > 0:
            needs.append("CAT I 0")
        rest = [w for w, r in (("II", raw[1]), ("III", raw[2])) if r >= Fraction(1, 20)]
        if rest:
            needs.append("CAT %s under 5%%" % " and ".join(rest))
    return {"pct": p10 / 10.0, "p10": p10, "rating": rating,
            "status": {"Very High": "critical", "High": "critical", "Moderate": "warning"}.get(rating, "ok"),
            "needs": ("Low needs: " + ", ".join(needs)) if needs else ""}


def _cora_rows(rows):
    a, d, o = [0, 0, 0], [0, 0, 0], [0, 0, 0]
    for r in rows:
        k = _n(str(r.get("cat") or "")[1:]) - 1
        if 0 <= k < 3:
            a[k] += _n(r.get("a"))
            d[k] += _n(r.get("d"))
            o[k] += _n(r.get("o"))
    return cora_of(a, d, o)


def _mkey(r, keys):
    return tuple(str(r.get(k) if r.get(k) is not None else "") for k in keys)


def _tile(t, dash, sel):
    facts = dash.get("facts") or {}
    kind = t.get("t")
    if kind == "fixed":
        return {"v": t.get("value"), "notby": [k for k in DIMS if (sel or {}).get(k)]}
    if kind == "cora":
        rows, notby = _rows(facts.get(t.get("fact")) or {}, sel, ignore=("cat",))
        x = _cora_rows(rows)
        out = {"cora": x, "notby": notby + (["cat"] if (sel or {}).get("cat") else [])}
        if x and t.get("was") and t["was"] in facts:
            wrows = _rows(facts[t["was"]], sel, ignore=("cat",))[0]
            keys = t.get("match") or []         # 1.71.0b: the change over the parts both runs have (the score stays all)
            if keys:
                both = set(_mkey(r, keys) for r in rows) & set(_mkey(r, keys) for r in wrows)
                rows, wrows = [r for r in rows if _mkey(r, keys) in both], [r for r in wrows if _mkey(r, keys) in both]
            xm, w = _cora_rows(rows), _cora_rows(wrows)
            if xm and w:
                out["d"] = (xm["p10"] - w["p10"]) / 10.0
        return out
    if kind == "pair":
        rows, notby = _rows(facts.get(t.get("fact")) or {}, sel)
        a, b = [_sum(rows, c) for c in t.get("cols") or ["a", "b"]]
        return {"v": a, "v2": b, "notby": notby, "status": "ok" if a >= b else "warning"}
    where = t.get("where") or {}
    if _clash(where, sel):
        return {"off": True, "v": 0, "notby": []}
    rows, notby = _rows(facts.get(t.get("fact")) or {}, sel)
    v = _sum(rows, t.get("col") or "n", where)
    out = {"v": v, "notby": notby}
    if t.get("was") and t["was"] in facts:
        wr, wn = _rows(facts[t["was"]], sel)
        out["d"] = v - _sum(wr, t.get("col") or "n", where)
    st = t.get("status") or ""
    out["status"] = ("critical" if st == "any" else "warning" if st == "any-warning" else "") if v else ("ok" if st else "")
    return out


def _count(x, dash, sel):
    fact = (dash.get("facts") or {}).get(x.get("fact")) or {}

    def cnt(rows):
        keys = x.get("key") or ["h"]
        named = set(tuple(str(r.get(k)) for k in keys) for r in rows if r.get("h"))
        return len(named) + sum(_n(r.get("n")) for r in rows if not r.get("h"))
    return {"v": cnt(_rows(fact, sel)[0]), "all": cnt(_rows(fact, {})[0])}


def _bars(p, dash, sel):
    rows, notby = _rows((dash.get("facts") or {}).get(p["fact"]) or {}, sel)
    keys = [s[0] for s in p["stacks"]]
    wt = dash.get("weights") or {}
    sums = {}
    for r in rows:
        g, k = str(r.get(p["group"])), str(r.get(p["stack"]))
        if k in keys:
            v = sums.setdefault(g, [0] * len(keys))
            v[keys.index(k)] += _n(r.get(p["col"]))
    out = []
    for i, g in enumerate(p["groups"]):
        v = sums.get(str(g["k"]), [0] * len(keys))
        if sum(v) > 0:
            out.append({"i": i, "v": v, "t": sum(v), "s": sum(x * _n(wt.get(k, 1)) for x, k in zip(v, keys))})
    out.sort(key=lambda x: (-x["s"], -x["t"], str(p["groups"][x["i"]].get("name", "")).lower()))
    return {"groups": out[:p.get("show") or 8], "count": len(out), "max": max([x["t"] for x in out] or [0]), "notby": notby}


def _series(p, dash, sel):
    fact = (dash.get("facts") or {}).get(p["fact"]) or {}
    rows, notby = _rows(fact, sel)
    lines, seen = [], []
    for ln in p["lines"]:
        where = ln.get("where") or {}
        if _clash(where, sel):
            continue
        eff = dict((k, str(v)) for k, v in (sel or {}).items() if v and k in (fact.get("cols") or []) and k != "h")
        eff.update(dict((k, str(v)) for k, v in where.items()))
        if eff in seen:
            continue
        seen.append(eff)
        vals = []
        for i, h in enumerate(p["have"]):              # a week the selection has no row in (a collection not there yet): a gap
            wr = [r for r in rows if _n(r.get(p["x"])) == i]
            vals.append(_sum(wr, p["col"], where) if h and wr else None)
        lines.append({"j": p["lines"].index(ln), "vals": vals})
    top = max([v for ln in lines for v in ln["vals"] if v is not None] or [0])
    ticks = dash_ticks(top)
    for ln in lines:                                  # the drawing: SERIES_W x SERIES_H, 0 at the bottom
        g = dash_path(ln["vals"], ticks[-1])
        ln["d"] = g["d"]
        ends = [(p, v) for p, v in zip(g["pts"], ln["vals"]) if p is not None]
        ln["end"] = [ends[-1][0][0], ends[-1][0][1], ends[-1][1]] if ends else None
        ln["dots"] = [list(p) for p in g["pts"] if p is not None] if len(ends) <= 2 else []
    return {"lines": lines, "max": top, "ticks": ticks, "ty": [round(SERIES_H - SERIES_H * t / float(ticks[-1]), 1) for t in ticks],
            "xs": [round(SERIES_PAD + (SERIES_W - 2 * SERIES_PAD) * (i / float(max(1, len(p["have"]) - 1))), 1) for i in range(len(p["have"]))],
            "notby": notby}


def _top(p, dash, sel):
    rows, notby = _rows((dash.get("facts") or {}).get(p["fact"]) or {}, sel)
    wt = dash.get("weights") or {}
    acc = {}
    for r in rows:
        i = _n(r.get("i"))
        x = acc.setdefault(i, {"i": i, "n": 0, "s": 0, "k1": 0, "k2": 0, "k3": 0})
        n, k = _n(r.get("n")), str(r.get("cat"))
        x["n"] += n
        x["s"] += n * (_n(wt.get(k, 1)) if p.get("score") == "weight" else 1)
        if k in x:
            x[k] += n
    out = sorted([x for x in acc.values() if x["n"] > 0],
                 key=lambda x: (-x["s"], -x["n"], str(p["items"][x["i"]].get("label", "")).lower() if x["i"] < len(p["items"]) else ""))
    return {"rows": out[:p.get("show") or 5], "count": len(out), "max": max([x["s"] for x in out] or [0]), "notby": notby}


def dash_view(dash, sel=None):
    """The view of every panel for a selection {col, pf, cat, h} (missing or "": all)."""
    sel = dict((k, str(v)) for k, v in (sel or {}).items() if k in DIMS and v)
    out = []
    for p in (dash or {}).get("panels") or []:
        t = p.get("t")
        if t == "tiles":
            out.append({"tiles": [_tile(x, dash, sel) for x in p["tiles"]]})
        elif t == "meta":
            out.append({"items": [_count(x, dash, sel) if x.get("t") == "count" else {} for x in p["items"]]})
        elif t == "bars":
            out.append(_bars(p, dash, sel))
        elif t == "series":
            out.append(_series(p, dash, sel))
        elif t == "top":
            out.append(_top(p, dash, sel))
        else:
            out.append({})
    return out


def dash_finish(dash):
    """The dashboard with each panel's view of no selection (view), for the template."""
    if not dash:
        return {}
    for p, v in zip(dash.get("panels") or [], dash_view(dash, {})):
        p["view"] = v
    return dash


def dash_ticks(top):
    """Up to four round steps from 0 that reach top (0, 10, 20, 30; 0, 13, 27, 40 is not round - 0, 15, 30, 45)."""
    top = max(1, _n(top))
    for step in (1, 2, 5, 10, 15, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000, 20000, 25000, 50000, 100000):
        if step * 3 >= top:
            return [0, step, step * 2, step * 3]
    step = -(-top // 3)
    return [0, step, step * 2, step * 3]


def dash_path(vals, top, w=SERIES_W, h=SERIES_H, pad=SERIES_PAD):
    """An SVG path through the weeks' values (None: a gap), x across w, y 0 at the bottom of h; and the points."""
    n = len(vals)
    top = max(1, top)
    pts = [(round(pad + (w - 2 * pad) * (i / float(max(1, n - 1))), 1), round(h - (h * v / float(top)), 1)) if v is not None else None
           for i, v in enumerate(vals)]
    d, pen = [], False
    for p in pts:
        if p is None:
            pen = False
            continue
        d.append(("L" if pen else "M") + "%s %s" % p)
        pen = True
    return {"d": " ".join(d), "pts": pts}


class FilterModule(object):
    def filters(self):
        return {"dash_view": dash_view, "dash_finish": dash_finish, "dash_ticks": dash_ticks, "dash_path": dash_path}
