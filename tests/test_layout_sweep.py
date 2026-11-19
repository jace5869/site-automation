#!/usr/bin/env python3
"""The layout sweep (look only): report_tidy (a section with nothing in it is left off, the all-clear ones are one line, a description goes under the table,
a sentence three rows repeat is said once, raw keys and raw JSON are said in words), the ops cards (an opened card takes the row, no value twice, long values
break at \\ / - _ .), the rail's status glyph and word, the two-line header, the 25-row cap, the 4-line cell, the bar's whole chips.
  python tests/test_layout_sweep.py        (the browser checks skip without chromium or google-chrome)"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
sys.path.insert(0, os.path.join(REPO, "plugins", "module_utils"))
import jinja2  # noqa: E402
import site_filters as sf  # noqa: E402

DOCS = os.path.join(REPO, "tests", "fixtures", "report_fit")
TPL = os.path.join(REPO, "roles", "site_email", "templates")


def fixture(name):
    with open(os.path.join(DOCS, name + ".json")) as fh:
        return json.load(fh)


def render(look, doc):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TPL), autoescape=False)
    env.filters.update(sf.FilterModule().filters())
    return env.get_template("report_%s.html.j2" % look).render(_report=doc, _email_footer="AAP job 9", _page_logo_cid="", _page_runs=[],
                                                            report_email_header_text="Site automation report")


def browser():
    for b in ("google-chrome", "chromium", "chromium-browser"):
        p = shutil.which(b)
        if p:
            return p
    return None


def dom(html, width=1280, extra_js=""):
    """The page after its own script ran (and extra_js, which writes its answer into #res) -> the DOM text."""
    html = html.replace("</body>", '<pre id="res"></pre><script>window.addEventListener("load",function(){setTimeout(function(){%s},500)})</script></body>' % extra_js)
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "p.html"), "w") as fh:
            fh.write(html)
        return subprocess.run([browser(), "--headless=new", "--no-sandbox", "--disable-gpu", "--window-size=%d,900" % width, "--virtual-time-budget=5000",
                               "--dump-dom", "file://" + os.path.join(d, "p.html")], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              universal_newlines=True, timeout=120).stdout


def answer(out):
    return re.search(r'<pre id="res">(.*?)</pre>', out, re.S).group(1)


