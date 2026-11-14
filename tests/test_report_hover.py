#!/usr/bin/env python3
"""1.57.0: the report website's hover layer and sparklines (plugins/filter/site_filters.py report_sparkline and
report_runway_bar, roles/site_email/templates/site_report.js, report_board.html.j2, report_hover.css.j2): the helpers'
markup (every value in the aria-label, data-points, no script in it); no inline script or handler in an atlas or brief
page, none of it in an email; and in a real Chrome, the page served with a Content-Security-Policy that allows only its
own script file (script-src 'self'): hovering a sparkline shows its day and value, keyboard focus does too (Tab, then the
arrow keys), a runway bar's tip is its data-tip, Esc hides it.
int-11: the Trend column (a section's sparks: ONTAP capacity 1.52.0, the STIG scorecard 1.58.0, patch age 1.61.0) hovers
the same way, and ticket insights' breach line (its own page and script, site-insights.js) shows its day's numbers.
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
        for tpl in ("report_atlas.html.j2", "report_brief.html.j2"):
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
        for tpl in ("report_atlas.html.j2", "report_brief.html.j2"):
            h = render(tpl, web)
            self.assertIn('<table class="bd">', h)
            self.assertIn('svg class="spark"', h)
            self.assertFalse(re.search(r"<[^>]+\son[a-z]+=", h), tpl)        # no onclick= and the like
            self.assertNotIn("javascript:", h)
            # its scripts: the early theme lines (kept: they only save a flash), the file, its inline copy - none new
            self.assertEqual(len(re.findall(r"<script", h)), 3, tpl)
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


TIP = "(() => {const t = document.getElementById('site-tip'); return t && !t.hidden ? [...t.children].map(x => x.textContent) : null})()"


@unittest.skipUnless(_browser(), "no headless browser here")
class RealChrome(unittest.TestCase):
    TPL = "report_atlas.html.j2"

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        web, _ = doc()
        h = render(cls.TPL, web)
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


if __name__ == "__main__":
    unittest.main()
