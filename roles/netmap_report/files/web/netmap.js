/* netmap.js - the network map's Sites view (roles/netmap_report, docs/NETWORK_MAP.md): sites as bubbles that open into
   a layered drawing (core, firewalls and distribution, access around their distribution), pan and zoom, search to centre,
   a side panel with the node and its neighbours, filters (only down, a site, a vendor), the changes since the last run.
   One SVG, rebuilt as nodes on each change. No library, no inline script, no HTML from strings (the website's and the
   portal's Content-Security-Policy, Trusted Types included), offline. The data: the page's <script type="application/json"
   id="netmap-data"> (the report page), or NM.load(data) again and again (the portal's live page, netmap-live.js). The world
   view (netglobe.js) opens a site here through window.nmOpenSite and hears of new data through NM.on. */
(function () {
  var box = document.getElementById('nm'); if (!box) return;
  var html = document.documentElement;
  function still() {                                   // motion off: report_motion / portal_motion, ?motion=off, the system's wish
    if (html.classList.contains('motion-off') || html.getAttribute('data-motion') === 'off' || /[?&]motion=off\b/.test(location.search)) return true;
    if (html.classList.contains('motion-always')) return false;
    return !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
  }
  box.classList.toggle('nm-move', !still());
  var svg = box.querySelector('.nm-svg'), world = box.querySelector('.nm-world'), gS = box.querySelector('.nm-sites'),
      gL = box.querySelector('.nm-links'), gN = box.querySelector('.nm-nodes'), tip = box.querySelector('.nm-stage .nm-tip'),
      panel = box.querySelector('.nm-panel'), pb = box.querySelector('.nm-pb'), perf = box.querySelector('.nm-perf');
  var ST = { Up: 'up', Warning: 'warning', Down: 'down', Unknown: 'unknown' }, GL = { Up: '✓', Warning: '!', Down: '×', Unknown: '?' };
  var R = { core: 13, fw: 10, dist: 11, wlc: 9, acc: 7 };
  var ROLE = { core: 'core', fw: 'firewall', dist: 'distribution', acc: 'access', wlc: 'wireless controller' };
  function dict() { return Object.create(null); }      // keyed by site names and ids from SolarWinds: no inherited keys (constructor ...)
  var S = { k: .42, x: 0, y: 0, open: dict(), sel: null, down: false, chg: false, site: '', vendor: '', pos: dict() };
  var NS = 'http://www.w3.org/2000/svg';
  var D = null, byId = dict(), bySite = dict(), nb = dict(), C = dict(), RS = dict(), P = dict(), shape = '', hooks = [];
  function el(ns, tag, a, kids) {
    var e = ns ? document.createElementNS(ns, tag) : document.createElement(tag);
    for (var k in a) if (a[k] != null && a[k] !== false) e.setAttribute(k, a[k]);
    (kids || []).forEach(function (c) { if (c != null && c !== '') e.appendChild(typeof c === 'object' ? c : document.createTextNode(String(c))); });
    return e;
  }
  function V(tag, a, kids) { return el(NS, tag, a, kids); }
  function H(tag, a, kids) { return el(null, tag, a, kids); }
  function put(parent, kids) { var f = document.createDocumentFragment(); kids.forEach(function (k) { f.appendChild(k); }); parent.replaceChildren(f); }

  /* ---- layout: each site's drawing, then the sites round a ring (sized by what is open in them) ---------------------- */
  function layout() {
    P = dict(); RS = dict();
    D.sites.forEach(function (s) {
      var ns = bySite[s] || [], c = [0, 0], role = function (r) { return ns.filter(function (n) { return n.role === r; }); };
      var cores = role('core'), fw = role('fw'), dist = role('dist'), wlc = role('wlc'), placed = dict();
      cores.forEach(function (n, i) { var a = cores.length > 1 ? i * 2 * Math.PI / cores.length + Math.PI : 0, r = cores.length > 1 ? 30 + 6 * cores.length : 0; P[n.id] = [Math.cos(a) * r, Math.sin(a) * r]; placed[n.id] = 1; });
      fw.forEach(function (n, i) { P[n.id] = [(i - (fw.length - 1) / 2) * 42, -100]; placed[n.id] = 1; });
      wlc.forEach(function (n, i) { P[n.id] = [(i - (wlc.length - 1) / 2) * 40, -170]; placed[n.id] = 1; });
      var span = 260, a0 = -90 + 50, r1 = 155 + Math.max(0, dist.length - 8) * 9, maxR = 190;
      dist.forEach(function (n, i) { var a = (a0 + (i + .5) * span / dist.length) * Math.PI / 180; P[n.id] = [Math.cos(a) * r1, Math.sin(a) * r1]; n._a = a; placed[n.id] = 1; });
      var w = dist.length ? (span / dist.length) * Math.PI / 180 * .92 : 0;
      dist.forEach(function (d) {                     // access switches in rings outside their distribution switch's wedge
        var kids = (nb[d.id] || []).map(function (l) { return byId[l.a === d.id ? l.b : l.a]; })
          .filter(function (x) { return x && x.site === s && !placed[x.id] && (x.role === 'acc'); });
        var r = r1 + 115, i = 0;
        while (i < kids.length) {
          var cap = Math.max(2, Math.floor(w * r / 26)), row = kids.slice(i, i + cap);
          row.forEach(function (n, j) { var a = d._a - w / 2 + (j + .5) * w / row.length; P[n.id] = [Math.cos(a) * r, Math.sin(a) * r]; placed[n.id] = 1; });
          i += cap; maxR = Math.max(maxR, r); r += 34;
        }
      });
      var rest = ns.filter(function (n) { return !placed[n.id]; });      // nothing to hang from: the outer rings, all round
      var r = maxR + 60, i = 0;
      while (i < rest.length) {
        var cap = Math.max(6, Math.floor(2 * Math.PI * r / 26)), row = rest.slice(i, i + cap);
        row.forEach(function (n, j) { var a = (j + .5) * 2 * Math.PI / row.length - Math.PI / 2; P[n.id] = [Math.cos(a) * r, Math.sin(a) * r]; });
        i += cap; maxR = Math.max(maxR, r); r += 34;
      }
      RS[s] = Math.max(378, maxR + 50);
    });
    place();
  }
  function rad(s) { return S.open[s] ? RS[s] + 30 : 175; }
  function place() {                                  // the sites round one ring, each with the room it takes
    var n = D.sites.length, tot = 0; C = dict();
    D.sites.forEach(function (s) { tot += 2 * rad(s) + 80; });
    var ring = Math.max(n > 1 ? 300 : 0, tot / (2 * Math.PI)), a = -Math.PI / 2;
    if (n === 1) { C[D.sites[0]] = [0, 0]; return; }
    D.sites.forEach(function (s) {
      var da = (2 * rad(s) + 80) / ring; a += da / 2; C[s] = [Math.cos(a) * ring, Math.sin(a) * ring]; a += da / 2;
    });
  }
  function at(n) { var c = C[n.site], p = P[n.id] || [0, 0]; return [c[0] + p[0], c[1] + p[1]]; }
  function pos(n) { return S.pos[n.id] || (S.open[n.site] ? at(n) : C[n.site]); }

  /* ---- what is dimmed ------------------------------------------------------------------------------------------------ */
  function dimNode(n) {
    if (S.site && n.site !== S.site) return true;
    if (S.vendor && n.vendor !== S.vendor) return true;
    if (S.down && n.status !== 'Down') return true;
    if (S.chg && !(n.new || n.gone || n.was)) return true;
    return false;
  }
  function dimLink(l) {
    if (S.chg) return !(l.new || l.gone);
    return dimNode(byId[l.a]) && dimNode(byId[l.b]);
  }

  /* ---- draw ------------------------------------------------------------------------------------------------------------ */
  function count(ns) { var o = { Up: 0, Warning: 0, Down: 0, Unknown: 0 }; ns.forEach(function (n) { if (!n.gone) o[n.status]++; }); return o; }
  function live(ns) { return ns.filter(function (n) { return !n.gone; }).length; }
  function bubble(s) {
    var c = C[s], ns = bySite[s] || [], o = count(ns), tot = live(ns) || 1, r = 92, L = 2 * Math.PI * r, off = 0, rings = [];
    ['Down', 'Warning', 'Unknown', 'Up'].forEach(function (k) {
      if (!o[k]) return; var len = L * o[k] / tot;
      rings.push(V('circle', { 'class': 'nm-ring r-' + ST[k], r: r, 'stroke-dasharray': Math.max(len - 3, 1).toFixed(1) + ' ' + (L - Math.max(len - 3, 1)).toFixed(1),
        'stroke-dashoffset': (-off).toFixed(1), transform: 'rotate(-90)' }));
      off += len;
    });
    var dim = (S.site && S.site !== s) || (S.down && !o.Down) || (S.chg && !ns.some(function (n) { return n.new || n.gone || n.was; }));
    var bs = Math.min(1.3, Math.max(1, .62 / S.k));   // zoomed out: the bubble keeps a size you can read (and its room)
    return V('g', { 'class': 'nm-bub' + (dim ? ' nm-dim' : '') + (o.Down ? ' has-down' : ''), 'data-site': s, transform: 'translate(' + c[0].toFixed(1) + ',' + c[1].toFixed(1) + ') scale(' + bs.toFixed(3) + ')', tabindex: '0', role: 'button',
      'aria-label': s + ': ' + live(ns) + ' nodes, ' + o.Down + ' down, ' + o.Warning + ' warning. Open it.' }, [o.Down ? V('circle', { 'class': 'nm-alarm', r: 96 }) : null, V('circle', { 'class': 'core', r: 80 })].concat(rings, [
      V('text', { 'class': 'cnt', 'text-anchor': 'middle', y: 4 }, [live(ns)]), V('text', { 'class': 'nm-site-sub', 'text-anchor': 'middle', y: 24 }, ['nodes']),
      o.Down ? V('text', { 'class': 'nm-down-w', 'text-anchor': 'middle', y: 46 }, ['× ' + o.Down + ' down']) : null,
      V('text', { 'class': 'nm-site-name', 'text-anchor': 'middle', y: 128 }, [s])]));
  }
  function draw() {
    if (!D) return;
    var t0 = performance.now(), sites = [], links = [], nodes = [];
    D.sites.forEach(function (s) {
      if (S.open[s]) {
        var c = C[s], ns = bySite[s] || [], o = count(ns), rs = RS[s], name = V('text', { 'class': 'nm-site-name', x: c[0], y: c[1] - rs - 12, 'text-anchor': 'middle' }, [s]);
        name.style.fontSize = Math.max(20, 15 / S.k).toFixed(0) + 'px';          // CSSOM, not a style attribute: allowed by style-src 'self'
        sites.push(V('g', { 'class': S.site && S.site !== s ? 'nm-dim' : null }, [V('circle', { 'class': 'nm-site-bg', cx: c[0], cy: c[1], r: rs }),
          V('circle', { 'class': 'nm-close', cx: c[0], cy: c[1], r: rs, 'data-close': s }, [V('title', {}, ['Close ' + s])]), name,
          V('text', { 'class': 'nm-site-sub', x: c[0], y: c[1] - rs + 8, 'text-anchor': 'middle' }, [(o.Down ? o.Down + ' down · ' : '') + live(ns) + ' nodes · click the ring to close'])]));
      } else sites.push(bubble(s));
    });
    D.links.forEach(function (l, i) {
      var a = byId[l.a], b = byId[l.b]; if (!a || !b) return;
      var wan = a.site !== b.site;
      if (!wan && !S.open[a.site]) return;
      var pa = wan && !S.open[a.site] ? C[a.site] : pos(a), pb_ = wan && !S.open[b.site] ? C[b.site] : pos(b);
      var cls = 'nm-link' + (wan ? ' wan' : '') + (l.status === 'Down' ? ' down' : '') + (l.status === 'Warning' ? ' warn' : '') + (l.new ? ' new' : '') + (l.gone ? ' gone' : '') + (l.flash ? ' flash' : '') + (dimLink(l) ? ' nm-dim' : '');
      var d = 'M' + pa[0].toFixed(1) + ' ' + pa[1].toFixed(1) + 'L' + pb_[0].toFixed(1) + ' ' + pb_[1].toFixed(1);
      links.push(V('path', { 'class': cls, d: d }), V('path', { 'class': 'nm-hit', 'data-l': i, d: d }));
    });
    D.nodes.forEach(function (n) {
      if (!S.open[n.site]) return;
      var p = pos(n), r = R[n.role] || 7, hot = (S.down && n.status === 'Down') || (S.chg && (n.new || n.gone || n.was));
      if (hot) r *= Math.max(1, .9 / S.k);          // what a filter asks for stays big enough to see zoomed out
      var name = V('text', { 'class': 'n', 'text-anchor': 'middle', y: (r + (hot ? 14 / S.k : 12)).toFixed(1) }, [n.name]);
      if (hot) name.style.fontSize = Math.max(10, 11 / S.k).toFixed(0) + 'px';
      nodes.push(V('g', { 'class': 'nm-node ' + (n.role || 'acc') + (n.new ? ' new' : '') + (n.gone ? ' gone' : '') + (dimNode(n) ? ' nm-dim' : '') + (S.sel === n.id ? ' sel' : '') + (hot ? ' hl' : '') + (n.flash ? ' flash' : ''),
        'data-id': n.id, transform: 'translate(' + p[0].toFixed(1) + ',' + p[1].toFixed(1) + ')', tabindex: '-1' }, [
        n.status === 'Down' && !n.gone ? V('circle', { 'class': 'nm-alarm', r: r + 3 }) : null,
        n.new ? V('circle', { 'class': 'ring', r: r + 5 }) : null, V('circle', { 'class': 'b st-' + ST[n.status], r: r }),
        V('text', { 'class': 'g', 'text-anchor': 'middle', dy: (r * .38).toFixed(1), 'font-size': Math.max(9, r * 1.05).toFixed(1) }, [n.gone ? '–' : GL[n.status]]), name]));
    });
    put(gS, sites); put(gL, links); put(gN, nodes);
    var ms = performance.now() - t0, shown = D.nodes.filter(function (n) { return S.open[n.site]; }).length;
    if (perf) perf.textContent = (/[?&]debug=1\b/.test(location.search) ? 'drawn in ' + ms.toFixed(1) + ' ms · ' : '') +    // the timing only with ?debug=1
      shown + ' of ' + D.nodes.length + ' nodes · ' + D.links.length + ' links';
    box.setAttribute('data-ms', ms.toFixed(2));
  }

  /* ---- view ------------------------------------------------------------------------------------------------------------- */
  function apply(ease) {
    world.classList.toggle('ease', !!ease && !still());
    world.style.transform = 'translate(' + S.x.toFixed(1) + 'px,' + S.y.toFixed(1) + 'px) scale(' + S.k.toFixed(4) + ')';
    svg.classList.toggle('lbl', S.k > 1.35);
  }
  function size() { var r = svg.getBoundingClientRect(); return [r.width, r.height]; }
  function fitTo(x0, y0, x1, y1, ease) {
    var s = size(); if (!s[0]) return;                    // hidden (the world view is showing): fitted when it shows
    var k = Math.min(s[0] / (x1 - x0), s[1] / (y1 - y0)) * .92;
    S.k = Math.max(.04, Math.min(4, k)); S.x = s[0] / 2 - (x0 + x1) / 2 * S.k; S.y = s[1] / 2 - (y0 + y1) / 2 * S.k; apply(ease); redraw();
  }
  function fitAll(ease) {
    if (!D || !D.sites.length) return;
    var xs = [], ys = [];
    D.sites.forEach(function (s) { var r = S.open[s] ? RS[s] + 30 : 140; xs.push(C[s][0] - r, C[s][0] + r); ys.push(C[s][1] - r, C[s][1] + r + (S.open[s] ? 20 : 60)); });
    ys.push(Math.max.apply(0, ys) + 120 / Math.max(S.k, .2));   // room for the legend
    fitTo(Math.min.apply(0, xs), Math.min.apply(0, ys), Math.max.apply(0, xs), Math.max.apply(0, ys), ease);
  }
  function fitSite(s, ease) { var c = C[s], r = RS[s] + 30; fitTo(c[0] - r, c[1] - r - 10, c[0] + r, c[1] + r, ease); }
  function center(n, k, ease) { var s = size(), p = pos(n); S.k = k || Math.max(S.k, 2); S.x = s[0] / 2 - p[0] * S.k; S.y = s[1] / 2 - p[1] * S.k; apply(ease); }
  function zoomAt(f, cx, cy) { var k = Math.max(.04, Math.min(4, S.k * f)); S.x = cx - (cx - S.x) * k / S.k; S.y = cy - (cy - S.y) * k / S.k; S.k = k; apply(false); redraw(); }
  var rq = 0;
  function redraw() { if (rq) return; rq = requestAnimationFrame(function () { rq = 0; draw(); }); }
  function openSite(s) { if (S.open[s]) return; S.open[s] = true; place(); }
  function closeSite(s) { delete S.open[s]; place(); }

  /* ---- the panel -------------------------------------------------------------------------------------------------------- */
  function chip(st) { return H('span', { 'class': 'nm-chip ' + ST[st] }, [GL[st] + ' ' + st]); }
  function show(n) {
    if (!n) return;
    S.sel = n.id;
    var other = function (l) { return byId[l.a === n.id ? l.b : l.a]; };
    var ls = (nb[n.id] || []).filter(function (l) { return other(l); }).sort(function (a, b) { var x = other(a), y = other(b); return (x.status === 'Down' ? 0 : 1) - (y.status === 'Down' ? 0 : 1) || (x.name < y.name ? -1 : 1); });
    var dl = [['IP', n.ip], ['Vendor', n.vendor], ['Model', n.model], ['Role', ROLE[n.role] || ''], ['Site', n.site], ['Location', n.loc], ['Response', n.resp], ['SolarWinds', n.why]];
    Object.keys(n.props || {}).forEach(function (k) { dl.push([k, n.props[k]]); });
    if (n.gone) dl.push(['Change', 'in the last run (' + D.last + '), not in this one']);
    else if (n.new) dl.push(['Change', D.last ? 'new since ' + D.last : 'new']);
    else if (n.was) dl.push(['Change', n.was + ' → ' + n.status + (D.last ? ' since ' + D.last : '')]);
    put(pb, [H('h3', {}, [n.name, n.new ? H('span', { 'class': 'nm-badge' }, ['NEW']) : null, n.gone ? H('span', { 'class': 'nm-badge' }, ['MISSING']) : null]), chip(n.status),
      H('dl', {}, [].concat.apply([], dl.filter(function (r) { return r[1]; }).map(function (r) { return [H('dt', {}, [r[0]]), H('dd', {}, [r[1]])]; }))),
      H('h4', {}, [ls.length === 1 ? 'Neighbour' : 'Neighbours (' + ls.length + ')']),
      H('ul', {}, ls.map(function (l) {
        var o = other(l), mine = l.a === n.id ? l.ia : l.ib, its = l.a === n.id ? l.ib : l.ia;
        return H('li', {}, [H('button', { type: 'button', 'data-go': o.id }, [H('span', { 'class': 'nm-dot st-' + ST[o.status], 'aria-hidden': 'true' }),
          H('span', {}, [o.name + ' ', H('span', { 'class': 'nm-sr' }, ['(' + o.status + ')']), (o.site !== n.site ? ' · ' + o.site : '') + (l.gone ? ' · link missing' : l.new ? ' · link new' : l.status === 'Down' ? ' · link down' : '')]),
          H('span', { 'class': 'ifs' }, [(mine || '?') + ' ↔ ' + (its || '?') + (l.speed ? ' · ' + l.speed : '') + (l['in'] != null ? ' · ' + l['in'] + '% in / ' + l.out + '% out' : '')])])]);
      })), H('p', { 'class': 'nm-src' }, [D.source + (D.run ? ' · ' + D.run : '')])]);
    panel.hidden = false; draw();
  }
  function go(n) {
    if (!n) return;
    if (!S.open[n.site]) { openSite(n.site); draw(); }
    center(n, Math.max(S.k, 1.9), true); show(n);
    var g = gN.querySelector('[data-id="' + n.id + '"]');
    if (g && !still()) g.insertBefore(V('circle', { 'class': 'nm-pulse go', r: 12 }), g.firstChild);
  }

  /* ---- events ------------------------------------------------------------------------------------------------------------ */
  var drag = null;
  svg.addEventListener('pointerdown', function (e) {
    var g = e.target.closest('.nm-node');
    drag = { x: e.clientX, y: e.clientY, sx: S.x, sy: S.y, node: g ? byId[g.getAttribute('data-id')] : null, moved: false };
    svg.setPointerCapture(e.pointerId);
  });
  svg.addEventListener('pointermove', function (e) {
    if (drag) {
      var dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (!drag.moved && Math.abs(dx) + Math.abs(dy) < 4) return;
      drag.moved = true; svg.classList.add('drag'); tip.hidden = true;
      if (drag.node) { var p = pos(drag.node); S.pos[drag.node.id] = [p[0] + dx / S.k, p[1] + dy / S.k]; drag.x = e.clientX; drag.y = e.clientY; draw(); }
      else { S.x = drag.sx + dx; S.y = drag.sy + dy; apply(false); }
      return;
    }
    var h = e.target.closest('.nm-hit, .nm-node, .nm-bub'), r = box.querySelector('.nm-stage').getBoundingClientRect();
    if (!h) { tip.hidden = true; return; }
    var t;
    if (h.classList.contains('nm-hit')) {
      var l = D.links[+h.getAttribute('data-l')], a = byId[l.a], b = byId[l.b];
      t = a.name + ' ' + (l.ia || '?') + ' ↔ ' + b.name + ' ' + (l.ib || '?') + (l.speed ? ' · ' + l.speed : '') + (l['in'] != null ? ' · ' + l['in'] + '% in / ' + l.out + '% out' : '') + ' · ' +
        (l.gone ? 'missing since the last run' : l.new ? 'new' : l.status + (l.why ? ' (' + l.why + ')' : ''));
    } else if (h.classList.contains('nm-node')) { var n = byId[h.getAttribute('data-id')]; t = n.name + ' · ' + GL[n.status] + ' ' + n.status + (n.vendor || n.model ? ' · ' + (n.vendor + ' ' + n.model).trim() : ''); }
    else t = h.getAttribute('aria-label');
    tip.textContent = t; tip.hidden = false; tip.style.left = (e.clientX - r.left) + 'px'; tip.style.top = (e.clientY - r.top) + 'px';
  });
  svg.addEventListener('pointerleave', function () { tip.hidden = true; });
  svg.addEventListener('pointerup', function (e) {
    var d = drag; drag = null; svg.classList.remove('drag');
    if (!d || d.moved) return;
    var g = e.target.closest('.nm-node'), b = e.target.closest('.nm-bub'), cl = e.target.closest('[data-close]');
    if (g) show(byId[g.getAttribute('data-id')]);
    else if (b) { var s = b.getAttribute('data-site'); openSite(s); draw(); fitSite(s, true); }
    else if (cl) { closeSite(cl.getAttribute('data-close')); draw(); }
  });
  svg.addEventListener('wheel', function (e) { e.preventDefault(); var r = svg.getBoundingClientRect(); zoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX - r.left, e.clientY - r.top); }, { passive: false });
  svg.addEventListener('keydown', function (e) { var b = e.target.closest('.nm-bub'); if (b && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); var s = b.getAttribute('data-site'); openSite(s); draw(); fitSite(s, true); } });
  pb.addEventListener('click', function (e) { var b = e.target.closest('[data-go]'); if (b) go(byId[b.getAttribute('data-go')]); });
  box.querySelector('.nm-x').addEventListener('click', function () { panel.hidden = true; S.sel = null; draw(); });
  var q = box.querySelector('.nm-q'), dlist = document.getElementById('nm-names');
  function find() {
    var v = q.value.trim().toLowerCase(); if (!v || !D) return;
    var n = D.nodes.filter(function (x) { return x.name.toLowerCase() === v || x.ip === v; })[0] || D.nodes.filter(function (x) { return x.name.toLowerCase().indexOf(v) >= 0 || (x.ip || '').indexOf(v) === 0; })[0];
    if (n) { if (box.getAttribute('data-view') === 'world' && window.nmView) window.nmView('sites'); go(n); }
  }
  q.addEventListener('change', find); q.addEventListener('keydown', function (e) { if (e.key === 'Enter') find(); });
  function tog(sel, key, then) {
    var b = box.querySelector(sel);
    b.addEventListener('click', function () { S[key] = !S[key]; b.setAttribute('aria-pressed', String(S[key])); if (then) then(S[key]); draw(); });
  }
  function openWhere(f) { D.nodes.forEach(function (n) { if (f(n)) S.open[n.site] = true; }); place(); }
  tog('.nm-down', 'down', function (on) { if (on) openWhere(function (n) { return n.status === 'Down'; }); draw(); fitAll(true); });
  tog('.nm-chg', 'chg', function (on) { if (on) openWhere(function (n) { return n.new || n.gone || n.was; }); draw(); fitAll(true); });
  var ss = box.querySelector('.nm-site'), vs = box.querySelector('.nm-vendor');
  ss.addEventListener('change', function () {
    if (box.getAttribute('data-view') === 'world' && ss.value && window.ngFly && window.ngFly(ss.value)) return;   // a placed site: the globe flies there, then opens it here
    if (box.getAttribute('data-view') === 'world' && ss.value && window.nmView) window.nmView('sites');
    S.site = ss.value; if (S.site) { openSite(S.site); draw(); fitSite(S.site, true); } else { draw(); fitAll(true); }
  });
  vs.addEventListener('change', function () { S.vendor = vs.value; draw(); });
  var ex = box.querySelector('.nm-exp');
  ex.addEventListener('click', function () {
    var all = D.sites.every(function (s) { return S.open[s]; });
    D.sites.forEach(function (s) { if (all) delete S.open[s]; else S.open[s] = true; });
    place(); ex.textContent = all ? 'Expand all' : 'Collapse all'; draw(); fitAll(true);
  });
  function inSites() { return box.getAttribute('data-view') !== 'world'; }
  box.querySelector('.nm-zi').addEventListener('click', function () { if (!inSites()) return; var s = size(); zoomAt(1.3, s[0] / 2, s[1] / 2); });
  box.querySelector('.nm-zo').addEventListener('click', function () { if (!inSites()) return; var s = size(); zoomAt(1 / 1.3, s[0] / 2, s[1] / 2); });
  box.querySelector('.nm-fit').addEventListener('click', function () { if (inSites()) fitAll(true); });
  function lists() {                                  // the Site and Vendor lists and the search's names, kept to the data
    var keep = ss.value, kv = vs.value;
    put(ss, [H('option', { value: '' }, ['All'])].concat(D.sites.map(function (s) { return H('option', {}, [s]); })));
    put(vs, [H('option', { value: '' }, ['All'])].concat(Object.keys(D.nodes.reduce(function (o, n) { if (n.vendor) o[n.vendor] = 1; return o; }, {})).sort()
      .map(function (v) { return H('option', {}, [v]); })));
    ss.value = D.sites.indexOf(keep) >= 0 ? keep : ''; vs.value = kv; if (vs.value !== kv) { vs.value = ''; S.vendor = ''; }
    if (dlist) put(dlist, D.nodes.filter(function (n) { return !n.gone; }).slice(0, 5000).map(function (n) { return H('option', { value: n.name }, [(n.ip ? n.ip + ' · ' : '') + n.site]); }));
  }

  /* ---- the data: the first, and each new one (the portal's live page) ---------------------------------------------------- */
  function index() {
    byId = dict(); bySite = dict(); nb = dict();
    D.nodes.forEach(function (n) { byId[n.id] = n; (bySite[n.site] = bySite[n.site] || []).push(n); });
    D.links.forEach(function (l) { (nb[l.a] = nb[l.a] || []).push(l); (nb[l.b] = nb[l.b] || []).push(l); });
  }
  function key(l) { return [l.a + '/' + (l.ka != null ? l.ka : l.ia), l.b + '/' + (l.kb != null ? l.kb : l.ib)].sort().join('|'); }
  // -> what changed since the data before: [{kind: down|up|status|new|gone|linkdown|linkup, ...}]
  function load(data) {
    var old = D, changes = [];
    if (old) {
      var on = dict(), ol = dict();
      old.nodes.forEach(function (n) { on[n.id] = n; });
      old.links.forEach(function (l) { ol[key(l)] = l; });
      data.nodes.forEach(function (n) {
        var o = on[n.id];
        if (!o) { if (!n.gone) changes.push({ kind: 'new', node: n }); return; }
        if (o.status !== n.status && !n.gone) { n.flash = true; changes.push({ kind: n.status === 'Down' ? 'down' : o.status === 'Down' ? 'up' : 'status', node: n, was: o.status }); }
      });
      data.links.forEach(function (l) {
        var o = ol[key(l)];
        if (o && o.status !== l.status && (l.status === 'Down' || o.status === 'Down')) { l.flash = true; changes.push({ kind: l.status === 'Down' ? 'linkdown' : 'linkup', link: l }); }
      });
    }
    var sh = data.sites.join('\u0001') + '\u0002' + data.nodes.map(function (n) { return n.id + ':' + n.site + ':' + n.role; }).join(',');
    D = data; index();
    if (sh !== shape) { shape = sh; layout(); } else place();
    lists();
    hooks.forEach(function (f) { f(D, changes); });
    if (S.sel != null && byId[S.sel] && !panel.hidden) show(byId[S.sel]); else draw();
    if (!old) fitAll(false);
    if (changes.length && !still()) setTimeout(function () { D.nodes.forEach(function (n) { delete n.flash; }); D.links.forEach(function (l) { delete l.flash; }); draw(); }, 4000);
    return changes;
  }
  // the world view: a site picked there opens here (its layout, fitted); the Sites button shows what was open
  window.nmOpenSite = function (s) { ss.value = ''; S.site = ''; openSite(s); draw(); fitSite(s, false); };
  window.nmShown = function () { draw(); fitAll(false); };
  window.NM = { load: load, on: function (f) { hooks.push(f); if (D) f(D, []); }, data: function () { return D; }, still: still };
  window.nmTest = { S: S, draw: draw, fitAll: fitAll, go: function (name) { go(D.nodes.filter(function (n) { return n.name === name; })[0]); },
    open: function (all) { D.sites.forEach(function (s) { S.open[s] = all; if (!all) delete S.open[s]; }); place(); draw(); fitAll(false); } };
  var src = document.getElementById('netmap-data');
  if (src) { try { load(JSON.parse(src.textContent)); } catch (e) { /* no data in the page: netmap-live.js brings it */ } }
})();