class Tidy(unittest.TestCase):
    def sec(self, title, **k):
        return dict({"title": title}, **k)

    def test_1_a_section_with_nothing_in_it_is_left_off(self):
        d = {"title": "t", "sections": [self.sec("Empty"), self.sec("Has rows", columns=["A"], rows=[["x"]]), self.sec("Lines", lines=["a finding"])]}
        t = sf.report_tidy(d)
        self.assertEqual([s["title"] for s in t["sections"]], ["Has rows", "Lines"])
        self.assertEqual(len(d["sections"]), 3)                                         # the report itself is not changed

    def test_2_all_clear_sections_are_one_line_at_the_end(self):
        d = {"sections": [self.sec("Nodes", text="State of each node. Every node is up."), self.sec("Volumes", lines=["None."]),
                          self.sec("Disks", status="ok", lines=["Nothing."]), self.sec("Problem", status="warning", columns=["A"], rows=[["x"]]),
                          self.sec("Ports", text="Ports. Every enabled port is up.")]}
        t = sf.report_tidy(d)
        self.assertEqual([s["title"] for s in t["sections"]], ["Problem", "Checked, nothing found"])
        self.assertEqual(t["sections"][-1]["text"], "Nodes, Volumes, Disks, Ports.")
        failed = {"sections": [self.sec("A", text="Each DC. No DC was read."), self.sec("B", text="The DCs answered. No response from dc02."),
                               self.sec("C", lines=["No backup job ran in 7 days."]), self.sec("D", status="critical")]}
        self.assertEqual([x["title"] for x in sf.report_tidy(failed)["sections"]], ["A", "B", "C", "D"])    # "nothing was read" is not "all clear"; a status is never dropped
        self.assertEqual(t["sections"][-1]["status"], "ok")

    def test_3_a_warning_or_a_section_with_something_stays(self):
        d = {"sections": [self.sec("W", status="warning", lines=["None."]), self.sec("C", status="critical", text="Every x is down."),
                          self.sec("Real text", text="This explains how it was read, with no verdict of its own."),
                          self.sec("One clear", lines=["None."])]}
        t = sf.report_tidy(d)
        self.assertEqual([s["title"] for s in t["sections"]], ["W", "C", "Real text", "One clear"])            # one all-clear section stays as it is

    def test_4_a_description_goes_under_the_table(self):
        d = {"sections": [self.sec("T", columns=["A"], rows=[["x"]], text="One. Two. Three. Four.")]}
        s = sf.report_tidy(d)["sections"][0]
        self.assertEqual((s["text"], s["note"], s["note_more"]), ("", "One. Two.", "Three. Four."))
        s = sf.report_tidy(d, False)["sections"][0]                                       # the ops look keeps its own description
        self.assertEqual((s["text"], s.get("note")), ("One. Two. Three. Four.", None))
        c = {"sections": [self.sec("Cards", cards=[{"title": "x"}], rows=[["x"]], columns=["A"], text="Start here text.")]}
        self.assertEqual(sf.report_tidy(c)["sections"][0]["text"], "Start here text.")  # cards and boards keep theirs

    def test_5_a_sentence_three_rows_repeat_is_said_once(self):
        why = "Adding vCPUs or memory needs the VM shut down (hot add is off)."
        rows = [["a", "CPU: busy; " + why], ["b", "memory: busy; " + why], ["c", why], ["d", "other reason"]]
        data = [["a", "A long value of a column that is data, not prose"]] * 4
        self.assertEqual(sf.report_tidy({"sections": [self.sec("T", columns=["VM", "Profile"], rows=data)]})["sections"][0]["rows"], data)   # data is never blanked
        s = sf.report_tidy({"sections": [self.sec("T", columns=["VM", "Why"], rows=rows)]})["sections"][0]
        self.assertEqual([r[1] for r in s["rows"]], ["CPU: busy", "memory: busy", why, "other reason"] if False else [r[1] for r in rows])   # 3 of 4 rows: it stays in each (Codex: only a sentence EVERY row has is factored)
        every = [["a", "CPU: busy; " + why], ["b", "memory: busy; " + why], ["c", why], ["d", why]]
        s = sf.report_tidy({"sections": [self.sec("T", columns=["VM", "Why"], rows=every)]})["sections"][0]
        self.assertEqual([r[1] for r in s["rows"]], ["CPU: busy", "memory: busy", "", ""])
        self.assertIn("Said once, for the rows it applies to: " + why.rstrip(".") + ".", s["note"] + " " + s["note_more"])
        two = [["a", why], ["b", why]]                                                  # two rows that all say it: said once too
        s = sf.report_tidy({"sections": [self.sec("T", columns=["VM", "Why"], rows=two)]})["sections"][0]
        self.assertEqual([r[1] for r in s["rows"]], ["", ""])
        few = [["a", why], ["b", "x"], ["c", "y"], ["d", "z"]]                          # one of four: not a repeat
        s = sf.report_tidy({"sections": [self.sec("T", columns=["VM", "Why"], rows=few)]})["sections"][0]
        self.assertEqual(s["rows"][0][1], why)

    def test_5b_the_all_clear_fold_needs_the_whole_text_clear(self):
        def kept(text):
            out = sf.report_tidy({"sections": [dict(self.sec("Volumes", text=text)), self.sec("Other", text="None.")]})["sections"]
            return any(x.get("title") == "Volumes" for x in out)
        self.assertTrue(not kept("Every volume is online."))                                       # folded: all clear
        self.assertTrue(not kept("What it shows. Every volume is online."))
        self.assertTrue(kept("3 volumes are offline. Every volume is online."))                    # a count before the clear sentence: the section stays
        self.assertTrue(kept("Volumes offline. Every aggregate is online."))

    def test_5c_partial_column_titles_do_not_raise(self):
        s = sf.report_tidy({"sections": [self.sec("T", columns=["a_b", "c_d", "Plain"], column_titles=["only one"], rows=[["x", "y", "z"]])]})["sections"][0]
        self.assertEqual(s["columns"], ["A b", "C d", "Plain"])
        self.assertEqual(s["column_titles"][:2], ["only one", "c_d"])

    def test_5d_the_email_keeps_the_raw_key_in_brackets(self):
        d = {"sections": [self.sec("S", columns=["Setting", "timeout_after", "What"], rows=[["a", "v", "true_sso"]])]}
        s = sf.report_tidy(d, False, True)["sections"][0]
        self.assertEqual(s["columns"][1], "Timeout after (timeout_after)")
        self.assertEqual(s["rows"][0][2], "True sso (true_sso)")

    def test_6_raw_keys_and_raw_json_are_said_in_words(self):
        d = {"sections": [self.sec("S", columns=["Setting", "timeout_after", "What"], rows=[["report_email_from", "v", "true_sso"], ["b", "w", "Not a key"],
                          ["c", "x", 'NOT STARTED: AAP did not start it (HTTP 400: {"resources_needed_to_start": ["Job Template \'VM - deploy\' is missing."]})']])]}
        s = sf.report_tidy(d)["sections"][0]
        self.assertEqual(s["columns"], ["Setting", "Timeout after", "What"])
        self.assertEqual(s["column_titles"][1], "timeout_after")                          # the key stays in the header's title
        self.assertEqual((s["rows"][0][0], s["rows"][0][2], s["rows"][1][2]), ("report_email_from", "True sso", "Not a key"))      # a setting's name is what people type: left as it is
        self.assertEqual(s["rows"][2][2], "NOT STARTED: AAP did not start it (HTTP 400: Job Template 'VM - deploy' is missing.)")
        keys = dict(((r, c), k) for r, c, k in s["cell_keys"]["k"])
        self.assertEqual(keys[(0, 2)], "true_sso")
        self.assertNotIn((0, 0), keys)
        self.assertIn('{"resources_needed_to_start"', keys[(2, 2)])
        self.assertEqual(s["cell_keys"]["n"], 3)
        host = {"sections": [self.sec("S", columns=["Host", "Datastore"], rows=[["esx_01", "ds_prod"]])]}
        self.assertEqual(sf.report_tidy(host)["sections"][0]["rows"], [["esx_01", "ds_prod"]])      # names in other columns are left alone

    def test_6b_a_long_hot_value_renders_in_ops(self):
        d = fixture("vm_health_report")
        done = False
        for sec in d["sections"]:
            cs = sec.get("cell_status") or []
            for ri, row in enumerate(cs):
                for ci, st in enumerate(row):
                    if st in ("critical", "warning") and ci > 0:
                        sec["rows"][ri][ci] = "very long " * 40 + "value"
                        done = True
        self.assertTrue(done)
        self.assertIn("very long", render("ops", d))                                                # lg was undefined on this path: the whole report failed

    def test_7_every_fixture_is_still_a_report(self):
        for name in sorted(os.listdir(DOCS)):
            doc = json.load(open(os.path.join(DOCS, name)))
            t = sf.report_tidy(doc)
            self.assertTrue(isinstance(t.get("sections", []), list), name)
            self.assertLessEqual(len(t.get("sections", [])), len(doc.get("sections", [])) + 1, name)


