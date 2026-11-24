#!/usr/bin/env python3
"""1.115.0: the Christmas theme (plugins/module_utils/site_festive.py) on the report website (site_report_web.py puts it into
the atlas, brief and ops pages and their index, and writes site-festive.css / site-festive.js) and the Site portal (site_portal.py
shell, static/festive.css / festive.js). Checked: the season and its last day (none, christmas, auto: Dec 1 to Jan 6); none
changes nothing; the other looks and the emails never get it (the email byte for byte); and in a real Chrome under the sites'
Content-Security-Policy (no violation): the decor on by default, the Festive menu from the mouse and the keyboard, each
switch (Festive, Lights, Twinkle, Snow) on and off and kept in the browser, a site setting of false hides its switch,
reduced motion (no twinkle, no snow), a page past its last day not festive, print, no sideways scroll at 1280 and 800,
the tables' sticky heads under the lights, and every tile, table cell, chip and status mark styled as with the theme off.
  python3 tests/test_festive.py      (the Chrome part skips without google-chrome or chromium)"""
import datetime
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "module_utils"))
import site_festive as fz  # noqa: E402
from test_report_hover import Cdp, _browser, render  # noqa: E402
from test_report_web import run_module, web_args  # noqa: E402
import test_report_web as trw  # noqa: E402,F401  (loads site_report_web with its module_utils)
rw = trw.rw

D = datetime.date
ME = "DOE.JANE.Q.1234567890"
REPORT = {
    "title": "VMware - VM health report", "subtitle": "vc01.corp.example.mil - 412 VMs, SITEA", "status": "critical",
    "summary": [
        {"label": "VMs checked", "value": 412, "status": "ok", "note": "on 24 hosts"},
        {"label": "Datastores over 90%", "value": 2, "status": "critical", "note": "ds-sitea-07, ds-sitea-11"},
        {"label": "Snapshots over 3 days", "value": 6, "status": "warning", "note": "oldest 19 days"},
        {"label": "Powered off", "value": 9, "status": "info", "note": "over 30 days: 3"}],
    "sections": [
        {"title": "Datastores", "status": "critical", "text": "Each datastore: how full it is.",
         "columns": ["Datastore", "Cluster", "Used", "Free", "Status"],
         "rows": [["ds-sitea-%02d" % i, "SITEA-CL01", "%d%%" % (95 - i * 3), "%.1f TB" % (0.5 + i / 2), "Over 90%" if i < 3 else "OK"] for i in range(1, 41)],
         "row_status": ["critical" if i < 3 else "" for i in range(1, 41)],
         "cell_status": [["", "", "critical" if i < 3 else "", "", "critical" if i < 3 else "ok"] for i in range(1, 41)]},
        {"title": "Snapshots over 3 days", "status": "warning", "text": "Snapshots older than 3 days.",
         "columns": ["VM", "Snapshot", "Age", "Made by"],
         "rows": [["vm-app01", "before patching", "19 days", ME], ["vm-db02", "pre-upgrade", "11 days", ME]],
         "row_status": ["warning", "warning"], "cell_status": [["", "", "warning", ""], ["", "", "warning", ""]]},
        {"title": "Hosts", "status": "ok", "text": "Each ESXi host.", "columns": ["Host", "Address"],
         "rows": [["esx01.corp.example.mil", "192.0.2.11"], ["esx02.corp.example.mil", "192.0.2.12"]]}]}
REPORT_CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'none'"
PROBE = """(() => {
  const sel = '.tile, .tile *, table th, table td, .chip, [class*="c-"], [class*="s-"], .hc, .ev, .rr, .area, .runs .bar, .hero h1, main h2, .jl li, .jd *';
  return [...document.querySelectorAll(sel)].filter(e => !e.closest('.fz-m, .fz-lights, .fz-snow, .fz-tree')).slice(0, 1500).map(e => {
    const c = getComputedStyle(e); return [e.tagName, e.className, c.color, c.backgroundColor, c.borderTopColor, c.borderTopWidth, c.fontFamily,
      c.fontSize, c.fontWeight].join('|'); });
})()"""
SHOWN = "(s => [...document.querySelectorAll(s)].filter(e => getComputedStyle(e).display !== 'none').length)"
PARTS_STATE = ("(() => { const r = document.documentElement; return [r.getAttribute('data-festive'), r.getAttribute('data-fz-lights'), "
               "r.getAttribute('data-fz-twinkle'), r.getAttribute('data-fz-snow')] })()")
CSP_HOOK = ("window.__csp = []; document.addEventListener('securitypolicyviolation', e => window.__csp.push(e.violatedDirective + ' ' + e.blockedURI));")


class Season(unittest.TestCase):
    def test_christmas_on_until_the_coming_jan_6(self):
        self.assertEqual(fz.season_until("christmas", D(2026, 12, 14)), "2027-01-06")
        self.assertEqual(fz.season_until("christmas", D(2027, 1, 5)), "2027-01-06")
        self.assertEqual(fz.season_until("christmas", D(2027, 1, 6)), "2027-01-06")
        self.assertEqual(fz.season_until("Christmas ", D(2026, 7, 1)), "2027-01-06")       # set by hand: on

    def test_auto_dec_1_to_jan_6(self):
        self.assertEqual(fz.season_until("auto", D(2026, 11, 30)), "")
        self.assertEqual(fz.season_until("auto", D(2026, 12, 1)), "2027-01-06")
        self.assertEqual(fz.season_until("auto", D(2026, 12, 31)), "2027-01-06")
        self.assertEqual(fz.season_until("auto", D(2027, 1, 6)), "2027-01-06")
        self.assertEqual(fz.season_until("auto", D(2027, 1, 7)), "")

    def test_the_end_is_one_instant_in_the_site_zone(self):
        # Codex P1 (1.115.0 review): the end is the site's midnight after Jan 6, the same instant for every viewer
        import zoneinfo
        self.assertEqual(fz.season_end("2027-01-06"), "2027-01-07T00:00:00Z")
        self.assertEqual(fz.season_end("2027-01-06", zoneinfo.ZoneInfo("Europe/Berlin")), "2027-01-06T23:00:00Z")
        self.assertEqual(fz.season_end("2027-01-06", zoneinfo.ZoneInfo("America/New_York")), "2027-01-07T05:00:00Z")
        self.assertEqual(fz.season_end(""), "")
        self.assertIn('data-season-end="2027-01-06T23:00:00Z"', fz.html_attrs("2027-01-06", "16bit", fz.PARTS, zoneinfo.ZoneInfo("Europe/Berlin")))

    def test_none_and_anything_else_off(self):
        for s in ("none", "", None, False, "easter", "off"):
            self.assertEqual(fz.season_until(s, D(2026, 12, 14)), "", s)

    def test_parts(self):
        self.assertEqual(fz.allowed(), ["lights", "twinkle", "sleigh", "snow"])
        self.assertEqual(fz.allowed(twinkle=False), ["lights", "sleigh", "snow"])
        self.assertEqual(fz.allowed(lights="false"), ["snow"])                  # no lights: no twinkle and no sleigh either
        self.assertEqual(fz.allowed(snow=False, twinkle="0", sleigh="0"), ["lights"])
        self.assertEqual(fz.allowed(True, True, True, False), ["lights", "twinkle", "snow"])
        self.assertEqual(fz.parts_of("16bit", fz.PARTS), ["lights", "twinkle", "sleigh", "snow"])
        self.assertEqual(fz.parts_of("8bit", fz.PARTS), ["lights", "twinkle", "snow"])          # 8bit: no sleigh
        self.assertEqual(fz.style_of("8BIT"), "8bit")
        self.assertEqual(fz.style_of("4bit"), "16bit")


