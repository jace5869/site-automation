"""1.68.1: the two scripts of the STIG scorecard links that open the review (stigman_link_style: open), run under node with
a stand-in browser and a stand-in STIG Manager client (Ext, addReview, addCollectionReview): the site portal's go page
keeps the target in the tab and goes on to STIG Manager's plain address; the script nginx adds to STIG Manager's page
opens the review once STIG Manager is up - only on the versions tested, only when the person can read it and the STIG is
there - and on anything else does nothing but one console line. tests/stig/run_stigman_links_test.sh does the same in
headless Chrome against the real STIG Manager.
  python3 -m unittest tests.test_stigman_links_js        (skipped without node)"""
import json
import os
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "roles", "site_portal_stack", "files", "static")
NODE = shutil.which("node")

# a stand-in browser and STIG Manager; CASE (JSON) says what the run is
HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const CASE = JSON.parse(process.argv[2]);
const out = {stored: null, replaced: null, said: [], calls: [], removed: []};
const store = Object.assign({}, CASE.storage || {});
const sessionStorage = {getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); out.stored = store[k] },
                        removeItem: k => { delete store[k] }};
const metas = CASE.metas || {};
const document = {
  querySelector: sel => { const m = /name="([^"]+)"/.exec(sel); return m && m[1] in metas ? {getAttribute: () => metas[m[1]]} : null },
  getElementById: id => (id === "loading-mask" && CASE.loading ? {} : null),
  currentScript: {getAttribute: n => (n === "data-versions" ? (CASE.versions || "") : null)}
};
const location = {replace: u => { out.replaced = u }};
const console = {info: m => out.said.push(m), log: () => {}, warn: () => {}, error: () => {}};
const tabs = {getItem: id => (CASE.open_tab === id ? {id} : null), remove: (t) => out.removed.push(t.id)};
const answers = CASE.answers || {};
const Ext = {
  getCmp: id => (id === "main-tab-panel" ? tabs : null),
  WindowMgr: {each: f => f({title: "What's New", close: () => out.calls.push("closed What's New")})},
  Ajax: {requestPromise: o => { out.calls.push("GET " + o.url); const a = answers[o.url];
    return a === undefined || a === 403 ? Promise.reject(new Error("HTTP 403 " + o.url)) : Promise.resolve(a) }}
};
const ctx = {document, location, sessionStorage, console, Ext, setInterval, clearInterval, Promise, JSON, Date, String, Number,
             encodeURIComponent, window: null, curUser: CASE.signed_in === false ? undefined : {username: "admin"},
             addReview: p => { out.calls.push(["addReview", p]); return Promise.resolve() },
             addCollectionReview: p => { out.calls.push(["addCollectionReview", p]); return Promise.resolve() }};
ctx.window = ctx;
vm.createContext(ctx);
if (CASE.stigman !== false) vm.runInContext("const STIGMAN = " + JSON.stringify({Env: {version: CASE.version || "1.6.18+3", apiBase: "api"},
                                                                                apiConfig: {ok: 1}}) + ";", ctx);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
