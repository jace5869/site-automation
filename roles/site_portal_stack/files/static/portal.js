// The site portal's pages (roles/site_portal_stack/files/site_portal.py serves them). No inline script anywhere: the
// pages run under script-src 'self'. Everything from the service or AAP goes into the page as text (textContent),
// never as HTML. A POST carries the page's token (X-Portal-Token) and goes to this site only.
"use strict";

const $ = (id) => document.getElementById(id);
const meta = (n) => { const m = document.querySelector('meta[name="' + n + '"]'); return m ? m.content : ""; };
const BASE = meta("portal-base") || "/portal/";
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = String(text);
  return e;
};
// a status as a word and a drawn mark, never a colour alone (the same marks as site_portal.py ICONS)
const SVG = "http://www.w3.org/2000/svg";
const ICON = {
  ok: [["path", {d: "M3.2 8.4l3 3 6.6-7", "stroke-linejoin": "round"}]],
  bad: [["path", {d: "M4 4l8 8M12 4l-8 8"}]],
  run: [["circle", {cx: 8, cy: 8, r: 5.2, "stroke-dasharray": "20 12"}]],
  wait: [["circle", {cx: 8, cy: 8, r: 5.4}], ["path", {d: "M8 5v3.3l2 1.3"}]],
  skip: [["circle", {cx: 8, cy: 8, r: 5.4}], ["path", {d: "M5.6 8h4.8"}]],
  arrow: [["path", {d: "M6 3.5L10.5 8 6 12.5", "stroke-linejoin": "round"}]],                                     // 1.92.0: the report button
  lock: [["rect", {x: 3.4, y: 7, width: 9.2, height: 6.2, rx: 1.5}], ["path", {d: "M5.5 7V5.4a2.5 2.5 0 015 0V7"}]],   // 1.92.0: may not decide
};
function icon(kind) {
  const s = document.createElementNS(SVG, "svg");
  s.setAttribute("class", "ic");
  s.setAttribute("viewBox", "0 0 16 16");
  s.setAttribute("aria-hidden", "true");
  for (const [tag, at] of ICON[kind] || ICON.skip) {
    const n = document.createElementNS(SVG, tag);
    n.setAttribute("fill", "none");
    n.setAttribute("stroke", "currentColor");
    n.setAttribute("stroke-width", "1.7");
    n.setAttribute("stroke-linecap", "round");
    for (const [k, v] of Object.entries(at)) n.setAttribute(k, v);
    s.append(n);
  }
  return s;
}
// status -> [icon, colour class, word]
const STATUS = {
  successful: ["ok", "s-ok", "Succeeded"], failed: ["bad", "s-critical", "Failed"], error: ["bad", "s-critical", "Error"],
  canceled: ["skip", "s-info", "Canceled"], running: ["run", "s-run", "Running"], pending: ["wait", "s-warning", "Pending"],
  waiting: ["wait", "s-warning", "Waiting"], new: ["wait", "s-warning", "New"], "not run": ["skip", "s-info", "Not run"],
};
function chip(status, word) {
  const s = STATUS[status] || ["skip", "s-info", status || "unknown"];
  const c = el("span", "chip " + s[1]);
  c.append(icon(s[0]), el("span", "", word || s[2]));
  return c;
}
function mark(kind, cls, word) {      // a chip that is not a job's status (Fixed, Still there, Not checked)
  const c = el("span", "chip " + cls);
  c.append(icon(kind), el("span", "", word));
  return c;
}
// times come from the service as the report pages show them (report_time_zone, 24 h): "2026-11-15 20:06 CET"
function when(text) {
  return text || "";
}
function took(sec) {
  if (sec === null || sec === undefined || sec === "") return "";
  const s = Math.round(Number(sec));
  if (!isFinite(s)) return "";
  return s < 60 ? s + " s" : s < 3600 ? Math.floor(s / 60) + " min " + (s % 60) + " s" : Math.floor(s / 3600) + " h " + Math.floor((s % 3600) / 60) + " min";
}
async function call(path, opts) {
  const r = await fetch(BASE + path, Object.assign({credentials: "same-origin", cache: "no-store"}, opts || {}));
  const body = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(body.error || ("HTTP " + r.status)); e.body = body; e.status = r.status; throw e; }
  return body;
}
function post(path, data) {
  return call(path, {method: "POST", headers: {"Content-Type": "application/json", "X-Portal-Token": meta("portal-token")},
                     body: JSON.stringify(data || {})});
}
function facts(dl, pairs) {
  dl.replaceChildren();
  for (const [k, v] of pairs) {
    if (v === "" || v === null || v === undefined) continue;
    const d = el("div");
    const dd = el("dd");
    if (v instanceof Node) dd.append(v); else dd.textContent = String(v);
    d.append(el("dt", "", k), dd);
    dl.append(d);
  }
}

// ---- the confirm page: Run sends the values; the service checks every one again
const qa = (node, sel) => Array.from(Element.prototype.querySelectorAll.call(node, sel));
function actionPage() {
  const f = $("run");
  if (f) initRun(f);
}
function initRun(f) {                  // the confirm page's form, or the Run dialog's (the same handler)
  const go0 = $("go"), jts = qa(f, 'input[name="portal-job-type"]');
  const label = () => { const c = jts.filter((x) => x.checked)[0]; if (go0 && c) go0.textContent = c.value === "check" ? "Launch as Check" : "Launch job"; };
  jts.forEach((x) => x.addEventListener("change", label));
  label();
  EventTarget.prototype.addEventListener.call(f, "submit", async (ev) => {          // a survey variable can be named like a form property: the form is read with the prototypes
    ev.preventDefault();
    const go = $("go");
    if (go.disabled) return;
    const vars = Object.create(null);          // a survey variable can be called constructor or toString: it is data, never an inherited property
    for (const x of qa(f, "input[type=number]")) if (x.validity && x.validity.badInput) { $("state").textContent = "A number is not finished."; $("state").className = "state bad"; x.focus(); return; }
    for (const x of qa(f, "input, select, textarea")) {
      if (!x.name || x.name === "portal-token" || x.name === "portal-sig" || x.name === "portal-job-type" || x.disabled) continue;
      if (x.type === "checkbox") { if (!vars[x.name]) vars[x.name] = []; if (x.checked) vars[x.name].push(x.value); }
      else if (x.dataset.pick) { if (!vars[x.name]) vars[x.name] = []; vars[x.name].push(x.value); }
      else vars[x.name] = x.value;
    }
    for (const b of qa(f, ".picker[data-on]")) { const n = b.dataset.picker; if (!vars[n]) vars[n] = []; }
    for (const w of qa(f, ".field")) { w.classList.remove("bad"); const y = w.querySelector(".why"); if (y) y.textContent = ""; }
    go.disabled = true;
    $("state").textContent = "Starting…";
    try {
      const sg = qa(f, 'input[name="portal-sig"]')[0];
      const jt = qa(f, 'input[name="portal-job-type"]:checked')[0];          // the Job type (a template that asks it at launch)
      const r = await post("api/actions/" + encodeURIComponent(Element.prototype.getAttribute.call(f, "data-action")), {vars, sig: sg ? sg.value : undefined, job_type: jt ? jt.value : undefined});
      rememberRunning(r.job_id);
      $("state").textContent = "Started: job " + r.job_id + (r.warning ? ". " + r.warning : "") + ". Opening it…";
      location.assign(r.url);
    } catch (e) {
      const p = (e.body && e.body.problems) || {};
      for (const [k, why] of Object.entries(p)) {
        const x = qa(f, '[name="' + CSS.escape(k) + '"]')[0];
        if (x) { const w = x.closest(".field"); const y = w && w.querySelector(".why"); if (y) { w.classList.add("bad"); y.textContent = (w.querySelector("label").textContent + " " + why); } }
      }
      $("state").textContent = e.message;
      $("state").className = "state bad";
      go.disabled = false;
    }
  });
}

