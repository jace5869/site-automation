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
// a status as a word and a mark, never a colour alone
const STATUS = {
  successful: ["ok", "✓", "Succeeded"], failed: ["bad", "✕", "Failed"], error: ["bad", "✕", "Error"],
  canceled: ["muted", "–", "Canceled"], running: ["run", "●", "Running"], pending: ["wait", "◷", "Pending"],
  waiting: ["wait", "◷", "Waiting"], new: ["wait", "◷", "New"], "not run": ["muted", "–", "Not run"],
};
function chip(status) {
  const s = STATUS[status] || ["muted", "•", status || "unknown"];
  const c = el("span", "chip " + s[0]);
  c.append(el("span", "mark", s[1]), el("span", "", s[2]));
  c.querySelector(".mark").setAttribute("aria-hidden", "true");
  return c;
}
function when(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString();
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
function actionPage() {
  const f = $("run");
  if (!f) return;
  f.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const go = $("go");
    if (go.disabled) return;
    const vars = {};
    for (const x of f.querySelectorAll("input, select, textarea")) vars[x.name] = x.value;
    for (const w of f.querySelectorAll(".field")) { w.classList.remove("bad"); w.querySelector(".why").textContent = ""; }
    go.disabled = true;
    $("state").textContent = "Starting…";
    try {
      const r = await post("api/actions/" + encodeURIComponent(f.dataset.action), {vars});
      $("state").textContent = "Started: job " + r.job_id + (r.warning ? ". " + r.warning : "") + ". Opening it…";
      location.assign(r.url);
    } catch (e) {
      const p = (e.body && e.body.problems) || {};
      for (const [k, why] of Object.entries(p)) {
        const x = f.querySelector('[name="' + CSS.escape(k) + '"]');
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
  async function tick() {
    let v;
    try {
      v = await call("api/jobs/" + id + via);
    } catch (e) {
      $("j-status").textContent = e.message;
      $("j-status").className = "status bad";
      if (e.status && e.status < 500) return;
      setTimeout(tick, 10000);
      return;
    }
    document.title = (v.name || ("Job " + v.id)) + " - " + (STATUS[v.status] ? STATUS[v.status][2] : v.status);
    $("j-title").textContent = v.name || ("Job " + v.id);
    const st = $("j-status");
    st.replaceChildren(chip(v.status), el("span", "", v.done ? " Finished " + when(v.finished) + (v.elapsed ? ", took " + took(v.elapsed) : "") + "."
                                                       : v.started ? " Started " + when(v.started) + ". This page follows it." : " Waiting to start."));
    st.className = "status";
    let link = "";
    if (v.aap_link) { link = el("a", "", "Open in AAP"); link.href = v.aap_link; link.rel = "noopener"; }
    const vals = Object.entries(v.values || {}).map(([k, x]) => k + ": " + (String(x).length > 120 ? String(x).slice(0, 120) + "…" : x)).join("; ");
    facts($("j-facts"), [["Job", v.id + (v.type === "workflow_job" ? " (workflow)" : "")], ["Action", v.action_title || v.action], ["Started by", v.requested_by],
                         ["Asked", when(v.requested_at)], ["On", v.limit], ["Values", vals],
                         ["Step of", v.via ? "workflow job " + v.via : ""], ["AAP", link]]);
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
        const sc = el("td"); sc.append(chip(s.status));
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
        const tr = el("tr", h.failed || h.unreachable ? "bad" : "");
        tr.append(el("td", "", h.host));
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
    for (const a of list) {
      const card = el("article", "card");
      card.append(el("h2", "", a.title));
      const dl = el("dl", "facts");
      const wf = el("a", "", a.workflow + " (job " + a.workflow_job + ")");
      wf.href = BASE + "jobs/" + a.workflow_job;
      const vals = Object.entries(a.values || {}).map(([k, x]) => k + ": " + (String(x).length > 200 ? String(x).slice(0, 200) + "…" : x)).join("; ");
      facts(dl, [["Step", a.name], ["Workflow", wf], ["Started by", a.requested_by || "not through the portal"], ["Started in", a.launched_in],
                 ["Values", vals], ["Waiting since", when(a.created)], ["Times out", when(a.timeout)]]);
      card.append(dl);
      if (approver) {
        const row = el("div", "go");
        const say = el("span", "state");
        say.setAttribute("aria-live", "polite");
        for (const [d, label] of [["approve", "Approve"], ["deny", "Deny"]]) {
          const b = el("button", d === "deny" ? "secondary" : "", label);
          b.type = "button";
          b.addEventListener("click", async () => {
            for (const x of row.querySelectorAll("button")) x.disabled = true;
            say.textContent = (d === "approve" ? "Approving" : "Denying") + "…";
            try {
              await post("api/approvals/" + a.id + "/" + d, {});
              say.textContent = (d === "approve" ? "Approved" : "Denied") + ". The workflow goes on in AAP.";
              setTimeout(load, 1500);
            } catch (e) {
              say.textContent = e.message;
              say.className = "state bad";
              for (const x of row.querySelectorAll("button")) x.disabled = false;
            }
          });
          row.append(b);
        }
        row.append(say);
        card.append(row);
      }
      box.append(card);
    }
  }
  load();
  setInterval(load, 15000);
}

document.addEventListener("DOMContentLoaded", () => {
  const p = document.body.dataset.page;
  if (p === "action") actionPage();
  if (p === "job") jobPage();
  if (p === "approvals") approvalsPage();
});
