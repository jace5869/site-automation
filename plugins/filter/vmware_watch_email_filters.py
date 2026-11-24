# -*- coding: utf-8 -*-
"""1.118.8, the VM outage watch's email limit (vm_watch_email_min_minutes): at most one email per that many minutes.
It wraps 1.112.5's vm_watch_email_decision (plugins/filter/vmware_watch_filters.py, unchanged: the watch's detection and
its change rules stay 1.118.4's) and adds:
- the floor: an email the decision wants is held when the last one went less than min_minutes ago. The change is kept
  in the email state (held) and the first run after the window sends one email with all of it ("Since the last email").
  The first email, a change, a reminder and the Recovered email all obey it. A review (vm_watch_review) is never held.
- the report database's state (site_vm_watch_email_db) when AAP cannot be read.
- no state at all (no AAP, no report database): only a critical condition is emailed, and only by a run that starts in
  the first 15 minutes of each min_minutes slot of the clock. The job output says what to attach."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import calendar
import importlib.util
import os
import re

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("vmware_watch_filters_email", os.path.join(_here, "vmware_watch_filters.py"))
_w = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_w)

GRACE = 60.0              # seconds: a run that starts up to a minute early (the scheduler, a queue) counts as on time
SLOT_MINUTES = 15.0       # no state: the part of each slot whose runs may email (at least one run of a 15-minute schedule)
HELD_MAX = 50             # held change lines kept at most


def _ts(text):
    """'2026-11-23T06:00:05Z' (the watch's read_at) -> epoch seconds, or 0."""
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)", str(text or ""))
    return float(calendar.timegm(tuple(int(x) for x in m.groups()))) if m else 0.0


def _hhmm(ts, tz):
    shown = _w._tm.local(_w._utc(ts), tz) if tz is not None else _w._utc(ts)
    parts = str(shown).split()
    return parts[1] if len(parts) > 1 else str(shown)


def _mins(v):
    return _w._num(v)