// ---- 1.87.0: motion (portal.css, html.moves - portal-theme.js sets it from portal_motion and the system's reduced-motion request). Counts go up on
// the first load and bump when they change later; a running row counts its run time; the job's ring moves on from its last fill; a skeleton shows the
// next page's shape while it loads. All of it is made of elements and attributes - no HTML from strings, no inline style.
const moves = () => document.documentElement.classList.contains("moves");
const TOAST_MS = 8000;                        // a toast that closes by itself (1.92.0: 8 s, was 12); portal.css draws its time left over the same 8 s
const TOAST_SHOWN = 2;                        // 1.92.0: toasts shown at once; the rest wait ("+N more")
function bump(node) {
  if (!moves()) return;
  node.classList.remove("mv-bump");
  void node.offsetWidth;                      // so the bump starts again
  node.classList.add("mv-bump");
}
function countUp(node) {                      // "All 12" -> All 0 ... All 12; what is not a number stays
  const tn = Array.from(node.childNodes).find((n) => n.nodeType === 3 && /\d/.test(n.nodeValue));
  const m = tn && /^(\D*)(\d+)(.*)$/.exec(tn.nodeValue);
  if (!m || !moves() || !Number(m[2])) return;
  const to = Number(m[2]), t0 = performance.now();
  tn.nodeValue = m[1] + "0" + m[3];
  const step = (t) => {
    if (!tn.isConnected) return;              // the page put a new count in meanwhile: that one is final
    if (!moves()) { tn.nodeValue = m[1] + to + m[3]; return; }       // the system asked for less motion meanwhile: the number, at once
    const p = Math.min(1, Math.max(0, (t - t0) / 700));
    tn.nodeValue = m[1] + Math.round(to * (1 - Math.pow(1 - p, 3))) + m[3];
    if (p < 1) requestAnimationFrame(step); else bump(node);
  };
  requestAnimationFrame(step);
}
function runTimes() {                         // the rows of running jobs: "running · 7 min 5 s", from their start (data-t0, UTC seconds) - read again each time, so rows swapped in count too
  const now = Date.now() / 1000;
  for (const li of document.querySelectorAll("#jrows li[data-t0]")) {
    const sm = li.querySelector(".r small");
    if (!sm || li.dataset.st !== "running") continue;
    sm.classList.add("mv-el");
    sm.textContent = "running \u00b7 " + took(Math.max(0, now - Number(li.dataset.t0)));
  }
}
let clockTimer = 0;
function clock() {                            // the 1 s clock: only while a row has a start to count from and the page is in view (again after each list swap)
  const want = !document.hidden && !!document.querySelector("#jrows li[data-t0]");
  if (want) runTimes();
  if (want && !clockTimer) clockTimer = setInterval(clock, 1000);
  else if (!want && clockTimer) { clearInterval(clockTimer); clockTimer = 0; }
}
function ringFrom(pane) {                     // the fill the ring shows now (its stroke-dashoffset), or null
  const a = pane && pane.querySelector(".ring .a");
  return a ? a.getAttribute("stroke-dashoffset") : null;
}
function ringIn(pane, from) {                 // the service drew the ring at its fill; with motion it moves there from "from" (empty on the first load)
  const r = pane && pane.querySelector(".ring");
  if (!r || !moves() || from === null) return;
  const a = r.querySelector(".a"), to = r.dataset.o;
  if (from === undefined) from = a.getAttribute("stroke-dasharray");
  if (String(from) === String(to)) return;
  a.classList.add("mv-hold");                 // jump to where it was, then move on
  a.setAttribute("stroke-dashoffset", from);
  void getComputedStyle(a).strokeDashoffset;
  a.classList.remove("mv-hold");
  requestAnimationFrame(() => requestAnimationFrame(() => a.setAttribute("stroke-dashoffset", to)));
}
const SKEL_MS = 8000;
function unskel(box) {                        // the content back, as it was
  if (!box.dataset.skel) return;
  clearTimeout(box._skelTimer);
  delete box._skelTimer;
  box.replaceChildren(...(box._kept || []));
  delete box.dataset.skel;
  if (box._back) { const f = box._back; box._back = null; f(); }
}
function skeleton(box, rows, back) {          // the shape of what comes, while the next page loads; it goes again (back() is called) when the page stays
  if (!box || box.dataset.skel) return;
  box._back = back || null;
  box._skelTimer = setTimeout(() => unskel(box), SKEL_MS);
  const k = el("div", "skel");
  k.setAttribute("aria-busy", "true");
  k.setAttribute("aria-label", "Loading");
  for (const c of rows) k.append(el("i", c));
  box._kept = Array.from(box.childNodes);
  box.dataset.skel = "1";
  box.replaceChildren(k);
}
window.addEventListener("pageshow", (ev) => {
  if (!ev.persisted) return;
  for (const b of document.querySelectorAll("[data-skel]")) unskel(b);
});

// ---- the Jobs page (1.78.0): a list on the left (My jobs | All jobs, search, status chips), the job on the right. The server renders both
// (the page reads the same without scripts); while the job runs this swaps the server's pane in every 3 s - and stops when it says it is done.
function jobList() {                         // the chips and the search filter the rows on the page -> apply (again, after the rows change)
  const box = $("jl");
  if (!box) return () => {};
  const q = $("jq");
  let st = "";
  const apply = () => {
    const t = (q.value || "").trim().toLowerCase();
    for (const li of $("jrows").children) li.hidden = !((!st || li.dataset.st === st) && (!t || (li.dataset.q || "").indexOf(t) >= 0));
    for (const b of $("jchips").children) b.setAttribute("aria-pressed", b.dataset.st === st ? "true" : "false");
  };
  q.addEventListener("input", apply);
  $("jchips").addEventListener("click", (ev) => { const b = ev.target.closest && ev.target.closest("button"); if (b) { st = b.dataset.st; apply(); } });
  return apply;
}
// 1.79.6: a job is over for the page when AAP says so - its running mark (.d.run, .ic-run) never stays after that
const LIVE = ["running", "pending", "waiting", "new"];
function jobsPage() {
  const apply = jobList();
  document.addEventListener("click", (ev) => {
    const b = ev.target.closest && ev.target.closest("button");
    if (!b) return;
    if (b.dataset.copy && navigator.clipboard) navigator.clipboard.writeText(b.dataset.copy).then(() => { b.textContent = "Copied"; setTimeout(() => { b.textContent = "Copy"; }, 1500); }, () => {});
    if (b.hasAttribute("data-copylink") && navigator.clipboard) navigator.clipboard.writeText(location.href).then(() => { b.textContent = "Copied"; setTimeout(() => { b.textContent = "Copy link"; }, 1500); }, () => {});
    if (b.hasAttribute("data-print")) window.print();
  });
  // 1.79.6: one request a turn - while the open job runs, its pane and the list come in the same answer (?pane=1&list=); when only rows of the
  // list run, the list alone, every 6 s. So each row and the pane end in their final state while the page is open, within the same per-person
  // budget as before. Nothing is asked while the page is hidden; coming back asks at once, but never sooner than the wait in force.
  let pane = $("jd"), wait = 3000, timer = 0, busy = false, last = 0;
  if (!pane) return;
  ringIn(pane);
  // 1.92.0: the job page's Approve / Deny (the server drew them for an approver who may decide): the same question and call as Approvals
  const decideBox = () => {
    const row = pane.querySelector("#decide .dbtns[data-aid]");
    if (!row || row.dataset.wired) return;
    row.dataset.wired = "1";
    decider(row, +row.dataset.aid, () => { last = 0; tick(); });         // asked at once (last 0), the wait kept: a failed read never loops (1.110.0)
  };
  decideBox();
  if (+pane.dataset.wait) wait = +pane.dataset.wait;
  clock();
  document.addEventListener("visibilitychange", clock);
  $("jrows").addEventListener("click", (ev) => {              // 1.87.0: another job picked - its pane's shape while its page loads
    const a = ev.target.closest && ev.target.closest("a");
    if (!a || ev.defaultPrevented || ev.button || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    if (pane.dataset.skel) { ev.preventDefault(); return; }
    if (a.getAttribute("href").split("?")[0].endsWith("/jobs/" + pane.dataset.job)) return;
    const li = a.parentNode;
    li.classList.add("picked");                                  // marked as coming; the open job keeps on and aria-current until the next page is there
    skeleton(pane, ["h", "s", "b", "", "", "s", "b"], () => { li.classList.remove("picked"); tick(); });
  });
  const paneLive = () => !!(pane && pane.dataset.done === "0" && pane.dataset.job);
  const listLive = () => !!document.querySelector("#jrows li[data-st=running]");
  function putList(fresh) {                    // the rows and the chips' counts; the search box stays (its focus, what is typed), the filter applies again
    const rows = $("jrows"), chips = $("jchips");
    const nr = fresh && fresh.querySelector("#jrows"), nc = fresh && fresh.querySelector("#jchips");
    if (!rows || !nr) return;
    const old = new Map(Array.from(rows.children).map((li) => [li.querySelector("a") ? li.querySelector("a").getAttribute("href").split("?")[0] : "", li]));
    const id = pane && pane.dataset.job;
    const out = [];
    for (const li of Array.from(nr.children)) {
      const href = li.querySelector("a") ? li.querySelector("a").getAttribute("href").split("?")[0] : "";
      const was = old.get(href);
      // AAP did not give this job's status just now: the row stays as it was (asked again next turn), never "over" by a failed read
      if (li.dataset.status === "unknown" && was && was.dataset.st === "running") { out.push(was); continue; }
      const n = document.importNode(li, true);
      const on = !!(id && href.endsWith("/jobs/" + id));
      n.classList.toggle("on", on);
      if (on) n.setAttribute("aria-current", "true"); else n.removeAttribute("aria-current");
      out.push(n);
    }
    rows.replaceChildren(...out);
    clock();
    if (chips && nc) {                          // the counts; the pressed chip is set again by apply
      const pressed = Array.from(chips.children).find((b) => b.getAttribute("aria-pressed") === "true");
      chips.replaceChildren(...Array.from(nc.children).map((b) => document.importNode(b, true)));
      for (const b of chips.children) b.setAttribute("aria-pressed", pressed && b.dataset.st === pressed.dataset.st ? "true" : "false");
    }
    apply();
  }
  function gone() {                            // no longer this person's to see (403/404): said in place, nothing of the old view left
    const p = el("section", "card jd");
    p.id = "jd"; p.dataset.done = "1";
    p.append(el("p", "empty", "This job is not available here any more."));
    pane.replaceWith(p);
    pane = p;
  }
  async function tick() {
    clearTimeout(timer);
    if (busy || document.hidden || pane.dataset.skel || !(paneLive() || listLive())) return;
    const early = wait - (Date.now() - last);
    if (early > 50) { timer = setTimeout(tick, early); return; }               // a resume never asks sooner than the wait in force
    busy = true;
    last = Date.now();
    const scope = ($("jl") && $("jl").dataset.scope) || "mine";
    try {
      const both = paneLive();
      const url = both ? "jobs/" + encodeURIComponent(pane.dataset.job) + "?pane=1&list=" + encodeURIComponent(scope) + (pane.dataset.via ? "&via=" + encodeURIComponent(pane.dataset.via) : "")
                       : "jobs?list=1&scope=" + encodeURIComponent(scope);
      const r = await fetch(BASE + url, {credentials: "same-origin", headers: {"Accept": "text/html"}});
      if (r.status === 429) wait = 10000;
      else if (both && (r.status === 403 || r.status === 404)) { gone(); wait = 6000; }
      else if (!r.ok) throw new Error("HTTP " + r.status);
      else {
        const doc = new DOMParser().parseFromString(await r.text(), "text/html");
        const np = both && doc.querySelector("#jd");
        let stale = false;
        if (np && np.dataset.error) stale = true;                                // AAP could not say just now: the pane stays as it was
        else if (np && !pane.dataset.skel) {
          const same = np.dataset.job === pane.dataset.job;
          const was = same ? ringFrom(pane) : undefined;
          const fresh = document.importNode(np, true);
          // 1.92.0: the decision box stays as the person left it - a question being answered is not swapped away; an open panel stays open
          const oldBox = same && pane.querySelector("#decide"), newBox = fresh.querySelector("#decide");
          const previous = oldBox?.querySelector("[data-aid]")?.dataset, next = newBox?.querySelector("[data-aid]")?.dataset;
          if (oldBox?.dataset.hold && previous && next && previous.aid === next.aid && previous.approveBlocked === next.approveBlocked &&
              previous.savedJob === next.savedJob && previous.pendingJob === next.pendingJob) newBox.replaceWith(oldBox);
          else if (oldBox && newBox && oldBox.querySelector("details.wic[open]") && newBox.querySelector("details.wic")) newBox.querySelector("details.wic").open = true;
          pane.replaceWith(fresh); pane = $("jd");
          ringIn(pane, was);
          decideBox();
        }
        putList(doc.querySelector("#jl"));
        wait = stale ? Math.min(Math.max(wait * 2, 6000), 30000) : (paneLive() ? (+pane.dataset.wait || 3000) : 6000);
      }
    } catch (e) { wait = Math.min(Math.max(wait * 2, 3000), 30000); }       // a failed answer: ask again later (3 s at least), never stop for good
    busy = false;
    if (paneLive() || listLive()) timer = setTimeout(tick, wait);
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden) tick(); });
  last = Date.now();
  timer = setTimeout(tick, wait);
}

