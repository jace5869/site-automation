#!/usr/bin/env python3
"""The fapolicyd denials report, easy to scan (1.67.0; plugins/filter/fapolicyd_filters.py, playbooks/fapolicyd_denials.yml,
the report website's dropdown filters and rail: false in both looks): At a glance first, ONE table of the denials grouped
by program and file (count, hosts, last seen, ACT's verdict) with a host and a program filter, the hosts with each one's ACT
answer closed under it and out of the rail, a short email, the workbook. A made-up fleet: tests/fapolicyd_fleet.py.
  python3 tests/test_denials_report.py"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
import fapolicyd_filters as ff  # noqa: E402
import fapolicyd_fleet as fleet  # noqa: E402
import test_report_fit as T  # noqa: E402

BIG = "app-server-long-name-07.subdomain.example.mil"


def docs():
    hvs, names = fleet.hostvars()
    return (ff.fapolicyd_denials_report(hvs, names, "today", "fapolicyd denials", True), ff.fapolicyd_denials_report(hvs, names, "today", "fapolicyd denials", False),
            ff.fapolicyd_denials_sheets(hvs, names))


class Model(unittest.TestCase):
    def setUp(self):
        self.web, self.mail, self.sheets = docs()
        self.s = dict((x["title"], x) for x in self.web["sections"])

    def test_at_a_glance_first(self):
        self.assertEqual([x["label"] for x in self.web["summary"]][:3], ["Denials", "Hosts with denials", "Programs denied"])
        self.assertEqual([x["value"] for x in self.web["summary"]][:3], [74, 5, 4])
        self.assertEqual(self.web["sections"][0]["title"], "At a glance")
        top = self.web["sections"][0]["lines"]
        self.assertEqual(top[0], "Top programs: bash 39 (4 hosts), python3 28 (2 hosts), java 4 (1 host), sh 3 (1 host)")
        self.assertEqual(top[1], "Top hosts: app-server-long-name-07 27, web01 20, db01 14, web02 9, app02 4")

    def test_one_table_of_denials_by_program_and_file(self):
        t = self.s["Denials by program and file"]
        self.assertEqual(t["columns"], ["Program", "File denied", "Denials", "Hosts", "Last seen", "ACT's verdict"])
        self.assertEqual([r[:3] for r in t["rows"]], [["bash", "/opt/monitor/agent", 39], ["python3", "/var/tmp/backup.py", 28],
                                                      ["java", "/opt/app/lib/plugin.jar", 4], ["sh", "/srv/db/tools/vacuum.sh", 3]])
        self.assertEqual(t["rows"][0][3], "4: app-server-long-name-07, db01 ...")
        self.assertEqual(t["tips"][0][3], ", ".join(sorted(["web01.example.mil", "web02.example.mil", "db01.example.mil", BIG])))
        self.assertEqual(t["rows"][0][4], "2026-11-15 09:15")                                  # the latest of the hosts' last denials
        self.assertEqual([r[5] for r in t["rows"]], ["Fix proposed", "Analysed", "Analysed", "Analysed"])   # ACT proposed adding the agent
        self.assertEqual(t["cell_status"][0][5], "info")

    def test_a_host_filter_and_a_program_filter(self):
        t = self.s["Denials by program and file"]
        self.assertEqual([(f["label"], f["options"]) for f in t["filters"]],
                         [("Host", ["app-server-long-name-07", "app02", "db01", "web01", "web02"]), ("Program", ["bash", "java", "python3", "sh"])])
        self.assertEqual(t["row_filters"][1], [["app-server-long-name-07", "web01"], ["python3"]])
        self.assertEqual(len(t["row_filters"]), len(t["rows"]))

    def test_the_hosts_and_each_hosts_answer_closed_and_out_of_the_rail(self):
        h = self.s["Hosts with denials or a problem"]
        self.assertEqual(h["columns"], ["Host", "fapolicyd", "auditd", "Denials", "Programs", "ACT"])
        self.assertEqual(h["rows"][0][:4], [BIG, "active", "active", 27])
        self.assertEqual(h["rows"][0][4:], ["bash, python3", "Analysed"])
        details = [x for x in self.web["sections"] if x["title"].endswith("ACT's analysis")]
        # 1.82.0: web01 and db01 answered with findings that name files: cards. The three others' answers name none: their text, closed.
        self.assertEqual(sorted(x["title"] for x in details), ["app-server-long-name-07: ACT's analysis", "app02: ACT's analysis", "web02: ACT's analysis"])
        self.assertTrue(all(x["collapsed"] and x["rail"] is False for x in details))
        rail = [x["title"] for x in self.web["sections"] if x.get("rail", True)]
        self.assertEqual(rail, ["At a glance", "Denials by program and file", "Hosts with denials or a problem", "What to do first",
                                "Why the denials: causes and fixes", "No denials"])

    def test_acts_answers_as_cause_cards(self):
        sec = self.s["Why the denials: causes and fixes"]
        self.assertEqual((sec["noun_label"], sec["count_text"]), ("File", "3 causes · 4 denied files"))
        self.assertIn("ISSO", sec["cause_note"])                                               # the approval, said once
        cards = sec["causes"]
        self.assertEqual([c["patterns"] for c in cards], [["D1"], ["D2"], ["D4"], ["D3"]])
        first = cards[0]
        self.assertEqual(first["hosts"], ["app-server-long-name-07", "db01", "web01", "web02"])          # every host that was denied it
        self.assertEqual([s["cmd"] for s in first["steps"]][:2], ["fapolicyd-cli --file add /opt/monitor/agent --trust-file monitor", "fapolicyd-cli --update"])
        self.assertTrue(first["evidence"][0]["m"].startswith("the same agent binary") or "SHA-256" in first["evidence"][0]["m"])      # ACT's own reading first
        self.assertTrue(any(e["m"].startswith("denied /usr/bin/bash -> /opt/monitor/agent") for e in first["evidence"]))     # then the denial lines
        self.assertTrue(cards[3]["unexplained"] and cards[3]["steps"] == [])                  # plugin.jar: no answer named it
        self.assertEqual(self.s["What to do first"]["cause_first"]["n"], 1)
        # the email: the first step and the table, no cards
        mail = dict((x["title"], x) for x in self.mail["sections"])
        self.assertIn("What to do first", mail)
        self.assertEqual(mail["Why the denials: causes and fixes"]["columns"][2], "Files")

    def test_no_card_when_acts_answers_name_no_file(self):
        hvs, names = fleet.hostvars()
        for hv in hvs:
            if "act_triage_result" in hv:
                hv["act_triage_result"] = dict(hv["act_triage_result"], summary=fleet.MD)
        r = ff.fapolicyd_denials_report(hvs, names, "today", "fapolicyd denials", True)
        titles = [x["title"] for x in r["sections"]]
        self.assertNotIn("What to do first", titles)
        self.assertEqual(sum(1 for t in titles if t.endswith("ACT's analysis")), 5)                  # every host keeps its text

    def test_the_fapolicyd_jobs_report_shows_the_cards_too(self):
        """1.82.0: the job's one report (roles/fapolicyd): its blocks over the hosts are the items; the website's page has the cards, the email the first step and the table."""
        block = {"exe": "/usr/bin/bash", "path": "/opt/monitor/agent", "perm": "execute", "kind": "denied", "count": 5, "last": "09:12"}
        hvs = [{"_fr": {"state": "read", "active": "active", "blocks": [block]}, "_fa_act_ran": True,
                "act_triage_result": {"status": "done", "summary": fleet.MD_DB}}]
        web = ff.fapolicyd_all_report(hvs, ["web01.example.mil"], "today", "fapolicyd", False, False, True)
        mail = ff.fapolicyd_all_report(hvs, ["web01.example.mil"], "today", "fapolicyd", False, False, False)
        w = dict((x["title"], x) for x in web["sections"])
        m = dict((x["title"], x) for x in mail["sections"])
        cards = w["Why the denials: causes and fixes"]["causes"]
        self.assertEqual([c["patterns"] for c in cards], [["D1"]])
        self.assertEqual(cards[0]["steps"][0]["cmd"], "fapolicyd-cli --file add /opt/monitor/agent --trust-file monitor")
        self.assertNotIn("causes", m["Why the denials: causes and fixes"])
        self.assertEqual(m["Why the denials: causes and fixes"]["columns"][2], "Files")
        self.assertIn("What to do first", m)
        # the answer also names /srv/db/tools/vacuum.sh, which this host never blocked: that finding is not placed, so the host keeps its text
        self.assertIn("web01.example.mil: ACT's analysis", w)

    def test_the_head_is_set_by_the_report(self):
        self.assertEqual(self.web["head_counts"], [[5, "hosts with denials", "warning"], [1, "auditd not running", "warning"]])
        self.assertEqual(self.web["head_scope"][:2], ["7 hosts read", "denials since today"])

    def test_the_email_is_short(self):
        titles = [x["title"] for x in self.mail["sections"]]
        self.assertEqual(titles, ["The denials that happen most", "Hosts with denials or a problem", "What to do first", "Why the denials: causes and fixes", "No denials"])
        self.assertNotIn("filters", self.mail["sections"][0])
        self.assertFalse(any(x["title"].endswith("ACT's analysis") for x in self.mail["sections"]))
        self.assertEqual([x["label"] for x in self.mail["summary"]], [x["label"] for x in self.web["summary"]])        # the same tiles
        hvs, names = fleet.hostvars()
        many = ff.fapolicyd_denials_report(hvs, names, "today", "", False, top=2)
        self.assertEqual(len(many["sections"][0]["rows"]), 2)
        self.assertIn("(2 more)", many["sections"][0]["text"])

    def test_the_workbook_has_all_of_it(self):
        d, h = self.sheets
        self.assertEqual((d["name"], len(d["rows"])), ("Denials by program and file", 4))
        self.assertEqual(d["rows"][0][:3], ["/usr/bin/bash", "/opt/monitor/agent", 39])         # the full path, every host
        self.assertIn(BIG, d["rows"][0][3])
        self.assertEqual((h["name"], len(h["rows"])), ("Hosts", 7))

    def test_a_host_without_its_audit_text_has_totals_only(self):
        hvs, names = fleet.hostvars()
        for hv in hvs:
            hv.pop("_fa_denials")
        r = ff.fapolicyd_denials_report(hvs, names, "today", "", True)
        self.assertNotIn("Denials by program and file", [x["title"] for x in r["sections"]])
        self.assertIn("74 denial(s) on 5 host(s)", " ".join(l for x in r["sections"] for l in x.get("lines", [])))
        self.assertEqual(r["summary"][0]["value"], 74)

    def test_nothing_denied(self):
        hv = {"_fa_rpm": {"rc": 0}, "_fa_active": {"stdout": "active"}, "_fa_auditd": {"stdout": "active"}, "_fa_count": "0", "_fa_paths": [],
              "_fa_exes": [], "_fa_findings": [], "_fa_denials": {"stdout": "", "rc": 1}}
        r = ff.fapolicyd_denials_report([hv], ["lin01.example.mil"], "today", "", True)
        self.assertEqual([x["title"] for x in r["sections"]], ["No denials"])
        self.assertTrue(r["title"].endswith("no denials since today"))


class Pages(unittest.TestCase):
    def setUp(self):
        self.web, _, _ = docs()

    def test_the_filters_and_the_rail_in_both_looks(self):
        for look in ("atlas", "brief", "ops"):
            h = T.render(look, self.web)
            self.assertEqual(h.count('<select data-f='), 2, look)
            self.assertIn('<option>bash</option>', h)
            self.assertIn('data-f0="|app-server-long-name-07|web01|" data-f1="|python3|"', h)
            rail = h[h.index('<nav class="card rail"'):]
            rail = rail[:rail.index("</nav>")]
            self.assertIn("At a glance", rail)
            self.assertNotIn("ACT&#39;s analysis", rail.replace("'", "&#39;"))                  # the rail lists the sections, not the hosts
            self.assertEqual(rail.count("<a href"), 6, look)                                   # the six sections: with the two of the cards (not the hosts' texts)

    def test_a_report_without_filters_is_as_before(self):
        plain = {"title": "T", "status": "ok", "summary": [], "sections": [{"title": "A", "columns": ["x", "y"], "rows": [["1", "2"]]}]}
        for look in ("atlas", "brief", "ops"):
            h = T.render(look, plain)
            self.assertNotIn("<select", h)
            self.assertNotIn(' data-f0=', h)


@unittest.skipUnless(T._browser(), "no headless browser here")
class Behaviour(unittest.TestCase):
    """The page's script: choosing a host or a program keeps the rows that have it, together; the count says so; CSV and the
    sort act on what is shown (a headless browser)."""
    def run_page(self, look, js):
        web, _, _ = docs()
        with tempfile.TemporaryDirectory() as d:
            html = T.render(look, web).replace("</body>", "<pre id='out'></pre><script>addEventListener('load',function(){setTimeout(function(){try{%s}catch(e){document.getElementById('out').textContent='ERR '+e}},600)})</script></body>" % js, 1)
            html = html.replace('src="site-report.js"', 'src="file://%s"' % os.path.join(REPO, "roles", "site_email", "templates", "site_report.js"))
            p = os.path.join(d, "p.html")
            open(p, "w").write(html)
            prof = tempfile.mkdtemp()
            out = subprocess.run([T._browser(), "--headless=new", "--disable-gpu", "--no-sandbox", "--user-data-dir=" + prof, "--allow-file-access-from-files",
                                  "--window-size=1280,900", "--virtual-time-budget=8000", "--dump-dom", "file://" + p], capture_output=True, text=True, timeout=120).stdout
            shutil.rmtree(prof, ignore_errors=True)
        m = re.search(r"<pre id=\"out\">(.*?)</pre>", out, re.S)
        return m.group(1) if m else out[-400:]

    def test_the_dropdowns_filter_the_rows(self):
        js = ("var sec=document.querySelectorAll('details.sec')[1]||document.querySelector('details.sec');"
              "var tb=[].filter.call(document.querySelectorAll('table'),function(t){return t.querySelector('th')&&/Program/.test(t.textContent)&&t.tBodies[0].rows.length==4})[0];"
              "var tools=tb.parentNode.previousElementSibling;var sel=tools.querySelectorAll('select[data-f]');"
              "function vis(){return [].filter.call(tb.tBodies[0].rows,function(r){return !r.hidden}).map(function(r){return r.cells[0].textContent.trim()}).join(',')}"
              "var o=[tools.hidden,sel[0].parentNode.hidden,vis()];"
              "sel[0].value='web01';sel[0].dispatchEvent(new Event('change'));o.push(vis());"
              "sel[1].value='python3';sel[1].dispatchEvent(new Event('change'));o.push(vis());"
              "o.push(tools.querySelector('.shown').textContent);"
              "sel[0].value='';sel[0].dispatchEvent(new Event('change'));o.push(vis());"
              "sel[1].value='';sel[1].dispatchEvent(new Event('change'));o.push(vis());"
              "document.getElementById('out').textContent=o.join(' | ')")
        for look in ("atlas", "brief", "ops"):
            self.assertEqual(self.run_page(look, js), "false | false | bash,python3,java,sh | bash,python3 | python3 | 1 of 4 | python3 | bash,python3,java,sh", look)


if __name__ == "__main__":
    unittest.main()
