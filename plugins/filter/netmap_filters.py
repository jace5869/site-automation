# -*- coding: utf-8 -*-
"""The network map's report (roles/netmap_report, docs/NETWORK_MAP.md): what site_swis_collect read from SolarWinds as
the report (email, website), its workbook, the job's lines, and the map itself on the website page (netmap_page_extra:
the map's markup, data, style and script, put into the page by report_page_extend)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type


MARK = "@@NETMAP@@"                     # the map section's place in the page (report_page_extend puts the map there)
ORDER = {"Down": 0, "Warning": 1, "Unknown": 2, "Up": 3}
WORD = {"Down": "DOWN", "Warning": "WARNING", "Unknown": "UNKNOWN", "Up": "Up"}
FROM = {"worldmap": "SolarWinds' Worldwide Map", "property": "custom properties", "setting": "netmap_sites"}


def _o(opts):
    o = dict(opts or {})
    o["max_rows"] = int(o.get("max_rows") or 500)
    return o


def _plural(n, word, many=None):
    return "%d %s" % (n, word if n == 1 else (many or word + "s"))


def _cut(rows, o, sec, what):
    if len(rows) > o["max_rows"]:
        sec["text"] = ((sec.get("text") or "") + " %d more %s not listed here (netmap_max_rows: %d): the workbook has all."
                       % (len(rows) - o["max_rows"], what, o["max_rows"])).strip()
        sec["rows"] = rows[:o["max_rows"]]
        sec["row_status"] = sec["row_status"][:o["max_rows"]]
    return sec


def _wan(m):
    """The WAN links (between two sites), worst and busiest first: [(link, a, b)]."""
    ids = dict((n["id"], n) for n in m.get("nodes") or [])
    out = [(l, ids[l["a"]], ids[l["b"]]) for l in m.get("links") or [] if l.get("wan") and not l.get("gone") and l["a"] in ids and l["b"] in ids]
    return sorted(out, key=lambda x: (ORDER.get(x[0]["status"], 9), -max(x[0].get("in", 0), x[0].get("out", 0)), x[1]["site"], x[2]["site"]))


def netmap_doc(res, opts=None):
    """The report: {title, status, subtitle, summary, sections}. opts: max_rows, web (true: the map section first, for the
    website), last (when the kept copy was made), why_not_compared, version_text."""
    o = _o(opts)
    r = res or {}
    m = r.get("map") or {}
    nodes = [n for n in m.get("nodes") or [] if not n.get("gone")]
    cnt = dict((k, sum(1 for n in nodes if n["status"] == k)) for k in ORDER)
    wan = _wan(m)
    wan_down = [w for w in wan if w[0]["status"] == "Down"]
    wan_warn = [w for w in wan if w[0]["status"] == "Warning"]
    changes = r.get("changes") or []
    sites = [s for s in m.get("sites") or [] if s.get("nodes")]
    unplaced = [s for s in sites if s.get("lat") is None]
    ver = r.get("version") or {}
    vtext = "SolarWinds %s" % ver["platform"] if ver.get("platform") else "SolarWinds"
    sections = []
    if not r.get("reachable", True):
        sections.append({"title": "SolarWinds not read", "status": "critical", "lines": [r.get("error") or "no answer"],
                         "text": "Nothing below is from this run: fix the reason, then run the job again."})
        return {"title": "Network map: SolarWinds not read", "status": "critical",
                "subtitle": "%s (SWIS, read-only) - %s" % (vtext, r.get("url") or ""),
                "summary": [{"label": "SolarWinds", "value": "not read", "status": "critical", "note": "the reason is below"}],
                "sections": sections}
    if o.get("web"):
        sections.append({"title": "The map", "status": "info", "text": MARK})
    bad = sorted([n for n in nodes if n["status"] in ("Down", "Warning")], key=lambda n: (ORDER[n["status"]], n["site"].lower(), n["name"].lower()))
    if bad:
        sec = {"title": "Down or warning (%d)" % len(bad), "status": "critical" if cnt["Down"] else "warning",
               "columns": ["Status", "Node", "Site", "Vendor / model", "IP", "SolarWinds says"],
               "rows": [[WORD[n["status"]], n["name"], n["site"], ("%s %s" % (n["vendor"], n["model"])).strip() or "-", n["ip"] or "-", n.get("why") or "-"] for n in bad],
               "row_status": ["critical" if n["status"] == "Down" else "warning" for n in bad]}
        sections.append(_cut(sec["rows"], o, sec, "nodes"))
    if wan:
        sec = {"title": "WAN links (%d%s)" % (len(wan), ", %d down" % len(wan_down) if wan_down else ""),
               "status": "critical" if wan_down else "warning" if wan_warn else "info",
               "text": "The links between two sites, worst and busiest first. In = into the first node, out = out of it.",
               "columns": ["Sites", "Link", "Bandwidth", "In %", "Out %", "Status"],
               "rows": [["%s ↔ %s" % (a["site"], b["site"]), "%s %s ↔ %s %s" % (a["name"], l["ia"], b["name"], l["ib"]), l.get("speed") or "-",
                         l.get("in", "-") if l["status"] != "Down" else "-", l.get("out", "-") if l["status"] != "Down" else "-",
                         WORD[l["status"]] + (": " + l["why"] if l.get("why") and l["status"] != "Up" else "")] for l, a, b in wan],
               "row_status": ["critical" if l["status"] == "Down" else "warning" if l["status"] == "Warning" else "" for l, a, b in wan]}
        sections.append(_cut(sec["rows"], o, sec, "links"))
    if changes:
        sec = {"title": "What changed since the last run (%d)" % len(changes), "status": "info",
               "text": "Compared with the kept copy of %s." % (o.get("last") or "the last run"),
               "columns": ["Change", "What", "Site", "Detail"],
               "rows": [[c["kind"], c["what"], c["site"], c.get("detail") or "-"] for c in changes],
               "row_status": ["critical" if c["kind"].endswith("→ Down") else "warning" if c["kind"] in ("Missing node", "Missing link") or c["kind"].endswith("→ Warning") else "" for c in changes]}
        sections.append(_cut(sec["rows"], o, sec, "changes"))
    if sites:
        rows = [[s["name"], s["nodes"], s["down"], s["warning"], WORD.get(s["worst"], s["worst"]),
                 ("%.2f, %.2f" % (s["lat"], s["lon"])) if s.get("lat") is not None else "Unplaced",
                 FROM.get(s.get("from"), "-")] for s in sites]
        sec = {"title": "Sites (%d)" % len(sites), "status": "info",
               "columns": ["Site", "Nodes", "Down", "Warning", "Worst", "Lat, long", "Placed by"],
               "rows": rows, "row_status": ["critical" if s["down"] else "" for s in sites]}
        sections.append(_cut(rows, o, sec, "sites"))
    if unplaced:
        sections.append({"title": "Unplaced sites (%d)" % len(unplaced), "status": "warning",
                         "text": "No coordinates from SolarWinds' Worldwide Map or the custom properties: give each one in "
                                 "netmap_sites (all.yml), e.g. {\"%s\": {lat: 39.4, lon: -93.2}}. They are in the Sites view and "
                                 "these tables as any other site." % unplaced[0]["name"],
                         "lines": ["%s (%s)" % (s["name"], _plural(s["nodes"], "node")) for s in unplaced]})
    nr = list(r.get("not_read") or [])
    if m.get("cut"):
        nr.append({"what": "nodes past netmap_max_nodes", "why": "only the first %d nodes were read" % len(nodes)})
    if m.get("hidden_links"):
        nr.append({"what": "%s" % _plural(m["hidden_links"], "link"), "why": "to a node this account may not see (its limitation in SolarWinds) - left out"})
    if nr:
        sections.append({"title": "Not all read", "status": "warning", "lines": ["%s: %s" % (x["what"], x["why"]) for x in nr]})
    links = [l for l in m.get("links") or [] if not l.get("gone")]
    title = "Network map: %d down, %d warning - %s, %s" % (cnt["Down"], cnt["Warning"], _plural(len(sites), "site"), _plural(len(nodes), "node"))
    if wan_down:
        title += " - %s down" % _plural(len(wan_down), "WAN link")
    summary = [{"label": "Nodes", "value": len(nodes), "note": "%s · %s" % (_plural(len(sites), "site"), _plural(len(links), "link"))},
               {"label": "Down", "value": cnt["Down"], "status": "critical" if cnt["Down"] else "ok", "note": "no answer to SolarWinds"},
               {"label": "Warning", "value": cnt["Warning"], "status": "warning" if cnt["Warning"] else "ok", "note": "up, with a problem"},
               {"label": "WAN links down", "value": len(wan_down), "status": "critical" if wan_down else "ok", "note": "of %d" % len(wan)}]
    if r.get("compared"):
        summary.append({"label": "Changed", "value": len(changes), "status": "info", "note": "since the last run"})
    if unplaced:
        summary.append({"label": "Unplaced sites", "value": len(unplaced), "status": "warning", "note": "netmap_sites"})
    sub = "%s (SWIS, read-only) · %s" % (vtext, ("compared with %s" % o["last"]) if r.get("compared") and o.get("last")
                                         else "not compared: %s" % (o.get("why_not_compared") or "no kept copy of an earlier run"))
    return {"title": title, "status": "critical" if cnt["Down"] or wan_down else "warning" if cnt["Warning"] or wan_warn or unplaced else "ok",
            "subtitle": sub, "summary": summary, "sections": sections}


def netmap_xlsx(res):
    """The workbook: Nodes, Links, WAN links, Sites, Changes - a row each, nothing cut."""
    r = res or {}
    m = r.get("map") or {}
    ids = dict((n["id"], n) for n in m.get("nodes") or [])
    props = sorted(set(k for n in m.get("nodes") or [] for k in (n.get("props") or {})))
    nodes = sorted(m.get("nodes") or [], key=lambda n: (n["site"].lower(), ORDER.get(n["status"], 9), n["name"].lower()))
    links = [l for l in m.get("links") or [] if l["a"] in ids and l["b"] in ids]
    sheets = [
        {"name": "Nodes", "columns": ["Node", "Status", "Site", "Role", "IP", "Vendor", "Model", "Location", "Response", "SolarWinds says",
                                      "Change", "NodeID"] + props,
         "rows": [[n["name"], "Missing" if n.get("gone") else n["status"], n["site"], n.get("role", ""), n.get("ip", ""), n.get("vendor", ""),
                   n.get("model", ""), n.get("loc", ""), n.get("resp", ""), n.get("why", ""),
                   "missing since the last run" if n.get("gone") else "new" if n.get("new") else ("%s → %s" % (n["was"], n["status"]) if n.get("was") else ""),
                   n["id"]] + [(n.get("props") or {}).get(p, "") for p in props] for n in nodes]},
        {"name": "Links", "columns": ["Node A", "Interface A", "Node B", "Interface B", "Site A", "Site B", "WAN", "Speed", "In %", "Out %", "Status", "Why", "Change"],
         "rows": [[ids[l["a"]]["name"], l.get("ia", ""), ids[l["b"]]["name"], l.get("ib", ""), ids[l["a"]]["site"], ids[l["b"]]["site"],
                   "yes" if l.get("wan") else "", l.get("speed", ""), l.get("in", ""), l.get("out", ""), "Missing" if l.get("gone") else l["status"],
                   l.get("why", ""), "missing since the last run" if l.get("gone") else "new" if l.get("new") else ""] for l in links]},
        {"name": "WAN links", "columns": ["Site A", "Site B", "Node A", "Interface A", "Node B", "Interface B", "Speed", "In %", "Out %", "Status", "Why"],
         "rows": [[a["site"], b["site"], a["name"], l["ia"], b["name"], l["ib"], l.get("speed", ""), l.get("in", ""), l.get("out", ""), l["status"], l.get("why", "")]
                  for l, a, b in _wan(m)]},
        {"name": "Sites", "columns": ["Site", "Nodes", "Down", "Warning", "Unknown", "Worst", "Latitude", "Longitude", "Placed by"],
         "rows": [[s["name"], s["nodes"], s["down"], s["warning"], s.get("unknown", 0), s["worst"], s["lat"] if s.get("lat") is not None else "",
                   s["lon"] if s.get("lon") is not None else "", FROM.get(s.get("from"), "unplaced")] for s in m.get("sites") or []]},
        {"name": "Changes", "columns": ["Change", "What", "Site", "Detail"],
         "rows": [[c["kind"], c["what"], c["site"], c.get("detail", "")] for c in r.get("changes") or []]}]
    return sheets


def netmap_lines(res):
    """The job's lines: the counts, the version, each site."""
    r = res or {}
    if not r.get("reachable", True):
        return ["SolarWinds not read: %s" % (r.get("error") or "no answer")]
    m = r.get("map") or {}
    c = m.get("counts") or {}
    v = r.get("version") or {}
    out = ["SolarWinds %s at %s: %d nodes (%d down, %d warning), %d links, %d sites; %d queries in %s s" % (
        v.get("platform") or "(version not read)", r.get("url") or "", sum(c.values()), c.get("Down", 0), c.get("Warning", 0),
        len([l for l in m.get("links") or [] if not l.get("gone")]), len(m.get("sites") or []), r.get("queries", 0), r.get("seconds", 0))]
    if v.get("modules"):
        out.append("Modules: " + ", ".join(v["modules"]))
    out.append("Topology from %s" % (m.get("topology") or "nowhere (no links)"))
    for s in m.get("sites") or []:
        out.append("%s: %d nodes, %d down, %d warning - %s" % (s["name"], s["nodes"], s["down"], s["warning"],
                   ("%.3f, %.3f (%s)" % (s["lat"], s["lon"], FROM.get(s["from"], s["from"]))) if s.get("lat") is not None else "unplaced"))
    out.extend("Not read - %s: %s" % (x["what"], x["why"]) for x in r.get("not_read") or [])
    out.append("Changes since the last run: %d" % len(r.get("changes") or []) if r.get("compared") else "Not compared with an earlier run")
    return out