// ---- the host picker (1.78.0): a searchable list of the inventory's hosts and groups, to tick one or many. The server renders a plain
// multi-select (it works without scripts); this puts the search box, the chips and the list in its place. Every pick is checked again by the service.
function hostPickers() {
  for (const box of document.querySelectorAll(".picker[data-picker]")) {
    const sel = box.querySelector("select");
    if (!sel || box.dataset.on) continue;
    box.dataset.on = "1";
    const field = box.dataset.picker, cap = parseInt(box.dataset.max, 10) || 50, src = box.dataset.src, setting = box.dataset.setting || "portal_action_max_hosts";
    const picked = new Map();                      // name -> hosts it stands for
    for (const o of sel.selectedOptions) { const m = /\((\d+) hosts?\)$/.exec(o.textContent); picked.set(o.value, m ? parseInt(m[1], 10) : 1); }
    sel.disabled = true;
    sel.hidden = true;
    for (const x of box.querySelectorAll(".help")) x.hidden = true;
    const chips = el("div", "pk-chips"), q = el("input", "pk-q"), list = el("ul", "pk-list"), foot = el("p", "pk-foot muted-line");
    const all = el("button", "btn pk-all", "Select all shown"), note = el("span", "muted-line pk-note"), hid = el("div", "pk-hidden");
    q.type = "search"; q.placeholder = "Search hosts and groups"; q.autocomplete = "off"; q.spellcheck = false;
    q.setAttribute("role", "combobox"); q.setAttribute("aria-expanded", "true"); q.setAttribute("aria-controls", "pk-" + field);
    q.setAttribute("aria-label", "Search the inventory's hosts and groups");
    list.id = "pk-" + field; list.setAttribute("role", "listbox"); list.setAttribute("aria-multiselectable", "true");
    all.type = "button";
    const bar = el("div", "pk-bar"); bar.append(all, note);
    box.append(chips, q, list, foot, bar, hid);
    let rows = [], on = 0, seq = 0, timer = 0, matches = 0, stale = "";
    const total = () => { let n = 0; for (const c of picked.values()) n += c; return n; };
    function sync() {
      chips.replaceChildren();
      hid.replaceChildren();
      for (const [name, c] of picked) {
        const chip = el("span", "chip s-info pk-chip");
        chip.append(el("span", "", c > 1 ? name + " (" + c + ")" : name));
        const x = el("button", "pk-x", "×");
        x.type = "button"; x.setAttribute("aria-label", "Remove " + name);
        x.addEventListener("click", () => { picked.delete(name); sync(); draw(); q.focus(); });
        chip.append(x);
        chips.append(chip);
        const h = document.createElement("input");
        h.type = "hidden"; h.name = field; h.value = name; h.dataset.pick = "1";
        hid.append(h);
      }
      if (!picked.size) chips.append(el("span", "muted-line", "Nothing picked yet."));
      note.textContent = "About " + total() + " of " + cap + " hosts";
    }
    function fits(r) { return picked.has(r.name) || r.count <= cap; }          // the server counts the distinct hosts of the groups and says when it is too many
    function draw() {
      list.replaceChildren();
      rows.forEach((r, i) => {
        const li = el("li", "pk-row" + (i === on ? " on" : "") + (picked.has(r.name) ? " sel" : "") + (fits(r) ? "" : " off"));
        li.setAttribute("role", "option"); li.setAttribute("aria-selected", picked.has(r.name) ? "true" : "false");
        if (!fits(r)) { li.setAttribute("aria-disabled", "true"); li.title = "Too big: at most " + cap + " hosts (" + setting + ")"; }
        const mark = el("span", "pk-box"); mark.setAttribute("aria-hidden", "true"); mark.textContent = picked.has(r.name) ? "✓" : "";
        li.append(mark, el("span", "pk-name", r.name), el("span", "pk-kind", r.kind === "group" ? "group · " + r.count + " host" + (r.count === 1 ? "" : "s") + " · " + (fits(r) ? "fits" : "too big: at most " + cap + " (" + setting + ")") : "host"));
        li.addEventListener("mousedown", (ev) => { ev.preventDefault(); on = i; toggle(r); });
        list.append(li);
      });
      if (!rows.length) list.append(el("li", "pk-empty", "Nothing matches."));
      foot.textContent = (stale ? stale + " " : "") + (matches > rows.length ? (matches - rows.length) + " more - type to narrow." : "");
      all.disabled = !rows.some((r) => !picked.has(r.name) && fits(r) && total() + r.count <= cap);
    }
    function toggle(r) {
      if (picked.has(r.name)) picked.delete(r.name); else if (fits(r)) picked.set(r.name, r.count);
      sync(); draw();
    }
    async function ask() {
      const my = ++seq;
      let r;
      try { r = await call("api/inventory?q=" + encodeURIComponent(q.value.trim()) + (box.dataset.inv ? "&inv=" + encodeURIComponent(box.dataset.inv) : "")); } catch (e) { foot.textContent = "The inventory could not be read just now."; return; }
      if (my !== seq) return;
      rows = r.items || []; matches = r.matches || rows.length; stale = r.stale || ""; on = 0;
      draw();
    }
    all.addEventListener("click", () => {
      for (const r of rows) if (!picked.has(r.name) && fits(r) && total() + r.count <= cap) picked.set(r.name, r.count);
      sync(); draw();
    });
    q.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(ask, 150); });
    q.addEventListener("keydown", (ev) => {
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        ev.preventDefault();
        if (rows.length) on = (on + (ev.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length;
        draw();
        const cur = list.children[on]; if (cur && cur.scrollIntoView) cur.scrollIntoView({block: "nearest"});
      } else if ((ev.key === "Enter" || (ev.key === " " && !q.value)) && rows[on]) { ev.preventDefault(); toggle(rows[on]); }
      else if (ev.key === "Backspace" && !q.value && picked.size) { picked.delete(Array.from(picked.keys()).pop()); sync(); draw(); }
    });
    sync();
    ask();
  }
}

// the finding list: "Skip this host" unticks the host's findings, and ticking one unticks Skip
function pickList() {
  for (const box of document.querySelectorAll(".group input[type=checkbox]")) {
    box.addEventListener("change", () => {
      const grp = box.closest(".group");
      const same = Array.from(grp.querySelectorAll("input[type=checkbox]"));
      if (!box.checked) return;
      for (const o of same) if (o !== box && ((box.value === "skip") || (o.value === "skip"))) o.checked = false;
    });
  }
}

