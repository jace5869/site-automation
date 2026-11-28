#!/usr/bin/env python3
"""1.140.0 tests for report pages' 'Copy both' (text + embedded PNG picture in HTML clipboard,
4 states: copying, copied, text only + why, copy failed + why), wall-fleet.js helper reuse,
double-run safety on saved pages, and email templates left unchanged."""
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

SAMPLE_REPORT = {
    "title": "Network health report",
    "subtitle": "SITEA · corp.example.mil · 28 November 2026",
    "status": "ok",
    "summary": [
        {"label": "Latest latency", "value": "18 ms", "status": "ok"},
        {"label": "Peak", "value": "32 ms", "status": "ok"},
    ],
    "sections": [
        {
            "title": "Latency snapshot",
            "status": "ok",
            "columns": ["Host", "Address", "Latency", "Status"],
            "rows": [
                ["gw01.corp.example.mil", "192.0.2.24", "18 ms", "Within limit"],
                ["gw02.corp.example.mil", "192.0.2.25", "24 ms", "Within limit"],
            ],
        },
        {
            "title": "Checks",
            "status": "warning",
            "lines": ["All samples below 40 ms warning threshold", "Peak 32 ms at 04:40 UTC"],
        },
    ],
}


def render_template(name, report=None):
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATES),
        autoescape=False,
    )
    env.filters.update(sf.FilterModule().filters())
    tpl = env.get_template(name)
    return tpl.render(
        _report=report or SAMPLE_REPORT,
        _email_footer="SITEA / corp.example.mil · AAP job 1400",
        _page_logo_cid="",
        _page_logo_size=[],
        _page_runs=[],
        report_email_header_text="SITEA operations report",
    )


class CopyPictureTemplates(unittest.TestCase):
    def test_web_templates_have_copy_both_and_four_state_styles(self):
        for tpl in ("report_atlas.html.j2", "report_brief.html.j2", "report_ops.html.j2"):
            with self.subTest(template=tpl):
                html = render_template(tpl)
                self.assertEqual(html.count('<button class="copy scopy" type="button" hidden'), 2)
                self.assertIn('<span class="t">Copy both</span>', html)
                self.assertIn('aria-label="Copy Latency snapshot"', html)
                for cls in (".scopy.st-info", ".scopy.st-good", ".scopy.st-warn", ".scopy.st-bad",
                            ".scopy-bar", ".scopy-bar.st-info", ".scopy-bar.st-good", ".scopy-bar.st-warn", ".scopy-bar.st-bad",
                            "@media print{.scopy-bar{display:none!important}}"):
                    self.assertIn(cls, html)
                if tpl == "report_ops.html.j2":
                    self.assertNotIn(".scopy .t{display:none}", html)

    def test_email_templates_unchanged(self):
        for tpl in ("report.html.j2", "report_command.html.j2", "report_portal.html.j2", "report_glass.html.j2"):
            with self.subTest(template=tpl):
                with open(os.path.join(TEMPLATES, tpl), encoding="utf-8") as fh:
                    raw = fh.read()
                self.assertNotIn("scopy-bar", raw)
                self.assertNotIn("Copy both", raw)

    def test_site_report_js_invariants(self):
        path = os.path.join(TEMPLATES, "site_report.js")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        src.encode("ascii")
        for forbidden in ("{{", "{%", "{#", "</script", ".innerHTML"):
            self.assertNotIn(forbidden, src)
        subprocess.run(["node", "--check", path], check=True)
        subprocess.run(["node", "--check", os.path.join(PORTAL_STATIC, "wall-fleet.js")], check=True)


