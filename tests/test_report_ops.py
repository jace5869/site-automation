"""1.69.0: the report website's ops look (report_web_theme: ops, roles/site_email/templates/report_ops.html.j2), rendered
with plain Jinja: the header card (status chip, verdict, headline number, SHOW), the status tiles (the STIG scorecard's
CAT tiles kept), Start here as up to 3 ranked cards (Open item, View details), the sections as cards (chip, count,
footer, Explore), short ones side by side, the bar list, the palette (validated violet pair, status colours with words),
strict CSP (no inline handler, nothing from elsewhere) and the parts site_report.js works on.
  python3 tests/test_report_ops.py"""
import json
import os
import re
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
import site_filters as sf  # noqa: E402

TPL = os.path.join(REPO, "roles", "site_email", "templates")


def render(doc, **v):
    import jinja2
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TPL), autoescape=False)
    env.filters.update(report_cell_html=sf.report_cell_html, report_sparkline=sf.report_sparkline, report_runway_bar=sf.report_runway_bar, report_hidden_columns=sf.report_hidden_columns,
                       safe_link=sf.safe_link, report_tidy=sf.report_tidy, safe_ref=sf.safe_ref, safe_site_url=sf.safe_site_url, safe_data=sf.safe_data)
    base = {"_report": doc, "_email_footer": "AAP job 9 (Linux - Disk), started by admin, 2026-11-17 06:00 UTC.", "_page_logo_cid": "",
            "_page_runs": [], "_page_template": "Linux - Disk <space>", "_page_job": "9", "report_email_header_text": "Site <automation> report"}
    base.update(v)
    return env.get_template("report_ops.html.j2").render(**base)


def fixture(name):
    with open(os.path.join(REPO, "tests", "fixtures", "report_fit", name + ".json")) as f:
        return json.load(f)


DOC = {"title": "Disk report: 2 <full>", "status": "critical", "subtitle": "<b>sub</b> read 06:00",
       "summary": [{"label": "Hosts", "value": 12, "note": "read"}, {"label": "Past <90%>", "value": 2, "status": "critical"},
                   {"label": "Past 85%", "value": 0, "status": "warning"}, {"label": "OK", "value": 10, "status": "ok"},
                   {"label": "Skipped", "value": 1}],
       "sections": [
           {"title": "Start here", "status": "critical", "cards": [
               {"status": "critical", "chip": "Critical", "meta": "Filesystem", "title": "<t>", "subject": "web01, /var", "next": "Free space:",
                "code": "du -xh </x>"},
               {"status": "warning", "title": "two", "subject": "db01"}, {"status": "warning", "title": "three"},
               {"status": "warning", "title": "four"}]},
           {"title": "Filesystems", "status": "critical", "columns": ["Host", "Mount", "Used", "Inodes", "Size", "Status"],
            "rows": [["web01", "/var", "95%", "1%", "40 GB", "late"], ["db01", "/data", "86.5%", "91%", "2 TB", "FAIL"]],
            "row_status": ["critical", "warning"], "cell_status": [["", "", "critical", "", "", ""], ["", "", "warning", "critical", "", ""]],
            "text": "Every file system past its level. " * 10, "actions": [{"label": "Clean up", "url": "https://portal.example.mil/portal/x"}]},
           {"title": "By site", "columns": ["Site", "Hosts"], "rows": [["SITEA", "8"], ["SITEB", "4"]]},
           {"title": "Notes", "lines": ["one <line>", "two"]},
           {"title": "Hosts", "columns": ["Host", "OS"], "rows": [["web01", "RHEL 9"], ["db01", "RHEL 8"]]}]}


