# -*- coding: utf-8 -*-
"""AD sites on the network map: the matching and the replication between AD sites, shared by the AD report's replication
map (plugins/filter/ad_map_filters.py, 1.101.0) and the network map's AD layer (1.102.0: the netmap job's page, from the
AD job's artifact ad_repl; the portal's live map, from the report database's ad_dc / ad_link rows). One copy of the rules,
so the AD report's "How AD sites were matched" and the network map's never disagree.

match(): each AD site to a network-map site - ad_site_map (set by hand), the DCs' own nodes (all of a site's DCs in one
network-map site), the same site code without separators, the same name in any case; the rest not matched ("set
ad_site_map"). pairs(): the replication links -> between AD sites and inside one, each with its worst age, failing,
partitions. compact(): what the AD job keeps for the network map (artifact ad_repl's "layer"). layer(): that, matched to a
network map's data, as the page draws it. Standard library only (the portal imports it too)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import re

INF = 10 ** 6
VERSION = 1                     # the compact form's version (artifact ad_repl.layer.v)
MAX_SITES = 1000                # AD sites in a layer at most (a forest bigger than that: the first ones)
MAX_PAIRS = 3000                # site pairs at most


def utc(text):
    """'2026-11-13T06:00:05Z' / '2026-11-13 06:00' / a datetime -> a naive UTC datetime, or None."""
    if isinstance(text, datetime.datetime):
        return text.replace(tzinfo=None)
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[ T](\d\d):(\d\d)(?::(\d\d))?", str(text or ""))
    return datetime.datetime(*[int(g) for g in m.groups()[:5]] + [int(m.group(6) or 0)]) if m else None


def code(x):
    return re.sub(r"[^A-Za-z0-9]", "", str(x or "")).upper()


def scale(opts):
    """The matrix's scale from the AD settings (low_hours, warn_hours, crit_hours) -> {low, warn, crit}."""
    o = opts or {}

    def pick(a, b, d):                                   # a 0 given is a 0 (no Low); not given: the AD job's default
        v = o.get(a, o.get(b))
        return float(d if v is None or v == "" else v)
    return {"low": pick("low_hours", "low", 4), "warn": pick("warn_hours", "warn", 6) or 6, "crit": pick("crit_hours", "crit", 24) or 24}


def cls(h, fail, o):
    if fail or h >= o["crit"]:
        return "critical"
    if h >= o["warn"]:
        return "warning"
    if o["low"] and h >= o["low"]:
        return "low"
    return "ok"


def match(sites, dcs_of, node_of, nm, site_map):
    """AD site -> (network-map site or "", by, why). nm: {names, no_site, node_site {node id: site}}."""
    no_site = nm.get("no_site") or "Not placed"
    names = [str(n) for n in nm.get("names") or [] if n and str(n) != no_site and str(n) != "No site"]   # never "Not placed"
    by_lower = dict((n.lower(), n) for n in names)
    by_code = {}
    for n in names:
        if code(n):                                      # a name of only separators has no code
            by_code.setdefault(code(n), []).append(n)
    # 1.102.0 review: ad_site_map keys and values in any case - a value is the map's own site name when it names one
    sm = dict((str(k).strip().lower(), by_lower.get(str(v).strip().lower(), str(v).strip())) for k, v in (site_map or {}).items())
    node_site = nm.get("node_site") or {}
    out = {}
    for s in sites:
        if s.lower() in sm:
            t = sm[s.lower()]
            if names and t not in names:                 # a site the map does not have, or "Not placed" (1.102.0 review)
                out[s] = ("", "none", "ad_site_map names %s, which is not a site on the network map - fix ad_site_map" % (t or '""'))
            else:
                out[s] = (t, "ad_site_map", "set in all.yml")
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
        c = by_code.get(code(s)) or [] if code(s) else []
        if len(c) == 1:                                  # the site codes first (the user: "we use site codes"), then the name
            out[s] = (c[0], "code", ("the same code (%s)" % c[0] if c[0] == s else "the same code without separators (%s)" % c[0]) + split)
            continue
        if s.lower() in by_lower:
            out[s] = (by_lower[s.lower()], "name", "the same name" + split)
            continue
        out[s] = ("", "none", ("no DC of it is in SolarWinds" if not placed else "its DCs are in several network-map sites")
                  + " and no name or code matches - set ad_site_map")
    return out


