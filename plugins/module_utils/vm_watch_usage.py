"""The VM outage watch's usage-alarm policy, shared by the live read and both timelines."""
import datetime
import math
import re
from collections import defaultdict

DEFAULT_ALARMS = ["CPU usage", "memory usage"]
DISK_USAGE = re.compile(r"\bdisk (?:space )?(?:usage|utili[sz]ation)\b|\busage on disk\b", re.I)


def alarm_kind(name, fragments):
    name = str(name or "").lower()
    normalized = re.sub(r"utili[sz]ation", "usage", name)
    for fragment in fragments:
        match = str(fragment or "").strip().lower()
        if match and (match in name or match in normalized or match == "disk usage" and DISK_USAGE.search(name)):
            return fragment
    return ""


def timestamp(value):
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        seconds = float(value)
        return seconds if math.isfinite(seconds) else None
    except (ValueError, TypeError):
        try:
            date = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return (date if date.tzinfo else date.replace(tzinfo=datetime.timezone.utc)).timestamp()
        except (ValueError, OverflowError):
            return None


def _entity(row):
    name = row.get("entity") or row.get("vm") or row.get("host") or ""
    kind = row.get("entity_type") or ("host" if name == row.get("host") else "vm")
    return kind, row.get("entity_moid") or row.get("moid") or name, name


def usage_window(events, states, read_at, begin, end, opts):
    """Return ordinary events and one summary per vCenter, alarm fragment, entity type and colour.
    Only a matching transition or an observed state can close a red interval. Clip before measuring.
    """
    fragments = opts.get("usage_alarms", DEFAULT_ALARMS)
    minimum = max(0, float(opts.get("usage_min_minutes", 15))) * 60
    show_yellow = opts.get("usage_show_yellow", False)
    ordinary, changes, groups = [], defaultdict(list), defaultdict(dict)
    begin, end, read_at = map(timestamp, (begin, end, read_at))

    def identity(row):
        kind, obj, _ = _entity(row)
        return row.get("vcenter", ""), kind, obj, row["alarm"].lower()

    def count(row, color, start, stop):
        if start is None or stop is None or begin is None or end is None or end <= begin:
            return
        start, stop = max(begin, start), min(end, stop)
        seconds = stop - start
        if seconds < 0 or (color == "red" and seconds < minimum) or (color == "yellow" and not show_yellow):
            return
        kind, obj, name = _entity(row)
        if not obj or color not in ("red", "yellow"):
            return
        group = groups[row.get("vcenter", ""), alarm_kind(row["alarm"], fragments), kind, color]
        before = group.get(obj)
        group[obj] = (name or obj, min(start, before[1]) if before else start,
                      max(seconds, before[2]) if before else seconds)

    for row in events or []:
        if not isinstance(row, dict) or row.get("type") != "AlarmStatusChangedEvent" or not alarm_kind(row.get("alarm"), fragments):
            ordinary.append(row)
            continue
        changes[identity(row)].append(row)

    pending = {}
    capped = set(opts.get("capped") or [])
    for key, rows in changes.items():
        # A missing transition time makes the sequence ambiguous.
        if any(timestamp(row.get("time")) is None for row in rows):
            continue
        for row in sorted(rows, key=lambda r: (timestamp(r["time"]), r.get("key") or 0)):
            at = timestamp(row["time"])
            # The lookback and in-window reads have separate caps. A missing page between
            # them could contain a recovery, so never pair across that gap.
            if key[0] in capped and begin is not None and at < begin:
                continue
            was, color = str(row.get("from") or "").lower(), str(row.get("to") or "").lower()
            previous = pending.get(key)
            if previous and (not was or was == previous[0]):
                count(row, previous[0], previous[1], at)
                if color == previous[0]:
                    continue
            pending[key] = color, at
            if color == "yellow":
                count(row, color, at, at)

    for row in states or []:
        if not alarm_kind(row.get("alarm"), fragments) or read_at is None:
            continue
        color = str(row.get("status") or "").lower()
        start = timestamp(row.get("time"))
        previous = pending.get(identity(row))
        if previous and previous[1] <= read_at:
            if previous[0] == color:
                start = max(start, previous[1]) if start is not None else previous[1]
            elif start is not None and start <= previous[1]:
                start = None
        count(row, color, read_at if color == "yellow" and start is None else start, read_at)

    summaries = []
    for (vc, fragment, kind, color), members in sorted(groups.items()):
        word = "VM" if kind == "vm" else kind
        duration = " %d+ min" % (min(val[2] for val in members.values()) // 60) if color == "red" else ""
        names = ", ".join(sorted(val[0] for val in members.values()))
        summary = "%d %s%s: %s %s%s (%s)" % (len(members), word, "" if len(members) == 1 else "s", fragment, color, duration, names)
        group_id = "vm-watch:usage:%s:%s:%s" % (kind, fragment.lower(), color)
        summaries.append(dict(id=group_id, type="VmWatchUsage", vcenter=vc, time=min(val[1] for val in members.values()),
                              message=summary, severity="critical" if color == "red" else "warning",
                              fragment=fragment, entity_type=kind, color=color))
    return ordinary, summaries