class Header(unittest.TestCase):
    def test_the_header_card(self):
        h = render(DOC)
        self.assertIn('<header class="hero card h-critical">', h)
        self.assertIn('<span class="stc s-critical"><span class="d" aria-hidden="true"></span>Critical</span>\n<h1>Disk report: 2 &lt;full&gt;</h1>', h)
        self.assertIn('<p class="say">Review what needs attention, worst first.</p>', h)
        # the headline number: the first tile that is Critical and not 0 (not the first tile)
        self.assertIn('<div class="hn"><div class="bn c-critical">2</div><div class="cap">Past &lt;90%&gt;</div></div>', h)
        # the strip: the subtitle and SHOW; under it the job, who and when
        self.assertIn('<summary><span class="ln1">&lt;b&gt;sub&lt;/b&gt; read 06:00</span><span class="tog" aria-hidden="true"><span class="sh">SHOW</span>', h)
        self.assertIn('<span class="eyebrow">Linux - Disk &lt;space&gt;</span><span>AAP job 9</span>', h)
        self.assertIn("<span>2026-11-17 06:00 UTC</span>", h)

    def test_words_for_each_status(self):
        for st, word in (("ok", "Healthy"), ("warning", "Warning"), ("info", "Not rated"), ("unknown", "Unavailable"), ("low", "Low")):
            h = render(dict(DOC, status=st, summary=[], sections=[]))
            self.assertIn('<span class="stc s-%s"><span class="d" aria-hidden="true"></span>%s</span>' % (st, word), h)
        self.assertNotIn('class="say"', render(dict(DOC, status="info", summary=[], sections=[])))   # informs: no line to act
        self.assertNotIn('class="hn"', render(dict(DOC, summary=[], sections=[])))                 # no number: no right side

    def test_headline_falls_back(self):
        h = render(dict(DOC, summary=[{"label": "Hosts", "value": "2 of 2", "status": "ok"}, {"label": "Read", "value": 5}]))
        self.assertIn('<div class="bn">5</div><div class="cap">Read</div>', h)            # no Critical/Warning: the first number


class Tiles(unittest.TestCase):
    def test_status_tiles(self):
        h = render(DOC)
        self.assertEqual(len(re.findall(r'<div class="tile[ "]', h)), 5)
        self.assertIn('<div class="k"><span class="d c-critical" aria-hidden="true"></span>Past &lt;90%&gt;</div>', h)
        self.assertIn('<div class="n">Critical - needs action</div>', h)                  # a line of what it means, its word
        self.assertIn('<div class="n">None</div>', h)                                     # Warning 0: none
        self.assertIn('<div class="n">read</div>', h)                                     # its own note first
        self.assertIn(".tiles{display:grid;grid-template-columns:repeat(4,minmax(0,1fr))", h)

    def test_the_scorecard_keeps_its_cat_tiles_and_links(self):
        doc = fixture("stig_scorecard_open_web")
        # 1.71.0: with its dashboard, the CAT tiles are the dashboard's (At a glance); without one, the ops look's own
        hd = render(doc)
        for k in (1, 2, 3):
            self.assertRegex(hd, r'<div class="dt dsum k%d[^"]*" data-t="sum">' % k)
        self.assertNotIn('<div class="tile tc k1', hd)
        doc = dict(doc)
        doc.pop("dashboard", None)
        h = render(doc)
        for k, label in ((1, "CAT I open"), (2, "CAT II open"), (3, "CAT III open")):
            self.assertRegex(h, r'<div class="tile tc k%d[^"]*"[^>]*>' % k)
            self.assertIn('<span class="d c-', h)
            self.assertIn(">%s</div>" % label, h)
        self.assertIn(".tile.tc .k .d{color:var(--cat);border-radius:3px}", h)
        self.assertIn('<div class="hn"><div class="bn c-critical">4</div><div class="cap">CAT I open</div></div>', h)
        # its links still go to STIG Manager (the go page of stigman_link_style: open here)
        self.assertIn("/stigman/go?", h)
        self.assertIn('target="_blank" rel="noopener"', h)


