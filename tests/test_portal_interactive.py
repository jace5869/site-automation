#!/usr/bin/env python3
"""1.78.4: the portal's interactive side, in a real browser on the demo (tests/site_portal/portal_demo.py: the real service, a stand-in AAP): pins and folded groups kept in
the browser, the keyboard, the badges and toasts, the approvals' confirm step - and the same pages without scripts. Skips without Chrome or Chromium.
  python tests/test_portal_interactive.py"""
import json
import os
import re
import subprocess
import sys
import time
import unittest
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from test_report_hover import Cdp, _browser  # noqa: E402

DEMO = os.path.join(HERE, "site_portal", "portal_demo.py")
PORT = int(os.environ.get("PORTAL_INTERACTIVE_PORT") or 18860)


@unittest.skipUnless(_browser(), "no google-chrome or chromium")
class Interactive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = subprocess.Popen([sys.executable, DEMO, "ops", str(PORT)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                                   env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        line = cls.srv.stdout.readline()
        assert line.startswith("READY"), line + (cls.srv.stderr.read() if cls.srv.poll() is not None else "")
        cls.ids = json.loads(line[len("READY "):])
        cls.c = Cdp(_browser(), 1280, 1000)
        cls.c.call("Page.enable")
        cls.c.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])

    @classmethod
    def tearDownClass(cls):
        cls.c.close()
        cls.srv.terminate()
        cls.srv.wait(timeout=20)

    def open(self, path):
        self.c.call("Page.navigate", url="http://127.0.0.1:%d/portal/%s" % (PORT, path))
        end = time.time() + 30
        while not self.c.js("document.readyState === 'complete' && !!document.body && document.documentElement.classList.contains('js-on')"):
            self.assertLess(time.time(), end, "the page did not load")
            time.sleep(0.1)

    def wait(self, expr, what="", secs=15):
        end = time.time() + secs
        while not self.c.js(expr):
            self.assertLess(time.time(), end, what or expr)
            time.sleep(0.1)

    def reset(self):
        self.c.call("Page.navigate", url="http://127.0.0.1:%d/portal/" % PORT)
        time.sleep(0.5)
        self.c.js("localStorage.clear()")

    # ---- pins and folded groups
    def test_1_a_pin_moves_the_card_to_the_top_and_is_kept(self):
        self.reset()
        self.open("")
        first = self.c.js("document.querySelector('#agrid .grp:not(.pinned) li[data-name]').dataset.name")
        self.assertFalse(self.c.js("document.querySelector('#agrid .pinned').hidden === false"))                # nothing pinned: the group is away
        self.c.js("document.querySelector('li[data-name=\"%s\"] .pin').click()" % first)
        self.assertEqual(self.c.js("document.querySelector('#agrid .pinned li[data-name]').dataset.name"), first)
        self.assertFalse(self.c.js("document.querySelector('#agrid .pinned').hidden"))
        self.assertEqual(self.c.js("document.querySelector('#agrid .pinned h2 .count').textContent"), "1")
        self.assertEqual(self.c.js("document.querySelector('li[data-name=\"%s\"] .pin').getAttribute('aria-pressed')" % first), "true")
        self.open("")                                                                                            # a new visit: still pinned
        self.assertEqual(self.c.js("document.querySelector('#agrid .pinned li[data-name]').dataset.name"), first)
        self.c.js("document.querySelector('li[data-name=\"%s\"] .pin').click()" % first)                      # unpin: back in its group
        self.assertTrue(self.c.js("document.querySelector('#agrid .pinned').hidden"))
        self.assertTrue(self.c.js("!!document.querySelector('#agrid .grp:not(.pinned) li[data-name=\"%s\"]')" % first))

    def test_2_a_group_folds_and_the_search_peeks(self):
        self.reset()
        self.open("")
        area = self.c.js("document.querySelector('#agrid .grp:not(.pinned)').dataset.area")
        self.c.js("document.querySelector('#agrid .grp[data-area=\"%s\"] h2').click()" % area)
        self.assertTrue(self.c.js("getComputedStyle(document.querySelector('#agrid .grp[data-area=\"%s\"] ul')).display === 'none'" % area))
        self.assertEqual(self.c.js("document.querySelector('#agrid .grp[data-area=\"%s\"] h2').getAttribute('aria-expanded')" % area), "false")
        self.open("")                                                                                            # kept
        self.assertTrue(self.c.js("getComputedStyle(document.querySelector('#agrid .grp[data-area=\"%s\"] ul')).display === 'none'" % area))
        self.c.js("(() => {const q = document.getElementById('aq'); q.value = 'a'; q.dispatchEvent(new Event('input', {bubbles: true}))})()")      # a search shows what matches
        self.assertTrue(self.c.js("getComputedStyle(document.querySelector('#agrid .grp[data-area=\"%s\"] ul')).display !== 'none'" % area))
        self.c.js("document.querySelector('#agrid .grp[data-area=\"%s\"] h2').dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}))" % area)

    # ---- the keyboard
    def test_3_the_keyboard(self):
        self.open("")
        self.c.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: '?', bubbles: true}))")
        self.assertTrue(self.c.js("!!document.querySelector('.keys') && document.querySelector('.keys').textContent.includes('Go to Approvals')"))
        self.c.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))")
        self.assertFalse(self.c.js("!!document.querySelector('.keys')"))
        self.c.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'g', bubbles: true})); document.dispatchEvent(new KeyboardEvent('keydown', {key: 'j', bubbles: true}))")
        self.wait("location.pathname === '/portal/jobs'", "g j goes to Jobs")
        self.open("")
        self.c.js("(() => {const q = document.getElementById('aq'); q.focus(); q.dispatchEvent(new KeyboardEvent('keydown', {key: '?', bubbles: true}))})()")     # typing in a field is left alone
        self.assertFalse(self.c.js("!!document.querySelector('.keys')"))

    # ---- badges and toasts
    def test_4_the_badges_and_a_toast_when_a_job_ends(self):
        self.reset()
        done = self.ids["clean"]                                                                                 # a finished job: seen as running before, so it ended
        self.c.js("localStorage.setItem('site-portal-jobs', JSON.stringify({%d: 'running'}))" % done)
        self.open("jobs")
        self.wait("!!document.querySelector('.toast')", "a toast came")
        t = self.c.js("document.querySelector('.toast').textContent")
        self.assertIn("Job %d is over" % done, t)
        self.assertTrue(self.c.js("document.querySelector('.toast a').getAttribute('href') === '/portal/jobs/%d'" % done))
        self.assertTrue(self.c.js("[...document.querySelectorAll('.nav a')].some(a => /jobs$/.test(a.getAttribute('href')) && a.querySelector('.bdg') && !a.querySelector('.bdg').hidden)"))   # a job runs: the badge
        self.open("jobs")                                                                                        # once: not again
        time.sleep(1.5)
        self.assertFalse(self.c.js("!!document.querySelector('.toast')"))

    # ---- the approvals page
    def test_5_an_approval_asks_once_more_in_place(self):
        self.open("approvals?__who=appr")
        self.wait("document.querySelectorAll('#approvals tbody tr').length > 0", "the approvals were drawn")
        self.assertTrue(self.c.js("!![...document.querySelectorAll('#approvals button')].find(b => b.textContent === 'Approve')"))
        self.c.js("[...document.querySelectorAll('#approvals button')].find(b => b.textContent === 'Approve').click()")
        self.assertTrue(self.c.js("!![...document.querySelectorAll('#approvals button')].find(b => b.textContent === 'Yes, approve')"))   # the one more question
        self.assertTrue(self.c.js("!![...document.querySelectorAll('#approvals button')].find(b => b.textContent === 'Back')"))
        self.c.js("[...document.querySelectorAll('#approvals button')].find(b => b.textContent === 'Back').click()")
        self.assertTrue(self.c.js("!![...document.querySelectorAll('#approvals button')].find(b => b.textContent === 'Approve')"))        # Back: nothing was decided
        self.assertEqual(self.c.js("document.querySelectorAll('#approvals tr.locked').length"), 0)

    def test_6_a_person_who_cannot_decide_has_a_grey_row(self):
        self.open("approvals")
        self.wait("document.querySelectorAll('#approvals tbody tr').length > 0", "the approvals were drawn")
        self.assertGreater(self.c.js("document.querySelectorAll('#approvals tr.locked').length"), 0)
        self.assertIn("decide it in AAP", self.c.js("document.querySelector('#approvals tr.locked .lockline').textContent"))
        self.assertEqual(self.c.js("document.querySelectorAll('#approvals button').length"), 0)

    # ---- without scripts: the pages are what they were
    def test_7_without_scripts_the_pages_work(self):
        def get(path):
            return urllib.request.urlopen("http://127.0.0.1:%d/portal/%s" % (PORT, path), timeout=20).read().decode()
        home = get("")
        self.assertNotIn('class="pin"', home)                                                                    # the stars and the Pinned group are the script's
        self.assertNotIn("bdg", home)
        self.assertGreaterEqual(len(re.findall(r'<section class="grp"', home)), 3)                           # every group, open, with its cards
        self.assertIn('data-run', home)
        job = get("jobs/%d" % self.ids["clean"])
        self.assertIn("data-again", job)                                                                         # Run again is a plain link
        self.assertRegex(job, r'<a class="btn pri" href="/portal/actions/disk_cleanup\?[^"]*" data-again')
        self.assertIn('id="approvals"', get("approvals?__who=appr"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