def page(look="atlas"):
    return render("report_%s.html.j2" % look, REPORT)


class Decorate(unittest.TestCase):
    def test_off_is_the_page_as_it_is(self):
        h = page()
        self.assertEqual(fz.decorate(h, "report", ""), h)

    def test_each_web_look_gets_it_in_its_bar(self):
        for look in ("atlas", "brief", "ops"):
            h = page(look)
            out = fz.decorate(h, "report", "2027-01-06", "16bit")
            self.assertNotEqual(out, h, look)
            for part in ('data-season="christmas"', 'data-season-until="2027-01-06"', 'class="fz-m"', 'class="fz-tree"',
                         'class="fz-lights"', 'class="fz-snow"', 'href="site-festive.css"', 'src="site-festive.js"'):
                self.assertEqual(out.count(part), 1, (look, part))
            self.assertLess(out.index('class="fz-lights"'), out.index('<div class="wrap'), look)      # in the bar
            self.assertNotIn("<script>", fz.menu("16bit", fz.PARTS) + fz.lights("16bit"))

    def test_the_other_looks_and_pages_never(self):
        for look in ("standard", "command", "portal", "glass"):
            h = render("report.html.j2" if look == "standard" else "report_%s.html.j2" % look, REPORT)
            self.assertEqual(fz.decorate(h, "report", "2027-01-06"), h, look)
        self.assertEqual(fz.decorate("<html><head></head><body>x</body></html>", "report", "2027-01-06"), "<html><head></head><body>x</body></html>")

    def test_site_switches(self):
        out = fz.decorate(page(), "report", "2027-01-06", "8bit", ["lights", "snow"])
        self.assertIn('data-fz="8bit"', out)
        self.assertIn('data-fz-allow="lights snow"', out)
        self.assertNotIn('data-k="twinkle"', out)
        self.assertIn('data-k="snow"', out)

    def test_the_insights_pages_never(self):
        from test_report_hover import breach_page
        h = breach_page()[0]
        self.assertEqual(fz.decorate(h, "report", "2027-01-06"), h)

    def test_no_outside_file_and_no_inline_code(self):
        for text in (fz.CSS, fz.JS):
            for bad in ("http://", "https://", "@import", "eval(", "innerHTML", "url(http"):
                self.assertNotIn(bad, text)
        out = fz.decorate(page(), "report", "2027-01-06")
        added = out
        for k in ("menu", "lights", "snow", "tree"):
            frag = getattr(fz, k)("16bit", fz.PARTS) if k == "menu" else getattr(fz, k)("16bit")
            self.assertNotIn(" style=", frag)
            self.assertNotRegex(frag, r"\son[a-z]+=")
        self.assertIn("site-festive.js", added)


class Email(unittest.TestCase):
    def test_the_email_is_byte_for_byte_the_same(self):
        from test_report_fit import EmailTiles
        env = EmailTiles()._env()
        for tpl in ("report.html.j2", "report_command.html.j2", "report_portal.html.j2", "report_glass.html.j2"):
            a = env.get_template(tpl).render(_report=REPORT, _email_footer="AAP job 9", report_email_header_text="x")
            b = env.get_template(tpl).render(_report=REPORT, _email_footer="AAP job 9", report_email_header_text="x", report_theme_season="christmas",
                                             report_theme_season_style="8bit", report_theme_season_twinkle=True)
            self.assertEqual(a, b, tpl)
            self.assertNotIn("fz-", b)


def publish(dest, season, theme="atlas", html=None, **kw):
    args = web_args(dest, html=html or page(theme if theme in ("atlas", "brief", "ops") else "atlas"), theme=theme, season=season, **kw)
    out = run_module(args)
    assert not out.get("failed"), out
    return out


