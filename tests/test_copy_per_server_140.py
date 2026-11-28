#!/usr/bin/env python3
"""1.140.0 tests for per-server Copy (text / picture / both), Copy all shown beside Expand all,
standard section Copy, untouched command/code copy blocks, double-run safety on saved pages,
and portal server lists (50-server fixture with multiple findings each)."""
import json
import os
import subprocess
import sys
import unittest

import jinja2

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TEMPLATES = os.path.join(REPO, "roles", "site_email", "templates")
PORTAL_STATIC = os.path.join(REPO, "roles", "site_portal_stack", "files", "static")
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
import site_filters as sf  # noqa: E402


def make_50_server_fixture():
    """Build a 50-server fixture (placeholders only) with multiple findings per server:
    6 critical, 3 not-reached/unknown, 16 warning (>10 cap), 25 OK."""
    servers = []
    # 6 critical servers (findings intentionally unsorted in DOM to assert worst-first sorting)
    crit_specs = [
        ("web01.corp.example.mil", "192.0.2.10", [
            ("info", "Info", "Package cache updated 3 d ago", "dnf makecache", "No action required."),
            ("warning", "Warning", "TLS certificate expires in 14 days", "/etc/pki/tls/web01.pem", "Renew TLS certificate before expiry."),
            ("critical", "Critical", "Disk /var at 95% used (crit 90%)", "2.5 GB free of 50 GB", "Rotate /var/log and purge old journal files."),
            ("critical", "Critical", "Service nginx.service failed", "exit-code status=1", "Inspect journalctl -u nginx and restart nginx."),
        ]),
        ("db01.corp.example.mil", "192.0.2.11", [
            ("warning", "Warning", "Slow query log growing", "124 slow queries in 1 h", "Review indexes on reporting tables."),
            ("critical", "Critical", "MariaDB replication lag 420 s (crit 300 s)", "SQL thread behind binlog 000418", "Check replication worker and long transactions."),
        ]),
        ("app01.corp.example.mil", "192.0.2.12", [
            ("critical", "Critical", "Java heap OOM killer triggered <app01>", "kernel oom-killer killed pid 4210", "Increase heap cap or inspect recent deploy."),
            ("warning", "Warning", "Swap usage at 82%", "3.3 GB / 4.0 GB", "Check memory pressure after heap fix."),
            ("low", "Low", "Audit log rotation warning", "auditd.conf max_log_file_action", "Verify audit log retention policy."),
        ]),
        ("auth01.corp.example.mil", "192.0.2.13", [
            ("critical", "Critical", "SSSD offline for corp.example.mil", "adcli testjoin failed", "Re-verify keytab and AD DC reachability."),
            ("critical", "Critical", "Chronyd unsynchronised (+14 s drift)", "No reachable NTP peers", "Check firewall UDP/123 to site NTP servers."),
        ]),
        ("mail01.corp.example.mil", "192.0.2.14", [
            ("critical", "Critical", "Postfix mail queue backed up (1,840 msgs)", "deferred queue growing", "Check relay host connectivity and flush queue."),
        ]),
        ("proxy01.corp.example.mil", "192.0.2.15", [
            ("critical", "Critical", "HAProxy backend pool down (0/3 up)", "health check HTTP 503", "Inspect backend web nodes and reload HAProxy."),
            ("warning", "Warning", "File descriptor usage 84%", "ulimit -n 4096", "Raise LimitNOFILE in systemd unit."),
        ]),
    ]
    for host, ip, flist in crit_specs:
        servers.append({
            "host": host, "ip": ip, "sev": "critical", "statusLabel": "Critical",
            "worst": [f[2] for f in flist if f[0] == "critical"][0],
            "hint": "df -h /var && systemctl status --failed",
            "findings": [{"sev": a, "sevLabel": b, "title": c, "evidence": d, "whatToDo": e} for a, b, c, d, e in flist],
        })

    # 3 not-reached / unknown servers (critical-level visibility)
    for idx in range(1, 4):
        servers.append({
            "host": "edge0%d.corp.example.mil" % idx,
            "ip": "192.0.2.%d" % (19 + idx),
            "sev": "unknown",
            "statusLabel": "Not reached",
            "worst": "Host did not report (SSH timeout after 30 s)",
            "hint": "ssh -o ConnectTimeout=5 edge0%d.corp.example.mil true" % idx,
            "findings": [{
                "sev": "unknown", "sevLabel": "Not reached",
                "title": "Host did not report (SSH timeout after 30 s)",
                "evidence": "Unreachable during health collection",
                "whatToDo": "Verify power, network path, and SSH access then re-run.",
            }],
        })

    # 16 warning servers (multi-finding, exercises 10-line cap + '• ... 6 more')
    for idx in range(1, 17):
        servers.append({
            "host": "wrk%02d.corp.example.mil" % idx,
            "ip": "192.0.2.%d" % (29 + idx),
            "sev": "warning",
            "statusLabel": "Warning",
            "worst": "Disk /opt at %d%% used (warn 80%%)" % (80 + (idx % 9)),
            "hint": "df -h /opt",
            "findings": [
                {"sev": "warning", "sevLabel": "Warning",
                 "title": "Disk /opt at %d%% used (warn 80%%)" % (80 + (idx % 9)),
                 "evidence": "Free space below 20%",
                 "whatToDo": "Archive old releases in /opt/releases."},
                {"sev": "low", "sevLabel": "Low",
                 "title": "Pending security reboot (%d d)" % (idx + 3),
                 "evidence": "needs-restarting -r exited 1",
                 "whatToDo": "Schedule reboot in next maintenance window."},
            ],
        })

    # 25 OK servers (counted only, NEVER listed in Copy all shown)
    for idx in range(1, 26):
        servers.append({
            "host": "oknode%02d.corp.example.mil" % idx,
            "ip": "192.0.2.%d" % (50 + idx),
            "sev": "ok",
            "statusLabel": "OK",
            "worst": "All 14 daily checks passed",
            "hint": "",
            "findings": [],
        })
    assert len(servers) == 50
    return servers


