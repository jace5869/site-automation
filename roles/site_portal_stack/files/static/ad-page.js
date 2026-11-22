// 1.101.2: the portal's Active Directory page (/portal/ad) - drawn from /portal/api/ad: the AD job's runs in the report database
// (now, since when, the last 48 runs, the week), SolarWinds for the newest run (the portal's own read), the next run, the link
// to the newest run's full report. Asks again every poll seconds (an ETag: a 304 when nothing changed). A DC whose state
// changes between two answers is said in a toast. Every state is said: the database off, not read now (the last answer
// shown with its own time), no run yet, a run older than portal_ad_old_minutes. No inline script or style (the CSP).
"use strict";
(function () {
  const root = document.getElementById("ad");
  if (!root || !window.fetch) return;
  const API = root.getAttribute("data-src");
  const toasts = document.getElementById("ad-toasts");
  const SVG = "http://www.w3.org/2000/svg";
  const WORD = {critical: "Critical", warning: "Warning", low: "Low", unknown: "Unknown", ok: "OK"};
  const MARK = {ok: [["path", {d: "M3.2 8.4l3 3 6.6-7"}]], bad: [["path", {d: "M4 4l8 8M12 4l-8 8"}]],
                warn: [["path", {d: "M8 3.2v5.6M8 11.6v.4"}]], skip: [["circle", {cx: 8, cy: 8, r: 5.4}], ["path", {d: "M5.6 8h4.8"}]]};
  const KIND = {ok: "ok", critical: "bad", warning: "warn", low: "warn", unknown: "skip"};
  const RANK = {critical: 0, warning: 1, unknown: 2, low: 3, ok: 4};
  const moves = () => document.documentElement.classList.contains("moves");

  function el(tag, cls, kids) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    (Array.isArray(kids) ? kids : kids == null ? [] : [kids]).forEach(k => { if (k != null && k !== false) e.append(typeof k === "object" ? k : String(k)); });
    return e;
  }
  function mark(kind) {
    const s = document.createElementNS(SVG, "svg");
    s.setAttribute("class", "ic"); s.setAttribute("viewBox", "0 0 16 16"); s.setAttribute("aria-hidden", "true");
    for (const [tag, at] of MARK[kind] || MARK.skip) {
      const n = document.createElementNS(SVG, tag);
      for (const [k, v] of Object.entries(Object.assign({fill: "none", stroke: "currentColor", "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round"}, at))) n.setAttribute(k, v);
      s.append(n);
    }
    return s;
  }
  function chip(st, text) {
    const k = WORD[st] ? st : "unknown";
    return el("span", "chip s-" + (k === "unknown" ? "info" : k), [mark(KIND[k]), text || WORD[k]]);
  }
  const ago = s => s == null ? "" : s < 60 ? "just now" : s < 3600 ? Math.round(s / 60) + " min ago" : (Math.round(s / 360) / 10) + " h ago";
  const hrs = m => m < 60 ? m + " min" : (Math.round(m / 6) / 10) + " h";
  const hhmm = t => String(t || "").replace(/^\d{4}-\d\d-\d\d /, "");       // "2026-11-23 09:30 UTC" -> "09:30 UTC": today's runs
  function table(head, rows, cls) {
    const t = el("table", cls || "");
    t.append(el("thead", "", el("tr", "", head.map(h => { const th = el("th", h[1] || "", h[0]); th.scope = "col"; return th; }))));
    t.append(el("tbody", "", rows));
    return el("div", "tw", t);
  }
  function sec(title, count, hint, kids) {
    const h = el("h2", "", [title, count != null ? el("span", "count", count) : null, hint ? el("span", "adp-hint", hint) : null]);
    return el("section", "sec", [h].concat(kids));
  }
  function tile(k, v, n) {
    return el("div", "tile", [el("div", "k", k), el("div", "v", v), el("div", "n", n || "")]);
  }
  const num = (n, st) => el("span", st ? "c-" + st : "", n);

  // ---- the parts --------------------------------------------------------------------------------------------------------------
  function live(a, m) {
    const run = m && m.run, sw = a.sw || {};
    const ok = run && !run.old && !a.db.stale && (!sw.on || !sw.stale);
    const l = el("div", "l", [el("span", "adp-dot" + (ok ? "" : " off"), ""), el("b", "", ok ? "Live" : "Not live")]);
    if (run) l.append(el("span", run.old ? "c-warning" : "", run.old ? "AD checks: as of " + run.as_of + " - " + hrs(run.age_min) + " old: the AD job has not run since"
                                                                      : "AD checks: as of " + hhmm(run.as_of) + " (the AD job" + (run.next ? "; next " + hhmm(run.next) : "") + ")"));
    l.append(el("span", "adp-sep", "·"));
    l.append(el("span", sw.stale ? "c-warning" : "", !sw.on ? "SolarWinds: not read (" + sw.why + ")" : sw.stale ? "SolarWinds: not read now - " + sw.stale
                                                    : sw.section ? "SolarWinds: updated " + ago(sw.age) : "SolarWinds: reading…"));
    const r = el("div", "r");
    if (a.report) { const x = el("a", "", "The full report of " + hhmm(a.report.when) + " →"); x.href = a.report.url; r.append(x); }
    return el("div", "card strip adp-live", [l, r]);
  }
  function note(kind, text) { return el("div", "note" + (kind ? " " + kind : ""), el("p", "", text)); }

  function swOf(sw) {          // the newest run's DCs as SolarWinds sees them: {dc: [text, status]}
    const out = {}, s = sw && sw.section;
    if (!s) return out;
    const i = s.columns.indexOf("DC"), j = s.columns.indexOf("SolarWinds");
    s.rows.forEach((r, k) => { out[String(r[i]).toLowerCase()] = [r[j], (s.cell_status[k] || [])[j] || ""]; });
    return out;
  }
  function glance(a, m) {
    const run = m.run, dcs = m.dcs, sw = a.sw || {}, links = m.links;
    const bad = dcs.filter(d => d.now !== "ok"), crit = dcs.filter(d => d.now === "critical"), warn = dcs.filter(d => d.now === "warning");
    const failing = links.filter(x => x.failures > 0), late = links.filter(x => !(x.failures > 0));
    const first = bad[0];
    const swDown = sw.tile ? sw.tile.value : null;
    const swFirst = Object.entries(swOf(sw)).filter(([, v]) => /critical|warning/.test(v[1]))[0];
    const day = m.hist.filter(h => Date.parse(h.at) >= Date.now() - 864e5).length;
    const t = [
      tile("Status now", chip(run.status || "ok"), crit.length + " DC" + (crit.length === 1 ? "" : "s") + " critical, " + (warn.length || "none") + " warning"),
      tile("DCs read", run.dcs_read + " of " + run.dcs, run.dcs_read === run.dcs ? "every DC answered" : (run.dcs - run.dcs_read) + " did not answer"),
      tile("DCs with a problem", num(bad.length, bad.length ? (crit.length ? "critical" : "warning") : ""),
           first ? first.dc + " " + WORD[first.now].toLowerCase() + (first.since_shown ? " since " + hhmm(first.since_shown) : "") : "none"),
      tile("SolarWinds: down or warning", swDown == null ? "-" : num(swDown, swDown ? sw.tile.status : ""),
           !sw.on ? "not read here" : swFirst ? swFirst[0].toUpperCase() + " " + swFirst[1][0] : sw.section ? "every DC up" : "reading…"),
      tile("Replication failing", num(failing.length + (failing.length === 1 ? " link" : " links"), failing.length ? "critical" : ""),
           failing.length ? "no success since " + hhmm(failing.map(x => x.last_success_shown).filter(Boolean).sort()[0] || "-") : "none"),
      tile("Replication late", num(late.length + (late.length === 1 ? " link" : " links"), late.length ? "warning" : ""), late.length ? "past the matrix's hours since the last success" : "none"),
      tile("AD job runs, 24 h", String(day), run.next ? "next " + hhmm(run.next) : "the schedule was not read"),
      tile("Worst DC, 7 days", m.worst7 ? m.worst7.dc : "none", m.worst7 ? "critical in " + m.worst7.critical + " of " + m.hist.length + " runs" : "no DC critical"),
    ];
    return sec("At a glance", null, null, [el("div", "tiles adp-tiles", t)]);
  }
  function dcTable(a, m) {
    const sws = swOf(a.sw), n = m.times.length;
    const swRank = d => { const s = sws[d.dc.toLowerCase()]; return s && s[1] === "critical" ? 0 : s && s[1] === "warning" ? 1 : 2; };
    const dcs = m.dcs.slice().sort((x, y) => (RANK[x.now] - RANK[y.now]) || (swRank(x) - swRank(y)) || x.dc.localeCompare(y.dc));
    const rows = dcs.map(d => {
      const s = sws[d.dc.toLowerCase()];
      const why = d.worst || (s && s[1] === "critical" ? "SolarWinds: " + s[0] + ", AD answers" : "");
      const tr = el("tr", d.now !== "ok" ? "r-" + d.now : "");
      tr.dataset.dc = d.dc;
      const strip = el("span", "adp-trend");
      strip.setAttribute("role", "img");
      strip.setAttribute("aria-label", d.dc + ": the last " + n + " runs, " + d.trend.filter(x => x && x !== "ok").length + " not OK");
      d.trend.forEach((x, k) => { const i = el("i", "k-" + (x || "none")); i.title = (m.times[k] || "") + ": " + (WORD[x] || "no run"); strip.append(i); });
      const since = d.now !== "ok" ? "since " + hhmm(d.since_shown) + " · " + d.runs_since + (d.longer ? "+" : "") + (d.runs_since === 1 && !d.longer ? " run" : " runs")
                                    : d.runs_since >= n ? "OK all " + n + " runs" : "OK since " + hhmm(d.since_shown);
      tr.append(el("td", "adp-dc", [el("b", "", d.dc), why ? el("div", "adp-why", why) : null]), el("td", "nw adp-wide", d.site || "-"),
                el("td", "nw", chip(d.now)), el("td", "nw", since), el("td", "", strip),
                el("td", "nw", s ? chip(s[1] === "critical" ? "critical" : s[1] === "warning" ? "warning" : "ok", s[0]) : el("span", "muted", a.sw.on ? "-" : "not read")),
                el("td", "adp-wide", why || el("span", "muted", "-")));
      return tr;
    });
    const leg = el("p", "adp-leg", [["ok", "OK"], ["warning", "Warning"], ["critical", "Critical"], ["none", "No run"]].map(([k, t]) => el("span", "", [el("i", "k-" + k), t])));
    return sec("Domain controllers", m.dcs.length, "worst first · a square is one run, oldest left",
               [table([["DC"], ["Site", "adp-wide"], ["Now"], ["Since"], ["Last " + n + " runs"], ["SolarWinds"], ["What is wrong", "adp-wide"]], rows, "adp-dcs"), leg]);
  }
  function linkTable(m) {
    if (!m.links.length) return sec("Replication links that need a look", 0, "as of " + hhmm(m.run.as_of), [el("p", "muted", "None: every replication link succeeded within the matrix's hours.")]);
    const rows = m.links.map(x => el("tr", RANK[x.now] < 2 ? "r-" + x.now : "", [
      el("td", "adp-dc", [el("b", "", x.frm), " → ", el("b", "", x.to), el("div", "adp-why", x.nc + (x.error ? " · " + x.error : ""))]),
      el("td", "nw adp-wide", x.nc), el("td", "nw", chip(x.now)), el("td", "nw adp-wide", x.last_success_shown || "never"),
      el("td", "nw", (x.failures > 0 ? "failing, no success since " : "late, no success since ") + (hhmm(x.last_success_shown) || "-")),
      el("td", "num", x.failures || "-"), el("td", "adp-wide", x.error || el("span", "muted", "-"))]));
    return sec("Replication links that need a look", m.links.length, "as of " + hhmm(m.run.as_of),
               [table([["From → to"], ["Partition", "adp-wide"], ["Now"], ["Last success", "adp-wide"], ["Since"], ["Failures", "num"], ["Error", "adp-wide"]], rows)]);
  }
  function windows(hist) {     // the worst run of each 2 hours (the week has ~336 runs: too many columns)
    const out = [], by = {};
    hist.forEach(h => {
      const t = Date.parse(h.at), k = Math.floor(t / 72e5);
      if (!(k in by)) { by[k] = out.length; out.push({k, at: h.at_shown, c: 0, w: 0}); }
      const o = out[by[k]];
      if (h.c > o.c || (h.c === o.c && h.w > o.w)) { o.c = h.c; o.w = h.w; o.at = h.at_shown; }
    });
    return out;
  }
  function chart(m) {
    const box = el("div", "card adp-chart"), tip = el("div", "adp-tip");
    tip.hidden = true;
    box.append(tip);
    const draw = () => {
      box.querySelectorAll("svg").forEach(x => x.remove());
      const ws = windows(m.hist);
      const W = Math.max(480, box.clientWidth - 24), H = 190, L = 28, B = 22, n = Math.max(ws.length, 12), bw = (W - L) / n;
      const max = Math.max(4, ...ws.map(h => h.c + h.w));
      const y = v => (H - B) - v / max * (H - B - 10);
      const s = document.createElementNS(SVG, "svg");
      s.setAttribute("viewBox", "0 0 " + W + " " + H); s.setAttribute("role", "img");
      s.setAttribute("aria-label", "DCs with a problem per run, last 7 days: the numbers are in the runs below");
      const add = (tag, at, text) => { const e = document.createElementNS(SVG, tag); for (const k in at) e.setAttribute(k, at[k]); if (text != null) e.textContent = text; s.append(e); return e; };
      [0, Math.round(max / 2), max].forEach(v => { add("line", {x1: L, x2: W, y1: y(v), y2: y(v), class: "gl"}); add("text", {x: L - 6, y: y(v) + 4, "text-anchor": "end", class: "ax"}, v); });
      let day = "";
      ws.forEach((h, i) => {
        const x = L + i * bw + 1, w = Math.max(1, bw - 2);
        if (h.c) add("rect", {x, y: y(h.c), width: w, height: (H - B) - y(h.c), rx: 1.5, class: "bc"});
        if (h.w) add("rect", {x, y: y(h.c + h.w), width: w, height: Math.max(0, y(h.c) - y(h.c + h.w) - 1), rx: 1.5, class: "bw"});
        const d = String(h.at).slice(0, 10);
        if (d !== day) { day = d; add("text", {x, y: H - 6, class: "ax"}, d.slice(5)); }
        const hit = add("rect", {x: L + i * bw, y: 0, width: bw, height: H - B, class: "hit"});
        hit.addEventListener("mousemove", e => {
          tip.replaceChildren(el("b", "", h.at), h.c + " DC" + (h.c === 1 ? "" : "s") + " critical · " + h.w + " warning");
          tip.hidden = false;
          const r = box.getBoundingClientRect();
          tip.style.left = Math.max(4, Math.min(e.clientX - r.left + 12, r.width - tip.offsetWidth - 8)) + "px";
          tip.style.top = Math.max(4, e.clientY - r.top - tip.offsetHeight - 10) + "px";
        });
        hit.addEventListener("mouseleave", () => { tip.hidden = true; });
      });
      box.insertBefore(s, tip);
    };
    requestAnimationFrame(draw);
    chart.redraw = draw;
    const runs = m.hist.slice(-8).reverse().map(h => el("tr", "", [el("td", "nw", h.at_shown), el("td", "nw", chip(h.c ? "critical" : h.w ? "warning" : "ok")),
      el("td", "num", h.c), el("td", "num", h.w), el("td", "num", h.read + " of " + h.dcs), el("td", "nw", h.job ? "job " + h.job : "-")]));
    const leg = el("p", "adp-leg", [el("span", "", [el("i", "k-critical"), "Critical"]), el("span", "", [el("i", "k-warning"), "Warning"])]);
    return sec("DCs with a problem, last 7 days", null, "the worst run of each 2 hours; the numbers: the runs below",
               [m.hist.length ? box : el("p", "muted", "No run in the last 7 days."), leg,
                table([["Run"], ["Status"], ["DCs critical", "num"], ["DCs warning", "num"], ["DCs read", "num"], ["Job"]], runs)]);
  }
  window.addEventListener("resize", () => { if (chart.redraw) chart.redraw(); });

  // ---- what changed between two answers: a toast, the row flashes once ---------------------------------------------------
  let last = null;
  function said(a, m) {
    const now = {}, sws = swOf(a.sw);
    (m && m.dcs || []).forEach(d => { now[d.dc] = [d.now, (sws[d.dc.toLowerCase()] || [])[0]]; });
    if (last) {
      const out = [];
      Object.keys(now).forEach(dc => {
        const w = last[dc];
        if (!w) return;
        if (w[0] !== now[dc][0]) out.push([dc + ": AD " + WORD[now[dc][0]], "was " + WORD[w[0]], RANK[now[dc][0]] < 2]);
        if (w[1] && now[dc][1] && w[1] !== now[dc][1]) out.push([dc + ": SolarWinds " + now[dc][1], "was " + w[1], now[dc][1] !== "Up"]);
      });
      out.slice(0, 3).forEach(([t, was, bad]) => {
        const x = el("button", "x", "×");
        x.type = "button"; x.setAttribute("aria-label", "Close");
        const box = el("div", "toast adp-toast" + (bad ? " bad" : ""), [el("div", "", [el("b", "", t), el("div", "muted", was)]), x]);
        x.addEventListener("click", () => box.remove());
        while (toasts.children.length >= 3) toasts.firstChild.remove();
        toasts.append(box);
        setTimeout(() => box.remove(), 15000);
        const row = root.querySelector('tr[data-dc="' + t.split(":")[0] + '"]');
        if (row && moves()) row.classList.add("adp-flash");
      });
    }
    last = now;
  }

  // ---- the answer --------------------------------------------------------------------------------------------------------------
  function draw(a) {
    const db = a.db || {}, m = db.model;
    const parts = [];
    if (!db.on) {
      parts.push(note("warn", "The report database is off here: " + db.why + "."));
    } else if (!m) {
      parts.push(db.stale ? note("bad", "The report database could not be read: " + db.stale + ". Tried again every minute.")
                          : note("", "Reading the report database…"));
    } else if (!m.run) {
      parts.push(live(a, m), note("", "No AD run in the report database yet: the AD job keeps each run there from 1.101.2 on, when its template has the " +
                                       "\"Report database\" credential (the template setup job attaches it). The next run is the first."));
    } else {
      parts.push(live(a, m));
      if (db.stale) parts.push(note("warn", "The report database was not read just now (" + db.stale + "): this is its answer of " + ago(db.age) + ", as of its run " + m.run.as_of + "."));
      if (m.run.old) parts.push(note("warn", "The newest AD run is " + hrs(m.run.age_min) + " old (more than " + a.old_after + " min): the AD job has not run since. " +
                                             "What follows is as of " + m.run.as_of + ", not now."));
      parts.push(glance(a, m), dcTable(a, m), linkTable(m), chart(m),
                 el("p", "adp-foot", "From the report database: the AD job's runs (ad_run, ad_dc, ad_link), read every minute while this page is open. " +
                                     "SolarWinds: read by this server every " + Math.round((a.sw.every || 600) / 60) + " min. Read only - nothing here changes AD."));
    }
    root.replaceChildren(...parts);
    said(a, m);
  }

  let etag = "", shown = null, got = Date.now(), timer = null, poll = 60;
  function ask() {
    fetch(API, {headers: etag ? {"If-None-Match": etag} : {}, credentials: "same-origin", cache: "no-store"}).then(r => {
      if (r.status === 304) return null;
      if (!r.ok) throw new Error("the portal answered " + r.status);
      etag = r.headers.get("ETag") || "";
      got = Date.now();
      return r.json();
    }).then(a => {
      if (a) { shown = a; poll = Math.max(15, Math.min(300, a.poll || 60)); draw(a); }
      else if (shown) {                         // the same answer: its ages move on
        const gone = Math.round((Date.now() - got) / 1000);
        const a2 = JSON.parse(JSON.stringify(shown));
        if (a2.sw && a2.sw.age != null) a2.sw.age += gone;
        if (a2.db && a2.db.age != null) a2.db.age += gone;
        last = null;                           // the same DCs: nothing to say
        draw(a2);
      }
    }).catch(e => {
      const n = document.getElementById("ad-off") || el("p", "note bad");
      n.id = "ad-off";
      n.textContent = "The portal did not answer (" + e.message + "): the page stays as it is, tried again in " + poll + " s.";
      if (!n.parentNode) root.prepend(n);
    }).then(() => { clearTimeout(timer); timer = setTimeout(ask, poll * 1000); });
  }
  window.adPage = {draw: d => { shown = d; draw(d); }};       // the tests
  ask();
})();
