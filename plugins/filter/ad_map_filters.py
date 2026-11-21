# -*- coding: utf-8 -*-
"""1.101.0: the AD health report's replication map (the report website) and "How AD sites were matched".

ad_repl_map(repl, swis, opts) -> {sections: [...], extra: {kind: admap, data, motion} | {}, lines: [...]}
  repl: _ad.repl (the DCs with their AD site, the replication links); swis: what site_ad_swis read (its dcs with their
  SolarWinds node, and netmap: where the network map puts those nodes - its own model, with the netmap_* settings);
  opts: ad_site_map, low_hours, warn_hours, crit_hours, motion, time_zone.

Each AD site is matched to a network-map site, in this order: ad_site_map (set by hand); the DCs' own nodes (each DC found in
SolarWinds, all of the site's in one network-map site - no naming luck needed); the same name in any case; the same code
without separators. The rest: not matched (said, "set ad_site_map"). Shown, never counted (counted: false)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import re

MARK = "@@ADMAP@@"
INF = 10 ** 6


def _utc(text):
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[ T](\d\d):(\d\d)(?::(\d\d))?", str(text or ""))
    return datetime.datetime(*[int(g) for g in m.groups()[:5]] + [int(m.group(6) or 0)]) if m else None


def _code(x):
    return re.sub(r"[^A-Za-z0-9]", "", str(x or "")).upper()


def _cls(h, fail, o):
    if fail or h >= o["crit"]:
        return "critical"
    if h >= o["warn"]:
        return "warning"
    if o["low"] and h >= o["low"]:
        return "low"
    return "ok"


def _match(sites, dcs_of, node_of, nm, site_map):
    """AD site -> (network-map site or "", by, why)."""
    names = list(nm.get("names") or [])
    no_site = nm.get("no_site") or "Not placed"
    by_lower = dict((n.lower(), n) for n in names)
    by_code = {}
    for n in names:
        by_code.setdefault(_code(n), []).append(n)
    sm = dict((str(k).lower(), str(v)) for k, v in (site_map or {}).items())
    node_site = nm.get("node_site") or {}
    out = {}
    for s in sites:
        if s.lower() in sm:
            out[s] = (sm[s.lower()], "ad_site_map", "set in all.yml")
            continue
        dcs = dcs_of.get(s) or []
        nodes = [node_of.get(d.lower()) for d in dcs]
        placed = [node_site.get(str(n)) for n in nodes if n is not None and node_site.get(str(n))]
        placed = [p for p in placed if p != no_site]
        if placed and len(set(placed)) == 1:
            out[s] = (placed[0], "the DCs' nodes", "%d of %d DC%s %s SolarWinds node%s in %s" % (
                len(placed), len(dcs), "" if len(dcs) == 1 else "s", "is a" if len(placed) == 1 else "are", "" if len(placed) == 1 else "s", placed[0]))
            continue
        split = "; its DCs' nodes are in %d network-map sites" % len(set(placed)) if len(set(placed)) > 1 else ""
        if s.lower() in by_lower:
            out[s] = (by_lower[s.lower()], "name", "the same name" + split)
            continue
        c = by_code.get(_code(s)) or []
        if len(c) == 1:
            out[s] = (c[0], "code", "the same code without separators (%s)%s" % (c[0], split))
            continue
        out[s] = ("", "none", ("no DC of it is in SolarWinds" if not placed else "its DCs are in several network-map sites")
                  + " and no name or code matches - set ad_site_map")
    return out


def _pairs(links, site_of, now, o):
    """The replication links -> (between sites {(a, b): agg}, inside a site {site: {(dc, dc): agg}})."""
    between, inside = {}, {}
    for ln in links or []:
        a, b = str(ln.get("frm") or ""), str(ln.get("to") or "")
        sa, sb = site_of.get(a.lower()), site_of.get(b.lower())
        if not a or not b or sa is None or sb is None:
            continue
        ls = _utc(ln.get("last_success"))
        h = (now - ls).total_seconds() / 3600.0 if ls else INF
        fail = int(ln.get("failures") or 0) > 0
        if sa != sb:
            k = tuple(sorted([sa, sb]))
            agg = between.setdefault(k, {})
        else:
            k = tuple(sorted([a, b], key=str.lower))
            agg = inside.setdefault(sa, {}).setdefault(k, {})
        if not agg:
            agg.update(h=0.0, fail=False, err="", parts=[], ls="", lf="", n=0)
        agg["n"] += 1
        agg["h"] = max(agg["h"], h)
        agg["fail"] = agg["fail"] or fail
        if fail and not agg["err"]:
            agg["err"] = str(ln.get("error") or "")
        nc = str(ln.get("nc") or "")
        if nc and nc not in agg["parts"]:
            agg["parts"].append(nc)
        if ln.get("last_success") and (not agg["ls"] or str(ln["last_success"]) < agg["ls"]):
            agg["ls"] = str(ln["last_success"])                      # the oldest of the pair's last successes: its worst
        if fail and ln.get("last_failure") and str(ln["last_failure"]) > agg["lf"]:
            agg["lf"] = str(ln["last_failure"])
    for agg in list(between.values()) + [x for v in inside.values() for x in v.values()]:
        agg["cls"] = _cls(agg["h"], agg["fail"], o)
        agg["h"] = None if agg["h"] >= INF else round(agg["h"], 1)
    return between, inside


def ad_repl_map(repl, swis=None, opts=None):
    r, sw_, o0 = repl or {}, swis or {}, opts or {}
    o = {"low": float(o0.get("low_hours") or 0), "warn": float(o0.get("warn_hours") or 6), "crit": float(o0.get("crit_hours") or 24)}
    try:
        now = _utc(r.get("now")) or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        dcs = [d for d in r.get("dcs") or [] if d.get("name")]
        site_of = dict((str(d["name"]).lower(), str(d.get("site") or "")) for d in dcs if d.get("site"))
        if len(set(site_of.values())) < 1:
            return {"sections": [], "extra": {}, "lines": ["Replication map: no AD site read"]}
        dcs_of = {}
        for d in dcs:
            if d.get("site"):
                dcs_of.setdefault(str(d["site"]), []).append(str(d["name"]))
        node_of = dict((str(x.get("dc") or "").lower(), x.get("node_id")) for x in sw_.get("dcs") or [] if x.get("node_id") is not None)
        nm = sw_.get("netmap") or {}
        sites = sorted(dcs_of, key=str.lower)
        match = _match(sites, dcs_of, node_of, nm if nm.get("ok") else {}, o0.get("ad_site_map") or {})
        between, inside = _pairs(r.get("links"), site_of, now, o)
        places = nm.get("places") or {} if nm.get("ok") else {}
        wan = {}
        for w in nm.get("wan") or [] if nm.get("ok") else []:
            wan[tuple(sorted([w["a"], w["b"]]))] = w
        data = {"sites": [], "links": [], "inside": {}, "scale": o, "wan_read": bool(nm.get("ok")),
                "placed_by": "netmap" if nm.get("ok") else ""}
        for s in sites:
            ns, by, why = match.get(s, ("", "none", ""))
            data["sites"].append({"n": s, "dcs": sorted(dcs_of[s], key=str.lower), "nm": ns, "by": by, "why": why,
                                  "p": places.get(ns) if ns else None})
        for (a, b), agg in sorted(between.items()):
            na, nb = match.get(a, ("",))[0], match.get(b, ("",))[0]
            w = wan.get(tuple(sorted([na, nb]))) if na and nb and na != nb else None
            data["links"].append(dict(agg, a=a, b=b, wan=({"n": w["n"], "bps": w["bps"], "util": w["util"], "status": w["status"], "ifs": w["ifs"]}
                                                          if w else None), nma=na, nmb=nb))
        for s, pr in inside.items():
            data["inside"][s] = [dict(agg, a=k[0], b=k[1]) for k, agg in sorted(pr.items())]
        rank = {"critical": 0, "warning": 1, "low": 2, "ok": 3}
        worst = min([x["cls"] for x in data["links"]] + [x["cls"] for v in data["inside"].values() for x in v] or ["ok"], key=lambda c: rank[c])
        how = ("Sites placed where the network map places them (%s)." % ("the SolarWinds snapshot" if nm.get("source") == "db-snapshot" else "read live from SolarWinds")
               if nm.get("ok") else "Not placed: %s." % (nm.get("why") or "no SolarWinds credential (From SolarWinds) - the sites in a ring"))
        sec_map = {"title": "Replication map", "counted": False, "status": {"critical": "critical", "warning": "warning"}.get(worst, "info"),
                   "text": MARK, "web_only": True, "note": how}
        rows = [[x["n"], x["nm"] or "-", x["by"] if x["by"] != "none" else "none", str(len(x["dcs"])), x["why"]] for x in data["sites"]]
        sec_match = {"title": "How AD sites were matched", "counted": False, "status": "info",
                     "text": ("Each AD site to a network-map site (its place, its WAN links): ad_site_map first, then the DCs' own nodes in "
                              "SolarWinds (all of a site's DCs in one network-map site - no naming luck needed), then the same name in any "
                              "case, then the same code without separators. %s" % how),
                     "columns": ["AD site", "Network map site", "Matched by", "DCs", "Why"], "kinds": ["", "", "nw", "num", "wrap"],
                     "rows": rows, "cell_status": [["", "", "critical" if x[2] == "none" else "", "", ""] for x in rows]}
        n_none = sum(1 for x in data["sites"] if x["by"] == "none")
        lines = ["Replication map: %d AD site(s), %d between them, %d matched to the network map%s" % (
            len(sites), len(data["links"]), len(sites) - n_none, (", %d not (set ad_site_map)" % n_none) if n_none else "")]
        return {"sections": [sec_map, sec_match], "extra": {"kind": "admap", "data": data,
                                                             "motion": o0.get("motion") if o0.get("motion") in ("always", "off") else ""},
                "lines": lines}
    except Exception as e:  # noqa: BLE001 - never the job's result
        return {"sections": [], "extra": {}, "lines": ["Replication map: not made (%s)" % e.__class__.__name__]}


def ad_map_sections(sections, web=True):
    """The map's sections for one form: the website gets both; the email and the workbook only the matching table."""
    return [s for s in sections or [] if web or not s.get("web_only")]


class FilterModule(object):
    def filters(self):
        return {"ad_repl_map": ad_repl_map, "ad_map_sections": ad_map_sections}
