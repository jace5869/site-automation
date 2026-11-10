#!/usr/bin/env python3
"""The classification banner (1.3.0; plugins/module_utils/site_banner.py, the filters in site_filters, the workbook's
first row - roles/site_email/library/site_xlsx.py): the enclave from site_enclave or the hosts, on / off, the standard
colours and a text colour that reads 4.5:1, the HTML (top and bottom, in print), the plain text, the subject; a report
kept for the copy job keeps its banner. tests/enclave/run_enclave_test.sh runs it through roles/site_email.
Run: python tests/test_banner.py"""
import importlib.util
import io
import os
import sys
import tempfile
import types
import unittest
import zipfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
_spec = importlib.util.spec_from_file_location("site_banner", os.path.join(REPO, "plugins", "module_utils", "site_banner.py"))
b = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(b)
import site_filters as sf  # noqa: E402

for _n in ("ansible", "ansible.module_utils"):
    sys.modules.setdefault(_n, types.ModuleType(_n))
if "ansible.module_utils.basic" not in sys.modules:
    _basic = types.ModuleType("ansible.module_utils.basic")
    _basic.AnsibleModule = object
    sys.modules["ansible.module_utils.basic"] = _basic
_spec = importlib.util.spec_from_file_location("site_xlsx", os.path.join(REPO, "roles", "site_email", "library", "site_xlsx.py"))
xl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(xl)
_spec = importlib.util.spec_from_file_location("site_report_page", os.path.join(REPO, "plugins", "module_utils", "site_report_page.py"))
pg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pg)

DOMAINS = ["classified.example.mil"]


class Enclave(unittest.TestCase):
    def test_site_enclave_wins(self):
        self.assertEqual(b.enclave_of("classified", [], [])[0], "classified")
        self.assertEqual(b.enclave_of("Unclassified", DOMAINS, [["web01.classified.example.mil", ""]])[0], "unclassified")

    def test_from_the_hosts(self):
        self.assertEqual(b.enclave_of("", DOMAINS, [["web01.example.mil", ""], ["app01", "app01.Classified.Example.mil."]]),
                         ("classified", "app01.classified.example.mil is under classified.example.mil (site_enclave_classified_domains)"))
        self.assertEqual(b.enclave_of("", DOMAINS, [["classified.example.mil", ""]])[0], "classified")      # the domain itself
        self.assertEqual(b.enclave_of("", DOMAINS, [["notclassified.example.mil", ""]])[0], "unclassified")  # a suffix, not text
        self.assertEqual(b.enclave_of("", ".classified.example.mil, other.example.mil", [["x.other.example.mil", ""]])[0], "classified")
        self.assertEqual(b.enclave_of("", [], [["web01.classified.example.mil", ""]]), ("unclassified", "site_enclave is not set"))

    def test_a_wrong_enclave(self):
        with self.assertRaises(ValueError):
            b.enclave_of("secret", [], [])


class Banner(unittest.TestCase):
    def test_auto(self):
        self.assertTrue(b.banner({"enclave": "classified"})["on"])
        self.assertFalse(b.banner({"enclave": "unclassified"})["on"])
        self.assertFalse(b.banner({})["on"])
        self.assertTrue(b.banner({"domains": DOMAINS, "hosts": [["db01.classified.example.mil", ""]]})["on"])

    def test_true_and_false(self):
        self.assertTrue(b.banner({"banner": True})["on"])
        self.assertTrue(b.banner({"banner": "true"})["on"])
        self.assertFalse(b.banner({"banner": False, "enclave": "classified"})["on"])
        self.assertFalse(b.banner({"banner": "false", "enclave": "classified"})["subject"])

    def test_the_standard_colours(self):
        for text, bg in (("SECRET", "#c8102e"), ("SECRET//NOFORN", "#c8102e"), ("TOP SECRET", "#ff8c00"), ("TOP SECRET//SCI", "#ff8c00"),
                         ("CONFIDENTIAL", "#0033a0"), ("CUI", "#502b85"), ("CONTROLLED UNCLASSIFIED INFORMATION", "#502b85"),
                         ("UNCLASSIFIED", "#007a33"), ("UNCLASSIFIED//FOUO", "#007a33"), ("secret", "#c8102e")):
            self.assertEqual(b.banner({"banner": True, "text": text})["bg"], bg, text)
        self.assertEqual(b.banner({"banner": True, "text": "SECRETARIAT"})["bg"], b.OTHER)     # a word, not the marking
        self.assertEqual(b.banner({"banner": True})["text"], "SECRET")

    def test_every_text_colour_reads(self):
        for _, bg in b.COLOURS + (("", b.OTHER),):
            self.assertGreaterEqual(b.contrast(b.ink(bg), bg), 4.5, bg)
        self.assertEqual(b.ink("#ff8c00"), "#111111")
        self.assertEqual(b.ink("#c8102e"), "#ffffff")

    def test_report_banner_color(self):
        self.assertEqual(b.banner({"banner": True, "color": "#ABC"})["bg"], "#aabbcc")
        self.assertEqual(b.banner({"banner": True, "color": "123456"})["bg"], "#123456")
        with self.assertRaises(ValueError):
            b.banner({"banner": True, "color": "red"})

    def test_the_filter_says_what_is_wrong(self):
        with self.assertRaises(Exception) as e:
            sf.report_banner({"enclave": "top"})
        self.assertIn("site_enclave must be classified, unclassified or empty", str(e.exception))