def vm_watch_email_floor(conditions, runs=None, opts=None):
    """The watch's email decision with the floor (1.118.8). opts: those of vm_watch_email_decision, plus
    min_minutes (0 = no floor), read_at (when this run read vCenter: the floor counts from run start to run start),
    db ({ok, state, error}: the report database's state, read when AAP could not be; {} = not read), db_why (why the
    report database was not read). -> the decision's dict, plus source (aap, db or none) and held (bool)."""
    o = dict(opts or {})
    why = str(o.get("why") or "")
    db = o.get("db") or {}
    source = "aap"
    if why:
        if db.get("ok"):
            st = db.get("state")
            runs = [{"artifacts": {_w.EMAIL_STATE: st}}] if isinstance(st, dict) else []
            o["why"] = ""
            source = "db"
        else:
            source = "none"
    floor = max(0.0, float(o.get("min_minutes") or 0))
    now = float(o.get("now") or 0)
    clock = _ts(o.get("read_at")) or now
    tz = _w._tm.zone(o.get("time_zone"))[0]
    d = _w.vm_watch_email_decision(conditions, runs if source != "none" else [], o)
    d = dict(d, lines=list(d["lines"]), changed=list(d["changed"]), source=source, held=False)
    if floor <= 0:
        return d
    ids = _w.vm_watch_email_ids(conditions, o.get("report_on"))
    unlimited = ("Email on every report this run",)

    if source == "none":
        if d["review"]:                                # a review always emails
            return d
        d["lines"] = [x for x in d["lines"] if not x.startswith(unlimited)]
        d["lines"].append(
            "No AAP credential or report database: the %s-minute email limit cannot work - attach the Red Hat Ansible "
            "Automation Platform credential to VM - outage watch (AAP: %s; report database: %s)."
            % (_mins(floor), why.rstrip("."), str(o.get("db_why") or db.get("error") or "not attached").rstrip(".")))
        slot = floor * 60
        part = min(slot, SLOT_MINUTES * 60)
        critical = any(v == "critical" for v in ids.values())
        start = clock - (clock % slot)
        in_part = clock % slot < part
        send = critical and in_part
        if send:
            return dict(d, send=True, kind="unknown")
        nxt = start + slot if not in_part else clock
        d.update(send=False, kind="unknown", state=None, carry=None,
                 why=("no critical condition, and the last email is not known (no AAP credential or report database)"
                      if not critical else
                      "the last email is not known, next possible at %s (vm_watch_email_min_minutes: %s)" % (_hhmm(nxt, tz), _mins(floor))))
        return d

    if d["review"]:                                    # a review always emails; it starts the next window
        if d["state"] is not None:
            d["state"] = dict(d["state"], read_ts=clock, held=[])
        return d
    prev = _w._email_state(runs)
    last = float((prev or {}).get("read_ts") or (prev or {}).get("sent_ts") or 0)
    held = [h for h in ((prev or {}).get("held") or []) if isinstance(h, dict) and h.get("line")]
    open_ = bool(last) and clock - last < floor * 60 - GRACE      # the window since the last email is still open
    sent_at = {"sent_at": _w._utc(now), "sent_ts": now, "read_ts": clock, "held": []}

    def earlier(changed):
        return [("Earlier (%s): %s" % (_hhmm(h.get("at") or 0, tz), h["line"])) for h in held if h["line"] not in changed]

    if d["kind"] == "always":                          # vm_watch_email_on_change: false - every report, but the floor holds
        if d["send"] and open_:
            keep = dict(prev) if prev else None
            d.update(send=False, state=keep, carry=keep, held=True, why=_held_why(last, floor, tz))
            return d
        if d["send"]:
            st = dict(sent_at, ids=ids, review_ts=float((prev or {}).get("review_ts") or 0))
            d.update(state=st, carry=dict(prev) if prev else None)
        else:
            d.update(state=dict(prev) if prev else None, carry=dict(prev) if prev else None)
        return d

    if d["send"]:
        if open_:
            seen = set(h["line"] for h in held)
            more = [{"line": x, "at": now} for x in d["changed"] if x not in seen]
            keep = d["carry"]
            keep = dict(keep, held=(held + more)[-HELD_MAX:]) if keep else None
            d.update(send=False, state=keep, carry=keep, held=True, why=_held_why(last, floor, tz))
            if not d["report"]:
                d["lines"].append("No email: " + d["why"])
            return d
        d["changed"] = d["changed"] + earlier(d["changed"])
        d["state"] = dict(d["state"], **sent_at) if d["state"] is not None else None
        return d

    # nothing new for the decision, but a new or worse condition was held: the first run after the window sends it (a flap
    # that came and went, an event that aged out of the window) - not while a vCenter was not read (nothing counts as gone
    # then). A held Cleared that came back is no news: the last email said so already.
    worse = [h for h in held if str(h["line"]).startswith(("New: ", "Worse: "))]
    if worse and not open_ and not o.get("unread") and d["kind"] in ("quiet", "none"):
        base = dict(d["state"] or prev or {}, **sent_at)
        if d["kind"] == "none":
            base["ids"] = {}
        d.update(send=True, kind="change" if ids else "recovered", report=True, why="", state=base,
                 carry=dict(prev) if prev else None, changed=earlier([]))
    elif held and not open_ and not o.get("unread") and not d["send"] and isinstance(d["state"], dict):
        d["state"] = d["carry"] = dict(d["state"], held=[])          # a Cleared that came back: dropped, as no news
    return d


def _held_why(last, floor, tz):
    return "last one at %s, next possible at %s (vm_watch_email_min_minutes: %s)" % (
        _hhmm(last, tz), _hhmm(last + floor * 60, tz), _mins(floor))


class FilterModule(object):
    def filters(self):
        return {"vm_watch_email_floor": vm_watch_email_floor}