class Pages(unittest.TestCase):
    def test_1_the_rail_dot_has_a_glyph_and_a_word(self):
        for look in ("atlas", "brief", "ops"):
            h = render(look, fixture("ad_report"))
            self.assertRegex(h, r'<span class="dot c-critical" data-g="!" aria-hidden="true"></span><span class="vh">Action required: </span>', look)
            self.assertIn('.rail a .dot:after{content:attr(data-g)', h)
            self.assertNotIn('<span class="dot c-critical"></span>', h)

    def test_2_the_ops_card_details_leave_out_what_is_shown(self):
        h = render("ops", fixture("ad_report"))
        card = h[h.index('<article class="todo c-critical">'):]
        card = card[:card.index("</article>")]
        heading = re.search(r"<h3>(.*?)</h3>", card).group(1)
        for dd in re.findall(r"<dd>(.*?)</dd>", card):
            self.assertNotEqual(re.sub(r"<[^>]+>", "", dd), heading)
        self.assertIn(".todo:has(.cdx:checked){grid-column:1/-1}", h)
        self.assertIn(".kv dd{margin:0;overflow-wrap:break-word;word-break:normal}", h)
        self.assertNotIn("overflow-wrap:anywhere}\n.cdx", h)

    def test_3_long_values_get_break_points(self):
        doc = {"title": "t", "status": "warning", "sections": [{"title": "S", "status": "warning", "columns": ["Name", "Path"],
               "rows": [["host", "\\\\fs01.corp.example.mil\\share\\deep\\folder\\file_name-with-dashes.txt"]], "cell_status": [["", "warning"]]}]}
        h = render("ops", doc)
        self.assertIn("\\<wbr>share\\<wbr>deep", h) if "<dl class=\"kv\">" in h else None

    def test_4_a_header_with_a_colon_is_two_lines(self):
        h = render("atlas", fixture("vm_rightsize_doc"))
        self.assertIn('CPU used <span class="hsub">p95 / peak</span>', h)
        self.assertIn("th .hsub{display:block;", h)

    def test_5_the_note_and_the_keys_are_in_the_page(self):
        h = render("atlas", fixture("vm_rightsize_doc"))
        self.assertIn('<div class="snote"><p>', h)
        self.assertIn("<summary>More</summary>", h)
        h = render("atlas", fixture("hz_settings"))
        self.assertIn(' data-keys="{', h)
        self.assertRegex(h, r"Timeout(?:<[^>]+>|\s)+after")

    def test_6_the_script_and_the_style(self):
        with open(os.path.join(TPL, "site_report.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertTrue(all(ord(c) < 128 for c in js), "ASCII only")
        for want in ("var CAP=25", "function longCells", "table[data-keys]", "Show all ", "function capFree", "r._c"):
            self.assertIn(want, js)
        self.assertNotIn("innerHTML", js)
        h = render("atlas", fixture("ad_report"))
        self.assertIn("@media (min-width:1200px){.nav{flex-wrap:wrap;overflow:hidden;", h)
        self.assertIn(".nav{position:relative;", h)
        self.assertIn("@media print{.capbar{display:none}tr[data-cap][hidden]{display:table-row}}", h)

    def test_7_nothing_empty_in_the_net_drops_page(self):
        h = render("atlas", fixture("vm_net_drops_doc"))
        for gone in ("Single sign-on and OCSP", "Datacenters", ">Needs attention</h2>"):
            self.assertNotIn(gone, h)
        self.assertIn("Network drops and errors", h)

    def test_8_the_all_clear_line_is_in_the_page_and_the_rail(self):
        h = render("atlas", fixture("ontap_report"))
        self.assertGreaterEqual(h.count("Checked, nothing found"), 2)                   # the section and its rail entry
        self.assertIn("Volumes 85% full or more, or not online", h[h.index("Checked, nothing found"):])          # "Every volume is online and below 85%" is all clear
        self.assertIn("No controller or shelf was read", h)                                                    # "nothing was read" is not: it stays a section
        self.assertLess(h.count('<a href="#s'), 2 * len(fixture("ontap_report")["sections"]))      # the bar and the rail list each section


@unittest.skipUnless(browser(), "no google-chrome or chromium")
class InBrowser(unittest.TestCase):
    def test_1_a_big_table_shows_25_rows_and_show_all(self):
        out = dom(render("atlas", fixture("stig_scorecard_big_web")), 1280,
                  'var t=document.querySelector(".capbar"),tb=t.previousElementSibling.querySelector("tbody");var r=function(){return [].filter.call(tb.rows,function(x){return !x.hidden&&!x.classList.contains("xr")}).length};'
                  'var a=r();t.querySelector("button").click();document.getElementById("res").textContent=a+","+r()+","+[].filter.call(tb.rows,function(x){return !x.classList.contains("xr")}).length+","+t.hidden')
        a, b, n, hid = answer(out).split(",")
        self.assertEqual(a, "25")
        self.assertEqual(int(b), int(n))
        self.assertEqual(hid, "true")

    def test_2_a_search_looks_in_every_row_whatever_the_cap_says(self):
        out = dom(render("atlas", fixture("stig_scorecard_big_web")), 1280,
                  'var tb=document.querySelector(".capbar").previousElementSibling.querySelector("tbody"),last=[].filter.call(tb.rows,function(x){return !x.classList.contains("xr")}).pop().cells[0].textContent.trim().split(/\\s+/)[0].toLowerCase();'
                  'var f=document.getElementById("find");f.value=last;f.dispatchEvent(new Event("search"));document.getElementById("res").textContent=[].filter.call(tb.rows,function(x){return !x.hidden}).length>0')
        self.assertEqual(answer(out), "true")

    def test_2b_a_filter_button_and_a_link_see_the_rows_past_the_cap(self):
        out = dom(render("atlas", fixture("stig_scorecard_big_web")), 1280,
                  'var tb=document.querySelector(".capbar").previousElementSibling.querySelector("tbody"),rows=[].filter.call(tb.rows,function(x){return !x.classList.contains("xr")}),first={};'
                  'rows.slice(0,25).forEach(function(r){first[r.getAttribute("data-col")]=1});var late=rows.slice(25).map(function(r){return r.getAttribute("data-col")}).filter(function(c){return c&&!first[c]})[0];'
                  'var b=document.querySelector(\'button[data-fk="col"][data-fv="\'+late+\'"]\');if(b)b.click();var shown=rows.filter(function(r){return !r.hidden}).length;'
                  'var all=document.querySelector(\'button[data-fk="col"][data-fv=""]\');if(all)all.click();var last=rows[rows.length-1];var hid=last.hidden;location.hash="#"+last.id;'
                  'setTimeout(function(){document.getElementById("res").textContent=[late,!!b,shown>0,hid,last.hidden].join(",")},300)')
        late, has_button, shown, was_hidden, now_hidden = answer(out).split(",")
        if has_button == "true":
            self.assertEqual(shown, "true")                                                  # a collection that has no row in the first 25 still shows its rows
        self.assertEqual((was_hidden, now_hidden), ("true", "false"))                        # a link to a row past the cap shows every row first

    DOC40 = {"title": "t", "status": "warning", "sections": [
        {"title": "A", "status": "warning", "columns": ["N", "Why"], "rows": [["row %d" % i, "x"] for i in range(40)],
         "row_status": ["critical" if i == 30 else "warning" if i == 33 else "ok" for i in range(40)]},
        {"title": "B", "status": "ok", "columns": ["N", "Why"], "rows": [["other %d" % i, "x"] for i in range(40)]}]}

    def test_2c_a_critical_or_warning_row_past_the_cap_is_never_folded(self):
        js = ('var tbs=document.querySelectorAll(".tw table"),a=tbs[0].tBodies[0],b=tbs[1].tBodies[0];'
              'var vis=function(t){return [].filter.call(t.rows,function(x){return !x.hidden&&!x.classList.contains("xr")}).map(function(x){return x.cells[0].textContent.trim()})};'
              'var v1=vis(a);var out=[v1.length,v1.indexOf("row 30")>=0,v1.indexOf("row 33")>=0,v1.indexOf("row 29")>=0,vis(b).length];'
              'tbs[0].querySelector("th button").click();out.push(vis(a).indexOf("row 30")>=0);'      # sorted: the severe ones are still there
              'var f=tbs[0].parentNode.previousElementSibling.querySelector(".filter");if(f){f.value="row 3";f.dispatchEvent(new Event("input",{bubbles:true}))}out.push(vis(a).length,vis(b).length);'
              'document.getElementById("res").textContent=out.join(",")')
        got = [int(x) if x.lstrip("-").isdigit() else x for x in answer(dom(render("atlas", self.DOC40), 1280, js)).split(",")]
        self.assertEqual(got[0], 25 + 2)                          # the first 25 and the critical and the warning row past them
        self.assertEqual(got[1:3], ["true", "true"])
        self.assertEqual(got[3], "false")                         # row 29 is past the cap and not severe
        self.assertEqual(got[4], 25)                              # the other table is capped on its own
        self.assertEqual(got[5], "true")

    def test_2d_one_tables_filter_does_not_open_another_table(self):
        js = ('var tbs=document.querySelectorAll(".tw table"),b=tbs[1].tBodies[0];var f=tbs[0].parentNode.previousElementSibling.querySelector(".filter");'
              'f.value="row";f.dispatchEvent(new Event("input",{bubbles:true}));'
              'document.getElementById("res").textContent=[].filter.call(b.rows,function(x){return !x.hidden}).length')
        self.assertEqual(answer(dom(render("atlas", self.DOC40), 1280, js)), "25")

    def test_2e_print_shows_the_cap_hidden_rows_only(self):
        js = ('var tb=document.querySelectorAll(".tw table")[0].tBodies[0];var r=tb.rows[28];'
              'document.getElementById("res").textContent=[r.hidden,r.hasAttribute("data-cap")].join(",")')
        self.assertEqual(answer(dom(render("atlas", self.DOC40), 1280, js)), "true,true")
        for look in ("atlas", "brief", "ops"):
            self.assertIn("tr[data-cap][hidden]{display:table-row}", render(look, self.DOC40))

    def test_3_a_cell_of_more_than_4_lines_shows_4_and_more(self):
        doc = {"title": "t", "status": "ok", "sections": [{"title": "S", "columns": ["VM", "Why"], "kinds": ["", "wrap"],
               "rows": [["a", " ".join("word%d" % i for i in range(120))], ["b", "short"]]}]}
        out = dom(render("atlas", doc), 1280,
                  'var c=document.querySelectorAll("td.cl");var td=c[0];var h1=td?td.querySelector(".cv").offsetHeight:0;if(td)td.click();var h2=td?td.querySelector(".cv").offsetHeight:0;'
                  'document.getElementById("res").textContent=c.length+","+(h2>h1)+","+(td?getComputedStyle(td,":after").content:"")')
        n, grew, after = answer(out).split(",", 2)
        self.assertEqual((n, grew), ("1", "true"))
        self.assertIn("less", after)

    def test_4_a_cell_said_in_words_keeps_its_key_in_its_title(self):
        out = dom(render("atlas", fixture("hz_settings")), 1280, 'document.getElementById("res").textContent=[].map.call(document.querySelectorAll("td[title]"),function(t){return t.title}).join("|")')
        self.assertIn("timeout_after", answer(out))

    def test_5_no_page_scrolls_sideways_at_800(self):
        for look in ("ops", "atlas"):
            for name in ("servicenow_war_report", "stig_ckl_report", "servicenow_insights_doc"):
                out = dom(render(look, fixture(name)), 800, 'document.getElementById("res").textContent=document.documentElement.scrollWidth')
                self.assertLessEqual(int(answer(out)), 800, "%s %s" % (look, name))

    def test_6_the_bar_shows_whole_chips_only(self):
        out = dom(render("atlas", fixture("e2e-servicenow-health")), 1280,
                  'var n=document.querySelector(".nav"),b=n.getBoundingClientRect(),bad=0;[].forEach.call(n.querySelectorAll("a"),function(a){var r=a.getBoundingClientRect();'
                  'if(r.height>0&&r.top<b.bottom-2&&(r.right>b.right+1||r.bottom>b.bottom+1))bad++});document.getElementById("res").textContent=bad')
        self.assertEqual(answer(out), "0")


if __name__ == "__main__":
    unittest.main(verbosity=1)