setTimeout(() => { out.left = store; console.log(""); process.stdout.write(JSON.stringify(out), () => process.exit(0)) }, CASE.wait || 1500);
"""


@unittest.skipUnless(NODE, "no node here")
class Js(unittest.TestCase):
    def run_js(self, name, case):
        r = subprocess.run([NODE, "-e", HARNESS, "--", os.path.join(STATIC, name), json.dumps(case)],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    # ---- the go page (stigman-go.js)
    def test_go_keeps_the_target_and_goes_to_the_plain_address(self):
        o = self.run_js("stigman-go.js", {"metas": {"sa-stigman-target": json.dumps({"c": "2", "b": "RHEL_9_STIG", "a": "11"}),
                                                    "sa-stigman-to": "/"}, "wait": 50})
        self.assertEqual(o["replaced"], "/")
        t = json.loads(o["stored"])
        self.assertEqual((t["c"], t["b"], t["a"]), ("2", "RHEL_9_STIG", "11"))
        self.assertGreater(t["at"], 0)

    def test_go_never_leaves_the_site(self):
        for to in ("//evil.example.mil/", "https://evil.example.mil/", "javascript:alert(1)", "", "/\\evil.example.mil/",
                   "/\\\\evil.example.mil", "/a\\b", "/a b", "/a\nb"):    # 1.68.1b: a browser reads /\x as //x
            self.assertEqual(self.run_js("stigman-go.js", {"metas": {"sa-stigman-to": to}, "wait": 50})["replaced"], "/", to)

    def test_go_without_a_target_still_goes_on(self):
        o = self.run_js("stigman-go.js", {"metas": {"sa-stigman-to": "/stigman/"}, "wait": 50})
        self.assertEqual((o["replaced"], o["stored"]), ("/stigman/", None))

    # ---- STIG Manager's page (stigman-open.js)
    def target(self, **k):
        import time
        t = dict({"c": "2", "b": "RHEL_9_STIG", "r": "SV-257985r1069364_rule", "at": int(time.time() * 1000)}, **k)
        return {"sa_stigman_open": json.dumps(t)}

    ASSET = {"name": "rhel04", "labelIds": ["7"], "collection": {"collectionId": "2"}, "stigs": [{"benchmarkId": "RHEL_9_STIG"}]}

    def test_an_asset_review_at_the_rule(self):
        o = self.run_js("stigman-open.js", {"storage": self.target(a="11"), "versions": "1.6.16 1.6.17 1.6.18",
                                            "answers": {"api/assets/11?projection=stigs": self.ASSET},
                                            "open_tab": "reviewTab-areview-11-RHEL_9_STIG"})
        call = [c for c in o["calls"] if isinstance(c, list)]
        self.assertEqual(len(call), 1, o)
        self.assertEqual(call[0][0], "addReview")
        self.assertEqual(call[0][1], {"leaf": {"collectionId": "2", "benchmarkId": "RHEL_9_STIG", "revisionStr": "latest",
                                               "stigName": "RHEL_9_STIG", "assetId": "11", "assetName": "rhel04", "assetLabelIds": ["7"]},
                                      "selectedRule": "SV-257985r1069364_rule"})
        self.assertEqual(o["removed"], ["reviewTab-areview-11-RHEL_9_STIG"])        # an open review: opened afresh
        self.assertIn("closed What's New", o["calls"])
        self.assertEqual(o["left"], {})                                            # once
        self.assertIn("[site automation link] opened the asset review of RHEL_9_STIG", o["said"])

    def test_a_collection_review_at_the_rule(self):
        o = self.run_js("stigman-open.js", {"storage": self.target(b="VMware Aria Operations 8.x Application"), "versions": "1.6.16 1.6.18",
                                            "version": "1.6.16",
                                            "answers": {"api/collections/2/stigs": [{"benchmarkId": "VMware Aria Operations 8.x Application"}]}})
        call = [c for c in o["calls"] if isinstance(c, list)]
        self.assertEqual([c[0] for c in call], ["addCollectionReview"])
        self.assertEqual(call[0][1]["leaf"]["benchmarkId"], "VMware Aria Operations 8.x Application")
        self.assertEqual(call[0][1]["selectedRule"], "SV-257985r1069364_rule")

    def nothing(self, case, why):
        o = self.run_js("stigman-open.js", case)
        self.assertFalse([c for c in o["calls"] if isinstance(c, list)], o)
        self.assertTrue(any(why in s for s in o["said"]), o["said"])
        return o

    def test_an_untested_version_does_nothing(self):
        o = self.nothing({"storage": self.target(a="11"), "versions": "1.6.16 1.6.18", "version": "1.7.0+1",
                          "answers": {"api/assets/11?projection=stigs": self.ASSET}}, "STIG Manager 1.7.0 is not a version this was tested on")
        self.assertEqual(o["calls"], [])                                           # not one request either

    def test_no_access_does_nothing(self):
        self.nothing({"storage": self.target(a="11"), "versions": "1.6.18", "answers": {}}, "not opened: HTTP 403")

    def test_an_asset_of_another_collection_or_without_the_stig_does_nothing(self):
        self.nothing({"storage": self.target(a="11", c="3"), "versions": "1.6.18",
                      "answers": {"api/assets/11?projection=stigs": self.ASSET}}, "not in that collection")
        self.nothing({"storage": self.target(b="MS_Windows_Server_2022_STIG"), "versions": "1.6.18",
                      "answers": {"api/collections/2/stigs": [{"benchmarkId": "RHEL_9_STIG"}]}}, "is not there")

    def test_an_old_or_bad_target_does_nothing_and_is_dropped(self):
        o = self.nothing({"storage": self.target(at=1), "versions": "1.6.18"}, "older than 10 minutes")
        self.assertEqual(o["left"], {})
        for bad in ({"c": "2 OR 1"}, {"b": "<script>"}, {"a": "x"}, {"r": "a b"}):
            o = self.nothing({"storage": self.target(**bad), "versions": "1.6.18"}, "not a link this script opens")
            self.assertEqual(o["left"], {}, bad)

    def test_before_the_sign_in_it_waits_and_keeps_the_target(self):
        """The first load goes to the sign-in page: the app never comes up there, the target stays for the load after it."""
        o = self.run_js("stigman-open.js", {"storage": self.target(a="11"), "versions": "1.6.18", "signed_in": False, "wait": 1300})
        self.assertEqual((o["calls"], list(o["left"])), ([], ["sa_stigman_open"]))

    def test_no_target_no_work(self):
        o = self.run_js("stigman-open.js", {"storage": {}, "versions": "1.6.18", "wait": 700})
        self.assertEqual((o["calls"], o["said"]), ([], []))

    def test_the_scripts_are_plain_ascii_without_inline_handlers(self):
        for name in ("stigman-go.js", "stigman-open.js"):
            with open(os.path.join(STATIC, name), "rb") as fh:
                b = fh.read()
            b.decode("ascii")
            self.assertNotIn(b"eval(", b)
            self.assertNotIn(b"innerHTML", b)


if __name__ == "__main__":
    unittest.main()
