// The portal's NetApp page (/portal/netapp, site_portal.py netapp_page): drawn from /api/netapp - NetApp - refresh volume
// list's newest run in the report database (the writer works out the states, the growth and the days; the portal only reads).
// No inline script: the page runs under script-src 'self'. Everything from the server goes in as text (textContent).
// Expand... opens the portal's own Run dialog (the expand template's form, the volume and Check preselected by the address).
"use strict";
(function () {
  const root = document.getElementById("na");
  if (!root) return;
  const SRC = root.getAttribute("data-src");
  const SVG = "http://www.w3.org/2000/svg";
  const WORD = {critical: "Critical", warning: "Warning", ok: "OK", unknown: "Unknown"};
  const MARK = {ok: [["path", {d: "M3.2 8.4l3 3 6.6-7"}]], bad: [["path", {d: "M4 4l8 8M12 4l-8 8"}]],
                warn: [["path", {d: "M8 3.2v5.6M8 11.6v.4"}]], skip: [["circle", {cx: 8, cy: 8, r: 5.4}], ["path", {d: "M5.6 8h4.8"}]]};
  const KIND = {ok: "ok", critical: "bad", warning: "warn", unknown: "skip"};
  let etag = "", timer = 0, showAll = false, perfBy = "lat", last = null, query = "";
  // 1.127.2: the site filter (?site= links a view); "" = every site
  let site = (new URLSearchParams(location.search).get("site") || "").toUpperCase();

  function el(tag, cls, kids) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    (Array.isArray(kids) ? kids : kids == null ? [] : [kids]).forEach((k) => {
      if (k != null && k !== false) e.append(typeof k === "object" ? k : String(k));
    });
    return e;
  }
  function mark(kind) {
    const s = document.createElementNS(SVG, "svg");
    s.setAttribute("class", "ic"); s.setAttribute("viewBox", "0 0 16 16"); s.setAttribute("aria-hidden", "true");
    (MARK[kind] || MARK.skip).forEach((p) => {
      const n = document.createElementNS(SVG, p[0]);
      const at = Object.assign({fill: "none", stroke: "currentColor", "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round"}, p[1]);
      Object.keys(at).forEach((k) => n.setAttribute(k, at[k]));
      s.append(n);
    });
    return s;
  }
  function chip(st, text) { const k = WORD[st] ? st : "unknown"; return el("span", "chip s-" + k, [mark(KIND[k]), text || WORD[k]]); }
  function table(head, rows, cls) {
    const t = el("table", cls || "");
    t.append(el("thead", "", el("tr", "", head.map((h) => { const th = el("th", h[1] || "", h[0]); th.scope = "col"; return th; }))));
    t.append(el("tbody", "", rows));
    return el("div", "tw", t);
  }
  function sec(id, title, count, kids) {
    const s = el("section", "sec", [el("h2", "", [title, count ? el("span", "count", count) : null])].concat(kids));
    s.id = id;
    return s;
  }
  function tile(k, v, n) { return el("div", "tile", [el("div", "k", k), el("div", "v", v), el("div", "n", n || "")]); }
  function td(cls, kids) { return el("td", cls, kids); }
  function tr(st, cells) { return el("tr", st && st !== "ok" ? "r-" + st : "", cells); }
  function fmt(n, d) { return n == null ? "-" : Number(n).toLocaleString("en-US", {minimumFractionDigits: d || 0, maximumFractionDigits: d || 0}); }
  function gb(b) { return b == null ? "-" : fmt(b / 1073741824, 0); }
  function tb(b) { return b == null ? "-" : fmt(b / 1099511627776, 1); }
  function size(b) { return b >= 1099511627776 ? fmt(b / 1099511627776, 2) + " TB" : fmt(b / 1073741824, 1) + " GB"; }
  function brk(t, at) { return String(t || "").split(at).join(at + "​"); }
  function sub(text, cls) { return el("span", "sub2" + (cls ? " " + cls : ""), text); }
  function dur(s) {
    if (s == null) return "-";
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    return d ? d + " d " + h + " h" : h ? h + " h" + (m ? " " + m + " min" : "") : m + " min";
  }
  function notRead(errors) { return (errors || []).length ? el("p", "more-line nap-nr", "Not read: " + errors.join("; ")) : null; }

  // ---- the live line
  function live(m, stale) {
    const r = m.run;
    const dot = el("span", "adp-dot" + (r.old || stale ? " off" : ""), "");
    const l = [dot, el("b", "", r.old || stale ? "Not current" : "Live"),
      el("span", "", "As of " + r.as_of + " (NetApp - refresh volume list" + (r.job ? ", job " + r.job : "") + ")")];
    if (r.old) l.push(el("span", "c-warning", "older than " + (last.old_after || 60) + " min: is the refresh's schedule on?"));
    if (stale) l.push(el("span", "c-warning", stale));
    return el("div", "card strip adp-live", [el("div", "l", l)]);
  }

  // ---- At a glance
  function glance(m) {
    const o = m.run.opts || {}, P = m.parts || {};
    const vols = m.volumes || [];
    const warn = vols.filter((v) => v.pct >= (o.volume_warn_pct || 85));
    const crit = vols.filter((v) => v.pct >= (o.volume_crit_pct || 90));
    const least = vols.slice().sort((a, b) => (b.pct - a.pct) || (a.name < b.name ? -1 : 1))[0];
    const t = [];
    const cl = (P.clusters || {}).rows;
    if (cl) {
      const ok = cl.filter((c) => c.st === "ok").length, bad = cl.filter((c) => c.st !== "ok")[0];
      t.push(tile("Clusters healthy", [el("span", "big", ok + " of " + cl.length), chip(ok === cl.length ? "ok" : (bad || {}).st)],
                  bad ? bad.name + ": " + bad.why : "every node up, no failed disk"));
    } else {
      t.push(tile("Clusters read", [el("span", "big", String((m.run.clusters || []).filter((c) => !c.error).length) + " of " + (m.run.clusters || []).length)],
                  "health not read (ontap_live_health)"));
    }
    t.push(tile("How full over " + fmt(o.volume_warn_pct || 85) + " %", [el("span", "big", String(warn.length)), chip(warn.length ? "warning" : "ok")],
                "of " + vols.length + " LUNs and volumes"));
    t.push(tile("How full over " + fmt(o.volume_crit_pct || 90) + " %", [el("span", "big", String(crit.length)), chip(crit.length ? "critical" : "ok")],
                crit.slice(0, 3).map((v) => v.name).join(", ") + (crit.length > 3 ? " …" : "")));
    // 1.127.2 (user): the least free one, not the days until full
    t.push(tile("Least free", least ? [el("span", "big", fmt(Math.max(0, 100 - Math.min(100, least.pct)), 1) + " % free"),
                chip(least.pct >= (o.volume_crit_pct || 90) ? "critical" : least.pct >= (o.volume_warn_pct || 85) ? "warning" : "ok")] : [el("span", "big", "-")],
                least ? least.name + (least.ds ? " (" + least.ds + ")" : "") + ", " + gb(least.free) + " GB free" : "nothing read"));
    const sm = P.snapmirror;
    if (sm) {
      const b = sm.rows.filter((x) => x.st === "critical")[0], w = sm.rows.filter((x) => x.st === "warning")[0];
      t.push(tile("SnapMirror broken", [el("span", "big", String(sm.counts.broken)), chip(sm.counts.broken ? "critical" : "ok")], b ? b.src + " → " + b.dst : "of " + sm.counts.all));
      t.push(tile("SnapMirror lagging", [el("span", "big", String(sm.counts.lagging)), chip(sm.counts.lagging ? "warning" : "ok")],
                  w ? dur(w.lag) + " behind" + (w.sched ? " a " + w.sched + " schedule" : "") : "within schedule"));
    }
    const dk = P.disks;
    if (dk) {
      const bad = dk.rows.filter((d) => d.st === "critical")[0];
      t.push(tile("Disks failed / spares", [el("span", "big", dk.counts.failed + " / " + dk.counts.spares), chip(bad ? "critical" : dk.rows.some((d) => d.st === "warning") ? "warning" : "ok")],
                  bad ? bad.node + ": " + bad.why : "no failed disk"));
    }
    const em = P.ems;
    if (em) {
      t.push(tile("ONTAP errors, " + fmt(em.counts.hours) + " h", [el("span", "big", String(em.counts.events)), chip(em.rows.some((e) => e.st === "critical") ? "critical" : em.counts.events ? "warning" : "ok")],
                  em.counts.kinds ? em.counts.kinds + " kinds; newest " + ((em.rows[0] || {}).shown || "") : "none"));
    }
    return sec("glance", "At a glance", null, [el("div", "tiles nap-tiles", t)]);
  }

  // ---- Expand...
  function expandCell(v) {
    if (v.x && v.x.length) {
      const b = el("a", "btn sm pri nap-x", "Expand…");
      b.href = v.x[0].href;
      b.setAttribute("data-expand", v.k);
      b.title = "NetApp - expand a volume, with " + v.name + " picked" + (last.model.expand.job_type ? " and Job type Check" : "");
      return td("nap-act", b);
    }
    if (v.dis) {
      const b = el("button", "btn sm nap-x", "Expand…");
      b.type = "button"; b.disabled = true; b.title = v.off;
      return td("nap-act", [b, el("span", "notoff", v.off)]);
    }
    return td("nap-act", el("span", "notoff", v.off || ""));
  }
  async function openExpand(a, v) {
    const dlg = document.getElementById("dlg");
    if (!dlg || !dlg.showModal || typeof fetchFrag !== "function") { location.assign(a.href); return; }
    try {
      const n = await fetchFrag(a.href + "&embed=1");
      dlg.replaceChildren(n);
      const f = n.querySelector("#run");
      if (f) {
        initRun(f);
        const sel = f.querySelector("select");
        const note = el("p", "hint", "From the list of " + last.model.run.as_of + " - " + v.full_text +
                        ". The job reads the LUN and the volume live again before it plans.");
        if (sel) {
          sel.closest(".field").append(note);
          const go = f.querySelector("#go");
          const blank = () => { if (go) go.disabled = !sel.value; };        // never a launch without the volume: AAP would use its default
          if (!sel.value) note.textContent = "This volume is not in the job's current list: pick it again from the list (the refresh rewrites it).";
          sel.addEventListener("change", blank);
          blank();
        }
        const ag = (last.model.aggrs || []).filter((x) => x.a === v.aggr && x.addr === v.addr)[0];
        if (ag) {
          const o = last.model.run.opts || {};
          f.querySelector(".btns").before(el("p", "muted-line", v.aggr + " is at " + fmt(ag.pct, 1) + " % used. The job grows the volume only if the aggregate stays at or under " +
            fmt(o.aggr_max_pct || 97) + " % (ontap_expand_aggr_max_pct)."));
        }
      }
      dlg.showModal();
    } catch (e) {
      location.assign(a.href);
    }
  }
  root.addEventListener("click", (ev) => {
    const a = ev.target.closest && ev.target.closest("a[data-expand]");
    if (a) {
      ev.preventDefault();
      const v = (last.model.volumes || []).filter((x) => x.k === a.getAttribute("data-expand"))[0];
      openExpand(a, v || {});
      return;
    }
    const st = ev.target.closest && ev.target.closest("button[data-site]");
    if (st) { pickSite(st.getAttribute("data-site")); return; }
    const s = ev.target.closest && ev.target.closest("button[data-all]");
    if (s) { showAll = !showAll; draw(); return; }
    const p = ev.target.closest && ev.target.closest("button[data-by]");
    if (p) { perfBy = p.getAttribute("data-by"); draw(); }
  });
  root.addEventListener("change", (ev) => { if (ev.target.id === "nap-site") pickSite(ev.target.value); });
  root.addEventListener("input", (ev) => {
    if (ev.target.id !== "nap-q") return;
    query = ev.target.value;
    const at = ev.target.selectionStart;
    draw();
    const n = document.getElementById("nap-q");
    if (n) { n.focus(); try { n.setSelectionRange(at, at); } catch (e) { /* a search box without a caret */ } }
  });
  document.addEventListener("click", (ev) => {
    const dlg = document.getElementById("dlg");
    if (!dlg) return;
    if (ev.target === dlg) { dlg.close(); return; }
    const c = ev.target.closest && ev.target.closest("[data-close]");
    if (c && c.closest("dialog") === dlg) { ev.preventDefault(); dlg.close(); }
  });

  // ---- Space runway
  function runway(m) {
    const o = m.run.opts || {};
    const all = m.volumes || [];
    const look = all.filter((v) => v.ds || v.st !== "ok" || v.pct >= (o.volume_warn_pct || 85) || (v.days != null && v.days <= (o.list_days || 90)));
    const q = query.trim().toLowerCase();
    const vols = q ? all.filter((v) => (v.ds + " " + v.name + " " + v.svm + " " + v.aggr).toLowerCase().indexOf(q) >= 0) : showAll ? all : look;
    const vcWhy = !o.vmware ? "not read - attach the VMware vCenter credential to NetApp - refresh volume list"
      : o.vc_error ? "not read - vCenter: " + o.vc_error : "";
    const row = (v) => {
      const ds = (n) => { const x = el("span", "nap-ds", n); x.title = n; return x; };          // one line; the full name on hover
      return tr(v.st, [td("", chip(v.st)),
        td("", [el("b", "mono", v.name), sub(v.svm + " · " + v.c), sub((v.ds ? v.ds : "no datastore") + " · " + v.aggr, "nap-narrow")]),
        td("nap-wide nap-dscol", v.ds ? [ds(v.ds), sub(v.kind + (v.lun ? (v.thick ? ", thick LUN" : ", thin LUN") : ""))]
          : [el("span", "muted", vcWhy || "no datastore on it"), v.lun ? sub(v.thick ? "thick LUN" : "thin LUN") : null]),
        td("nap-wide mono", v.aggr),
        (() => { const c = td("num", [fmt(Math.min(100, v.pct), 1), sub(v.src)]); if (v.pct > 100) c.title = fmt(v.pct, 1) + " % (more written than its size: ONTAP counts its metadata)"; return c; })(), td("num", gb(v.free)),
        (() => { const c = td("num", v.g == null ? el("span", "muted", "-") : (v.g > 0 ? "+" : "") + fmt(v.g, 1)); if (v.g == null && v.rate_why) c.title = v.rate_why; return c; })(),
        expandCell(v)]);
    };
    // VMware datastores first, then the rest (the reader's order); a thin heading over each group when both are there
    const onDs = vols.filter((v) => v.ds), others = vols.filter((v) => !v.ds);
    const grp = (t) => { const th = el("th", "", t); th.colSpan = 8; th.scope = "colgroup"; return el("tr", "nap-grp", th); };
    const rows = onDs.length && others.length
      ? [grp("VMware datastores (" + onDs.length + ")")].concat(onDs.map(row), [grp("Other volumes and LUNs (" + others.length + ")")], others.map(row))
      : vols.map(row);
    const ex = m.expand;
    const lo = m.left_out || {};
    const more = (showAll ? "All " + all.length : look.length + " of " + all.length) + " LUNs and volumes" +
      (showAll ? "." : ": the VMware datastores, and the ones over " + fmt(o.volume_warn_pct || 85) + " % or full within " + fmt(o.list_days || 90) + " days (ontap_live_list_days).") +
      (lo.roots || lo.small ? " Left out: " + [lo.roots ? lo.roots + " root volumes" : "", lo.small ? lo.small + " under " + fmt(o.min_gb) + " GB (ontap_volume_min_gb)" : ""].filter(Boolean).join(", ") + "." : "");
    const aggrRows = (m.aggrs || []).map((a) => tr(a.st, [td("", chip(a.st)), td("", [el("b", "mono", a.a), sub(a.node + " · " + a.c)]),
      td("num", fmt(a.pct, 1)), td("num", tb(a.free)), td("num nap-wide", size(a.size)), td("num", a.g == null ? "-" : (a.g > 0 ? "+" : "") + fmt(a.g))]));
    // 1.128.0 (user): no days-until columns for the aggregates here - the health report keeps them
    const need = all.filter((v) => v.st !== "ok").length, needA = (m.aggrs || []).filter((a) => a.st !== "ok").length;
    const toggle = el("button", "btn sm", showAll ? "Show the ones that need a look" : "Show all " + all.length);
    toggle.type = "button"; toggle.setAttribute("data-all", "1");
    const search = el("input", "nap-q");
    search.type = "search"; search.id = "nap-q"; search.value = query; search.placeholder = "Datastore, volume, SVM or aggregate";
    search.setAttribute("aria-label", "Find a datastore, volume, SVM or aggregate");
    return sec("runway", "Space runway", need + " LUNs or volumes, " + needA + " aggregates need a look", [
      el("div", "ctrl", [el("p", "h3", "LUNs and volumes: VMware datastores first, each least free first"), el("div", "nap-tools", [search, toggle])]),
      table([["Now"], ["Volume / LUN"], ["Datastore", "nap-wide"], ["Aggregate", "nap-wide"], ["Used %", "num"], ["Free GB", "num"],
             ["Growth GB/day", "num"], ["", "actcol"]], rows, "nap-vols"),
      el("p", "more-line", q ? vols.length + " of " + all.length + " match \u201c" + query.trim() + "\u201d." : more),
      vcWhy ? el("p", "more-line nap-nr", "Datastores " + vcWhy + ".") : null,
      el("p", "more-line", "A volume that holds a LUN is judged by its LUN: the datastore's own used space when vCenter was read, else the LUN's blocks written. " +
         "Expand… opens " + ex.template + " with it picked" + (ex.job_type ? " and Job type Check" : "") + "." + (ex.on ? "" : " Off here: " + ex.why + ".")),
      el("p", "h3", "Aggregates, the ones that need a look first"),
      table([["Now"], ["Aggregate"], ["Used %", "num"], ["Free TB", "num"], ["Size", "num nap-wide"], ["Growth GB/day", "num"]], aggrRows),
      el("p", "more-line", fmt(o.aggr_max_pct || 97) + " % is ontap_expand_aggr_max_pct: past it, the expand job refuses to grow a volume on that aggregate. " +
         "Growth: the slope of the points of the last " + fmt(o.growth_days || 7) + " days (ontap_live_growth_days), once they span " + fmt(o.min_hours || 6) + " h.")
    ]);
  }

  // ---- SnapMirror
  function snapmirror(m) {
    const sm = (m.parts || {}).snapmirror;
    if (!sm) return sec("snapmirror", "SnapMirror", null, [el("p", "more-line", "Not read: ontap_live_health is off for NetApp - refresh volume list.")]);
    const o = m.run.opts || {};
    const rows = sm.rows.map((s) => tr(s.st === "ok" ? "" : s.st, [td("", chip(s.st, s.word)),
      td("", [el("span", "mono brk path", brk(s.src, ":")), el("span", "mono brk path", ["→ ", brk(s.dst, ":")]), sub("read on " + s.dc)]),
      td("", [String(s.state || "").replace(/[_-]/g, " "), s.why ? sub(s.why) : null]),
      td("num", [dur(s.lag), s.per ? sub(fmt(s.lag / s.per, 1) + "x the schedule") : null]), td("", s.sched || "-"), td("nw", s.shown || "-")]));
    const shown = sm.rows.length, n = sm.counts.all;
    return sec("snapmirror", "SnapMirror", sm.counts.broken + " broken, " + sm.counts.lagging + " lagging of " + n, [
      table([["Now"], ["Source → destination"], ["State"], ["Lag", "num"], ["Schedule"], ["Last transfer"]], rows),
      el("p", "more-line", (shown < n ? shown + " of " + n + " relationships, problems first." : "") +
         " Lagging: over 2x its schedule, or over " + fmt(o.lag_warn_hours || 24) + " h (ontap_snapmirror_lag_warn_hours); broken or over " +
         fmt(o.lag_crit_hours || 48) + " h is critical. Read on the destination cluster."),
      notRead(sm.errors)]);
  }

  // ---- Health
  function health(m) {
    const P = m.parts || {};
    if (!P.nodes) return sec("health", "Health", null, [el("p", "more-line", "Not read: ontap_live_health is off for NetApp - refresh volume list.")]);
    const nodeRows = P.nodes.rows.map((n) => tr(n.st, [td("", chip(n.st, n.state === "up" ? "Up" : n.state)), td("", [el("span", "mono", n.node), sub(n.model + " · " + n.cluster)]),
      td("mono", n.partner || "-"), td("", n.takeover), td("num", n.up_s == null ? "-" : fmt(n.up_s / 86400) + " d")]));
    const diskRows = P.disks.rows.map((d) => tr(d.st, [td("", chip(d.st)), td("", [el("span", "mono", d.node), d.why ? sub(d.why) : sub(d.cluster)]),
      td("num", el("span", d.failed ? "c-critical" : "", String(d.failed))), td("num", String(d.rebuild)),
      td("num", el("span", d.spares === 0 && !d.shared ? "c-critical" : "", String(d.spares)))]));
    const emsRows = P.ems.rows.map((e) => tr("", [td("nw", e.shown || ""), td("", [el("span", "mono brk", brk(e.ev, ".")), sub(e.node + (e.n > 1 ? " · " + e.n + " times" : ""))]),
      td("", chip(e.st, e.sev)), td("", e.msg)]));
    const o = m.run.opts || {};
    const em = P.ems.counts;
    return sec("health", "Health", ((P.clusters || {}).rows || []).length + " clusters, " + P.nodes.counts.all + " nodes", [
      el("div", "grid2", [
        el("div", "", [el("p", "h3", "Nodes and HA pairs"), table([["Now"], ["Node"], ["HA partner"], ["Takeover possible"], ["Up", "num"]], nodeRows), notRead(P.nodes.errors)]),
        el("div", "", [el("p", "h3", "Disks by node"), table([["Now"], ["Node"], ["Failed", "num"], ["Rebuilding", "num"], ["Spares", "num"]], diskRows),
                       el("p", "more-line", "Fewer spares than ontap_min_spares (" + fmt(o.min_spares) + ") is amber, none or a broken disk red."), notRead(P.disks.errors)])
      ]),
      el("p", "h3", "ONTAP errors, last " + fmt(em.hours) + " h, newest first"),
      emsRows.length ? table([["Time"], ["Event"], ["Severity"], ["What ONTAP said"]], emsRows) : el("p", "more-line", "No emergency, alert or error event."),
      el("p", "more-line", "Emergency, alert and error events (ontap_ems_hours); the same event on the same node counted once" +
         (em.left_out ? "; " + em.left_out + " left out by ontap_ems_ignore / ontap_ems_ignore_ldap." : ".")),
      notRead(P.ems.errors)]);
  }

  // ---- Performance
  function perf(m) {
    const pf = (m.parts || {}).perf;
    if (!pf) return sec("perf", "Performance", null, [el("p", "more-line", "Not read: ontap_live_perf is off for NetApp - refresh volume list.")]);
    const o = m.run.opts || {};
    const rows = pf.rows.slice().sort((a, b) => perfBy === "iops" ? (b.iops || 0) - (a.iops || 0) : (b.lat || 0) - (a.lat || 0)).slice(0, o.perf_top || 10);
    const body = rows.map((p) => tr(p.st, [td("", chip(p.st)),
      td("", [el("b", "mono", p.vol + (p.lun ? "/" + p.lun : "")), sub(p.svm + " · " + p.cluster + (p.lun ? " · LUN" : " · NFS"))]),
      td("", p.ds ? p.ds : el("span", "muted", "none")),
      td("num", el("span", p.st === "ok" ? "" : "c-" + p.st, fmt(p.lat, 1))), td("num", fmt(p.iops)), td("num", fmt(p.mbs))]));
    const seg = el("div", "seg2", [["lat", "By latency"], ["iops", "By IOPS"]].map((x) => {
      const b = el("button", "", x[1]); b.type = "button"; b.setAttribute("data-by", x[0]); b.setAttribute("aria-pressed", String(perfBy === x[0])); return b;
    }));
    return sec("perf", "Performance", "busiest " + rows.length + " of " + pf.counts.all + ", ONTAP's latest sample" + (pf.counts.sample === "PT15S" ? " (15 s)" : ""), [
      el("div", "ctrl", [el("span", "lbl", "Busiest"), seg]),
      table([["Now"], ["Volume / LUN"], ["Datastore"], ["Latency ms", "num"], ["IOPS", "num"], ["MB/s", "num"]], body),
      el("p", "more-line", "Warning from " + fmt(o.lat_warn_ms) + " ms, critical from " + fmt(o.lat_crit_ms) + " ms (ontap_perf_latency_warn_ms, ontap_perf_latency_crit_ms). " +
         "A sample of the moment the refresh ran, not an average."),
      notRead(pf.errors)]);
  }

  // ---- the site filter (1.127.2): every part follows it, the tiles too
  function bySite(m) {
    if (!site || !(m.sites || []).some((x) => x.site === site)) return m;
    const on = (r) => r.site === site;
    const P = {};
    Object.keys(m.parts || {}).forEach((k) => {
      const b = m.parts[k], rows = (b.rows || []).filter(on), c = Object.assign({}, b.counts || {});
      if (k === "snapmirror") Object.assign(c, {all: rows.length, broken: rows.filter((r) => /broken/i.test(r.state || "") || r.st === "critical").length,
                                                lagging: rows.filter((r) => r.st === "warning").length});
      if (k === "disks") Object.assign(c, {failed: rows.reduce((n, r) => n + (r.failed || 0), 0), spares: rows.reduce((n, r) => n + (r.spares || 0), 0), nodes: rows.length});
      if (k === "nodes") Object.assign(c, {all: rows.length, down: rows.filter((r) => r.state !== "up").length});
      if (k === "ems") Object.assign(c, {events: rows.reduce((n, r) => n + (r.n || 1), 0), kinds: new Set(rows.map((r) => r.ev)).size});
      if (k === "perf") c.all = rows.length;
      P[k] = Object.assign({}, b, {rows: rows, counts: c});
    });
    return Object.assign({}, m, {volumes: (m.volumes || []).filter(on), aggrs: (m.aggrs || []).filter(on), parts: P});
  }
  function siteBar(m) {
    const ss = m.sites || [];
    if (!ss.length) return null;
    const all = (m.volumes || []).length;
    const opts = [["", "All (" + all + ")"]].concat(ss.map((x) => [x.site, x.site + " (" + x.n + ")"]));
    const seg = el("div", "seg2 nap-sites-b", opts.map((x) => {
      const b = el("button", "", x[1]); b.type = "button"; b.setAttribute("data-site", x[0]); b.setAttribute("aria-pressed", String(site === x[0])); return b;
    }));
    seg.setAttribute("role", "group"); seg.setAttribute("aria-label", "Site");
    const sel = el("select", "nap-q nap-sites-s", opts.map((x) => { const o = el("option", "", x[1]); o.value = x[0]; o.selected = site === x[0]; return o; }));
    sel.id = "nap-site"; sel.setAttribute("aria-label", "Site");
    return el("div", "ctrl nap-sites", [el("p", "h3", "Site"), seg, sel,
      el("span", "more-line nap-sites-n", "Every part of the page follows it. The cluster's (or SVM's) name gives the site (ontap_live_site_pattern).")]);
  }
  function pickSite(v) {
    site = v || "";
    const u = new URL(location.href);
    if (site) u.searchParams.set("site", site); else u.searchParams.delete("site");
    history.replaceState(null, "", u.pathname + u.search + u.hash);
    draw();
  }

  function draw() {
    const d = last;
    if (!d) return;
    const kids = [];
    if (!d.model) {
      kids.push(el("p", "note" + (d.loading ? "" : " warn"), d.message || "Nothing to show."));
    } else {
      const m0 = d.model, m = bySite(m0);
      kids.push(live(m, d.stale));
      const sb = siteBar(m0);
      if (sb) kids.push(sb);
      if ((m.run.not_read || []).length) kids.push(el("p", "note warn", "Not read by the refresh: " + m.run.not_read.join("; ")));
      kids.push(glance(m), runway(m), snapmirror(m), health(m), perf(m));
      kids.push(el("p", "adp-foot", "Read from the report database (the refresh job's newest run, times in " + m.run.zone + "). GET only on NetApp; Expand… only opens the existing job."));
    }
    root.replaceChildren.apply(root, kids);
  }

  async function load() {
    clearTimeout(timer);
    let wait = 60;
    try {
      const r = await fetch(SRC, {credentials: "same-origin", cache: "no-store", headers: etag ? {"If-None-Match": etag} : {}});
      if (r.status === 200) {
        etag = r.headers.get("ETag") || "";
        last = await r.json();
        draw();
        wait = last.loading ? 3 : last.poll || 60;
      } else if (r.status !== 304) {
        last = {message: "The page could not ask the portal (HTTP " + r.status + "): it tries again in a minute."};
        draw();
      } else if (last) {
        wait = last.poll || 60;
      }
    } catch (e) {
      last = last || {message: "The portal did not answer: it tries again in a minute."};
      draw();
    }
    timer = setTimeout(() => { if (!document.hidden) load(); else document.addEventListener("visibilitychange", load, {once: true}); }, wait * 1000);
  }
  load();
})();