class CopyPictureBehavior(unittest.TestCase):
    def test_copy_both_states_double_run_and_wall_helper_reuse(self):
        js_driver = r"""
        const fs = require("fs");
        const vm = require("vm");
        const assert = require("assert");

        const reportSrc = fs.readFileSync(process.argv[1], "utf8");
        const wallSrc = fs.readFileSync(process.argv[2], "utf8");
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
          removeChild(c) {
            const i = this.children.indexOf(c);
            if (i >= 0) { this.children.splice(i, 1); c.parentNode = null; }
            return c;
          }
          remove() { if (this.parentNode) this.parentNode.removeChild(this); }
          addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); }
          dispatch(ev, e = {}) {
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
          if (tagM) {
            if (el.tagName !== tagM[1].toUpperCase()) return false;
            rest = rest.slice(tagM[0].length);
            matched = true;
          }
          const idM = rest.match(/#([a-zA-Z0-9_-]+)/);
          if (idM) {
            if (el.getAttribute("id") !== idM[1]) return false;
            rest = rest.replace(idM[0], "");
            matched = true;
          }
          const clsRe = /\.([a-zA-Z0-9_-]+)/g;
          let m;
          while ((m = clsRe.exec(rest))) {
            if (!el.classList.contains(m[1])) return false;
            matched = true;
          }
          const attrRe = /\[([a-zA-Z0-9_-]+)(?:=(?:"([^"]*)"|([^\]]*)))?\]/g;
          while ((m = attrRe.exec(rest))) {
            if (!el.hasAttribute(m[1])) return false;
            const want = m[2] !== undefined ? m[2] : m[3];
            if (want !== undefined && el.getAttribute(m[1]) !== want) return false;
            matched = true;
          }
          return matched;
        }

        function makeEnv(opts = {}) {
          const docRoot = new Node("html");
          const bodyRoot = new Node("body");
          docRoot.appendChild(bodyRoot);
          const h1 = new Node("h1"); h1.textContent = "Network health report";
          bodyRoot.appendChild(h1);

          const sec = new Node("details", {class: "card sec", id: "s1"});
          const sum = new Node("summary");
          const h2 = new Node("h2"); h2.textContent = "Latency snapshot";
          const chip = new Node("span", {class: "chip s-ok"}); chip.textContent = "OK";
          const btn = new Node("button", {class: "copy scopy", type: "button", title: "Copy this section as text and picture"});
          btn.hidden = true;
          const lbl = new Node("span", {class: "t"}); lbl.textContent = "Copy both";
          btn.appendChild(lbl);
          sum.appendChild(h2); sum.appendChild(chip); sum.appendChild(btn);
          sec.appendChild(sum);

          const sbody = new Node("div", {class: "body"});
          const ul = new Node("ul", {class: "lines"});
          const li1 = new Node("li"); li1.textContent = "Latest 18 ms <gw01>";
          const li2 = new Node("li"); li2.textContent = "Peak 32 ms";
          ul.appendChild(li1); ul.appendChild(li2);
          sbody.appendChild(ul);
          sec.appendChild(sbody);
          bodyRoot.appendChild(sec);

          if (opts.savedDirty) {
            btn.hidden = false;
            btn.disabled = true;
            btn.classList.add("done", "st-good");
            lbl.textContent = "Copied";
            const b1 = new Node("div", {class: "scopy-bar st-good"});
            const g1 = new Node("span", {class: "sg"}); g1.textContent = "\u2713";
            const t1 = new Node("span", {class: "st"}); t1.textContent = "Old message";
            b1.appendChild(g1); b1.appendChild(t1);
            const b2 = new Node("div", {class: "scopy-bar st-bad"});
            sbody.appendChild(b1); sbody.appendChild(b2);
          }

          const copiedExec = [];
          const doc = {
            readyState: "complete",
            title: "Network health report",
            documentElement: docRoot,
            body: bodyRoot,
            querySelector: s => docRoot.querySelector(s),
            querySelectorAll: s => docRoot.querySelectorAll(s),
            getElementById: () => null,
            addEventListener() {},
            createTextNode: t => ({ nodeType: 3, textContent: String(t) }),
            execCommand: cmd => {
              if (opts.execFail) return false;
              copiedExec.push(cmd);
              return true;
            },
            createElement: tag => {
              const n = new Node(tag);
              if (tag === "textarea") { n.select = () => { copiedExec.lastValue = n.value; }; }
              if (tag === "canvas") {
                n.getContext = () => opts.noCanvas ? null : ({
                  scale() {}, createLinearGradient: () => ({ addColorStop() {} }),
                  fillRect() {}, strokeRect() {}, fillText() {}, beginPath() {}, moveTo() {}, lineTo() {}, arc() {}, stroke() {}, fill() {},
                  measureText: s => ({ width: String(s).length * 7 }),
                });
                n.toDataURL = () => opts.noCanvas ? "" : FAKE_PNG;
              }
              return n;
            },
          };

          const win = {
            isSecureContext: opts.secure !== undefined ? opts.secure : true,
            addEventListener() {},
            ClipboardItem: opts.noClipboardItem ? undefined : class ClipboardItem {
              constructor(parts) { this.parts = parts; }
              static supports(t) { return opts.htmlSupported !== false || t !== "text/html"; }
            },
          };
          if (opts.wallHelper) win.SAWallFleet = { copy: opts.wallHelper };

          const ctx = {
            window: win,
            document: doc,
            navigator: { clipboard: opts.clipboard },
            location: { protocol: opts.secure === false ? "http:" : "https:", href: "https://portal.corp.example.mil/reports/latest.html" },
            localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
            Blob,
            Promise,
            ClipboardItem: win.ClipboardItem,
            getComputedStyle: () => ({ display: "block", position: "static" }),
            setTimeout, clearTimeout,
            URL,
            console,
          };
          ctx.globalThis = ctx;
          win.document = doc;
          win.navigator = ctx.navigator;
          win.location = ctx.location;
          return { ctx, win, doc, sec, sbody, btn, lbl, copiedExec };
        }

        async function runAll() {
          // 1. Happy path: Copying... -> Copied with text/plain + text/html containing text AND embedded PNG <img>.
          {
            let resolveWrite;
            const writes = [];
            const env = makeEnv({
              clipboard: {
                write: items => {
                  writes.push(items[0].parts);
                  return new Promise(res => { resolveWrite = res; });
                }
              }
            });
            vm.runInNewContext(reportSrc, env.ctx);
            assert.strictEqual(env.btn.hidden, false);
            assert.strictEqual(env.lbl.textContent, "Copy both");
            const ev = env.btn.dispatch("click");
            assert.strictEqual(ev.defaultPrevented, true);
            assert.strictEqual(ev.stopped, true);
            // Synchronously in Copying... state and write() already invoked.
            assert.strictEqual(writes.length, 1);
            assert.strictEqual(env.btn.disabled, true);
            assert.strictEqual(env.lbl.textContent, "Copying\u2026");
            const bar = env.sbody.querySelector(".scopy-bar");
            assert.strictEqual(bar.hidden, false);
            assert.strictEqual(bar.classList.contains("st-info"), true);
            assert.strictEqual(bar.querySelector(".sg").textContent, "\u25f7");
            assert.strictEqual(bar.querySelector(".st").textContent, "Writing text and picture\u2026");

            // Check clipboard payload has BOTH escaped text AND embedded PNG data URL.
            const plain = await writes[0]["text/plain"].text();
            const html = await writes[0]["text/html"].text();
            assert(plain.includes("Latest 18 ms <gw01>"));
            assert(html.includes("Latest 18 ms &lt;gw01&gt;"));
            assert(html.includes('<img width="640" style="max-width:100%;height:auto"'));
            assert(html.includes('src="' + FAKE_PNG + '"'));

            resolveWrite();
            await new Promise(r => setImmediate(r));
            assert.strictEqual(env.btn.disabled, false);
            assert.strictEqual(env.lbl.textContent, "Copied");
            assert.strictEqual(env.btn.classList.contains("done"), true);
            assert.strictEqual(env.btn.classList.contains("st-good"), true);
            assert.strictEqual(bar.classList.contains("st-good"), true);
            assert.strictEqual(bar.querySelector(".sg").textContent, "\u2713");
            assert.strictEqual(bar.querySelector(".st").textContent, "Copied text and picture. Ready to paste.");
          }

          // 2. Text only: browser does not support text/html ClipboardItem -> falls back to writeText + why.
          {
            const texts = [];
            const env = makeEnv({
              htmlSupported: false,
              clipboard: { write: async () => assert.fail("should not call write"), writeText: async t => texts.push(t) }
            });
            vm.runInNewContext(reportSrc, env.ctx);
            env.btn.dispatch("click");
            await new Promise(r => setImmediate(r));
            assert.strictEqual(texts.length, 1);
            assert(texts[0].includes("Latest 18 ms <gw01>"));
            assert.strictEqual(env.lbl.textContent, "Text only");
            assert.strictEqual(env.btn.classList.contains("st-warn"), true);
            const bar = env.sbody.querySelector(".scopy-bar");
            assert.strictEqual(bar.querySelector(".sg").textContent, "T");
            assert(bar.querySelector(".st").textContent.includes("does not support picture copying"));
          }

          // 3. Text only: clipboard.write rejects with NotSupportedError -> falls back to writeText + why.
          {
            const texts = [];
            const env = makeEnv({
              clipboard: {
                write: async () => { const e = new Error("no html"); e.name = "NotSupportedError"; throw e; },
                writeText: async t => texts.push(t),
              }
            });
            vm.runInNewContext(reportSrc, env.ctx);
            env.btn.dispatch("click");
            await new Promise(r => setImmediate(r));
            assert.strictEqual(texts.length, 1);
            assert.strictEqual(env.lbl.textContent, "Text only");
            assert.strictEqual(env.sbody.querySelector(".scopy-bar .sg").textContent, "T");
          }

          // 4. Text only: insecure context (plain HTTP / saved file) -> execCommand fallback + HTTPS reason.
          {
            const env = makeEnv({ secure: false, clipboard: undefined });
            vm.runInNewContext(reportSrc, env.ctx);
            env.btn.dispatch("click");
            await new Promise(r => setImmediate(r));
            assert.strictEqual(env.copiedExec[0], "copy");
            assert(env.copiedExec.lastValue.includes("Latest 18 ms <gw01>"));
            assert.strictEqual(env.lbl.textContent, "Text only");
            assert(env.sbody.querySelector(".scopy-bar .st").textContent.includes("needs HTTPS"));
          }

          // 5. Text only: canvas unavailable -> picture cannot be drawn -> falls back to text + why.
          {
            const texts = [];
            const env = makeEnv({
              noCanvas: true,
              clipboard: { write: async () => assert.fail("no picture"), writeText: async t => texts.push(t) }
            });
            vm.runInNewContext(reportSrc, env.ctx);
            env.btn.dispatch("click");
            await new Promise(r => setImmediate(r));
            assert.strictEqual(texts.length, 1);
            assert.strictEqual(env.lbl.textContent, "Text only");
            assert(env.sbody.querySelector(".scopy-bar .st").textContent.includes("Picture could not be drawn"));
          }

          // 6. Copy failed: NotAllowedError -> permission denied reason, button re-enabled.
          {
            const env = makeEnv({
              clipboard: { write: async () => { const e = new Error("denied"); e.name = "NotAllowedError"; throw e; } }
            });
            vm.runInNewContext(reportSrc, env.ctx);
            env.btn.dispatch("click");
            await new Promise(r => setImmediate(r));
            assert.strictEqual(env.btn.disabled, false);
            assert.strictEqual(env.lbl.textContent, "Copy failed");
            assert.strictEqual(env.btn.classList.contains("st-bad"), true);
            const bar = env.sbody.querySelector(".scopy-bar");
            assert.strictEqual(bar.querySelector(".sg").textContent, "!");
            assert(bar.querySelector(".st").textContent.includes("Clipboard access denied"));
          }

          // 7. Double-run safety on a saved page:
          //    (a) dual execution in same window (external + inline script) binds once.
          {
            const writes = [];
            const env = makeEnv({ clipboard: { write: async items => writes.push(items[0].parts) } });
            vm.runInNewContext(reportSrc, env.ctx);
            vm.runInNewContext(reportSrc, env.ctx);
            assert.strictEqual(env.sbody.querySelectorAll(".scopy-bar").length, 1);
            env.btn.dispatch("click");
            await new Promise(r => setImmediate(r));
            assert.strictEqual(writes.length, 1);
          }
          //    (b) saved-after-run DOM reopened resets stale disabled/Copied state and deduplicates .scopy-bar.
          {
            const writes = [];
            const env = makeEnv({ savedDirty: true, clipboard: { write: async items => writes.push(items[0].parts) } });
            vm.runInNewContext(reportSrc, env.ctx);
            assert.strictEqual(env.btn.disabled, false);
            assert.strictEqual(env.lbl.textContent, "Copy both");
            assert.strictEqual(env.btn.classList.contains("done"), false);
            assert.strictEqual(env.sbody.querySelectorAll(".scopy-bar").length, 1);
            assert.strictEqual(env.sbody.querySelector(".scopy-bar").hidden, true);
            env.btn.dispatch("click");
            await new Promise(r => setImmediate(r));
            assert.strictEqual(writes.length, 1);
            assert.strictEqual(env.lbl.textContent, "Copied");
          }

          // 8. Wall helper export and reuse in site_report.js.
          {
            const wallWin = { setInterval: () => 0 };
            const wallCtx = {
              window: wallWin,
              document: { hidden: false, createElement: () => new Node("div") },
              setInterval: () => 0,
              Blob, Promise, console,
            };
            vm.runInNewContext(wallSrc, wallCtx);
            const helper = wallWin.SAWallFleet && wallWin.SAWallFleet.copy;
            assert(helper, "wall-fleet.js must export window.SAWallFleet.copy");
            for (const fn of ["canRichHtml", "isBlocked", "isUnsupported", "pngDataUrl", "embedPictureHtml", "copyTextFallback", "copyBothRich"]) {
              assert.strictEqual(typeof helper[fn], "function", "missing helper " + fn);
            }
            const embedded = helper.embedPictureHtml("<p>hi</p>", FAKE_PNG, 'A "B" <C>');
            assert(embedded.includes('alt="A &quot;B&quot; &lt;C&gt;"'));
            assert(embedded.includes('src="' + FAKE_PNG + '"'));

            // When SAWallFleet.copy is present, site_report.js delegates to it.
            let delegated = 0;
            const spyHelper = Object.assign({}, helper, {
              copyBothRich: opts => { delegated++; opts.onState("good", "Copied", "Copied text and picture. Ready to paste."); opts.onDone(); return Promise.resolve(); }
            });
            const env = makeEnv({ wallHelper: spyHelper, clipboard: {} });
            vm.runInNewContext(reportSrc, env.ctx);
            assert.strictEqual(env.win.siteReport.copyHelp, spyHelper);
            env.btn.dispatch("click");
            assert.strictEqual(delegated, 1);
            assert.strictEqual(env.lbl.textContent, "Copied");
          }
        }

        runAll().catch(e => { console.error(e); process.exit(1); });
        """
        subprocess.run(
            ["node", "-e", js_driver, os.path.join(TEMPLATES, "site_report.js"), os.path.join(PORTAL_STATIC, "wall-fleet.js")],
            check=True,
        )


if __name__ == "__main__":
    unittest.main()
