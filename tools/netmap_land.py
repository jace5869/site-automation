#!/usr/bin/env python3
"""The network map's land outlines (roles/netmap_report/files/web/world-land.js, 1.85.0): Natural Earth's 1:110m land
(ne_110m_land, public domain - naturalearthdata.com; the GeoJSON from github.com/nvkelso/natural-earth-vector) as rings of
flat [lon, lat, ...] lists, 1 decimal, simplified (Douglas-Peucker 0.25 degrees), rings under 1 degree across dropped.
The file is bundled: the map fetches nothing.
    python3 tools/netmap_land.py ne_110m_land.geojson roles/netmap_report/files/web/world-land.js"""
import json
import sys

src, out = sys.argv[1], sys.argv[2]
g = json.load(open(src))


def dp(pts, eps):
    if len(pts) < 3:
        return pts
    a, b = pts[0], pts[-1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = (dx * dx + dy * dy) ** .5
    best, bi = 0, 0
    for i in range(1, len(pts) - 1):
        p = pts[i]
        d = abs(dy * p[0] - dx * p[1] + b[0] * a[1] - b[1] * a[0]) / n if n else ((p[0] - a[0]) ** 2 + (p[1] - a[1]) ** 2) ** .5
        if d > best:
            best, bi = d, i
    if best > eps:
        return dp(pts[:bi + 1], eps)[:-1] + dp(pts[bi:], eps)
    return [a, b]


rings, npts = [], 0
for f in g["features"]:
    geo = f["geometry"]
    polys = [geo["coordinates"]] if geo["type"] == "Polygon" else geo["coordinates"]
    for poly in polys:
        r = poly[0]                                  # outer rings only: 110m land has no holes worth drawing
        xs, ys = [p[0] for p in r], [p[1] for p in r]
        if max(max(xs) - min(xs), max(ys) - min(ys)) < 1:
            continue
        s = dp(r, .25)
        q = []
        for p in s:
            t = [round(p[0], 1), round(p[1], 1)]
            if not q or q[-1] != t:
                q.append(t)
        if len(q) < 4:
            continue
        rings.append([v for p in q for v in p])
        npts += len(q)
body = json.dumps(rings, separators=(",", ":"))
open(out, "w").write("/* Land outlines: Natural Earth 1:110m land (ne_110m_land, public domain, naturalearthdata.com), simplified for the\n"
                     "   network map's globe. Rings of lon,lat pairs. */\nwindow.NM_LAND=" + body + ";\n")
print("rings", len(rings), "points", npts, "bytes", len(body))
