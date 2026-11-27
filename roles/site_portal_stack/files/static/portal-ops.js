// 1.136.1: the Actions page's Ops center section, in the viewer's look (html[data-skin], static/portal-looks.css) - the wall's areas
// in short from /api/wall/summary (the wall's own rule: green only when every source of the area is current; a stale one is grey,
// never green). Mode comes from portal_actions_ops_center (data-mode): line (default, one clickable status strip) or full (status
// tiles with counts and limit charts; Strata spotlight stage; Metro turning tiles; Atlas plain status tiles, no island map).
// Every limit chart has its bands, its labelled dashed limits and one sentence. No inline script or style; no HTML from strings.
(function () {
  "use strict";
  var sec = document.getElementById("ops");
  if (!sec || !window.fetch) return;
  var SRC = sec.getAttribute("data-src"), WALL = sec.getAttribute("data-wall"), TITLE = sec.getAttribute("data-title") || "Ops center";
  var MODE = sec.getAttribute("data-mode") || "line";
  var de = document.documentElement, NS = "http://www.w3.org/2000/svg";
  var D = null, D0 = null, SKEW = 0, FAIL = 0, SPOT = 0, SPOT_LEFT = 8, PAUSE = 0, FLIP = 0, MA_ALT = false;
  var EVERY = 8;                                  // seconds: the spotlight's step and live tile updates

  function h(tag, cls, kids, at) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    for (var k in at || {}) if (at[k] != null && at[k] !== false) e.setAttribute(k, at[k] === true ? "" : at[k]);
    (kids || []).forEach(function (x) { if (x == null || x === "") return; e.appendChild(typeof x === "string" || typeof x === "number" ? document.createTextNode(String(x)) : x); });
    return e;
  }
  function s(tag, at, kids) {
    var e = document.createElementNS(NS, tag);
    for (var k in at || {}) if (at[k] != null) e.setAttribute(k, at[k]);
    (kids || []).forEach(function (x) { if (x != null) e.appendChild(typeof x === "string" ? document.createTextNode(x) : x); });
    return e;
  }
  function clear(e) { while (e.firstChild) e.removeChild(e.firstChild); return e; }
  function skin() { return de.getAttribute("data-skin") || "metro-atlas"; }
  function moves() { return de.classList.contains("moves"); }
  function now() { return Date.now() + SKEW; }

  var WORD = {crit: "Critical", warn: "Warning", ok: "OK", stale: "Not current", off: "Not on the wall yet"};
  var SHORT = {crit: "Crit", warn: "Warn", ok: "OK", stale: "Stale", off: "Off"};
  var ICON = {
    crit: "M8 2.8h8l5.2 5.2v8L16 21.2H8L2.8 16V8zM12 7.5v5.5M12 16.3h.01",
    warn: "M12 3.5L2.5 20h19zM12 10v4.5M12 17.2h.01",
    ok: "M4.5 12.5l5 5L19.5 7",
    stale: "M12 3a9 9 0 1 0 .01 0zM9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .8-1 1.5v.7M12 17h.01",
    off: "M12 3a9 9 0 1 0 .01 0zM7.5 12h9"
  };
  var AREA = {
    storage: "M4 6c0 1.5 3.6 2.8 8 2.8s8-1.3 8-2.8-3.6-2.8-8-2.8S4 4.5 4 6zM4 6v12c0 1.5 3.6 2.8 8 2.8s8-1.3 8-2.8V6M4 12c0 1.5 3.6 2.8 8 2.8s8-1.3 8-2.8",
    vmware: "M3 4h18v7H3zM3 13h18v7H3zM7 7.5h.01M7 16.5h.01M11 7.5h6M11 16.5h6",
    vdi: "M3 4h18v12H3zM8 20h8M12 16v4",
    ad: "M9 4.5a3.5 3.5 0 1 0 .01 0zM2.5 20a6.5 6.5 0 0 1 13 0M15.5 4.6a3.5 3.5 0 0 1 0 6.8M17.5 14a6.5 6.5 0 0 1 4 6",
    tickets: "M3.5 6.5h17v3.5a2 2 0 0 0 0 4v3.5h-17V14a2 2 0 0 0 0-4zM14.5 6.5v2M14.5 11v2M14.5 15.5v2",
    network: "M9 3h6v5H9zM3 16h6v5H3zM15 16h6v5h-6zM12 8v4M6 16v-4h12v4"
  };
  function ic(d, cls) {
    return s("svg", {viewBox: "0 0 24 24", "aria-hidden": "true", class: cls || null, fill: "none", stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round"}, [s("path", {d: d})]);
  }
  function word(st, txt) { return h("span", "ow " + st, [ic(ICON[st] || ICON.off), txt || WORD[st] || st]); }
  function hm(t) {
    try { return new Intl.DateTimeFormat("en-GB", {hour: "2-digit", minute: "2-digit", timeZone: (D && D.zone) || "UTC"}).format(new Date(t)); }
    catch (e) { return new Date(t).toISOString().slice(11, 16); }
  }
  function ago(t) { var m = Math.max(0, Math.round((now() - t) / 60000)); return m < 1 ? "under a minute" : m < 90 ? m + " min" : Math.round(m / 60) + " h"; }
  function num(v) { return (Math.round(v * 10) / 10).toString(); }

  // ---- the limit chart: one value against its warn / crit limits (0-100 %)
  // the metric's state as the server said it (the wall's rule), never better: anything but ok / warn / crit is not current
  function zone(m) {
    if (m.state !== "ok" && m.state !== "warn" && m.state !== "crit") return "stale";
    if (m.forced) return m.state;
    var z = m.crit != null && m.v >= m.crit ? "crit" : m.warn != null && m.v >= m.warn ? "warn" : "ok";
    return z === "ok" && m.state !== "ok" ? m.state : z;
  }
  function pts(d) { d = Math.round(Math.abs(d) * 10) / 10; return d + (d === 1 ? " pt" : " pts"); }
  function sentence(m) {
    var z = zone(m), v = num(m.v) + m.unit, wl = m.crit == null ? "high" : "warn";
    if (z === "stale") return [h("b", "z-stale", [v + " · not current"]), " · last known value, its source is late or failing"];
    if (m.forced) return [h("b", "z-" + z, [v + " · " + WORD[z].toUpperCase()]), " · " + m.forced];
    if (z === "crit") return [h("b", "z-crit", [v + " · CRITICAL"]), " · " + pts(m.v - m.crit) + " over crit " + num(m.crit)];
    if (z === "warn") return [h("b", "z-warn", [v + " · " + pts(m.v - m.warn) + " over " + wl + " " + num(m.warn)]), m.crit != null ? " · crit at " + num(m.crit) : ""];
    var t = m.warn != null ? m.warn : m.crit;
    return [h("b", "", [v + " · OK"]), " · " + pts(t - m.v) + " below " + (m.warn != null ? wl + " " + num(m.warn) : "crit " + num(m.crit))];
  }
  function chart(m, big) {
    // the bands and the value in an SVG that stretches; the limit labels as text over it (never stretched): warn above the bar,
    // crit under it - never on top of each other, whatever the limits
    var W = 300, bh = big ? 20 : 16, X = function (v) { return Math.max(0, Math.min(100, v)) * W / 100; };
    var kids = [s("rect", {class: "tr", x: 0, y: 0, width: W, height: bh})];
    if (m.warn != null) kids.push(s("rect", {class: "bw", x: X(m.warn), y: 0, width: X(m.crit != null ? m.crit : 100) - X(m.warn), height: bh}));
    if (m.crit != null) kids.push(s("rect", {class: "bc", x: X(m.crit), y: 0, width: W - X(m.crit), height: bh}));
    var vy = bh * 0.25, vh = bh * 0.5;
    if (zone(m) === "stale") kids.push(s("rect", {class: "vs", x: 0, y: vy, width: X(m.v), height: vh}));
    else {
      var w0 = m.warn != null ? m.warn : 101, c0 = m.crit != null ? m.crit : 101;
      kids.push(s("rect", {class: "vo", x: 0, y: vy, width: X(Math.min(m.v, w0)), height: vh}));
      if (m.v > w0) kids.push(s("rect", {class: "vw", x: X(w0), y: vy, width: X(Math.min(m.v, c0)) - X(w0), height: vh}));
      if (m.v > c0) kids.push(s("rect", {class: "vc", x: X(c0), y: vy, width: X(m.v) - X(c0), height: vh}));
    }
    var labels = [];
    var lim = function (v, cls, tcls, label, below) {
      kids.push(s("line", {class: cls, x1: X(v), x2: X(v), y1: 0, y2: bh, "vector-effect": "non-scaling-stroke"}));
      var t = h("span", "olb " + tcls + (below ? " below" : "") + (v > 70 ? " end" : ""), [label]);
      t.style.left = Math.max(0, Math.min(100, v)) + "%";
      labels.push(t);
    };
    if (m.warn != null) lim(m.warn, "lw", "olw", (m.crit == null ? "high " : "warn ") + num(m.warn), false);
    if (m.crit != null) lim(m.crit, "lcr", "olx", "crit " + num(m.crit), true);
    var svg = s("svg", {viewBox: "0 0 " + W + " " + bh, preserveAspectRatio: "none", role: "img", class: big ? "big" : null,
                        "aria-label": m.label + " " + num(m.v) + m.unit + (m.warn != null ? ", warning at " + num(m.warn) : "") + (m.crit != null ? ", critical at " + num(m.crit) : "")}, kids);
    return h("div", "olc", [h("div", "lc-b" + (m.crit != null ? " lc2" : ""), [svg].concat(labels)), h("p", "", sentence(m))]);
  }

  // ---- ordering: criticals first, then warnings, then stale, then OK, then off
  var RANK = {crit: 4, warn: 3, stale: 2, ok: 1, off: 0};
  function cmp(x, y) {
    return (RANK[y.state] - RANK[x.state]) || ((y.crit || 0) - (x.crit || 0)) || ((y.warn || 0) - (x.warn || 0));
  }
  function ordered() { return D.areas.slice().sort(cmp); }
  function spotList() { return D.areas.filter(function (a) { return a.state !== "off"; }).sort(cmp); }

  // ---- one-line Ops center strip (portal_actions_ops_center: line, the default)
  function stripArea(a, extraCls) {
    var href = WALL + (a.state === "off" ? "" : "?area=" + encodeURIComponent(a.id));
    var cnt = a.state === "crit" ? (a.crit || 0) : a.state === "warn" ? (a.warn || 0) : a.state === "ok" ? 0 : null;
    var parts = [ic(ICON[a.state] || ICON.off), h("span", "oa-name", [a.short || a.name])];
    if (cnt != null) parts.push(h("b", "oa-cnt", [String(cnt)]));
    parts.push(h("span", "oa-st", [SHORT[a.state] || a.state]));
    var tip = a.name + ": " + WORD[a.state] + (a.crit || a.warn ? " (" + (a.crit || 0) + " critical, " + (a.warn || 0) + " warning)" : "") + " - open on wall";
    return h("a", "ops-area s-" + a.state + (extraCls ? " " + extraCls : ""), parts, {href: href, title: tip, "data-id": a.id});
  }
  function renderLine() {
    var areas = ordered();
    var crits = areas.filter(function (a) { return a.state === "crit"; });
    var warns = areas.filter(function (a) { return a.state === "warn"; });
    var stales = areas.filter(function (a) { return a.state === "stale"; });
    var oks = areas.filter(function (a) { return a.state === "ok"; });
    var overall = crits.length ? "crit" : warns.length ? "warn" : stales.length ? "stale" : oks.length ? "ok" : "off";
    var sumTxt = crits.length ? crits.length + (crits.length === 1 ? " area critical" : " areas critical")
      : warns.length ? warns.length + (warns.length === 1 ? " area warning" : " areas warning")
      : stales.length ? stales.length + (stales.length === 1 ? " area not current" : " areas not current")
      : oks.length ? (oks.length === 1 ? "1 area OK" : "All " + oks.length + " areas OK")
      : "Not on the wall yet";
    var pills = [];
    crits.forEach(function (a) { pills.push(stripArea(a)); });
    warns.forEach(function (a) { pills.push(stripArea(a)); });
    oks.forEach(function (a) { pills.push(stripArea(a, "ok-tile")); });
    if (oks.length && (crits.length || warns.length || stales.length)) {
      pills.push(h("span", "ops-ok-sum", [ic(ICON.ok), oks.length + (oks.length === 1 ? " area OK" : " areas OK")]));
    }
    if (stales.length && (crits.length || warns.length)) {
      pills.push(h("span", "ops-ok-sum is-stale", [ic(ICON.stale), stales.length + " not current"]));
    }
    var read = D.now ? "live · read " + hm(D.now * 1000) : "";
    var late = D.now && now() - D.now * 1000 > (D.late || 180) * 1000;
    var kids = [
      h("div", "ops-id", [h("h2", "ops-site", [TITLE], {id: "ops-h"})]),
      h("span", "ops-div", ["-"], {"aria-hidden": "true"}),
      word(overall, sumTxt)
    ];
    if (pills.length) kids.push(h("div", "ops-areas", pills, {role: "group", "aria-label": "Area status counts"}));
    if (read) kids.push(h("span", "ma-live", [h("i"), (late ? "LATE · " : "") + read], {"aria-hidden": "true"}));
    kids.push(h("a", "ops-open", ["Open wall ›"], {href: WALL, title: "Open " + TITLE + (read ? " (" + read + ")" : "")}));
    sec.appendChild(h("div", "ops-strip s-" + overall, kids));
  }

  // ---- full mode parts (portal_actions_ops_center: full)
  function lamps(a) {
    var cur = a.state === "crit" || a.state === "warn";                       // a stale area's counts are never lit
    var on = function (k, n) { return h("span", "olm " + k + (n && cur ? " on" : ""), [h("i"), (cur ? n || 0 : "–") + " " + (k === "crit" ? "crit" : "warn")]); };
    var okLamp = h("span", "olm ok" + (a.state === "ok" ? " on" : ""), [h("i"), a.state === "stale" ? "not current" : "OK"]);
    if (a.state === "stale") okLamp.className = "olm stale on";
    return h("div", "ot-lamps", [okLamp, on("warn", a.warn), on("crit", a.crit)], {"aria-hidden": "true"});
  }
  function back(a) {
    var rows = (a.worst || []).map(function (c) { return h("div", "r", [h("span", "", [c.t]), h("b", "", [(c.sev === "crit" ? "CRIT" : "WARN") + (c.start ? " · " + ago(c.start * 1000) : "")])]); });
    if (!rows.length) rows = [h("div", "r", [h("span", "", [a.state === "ok" ? "Nothing open" : a.state === "off" ? "Not on the wall yet" : "No current data"]),
                                             h("b", "", [a.asof ? "read " + hm(a.asof * 1000) : ""])])];
    return h("div", "ot-back", [h("h4", "", [a.name + " · worst first"])].concat(rows), {"aria-hidden": "true"});
  }
  function tileCounts(a) {
    if (a.state === "off") return null;
    if (a.state === "stale") return h("p", "ot-counts", ["Not current · grey, never green"]);
    if (a.state === "ok") return h("p", "ot-counts", ["0 critical · 0 warning · OK"]);
    return h("p", "ot-counts", ["Crit " + (a.crit || 0) + " · Warn " + (a.warn || 0)]);
  }
  function tile(a) {
    var href = WALL + (a.state === "off" ? "" : "?area=" + encodeURIComponent(a.id));
    var big = a.big ? h("div", "ot-big", [h("b", "", [a.big]), h("span", "", [a.unit])]) : a.state === "off" ? null : h("div", "ot-big", [h("b", "", ["–"]), h("span", "", ["no value read"])]);
    var altNote = a.worst && a.worst[0] ? "Worst: " + a.worst[0].t : a.asof ? "Read " + hm(a.asof * 1000) : a.note || "";
    var noteEl = a.note ? h("p", "ot-note", [a.note], {"data-a": a.note, "data-b": altNote || a.note}) : null;
    var face = h("div", "ot-face", [
      h("div", "ot-h", [ic(AREA[a.id] || AREA.network), h("span", "", [a.short || a.name]), word(a.state)]),
      big,
      tileCounts(a),
      noteEl,
      a.metric ? chart(a.metric) : null,
      a.state === "off" ? h("p", "ot-why", ["No live source on the wall yet - grey, never a guess."]) : a.why ? h("p", "ot-why", ["Why grey: " + a.why]) : (a.state !== "off" && (a.crit || a.warn) && a.worst && a.worst[0] ? h("p", "ot-why", ["Worst: " + a.worst[0].t]) : null),
      a.state === "off" ? null : lamps(a),
      h("span", "ot-cue", [a.state === "off" ? "Open the wall ›" : "Open on the wall ›"])
    ]);
    var label = (a.name + ": " + WORD[a.state]) + (a.crit || a.warn ? ", " + a.crit + " critical, " + a.warn + " warning" : "") + ". Opens the wall.";
    return h("li", "ot s-" + a.state, [h("a", "", [face, skin() === "metro" ? back(a) : null], {href: href, "aria-label": label})], {"data-id": a.id});
  }
  function spotlight() {
    var list = spotList();
    if (!list.length) return null;
    SPOT = SPOT % list.length;
    var a = list[SPOT];
    var paused = PAUSE > now();
    var step = h("span", "sp-step", [
      h("span", "", [list.length > 1 ? (SPOT + 1) + " of " + list.length + " · " + (paused ? "paused" : moves() ? "next in " + SPOT_LEFT + " s" : "press Next") : "1 of 1"]),
      list.length > 1 ? h("button", "", ["‹ Back"], {type: "button", "data-sp": "-1", title: "The area before"}) : null,
      list.length > 1 ? h("button", "", ["Next ›"], {type: "button", "data-sp": "1", title: "The next area"}) : null]);
    return h("div", "ops-spot s-" + a.state, [
      h("div", "sp-lab", [ic(ICON[a.state] || ICON.off), "Spotlight · " + a.name + " · worst first, every " + EVERY + " s", step]),
      h("div", "sp-main", [h("h3", "", [ic(AREA[a.id] || AREA.network), a.name, word(a.state)]),
        a.big ? h("div", "ot-big", [h("b", "", [a.big]), h("span", "", [a.unit])]) : null,
        h("p", "ot-note", [a.worst && a.worst[0] ? a.worst[0].t + (a.worst[0].start ? " · since " + hm(a.worst[0].start * 1000) : "") : a.why || a.note || "Nothing open"])]),
      h("div", "sp-chart", [a.metric ? chart(a.metric, true) : h("p", "ot-note", ["No limit chart: this area has no value with a limit."])]),
      h("div", "sp-side", [lamps(a), h("a", "btn", ["Inspect " + (a.short || a.name) + " ›"], {href: WALL + "?area=" + encodeURIComponent(a.id)})])
    ], {"aria-live": "off", "data-id": a.id});
  }
  function summaryLine() {
    var need = D.areas.filter(function (a) { return a.state === "crit" || a.state === "warn"; });
    var n = {ok: 0, warn: 0, crit: 0, stale: 0};
    D.areas.forEach(function (a) { if (n[a.state] != null) n[a.state]++; });
    var say = need.length ? (need.length === 1 ? "1 area needs a look." : need.length + " areas need a look.")
      : n.stale && n.ok ? n.ok + (n.ok === 1 ? " area OK, " : " areas OK, ") + n.stale + " not current - grey, never green."
      : n.stale ? "No area current enough to say OK." : n.ok ? "All areas on the wall are OK." : "No area on the wall yet.";
    var parts = [h("b", "", [say])];
    need.forEach(function (a) { parts.push(h("span", "", [a.name + ": " + [a.crit ? a.crit + " critical" : "", a.warn ? a.warn + " warning" : ""].filter(Boolean).join(", ")])); });
    var lp = h("span", "lamps", []);
    [["crit", "critical"], ["warn", "warning"], ["ok", "OK"], ["stale", "not current"]].forEach(function (k) {
      if (n[k[0]]) lp.appendChild(h("span", "ow " + k[0], [ic(ICON[k[0]]), n[k[0]] + (n[k[0]] === 1 ? " area " : " areas ") + k[1]]));
    });
    parts.push(lp);
    return h("p", "ops-say", parts);
  }
  function renderFull(lk) {
    var hint = lk === "strata" ? "spotlight steps every " + EVERY + " s · click an area for its wall"
      : lk === "metro" ? (moves() ? "tiles turn over every " + EVERY + " s to their worst items · " : "") + "click a tile for its wall"
      : "plain status tiles with counts · click an area for its wall";
    var read = D.now ? "live · read " + hm(D.now * 1000) : "";
    var late = D.now && now() - D.now * 1000 > (D.late || 180) * 1000;
    sec.appendChild(h("h2", "ops-h", [TITLE, h("span", "count", [D.areas.length + " areas"]),
      h("span", "ma-live", [h("i"), "live"], {"aria-hidden": "true"}),
      h("span", "hint", [(late ? "LATE - " : "") + read + (read ? " · " : "") + hint])], {id: "ops-h"}));
    sec.appendChild(summaryLine());
    if (lk === "strata") { var sp = spotlight(); if (sp) sec.appendChild(sp); }
    var spot = lk === "strata" ? spotList()[SPOT] : null;
    var ol = h("ol", "ops-tiles", ordered().map(function (a) { var li = tile(a); if (spot && spot.id === a.id) li.classList.add("on-spot"); return li; }));
    sec.appendChild(ol);
    sec.appendChild(h("p", "ops-foot", ["Grey = no current data, never green: LATE after " + Math.round((D.late || 180) / 60) + " min, not current after " + Math.round((D.stale || 600) / 60) +
      " min. The same rule and limits as the wall (portal_wall_datastore_warn / _crit, portal_wall_aggr_warn / _crit, portal_wall_high_pct)."]));
  }

  // the server's state as of its answer, aged on this page's clock (the wall's rule between two reads, in a background tab, after a
  // sleep): OK turns grey when its oldest source passes LATE (ok_until); a condition stops counting when its own source passes STALE
  // (live: [severity, until]); a value with a limit the same, by its own source; the whole answer is not current once it is older
  // than STALE. Worked out from the answer as it came (D0), so a state never comes back.
  function aged() {
    var t = now(), stale = (D.stale || 600) * 1000, late = (D.late || 180) * 1000, old = D.now && t - D.now * 1000 > stale;
    D.areas = D0.areas.map(function (a0) {
      var a = JSON.parse(JSON.stringify(a0));
      if (a.state === "off") return a;
      if (a.metric) {
        var ma = a.metric.asof ? t - a.metric.asof * 1000 : Infinity;
        if (old || (a.metric.state === "ok" && ma > late) || ma > stale) a.metric.state = "stale";
      }
      if (old) { a.state = "stale"; a.crit = 0; a.warn = 0; a.worst = []; a.why = "the wall's answer is older than " + Math.round(stale / 60000) + " min"; return a; }
      if (a.state === "ok" && (a.ok_until == null || t > a.ok_until * 1000)) {
        a.state = "stale"; a.why = a.why || "a source of this area passed LATE (" + Math.round(late / 60000) + " min) since the page read it";
      }
      if (a.state === "crit" || a.state === "warn") {
        var n = {crit: 0, warn: 0};
        (a.live || []).forEach(function (x) { if ((x[1] == null || t <= x[1] * 1000) && n[x[0]] != null) n[x[0]]++; });
        a.crit = n.crit; a.warn = n.warn;
        if (!n.crit && !n.warn) { a.state = "stale"; a.worst = []; a.why = "its open conditions' sources passed STALE (" + Math.round(stale / 60000) + " min) - last known, not current"; }
        else a.state = n.crit ? "crit" : "warn";
      }
      return a;
    });
  }
  function sig() { return D ? D.areas.map(function (a) { return a.id + a.state + a.crit + a.warn + (a.metric ? a.metric.state : ""); }).join(",") : ""; }
  var LASTSIG = "";
  function render() {
    if (!D) return;
    aged();
    LASTSIG = sig();
    // a redraw keeps the keyboard where it was: the same area's same control
    var fa = document.activeElement && sec.contains(document.activeElement) ? document.activeElement : null;
    var fid = fa && fa.closest("[data-id]") ? fa.closest("[data-id]").getAttribute("data-id") : "", fsp = fa ? fa.getAttribute("data-sp") : null;
    var lk = skin();
    clear(sec);
    if (MODE === "full") renderFull(lk);
    else renderLine();
    sec.hidden = false;
    if (fa) {
      var back = fsp ? sec.querySelector('button[data-sp="' + fsp + '"]')
        : fid ? sec.querySelector('[data-id="' + fid + '"] a, a[data-id="' + fid + '"]') : null;
      if (back) back.focus();
    }
  }
  function failed(msg) {
    FAIL++;
    if (D) {                                     // a later read failed: every value grey, said - never a stale green, no lamp lit
      D = D0 = JSON.parse(JSON.stringify(D0));
      D.areas.forEach(function (a) { if (a.state !== "off") { a.state = "stale"; a.crit = 0; a.warn = 0; a.worst = []; a.why = "the portal could not read the wall (" + msg + ")"; if (a.metric) a.metric.state = "stale"; } });
      render();
      return;
    }
    clear(sec);
    if (MODE === "full") {
      sec.appendChild(h("h2", "ops-h", [TITLE], {id: "ops-h"}));
      sec.appendChild(h("p", "ops-note", ["The wall's areas could not be read (" + msg + "). They are tried again in 60 s; the wall itself: ", h("a", "", ["open it"], {href: WALL}), "."]));
    } else {
      sec.appendChild(h("div", "ops-strip s-stale", [
        h("div", "ops-id", [h("h2", "ops-site", [TITLE], {id: "ops-h"})]),
        h("span", "ops-div", ["-"], {"aria-hidden": "true"}),
        word("stale", "Not current"),
        h("span", "ops-note", ["Could not read the wall (" + msg + ")."]),
        h("a", "ops-open", ["Open wall ›"], {href: WALL})
      ]));
    }
    sec.hidden = false;
  }
  var SEQ = 0;
  function load() {
    var mine = ++SEQ;
    fetch(SRC, {headers: {Accept: "application/json"}, credentials: "same-origin", cache: "no-store"}).then(function (r) {
      if (mine !== SEQ) return null;                                         // an older read that answered late: dropped
      if (r.status === 404) { D = D0 = null; sec.hidden = true; return null; }  // the wall is off here: nothing kept to show again
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (a) {
      if (!a || mine !== SEQ) return;                                        // an older read that answered late: dropped
      if (!Array.isArray(a.areas)) throw new Error("no areas in the answer");
      FAIL = 0;
      if (a.now) SKEW = a.now * 1000 - Date.now();
      D0 = a;
      D = JSON.parse(JSON.stringify(a));
      render();
    }).catch(function (e) { if (mine === SEQ) failed(e && e.message ? e.message : "no answer"); });
  }
  sec.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("button[data-sp]");
    if (!b) return;
    var n = spotList().length || 1;
    SPOT = (SPOT + (+b.getAttribute("data-sp")) + n) % n;
    SPOT_LEFT = EVERY;
    PAUSE = now() + 30000;                       // a step by hand: the stage waits 30 s before it moves on
    render();
    var again = sec.querySelector('button[data-sp="' + b.getAttribute("data-sp") + '"]');
    if (again) again.focus();
  });
  sec.addEventListener("focusin", function () { PAUSE = Math.max(PAUSE, now() + 15000); });
  document.addEventListener("portal-look", function () { SPOT = 0; MA_ALT = false; render(); });
  document.addEventListener("visibilitychange", function () { if (!document.hidden) { render(); load(); } });
  load();
  var tick = 0;
  setInterval(function () {
    tick++;
    if (tick % 60 === 0) load();
    if (!D || document.hidden) return;
    var before = LASTSIG; aged();
    if (sig() !== before) { render(); return; }                               // a state passed LATE / STALE: drawn at once
    if (MODE !== "full") return;
    var lk = skin();
    if (lk === "strata" && moves() && PAUSE <= now() && spotList().length > 1) {
      SPOT_LEFT--;
      if (SPOT_LEFT <= 0) { SPOT_LEFT = EVERY; SPOT++; render(); return; }
      var st = sec.querySelector(".sp-step span");
      var n = spotList().length;
      if (st && n > 1) st.textContent = ((SPOT % n) + 1) + " of " + n + " · next in " + SPOT_LEFT + " s";
    }
    if (lk === "metro" && moves() && tick % EVERY === 0) {     // one tile at a time turns over to its worst items for 4 s
      var ts = sec.querySelectorAll(".ot");
      if (!ts.length) return;
      var t = ts[FLIP % ts.length];
      FLIP++;
      if (t.matches(":hover") || t.contains(document.activeElement)) return;
      t.classList.add("flip");
      setTimeout(function () { t.classList.remove("flip"); }, 4000);
    }
    if (lk === "metro-atlas" && moves() && tick % EVERY === 0) {
      var notes = sec.querySelectorAll(".ot-note[data-a]");
      if (!notes.length) return;
      MA_ALT = !MA_ALT;
      notes.forEach(function (el) {
        var card = el.closest(".ot");
        if (card && (card.matches(":hover") || card.contains(document.activeElement))) return;
        var next = MA_ALT ? el.getAttribute("data-b") : el.getAttribute("data-a");
        if (!next || next === el.textContent) return;
        el.textContent = next;
        el.classList.remove("ma-swap");
        void el.offsetWidth;
        el.classList.add("ma-swap");
      });
    }
  }, 1000);
})();
