#!/usr/bin/env python3
"""1.68.1: headless Chrome through the scorecard's go links (stigman_link_style: open) against a real STIG Manager behind
nginx with nginx-stigman-links.conf - for tests/stig/run_stigman_links_test.sh. Prints ok/FAIL lines; exit 1 on a FAIL.
  stigman_links_browser.py SETTINGS.json
SETTINGS: {"base": http://localhost:PORT (nginx with the lines), "untested": ... (the lines with another version list),
"bare": ... (no lines), "user", "password", "c", "c_none" (a collection the user has no grant in), "a", "b", "r1", "r2",
"shots": a folder for screenshots ('' = none)}"""
import base64
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from test_report_fit import _browser  # noqa: E402
from test_report_hover import Cdp  # noqa: E402

S = json.load(open(sys.argv[1]))
fails = 0


def ok(m):
    print("ok   - " + m, flush=True)


def bad(m):
    global fails
    fails += 1
    print("FAIL - " + m, flush=True)


class Browser(Cdp):
    """Cdp that keeps the page's console lines (Runtime.consoleAPICalled) instead of dropping them."""
    def __init__(self, *a):
        Cdp.__init__(self, *a)
        self.console = []

    def _frame(self):
        while True:
            m = Cdp._frame(self)
            try:
                j = json.loads(m)
            except ValueError:
                return m
            if j.get("method") == "Runtime.consoleAPICalled":
                self.console.append(" ".join(str(x.get("value", "")) for x in j["params"].get("args", [])))
                continue
            return m


def wait(b, expr, secs=90):
    end = time.time() + secs
    while time.time() < end:
        try:
            v = b.js(expr)
        except RuntimeError:
            v = None
        if v:
            return v
        time.sleep(0.5)
    return None


def go(b, url):
    b.call("Page.navigate", url=url)
    time.sleep(1)


def sign_in(b):
    """Keycloak's form when it shows (a first visit of an origin); signed in already: nothing."""
    if wait(b, "!!document.getElementById('kc-login') || (typeof Ext !== 'undefined' && !!Ext.getCmp && !!Ext.getCmp('main-tab-panel'))", 60) \
            and b.js("!!document.getElementById('kc-login')"):
        b.js("document.getElementById('username').value=%s;document.getElementById('password').value=%s;"
             "document.getElementById('kc-login').click();true" % (json.dumps(S["user"]), json.dumps(S["password"])))
        time.sleep(1)


APP_UP = ("typeof Ext !== 'undefined' && !!Ext.getCmp && !!Ext.getCmp('main-tab-panel') && typeof curUser !== 'undefined' && !!curUser"
          " && !document.getElementById('loading-mask')")
ACTIVE = "(function(){try{var t=Ext.getCmp('main-tab-panel').getActiveTab();return t?t.id:''}catch(e){return ''}})()"
# the rule selected in the active tab's grids (the review's checklist grid)
SELECTED = ("(function(){try{var t=Ext.getCmp('main-tab-panel').getActiveTab(),out=[];"
            "t.findBy(function(c){try{var r=c.getSelectionModel&&c.getSelectionModel().getSelected&&c.getSelectionModel().getSelected();"
            "if(r&&r.data&&r.data.ruleId)out.push(r.data.ruleId)}catch(e){}return false});return out}catch(e){return []}})()")
ERRBOX = ("(function(){var n=0;try{Ext.WindowMgr.each(function(w){if(w.isVisible()&&/error/i.test(w.title||''))n++})}catch(e){}"
          "return n})()")


def shot(b, name):
    if S.get("shots"):
        os.makedirs(S["shots"], exist_ok=True)
        with open(os.path.join(S["shots"], name), "wb") as fh:
            fh.write(base64.b64decode(b.call("Page.captureScreenshot", format="png")["data"]))


def link(base, **q):
    from urllib.parse import urlencode
    return base + "/portal/stigman/go?" + urlencode(q)