class StartHere(unittest.TestCase):
    def test_three_ranked_cards(self):
        h = render(DOC)
        self.assertIn('<details class="sec start" id="s1" open>', h)
        self.assertEqual(h.count('<article class="todo '), 4)
        self.assertEqual(h.count(' later">'), 1)                                          # the 4th under Every step
        self.assertIn('<span class="lbl">Up next</span>', h)
        self.assertIn('<label class="alll" for="all1"><span class="o">Every step (4) &darr;</span>', h)
        self.assertIn('<input type="checkbox" class="allx" id="all1" aria-label="Show every step">', h)
        # Open item: the section with the card's subject (web01: Filesystems), View details: its host and command
        first = h[h.index('<article class="todo c-critical">'):h.index("</article>")]
        self.assertIn('<a href="#s2" title="To Filesystems">Open item &rarr;</a>', first)
        self.assertIn('<label for="cd1-1"><span class="o">View details &rarr;</span>', first)
        self.assertIn('<pre>du -xh &lt;/x&gt;</pre><button class="copy" type="button" hidden>Copy</button>', first)
        self.assertIn('<div class="subj mono">web01, /var</div>', first)
        self.assertNotIn("<t>", h)
        # a card with nothing more: no View details; no section of its status with its subject: the first of its status
        third = h.split('<article class="todo ')[3]
        self.assertNotIn("View details", third.split("</article>")[0])

    def test_cards_beside_other_parts_are_in_a_card(self):
        doc = fixture("ad_repl_dcs_12")
        h = render(doc)
        self.assertNotIn('class="sec start"', h)                                         # a matrix beside the cards: a card
        self.assertIn('<details class="sec" id="s1" open>', h)


class Followups(unittest.TestCase):
    """1.69.0b (the user's review): tile rows by their count (no tile alone on a row), Start here on every report with
    findings (from its own rows when it gives no cards), and the AD replication matrix as in atlas."""

    def test_tile_rows_by_count(self):
        for n, cls in ((5, "t5"), (6, "t6"), (8, "t8"), (9, "t9"), (12, "tx")):
            h = render(dict(DOC, summary=[{"label": "T%d" % i, "value": i} for i in range(n)]))
            self.assertIn('<div class="tiles %s">' % cls, h)
        h = render(DOC)
        self.assertIn(".tiles.t5,.tiles.t9,.tiles.t10{grid-template-columns:repeat(5,minmax(0,1fr))}", h)
        self.assertIn(".tiles.t3,.tiles.t6{grid-template-columns:repeat(3,minmax(0,1fr))}", h)
        self.assertIn('.tiles,.tiles[class*=" t"]{grid-template-columns:repeat(2,minmax(0,1fr))}', h)   # 2 a row at 900 px
        self.assertIn("@supports (grid-template-rows:subgrid){.tile{display:grid;grid-row:span 3;grid-template-rows:subgrid", h)

    def test_start_here_from_the_scorecard_rows(self):
        doc = fixture("stig_scorecard_open_web")
        self.assertNotIn('id="start"', render(doc))          # 1.71.0: the dashboard is the first screen - no Start here
        doc = dict(doc)
        doc.pop("dashboard", None)
        h = render(doc)
        self.assertIn('<section class="sec start auto" id="start" aria-label="Start here">', h)
        self.assertIn('<span class="pill">3 of 8 findings, worst first</span>', h)
        cards = re.findall(r'<article class="todo c-(\w+)">.*?<div class="from">([^<]*)</div>\n<h3>([^<]*)</h3>', h, re.S)
        self.assertEqual(cards, [("critical", "Findings", "SV-254247 (WN22-00-000110)"), ("critical", "Findings", "SV-254239 (WN22-00-000030)"),
                                 ("critical", "Findings", "SV-257777 (RHEL-09-211010)")])     # CAT I, by hosts open
        self.assertIn('<span class="d" aria-hidden="true"></span>CAT I</span>', h)
        self.assertIn('<p class="next">Windows Server 2022 must be maintained at a supported servicing level.</p>', h)
        first = h[h.index('<article class="todo c-critical">'):]
        first = first[:first.index("</article>")]
        href = re.search(r'<a href="#([^"]+)" title="To its row in Findings \(10\)">Open item', first).group(1)
        self.assertIn(' id="%s"' % href, h)                                               # its row has that id
        self.assertIn('<dl class="kv"><dt>', first)                                       # View details: the row's values

    def test_start_here_from_health_rows(self):
        h = render(fixture("ad_report"))
        self.assertIn('<span class="pill">3 of 25 findings, worst first</span>', h)
        self.assertIn('<div class="from">Domain upkeep</div>\n<h3>corp.test</h3>', h)
        self.assertIn("<p class=\"next\">Recycle Bin: Off; RID pool left: 1.3%; krbtgt password: 699 days</p>", h)
        self.assertIn('<a href="#s2r0" title="To its row in Domain upkeep">Open item &rarr;</a>', h)
        self.assertIn(' id="s2r0"', h)

    def test_no_start_here_of_its_own_when_none_is_due(self):
        self.assertNotIn('class="sec start auto"', render(DOC))                               # its own cards: those
        self.assertNotIn('class="sec start auto"', render(fixture("ad_repl_dcs_12")))         # cards beside the matrix
        ok = dict(fixture("ad_report"), status="ok")
        self.assertNotIn('class="sec start auto"', render(ok))                               # nothing to act on

    def test_the_replication_matrix_as_in_atlas(self):
        import test_report_fit as t
        for n in ("ad_repl_dcs_12", "ad_repl_forest", "ad_repl_web"):
            doc = fixture(n)
            o, a = render(doc), t.render("atlas", doc)
            grid = lambda h: h[h.index('<div class="rm-legend'):h.index("</table>", h.index('<div class="rm-wrap')) + 8]
            self.assertIn('<table class="rm rm-', o, n)
            self.assertEqual(grid(o), grid(a), n)                                         # the same cells, words and tips
        css = open(os.path.join(TPL, "report_matrix.css.j2")).read().split("-#}")[-1].strip()
        self.assertIn(css.splitlines()[0], render(fixture("ad_repl_dcs_12")))           # its CSS, the look's colours


