"""1.21.0: every report page fits its window - no table scrolls sideways at 1280, 1366, 1440 and 1920 px and no page scrolls at
390 px (a phone; a table there may scroll inside its card) - in the atlas and brief looks, light and dark. Each report's
document as its unit tests or its e2e run make it (tests/fixtures/report_fit, refreshed by tests/report_fit_docs.py), and
again with long values: names 22 characters longer, numbers wider. A table that does not fit first takes the script's
steps (f1 to f4: wrapping); one that scrolls before its last step fails here (after it: no word broken, fx - listed). At every width no date, time, IP address, number, heading word or chip of
up to 12 characters is split over two lines (a Range around each: more than one line box is a split). Many pages per browser
start: each width is one page of frames, the report pages in them at that width. And fit() is quick on a big table:
opening or closing a section that changes no table's width refits nothing.
  python tests/test_report_fit.py           (skips without google-chrome or chromium)"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(REPO, "tests", "fixtures", "report_fit")
WIDE = (1280, 1366, 1440, 1920)
PHONE = 390
BATCH = 40                       # report pages per browser start (each twice: light and dark)

ID = re.compile(r'^(?=.*(\d|_|\.[a-z]))(?=.*[A-Za-z])[A-Za-z][\w.\-]{3,40}$')    # vc01, esx01.example.mil, ds_prod (not N-1)
NUM = re.compile(r'^(\d{2,})((?:\.\d+)?)$')


def _longer_word(t):
    lead = re.match(r'^[(\[{"\']*', t).group(0)
    trail = re.search(r'[)\]},;:"\'%]*$', t).group(0)
    core = t[len(lead):len(t) - len(trail)] if trail else t[len(lead):]
    if ID.match(core):
        core = core.replace('.', '-prod-east-rack12-0042.', 1) if '.' in core else core + '-prod-east-rack12-0042'
    elif NUM.match(core) and '%' not in trail:
        m = NUM.match(core)
        core = '{:,}'.format(int(m.group(1)) + 12000) + m.group(2)
    return lead + core + trail


def longer(v):
    """A cell with long values: a name 22 characters longer (vc01 -> vc01-prod-east-rack12-0042), a number of two or
    more digits wider (30 -> 12,030); percentages, prose and booleans as they are."""
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, int):
        return v * 1000 + 7 if abs(v) >= 10 else v
    if isinstance(v, float):
        return round(v * 1000 + 0.5, 2) if abs(v) >= 10 else v
    if isinstance(v, str):
        return v if '\n' in v or len(v) > 160 else ' '.join(_longer_word(t) for t in v.split(' '))
    if isinstance(v, list):
        return [longer(x) for x in v]
    return v


def long_doc(doc):
    return dict(doc, sections=[dict(s, rows=[longer(r) for r in s["rows"]]) if s.get("rows") else s
                               for s in doc["sections"]])


def render(look, doc):
    import jinja2
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(REPO, "roles", "site_email", "templates")),
                             autoescape=False)
    return env.get_template("report_%s.html.j2" % look).render(
        _report=doc, _email_footer="AAP job 9", _page_logo_cid="", _page_runs=[],
        report_email_header_text="Site automation report")


# in each frame, after the page's own script ran (sections open, a resize - its fit): the page wider than the frame,
# and each table whose box scrolls sideways (its section's title, how much)
HARNESS = r"""<!doctype html><html><body><pre id="out"></pre><script>
var W=%(w)d,P=%(pages)s,R={},n=0;
function measure(d){var de=d.documentElement,r={page:Math.max(0,de.scrollWidth-de.clientWidth),inner:[],last:[]};
  [].forEach.call(d.querySelectorAll('table'),function(t){if(!t.offsetParent)return;var a=t.parentElement;
    while(a&&a!==d.body){var o=getComputedStyle(a).overflowX;if(o==='auto'||o==='scroll'||o==='hidden')break;a=a.parentElement}
    if(a&&a!==d.body&&a.scrollWidth>a.clientWidth+1){var h=t.closest('details');h=h&&h.querySelector('h2');
      if(t.classList.contains('f4'))r.inner.push('(with f4) ');
      (t.classList.contains('fx')?r.last:r.inner).push((h?h.childNodes[0].textContent.trim():'?')+' +'+(a.scrollWidth-a.clientWidth))}});
  r.split=splits(d);r.torn=torn(d);return r}
// a phone's card: each cell is its label and ONE value - a value in pieces (its text beside a span) is torn apart
function torn(d){var out=[];[].forEach.call(d.querySelectorAll('td'),function(td){if(!td.offsetParent||getComputedStyle(td).display!=='grid')return;
  var n=[].filter.call(td.childNodes,function(c){return c.nodeType===1||(c.nodeType===3&&c.nodeValue.trim())}).length;
  if(n>1)out.push((td.getAttribute('data-l')||'?')+': '+td.textContent.trim().slice(0,40))});return out}
