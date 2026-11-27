#!/usr/bin/env python3
"""1.75.2: the portal's pages fit their window - in the ops, atlas and brief looks, light and dark, at 1280 and 800 px:
no page scrolls sideways, no card, table, tile, output box, field or bar is wider than its box (a long finding id, a long output
line, the approvals table, the refused page, and the pick steps: the host, the findings, several hosts, nothing found). The portal runs on the real actions.yml with the stand-in AAP
(tests/site_portal/portal_demo.py); the pages are measured in frames of one page, in headless Chrome.
  python tests/test_portal_fit.py          (skips without google-chrome or chromium)"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unittest
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(REPO, "tests", "site_portal", "portal_demo.py")
PORT = int(os.environ.get("PORTAL_FIT_PORT") or 18830)


def browser():
    for b in ("google-chrome", "chromium", "chromium-browser"):
        p = shutil.which(b)
        if p:
            return p
    return None


@unittest.skipUnless(browser(), "no google-chrome or chromium")
class Fit(unittest.TestCase):
    def run_look(self, look, port, *flags, skin=""):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        srv = subprocess.Popen([sys.executable, DEMO, look, str(port)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, env=env)
        try:
            line = srv.stdout.readline()
            self.assertTrue(line.startswith("READY"), line + srv.stderr.read() if srv.poll() is not None else line)
            out = subprocess.run([browser(), "--headless=new", "--no-sandbox", "--disable-gpu", "--dump-dom", "--virtual-time-budget=90000"] + list(flags) + [
                                  "http://127.0.0.1:%d/__fit?w=1280,800%s" % (port, ("&skin=" + skin) if skin else "")], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 universal_newlines=True, timeout=400).stdout
            m = re.search(r"FIT(\[.*\])END", out, re.S)
            self.assertTrue(m, "the fit page did not finish: " + out[-300:])
            return json.loads(m.group(1).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
        finally:
            srv.terminate()
            srv.wait(timeout=20)

    def test_every_page_fits_in_every_look(self):
        for i, look in enumerate(("ops", "atlas", "brief")):
            rows = self.run_look(look, PORT + i)
            self.assertEqual(len(rows), 21 * 2 * 2, look)                         # twenty-one pages (1.92.0: a job waiting with its plan, twice), light and dark, two widths
            for r in rows:
                self.assertEqual(r["look"], look, r)
                self.assertTrue(r["h1"], r)                                       # the page drew (its script ran, its heading is there)
                self.assertEqual(r["bad"], [], "%s %s %s %dpx" % (look, r["page"], r["theme"], r["width"]))
            self.assertEqual(sorted(set(r["bg"].split()[0] for r in rows if r["theme"] == "dark")), ["dark"], look)
            self.assertEqual(sorted(set(r["bg"].split()[0] for r in rows if r["theme"] == "light")), ["light"], look)
            # 1.78.2: what is running turns - the icon (chip, host chips, Now line), the jobs list's ring, the progress bar's stripe
            for r in rows:
                if r["page"] == "job-running":
                    m = r["motion"]
                    self.assertGreater(m["icons"], 0, (look, r["theme"]))
                    self.assertEqual((m["icon"], m["ring"], m["now"]), ("spin", "spin", True), (look, r["theme"], r["width"]))
                    self.assertTrue(m["pbvalue"] is not None, (look, r["theme"]))                       # the bar is there (its stripe is a pseudo-element: the CSS test reads its rules)
                else:
                    self.assertTrue(r["motion"]["icon"] in ("", "spin"), r["page"])

    def test_every_page_fits_in_every_portal_look(self):
        """1.135.0: the portal looks (portal_look) over the palette: every page, light and dark, 1280 and 800 px - nothing overflows"""
        for i, skin in enumerate(("metro-atlas", "metro-calm", "metro-wall", "atlas-clean", "atlas-night", "contour-lite",
                                  "strata", "metro", "relay", "atlas")):
            rows = self.run_look("atlas", PORT + 10 + i, skin=skin)
            self.assertEqual(len(rows), 21 * 2 * 2, skin)
            for r in rows:
                self.assertEqual(r["skin"], skin, r)
                self.assertTrue(r["h1"], r)
                self.assertEqual(r["bad"], [], "%s %s %s %dpx" % (skin, r["page"], r["theme"], r["width"]))
            self.assertEqual(sorted(set(r["bg"].split()[0] for r in rows if r["theme"] == "dark")), ["dark"], skin)
            self.assertEqual(sorted(set(r["bg"].split()[0] for r in rows if r["theme"] == "light")), ["light"], skin)

    def test_nothing_moves_under_reduced_motion(self):
        rows = self.run_look("ops", PORT + 5, "--force-prefers-reduced-motion")
        running = [r for r in rows if r["page"] == "job-running"]
        self.assertTrue(running)
        for r in running:
            self.assertGreater(r["motion"]["icons"], 0)
            self.assertEqual((r["motion"]["icon"], r["motion"]["ring"]), ("none", "none"), r["theme"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
