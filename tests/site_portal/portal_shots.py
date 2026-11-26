#!/usr/bin/env python3
"""Screenshots of the site portal's pages for review, by headless Chrome (tests/test_report_hover.py's Cdp), at 1280 and
800 px, light and dark: portal_shots.py BASE OUTDIR NAME=PATH [NAME=PATH ...]. BASE is a test-only server of
tests/site_portal/run_portal_test.sh (no client certificate: its nginx sets a fixed test person). Each page is opened,
given time for its script to ask the service, and shot whole. Prints one line per file; exits 1 when a page shows a
script error (the console) or overflows sideways."""
import base64
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from test_report_hover import Cdp  # noqa: E402
from test_report_fit import _browser  # noqa: E402


def shoot(c, url, out, width, dark, settle, height=0):
    """height: a screen of that size, shot as it is (1.135.0: the wall's room styles in kiosk); 0: the whole page"""
    c.call("Emulation.setDeviceMetricsOverride", width=width, height=height or 900, deviceScaleFactor=1, mobile=False)
    c.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "dark" if dark else "light"},
                                                   {"name": "prefers-reduced-motion", "value": "reduce"}])
    c.call("Page.navigate", url=url)
    end = time.time() + 30
    while c.js("document.readyState") != "complete" and time.time() < end:
        time.sleep(0.2)
    time.sleep(settle)
    errs = c.js("window.__errs || []")
    if not c.js("!!document.querySelector('.appbar') && !document.querySelector('.status.bad, .state.bad')"):
        errs = (errs or []) + ["not the portal's page, or the page says it failed: " + str(c.js("document.title"))]
    wide = c.js("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    if height:                                     # a screen: nothing may scroll either way
        tall = c.js("document.documentElement.scrollHeight - document.documentElement.clientHeight")
        if tall and tall > 0:
            errs = (errs or []) + ["the screen scrolls down by %spx" % tall]
    else:
        h = int(c.js("Math.ceil(document.documentElement.scrollHeight)") or 900)
        c.call("Emulation.setDeviceMetricsOverride", width=width, height=max(600, min(h, 6000)), deviceScaleFactor=1, mobile=False)
    time.sleep(0.3)
    png = c.call("Page.captureScreenshot", format="png", captureBeyondViewport=False)["data"]
    with open(out, "wb") as fh:
        fh.write(base64.b64decode(png))
    return errs, wide


def main():
    base, outdir, pages = sys.argv[1].rstrip("/") + "/", sys.argv[2], [a.split("=", 1) for a in sys.argv[3:]]
    os.makedirs(outdir, exist_ok=True)
    c = Cdp(_browser(), 1280, 900)
    bad = 0
    try:
        c.call("Page.enable")
        c.call("Security.setIgnoreCertificateErrors", ignore=True)        # the test server's certificate
        # script errors kept for the check (CSP violations and exceptions both reach window.onerror or the console)
        c.call("Page.addScriptToEvaluateOnNewDocument", source=(
            "window.__errs=[];addEventListener('error',e=>__errs.push(String(e.message)));"
            "addEventListener('securitypolicyviolation',e=>__errs.push('CSP '+e.violatedDirective+' '+e.blockedURI));"))
        for name, path in pages:
            sizes = [(1280, 0), (800, 0)]
            if "@" in path:                                  # 1.135.0: PATH@WxH - one screen size, as it is
                path, wh = path.rsplit("@", 1)
                sizes = [tuple(int(x) for x in wh.split("x"))]
            for width, height in sizes:
                for dark in (False, True):
                    f = os.path.join(outdir, "%s-%d%s-%s.png" % (name, width, ("x%d" % height) if height else "", "dark" if dark else "light"))
                    errs, wide = shoot(c, base + path, f, width, dark, 3.5 if name.startswith(("job", "wall")) else 2.0, height)
                    look = c.js("document.documentElement.getAttribute('data-skin') || ''")
                    ok = not errs and (wide or 0) <= 0
                    bad += 0 if ok else 1
                    print(json.dumps({"file": f, "errors": errs, "overflow_px": wide, "look": look, "ok": ok}))
    finally:
        c.close()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
