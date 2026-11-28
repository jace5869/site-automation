#!/usr/bin/env python3
"""1.135.0: the portal's looks (portal_look) and the wall's new styles (portal_wall_style: strata, relay, metro), the Actions page's
Ops center section (/api/wall/summary) and Share to Teams. Placeholders only. Run with python3 tests/test_portal_looks.py."""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ROLE = os.path.join(ROOT, "roles", "site_portal_stack")
FILES = os.path.join(ROLE, "files")
STATIC = os.path.join(FILES, "static")
for p in (FILES, os.path.join(ROOT, "plugins", "module_utils"), os.path.join(ROOT, "plugins", "filter"), os.path.join(ROOT, "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import site_portal as sp  # noqa: E402
import wall_model as M  # noqa: E402
import gen_portal_looks as G  # noqa: E402


def read(*p):
    with open(os.path.join(*p), encoding="utf-8") as fh:
        return fh.read()


class Settings(unittest.TestCase):
    def cfg(self, **env):
        return sp.Config(dict({"PORTAL_DATA": "/tmp/sa-looks-test", "PORTAL_REPORTS": "/tmp", "PORTAL_ACTIONS": "/nonexistent",
                               "PORTAL_AAP_URL": "http://127.0.0.1:9", "PORTAL_AAP_TOKEN": "x"}, **env))

    def test_portal_look(self):
        self.assertEqual(self.cfg().skin, "metro-atlas")                                               # the default
        for v, want in (("metro", "metro"), (" Relay ", "relay"), ("PRESS", "metro-atlas"), ("tower", "metro-atlas"),
                        ("chroma", "metro-atlas"), ("violet", "metro-atlas"),
                        ("bento", "metro-atlas"), ("", "metro-atlas"), ('x"><b>', "metro-atlas")):
            self.assertEqual(self.cfg(PORTAL_LOOK=v).skin, want, v)
        self.assertEqual(sp.SKIN_KEYS, ("metro-atlas", "metro-calm", "metro-wall", "atlas-clean", "atlas-night", "contour-lite",
                                        "strata", "metro", "relay", "atlas"))

    def test_unavailable_look_warns_once_and_uses_default(self):
        for key, attr, default in (("PORTAL_LOOK", "skin", sp.DEFAULT_SKIN), ("PORTAL_THEME", "look", "atlas")):
            for name in ("chroma", "press", "tower", "unknown"):
                with self.assertLogs(sp.__name__, level="WARNING") as log:
                    self.assertEqual(getattr(self.cfg(**{key: name}), attr), default)
                self.assertEqual(len(log.records), 1)
                self.assertIn(default, log.output[0])

    @unittest.skipUnless(shutil.which("node"), "no node")
    def test_removed_browser_look_warns_once(self):
        script = r"""
        const vm = require("vm"), fs = require("fs"), assert = require("assert");
        const warnings = [], saved = new Map([["site-portal-look", "chroma"]]);
        const root = {getAttribute: () => "metro-atlas", setAttribute: () => assert.fail("unexpected override"),
                      classList: {toggle() {}, contains() {return false;}}};
        const context = {document: {documentElement: root, addEventListener() {}}, window: {}, location: {search: ""},
                         localStorage: {getItem: k => saved.get(k), removeItem: k => saved.delete(k)},
                         console: {warn: (...args) => warnings.push(args)}, URLSearchParams};
        const script = fs.readFileSync(process.argv[1], "utf8");
        vm.runInNewContext(script, context); vm.runInNewContext(script, context);
        assert.deepEqual(warnings.map(w => w.slice(1)), [["chroma", "metro-atlas"]]);
        """
        subprocess.run(["node", "-e", script, os.path.join(STATIC, "portal-theme.js")], check=True)

    def test_report_look_in_publish_and_copy(self):
        import yaml
        from jinja2 import Environment
        from report_page_filters import report_web_look
        env = Environment()
        env.filters.update(report_web_look=report_web_look, bool=bool)
        for file in ("main.yml", "copy.yml"):
            tasks = yaml.safe_load(read(ROOT, "roles", "site_email", "tasks", file))
            setting = next(t["ansible.builtin.set_fact"]["_web_theme"] for t in tasks
                           if "_web_theme" in t.get("ansible.builtin.set_fact", {}))
            warning, = [t for t in tasks if "warn when the configured report look" in t["name"]]
            for name in ("chroma", "press", "tower", "unknown", " Glass ", "ops", "standard"):
                look = env.from_string(setting).render(report_web_theme=name)
                invalid = name in ("chroma", "press", "tower", "unknown")
                self.assertEqual(look, "atlas" if invalid else name.strip().lower())
                self.assertEqual(env.compile_expression(warning["when"])(_web_on=True, report_web_theme=name, _web_theme=look), invalid)

    def test_deploy_access_warning(self):
        import yaml
        from jinja2 import Environment
        tasks = yaml.safe_load(read(ROLE, "tasks", "main.yml"))
        fact = next(t["ansible.builtin.set_fact"]["_portal_access_warning"] for t in tasks
                    if "_portal_access_warning" in t.get("ansible.builtin.set_fact", {}))
        grants = dict.fromkeys(("portal_admins", "portal_admin_groups", "portal_runners", "portal_run_groups"), [])
        render = Environment().from_string(fact).render
        self.assertIn("Set portal_admins", render(grants))
        for key in grants:
            self.assertEqual(render(dict(grants, **{key: ["SITEA"]})), "")
        self.assertTrue(any(t.get("ansible.builtin.debug", {}).get("msg") == "{{ _portal_access_warning }}" for t in tasks))
        self.assertIn("_portal_access_warning", tasks[-1]["vars"]["_head"])

    def test_portal_wall_style(self):
        self.assertEqual(self.cfg().wall_style, "cockpit")                                        # unchanged for current users
        for v, want in (("strata", "strata"), ("RELAY", "relay"), ("metro", "metro"), ("map", "map"), ("lights", "lights"), ("tv", "cockpit"), ("", "cockpit")):
            self.assertEqual(self.cfg(PORTAL_WALL_STYLE=v).wall_style, want, v)

    def test_the_bar_has_the_look_picker(self):
        page = sp.shell(self.cfg(PORTAL_LOOK="relay"), "T", "<p>x</p>")
        self.assertIn('data-skin="relay" data-skin-default="relay"', page)
        self.assertIn('<select id="look" aria-label="Look">', page)
        self.assertEqual(re.findall(r'<option value="([a-z-]+)"', page), list(sp.SKIN_KEYS))
        self.assertRegex(page, r'<option value="relay" title="[^"]+" selected>Relay Panel</option>')
        self.assertRegex(page, r'<link rel="stylesheet" href="/portal/static/portal-looks\.css\?v=[0-9a-f]{10}">')
        self.assertNotIn("style=", page)
        self.assertNotRegex(page, r"<script>(?!</script>)")                                        # no inline script
        self.assertNotRegex(page, r"\son[a-z]+=")                                                  # no inline handler

    def test_classic_wall_shares_the_portal_looks(self):
        # 1.136.1: check the rendered picker and the assets that apply the saved browser choice.
        for configured, expected in (("metro-wall", "metro-wall"), ("press", "metro-atlas"), ("unknown", "metro-atlas")):
            with self.subTest(configured=configured):
                cfg = self.cfg(PORTAL_LOOK=configured)
                self.assertFalse(cfg.wall_fleet)
                page = sp.wall_page(cfg, "Viewer", {})
                self.assertIn('data-skin="%s" data-skin-default="%s"' % (expected, expected), page)
                self.assertEqual(re.findall(r'<option value="([a-z-]+)"', page), list(sp.SKIN_KEYS))
                self.assertRegex(page, r'<option value="%s"[^>]* selected>' % expected)
                for name in ("portal-theme.js", "portal-looks.css"):
                    self.assertIn("static/%s?v=%s" % (name, sp.static_version(name)), page)

    def test_the_wall_page_carries_its_default_style_and_files(self):
        cfg = self.cfg(PORTAL_WALL_STYLE="metro")
        page = sp.wall_page(cfg, "DOE.JANE", {"wall-styles.js": "a" * 10, "wall-share.js": "b" * 10, "wall-styles.css": "c" * 10})
        self.assertIn('data-style="metro"', page)
        for n in ("wall-share.js", "wall-styles.js", "wall-page.js", "wall-styles.css"):
            self.assertIn("static/" + n, page)
        self.assertLess(page.index("wall-styles.js"), page.index("wall-page.js"))                 # the styles are there before the page draws
        for n in ("wall-styles.js", "wall-styles.css", "wall-share.js"):
            self.assertIn(n, sp.WALL_STATIC)

    def test_the_actions_page_section(self):
        self.assertEqual(sp.ops_section(self.cfg(PORTAL_WALL="0")), "")
        s = sp.ops_section(self.cfg(PORTAL_WALL_TITLE='Ops <b>wall</b>'))
        self.assertIn('data-src="/portal/api/wall/summary"', s)
        self.assertIn("Ops &lt;b&gt;wall&lt;/b&gt;", s)
        self.assertIn(" hidden>", s)                                                               # nothing shown until its answer

    def test_the_deploy_checks_both_settings(self):
        y = read(ROLE, "tasks", "main.yml")
        m = re.search(r"portal_wall_style \| default\('cockpit'\) \| string \| lower \| trim\) in \[(.*?)\]", y)
        self.assertEqual(tuple(re.findall(r"'([a-z]+)'", m.group(1))), sp.WALL_STYLES)
        self.assertIn('PORTAL_LOOK: "{{ portal_look', y)
        self.assertIn('PORTAL_WALL_STYLE: "{{ portal_wall_style', y)
        self.assertIn("src: static/fonts/", y)
        d = read(ROLE, "defaults", "main.yml")
        # 1.136.1: inventory overrides reach the service alongside the shared look.
        self.assertRegex(d, r"(?m)^portal_actions_ops_center: line(?: +#.*)?$")
        self.assertIn('PORTAL_ACTIONS_OPS_CENTER: "{{ portal_actions_ops_center | default(\'line\') | string | lower | trim }}"', y)
        self.assertIn("\nportal_look: metro-atlas\n", d)
        self.assertIn("\nportal_wall_style: cockpit\n", d)
        for n in ("wall-styles.js", "wall-styles.css", "wall-share.js", "portal-looks.css", "portal-looks.js"):
            self.assertIn("{src: static/%s, dest: static/%s}" % (n, n), y)

    def test_the_refusal_page_needs_only_what_a_refused_reader_may_fetch(self):
        """review r1 (Codex 16): the reader-refusal page links the base stylesheet only (the looks' is behind the reader check)"""
        cfg = self.cfg()
        cfg.ad_groups_why, cfg.ad_groups = "", None
        page = sp.reader_refusal_page(cfg, "DOE.JANE.Q.1234567890")
        self.assertIn("static/portal.css", page)
        self.assertNotIn("portal-looks.css", page)

    def test_the_theme_script_is_versioned(self):
        """review r1 (Neon 15): portal-theme.js changed in 1.135.0 - its address carries its hash, so a browser takes the new one"""
        self.assertRegex(sp.shell(self.cfg(), "T", "<p>x</p>"), r'src="/portal/static/portal-theme\.js\?v=[0-9a-f]{10}"')


class LookFiles(unittest.TestCase):
    def test_the_stylesheet_is_made_from_its_source(self):
        self.assertEqual(G.build(), read(STATIC, "portal-looks.css"), "python scripts/gen_portal_looks.py")

    def test_every_look_is_readable_in_light_and_dark(self):
        self.assertEqual(G.check_contrast(), [])

    def test_every_look_has_all_five_blocks_and_the_whole_token_set(self):
        css = read(STATIC, "portal-looks.css")
        for k in sp.SKIN_KEYS:
            sel = ':root[data-skin="%s"]' % k
            for tail in ("{", ':not([data-theme="light"]){', '[data-theme="dark"]{', ":has(#theme-light:checked){", ":has(#theme-dark:checked){"):
                self.assertIn(sel + tail, css, (k, tail))
            block = css[css.index(sel + "{"):]
            block = block[:block.index("}")]
            for tok in ("--bg:", "--card:", "--card-strong:", "--edge:", "--line:", "--ink:", "--muted:", "--accent:", "--accent-ink:", "--ok:", "--ok-bg:",
                        "--warning:", "--warning-bd:", "--critical:", "--critical-bg:", "--unknown:", "--bar:", "--hover:", "--zebra:", "--code:", "--shadow:"):
                self.assertIn(tok, block, (k, tok))

    def test_fonts_are_bundled_with_their_licence_and_nothing_is_fetched(self):
        css = read(STATIC, "portal-looks.css") + read(STATIC, "wall-styles.css")
        urls = re.findall(r'url\("([^"]+)"\)', css)
        self.assertTrue(urls)
        fams = set()
        for u in urls:
            self.assertTrue(u.startswith("fonts/"), u)
            self.assertTrue(os.path.isfile(os.path.join(STATIC, u)), u)
            self.assertTrue(sp.FONT_NAME.match(u), u)
            fams.add(u.split("/")[1].rsplit("-latin-", 1)[0])
        for f in fams:
            lic = read(STATIC, "fonts", f + "-OFL.txt")
            self.assertIn("SIL Open Font License", lic, f)
        for n in ("portal-looks.css", "wall-styles.css", "portal-looks.js", "wall-styles.js", "wall-share.js"):
            self.assertNotRegex(read(STATIC, n), r"https?://(?!www\.w3\.org/2000/svg)", n)    # offline: no CDN, no outside file

    def test_the_font_route_takes_only_its_own_names(self):
        for bad in ("fonts/../site_portal.py", "fonts/x.woff2", "fonts/figtree-latin-400-normal.woff2/../../a", "fonts/Figtree-latin-400-normal.woff2"):
            self.assertIsNone(sp.FONT_NAME.match(bad), bad)
        self.assertTrue(sp.FONT_NAME.match("fonts/figtree-latin-400-normal.woff2"))

    def test_no_html_from_strings_and_no_inline_handlers(self):
        for n in ("portal-looks.js", "wall-styles.js", "wall-share.js"):
            js = read(STATIC, n)
            self.assertNotIn("innerHTML", js, n)
            self.assertNotIn("insertAdjacentHTML", js, n)
            self.assertNotRegex(js, r"setAttribute\(\"(style|on[a-z]+)\"", n)
            self.assertNotIn("eval(", js, n)

    def test_cues_say_what_things_do(self):
        js = read(STATIC, "portal-ops.js") + read(STATIC, "wall-styles.js")
        for cue in ("click an area for its wall", "tiles turn over every", "spotlight steps every", "Open on the wall",
                    "Click: on stage", "problems first", "Tiles turn over every", "Share to Teams", "click a row for its detail card", "live · read "):
            self.assertIn(cue, js, cue)
        share = read(STATIC, "wall-share.js")
        for b in ('"Copy text"', '"Copy picture"', '"Copy both"', '"Save picture"'):
            self.assertIn(b, share)


def answer(now, vc_age=20, ontap_age=20, conds=None, complete=True):
    """a small made-up wall model (build()'s shape): one vCenter, one ONTAP cluster, the off areas"""
    return {
        "now": now, "late": 180, "stale": 600, "title": "Ops center", "enclave": "ENCLAVE-A",
        "areas": [{"id": "storage", "name": "Storage", "on": True, "built": True, "feeds": ["ontap:nas1.example.mil", "vc:vc1.example.mil"], "complete": complete},
                  {"id": "vmware", "name": "VMware", "on": True, "built": True, "feeds": ["vc:vc1.example.mil"], "complete": complete},
                  {"id": "vdi", "name": "VDI", "on": False, "built": False, "feeds": [], "not_on_wall": "not on the wall yet."}],
        "feeds": {"vc:vc1.example.mil": {"name": "vCenter vc1", "state": "ok", "asof": now - vc_age},
                  "ontap:nas1.example.mil": {"name": "ONTAP nas1", "state": "ok", "asof": now - ontap_age}},
        "conds": conds if conds is not None else [],
        "thresholds": {"aggr": [85.0, 97.0], "ds": [85.0, 95.0], "high": [90.0, 15]},
        "storage": {"aggr": [{"n": "aggr2", "v": 91.0, "feed": "ontap:nas1.example.mil"}], "ds": [], "nodes": [4, 4]},
        "vmware": {"clusters": [{"n": "CL01", "vc": "vc1.example.mil", "feed": "vc:vc1.example.mil", "up": 7, "of": 8, "cpu": 60.7}], "hosts": []},
    }


class Summary(unittest.TestCase):
    def by(self, s):
        return dict((a["id"], a) for a in s["areas"])

    def test_current_and_clean_is_ok(self):
        now = time.time()
        a = self.by(M.summary(answer(now), now))
        self.assertEqual((a["storage"]["state"], a["vmware"]["state"], a["vdi"]["state"]), ("ok", "ok", "off"))
        self.assertEqual(a["storage"]["metric"]["v"], 91.0)
        self.assertEqual((a["storage"]["metric"]["warn"], a["storage"]["metric"]["crit"]), (85.0, 97.0))       # the settings' limits
        self.assertEqual(a["vmware"]["big"], "7/8")
        self.assertEqual(a["vmware"]["metric"]["warn"], 90.0)

    def test_a_stale_feed_is_never_green(self):
        now = time.time()
        for vc_age, ontap_age in ((900, 20), (20, 900), (None, 20)):
            m = answer(now, vc_age or 0, ontap_age)
            if vc_age is None:
                m["feeds"]["vc:vc1.example.mil"]["asof"] = None
            a = self.by(M.summary(m, now))
            self.assertEqual(a["storage"]["state"], "stale", (vc_age, ontap_age))                 # Storage lists both feeds
            self.assertNotEqual(a["storage"]["metric"]["state"], "ok", (vc_age, ontap_age))
            self.assertTrue(a["storage"]["why"], (vc_age, ontap_age))
        a = self.by(M.summary(answer(now, vc_age=300), now))                                      # LATE: not green either
        self.assertEqual(a["vmware"]["state"], "stale")
        a = self.by(M.summary(answer(now, complete=False), now))
        self.assertEqual(a["vmware"]["state"], "stale")
        m = answer(now)
        m["feeds"]["vc:vc1.example.mil"]["state"] = "auth"
        self.assertEqual(self.by(M.summary(m, now))["vmware"]["state"], "stale")
        m = answer(now)
        del m["conds"]
        self.assertEqual(self.by(M.summary(m, now))["vmware"]["state"], "stale")               # no condition list: nothing known

    def test_conditions_count_and_a_last_known_one_is_grey(self):
        now = time.time()
        conds = [{"id": "a", "area": "vmware", "sev": "crit", "t": "host down", "feed": "vc:vc1.example.mil", "end": None, "start": now - 60},
                 {"id": "b", "area": "storage", "sev": "warn", "t": "aggr2 91 % used", "feed": "ontap:nas1.example.mil", "end": None, "start": now - 60},
                 {"id": "c", "area": "storage", "sev": "crit", "t": "cleared", "feed": "ontap:nas1.example.mil", "end": now - 10, "start": now - 600}]
        s = M.summary(answer(now, conds=conds), now)
        a = self.by(s)
        self.assertEqual((a["vmware"]["state"], a["vmware"]["crit"]), ("crit", 1))
        self.assertEqual((a["storage"]["state"], a["storage"]["warn"], a["storage"]["crit"]), ("warn", 1, 0))
        self.assertEqual(s["state"], "crit")
        self.assertEqual(a["vmware"]["worst"][0]["t"], "host down")
        conds[0]["last_known"] = True
        self.assertEqual(self.by(M.summary(answer(now, conds=conds), now))["vmware"]["state"], "stale")

    def test_an_inaccessible_datastore_is_critical_whatever_its_fill(self):
        """review r1 (Codex 5): no aggregate, a watched datastore vCenter marks inaccessible at 50 %: critical, as the wall's dsLevel"""
        now = time.time()
        m = answer(now)
        m["storage"]["aggr"] = []
        m["storage"]["ds"] = [{"n": "DS-A", "v": 50.0, "accessible": False, "capacity_watched": True, "feed": "vc:vc1.example.mil"},
                              {"n": "DS-B", "v": 70.0, "accessible": True, "capacity_watched": True, "feed": "vc:vc1.example.mil"}]
        met = self.by(M.summary(m, now))["storage"]["metric"]
        self.assertEqual((met["obj"], met["state"]), ("DS-A", "crit"))
        self.assertTrue(met.get("forced"))

    def test_each_state_carries_until_when_it_holds(self):
        """review r2 (Codex 1, Neon 1): OK holds until the oldest source passes LATE; each current condition until its own
        source passes STALE - one with no feed of its own (a vCenter not answering) until the next read; never cleared by an
        unrelated old source"""
        now = time.time()
        a = self.by(M.summary(answer(now, vc_age=20, ontap_age=50), now))
        self.assertAlmostEqual(a["storage"]["ok_until"], now - 50 + 180, places=3)
        self.assertAlmostEqual(a["storage"]["metric"]["asof"], now - 50, places=3)
        conds = [{"id": "vc:vc2", "kind": "vc", "area": "vmware", "sev": "crit", "t": "vCenter vc2 login failed", "feed": "", "end": None, "start": now},
                 {"id": "h", "area": "vmware", "sev": "warn", "t": "host high", "feed": "vc:vc1.example.mil", "end": None, "start": now}]
        a = self.by(M.summary(answer(now, conds=conds), now))
        self.assertEqual(a["vmware"]["state"], "crit")
        self.assertIsNone(a["vmware"]["ok_until"])
        self.assertEqual(sorted((x[0], x[1] is None) for x in a["vmware"]["live"]), [("crit", True), ("warn", False)])

    def test_the_endpoint(self):
        class FakeWall(object):
            cfg = type("C", (), {"tz_name": "Europe/Berlin"})()

            def model(self, now=None):
                return answer(now)
        out = sp.WallLive.summary(FakeWall(), now=time.time())
        self.assertEqual(out["zone"], "Europe/Berlin")
        self.assertEqual([a["id"] for a in out["areas"]], ["storage", "vmware", "vdi"])
        json.dumps(out)


@unittest.skipUnless(shutil.which("node"), "needs Node")
class ShareText(unittest.TestCase):
    def run_js(self, code):
        src = "global.window = {}; global.document = {};\n" + read(STATIC, "wall-share.js") + "\n" + code
        return subprocess.run(["node", "-e", src], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)

    def test_the_teams_html_is_escaped(self):
        evil = '<img src=x onerror=alert(1)>'
        a = {"sev": "crit", "area": "VMware" + evil, "title": "host " + evil, "obj": evil, "since": "05:27", "age": "3 h", "site": 'S"<b>',
             "v": None, "warn": None, "crit": None, "src": "vCenter " + evil, "asof": "08:00", "asofAge": "", "check": evil,
             "link": 'https://portal.example.mil/portal/wall?area=vmware&open=c%3Ax"><script>', "stamp": "08:01", "zone": "UTC"}
        r = self.run_js("var S = window.SAWallShare; process.stdout.write(JSON.stringify([S.html(%s), S.text(%s)]));" % (json.dumps(a), json.dumps(a)))
        self.assertEqual(r.returncode, 0, r.stderr)
        html_, text = json.loads(r.stdout)
        self.assertNotIn("<img", html_)
        self.assertNotIn("<script", html_)
        self.assertNotIn('"><', html_.split('href="', 1)[1].split('">Open on the wall', 1)[0])
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html_)
        self.assertIn("host " + evil, text)                                                       # the plain text is the text itself

    def test_a_cleared_or_last_known_condition_is_said_so(self):
        """review r1 (Codex 3, 4): a cleared condition is never shared as open; a last-known one says when it was last seen"""
        base = {"area": "VMware", "title": "host down", "obj": "", "since": "05:27", "until": "06:00", "age": "33 min", "site": "",
                "v": None, "warn": None, "crit": None, "src": "vCenter", "asof": "05:59", "asofAge": "", "check": "-", "link": "https://portal.example.mil/portal/wall",
                "stamp": "08:00", "zone": "UTC", "lastSeen": ""}
        r = self.run_js("var S = window.SAWallShare; process.stdout.write(JSON.stringify([S.text(%s), S.text(%s)]));"
                        % (json.dumps(dict(base, sev="cleared")), json.dumps(dict(base, sev="stale", lastSeen="05:59:30 (2 h ago)"))))
        self.assertEqual(r.returncode, 0, r.stderr)
        cleared, last = json.loads(r.stdout)
        self.assertTrue(cleared.startswith("[CLEARED]"), cleared)
        self.assertIn("Was open 05:27 to 06:00 (33 min) - cleared, not open now", cleared)
        self.assertNotIn("CRITICAL", cleared)
        self.assertTrue(last.startswith("[NOT CURRENT]"), last)
        self.assertIn("last seen 05:59:30 (2 h ago)", last)
        self.assertIn("recovery not observed", last)

    def test_the_words_say_where_the_value_is(self):
        r = self.run_js("var w = window.SAWallShare.words; process.stdout.write(JSON.stringify([w({v: 91, warn: 85, crit: 97, unit: '%'}), "
                        "w({v: 98, warn: 85, crit: 97, unit: '%'}), w({v: 60.7, warn: 90, crit: null, unit: '%'}), w({v: 30, warn: 24, crit: 72, unit: ' h'})]));")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), ["91% · 6 pts over warn 85 · crit at 97", "98% · 1 pt over crit 97", "60.7% · 29.3 pts below high 90",
                                                "30 h · 6 h over warn 24 · crit at 72"])
        r = self.run_js("process.stdout.write(window.SAWallShare.words({v: 72, warn: 24, crit: 72, unit: ' h', strict: true}));")  # the wall's rule: over, not at
        self.assertEqual(r.stdout, "72 h · 48 h over warn 24 · crit at 72")


if __name__ == "__main__":
    unittest.main()