// ---- several findings picked: Run starts each as its own job, one after the other (the same call as one Run)
function manyPage() {
  const t = $("many"), go = $("go-many");
  if (!t || !go) return;
  go.addEventListener("click", async () => {
    go.disabled = true;
    let ok = 0, bad = 0;
    for (const tr of t.querySelectorAll("tbody tr")) {
      const cell = tr.querySelector("[data-cell=state]");
      if (tr.dataset.bad) { bad++; continue; }
      cell.replaceChildren(chip("running", "Starting"));
      const vars = {report_job: tr.dataset.job, finding_id: tr.dataset.fid};
      vars[t.dataset.hv] = tr.dataset.host;
      try {
        const r = await post("api/actions/" + encodeURIComponent(t.dataset.action), {vars});
        rememberRunning(r.job_id);
        const a = el("a", "", "job " + r.job_id);
        a.href = r.url;
        cell.replaceChildren(mark("ok", "s-ok", "Started"), document.createTextNode(" "), a);
        if (r.warning) cell.append(el("div", "sub", r.warning));
        ok++;
      } catch (e) {
        cell.replaceChildren(mark("bad", "s-critical", "Not started"), el("div", "sub", e.message));
        bad++;
      }
    }
    $("state").textContent = ok + " started" + (bad ? ", " + bad + " not started" : "") + ".";
    if (bad) { $("state").className = "state bad"; }
  });
}

// ---- the Actions page: the status of the jobs you started, live (the same call the job page makes)
// ---- the Actions page (1.78.0): the tiles and the search filter the cards (links and a form without scripts); Run opens a dialog with the form
// (fetched from the server, which renders it); Details is a panel. Without scripts: the confirm page and the details page.
async function fetchFrag(url) {
  const r = await fetch(url, {credentials: "same-origin", headers: {"Accept": "text/html"}});
  if (!r.ok) throw new Error("HTTP " + r.status);
  const doc = new DOMParser().parseFromString(await r.text(), "text/html");
  const n = doc.querySelector(".dbody");
  if (!n) throw new Error("no content");
  return document.importNode(n, true);
}
function homeActions() {
  const grid = $("agrid"), q = $("aq"), tiles = $("ftiles");
  if (!grid || !q || !tiles) return;
  const cards = Array.from(grid.querySelectorAll("li[data-wf]")), groups = Array.from(grid.querySelectorAll(".grp")), params = new URLSearchParams(location.search);
  // 1.78.4: pinned actions on top (a star on each card) and groups that fold - both kept in this browser, both only with scripts
  const mem = (k) => { try { return JSON.parse(localStorage.getItem(k) || "[]"); } catch (e) { return []; } };
  const keep = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* no memory: it works for this page */ } };
  let pins = mem("site-portal-pins"), folded = mem("site-portal-folded");
  const pinGroup = el("section", "grp pinned");
  pinGroup.hidden = true;
  const ph = el("h2"); ph.append(el("span", "chev", "\u25be"), document.createTextNode("Pinned "), el("span", "count", "0"));
  const pul = el("ul", "acts cols3");
  pinGroup.append(ph, pul);
  grid.prepend(pinGroup);
  groups.unshift(pinGroup);
  for (const li of cards) li._home = li.parentNode;
  const home = (li) => {                                                     // back to its area, in its place (by title)
    const ul = li._home;
    const next = Array.from(ul.children).find((x) => (x.dataset.title || "") > (li.dataset.title || ""));
    ul.insertBefore(li, next || null);
  };
  const place = () => {
    for (const li of cards) {
      const on = pins.indexOf(li.dataset.name) >= 0;
      const star = li.querySelector(".pin");
      if (star) { star.setAttribute("aria-pressed", on ? "true" : "false"); star.title = on ? "Unpin" : "Pin on top"; star.classList.toggle("on", on); }
      if (on && li.parentNode !== pul) pul.append(li);
      if (!on && li.parentNode === pul) home(li);
    }
    Array.from(pul.children).sort((a, b) => (a.dataset.title || "") < (b.dataset.title || "") ? -1 : 1).forEach((x) => pul.append(x));
  };
  for (const li of cards) {
    const top = li.querySelector(".top");
    if (!top) continue;
    const b = el("button", "pin", "\u2605");
    b.type = "button";
    b.setAttribute("aria-label", "Pin " + (li.dataset.title || ""));
    b.addEventListener("click", () => {
      const n = li.dataset.name, i = pins.indexOf(n);
      if (i >= 0) pins.splice(i, 1); else pins.push(n);
      keep("site-portal-pins", pins);
      place(); apply();
    });
    top.append(b);
  }
  for (const g of groups) {                                                  // a heading that folds its cards
    const h = g.querySelector("h2");
    if (!h) continue;
    const area = g.dataset.area || "Pinned";
    if (!h.querySelector(".chev")) h.prepend(el("span", "chev", "\u25be"));
    h.tabIndex = 0; h.setAttribute("role", "button");
    const fold = (on) => { g.classList.toggle("folded", on); h.setAttribute("aria-expanded", on ? "false" : "true"); const c = h.querySelector(".chev"); if (c) c.textContent = on ? "\u25b8" : "\u25be"; };
    fold(folded.indexOf(area) >= 0);
    const flip = () => {
      const on = !g.classList.contains("folded");
      fold(on);
      folded = folded.filter((x) => x !== area);
      if (on) folded.push(area);
      keep("site-portal-folded", folded);
    };
    h.addEventListener("click", flip);
    h.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); flip(); } });
  }
  let show = params.get("show") || "";
  q.value = params.get("q") || q.value;
  const apply = () => {
    const t = q.value.trim().toLowerCase();
    let n = 0;
    for (const li of cards) {
      const d = li.dataset;
      const ok = (!show || (show === "job" && d.wf === "0") || (show === "workflow" && d.wf === "1") || (show === "failed" && d.failed === "1")) && (!t || (d.q || "").indexOf(t) >= 0);
      li.hidden = !ok;
      if (ok) n++;
    }
    for (const g of groups) {                                                 // a group with nothing to show hides; its count follows
      const k = g.querySelectorAll("li[data-wf]:not([hidden])").length;
      g.hidden = k === 0;
      const c = g.querySelector("h2 .count");
      if (c) c.textContent = String(k);
    }
    for (const g of groups) g.classList.toggle("peek", !!t || !!show);          // a search or a filter shows what matches, folded or not
    $("acount").textContent = n + " of " + cards.length;
    for (const a of tiles.children) a.setAttribute("aria-current", a.dataset.show === show ? "true" : "false");
  };
  tiles.addEventListener("click", (ev) => { const a = ev.target.closest && ev.target.closest("a[data-show]"); if (!a) return; ev.preventDefault(); show = a.dataset.show; apply(); });
  q.addEventListener("input", apply);
  q.form.addEventListener("submit", (ev) => { ev.preventDefault(); apply(); });
  document.addEventListener("keydown", (ev) => {
    const tag = (document.activeElement && document.activeElement.tagName) || "";
    if (ev.key === "/" && !ev.ctrlKey && !ev.metaKey && !ev.altKey && !/^(INPUT|TEXTAREA|SELECT)$/.test(tag)) { ev.preventDefault(); q.focus(); q.select(); }
  });
  place();
  apply();
  const dlg = $("dlg"), drawer = $("drawer");
  if (!dlg || !dlg.showModal) return apply;                               // an old browser: the links go to the pages
  const open = async (box, url, after) => {
    try {
      const n = await fetchFrag(url);
      box.replaceChildren(n);
      if (after) after(n);
      if (!box.open) box.showModal();
    } catch (e) { return false; }
    return true;
  };
  document.addEventListener("click", async (ev) => {
    const t = ev.target;
    if (t === dlg || t === drawer) { t.close(); return; }                 // a click on the dark around it
    const c = t.closest && t.closest("[data-close]");
    if (c) { const box = c.closest("dialog"); if (box) { ev.preventDefault(); box.close(); } return; }
    const run = t.closest && t.closest("a[data-run]");
    if (run && !run.hasAttribute("data-pick")) {
      ev.preventDefault();
      if (drawer.open) drawer.close();
      const ok = await open(dlg, run.getAttribute("href") + (run.getAttribute("href").indexOf("?") < 0 ? "?embed=1" : "&embed=1"), (n) => { const f = n.querySelector("#run"); if (f) { initRun(f); hostPickers(); } });
      if (!ok) location.assign(run.getAttribute("href"));
      return;
    }
    const d = t.closest && t.closest("a[data-details]");
    if (d) {
      ev.preventDefault();
      const ok = await open(drawer, d.getAttribute("href") + "?embed=1");
      if (!ok) location.assign(d.getAttribute("href"));
    }
  });
  return apply;
}

function homePage() {
  homeLive(homeActions() || (() => {}));
  const r = $("refresh");
  if (r && r.form) r.form.addEventListener("submit", () => {
    for (const c of Array.from(document.querySelectorAll("#agrid li:not([hidden]) > .tcard")).slice(0, 6)) skeleton(c, ["h", "s", "", "", "b"]);
  });
}

