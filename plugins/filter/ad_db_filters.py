# -*- coding: utf-8 -*-
"""1.101.2: what the AD health job keeps of each run in the report database (roles/ad_report/library/site_ad_db.py) for the
portal's live AD page (/portal/ad): the run, each DC's state and each replication link's. Read only: from what the report
read (_ad). Never raises: a run it cannot make rows of is kept without them (said).

ad_db_payload(ad, live, opts) -> {status, critical, warning, dcs: [...], links: [...], live, why}"""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import importlib.util
import os
import re

# the AD matrix's own link classifier (ad_repl_filters.link_status): the job's report and the portal say the same of a link
_spec = importlib.util.spec_from_file_location("ad_repl_for_db", os.path.join(os.path.dirname(os.path.abspath(__file__)), "ad_repl_filters.py"))
_rp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rp)
ident = _rp._ad.ident
INBOUND = re.compile(r"^\s*(\S+)\s+<-\s")             # ad_filters' replication problem: "DC02 <- DC01 (Configuration)"

RANK = {"critical": 0, "warning": 1, "unknown": 2, "low": 3, "info": 4, "ok": 5, "": 6}


def _utc(text):
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d)(?::(\d\d))?", str(text or ""))
    if not m:
        return None
    return datetime.datetime(*[int(x or 0) for x in m.groups()])


def link_status(link, now, opts, tombstone_days=0):
    """A replication link's state as the AD report's matrix says it (ad_repl_filters.link_status, one rule): never
    replicated a warning, failing a warning until crit_hours without a success, low_hours 0 no Low, the tombstone
    lifetime critical. now: the run's read (naive UTC or aware)."""
    o = opts or {}
    oo = {"low": float(o.get("low_hours", 4) or 0), "warn": float(o.get("warn_hours", 6)), "crit": float(o.get("crit_hours", 24)),
          "tsl": float(tombstone_days or 0)}
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    return _rp.link_status(_rp._ad.age_hours(link.get("last_success"), now), int(link.get("failures") or 0), oo)[0]


def problem_dc(p):
    """The DC a problem is of (lower case): its structured dc, else the receiving DC of an inbound replication problem
    ("DC02 <- DC01 (Configuration)": DC02), else its object."""
    if p.get("dc"):
        return str(p["dc"]).lower()
    obj = str(p.get("object") or "")
    m = INBOUND.match(obj)
    return (m.group(1) if m else obj).lower()


def ad_db_payload(ad, live=None, opts=None):
    try:
        return _payload(ad, live, opts)
    except Exception as e:  # noqa: BLE001 - the run is still kept, without its rows, and says so (ad_run.why: the portal shows it)
        st = str(((ad or {}).get("report") or {}).get("status") or "")
        return {"status": st if st in ("critical", "warning") else "unknown", "critical": 0, "warning": 0, "dcs": [], "links": [],
                "live": live or {}, "why": "the AD job could not make this run's DCs and links into rows (%s: %s)" % (e.__class__.__name__, str(e)[:120])}


def _payload(ad, live, opts):
    a = ad or {}
    probs, by_name = {}, {}
    for p in a.get("problems") or []:
        if isinstance(p, dict) and p.get("object"):
            dom, obj = str(p.get("domain") or "").lower(), problem_dc(p)
            item = (str(p.get("severity") or ""), str(p.get("text") or ""))
            if dom:
                probs.setdefault((dom, obj), []).append(item)
            else:                                        # a problem with no domain (a security event): the DC of that name
                by_name.setdefault(obj, []).append(item)
    repl = a.get("repl") or {}
    raw = [d for d in repl.get("dcs") or [] if isinstance(d, dict) and d.get("name")]
    qualified = list(a.get("dcs") or []) + [d for d in raw if ident.domain(d)]
    rdcs = {}
    for d in raw:
        alias = dict(d)
        sites = {ident.dc_site(x) for x in raw if ident.short_name(x) == ident.short_name(d) and ident.dc_site(x)}
        if len(sites) == 1 and not any(ident.dc_site(x) for x in qualified if ident.short_name(x) == ident.short_name(d)):
            alias.pop("site", None)
        found = ident.resolve(alias, qualified) if not ident.domain(d) and not d.get("dc_id") else None
        rec = dict(found or {}, **d)
        if found:
            rec.update(dc_id=ident.dc_id(found), domain=ident.domain(found))
        rdcs[ident.dc_id(rec)] = rec
    dcs = []
    observations = {}
    for d in a.get("dcs") or []:
        found = ident.resolve(d, rdcs.values())
        observations[ident.dc_id(found or d)] = dict(d, dc_id=ident.dc_id(found or d))
    population = dict(rdcs, **observations)
    for key, d in observations.items():
        name = str(d.get("dc") or "")
        if not name:
            continue
        ps = sorted(probs.get((str(d.get("domain") or "").lower(), name.lower()), []) + (by_name.get(name.lower(), []) if len({ident.dc_id(x) for x in population.values() if ident.short_name(x) == name.lower()}) == 1 else []),
                    key=lambda x: RANK.get(x[0], 6))
        if not d.get("read"):
            st, worst = "critical", "not read: %s" % (d.get("error") or "no answer")
        else:                                            # info and the like: OK (the page's states are critical, warning, low, ok)
            st = ps[0][0] if ps and ps[0][0] in ("critical", "warning", "low") else "ok"
            worst = ps[0][1] if st != "ok" else ""
        rd = rdcs.get(key) or {}
        rr = bool(rd) and bool(rd.get("read", True)) and not rd.get("partial")
        dcs.append({"dc_id": key, "site_id": ident.dc_site(rd or d), "dc": name[:255], "domain": ident.domain(d)[:255], "site": str(rd.get("site") or d.get("site") or "")[:255],
                    "read": bool(d.get("read")), "repl_read": bool(d.get("read")) and rr, "status": st[:16], "problems": len(ps), "worst": worst[:300]})
    now = _utc(repl.get("now"))
    tsl = repl.get("tombstone_days") or 0
    links = []
    for x in repl.get("links") or []:
        if not isinstance(x, dict) or not x.get("frm") or not x.get("to"):
            continue
        links.append({"dst_id": ident.link_id(x)[0], "src_id": ident.link_id(x)[1], "nc_dn": x.get("nc_dn") or x.get("nc") or "", "domain": str(x.get("domain") or "").lower()[:255], "frm": str(x["frm"])[:255], "src": str(x.get("src") or "")[:255],
                      "to": str(x["to"])[:255], "nc": str(x.get("nc") or "")[:255],
                      "last_success": x.get("last_success") or "", "failures": int(x.get("failures") or 0),
                      "error": str(x.get("error") or "")[:255], "last_failure": x.get("last_failure") or "",
                      "status": link_status(x, now, opts, tsl)})
    # replication coverage: every DC the replication read knows (also those AD lists that were not polled) and how many read
    return {"status": str((a.get("report") or {}).get("status") or ""), "critical": int(a.get("critical") or 0),
            "warning": int(a.get("warning") or 0), "dcs": dcs, "links": links, "live": live or {}, "why": "",
            "repl_dcs": len(rdcs), "repl_read": sum(1 for key, d in rdcs.items() if key in observations and bool(d.get("read", True)) and not d.get("partial"))}


class FilterModule(object):
    def filters(self):
        return {"ad_db_payload": ad_db_payload}
