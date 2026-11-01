// VMware ask - the page (roles/vmware_ask_stack/files/vmware_ask.py answers). Everything from the service or from
// vCenter goes into the page as text (textContent), never as HTML. Its addresses are relative: the page works under
// the path the reverse proxy gives it (/ask/).
"use strict";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = String(text);
  return e;
};
let me = null;

async function api(path, opts) {
  const r = await fetch(path, Object.assign({credentials: "same-origin"}, opts || {}));
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || ("HTTP " + r.status));
  return body;
}

function when(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString();
}

function showRead(v) {
  const p = $("read");
  const parts = [];
  if (v.read_at) {
    const c = v.counts || {};
    parts.push("vCenter read " + when(v.read_at) + " (" + (v.vcenters || []).length + " vCenter(s), " + (c.vms || 0) + " VMs, " +
               (c.hosts || 0) + " hosts, " + (c.datastores || 0) + " datastores). Answers are as of then.");
  } else {
    parts.push(v.reading ? "vCenter is being read for the first time…" : "vCenter has not been read yet.");
  }
  const bad = (v.vcenters || []).filter((x) => x.error);
  if (bad.length) parts.push("Not read: " + bad.map((x) => x.name + " (" + x.error + ")").join("; ") + ".");
  if (v.error) parts.push("The last read failed: " + v.error);
  p.textContent = parts.join(" ");
  p.className = "read" + (bad.length || v.error ? " warn" : "");
}

function banner(b) {
  if (!b || !b.text) return;
  for (const id of ["banner-top", "banner-bottom"]) {
    const e = $(id);
    e.textContent = b.text;
    e.style.background = b.bg;
    e.style.color = b.fg;
    e.hidden = false;
  }
}

function csv(res) {
  const q = (v) => '"' + String(v === null || v === undefined ? "" : v).replace(/"/g, '""') + '"';
  const lines = [res.columns.map(q).join(",")].concat(res.rows.map((r) => r.map(q).join(",")));
  const a = el("a");
  a.href = URL.createObjectURL(new Blob([lines.join("\r\n") + "\r\n"], {type: "text/csv"}));
  a.download = "vmware-answer.csv";
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

function table(columns, rows) {
  const t = el("table");
  const hr = el("tr");
  columns.forEach((c) => hr.appendChild(el("th", null, c)));
  t.appendChild(el("thead")).appendChild(hr);
  const tb = el("tbody");
  rows.forEach((r) => {
    const tr = el("tr");
    r.forEach((v) => tr.appendChild(el("td", null, v)));
    tb.appendChild(tr);
  });
  t.appendChild(tb);
  return t;
}

function again(card, question) {
  const a = el("div", "actions");
  const b = el("button", null, "Edit and ask again");
  b.type = "button";
  b.onclick = () => { $("q").value = question; $("q").focus(); };
  a.appendChild(b);
  card.appendChild(a);
  return a;
}

function render(card, r) {
  card.textContent = "";
  if (r.status !== "ok") {
    card.className = "card " + (r.status === "refused" ? "refused" : "error");
    card.appendChild(el("h2", null, r.status === "refused" ? "Refused" : "Not answered"));
    card.appendChild(el("p", null, r.message));
    const how = el("div", "how");
    if (r.read_by) how.appendChild(el("div", null, "Read by: " + r.read_by));
    if (r.read_as) how.appendChild(el("div", null, "Read as: " + r.read_as));
    card.appendChild(how);
    if (r.genai_said) card.appendChild(el("pre", "said", "GenAI answered: " + r.genai_said));
    again(card, r.question);
    return;
  }
  card.className = "card";
  const noun = r.total === 1 ? r.noun[0] : r.noun[1];
  card.appendChild(el("h2", null, r.total + " " + noun + (r.listed < r.total ? " (the first " + r.listed + " listed)" : "")));
  const how = el("div", "how");
  const line = (label, text) => {
    const d = el("div");
    d.appendChild(el("b", null, label + ": "));
    d.appendChild(document.createTextNode(text));
    how.appendChild(d);
  };
  line("Read as", r.read_as);
  line("Read by", r.read_by);
  line("vCenter read", when(r.read_at));
  card.appendChild(how);
  if (r.note) card.appendChild(el("div", "note", r.note));
  if ((r.vcenters_not_read || []).length) {
    card.appendChild(el("div", "note", "Not in this answer - not read: " +
      r.vcenters_not_read.map((v) => v.name + " (" + v.error + ")").join("; ")));
  }
  if (r.count_by && r.count_by.rows) {
    const t = table([r.count_by.label, r.noun[1].charAt(0).toUpperCase() + r.noun[1].slice(1)], r.count_by.rows);
    t.classList.add("count");
    card.appendChild(t);
  }
  if (r.rows.length) {
    const w = el("div", "wrap");
    w.appendChild(table(r.columns, r.rows.slice(0, r.shown || r.rows.length)));
    card.appendChild(w);
  }
  const notes = [];
  if (r.shown < r.rows.length) notes.push((r.rows.length - r.shown) + " more not shown here: the CSV has every row.");
  if (r.templates_left_out) notes.push(r.templates_left_out + " template(s) left out (ask about templates to see them).");
  if (!r.rows.length) notes.push("Nothing matches.");
  if (notes.length) card.appendChild(el("p", "more", notes.join(" ")));
  const a = again(card, r.question);
  if (r.rows.length) {
    const b = el("button", null, "Download CSV");
    b.type = "button";
    b.onclick = () => csv(r);
    a.appendChild(b);
  }
}

async function ask(question) {
  $("hello").hidden = true;
  const log = $("log");
  log.appendChild(el("div", "you", question));
  const card = el("div", "card");
  card.appendChild(el("p", "working", "Reading the question…"));
  log.appendChild(card);
  card.scrollIntoView({block: "nearest"});
  try {
    const {id} = await api("api/ask", {method: "POST", headers: {"Content-Type": "application/json", "X-Ask": "1"},
                                        body: JSON.stringify({question})});
    for (let i = 0; ; i++) {
      await new Promise((ok) => setTimeout(ok, i < 10 ? 700 : 1500));
      const j = await api("api/ask/" + encodeURIComponent(id));
      if (j.status !== "running") { render(card, j.result); break; }
    }
  } catch (e) {
    render(card, {status: "error", message: e.message, question});
  }
  card.scrollIntoView({block: "nearest"});
}

document.addEventListener("DOMContentLoaded", async () => {
  try {
    me = await api("api/me");
  } catch (e) {
    $("read").textContent = e.message;
    return;
  }
  $("user").textContent = me.user;
  $("user").title = me.subject;
  $("title").textContent = me.title;
  document.title = me.title;
  banner(me.banner);
  showRead(me.vcenter);
  setInterval(() => api("api/me").then((m) => showRead(m.vcenter)).catch(() => {}), 60000);
  $("examples").addEventListener("click", (ev) => {
    if (ev.target.tagName === "BUTTON") { $("q").value = ev.target.textContent; $("q").focus(); }
  });
  $("ask").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const q = $("q").value.trim();
    if (!q) return;
    $("q").value = "";
    $("go").disabled = true;
    try { await ask(q); } finally { $("go").disabled = false; $("q").focus(); }
  });
  $("q").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); $("ask").requestSubmit(); }
  });
});
