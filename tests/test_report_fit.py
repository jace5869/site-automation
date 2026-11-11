"""1.21.0: every report page fits its window - no table scrolls sideways at 1366, 1440 and 1920 px and no page scrolls at
390 px (a phone; a table there may scroll inside its card) - in the atlas and brief looks, light and dark. Each report's
document as its unit tests or its e2e run make it (tests/fixtures/report_fit, refreshed by tests/report_fit_docs.py), and
again with long values: names 22 characters longer, numbers wider. A table that does not fit first takes the script's
steps (f1, f2, f3: wrapping); one that still scrolls fails here. Many pages per browser start: each width is one page of
frames, the report pages in them at that width.
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
WIDE = (1366, 1440, 1920)
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
function measure(d){var de=d.documentElement,r={page:Math.max(0,de.scrollWidth-de.clientWidth),inner:[]};
  [].forEach.call(d.querySelectorAll('table'),function(t){if(!t.offsetParent)return;var a=t.parentElement;
    while(a&&a!==d.body){var o=getComputedStyle(a).overflowX;if(o==='auto'||o==='scroll'||o==='hidden')break;a=a.parentElement}
    if(a&&a!==d.body&&a.scrollWidth>a.clientWidth+1){var h=t.closest('details');h=h&&h.querySelector('h2');
      r.inner.push((h?h.childNodes[0].textContent.trim():'?')+' +'+(a.scrollWidth-a.clientWidth))}});
  return r}
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
        bad = []
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
        self.assertEqual(bad, [], "\n".join(bad[:40]) + ("\n... %d in all" % len(bad) if len(bad) > 40 else ""))
        print("ok: %d report pages (%d documents, atlas and brief, as tested and with long values) fit at %s px and %d px, "
              "light and dark" % (len(pages), len(docs), "/".join(map(str, WIDE)), PHONE), file=sys.stderr)


if __name__ == "__main__":
    unittest.main()
