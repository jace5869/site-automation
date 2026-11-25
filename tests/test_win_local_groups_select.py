#!/usr/bin/env python3
"""1.122.0: the report page's Export selected (roles/site_email/templates/site_report.js, SelX; a section with select_export:
the local groups' By server and By member). With node: the ticked rows as CSV (quoted where needed), JSON and the approved
list's YAML (one entry per group, any case once, a group with no entry []) - the YAML parsed back. The page: the section
says data-sel-export, each row its data-sel, no inline script.
  python3 tests/test_win_local_groups_select.py      (the JS part skips without node)"""
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
JS = os.path.join(REPO, "roles", "site_email", "templates", "site_report.js")

ROWS = [{"g": "Administrators", "e": "{computer}\\Administrator", "m": ".\\Administrator", "i": "S-1-5-21-9-9-9-500", "k": "Local user", "n": 5, "s": "SRV-APP01"},
        {"g": "Administrators", "e": "{COMPUTER}\\administrator", "m": ".\\Administrator", "i": "S-1-5-21-8-8-8-500", "k": "Local user", "n": 5, "s": "SRV-APP02"},
        {"g": "Administrators", "e": "CORP\\it's, \"quoted\"", "m": "CORP\\it's, \"quoted\"", "i": "S-1-5-21-1-1-1-7", "k": "Domain group", "n": 2},
        {"g": "Account Operators", "e": "", "m": "", "i": "", "k": "", "n": 0}]


def run_js(expr):
    with open(JS) as fh:
        src = fh.read()
    pure = re.search(r"/\* sel-pure-start \*/(.*?)/\* sel-pure-end \*/", src, re.S).group(1)
    r = subprocess.run(["node", "-e", pure + "\nprocess.stdout.write(JSON.stringify(" + expr + "));"],
                       input="", capture_output=True, text=True, timeout=60, env=dict(os.environ, ROWS=json.dumps(ROWS)))
    if r.returncode:
        raise AssertionError(r.stderr)
    return json.loads(r.stdout)


@unittest.skipUnless(shutil.which("node"), "no node")
class Js(unittest.TestCase):
    def test_csv(self):
        import csv
        import io
        text = run_js("SelX.csv(JSON.parse(process.env.ROWS))")
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[0], ["server", "group", "member", "sid", "kind", "entry", "servers"])
        self.assertEqual(rows[3][2], "CORP\\it's, \"quoted\"")
        self.assertEqual(len(rows), 5)

    def test_csv_formula_cells(self):
        text = run_js("SelX.csv([{g:'@Ops',m:'=1+1',e:'-x',i:'+y',k:'',n:3}])")
        self.assertEqual(text.splitlines()[1], ",'@Ops,'=1+1,'+y,,'-x,3")

    def test_by_member_one_line_per_server(self):
        rows = run_js("JSON.parse(SelX.json([{g:'Administrators',m:'.\\\\svc',e:'{computer}\\\\svc',i:'',k:'Local user',n:2,"
                      "x:{'SRV-B':'S-1-5-21-2-2-2-1001','SRV-A':'S-1-5-21-1-1-1-1001'}}]))")
        self.assertEqual([(r["server"], r["sid"]) for r in rows], [("SRV-A", "S-1-5-21-1-1-1-1001"), ("SRV-B", "S-1-5-21-2-2-2-1001")])

    def test_yaml_template_marks(self):
        text = run_js("SelX.yaml([{g:'Administrators',e:'CORP\\\\{{ lookup(\\'env\\',\\'X\\') }}',i:'S-1-5-21-1-1-1-5'},"
                      "{g:'Administrators',e:'{% raw %}x',i:''}], 'win_local_groups_approved', 'groups')")
        self.assertNotIn("{{", text)
        self.assertNotIn("{%", text)
        self.assertEqual(yaml.safe_load(text)["win_local_groups_approved"]["groups"]["Administrators"], ["S-1-5-21-1-1-1-5"])

    def test_json(self):
        d = json.loads(run_js("SelX.json(JSON.parse(process.env.ROWS))"))
        self.assertEqual(d[0]["server"], "SRV-APP01")
        self.assertNotIn("server", d[2])

    def test_yaml(self):
        text = run_js("SelX.yaml(JSON.parse(process.env.ROWS), 'win_local_groups_approved', 'groups')")
        g = yaml.safe_load(text)["win_local_groups_approved"]["groups"]
        self.assertEqual(g["Administrators"], ["{computer}\\Administrator", "CORP\\it's, \"quoted\""])     # any case once
        self.assertEqual(g["Account Operators"], [])


