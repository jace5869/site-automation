# -*- coding: utf-8 -*-
"""The times a report shows, in report_time_zone (1.49.0): the report website's index (every run of every report,
"updated"), the strip of past runs and the footer line of a report. What is stored stays UTC, as before
("2026-11-13 04:37 UTC" - the index.json, each run's JSON, the order of the runs): a time is turned only where it is
shown, so a run written before report_time_zone was set shows in it too - "2026-11-13 05:37 CET", summer time as it
was on that day ("2026-07-13 06:37 CEST"). "" (or UTC) = every time as it is stored. A name this execution
environment does not know (or one without zoneinfo, Python 3.9, or without the time zone data, tzdata) = UTC, with a
note for the job's output - never a failure. Shared by roles/site_email (site_report_web) and
plugins/filter/site_filters.py (which loads this file). Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import re

try:
    import zoneinfo
except ImportError:                                  # Python before 3.9
    zoneinfo = None

UTC_NAMES = ("UTC", "Z", "ETC/UTC", "GMT")
# a time as the website stores it - "2026-11-13 04:37 UTC" - and as the strip of past runs has it, "2026-11-13 04:37";
# as the reports print UTC times (1.54.0) also with seconds, "2026-10-05 03:52:47", and as ISO, "2026-10-05T03:52:47Z"
STORED = re.compile(r"^(\d{4})-(\d\d)-(\d\d)[ T](\d\d):(\d\d)(?::(\d\d)(?:\.\d+)?)?(?: UTC|Z|\+00:00)?$")


def zone(name):
    """report_time_zone -> (its tzinfo, or None for UTC only; a note when it is not a time zone known here)."""
    name = str(name or "").strip()
    if not name or name.upper() in UTC_NAMES:
        return None, ""
    if zoneinfo is None:
        return None, ("report_time_zone %s is not used: this execution environment's Python has no zoneinfo (3.9 or "
                      "later has it): the report website's times are in UTC" % name)
    try:
        return zoneinfo.ZoneInfo(name), ""
    except Exception:                                # noqa: BLE001 - unknown name, no time zone data in the EE
        return None, ("report_time_zone %s is not a time zone this execution environment knows (a name as timedatectl "
                      "list-timezones prints them, e.g. America/New_York; the execution environment needs the tzdata "
                      "package): the report website's times are in UTC" % name)


def local(text, tz=None):
    """A stored time as it is shown: '2026-11-13 04:37 UTC' -> '2026-11-13 05:37 CET' in tz (zone); with seconds, as a
    report reads them, '2026-10-05T03:52:47Z' -> '2026-10-05 05:52:47 CEST'. Without a time zone, and anything that is
    not such a time ('this run', '', a job number), as it is."""
    if tz is None:
        return text
    m = STORED.match(str(text or "").strip())
    if not m:
        return text
    g = m.groups()
    try:
        u = datetime.datetime(*[int(x) for x in g[:5]] + [int(g[5] or 0)], tzinfo=datetime.timezone.utc)
        return u.astimezone(tz).strftime("%Y-%m-%d %H:%M" + (":%S" if g[5] else "") + " %Z").strip()
    except (ValueError, OverflowError):              # the 31st of a month with 30 days: as it is
        return text


def column(name, tz=None):
    """A time column's header (1.54.0): 'When (UTC)' as it is without a time zone; with one, 'When' - each time in the
    column then says its own zone, CET or CEST by its date."""
    if tz is None:
        return name
    return str(name).replace(" (UTC)", "")


def stamp(tz=None, now=None):
    """Now, as the website stores it - '2026-11-13 04:37 UTC' - or, with a time zone, as it is shown there."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if tz is None:
        return now.strftime("%Y-%m-%d %H:%M UTC")
    return now.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z").strip()
