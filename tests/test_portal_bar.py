#!/usr/bin/env python3
"""1.112.3: the portal's bar never covers a page tab. The bar is the real one (site_portal.shell) with the real stylesheets
(static/portal.css, netmap.css), measured in headless Chrome at 360 to 1280 px, in the ops, atlas and brief looks, light and
dark. Four bars each: every tab (Network map and Active Directory on) with both counts and a long user name, the usual portal
name and a long one; four tabs; and the worst case - a logo, a long portal name, a long marking banner, a 150-character user
name and a map toast showing. In every bar: each tab, count, the portal's name and the Auto/Light/Dark switch is shown, whole,
inside the window and over nothing else in the bar; the map's toast is under the bar; the page does not scroll sideways. At
800 px the tabs take at most two rows; at 1280 px with the usual name the bar is one row. Each frame posts its own measures to
the host page (file:// frames are not same-origin). Needs the portal's own Python (3.7+: site_portal is imported).
  python tests/test_portal_bar.py          (skips without google-chrome or chromium - in CI it fails instead)"""
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import unittest
import zlib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = os.path.join(REPO, "roles", "site_portal_stack", "files")
sys.path[:0] = [os.path.join(REPO, "plugins", "filter"), os.path.join(REPO, "plugins", "module_utils"), FILES]

WIDTHS = (360, 640, 700, 800, 900, 1000, 1001, 1100, 1280)
LOOKS = ("ops", "atlas", "brief")
THEMES = ("light", "dark")
TABS = ["Actions", "Jobs", "Reports", "Approvals", "Network map", "Active Directory"]
USER = "WILLIAMS-JOHNSON.ALEXANDRA.Q.1234567890"                      # test data: a long certificate name (39 characters)
LONG_USER = "WILLIAMS-JOHNSON-" * 7 + "ALEXANDRA.Q.1234567890"           # 141 + ...: the longest the portal takes is 200
LONG_USER = LONG_USER + "9" * (150 - len(LONG_USER))
BANNER = "W" * 92                                                       # one 92-character word (no hyphen to break at)
HOST_NAME = "SITEC" + "W" * 58 + ".example.mil"                          # a 63-character DNS label, unbroken
# the bars: (name, portal name, all tabs, logo, banner, user, a map toast)
BARS = (("usual", "Site portal", True, False, False, USER, False),
        ("long", "Site automation portal", True, False, False, USER, False),
        ("four", "Site portal", False, False, False, USER, False),
        ("worst", "Enterprise Network Operations Portal", True, True, True, LONG_USER, True))