class Where(unittest.TestCase):
    B = b.banner({"banner": True, "text": "SECRET <x>"})

    def test_html_top_and_bottom(self):
        page = '<html><head><title>t</title></head><body class="x"><h1>Report</h1></body></html>'
        out = sf.report_banner_html(page, self.B)
        self.assertEqual(out.count('class="site-banner '), 2)
        self.assertLess(out.index('<body class="x">'), out.index("site-banner-top"))
        self.assertLess(out.index('class="site-banner site-banner-top'), out.index("<h1>"))
        self.assertLess(out.index("<h1>"), out.index('class="site-banner site-banner-bottom'))
        self.assertLess(out.index('class="site-banner site-banner-bottom'), out.index("</body>"))
        self.assertIn("SECRET &lt;x&gt;", out)
        self.assertIn('bgcolor="#c8102e"', out)                    # Outlook
        self.assertIn("print-color-adjust:exact", out)
        self.assertIn("@media print{.site-banner-top{position:fixed;top:0", out)
        self.assertEqual(sf.report_banner_html(out, self.B), out)     # never twice
        self.assertEqual(sf.report_banner_html(page, {}), page)
        self.assertEqual(sf.report_banner_html(page, dict(self.B, on=False)), page)
        self.assertTrue(sf.report_banner_html("<p>x</p>", self.B).startswith("<style>"))     # no body: around it

    def test_text_and_subject(self):
        self.assertEqual(sf.report_banner_text("Report\nline\n", self.B), "SECRET <x>\n\nReport\nline\n\nSECRET <x>")
        self.assertEqual(sf.report_banner_text("Report", {}), "Report")
        self.assertEqual(sf.report_banner_subject("[AAP] Disk report", self.B), "(SECRET <x>) [AAP] Disk report")
        self.assertEqual(sf.report_banner_subject("[AAP] Disk report", dict(self.B, subject=False)), "[AAP] Disk report")

    def test_the_workbook_first_row(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "w.xlsx")
        xl.write_xlsx(p, [{"name": "VMs", "columns": ["VM", "CPU", "Memory"], "rows": [["a", 1, 2]]}, {"name": "Empty"}],
                      b.banner({"banner": True, "text": "TOP SECRET"}))
        z = zipfile.ZipFile(p)
        s1 = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn('<row r="1"><c r="A1" s="2" t="inlineStr"><is><t>TOP SECRET</t></is></c></row><row r="2">', s1)
        self.assertIn('<mergeCell ref="A1:C1"/>', s1)
        self.assertIn('<autoFilter ref="A2:C3"/>', s1)
        self.assertIn('ySplit="2" topLeftCell="A3"', s1)
        self.assertIn('<row r="1"><c r="A1" s="2"', z.read("xl/worksheets/sheet2.xml").decode())   # every sheet
        st = z.read("xl/styles.xml").decode()
        self.assertIn('<fgColor rgb="FFFF8C00"/>', st)
        self.assertIn('<color rgb="FF111111"/>', st)
        self.assertIn("'VMs'!$A$2:$C$3", z.read("xl/workbook.xml").decode())
        xl.write_xlsx(p, [{"name": "VMs", "columns": ["VM"], "rows": [["a"]]}])
        s1 = zipfile.ZipFile(p).read("xl/worksheets/sheet1.xml").decode()
        self.assertNotIn('s="2"', s1)
        self.assertIn('<autoFilter ref="A1:A2"/>', s1)

    def test_kept_for_the_copy_job_with_its_banner(self):
        doc = {"data": {"report": {"title": "t", "sections": []}, "banner": self.B}, "files": {"page": "a.html"}}
        text, cut = pg.pack(doc, 100000)
        self.assertEqual(sf.report_page_unpack(text)["data"]["banner"], self.B)

    def test_the_website_module_uses_it(self):
        with open(os.path.join(REPO, "roles", "site_email", "library", "site_report_web.py")) as f:
            src = f.read()
        self.assertIn("from ansible.module_utils.site_banner import inject as banner_inject", src)
        self.assertIn("html = banner_inject(web_page(", src)
        self.assertIn("put(index, banner_inject(index_html(", src)


class TheEnclavesChapter(unittest.TestCase):
    """docs/ENCLAVES.md names only settings that exist (a role's defaults), and only placeholders."""
    NOT_SETTINGS = {"inventory_hostname", "ansible_host", "search_base", "use_act", "report_email_subject_prefix"}

    def test_every_setting_named_exists(self):
        import glob
        import re
        known = set()
        for f in glob.glob(os.path.join(REPO, "roles", "*", "defaults", "main.yml")):
            known.update(re.findall(r"^([a-z][a-z0-9_]*):", open(f).read(), re.M))
        doc = open(os.path.join(REPO, "docs", "ENCLAVES.md")).read()
        named = set(re.findall(r"`([a-z][a-z0-9]*_[a-z0-9_]+)`", doc)) - self.NOT_SETTINGS
        self.assertGreater(len(named), 50)
        self.assertEqual(sorted(n for n in named if n not in known), [])
        for n in ("site_enclave", "site_enclave_classified_domains", "report_banner", "report_banner_text", "report_banner_color",
                  "report_banner_subject", "site_act_enabled"):
            self.assertIn("`%s`" % n, doc)
        self.assertIn("(TEMPLATE_AUDIT.md)", doc)
        self.assertIn("(ENCLAVES.md)", open(os.path.join(REPO, "docs", "TEMPLATE_AUDIT.md")).read())


if __name__ == "__main__":
    unittest.main(verbosity=1)
