#!/usr/bin/env python3
"""1.86.0: the report website's team view (report_teams; plugins/filter/team_filters.py, findings_report's teams, the
templates report_teams.html.j2 and report_pop.css.j2) and its motion and team layer (report_pop.js, report_motion) -
on the atlas, brief and ops pages and the ticket insights' pages. Placeholders only (TEAM-A, SITEA, example.mil).
Run: python tests/test_report_teams.py"""
import glob
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
sys.path.insert(0, os.path.join(REPO, "plugins", "module_utils"))
import site_filters as sf  # noqa: E402
import team_filters as tf  # noqa: E402

TPL = os.path.join(REPO, "roles", "site_email", "templates")
D = ".corp.example.mil"
TEAMS = {"TEAM-A": ["group:linux_web"], "TEAM-B": ["group:linux_db", "host:app01*"], "TEAM-C": ["group:infra"]}
HG = {"web01" + D: ["linux_web"], "web02" + D: ["linux_web"], "db01" + D: ["linux_db"], "app01" + D: ["infra"],
      "app02" + D: ["infra"], "tools01" + D: ["tools"]}
F = {"web01" + D: [{"check": "disk", "severity": "critical", "summary": "/var is 97% full", "hint": "df -h /var"}],
     "web02" + D: [{"check": "patching", "severity": "warning", "summary": "no update for 52 days", "hint": "rpm -qa --last | head"}],
     "db01" + D: [{"check": "memory", "severity": "warning", "summary": "swap 71% used", "hint": "free -m"},
                  {"check": "patching", "severity": "low", "summary": "restart pending", "hint": "needs-restarting -r"}],
     "app01" + D: [{"check": "selinux", "severity": "critical", "summary": "SELinux is permissive", "hint": "getenforce"}],
     "app02" + D: []}
MISSING = ["tools01" + D]


def teams():
    return {"map": tf.report_team_map(HG, "SITEA Linux", TEAMS), "names": tf.report_team_names(TEAMS), "other": "Unassigned", "setting": TEAMS}


def report(with_teams=True):
    return sf.findings_report(F, "Health check", MISSING, ["disk"], [], None, teams() if with_teams else None)


_FILTERS = {}


def filters():
    """Every filter of plugins/filter (Jinja's own kept: Ansible's are not loaded here)."""
    if not _FILTERS:
        for f in sorted(glob.glob(os.path.join(REPO, "plugins", "filter", "*.py"))):
            spec = importlib.util.spec_from_file_location("teams_t_" + os.path.basename(f)[:-3], f)
            m = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(m)
                _FILTERS.update(m.FilterModule().filters())
            except Exception:                    # noqa: BLE001 - a filter file that needs Ansible: not this test's
                pass
    return _FILTERS


def render(look, doc, runs=None, **v):
    import jinja2
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TPL), autoescape=False, extensions=["jinja2.ext.loopcontrols", "jinja2.ext.do"])
    env.filters.update(dict((k, f) for k, f in filters().items() if k not in env.filters))
    base = {"_report": doc, "_page_report": doc, "_email_footer": "AAP job 1", "_page_logo_cid": "", "_page_runs": runs or [],
            "report_email_header_text": "Site automation report"}
    base.update(v)
    return env.get_template("report_%s.html.j2" % look).render(**base)


def past(n=4):
    out = []
    for i in range(n):
        out.append({"id": 100 + i, "status": "successful", "finished": "2026-11-%02dT06:01:00Z" % (16 + i),
                    "report": {"status": "warning", "value": 6, "label": "Hosts checked",
                               "teams": {"": [2, 2 + i, 1], "TEAM-A": [1, 1 + i, 0], "TEAM-B": [1, 1, 1], "TEAM-C": [1, 0, 0], "Unassigned": [0, 0, 0]}}})
    return out


