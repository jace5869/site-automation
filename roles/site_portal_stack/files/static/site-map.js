/* 1.139.0: Offline Wall Map of Sites placed by SolarWinds Orion.WorldMap.Point lat/lon (/portal/sitemap).
   Matches mock/site-map.html across all 10 portal looks, light & dark, 800 / 1280 / 1920 px board.
   Uses bundled Natural Earth 1:110m vector land polygons from static/wall-land.js (window.WALL_LAND) - zero internet.
   Strict CSP: pure DOM/SVG creation only (no HTML string injection or inline style attributes). */
(function () {
  "use strict";
  var SVG_NS = "http://www.w3.org/2000/svg";
  var MAX_LAT = 82;
  var ROOT = document.getElementById("site-map");
  if (!ROOT) return;

  var SRC = ROOT.getAttribute("data-src") || "/portal/api/sitemap";
  var WALL_URL = ROOT.getAttribute("data-wall") || "/portal/wall";

  var S = {
    data: null, etag: "", failMsg: "", loading: false,
    view: "split", filter: "all", query: "", region: "all",
    cluster: true, wan: true, dimOk: false, board: false,
    tour: false, tourIdx: 0, tourTick: 0,
    zoom: 1.0, cx: 0.0, cy: 0.36, // Mercator normalized [0..1] x [0..1], cx in [-180..180] lon, cy in [0..1]
    selected: null, drawer: null, clusterPop: null,
    sortKey: "status", sortDir: 1, cursor: null, fitted: false, pollLeft: 60
  };
  var DRAG = {on: false, x0: 0, y0: 0, cx0: 0, cy0: 0};
  var MAP_W = 920, MAP_H = 540;

  function h(tag, cls, kids, attrs) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (attrs) Object.keys(attrs).forEach(function (k) { if (attrs[k] !== null && attrs[k] !== undefined) e.setAttribute(k, String(attrs[k])); });
    (kids || []).forEach(function (c) {
      if (c === null || c === undefined || c === false) return;
      e.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
    });
    return e;
  }
  function sv(tag, attrs, kids) {
    var e = document.createElementNS(SVG_NS, tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { e.setAttribute(k, String(attrs[k])); });
    (kids || []).forEach(function (c) { if (c) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return e;
  }
  function clear(el) { while (el && el.firstChild) el.removeChild(el.firstChild); }

  // Mercator projection (normalized world x in [0..1], y in [0..1])
  function latToY(lat) {
    var cl = Math.max(-MAX_LAT, Math.min(MAX_LAT, lat)) * Math.PI / 180;
    return (1 - Math.log(Math.tan(cl) + 1 / Math.cos(cl)) / Math.PI) / 2;
  }
  function yToLat(ny) {
    var n = Math.PI * (1 - 2 * ny);
    return Math.atan(0.5 * (Math.exp(n) - Math.exp(-n))) * 180 / Math.PI;
  }
  function lonToX(lon) { return (lon + 180) / 360; }
  function xToLon(nx) { return nx * 360 - 180; }

  function worldScale() { return Math.min(MAP_W, MAP_H * 1.9) * S.zoom; }
  function toScreen(lat, lon) {
    var sc = worldScale();
    var wx = lonToX(lon), wy = latToY(lat);
    var cx = lonToX(S.cx);
    var dx = wx - cx;
    if (dx > 0.5) dx -= 1;
    if (dx < -0.5) dx += 1;
    return [MAP_W / 2 + dx * sc, MAP_H / 2 + (wy - S.cy) * sc];
  }
  function fromScreen(sx, sy) {
    var sc = worldScale();
    var nx = lonToX(S.cx) + (sx - MAP_W / 2) / sc;
    while (nx < 0) nx += 1;
    while (nx > 1) nx -= 1;
    var ny = Math.max(0.02, Math.min(0.98, S.cy + (sy - MAP_H / 2) / sc));
    return [yToLat(ny), xToLon(nx)];
  }

  function fitBounds(minLat, minLon, maxLat, maxLon) {
    var padLat = Math.max(3, (maxLat - minLat) * 0.25);
    var padLon = Math.max(5, (maxLon - minLon) * 0.25);
    var la1 = Math.max(-MAX_LAT, minLat - padLat), la2 = Math.min(MAX_LAT, maxLat + padLat);
    var lo1 = Math.max(-180, minLon - padLon), lo2 = Math.min(180, maxLon + padLon);
    S.cx = (lo1 + lo2) / 2;
    S.cy = (latToY(la1) + latToY(la2)) / 2;
    var spanX = Math.max(0.015, (lo2 - lo1) / 360);
    var spanY = Math.max(0.015, Math.abs(latToY(la1) - latToY(la2)));
    var base = Math.min(MAP_W, MAP_H * 1.9);
    var zx = (MAP_W * 0.84) / (spanX * base);
    var zy = (MAP_H * 0.80) / (spanY * base);
    S.zoom = Math.max(1.0, Math.min(18.0, Math.min(zx, zy)));
  }

  function fitAllPlaced() {
    var pts = ((S.data && S.data.sites) || []).filter(function (s) { return s.placed && s.lat !== null && s.lon !== null; });
    if (!pts.length) { S.cx = 0; S.cy = 0.38; S.zoom = 1.0; return; }
    var lats = pts.map(function (p) { return p.lat; }), lons = pts.map(function (p) { return p.lon; });
    fitBounds(Math.min.apply(null, lats), Math.min.apply(null, lons), Math.max.apply(null, lats), Math.max.apply(null, lons));
  }

  function fmtAge(sec) {
    if (sec === null || sec === undefined || isNaN(sec)) return "not read";
    sec = Math.max(0, Math.floor(sec));
    if (sec < 60) return sec + "s ago";
    if (sec < 3600) return Math.floor(sec / 60) + "m ago";
    return Math.floor(sec / 3600) + "h " + Math.floor((sec % 3600) / 60) + "m ago";
  }
  function statusWord(st) { return st === "crit" ? "CRITICAL" : st === "warn" ? "WARNING" : st === "ok" ? "OK" : "NOT CURRENT"; }
  function statusRank(st) { return st === "crit" ? 0 : st === "warn" ? 1 : st === "stale" ? 2 : 3; }

  function filteredSites() {
    var list = (S.data && S.data.sites) || [];
    var q = (S.query || "").trim().toLowerCase();
    return list.filter(function (s) {
      if (S.filter === "unplaced" && s.placed) return false;
      if (S.filter !== "all" && S.filter !== "unplaced" && s.status !== S.filter) return false;
      if (q && ((s.name || "") + " " + (s.region || "") + " " + (s.coord_source || "") + " " + (s.unplaced_why || "")).toLowerCase().indexOf(q) < 0) return false;
      return true;
    });
  }

  function fetchMap(force) {
    if (S.loading) return;
    S.loading = true;
    var url = SRC + (force ? "?poll=now" : "");
    var hd = {Accept: "application/json"};
    if (S.etag && !force) hd["If-None-Match"] = S.etag;
    fetch(url, {headers: hd, credentials: "same-origin", cache: "no-store"}).then(function (res) {
      if (res.status === 304) return null;
      if (!res.ok) throw new Error("HTTP " + res.status);
      S.etag = (res.headers && res.headers.get && res.headers.get("ETag")) || "";
      return res.json();
    }).then(function (doc) {
      S.loading = false;
      S.failMsg = "";
      if (doc) {
        S.data = doc;
        S.pollLeft = Math.max(15, Number(doc.poll) || 60);
        if (!S.fitted) { S.fitted = true; fitAllPlaced(); }
      }
      render();
    }).catch(function (err) {
      S.loading = false;
      // 1.139.0: keep the last map visible while the poll timer retries.
      S.etag = "";
      S.failMsg = "Site map feed unreachable: " + (err && err.message ? err.message : "network error");
      render();
    });
  }

  function copyText(txt, msgEl) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(txt).then(function () {
        if (msgEl) msgEl.textContent = "Copied to clipboard - press Ctrl+V in Teams.";
      }, function () {
        if (msgEl) msgEl.textContent = "Clipboard blocked - copy manually.";
      });
    } else if (msgEl) {
      msgEl.textContent = "Clipboard API unavailable.";
    }
  }

  // Distinct status pin marker (color + shape + icon)
  function pinGroup(site, x, y) {
    var g = sv("g", {class: "sm-pin" + (S.dimOk && site.status === "ok" ? " dim" : ""), tabindex: "0", role: "button",
      "aria-label": site.name + " " + statusWord(site.status) + ", " + site.up + " of " + site.nodes + " nodes up"});
    if (site.status === "crit") {
      g.appendChild(sv("circle", {cx: x, cy: y, r: 10, fill: "none", stroke: "var(--critical)", "stroke-width": 2, class: "sm-pulse"}));
      // Octagon for critical
      var r = 8.5, pts = [];
      for (var i = 0; i < 8; i++) {
        var a = (i * 45 + 22.5) * Math.PI / 180;
        pts.push((x + r * Math.cos(a)).toFixed(1) + "," + (y + r * Math.sin(a)).toFixed(1));
      }
      g.appendChild(sv("polygon", {points: pts.join(" "), fill: "var(--critical)", stroke: "var(--card-solid,var(--card))", "stroke-width": 1.6}));
      g.appendChild(sv("text", {x: x, y: y + 0.5, fill: "#ffffff", "font-size": 10, "font-weight": 800, "text-anchor": "middle", "dominant-baseline": "central"}, ["!"]));
    } else if (site.status === "warn") {
      // Diamond for warning
      var d = 8.5;
      g.appendChild(sv("polygon", {points: x + "," + (y - d) + " " + (x + d) + "," + y + " " + x + "," + (y + d) + " " + (x - d) + "," + y,
        fill: "var(--warning)", stroke: "var(--card-solid,var(--card))", "stroke-width": 1.5}));
    } else if (site.status === "stale") {
      // Dashed hollow circle for stale / not current (never green!)
      g.appendChild(sv("circle", {cx: x, cy: y, r: 7.5, fill: "var(--card-solid,var(--card))", stroke: "var(--unknown)", "stroke-width": 2, "stroke-dasharray": "3 2"}));
      g.appendChild(sv("text", {x: x, y: y + 0.5, fill: "var(--unknown)", "font-size": 9.5, "font-weight": 700, "text-anchor": "middle", "dominant-baseline": "central"}, ["?"]));
    } else {
      g.appendChild(sv("circle", {cx: x, cy: y, r: 6.5, fill: "var(--ok)", stroke: "var(--card-solid,var(--card))", "stroke-width": 1.5}));
    }
    if (S.selected === site.id) {
      g.appendChild(sv("circle", {cx: x, cy: y, r: 12.5, fill: "none", stroke: "var(--accent)", "stroke-width": 2}));
    }
    g.addEventListener("click", function (ev) { ev.stopPropagation(); S.selected = site.id; S.drawer = {kind: "site", item: site}; S.clusterPop = null; S.tour = false; render(); });
    g.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); S.selected = site.id; S.drawer = {kind: "site", item: site}; render(); } });
    return g;
  }

  // Spatial clustering (44px merge radius)
  function buildClusters(placed) {
    var items = placed.map(function (s) { var p = toScreen(s.lat, s.lon); return {site: s, x: p[0], y: p[1]}; });
    if (!S.cluster) return items.map(function (it) { return {x: it.x, y: it.y, items: [it.site]}; });
    var R2 = 44 * 44;
    var clusters = [];
    items.forEach(function (it) {
      var hit = null;
      for (var i = 0; i < clusters.length; i++) {
        var dx = clusters[i].x - it.x, dy = clusters[i].y - it.y;
        if (dx * dx + dy * dy <= R2) { hit = clusters[i]; break; }
      }
      if (!hit) clusters.push({x: it.x, y: it.y, items: [it.site]});
      else {
        var n = hit.items.length;
        hit.x = (hit.x * n + it.x) / (n + 1);
        hit.y = (hit.y * n + it.y) / (n + 1);
        hit.items.push(it.site);
      }
    });
    return clusters;
  }

  function clusterGroup(cl) {
    var g = sv("g", {class: "sm-pin", tabindex: "0", role: "button", "aria-label": cl.items.length + " sites clustered"});
    var r = 14, cx = cl.x, cy = cl.y;
    g.appendChild(sv("circle", {cx: cx, cy: cy, r: r, fill: "var(--card-solid,var(--card))", stroke: "var(--edge)", "stroke-width": 1.5}));
    // Multi-segment status ring
    var cnt = {crit: 0, warn: 0, stale: 0, ok: 0};
    cl.items.forEach(function (s) { cnt[s.status] = (cnt[s.status] || 0) + 1; });
    var tot = cl.items.length, circ = 2 * Math.PI * r, off = 0;
    [["crit", "var(--critical)"], ["warn", "var(--warning)"], ["stale", "var(--unknown)"], ["ok", "var(--ok)"]].forEach(function (pair) {
      var k = pair[0], col = pair[1];
      if (!cnt[k]) return;
      var len = (cnt[k] / tot) * circ;
      g.appendChild(sv("circle", {
        cx: cx, cy: cy, r: r, fill: "none", stroke: col, "stroke-width": 3.5,
        "stroke-dasharray": len.toFixed(1) + " " + circ.toFixed(1),
        "stroke-dashoffset": (-off).toFixed(1),
        transform: "rotate(-90 " + cx.toFixed(1) + " " + cy.toFixed(1) + ")"
      }));
      off += len;
    });
    g.appendChild(sv("text", {x: cx, y: cy, class: "sm-cl-lbl"}, [String(tot)]));
    g.addEventListener("click", function (ev) {
      ev.stopPropagation();
      S.clusterPop = {x: Math.min(MAP_W - 230, Math.max(10, cx + 16)), y: Math.min(MAP_H - 180, Math.max(10, cy - 20)), items: cl.items};
      render();
    });
    return g;
  }

  function buildMapCanvas(sites, links) {
    var wrap = h("div", "sm-mapwrap" + (DRAG.on ? " drag" : ""));
    var svg = sv("svg", {class: "sm-svg", viewBox: "0 0 " + MAP_W + " " + MAP_H, role: "img", "aria-label": "Site map"});

    // 1. Graticule
    var step = S.zoom >= 5 ? 10 : S.zoom >= 2.2 ? 20 : 30;
    for (var lon = -180; lon <= 180; lon += step) {
      var p1 = toScreen(MAX_LAT, lon), p2 = toScreen(-MAX_LAT, lon);
      if (p1[0] >= -20 && p1[0] <= MAP_W + 20) {
        svg.appendChild(sv("line", {x1: p1[0].toFixed(1), y1: 0, x2: p2[0].toFixed(1), y2: MAP_H, class: "sm-grat"}));
        svg.appendChild(sv("text", {x: (p1[0] + 3).toFixed(1), y: 12, class: "sm-grat-lbl"}, [lon + "°"]));
      }
    }
    for (var lat = -60; lat <= 75; lat += step) {
      var pl = toScreen(lat, -180);
      if (pl[1] >= -10 && pl[1] <= MAP_H + 10) {
        svg.appendChild(sv("line", {x1: 0, y1: pl[1].toFixed(1), x2: MAP_W, y2: pl[1].toFixed(1), class: "sm-grat"}));
        svg.appendChild(sv("text", {x: 4, y: (pl[1] - 3).toFixed(1), class: "sm-grat-lbl"}, [lat + "°"]));
      }
    }

    // 2. Bundled offline Natural Earth land rings (window.WALL_LAND from static/wall-land.js)
    var land = window.WALL_LAND || [];
    var dParts = [];
    for (var i = 0; i < land.length; i++) {
      var ring = land[i];
      if (!ring || ring.length < 4) continue;
      var seg = "", prevX = null;
      for (var j = 0; j < ring.length; j += 2) {
        var pt = toScreen(ring[j + 1], ring[j]);
        var cmd = (j === 0 || (prevX !== null && Math.abs(pt[0] - prevX) > MAP_W * 0.45)) ? "M" : "L";
        seg += cmd + pt[0].toFixed(1) + "," + pt[1].toFixed(1);
        prevX = pt[0];
      }
      dParts.push(seg + "Z");
    }
    if (dParts.length) svg.appendChild(sv("path", {d: dParts.join(" "), class: "sm-land"}));

    // 3. Curved WAN links between placed sites
    var byId = {};
    sites.forEach(function (s) { if (s.placed) byId[s.id] = s; });
    if (S.wan) {
      (links || []).forEach(function (lk) {
        var sa = byId[lk.a], sb = byId[lk.b];
        if (!sa || !sb) return;
        var pa = toScreen(sa.lat, sa.lon), pb = toScreen(sb.lat, sb.lon);
        if (Math.abs(pa[0] - pb[0]) > MAP_W * 0.65) return;
        var mx = (pa[0] + pb[0]) / 2, my = (pa[1] + pb[1]) / 2 - Math.min(36, Math.hypot(pb[0] - pa[0], pb[1] - pa[1]) * 0.16);
        var path = sv("path", {
          d: "M" + pa[0].toFixed(1) + "," + pa[1].toFixed(1) + " Q" + mx.toFixed(1) + "," + my.toFixed(1) + " " + pb[0].toFixed(1) + "," + pb[1].toFixed(1),
          class: "sm-link " + (lk.status || "stale")
        });
        path.addEventListener("click", function (ev) { ev.stopPropagation(); S.drawer = {kind: "link", item: lk}; render(); });
        svg.appendChild(path);
        if (lk.status === "crit") {
          svg.appendChild(sv("text", {x: mx.toFixed(1), y: (my - 3).toFixed(1), class: "sm-wandown"}, ["WAN DOWN"]));
        }
      });
    }

    // 4. Site pins & clusters + collision-avoiding labels
    var placed = sites.filter(function (s) { return s.placed && s.lat !== null && s.lon !== null; });
    var clusters = buildClusters(placed);
    var placedBoxes = [];
    clusters.forEach(function (cl) {
      if (cl.items.length > 1) {
        svg.appendChild(clusterGroup(cl));
      } else {
        var s = cl.items[0];
        svg.appendChild(pinGroup(s, cl.x, cl.y));
        // Collision-avoiding label
        var lw = Math.min(120, s.name.length * 6.5 + 10), lh = 14;
        var lx = cl.x + 11, ly = cl.y + 4;
        var box = {l: lx, r: lx + lw, t: ly - lh, b: ly};
        var clash = placedBoxes.some(function (b) { return box.l < b.r && b.l < box.r && box.t < b.b && b.t < box.b; });
        if (!clash || s.status === "crit" || S.selected === s.id) {
          placedBoxes.push(box);
          svg.appendChild(sv("text", {x: lx.toFixed(1), y: ly.toFixed(1), class: "sm-lbl"}, [s.name]));
        }
      }
    });

    // Interactions: drag pan, wheel zoom, cursor lat/lon
    svg.addEventListener("mousedown", function (ev) {
      if (ev.button !== 0) return;
      DRAG = {on: true, x0: ev.clientX, y0: ev.clientY, cx0: S.cx, cy0: S.cy};
      S.clusterPop = null; S.tour = false;
      wrap.classList.add("drag");
    });
    svg.addEventListener("mousemove", function (ev) {
      var rect = svg.getBoundingClientRect();
      var sx = (ev.clientX - rect.left) * (MAP_W / Math.max(1, rect.width));
      var sy = (ev.clientY - rect.top) * (MAP_H / Math.max(1, rect.height));
      if (DRAG.on) {
        var sc = worldScale();
        var dx = (ev.clientX - DRAG.x0) * (MAP_W / Math.max(1, rect.width));
        var dy = (ev.clientY - DRAG.y0) * (MAP_H / Math.max(1, rect.height));
        S.cx = DRAG.cx0 - (dx / sc) * 360;
        while (S.cx > 180) S.cx -= 360;
        while (S.cx < -180) S.cx += 360;
        S.cy = Math.max(0.08, Math.min(0.92, DRAG.cy0 - dy / sc));
        render();
      } else {
        var ll = fromScreen(sx, sy);
        S.cursor = ll;
        var curEl = wrap.querySelector(".sm-cursor");
        if (curEl) curEl.textContent = ll[0].toFixed(2) + "°, " + ll[1].toFixed(2) + "° · " + S.zoom.toFixed(1) + "×";
      }
    });
    svg.addEventListener("wheel", function (ev) {
      ev.preventDefault();
      S.tour = false; S.clusterPop = null;
      var factor = ev.deltaY < 0 ? 1.25 : 0.8;
      S.zoom = Math.max(1.0, Math.min(20.0, S.zoom * factor));
      render();
    }, {passive: false});
    svg.addEventListener("click", function () { if (S.clusterPop) { S.clusterPop = null; render(); } });

    wrap.appendChild(svg);

    if (!placed.length) {
      var msg = (S.data && S.data.sites && S.data.sites.length)
        ? "No placed sites match this filter (see Unplaced sites in the side list)."
        : ((S.data && S.data.feed && S.data.feed.why) || "No live site coordinates read from SolarWinds yet.");
      wrap.appendChild(h("div", "sm-map-empty", [h("span", "", [msg])]));
    }

    // Zoom controls
    var zIn = h("button", "", ["+"], {type: "button", title: "Zoom in", "aria-label": "Zoom in"});
    zIn.addEventListener("click", function () { S.zoom = Math.min(20, S.zoom * 1.4); render(); });
    var zOut = h("button", "", ["−"], {type: "button", title: "Zoom out", "aria-label": "Zoom out"});
    zOut.addEventListener("click", function () { S.zoom = Math.max(1, S.zoom / 1.4); render(); });
    var zFit = h("button", "", ["⤢"], {type: "button", title: "Fit placed sites", "aria-label": "Fit all sites"});
    zFit.addEventListener("click", function () { S.region = "all"; fitAllPlaced(); render(); });
    wrap.appendChild(h("div", "sm-zoom", [zIn, zOut, zFit]));

    // Dynamic scale bar (km at center latitude)
    var latRad = Math.abs(yToLat(S.cy)) * Math.PI / 180;
    var kmPerPx = (40075 * Math.cos(latRad)) / Math.max(1, worldScale());
    var niceKm = [10, 25, 50, 100, 250, 500, 1000, 2500, 5000].filter(function (k) { return k / kmPerPx >= 48 && k / kmPerPx <= 150; })[0] || Math.round(80 * kmPerPx);
    var barPx = Math.max(36, Math.min(150, Math.round(niceKm / Math.max(0.01, kmPerPx))));
    var scBar = h("div", "sm-scale-bar");
    scBar.style.width = barPx + "px";
    wrap.appendChild(h("div", "sm-scale", [h("span", "", [niceKm + " km (" + Math.round(niceKm * 0.621) + " mi)"]), scBar]));

    // Cursor readout pill
    wrap.appendChild(h("div", "sm-cursor", [
      S.cursor ? (S.cursor[0].toFixed(2) + "°, " + S.cursor[1].toFixed(2) + "° · " + S.zoom.toFixed(1) + "×")
        : ("Center " + yToLat(S.cy).toFixed(1) + "°, " + S.cx.toFixed(1) + "° · " + S.zoom.toFixed(1) + "×")
    ]));

    // Minimap overview inset
    var mw = 140, mh = 74;
    var msvg = sv("svg", {viewBox: "0 0 " + mw + " " + mh, "aria-hidden": "true"});
    placed.forEach(function (s) {
      var mx = lonToX(s.lon) * mw, my = latToY(s.lat) * mh;
      var col = s.status === "crit" ? "var(--critical)" : s.status === "warn" ? "var(--warning)" : s.status === "ok" ? "var(--ok)" : "var(--unknown)";
      msvg.appendChild(sv("circle", {cx: mx.toFixed(1), cy: my.toFixed(1), r: 2, fill: col}));
    });
    var vw = Math.min(mw, (MAP_W / worldScale()) * mw);
    var vh = Math.min(mh, (MAP_H / worldScale()) * mh);
    var vx = Math.max(0, Math.min(mw - vw, lonToX(S.cx) * mw - vw / 2));
    var vy = Math.max(0, Math.min(mh - vh, S.cy * mh - vh / 2));
    msvg.appendChild(sv("rect", {x: vx.toFixed(1), y: vy.toFixed(1), width: vw.toFixed(1), height: vh.toFixed(1),
      fill: "none", stroke: "var(--accent)", "stroke-width": 1.3}));
    wrap.appendChild(h("div", "sm-mini", [msvg]));

    // Cluster popover
    if (S.clusterPop) {
      var pop = h("div", "sm-pop on");
      pop.style.left = S.clusterPop.x + "px";
      pop.style.top = S.clusterPop.y + "px";
      var zBtn = h("button", "sm-btn", ["Zoom in"], {type: "button"});
      zBtn.addEventListener("click", function () {
        var its = S.clusterPop.items;
        var lats = its.map(function (x) { return x.lat; }), lons = its.map(function (x) { return x.lon; });
        fitBounds(Math.min.apply(null, lats), Math.min.apply(null, lons), Math.max.apply(null, lats), Math.max.apply(null, lons));
        S.zoom = Math.min(20, Math.max(S.zoom, 4));
        S.clusterPop = null; render();
      });
      pop.appendChild(h("div", "sm-pop-h", [h("span", "", [S.clusterPop.items.length + " clustered sites"]), zBtn]));
      S.clusterPop.items.forEach(function (st) {
        var it = h("button", "sm-pop-item", [
          h("span", "", [st.name]),
          h("span", "sm-badge " + st.status, [statusWord(st.status)])
        ], {type: "button"});
        it.addEventListener("click", function () { S.selected = st.id; S.drawer = {kind: "site", item: st}; S.clusterPop = null; render(); });
        pop.appendChild(it);
      });
      wrap.appendChild(pop);
    }
    return wrap;
  }

  function buildFallbackTable(sites) {
    var rows = sites.slice();
    var k = S.sortKey, dir = S.sortDir;
    rows.sort(function (a, b) {
      var av, bv;
      if (k === "status") { av = statusRank(a.status); bv = statusRank(b.status); }
      else if (k === "name") { av = a.name.toLowerCase(); bv = b.name.toLowerCase(); }
      else if (k === "nodes") { av = a.down * 10000 - a.nodes; bv = b.down * 10000 - b.nodes; }
      else if (k === "rtt") { av = a.rtt === null ? -1 : a.rtt; bv = b.rtt === null ? -1 : b.rtt; }
      else if (k === "loss") { av = a.loss === null ? -1 : a.loss; bv = b.loss === null ? -1 : b.loss; }
      else { av = a.wan_util || 0; bv = b.wan_util || 0; }
      return av < bv ? -dir : av > bv ? dir : 0;
    });
    var cols = [["name", "Site"], ["status", "Status"], ["coord", "Lat / Lon (Source)"], ["nodes", "Nodes (Up/Down)"],
      ["util", "WAN Util"], ["rtt", "RTT"], ["loss", "Loss"], ["issues", "Active issues"]];
    var trH = h("tr");
    cols.forEach(function (c) {
      var th = h("th", "", [c[1] + (S.sortKey === c[0] ? (S.sortDir > 0 ? " ▲" : " ▼") : "")]);
      if (c[0] !== "coord" && c[0] !== "issues") {
        th.addEventListener("click", function () {
          if (S.sortKey === c[0]) S.sortDir = -S.sortDir;
          else { S.sortKey = c[0]; S.sortDir = 1; }
          render();
        });
      }
      trH.appendChild(th);
    });
    var tb = h("tbody");
    if (!rows.length) {
      tb.appendChild(h("tr", "", [h("td", "", ["No sites match the active filter."], {colspan: 8})]));
    } else {
      rows.forEach(function (s) {
        var tr = h("tr", "", [
          h("td", "", [h("b", "", [s.name])]),
          h("td", "", [h("span", "sm-badge " + s.status, [statusWord(s.status)])]),
          h("td", "mono", [s.placed ? (s.lat.toFixed(3) + ", " + s.lon.toFixed(3) + " · " + s.coord_source) : ("Unplaced — " + (s.unplaced_why || "no coords"))]),
          h("td", "mono", [s.up + "/" + s.nodes + (s.down ? " (" + s.down + " down)" : "")]),
          h("td", "mono", [s.wan_util == null ? "Not read" : s.wan_util + "%"]),
          h("td", "mono", [s.rtt !== null ? (s.rtt + " ms") : "—"]),
          h("td", "mono", [s.loss !== null ? (s.loss + "%") : "—"]),
          h("td", "", [(s.issues && s.issues.length) ? s.issues.slice(0, 2).map(function (i) { return i.obj + ": " + i.why; }).join("; ") : "—"])
        ]);
        tr.addEventListener("click", function () { S.selected = s.id; S.drawer = {kind: "site", item: s}; render(); });
        tb.appendChild(tr);
      });
    }
    return h("div", "sm-tbl-wrap", [h("table", "sm-tbl", [h("thead", "", [trH]), tb])]);
  }

  function renderDrawer() {
    var back = document.getElementById("sm-back");
    if (!back) {
      back = h("div", "sm-back", [], {id: "sm-back", role: "dialog", "aria-modal": "true"});
      back.addEventListener("click", function (ev) { if (ev.target === back) { S.drawer = null; renderDrawer(); } });
      document.body.appendChild(back);
    }
    clear(back);
    if (!S.drawer) { back.classList.remove("on"); return; }
    back.classList.add("on");
    var k = S.drawer.kind, it = S.drawer.item, doc = S.data || {};
    var drw = h("div", "sm-drw");
    var xBtn = h("button", "sm-drw-x", ["×"], {type: "button", "aria-label": "Close drawer"});
    xBtn.addEventListener("click", function () { S.drawer = null; renderDrawer(); });
    drw.appendChild(h("div", "sm-drw-h", [
      h("h3", "", [k === "site" ? it.name : ("WAN Link: " + it.a + " ↔ " + it.b)]),
      h("span", "sm-badge " + (it.status || "stale"), [statusWord(it.status)]),
      xBtn
    ]));

    var kv = h("dl", "sm-kv");
    function addKV(label, val) { kv.appendChild(h("dt", "", [label])); kv.appendChild(h("dd", "", [String(val)])); }
    var teams = "";

    if (k === "site") {
      addKV("Status", statusWord(it.status));
      addKV("Placement", it.placed ? (it.lat.toFixed(4) + "°, " + it.lon.toFixed(4) + "° (" + it.coord_source + ")") : ("Unplaced — " + (it.unplaced_why || "no coordinates")));
      addKV("Region", it.region || "—");
      addKV("Nodes", it.up + " up / " + it.down + " down / " + it.warn + " warn / " + it.stale + " stale (of " + it.nodes + ")");
      addKV("WAN", (it.wan_down ? (it.wan_down + " link down · ") : "") + "Max util " + (it.wan_util == null ? "not read" : it.wan_util + "%"));
      addKV("RTT / Loss", (it.rtt !== null ? (it.rtt + " ms") : "—") + " / " + (it.loss !== null ? (it.loss + "%") : "—"));
      drw.appendChild(kv);

      // WAN RTT 4h limit chart
      var hist = (it.rtt_hist || []).filter(function (p) { return p && typeof p[1] === "number"; });
      if (hist.length >= 2) {
        var rlim = (doc.limits && doc.limits.wan_rtt) || [80, 150];
        var W = 440, H = 110, pl = 34, pr = 10, pt = 10, pb = 16, cw = W - pl - pr, ch = H - pt - pb;
        var mx = Math.max(rlim[1] * 1.15, Math.max.apply(null, hist.map(function (p) { return p[1]; })) * 1.1, 1);
        var yOf = function (v) { return pt + ch - Math.min(1, v / mx) * ch; };
        var rsvg = sv("svg", {class: "sm-rtt-svg", viewBox: "0 0 " + W + " " + H});
        rsvg.appendChild(sv("line", {x1: pl, y1: yOf(rlim[1]), x2: W - pr, y2: yOf(rlim[1]), stroke: "var(--critical)", "stroke-dasharray": "4 3"}));
        rsvg.appendChild(sv("line", {x1: pl, y1: yOf(rlim[0]), x2: W - pr, y2: yOf(rlim[0]), stroke: "var(--warning)", "stroke-dasharray": "4 3"}));
        var d = hist.map(function (p, i) { return (i === 0 ? "M" : "L") + (pl + (i / (hist.length - 1)) * cw).toFixed(1) + " " + yOf(p[1]).toFixed(1); }).join(" ");
        rsvg.appendChild(sv("path", {d: d, fill: "none", stroke: "var(--accent)", "stroke-width": 2}));
        drw.appendChild(h("div", "", [h("b", "", ["WAN RTT trend (warn " + rlim[0] + " / crit " + rlim[1] + " ms)"]), rsvg]));
      }
      if (it.issues && it.issues.length) {
        drw.appendChild(h("b", "", ["Active site issues (" + it.issues.length + ")"]));
        var ul = h("div", "sm-slist");
        it.issues.forEach(function (iss) {
          ul.appendChild(h("div", "sm-item " + iss.sev, [
            h("div", "sm-item-t", [h("b", "", [iss.obj]), h("span", "sm-badge " + iss.sev, [iss.sev.toUpperCase()])]),
            h("div", "sm-item-m", [(iss.ip ? iss.ip + " · " : "") + iss.why])
          ]));
        });
        drw.appendChild(ul);
      }
      teams = "[SITE " + statusWord(it.status) + "] " + it.name + " · " + it.up + "/" + it.nodes + " up (" + it.down + " down)" +
        (it.placed ? (" · " + it.lat.toFixed(3) + ", " + it.lon.toFixed(3)) : " · unplaced") + "\nSite map: " + location.href;
    } else {
      addKV("Endpoints", it.na + " (" + (it.ia || "if") + ") ↔ " + it.nb + " (" + (it.ib || "if") + ")");
      addKV("Utilization", (it.util == null ? "not read" : it.util + "%"));
      addKV("RTT / Loss", (it.rtt !== null ? (it.rtt + " ms") : "—") + " / " + (it.loss !== null ? (it.loss + "%") : "—"));
      addKV("Reason", it.why || "Operational");
      drw.appendChild(kv);
      teams = "[WAN " + statusWord(it.status) + "] " + it.a + " <-> " + it.b + " · Util " + (it.util == null ? "not read" : it.util + "%") + " · " + (it.why || "OK");
    }

    var acts = h("div", "sm-drw-acts");
    var msg = h("span", "sm-msg");
    if (k === "site" && it.placed) {
      var z = h("button", "sm-btn", ["Center on map"], {type: "button"});
      z.addEventListener("click", function () { S.cx = it.lon; S.cy = latToY(it.lat); S.zoom = Math.max(S.zoom, 5); S.drawer = null; render(); });
      acts.appendChild(z);
    }
    var cp = h("button", "sm-btn pri", ["Copy Teams summary"], {type: "button"});
    cp.addEventListener("click", function () { copyText(teams, msg); });
    acts.appendChild(cp);
    acts.appendChild(msg);
    drw.appendChild(acts);
    back.appendChild(drw);
  }

  function render() {
    document.documentElement.classList.toggle("sm-board", S.board);
    clear(ROOT);
    if (!S.data) {
      ROOT.appendChild(h("div", "sm-ban stale", [
        h("span", "tag", ["NOT READ"]),
        h("b", "", [S.failMsg || "Reading live SolarWinds site locations…"])
      ]));
      return;
    }
    var doc = S.data;
    var feed = S.failMsg ? {fresh: "stale", why: S.failMsg} : (doc.feed || {});
    var cnt = doc.counts || {};
    var sites = filteredSites();

    // 1. HUD
    var hud = h("div", "sm-hud");
    hud.appendChild(h("div", "sm-ttl", [
      h("h1", "", ["Site map"]),
      doc.enclave ? h("span", "enc", [doc.enclave]) : null
    ]));
    var tallies = h("div", "sm-tallies");
    [["all", "All " + (cnt.all || 0), ""], ["crit", "Crit " + (cnt.crit || 0), "crit"], ["warn", "Warn " + (cnt.warn || 0), "warn"],
      ["stale", "Stale " + (cnt.stale || 0), "stale"], ["ok", "OK " + (cnt.ok || 0), "ok"], ["unplaced", "Unplaced " + (cnt.unplaced || 0), "warn"]].forEach(function (t) {
      var b = h("button", "sm-tp " + t[2], [t[1]], {type: "button", "aria-pressed": S.filter === t[0] ? "true" : "false"});
      b.addEventListener("click", function () { S.filter = t[0]; render(); });
      tallies.appendChild(b);
    });
    hud.appendChild(tallies);

    var hudR = h("div", "sm-hud-r");
    hudR.appendChild(h("span", "sm-feed " + (feed.fresh || "not_read"), [
      h("i"),
      h("span", "", ["SolarWinds: " + (feed.fresh === "ok" ? fmtAge(feed.age_s) : feed.fresh === "late" ? ("LATE · " + fmtAge(feed.age_s)) : "not read")])
    ], {title: feed.why || "SolarWinds SWIS"}));
    hudR.appendChild(h("a", "sm-btn", ["Health wall"], {href: WALL_URL + "?area=health"}));
    var bBoard = h("button", "sm-btn", [S.board ? "Exit 1920 Board" : "1920 Board"], {type: "button", "aria-pressed": S.board ? "true" : "false"});
    bBoard.addEventListener("click", function () { S.board = !S.board; render(); });
    hudR.appendChild(bBoard);
    hud.appendChild(hudR);
    ROOT.appendChild(hud);

    // 2. Status banner (never green when stale/unread)
    var bSt = S.failMsg ? "stale" : (doc.state || "stale");
    var bTxt = S.failMsg ? S.failMsg + " — showing last known map"
      : bSt === "crit" ? ((cnt.crit || 0) + " site" + (cnt.crit === 1 ? "" : "s") + " critical")
      : bSt === "warn" ? ((cnt.warn || 0) + " site" + (cnt.warn === 1 ? "" : "s") + " warning")
      : bSt === "ok" ? ((cnt.placed || 0) + " placed sites current and OK")
      : ("Not current — " + (feed.why || "SolarWinds feed not read yet (shown grey, never green)"));
    ROOT.appendChild(h("div", "sm-ban " + bSt, [
      h("span", "tag", [statusWord(bSt)]),
      h("b", "", [bTxt]),
      cnt.unplaced ? h("span", "sub", [cnt.unplaced + " unplaced site(s) kept in queue — never dropped"]) : null
    ]));

    // 3. Toolbar
    var bar = h("div", "sm-bar");
    var vSeg = h("div", "sm-seg", [], {role: "group", "aria-label": "View mode"});
    [["split", "Split"], ["wall", "Map focus"], ["list", "List (fallback)"]].forEach(function (v) {
      var b = h("button", "", [v[1]], {type: "button", "aria-pressed": S.view === v[0] ? "true" : "false"});
      b.addEventListener("click", function () { S.view = v[0]; render(); });
      vSeg.appendChild(b);
    });
    bar.appendChild(vSeg);

    var regs = h("div", "sm-regs");
    (doc.regions || []).forEach(function (rg) {
      var b = h("button", "sm-btn", [rg.name + " (" + rg.count + ")"], {type: "button", "aria-pressed": S.region === rg.id ? "true" : "false"});
      b.addEventListener("click", function () {
        S.region = rg.id; S.clusterPop = null;
        if (rg.bounds) fitBounds(rg.bounds[0], rg.bounds[1], rg.bounds[2], rg.bounds[3]);
        else fitAllPlaced();
        render();
      });
      regs.appendChild(b);
    });
    bar.appendChild(regs);

    function mkChk(label, val, onChange) {
      var cb = h("input", "", [], {type: "checkbox"});
      cb.checked = !!val;
      cb.addEventListener("change", function () { onChange(cb.checked); render(); });
      return h("label", "sm-chk", [cb, label]);
    }
    bar.appendChild(mkChk("Cluster nearby", S.cluster, function (v) { S.cluster = v; S.clusterPop = null; }));
    bar.appendChild(mkChk("WAN links", S.wan, function (v) { S.wan = v; }));
    bar.appendChild(mkChk("Dim OK", S.dimOk, function (v) { S.dimOk = v; }));

    var barR = h("div", "sm-bar-r");
    var bTour = h("button", "sm-btn", [S.tour ? "Stop tour" : "Auto-tour"], {type: "button", "aria-pressed": S.tour ? "true" : "false"});
    bTour.addEventListener("click", function () { S.tour = !S.tour; S.tourTick = 0; render(); });
    barR.appendChild(bTour);
    if (S.tour) {
      var dw = h("span", "sm-dwell", [h("i")]);
      dw.firstChild.style.width = (S.tourTick * 10) + "%";
      barR.appendChild(dw);
    }
    var bPoll = h("button", "sm-btn", ["Poll now"], {type: "button"});
    bPoll.addEventListener("click", function () { fetchMap(true); });
    barR.appendChild(bPoll);
    bar.appendChild(barR);
    ROOT.appendChild(bar);

    // 4. Main stage (Split / Map focus / List fallback)
    var stage = h("div", "sm-stage mode-" + S.view);
    if (S.view === "list") {
      stage.appendChild(buildFallbackTable(sites));
    } else {
      stage.appendChild(buildMapCanvas(sites, doc.links || []));
      // Side list (placed worst-first + unplaced queue)
      var side = h("aside", "sm-side");
      var qIn = h("input", "sm-q", [], {type: "search", placeholder: "Search sites or regions…", value: S.query, "aria-label": "Search sites"});
      qIn.addEventListener("input", function () { S.query = qIn.value; render(); });
      side.appendChild(h("div", "sm-side-h", [h("b", "", ["Sites · worst first (" + sites.length + ")"]), qIn]));
      var slist = h("div", "sm-slist");
      var placedS = sites.filter(function (s) { return s.placed; });
      var unplS = sites.filter(function (s) { return !s.placed; });
      function siteCard(s) {
        var it = h("button", "sm-item " + s.status + (S.selected === s.id ? " sel" : ""), [
          h("div", "sm-item-t", [h("b", "", [s.name]), h("span", "sm-badge " + s.status, [statusWord(s.status)])]),
          h("div", "sm-item-m", [s.up + "/" + s.nodes + " up" + (s.down ? " · " + s.down + " down" : "") +
            (s.rtt !== null ? " · " + s.rtt + " ms" : "") + (s.placed ? "" : " · " + (s.unplaced_why || "no coordinates"))])
        ], {type: "button"});
        it.addEventListener("click", function () {
          S.selected = s.id; S.drawer = {kind: "site", item: s}; S.tour = false;
          if (s.placed) { S.cx = s.lon; S.cy = latToY(s.lat); S.zoom = Math.max(S.zoom, 4); }
          render();
        });
        return it;
      }
      if (!placedS.length && !unplS.length) {
        slist.appendChild(h("div", "sm-item stale", [h("div", "sm-item-m", ["No sites match the filter."])]));
      }
      placedS.forEach(function (s) { slist.appendChild(siteCard(s)); });
      if (unplS.length) {
        slist.appendChild(h("div", "sm-unpl-h", ["Unplaced sites (" + unplS.length + ") — never dropped"]));
        unplS.forEach(function (s) { slist.appendChild(siteCard(s)); });
      }
      side.appendChild(slist);
      stage.appendChild(side);
    }
    ROOT.appendChild(stage);
    renderDrawer();
  }

  window.addEventListener("mouseup", function () {
    if (DRAG.on) { DRAG.on = false; var mw = ROOT.querySelector(".sm-mapwrap"); if (mw) mw.classList.remove("drag"); }
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.target && (ev.target.tagName === "INPUT" || ev.target.tagName === "TEXTAREA")) return;
    if (ev.key === "Escape") { if (S.drawer) { S.drawer = null; renderDrawer(); } else if (S.board) { S.board = false; render(); } }
    else if (ev.key === "+" || ev.key === "=") { S.zoom = Math.min(20, S.zoom * 1.3); render(); }
    else if (ev.key === "-") { S.zoom = Math.max(1, S.zoom / 1.3); render(); }
    else if (ev.key === "0") { fitAllPlaced(); render(); }
  });
  document.addEventListener("portal-look", function () { if (S.data) render(); });

  var qs = new URLSearchParams(location.search);
  if (qs.get("board") === "1") S.board = true;
  if (qs.get("view") && ["split", "wall", "list"].indexOf(qs.get("view")) >= 0) S.view = qs.get("view");

  setInterval(function () {
    S.pollLeft--;
    if (S.pollLeft <= 0) { S.pollLeft = Math.max(15, Number(S.data && S.data.poll) || 60); fetchMap(false); }
    if (S.tour && S.data) {
      var placed = (S.data.sites || []).filter(function (s) { return s.placed; });
      if (placed.length) {
        S.tourTick++;
        if (S.tourTick >= 10) {
          S.tourTick = 0;
          S.tourIdx = (S.tourIdx + 1) % placed.length;
          var target = placed[S.tourIdx];
          S.selected = target.id;
          S.cx = target.lon;
          S.cy = latToY(target.lat);
          S.zoom = Math.max(4, S.zoom);
          render();
        } else {
          var dw = ROOT.querySelector(".sm-dwell i");
          if (dw) dw.style.width = (S.tourTick * 10) + "%";
        }
      }
    }
  }, 1000);

  fetchMap(false);
})();