// ---- 1.79.5: a card's "Last run" while that run is not over: the page asks for itself again every 15 s (the service keeps AAP's answer a minute)
// and puts each card's new Last run in place - the running mark goes when the run ends. Nothing is asked while the page is hidden or nothing runs.
function homeLive(apply) {
  const grid = $("agrid");
  if (!grid || !window.DOMParser) return;
  let timer = 0, wait = 15000, busy = false;
  const live = () => Array.from(grid.querySelectorAll(".meta.lastrun")).some((m) => LIVE.indexOf(m.dataset.st) >= 0);
  async function tick() {
    clearTimeout(timer);
    if (busy || !live() || document.hidden) return;
    busy = true;
    try {
      const r = await fetch(BASE, {credentials: "same-origin", headers: {"Accept": "text/html"}});
      if (!r.ok) throw new Error("HTTP " + r.status);
      const doc = new DOMParser().parseFromString(await r.text(), "text/html");
      for (const li of grid.querySelectorAll("li[data-name]")) {
        const old = li.querySelector(".meta.lastrun");
        if (!old) continue;
        const card = doc.querySelector('li[data-name="' + CSS.escape(li.dataset.name) + '"]');
        const fresh = card && card.querySelector(".meta.lastrun");
        if (fresh) old.replaceWith(document.importNode(fresh, true));
        else if (LIVE.indexOf(old.dataset.st) >= 0) {
          // 1.79.6 (Codex): the action is gone from the page or off now - its old run is not shown as running any more; a reload shows it as it is
          const m = el("div", "meta lastrun", "Last run: reload the page to see it");
          m.dataset.st = "";
          old.replaceWith(m);
        }
        if (card) li.dataset.failed = card.dataset.failed || "0";               // the "Last run failed" tile and filter follow
      }
      const nt = doc.querySelector('#ftiles a[data-show="failed"] .v'), ot = document.querySelector('#ftiles a[data-show="failed"] .v');
      if (nt && ot) ot.textContent = nt.textContent;
      apply();
      wait = 15000;
    } catch (e) { wait = Math.min(wait * 2, 120000); }
    busy = false;
    if (live()) timer = setTimeout(tick, wait);
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden) tick(); });
  timer = setTimeout(tick, wait);
}

// 1.79.5: a job started from this browser is known as running at once - its toast comes even when it ends before the next look (liveBadges)
function rememberRunning(id) {
  if (id === undefined || id === null) return;
  try {
    const seen = JSON.parse(localStorage.getItem("site-portal-jobs") || "{}");
    seen[id] = "running";
    localStorage.setItem("site-portal-jobs", JSON.stringify(seen));
  } catch (e) { /* no memory in this browser: the toast needs a look while it runs */ }
}

// Keep a draft per VM while the shortlist is open. Only a measured, complete destination can be saved.
function placementEditor(card, approval, viewer, save, undo) {
  const plan = approval.deploy_plan, shortlist = approval.deploy_shortlist;
  const panel = el("section", "placement-editor");
  const names = plan.vms.filter(vm => Object.hasOwn(shortlist.vms, vm.name));
  const choices = new Map(), tables = new Map();
  for (const group of card.querySelectorAll("[data-vm]")) tables.set(group.dataset.vm, group);
  const fields = ["vcenter", "cluster", "datastore", "folder"];
  const location = c => c.vcenter + ", datacenter " + c.datacenter;
  const dcKey = c => c.vcenter + "|" + c.datacenter;
  const stores = (lists, c) => c ? lists.datastores[dcKey(c) + "|" + c.name] || [] : [];
  const folders = (lists, c) => c ? lists.folders[dcKey(c)] || [] : [];
  const number = n => n == null ? "unknown" : String(Math.round(n * 10) / 10);
  const clusterFit = c => number(c.pct_after) + " % memory with this VM and " + number(c.down) +
    (c.down === 1 ? " host" : " hosts") + " down (target " + number(c.target) + " %)";
  const storeFit = d => number(d.pct_after) + " % used with the VM at full size (at most " + number(d.max) + " %)";
  const button = (label, cls) => {
    const b = el("button", cls, label);
    b.type = "button";
    return b;
  };
  const originals = new Map(names.map(vm => [vm.name, Object.fromEntries(vm.rows.map(r => [r.key, r.value]))]));
  let name = names[0].name;
  const clusterBox = el("fieldset"), storeBox = el("fieldset");
  const folderLabel = el("label", "placement-folder", "Folder"), folder = el("select");
  const note = el("pre"), saveButton = button("Save the new placement", "pri"), back = button("Undo");
  const message = el("p", "state");
  message.setAttribute("role", "status");
  const preview = el("div", "placement-note"), actions = el("div", "dbtns");
  panel.append(el("h3", "", "Change placement"));
  if (names.length > 1) {
    const label = el("label", "placement-folder", "VM"), select = el("select");
    for (const vm of names) select.append(el("option", "", vm.name));
    select.onchange = () => {
      if (choices.get(name)?.edited) {
        select.value = name;
        message.textContent = "Save one VM at a time.";
        return;
      }
      name = select.value;
      showVM();
    };
    label.append(select);
    panel.append(label);
  } else panel.append(el("b", "mono", name));
  const columns = el("div", "placement-lists");
  columns.append(clusterBox, storeBox);
  folderLabel.append(folder);
  preview.append(el("h4", "", "Work note preview for " + plan.ticket), note);
  actions.append(saveButton, back, message);
  panel.append(columns, folderLabel, preview, actions);

  function clearMarks() {
    for (const tr of card.querySelectorAll("tr.placement-draft")) {
      tr.classList.remove("placement-draft");
      for (const mark of tr.querySelectorAll(".draft")) mark.remove();
    }
  }
  let requested = [];
  function refreshDraft() {
    clearMarks();
    for (const chip of panel.querySelectorAll(".placement-pick")) chip.remove();
    const notes = [];
    requested = [];
    let valid = true;
    for (const [vm, pick] of choices) {
      if (vm !== name || !pick.cluster) continue;
      const c = pick.cluster, d = pick.store;
      const values = {vcenter: location(c), cluster: c.name, datastore: d?.name || "", folder: pick.folder};
      const old = originals.get(vm);
      const moved = old.vcenter !== values.vcenter;
      const changed = fields.filter(k => moved || old[k] !== values[k]);
      requested.push({vm_name: vm, vcenter: c.vcenter, cluster: c.name, datastore: values.datastore, folder: values.folder});
      valid = valid && Boolean(c.fits && d?.fits && pick.folder);
      for (const tr of tables.get(vm).querySelectorAll("[data-field]")) {
        const key = tr.dataset.field;
        if (!changed.includes(key)) continue;
        tr.classList.add("placement-draft");
        tr.cells[1].append(el("b", "draft", values[key] || "Choose a " + key), el("div", "sub draft", "Was " + old[key]));
        tr.cells[2].append(el("span", "chip s-plan draft", "Changed by you"));
      }
      if (vm === name) {
        for (const key of ["cluster", "datastore", "folder"]) {
          const selected = key === "folder" ? folderLabel : panel.querySelector('input[name="placement-' + key + '"]:checked')?.parentElement;
          if (changed.includes(key) && values[key] && selected) selected.append(el("span", "chip s-plan placement-pick", "Changed by you"));
        }
      }
      const reasons = {
        vcenter: "was " + (old.vcenter || "").split(", datacenter ")[0],
        cluster: number(c.pct_after) + "% memory used with this VM and " + c.down + (c.down === 1 ? " host" : " hosts") + " down (target " + c.target + "%)",
        datastore: d ? number(d.pct_after) + "% used with this VM at full size (at most " + d.max + "%)" : "not selected",
        folder: "vm_deploy_folders"
      };
      const labels = {vcenter: "vCenter", cluster: "Cluster", datastore: "Datastore", folder: "Folder"};
      const lines = fields.map(k => "- " + labels[k] + ": " + (values[k] || "not selected") + "   (changed by the approver: " + reasons[k] + ")");
      notes.push("[AAP] Placement changed - VM " + vm + " (AAP job pending, asked by " + viewer +
        ", for AAP job " + shortlist.job + ", ticket text " + plan.digest + "):\nIt would go to:\n" + lines.join("\n"));
    }
    message.textContent = "";
    preview.hidden = !notes.length;
    note.textContent = notes.join("\n\n");
    saveButton.disabled = !valid || !notes.length;
  }
  function option(box, group, choice, checked, detail, context, reason, select) {
    const label = el("label", "placement-option" + (reason ? " off" : ""));
    const radio = el("input"), title = el("span", "mono", choice.name);
    radio.type = "radio";
    radio.name = group;
    radio.checked = checked && !reason;
    radio.disabled = Boolean(reason);
    radio.onchange = select;
    if (context) title.append(el("small", "", context));
    label.append(radio, title);
    const fit = el("span", "placement-fit");
    if (choice.pct_after != null) {
      const bar = el("meter");
      bar.max = 100;
      bar.high = choice.target ?? choice.max ?? 100;
      bar.optimum = 0;
      bar.value = choice.pct_after;
      bar.setAttribute("aria-hidden", "true");
      fit.append(bar);
    }
    fit.append(el("span", "", detail));
    label.append(fit);
    if (reason) label.append(el("small", "placement-reason", reason));
    box.append(label);
  }
  function showStorage() {
    const pick = choices.get(name), c = pick.cluster, lists = shortlist.vms[name];
    storeBox.replaceChildren(el("legend", "", "Datastore"));
    for (const d of stores(lists, c)) {
      option(storeBox, "placement-datastore", d, pick.store === d, number(d.free_gb) + " GB free · " + storeFit(d),
        "", d.fits ? "" : d.why || "Does not fit", () => { pick.store = d; pick.edited = true; refreshDraft(); });
    }
    if (!stores(lists, c).length) storeBox.append(el("p", "sub", c ? "No datastores in this shortlist." : "Choose a fitting cluster."));
    folder.replaceChildren();
    if (!pick.folder && folders(lists, c).length) {
      const prompt = el("option", "", "Choose a folder");
      prompt.value = "";
      prompt.disabled = true;
      folder.append(prompt);
    }
    for (const f of folders(lists, c)) folder.append(el("option", "", f));
    folder.value = pick.folder;
    folder.disabled = !folder.options.length;
    folder.onchange = () => { pick.folder = folder.value; pick.edited = true; refreshDraft(); };
    refreshDraft();
  }
  function chooseCluster(c, initial) {
    const lists = shortlist.vms[name], old = originals.get(name);
    const fitting = stores(lists, c).filter(d => d.fits).sort((a, b) => a.pct_after - b.pct_after);
    const paths = folders(lists, c);
    const store = initial ? fitting.find(d => d.name === old.datastore) : fitting[0];
    choices.set(name, {cluster: c, store, folder: paths.includes(old.folder) ? old.folder : initial ? "" : paths[0] || "", edited: !initial});
  }
  function showVM() {
    const lists = shortlist.vms[name], old = originals.get(name);
    const ambiguous = c => lists.clusters.some(o => o !== c && o.vcenter === c.vcenter && o.name === c.name);
    if (!choices.has(name)) chooseCluster(lists.clusters.find(c => c.fits && !ambiguous(c) && location(c) === old.vcenter && c.name === old.cluster), true);
    clusterBox.replaceChildren(el("legend", "", "Cluster"));
    for (const [group, heading] of [["plan", "In the plan's vCenter"], ["outside", "Outside the plan's vCenter"]]) {
      const rows = lists.clusters.filter(c => c.group === group);
      if (rows.length) clusterBox.append(el("h4", "", heading));
      for (const c of rows) {
        const reason = !c.fits ? c.why || "Does not fit" : ambiguous(c) ? "This cluster name is ambiguous in its vCenter." : "";
        option(clusterBox, "placement-cluster", c, choices.get(name).cluster === c, clusterFit(c), c.vcenter + " · " + c.datacenter,
          reason, () => { chooseCluster(c, false); showStorage(); });
      }
    }
    showStorage();
  }
  back.onclick = () => { clearMarks(); panel.remove(); undo(); };
  saveButton.onclick = async () => {
    const controls = panel.querySelectorAll("button:enabled, input:enabled, select:enabled");
    controls.forEach(b => { b.disabled = true; });
    try {
      const result = await post("api/approvals/" + approval.id + "/placement", {vms: requested});
      clearMarks();
      panel.remove();
      save(result.job);
    } catch (e) {
      message.textContent = e.message;
      controls.forEach(b => { b.disabled = false; });
    }
  };
  showVM();
  return panel;
}