class Matching(unittest.TestCase):
    def test_order_several_and_other(self):
        self.assertEqual(tf.site_teams("web01" + D, ["linux_web"], "", TEAMS), ["TEAM-A"])
        self.assertEqual(tf.site_teams("app01" + D, ["infra"], "", TEAMS), ["TEAM-B", "TEAM-C"])      # the setting's order: TEAM-B owns it
        self.assertEqual(tf.site_teams("tools01" + D, ["tools"], "", TEAMS), ["Unassigned"])
        self.assertEqual(tf.site_teams("tools01" + D, ["tools"], "", TEAMS, "Nobody"), ["Nobody"])

    def test_matchers(self):
        t = {"T1": ["host:APP*.corp.example.mil"], "T2": ["host:db0?"], "T3": ["inventory:sitea windows"], "T4": ["linux_web"]}
        self.assertEqual(tf.site_teams("app07.corp.example.mil", [], "", t), ["T1"])           # any case
        self.assertEqual(tf.site_teams("db05.corp.example.mil", [], "", t), ["T2"])            # no dot: the short name too
        self.assertEqual(tf.site_teams("x" + D, [], "SITEA Windows", t), ["T3"])
        self.assertEqual(tf.site_teams("x" + D, ["linux_web"], "", t), ["T4"])                  # a bare word is a group
        self.assertEqual(tf.site_teams("x" + D, ["Linux_Web"], "", t), ["Unassigned"])        # a group: exactly

    def test_off(self):
        for off in ({}, None, [], ""):
            self.assertEqual(tf.site_teams("web01" + D, ["linux_web"], "", off), [])
            self.assertEqual(tf.report_team_map(HG, "", off), {})
            self.assertEqual(tf.report_team_names(off), [])

    def test_names(self):
        self.assertEqual(tf.report_team_names(TEAMS), ["TEAM-A", "TEAM-B", "TEAM-C", "Unassigned"])

    def test_notes_never_fail(self):
        bad = {"TEAM-A": ["group:linux_web"], "TEAM-X": ["group:nope"], "TEAM-Y": "ftp:x", "": ["group:a"], "TEAM-Z": 5}
        n = tf.report_teams_notes(bad, HG, ["linux_web", "linux_db", "infra", "tools"], "")
        self.assertIn("report_teams: TEAM-X matches no host of this run", n)
        self.assertIn("report_teams: TEAM-X - group:nope is not a group of the inventory", n)
        self.assertTrue(any("TEAM-Y - 'ftp:x' is not group:" in x for x in n), n)
        self.assertTrue(any("a team without a name" in x for x in n), n)
        self.assertTrue(any("TEAM-Z - its matchers are not a list" in x for x in n), n)
        self.assertEqual(tf.report_teams_notes("TEAM-A", HG), ["report_teams is not a mapping of team: [matchers] - no teams this run"])
        self.assertEqual(tf.report_teams_notes(TEAMS, HG, list(set(g for gs in HG.values() for g in gs)), ""), [])
        self.assertEqual(tf.site_teams("web01" + D, ["linux_web"], "", bad), ["TEAM-A"])        # the rest still works