def pairs(links, site_of, now, o):
    """The replication links -> (between sites {(a, b): agg}, inside a site {site: {(dc, dc): agg}})."""
    between, inside = {}, {}
    for ln in links or []:
        a, b = str(ln.get("frm") or ""), str(ln.get("to") or "")
        sa, sb = site_of.get(a.lower()), site_of.get(b.lower())
        if not a or not b or sa is None or sb is None:
            continue
        ls = utc(ln.get("last_success"))
        h = (now - ls).total_seconds() / 3600.0 if ls else INF
        fail = int(ln.get("failures") or 0) > 0
        if sa != sb:
            k = tuple(sorted([sa, sb]))
            agg = between.setdefault(k, {})
        else:
            k = tuple(sorted([a, b], key=str.lower))
            agg = inside.setdefault(sa, {}).setdefault(k, {})
        if not agg:
            agg.update(h=0.0, fail=False, err="", parts=[], ls="", lf="", n=0, never=False)
        agg["n"] += 1
        agg["h"] = max(agg["h"], h)
        agg["fail"] = agg["fail"] or fail
        if fail and not agg["err"]:
            agg["err"] = str(ln.get("error") or "")
        nc = str(ln.get("nc") or "")
        if nc and nc not in agg["parts"]:
            agg["parts"].append(nc)
        lst = utc(ln.get("last_success"))                          # a database's DATETIME or text, an ISO time: one form
        lst = lst.strftime("%Y-%m-%dT%H:%M:%SZ") if lst else ""
        if not lst:
            agg["never"] = True                                      # one link never replicated: the pair's age is "never"
        if lst and (not agg["ls"] or lst < agg["ls"]):
            agg["ls"] = lst                                          # the oldest of the pair's last successes: its worst
        lf = utc(ln.get("last_failure"))
        lf = lf.strftime("%Y-%m-%dT%H:%M:%SZ") if lf else ""
        if fail and lf and lf > agg["lf"]:
            agg["lf"] = lf
    for agg in list(between.values()) + [x for v in inside.values() for x in v.values()]:
        agg["cls"] = cls(agg["h"], agg["fail"], o)
        agg["h"] = None if agg["h"] >= INF else round(agg["h"], 1)
    return between, inside


def index(dcs, dc_nodes=None):
    """The DCs -> (site_of {dc name, lower case: its AD site}, dcs_of {AD site: [dc]}, node_of {dc, lower case: SolarWinds node
    id}, dup {short names more than one DC has}). One rule for the AD report's map and the network map's AD layer (1.102.0
    review): a DC name listed in two AD sites places no link (which one is meant is not known); a short name two DCs share
    (two domains' DC01) never finds a node by that short name - the replication data has no domain."""
    rows = [d for d in dcs or [] if isinstance(d, dict) and d.get("name") and d.get("site")]
    where = {}
    for d in rows:
        where.setdefault(str(d["name"]).lower(), set()).add(str(d["site"]))
    site_of = dict((k, next(iter(v))) for k, v in where.items() if len(v) == 1)
    dcs_of = {}
    for d in rows:
        lst = dcs_of.setdefault(str(d["site"]), [])
        if str(d["name"]) not in lst:
            lst.append(str(d["name"]))
    cnt = {}                                             # DC identities: a name in two AD sites is two DCs (1.102.0 review)
    for k, v in where.items():
        cnt[short(k)] = cnt.get(short(k), 0) + len(v)
    fq = {}
    for x in dc_nodes or []:
        if isinstance(x, dict) and x.get("dc"):
            fq.setdefault(short(x["dc"]), set()).add(str(x.get("fqdn") or x.get("domain") or "").lower())
    for k, v in fq.items():
        if len(v) > 1:
            cnt[k] = max(cnt.get(k, 0), len(v))
    dup = set(k for k, v in cnt.items() if v > 1)
    seen = {}
    for x in dc_nodes or []:
        k = str((x or {}).get("dc") or "").lower()
        if k:
            seen.setdefault(k, []).append(x.get("node_id"))
    node_of = dict((k, v[0]) for k, v in seen.items() if len(v) == 1 and v[0] is not None and short(k) not in dup)
    return site_of, dcs_of, node_of, dup


def orion(url):
    """Which SolarWinds a node id belongs to: its address's host and port, lower case ('orion.example.mil:17774'); "" when
    not known. Node ids are an Orion's own: a saved id is used only on the map of the same Orion (1.102.0 review)."""
    t = str(url or "").strip().lower()
    if not t or "@" in t:
        return ""
    t = re.sub(r"^[a-z]+://", "", t).split("/", 1)[0]
    if not re.match(r"^[a-z0-9._-]+(:\d{1,5})?$", t):
        return ""
    return t if ":" in t else t + ":17774"


