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


if __name__ == "__main__":
    unittest.main()