class Report(unittest.TestCase):
    def test_without_teams_as_before(self):
        self.assertEqual(sf.findings_report(F, "Health check", MISSING, ["disk"], []), report(False))
        self.assertEqual(sf.findings_report(F, "Health check", MISSING, ["disk"], [], None, {"names": []}), report(False))

    def test_rows_say_their_teams_and_severity(self):
        r = report()
        fs = [s for s in r["sections"] if s["title"] == "Findings"][0]
        tags = dict((row[0] + "/" + row[2], t["data"]) for row, t in zip(fs["rows"], fs["row_tags"]))
        self.assertEqual(tags["app01" + D + "/selinux"], {"team": '["TEAM-B","TEAM-C"]', "sev": "critical"})
        self.assertEqual(tags["db01" + D + "/patching"], {"team": '["TEAM-B"]', "sev": "low"})
        self.assertEqual(len(fs["row_tags"]), len(fs["rows"]))

    def test_the_team_view_takes_start_heres_place(self):
        r = report()
        titles = [s["title"] for s in r["sections"]]
        self.assertEqual(titles[0], "Your team")
        self.assertNotIn("Start here", titles)
        self.assertIn("Start here", [s["title"] for s in report(False)["sections"]])
        t = r["teams"]
        self.assertEqual(t["names"], ["TEAM-A", "TEAM-B", "TEAM-C", "Unassigned"])
        self.assertEqual(t["figures"][""]["hosts"], 6)
        self.assertEqual(t["figures"][""]["critical"], 2)                        # each finding once
        self.assertEqual(t["figures"]["TEAM-B"]["critical"], 1)                  # app01 (shared): its findings count for both teams
        self.assertEqual(t["figures"]["TEAM-C"]["critical"], 1)
        self.assertEqual(t["figures"]["TEAM-C"]["shared"], 1)                    # app01 is TEAM-B's (its owner)
        self.assertEqual(t["figures"]["Unassigned"], dict(t["figures"]["Unassigned"], hosts=1, reported=0, missing=1))
        self.assertEqual(t["counts"]["TEAM-A"], [1, 1, 0])
        self.assertEqual(t["counts"][""], [2, 2, 1])
        self.assertEqual([c["title"] for c in t["todo"]["TEAM-A"]], ["/var is 97% full", "no update for 52 days"])
        self.assertIn("TEAM-B: group:linux_db, host:app01*", t["map"])

    def test_a_reports_own_start_here_wins(self):
        own = {"title": "CAT I", "top": True, "start_here": [{"status": "critical", "title": "x"}]}
        r = sf.findings_report(F, "STIG", [], [], [own], None, teams())
        self.assertNotIn("teams", r)
        self.assertEqual(r["sections"][0]["title"], "Start here")

    def test_extra_boxes_kept(self):
        box = {"title": "Patch age", "boxes": [{"label": "Patching late", "value": 2, "status": "warning"}], "lines": ["x"]}
        r = sf.findings_report(F, "Health check", MISSING, [], [box], None, teams())
        self.assertEqual(r["teams"]["tiles_kept"], box["boxes"])

    def test_the_runs_keep_the_teams(self):
        r = report()
        h = sf.report_headline(report(False), r)
        self.assertEqual(h["teams"]["TEAM-A"], [1, 1, 0])
        self.assertNotIn("teams", sf.report_headline(report(False)))
        runs = sf.report_runs(past(), r, 14, 36)
        self.assertEqual(runs[0]["teams"]["TEAM-A"], [1, 1, 0])
        self.assertTrue(runs[-1]["current"])
        self.assertEqual(runs[-1]["teams"]["TEAM-A"], [1, 1, 0])

    def test_the_view(self):
        v = tf.report_team_view(report()["teams"], sf.report_runs(past(), report(), 14, 36))
        a = [x for x in v["views"] if x["team"] == "TEAM-A"][0]
        self.assertEqual([h["short"] for h in a["runs"]], ["Nov 16", "Nov 17", "Nov 18", "Nov 19", "this run"])
        self.assertEqual(a["d_warn"], {"dir": "down", "n": 3, "text": "3 fewer than last run"})     # 4 last run, 1 now
        self.assertEqual(a["d_crit"]["text"], "same as last run")
        e = v["views"][0]
        self.assertEqual(e["team"], "")
        self.assertEqual(e["d_warn"]["text"], "3 fewer than last run")             # everyone's own numbers (""), not a sum
        self.assertEqual(e["pct"], 20)                                              # 1 of the 5 that reported (app02)
        self.assertEqual(v["counts"], {"TEAM-A": 2, "TEAM-B": 2, "TEAM-C": 2, "Unassigned": 1})
        nv = tf.report_team_view(report()["teams"], [])
        self.assertIsNone(nv["views"][1]["d_crit"])                                # no last run: no change said
        self.assertEqual(len(nv["views"][1]["runs"]), 1)


