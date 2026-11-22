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
        return {"status": "unknown", "critical": 0, "warning": 0, "dcs": [], "links": [],
                "live": live or {}, "why": "the AD job could not make this run's DCs and links into rows (%s: %s)" % (e.__class__.__name__, str(e)[:120])}


def _payload(ad, live, opts):
    a = ad or {}
    repl = a.get("repl") or {}
    refused = a.get("unsupported") or ident.unsupported(a)
    if refused or a.get("why") == ident.REFUSAL or repl.get("why") == ident.REFUSAL:
        return {"status": "unknown", "why": ident.REFUSAL, "dcs": [], "links": [],
                "live": {"unsupported": refused}, "repl_dcs": 0, "repl_read": 0}
    probs = {}
    for p in a.get("problems") or []:
        if isinstance(p, dict) and p.get("object"):
            probs.setdefault(problem_dc(p), []).append((str(p.get("severity") or ""), str(p.get("text") or "")))
    rdcs = ident.merge(repl.get("dcs") or [])
    observations = ident.merge(a.get("dcs") or [])
    population = ident.merge(list(rdcs.values()) + list(observations.values()))
    matched = ident.merge((opts or {}).get("solarwinds", {}).get("dcs") or [])
    dcs = []
    for key, d in population.items():
        ps = sorted(probs.get(key, []), key=lambda x: RANK.get(x[0], 6))
        rd = rdcs.get(key) or {}
        polled = key in observations
        if not polled:
            st, worst = "unknown", "not polled"
        elif not d.get("read"):
            st, worst = "critical", "not read: %s" % (d.get("error") or "no answer")
        else:
            st = ps[0][0] if ps and ps[0][0] in ("critical", "warning", "low") else "ok"
            worst = ps[0][1] if st != "ok" else ""
        stale = bool(d.get("stale_dsa") or rd.get("stale_dsa"))
        if stale and polled:
            worst = "Two NTDS objects for %s - stale DSA, metadata cleanup" % key.upper()
            if st == "ok":
                st = "warning"
        sw = matched.get(key) or {}
        dcs.append({"dc": key, "domain": ident.domain(d), "site": str(d.get("site") or rd.get("site") or ""),
                    "dns_host": d.get("dns_host") or d.get("fqdn") or rd.get("fqdn") or "", "dsa_guid": d.get("dsa_guid") or rd.get("dsa_guid") or "",
                    "node_id": sw.get("node_id") if sw.get("matched") else None, "stale_dsa": stale, "match_why": sw.get("unmatched_why") or ("not in SolarWinds" if sw else "not read"),
                    "read": bool(d.get("read")) and polled, "repl_read": bool(rd.get("read")) and not rd.get("partial"),
                    "status": st, "problems": len(ps), "worst": worst[:300]})
    now = _utc(repl.get("now"))
    tsl = repl.get("tombstone_days") or 0
    links = []
    for x in repl.get("links") or []:
        if not isinstance(x, dict) or not x.get("frm") or not x.get("to"):
            continue
        links.append({"dst_id": ident.link_id(x)[0], "src_id": ident.link_id(x)[1], "nc_dn": x.get("nc_dn") or x.get("nc") or "", "domain": str(x.get("domain") or "").lower()[:255], "frm": ident.link_id(x)[1], "src": str(x.get("src") or "")[:255],
                      "to": ident.link_id(x)[0], "nc": str(x.get("nc") or "")[:255],
                      "last_success": x.get("last_success") or "", "failures": int(x.get("failures") or 0),
                      "error": str(x.get("error") or "")[:255], "last_failure": x.get("last_failure") or "",
                      "status": link_status(x, now, opts, tsl)})
    links = ident.links(links)
    live = dict(live or {}, dcs=[dict(d, fqdn=d["dns_host"]) for d in dcs],
                coverage=ident.coverage(repl), discovered=repl.get("discovered", bool(rdcs)))
    # replication coverage: every DC the replication read knows (also those AD lists that were not polled) and how many read
    return {"status": str((a.get("report") or {}).get("status") or ""), "critical": int(a.get("critical") or 0),
            "warning": int(a.get("warning") or 0), "dcs": dcs, "links": links, "live": live, "why": "",
            "repl_dcs": len(rdcs), "repl_read": sum(1 for key, d in rdcs.items() if key in observations and bool(d.get("read")) and not d.get("partial"))}


class FilterModule(object):
    def filters(self):
        return {"ad_db_payload": ad_db_payload}