class CopyPerServerTests(unittest.TestCase):
    def test_50_server_report_copy_order_caps_counts_ok_excluded_picture_bounded(self):
        fixture = make_50_server_fixture()
        driver = r"""
        const fs = require("fs");
        const vm = require("vm");
        const assert = require("assert");

        const reportSrc = fs.readFileSync(process.argv[1], "utf8");
        const servers = JSON.parse(process.argv[2]);
        const FAKE_PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aU9sAAAAASUVORK5CYII=";

        class Node {
          constructor(tag, attrs = {}) {
            this.tagName = tag.toUpperCase();
            this.nodeType = 1;
            this.attrs = Object.assign({}, attrs);
            this.children = [];
            this.parentNode = null;
            this.hidden = false;
            this.disabled = false;
            this.title = attrs.title || "";
            this.style = {};
            this.listeners = {};
            this._text = "";
            this._cls = new Set((attrs.class || "").split(/\s+/).filter(Boolean));
            this.classList = {
              add: (...ks) => ks.forEach(k => this._cls.add(k)),
              remove: (...ks) => ks.forEach(k => this._cls.delete(k)),
              contains: k => this._cls.has(k),
              toggle: (k, f) => (f === undefined ? (this._cls.has(k) ? this._cls.delete(k) : this._cls.add(k)) : (f ? this._cls.add(k) : this._cls.delete(k))),
            };
          }
          get className() { return Array.from(this._cls).join(" "); }
          set className(v) { this._cls = new Set(String(v || "").split(/\s+/).filter(Boolean)); }
          get firstChild() { return this.children[0] || null; }
          get nextSibling() { return this.nextElementSibling; }
          get previousSibling() { return this.previousElementSibling; }
          get nextElementSibling() {
            if (!this.parentNode) return null;
            const i = this.parentNode.children.indexOf(this);
            return i >= 0 ? (this.parentNode.children[i + 1] || null) : null;
          }
          get previousElementSibling() {
            if (!this.parentNode) return null;
            const i = this.parentNode.children.indexOf(this);
            return i > 0 ? this.parentNode.children[i - 1] : null;
          }
          get cells() { return this.children.filter(c => c.tagName === "TD" || c.tagName === "TH"); }
          get rows() { return this.children.filter(c => c.tagName === "TR"); }
          get tHead() { return this.children.find(c => c.tagName === "THEAD") || null; }
          get tBodies() { return this.children.filter(c => c.tagName === "TBODY"); }
          get textContent() {
            if (!this.children.length) return this._text;
            return this.children.map(c => c.textContent).join("");
          }
          set textContent(v) { this.children = []; this._text = String(v == null ? "" : v); }
          getAttribute(k) { return k === "class" ? this.className : (k in this.attrs ? this.attrs[k] : null); }
          setAttribute(k, v) { if (k === "class") this.className = v; else this.attrs[k] = String(v); }
          hasAttribute(k) { return k === "class" ? !!this.className : (k in this.attrs); }
          removeAttribute(k) { delete this.attrs[k]; }
          appendChild(c) {
            if (c.parentNode) c.parentNode.removeChild(c);
            c.parentNode = this;
            this.children.push(c);
            return c;
          }
          insertBefore(c, ref) {
            if (c.parentNode) c.parentNode.removeChild(c);
            c.parentNode = this;
            const i = ref ? this.children.indexOf(ref) : -1;
            if (i >= 0) this.children.splice(i, 0, c); else this.children.push(c);
            return c;
          }
          removeChild(c) {
            const i = this.children.indexOf(c);
            if (i >= 0) { this.children.splice(i, 1); c.parentNode = null; }
            return c;
          }
          remove() { if (this.parentNode) this.parentNode.removeChild(this); }
          addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); }
          dispatch(ev, e = {}) {
            e.target = e.target || this;
            e.preventDefault = e.preventDefault || (() => { e.defaultPrevented = true; });
            e.stopPropagation = e.stopPropagation || (() => { e.stopped = true; });
            (this.listeners[ev] || []).forEach(fn => fn(e));
            return e;
          }
          matches(sel) {
            return sel.split(",").some(part => {
              part = part.trim();
              if (!part) return false;
              const segs = part.split(/\s+/);
              const last = segs[segs.length - 1];
              if (!matchSimple(this, last)) return false;
              if (segs.length === 1) return true;
              let cur = this.parentNode;
              for (let i = segs.length - 2; i >= 0; i--) {
                while (cur && !matchSimple(cur, segs[i])) cur = cur.parentNode;
                if (!cur) return false;
                cur = cur.parentNode;
              }
              return true;
            });
          }
          closest(sel) {
            let cur = this;
            while (cur) { if (cur.matches(sel)) return cur; cur = cur.parentNode; }
            return null;
          }
          querySelectorAll(sel) {
            const out = [];
            const walk = n => {
              for (const c of n.children) {
                if (c.nodeType === 1) {
                  if (c.matches(sel)) out.push(c);
                  walk(c);
                }
              }
            };
            walk(this);
            return out;
          }
          querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
        }

        function matchSimple(el, s) {
          if (!s || s.includes(":") || s.includes("+")) return false;
          if (s.includes(">")) {
            const ps = s.split(">");
            return matchSimple(el, ps[1]) && el.parentNode && matchSimple(el.parentNode, ps[0]);
          }
          let rest = s, matched = false;
          const tagM = rest.match(/^([a-zA-Z0-9]+)/);
          if (tagM) { if (el.tagName !== tagM[1].toUpperCase()) return false; rest = rest.slice(tagM[0].length); matched = true; }
          const idM = rest.match(/#([a-zA-Z0-9_-]+)/);
          if (idM) { if (el.getAttribute("id") !== idM[1]) return false; rest = rest.replace(idM[0], ""); matched = true; }
          const clsRe = /\.([a-zA-Z0-9_-]+)/g; let m;
          while ((m = clsRe.exec(rest))) { if (!el.classList.contains(m[1])) return false; matched = true; }
          const attrRe = /\[([a-zA-Z0-9_-]+)(?:=(?:"([^"]*)"|([^\]]*)))?\]/g;
          while ((m = attrRe.exec(rest))) {
            if (!el.hasAttribute(m[1])) return false;
            const want = m[2] !== undefined ? m[2] : m[3];
            if (want !== undefined && el.getAttribute(m[1]) !== want) return false;
            matched = true;
          }
          return matched;
        }

        function build50ServerEnv(opts = {}) {
          const docRoot = new Node("html", {"data-row-window": ""});
          const bodyRoot = new Node("body");
          docRoot.appendChild(bodyRoot);

          const hero = new Node("header", {class: "card hero h-critical"});
          const h1 = new Node("h1"); h1.textContent = "Linux health check";
          const hscope = new Node("div", {class: "hscope"}); hscope.textContent = "SITEA \u00b7 corp.example.mil \u00b7 2026-11-28 08:00 UTC";
          hero.appendChild(h1); hero.appendChild(hscope);
          bodyRoot.appendChild(hero);

          const sec = new Node("details", {class: "card sec srv-sec", id: "s1"});
          const sum = new Node("summary");
          const h2 = new Node("h2"); h2.textContent = "Servers";
          sum.appendChild(h2); sec.appendChild(sum);
          const sbody = new Node("div", {class: "body"});

          const tools = new Node("div", {class: "tools2 srv-tools"});
          const flt = new Node("input", {class: "filter", type: "search"});
          const shownSpan = new Node("span", {class: "shown"});
          const sp = new Node("span", {class: "sp"});
          const expBtn = new Node("button", {class: "btn srv-exp", type: "button"}); expBtn.textContent = "Expand all";
          const colBtn = new Node("button", {class: "btn srv-col", type: "button"}); colBtn.textContent = "Collapse all";
          const csvBtn = new Node("button", {class: "btn csv", type: "button"}); csvBtn.textContent = "CSV";
          [flt, shownSpan, sp, expBtn, colBtn, csvBtn].forEach(x => tools.appendChild(x));
          sbody.appendChild(tools);

          const tw = new Node("div", {class: "tw"});
          const tbl = new Node("table", {class: "xt srv-tbl"});
          const thead = new Node("thead");
          const htr = new Node("tr");
          [["k-x", "Open"], ["k-wrap", "Server"], ["k-nw", "Status"], ["k-long", "Worst finding"]].forEach(([c, t]) => {
            const th = new Node("th", {class: c}); th.textContent = t; htr.appendChild(th);
          });
          thead.appendChild(htr); tbl.appendChild(thead);
          const tbody = new Node("tbody");
          servers.forEach((s, idx) => {
            const tr = new Node("tr", {class: "r-" + s.sev, "data-sev": s.sev, id: "srv-" + idx});
            const tdX = new Node("td", {class: "k-x"});
            const xb = new Node("button", {class: "xb", type: "button", "aria-expanded": "false"});
            tdX.appendChild(xb);
            const tdN = new Node("td", {class: "k-wrap srv-name", "data-l": "Server", "data-v": s.host});
            const hostEl = new Node("strong", {class: "srv-host mono"}); hostEl.textContent = s.host;
            const ipEl = new Node("span", {class: "srv-ip mono"}); ipEl.textContent = s.ip;
            tdN.appendChild(hostEl); tdN.appendChild(ipEl);
            const tdS = new Node("td", {class: "nw", "data-l": "Status", "data-v": s.statusLabel}); tdS.textContent = s.statusLabel;
            const tdW = new Node("td", {class: "k-long srv-worst", "data-l": "Worst finding", "data-v": s.worst}); tdW.textContent = s.worst;
            [tdX, tdN, tdS, tdW].forEach(x => tr.appendChild(x));
            tbody.appendChild(tr);

            const xr = new Node("tr", {class: "xr"}); xr.hidden = true;
            const xd = new Node("td", {class: "xd srv-xd", colspan: "4"});
            const det = new Node("div", {class: "srv-detail"});
            s.findings.forEach(f => {
              const art = new Node("article", {class: "srv-finding sf-" + f.sev});
              const fh = new Node("div", {class: "srv-fhead"});
              const ch = new Node("span", {class: "chip s-" + f.sev}); ch.textContent = f.sevLabel;
              const h3 = new Node("h3"); h3.textContent = f.title;
              fh.appendChild(ch); fh.appendChild(h3); art.appendChild(fh);
              if (f.evidence) { const ev = new Node("p", {class: "srv-ev mono"}); ev.textContent = f.evidence; art.appendChild(ev); }
              if (f.whatToDo) { const fx = new Node("p", {class: "srv-fix"}); fx.textContent = "What to do: " + f.whatToDo; art.appendChild(fx); }
              if (s.hint) {
                const code = new Node("div", {class: "code"});
                const pre = new Node("pre"); pre.textContent = s.hint;
                const cp = new Node("button", {class: "copy", type: "button"}); cp.hidden = true; cp.textContent = "Copy";
                code.appendChild(pre); code.appendChild(cp); art.appendChild(code);
              }
              det.appendChild(art);
            });
            xd.appendChild(det); xr.appendChild(xd); tbody.appendChild(xr);
          });
          tbl.appendChild(tbody); tw.appendChild(tbl); sbody.appendChild(tw);
          sec.appendChild(sbody); bodyRoot.appendChild(sec);

          const canvasLog = [];
          const writes = [];
          const textWrites = [];
          const doc = {
            readyState: "complete",
            title: "Linux health check \u00b7 SITEA",
            documentElement: docRoot,
            body: bodyRoot,
            querySelector: s => docRoot.querySelector(s),
            querySelectorAll: s => docRoot.querySelectorAll(s),
            getElementById: id => docRoot.querySelector("#" + id),
            addEventListener() {},
            createTextNode: t => ({ nodeType: 3, textContent: String(t) }),
            createElementNS: (_, tag) => new Node(tag),
            execCommand: () => true,
            createElement: tag => {
              const n = new Node(tag);
              if (tag === "textarea") { n.select = () => { textWrites.push(n.value); }; }
              if (tag === "canvas") {
                const texts = [];
                n.getContext = () => ({
                  scale() {}, createLinearGradient: () => ({ addColorStop() {} }),
                  fillRect() {}, strokeRect() {}, beginPath() {}, moveTo() {}, lineTo() {}, arc() {}, stroke() {}, fill() {},
                  fillText: t => { texts.push(String(t)); },
                  measureText: s => ({ width: String(s).length * 6.5 }),
                });
                n.toDataURL = () => { canvasLog.push({ width: n.width, height: n.height, texts: texts.slice() }); return FAKE_PNG; };
              }
              return n;
            },
          };

          const win = {
            isSecureContext: true,
            innerHeight: 900,
            addEventListener() {},
            ClipboardItem: class ClipboardItem {
              constructor(parts) { this.parts = parts; }
              static supports(t) { return opts.noPic ? t !== "image/png" : true; }
            },
          };
          const clipboard = {
            write: items => { writes.push(items[0].parts); return Promise.resolve(); },
            writeText: t => { textWrites.push(t); return Promise.resolve(); },
          };
          const ctx = {
            window: win, document: doc, navigator: { clipboard },
            location: { protocol: "https:", href: "https://portal.corp.example.mil/reports/linux-health/latest.html" },
            localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
            Blob, Promise, Uint8Array, atob: s => Buffer.from(s, "base64").toString("binary"),
            ClipboardItem: win.ClipboardItem,
            getComputedStyle: () => ({ display: "block", position: "static" }),
            setTimeout: fn => { return 0; }, clearTimeout() {}, URL, console,
          };
          ctx.globalThis = ctx;
          win.document = doc; win.navigator = ctx.navigator; win.location = ctx.location;
          return { ctx, win, doc, sec, sbody, tools, tbl, tbody, flt, writes, textWrites, canvasLog };
        }

        async function runAll() {
          const env = build50ServerEnv();
          vm.runInNewContext(reportSrc, env.ctx);

          // 1. Copy all shown is placed beside Expand all / Collapse all in .srv-tools with badge count 50.
          const copyAll = env.tools.querySelector(".srv-copy-all");
          assert(copyAll, "Copy all shown control must exist in .srv-tools");
          const toolKids = env.tools.children;
          const expIdx = toolKids.indexOf(env.tools.querySelector(".srv-exp"));
          const allIdx = toolKids.indexOf(copyAll);
          assert(allIdx > expIdx && allIdx <= expIdx + 2, "Copy all shown must sit beside Expand/Collapse all");
          assert.strictEqual(copyAll.querySelector(".cnt").textContent, "50");

          // Every server row got a per-row Copy control (50 rows), and colspan incremented 4 -> 5.
          const rowSplits = env.tbody.querySelectorAll("td.srv-copy-td .copy-split");
          assert.strictEqual(rowSplits.length, 50);
          const firstXd = env.tbody.querySelector("tr.xr td.srv-xd");
          assert.strictEqual(firstXd.getAttribute("colspan"), "5");

          // 2. Click Copy all shown (default = both): assert exact 50-server TEXT format + HTML + ~700px bounded PICTURE.
          copyAll.querySelector(".copy-main").dispatch("click");
          await new Promise(r => setImmediate(r));
          assert.strictEqual(env.writes.length, 1);
          const allPlain = await env.writes[0]["text/plain"].text();
          const allHtml = await env.writes[0]["text/html"].text();
          const lines = allPlain.split("\n");

          // Line 1: <site> <report> · <date time> · n critical, n warning, n OK (+ not reached)
          assert.strictEqual(
            lines[0],
            "SITEA Linux health check \u00b7 2026-11-28 08:00 UTC \u00b7 6 critical, 16 warning, 25 OK, 3 not reached",
            "Line 1 mismatch: " + lines[0]
          );

          // Order: Critical (6) -> Not reached (3) -> Warning (16) -> 25 servers OK -> Full report: <url>
          const iCrit = allPlain.indexOf("Critical (6)");
          const iUnk = allPlain.indexOf("Not reached (3)");
          const iWarn = allPlain.indexOf("Warning (16)");
          const iOk = allPlain.indexOf("25 servers OK");
          const iLink = allPlain.indexOf("Full report: https://portal.corp.example.mil/reports/linux-health/latest.html");
          assert(iCrit > 0 && iUnk > iCrit && iWarn > iUnk && iOk > iWarn && iLink > iWarn && iLink > iOk,
            "Section ordering wrong:\n" + allPlain);

          // Critical: all 6 listed, with (+n more findings)
          for (const h of ["web01.corp.example.mil", "db01.corp.example.mil", "app01.corp.example.mil",
                           "auth01.corp.example.mil", "mail01.corp.example.mil", "proxy01.corp.example.mil"]) {
            assert(allPlain.includes("\u2022 " + h + " - "), "Missing critical host " + h);
          }
          assert(allPlain.includes("\u2022 web01.corp.example.mil - Disk /var at 95% used (crit 90%) (+3 more findings)"));
          assert(allPlain.includes("\u2022 db01.corp.example.mil - MariaDB replication lag 420 s (crit 300 s) (+1 more finding)"));
          // Single-finding critical has no (+n more)
          assert(allPlain.includes("\u2022 mail01.corp.example.mil - Postfix mail queue backed up (1,840 msgs)\n"));

          // Not reached: all 3 listed with critical-level visibility (uncapped, before Warning)
          for (const h of ["edge01.corp.example.mil", "edge02.corp.example.mil", "edge03.corp.example.mil"]) {
            assert(allPlain.includes("\u2022 " + h + " - "), "Missing not-reached host " + h);
          }

          // Warning: max 10 lines + '• ... 6 more'
          for (let i = 1; i <= 10; i++) {
            const hn = "wrk" + String(i).padStart(2, "0") + ".corp.example.mil";
            assert(allPlain.includes("\u2022 " + hn + " - "), "Missing warning host " + hn);
          }
          for (let i = 11; i <= 16; i++) {
            const hn = "wrk" + String(i).padStart(2, "0") + ".corp.example.mil";
            assert(!allPlain.includes(hn), "Warning host past cap 10 must not be listed: " + hn);
          }
          assert(allPlain.includes("\u2022 ... 6 more"), "Missing warning overflow line");

          // OK servers counted only ('25 servers OK'), NEVER listed
          for (let i = 1; i <= 25; i++) {
            const hn = "oknode" + String(i).padStart(2, "0") + ".corp.example.mil";
            assert(!allPlain.includes(hn), "OK host must NEVER be listed in plain: " + hn);
            assert(!allHtml.includes(hn), "OK host must NEVER be listed in html: " + hn);
          }

          // Last line is Full report: <url>
          assert.strictEqual(lines[lines.length - 1], "Full report: https://portal.corp.example.mil/reports/linux-health/latest.html");

          // Rich HTML has <strong> bold, escaped angle brackets, safe link, and embedded PNG width=700
          assert(allHtml.includes("<strong>Critical (6)</strong>"));
          assert(allHtml.includes("<strong>web01.corp.example.mil</strong>"));
          assert(allHtml.includes("&lt;app01&gt;"));
          assert(!allHtml.includes("<app01>"));
          assert(allHtml.includes('<a href="https://portal.corp.example.mil/reports/linux-health/latest.html">'));
          assert(allHtml.includes('<img width="700"'));
          assert(allHtml.includes(FAKE_PNG));

          // Picture size bounded (~700 px logical = 1400 px @2x; height bounded for 50 servers)
          assert.strictEqual(env.canvasLog.length, 1);
          const allPic = env.canvasLog[0];
          assert.strictEqual(allPic.width, 1400, "Teams card canvas width should be 700*2=1400");
          assert(allPic.height > 400 && allPic.height <= 2000, "50-server card height must stay bounded, got " + allPic.height);
          const picJoined = allPic.texts.join("\n");
          assert(picJoined.includes("6 critical") && picJoined.includes("16 warning") && picJoined.includes("25 OK"));
          assert(picJoined.includes("web01.corp.example.mil") && picJoined.includes("edge01.corp.example.mil"));
          assert(picJoined.includes("+6 more warning servers in full report"));
          assert(picJoined.includes("25 servers OK"));
          assert(!picJoined.includes("oknode01.corp.example.mil"), "OK server must NEVER be drawn in picture");
          assert(picJoined.includes("Full report: https://portal.corp.example.mil/reports/linux-health/latest.html"));

          // 3. Single server Copy (web01): status first, ALL 4 findings worst-first, each with What to do, report link last.
          const web01Split = rowSplits[0];
          // Test 'text' mode via dropdown menu option (NO <img>)
          web01Split.querySelector('.copy-opt[data-mode="text"]').dispatch("click");
          await new Promise(r => setImmediate(r));
          assert.strictEqual(env.writes.length, 2);
          const onePlain = await env.writes[1]["text/plain"].text();
          const oneHtml = await env.writes[1]["text/html"].text();
          assert(!oneHtml.includes("<img"), "Copy text must NOT include <img>");
          const oneLines = onePlain.split("\n");
          assert(oneLines[0].startsWith("[CRITICAL] web01.corp.example.mil (192.0.2.10)"), "Header wrong: " + oneLines[0]);
          // Findings sorted worst-first: 2 criticals -> 1 warning -> 1 info (was reversed in DOM!)
          assert(oneLines[1].includes("[CRITICAL] Disk /var at 95% used") && oneLines[1].includes("What to do: Rotate /var/log"));
          assert(oneLines[2].includes("[CRITICAL] Service nginx.service failed") && oneLines[2].includes("What to do: Inspect journalctl"));
          assert(oneLines[3].includes("[WARNING] TLS certificate expires in 14 days") && oneLines[3].includes("What to do: Renew TLS"));
          assert(oneLines[4].includes("[INFO] Package cache updated 3 d ago") && oneLines[4].includes("What to do: No action required."));
          assert.strictEqual(oneLines[oneLines.length - 1], "Full report: https://portal.corp.example.mil/reports/linux-health/latest.html");

          // Test 'picture' mode via dropdown menu option -> image/png Blob
          web01Split.querySelector('.copy-opt[data-mode="picture"]').dispatch("click");
          await new Promise(r => setImmediate(r));
          assert.strictEqual(env.writes.length, 3);
          assert(env.writes[2]["image/png"], "Copy picture must write image/png Blob");
          assert(!env.writes[2]["text/html"], "Copy picture must not write text/html");
          const onePic = env.canvasLog[env.canvasLog.length - 1];
          assert.strictEqual(onePic.width, 1400);
          assert(onePic.height <= 1200);

          // 4. Filter active: filter to 'db01' -> Copy all shown copies ONLY 1 shown server!
          env.flt.value = "db01";
          env.flt.dispatch("input");
          assert.strictEqual(copyAll.querySelector(".cnt").textContent, "1");
          copyAll.querySelector('.copy-opt[data-mode="text"]').dispatch("click");
          await new Promise(r => setImmediate(r));
          const fltPlain = await env.writes[3]["text/plain"].text();
          assert(fltPlain.includes("1 critical, 0 warning, 0 OK"));
          assert(fltPlain.includes("db01.corp.example.mil"));
          assert(!fltPlain.includes("web01.corp.example.mil"));

          // Filter to OK only -> OK servers still counted only, never listed
          env.flt.value = "oknode";
          env.flt.dispatch("input");
          assert.strictEqual(copyAll.querySelector(".cnt").textContent, "25");
          copyAll.querySelector('.copy-opt[data-mode="text"]').dispatch("click");
          await new Promise(r => setImmediate(r));
          const okOnlyPlain = await env.writes[4]["text/plain"].text();
          assert(okOnlyPlain.includes("0 critical, 0 warning, 25 OK"));
          assert(okOnlyPlain.includes("25 servers OK"));
          assert(!okOnlyPlain.includes("oknode01.corp.example.mil"));

          // Filter to zero matches -> Copy all shown disabled
          env.flt.value = "no-such-server-zzz";
          env.flt.dispatch("input");
          assert.strictEqual(copyAll.querySelector(".cnt").textContent, "0");
          assert.strictEqual(copyAll.querySelector(".copy-main").disabled, true);

          // 5. Command/code copy blocks (.code .copy) UNCHANGED: raw command only!
          const cmdBtn = env.tbody.querySelector(".code .copy");
          assert(cmdBtn && !cmdBtn.closest(".copy-split"), "Command Copy button must NOT be wrapped in .copy-split");
          env.textWrites.length = 0;
          cmdBtn.dispatch("click");
          await new Promise(r => setImmediate(r));
          assert.strictEqual(env.textWrites[0], "df -h /var && systemctl status --failed");

          // 6. Double-run safety on saved page (simulate saved-after-run DOM reopened in fresh window)
          const savedEnv = build50ServerEnv();
          vm.runInNewContext(reportSrc, savedEnv.ctx);
          // Simulate browser Save-As: keep mutated DOM, mark stale state, delete window.siteReport, re-run!
          const staleBtn = savedEnv.tools.querySelector(".srv-copy-all .copy-main");
          staleBtn.disabled = true; staleBtn.classList.add("st-good", "done");
          const dupBar = savedEnv.doc.createElement("div"); dupBar.className = "scopy-bar st-bad";
          savedEnv.sbody.appendChild(dupBar);
          delete savedEnv.ctx.window.siteReport;
          // Clear _splitBound / _srvCopyAllBound flags as a freshly parsed saved HTML document has no JS properties
          const stripProps = n => {
            delete n._splitBound; delete n._srvCopyAllBound; delete n._copyBound; delete n._capApply;
            (n.children || []).forEach(stripProps);
          };
          stripProps(savedEnv.doc.documentElement);
          vm.runInNewContext(reportSrc, savedEnv.ctx);
          assert.strictEqual(savedEnv.tbl.tHead.rows[0].querySelectorAll("th.srv-copy-th").length, 1);
          assert.strictEqual(savedEnv.tbody.querySelectorAll("td.srv-copy-td").length, 50);
          assert.strictEqual(savedEnv.tools.querySelectorAll(".srv-copy-all").length, 1);
          assert.strictEqual(savedEnv.sbody.querySelectorAll(".scopy-bar").length, 1);
          assert.strictEqual(savedEnv.tbody.querySelector("tr.xr td.srv-xd").getAttribute("colspan"), "5");
          assert.strictEqual(savedEnv.tools.querySelector(".srv-copy-all .copy-main").disabled, false);

          // 7. Picture mode fallback when image/png unsupported -> Text only + why!
          const noPicEnv = build50ServerEnv({ noPic: true });
          vm.runInNewContext(reportSrc, noPicEnv.ctx);
          const firstRowSp = noPicEnv.tbody.querySelector("td.srv-copy-td .copy-split");
          firstRowSp.querySelector('.copy-opt[data-mode="picture"]').dispatch("click");
          await new Promise(r => setImmediate(r));
          assert.strictEqual(firstRowSp.querySelector(".copy-main .t").textContent, "Text only");
          assert(noPicEnv.sbody.querySelector(".scopy-bar .st").textContent.includes("does not support picture copying"));
        }

        runAll().catch(e => { console.error(e); process.exit(1); });
        """
        subprocess.run(
            ["node", "-e", driver, os.path.join(TEMPLATES, "site_report.js"), json.dumps(fixture)],
            check=True,
        )

    def test_portal_copy_helper_and_server_lists(self):
        fixture = make_50_server_fixture()
        driver = r"""
        const fs = require("fs");
        const vm = require("vm");
        const assert = require("assert");

        const portalSrc = fs.readFileSync(process.argv[1], "utf8");
        const wallFleetSrc = fs.readFileSync(process.argv[2], "utf8");
        const servers = JSON.parse(process.argv[3]);
        const FAKE_PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aU9sAAAAASUVORK5CYII=";

        const canvasCalls = [];
        const doc = {
          hidden: false,
          documentElement: { classList: { add() {}, toggle() {} } },
          body: { dataset: {}, appendChild() {}, removeChild() {} },
          querySelector: () => null,
          querySelectorAll: () => [],
          getElementById: () => null,
          addEventListener() {},
          createElement: tag => ({
            tagName: tag.toUpperCase(), className: "", children: [], style: {},
            setAttribute() {}, appendChild(c) { this.children.push(c); return c; },
            append(...xs) { this.children.push(...xs); },
            addEventListener() {}, querySelectorAll: () => [],
            getContext: () => ({
              scale() {}, createLinearGradient: () => ({ addColorStop() {} }),
              fillRect() {}, strokeRect() {}, fillText() {},
              measureText: s => ({ width: String(s).length * 6.5 }),
            }),
            toDataURL() { canvasCalls.push({ w: this.width, h: this.height }); return FAKE_PNG; },
          }),
          createTextNode: t => ({ nodeType: 3, textContent: String(t) }),
        };
        const win = { document: doc, setInterval: () => 0, isSecureContext: true, addEventListener() {}, removeEventListener() {} };
        const ctx = {
          window: win, document: doc, navigator: {}, location: { href: "https://portal.corp.example.mil/portal/wall?area=monitor" },
          setInterval: () => 0, setTimeout: () => 0, clearTimeout() {},
          Blob, Promise, Uint8Array, atob: s => Buffer.from(s, "base64").toString("binary"), console,
        };
        vm.runInNewContext(portalSrc, ctx);
        const pc = win.SAPortalCopy;
        assert(pc, "portal.js must export window.SAPortalCopy");

        // Wall-fleet reuses window.SAPortalCopy when loaded in portal shell.
        vm.runInNewContext(wallFleetSrc, ctx);
        assert.strictEqual(win.SAWallFleet.copy, pc, "wall-fleet.js must reuse window.SAPortalCopy");

        // Verify 50-server formatting & bounded picture in portal helper
        const copyCtx = { site: "SITEA", report: "Ops monitor", dateTime: "2026-11-28 08:00 UTC", url: "https://portal.corp.example.mil/portal/wall?area=monitor" };
        const res = pc.formatAllShown(servers, copyCtx);
        const lines = res.plain.split("\n");
        assert.strictEqual(lines[0], "SITEA Ops monitor \u00b7 2026-11-28 08:00 UTC \u00b7 6 critical, 16 warning, 25 OK, 3 not reached");
        assert(res.plain.includes("\u2022 ... 6 more"));
        assert(res.plain.includes("25 servers OK"));
        assert(!res.plain.includes("oknode01.corp.example.mil"));
        assert.strictEqual(lines[lines.length - 1], "Full report: https://portal.corp.example.mil/portal/wall?area=monitor");
        assert(res.html.includes("<strong>Critical (6)</strong>"));

        const pic = pc.drawAllShownCard(servers, copyCtx);
        assert.strictEqual(pic.width, 700);
        assert(pic.height <= 1000);
        assert.strictEqual(pic.dataUrl, FAKE_PNG);

        const one = pc.formatOneServer(servers[0], copyCtx);
        assert(one.plain.startsWith("[CRITICAL] web01.corp.example.mil (192.0.2.10)"));
        assert(one.plain.includes("What to do: Rotate /var/log"));
        assert(one.plain.endsWith("Full report: https://portal.corp.example.mil/portal/wall?area=monitor"));
        """
        subprocess.run(
            ["node", "-e", driver,
             os.path.join(PORTAL_STATIC, "portal.js"),
             os.path.join(PORTAL_STATIC, "wall-fleet.js"),
             json.dumps(fixture)],
            check=True,
        )

    def test_portal_server_list_files_wire_copy_and_preserve_raw_command_copy(self):
        for fname in ("wall-fleet.js", "wall-health.js", "ad-page.js", "wall-page.js"):
            with open(os.path.join(PORTAL_STATIC, fname), encoding="utf-8") as fh:
                src = fh.read()
            self.assertIn("Copy all shown", src, fname)
            self.assertIn("createSplitControl", src, fname)
            self.assertNotIn("innerHTML", src, fname)
        # AD page's raw command copy button (.adp-copy) stays raw command only
        with open(os.path.join(PORTAL_STATIC, "ad-page.js"), encoding="utf-8") as fh:
            ad_src = fh.read()
        self.assertIn("navigator.clipboard.writeText(code.textContent)", ad_src)


if __name__ == "__main__":
    unittest.main()
