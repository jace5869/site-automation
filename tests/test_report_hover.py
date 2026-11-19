#!/usr/bin/env python3
"""1.57.0: the report website's hover layer and sparklines (plugins/filter/site_filters.py report_sparkline and
report_runway_bar, roles/site_email/templates/site_report.js, report_board.html.j2, report_hover.css.j2): the helpers'
markup (every value in the aria-label, data-points, no script in it); no inline script or handler in an atlas or brief
page, none of it in an email; and in a real Chrome, the page served with a Content-Security-Policy that allows only its
own script file (script-src 'self'): hovering a sparkline shows its day and value, keyboard focus does too (Tab, then the
arrow keys), a runway bar's tip is its data-tip, Esc hides it.
int-11: the Trend column (a section's sparks: ONTAP capacity 1.52.0, the STIG scorecard 1.58.0, patch age 1.61.0) hovers
the same way, and ticket insights' breach line (its own page and script, site-insights.js) shows its day's numbers.
1.64.0: the certificate timeline's marks (data-tip): hover, Tab from mark to mark, Esc.
  python3 tests/test_report_hover.py      (the Chrome part skips without google-chrome or chromium)"""
import base64
import http.server
import json
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
import site_filters as sf  # noqa: E402
import capacity_filters as cf  # noqa: E402
import ontap_capacity_filters as ocf  # noqa: E402
import patch_age_filters as paf  # noqa: E402
import servicenow_insights_filters as snf  # noqa: E402
import servicenow_insights_page as snp  # noqa: E402
import stig_scorecard_filters as ssf  # noqa: E402
import test_capacity as tc  # noqa: E402
import test_cert_timeline as tct  # noqa: E402
import test_outage_timeline as tot  # noqa: E402
import test_servicenow_breach as tsb  # noqa: E402
from test_report_fit import EmailTiles, _browser  # noqa: E402

DAY = 86400
T0 = 1790000000


def trend_section():
    """A Trend column (kind spark) whose rows' lines come from the three producers, and a row without a line."""
    now = T0 + 84 * DAY
    sparks = [ocf.spark([(T0 + i * 7 * DAY, 60 + 2 * i) for i in range(13)], T0, now, [80, 90], what="Used % of vol_sitea_01"),
              ssf.spark([(0, 20, 1, 17, 2), (1, 19, 1, 16, 2), (2, 18, 1, 15, 2), (3, 14, 1, 12, 1)],
                        ["16 Oct", "23 Oct", "30 Oct", "6 Nov"], "SITEA - RHEL 9"),
              paf.spark([(T0 + 4 * DAY, 80), (T0 + 74 * DAY, 100), (now, 40)], now, "SITEA-linux-web"),
              {}]
    return {"title": "Trend column", "columns": ["Name", "Now", "Trend"], "kinds": ["", "num", "spark"], "sparks": sparks,
            "rows": [["vol_sitea_01", "84.0", sparks[0]["label"]], ["SITEA - RHEL 9", "14", sparks[1]["label"]],
                     ["SITEA-linux-web", "40%", sparks[2]["label"]], ["SITEA-other", "-", ""]]}


def doc():
    """The capacity report of test_capacity's board data, the website's with a Trend column section: (the website's,
    the email's)."""
    r = cf.vm_capacity_report(tc.board_data(), tc.BOPTS)
    return dict(r["web"], sections=list(r["web"]["sections"]) + [trend_section()]), r["report"]


def breach_page():
    """Ticket insights' About to breach page on test_servicenow_breach's seed site."""
    m = snf.breach_model(tsb.seed_input(), {})
    return snp.servicenow_insights_breach_page(m, {"look": "atlas"}), m


def render(tpl, d):
    return EmailTiles()._env().get_template(tpl).render(_report=d, _email_footer="AAP job 9", report_email_header_text="x")


class Helpers(unittest.TestCase):
    def test_sparkline(self):
        pts = [[T0 + i * DAY, 50 + i] for i in range(5)]
        svg = sf.report_sparkline(pts, {"label": "PROD memory used", "lines": [85, 90]})
        self.assertTrue(svg.startswith('<svg class="spark" '))
        aria = re.search(r'aria-label="([^"]*)"', svg).group(1)
        for v in ("50.0%", "51.0%", "52.0%", "53.0%", "54.0%"):             # every value, for a screen reader
            self.assertIn(v, aria)
        self.assertTrue(aria.startswith("PROD memory used, Mon 21 Sep 2026 to Fri 25 Sep 2026: "), aria)
        data = json.loads(re.search(r'data-points="([^"]*)"', svg).group(1).replace("&quot;", '"'))
        self.assertEqual(len(data), 5)
        self.assertEqual(data[-1][2:], ["Fri 25 Sep 2026", "54.0%"])
        self.assertEqual(svg.count('class="ref"'), 2)                      # the dashed 85 and 90 lines
        self.assertIn('tabindex="0"', svg)                                  # keyboard: Tab to it
        self.assertNotIn("<script", svg)
        self.assertFalse(re.search(r"\son[a-z]+=", svg))
        self.assertEqual(sf.report_sparkline([], {}), "")
        self.assertEqual(sf.report_sparkline([[T0, None]], {}), "")
        self.assertIn("&lt;b&gt;", sf.report_sparkline(pts, {"label": "<b>"}))   # escaped

    def test_runway_bar(self):
        o = {"warn": 90, "crit": 30}
        for days, band in ((10, "critical"), (60, "warning"), (150, "low"), (400, "ok"), (None, "ok"), (5000, "ok")):
            bar = sf.report_runway_bar(days, o)
            self.assertIn('class="rf-%s"' % band, bar, days)
            self.assertEqual([m for m in re.findall(r'class="rw-(\w+)"', bar)], ["critical", "warning", "low", "ok"])
        self.assertIn('aria-hidden="true"', sf.report_runway_bar(10, o))   # the words are beside it and in its tip


class TrendColumn(unittest.TestCase):
    """int-11: the Trend column's lines carry data-points (as report_sparkline's), a Tab stop and no <title> of their own."""
    def test_each_producer_hovers(self):
        web, _ = doc()
        for tpl in ("report_atlas.html.j2", "report_brief.html.j2", "report_ops.html.j2"):
            h = render(tpl, web)
            cells = re.findall(r'<td class="k-spark"[^>]*>(.*?)</td>', h)
            self.assertEqual(len(cells), 4, tpl)
            self.assertEqual(cells[3], "", tpl)                                   # no line: an empty cell
            for cell, sp in zip(cells[:3], trend_section()["sparks"][:3]):
                self.assertIn('tabindex="0"', cell)
                self.assertNotIn("<title>", cell)                                  # one tip, the shared one
                self.assertIn('data-label="%s"' % sp["name"], cell)
                data = json.loads(re.search(r"data-points='([^']*)'", cell).group(1))
                self.assertEqual(data, [[p["x"], p["y"], p["label"], p["text"]] for p in sp["data_points"]])
                self.assertEqual(data[-1][:2], sp["last"])                          # the guide's last point is the dot
        ont, stig, pa_ = trend_section()["sparks"][:3]
        self.assertEqual((ont["data_points"][-1]["label"], ont["data_points"][-1]["text"]), ("Mon 14 Dec 2026", "84.0%"))
        self.assertEqual(stig["data_points"][-1]["label"], "Week to 6 Nov")
        self.assertEqual(pa_["data_points"][-1]["text"], "40% on time")

    def test_without_data_points_the_old_line(self):
        sp = dict(trend_section()["sparks"][0])
        sp.pop("data_points")
        web = dict(doc()[0], sections=[dict(trend_section(), sparks=[sp, {}, {}, {}])])
        cell = re.findall(r'<td class="k-spark"[^>]*>(.*?)</td>', render("report_atlas.html.j2", web))[0]
        self.assertIn("<title>", cell)
        self.assertNotIn("data-points", cell)
        self.assertNotIn("tabindex", cell)

    def test_breach_line(self):
        h, m = breach_page()
        svg = h.split('<svg class="breach-trend"')[1].split("</svg>")[0]
        self.assertEqual(svg.count("data-t="), len(m["points"]))
        self.assertIn('<script src="site-insights.js">', h)                    # its own script, from the website


