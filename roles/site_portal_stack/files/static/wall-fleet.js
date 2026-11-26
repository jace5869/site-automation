// 1.138.0: the ops wall's "Fleet & services" area (?area=fleet) - every node SolarWinds watches, up or down, by service group,
// compared with vCenter (roles/site_portal_stack/files/wall_fleet.py makes /api/wall/fleet). One board in every wall style (the
// style's colours and type): the services strip (worst member wins; click = the Spotlight), one square a node on a canvas (down: a
// red ring and its name; warning: amber with a notch; grey hollow: powered off or unmanaged; grey: no current data - never green),
// "Not up · worst first" with checkboxes, "SolarWinds vs vCenter", and a Spotlight that steps through the groups, problems first.
// A click on a node (a square, a row, a chip) opens its card: Copy text / picture / both, Save picture (static/wall-share.js);
// Ctrl / Shift + click selects several, "Share selected to Teams" shares them at once. No HTML from strings (the page's CSP): the
// DOM is built node by node, every value from the answer goes in as text.
(function () {
  "use strict";
  var A = null, F = null, ETAG = "", FAIL = "", LOADED_AT = 0, BUSY = false, VER = 0, GSC = {sig: "", m: {}};
  var B = null, P = {};                       // the board (kept across the page's redraws, so an open card survives them) and its parts
  var SEL = {}, OV = null;                    // the selection (node keys), the open card or list
  var SPOT = {g: null, left: 20, pausedUntil: 0}, DWELL = 20;
  var LAY = {blocks: [], S: 10, k: 1};
  var ST_WORD = {d: "DOWN", w: "WARN", p: "OFF", m: "UNMANAGED", x: "UNKNOWN", u: "UP"};
  var KIND = {g: "SolarWinds group", c: "custom property", n: "in SolarWinds, no group", v: "not in SolarWinds - status from vCenter"};

  function h(t, c, k, a) { return A.h(t, c, k, a); }
  function W() { return A.W(); }
  function NOW() { return A.NOW(); }
  function frozen() { return A.frozen(); }
  function clear(e) { while (e.firstChild) e.removeChild(e.firstChild); return e; }
  function soon(fn) { requestAnimationFrame(function () { fn(); }); }      // after the layout (never the page's test queue)

  // ---------------------------------------------------------------- the answer
  function url() { return A.src + "/fleet"; }
  function adapt(a) {
    var arr = function (x) { return Array.isArray(x) ? x : []; };
    var groups = arr(a.groups).map(function (g, i) { return {i: i, name: String(g[0] || ""), kind: g[1] || "g", sw: g[2] || "", nodes: []}; });
    var hosts = arr(a.hosts), models = arr(a.models), vcs = arr(a.vcs), vmw = a.vmw || {};
    var nodes = arr(a.nodes).filter(Array.isArray).map(function (r) {
      var n = {g: +r[0] || 0, name: String(r[1] == null ? "" : r[1]), ip: String(r[2] || ""), c: ST_WORD[r[3]] ? r[3] : "x", rt: typeof r[4] === "number" ? r[4] : null,
               loss: typeof r[5] === "number" ? r[5] : null, since: r[6] ? r[6] * 1000 : 0, exact: !!r[7], why: String(r[8] || ""), vm: vmw[r[9]] || "",
               host: hosts[r[10]] || "", vc: vcs[r[11]] ? String(vcs[r[11]][0]) : "", flags: String(r[12] || ""), key: String(r[13]), also: arr(r[14]),
               polled: typeof r[15] === "number" ? r[15] : null, said: String(r[16] || ""), model: models[r[17]] || "", vci: typeof r[11] === "number" ? r[11] : -1};
      n.vconly = n.flags.indexOf("v") >= 0;
      return n;
    });
    // g.nodes: its squares (each node once, in its first group); g.members: every node in it (a node in two groups counts in both)
    groups.forEach(function (g) { g.members = []; });
    nodes.forEach(function (n) { var g = groups[n.g]; if (g) { g.nodes.push(n); g.members.push(n); } else n.g = -1; });
    nodes.forEach(function (n) { if (n.g >= 0) n.also.forEach(function (i) { if (groups[i] && i !== n.g) groups[i].members.push(n); }); });
    nodes = nodes.filter(function (n) { return n.g >= 0; });
    var by = {};
    nodes.forEach(function (n) { by[n.key] = n; });
    var f = a.feed || {};
    VER++;
    return {groups: groups, nodes: nodes, by: by, cmp: a.cmp || {}, counts: a.counts || {}, vcs: vcs, incomplete: arr(a.incomplete), notes: arr(a.notes),
            seconds: a.seconds || 60, feed: f, zone: a.zone || "UTC", title: a.title || "", enclave: a.enclave || "", group_by: a.group_by || "groups",
            vmRule: (a.cmp || {}).vm_rule || "", late: +a.late || 180, stale: +a.stale || 600};
  }
  function load(first) {
    if (BUSY) return;
    BUSY = true;
    var hd = {Accept: "application/json"};
    if (ETAG && !first) hd["If-None-Match"] = ETAG;
    fetch(url(), {headers: hd, credentials: "same-origin", cache: "no-store"}).then(function (res) {
      if (res.status === 304) return null;
      if (!res.ok) return res.json().then(function (j) { throw new Error((j && j.error) || "HTTP " + res.status); }, function () { throw new Error("HTTP " + res.status); });
      ETAG = (res.headers && res.headers.get && res.headers.get("ETag")) || "";
      return res.json();
    }).then(function (a) {
      BUSY = false; FAIL = ""; LOADED_AT = Date.now();
      if (a) { F = adapt(a); Object.keys(SEL).forEach(function (k) { if (!F.by[k]) delete SEL[k]; }); }
      if (A && A.S.area === "fleet") { refresh(); firstOpen(); }
    }).catch(function (e) {
      BUSY = false; FAIL = (e && e.message) || "not reachable";
      if (A && A.S.area === "fleet") refresh();
    });
  }

  // ---------------------------------------------------------------- the one rule: green only when current
  // freshness from the rows on screen: their own answer's read times (never the wall's other poll), the portal reachable,
  // this answer fetched; LATE / STALE as the answer says (SolarWinds: stretched with portal_wall_fleet_seconds)
  function age(asof) { return asof == null ? null : Math.max(0, (NOW() - asof * 1000) / 1000); }
  function fr(asof, state, late, stale) {
    if (FAIL || A.unreach() || state !== "ok" || asof == null) return "stale";
    var a = age(asof);
    return a > stale ? "stale" : a > late ? "late" : "ok";
  }
  function swFresh() { return F ? fr(F.feed.asof, F.feed.state, F.late, F.stale) : "stale"; }
  function vcFreshI(i) { var v = F && F.vcs[i]; return v ? fr(v[1], v[2], (W() && W().late) || 180, (W() && W().stale) || 600) : "stale"; }
  function fresh(n) { return n.vconly ? (n.flags.indexOf("s") >= 0 ? "stale" : vcFreshI(n.vci)) : swFresh(); }
  function sig() { var x = [VER, swFresh(), FAIL ? 1 : 0]; (F ? F.vcs : []).forEach(function (v, i) { x.push(vcFreshI(i)); }); return x.join(","); }
  // what a node is drawn as: up (green, only when its source is current), down, warn, off (hollow grey), unk (grey),
  // lastdown / lastwarn (its source not current: grey, "last known")
  function shown(n) {
    var f = fresh(n), old = n.flags.indexOf("o") >= 0, whole = !F.incomplete.length || n.vconly;
    if (n.c === "d") return f === "stale" ? "lastdown" : "down";                    // red beats grey: an old poll's down stays down
    if (n.c === "w") return f === "stale" || old ? "lastwarn" : "warn";
    if (n.c === "p" || n.c === "m") return "off";
    if (n.c === "u") return f === "ok" && !old && whole ? "up" : "unk";            // green only from a current, whole read and poll
    return "unk";
  }
  var RANK = {down: 0, warn: 1, lastdown: 2, lastwarn: 3, unk: 4, off: 5, up: 6};
  function sevOf(s) { return s === "down" ? "crit" : s === "warn" ? "warn" : s === "up" ? "ok" : s === "off" ? "off" : "stale"; }
  // a group's state from every member (a node in two groups counts in both), kept until the data or a source's freshness changes
  function gState(g) {
    var sg = sig();
    if (GSC.sig !== sg) GSC = {sig: sg, m: {}};
    if (GSC.m[g.i]) return GSC.m[g.i];
    var c = {down: 0, warn: 0, up: 0, off: 0, unk: 0, lastdown: 0, lastwarn: 0};
    (g.members || g.nodes).forEach(function (n) { c[shown(n)]++; });
    c.n = (g.members || g.nodes).length;
    c.st = c.down ? "crit" : c.warn ? "warn" : c.unk || c.lastdown || c.lastwarn || !c.n ? "stale" : !c.up ? "off" : "ok";
    if (c.st === "ok" && g.kind !== "v" && F.incomplete.length) c.st = "stale";        // a read not whole: never green
    GSC.m[g.i] = c;
    return c;
  }
  function svcOf(n) { var g = F.groups[n.g]; return g ? (g.kind === "n" ? "no group" : g.name) : ""; }
  function hm(ms) { return A.hm(ms); }
  function phrase(n) {
    var s = shown(n), d = n.since ? A.dur(NOW() - n.since) : "";
    var since = n.since ? " (since " + hm(n.since) + (n.exact ? "" : " or earlier") + ")" : " (since not known yet)";
    var plus = n.exact ? "" : "+";
    if (n.c === "d") return (s === "lastdown" ? "last known down" : "down") + (d ? " " + d.replace(/ min$/, plus + " min").replace(/ h$/, plus + " h") : "") + since;
    if (n.c === "w") return (n.why || "warning") + (d ? " (" + d + (n.exact ? "" : "+") + ", since " + hm(n.since) + (n.exact ? "" : " or earlier") + ")" : "");
    if (n.c === "u") return s === "up" ? "up" + (n.rt != null ? " · " + n.rt + " ms" : "") : "up when last read - not current";
    return n.why || ST_WORD[n.c].toLowerCase();
  }
  function agoS(asof) { return asof == null ? "never read" : A.ago(age(asof)); }
  function staleText() {
    if (FAIL) return "the Fleet answer could not be read (" + FAIL + ") - the last one shown";
    if (A.unreach()) return "the portal is not answering - the last answer shown";
    var f = F.feed, fv = swFresh();
    if (fv === "ok") return "";
    if (!f.asof) return "SolarWinds not read yet" + (f.why ? ": " + f.why : "");
    return "SolarWinds last read " + agoS(f.asof) + (f.state !== "ok" && f.why ? " - " + f.why : "") + (fv === "late" ? " (late)" : "");
  }
  function srcOf(n) { return n.vconly ? {name: "vCenter " + n.vc, asof: (F.vcs[n.vci] || [])[1]} : {name: "SolarWinds", asof: F.feed.asof}; }
  function share(n) {
    var s = shown(n);
    return {st: ST_WORD[n.c], sev: sevOf(s), name: n.name, svc: svcOf(n), ip: n.ip, phrase: phrase(n), loss: n.loss, vc: n.vm, host: n.host,
            said: n.why || (n.said ? "SolarWinds: " + n.said : ""), up: s === "up" || s === "off"};
  }
  function ctx(L) {
    var w = W(), z = F.zone || "UTC", day = "", one = L.length === 1 ? L[0] : null;
    try { day = new Intl.DateTimeFormat("en-GB", {timeZone: z, day: "2-digit", month: "short"}).format(new Date(NOW())); } catch (e) { day = ""; }
    var base = location.origin + A.src.replace(/api\/wall$/, "wall") + "?area=fleet";
    var src = one ? srcOf(one) : null, vco = L.some(function (n) { return n.vconly; }), sw = L.some(function (n) { return !n.vconly; });
    var stale = L.some(function (n) { var s = shown(n); return s === "lastdown" || s === "lastwarn" || s === "unk"; }) ?
      (sw && swFresh() !== "ok" ? staleText() : "a source of these is not current - the last known state") : (sw ? staleText() : "");
    return {site: w.enclave || "", stamp: hm(NOW()), day: day, zone: A.zoneName(),
            src: one ? src.name + (one.vconly ? "" : " every " + F.seconds + " s · vCenter compared") : (sw ? "SolarWinds every " + F.seconds + " s" : "") + (sw && vco ? " · " : "") + (vco ? "vCenter (VMs not in SolarWinds)" : ""),
            asof: src && src.asof ? hm(src.asof * 1000) : hm(NOW()), link: one ? base + "&node=" + encodeURIComponent(one.key) : base, stale: stale};
  }

  // ---------------------------------------------------------------- the board
  function build() {
    B = h("div", "wx wx-fleet", null, {"aria-label": "Fleet and services"});
    P.title = h("b", "", ["Fleet & services"]);
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
    P.selc = h("span", "selc", ["0 selected"]);
    P.clr = h("button", "btn lnk", ["Clear"], {type: "button", title: "Untick every node", hidden: true});
    P.evs = h("div", "fl-evs");
    P.more = h("button", "fl-more", [""], {type: "button", hidden: true, title: "Every node not up, with checkboxes"});
    var list = h("section", "fl-panel fl-list", [h("div", "ph", [h("h3", "", ["Not up · worst first"]), P.ncount]),
      h("div", "evh", [P.selall, P.shsel, P.selc, P.clr]), P.evs, P.more]);
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
    stage.appendChild(B);
    refresh();
  }
  function fitted(k) { LAY.k = k || 1; if (A && A.S.area === "fleet") soon(drawGrid); }

  function refresh() {
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
      P.ban.className = "fl-ban on"; P.ban.textContent = FAIL ? "The Fleet area could not be read: " + FAIL + ". It tries again every 30 s." : "Reading SolarWinds…";
      return;
    }
    pills(); banner(); services(); notUp(); compare(); spotlight(); footer();
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
  // each source's age, as the wall's rule says it: "35 s", LATE, NOT CURRENT - from the Fleet answer's own read times (ticks every second)
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
    F.nodes.forEach(function (n) { if (!seen[n.key]) { seen[n.key] = 1; c[shown(n)]++; } });
    return c;
  }
  function pills() {
    var c = counts(), last = c.lastdown + c.lastwarn;
    clear(P.pills);
    var p = function (cls, st, txt, title) { return h("span", "pill " + cls, [A.word(st, txt)], {title: title}); };
    add(P.pills, [
      p("c" + (c.down ? " on" : ""), c.down ? "crit" : "off", c.down + " down", "nodes down now (SolarWinds; powered off in vCenter is not down)"),
      p("w" + (c.warn ? " on" : ""), c.warn ? "warn" : "off", c.warn + " warning", "packet loss or slow answers"),
      p("o", c.up ? "ok" : "off", c.up.toLocaleString("en") + " up", "up, from a current read"),
      p("g", "off", c.off + " off / unmanaged", "powered off in vCenter, or unmanaged in SolarWinds"),
      c.unk || last ? p("s", "stale", (c.unk + last).toLocaleString("en") + " no current data", "grey: no current read - never green" + (last ? " · " + last + " of them last known not up (in the Not up list)" : "")) : null]);
  }
  function add(e, kids) { return A.add(e, kids); }
  function banner() {
    var t = staleText(), inc = F.incomplete;
    P.ban.className = "fl-ban" + (t || inc.length ? " on" : "");
    clear(P.ban);
    if (t) add(P.ban, [A.word("stale", "NOT CURRENT"), h("span", "", [t + " - grey = no current data; nothing here is green until SolarWinds is read again."])]);
    else if (inc.length) add(P.ban, [A.word("stale", "NOT WHOLE"), h("span", "", [inc.slice(0, 2).join(" · ") + " - the area is not shown OK until the read is whole."])]);
  }
  // the services strip: SolarWinds' groups (or the custom property's values), worst member wins
  function services() {
    clear(P.svc);
    var gs = F.groups.filter(function (g) { return g.kind === "g" || g.kind === "c"; });
    var cue = document.getElementById("flSvcCue");
    if (cue) cue.textContent = (F.group_by === "groups" ? "SolarWinds groups" : "by the custom property " + F.group_by.slice(7)) + " · worst member wins";
    if (!gs.length) { P.svc.appendChild(h("p", "wx-empty", [F.group_by === "groups" ? "No SolarWinds group with nodes in it - every node is under Workloads (no group)." : "No node has a value in " + F.group_by.slice(7) + "."])); return; }
    var max = 16, list = gs;
    if (gs.length > max) {
      var rk = {crit: 0, warn: 1, stale: 2, ok: 3};
      list = gs.slice().sort(function (a, b) { return rk[gState(a).st] - rk[gState(b).st] || a.i - b.i; }).slice(0, max - 1).sort(function (a, b) { return a.i - b.i; });
    }
    P.svc.classList.toggle("two", list.length > 8);
    list.forEach(function (g) {
      var c = gState(g), word = c.down ? c.down + " down" : c.warn ? c.warn + " warn" : c.st === "stale" ? "not current" : c.st === "off" ? "off / unmanaged" : c.off ? "rest off" : "all up";
      P.svc.appendChild(h("div", "sv " + c.st + (SPOT.g === g.i ? " on" : ""), [h("b", "", [g.name]),
        h("span", "ln", [A.word(c.st === "ok" ? "ok" : c.st, word), h("small", "", [c.up + "/" + c.n + " up"])])],
        {"data-g": String(g.i), tabindex: "0", role: "button", title: g.name + ": put it in the Spotlight" + (g.sw ? " · SolarWinds says " + ST_WORD[g.sw].toLowerCase() : "")}));
    });
    if (gs.length > list.length) P.svc.appendChild(h("div", "sv more", [h("b", "", ["+" + (gs.length - list.length) + " more groups"]), h("span", "", ["all in the grid below"])]));
  }
  function notUpList() {
    return F.nodes.filter(function (n) { var s = shown(n); return s === "down" || s === "warn" || s === "lastdown" || s === "lastwarn"; })
      .sort(function (a, b) { return RANK[shown(a)] - RANK[shown(b)] || (a.since || 9e15) - (b.since || 9e15) || (a.name < b.name ? -1 : 1); });
  }
  function row(n, cls) {
    var s = shown(n), cb = h("input", "", null, {type: "checkbox", "data-k": n.key, "aria-label": "select " + n.name, checked: !!SEL[n.key]});
    return h("div", "ev " + sevOf(s) + (cls ? " " + cls : ""), [
      h("span", "s", [ST_WORD[n.c]]), h("span", "n", [cb, h("b", "", [n.name])]), h("span", "a", [n.since ? A.dur(NOW() - n.since) + (n.exact ? "" : "+") : ""]),
      h("span", "d", [[svcOf(n), n.ip || "no IP", n.why || ST_WORD[n.c].toLowerCase(), n.since ? "since " + hm(n.since) : ""].filter(Boolean).join(" · ")])],
      {"data-k": n.key, tabindex: "0", role: "button", title: n.name + ": click for its card"});
  }
  function notUp() {
    var L = notUpList(), nsel = Object.keys(SEL).length;
    P.ncount.textContent = L.length ? L.length + " node" + (L.length === 1 ? "" : "s") : "";
    P.selc.textContent = nsel + " selected";
    P.clr.hidden = !nsel;
    clear(P.evs);
    if (!L.length) {
      var cn = counts();
      P.evs.appendChild(h("p", "wx-empty", [swFresh() !== "ok" ? "No current data - nothing can be said up or down." :
        cn.unk ? "Nothing down or warning in the current read - " + cn.unk + " without current data (grey)." :
        "Every node SolarWinds watches is up, or off on purpose."]));
      P.more.hidden = true;
      return;
    }
    L.slice(0, 40).forEach(function (n) { P.evs.appendChild(row(n)); });
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
  function compare() {
    var c = F.cmp;
    clear(P.cmp);
    P.cmpcue.textContent = "compared at each read · click a line for the list";
    if (!F.vcs.length) { P.cmp.appendChild(h("p", "wx-empty", ["No vCenter on the wall - nothing compared (the VMware area needs the \"VMware vCenter\" credential)."])); return; }
    var line = function (n, cls, t, sub, ref) {
      return h("div", "r" + (n ? " " + cls : "") + (ref ? "" : " flat"), [h("b", "", [String(n)]), h("div", "", [t, h("small", "", [sub])])],
               ref && n ? {"data-cmp": ref, tabindex: "0", role: "button", title: "The list of these nodes"} : null);
    };
    var curVc = F.vcs.some(function (v, i) { return vcFreshI(i) === "ok"; });
    add(P.cmp, [
      line(c.vc_only == null ? "–" : c.vc_only, "", "VMs in vCenter, not monitored in SolarWinds", c.vc_only == null ? "not counted: SolarWinds' read is not whole and current" : "in the grid as \"vCenter only\", with vCenter's status (heartbeat)", "v"),
      line(c.vc_read ? c.not_in_vc || 0 : "–", "warn", "SolarWinds VMs not found in vCenter", c.vc_read ? "decommissioned? still polled · a VM by its Vendor or MachineType (" + F.vmRule + ")" : "not counted: not every vCenter's read is current", "n"),
      line(c.disagree || 0, "crit", "Disagree: SolarWinds down, vCenter running with heartbeat", (c.disagree_names || []).slice(0, 3).join(", ") + (c.disagree ? " - likely the network path, not the VM" : "none"), "g"),
      line(c.off_in_vc || 0, "", "SolarWinds down, powered off in vCenter", "shown as Powered off (grey), never down", "p"),
      c.ambiguous ? line(c.ambiguous, "warn", "Not compared: two VMs of one name", "the IP did not settle which - see each node's card", "a") : null,
      curVc ? null : h("p", "wx-empty", ["vCenter not current: nothing is turned from down to Powered off until it is read again."])]);
  }
  // the Spotlight: one group at a time, problems first; steps every DWELL s, or by ◀ ▶ (a click pauses it for 60 s)
  function spotOrder() {
    var rk = {crit: 0, warn: 1, stale: 2, ok: 3};
    return F.groups.filter(function (g) { return (g.members || g.nodes).length; }).map(function (g) { return {g: g, s: gState(g)}; })
      .sort(function (a, b) { return rk[a.s.st] - rk[b.s.st] || b.s.down - a.s.down || b.s.warn - a.s.warn || a.g.i - b.g.i; }).map(function (x) { return x.g; });
  }
  function spotlight() {
    var order = spotOrder();
    if (!order.length) { P.sph.textContent = "Spotlight"; P.spcue.textContent = ""; clear(P.chips); return; }
    var idx = order.map(function (g) { return g.i; }).indexOf(SPOT.g);
    if (idx < 0) { idx = 0; SPOT.g = order[0].i; }
    var g = order[idx], c = gState(g);
    P.sph.textContent = "Spotlight · " + g.name;
    spotCue(idx, order.length);
    clear(P.chips);
    var ns = (g.members || g.nodes).slice().sort(function (a, b) { return RANK[shown(a)] - RANK[shown(b)] || (a.name < b.name ? -1 : 1); });
    var max = 15, show = ns.length > max ? ns.slice(0, max - 1) : ns;
    show.forEach(function (n) {
      var s = shown(n);
      P.chips.appendChild(h("div", "chip " + s, [h("i"), h("b", "", [n.name]),
        h("span", "", [s === "down" || s === "lastdown" ? "down" : s === "off" ? (n.c === "m" ? "unmanaged" : "off") : n.c === "w" ? (n.loss ? n.loss + "% loss" : n.rt != null ? n.rt + " ms" : "warn") : n.rt != null ? n.rt + " ms" : s === "unk" ? "?" : "up"])],
        {"data-k": n.key, tabindex: "0", role: "button", title: n.name + ": click for its card"}));
    });
    if (ns.length > show.length) P.chips.appendChild(h("div", "chip rest", [h("b", "", ["+" + (ns.length - show.length) + " more" + (c.down + c.warn > show.length ? "" : " up")])]));
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
    add(P.ft, [k("up", "up"), k("warn", "warning (loss or slow)"), k("down", "down"), k("off", "powered off (vCenter) / unmanaged"), k("unk", "no current data"),
      h("span", "rule", ["SolarWinds every " + F.seconds + " s · powered off in vCenter is never \"down\" · grey = no current data, never green"]),
      h("span", "rot rotcue", [A.ROT.on ? "Rotating " + (A.ROT.mode === "styles" ? "styles" : "areas") + " every " + A.ROT.every + " s" : "Rotation off"])]);
  }

  // ---------------------------------------------------------------- the grid (canvas)
  function colours() {
    var cs = {}, pr = P.probe;
    ["--fl-up", "--fl-warn", "--fl-crit", "--fl-grey", "--fl-accent", "--wx-ink", "--wx-mute", "--fl-label-ink", "--wx-panel", "--fl-notch"].forEach(function (t) {
      pr.style.color = ""; pr.style.color = "var(" + t + ")"; cs[t] = getComputedStyle(pr).color;
    });
    var f = getComputedStyle(B);
    cs.sans = f.getPropertyValue("--wx-sans").trim() || "sans-serif"; cs.mono = f.getPropertyValue("--wx-mono").trim() || "monospace";
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
    var cv = P.cv, x = cv.getContext("2d");
    cv.style.width = Wd + "px"; cv.style.height = Hd + "px";
    cv.width = Math.round(Wd * dpr); cv.height = Math.round(Hd * dpr);
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
        x.fillText(g.nodes.length.toLocaleString("en") + " nodes" + (c.down ? " · " + c.down + " down" : "") + (c.warn ? " · " + c.warn + " warn" : ""), x0 + L.colW, y + 14);
        x.textAlign = "left";
        var top = y + 22;
        LAY.blocks.push({g: g, x: x0, y: y, top: top, w: L.colW, per: L.per, rows: b.rows});
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
          if (s === "down" && S >= 5) { x.strokeStyle = C["--fl-crit"]; x.lineWidth = 2; x.beginPath(); x.arc(cx + S / 2, cy + S / 2, Math.max(S, 7), 0, 7); x.stroke(); labels.push({n: n, x: cx, y: cy}); }
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
    if (x.measureText(t).width <= w) return t;
    while (t.length > 1 && x.measureText(t + "…").width > w) t = t.slice(0, -1);
    return t + "…";
  }
  function hit(e) {
    var r = P.cv.getBoundingClientRect(), k = r.width / Math.max(1, P.cv.clientWidth || 1), mx = (e.clientX - r.left) / k, my = (e.clientY - r.top) / k, S = LAY.S;
    for (var i = 0; i < LAY.blocks.length; i++) {
      var b = LAY.blocks[i], gap = Math.max(1, Math.round(S * 0.24));
      if (mx < b.x - 4 || mx > b.x + b.w + 4) continue;
      if (my >= b.y - 4 && my < b.top) return {g: b.g};
      if (my >= b.top && my < b.top + b.rows * (S + gap)) {
        var col = Math.floor((mx - b.x) / (S + gap)), rw = Math.floor((my - b.top) / (S + gap)), idx = rw * b.per + col;
        if (col >= 0 && col < b.per && idx < b.g.nodes.length) return {n: b.g.nodes[idx], mx: mx, my: my};
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
  function openCard(L, kind) {
    L = L.filter(Boolean);
    if (!L.length) return;
    OV = {kind: kind || "card", keys: L.map(function (n) { return n.key; })};
    clear(P.ov);
    var one = L.length === 1 && kind !== "bulk", n = L[0], s = shown(n);
    var x = h("button", "x", ["×"], {type: "button", title: "Close (Esc)", "aria-label": "Close"});
    x.addEventListener("click", closeOv);
    var head = one ? h("h2", "", [h("span", "tag " + sevOf(s), [ST_WORD[n.c]]), h("span", "nm", [n.name]), h("small", "", [svcOf(n)]), x]) :
      h("h2", "", [h("span", "nm", ["Share " + L.length + " node" + (L.length === 1 ? "" : "s") + " to Teams"]),
        h("small", "", [L.filter(function (m) { return m.c === "d"; }).length + " down · " + L.filter(function (m) { return m.c === "w"; }).length + " warning"]), x]);
    var extra = [];
    if (one) {
      var as = h("button", "", [SEL[n.key] ? "Remove from selection" : "Add to selection"], {type: "button", title: "Tick it in the Not up list (to share several at once)"});
      as.addEventListener("click", function () { if (SEL[n.key]) delete SEL[n.key]; else SEL[n.key] = 1; as.textContent = SEL[n.key] ? "Remove from selection" : "Add to selection"; notUp(); drawGrid(); });
      extra.push(as);
    }
    var sh = window.SAWallShare && window.SAWallShare.nodes ? window.SAWallShare.nodes.panel(L.map(share), ctx(one ? [n] : L), extra) :
      h("p", "note warn", ["Share to Teams (wall-share.js) did not load - reload the page."]);
    var g = F.groups[n.g] || {}, also = (n.also || []).map(function (i) { return (F.groups[i] || {}).name; }).filter(Boolean);
    var fr = fresh(n), sr = srcOf(n);
    var body = one ? kv([
      ["Service", (g.kind === "n" ? "no group (Workloads)" : g.name || "-") + (also.length ? " · also in " + also.join(", ") : "")],
      ["IP", n.ip || "none known", "mono"],
      ["Status", (s === "up" ? "up" : ST_WORD[n.c].toLowerCase()) + (n.why ? " · " + n.why : "") + (fr === "stale" ? " · NOT CURRENT (last known)" : fr === "late" ? " · LATE" : ""), sevOf(s)],
      ["Since", n.since ? hm(n.since) + (n.exact ? "" : " or earlier") + " (" + A.dur(NOW() - n.since) + (n.exact ? "" : "+") + ")" : n.c === "u" ? "-" : "not known yet (seen at the first read)"],
      n.vconly ? null : ["Response / loss", (n.c === "d" ? "no reply" : n.rt != null ? n.rt + " ms" : "-") + " · " + (n.loss != null ? n.loss + "% loss" : "loss not read"), "mono"],
      ["vCenter", n.vm ? n.vm + (n.host ? " · host " + n.host : "") + (n.vc ? " · " + n.vc : "") + (n.flags.indexOf("s") >= 0 ? " · not current - not compared" : "") :
        n.flags.indexOf("a") >= 0 ? "more than one VM of this name - not compared (the IP did not settle it)" : n.flags.indexOf("n") >= 0 ? "a VM by SolarWinds' Vendor / MachineType, not found in vCenter" : "not a VM vCenter has (or no vCenter on the wall)"],
      n.vconly ? null : ["SolarWinds says", (n.said || "-") + (n.model ? " · " + n.model : "") + (n.polled != null ? " · polled " + A.ago(n.polled).replace(" ago", "") + " before the read" : "")],
      ["Source", sr.name + " · " + (sr.asof != null ? "as of " + A.hms(sr.asof * 1000) + " (" + agoS(sr.asof) + ")" : "never read") + (fr === "ok" ? "" : fr === "late" ? " · LATE" : " · NOT CURRENT")],
      ["Check", n.vconly ? "vSphere Client of " + n.vc + " › VM " + n.name + " › Summary" : "SolarWinds › Node Details of " + n.name + (n.vm ? "; vSphere Client › VM › Summary" : "")]]) : null;
    var card = h("div", "fl-card" + (one ? "" : " bulk"), [head, body ? h("div", "fl-cb", [body]) : null, sh], {role: "dialog", "aria-modal": "true", "aria-label": one ? n.name : "Share to Teams"});
    P.ov.appendChild(card);
    P.ov.classList.add("on");
    if (sh.__focus) sh.__focus.focus();
    spotlight();
  }
  // the full list (from "+N more") and the comparison's lists: rows with checkboxes, Share these
  function openList(title, L, note) {
    OV = {kind: "list", keys: []};
    clear(P.ov);
    var x = h("button", "x", ["×"], {type: "button", title: "Close (Esc)", "aria-label": "Close"});
    x.addEventListener("click", closeOv);
    var shareB = h("button", "btn pri", ["Share these to Teams"], {type: "button"});
    var selB = h("button", "btn", ["Tick all of these"], {type: "button"});
    var box = h("div", "fl-evs all");
    L.forEach(function (n) { box.appendChild(row(n)); });               // every one reachable (a long list scrolls)
    shareB.addEventListener("click", function () { var sel = L.filter(function (n) { return SEL[n.key]; }); openCard(sel.length ? sel : L, "bulk"); });
    selB.addEventListener("click", function () { L.forEach(function (n) { SEL[n.key] = 1; }); box.querySelectorAll("input[type=checkbox]").forEach(function (c) { c.checked = true; }); notUp(); drawGrid(); });
    P.ov.appendChild(h("div", "fl-card list", [h("h2", "", [h("span", "nm", [title]), h("small", "", [L.length + " node" + (L.length === 1 ? "" : "s")]), x]),
      note ? h("p", "fl-note", [note]) : null, h("div", "evh", [selB, shareB, h("span", "fl-click", ["click a row for its card · tick to share several"])]), box], {role: "dialog", "aria-modal": "true", "aria-label": title}));
    P.ov.classList.add("on");
    x.focus();
    spotlight();
  }
  function selected() { return Object.keys(SEL).map(function (k) { return F.by[k]; }).filter(Boolean).sort(function (a, b) { return RANK[shown(a)] - RANK[shown(b)] || (a.name < b.name ? -1 : 1); }); }

  // ---------------------------------------------------------------- events
  function wire() {
    B.addEventListener("click", function (e) {
      if (!F) return;
      var t = e.target;
      if (t.closest(".fl-card") && t.closest("button") && !t.closest(".fl-evs")) return;    // the card's own buttons
      var cb = t.closest("input[type=checkbox][data-k]");
      if (cb) { if (cb.checked) SEL[cb.getAttribute("data-k")] = 1; else delete SEL[cb.getAttribute("data-k")]; notUp(); drawGrid(); e.stopPropagation(); return; }
      var k = t.closest("[data-k]");
      if (k && F.by[k.getAttribute("data-k")]) { openCard([F.by[k.getAttribute("data-k")]]); return; }
      var g = t.closest("[data-g]");
      if (g) { SPOT.g = +g.getAttribute("data-g"); SPOT.left = DWELL; SPOT.pausedUntil = Date.now() + 60000; services(); spotlight(); drawGrid(); return; }
      var cm = t.closest("[data-cmp]");
      if (cm) {
        var w = cm.getAttribute("data-cmp"), L = F.nodes.filter(function (n) { return w === "v" ? n.vconly : w === "p" ? n.c === "p" && n.why.indexOf("SolarWinds: down") >= 0 : n.flags.indexOf(w) >= 0; });
        openList({v: "VMs in vCenter, not in SolarWinds", n: "SolarWinds VMs not found in vCenter", g: "Disagree: SolarWinds down, vCenter running", p: "SolarWinds down, powered off in vCenter", a: "Not compared: two VMs of one name"}[w], L,
                 w === "n" ? "A node is a VM by its Vendor or MachineType (" + F.vmRule + "); not found by name (any case, short name or FQDN) in any vCenter." : "");
        return;
      }
      if (t === P.selall) { notUpList().forEach(function (n) { if (n.c === "d") SEL[n.key] = 1; }); notUp(); drawGrid(); return; }
      if (t === P.clr) { SEL = {}; notUp(); drawGrid(); return; }
      if (t === P.shsel) { var sel = selected(); openCard(sel.length ? sel : notUpList().filter(function (n) { return n.c === "d"; }), "bulk"); return; }
      if (t === P.more) { openList("Not up · worst first", notUpList()); return; }
      if (t === P.prev || t === P.next) { step(t === P.next ? 1 : -1, true); return; }
      if (t === P.ov) closeOv();
    });
    B.addEventListener("keydown", function (e) {           // a source's chip ([data-d]) is the page's own: it opens the source's card
      if ((e.key === "Enter" || e.key === " ") && e.target.matches("[role=button]") && !e.target.closest("button,input,[data-d]")) { e.preventDefault(); e.target.click(); }
    });
    P.cv.addEventListener("mousemove", function (e) {
      var r = hit(e);
      if (!r.n) { P.tip.classList.remove("on"); P.cv.style.cursor = r.g ? "pointer" : "default"; return; }
      P.cv.style.cursor = "pointer";
      var n = r.n, s = shown(n);
      clear(P.tip);
      add(P.tip, [h("b", "", [n.name]), " · " + svcOf(n), h("br"), (n.ip || "no IP") + " · " + phrase(n), n.vm ? [h("br"), "vCenter: " + n.vm] : null,
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
      if (!A || A.S.area !== "fleet" || !B || !B.isConnected) return;
      if (e.key === "Escape" && OV) { e.stopImmediatePropagation(); e.preventDefault(); closeOv(); return; }
      if (OV || (e.target.closest && e.target.closest("select,input,textarea"))) return;
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") { e.preventDefault(); step(e.key === "ArrowRight" ? 1 : -1, true); }
    }, true);
  }
  // the test harness (data-test) only: ?fleet=one | bulk opens a card at once (the screenshots); ?node=<key> on any wall: its card
  var OPENED = false;
  function firstOpen() {
    if (OPENED || !F) return;
    OPENED = true;
    var q = A.qs, k = q.get("node");
    if (k && F.by[k]) { openCard([F.by[k]]); return; }
    if (!A.test) return;
    var downs = notUpList().filter(function (n) { return n.c === "d"; });
    if (q.get("fleet") === "one" && downs.length) openCard([downs[0]]);
    if (q.get("fleet") === "bulk" && downs.length) { downs.forEach(function (n) { SEL[n.key] = 1; }); notUp(); drawGrid(); openCard(selected(), "bulk"); }
    if (q.get("fleet") === "list") openList("Not up · worst first", notUpList());
  }

  // ---------------------------------------------------------------- the clock: the Spotlight's dwell, a read every 30 s, the hold while a card is open
  var tick = 0;
  setInterval(function () {
    if (!A || !B || !B.isConnected || A.S.area !== "fleet" || document.hidden) return;
    tick++;
    if (OV && A.ROT.on) A.ROT.resumeAt = Date.now() + A.idleResume * 1000;     // a card open holds the rotation
    if (!frozen() && tick % 30 === 0) load(false);
    if (!F || frozen()) return;
    P.clock.firstChild.textContent = A.hms(NOW());
    ages();
    if (!OV && SPOT.pausedUntil <= Date.now()) {
      SPOT.left--;
      if (SPOT.left <= 0) { step(1, false); return; }
    }
    var order = spotOrder();
    spotCue(Math.max(0, order.map(function (g) { return g.i; }).indexOf(SPOT.g)), order.length);
  }, 1000);

  window.SAWallFleet = {render: render, fitted: fitted, _adapt: adapt};
})();