@unittest.skipUnless(shutil.which("node"), "no node")
class SameChecks(unittest.TestCase):
    """The page's answer files and PowerShell check from the ticked rows are the job's, to the character."""

    def test_identical(self):
        import win_local_groups_filters as w
        parts = w.check_parts()
        rows = [{"g": "Administrators", "e": "{computer}\\Administrator", "i": "S-1-5-21-9-9-9-500"},
                {"g": "Administrators", "e": "CORP\\Domain Admins", "i": "S-1-5-21-1-1-1-512"},
                {"g": "administrators", "e": "CORP\\ignored", "i": ""},
                {"g": "Remote Desktop Users", "e": "CORP\\Helpdesk-RDP", "i": ""},
                {"g": "Administrators", "e": "CORP\\it's \"q\" & <x>", "i": ""},
                {"g": "Administrators", "e": "corp\\domain admins", "i": ""},
                {"g": "Administrators", "e": "S-1-5-21-1-1-1-9907", "i": "S-1-5-21-1-1-1-9907"},
                {"g": "Administrators", "e": "CORP\\bad\u000b", "i": ""},
                {"g": "Administrators", "e": "CORP\\Jos\u00e9", "i": ""},
                {"g": "Administrators", "e": "CORP\\Ops\U0001F680", "i": ""},
                {"g": "Administrators", "e": " CORP\\Feff\ufeff ", "i": ""}]
        env = dict(os.environ, ROWS=json.dumps(rows), PARTS=json.dumps(parts))
        with open(JS) as fh:
            pure = re.search(r"/\* sel-pure-start \*/(.*?)/\* sel-pure-end \*/", fh.read(), re.S).group(1)
        js = pure + """
var rows=JSON.parse(process.env.ROWS),c=JSON.parse(process.env.PARTS),e=SelX.admins(rows);
process.stdout.write(JSON.stringify({ps1:SelX.ps1(e,c),xml:c.stigs.map(function(st){return SelX.xml(e,c,st)}),e:e}));"""
        r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        ents = [tuple(x) for x in out["e"]]
        self.assertEqual([x[0] for x in ents], ["{computer}\\Administrator", "CORP\\Domain Admins", "CORP\\it's \"q\" & <x>",
                                                "corp\\domain admins", "S-1-5-21-1-1-1-9907", "CORP\\bad\u000b", "CORP\\Jos\u00e9", "CORP\\Ops\U0001F680", " CORP\\Feff\ufeff "])
        self.assertIn('"CORP\\Feff\ufeff"', out["ps1"])
        self.assertIn("Ops\U0001F680", out["ps1"])
        self.assertNotIn("bad", out["ps1"])
        self.assertTrue(out["ps1"].startswith("\ufeff"))
        self.assertEqual(out["ps1"], w.check_script(ents, parts))
        self.assertEqual(out["xml"], [w.answer_file(ents, st, parts) for st in parts["stigs"]])


class Page(unittest.TestCase):
    def test_rendered(self):
        import site_filters as sf
        import test_win_local_groups as t
        import win_local_groups_filters as w
        from test_report_hover import render
        m = w.win_local_groups_model(t.fleet(), "auto")
        by = dict((s["host"], w.win_local_groups_findings(m, s["host"])) for s in m["read"])
        r = sf.findings_report(by, "Windows - local group members", [], ["local groups"], w.win_local_groups_sections(m, True))
        page = render("report_atlas.html.j2", r)
        self.assertEqual(page.count("data-sel-export="), 2)
        self.assertIn("data-sel=", page)
        self.assertIn("For xlsx, use the workbook", page)
        body = page.split("<body", 1)[1]
        self.assertNotIn(" onclick=", body)
        # the email has none of it
        mail = sf.findings_report(by, "Windows - local group members", [], ["local groups"], w.win_local_groups_sections(m, False))
        self.assertNotIn("data-sel", render("report.html.j2", mail))