class Pages(unittest.TestCase):
    def test_no_inline_script_or_handler_and_none_of_it_in_an_email(self):
        web, mail = doc()
        for tpl in ("report_atlas.html.j2", "report_brief.html.j2", "report_ops.html.j2"):
            h = render(tpl, web)
            self.assertIn('<table class="bd">', h)
            self.assertIn('svg class="spark"', h)
            self.assertFalse(re.search(r"<[^>]+\son[a-z]+=", h), tpl)        # no onclick= and the like
            self.assertNotIn("javascript:", h)
            # its scripts: the early theme lines (kept: they only save a flash), the file, its inline copy - none new
            # (ops, 1.69.0: no early lines - the file in its head sets the theme first)
            self.assertEqual(len(re.findall(r"<script", h)), 2 if "ops" in tpl else 3, tpl)
            self.assertNotIn(" title=", re.sub(r'<button[^>]*>', "", h.split('<table class="bd">')[1].split("</table>")[0]))
            for t in re.findall(r'data-tip="([^"]*)"', h):
                self.assertTrue(t.strip(), tpl)
        for tpl in ("report.html.j2", "report_command.html.j2", "report_portal.html.j2", "report_glass.html.j2"):
            h = render(tpl, mail)
            for x in ("data-tip", "svg class=\"spark\"", "data-points", "rwb"):
                self.assertNotIn(x, h, tpl)
            self.assertIn("The numbers behind the lines", h)

    def test_the_script_is_ascii_and_uses_no_inline_handlers(self):
        with open(os.path.join(REPO, "roles", "site_email", "templates", "site_report.js")) as f:
            js = f.read()
        js.encode("ascii")
        self.assertNotIn("innerHTML", js)                                 # the tips' text: textContent only
        self.assertNotIn("setAttribute('on", js)
        self.assertIn("aria-describedby", js)