def opened(b, want_tab, want_rule, what, png=""):
    tab = wait(b, "(%s) === %s" % (ACTIVE, json.dumps(want_tab)), 60)
    sel = wait(b, "(%s).indexOf(%s) >= 0" % (SELECTED, json.dumps(want_rule)), 30) if tab and want_rule else None
    url = b.js("location.href")
    if tab and (sel or not want_rule) and "#" not in url and "?" not in url and not b.js(ERRBOX):
        ok(what)
        if png:
            time.sleep(1)
            shot(b, png)
    else:
        bad("%s: active tab %r, selected %r, url %s, console %s" % (what, b.js(ACTIVE), b.js(SELECTED), url, b.console[-3:]))


def stayed_home(b, what, said):
    up = wait(b, APP_UP, 90)
    time.sleep(4)
    tab, url = b.js(ACTIVE), b.js("location.href")
    heard = [c for c in b.console if said in c]
    if up and not str(tab).startswith(("reviewTab", "collection-review-tab")) and "#" not in url and not b.js(ERRBOX) \
            and (heard or not said):
        ok(what)
    else:
        bad("%s: up %r, active tab %r, url %s, console %s" % (what, up, tab, url, b.console[-4:]))


br = _browser()
b = Browser(br, 1280, 800)
try:
    b.call("Runtime.enable")
    b.call("Page.enable")
    base = S["base"]
    tab_a = "reviewTab-areview-%s-%s" % (S["a"], S["b"].replace(".", "_").replace(" ", "_"))
    tab_c = "collection-review-tab-creview-%s-%s" % (S["c"], S["b"].replace(".", "_").replace(" ", "_"))

    # 1. cold: not signed in - the link, the sign-in page, back, the asset's review at the rule
    go(b, link(base, c=S["c"], b=S["b"], a=S["a"], r=S["r1"]))
    sign_in(b)
    opened(b, tab_a, S["r1"], "a cold browser: the link, the sign-in, then the asset's review of %s at %s (the address clean)" % (S["b"], S["r1"]),
           "1-asset-review.png")
    # 2. signed in: the collection's review at another rule
    go(b, link(base, c=S["c"], b=S["b"], r=S["r2"]))
    sign_in(b)
    opened(b, tab_c, S["r2"], "signed in: the collection's review of %s at %s" % (S["b"], S["r2"]), "2-collection-review.png")
    # 3. the same asset again at another rule: the review opened afresh, the new rule selected (not the old one)
    go(b, link(base, c=S["c"], b=S["b"], a=S["a"], r=S["r2"]))
    sign_in(b)
    opened(b, tab_a, S["r2"], "the same asset again at another rule: that rule, not the one before")
    # 4. a collection the person has no grant in: the home page, no error box
    b.console[:] = []
    go(b, link(base, c=S["c_none"], b=S["b"], r=S["r1"]))
    sign_in(b)
    stayed_home(b, "a collection the person cannot read: STIG Manager's home page, no error box", "not opened")
    # 5. a value that cannot pass: the portal sends the browser straight to the plain address
    b.console[:] = []
    go(b, base + "/portal/stigman/go?c=1%20OR%201&b=" + S["b"])
    sign_in(b)
    stayed_home(b, "a link with a value that cannot pass: the plain address, the home page", "")
    # 6. the lines with another version list: nothing opened, said in the console
    b.console[:] = []
    go(b, link(S["untested"], c=S["c"], b=S["b"], a=S["a"], r=S["r1"]))
    sign_in(b)
    stayed_home(b, "a STIG Manager version not tested: the home page, said in the console only", "is not a version this was tested on")
    # 7. nginx without the lines: the link still signs in and shows the home page (never a broken sign-in)
    b.console[:] = []
    go(b, link(S["bare"], c=S["c"], b=S["b"], a=S["a"], r=S["r1"]))
    sign_in(b)
    stayed_home(b, "nginx without the lines: the home page, the sign-in works", "")
    shot(b, "3-without-the-lines-home.png")
finally:
    b.close()
print("all link scenarios passed" if not fails else "%d link scenario(s) FAILED" % fails)
sys.exit(1 if fails else 0)