def compact(repl, opts=None, dc_nodes=None, orion_url=""):
    """What the AD job keeps for the network map's AD layer (artifact ad_repl's "layer"): the scale, each AD site's DCs, the
    DCs' SolarWinds node ids when the AD job read SolarWinds, and the replication between AD sites (worst first). Small:
    no DC-to-DC rows. repl: _ad.repl ({now, dcs [{name, site}], links [{frm, to, nc, last_success, failures, error, ...}]});
    dc_nodes: site_ad_swis's dcs [{dc, node_id}]."""
    r = repl or {}
    o = scale(opts)
    now = utc(r.get("now")) or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    site_of, dcs_of, nodes, dup = index(r.get("dcs"), dc_nodes)
    names = set(str(d.get("name") or "").lower() for d in r.get("dcs") or [] if isinstance(d, dict))
    fqdn = {}                                            # a DC's full name, when SolarWinds' read of the DCs knew it
    for x in dc_nodes or []:
        if isinstance(x, dict) and x.get("dc") and x.get("fqdn") and short(x["dc"]) not in dup:
            fqdn[str(x["dc"]).lower()] = str(x["fqdn"]).lower()
    # how many DCs' replication was read ("read": false - no rights, not reached): none read is no layer (1.102.0 review)
    read = sum(1 for d in r.get("dcs") or [] if isinstance(d, dict) and d.get("name") and d.get("read", True) is not False)
    between, _inside = pairs(r.get("links"), site_of, now, o)
    rank = {"critical": 0, "warning": 1, "low": 2, "ok": 3}
    ps = sorted(between.items(), key=lambda kv: (rank[kv[1]["cls"]], -(kv[1]["h"] if kv[1]["h"] is not None else INF), kv[0]))
    sites = sorted(dcs_of, key=str.lower)[:MAX_SITES]
    return {"v": VERSION, "read_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "scale": o,
            "dcs": dict((s, sorted(dcs_of[s], key=str.lower)) for s in sites),
            "nodes": dict((k, v) for k, v in nodes.items() if k in site_of), "dup": sorted(dup), "orion": orion(orion_url),
            "fqdn": dict((k, v) for k, v in fqdn.items() if k in names), "read": read,
            "pairs": [{"a": a, "b": b, "h": g["h"], "cls": g["cls"], "fail": g["fail"], "err": g["err"][:200], "parts": g["parts"][:8],
                       "ls": g["ls"], "never": g["never"], "n": g["n"]} for (a, b), g in ps[:MAX_PAIRS]]}


def short(name):
    """A node's or a DC's short name in lower case: 'dc01.corp.example.mil' -> 'dc01'."""
    return str(name or "").strip().split(".")[0].lower()


