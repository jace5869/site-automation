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
                                   env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PORTAL_DEMO_LAST_TTL="2"))       # 1.79.5: the cards' last run read again soon
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
        # 1.92.0: why, in words to act on - the setting named
        self.assertEqual(self.c.js("document.querySelector('#approvals tr.locked .dwhy').textContent"),
                         "You are not in portal_approvers - only the approvers decide.")
        self.assertEqual(self.c.js("document.querySelector('#approvals tr.locked .dwhy code').textContent"), "portal_approvers")
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


    # ---- 1.79.6: nothing turns after a job is over - the Jobs page (its list and its pane) and the Actions page's cards, while the page is open
    SPIN = "document.querySelectorAll('.ic-run, .jrows .d.run').length"

    def hook(self, path):
        return json.loads(urllib.request.urlopen("http://127.0.0.1:%d/%s" % (PORT, path), timeout=20).read())

    def row(self, jid):
        return "document.querySelector('#jrows li a[href^=\"/portal/jobs/%d?\"]').closest('li')" % jid

    def test_8_a_job_that_ends_while_the_page_is_open_stops_turning(self):
        self.reset()
        self.hook("__end?job=all&status=successful")                                                             # a clean start: nothing runs
        x, y = self.hook("__launch")["id"], self.hook("__launch")["id"]
        time.sleep(1)                                                                                            # past pending
        self.open("jobs/%d" % x)                                                                                 # x open, y in the list - both run
        self.assertEqual(self.c.js("document.getElementById('jd').dataset.done"), "0")
        self.assertTrue(self.c.js(self.row(y) + ".querySelector('.d.run') !== null"))
        self.c.js("const q = document.getElementById('jq'); q.focus(); q.value = 'recheck'; q.dispatchEvent(new Event('input'))")
        # y ends first (the list follows while a row runs), then x fails (its pane and its row)
        self.hook("__end?job=%d&status=successful" % y)
        self.wait(self.row(y) + ".dataset.st === 'succeeded' && !" + self.row(y) + ".querySelector('.d.run')", "y's row stops turning", 20)
        self.assertEqual(self.c.js("document.getElementById('jq').value"), "recheck")                         # the search kept
        self.assertEqual(self.c.js("document.activeElement && document.activeElement.id"), "jq")                # and its focus: the box is never replaced
        self.assertTrue(self.c.js(self.row(x) + ".classList.contains('on')"))                                  # the open job still marked
        self.hook("__end?job=%d&status=failed" % x)
        self.wait("document.getElementById('jd').dataset.done === '1'", "x's pane says it is over", 20)
        self.wait(self.row(x) + ".dataset.st === 'failed'", "x's row", 20)
        self.assertEqual(self.c.js(self.SPIN), 0, "nothing turns once every job is over")
        # canceled and error: the same; a step AAP still lists as running is "Not finished", never a turning mark
        c, e = self.hook("__launch")["id"], self.hook("__launch")["id"]
        time.sleep(1)
        self.open("jobs/%d" % c)
        self.hook("__end?job=%d&status=canceled" % c)
        self.hook("__end?job=%d&status=error" % e)
        self.wait("document.getElementById('jd').dataset.done === '1'", "the canceled one's pane", 20)
        self.wait(self.row(e) + ".dataset.st === 'failed'", "the error one's row", 20)
        self.assertEqual(self.c.js(self.SPIN), 0)
        self.assertIn("Canceled", self.c.js("document.getElementById('jd').textContent"))

    def test_9_hidden_finished_between_looks_and_the_cards(self):
        self.reset()
        self.hook("__end?job=all&status=successful")
        a = self.hook("__launch")["id"]
        time.sleep(1)
        self.open("jobs/%d" % a)
        # the page hidden: nothing is asked; shown again: asked at once
        self.c.js("Object.defineProperty(document, 'hidden', {configurable: true, get: () => true}); document.dispatchEvent(new Event('visibilitychange'))")
        self.hook("__end?job=%d&status=successful" % a)
        time.sleep(7)
        self.assertEqual(self.c.js("document.getElementById('jd').dataset.done"), "0", "hidden: not asked")
        self.c.js("Object.defineProperty(document, 'hidden', {configurable: true, get: () => false}); document.dispatchEvent(new Event('visibilitychange'))")
        self.wait("document.getElementById('jd').dataset.done === '1'", "shown again: asked at once", 5)
        self.wait(self.SPIN + " === 0", "and nothing turns", 10)
        # a job this browser started that ends before the badges ever saw it running: its toast still comes
        b = self.hook("__launch")["id"]
        self.open("approvals")
        self.c.js("rememberRunning(%d)" % b)
        self.hook("__end?job=%d&status=successful" % b)
        self.wait("[...document.querySelectorAll('.toast')].some(t => t.textContent.indexOf('Job %d is over') >= 0)" % b, "the toast", 30)
        b2 = self.hook("__launch")["id"]                                                                         # canceled: an "Ended" toast too
        self.c.js("rememberRunning(%d)" % b2)
        self.hook("__end?job=%d&status=canceled" % b2)
        self.wait("[...document.querySelectorAll('.toast')].some(t => t.textContent.indexOf('Ended') >= 0 && t.textContent.indexOf('Job %d is over') >= 0)" % b2,
                  "the canceled one's toast", 30)
        # the Actions page: the card's Last run turns while its run is not over, and stops when it ends (the page asks again)
        k = self.hook("__launch")["id"]
        time.sleep(3)                                                                                            # past the cards' memory (PORTAL_DEMO_LAST_TTL 2 s)
        self.open("")
        card = "document.querySelector('li[data-name=\"stig_recheck\"] .meta.lastrun')"
        self.assertIn(self.c.js(card + ".dataset.st"), ("running", "pending", "waiting"))
        self.assertTrue(self.c.js(card + ".querySelector('.ic-run') !== null"))
        failed0 = int(self.c.js("document.querySelector('#ftiles a[data-show=\"failed\"] .v').textContent"))
        self.hook("__end?job=%d&status=failed" % k)
        self.wait(card + ".dataset.st === 'failed' && !" + card + ".querySelector('.ic-run')", "the card stops turning", 40)
        self.assertEqual(self.c.js(self.SPIN), 0)
        # the "Last run failed" tile and filter follow (1.79.6, Codex)
        self.assertEqual(self.c.js("document.querySelector('li[data-name=\"stig_recheck\"]').dataset.failed"), "1")
        self.assertEqual(int(self.c.js("document.querySelector('#ftiles a[data-show=\"failed\"] .v').textContent")), failed0 + 1)
        # the Details panel is a snapshot: a run that runs is drawn still (never .ic-run)
        k2 = self.hook("__launch")["id"]
        time.sleep(1)
        self.open("actions/stig_recheck/details")
        self.assertIn("Running", self.c.js("document.body.textContent"))
        self.assertEqual(self.c.js(self.SPIN), 0)
        self.hook("__end?job=%d&status=successful" % k2)
        # the stylesheet: a pane that is over never turns, even with the motion always on
        self.open("jobs/%d" % a)
        self.c.js("document.documentElement.classList.add('motion-always'); const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');"
                  "s.setAttribute('class', 'ic ic-run'); document.getElementById('jd').append(s)")
        self.assertEqual(self.c.js("getComputedStyle(document.querySelector('#jd .ic-run')).animationName"), "none")


if __name__ == "__main__":
    unittest.main(verbosity=1)