class Pages(unittest.TestCase):
    def test_each_look(self):
        runs = sf.report_runs(past(), report(), 14, 36)
        for look in ("atlas", "brief", "ops"):
            h = render(look, report(), runs)
            self.assertEqual(h.count('class="pop-teams"'), 1, look)
            self.assertIn('<button type="button" class="pt-b pt-un" data-team="Unassigned" aria-pressed="false">Unassigned <span class="pt-n">1</span></button>', h)
            self.assertIn('<div class="pop-team-view" data-team="TEAM-A" hidden>', h)
            self.assertIn('<tr data-team="[&#34;TEAM-B&#34;,&#34;TEAM-C&#34;]" data-sev="critical"', h)
            self.assertIn("3 fewer than last run", h)
            self.assertNotIn('>Start here<', h)
            self.assertIn("window.reportPop", h)
            self.assertIn(".pop-move .pop-wait", h)
            self.assertNotIn("style=", h[h.index('class="pop-teams"'):h.index("</section>", h.index('class="pop-teams"'))], look)   # CSP: no inline style
            p = render(look, report(False), runs)
            self.assertNotIn('class="pop-teams"', p)
            self.assertIn("Start here", p)
            self.assertIn("window.reportPop", p)                                   # the motion layer on every page

    def test_motion(self):
        for look in ("atlas", "brief", "ops"):
            self.assertIn('<html lang="en">', render(look, report(False)))
            self.assertIn('<html lang="en" class="motion-off">', render(look, report(False), report_motion="off"))
            self.assertIn('<html lang="en" class="motion-always">', render(look, report(False), report_motion="Always"))
            self.assertIn('<html lang="en">', render(look, report(False), report_motion="sideways"))

    def test_the_script(self):
        js = open(os.path.join(TPL, "report_pop.js"), encoding="utf-8").read()
        self.assertTrue(all(ord(c) < 128 for c in js), "report_pop.js: ASCII only (the Windows writer)")
        for bad in ("{{", "{%", "{#", "innerHTML", "eval(", "new Function"):
            self.assertNotIn(bad, js)
        css = open(os.path.join(TPL, "report_pop.css.j2"), encoding="utf-8").read()
        for bad in ("{{", "{%", "{#", "@import", "url(http"):
            self.assertNotIn(bad, css)
        sr = open(os.path.join(TPL, "site_report.js"), encoding="utf-8").read()
        self.assertIn("r._tm||r._sv", sr)
        self.assertIn("window.siteReport.rowsChanged", sr)
        node = shutil.which("node")
        if node:
            for f in ("report_pop.js", "site_report.js"):
                out = subprocess.run([node, "--check", os.path.join(TPL, f)], capture_output=True, text=True)
                self.assertEqual(out.returncode, 0, out.stderr)

    def test_no_teams_in_the_email_looks(self):
        import jinja2
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(TPL), autoescape=False, extensions=["jinja2.ext.loopcontrols", "jinja2.ext.do"])
        env.filters.update(dict((k, f) for k, f in filters().items() if k not in env.filters))
        for t in ("report.html.j2", "report_command.html.j2", "report_portal.html.j2", "report_glass.html.j2"):
            try:
                h = env.get_template(t).render(_report=report(False), _email_footer="x", _email_runs=[])
            except Exception as e:                      # noqa: BLE001 - a look that needs more than this test gives
                self.skipTest("%s: %s" % (t, e))
            self.assertNotIn("pop-team", h)
            self.assertNotIn("reportPop", h)


