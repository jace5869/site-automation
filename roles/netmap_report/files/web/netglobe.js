/* netglobe.js - the network map's world view (roles/netmap_report, docs/NETWORK_MAP.md): the sites on a globe
   (orthographic) or a flat map (equirectangular) - Globe / Flat in the map's bar, the viewer's choice kept in this browser,
   the site's default netmap_view; the switch unrolls the globe into the flat map (and back). The WAN links as great-circle
   arcs: width = bandwidth, colour = how busy (one blue ramp), dashes that move in two lanes (in and out) at a speed set by
   their %, a down link red, dashed and still. Drag to turn, wheel to zoom, click a site to fly in and open it in the Sites
   view (netmap.js). Sites without coordinates wait in the Unplaced tray. One canvas, drawn each frame while something
   moves; the land is Natural Earth 1:110m (world-land.js, public domain). No library, no inline script, no HTML from
   strings, offline. Motion off (report_motion / portal_motion off, the system's reduced motion unless always,
   ?motion=off): nothing turns or flows, the switch and the fly-in are jumps; dragging still works. New data (the portal's
   live page) comes through NM.on: the view stays where it is. */
(function () {
  var box = document.getElementById('nm'), cv = box && box.querySelector('.ng-cv'); if (!cv || !window.NM_LAND || !window.NM) return;
  var ctx = cv.getContext('2d'), tip = box.querySelector('.ng-tip'), perf = box.querySelector('.ng-perf'), tray = box.querySelector('.ng-tray');
  var RAD = Math.PI / 180, GL = { Up: '✓', Warning: '!', Down: '×', Unknown: '?' };
  var HOME = { lam: -35, phi: 28, k: 1 }, KEY = 'site-netmap-view', DEBUG = /[?&]debug=1\b/.test(location.search);
  var G = { lam: HOME.lam, phi: HOME.phi, k: HOME.k, flat: false, region: false, mix: 0, morph: null, W: 0, H: 0, dpr: 1, hover: null, spin: true, idle: 0, fly: null };
  function dict() { return Object.create(null); }      // keyed by site names from SolarWinds: no inherited keys
  function own(o, k) { return !!o && Object.prototype.hasOwnProperty.call(o, k); }
  var sites = [], bySite = dict(), arcs = [], D = null;
  function still() { return window.NM.still(); }

  /* ---- the data: sites (devices, worst status) and the WAN links between them, from the same pull as the Sites view ---- */
  function vec(lat, lon) { return [Math.cos(lat * RAD) * Math.cos(lon * RAD), Math.cos(lat * RAD) * Math.sin(lon * RAD), Math.sin(lat * RAD)]; }
  function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
  function bps(t) { var m = /^([\d.]+)\s*([KMGT]?)/i.exec(t || ''); return m ? parseFloat(m[1]) * ({ '': 1, K: 1e3, M: 1e6, G: 1e9, T: 1e12 })[m[2].toUpperCase()] : 0; }
  function speedText(b) { var u = [[1e12, 'T'], [1e9, 'G'], [1e6, 'M'], [1e3, 'K']]; for (var i = 0; i < u.length; i++) if (b >= u[i][0]) { var v = b / u[i][0]; return (Math.abs(v - Math.round(v)) < .05 ? Math.round(v) : v.toFixed(1)) + u[i][1]; } return b ? String(b) : ''; }
  // 1.102.0: the state of a pair of sites' WAN links, one rule for the globe, the flat map and the Region view: any up (or
  // warning) -> Up, or Warning when one is down, not known or warning; none up and one down -> Down, even when the others
  // were not read (1.95.1's review: "1 down, the others not read" is Down, not Unknown); none read at all -> Unknown
  function wanState(ls) {
    var n = ls.length, up = ls.filter(function (l) { return l.status === 'Up' || l.status === 'Warning'; }), dn = ls.filter(function (l) { return l.status === 'Down'; });
    if (!up.length && dn.length) return { status: 'Down', why: dn.length === n ? (n > 1 ? 'all ' + n + ' links down' : (dn[0].why || 'down'))
      : dn.length + ' down, the other' + (n - dn.length === 1 ? '' : 's') + ' not read' };
    if (!up.length) return { status: 'Unknown', why: 'not in the last status read' };
    var warn = up.length < n || up.some(function (l) { return l.status === 'Warning'; });
    return { status: warn ? 'Warning' : 'Up', why: up.length < n ? (n - up.length) + ' of ' + n + ' links down or not known'
      : (up.filter(function (l) { return l.status === 'Warning'; }).map(function (l) { return l.why; })[0] || '') };
  }
  function build(data, changes) {
    D = data;
    var byId = dict(), old = bySite; D.nodes.forEach(function (n) { byId[n.id] = n; });
    sites = D.sites.filter(function (s) { return own(D.geo, s) && Array.isArray(D.geo[s]); }).map(function (s) {
      var ns = D.nodes.filter(function (n) { return n.site === s && !n.gone; }), c = { Up: 0, Warning: 0, Down: 0, Unknown: 0 };
      ns.forEach(function (n) { c[n.status]++; });
      var worst = c.Down ? 'Down' : c.Warning ? 'Warning' : c.Unknown && c.Unknown === ns.length ? 'Unknown' : ns.length ? 'Up' : 'Unknown', g = D.geo[s];
      var o = old[s], flash = o && o.worst !== worst && (worst === 'Down' || o.worst === 'Down') ? performance.now() : o ? o.flash : 0;
      return { name: s, lat: g[0], lon: g[1], v: vec(g[0], g[1]), n: ns.length, c: c, worst: worst, r: 3 + 1.1 * Math.sqrt(ns.length), flash: flash, p: o ? o.p : null };
    });
    bySite = dict(); sites.forEach(function (s) { bySite[s.name] = s; });
    var pair = dict();                                    // the WAN links of two sites, as one arc: their bandwidth added up
    D.links.forEach(function (l) {
      var a = byId[l.a], b = byId[l.b]; if (!a || !b || a.site === b.site || l.gone) return;
      var A = bySite[a.site], B = bySite[b.site]; if (!A || !B) return;
      var flip = A.name > B.name, k = flip ? B.name + '\u0001' + A.name : A.name + '\u0001' + B.name;
      var p = pair[k] = pair[k] || { a: flip ? B : A, b: flip ? A : B, ls: [] };
      p.ls.push({ l: l, pin: flip ? l.out : l['in'], pout: flip ? l['in'] : l.out });
    });
    var oldArcs = dict(); arcs.forEach(function (A) { oldArcs[A.key] = A; });
    arcs = Object.keys(pair).map(function (k) {
      var p = pair[k], a = p.a, b = p.b, ls = p.ls, st = wanState(ls.map(function (x) { return x.l; })), up = ls.filter(function (x) { return x.l.status === 'Up' || x.l.status === 'Warning'; });
      var unk = st.status === 'Unknown', down = st.status === 'Down', why = st.why;
      var bw = ls.reduce(function (s, x) { return s + bps(x.l.speed); }, 0);
      var pin = up.reduce(function (m, x) { return Math.max(m, x.pin || 0); }, 0), pout = up.reduce(function (m, x) { return Math.max(m, x.pout || 0); }, 0);
      var om = Math.acos(Math.max(-1, Math.min(1, dot(a.v, b.v)))), pts = [], ll = [];
      for (var i = 0; i <= 64; i++) {
        var t = i / 64, s0 = om ? Math.sin((1 - t) * om) / Math.sin(om) : 1 - t, s1 = om ? Math.sin(t * om) / Math.sin(om) : t;
        var q = [a.v[0] * s0 + b.v[0] * s1, a.v[1] * s0 + b.v[1] * s1, a.v[2] * s0 + b.v[2] * s1], h = 1 + (.02 + .07 * om / Math.PI) * Math.sin(Math.PI * t);
        pts.push([q[0] * h, q[1] * h, q[2] * h]);
        ll.push([Math.atan2(q[1], q[0]) / RAD, Math.asin(Math.max(-1, Math.min(1, q[2] / (Math.hypot(q[0], q[1], q[2]) || 1)))) / RAD]);
      }
      for (var j = 1; j < ll.length; j++) { while (ll[j][0] - ll[j - 1][0] > 180) ll[j][0] -= 360; while (ll[j][0] - ll[j - 1][0] < -180) ll[j][0] += 360; }   // one line across the date line
      var o = oldArcs[k], status = st.status;
      var known = up.some(function (x) { return x.l['in'] != null || x.l.out != null; });   // 1.102.0 review: no % read is not 0%
      var L = { status: status, 'in': Math.round(pin), out: Math.round(pout), why: why, known: known };
      return { key: k, l: L, a: a, b: b, pts: pts, ll: ll, bw: bw, ls: ls.map(function (x) { return x.l; }), w: Math.max(1.6, Math.min(6.5, 1.6 + 1.25 * Math.log(Math.max(bw, 1e8) / 1e8) / Math.LN10)), scr: [],
        oIn: o ? o.oIn : Math.random() * 40, oOut: o ? o.oOut : Math.random() * 40, flash: o && o.l.status !== status && (status === 'Down' || o.l.status === 'Down') ? performance.now() : o ? o.flash : 0,
        tip: a.name + ' ↔ ' + b.name + (bw ? ' · ' + speedText(bw) : '') + (ls.length > 1 ? ' (' + ls.length + ' links)' : '') + ' · ' + (down ? 'no traffic' : unk || !known ? 'traffic not known' : L['in'] + '% in / ' + L.out + '% out') + ' · ' + status + (why ? ' (' + why + ')' : '') };
    });
    if (tray) {                                       // the sites without coordinates: buttons that open them in the Sites view
      var un = (D.unplaced || []).filter(function (s) { return D.sites.indexOf(s) >= 0; });
      var kids = un.map(function (s) {
        var ns = D.nodes.filter(function (n) { return n.site === s && !n.gone; }), dn = ns.filter(function (n) { return n.status === 'Down'; }).length;
        var b = document.createElement('button'); b.type = 'button'; b.className = 'nm-b ng-un' + (dn ? ' dn' : ''); b.setAttribute('data-site', s);
        b.textContent = s + ' · ' + ns.length + (dn ? ' · × ' + dn + ' down' : ''); return b;
      });
      if (kids.length) { var h = document.createElement('span'); h.className = 'ng-lt'; h.textContent = 'Unplaced'; kids.unshift(h);
        var i = document.createElement('span'); i.className = 'ng-ui'; i.textContent = 'no coordinates: netmap_sites'; kids.push(i); }
      tray.replaceChildren.apply(tray, kids); tray.hidden = !un.length;
    }
    if (changes && changes.length) start(); else redraw();
    if (window.RG) window.RG.update();                // 1.102.0: the Region view draws the same sites and links
  }
  var land = window.NM_LAND.map(function (r) { var o = [], lo = 0; for (var i = 0; i < r.length; i += 2) { o.push(vec(r[i + 1], r[i])); lo += r[i]; } return { v: o, ll: r, mid: lo / (r.length / 2) }; });
  var grat = [];
  for (var lo = -180; lo < 180; lo += 30) { var m = []; for (var la = -90; la <= 90; la += 3) m.push(vec(la, lo)); grat.push(m); }
  for (la = -60; la <= 60; la += 30) { var p = []; for (lo = -180; lo <= 180; lo += 3) p.push(vec(la, lo)); grat.push(p); }

  /* ---- colours: the page's tokens, read again when the theme changes ------------------------------------------------- */
  var K = {}, glow = {};
  function tokens() {
    var cs = getComputedStyle(box);
    ['ng-space', 'ng-ocean', 'ng-land', 'ng-coast', 'ng-grat', 'ng-b0', 'ng-b1', 'ng-b2', 'ng-b3', 'nm-up', 'nm-warn', 'nm-down', 'nm-unk', 'ink', 'card', 'muted'].forEach(function (k) {
      K[k] = cs.getPropertyValue('--' + k).trim() || '#888';
    });
    K.st = { Up: K['nm-up'], Warning: K['nm-warn'], Down: K['nm-down'], Unknown: K['nm-unk'] };
    glow = {};                                         // one glow per status, drawn once (no shadowBlur each frame)
    Object.keys(K.st).forEach(function (st) {
      var c = document.createElement('canvas'), s = 64; c.width = c.height = s;
      var g = c.getContext('2d'), gr = g.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
      gr.addColorStop(0, alpha(K.st[st], .55)); gr.addColorStop(.35, alpha(K.st[st], .22)); gr.addColorStop(1, alpha(K.st[st], 0));
      g.fillStyle = gr; g.fillRect(0, 0, s, s); glow[st] = c;
    });
    redraw();
  }
  function alpha(c, a) {
    var m = /^#([0-9a-f]{6})$/i.exec(c);
    if (m) { var n = parseInt(m[1], 16); return 'rgba(' + (n >> 16) + ',' + (n >> 8 & 255) + ',' + (n & 255) + ',' + a + ')'; }
    m = /^rgba?\(([^)]+)\)$/.exec(c); if (m) { var q = m[1].split(','); return 'rgba(' + q[0] + ',' + q[1] + ',' + q[2] + ',' + a + ')'; }
    return c;
  }
  function band(pc) { return K['ng-b' + (pc < 25 ? 0 : pc < 50 ? 1 : pc < 75 ? 2 : 3)]; }

  /* ---- projection: the globe's (lam, phi, k) and the flat map's (lam, 0, its k) ---------------------------------------- */
  var cl, sl, cp, sp, R, cx, cy, S, fphi = 0;
  function setup(lam, phi, k, flat) {
    cl = Math.cos(lam * RAD); sl = Math.sin(lam * RAD); cp = Math.cos(phi * RAD); sp = Math.sin(phi * RAD);
    cx = G.W / 2; cy = G.H / 2 - 14; R = Math.min(G.W, G.H - 60) * .44 * k; S = G.W / 360 * k; fphi = flat ? phi : 0;
  }
  // a point on (or above) the sphere -> [x, y, depth]: depth > 0 faces us
  function proj(v) {
    var x1 = v[0] * cl + v[1] * sl, y1 = -v[0] * sl + v[1] * cl, z1 = v[2];
    return [cx + R * y1, cy - R * (-x1 * sp + z1 * cp), x1 * cp + z1 * sp];
  }
  function rim(v) {                                   // behind the globe: on its rim, where the unrolling starts from
    var p = proj(v); if (p[2] > 0) return p;
    var dx = p[0] - cx, dy = p[1] - cy, L = Math.hypot(dx, dy) || 1; return [cx + dx / L * R, cy + dy / L * R, p[2]];
  }
  function flatXY(lon, lat, off, lam) { var x = lon - lam + off; return [cx + x * S, cy - (lat - fphi * .35) * S]; }
  function wrap(x) { return ((x + 540) % 360) - 180; }
  function flatK() { return Math.max(1, (G.H - 70) / (G.W / 2)); }

  // a land ring on the globe: the part behind runs along the rim, so the fill stays right
  function ring(vs) {
    var n = vs.length, P = vs.map(proj), start = -1;
    for (var i = 0; i < n; i++) if (P[i][2] > 0) { start = i; break; }
    if (start < 0) return;
    ctx.moveTo(P[start][0], P[start][1]);
    var hid = false, acc = 0, lastA = 0, a0 = 0;
    for (var j = 1; j <= n; j++) {
      var a = P[(start + j - 1) % n], b = P[(start + j) % n];
      if (a[2] > 0 && b[2] > 0) { ctx.lineTo(b[0], b[1]); continue; }
      var t = a[2] / (a[2] - b[2]), x = a[0] + (b[0] - a[0]) * t - cx, y = a[1] + (b[1] - a[1]) * t - cy, ang = Math.atan2(y, x);
      if (a[2] > 0 && b[2] <= 0) { ctx.lineTo(cx + R * Math.cos(ang), cy + R * Math.sin(ang)); hid = true; acc = 0; lastA = ang; a0 = ang; }
      if (a[2] <= 0 && b[2] <= 0) { var ab = Math.atan2(b[1] - cy, b[0] - cx); acc += norm(ab - lastA); lastA = ab; }
      if (a[2] <= 0 && b[2] > 0) {
        acc += norm(ang - lastA);
        if (hid) ctx.arc(cx, cy, R, a0, a0 + acc, acc < 0);
        hid = false; ctx.lineTo(b[0], b[1]);
      }
    }
  }
  function norm(d) { while (d > Math.PI) d -= 2 * Math.PI; while (d < -Math.PI) d += 2 * Math.PI; return d; }
  // a line (graticule, arc) on the globe: only the parts in front; arcs above the surface also show beside the rim
  function runs(vs, lifted) {
    var out = [], cur = null;
    vs.forEach(function (v) {
      var p = proj(v), dx = p[0] - cx, dy = p[1] - cy, vis = p[2] > 0 || (lifted && dx * dx + dy * dy > R * R);
      if (vis) { if (!cur) out.push(cur = []); cur.push(p); } else cur = null;
    });
    return out;
  }

  /* ---- paint -------------------------------------------------------------------------------------------------------- */
  var last = 0, frames = [], drawMs = [];
  function paint(now) {
    var t0 = performance.now(); now = now || t0;
    if (!G.W || !K.st || !D || G.region) return;      // hidden, not measured yet, or the Region view (netregion.js) shows
    var dt = last ? Math.min(64, now - last) : 16; last = now;
    if (G.fly) flyStep(now);
    else if (G.morph) morphStep(now);
    else if (G.spin && !still() && !G.flat && !G.hover && !drag && now > G.idle) G.lam += dt * .004;       // 4 degrees a second
    var c = ctx; c.setTransform(G.dpr, 0, 0, G.dpr, 0, 0);
    c.fillStyle = K['ng-space']; c.fillRect(0, 0, G.W, G.H);
    if (G.mix > 0 && G.mix < 1) { paintMorph(now); return; }
    setup(G.lam, G.phi, G.k, G.flat);
    if (G.flat) paintFlat(); else paintGlobe();
    arcs.forEach(function (A) {
      A.scr = [];
      if (G.flat) [-360, 0, 360].forEach(function (off) { A.scr.push(A.ll.map(function (q) { return flatXY(q[0], q[1], off, G.lam); })); });
      else A.scr = runs(A.pts, true).map(function (r) { return r.filter(function (q) { return q[2] > -0.02; }); });
    });
    arcs.forEach(function (A) { if (A !== G.hover) arc(A, dt, false, now); });
    if (G.hover && G.hover.l) arc(G.hover, dt, true, now);
    sites.forEach(function (s) { s.p = null;
      if (G.flat) { var x = wrap(s.lon - G.lam); s.p = flatXY(G.lam + x, s.lat, 0, G.lam); } else { var q = proj(s.v); if (q[2] > 0) s.p = q; } });
    sites.forEach(function (s) { if (s.p) site(s, now, s.p); });
    var RK = { Down: 0, Warning: 1 };              // 1.95.0: the labels that matter first (the one pointed at, down, warning, the biggest); one with no free place is left out
    labels(sites.filter(function (s) { return s.p; }).sort(function (a, b) { return ((b === G.hover) - (a === G.hover)) || ((RK[a.worst] != null ? RK[a.worst] : 2) - (RK[b.worst] != null ? RK[b.worst] : 2)) || (b.n - a.n); }));
    var ms = performance.now() - t0; drawMs.push(ms); frames.push(now);
    if (drawMs.length > 240) { drawMs.shift(); frames.shift(); }
    if (perf && (frames.length % 30 === 0 || still())) {
      var gaps = frames.slice(1).map(function (f, i) { return f - frames[i]; }).sort(function (a, b) { return a - b; }), md = drawMs.slice().sort(function (a, b) { return a - b; });
      perf.textContent = (DEBUG ? 'script ' + md[md.length >> 1].toFixed(1) + ' ms a frame' + (gaps.length > 10 && !still() ? ' · ' + Math.round(1000 / gaps[gaps.length >> 1]) + ' frames a second' : '') + ' · ' : '') +
        sites.length + ' sites · ' + arcs.length + ' WAN links';     // the timing only with ?debug=1
    }
  }
  function paintGlobe() {
    var c = ctx;
    var at = c.createRadialGradient(cx, cy, R * .96, cx, cy, R * 1.12);            // the air: a faint halo round the rim
    at.addColorStop(0, alpha(K['ng-b1'], .22)); at.addColorStop(1, alpha(K['ng-b1'], 0));
    c.fillStyle = at; c.beginPath(); c.arc(cx, cy, R * 1.12, 0, 7); c.fill();
    c.fillStyle = K['ng-ocean']; c.beginPath(); c.arc(cx, cy, R, 0, 7); c.fill();
    c.strokeStyle = K['ng-grat']; c.lineWidth = 1; c.beginPath();
    grat.forEach(function (g) { runs(g, false).forEach(function (r) { c.moveTo(r[0][0], r[0][1]); for (var i = 1; i < r.length; i++) c.lineTo(r[i][0], r[i][1]); }); });
    c.stroke();
    c.fillStyle = K['ng-land']; c.strokeStyle = K['ng-coast']; c.lineWidth = .8; c.lineJoin = 'round';
    c.beginPath(); land.forEach(function (L) { ring(L.v); c.closePath(); }); c.fill(); c.stroke();
    var sh = c.createRadialGradient(cx - R * .35, cy - R * .4, R * .1, cx, cy, R);  // a little light from the top left
    sh.addColorStop(0, 'rgba(255,255,255,.10)'); sh.addColorStop(.7, 'rgba(0,0,0,0)'); sh.addColorStop(1, 'rgba(0,0,0,.10)');
    c.fillStyle = sh; c.beginPath(); c.arc(cx, cy, R, 0, 7); c.fill();
    c.strokeStyle = K['ng-coast']; c.lineWidth = 1; c.beginPath(); c.arc(cx, cy, R, 0, 7); c.stroke();
  }
  function paintFlat() {
    var c = ctx, top = flatXY(0, 90, 0, G.lam)[1], bot = flatXY(0, -90, 0, G.lam)[1];
    c.fillStyle = K['ng-ocean']; c.fillRect(0, top, G.W, bot - top);
    c.strokeStyle = K['ng-grat']; c.lineWidth = 1; c.beginPath();
    for (var lo = -540; lo <= 540; lo += 30) { var x = flatXY(lo, 0, 0, G.lam)[0]; c.moveTo(x, top); c.lineTo(x, bot); }
    for (var la = -60; la <= 60; la += 30) { var y = flatXY(0, la, 0, G.lam)[1]; c.moveTo(0, y); c.lineTo(G.W, y); }
    c.stroke();
    c.fillStyle = K['ng-land']; c.strokeStyle = K['ng-coast']; c.lineWidth = .8; c.lineJoin = 'round'; c.beginPath();
    [-360, 0, 360].forEach(function (off) {
      land.forEach(function (L) { var r = L.ll; for (var i = 0; i < r.length; i += 2) { var q = flatXY(r[i], r[i + 1], off, G.lam); if (i) c.lineTo(q[0], q[1]); else c.moveTo(q[0], q[1]); } c.closePath(); });
    });
    c.fill(); c.stroke();
  }
  // the switch, half way: every point moves from where the globe shows it (its back half from the rim) to its place on the
  // flat map, the round sea opens into the rectangle - drawn from the two projections, mixed by G.mix
  function paintMorph(now) {
    var e = G.mix, f = 1 - e, c = ctx, gk = G.morph.gk, fk = G.morph.fk, gphi = G.morph.gphi;
    function off(mid) { return Math.round((G.lam - mid) / 360) * 360; }
    function both(get) { setup(G.lam, gphi, gk, false); var a = get(true); setup(G.lam, G.morph.fphi, fk, true); var b = get(false); return a.map(function (q, i) { return [q[0] * f + b[i][0] * e, q[1] * f + b[i][1] * e]; }); }
    var sea = both(function (g) {
      var out = [];
      for (var i = 0; i < 96; i++) {
        var t = i / 96 * 2 * Math.PI, dx = Math.cos(t), dy = Math.sin(t);
        if (g) out.push([cx + dx * R, cy + dy * R]);
        else { var hw = 180 * S, top = flatXY(0, 90, 0, G.lam)[1], bot = flatXY(0, -90, 0, G.lam)[1], hh = (bot - top) / 2, my = (top + bot) / 2, s = Math.min(hw / Math.abs(dx || 1e-9), hh / Math.abs(dy || 1e-9)); out.push([cx + dx * s, my + dy * s]); }
      }
      return out;
    });
    c.fillStyle = K['ng-ocean']; c.beginPath(); sea.forEach(function (q, i) { if (i) c.lineTo(q[0], q[1]); else c.moveTo(q[0], q[1]); }); c.closePath(); c.fill();
    c.fillStyle = K['ng-land']; c.strokeStyle = K['ng-coast']; c.lineWidth = .8; c.lineJoin = 'round'; c.beginPath();
    land.forEach(function (L) {
      var o = off(L.mid), pts = both(function (g) { return g ? L.v.map(rim) : L.v.map(function (v, i) { return flatXY(L.ll[2 * i] + o, L.ll[2 * i + 1], 0, G.lam); }); });
      pts.forEach(function (q, i) { if (i) c.lineTo(q[0], q[1]); else c.moveTo(q[0], q[1]); }); c.closePath();
    });
    c.fill(); c.stroke();
    arcs.forEach(function (A) {
      var o = off(A.ll[32][0]), pts = both(function (g) { return g ? A.pts.map(rim) : A.ll.map(function (q) { return flatXY(q[0] + o, q[1], 0, G.lam); }); });
      c.setLineDash([]); c.lineCap = 'round'; c.lineWidth = A.w; c.strokeStyle = A.l.status === 'Down' ? K['nm-down'] : A.l.status === 'Warning' ? K['nm-warn'] : band(Math.max(A.l['in'], A.l.out));
      c.beginPath(); pts.forEach(function (q, i) { if (i) c.lineTo(q[0], q[1]); else c.moveTo(q[0], q[1]); }); c.stroke();
    });
    sites.forEach(function (s) { var o = off(s.lon), q = both(function (g) { return [g ? rim(s.v) : flatXY(s.lon + o, s.lat, 0, G.lam)]; })[0]; s.p = null; site(s, now, q); });
  }
  function poly(r, side, d) {                          // a lane: the line moved side * d pixels across its direction
    var c = ctx; c.beginPath();
    for (var i = 0; i < r.length; i++) {
      var a = r[Math.max(0, i - 1)], b = r[Math.min(r.length - 1, i + 1)], dx = b[0] - a[0], dy = b[1] - a[1], L = Math.hypot(dx, dy) || 1;
      var x = r[i][0] - dy / L * side * d, y = r[i][1] + dx / L * side * d;
      if (i) c.lineTo(x, y); else c.moveTo(x, y);
    }
    c.stroke();
  }
  function arc(A, dt, hot, now) {
    var c = ctx, l = A.l, w = A.w * (hot ? 1.5 : 1), down = l.status === 'Down', warn = l.status === 'Warning', unk = l.status === 'Unknown';
    var fl = A.flash && now - A.flash < 4000 && !still();          // it just went down (or came back): a ring of light runs along it
    c.lineCap = 'round'; c.lineJoin = 'round';
    if (!still() && !down && !unk) { A.oIn += dt * (.006 + .05 * l['in'] / 100) * 6; A.oOut += dt * (.006 + .05 * l.out / 100) * 6; }
    A.scr.forEach(function (r) {
      if (r.length < 2) return;
      c.setLineDash([]); c.strokeStyle = K['ng-ocean']; c.lineWidth = w + 3; c.globalAlpha = .9; poly(r, 0, 0);     // a surface casing: the arc reads on land and sea
      c.globalAlpha = 1;
      if (fl) { c.strokeStyle = alpha(down ? K['nm-down'] : K['nm-up'], .25 + .25 * Math.sin((now - A.flash) / 160)); c.lineWidth = w + 8; poly(r, 0, 0); }
      if (unk) { c.strokeStyle = alpha(K['nm-unk'], .7); c.lineWidth = Math.max(1.6, w * .6); c.setLineDash([2, 5]); c.lineDashOffset = 0; poly(r, 0, 0); c.setLineDash([]); return; }
      if (down) { c.strokeStyle = K['nm-down']; c.lineWidth = Math.max(2, w * .8); c.setLineDash([6, 6]); c.lineDashOffset = 0; poly(r, 0, 0); c.setLineDash([]); return; }
      c.strokeStyle = warn ? alpha(K['nm-warn'], .35) : alpha(band(Math.max(l['in'], l.out)), .30); c.lineWidth = w + 1; poly(r, 0, 0);
      var lw = Math.max(1.4, w / 2), o = Math.max(1, w / 4);
      c.lineWidth = lw; c.setLineDash([lw * 2.2, lw * 3.2]);
      c.strokeStyle = warn ? K['nm-warn'] : band(l['in']); c.lineDashOffset = A.oIn; poly(r, 1, o);            // in: dashes run towards the first site
      c.strokeStyle = warn ? K['nm-warn'] : band(l.out); c.lineDashOffset = -A.oOut; poly(r, -1, o);        // out: towards the second
      c.setLineDash([]);
    });
  }
  function site(s, now, p) {
    var c = ctx, r = s.r * Math.min(1.6, Math.max(1, Math.sqrt(G.k) * .9)), pulse = !still() && s.worst === 'Down' ? (1 + .25 * Math.sin(now / 380)) : 1;
    var gs = r * 4.2 * pulse; c.drawImage(glow[s.worst], p[0] - gs / 2, p[1] - gs / 2, gs, gs);
    if (s.flash && now - s.flash < 4000 && !still()) {                 // it just changed: a ring goes out from it
      var t = ((now - s.flash) % 1300) / 1300; c.beginPath(); c.arc(p[0], p[1], r + 4 + t * 26, 0, 7); c.strokeStyle = alpha(K.st[s.worst], .8 * (1 - t)); c.lineWidth = 3; c.stroke();
    }
    c.beginPath(); c.arc(p[0], p[1], r + 2, 0, 7); c.fillStyle = K['ng-ocean']; c.fill();
    c.beginPath(); c.arc(p[0], p[1], r, 0, 7); c.fillStyle = K.st[s.worst]; c.fill();
    if (G.hover === s) { c.beginPath(); c.arc(p[0], p[1], r + 5, 0, 7); c.strokeStyle = K.ink; c.lineWidth = 2; c.stroke(); }
    c.fillStyle = s.worst === 'Warning' ? '#1b1b1b' : '#fff'; c.font = '800 ' + Math.round(r * 1.1) + 'px system-ui,sans-serif'; c.textAlign = 'center'; c.textBaseline = 'middle';
    c.fillText(GL[s.worst], p[0], p[1] + 1);
    s.rr = r;
  }
  function labels(list) {                              // right of the dot, else left, below, above: the first place that is free
    var c = ctx, taken = list.map(function (s) { return [s.p[0] - s.rr - 2, s.p[1] - s.rr - 2, s.p[0] + s.rr + 2, s.p[1] + s.rr + 2]; });
    var groups = [];                                     // sites on (nearly) one spot: one label, the first (the worst) and "+N", and a count
    list.forEach(function (s) {
      var g = groups.filter(function (g) { return Math.abs(g[0].p[0] - s.p[0]) < 10 && Math.abs(g[0].p[1] - s.p[1]) < 10; })[0];
      if (g) g.push(s); else groups.push([s]);
    });
    c.font = '700 12.5px system-ui,sans-serif'; c.textBaseline = 'middle'; c.lineWidth = 3.5; c.lineJoin = 'round';
    groups.forEach(function (g) {
      var s = g[0], name = s.name + (g.length > 1 ? ' +' + (g.length - 1) : '');
      if (g.length > 1) {                                // the count, on the dot's upper right
        var bx = s.p[0] + s.rr * .8, by = s.p[1] - s.rr * .8, t = String(g.length);
        c.font = '800 10px system-ui,sans-serif'; var bw = Math.max(15, c.measureText(t).width + 8);
        c.beginPath(); c.arc(bx, by, bw / 2, 0, 7); c.fillStyle = K.ink; c.fill(); c.lineWidth = 2; c.strokeStyle = K['ng-ocean']; c.stroke();
        c.fillStyle = K['ng-ocean']; c.textAlign = 'center'; c.fillText(t, bx, by + .5);
        taken.push([bx - bw / 2, by - bw / 2, bx + bw / 2, by + bw / 2]);
        c.font = '700 12.5px system-ui,sans-serif'; c.lineWidth = 3.5;
      }
      var w = c.measureText(name).width, p = s.p, r = s.rr + (g.length > 1 ? 6 : 0), h = 15, at = [[p[0] + r + 6, p[1]], [p[0] - r - 6 - w, p[1]], [p[0] - w / 2, p[1] + r + 12], [p[0] - w / 2, p[1] - r - 12]];
      var pick = at.filter(function (q) { var b = [q[0] - 2, q[1] - h / 2, q[0] + w + 2, q[1] + h / 2];
        return !taken.some(function (t) { return b[0] < t[2] && b[2] > t[0] && b[1] < t[3] && b[3] > t[1]; }); })[0];
      if (!pick) return;                                 // crowded: no label (the dot stays; pointing at it, or zooming in, names it)
      taken.push([pick[0] - 2, pick[1] - h / 2, pick[0] + w + 2, pick[1] + h / 2]);
      c.textAlign = 'left'; c.strokeStyle = K['ng-ocean']; c.strokeText(name, pick[0], pick[1]); c.fillStyle = K.ink; c.fillText(name, pick[0], pick[1]);
    });
  }

  /* ---- the loop: each display frame while it moves; on demand when motion is off --------------------------------------- */
  var running = false;
  function loop(t) { if (!running) return; paint(t); requestAnimationFrame(loop); }
  function start() { if (running || G.region) return; if (still()) { redraw(); return; } running = true; requestAnimationFrame(loop); }
  function stop() { running = false; }
  function redraw() { if (!running) requestAnimationFrame(function (t) { paint(t); }); }
  function resize() {
    var r = cv.getBoundingClientRect(); if (!r.width) return;
    G.dpr = Math.min(2, window.devicePixelRatio || 1); G.W = r.width; G.H = r.height;
    cv.width = Math.round(r.width * G.dpr); cv.height = Math.round(r.height * G.dpr);
    if (G.flat && !G.morph && G.fitPending && sites.length) fitFlat();
    redraw();
  }
  if ('ResizeObserver' in window) new ResizeObserver(resize).observe(cv); else addEventListener('resize', resize);
  document.addEventListener('visibilitychange', function () { if (document.hidden) stop(); else if (box.getAttribute('data-view') === 'world') start(); });

  /* ---- hover, drag, wheel, keys --------------------------------------------------------------------------------------- */
  function hit(x, y) {
    for (var i = sites.length - 1; i >= 0; i--) { var s = sites[i]; if (s.p && Math.hypot(s.p[0] - x, s.p[1] - y) < (s.rr || s.r) + 6) return s; }
    var best = null, bd = Infinity;          // the nearest arc within reach (its width, at least 9 px)
    arcs.forEach(function (A) {
      A.scr.forEach(function (r) {
        for (var i = 1; i < r.length; i++) { var d = seg(x, y, r[i - 1], r[i]); if (d < Math.max(9, A.w) && d < bd) { bd = d; best = A; } }
      });
    });
    return best;
  }
  function seg(x, y, a, b) {
    var dx = b[0] - a[0], dy = b[1] - a[1], L = dx * dx + dy * dy, t = L ? Math.max(0, Math.min(1, ((x - a[0]) * dx + (y - a[1]) * dy) / L)) : 0;
    return Math.hypot(x - a[0] - t * dx, y - a[1] - t * dy);
  }
  function tipFor(h) {
    if (!h) return '';
    if (h.l) return h.tip;
    var near = h.p ? sites.filter(function (o) { return o !== h && o.p && Math.abs(o.p[0] - h.p[0]) < 10 && Math.abs(o.p[1] - h.p[1]) < 10; }).map(function (o) { return o.name; }) : [];
    return h.name + ' · ' + GL[h.worst] + ' ' + h.worst + ' · ' + h.n + ' devices · ' + h.c.Down + ' down, ' + h.c.Warning + ' warning · click to open' +
      (near.length ? ' · also here: ' + near.slice(0, 6).join(', ') + (near.length > 6 ? ' +' + (near.length - 6) : '') + ' (zoom in to part them)' : '');
  }
  var drag = null;
  cv.addEventListener('pointerdown', function (e) { drag = { x: e.clientX, y: e.clientY, lam: G.lam, phi: G.phi, moved: false }; cv.setPointerCapture(e.pointerId); });
  cv.addEventListener('pointermove', function (e) {
    var r = cv.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
    if (drag) {
      var dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (!drag.moved && Math.abs(dx) + Math.abs(dy) < 4) return;
      if (G.morph) return;
      drag.moved = true; cv.classList.add('drag'); tip.hidden = true; G.hover = null; G.fly = null;
      setup(G.lam, G.phi, G.k, G.flat);
      var f = G.flat ? 1 / S : 1 / (R * RAD);            // pixels -> degrees
      G.lam = drag.lam - dx * f; G.phi = Math.max(G.flat ? -120 : -80, Math.min(G.flat ? 120 : 80, drag.phi + dy * f * (G.flat ? 2.8 : 1)));
      G.idle = performance.now() + 6000; redraw(); return;
    }
    var h = G.morph ? null : hit(x, y);
    if (h !== G.hover) { G.hover = h; cv.classList.toggle('hot', !!h); redraw(); }
    if (!h) { tip.hidden = true; return; }
    tip.textContent = tipFor(h); tip.hidden = false; tip.style.left = x + 'px'; tip.style.top = y + 'px';
  });
  cv.addEventListener('pointerleave', function () { tip.hidden = true; if (G.hover) { G.hover = null; redraw(); } });
  cv.addEventListener('pointerup', function (e) {
    var d = drag; drag = null; cv.classList.remove('drag');
    if (!d || d.moved) return;
    var r = cv.getBoundingClientRect(), h = hit(e.clientX - r.left, e.clientY - r.top);
    if (h && !h.l) fly(h.name);
  });
  function zoom(f) { G.k = Math.max(G.flat ? flatK() * .7 : .7, Math.min(8, G.k * f)); G.idle = performance.now() + 6000; redraw(); }
  cv.addEventListener('wheel', function (e) { e.preventDefault(); zoom(e.deltaY < 0 ? 1.15 : 1 / 1.15); }, { passive: false });
  cv.addEventListener('keydown', function (e) {
    var k = e.key, step = 10 / G.k;
    if (k === 'ArrowLeft') G.lam -= step; else if (k === 'ArrowRight') G.lam += step; else if (k === 'ArrowUp') G.phi = Math.min(80, G.phi + step);
    else if (k === 'ArrowDown') G.phi = Math.max(-80, G.phi - step); else if (k === '+' || k === '=') zoom(1.25); else if (k === '-') zoom(1 / 1.25); else return;
    e.preventDefault(); G.idle = performance.now() + 6000; redraw();
  });
  function inWorld() { return box.getAttribute('data-view') === 'world'; }
  function rz(f) { if (G.region && window.RG) { window.RG.zoom(f); return true; } return false; }   // 1.102.0: the Region view's own
  box.querySelector('.nm-zi').addEventListener('click', function () { if (inWorld() && !rz(1.3)) zoom(1.3); });
  box.querySelector('.nm-zo').addEventListener('click', function () { if (inWorld() && !rz(1 / 1.3)) zoom(1 / 1.3); });
  box.querySelector('.nm-fit').addEventListener('click', function () {
    if (!inWorld() || rz(0)) return;
    G.fly = null;
    if (G.flat) fitFlat(); else { G.lam = HOME.lam; G.phi = HOME.phi; G.k = HOME.k; }
    redraw();
  });
  // the flat map framed on every placed site, with a margin: centred across the widest empty stretch of longitude, so a
  // group of sites on both sides of the date line stays together
  function flatFrame() {
    var W = G.W || 1000, H = G.H || 600, mx = 110, top = 70, bot = 84, n = sites.length;   // room for a dot and its name
    if (!n) return { lam: HOME.lam, phi: 0, k: flatK() };
    var a = sites.map(function (s) { return ((s.lon % 360) + 360) % 360; }).sort(function (x, y) { return x - y; }), gap = -1, at = 0;
    for (var i = 0; i < n; i++) { var g = (i + 1 < n ? a[i + 1] : a[0] + 360) - a[i]; if (g > gap) { gap = g; at = i; } }
    var span = n > 1 ? 360 - gap : 0, lam = wrap(a[(at + 1) % n] + span / 2);
    var la = sites.map(function (s) { return s.lat; }), lo = Math.min.apply(0, la), hi = Math.max.apply(0, la);
    var kx = (W - 2 * mx) / (Math.max(span, 40) * W / 360), ky = (H - top - bot) / (Math.max(hi - lo, 25) * W / 360);
    var k = Math.max(.8, Math.min(kx, ky, 6)), S = W / 360 * k, cy = H / 2 - 14, yc = (top + H - bot) / 2;
    return { lam: lam, phi: ((lo + hi) / 2 - (cy - yc) / S) / .35, k: k };
  }
  function fitFlat() { var f = flatFrame(); G.lam = f.lam; G.phi = f.phi; G.k = f.k; G.fitPending = !sites.length; }   // no sites yet: framed when they come
  if (tray) tray.addEventListener('click', function (e) { var b = e.target.closest('[data-site]'); if (b) open(b.getAttribute('data-site')); });

  /* ---- Region / Globe / Flat: the viewer's choice kept (this browser), else the site's netmap_view; the switch unrolls --- */
  var pb = [].slice.call(box.querySelectorAll('.ng-p'));
  function mode() { return G.region ? 'region' : G.flat ? 'flat' : 'globe'; }
  function mark() { pb.forEach(function (b) { b.setAttribute('aria-pressed', String(b.getAttribute('data-p') === mode())); }); box.classList.toggle('ng-rg', G.region); }
  // 1.102.0: Region - the placed sites' region on a flat map drawn by netregion.js (the canvas stops); Globe and Flat as before
  function setMode(m) {
    if (m === 'region') {
      if (G.region || !box.querySelector('.rg-wrap')) return;
      tip.hidden = true; G.hover = null; G.fly = null; G.morph = null; G.region = true; stop(); mark();
      try { localStorage.setItem(KEY, 'region'); } catch (e) { /* this page only */ }
      if (window.RG) window.RG.update();
      return;
    }
    if (G.region) {                                    // from the Region view: straight to the globe or the flat map
      G.region = false; G.flat = m === 'flat'; G.mix = G.flat ? 1 : 0;
      if (G.flat) { if (G.W) fitFlat(); else G.fitPending = true; } else { G.phi = HOME.phi; G.k = HOME.k; }
      mark();
      try { localStorage.setItem(KEY, m); } catch (e) { /* this page only */ }
      resize(); if (inWorld()) start();
      return;
    }
    setFlat(m === 'flat', true);
  }
  function setFlat(flat, animate) {
    if (flat === G.flat && !G.morph) return;
    tip.hidden = true; G.hover = null; G.fly = null;
    var gk = G.flat ? HOME.k : G.k, gphi = G.flat ? HOME.phi : G.phi;
    G.flat = flat; mark();
    try { localStorage.setItem(KEY, flat ? 'flat' : 'globe'); } catch (e) { /* no storage: this page only */ }
    if (!animate || still() || !G.W) {
      G.mix = flat ? 1 : 0; G.morph = null;
      if (flat) { if (G.W) fitFlat(); else G.fitPending = true; } else { G.phi = gphi; G.k = Math.min(gk, 2.5); }
      redraw(); return;
    }
    var f = flatFrame();
    G.morph = { t0: performance.now(), d: 950, from: G.mix, to: flat ? 1 : 0, gk: Math.min(gk, 2.5), gphi: gphi, fk: f.k, fphi: f.phi,
                lam0: G.lam, lam1: flat ? G.lam + norm((f.lam - G.lam) * RAD) / RAD : G.lam };
    start();
  }
  function morphStep(now) {
    var M = G.morph, t = Math.min(1, (now - M.t0) / M.d), e = t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
    G.mix = M.from + (M.to - M.from) * e;
    G.lam = M.lam0 + (M.lam1 - M.lam0) * e;                        // to the flat map: it turns to the sites' middle as it unrolls
    if (t >= 1) { G.morph = null; G.mix = M.to; G.phi = G.flat ? M.fphi : M.gphi; G.k = G.flat ? M.fk : M.gk; if (G.flat && !sites.length) G.fitPending = true; }
  }
  pb.forEach(function (b) { b.addEventListener('click', function () { setMode(b.getAttribute('data-p')); }); });

  /* ---- fly in to a site, then open it in the Sites view ----------------------------------------------------------------- */
  function fly(name) {
    var s = bySite[name]; if (!s) return false;
    if (G.region) { open(name); return true; }        // the Region view: no flight, the site opens
    tip.hidden = true; G.hover = null; G.morph = null; G.mix = G.flat ? 1 : 0;
    var to = { lam: s.lon, phi: G.flat ? s.lat / .35 : s.lat, k: G.flat ? flatK() * 3 : 3.4 };
    if (still()) { G.lam = to.lam; G.phi = to.phi; G.k = to.k; paint(); open(name); return true; }
    G.fly = { t0: performance.now(), d: 1100, from: { lam: G.lam, phi: G.phi, k: G.k }, to: to, name: name };
    G.fly.to.lam = G.lam + norm((to.lam - G.lam) * RAD) / RAD;          // the short way round
    start();
    return true;
  }
  function flyStep(now) {
    var F = G.fly, t = Math.min(1, (now - F.t0) / F.d), e = t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
    G.lam = F.from.lam + (F.to.lam - F.from.lam) * e; G.phi = F.from.phi + (F.to.phi - F.from.phi) * e;
    G.k = F.from.k * Math.pow(F.to.k / F.from.k, e);
    if (t >= 1) { G.fly = null; setTimeout(function () { open(F.name); }, 120); }
  }
  function open(name) { if (window.nmOpenSite) window.nmOpenSite(name); else view('site'); }   // 1.95.0: the site's own level (netmap.js)
  window.ngFly = fly;

  /* ---- the world view, or another level (netmap.js) ------------------------------------------------------------------------------------------------- */
  var vb = [].slice.call(box.querySelectorAll('.nm-v'));
  var shown = '';
  function view(v) {
    box.setAttribute('data-view', v);
    vb.forEach(function (b) { b.setAttribute('aria-pressed', String(b.getAttribute('data-v') === v)); });
    if (v === 'world' && shown !== 'world') { if (G.flat) G.fitPending = true; else G.k = Math.min(G.k, 1.2); G.idle = performance.now() + 1500; resize(); start(); if (window.RG) window.RG.update(); }
    else if (v !== 'world') { stop(); tip.hidden = true; }
    shown = v;                                           // 1.95.1: a live refresh (the same view again) keeps where the viewer turned it
  }
  window.nmView = function (v) { view(v); };            // 1.95.0: world (this globe), tiles, site, building, device (netmap.js draws those)
  vb.forEach(function (b) {
    b.addEventListener('click', function () {
      var v = b.getAttribute('data-v'); if (v === box.getAttribute('data-view')) return;
      window.nmView(v);
    });
  });
  var mo = window.matchMedia && matchMedia('(prefers-color-scheme: dark)');
  if (mo && mo.addEventListener) mo.addEventListener('change', tokens);
  new MutationObserver(tokens).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  document.addEventListener('change', function (e) { if (e.target && e.target.name === 'theme') setTimeout(tokens, 0); });

  // 1.102.0: what the Region view (netregion.js) draws - the same sites and WAN links as the globe, one state rule
  window.NG = { sites: function () { return sites; }, arcs: function () { return arcs; }, mode: mode, setMode: setMode, wanState: wanState, speedText: speedText, data: function () { return D; } };
  window.ngTest = { G: G, sites: function () { return sites; }, arcs: function () { return arcs; }, paint: paint, hit: hit, fly: fly, start: start, stop: stop, drawMs: drawMs, frames: frames, setFlat: setFlat, setMode: setMode, wanState: wanState,
    point: function (name) { var s = bySite[name]; return s && s.p ? [s.p[0], s.p[1]] : null; },
    arcAt: function (i, f) { var A = arcs[i]; if (!A) return null; var r = A.scr.filter(function (x) { var q = x[Math.floor(x.length * (f || .5))]; return x.length > 4 && q[0] > 20 && q[0] < G.W - 20 && q[1] > 20 && q[1] < G.H - 60; })[0]; return r ? r[Math.floor(r.length * (f || .5))] : null; } };
  var first = true;
  window.NM.on(function (data, changes) {
    if (first) {                                      // the projection: this browser's last choice, else the site's (netmap_view)
      first = false; var want = data.view === 'flat' || data.view === 'globe' ? data.view : 'region';   // 1.102.0: Region unless the site says
      try { var kept = localStorage.getItem(KEY); if (kept === 'flat' || kept === 'globe' || kept === 'region') want = kept; } catch (e) { /* the site's */ }
      if (want === 'region' && !box.querySelector('.rg-wrap')) want = 'globe';   // 1.102.0 review: an older page (no Region in it) - the globe
      G.region = want === 'region'; G.flat = want === 'flat'; G.mix = G.flat ? 1 : 0; G.phi = G.flat ? 0 : HOME.phi; G.fitPending = G.flat; mark();
    }
    build(data, changes);
    if (G.flat && G.fitPending && G.W && sites.length) { fitFlat(); redraw(); }
  });
  tokens(); resize(); paint(); start();
})();
