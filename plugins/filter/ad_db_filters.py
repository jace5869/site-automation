# -*- coding: utf-8 -*-
"""1.101.2: what the AD health job keeps of each run in the report database (roles/ad_report/library/site_ad_db.py) for the
portal's live AD page (/portal/ad): the run, each DC's state and each replication link's. Read only: from what the report
read (_ad). Never raises: a run it cannot make rows of is kept without them (said).

ad_db_payload(ad, live, opts) -> {status, critical, warning, dcs: [...], links: [...], live, why}"""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import re

RANK = {"critical": 0, "warning": 1, "unknown": 2, "low": 3, "info": 4, "ok": 5, "": 6}


def _utc(text):
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d)(?::(\d\d))?", str(text or ""))
    if not m:
        return None
    return datetime.datetime(*[int(x or 0) for x in m.groups()])


def link_status(link, now, opts):
    """A replication link's state on the matrix's scale: failing or no success in crit_hours critical, warn_hours warning,
    low_hours low, else ok; '' when its last success is not known and it is not failing."""
    if int(link.get("failures") or 0) > 0:
        return "critical"
    t = _utc(link.get("last_success"))
    if t is None or now is None:
        return ""
    h = (now - t).total_seconds() / 3600.0
    o = opts or {}
    return ("critical" if h >= float(o.get("crit_hours", 24)) else "warning" if h >= float(o.get("warn_hours", 6))
            else "low" if h >= float(o.get("low_hours", 4)) else "ok")


def ad_db_payload(ad, live=None, opts=None):
    try:
        return _payload(ad, live, opts)
    except Exception as e:  # noqa: BLE001 - the run is still kept, without its rows
        return {"status": str((ad or {}).get("report", {}).get("status") or ""), "critical": 0, "warning": 0, "dcs": [], "links": [],
                "live": live or {}, "why": "the DCs and links were not made into rows (%s)" % e.__class__.__name__}


def _payload(ad, live, opts):
    a = ad or {}
    probs = {}
    for p in a.get("problems") or []:
        if isinstance(p, dict) and p.get("object"):
            k = (str(p.get("domain") or "").lower(), str(p.get("object")).lower())
            probs.setdefault(k, []).append((str(p.get("severity") or ""), str(p.get("text") or "")))
    repl = a.get("repl") or {}
    site = dict((str(d.get("name") or "").lower(), str(d.get("site") or "")) for d in repl.get("dcs") or [] if isinstance(d, dict))
    dcs = []
    for d in a.get("dcs") or []:
        name = str(d.get("dc") or "")
        if not name:
            continue
        ps = sorted(probs.get((str(d.get("domain") or "").lower(), name.lower()), []), key=lambda x: RANK.get(x[0], 6))
        if not d.get("read"):
            st, worst = "critical", "not read: %s" % (d.get("error") or "no answer")
        else:
            st = ps[0][0] if ps else "ok"
            worst = ps[0][1] if ps else ""
        dcs.append({"dc": name[:255], "domain": str(d.get("domain") or "")[:255], "site": site.get(name.lower(), "")[:255],
                    "read": bool(d.get("read")), "status": st[:16], "problems": len(ps), "worst": worst[:300]})
    now = _utc(repl.get("now"))
    links = []
    for x in repl.get("links") or []:
        if not isinstance(x, dict) or not x.get("frm") or not x.get("to"):
            continue
        links.append({"frm": str(x["frm"])[:255], "to": str(x["to"])[:255], "nc": str(x.get("nc") or "")[:255],
                      "last_success": x.get("last_success") or "", "failures": int(x.get("failures") or 0),
                      "error": str(x.get("error") or "")[:255], "last_failure": x.get("last_failure") or "",
                      "status": link_status(x, now, opts)})
    return {"status": str((a.get("report") or {}).get("status") or ""), "critical": int(a.get("critical") or 0),
            "warning": int(a.get("warning") or 0), "dcs": dcs, "links": links, "live": live or {}, "why": ""}


class FilterModule(object):
    def filters(self):
        return {"ad_db_payload": ad_db_payload}
