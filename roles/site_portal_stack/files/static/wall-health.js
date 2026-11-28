/* 1.138.0: Network & AD health area of the ops wall (/portal/wall?area=health) + 1920 Board mode.
   Matches mock/health-wall.html in all 10 portal looks, light & dark, 800 / 1280 / 1920 px.
   Live data only from /portal/api/wall/health; missing/stale feeds always grey 'not read', never green.
   Strict CSP: pure DOM/SVG creation only (no HTML string injection or inline style attributes). */
(function () {
  "use strict";
  var SVG_NS = "http://www.w3.org/2000/svg";
  var ICON = "M3 12h4l2.5-6 5 12 2.5-6H21";
  var ST = {
    data: null, etag: "", paused: false, board: false,
    domain: "all", bucket: "all", query: "", trend: "rtt",
    spotIdx: 0, spotPaused: false, spotTick: 0,
    pollLeft: 60, flipIdx: 0, timer: null, stage: null, api: null,
    src: "/portal/api/wall/health", loading: false, failMsg: "", drawer: null,
    sel: Object.create(null), lastSelKey: ""
  };

  function h(tag, cls, kids, attrs) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (attrs) Object.keys(attrs).forEach(function (k) { if (attrs[k] !== null && attrs[k] !== undefined) e.setAttribute(k, String(attrs[k])); });
    (kids || []).forEach(function (c) {
      if (c === null || c === undefined || c === false) return;
      e.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
    });
    return e;
  }
  function sv(tag, attrs, kids) {
    var e = document.createElementNS(SVG_NS, tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { e.setAttribute(k, String(attrs[k])); });
    (kids || []).forEach(function (c) { if (c) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return e;
  }
  function clear(el) { while (el && el.firstChild) el.removeChild(el.firstChild); }

  function fmtAge(sec) {
    if (sec === null || sec === undefined || isNaN(sec)) return "not read";
    sec = Math.max(0, Math.floor(sec));
    if (sec < 60) return sec + "s ago";
    if (sec < 3600) return Math.floor(sec / 60) + "m ago";
    if (sec < 86400) return Math.floor(sec / 3600) + "h " + Math.floor((sec % 3600) / 60) + "m ago";
    return Math.floor(sec / 86400) + "d ago";
  }
  function fmtDur(start, now) {
    if (!start) return "—";
    var s = Math.max(0, Math.floor((now || Date.now() / 1000) - start));
    if (s < 60) return s + "s";
    if (s < 3600) return Math.floor(s / 60) + "m";
    return Math.floor(s / 3600) + "h " + Math.floor((s % 3600) / 60) + "m";
  }
  function fmtTime(ts) {
    if (!ts) return "not read";
    try { return new Date(ts * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"}); }
    catch (e) { return String(Math.round(ts)); }
  }
  function sevWord(s) { return s === "crit" ? "CRITICAL" : s === "warn" ? "WARNING" : s === "ok" ? "OK" : "NOT CURRENT"; }
  function bucketTitle(b) { return b === "down" ? "Down / unreachable" : b === "full" ? "Full / near capacity" : "Sustained errors"; }

  function copyText(txt, msgEl) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(txt).then(function () {
        if (msgEl) msgEl.textContent = "Copied to clipboard - press Ctrl+V in Teams.";
      }, function () {
        if (msgEl) msgEl.textContent = "Clipboard blocked by browser - select and copy manually.";
      });
    } else if (msgEl) {
      msgEl.textContent = "Clipboard API unavailable in this browser.";
    }
  }

  function fetchHealth(force) {
    if (ST.loading) return;
    // 1.138.0: exit invalidates this request, including across a quick re-entry.
    var request = {};
    ST.loading = request;
    var etag = "";
    var url = ST.src + (force ? "?poll=now" : "");
    var hd = {Accept: "application/json"};
    if (ST.etag && !force) hd["If-None-Match"] = ST.etag;
    fetch(url, {headers: hd, credentials: "same-origin", cache: "no-store"}).then(function (res) {
      if (res.status === 304) return null;
      if (!res.ok) throw new Error("HTTP " + res.status);
      etag = (res.headers && res.headers.get && res.headers.get("ETag")) || "";
      return res.json();
    }).then(function (doc) {
      if (ST.loading !== request) return;
      ST.loading = false;
      ST.failMsg = "";
      if (doc) {
        ST.etag = etag;
        ST.data = doc;
        ST.pollLeft = Math.max(15, Number(doc.poll) || 60);
      }
      draw();
    }).catch(function (err) {
      if (ST.loading !== request) return;
      ST.loading = false;
      ST.data = null;
      ST.etag = "";
      ST.failMsg = "Health wall feed unreachable: " + (err && err.message ? err.message : "network error");
      draw();
    });
  }

  function ensureTimer() {
    if (ST.timer) return;
    ST.timer = setInterval(function () {
      if (!ST.stage || !document.body.contains(ST.stage)) return;
      if (!ST.paused) {
        ST.pollLeft--;
        if (ST.pollLeft <= 0) {
          ST.pollLeft = Math.max(15, Number(ST.data && ST.data.poll) || 60);
          fetchHealth(false);
        }
        var cd = ST.stage.querySelector(".wh-cd");
        if (cd) cd.textContent = "Next poll in " + Math.max(0, ST.pollLeft) + "s";
      }
      // Spotlight dwell (10s) + Metro-Atlas / Metro subtle live motion
      if (!ST.spotPaused && ST.data && (ST.data.conds || []).length > 1) {
        ST.spotTick++;
        if (ST.spotTick >= 10) {
          ST.spotTick = 0;
          ST.spotIdx = (ST.spotIdx + 1) % ST.data.conds.length;
          ST.flipIdx = (ST.flipIdx + 1) % 4;
          draw();
        } else {
          var bar = ST.stage.querySelector(".wh-spot .dwell i");
          if (bar) bar.style.width = (ST.spotTick * 10) + "%";
        }
      }
    }, 1000);
  }

  function sparkSvg(vals, state) {
    var pts = (vals || []).filter(function (v) { return typeof v === "number" && isFinite(v); });
    var w = 180, hh = 30;
    var s = sv("svg", {class: "wh-kpi-svg spk", viewBox: "0 0 " + w + " " + hh, preserveAspectRatio: "none", "aria-hidden": "true"});
    if (pts.length < 2) {
      s.appendChild(sv("line", {x1: 0, y1: hh / 2, x2: w, y2: hh / 2, stroke: "var(--edge)", "stroke-dasharray": "4 4", "stroke-width": 1.2}));
      return s;
    }
    var mn = Math.min.apply(null, pts), mx = Math.max.apply(null, pts);
    if (mx === mn) { mx = mn + 1; }
    var d = pts.map(function (v, i) {
      var x = (i / (pts.length - 1)) * w;
      var y = hh - 3 - ((v - mn) / (mx - mn)) * (hh - 6);
      return (i === 0 ? "M" : "L") + x.toFixed(1) + " " + y.toFixed(1);
    }).join(" ");
    var col = state === "crit" ? "var(--critical)" : state === "warn" ? "var(--warning)" : state === "ok" ? "var(--ok)" : "var(--unknown)";
    s.appendChild(sv("path", {d: d, fill: "none", stroke: col, "stroke-width": 1.8, "stroke-linejoin": "round", "stroke-linecap": "round"}));
    return s;
  }

  function trendChart(doc) {
    var metrics = {
      rtt: {label: "WAN RTT", key: "rtt", unit: "ms", lim: (doc.limits && doc.limits.wan_rtt) || [80, 150]},
      loss: {label: "WAN Loss", key: "loss", unit: "%", lim: (doc.limits && doc.limits.wan_loss) || [1, 3]},
      ad: {label: "AD Max Lag", key: "ad_lag_min", unit: "m", lim: [((doc.limits && doc.limits.ad_repl_hours) || [6, 24])[0] * 60, ((doc.limits && doc.limits.ad_repl_hours) || [6, 24])[1] * 60]},
      vol: {label: "Fullest Vol", key: "vol_pct", unit: "%", lim: (doc.limits && doc.limits.volume) || [85, 95]}
    };
    var cfg = metrics[ST.trend] || metrics.rtt;
    var rows = (doc.trends || []).map(function (p) { return {t: p.t, v: p[cfg.key]}; }).filter(function (p) { return typeof p.v === "number" && isFinite(p.v); });
    var box = h("div", "wh-chart-box");
    var tip = h("div", "wh-tip");
    if (!rows.length) {
      box.appendChild(h("div", "wh-empty", ["No live " + cfg.label + " samples yet (grey / not read until SolarWinds or AD DB answers)."]));
      return box;
    }
    var W = 520, H = 150, pl = 40, pr = 12, pt = 12, pb = 22;
    var cw = W - pl - pr, ch = H - pt - pb;
    var vals = rows.map(function (r) { return r.v; });
    var maxY = Math.max(cfg.lim[1] * 1.15, Math.max.apply(null, vals) * 1.1, 1);
    var yOf = function (v) { return pt + ch - Math.min(1, Math.max(0, v / maxY)) * ch; };
    var xOf = function (i) { return rows.length === 1 ? pl + cw / 2 : pl + (i / (rows.length - 1)) * cw; };

    var s = sv("svg", {class: "wh-chart-svg", viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": cfg.label + " 4 hour trend"});
    // Warn & Crit bands
    var yCrit = yOf(cfg.lim[1]), yWarn = yOf(cfg.lim[0]);
    s.appendChild(sv("rect", {x: pl, y: pt, width: cw, height: Math.max(0, yCrit - pt), fill: "var(--critical-bg)"}));
    s.appendChild(sv("rect", {x: pl, y: yCrit, width: cw, height: Math.max(0, yWarn - yCrit), fill: "var(--warning-bg)"}));
    // Threshold dashed lines
    s.appendChild(sv("line", {x1: pl, y1: yCrit, x2: W - pr, y2: yCrit, stroke: "var(--critical)", "stroke-dasharray": "4 3", "stroke-width": 1}));
    s.appendChild(sv("line", {x1: pl, y1: yWarn, x2: W - pr, y2: yWarn, stroke: "var(--warning)", "stroke-dasharray": "4 3", "stroke-width": 1}));
    s.appendChild(sv("text", {x: pl - 4, y: yCrit + 3, "text-anchor": "end", fill: "var(--critical)", "font-size": 9.5, "font-family": "var(--mono)"}, [String(Math.round(cfg.lim[1]))]));
    s.appendChild(sv("text", {x: pl - 4, y: yWarn + 3, "text-anchor": "end", fill: "var(--warning)", "font-size": 9.5, "font-family": "var(--mono)"}, [String(Math.round(cfg.lim[0]))]));
    s.appendChild(sv("line", {x1: pl, y1: pt + ch, x2: W - pr, y2: pt + ch, stroke: "var(--edge)", "stroke-width": 1}));

    var pathD = rows.map(function (r, i) { return (i === 0 ? "M" : "L") + xOf(i).toFixed(1) + " " + yOf(r.v).toFixed(1); }).join(" ");
    s.appendChild(sv("path", {d: pathD, fill: "none", stroke: "var(--accent)", "stroke-width": 2, "stroke-linejoin": "round"}));

    rows.forEach(function (r, i) {
      var cx = xOf(i), cy = yOf(r.v);
      var col = r.v >= cfg.lim[1] ? "var(--critical)" : r.v >= cfg.lim[0] ? "var(--warning)" : "var(--accent)";
      var dot = sv("circle", {cx: cx.toFixed(1), cy: cy.toFixed(1), r: rows.length < 20 ? 3.2 : 2.2, fill: col});
      dot.addEventListener("mouseenter", function () {
        tip.textContent = fmtTime(r.t) + " · " + r.v + " " + cfg.unit + " (warn " + cfg.lim[0] + " / crit " + cfg.lim[1] + ")";
        tip.classList.add("on");
        tip.style.left = Math.min(72, Math.max(4, (cx / W) * 100)) + "%";
        tip.style.top = Math.max(0, (cy / H) * 100 - 18) + "%";
      });
      dot.addEventListener("mouseleave", function () { tip.classList.remove("on"); });
      s.appendChild(dot);
    });
    // X-axis endpoints
    s.appendChild(sv("text", {x: pl, y: H - 5, fill: "var(--muted)", "font-size": 9.5, "font-family": "var(--mono)"}, [fmtTime(rows[0].t)]));
    s.appendChild(sv("text", {x: W - pr, y: H - 5, "text-anchor": "end", fill: "var(--muted)", "font-size": 9.5, "font-family": "var(--mono)"}, [fmtTime(rows[rows.length - 1].t)]));
    box.appendChild(s);
    box.appendChild(tip);
    return box;
  }

  function openDrawer(kind, item) {
    ST.drawer = {kind: kind, item: item};
    renderDrawer();
  }
  function closeDrawer() {
    ST.drawer = null;
    var back = document.getElementById("wh-back");
    if (back) back.classList.remove("on");
  }

  function renderDrawer() {
    var back = document.getElementById("wh-back");
    if (!back) {
      back = h("div", "wh-back", [], {id: "wh-back", role: "dialog", "aria-modal": "true"});
      back.addEventListener("click", function (ev) { if (ev.target === back) closeDrawer(); });
      document.body.appendChild(back);
    }
    clear(back);
    if (!ST.drawer) { back.classList.remove("on"); return; }
    back.classList.add("on");
    var k = ST.drawer.kind, it = ST.drawer.item, doc = ST.data || {};
    var drw = h("div", "wh-drw");
    var closeBtn = h("button", "wh-drw-x", ["×"], {type: "button", "aria-label": "Close drawer"});
    closeBtn.addEventListener("click", closeDrawer);
    var head = h("div", "wh-drw-h", [h("h3", "", [
      k === "cond" ? (it.obj + " · " + it.title)
        : k === "pair" ? ("AD Replication: " + it.src + " → " + it.dst)
        : k === "feed" ? ("Feed source: " + it.name)
        : k === "node" ? ("Node: " + it.name)
        : k === "sup" ? ("Suppressed conditions (" + (doc.suppressed || []).length + ")")
        : "Wall rules & limits in force"
    ]), closeBtn]);
    drw.appendChild(head);

    var kv = h("dl", "wh-kv");
    function addKV(label, val) { kv.appendChild(h("dt", "", [label])); kv.appendChild(h("dd", "", [String(val)])); }
    var teamsText = "";

    if (k === "cond") {
      addKV("Severity", it.last_known ? "LAST KNOWN (stale feed)" : sevWord(it.sev));
      addKV("Bucket", bucketTitle(it.bucket));
      addKV("Domain", it.domain === "ad" ? "Active Directory" : "Network / SolarWinds");
      addKV("Site", it.site || "—");
      addKV("Value", it.val_text || (it.val !== null && it.val !== undefined ? (it.val + " " + (it.unit || "")) : "—"));
      addKV("Limits", "Warn " + it.warn + " / Crit " + it.crit + " " + (it.unit || ""));
      addKV("Open for", fmtDur(it.start, doc.now) + " (since " + fmtTime(it.start) + ")");
      addKV("Detail", it.detail || "—");
      addKV("Where to check", it.check || "—");
      drw.appendChild(kv);
      teamsText = "[" + sevWord(it.sev) + "] " + it.obj + " — " + it.title + "\nSite: " + (it.site || "—") +
        " | Value: " + (it.val_text || it.val) + " | Open: " + fmtDur(it.start, doc.now) +
        "\nCheck: " + (it.check || "Ops wall") + "\nWall: " + location.href;
    } else if (k === "pair") {
      addKV("Status", sevWord(it.status));
      addKV("Sites", (it.src_site || "—") + " → " + (it.dst_site || "—"));
      addKV("Max lag", it.lag_min !== null && it.lag_min !== undefined ? (it.lag_min + " min") : "not read");
      addKV("Failures", String(it.failures || 0));
      addKV("Error", it.error || "—");
      drw.appendChild(kv);
      if (it.partitions && it.partitions.length) {
        var tbl = h("table", "wh-drw-tbl", [
          h("thead", "", [h("tr", "", [h("th", "", ["Partition"]), h("th", "", ["State"]), h("th", "", ["Lag"]), h("th", "", ["Fails"])])]),
          h("tbody", "", it.partitions.map(function (p) {
            return h("tr", "", [h("td", "", [p.nc]), h("td", "", [sevWord(p.status)]),
              h("td", "", [p.lag_min !== null ? (p.lag_min + "m") : "—"]), h("td", "", [String(p.failures || 0)])]);
          }))
        ]);
        drw.appendChild(tbl);
      }
      drw.appendChild(h("pre", "wh-cmd", ["repadmin /showrepl " + it.dst + " /verbose"]));
      teamsText = "[AD REPL " + sevWord(it.status) + "] " + it.src + " -> " + it.dst + " | Lag: " +
        (it.lag_min !== null ? it.lag_min + "m" : "not read") + " | Failures: " + (it.failures || 0);
    } else if (k === "feed") {
      addKV("State", it.fresh === "ok" ? "CURRENT" : it.fresh === "late" ? "LATE" : "NOT CURRENT / NOT READ");
      addKV("Last read", it.asof ? (fmtTime(it.asof) + " (" + fmtAge(it.age_s) + ")") : "never");
      addKV("Late / Stale", Math.round((it.late || 180) / 60) + "m / " + Math.round((it.stale || 600) / 60) + "m");
      addKV("Reason", it.why || "Reading normally");
      drw.appendChild(kv);
    } else if (k === "node") {
      addKV("Status", String(it.status || "stale").toUpperCase());
      addKV("IP", it.ip || "—");
      addKV("Site", it.site || "—");
      addKV("RTT / Loss", (it.rtt !== null && it.rtt !== undefined ? it.rtt + " ms" : "—") + " / " + (it.loss !== null && it.loss !== undefined ? it.loss + "%" : "—"));
      addKV("SolarWinds", it.why || "—");
      drw.appendChild(kv);
      teamsText = "[NODE " + String(it.status || "stale").toUpperCase() + "] " + it.name + " (" + (it.ip || "no IP") + ") · Site: " + (it.site || "—");
    } else if (k === "sup") {
      var list = doc.suppressed || [];
      if (!list.length) {
        drw.appendChild(h("div", "wh-empty", ["No suppressed conditions on the last poll."]));
      } else {
        drw.appendChild(h("table", "wh-drw-tbl", [
          h("thead", "", [h("tr", "", [h("th", "", ["Domain"]), h("th", "", ["Object"]), h("th", "", ["Why kept off the wall"])])]),
          h("tbody", "", list.map(function (s) {
            return h("tr", "", [h("td", "", [s.domain === "ad" ? "AD" : "Net"]), h("td", "", [s.obj]), h("td", "", [s.why])]);
          }))
        ]));
      }
    } else {
      var lim = doc.limits || {};
      addKV("Down confirmation", (lim.down_polls || 2) + " consecutive polls (or 2 AD checks)");
      addKV("Sustained window", (lim.sustained_minutes || 15) + " min (blips suppressed)");
      addKV("Volume full", "Warn " + ((lim.volume || [85, 95])[0]) + "% · Crit " + ((lim.volume || [85, 95])[1]) + "%");
      addKV("WAN utilization", "Warn " + ((lim.wan_util || [75, 90])[0]) + "% · Crit " + ((lim.wan_util || [75, 90])[1]) + "%");
      addKV("WAN RTT / Loss", "RTT " + ((lim.wan_rtt || [80, 150]).join("/")) + " ms · Loss " + ((lim.wan_loss || [1, 3]).join("/")) + "%");
      addKV("Interface errors", "Warn " + ((lim.if_err || [1, 2.5])[0]) + "% · Crit " + ((lim.if_err || [1, 2.5])[1]) + "%");
      addKV("AD replication lag", "Warn " + ((lim.ad_repl_hours || [6, 24])[0]) + "h · Crit " + ((lim.ad_repl_hours || [6, 24])[1]) + "h");
      addKV("Freshness rule", "Missing, failed, or stale feeds always show grey 'not read' / 'Not current', never green.");
      drw.appendChild(kv);
    }

    var acts = h("div", "wh-drw-acts");
    var msg = h("span", "wh-msg");
    if (teamsText) {
      var cp = h("button", "wh-btn pri", ["Copy Teams summary"], {type: "button"});
      cp.addEventListener("click", function () { copyText(teamsText, msg); });
      acts.appendChild(cp);
    }
    var cl = h("button", "wh-btn", ["Close"], {type: "button"});
    cl.addEventListener("click", closeDrawer);
    acts.appendChild(cl);
    acts.appendChild(msg);
    drw.appendChild(acts);
    back.appendChild(drw);
  }

  // 1.140.0: shared Copy controls (per server/condition row + Copy all shown) reusing window.SAPortalCopy
  function portalCopy() {
    var c = window.SAPortalCopy || (window.SAWallFleet && window.SAWallFleet.copy);
    return c && typeof c.createSplitControl === "function" ? c : null;
  }
  function condToServer(c) {
    var sev = c.last_known ? "unknown" : (c.sev === "crit" ? "critical" : c.sev === "warn" ? "warning" : (c.sev || "unknown"));
    var stLab = c.last_known ? "Not reached" : sevWord(c.sev);
    var ttl = (c.title || "Condition") + (c.val_text ? " (" + c.val_text + ")" : "");
    var fix = c.check ? ("Check " + c.check) : "Inspect on Network & AD health wall";
    return {
      host: c.obj || "Host", ip: "", sev: sev, statusLabel: stLab, worst: ttl, summary: ttl,
      findings: [{sev: sev, sevLabel: stLab, title: ttl, evidence: c.detail || "", whatToDo: fix}],
      totalFindings: 1, cappedCount: 0
    };
  }
  function healthCopyCtx(doc) {
    var d = doc && doc.now ? new Date(doc.now * 1000).toISOString().slice(0, 16).replace("T", " ") + " UTC" : "";
    return {site: (doc && doc.enclave) || "", report: "Network & AD health", dateTime: d, url: typeof location !== "undefined" ? location.href : ""};
  }

  function condKey(c) {
    return String(c.id || ((c.domain || "") + "|" + (c.bucket || "") + "|" + (c.obj || "") + "|" + (c.title || "")));
  }
  function buildConditionRow(c, now, doc, bar, selCtx) {
    var cls = "wh-row " + (c.last_known ? "stale" : c.sev);
    var row = h("button", cls, [], {type: "button"});
    row.addEventListener("click", function () { openDrawer("cond", c); });
    row.appendChild(h("span", "wh-sev " + (c.last_known ? "stale" : c.sev), [c.last_known ? "LAST" : c.sev === "crit" ? "CRIT" : "WARN"]));
    row.appendChild(h("div", "wh-rmain", [
      h("div", "ob", [c.obj || ""]),
      h("div", "ti", [c.title || ""]),
      h("div", "mt", [(c.domain === "ad" ? "AD" : "NET") + (c.site ? " · " + c.site : "") + (c.detail ? " · " + c.detail : "")])
    ]));

    // Inline limit bar
    var olc = h("div", "wh-olc");
    olc.appendChild(h("div", "vt", [c.val_text || (c.val !== null && c.val !== undefined ? (c.val + (c.unit || "")) : "—")]));
    var tr = h("div", "tr");
    var maxV = Math.max(Number(c.crit) || 100, Number(c.val) || 0, 1) * 1.1;
    var fl = h("div", "fl");
    fl.style.width = Math.min(100, Math.max(4, ((Number(c.val) || 0) / maxV) * 100)).toFixed(1) + "%";
    tr.appendChild(fl);
    if (c.warn) {
      var mw = h("i", "mk"); mw.style.left = Math.min(98, (Number(c.warn) / maxV) * 100).toFixed(1) + "%";
      tr.appendChild(mw);
    }
    if (c.crit) {
      var mc = h("i", "mk"); mc.style.left = Math.min(98, (Number(c.crit) / maxV) * 100).toFixed(1) + "%";
      tr.appendChild(mc);
    }
    olc.appendChild(tr);
    row.appendChild(olc);
    row.appendChild(h("span", "wh-age", [fmtDur(c.start, now)]));
    var pc = portalCopy();
    if (!pc) return row;
    var key = condKey(c);
    var cb = pc.createSelBox ? pc.createSelBox(c.obj || "condition") : h("input", "srv-sel-cb", [], {type: "checkbox", "aria-label": "Select " + (c.obj || "condition")});
    cb.checked = !!ST.sel[key];
    cb.setAttribute("data-sel-key", key);
    var sp = pc.createSplitControl({
      cls: "sm wh-row-copy", scope: "server", baseLabel: "Copy",
      title: "Copy " + (c.obj || "item") + " status and findings (text + picture)",
      ariaLabel: "Copy " + (c.obj || "item"), toggleLabel: "Copy options for " + (c.obj || "item"),
      menuTitle: String(c.obj || "Host").split(".")[0], menuTag: "Server row",
      subtitles: {both: "Text + inline PNG", text: "Status + findings", picture: "PNG server card"},
      onCopy: function (mode, btn, finish) {
        var srv = condToServer(c), cx = healthCopyCtx(doc);
        var fmt = pc.formatOneServer(srv, cx), pic = mode === "text" ? {dataUrl: ""} : pc.drawOneServerCard(srv, cx);
        return pc.copyMode(mode, {
          plain: fmt.plain, html: fmt.html, dataUrl: pic.dataUrl, width: 700, alt: srv.host + " status card", focusEl: btn,
          onState: function (st, lab, msg) { pc.setFeedback(btn, bar, st, lab, msg); },
          onDone: finish
        });
      }
    });
    var wrap = h("div", "wh-row-wrap" + (cb.checked ? " srv-selected" : ""), [cb, row, sp]);
    var syncOne = function (e) {
      if (e && e.stopPropagation) e.stopPropagation();
      if (e && e.shiftKey && selCtx && ST.lastSelKey && ST.lastSelKey !== key) {
        var ks = selCtx.shownKeys(), a = ks.indexOf(ST.lastSelKey), b = ks.indexOf(key);
        if (a >= 0 && b >= 0) {
          var lo = Math.min(a, b), hi = Math.max(a, b), on = !!cb.checked;
          for (var i = lo; i <= hi; i++) { if (on) ST.sel[ks[i]] = 1; else delete ST.sel[ks[i]]; }
          selCtx.syncDom();
        }
      }
      if (cb.checked) ST.sel[key] = 1; else delete ST.sel[key];
      ST.lastSelKey = key;
      wrap.classList.toggle("srv-selected", !!cb.checked);
      if (selCtx) selCtx.sync();
    };
    cb.addEventListener("click", syncOne);
    cb.addEventListener("change", function () {
      if (cb.checked) ST.sel[key] = 1; else delete ST.sel[key];
      wrap.classList.toggle("srv-selected", !!cb.checked);
      if (selCtx) selCtx.sync();
    });
    return wrap;
  }

  function draw() {
    if (!ST.stage) return;
    document.documentElement.classList.toggle("wh-board", ST.board);
    clear(ST.stage);
    var wrap = h("div", "wh-wrap");
    if (!ST.data) {
      wrap.appendChild(h("div", "wh-ban stale", [
        h("span", "tag", ["NOT READ"]),
        h("b", "", [ST.failMsg || "Reading live Network & AD health feeds…"])
      ]));
      ST.stage.appendChild(wrap);
      return;
    }
    var doc = ST.data;
    var now = doc.now || (Date.now() / 1000);

    // 1. Control & freshness strip
    var ctrl = h("div", "wh-ctrl");
    ctrl.appendChild(h("div", "wh-ttl", [
      h("h1", "", ["Network & AD health"]),
      doc.enclave ? h("span", "enc", [doc.enclave]) : null
    ]));
    var feedsEl = h("div", "wh-feeds");
    (doc.feeds || []).forEach(function (f) {
      var fp = h("button", "wh-fp " + (f.fresh || "not_read"), [
        h("i"),
        h("span", "", [f.name + ": " + (f.fresh === "ok" ? fmtAge(f.age_s) : f.fresh === "late" ? ("LATE · " + fmtAge(f.age_s)) : "not read")])
      ], {type: "button", title: f.why || f.name});
      fp.addEventListener("click", function () { openDrawer("feed", f); });
      feedsEl.appendChild(fp);
    });
    ctrl.appendChild(feedsEl);

    var acts = h("div", "wh-acts");
    acts.appendChild(h("span", "wh-cd", [ST.paused ? "Paused" : ("Next poll in " + Math.max(0, ST.pollLeft) + "s")]));
    var bPause = h("button", "wh-btn", [ST.paused ? "Resume" : "Pause"], {type: "button", "aria-pressed": ST.paused ? "true" : "false"});
    bPause.addEventListener("click", function () { ST.paused = !ST.paused; draw(); });
    var bPoll = h("button", "wh-btn", ["Poll now"], {type: "button"});
    bPoll.addEventListener("click", function () { fetchHealth(true); });
    var bRules = h("button", "wh-btn", ["Rules"], {type: "button"});
    bRules.addEventListener("click", function () { openDrawer("rules", {}); });
    var bBoard = h("button", "wh-btn", [ST.board ? "Exit 1920 Board" : "1920 Board"], {type: "button", "aria-pressed": ST.board ? "true" : "false"});
    bBoard.addEventListener("click", function () { ST.board = !ST.board; draw(); });
    acts.appendChild(bPause);
    acts.appendChild(bPoll);
    acts.appendChild(bRules);
    if (doc.links && doc.links.sitemap) acts.appendChild(h("a", "wh-btn", ["Site map"], {href: doc.links.sitemap}));
    acts.appendChild(bBoard);
    ctrl.appendChild(acts);
    wrap.appendChild(ctrl);

    // 2. Status banner (never green when feeds missing/stale!)
    var banState = ST.failMsg ? "stale" : (doc.state || "stale");
    var banHead = ST.failMsg ? ST.failMsg
      : banState === "crit" ? ((doc.counts.crit || 0) + " critical wall condition" + (doc.counts.crit === 1 ? "" : "s") + " active")
      : banState === "warn" ? ((doc.counts.warn || 0) + " warning wall condition" + (doc.counts.warn === 1 ? "" : "s") + " active")
      : banState === "ok" ? "All live Network & AD feeds current — no wall conditions open"
      : "Not current — one or more live feeds are missing, unread, or stale (shown grey, never green)";
    var banSub = (doc.feeds || []).filter(function (f) { return f.fresh !== "ok" && f.why; }).map(function (f) { return f.name + ": " + f.why; }).slice(0, 2).join(" · ");
    var ban = h("div", "wh-ban " + banState, [
      h("span", "tag", [sevWord(banState)]),
      h("b", "", [banHead]),
      banSub ? h("span", "sub", [banSub]) : null
    ]);
    if (doc.conds && doc.conds.length) {
      var ins = h("button", "wh-btn go", ["Inspect worst"], {type: "button"});
      ins.addEventListener("click", function () { openDrawer("cond", doc.conds[0]); });
      ban.appendChild(ins);
    }
    wrap.appendChild(ban);

    // 3. 4 KPI tiles
    var kpis = h("div", "wh-kpis");
    var skin = document.documentElement.getAttribute("data-skin") || "";
    (doc.kpis || []).forEach(function (kp, idx) {
      var flipped = skin === "metro" && (idx === ST.flipIdx) && (doc.conds || []).length > 0;
      var tile = h("div", "wh-kpi " + (kp.state || "stale") + (flipped ? " flipped" : ""));
      tile.appendChild(h("div", "k", [
        h("span", "", [kp.label || ""]),
        h("span", "st", [kp.state === "ok" ? "OK" : kp.state === "crit" ? "CRIT" : kp.state === "warn" ? "WARN" : "NOT READ"])
      ]));
      tile.appendChild(h("div", "val", [kp.value || "Not read"]));
      var subText = flipped && doc.conds[0] ? ("Worst: " + doc.conds[0].obj + " — " + doc.conds[0].title) : (kp.sub || "");
      tile.appendChild(h("div", "sub", [subText], {title: subText}));
      tile.appendChild(sparkSvg(kp.spark, kp.state));
      kpis.appendChild(tile);
    });
    wrap.appendChild(kpis);

    // 4. Spotlight strip (when >= 1 active condition)
    var conds = doc.conds || [];
    if (conds.length) {
      if (ST.spotIdx >= conds.length) ST.spotIdx = 0;
      var sc = conds[ST.spotIdx];
      var spot = h("div", "wh-spot");
      spot.appendChild(h("span", "ey", ["Spotlight " + (ST.spotIdx + 1) + "/" + conds.length]));
      spot.appendChild(h("span", "wh-sev " + (sc.last_known ? "stale" : sc.sev), [sc.last_known ? "LAST" : sc.sev.toUpperCase()]));
      var stxt = h("span", "txt", [sc.obj + " — " + sc.title + (sc.val_text ? (" (" + sc.val_text + ")") : "")]);
      stxt.addEventListener("click", function () { openDrawer("cond", sc); });
      spot.appendChild(stxt);
      var sctl = h("div", "ctl");
      var spPrev = h("button", "wh-btn", ["‹"], {type: "button", "aria-label": "Previous spotlight"});
      spPrev.addEventListener("click", function () { ST.spotIdx = (ST.spotIdx - 1 + conds.length) % conds.length; ST.spotTick = 0; draw(); });
      var spNext = h("button", "wh-btn", ["›"], {type: "button", "aria-label": "Next spotlight"});
      spNext.addEventListener("click", function () { ST.spotIdx = (ST.spotIdx + 1) % conds.length; ST.spotTick = 0; draw(); });
      var spP = h("button", "wh-btn", [ST.spotPaused ? "Play" : "Hold"], {type: "button"});
      spP.addEventListener("click", function () { ST.spotPaused = !ST.spotPaused; draw(); });
      sctl.appendChild(spPrev); sctl.appendChild(spNext); sctl.appendChild(spP);
      spot.appendChild(sctl);
      var dw = h("div", "dwell", [h("i")]);
      dw.firstChild.style.width = (ST.spotTick * 10) + "%";
      spot.appendChild(dw);
      wrap.appendChild(spot);
    }

    // 5. Main 2-column grid
    var grid = h("div", "wh-grid");
    var left = h("div", "wh-col");
    var right = h("div", "wh-col");

    // Left: Active wall conditions (Down / Full / Sustained errors)
    var condCard = h("section", "wh-card");
    condCard.appendChild(h("div", "wh-ch", [
      h("h2", "", ["Active wall conditions"]),
      h("span", "cnt", [conds.length + " open · only down, full, and sustained errors"])
    ]));

    // Filter controls
    var flt = h("div", "wh-flt");
    var domSeg = h("div", "wh-seg", [], {role: "group", "aria-label": "Domain filter"});
    [["all", "All"], ["net", "Network"], ["ad", "AD"]].forEach(function (pair) {
      var b = h("button", "", [pair[1]], {type: "button", "aria-pressed": ST.domain === pair[0] ? "true" : "false"});
      b.addEventListener("click", function () { ST.domain = pair[0]; draw(); });
      domSeg.appendChild(b);
    });
    var bktSeg = h("div", "wh-seg", [], {role: "group", "aria-label": "Bucket filter"});
    [["all", "All"], ["down", "Down"], ["full", "Full"], ["err", "Sustained"]].forEach(function (pair) {
      var b = h("button", "", [pair[1]], {type: "button", "aria-pressed": ST.bucket === pair[0] ? "true" : "false"});
      b.addEventListener("click", function () { ST.bucket = pair[0]; draw(); });
      bktSeg.appendChild(b);
    });
    var qIn = h("input", "wh-q", [], {type: "search", placeholder: "Filter host, DC, site…", value: ST.query, "aria-label": "Filter conditions"});
    qIn.addEventListener("input", function () { ST.query = qIn.value; draw(); });
    flt.appendChild(domSeg); flt.appendChild(bktSeg); flt.appendChild(qIn);
    condCard.appendChild(flt);

    var qLow = (ST.query || "").trim().toLowerCase();
    var shown = conds.filter(function (c) {
      if (ST.domain !== "all" && c.domain !== ST.domain) return false;
      if (ST.bucket !== "all" && c.bucket !== ST.bucket) return false;
      if (qLow && ((c.obj || "") + " " + (c.title || "") + " " + (c.site || "") + " " + (c.detail || "")).toLowerCase().indexOf(qLow) < 0) return false;
      return true;
    });

    var validKeys = Object.create(null);
    conds.forEach(function (c) { validKeys[condKey(c)] = 1; });
    Object.keys(ST.sel).forEach(function (k) { if (!validKeys[k]) delete ST.sel[k]; });

    var pc = portalCopy();
    var copyBar = pc ? pc.createFeedbackBar() : null;
    var selectedConds = function () { return conds.filter(function (c) { return !!ST.sel[condKey(c)]; }); };
    var selCnt = pc && pc.createSelCount ? pc.createSelCount() : null;
    var selClr = pc && pc.createSelClear ? pc.createSelClear() : null;
    var selCue = pc && pc.createSelCue ? pc.createSelCue() : null;
    var copyAllBtn = null;
    var selCtx = {
      shownKeys: function () { return shown.map(condKey); },
      syncDom: function () {
        condCard.querySelectorAll(".wh-row-wrap").forEach(function (w) {
          var cb = w.querySelector("input.srv-sel-cb");
          if (!cb) return;
          var on = !!ST.sel[cb.getAttribute("data-sel-key") || ""];
          cb.checked = on;
          w.classList.toggle("srv-selected", on);
        });
      },
      sync: function () {
        var nsel = selectedConds().length;
        if (selCnt) { selCnt.textContent = nsel + " selected"; selCnt.classList.toggle("has-sel", nsel > 0); }
        if (selClr) selClr.hidden = nsel === 0;
        if (copyAllBtn && copyAllBtn.setSelection) copyAllBtn.setSelection(nsel, shown.length, "condition");
      }
    };
    if (pc) {
      copyAllBtn = pc.createSplitControl({
        cls: "pri wh-copy-all", scope: "all", baseLabel: "Copy all shown", withCount: true, count: shown.length,
        title: "Copy every condition currently shown after filters (text + picture)",
        ariaLabel: "Copy all shown conditions", toggleLabel: "Copy all shown or selected options",
        menuTitle: "Copy all shown", menuTag: shown.length + " row" + (shown.length === 1 ? "" : "s"),
        subtitles: {both: "Text summary + PNG", text: "Plain + HTML summary", picture: "PNG summary card"},
        isEmpty: function () { return !(selectedConds().length || shown.length); },
        onCopy: function (mode, btn, finish) {
          var list = selectedConds();
          if (!list.length) list = shown;
          if (!list.length) { finish(); return Promise.resolve(); }
          var srvs = list.map(condToServer), cx = healthCopyCtx(doc);
          var fmt = pc.formatAllShown(srvs, cx), pic = mode === "text" ? {dataUrl: ""} : pc.drawAllShownCard(srvs, cx);
          return pc.copyMode(mode, {
            plain: fmt.plain, html: fmt.html, dataUrl: pic.dataUrl, width: 700, alt: "Network & AD health summary card", focusEl: btn,
            onState: function (st, lab, msg) { pc.setFeedback(btn, copyBar, st, lab, msg); },
            onDone: finish
          });
        }
      });
      if (selClr) {
        selClr.addEventListener("click", function () {
          ST.sel = Object.create(null);
          ST.lastSelKey = "";
          selCtx.syncDom();
          selCtx.sync();
        });
      }
      var selBar = h("div", "wh-sel-bar", [copyAllBtn, selCnt, selClr, selCue]);
      flt.appendChild(selBar);
      selCtx.sync();
    }

    if (!shown.length) {
      condCard.appendChild(h("div", "wh-empty", [
        conds.length ? "No open conditions match the active filter."
          : banState === "ok" ? "No active wall conditions — all watched network and AD checks are within limits."
          : "No active conditions reported because live feeds are not current or not configured yet (see feed pills above)."
      ]));
    } else {
      ["down", "full", "err"].forEach(function (bk) {
        var grp = shown.filter(function (c) { return c.bucket === bk; });
        if (!grp.length) return;
        var sec = h("div", "wh-bkt");
        sec.appendChild(h("div", "wh-bh", [h("b", "", [bucketTitle(bk)]), h("span", "", ["(" + grp.length + ")"])]));
        var lst = h("div", "wh-list");
        grp.forEach(function (c) { lst.appendChild(buildConditionRow(c, now, doc, copyBar, selCtx)); });
        sec.appendChild(lst);
        condCard.appendChild(sec);
      });
    }
    if (copyBar) condCard.appendChild(copyBar);

    var supCnt = (doc.counts && doc.counts.suppressed) || (doc.suppressed || []).length;
    var supBtn = h("button", "wh-btn", ["Inspect suppressed (" + supCnt + ")"], {type: "button"});
    supBtn.addEventListener("click", function () { openDrawer("sup", {}); });
    condCard.appendChild(h("div", "wh-sup", [
      h("span", "", ["Short blips, unmanaged nodes & dependent children behind a down parent stay off the wall."]),
      supBtn
    ]));
    left.appendChild(condCard);

    // Right 1: 4h Trends with limits
    var trCard = h("section", "wh-card");
    var trSeg = h("div", "wh-seg", [], {role: "group", "aria-label": "Trend metric"});
    [["rtt", "WAN RTT"], ["loss", "Loss"], ["ad", "AD Lag"], ["vol", "Volume"]].forEach(function (p) {
      var b = h("button", "", [p[1]], {type: "button", "aria-pressed": ST.trend === p[0] ? "true" : "false"});
      b.addEventListener("click", function () { ST.trend = p[0]; draw(); });
      trSeg.appendChild(b);
    });
    trCard.appendChild(h("div", "wh-ch", [h("h2", "", ["4h trends with limits"]), h("div", "r", [trSeg])]));
    trCard.appendChild(trendChart(doc));
    right.appendChild(trCard);

    // Right 2: AD replication matrix
    var adCard = h("section", "wh-card");
    var ad = doc.ad || {};
    adCard.appendChild(h("div", "wh-ch", [
      h("h2", "", ["AD replication matrix"]),
      h("span", "cnt", [ad.read ? ((ad.dcs || []).length + " DCs · " + (ad.ok_pairs || 0) + "/" + (ad.total_pairs || 0) + " pairs OK") : "Not read"]),
      doc.links && doc.links.ad ? h("a", "wh-btn r", ["Full AD page"], {href: doc.links.ad}) : null
    ]));
    if (!ad.read || !(ad.dcs || []).length) {
      var adFeed = (doc.feeds || []).filter(function (f) { return f.id === "adjob"; })[0];
      adCard.appendChild(h("div", "wh-empty", ["AD replication not read: " + ((adFeed && adFeed.why) || "attach Report database credential and run AD health job.")]));
    } else {
      var dcs = (ad.dcs || []).slice(0, 10);
      var pmap = {};
      (ad.pairs || []).forEach(function (p) { pmap[p.src.toLowerCase() + ">" + p.dst.toLowerCase()] = p; });
      var mx = h("table", "wh-mx");
      var thr = h("tr", "", [h("th", "", ["Src ↓ / Dst →"])]);
      dcs.forEach(function (d) { thr.appendChild(h("th", "", [d.name.split(".")[0]], {title: d.name + (d.site ? " (" + d.site + ")" : "")})); });
      mx.appendChild(h("thead", "", [thr]));
      var tb = h("tbody");
      dcs.forEach(function (sr) {
        var tr = h("tr", "", [h("th", "rh", [sr.name.split(".")[0]], {title: sr.name})]);
        dcs.forEach(function (ds) {
          var td = h("td");
          if (sr.id === ds.id) {
            td.appendChild(h("span", "wh-mc self", ["—"]));
          } else {
            var pair = pmap[sr.id + ">" + ds.id];
            if (!pair) {
              td.appendChild(h("span", "wh-mc " + (ds.repl_read ? "self" : "stale"), [ds.repl_read ? "·" : "?"], {title: ds.repl_read ? "Not replication partners" : "Destination replication not read"}));
            } else {
              var lbl = pair.status === "stale" ? "?" : pair.lag_min !== null ? (Math.round(pair.lag_min) + "m") : (pair.failures + "f");
              var btn = h("button", "wh-mc " + pair.status, [lbl], {type: "button", title: pair.src + " → " + pair.dst + ": " + sevWord(pair.status)});
              btn.addEventListener("click", (function (pp) { return function () { openDrawer("pair", pp); }; })(pair));
              td.appendChild(btn);
            }
          }
          tr.appendChild(td);
        });
        tb.appendChild(tr);
      });
      mx.appendChild(tb);
      adCard.appendChild(h("div", "wh-mx-wrap", [mx]));
    }
    right.appendChild(adCard);

    // Right 3: Monitored estate node squares
    var estCard = h("section", "wh-card");
    var est = doc.estate || [];
    estCard.appendChild(h("div", "wh-ch", [
      h("h2", "", ["Monitored estate"]),
      h("span", "cnt", [est.length ? (est.length + " groups · click any square") : "Not read"])
    ]));
    if (!est.length) {
      var swFeed = (doc.feeds || []).filter(function (f) { return f.id === "swis"; })[0];
      estCard.appendChild(h("div", "wh-empty", ["SolarWinds nodes not read: " + ((swFeed && swFeed.why) || "attach SolarWinds read-only credential.")]));
    } else {
      var egBox = h("div", "wh-est");
      est.slice(0, 8).forEach(function (g) {
        var sqg = h("div", "wh-sqg");
        (g.nodes || []).forEach(function (nd) {
          var sq = h("button", "wh-sq " + (nd.status || "stale"), [], {
            type: "button", title: nd.name + " (" + (nd.status || "stale").toUpperCase() + ")" + (nd.why ? " · " + nd.why : ""),
            "aria-label": nd.name + " " + (nd.status || "stale")
          });
          sq.addEventListener("click", (function (nn) { return function () { openDrawer("node", nn); }; })(nd));
          sqg.appendChild(sq);
        });
        egBox.appendChild(h("div", "wh-eg", [
          h("div", "wh-egh", [
            h("b", "", [g.name]),
            h("span", "cnt", [g.up + "/" + g.total + " up" + (g.down ? " · " + g.down + " down" : "") + (g.warn ? " · " + g.warn + " warn" : "")])
          ]),
          sqg
        ]));
      });
      estCard.appendChild(egBox);
    }
    right.appendChild(estCard);

    // Right 4: Last 4h timeline
    var tlCard = h("section", "wh-card");
    var tl = doc.timeline || [];
    tlCard.appendChild(h("div", "wh-ch", [h("h2", "", ["Last 4 hours timeline"]), h("span", "cnt", [tl.length + " events"])]));
    if (!tl.length) {
      tlCard.appendChild(h("div", "wh-empty", ["No wall conditions opened or cleared in the last 4 hours."]));
    } else {
      var tlBox = h("div", "wh-tl");
      var t0 = now - 4 * 3600;
      tl.slice(0, 10).forEach(function (ev) {
        var st = Math.max(t0, ev.start || t0), en = Math.min(now, ev.end || now);
        var leftPct = ((st - t0) / (4 * 3600)) * 100;
        var widPct = Math.max(2, ((en - st) / (4 * 3600)) * 100);
        var bar = h("div", "bar");
        bar.style.left = leftPct.toFixed(1) + "%";
        bar.style.width = Math.min(100 - leftPct, widPct).toFixed(1) + "%";
        tlBox.appendChild(h("div", "wh-tlr " + (ev.sev || "stale") + (ev.end ? " cleared" : ""), [
          h("span", "nm", [ev.obj || ""], {title: (ev.obj || "") + " — " + (ev.title || "")}),
          h("div", "trk", [bar]),
          h("span", "st", [ev.end ? "cleared" : "open"])
        ]));
      });
      tlCard.appendChild(tlBox);
    }
    right.appendChild(tlCard);

    grid.appendChild(left);
    grid.appendChild(right);
    wrap.appendChild(grid);
    ST.stage.appendChild(wrap);
  }

  document.addEventListener("keydown", function (ev) {
    if (ST.stage && ev.key === "Escape") {
      if (ST.drawer) closeDrawer();
      else if (ST.board) { ST.board = false; draw(); }
    }
  });
  document.addEventListener("portal-look", function () { if (ST.stage && ST.data) draw(); });

  window.SAWallHealth = {
    icon: ICON,
    leave: function () {
      if (!ST.stage) return;
      clearInterval(ST.timer);
      ST.timer = null;
      ST.stage = null;
      ST.loading = false;
      closeDrawer();
      document.documentElement.classList.remove("wh-board");
    },
    render: function (api, stage) {
      var entering = !ST.stage;
      ST.api = api;
      ST.stage = stage;
      var root = document.getElementById("wall");
      if (root && root.getAttribute("data-health-src")) ST.src = root.getAttribute("data-health-src");
      var qs = new URLSearchParams(location.search);
      if (qs.get("board") === "1") ST.board = true;
      ensureTimer();
      draw();
      if (entering) fetchHealth(false);
    },
    tile: function (A, sevWordFn) {
      var W = A.W();
      var hm = W && W.health ? W.health : {};
      var st = A.areaState("health");
      var c = (hm.counts && hm.counts.crit) || 0, w = (hm.counts && hm.counts.warn) || 0;
      return {
        id: "health", name: "Network & AD health", team: "Network / AD", st: st,
        off: false, n: {crit: c, warn: w},
        big: st === "stale" ? "—" : String((hm.counts && hm.counts.open) || 0),
        unit: st === "stale" ? "not current" : "conditions",
        note: sevWordFn ? sevWordFn(st) : st
      };
    }
  };
})();