// ---- 1.92.0: Approve / Deny with one more question, in place (Sure? Yes, approve / Back) - the Approvals page's rows and the job page's box alike.
// row: where the buttons are drawn; after(d): what follows a decision AAP took (the list read again, the job's pane asked again).
function decider(row, aid, after) {
  const msg = el("span", "state");
  msg.setAttribute("aria-live", "polite");
  const box = row.closest(".decide");
  const keep = row.querySelector(".log"), rep = row.querySelector("a.btn");
  const approveButton = row.querySelector('[data-d="approve"]');
  const approveLabel = approveButton ? approveButton.textContent : row.dataset.approveLabel || "Approve";
  const change = row.querySelector("[data-change-placement]");
  let pendingJob = Number(row.dataset.pendingJob);
  const hold = (on) => { if (box) { if (on) box.dataset.hold = "1"; else delete box.dataset.hold; } };
  const draw = () => {
    hold(false);
    row.replaceChildren();
    for (const [d, label] of [["approve", approveLabel], ["deny", "Deny"]]) {
      if ((pendingJob || row.dataset.approveBlocked) && d === "approve") continue;
      const b = el("button", d === "deny" ? "deny" : "pri", label);
      b.type = "button";
      b.dataset.d = d;
      b.addEventListener("click", () => confirmIt(d));
      row.append(b);
    }
    if (change && !pendingJob) row.append(change);
    if (rep) row.append(el("span", "sp"), rep);
    row.append(msg);
    if (keep) row.append(keep);
  };
  const confirmIt = (d) => {
    hold(true);                                                   // the job page's pane is not swapped under a question
    row.replaceChildren();
    const yes = el("button", d === "deny" ? "deny" : "pri", d === "approve" ? "Yes, approve" : "Yes, deny");
    const back = el("button", "", "Back");
    yes.type = back.type = "button";
    row.append(el("span", "chip s-warning", "Sure?"), yes, back, msg);
    if (keep) row.append(keep);
    back.addEventListener("click", draw);
    yes.focus();
    yes.addEventListener("click", async () => {
      for (const x of row.querySelectorAll("button")) x.disabled = true;
      msg.className = "state";
      msg.textContent = (d === "approve" ? "Approving" : "Denying") + "…";
      try {
        await post("api/approvals/" + aid + "/" + d, d === "approve" ? {plan: row.dataset.savedJob || ""} : {});
        msg.textContent = (d === "approve" ? "Approved" : "Denied") + ". The workflow goes on in AAP.";
        row.replaceChildren(msg);                                 // the question is answered: no Sure? left to hold the list (1.92.0 review)
        hold(false);
        setTimeout(() => after(d), 1500);
      } catch (e) {
        // 1.110.0 (1.92.0 review): never a Sure? left behind - it would hold the list's 15 s read and the job page's pane.
        // Decided already, timed out or not allowed (404, 403): the message alone, then the list or the pane is read again;
        // anything else (AAP did not take it): Approve / Deny again, with the message, to try once more.
        if (e.status === 404 || e.status === 403 || e.status === 409) {
          row.replaceChildren(msg);
          hold(false);
          setTimeout(() => after(null), 3000);
        } else {
          draw();
        }
        msg.textContent = e.message;
        msg.className = "state bad";
      }
    });
  };
  function follow(job) {
    pendingJob = job;
    row.dataset.pendingJob = String(job);
    draw();
    msg.textContent = "Checking placement in AAP job " + job + ".";
    hold(true);
    async function poll() {
      if (!row.isConnected) return;
      try {
        const result = await call("api/placement/" + job);
        if (result.done) {
          msg.textContent = result.ok ? "Placement saved. Updating the plan…" : result.error + (result.launch_note === "not written" ? " Nothing was changed." : "");
          hold(false);
          after(null);
          return;
        }
      } catch (e) {
        msg.textContent = e.message;
        if (e.status === 403 || e.status === 404) { hold(false); after(null); return; }
      }
      setTimeout(poll, 3000);
    }
    setTimeout(poll, 3000);
  }
  if (change) change.onclick = async () => {
    hold(true);
    row.replaceChildren(msg);
    msg.textContent = "Loading the placement shortlist…";
    try {
      const listing = await call("api/approvals");
      const approval = listing.approvals.find(a => a.id === aid);
      if (!approval?.may_decide || !approval.deploy_shortlist) { hold(false); after(null); return; }
      if (approval.deploy_plan.placement?.done === false) { follow(approval.deploy_plan.placement.job); return; }
      msg.textContent = "";
      const editor = placementEditor(box, approval, row.dataset.viewer, follow, () => { draw(); change.focus(); });
      row.before(editor);
      editor.querySelector("select:enabled, input:enabled, button:enabled")?.focus();
    } catch (e) {
      draw();
      msg.textContent = e.message;
    }
  };
  if (pendingJob && box) follow(pendingJob);
  else draw();
}

