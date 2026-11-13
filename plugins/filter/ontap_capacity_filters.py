# -*- coding: utf-8 -*-
"""The NetApp ONTAP capacity trends report (playbooks/ontap_capacity_trends.yml, 1.52.0): what is filling, how fast and
when it is full. Each run reads every cluster's aggregates and volumes once (roles/ontap_report/library/site_ontap_collect.py
with _ontap_cap_queries) and keeps the sizes as one point (ontap_capacity_points: the job's artifact ontap_capacity, and
a row per object in the report database). The trend is this run's point with the earlier runs' points - from AAP's job
history and from the report database (ontap_capacity_runs: one point per run). ontap_capacity: the report, the website's
version with a sparkline per row, the workbook and the lines for the job's log.

Only what was measured: a rate is the least-squares slope of the used space over the runs (after the last change, for a
volume), a "change" is a size that changed or used space that dropped between two runs - never a guessed cause. Fewer
than 3 runs: no rate, no days."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import re

GB = 1024.0 ** 3
TB = 1024.0 ** 4
DAY = 86400.0
SLACK = 3600.0                # a daily run starts a little earlier or later: the run of the window's first day counts
MIN_POINTS = 3                  # a rate needs this many runs
RESIZE_PCT = 1.0                # a size that changed by more than this, between two runs: resized
DROP_POINTS = 5.0               # used space that fell by this many points of the volume's size, between two runs: a drop
SPARK_W, SPARK_H, SPARK_PAD = 120, 30, 4
SPARK_MAX = 60                  # points drawn (a longer history: the last point of each of 60 equal spans)
WEEK_COLUMNS = 9                # "The numbers": at most this many columns (a longer window: every 2nd, 3rd ... week)
AGGR_SOON, AGGR_NOW = 90, 30    # an aggregate reaching ontap_aggr_crit_pct within 90 days: amber; within 30: red
NEAR_RESERVE = 80.0             # snapshots using this share of the warning level of their reserve: listed as near it
HISTORY_SIZE_PCT, HISTORY_USED_POINTS = 2.0, 5.0      # ONTAP's own cluster history is used when it agrees this closely


# ---- small helpers --------------------------------------------------------------------------------
def g(d, path, default=None):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur if cur is not None else default


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


def iso(t):
    return datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def day(t):
    return datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc).strftime("%Y-%m-%d")


def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def pct(used, size):
    return 100.0 * used / size if size else None


def f1(v):
    """A number for a cell, one decimal: 71.4; '-' when there is none."""
    return "-" if v is None else "%.1f" % v


def whole(v):
    """Days or months for a cell: under 10 with one decimal, else whole; '-' when there is none."""
    return "-" if v is None else ("%.1f" % v if v < 10 else "%d" % round(v))


def size_text(b):
    """A size in words (What changed): 2.5 TB, 800 GB."""
    return "%.1f TB" % (b / TB) if b >= TB else "%d GB" % round(b / GB)


def as_bool(v):
    return v if isinstance(v, bool) else str(v).strip().lower() in ("true", "yes", "1", "on")


def slope(points):
    """[(t seconds, y)] -> y per day (least squares), or None: fewer than MIN_POINTS points, or all at one time."""
    if len(points) < MIN_POINTS:
        return None
    n = float(len(points))
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    den = sum((p[0] - mx) ** 2 for p in points)
    if den <= 0:
        return None
    return sum((p[0] - mx) * (p[1] - my) for p in points) / den * DAY


def days_to(points, rate, level_pct):
    """Days until the last point's used space is level_pct of its size at rate (bytes a day): 0 = there now, None = not
    growing (or no rate)."""
    t, size, used = points[-1]
    if not size:
        return None
    left = level_pct / 100.0 * size - used
    if left <= 0:
        return 0.0
    return left / rate if rate and rate > 0 else None


# ---- the points ---------------------------------------------------------------------------------------
def ontap_capacity_points(collected, opts=None):
    """What the collector read -> this run's point (the artifact ontap_capacity): {v, read_at, clusters: [{uuid, name,
    size_b, used_b, aggregates: [{uuid, size_b, used_b}], volumes: [{uuid, size_b, used_b}], snapshot_reserve: [{uuid,
    reserve_pct, snap_used_b}]}]}. Volumes: online, not an SVM's root, ontap_volume_min_gb or larger. A part that could
    not be read is left out (not an empty list): a gap in the trend, never a false zero. names: the same objects by
    name, for the report and the database - not kept in the artifact."""
    o = opts or {}
    col = collected or {}
    vmin = float(o.get("volume_min_gb", 50) or 0) * GB
    out, names = [], {}
    for c in col.get("clusters") or []:
        if not c.get("reachable", True):
            continue
        data = c.get("data") or {}
        info = ((data.get("cluster") or {}).get("records") or [{}])[0]
        cid = str(info.get("uuid") or info.get("name") or c.get("address") or "")
        cname = str(info.get("name") or c.get("address") or "")
        point = {"uuid": cid, "name": cname}
        names[cid] = {"name": cname, "address": str(c.get("address") or ""), "aggregates": {}, "volumes": {}}
        ag = data.get("aggregates") or {}
        if not ag.get("error") and not ag.get("truncated"):
            rows = []
            for a in ag.get("records") or []:
                size, used = num(g(a, "space.block_storage.size")), num(g(a, "space.block_storage.used"))
                if a.get("uuid") and size and used is not None:
                    rows.append({"uuid": str(a["uuid"]), "size_b": int(size), "used_b": int(used)})
                    names[cid]["aggregates"][str(a["uuid"])] = {"name": str(a.get("name") or ""), "node": str(g(a, "node.name", ""))}
            point["aggregates"] = rows
            point["size_b"] = sum(r["size_b"] for r in rows)
            point["used_b"] = sum(r["used_b"] for r in rows)
        vo = data.get("volumes") or {}
        if not vo.get("error") and not vo.get("truncated"):
            rows, snaps = [], []
            for v in vo.get("records") or []:
                size, used = num(g(v, "space.size")), num(g(v, "space.used"))
                if (not v.get("uuid") or as_bool(v.get("is_svm_root")) or v.get("state", "online") != "online" or not size
                        or used is None or size < vmin):
                    continue
                uid = str(v["uuid"])
                rows.append({"uuid": uid, "size_b": int(size), "used_b": int(used)})
                names[cid]["volumes"][uid] = {"name": str(v.get("name") or ""), "svm": str(g(v, "svm.name", ""))}
                rp, su = num(g(v, "space.snapshot.reserve_percent")), num(g(v, "space.snapshot.used"))
                if rp and su:
                    snaps.append({"uuid": uid, "reserve_pct": rp, "snap_used_b": int(su)})
            point["volumes"] = rows
            point["snapshot_reserve"] = snaps
        out.append(point)
    return {"v": 1, "read_at": str(col.get("read_at") or ""), "clusters": out, "names": names}


def ontap_capacity_runs(current, aap=None, db=None, opts=None):
    """This run's point with the earlier runs' - from AAP's job history (site_ontap_capacity_history) and from the report
    database (site_ontap_capacity_db) - as one list, oldest first: one point per run. A run both hold (the same job, or
    the same minute read) counts once: AAP's first, then the database's. Only the window's (ontap_capacity_days), never
    one after this run. -> {runs: [...], counts: {aap, db, both, used}}"""
    o = opts or {}
    now = parse_time((current or {}).get("read_at")) or 0
    since = now - float(o.get("days", 30) or 30) * DAY - SLACK
    jobs, minutes, out = {}, {}, []                     # a job ID, a minute -> where its run came from
    counts = {"aap": 0, "db": 0, "both": 0}

    def take(run, src):
        t = parse_time((run or {}).get("read_at"))
        if t is None or not isinstance(run.get("clusters"), list):
            return
        job = str(run.get("job") or "")
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


def _series(runs):
    """{(cluster, kind, uuid): [(t, size_b, used_b)]} and the snapshot reserves' {(cluster, uuid): [(t, reserve_b,
    snap_used_b)]}, oldest first."""
    ser, snap = {}, {}
    for r in runs:
        t = r["_t"]
        for c in r.get("clusters") or []:
            if not isinstance(c, dict):
                continue
            cid = str(c.get("uuid") or c.get("name") or "")
            if num(c.get("size_b")) and num(c.get("used_b")) is not None:
                ser.setdefault((cid, "cluster", cid), []).append((t, float(c["size_b"]), float(c["used_b"])))
            sizes = {}
            for kind, key in (("aggregate", "aggregates"), ("volume", "volumes")):
                for x in c.get(key) or []:
                    if isinstance(x, dict) and x.get("uuid") and num(x.get("size_b")) and num(x.get("used_b")) is not None:
                        ser.setdefault((cid, kind, str(x["uuid"])), []).append((t, float(x["size_b"]), float(x["used_b"])))
                        if kind == "volume":
                            sizes[str(x["uuid"])] = float(x["size_b"])
            for x in c.get("snapshot_reserve") or []:
                if isinstance(x, dict) and x.get("uuid") and num(x.get("reserve_pct")) and num(x.get("snap_used_b")) is not None:
                    size = sizes.get(str(x["uuid"]))
                    if size:
                        snap.setdefault((cid, str(x["uuid"])), []).append((t, size * float(x["reserve_pct"]) / 100.0, float(x["snap_used_b"])))
    return ser, snap


# ---- a volume's story -----------------------------------------------------------------------------------
def _change_text(before, after):
    """What changed between two points, only what the numbers show: 'resized 2.0 TB to 2.5 TB', 'used space dropped 12%',
    or both."""
    out = []
    if before[1] and abs(after[1] - before[1]) / before[1] * 100.0 > RESIZE_PCT / 2.0:
        out.append("resized %s to %s" % (size_text(before[1]), size_text(after[1])))
    if before[2] and after[2] < before[2] and (before[2] - after[2]) / before[2] * 100.0 >= 0.5:
        out.append("used space dropped %d%%" % round((before[2] - after[2]) / before[2] * 100.0))
    return ", ".join(out)


def volume_story(points, opts=None):
    """One volume's points [(t, size_b, used_b)] -> what the report says of it: {pct, back_again, fast, change (index of
    the first point after the last change, or None), changed (words), earlier ({pct, t} - the peak before the change),
    rate_b (bytes a day over the runs after the change, None under 3), after (those runs), days_full, days_crit}."""
    o = opts or {}
    warn, crit = float(o.get("volume_warn_pct", 85)), float(o.get("volume_crit_pct", 90))
    again, fast = float(o.get("again_days", 7)), float(o.get("fast_days", 30))
    p = [pct(x[2], x[1]) for x in points]
    last = len(points) - 1
    out = {"pct": p[-1], "back_again": False, "fast": False, "change": None, "changed": "", "earlier": None, "rate_b": None,
           "days_full": None, "days_crit": None}
    start = 0
    # back again: over the warning level now, under it before that, and over it before that - within again days
    if p[-1] >= warn:
        k = last
        while k >= 0 and p[k] >= warn:
            k -= 1
        if 0 <= k < last:
            j = k
            while j >= 0 and p[j] < warn:
                j -= 1
            if j >= 0 and points[j][0] >= points[-1][0] - again * DAY:
                low = min(range(j + 1, k + 1), key=lambda i: p[i])
                first = j
                while first > 0 and p[first - 1] >= warn:
                    first -= 1
                peak = max(range(first, j + 1), key=lambda i: p[i])
                out.update(back_again=True, change=j + 1, changed=_change_text(points[j], points[low]),
                           earlier={"pct": p[peak], "t": points[peak][0]})
                start = low
    if not out["back_again"]:
        # the last change between two runs: a new size, or used space that fell
        for i in range(last, 0, -1):
            a, b = points[i - 1], points[i]
            resized = abs(b[1] - a[1]) / a[1] * 100.0 > RESIZE_PCT
            dropped = (a[2] - b[2]) / a[1] * 100.0 >= DROP_POINTS
            if resized or dropped:
                peak = max(range(0, i), key=lambda n: p[n])
                out.update(change=i, changed=_change_text(a, b), earlier={"pct": p[peak], "t": points[peak][0]})
                start = i
                break
    after = points[start:]
    out["after"] = len(after)
    rate = slope([(x[0], x[2]) for x in after])
    out["rate_b"] = rate
    if rate is not None:
        out["days_full"] = days_to(points, rate, 100.0)
        out["days_crit"] = days_to(points, rate, crit)
        out["fast"] = rate > 0 and out["days_crit"] is not None and out["days_crit"] <= fast
    return out


# ---- the sparkline and the numbers behind it ---------------------------------------------------------------
def spark(values, since, now, lines, change_t=None, what=""):
    """[(t, y)] -> the sparkline's geometry (the atlas and brief looks draw it as an inline SVG): {w, h, points ('x,y
    x,y'), last [x, y], lines [y of each dashed level], mark (x of the change, or None), label (the values in words, for
    aria-label and the hover)}. x by time across the window, so a gap is a gap; y from the lowest of the values and the
    levels to the highest, so the line is read against the levels."""
    if not values:
        return {}
    vals = values
    if len(vals) > SPARK_MAX:                                    # the last point of each of SPARK_MAX equal spans
        span = (now - since) / SPARK_MAX or 1.0
        keep = {}
        for t, y in vals:
            keep[min(SPARK_MAX - 1, int((t - since) / span))] = (t, y)
        vals = [keep[k] for k in sorted(keep)]
    ys = [v[1] for v in vals] + list(lines)
    lo, hi = min(ys), max(ys)
    if hi - lo < 1e-9:
        lo, hi = lo - 1.0, hi + 1.0
    inner_w, inner_h = SPARK_W - 2.0 * SPARK_PAD, SPARK_H - 2.0 * SPARK_PAD

    def x(t):
        return round(SPARK_PAD + max(0.0, min(1.0, (t - since) / ((now - since) or 1.0))) * inner_w, 1)

    def y(v):
        return round(SPARK_PAD + (1.0 - (v - lo) / (hi - lo)) * inner_h, 1)

    pts = [(x(t), y(v)) for t, v in vals]
    label = "%s: %s on %s" % (what or "Trend", f1(values[-1][1]), day(values[-1][0]))
    if len(values) > 1:
        label = "%s: %s on %s to %s on %s, %d runs" % (what or "Trend", f1(values[0][1]), day(values[0][0]), f1(values[-1][1]),
                                                       day(values[-1][0]), len(values))
        weekly = [w for w in weeks(values, since, now)[1] if w is not None]
        if len(weekly) > 2:
            label += "; by week: " + ", ".join(f1(w) for w in weekly)
    return {"w": SPARK_W, "h": SPARK_H, "points": " ".join("%s,%s" % q for q in pts), "last": list(pts[-1]),
            "lines": [y(v) for v in lines], "mark": x(change_t) if change_t is not None else None, "label": label}


def weeks(values, since, now):
    """[(t, y)] -> (the columns' dates, the values): the window in weeks ending now (every 2nd, 3rd ... week when there
    are more than WEEK_COLUMNS), each the last value read in it, None where there was no run."""
    n = max(1, int(-(-(now - since) // (7 * DAY))))
    step = max(1, -(-n // WEEK_COLUMNS))
    cols = -(-n // step)
    span = step * 7 * DAY
    heads, out = [], []
    for k in range(cols - 1, -1, -1):
        end, start = now - k * span, now - (k + 1) * span
        inside = [v for v in values if start < v[0] <= end]
        heads.append(day(end)[5:])
        out.append(inside[-1][1] if inside else None)
    return heads, out


# ---- the report ------------------------------------------------------------------------------------------
class _Doc(object):
    """The report twice: sections for the email and the other looks, and web - the same with a Trend column."""

    def __init__(self):
        self.sections, self.web, self.numbers = [], [], []

    def table(self, title, text, columns, rows, none_text, nums=(), wrap=(), titles=None, lines=None, collapsed=False, trend=""):
        """rows: [(cells, cell statuses, row status, spark or None, (what, name, cluster, weekly values))]."""
        def kinds(cols):
            return ["num" if c in nums else ("wrap" if c in wrap else ("spark" if c == "Trend" else "")) for c in cols]
        for web in (False, True):
            s = {"title": title}
            if collapsed:
                s["collapsed"] = True
            if rows:
                cols = columns + (["Trend"] if web else [])
                s.update({"text": text, "columns": cols, "kinds": kinds(cols),
                          "rows": [r[0] + ([(r[3] or {}).get("label", "")] if web else []) for r in rows],
                          "cell_status": [list(r[1]) + [""] * (len(cols) - len(r[1])) for r in rows],
                          "row_status": [r[2] if r[2] in ("critical", "warning") else "" for r in rows]})
                if titles:
                    s["column_titles"] = [titles.get(c, c) for c in cols]
                if web:
                    s["sparks"] = [r[3] or {} for r in rows]
                st = "critical" if any(r[2] == "critical" for r in rows) else ("warning" if any(r[2] == "warning" for r in rows) else "")
                if st:
                    s["status"] = st
            else:
                s["text"] = (text + " " + none_text).strip()
            notes = ([trend] if web and rows and trend else []) + list(lines or [])
            if notes:
                s["lines"] = notes
            (self.web if web else self.sections).append(s)
        self.numbers += [r[4] for r in rows if len(r) > 4 and r[4]]


def _count(n, word):
    """1 volume, 2 volumes."""
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def _few(names, total):
    """The rows with too few runs for a rate, said once: every row (a new install), or the first few by name."""
    if not names:
        return []
    if len(names) == total:
        return ["Fewer than %d runs so far: no rate and no days yet. Each run adds one point." % MIN_POINTS]
    return ["Fewer than %d runs, so no rate and no days yet: %s%s." % (MIN_POINTS, ", ".join(names[:5]),
                                                                       " and %d more" % (len(names) - 5) if len(names) > 5 else "")]


def _ontap_history(c, own, since):
    """ONTAP's own cluster capacity history (GET /api/storage/cluster/metrics, ONTAP 9.17.1 and later; cluster-wide only)
    for the days before this report's first run -> (points, a line for the page or ''). Used only when its newest sample
    agrees with what this run summed from the aggregates - else the two count the space differently, and it is left out."""
    d = (c.get("data") or {}).get("cluster_history")
    if not isinstance(d, dict) or not own:
        return [], ""
    first = own[0][0]
    if first - since < 1.5 * DAY:
        return [], ""                                           # the window is covered by this report's own runs
    if d.get("error") or not d.get("records"):
        return [], "%s: ONTAP's own cluster capacity history was not read (%s) - the trend starts with this report's first run." % (
            c["_name"], d.get("error") or "this ONTAP has none")
    days = {}
    for r in d.get("records") or []:
        t = parse_time(r.get("timestamp"))
        size, used = num(r.get("size")), num(r.get("used_space"))
        if t is None or str(r.get("status", "ok")) != "ok" or not size or used is None:
            continue
        if day(t) not in days or t > days[day(t)][0]:
            days[day(t)] = (t, float(size), float(used))
    pts = sorted(days.values())
    if not pts:
        return [], ""
    newest, now = pts[-1], own[-1]
    if (abs(newest[1] - now[1]) / now[1] * 100.0 > HISTORY_SIZE_PCT
            or abs(newest[2] - now[2]) / now[1] * 100.0 > HISTORY_USED_POINTS):
        return [], ("%s: ONTAP's own cluster capacity history counts the space differently (%s of %s there, %s of %s summed "
                    "from the aggregates) - not used." % (c["_name"], size_text(newest[2]), size_text(newest[1]),
                                                          size_text(now[2]), size_text(now[1])))
    older = [x for x in pts if since <= x[0] < first - DAY / 2.0]
    if not older:
        return [], ""
    return older, "%s: the %d days before this report's first run come from ONTAP's own cluster capacity history." % (c["_name"], len(older))


def ontap_capacity(collected, point, runs, opts=None):
    """-> {report, web, xlsx, lines, counts}: the capacity trends report (the layout roles/site_email renders), its
    website version (a Trend column: sections' sparks, kind spark), the workbook's sheets and the job log's lines.
    collected: the collector's result; point: ontap_capacity_points of it; runs: ontap_capacity_runs(...).runs."""
    o = opts or {}
    col = collected or {}
    names = (point or {}).get("names") or {}
    runs = list(runs or [])
    now = parse_time(col.get("read_at")) or (runs[-1]["_t"] if runs else 0)
    win = float(o.get("days", 30) or 30)
    since = now - win * DAY
    aw, ac = float(o.get("aggr_warn_pct", 85)), float(o.get("aggr_crit_pct", 90))
    vw, vc = float(o.get("volume_warn_pct", 85)), float(o.get("volume_crit_pct", 90))
    sw, sc = float(o.get("snap_warn_pct", 100)), float(o.get("snap_crit_pct", 150))
    again, fast, top = int(float(o.get("again_days", 7))), int(float(o.get("fast_days", 30))), int(float(o.get("top", 5)))
    max_rows = int(float(o.get("max_rows", 50)))
    n = lambda v: "%d" % v if float(v) == int(v) else "%s" % v          # noqa: E731 - a level for a title: 85, 87.5
    ser, snapser = _series(runs)
    D = _Doc()

    def trend(what, lo, hi, more=""):
        return "Trend: %s over the last %s days. Dashed lines: %s%% and %s%%. Dot: this run.%s" % (what, n(win), n(lo), n(hi), more)
    cs = []
    for c in col.get("clusters") or []:
        c = dict(c)
        info = (((c.get("data") or {}).get("cluster") or {}).get("records") or [{}])[0]
        c["_id"] = str(info.get("uuid") or info.get("name") or c.get("address") or "")
        c["_name"] = str(info.get("name") or c.get("address") or "")
        cs.append(c)
    up = [c for c in cs if c.get("reachable", True)]

    def weekly(vals, what, name, cluster):
        return (what, name, cluster, weeks(vals, since, now)[1])

    # ---- clusters ------------------------------------------------------------------------------------
    rows, few, notes, points_rows = [], [], [], []
    for c in cs:
        if not c.get("reachable", True):
            rows.append((["%s" % c.get("address", ""), "-", "-", "-", "-", "-"], ["critical"], "critical", None, None))
            notes.append("%s: could not be read - %s" % (c.get("address", ""), c.get("error", "")))
            continue
        for key in ("aggregates", "volumes"):
            d = (c.get("data") or {}).get(key) or {}
            if d.get("error") or d.get("truncated"):
                notes.append("%s: the %s could not be read (%s) - no point this run." % (
                    c["_name"], key, d.get("error") or "more than the collector reads"))
        pts = ser.get((c["_id"], "cluster", c["_id"])) or []
        if not pts:
            rows.append(([c["_name"], "-", "-", "-", "-", "-"], [], "", None, None))
            continue
        older, line = _ontap_history(c, pts, since)
        if line:
            notes.append(line)
        pts = older + pts
        rate = slope([(x[0], x[2]) for x in pts])
        p = pct(pts[-1][2], pts[-1][1])
        to_warn = days_to(pts, rate, aw) if rate is not None else None
        if rate is None:
            few.append(c["_name"])
        vals = [(x[0], pct(x[2], x[1])) for x in pts]
        st = "critical" if p >= ac else ("warning" if p >= aw else "")
        rows.append(([c["_name"], f1(pts[-1][1] / TB), f1(pts[-1][2] / TB), f1(p), f1(rate * 30 / GB) if rate is not None else "-",
                      ("now" if to_warn == 0 else whole(to_warn / 30.4)) if to_warn is not None else "-"],
                     ["", "", "", st], st, spark(vals, since, now, [aw, ac], what="Used % of " + c["_name"]),
                     weekly(vals, "Cluster", c["_name"], c["_name"])))
        points_rows += [[iso(x[0]), c["_name"], "Cluster", c["_name"], "", int(x[1]), int(x[2]), round(pct(x[2], x[1]), 2)] for x in pts]
    D.table("Clusters", "Each cluster's space: the sum of its aggregates. Growth is the used space's slope over the runs "
            "of the last %s days; the months are until %s%% used at that rate." % (n(win), n(aw)),
            ["Cluster", "Size TB", "Used TB", "Used %", "Growth GB / 30 days", "Reaches %s%% in (months)" % n(aw)], rows,
            "No cluster was read.", nums=("Size TB", "Used TB", "Used %", "Growth GB / 30 days", "Reaches %s%% in (months)" % n(aw)),
            wrap=("Cluster",), trend=trend("used %", aw, ac), lines=notes + _few(few, len([r for r in rows if r[3] is not None])))

    # ---- aggregates ------------------------------------------------------------------------------------
    rows, few = [], []
    aggr_warn = aggr_soon = aggr_red = 0
    for c in up:
        for uid, nm in sorted((names.get(c["_id"], {}).get("aggregates") or {}).items(), key=lambda kv: kv[1]["name"]):
            pts = ser.get((c["_id"], "aggregate", uid)) or []
            if not pts:
                continue
            rate = slope([(x[0], x[2]) for x in pts])
            p = pct(pts[-1][2], pts[-1][1])
            to_crit = days_to(pts, rate, ac) if rate is not None else (0.0 if p >= ac else None)
            if rate is None:
                few.append("%s / %s" % (c["_name"], nm["name"]))
            red = to_crit is not None and to_crit <= AGGR_NOW
            soon = to_crit is not None and to_crit <= AGGR_SOON
            st = "critical" if red else ("warning" if p >= aw or soon else "")
            aggr_warn += 1 if p >= aw else 0
            aggr_soon += 1 if soon else 0
            aggr_red += 1 if red else 0
            vals = [(x[0], pct(x[2], x[1])) for x in pts]
            rows.append(([nm["name"], c["_name"], f1(pts[-1][1] / TB), f1(p), f1(rate / GB) if rate is not None else "-",
                          ("now" if to_crit == 0 else whole(to_crit)) if to_crit is not None else "-"],
                         ["", "", "", "critical" if p >= ac else ("warning" if p >= aw else ""), "", "critical" if red else ("warning" if soon else "")],
                         st, spark(vals, since, now, [aw, ac], what="Used %% of %s" % nm["name"]),
                         weekly(vals, "Aggregate", nm["name"], c["_name"])))
            points_rows += [[iso(x[0]), c["_name"], "Aggregate", nm["name"], "", int(x[1]), int(x[2]), round(pct(x[2], x[1]), 2)] for x in pts]
    rows.sort(key=lambda r: ({"critical": 0, "warning": 1}.get(r[2], 2), -float(r[0][3])))
    D.table("Aggregates", "Amber: %s%% used or more now, or %s%% within %d days at this rate. Red: %s%% within %d days. "
            "A dash under the days: not growing." % (n(aw), n(ac), AGGR_SOON, n(ac), AGGR_NOW),
            ["Aggregate", "Cluster", "Size TB", "Used % now", "Growth GB/day", "Days to %s%%" % n(ac)], rows[:max_rows],
            "No aggregate was read.", nums=("Size TB", "Used % now", "Growth GB/day", "Days to %s%%" % n(ac)), wrap=("Cluster", "Aggregate"),
            trend=trend("used %", aw, ac),
            lines=_few(few, len(rows)) + (["The first %d of %d are shown; the workbook has all." % (max_rows, len(rows))] if len(rows) > max_rows else []))

    # ---- volumes ---------------------------------------------------------------------------------------
    vols = []
    for c in up:
        for uid, nm in (names.get(c["_id"], {}).get("volumes") or {}).items():
            pts = ser.get((c["_id"], "volume", uid)) or []
            if pts:
                vols.append({"c": c, "uid": uid, "name": nm["name"], "svm": nm["svm"], "pts": pts, "s": volume_story(pts, o),
                             "vals": [(x[0], pct(x[2], x[1])) for x in pts]})
    again_rows = []
    n_again = n_fast = 0
    for v in vols:
        s = v["s"]
        if not (s["back_again"] or s["fast"]):
            continue
        n_again += 1 if s["back_again"] else 0
        n_fast += 1 if s["fast"] else 0
        why = "Back again" if s["back_again"] else "Filling fast"
        if s["back_again"] and s["fast"]:
            why = "Back again, filling fast"
        ch = v["pts"][s["change"]][0] if s["change"] is not None else None
        v["spark"] = spark(v["vals"], since, now, [vw, vc], change_t=ch, what="Used %% of %s" % v["name"])
        again_rows.append(([v["name"], "%s / %s" % (v["c"]["_name"], v["svm"]), why, f1(s["pct"]),
                            ("%s, from %s%% on %s" % (s["changed"], f1(s["earlier"]["pct"]), day(s["earlier"]["t"])) if s["earlier"]
                             else s["changed"]) if s["changed"] else "-",
                            f1(s["rate_b"] / GB) if s["rate_b"] is not None else "-",
                            ("now" if s["days_full"] == 0 else whole(s["days_full"])) if s["days_full"] is not None else "-"],
                           ["", "", "warning", "critical" if s["pct"] >= vc else ("warning" if s["pct"] >= vw else "")],
                           "warning", v["spark"], weekly(v["vals"], "Volume", v["name"], v["c"]["_name"])))
        v["shown"] = True
    again_rows.sort(key=lambda r: (0 if r[0][2].startswith("Back") else 1, -float(r[0][3])))
    few_again = ["%s (%d runs after the change)" % (v["name"], v["s"]["after"]) for v in vols
                 if v["s"]["back_again"] and v["s"]["rate_b"] is None]
    D.table("Volumes filling again",
            "Back again: %s%% used or more now and also within the last %d days, with a drop below it in between. Filling fast: "
            "reaches %s%% within %d days at its rate. What changed says only what the numbers show, with the peak used %% before "
            "it. The rate is over the runs after the change." % (n(vw), again, n(vc), fast),
            ["Volume", "Cluster / SVM", "Why", "Used % now", "What changed", "Fill GB/day", "Days to full"],
            again_rows[:max_rows], "No volume is back over %s%% and none reaches %s%% within %d days." % (n(vw), n(vc), fast),
            nums=("Used % now", "Fill GB/day", "Days to full"), wrap=("Volume", "Cluster / SVM", "Why", "What changed"),
            titles={"What changed": "What changed, and the peak used % before it with its date",
                    "Fill GB/day": "Fill GB/day (the runs after the last change)", "Days to full": "Days to full (100%) at that rate"},
            trend=trend("used %", vw, vc, " Upright line: the change."),
            lines=(["A rate needs %d runs after the change: %s." % (MIN_POINTS, ", ".join(few_again[:5]))] if few_again else [])
            + (["The first %d of %d are shown; the workbook has all." % (max_rows, len(again_rows))] if len(again_rows) > max_rows else []))

    growing = sorted([v for v in vols if v["s"]["rate_b"] is not None and v["s"]["rate_b"] > 0], key=lambda v: -v["s"]["rate_b"])
    rows = []
    for v in growing[:top]:
        s = v["s"]
        ch = v["pts"][s["change"]][0] if s["change"] is not None else None
        rows.append(([v["name"], "%s / %s" % (v["c"]["_name"], v["svm"]), f1(v["pts"][-1][1] / TB), f1(s["pct"]), f1(s["rate_b"] / GB),
                      ("now" if s["days_full"] == 0 else whole(s["days_full"])) if s["days_full"] is not None else "-"],
                     ["", "", "", "critical" if s["pct"] >= vc else ("warning" if s["pct"] >= vw else "")], "",
                     spark(v["vals"], since, now, [vw, vc], change_t=ch, what="Used %% of %s" % v["name"]),
                     None if v.get("shown") else weekly(v["vals"], "Volume", v["name"], v["c"]["_name"])))
        v["shown"] = True
    n_few = sum(1 for v in vols if v["s"]["rate_b"] is None)
    D.table("Fastest growing volumes", "The %d volumes whose used space grows fastest, whatever their used %%. Volumes of "
            "%s GB or more (ontap_volume_min_gb)." % (top, n(float(o.get("volume_min_gb", 50) or 0))),
            ["Volume", "Cluster / SVM", "Size TB", "Used % now", "Growth GB/day", "Days to full"], rows,
            "No volume is growing." if vols and n_few < len(vols) else "No rate yet.",
            nums=("Size TB", "Used % now", "Growth GB/day", "Days to full"), wrap=("Volume", "Cluster / SVM"),
            trend=trend("used %", vw, vc, " Upright line: a change (resized, or used space dropped)."),
            lines=_few(["x"] * n_few, len(vols)) if n_few == len(vols) else (
                ["%d of %d volumes have fewer than %d runs after their last change: no rate yet." % (n_few, len(vols), MIN_POINTS)] if n_few else []))

    # ---- snapshot space ----------------------------------------------------------------------------------
    rows = []
    n_over = 0
    for v in vols:
        pts = snapser.get((v["c"]["_id"], v["uid"])) or []
        if not pts or pts[-1][0] != v["pts"][-1][0] or not pts[-1][1]:
            continue
        vals = [(x[0], pct(x[2], x[1])) for x in pts if x[1]]
        p = vals[-1][1]
        if p < sw * NEAR_RESERVE / 100.0:
            continue
        over = p >= sw
        n_over += 1 if over else 0
        st = "critical" if p >= sc else ("warning" if over else "")
        rows.append(([v["name"], "%s / %s" % (v["c"]["_name"], v["svm"]), f1(pts[-1][1] / GB), f1(p),
                      "Spills into the volume" if p >= 100 else ("Over %s%% of its reserve" % n(sw) if over else "Near its reserve")], ["", "", "", st, st], st,
                     spark(vals, since, now, [sw, sc], what="Reserve used %% of %s" % v["name"]),
                     weekly(vals, "Snapshot reserve", v["name"], v["c"]["_name"])))
        points_rows += [[iso(x[0]), v["c"]["_name"], "Snapshot reserve", v["name"], v["svm"], int(x[1]), int(x[2]),
                         round(pct(x[2], x[1]), 2)] for x in pts if x[1]]
    rows.sort(key=lambda r: -float(r[0][3]))
    D.table("Snapshot space", "Volumes whose snapshots use %s%% of their snapshot reserve or more. Over 100%% the snapshots "
            "take space from the volume itself. Volumes without a reserve are left out." % n(sw * NEAR_RESERVE / 100.0),
            ["Volume", "Cluster / SVM", "Reserve GB", "Reserve used % now", "Note"], rows[:max_rows],
            "No volume's snapshots are near their reserve.", nums=("Reserve GB", "Reserve used % now"),
            wrap=("Volume", "Cluster / SVM", "Note"), trend=trend("reserve used %", sw, sc),
            lines=["The first %d of %d are shown; the workbook has all." % (max_rows, len(rows))] if len(rows) > max_rows else [])

    # ---- the numbers ---------------------------------------------------------------------------------------
    heads = weeks([], since, now)[0]
    step = max(1, -(-max(1, int(-(-(now - since) // (7 * DAY)))) // WEEK_COLUMNS))
    numbers = [([w[0], w[1], w[2]] + [f1(x) if x is not None else "" for x in w[3]], [], "", None, None) for w in D.numbers]
    for v in vols:
        if v.get("shown"):
            points_rows += [[iso(x[0]), v["c"]["_name"], "Volume", v["name"], v["svm"], int(x[1]), int(x[2]), round(pct(x[2], x[1]), 2)]
                            for x in v["pts"]]
    for web in (False, True):
        (D.web if web else D.sections).append(dict(
            {"title": "The numbers", "collapsed": True,
             "text": "The used %% behind every trend above, the last value read in each %s ending on the date (month-day, UTC). "
                     "Empty: no run then. The workbook has every point." % ("week" if step == 1 else "%d weeks" % step)},
            **({"columns": ["What", "Name", "Cluster"] + heads, "kinds": ["", "wrap", "wrap"] + ["num"] * len(heads),
                "rows": [r[0] for r in numbers]} if numbers else {"text": "No trend yet."})))

    # ---- how this was made -------------------------------------------------------------------------------
    cn = o.get("counts") or {}
    made = ["%s in the last %s days (ontap_capacity_days), this one included: %d from AAP's job history, %d from the report database%s."
            % (_count(len(runs), "run"), n(win), int(cn.get("aap", 0)), int(cn.get("db", 0)),
               " (%d more in both, counted once)" % int(cn["both"]) if cn.get("both") else "")]
    if runs:
        made.append("The oldest point: %s UTC." % iso(runs[0]["_t"]).replace("T", " ")[:16])
    made += [str(x) for x in (o.get("history_notes") or []) if x]
    made.append("A rate is the slope of the used space over the runs (for a volume: the runs after its last change). "
                "It needs %d runs." % MIN_POINTS)
    made.append("A change is a size that changed by more than %s%%, or used space that fell by %s%% of the size or more, between two runs."
                % (n(RESIZE_PCT), n(DROP_POINTS)))
    made.append("For a volume that is back again, the change is its drop below %s%% (ontap_volume_warn_pct)." % n(vw))
    for web in (False, True):
        (D.web if web else D.sections).append({"title": "How this was made", "collapsed": True, "lines": made})

    # ---- title, tiles ---------------------------------------------------------------------------------------
    fastest = growing[0] if growing else None
    summary = [
        {"label": "Clusters read", "value": "%d of %d" % (len(up), len(cs)), "status": "ok" if len(up) == len(cs) else "critical"},
        {"label": "Aggregates %s%%+" % n(aw), "value": aggr_warn, "status": "warning" if aggr_warn else "ok"},
        {"label": "Aggregates full in %d days" % AGGR_SOON, "value": aggr_soon, "status": "critical" if aggr_red else ("warning" if aggr_soon else "ok"),
         "note": "reach %s%%" % n(ac)},
        {"label": "Fastest growth (GB/day)", "value": f1(fastest["s"]["rate_b"] / GB) if fastest else "-", "status": "info",
         "note": fastest["name"] if fastest else "no rate yet"},
        {"label": "Snapshots over reserve", "value": n_over, "status": "warning" if n_over else "ok"},
        {"label": "Volumes filling fast", "value": n_fast, "status": "warning" if n_fast else "ok",
         "note": "%s%% within %d days" % (n(vc), fast)},
        {"label": "Volumes back again", "value": n_again, "status": "warning" if n_again else "ok",
         "note": "over %s%% again within %d days" % (n(vw), again)}]
    status = "critical" if aggr_red or len(up) < len(cs) else ("warning" if aggr_warn or aggr_soon or n_over or n_fast or n_again else "ok")
    bits = []
    if aggr_soon:
        bits.append("%s full within %d days" % (_count(aggr_soon, "aggregate"), AGGR_SOON))
    if n_again:
        bits.append("%s back again" % _count(n_again, "volume"))
    if n_fast:
        bits.append("%d filling fast" % n_fast)
    title = "NetApp ONTAP capacity trends: " + (", ".join(bits) if bits else (
        "nothing filling fast on %s" % _count(len(up), "cluster") if len(runs) >= MIN_POINTS else "run %d - a trend needs %d" % (len(runs), MIN_POINTS)))
    base = {"title": title, "status": status, "summary": summary,
            "subtitle": "Read %s UTC - %s in the last %s days" % (iso(now).replace("T", " ")[:16], _count(len(runs), "run"), n(win))}
    xlsx = [
        {"name": "Volumes", "columns": ["Cluster", "SVM", "Volume", "Size GB", "Used GB", "Used %", "Runs", "Growth GB/day", "Days to full",
                                        "Why", "What changed", "Earlier peak %", "Earlier peak (UTC)"],
         "rows": [[v["c"]["_name"], v["svm"], v["name"], round(v["pts"][-1][1] / GB, 1), round(v["pts"][-1][2] / GB, 1), round(v["s"]["pct"], 1),
                   len(v["pts"]), round(v["s"]["rate_b"] / GB, 2) if v["s"]["rate_b"] is not None else "",
                   round(v["s"]["days_full"], 1) if v["s"]["days_full"] is not None else "",
                   ", ".join(w for w, on in (("Back again", v["s"]["back_again"]), ("Filling fast", v["s"]["fast"])) if on),
                   v["s"]["changed"], round(v["s"]["earlier"]["pct"], 1) if v["s"]["earlier"] else "",
                   iso(v["s"]["earlier"]["t"]) if v["s"]["earlier"] else ""]
                  for v in sorted(vols, key=lambda v: (v["c"]["_name"], v["svm"], v["name"]))]},
        {"name": "Points", "columns": ["Read (UTC)", "Cluster", "What", "Name", "SVM", "Size bytes", "Used bytes", "Used %"],
         "rows": sorted(points_rows, key=lambda r: (r[2], r[1], r[3], r[0]))}]
    for s in D.sections:
        if s.get("columns") and s["title"] not in ("The numbers",):
            xlsx.insert(len(xlsx) - 2, {"name": s["title"][:31], "columns": s["columns"], "rows": s["rows"]})
    lines = [title] + ["%s: %s%s" % (t["label"], t["value"], " (%s)" % t["note"] if t.get("note") else "") for t in summary] + made[:2]
    return {"report": dict(base, sections=D.sections), "web": _capacity_web(base, D.web), "xlsx": xlsx, "lines": lines,
            "counts": {"clusters": len(cs), "read": len(up), "runs": len(runs), "volumes": len(vols), "aggr_warn": aggr_warn,
                       "aggr_soon": aggr_soon, "snapshots_over": n_over, "filling_fast": n_fast, "back_again": n_again}}


def _capacity_web(base, sections):
    """The website's document: the report with a Trend column. A function of its own, so tests/report_fit_docs.py keeps
    it (it keeps what a filter's function returns) and tests/test_report_fit.py measures the tables with their lines."""
    return dict(base, sections=sections)


def ontap_capacity_db_rows(point):
    """This run's point as the report database's rows (site_ontap_capacity_db): [[cluster, cluster_name, kind, uuid, name,
    svm, size_b, used_b, reserve_pct, snap_used_b]] - a row per cluster, aggregate and volume."""
    names = (point or {}).get("names") or {}
    out = []
    for c in (point or {}).get("clusters") or []:
        nm = names.get(c["uuid"]) or {}
        if c.get("size_b"):
            out.append([c["uuid"], c.get("name", ""), "cluster", c["uuid"], c.get("name", ""), "", c["size_b"], c.get("used_b", 0), None, None])
        for a in c.get("aggregates") or []:
            out.append([c["uuid"], c.get("name", ""), "aggregate", a["uuid"], (nm.get("aggregates") or {}).get(a["uuid"], {}).get("name", ""),
                        "", a["size_b"], a["used_b"], None, None])
        snaps = dict((s["uuid"], s) for s in c.get("snapshot_reserve") or [])
        for v in c.get("volumes") or []:
            n = (nm.get("volumes") or {}).get(v["uuid"], {})
            s = snaps.get(v["uuid"]) or {}
            out.append([c["uuid"], c.get("name", ""), "volume", v["uuid"], n.get("name", ""), n.get("svm", ""), v["size_b"], v["used_b"],
                        s.get("reserve_pct"), s.get("snap_used_b")])
    return out


class FilterModule(object):
    def filters(self):
        return {"ontap_capacity_points": ontap_capacity_points, "ontap_capacity_runs": ontap_capacity_runs,
                "ontap_capacity": ontap_capacity, "ontap_capacity_db_rows": ontap_capacity_db_rows}
