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
  if (!f) return;
  EventTarget.prototype.addEventListener.call(f, "submit", async (ev) => {          // a survey variable can be named like a form property: the form is read with the prototypes
    ev.preventDefault();
    const go = $("go");
    if (go.disabled) return;
    const vars = Object.create(null);          // a survey variable can be called constructor or toString: it is data, never an inherited property
    for (const x of qa(f, "input[type=number]")) if (x.validity && x.validity.badInput) { $("state").textContent = "A number is not finished."; $("state").className = "state bad"; x.focus(); return; }
    for (const x of qa(f, "input, select, textarea")) {
      if (!x.name || x.name === "portal-token" || x.name === "portal-sig" || x.disabled) continue;
      if (x.type === "checkbox") { if (!vars[x.name]) vars[x.name] = []; if (x.checked) vars[x.name].push(x.value); }
      else if (x.dataset.pick) { if (!vars[x.name]) vars[x.name] = []; vars[x.name].push(x.value); }
      else vars[x.name] = x.value;
    }
    for (const b of qa(f, ".picker[data-on]")) { const n = b.dataset.picker; if (!vars[n]) vars[n] = []; }
    for (const w of qa(f, ".field")) { w.classList.remove("bad"); w.querySelector(".why").textContent = ""; }
    go.disabled = true;
    $("state").textContent = "Starting…";
    try {
      const sg = qa(f, 'input[name="portal-sig"]')[0];
      const r = await post("api/actions/" + encodeURIComponent(Element.prototype.getAttribute.call(f, "data-action")), {vars, sig: sg ? sg.value : undefined});
      $("state").textContent = "Started: job " + r.job_id + (r.warning ? ". " + r.warning : "") + ". Opening it…";
      location.assign(r.url);
    } catch (e) {
      const p = (e.body && e.body.problems) || {};
      for (const [k, why] of Object.entries(p)) {
        const x = qa(f, '[name="' + CSS.escape(k) + '"]')[0];
        if (x) { const w = x.closest(".field"); w.classList.add("bad"); w.querySelector(".why").textContent = (w.querySelector("label").textContent + " " + why); }
      }
      $("state").textContent = e.message;
      $("state").className = "state bad";
      go.disabled = false;
    }
  });
}