def layer(c, page, site_map=None, no_site="Not placed", why="", source="", now=None, orion_url=None):
    """The network map's AD layer, as the page draws it (its data's "ad"), from compact() and the map's own data (page_data:
    sites, nodes [{id, name, site}]). A DC's node: the id the AD job read, else the network map's node of the same short
    name (one only). -> {why, source, read_at, scale, sites [{n, nm, by, why, dcs}], links [{a, b, nma, nmb, h, cls, fail,
    err, parts, n}], same: pairs inside one network-map site (not drawn), none: AD sites not matched}. Never raises: a
    broken input is a why."""
    if why or not isinstance(c, dict) or not c.get("dcs"):
        return {"why": why or "no AD replication read", "source": source}
    if c.get("read") == 0:                               # every DC's replication unread (rights, reach): nothing to draw
        return {"why": "no DC's replication could be read in the AD run of %s (the AD report says why)" % (c.get("read_at") or "?"), "source": source}
    try:
        p = page or {}
        nodes = [n for n in p.get("nodes") or [] if isinstance(n, dict) and not n.get("gone")]
        node_site = dict((str(n.get("id")), str(n.get("site") or "")) for n in nodes if n.get("id") is not None)
        by_short = {}
        for n in nodes:
            if n.get("id") is not None:
                by_short.setdefault(short(n.get("name")), []).append(n["id"])
        names_of = dict((str(n["id"]), str(n.get("name") or "").strip().lower()) for n in nodes if n.get("id") is not None)
        dcs_of = dict((str(s), [str(d) for d in v or []]) for s, v in (c.get("dcs") or {}).items())
        allk = [d.lower() for v in dcs_of.values() for d in v]       # one entry per AD site a DC is in: identities
        dup = set(c.get("dup") or []) | set(x for x in set(short(k) for k in allk) if sum(1 for k in allk if short(k) == x) > 1)
        # saved ids only from the same SolarWinds (an id is an Orion's own), never for a shared short name (1.102.0 review)
        same = bool(c.get("orion")) and orion_url is not None and c.get("orion") == orion(orion_url)
        node_of = dict((str(k).lower(), v) for k, v in (c.get("nodes") or {}).items() if same and short(k) not in dup)
        fq = dict((str(k).lower(), str(v).lower()) for k, v in (c.get("fqdn") or {}).items())
        for k in set(allk):                              # else the map's node of that name
            if k in node_of or short(k) in dup:
                continue
            cand = by_short.get(short(k), [])
            if len(cand) != 1:
                continue
            nn, full = names_of.get(str(cand[0]), ""), fq.get(k) or (k if "." in k else "")
            if full and "." in nn and nn != full:        # both with a domain: only the same full name
                continue
            node_of[k] = cand[0]
        names = [str(s) for s in p.get("sites") or []]
        sites = sorted(dcs_of, key=str.lower)
        m = match(sites, dcs_of, node_of, {"names": names, "no_site": no_site, "node_site": node_site}, site_map or {})
        out_sites = [{"n": s, "nm": m[s][0], "by": m[s][1], "why": m[s][2], "dcs": len(dcs_of[s])} for s in sites]
        o = scale(c.get("scale") or {})
        t = utc(c.get("read_at"))
        nw = utc(now) if now is not None else datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        # 1.102.0 review: the ages as of now, not as of the AD run - a reading never looks fresher than it is; an AD run
        # older than the scale's top makes every line red (stale)
        late = max(0.0, (nw - t).total_seconds() / 3600.0) if t and nw else 0.0
        links, same = [], 0
        for x in c.get("pairs") or []:
            a, b = str(x.get("a") or ""), str(x.get("b") or "")
            na, nb = m.get(a, ("",))[0], m.get(b, ("",))[0]
            if na and nb and na == nb:
                same += 1
                continue
            h = x.get("h")
            h = None if h is None else round(float(h) + late, 1)
            lsx = str(x.get("ls") or "")
            fail = bool(x.get("fail"))
            links.append({"a": a, "b": b, "nma": na, "nmb": nb, "h": h, "cls": cls(INF if h is None else h, fail, o) if t else str(x.get("cls") or "critical"),
                          "fail": fail, "err": str(x.get("err") or ""), "parts": list(x.get("parts") or []), "n": int(x.get("n") or 1),
                          "ls": lsx, "never": bool(x.get("never")) or h is None})
        return {"why": "", "source": source, "read_at": str(c.get("read_at") or ""), "as_of": t.strftime("%Y-%m-%d %H:%M UTC") if t else "",
                "age_h": round(late, 1), "stale": bool(late >= o["crit"]) or not t, "scale": o,
                "sites": out_sites, "links": links, "same": same, "none": sum(1 for s in out_sites if s["by"] == "none")}
    except Exception as e:  # noqa: BLE001 - never the map's
        return {"why": "the AD layer could not be made (%s)" % e.__class__.__name__, "source": source}


def from_rows(read_at, dc_rows, link_rows, opts=None):
    """The report database's newest AD run (ad_run.read_at, ad_dc rows (dc, site[, repl_read]), ad_link rows (frm, to_dc, nc,
    last_success, failures, error, last_failure)) -> compact(). The ages as of the AD run's read."""
    dcs = [dict({"name": str(r[0]), "site": str(r[1] or "")}, **({"read": bool(r[2])} if len(r) > 2 and r[2] is not None else {}))
           for r in dc_rows or [] if r and r[0] and r[1]]           # (dc, site[, repl_read]): no DC's replication read is no layer
    links = [{"frm": r[0], "to": r[1], "nc": r[2], "last_success": r[3], "failures": r[4], "error": r[5], "last_failure": r[6]}
             for r in link_rows or [] if r and r[0] and r[1]]
    t = utc(read_at)
    return compact({"now": t.strftime("%Y-%m-%dT%H:%M:%SZ") if t else "", "dcs": dcs, "links": links}, opts)