def netmap_page_extra(res, motion="auto"):
    """The map's piece of the website page, as site_email takes it (_report_page_extra) and keeps it for the copy job:
    {kind: netmap, data, motion} - data only; the markup, style and script come from this repository's files
    (plugins/filter/report_page_filters.py, report_page_piece), in the report job and in the copy job alike."""
    r = res or {}
    if not r.get("reachable", True) or not isinstance(r.get("data"), dict):
        return {}
    return {"kind": "netmap", "data": r["data"], "motion": motion if motion in ("always", "off") else ""}


def netmap_summary_counts(res):
    """The job artifact netmap: the counts and what changed, for workflows."""
    r = res or {}
    m = r.get("map") or {}
    return {"reachable": r.get("reachable", False), "counts": m.get("counts") or {}, "sites": len(m.get("sites") or []),
            "unplaced": m.get("unplaced") or [], "wan_down": len([w for w in _wan(m) if w[0]["status"] == "Down"]),
            "changes": len(r.get("changes") or []), "version": (r.get("version") or {}).get("platform", ""), "read_at": r.get("read_at", "")}


class FilterModule(object):
    def filters(self):
        return {"netmap_doc": netmap_doc, "netmap_xlsx": netmap_xlsx, "netmap_lines": netmap_lines,
                "netmap_page_extra": netmap_page_extra, "netmap_summary_counts": netmap_summary_counts}