class Sections(unittest.TestCase):
    def test_cards_count_footer_explore(self):
        h = render(DOC)
        sec = h[h.index('<details class="sec" id="s2" open>'):h.index('<details class="sec" id="s3"')]
        self.assertIn('<span class="pill"><span class="count">2</span> rows</span>', sec)
        self.assertIn('<input type="checkbox" class="xpc" id="xp2"', sec)
        self.assertIn('<p class="st">Every file system', sec)                             # long: two lines, the rest under Explore
        self.assertIn('<label class="xpl" for="xp2"><span class="o">Explore &rarr;</span>', h)
        self.assertIn('<div class="cf"><span class="src">Linux - Disk &lt;space&gt; · 2026-11-17 06:00 UTC</span>', h)
        # the portal's button: in the footer, the primary (solid) one; not in the tools row
        self.assertIn('<a class="btn pl" href="https://portal.example.mil/portal/x"', h)
        tools = sec[sec.index('<div class="tools2">'):sec.index('<div class="tw">')]
        self.assertNotIn("btn pl", tools)
        self.assertIn(".btn.pl,.btn.pri{background:var(--pri);color:var(--pri-ink)", h)

    def test_short_sections_side_by_side(self):
        h = render(DOC)
        row = h[h.index('<div class="row">'):]
        for t in ("By site", "Notes", "Hosts"):
            self.assertIn("<h2>%s</h2>" % t, row)
        self.assertNotIn("<h2>Filesystems</h2>", row)                                     # 6 columns: the whole row
        self.assertEqual(h.count('<div class="row">'), 1)
        self.assertIn(".row{display:grid;grid-template-columns:repeat(auto-fit,minmax(max(320px,calc((100% - 2 * var(--gap)) / 3)),1fr))", h)
        self.assertIn("@media (max-width:900px){\n  .row{grid-template-columns:minmax(0,1fr)}", h)   # one at 800 px

    def test_bar_list(self):
        h = render(DOC)
        self.assertIn('<ul class="lines blist" aria-label="By site">', h)
        self.assertIn('<li><span class="bl">SITEA</span><span class="bt" aria-hidden="true"><i style="width:100.0%"></i></span><span class="bv">8</span></li>', h)
        self.assertIn('<i style="width:50.0%"></i></span><span class="bv">4</span>', h)
        # not numbers in the second column: a table
        self.assertNotIn('aria-label="Hosts"', h)
        self.assertIn('ul.blist .bt i{display:block;height:100%;border-radius:4px;background:var(--v1)', h)

    def test_a_split_bar_for_percentages(self):
        h = render(DOC)
        self.assertIn(".meter{background:var(--v2)}.meter i{background:var(--v1)", h)
        self.assertIn('<span class="meter"><i style="width:1.0%"></i></span><span class="pv">1%</span>', h)
        self.assertIn('<span class="meter m-critical"><i style="width:95.0%"></i></span><span class="pv c-critical"><svg', h)