// a date, a time or an IP address in a cell, and a chip of up to 12 characters: on more than one line is a split
// (a word of its own: a date inside a file name - localhost_log.2026-11-11.txt, user@192.0.2.9, host:1688 - is part of that name)
// a number (16,210  6338  59%%  12,050.0) too: never broken inside its digits
var TOK=/(?<![\w.@\-:\/\\])(\d{4}-\d\d-\d\d(T[\d:.]+Z?)?|\d\d?\/\d\d?\/\d{4}|\d\d?:\d\d(:\d\d)?|(\d{1,3}\.){3}\d{1,3}(\/\d+|:\d+)?|\d[\d,.]*\d%%?)(?![\w@\-])/g;
function lines(rg){var tops=[];[].forEach.call(rg.getClientRects(),function(b){if(b.width<1)return;
  if(!tops.some(function(t){return Math.abs(t-b.top)<3}))tops.push(b.top)});return tops.length}
function splits(d){var out=[];
  [].forEach.call(d.querySelectorAll('td'),function(td){if(!td.offsetParent)return;
    var it=d.createTreeWalker(td,NodeFilter.SHOW_TEXT),n;
    while((n=it.nextNode())){var m,v=n.nodeValue;TOK.lastIndex=0;
      while((m=TOK.exec(v))){var rg=d.createRange();rg.setStart(n,m.index);rg.setEnd(n,m.index+m[0].length);
        if(lines(rg)>1)out.push(m[0])}}});
  // a heading's words: each on one line
  [].forEach.call(d.querySelectorAll('th button'),function(b){if(!b.offsetParent)return;var x=b.firstChild;if(!x||x.nodeType!==3)return;
    var re=/\S+/g,m;while((m=re.exec(x.nodeValue))){var rg=d.createRange();rg.setStart(x,m.index);rg.setEnd(x,m.index+m[0].length);
      if(lines(rg)>1)out.push('heading '+m[0])}});
  [].forEach.call(d.querySelectorAll('td .chip'),function(c){if(!c.offsetParent)return;var x=c.lastElementChild;
    if(!x||x.textContent.length>12)return;var rg=d.createRange();rg.selectNodeContents(x);if(lines(rg)>1)out.push('chip '+x.textContent)});
  return out}
P.forEach(function(p){['light','dark'].forEach(function(s){var f=document.createElement('iframe');
  f.style.cssText='width:'+W+'px;height:900px;border:0;display:block';
  f.onload=function(){var d=f.contentDocument;d.documentElement.setAttribute('data-theme',s);
    [].forEach.call(d.querySelectorAll('details'),function(x){if(!x.classList.contains('cols'))x.open=true});
    f.contentWindow.dispatchEvent(new Event('resize'));
    setTimeout(function(){R[p+' '+s]=measure(d);f.remove();if(++n===P.length*2)document.getElementById('out').textContent=JSON.stringify(R)},600)};
  f.src=p;document.body.appendChild(f)})});
