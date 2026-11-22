# -*- coding: utf-8 -*-
"""1.101.0: the AD health report's replication map (the report website) and "How AD sites were matched".

ad_repl_map(repl, swis, opts) -> {sections: [...], extra: {kind: admap, data, motion} | {}, lines: [...]}
  repl: _ad.repl (the DCs with their AD site, the replication links); swis: what site_ad_swis read (its dcs with their
  SolarWinds node, and netmap: where the network map puts those nodes - its own model, with the netmap_* settings);
  opts: ad_site_map, low_hours, warn_hours, crit_hours, motion, time_zone.

Each AD site is matched to a network-map site, in this order: ad_site_map (set by hand); the DCs' own nodes (each DC found in
SolarWinds, all of the site's in one network-map site - no naming luck needed); the same site code without separators; the
same name in any case. The rest: not matched (said, "set ad_site_map"). Shown, never counted (counted: false)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import importlib.util
import os

# the matching and the pairs: plugins/module_utils/site_ad_layer.py (1.102.0: shared with the network map's AD layer)
_spec = importlib.util.spec_from_file_location(
    "site_ad_layer", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "module_utils", "site_ad_layer.py"))
_al = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_al)

MARK = "@@ADMAP@@"
INF = _al.INF
_utc, _code, _cls, _match, _pairs = _al.utc, _al.code, _al.cls, _al.match, _al.pairs


def ad_repl_map(repl, swis=None, opts=None):
    r, sw_, o0 = repl or {}, swis or {}, opts or {}
    o = {"low": float(o0.get("low_hours") or 0), "warn": float(o0.get("warn_hours") or 6), "crit": float(o0.get("crit_hours") or 24)}
    try:
        now = _utc(r.get("now")) or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        # the DCs, their sites and nodes: one rule with the network map's AD layer (site_ad_layer.index, 1.102.0 review)
        site_of, dcs_of, node_of, _dup = _al.index(r.get("dcs"), sw_.get("dcs"))
        if not dcs_of:
            return {"sections": [], "extra": {}, "lines": ["Replication map: no AD site read"]}
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
                     "text": ("Each AD site to a network-map site (its place, its WAN links): ad_site_map first (set by hand), then the DCs' own "
                              "nodes in SolarWinds (all of a site's DCs in one network-map site - no naming luck needed), then the same site "
                              "code without separators, then the same name in any case. %s" % how),
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


def ad_layer_artifact(repl, swis=None, opts=None):
    """1.102.0: what this AD run keeps for the network map's AD layer (job artifact ad_repl, its "layer"): the scale, each AD
    site's DCs (with their SolarWinds node ids when read), the replication between AD sites. {} when it cannot be made."""
    try:
        return _al.compact(repl, opts, (swis or {}).get("dcs") or [], (opts or {}).get("orion") or "")
    except Exception:  # noqa: BLE001 - never the job's result
        return {}


class FilterModule(object):
    def filters(self):
        return {"ad_repl_map": ad_repl_map, "ad_map_sections": ad_map_sections, "ad_layer_artifact": ad_layer_artifact}