def _modules():
    """site_report_web as Ansible gives it its module_utils (as tests/test_report_web.py does)."""
    import importlib.util
    sys.path.insert(0, os.path.join(REPO, "roles", "site_email", "library"))
    for name in ("site_aap", "site_report_page", "site_banner", "site_time", "site_report_files", "site_festive"):
        if "ansible.module_utils." + name in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location("ansible.module_utils." + name, os.path.join(REPO, "plugins", "module_utils", name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["ansible.module_utils." + name] = mod
        spec.loader.exec_module(mod)
    import site_report_web as rw
    return rw


def big_fleet(n=45):
    """n servers, 3 By server rows each (two Administrators, one Remote Desktop Users): past the page's first 100 rows."""
    import test_win_local_groups as t
    import win_local_groups_filters as w
    hd = t.mem("CORP\\Helpdesk-RDP", t.D + "-4202")
    return w.win_local_groups_model([{"host": "SRV-%03d.corp.example.mil" % i,
                                      "result": t.result("SRV-%03d" % i, [t.admins("SRV-%03d" % i, i), t.rdp([hd])])} for i in range(n)], "auto")


HARNESS = """<pre id="out"></pre><script>
window.addEventListener('load',function(){setTimeout(function(){var R={errors:E};
  var sec=document.querySelector('details.sec[data-sel-export]');R.bar=!!(sec&&sec.querySelector('.selx'));
  if(sec){var all=sec.querySelector('.selx .sela input');all.click();
    var tb=sec.querySelector('.tw table');R.rows=tb.querySelectorAll('tbody tr[data-sel]').length;
    R.ticked=tb.querySelectorAll('tbody tr[data-sel] input.selc:checked').length;
    R.count=sec.querySelector('.selx .seln').textContent}
  document.getElementById('out').textContent=JSON.stringify(R)},300)});
</script>"""


class Review1122(unittest.TestCase):
    """1.122.2: Select all shown takes every row (past the first 100 too), on every web look (atlas, brief, ops); a By
    server row's checks approve it on that server only; the website hosts the files the job promises."""

    def test_by_server_rows_carry_the_server(self):
        import win_local_groups_filters as w
        m = big_fleet(2)
        sec = [x for x in w.win_local_groups_sections(m, True) if x["title"] == "By server"][0]
        sels = [json.loads(t["data"]["sel"]) for t in sec["row_tags"] if t.get("data")]
        da = [x for x in sels if x["m"] == "CORP\\Domain Admins"][0]
        self.assertEqual((da["s"], da["f"], da["c"]), ("SRV-000.corp.example.mil", "SRV-000.corp.example.mil", "SRV-000"))

    @unittest.skipUnless(shutil.which("node"), "no node")
    def test_a_by_server_row_is_approved_on_its_server_only(self):
        rows = [{"g": "Administrators", "e": "CORP\\SQL-Admins", "s": "srv-sql01.corp.example.mil", "f": "SRV-SQL01.corp.example.mil", "c": "SRV-SQL01"},
                {"g": "Administrators", "e": "CORP\\Ops", "s": "wg01", "f": "", "c": "WG01"},                  # a workgroup server: its name
                {"g": "Administrators", "e": "CORP\\Odd", "s": "x", "f": "", "c": "NÄME"},              # neither: left out
                {"g": "Administrators", "e": "CORP\\Domain Admins", "x": {"SRV-A": "S-1-5-21-1-1-1-512"}}]    # By member: every server
        env = dict(os.environ, ROWS=json.dumps(rows))
        with open(JS) as fh:
            pure = re.search(r"/\* sel-pure-start \*/(.*?)/\* sel-pure-end \*/", fh.read(), re.S).group(1)
        r = subprocess.run(["node", "-e", pure + "\nvar e=SelX.admins(JSON.parse(process.env.ROWS));process.stdout.write(JSON.stringify([e,e.left]));"],
                           capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        e, left = json.loads(r.stdout)
        self.assertEqual(e, [["CORP\\SQL-Admins", "", "SRV-SQL01.corp.example.mil"], ["CORP\\Ops", "WG01", ""], ["CORP\\Domain Admins", "", ""]])
        self.assertEqual(left, 1)
        import win_local_groups_filters as w
        self.assertIn('"CORP\\SQL-Admins","","SRV-SQL01.corp.example.mil"', w.check_data([tuple(x) for x in e]))

    def test_the_website_hosts_the_promised_files(self):
        _modules()
        rf = sys.modules["ansible.module_utils.site_report_files"]
        import tempfile
        d = tempfile.mkdtemp()
        items = []
        for n in ("local-groups.json", "approved-list.yml", "check-local-admins.ps1"):
            with open(os.path.join(d, n), "w") as fh:
                fh.write("x\n")
            items.append({"path": os.path.join(d, n), "filename": n})
        base = [".xml", ".docx", ".xlsx", ".csv", ".md", ".txt", ".pdf"]
        ok, refused = rf.prepare(items, 20, base + [".json", ".yml", ".ps1"])
        self.assertEqual(refused, [])
        self.assertEqual([x["ctype"] for x in ok], ["application/json", "text/plain; charset=utf-8", "text/plain; charset=utf-8"])
        self.assertEqual(len(rf.prepare(items, 20, base)[1]), 3)                                          # the default list alone: refused
        with open(os.path.join(REPO, "playbooks", "win_local_groups.yml")) as fh:
            self.assertIn("_report_web_files_more: ['.json', '.yml', '.ps1', '.txt']", fh.read())
        for f in ("main.yml", "copy_page.yml"):
            with open(os.path.join(REPO, "roles", "site_email", "tasks", f)) as fh:
                self.assertIn(("+ _report_web_files_more | default([])" if f == "main.yml" else "+ _copy_doc.data.files_more | default([])"), fh.read(), f)

    def test_select_all_in_a_browser_every_look(self):
        b = next((shutil.which(x) for x in ("google-chrome", "chromium", "chromium-browser") if shutil.which(x)), None)
        if not b:
            self.skipTest("no headless browser here")
        import site_filters as sf
        import tempfile
        import win_local_groups_filters as w
        from test_report_hover import render
        rw = _modules()
        m = big_fleet()
        by = dict((s["host"], w.win_local_groups_findings(m, s["host"])) for s in m["read"])
        r = sf.findings_report(by, "Windows - local group members", [], ["local groups"], w.win_local_groups_sections(m, True))
        n = len([x for x in r["sections"] if x["title"] == "By server"][0]["rows"])
        self.assertGreater(n, 120)
        for look in ("atlas", "brief", "ops"):
            raw = render("report_%s.html.j2" % look, r)
            js = rw.page_script(raw)
            page = rw.deferred_page(raw, "page.html", js, rw.SCRIPT)
            self.assertIn('class="defer-main"', page, look)
            page = page.replace("<head>", "<head><script>var E=[];window.addEventListener('error',function(e){E.push(String(e.message))})</script>", 1)
            page = page.replace("</body>", HARNESS + "</body>", 1)
            with tempfile.TemporaryDirectory() as d:
                with open(os.path.join(d, "page.html"), "w") as fh:
                    fh.write(page)
                with open(os.path.join(d, rw.report_script_file(js)), "w") as fh:
                    fh.write(js)
                out = subprocess.run([b, "--headless=new", "--disable-gpu", "--no-sandbox", "--window-size=1280,1200",
                                      "--virtual-time-budget=6000", "--dump-dom", "file://" + os.path.join(d, "page.html")],
                                     capture_output=True, text=True, timeout=120)
            mm = re.search(r'<pre id="out">(.*?)</pre>', out.stdout, re.S)
            self.assertTrue(mm and mm.group(1), (look, out.stderr[-800:]))
            import html as htmllib
            res = json.loads(htmllib.unescape(mm.group(1)))
            self.assertEqual(res["errors"], [], look)
            self.assertTrue(res["bar"], look)                                                         # brief and ops too
            self.assertEqual((res["rows"], res["ticked"]), (n, n), look)                             # every row, not the first 100
            self.assertEqual(res["count"], "%d selected" % n, look)


if __name__ == "__main__":
    unittest.main()