MEASURE = """<script>
(function () {
  function box(e) { var r = e.getBoundingClientRect(); return {l: r.left, r: r.right, t: r.top, b: r.bottom}; }
  function shown(e) { var r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== "hidden"; }
  function name(e) { return e.id ? "#" + e.id : (e.className ? "." + e.className.split(" ")[0] : e.tagName) + (e.tagName === "A" ? " " + e.textContent : ""); }
  function over(a, b) { return a.l < b.r - 0.5 && b.l < a.r - 0.5 && a.t < b.b - 0.5 && b.t < a.b - 0.5; }
  function inside(a, b) { return a.l >= b.l - 0.5 && a.r <= b.r + 0.5 && a.t >= b.t - 0.5 && a.b <= b.b + 0.5; }
  addEventListener("load", function () {
    // the toast measured where it stays: its slide-in ended at once (frames out of view do not run animations)
    var t = document.querySelector(".nm-toast");
    if (t && t.getAnimations) t.getAnimations().forEach(function (a) { a.finish(); });
    scrollTo(0, 400);                     // past the banner: the bar stays at the top (sticky), the toast under it
    measure();
  });
  function measure() {
    var de = document.documentElement, w = de.clientWidth, bad = [];
    var q = function (s) { return Array.prototype.slice.call(document.querySelectorAll(s)); };
    var who = document.querySelector(".who"), seg = document.getElementById("theme"), toast = document.querySelector(".nm-toast");
    var parts = [document.querySelector(".home"), who, seg].concat(q(".nav a")).filter(function (e) { return e && shown(e); });
    parts.forEach(function (e, i) {
      var a = box(e);
      if (a.l < -0.5 || a.r > w + 0.5) bad.push(name(e) + " is cut by the window: " + Math.round(a.l) + ".." + Math.round(a.r) + " of " + w);
      if (e !== who && e.scrollWidth > e.clientWidth + 1) bad.push(name(e) + " cuts its text: " + e.scrollWidth + " > " + e.clientWidth);
      parts.slice(i + 1).forEach(function (f) { if (over(a, box(f))) bad.push(name(e) + " overlaps " + name(f)); });
      if (toast && over(a, box(toast))) bad.push("the map toast overlaps " + name(e));
    });
    q(".nav a .bdg").forEach(function (b) { if (!shown(b) || !inside(box(b), box(b.parentNode))) bad.push("a count is not in its tab: " + b.parentNode.textContent); });
    if (toast && box(toast).t < box(document.querySelector(".appbar")).b - 0.5) bad.push("the map toast is over the bar");
    if (toast && (box(toast).l < -0.5 || box(toast).r > w + 0.5)) bad.push("the map toast is cut by the window");
    if (de.scrollWidth > w + 1) bad.push("the page scrolls sideways: " + de.scrollWidth + " > " + w);
    var home = document.querySelector(".home"), bar = document.querySelector(".appbar");
    if (Math.abs(box(bar).t) > 0.5) bad.push("the bar left the top of the window: " + box(bar).t);
    var title = Array.prototype.filter.call(home.childNodes, function (n) { return n.nodeType === 3; })[0], rg = document.createRange();
    rg.selectNodeContents(title);
    var tr = rg.getClientRects();
    if (!shown(home) || !tr.length) bad.push("the portal's name is not shown");
    Array.prototype.forEach.call(tr, function (r) { if (!inside({l: r.left, r: r.right, t: r.top, b: r.bottom}, box(home))) bad.push("the portal's name is outside its box"); });
    q(".banner").forEach(function (b) { if (b.scrollWidth > b.clientWidth + 1) bad.push("the banner cuts its text: " + b.scrollWidth + " > " + b.clientWidth); });
    if (toast) {
      var tb = box(toast), hit = document.elementFromPoint((tb.l + tb.r) / 2, (tb.t + tb.b) / 2), msg = toast.querySelector("span");
      if (!hit || !toast.contains(hit)) bad.push("the map toast is covered or clipped (" + (hit ? name(hit) : "nothing") + " at its centre)");
      if (msg.scrollWidth > msg.clientWidth + 1 || !inside(box(msg), tb)) bad.push("the map toast cuts its text: " + msg.scrollWidth + " > " + msg.clientWidth);
    }
    var logo = document.querySelector(".home img.logo");
    parent.postMessage({frame: document.body.getAttribute("data-frame"), width: innerWidth, bad: bad,
                        tabs: q(".nav a").map(function (a) { return {text: a.firstChild.textContent, shown: shown(a), box: box(a)}; }),
                        counts: q(".nav a .bdg").map(function (b) { return b.textContent; }),
                        labels: q("#theme label").filter(shown).map(function (l) { return l.textContent; }),
                        seg: box(seg), bar: box(document.querySelector(".appbar")), who: shown(who), whoTitle: who.title,
                        logo: logo ? (shown(logo) ? logo.naturalWidth : 0) : null, toast: toast ? shown(toast) : null,
                        banner: q(".banner").map(function (b) { return b.textContent; }), title: title.textContent,
                        logoIn: logo ? inside(box(logo), box(home)) && box(logo).l >= -0.5 && box(logo).r <= w + 0.5 : null}, "*");
  }
})();
</script>"""