class Look(unittest.TestCase):
    def test_palette(self):
        h = render(DOC)
        style = h.split("<style>")[1].split("</style>")[0]
        for tok in ("--bg:#eef0f6", "--card:#ffffff", "--edge:#dde1ea", "--ink:#14213d", "--v1:#6d28d9", "--v2:#9f7aea",
                    "--bg:#0f1626", "--card:#141d30", "--edge:#24304a", "--v1:#7d3df3", "--v2:#9780e7", "--pri:#8ec5ff"):
            self.assertIn(tok + ";", style, tok)
        self.assertIn('@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){', style)
        self.assertIn(':root:has(#theme-dark:checked){', style)                          # the switch works without the script

    def test_text_contrast(self):
        """Every text colour 4.5:1 or better on the card and the page, light and dark."""
        def lum(x):
            c = [int(x[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            c = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
            return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

        def cr(a, b):
            la, lb = lum(a), lum(b)
            return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)
        src = open(os.path.join(TPL, "report_ops.html.j2")).read()
        for part in ("{%- set light %}", "{%- set dark %}"):
            body = src[src.index(part):src.index("{%- endset %}", src.index(part))]
            tok = dict(re.findall(r"(--[\w-]+):(#[0-9a-f]{6});", body))
            for k in ("ink", "muted", "label", "accent", "ok", "warning", "critical", "info", "low", "unknown"):
                for surf in ("card", "bg", "card-strong"):
                    self.assertGreaterEqual(cr(tok["--" + k], tok["--" + surf]), 4.5, (part, k, surf))
            self.assertGreaterEqual(cr(tok["--pri-ink"], tok["--pri"]), 4.5)

    def test_strict_csp_and_offline(self):
        h = render(DOC)
        self.assertFalse(re.search(r"<[^>]+\son[a-z]+=", h))                             # no onclick= and the like
        self.assertNotIn("javascript:", h)
        self.assertNotRegex(h, r'<link|src="https?:|@import|url\(https?:')
        self.assertEqual(len(re.findall(r"<script", h)), 2)                              # the file, and its copy for a saved page
        self.assertIn('<script src="site-report.js"></script>', h)
        for raw in ("<b>sub</b>", "<line>", "<automation>", "<t>", "</x>", "<90%>", "<space>", "<full>"):
            self.assertNotIn(raw, h)

    def test_what_the_script_works_on(self):
        h = render(DOC)
        for part in ('id="theme"', 'id="density"', 'id="print"', 'id="find"', 'class="nav railed"', 'class="card rail"', 'id="rail-hide"', '<details class="sec', '<div class="tools2">',
                     '<div class="tw">', 'class="copy scopy"', 'class="tcopy"', 'class="btn csv"', '<details class="cols">', '<summary><div class="hr">'):
            self.assertIn(part, h, part)
        # the tools row is just before its table (site_report.js finds it there), the count in the summary
        self.assertRegex(h, re.compile(r'<div class="tools2">.*?</div>\n<div class="tw">', re.S))
        js = open(os.path.join(TPL, "site_report.js")).read()
        self.assertIn("c=sec.querySelector('summary .count')", js)

    def test_print_and_motion(self):
        h = render(DOC)
        self.assertIn(".cdd,.todo.later{display:block!important}", h)
        self.assertIn(".hstrip::details-content{content-visibility:visible}", h)
        self.assertIn("@media (prefers-reduced-motion:reduce){:root:not(.motion-always) *{transition:none!important;animation:none!important}", h)

    def test_every_fixture_renders(self):
        import glob
        for f in sorted(glob.glob(os.path.join(REPO, "tests", "fixtures", "report_fit", "*.json"))):
            with open(f) as fh:
                h = render(json.load(fh))
            self.assertIn("</html>", h, f)
            self.assertEqual(h.count("<details class=\"sec"), h.count('<div class="body">'), f)



PROBE = r"""<script>
window.addEventListener('load',function(){setTimeout(function(){
  var o={},bar=document.querySelector('.appbar').getBoundingClientRect().bottom,rail=document.querySelector('.has-rail>.rail');
  document.documentElement.style.scrollBehavior='auto';         /* the page scrolls smoothly: here at once */
  window.scrollTo(0,document.documentElement.scrollHeight);
  setTimeout(function(){var r=rail.getBoundingClientRect();
    o.bar=bar;o.top=r.top;o.bottom=r.bottom;o.vh=window.innerHeight;o.own=rail.scrollHeight>rail.clientHeight;
    o.scrolled=window.scrollY>2000;
    o.groups=[].map.call(rail.querySelectorAll('details.rg'),function(d){return d.querySelector('.rgt').textContent});
    var a=rail.querySelectorAll('details.rg a')[6];a.click();
    setTimeout(function(){var t=document.getElementById(a.getAttribute('href').slice(1)).getBoundingClientRect().top;o.landed=t;
      document.getElementById('rail-hide').click();
      o.hidden=document.documentElement.getAttribute('data-rail');var k=null;try{k=localStorage.getItem('site-report-rail')}catch(e){}o.kept=k;
      o.strip=rail.getBoundingClientRect().width;
      document.getElementById('out').textContent=JSON.stringify(o)},400)},300)},300)});
</script><pre id="out"></pre></body>"""


XSS = '"><script>alert(1)</script><img src=x onerror=alert(2)>'


def _poison(x, keep=("status", "kind", "kinds", "cell_status", "row_status", "hue", "cat", "points", "data_points", "w", "h", "last", "mark")):
    """Every text value of a report with a script and an onerror after it (statuses and drawing numbers kept)."""
    if isinstance(x, dict):
        return dict((k, v if k in keep else _poison(v)) for k, v in x.items())
    if isinstance(x, list):
        return [_poison(v) for v in x]
    if isinstance(x, str) and x and not x.startswith("#") and not x.startswith("http"):
        return x + XSS
    return x


class Escaping(unittest.TestCase):
    """1.69.1c (Codex P1): a report value never reaches the page as markup - a card's chip and meta (OpenSCAP's severity,
    stig_details_cards), titles, every cell - in the ops look, and atlas and brief beside it (their shared parts)."""

    def test_a_card_chip_meta_title(self):
        doc = json.loads(json.dumps(DOC))
        doc["sections"][0]["cards"][0].update(chip=XSS, meta=XSS, title=XSS, subject=XSS, next=XSS)
        doc["sections"][0]["cards"][1].update(meta=XSS)
        doc["title"] = XSS
        h = render(doc)
        self.assertNotIn("<script>alert", h)
        self.assertNotIn("<img src=x", h)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", h)

    def test_every_fixture_every_value(self):
        import glob
        import jinja2
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(TPL), autoescape=False)
        env.filters.update(report_cell_html=sf.report_cell_html, report_sparkline=sf.report_sparkline, report_runway_bar=sf.report_runway_bar, report_hidden_columns=sf.report_hidden_columns,
                       safe_link=sf.safe_link, report_tidy=sf.report_tidy, safe_ref=sf.safe_ref, safe_site_url=sf.safe_site_url, safe_data=sf.safe_data)
        files = sorted(glob.glob(os.path.join(REPO, "tests", "fixtures", "report_fit", "*.json")))
        self.assertGreater(len(files), 100)
        for look in ("ops", "atlas", "brief"):
            tpl = env.get_template("report_%s.html.j2" % look)
            for f in files:
                with open(f) as fh:
                    d = json.load(fh)
                h = tpl.render(_report=_poison(d.get("report", d)), _email_footer="f", _page_logo_cid="", _page_runs=[],
                               _page_template="T", _page_job="9", report_email_header_text="H")
                self.assertEqual((h.count("<script>alert"), h.count("<img src=x")), (0, 0), (look, os.path.basename(f)))


class Rail(unittest.TestCase):
    """The user's rule for every side index (1.69.0): it stays in view (sticky under the bar, as tall as the screen
    under it, its own scroll - at the page's end too), its groups fold, one button puts it away (kept), a picked section
    lands just under the bar (the page's scroll padding, not added twice)."""

    def test_markup(self):
        h = render(fixture("ad_report"))
        rail = h[h.index('<nav class="card rail"'):h.index("</nav>", h.index('<nav class="card rail"'))]
        self.assertEqual(re.findall(r'<span class="rgt">([^<]+)</span>', rail), ["Needs attention", "Everything else"])
        self.assertEqual(rail.count('<details class="rg" open>'), 2)
        self.assertIn('<input type="checkbox" id="rail-hide"', rail)
        self.assertIn('<nav class="nav railed" aria-label="Sections">', h)               # narrower: the bar's list
        self.assertIn("max-height:calc(100vh - var(--appbar) - 24px)", h)
        self.assertIn("html{scroll-padding-top:calc(var(--appbar) + 12px)}", h)         # lands under the bar, once
        self.assertNotIn('class="card rail"', render(dict(DOC, sections=DOC["sections"][:2])))   # 2 sections: no rail

    def test_in_a_browser(self):
        import shutil
        import subprocess
        import tempfile
        b = next((shutil.which(x) for x in ("google-chrome", "chromium", "chromium-browser") if shutil.which(x)), None)
        if not b:
            self.skipTest("no headless browser here")
        h = render(fixture("ad_report")).replace("</body>", PROBE, 1)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "p.html")
            with open(p, "w") as f:
                f.write(h)
            out = subprocess.run([b, "--headless=new", "--disable-gpu", "--no-sandbox", "--user-data-dir=" + os.path.join(d, "u"),
                                  "--window-size=1280,800", "--virtual-time-budget=8000", "--dump-dom", "file://" + p],
                                 capture_output=True, text=True, timeout=120).stdout
        import html as htmllib
        o = json.loads(htmllib.unescape(re.search(r'<pre id="out">(.*?)</pre>', out, re.S).group(1)))
        self.assertTrue(o["scrolled"], o)
        self.assertGreaterEqual(o["top"], o["bar"] - 1, o)                                # in view, under the bar, at the bottom
        self.assertLessEqual(o["bottom"], o["vh"] + 1, o)                                # no taller than the screen
        self.assertTrue(o["own"], o)                                                     # 25 sections: its own scroll
        self.assertEqual(o["groups"], ["Needs attention", "Everything else"])
        self.assertGreaterEqual(o["landed"], o["bar"] - 1, o)                            # a picked section: under the bar
        self.assertLess(o["landed"], o["bar"] + 30, o)
        self.assertEqual(o["hidden"], "hidden")                                          # put away, and kept
        self.assertEqual(o["kept"], "hidden")
        self.assertLess(o["strip"], 60, o)

if __name__ == "__main__":
    unittest.main()