</script></body></html>"""


def _browser():
    for b in ("google-chrome", "chromium", "chromium-browser"):
        if shutil.which(b):
            return shutil.which(b)
    return None


def measure(browser, folder, pages, width):
    """{page name + ' light'|' dark': {page: px, inner: [title +px]}} for these pages at this width."""
    out = {}
    for i in range(0, len(pages), BATCH):
        part = pages[i:i + BATCH]
        h = os.path.join(folder, "fit-%d-%d.html" % (width, i))
        with open(h, "w") as f:
            f.write(HARNESS % {"w": width, "pages": json.dumps(part)})
        with tempfile.TemporaryDirectory() as prof:
            r = subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-sandbox", "--user-data-dir=" + prof,
                                "--allow-file-access-from-files", "--window-size=%d,900" % (width + 40),
                                "--virtual-time-budget=120000", "--dump-dom", "file://" + h],
                               capture_output=True, text=True, timeout=600)
        m = re.search(r'<pre id="out">(.*?)</pre>', r.stdout, re.S)
        if not (m and m.group(1)):
            raise AssertionError("no result at %d px (pages %s...): %s" % (width, part[:2], r.stderr[-600:]))
        import html as htmllib
        out.update(json.loads(htmllib.unescape(m.group(1))))
    return out


class Fit(unittest.TestCase):
    def test_every_report_fits(self):
        b = _browser()
        if not b:
            self.skipTest("no headless browser here")
        docs = sorted(glob.glob(os.path.join(DOCS, "*.json")))
        self.assertGreater(len(docs), 50, "the reports' documents (tests/report_fit_docs.py)")
        bad, last = [], {}
        with tempfile.TemporaryDirectory() as d:
            pages = []
            for f in docs:
                name = os.path.basename(f)[:-5]
                with open(f) as fh:
                    doc = json.load(fh)
                for look in ("atlas", "brief"):
                    for kind, dd in (("", doc), ("-long", long_doc(doc))):
                        p = "%s-%s%s.html" % (look, name, kind)
                        with open(os.path.join(d, p), "w") as o:
                            o.write(render(look, dd))
                        pages.append(p)
            for w in WIDE + (PHONE,):
                for k, r in sorted(measure(b, d, pages, w).items()):
                    if r["page"]:
                        bad.append("%s at %d px: the page scrolls sideways (+%d px)" % (k, w, r["page"]))
                    if w != PHONE and r["inner"]:
                        bad.append("%s at %d px: a table scrolls sideways: %s" % (k, w, "; ".join(r["inner"])))
                    if r["split"]:
                        bad.append("%s at %d px: split over two lines: %s" % (k, w, ", ".join(r["split"][:6])))
                    if r["torn"]:
                        bad.append("%s at %d px: a value in pieces on the phone's card: %s" % (k, w, "; ".join(r["torn"][:4])))
                    if w != PHONE and r["last"]:
                        last.setdefault(k.rsplit(" ", 1)[0].replace("-long.html", ".html"), set()).add(
                            "%d px: %s" % (w, "; ".join(x.rsplit(" +", 1)[0] for x in r["last"])))
        self.assertEqual(bad, [], "\n".join(bad[:40]) + ("\n... %d in all" % len(bad) if len(bad) > 40 else ""))
        for k in sorted(last):          # only after every step: a table that cannot fit with its values whole
            print("  scrolls inside its card: %s - %s" % (k, ", ".join(sorted(last[k]))), file=sys.stderr)
        print("ok: %d report pages (%d documents, atlas and brief, as tested and with long values) fit at %s px and %d px, "
              "light and dark" % (len(pages), len(docs), "/".join(map(str, WIDE)), PHONE), file=sys.stderr)


# a big table's page in a frame: once it is loaded (its script ran), timed at once, synchronously: a section closed (the
# page's toggle handler: fit) and the rows' height changed (every table fitted again)
TIMING = r"""<!doctype html><html><body><pre id="out"></pre><script>
var f=document.createElement('iframe');f.style.cssText='width:1280px;height:900px;border:0';
f.onload=function(){var d=f.contentDocument,w=f.contentWindow,secs=d.querySelectorAll('details.sec'),small=secs[secs.length-1];
  var big=d.querySelector('.tw table'),t0=w.performance.now();
  small.open=false;small.dispatchEvent(new Event('toggle'));void d.body.offsetHeight;var t1=w.performance.now();
  var c=d.querySelector('#density-compact');c.checked=true;c.dispatchEvent(new Event('change',{bubbles:true}));void d.body.offsetHeight;
  var t2=w.performance.now();
  document.getElementById('out').textContent=JSON.stringify({toggle:t1-t0,refit:t2-t1,steps:big.className,rows:big.tBodies[0].rows.length})};
f.src='%(page)s';document.body.appendChild(f);
</script></body></html>"""


class Quick(unittest.TestCase):
    def test_fit_is_quick_on_a_big_table(self):
        """2,000 rows of 14 columns at 1280 px: closing another section refits nothing (under 100 ms here; 300 ms
        allowed for a slow runner); the rows' height changed refits it (printed, for the record)."""
        b = _browser()
        if not b:
            self.skipTest("no headless browser here")
        cols = ["Cluster", "Datacenter", "Hosts", "CPU used", "Memory used", "Memory after host failure", "vCPU per core",
                "VMs on", "Average VM", "Memory growth", "Memory runway", "Room for more VMs", "Hosts recommended",
                "Last read"]
        rows = [["PROD-%04d (vc01 / DC1)" % i, "vc01 / DC1", "3 of 4", "25%%", "78%%", "117%%", 1.88, 30 + i,
                 "4.0 vCPU, 50.0 GB", "+120.0 GB / month", "80%% N-1 reached", "0 (memory)",
                 "1 now (memory 59%% on average then); 5 within 12 months", "2026-10-20 18:14"] for i in range(2000)]
        rows = [[c.replace("%%", "%") if isinstance(c, str) else c for c in r] for r in rows]
        doc = {"title": "Big", "status": "info", "sections": [
            {"title": "Clusters", "columns": cols, "rows": rows, "cell_status": [["", "", "warning"] + [""] * 11] * 2000},
            {"title": "Notes", "lines": ["one", "two"]}]}
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "big.html"), "w") as o:
                o.write(render("atlas", doc))
            h = os.path.join(d, "timing.html")
            with open(h, "w") as o:
                o.write(TIMING % {"page": "big.html"})
            with tempfile.TemporaryDirectory() as prof:
                r = subprocess.run([b, "--headless=new", "--disable-gpu", "--no-sandbox", "--user-data-dir=" + prof,
                                    "--allow-file-access-from-files", "--window-size=1320,900", "--dump-dom", "file://" + h],
                                   capture_output=True, text=True, timeout=300)
        m = re.search(r'<pre id="out">(.*?)</pre>', r.stdout, re.S)
        self.assertTrue(m and m.group(1), r.stderr[-600:])
        import html as htmllib
        t = json.loads(htmllib.unescape(m.group(1)))
        self.assertEqual(t["rows"], 2000)
        self.assertLess(t["toggle"], 300, t)
        print("ok: fit on 2,000 rows x 14 columns at 1280 px (%s): a section closed %.0f ms, every table again (rows' "
              "height) %.0f ms" % (t["steps"] or "no step", t["toggle"], t["refit"]), file=sys.stderr)


if __name__ == "__main__":
    unittest.main()