HOST = """<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0"><pre id="res">waiting</pre>%s<script>
var want = %d, got = [];
addEventListener("message", function (ev) { got.push(ev.data); if (got.length === want) document.getElementById("res").textContent = "BAR" + JSON.stringify(got) + "END"; });
</script></body></html>"""


def browser():
    for b in ("google-chrome", "chromium", "chromium-browser"):
        p = shutil.which(b)
        if p:
            return p
    return None


def png(path, w, h):
    """A plain w x h PNG (the logo): no image library needed."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    raw = b"".join(b"\0" + b"\x30\x60\x90" * w for _ in range(h))
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def page(tmp, look, theme, bar):
    """One portal page with the real bar: the counts on Jobs and Approvals showing (portal.js adds them), a long main."""
    import site_portal as sp
    kind, title, tabs, logo, banner, user, toast = bar
    cfg = types.SimpleNamespace(banner={"on": banner, "text": BANNER}, path="", netmap=tabs, netmap_why="", ad=tabs, logo=logo, logo_v=1,
                                title=title, look=look, motion="auto", netmap_seconds=60, netmap_opts={},
                                report_db=None, season="none", season_style="16bit", season_parts={}, tz=None)   # 1.111.0 history, 1.115.0 season: off
    html = sp.netmap_page(cfg, user, os.path.join(tmp, "no-map"), {})        # the live Network map page itself (no map: its stand-in)
    assert html.count(sp.NETMAP_TOASTS) == 1 and html.index('<div class="appbar">') < html.index(sp.NETMAP_TOASTS) < html.index("<main>")
    if toast:                                                             # a toast as netmap-live.js draws it
        html = html.replace(sp.NETMAP_TOASTS, sp.NETMAP_TOASTS.replace("></div>", '><div class="nm-toast down" role="status"><b aria-hidden="true">'
                            '&#215;</b><span>Link down: %s &#8596; SITEE-RTR01.example.mil</span></div></div>' % HOST_NAME), 1)
    html = html.replace("</main>", "<p>Test data.</p>" * 120 + "</main>", 1)
    html = re.sub(r"<script [^>]*></script>", "", html)                   # no portal.js: no calls; the counts are drawn here
    for href, n in (("jobs", 12), ("approvals", 3)):
        html, done = re.subn(r'(<a href="%s"[^>]*>[^<]*)</a>' % href, r'\1<span class="bdg">%d</span></a>' % n, html, count=1)
        assert done == 1, href
    html = html.replace('<html lang="en"', '<html data-theme="%s" lang="en"' % theme, 1)
    frame = "%s-%s-%s" % (look, theme, kind)
    html = html.replace('<body data-page="netmap">', '<body data-page="netmap" data-frame="%s">' % frame, 1)
    html = html.replace("</body>", MEASURE + "</body>", 1)
    with open(os.path.join(tmp, frame + ".html"), "w") as fh:
        fh.write(html)
    return frame


@unittest.skipUnless(browser() or os.environ.get("CI"), "no google-chrome or chromium")     # in CI (CI=true): never skipped
class Bar(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="portal-bar-")
        st = os.path.join(cls.tmp, "static")
        os.makedirs(st)
        shutil.copy(os.path.join(FILES, "static", "portal.css"), os.path.join(st, "portal.css"))
        shutil.copy(os.path.join(REPO, "roles", "netmap_report", "files", "web", "netmap.css"), os.path.join(st, "netmap.css"))
        with open(os.path.join(st, "banner.css"), "w") as fh:
            fh.write("")
        png(os.path.join(st, "logo"), 160, 40)                              # the widest logo the bar shows (max-width 160px)
        cls.frames_ = [page(cls.tmp, look, theme, bar) for look in LOOKS for theme in THEMES for bar in BARS]
        cls.rows = {}
        for w in WIDTHS:
            host = os.path.join(cls.tmp, "host-%d.html" % w)
            with open(host, "w") as fh:
                fh.write(HOST % ("".join('<iframe src="%s.html" width="%d" height="600" style="display:block;border:0"></iframe>' % (f, w)
                                         for f in cls.frames_), len(cls.frames_)))
            out = subprocess.run([browser(), "--headless=new", "--no-sandbox", "--disable-gpu", "--allow-file-access-from-files",
                                  "--window-size=%d,800" % (w + 40), "--dump-dom", "--virtual-time-budget=20000", "file://" + host],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True, timeout=180).stdout
            m = re.search(r"BAR(\[.*\])END", out, re.S)
            cls.rows[w] = json.loads(m.group(1).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')) if m else out[-400:]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def frames(self, w):
        rows = self.rows[w]
        self.assertIsInstance(rows, list, "the frames at %d px did not report: %s" % (w, rows))
        self.assertEqual(sorted(r["frame"] for r in rows), sorted(self.frames_), w)          # each frame once, none missing
        for r in rows:
            self.assertEqual(r["width"], w, r["frame"])
        return rows

    @staticmethod
    def rows_of_tabs(r):
        return len(set(round(t["box"]["t"]) for t in r["tabs"]))

    def test_nothing_in_the_bar_covers_a_tab(self):
        for w in WIDTHS:
            for r in self.frames(w):
                f = "%s at %d px" % (r["frame"], w)
                self.assertEqual(r["bad"], [], f)
                self.assertEqual([t["text"] for t in r["tabs"]], TABS if not r["frame"].endswith("-four") else TABS[:4], f)
                self.assertTrue(all(t["shown"] for t in r["tabs"]), f)
                self.assertEqual(r["counts"], ["12", "3"], f)
                self.assertEqual(r["labels"], ["Auto", "Light", "Dark"], f)
                self.assertEqual(r["who"], w > 1000, f)                               # the user name: hidden at 1000 px and narrower
                worst = r["frame"].endswith("-worst")
                self.assertEqual(r["whoTitle"], LONG_USER if worst else USER, f)       # a cut name is whole in its title
                self.assertEqual((r["logo"], r["logoIn"], r["toast"], r["banner"]), (160, True, True, [BANNER, BANNER]) if worst else (None, None, None, []), f)
                self.assertEqual(r["title"], dict((b[0], b[1]) for b in BARS)[r["frame"].rsplit("-", 1)[1]], f)

    def test_the_last_tab_and_the_switch_at_800(self):
        # the case seen: Network map and Active Directory on, at 800 px - "Active Directory" was under Auto/Light/Dark
        for r in self.frames(800):
            if not r["frame"].endswith("-four"):
                a, b = r["tabs"][-1]["box"], r["seg"]
                self.assertEqual(r["tabs"][-1]["text"], "Active Directory")
                apart = a["r"] <= b["l"] + 0.5 or b["r"] <= a["l"] + 0.5 or a["b"] <= b["t"] + 0.5 or b["b"] <= a["t"] + 0.5
                self.assertTrue(apart, "%s: Active Directory %s, the switch %s" % (r["frame"], a, b))

    def test_two_rows_of_tabs_at_800_not_more(self):
        for r in self.frames(800):
            self.assertLessEqual(self.rows_of_tabs(r), 2 if r["frame"].endswith(("-usual", "-long", "-four")) else 3, r["frame"])

    def test_one_row_at_1280(self):
        # the usual portal name, every tab, both counts, the long user name: one row as before. A longer portal name
        # (portal_title) may take the tabs to a second row - never under the switch
        for r in self.frames(1280):
            if r["frame"].endswith(("-usual", "-four")):
                self.assertEqual(self.rows_of_tabs(r), 1, r["frame"])
                self.assertLess(r["bar"]["b"] - r["bar"]["t"], 70, r["frame"])     # one row (60 px and its edge)


if __name__ == "__main__":
    unittest.main(verbosity=1)