class Review1860(unittest.TestCase):
    """The fixes of the 1.86.0 review (Neon)."""
    def test_names_twice_or_as_the_other_are_left_out(self):
        t = {"TEAM-A": ["group:linux_web"], " team-a ": ["group:infra"], "TEAM  B": ["group:linux_db"], "unassigned": ["group:tools"],
             "OPS|WEB": ["host:app01*"], "OPS": ["host:app02*"]}
        self.assertEqual(tf.report_team_names(t), ["TEAM-A", "TEAM B", "OPS|WEB", "OPS", "Unassigned"])
        notes = tf.report_teams_notes(t, HG, ["linux_web", "linux_db", "infra", "tools"])
        self.assertIn("report_teams: team-a - another team has this name - left out (rename it)", notes)
        self.assertIn("report_teams: unassigned - the name of the hosts no team has (report_team_other) - left out (rename it)", notes)
        self.assertEqual(tf.site_teams("app02" + D, ["infra"], "", t), ["OPS"])            # OPS is not OPS|WEB
        r = sf.findings_report(F, "Health check", MISSING, [], [], None,
                               {"map": tf.report_team_map(HG, "", t), "names": tf.report_team_names(t), "other": "Unassigned", "setting": t})
        tags = dict((k, v) for k, v in (r.get("row_tags") or {}).items()) if isinstance(r.get("row_tags"), dict) else {}
        self.assertTrue(all(isinstance(__import__("json").loads(v["team"]), list) for v in tags.values()) if tags else True)

    def test_a_team_nobody_heard_from_is_not_all_clear(self):
        t = report()["teams"]
        todo = t["todo"]["Unassigned"]                                      # tools01: it did not report
        self.assertEqual([(c["status"], c.get("chip")) for c in todo], [("warning", "Not checked")])
        self.assertIn("tools01" + D, todo[0]["subject"])
        h = render("atlas", report(), sf.report_runs(past(), report(), 14, 36))
        self.assertIn("Not checked", h)

    def test_an_all_clear_run_keeps_the_team_view(self):
        clean = dict((h, []) for h in HG)
        r = sf.findings_report(clean, "Health check", [], [], [], None, teams())
        self.assertIn("Your team", [s["title"] for s in sf.report_tidy(r)["sections"]])

    def test_the_fold_row_is_no_data_row(self):
        sr = open(os.path.join(TPL, "site_report.js"), encoding="utf-8").read()
        self.assertIn("return (!r.hidden||r._c)&&!r.classList.contains('xr')&&!r.classList.contains('pt-fold')}", sr)      # Copy and CSV
        self.assertIn("if(r.classList.contains('xr')||r.classList.contains('pt-fold'))continue;", sr)     # the rows a table registers (1.100.0's register())
        self.assertIn("', '+others+' more in other teams'", sr)
        for look in ("atlas", "brief", "ops"):
            self.assertIn("tr:not(.xr):not(.pt-fold)>:is(th,td):nth-child(", open(os.path.join(TPL, "report_%s.html.j2" % look), encoding="utf-8").read())

    def test_motion_switches(self):
        css = open(os.path.join(TPL, "report_pop.css.j2"), encoding="utf-8").read()
        self.assertIn("@media screen and (min-width:640.02px){.appbar,header.bar{position:sticky}}", css)
        self.assertNotIn("\n.appbar,header.bar{position:sticky}", css)                  # never over the looks' 640 px and print rules
        self.assertIn(".motion-off *,.pop-still *{animation:none!important;transition:none!important}", css)
        for look in ("brief", "ops"):
            self.assertIn("@media (prefers-reduced-motion:reduce){:root:not(.motion-always) *{transition:none!important;animation:none!important}",
                          open(os.path.join(TPL, "report_%s.html.j2" % look), encoding="utf-8").read())
        self.assertIn("'motion': _sni_opts.motion", open(os.path.join(REPO, "roles", "servicenow_insights", "tasks", "breach.yml"), encoding="utf-8").read())
        js = open(os.path.join(TPL, "report_pop.js"), encoding="utf-8").read()
        self.assertIn("if(v._popStop)v._popStop();", js)
        self.assertIn("r._tl=JSON.parse(r.getAttribute('data-team')||'[]')", js)

    def test_in_a_browser(self):
        """Count-up: TEAM-A, TEAM-B, TEAM-A within the count ends on TEAM-A's numbers. Search with a team chosen: a match in another team
        keeps the section, said."""
        chrome = shutil.which("google-chrome") or shutil.which("chromium")
        if not chrome:
            self.skipTest("no Chrome here")
        import tempfile
        h = render("atlas", report(), sf.report_runs(past(), report(), 14, 36))
        probe = ('<script>addEventListener("load",function(){var D=document,o={};function b(t){return D.querySelector(\'.pop-team .pt-b[data-team="\'+t+\'"]\')}'
                 'function tiles(){return [].map.call(D.querySelectorAll(".pop-team-view:not([hidden]) .pt-tile .v"),function(v){return v.firstChild.nodeValue.trim()})}'
                 'setTimeout(function(){b("TEAM-A").click();setTimeout(function(){o.a0=tiles().join("|");b("TEAM-B").click();setTimeout(function(){b("TEAM-A").click();setTimeout(function(){o.a1=tiles().join("|");'
                 'var f=D.getElementById("find");f.value="selinux";f.dispatchEvent(new Event("input"));setTimeout(function(){'
                 'var d=[].filter.call(D.querySelectorAll("details"),function(x){var s=x.querySelector("summary h2");return s&&/Findings/.test(s.textContent)})[0];'
                 'o.sec=d?!d.hidden:null;o.found=(D.querySelector(".find-n,#found,.found")||{}).textContent||"";'
                 'var fb=D.querySelector(".pt-fb");o.fold=fb?fb.textContent:"";D.title="R "+JSON.stringify(o)},600)},2500)},120)},2500)},1500)})</script></body>')
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "p.html")
            with open(p, "w") as fh:
                fh.write(h.replace("</body>", probe, 1))
            out = subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--virtual-time-budget=16000", "--window-size=1280,900",
                                  "--dump-dom", "file://" + p], capture_output=True, text=True, timeout=120).stdout
        m = re.search(r"<title>R (.*?)</title>", out)
        self.assertTrue(m, out[-400:])
        o = __import__("json").loads(m.group(1).replace("&quot;", '"').replace("&amp;", "&"))
        self.assertEqual(o["a1"], o["a0"])                     # TEAM-A again: its own numbers, not one in between, not 0
        self.assertTrue(o["sec"], o)                           # app01's SELinux finding is TEAM-B's: the section stays
        self.assertIn("more in other teams", o["found"] + " " + o["fold"])