// 1.92.0: "What it will change" - closed, one line of count chips; open, each host's items with their level (as the job page draws it: plan_html)
const LEVEL = {critical: ["bad", "s-critical", "Critical"], warning: ["wait", "s-warning", "Warning"], low: ["skip", "s-low", "Low"], info: ["skip", "s-info", "Info"]};
function chev() {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("class", "ic chev"); s.setAttribute("viewBox", "0 0 16 16"); s.setAttribute("aria-hidden", "true");
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
  p.setAttribute("d", "M6 4l4 4-4 4"); p.setAttribute("fill", "none"); p.setAttribute("stroke", "currentColor"); p.setAttribute("stroke-width", "1.8");
  p.setAttribute("stroke-linecap", "round"); p.setAttribute("stroke-linejoin", "round");
  s.append(p);
  return s;
}
function planEl(plan) {
  if (!plan) return null;
  const c = plan.counts || {};
  const d = el("details", "wic"), sm = el("summary"), cnt = el("span", "cnt");
  const words = [[c.changes + " change" + (c.changes === 1 ? "" : "s"), ""], [c.hosts + " host" + (c.hosts === 1 ? "" : "s"), ""]];
  for (const k of ["critical", "warning"]) if (c[k]) words.push([c[k] + " " + k, k]);
  for (const [w, k] of words) {
    const ch = el("span", "chip " + (k ? LEVEL[k][1] : "n"));
    if (k) ch.append(icon(LEVEL[k][0]));
    ch.append(document.createTextNode(w));
    cnt.append(ch);
  }
  sm.append(chev(), el("span", "t", "What it will change"), cnt);
  const body = el("div", "wicp");
  if (plan.title) body.append(el("p", "pt", plan.title));
  for (const h of plan.hosts || []) {
    const g = el("div", "hg"), ul = el("ul");
    g.append(el("div", "hn", h.host));
    for (const it of h.items || []) {
      const lv = LEVEL[it.level] || LEVEL.info, li = el("li"), ch = el("span", "chip " + lv[1]);
      ch.append(icon(lv[0]), document.createTextNode(lv[2]));
      li.append(ch, el("span", "", it.what));
      ul.append(li);
    }
    g.append(ul);
    if (h.more) g.append(el("p", "more", "and " + h.more + " more"));
    body.append(g);
  }
  if (plan.left_out) body.append(el("p", "more", "and " + plan.left_out + " more in the report (the check lists " + plan.shown + " here)"));
  d.append(sm, body);
  return d;
}
function deployPlanEl(plan) {
  const sources = {ticket: ["s-ok", "From the ticket", "ok"], default: ["s-info", "Default", "skip"],
                   job: ["s-low", "Chosen by the job", "wait"], approver: ["s-plan", "Changed by the approver", "ok"], stop: ["s-critical", "Stop", "bad"]};
  const c = plan.counts;
  const details = el("details", "wic"), summary = el("summary"), counts = el("span", "cnt");
  counts.append(el("span", "chip n", c.vms + (c.vms === 1 ? " VM" : " VMs")));
  for (const key of ["ticket", "default", "job"]) {
    const [cls, label] = sources[key];
    counts.append(el("span", "chip " + cls, c[key] + " " + label.toLowerCase()));
  }
  if (c.approver) counts.append(el("span", "chip s-plan", c.approver + " changed by the approver"));
  if (c.warnings) counts.append(el("span", "chip s-warning", c.warnings + (c.warnings === 1 ? " warning" : " warnings")));
  summary.append(chev(), el("span", "t", "What will be built"), counts);
  const body = el("div", "wicp deploy-list");
  body.append(el("p", "pt", [plan.ticket, ...plan.tasks, plan.digest].filter(Boolean).join(" · ")));
  if (plan.approve_error) body.append(el("p", "placement-status", plan.approve_error));
  for (const vm of plan.vms) {
    const group = el("div", "hg"), list = el("ul");
    group.append(el("div", "hn", vm.name));
    for (const row of vm.rows) {
      const [cls, source, mark] = sources[row.kind];
      const chip = el("span", "chip " + cls, source);
      chip.prepend(icon(mark));
      const level = row.kind === "stop" ? "stop" : row.level;
      const item = el("li", level ? "deploy-" + level : "");
      const value = el("div");
      value.append(el("b", "", row.value), el("div", "sub", row.where));
      if (row.why && level) value.append(el("div", "deploy-why", (level === "warning" ? "Warning: " : "Stop: ") + row.why));
      if (row.kind === "approver" && row.why) value.append(el("div", "deploy-why", row.why));
      item.append(el("span", "deploy-label", row.label), value, chip);
      list.append(item);
    }
    group.append(list);
    body.append(group);
  }
  details.append(summary, body);
  return details;
}

