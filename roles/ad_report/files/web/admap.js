/* 1.101.0: the AD report's replication map (roles/ad_report/files/web; plugins/filter/ad_map_filters.py makes its data).
   The AD sites where the network map places their sites (else a ring around the site with the most DCs), the replication
   connections between them - colour: time since the last success (the matrix's scale), width: the WAN traffic between the
   two sites in SolarWinds, dashes moving with it - and a site with several DCs opening in place into its DCs and the
   connections inside it. No inline script or style in the page; motion: report_motion and the system's wish. */
function drawAdMap(box, D, portal, stale) {
  if (!box || !D || !D.sites || !D.sites.length) return;
  var html = document.documentElement;
  function still() {
    if (html.classList.contains('motion-off') || html.getAttribute('data-motion') === 'off' || /[?&]motion=off\b/.test(location.search)) return true;
    if (html.classList.contains('motion-always')) return false;
    return !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
  }
  if (!still()) box.classList.add('am-move');
  var NS = 'http://www.w3.org/2000/svg';
  function sv(t, at, kids) { var e = document.createElementNS(NS, t); for (var k in at || {}) if (at[k] != null) e.setAttribute(k, at[k]); (kids || []).forEach(function (c) { e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); }); return e; }
  function el(t, c, kids) { var e = document.createElement(t); if (c) e.className = c; (kids || []).forEach(function (k) { if (k != null) e.appendChild(typeof k === 'string' ? document.createTextNode(k) : k); }); return e; }
  function own(o, k) { return !!o && Object.prototype.hasOwnProperty.call(o, k); }
  var wrap = box.querySelector('.am-wrap'), tip = box.querySelector('.am-tip'), note = box.querySelector('.am-note'), leg = box.querySelector('.am-leg');
  var sites = D.sites || [], links = D.links || [], inside = D.inside || {}, open = '';
  var byName = Object.create(null); sites.forEach(function (s) { byName[s.n] = s; });
  function ago(h) { return h == null ? 'never' : h < 1 ? Math.round(h * 60) + ' min ago' : h < 48 ? (Math.round(h * 10) / 10) + ' h ago' : Math.round(h / 24) + ' days ago'; }
  function bpsText(b) { if (!b) return ''; var u = [[1e12, 'Tbps'], [1e9, 'Gbps'], [1e6, 'Mbps'], [1e3, 'kbps']]; for (var i = 0; i < u.length; i++) if (b >= u[i][0]) return (Math.round(b / u[i][0] * 10) / 10) + ' ' + u[i][1]; return b + ' bps'; }
  var placedN = sites.filter(function (s) { return s.p; }).length;
  note.textContent = placedN ? '' : (D.placed_by ? 'No AD site has a place on the network map (netmap_sites): the sites in a ring.' :
    'Not placed (no network-map sites read): the sites in a ring around the one with the most DCs.');

  /* 1.101.1: co-located AD sites - the same network-map site, or within 30 px - are ONE location bubble
     "<site> · N AD sites" (the worst colour); it opens in place into its AD sites and the lines between them; an AD site with
     several DCs opens again from there. Lines from elsewhere go to the bubble, one a pair while it is closed. Everything in
     one place: no geography, the ring. Labels: the worst and the bubbles first, the rest only where they fit - every dot names
     itself on hover and focus. */
  var openLoc = '', openSite = '', RANKC = { critical: 0, warning: 1, low: 2, ok: 3 };
  function worstOf(cs) { if (stale) return 'unknown'; return cs.reduce(function (w, c) { return RANKC[c] < RANKC[w] ? c : w; }, 'ok'); }
  function draw() {
    [].slice.call(wrap.querySelectorAll('svg')).forEach(function (x) { x.remove(); });
    tip.hidden = true;
    var W = Math.max(560, Math.round(wrap.clientWidth || 1000)), H = Math.round(Math.max(380, W * 0.55));
    var svg = sv('svg', { 'class': 'am-svg', viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': 'Replication map' });
    wrap.insertBefore(svg, tip);
    var placed = sites.filter(function (s) { return s.p; }), un = sites.filter(function (s) { return !s.p; }), pos = Object.create(null), pj = null;
    if (placed.length) {
      var la = placed.map(function (s) { return s.p[0]; }), lo = placed.map(function (s) { return s.p[1]; });
      var lat0 = Math.min.apply(0, la) - 3, lat1 = Math.max.apply(0, la) + 3, lon0 = Math.min.apply(0, lo) - 4, lon1 = Math.max.apply(0, lo) + 4;
      var kx = Math.cos((lat0 + lat1) / 2 * Math.PI / 180), mapW = W - (un.length ? 180 : 20), sc = Math.min(mapW / Math.max(1e-6, (lon1 - lon0) * kx), (H - 40) / Math.max(1e-6, lat1 - lat0));
      var ox = 10 + (mapW - (lon1 - lon0) * kx * sc) / 2, oy = 20 + (H - 40 - (lat1 - lat0) * sc) / 2;
      pj = function (lat, lon) { return [ox + (lon - lon0) * kx * sc, oy + (lat1 - lat) * sc]; };
      placed.forEach(function (s) { pos[s.n] = pj(s.p[0], s.p[1]); });
    }
    /* the locations: AD sites on one network-map site, or within 30 px */
    var loc = Object.create(null), locs = [];
    placed.forEach(function (s) {
      var hit = locs.filter(function (L) { return L.nm === s.nm || Math.hypot(L.x - pos[s.n][0], L.y - pos[s.n][1]) < 30; })[0];
      if (!hit) { hit = { id: '', nm: s.nm, x: pos[s.n][0], y: pos[s.n][1], m: [] }; locs.push(hit); }
      hit.m.push(s); loc[s.n] = hit;
    });
    var rad = function (L) { return L.m.length > 1 ? 17 : 11; }, merged = true;
    while (merged) {                                     // locations whose discs would touch: one (the east end of a busy map)
      merged = false;
      for (var i = 0; i < locs.length && !merged; i++) for (var j = i + 1; j < locs.length && !merged; j++) {
        var A = locs[i], B = locs[j];
        if (Math.hypot(A.x - B.x, A.y - B.y) < rad(A) + rad(B) + 10) {
          var n = A.m.length + B.m.length, big = A.m.length >= B.m.length ? A : B;
          A.x = (A.x * A.m.length + B.x * B.m.length) / n; A.y = (A.y * A.m.length + B.y * B.m.length) / n;
          A.places = (A.places || [A.nm]).concat(B.places || [B.nm]).filter(function (x, k, a) { return x && a.indexOf(x) === k; }); A.nm = big.nm;
          A.m = A.m.concat(B.m); B.m.forEach(function (s) { loc[s.n] = A; });
          locs.splice(j, 1); merged = true;
        }
      }
    }
    locs.forEach(function (L) { L.id = 'L:' + L.m.map(function (s) { return s.n; }).sort().join('\n'); });   // the same place opens after a resize
    var oneSpot = locs.length === 1 && placed.length > 1;
    if (oneSpot || !placed.length) {                     // everything in one place (or nowhere): no geography - the ring
      var hub = sites.slice().sort(function (a, b) { return b.dcs.length - a.dcs.length; })[0], rest = sites.filter(function (s) { return s !== hub; });
      pos = Object.create(null); pos[hub.n] = [W / 2, H / 2]; loc = Object.create(null); locs = [];
      rest.forEach(function (s, k) { var a = -Math.PI / 2 + k * 2 * Math.PI / Math.max(1, rest.length); pos[s.n] = [W / 2 + W * 0.36 * Math.cos(a), H / 2 + H * 0.38 * Math.sin(a)]; });
      sites.forEach(function (s) { var L = { id: 'S:' + s.n, nm: s.nm, x: pos[s.n][0], y: pos[s.n][1], m: [s] }; locs.push(L); loc[s.n] = L; });
      note.textContent = !oneSpot ? note.textContent : un.length
        ? 'Every AD site with a map position is at ' + (placed[0].nm || 'one place') + ' (' + un.length + ' without one): no map, the sites in a ring.'
        : 'Every AD site is at ' + (placed[0].nm || 'one place') + ': no map, the sites in a ring.';
    } else {
      var gl = sv('g'); svg.appendChild(gl);
      (window.NM_LAND || []).forEach(function (r) { var d = '';
        for (var i = 0; i < r.length; i += 2) { var q = pj(r[i + 1], r[i]); d += (i ? 'L' : 'M') + q[0].toFixed(1) + ',' + q[1].toFixed(1); }
        gl.appendChild(sv('path', { d: d + 'Z', 'class': 'am-land' })); });
      if (un.length) svg.appendChild(sv('text', { x: W - 165, y: 34, 'class': 'am-unpl' }, ['NO MAP POSITION']));
      un.forEach(function (s, k) { var L = { id: 'S:' + s.n, nm: '', x: W - 150, y: 66 + k * 54, m: [s] }; locs.push(L); loc[s.n] = L; pos[s.n] = [L.x, L.y]; });
    }
    var linksAt = {};                                    // each location's links' worst colour
    links.forEach(function (l) { [l.a, l.b].forEach(function (n) { if (loc[n]) (linksAt[loc[n].id] = linksAt[loc[n].id] || []).push(l.cls); }); });
    Object.keys(inside).forEach(function (n) { if (loc[n]) (inside[n] || []).forEach(function (t) { (linksAt[loc[n].id] = linksAt[loc[n].id] || []).push(t.cls); }); });   // DC to DC inside a site counts too
    /* where a link's end is drawn: the AD site when its location is the open one, else its location */
    var O = openLoc && (locs.filter(function (L) { return L.id === openLoc; })[0] || locs.filter(function (L) { return L.m.some(function (s) { return 'L:' + s.n === openLoc.split('\n')[0]; }); })[0]), R = 0, mpos = Object.create(null);
    if (O) openLoc = O.id;
    if (O) {                                             // the opened place stays inside the drawing (its labels too)
      R = Math.min(130, Math.max(60, 26 * O.m.length), W * 0.14);
      O = { id: O.id, nm: O.nm, m: O.m, x: Math.max(R + 110, Math.min(O.x, W - R - 110)), y: Math.max(R + 50, Math.min(O.y, H - R - 36)) };
      locs.forEach(function (L) {
        if (L.id === O.id) { L.open = true; return; }
        var dx = L.x - O.x, dy = L.y - O.y, dd = Math.hypot(dx, dy), ux = dd ? dx / dd : 1, uy = dd ? dy / dd : 0;
        var lim = R + 30 + rad(L) + 8 + (uy < -0.4 ? 30 : 0);          // above it: clear of its "close" label too
        if (dd < lim) { L.x = O.x + ux * lim; L.y = O.y + uy * lim; }    // nudged out, with its lines
      });
      O.m.forEach(function (s, k) { var a = -Math.PI / 2 + k * 2 * Math.PI / O.m.length; mpos[s.n] = [O.x + R * Math.cos(a), O.y + R * Math.sin(a)]; });
    }
    function endOf(n) { var L = loc[n]; if (!L) return null; return O && L.open ? { key: 'S:' + n, p: mpos[n], open: true } : { key: L.id, p: [L.x, L.y] }; }
    var agg = {};
    links.forEach(function (l) {
      var a = endOf(l.a), b = endOf(l.b); if (!a || !b || a.key === b.key) return;
      var inner = a.open && b.open, k = [a.key, b.key].sort().join('|'), g = agg[k] || (agg[k] = { a: a, b: b, ls: [], inner: inner });
      g.ls.push(l);
    });
    var gL = sv('g'), gS = sv('g'), gI = sv('g'); svg.appendChild(gL); svg.appendChild(gS); svg.appendChild(gI);
    var maxB = Math.max.apply(0, links.map(function (l) { return l.wan && l.wan.bps || 0; }).concat([1]));
    function place(ev, cx, cy) {
      tip.hidden = false;
      if (portal) return;  // the portal's tooltip is anchored by its stylesheet
      var r = wrap.getBoundingClientRect(), x = (ev && ev.clientX ? ev.clientX - r.left : cx / W * r.width) + 14, y = (ev && ev.clientY ? ev.clientY - r.top : cy / H * r.height) + 14;
      tip.style.left = Math.max(4, Math.min(x, r.width - 350)) + 'px'; tip.style.top = Math.max(4, Math.min(y, r.height - 190)) + 'px';
    }
    function hit(d, kids, cx, cy, label, g, hw) {
      var h = sv('path', { d: d, 'class': 'am-hit', tabindex: '0', role: 'button', 'aria-label': label });
      var show = function (ev) { tip.replaceChildren.apply(tip, kids().filter(Boolean)); place(ev, cx, cy); };
      h.addEventListener('mousemove', show); h.addEventListener('focus', function () { show(null); });
      h.addEventListener('mouseleave', function () { tip.hidden = true; }); h.addEventListener('blur', function () { tip.hidden = true; });
      (g || gL).appendChild(h);
      (g || gL).appendChild(sv('path', { d: d, 'class': 'am-hi', 'stroke-width': hw || 8 }));   // the hover / focus highlight
    }
    function lineCard(l) {                               // one connection between sites: everything known about it
      var w = l.wan;
      return [el('b', '', ['Between sites: ' + l.a + ' - ' + l.b]),
        el('div', '', ['Last success ' + ago(l.h) + (l.fail ? '; failing' + (l.err ? ': ' + l.err : '') : '')]),
        el('div', 'm', ['Partitions: ' + (l.parts || []).join(', ')]),
        el('div', '', [w ? 'WAN: ' + (w.bps ? bpsText(w.bps) : 'speed not known') + (w.util != null ? ', busiest ' + w.util + '%' : '') + (w.n > 1 ? ' (' + w.n + ' links)' : '') + (w.status && w.status !== 'Up' ? ' - ' + w.status : '')
          : D.wan_read ? 'WAN: no link between these sites in SolarWinds' : 'WAN: not read']),
        w && w.ifs && w.ifs.length ? el('div', 'm', [w.ifs[0]]) : null,
        el('div', 'm', [(l.nma || l.nmb) ? 'Network map: ' + (l.nma || '-') + ' - ' + (l.nmb || '-') : 'Not on the network map'])];
    }
    Object.keys(agg).forEach(function (k) {
      var g = agg[k], a = g.a.p, b = g.b.p, ls = g.ls, cls = worstOf(ls.map(function (l) { return l.cls; }));
      var w0 = ls.map(function (l) { return l.wan && l.wan.bps || 0; }), bps = Math.max.apply(0, w0), util = Math.max.apply(0, ls.map(function (l) { return l.wan && l.wan.util != null ? l.wan.util : -1; }));
      var straight = g.inner || oneSpot, cu = window.SiteMap.curve(a, b, straight ? 0 : 0.12), cx = cu.c[0], cy = cu.c[1];   // the shared curve (maplabel.js)
      var d = straight ? 'M' + a[0] + ',' + a[1] + ' L' + b[0] + ',' + b[1] : cu.d;
      var w = straight ? 2.4 : bps ? 2.5 + 6 * Math.log(1 + bps) / Math.log(1 + maxB) : 2;
      var layer = straight ? gI : gL;
      var hasWan = ls.some(function (l) { return l.wan; });
      layer.appendChild(sv('path', { d: d, 'class': (straight ? 'am-i' : 'am-l') + ' k-' + cls + (!straight && !hasWan ? ' k-none' : ''), 'stroke-width': w.toFixed(1) }));
      if (!portal && !straight && util >= 0) { var f = sv('path', { d: d, 'class': 'am-flow', 'stroke-width': Math.max(1.2, w * .45).toFixed(1) }); f.style.animationDuration = (5 / (0.25 + util / 50)).toFixed(2) + 's'; gL.appendChild(f); }
      hit(d, function () {
        if (ls.length === 1 && !straight) return lineCard(ls[0]);
        if (ls.length === 1) return [el('b', '', ['Same place: ' + ls[0].a + ' - ' + ls[0].b]),
          el('div', '', ['Last success ' + ago(ls[0].h) + (ls[0].fail ? '; failing' + (ls[0].err ? ': ' + ls[0].err : '') : '')]),
          el('div', 'm', ['Partitions: ' + (ls[0].parts || []).join(', ')]), el('div', 'm', ['No WAN: the same location'])];
        var worst = ls.slice().sort(function (x, y) { return RANKC[x.cls] - RANKC[y.cls] || (y.h || 1e9) - (x.h || 1e9); });
        return [el('b', '', [ls.length > 1 ? ls.length + ' AD connections here' : (straight ? 'Same place: ' : 'Between sites: ') + ls[0].a + ' - ' + ls[0].b])]
          .concat(worst.slice(0, 4).map(function (l) { return el('div', l === worst[0] ? '' : 'm', [l.a + ' - ' + l.b + ': ' + ago(l.h) + (l.fail ? ', failing' + (l.err ? ' ' + l.err.split(' ')[0] : '') : '')]); }))
          .concat([ls.length > 4 ? el('div', 'm', ['+' + (ls.length - 4) + ' more (open the location)']) : null,
            straight ? el('div', 'm', ['No WAN: the same location']) : el('div', '', [hasWan ? 'WAN: ' + (bps ? bpsText(bps) : 'speed not known') + (util >= 0 ? ', busiest ' + util + '%' : '') : D.wan_read ? 'WAN: no link in SolarWinds' : 'WAN: not read'])]);
      }, cx, cy, ls.length === 1 ? ls[0].a + ' to ' + ls[0].b + ', last success ' + ago(ls[0].h) : ls.length + ' connections', straight ? gI : gL, straight ? 8 : w + 6);
    });
    /* labels: the worst and the bubbles first, the rest where they fit (all names on hover and focus) - 1.102.0: the
       placement shared with the network map's Region view (roles/netmap_report/files/web/maplabel.js) */
    var LB = window.SiteMap.labels(svg, W, H, { probe: 'am-s', sub: 'sub', lead: 'am-lead', line: 13 }), taken = LB.taken;
    if (O) taken.push([O.x - R - 40, O.y - R - 50, O.x + R + 40, O.y + R + 40]);
    function label(g, x, y, lines, force, end, r) { return LB.place(g, x, y, lines, force, end, r); }
    function nameTip(g, lines, x, y) {
      var show = function (ev) { tip.replaceChildren.apply(tip, [el('b', '', [lines[0]])].concat(lines.slice(1).map(function (t) { return el('div', 'm', [t]); }))); place(ev, x, y); };
      g.addEventListener('mousemove', show); g.addEventListener('focus', function () { show(null); });
      g.addEventListener('mouseleave', function () { tip.hidden = true; }); g.addEventListener('blur', function () { tip.hidden = true; });
    }
    var order = locs.slice().sort(function (a, b) {
      var wa = worstOf(linksAt[a.id] || []), wb = worstOf(linksAt[b.id] || []);
      return (b.m.length > 1) - (a.m.length > 1) || RANKC[wa] - RANKC[wb] || b.m.length - a.m.length;
    });
    order.forEach(function (L) { if (!L.open) LB.dot(L.x, L.y, L.m.length > 1 ? 17 : 11); });   // no label on any dot
    order.forEach(function (L) {
      if (L.open) return;
      var bub = L.m.length > 1, s = L.m[0], wst = worstOf(linksAt[L.id] || []), dcsN = L.m.reduce(function (n, x) { return n + x.dcs.length; }, 0);
      var g = sv('g', { 'class': 'am-s' + (bub ? ' bub k-' + wst : ' many'), tabindex: '0', role: 'button',
        'aria-label': bub ? (L.nm || 'a place') + ': ' + L.m.length + ' AD sites, ' + dcsN + ' DCs - open' : s.n + ', ' + s.dcs.length + ' DCs - open' });
      var r = bub ? 17 : 11;
      g.appendChild(sv('circle', { cx: L.x, cy: L.y, r: r }));
      g.appendChild(sv('text', { x: L.x, y: L.y + 4, 'text-anchor': 'middle', 'class': 'cnt' }, [String(bub ? L.m.length : s.dcs.length)]));
      var where = (L.nm || 'here') + ((L.places || []).length > 1 ? ' +' + (L.places.length - 1) : '');
      var lines = bub ? [where + ' · ' + L.m.length + ' AD sites', dcsN + ' DCs'] : [s.n, s.dcs.length + (s.dcs.length === 1 ? ' DC' : ' DCs') + (s.nm && s.nm !== s.n ? ' · ' + s.nm : (!s.nm ? ' · no network-map site' : ''))];
      label(g, L.x + r + 6, L.y - 1, lines, wst === 'critical', false, r);
      nameTip(g, lines, L.x, L.y);
      var go = bub ? function () { openLoc = L.id; openSite = ''; open = ''; draw(); } : function () { open = s.n; openLoc = ''; openSite = ''; draw(); };
      g.addEventListener('click', go); g.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); go(); } });
      gS.appendChild(g);
    });
    LB.done();
    if (O) {                                             // the location opened in place: its AD sites, the lines between them
      gI.insertBefore(sv('circle', { cx: O.x, cy: O.y, r: R + 30, 'class': 'am-ring' }), gI.firstChild);
      O.m.forEach(function (s) {
        var q = mpos[s.n], right = q[0] >= O.x, g = sv('g', { 'class': 'am-s many', tabindex: '0', role: 'button', 'aria-label': s.n + ', ' + s.dcs.length + ' DCs - open' });
        g.appendChild(sv('circle', { cx: q[0], cy: q[1], r: 10 }));
        g.appendChild(sv('text', { x: q[0], y: q[1] + 4, 'text-anchor': 'middle', 'class': 'cnt' }, [String(s.dcs.length)]));
        g.appendChild(sv('text', { x: q[0] + (right ? 14 : -14), y: q[1] + 4, 'text-anchor': right ? 'start' : 'end' }, [s.n]));   // inside the disc: nothing else there
        nameTip(g, [s.n, s.dcs.join(', ')], q[0], q[1]);
        var go = function () { openSite = s.n; open = ''; draw(); };
        g.addEventListener('click', go); g.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); go(); } });
        gI.appendChild(g);
      });
      var c = sv('g', { 'class': 'am-close', tabindex: '0', role: 'button' });
      c.appendChild(sv('text', { x: O.x, y: O.y - R - 38, 'text-anchor': 'middle' }, [(O.nm || 'here') + ' · ' + O.m.length + ' AD sites · close']));
      var shut = function () { openLoc = ''; openSite = ''; open = ''; draw(); };
      c.addEventListener('click', shut); c.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ' || ev.key === 'Escape') { ev.preventDefault(); shut(); } });
      gI.appendChild(c);
    }
    var which = openSite || open, S0 = which && byName[which];
    if (S0) {                                            // an AD site's DCs (from the map, or from an opened location)
      var cp = O && openSite ? [O.x, O.y] : pos[which] || [W / 2, H / 2], R2 = Math.min(84, W * 0.08), dp = Object.create(null);
      if (O && openSite) [].slice.call(gI.childNodes).forEach(function (n) { n.remove(); });
      S0.dcs.forEach(function (d, k) { var a = -Math.PI / 2 + k * 2 * Math.PI / S0.dcs.length; dp[d.toLowerCase()] = [cp[0] + R2 * Math.cos(a), cp[1] + R2 * Math.sin(a)]; });
      gI.appendChild(sv('circle', { cx: cp[0], cy: cp[1], r: R2 + 30, 'class': 'am-ring' }));
      (own(inside, which) ? inside[which] : []).forEach(function (t) {
        var a = dp[t.a.toLowerCase()], b = dp[t.b.toLowerCase()]; if (!a || !b) return;
        var d = 'M' + a[0] + ',' + a[1] + ' L' + b[0] + ',' + b[1];
        gI.appendChild(sv('path', { d: d, 'class': 'am-i k-' + (stale ? 'unknown' : t.cls) }));
        hit(d, function () { return [el('b', '', ['Inside ' + which + ': ' + t.a + ' - ' + t.b]),
          el('div', '', ['Last success ' + ago(t.h) + (t.fail ? '; failing' + (t.err ? ': ' + t.err : '') : '')]),
          el('div', 'm', ['Partitions: ' + (t.parts || []).join(', ')]), el('div', 'm', ['No WAN: the same site'])]; },
          (a[0] + b[0]) / 2, (a[1] + b[1]) / 2, t.a + ' to ' + t.b + ' inside ' + which, gI, 8);
      });
      S0.dcs.forEach(function (d) { var q = dp[d.toLowerCase()], right = q[0] >= cp[0], g = sv('g', { 'class': 'am-s dc' });
        g.appendChild(sv('circle', { cx: q[0], cy: q[1], r: 6 }));
        g.appendChild(sv('text', { x: q[0] + (right ? 9 : -9), y: q[1] + 4, 'text-anchor': right ? 'start' : 'end' }, [d])); gI.appendChild(g); });
      var c2 = sv('g', { 'class': 'am-close', tabindex: '0', role: 'button' });
      c2.appendChild(sv('text', { x: cp[0], y: cp[1] - R2 - 38, 'text-anchor': 'middle' }, [which + ' · ' + S0.dcs.length + ' DCs · ' + (O && openSite ? 'back to ' + (O.nm || 'the place') : 'close')]));
      var back = function () { if (O && openSite) openSite = ''; else { open = ''; openLoc = ''; openSite = ''; } draw(); };
      c2.addEventListener('click', back); c2.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ' || ev.key === 'Escape') { ev.preventDefault(); back(); } });
      gI.appendChild(c2);
    }
    window.amTest.openLoc = function () { var L = locs.filter(function (x) { return x.m.length > 1; }).sort(function (a, b) { return b.m.length - a.m.length; })[0]; if (!L) return false; openLoc = L.id; openSite = ''; draw(); return true; };
    window.amTest.openMember = function (n) { var L = O || locs.filter(function (x) { return x.id === openLoc; })[0]; if (!L) return false; var m = L.m.filter(function (s) { return s.dcs.length > 1; }); var pick = m.filter(function (s) { return s.n === n; })[0] || m[0]; if (!pick) return false; openSite = pick.n; draw(); return true; };
  }
  leg.replaceChildren.apply(leg, [el('span', '', [el('i', 'ok'), 'under ' + (D.scale.low || D.scale.warn) + ' h']),
    D.scale.low ? el('span', '', [el('i', 'low'), D.scale.low + '-' + D.scale.warn + ' h (Low)']) : null,
    el('span', '', [el('i', 'warning'), D.scale.warn + '-' + D.scale.crit + ' h']), el('span', '', [el('i', 'critical'), D.scale.crit + ' h or failing']),
    portal ? null : el('span', '', [el('i', 'none'), 'no WAN link in SolarWinds']), portal ? null : el('span', '', [el('i', 'thick'), 'between sites: as wide as the WAN traffic']),
    el('span', '', [el('i', 'thin'), 'inside a site: thin, straight'])].filter(Boolean));
  if (stale) leg.textContent = '? Stale AD job';
  window.amTest = {};
  draw();
  var t = 0;
  function resize() { clearTimeout(t); t = setTimeout(draw, 150); }
  window.addEventListener('resize', resize);
  window.amTest = window.amTest || {}; window.amTest.open = function (n) { open = n || ''; openLoc = ''; openSite = ''; draw(); }; window.amTest.data = D;
  return function () { clearTimeout(t); window.removeEventListener('resize', resize); };
}

(function () {
  var box = document.getElementById('am'), src = document.getElementById('admap-data');
  if (!box || !src) return;
  var data;
  try { data = JSON.parse(src.textContent); } catch (e) { return; }
  drawAdMap(box, data);
})();
