// The portal's ops-center wall (/portal/wall): one model from /api/wall (roles/site_portal_stack/files/wall_model.py), three ways to
// draw it - Lights, Cockpit (the default), Map - with the style saved per viewer, ?style=, ?area=, ?kiosk=1, ?rotate=, ?every=.
// Every gauge, tile, counter, light and row opens a detail card: what is wrong, since when, the value against its threshold, what it
// touches, the source and its age, where to check it. Freshness: LATE (amber) after 3 min, STALE after 10 min; stale is never green.
// No HTML strings, no inline style attributes (the page's CSP): the DOM is built node by node; the timeline sets CSSOM positions only.
(function () {
  "use strict";
  var root = document.getElementById("wall");
  if (!root) return;
  var SRC = root.getAttribute("data-src");
  var QS = new URLSearchParams(location.search);
  // Codex 1.130.0: ?freeze= (a still clock, no reads: screenshots) and ?pull= only where the portal runs as the test harness -
  // a production wall URL always ages and reads
  var TEST = root.getAttribute("data-test") === "1";
  var FREEZE = TEST && QS.get("freeze") === "1";
  var MIN = 60000;
  var W = null, SKEW = 0, BY = {}, ETAG = "", LOADED = false;
  // the portal itself not answering (a restart, the network): after FAILS_MAX failed reads every source counts as not current
  var FAILS = 0, FAILS_MAX = 1, UNREACH = false, UNREACH_SINCE = 0, LASTF = "";
  function NOW() { return FREEZE && W ? W.now * 1000 : Date.now() + SKEW; }

  // ---------------------------------------------------------------- DOM helpers (text only, never HTML)
  var NS = "http://www.w3.org/2000/svg";
  function add(e, kids) {
    (Array.isArray(kids) ? kids : [kids]).forEach(function (c) {
      if (c == null || c === false || c === "") return;
      if (Array.isArray(c)) { add(e, c); return; }
      e.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
    });
    return e;
  }
  function h(tag, cls, kids, at) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (at) for (var k in at) if (at[k] != null && at[k] !== false) e.setAttribute(k, at[k] === true ? "" : String(at[k]));
    return add(e, kids);
  }
  function s(tag, at, kids) {
    var e = document.createElementNS(NS, tag);
    if (at) for (var k in at) if (at[k] != null) e.setAttribute(k, String(at[k]));
    return add(e, kids);
  }
  function clear(e) { while (e.firstChild) e.removeChild(e.firstChild); return e; }
  function $(sel) { return root.querySelector(sel); }

  // icons as data: [tag, attrs] lists, drawn with s()
  var P = function (d, extra) { var a = {d: d, fill: "none", stroke: "currentColor"}; for (var k in extra) a[k] = extra[k]; return ["path", a]; };
  var LAMP_IC = {
    ok: [P("M5.5 12.5l4.2 4.2L18.5 7.8", {"stroke-width": 3, "stroke-linecap": "round", "stroke-linejoin": "round"})],
    warn: [P("M12 5.5v8.5", {"stroke-width": 3.2, "stroke-linecap": "round"}), ["circle", {cx: 12, cy: 18.6, r: 1.9, fill: "currentColor"}]],
    crit: [P("M6.8 6.8l10.4 10.4M17.2 6.8L6.8 17.2", {"stroke-width": 3.2, "stroke-linecap": "round"})],
    stale: [P("M9.2 9.3a2.9 2.9 0 1 1 4 2.7c-.8.4-1.2 1-1.2 1.9v.6", {"stroke-width": 2.6, "stroke-linecap": "round"}), ["circle", {cx: 12, cy: 18.3, r: 1.6, fill: "currentColor"}]],
    off: [P("M7 12h10", {"stroke-width": 2.6, "stroke-linecap": "round"})]
  };
  var WORD_IC = {
    ok: [["circle", {cx: 8, cy: 8, r: 6.5, fill: "none", stroke: "currentColor", "stroke-width": 1.6}], P("M5 8.2l2 2 4-4.4", {"stroke-width": 1.8, "stroke-linecap": "round", "stroke-linejoin": "round"})],
    warn: [P("M8 1.8l6.6 12H1.4z", {"stroke-width": 1.6, "stroke-linejoin": "round"}), P("M8 6.3v3.6M8 11.6v.4", {"stroke-width": 1.8, "stroke-linecap": "round"})],
    crit: [["path", {d: "M5.2 1.5h5.6l3.7 3.7v5.6l-3.7 3.7H5.2l-3.7-3.7V5.2z", fill: "currentColor"}], ["path", {d: "M5.8 5.8l4.4 4.4M10.2 5.8l-4.4 4.4", "class": "xk", "stroke-width": 1.8, "stroke-linecap": "round"}]],
    stale: [["circle", {cx: 8, cy: 8, r: 6.5, fill: "none", stroke: "currentColor", "stroke-width": 1.6, "stroke-dasharray": "2.2 1.8"}], P("M6.3 6.4a1.8 1.8 0 1 1 2.5 1.7c-.5.3-.8.6-.8 1.2v.3M8 11.4v.2", {"stroke-width": 1.6, "stroke-linecap": "round"})],
    off: [["circle", {cx: 8, cy: 8, r: 6.5, fill: "none", stroke: "currentColor", "stroke-width": 1.4, "stroke-dasharray": "2.2 1.8"}], P("M5 8h6", {"stroke-width": 1.6, "stroke-linecap": "round"})]
  };
  var AREA_IC = {
    storage: [P("M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3v12c0 1.7-3.6 3-8 3s-8-1.3-8-3zM4 6c0 1.7 3.6 3 8 3s8-1.3 8-3M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3")],
    vmware: [["rect", {x: 3, y: 4, width: 18, height: 6, rx: 1.5}], ["rect", {x: 3, y: 14, width: 18, height: 6, rx: 1.5}], P("M7 7h.01M7 17h.01")],
    ad: [["circle", {cx: 9, cy: 8, r: 3.2}], P("M3 20c.6-3.4 3-5.3 6-5.3s5.4 1.9 6 5.3"), ["circle", {cx: 17, cy: 9, r: 2.4}], P("M16.5 14.2c2.4.2 4 1.8 4.5 4.6")],
    vdi: [["rect", {x: 3, y: 4, width: 18, height: 12, rx: 1.5}], P("M8 20h8M12 16v4"), P("M7.5 10.5l2.5-2.5 2.5 2.5 4-4")],
    tickets: [P("M3 8a2 2 0 002-2h14a2 2 0 002 2v2a2 2 0 000 4v2a2 2 0 00-2 2H5a2 2 0 00-2-2v-2a2 2 0 000-4z"), P("M9.5 6v12", {"stroke-dasharray": "2 2"})],
    list: [P("M4 6h16M4 12h16M4 18h10")]
  };
  if (globalThis.SAWallFleet) AREA_IC.monitor = globalThis.SAWallFleet.icon;
  if (globalThis.SAWallHealth) AREA_IC.health = [P(globalThis.SAWallHealth.icon)];
  function icon(list, vb, cls) {
    var e = s("svg", {viewBox: vb || "0 0 24 24", "aria-hidden": "true", "class": cls || null});
    list.forEach(function (x) { e.appendChild(s(x[0], x[1])); });
    return e;
  }
  function areaIcon(id) {
    var e = icon(AREA_IC[id] || AREA_IC.list, "0 0 24 24", "ai");
    e.setAttribute("fill", "none"); e.setAttribute("stroke", "currentColor"); e.setAttribute("stroke-width", "1.8");
    e.setAttribute("stroke-linecap", "round"); e.setAttribute("stroke-linejoin", "round");
    return e;
  }
  var WORD = {ok: "OK", warn: "Warning", crit: "Critical", stale: "Not current", off: "Not on the wall yet"};
  var GLYPH = {crit: "✕", warn: "!", ok: "✓", stale: "?", none: "–", off: "–"};
  function lamp(st, cls) { return h("span", "lamp s-" + st + (cls ? " " + cls : ""), [icon(LAMP_IC[st] || LAMP_IC.off)], {"aria-hidden": "true"}); }
  function word(st, txt) { return h("span", "word " + st, [icon(WORD_IC[st] || WORD_IC.off, "0 0 16 16"), txt || WORD[st]]); }

  // ---------------------------------------------------------------- time
  var ZONE = "UTC", FMT, FMTHM, FMTD;
  function setZone(z) {
    try { new Intl.DateTimeFormat("en-GB", {timeZone: z}); ZONE = z; } catch (e) { ZONE = "UTC"; }
    FMT = new Intl.DateTimeFormat("en-GB", {timeZone: ZONE, hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false});
    FMTHM = new Intl.DateTimeFormat("en-GB", {timeZone: ZONE, hour: "2-digit", minute: "2-digit", hour12: false});
    FMTD = new Intl.DateTimeFormat("en-GB", {timeZone: ZONE, day: "2-digit", month: "short"});
  }
  setZone("UTC");
  function zoneName() { return ZONE === "UTC" ? "UTC" : ZONE.split("/").pop().replace(/_/g, " "); }
  function hms(t) { return FMT.format(new Date(t)); }
  function hm(t) { return FMTHM.format(new Date(t)); }
  function when(t) { return NOW() - t > 20 * 3600000 ? FMTD.format(new Date(t)) + " " + hm(t) : hm(t); }
  function dur(ms) {
    var m = Math.max(0, Math.round(ms / MIN));
    if (m < 60) return m + " min";
    if (m >= 1440) { var dd = Math.floor(m / 1440), hh = Math.floor((m % 1440) / 60); return dd + " d" + (hh ? " " + hh + " h" : ""); }
    return Math.floor(m / 60) + " h" + (m % 60 ? " " + (m % 60) + " min" : "");
  }
  function ago(sec) {
    sec = Math.max(0, Math.round(sec));
    if (sec < 60) return sec + " s ago";
    if (sec < 3600) { var m = Math.floor(sec / 60), r = sec % 60; return m + " min" + (sec < 600 && r ? " " + r + " s" : "") + " ago"; }
    if (sec < 172800) return Math.floor(sec / 3600) + " h " + Math.floor((sec % 3600) / 60) + " min ago";
    return Math.floor(sec / 86400) + " d ago";
  }
  function pct(v) { return v == null ? "?" : (Math.round(v * 10) / 10).toString(); }
  function size(b) { b = +b || 0; return b >= Math.pow(1024, 4) ? (b / Math.pow(1024, 4)).toFixed(1) + " TB" : Math.round(b / Math.pow(1024, 3)) + " GB"; }

  // ---------------------------------------------------------------- the model, freshness, the one set of rules
  var S = {area: "overview", style: "cockpit", site: ""};
  // 1.135.0: three more styles - Strata, Relay, Metro (static/wall-styles.js; the same model, rule and detail cards)
  var STYLES = ["lights", "cockpit", "map", "strata", "relay", "metro"],
    STYLE_NAME = {lights: "Lights", cockpit: "Cockpit", map: "Map", strata: "Strata", relay: "Relay", metro: "Metro"},
    STYLE_CLASS = {lights: "sL", cockpit: "sK", map: "sM", strata: "sS", relay: "sR", metro: "sX"},
    SCREEN = {strata: 1, relay: 1, metro: 1};       // the styles drawn on a 1920 x 1080 board that fills the screen
  var K_STYLE = "opsWall.style", K_THEME = "site-report-theme";
  function sget(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function sset(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* this page only */ } }
  function fleetRenderer() { return window.SAWallFleet && typeof window.SAWallFleet.render === "function"; }
  function healthRenderer() { return window.SAWallHealth && typeof window.SAWallHealth.render === "function"; }
  function adapt(a) {
    // Codex verify 4: an answer missing a part never throws and never passes - a missing list is empty, a missing feed is
    // not current, a missing state is not "ok"
    function arr(x) { return Array.isArray(x) ? x : []; }
    a.noConds = !Array.isArray(a.conds);                  // Codex r5 #16: no condition list at all - nothing is known, nothing green
    a.conds = arr(a.conds).filter(function (c) { return c && typeof c === "object"; });
    a.feeds = a.feeds && typeof a.feeds === "object" && !Array.isArray(a.feeds) ? a.feeds : {};
    a.areas = arr(a.areas).filter(function (x) {
      return x && typeof x === "object" && (x.id !== "monitor" || fleetRenderer()) && (x.id !== "health" || healthRenderer());
    });
    a.conds.forEach(function (c) { c.start = c.start * 1000; c.end = c.end == null ? null : c.end * 1000; c.seen = c.seen == null ? null : c.seen * 1000; });
    Object.keys(a.feeds).forEach(function (k) {
      var f = a.feeds[k];
      if (!f || typeof f !== "object") { delete a.feeds[k]; return; }
      f.asof = f.asof == null ? null : f.asof * 1000;
    });
    BY = {};
    a.conds.forEach(function (c) { BY[c.id] = c; });
    a.areaById = {};
    a.areas.forEach(function (x) { x.feeds = arr(x.feeds); a.areaById[x.id] = x; });
    return a;
  }
  function LATE() { return (W && W.late) || 180; }
  function STALE() { return (W && W.stale) || 600; }
  var FRESH_TITLE = "Amber (LATE) after 3 min, STALE after 10 min. Stale data is never shown green.";
  function ageS(asof) { return asof == null ? null : Math.max(0, Math.round((NOW() - asof) / 1000)); }
  // 1.136.0: a feed may carry its own limits (the Ops monitor area's SolarWinds when read less often than every minute)
  function fstate(sec, f) {
    var late = f && typeof f.late === "number" ? f.late : LATE(), stale = f && typeof f.stale === "number" ? f.stale : STALE();
    return sec == null ? "stale" : sec > stale ? "stale" : sec > late ? "late" : "ok";
  }
  function feed(k) { return (W && W.feeds && W.feeds[k]) || null; }
  function feedFresh(k) {
    if (UNREACH) return "stale";
    if (k === "-live-") return "ok";
    var f = feed(k);
    if (!f) return "stale";                                         // a feed the answer does not have: not current
    if (f.state !== "ok") return "stale";        // only a feed whose latest read worked is current (never, error, refused, paused, missing: not)
    return fstate(ageS(f.asof), (k === "sw:fleet" || k === "sw:health" || k === "ad:db") ? f : null);
  }
  function areaOf(id) { return W.areaById[id]; }
  function areaOn(id) { var a = areaOf(id); return !!(a && a.on); }
  function areaList() { return W.areas.filter(function (a) { return a.built; }); }
  function aShort(id) { var a = areaOf(id); return a ? (a.short || a.name) : id; }
  function areaFeeds(id) { var a = areaOf(id); return a && Array.isArray(a.feeds) ? a.feeds : []; }
  // a feed that never answered (its login failed, or not read yet) stands for itself as a condition; the area's "as of" is the
  // oldest of the feeds that have data - unless none has
  // Codex verify 2: every feed counts - a vCenter that never returned its inventory is never hidden behind a current one
  function liveFeeds(keys) { return keys; }
  function areaAsof(id) { var m = null; liveFeeds(areaFeeds(id)).forEach(function (k) { var t = feed(k) ? feed(k).asof : null; if (t != null && (m === null || t < m)) m = t; }); return m; }
  function asofOf(k) { var f = feed(k); return f && f.asof != null ? f.asof : 0; }
  function areaFresh(id) { var w = "ok", r = {ok: 0, late: 1, stale: 2}, fs = liveFeeds(areaFeeds(id)); fs.forEach(function (k) { var f = feedFresh(k); if (r[f] > r[w]) w = f; }); return fs.length ? w : "stale"; }
  function greyReasons(id, preferred) {
    var reasons = UNREACH ? ["portal: /api/wall not answering"] : [];
    if (W.noConds) reasons.push("portal: conditions not read");
    var keys = areaFeeds(id);
    if (preferred) keys = [preferred].concat(keys.filter(function (k) { return k !== preferred; }));
    keys.forEach(function (k) {
      var f = feed(k), freshness = feedFresh(k), why;
      if (!f) why = "source missing from the answer";
      else if (f.state !== "ok") why = f.why || ({never: "not read yet", auth: "login refused", error: "not answering", paused: "login paused"}[f.state] || "state unread");
      else if (freshness !== "ok" && !UNREACH) why = f.asof == null ? "not read yet" : freshness + " · last read " + ago(ageS(f.asof));
      else if ((f.unread || []).length) why = f.unread.join(" · ");
      if (why) {
        var name = f ? f.name : k;
        reasons.push(why.indexOf(name + ":") === 0 ? why : name + ": " + why);
      }
    });
    var area = areaOf(id);
    if (!keys.length && area && area.on) reasons.push(area.name + ": source list unread");
    if (!reasons.length && area && area.complete !== true) {
      reasons = (area.incomplete || []).length ? area.incomplete : [area.name + ": inventory coverage unread"];
    }
    if (!reasons.length && openConds(id).some(condStale)) reasons = openConds(id).filter(condStale).slice(0, 3).map(function (c) {
      var f = feed(c.feed);
      return (f ? f.name : c.vc || c.cluster || "source") + ": " + c.t + " (last seen, recovery unread)";
    });
    return reasons.join(" · ");
  }
  function greyGauge(fk, fallback) {
    var f = feed(fk);
    return "Why grey: " + (greyReasons(homeOf(fk || ""), fk) || (f ? f.name : "source unread") + ": " + fallback);
  }
  function greyNote(id) {
    var why = greyReasons(id);
    return why ? h("div", "grey-reason", ["Why grey: " + why]) : null;
  }
  function areaFeedName(id) { var fs = liveFeeds(areaFeeds(id)), w = null; fs.forEach(function (k) { if (!w || asofOf(k) < asofOf(w)) w = k; }); return w ? (feed(w) ? feed(w).name : w + " (not in the answer)") : "no source"; }
  // not current: its source old or failing - or a LAST KNOWN condition (no longer in the read, never seen OK: Codex verify 4)
  function condStale(c) { return UNREACH || !!c.last_known || !!(c.feed && feedFresh(c.feed) === "stale"); }
  // the freshness of every feed (and the portal's): a change redraws the page, so nothing stays green past STALE
  function freshSig() { return (UNREACH ? "U" : "") + (W && W.fleet ? areaState("monitor") : "") + Object.keys((W && W.feeds) || {}).sort().map(function (k) { return k + "=" + feedFresh(k); }).join(","); }
  function openConds(area) { return W.conds.filter(function (c) { return c.end == null && (!area || c.area === area); }); }
  function areaCounts(id) { var n = {crit: 0, warn: 0}; openConds(id).forEach(function (c) { if (!condStale(c)) n[c.sev]++; }); return n; }
  function lastKnown(id) { var n = {crit: 0, warn: 0}; openConds(id).forEach(function (c) { n[c.sev]++; }); return n; }
  // a stale feed is never green: crit / warn from the current conditions; nothing current and a feed stale -> stale
  function areaState(id) {
    if (!areaOn(id)) return "off";
    if (id === "monitor") {
      var f = W.fleet;
      return !UNREACH && !(window.SAWallFleet && window.SAWallFleet.failed()) && f &&
        feedFresh("sw:fleet") === "ok" &&
        Number.isFinite(f.valid_until) && NOW() < f.valid_until * 1000 ? f.state : "stale";
    }
    if (W.noConds) return "stale";
    var n = areaCounts(id);
    if (n.crit) return "crit";
    if (n.warn) return "warn";
    if (openConds(id).some(condStale)) return "stale";
    // LATE never beside a green lamp; and every feed of the area must itself pass the one rule (its own area read whole)
    return areaFresh(id) !== "ok" || areaOf(id).complete !== true || areaFeeds(id).some(function (k) { return status("ok", k) !== "ok"; }) ? "stale" : "ok";
  }
  function overallState() {
    // nothing read yet, no area on: never green
    var r = {crit: 3, warn: 2, stale: 1, ok: 0, off: -1}, w = "off";
    areaList().forEach(function (a) { var st = areaState(a.id); if (r[st] > r[w]) w = st; });
    return w;
  }
  // THE rule for every status the wall draws (Codex verify 1.130.0): green ("ok") only when the check ran, its source is there
  // and current, and the value is OK. A check that did not run, or no source at all: "off" (grey). An old source: "stale".
  // A critical or warning from a source that is there stays what it is (an old one: stale, said "last known").
  function status(st, fk, ran) {
    if (ran === false) return "off";
    if (UNREACH && st !== "off") return "stale";
    if (!fk) return st === "ok" ? "off" : st;
    if (feedFresh(fk) === "stale") return "stale";
    if (st === "ok" && (W.noConds || (fk !== "-live-" && (feedFresh(fk) !== "ok" || !homeOk(fk))))) return "stale";
    return st;
  }
  // a feed's own area (vCenter -> VMware, NetApp -> Storage): green there only while that area's read is complete and every one
  // of its feeds is current (one vCenter not read, one host without a state: nothing of VMware is green)
  function homeOf(fk) { return fk.indexOf("vc:") === 0 ? "vmware" : fk.indexOf("ontap:") === 0 ? "storage" : fk === "sw:fleet" ? "monitor" : ""; }
  function homeOk(fk) {
    var a = homeOf(fk);
    if (!a) return true;
    var ar = W.areaById[a];
    if (!ar || ar.complete !== true) return false;
    var fs = areaFeeds(a);
    if (fs.indexOf(fk) < 0) return false;                                             // a feed its area does not list: never whole
    return Object.keys(W.feeds).every(function (k) { return homeOf(k) !== a || feedFresh(k) === "ok"; }) &&
      fs.every(function (k) { return homeOf(k) !== a || feed(k); });                 // a listed feed missing from the answer: never whole
  }
  function withFresh(st, fk) { return status(st, fk); }
  function countText(n) { var p = []; if (n.crit) p.push(n.crit + " critical"); if (n.warn) p.push(n.warn + " warning"); return p.length ? p.join(" · ") : "No wall condition"; }
  function nOpen(kind) { return openConds().filter(function (c) { return (Array.isArray(kind) ? kind.indexOf(c.kind) >= 0 : c.kind === kind); }); }
  function level(v, w, c) { return v == null || typeof v !== "number" || isNaN(v) ? "stale" : v >= c ? "crit" : v >= w ? "warn" : "ok"; }
  function thr(name) { return W.thresholds[name]; }
  // the Now list: worst first, then longest; a stale area is an item too ("data not current")
  function nowList(area) {
    var items = [];
    openConds(area).forEach(function (c) {
      if (condStale(c)) return;
      items.push({id: c.id, type: "cond", sev: c.sev, area: c.area, c: c, t: c.t, sub: c.obj, since: c.start, site: c.site});
    });
    (area ? [area] : areaList().map(function (a) { return a.id; })).forEach(function (a) {
      if (!areaOn(a)) return;
      areaFeeds(a).forEach(function (k) {
        if (feedFresh(k) !== "stale") return;
        var f = feed(k) || {name: k + " (not in the answer)", asof: null, state: "never"}, n = 0;
        if (f.asof == null && (f.state === "auth" || f.state === "error" || f.state === "paused")) return;     // its own condition says it (login failed / not answering)
        openConds(a).forEach(function (c) { if (c.feed === k) n++; });
        if (items.some(function (i) { return i.id === "feed-" + k; })) return;
        items.push({id: "feed-" + k, type: "feed", feed: k, sev: "stale", area: a, t: f.name + ": data not current",
                    sub: f.name + (f.state === "auth" ? " - login failed, check the credential" : f.state === "error" ? " not answering" : "") + " · last known: " + (n ? n + " open" : "no wall condition"),
                    since: f.asof});
      });
    });
    var rank = function (i) { return i.sev === "crit" ? 10 : i.sev === "stale" ? 15 : 20; };
    return items.sort(function (a, b) { return rank(a) - rank(b) || (a.since || 0) - (b.since || 0); });
  }

  // ---------------------------------------------------------------- freshness marks
  function freshTxt(asof, f) {
    if (asof == null) return {st: "stale", t: "never read"};
    var a = ageS(asof), st = UNREACH ? "stale" : fstate(a, f);
    return {st: st, t: "as of " + hms(asof) + " (" + ago(a) + ")"};
  }
  function freshK(fk, cls) {          // the cockpit's mono line: [STALE] as of 14:20:35 (11 min ago)
    var f = feed(fk), x = freshTxt(f ? f.asof : null, fk === "sw:fleet" ? f : null);
    var e = h("span", (cls || "fresh") + (x.st === "ok" ? "" : " " + x.st), [x.st === "ok" ? null : h("span", "tag", [x.st === "stale" ? "STALE" : "LATE"]), x.t],
              {title: (f ? f.name + (f.why ? " - " + f.why : "") + ". " : "") + FRESH_TITLE});
    e.__fk = fk;
    e.setAttribute("data-fresh", fk || "");
    return e;
  }
  function freshArea(id, cls) {
    var asof = areaAsof(id), x = freshTxt(asof), fs = areaFresh(id);
    var e = h("span", (cls || "fresh") + (fs === "ok" ? "" : " " + fs), [fs === "ok" ? null : h("span", "tag", [fs === "stale" ? "STALE" : "LATE"]), x.t], {title: areaFeedName(id) + ". " + FRESH_TITLE});
    e.setAttribute("data-fresh-area", id);
    return e;
  }
  function repaintFresh() {
    if (!W) return;
    root.querySelectorAll("[data-fresh]").forEach(function (e) {
      var fk = e.getAttribute("data-fresh"), f = feed(fk), x = freshTxt(f ? f.asof : null, fk === "sw:fleet" ? f : null), last = e.lastChild;
      if (last && last.nodeType === 3) last.nodeValue = x.t;
    });
    root.querySelectorAll("[data-fresh-area]").forEach(function (e) {
      var x = freshTxt(areaAsof(e.getAttribute("data-fresh-area"))), last = e.lastChild;
      if (last && last.nodeType === 3) last.nodeValue = x.t;
    });
  }

  // drawing that needs the layout (the map, the timeline): the next frame; the test harness runs them at once (flush)
  var PENDING = null;
  function later(fn) { if (PENDING) PENDING.push(fn); else requestAnimationFrame(fn); }
  function flush() { while (PENDING && PENDING.length) PENDING.shift()(); }

  // ---------------------------------------------------------------- the frame: header, stage, tooltip, detail card
  var hdr, stage, tip, back, card;
  function seg(label, items, cls, attr) {
    var g = h("span", "hseg " + cls + " hide-k", [label ? h("span", "lbl", [label]) : null], {role: "group", "aria-label": label || cls});
    items.forEach(function (it) { var b = h("button", "", [it.icon || null, it.t], {type: "button", title: it.title || null}); b.setAttribute(attr, it.v); g.appendChild(b); });
    return g;
  }
  function frame() {
    clear(root);
    root.className = "wall";
    var brandMark = s("svg", {viewBox: "0 0 16 16", "aria-hidden": "true"}, [s("circle", {cx: 8, cy: 8, r: 5.6, fill: "none", stroke: "currentColor", "stroke-width": 2}), s("circle", {cx: 8, cy: 8, r: 2, fill: "currentColor"})]);
    var styleIc = {
      lights: icon([["circle", {cx: 5, cy: 8, r: 3.2, fill: "currentColor"}], ["circle", {cx: 12, cy: 8, r: 2.2, fill: "none", stroke: "currentColor", "stroke-width": 1.5}]], "0 0 16 16"),
      cockpit: icon([P("M2.5 11.5a5.5 5.5 0 1 1 11 0", {"stroke-width": 1.6, "stroke-linecap": "round"}), P("M8 11.5l2.6-3.6", {"stroke-width": 1.6, "stroke-linecap": "round"})], "0 0 16 16"),
      map: icon([P("M1.5 3.5l4-1.5 5 1.5 4-1.5v10.5l-4 1.5-5-1.5-4 1.5z M5.5 2v10.5 M10.5 3.5V14", {"stroke-width": 1.4, "stroke-linejoin": "round"})], "0 0 16 16"),
      strata: icon([P("M2 3h12v4H2zM2 9h5v4H2zM9 9h5v4H9z", {"stroke-width": 1.4, "stroke-linejoin": "round"})], "0 0 16 16"),
      relay: icon([["circle", {cx: 4, cy: 8, r: 2, fill: "currentColor"}], ["circle", {cx: 8, cy: 8, r: 2, fill: "none", stroke: "currentColor", "stroke-width": 1.4}], ["circle", {cx: 12, cy: 8, r: 2, fill: "none", stroke: "currentColor", "stroke-width": 1.4}]], "0 0 16 16"),
      metro: icon([["rect", {x: 2, y: 2, width: 7, height: 7, fill: "currentColor"}], ["rect", {x: 10.5, y: 2, width: 3.5, height: 7, fill: "none", stroke: "currentColor", "stroke-width": 1.3}], ["rect", {x: 2, y: 10.5, width: 12, height: 3.5, fill: "none", stroke: "currentColor", "stroke-width": 1.3}]], "0 0 16 16")
    };
    var rotPop = h("div", "pop rpop", [
      h("h3", "", ["Rotate for a wall screen"]),
      h("div", "row", [h("label", "", [h("input", "", null, {type: "checkbox", id: "rotOn"}), " Rotate"]),
        h("label", "", ["every ", h("select", "", [h("option", "", ["30 s"], {value: "30"}), h("option", "", ["60 s"], {value: "60"}), h("option", "", ["120 s"], {value: "120"})], {id: "rotEvery"})])]),
      h("div", "row", [h("label", "", [h("input", "", null, {type: "radio", name: "rotMode", value: "styles", id: "rotStyles"}), " rotate styles"]),
        h("label", "", [h("input", "", null, {type: "radio", name: "rotMode", value: "areas", id: "rotAreas"}), " rotate areas"])]),
      h("p", "note", [""], {id: "rotNote"})], {id: "rotPop"});
    hdr = h("header", "hdr", [h("div", "hdr-in", [
      h("div", "brand", [h("span", "mk", [brandMark]), h("b", "", [(W && W.title) || root.getAttribute("data-title") || "Ops center"], {id: "wTitle"}),
        h("span", "enc", [(W && W.enclave) || root.getAttribute("data-enclave") || ""], {id: "encLbl"})]),
      h("span", "karea", [""], {id: "karea"}),
      h("span", "hsep hide-k"),
      h("div", "dd hide-k", [h("button", "hbtn", null, {type: "button", id: "areaBtn", "aria-haspopup": "true", "aria-expanded": "false", title: "Wall area"}),
        h("div", "pop amenu", null, {role: "menu", id: "areaMenu"})], {id: "ddArea"}),
      h("div", "htools", [
        seg("Style", STYLES.map(function (st, i) { return {v: st, t: STYLE_NAME[st], icon: styleIc[st], title: STYLE_NAME[st] + " (" + (i + 1) + ")"}; }), "style", "data-style"),
        h("div", "dd", [h("button", "hbtn", [icon([P("M13 8a5 5 0 1 1-1.6-3.7M13 2.5v3h-3", {"stroke-width": 1.6, "stroke-linecap": "round"})], "0 0 16 16", "ic"),
          h("span", "", ["Rotate"], {id: "rotLbl"}), h("span", "rcd", [""], {id: "rotCd"})],
          {type: "button", id: "rotBtn", "aria-haspopup": "true", "aria-expanded": "false", "aria-pressed": "false", title: "Rotate styles or areas on a wall screen (R)"}), rotPop], {id: "ddRot"}),
        h("span", "hsep hide-k"),
        h("span", "hclock", [""], {id: "clock"}),
        h("button", "hbtn hide-k fbk", ["Feedback"], {type: "button", id: "fbBtn", title: "Tell the wall's team what is good, missing or wrong", hidden: true}),
        h("a", "hbtn hide-k fbk", ["Feedback list"], {id: "fbList", href: "#", hidden: true}),
        h("button", "hbtn hide-k", ["Light"], {type: "button", id: "themeBtn", title: "Light or dark (T)"}),
        h("button", "hbtn hide-k", [icon([P("M2.5 6V2.5H6M10 2.5h3.5V6M13.5 10v3.5H10M6 13.5H2.5V10", {"stroke-width": 1.6})], "0 0 16 16", "ic"), "Kiosk"], {type: "button", id: "kioskBtn", title: "Full screen, controls hidden (K)"}),
        h("button", "hbtn kexit", ["Exit kiosk (Esc)"], {type: "button", id: "kexit"})])])], {id: "hdr"});
    stage = h("div", "", null, {id: "stage"});
    tip = h("div", "", null, {id: "tip", role: "tooltip"});
    card = h("div", "dsc", null, {role: "dialog", "aria-modal": "true", id: "dsCard", "aria-labelledby": "dTitle"});
    back = h("div", "", [card], {id: "dsBack", role: "presentation"});
    add(root, [hdr, stage, tip, back]);
    wire();
  }

  // ---------------------------------------------------------------- header state
  function areaTitle(id) { return id === "overview" ? "Overview" : id === "watch" ? "What the wall watches" : areaOf(id).name; }
  function areaLampState(id) { return id === "overview" ? overallState() : id === "watch" ? null : areaState(id); }
  function renderHeader() {
    $("#wTitle").textContent = W.title;
    $("#encLbl").textContent = W.enclave || "";
    root.querySelectorAll(".hseg.style button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.getAttribute("data-style") === S.style)); });
    var ls = areaLampState(S.area), ab = clear($("#areaBtn"));
    add(ab, [ls ? lamp(ls) : null, h("span", "", [areaTitle(S.area)]), icon([P("M4 6l4 4 4-4", {"stroke-width": 1.8})], "0 0 16 16", "cv")]);
    var m = clear($("#areaMenu"));
    m.appendChild(h("div", "hd", ["Wall areas" + (W.enclave ? " · " + W.enclave : "")]));
    var row = function (id, name, st, extra) {
      return h("a", S.area === id ? "cur" : "", [st ? lamp(st) : h("span"), h("span", "", [name]), st ? h("span", "w " + st, [WORD[st] + (extra ? " · " + extra : "")]) : h("span")],
               {role: "menuitem", tabindex: "0", "data-area": id});
    };
    m.appendChild(row("overview", "Overview", overallState(), ""));
    W.areas.forEach(function (a) {
      if (!a.built) return;
      var st = areaState(a.id), n = areaCounts(a.id), x = st === "stale" ? "data " + (areaAsof(a.id) ? ago(ageS(areaAsof(a.id))).replace(" ago", " old") : "never read") : (n.crit + n.warn ? String(n.crit + n.warn) : "");
      m.appendChild(row(a.id, a.name + (a.sub ? " (" + a.sub + ")" : ""), st, x));
    });
    m.appendChild(h("hr"));
    W.areas.forEach(function (a) { if (!a.built) m.appendChild(h("a", "off", [lamp("off"), h("span", "", [a.name]), h("span", "w off", ["not on the wall yet"])], {role: "menuitem", "aria-disabled": "true"})); });
    m.appendChild(h("hr"));
    m.appendChild(h("a", S.area === "watch" ? "cur" : "", [h("span"), h("span", "", ["What the wall watches"]), h("span", "w off", ["setup"])], {role: "menuitem", tabindex: "0", "data-area": "watch"}));
    var dark = document.documentElement.getAttribute("data-theme") === "dark" || (!document.documentElement.getAttribute("data-theme") && matchMedia && matchMedia("(prefers-color-scheme: dark)").matches);
    $("#themeBtn").textContent = dark ? "Light" : "Dark";
    $("#karea").textContent = areaTitle(S.area) + " · " + STYLE_NAME[S.style];
    var me = W.me || {};
    if (me.feedback) $("#fbBtn").removeAttribute("hidden"); else $("#fbBtn").setAttribute("hidden", "");
    if (me.reader) { $("#fbList").removeAttribute("hidden"); $("#fbList").setAttribute("href", SRC.replace(/api\/wall$/, "wall/feedback")); } else $("#fbList").setAttribute("hidden", "");
    tickClock();
    renderRot();
  }
  function tickClock() { var c = clear($("#clock")); add(c, [hms(NOW()), h("small", "", [zoneName() + " · refresh every " + ((W && W.poll) || 30) + " s"])]); }

  // ---------------------------------------------------------------- render
  var RENDER = {};
  function render() {
    if (!W) return;
    if (S.area === "monitor" && !fleetRenderer()) S.area = "overview";
    if (S.area === "health" && !healthRenderer()) S.area = "overview";
    var de = document.documentElement;
    STYLES.forEach(function (st) { de.classList.remove("style-" + st); });
    de.classList.add("style-" + S.style);
    de.classList.toggle("freeze", FREEZE);
    de.classList.toggle("fit", S.style === "map" && S.area !== "watch" && innerWidth >= 1600 && innerHeight >= 960);
    de.classList.toggle("screen", screenOn());
    if (globalThis.SAWallFleet) de.classList.toggle("area-monitor", S.area === "monitor");
    if (globalThis.SAWallHealth) de.classList.toggle("area-health", S.area === "health");
    if (!screenOn()) stage.style.height = "";
    stage.className = STYLE_CLASS[S.style];
    clear(stage);
    tip.classList.remove("on");
    renderHeader();
    // 1.136.0: Ops monitor; 1.139.0: Network & AD health area (static/wall-health.js)
    if (monArea()) window.SAWallFleet.render(API, stage);
    else if (healthArea()) window.SAWallHealth.render(API, stage);
    else RENDER[S.style](stage);
    if (screenOn()) fitScreen();
    if (UNREACH) stage.insertBefore(h("div", "unreach", [lamp("stale"), h("b", "", ["Portal not reachable"]),
      " since " + hms(UNREACH_SINCE) + " - every value below is the last known one and not current. The page keeps trying every " + ((W && W.poll) || 30) + " s."],
      {role: "alert"}), stage.firstChild);
    LASTF = freshSig();
    if (openRef && openRef !== "fb:") openDetail(openRef, true);
    if (QS.get("check") === "1") setTimeout(function () {
      root.setAttribute("data-check", JSON.stringify({style: S.style, area: S.area, sw: document.documentElement.scrollWidth, iw: innerWidth})); }, 300);
  }
  function canonicalArea(id) { return id === "fleet" && globalThis.SAWallFleet ? "monitor" : id; }
  function setArea(id) {
    id = canonicalArea(id);
    if (id !== "overview" && id !== "watch" && !areaOn(id)) id = "overview";
    S.area = id; S.site = ""; closePops(); closeDetail(); render();
  }
  // the styles drawn on a 1920 x 1080 board that fills the screen - and the Ops monitor area in every style (1.136.0)
  function screenOn() { return S.area !== "watch" && !healthArea() && (!!SCREEN[S.style] || monArea()); }
  function monArea() { return S.area === "monitor" && fleetRenderer(); }   // only with the monitor on (its script loaded)
  function healthArea() { return S.area === "health" && healthRenderer(); } // 1.139.0: Network & AD health area
  function setStyle(st, user) { if (STYLES.indexOf(st) < 0) return; S.style = st; if (user) sset(K_STYLE, st); closePops(); render(); }
  function closePops() { ["#ddArea", "#ddRot"].forEach(function (sel) { var d = $(sel); if (d) { d.classList.remove("open"); d.querySelector("button").setAttribute("aria-expanded", "false"); } }); }
  function togglePop(sel) { var d = $(sel), o = !d.classList.contains("open"); closePops(); if (o) { d.classList.add("open"); d.querySelector("button").setAttribute("aria-expanded", "true"); } }
  function setKiosk(on, user) {
    document.body.classList.toggle("kiosk", on);
    if (SCREEN[S.style] || monArea()) fitScreen(); // 1.135.0: the board takes the room the page now leaves
    if (user) { try { if (on && document.documentElement.requestFullscreen) document.documentElement.requestFullscreen().catch(function () {}); else if (!on && document.fullscreenElement) document.exitFullscreen(); } catch (e) { /* not allowed here */ } }
  }
  var ROT = {on: false, mode: "styles", every: 60, left: 60, resumeAt: 0}, IDLE_RESUME = 20;
  function renderRot() {
    $("#rotOn").checked = ROT.on; $("#rotEvery").value = String(ROT.every);
    $("#rotStyles").checked = ROT.mode === "styles"; $("#rotAreas").checked = ROT.mode === "areas";
    $("#rotBtn").setAttribute("aria-pressed", String(ROT.on));
    $("#rotLbl").textContent = ROT.on ? (ROT.mode === "styles" ? "Rotate styles" : "Rotate areas") : "Rotate";
    var cd = $("#rotCd");
    if (!ROT.on) { cd.textContent = "off"; cd.className = "rcd"; }
    else if (ROT.resumeAt) { cd.textContent = "paused - resumes in " + Math.max(0, Math.ceil((ROT.resumeAt - Date.now()) / 1000)) + " s"; cd.className = "rcd paused"; }
    else { cd.textContent = "next in " + ROT.left + " s"; cd.className = "rcd on"; }
    $("#rotNote").textContent = (ROT.mode === "styles" ? "Styles: " + STYLES.map(function (x) { return STYLE_NAME[x]; }).join(", ") + " on the same area (" + areaTitle(S.area) + "). " :
      "Areas: " + ["Overview"].concat(areaList().filter(function (a) { return a.on; }).map(function (a) { return a.short || a.name; })).join(", ") + " in " + STYLE_NAME[S.style] + ". ") +
      "Clicks, keys, scrolling or touch pause rotation. In kiosk mode only keys pause it. Rotation resumes after " + IDLE_RESUME + " s.";
  }
  function rotStep() {
    if (ROT.mode === "styles") setStyle(STYLES[(STYLES.indexOf(S.style) + 1) % STYLES.length], false);
    else { var seq = ["overview"].concat(areaList().filter(function (a) { return a.on; }).map(function (a) { return a.id; })), i = seq.indexOf(S.area); setArea(seq[(i + 1) % seq.length]); }
  }
  function rotateShortcut(e) {
    return (e.key === "r" || e.key === "R") && !(e.target.closest && e.target.closest("select,input,textarea"));
  }
  function activity(e) {
    if (!ROT.on || $("#ddRot").contains(e.target)) return;
    if (e.type === "keydown") {
      if (rotateShortcut(e) || ["Shift", "Control", "Alt", "AltGraph", "Meta", "CapsLock", "NumLock", "ScrollLock",
          "Fn", "FnLock", "Symbol", "SymbolLock", "Hyper", "Super"].indexOf(e.key) >= 0) return;
    } else if (document.body.classList.contains("kiosk")) return;
    ROT.resumeAt = Date.now() + IDLE_RESUME * 1000;
    renderRot();
  }
  function wire() {
    $("#areaBtn").addEventListener("click", function (e) { e.stopPropagation(); togglePop("#ddArea"); });
    $("#rotBtn").addEventListener("click", function (e) { e.stopPropagation(); togglePop("#ddRot"); });
    $("#areaMenu").addEventListener("click", function (e) { var a = e.target.closest("[data-area]"); if (a) setArea(a.getAttribute("data-area")); });
    $("#areaMenu").addEventListener("keydown", function (e) { var a = e.target.closest("[data-area]"); if (a && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); setArea(a.getAttribute("data-area")); } });
    $("#rotPop").addEventListener("click", function (e) { e.stopPropagation(); });
    document.addEventListener("click", function (e) { if (!e.target.closest || !e.target.closest(".dd")) closePops(); });
    root.querySelectorAll(".hseg.style button").forEach(function (b) { b.addEventListener("click", function () { setStyle(b.getAttribute("data-style"), true); }); });
    $("#themeBtn").addEventListener("click", function () {
      var de = document.documentElement, dark = $("#themeBtn").textContent === "Light", t = dark ? "light" : "dark";
      de.setAttribute("data-theme", t); sset(K_THEME, t);
      var r = document.querySelector('#theme input[value="' + t + '"]'); if (r) r.checked = true;
      render();
    });
    $("#kioskBtn").addEventListener("click", function () { setKiosk(true, true); });
    $("#fbBtn").addEventListener("click", function () { openFeedback(""); });
    $("#kexit").addEventListener("click", function () { setKiosk(false, true); });
    $("#rotOn").addEventListener("change", function () { ROT.on = this.checked; ROT.left = ROT.every; ROT.resumeAt = 0; renderRot(); });
    $("#rotEvery").addEventListener("change", function () { ROT.every = +this.value; ROT.left = ROT.every; renderRot(); });
    root.querySelectorAll('input[name="rotMode"]').forEach(function (r) { r.addEventListener("change", function () { ROT.mode = this.value; ROT.left = ROT.every; renderRot(); }); });
    ["mousedown", "click", "keydown", "wheel", "touchstart"].forEach(function (t) { document.addEventListener(t, activity, {capture: true, passive: true}); });
    // one click handler for the whole stage: a detail ref wins, then an area link
    stage.addEventListener("click", function (e) {
      var d = e.target.closest("[data-d]");
      if (d && stage.contains(d)) { e.preventDefault(); openDetail(d.getAttribute("data-d")); return; }
      var g = e.target.closest("[data-goto]");
      if (g) { e.preventDefault(); setArea(g.getAttribute("data-goto")); }
    });
    stage.addEventListener("keydown", function (e) {
      if (e.key !== "Enter" && e.key !== " ") return;
      if (e.target.closest("button,a[href]")) return;    // a real button or link (Share to Teams in a row) does its own click
      var d = e.target.closest("[data-d]"), g = e.target.closest("[data-goto]");
      if (d) { e.preventDefault(); openDetail(d.getAttribute("data-d")); } else if (g) { e.preventDefault(); setArea(g.getAttribute("data-goto")); }
    });
    back.addEventListener("click", function (e) {
      if (e.target === back) { closeDetail(); return; }
      var d = e.target.closest("[data-d]");
      if (d) { e.preventDefault(); openDetail(d.getAttribute("data-d")); return; }
      var sh = e.target.closest("[data-share]");
      if (sh && BY[sh.getAttribute("data-share")]) { openShare(sh.getAttribute("data-share")); return; }
      var fb = e.target.closest("[data-fb]");
      if (fb) { var t = card.querySelector("h2"); openFeedback((t ? t.textContent : "") + " (" + fb.getAttribute("data-fb") + ")", fb.getAttribute("data-fb-area") || "general"); return; }
      if (e.target.closest(".x")) closeDetail();
    });
    document.addEventListener("keydown", function (e) {
      if (e.target.closest && e.target.closest("select,input,textarea")) return;
      if (e.key === "Escape") { if (closeDetail()) return; closePops(); if (document.body.classList.contains("kiosk")) setKiosk(false, true); if (S.site) { S.site = ""; render(); } }
      else if (/^[1-6]$/.test(e.key)) setStyle(STYLES[+e.key - 1], true);
      else if (e.key === "k" || e.key === "K") setKiosk(!document.body.classList.contains("kiosk"), true);
      else if (e.key === "t" || e.key === "T") $("#themeBtn").click();
      else if (rotateShortcut(e)) togglePop("#ddRot");
    });
    document.addEventListener("pointermove", function (e) {
      if (!e.target.closest) return;
      var el = e.target.closest("[data-tip]");
      if (el) { var p = el.getAttribute("data-tip").split("|"); showTip(p, e.clientX, e.clientY); return; }
      var sp = e.target.closest("svg[data-spark]");
      if (sp) {
        var a = sp.getAttribute("data-spark").split(","), r = sp.getBoundingClientRect(), i = Math.max(0, Math.min(a.length - 1, Math.round((e.clientX - r.left) / r.width * (a.length - 1))));
        showTip([a[i] === "" ? "no sample" : a[i] + " %", (a.length - 1 - i === 0 ? "now" : (a.length - 1 - i) + " min ago")], e.clientX, e.clientY); return;
      }
      tip.classList.remove("on");
    });
    var rt;
    document.addEventListener("fullscreenchange", function () { if (SCREEN[S.style] || monArea()) fitScreen(); });
    window.addEventListener("resize", function () { clearTimeout(rt); rt = setTimeout(function () { if (S.style === "map" && !monArea()) render(); else if (SCREEN[S.style] || monArea()) fitScreen(); }, 150); });
  }
  function showTip(lines, x, y) {
    clear(tip);
    lines.filter(Boolean).forEach(function (l, i) { tip.appendChild(h(i ? "div" : "b", "", [l])); });
    tip.classList.add("on");
    var w = tip.offsetWidth;
    tip.style.left = Math.min(x + 14, innerWidth - w - 8) + "px";
    tip.style.top = (y + 14) + "px";
  }

  // ---------------------------------------------------------------- the detail card: every element opens one
  var openRef = "", lastFocus = null;
  function fullLink(id) {
    var u = W.full && W.full[id];
    if (!u) return null;
    return h("a", "fullp", ["Full page ", h("span", "", ["↗"], {"aria-hidden": "true"})], {href: u, title: "opens the full " + aShort(id) + " page"});
  }
  function kv(rows) {
    var t = h("div", "dkv");
    rows.forEach(function (r) { if (!r) return; t.appendChild(h("div", "k", [r[0]])); t.appendChild(h("div", "v", [r[1]])); });
    return t;
  }
  function vsBar(v, w, c, st) {
    // the value against its marks: one bar, the warning and critical marks, numbers as text beside it
    var svg = s("svg", {viewBox: "0 0 100 10", preserveAspectRatio: "none", "class": "vsb", role: "img", "aria-label": pct(v) + " % against warning " + w + ", critical " + c});
    svg.appendChild(s("rect", {x: 0, y: 1, width: 100, height: 8, rx: 1.5, "class": "trk"}));
    if (v != null) svg.appendChild(s("rect", {x: 0, y: 1, width: Math.max(0.5, Math.min(100, v)), height: 8, rx: 1.5, "class": "fl f-" + st}));
    [w, c].forEach(function (m) { if (m != null && m <= 100) svg.appendChild(s("line", {x1: m, x2: m, y1: 0, y2: 10, "class": "mk"})); });
    return h("div", "vs", [svg, h("div", "vax", [h("span", "", ["0"]), w != null ? h("span", "", ["warning " + w]) : null, h("span", "", ["critical " + c]), h("span", "", ["100"])])]);
  }
  function objList(names, max, kind) {
    max = max || 60;
    if (!names || !names.length) return h("span", "muted", ["none"]);
    var ul = h("ul", "dlist");
    names.slice(0, max).forEach(function (n) { ul.appendChild(h("li", "", [kind ? h("button", "lnk", [n], {type: "button", "data-d": kind + n}) : n])); });
    if (names.length > max) ul.appendChild(h("li", "muted", ["+" + (names.length - max) + " more"]));
    return ul;
  }
  function srcRow(fk) {
    var f = feed(fk);
    if (!f) return ["Source", "-"];
    var x = freshTxt(f.asof);
    return ["Source", h("span", "", [f.name + " · " + (f.src || ""), h("br"), h("span", "fr " + (x.st === "ok" ? "" : x.st), [x.st === "ok" ? "" : (x.st === "stale" ? "STALE · " : "LATE · "), x.t]),
      f.state !== "ok" ? h("span", "bad", [" · " + (f.why || f.state || "not read")]) : null])];
  }
  function dHead(title, st, chips, fk) {
    var x = h("button", "x", ["×"], {type: "button", "aria-label": "Close (Esc)", title: "Close (Esc)"});
    return h("div", "dh", [h("div", "", [h("h2", "", [title], {id: "dTitle"}),
      h("div", "meta", (chips || []).map(function (c) { return h("span", "chipt", [c]); }).concat([st ? wordWd(st) : null, fk ? freshK(fk, "fr") : null]))]), x]);
  }
  function wordWd(st) { return h("span", "wd " + st, [icon(WORD_IC[st] || WORD_IC.off, "0 0 16 16"), WORD[st]]); }
  function dFoot(area, extra) {
    var f = area ? fullLink(area) : null, fb = null;
    if (W.me && W.me.feedback) fb = h("button", "fbon kxh", ["Feedback on this card"], {type: "button", "data-fb": openRef, "data-fb-area": area || "general"});
    return h("div", "df", [f, extra || null, fb, h("span", "lg", ["Esc closes · every number here is the wall's own read"])]);
  }
  // ---------------------------------------------------------------- Feedback: a short form, saved on the portal server
  function token() { var m = document.querySelector('meta[name="portal-token"]'); return m ? m.getAttribute("content") : ""; }
  function openFeedback(ref, area) {
    var me = W.me || {};
    if (!me.feedback) return;
    if (!openRef) lastFocus = document.activeElement;
    openRef = "fb:";
    clear(card);
    var sel = h("select", "", (me.areas || []).map(function (a) { return h("option", "", [a[1]], {value: a[0]}); }), {id: "fbArea"});
    sel.value = area || (S.area === "watch" ? "general" : S.area);
    if (sel.selectedIndex < 0) sel.value = "general";
    var ta = h("textarea", "", null, {id: "fbText", rows: 6, maxlength: me.max || 1000, placeholder: "What is good, what is missing, what is wrong - one box is enough."});
    var cnt = h("span", "fbn", ["0 / " + (me.max || 1000)]);
    ta.addEventListener("input", function () { cnt.textContent = ta.value.length + " / " + (me.max || 1000); });
    var msg = h("p", "fbmsg", [""], {role: "status"});
    var send = h("button", "fbsend", ["Send"], {type: "submit"});
    var form = h("form", "fbform", [h("label", "", ["Area ", sel]), ref ? h("p", "muted", ["About: " + ref]) : null,
      h("label", "", ["Your feedback ", ta]), h("div", "fbrow", [cnt, send]), msg]);
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      send.disabled = true;
      msg.textContent = "Sending…";
      fetch(SRC + "/feedback", {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json", "X-Portal-Token": token()},
        body: JSON.stringify({area: sel.value, text: ta.value, ref: ref || ""})}).then(function (r) {
        return r.json().then(function (j) { return {ok: r.ok, j: j}; });
      }).then(function (x) {
        if (x.ok) { msg.textContent = "Thank you - saved."; ta.value = ""; setTimeout(closeDetail, 1200); }
        else { msg.textContent = (x.j && x.j.error) || "Not saved."; send.disabled = false; }
      }).catch(function () { msg.textContent = "Not saved: the portal did not answer."; send.disabled = false; });
    });
    add(card, [dHead("Feedback on the wall", null, ["saved on the portal server with your name and the time · no email"]), h("div", "dbody", [form])]);
    back.classList.add("on");
    ta.focus();
  }
  function condBody(c) {
    var k = W.kinds[c.kind] || {}, st = c.end != null ? status("ok", c.feed || "-live-") : condStale(c) ? "stale" : c.sev, rows = [];
    rows.push(["What is wrong", h("span", "", [h("b", "", [c.t]), h("br"), c.obj || ""])]);
    rows.push(["Wall rule", k.label + " (" + (c.sev === "crit" ? "critical" : "warning") + ")"]);
    if (c.last_known) rows.push(["Not current", h("span", "", [word("stale", "Last seen " + hms(c.seen || c.start)), " · not in the latest read and not seen OK since - last known, never cleared by missing data"])]);
    rows.push(["Since", c.end != null ? hms(c.start) + " → " + hms(c.end) + " · lasted " + dur(c.end - c.start) + " (cleared)" :
      when(c.start) + " · for " + dur(NOW() - c.start) + (c.kind === "cpu" || c.kind === "mem" ? " (start of the run at or over the line)" : " (first seen by the wall)")]);
    if (c.v != null) {
      var t = c.kind === "dsfull" || c.kind === "dswarn" ? thr("ds") : c.kind === "aggrfull" || c.kind === "aggrwarn" ? thr("aggr") : [null, thr("high")[0]];
      rows.push(["Now vs threshold", h("div", "", [h("b", "", [pct(c.v) + " %"]), vsBar(c.v, t[0], t[1], st)])]);
    }
    var host = c.host ? hostBy(c.vc, c.host) : null;
    if (c.kind === "host" && host) {
      rows.push(["VMs on it", objList(host.vms, 80)]);
      rows.push(["Datastores on it", objList(host.ds, 40, "ds:" + host.vc + "|")]);
    } else if (host) {
      rows.push(["Host", h("button", "lnk", [host.n], {type: "button", "data-d": "host:" + host.vc + "|" + host.n})]);
    }
    if (c.ds) {
      var d = dsBy((c.vc ? c.vc + "|" : "") + c.ds);
      if (d) rows.push(["Datastore", h("span", "", [h("button", "lnk", [c.ds + " · storage path"], {type: "button", "data-d": dsRef(c.ds, c.vc || "")}),
        " · " + (d.hosts || []).length + " hosts · " + (d.nvms || 0) + " VMs"])]);
    }
    if (c.kind === "vc") rows.push(["Affected", "every host, VM and datastore of vCenter " + c.vc + " shows its last known values, grey"]);
    rows.push(srcRow(c.feed || (c.vc ? "vc:" + c.vc : "")));
    rows.push(["Where to verify", verify(c)]);
    return {title: c.t, st: st, chips: [aShort(c.area), c.site || null].filter(Boolean), fk: c.feed, rows: rows, area: c.area};
  }
  function verify(c) {
    if (c.kind === "vc") return "vSphere Client: https://" + c.vc + "/ui (does it answer, does the account log in?)";
    if (c.kind === "hardware") return "vSphere Client › Host › Monitor › Issues and Alarms";
    if (c.kind === "host" || c.kind === "maint") return "vSphere Client of " + c.vc + " › cluster " + (c.cluster || "-") + " › host " + c.host + " › Summary (connection state, maintenance)";
    if (c.kind === "dsi" || c.kind === "dsfull" || c.kind === "dswarn") return c.vc ? "vSphere Client of " + c.vc + " › Storage › datastore " + c.ds + " › Monitor / Capacity" : "NetApp page › the datastore " + c.ds;
    if (c.kind === "vmhung") return "vSphere Client of " + c.vc + " › VM " + c.vm + " › Summary (VMware Tools heartbeat), console";
    if (c.kind === "cpu" || c.kind === "mem") return "vSphere Client of " + c.vc + " › " + (c.obj_kind === "vm" ? "VM " : "host ") + c.obj_name + " › Monitor › Performance (" + (c.kind === "cpu" ? "CPU" : "memory") + ", last hour)";
    if (c.kind === "aggrfull" || c.kind === "aggrwarn") return "ONTAP System Manager of " + (c.cluster || "the cluster") + " › Storage › Tiers › " + c.aggr;
    if (c.kind === "sm") return "ONTAP System Manager of " + (c.cluster || "the destination cluster") + " › Protection › Relationships";
    if (c.kind === "node") return "ONTAP System Manager of " + (c.cluster || "the cluster") + " › Cluster › Overview (nodes, takeover)";
    if (c.kind === "fleetdown" || c.kind === "fleetwarn") return "SolarWinds › My Dashboards › Groups › " + (c.group || "the group") + " (each node's Node Details); the wall's Ops monitor area";
    return "-";
  }
  function hostBy(vc, n) { var r = null; W.vmware.hosts.forEach(function (x) { if (x.n === n && (!vc || x.vc === vc)) r = x; }); return r; }
  // a datastore is its vCenter and its name (two vCenters may each have one of a name): "ds:<vCenter>|<name>", or "ds:<name>"
  function dsRef(d, vc) { return "ds:" + ((vc != null ? vc : d.vc) ? (vc != null ? vc : d.vc) + "|" : "") + (d.n != null ? d.n : d); }
  function dsKey(ref) { var i = ref.indexOf("|"); return i < 0 ? {vc: "", n: ref} : {vc: ref.slice(0, i), n: ref.slice(i + 1)}; }
  function dsBy(ref) { var k = dsKey(ref), r = null; (W.storage.configured ? W.vmware.datastores : W.storage.ds || []).forEach(function (x) { if (x.n === k.n && (!k.vc || x.vc === k.vc) && !r) r = x; }); return r; }
  // datastore names -> links by vCenter and name (a name two vCenters have: one link each)
  function dsLinks(names) {
    var refs = [];
    names.forEach(function (n) {
      var m = (W.storage.ds || []).filter(function (d) { return d.n === n; });
      if (!m.length) refs.push({t: n, r: "ds:" + n});
      m.forEach(function (d) { refs.push({t: n + (m.length > 1 && d.vc ? " (" + d.vc + ")" : ""), r: dsRef(d)}); });
    });
    if (!refs.length) return h("span", "muted", ["none"]);
    return h("ul", "dlist", refs.slice(0, 40).map(function (x) { return h("li", "", [h("button", "lnk", [x.t], {type: "button", "data-d": x.r})]); }));
  }
  function condsOn(pred) { return W.conds.filter(function (c) { return c.end == null && pred(c); }); }
  function condRows(list, fk, ran) {
    // none open: "None now" in green only when the check ran and its source is current (the one rule)
    if (!list.length) { var z = status("ok", fk, ran); return h("span", "", [word(z, z === "ok" ? "None now" : z === "off" ? "Not checked here" : null)]); }
    var ul = h("ul", "dlist conds");
    list.forEach(function (c) { var st = condStale(c) ? "stale" : c.sev; ul.appendChild(h("li", "", [h("button", "lnk " + st, [GLYPH[st] + " " + c.t], {type: "button", "data-d": "c:" + c.id}), c.kind === "hardware" ? h("span", "", [" · " + c.obj]) : null, h("span", "muted", [c.last_known ? " · last seen " + hms(c.seen || c.start) : " · " + dur(NOW() - c.start)])])); });
    return ul;
  }
  function exclusions(kinds) {
    var objects = (W.vmware.excluded || []).concat(W.storage.excluded || []);
    var rows = objects.filter(function (x) { return kinds.indexOf(x.kind) >= 0; });
    return rows.length ? ["Outside this watch", objList(rows.map(function (x) { return x.source + " · " + x.name + " · " + x.reason; }), rows.length)] : null;
  }
  var DET = {
    kind: function (kind) {
      var rule = W.kinds[kind], list = nOpen(kind);
      if (!rule) return null;
      var current = list.filter(function (c) { return !condStale(c); });
      var fk = rule.area === "vmware" ? vcFeed() : ntFeed();
      return {title: rule.label, area: rule.area, st: current.length ? (current.some(function (c) { return c.sev === "crit"; }) ? "crit" : "warn") : "stale",
              rows: [["Open conditions", condRows(list, fk)], exclusions(kind === "voloff" ? ["volumes"] : [])]};
    },
    c: function (id) { var c = BY[id]; return c ? condBody(c) : null; },
    host: function (ref) {
      var p = ref.split("|"), x = hostBy(p[0], p[1]);
      if (!x) return null;
      var st = withFresh(x.st, x.feed), cs = condsOn(function (c) { return c.vc === x.vc && (c.host === x.n || c.obj_name === x.n); });
      var hp = thr("high")[0];
      return {title: x.n, st: st, chips: ["Host", "cluster " + (x.cluster || "-"), "vCenter " + x.vc], fk: x.feed, area: "vmware", rows: [
        ["State", x.state + (x.maint ? " · in maintenance mode" : "") + (x.power ? " · " + x.power : "")],
        ["Wall conditions", condRows(cs, x.feed)],
        ["CPU now", h("div", "", [h("b", "", [pct(x.cpu) + " %"]), vsBar(x.cpu, null, hp, withFresh(level(x.cpu, 101, hp), x.feed))])],
        ["Memory now", h("div", "", [h("b", "", [pct(x.mem) + " %"]), vsBar(x.mem, null, hp, withFresh(level(x.mem, 101, hp), x.feed))])],
        ["VMs on it (" + x.nvms + ")", objList(x.vms, 80)],
        ["Datastores on it", objList(x.ds, 40, "ds:" + x.vc + "|")],
        srcRow(x.feed),
        ["Where to verify", "vSphere Client of " + x.vc + " › cluster " + (x.cluster || "-") + " › host " + x.n + " › Summary / Monitor"]]};
    },
    cl: function (ref) {
      var p = ref.split("|"), cl = null;
      W.vmware.clusters.forEach(function (x) { if (x.vc === p[0] && x.n === p[1]) cl = x; });
      if (!cl) return null;
      var m = p[2] || "hosts", hp = thr("high");
      var hs = W.vmware.hosts.filter(function (x) { return x.vc === cl.vc && (x.cluster || "(standalone)") === cl.n; });
      hs.sort(function (a, b) { return (m === "mem" ? (b.mem || 0) - (a.mem || 0) : m === "cpu" ? (b.cpu || 0) - (a.cpu || 0) : (a.st === "ok") - (b.st === "ok")); });
      var tb = h("table", "dtab", [h("thead", "", [h("tr", "", [h("th", "", ["Host"]), h("th", "", ["State"]), h("th", "n", ["CPU %"]), h("th", "n", ["Memory %"]), h("th", "n", ["VMs"])])])]), body = h("tbody");
      hs.forEach(function (x) {
        var st = withFresh(x.st, x.feed);
        body.appendChild(h("tr", st, [h("td", "", [h("button", "lnk", [x.n], {type: "button", "data-d": "host:" + x.vc + "|" + x.n})]), h("td", "", [wordWd(st)]),
          h("td", "n", [pct(x.cpu)]), h("td", "n", [pct(x.mem)]), h("td", "n", [String(x.nvms)])]));
      });
      tb.appendChild(body);
      var v = m === "cpu" ? cl.cpu : m === "mem" ? cl.mem : null;
      var st = m === "hosts" ? (cl.down.length ? withFresh("crit", cl.feed) : (cl.unknown || []).length ? "stale" : withFresh("ok", cl.feed)) : withFresh(level(v, 101, hp[0]), cl.feed);
      return {title: (m === "cpu" ? "CPU · " : m === "mem" ? "Memory · " : "Hosts · ") + cl.n, st: st, chips: ["Cluster", "vCenter " + cl.vc], fk: cl.feed, area: "vmware", rows: [
        m === "hosts" ? ["Hosts up", cl.up + " of " + cl.of + (cl.down.length ? " · not responding: " + cl.down.join(", ") : "") + (cl.maint.length ? " · maintenance: " + cl.maint.join(", ") : "")] :
          ["Now vs threshold", h("div", "", [h("b", "", [pct(v) + " %"]), " average of the connected hosts · the wall flags a host or VM at " + hp[0] + " % for " + hp[1] + " min", vsBar(v, null, hp[0], st)])],
        ["Wall conditions", condRows(condsOn(function (c) { return c.cluster === cl.n || (c.host && hs.some(function (x) { return x.n === c.host; })); }), cl.feed)],
        ["Hosts", tb], srcRow(cl.feed),
        ["Where to verify", "vSphere Client of " + cl.vc + " › cluster " + cl.n + " › Monitor › " + (m === "hosts" ? "Issues and Alarms" : "Performance")]]};
    },
    aggr: function (ref) {
      var a = null;
      W.storage.aggr.forEach(function (x) { if (x.ref === ref) a = x; });
      if (!a) return null;
      var t = thr("aggr"), st = withFresh(level(a.v, t[0], t[1]), a.feed);
      var dss = (W.storage.ds || []).filter(function (d) { var p = W.storage.paths[d.n]; return p && p.rows.some(function (r) { return r.aggr === a.n && r.cluster === a.c; }); }).map(function (d) { return d.n; });
      return {title: a.n, st: st, chips: ["Aggregate", "cluster " + a.c, "node " + a.node], fk: a.feed, area: "storage", rows: [
        ["Now vs threshold", h("div", "", [h("b", "", [pct(a.v) + " % used"]), " · " + size(a.free) + " free of " + size(a.size), vsBar(a.v, t[0], t[1], st)])],
        ["Wall conditions", condRows(condsOn(function (c) { return c.ref === a.ref; }), a.feed)],
        ["Datastores on it", dsLinks(dss)],
        srcRow(a.feed), ["Where to verify", "ONTAP System Manager of " + a.c + " › Storage › Tiers › " + a.n]]};
    },
    ctr: function (k) {
      var def = CTR_DEF[k];
      if (!def) return null;
      var unk = k === "hosts" && W.vmware.hosts.some(function (x) { return x.st === "stale"; });
      var ranK = k === "sm" ? W.storage.ran.sm : k === "nodes" ? W.storage.ran.nodes : k === "hung" ? W.thresholds.vm_heartbeat && W.vmware.heartbeat_watched > 0 : k === "hardware" ? W.vmware.hosts.length > 0 : undefined;
      var list = nOpen(def.kinds), st = !list.length && unk ? "stale" : list.length ? (list.some(function (c) { return c.sev === "crit" && !condStale(c); }) ? "crit" : list.every(condStale) ? "stale" : "warn") : status("ok", def.feed(), ranK);
      return {title: def.l, st: st, chips: [aShort(def.area), list.length + " open"], fk: def.feed(), area: def.area, rows: [
        ["Counts", def.what], k === "sm" && (W.storage.smUnknown || []).length ? ["Unknown lag", objList(W.storage.smUnknown)] : null, k === "hung" ? ["Heartbeat coverage", "not watched: " + W.vmware.heartbeat_unwatched] : null,
        exclusions(({hosts: ["hosts"], hardware: ["hosts"], maint: ["hosts"], hung: ["vms"], hot: ["hosts", "vms"], ds: ["datastores"],
                     dsi: ["datastores"], aggr: ["aggregates", "volumes"]})[k] || []), ["Open now", unk && !list.length ? h("span", "", [word("stale", "Not all hosts read")]) : condRows(list, def.feed(), ranK)], srcRow(def.feed()), ["Where to verify", def.verify]]};
    },
    feed: function (k) {
      var f = feed(k);
      if (!f) return null;
      var x = freshTxt(f.asof, k === "sw:fleet" ? f : null), st0 = x.st === "stale" ? "stale" : f.state === "ok" || f.state === "never" ? (x.st === "late" ? "warn" : "ok") : "crit", st = st0 === "ok" ? status("ok", k) : st0;
      if (!homeOf(k)) st = "off";                        // information only (the report database): never a status, never green
      return {title: f.name, st: st, chips: ["Data source"], fk: k, rows: [
        ["State", f.why || (f.state === "ok" ? "answering" : f.state === "never" ? "not read yet" : f.state || "unknown")],
        ["Last good read", f.asof ? hms(f.asof) + " (" + ago(ageS(f.asof)) + ")" : "never"],
        f.fail_since ? ["Failing since", hms(f.fail_since * 1000) + " (" + ago(ageS(f.fail_since * 1000)) + ")"] : null,
        ["How it is read", f.src || "-"], ["Rule", FRESH_TITLE]]};
    }
  };
  var CTR_DEF = {
    hardware: {l: "Host hardware", kinds: ["hardware"], area: "vmware", feed: vcFeed, what: "host overallStatus and triggered alarms", verify: "vSphere Client › Host › Monitor › Issues and Alarms"},
    hosts: {l: "Hosts not responding", kinds: ["host"], area: "vmware", feed: vcFeed, what: "hosts vCenter shows not responding or disconnected", verify: "vSphere Client › Hosts and Clusters (red hosts)"},
    vc: {l: "vCenter not answering", kinds: ["vc"], area: "vmware", feed: vcFeed, what: "vCenters the wall could not read (or whose login failed)", verify: "the vSphere Client of each vCenter"},
    dsi: {l: "Datastores inaccessible", kinds: ["dsi"], area: "vmware", feed: vcFeed, what: "datastores vCenter marks inaccessible", verify: "vSphere Client › Storage (datastores with a red mark)"},
    hung: {l: "VMs with no heartbeat", kinds: ["vmhung"], area: "vmware", feed: vcFeed, what: "powered-on workload VMs with previously running Tools and a red heartbeat or stopped Tools", verify: "vSphere Client › the VM › Summary"},
    hot: {l: "Sustained high", kinds: ["cpu", "mem"], area: "vmware", feed: vcFeed, what: "hosts and VMs at the line for the sustained window (short spikes are not counted)", verify: "vSphere Client › the object › Monitor › Performance"},
    maint: {l: "Hosts in maintenance", kinds: ["maint"], area: "vmware", feed: vcFeed, what: "hosts in maintenance mode not listed as expected (portal_wall_maintenance_expected)", verify: "vSphere Client › the host › Summary"},
    sm: {l: "SnapMirror health and lag", kinds: ["sm"], area: "storage", feed: ntFeed, what: "live relationships broken off, unhealthy or over the configured lag thresholds", verify: "ONTAP System Manager › Protection › Relationships"},
    nodes: {l: "Storage nodes down", kinds: ["node"], area: "storage", feed: ntFeed, what: "nodes not up, clusters not answering", verify: "ONTAP System Manager › Cluster › Overview"},
    ds: {l: "Datastores full", kinds: ["dsfull", "dswarn"], area: "storage", feed: dsFeed, what: "datastores at or over the warning line", verify: "vSphere Client › Storage › the datastore › Capacity"},
    aggr: {l: "Aggregates full", kinds: ["aggrfull", "aggrwarn"], area: "storage", feed: ntFeed, what: "aggregates at or over the warning line", verify: "ONTAP System Manager › Storage › Tiers"}
  };
  // the NetApp feed: the oldest live cluster read (the report database is never a light on the wall)
  function oldest(keys) { var w = null; liveFeeds(keys).forEach(function (k) { if (!w || asofOf(k) < asofOf(w)) w = k; }); return w; }
  function ntFeed() { return oldest(Object.keys((W && W.feeds) || {}).filter(function (k) { return k.indexOf("ontap:") === 0; })); }
  // the datastores' read: the vCenters' (live); none -> no source (never green)
  function dsFeed() { return vcFeed(); }
  function vcFeed() { return oldest(((W.vmware || {}).vcenters || []).map(function (x) { return x.feed; })); }
  function openDetail(ref, again) {
    // 1.135.0: Share to Teams open: a refresh keeps it (its text and picture are a snapshot) - the card is redrawn on the next open
    if (again && card.querySelector(".share")) return;
    var i = ref.indexOf(":"), kind = ref.slice(0, i), key = ref.slice(i + 1);
    if (kind === "ds") { openDs(key, again); return; }
    var m = DET[kind] ? DET[kind](key) : null;
    clear(card);
    if (!m) { if (again) { closeDetail(); return; } m = {title: "Not on the wall any more", st: "off", rows: [["", "It cleared or left the read since this was drawn."]]}; }
    if (!again) lastFocus = document.activeElement;
    openRef = ref;
    var share = kind === "c" && BY[key] && window.SAWallShare ? h("button", "shareon", ["Share to Teams…"], {type: "button", "data-share": key, title: "Copy this condition as text and as a picture, to paste into a Teams chat"}) : null;
    add(card, [dHead(m.title, m.st, m.chips, m.fk), h("div", "dbody " + (m.st || ""), [kv(m.rows)]), dFoot(m.area, share)]);
    back.classList.add("on");
    tip.classList.remove("on");
    if (!again) { var x = card.querySelector(".x"); if (x) x.focus(); }
  }
  function closeDetail() {
    if (!openRef) return false;
    openRef = "";
    back.classList.remove("on");
    clear(card);
    if (lastFocus && lastFocus.focus) try { lastFocus.focus(); } catch (e) { /* gone */ }
    return true;
  }

  // ---------------------------------------------------------------- the storage path card: datastore -> LUN / volume -> aggregate
  function pathRow(o) {
    // not read (no fill in this read) or an old source: never green; information (the report database): neutral, no status
    var st = o.info ? "info" : o.stale || o.pct == null ? "stale" : o.crit ? "crit" : status(level(o.pct, o.w, o.c), o.fk);
    var svg = s("svg", {viewBox: "0 0 100 10", preserveAspectRatio: "none", "class": "vsb", role: "img", "aria-label": o.title + ": " + pct(o.pct) + " % used, " + (o.info ? "information" : WORD[st])});
    svg.appendChild(s("rect", {x: 0, y: 1, width: 100, height: 8, rx: 1.5, "class": "trk"}));
    if (o.pct != null) svg.appendChild(s("rect", {x: 0, y: 1, width: Math.max(0.5, Math.min(100, o.pct)), height: 8, rx: 1.5, "class": "fl f-" + st}));
    [o.w, o.c].forEach(function (m) { svg.appendChild(s("line", {x1: m, x2: m, y1: 0, y2: 10, "class": "mk"})); });
    return h("div", "lr" + (o.stale ? " stale" : ""), [
      h("div", "ln", [h("b", "", [o.title])].concat(o.sub.map(function (t) { return h("span", t.m ? "m" : "", [t.t], {title: t.t}); }))),
      h("div", "", [svg, h("div", "ax2", [h("span", "", ["0"]), h("span", "", [o.w + " / " + o.c]), h("span", "", ["100"])])]),
      h("div", "num", [h("b", "", [o.stale ? "? " : pct(o.pct) + " % "])].concat((o.nums || []).map(function (n, i) { return h("span", i ? "" : "first", [(o.stale && !i ? "last known " : "") + n]); }))),
      h("div", "", [o.info ? h("span", "muted", ["information"]) : wordWd(st)]), o.note ? h("p", "note", [o.note]) : null]);
  }
  function openDs(ref, again) {
    var name = dsKey(ref).n, d = dsBy(ref), p = W.storage.paths[name], dt = thr("ds"), at = thr("aggr");
    if (!again) lastFocus = document.activeElement;
    openRef = "ds:" + ref;
    clear(card);
    var fk = d ? d.feed : ntFeed(), st = d ? withFresh(dsLevel(d), fk) : "stale";
    var type = d && d.type ? (d.type === "NFS" || d.type === "NFS41" ? "NFS on a volume" : d.type === "VMFS" ? "VMFS on a LUN" : d.type) : (p && p.rows[0] && p.rows[0].lun ? "VMFS on a LUN" : "type unknown");
    add(card, dHead(name, st, [type, d && d.vc ? "vCenter " + d.vc : null].filter(Boolean), fk));
    var rows = h("div", "rows");
    if (d) {
      if (d.capacity_watched === false) rows.appendChild(h("p", "note", [d.watch_reason || "capacity not watched"]));
      rows.appendChild(pathRow({fk: fk, info: d.capacity_watched === false, crit: d.accessible === false, title: "Datastore", sub: [{t: "as vCenter sees it"}, {t: (d.nvms != null ? d.nvms + " VMs · " : "") + (d.hosts ? d.hosts.length + " hosts" : "")}], pct: d.v, w: dt[0], c: dt[1],
        stale: feedFresh(fk) === "stale", nums: [size(d.size - d.free) + " of " + size(d.size), size(d.free) + " free"]}));
      if (d.accessible === false) rows.appendChild(h("p", "note bad", ["vCenter marks this datastore inaccessible."]));
      var cs = condsOn(function (c) { return c.ds === name; });
      if (cs.length) rows.appendChild(h("div", "lr", [h("div", "ln", [h("b", "", ["Wall conditions"])]), h("div", "span3", [condRows(cs, fk)])]));
    } else {
      var omitted = (W.vmware.excluded || []).filter(function (x) {
        return x.kind === "datastores" && x.name === name && (!dsKey(ref).vc || dsKey(ref).vc === x.source);
      });
      rows.appendChild(h("p", "note", [omitted.length ? omitted[0].reason : "The vCenters' read has no datastore of this name."]));
    }
    add(card, [h("div", "sec", ["vCenter"]), rows]);
    if (!p || !p.rows.length) {
      add(card, h("div", "nomatch", [icon(WORD_IC.stale, "0 0 16 16"), h("span", "", [h("b", "", ["No NetApp match found"]), " - the datastore is not on ONTAP, or NetApp - refresh volume list has not matched it (its datastore match settings)."])]));
    } else {
      // Codex verify 4: the report database (the NetApp refresh, 15-30 min old by design) is INFORMATION here - neutral bars,
      // no status word, never a light; its age said in words
      var rf = feed("netapp-run"), age = rf && rf.asof != null ? ago(ageS(rf.asof)) : "age unknown";
      var nr = h("div", "rows");
      p.rows.forEach(function (r) {
        if (r.lun) nr.appendChild(pathRow({info: true, title: "LUN", sub: [{t: "/vol/" + r.vol + "/" + r.lun, m: 1}, {t: r.svm + (r.thick ? " · space-reserved" : "")}], pct: r.src === "LUN written" ? r.pct : null, w: dt[0], c: dt[1],
          nums: r.src === "LUN written" ? [size(r.used) + " of " + size(r.size)] : ["the LUN's own fill is not in the refresh"], note: r.thick ? "A space-reserved LUN always fills its volume; watch the LUN / datastore, not the volume %." : ""}));
        nr.appendChild(pathRow({info: true, title: "Volume", sub: [{t: r.vol, m: 1}, {t: r.svm + " · cluster " + r.cluster + (r.aggr ? " · aggregate " + r.aggr : "")}], pct: r.src === "volume used" ? r.pct : null, w: dt[0], c: dt[1],
          nums: [r.src === "volume used" ? size(r.used) + " of " + size(r.size) : "size " + size(r.vol_size)]}));
      });
      add(card, [h("div", "sec", ["NetApp · " + p.rows[0].cluster + " · from the NetApp refresh, " + age + (rf && rf.state !== "ok" ? " (the latest refresh could not be read" + (rf.why ? ": " + rf.why : "") + ")" : "") + " · information, not a status"]), nr]);
    }
    var link = W.full && W.full.storage ? h("a", "fullp", ["Open on NetApp page ", h("span", "", ["↗"], {"aria-hidden": "true"})], {href: W.full.storage + "?q=" + encodeURIComponent(name)}) : null;
    var fbk = W.me && W.me.feedback ? h("button", "fbon kxh", ["Feedback on this card"], {type: "button", "data-fb": "ds:" + ref, "data-fb-area": "storage"}) : null;
    add(card, h("div", "df", [link, fbk, h("span", "lg", ["Where to verify: vSphere Client › Storage › " + name + "; ONTAP System Manager › Storage › Volumes. Marks: datastore, LUN, volume " + dt[0] + " / " + dt[1] + " %; aggregate " + at[0] + " / " + at[1] + " %."])]));
    back.classList.add("on");
    tip.classList.remove("on");
    if (!again) { var x = card.querySelector(".x"); if (x) x.focus(); }
  }

  // ================================================================ COCKPIT (the default): gauges, counters, the Now strip
  function pt(cx, cy, r, a) { var rad = (a - 90) * Math.PI / 180; return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)]; }
  function arc(cx, cy, r, a0, a1) { var p0 = pt(cx, cy, r, a0), p1 = pt(cx, cy, r, a1), large = (a1 - a0) > 180 ? 1 : 0; return "M" + p0[0].toFixed(2) + " " + p0[1].toFixed(2) + " A" + r + " " + r + " 0 " + large + " 1 " + p1[0].toFixed(2) + " " + p1[1].toFixed(2); }
  function gauge(o) {
    var max = o.max || 100, A0 = -120, A1 = 120, cx = 100, cy = 100, r = 76, sw = 13, st = o.st;
    var v = o.v == null ? 0 : o.v, frac = Math.max(0, Math.min(1, v / max)), aV = A0 + (A1 - A0) * frac;
    var disp = o.v == null || (st === "stale" && o.hideStale) ? "?" : o.disp;
    var svg = s("svg", {viewBox: "-18 -4 236 176", role: "img", "aria-label": o.label + ": " + disp + (o.unit || "") + ", " + WORD[st]});
    svg.appendChild(s("path", {d: arc(cx, cy, r, A0, A1), fill: "none", "class": "gtrk", "stroke-width": sw}));
    if (frac > 0.002 && disp !== "?") svg.appendChild(s("path", {"class": "val g-" + st, d: arc(cx, cy, r, A0, Math.max(aV, A0 + 1)), fill: "none", "stroke-width": sw}));
    for (var i = 0; i <= 10; i++) { var a = A0 + (A1 - A0) * i / 10, ri = r + sw / 2 + 2, ro = ri + (i % 5 === 0 ? 7 : 4), p = pt(cx, cy, ri, a), q = pt(cx, cy, ro, a); svg.appendChild(s("line", {"class": "tick", x1: p[0].toFixed(1), y1: p[1].toFixed(1), x2: q[0].toFixed(1), y2: q[1].toFixed(1)})); }
    var lo = pt(cx, cy, r, A0), hi = pt(cx, cy, r, A1);
    svg.appendChild(s("text", {"class": "tlab", x: lo[0].toFixed(1), y: (lo[1] + 22).toFixed(1), "text-anchor": "middle"}, ["0"]));
    svg.appendChild(s("text", {"class": "tlab", x: hi[0].toFixed(1), y: (hi[1] + 22).toFixed(1), "text-anchor": "middle"}, [String(o.maxLabel || max)]));
    (o.thr || []).forEach(function (t) {
      if (t == null || t > max) return;
      var a2 = A0 + (A1 - A0) * t / max, p2 = pt(cx, cy, r - sw / 2 - 4, a2), q2 = pt(cx, cy, r + sw / 2 + 9, a2), lp = pt(cx, cy, r + sw / 2 + 18, a2);
      svg.appendChild(s("line", {"class": "thr", x1: p2[0].toFixed(1), y1: p2[1].toFixed(1), x2: q2[0].toFixed(1), y2: q2[1].toFixed(1)}));
      svg.appendChild(s("text", {"class": "thrl", x: lp[0].toFixed(1), y: (lp[1] + 3.5).toFixed(1), "text-anchor": "middle"}, [String(t)]));
    });
    var big = disp.length > 4 ? 40 : 48;
    svg.appendChild(s("text", {"class": "gnum", x: 100, y: o.sub ? 108 : 116, "text-anchor": "middle", "font-size": big}, [disp, o.unit && disp !== "?" ? s("tspan", {"class": "gunit", "font-size": 20, dx: 2}, [o.unit]) : null]));
    var why = st === "stale" ? greyGauge(o.feed, o.whyStale ? o.why : "value unread") : o.why;
    var g = h("div", "gauge" + (st === "stale" ? " stale" : "") + (st === "crit" ? " gcrit" : ""), [h("div", "gl", [o.label]), o.small ? h("div", "gs", [o.small]) : null, svg,
      word(st, o.wordTxt), h("div", "why", [why || " "])],
      {tabindex: "0", role: "button", "data-d": o.d || null, title: st === "stale" ? why : o.title || ("Click: details of " + o.label)});
    return g;
  }
  function pctGauge(label, small, v, thr2, st, why, fk, d, title) {
    return gauge({label: label, small: small, v: v, disp: pct(v), unit: "%", thr: thr2, st: withFresh(st, fk), feed: fk, why: why, d: d, title: title});
  }
  function ctr(o) {
    // stale data: always "?" and Not current, the last known count in words - never green, never a colour from old data
    // (a live count - a vCenter not answering now - is never stale)
    // ... and a condition still open from a current source (another vCenter, the cluster itself) is never hidden by an old one
    var liveOpen = (o.kinds ? nOpen(o.kinds) : []).some(function (c) { return !condStale(c); });
    if (o.ran === false && o.feed && !liveOpen) { o.need = true; o.feed = null; o.needWhy = o.ranWhy || "the check did not run"; }
    if (o.need && !o.feed && !liveOpen) {                                    // no source for it here (no NetApp read): grey, never "None" in green
      return h("div", "ctr off", [h("div", "cl", [o.l], {title: o.l}), h("div", "row", [h("span", "big", ["\u2013"]), word("off", "Not on the wall")]),
        h("div", "det", [o.needWhy || "no source for it here"])], {tabindex: "0", role: "button", "data-d": "ctr:" + o.k, title: o.l + ": " + (o.needWhy || "no source for it here")});
    }
    var stale = ((o.v > 0 && o.kinds && !liveOpen) || !!(o.feed && feedFresh(o.feed) === "stale") || !!o.unknown) && !liveOpen && !(o.live && o.v > 0 && !UNREACH),
      st = stale ? "stale" : status(o.st || (o.v > 0 ? "crit" : "ok"), liveOpen || (o.live && o.v > 0) ? "-live-" : o.feed);
    var w = st === "ok" ? word("ok", "None") : st === "stale" ? word("stale") : word(st, o.word);
    return h("div", "ctr " + st, [h("div", "cl", [o.l], {title: o.l}),
      h("div", "row", [h("span", "big", [stale ? "?" : String(o.v)]), o.of != null ? h("span", "of", ["of " + o.of]) : null, w]),
      h("div", "det", [o.unknown && stale ? o.unknown : st === "stale" ? "Why grey: " + (greyReasons(homeOf(o.feed || "")) || "last known: " + o.v) : (o.det || " ")]), o.feed ? freshK(o.feed) : null],
      {tabindex: "0", role: "button", "data-d": "ctr:" + o.k, title: "Click: what this counts, what is open, where to check"});
  }
  function detOf(list) { return list.length ? [h("b", "", [list[0].t]), " · " + dur(NOW() - list[0].start)] : null; }
  var CT = {
    hardware: function () {
      var l = nOpen("hardware"), critical = l.some(function (c) { return c.sev === "crit" && !condStale(c); });
      return ctr({k: "hardware", l: "Host hardware", ran: W.vmware.hosts.length > 0, ranWhy: "no watched hosts", kinds: ["hardware"], v: l.length, feed: vcFeed(),
                  st: critical ? "crit" : l.length ? "warn" : "ok", det: l.map(function (c) { return c.host; }).join(", ") || "all hosts green"});
    },
    hosts: function () {
      var l = nOpen("host"), u = W.vmware.hosts.filter(function (x) { return x.st === "stale"; }).length;
      return ctr({k: "hosts", kinds: ["host"], l: "Hosts not responding", v: l.length, feed: vcFeed(), unknown: u ? u + " host(s): state not read - not all connected" : "",
                  det: detOf(l) || (u ? u + " host(s): state not read" : "all hosts connected")});
    },
    vc: function () { var v = W.vmware.vcenters, dn = v.filter(function (x) { return !x.up; }).length; return ctr({k: "vc", kinds: ["vc"], l: "vCenter not answering", v: dn, of: v.length, feed: vcFeed(), live: true, det: dn ? detOf(nOpen("vc")) : v.map(function (x) { return x.name; }).join(", ") + " answering"}); },
    dsi: function () { var l = nOpen("dsi"); return ctr({k: "dsi", kinds: ["dsi"], l: "Datastores inaccessible", v: l.length, feed: vcFeed(), det: detOf(l) || "all " + W.vmware.datastores.length + " accessible"}); },
    hung: function () { var l = nOpen("vmhung"); return ctr({k: "hung", ran: W.thresholds.vm_heartbeat && W.vmware.heartbeat_watched > 0, ranWhy: W.thresholds.vm_heartbeat ? "not watched: " + W.vmware.heartbeat_unwatched : "off: portal_wall_vm_heartbeat is false", kinds: ["vmhung"], l: "VMs with no heartbeat", v: l.length, feed: vcFeed(), det: detOf(l) || (W.thresholds.vm_heartbeat ? "no watched VM has lost its heartbeat" : "off (portal_wall_vm_heartbeat)")}); },
    hot: function () { var l = nOpen(["cpu", "mem"]); return ctr({k: "hot", kinds: ["cpu", "mem"], l: "Sustained high (" + thr("high")[1] + " min+)", v: l.length, st: l.length ? "warn" : "ok", word: "Warning", feed: vcFeed(), det: detOf(l) || "no host or VM"}); },
    maint: function () { var l = nOpen("maint"); return ctr({k: "maint", kinds: ["maint"], l: "Hosts in maintenance", v: l.length, st: l.length ? "warn" : "ok", word: "Warning", feed: vcFeed(), det: detOf(l) || "none unexpected"}); },
    sm: function () { var l = nOpen("sm"); return ctr({k: "sm", ran: W.storage.ran.sm, ranWhy: "live ONTAP check incomplete", need: true, needWhy: "no NetApp read: the NetApp page's run or the NetApp ONTAP credential", kinds: ["sm"], l: "SnapMirror health and lag", v: l.length, st: l.some(function (c) { return c.sev === "crit" && !condStale(c); }) ? "crit" : l.length ? "warn" : "ok", feed: ntFeed(), det: detOf(l) || W.storage.smRel + " relationships healthy"}); },
    nodes: function () { var n = W.storage.nodes, l = nOpen("node"); return ctr({k: "nodes", ran: W.storage.ran.nodes, ranWhy: "live ONTAP check incomplete", need: true, needWhy: "no NetApp read: the NetApp page's run or the NetApp ONTAP credential", kinds: ["node"], l: "Storage nodes down", v: l.length, of: n[1] || null, feed: ntFeed(), det: detOf(l) || n[0] + " of " + n[1] + " nodes up"}); },
    ds: function () { var l = nOpen(["dsfull", "dswarn"]); return ctr({k: "ds", kinds: ["dsfull", "dswarn"], need: true, needWhy: "no vCenter or NetApp read", l: "Datastores full", v: l.length, st: l.some(function (c) { return c.kind === "dsfull"; }) ? "crit" : l.length ? "warn" : "ok", word: l.some(function (c) { return c.kind === "dsfull"; }) ? null : "Warning", feed: dsFeed(), det: detOf(l) || "all under " + thr("ds")[0] + " %"}); }
  };
  function busiest(m) { var b = null; W.vmware.clusters.forEach(function (c) { if (c[m] != null && (!b || c[m] > b[m])) b = c; }); return b; }
  function hostsUp() { var u = 0, o = 0; W.vmware.clusters.forEach(function (c) { u += c.up; o += c.of; }); return [u, o]; }
  function groupNow(items) {
    var groups = new Map(), result = [];
    items.forEach(function (item) {
      if (!item.c) { result.push(item); return; }
      var k = item.c.kind;
      if (!groups.has(k)) {
        var group = Object.assign({}, item, {members: []});
        groups.set(k, group);
        result.push(group);
      }
      groups.get(k).members.push(item.c);
    });
    result.forEach(function (g) {
      if (!g.members || g.members.length < 2) return;
      var k = g.c.kind, count = g.members.length;
      var names = g.members.slice(0, 3).map(function (c) { return c.vm || c.obj_name || c.ds || c.aggr || c.host || c.vc || c.cluster || c.t; });
      g.t = (k === "vmhung" ? count + " VMs: no heartbeat" : count + " · " + W.kinds[k].label) + " · " + names.join(", ") + (count > 3 ? " +" + (count - 3) : "");
      g.detail = "kind:" + k;
    });
    return result;
  }
  function nowStrip(area) {
    var items = nowList(area), n = items.length;
    var ok = icon(WORD_IC.ok, "0 0 16 16");
    if (!n && (area ? areaState(area) : overallState()) !== "ok")      // nothing open, but nothing current either: never the green "clear"
      return h("div", "now nocur", [h("span", "lab", [icon(WORD_IC.stale, "0 0 16 16"), "Now"]), h("span", "", ["No current data" + (area ? " for " + aShort(area) : "") + "."])], {role: "status"});
    if (!n) return h("div", "now clear", [h("span", "lab", [ok, "Now"]), h("span", "", ["Nothing on the wall" + (area ? " for " + aShort(area) : "") + "."])], {role: "status"});
    var crit = items.filter(function (i) { return i.sev === "crit"; }).length, warn = items.filter(function (i) { return i.sev === "warn"; }).length, stl = items.filter(function (i) { return i.sev === "stale"; }).length;
    var lab = [crit ? crit + " critical" : "", warn ? warn + " warning" : "", stl ? stl + " not current" : ""].filter(Boolean).join(" · ");
    var ul = h("ul");
    groupNow(items).forEach(function (i) {
      ul.appendChild(h("li", i.sev, [icon(WORD_IC[i.sev], "0 0 16 16"), i.t, h("span", "for", [i.type === "cond" ? dur(NOW() - i.since) : (i.since ? ago(ageS(i.since)).replace(" ago", " old") : "never read")])],
        {tabindex: "0", role: "button", "data-d": i.detail || (i.type === "cond" ? "c:" + i.id : "feed:" + i.feed), title: "Click: details"}));
    });
    // the heading's colour: red with a critical, amber with a warning, grey with only "not current" - never green with anything open
    return h("div", "now" + (crit ? "" : warn ? " warnonly" : " nocur"), [h("span", "lab", [icon(WORD_IC[crit ? "crit" : warn ? "warn" : "stale"], "0 0 16 16"), "Now · " + lab]), ul, h("span", "more", [""], {id: "nowMore"})], {role: "status"});
  }
  function countHidden() {
    var ul = stage.querySelector(".now ul"); if (!ul) return;
    var lis = ul.querySelectorAll("li"); if (!lis.length) return;
    var top0 = lis[0].offsetTop, hid = 0;
    lis.forEach(function (li) { if (li.offsetTop > top0 + 4) hid++; });
    var m = stage.querySelector("#nowMore"); if (m) m.textContent = hid ? "+" + hid + " more" : "";
  }
  function khead(title, area) { return h("div", "khead", [h("h1", "", [title]), area ? h("span", "khr", [fullLink(area)]) : null]); }
  function panel(title, cnt, fk, kids, cls, go) {
    return h("div", "panel" + (cls ? " " + cls : ""), [h("div", "ph", [h("h2", go ? "golink" : "", [title], go ? {"data-goto": go, tabindex: "0", title: "Open the " + title + " wall view"} : null),
      cnt ? h("span", "cnt", [cnt]) : null, fk ? (fk.indexOf("area:") === 0 ? freshArea(fk.slice(5)) : freshK(fk)) : null]), go ? greyNote(go) : null, kids]);
  }
  function offPanel(a, cls) {
    return h("div", "panel offp" + (cls ? " " + cls : ""), [h("div", "ph", [h("h2", "", [a.name])]),
      h("div", "offb", [lamp("off"), h("div", "", [h("b", "", ["Not on the wall yet"]), h("div", "muted", [a.why || "a later stage of the wall"])])])]);
  }
  function hotPanel(grow) {
    var hot = W.vmware.hot, b = W.vmware.building, hp = thr("high");
    var tb = h("table", "list", [h("thead", "", [h("tr", "", ["Name", "Kind", "Metric", "Now", "For", "Last hour (40-100 %)", "State"].map(function (t, i) { return h("th", i === 3 || i === 4 ? "num" : "", [t]); }))])]);
    var body = h("tbody");
    if (!hot.length) body.appendChild(h("tr", "", [h("td", "", [word(withFresh("ok", vcFeed()), "None now"), h("span", "muted", [" · no host or VM at " + hp[0] + " % or more for " + hp[1] + " min"])], {colspan: 7})]));
    hot.forEach(function (x) {
      var c = BY[x.cond], st = withFresh(c ? c.sev : "warn", x.feed);
      body.appendChild(h("tr", st, [h("td", "nm", [x.n]), h("td", "", [x.k]), h("td", "", [x.m]), h("td", "num", [h("b", "", [pct(x.v) + " %"])]), h("td", "num", [c ? dur(NOW() - c.start) : "?"]),
        h("td", "spk", [spark(x.spark, st, 40)]), h("td", "", [word(st, st === "warn" ? "Warning" : null)])], {tabindex: "0", role: "button", "data-d": "c:" + x.cond}));
    });
    tb.appendChild(body);
    var note = b.length ? b.length + " more at " + hp[0] + " % or more for under " + hp[1] + " min (" + b.slice(0, 4).map(function (x) { return x.n + " " + x.m.toLowerCase() + " " + pct(x.v) + " % for " + x.min + " min"; }).join(", ") +
      (b.length > 4 ? ", …" : "") + ") - short spikes stay off the wall; they join this list at " + hp[1] + " min." : "Short spikes stay off the wall; an object joins this list after " + hp[1] + " min at " + hp[0] + " % or more.";
    return panel("Hosts and VMs - sustained high", "CPU or memory ≥ " + hp[0] + " % for " + hp[1] + " min or longer · " + hot.length, vcFeed(), h("div", "body", [tb, h("p", "note", [note])]), grow ? "grow" : "");
  }
  function spark(a, st, hh) {
    hh = hh || 40;
    var w = 240, n = a.length, LO = 40, Y = function (v) { return hh - 2 - (Math.max(LO, v) - LO) / (100 - LO) * (hh - 4); };
    // one value a minute; a read every W.seconds leaves minutes empty - the line goes on over those, it breaks only over a real gap
    var d = "", pen = false, gap = 0, allow = Math.max(1, Math.ceil(2.5 * ((W && W.seconds) || 60) / 60) - 1);
    a.forEach(function (v, i) {
      if (v == null) { gap++; if (gap > allow) pen = false; return; }
      d += (pen ? "L" : "M") + ((i / (n - 1)) * w).toFixed(1) + " " + Y(v).toFixed(1); pen = true; gap = 0;
    });
    var vals = a.filter(function (v) { return v != null; });
    var svg = s("svg", {viewBox: "0 0 " + w + " " + hh, preserveAspectRatio: "none", "class": "sp" + hh, "data-spark": a.map(function (v) { return v == null ? "" : Math.round(v); }).join(","),
      "aria-label": "last hour" + (vals.length ? ", " + Math.round(Math.min.apply(null, vals)) + " to " + Math.round(Math.max.apply(null, vals)) + " %" : "")});
    svg.appendChild(s("line", {"class": "base", x1: 0, y1: hh - 2, x2: w, y2: hh - 2}));
    svg.appendChild(s("line", {"class": "t90", x1: 0, y1: Y(thr("high")[0]).toFixed(1), x2: w, y2: Y(thr("high")[0]).toFixed(1)}));
    if (d) svg.appendChild(s("path", {"class": "ln g-" + st, d: d}));
    return svg;
  }
  // a datastore vCenter marks inaccessible is Critical whatever its fill
  function dsLevel(d) { var t = thr("ds"); return d.accessible === false ? "crit" : d.capacity_watched === false ? "off" : level(d.v, t[0], t[1]); }
  function dsBars(rows, fkDefault) {
    var t = thr("ds"), g = h("div", "bars", [h("div", "hd", ["Datastore"]), h("div", "hd", ["Used (line at " + t[1] + " %)"]), h("div", "hd r", ["Used %"]), h("div", "hd r", ["Free"]), h("div", "hd", ["State"])]);
    rows.forEach(function (d) {
      var fk = d.feed || fkDefault, st = withFresh(dsLevel(d), fk), c = d.cond ? BY[d.cond] : null;
      var svg = s("svg", {viewBox: "0 0 100 10", preserveAspectRatio: "none", "class": "trk2"}, [s("rect", {x: 0, y: 0, width: 100, height: 10, rx: 2, "class": "trk"}),
        s("rect", {x: 0, y: 0, width: Math.max(0.5, Math.min(100, d.v || 0)), height: 10, rx: 2, "class": "fl f-" + st}), s("line", {x1: t[1], x2: t[1], y1: -2, y2: 12, "class": "mk"})]);
      var at = {"data-d": dsRef(d), tabindex: "0", role: "button", title: d.n + ": " + pct(d.v) + " % used, " + size(d.free) + " free · click: storage path"};
      add(g, [h("div", "nm", [d.n], at), h("div", "trkw", [svg], {"data-d": dsRef(d)}), h("div", "num", [h("b", "", [pct(d.v)])]), h("div", "num muted", [size(d.free)]),
        h("div", "st", [word(st, st === "warn" ? "Warning" : null), c && st !== "stale" ? h("span", "muted sm", [" " + dur(NOW() - c.start)]) : null])]);
    });
    if (!rows.length) g.appendChild(h("div", "full muted", ["No datastore in the read."]));
    return g;
  }
  function aggrGauge(a) {
    var t = thr("aggr"), st = level(a.v, t[0], t[1]), c = a.cond ? BY[a.cond] : null;
    var why = st === "crit" ? "at or over " + t[1] + " %" + (c ? " for " + dur(NOW() - c.start) : "") : st === "warn" ? "over " + t[0] + " %" + (c ? " for " + dur(NOW() - c.start) : "") + " · " + size(a.free) + " free" : "under " + t[0] + " % · " + size(a.free) + " free";
    return pctGauge("Fullest aggregate", a.n, a.v, t, st, why, a.feed, "aggr:" + a.ref, "Warning " + t[0] + " %, critical " + t[1] + " %");
  }
  function dsGauge(d, label) {
    var t = thr("ds"), st = dsLevel(d), c = d.cond ? BY[d.cond] : null;
    return pctGauge(label || "Fullest datastore", d.n, d.v, t, st, d.accessible === false ? "inaccessible (vCenter)" : st === "ok" ? "under " + t[0] + " % · " + size(d.free) + " free" : (st === "crit" ? "at or over " + t[1] + " %" : "over " + t[0] + " %") + (c ? " for " + dur(NOW() - c.start) : ""),
      d.feed, dsRef(d), "Warning " + t[0] + " %, critical " + t[1] + " %");
  }
  function clGauge(c, m, label) {
    var hp = thr("high")[0], v = c[m];
    return pctGauge(label || (m === "cpu" ? "Cluster CPU" : "Cluster memory"), c.n + (label ? "" : " (busiest)"), v, [hp], level(v, 101, hp) === "crit" ? "warn" : level(v, 101, hp),
      v != null && v >= hp ? "at the line - a host or VM is flagged after " + thr("high")[1] + " min" : "warning: " + hp + " % for " + thr("high")[1] + " min", c.feed, "cl:" + c.vc + "|" + c.n + "|" + m);
  }
  function upGauge(label, small, up, of, why, fk, d, liveDown, downN) {
    // a host down in a current read stays Critical whatever another source's age; hosts not up and none down: state unknown
    var st = up < of && liveDown && !UNREACH ? "crit" : up < of && downN === 0 ? "stale" : withFresh(up < of ? "crit" : "ok", fk);
    return gauge({label: label, small: small, v: of ? up : null, max: of || 1, maxLabel: of, feed: fk, disp: up + "/" + of, st: st,
                  why: up < of && downN === 0 ? (of - up) + " host(s): state not read" : why, whyStale: up < of && downN === 0, d: d});
  }
  function teamStorage(n) {
    var a = W.storage.aggr[0], d = (W.storage.ds || [])[0], g = [];
    if (a) g.push(aggrGauge(a));
    if (d) g.push(dsGauge(d));
    if (!g.length) g.push(h("div", "nodata muted", [W.storage.why || areaOf("storage").why || "No storage data yet."]));
    return panel("Storage", "NetApp + datastores", "area:storage", [h("div", "gauges c" + Math.max(1, g.length), g), h("div", "counters cc3", [CT.sm(), CT.nodes(), CT.ds()])], "n" + n, "storage");
  }
  function teamVmware(n) {
    var cpu = busiest("cpu"), mem = busiest("mem"), hu = hostsUp(), g = [];
    if (cpu) g.push(clGauge(cpu, "cpu"));
    if (mem) g.push(clGauge(mem, "mem"));
    var down = nOpen("host");
    var allCl = W.vmware.clusters[0];
    g.push(upGauge("ESXi hosts up", "all clusters", hu[0], hu[1], down.length ? down.map(function (c) { return c.t; }).join(", ") : "all connected", vcFeed(),
      down.length ? "c:" + down[0].id : allCl ? "cl:" + allCl.vc + "|" + allCl.n + "|hosts" : null, down.some(function (c) { return !condStale(c); }), down.length));
    return panel("VMware", W.vmware.vcenters.map(function (x) { return x.name; }).join(", "), "area:vmware", [h("div", "gauges c" + g.length, g), h("div", "counters cc3", [CT.hosts(), CT.vc(), CT.hot()])], "n" + n, "vmware");
  }
  function pOverview() {
    var parts = [khead("Overview" + (W.enclave ? " · " + W.enclave : ""))], teams = h("div", "teams gm245");
    var st = areaOf("storage"), vm = areaOf("vmware");
    teams.appendChild(st.on ? teamStorage(2) : offPanel(st, "n2"));
    teams.appendChild(vm.on ? teamVmware(3) : offPanel(vm, "n3"));
    parts.push(teams);
    var more = h("div", "counters cc4");
    W.areas.forEach(function (a) { if (!a.built) more.appendChild(h("div", "ctr off", [h("div", "cl", [a.name]), h("div", "row", [h("span", "big", ["–"]), word("off", "Not on the wall yet")]), h("div", "det", ["a later stage"])], {title: a.name + ": not on the wall yet"})); });
    more.appendChild(h("div", "ctr off", [h("div", "cl", ["Network"]), h("div", "row", [h("span", "big", ["–"]), word("off", "Not on the wall yet")]), h("div", "det", ["WAN and sites: a later stage"])]));
    parts.push(panel("Directory, outages, problems, network", "grey = not on the wall yet", null, more));
    parts.push(vm.on ? hotPanel(true) : h("div", "grow"));
    return parts;
  }
  function pStorage() {
    var stg = W.storage, t = thr("aggr");
    var g = stg.aggr.slice(0, 8).map(function (a) { var st = level(a.v, t[0], t[1]); return pctGauge(a.n, a.c + " · " + a.node, a.v, t, st, size(a.free) + " free", a.feed, "aggr:" + a.ref); });
    var tb = h("table", "list", [h("thead", "", [h("tr", "", [h("th", "", ["Destination"]), h("th", "", ["Source"]), h("th", "", ["State"]), h("th", "num", ["For"])])])]), body = h("tbody");
    stg.smBroken.forEach(function (b) { var c = BY[b.cond], st = withFresh(b.sev || "warn", b.feed || ntFeed()); body.appendChild(h("tr", st, [h("td", "nm", [b.dst]), h("td", "nm", [b.src]), h("td", "", [word(st, b.word || "Broken")]), h("td", "num", [c ? dur(NOW() - c.start) : ""])], {"data-d": "c:" + b.cond, tabindex: "0", role: "button"})); });
    if (!stg.smBroken.length) body.appendChild(h("tr", "", [h("td", "", [word(status("ok", ntFeed(), W.storage.ran.sm), status("ok", ntFeed(), W.storage.ran.sm) === "ok" ? "None over threshold or unhealthy" : null)], {colspan: 4})]));
    tb.appendChild(body);
    return [khead("Storage · " + (stg.clusters.join(", ") || "NetApp"), "storage"),
      panel("Aggregates", stg.aggr.length + " · warning at " + t[0] + " %, critical at " + t[1] + " %", ntFeed(), g.length ? h("div", "gauges c" + Math.min(8, g.length) + " cm4", g) : h("p", "note", [stg.why || "No NetApp run in the report database yet."])),
      h("div", "counters cc4", [CT.sm(), CT.nodes(), CT.ds(), ctr({k: "aggr", need: true, needWhy: "no NetApp read: the NetApp page's run or the NetApp ONTAP credential", kinds: ["aggrfull", "aggrwarn"], l: "Aggregates full", v: nOpen(["aggrfull", "aggrwarn"]).length, st: nOpen("aggrfull").length ? "crit" : nOpen("aggrwarn").length ? "warn" : "ok", word: nOpen("aggrfull").length ? null : "Warning", feed: ntFeed(), det: detOf(nOpen(["aggrfull", "aggrwarn"])) || "all under " + t[0] + " %"})]),
      h("div", "split grow", [panel("Fullest datastores", "top " + Math.min(12, stg.ds.length) + " of " + stg.dsTotal + " · warning " + thr("ds")[0] + " %, critical " + thr("ds")[1] + " % · click: storage path", vcFeed() || ntFeed(),
        h("div", "body", [dsBars(stg.ds.slice(0, 12), ntFeed())]), "grow"),
        panel("SnapMirror health and lag", String(stg.smBroken.length), ntFeed(), h("div", "body", [tb, h("p", "note", ["Broken, unhealthy and overdue relationships appear here."])]), "grow")])];
  }
  function pVmware() {
    var v = W.vmware, g = [];
    v.clusters.forEach(function (c) { g.push(clGauge(c, "cpu", "CPU")); g.push(clGauge(c, "mem", "Memory")); });
    var hg = v.clusters.map(function (c) { return upGauge("Hosts up", c.n, c.up, c.of, c.down.length ? c.down.join(", ") + " not responding" : c.maint.length ? c.maint.length + " in maintenance" : "all connected", c.feed, "cl:" + c.vc + "|" + c.n + "|hosts",
      nOpen("host").some(function (x) { return x.cluster === c.n && !condStale(x); }), c.down.length); });
    var hu = hostsUp(), nc = Math.min(10, g.length);
    return [khead("VMware · " + v.vcenters.map(function (x) { return x.name; }).join(", "), "vmware"),
      panel("Clusters", "CPU and memory · the line: " + thr("high")[0] + " % for " + thr("high")[1] + " min", vcFeed(), h("div", "gauges gm200 c" + Math.max(1, nc) + " cm4", g)),
      h("div", "split even", [panel("Hosts", hu[0] + " of " + hu[1] + " connected", vcFeed(), h("div", "gauges gm170 c" + Math.max(1, Math.min(6, hg.length)) + " cm" + Math.max(1, Math.min(4, hg.length)), hg)),
        panel("Conditions", "routine vCenter alarms are not on the wall", null, h("div", "counters cc3 ccm2", [CT.hosts(), CT.vc(), CT.dsi(), CT.hung(), CT.maint(), CT.hot(), CT.hardware()]))]),
      h("div", "split grow", [hotPanel(true), panel("Fullest datastores", "top 6 of " + W.storage.dsTotal + " · Storage owns these", vcFeed(), h("div", "body", [dsBars((W.storage.ds || []).slice(0, 6), vcFeed())]), "grow")])];
  }
  RENDER.cockpit = function (r) {
    var a = S.area;
    r.appendChild(nowStrip(a === "overview" || a === "watch" ? null : a));
    var main = h("div", "kmain");
    if (a === "watch") add(main, [khead("What the wall watches"), h("div", "panel", [watchEl()])]);
    else add(main, a === "storage" ? pStorage() : a === "vmware" ? pVmware() : pOverview());
    r.appendChild(main);
    later(countHidden);
  };

  // ================================================================ 1.135.0: Strata, Relay, Metro - drawn by static/wall-styles.js on a
  // 1920 x 1080 board scaled to the room the page leaves (kiosk: the whole screen); this page lends it its model, rule and helpers
  function fitScreen() {
    var b = stage.querySelector(".wx");
    if (!b) return;
    // the room below the stage's top, less the lower classification banner (report_banner) that follows the page
    var low = document.querySelector("main ~ .banner"), lowH = low ? low.getBoundingClientRect().height : 0;
    var top = stage.getBoundingClientRect().top + window.scrollY, w = Math.max(320, stage.clientWidth), hh = Math.max(240, Math.floor(innerHeight - Math.ceil(Math.max(0, top)) - Math.ceil(lowH)));     // whole pixels: never a 1 px scroll
    var k = Math.min(w / 1920, hh / 1080);
    b.style.width = (w / k) + "px";
    b.style.height = (hh / k) + "px";
    b.style.transform = "scale(" + k + ")";
    stage.style.height = hh + "px";
    if (monArea()) window.SAWallFleet.fitted(k);
  }
  // Share to Teams over the detail card: a snapshot; closing it draws the card again from the latest read
  function openShare(id) {
    if (!window.SAWallShare || !BY[id]) return;
    window.SAWallShare.open(card, shareOf(BY[id]), function () { if (openRef) openDetail(openRef, true); });
  }
  // a condition with no feed of its own (a vCenter or a cluster not answering): its source is that vCenter or cluster
  function condFeed(c) { return c.feed || (c.vc ? "vc:" + c.vc : c.kind === "node" && String(c.id).indexOf("node:") === 0 ? "ontap:" + String(c.id).slice(5) : ""); }
  function shareOf(c) {
    var cleared = c.end != null;
    // what a Teams post says about one condition: from the model only (escaped by static/wall-share.js), the limits from the settings
    var t = c.kind === "dsfull" || c.kind === "dswarn" ? thr("ds") : c.kind === "aggrfull" || c.kind === "aggrwarn" ? thr("aggr") :
      c.kind === "cpu" || c.kind === "mem" ? [thr("high")[0], null] : c.kind === "sm" && c.lag_h != null ? thr("snapmirror_lag_hours") : null;
    var hot = null;
    if (c.kind === "cpu" || c.kind === "mem") (W.vmware.hot || []).forEach(function (x) { if (x.cond === c.id) hot = x; });
    var f = feed(condFeed(c)), stale = !cleared && condStale(c);
    // a last-known or cleared condition: its own last observation, never the feed's newer read that did not see it
    var seen = (c.last_known || cleared) && c.seen ? c.seen : null;
    return {sev: cleared ? "cleared" : stale ? "stale" : c.sev, area: aShort(c.area), title: c.t, obj: c.obj || "", since: c.start ? when(c.start) : "",
            until: cleared ? when(c.end) : "", age: c.start ? dur((cleared ? c.end : NOW()) - c.start) : "",
            lastSeen: c.last_known && seen ? when(seen) + " (" + ago(ageS(seen)) + ")" : "", strict: c.kind === "sm",
            site: [W.title, W.enclave, c.site].filter(Boolean).join(" · "),
            v: c.v != null ? c.v : c.kind === "sm" && c.lag_h != null ? c.lag_h : null, unit: c.kind === "sm" ? " h" : "%",
            warn: t ? t[0] : null, crit: t ? t[1] : null, max: c.kind === "sm" && t ? Math.max(t[1] * 1.3, (c.lag_h || 0) * 1.1) : 100,
            series: hot && Array.isArray(hot.spark) ? hot.spark : null, seriesLab: hot ? "last hour, per minute" : "",
            src: f ? f.name : (c.feed || "the wall's read"), asof: seen ? when(seen) : f && f.asof ? when(f.asof) : "never", asofAge: seen ? ago(ageS(seen)) : f && f.asof ? ago(ageS(f.asof)) : "",
            check: verify(c), link: location.origin + SRC.replace(/api\/wall$/, "wall") + "?area=" + encodeURIComponent(c.area) + "&open=" + encodeURIComponent("c:" + c.id),
            stamp: hms(NOW()), zone: zoneName()};
  }
  var API = {
    h: h, s: s, add: add, clear: clear, icon: icon, P: P, lamp: lamp, word: word, WORD: WORD, GLYPH: GLYPH, areaIcon: areaIcon,
    W: function () { return W; }, S: S, NOW: NOW, hm: hm, hms: hms, when: when, dur: dur, ago: ago, ageS: ageS, pct: pct, size: size, zoneName: zoneName,
    areaList: areaList, areaOf: areaOf, areaOn: areaOn, aShort: aShort, areaState: areaState, areaCounts: areaCounts, areaAsof: areaAsof, areaFresh: areaFresh,
    overallState: overallState, openConds: openConds, condStale: condStale, nowList: nowList, feed: feed, feedFresh: feedFresh, status: status,
    level: level, thr: thr, greyReasons: greyReasons, freshTxt: freshTxt, freshK: freshK, freshArea: freshArea, dsLevel: dsLevel, vcFeed: vcFeed, ntFeed: ntFeed,
    unreach: function () { return UNREACH; }, logo: root.getAttribute("data-logo") || "", later: later, ROT: ROT,
    dsRef: dsRef, cond: function (id) { return BY[id] || null; }, condFeed: condFeed, lastKnown: lastKnown, openDetail: openDetail, setArea: setArea, render: function () { render(); }, fit: fitScreen,
    share: function (id) { if (!BY[id]) return; openDetail("c:" + id); openShare(id); }
  };
  if (globalThis.SAWallFleet) Object.assign(API, {
    src: SRC, test: TEST, frozen: function () { return FREEZE; }, qs: QS, idleResume: IDLE_RESUME, styleName: function () { return STYLE_NAME[S.style]; }
  });
  ["strata", "relay", "metro"].forEach(function (st) {
    RENDER[st] = function (r) {
      if (S.area === "watch") { add(r, [h("div", "kmain", [khead("What the wall watches"), h("div", "panel", [watchEl()])])]); return; }   // the setup list, as in Cockpit
      if (window.SAWallStyles && window.SAWallStyles[st]) window.SAWallStyles[st](API, r);
      else r.appendChild(h("p", "note warn", ["This style's file (wall-styles.js) did not load - reload the page."]));
    };
  });

  // ================================================================ LIGHTS: one big status light per area
  function lWorst(id) {
    var st = areaState(id), l = nowList(id).filter(function (i) { return i.type === "cond"; });
    if (st === "stale") { var o = openConds(id); return o.length ? "Last known: " + o[0].t : "Last known: no wall condition"; }
    return l.length ? l[0].t + " · " + dur(NOW() - l[0].since) : "All clear: no wall condition";
  }
  function lFresh(id) {
    var f = areaFresh(id), asof = areaAsof(id), x = freshTxt(asof);
    return h("div", "fresh f-" + f, [h("span", "", ["as of ", h("b", "", [asof ? hms(asof) : "never"])]), h("span", "ago", [asof ? "(" + ago(ageS(asof)) + ")" : ""]),
      f === "ok" ? null : h("span", "chip " + (f === "stale" ? "stale-r" : "stale-a"), [icon(LAMP_IC.stale, "0 0 24 24", "ic"), f === "stale" ? "STALE" : "LATE"])], {title: areaFeedName(id) + ". " + FRESH_TITLE, "data-x": x.st});
  }
  function lMini(id) {
    if (id === "storage") { var a = W.storage.aggr[0], d = (W.storage.ds || [])[0]; return [[a ? pct(a.v) + " %" : "-", "fullest aggregate", a ? "aggr:" + a.ref : null], [d ? pct(d.v) + " %" : "-", "fullest datastore", d ? dsRef(d) : null]]; }
    if (id === "vmware") { var hu = hostsUp(), v = W.vmware.vcenters; return [[hu[0] + " / " + hu[1], "hosts up", "ctr:hosts"], [v.filter(function (x) { return x.up; }).length + " / " + v.length, "vCenters up", "ctr:vc"]]; }
    return [];
  }
  function lOverview() {
    var list = areaList(), out = [];
    out.push(h("div", "whead", [h("h1", "", [W.title, h("small", "", ["Overview"])]), h("div", "meta", [h("span", "", [W.enclave || ""])])]));
    var crit = nowList(null).filter(function (i) { return i.sev === "crit"; });
    if (crit.length) {
      var c = crit[0];
      out.push(h("a", "lban", [lamp("crit"), h("div", "", [h("b", "", ["Critical now "]), h("span", "bt", [c.t + " · since " + hm(c.since) + " (" + dur(NOW() - c.since) + ")" + (crit.length > 1 ? " · +" + (crit.length - 1) + " more critical" : "")])]),
        h("span", "go", ["Details ›"])], {tabindex: "0", role: "button", "data-d": "c:" + c.id}));
    }
    var ov = h("div", "ov c3");
    list.forEach(function (a) {
      if (!a.on) return;
      var st = areaState(a.id), n = areaCounts(a.id), nc = st === "stale";
      ov.appendChild(h("a", "tile" + (st === "crit" ? " t-crit" : st === "warn" ? " t-warn" : st === "stale" ? " t-stale" : ""), [
        h("div", "th", [areaIcon(a.id), a.name]),
        h("div", "tb", [lamp(st), h("div", "", [h("div", "word" + (nc ? " sm" : ""), [WORD[st]]), h("div", "cnt", [nc ? h("span", "lastk", ["Last known: " + countText(lastKnown(a.id)).toLowerCase()]) : countText(n)])])]),
        h("div", "worst", [lWorst(a.id)]), greyNote(a.id),
        h("div", "mini", lMini(a.id).map(function (m) { return h("span", "", [h("b", "", [m[0]]), " " + m[1]], m[2] ? {"data-d": m[2], tabindex: "0", role: "button"} : null); })),
        lFresh(a.id)], {"data-goto": a.id, tabindex: "0", "aria-label": a.name + ": " + WORD[st]}));
    });
    var items = nowList(null), feedL = h("ul", "feed");
    items.slice(0, 9).forEach(function (i) {
      feedL.appendChild(h("li", i.sev, [lamp(i.sev), h("span", "", [i.t, h("span", "a", [aShort(i.area) + " \u00b7 " + (i.type === "cond" ? dur(NOW() - i.since) : "data " + (i.since ? ago(ageS(i.since)).replace(" ago", " old") : "never read"))])])],
        {tabindex: "0", role: "button", "data-d": i.type === "cond" ? "c:" + i.id : "feed:" + i.feed}));
    });
    if (!items.length) feedL.appendChild(overallState() === "ok" ? h("li", "", [lamp("ok"), h("span", "", ["Nothing on the wall", h("span", "a", ["no wall condition open"])])]) :
      h("li", "", [lamp("stale"), h("span", "", ["No current data", h("span", "a", ["nothing read yet, or nothing on the wall here"])])]));
    ov.appendChild(h("div", "tile feedt", [h("div", "th", [areaIcon("list"), "On the wall now", h("span", "chip", ["worst first, then longest"])]), feedL,
      items.length > 9 ? h("div", "lastk", ["+" + (items.length - 9) + " more, lower ranked"]) : null]));
    var offs = W.areas.filter(function (a) { return !a.on; }).map(function (a) { return [a.id, a.name, a.built ? (a.why || "no data yet") : "a later stage"]; }).concat([["list", "Network", "WAN and sites: a later stage"]]);
    ov.appendChild(h("div", "tile t-off offs", [h("div", "th", ["Not on the wall yet"]), h("div", "offl", offs.map(function (o) {
      return h("div", "offi", [lamp("off"), h("div", "", [h("b", "", [o[1]]), h("div", "muted", [o[2]])])]); }))]));
    out.push(ov);
    out.push(h("div", "foot", [h("span", "", ["Only wall conditions light the wall. Routine alarms and events never reach it."]), h("a", "", ["What the wall watches ›"], {"data-goto": "watch", tabindex: "0"}),
      h("span", "", ["Grey with ? = data not current; the wall never shows green on old data. Network: not on the wall yet."])]));
    return out;
  }
  function lGauge(name, v, w, c, sub, st, d, fk) {
    var cx = 100, cy = 98, r = 70, a0 = 150, sw = 240;
    var ptl = function (deg, rr) { var t = deg * Math.PI / 180; return [cx + rr * Math.cos(t), cy + rr * Math.sin(t)]; };
    var arcl = function (from, to) { var p1 = ptl(from, r), p2 = ptl(to, r); return "M" + p1[0].toFixed(1) + " " + p1[1].toFixed(1) + "A" + r + " " + r + " 0 " + (to - from > 180 ? 1 : 0) + " 1 " + p2[0].toFixed(1) + " " + p2[1].toFixed(1); };
    var ang = function (x) { return a0 + sw * x / 100; };
    var svg = s("svg", {viewBox: "0 0 200 170", role: "img", "aria-label": name + " " + pct(v) + " percent used, " + WORD[st]});
    svg.appendChild(s("path", {d: arcl(a0, a0 + sw), fill: "none", "class": "ltrk", "stroke-width": 16}));
    if (v != null) svg.appendChild(s("path", {d: arcl(a0, ang(Math.max(Math.min(v, 100), 0.5))), fill: "none", "class": "lfill", "stroke-width": 16, "stroke-dasharray": st === "stale" ? "6 4" : null}));
    [w, c].forEach(function (x) { if (x == null) return; var p1 = ptl(ang(x), r - 13), p2 = ptl(ang(x), r + 13), l = ptl(ang(x), r + 24);
      svg.appendChild(s("line", {x1: p1[0].toFixed(1), y1: p1[1].toFixed(1), x2: p2[0].toFixed(1), y2: p2[1].toFixed(1), stroke: "currentColor", "stroke-width": 2.5}));
      svg.appendChild(s("text", {"class": "gtick", x: l[0].toFixed(1), y: (l[1] + 4).toFixed(1), "text-anchor": "middle"}, [String(x)])); });
    var z = ptl(a0, r), hh2 = ptl(a0 + sw, r);
    svg.appendChild(s("text", {"class": "gtick", x: z[0].toFixed(1), y: z[1] + 26, "text-anchor": "middle"}, ["0"]));
    svg.appendChild(s("text", {"class": "gtick", x: hh2[0].toFixed(1), y: hh2[1] + 26, "text-anchor": "middle"}, ["100"]));
    svg.appendChild(s("text", {"class": "gtxt", x: 100, y: 102, "text-anchor": "middle"}, [st === "stale" ? "?" : pct(v), st === "stale" ? null : s("tspan", {"class": "gunit", dx: 2}, ["%"])]));
    svg.appendChild(s("text", {"class": "gword", x: 100, y: 128, "text-anchor": "middle"}, [WORD[st]]));
    return h("div", "gauge s-" + st, [svg, h("div", "gn", [name]), h("div", "gs", [st === "stale" ? greyGauge(fk, "value unread") : sub])], {tabindex: "0", role: "button", "data-d": d, "data-tip": name + "|" + pct(v) + " % · " + WORD[st] + (w != null ? " (warning " + w + " %, critical " + c + " %)" : " (line " + c + " %)")});
  }
  function lCells(list) {
    return h("div", "lg", list.map(function (x) { return h("div", "cell s-" + x.st + (x.st !== "ok" ? " bad" : ""), [lamp(x.st), h("span", "nm", [x.n])], {tabindex: "0", role: "button", "data-d": x.d, "data-tip": x.n + "|" + x.tip}); }));
  }
  function bigs(list) { return h("div", "big", list.map(function (b) { return h("div", "", [h("b", "", [String(b[0])]), h("span", "", [b[1]])], b[2] ? {"data-d": b[2], tabindex: "0", role: "button"} : null); })); }
  function lAreaHead(id) {
    var a = areaOf(id), st = areaState(id), n = areaCounts(id);
    return [h("div", "whead", [h("h1", "", [a.name, a.sub ? h("small", "", ["(" + a.sub + ")"]) : null]), h("div", "meta", [h("span", "", [W.enclave || ""]), fullLink(id)])]),
      h("div", "card sum", [lamp(st), h("div", "", [h("div", "word", [WORD[st]]), h("div", "cnt", [(st === "stale" ? "Last known: " + countText(lastKnown(id)).toLowerCase() : countText(n)) + " · " + lWorst(id)]), greyNote(id), lFresh(id)])])];
  }
  function lLegend(id) {
    var ks = Object.keys(W.kinds).filter(function (k) { return W.kinds[k].area === id; });
    return h("p", "span3 legend", [h("b", "", ["Lights this area: "]), ks.map(function (k) { return W.kinds[k].label + " (" + (W.kinds[k].levels || (W.kinds[k].sev === "crit" ? "critical" : "warning")) + ")"; }).join(" · "), ". ",
      h("b", "", ["Not on the wall: "]), areaOf(id).not_on_wall + " ", h("a", "lnk2", ["What the wall watches ›"], {"data-goto": "watch", tabindex: "0"})]);
  }
  function lBars(rows, w, c) {
    var g = h("div", "bars", [h("div", "baxis", [h("span"), h("div", "ax", [h("span", "a0", ["0"]), h("span", "", [w + " / " + c]), h("span", "a1", ["100"])]), h("span"), h("span")])]);
    rows.forEach(function (r) {
      var st = withFresh(r.crit ? "crit" : level(r.v, w, c), r.feed);
      var svg = s("svg", {viewBox: "0 0 100 10", preserveAspectRatio: "none", "class": "lbar"}, [s("rect", {x: 0, y: 0, width: 100, height: 10, "class": "trk"}),
        s("rect", {x: 0, y: 0, width: Math.max(0.5, Math.min(100, r.v || 0)), height: 10, "class": "lfill"}), s("line", {x1: w, x2: w, y1: -1, y2: 11, "class": "mk"}), s("line", {x1: c, x2: c, y1: -1, y2: 11, "class": "mk"})]);
      g.appendChild(h("div", "brow s-" + st, [h("span", "n", [r.n]), h("div", "btw", [svg]), h("span", "v", [pct(r.v) + " %"]), h("span", "st", [lamp(st), WORD[st]])],
        {tabindex: "0", role: "button", "data-d": r.d, "data-tip": r.n + "|" + pct(r.v) + " % · " + WORD[st]}));
    });
    return g;
  }
  var LVIEWS = {
    storage: function () {
      var stg = W.storage, t = thr("aggr"), dt = thr("ds"), n = areaCounts("storage");
      return lAreaHead("storage").concat([h("div", "av", [
        h("div", "card span2", [h("h2", "", ["Aggregates, % used", h("span", "r", [h("span", "chip", ["marks: warning " + t[0] + ", critical " + t[1]])])]),
          stg.aggr.length ? h("div", "gauges", stg.aggr.slice(0, 4).map(function (a) { return lGauge(a.n, a.v, t[0], t[1], size(a.free) + " free", withFresh(level(a.v, t[0], t[1]), a.feed), "aggr:" + a.ref, a.feed); })) : h("p", "legend", [stg.why || "No NetApp run yet."])]),
        h("div", "card", [h("h2", "", ["Now"]), bigs([[n.crit + n.warn, "wall conditions"], [stg.nodes[0] + " / " + stg.nodes[1], "nodes up", "ctr:nodes"], [stg.smBroken.length, "SnapMirror health and lag", "ctr:sm"], [nOpen(["dsfull", "dswarn"]).length, "datastores full", "ctr:ds"]])]),
        h("div", "card span2", [h("h2", "", ["Datastores, fullest " + Math.min(6, stg.ds.length) + " of " + stg.dsTotal, h("span", "r", [h("span", "chip", ["marks: warning " + dt[0] + ", critical " + dt[1] + " · click: storage path"])])]),
          lBars(stg.ds.slice(0, 6).map(function (d) { return {n: d.n, v: d.v, feed: d.feed, d: dsRef(d), crit: d.accessible === false}; }), dt[0], dt[1])]),
        h("div", "card", [h("h2", "", ["SnapMirror health and lag"]), stg.smBroken.length ? h("div", "lg", stg.smBroken.map(function (b) { return h("div", "cell bad s-" + withFresh(b.sev || "warn", b.feed || ntFeed()), [lamp(withFresh(b.sev || "warn", b.feed || ntFeed())), h("span", "nm", [b.dst])], {"data-d": "c:" + b.cond, tabindex: "0", role: "button"}); })) :
          h("span", "st", [lamp(status("ok", ntFeed(), W.storage.ran.sm)), status("ok", ntFeed(), W.storage.ran.sm) === "ok" ? "None over threshold or unhealthy" : WORD[status("ok", ntFeed(), W.storage.ran.sm)]]), h("p", "legend", [stg.smRel + " relationships. Unknown lag is grey."])]),
        lLegend("storage")])]);
    },
    vmware: function () {
      var v = W.vmware, hu = hostsUp(), n = areaCounts("vmware"), dt = thr("ds");
      var hosts = v.hosts.map(function (x) { return {n: x.n, st: withFresh(x.st, x.feed), d: "host:" + x.vc + "|" + x.n, tip: x.state + (x.maint ? ", maintenance" : "") + " · CPU " + pct(x.cpu) + " % · memory " + pct(x.mem) + " %"}; });
      var RK = {crit: 0, warn: 1, stale: 2, off: 3, ok: 4};
      hosts.sort(function (a, b) { return RK[a.st] - RK[b.st] || (a.n < b.n ? -1 : 1); });
      var bad = hosts.filter(function (x) { return x.st !== "ok"; }).length, shown = Math.max(bad, Math.min(hosts.length, 120)), rest = hosts.slice(shown);
      var hot = v.hot.map(function (x) { var c = BY[x.cond]; return h("div", "row", [lamp(withFresh("warn", x.feed)), h("span", "", [h("b", "", [(c ? c.t : x.n) + (c ? " for " + dur(NOW() - c.start) : "")]), h("br"), h("span", "legend", [x.k + " · " + (x.host ? "host " + x.host : "")])])], {"data-d": "c:" + x.cond, tabindex: "0", role: "button"}); });
      return lAreaHead("vmware").concat([h("div", "av", [
        h("div", "card span2", [h("h2", "", ["Hosts (" + v.hosts.length + ", problems first)", h("span", "r", [h("span", "chip", [(hu[1] - hu[0]) + " not responding"])])]), lCells(hosts.slice(0, shown)),
          rest.length ? h("p", "legend", ["+" + rest.length + " more not shown (" + rest.length + " " + WORD[rest[0].st].toLowerCase() + ")"]) : null]),
        h("div", "card", [h("h2", "", ["Now"]), bigs([[n.crit + n.warn, "wall conditions open"], [hu[0] + " / " + hu[1], "hosts up", "ctr:hosts"], [v.vcenters.filter(function (x) { return x.up; }).length + " / " + v.vcenters.length, "vCenters reachable", "ctr:vc"], [nOpen("dsi").length, "datastores inaccessible", "ctr:dsi"]])]),
        h("div", "card span2", [h("h2", "", ["Datastores, fullest 4", h("span", "r", [h("span", "chip", ["warning " + dt[0] + ", critical " + dt[1]])])]),
          h("div", "gauges", (W.storage.ds || []).slice(0, 4).map(function (d) { return lGauge(d.n, d.v, dt[0], dt[1], size(d.free) + " free", withFresh(dsLevel(d), d.feed), dsRef(d), d.feed); }))]),
        h("div", "card", [h("h2", "", ["Sustained load (" + thr("high")[0] + " % or more for " + thr("high")[1] + " min)"]), h("div", "sus", hot.length ? hot : [h("div", "row", [lamp(withFresh("ok", vcFeed())), h("span", "", [h("b", "", ["None"])])])]),
          v.building.length ? h("div", "blip", ["Not shown (under " + thr("high")[1] + " min): " + v.building.slice(0, 4).map(function (b) { return b.k + " " + b.n + " " + b.m.toLowerCase() + " " + pct(b.v) + " % for " + b.min + " min"; }).join(", ") + "."]) : null]),
        lLegend("vmware")])]);
    }
  };
  RENDER.lights = function (r) {
    var body = S.area === "watch" ? [h("div", "whead", [h("h1", "", ["What the wall watches", h("small", "", ["setup"])])]), h("div", "card", [watchEl()])] :
      S.area === "overview" ? lOverview() : LVIEWS[S.area]();
    add(r, body);
  };

  // ================================================================ MAP: sites, the last 4 hours, the Now list
  var CTRY = {US: [-98, 39], DE: [10.4, 51.2], GB: [-2.2, 53.6], IT: [12.6, 42.8], ES: [-3.7, 40.2], KE: [37.9, 0.2], DJ: [42.6, 11.8], JP: [138.5, 36.5], KR: [127.8, 36.4],
    BH: [50.6, 26.0], BE: [4.6, 50.6], NL: [5.3, 52.1], PL: [19.1, 51.9], QA: [51.2, 25.3], KW: [47.5, 29.3], AE: [54.3, 23.9], TR: [35.2, 39.0], GR: [21.8, 39.1], PT: [-8.2, 39.6],
    NO: [8.5, 61.0], RO: [24.9, 45.9], HU: [19.5, 47.2], CZ: [15.5, 49.8], AU: [134.0, -25.3], CA: [-106.3, 56.1], HN: [-86.2, 14.8], CU: [-79.5, 21.5], GU: [144.8, 13.4]};
  function sites() { return (W.sites || []).map(function (x) { return x.id; }); }
  function placeOf(id) {
    var p = null;
    (W.sites || []).forEach(function (x) { if (x.id === id) p = x.place; });
    if (Array.isArray(p) && p.length === 2) return [+p[0], +p[1]];
    if (typeof p === "string" && CTRY[p.toUpperCase()]) return CTRY[p.toUpperCase()];
    return null;
  }
  function siteState(id) {
    var cs = openConds().filter(function (c) { return c.site === id; }), live = cs.filter(function (c) { return !condStale(c); }), w = null;
    live.forEach(function (c) { if (!w || (c.sev === "crit" && w.sev !== "crit")) w = c; });
    if (w) return {k: w.sev, w: w.sev === "crit" ? "down" : "warning", c: w};
    if (cs.length) return {k: "stale", w: "not current", c: cs[0]};
    // nothing open: OK only when the sources that cover the site are current - never a green dot from old data
    var fks = {};
    var unknown = false;
    W.vmware.hosts.forEach(function (x) { if (x.site === id) { fks[x.feed] = 1; if (x.st === "stale") unknown = true; } });
    W.storage.aggr.forEach(function (x) { if (x.site === id && x.feed) fks[x.feed] = 1; });
    var keys = Object.keys(fks);
    if (!keys.length) areaList().forEach(function (a) { if (a.on) areaFeeds(a.id).forEach(function (k) { fks[k] = 1; }); });
    keys = Object.keys(fks);
    if (UNREACH || unknown || !keys.length || keys.some(function (k) { return status("ok", k) !== "ok"; })) return {k: "stale", w: "not current"};
    return {k: "ok", w: "ok"};
  }
  function frEl(asof) {
    var x = freshTxt(asof), e = h("span", "fr" + (x.st === "ok" ? "" : " f-" + x.st), [(x.st === "stale" ? "STALE · " : x.st === "late" ? "LATE · " : "") + x.t], {title: FRESH_TITLE});
    return e;
  }
  function my(lat) { return Math.log(Math.tan(Math.PI / 4 + Math.max(-80, Math.min(80, lat)) * Math.PI / 360)) * 180 / Math.PI; }
  function PJ(lon, lat) { return [lon, -my(lat)]; }
  var LANDPATH = null;
  function landPath() {
    if (LANDPATH != null) return LANDPATH;
    LANDPATH = (window.WALL_LAND || []).map(function (r) { var d = ""; for (var i = 0; i < r.length; i += 2) { var p = PJ(r[i], r[i + 1]); d += (i ? "L" : "M") + p[0].toFixed(2) + " " + p[1].toFixed(2); } return d + "Z"; }).join("");
    return LANDPATH;
  }
  function drawMap(svg) {
    clear(svg);
    var placed = sites().filter(placeOf);
    var Wd = svg.clientWidth || 900, Hd = svg.clientHeight || 500;
    var pts = placed.map(function (id) { var c = placeOf(id); return PJ(c[0], c[1]); });
    if (!pts.length) pts = [PJ(-120, 60), PJ(150, -40)];
    var x0 = Math.min.apply(null, pts.map(function (p) { return p[0]; })) - 24, x1 = Math.max.apply(null, pts.map(function (p) { return p[0]; })) + 24;
    var y0 = Math.min.apply(null, pts.map(function (p) { return p[1]; })) - 12, y1 = Math.max.apply(null, pts.map(function (p) { return p[1]; })) + 26;
    var bw = x1 - x0, bh = y1 - y0, ar = Wd / Hd;
    if (bw / bh < ar) { var nw = bh * ar; x0 -= (nw - bw) / 2; bw = nw; } else { var nh = bw / ar; y0 -= (nh - bh) / 2; bh = nh; }
    svg.setAttribute("viewBox", x0 + " " + y0 + " " + bw + " " + bh);
    var u = bw / Wd;
    svg.appendChild(s("path", {"class": "land", d: landPath(), "vector-effect": "non-scaling-stroke"}));
    var seen = {};
    placed.sort(function (a, b) { var r = {crit: 0, stale: 1, warn: 2, ok: 3}; return r[siteState(b).k] - r[siteState(a).k]; }).forEach(function (id) {
      var c = placeOf(id), p = PJ(c[0], c[1]), key = c.join(","), n = seen[key] = (seen[key] || 0) + 1;
      if (n > 1) p = [p[0] + (n % 2 ? 1 : -1) * 9 * u * Math.ceil((n - 1) / 2), p[1] + 5 * u * (n - 1)];
      var st = siteState(id), R = 11 * u, dim = S.site && S.site !== id;
      var g = s("g", {"class": "sdot k-" + st.k + (dim ? " dim" : ""), tabindex: 0, role: "button", "aria-label": id + ": " + st.w});
      if (st.k === "crit") g.appendChild(s("circle", {"class": "pulse", cx: p[0], cy: p[1], r: R, "stroke-width": 2 * u}));
      if (S.site === id) g.appendChild(s("circle", {"class": "sel", cx: p[0], cy: p[1], r: R + 5 * u, "stroke-width": 2.5 * u}));
      g.appendChild(s("circle", {"class": "b", cx: p[0], cy: p[1], r: R, "stroke-width": 2 * u, "stroke-dasharray": st.k === "stale" ? (3.2 * u) + " " + (2.4 * u) : "none"}));
      g.appendChild(s("text", {"class": "g", x: p[0], y: p[1] + 0.5 * u, "font-size": 12 * u}, [GLYPH[st.k]]));
      var left = p[0] > x0 + bw * 0.62, lx = left ? p[0] - R - 5 * u : p[0] + R + 5 * u, an = left ? "end" : "start";
      g.appendChild(s("text", {"class": "l1", x: lx, y: p[1] - 1 * u, "font-size": 13 * u, "stroke-width": 3.5 * u, "text-anchor": an}, [id]));
      if (st.c) g.appendChild(s("text", {"class": "l2", x: lx, y: p[1] + 12 * u, "font-size": 11.5 * u, "stroke-width": 3.5 * u, "text-anchor": an}, [st.k === "stale" ? "not current" : st.c.t.slice(0, 34)]));
      g.addEventListener("click", function () { if (st.c) openDetail("c:" + st.c.id); else { S.site = S.site === id ? "" : id; render(); } });
      svg.appendChild(g);
    });
  }
  function mapCard() {
    var cardEl = h("section", "card mapcard"), box = h("div", "mapbox"), svg = s("svg", {"class": "map", role: "img", "aria-label": "Site map"});
    var placed = sites().filter(placeOf).length;
    box.appendChild(svg);
    if (!placed) box.appendChild(h("div", "mapnote", [h("b", "", ["Sites are not placed on the map yet"]), sites().length ? " - " + sites().length + " sites found in the names (" + sites().slice(0, 8).join(", ") + (sites().length > 8 ? ", …" : "") + "). " : " - no site code found in the cluster or host names. ",
      "The Map uses the network map's netmap_sites ({lat, lon} by site name or pattern, in all.yml); set it there, or portal_wall_site_places (a country code or [longitude, latitude] per site) to put them here."]));
    add(cardEl, [h("div", "ch", [h("h2", "", ["Where"]), h("span", "sub", [S.site ? "focused on " + S.site + " · click again to clear" : "click a dot: the condition that lights it"])]), box,
      h("div", "maplg", [h("span", "", ["✕ down"]), h("span", "", ["! warning"]), h("span", "", ["✓ ok"]), h("span", "", ["? hollow = not current (older than 10 min, never green)"]), h("span", "", ["dots: VMware and Storage conditions · no WAN lines (Network not on the wall yet)"])])]);
    later(function () { drawMap(svg); });
    return cardEl;
  }
  function scopeArea() { return S.area === "overview" || S.area === "watch" ? "" : S.area; }
  function tlEntries() { var from = NOW() - 4 * 60 * MIN, a = scopeArea(); return W.conds.filter(function (c) { return (!a || c.area === a) && (c.end == null || c.end >= from) && (!S.site || c.site === S.site); }); }
  function place(e, prop) { for (var k in prop) e.style[k] = prop[k]; return e; }
  function drawTimeline(tl, list) {
    clear(tl);
    var Hh = tl.clientHeight - 14, top = 8, span = 4 * 60 * MIN, now = NOW(), y = function (t) { return top + (now - t) / span * Hh; };
    var first = Math.floor(now / (30 * MIN)) * 30 * MIN;
    for (var t = first; t >= now - span; t -= 30 * MIN) { var yy = y(t); if (yy < top + 14) continue; tl.appendChild(place(h("div", "gl"), {top: yy + "px"})); tl.appendChild(place(h("div", "tk", [hm(t)]), {top: yy + "px"})); }
    tl.appendChild(place(h("div", "nowl"), {top: top + "px"}));
    tl.appendChild(place(h("div", "nowt", ["now"]), {top: top + "px"}));
    if (!list.length) {
      var a0 = scopeArea(), okNow = (a0 ? areaState(a0) : overallState()) === "ok";       // "nothing" said in green only on current data
      var young = W.since != null && NOW() - W.since * 1000 < 4 * 60 * MIN;    // a cold start: it has not watched 4 hours yet
      tl.appendChild(h("div", "empty tlempty" + (okNow ? "" : " nocur"), [h("b", "", [okNow ? (young ? "✓ Nothing since " + hm(W.since * 1000) + " (watching since then)" : "✓ Nothing in the last 4 hours") : "? No current data"]),
        okNow ? "No wall conditions for this filter." : "Nothing on the wall - but the sources are not current, so it is not known."]));
      return;
    }
    var ents = list.map(function (c) { return {c: c, s: Math.max(c.start, now - span), e: c.end == null ? now : c.end}; }).sort(function (a, b) { return a.s - b.s; });
    var lanesEnd = [];
    ents.forEach(function (x) { var L = 0; while (lanesEnd[L] != null && lanesEnd[L] > x.s - 3 * MIN) L++; lanesEnd[L] = x.e; x.lane = L; });
    var nL = Math.max(1, Math.min(10, lanesEnd.length)), LX = 50, LW = 7;
    ents.forEach(function (x) {
      var sev = condStale(x.c) && x.c.end == null ? "stale" : x.c.sev;
      tl.appendChild(place(h("div", "bar k-" + sev + (x.c.end == null ? " open" : " cl"), null, {"data-d": "c:" + x.c.id, "data-tip": (x.c.site ? x.c.site + " · " : "") + x.c.t + "|" + hm(x.c.start) + " → " + (x.c.end ? hm(x.c.end) : "ongoing")}),
        {left: (LX + Math.min(9, x.lane) * LW) + "px", top: y(x.e) + "px", height: Math.max(5, y(x.s) - y(x.e)) + "px"}));
    });
    var CH = 46, GAP = 4, cardLeft = LX + nL * LW + 18, avail = Hh + 4;
    var cards = ents.map(function (x) { return {x: x, ay: x.c.end == null ? y(x.s) : y(x.e)}; }).sort(function (a, b) { return a.ay - b.ay; });
    var dropped = 0;
    function layout() {
      var yy2 = -1e9;
      cards.forEach(function (k) { k.y = Math.max(k.ay - CH / 2, yy2 + GAP, top + 4); yy2 = k.y + CH; });
      var lim = top + avail - (dropped ? 18 : 0);
      for (var i = cards.length - 1; i >= 0; i--) { var nx = i === cards.length - 1 ? lim : cards[i + 1].y - GAP; if (cards[i].y + CH > nx) cards[i].y = nx - CH; }
      return !cards.length || cards[0].y >= top + 2;
    }
    var droppedOpen = 0;
    while (!layout()) { var idx = -1; for (var i = cards.length - 1; i >= 0; i--) if (cards[i].x.c.end != null) { idx = i; break; } if (idx < 0) idx = cards.length - 1; if (cards[idx].x.c.end == null) droppedOpen++; cards.splice(idx, 1); dropped++; }
    var lead = s("svg", {"class": "lead"});
    tl.appendChild(lead);
    cards.forEach(function (k) {
      var c = k.x.c, cl = c.end != null, ax = LX + Math.min(9, k.x.lane) * LW + 2.5, stl = !cl && condStale(c), sev = stl ? "stale" : c.sev;
      lead.appendChild(s("path", {d: "M" + ax + " " + k.ay + "C" + (cardLeft - 10) + " " + k.ay + " " + (ax + 10) + " " + (k.y + CH / 2) + " " + cardLeft + " " + (k.y + CH / 2)}));
      lead.appendChild(s("circle", {"class": "dotk k-" + sev, cx: ax, cy: k.ay, r: 4}));
      var w = cl ? "✓ cleared" : stl ? "? not current" : GLYPH[c.sev] + " " + (c.sev === "crit" ? "down" : "warning");
      var tm = cl ? h("span", "tm", [hm(c.start) + " → ", h("b", "", [hm(c.end)]), " · lasted " + dur(c.end - c.start)]) : h("span", "tm", [h("b", "", [when(c.start)]), " → ongoing · " + dur(now - c.start)]);
      tl.appendChild(place(h("div", "ev k-" + sev + (cl ? " cl" : ""), [h("div", "r1", [h("span", "w", [w]), c.site ? h("span", "s mono", [c.site]) : null, h("span", "c", [c.t])]), h("div", "r2", [tm, " · " + aShort(c.area)])],
        {"data-d": "c:" + c.id, tabindex: "0", role: "button"}), {left: cardLeft + "px", top: k.y + "px", height: CH + "px"}));
    });
    // Codex r5 #19: "cleared" only for the ones that cleared
    if (dropped) tl.appendChild(h("div", "more", ["+" + dropped + " earlier (" + (dropped - droppedOpen ? (dropped - droppedOpen) + " cleared" : "") + (dropped - droppedOpen && droppedOpen ? ", " : "") + (droppedOpen ? droppedOpen + " still open" : "") + ") · pick an area or a site to see them"]));
  }
  function timelineCard() {
    var c = h("section", "card tlcard"), tl = h("div", "tl"), list = tlEntries();
    add(c, [h("div", "ch", [h("h2", "", ["Last 4 hours"]), h("span", "sub", [list.length + " wall conditions · started → cleared"]), h("span", "r", ["now at top"])]), tl]);
    later(function () { drawTimeline(tl, list); });
    return c;
  }
  function nowCard() {
    var a = scopeArea(), items = nowList(a || null).filter(function (i) { return !S.site || i.site === S.site; }), c = h("section", "card nowcard"), nl = h("div", "nl");
    add(c, [h("div", "ch", [h("h2", "", ["Now"]), h("span", "sub", [items.length + " open · worst first, then longest"]), h("span", "r", ["each row: data as of"])]), nl]);
    if (!items.length && (a ? areaState(a) : overallState()) !== "ok") nl.appendChild(h("div", "empty nocur", [h("b", "", ["? No current data"]), "Nothing read yet, or every source is old."]));
    else if (!items.length) nl.appendChild(h("div", "empty", [h("b", "", ["✓ Nothing on the wall"]), "No open conditions" + (a ? " for " + aShort(a) : "") + (S.site ? " at " + S.site : "") + "."]));
    items.forEach(function (it) {
      var row;
      if (it.type !== "cond") {
        row = h("div", "nr k-stale", [h("div", "rk", [h("span", "w", ["? not current"]), h("span", "team", [aShort(it.area)])]), h("div", "h", [it.t]),
          h("div", "d", [h("b", "", [it.since ? ago(ageS(it.since)).replace(" ago", "") : "never"]), h("span", "", [it.since ? "since " + hms(it.since) : ""])]), h("div", "o", [it.sub]), frEl(it.since)],
          {"data-d": "feed:" + it.feed, tabindex: "0", role: "button"});
      } else {
        var c2 = it.c, f = feed(c2.feed);
        row = h("div", "nr k-" + c2.sev, [h("div", "rk", [h("span", "w", [GLYPH[c2.sev] + " " + (c2.sev === "crit" ? "down" : "warning")]), h("span", "team", [aShort(c2.area)])]),
          h("div", "h", [c2.site ? h("span", "code", [c2.site]) : null, c2.t]), h("div", "d", [h("b", "", [dur(NOW() - c2.start)]), h("span", "", ["since " + (NOW() - c2.start > 72e6 ? when(c2.start) : hms(c2.start))])]),
          h("div", "o", [c2.obj || ""]), f ? frEl(f.asof) : h("span", "fr", ["now"])], {"data-d": "c:" + c2.id, tabindex: "0", role: "button"});
      }
      nl.appendChild(row);
    });
    return c;
  }
  var GROUPS = [["hostvc", "Host / vCenter down"], ["dsvm", "Datastore inaccessible / VM no heartbeat"], ["load", "CPU / memory sustained high"], ["ds", "Datastore full"], ["aggr", "Aggregate full / node down"], ["sm", "SnapMirror health and lag"]];
  function condTiles() {
    var cardEl = h("section", "card wide"), g = h("div", "conds"), from = NOW() - 4 * 60 * MIN, a = scopeArea();
    GROUPS.forEach(function (gr) {
      var ks = Object.keys(W.kinds).filter(function (k) { return W.kinds[k].grp === gr[0] && (!a || W.kinds[k].area === a); });
      if (!ks.length) return;
      var now = openConds().filter(function (c) { return ks.indexOf(c.kind) >= 0 && (!S.site || c.site === S.site); });
      var h4 = W.conds.filter(function (c) { return ks.indexOf(c.kind) >= 0 && (c.end == null || c.end >= from) && (!S.site || c.site === S.site); }).length;
      var cur = now.filter(function (c) { return !condStale(c); }), stale = now.length && !cur.length;
      var sev = cur.some(function (c) { return c.sev === "crit"; }) ? "crit" : cur.length ? "warn" : now.length ? "stale" : "";
      var ref = now.length === 1 ? "c:" + now[0].id : "grp:" + gr[0];
      g.appendChild(h("div", "cd" + (sev ? " on k-" + sev : " zero"), [h("div", "n", [stale ? "?" : String(now.length), sev ? h("i", "", [(stale ? "not current" : GLYPH[sev] + " " + (sev === "crit" ? "down" : "warning"))]) : null]),
        h("div", "nm", [gr[1]]), h("div", "h4", [stale ? "last known " + now.length + " · last 4 h: " + h4 : "last 4 h: " + h4])], {title: gr[1], "data-d": ref, tabindex: "0", role: "button"}));
    });
    add(cardEl, [h("div", "ch", [h("h2", "", ["Wall conditions"]), h("span", "sub", ["open now · and in the last 4 hours · routine alarms never shown"])]), g]);
    return cardEl;
  }
  DET.grp = function (g) {
    var gr = null; GROUPS.forEach(function (x) { if (x[0] === g) gr = x; });
    if (!gr) return null;
    var ks = Object.keys(W.kinds).filter(function (k) { return W.kinds[k].grp === g; }), list = nOpen(ks), area = W.kinds[ks[0]].area;
    var from = NOW() - 4 * 60 * MIN, cl = W.conds.filter(function (c) { return ks.indexOf(c.kind) >= 0 && c.end != null && c.end >= from; });
    var cur = list.filter(function (c) { return !condStale(c); });
    return {title: gr[1], st: cur.length ? (cur.some(function (c) { return c.sev === "crit"; }) ? "crit" : "warn") : list.length ? "stale" : status("ok", g === "ds" ? dsFeed() : area === "storage" ? ntFeed() : vcFeed(), g === "sm" ? W.storage.ran.sm : g === "aggr" ? W.storage.ran.nodes : g === "dsvm" ? W.thresholds.vm_heartbeat : undefined), chips: [aShort(area)], area: area, rows: [
      exclusions(g === "aggr" ? ["aggregates", "volumes"] : g === "ds" ? ["datastores"] : []), ["Wall rules", ks.map(function (k) { return W.kinds[k].label; }).join(" · ")], ["Open now", condRows(list, g === "ds" ? dsFeed() : area === "storage" ? ntFeed() : vcFeed(), g === "sm" ? W.storage.ran.sm : g === "aggr" ? W.storage.ran.nodes : g === "dsvm" ? W.thresholds.vm_heartbeat : undefined)], ["Cleared, last 4 h", cl.length ? condRows(cl, "-live-") : "none"]]};
  };
  function siteChips() {
    var cardEl = h("section", "card wide"), g = h("div", "sites");
    sites().forEach(function (id) {
      var st = siteState(id), n = openConds().filter(function (c) { return c.site === id; }).length;
      var c = h("div", "sc k-" + st.k + (S.site === id ? " sel" : ""), [h("div", "a", [h("span", "w", [GLYPH[st.k]]), h("b", "", [id])]), h("span", "fr", [st.k === "stale" ? "not current" : n ? n + " open" : "no wall condition"])], {tabindex: "0", role: "button"});
      c.addEventListener("click", function () { S.site = S.site === id ? "" : id; render(); });
      g.appendChild(c);
    });
    if (!sites().length) g.appendChild(h("div", "empty", ["No site code found in the names (portal_wall_site_pattern)."]));
    add(cardEl, [h("div", "ch", [h("h2", "", ["Sites"]), h("span", "sub", ["click a site to focus the timeline and the list"])]), g]);
    return cardEl;
  }
  function mapCtl(r) {
    var feeds = h("div", "feeds");
    Object.keys(W.feeds).forEach(function (k) { var f = W.feeds[k], x = freshTxt(f.asof); feeds.appendChild(h("span", "feed" + (x.st === "ok" ? "" : " f-" + x.st), [h("b", "", [f.name]), " ", frEl(f.asof)], {"data-d": "feed:" + k, tabindex: "0", role: "button", title: f.src})); });
    r.appendChild(h("div", "ctl", [feeds]));
    var c = h("div", "ctl", [h("span", "lbl", ["Area"])]);
    var mk = function (label, id) { var n = id ? openConds(id).length : openConds().length; return h("button", "chip" + ((S.area === id || (!id && S.area === "overview")) ? " on" : ""), [label + " ", h("span", "n", [String(n)])], {"data-goto": id || "overview", type: "button"}); };
    c.appendChild(mk("All", ""));
    areaList().forEach(function (a) { if (a.on) c.appendChild(mk(a.short || a.name, a.id)); });
    if (S.site) { var x = h("button", "", ["Show all sites"], {type: "button"}); x.addEventListener("click", function () { S.site = ""; render(); }); c.appendChild(h("span", "focus", ["Focus: " + S.site + " ", x])); }
    var t = h("div", "tally", [h("span", "lbl", ["Sites"])]), n = {crit: 0, warn: 0, stale: 0, ok: 0};
    sites().forEach(function (id) { n[siteState(id).k]++; });
    [["crit", "down"], ["warn", "warning"], ["stale", "not current"], ["ok", "ok"]].forEach(function (k) { if (!n[k[0]]) return; t.appendChild(h("span", "tag k-" + k[0], [h("i", "", [GLYPH[k[0]]]), " " + n[k[0]] + " " + k[1]])); });
    if (scopeArea()) add(t, [fullLink(scopeArea())]);          // none for VMware (no portal page): nothing added
    c.appendChild(t);
    r.appendChild(c);
  }
  RENDER.map = function (r) {
    mapCtl(r);
    if (S.area === "watch") { r.appendChild(h("section", "card padc", [watchEl()])); return; }
    if (scopeArea()) r.appendChild(h("div", "fb", [h("p", "fallback", [icon([["circle", {cx: 8, cy: 8, r: 6.4, fill: "none", stroke: "currentColor", "stroke-width": 1.4}], P("M8 7.2v4M8 4.8v.2", {"stroke-width": 1.6, "stroke-linecap": "round"})], "0 0 16 16"),
      h("span", "", ["Map draws an area as the overview filtered to that team: dots, timeline, tiles and Now list show " + aShort(scopeArea()) + " only."])])]));
    var crit = nowList(scopeArea() || null).filter(function (i) { return i.sev === "crit"; });
    if (crit.length) r.appendChild(h("div", "ostrip", [h("span", "w", ["✕ CRITICAL NOW"]), h("span", "what", [crit[0].t]), h("span", "", ["since " + hms(crit[0].since) + " · " + dur(NOW() - crit[0].since)]),
      crit.length > 1 ? h("span", "", ["+" + (crit.length - 1) + " more critical"]) : null, h("span", "go", ["Details →"])], {"data-d": "c:" + crit[0].id, tabindex: "0", role: "button"}));
    var grid = h("div", "ov"), mid = h("div", "mid");
    add(mid, [mapCard(), condTiles(), siteChips()]);
    add(grid, [timelineCard(), mid, nowCard()]);
    r.appendChild(h("div", "mpage", [grid]));
  };

  // ================================================================ What the wall watches (every style): from the answer's own rules
  function liveOntap() { return Object.keys(W.feeds).some(function (k) { return k.indexOf("ontap:") === 0; }); }
  function watchEl() {
    var hp = thr("high"), wrap = h("div", "wwatch");
    add(wrap, [h("p", "wlead", [h("b", "", ["Routine alarms and events never reach the wall"]), " (informational alarms, short spikes, snapshot age, tools out of date). Only the conditions below light an area. Freshness: each feed shows ",
      h("b", "", ["as of"]), " with its age; ", h("b", "", ["LATE"]), " (amber) after " + Math.round(LATE() / 60) + " min, ", h("b", "", ["STALE"]), " after " + Math.round(STALE() / 60) + " min. A stale feed is never green: a hollow grey ", h("b", "", ["?"]), " and the words ", h("b", "", ["Not current"]), "; its last values stay, grey."]),
      h("p", "wlead", [h("b", "", ["Thresholds in force: "]), "datastore " + thr("ds")[0] + " / " + thr("ds")[1] + " %, aggregate " + thr("aggr")[0] + " / " + thr("aggr")[1] + " %, CPU or memory " + hp[0] + " % for " + hp[1] + " min",
        (W.thresholds.maintenance_expected || []).length ? "; maintenance expected for " + W.thresholds.maintenance_expected.join(", ") : "", ". The portal's settings portal_wall_* (docs/SITE_PORTAL.md)."]),
      h("p", "wlead", [h("b", "", ["Enclave: "]), (W.enclave || "this portal's") + " - one portal serves one enclave. ", h("b", "", ["Network: not on the wall yet."]), " VDI, Active Directory and Outages & problems: not on the wall yet (grey tiles)."])]);
    var tb = h("table", "wtab", [h("thead", "", [h("tr", "", [h("th", "", ["Area"]), h("th", "", ["Wall condition"]), h("th", "", ["Level"]), h("th", "", ["Read from"]), h("th", "", ["Not on the wall"])])])]), body = h("tbody");
    W.areas.forEach(function (a) {
      var ks = Object.keys(W.kinds).filter(function (k) { return W.kinds[k].area === a.id; });
      if (!ks.length) { body.appendChild(h("tr", "", [h("td", "ar", [a.name]), h("td", "muted", ["not on the wall yet"]), h("td"), h("td"), h("td", "no", [""])])); return; }
      ks.forEach(function (k, i) {
        var tr = h("tr");
        if (i === 0) tr.appendChild(h("td", "ar", [a.name], {rowspan: ks.length}));
        add(tr, [h("td", "", [W.kinds[k].label]), h("td", "lv " + W.kinds[k].sev, [W.kinds[k].levels || (W.kinds[k].sev === "crit" ? "critical" : "warning")]),
          h("td", "muted", [k === "voloff" ? "ONTAP, live (the NetApp ONTAP credential)" : ["aggrfull", "aggrwarn", "sm", "node"].indexOf(k) >= 0 ?
            (liveOntap() ? "ONTAP, live" : "NetApp - refresh volume list's run (report database)") : "vCenter, live"])]);
        if (i === 0) tr.appendChild(h("td", "no", [a.not_on_wall], {rowspan: ks.length}));
        body.appendChild(tr);
      });
    });
    body.appendChild(h("tr", "", [h("td", "ar", ["Network"]), h("td", "muted", ["not on the wall yet"]), h("td"), h("td"), h("td", "no", [""])]));
    tb.appendChild(body);
    wrap.appendChild(tb);
    var fl = h("div", "wfeeds");
    Object.keys(W.feeds).forEach(function (k) { var f = W.feeds[k]; fl.appendChild(h("div", "", [h("button", "lnk", [f.name], {type: "button", "data-d": "feed:" + k}), " · " + f.src + " · ", frEl(f.asof)])); });
    add(wrap, [h("h3", "", ["Data sources"]), fl]);
    return wrap;
  }

  // ---------------------------------------------------------------- start: the URL, this viewer's choice, the answer
  (function () {
    // the address's style, else this viewer's last pick, else the site's portal_wall_style (data-style), else Cockpit
    var st = QS.get("style"), dflt = root.getAttribute("data-style");
    S.style = STYLES.indexOf(st) >= 0 ? st : (STYLES.indexOf(sget(K_STYLE)) >= 0 ? sget(K_STYLE) : (STYLES.indexOf(dflt) >= 0 ? dflt : "cockpit"));
    S.area = canonicalArea(QS.get("area") || "overview");
    var th = QS.get("theme"); if (th === "dark" || th === "light") document.documentElement.setAttribute("data-theme", th);
    var r = QS.get("rotate"); if (r === "styles" || r === "areas") { ROT.on = true; ROT.mode = r; }
    var ev = +QS.get("every"); if (ev === 30 || ev === 60 || ev === 120) ROT.every = ev; ROT.left = ROT.every;
  })();
  function load(first) {
    var hd = {Accept: "application/json"};
    if (ETAG && !first) hd["If-None-Match"] = ETAG;
    return fetch(SRC, {headers: hd, credentials: "same-origin", cache: "no-store"}).then(function (res) {
      if (res.status === 304) return null;
      if (!res.ok) throw new Error("HTTP " + res.status);
      ETAG = (res.headers && res.headers.get && res.headers.get("ETag")) || "";
      return res.json();
    }).then(function (a) {
      FAILS = 0;
      if (UNREACH) { UNREACH = false; if (!a && W) { render(); return; } }
      if (!a) return;
      var keepScroll = window.scrollY;
      if (a.now && !FREEZE) SKEW = a.now * 1000 - Date.now();
      W = adapt(a);
      setZone(W.zone || "UTC");
      if (!LOADED) {
        LOADED = true;
        frame();
        if (S.area !== "overview" && S.area !== "watch" && !areaOn(S.area)) S.area = "overview";
        if (QS.get("kiosk") === "1") setKiosk(true, false);
      }
      render();
      if (first && QS.get("open")) { if (QS.get("open") === "feedback") openFeedback(""); else openDetail(QS.get("open")); }
      window.scrollTo(0, keepScroll);
      root.setAttribute("data-ready", "1");
    }).catch(function (e) {
      if (!LOADED) { clear(root); root.appendChild(h("p", "note warn", ["The wall could not be read: " + e.message + ". It tries again in 30 s."])); }
      else {
        try { console.error("wall: " + (e && e.stack || e)); } catch (x) { /* no console */ }
        FAILS++;
        var c = $("#clock"); if (c) c.classList.add("late");
        if (FAILS >= FAILS_MAX && !UNREACH) { UNREACH = true; UNREACH_SINCE = NOW(); render(); }
      }
    });
  }
  // the test harness only (data-test): set an answer, draw a view, open a card, list what is drawn green - the tests' fuzz run
  if (TEST) {
    PENDING = [];
    // the OK colours of the theme in force (any that equals a colour that is not OK left out): what "green" is on this page
    var okColours = function () {
      var probe = document.createElement("span"), cs = getComputedStyle(document.body), seen = {};
      root.appendChild(probe);
      var rgb = function (tok) { var v = cs.getPropertyValue(tok).trim(); if (!v) return ""; probe.style.color = ""; probe.style.color = v; return getComputedStyle(probe).color; };
      var not = ["--ink", "--muted", "--label", "--accent", "--accent-ink", "--bg", "--card", "--card-strong", "--edge", "--line", "--li-ok", "--li-warn",
                 "--li-crit", "--li-stale", "--g-track", "--track", "--g-thr", "--g-tick", "--sea", "--land", "--landline"].map(rgb);
      ["--ok", "--l-ok", "--m-ok", "--g-ok", "--ok-bg", "--okc-bg", "--gl-ok"].forEach(function (t) { var c = rgb(t); if (c && not.indexOf(c) < 0) seen[c] = t; });
      root.removeChild(probe);
      return seen;
    };
    var visible = function (e) { var r = e.getClientRects(); if (!r.length) return false; var st = getComputedStyle(e); return st.visibility !== "hidden" && st.display !== "none" && +st.opacity > 0; };
    window.__wallTest = {
      set: function (a) { if (LOADED) closeDetail(); W = adapt(a); setZone(W.zone || "UTC"); if (!LOADED) { LOADED = true; frame(); } render(); flush(); },
      view: function (st, area) { closeDetail(); S.style = st; S.area = area; S.site = ""; render(); flush(); },
      open: function (ref) { closeDetail(); openDetail(ref); flush(); },
      // every visible element of the wall, its computed colours (text, fill, stroke, background, border) against the OK colours
      greens: function () {
        var ok = okColours(), out = [], n = 0;
        root.querySelectorAll("*").forEach(function (e) {
          if (e.closest("#tip")) return;
          if (e.closest("#dsBack") && !back.classList.contains("on")) return;
          if (!visible(e)) return;
          n++;
          var st = getComputedStyle(e), hit = "";
          var inSvg = e instanceof SVGElement, ownText = [].some.call(e.childNodes, function (x) { return x.nodeType === 3 && x.nodeValue.trim(); });
          var props = ["background-color", "border-left-color", "border-top-color", "box-shadow"];
          if (ownText) props.push("color");
          if (inSvg && e.tagName.toLowerCase() !== "svg") props.push("fill", "stroke");
          props.forEach(function (p) {
            var v = st.getPropertyValue(p);
            if (p.indexOf("border") === 0 && parseFloat(st.getPropertyValue(p.replace("-color", "-width"))) === 0) return;
            Object.keys(ok).forEach(function (c) { if (!hit && v && v.indexOf(c) >= 0) hit = p + "=" + ok[c]; });
          });
          if (hit) { var c = e.closest("[data-d]"); out.push(hit + " " + e.tagName.toLowerCase() + "." + (e.getAttribute("class") || "") + " @ " + (c ? c.getAttribute("data-d") : (e.textContent || "").trim().slice(0, 40))); }
        });
        return {greens: out, scanned: n, sdots: root.querySelectorAll(".sdot, .mapnote").length, ticks: root.querySelectorAll(".tl .tk, .tl .nowt").length};
      }
    };
  }
  load(true);
  if (!FREEZE) {
    var PULL = TEST && QS.get("pull") ? Math.max(2, +QS.get("pull") || 2) : 0;      // ?pull=N: a shorter read interval (the tests)
    var pullIn = PULL || 30;
    var firstIn = PULL || 30;
    setInterval(function () {
      if (!W) { if (--firstIn <= 0) { firstIn = PULL || 30; load(true); } return; }      // the first read failed: tried again
      tickClock();
      if (ROT.on) {
        if (ROT.resumeAt) { if (Date.now() >= ROT.resumeAt) { ROT.resumeAt = 0; ROT.left = ROT.every; } }
        else { ROT.left--; if (ROT.left <= 0) { ROT.left = ROT.every; renderRot(); rotStep(); return; } }
        renderRot();
      }
      pullIn--;
      if (pullIn <= 0) { pullIn = PULL || (W && W.poll) || 30; load(false); return; }
      if (freshSig() !== LASTF) { var y = window.scrollY; render(); window.scrollTo(0, y); return; }     // a feed went LATE / STALE
      repaintFresh();
    }, 1000);
  }
})();
