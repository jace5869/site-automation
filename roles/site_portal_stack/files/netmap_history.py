"""Network map history for the portal. Times in the model are UTC epoch seconds."""
import datetime
import re
from bisect import bisect_left
from collections import defaultdict
from statistics import median

DAY = 86400
BUCKET = 7200
STRIP_LIMIT = 288
ROW_LIMIT = 300
ORDER = ("Down", "Warning", "Unknown", "Up")
NO_LOGIN = ('Network map history needs the "Report database" credential (report_reader) on Site portal - deploy '
            'and portal_report_db enabled. Run the deploy with that credential.')
NO_TABLES = ('Network map history tables are missing. Run Network map with netmap_db enabled and its Report database writer credential.')
NO_READ = ('No network map read yet. Run Network map with netmap_db enabled and its Report database credential.')
DB_ERROR = 'Network map history could not read the report database. Check the report_reader credential on Site portal - deploy.'


def _source(url):
    """Normalize the configured SWIS authority to the stored host:port comparison key."""
    t = str(url or "").strip().lower()
    authority = re.split(r"[/?#]", re.sub(r"^https://", "", t), maxsplit=1)[0]
    m = re.fullmatch(r"([a-z0-9._-]+|\[[0-9a-f:.]+\])(?::(\d{1,5}))?", authority)
    if not m:
        return ""
    host, port = m.groups()
    # site_swis.base_url leaves a bare bracketed address on the HTTPS default port.
    return ("%s:%s" % (host.rstrip("."), port or (443 if host.endswith("]") else 17774)))[:255]


def _gap(times, fallback):
    if len(times) < 2:
        return fallback
    return max(60, median(b - a for a, b in zip(times, times[1:])))


def _strip_bar(i, row, busy):
    pin, pout = row.get("in_pct"), row.get("out_pct")
    return {"i": i, "status": "Warning" if busy else row["status"], "height": max((v for v in (pin, pout) if v is not None), default=None),
            "in": pin, "out": pout}


