// 1.136.0: the ops wall's "Ops monitor" area (?area=monitor) - every node SolarWinds watches, up or down, by service group,
// compared with vCenter (roles/site_portal_stack/files/wall_fleet.py makes /api/wall/fleet). One board in every wall style (the
// style's colours and type): the services strip (server group verdict; click = the Spotlight), one square a node on a canvas (down: a
// red ring and its name; warning: amber with a notch; grey hollow: unmanaged; grey: no current data - never green),
// "Not up · worst first" with checkboxes, "SolarWinds vs vCenter", and a Spotlight that steps through the groups, problems first.
// A click on a node (a square, a row, a chip) opens its card: Copy text / picture / both, Save picture;
// Ctrl / Shift + click selects several, "Share selected to Teams" shares them at once. No HTML from strings (the page's CSP): the
// DOM is built node by node, every value from the answer goes in as text.
(function () {
  "use strict";
  var A = null, F = null, FAIL = "", LOADED_AT = 0, lastBoot = 0, lastSeq = 0, BUSY = false, VER = 0;
  var B = null, P = {};                       // the board (kept across the page's redraws, so an open card survives them) and its parts
  var SEL = Object.create(null), LAST_SEL = "", OV = null;          // no prototype: a key from the address (?node=) is only ever a node                    // the selection (node keys), the open card or list
  var SPOT = {g: null, left: 20, pausedUntil: 0}, DWELL = 20;
  var LAY = {blocks: [], S: 10, k: 1};
  var ST_WORD = {d: "DOWN", w: "WARN", p: "OFF", m: "UNMANAGED", x: "UNKNOWN", u: "UP"};
  var KIND = {g: "SolarWinds group", c: "custom property", n: "in SolarWinds, no group", v: "vCenter inventory only"};

  function h(t, c, k, a) { return A.h(t, c, k, a); }
  function W() { return A.W(); }
  function NOW() { return F ? F.now * 1000 + (A.frozen && frozen() ? 0 : performance.now() - F.receipt) : A.NOW(); }
  function current(until) { return Number.isFinite(until) && NOW() < until * 1000; }
  function frozen() { return A.frozen(); }
  function clear(e) { while (e.firstChild) e.removeChild(e.firstChild); return e; }
  function soon(fn) { requestAnimationFrame(function () { fn(); }); }      // after the layout (never the page's test queue)

  // ---------------------------------------------------------------- the answer
  function accept(a) {
    if (!Number.isSafeInteger(a.boot) || !Number.isSafeInteger(a.seq)) return false;
    if (a.boot < lastBoot || (a.boot === lastBoot && a.seq < lastSeq)) return false;
    lastBoot = a.boot; lastSeq = a.seq;
    return true;
  }
  function encoded(value) {
    var s = String(value);
    return encodeURIComponent(s.toWellFormed ? s.toWellFormed() :
      s.replace(/[\uD800-\uDBFF][\uDC00-\uDFFF]|[\uD800-\uDFFF]/g, function (m) { return m.length === 2 ? m : "\uFFFD"; }));
  }
  function url() { return A.src + "/fleet"; }
  function adapt(a) {
    var receipt = performance.now();
    var arr = function (x) { return Array.isArray(x) ? x : []; };
    var groups = arr(a.groups).map(function (g, i) { return {i: i, name: String(g[0] || ""), kind: g[1] || "g", verdict: g[3] || {}, nodes: []}; });
    var hosts = arr(a.hosts), models = arr(a.models), vcs = arr(a.vcs), vmw = a.vmw || {};
    var nodes = arr(a.nodes).filter(Array.isArray).map(function (r) {
      var n = {g: +r[0] || 0, name: String(r[1] == null ? "" : r[1]), ip: String(r[2] || ""), c: ST_WORD[r[3]] ? r[3] : "x", rt: typeof r[4] === "number" ? r[4] : null,
               loss: typeof r[5] === "number" ? r[5] : null, since: r[6] ? r[6] * 1000 : 0, exact: !!r[7], why: String(r[8] || ""), vm: vmw[r[9]] || "",
               host: hosts[r[10]] || "", vc: vcs[r[11]] ? String(vcs[r[11]][0]) : "", flags: String(r[12] || ""), key: String(r[13]), also: arr(r[14]),
               polled: typeof r[15] === "number" ? r[15] : null, said: String(r[16] || ""), model: models[r[17]] || "", vci: typeof r[11] === "number" ? r[11] : -1};
      n.verdict = r[18] || "unk"; n.until = r[19];
      n.vcAt = (vcs[n.vci] || [])[1];
      n.vconly = n.flags.indexOf("v") >= 0;
      return n;
    });
    // g.nodes: its squares (each node once, in its first group); g.members: every node in it (a node in two groups counts in both)
    groups.forEach(function (g) { g.members = []; });
    nodes.forEach(function (n) { var g = groups[n.g]; if (g) { g.nodes.push(n); g.members.push(n); } else n.g = -1; });
    nodes.forEach(function (n) { if (n.g >= 0) n.also.forEach(function (i) { if (groups[i] && i !== n.g) groups[i].members.push(n); }); });
    nodes = nodes.filter(function (n) { return n.g >= 0; });
    var by = Object.create(null);
    nodes.forEach(function (n) { by[n.key] = n; });
    var f = a.feed || {};
    var deadlines = nodes.map(function (n) { return n.until; }).concat(groups.map(function (g) { return g.verdict.valid_until; }))
      .filter(Number.isFinite).map(function (t) { return t * 1000; }).sort(function (a, b) { return a - b; });
    VER++;
    return {now: a.now, receipt: receipt, coverageUntil: (a.verdict || {}).valid_until, deadlines: deadlines, expired: 0, groups: groups, nodes: nodes, by: by, cmp: a.cmp || {}, counts: a.counts || {}, vcs: vcs, incomplete: arr(a.incomplete), notes: arr(a.notes),
            seconds: a.seconds || 60, feed: f, zone: a.zone || "UTC", title: a.title || "", enclave: a.enclave || "", group_by: a.group_by || "groups",
            vmRule: (a.cmp || {}).vm_rule || "", late: +a.late || 180, stale: +a.stale || 600};
  }
  async function load(first) {
    if (BUSY) return;
    BUSY = true;
    var timeout, requested = performance.now();
    try {
      var opts = {headers: {Accept: "application/json"}, credentials: "same-origin", cache: "no-store"};
      if (typeof AbortController === "function") {
        var controller = new AbortController();
        opts.signal = controller.signal;
        timeout = setTimeout(function () { controller.abort(); }, 60000);
      }
      var res = await fetch(url(), opts);
      if (res.status !== 200) {
        var error = new Error("HTTP " + res.status);
        if (res.json) {
          var body = await res.json();
          error.boot = body && body.boot; error.seq = body && body.seq;
        }
        throw error;
      }
      var a = await res.json();
      if (!a || !Array.isArray(a.nodes) || !Array.isArray(a.groups)) throw new Error("invalid fleet answer");
      if (!Number.isFinite(a.now)) throw new Error("missing fleet time");
      if (!accept(a)) return;
      F = adapt(a);
      F.receipt = requested;  // Time in transit counts against the server's deadlines.
      FAIL = ""; LOADED_AT = performance.now();
      Object.keys(SEL).forEach(function (k) { if (!F.by[k]) delete SEL[k]; });
      if (A && A.S.area === "monitor") { refresh(); firstOpen(); }
      else if (A && A.render) A.render();
    } catch (e) {
      if (e && Number.isSafeInteger(e.seq) && !accept(e)) return;
      FAIL = (e && e.message) || "not reachable";
      if (A && A.S.area === "monitor") refresh();
    } finally { clearTimeout(timeout); BUSY = false; }
  }

  // ---------------------------------------------------------------- the one rule: green only when current
  // freshness from the rows on screen: their own answer's read times (never the wall's other poll), the portal reachable,
  // this answer fetched; LATE / STALE as the answer says (SolarWinds: stretched with portal_wall_monitor_seconds)
  function age(asof) { return asof == null ? null : Math.max(0, (NOW() - asof * 1000) / 1000); }
  function fr(asof, state, late, stale) {
    if (FAIL || A.unreach() || state !== "ok" || asof == null) return "stale";
    var a = age(asof);
    return a >= stale ? "stale" : a >= late ? "late" : "ok";
  }
  function swFresh() {                 // the worse of this answer's own read and the wall's newer word on SolarWinds
    if (!F) return "stale";
    var a = fr(F.feed.asof, F.feed.state, F.late, F.stale);
    if (A.feed("sw:fleet")) return worse(a, A.feedFresh("sw:fleet"));
    return W() && W().feeds ? "stale" : a;
  }
  var RK = {ok: 0, late: 1, stale: 2};
  function worse(a, b) { return RK[b] > RK[a] ? b : a; }
  function vcFreshI(i) {                // the worse of this answer's read of that vCenter and the wall's newer word on it
    var v = F && F.vcs[i];
    if (!v) return "stale";
    var a = fr(v[1], v[2], (W() && W().late) || 180, (W() && W().stale) || 600);
    return A.feed("vc:" + v[0]) ? worse(a, A.feedFresh("vc:" + v[0])) : a;
  }
  // a vCenter-only VM: its vCenter's read, and SolarWinds' (that it is not in SolarWinds is only known from a current read)
  function fresh(n) { return n.vconly ? worse(vcFreshI(n.vci), swFresh()) : swFresh(); }
  function sig() {
    var k = VER + "|" + FAIL + "|" + (A.unreach() ? 1 : 0);
    if (F) {
      var now = NOW();
      while (F.expired < F.deadlines.length && F.deadlines[F.expired] <= now) F.expired++;
    }
    var x = [k, swFresh(), F && F.expired];
    (F ? F.vcs : []).forEach(function (v, i) { x.push(vcFreshI(i)); });
    return x.join(",");
  }
  // what a node is drawn as: up (green, only when its source is current), down, warn, off (hollow grey), unk (grey),
  // lastdown / lastwarn (its source not current: grey, "last known")
  function inventoriesCurrent() {
    return F.vcs.every(function (v, i) { return vcFreshI(i) === "ok"; });
  }
  function shown(n) {
    if (n.vconly) return "info";
    if (n.c === "p" && !inventoriesCurrent()) return "unk";
    if (n.verdict !== "unk" && swFresh() === "ok" && current(n.until)) return n.verdict;
    if (n.c === "d") return "lastdown";
    if (n.c === "w") return "lastwarn";
    return "unk";
  }
  var RANK = {down: 0, warn: 1, lastdown: 2, lastwarn: 3, unk: 4, off: 5, up: 6};
  function statusWord(n, s) {
    if (s === "off") return n.c === "m" ? "UNMANAGED" : "POWERED OFF";
    return {info: "INFO", up: "UP", unk: "UNKNOWN", down: "DOWN", warn: "WARN", lastdown: "LAST KNOWN DOWN", lastwarn: "LAST KNOWN WARN"}[s];
  }
  function sevOf(s) { return s === "down" ? "crit" : s === "warn" ? "warn" : s === "up" ? "ok" : s === "off" ? "off" : "stale"; }
  // a group's state from every member (a node in two groups counts in both), kept until the data or a source's freshness changes
  function gState(g) {
    if (g.kind === "v") return {st: "info", label: "information only", up: 0, down: 0, warn: 0, n: coverageCurrent() ? g.verdict.total : "–"};
    var v = g.verdict, usable = swFresh() === "ok" && current(v.valid_until) && (!v.powered_off || inventoriesCurrent());
    return {st: usable ? v.state : "stale", label: usable ? v.label : "not current",
            up: usable ? v.up : 0, down: usable ? v.down : 0, warn: usable ? v.warn : 0, n: v.total || 0};
  }
  function svcOf(n) { var g = F.groups[n.g]; return g ? (g.kind === "n" ? "no group" : g.name) : ""; }
  function hm(ms) { return A.hm(ms); }
  function phrase(n) {
    var s = shown(n), d = n.since ? A.dur(NOW() - n.since) : "";
    var since = n.since ? " (since " + hm(n.since) + (n.exact ? "" : " or earlier") + ")" : " (since not known yet)";
    if (s === "info") return "vCenter inventory only";
    if (s === "unk") return "unknown - no current data";
    if (s === "lastdown" || s === "lastwarn") return statusWord(n, s).toLowerCase() + (n.vconly ? "" : " · " + pollText(n));
    if (s === "down") return "down" + (d ? (n.exact ? " " : " at least ") + d : "") + since;
    if (s === "warn") return (n.why || "warning") + (d ? " (" + d + (n.exact ? "" : "+") + ", since " + hm(n.since) + (n.exact ? "" : " or earlier") + ")" : "");
    if (s === "up") return "up" + (n.rt != null ? " · " + n.rt + " ms" : "");
    return n.why || ST_WORD[n.c].toLowerCase();
  }
  function agoS(asof) { return asof == null ? "never read" : A.ago(age(asof)); }
  function staleText() {
    if (FAIL) return "the Ops monitor answer could not be read (" + FAIL + ") - the last one shown";
    if (A.unreach()) return "the portal is not answering - the last answer shown";
    var f = F.feed, fv = swFresh();
    if (fv === "ok") return "";
    if (!f.asof) return "SolarWinds not read yet" + (f.why ? ": " + f.why : "");
    return "SolarWinds last read " + agoS(f.asof) + (f.state !== "ok" && f.why ? " - " + f.why : "") + (fv === "late" ? " (late)" : "");
  }
  function srcOf(n) { return n.vconly ? {name: "vCenter " + n.vc, asof: (F.vcs[n.vci] || [])[1]} : {name: "SolarWinds", asof: F.feed.asof}; }
  function pollText(n) { return n.polled == null ? "SolarWinds last poll unknown" : "SolarWinds last polled " + hm(n.polled * 1000); }
  function vmText(n) {
    var freshness = vcFreshI(n.vci);
    return n.vm && (n.vm + ", as of " + (n.vcAt == null ? "unknown" : hm(n.vcAt * 1000)) +
      (freshness === "ok" ? "" : freshness === "late" ? " (LATE)" : " (NOT CURRENT)"));
  }
  function share(n) {
    var s = shown(n);
    var word = statusWord(n, s);
    if (!n.vconly && (s === "lastdown" || s === "lastwarn")) word = (s === "lastdown" ? "DOWN" : "WARN") + " - last known, " + pollText(n);
    var sinceLocal = n.since ? ((A && A.when ? A.when(n.since) : hm(n.since)) + (n.exact ? "" : " or earlier"))
      : (s === "up" || s === "off" || s === "info" ? "\u2014" : "not known");
    var durText = n.since ? (A.dur(Math.max(0, NOW() - n.since)) + (n.exact ? "" : "+")) : "\u2014";
    var serverStr = (n.ip && n.host) ? (n.ip + " / " + n.host) : (n.ip || n.host || "no IP");
    return {st: word, sev: sevOf(s), name: n.name, svc: svcOf(n), ip: n.ip, host: n.host || "", server: serverStr,
            since: n.since || 0, exact: !!n.exact, downSince: sinceLocal, downFor: durText,
            downSec: n.since ? Math.max(0, Math.round((NOW() - n.since) / 1000)) : null,
            phrase: phrase(n), loss: n.loss, vc: vmText(n),
            said: sevOf(s) === "stale" ? phrase(n) : n.why, up: s === "up" || s === "off"};
  }
  function ctx(L, kind) {
    var w = W(), z = F.zone || "UTC", day = "", bulk = kind === "bulk", one = L.length === 1 && !bulk ? L[0] : null;
    try { day = new Intl.DateTimeFormat("en-GB", {timeZone: z, day: "2-digit", month: "short"}).format(new Date(NOW())); } catch (e) { day = ""; }
    var base = location.origin + A.src.replace(/api\/wall$/, "wall") + "?area=monitor";
    var src = one ? srcOf(one) : null, vco = L.some(function (n) { return n.vconly; }), sw = L.some(function (n) { return !n.vconly; });
    var notCur = L.filter(function (n) { var s = shown(n); return fresh(n) !== "ok" || s === "lastdown" || s === "lastwarn" || s === "unk"; });
    var stale = !notCur.length ? "" : notCur.some(function (n) { return !n.vconly; }) && swFresh() !== "ok" ? staleText() :
      notCur.some(function (n) { return n.vconly && fresh(n) !== "ok"; }) ? "vCenter " + notCur.filter(function (n) { return n.vconly; })[0].vc + " not current - the last known state" :
      notCur.some(function (n) { return n.flags.indexOf("o") >= 0; }) ? "SolarWinds itself has not polled some of these lately - their state is not current" :
      F.incomplete.length ? "the read is not whole: " + F.incomplete[0] :
      "a source of these is not current - the last known state";
    return {site: w.enclave || "", stamp: hm(NOW()), day: day, zone: A.zoneName(), tz: z, bulk: bulk,
            src: one ? src.name + (one.vconly ? "" : " every " + F.seconds + " s") : (sw ? "SolarWinds every " + F.seconds + " s" : "") + (sw && vco ? " · " : "") + (vco ? "vCenter (VMs not in SolarWinds)" : ""),
            asof: src && src.asof ? hm(src.asof * 1000) : hm(NOW()), link: one ? base + "&node=" + encoded(one.key) : base, stale: stale};
  }

  // ---------------------------------------------------------------- the board
  function build() {
    B = h("div", "wx wx-fleet", null, {"aria-label": "Ops monitor"});
    P.title = h("b", "", ["Ops monitor"]);
    P.small = h("small", "", [""]);
    P.site = h("span", "fl-site", [""]);
    P.pills = h("div", "fl-pills", null, {"aria-live": "polite"});
    P.src = h("div", "fl-src");
    P.clock = h("span", "wx-clock", [h("b", "", [""]), h("small", "", [""])]);
    P.logo = h("span", "fl-logo");
    B.appendChild(h("header", "fl-hd", [P.logo, h("div", "fl-ttl", [P.small, P.title]), P.site, P.pills, P.src, P.clock]));
    P.ban = h("div", "fl-ban", null, {role: "status"});
    B.appendChild(P.ban);
    P.svc = h("div", "fl-svc");
    P.cv = h("canvas", "fl-cv", null, {role: "img", "aria-label": "Every node as a square, by service group"});
    P.tip = h("div", "fl-tip", null, {role: "tooltip"});
    P.gw = h("div", "fl-gw", [P.cv, P.tip]);
    P.gcue = h("span", "cue fl-click", ["each square = one node · click it for its card · Ctrl+click to select several · click a group name for the Spotlight"]);
    var left = h("section", "fl-panel fl-left", [
      h("div", "ph", [h("h3", "", ["Services"]), h("span", "cue", [h("span", "", [""], {id: "flSvcCue"}), h("span", "fl-click", [" · click a service"])])]), P.svc,
      h("div", "ph", [h("h3", "", ["Every node, by service group"]), P.gcue]), P.gw]);
    P.ncount = h("span", "cue", [""]);
    P.selall = h("button", "btn", ["Select all down"], {type: "button", title: "Tick every node that is down now"});
    P.shsel = h("button", "btn pri", ["Share selected to Teams"], {type: "button", title: "The ticked nodes (none ticked: every node down) as one message and one picture"});
    P.selc = h("span", "selc", ["0 selected"], {"aria-live": "polite"});
    P.clr = h("button", "btn lnk", ["Clear"], {type: "button", title: "Untick every node", hidden: true});
    P.selcue = h("span", "srv-sel-cue fl-click", ["Tick or Space to select · Shift+click range"]);
    P.copyBar = copyHelp.createFeedbackBar ? copyHelp.createFeedbackBar() : h("div", "copy-bar", null, {hidden: true});
    P.copyAll = copyHelp.createSplitControl ? copyHelp.createSplitControl({
      cls: "pri fl-copy-all", scope: "all", baseLabel: "Copy all shown", withCount: true, count: 0,
      title: "Copy every node currently shown in Not up (text + picture)",
      ariaLabel: "Copy all shown nodes", toggleLabel: "Copy all shown or selected options",
      menuTitle: "Copy all shown", menuTag: "0 rows",
      subtitles: {both: "Text summary + PNG", text: "Plain + HTML summary", picture: "PNG summary card"},
      isEmpty: function () { return !(selected().length || notUpList().length); },
      onCopy: function (mode, btn, finish) {
        var sel = selected(), L = sel.length ? sel : notUpList();
        if (!L.length) { finish(); return Promise.resolve(); }
        return copyNodesList(L, mode, btn, P.copyBar, finish);
      }
    }) : null;
    P.evs = h("div", "fl-evs");
    P.more = h("button", "fl-more", [""], {type: "button", hidden: true, title: "Every node not up, with checkboxes"});
    var list = h("section", "fl-panel fl-list", [h("div", "ph", [h("h3", "", ["Not up · worst first"]), P.ncount]),
      h("div", "evh", [P.selall, P.shsel, P.copyAll, P.selc, P.clr, P.selcue]), P.evs, P.more, P.copyBar]);
    P.cmp = h("div", "fl-cmp");
    P.cmpcue = h("span", "cue", [""]);
    var cmp = h("section", "fl-panel fl-cov", [h("div", "ph", [h("h3", "", ["SolarWinds vs vCenter"]), P.cmpcue]), P.cmp]);
    P.sph = h("h3", "", ["Spotlight"]);
    P.spcue = h("span", "cue", [""]);
    P.dw = h("i");
    P.prev = h("button", "stp", ["◀"], {type: "button", title: "The group before (←)"});
    P.next = h("button", "stp", ["▶"], {type: "button", title: "The next group (→)"});
    P.chips = h("div", "fl-chips");
    var spot = h("section", "fl-panel fl-spot", [h("div", "ph", [P.sph, P.spcue, h("span", "stps", [P.prev, P.next])]), h("div", "dwell", [P.dw], {"aria-hidden": "true"}), P.chips]);
    B.appendChild(h("div", "fl-main", [left, h("div", "fl-right", [list, cmp, spot])]));
    P.ft = h("footer", "fl-ft");
    B.appendChild(P.ft);
    P.ov = h("div", "fl-ov", null, {role: "presentation"});
    B.appendChild(P.ov);
    P.probe = h("span", "fl-probe", null, {"aria-hidden": "true"});
    B.appendChild(P.probe);
    wire();
  }
  function render(api, stage) {
    A = api;
    if (!B) { build(); load(true); }
    else if (performance.now() - LOADED_AT > 30000) load(false);          // back on the area after a while: read it again at once
    stage.appendChild(B);
    refresh();
  }
  function fitted(k) { LAY.k = k || 1; if (A && A.S.area === "monitor") soon(drawGrid); }

  function refresh() {
    LASTSIG = A ? sig() : "";
    if (!B || !A || !W()) return;
    var w = W();
    clear(P.logo);
    if (A.logo) P.logo.appendChild(h("span", "wx-logo", [h("img", "", null, {src: A.logo, alt: w.title || "Logo"})]));
    else P.logo.appendChild(h("span", "wx-logo badge", [h("b", "", [((w.title || "O").charAt(0) || "O").toUpperCase()])], {"aria-hidden": "true"}));
    P.small.textContent = (w.title || "Ops center") + " · " + A.styleName();
    P.site.textContent = w.enclave || "";
    P.site.hidden = !w.enclave;
    P.clock.firstChild.textContent = A.hms(NOW());
    P.clock.lastChild.textContent = A.zoneName();
    srcs();
    if (!F) {
      clear(P.pills); clear(P.svc); clear(P.evs); clear(P.cmp); clear(P.chips); clear(P.ft);
      P.ban.className = "fl-ban on"; P.ban.textContent = FAIL ? "The Ops monitor area could not be read: " + FAIL + ". It tries again every 30 s." : "Reading SolarWinds…";
      return;
    }
    pills(); banner(); services(); notUp(); compare(); spotlight(); footer();
    if (P.tip) P.tip.classList.remove("on");
    if (OV && OV.kind === "list") openList(OV.title, OV.filter, OV.note, OV.limit);
    else if (OV) {
      var current = OV.keys.map(function (k) { return F.by[k]; }).filter(Boolean);
      if (current.length) openCard(current, OV.kind, true);
      else closeOv();
    }
    soon(drawGrid);
  }
  function srcs() {
    clear(P.src);
    var mk = function (fk, name, ref, why) {
      var e = h("span", "fd", [h("i"), h("span", "", [name + " "]), h("b", "age", [""])],
                {"data-d": "feed:" + fk, tabindex: "0", role: "button", title: name + (why ? " - " + why : "") + " · read by the portal itself · click for the source's card"});
      e.setAttribute("data-fl-age", ref);
      return e;
    };
    if (!F) return;
    P.src.appendChild(mk("sw:fleet", "SolarWinds", "sw", F.feed.why));
    F.vcs.forEach(function (v, i) { P.src.appendChild(mk("vc:" + v[0], "vCenter " + String(v[0]).split(".")[0], "vc" + i, "")); });
    ages();
  }
  // each source's age, as the wall's rule says it: "35 s", LATE, NOT CURRENT - from the Ops monitor answer's own read times (ticks every second)
  function ages() {
    if (!F) return;
    P.src.querySelectorAll("[data-fl-age]").forEach(function (e) {
      var r = e.getAttribute("data-fl-age"), i = r === "sw" ? -1 : +r.slice(2), asof = i < 0 ? F.feed.asof : (F.vcs[i] || [])[1];
      var fv = i < 0 ? swFresh() : vcFreshI(i), ag = asof != null ? A.ago(age(asof)).replace(" ago", "") : "never read";
      e.className = "fd " + fv;
      e.lastChild.textContent = fv === "ok" ? ag : fv === "late" ? "LATE " + ag : "NOT CURRENT" + (asof != null ? " · " + ag : "");
    });
  }
  function counts() {
    var c = {down: 0, warn: 0, up: 0, off: 0, unk: 0, lastdown: 0, lastwarn: 0}, seen = {};
    F.nodes.forEach(function (n) { if (!n.vconly && !seen[n.key]) { seen[n.key] = 1; c[shown(n)]++; } });
    return c;
  }
  function pills() {
    var c = counts(), last = c.lastdown + c.lastwarn;
    clear(P.pills);
    var p = function (cls, st, txt, title) { return h("span", "pill " + cls, [A.word(st, txt)], {title: title}); };
    add(P.pills, [
      p("c" + (c.down ? " on" : ""), c.down ? "crit" : "off", c.down + " down", "nodes down now (SolarWinds)"),
      p("w" + (c.warn ? " on" : ""), c.warn ? "warn" : "off", c.warn + " warning", "packet loss or slow answers"),
      p("o", c.up ? "ok" : "off", c.up.toLocaleString("en") + " up", "up, from a current read"),
      p("g", "off", c.off + " off", "powered off or unmanaged"),
      c.unk || last ? p("s", "stale", (c.unk + last).toLocaleString("en") + " no current data", "grey: no current read - never green" + (last ? " · " + last + " of them last known not up (in the Not up list)" : "")) : null]);
  }
  function add(e, kids) { return A.add(e, kids); }
  function banner() {
    var t = staleText(), inc = F.incomplete;
    P.ban.className = "fl-ban" + (t || inc.length ? " on" : "");
    clear(P.ban);
    if (t) add(P.ban, [A.word("stale", "NOT CURRENT"), h("span", "", [t + " - grey = no current data; nothing here is green until SolarWinds is read again."])]);
    else if (inc.length) add(P.ban, [A.word("stale", "NOT WHOLE"), h("span", "", [inc.slice(0, 2).join(" · ") + " - the area is not shown OK until the read is whole."])]);
    else if (F.notes.length) { P.ban.className = "fl-ban on note"; add(P.ban, [A.word("off", "NOTE"), h("span", "", [F.notes.slice(0, 2).join(" · ")])]); }
  }
  // the services strip: SolarWinds' groups (or the custom property's values)
  function services() {
    clear(P.svc);
    var gs = F.groups.filter(function (g) { return g.kind === "g" || g.kind === "c"; });
    var cue = document.getElementById("flSvcCue");
    if (cue) cue.textContent = (F.group_by === "groups" ? "SolarWinds groups" : "by the custom property " + F.group_by.slice(7)) + " · current members";
    if (!gs.length && F.feed.asof == null) { P.svc.appendChild(h("p", "wx-empty", ["SolarWinds not read yet - no service to show."])); return; }
    if (!gs.length) { P.svc.appendChild(h("p", "wx-empty", [F.group_by === "groups" ? "No SolarWinds group with nodes in it - every node is under Workloads (no group)." : "No node has a value in " + F.group_by.slice(7) + "."])); return; }
    var max = 16, list = gs;
    if (gs.length > max) {
      var rk = {crit: 0, warn: 1, stale: 2, ok: 3, off: 4};
      list = gs.slice().sort(function (a, b) { return rk[gState(a).st] - rk[gState(b).st] || a.i - b.i; }).slice(0, max - 1).sort(function (a, b) { return a.i - b.i; });
    }
    P.svc.classList.toggle("two", list.length > 8);
    list.forEach(function (g) {
      var c = gState(g), word = c.down ? c.down + " down" : c.warn ? c.warn + " warn" : c.st === "ok" && c.up === c.n ? "all up" : c.label;
      P.svc.appendChild(h("div", "sv " + c.st + (SPOT.g === g.i ? " on" : ""), [h("b", "", [g.name]),
        h("span", "ln", [A.word(c.st === "ok" ? "ok" : c.st, word), h("small", "", [c.st === "stale" ? c.n + " members" : c.up + "/" + c.n + " up"])])],
        {"data-g": String(g.i), tabindex: "0", role: "button", title: g.name + ": put it in the Spotlight"}));
    });
    if (gs.length > list.length) P.svc.appendChild(h("div", "sv more", [h("b", "", ["+" + (gs.length - list.length) + " more groups"]), h("span", "", ["all in the grid below"])]));
  }
  function notUpList() {
    return F.nodes.filter(function (n) { var s = shown(n); return s === "down" || s === "warn" || s === "lastdown" || s === "lastwarn"; })
      .sort(function (a, b) { return RANK[shown(a)] - RANK[shown(b)] || (a.since || 9e15) - (b.since || 9e15) || (a.name < b.name ? -1 : 1); });
  }
  function nodeToServer(n) {
    var s = shown(n), st = statusWord(n, s), sev = s === "down" ? "critical" : s === "warn" ? "warning" : (s === "lastdown" || s === "lastwarn" || s === "unk") ? "unknown" : s === "up" ? "ok" : "info";
    var checkHint = n.vconly ? "vSphere Client of " + n.vc + " > VM " + n.name + " > Summary"
      : "SolarWinds > Node Details of " + n.name + (n.vm ? "; vSphere Client > VM > Summary" : "");
    var findings = [];
    if (sev !== "ok") {
      findings.push({sev: sev, sevLabel: st, title: [svcOf(n), phrase(n)].filter(Boolean).join(" - ") || st,
        evidence: n.said || "", whatToDo: "Check " + checkHint});
      if (n.vm) findings.push({sev: "info", sevLabel: "Info", title: "vCenter: " + vmText(n), evidence: "", whatToDo: "Verify VM state in vSphere Client."});
    }
    return {host: n.name, ip: n.ip || "", sev: sev, statusLabel: st, worst: phrase(n) || st, summary: [svcOf(n), phrase(n)].filter(Boolean).join(" - "),
      findings: findings, totalFindings: findings.length, cappedCount: 0};
  }
  function fleetCopyCtx(L) {
    var c = ctx(L);
    return {site: c.site || "", report: "Ops monitor", dateTime: [c.day, c.stamp, c.zone].filter(Boolean).join(" "), url: c.link || (typeof location !== "undefined" ? location.href : "")};
  }
  function copyOneNode(n, mode, btn, bar, finish) {
    var srv = nodeToServer(n), c = fleetCopyCtx([n]);
    var fmt = copyHelp.formatOneServer(srv, c);
    var pic = mode === "text" ? {dataUrl: ""} : copyHelp.drawOneServerCard(srv, c);
    return copyHelp.copyMode(mode, {
      plain: fmt.plain, html: fmt.html, dataUrl: pic.dataUrl, width: 700, alt: srv.host + " status card", focusEl: btn,
      onState: function (st, lab, msg) { copyHelp.setFeedback(btn, bar, st, lab, msg); },
      onDone: finish
    });
  }
  function copyNodesList(L, mode, btn, bar, finish) {
    var servers = L.map(nodeToServer), c = fleetCopyCtx(L);
    var fmt = copyHelp.formatAllShown(servers, c);
    var pic = mode === "text" ? {dataUrl: ""} : copyHelp.drawAllShownCard(servers, c);
    return copyHelp.copyMode(mode, {
      plain: fmt.plain, html: fmt.html, dataUrl: pic.dataUrl, width: 700, alt: "Ops monitor summary card", focusEl: btn,
      onState: function (st, lab, msg) { copyHelp.setFeedback(btn, bar, st, lab, msg); },
      onDone: finish
    });
  }
  function row(n, cls, barEl) {
    var s = shown(n), ticked = !!SEL[n.key];
    var cb = h("input", "srv-sel-cb", null, {type: "checkbox", "data-k": n.key, "aria-label": "select " + n.name,
      title: "Select " + n.name + " (Space to toggle, Shift+click for range)", checked: ticked});
    var cp = copyHelp.createSplitControl ? copyHelp.createSplitControl({
      cls: "sm fl-row-copy", scope: "server", baseLabel: "Copy",
      title: "Copy " + n.name + " status and findings (text + picture)",
      ariaLabel: "Copy " + n.name, toggleLabel: "Copy options for " + n.name,
      menuTitle: String(n.name || "Node").split(".")[0], menuTag: "Server row",
      subtitles: {both: "Text + inline PNG", text: "Status + all findings", picture: "PNG server card"},
      onCopy: function (mode, btn, finish) { return copyOneNode(n, mode, btn, barEl || P.copyBar, finish); }
    }) : null;
    return h("div", "ev " + sevOf(s) + (ticked ? " srv-selected" : "") + (cls ? " " + cls : ""), [
      h("span", "s", [statusWord(n, s)]), h("span", "n", [cb, h("b", "", [n.name])]), h("span", "a", [n.since ? A.dur(NOW() - n.since) + (n.exact ? "" : "+") : ""]),
      cp,
      h("span", "d", [[svcOf(n), n.ip || "no IP", phrase(n)].filter(Boolean).join(" · ")])],
      {"data-k": n.key, tabindex: "0", role: "button", title: n.name + ": click for its card"});
  }
  function notUp() {
    var L = notUpList(), nsel = Object.keys(SEL).length;
    P.ncount.textContent = L.length ? L.length + " node" + (L.length === 1 ? "" : "s") : "";
    P.selc.textContent = nsel + " selected";
    P.selc.classList.toggle("has-sel", nsel > 0);
    P.clr.hidden = !nsel;
    if (P.copyAll && P.copyAll.setSelection) P.copyAll.setSelection(nsel, L.length, "node");
    else if (P.copyAll && P.copyAll.setCount) P.copyAll.setCount(nsel || L.length);
    clear(P.evs);
    if (!L.length) {
      var cn = counts();
      P.evs.appendChild(h("p", "wx-empty", [swFresh() !== "ok" || F.incomplete.length ? "No current, whole read - nothing can be said up or down here." :
        cn.unk ? "Nothing down or warning in the current read - " + cn.unk + " without current data (grey)." :
        !cn.up && cn.off ? "All monitored nodes are off." : !cn.up ? "No monitored nodes." : "Every monitored node is up, powered off or unmanaged."]));
      P.more.hidden = true;
      P.evs.__n = 0;
      return;
    }
    L.slice(0, 40).forEach(function (n) { P.evs.appendChild(row(n, "", P.copyBar)); });
    P.evs.__n = L.length;
    soon(fitList);
  }
  // as many rows as the panel shows (whole rows only); the rest behind "+N more" - measured again after every fit and draw
  function fitList() {
    var rows = P.evs.querySelectorAll(".ev"), box = P.evs.getBoundingClientRect(), shownN = 0;
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i].getBoundingClientRect(), vis = r.bottom <= box.bottom + 0.5;
      rows[i].classList.toggle("cut", !vis);
      if (vis) shownN++;
    }
    var n = P.evs.__n || 0;
    P.more.hidden = n <= shownN;
    P.more.textContent = "+" + (n - shownN) + " more · open the full list";
  }
  function coverageCurrent() {
    var wallFleet = W() && W().fleet;
    return F.cmp.vc_only != null && !(wallFleet && wallFleet.complete === false) && swFresh() === "ok" && current(F.coverageUntil);
  }
  function compare() {
    var c = F.cmp;
    clear(P.cmp);
    P.cmpcue.textContent = "compared at each read · click a line for the list";
    if (!F.vcs.length) { P.cmp.appendChild(h("p", "wx-empty", ["No vCenter on the wall - nothing compared (the VMware area needs the \"VMware vCenter\" credential)."])); return; }
    var line = function (n, cls, t, sub, ref) {
      var real = typeof n === "number" && n > 0;
      return h("div", "r" + (real ? " " + cls : " flat"), [h("b", "", [String(n)]), h("div", "", [t, h("small", "", [sub])])],
               ref && real ? {"data-cmp": ref, tabindex: "0", role: "button", title: "The list of these nodes"} : null);
    };
    var curVc = F.vcs.every(function (v, i) { return vcFreshI(i) === "ok"; });
    var vco = coverageCurrent() ? c.vc_only : null, nf = c.vc_read && curVc ? c.not_in_vc || 0 : null;
    add(P.cmp, [
      line(vco == null ? "–" : vco, "", "VMs in vCenter, not monitored in SolarWinds", vco == null ? "not counted: SolarWinds' read is not whole and current" : "grey inventory rows in the vCenter only group", "v"),
      line(nf == null ? "–" : nf, "", "SolarWinds VMs not found in vCenter", nf != null ? "identified by SolarWinds Vendor or MachineType (" + F.vmRule + ")" : "not counted: not every vCenter's read is current", "n"),
      c.ambiguous ? line(c.ambiguous, "", "Ambiguous inventory matches", "more than one node or VM fits the available names and IPs", "a") : null,
      curVc ? null : h("p", "wx-empty", ["A vCenter is not current: its last read is dated on matched cards."])]);
  }
  // the Spotlight: one group at a time, problems first; steps every DWELL s, or by ◀ ▶ (a click pauses it for 60 s)
  function spotOrder() {
    var rk = {crit: 0, warn: 1, stale: 2, ok: 3, off: 4, info: 5};
    return F.groups.filter(function (g) { return (g.members || g.nodes).length && (g.kind !== "v" || coverageCurrent()); }).map(function (g) { return {g: g, s: gState(g)}; })
      .sort(function (a, b) { return rk[a.s.st] - rk[b.s.st] || b.s.down - a.s.down || b.s.warn - a.s.warn || a.g.i - b.g.i; }).map(function (x) { return x.g; });
  }
  function spotlight() {
    var order = spotOrder();
    if (!order.length) { P.sph.textContent = "Spotlight"; P.spcue.textContent = ""; clear(P.chips); return; }
    var idx = order.map(function (g) { return g.i; }).indexOf(SPOT.g);
    if (idx < 0) { idx = 0; SPOT.g = order[0].i; }
    var g = order[idx];
    P.sph.textContent = "Spotlight · " + g.name;
    spotCue(idx, order.length);
    clear(P.chips);
    var ns = (g.members || g.nodes).slice().sort(function (a, b) { return RANK[shown(a)] - RANK[shown(b)] || (a.name < b.name ? -1 : 1); });
    var max = 15, show = ns.length > max ? ns.slice(0, max - 1) : ns;
    show.forEach(function (n) {
      var s = shown(n);
      P.chips.appendChild(h("div", "chip " + s, [h("i"), h("b", "", [n.name]),
        h("span", "", [s === "lastdown" || s === "lastwarn" ? phrase(n) : s === "down" ? "down" : s === "off" ? (n.c === "m" ? "unmanaged" : phrase(n)) : n.c === "w" ? (n.loss ? n.loss + "% loss" : n.rt != null ? n.rt + " ms" : "warn") : s === "info" ? "info" : s === "unk" ? "?" : n.rt != null ? n.rt + " ms" : "up"])],
        {"data-k": n.key, tabindex: "0", role: "button", title: n.name + ": click for its card"}));
    });
    var rest = ns.slice(show.length), allUp = rest.every(function (n) { return shown(n) === "up"; });
    if (rest.length) P.chips.appendChild(h("div", "chip rest", [h("b", "", ["+" + rest.length + " more" + (allUp ? " up" : "")])]));
  }
  function spotCue(idx, n) {
    var moving = !frozen() && !OV && SPOT.pausedUntil <= Date.now() && n > 1;
    P.spcue.textContent = "group " + (idx + 1) + " of " + n + " · problems first · " + (OV ? "held while a card is open" : !moving ? (n > 1 ? (SPOT.pausedUntil > Date.now() ? "paused 60 s after a click" : "stepping off") : "one group") : "next in " + SPOT.left + " s") + " · ◀ ▶ to step";
    P.dw.style.width = (moving ? Math.max(0, Math.min(100, (DWELL - SPOT.left) / DWELL * 100)) : 0) + "%";
  }
  function step(d, byHand) {
    var order = spotOrder();
    if (!order.length) return;
    var idx = order.map(function (g) { return g.i; }).indexOf(SPOT.g);
    SPOT.g = order[((idx < 0 ? 0 : idx) + d + order.length) % order.length].i;
    SPOT.left = DWELL;
    if (byHand) SPOT.pausedUntil = Date.now() + 60000;
    services(); spotlight(); drawGrid();
  }
  function footer() {
    clear(P.ft);
    var k = function (cls, t) { return h("span", "k", [h("i", cls), t]); };
    add(P.ft, [k("up", "up"), k("warn", "warning (loss or slow)"), k("down", "down"), k("off", "powered off or unmanaged"), k("unk", "no current data"),
      h("span", "rule", ["SolarWinds every " + F.seconds + " s · current vCenter power can explain a down node"]),
      h("span", "rot rotcue", [A.ROT.on ? "Rotating " + (A.ROT.mode === "styles" ? "styles" : "areas") + " every " + A.ROT.every + " s" : "Rotation off"])]);
  }

  // ---------------------------------------------------------------- the grid (canvas)
  var COLC = {k: "", v: null};
  function colours() {
    var de = document.documentElement, key = de.className + "|" + (de.getAttribute("data-theme") || "") + "|" + (window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)").matches : "");
    if (COLC.k === key && COLC.v) return COLC.v;
    var cs = {}, pr = P.probe;
    ["--fl-up", "--fl-warn", "--fl-crit", "--fl-grey", "--fl-accent", "--wx-ink", "--wx-mute", "--fl-label-ink", "--wx-panel", "--fl-notch"].forEach(function (t) {
      pr.style.color = ""; pr.style.color = "var(" + t + ")"; cs[t] = getComputedStyle(pr).color;
    });
    var f = getComputedStyle(B);
    cs.sans = f.getPropertyValue("--wx-sans").trim() || "sans-serif"; cs.mono = f.getPropertyValue("--wx-mono").trim() || "monospace";
    COLC = {k: key, v: cs};
    return cs;
  }
  // the square size that fits every group in the room: the biggest of 18 to 3 px, in 1 to 3 columns (the shortest column first)
  function layout(gs, Wd, Hd) {
    var best = null;
    [18, 16, 14, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3].some(function (S) {
      [1, 2, 3].some(function (C) {
        var gap = Math.max(1, Math.round(S * 0.24)), colW = (Wd - (C - 1) * 18) / C, per = Math.max(1, Math.floor((colW + gap) / (S + gap)));
        var hs = [], cols = [], i;
        for (i = 0; i < C; i++) { hs.push(0); cols.push([]); }
        gs.forEach(function (g) {
          var rows = Math.ceil(g.nodes.length / per), hh = 22 + rows * (S + gap) + 14, j = hs.indexOf(Math.min.apply(null, hs));
          cols[j].push({g: g, y: hs[j], rows: rows}); hs[j] += hh;
        });
        if (Math.max.apply(null, hs) <= Hd) { best = {S: S, gap: gap, C: C, colW: colW, per: per, cols: cols}; return true; }
        return false;
      });
      return !!best;
    });
    if (!best) {
      var S = 3, gap = 1, C = 3, colW = (Wd - 36) / 3, per = Math.floor((colW + gap) / (S + gap)), hs = [0, 0, 0], cols = [[], [], []];
      gs.forEach(function (g) { var rows = Math.ceil(g.nodes.length / per), j = hs.indexOf(Math.min.apply(null, hs)); cols[j].push({g: g, y: hs[j], rows: rows}); hs[j] += 22 + rows * (S + gap) + 14; });
      best = {S: S, gap: gap, C: C, colW: colW, per: per, cols: cols, cut: true};
    }
    return best;
  }
  function drawGrid() {
    if (!B || !F || !P.gw.isConnected) return;
    var Wd = Math.max(200, P.gw.clientWidth), Hd = Math.max(120, P.gw.clientHeight), dpr = (window.devicePixelRatio || 1) * (LAY.k || 1);
    var cv = P.cv, x = !P.gridList && cv.getContext("2d"), pw = Math.round(Wd * dpr), ph = Math.round(Hd * dpr);
    if (!x) {
      if (!P.gridList) {
        clear(P.gw);
        P.gridList = h("div", "fl-evs all");
        P.gw.appendChild(P.gridList);
        P.gcue.textContent = "click a row for its card · tick to select";
      }
      clear(P.gridList);
      F.nodes.forEach(function (n) { P.gridList.appendChild(row(n)); });
      return;
    }
    if (cv.width !== pw || cv.height !== ph) {                    // the backing store only when the size changed
      cv.style.width = Wd + "px"; cv.style.height = Hd + "px";
      cv.width = pw; cv.height = ph;
    }
    x.setTransform(dpr, 0, 0, dpr, 0, 0);
    x.clearRect(0, 0, Wd, Hd);
    var C = colours(), gs = F.groups.filter(function (g) { return g.nodes.length; }), L = layout(gs, Wd, Hd), S = L.S, gap = L.gap;
    LAY.blocks = []; LAY.S = S;
    var labels = [];
    L.cols.forEach(function (col, ci) {
      var x0 = ci * (L.colW + 18);
      col.forEach(function (b) {
        var g = b.g, c = gState(g), y = b.y;
        x.textBaseline = "alphabetic"; x.textAlign = "left";
        x.font = "600 14px " + C.sans; x.fillStyle = C["--wx-ink"];
        var nm = g.kind === "n" ? "Workloads" : g.name, tw = Math.min(x.measureText(nm).width, L.colW * 0.5);
        x.fillText(fit(x, nm, L.colW * 0.5), x0, y + 14);
        x.font = "500 12px " + C.mono; x.fillStyle = C["--wx-mute"];
        x.fillText(fit(x, KIND[g.kind] || "", L.colW * 0.5 - tw - 30), x0 + tw + 10, y + 14);
        x.fillStyle = c.down ? C["--fl-crit"] : c.warn ? C["--fl-warn"] : C["--wx-mute"]; x.textAlign = "right";
        x.fillText(fit(x, c.n.toLocaleString("en") + " nodes · " + c.label, L.colW * 0.48), x0 + L.colW, y + 14);
        x.textAlign = "left";
        var top = y + 22;
        LAY.blocks.push({name: g.name, kind: g.kind, keys: g.nodes.map(function (n) { return n.key; }), x: x0, y: y, top: top, w: L.colW, per: L.per, rows: b.rows});
        g.nodes.forEach(function (n, i) {
          var cx = x0 + (i % L.per) * (S + gap), cy = top + Math.floor(i / L.per) * (S + gap), s = shown(n);
          if (s === "off") { x.strokeStyle = C["--fl-grey"]; x.lineWidth = Math.max(1, S / 8); x.strokeRect(cx + .75, cy + .75, S - 1.5, S - 1.5); }
          else {
            x.fillStyle = s === "down" ? C["--fl-crit"] : s === "warn" ? C["--fl-warn"] : s === "up" ? C["--fl-up"] : C["--fl-grey"];
            if (s === "unk") x.globalAlpha = .45;
            x.fillRect(cx, cy, S, S);
            x.globalAlpha = 1;
            if (s === "warn" && S >= 7) { x.fillStyle = C["--fl-notch"]; x.fillRect(cx + S * .3, cy + S * .3, S * .4, S * .4); }       // a shape, not a colour only
            if ((s === "lastdown" || s === "lastwarn") && S >= 5) { x.strokeStyle = s === "lastdown" ? C["--fl-crit"] : C["--fl-warn"]; x.setLineDash([2, 2]); x.lineWidth = 1.5; x.strokeRect(cx + .75, cy + .75, S - 1.5, S - 1.5); x.setLineDash([]); }
          }
          if (s === "down") { x.strokeStyle = C["--fl-crit"]; x.lineWidth = 2; x.beginPath(); x.arc(cx + S / 2, cy + S / 2, Math.max(S, 7), 0, 7); x.stroke(); labels.push({n: n, x: cx, y: cy}); }
          if (SEL[n.key]) { x.strokeStyle = C["--fl-accent"]; x.lineWidth = 3; x.strokeRect(cx - 3, cy - 3, S + 6, S + 6); }
        });
        if (SPOT.g === g.i) { x.strokeStyle = C["--fl-accent"]; x.lineWidth = 2; x.strokeRect(x0 - 5, y - 4, L.colW + 10, 22 + b.rows * (S + gap) + 6); }
      });
    });
    // the names of the nodes down: the longest down first, at most 14
    labels.sort(function (a, b) { return (a.n.since || 9e15) - (b.n.since || 9e15); }).slice(0, 14).forEach(function (l) {
      x.font = "700 13px " + C.mono;
      var t = fit(x, l.n.name, 220), w = x.measureText(t).width + 12, lx = Math.min(l.x + S + Math.max(S, 7) / 2 + 6, Wd - w - 2), ly = Math.max(0, Math.min(Hd - 22, l.y + S / 2 - 11));
      x.fillStyle = C["--fl-crit"]; x.beginPath(); if (x.roundRect) x.roundRect(lx, ly, w, 22, 6); else x.rect(lx, ly, w, 22); x.fill();
      x.fillStyle = C["--fl-label-ink"]; x.fillText(t, lx + 6, ly + 15);
    });
    fitList();
    if (L.cut) { x.font = "600 13px " + C.mono; x.fillStyle = C["--wx-mute"]; x.fillText("more nodes than this screen shows at 3 px - the list and the Spotlight have them all", 0, Hd - 4); }
  }
  function fit(x, t, w) {
    t = String(t || "");
    if (w <= 8) return "";
    if (x.measureText(t).width <= w) return t;
    while (t.length > 1 && x.measureText(t + "…").width > w) t = t.slice(0, -1);
    return t + "…";
  }
  function hit(e) {
    var r = P.cv.getBoundingClientRect(), k = r.width / Math.max(1, P.cv.clientWidth || 1), mx = (e.clientX - r.left) / k, my = (e.clientY - r.top) / k, S = LAY.S;
    for (var i = 0; i < LAY.blocks.length; i++) {
      var b = LAY.blocks[i], gap = Math.max(1, Math.round(S * 0.24));
      if (mx < b.x - 4 || mx > b.x + b.w + 4) continue;
      if (my >= b.y - 4 && my < b.top) return {g: F.groups.find(function (g) { return g.name === b.name && g.kind === b.kind; })};
      if (my >= b.top && my < b.top + b.rows * (S + gap)) {
        var col = Math.floor((mx - b.x) / (S + gap)), rw = Math.floor((my - b.top) / (S + gap)), idx = rw * b.per + col;
        if (col >= 0 && col < b.per && idx < b.keys.length) return {n: F.by[b.keys[idx]], mx: mx, my: my};
      }
    }
    return {};
  }

  // ---------------------------------------------------------------- the card (one node) and Share (many)
  function kv(rows) {
    var t = h("div", "kv");
    rows.forEach(function (r) { if (r) { t.appendChild(h("span", "", [r[0]])); t.appendChild(h("b", r[2] || "", [r[1]])); } });
    return t;
  }
  function closeOv() {
    if (!OV) return false;
    OV = null;
    P.ov.classList.remove("on");
    clear(P.ov);
    spotlight();
    return true;
  }
  function cardDetails(n, s) {
    var g = F.groups[n.g] || {}, also = (n.also || []).map(function (i) { return (F.groups[i] || {}).name; }).filter(Boolean);
    var fr = fresh(n), sr = srcOf(n);
    return [
      ["Service", (g.kind === "n" ? "no group (Workloads)" : g.name || "-") + (also.length ? " · also in " + also.join(", ") : "")],
      ["IP", n.ip || "none known", "mono"],
      ["Status", statusWord(n, s).toLowerCase() + (n.why && (n.c !== "p" || s === "off") ? " · " + n.why : ""), sevOf(s)],
      n.vconly ? null : ["Since", n.since ? hm(n.since) + (n.exact ? "" : " or earlier") + " (" + A.dur(NOW() - n.since) + (n.exact ? "" : "+") + ")" : n.c === "u" ? "-" : "not known yet (seen at the first read)"],
      n.vconly ? null : ["Response / loss", (n.c === "d" ? "no reply" : n.rt != null ? n.rt + " ms" : "-") + " · " + (n.loss != null ? n.loss + "% loss" : "loss not read"), "mono"],
      n.vm ? ["vCenter says:", vmText(n)] : null,
      n.vconly ? null : ["SolarWinds says", (sevOf(s) !== "stale" && n.said || "-") + (n.model ? " · " + n.model : "") + (n.polled != null ? " · polled " + A.hms(n.polled * 1000) : "")],
      ["Source", sr.name + " · " + (sr.asof != null ? "as of " + A.hms(sr.asof * 1000) + " (" + agoS(sr.asof) + ")" : "never read") + (fr === "ok" ? "" : fr === "late" ? " · LATE" : " · NOT CURRENT")],
      ["Check", n.vconly ? "vSphere Client of " + n.vc + " › VM " + n.name + " › Summary" : "SolarWinds › Node Details of " + n.name + (n.vm ? "; vSphere Client › VM › Summary" : "")]];
  }
  function nodeOrder(a, b) {
    return RANK[shown(a)] - RANK[shown(b)] || (a.since || 9e15) - (b.since || 9e15) || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0);
  }
  function openCard(L, kind, refreshing) {
    L = L.filter(Boolean);
    if (!L.length) return;
    var one = L.length === 1 && kind !== "bulk";
    if (!one) L = L.slice().sort(nodeOrder);
    var n = L[0], s = shown(n);
    var keys = L.map(function (m) { return m.key; }), rows = L.map(share), context = ctx(L, kind);
    var details = one ? cardDetails(n, s) : null;
    // The share's creation time is stamped when it opens. Only changes to the selected content need a new preview.
    var signature = JSON.stringify([keys, rows, details, context.stale, context.site, context.src, context.zone, one ? context.asof : null]);
    if (refreshing && signature === OV.signature) return;
    var focusIndex = refreshing ? Array.prototype.indexOf.call(P.ov.querySelectorAll("button"), document.activeElement) : -1;
    OV = {kind: kind || "card", keys: keys, signature: signature};
    clear(P.ov);
    var x = h("button", "x", ["×"], {type: "button", title: "Close (Esc)", "aria-label": "Close"});
    x.addEventListener("click", closeOv);
    var head = one ? h("h2", "", [h("span", "tag " + sevOf(s), [statusWord(n, s)]), h("span", "nm", [n.name]), h("small", "", [svcOf(n)]), x]) :
      h("h2", "", [h("span", "nm", ["Share " + L.length + " node" + (L.length === 1 ? "" : "s") + " to Teams"]),
        h("small", "", [L.filter(function (m) { return shown(m) === "down"; }).length + " down · " + L.filter(function (m) { return shown(m) === "warn"; }).length + " warning"]), x]);
    var extra = [];
    if (one) {
      var as = h("button", "", [SEL[n.key] ? "Remove from selection" : "Add to selection"], {type: "button", title: "Tick it in the Not up list (to share several at once)"});
      as.addEventListener("click", function () { if (SEL[n.key]) delete SEL[n.key]; else SEL[n.key] = 1; as.textContent = SEL[n.key] ? "Remove from selection" : "Add to selection"; notUp(); drawGrid(); });
      extra.push(as);
    }
    var sh = nodesPanel(rows, context, extra, function () {
      var latest = keys.map(function (key) { return F.by[key]; }).filter(Boolean);
      if (!one) latest.sort(nodeOrder);
      return [latest.map(share), ctx(latest, kind)];
    });
    var card = h("div", "fl-card" + (one ? "" : " bulk"), [head, details ? h("div", "fl-cb", [kv(details)]) : null, sh], {role: "dialog", "aria-modal": "true", "aria-label": one ? n.name : "Share to Teams"});
    P.ov.appendChild(card);
    P.ov.classList.add("on");
    var focus = focusIndex >= 0 ? P.ov.querySelectorAll("button")[focusIndex] : null;
    if (focus) focus.focus();
    else if (!refreshing && sh.__focus) sh.__focus.focus();
    spotlight();
  }
  // the full list (from "+N more") and the comparison's lists: rows with checkboxes, Share these
  function comparisonRows(w) {
    return F.nodes.filter(function (n) {
      if (w === "v") return coverageCurrent() && n.vconly;
      if (w === "n" && !F.vcs.every(function (_, i) { return vcFreshI(i) === "ok"; })) return false;
      return n.flags.includes(w);
    });
  }
  function openList(title, filter, note, limit) {
    var L = filter ? comparisonRows(filter) : notUpList();
    OV = {kind: "list", title: title, filter: filter, note: note};
    clear(P.ov);
    var x = h("button", "x", ["×"], {type: "button", title: "Close (Esc)", "aria-label": "Close"});
    x.addEventListener("click", closeOv);
    var shareB = h("button", "btn pri", ["Share these to Teams"], {type: "button"});
    var selB = h("button", "btn", ["Tick all of these"], {type: "button"});
    var selcModal = h("span", "selc", ["0 selected"], {"aria-live": "polite"});
    var clrModal = h("button", "btn lnk", ["Clear"], {type: "button", title: "Untick selected nodes in this list", hidden: true});
    var modalBar = copyHelp.createFeedbackBar ? copyHelp.createFeedbackBar() : h("div", "copy-bar", null, {hidden: true});
    var pickedInList = function () {
      return L.map(function (n) { return F.by[n.key]; }).filter(function (n) { return n && SEL[n.key]; }).sort(nodeOrder);
    };
    var copyAllModal = copyHelp.createSplitControl ? copyHelp.createSplitControl({
      cls: "pri fl-copy-all", scope: "all", baseLabel: "Copy all shown", withCount: true, count: L.length,
      title: "Copy every node in this list (text + picture)",
      ariaLabel: "Copy all shown nodes", toggleLabel: "Copy all shown or selected options",
      menuTitle: "Copy all shown", menuTag: L.length + " row" + (L.length === 1 ? "" : "s"),
      subtitles: {both: "Text summary + PNG", text: "Plain + HTML summary", picture: "PNG summary card"},
      isEmpty: function () { return !(pickedInList().length || L.length); },
      onCopy: function (mode, btn, finish) {
        var sel = pickedInList();
        var now = sel.length ? sel : L.map(function (n) { return F.by[n.key]; }).filter(Boolean);
        if (!now.length) { finish(); return Promise.resolve(); }
        return copyNodesList(now, mode, btn, modalBar, finish);
      }
    }) : null;
    var box = h("div", "fl-evs all");
    var syncModalSel = function () {
      var nsel = pickedInList().length;
      selcModal.textContent = nsel + " selected";
      selcModal.classList.toggle("has-sel", nsel > 0);
      clrModal.hidden = nsel === 0;
      if (copyAllModal && copyAllModal.setSelection) copyAllModal.setSelection(nsel, L.length, "node");
      else if (copyAllModal && copyAllModal.setCount) copyAllModal.setCount(nsel || L.length);
      box.querySelectorAll(".ev").forEach(function (ev) {
        var k = ev.getAttribute("data-k"), cb = ev.querySelector("input[type=checkbox][data-k]");
        var on = !!(k && SEL[k]);
        if (cb) cb.checked = on;
        ev.classList.toggle("srv-selected", on);
      });
    };
    OV.syncSel = syncModalSel;
    var shownRows = 0, more = h("button", "btn", [""], {type: "button"});
    var page = function () {                                           // 200 rows at a time: every one reachable, none built ahead
      L.slice(shownRows, shownRows + 200).forEach(function (n) { box.insertBefore(row(n, "", modalBar), more); });
      shownRows = Math.min(L.length, shownRows + 200);
      OV.limit = shownRows;
      more.hidden = shownRows >= L.length;
      more.textContent = "Show " + Math.min(200, L.length - shownRows) + " more (" + (L.length - shownRows) + " not shown yet)";
    };
    box.appendChild(more);
    more.addEventListener("click", function (e) { e.stopPropagation(); page(); });
    do { page(); } while (shownRows < Math.min(limit || 200, L.length));
    shareB.addEventListener("click", function () {
      var now = L.map(function (n) { return F.by[n.key]; }).filter(Boolean);     // the latest read's rows, never the list's old ones
      var sel = now.filter(function (n) { return SEL[n.key]; });
      openCard((sel.length ? sel : now).sort(nodeOrder), "bulk");
    });
    selB.addEventListener("click", function () { L.forEach(function (n) { if (F.by[n.key]) SEL[n.key] = 1; }); syncModalSel(); notUp(); drawGrid(); });
    clrModal.addEventListener("click", function () { L.forEach(function (n) { delete SEL[n.key]; }); LAST_SEL = ""; syncModalSel(); notUp(); drawGrid(); });
    syncModalSel();
    P.ov.appendChild(h("div", "fl-card list", [h("h2", "", [h("span", "nm", [title]), h("small", "", [filter === "v" && !coverageCurrent() ? "–" : L.length + " node" + (L.length === 1 ? "" : "s")]), x]),
      note ? h("p", "fl-note", [note]) : null, h("div", "evh", [selB, shareB, copyAllModal, selcModal, clrModal, h("span", "srv-sel-cue fl-click", ["Tick or Space to select · Shift+click range · click row for card"])]), box, modalBar], {role: "dialog", "aria-modal": "true", "aria-label": title}));
    P.ov.classList.add("on");
    if (!limit) x.focus();
    spotlight();
  }
  function selected() { return Object.keys(SEL).map(function (k) { return F.by[k]; }).filter(Boolean).sort(nodeOrder); }

  // ---------------------------------------------------------------- events
  function wire() {
    B.addEventListener("click", function (e) {
      if (!F) return;
      var t = e.target;
      if (t.closest && t.closest(".copy-split")) return;
      if (t.closest(".fl-card") && t.closest("button") && !t.closest(".fl-evs")) return;    // the card's own buttons
      var cb = t.closest("input[type=checkbox][data-k]");
      if (cb) {
        var ck = cb.getAttribute("data-k"), inList = P.evs.contains(cb), on = !!cb.checked;
        if (e.shiftKey && LAST_SEL && LAST_SEL !== ck) {
          var scope = cb.closest(".fl-evs") || P.evs;
          var ks = [].map.call(scope.querySelectorAll("input[type=checkbox][data-k]"), function (x) { return x.getAttribute("data-k"); });
          var aI = ks.indexOf(LAST_SEL), bI = ks.indexOf(ck);
          if (aI >= 0 && bI >= 0) {
            for (var ri = Math.min(aI, bI); ri <= Math.max(aI, bI); ri++) { if (on) SEL[ks[ri]] = 1; else delete SEL[ks[ri]]; }
          }
        }
        if (on) SEL[ck] = 1; else delete SEL[ck];
        LAST_SEL = ck;
        if (inList) { notUp(); var again = P.evs.querySelector('input[data-k="' + (window.CSS && CSS.escape ? CSS.escape(ck) : ck) + '"]'); if (again) again.focus(); }
        else { notUp(); if (OV && OV.syncSel) OV.syncSel(); }                    // ticked in the full list: the panel's boxes and count follow
        drawGrid(); e.stopPropagation(); return;
      }
      var k = t.closest("[data-k]");
      if (k && F.by[k.getAttribute("data-k")]) { openCard([F.by[k.getAttribute("data-k")]]); return; }
      var g = t.closest("[data-g]");
      if (g) { SPOT.g = +g.getAttribute("data-g"); SPOT.left = DWELL; SPOT.pausedUntil = Date.now() + 60000; services(); spotlight(); drawGrid(); return; }
      var cm = t.closest("[data-cmp]");
      if (cm) {
        var w = cm.getAttribute("data-cmp");
        openList({v: "VMs in vCenter, not monitored in SolarWinds", n: "SolarWinds VMs not found in vCenter", a: "Ambiguous inventory matches"}[w], w,
                 w === "n" ? "A node is a VM by its Vendor or MachineType (" + F.vmRule + "); not found by exact name or guest IP in any vCenter." : "");
        return;
      }
      if (t === P.selall) { notUpList().forEach(function (n) { if (shown(n) === "down") SEL[n.key] = 1; }); notUp(); drawGrid(); return; }
      if (t === P.clr) { SEL = Object.create(null); LAST_SEL = ""; notUp(); drawGrid(); return; }
      if (t === P.shsel) {
        var sel = selected(), L2 = sel.length ? sel : notUpList().filter(function (n) { return shown(n) === "down"; });
        if (!L2.length) L2 = notUpList();                       // nothing ticked, nothing down: every node not up
        if (!L2.length) { P.selc.textContent = "nothing to share - nothing down or warning in this read"; return; }
        openCard(L2, "bulk"); return;
      }
      if (t === P.more) { openList("Not up · worst first"); return; }
      if (t === P.prev || t === P.next) { step(t === P.next ? 1 : -1, true); return; }
      if (t === P.ov) closeOv();
    });
    B.addEventListener("keydown", function (e) {           // a source's chip ([data-d]) is the page's own: it opens the source's card
      if (!F) return;
      if ((e.key === "Enter" || e.key === " ") && e.target.matches("[role=button]") && !e.target.closest("button,input,[data-d]")) { e.preventDefault(); e.target.click(); }
    });
    P.cv.addEventListener("mousemove", function (e) {
      var r = hit(e);
      if (!r.n) { P.tip.classList.remove("on"); P.cv.style.cursor = r.g ? "pointer" : "default"; return; }
      P.cv.style.cursor = "pointer";
      var n = r.n, s = shown(n);
      clear(P.tip);
      add(P.tip, [h("b", "", [n.name]), " · " + svcOf(n), h("br"), (n.ip || "no IP") + " · " + phrase(n),
        h("br"), h("span", "mute", [s === "unk" ? "grey: no current data" : "click for the card · Ctrl+click to select"])]);
      P.tip.classList.add("on");
      P.tip.style.left = Math.max(0, Math.min(r.mx + 18, P.gw.clientWidth - 340)) + "px";
      P.tip.style.top = Math.max(0, Math.min(r.my + 18, P.gw.clientHeight - 96)) + "px";
    });
    P.cv.addEventListener("mouseleave", function () { P.tip.classList.remove("on"); });
    P.cv.addEventListener("click", function (e) {
      var r = hit(e);
      if (r.n) {
        if (e.ctrlKey || e.metaKey || e.shiftKey) { if (SEL[r.n.key]) delete SEL[r.n.key]; else SEL[r.n.key] = 1; notUp(); drawGrid(); }
        else openCard([r.n]);
      } else if (r.g) { SPOT.g = r.g.i; SPOT.left = DWELL; SPOT.pausedUntil = Date.now() + 60000; services(); spotlight(); drawGrid(); }
    });
    // Esc closes the card first (and never leaves kiosk while one is open); ← → step the Spotlight
    document.addEventListener("keydown", function (e) {
      if (!F || !A || A.S.area !== "monitor" || !B || !B.isConnected) return;
      if (e.key === "Escape" && OV) { e.stopImmediatePropagation(); e.preventDefault(); closeOv(); return; }
      if (OV || (e.target.closest && e.target.closest("select,input,textarea"))) return;
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") { e.preventDefault(); step(e.key === "ArrowRight" ? 1 : -1, true); }
    }, true);
  }
  // the test harness (data-test) only: ?monitor=one | bulk opens a card at once (the screenshots); ?node=<key> on any wall: its card
  var OPENED = false;
  function firstOpen() {
    if (OPENED || !F) return;
    OPENED = true;
    var q = A.qs, k = q.get("node");
    if (k && Object.prototype.hasOwnProperty.call(F.by, k)) { openCard([F.by[k]]); return; }
    if (!A.test) return;
    var downs = notUpList().filter(function (n) { return shown(n) === "down"; });
    if (q.get("monitor") === "one" && downs.length) openCard([downs[0]]);
    if (q.get("monitor") === "bulk" && downs.length) { downs.forEach(function (n) { SEL[n.key] = 1; }); notUp(); drawGrid(); openCard(selected(), "bulk"); }
    if (q.get("monitor") === "list") openList("Not up · worst first");
  }

  // ---------------------------------------------------------------- the clock: the Spotlight's dwell, a read every 30 s, the hold while a card is open
  var tick = 0, LASTSIG = "";
  setInterval(function () {
    if (!A || !B || !B.isConnected || A.S.area !== "monitor" || document.hidden) return;
    tick++;
    if (OV && A.ROT.on) A.ROT.resumeAt = Date.now() + A.idleResume * 1000;     // a card open holds the rotation
    if (!frozen() && tick % 30 === 0) load(false);
    if (!F || frozen()) return;
    P.clock.firstChild.textContent = A.hms(NOW());
    ages();
    if (sig() !== LASTSIG) { refresh(); return; }       // a source went LATE / NOT CURRENT: drawn again at once
    if (!OV && SPOT.pausedUntil <= Date.now()) {
      SPOT.left--;
      if (SPOT.left <= 0) { step(1, false); return; }
    }
    var order = spotOrder();
    if (order.length) spotCue(Math.max(0, order.map(function (g) { return g.i; }).indexOf(SPOT.g)), order.length);
  }, 1000);

  function esc(t) {
    return String(t == null ? "" : t).replace(/[&<>"']/g, function (ch) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[ch]; });
  }
  function shareElement(tag, cls, kids, at) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    for (var k in at || {}) if (at[k] != null) e.setAttribute(k, at[k]);
    (kids || []).forEach(function (c) { if (c != null) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return e;
  }
  // 1.140.0: shared clipboard helpers (reuses window.SAPortalCopy when loaded in portal shell; keeps full fallback standalone)
  var copyHelp = window.SAPortalCopy || {
    canRichHtml: function () {
      return !!(navigator.clipboard && navigator.clipboard.write && window.ClipboardItem && window.isSecureContext
        && (!window.ClipboardItem.supports || window.ClipboardItem.supports("text/html")));
    },
    canPicture: function () {
      return !!(navigator.clipboard && navigator.clipboard.write && window.ClipboardItem && window.isSecureContext
        && (!window.ClipboardItem.supports || window.ClipboardItem.supports("image/png")));
    },
    isBlocked: function (e) {
      return !!(e && (e.name === "NotAllowedError" || e.name === "SecurityError"));
    },
    isUnsupported: function (e) {
      return !!(e && (e.name === "NotSupportedError" || e.name === "DataError" || e.name === "TypeError"));
    },
    pngDataUrl: function (cv) {
      if (!cv || typeof cv.toDataURL !== "function") return "";
      try {
        var u = cv.toDataURL("image/png");
        return typeof u === "string" && u.indexOf("data:image/png;base64,") === 0 ? u : "";
      } catch (e) { return ""; }
    },
    dataUrlToBlob: function (dataUrl) {
      if (!dataUrl || dataUrl.indexOf("data:image/png;base64,") !== 0) return null;
      try {
        var raw = atob(dataUrl.slice(22)), arr = new Uint8Array(raw.length);
        for (var i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
        return new Blob([arr], {type: "image/png"});
      } catch (e) { return null; }
    },
    embedPictureHtml: function (html, dataUrl, alt, width) {
      var body = html || "", w = width || 640;
      if (!dataUrl || dataUrl.indexOf("data:image/png;base64,") !== 0) return body;
      return body + '<p><img width="' + w + '" style="max-width:100%;height:auto" alt="' + esc(alt || "Snapshot") + '" src="' + dataUrl + '"></p>';
    },
    copyTextFallback: function (plain, focusEl) {
      var text = String(plain == null ? "" : plain);
      if (navigator.clipboard && navigator.clipboard.writeText && window.isSecureContext !== false) {
        return navigator.clipboard.writeText(text).catch(function (e) {
          if (copyHelp.isBlocked(e)) throw e;
          return copyHelp._execCopy(text, focusEl);
        });
      }
      return copyHelp._execCopy(text, focusEl);
    },
    _execCopy: function (text, focusEl) {
      return new Promise(function (res, rej) {
        var area = document.createElement("textarea");
        area.value = text;
        area.setAttribute("readonly", "");
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        try {
          if (typeof document.execCommand === "function" && document.execCommand("copy")) res();
          else rej(new Error("The browser blocked text copying."));
        } catch (e) {
          rej(e);
        } finally {
          if (area.parentNode) area.parentNode.removeChild(area);
          if (focusEl && typeof focusEl.focus === "function") focusEl.focus();
        }
      });
    },
    copyBothRich: function (opts) {
      var notify = opts.onState || function () {};
      var done = opts.onDone || function () {};
      var plain = String(opts.plain == null ? "" : opts.plain);
      var dataUrl = opts.dataUrl || "";
      var html = copyHelp.embedPictureHtml(opts.html || "", dataUrl, opts.alt, opts.width);
      var failMsg = function (e) {
        if (copyHelp.isBlocked(e)) return "Clipboard access denied. Allow clipboard access and retry.";
        return (e && e.message) ? String(e.message) : "Clipboard write failed. Try copying again.";
      };
      var fallbackText = function (why) {
        return copyHelp.copyTextFallback(plain, opts.focusEl).then(function () {
          notify("warn", "Text only", "Text copied. " + why);
        }, function (e) {
          notify("bad", "Copy failed", failMsg(e));
        }).then(done, done);
      };
      notify("info", "Copying\u2026", "Writing text and picture\u2026");
      var canRich = copyHelp.canRichHtml();
      if (!canRich || !dataUrl) {
        var why = window.isSecureContext === false
          ? "Rich HTML and picture copying needs HTTPS."
          : !canRich ? "This browser does not support picture copying."
          : "Picture could not be drawn here.";
        return fallbackText(why);
      }
      var items = {"text/plain": new Blob([plain], {type: "text/plain"}), "text/html": new Blob([html], {type: "text/html"})};
      if (opts.extraItems) {
        for (var k in opts.extraItems) if (Object.prototype.hasOwnProperty.call(opts.extraItems, k)) items[k] = opts.extraItems[k];
      }
      try {
        // Call clipboard.write synchronously inside the user gesture turn.
        return navigator.clipboard.write([new ClipboardItem(items)]).then(function () {
          notify("good", "Copied", "Copied text and picture. Ready to paste.");
          done();
        }, function (e) {
          if (copyHelp.isUnsupported(e)) return fallbackText("This browser does not support picture copying.");
          notify("bad", "Copy failed", failMsg(e));
          done();
        });
      } catch (e) {
        if (copyHelp.isUnsupported(e)) return fallbackText("This browser does not support picture copying.");
        notify("bad", "Copy failed", failMsg(e));
        done();
        return Promise.resolve();
      }
    },
    copyMode: function (mode, opts) {
      if (mode === "both" || !mode) return copyHelp.copyBothRich(opts);
      var notify = opts.onState || function () {}, done = opts.onDone || function () {};
      var plain = String(opts.plain == null ? "" : opts.plain), html = String(opts.html || "");
      var failMsg = function (e) {
        if (copyHelp.isBlocked(e)) return "Clipboard access denied. Allow clipboard access and retry.";
        return (e && e.message) ? String(e.message) : "Clipboard write failed. Try copying again.";
      };
      if (mode === "text") {
        notify("info", "Copying\u2026", "Writing text\u2026");
        var plainFallback = function () {
          return copyHelp.copyTextFallback(plain, opts.focusEl).then(function () {
            notify("good", "Copied", "Copied text. Ready to paste.");
          }, function (e) { notify("bad", "Copy failed", failMsg(e)); }).then(done, done);
        };
        if (html && copyHelp.canRichHtml()) {
          try {
            return navigator.clipboard.write([new ClipboardItem({
              "text/plain": new Blob([plain], {type: "text/plain"}), "text/html": new Blob([html], {type: "text/html"})
            })]).then(function () { notify("good", "Copied", "Copied text. Ready to paste."); done(); }, function (e) {
              if (copyHelp.isUnsupported(e)) return plainFallback();
              notify("bad", "Copy failed", failMsg(e)); done();
            });
          } catch (e) {
            if (copyHelp.isUnsupported(e)) return plainFallback();
            notify("bad", "Copy failed", failMsg(e)); done(); return Promise.resolve();
          }
        }
        return plainFallback();
      }
      notify("info", "Copying\u2026", "Writing picture\u2026");
      var picFallback = function (why) {
        return copyHelp.copyTextFallback(plain, opts.focusEl).then(function () {
          notify("warn", "Text only", "Text copied. " + why);
        }, function (e) { notify("bad", "Copy failed", failMsg(e)); }).then(done, done);
      };
      var canPic = copyHelp.canPicture(), blob = copyHelp.dataUrlToBlob(opts.dataUrl || "");
      if (!canPic || !blob) {
        var why = window.isSecureContext === false ? "Picture copying needs HTTPS."
          : !canPic ? "This browser does not support picture copying." : "Picture could not be drawn here.";
        return picFallback(why);
      }
      try {
        return navigator.clipboard.write([new ClipboardItem({"image/png": blob})]).then(function () {
          notify("good", "Copied", "Copied picture. Ready to paste."); done();
        }, function (e) {
          if (copyHelp.isUnsupported(e)) return picFallback("This browser does not support picture copying.");
          notify("bad", "Copy failed", failMsg(e)); done();
        });
      } catch (e) {
        if (copyHelp.isUnsupported(e)) return picFallback("This browser does not support picture copying.");
        notify("bad", "Copy failed", failMsg(e)); done(); return Promise.resolve();
      }
    }
  };
  // Monitor share controls: Copy text (text/plain
  // and text/html), Copy picture (PNG), Copy both, Save picture - each says in words what happened. o: {text(), html(), canvas(),
  // file, say (the status element), pic (the picture button's title)} -> [copyText, copyPic, copyBoth, save]
  function actions(o) {
    var say = o.say;
    var blob = function (cv) {
      return new Promise(function (res, rej) {
        (cv || o.canvas()).toBlob(function (bl) { if (bl) res(bl); else rej(new Error("no picture")); }, "image/png");
      });
    };
    var can = copyHelp.canRichHtml;
    var blocked = copyHelp.isBlocked;
    var b = function (t, cls, fn, title) {
      var x = shareElement("button", cls || "", [t], {type: "button", title: title});
      x.addEventListener("click", function () { if (o.prepare) o.prepare(); fn(); });
      return x;
    };
    var write = function (items, ok, no) {
      try {
        navigator.clipboard.write([new ClipboardItem(items)]).then(function () { say.textContent = ok; }, function (e) { say.textContent = no(e); });
      } catch (e) { say.textContent = no(e); }
    };
    var copyText = b("Copy text", "pri", function () {
      if (!can()) {
        if (navigator.clipboard && navigator.clipboard.writeText && window.isSecureContext) {
          navigator.clipboard.writeText(o.text()).then(function () { say.textContent = "Plain text copied (this browser takes no formatting) - in Teams press Ctrl+V."; },
            function () { say.textContent = "The browser blocked the copy - select the message above and press Ctrl+C."; });
        } else say.textContent = "This browser keeps the clipboard closed here - select the message above and press Ctrl+C.";
        return;
      }
      write({"text/plain": new Blob([o.text()], {type: "text/plain"}), "text/html": new Blob([o.html()], {type: "text/html"})},
        "Text copied - in Teams click the chat box and press Ctrl+V.",
        function (e) { return blocked(e) ? "The browser blocked the clipboard - select the message above and press Ctrl+C." : "The copy did not work here - select the message above and press Ctrl+C."; });
    }, "The message, formatted (bold, the link) with a plain-text copy for apps that take only text");
    if (!o.canvas) return [copyText];
    var copyPic = b("Copy picture", "", function () {
      if (!can()) { say.textContent = "This browser keeps pictures off the clipboard - use Save picture and drag the file into the chat."; return; }
      write({"image/png": blob()}, "Picture copied - in Teams press Ctrl+V.",
        function (e) { return blocked(e) ? "The browser blocked the clipboard - use Save picture and drag the file into the chat." : "This browser takes no pictures on the clipboard - use Save picture."; });
    }, o.pic || "The picture, as a PNG");
    var copyBoth = b("Copy both", "", function () {
      if (!can()) { say.textContent = "This browser keeps the clipboard closed here - select the message and press Ctrl+C, then use Save picture."; return; }
      var cv = o.canvas();
      var html = copyHelp.embedPictureHtml(o.html(), copyHelp.pngDataUrl(cv), o.pic || "Ops monitor snapshot");
      write({"text/plain": new Blob([o.text()], {type: "text/plain"}), "text/html": new Blob([html], {type: "text/html"}), "image/png": blob(cv)},
        "Copied together - press Ctrl+V in Teams. If only the text arrives, press Copy picture and Ctrl+V again (Teams picks one form of a paste).",
        function (e) { return blocked(e) ? "The browser blocked the clipboard - select the message and press Ctrl+C, then use Save picture." : "This browser takes one form at a time - use Copy text, paste, then Copy picture."; });
    }, "The message and the picture on the clipboard together (Teams may paste only one of them)");
    var save = b("Save picture", "", function () {
      try {
        var l = document.createElement("a");
        l.download = o.file;
        l.href = o.canvas().toDataURL("image/png");
        document.body.appendChild(l); l.click(); document.body.removeChild(l);
        say.textContent = "Picture saved - drag it from your downloads into the Teams chat.";
      } catch (e) { say.textContent = "The picture could not be saved here - right-click the picture and save it."; }
    }, "Download the picture (when the browser keeps pictures off the clipboard)");
    return [copyText, copyPic, copyBoth, save];
  }
  var NSEV = {crit: {c: "#e0454b", ink: "#ffffff"}, warn: {c: "#e8a92c", ink: "#1a1200"}, stale: {c: "#8b95a3", ink: "#111418"},
              off: {c: "#8b95a3", ink: "#111418"}, ok: {c: "#2f9e65", ink: "#ffffff"}};
  var S_RK = {crit: 0, warn: 1, stale: 2, off: 3, ok: 4};
  var PIC_MAX = 40, TEXT_MAX = 200;
  function rowSinceMs(n) {
    if (n && typeof n.since === "number" && n.since > 0) return n.since < 1e11 ? n.since * 1000 : n.since;
    return 0;
  }
  function fmtDurSec(sec) {
    var m = Math.max(0, Math.round(sec / 60));
    if (m < 60) return m + " min";
    if (m >= 1440) { var dd = Math.floor(m / 1440), hh = Math.floor((m % 1440) / 60); return dd + " d" + (hh ? " " + hh + " h" : ""); }
    return Math.floor(m / 60) + " h" + (m % 60 ? " " + (m % 60) + " min" : "");
  }
  function rowServer(n) {
    if (n.server) return String(n.server);
    if (n.ip && n.host) return n.ip + " / " + n.host;
    return n.ip || n.host || "no IP";
  }
  function rowService(n) { return n.svc || "no group"; }
  function rowDownSince(n, ctx) {
    if (n.downSince) return String(n.downSince);
    var ms = rowSinceMs(n);
    if (ms) {
      var z = (ctx && (ctx.tz || ctx.zone)) || "UTC", t = "";
      try {
        var nowMs = (ctx && typeof ctx.now === "number") ? (ctx.now < 1e11 ? ctx.now * 1000 : ctx.now) : Date.now();
        var hmFmt = new Intl.DateTimeFormat("en-GB", {timeZone: z, hour: "2-digit", minute: "2-digit", hour12: false});
        t = nowMs - ms > 20 * 3600000
          ? new Intl.DateTimeFormat("en-GB", {timeZone: z, day: "2-digit", month: "short"}).format(new Date(ms)) + " " + hmFmt.format(new Date(ms))
          : hmFmt.format(new Date(ms));
      } catch (e) { t = new Date(ms).toISOString().slice(11, 16); }
      return t + (n.exact === false ? " or earlier" : "");
    }
    var ph = String(n.phrase || "");
    var m = ph.match(/\(since\s+([^)]+)\)/i) || ph.match(/\bsince\s+([^,)]+)/i);
    if (m) return m[1].trim();
    return "\u2014";
  }
  function rowDownFor(n) {
    if (n.downFor) return String(n.downFor);
    if (typeof n.downSec === "number" && Number.isFinite(n.downSec)) return fmtDurSec(n.downSec) + (n.exact === false ? "+" : "");
    var ph = String(n.phrase || "");
    var m = ph.match(/^down\s+(at least\s+)?([^(]+)/i);
    if (m) return m[2].trim() + (m[1] ? "+" : "");
    var m2 = ph.match(/\(([^,)]+?)(?:,\s*since|\))/i);
    if (m2 && !/^since\b/i.test(m2[1].trim())) return m2[1].trim();
    return ph || "\u2014";
  }
  function rowDownSec(n) {
    if (typeof n.downSec === "number" && Number.isFinite(n.downSec)) return n.downSec;
    var dur = rowDownFor(n);
    if (!dur || dur === "-" || dur === "\u2014") return -1;
    var sec = 0, hit = false, m;
    if ((m = dur.match(/(\d+)\s*d\b/i))) { sec += (+m[1]) * 86400; hit = true; }
    if ((m = dur.match(/(\d+)\s*h\b/i))) { sec += (+m[1]) * 3600; hit = true; }
    if ((m = dur.match(/(\d+)\s*m(?:in)?\b/i))) { sec += (+m[1]) * 60; hit = true; }
    if ((m = dur.match(/(\d+)\s*s\b/i))) { sec += (+m[1]); hit = true; }
    return hit ? sec : -1;
  }
  function sortShareRows(L) {
    return (L || []).map(function (n, i) { return [n, i]; }).sort(function (a, b) {
      var na = a[0], nb = b[0];
      var ra = S_RK[na.sev] !== undefined ? S_RK[na.sev] : 2;
      var rb = S_RK[nb.sev] !== undefined ? S_RK[nb.sev] : 2;
      if (ra !== rb) return ra - rb;
      var sa = rowSinceMs(na), sb = rowSinceMs(nb);
      if (sa && sb && sa !== sb) return sa - sb;
      if (sa && !sb) return -1;
      if (!sa && sb) return 1;
      var da = rowDownSec(na), db = rowDownSec(nb);
      if (da >= 0 && db >= 0 && da !== db) return db - da;
      if (da >= 0 && db < 0) return -1;
      if (da < 0 && db >= 0) return 1;
      return a[1] - b[1];
    }).map(function (p) { return p[0]; });
  }
  function isSingleCard(L, ctx) { return L.length === 1 && !(ctx && ctx.bulk); }
  function nline(n, ctx, mixed) {
    var c = ctx && typeof ctx === "object" ? ctx : null;
    var nodeCol = (mixed === true && n.st && n.st !== "DOWN" ? "[" + n.st + "] " : "") + (n.name || "unknown");
    return [nodeCol, rowServer(n), rowService(n), rowDownSince(n, c), rowDownFor(n)].join(" | ");
  }
  function nhead(L, ctx) {
    ctx = ctx || {};
    if (isSingleCard(L, ctx)) return "[" + L[0].st + "] " + L[0].name + " - " + (L[0].svc || "no group");
    var down = L.filter(function (n) { return n.st === "DOWN" || n.sev === "crit"; }).length;
    var warn = L.filter(function (n) { return n.sev === "warn"; }).length;
    var other = L.length - down - warn;
    var allBad = L.every(function (n) { return !n.up && n.st !== "INFO"; });
    var timeStr = [ctx.day, ctx.stamp, ctx.zone].filter(Boolean).join(" ");
    var cntStr = down === L.length
      ? down + " node" + (down === 1 ? "" : "s") + " down"
      : down > 0
        ? down + " node" + (down === 1 ? "" : "s") + " down" + (warn ? ", " + warn + " warning" : "") + (other ? ", " + other + " other" : "")
        : warn === L.length
          ? warn + " node" + (warn === 1 ? "" : "s") + " warning"
          : L.length + (allBad ? " nodes not up" : " nodes");
    return [ctx.site, timeStr, cntStr].filter(Boolean).join(" \u00b7 ");
  }
  // the link back to the wall: http(s) only (anything else: the wall's own path, never a script)
  function safeLink(u) { return /^https?:\/\//i.test(String(u || "")) ? String(u) : "#"; }
  function ntext(L, ctx) {
    ctx = ctx || {};
    var one = isSingleCard(L, ctx);
    var rows = one ? L : sortShareRows(L);
    var out = [nhead(rows, ctx)];
    if (one) {
      var n = rows[0];
      out.push("IP " + (n.ip || "none") + (n.host ? " · Host " + n.host : "") + " · " + n.phrase + (n.loss ? " · " + n.loss + "% loss" : ""));
      if (n.vc) out.push("vCenter says: " + n.vc);
      out.push("Source: " + ctx.src + " · as of " + (ctx.asof || ctx.stamp) + (ctx.site ? " · " + ctx.site : ""));
      if (ctx.stale) out.push("Not current: " + ctx.stale);
      return out.join("\n") + "\nWall: " + ctx.link;
    }
    var mixed = !rows.every(function (n) { return n.st === "DOWN"; });
    out.push("");
    out.push("Node | Server | Service | Down since | Down for");
    rows.slice(0, TEXT_MAX).forEach(function (n) {
      out.push(nline(n, ctx, mixed) + (n.vc ? " · vCenter says: " + n.vc : ""));
    });
    if (rows.length > TEXT_MAX) out.push("+" + (rows.length - TEXT_MAX) + " more on the wall");
    if (ctx.stale) out.push("Not current: " + ctx.stale);
    out.push("");
    out.push("Wall: " + ctx.link);
    return out.join("\n");
  }
  function nhtml(L, ctx) {
    ctx = ctx || {};
    var td = function (t, st) { return "<td" + (st ? ' style="' + st + '"' : "") + ">" + esc(t) + "</td>"; };
    var th = function (t, st) { return "<th" + (st ? ' style="' + st + '"' : "") + ">" + esc(t) + "</th>"; };
    var one = isSingleCard(L, ctx);
    var rows = one ? L : sortShareRows(L);
    var link = safeLink(ctx.link);
    var linkTxt = link === "#" ? "Open on the wall" : ctx.link;
    if (one) {
      var n0 = rows[0], c0 = (NSEV[n0.sev] || NSEV.stale).c;
      var oneRow = "<tr>" + td(n0.st, "color:" + c0 + ";font-weight:700;padding-right:10px") + "<td style=\"padding-right:10px\"><b>" + esc(n0.name) + "</b></td>" +
        td(rowService(n0), "padding-right:10px") + td(rowServer(n0), "padding-right:10px") + td(n0.phrase) + "</tr>";
      return "<p><b>" + esc(nhead(rows, ctx)) + "</b>" + (ctx.site ? " · " + esc(ctx.site) : "") + "</p><table>" + oneRow + "</table>" +
        (n0.vc ? "<p>vCenter says: " + esc(n0.vc) + "</p>" : "") +
        "<p>Source: " + esc(ctx.src) + " · as of " + esc(ctx.asof || ctx.stamp) + "</p>" +
        (ctx.stale ? "<p><i>Not current: " + esc(ctx.stale) + "</i></p>" : "") +
        '<p><a href="' + esc(link) + '">Open on the wall</a> · ' + esc(ctx.src) + "</p>";
    }
    var mixed = !rows.every(function (n) { return n.st === "DOWN"; });
    var thSt = "text-align:left;padding:4px 10px 4px 0;border-bottom:1px solid #8b95a3;font-weight:700";
    var headRow = "<tr>" + th("Node", thSt) + th("Server", thSt) + th("Service", thSt) + th("Down since", thSt) + th("Down for", thSt) + "</tr>";
    var bodyRows = rows.slice(0, TEXT_MAX).map(function (n) {
      var c = (NSEV[n.sev] || NSEV.stale).c;
      var tag = mixed && n.st && n.st !== "DOWN" ? '<span style="color:' + c + ';font-weight:700">[' + esc(n.st) + ']</span> ' : "";
      var durStyle = "padding:3px 10px 3px 0;font-weight:700" + (n.sev === "crit" ? ";color:" + c : n.sev === "warn" ? ";color:#b87800" : "");
      return "<tr><td style=\"padding:3px 10px 3px 0\">" + tag + "<b>" + esc(n.name) + "</b></td>" +
        td(rowServer(n), "padding:3px 10px 3px 0") + td(rowService(n), "padding:3px 10px 3px 0") +
        td(rowDownSince(n, ctx), "padding:3px 10px 3px 0") +
        td(rowDownFor(n) + (n.vc ? " · vCenter says: " + n.vc : ""), durStyle) + "</tr>";
    }).join("");
    return "<p><b>" + esc(nhead(rows, ctx)) + "</b></p><table style=\"border-collapse:collapse\"><thead>" + headRow + "</thead><tbody>" + bodyRows + "</tbody></table>" +
      (rows.length > TEXT_MAX ? "<p>+" + (rows.length - TEXT_MAX) + " more on the wall</p>" : "") +
      (ctx.stale ? "<p><i>Not current: " + esc(ctx.stale) + "</i></p>" : "") +
      '<p>Wall: <a href="' + esc(link) + '">' + esc(linkTxt) + "</a> · " + esc(ctx.src) + "</p>";
  }
  function npicture(L, ctx) {
    ctx = ctx || {};
    var cv = document.createElement("canvas"), x = cv.getContext("2d");
    if (!x) return null;
    var one = isSingleCard(L, ctx);
    var rows = one ? L : sortShareRows(L);
    var shown = rows.slice(0, PIC_MAX), more = rows.length - shown.length;
    var sans = "Figtree, 'Segoe UI', Arial, sans-serif", mono = "'Fira Code', Consolas, monospace";
    var worst = one ? rows[0].sev : rows.some(function (n) { return n.sev === "crit"; }) ? "crit" : rows.some(function (n) { return n.sev === "warn"; }) ? "warn" : "stale";
    if (one) {
      var Wd = 1000, Hh = 330;
      cv.width = Wd * 2; cv.height = Hh * 2; x.scale(2, 2);
      x.fillStyle = "#15181d"; x.fillRect(0, 0, Wd, Hh);
      x.fillStyle = (NSEV[worst] || NSEV.stale).c; x.fillRect(0, 0, 12, Hh);
      x.fillStyle = "#f3f5f7"; x.font = "700 30px " + sans;
      x.fillText(fit(x, rows[0].name, 560), 36, 52);
      x.font = "600 16px " + mono; x.fillStyle = "#9aa3ae"; x.textAlign = "right";
      x.fillText(fit(x, (ctx.site ? ctx.site + " · " : "") + [ctx.day, ctx.stamp, ctx.zone].filter(Boolean).join(" "), 360), Wd - 30, 50);
      x.textAlign = "left"; x.fillStyle = "#c9d0d8"; x.font = "500 18px " + sans;
      x.fillText(fit(x, rowService(rows[0]) + " · " + rowServer(rows[0]), Wd - 72), 36, 82);
      var n = rows[0], s = NSEV[n.sev] || NSEV.stale;
      x.fillStyle = s.c; x.fillRect(36, 110, 6, 112);
      x.font = "700 15px " + mono; var bw = x.measureText(n.st).width + 20;
      x.fillRect(58, 112, bw, 26); x.fillStyle = s.ink; x.fillText(n.st, 68, 130);
      x.fillStyle = "#f3f5f7"; x.font = "700 26px " + sans; x.fillText(fit(x, n.phrase, Wd - 120 - bw), 70 + bw, 134);
      x.fillStyle = "#c9d0d8"; x.font = "500 19px " + sans;
      x.fillText(fit(x, n.said || "", Wd - 110), 58, 170);
      x.fillText(fit(x, n.vc ? "vCenter says: " + n.vc : "", Wd - 110), 58, 202);
      if (ctx.stale) { x.fillStyle = "#f3c46a"; x.font = "600 17px " + sans; x.fillText(fit(x, "NOT CURRENT: " + ctx.stale, Wd - 110), 58, 236); }
      x.fillStyle = "#9aa3ae"; x.font = "500 14px " + mono;
      x.fillText(fit(x, "Source " + ctx.src + (ctx.asof ? " · as of " + ctx.asof : "") + (ctx.link ? " · Wall: " + ctx.link : " · Ops center wall"), Wd - 72), 36, Hh - 18);
      return cv;
    }
    var mixed = !rows.every(function (n) { return n.st === "DOWN"; });
    var Wd = 1120, compact = shown.length > 15, rowH = compact ? 26 : 30;
    var headH = ctx.stale ? 104 : 84, colH = 26, moreH = more > 0 ? 30 : 0, footH = 34;
    var Hh = headH + colH + shown.length * rowH + moreH + footH;
    cv.width = Wd * 2; cv.height = Hh * 2; x.scale(2, 2);
    x.fillStyle = "#141922"; x.fillRect(0, 0, Wd, Hh);
    x.fillStyle = (NSEV[worst] || NSEV.stale).c; x.fillRect(0, 0, 8, Hh);
    x.textAlign = "left";
    x.fillStyle = "#96adc2"; x.font = "600 11px " + mono;
    x.fillText(mixed ? "OPS MONITOR · NODE STATUS REPORT CARD" : "OPS MONITOR · DOWN NODES REPORT CARD", 28, 24);
    x.textAlign = "right"; x.fillStyle = ctx.stale ? "#f3c46a" : "#96adc2";
    x.fillText(fit(x, (ctx.src || "SolarWinds") + (ctx.stale ? " · NOT CURRENT" : ""), 420), Wd - 24, 24);
    x.textAlign = "left";
    x.fillStyle = "#f4f7fa"; x.font = "700 22px " + sans;
    x.fillText(fit(x, nhead(rows, ctx), Wd - 56), 28, 55);
    x.fillStyle = "#9ab0c4"; x.font = "500 11.5px " + mono;
    x.fillText(fit(x, "Longest down first" + (more > 0 ? " · showing first " + shown.length + " of " + rows.length : " · " + rows.length + " total"), Wd - 56), 28, 74);
    if (ctx.stale) {
      x.fillStyle = "#f3c46a"; x.font = "600 12px " + sans;
      x.fillText(fit(x, "NOT CURRENT: " + ctx.stale, Wd - 56), 28, 94);
    }
    var y = headH;
    x.fillStyle = "#202836"; x.fillRect(24, y, Wd - 48, colH);
    x.fillStyle = "#9eb5ca"; x.font = "700 11px " + mono;
    x.fillText("NODE", 34, y + 17);
    x.fillText("SERVER (IP / HOST)", 264, y + 17);
    x.fillText("SERVICE", 584, y + 17);
    x.fillText("DOWN SINCE", 798, y + 17);
    x.textAlign = "right";
    x.fillText("DOWN FOR", Wd - 32, y + 17);
    x.textAlign = "left";
    y += colH;
    var fSize = compact ? 12 : 13, baseOff = compact ? 17 : 20;
    shown.forEach(function (n, i) {
      var s = NSEV[n.sev] || NSEV.stale;
      x.fillStyle = i % 2 ? "#161d28" : "#1b2330";
      x.fillRect(24, y, Wd - 48, rowH);
      x.fillStyle = s.c; x.fillRect(24, y, 3, rowH);
      x.fillStyle = "#263140"; x.fillRect(24, y + rowH - 1, Wd - 48, 1);
      var nodeTxt = (mixed && n.st && n.st !== "DOWN" ? "[" + n.st + "] " : "") + (n.name || "unknown");
      x.fillStyle = "#f4f7fa"; x.font = "700 " + fSize + "px " + mono;
      x.fillText(fit(x, nodeTxt, 222), 34, y + baseOff);
      x.fillStyle = "#d3e0ec"; x.font = "500 " + fSize + "px " + mono;
      x.fillText(fit(x, rowServer(n), 310), 264, y + baseOff);
      x.fillStyle = "#dce6f0"; x.font = "500 " + fSize + "px " + sans;
      x.fillText(fit(x, rowService(n), 204), 584, y + baseOff);
      x.fillStyle = "#c8d6e3"; x.font = "500 " + fSize + "px " + mono;
      x.fillText(fit(x, rowDownSince(n, ctx), 166), 798, y + baseOff);
      x.textAlign = "right";
      x.fillStyle = n.sev === "crit" ? "#ff8f94" : n.sev === "warn" ? "#f3c46a" : "#c8d6e3";
      x.font = "700 " + fSize + "px " + mono;
      x.fillText(fit(x, rowDownFor(n), 118), Wd - 32, y + baseOff);
      x.textAlign = "left";
      y += rowH;
    });
    if (more > 0) {
      x.fillStyle = "#202836"; x.fillRect(24, y, Wd - 48, moreH - 4);
      x.fillStyle = "#f3c46a"; x.font = "700 12.5px " + mono;
      x.fillText("+" + more + " more on the wall (showing " + shown.length + " longest down)", 34, y + 18);
      y += moreH;
    }
    x.fillStyle = "#2b3748"; x.fillRect(24, Hh - footH, Wd - 48, 1);
    x.fillStyle = "#9ab0c4"; x.font = "500 11.5px " + mono;
    x.fillText(fit(x, "Wall: " + (ctx.link || "Ops center wall"), Wd - 56), 28, Hh - 12);
    return cv;
  }
  // the share panel for nodes, built node by node: the message as it pastes, the buttons, what happened, the picture
  function nodesPanel(L, ctx, extra, current) {
    ctx = ctx || {};
    var one = isSingleCard(L, ctx);
    var rows = one ? L : sortShareRows(L);
    var mixed = !rows.every(function (n) { return n.st === "DOWN"; });
    var say = shareElement("p", "shd", [""], {role: "status", "aria-live": "polite"});
    var tb = shareElement("table", "fl-msgt", null);
    if (!one) {
      tb.appendChild(shareElement("thead", "", [shareElement("tr", "", [
        shareElement("th", "", ["Node"]), shareElement("th", "", ["Server"]), shareElement("th", "", ["Service"]),
        shareElement("th", "", ["Down since"]), shareElement("th", "", ["Down for"])
      ])]));
    }
    var tbody = shareElement("tbody", "", null);
    rows.slice(0, TEXT_MAX).forEach(function (n) {
      if (one) {
        tbody.appendChild(shareElement("tr", "", [shareElement("td", "s " + n.sev, [n.st]), shareElement("td", "", [shareElement("b", "", [n.name])]), shareElement("td", "", [rowService(n)]),
          shareElement("td", "", [rowServer(n)]), shareElement("td", "", [n.phrase])]));
      } else {
        var nodeCellKids = mixed && n.st && n.st !== "DOWN"
          ? [shareElement("span", "s " + n.sev, ["[" + n.st + "] "]), shareElement("b", "", [n.name])]
          : [shareElement("b", "", [n.name])];
        tbody.appendChild(shareElement("tr", "", [
          shareElement("td", "", nodeCellKids),
          shareElement("td", "mono", [rowServer(n)]),
          shareElement("td", "", [rowService(n)]),
          shareElement("td", "mono", [rowDownSince(n, ctx)]),
          shareElement("td", "s mono " + n.sev, [rowDownFor(n)])
        ]));
      }
    });
    tb.appendChild(tbody);
    var msg = shareElement("div", "msg", [shareElement("p", "", [shareElement("b", "", [nhead(rows, ctx)])]), tb,
      rows.length > TEXT_MAX ? shareElement("p", "", ["+" + (rows.length - TEXT_MAX) + " more on the wall"]) : null,
      ctx.stale ? shareElement("p", "", ["Not current: " + ctx.stale]) : null, shareElement("p", "", ["Wall: " + ctx.link])]);
    var cv = npicture(rows, ctx), picture;
    if (cv) {
      var img = shareElement("img", "shi", null, {alt: "The picture: " + nhead(rows, ctx)});
      img.src = cv.toDataURL("image/png");
      picture = shareElement("div", "", [shareElement("p", "lab", ["Picture - drawn from the wall's data, not a screen grab" + (rows.length > PIC_MAX ? " (the first " + PIC_MAX + ")" : "")]), img]);
      if (document.fonts && document.fonts.load) {
        Promise.all([document.fonts.load("700 30px Figtree"), document.fonts.load("600 16px 'Fira Code'")])
          .then(function () { var updated = npicture(rows, ctx); if (updated) img.src = updated.toDataURL("image/png"); }, function () { /* the system font stays */ });
      }
    }
    var content;
    var acts = actions({prepare: function () { content = current(); },
                        text: function () { return ntext.apply(null, content); }, html: function () { return nhtml.apply(null, content); },
                        canvas: cv && function () { return npicture.apply(null, content); },
                        file: (one ? rows[0].name : "nodes-down").replace(/[^A-Za-z0-9._-]+/g, "-").slice(0, 60) + "-" + String(ctx.stamp || "").replace(/[^0-9]/g, "") + ".png",
                        say: say, pic: "The picture on the right, as a PNG"});
    var panel = shareElement("div", "fl-share", [
      shareElement("div", "", [shareElement("p", "lab", ["Message - pastes formatted"]), msg, shareElement("div", "btns", acts.concat(extra || [])), say,
        shareElement("p", "hint", [cv ? "In Teams: click the chat box, then Ctrl+V. Copy both puts the text and the picture on together; Teams may paste only one - then Copy picture and paste again." : "In Teams: click the chat box, then Ctrl+V."])]),
      picture]);
    panel.__focus = acts[0];
    return panel;
  }
  function fleetTile(api, sevWord) {
    var w = api.W(), f = w.fleet || {}, c = f.counts || {}, st = api.areaState("monitor"), n = api.areaCounts("monitor"), tot = f.total || 0;
    var up = f.up || 0;
    if (st === "stale") n = {crit: 0, warn: 0};
    return {id: "monitor", word: st === "off" && f.state === "off" ? f.label : sevWord(st), st: st, n: n, m: null, big: st !== "stale" && tot ? up.toLocaleString("en") + "/" + tot.toLocaleString("en") : "–", unit: st === "stale" ? "no current data" : tot ? "nodes up" : "no value read",
            sub: st === "stale" ? "" : st === "off" ? f.label : n.crit + n.warn ? api.nowList("monitor").filter(function (i) { return i.type === "cond"; }).slice(0, 1).map(function (i) { return i.t; })[0] || "" :
              tot ? (+c.d || 0) + " down · " + (+c.p || 0) + " powered off · " + (+c.m || 0) + " unmanaged · " + (f.groups || 0) + " groups" : ""};
  }

  function groupChart(api, a, box) {
    var h = api.h;
    var fw = (api.W().fleet || {}).worst || [], last = a.st === "stale";
    box.appendChild(h("div", "sx-chart", [h("div", "ch", [h("b", "", ["Service groups with a node not up"]), h("span", "", ["click for the Ops monitor area"])])].concat(
      !last && fw.length ? fw.map(function (g) {
        var st = g[1] ? "crit" : "warn";
        return h("div", "crow " + st, [h("span", "nm", [String(g[0])]), api.word(st, [g[1] ? g[1] + " down" : "", g[2] ? g[2] + " warning" : ""].filter(Boolean).join(" · "))],
                 {"data-goto": "monitor", tabindex: "0", role: "button", title: "Open Ops monitor"});
      }) : [h("p", "wx-empty", [a.st === "ok" ? "No service group with a node down or warning." : "No current data."])]).concat([
      h("button", "sx-insp", ["Open Ops monitor →"], {type: "button", "data-goto": "monitor", title: "Every node by service group"})])));
  }

  var monitorIcon = [["rect", {x: 3, y: 3, width: 5, height: 5, rx: 1}], ["rect", {x: 9.5, y: 3, width: 5, height: 5, rx: 1}], ["rect", {x: 16, y: 3, width: 5, height: 5, rx: 1}],
            ["rect", {x: 3, y: 9.5, width: 5, height: 5, rx: 1}], ["rect", {x: 9.5, y: 9.5, width: 5, height: 5, rx: 1}], ["circle", {cx: 18.5, cy: 12, r: 3}],
            ["rect", {x: 3, y: 16, width: 5, height: 5, rx: 1}], ["rect", {x: 9.5, y: 16, width: 5, height: 5, rx: 1}], ["rect", {x: 16, y: 16, width: 5, height: 5, rx: 1}]];
  window.SAWallFleet = {icon: monitorIcon, tile: fleetTile, groupChart: groupChart, render: render, fitted: fitted, failed: function () { return !!FAIL; },
    copy: copyHelp, nodes: {line: nline, text: ntext, html: nhtml, picture: npicture, panel: nodesPanel}, _adapt: adapt};
})();
