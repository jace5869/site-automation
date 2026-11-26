// 1.135.0: three wall styles for a room's screen - Strata (a console: area tiles, a threshold watch, a spotlight that steps through
// the areas, problems first), Relay (an annunciator: system bays with lamp plates, the active events, a capacity watch) and Metro
// (live tiles: a critical area takes four tiles, a warning two; tiles turn over to their worst items; the Now list with Share to
// Teams). static/wall-page.js lends each the wall's model and its one rule (API): green only when the check ran and its source is
// current; LATE / NOT CURRENT grey, never green; an area not on the wall yet stays grey "Not on the wall yet". Each is drawn on a
// 1920 x 1080 board that wall-page.js scales to fill the screen (kiosk: the whole screen). No inline script; no HTML from strings.
(function () {
  "use strict";
  var A = null;                       // the wall's API (set on each draw)
  var SPOT = {i: 0, left: 10, pausedUntil: 0}, EVERY = 10, FLIP_EVERY = 8, FLIP = 0;
  var OFF_AREAS = [["vdi", "VDI", "Horizon"], ["ad", "Active Directory", ""], ["tickets", "Outages & problems", "ServiceNow"], ["network", "Network", "SolarWinds"]];
  var NET_IC = [["path", {d: "M9 3h6v5H9zM3 16h6v5H3zM15 16h6v5h-6zM12 8v4M6 16v-4h12v4", fill: "none", stroke: "currentColor", "stroke-width": 1.8, "stroke-linejoin": "round"}]];

  function h(t, c, k, a) { return A.h(t, c, k, a); }
  function W() { return A.W(); }
  function moves() { return document.documentElement.classList.contains("moves"); }
  function aIcon(id) {
    if (id === "network") { var e = A.icon(NET_IC, "0 0 24 24", "ai"); return e; }
    return A.areaIcon(id);
  }
  function logo(cls) {
    if (A.logo) return h("span", "wx-logo " + (cls || ""), [h("img", "", null, {src: A.logo, alt: W().title || "Logo"})]);
    return h("span", "wx-logo badge " + (cls || ""), [h("b", "", [((W().title || "O").charAt(0) || "O").toUpperCase()])], {"aria-hidden": "true"});
  }
  function lvl(v, w, c) { return A.level(v, w, c == null ? 1e9 : c); }
  function num(v) { return v == null ? "?" : (Math.round(v * 10) / 10).toString(); }
  function pts(d, unit) { d = Math.round(Math.abs(d) * 10) / 10; return d + (unit === "%" ? (d === 1 ? " pt" : " pts") : unit); }
  // one sentence: where the value is against its limits
  function said(m) {
    var u = m.unit || "%", v = num(m.v) + (u === "%" ? "%" : u);
    if (m.st === "stale" || m.st === "off") return v + " · not current (last known)";
    if (m.c != null && m.v >= m.c) return v + " · " + pts(m.v - m.c, u) + " over crit " + num(m.c);
    if (m.w != null && m.v >= m.w) return v + " · " + pts(m.v - m.w, u) + " over " + (m.c == null ? "high " : "warn ") + num(m.w) + (m.c != null ? " · crit at " + num(m.c) : "");
    return v + " · " + pts((m.w != null ? m.w : m.c) - m.v, u) + " under " + (m.w != null ? (m.c == null ? "high " : "warn ") + num(m.w) : "crit " + num(m.c));
  }
  // the limit bar: track, warn / crit bands, labelled dashed limits, the value grey while OK and amber / red where it crosses
  function bar(m, cls) {
    var max = m.max || 100, P = function (v) { return Math.max(0, Math.min(100, v / max * 100)); };
    var b = h("div", "wb " + (cls || ""), [], {role: "img", "aria-label": (m.label || "") + " " + said(m)});
    var tr = h("div", "wb-t");
    var place = function (e, l, w) { e.style.left = l + "%"; if (w != null) e.style.width = w + "%"; return e; };
    if (m.w != null) tr.appendChild(place(h("i", "xb-w"), P(m.w), P(m.c != null ? m.c : max) - P(m.w)));
    if (m.c != null) tr.appendChild(place(h("i", "xb-c"), P(m.c), 100 - P(m.c)));
    var st = m.st || "ok", w0 = m.w != null ? m.w : 1e9, c0 = m.c != null ? m.c : 1e9;
    if (st === "stale" || st === "off") tr.appendChild(place(h("i", "xv xv-s"), 0, P(m.v)));
    else {
      tr.appendChild(place(h("i", "xv xv-o"), 0, P(Math.min(m.v, w0))));
      if (m.v > w0) tr.appendChild(place(h("i", "xv xv-w"), P(w0), P(Math.min(m.v, c0)) - P(w0)));
      if (m.v > c0) tr.appendChild(place(h("i", "xv xv-c"), P(c0), P(m.v) - P(c0)));
    }
    if (m.w != null) tr.appendChild(place(h("i", "xl xl-w"), P(m.w)));
    if (m.c != null) tr.appendChild(place(h("i", "xl xl-c"), P(m.c)));
    b.appendChild(tr);
    var lab = h("div", "wb-l");
    if (m.w != null) lab.appendChild(place(h("span", "xt-w" + (P(m.w) > 80 ? " end" : ""), [(m.c == null ? "high " : "warn ") + num(m.w) + (m.unit && m.unit !== "%" ? m.unit : "")]), P(m.w)));
    if (m.c != null) lab.appendChild(place(h("span", "xt-c" + (P(m.c) > 88 ? " end" : "") + (m.w != null && P(m.c) - P(m.w) < 16 ? " low" : ""), ["crit " + num(m.c) + (m.unit && m.unit !== "%" ? m.unit : "")]), P(m.c)));
    b.appendChild(lab);
    return b;
  }

  // ---------------------------------------------------------------- the data each style draws (only what the wall's model has)
  // a source's state by the wall's own rule: LATE by its age, else green only when status("ok") says so (read OK, current, its
  // area whole) - a failed login 20 s after a good read is not current, never green
  function feedSt(fk) { var fr = A.feedFresh(fk); return fr === "stale" ? "stale" : fr === "late" ? "late" : A.status("ok", fk) === "ok" ? "ok" : "stale"; }
  function fresh(fk) { var f = A.feed(fk), x = A.freshTxt(f ? f.asof : null); return {st: feedSt(fk), name: f ? f.name : "no source", t: x.t}; }
  // the age text is the line's last text node, marked for wall-page.js's repaint every second (data-fresh / data-fresh-area)
  function srcLine(fk, areaId) {
    var x = areaId ? A.freshTxt(A.areaAsof(areaId)) : fresh(fk), st = areaId ? A.areaFresh(areaId) : x.st;
    var name = areaId ? (A.areaOf(areaId) || {}).sub || "" : x.name;
    var e = h("div", "wx-src " + st, [st === "ok" ? null : h("b", "tag", [st === "stale" ? "NOT CURRENT" : "LATE"]), name ? h("span", "", [name + " · "]) : null, x.t]);
    e.setAttribute(areaId ? "data-fresh-area" : "data-fresh", areaId || fk || "");
    return e;
  }
  function storageInfo() {
    var w = W(), st = A.areaState("storage"), n = A.areaCounts("storage"), t = A.thr("aggr"), td = A.thr("ds");
    var ag = (w.storage.aggr || []).filter(function (a) { return typeof a.v === "number"; });
    var ds = (w.storage.ds || []).filter(function (d) { return typeof d.v === "number" && d.capacity_watched !== false; }).sort(function (a, b) { return b.v - a.v; });
    var m = null;
    if (ag.length) m = {label: "fullest aggregate " + ag[0].n, obj: ag[0].n, v: ag[0].v, w: t[0], c: t[1], st: A.status(lvl(ag[0].v, t[0], t[1]), ag[0].feed), ref: "aggr:" + ag[0].ref, fk: ag[0].feed};
    else if (ds.length) m = {label: "fullest datastore " + ds[0].n, obj: ds[0].n, v: ds[0].v, w: td[0], c: td[1], st: A.status(A.dsLevel(ds[0]), ds[0].feed), ref: A.dsRef(ds[0]), fk: ds[0].feed};
    var nodes = Array.isArray(w.storage.nodes) && w.storage.nodes.length === 2 ? w.storage.nodes : null;
    return {id: "storage", st: st, n: n, m: m, big: m ? num(m.v) + "%" : "–", unit: m ? m.obj + " used" : "no value read",
            sub: n.crit + n.warn ? A.nowList("storage").filter(function (i) { return i.type === "cond"; }).slice(0, 1).map(function (i) { return i.t; })[0] || "" :
              nodes && nodes[1] > 0 ? nodes[0] + " of " + nodes[1] + " storage nodes up" : "", ag: ag, ds: ds};
  }
  function vmwareInfo() {
    var w = W(), st = A.areaState("vmware"), n = A.areaCounts("vmware"), hp = A.thr("high")[0];
    var cl = (w.vmware.clusters || []), up = 0, of = 0;
    cl.forEach(function (c) { up += +c.up || 0; of += +c.of || 0; });
    var busy = cl.filter(function (c) { return typeof c.cpu === "number"; }).sort(function (a, b) { return b.cpu - a.cpu; });
    var m = busy.length ? {label: "busiest cluster CPU " + busy[0].n, obj: busy[0].n, v: busy[0].cpu, w: hp, c: null, st: A.status(lvl(busy[0].cpu, hp, null), busy[0].feed),
                           ref: "cl:" + busy[0].vc + "|" + busy[0].n + "|cpu", fk: busy[0].feed} : null;
    var vms = 0; (w.vmware.hosts || []).forEach(function (x) { vms += +x.nvms || 0; });
    return {id: "vmware", st: st, n: n, m: m, big: of ? up + "/" + of : "–", unit: of ? "hosts up" : "no value read",
            sub: n.crit + n.warn ? A.nowList("vmware").filter(function (i) { return i.type === "cond"; }).slice(0, 1).map(function (i) { return i.t; })[0] || "" : of ? vms + " VMs on " + of + " hosts" : "", busy: busy};
  }
  // 1.138.0: Ops monitor (static/wall-fleet.js draws the area itself): its tile - nodes up of all, the worst group
  function fleetInfo() {
    var w = W(), f = w.fleet || {}, c = f.counts || {}, st = A.areaState("monitor"), n = A.areaCounts("monitor"), tot = 0;
    Object.keys(c).forEach(function (k) { tot += +c[k] || 0; });
    var up = +c.u || 0;
    return {id: "monitor", st: st, n: n, m: null, big: tot ? up.toLocaleString("en") + "/" + tot.toLocaleString("en") : "–", unit: tot ? "nodes up" : "no value read",
            sub: n.crit + n.warn ? A.nowList("monitor").filter(function (i) { return i.type === "cond"; }).slice(0, 1).map(function (i) { return i.t; })[0] || "" :
              tot ? (+c.d || 0) + " down · " + ((+c.p || 0) + (+c.m || 0)) + " off or unmanaged · " + (f.groups || 0) + " groups" : ""};
  }
  function fleetOn() { return (W().areas || []).some(function (a) { return a.id === "monitor" && a.built; }); }
  function areas() {
    var out = [];
    if ((W().areas || []).some(function (a) { return a.id === "storage" && a.built; })) out.push(storageInfo());
    if ((W().areas || []).some(function (a) { return a.id === "vmware" && a.built; })) out.push(vmwareInfo());
    if (fleetOn()) out.push(fleetInfo());
    OFF_AREAS.forEach(function (o) {
      var a = A.areaOf(o[0]);
      if (a && a.built) return;
      if (o[0] === "network" && fleetOn()) return;     // SolarWinds is on the wall: its tile is Ops monitor
      out.push({id: o[0], st: "off", name: a ? a.name : o[1], sub: (a && a.sub) || o[2], n: {crit: 0, warn: 0}, m: null, big: "", unit: "", off: true});
    });
    out = out.map(function (x) {
      if (x.off || A.areaOn(x.id)) return x;
      var a = A.areaOf(x.id) || {};                 // built but no source set (no vCenter / ONTAP here): grey, "Not on the wall yet"
      return {id: x.id, st: "off", name: a.name || x.id, sub: a.sub || "", n: {crit: 0, warn: 0}, m: null, big: "", unit: "", off: true};
    });
    out.forEach(function (x) { if (!x.name) x.name = (A.areaOf(x.id) || {}).name || x.id; x.on = A.areaOn(x.id); });
    return out;
  }
  var RANK = {crit: 4, warn: 3, stale: 2, ok: 1, off: 0};
  function counts(list) { var c = {crit: 0, warn: 0, ok: 0, stale: 0}; list.forEach(function (a) { if (c[a.st] != null) c[a.st]++; }); return c; }
  // the capacity rows: aggregates and datastores, fullest first, each against its own limits
  function capRows(max, areaId) {
    var w = W(), t = A.thr("aggr"), td = A.thr("ds"), rows = [];
    if ((areaId || focus()) === "vmware") {                    // the VMware area: each cluster's CPU and memory against the high line
      var hp = A.thr("high")[0];
      (w.vmware.clusters || []).forEach(function (c) {
        ["cpu", "mem"].forEach(function (k) { if (typeof c[k] === "number") rows.push({label: c.n, sub: "cluster " + (k === "cpu" ? "CPU" : "memory"), v: c[k], w: hp, c: null, st: A.status(lvl(c[k], hp, null), c.feed), ref: "cl:" + c.vc + "|" + c.n + "|" + k}); });
      });
      rows.sort(function (a, b) { return b.v - a.v; });
      return rows.slice(0, max);
    }
    (w.storage.aggr || []).forEach(function (a) { if (typeof a.v === "number") rows.push({label: a.n, sub: "aggregate", v: a.v, w: t[0], c: t[1], st: A.status(lvl(a.v, t[0], t[1]), a.feed), ref: "aggr:" + a.ref}); });
    (w.storage.ds || []).forEach(function (d) { if (typeof d.v === "number" && d.capacity_watched !== false) rows.push({label: d.n, sub: "datastore", v: d.v, w: td[0], c: td[1], st: A.status(A.dsLevel(d), d.feed), ref: A.dsRef(d)}); });
    rows.sort(function (a, b) { return (b.v - (b.w || 0)) - (a.v - (a.w || 0)); });
    return rows.slice(0, max);
  }
  // ?area=storage / vmware (or the area menu, or rotate areas): the board keeps every area's tile, and its events, spotlight and
  // capacity rows are that area's
  function focus() { var a = A.S.area; return a === "storage" || a === "vmware" ? a : null; }
  function focusName() { var f = focus(); return f ? (A.areaOf(f) || {}).name || f : ""; }
  function worstNow() { return A.nowList(focus()); }
  function sevWord(st) { return st === "crit" ? "Critical" : st === "warn" ? "Warning" : st === "stale" ? "Not current" : st === "ok" ? "OK" : "Not on the wall yet"; }
  function clock() { return h("span", "wx-clock", [h("b", "", [A.hms(A.NOW())]), h("small", "", [A.zoneName()])]); }
  function dayTxt() {
    try { return new Intl.DateTimeFormat("en-GB", {weekday: "short", day: "2-digit", month: "short", year: "numeric", timeZone: W().zone || "UTC"}).format(new Date(A.NOW())); }
    catch (e) { return new Date(A.NOW()).toDateString(); }
  }
  // what moves by itself says so, kiosk too: the rotation (Rotate button, R) and how often
  function rotCue() { return A.ROT.on ? "Rotating " + (A.ROT.mode === "styles" ? "styles" : "areas") + " every " + A.ROT.every + " s" : "Rotation off"; }
  function nothingOn() { return h("p", "wx-empty", ["Nothing on the wall is open, or nothing current to say so: green only when every source of an area is current."]); }

  // ================================================================ STRATA
  function strata(r) {
    var w = W(), list = areas(), c = counts(list), now = worstNow();
    var board = h("div", "wx wx-strata");
    var sum = h("div", "sx-sum", [
      h("span", "p crit" + (c.crit ? " on" : ""), [A.word("crit", c.crit + " Crit")]), h("span", "p warn" + (c.warn ? " on" : ""), [A.word("warn", c.warn + " Warn")]),
      h("span", "p ok" + (c.ok ? " on" : ""), [A.word(c.ok ? "ok" : "off", c.ok + " OK")]), h("span", "p stale" + (c.stale ? " on" : ""), [A.word("stale", c.stale + " Not current")])]);
    board.appendChild(h("header", "sx-hd", [logo(), h("div", "sx-t", [h("small", "", ["Strata · ops wall" + (focus() ? " · " + focusName() : "")]), h("b", "", [w.title || "Ops center"])]),
      w.enclave ? h("span", "sx-chip", [h("small", "", ["Enclave"]), " " + w.enclave]) : null, sum, h("div", "sx-time", [clock()])]));
    var top = now.filter(function (i) { return i.type === "cond" && i.sev === "crit"; })[0] || now.filter(function (i) { return i.type === "cond"; })[0];
    if (top) board.classList.add("ban");
    if (top) board.appendChild(h("div", "sx-ban " + top.sev, [A.word(top.sev, sevWord(top.sev).toUpperCase()), h("b", "", [A.aShort(top.area) + " · " + top.t]),
      h("span", "", ["Since " + A.when(top.since) + " (" + A.dur(A.NOW() - top.since) + ")" + (top.sub ? " · " + top.sub : "")]),
      srcLine(A.condFeed(top.c)), h("button", "sx-insp", ["Inspect →"], {type: "button", "data-d": "c:" + top.id, title: "Open its detail card (and Share to Teams)"})]));
    var left = h("div", "sx-left"), tiles = h("div", "sx-tiles");
    var spotList = list.filter(function (a) { return a.st !== "off"; }).sort(function (a, b) { return (RANK[b.st] - RANK[a.st]) || ((b.n.crit + b.n.warn) - (a.n.crit + a.n.warn)); });
    if (focus() !== SPOT.focus) {               // the area chosen (?area=, the menu, rotate areas) goes on stage once; a click may move it
      SPOT.focus = focus();
      var fi = spotList.map(function (a) { return a.id; }).indexOf(focus());
      if (fi >= 0) SPOT.i = fi;
    }
    SPOT.n = spotList.length;
    var spot = spotList.length ? spotList[SPOT.i % spotList.length] : null;
    list.forEach(function (a) {
      var t = h("div", "sx-tile " + a.st + (spot && spot.id === a.id ? " onstage" : ""), [
        h("div", "th", [aIcon(a.id), h("b", "", [a.name]), A.word(a.st, sevWord(a.st))]),
        a.off ? h("div", "big off", ["–"]) : h("div", "big", [h("b", "", [a.big]), h("span", "", [a.unit])]),
        h("p", "sub", [a.off ? "Not on the wall yet" + (a.sub ? " (" + a.sub + ")" : "") + " - grey, never a guess." : a.sub || ""]),
        a.m ? bar(a.m, "sm") : h("div", "wb-ph"),
        a.m ? h("p", "say " + a.m.st, [said(a.m)]) : null,
        a.off ? null : h("div", "tf", [srcLine(null, a.id), spot && spot.id === a.id ? h("span", "stg", ["On stage"]) : h("span", "cue", ["Click: on stage ›"])])
      ], a.off ? {"aria-label": a.name + ": not on the wall yet"} : {"data-spot": a.id, tabindex: "0", role: "button", title: "Put " + a.name + " on the stage (right)"});
      tiles.appendChild(t);
    });
    left.appendChild(tiles);
    // the threshold watch: every value with a limit the wall reads, against its limit - worst first
    var watch = h("div", "sx-watch", [h("div", "wh", [h("b", "", ["Threshold watch - value vs limit"]), h("span", "", ["click a row for its detail card"])])]);
    var rows = capRows(3), vm = list.filter(function (a) { return a.id === "vmware"; })[0];
    if (vm && vm.m && !focus()) rows.push({label: vm.m.obj, sub: "cluster CPU", v: vm.m.v, w: vm.m.w, c: null, st: vm.m.st, ref: vm.m.ref});
    rows.slice(0, 4).forEach(function (m) {
      watch.appendChild(h("div", "wr " + m.st, [A.word(m.st, sevWord(m.st)), h("span", "nm", [h("b", "", [m.label]), " " + m.sub]), bar(m), h("span", "wsay", [said(m)])],
        {"data-d": m.ref, tabindex: "0", role: "button"}));
    });
    if (!rows.length) watch.appendChild(h("p", "wx-empty", ["No value with a limit in the read (no live aggregate, datastore or cluster)."]));
    left.appendChild(watch);
    // the stage: one area at a time, problems first; steps every EVERY s, or by hand
    var right = h("div", "sx-stage" + (spot ? " " + spot.st : ""));
    var tabs = h("div", "sx-tabs", spotList.map(function (a, i) {
      return h("button", "tb " + a.st + (spot && spot.id === a.id ? " on" : ""), [A.word(a.st, String(i + 1) + " " + (A.aShort(a.id) || a.name))], {type: "button", "data-spot": a.id});
    }).concat([h("span", "cd", [spotList.length > 1 ? (focus() ? "showing " + focusName() + " - ‹ › to step" : SPOT.pausedUntil > Date.now() ? "paused 60 s after a click" : moves() && !frozen() ? "next in " + SPOT.left + " s" : "use ‹ ›") + " · problems first" : "problems first"]),
      h("button", "sb", ["‹"], {type: "button", "data-step": "-1", title: "The area before"}), h("button", "sb", ["›"], {type: "button", "data-step": "1", title: "The next area"})]));
    right.appendChild(tabs);
    if (spot) right.appendChild(stageOf(spot)); else right.appendChild(nothingOn());
    board.appendChild(h("div", "sx-body", [left, right]));
    var feeds = Object.keys(w.feeds || {}).filter(function (k) { return k !== "netapp-run"; });
    board.appendChild(h("footer", "sx-ft", feeds.map(function (k) { var x = fresh(k), e = h("span", "fd " + x.st, [h("i"), h("span", "", [x.name + " · "]), x.t], {"data-d": "feed:" + k, tabindex: "0", role: "button"}); e.setAttribute("data-fresh", k); return e; }).concat([
      h("span", "rot rotcue", [rotCue()]),
      h("span", "lg", ["Key: ", A.word("crit", "Crit"), A.word("warn", "Warn / late > " + Math.round((w.late || 180) / 60) + " min"), h("span", "lgk", ["✓ OK = every source current"]), A.word("stale", "Not current > " + Math.round((w.stale || 600) / 60) + " min")])])));
    r.appendChild(board);
    board.addEventListener("click", function (e) {
      var sp = e.target.closest("[data-spot]"), st = e.target.closest("[data-step]");
      if (st) { SPOT.i = (SPOT.i + (+st.getAttribute("data-step")) + Math.max(1, spotList.length)) % Math.max(1, spotList.length); SPOT.left = EVERY; SPOT.pausedUntil = Date.now() + 60000; A.render(); return; }
      if (sp) { var i = spotList.map(function (a) { return a.id; }).indexOf(sp.getAttribute("data-spot")); if (i >= 0) { SPOT.i = i; SPOT.left = EVERY; SPOT.pausedUntil = Date.now() + 60000; A.render(); } }
    });
    board.addEventListener("keydown", function (e) { if ((e.key === "Enter" || e.key === " ") && e.target.getAttribute && e.target.getAttribute("data-spot")) { e.preventDefault(); e.target.click(); } });
  }
  function clearTo(r) { A.clear(r); return r; }
  function stageOf(a) {
    var box = h("div", "sx-spot");
    var items = A.nowList(a.id).filter(function (i) { return i.type === "cond"; });
    box.appendChild(h("div", "sh", [A.word(a.st, sevWord(a.st)), h("small", "", ["Spotlight · " + a.name]),
      h("b", "", [items[0] ? items[0].t : a.st === "stale" ? a.name + ": data not current" : a.name + ": nothing open"]),
      items[0] ? h("button", "sx-insp", ["Inspect →"], {type: "button", "data-d": "c:" + items[0].id}) : null]));
    if (a.id === "storage") {
      var rows = capRows(8, "storage");
      box.appendChild(h("div", "sx-chart", [h("div", "ch", [h("b", "", ["Capacity - aggregates and datastores, nearest their limit first"]), h("span", "", ["each against its own warn / crit"])])].concat(
        rows.map(function (m) { return h("div", "crow " + m.st, [h("span", "nm", [m.label, h("small", "", [m.sub])]), bar(m, "sm"), h("span", "v", [said(m)])], {"data-d": m.ref, tabindex: "0", role: "button"}); }))));
    } else if (a.id === "vmware") {
      var w = W(), hosts = (w.vmware.hosts || []);
      var hot = (w.vmware.hot || []).slice(0, 4), hp = A.thr("high")[0];
      if (hot.length) box.appendChild(h("div", "sx-chart", [h("div", "ch", [h("b", "", ["Sustained high - the last hour against the " + hp + " % line"]), h("span", "", [A.thr("high")[1] + " min or longer counts"])])].concat(
        hot.map(function (x) { return h("div", "hrow", [h("span", "nm", [x.n, h("small", "", [x.k + " · " + x.m])]), spark(x.spark, hp, (x.cond && A.cond(x.cond) && A.condStale(A.cond(x.cond))) || A.status("warn", x.cond && A.cond(x.cond) ? A.condFeed(A.cond(x.cond)) : A.vcFeed()) !== "warn" ? "stale" : "warn"), h("span", "v", [num(x.v) + "% · " + pts(x.v - hp, "%") + " over high " + hp])],
          x.cond ? {"data-d": "c:" + x.cond, tabindex: "0", role: "button"} : null); }))));
      box.appendChild(h("div", "sx-fleet", [h("div", "ch", [h("b", "", ["Hosts (" + hosts.length + ")"]), h("span", "", ["each square a host · click for its card"])]),
        h("div", "fg", hosts.slice(0, 96).map(function (x) {
          var st = A.status(["crit", "warn", "ok", "stale"].indexOf(x.st) >= 0 ? x.st : "stale", x.feed);
          return h("span", "hq " + st, [x.n.split(".")[0]], {"data-d": "host:" + x.vc + "|" + x.n, tabindex: "0", role: "button", title: x.n + ": " + sevWord(st)});
        }))]));
    } else if (a.id === "monitor") {                    // 1.138.0: the worst groups; the whole area one click away
      var fw = (W().fleet || {}).worst || [], last = a.st === "stale";
      box.appendChild(h("div", "sx-chart", [h("div", "ch", [h("b", "", ["Service groups with a node not up" + (last ? " (last known)" : "")]), h("span", "", ["click for the Ops monitor area"])])].concat(
        fw.length ? fw.map(function (g) {
          var st = last ? "stale" : g[1] ? "crit" : "warn";
          return h("div", "crow " + st, [h("span", "nm", [String(g[0])]), A.word(st, [g[1] ? g[1] + " down" : "", g[2] ? g[2] + " warning" : ""].filter(Boolean).join(" · "))],
                   {"data-goto": "monitor", tabindex: "0", role: "button", title: "Open Ops monitor"});
        }) : [h("p", "wx-empty", [last ? "No current data." : "Every group up."])]).concat([
        h("button", "sx-insp", ["Open Ops monitor →"], {type: "button", "data-goto": "monitor", title: "Every node by service group"})])));
    }
    var side = h("div", "sx-side", [h("b", "", ["Open now - " + a.name])]);
    if (!items.length) side.appendChild(h("p", "wx-empty", [a.st === "ok" ? "Nothing open." : "Nothing current - grey until the source answers."]));
    items.slice(0, 6).forEach(function (i) { side.appendChild(h("div", "it " + i.sev, [A.word(i.sev, sevWord(i.sev)), h("span", "", [i.t]), h("small", "", [A.dur(A.NOW() - i.since)])], {"data-d": "c:" + i.id, tabindex: "0", role: "button"})); });
    box.appendChild(side);
    return box;
  }
  function spark(series, line, st) {
    var a = Array.isArray(series) ? series : [], n = a.length, w = 300, hh = 40, lo = 0, hi = 100;
    var Y = function (v) { return hh - 2 - (Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo) * (hh - 4); };
    var kids = [A.s("rect", {class: "sk-b", x: 0, y: 0, width: w, height: Y(line)}), A.s("line", {class: "sk-l", x1: 0, x2: w, y1: Y(line), y2: Y(line), "vector-effect": "non-scaling-stroke"})];
    for (var i = 1; i < n; i++) {
      if (typeof a[i] !== "number" || typeof a[i - 1] !== "number") continue;
      var over = Math.max(a[i], a[i - 1]) >= line;
      kids.push(A.s("line", {class: "seg " + (st === "stale" ? "stale" : over ? "warn" : "ok"), x1: (i - 1) * w / (n - 1), x2: i * w / (n - 1), y1: Y(a[i - 1]), y2: Y(a[i]), "vector-effect": "non-scaling-stroke"}));
    }
    return A.s("svg", {viewBox: "0 0 " + w + " " + hh, preserveAspectRatio: "none", class: "spk", role: "img", "aria-label": "last hour against the " + line + " % line"}, kids);
  }

  // ================================================================ RELAY
  function relay(r) {
    var w = W(), list = areas(), now = worstNow();
    var board = h("div", "wx wx-relay");
    board.appendChild(h("header", "rx-hd", [logo(), h("div", "rx-t", [h("b", "", [w.title || "Ops center"]), h("small", "", [[w.enclave, focusName()].filter(Boolean).join(" / ")])]),
      h("div", "rx-time", [clock(), h("small", "wx-day", [dayTxt()])])]));
    var crit = now.filter(function (i) { return i.type === "cond" && i.sev === "crit"; })[0];
    if (crit) board.classList.add("ban");
    if (crit) board.appendChild(h("div", "rx-alarm", [A.lamp("crit", "big"), h("div", "", [h("small", "", ["Critical alarm · " + A.aShort(crit.area)]), h("b", "", [crit.t])]),
      h("div", "o", [h("b", "", [crit.sub || ""]), h("span", "", ["Since " + A.when(crit.since) + " (" + A.dur(A.NOW() - crit.since) + ")"])]),
      h("button", "rx-go", ["View detail →"], {type: "button", "data-d": "c:" + crit.id})]));
    var bays = h("div", "rx-bays");
    list.forEach(function (a, i) {
      var n = a.n.crit + a.n.warn;
      bays.appendChild(h("div", "rx-bay " + a.st, [
        h("div", "bh", [aIcon(a.id), h("b", "", [a.name]), h("small", "", ["B-0" + (i + 1)])]),
        h("div", "plate " + a.st, [A.word(a.st, sevWord(a.st).toUpperCase()), h("span", "ticks", [n ? (n > 9 ? "9+" : String(n)) + " open" : ""])]),
        h("p", "bs", [a.off ? "No live source yet - grey, never a guess" : a.n.crit + a.n.warn ? countTxt(a.n) : a.st === "stale" ? lastTxt(a.id) : a.big ? a.big + " " + a.unit : ""]),
        a.off ? h("div", "bf", ["Not on the wall yet" + (a.sub ? " · " + a.sub : "")]) : h("div", "bf", [srcLine(null, a.id)])
      ], a.off ? null : {"data-goto": a.id, tabindex: "0", role: "button", title: "Show " + a.name + " on the wall (this style)"}));
    });
    var evs = h("div", "rx-evs", [h("div", "eh", [h("b", "", ["Active events"]), h("span", "", ["worst first · click for the detail card"])])]);
    var conds = now.filter(function (i) { return i.type === "cond" || i.type === "feed"; });
    conds.slice(0, 5).forEach(function (i) {
      evs.appendChild(h("div", "ev " + i.sev, [A.lamp(i.sev), h("div", "", [h("small", "", [sevWord(i.sev) + " / " + A.aShort(i.area)]), h("b", "", [i.t]), h("span", "", [i.sub || ""])]),
        h("time", "", [i.since ? A.hm(i.since) : ""])], i.type === "cond" ? {"data-d": "c:" + i.id, tabindex: "0", role: "button"} : {"data-d": "feed:" + i.feed, tabindex: "0", role: "button"}));
    });
    if (!conds.length) evs.appendChild(nothingOn());
    evs.appendChild(h("div", "ef", [h("span", "", [conds.length + " active event" + (conds.length === 1 ? "" : "s")]), conds.length > 5 ? h("span", "", ["+" + (conds.length - 5) + " more in the Cockpit style"]) : null]));
    board.appendChild(h("div", "rx-mid", [h("section", "rx-sys", [h("div", "sh", [h("b", "", ["System bays"]), h("span", "", [list.length + " areas · click a bay"])]), bays]), evs]));
    var cap = h("div", "rx-cap");
    var vm = list.filter(function (a) { return a.id === "vmware"; })[0];
    var caps = capRows(focus() ? 3 : 2);
    if (vm && vm.m && !focus()) caps.push({label: vm.m.obj, sub: "busiest cluster CPU", v: vm.m.v, w: vm.m.w, c: null, st: vm.m.st, ref: vm.m.ref});
    caps.forEach(function (m) {
      cap.appendChild(h("div", "cc " + m.st, [h("div", "ct", [h("b", "", [m.sub.charAt(0).toUpperCase() + m.sub.slice(1)]), h("small", "", [m.label])]),
        h("div", "cv", [h("b", "", [num(m.v)]), h("span", "", ["%"]), h("em", "", [said(m).split(" · ").slice(1).join(" · ")])]), bar(m)], {"data-d": m.ref, tabindex: "0", role: "button"}));
    });
    if (!caps.length) cap.appendChild(h("p", "wx-empty", ["No value with a limit in the read."]));
    board.appendChild(h("section", "rx-capw", [h("div", "sh", [h("b", "", ["Capacity watch"]), h("span", "", ["value against its warn / crit limit"])]), cap]));
    var feeds = Object.keys(w.feeds || {}).filter(function (k) { return k !== "netapp-run"; }), cur = feeds.filter(function (k) { return A.feedFresh(k) === "ok"; }).length;
    board.appendChild(h("footer", "rx-ft", [h("span", "", [cur + " of " + feeds.length + " sources current"]),
      h("span", "rotcue", [rotCue()]),
      h("span", "", ["Grey = no current data, never green"])]));
    r.appendChild(board);
  }
  // a stale area's last-known open conditions (kept until a read sees them recover), never "none" while one is kept
  function lastTxt(id) { var n = A.lastKnown(id), k = n.crit + n.warn; return k ? "Last known: " + countTxt(n) + " - data not current" : "Last known: no wall condition - data not current"; }
  function countTxt(n) { return [n.crit ? n.crit + " critical" : "", n.warn ? n.warn + " warning" : ""].filter(Boolean).join(" · "); }

  // ================================================================ METRO
  function metro(r) {
    var w = W(), list = areas(), c = counts(list), now = worstNow();
    var board = h("div", "wx wx-metro" + (c.crit ? " has-crit" : ""));
    var feeds = Object.keys(w.feeds || {}).filter(function (k) { return k !== "netapp-run"; });
    board.appendChild(h("header", "mx-hd", [logo(), h("b", "t", [w.title || "Ops center"]), h("span", "site", [[w.enclave, focusName()].filter(Boolean).join(" · ")]),
      h("div", "sum", [c.crit ? h("span", "c", [A.word("crit", c.crit + " CRITICAL")]) : null, c.warn ? h("span", "w", [A.word("warn", c.warn + " WARNING")]) : null,
        h("span", "o", [A.word(c.ok ? "ok" : "off", c.ok + " OK")]), c.stale ? h("span", "s", [A.word("stale", c.stale + " NOT CURRENT")]) : null]),
      h("div", "src", feeds.slice(0, 3).map(function (k) { var f = A.feed(k), st = A.feedFresh(k), age = f && f.asof ? A.ago(A.ageS(f.asof)).replace(" ago", "") : "never";
        st = feedSt(k);
        return h("span", st, [h("i"), (f ? f.name.replace(/^(vCenter|ONTAP) /, "$1 ").split(".")[0] : k) + " ", h("b", "age", [st === "ok" ? age : st === "late" ? "LATE " + age : "NOT CURRENT"])],
          {"data-d": "feed:" + k, "data-age": k, tabindex: "0", role: "button"}); })),
      clock()]));
    var grid = h("section", "mx-grid");
    var sorted = list.slice().sort(function (a, b) { return RANK[b.st] - RANK[a.st]; });
    sorted.forEach(function (a) {
      var items = a.off ? [] : A.nowList(a.id).filter(function (i) { return i.type === "cond"; });
      var face = h("div", "face", [h("div", "nm", [aIcon(a.id), a.name]),
        a.off ? null : h("div", "big", [a.big, h("small", "", [a.unit])]),
        h("p", "mwhy", [a.off ? "No live source on the wall yet" + (a.sub ? " (" + a.sub + ")" : "") : items[0] ? items[0].t + " · since " + A.hm(items[0].since) + " (" + A.dur(A.NOW() - items[0].since) + ")" : a.sub || ""]),
        a.m ? bar(a.m) : null, a.m ? h("p", "say", [said(a.m)]) : null,
        h("div", "word", [A.word(a.st, sevWord(a.st).toUpperCase())])]);
      var back = h("div", "back", [h("h4", "", [a.name + " · worst items"])].concat(items.slice(0, 4).map(function (i) {
        return h("div", "r", [h("span", "", [i.t]), h("b", "", [(i.sev === "crit" ? "CRIT " : "WARN ") + A.dur(A.NOW() - i.since)])]);
      })).concat(items.length ? [] : [h("div", "r", [h("span", "", [a.off ? "Not on the wall yet" : a.st === "ok" ? "Nothing open" : "No current data"]), h("b", "", [""])])]), {"aria-hidden": "true"});
      grid.appendChild(h("div", "t " + a.st, [face, back], a.off ? null : {"data-goto": a.id, tabindex: "0", role: "button", title: "Show " + a.name + " on the wall · tiles turn over to their worst items"}));
    });
    var used = 0, lastOne = null;
    sorted.forEach(function (a) { used += a.st === "crit" ? 4 : a.st === "warn" ? 2 : 1; });
    grid.querySelectorAll(".t").forEach(function (t) { if (!t.classList.contains("crit") && !t.classList.contains("warn")) lastOne = t; });
    var spare = (4 - used % 4) % 4;
    if (lastOne && spare) lastOne.classList.add("span" + Math.min(4, 1 + spare));
    var side = h("aside", "mx-now", [h("h3", "", [h("span", "", ["Now · worst first"]), h("span", "", [now.length + " open"])])]);
    now.slice(0, 5).forEach(function (i) {
      side.appendChild(h("div", "ev " + i.sev, [h("span", "s", [i.sev === "crit" ? "CRIT" : i.sev === "warn" ? "WARN" : "NOT CURRENT"]), h("span", "wt", [i.t]),
        h("span", "a", [i.since ? A.dur(A.NOW() - i.since) : ""]),
        h("span", "d", [A.aShort(i.area) + (i.sub ? " · " + i.sub : "")]),
        i.type === "cond" ? h("button", "shb", ["Share to Teams"], {type: "button", "data-share-now": i.id, title: "Copy this as a message and a picture for Teams"}) : null],
        i.type === "cond" ? {"data-d": "c:" + i.id, tabindex: "0", role: "button"} : {"data-d": "feed:" + i.feed, tabindex: "0", role: "button"}));
    });
    if (!now.length) side.appendChild(nothingOn());
    var mine = (w.conds || []).filter(function (x) { return !focus() || x.area === focus(); });
    var since = A.NOW() - 4 * 3600000, opened = mine.filter(function (x) { return x.start >= since; }).length, cleared = mine.filter(function (x) { return x.end != null && x.end >= since; }).length;
    var longest = mine.filter(function (x) { return x.end == null; }).reduce(function (m, x) { return Math.max(m, A.NOW() - x.start); }, 0);
    side.appendChild(h("div", "shift", [h("h3", "", [h("span", "", ["Last 4 hours" + (focus() ? " · " + focusName() : "")])]),
      h("div", "kv", [h("b", "", [String(opened)]), h("span", "", ["opened"])]), h("div", "kv", [h("b", "", [String(cleared)]), h("span", "", ["cleared"])]),
      h("div", "kv", [h("b", "", [longest ? A.dur(longest) : "–"]), h("span", "", ["longest open"])])]));
    side.appendChild(h("p", "quiet", ["Quiet: " + (list.filter(function (a) { return a.st === "ok"; }).map(function (a) { return a.name; }).join(", ") || "none") + ". Grey = no current data, never green."]));
    board.appendChild(h("div", "mx-main", [grid, side]));
    var cap = h("section", "mx-cap", [h("h3", "", ["Capacity"])]);
    capRows(6).forEach(function (m) { cap.appendChild(h("div", "bb " + m.st, [h("div", "lab", [h("span", "", [m.label]), h("b", "", [num(m.v) + "%"])]), bar(m, "sm")], {"data-d": m.ref, tabindex: "0", role: "button"})); });
    board.appendChild(cap);
    board.appendChild(h("div", "mx-pv", [(moves() ? "Tiles turn over every " + FLIP_EVERY + " s to their worst items · " : "Hover a tile to see its worst items · ") + "a tile shows its area · a row opens its detail card · Share to Teams copies text and a picture · ", h("span", "rotcue", [rotCue()])]));
    r.appendChild(board);
    board.addEventListener("click", function (e) {
      var b = e.target.closest("[data-share-now]");
      if (b) { e.stopPropagation(); e.preventDefault(); A.share(b.getAttribute("data-share-now")); }
    }, true);
  }

  // the spotlight's step and Metro's turn: one timer, only for the style on screen
  setInterval(function () {
    if (!A || !W() || document.hidden) return;
    var de = document.documentElement;
    if (!frozen()) {                             // the board's own clock, date, source ages and rotation cue tick with the page
      document.querySelectorAll(".wx .wx-clock>b").forEach(function (c) { c.textContent = A.hms(A.NOW()); });
      document.querySelectorAll(".wx .wx-day").forEach(function (c) { c.textContent = dayTxt(); });
      document.querySelectorAll(".wx .rotcue").forEach(function (c) { c.textContent = rotCue(); });
      document.querySelectorAll(".wx [data-age]").forEach(function (e) {
        var k = e.getAttribute("data-age"), f = A.feed(k), st = feedSt(k), age = f && f.asof ? A.ago(A.ageS(f.asof)).replace(" ago", "") : "never", b = e.querySelector(".age");
        e.className = st;
        if (b) b.textContent = st === "ok" ? age : st === "late" ? "LATE " + age : "NOT CURRENT";
      });
    }
    // the spotlight steps on its own only over the whole board (an area chosen stays on stage), never on What the wall watches
    if (de.classList.contains("style-strata") && moves() && !frozen() && !focus() && A.S.area !== "watch" && document.querySelector(".wx-strata")
        && (SPOT.n || 0) > 1 && SPOT.pausedUntil <= Date.now()) {
      SPOT.left--;
      var cd = document.querySelector(".sx-tabs .cd");
      if (SPOT.left <= 0) {
        SPOT.left = EVERY; SPOT.i++;
        var stage = document.getElementById("stage");
        if (stage && !document.querySelector("#dsBack.on")) A.render();
      } else if (cd) cd.textContent = "next in " + SPOT.left + " s · problems first";
    }
    if (de.classList.contains("style-metro") && moves() && !frozen()) {
      FLIP++;
      if (FLIP % FLIP_EVERY) return;
      var ts = document.querySelectorAll(".wx-metro .mx-grid .t");
      if (!ts.length) return;
      var t = ts[(FLIP / FLIP_EVERY) % ts.length];
      if (t.matches(":hover") || t.contains(document.activeElement)) return;
      t.classList.add("flip");
      setTimeout(function () { t.classList.remove("flip"); }, 4000);
    }
  }, 1000);
  function frozen() { return document.documentElement.classList.contains("freeze"); }

  window.SAWallStyles = {
    strata: function (api, r) { A = api; strata(r); },
    relay: function (api, r) { A = api; relay(r); },
    metro: function (api, r) { A = api; metro(r); }
  };
})();
