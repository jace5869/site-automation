# -*- coding: utf-8 -*-
"""The portal's NetApp page (/portal/netapp): the arithmetic its writer (roles/ontap_report/library/site_ontap_live_db.py)
uses - the growth of a volume or an aggregate from its points in the report database, the days until full (or until the
aggregate limit), and a row's status from the same settings the health report and the expand job use. Standard library
only; the tests import it as it is."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import hashlib
import json

GB = 1024.0 ** 3
DAY = 86400.0
MIN_GB_DAY = 0.01           # under 10 MB a day is not filling (the days until full would read in millions)


def row_key(*parts):
    """A fixed-width key over a row's identity (the names can be longer than an index allows)."""
    return hashlib.sha1(json.dumps([str(p or "") for p in parts], separators=(",", ":")).encode("utf-8")).hexdigest()


def rate(n, t0, t1, sx, sy, sxx, sxy, min_points=3, min_hours=6.0):
    """The least-squares slope of the used space over time, from the sums the database gives (x in days, y in GB) ->
    (GB a day or None, why not). Fewer than min_points points, or points over less than min_hours: no rate - at a 15-minute
    refresh three points are 30 minutes, and a day's rate from them is noise."""
    n = int(n or 0)
    span_h = (float(t1 or 0) - float(t0 or 0)) / 3600.0 if n else 0.0
    if n < int(min_points) or span_h < float(min_hours):
        return None, "rate after %s h (%d of %d points)" % (_num(min_hours), min(n, int(min_points)), int(min_points))
    den = n * float(sxx) - float(sx) ** 2
    if den <= 0:
        return None, "the points are all at one time"
    slope = (n * float(sxy) - float(sx) * float(sy)) / den
    # the sums cancel: points that do not move leave a rounding residue (1e-9 GB a day and the like), not a growth -
    # it is 0 when under a millionth of the mean used a day
    if abs(slope) < 1e-6 * max(1.0, abs(float(sy) / n)):
        slope = 0.0
    return slope, ""


def _num(v):
    v = float(v)
    return ("%d" % v) if v == int(v) else ("%.1f" % v)


def days_until(free_b, gb_day):
    """Days until free_b bytes are used at gb_day GB a day; None when not filling (under MIN_GB_DAY, or no rate); 0 when there now."""
    if gb_day is None or gb_day < MIN_GB_DAY:
        return None
    return max(0.0, float(free_b) / GB / gb_day)


def level(pct, warn, crit):
    return "critical" if pct >= crit else ("warning" if pct >= warn else "ok")


def days_level(days, warn, crit):
    if days is None:
        return "ok"
    return "critical" if days < crit else ("warning" if days < warn else "ok")


RANK = {"critical": 0, "warning": 1, "unknown": 2, "ok": 3, "": 4}


def worst(*sts):
    s = sorted((x for x in sts if x), key=lambda x: RANK.get(x, 4))
    return s[0] if s else "ok"


def volume_status(used_pct, days, o):
    """A volume's state now: its used % (ontap_volume_warn_pct, ontap_volume_crit_pct) and its days until full
    (ontap_live_days_warn, ontap_live_days_crit)."""
    return worst(level(used_pct, float(o.get("volume_warn_pct", 85)), float(o.get("volume_crit_pct", 90))),
                 days_level(days, float(o.get("days_warn", 30)), float(o.get("days_crit", 7))))


def aggr_status(used_pct, days_limit, o):
    """An aggregate's state now: its used % (ontap_aggr_warn_pct, ontap_aggr_crit_pct) and its days until the expand job's
    limit (ontap_expand_aggr_max_pct) - against the same day settings as a volume."""
    return worst(level(used_pct, float(o.get("aggr_warn_pct", 85)), float(o.get("aggr_crit_pct", 90))),
                 days_level(days_limit, float(o.get("days_warn", 30)), float(o.get("days_crit", 7))))