// ---- a job, live: the service asks AAP; the page asks the service every few seconds until it is done
function jobPage() {
  const box = $("job");
  if (!box) return;
  const id = box.dataset.job;
  const via = box.dataset.via ? "?via=" + encodeURIComponent(box.dataset.via) : "";
  let wait = 2000;
  const out = box.querySelector("#j-out pre");
  const say = (id_, v) => { const n = $(id_); if (n) n.textContent = v === undefined || v === null ? "" : String(v); };
  async function tick() {
    let v;
    try {
      v = await call("api/jobs/" + id + via);
    } catch (e) {
      $("j-status").textContent = e.message;
      $("j-status").className = "v status bad";
      if (e.status && e.status < 500) return;
      setTimeout(tick, 10000);
      return;
    }
    document.title = (v.name || ("Job " + v.id)) + " - " + (STATUS[v.status] ? STATUS[v.status][2] : v.status);
    $("j-title").textContent = v.name || ("Job " + v.id);
    $("j-status").className = "v";
    $("j-status").replaceChildren(chip(v.status));
    say("j-when", v.done ? "Finished " + when(v.finished_text) + (v.elapsed ? ", took " + took(v.elapsed) : "") + "."
                         : v.started ? "Started " + when(v.started_text) + ". This page follows it." : "Waiting to start.");
    say("j-on", v.limit || "the hosts the " + (v.type === "workflow_job" ? "workflow" : "template") + " chooses");
    $("j-on").className = v.limit ? "v mono" : "v";
    say("j-action", (v.action_title || v.action || "") + (v.type === "workflow_job" ? " (workflow)" : ""));
    say("j-by", v.requested_by || "not through the portal");
    say("j-asked", when(v.requested_at_text) ? "Asked " + when(v.requested_at_text) : "");
    const vals = Object.entries(v.values || {}).map(([k, x]) => k + ": " + (String(x).length > 120 ? String(x).slice(0, 120) + "…" : x)).join("; ");
    say("j-vals", [vals, v.via ? "Step of workflow job " + v.via : ""].filter(Boolean).join(" · "));
    const aap = $("j-aap");
    if (v.aap_link) { const link = el("a", "", "Open in AAP"); link.href = v.aap_link; link.rel = "noopener"; aap.replaceChildren(link); }
    // part C: what came of the fix (its portal_result) and the action ACT names next
    if (v.result) {
      const r = {true: ["ok", "s-ok", "Fixed", "ok"], false: ["bad", "s-critical", "Still there", "bad"]}[v.result.fixed] || ["skip", "s-info", "Not checked", "mid"];
      const card = el("div", "card rc " + r[3]);
      card.append(mark(r[0], r[1], r[2]));
      const parts = [];
      if (v.result.before) parts.push("Before: " + v.result.before);
      if (v.result.after) parts.push("After: " + v.result.after);
      if (parts.length) card.append(el("span", "ba", parts.join(" \u2192 ")));
      if (v.result.summary) card.append(el("p", "", v.result.summary));
      $("j-result").replaceChildren(card);
      $("j-result-sec").hidden = false;
    }
    if (v.next) {
      const p = $("j-next");
      const a = el("a", "btn pri", v.next.label);
      a.href = v.next.url;
      p.replaceChildren(el("span", "sub", "ACT suggests next: "), a);
      p.hidden = false;
    }
    if (v.steps) {
      const sec = $("j-steps");
      sec.hidden = false;
      const tb = sec.querySelector("tbody");
      tb.replaceChildren();
      for (const s of v.steps) {
        const tr = el("tr");
        const td = el("td", "num");
        if (s.job && s.type === "job") { const a = el("a", "", s.job); a.href = BASE + "jobs/" + s.job + "?via=" + v.id; td.append(a); }
        else td.textContent = s.job || "";
        const sc = el("td", "nw"); sc.append(chip(s.status));
        tr.append(el("td", "", s.name + (s.type === "workflow_approval" ? " (approval)" : "")), sc, td);
        tb.append(tr);
      }
    }
    if (v.hosts && v.hosts.length) {
      const sec = $("j-hosts");
      sec.hidden = false;
      const tb = sec.querySelector("tbody");
      tb.replaceChildren();
      for (const h of v.hosts) {
        const tr = el("tr", h.failed || h.unreachable ? "hot-critical" : "");
        tr.append(el("td", "mono", h.host));
        for (const k of ["ok", "changed", "failed", "unreachable", "skipped"]) tr.append(el("td", "num", h[k]));
        tb.append(tr);
      }
    }
    if (v.links && v.links.length) {
      const sec = $("j-links");
      sec.hidden = false;
      const ul = sec.querySelector("ul");
      ul.replaceChildren();
      for (const l of v.links) { const li = el("li"); const a = el("a", "", l.label + ": " + l.url); a.href = l.url; a.rel = "noopener"; li.append(a); ul.append(li); }
    }
    if (typeof v.output === "string") {
      $("j-out").hidden = false;
      $("j-more").hidden = !v.output_more;
      const atEnd = out.scrollTop + out.clientHeight >= out.scrollHeight - 8;
      if (out.textContent !== v.output) {
        out.textContent = v.output || (v.done ? "(no output)" : "(no output yet)");
        if (atEnd) out.scrollTop = out.scrollHeight;
      }
    }
    if (v.artifacts && Object.keys(v.artifacts).length) {
      $("j-art").hidden = false;
      box.querySelector("#j-art pre").textContent = JSON.stringify(v.artifacts, null, 2).slice(0, 50000);
    }
    if (!v.done) { setTimeout(tick, wait); wait = Math.min(wait + 1000, 5000); }
  }
  tick();
}