class Website(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_on_writes_the_two_files_and_links_them_from_each_place(self):
        out = publish(self.root, "christmas")
        self.assertIn(fz.CSS_FILE, out["written"])
        self.assertIn(fz.JS_FILE, out["written"])
        with open(os.path.join(self.root, fz.CSS_FILE)) as f:
            self.assertEqual(f.read(), fz.CSS)
        with open(os.path.join(self.root, fz.JS_FILE)) as f:
            self.assertEqual(f.read(), fz.JS)
        with open(os.path.join(self.root, "vm", "latest.html")) as f:
            h = f.read()
        self.assertIn('href="../site-festive.css?v=%s"' % fz.version(fz.CSS), h)
        self.assertIn('src="../site-festive.js?v=%s"' % fz.version(fz.JS), h)
        self.assertIn('data-season="christmas"', h)
        self.assertRegex(h, r'data-season-end="\d{4}-01-0[67]T\d\d:00:00Z"')
        with open(os.path.join(self.root, "index.html")) as f:
            ix = f.read()
        self.assertIn('src="site-festive.js?v=', ix)
        self.assertIn('class="fz-lights"', ix)
        again = publish(self.root, "christmas")                 # the same files there: not written again
        self.assertNotIn(fz.CSS_FILE, again["written"])

    def test_none_and_auto_out_of_season_write_nothing_of_it(self):
        a = publish(os.path.join(self.root), "none")
        self.assertNotIn(fz.CSS_FILE, a["written"])
        with open(os.path.join(self.root, "vm", "latest.html")) as f:
            self.assertNotIn("fz-", f.read())
        with open(os.path.join(self.root, "index.html")) as f:
            self.assertNotIn("fz-", f.read())

    def test_none_is_the_page_as_without_the_setting(self):
        d1, d2 = os.path.join(self.root, "a"), os.path.join(self.root, "b")
        os.makedirs(d1)
        os.makedirs(d2)
        publish(d1, "none")
        args = web_args(d2, html=page(), theme="atlas")
        run_module(args)
        with open(os.path.join(d1, "vm", "latest.html")) as f, open(os.path.join(d2, "vm", "latest.html")) as g:
            self.assertEqual(f.read(), g.read())

    def test_an_email_look_on_the_website_never(self):
        out = publish(self.root, "christmas", theme="standard", html=render("report.html.j2", REPORT))
        self.assertNotIn(fz.CSS_FILE, out["written"])
        with open(os.path.join(self.root, "vm", "latest.html")) as f:
            self.assertNotIn("fz-", f.read())

    def test_the_windows_script_writes_them_too(self):
        out = publish(self.root, "christmas", windows_host="web01.corp.example.mil")
        with open(out["script"], encoding="utf-8-sig") as f:
            ps = f.read()
        os.remove(out["script"])
        self.assertIn("'site-festive.css'", ps)
        self.assertIn("'site-festive.js'", ps)


class Csp(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Content-Security-Policy", REPORT_CSP)
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *a):
        pass


@unittest.skipUnless(_browser(), "no headless browser here")
class RealChrome(unittest.TestCase):
    """The website's report page and index, published by the module (season christmas, and none for the reference)."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        for sub, season in (("on", "christmas"), ("off", "none")):
            os.makedirs(os.path.join(cls.dir, sub))
            publish(os.path.join(cls.dir, sub), season)
            for look in ("brief", "ops"):                                  # every look made for a browser
                os.makedirs(os.path.join(cls.dir, "%s-%s" % (sub, look)))
                publish(os.path.join(cls.dir, "%s-%s" % (sub, look)), season, theme=look)
        os.makedirs(os.path.join(cls.dir, "eight"))
        publish(os.path.join(cls.dir, "eight"), "christmas", season_style="8bit", season_snow=False, season_twinkle=False)
        with open(os.path.join(cls.dir, "on", "vm", "latest.html")) as f:
            h = f.read()
        with open(os.path.join(cls.dir, "on", "vm", "past.html"), "w") as f:   # written last season: over by now
            f.write(h.replace('data-season-until="', 'data-season-until="2020-01-06" data-x="', 1)
                    .replace('data-season-end="', 'data-season-end="2020-01-07T00:00:00Z" data-y="', 1))
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **k: Csp(*a, directory=cls.dir, **k))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.c = Cdp(_browser(), 1280, 900)
        cls.c.call("Page.enable")
        cls.c.call("Page.addScriptToEvaluateOnNewDocument", source=CSP_HOOK)

    @classmethod
    def tearDownClass(cls):
        cls.c.close()
        cls.srv.shutdown()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def open(self, path, w=1280, scheme="light", motion="no-preference", clear=False):
        c = self.c
        c.call("Emulation.setDeviceMetricsOverride", width=w, height=900, deviceScaleFactor=1, mobile=False)
        c.call("Emulation.setEmulatedMedia", media="", features=[{"name": "prefers-color-scheme", "value": scheme},
                                                                 {"name": "prefers-reduced-motion", "value": motion}])
        url = "http://127.0.0.1:%d/%s" % (self.srv.server_address[1], path)
        if clear:
            c.call("Page.navigate", url=url)
            self.ready()
            c.js("localStorage.clear()")
        c.call("Page.navigate", url=url)
        self.ready()
        time.sleep(0.3)

    def ready(self):
        end = time.time() + 30
        while not self.c.js("document.readyState === 'complete'"):
            self.assertLess(time.time(), end)
            time.sleep(0.1)

    def shown(self, sel):
        return self.c.js(SHOWN + "(%s)" % json.dumps(sel))

    def anim(self):
        return self.c.js("getComputedStyle(document.querySelector('.fz-b1')).animationName")

    def press(self, key, code, vk, text=""):
        kw = {"text": text} if text else {}
        self.c.call("Input.dispatchKeyEvent", type="keyDown", key=key, code=code, windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk, **kw)
        self.c.call("Input.dispatchKeyEvent", type="keyUp", key=key, code=code, windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk)
        time.sleep(0.1)

    def csp_off(self, path):
        """The violations the page has with the setting at none (its own inline copy of site-report.js, by design): the
        theme adds none."""
        self.open("off/" + path)
        return self.c.js("window.__csp")

    def flip(self, k):
        self.c.js("document.querySelector('.fz-sw[data-k=\"%s\"]').click()" % k)

    def test_1_on_by_default_and_no_csp_violation(self):
        for path in ("on/vm/latest.html", "on/index.html"):
            before = self.csp_off(path[3:])
            self.open(path, clear=True)
            self.assertEqual(self.c.js(PARTS_STATE), ["on", "on", "on", "on"], path)
            self.assertEqual(self.shown(".fz-lights"), 1, path)
            self.assertEqual(self.shown(".fz-snow"), 1, path)
            self.assertEqual(self.shown(".fz-tree"), 1, path)
            self.assertEqual(self.shown(".fz-m"), 1, path)
            self.assertEqual(self.anim(), "fz-tw", path)
            self.assertEqual(self.c.js("window.__csp"), before, path)
            self.assertFalse([v for v in before if "festive" in v or "style-src" in v], before)
            self.assertEqual(self.c.js("document.documentElement.scrollWidth - document.documentElement.clientWidth"), 0, path)

    def test_2_the_menu_from_the_keyboard(self):
        self.open("on/vm/latest.html", clear=True)
        self.assertTrue(self.c.js("document.querySelector('.fz-pop').hidden"))
        self.c.js("document.querySelector('.fz-tg').focus()")
        self.press("Enter", "Enter", 13, "\r")
        self.assertFalse(self.c.js("document.querySelector('.fz-pop').hidden"))
        self.assertEqual(self.c.js("document.querySelector('.fz-tg').getAttribute('aria-expanded')"), "true")
        self.assertEqual(self.c.js("document.activeElement.getAttribute('data-k')"), "festive")
        self.press("Tab", "Tab", 9)
        self.assertEqual(self.c.js("document.activeElement.getAttribute('data-k')"), "lights")
        self.press(" ", "Space", 32, " ")                                   # Lights off from the keyboard
        self.assertEqual(self.c.js(PARTS_STATE)[1], "off")
        self.press("Escape", "Escape", 27)
        self.assertTrue(self.c.js("document.querySelector('.fz-pop').hidden"))
        self.assertTrue(self.c.js("document.activeElement.classList.contains('fz-tg')"))
        self.c.js("localStorage.clear()")

    def test_3_each_switch_on_and_off_and_kept(self):
        self.open("on/vm/latest.html", clear=True)
        self.c.js("document.querySelector('.fz-tg').click()")
        # twinkle
        self.flip("twinkle")
        self.assertEqual(self.c.js(PARTS_STATE), ["on", "on", "off", "on"])
        self.assertEqual(self.anim(), "none")
        self.assertEqual(self.c.js("localStorage.getItem('site-festive-twinkle')"), "off")
        self.open("on/vm/latest.html")
        self.assertEqual(self.anim(), "none")
        self.assertEqual(self.shown(".fz-lights"), 1)                    # the lights stay, steady
        self.c.js("document.querySelector('.fz-tg').click()")
        self.flip("twinkle")
        self.assertEqual(self.anim(), "fz-tw")
        self.assertIsNone(self.c.js("localStorage.getItem('site-festive-twinkle')"))
        # lights: off hides the string and the Twinkle switch
        self.flip("lights")
        self.assertEqual(self.shown(".fz-lights"), 0)
        self.assertTrue(self.c.js("document.querySelector('.fz-sw[data-k=\"twinkle\"]').hidden"))
        self.assertEqual(self.c.js("localStorage.getItem('site-festive-lights')"), "off")
        self.open("on/vm/latest.html")
        self.assertEqual(self.shown(".fz-lights"), 0)
        self.assertEqual(self.shown(".fz-tree"), 1)                      # the tree stays with Festive
        self.c.js("document.querySelector('.fz-tg').click()")
        self.flip("lights")
        self.assertEqual(self.shown(".fz-lights"), 1)
        # snow
        self.flip("snow")
        self.assertEqual(self.shown(".fz-snow"), 0)
        self.assertEqual(self.c.js("localStorage.getItem('site-festive-snow')"), "off")
        self.open("on/index.html")                                      # the same keys on the index
        self.assertEqual(self.shown(".fz-snow"), 0)
        self.assertEqual(self.shown(".fz-lights"), 1)
        self.c.js("document.querySelector('.fz-tg').click()")
        self.flip("snow")
        self.assertEqual(self.shown(".fz-snow"), 1)
        # Festive: all of it
        self.flip("festive")
        self.assertEqual(self.c.js(PARTS_STATE)[0], "off")
        for sel in (".fz-lights", ".fz-snow", ".fz-tree"):
            self.assertEqual(self.shown(sel), 0, sel)
        self.assertEqual(self.shown(".fz-m"), 1)                         # the button stays, to turn it on again
        self.assertEqual(self.c.js("document.querySelector('.fz-tg .fz-s').textContent"), "Off")
        self.assertTrue(self.c.js("document.querySelector('.fz-sw[data-k=\"snow\"]').hidden"))
        self.open("on/vm/latest.html")
        self.assertEqual(self.c.js(PARTS_STATE)[0], "off")
        self.assertEqual(self.c.js("getComputedStyle(document.querySelector('.nav a') || document.body, '::after').backgroundImage"), "none")
        self.c.js("document.querySelector('.fz-tg').click()")
        self.flip("festive")
        self.assertEqual(self.shown(".fz-lights"), 1)
        self.assertFalse([v for v in self.c.js("window.__csp") if "inline" not in v])
        self.c.js("localStorage.clear()")

    def test_4_reduced_motion_no_twinkle_no_snow(self):
        self.open("on/vm/latest.html", motion="reduce", clear=True)
        self.assertEqual(self.anim(), "none")
        self.assertEqual(self.shown(".fz-snow"), 0)
        self.assertEqual(self.shown(".fz-lights"), 1)
        self.c.js("document.querySelector('.fz-tg').click()")
        self.assertEqual(self.shown(".fz-sw[data-k=\"twinkle\"]"), 0)
        self.assertEqual(self.shown(".fz-sw[data-k=\"snow\"]"), 0)
        self.assertEqual(self.shown(".fz-sw[data-k=\"lights\"]"), 1)

    def test_5_site_settings_hide_their_switch(self):
        self.open("eight/vm/latest.html", clear=True)
        self.assertEqual(self.c.js("document.documentElement.getAttribute('data-fz')"), "8bit")
        self.assertEqual(self.c.js(PARTS_STATE), ["on", "on", "off", "off"])
        self.assertEqual(self.c.js("document.querySelectorAll('.fz-sw[data-k=\"twinkle\"], .fz-sw[data-k=\"snow\"]').length"), 0)
        self.assertEqual(self.anim(), "none")
        self.assertEqual(self.shown(".fz-snow"), 0)

    def test_6_past_its_last_day_not_festive(self):
        self.open("on/vm/past.html", clear=True)
        self.assertIsNone(self.c.js("document.documentElement.getAttribute('data-festive')"))
        for sel in (".fz-lights", ".fz-snow", ".fz-tree", ".fz-m"):
            self.assertEqual(self.shown(sel), 0, sel)

    def variant(self, name, end, until="2099-01-06"):
        with open(os.path.join(self.dir, "on", "vm", "latest.html")) as f:
            h = f.read()
        import re
        h = re.sub(r'data-season-until="[^"]*"', 'data-season-until="%s"' % until, h, count=1)
        h = re.sub(r'data-season-end="[^"]*"', 'data-season-end="%s"' % end, h, count=1)
        with open(os.path.join(self.dir, "on", "vm", name), "w") as f:
            f.write(h)
        return "on/vm/" + name

    def test_6b_the_end_is_the_sites_instant_in_any_viewer_zone(self):
        # Codex P1: a viewer whose local date is still Jan 6 after the site's season ended sees no decor
        past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
        local_today = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 10 * 3600))
        path = self.variant("zone.html", past, until=local_today)
        self.c.call("Emulation.setTimezoneOverride", timezoneId="Pacific/Honolulu")
        try:
            self.open(path, clear=True)
            self.assertIsNone(self.c.js("document.documentElement.getAttribute('data-festive')"))
            self.assertEqual(self.shown(".fz-lights"), 0)
        finally:
            self.c.call("Emulation.setTimezoneOverride", timezoneId="")

    def test_6c_a_page_left_open_stops_at_the_end(self):
        # Codex P1: the end checked again while the page is open (a minute's timer, and when the tab comes back)
        soon = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 4))
        self.open(self.variant("open.html", soon), clear=True)
        self.assertEqual(self.c.js("document.documentElement.getAttribute('data-festive')"), "on")
        time.sleep(5)
        self.c.js("document.dispatchEvent(new Event('visibilitychange'))")
        self.assertIsNone(self.c.js("document.documentElement.getAttribute('data-festive')"))
        for sel in (".fz-lights", ".fz-snow", ".fz-tree", ".fz-m"):
            self.assertEqual(self.shown(sel), 0, sel)

    def test_7_content_unchanged_light_dark_1280_800(self):
        for path, look in [(p, l) for p in ("vm/latest.html", "index.html") for l in ("", "-brief", "-ops")]:
            for scheme in ("light", "dark"):
                for w in (1280, 800):
                    self.open("off%s/%s" % (look, path), w, scheme, clear=True)
                    ref = self.c.js(PROBE)
                    self.open("on%s/%s" % (look, path), w, scheme)
                    self.assertEqual(self.shown(".fz-lights"), 1, (look, path))
                    self.assertEqual(self.c.js("document.documentElement.getAttribute('data-festive')"), "on")
                    now = self.c.js(PROBE)
                    self.assertGreater(len(ref), 20, path)
                    self.assertEqual(now, ref, (path, scheme, w))
                    self.assertEqual(self.c.js("document.documentElement.scrollWidth - document.documentElement.clientWidth"), 0, (path, w))

    def test_8_sticky_heads_sit_under_the_lights(self):
        for look in ("", "-brief", "-ops"):
            for w in (1280, 800):
                self.sticky("on%s/vm/latest.html" % look, w)

    def sticky(self, path, w):
        self.open(path, w, clear=True)
        self.c.js("(() => { const t = document.querySelector('.wrap table'); scrollTo(0, t.getBoundingClientRect().top + scrollY + 300); })()")
        time.sleep(0.3)
        got = self.c.js("""(() => { const bar = document.querySelector('.appbar').getBoundingClientRect();
          const th = [...document.querySelectorAll('.wrap th')].find(t => { const r = t.getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight; });
          if (!th) return null; const r = th.getBoundingClientRect();
          const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
          return [Math.round(bar.bottom), Math.round(r.top), !!hit && th.contains(hit)]; })()""")
        self.assertIsNotNone(got, (path, w))
        self.assertGreaterEqual(got[1] + 1, got[0], (path, w))
        self.assertTrue(got[2], (path, w, got))

    def test_9_print_shows_none_of_it(self):
        self.open("on/vm/latest.html", clear=True)
        self.c.call("Emulation.setEmulatedMedia", media="print")
        time.sleep(0.2)
        for sel in (".fz-lights", ".fz-snow", ".fz-tree", ".fz-m", ".fz-sky", ".fz-snowman"):
            self.assertEqual(self.shown(sel), 0, sel)
        self.c.call("Emulation.setEmulatedMedia", media="")

    # ---- the sleigh, the snowman, the snow under 1200 px
    def rect(self, sel):
        return self.c.js("(r => r && [r.left, r.top, r.right, r.bottom])(document.querySelector(%s)?.getBoundingClientRect())" % json.dumps(sel))

    def test_10_no_snow_under_1200_px(self):
        for path in ("on/vm/latest.html", "on/index.html"):
            self.open(path, 800, clear=True)
            self.assertEqual(self.c.js(PARTS_STATE)[3], "on", path)
            self.assertEqual(self.shown(".fz-snow"), 0, path)
            self.open(path, 1280)
            self.assertEqual(self.shown(".fz-snow"), 1, path)

    def test_11_the_sleigh_flies_in_the_lights_now_and_then(self):
        for path in ("on/vm/latest.html", "on/index.html"):
            for w in (1280, 800):
                self.open(path, w, clear=True)
                self.assertEqual(self.c.js("document.documentElement.getAttribute('data-fz-sleigh')"), "on", path)
                self.assertEqual(self.shown(".fz-sky"), 1, path)
                self.assertEqual(self.c.js("document.querySelector('.fz-sky').parentElement.className"), "fz-lights")
                self.assertEqual(self.rect(".fz-sky"), self.rect(".fz-lights"), (path, w))        # the lights' strip: nothing else there
                self.assertEqual(self.c.js("getComputedStyle(document.querySelector('.fz-sky')).pointerEvents"), "none")
                self.assertFalse(self.c.js("document.querySelector('.fz-sky').classList.contains('fz-fly')"))
                time.sleep(3.6)                                                                  # ~3 s after the page opens
                self.assertTrue(self.c.js("document.querySelector('.fz-sky').classList.contains('fz-fly')"), (path, w))
                self.assertEqual(self.c.js("getComputedStyle(document.querySelector('.fz-sled')).animationName"), "fz-sled")
                self.assertGreater(self.c.js("+localStorage.getItem('site-festive-sleigh-at')"), 0)
                self.assertEqual(self.c.js("document.documentElement.scrollWidth - document.documentElement.clientWidth"), 0, (path, w))
                self.open(path, w)                                                               # the next page: not again so soon
                time.sleep(3.6)
                self.assertFalse(self.c.js("document.querySelector('.fz-sky').classList.contains('fz-fly')"), (path, w))
                self.assertEqual(self.c.js("getComputedStyle(document.querySelector('.fz-sled')).visibility"), "hidden")
        self.c.js("document.querySelector('.fz-tg').click()")
        self.assertEqual(self.shown('.fz-sw[data-k="sleigh"]'), 1)
        self.flip("lights")                                                                      # only while Lights is on
        self.assertEqual(self.shown(".fz-sky"), 0)
        self.assertEqual(self.shown('.fz-sw[data-k="sleigh"]'), 0)
        self.assertEqual(self.c.js("document.documentElement.getAttribute('data-fz-sleigh')"), "off")
        self.flip("lights")
        self.flip("sleigh")
        self.assertEqual(self.shown(".fz-sky"), 0)
        self.assertEqual(self.c.js("localStorage.getItem('site-festive-sleigh')"), "off")
        self.c.js("localStorage.clear()")

    def parked(self):
        sled, lights, holly = self.rect(".fz-sled"), self.rect(".fz-lights"), self.rect(".fz-holly.r")
        self.assertEqual(self.c.js("getComputedStyle(document.querySelector('.fz-sled')).visibility"), "visible")
        self.assertEqual(self.c.js("getComputedStyle(document.querySelector('.fz-sled')).animationName"), "none")
        self.assertGreaterEqual(sled[0], lights[0])
        self.assertLessEqual(sled[2], holly[0])                                                 # still, at the right end, before the holly
        self.assertGreater(sled[0], lights[2] / 2)
        time.sleep(3.6)
        self.assertFalse(self.c.js("document.querySelector('.fz-sky').classList.contains('fz-fly')"))

    def test_12_less_motion_parks_the_sleigh_and_the_snowman_is_still(self):
        for w in (1280, 800):
            self.open("on/vm/latest.html", w, motion="reduce", clear=True)
            self.assertEqual(self.shown(".fz-sky"), 1)
            self.parked()
            self.assertEqual(self.c.js("getComputedStyle(document.querySelector('.fz-arm-d')).animationName"), "none")
            self.assertEqual(self.shown(".fz-arm-u"), 0)
        self.open("on/vm/latest.html", clear=True)                                               # the site's motion off
        self.c.js("document.documentElement.classList.add('motion-off')")
        self.parked()

    def test_13_the_snowman_by_the_reports_title_not_on_the_index(self):
        for look in ("", "-brief", "-ops"):
            self.open("on%s/vm/latest.html" % look, clear=True)
            self.assertEqual(self.shown(".fz-snowman"), 1, look)
            self.assertEqual(self.c.js("document.querySelector('.fz-snowman').parentElement.tagName"), "H1", look)
            self.assertTrue(self.c.js("!!document.querySelector('.wrap h1 .fz-snowman, main h1 .fz-snowman')"), look)
            self.assertEqual(self.c.js("getComputedStyle(document.querySelector('.fz-arm-d')).animationName"), "fz-armd", look)
        self.open("on/index.html")
        self.assertEqual(self.c.js("document.querySelectorAll('.fz-snowman').length"), 0)
        self.assertEqual(self.shown(".fz-tree"), 1)

    def test_14_8bit_has_neither(self):
        self.open("eight/vm/latest.html", clear=True)
        self.assertEqual(self.c.js("document.querySelectorAll('.fz-sky, .fz-snowman, .fz-sw[data-k=\"sleigh\"]').length"), 0)
        self.assertIsNone(self.c.js("document.documentElement.getAttribute('data-fz-sleigh') === 'on' || null"))


class SleighAndSnowman(unittest.TestCase):
    """The markup: where each goes, the settings wired end to end, nothing inline."""

    def test_report_page(self):
        h = page()
        out = fz.decorate(h, "report", "2027-01-06", "16bit")
        self.assertEqual(out.count('class="fz-sky"'), 1)
        self.assertEqual(out.count('class="fz-snowman"'), 1)
        lights = out.index('class="fz-lights"')
        self.assertTrue(lights < out.index('class="fz-sky"') < out.index('<div class="wrap'))     # in the lights, in the bar
        i = out.index('class="fz-snowman"')
        self.assertEqual(out.index("</h1>", out.index('<div class="wrap')), out.index("</svg>", i) + len("</svg>"))   # right before the title's </h1>
        self.assertIn('data-k="sleigh"', out)
        self.assertIn('data-fz-allow="lights twinkle sleigh snow"', out)
        self.assertEqual(out.count("<script>"), h.count("<script>"))                                # no inline script added

    def test_index_page_has_the_sleigh_but_no_snowman(self):
        d = tempfile.mkdtemp()
        try:
            publish(d, "christmas")
            with open(os.path.join(d, "index.html")) as fh:
                idx = fh.read()
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertIn('class="fz-sky"', idx)
        self.assertNotIn("fz-snowman", idx)
        self.assertIn('class="fz-tree"', idx)

    def test_8bit_and_the_setting_off_leave_it_out(self):
        for style, parts in (("8bit", fz.PARTS), ("16bit", ["lights", "twinkle", "snow"]), ("16bit", ["sleigh", "snow"])):
            out = fz.decorate(page(), "report", "2027-01-06", style, parts)
            self.assertNotIn('class="fz-sky"', out, (style, parts))
            self.assertNotIn('data-k="sleigh"', out, (style, parts))
            self.assertNotIn("sleigh", out[:out.index("<head")], (style, parts))
        self.assertNotIn("fz-snowman", fz.decorate(page(), "report", "2027-01-06", "8bit"))
        self.assertEqual(fz.sky("8bit") + fz.snowman("8bit"), "")

    def test_nothing_inline(self):
        for frag in (fz.sky("16bit"), fz.snowman("16bit"), fz.lights("16bit", True)):
            self.assertNotIn(" style=", frag)
            self.assertNotIn("<script", frag)
            self.assertNotRegex(frag, r"\son[a-z]+=")
            self.assertIn('aria-hidden="true"', frag)
        self.assertNotIn(".style", fz.JS)                                 # no CSSOM either: the flight is CSS only
        self.assertNotIn("setAttribute(\"style", fz.JS)

    def test_the_snow_rule_under_1200_px_wins(self):
        self.assertIn('@media (max-width:1199px){html[data-festive="on"][data-fz-snow="on"] .fz-snow{display:none}}', fz.CSS)

    def test_portal_shell(self):
        sys.path[:0] = [os.path.join(REPO, "plugins", "filter"), os.path.join(REPO, "roles", "site_portal_stack", "files")]
        import site_portal as sp
        base = {"PORTAL_DATA": "/tmp", "PORTAL_REPORTS": "/tmp", "PORTAL_ACTIONS": "/tmp/x.json", "PORTAL_AAP_URL": "http://127.0.0.1:9",
                "PORTAL_AAP_TOKEN": "x", "PORTAL_TITLE": "Example site portal", "PORTAL_SEASON": "christmas"}
        body = '<div class="hero"><h1>What you can start</h1></div><h2>x</h2><h1>second</h1>'
        h = sp.shell(sp.Config(base), "Actions", body, ME, page="home")
        self.assertIn('class="fz-sky"', h)
        self.assertIn('data-k="sleigh"', h)
        self.assertIn('What you can start<svg class="fz-snowman"', h)
        self.assertEqual(h.count("fz-snowman"), 1)
        self.assertFalse(re.search(r"<script(?![^>]*\ssrc=)[^>]*>", h))                       # every script a file
        for env in ({"PORTAL_SEASON_SLEIGH": "0"}, {"PORTAL_SEASON_LIGHTS": "0"}, {"PORTAL_SEASON_STYLE": "8bit"}):
            h = sp.shell(sp.Config(dict(base, **env)), "Actions", body, ME, page="home")
            self.assertNotIn('class="fz-sky"', h, env)
            self.assertNotIn('data-k="sleigh"', h, env)
        self.assertNotIn("fz-snowman", h)                                                       # 8bit
        h = sp.shell(sp.Config(dict(base, PORTAL_SEASON="none")), "Actions", body, ME, page="home")
        self.assertNotIn("fz-", h)

    def test_the_setting_is_wired(self):
        def rd(*p):
            with open(os.path.join(REPO, *p)) as fh:
                return fh.read()
        self.assertIn("\nreport_theme_season_sleigh: true", rd("roles", "site_email", "defaults", "main.yml"))
        for f in ("main.yml", "copy_page.yml", "pages.yml"):
            self.assertIn('season_sleigh: "{{ report_theme_season_sleigh | default(true) | bool }}"', rd("roles", "site_email", "tasks", f), f)
        self.assertIn("PORTAL_SEASON_SLEIGH: \"{{ '1' if report_theme_season_sleigh | default(true) | bool else '0' }}\"",
                      rd("roles", "site_portal_stack", "tasks", "main.yml"))
        self.assertIn('g("PORTAL_SEASON_SLEIGH", "1")', rd("roles", "site_portal_stack", "files", "site_portal.py"))
        web = rd("roles", "site_email", "library", "site_report_web.py")
        self.assertIn("season_sleigh=dict(type='bool', default=True)", web)
        self.assertIn("p['season_sleigh']", web)
        self.assertIn("report_theme_season_sleigh", rd("docs", "VARIABLES_REFERENCE.md"))
        self.assertIn("report_theme_season_sleigh", rd("docs", "WEB_REPORTS.md"))
        d = tempfile.mkdtemp()
        try:
            publish(d, "christmas", season_sleigh=False)
            with open(os.path.join(d, "vm", "latest.html")) as fh:
                h = fh.read()
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertNotIn('class="fz-sky"', h)
        self.assertNotIn('data-k="sleigh"', h)
        self.assertIn("fz-snowman", h)                                                          # the snowman has no switch


PORT = int(os.environ.get("FESTIVE_PORTAL_PORT") or 18871)
PAGE_CSP_CHECK = "style-src 'self';"


@unittest.skipUnless(_browser(), "no google-chrome or chromium")
class Portal(unittest.TestCase):
    """The portal's demo (tests/site_portal/portal_demo.py) with portal_theme_season christmas: its own CSP (style-src 'self')."""

    @classmethod
    def setUpClass(cls):
        demo = os.path.join(HERE, "site_portal", "portal_demo.py")
        cls.srv = subprocess.Popen([sys.executable, demo, "atlas", str(PORT)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                                   env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PORTAL_DEMO_SEASON="christmas"))
        line = cls.srv.stdout.readline()
        assert line.startswith("READY"), line + (cls.srv.stderr.read() if cls.srv.poll() is not None else "")
        cls.c = Cdp(_browser(), 1280, 900)
        cls.c.call("Page.enable")
        cls.c.call("Page.addScriptToEvaluateOnNewDocument", source=CSP_HOOK)

    @classmethod
    def tearDownClass(cls):
        cls.c.close()
        cls.srv.terminate()
        cls.srv.wait(timeout=20)

    def open(self, path, motion="no-preference"):
        self.c.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": motion}])
        self.c.call("Page.navigate", url="http://127.0.0.1:%d/portal/%s" % (PORT, path))
        end = time.time() + 30
        while not self.c.js("document.readyState === 'complete' && !!document.body"):
            self.assertLess(time.time(), end)
            time.sleep(0.1)
        time.sleep(0.3)

    def test_jobs_page_festive_switches_and_csp(self):
        self.open("jobs")
        self.c.js("localStorage.clear()")
        self.open("jobs")
        self.assertEqual(self.c.js(PARTS_STATE), ["on", "on", "on", "on"])
        self.assertEqual(self.c.js(SHOWN + "('.fz-lights')"), 1)
        self.assertEqual(self.c.js(SHOWN + "('.fz-tree')"), 1)
        self.assertEqual(self.c.js("getComputedStyle(document.querySelector('.fz-b1')).animationName"), "fz-tw")
        self.c.js("document.querySelector('.fz-tg').click()")
        self.c.js("document.querySelector('.fz-sw[data-k=\"twinkle\"]').click()")
        self.assertEqual(self.c.js("getComputedStyle(document.querySelector('.fz-b1')).animationName"), "none")
        self.c.js("document.querySelector('.fz-sw[data-k=\"festive\"]').click()")
        self.assertEqual(self.c.js(SHOWN + "('.fz-lights')"), 0)
        self.open("")
        self.assertEqual(self.c.js(PARTS_STATE)[:3], ["off", "on", "off"])
        self.assertEqual(self.c.js("window.__csp"), [])
        self.c.js("localStorage.clear()")
        self.open("jobs", motion="reduce")
        self.assertEqual(self.c.js("getComputedStyle(document.querySelector('.fz-b1')).animationName"), "none")
        self.assertEqual(self.c.js(SHOWN + "('.fz-snow')"), 0)
        self.assertEqual(self.c.js("window.__csp"), [])

    def test_the_files_come_with_the_pages_csp(self):
        import urllib.request
        for n, body in (("festive.css", fz.CSS), ("festive.js", fz.JS)):
            r = urllib.request.urlopen("http://127.0.0.1:%d/portal/static/%s" % (PORT, n), timeout=10)
            self.assertEqual(r.read().decode("utf-8"), body)


class FakePortal(object):
    """The Jobs page's data (as the mockup drew it): fake jobs, hosts and values only."""
    actions = {}

    def token(self, cn):
        return "t"

    def job_list(self, cn, scope="mine"):
        rows = [(5121, "Linux - Patch a group of hosts", "successful", "succeeded"), (5115, "Linux - Certificate trust check", "failed", "failed"),
                (5099, "VMware - VM health report", "canceled", "other")]
        return [{"id": j, "title": t, "status": st, "group": g, "started": "2026-12-14T09:0%d:00.000000Z" % i, "elapsed": 60, "by": ME}
                for i, (j, t, st, g) in enumerate(rows)]

    def progress(self, jid, cn, via=None):
        return 200, {"done": True, "hosts": [{"host": "app0%d.corp.example.mil" % i, "state": "done"} for i in (1, 2)], "hosts_line": "2 hosts: 2 done",
                     "lines": [{"k": "changed", "host": "app01.corp.example.mil", "text": "12 packages updated"}]}

    def job(self, jid, via=None, cn=None):
        return 200, {"status": "successful", "type": "job", "action_title": "Linux - Patch a group of hosts", "started": "2026-12-14T09:00:00.000000Z",
                     "finished": "2026-12-14T09:10:00.000000Z", "elapsed": 600, "requested_by": ME, "values": {"Ticket": "XINC0000123"}, "buttons": []}


class PortalCsp(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'")
        super().end_headers()

    def log_message(self, *a):
        pass


@unittest.skipUnless(_browser(), "no headless browser here")
class PortalProbe(unittest.TestCase):
    """The portal's Jobs page (the real jobs_page and shell) with the theme on and at none: every tile, cell, chip and status mark
    styled the same, light and dark, 1280 and 800 px, no sideways scroll, no CSP violation from the theme."""

    @classmethod
    def setUpClass(cls):
        sys.path[:0] = [os.path.join(REPO, "plugins", "filter"), os.path.join(REPO, "roles", "site_portal_stack", "files")]
        import site_portal as sp
        cls.dir = tempfile.mkdtemp()
        st = os.path.join(cls.dir, "static")
        os.makedirs(st)
        for n in ("portal.css", "portal-theme.js", "portal.js"):
            shutil.copy(os.path.join(REPO, "roles", "site_portal_stack", "files", "static", n), os.path.join(st, n))
        for n, body in (("banner.css", ".banner{background:#4d4d4d;color:#fff}\n"), ("festive.css", fz.CSS), ("festive.js", fz.JS)):
            with open(os.path.join(st, n), "w") as fh:
                fh.write(body)
        for name, season in (("on", "christmas"), ("off", "none")):
            cfg = sp.Config({"PORTAL_DATA": "/tmp", "PORTAL_REPORTS": "/tmp", "PORTAL_ACTIONS": "/tmp/x.json", "PORTAL_AAP_URL": "http://127.0.0.1:9",
                             "PORTAL_AAP_TOKEN": "x", "PORTAL_TITLE": "Example site portal", "PORTAL_SEASON": season})
            with open(os.path.join(cls.dir, name + ".html"), "w") as fh:
                FakePortal.GROUPS = sp.Portal.GROUPS
                fh.write(sp.jobs_page(cfg, FakePortal(), ME, 5121).replace("/portal/static/", "static/"))
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **k: PortalCsp(*a, directory=cls.dir, **k))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.c = Cdp(_browser(), 1280, 900)
        cls.c.call("Page.enable")
        cls.c.call("Page.addScriptToEvaluateOnNewDocument", source=CSP_HOOK)

    @classmethod
    def tearDownClass(cls):
        cls.c.close()
        cls.srv.shutdown()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def load(self, name, w, scheme):
        self.c.call("Emulation.setDeviceMetricsOverride", width=w, height=900, deviceScaleFactor=1, mobile=False)
        self.c.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": scheme}])
        self.c.call("Page.navigate", url="http://127.0.0.1:%d/%s.html" % (self.srv.server_address[1], name))
        end = time.time() + 30
        while not self.c.js("document.readyState === 'complete'"):
            self.assertLess(time.time(), end)
            time.sleep(0.1)
        time.sleep(0.4)

    def test_content_unchanged_and_csp(self):
        for scheme in ("light", "dark"):
            for w in (1280, 800):
                self.load("off", w, scheme)
                ref, before = self.c.js(PROBE), self.c.js("window.__csp")
                self.load("on", w, scheme)
                self.assertEqual(self.c.js("document.documentElement.getAttribute('data-festive')"), "on")
                self.assertEqual(self.c.js(SHOWN + "('.fz-lights')"), 1)
                self.assertGreater(len(ref), 20)
                self.assertEqual(self.c.js(PROBE), ref, (scheme, w))
                self.assertEqual(self.c.js("document.documentElement.scrollWidth - document.documentElement.clientWidth"), 0, (scheme, w))
                self.assertEqual(self.c.js("window.__csp"), before, (scheme, w))


class PortalShell(unittest.TestCase):
    def cfg(self, **env):
        sys.path[:0] = [os.path.join(REPO, "plugins", "filter"), os.path.join(REPO, "roles", "site_portal_stack", "files")]
        import site_portal as sp
        base = {"PORTAL_DATA": "/tmp", "PORTAL_REPORTS": "/tmp", "PORTAL_ACTIONS": "/tmp/x.json", "PORTAL_AAP_URL": "http://127.0.0.1:9",
                "PORTAL_AAP_TOKEN": "x", "PORTAL_TITLE": "Example site portal"}
        return sp, sp.Config(dict(base, **env))

    def test_none_is_the_page_as_before_and_on_has_it(self):
        sp, off = self.cfg()
        h = sp.shell(off, "Jobs", "<p>x</p>", "DOE.JANE.Q.1234567890", page="jobs")
        self.assertNotIn("fz-", h)
        self.assertNotIn("festive", h)
        sp, on = self.cfg(PORTAL_SEASON="christmas", PORTAL_SEASON_SNOW="0")
        h = sp.shell(on, "Jobs", "<p>x</p>", "DOE.JANE.Q.1234567890", page="jobs")
        for part in ('data-season="christmas"', 'class="fz-m"', 'class="fz-lights"', 'class="fz-tree"', "static/festive.css?v=", "static/festive.js?v="):
            self.assertIn(part, h)
        self.assertNotIn('data-k="snow"', h)
        self.assertNotIn(" style=", h)
        sp, bad = self.cfg(PORTAL_SEASON="easter")
        self.assertEqual(bad.season, "none")

    def test_auto_decided_on_each_page(self):
        sp, cfg = self.cfg(PORTAL_SEASON="auto")
        real = sp.datetime

        class Fake(object):
            date = real.date
            timezone = real.timezone

            class datetime(object):
                @staticmethod
                def now(tz=None):
                    return real.datetime(2026, 12, 2, 9, 0, tzinfo=tz)
        try:
            sp.datetime = Fake
            self.assertIn('data-season-until="2027-01-06"', sp.shell(cfg, "Jobs", "", page="jobs"))
            Fake.datetime.now = staticmethod(lambda tz=None: real.datetime(2027, 1, 7, 9, 0, tzinfo=tz))
            self.assertNotIn("data-season", sp.shell(cfg, "Jobs", "", page="jobs"))
        finally:
            sp.datetime = real


if __name__ == "__main__":
    unittest.main()
