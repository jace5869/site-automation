/* maplabel.js (1.102.0) - shared by the AD report's replication map (roles/ad_report/files/web/admap.js) and the network
   map's Region view (netregion.js): the curve between two places, and the labels - right of the dot, else left, below,
   above, then further out all round it; never on a dot or on another label, never out of the drawing; a label that finds
   no free place is left off (its name on hover and focus), unless forced - then it may cover another label, never a dot;
   a thin leader when it lands away from its dot. A label's width as this browser draws it (a wider font than ours must not
   overlap). No inline script or style, no HTML from strings. */
(function () {
  var NS = 'http://www.w3.org/2000/svg';
  function sv(t, at, kids) { var e = document.createElementNS(NS, t); for (var k in at || {}) if (at[k] != null) e.setAttribute(k, at[k]); (kids || []).forEach(function (c) { e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); }); return e; }
  // a quadratic curve from a to b bowed k of its length to one side (k < 0: the other side) -> {d, c (its control point), m (its middle)}
  function curve(a, b, k) {
    var mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2, dx = b[0] - a[0], dy = b[1] - a[1], cx = mx - dy * k, cy = my + dx * k;
    return { d: 'M' + a[0].toFixed(1) + ',' + a[1].toFixed(1) + ' Q' + cx.toFixed(1) + ',' + cy.toFixed(1) + ' ' + b[0].toFixed(1) + ',' + b[1].toFixed(1),
      c: [cx, cy], m: [(mx + cx) / 2, (my + cy) / 2] };
  }
  // the labels of one drawing: svg, its size W x H; o.probe: the class of the group a label is drawn in (its font), o.sub: a
  // second line's class, o.lead: the leader's class, o.line: the line height
  function labels(svg, W, H, o) {
    o = o || {};
    var LH = o.line || 13, taken = [], dots = [];
    var probe = sv('g', { 'class': o.probe || null, visibility: 'hidden', 'aria-hidden': 'true' }); svg.appendChild(probe);
    // an unseen drawing (no layout yet) falls back to a width wide enough for a wide bold sans
    function textW(lines) {
      return Math.max.apply(0, lines.map(function (t, i) {
        var e = sv('text', { 'class': i ? (o.sub || 'sub') : null }, [t]), n; probe.appendChild(e);
        try { n = e.getComputedTextLength(); } catch (err) { n = 0; }
        e.remove(); return n > 0 ? Math.ceil(n) + 1 : t.length * 7.4;
      }));
    }
    function dot(x, y, r) { var b = [x - r - 2, y - r - 2, x + r + 2, y + r + 2]; taken.push(b); dots.push(b); }
    // g: where the text goes; x, y: the first try (right of the dot: its centre + r + 6); lines: the name, then more;
    // force: placed even with no free place (never on a dot); end: only right-aligned at x; r: the dot's radius
    // cy: the dot's centre (the leader starts there; 1.102.0 review), else y + 1 (the AD map's labels sit 1 px above it)
    function place(g, x, y, lines, force, end, r, cy) {
      var w = textW(lines), h = LH * lines.length, rr = r || 0, cx = x - rr - 6;
      var tries = end ? [[x - w, y]] : [[x, y], [cx - rr - 6 - w, y], [cx - w / 2, y + rr + 16], [cx - w / 2, y - rr - 6 - h],
        [x, y + rr + 14], [x, y - rr - 10], [cx - rr - 6 - w, y + rr + 14], [cx - rr - 6 - w, y - rr - 10], [x + 24, y + 26], [x + 24, y - 26]]
        .concat([0, 1, 2, 3, 4, 5, 6, 7].map(function (k) {             // further out, all round the dot (a busy hub's label)
          var a = k * Math.PI / 4, dx = Math.cos(a), dy = Math.sin(a), d = rr + 40;
          return [cx + dx * d - (dx < -0.3 ? w : Math.abs(dx) <= 0.3 ? w / 2 : 0), y + dy * d]; }));
      var hits = function (q, list) { var b = [q[0] - 2, q[1] - 12, q[0] + w + 2, q[1] + h - 9];
        return list.filter(function (t) { return b[0] < t[2] && b[2] > t[0] && b[1] < t[3] && b[3] > t[1]; }).length; };
      var al = end ? ['end'] : ['start', 'end', 'middle', 'middle', 'start', 'start', 'end', 'end', 'start', 'start']
        .concat([0, 1, 2, 3, 4, 5, 6, 7].map(function (k) { var dx = Math.cos(k * Math.PI / 4); return dx < -0.3 ? 'end' : Math.abs(dx) <= 0.3 ? 'middle' : 'start'; }));
      var fit = tries.map(function (q, k) { var f = [Math.max(4, Math.min(q[0], W - w - 4)), Math.max(14, Math.min(q[1], H - h))]; f.al = al[k]; return f; });
      var pick = fit.filter(function (q) { return !hits(q, taken); })[0];
      if (!pick && force) pick = fit.filter(function (q) { return !hits(q, dots); }).sort(function (p, q) { return hits(p, taken) - hits(q, taken); })[0];
      if (!pick) return false;                           // never on a dot: left off (its name on hover and keyboard focus)
      taken.push([pick[0] - 2, pick[1] - 12, pick[0] + w + 2, pick[1] + h - 9]);
      // the leader is decided by the name (the first line): a second line beside the dot does not make the name near it
      var w0 = lines.length > 1 ? textW(lines.slice(0, 1)) : w, x0 = pick.al === 'end' ? pick[0] + w - w0 : pick.al === 'middle' ? pick[0] + (w - w0) / 2 : pick[0];
      var bx = [x0 - 2, pick[1] - 12, x0 + w0 + 2, pick[1] + 4], dy0 = cy != null ? cy : y + 1;
      var nx = Math.max(bx[0], Math.min(cx, bx[2])), ny = Math.max(bx[1], Math.min(dy0, bx[3])), dd = Math.hypot(nx - cx, ny - dy0) || 1;
      if (!end && dd > rr + 10) {                         // not next to its dot (a far place, or pushed in from the edge): a thin leader to it
        g.appendChild(sv('line', { x1: (cx + (nx - cx) / dd * (rr + 1)).toFixed(1), y1: (dy0 + (ny - dy0) / dd * (rr + 1)).toFixed(1), x2: nx.toFixed(1), y2: ny.toFixed(1), 'class': o.lead || null }));
      }
      // anchored on the side that faces the dot: the gap to it is exact whatever the text's real width
      var ax = pick.al === 'end' ? pick[0] + w : pick.al === 'middle' ? pick[0] + w / 2 : pick[0];
      lines.forEach(function (t, i) { g.appendChild(sv('text', { x: ax, y: pick[1] + i * LH, 'text-anchor': pick.al, 'class': i ? (o.sub || 'sub') : null }, [t])); });
      return true;
    }
    return { taken: taken, dots: dots, dot: dot, textW: textW, place: place, done: function () { probe.remove(); } };
  }
  window.SiteMap = { curve: curve, labels: labels, sv: sv };
})();
