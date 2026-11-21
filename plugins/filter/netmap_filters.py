# -*- coding: utf-8 -*-
"""The network map's report (roles/netmap_report, docs/NETWORK_MAP.md): what site_swis_collect read from SolarWinds as
the report (email, website), its workbook, the job's lines, and the map itself on the website page (netmap_page_extra:
the map's markup, data, style and script, put into the page by report_page_extend)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import importlib.util
import os

# the discovery settings' check (1.95.0): plugins/module_utils/site_swis.py, loaded as ad_filters.py loads site_time
_spec = importlib.util.spec_from_file_location(
    "site_swis", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "module_utils", "site_swis.py"))
_sw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sw)


def netmap_check_opts(o):
    """The portal deploy's netmap settings -> what is wrong with them ([] = fine): the job's own check (check_opts)."""
    return _sw.check_opts(o if isinstance(o, dict) else {})


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
    unplaced = [s for s in sites if s.get("lat") is None and s["name"] != m.get("no_site")]   # 1.95.0: "Not placed" is no site
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
        nb = {}
        for n in nodes:
            if n.get("building"):
                nb.setdefault(n["site"], set()).add(n["building"])
        rows = [[s["name"], s["nodes"], len(nb.get(s["name"], ())), s["down"], s["warning"], WORD.get(s["worst"], s["worst"]),
                 ("%.2f, %.2f" % (s["lat"], s["lon"])) if s.get("lat") is not None else "Unplaced",
                 FROM.get(s.get("from"), "-")] for s in sites]
        sec = {"title": "Sites (%d)" % len(sites), "status": "info",
               "columns": ["Site", "Nodes", "Buildings", "Down", "Warning", "Worst", "Lat, long", "Placed by"],
               "rows": rows, "row_status": ["critical" if s["down"] else "" for s in sites]}
        sections.append(_cut(rows, o, sec, "sites"))
    if unplaced:
        sections.append({"title": "Unplaced sites (%d)" % len(unplaced), "status": "warning",
                         "text": "No coordinates from SolarWinds' Worldwide Map or the custom properties: give them in netmap_sites "
                                 "(all.yml) by name or by a pattern - e.g. {\"%s\": {lat: 39.4, lon: -93.2}}, or {\"%s*\": {...}} for every "
                                 "site whose name starts so. The map shows them as tiles." % (unplaced[0]["name"], unplaced[0]["name"][:4]),
                         "lines": ["%s (%s)" % (s["name"], _plural(s["nodes"], "node")) for s in unplaced[:30]]
                                  + (["and %d more (the workbook's Sites sheet lists them all)" % (len(unplaced) - 30)] if len(unplaced) > 30 else [])})
    nr = list(r.get("not_read") or [])
    if m.get("cut"):
        nr.append({"what": "nodes past netmap_max_nodes", "why": "only the first %d nodes were read" % len(nodes)})
    if m.get("hidden_links"):
        nr.append({"what": "%s" % _plural(m["hidden_links"], "link"), "why": "to a node this account may not see (its limitation in SolarWinds) - left out"})
    if nr:
        sections.append({"title": "Not all read", "status": "warning", "lines": ["%s: %s" % (x["what"], x["why"]) for x in nr]})
    sections.extend(_discovery_sections(m, full=bool(o.get("web"))))   # the email: the rule a level; the page: all of it
    links = [l for l in m.get("links") or [] if not l.get("gone")]
    title = "Network map: %d down, %d warning - %s, %s" % (cnt["Down"], cnt["Warning"], _plural(len([s for s in sites if s["name"] != m.get("no_site")]), "site"), _plural(len(nodes), "node"))
    if wan_down:
        title += " - %s down" % _plural(len(wan_down), "WAN link")
    summary = [{"label": "Nodes", "value": len(nodes), "note": "%s · %s" % (_plural(len([s for s in sites if s["name"] != m.get("no_site")]), "site"), _plural(len(links), "link"))},
               {"label": "Down", "value": cnt["Down"], "status": "critical" if cnt["Down"] else "ok", "note": "no answer to SolarWinds"},
               {"label": "Warning", "value": cnt["Warning"], "status": "warning" if cnt["Warning"] else "ok", "note": "up, with a problem"},
               {"label": "WAN links down", "value": len(wan_down), "status": "critical" if wan_down else "ok", "note": "of %d" % len(wan)}]
    if r.get("compared"):
        summary.append({"label": "Changed", "value": len(changes), "status": "info", "note": "since the last run"})
    if unplaced:
        summary.append({"label": "Unplaced sites", "value": len(unplaced), "status": "warning", "note": "netmap_sites"})
    sub = "%s (SWIS, read-only) · %s" % (vtext, ("compared with %s" % o["last"]) if r.get("compared") and o.get("last")
                                         else "not compared: %s" % (o.get("why_not_compared") or "no kept copy of an earlier run"))
    nsites = len([s for s in sites if s["name"] != m.get("no_site")])
    # 1.82.0: the head - the name alone, what is down and warning as counts, what was read as the scope
    h_counts = [[n, w, st] for n, w, st in ((cnt["Down"], "down", "critical"), (len(wan_down), "WAN link%s down" % ("" if len(wan_down) == 1 else "s"), "critical"),
                                             (cnt["Warning"], "warning" if cnt["Warning"] == 1 else "warnings", "warning")) if n] or [[0, "down or warning", "ok"]]
    return {"title": title, "status": "critical" if cnt["Down"] or wan_down else "warning" if cnt["Warning"] or wan_warn or unplaced else "ok",
            "head_name": "Network map", "head_counts": h_counts,
            "head_scope": [_plural(nsites, "site"), _plural(len(nodes), "node")],
            "subtitle": sub, "summary": summary, "sections": sections}


def _pct(x):
    return "%d%%" % round((x or 0) * 100)


def _discovery_sections(m, full=True):
    """1.95.0: how the sites, buildings and closets were read - the rule a level, the readings, the fields, the roles (last:
    the explanation after the results)."""
    d = m.get("discovery") or {}
    if not d.get("levels"):
        return []
    out = [{"title": "Discovery: how the sites, buildings and closets were read", "status": "info",
            "text": d.get("best") or "", "columns": ["Level", "Read from", "Nodes", "Groups", "Agrees with", "Decided by", "Examples"],
            "rows": [[lv["level"].capitalize(), lv["rule"], _pct(lv["coverage"]), lv["groups"], ", ".join(lv.get("agrees") or []) or "-",
                      lv.get("decided") or "-", ", ".join(lv.get("samples") or [])] for lv in d["levels"]]}]
    if not full:
        out[0]["text"] = (d.get("best") or "") + " The report's page and the workbook's Discovery sheet show every reading and field."
        return out
    site = d["levels"][0]
    rd = site.get("readings") or []
    if rd:
        out.append({"title": "Discovery: the readings of the site", "status": "info",
                    "text": "The chosen one first; the others as they scored (coverage x a sane number of groups x the links inside "
                            "x how whole the groups are). Set one in all.yml: netmap_site_pattern or netmap_hierarchy.",
                    "columns": ["", "Read from", "Nodes", "Groups", "Links inside", "Pieces a group", "Score", "Examples"],
                    "rows": [["chosen" if r.get("chosen") else "", r["rule"], _pct(r["coverage"]), r["groups"],
                              "-" if r.get("cohesion") is None else _pct(r["cohesion"]), r.get("pieces", 1),
                              "-" if r.get("score") is None else r["score"], _examples(r)] for r in rd]})
    if d.get("fields"):
        out.append({"title": "Discovery: what the nodes carry", "status": "info",
                    "text": "Each field: how many nodes have a value, how many different values, and five of them. A custom property's "
                            "values are shown only when netmap_properties or netmap_hierarchy names it, or a level reads it; e-mail "
                            "addresses, phone numbers and serial-like values never.",
                    "columns": ["Field", "Nodes with a value", "Different values", "Examples"],
                    "rows": [[f["field"], f["filled"], f["distinct"], _examples(f)] for f in d["fields"]]})
    if d.get("roles"):
        out.append({"title": "Discovery: the devices' roles", "status": "info",
                    "text": "From the name (CR, DS, AS, FW ...), else the model, else the number of links; netmap_roles sets one by name.",
                    "columns": ["Role", "Devices", "From the name", "From the model", "From the links", "netmap_roles"],
                    "rows": [[r["role"], r["n"], r.get("name", 0), r.get("model", 0), r.get("links", 0), r.get("netmap_roles", 0)] for r in d["roles"]]})
    un = d.get("unplaced_nodes") or []
    if un:
        out.append({"title": "Discovery: nodes not placed (%d)" % len(un), "status": "info",
                    "text": "No site from the reading above: they are on the site \"%s\"." % (m.get("no_site") or "Not placed"),
                    "lines": un[:30] + (["and %d more" % (len(un) - 30)] if len(un) > 30 else [])})
    return out


def netmap_xlsx(res):
    """The workbook: Nodes, Links, WAN links, Sites, Changes - a row each, nothing cut."""
    r = res or {}
    m = r.get("map") or {}
    ids = dict((n["id"], n) for n in m.get("nodes") or [])
    props = sorted(set(k for n in m.get("nodes") or [] for k in (n.get("props") or {})))
    nodes = sorted(m.get("nodes") or [], key=lambda n: (n["site"].lower(), ORDER.get(n["status"], 9), n["name"].lower()))
    links = [l for l in m.get("links") or [] if l["a"] in ids and l["b"] in ids]
    sheets = [
        {"name": "Nodes", "columns": ["Node", "Status", "Site", "Building", "Closet", "Role", "Role from", "IP", "Vendor", "Model", "Location", "Response",
                                      "SolarWinds says", "Change", "NodeID"] + props,
         "rows": [[n["name"], "Missing" if n.get("gone") else n["status"], n["site"], n.get("building", ""), n.get("closet", ""), n.get("role", ""),
                   n.get("role_from", ""), n.get("ip", ""), n.get("vendor", ""),
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
    d = m.get("discovery") or {}
    if d.get("levels"):                                  # 1.95.0: how it was read - the rule a level, every reading, every field
        rows = []
        for lv in d["levels"]:
            for x in lv.get("readings") or []:
                rows.append([lv["level"], "chosen" if x.get("chosen") else "", x["rule"], x["coverage"], x["groups"], x.get("cohesion"), x.get("pieces"),
                             x.get("score"), lv.get("decided") if x.get("chosen") else "", _examples(x)])
        rows += [["field", "", f["field"], f["filled"], f["distinct"], "", "", "", "", _examples(f)] for f in d.get("fields") or []]
        sheets.append({"name": "Discovery", "columns": ["Level", "Chosen", "Read from", "Coverage (nodes with a value)", "Groups", "Links inside",
                                                        "Pieces a group", "Score", "Decided by", "Examples"], "rows": rows})
    return sheets


def _examples(x):
    """A field's or a reading's examples - or why a custom property's are not shown."""
    if x.get("hidden"):
        return "(values not shown: name it in netmap_properties to see them)"
    return ", ".join(x.get("samples") or [])


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
    d = m.get("discovery") or {}
    if d.get("best"):                                    # 1.95.0: the discovery, in the job's output
        out.append("Discovery: " + d["best"])
        for lv in d.get("levels") or []:
            out.append("  %s: %s - %d groups, %s of the nodes (%s)" % (lv["level"], lv["rule"], lv["groups"], _pct(lv["coverage"]), lv.get("decided") or "-"))
        for f in sorted(d.get("fields") or [], key=lambda f: (-f["filled"], f["distinct"]))[:12]:
            out.append("  field %s: %d nodes, %d values%s" % (f["field"], f["filled"], f["distinct"],
                                                               (", e.g. " + ", ".join(f["samples"][:3])) if f.get("samples") else ""))
    for s in (m.get("sites") or [])[:60]:
        out.append("%s: %d nodes, %d down, %d warning - %s" % (s["name"], s["nodes"], s["down"], s["warning"],
                   ("%.3f, %.3f (%s)" % (s["lat"], s["lon"], FROM.get(s["from"], s["from"]))) if s.get("lat") is not None else "unplaced"))
    if len(m.get("sites") or []) > 60:
        out.append("... and %d more sites (the report's Sites table)" % (len(m["sites"]) - 60))
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
                "netmap_page_extra": netmap_page_extra, "netmap_summary_counts": netmap_summary_counts,
                "netmap_check_opts": netmap_check_opts}