def history(reads, links, sites, link_days, site_days, now, earlier_good=()):
    """Summarize one consistent snapshot. A missing key, a missed read and a long gap all break adjacency.
    Day rows retain counts and down time, but cannot supply timestamps for individual outages or chart points.
    Input lists are ordered by time. A few preceding reads give context at the left edge of the week.
    """
    if not reads:
        return {"message": NO_READ}
    start = now - 7 * DAY
    times = [r["read_at"] for r in reads]
    good = [r["read_at"] for r in reads if r["ok"]]
    cadence_times = [*earlier_good, *good]
    recent_gap = _gap(cadence_times[-50:], 1800)
    stored_gap = {}
    # usual_gap uses the previous day's maximum stored link gap, then its maximum site gap.
    for rows in (site_days, link_days):
        by_day = {}
        for r in rows:
            if r.get("gap_min") is not None:
                day = r["day"] // DAY
                by_day[day] = max(by_day.get(day, 60), float(r["gap_min"]) * 60)
        stored_gap.update(by_day)
    day_gap = {}
    for day in {t // DAY for t in good}:
        lo = bisect_left(cadence_times, (day - 3) * DAY)
        hi = bisect_left(cadence_times, (day + 4) * DAY)
        day_gap[day] = _gap(cadence_times[lo:hi], stored_gap.get(day - 1, recent_gap))
    latest = reads[-1]
    last = times[-1]
    if len(cadence_times) < 2:
        # Freshness can still use the observed cadence from across the archive.
        day_gaps = [float(r["gap_min"]) * 60 for r in link_days + site_days if r.get("gap_min")]
        recent_gap = max(60, median(day_gaps or [1800]))
    stale_after = max(1800, 2 * recent_gap)
    stale = now - last > stale_after
    index = {t: i for i, t in enumerate(times)}
    day_times = [t for t in times if now - DAY < t <= now]
    strip_times = day_times[-STRIP_LIMIT:]
    strip_good = [(i, t) for i, t in enumerate(strip_times) if reads[index[t]]["ok"]]
    first_bucket = int(start // BUCKET * BUCKET)
    week = list(range(first_bucket, int(now // BUCKET * BUCKET) + 1, BUCKET))
    counts = {r["read_at"]: [0, 0] for r in reads if r["ok"] and start < r["read_at"] <= now}
    # The writer estimates down time up to the next successful read (also past a miss), or one usual gap.
    steps = {}
    following = None
    for r in reversed(reads):
        t = r["read_at"]
        if r["ok"]:
            nominal = day_gap[t // DAY]
            midnight = (t // DAY + 1) * DAY
            step = nominal
            if following is not None and following < midnight + 3 * DAY and following - t <= 2 * nominal:
                step = following - t
            steps[t] = max(0, min(now, midnight, t + step) - max(start, t))
            following = t

    outages = []
    rolled = []
    totals = {"reads": {"shown": len(strip_times), "total": len(day_times)}}
    endpoint_counts = defaultdict(int)
    rows_at = good[-1] if good else None
    partial_day = any(d["day"] < start < d["day"] + DAY and d["down_min"] for d in link_days + site_days)

    def elements(records, days, key, kind, summary):
        by_key = defaultdict(dict)
        for row in records:
            by_key[row[key]][row["read_at"]] = row
        archive = defaultdict(list)
        for d in days:
            archive[d[key]].append(d)
        result = []
        busy_reads = set()
        for ident in by_key.keys() | archive.keys():
            seen = by_key[ident]
            daily = archive[ident]
            label_row = next(reversed(seen.values())) if seen else daily[-1]
            label = label_row["site"] if kind == "Site" else label_row["site_a"] + " ↔ " + label_row["site_b"]
            base = {"kind": kind, "key": ident, "label": label}
            if kind == "WAN link":
                base["ends"] = " ↔ ".join(n for n in (label_row.get("node_a"), label_row.get("node_b")) if n)
            down_seconds = 0
            total = 0
            for d in daily:
                if start <= d["day"] and d["day"] + DAY <= now:
                    total += d["outages"]
                    down_seconds += d["down_min"] * 60
                    if d["outages"] or d["down_min"]:
                        rolled.append(dict(base, day=d["day"], count=d["outages"], minutes=d["down_min"]))
            buckets = {}
            previous = None
            status = None
            since = None
            streak = busy = 0
            run = []
            prior_down = 0
            prior_at = None

            def finish_run(back=None, ongoing=False):
                """Count a run once, even when its second down read was in an archived day."""
                nonlocal total
                if not run or len(run) + prior_down < 2:
                    return
                seconds = sum(steps[t] for t in run)
                if run[-1] <= start and not seconds:
                    return
                if prior_down:
                    # Only the last read of that day has a timestamp. Earlier down reads could belong to other runs.
                    tail_gap = float(daily[-1]["gap_min"]) * 60 if daily[-1].get("gap_min") else day_gap[run[0] // DAY]
                    seconds += max(0, min(run[0], prior_at + tail_gap, (prior_at // DAY + 1) * DAY) - max(start, prior_at))
                if prior_down < 2:
                    confirmed = run[0] if prior_down else run[1]
                    total += int(start < confirmed <= now)
                outages.append(dict(base, start=prior_at if prior_down else run[0], back=back, counted_in_day=prior_down == 2,
                                    earlier_start=prior_down == 2 or (run[0] == times[0] and run[0] <= start),
                                    minutes=round(seconds / 60), end="Back" if back else "Still down" if ongoing else "Last seen down"))

            for t, row in seen.items():
                pos = index[t]
                if not reads[pos]["ok"]:
                    continue
                cap = 2 * day_gap[t // DAY]
                adjacent = (previous is not None and pos > 0 and times[pos - 1] == previous and t - previous <= cap
                            and (status != "Down" or previous // DAY >= t // DAY - 1))
                if not adjacent:
                    finish_run()
                    run, prior_down, prior_at = [], 0, None
                    status, streak, busy = None, 0, 0
                pin, pout = row.get("in_pct"), row.get("out_pct")
                height = max((v for v in (pin, pout) if v is not None), default=None)
                state = row["status"]
                busy = busy + 1 if state != "Down" and height is not None and height > 80 else 0
                if busy >= 3:
                    state = "Warning"
                    if strip_times and t >= strip_times[0]:
                        busy_reads.add((ident, t))
                if state != status:
                    finish_run(back=t if adjacent and state in ("Up", "Warning") else None)
                    run, prior_down, prior_at = [], 0, None
                    streak, since = 0, t
                # A rolled day can supply the predecessor of the first remaining raw read.
                if previous is None and daily and state == "Down":
                    tail = daily[-1]
                    at = tail.get("tail_at")
                    if (tail["day"] == (t // DAY - 1) * DAY and at is not None and tail["tail_down"]
                            and 0 < t - at <= cap and (pos == 0 or times[pos - 1] <= at)):
                        prior_down, prior_at = tail["tail_down"], at
                        streak, since = prior_down, at
                status, previous, streak = state, t, streak + 1
                if start < t <= now:
                    if state in ("Down", "Warning"):
                        counts[t][0 if state == "Down" else 1] += 1
                    if kind == "WAN link":
                        b = buckets.setdefault(t // BUCKET * BUCKET, {"in": None, "out": None, "down": False})
                        for k, v in (("in", pin), ("out", pout)):
                            if v is not None and (b[k] is None or v > b[k]):
                                b[k] = v
                        b["down"] |= state == "Down"
                if state == "Down":
                    down_seconds += steps[t]
                    run.append(t)
            finish_run(ongoing=previous == last and not stale)
            summary["outages"] += total
            if rows_at not in seen:
                continue
            row = dict(seen[rows_at], **base)
            row.update(status=status, since=since, since_limited=(since == times[0] and since <= start) or prior_down == 2,
                       streak=streak, outages=total, down_min=round(down_seconds / 60))
            summary["down"] += int(status == "Down")
            if kind == "WAN link":
                row["week"] = buckets
                summary["warnings"] += int(status == "Warning")
                for name in {row["site_a"], row["site_b"]}:
                    endpoint_counts[name] += 1
            else:
                summary["nodes"] += row["nodes"]
                summary["warnings"] += row["warning"]
                row["wan_links"] = endpoint_counts[row["site"]]
            result.append(row)
        result.sort(key=lambda r: (ORDER.index(r["status"]), -r["down_min"], r["label"], r["key"]))
        summary["total"] = len(result)
        shown = result[:ROW_LIMIT]
        summary["shown"] = len(shown)
        for row in shown:
            ident = row[key]
            seen = by_key[ident]
            row["strip"] = [_strip_bar(i, seen[t], (ident, t) in busy_reads) for i, t in strip_good if t in seen]
            if kind == "WAN link":
                row["week"] = [row["week"].get(t) for t in week]
        return shown

    totals["links"] = dict(down=0, warnings=0, outages=0)
    totals["sites"] = dict(down=0, warnings=0, outages=0, nodes=0)
    link_rows = elements(links, link_days, "link_key", "WAN link", totals["links"])
    site_rows = elements(sites, site_days, "site_key", "Site", totals["sites"])
    worst = {}
    for t, pair in counts.items():
        k = t // BUCKET * BUCKET
        worst[k] = max(worst.get(k, pair), pair)
    return {"read": latest, "partial_day": partial_day, "as_of": rows_at, "stale": stale, "stale_after": stale_after, "gap": recent_gap,
            "times": strip_times, "missed": [i for i, t in enumerate(strip_times) if not reads[index[t]]["ok"]],
            "reads_ok": sum(bool(reads[index[t]]["ok"]) for t in day_times), "totals": totals,
            "links": link_rows, "sites": site_rows, "week": week, "worst": [worst.get(t) for t in week],
            "outages": sorted(outages, key=lambda o: (-o["start"], o["kind"], o["key"])),
            "days": sorted(rolled, key=lambda d: (-d["day"], d["kind"], d["key"]))}


class Database:
    """The portal supplies its existing credential/CA/timeout-aware connection factory."""
    def __init__(self, connect, swis_url=""):
        self.connect = connect
        self.source = _source(swis_url)

    def read(self, now):
        utc = datetime.timezone.utc
        end = datetime.datetime.fromtimestamp(now, utc).replace(tzinfo=None)
        start = end - datetime.timedelta(days=7)
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            cur.execute("START TRANSACTION READ ONLY")

            def epoch(value):
                dt = value if isinstance(value, datetime.datetime) else datetime.datetime.fromisoformat(str(value))
                return int(dt.replace(tzinfo=utc).timestamp())

            def select(table, fields, clause, args, limit):
                # Identifiers, clauses and bounds are constants of this reader. Only dates are parameters.
                cur.execute("SELECT %s FROM %s %s LIMIT %d" % (fields, table, clause, limit + 1), args)
                values = cur.fetchall()
                if len(values) > limit:
                    raise ValueError("history query limit reached")
                rows = [dict(zip(fields.split(", "), v)) for v in values]
                for row in rows:
                    for k in ("read_at", "day", "tail_at"):
                        if row.get(k) is not None:
                            row[k] = epoch(row[k])
                return rows

            cur.execute("SELECT version FROM schema_version WHERE component = 'netmap' LIMIT 1")
            version = cur.fetchone()
            if not version:
                return {"message": NO_TABLES}
            if version[0] != 1:
                return {"message": "Network map history needs netmap schema version 1. Check the portal and Network map job versions."}
            fields = "read_at, job, ok, source, nodes, sites, wan_links, nodes_warning, nodes_unknown"
            # A stale map may predate the entire week. Its last observations still have a time.
            cur.execute("SELECT " + fields + " FROM netmap_read WHERE read_at <= %s ORDER BY read_at DESC LIMIT 50", (start,))
            context = [dict(zip(fields.split(", "), v)) for v in reversed(cur.fetchall())]
            for r in context:
                r["read_at"] = epoch(r["read_at"])
            window = "WHERE read_at > %s AND read_at <= %s ORDER BY read_at"
            reads = context + select("netmap_read", fields, window, (start, end), 12000)
            if not reads:
                return {"message": NO_READ}
            latest_good = next((r for r in reversed(reads) if r["ok"]), None)
            if self.source:
                if latest_good is None:
                    cur.execute("SELECT source FROM netmap_read WHERE source <> '' AND read_at <= %s ORDER BY read_at DESC LIMIT 1", (end,))
                    stored = cur.fetchone()
                    source = stored[0] if stored else ""
                else:
                    source = latest_good["source"]
                if source and source != self.source:
                    return {"message": "the history is of %s; the map below reads %s" % (source, self.source)}
            first = datetime.datetime.fromtimestamp(reads[0]["read_at"], utc).replace(tzinfo=None)
            earlier_good = []
            if latest_good and context:
                # Include the left edge of the cadence window without loading more element rows.
                lo = first.replace(hour=0, minute=0, second=0) - datetime.timedelta(days=3)
                earlier_good = [r["read_at"] for r in select("netmap_read", "read_at",
                                "WHERE ok = 1 AND read_at >= %s AND read_at < %s ORDER BY read_at", (lo, first), 12000)]
            window = "WHERE read_at BETWEEN %s AND %s ORDER BY read_at"
            links = select("netmap_link", "read_at, link_key, site_a, site_b, node_a, node_b, speed, status, in_pct, out_pct",
                           window, (first, end), 200000)
            sites = select("netmap_site", "read_at, site_key, site, nodes, down, warning, unknown, status", window, (first, end), 200000)
            window = "WHERE day BETWEEN %s AND %s ORDER BY day"
            days = (start.date() - datetime.timedelta(days=1), end.date())
            totals = "outages, down_min, tail_down, tail_at, gap_min"
            link_days = select("netmap_link_day", "day, link_key, site_a, site_b, node_a, node_b, " + totals, window, days, 4000)
            site_days = select("netmap_site_day", "day, site_key, site, " + totals, window, days, 4000)
        except Exception as exc:
            if exc.args and exc.args[0] == 1146:  # MySQL ER_NO_SUCH_TABLE
                return {"message": NO_TABLES}
            raise
        finally:
            try:
                conn.rollback()
            finally:
                conn.close()
        return history(reads, links, sites, link_days, site_days, now, earlier_good)
