/* 1.101.0: the AD report's replication map (roles/ad_report/files/web; plugins/filter/ad_map_filters.py makes its data).
   The AD sites where the network map places their sites (else a ring around the site with the most DCs), the replication
   connections between them - colour: time since the last success (the matrix's scale), width: the WAN traffic between the
   two sites in SolarWinds, dashes moving with it - and a site with several DCs opening in place into its DCs and the
   connections inside it. No inline script or style in the page; motion: report_motion and the system's wish. */
(function () {
  var box = document.getElementById('am'), src = document.getElementById('admap-data');
  if (!box || !src) return;
  var D; try { D = JSON.parse(src.textContent); } catch (e) { return; }
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
  var byName = {}; sites.forEach(function (s) { byName[s.n] = s; });
  function ago(h) { return h == null ? 'never' : h < 1 ? Math.round(h * 60) + ' min ago' : h < 48 ? (Math.round(h * 10) / 10) + ' h ago' : Math.round(h / 24) + ' days ago'; }
  function bpsText(b) { if (!b) return ''; var u = [[1e12, 'Tbps'], [1e9, 'Gbps'], [1e6, 'Mbps'], [1e3, 'kbps']]; for (var i = 0; i < u.length; i++) if (b >= u[i][0]) return (Math.round(b / u[i][0] * 10) / 10) + ' ' + u[i][1]; return b + ' bps'; }
  var placedN = sites.filter(function (s) { return s.p; }).length;
  note.textContent = placedN ? '' : (D.placed_by ? 'No AD site has a place on the network map (netmap_sites): the sites in a ring.' :
    'Not placed (no network-map sites read): the sites in a ring around the one with the most DCs.');

  function draw() {
    [].slice.call(wrap.querySelectorAll('svg')).forEach(function (x) { x.remove(); });
    tip.hidden = true;
    var W = Math.max(560, Math.round(wrap.clientWidth || 1000)), H = Math.round(Math.max(360, W * 0.52));
    var svg = sv('svg', { 'class': 'am-svg', viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': 'Replication map: ' + sites.length + ' AD sites, ' + links.length + ' connections between them' });
    wrap.insertBefore(svg, tip);
    var pos = {}, placed = sites.filter(function (s) { return s.p; }), un = sites.filter(function (s) { return !s.p; });
    if (placed.length) {
      var la = placed.map(function (s) { return s.p[0]; }), lo = placed.map(function (s) { return s.p[1]; });
      var pad = placed.length > 1 ? 4 : 10, lat0 = Math.min.apply(0, la) - pad, lat1 = Math.max.apply(0, la) + pad, lon0 = Math.min.apply(0, lo) - pad * 1.5, lon1 = Math.max.apply(0, lo) + pad * 1.5;
      var kx = Math.cos((lat0 + lat1) / 2 * Math.PI / 180), mapW = W - (un.length ? 180 : 20), sc = Math.min(mapW / ((lon1 - lon0) * kx), (H - 40) / (lat1 - lat0));
      var ox = 10 + (mapW - (lon1 - lon0) * kx * sc) / 2, oy = 20 + (H - 40 - (lat1 - lat0) * sc) / 2;
      var pj = function (lat, lon) { return [ox + (lon - lon0) * kx * sc, oy + (lat1 - lat) * sc]; };
      var gl = sv('g'); svg.appendChild(gl);
      (window.NM_LAND || []).forEach(function (r) { var d = '', near = false;
        for (var i = 0; i < r.length; i += 2) { var q = pj(r[i + 1], r[i]); d += (i ? 'L' : 'M') + q[0].toFixed(1) + ',' + q[1].toFixed(1);
          if (r[i] > lon0 - 40 && r[i] < lon1 + 40 && r[i + 1] > lat0 - 30 && r[i + 1] < lat1 + 30) near = true; }
        if (near) gl.appendChild(sv('path', { d: d + 'Z', 'class': 'am-land' })); });
      var at = {};                                        // AD sites on one network-map site: side by side
      placed.forEach(function (s) { var q = pj(s.p[0], s.p[1]), k = Math.round(q[0]) + ',' + Math.round(q[1]), i = at[k] = (at[k] || 0) + 1;
        pos[s.n] = i === 1 ? q : [q[0] + 26 * Math.cos(i * 2.1), q[1] + 26 * Math.sin(i * 2.1)]; });
      if (un.length) svg.appendChild(sv('text', { x: W - 165, y: 34, 'class': 'am-unpl' }, ['NO MAP POSITION']));
      un.forEach(function (s, k) { pos[s.n] = [W - 150, 66 + k * 54]; });
    } else {
      var hub = sites.slice().sort(function (a, b) { return b.dcs.length - a.dcs.length; })[0], rest = sites.filter(function (s) { return s !== hub; });
      if (hub) pos[hub.n] = [W / 2, H / 2];
      rest.forEach(function (s, k) { var a = -Math.PI / 2 + k * 2 * Math.PI / Math.max(1, rest.length); pos[s.n] = [W / 2 + W * 0.36 * Math.cos(a), H / 2 + H * 0.38 * Math.sin(a)]; });
    }
    var gL = sv('g'), gS = sv('g'), gI = sv('g'); svg.appendChild(gL); svg.appendChild(gS); svg.appendChild(gI);
    var maxB = Math.max.apply(0, links.map(function (l) { return l.wan && l.wan.bps || 0; }).concat([1]));
    function place(ev, cx, cy) {
      var r = wrap.getBoundingClientRect(), x = (ev && ev.clientX ? ev.clientX - r.left : cx / W * r.width) + 14, y = (ev && ev.clientY ? ev.clientY - r.top : cy / H * r.height) + 14;
      tip.hidden = false; tip.style.left = Math.max(4, Math.min(x, r.width - 350)) + 'px'; tip.style.top = Math.max(4, Math.min(y, r.height - 170)) + 'px';
    }
    function hit(d, kids, cx, cy, label) {
      var h = sv('path', { d: d, 'class': 'am-hit', tabindex: '0', role: 'button', 'aria-label': label });
      var show = function (ev) { tip.replaceChildren.apply(tip, kids().filter(Boolean)); place(ev, cx, cy); };   // no null shown as text
      h.addEventListener('mousemove', show); h.addEventListener('focus', function () { show(null); });
      h.addEventListener('mouseleave', function () { tip.hidden = true; }); h.addEventListener('blur', function () { tip.hidden = true; });
      return h;
    }
    function lineCard(l) {
      var w = l.wan;
      return [el('b', '', ['Between sites: ' + l.a + ' - ' + l.b]),
        el('div', '', ['Last success ' + ago(l.h) + (l.fail ? '; failing' + (l.err ? ': ' + l.err : '') : '')]),
        el('div', 'm', ['Partitions: ' + (l.parts || []).join(', ')]),
        el('div', '', [w ? 'WAN: ' + (w.bps ? bpsText(w.bps) : 'speed not known') + (w.util != null ? ', busiest ' + w.util + '%' : '') + (w.n > 1 ? ' (' + w.n + ' links)' : '') + (w.status && w.status !== 'Up' ? ' - ' + w.status : '')
          : D.wan_read ? 'WAN: no link between these sites in SolarWinds' : 'WAN: not read']),
        w && w.ifs && w.ifs.length ? el('div', 'm', [w.ifs[0]]) : null,
        el('div', 'm', [(l.nma || l.nmb) ? 'Network map: ' + (l.nma || '-') + ' - ' + (l.nmb || '-') : 'Not on the network map'])];
    }
    links.forEach(function (l) {
      var a = pos[l.a], b = pos[l.b]; if (!a || !b) return;
      var mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2, dx = b[0] - a[0], dy = b[1] - a[1], cx = mx - dy * 0.12, cy = my + dx * 0.12;
      var d = 'M' + a[0] + ',' + a[1] + ' Q' + cx + ',' + cy + ' ' + b[0] + ',' + b[1];
      var bps = l.wan && l.wan.bps, w = bps ? 2.5 + 6 * Math.log(1 + bps) / Math.log(1 + maxB) : 2;
      gL.appendChild(sv('path', { d: d, 'class': 'am-l k-' + l.cls + (l.wan ? '' : ' k-none'), 'stroke-width': w.toFixed(1) }));
      if (l.wan && l.wan.util != null) {
        var f = sv('path', { d: d, 'class': 'am-flow', 'stroke-width': Math.max(1.2, w * 0.45).toFixed(1) });
        f.style.animationDuration = (5 / (0.25 + l.wan.util / 50)).toFixed(2) + 's'; gL.appendChild(f);
      }
      gL.appendChild(hit(d, function () { return lineCard(l); }, cx, cy, l.a + ' to ' + l.b + ', last success ' + ago(l.h)));
      gL.appendChild(sv('path', { d: d, 'class': 'am-hi', 'stroke-width': (w + 6).toFixed(1) }));
    });
    var taken = [];
    function label(g, x, y, lines, force, anchorEnd) {
      var w = Math.max.apply(0, lines.map(function (t) { return t.length; })) * 6.6;
      if (anchorEnd) x -= w;
      x = Math.max(4, Math.min(x, W - w - 4)); y = Math.max(14, Math.min(y, H - 6 - 13 * (lines.length - 1)));   // never cut off
      var b = [x, y - 12, x + w, y + 4 + 13 * (lines.length - 1)];
      if (!force && taken.some(function (t) { return b[0] < t[2] && b[2] > t[0] && b[1] < t[3] && b[3] > t[1]; })) return;
      taken.push(b); lines.forEach(function (t, i) { g.appendChild(sv('text', { x: x, y: y + i * 13, 'class': i ? 'sub' : null }, [t])); });
    }
    var disc = null;
    if (open && pos[open]) { var R0 = Math.min(84, W * 0.08); disc = { x: pos[open][0], y: pos[open][1], r: R0 + 24 }; taken.push([disc.x - disc.r, disc.y - disc.r - 40, disc.x + disc.r, disc.y + disc.r]); }
    var bad = {}; links.forEach(function (l) { if (l.cls === 'critical' || l.cls === 'warning') { bad[l.a] = 1; bad[l.b] = 1; } });
    sites.slice().sort(function (a, b) { return (bad[b.n] ? 1 : 0) - (bad[a.n] ? 1 : 0) || b.dcs.length - a.dcs.length; }).forEach(function (s) {
      var q = pos[s.n]; if (!q) return;
      if (open === s.n) return;
      var many = s.dcs.length > 1, r = many ? 12 : 8, g = sv('g', { 'class': 'am-s' + (many ? ' many' : ''), tabindex: many ? '0' : null, role: many ? 'button' : null,
        'aria-label': many ? s.n + ', ' + s.dcs.length + ' DCs: open' : null });
      g.appendChild(sv('circle', { cx: q[0], cy: q[1], r: r }));
      if (many) g.appendChild(sv('text', { x: q[0], y: q[1] + 4, 'text-anchor': 'middle', 'class': 'cnt' }, [String(s.dcs.length)]));
      var lines = [s.n, s.nm && s.nm !== s.n ? s.nm : (!s.nm ? 'no network-map site' : '')].filter(Boolean);
      var dx = disc ? q[0] - disc.x : 0, dy = disc ? q[1] - disc.y : 0, dd = Math.sqrt(dx * dx + dy * dy);
      if (disc && dd < disc.r + 70) {                    // under (or by) the opened site's disc: the label just outside it
        var ux = dd ? dx / dd : 1, uy = dd ? dy / dd : 0, lx = disc.x + ux * (disc.r + 10), ly = disc.y + uy * (disc.r + 14) + 4;
        label(g, lx, ly, lines, true, ux < 0);
      } else {
        label(g, q[0] + r + 6, q[1] - 1, lines, !!bad[s.n] || many);
      }
      if (many) { var go = function () { open = s.n; draw(); }; g.addEventListener('click', go);
        g.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); go(); } }); }
      gS.appendChild(g);
    });
    if (open && pos[open]) {                              // a site opened in place: its DCs, the connections inside it
      var s = byName[open], hp = pos[open], R = Math.min(84, W * 0.08), dp = {};
      s.dcs.forEach(function (d, k) { var a = -Math.PI / 2 + k * 2 * Math.PI / s.dcs.length; dp[d.toLowerCase()] = [hp[0] + R * Math.cos(a), hp[1] + R * Math.sin(a)]; });
      gI.appendChild(sv('circle', { cx: hp[0], cy: hp[1], r: R + 24, 'class': 'am-ring' }));
      (inside[open] || []).forEach(function (t) {
        var a = dp[t.a.toLowerCase()], b = dp[t.b.toLowerCase()]; if (!a || !b) return;
        var d = 'M' + a[0] + ',' + a[1] + ' L' + b[0] + ',' + b[1];
        gI.appendChild(sv('path', { d: d, 'class': 'am-i k-' + t.cls }));
        gI.appendChild(hit(d, function () { return [el('b', '', ['Inside ' + open + ': ' + t.a + ' - ' + t.b]),
          el('div', '', ['Last success ' + ago(t.h) + (t.fail ? '; failing' + (t.err ? ': ' + t.err : '') : '')]),
          el('div', 'm', ['Partitions: ' + (t.parts || []).join(', ')]), el('div', 'm', ['No WAN: the same site'])]; },
          (a[0] + b[0]) / 2, (a[1] + b[1]) / 2, t.a + ' to ' + t.b + ' inside ' + open));
        gI.appendChild(sv('path', { d: d, 'class': 'am-hi', 'stroke-width': 8 }));
      });
      s.dcs.forEach(function (d) { var q = dp[d.toLowerCase()], g = sv('g', { 'class': 'am-s dc' }), right = q[0] >= hp[0];
        g.appendChild(sv('circle', { cx: q[0], cy: q[1], r: 6 }));
        g.appendChild(sv('text', { x: q[0] + (right ? 9 : -9), y: q[1] + 4, 'text-anchor': right ? 'start' : 'end' }, [d])); gI.appendChild(g); });
      var c = sv('g', { 'class': 'am-close', tabindex: '0', role: 'button', 'aria-label': 'Close ' + open });
      c.appendChild(sv('text', { x: hp[0], y: hp[1] - R - 32, 'text-anchor': 'middle' }, [open + ' - ' + s.dcs.length + ' DCs - close']));
      var shut = function () { open = ''; draw(); };
      c.addEventListener('click', shut); c.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ' || ev.key === 'Escape') { ev.preventDefault(); shut(); } });
      gI.appendChild(c);
    }
  }
  leg.replaceChildren.apply(leg, [el('span', '', [el('i', 'ok'), 'under ' + (D.scale.low || D.scale.warn) + ' h']),
    D.scale.low ? el('span', '', [el('i', 'low'), D.scale.low + '-' + D.scale.warn + ' h (Low)']) : null,
    el('span', '', [el('i', 'warning'), D.scale.warn + '-' + D.scale.crit + ' h']), el('span', '', [el('i', 'critical'), D.scale.crit + ' h or failing']),
    el('span', '', [el('i', 'none'), 'no WAN link in SolarWinds']), el('span', '', [el('i', 'thick'), 'between sites: as wide as the WAN traffic']),
    el('span', '', [el('i', 'thin'), 'inside a site: thin, straight'])].filter(Boolean));
  draw();
  var t = 0; window.addEventListener('resize', function () { clearTimeout(t); t = setTimeout(draw, 150); });
  window.amTest = { open: function (n) { open = n || ''; draw(); }, data: D };
})();
