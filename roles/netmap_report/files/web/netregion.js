/* netregion.js (1.102.0) - the network map's Region view (World -> Map -> Region, docs/NETWORK_MAP.md): the placed sites'
   region on a flat map, fitted to them, drawn as SVG from the same sites and WAN links as the globe (netglobe.js, window.NG).
   Two layers, either or both (never none), the viewer's choice kept in this browser:
   - WAN traffic: a curve between two sites - width = bandwidth, colour = utilization (the four steps of the globe), dashes
     moving with the traffic (still when motion is off); a down link red and dashed, one not read grey and dotted. A hub's
     fan merges into a trunk for the last part of each line. Hover or focus a line: its card; the other lines fade.
   - AD replication (the data's "ad": the AD job's replication matched to the map's sites, plugins/module_utils/
     site_ad_layer.py): a thin line on the other side of the WAN curve, coloured by the time since the last success (the
     replication matrix's scale); with both on, the WAN lines fade; "How AD sites were matched" under the map.
   Sites: coloured by their worst status, sized by their devices; down and warning sites labelled first; a label never
   covers another or a dot (maplabel.js, shared with the AD report's map). Drag to move, wheel or + / - to zoom, Fit. No
   library, no inline script or style, no HTML from strings, offline. */
(function () {
  var box = document.getElementById('nm'), stage = box && box.querySelector('.ng-stage'), wrap = box && box.querySelector('.rg-wrap');
  if (!wrap || !window.NM || !window.NG || !window.SiteMap) return;
  var SM = window.SiteMap, sv = SM.sv, tip = wrap.querySelector('.rg-tip'), leg = wrap.querySelector('.rg-leg'), mt = box.querySelector('.rg-match');
  var perf = box.querySelector('.ng-perf'), lb = [].slice.call(box.querySelectorAll('.ng-layer'));
  var GL = { Up: '✓', Warning: '!', Down: '×', Unknown: '?' }, RAD = Math.PI / 180, LKEY = 'site-netmap-layers', BUNDLE = 6;
  var RANKC = { critical: 0, warning: 1, low: 2, ok: 3 }, RANKS = { Down: 0, Warning: 1, Unknown: 2, Up: 3 };
  var R = { k: 1, px: 0, py: 0, hover: '', sel: '', wan: true, ad: true, W: 0, H: 0, F: null };
  try { var kept = localStorage.getItem(LKEY); if (kept === 'wan' || kept === 'ad' || kept === 'wan,ad') { R.wan = /wan/.test(kept); R.ad = /ad/.test(kept); } } catch (e) { /* both */ }
  function dict() { return Object.create(null); }
  function el(t, c, kids, at) {
    var e = document.createElement(t); if (c) e.className = c;
    for (var k in at || {}) if (at[k] != null) e.setAttribute(k, at[k]);
    (kids || []).forEach(function (k) { if (k != null && k !== '') e.appendChild(typeof k === 'object' ? k : document.createTextNode(String(k))); });
    return e;
  }
  function w180(x) { return ((x + 540) % 360) - 180; }
  function ad() { var D = window.NM.data(); return D && D.ad || null; }
  function adReady() { var a = ad(); return !!(a && !a.why && a.sites); }
  function adOn() { return R.ad && adReady(); }
  // 1.102.0 review: the ages as the page is LOOKED AT - from each line's last success and the AD run's time (absolute), so
  // a page opened a day later (or the portal's kept map) shows its lines red and the run stale; -> a signature of the classes
  function aged() {
    var a0 = ad(); if (!a0 || a0.why) return '';
    var sc = a0.scale || { low: 4, warn: 6, crit: 24 }, now = Date.now(), sig = [];
    (a0.links || []).forEach(function (l) {
      if (l.never) l.h = null; else if (l.ls && !isNaN(Date.parse(l.ls))) l.h = Math.max(0, Math.round((now - Date.parse(l.ls)) / 36e5 * 10) / 10);
      l.cls = l.fail || l.h == null || l.h >= sc.crit ? 'critical' : l.h >= sc.warn ? 'warning' : sc.low && l.h >= sc.low ? 'low' : 'ok';
      sig.push(l.cls);
    });
    var t = Date.parse(a0.read_at || '');
    if (!isNaN(t)) { a0.age_h = Math.round((now - t) / 36e5 * 10) / 10; a0.stale = a0.age_h >= sc.crit; }
    return sig.join(',') + (a0.stale ? '!' : '');
  }
  function wanOn() { return R.wan || !adOn(); }                  // never none: no AD layer to show means the WAN one
  function on() { return box.classList.contains('ng-rg') && box.getAttribute('data-view') === 'world'; }
  function ago(h) { return h == null ? 'never' : h < 1 ? Math.round(h * 60) + ' min ago' : h < 48 ? (Math.round(h * 10) / 10) + ' h ago' : Math.round(h / 24) + ' days ago'; }
  function bpsText(b) { return b ? window.NG.speedText(b).replace(/([0-9])([KMGT]?)$/, '$1 $2') + 'bps' : 'speed not known'; }
  function plural(n, w) { return n + ' ' + w + (n === 1 ? '' : 's'); }

  /* ---- the frame: the placed sites' extent (across the widest empty stretch of longitude, so a region on both sides of the
          date line stays together), with room for the labels; zoom and pan on top ------------------------------------- */
  function frame(sites, W, H) {
    var n = sites.length; if (!n) return null;
    var a = sites.map(function (s) { return ((s.lon % 360) + 360) % 360; }).sort(function (x, y) { return x - y; }), gap = -1, at = 0;
    for (var i = 0; i < n; i++) { var g = (i + 1 < n ? a[i + 1] : a[0] + 360) - a[i]; if (g > gap) { gap = g; at = i; } }
    var span = n > 1 ? 360 - gap : 0, lam = w180(a[(at + 1) % n] + span / 2);
    var la = sites.map(function (s) { return s.lat; }), lo = Math.min.apply(0, la), hi = Math.max.apply(0, la), phi = (lo + hi) / 2;
    var kx = Math.max(0.2, Math.cos(phi * RAD)), mx = Math.min(130, W * 0.13), top = 40, bot = 34;
    var sc = Math.min((W - 2 * mx) / (Math.max(span, 8) * kx), (H - top - bot) / Math.max(hi - lo, 5)) * R.k;
    return { lam: lam, phi: phi, kx: kx, sc: sc, cx: W / 2 + R.px, cy: (top + H - bot) / 2 + R.py };
  }
  function pj(F, lat, lon) { return [F.cx + w180(lon - F.lam) * F.kx * F.sc, F.cy - (lat - F.phi) * F.sc]; }
  function land(F, W, H) {                             // Natural Earth's land, one path, only the rings near the view
    var d = '', halfLon = W / 2 / (F.kx * F.sc) + 30, halfLat = H / 2 / F.sc + 20;
    (window.NM_LAND || []).forEach(function (r) {
      var mid = 0, i; for (i = 0; i < r.length; i += 2) mid += r[i]; mid /= r.length / 2;
      var off = Math.round((F.lam - mid) / 360) * 360, near = false, part = '';
      for (i = 0; i < r.length; i += 2) {
        var x = r[i] + off - F.lam, y = r[i + 1] - F.phi;
        if (Math.abs(x) < halfLon && Math.abs(y) < halfLat) near = true;
        part += (i ? 'L' : 'M') + (F.cx + x * F.kx * F.sc).toFixed(1) + ',' + (F.cy - y * F.sc).toFixed(1);
      }
      if (near) d += part + 'Z';
    });
    return d;
  }

  /* ---- the WAN lines: a curve, or for a hub's fan a curve into a trunk shared by the lines that leave the hub the same way */
  function wanPaths(arcs, pos) {
    var deg = dict(), groups = dict(), out = [];
    arcs.forEach(function (A) { deg[A.a.name] = (deg[A.a.name] || 0) + 1; deg[A.b.name] = (deg[A.b.name] || 0) + 1; });
    arcs.forEach(function (A) {
      var p = pos[A.a.name], q = pos[A.b.name]; if (!p || !q) return;
      var hubA = deg[A.a.name] >= deg[A.b.name], hub = hubA ? A.a.name : A.b.name, hp = hubA ? p : q, fp = hubA ? q : p, len = Math.hypot(fp[0] - hp[0], fp[1] - hp[1]);
      var x = { A: A, p: p, q: q };
      if (deg[hub] >= BUNDLE && len > 60) {
        var ang = Math.atan2(fp[1] - hp[1], fp[0] - hp[0]), k = hub + '\u0001' + ((Math.round(ang / (Math.PI / 6)) % 12) + 12) % 12;   // one bucket across the ±π seam (review)
        x.hub = { name: hub, hp: hp, fp: fp, farIsB: hubA, len: len, u: [(fp[0] - hp[0]) / len, (fp[1] - hp[1]) / len] };
        (groups[k] = groups[k] || []).push(x);
      }
      out.push(x);
    });
    Object.keys(groups).forEach(function (k) {
      var g = groups[k], ux = 0, uy = 0, lens = g.map(function (x) { ux += x.hub.u[0]; uy += x.hub.u[1]; return x.hub.len; }).sort(function (a, b) { return a - b; });
      var L = Math.hypot(ux, uy) || 1, d = Math.max(16, Math.min(70, 0.15 * lens[lens.length >> 1], 0.3 * lens[0])), hp = g[0].hub.hp;
      var T = [hp[0] + ux / L * d, hp[1] + uy / L * d], u = [ux / L, uy / L];
      if (g.length < 2) return;                           // alone that way: a plain curve
      g.forEach(function (x) { x.trunk = { T: T, u: u, hp: hp }; });
    });
    out.forEach(function (x) {
      if (!x.trunk) { var c = SM.curve(x.p, x.q, 0.14); x.d = c.d; x.m = c.m; return; }
      var F = x.hub.fp, T = x.trunk.T, u = x.trunk.u, hp = x.trunk.hp, dx = T[0] - F[0], dy = T[1] - F[1], L = Math.hypot(dx, dy) || 1;
      var k = (x.hub.farIsB ? -1 : 1) * 0.14;           // the same side as the plain curve a -> b would bow
      var c1 = [F[0] + dx * 0.4 - dy * k * 1.6, F[1] + dy * 0.4 + dx * k * 1.6], c2 = [T[0] + u[0] * L * 0.3, T[1] + u[1] * L * 0.3];
      x.d = 'M' + F[0].toFixed(1) + ',' + F[1].toFixed(1) + ' C' + c1[0].toFixed(1) + ',' + c1[1].toFixed(1) + ' ' + c2[0].toFixed(1) + ',' + c2[1].toFixed(1) +
        ' ' + T[0].toFixed(1) + ',' + T[1].toFixed(1) + ' L' + hp[0].toFixed(1) + ',' + hp[1].toFixed(1);
      x.m = [(F[0] + c1[0] * 3 + c2[0] * 3 + T[0]) / 8, (F[1] + c1[1] * 3 + c2[1] * 3 + T[1]) / 8];
    });
    return out;
  }
  function band(u) { return u < 25 ? 'b0' : u < 50 ? 'b1' : u < 75 ? 'b2' : 'b3'; }

  /* ---- the cards ------------------------------------------------------------------------------------------------------ */
  function show(kids, ev, at) {
    tip.replaceChildren.apply(tip, kids.filter(Boolean));
    // a point of the drawing (keyboard focus) scaled by the drawing itself, not the box (the legend under it is in the box)
    var r = wrap.getBoundingClientRect(), sr = svg ? svg.getBoundingClientRect() : r;
    var x = ev && ev.clientX != null ? ev.clientX - r.left : sr.left - r.left + at[0] / R.W * sr.width, y = ev && ev.clientY != null ? ev.clientY - r.top : sr.top - r.top + at[1] / R.H * sr.height;
    tip.hidden = false;
    var tw = tip.offsetWidth || 320, th = tip.offsetHeight || 120;
    tip.style.left = Math.max(4, Math.min(x + 14, r.width - tw - 4)) + 'px';
    tip.style.top = Math.max(4, y + 14 + th > r.height ? y - th - 10 : y + 14) + 'px';
  }
  function adLines(ls) {
    return ls.slice().sort(function (x, y) { return RANKC[x.cls] - RANKC[y.cls] || (y.h == null ? 1e9 : y.h) - (x.h == null ? 1e9 : x.h); });
  }
  function wanCard(x, adg) {
    var A = x.A, l = A.l, ifs = A.ls.map(function (k) { var a = byId[k.a], b = byId[k.b];
      return (a ? a.name : '?') + ' ' + (k.ia || '') + ' ↔ ' + (b ? b.name : '?') + ' ' + (k.ib || ''); });
    var kids = [el('b', '', [A.a.name + ' ↔ ' + A.b.name]),
      el('div', '', [plural(A.ls.length, 'WAN link') + ' · ' + bpsText(A.bw) + ' · ' + (l.status === 'Down' ? 'no traffic' : l.status === 'Unknown' || l.known === false ? 'traffic not known' : 'busiest ' + Math.max(l['in'], l.out) + '% (' + l['in'] + '% in / ' + l.out + '% out)')]),
      l.status !== 'Up' ? el('div', 'k-' + l.status.toLowerCase(), [GL[l.status] + ' ' + l.status + (l.why ? ' - ' + l.why : '')]) : null]
      .concat(ifs.slice(0, 3).map(function (t) { return el('div', 'm', [t]); }))
      .concat([ifs.length > 3 ? el('div', 'm', ['+' + (ifs.length - 3) + ' more']) : null]);
    var g = adOn() && adg[A.key];
    if (g) {
      var w = adLines(g.ls)[0];
      kids.push(el('div', 'ad', [el('b', '', ['AD replication over it: ' + (g.ls.length > 1 ? plural(g.ls.length, 'connection') : w.a + ' ↔ ' + w.b)]),
        (g.ls.length > 1 ? w.a + ' ↔ ' + w.b + ': ' : '') + 'last success ' + ago(w.h) + (w.fail ? '; failing' + (w.err ? ' ' + w.err : '') : ''), asOf()]));
    }
    return kids;
  }
  function adCard(g, wan) {
    var ls = adLines(g.ls), w = ls[0];
    var kids = [el('b', '', ['AD replication: ' + (ls.length > 1 ? plural(ls.length, 'connection') : w.a + ' ↔ ' + w.b)]),
      el('div', 'm', [g.a + ' ↔ ' + g.b + ' (the network map’s sites)'])];
    ls.slice(0, 4).forEach(function (l) {
      kids.push(el('div', l === w ? '' : 'm', [(ls.length > 1 ? l.a + ' ↔ ' + l.b + ': ' : '') + 'last success ' + ago(l.h) + (l.fail ? '; failing' + (l.err ? ' ' + l.err : '') : '') +
        (l.parts && l.parts.length && ls.length === 1 ? ' · partitions: ' + l.parts.join(', ') : '')]));
    });
    if (ls.length > 4) kids.push(el('div', 'm', ['+' + (ls.length - 4) + ' more']));
    kids.push(asOf());
    kids.push(el('div', 'ad', [wan ? 'WAN: ' + bpsText(wan.bw) + ', ' + (wan.l.status === 'Down' ? 'down' : wan.l.status === 'Unknown' ? 'not read' : 'busiest ' + Math.max(wan.l['in'], wan.l.out) + '%') +
      (wan.ls.length > 1 ? ' (' + wan.ls.length + ' links)' : '') : 'WAN: no link between these sites in SolarWinds']));
    return kids;
  }
  // the AD run's time, and when its readings are stale or its run failed (1.102.0 review: never drawn as fresh)
  function asOf() {
    var a0 = ad(); if (!a0 || a0.why) return null;
    return el('div', a0.stale || a0.run_status === 'failed' ? 'k-down' : 'm', ['AD read ' + (a0.as_of || '?') + (a0.stale ? ' - stale (' + a0.age_h + ' h old)' : '') +
      (a0.run_status === 'failed' ? ' - that AD run failed' + (a0.job ? ' (job ' + a0.job + ')' : '') : '')]);
  }
  // a site, or the sites on one spot (1.102.0 review: every one of them, each with its AD sites)
  function siteCard(g, adAt) {
    var s = g[0], kids = [el('b', '', [s.name + (g.length > 1 ? ' and ' + (g.length - 1) + ' more here' : '')])];
    g.forEach(function (x, i) {
      var ads = adAt[x.name] || [];
      kids.push(el('div', 'k-' + x.worst.toLowerCase(), [(g.length > 1 ? x.name + ': ' : '') + GL[x.worst] + ' ' + x.worst]));
      if (i < 6) kids.push(el('div', '', [plural(x.n, 'device') + ' · ' + x.c.Down + ' down, ' + x.c.Warning + ' warning']));
      if (ads.length) kids.push(el('div', 'ad', ['AD' + (g.length > 1 ? ' (' + x.name + ')' : '') + ': ' + ads.slice(0, 4).join(', ') + (ads.length > 4 ? ' +' + (ads.length - 4) : '')]));
    });
    kids.push(el('div', 'm', [g.length > 1 ? 'Zoom in to part them; click (or Enter) opens one' : 'Click (or Enter) to open it']));
    return kids;
  }

  /* ---- the drawing ---------------------------------------------------------------------------------------------------- */
  var byId = dict(), svg = null, hits = [];
  function hot(key) {                                  // a line pointed at or selected: the others fade
    R.hover = key || '';
    var k = R.sel || R.hover;
    if (!svg) return;
    svg.classList.toggle('rg-hot', !!k);
    [].forEach.call(svg.querySelectorAll('.rg-wl,.rg-al'), function (g) { g.classList.toggle('on', g.getAttribute('data-k') === k); });
  }
  function draw() {
    if (!on()) return;
    var sites = window.NG.sites(), arcs = window.NG.arcs(), D = window.NM.data();
    if (!D) return;
    byId = dict(); (D.nodes || []).forEach(function (n) { byId[n.id] = n; });
    var W = Math.max(320, Math.round(wrap.clientWidth || stage.clientWidth || 1000)), H = Math.round(Math.max(360, Math.min(660, W * 0.56)));
    R.W = W; R.H = H;
    var F = R.F = frame(sites, W, H);
    var s0 = svg; svg = sv('svg', { 'class': 'rg-svg', viewBox: '0 0 ' + W + ' ' + H, width: W, height: H, role: 'group',
      'aria-label': 'The placed sites’ region: ' + plural(sites.length, 'site') + ', their WAN links' + (adOn() ? ' and the AD replication between them' : '') + '. Tab to a line or a site for its card.' });
    svg.classList.toggle('rg-both', adOn() && wanOn());
    if (s0) wrap.replaceChild(svg, s0); else wrap.insertBefore(svg, tip);
    hits = [];
    if (!F) { drawLegend(); drawMatch(); return; }
    svg.appendChild(sv('path', { d: land(F, W, H), 'class': 'rg-land' }));
    var pos = dict(); sites.forEach(function (s) { pos[s.name] = pj(F, s.lat, s.lon); });
    var gW = sv('g', { 'class': 'rg-wan' }), gA = sv('g', { 'class': 'rg-ads' }), gS = sv('g', { 'class': 'rg-sites' });
    svg.appendChild(gW); svg.appendChild(gA); svg.appendChild(gS);
    lastSig = aged();
    var a0 = ad(), adg = dict(), adAt = dict();
    if (adOn()) {
      (a0.links || []).forEach(function (l) {
        if (!l.nma || !l.nmb || !pos[l.nma] || !pos[l.nmb]) return;
        var p = [l.nma, l.nmb].sort(), k = p[0] + '\u0001' + p[1];
        (adg[k] = adg[k] || { k: k, a: p[0], b: p[1], ls: [] }).ls.push(l);
      });
      (a0.sites || []).forEach(function (x) { if (x.nm) (adAt[x.nm] = adAt[x.nm] || []).push(x.n); });
    }
    var wanBy = dict(); arcs.forEach(function (A) { wanBy[A.key] = A; });
    function hit(g, d, key, label, card, mid, w) {
      var h = sv('path', { d: d, 'class': 'rg-hit', tabindex: '0', role: 'button', 'aria-label': label, 'stroke-width': Math.max(14, w + 8) });
      h.addEventListener('mousemove', function (ev) { if (!R.sel) hot(key); if (!R.sel || R.sel === key) show(card(), ev); });
      h.addEventListener('mouseleave', function () { if (!R.sel) { hot(''); tip.hidden = true; } });
      h.addEventListener('focus', function () { hot(key); show(card(), null, mid); });
      h.addEventListener('blur', function () { if (!R.sel) { hot(''); tip.hidden = true; } });
      h.addEventListener('click', function (ev) { ev.stopPropagation(); R.sel = R.sel === key ? '' : key; hot(key); if (R.sel) show(card(), ev); else tip.hidden = true; });
      h.addEventListener('keydown', function (ev) {          // Enter or Space keeps (or lets go of) the card, as a click does
        if (ev.key !== 'Enter' && ev.key !== ' ') return;
        ev.preventDefault(); R.sel = R.sel === key ? '' : key; hot(key); if (R.sel) show(card(), null, mid); else tip.hidden = true;
      });
      g.appendChild(h); hits.push({ key: key, el: h, mid: mid, card: card });
    }
    var maxBw = 0;
    if (wanOn()) {
      var SEV = { Up: 0, Unknown: 1, Warning: 2, Down: 3 };   // 1.102.0 review: drawn least first - a down link is never under an up one on a trunk
      wanPaths(arcs, pos).sort(function (a, b) { return (SEV[a.A.l.status] || 0) - (SEV[b.A.l.status] || 0); }).forEach(function (x) {
        var A = x.A, l = A.l, st = l.status, u = Math.max(l['in'] || 0, l.out || 0);
        var g = sv('g', { 'class': 'rg-wl', 'data-k': A.key }), w = A.w;
        var nk = l.known === false;                      // no utilization read: "traffic not known", never 0% (review)
        var c = st === 'Down' ? 'down' : st === 'Unknown' ? 'unk' : st === 'Warning' ? 'warn' : nk ? 'nk' : band(u);
        g.appendChild(sv('path', { d: x.d, 'class': 'rg-case', 'stroke-width': (w + 3).toFixed(1) }));
        g.appendChild(sv('path', { d: x.d, 'class': 'rg-w ' + c, 'stroke-width': (st === 'Down' || st === 'Unknown' ? Math.max(2, w * 0.8) : w).toFixed(1) }));
        if (st !== 'Down' && st !== 'Unknown' && !nk) {  // the traffic: dashes that move faster the busier the link
          var f = sv('path', { d: x.d, 'class': 'rg-flow', 'stroke-width': Math.max(1, w * 0.45).toFixed(1) });
          f.style.animationDuration = (6 / (0.3 + u / 40)).toFixed(2) + 's'; g.appendChild(f);
        }
        gW.appendChild(g);
        hit(g, x.d, A.key, A.a.name + ' to ' + A.b.name + ': ' + plural(A.ls.length, 'WAN link') + ', ' + st + (st === 'Up' || st === 'Warning' ? ', busiest ' + u + '%' : ''),
          function () { return wanCard(x, adg); }, x.m, w);
        maxBw = Math.max(maxBw, A.bw || 0);
      });
    }
    Object.keys(adg).forEach(function (k) {
      var g0 = adg[k], ls = adLines(g0.ls), cls = ls[0].cls, cu = SM.curve(pos[g0.a], pos[g0.b], -0.14);
      var g = sv('g', { 'class': 'rg-al', 'data-k': 'ad\u0001' + k });
      g.appendChild(sv('path', { d: cu.d, 'class': 'rg-adcase' }));
      g.appendChild(sv('path', { d: cu.d, 'class': 'rg-ad k-' + cls }));
      gA.appendChild(g);
      hit(g, cu.d, 'ad\u0001' + k, 'AD replication ' + (ls.length > 1 ? plural(ls.length, 'connection') : ls[0].a + ' to ' + ls[0].b) + ' between ' + g0.a + ' and ' + g0.b + ', last success ' + ago(ls[0].h),
        function () { return adCard(g0, wanBy[k]); }, cu.m, 3);
    });
    /* the sites: dots first (no label on any), then the labels - down, warning, the biggest first; down ones always */
    var LB = SM.labels(svg, W, H, { probe: 'rg-s', sub: 'sub', lead: 'rg-lead', line: 13 });
    var list = sites.filter(function (s) { var p = pos[s.name]; return p[0] > -20 && p[0] < W + 20 && p[1] > -20 && p[1] < H + 20; })
      .sort(function (a, b) { return (RANKS[a.worst] - RANKS[b.worst]) || (b.n - a.n) || (a.name < b.name ? -1 : 1); });
    list.forEach(function (s) { s.rgr = Math.min(13, 3.5 + 1.1 * Math.sqrt(s.n || 1)); });
    var groups = [];                                     // dots on top of each other: one label, the worst first, "+N"
    list.forEach(function (s) {
      var p = pos[s.name], g = groups.filter(function (g) { var q = pos[g[0].name]; return Math.hypot(q[0] - p[0], q[1] - p[1]) < Math.max(6, (g[0].rgr + s.rgr) * 0.6); })[0];
      if (g) g.push(s); else groups.push([s]);
    });
    groups.forEach(function (g) { g.forEach(function (s) { var p = pos[s.name]; LB.dot(p[0], p[1], s.rgr); }); });
    var gD = sv('g'), gL = sv('g'); gS.appendChild(gD); gS.appendChild(gL);
    groups.slice().reverse().forEach(function (g) {      // the worst drawn last: on top
      g.slice().reverse().forEach(function (s) {
        var p = pos[s.name], d = sv('g', { 'class': 'rg-s k-' + s.worst.toLowerCase(), tabindex: '0', role: 'button',
          'aria-label': s.name + ', ' + s.worst + ', ' + plural(s.n, 'device') + ' - open' });
        d.appendChild(sv('circle', { cx: p[0].toFixed(1), cy: p[1].toFixed(1), r: s.rgr.toFixed(1) }));
        if (s.worst !== 'Up') d.appendChild(sv('text', { x: p[0].toFixed(1), y: (p[1] + 3.6).toFixed(1), 'text-anchor': 'middle', 'class': 'gl' }, [GL[s.worst]]));
        var go = function () { if (window.nmOpenSite) window.nmOpenSite(s.name); };
        d.addEventListener('click', function (ev) { ev.stopPropagation(); go(); });
        d.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); go(); } });
        d.addEventListener('mousemove', function (ev) { if (!R.sel) show(siteCard(g, adAt), ev); });
        d.addEventListener('focus', function () { show(siteCard(g, adAt), null, p); });
        d.addEventListener('mouseleave', function () { if (!R.sel) tip.hidden = true; });
        d.addEventListener('blur', function () { if (!R.sel) tip.hidden = true; });
        gD.appendChild(d);
      });
    });
    groups.forEach(function (g) {
      var s = g[0], p = pos[s.name], ads = [].concat.apply([], g.map(function (x) { return adAt[x.name] || []; })), lines = [s.name + (g.length > 1 ? ' +' + (g.length - 1) : '')];
      if (ads.length) lines.push('AD: ' + ads[0] + (ads.length > 1 ? ' +' + (ads.length - 1) : ''));
      var t = sv('g', { 'class': 'rg-s rg-lab k-' + s.worst.toLowerCase(), 'aria-hidden': 'true' });
      if (LB.place(t, p[0] + s.rgr + 6, p[1] + 4, lines, s.worst === 'Down', false, s.rgr, p[1])) gL.appendChild(t);
    });
    LB.done();
    hot(R.hover);
    if (R.sel) { var hs = hits.filter(function (h) { return h.key === R.sel; })[0]; if (hs) { hot(R.sel); show(hs.card(), null, hs.mid); } else { R.sel = ''; tip.hidden = true; } }
    if (perf) perf.textContent = plural(sites.length, 'site') + ' · ' + plural(arcs.length, 'WAN link') + (adOn() ? ' · ' + plural(Object.keys(adg).length, 'AD line') : '');
    drawLegend(); drawMatch();
  }
  function drawLegend() {
    var a0 = ad(), kids = [];
    if (wanOn()) kids = kids.concat([el('span', 't', ['WAN']), el('span', '', [el('i', 'b0'), 'under 25%']), el('span', '', [el('i', 'b1'), '25-50%']),
      el('span', '', [el('i', 'b2'), '50-75%']), el('span', '', [el('i', 'b3'), '75% and over']), el('span', '', [el('i', 'warn'), 'warning']),
      el('span', '', [el('i', 'down'), 'down']), el('span', '', [el('i', 'unk'), 'not read']), el('span', '', [el('i', 'nk'), 'traffic not known']), el('span', '', ['width = bandwidth · dashes = traffic'])]);
    if (adOn()) {
      var sc = a0.scale || { low: 4, warn: 6, crit: 24 };
      kids = kids.concat([el('span', 't', ['AD replication']), el('span', '', [el('i', 'ok'), 'under ' + (sc.low || sc.warn) + ' h']),
        sc.low ? el('span', '', [el('i', 'low'), sc.low + '-' + sc.warn + ' h']) : null,
        el('span', '', [el('i', 'warning'), sc.warn + '-' + sc.crit + ' h']), el('span', '', [el('i', 'critical'), sc.crit + ' h or failing'])]);
      var ao = asOf(); if (ao) kids.push(el('span', a0.stale || a0.run_status === 'failed' ? 'why bad' : 'why', [ao.textContent]));
    } else if (a0 && a0.why) kids.push(el('span', 'why', ['AD replication: ' + a0.why]));
    leg.replaceChildren.apply(leg, kids.filter(Boolean));
  }
  function drawMatch() {
    var a0 = ad();
    mt.hidden = !(on() && adOn());
    if (mt.hidden) { mt.replaceChildren(); return; }
    var rows = a0.sites || [], MAX = 300, tb = el('tbody');
    rows.slice(0, MAX).forEach(function (x) {
      tb.appendChild(el('tr', '', [el('td', '', [x.n]), el('td', 'rg-mono', [x.nm || '-']), el('td', x.by === 'none' ? 'none' : '', [x.by]), el('td', 'n', [String(x.dcs)]), el('td', '', [x.why])]));
    });
    var at = a0.as_of || a0.read_at || '?', src = a0.source === 'db' ? 'the report database (the AD job’s run of ' + at + ')' : 'the AD job’s run of ' + at + ' (its job artifact)';
    mt.replaceChildren(el('h3', '', ['How AD sites were matched']),
      el('p', '', ['Each AD site to a network-map site: ad_site_map first (set by hand), then the DCs’ own nodes (all of a site’s DCs in one network-map site), then the same site code without separators, then the same name in any case. Set ad_site_map for the rest. Read from ' + src + '.' +
        (a0.same ? ' ' + plural(a0.same, 'replication connection') + ' inside one network-map site ' + (a0.same === 1 ? 'is' : 'are') + ' not drawn.' : '')]),
      el('div', 'rg-tw', [el('table', '', [el('thead', '', [el('tr', '', [el('th', '', ['AD site']), el('th', '', ['Network map site']), el('th', '', ['Matched by']), el('th', 'n', ['DCs']), el('th', '', ['Why'])])]), tb])]),
      rows.length > MAX ? el('p', '', [(rows.length - MAX) + ' more AD sites (the AD report has them all)']) : el('span'));
  }

  /* ---- the layers, zoom, pan ------------------------------------------------------------------------------------------- */
  function marks() {
    var a0 = ad(), ok = adReady();
    lb.forEach(function (b) {
      var l = b.getAttribute('data-l');
      if (l === 'ad') { b.disabled = !ok; b.title = ok ? '' : 'AD replication: ' + (a0 && a0.why || 'not read'); b.setAttribute('aria-pressed', String(adOn())); }
      else b.setAttribute('aria-pressed', String(wanOn()));
    });
  }
  lb.forEach(function (b) {
    b.addEventListener('click', function () {
      var l = b.getAttribute('data-l'), want = b.getAttribute('aria-pressed') !== 'true';
      // either or both, never none: turning off the only one shown turns the other on (no AD data: the WAN layer stays)
      if (l === 'wan') { if (!want && !adReady()) return; R.wan = want; if (!want) R.ad = true; }
      else { R.ad = want; if (!want) R.wan = true; }
      try { localStorage.setItem(LKEY, [R.wan ? 'wan' : '', R.ad ? 'ad' : ''].filter(Boolean).join(',')); } catch (e) { /* this page only */ }
      R.sel = ''; tip.hidden = true; marks(); draw();
    });
  });
  function zoom(f) {
    if (!f) { R.k = 1; R.px = 0; R.py = 0; }             // Fit
    else { var k = Math.max(0.5, Math.min(12, R.k * f)); f = k / R.k; R.k = k; R.px *= f; R.py *= f; }
    tip.hidden = true; draw();
  }
  var drag = null, raf = 0;
  wrap.addEventListener('pointerdown', function (e) {
    if (e.target.closest && e.target.closest('.rg-hit,.rg-s')) return;
    drag = { x: e.clientX, y: e.clientY, px: R.px, py: R.py, moved: false };
  });
  wrap.addEventListener('pointermove', function (e) {
    if (!drag) return;
    var dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (!drag.moved && Math.abs(dx) + Math.abs(dy) < 4) return;
    drag.moved = true; wrap.classList.add('drag'); tip.hidden = true;
    var r = wrap.getBoundingClientRect(), s = R.W / (r.width || R.W);
    R.px = drag.px + dx * s; R.py = drag.py + dy * s;
    if (!raf) raf = requestAnimationFrame(function () { raf = 0; draw(); });
  });
  ['pointerup', 'pointercancel', 'pointerleave'].forEach(function (t) { wrap.addEventListener(t, function () { if (drag) { drag = null; wrap.classList.remove('drag'); } }); });
  wrap.addEventListener('click', function (e) { if (e.target.closest && e.target.closest('.rg-hit,.rg-s')) return; if (R.sel) { R.sel = ''; hot(''); tip.hidden = true; } });
  wrap.addEventListener('wheel', function (e) { if (!on()) return; e.preventDefault(); zoom(e.deltaY < 0 ? 1.15 : 1 / 1.15); }, { passive: false });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && R.sel) { R.sel = ''; hot(''); tip.hidden = true; } });
  var rt = 0, lastW = 0;
  function resized() { var w = wrap.clientWidth; if (!on() || !w || w === lastW) return; lastW = w; clearTimeout(rt); rt = setTimeout(draw, 120); }
  if ('ResizeObserver' in window) new ResizeObserver(resized).observe(stage); else window.addEventListener('resize', resized);

  // netglobe.js calls update() after each new read and when Region is picked; the layers, zoom and selection stay
  var lastSig = '';                                    // a threshold passed while the page is open: drawn again
  setInterval(function () { if (on() && adReady()) { var s = aged(); if (s !== lastSig) draw(); } }, 60000);
  window.RG = { update: function () { marks(); if (on()) { lastW = wrap.clientWidth; draw(); } else { tip.hidden = true; mt.hidden = true; } }, zoom: zoom };
  window.rgTest = { R: R, draw: draw, hits: function () { return hits.map(function (h) { return h.key; }); }, wanPaths: wanPaths,
    mid: function (key) { var h = hits.filter(function (x) { return x.key === key; })[0]; return h ? h.mid : null; },
    card: function (key) { var h = hits.filter(function (x) { return x.key === key; })[0]; if (!h) return false; h.el.dispatchEvent(new FocusEvent('focus')); return true; },
    select: function (key) { R.sel = key || ''; hot(R.sel); var h = hits.filter(function (x) { return x.key === key; })[0]; if (h) show(h.card(), null, h.mid); else tip.hidden = true; } };
  window.RG.update();
})();
