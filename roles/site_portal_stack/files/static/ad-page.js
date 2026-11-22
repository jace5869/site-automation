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
  let today = "";                             // the newest run's day: a time of that day shown without it
  const hhmm = t => { const x = String(t || ""); return today && x.slice(0, 10) === today ? x.slice(11) : x; };
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
    const ok = run && !a.down && !run.old && !a.db.stale && !a.db.refreshing && (!sw.on || !sw.stale);
    const l = el("div", "l", [el("span", "adp-dot" + (ok ? "" : " off"), ""), el("b", "", ok ? "Live" : a.db.refreshing && !a.down ? "Refreshing…" : "Not live")]);
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

  // the newest run's DCs as SolarWinds sees them: {dc_id: [text, status]}. status: critical, warning, ok (a positive Up only) or "" (not in SolarWinds, not matched, not known)
  const idOf = d => d.dc_id || (String(d.domain || "").replace(/\.$/, "") + "|" + String(d.dc || "")).toLowerCase();
  function swOf(sw) {
    const out = {}, s = sw && sw.section;
    if (!s) return out;
    const i = s.columns.indexOf("DC"), j = s.columns.indexOf("SolarWinds"), keys = s.keys || [];
    s.rows.forEach((r, k) => {
      const c = (s.cell_status[k] || [])[j] || "", st = c === "critical" || c === "warning" ? c : c === "ok" && r[j] === "Up" ? "ok" : "";
      if (keys.length === s.rows.length && (!/^\|/.test(keys[k]) || keys[k].includes("\\"))) { out[keys[k]] = [r[j], st]; return; }
      const key = "?|" + String(r[i]).toLowerCase();        // no domain with the row: by name, a name twice not known
      out[key] = key in out ? ["two DCs of this name", ""] : [r[j], st];
    });
    return out;
  }
  // a row without a domain: only for the one DC of that name on the page - never one Up for two domains' DC01
  const swFor = (sws, d, m) => sws[idOf(d)] || ((m.live && m.live.known || m.dcs || []).filter(x => String(x.dc || x.name).toLowerCase() === String(d.dc).toLowerCase()).length === 1
                                                ? sws["?|" + String(d.dc).toLowerCase()] : undefined);
  // a DC's name, with its domain when another DC on the page has the same name
  const nameOf = (m, d) => (m.dcs || []).filter(x => x.dc.toLowerCase() === String(d.dc).toLowerCase()).length > 1 && d.domain ? d.dc + " (" + d.domain + ")" : d.dc;
  function glance(a, m) {
    const run = m.run, dcs = m.dcs, sw = a.sw || {}, links = m.links;
    const bad = dcs.filter(d => d.now !== "ok"), crit = dcs.filter(d => d.now === "critical"), warn = dcs.filter(d => d.now === "warning");
    const failing = links.filter(x => x.failures > 0), late = links.filter(x => !(x.failures > 0));
    const first = bad[0];
    const swDown = sw.tile ? sw.tile.value : null;
    const sws = swOf(sw), seen = dcs.map(d => [d, swFor(sws, d, m)]);
    const swFirst = seen.filter(([, v]) => v && /critical|warning/.test(v[1])).sort((x, y) => RANK[x[1][1]] - RANK[y[1][1]])[0];
    const swUp = seen.filter(([, v]) => v && v[1] === "ok").length, swNot = dcs.length - swUp - seen.filter(([, v]) => v && /critical|warning/.test(v[1])).length;
    const day = m.hist.filter(h => Date.parse(h.at) >= Date.now() - 864e5).length;
    const t = [
      tile("Status now", chip(run.status || "ok"), crit.length + " DC" + (crit.length === 1 ? "" : "s") + " critical, " + (warn.length || "none") + " warning"),
      run.dcs ? tile("DCs read", run.dcs_read + " of " + run.dcs, run.dcs_read === run.dcs ? "every DC answered" : (run.dcs - run.dcs_read) + " did not answer")
              : tile("DCs read", num("none", "critical"), "the AD job found no DC (its read of the domain failed)"),
      tile("DCs with a problem", num(bad.length, bad.length ? (crit.length ? "critical" : "warning") : ""),
           first ? first.dc + " " + WORD[first.now].toLowerCase() + (first.since_shown ? " since " + hhmm(first.since_shown) : "") : "none"),
      tile("SolarWinds: down or warning", swDown == null ? "-" : num(swDown, swDown ? sw.tile.status : ""),
           !sw.on ? "not read here" : swFirst ? nameOf(m, swFirst[0]) + " " + swFirst[1][0]
             : !sw.section ? "reading…" : swUp === dcs.length ? "every DC up"
             : swNot + " DC" + (swNot === 1 ? "" : "s") + " not in SolarWinds or not matched, " + swUp + " up"),
      !replRead(run) && !failing.length ? tile("Replication failing", num("not read", "warning"), replWhy(run))
        : tile("Replication failing", num(failing.length + (failing.length === 1 ? " link" : " links"), failing.length ? "critical" : ""),
               failing.length ? "no success since " + hhmm(failing.map(x => x.last_success_shown).filter(Boolean).sort()[0] || "-") +
                                (replAll(run) ? "" : " - and only " + replWhy(run))
                              : replAll(run) ? "none" : "none of those read - " + replWhy(run)),
      !replRead(run) && !late.length ? tile("Replication late", num("not read", "warning"), replWhy(run))
        : tile("Replication late", num(late.length + (late.length === 1 ? " link" : " links"), late.length ? "warning" : ""),
               late.length ? "past the matrix's hours since the last success" : replAll(run) ? "none" : "none of those read"),
      tile("AD job runs, 24 h", String(day), run.next ? "next " + hhmm(run.next) : "the schedule was not read"),
      tile("Worst DC, 7 days", m.worst7 ? nameOf(m, m.worst7) : "none", m.worst7 ? "critical in " + m.worst7.critical + " of " + m.hist.length + " runs" : "no DC critical"),
    ];
    return sec("At a glance", null, null, [el("div", "tiles adp-tiles", t)]);
  }
  function dcTable(a, m) {
    const sws = swOf(a.sw), n = m.times.length;
    const swRank = d => { const s = swFor(sws, d, m); return s && s[1] === "critical" ? 0 : s && s[1] === "warning" ? 1 : 2; };
    const dcs = m.dcs.slice().sort((x, y) => (RANK[x.now] - RANK[y.now]) || (swRank(x) - swRank(y)) || x.dc.localeCompare(y.dc));
    const rows = dcs.map(d => {
      const s = swFor(sws, d, m);
      const why = d.worst || (s && s[1] === "critical" ? "SolarWinds: " + s[0] + ", AD answers" : "");
      const tr = el("tr", d.now !== "ok" ? "r-" + d.now : "");
      tr.dataset.dc = idOf(d);
      const strip = el("span", "adp-trend");
      strip.setAttribute("role", "img");
      strip.setAttribute("aria-label", d.dc + ": the last " + n + " runs, " + d.trend.filter(x => x && x !== "ok").length + " not OK");
      d.trend.forEach((x, k) => { const i = el("i", "k-" + (x || "none")); i.title = (m.times[k] || "") + ": " + (WORD[x] || "no run"); strip.append(i); });
      const since = d.now !== "ok" ? "since " + hhmm(d.since_shown) + " · " + d.runs_since + (d.longer ? "+" : "") + (d.runs_since === 1 && !d.longer ? " run" : " runs")
                                    : d.runs_since >= n ? "OK all " + n + " runs" : "OK since " + hhmm(d.since_shown);
      tr.append(el("td", "adp-dc", [el("b", "", nameOf(m, d)), why ? el("div", "adp-why", why) : null]), el("td", "nw adp-wide", d.site || "-"),
                el("td", "nw", chip(d.now)), el("td", "nw", since), el("td", "", strip),
                el("td", "nw", s ? chip(s[1] || "unknown", s[0]) : el("span", "muted", a.sw.on ? "-" : "not read")),
                el("td", "adp-wide", why || el("span", "muted", "-")));
      return tr;
    });
    const leg = el("p", "adp-leg", [["ok", "OK"], ["warning", "Warning"], ["critical", "Critical"], ["none", "No run"]].map(([k, t]) => el("span", "", [el("i", "k-" + k), t])));
    return sec("Domain controllers", m.dcs.length, "worst first · a square is one run, oldest left",
               [table([["DC"], ["Site", "adp-wide"], ["Now"], ["Since"], ["Last 24 h (" + n + (n === 1 ? " run)" : " runs)")], ["SolarWinds"], ["What is wrong", "adp-wide"]], rows, "adp-dcs"), leg]);
  }
  // the newest run's replication coverage: repl_read of dcs DCs read (an older answer without it: all)
  // the whole population the replication read knows (also DCs AD lists that were not polled); read: in full
  const replOf = run => run.repl_dcs || run.dcs || 0;
  const replRead = run => run.repl_read == null || run.repl_read > 0;
  const replAll = run => run.repl_read == null || run.repl_read >= replOf(run);
  const replWhy = run => (run.repl_read || 0) + " of " + replOf(run) + " DCs' replication read in full (no rights on a partition, not reached, or not polled: the full report says which)";
  function linkTable(m) {
    // what was seen is shown whatever the coverage; the coverage is said beside it
    const part = !replRead(m.run) ? note("warn", "Replication was not read in full in this run: " + replWhy(m.run) + ". " +
                                            (m.links.length ? "The links below were seen; the others are not known." : "No link is known - not \"all fine\"."))
               : replAll(m.run) ? null : note("warn", "Only " + replWhy(m.run) + ": the others' links are not known.");
    if (!replRead(m.run) && !m.links.length) return sec("Replication links that need a look", null, "as of " + hhmm(m.run.as_of), [part]);
    if (!m.links.length) return sec("Replication links that need a look", 0, "as of " + hhmm(m.run.as_of),
                                    [part, el("p", "muted", part ? "None among the DCs whose replication was read." : "None: every replication link succeeded within the matrix's hours.")]);
    const rows = m.links.map(x => el("tr", RANK[x.now] < 2 ? "r-" + x.now : "", [
      el("td", "adp-dc", [el("b", "", x.frm), " → ", el("b", "", x.to), el("div", "adp-why", x.nc + (x.error ? " · " + x.error : ""))]),
      el("td", "nw adp-wide", x.nc), el("td", "nw", chip(x.now)), el("td", "nw adp-wide", x.last_success_shown || "never"),
      el("td", "nw", (x.failures > 0 ? "failing, no success since " : "late, no success since ") + (hhmm(x.last_success_shown) || "-")),
      el("td", "num", x.failures || "-"), el("td", "adp-wide", x.error || el("span", "muted", "-"))]));
    return sec("Replication links that need a look", m.links.length, "as of " + hhmm(m.run.as_of),
               [part, table([["From → to"], ["Partition", "adp-wide"], ["Now"], ["Last success", "adp-wide"], ["Since"], ["Failures", "num"], ["Error", "adp-wide"]], rows)]);
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
    const box = el("div", "card adp-chart");
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
        hit.addEventListener("mousemove", e => show(h, e));
        hit.addEventListener("mouseleave", () => tip.setAttribute("visibility", "hidden"));
      });
      // the card: in the SVG, placed with its own attributes - no inline style (the CSP)
      const tip = add("g", {class: "tipg", visibility: "hidden"}), bg = document.createElementNS(SVG, "rect");
      const t1 = document.createElementNS(SVG, "text"), t2 = document.createElementNS(SVG, "text");
      bg.setAttribute("rx", 8); t1.setAttribute("class", "tb"); t2.setAttribute("class", "tt");
      t1.setAttribute("x", 10); t1.setAttribute("y", 19); t2.setAttribute("x", 10); t2.setAttribute("y", 37);
      tip.append(bg, t1, t2);
      function show(h, e) {
        t1.textContent = h.at;
        t2.textContent = h.c + " DC" + (h.c === 1 ? "" : "s") + " critical · " + h.w + " warning";
        const w = Math.ceil(Math.max(t1.getComputedTextLength(), t2.getComputedTextLength())) + 20, hh = 46;
        bg.setAttribute("width", w); bg.setAttribute("height", hh);
        const r = s.getBoundingClientRect(), k = W / Math.max(1, r.width);
        const px = (e.clientX - r.left) * k, py = (e.clientY - r.top) * k;
        tip.setAttribute("transform", "translate(" + Math.max(4, Math.min(px + 12, W - w - 4)) + "," + Math.max(4, Math.min(py - hh - 10, H - hh - 4)) + ")");
        tip.setAttribute("visibility", "visible");
      }
      box.append(s);
    };
    chart.redraw = draw;                       // drawn once the page is in place (its width known): draw() below
    const runs = m.hist.slice(-8).reverse().map(h => el("tr", "", [el("td", "nw", h.at_shown), el("td", "nw", h.why ? chip("unknown", "Rows not kept") : chip(WORD[h.status] ? h.status : h.c ? "critical" : h.w ? "warning" : h.status ? "unknown" : "ok")),
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
    (m && m.dcs || []).forEach(d => { now[idOf(d)] = [d.now, (swFor(sws, d, m) || [])[0], nameOf(m, d)]; });
    if (last) {
      const out = [];
      Object.keys(now).forEach(id => {
        const w = last[id], v = now[id];
        if (!w) return;
        if (w[0] !== v[0]) out.push([v[2] + ": AD " + WORD[v[0]], "was " + WORD[w[0]], RANK[v[0]] < 2, id]);
        if (w[1] && v[1] && w[1] !== v[1]) out.push([v[2] + ": SolarWinds " + v[1], "was " + w[1], v[1] !== "Up", id]);
      });
      out.slice(0, 3).forEach(([t, was, bad, id]) => {
        const x = el("button", "x", "×");
        x.type = "button"; x.setAttribute("aria-label", "Close");
        const box = el("div", "toast adp-toast" + (bad ? " bad" : ""), [el("div", "", [el("b", "", t), el("div", "muted", was)]), x]);
        x.addEventListener("click", () => box.remove());
        while (toasts.children.length >= 3) toasts.firstChild.remove();
        toasts.append(box);
        setTimeout(() => box.remove(), 15000);
        const row = [...root.querySelectorAll("tr[data-dc]")].find(r => r.dataset.dc === id);
        if (row && moves()) row.classList.add("adp-flash");
      });
    }
    last = now;
  }

  // ---- the answer --------------------------------------------------------------------------------------------------------------
  function draw(a) {
    const db = a.db || {}, m = db.model;
    const parts = [];
    today = m && m.run && m.run.as_of ? String(m.run.as_of).slice(0, 10) : "";
    if (!db.on) {
      parts.push(note("warn", "The report database is off here: " + db.why + "."));
    } else if (!m) {
      parts.push(db.stale ? note("bad", "The report database could not be read: " + db.stale + ". Tried again every minute.")
                          : note("", "Reading the report database…"));
    } else if (!m.run && db.stale) {           // the last good read found none; this one failed: a run may be there now
      parts.push(note("bad", "The report database could not be read: " + db.stale + ". Its last answer (" + ago(db.age) + ") had no AD run; " +
                             "whether there is one now is not known. Tried again every minute."));
    } else if (!m.run) {
      parts.push(live(a, m), note("", "No AD run in the report database yet: the AD job keeps each run there from 1.101.2 on, when its template has the " +
                                       "\"Report database\" credential (the template setup job attaches it). The next run is the first."));
    } else {
      parts.push(live(a, m));
      if (m.run.why) parts.push(note("bad", "This run's DCs and links could not be kept: " + m.run.why + ". Its counts below are not the AD state."));
      if (db.stale) parts.push(note("warn", "The report database was not read just now (" + db.stale + "): this is its answer of " + ago(db.age) + ", as of its run " + m.run.as_of + "."));
      else if (db.refreshing) parts.push(note("", "Reading the report database again: this is its answer of " + ago(db.age) + " (nobody had the page open)."));
      if (m.run.old) parts.push(note("warn", "The newest AD run is " + hrs(m.run.age_min) + " old (more than " + a.old_after + " min): the AD job has not run since. " +
                                             "What follows is as of " + m.run.as_of + ", not now."));
      parts.push(glance(a, m), dcTable(a, m), linkTable(m), chart(m),
                 el("p", "adp-foot", "From the report database: the AD job's runs (ad_run, ad_dc, ad_link), read every minute while this page is open. " +
                                     "SolarWinds: read by this server every " + Math.round((a.sw.every || 600) / 60) + " min. Read only - nothing here changes AD."));
    }
    if (a.down) parts.unshift(note("bad", "The portal did not answer (" + a.down + "): this is its answer of " + ago(a.since) +
                                          ", not now. Tried again in " + poll + " s."));
    root.replaceChildren(...parts);
    if (m && m.run && chart.redraw && document.querySelector(".adp-chart")) chart.redraw();   // now: no frame to wait for (a background tab has none)
    said(a, m);
  }

  // the last answer as of now: its ages moved on (a run grows old past portal_ad_old_minutes), down: the portal not answering
  function aged(a, down) {
    const gone = Math.round((Date.now() - got) / 1000), a2 = JSON.parse(JSON.stringify(a));
    if (a2.sw && a2.sw.age != null) a2.sw.age += gone;
    if (a2.db && a2.db.age != null) a2.db.age += gone;
    const run = a2.db && a2.db.model && a2.db.model.run;
    if (run && run.age_min != null) {
      run.age_min += Math.floor(gone / 60);
      run.old = run.old || run.age_min > (a2.old_after || 90);
    }
    a2.down = down || "";
    a2.since = gone;
    return a2;
  }

  // an answer the page can draw: its parts there and of the right kind (anything else is a failed poll)
  const arr = x => Array.isArray(x);
  function usable(a) {
    if (!a || typeof a !== "object" || !a.db || typeof a.db !== "object" || (a.sw != null && typeof a.sw !== "object")) return false;
    const m = a.db.model;
    if (m == null) return true;
    if (typeof m !== "object") return false;
    if (m.run == null) return true;
    return typeof m.run === "object" && arr(m.dcs) && arr(m.links) && arr(m.hist) && arr(m.times) &&
           m.dcs.every(d => d && typeof d.dc === "string" && arr(d.trend));
  }
  const ASK_SECONDS = 20;                     // a poll, its body too, gets this long - then it is a failed poll (Not live)
  let etag = "", shown = null, got = Date.now(), timer = null, poll = 60, down = "", seq = 0;
  function ask() {
    const n0 = ++seq, ctl = window.AbortController ? new AbortController() : null;
    let late;
    const deadline = new Promise((_, no) => { late = setTimeout(() => { if (ctl) ctl.abort(); no(new Error("no answer in " + ASK_SECONDS + " s")); }, ASK_SECONDS * 1000); });
    const asked = fetch(API, {headers: etag ? {"If-None-Match": etag} : {}, credentials: "same-origin", cache: "no-store", signal: ctl ? ctl.signal : undefined})
      .then(r => {
        if (r.status === 304) return null;
        if (!r.ok) throw new Error("the portal answered " + r.status);
        // the body first: its tag and time are kept only with a body read whole (a cut body never leaves its tag behind)
        return r.json().then(a => ({a, tag: r.headers.get("ETag") || ""}));
      });
    Promise.race([asked, deadline]).then(got2 => {
      if (n0 !== seq) return;                   // an older poll that answered late: not this one's
      down = "";                                // an answer (200 or 304): the portal is there again
      if (got2) {
        const a = got2.a;
        if (!usable(a)) throw new Error("the portal's answer could not be read");
        try {
          draw(a);                              // drawn first: only an answer that can be shown replaces the last good one
        } catch (e) {
          throw new Error("the portal's answer could not be shown");
        }
        shown = a; etag = got2.tag; got = Date.now();
        // the first read not there yet, or an answer from before a quiet spell: asked again soon, not in a minute
        poll = a.db && a.db.on && (!a.db.model || a.db.refreshing) && !a.db.stale ? 3 : Math.max(15, Math.min(300, a.poll || 60));
      }
      else if (shown) {                         // the same answer: its ages move on
        last = null;                           // the same DCs: nothing to say
        draw(aged(shown));
      }
    }).catch(e => {
      if (n0 !== seq) return;
      down = e.message || "no answer";
      if (shown) {                              // the last answer, said as not now: not Live, its ages still growing
        last = null;
        try { draw(aged(shown, down)); } catch (_) { /* the last good one could not be drawn again: the next poll tries */ }
        return;
      }
      const n = document.getElementById("ad-off") || el("p", "note bad");
      n.id = "ad-off";
      n.textContent = "The portal did not answer (" + down + "): tried again in " + poll + " s.";
      if (!n.parentNode) root.prepend(n);
    }).then(again, again);                      // asked again whatever happened above
    function again() { clearTimeout(late); clearTimeout(timer); timer = setTimeout(ask, poll * 1000); }
  }
  window.adPage = {draw: d => { shown = d; draw(d); }};       // the tests
  ask();
})();