// ---- pick a host, then a finding (1.75.3): the typeahead of the host field (without this script the field is a plain one)
// ---- the host picker (1.78.0): a searchable list of the inventory's hosts and groups, to tick one or many. The server renders a plain
// multi-select (it works without scripts); this puts the search box, the chips and the list in its place. Every pick is checked again by the service.
function hostPickers() {
  for (const box of document.querySelectorAll(".picker[data-picker]")) {
    const sel = box.querySelector("select");
    if (!sel || box.dataset.on) continue;
    box.dataset.on = "1";
    const field = box.dataset.picker, cap = parseInt(box.dataset.max, 10) || 5, src = box.dataset.src;
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
    let rows = [], on = 0, seq = 0, timer = 0, matches = 0;
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
        if (!fits(r)) { li.setAttribute("aria-disabled", "true"); li.title = "Too many hosts: at most " + cap; }
        const mark = el("span", "pk-box"); mark.setAttribute("aria-hidden", "true"); mark.textContent = picked.has(r.name) ? "✓" : "";
        li.append(mark, el("span", "pk-name", r.name), el("span", "pk-kind", r.kind === "group" ? "group · " + r.count + " host" + (r.count === 1 ? "" : "s") : "host"));
        li.addEventListener("mousedown", (ev) => { ev.preventDefault(); on = i; toggle(r); });
        list.append(li);
      });
      if (!rows.length) list.append(el("li", "pk-empty", "Nothing matches."));
      foot.textContent = matches > rows.length ? (matches - rows.length) + " more - type to narrow." : "";
      all.disabled = !rows.some((r) => !picked.has(r.name) && fits(r) && total() + r.count <= cap);
    }
    function toggle(r) {
      if (picked.has(r.name)) picked.delete(r.name); else if (fits(r)) picked.set(r.name, r.count);
      sync(); draw();
    }
    async function ask() {
      const my = ++seq;
      let r;
      try { r = await call("api/inventory?q=" + encodeURIComponent(q.value.trim())); } catch (e) { foot.textContent = "The inventory could not be read just now."; return; }
      if (my !== seq) return;
      rows = r.items || []; matches = r.matches || rows.length; on = 0;
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
function homePage() {
  const chips = Array.from(document.querySelectorAll("#started [data-job]"));
  if (!chips.length) return;
  async function ask() {
    let open = 0;
    for (const c of chips) {
      if (c.dataset.done) continue;
      try {
        const v = await call("api/jobs/" + c.dataset.job);
        const n = chip(v.status);
        n.dataset.job = c.dataset.job;
        if (v.done) n.dataset.done = "1"; else open++;
        c.replaceWith(n);
        const tr = n.closest("tr");
        if (tr) tr.className = v.status === "failed" || v.status === "error" ? "hot-critical" : (v.status === "waiting" || v.status === "pending" ? "hot-warning" : "");
        chips[chips.indexOf(c)] = n;
      } catch (e) {
        c.lastChild.textContent = "Not read";
      }
    }
    if (open) setTimeout(ask, 10000);
  }
  ask();
}

// ---- approvals: the listed workflows waiting; Approve / Deny for the approvers
function approvalsPage() {
  const box = $("approvals");
  if (!box) return;
  const approver = box.dataset.approver === "1";
  async function load() {
    let r;
    try {
      r = await call("api/approvals");
    } catch (e) {
      $("a-status").textContent = e.message;
      $("a-status").className = "status bad";
      return;
    }
    const list = r.approvals || [];
    $("a-status").textContent = list.length ? list.length + " waiting." : "Nothing is waiting for a decision.";
    $("a-status").className = "status";
    box.replaceChildren();
    if (!list.length) return;
    const tb = el("tbody");
    for (const a of list) {
      const tr = el("tr", "hot-warning");
      const wf = el("a", "", a.workflow + " (job " + a.workflow_job + ")");
      wf.href = BASE + "jobs/" + a.workflow_job;
      const vals = Object.entries(a.values || {}).map(([k, x]) => k + ": " + (String(x).length > 200 ? String(x).slice(0, 200) + "…" : x)).join("; ");
      const what = el("td");
      what.append(el("b", "", a.title), el("div", "sub", a.name));
      const who = el("td", "nw");
      who.append(el("div", "mono", a.requested_by || "not through the portal"), el("div", "sub", a.launched_in || ""));
      const run = el("td");
      run.append(wf, el("div", "sub", vals));
      const since = el("td", "mono nw");
      since.append(el("div", "", when(a.created_text)), el("div", "sub", a.timeout_text ? "Times out " + when(a.timeout_text) : ""));
      const act = el("td", "nw");
      if (approver && a.may_decide) {
        const row = el("div", "row-btns");
        const msg = el("span", "state");
        msg.setAttribute("aria-live", "polite");
        for (const [d, label] of [["approve", "Approve"], ["deny", "Deny"]]) {
          const b = el("button", d === "deny" ? "deny" : "pri", label);
          b.type = "button";
          b.addEventListener("click", async () => {
            for (const x of row.querySelectorAll("button")) x.disabled = true;
            msg.textContent = (d === "approve" ? "Approving" : "Denying") + "…";
            try {
              await post("api/approvals/" + a.id + "/" + d, {});
              msg.textContent = (d === "approve" ? "Approved" : "Denied") + ". The workflow goes on in AAP.";
              setTimeout(load, 1500);
            } catch (e) {
              msg.textContent = e.message;
              msg.className = "state bad";
              for (const x of row.querySelectorAll("button")) x.disabled = false;
            }
          });
          row.append(b);
        }
        row.append(msg);
        act.append(row);
      } else if (approver) {
        act.append(el("span", "state", "You started this run, so another approver decides (portal_allow_self_approval: false)."));
      } else {
        act.append(el("span", "state", "Only the approvers decide."));
      }
      tr.append(what, who, run, since, act);
      tb.append(tr);
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

document.addEventListener("DOMContentLoaded", () => {
  const p = document.body.dataset.page;
  if (p === "home") homePage();
  if (p === "action") { actionPage(); hostPickers(); pickList(); manyPage(); }
  if (p === "job") jobPage();
  if (p === "approvals") approvalsPage();
});