// 1.92.0: why someone may not decide, with the setting's name to search for (the service says which: approvers, self, role)
function whyEl(a) {
  const box = el("div", "dwhy"), t = el("span");
  const code = (x) => el("code", "", x);
  if (a.why === "approvers") t.append(el("b", "", "You are not an approver"), document.createTextNode(" ("), code("portal_approvers"), document.createTextNode(", "),
                                      code("portal_approver_groups"), document.createTextNode(") - only the approvers decide."));
  else if (a.why === "self") t.append(el("b", "", "You started this run"), document.createTextNode(" - another approver decides ("), code("portal_allow_self_approval"), document.createTextNode(")."));
  else if (a.why === "role") t.append(el("b", "", "The portal's account has no Approve role on this workflow"), document.createTextNode(" - give it Approve in AAP, or decide there."));
  else t.textContent = a.reason || "Decide it in AAP.";
  box.append(icon("lock"), t);
  return box;
}
function reportEl(rep, cls) {
  if (!rep || !/^https?:\/\//.test(rep.url)) return null;
  const a = el("a", cls || "btn sm2", "Open the full report ");
  a.href = rep.url; a.target = "_blank"; a.rel = "noopener";
  a.append(icon("arrow"));
  return a;
}

// ---- approvals: every workflow approval waiting in AAP that the portal's account can see; Approve / Deny (with one more question, in place) for the approvers
function approvalsPage() {
  const box = $("approvals");
  if (!box) return;
  let approver = box.dataset.approver === "1";            // 1.122.2: as the service says on each read (AD groups read since the page opened)
  const open = new Set();                                        // the panels open across the 15 s reads
  async function load() {
    let r;
    try {
      r = await call("api/approvals");
    } catch (e) {
      $("a-status").textContent = e.message;
      $("a-status").className = "status bad";
      return;
    }
    if (box.querySelector(".decs .chip.s-warning")) return;      // someone is answering Sure?: the list is read again after
    const list = r.approvals || [];
    if (typeof r.approver === "boolean") approver = r.approver;
    const mineN = list.filter((a) => a.may_decide).length;
    $("a-status").textContent = list.length ? list.length + " waiting" + (approver ? ", " + mineN + " for you to decide here." : ".") : "Nothing is waiting for a decision.";
    $("a-status").className = "status";
    box.replaceChildren();
    if (!list.length) return;
    const tb = el("tbody");
    for (const a of list) {
      const tr = el("tr", a.may_decide ? "hot-warning" : "locked");
      const wf = el("a", "", a.workflow + " (job " + a.workflow_job + ")");
      wf.href = BASE + "jobs/" + a.workflow_job;
      const vals = Object.entries(a.values || {}).map(([k, x]) => k + ": " + (String(x).length > 200 ? String(x).slice(0, 200) + "…" : x)).join("; ");
      const what = el("td");
      what.append(el("b", "", a.title), el("div", "sub", a.name));
      if (a.description) what.append(el("div", "sub", a.description));
      const who = el("td", "nw");
      who.append(el("div", "mono", a.requested_by || a.launched_in || "not through the portal"), el("div", "sub", a.requested_by ? (a.launched_in || "") : ""));
      const run = el("td");
      run.append(wf, el("div", "sub", vals));
      const apw = el("div", "apw"), plan = a.deploy_plan ? deployPlanEl(a.deploy_plan) : planEl(a.plan), rep = reportEl(a.report);
      let wrow = null;
      if (plan) {
        apw.append(plan);
        // wide: the open panel is a row of its own under the approval (the whole width); narrow (cards): in place
        plan.addEventListener("toggle", () => {
          if (plan.open) {
            open.add(a.id);
            wrow = el("tr", "wicrow" + (a.may_decide ? " hot-warning" : ""));
            const td = el("td");
            td.colSpan = 5;
            td.append(plan.querySelector(".wicp").cloneNode(true));
            wrow.append(td);
            tr.after(wrow);
          } else {
            open.delete(a.id);
            if (wrow) wrow.remove();
            wrow = null;
          }
        });
      }
      if (rep) { const d = el("div"); d.append(rep); apw.append(d); }
      if (!plan && !rep) apw.append(el("div", "sub", "The check left no summary and no report."));
      else if (!plan) apw.append(el("div", "sub", "The check left no summary: the report has the plan."));
      run.append(apw);
      const since = el("td", "mono nw");
      since.append(el("div", "", when(a.created_text)), el("div", "sub", a.time_left ? a.time_left + " left" : (a.timeout_text ? "Times out " + when(a.timeout_text) : "")));
      const act = el("td", "nw decs");
      if (a.may_decide) {
        const row = el("div", "row-btns");
        if (a.deploy_plan?.approve_error) row.dataset.approveBlocked = "1";
        row.dataset.savedJob = a.deploy_plan?.saved_job || "";
        if (a.deploy_plan?.saved_job) row.dataset.approveLabel = "Approve with the new placement";
        if (a.deploy_plan?.placement?.done === false) row.dataset.pendingJob = a.deploy_plan.placement.job;
        decider(row, a.id, () => load());
        act.append(row);
      } else {
        act.append(whyEl(a));
      }
      tr.append(what, who, run, since, act);
      tb.append(tr);
      if (plan && open.has(a.id)) plan.open = true;
    }
    const th = (t) => el("th", "", t);
    const head = el("thead");
    const hr = el("tr");
    hr.append(th("Waiting for"), th("Started by"), th("Run"), th("Waiting since"), th("Decision"));
    head.append(hr);
    const t = el("table");
    t.append(head, tb);
    const w = el("div", "tw");
    w.append(t);
    box.append(w);
  }
  load();
  setInterval(load, 15000);
}

// ---- 1.78.4: on every page - a count on Jobs (running) and Approvals (waiting for this person), and a toast when a job they started ends. One small call every 10 s while the page
// is open and in view; what was seen is kept in this browser (localStorage) so a toast comes once. Without scripts: nothing, the pages work as before.
function liveBadges() {
  const nav = document.querySelector(".nav");
  if (!nav || !window.fetch) return;
  const badge = (href) => {
    const a = nav.querySelector('a[href$="' + href + '"]');
    if (!a) return null;
    const b = el("span", "bdg");
    b.hidden = true;
    a.append(b);
    return b;
  };
  const bJobs = badge("jobs"), bAppr = badge("approvals");
  let box = null, fails = 0, timer = 0;
  const key = "site-portal-jobs";
  // 1.92.0: at most TOAST_SHOWN at a time (the rest wait, "+N more"), bottom right in a gutter the page leaves free while one
  // shows (html.toasting; bottom centre at 900 px and narrower); one that closes by itself waits while the pointer or the
  // keyboard is on it
  const queue = [];
  let more = null;
  const tidy = () => {
    if (queue.length && box && box.children.length - (more ? 1 : 0) < TOAST_SHOWN) put(queue.shift());
    if (queue.length) {
      if (!more) { more = el("div", "tmore"); box.append(more); }
      more.textContent = "+" + queue.length + " more";
    } else if (more) { more.remove(); more = null; }
    place();
  };
  const put = (t) => {
    box.insertBefore(t, more);
    if (t.classList.contains("timed")) {
      let left = TOAST_MS, at = Date.now(), timer = setTimeout(() => close(t), left);
      const hold = () => { clearTimeout(timer); left -= Date.now() - at; t.classList.add("held"); };
      const go = () => { if (t.contains(document.activeElement) || t.matches(":hover")) return; at = Date.now(); t.classList.remove("held"); timer = setTimeout(() => close(t), Math.max(left, 1500)); };
      t.addEventListener("mouseenter", hold); t.addEventListener("focusin", hold);
      t.addEventListener("mouseleave", go); t.addEventListener("focusout", () => setTimeout(go, 0));
    }
  };
  const close = (t) => { t.remove(); tidy(); };
  // where the stack goes, so it never covers the page's main column: 1500 px and wider, a gutter on the right (html.toasting);
  // the Jobs page's two columns, over the job list (bottom left); any other page, in the page under its heading; 900 px and
  // narrower, bottom centre
  const place = () => {
    if (!box) return;
    const w = window.innerWidth, two = !!document.querySelector(".two-pane") && w > 1000;
    const at = w <= 900 ? "tb" : w >= 1500 ? "tr" : two ? "tl" : "ti";
    box.className = "toasts " + at;
    const main = document.querySelector("main"), hero = main && main.querySelector(".hero");
    if (at === "ti" && main) { if (box.parentNode !== main) (hero ? hero.after(box) : main.prepend(box)); }
    else if (box.parentNode !== document.body) document.body.append(box);
    document.documentElement.classList.toggle("toasting", at === "tr" && !!box.querySelector(".toast"));
  };
  let rz = 0;
  window.addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(place, 150); });
  const toast = (title, text, bad, id, label, kind, href) => {
    if (!box) { box = el("div", "toasts"); box.setAttribute("role", "status"); box.setAttribute("aria-live", "polite"); document.body.append(box); }
    const t = el("div", "toast" + (bad ? " bad" : " timed") + (kind ? " " + kind : ""));
    const x = el("button", "tx", "×");
    x.type = "button"; x.setAttribute("aria-label", "Dismiss");
    x.addEventListener("click", () => close(t));
    const a = el("a", "", label || "Open job " + id);
    a.href = BASE + (href || "jobs/" + id);
    t.append(x, el("b", "", title), el("p", "", text), a);
    if (box.querySelectorAll(".toast").length < TOAST_SHOWN) put(t); else queue.push(t);
    tidy();
  };
  const show = (b, n) => {
    if (!b) return;
    const t = n > 99 ? "99+" : String(n || "");
    b.hidden = !(n > 0);
    if (b.textContent !== t) { b.textContent = t; if (!b.hidden) bump(b); }
  };
  async function tick() {
    clearTimeout(timer);
    if (document.visibilityState === "hidden") { timer = setTimeout(tick, 10000); return; }
    try {
      const r = await call("api/summary");
      fails = 0;
      show(bJobs, r.running);
      show(bAppr, r.approvals);
      let seen = {};
      try { seen = JSON.parse(localStorage.getItem(key) || "{}"); } catch (e) { seen = {}; }
      const now = {};
      // 1.79.5: a job this browser started and the summary does not list yet stays known as running (at most 50 kept)
      for (const k of Object.keys(seen).filter((k) => seen[k] === "running").slice(-50)) now[k] = "running";
      for (const j of r.recent || []) {
        // a status the portal could not read keeps what was known; 1.79.6 (Codex): canceled is over (its group is other, as an unread one)
        const over = j.group === "succeeded" || j.group === "failed" || j.status === "canceled";
        now[j.id] = over ? (j.status === "canceled" ? "canceled" : j.group) : (j.group === "other" && seen[j.id]) ? seen[j.id] : j.group;
        if (seen[j.id] === "running" && over) {
          const ok = j.group === "succeeded";
          toast((ok ? "Succeeded - " : j.group === "failed" ? "Failed - " : "Ended - ") + j.title, "Job " + j.id + " is over.", !ok, j.id);
        }
      }
      try { localStorage.setItem(key, JSON.stringify(now)); } catch (e) { /* private window: no memory, toasts still work for this page */ }
      // 1.92.0: an approval new for this person to decide - one toast for each, once in this browser session; none for the one
      // whose job page is open (its box is there)
      let shown = [];
      try { shown = JSON.parse(sessionStorage.getItem("site-portal-approvals") || "[]"); } catch (e) { shown = []; }
      for (const w of (r.waiting || []).filter((w) => shown.indexOf(w.id) < 0)) {
        shown.push(w.id);
        if (document.querySelector('#jd[data-job="' + w.workflow_job + '"]') || document.body.dataset.page === "approvals") continue;
        toast("Approval waiting: " + w.title, "Job " + w.workflow_job + " waits for your decision" + (w.left ? " - " + w.left + " left." : "."), false, w.workflow_job,
              "Open it and decide", "appr", w.portal ? "" : "approvals");          // not started here: its job page is not the portal's
      }
      try { sessionStorage.setItem("site-portal-approvals", JSON.stringify(shown.slice(-200))); } catch (e) { /* no memory: each page shows them again */ }
      timer = setTimeout(tick, 10000);
    } catch (e) {
      fails += 1;
      timer = setTimeout(tick, fails > 3 ? 60000 : 10000);
    }
  }
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") tick(); });
  tick();
}

// ---- 1.78.4: keyboard on every page: g a / g j / g p go to Actions, Jobs, Approvals; ? lists them (/ searches, on the Actions page)
function keyboard() {
  let g = 0, dlg = null;
  const go = { a: "", j: "jobs", p: "approvals", r: "reports/" };
  const help = () => {
    if (dlg) { dlg.remove(); dlg = null; return; }
    dlg = el("div", "keys"); dlg.setAttribute("role", "dialog"); dlg.setAttribute("aria-label", "Keyboard shortcuts");
    const rows = [["/", "Search the actions"], ["g a", "Go to Actions"], ["g j", "Go to Jobs"], ["g p", "Go to Approvals"], ["?", "This list (Esc closes it)"]];
    dlg.append(el("h3", "", "Keyboard"));
    const dl = el("dl");
    for (const [k, t] of rows) { const d = el("dt"); for (const x of k.split(" ")) d.append(el("kbd", "", x), document.createTextNode(" ")); dl.append(d, el("dd", "", t)); }
    dlg.append(dl);
    document.body.append(dlg);
  };
  document.addEventListener("keydown", (ev) => {
    const tag = (document.activeElement && document.activeElement.tagName) || "";
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(tag) || ev.ctrlKey || ev.metaKey || ev.altKey || (document.activeElement && document.activeElement.isContentEditable)) return;
    if (ev.key === "Escape" && dlg) { help(); return; }
    if (ev.key === "?") { ev.preventDefault(); help(); return; }
    if (g && go[ev.key] !== undefined) { ev.preventDefault(); g = 0; location.assign(BASE + go[ev.key]); return; }
    g = ev.key === "g" ? Date.now() : 0;
    if (g) setTimeout(() => { g = 0; }, 1200);
  });
}

document.addEventListener("DOMContentLoaded", () => {
  const p = document.body.dataset.page;
  document.documentElement.classList.add("js-on");
  if (p === "home") homePage();
  if (p === "action") { actionPage(); hostPickers(); pickList(); manyPage(); }
  if (p === "jobs") jobsPage();
  if (p === "approvals") approvalsPage();
  liveBadges();
  keyboard();
  // 1.87.0: the first load's counts go up (after the page's own code put them in); then the page is settled - nothing comes in again
  for (const n of document.querySelectorAll("#ftiles .v, #agrid .grp > h2 .count, #jchips button")) countUp(n);
  setTimeout(() => document.documentElement.classList.add("settled"), 1200);
});
