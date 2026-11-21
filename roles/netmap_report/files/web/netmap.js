/* netmap.js - the network map, level by level (roles/netmap_report, docs/NETWORK_MAP.md; 1.95.0): the World (site tiles,
   or the globe of netglobe.js when the sites are placed), a Site (its buildings and the links between them, counted), a
   Building (its devices in tiers: the edge, core, then each closet - a distribution switch over its access switches), a
   Device (its links' interfaces and neighbours). Never every node at once. One search (sites, buildings, devices, IPs),
   three chips (Down, Warnings, Changed), Filters (role, vendor), breadcrumbs - no history step (Back leaves the page).
   No library, no inline script or style, no HTML from strings (the website's and the portal's Content-Security-Policy,
   Trusted Types included), offline. The data: the page's <script type="application/json" id="netmap-data">, or NM.load(data)
   again and again (the portal's live page, netmap-live.js); the level, the selection and the filters stay. */
(function () {
  var box = document.getElementById('nm'); if (!box) return;
  var html = document.documentElement;
  function still() {                                   // motion off: report_motion / portal_motion, ?motion=off, the system's wish
    if (html.classList.contains('motion-off') || html.getAttribute('data-motion') === 'off' || /[?&]motion=off\b/.test(location.search)) return true;
    if (html.classList.contains('motion-always')) return false;
    return !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
  }
  box.classList.toggle('nm-move', !still());
  var NS = 'http://www.w3.org/2000/svg', GL = { Up: '✓', Warning: '!', Down: '×', Unknown: '?' };
  var ROLE_OLD = { fw: 'firewall', dist: 'distribution', acc: 'access', wlc: 'wireless' };   // a 1.85 map (the copy job)
  var ROLES = ['core', 'distribution', 'access', 'firewall', 'router', 'wireless', 'server', 'other'];
  var RANK = { Down: 0, Warning: 1, Unknown: 2, Up: 3 };
  function dict() { return Object.create(null); }      // keyed by names from SolarWinds: no inherited keys (constructor ...)
  function el(tag, cls, kids, at) {
    var e = document.createElement(tag); if (cls) e.className = cls;
    for (var k in at || {}) if (at[k] != null && at[k] !== false) e.setAttribute(k, at[k]);
    (kids || []).forEach(function (c) { if (c != null && c !== '') e.appendChild(typeof c === 'object' ? c : document.createTextNode(String(c))); });
    return e;
  }
  function sv(tag, at, kids) {
    var e = document.createElementNS(NS, tag);
    for (var k in at || {}) if (at[k] != null) e.setAttribute(k, at[k]);
    (kids || []).forEach(function (c) { e.appendChild(typeof c === 'object' ? c : document.createTextNode(String(c))); });
    return e;
  }
  function stc(s) { return (s || 'Unknown').toLowerCase(); }
  var body = box.querySelector('.nm-body'), crumb = box.querySelector('.nm-crumb'), note = box.querySelector('.nm-note');
  var q = box.querySelector('.nm-q'), sugg = box.querySelector('.nm-sugg'), fbtn = box.querySelector('.nm-fbtn'), fp = box.querySelector('.nm-fp');
  var S = { level: 'world', site: '', building: '', device: null, chip: dict(), roles: dict(), vendor: '', world: '' };
  var D = null, byId = dict(), sites = dict(), order = [], nb = dict(), hooks = [];
  var KEY = 'site-netmap-world';

  /* ---- the data -> sites, buildings, closets; links per node ---------------------------------------------------------- */
  function index() {
    byId = dict(); sites = dict(); nb = dict(); order = [];
    var none = D.no_site || 'Not placed';
    D.nodes.forEach(function (n) {
      n.role = ROLE_OLD[n.role] || n.role || 'other';
      byId[n.id] = n;
      var s = sites[n.site] || (sites[n.site] = { name: n.site, nodes: [], b: dict(), bo: [], down: 0, warning: 0, unknown: 0, chg: 0, links: dict(), none: n.site === none || n.site === 'No site' });
      s.nodes.push(n);
      if (n.status === 'Down') s.down++; else if (n.status === 'Warning') s.warning++; else if (n.status === 'Unknown') s.unknown++;
      if (n.new || n.gone || n.was) s.chg++;
      var bn = n.building || '';
      var b = s.b[bn] || (s.b[bn] = { name: bn, nodes: [], down: 0, warning: 0, unknown: 0, closets: dict() });
      if (!b.nodes.length) s.bo.push(bn);
      b.nodes.push(n);
      if (n.status === 'Down') b.down++; else if (n.status === 'Warning') b.warning++; else if (n.status === 'Unknown') b.unknown++;
      if (n.closet) b.closets[n.closet] = 1;
    });
    D.links.forEach(function (l) {
      (nb[l.a] = nb[l.a] || []).push(l); (nb[l.b] = nb[l.b] || []).push(l);
      var a = byId[l.a], b = byId[l.b]; if (!a || !b || a.site === b.site) return;
      [[a, b], [b, a]].forEach(function (p) {
        var e = sites[p[0].site].links[p[1].site] || (sites[p[0].site].links[p[1].site] = { to: p[1].site, n: 0, worst: 'Up', busy: 0 });
        e.n++; if (RANK[l.status] < RANK[e.worst]) e.worst = l.status;
        e.busy = Math.max(e.busy, l['in'] || 0, l.out || 0);
      });
    });
    order = Object.keys(sites).map(function (k) { return sites[k]; }).sort(function (a, b) {
      return (a.none - b.none) || (b.down - a.down) || (b.warning - a.warning) || (a.name < b.name ? -1 : 1); });
    order.forEach(function (s) { s.bo.sort(); });
  }
  function worst(x) { return x.down ? 'Down' : x.warning ? 'Warning' : x.unknown && x.unknown === (x.nodes || []).length ? 'Unknown' : 'Up'; }
  function words(x) { return x.down ? GL.Down + ' ' + x.down + ' down' : x.warning ? GL.Warning + ' ' + x.warning + ' warning' :
    x.unknown && x.unknown === (x.nodes || []).length ? GL.Unknown + ' not known' : GL.Up + ' all up'; }   // 1.95.1: all Unknown is not "all up"
  function own(o, k) { return !!o && Object.prototype.hasOwnProperty.call(o, k) && Array.isArray(o[k]); }   // geo: never "constructor"
  function keep(n) {                                   // the Filters: role, vendor
    if (Object.keys(S.roles).length && !S.roles[n.role]) return false;
    if (S.vendor && n.vendor !== S.vendor) return false;
    return true;
  }
  function chipOn(n) {                                 // the chips: what to show (the rest is dimmed, or left out of the tiles)
    var c = S.chip; if (!c.down && !c.warning && !c.chg) return true;
    return (c.down && n.status === 'Down') || (c.warning && n.status === 'Warning') || (c.chg && (n.new || n.gone || n.was));
  }
  function hubs() {                                    // a site linked to many others is a hub: its tile says how many
    var many = Math.max(5, Math.round(order.length * .3));
    return order.filter(function (s) { return Object.keys(s.links).length >= many; });
  }

  /* ---- the level, the breadcrumbs, the note ---------------------------------------------------------------------------- */
  function go(level, site, building, device) {
    S.level = level; S.site = site || ''; S.building = building == null ? '' : building; S.device = device == null ? null : device;
    if (window.nmView) window.nmView(level === 'world' && S.world === 'globe' ? 'world' : level === 'world' ? 'tiles' : level);
    else box.setAttribute('data-view', level === 'world' && S.world === 'globe' ? 'world' : level === 'world' ? 'tiles' : level);
    render();
  }
  function crumbs() {
    var t = [['World', function () { go('world'); }]];
    if (S.level !== 'world') t.push([S.site, S.level === 'site' ? null : function () { go('site', S.site); }]);
    if (S.level === 'building' || S.level === 'device') t.push([S.building ? 'Building ' + S.building : 'The whole site', S.level === 'building' ? null : function () { go('building', S.site, S.building); }]);
    if (S.level === 'device' && byId[S.device]) t.push([byId[S.device].name, null]);
    crumb.replaceChildren();
    t.forEach(function (x, i) {
      if (i) crumb.appendChild(el('span', 'nm-sep', ['›'], { 'aria-hidden': 'true' }));
      var last = i === t.length - 1;
      var b = last ? el('span', 'nm-here', [x[0]], { 'aria-current': 'location' }) : el('button', 'nm-cb', [x[0]], { type: 'button' });
      if (!last) b.addEventListener('click', x[1]);
      crumb.appendChild(b);
    });
    var w = box.querySelector('.nm-ws'); if (w) w.hidden = S.level !== 'world';
  }
  function say(text) { note.replaceChildren(); if (text) note.appendChild(typeof text === 'object' ? text : document.createTextNode(text)); note.hidden = !text; }

  /* ---- World: the site tiles, worst first; the links between sites (counted) ------------------------------------------ */
  function world() {
    var real = order.filter(function (s) { return !s.none; }), placed = real.filter(function (s) { return own(D.geo, s.name); }).length;
    if (S.world === 'globe') { say(''); body.replaceChildren(); return; }
    var m = el('span', '', [placed === 0 ? 'None of the ' + real.length + ' sites has a place on the globe yet, so they show as tiles. ' : (real.length - placed) + ' of ' + real.length + ' sites have no place on the globe yet. ',
      'Place them with ', el('code', '', ['netmap_sites']), ' (a name, or a pattern such as ', el('code', '', ['ABCD*']), '), SolarWinds’ Worldwide Map, or ', el('code', '', ['netmap_lat_property']), '.']);
    say(real.length - placed > 0 ? m : '');
    var hs = dict(); hubs().forEach(function (s) { hs[s.name] = 1; });
    var narrowed = Object.keys(S.chip).some(function (k) { return S.chip[k]; }) || Object.keys(S.roles).length || S.vendor;
    var tiles = el('div', 'nm-tiles', [], { role: 'list' }), shown = 0;
    order.forEach(function (s) {
      if (narrowed && !s.nodes.some(function (n) { return keep(n) && chipOn(n); })) return;
      shown++;
      var e = Object.keys(s.links).map(function (k) { return s.links[k]; }), wd = e.filter(function (x) { return x.worst === 'Down'; }).length, nbld = s.bo.filter(Boolean).length;
      var wan = hs[s.name] ? el('span', 'nm-wan', ['hub: ' + e.length + ' sites']) :
        !e.length ? el('span', 'nm-wan', [s.none ? 'no site in its name or properties' : 'no WAN link']) :
        el('span', 'nm-wan', [el('span', 'nm-st ' + (wd ? 'down' : 'up'), [wd ? GL.Down + ' WAN down' : GL.Up + ' WAN']), ' to ' + e.slice(0, 2).map(function (x) { return x.to; }).join(', ') + (e.length > 2 ? ' +' + (e.length - 2) : '')]);
      var t = el('button', 'nm-tile k-' + stc(worst(s)) + (s.none ? ' nm-none' : ''), [
        el('span', 'nm-tn', [el('span', 'nm-tname', [s.name]), el('span', 'nm-st ' + stc(worst(s)), [words(s)])]),
        el('span', 'nm-tm', [s.nodes.length + ' device' + (s.nodes.length === 1 ? '' : 's') + (nbld ? ' · ' + nbld + ' building' + (nbld === 1 ? '' : 's') : '')]), wan],
        { type: 'button', role: 'listitem', 'data-site': s.name });
      t.addEventListener('click', function () { go('site', s.name); });
      tiles.appendChild(t);
    });
    if (!shown) tiles.appendChild(el('p', 'nm-empty', ['Nothing matches the chips and filters.']));
    var all = [], seen = dict();
    order.forEach(function (s) { Object.keys(s.links).forEach(function (k) { var x = s.links[k], kk = [s.name, k].sort().join('\u0001'); if (seen[kk]) return; seen[kk] = 1; all.push({ a: s.name, b: k, n: x.n, worst: x.worst, busy: x.busy }); }); });
    all.sort(function (a, b) { return (RANK[a.worst] - RANK[b.worst]) || (b.busy - a.busy); });
    var tb = el('tbody'); all.slice(0, 12).forEach(function (x) {
      tb.appendChild(el('tr', '', [el('td', 'nm-mono', [x.a + ' – ' + x.b]), el('td', 'nm-num', [x.n]),
        el('td', '', [el('span', 'nm-st ' + stc(x.worst), [x.worst === 'Up' ? GL.Up + ' ' + Math.round(x.busy) + '%' : GL[x.worst] + ' ' + x.worst])])]));
    });
    var side = el('aside', 'nm-side', [el('h3', '', ['Links between sites']), el('table', 'nm-lt', [el('thead', '', [el('tr', '', [el('th', '', ['Sites']), el('th', 'nm-num', ['Links']), el('th', '', ['Worst'])])]), tb]),
      el('p', 'nm-more', [all.length > 12 ? (all.length - 12) + ' more · down first, then the busiest' : all.length ? 'down first, then the busiest' : 'no links between sites'])]);
    body.replaceChildren(el('div', 'nm-world2', [tiles, side]));
  }

  /* ---- a Site: its buildings and the links between them; its WAN, per site at the other end --------------------------- */
  function width() { return Math.max(320, body.clientWidth || box.clientWidth || 1000); }
  function card(svg, x, y, w, h, title, lines, st, cls) {
    var g = sv('g', { transform: 'translate(' + x + ',' + y + ')', 'class': 'nm-card k-' + st + (cls ? ' ' + cls : ''), tabindex: '0', role: 'button' });
    g.appendChild(sv('rect', { width: w, height: h, rx: 12, 'class': 'nm-cbox' }));
    g.appendChild(sv('rect', { width: w, height: 4, rx: 2, 'class': 'nm-cbar' }));
    g.appendChild(sv('text', { x: 14, y: 27, 'class': 'nm-ct' }, [title]));
    lines.forEach(function (l, i) { g.appendChild(sv('text', { x: 14, y: 48 + i * 19, 'class': 'nm-cl' + (l[1] ? ' ' + l[1] : '') }, [l[0]])); });
    svg.appendChild(g); return g;
  }
  function pill(svg, x, y, text, st) {
    svg.appendChild(sv('rect', { x: x - 34, y: y - 11, width: 68, height: 22, rx: 11, 'class': 'nm-pill' }));
    svg.appendChild(sv('text', { x: x, y: y + 4, 'class': 'nm-pt k-' + st, 'text-anchor': 'middle' }, [text]));
  }
  function activate(g, f) {
    g.addEventListener('click', f);
    g.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); f(); } });
  }
  function site() {
    var s = sites[S.site]; if (!s) return go('world');
    var W = width(), bw = 230, bh = 120, gap = 34, cols = Math.max(1, Math.floor((W - 20 + gap) / (bw + gap)));
    var names = s.bo.length ? s.bo : [''];
    var wan = Object.keys(s.links).map(function (k) { return s.links[k]; }).sort(function (a, b) { return (RANK[a.worst] - RANK[b.worst]) || (b.n - a.n); });
    var wcap = Math.max(1, Math.min(6, Math.floor((W - 20) / 210))), top = wan.length ? 110 : 20, rows = Math.ceil(names.length / cols);
    var H = top + rows * (bh + 70) + 30, svg = sv('svg', { 'class': 'nm-draw', viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': S.site + ': its buildings and the links between them' });
    var gL = sv('g'), gC = sv('g'); svg.appendChild(gL); svg.appendChild(gC);
    var pos = dict(), wbw = 190, x0 = (W - Math.min(wan.length, wcap) * (wbw + 20) + 20) / 2;
    wan.slice(0, wcap).forEach(function (e, i) {
      var g = card(gC, x0 + i * (wbw + 20), 12, wbw, 62, e.to, [[e.n + ' link' + (e.n === 1 ? '' : 's') + ' · ' + (e.worst === 'Up' ? 'up' : e.worst.toLowerCase()), 'k-' + stc(e.worst)]], stc(e.worst), 'nm-wanbox');
      g.setAttribute('aria-label', 'WAN to ' + e.to + ': ' + e.n + ' links');
      activate(g, function () { go('site', e.to); });
      pos['\u0001' + e.to] = [x0 + i * (wbw + 20) + wbw / 2, 74];
    });
    if (wan.length > wcap) gC.appendChild(sv('text', { x: W - 12, y: 30, 'class': 'nm-cl', 'text-anchor': 'end' }, ['+' + (wan.length - wcap) + ' more sites']));
    names.forEach(function (bn, i) {
      var b = s.b[bn] || { nodes: s.nodes, down: s.down, warning: s.warning, closets: dict() }, r = Math.floor(i / cols), c = i % cols;
      var inRow = Math.min(cols, names.length - r * cols), rx0 = (W - inRow * (bw + gap) + gap) / 2, x = rx0 + c * (bw + gap), y = top + r * (bh + 70);
      var roles = ['core', 'distribution', 'access'].map(function (k) { return b.nodes.filter(function (n) { return n.role === k; }).length + ' ' + k; }).join(' · ');
      var ncl = Object.keys(b.closets).length;
      var g = card(gC, x, y, bw, bh, bn ? 'Building ' + bn : 'The whole site', [[b.nodes.length + ' devices · ' + ncl + ' closet' + (ncl === 1 ? '' : 's')], [roles],
        [words(b), 'k-' + stc(worst(b))]], stc(worst(b)), b.nodes.some(chipOn) ? '' : 'nm-dim');
      g.setAttribute('aria-label', (bn ? 'Building ' + bn : 'The whole site') + ': ' + b.nodes.length + ' devices, ' + words(b));
      activate(g, function () { go('building', S.site, bn); });
      pos[bn] = [x + bw / 2, y, y + bh, x, x + bw];
    });
    var be = dict();                                   // the links between buildings, counted; the WAN ones, per building
    s.nodes.forEach(function (n) { (nb[n.id] || []).forEach(function (l) {
      var o = byId[l.a === n.id ? l.b : l.a]; if (!o) return;     // either end may be this site's (a WAN link stored remote -> local too)
      if (o.site === s.name && l.a !== n.id) return;               // a link inside the site: counted once, from its a end
      var nB = n.building || '', oB = o.building || '', k, e;
      if (o.site !== s.name || n.site !== s.name) {
        var other = o.site !== s.name ? o.site : n.site, mine = o.site !== s.name ? nB : oB;
        if (!pos['\u0001' + other]) return;
        k = '\u0001' + other + '\u0002' + mine; e = be[k] || (be[k] = { a: '\u0001' + other, b: mine, n: 0, worst: 'Up' });
      } else {
        if (nB === oB) return;
        k = [nB, oB].sort().join('\u0002'); e = be[k] || (be[k] = { a: nB, b: oB, n: 0, worst: 'Up' });
      }
      e.n++; if (RANK[l.status] < RANK[e.worst]) e.worst = l.status;
    }); });
    Object.keys(be).forEach(function (k) {
      var e = be[k], a = pos[e.a], b = pos[e.b]; if (!a || !b) return;
      var st = stc(e.worst), d, mid, side = Math.abs(a[1] - b[1]) < 1 && Math.abs(a[0] - b[0]) < bw + gap + 2;
      if (e.a.charAt(0) === '\u0001') { d = 'M' + a[0] + ',' + a[1] + ' C' + a[0] + ',' + (a[1] + 40) + ' ' + b[0] + ',' + (b[1] - 40) + ' ' + b[0] + ',' + b[1]; mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]; }
      else if (side) { d = 'M' + Math.min(a[4], b[4]) + ',' + (a[1] + bh / 2) + ' H' + Math.max(a[3], b[3]); mid = [(a[0] + b[0]) / 2, a[1] + bh / 2]; }
      else if (Math.abs(a[0] - b[0]) < 1) { var up = a[1] < b[1] ? a : b, dn = a[1] < b[1] ? b : a; d = 'M' + up[0] + ',' + up[2] + ' V' + dn[1]; mid = [up[0], (up[2] + dn[1]) / 2]; }   // one above the other
      else { var yy = Math.max(a[2], b[2]) + 46; d = 'M' + a[0] + ',' + a[2] + ' C' + a[0] + ',' + yy + ' ' + b[0] + ',' + yy + ' ' + b[0] + ',' + b[2]; mid = [(a[0] + b[0]) / 2, yy - 10]; }
      gL.appendChild(sv('path', { d: d, 'class': 'nm-agg k-' + st }));
      pill(gL, mid[0], mid[1], e.n + ' link' + (e.n === 1 ? '' : 's'), st);
    });
    var nb2 = s.bo.filter(Boolean).length;
    say(S.site + ': ' + (nb2 ? nb2 + ' building' + (nb2 === 1 ? '' : 's') : 'no buildings read') + ', ' + s.nodes.length + ' devices. A line counts the links between two buildings; red: one is down. A building opens its devices in tiers.');
    body.replaceChildren(el('div', 'nm-drawbox', [svg]));
  }

  /* ---- a Building: tiers - the edge (router, firewall), core, servers and wireless, then each closet (its distribution switch
          over its access switches) packed in rows, never wider than the page. The links run in the gaps between rows, a closet's
          trunk in its left gutter, the rest in the drawing's left gutter and a channel above each row of closets: no line
          crosses a box. A closet shows its down and warning devices first, then the rest up to 18: Show all lists them all. --- */
  var NW = 112, NH = 36, CAP = 18;
  S.all = S.all || dict();                             // the closets shown in full (Show all)
  function dev(svg, x, y, n, P, meta) {
    var g = sv('g', { transform: 'translate(' + (x - NW / 2) + ',' + (y - NH / 2) + ')', 'class': 'nm-dev k-' + stc(n.status) + ' r-' + n.role + (n.new ? ' new' : '') + (n.gone ? ' gone' : '') + (n.flash ? ' flash' : '') + (chipOn(n) ? '' : ' nm-dim'),
      tabindex: '0', role: 'button', 'aria-label': n.name + ', ' + n.role + ', ' + n.status });
    g.appendChild(sv('rect', { width: NW, height: NH, rx: 8, 'class': 'nm-dbox' }));
    g.appendChild(sv('rect', { width: 5, height: NH, rx: 2, 'class': 'nm-drole' }));
    var short = n.name.split(/[-_.]/).slice(-1)[0] || n.name;
    g.appendChild(sv('text', { x: 12, y: 15, 'class': 'nm-dn' }, [short.length > 13 ? short.slice(0, 12) + '…' : short]));
    g.appendChild(sv('text', { x: 12, y: 29, 'class': 'nm-dr' }, [n.role]));
    g.appendChild(sv('text', { x: NW - 9, y: 15, 'class': 'nm-dg', 'text-anchor': 'end' }, [GL[n.status] || '?']));
    g.appendChild(sv('title', {}, [n.name + (n.ip ? ' · ' + n.ip : '') + ' · ' + n.status]));
    activate(g, function () { go('device', S.site, S.building, n.id); });
    svg.appendChild(g);
    P[n.id] = { x: x, y: y, top: y - NH / 2, bot: y + NH / 2, closet: meta.closet, gapUp: meta.gapUp, gapDown: meta.gapDown, row: meta.row, crow: meta.crow, band: meta.band };
  }
  function shownOf(acc, key) {                          // down and warning first, always; then the rest, up to CAP (Show all: every one)
    var bad = acc.filter(function (n) { return n.status === 'Down' || n.status === 'Warning'; }), ok = acc.filter(function (n) { return n.status !== 'Down' && n.status !== 'Warning'; });
    var list = bad.concat(ok);
    return S.all[key] ? list : list.slice(0, Math.max(CAP, bad.length));
  }
  function building() {
    var s = sites[S.site]; if (!s || !s.b[S.building]) return go('site', S.site);
    var all = s.b[S.building].nodes.filter(keep), W = width(), P = dict();
    var edge = all.filter(function (n) { return n.role === 'firewall' || n.role === 'router'; }),
        core = all.filter(function (n) { return n.role === 'core'; }),
        side = all.filter(function (n) { return n.role === 'server' || n.role === 'wireless'; }),
        dist = all.filter(function (n) { return n.role === 'distribution'; }),
        rest = all.filter(function (n) { return n.role === 'access' || n.role === 'other'; });
    var under = dict(), dname = dict(); dist.forEach(function (d) { under[d.id] = []; dname[d.name.toUpperCase().split('.')[0]] = d.id; });
    var loose = [];
    rest.forEach(function (n) { var d = n.closet && dname[String(n.closet).toUpperCase()]; if (d != null) under[d].push(n); else loose.push(n); });
    var blocks = dist.map(function (d) { return { d: d, acc: under[d.id] }; });
    if (loose.length) blocks.push({ d: null, acc: loose });
    var X0 = W < 700 ? 22 : 178, X1 = W - 14, avail = X1 - X0, SL = NW + 10, per = Math.max(1, Math.min(6, Math.floor((avail - 30) / SL))), GUT = X0 - 12;
    var y = 50, svg = sv('svg', { 'class': 'nm-draw', role: 'img', 'aria-label': (S.building ? 'Building ' + S.building : 'The site') + ': its devices in tiers' });
    var gL = sv('g'), gD = sv('g'); svg.appendChild(gL); svg.appendChild(gD);
    var tier = 0, band = 0;                            // band: each row of boxes, top down (a link to the next band goes straight)
    function row(list, label, yy) {
      if (!list.length) return yy;
      var n = list.length, cols = Math.max(1, Math.floor(avail / SL)), r = Math.ceil(n / cols);
      for (var i = 0; i < n; i++) {
        var rr = Math.floor(i / cols), inRow = Math.min(cols, n - rr * cols), x0 = X0 + (avail - inRow * SL) / 2 + SL / 2, yc = yy + rr * (NH + 16);
        dev(gD, x0 + (i % cols) * SL, yc, list[i], P, { closet: null, gapUp: yc - NH / 2 - 8, gapDown: yc + NH / 2 + 8, row: 't' + tier + '.' + rr, crow: -1, band: band + rr });
      }
      band += r;
      if (W >= 700) svg.appendChild(sv('text', { x: 14, y: yy + 4, 'class': 'nm-tier' }, [label.toUpperCase()]));
      tier++;
      return yy + r * (NH + 16) + 34;
    }
    y = row(edge, 'edge', y); y = row(core, 'core', y); y = row(side, 'servers, wireless', y);
    if (blocks.length && W >= 700) svg.appendChild(sv('text', { x: 14, y: y + 4, 'class': 'nm-tier' }, ['CLOSETS']));
    var bx = X0, by = y, rowH = 0, crow = 0, chan = [by - 34], boxes = [];
    blocks.forEach(function (b, bi) {
      var key = S.site + '\u0001' + S.building + '\u0001' + (b.d ? b.d.id : '-'), shown = shownOf(b.acc, key), more = b.acc.length - shown.length;
      var cols = Math.max(1, Math.min(per, shown.length || 1)), rws = Math.ceil(shown.length / cols);
      var w = cols * SL + 24, h = (b.d ? NH + 30 : 10) + rws * (NH + 12) + 30;
      if (bx + w > X1 && bx > X0) { bx = X0; by += rowH + 26; rowH = 0; crow++; chan.push(by - 34); }
      var cband = band + crow;
      gL.appendChild(sv('rect', { x: bx, y: by - 26, width: w, height: h, rx: 12, 'class': 'nm-closet' }));
      gL.appendChild(sv('text', { x: bx + 10, y: by + h - 36, 'class': 'nm-cap' }, [(b.d ? 'closet under ' + (b.d.name.split(/[-_.]/).slice(-1)[0]) : 'no distribution switch') + ' · ' + b.acc.length]));
      if (more > 0 || S.all[key]) {                    // Show all / Show fewer: a button in the closet's caption line
        var bt = sv('g', { 'class': 'nm-more', tabindex: '0', role: 'button', transform: 'translate(' + (bx + w - 10) + ',' + (by + h - 36) + ')' });
        var label = S.all[key] ? 'Show fewer' : 'Show all ' + b.acc.length;
        bt.appendChild(sv('text', { x: 0, y: 0, 'text-anchor': 'end' }, [label]));
        activate(bt, function () { if (S.all[key]) delete S.all[key]; else S.all[key] = 1; render(); });
        gD.appendChild(bt);
      }
      var trunk = bx + 6, cl = { trunk: trunk, top: by - 26 };
      if (b.d) dev(gD, bx + w / 2, by, b.d, P, { closet: cl, gapUp: by - NH / 2 - 8, gapDown: by + NH / 2 + 7, row: 'c' + bi + '.d', crow: crow, band: cband });
      shown.forEach(function (n, i) {
        var yc = by + (b.d ? NH + 26 : 0) + Math.floor(i / cols) * (NH + 12);
        dev(gD, bx + 12 + SL / 2 + (i % cols) * SL, yc, n, P, { closet: cl, gapUp: yc - NH / 2 - 6, gapDown: yc + NH / 2 + 6, row: 'c' + bi + '.' + Math.floor(i / cols), crow: crow, band: cband });
      });
      bx += w + 18; rowH = Math.max(rowH, h);
    });
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + ((blocks.length ? by + rowH : y) + 10));
    function route(a, b) {                             // a path that keeps to the gaps and gutters
      if (a.row === b.row) {
        if (Math.abs(a.x - b.x) <= SL + 1) return 'M' + (Math.min(a.x, b.x) + NW / 2) + ',' + a.y + ' H' + (Math.max(a.x, b.x) - NW / 2);
        return 'M' + a.x + ',' + a.top + ' V' + a.gapUp + ' H' + b.x + ' V' + b.top;
      }
      var up = a.y < b.y ? a : b, lo = a.y < b.y ? b : a;
      if (up.closet && up.closet === lo.closet)            // inside one closet: its trunk
        return 'M' + up.x + ',' + up.bot + ' V' + up.gapDown + ' H' + up.closet.trunk + ' V' + lo.gapUp + ' H' + lo.x + ' V' + lo.top;
      var into = lo.closet ? chan[lo.crow] : lo.gapUp;      // into a closet: the channel above its row of closets, then down
      if (!up.closet && lo.band === up.band + 1)            // the next row down: straight across the gap between them
        return 'M' + up.x + ',' + up.bot + ' V' + into + ' H' + lo.x + ' V' + lo.top;
      return 'M' + up.x + ',' + up.bot + ' V' + up.gapDown + ' H' + GUT + ' V' + into + ' H' + lo.x + ' V' + lo.top;
    }
    var drawn = 0;
    all.forEach(function (n) { (nb[n.id] || []).forEach(function (l) {
      if (l.a !== n.id) return; var a = P[l.a], b = P[l.b]; if (!a || !b || drawn > 2000) return; drawn++;
      gL.appendChild(sv('path', { d: route(a, b), 'class': 'nm-l k-' + stc(l.status) + (/^(10|25|40|100) ?G/i.test(l.speed || '') ? ' fat' : '') }));
    }); });
    var out = 0; all.forEach(function (n) { (nb[n.id] || []).forEach(function (l) { var o = byId[l.a === n.id ? l.b : l.a]; if (o && (o.site !== n.site || (o.building || '') !== (n.building || ''))) out++; }); });
    say((S.building ? 'Building ' + S.building : S.site) + ': ' + all.length + ' devices in tiers' + (out ? '; ' + out + ' link' + (out === 1 ? '' : 's') + ' out of the building (on its devices)' : '') + '. A device opens its links.');
    var leg = el('p', 'nm-leg2', ROLES.slice(0, 7).map(function (r) { return el('span', '', [el('i', 'nm-lg r-' + r), r]); }).concat([el('span', '', [GL.Up + ' up · ' + GL.Warning + ' warning · ' + GL.Down + ' down (red, dashed)'])]));
    body.replaceChildren(el('div', 'nm-drawbox', [svg]), leg);
  }

  /* ---- a Device: its links' interfaces and the neighbours (a neighbour opens its own) ------------------------------- */
  function device() {
    var n = byId[S.device]; if (!n) return go('building', S.site, S.building);
    var ls = (nb[n.id] || []).slice().sort(function (a, b) { return RANK[a.status] - RANK[b.status]; });
    var facts = [['Role', n.role + (n.role_from ? ' (from its ' + n.role_from + ')' : '')], ['IP', n.ip], ['Model', ((n.vendor || '') + ' ' + (n.model || '')).trim()], ['Site', n.site],
      ['Building', n.building], ['Closet', n.closet], ['Response', n.resp], ['Location', n.loc]].filter(function (p) { return p[1]; });
    var dl = el('dl'); facts.forEach(function (p) { dl.appendChild(el('dt', '', [p[0]])); dl.appendChild(el('dd', '', [p[1]])); });
    var head = el('div', 'nm-devhead k-' + stc(n.status), [el('h3', '', [n.name]), el('span', 'nm-st ' + stc(n.status), [GL[n.status] + ' ' + n.status + (n.why ? ' - ' + n.why : '')]), dl]);
    var list = el('ul', 'nm-nbrs');
    ls.forEach(function (l) {
      var mine = l.a === n.id, o = byId[mine ? l.b : l.a]; if (!o) return;
      var b = el('button', 'nm-nb k-' + stc(l.status), [el('span', 'nm-st ' + stc(o.status), [GL[o.status] || '?']), el('span', 'nm-nn', [o.name + (o.site !== n.site ? ' (' + o.site + ')' : '')]),
        el('span', 'nm-ifs', [(mine ? l.ia : l.ib) + ' ↔ ' + (mine ? l.ib : l.ia) + (l.speed ? ' · ' + l.speed : '') + (l['in'] != null ? ' · in ' + l['in'] + '% out ' + l.out + '%' : '') + (l.status !== 'Up' ? ' · ' + l.status : '')])], { type: 'button' });
      b.addEventListener('click', function () { go('device', o.site, o.building || '', o.id); });
      list.appendChild(el('li', '', [b]));
    });
    say(n.name + ': its ' + ls.length + ' link' + (ls.length === 1 ? '' : 's') + ' - the interfaces on its links (SolarWinds’ topology), the neighbour at the other end.');
    body.replaceChildren(el('div', 'nm-device', [head, el('h4', '', ['Its links’ interfaces']), ls.length ? list : el('p', 'nm-empty', ['No link of this device is in SolarWinds’ topology.'])]));
  }

  function render() {
    if (!D) return;
    crumbs();
    var c = { down: 0, warning: 0, chg: 0 };
    D.nodes.forEach(function (n) { if (n.status === 'Down') c.down++; else if (n.status === 'Warning') c.warning++; if (n.new || n.gone || n.was) c.chg++; });
    [].forEach.call(box.querySelectorAll('.nm-c'), function (b) { var k = b.getAttribute('data-c'); b.querySelector('.nm-n').textContent = c[k]; b.setAttribute('aria-pressed', String(!!S.chip[k])); });
    if (S.level === 'world') world(); else if (S.level === 'site') site(); else if (S.level === 'building') building(); else device();
  }

  /* ---- the search: sites, buildings, devices and IPs, grouped; arrow keys, Enter, Escape -------------------------------- */
  var hits = [], cur = -1;
  function find(v) {
    v = v.trim().toLowerCase(); hits = []; if (!v || !D) return;
    var g = { s: [], b: [], d: [] };
    order.forEach(function (s) {
      if (g.s.length < 5 && s.name.toLowerCase().indexOf(v) >= 0) g.s.push({ t: s.name, sub: s.nodes.length + ' devices', go: ['site', s.name] });
      s.bo.forEach(function (bn) { if (bn && g.b.length < 5 && (s.name + ' ' + bn).toLowerCase().indexOf(v) >= 0) g.b.push({ t: s.name + ' › ' + bn, sub: s.b[bn].nodes.length + ' devices', go: ['building', s.name, bn] }); });
    });
    for (var i = 0; i < D.nodes.length && g.d.length < 6; i++) {
      var n = D.nodes[i]; if (n.gone) continue;
      if (n.name.toLowerCase().indexOf(v) >= 0 || (n.ip && n.ip.indexOf(v) === 0)) g.d.push({ t: n.name, sub: n.role + (n.ip ? ' · ' + n.ip : ''), go: ['device', n.site, n.building || '', n.id] });
    }
    [['Sites', g.s], ['Buildings', g.b], ['Devices', g.d]].forEach(function (x) { x[1].forEach(function (h) { h.grp = x[0]; hits.push(h); }); });
  }
  function drawSugg() {
    sugg.replaceChildren(); var last = '';
    hits.forEach(function (h, i) {
      if (h.grp !== last) { sugg.appendChild(el('div', 'nm-sg', [h.grp], { role: 'presentation' })); last = h.grp; }
      var o = el('div', 'nm-so' + (i === cur ? ' on' : ''), [el('span', '', [h.t]), el('small', '', [h.sub])], { role: 'option', id: 'nm-o' + i, 'aria-selected': String(i === cur) });
      o.addEventListener('mousedown', function (ev) { ev.preventDefault(); pick(i); });
      sugg.appendChild(o);
    });
    if (!hits.length && q.value.trim()) sugg.appendChild(el('div', 'nm-sg', ['Nothing matches'], { role: 'presentation' }));
    sugg.hidden = !q.value.trim(); q.setAttribute('aria-expanded', String(!sugg.hidden));
    if (cur >= 0) q.setAttribute('aria-activedescendant', 'nm-o' + cur); else q.removeAttribute('aria-activedescendant');
  }
  function pick(i) {
    var h = hits[i]; if (!h) return;
    sugg.hidden = true; q.setAttribute('aria-expanded', 'false'); q.value = '';
    if (h.go[0] === 'site' && S.world === 'globe' && S.level === 'world' && window.ngFly && own(D.geo, h.go[1]) && window.ngFly(h.go[1])) return;   // a placed site: the globe flies there
    go.apply(null, h.go);
  }
  q.addEventListener('input', function () { cur = -1; find(q.value); drawSugg(); });
  q.addEventListener('keydown', function (ev) {
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') { ev.preventDefault(); if (!hits.length) return; cur = (cur + (ev.key === 'ArrowDown' ? 1 : -1) + hits.length) % hits.length; drawSugg(); }
    else if (ev.key === 'Enter') { ev.preventDefault(); pick(cur >= 0 ? cur : 0); }
    else if (ev.key === 'Escape') { sugg.hidden = true; q.setAttribute('aria-expanded', 'false'); }
  });
  q.addEventListener('blur', function () { setTimeout(function () { sugg.hidden = true; q.setAttribute('aria-expanded', 'false'); }, 150); });

  /* ---- the chips (Down jumps to the one site that has it), Filters, Tiles / Map ------------------------------------------ */
  [].forEach.call(box.querySelectorAll('.nm-c'), function (b) {
    b.addEventListener('click', function () {
      var k = b.getAttribute('data-c'); S.chip[k] = !S.chip[k];
      if (k === 'down' && S.chip.down && S.level === 'world') {
        var ds = order.filter(function (s) { return s.down; });
        if (ds.length === 1) return go('site', ds[0].name);
      }
      render();
    });
  });
  fbtn.addEventListener('click', function () { fp.hidden = !fp.hidden; fbtn.setAttribute('aria-expanded', String(!fp.hidden)); });
  document.addEventListener('click', function (ev) { if (!fp.hidden && !fp.contains(ev.target) && !fbtn.contains(ev.target)) { fp.hidden = true; fbtn.setAttribute('aria-expanded', 'false'); } });
  var vsel = box.querySelector('.nm-vendor'), rbox = box.querySelector('.nm-roles');
  function filters() {
    var n = Object.keys(S.roles).length + (S.vendor ? 1 : 0), bd = fbtn.querySelector('.nm-bdg');
    bd.textContent = n; bd.hidden = !n; box.querySelector('.nm-fon').textContent = n ? n + ' filter' + (n === 1 ? '' : 's') + ' on' : 'none on';
  }
  function filterLists() {
    var have = dict(), vend = dict(); D.nodes.forEach(function (n) { have[n.role] = 1; if (n.vendor) vend[n.vendor] = 1; });
    rbox.replaceChildren();
    ROLES.filter(function (r) { return have[r]; }).forEach(function (r) {
      var c = el('input', '', [], { type: 'checkbox', value: r }); c.checked = !!S.roles[r];
      c.addEventListener('change', function () { if (c.checked) S.roles[r] = 1; else delete S.roles[r]; filters(); render(); });
      rbox.appendChild(el('label', '', [c, r]));
    });
    var keepv = S.vendor; vsel.replaceChildren(el('option', '', ['All vendors'], { value: '' }));
    Object.keys(vend).sort().forEach(function (v) { vsel.appendChild(el('option', '', [v], { value: v })); });
    vsel.value = vend[keepv] ? keepv : ''; S.vendor = vsel.value; filters();
  }
  vsel.addEventListener('change', function () { S.vendor = vsel.value; filters(); render(); });
  box.querySelector('.nm-clear').addEventListener('click', function () { S.roles = dict(); S.vendor = ''; vsel.value = ''; filterLists(); render(); });
  [].forEach.call(box.querySelectorAll('.nm-wv'), function (b) {
    b.addEventListener('click', function () {
      S.world = b.getAttribute('data-w');
      try { localStorage.setItem(KEY, S.world); } catch (e) { /* this page only */ }
      [].forEach.call(box.querySelectorAll('.nm-wv'), function (x) { x.setAttribute('aria-pressed', String(x === b)); });
      go('world');
    });
  });
  var rt = 0;
  window.addEventListener('resize', function () { clearTimeout(rt); rt = setTimeout(function () { if (S.level === 'site' || S.level === 'building') render(); }, 150); });

  /* ---- the data: the first, and each new one (the portal's live page): the level and the choices stay ----------------- */
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
    D = data; index();
    if (!old) {                                          // the first: the globe when most sites are placed, else the tiles
      var real = order.filter(function (s) { return !s.none; }), placed = real.filter(function (s) { return own(D.geo, s.name); }).length, kept = '';
      try { kept = localStorage.getItem(KEY) || ''; } catch (e) { /* the default */ }
      S.world = kept === 'tiles' || kept === 'globe' ? kept : (real.length && placed * 2 >= real.length ? 'globe' : 'tiles');
      if (!placed) S.world = 'tiles';
      [].forEach.call(box.querySelectorAll('.nm-wv'), function (x) { x.setAttribute('aria-pressed', String(x.getAttribute('data-w') === S.world)); x.disabled = !placed && x.getAttribute('data-w') === 'globe'; });
    }
    if (S.level !== 'world' && !sites[S.site]) S.level = 'world';
    filterLists();
    hooks.forEach(function (f) { f(D, changes); });
    go(S.level, S.site, S.building, S.device);
    if (changes.length && !still()) setTimeout(function () { D.nodes.forEach(function (n) { delete n.flash; }); D.links.forEach(function (l) { delete l.flash; }); render(); }, 4000);
    return changes;
  }
  // the globe: a site picked there opens here
  window.nmOpenSite = function (s) { go('site', s); };
  window.nmShown = function () { render(); };
  window.NM = { load: load, on: function (f) { hooks.push(f); if (D) f(D, []); }, data: function () { return D; }, still: still };
  window.nmTest = { S: S, go: go, shownOf: shownOf, sites: function () { return order.map(function (s) { return s.name; }); }, render: render };
  var src = document.getElementById('netmap-data');
  if (src) { try { load(JSON.parse(src.textContent)); } catch (e) { /* no data in the page: netmap-live.js brings it */ } }
})();
