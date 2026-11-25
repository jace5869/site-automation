"""The AD report's replication views, using one saved run and the portal's map settings."""
from hashlib import sha256
from html import escape

import ad_repl_filters as report
import site_ad_layer as layer
import site_swis
import site_time


def map_data(dcs, links, at, opts, scale):
    site_of, members, nodes, _ = layer.index(dcs)
    places = opts.get("sites") or {}
    aliases = opts.get("ad_site_map") or {}
    names = {s for s in places if "*" not in s and "?" not in s}
    for s in list(members) + list(aliases.values()):
        if site_swis.place(places, s) and not any(n.lower() == s.lower() for n in names):
            names.add(s)
    matched = layer.match(members, members, nodes, {"names": sorted(names)}, aliases)
    sites = []
    for s in sorted(members, key=str.lower):
        name = matched[s][0]
        pos = site_swis.place(places, name) if name else None
        coords = None
        if isinstance(pos, dict):
            lat, lon = site_swis._num(pos.get("lat")), site_swis._num(pos.get("lon"))
            if lat is not None and lon is not None and abs(lat) <= 90 and abs(lon) <= 180:
                coords = [lat, lon]
        sites.append({"n": s, "dcs": sorted(members[s]), "nm": name, "p": coords})
    between, inside = layer.pairs(links, site_of, at, scale)
    return {"sites": sites, "links": [dict(p, a=a, b=b, nma=matched[a][0], nmb=matched[b][0])
                                      for (a, b), p in sorted(between.items())],
            "inside": {s: [dict(p, a=a, b=b) for (a, b), p in sorted(pr.items())] for s, pr in inside.items()},
            "scale": scale, "placed_by": bool(places), "wan_read": False}


def shape(dcs, links, at, opts, tz):
    scale = layer.scale(opts.get("ad_scale"))
    settings = dict(scale, tsl=0, max_dcs=max(1, int(opts.get("ad_repl_matrix_max_dcs", report.FIT_COLS))))
    population = [dict(name=d["dc"], site=d["site"], read=d["repl_read"], why=d["worst"]) for d in dcs]
    site_of = {d["dc_id"]: d["site"] for d in dcs}
    pairs = report._pairs({"links": links}, report._ad.parse_time(at), settings, tz, saved=True)
    matrix = report._matrix(population, pairs, settings, site_of, portal=True)
    ordered = sorted(pairs.values(), key=lambda p: (report.RANK[p["st"]], -(p["age"] if p["age"] is not None else 1e9),
                                                    p["src_id"], p["dst_id"]))
    return {"matrix": matrix, "ordered": ordered, "site_of": site_of,
            "map": map_data(population, links, at, opts, scale), "tz": tz}


def panel_id(src, dst):
    return "ad-p-" + sha256((src + "\0" + dst).encode()).hexdigest()[:20]


def state_value(st, value, stale):
    state = "unknown" if stale or st not in report.WORD else st
    return '<span class="adp-state s-%s" title="%s">%s</span>' % (
        state, "Stale" if stale else report.WORD[state], "?" if stale else escape(value))


def grid(mx, stale):
    out = ['<div class="tw adp-grid"><table aria-label="Replication matrix"><thead>']
    if mx["groups"]:
        out.append('<tr><td></td>%s</tr>' % "".join('<th scope="colgroup" colspan="%d">%s</th>' %
                   (g["n"], escape(g["name"])) for g in mx["groups"]))
    out.append('<tr><th scope="col">Source ↓ Destination →</th>%s</tr></thead><tbody>' % "".join(
        '<th scope="col" title="%s">%s</th>' % (escape(c["name"]), escape(c.get("key", c["name"]))) for c in mx["cols"]))
    unread = {}
    for row in mx["rows"]:
        if row["first"]:
            out.append('</tbody><tbody><tr class="adp-band"><th scope="rowgroup" colspan="%d">%s</th></tr>' %
                       (1 + len(mx["cols"]), escape(row["site"])))
        out.append('<tr><th scope="row">%s</th>' % escape((row.get("key", "") + " " + row["name"]).strip()))
        for cell, col in zip(row["cells"], mx["cols"]):
            st = cell["st"]
            if st == "self":
                out.append('<td class="adp-self" aria-label="Same DC"></td>')
                continue
            if st == "none" and not stale:
                out.append('<td class="adp-none" title="Not replication partners">·</td>')
                continue
            value = state_value(st, cell["text"], stale)
            if "pair" in cell:
                label = "%s to %s, %s" % (row["name"], col["name"], "stale" if stale else (report.WORD.get(st, "Not read") + ", " + cell["say"]))
                value = '<a href="#%s" aria-label="%s">%s</a>' % (panel_id(*cell["pair"]), escape(label), value)
            elif st == "unknown":
                key = panel_id("", col["name"])
                unread[key] = col
                value = '<a href="#%s" aria-label="%s">%s</a>' % (key, escape(cell["tip"]), value)
            out.append("<td>%s</td>" % value)
        out.append("</tr>")
    out.append("</tbody></table></div>")
    for key, col in unread.items():
        out.append('<aside class="card adp-panel" id="%s" tabindex="-1"><h3>%s · Not read</h3>'
                   '<a class="adp-close" href="#ad-replication" aria-label="Close details">×</a>'
                   '<p>The job could not read all inbound replication at this destination.</p>%s</aside>' %
                   (key, escape(col["name"]), verify(col["name"]) if mx["mode"] == "dc" else ""))
    return "".join(out)