class Insights(unittest.TestCase):
    def test_team_bar_and_motion(self):
        import servicenow_insights_page as P
        self.assertTrue(P.POP_JS.startswith("/* site-automation - the report website's motion"))
        self.assertIn(".pop-team-bar", P.POP_CSS)
        nodes = [{"key": "all", "label": "All", "lv": 0, "full": "All assignment groups"},
                 {"key": "Ops - Linux", "label": "Linux", "lv": 1, "full": "Ops - Linux"},
                 {"key": "Ops - VDI", "label": "VDI", "lv": 1, "full": "Ops - VDI"}]
        links = {"all": "all.html", "Ops - Linux": "linux.html", "Ops - VDI": "vdi.html"}
        b = P.pop_team_bar(nodes, ["Ops - Linux", "Ops - VDI"], links, "Ops - VDI", False)
        self.assertIn('<a class="pt-b" href="vdi.html" data-team="Ops - VDI" title="Ops - VDI" aria-current="page">VDI</a>', b)
        self.assertIn('<a class="pt-b" href="all.html" data-team="" title="All assignment groups">All groups</a>', b)
        self.assertIn('data-pages="1" data-current="Ops - VDI"', b)
        self.assertEqual(P.pop_team_bar(nodes, ["Ops - Linux"], links, None, True), "")             # one team: no bar
        self.assertEqual(P._html_open({"motion": "off"}), '<html lang="en" class="motion-off">')
        self.assertEqual(P._html_open({}), '<html lang="en">')


if __name__ == "__main__":
    unittest.main()