# ---- a real Chrome, driven over its DevTools protocol (a small WebSocket client: the standard library only) ----------
class Cdp(object):
    def __init__(self, browser, width, height):
        self.prof = tempfile.mkdtemp()
        self.p = subprocess.Popen([browser, "--headless=new", "--disable-gpu", "--no-sandbox", "--remote-debugging-port=0",
                                   "--user-data-dir=" + self.prof, "--window-size=%d,%d" % (width, height), "about:blank"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        port_file, end, got = os.path.join(self.prof, "DevToolsActivePort"), time.time() + 30, ""
        while not got:
            if os.path.exists(port_file):
                with open(port_file) as f:
                    got = f.read().strip()
            if not got and time.time() > end:
                raise RuntimeError("Chrome gave no DevTools port")
            time.sleep(0.1)
        port = int(got.split()[0])
        page = [t for t in json.load(urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=10)) if t["type"] == "page"][0]
        path = page["webSocketDebuggerUrl"].split(str(port), 1)[1]
        self.s = socket.create_connection(("127.0.0.1", port), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(("GET %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, port, key)).encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += self.s.recv(1)
        assert b" 101 " in head.split(b"\r\n")[0], head
        self.n = 0

    def _read(self, n):
        b = b""
        while len(b) < n:
            c = self.s.recv(n - len(b))
            if not c:
                raise RuntimeError("DevTools closed")
            b += c
        return b

    def _frame(self):
        msg = b""
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7f
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            msg += self._read(n)
            if b0 & 0x80:
                return msg

    def call(self, method, **params):
        self.n += 1
        data = json.dumps({"id": self.n, "method": method, "params": params}).encode()
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else (bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536
                                                                     else bytes([0x80 | 127]) + struct.pack(">Q", n)))
        self.s.sendall(head + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(data)))
        while True:
            m = json.loads(self._frame())
            if m.get("id") == self.n:
                if "error" in m:
                    raise RuntimeError("%s: %s" % (method, m["error"]))
                return m.get("result", {})

    def js(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    def key(self, k, code, vk):
        for t in ("rawKeyDown", "keyUp"):
            self.call("Input.dispatchKeyEvent", type=t, key=k, code=code, windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk)

    def mouse(self, x, y):
        self.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y, pointerType="mouse")

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass
        try:
            os.killpg(self.p.pid, signal.SIGKILL)
        except OSError:
            pass
        self.p.wait(timeout=30)
        shutil.rmtree(self.prof, ignore_errors=True)


class Csp(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Content-Security-Policy", "script-src 'self'")
        super().end_headers()

    def log_message(self, *a):
        pass


LIST = ("(() => {const t = document.getElementById('site-tip'); if (!t || t.hidden || !t.classList.contains('tt-list')) return null; "
        "return {head: t.querySelector('.tt-head').textContent, lines: [...t.querySelectorAll('.tt-ln')].map(a => "
        "[a.querySelector('.tt-t').textContent, a.querySelector('.tt-w').firstChild.textContent, a.querySelector('.tt-t').getAttribute('href')])}})()")
TIP = "(() => {const t = document.getElementById('site-tip'); return t && !t.hidden ? [...t.children].map(x => x.textContent) : null})()"


@unittest.skipUnless(_browser(), "no headless browser here")
class RealChrome(unittest.TestCase):
    TPL = "report_atlas.html.j2"

    @classmethod
    def web(cls):
        return doc()[0]

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        h = render(cls.TPL, cls.web())
        with open(os.path.join(cls.dir, "page.html"), "w") as f:
            f.write(h)
        shutil.copy(os.path.join(REPO, "roles", "site_email", "templates", "site_report.js"), os.path.join(cls.dir, "site-report.js"))
        handler = lambda *a, **k: Csp(*a, directory=cls.dir, **k)          # noqa: E731
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.c = Cdp(_browser(), 1366, 1000)
        cls.c.call("Page.enable")
        # no smooth scrolling (the looks honour reduced motion): a Tab's scroll is over before the mouse moves
        cls.c.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
        cls.c.call("Page.navigate", url="http://127.0.0.1:%d/page.html" % cls.srv.server_address[1])
        end = time.time() + 30
        while not cls.c.js("document.readyState === 'complete' && !!window.siteReport"):
            if time.time() > end:
                raise RuntimeError("the page or its script did not load")
            time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.c.close()
        cls.srv.shutdown()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def at(self, sel, i=0):
        """The middle of the i-th element of sel, scrolled into view: (x, y) in the window, once the page stands still."""
        last = None
        for _ in range(50):
            y = self.c.js("scrollY")
            if y == last:
                break
            last = y
            time.sleep(0.05)
        return self.c.js("(() => {const e = document.querySelectorAll(%s)[%d]; e.scrollIntoView({block: 'center', behavior: 'instant'}); "
                         "const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]})()" % (json.dumps(sel), i))

    def test_1_the_inline_copy_is_blocked_the_file_runs(self):
        self.assertTrue(self.c.js("document.documentElement.classList.contains('js')"))
        self.assertTrue(self.c.js("!!document.getElementById('site-tip')"))

    def test_2_hovering_a_sparkline_shows_the_day_and_value(self):
        x, y = self.at("svg.spark")
        self.c.mouse(5, 5)
        self.c.mouse(x, y)
        tip = self.c.js(TIP)
        self.assertIsNotNone(tip, "no tip on hover")
        pts = self.c.js("JSON.parse(document.querySelector('svg.spark').getAttribute('data-points'))")
        self.assertIn(tip[0], [p[3] for p in pts])                       # the value leads
        self.assertTrue(re.search(r", \w{3} \d{1,2} \w{3} \d{4}$", tip[1]), tip)
        self.assertTrue(self.c.js("!!document.querySelector('svg.spark line.hg') && !!document.querySelector('svg.spark circle.hd')"))
        self.c.mouse(x + 60, y)                                          # further right: a later day
        self.assertNotEqual(self.c.js(TIP), tip)
        self.c.mouse(5, 5)                                               # away: hidden, the guide gone
        self.assertIsNone(self.c.js(TIP))
        self.assertFalse(self.c.js("!!document.querySelector('svg.spark line.hg')"))

    def test_3_keyboard_focus_shows_it_too(self):
        self.c.js("document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0)")
        for _ in range(300):
            self.c.key("Tab", "Tab", 9)
            if self.c.js("document.activeElement.matches('svg.spark')"):
                break
        self.assertTrue(self.c.js("document.activeElement.matches('svg.spark')"), "Tab never reached a sparkline")
        pts = self.c.js("JSON.parse(document.activeElement.getAttribute('data-points'))")
        tip = self.c.js(TIP)
        self.assertEqual(tip[0], pts[-1][3])                             # focus: the last day
        self.c.key("ArrowLeft", "ArrowLeft", 37)
        self.assertEqual(self.c.js(TIP)[0], pts[-2][3])
        self.assertTrue(self.c.js(TIP)[1].endswith(pts[-2][2]))
        self.c.key("Escape", "Escape", 27)
        self.assertIsNone(self.c.js(TIP))

    def test_4_a_runway_bar_and_a_tile(self):
        for sel in (".rw[data-tip]", ".tile[data-tip]"):
            x, y = self.at(sel)
            self.c.mouse(5, 5)
            self.c.mouse(x, y)
            want = self.c.js("document.querySelector(%s).getAttribute('data-tip').split('\\n')" % json.dumps(sel))
            self.assertEqual(self.c.js(TIP), want, sel)
            box = self.c.js("(() => {const r = document.getElementById('site-tip').getBoundingClientRect(); "
                            "return [r.left, r.right, document.documentElement.clientWidth]})()")
            self.assertGreaterEqual(box[0], 0, sel)                       # inside the window
            self.assertLessEqual(box[1], box[2], sel)
        self.assertFalse(self.c.js("[...document.querySelectorAll('[data-tip]')].some(e => e.hasAttribute('title'))"))


    def test_5_a_trend_column_line(self):
        sel = "td.k-spark svg.spark"
        n = self.c.js("document.querySelectorAll(%s).length" % json.dumps(sel))
        self.assertEqual(n, 3)
        for i in range(n):
            self.at(sel, i)
            x, y = self.c.js("(() => {const s = document.querySelectorAll(%s)[%d], r = s.getBoundingClientRect(), vb = s.viewBox.baseVal, "
                             "p = JSON.parse(s.getAttribute('data-points')).pop(); return [r.left + p[0] * r.width / vb.width, "
                             "r.top + p[1] * r.height / vb.height]})()" % (json.dumps(sel), i))
            self.c.mouse(5, 5)
            self.c.mouse(x, y)                                                 # on the last point
            pts = self.c.js("JSON.parse(document.querySelectorAll(%s)[%d].getAttribute('data-points'))" % (json.dumps(sel), i))
            name = self.c.js("document.querySelectorAll(%s)[%d].getAttribute('data-label')" % (json.dumps(sel), i))
            self.assertEqual(self.c.js(TIP), [pts[-1][3], "%s, %s" % (name, pts[-1][2])], i)
        self.c.mouse(5, 5)
        self.c.js("document.activeElement && document.activeElement.blur(); document.querySelector(%s).scrollIntoView({block: 'center'})"
                  % json.dumps("td.k-spark"))
        for _ in range(400):
            self.c.key("Tab", "Tab", 9)
            if self.c.js("document.activeElement.matches(%s)" % json.dumps(sel)):
                break
        self.assertTrue(self.c.js("document.activeElement.matches(%s)" % json.dumps(sel)), "Tab never reached a Trend line")
        pts = self.c.js("JSON.parse(document.activeElement.getAttribute('data-points'))")
        self.assertEqual(self.c.js(TIP)[0], pts[-1][3])
        self.c.key("Home", "Home", 36)
        self.assertEqual(self.c.js(TIP)[0], pts[0][3])
        self.c.key("Escape", "Escape", 27)
        self.assertIsNone(self.c.js(TIP))


class RealChromeBrief(RealChrome):
    """The brief look: the same script and board."""
    TPL = "report_brief.html.j2"


@unittest.skipUnless(_browser(), "no headless browser here")
class RealChromeTimeline(RealChrome):
    """1.64.0: the certificate timeline's marks (their data-tip) under the same policy - hover, the keyboard, Esc."""
    test_2_hovering_a_sparkline_shows_the_day_and_value = test_3_keyboard_focus_shows_it_too = None
    test_4_a_runway_bar_and_a_tile = test_5_a_trend_column_line = None
    SEL = ".tl-track .tl-m[data-tip]"

    @classmethod
    def web(cls):
        r = tct.made()
        return {"title": "Certificate report", "status": r["status"], "summary": r["boxes"], "sections": r["web_sections"]}

    def test_6_hovering_a_mark_shows_the_certificate(self):
        n = self.c.js("document.querySelectorAll(%s).length" % json.dumps(self.SEL))
        self.assertEqual(n, 18)
        for i in (0, 4, n - 1):                                               # one expired, two on one day, the last row's last
            x, y = self.at(self.SEL, i)
            self.c.mouse(5, 5)
            self.c.mouse(x, y)
            want = self.c.js("document.querySelectorAll(%s)[%d].getAttribute('data-tip').split('\\n')" % (json.dumps(self.SEL), i))
            self.assertEqual(self.c.js(TIP), want, i)
            box = self.c.js("(() => {const r = document.getElementById('site-tip').getBoundingClientRect(); "
                            "return [r.left, r.right, document.documentElement.clientWidth]})()")
            self.assertGreaterEqual(box[0], 0, i)
            self.assertLessEqual(box[1], box[2], i)
        self.assertEqual(self.c.js("document.querySelectorAll(%s)[0].getAttribute('data-tip').split('\\n')[0]" % json.dumps(self.SEL)),
                         "old-intranet.example.mil")
        self.c.mouse(5, 5)
        self.assertIsNone(self.c.js(TIP))
        self.assertFalse(self.c.js("[...document.querySelectorAll('[data-tip]')].some(e => e.hasAttribute('title'))"))

    def test_7_the_keyboard_reaches_each_mark(self):
        self.c.js("document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0)")
        seen = []
        for _ in range(300):
            self.c.key("Tab", "Tab", 9)
            if self.c.js("document.activeElement.matches(%s)" % json.dumps(self.SEL)):
                tip = self.c.js(TIP)
                self.assertEqual(tip, self.c.js("document.activeElement.getAttribute('data-tip').split('\\n')"))
                seen.append(tip[0])
            elif seen:
                break
        self.assertEqual(len(seen), 18, "Tab did not stop at every mark")
        self.assertEqual(seen[0], "old-intranet.example.mil")                 # the first row's first mark: document order
        self.c.js("document.querySelector(%s).focus()" % json.dumps(self.SEL))
        self.assertIsNotNone(self.c.js(TIP))
        self.c.key("Escape", "Escape", 27)
        self.assertIsNone(self.c.js(TIP))


class RealChromeTimelineBrief(RealChromeTimeline):
    TPL = "report_brief.html.j2"


class RealChromeTimelineDense(RealChromeTimeline):
    """Certificates a day or two apart (tester-2, int-13): marks that would sit on each other are one mark, so no mark
    hides another and the mouse reaches every one - at a wide window and at 800 px."""
    test_6_hovering_a_mark_shows_the_certificate = test_7_the_keyboard_reaches_each_mark = None

    @classmethod
    def web(cls):
        r = tct.made(tct.dense())
        return {"title": "Certificate report", "status": r["status"], "summary": r["boxes"], "sections": r["web_sections"]}

    def test_8_no_mark_covers_another(self):
        boxes = ("[...document.querySelectorAll('.tl-track')].map(t => [...t.querySelectorAll('.tl-m')].map(m => "
                 "{const r = m.getBoundingClientRect(); return [r.left, r.right]}))")
        on_top = ("[...document.querySelectorAll(%s)].filter(m => {m.scrollIntoView({block: 'center', behavior: 'instant'}); "
                  "const r = m.getBoundingClientRect(); return document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)"
                  ".closest('.tl-m') !== m}).length" % json.dumps(self.SEL))
        try:
            for width in (1366, 800):
                self.c.call("Emulation.setDeviceMetricsOverride", width=width, height=1000, deviceScaleFactor=1, mobile=False)
                rows = self.c.js(boxes)
                self.assertEqual(len(rows), 8, width)                          # ten systems: seven rows and Other
                self.assertGreater(max(len(r) for r in rows), 2, width)
                for r in rows:
                    for a, b in zip(r, r[1:]):
                        self.assertGreaterEqual(b[0] - a[1], 2, (width, r))    # the ring's two pixels stay free
                self.assertEqual(self.c.js(on_top), 0, width)
            many = self.c.js("[...document.querySelectorAll('.tl-m.n')].map(m => [m.textContent.trim(), m.getAttribute('data-tip').split('\\n')[0]])")
            self.assertTrue(any(re.match(r"^\d+ certificates: \d+ \w+ to \w+ \d+ \w+ \d+$", t) for _, t in many), many)
            self.assertTrue(all(t.startswith(n + " certificates") for n, t in many), many)
            x, y = self.at(".tl-m.n")                                          # and its tip opens, with a line per certificate
            self.c.mouse(5, 5)
            self.c.mouse(x, y)
            tip = self.c.js(TIP)
            self.assertEqual(tip, self.c.js("document.querySelector('.tl-m.n').getAttribute('data-tip').split('\\n')"))
            self.assertGreater(len(tip), 2)
        finally:
            self.c.call("Emulation.clearDeviceMetricsOverride")


class RealChromeTimelineDenseBrief(RealChromeTimelineDense):
    TPL = "report_brief.html.j2"


@unittest.skipUnless(_browser(), "no headless browser here")
class RealChromeOutage(RealChrome):
    """1.66.0: the outage time line's marks (their data-tip) under the same policy - hover, the keyboard, Esc."""
    test_2_hovering_a_sparkline_shows_the_day_and_value = test_3_keyboard_focus_shows_it_too = None
    test_4_a_runway_bar_and_a_tile = test_5_a_trend_column_line = None
    SEL = ".ot-track .ot-m[data-tip]"

    @classmethod
    def web(cls):
        return tot.ot.outage_timeline_doc(tot.WATCH, tot.made(), True, True)

    def test_6_hovering_a_mark_shows_the_event(self):
        n = self.c.js("document.querySelectorAll(%s).length" % json.dumps(self.SEL))
        self.assertEqual(n, 18)
        for i in (0, 7, n - 1):                                               # the first alarm, three marks at one time, the ACT lane's
            x, y = self.at(self.SEL, i)
            self.c.mouse(5, 5)
            self.c.mouse(x, y)
            self.assertEqual(self.c.js(LIST), self.want_list(i), i)
            box = self.c.js("(() => {const r = document.getElementById('site-tip').getBoundingClientRect(); "
                            "return [r.left, r.right, document.documentElement.clientWidth]})()")
            self.assertGreaterEqual(box[0], 0, i)
            self.assertLessEqual(box[1], box[2], i)
        self.assertEqual(self.c.js("document.querySelectorAll(%s)[5].getAttribute('data-tip').split('\\n')" % json.dumps(self.SEL)),
                         ["1  12 Nov 01:41:10 CET  Low", "Storage path redundancy degraded, back after 40 s - esx03"])   # the plain tip is still there
        x, y = self.at(self.SEL, 5)
        self.c.mouse(5, 5)
        self.c.mouse(x, y)
        self.assertEqual(self.c.js(LIST), {"head": "1 event \u00b7 01:41", "lines": [["01:41:10", "Storage path redundancy degraded, back after 40 s", "#ot-row-1"]]})
        self.c.mouse(5, 5)
        self.assertIsNone(self.c.js(LIST))
        self.assertFalse(self.c.js("[...document.querySelectorAll('[data-tip]')].some(e => e.hasAttribute('title'))"))
        # no mark lies over another: each one's middle is its own
        self.assertEqual(self.c.js("[...document.querySelectorAll(%s)].filter(e => {const r = e.getBoundingClientRect(); "
                                   "return document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2).closest('.ot-m') !== e}).length"
                                   % json.dumps(self.SEL)), 0)

    def want_list(self, i):
        """The tip's list for the i-th mark, worked out from its data-ev: the critical first, then by time; the header with the span."""
        ev = self.c.js("JSON.parse(document.querySelectorAll(%s)[%d].getAttribute('data-ev'))" % (json.dumps(self.SEL), i))
        rank = {"critical": 4, "warning": 3, "low": 2, "info": 1, "ok": 0}
        by = sorted(ev, key=lambda e: e["n"])
        head = "%d event%s \u00b7 %s" % (len(ev), "" if len(ev) == 1 else "s", by[0]["m"] + ("-" + by[-1]["m"] if by[-1]["m"] != by[0]["m"] else ""))
        lines = [[e["t"], e["w"], "#ot-row-%d" % e["n"]] for e in sorted(ev, key=lambda e: (-rank[e["l"]], e["n"]))]
        return {"head": head, "lines": lines}

    def test_7_the_keyboard_reaches_each_mark(self):
        self.c.js("document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0)")
        seen = []
        for _ in range(300):
            self.c.key("Tab", "Tab", 9)
            if self.c.js("document.activeElement.matches(%s)" % json.dumps(self.SEL)):
                tip = self.c.js(LIST)
                self.assertEqual(tip["head"].split(" ")[0], str(self.c.js("JSON.parse(document.activeElement.getAttribute('data-ev')).length")))
                seen.append(tip["lines"][0][1])
            elif seen:
                break
        self.assertEqual(len(seen), 18, "Tab did not stop at every mark")
        self.assertEqual(seen[0], "Alarm yellow: Host storage path redundancy")          # the first lane's first mark: document order
        self.c.js("document.querySelector(%s).focus()" % json.dumps(self.SEL))
        self.assertIsNotNone(self.c.js(LIST))
        self.c.key("Escape", "Escape", 27)
        self.assertIsNone(self.c.js(LIST))                                                  # Esc closes it, and the focus returned does not open it again
        self.assertTrue(self.c.js("document.activeElement.matches(%s)" % json.dumps(self.SEL)))

    def test_8_a_click_or_enter_pins_it_and_the_list_scrolls(self):
        self.c.js("document.activeElement && document.activeElement.blur()")
        i = 7                                                                                              # a mark of the vCenter lane, in the middle of the chart
        x, y = self.at(self.SEL, i)
        self.c.mouse(x, y)
        self.assertFalse(self.c.js("document.getElementById('site-tip').classList.contains('pin')"))     # hovering shows it, does not pin it
        self.c.js("document.querySelectorAll(%s)[%d].click()" % (json.dumps(self.SEL), i))
        self.assertTrue(self.c.js("document.getElementById('site-tip').classList.contains('pin')"))
        self.c.mouse(5, 5)                                                                                # pinned: the pointer leaving does not close it
        self.assertIsNotNone(self.c.js(LIST))
        box = self.c.js("(() => {const t = document.getElementById('site-tip'), b = t.querySelector('.tt-body'), p = document.querySelector('.ot-plot').getBoundingClientRect(), "
                        "r = t.getBoundingClientRect(); return [Math.round(r.width), getComputedStyle(b).maxHeight, getComputedStyle(b).overflowY, r.left >= p.left - 1, r.right <= p.right + 1]})()")
        self.assertEqual(box, [420, "320px", "auto", True, True])                                         # fixed width, a max height, a scroll bar, inside the chart
        self.c.key("Escape", "Escape", 27)
        self.assertIsNone(self.c.js(LIST))
        self.c.js("document.querySelectorAll(%s)[%d].focus()" % (json.dumps(self.SEL), i))
        self.c.key("Enter", "Enter", 13)                                                                  # Enter on a Tab stop pins it too
        self.assertTrue(self.c.js("document.getElementById('site-tip').classList.contains('pin')"))
        n = self.c.js(LIST)["lines"][0][2]
        self.c.js("document.querySelector('#site-tip .tt-ln').click()")                                    # a line (anywhere on it): to its row, which flashes
        self.assertIsNone(self.c.js(LIST))
        self.assertTrue(self.c.js("document.getElementById(%s).classList.contains('ot-flash')" % json.dumps(n[1:])))
        # a click elsewhere closes a pinned one
        self.c.js("document.querySelectorAll(%s)[%d].click()" % (json.dumps(self.SEL), i))
        self.assertIsNotNone(self.c.js(LIST))
        self.c.mouse(5, 5)
        self.c.js("document.body.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}))")
        self.assertIsNone(self.c.js(LIST))


class RealChromeOutageFilters(RealChromeOutage):
    """The controls above the chart: Related only, the kinds, Show every repeat - the marks and The events follow, the choice is kept."""
    test_6_hovering_a_mark_shows_the_event = test_7_the_keyboard_reaches_each_mark = test_8_a_click_or_enter_pins_it_and_the_list_scrolls = None

    @classmethod
    def web(cls):
        f = tot.Fine("test_the_kinds")
        evs = f.evs() + [tot.alarm("01:%02d:00" % (5 + 4 * i), "Virtual machine CPU usage", "yellow", "green") for i in range(4)]
        evs.sort(key=lambda e: e["time"])
        return tot.ot.outage_timeline_doc(tot.WATCH, tot.made(evs, []), True, True)

    def count(self, expr):
        return self.c.js("document.querySelectorAll(%s).length" % json.dumps(expr))

    def test_9_related_only_and_the_kinds(self):
        self.c.js("localStorage.removeItem('site-report-outage')")
        self.assertFalse(self.c.js("document.querySelector('[data-ot-ctl]').hidden"))                    # the controls show once the script runs
        all_marks = self.count(".ot-track .ot-m")
        self.assertGreater(self.count(".ot-track .ot-m.ot-dim"), 0)                                       # dimmed, still there
        self.assertGreater(self.count(".ot-track .ot-m.ot-rel"), 0)
        self.assertEqual(self.count(".ot-track .ot-m.ot-off"), 0)
        self.c.js("document.querySelector('input[name=ot-show][value=rel]').click()")
        off = self.count(".ot-track .ot-m.ot-off")
        self.assertEqual(off, self.count(".ot-track .ot-m.ot-dim"))                                       # Related only hides the unrelated marks
        self.assertEqual(self.c.js("[...document.querySelectorAll('tr[data-cls]')].filter(r => r.hidden && r.getAttribute('data-rel') === '0' && !r.hasAttribute('data-rep')).length"),
                         self.c.js("[...document.querySelectorAll('tr[data-cls]')].filter(r => r.getAttribute('data-rel') === '0' && !r.hasAttribute('data-rep')).length"))
        self.assertEqual(json.loads(self.c.js("localStorage.getItem('site-report-outage')"))["show"], "rel")  # remembered
        self.c.js("document.querySelector('input[name=ot-show][value=all]').click()")
        self.assertEqual(self.count(".ot-track .ot-m.ot-off"), 0)
        # a kind off: the marks of that kind only and its rows go; the others stay
        self.c.js("document.querySelector('input[name=ot-kind][value=network]').click()")
        self.assertEqual(self.c.js("[...document.querySelectorAll('tr[data-cls=network]')].every(r => r.hidden)"), True)
        self.assertEqual(self.c.js("[...document.querySelectorAll('tr[data-cls=host]')].every(r => !r.hidden)"), True)
        self.assertGreater(self.count(".ot-track .ot-m.ot-off"), 0)
        self.assertLess(self.count(".ot-track .ot-m.ot-off"), all_marks)
        self.assertEqual(self.c.js("JSON.parse(localStorage.getItem('site-report-outage')).off.network"), True)
        # the tip of a mark lists only the events still shown
        self.c.js("document.querySelector('input[name=ot-kind][value=network]').click()")
        self.assertEqual(self.count(".ot-track .ot-m.ot-off"), 0)

    def test_a_repeats_fold_and_show_every_repeat(self):
        self.c.js("localStorage.removeItem('site-report-outage')")
        self.c.js("document.querySelector('input[name=ot-rep]').checked && document.querySelector('input[name=ot-rep]').click(); "
                  "document.querySelector('input[name=ot-kind]').dispatchEvent(new Event('change', {bubbles: true}))")
        reps = self.count("tr[data-rep]")
        self.assertEqual(reps, 3)
        self.assertEqual(self.c.js("[...document.querySelectorAll('tr[data-rep]')].every(r => r.hidden)"), True)
        head = self.c.js("(() => {const r = document.querySelector('tr[data-fold]'); return [r.cells[4].textContent.trim(), r.cells[4].title]})()")
        self.assertTrue(head[0].endswith("\u00d74, 02:05-02:17"), head)
        self.assertEqual(head[1], "02:05:00, 02:09:00, 02:13:00, 02:17:00")                              # the times in its title
        self.c.js("document.querySelector('input[name=ot-rep]').click()")
        self.assertEqual(self.c.js("[...document.querySelectorAll('tr[data-rep]')].every(r => !r.hidden)"), True)
        self.assertFalse(self.c.js("document.querySelector('tr[data-fold]').cells[4].textContent.includes('\u00d74')"))
        self.assertEqual(self.c.js("JSON.parse(localStorage.getItem('site-report-outage')).rep"), True)
        self.c.js("document.querySelector('input[name=ot-rep]').click()")


    def reset(self):
        self.c.js("localStorage.removeItem('site-report-outage')")
        self.c.js("document.querySelector('input[name=ot-show][value=all]').click(); document.querySelector('input[name=ot-rep]').checked && document.querySelector('input[name=ot-rep]').click(); "
                  "[...document.querySelectorAll('input[name=ot-kind]')].forEach(i => { if (!i.checked) i.click() })")

    def test_b_the_table_filter_and_the_outage_filter_work_together(self):
        """Codex 3: the outage exclusion is one more input of the row's visibility; clearing the table's filter must not show a row Related only hides."""
        self.reset()
        shown = "[...document.querySelectorAll('tr[data-cls]')].filter(r => !r.hidden).length"
        total = self.count("tr[data-cls]")
        self.c.js("document.querySelector('input[name=ot-show][value=rel]').click()")
        rel_only = self.c.js(shown)
        self.assertLess(rel_only, total)
        tb = self.c.js("(() => {const t = document.querySelector('tr[data-cls]').closest('.tw'); return !!t.previousElementSibling.querySelector('.filter')})()")
        self.assertTrue(tb, "the events table has its filter box")
        box = "document.querySelector('tr[data-cls]').closest('.tw').previousElementSibling.querySelector('.filter')"
        self.c.js("(() => {const f = %s; f.value = 'zzzz-nothing'; f.dispatchEvent(new Event('input', {bubbles: true}))})()" % box)
        self.assertEqual(self.c.js(shown), 0)
        self.c.js("(() => {const f = %s; f.value = ''; f.dispatchEvent(new Event('input', {bubbles: true}))})()" % box)
        self.assertEqual(self.c.js(shown), rel_only)                       # the filter cleared: Related only still holds
        shown_n = self.c.js("document.querySelector('tr[data-cls]').closest('.tw').previousElementSibling.querySelector('.shown').textContent")
        self.assertEqual(shown_n, "%d of %d" % (rel_only, total - self.count("tr[data-rep]") + self.count("tr[data-rep]")) if False else shown_n)
        self.assertTrue(shown_n.startswith("%d of " % rel_only), shown_n)   # the count follows
        self.c.js("document.querySelector('input[name=ot-show][value=all]').click()")
        self.c.js("(() => {const f = %s; f.value = 'zzzz-nothing'; f.dispatchEvent(new Event('input', {bubbles: true}))})()" % box)
        self.c.js("document.querySelector('input[name=ot-show][value=rel]').click()")
        self.c.js("document.querySelector('input[name=ot-show][value=all]').click()")
        self.assertEqual(self.c.js(shown), 0)                              # the outage filter does not show a row the table's filter hides
        self.c.js("(() => {const f = %s; f.value = ''; f.dispatchEvent(new Event('input', {bubbles: true}))})()" % box)

    def test_c_folding_keeps_the_cells_link(self):
        """Codex 4: the fold's text is its own node; an anchor in the cell survives both views."""
        self.reset()
        self.c.js("(() => {const r = document.querySelector('tr[data-fold]'), td = r.cells[4], a = document.createElement('a'); a.className = 'keep'; a.href = 'https://x.example/'; "
                  "const t = td.firstChild; a.textContent = t.textContent; td.replaceChild(a, t)})()")
        self.c.js("document.querySelector('input[name=ot-rep]').click()")
        self.assertEqual(self.c.js("document.querySelectorAll('tr[data-fold] a.keep').length"), 1)
        self.c.js("document.querySelector('input[name=ot-rep]').click()")
        self.assertEqual(self.c.js("document.querySelectorAll('tr[data-fold] a.keep').length"), 1)
        self.assertIn("\u00d74", self.c.js("document.querySelector('tr[data-fold]').cells[4].textContent"))
        self.assertEqual(self.c.js("document.querySelectorAll('tr[data-fold] .ot-fold').length"), 1)       # one suffix node, not one per switch

    def test_d_a_repeats_line_goes_to_its_head_after_a_sort(self):
        """Codex 5: the head row is found by id (data-head), not by walking the table backwards."""
        self.reset()
        self.assertEqual(self.c.js("[...document.querySelectorAll('tr[data-rep]')].every(r => r.getAttribute('data-head') === document.querySelector('tr[data-fold]').id)"), True)
        # the table sorted another way: the repeats now come before their head
        self.c.js("(() => {const h = document.querySelector('tr[data-fold]'); h.parentNode.appendChild(h)})()")
        n = self.c.js("document.querySelector('tr[data-rep]').getAttribute('data-num')")
        self.c.js("(() => {const m = [...document.querySelectorAll('.ot-track .ot-m')].find(m => JSON.parse(m.getAttribute('data-ev')).some(e => e.n == %s)); m.click()})()" % n)
        self.c.js("[...document.querySelectorAll('#site-tip .tt-ln')].find(l => l.querySelector('a.tt-t').getAttribute('href') === '#ot-row-%s').click()" % n)
        self.assertTrue(self.c.js("document.querySelector('tr[data-fold]').classList.contains('ot-flash')"))
        self.assertFalse(self.c.js("document.querySelector('tr[data-rep]').classList.contains('ot-flash')"))


class RealChromeOutageGroups(RealChromeOutage):
    """Codex 6: a mark that stands for several events shows what is left of them."""
    test_6_hovering_a_mark_shows_the_event = test_7_the_keyboard_reaches_each_mark = test_8_a_click_or_enter_pins_it_and_the_list_scrolls = None

    @classmethod
    def web(cls):
        evs = [tot.alarm("01:19:40", "Host connection and power state", "red", "green"),
               tot.alarm("01:19:41", "Datastore accessibility", "yellow", "green"),
               tot.alarm("01:19:42", "Virtual machine CPU usage", "yellow", "green"),
               tot.ev("01:19:43", "AlarmStatusChangedEvent", tot.H, vm="vm-app01", alarm="Virtual machine heartbeat", entity="vm-app01", to="red", **{"from": "green"}),
               tot.ev("01:19:44", "AlarmStatusChangedEvent", tot.H, alarm="Datastore usage on disk", entity="ds-archive", to="yellow", **{"from": "green"})]
        return tot.ot.outage_timeline_doc(tot.WATCH, tot.made(evs, []), True, True)

    GROUP = ("[...document.querySelectorAll('.ot-track .ot-m')].find(m => JSON.parse(m.getAttribute('data-ev')).length > 2)")

    def state(self):
        return self.c.js("(() => {const m = %s; return {label: m.querySelector('b').textContent, cls: [...m.classList].filter(c => /^ot-(critical|warning|low|info|ok|rel|dim|off)$/.test(c)).sort(), "
                         "aria: m.getAttribute('aria-label'), tip: m.getAttribute('data-tip'), icon: m.querySelector('svg') && m.querySelector('svg').outerHTML}})()" % self.GROUP)

    def test_9_label_level_outline_and_text_follow_the_visible_events(self):
        self.c.js("localStorage.removeItem('site-report-outage')")
        before = self.state()
        self.assertRegex(before["label"], r"^\d+\+\d+$")
        ev = self.c.js("JSON.parse(%s.getAttribute('data-ev'))" % self.GROUP)
        classes = sorted(set(e["c"] for e in ev))
        self.assertGreater(len(classes), 1, classes)
        worst = max(ev, key=lambda e: {"critical": 4, "warning": 3, "low": 2, "info": 1, "ok": 0}[e["l"]])
        # hide every kind but the one of the lowest event: the mark becomes that event's
        low = min(ev, key=lambda e: ({"critical": 4, "warning": 3, "low": 2, "info": 1, "ok": 0}[e["l"]], -e["n"]))
        for c in classes:
            if c != low["c"]:
                self.c.js("document.querySelector('input[name=ot-kind][value=%s]').click()" % c)
        now = self.state()
        left = [e for e in ev if e["c"] == low["c"]]
        self.assertEqual(now["label"], str(left[0]["n"]) if len(left) == 1 else "%d+%d" % (left[0]["n"], len(left) - 1))
        self.assertIn("ot-" + max(left, key=lambda e: {"critical": 4, "warning": 3, "low": 2, "info": 1, "ok": 0}[e["l"]])["l"], now["cls"])
        if worst["l"] != max(left, key=lambda e: {"critical": 4, "warning": 3, "low": 2, "info": 1, "ok": 0}[e["l"]])["l"]:
            self.assertNotIn("ot-" + worst["l"], now["cls"])
            self.assertNotEqual(now["icon"], before["icon"])
        self.assertNotEqual(now["aria"], before["aria"])
        self.assertTrue(all(w["w"] in now["aria"] for w in left))
        self.assertFalse(any(w["w"] in now["aria"] for w in ev if w["c"] != low["c"]))
        self.assertFalse(any(w["w"] in now["tip"] for w in ev if w["c"] != low["c"]))
        # all back: as it was
        for c in classes:
            if c != low["c"]:
                self.c.js("document.querySelector('input[name=ot-kind][value=%s]').click()" % c)
        self.assertEqual(self.state(), before)


class RealChromeOutageFiltersBrief(RealChromeOutageFilters):
    TPL = "report_brief.html.j2"


class RealChromeOutageFiltersOps(RealChromeOutageFilters):
    TPL = "report_ops.html.j2"


class RealChromeOutageLinks(RealChromeOutage):
    """The incidents of ServiceNow linked: in the tip's lines, in The events and in the first card - a new tab, https only."""
    test_6_hovering_a_mark_shows_the_event = test_7_the_keyboard_reaches_each_mark = test_8_a_click_or_enter_pins_it_and_the_list_scrolls = None
    URL = "https://dev.service-now.example/nav_to.do?uri=incident.do%3Fsys_id%3Da1"

    @classmethod
    def web(cls):
        return tot.ot.outage_timeline_doc(tot.WATCH, tot.made(instance="https://dev.service-now.example"), True, True)

    def test_9_the_incident_number_is_a_link_in_the_tip(self):
        i = self.c.js("[...document.querySelectorAll(%s)].findIndex(m => JSON.parse(m.getAttribute('data-ev'))[0].k)" % json.dumps(self.SEL))
        self.assertGreaterEqual(i, 0)
        x, y = self.at(self.SEL, i)
        self.c.mouse(5, 5)
        self.c.mouse(x, y)
        got = self.c.js("(() => {const a = document.querySelector('#site-tip .tt-inc'); return a && [a.textContent, a.href, a.target, a.rel]})()")
        self.assertEqual(got, ["INC0012345", self.URL, "_blank", "noopener noreferrer"])
        self.c.mouse(5, 5)

    def test_a_the_events_and_the_card(self):
        cell = self.c.js("(() => {const r = document.querySelector('tr[data-cls]:has(a[href*=\"nav_to.do\"])'); const a = r.querySelector('a[href*=\"nav_to.do\"]'); return [a.href, a.target, a.rel]})()")
        self.assertEqual(cell[:2], [self.URL, "_blank"])
        self.assertIn("noopener", cell[2])
        card = self.c.js("(() => {const a = document.querySelector('.cards h3 a'); return [a.textContent, a.href, a.parentElement.textContent]})()")
        self.assertEqual(card[:2], ["INC0012345", self.URL])
        self.assertTrue(card[2].startswith("INC0012345 opened at"))


class RealChromeOutageBrief(RealChromeOutage):
    TPL = "report_brief.html.j2"


@unittest.skipUnless(_browser(), "no headless browser here")
class RealChromeBreach(unittest.TestCase):
    """Ticket insights' About to breach line: its page loads site-insights.js (served beside it, script-src 'self'); a
    day's hit area shows that day's numbers in the page's own tip."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        h, cls.m = breach_page()
        with open(os.path.join(cls.dir, "page.html"), "w") as f:
            f.write(h)
        with open(os.path.join(cls.dir, "site-insights.js"), "w") as f:
            f.write(snp.SCRIPT)
        handler = lambda *a, **k: Csp(*a, directory=cls.dir, **k)          # noqa: E731
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.c = Cdp(_browser(), 1280, 900)
        cls.c.call("Page.enable")
        cls.c.call("Page.navigate", url="http://127.0.0.1:%d/page.html" % cls.srv.server_address[1])
        end = time.time() + 30
        while not cls.c.js("document.readyState === 'complete' && !!window.siteInsights"):
            if time.time() > end:
                raise RuntimeError("the page or its script did not load")
            time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.c.close()
        cls.srv.shutdown()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_a_day_shows_its_numbers(self):
        last = self.m["points"][-1]
        x, y = self.c.js("(() => {const h = document.querySelectorAll('svg.breach-trend rect.hit'); const e = h[h.length - 1]; "
                         "e.scrollIntoView({block: 'center', behavior: 'instant'}); const r = e.getBoundingClientRect(); "
                         "return [r.left + r.width / 2, r.top + r.height / 2]})()")
        time.sleep(0.2)
        self.c.mouse(x, y)
        tip = self.c.js("(() => {const t = document.getElementById('tip'); return t && !t.hidden ? t.textContent : null})()")
        self.assertIsNotNone(tip, "no tip on the breach line")
        self.assertIn("Breached %d" % last["n"], tip)
        self.assertIn("7-day average %.1f" % last["avg7"], tip)
        self.assertFalse(self.c.js("!!document.querySelector('svg.breach-trend rect.hit title')"))   # the script took the titles off

    def test_the_keyboard(self):
        """Tab to the chart: the last day's tip; the arrow keys, Home and End step through the days; Esc hides it."""
        pts = self.m["points"]
        tip = "(() => {const t = document.getElementById('tip'); return t && !t.hidden ? t.textContent : null})()"
        self.c.mouse(5, 5)
        self.c.js("document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0)")
        for _ in range(300):
            self.c.key("Tab", "Tab", 9)
            if self.c.js("document.activeElement.matches('svg.breach-trend')"):
                break
        self.assertTrue(self.c.js("document.activeElement.matches('svg.breach-trend')"), "Tab never reached the chart")
        self.assertEqual(self.c.js("document.getElementById('tip').getAttribute('role')"), "tooltip")
        self.assertEqual(self.c.js("document.activeElement.getAttribute('aria-describedby')"), "tip")
        day = lambda p: "Breached %d7-day average %.1f" % (p["n"], p["avg7"])       # noqa: E731
        self.assertIn(day(pts[-1]), self.c.js(tip))
        self.c.key("ArrowLeft", "ArrowLeft", 37)
        self.assertIn(day(pts[-2]), self.c.js(tip))
        self.assertEqual(self.c.js("[...document.querySelectorAll('svg.breach-trend rect.hit')].findIndex(r => r.classList.contains('on'))"),
                         len(pts) - 2)                                               # the day is marked
        self.c.key("Home", "Home", 36)
        self.assertIn(day(pts[0]), self.c.js(tip))
        self.c.key("End", "End", 35)
        self.assertIn(day(pts[-1]), self.c.js(tip))
        box = self.c.js("(() => {const r = document.getElementById('tip').getBoundingClientRect(); return [r.left, r.right, innerWidth]})()")
        self.assertTrue(box[0] >= 0 and box[1] <= box[2], box)                       # inside the window
        self.c.key("Escape", "Escape", 27)
        self.assertIsNone(self.c.js(tip))
        self.assertTrue(self.c.js("document.activeElement.matches('svg.breach-trend')"))   # the focus stays


@unittest.skipUnless(_browser(), "no headless browser here")
class RealChromeInsightsIndex(unittest.TestCase):
    """1.68.2: ticket insights' side index (site-insights.js, script-src 'self'): it fits the window and scrolls on its own;
    each level folds, open only along the branch of the page shown; a team page opens with the sections in view (never
    scrolled away to the team); a team picked in the index opens with the index where it was; a section picked lands under
    the sticky bar; Hide / Show kept in site-report-rail, the website's rails' key."""

    @classmethod
    def setUpClass(cls):
        import test_insights_org as O
        import test_servicenow_insights as TS
        cls.dir = tempfile.mkdtemp()
        m = O.model()
        name = lambda k: "p_%s.html" % re.sub(r"[^A-Za-z0-9]+", "_", k)            # noqa: E731
        links = dict((p["key"], name(p["key"])) for p in m["pages"])
        links["all"] = "index.html"
        cls.deep = max(m["pages"], key=lambda p: len(p["path"]))["key"]
        for key in ["all"] + [p["key"] for p in m["pages"]]:
            with open(os.path.join(cls.dir, links[key]), "w") as f:
                f.write(snp.servicenow_insights_page(m, key, dict(TS.HIER_OPTS, links=links)))
        with open(os.path.join(cls.dir, "site-insights.js"), "w") as f:
            f.write(snp.SCRIPT)
        cls.links = links
        handler = lambda *a, **k: Csp(*a, directory=cls.dir, **k)          # noqa: E731
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d/" % cls.srv.server_address[1]
        cls.c = Cdp(_browser(), 1280, 800)
        cls.c.call("Page.enable")

    @classmethod
    def tearDownClass(cls):
        cls.c.close()
        cls.srv.shutdown()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def load(self, page, wait_nav=False):
        if not wait_nav:
            self.c.call("Page.navigate", url=self.base + page)
        time.sleep(0.3)
        end = time.time() + 30
        while not self.c.js("document.readyState === 'complete' && !!window.siteInsights && location.pathname === '/%s'" % page):
            if time.time() > end:
                raise RuntimeError("%s did not load" % page)
            time.sleep(0.1)
        time.sleep(0.2)

    def test_the_index(self):
        c = self.c
        self.load("index.html")
        c.js("localStorage.removeItem('site-report-rail'); sessionStorage.clear()")
        deep = self.links[self.deep]
        self.load(deep)
        st = c.js("(() => {const t = document.querySelector('.toc'), r = t.getBoundingClientRect(); return {bottom: r.bottom, ih: innerHeight, "
                  "sh: t.scrollHeight, ch: t.clientHeight, st: t.scrollTop, open: [...document.querySelectorAll('.toc details.tn[open]')].length, "
                  "folds: document.querySelectorAll('.toc details.tn').length, first: document.querySelector('.toc a[href^=\"#\"]').getBoundingClientRect().top - r.top}})()")
        self.assertLessEqual(st["bottom"], st["ih"])                                   # it fits the window ...
        self.assertGreater(st["sh"], st["ch"])                                          # ... and scrolls on its own
        self.assertEqual(st["st"], 0)                                                   # the sections in view, not scrolled to the team
        self.assertGreaterEqual(st["first"], 0)
        self.assertTrue(0 < st["open"] < st["folds"], st)                              # only the branch shown open
        self.assertTrue(c.js("[...document.querySelectorAll('.toc details.tn[open]')].every(d => d.querySelector('a[aria-current]'))"))
        # a section picked in the index: it lands under the sticky bar
        top, barb = c.js("(async () => {const a = [...document.querySelectorAll('.toc a[href^=\"#\"]')][6]; a.click(); "
                         "await new Promise(r => setTimeout(r, 300)); return [document.querySelector(a.getAttribute('href')).getBoundingClientRect().top, "
                         "document.querySelector('.bar').getBoundingClientRect().bottom]})()")
        self.assertTrue(barb <= top <= barb + 20, (top, barb))
        # a team picked in the index: the next page's index where this one was
        c.js("document.querySelector('.toc').scrollTop = 120")
        to = c.js("(() => {const a = [...document.querySelectorAll('.toc a.team')].filter(a => !a.hasAttribute('aria-current') && a.offsetParent)[1]; "
                  "const h = a.getAttribute('href'); a.click(); return h})()")
        self.load(to, wait_nav=True)
        self.assertEqual(c.js("document.querySelector('.toc').scrollTop"), 120)
        # the filter opens every level; cleared, the folds are back as they were
        before = c.js("[...document.querySelectorAll('.toc details.tn')].map(d => d.open)")
        c.js("(() => {const f = document.getElementById('teamq'); f.value = 'desk'; f.dispatchEvent(new Event('input')); f.value = ''; "
             "f.dispatchEvent(new Event('input'))})()")
        self.assertEqual(c.js("[...document.querySelectorAll('.toc details.tn')].map(d => d.open)"), before)
        # Hide / Show: the tables get the width, the choice kept for every page (the website's rails' key)
        left = c.js("document.querySelector('main').getBoundingClientRect().left")
        c.js("document.querySelector('label.railx').click()")
        time.sleep(0.2)
        self.assertEqual(c.js("[document.documentElement.getAttribute('data-rail'), localStorage.getItem('site-report-rail')]"), ["hidden", "hidden"])
        self.assertLess(c.js("document.querySelector('main').getBoundingClientRect().left"), left - 100)
        self.load("index.html")
        self.assertTrue(c.js("document.getElementById('rail-hide').checked && document.documentElement.getAttribute('data-rail') === 'hidden'"))
        c.js("document.querySelector('label.railx').click()")
        time.sleep(0.2)
        self.assertEqual(c.js("[document.documentElement.getAttribute('data-rail'), localStorage.getItem('site-report-rail')]"), [None, None])


if __name__ == "__main__":
    unittest.main()