def verify(dst):
    return ('<p class="adp-command"><code>repadmin /showrepl %s /verbose</code>'
            ' <button type="button" class="adp-copy" hidden>Copy</button></p>' % escape(dst))


def details(pair, tz, stale):
    src, dst = pair["src_id"], pair["dst_id"]
    out = ['<aside class="card adp-panel" id="%s" tabindex="-1"><h3>%s → %s</h3>'
           '<a class="adp-close" href="#ad-replication" aria-label="Close link details">×</a>'
           '<div class="tw"><table><thead><tr><th>Partition (worst first)</th><th>State</th><th>Last success</th><th>Last failure</th>'
           '<th>Failures in a row</th><th>Error code and text</th></tr></thead><tbody>' %
           (panel_id(src, dst), escape(pair["frm"]), escape(pair["to"]))]
    for x in pair["partitions"]:
        last = site_time.local(x["last_success"], tz) if x["last_success"] else "Never"
        failure = site_time.local(x["last_failure"], tz) if x["last_failure"] else "—"
        error = report._err(x["error"]) or x["error"] or "—"
        if error.isdecimal() and error != "0":
            error += " (description unavailable)"
        out.append('<tr><th scope="row">%s</th><td>%s</td><td>%s</td><td>%s</td><td>%d</td><td>%s</td></tr>' %
                   (escape(x["nc_dn"] or x["nc"]), state_value(x["st"], report.WORD[x["st"]], stale),
                    escape(last), escape(failure), x["failures"], escape(error)))
    out.extend(["</tbody></table></div>", verify(dst), "</aside>"])
    return "".join(out)


def matrix_html(data, stale):
    mx, scale = data["matrix"], data["map"]["scale"]
    out = ['<div class="adp-rep-body">']
    if not mx["cols"] and not data["ordered"]:
        return '<p class="muted">No replication data in this run.</p>'
    out.append('<input type="radio" class="adp-choice" name="ad-view" id="ad-grid-view" checked>'
               '<label for="ad-grid-view">Matrix</label> '
               '<input type="radio" class="adp-choice" name="ad-view" id="ad-list-view">'
               '<label for="ad-list-view">Links, worst first</label><div class="adp-matrix-view">')
    bands = [("ok", "OK")]
    if scale["low"]:
        bands.append(("low", "Low ≥%g h" % scale["low"]))
    bands.extend([("warning", "Warning ≥%g h or failing" % scale["warn"]), ("critical", "Critical ≥%g h" % scale["crit"])])
    out.append('<p class="adp-key">%s <span>? not read</span> <span>· not partners</span> <span>— never replicated</span></p>' %
               " ".join('<span class="%s">%s</span>' % ("muted" if stale else "c-" + s, t) for s, t in bands))
    out.append(grid(mx, stale))
    out.append('<p class="muted">%s Select a link for its partitions and last success.</p></div>' %
               (escape(mx["note"]) if mx["mode"] == "site" else "Each age is the worst partition at the time of the AD job."))
    out.append('<div class="tw adp-list-view"><table><thead><tr><th>Source → destination</th><th>Sites</th><th>State</th>'
               '<th>Last success</th><th>Failures in a row</th><th>Error</th></tr></thead><tbody>')
    for p in data["ordered"]:
        key = p["src_id"], p["dst_id"]
        sites = " → ".join(data["site_of"].get(k) or report.NO_SITE for k in key)
        out.append('<tr><td><a href="#%s">%s → %s</a></td><td>%s</td><td>%s</td><td>%s</td><td>%d</td><td>%s</td></tr>' %
                   (panel_id(*key), escape(p["frm"]), escape(p["to"]), escape(sites),
                    state_value(p["st"], report.WORD[p["st"]], stale), escape(p["ls_local"] or "Never"),
                    p["failures"], escape(report._err(p["error"]) or "—")))
    out.append("</tbody></table></div>")
    for p in data["ordered"]:
        out.append(details(p, data["tz"], stale))
    return "".join(out) + "</div>"
