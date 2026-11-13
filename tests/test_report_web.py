#!/usr/bin/env python3
"""The report website (roles/site_email, report_web_*): the file names (report_web_files,
plugins/filter/site_filters.py) and the site_report_web module's pieces - the logo put inside the page, the
index page, the report folder (a local path), an older run never over a newer one, and the PowerShell scripts for a
Windows server (report_web_host) - run by pwsh in Constrained Language Mode when pwsh is there. The copy job
(report_web_copy): the report kept small (plugins/module_utils/site_report_page.py) and read back the same by the
filter report_page_unpack, and site_report_copy against a fake AAP in this process - where the last copy got to, Check
runs passed over, max_pages, named jobs, a report that cannot be read.
    python3 tests/test_report_web.py"""
import base64
import datetime
import gzip
import hashlib
import importlib.util
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
sys.path.insert(0, os.path.join(REPO, "roles", "site_email", "library"))
for _name in ("site_aap", "site_report_page", "site_banner", "site_time"):   # as Ansible gives them to the modules (ansible.cfg: module_utils)
    _spec = importlib.util.spec_from_file_location("ansible.module_utils." + _name, os.path.join(REPO, "plugins", "module_utils", _name + ".py"))
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["ansible.module_utils." + _name] = _mod
    _spec.loader.exec_module(_mod)
pg = sys.modules["ansible.module_utils.site_report_page"]

import site_filters as sf  # noqa: E402
import site_report_copy as rc  # noqa: E402
import site_report_web as rw  # noqa: E402

PATTERNS = {"page": "{template}/{date}_{time}_job-{job}.html", "latest": "{template}/latest.html", "index": "index.html"}
NAMES = {"template": "Linux - Certificate trust report", "template_id": 71, "job": 907, "status": "critical",
         "date": "2026-10-23", "time": "0605", "year": "2026", "month": "10", "day": "23"}


class Files(unittest.TestCase):
    def test_default_names(self):
        self.assertEqual(sf.report_web_files(PATTERNS, NAMES), {
            "page": "linux-certificate-trust-report/2026-10-23_0605_job-907.html",
            "latest": "linux-certificate-trust-report/latest.html", "index": "index.html"})

    def test_own_names_and_values_made_safe(self):
        out = sf.report_web_files({"page": "{site}/{year}/{month}/{template_id}-{status}.html", "latest": "", "index": ""},
                                  dict(NAMES, site="HQ East / bldg 2"))
        self.assertEqual(out, {"page": "HQ-East-bldg-2/2026/10/71-critical.html", "latest": "", "index": ""})

    def test_no_template_name(self):
        self.assertEqual(sf.report_web_files(PATTERNS, dict(NAMES, template="  "))["latest"], "report/latest.html")

    def test_latest_same_as_page_is_dropped(self):
        self.assertEqual(sf.report_web_files({"page": "{template}.html", "latest": "{template}.html"}, NAMES)["latest"], "")

    def test_refused(self):
        for pat, says in (("{nope}.html", "there is no {nope}; the names are {date}, {day}"),
                          ("../{job}.html", "a path inside the report folder"),
                          ("/srv/{job}.html", "a path inside the report folder"),
                          ("{template}//{job}.html", "a path inside the report folder"),
                          ("{template}/{job}.htm", "must end in .html"),
                          ("{template", "a name goes in braces"),
                          ("", "report_web_page is empty")):
            with self.assertRaises(sf._FilterError) as e:
                sf.report_web_files(dict(PATTERNS, page=pat), NAMES)
            self.assertIn(says, str(e.exception), pat)
        with self.assertRaises(sf._FilterError) as e:
            sf.report_web_files(dict(PATTERNS, index="{job}/../index.html"), NAMES)
        self.assertIn("report_web_index", str(e.exception))


class Module(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.logo = os.path.join(self.tmp, "logo.png")
        with open(self.logo, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\nfake")

    def test_inline_images(self):
        page = rw.inline_images('<img src="cid:site-report-logo"><img src="cid:other">',
                                [{"cid": "site-report-logo", "path": self.logo}, {"cid": "other", "path": "/nope.png"}])
        self.assertIn('src="data:image/png;base64,iVBORw0KGgpmYWtl"', page)
        self.assertIn('src="cid:other"', page)                       # not there: left as it was

    def test_web_page(self):
        page = rw.web_page('<html><head><title>t</title></head><body><img src="cid:site-report-logo"></body></html>',
                           [{"cid": "site-report-logo", "path": self.logo}])
        self.assertIn("body>table:first-of-type{height:100vh}", page.split("</head>")[0])
        self.assertIn('src="data:image/png;base64,', page)

    def test_web_page_atlas(self):
        page = rw.web_page('<html><head><title>t</title></head><body></body></html>', [], "atlas")
        self.assertNotIn("first-of-type", page)                                  # a web page already: nothing added

    def test_index_page_atlas(self):
        runs = [_run(9, "2026-10-09 06:00 UTC", "warning"), _run(8, "2026-10-08 06:00 UTC")]
        reports = [{"key": "vm/latest.html", "name": "VMware - VM <health>", "status": "warning", "when": runs[0]["when"],
                    "job": 9, "page": runs[0]["page"], "latest": "vm/latest.html", "title": "2 <VMs>", "runs": runs},
                   {"key": "p/latest.html", "name": "Patching", "status": "critical", "when": "2026-10-09 04:00 UTC",
                    "job": "local", "page": "p/b.html", "latest": "p/latest.html"}]
        html = rw.index_html(reports, "site/index.html", "HQ & ops", ("data:image/png;base64,AA", 42, 56), "now", (), "atlas")
        self.assertIn('class="seg" id="theme"', html)
        self.assertIn('@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){', html)
        self.assertIn('<img class="logo" src="data:image/png;base64,AA" width="42" height="56"', html)
        self.assertIn("HQ &amp; ops", html)
        self.assertIn("VMware - VM &lt;health&gt;", html)
        self.assertIn("2 &lt;VMs&gt;", html)
        self.assertIn('href="../vm/latest.html"', html)
        self.assertIn('<label for="t1"><span class="dot c-warning"></span>VMware <span class="n">1</span></label>', html)
        self.assertIn('#t1:checked~.list .r:not(.a1){display:none}', html)
        self.assertIn("2 runs</span>", html)
        self.assertIn("job 9", html)
        self.assertNotIn("job local", html)
        self.assertNotRegex(html, r'<link|src="https?:|@import')
        self.assertIn("No reports yet.", rw.index_html([], "index.html", "x", None, "now", (), "atlas"))
        self.assertNotIn('class="tab"', rw.index_html([], "index.html", "x", None, "now", (), "atlas"))

    def test_atlas_colours_as_the_page(self):
        """The index page's colours are the report page's (roles/site_email/templates/report_atlas.html.j2)."""
        import re
        with open(os.path.join(REPO, "roles", "site_email", "templates", "report_atlas.html.j2")) as f:
            tpl = f.read()
        light = tpl[tpl.index("{%- set light %}"):tpl.index("{%- set dark %}")]
        dark = tpl[tpl.index("{%- set dark %}"):tpl.index("{%- endset %}", tpl.index("{%- set dark %}"))]
        for mine, theirs in ((rw.ATLAS_LIGHT, light), (rw.ATLAS_DARK, dark)):
            tokens = dict(re.findall(r"(--[\w-]+):([^;]+);", mine))
            self.assertGreater(len(tokens), 30)
            for k, v in tokens.items():
                self.assertIn("%s:%s;" % (k, v), theirs, k)

    def test_atlas_switch_without_the_script(self):
        """The index page's light / auto / dark switch: radio buttons the CSS acts on, so it works where a web server's
        Content-Security-Policy blocks the page's script; the script only keeps the choice."""
        html = rw.index_html([], "index.html", "x", None, "now", (), "atlas")
        self.assertIn('<div class="seg" id="theme" role="radiogroup" aria-label="Light or dark">', html)   # not hidden
        for t in ("light", "auto", "dark"):
            self.assertIn('<input type="radio" name="site-report-theme" id="theme-%s" value="%s"' % (t, t), html)
            self.assertIn('<label for="theme-%s"' % t, html)
        self.assertIn('id="theme-auto" value="auto" checked>', html)
        self.assertIn(":root:has(#theme-light:checked){--ink:#111827;", html)
        self.assertIn(":root:has(#theme-dark:checked){--ink:#e6edf3;", html)
        self.assertIn(':root[data-theme="dark"]{--ink:#e6edf3;', html)      # older browsers without :has(): the script
        self.assertIn("localStorage.setItem('site-report-theme',n)", html)
        self.assertNotIn("<button", html)

    def test_index_page(self):
        reports = [{"key": "b", "name": "VMware <snapshots>", "title": "3 failed", "status": "critical", "when": "2026-10-23 06:05 UTC",
                    "job": 4182, "page": "vmware/2026-10-23_0605_job-4182.html", "latest": "vmware/latest.html"},
                   {"key": "a", "name": "Linux", "title": "ok", "status": "ok", "when": "2026-10-22 06:00 UTC", "job": "local",
                    "page": "linux/x.html", "latest": ""}]
        html = rw.index_html(reports, "site/index.html", "HQ & ops", ("data:image/png;base64,AA", 42, 56), "2026-10-23 06:06 UTC")
        self.assertIn('href="../vmware/latest.html"', html)                # links relative to the index
        self.assertIn('href="../linux/x.html"', html)                      # no latest: the run's page
        self.assertIn("VMware &lt;snapshots&gt;", html)
        self.assertIn("HQ &amp; ops", html)
        self.assertIn("job 4182", html)
        self.assertNotIn("job local", html)
        self.assertIn('width="42" height="56"', html)
        self.assertIn("2 report(s)", html)
        self.assertIn("No reports yet.", rw.index_html([], "index.html", "x", None, "now"))
        self.assertNotIn('class="tab"', rw.index_html([], "index.html", "x", None, "now"))      # no tabs without reports

    def test_index_page_tabs_and_runs(self):
        runs = [_run(9, "2026-10-09 06:00 UTC", "warning"), _run(8, "2026-10-08 06:00 UTC")]
        reports = [{"key": "vm/latest.html", "name": "VMware - VM health report", "status": "warning", "when": runs[0]["when"],
                    "job": 9, "page": runs[0]["page"], "latest": "vm/latest.html", "title": "2 VMs", "runs": runs},
                   {"key": "ds/latest.html", "name": "VMware - Datastore report", "status": "ok", "when": "2026-10-09 05:00 UTC",
                    "job": 7, "page": "ds/a.html", "latest": "ds/latest.html"},                       # an index from before
                   {"key": "p/latest.html", "name": "Patching", "status": "critical", "when": "2026-10-09 04:00 UTC",
                    "job": 6, "page": "p/b.html", "latest": "p/latest.html"}]
        html = rw.index_html(reports, "index.html", "x", None, "now")
        self.assertIn('<label for="t0">All <span style="font-weight:400;">3</span></label>', html)
        self.assertIn('<label for="t1"><span style="color:#9a5806;">&#9679;</span> VMware <span style="font-weight:400;">2</span></label>', html)
        self.assertIn('<label for="t2"><span style="color:#b42318;">&#9679;</span> Other <span style="font-weight:400;">1</span></label>', html)
        self.assertIn("#t1:checked~.list .r:not(.a1){display:none}", html)
        self.assertNotIn(".r:not(.a0)", html)                                                         # All hides nothing
        self.assertEqual(html.count('<details class="r a1"'), 2)
        self.assertIn('id="t0" class="tab" checked>', html)
        self.assertIn('href="vm/2026-10-09_job-9.html" style="color:#0f6b4f;font-weight:600;">2026-10-09 06:00 UTC</a> <span', html)
        self.assertIn('href="vm/2026-10-08_job-8.html"', html)
        self.assertIn("2 runs</span>", html)
        self.assertIn('href="ds/a.html" style="color:#0f6b4f;font-weight:600;">', html)              # its one run, from the line
        self.assertIn("1 run</span>", html)
        self.assertEqual(html.encode("ascii", "strict").decode("ascii"), html)                         # ASCII: any PowerShell writes it

    def test_folder_target(self):
        t = rw.Target(self.tmp, "", 5)
        self.assertIsNone(t.read("a/index.json"))
        t.write("a/b/page.html", "<p>é</p>", "text/html")
        self.assertEqual(t.read("a/b/page.html"), "<p>é</p>")
        self.assertEqual([f for f in os.listdir(os.path.join(self.tmp, "a", "b"))], ["page.html"])   # no temporary file left
        for bad in ("relative/path", os.path.join(self.tmp, "not-mounted")):
            with self.assertRaises(rw.Fail):
                rw.Target(bad, "", 5)
        # the same page again is not written (0.97.0: hundreds of pages a run onto a share); a changed one is
        path = os.path.join(self.tmp, "a", "b", "page.html")
        os.utime(path, (1000000000, 1000000000))
        t.write("a/b/page.html", "<p>é</p>", "text/html")
        self.assertEqual(os.path.getmtime(path), 1000000000)
        t.write("a/b/page.html", "<p>è</p>", "text/html")
        self.assertNotEqual(os.path.getmtime(path), 1000000000)
        self.assertEqual(t.read("a/b/page.html"), "<p>è</p>")

    def test_smb_path(self):
        self.assertEqual(rw.smb_path('\\\\fileserver\\its\\aap\\reports\\'), ('fileserver', '\\\\fileserver\\its\\aap\\reports'))
        self.assertEqual(rw.smb_path('smb://fileserver/its/aap/reports'), ('fileserver', '\\\\fileserver\\its\\aap\\reports'))
        self.assertEqual(rw.smb_path('smb:/fileserver/its/aap'), ('fileserver', '\\\\fileserver\\its\\aap'))
        self.assertEqual(rw.smb_path('SMB://fileserver/its/aap/'), ('fileserver', '\\\\fileserver\\its\\aap'))
        self.assertEqual(rw.smb_path('//fs01.example.mil/its'), ('fs01.example.mil', '\\\\fs01.example.mil\\its'))
        for not_smb in ('/mnt/reports', 'https://reports.example.mil/up', 'D:\\reports'):
            self.assertIsNone(rw.smb_path(not_smb))
        with self.assertRaises(rw.Fail):
            rw.smb_path('\\\\fileserver')

    def test_smb_login_needed(self):
        saved = {k: os.environ.pop(k, None) for k in ("REPORT_WEB_USERNAME", "REPORT_WEB_PASSWORD")}
        try:
            with self.assertRaises(rw.Fail) as e:
                rw.SmbTarget('\\\\fileserver\\its\\aap', 'ntlm', False, 445, 2)
            self.assertTrue('Report website' in str(e.exception) or 'smbprotocol' in str(e.exception))
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_smb_login_failures_said_in_words(self):
        t = rw.SmbTarget.__new__(rw.SmbTarget)      # no login: only the words for what went wrong
        t.kw, t.auth = {'port': 445}, 'negotiate'
        why = lambda text: t._why('logging in to fs01.example.mil as svc', Exception(text))     # noqa: E731
        self.assertIn('user@YOURDOMAIN.MIL - the domain\'s full name in capitals',
                      why('SpnegoError (1): Configuration file does not specify default realm, Context: Getting kerberos'))
        self.assertIn("the server's full name", why('ValueError: unsupported hash type md4'))
        self.assertIn('the server by its full name', why('Server not found in Kerberos database'))
        self.assertNotIn('negotiate (Kerberos too', why('SpnegoError (9): Invalid token was supplied, Minor: Success'))
        t.auth = 'kerberos'
        self.assertIn('use report_web_smb_auth: negotiate', why('SpnegoError (9): Invalid token was supplied, Minor: Success'))

    def test_http_login_from_the_environment(self):
        os.environ.update(REPORT_WEB_TOKEN="", REPORT_WEB_USERNAME="up", REPORT_WEB_PASSWORD="pw")
        self.assertEqual(rw.Target("https://reports.example.mil/r", "", 5).auth, "Basic dXA6cHc=")
        os.environ["REPORT_WEB_TOKEN"] = "tok"
        self.assertEqual(rw.Target("https://reports.example.mil/r", "", 5).auth, "Bearer tok")
        for k in ("REPORT_WEB_TOKEN", "REPORT_WEB_USERNAME", "REPORT_WEB_PASSWORD"):
            os.environ.pop(k)
        self.assertEqual(rw.Target("http://reports.example.mil/r", "", 5).auth, "")


def kept_doc(job=907, rows=2, when="2026-10-23 06:05 UTC", template="Linux - Certificate trust report"):
    """What a report job keeps for the copy job (site_report_web with keep)."""
    page = "linux-certificate-trust-report/2026-10-23_0605_job-%d.html" % job
    report = {"title": "Certificate trust", "status": "warning", "summary": [{"label": "Hosts", "value": 3}],
              "sections": [{"title": "All", "status": "warning", "columns": ["Host", "Path"],
                            "rows": [["web%04d.example.mil" % i, "/etc/pki/%d.pem" % i] for i in range(rows)],
                            "row_status": ["warning"] * rows, "cell_status": [["warning", ""]] * rows},
                           {"title": "Notes", "lines": ["one"]}]}
    return {"files": {"page": page, "latest": "linux-certificate-trust-report/latest.html", "index": "index.html"},
            "url": "https://reports.example.mil/" + page,
            "data": {"report": report, "job": job, "template": template, "template_id": 71, "when": when, "runs": []},
            "entry": {"key": "linux-certificate-trust-report/latest.html", "name": template, "title": "Certificate trust",
                      "status": "warning", "when": when, "job": job, "page": page},
            "footer": "AAP job %d (%s), started by admin, %s." % (job, template, when)}


class Packed(unittest.TestCase):
    def test_round_trip_and_the_filter_reads_it_the_same(self):
        text, cut = pg.pack(kept_doc(), 256 * 1024)
        self.assertEqual(cut, [])
        doc = pg.unpack(text)
        self.assertEqual(doc, dict(kept_doc(), v=1))
        self.assertEqual(sf.report_page_unpack(text), doc)
        self.assertEqual(pg.pack(kept_doc(), 256 * 1024)[0], text)                 # the same report packs the same

    def test_a_big_report_cut_to_fit_and_says_so(self):
        random.seed(5)
        doc = kept_doc(rows=6000)
        for r in doc["data"]["report"]["sections"][0]["rows"]:
            r[1] = "%x" % random.getrandbits(160)                                  # does not compress away
        text, cut = pg.pack(doc, 64 * 1024)
        self.assertLessEqual(len(text), 64 * 1024)
        back = pg.unpack(text)["data"]["report"]["sections"][0]
        kept = len(back["rows"])
        self.assertEqual(cut, [("All", kept, 6000)])
        self.assertLess(kept, 6000)
        self.assertEqual((len(back["row_status"]), len(back["cell_status"])), (kept, kept))
        self.assertIn("This page shows the first %d of 6000 rows (report_web_copy_max_kb)." % kept, back["text"])
        self.assertEqual(len(doc["data"]["report"]["sections"][0]["rows"]), 6000)    # the report itself is not changed
        # 1.23.0: with its note the text still fits (these sizes were a few bytes over before)
        random.seed(5)
        rows = [["h%d" % i, "%x" % random.getrandbits(160)] for i in range(6000)]
        small = {"files": {"page": "a.html"}, "entry": {}, "data": {"report": {"sections": [{"title": "All", "rows": rows}]}}}
        for most in (7165, 14339, 28476):
            self.assertLessEqual(len(pg.pack(small, most)[0]), most)

    def test_too_big_even_cut(self):
        doc = kept_doc(rows=5)
        doc["data"]["report"]["subtitle"] = "%x" % random.getrandbits(80000)
        self.assertEqual(pg.pack(doc, 4096)[0], "")

    def test_what_cannot_be_read(self):
        for text, says in (("not base64!", "not a kept report"),
                           (base64.b64encode(gzip.compress(b'{"v": 9}')).decode(), "another version (9): update the copy job"),
                           (base64.b64encode(gzip.compress(b'{"v": 1, "files": {}}')).decode(), "without its page's name"),
                           (base64.b64encode(gzip.compress(b'{"v": 1, "files": {"page": "a.html"}}')).decode(), "without its report")):
            with self.assertRaises(ValueError) as e:
                pg.unpack(text)
            self.assertIn(says, str(e.exception))
        with self.assertRaises(sf._FilterError):
            sf.report_page_unpack("not base64!")


class Newer(unittest.TestCase):
    def test_an_older_run_never_over_a_newer_one(self):
        old = {"key": "k", "when": "2026-10-23 06:05 UTC"}
        self.assertTrue(rw.newer(None, {"when": "2020-01-01 00:00 UTC"}))
        self.assertTrue(rw.newer(old, {"when": "2026-10-23 06:05 UTC"}))                 # the same minute: written
        self.assertTrue(rw.newer(old, {"when": "2026-10-23 07:00 UTC"}))
        self.assertFalse(rw.newer(old, {"when": "2026-10-22 23:59 UTC"}))
        self.assertTrue(rw.newer({"key": "k"}, {"when": "2026-10-22 23:59 UTC"}))       # a line without when (older index)


def _run(job, when, status="ok"):
    return {"page": "vm/2026-10-%s_job-%d.html" % (when[8:10], job), "status": status, "when": when, "job": job, "title": "t%d" % job}


class History(unittest.TestCase):
    def entry(self, job, when, status="ok"):
        return dict(_run(job, when, status), key="vm/latest.html", name="VMware - VM health report", latest="vm/latest.html")

    def test_the_last_runs_kept_the_older_ones_dropped(self):
        line, dropped = rw.history(None, self.entry(1, "2026-10-01 06:00 UTC"), 5)
        self.assertEqual([x["job"] for x in line["runs"]], [1])
        self.assertEqual(dropped, [])
        for job in range(2, 8):
            line, dropped = rw.history(line, self.entry(job, "2026-10-%02d 06:00 UTC" % job), 5)
        self.assertEqual([x["job"] for x in line["runs"]], [7, 6, 5, 4, 3])              # newest first, 5 of them
        self.assertEqual([x["job"] for x in dropped], [2])                               # 1 went the run before
        self.assertEqual(line["job"], 7)
        # no key, name or runs in a run; a banner key only when the line had one (1.24.0c: the module always gives one)
        self.assertEqual(sorted(line["runs"][0]), sorted(set(rw.RUN_KEYS) - {"banner"}))
        line, _ = rw.history(line, dict(self.entry(8, "2026-10-08 06:00 UTC"), banner={}), 5)
        self.assertEqual(line["runs"][0]["banner"], {})

    def test_an_older_run_copied_later(self):
        line = None
        for job in (1, 3, 4):
            line, _ = rw.history(line, self.entry(job, "2026-10-%02d 06:00 UTC" % job), 3)
        line, dropped = rw.history(line, self.entry(2, "2026-10-02 06:00 UTC", "critical"), 3)
        self.assertEqual(line["job"], 4)                                                  # the line stays the newest run's
        self.assertEqual([x["job"] for x in line["runs"]], [4, 3, 2])
        self.assertEqual([x["job"] for x in dropped], [1])
        line, dropped = rw.history(line, self.entry(0, "2026-09-30 06:00 UTC"), 3)
        self.assertEqual([x["job"] for x in line["runs"]], [4, 3, 2])
        self.assertEqual(dropped, [])                                                     # its page, just written, stays

    def test_an_index_from_before_and_a_run_again(self):
        old = {"key": "vm/latest.html", "name": "VMware - VM health report", "latest": "vm/latest.html", **_run(1, "2026-10-01 06:00 UTC")}
        line, dropped = rw.history(old, self.entry(2, "2026-10-02 06:00 UTC"), 5)
        self.assertEqual([x["job"] for x in line["runs"]], [2, 1])                       # its one run, from the line itself
        line, dropped = rw.history(line, self.entry(2, "2026-10-02 06:00 UTC", "warning"), 5)
        self.assertEqual([(x["job"], x["status"]) for x in line["runs"]], [(2, "warning"), (1, "ok")])   # the same page once
        line, dropped = rw.history(line, self.entry(3, "2026-10-03 06:00 UTC"), 0)        # at least 1
        self.assertEqual([x["job"] for x in line["runs"]], [3])
        self.assertEqual([x["job"] for x in dropped], [2, 1])

    def test_deletable(self):
        kept = {"index.html", "index.json", "vm/latest.html", "vm/latest.json"}
        for rel in ("vm/2026-10-01_job-1.html", "vm/2026-10-01_job-1.json", "site/vm/2026/ok-1.html"):
            self.assertTrue(rw.deletable(rel, kept), rel)
        for rel in ("vm/latest.html", "index.json", "../x.html", "vm/../../x.html", "/etc/x.html", "C:/x.html", "a\\b.html",
                    "vm//x.html", "vm/x.txt", "vm/x", None, 5):
            self.assertFalse(rw.deletable(rel, kept), rel)

    def test_area(self):
        self.assertEqual(rw.area("VMware - VM health report"), "VMware")
        self.assertEqual(rw.area("Linux - Certificate trust report - weekly"), "Linux")
        self.assertEqual(rw.area("Patching"), "Other")
        self.assertEqual(rw.area(" - x"), "Other")
        self.assertEqual(rw.area(None), "Other")
        tabs = [{"tab": "Certificates", "match": ["*certificate*"]}, {"tab": "VMware", "match": ["VM *", "VMware*"]},
                {"tab": "Linux", "match": "Health check*"}]
        self.assertEqual(rw.area("VMware - certificate report", tabs), "Certificates")             # the first that matches
        self.assertEqual(rw.area("VM - health report", tabs), "VMware")
        self.assertEqual(rw.area("vm - HEALTH report", tabs), "VMware")                            # any case
        self.assertEqual(rw.area("Health check", tabs), "Linux")                                   # one name, not a list
        self.assertEqual(rw.area("Backup - nightly", tabs), "Backup")                              # none: its name up to " - "
        html = rw.index_html([{"name": n, "status": "ok", "page": "p%d.html" % i} for i, n in enumerate(
            ["Backup - nightly", "Patching", "VM - health report", "Certificate report"])], "index.html", "x", None, "now", tabs)
        self.assertEqual(rw.re.findall(r'<label for="t\d">(?:<span[^>]*>[^<]*</span> )?([^<]+?) <span', html),
                         ["All", "Certificates", "VMware", "Backup", "Other"])                     # the tabs' order, A to Z, Other

    def test_the_default_tabs_take_every_template_of_the_guide(self):
        import re
        import yaml
        with open(os.path.join(REPO, "roles", "site_email", "defaults", "main.yml")) as f:
            tabs = yaml.safe_load(f)["report_web_tabs"]
        with open(os.path.join(REPO, "docs", "JOB_TEMPLATES.md")) as f:
            names = re.findall(r"^\| ([^|]+?) \| `playbooks/", f.read(), re.M)
        self.assertGreater(len(names), 70)
        never = {"Outage AAR - Word document", "SOP - Word document"}      # emailed only, never on the website (_report_web_skip)
        names = [n for n in names if n not in never]
        self.assertEqual([n for n in names if rw.area(n, tabs) == "Other" or rw.area(n, tabs) not in [t["tab"] for t in tabs]], [])


class InUse(unittest.TestCase):
    def setUp(self):
        self.wait, rw.WAIT = rw.WAIT, (0, 0)

    def tearDown(self):
        rw.WAIT = self.wait

    def test_again_until_it_works_or_not_worth_it(self):
        tries = []

        def busy():
            tries.append(1)
            if len(tries) < 3:
                raise OSError(13, "Permission denied")
            return "done"
        self.assertEqual(rw.again(busy, lambda e: True), "done")
        self.assertEqual(len(tries), 3)
        with self.assertRaises(OSError):                                                # 1 + len(WAIT) tries, then its error
            rw.again(lambda: (_ for _ in ()).throw(OSError(13, "x")), lambda e: True)
        tries[:] = []
        with self.assertRaises(ValueError):
            rw.again(lambda: tries.append(1) or (_ for _ in ()).throw(ValueError()), lambda e: False)
        self.assertEqual(len(tries), 1)                                                 # not worth it: at once

    def test_smb_errors_that_go_away(self):
        self.assertTrue(rw._smb_in_use(Exception("Received unexpected status from the server: STATUS_ACCESS_DENIED (3221225506)")))
        self.assertTrue(rw._smb_in_use(Exception("[NtStatus 0xc0000043] The process cannot access the file because it is being used")))
        self.assertFalse(rw._smb_in_use(Exception("STATUS_LOGON_FAILURE")))

    def test_folder_remove(self):
        tmp = tempfile.mkdtemp()
        t = rw.Target(tmp, "", 5)
        t.write("a/old.html", "x", "text/html")
        t.remove("a/old.html")
        t.remove("a/old.html")                                                          # gone already: fine
        self.assertEqual(os.listdir(os.path.join(tmp, "a")), [])
        shutil.rmtree(tmp)


# ---- a fake AAP: job template 90 is the copy job, the others report jobs ----------------------------------------------
def _t(minutes):
    return (datetime.datetime(2026, 10, 24, 6, 0, 0) + datetime.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _kept(job, minutes):
    return [pg.pack(kept_doc(job=job, when=_t(minutes)[:16].replace("T", " ") + " UTC"), 256 * 1024)[0]]


class FakeAap(object):
    def __init__(self):
        self.jobs, self.forbid, self.calls = [], False, []

    def add(self, i, template, minutes, artifacts=None, job_type="run", status="successful"):
        self.jobs.append({"id": i, "job_template": template, "name": "T%d" % template, "status": status, "job_type": job_type,
                          "finished": _t(minutes), "artifacts": artifacts or {}})

    def answer(self, path, q):
        self.calls.append((path, q))
        if path == "me/":
            return 200, {"id": 1}
        if path.startswith("jobs/") and path.count("/") == 2:
            found = [j for j in self.jobs if str(j["id"]) == path.split("/")[1]]
            return (200, found[0]) if found else (404, {"detail": "Not found."})
        if path != "jobs/":
            return 404, {"detail": "Not found."}
        if self.forbid:
            return 403, {"detail": "You do not have permission to perform this action."}
        js = [j for j in self.jobs if (not q.get("job_type") or j["job_type"] == q["job_type"])
              and (not q.get("job_template") or str(j["job_template"]) == q["job_template"])
              and (not q.get("id__lt") or j["id"] < int(q["id__lt"]))
              and (not q.get("status__in") or j["status"] in q["status__in"].split(","))
              and (not q.get("finished__gt") or j["finished"] > q["finished__gt"])]
        js.sort(key=lambda j: -j["id"] if q.get("order_by") == "-id" else (j["finished"], j["id"]))
        size, page = int(q.get("page_size", 25)), int(q.get("page", 1))
        nxt = None
        if len(js) > size * page:
            nxt = "/api/controller/v2/jobs/?" + urllib.parse.urlencode(dict(q, page=page + 1))
        return 200, {"count": len(js), "next": nxt, "results": [{k: v for k, v in j.items() if k != "artifacts"}
                                                                 for j in js[size * (page - 1):size * page]]}


def serve(fake):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            base = "/api/controller/v2/"
            code, obj = (404, {}) if not u.path.startswith(base) else fake.answer(u.path[len(base):], dict(urllib.parse.parse_qsl(u.query)))
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Copy(unittest.TestCase):
    NOW = datetime.datetime(2026, 10, 24, 12, 0, 0)

    def setUp(self):
        self.fake = FakeAap()
        self.srv = serve(self.fake)
        env = {"CONTROLLER_HOST": "http://127.0.0.1:%d" % self.srv.server_port, "CONTROLLER_USERNAME": "audit", "CONTROLLER_PASSWORD": "pw"}
        self.api = rc.Api(rc.connection(env), "", 10)
        self.assertEqual(self.api.probe(), "")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def p(self, **kw):
        return dict({"job_id": 500, "template_id": 90, "jobs": [], "hours": 24, "max_pages": 50, "overlap_minutes": 5}, **kw)

    def test_first_run_the_last_hours_oldest_first_its_own_jobs_left_out(self):
        f = self.fake
        f.add(101, 71, -60 * 30, {"site_report_page": _kept(101, -60 * 30)})       # 30 hours ago: too old
        f.add(103, 72, 20, {"site_report_page": _kept(103, 20)})
        f.add(102, 71, 10, {"site_report_page": _kept(102, 10), "other": 1})
        f.add(104, 73, 30, {"site_report_run": {"status": "ok"}})                   # sent no page for the copy job
        f.add(105, 90, 40, {"site_report_copied": {"until": _t(-60), "seen": []}}, job_type="check")   # a Check run of this job
        f.add(106, 71, 50, {"site_report_page": _kept(106, 50)}, status="failed")   # a report job that failed: still copied
        r = rc.find(self.api, self.p(), self.NOW)
        self.assertEqual([x["job"] for x in r["reports"]], [102, 103, 106])
        self.assertEqual(r["since"], "2026-10-23T12:00:00.000000Z")
        self.assertEqual((r["until"], r["seen"], r["more"]), (_t(50), [106], False))
        self.assertEqual(sf.report_page_unpack(r["reports"][0]["packed"])["data"]["job"], 102)
        self.assertEqual(r["warnings_text"], [])

    def test_from_where_the_last_run_got_to_check_and_named_runs_passed_over(self):
        f = self.fake
        f.add(102, 71, 10, {"site_report_page": _kept(102, 10)})
        f.add(103, 72, 14, {"site_report_page": _kept(103, 14)})
        f.add(104, 71, 30, {"site_report_page": _kept(104, 30)})
        f.add(400, 90, 15, {"site_report_copied": {"until": _t(14), "seen": [103]}})        # the last real copy
        f.add(401, 90, 20, {})                                                             # report_web_copy_jobs: keeps none
        f.add(402, 90, 25, {"site_report_copied": {"until": _t(29), "seen": []}}, job_type="check")
        r = rc.find(self.api, self.p(), self.NOW)
        self.assertEqual(r["since"], _t(9))                                  # 5 minutes before: 102 listed again ...
        self.assertEqual([x["job"] for x in r["reports"]], [102, 104])        # ... but 103 was seen
        self.assertEqual(r["until"], _t(30))

    def test_nothing_new(self):
        r = rc.find(self.api, self.p(), self.NOW)                                  # nothing at all: the same place next time
        self.assertEqual((r["reports"], r["until"], r["seen"]), ([], r["since"], []))
        self.fake.add(400, 90, 15, {"site_report_copied": {"until": _t(14), "seen": [103]}})
        r = rc.find(self.api, self.p(), self.NOW)                                  # only the last copy itself: past it
        self.assertEqual((r["reports"], r["until"], r["seen"]), ([], _t(15), [400]))

    def test_at_most_max_pages_the_rest_next_time(self):
        for i in range(6):
            self.fake.add(200 + i, 71, 10 + i, {"site_report_page": _kept(200 + i, 10 + i)})
        r = rc.find(self.api, self.p(max_pages=4), self.NOW)
        self.assertEqual(([x["job"] for x in r["reports"]], r["more"], r["until"]), ([200, 201, 202, 203], True, _t(13)))
        self.fake.add(400, 90, 20, {"site_report_copied": {"until": r["until"], "seen": r["seen"]}})
        r2 = rc.find(self.api, self.p(), self.NOW)
        self.assertEqual([x["job"] for x in r2["reports"]], [204, 205])

    def test_many_pages_of_jobs(self):
        for i in range(450):
            self.fake.add(1000 + i, 70, i % 300, {})
        self.fake.add(2000, 71, 301, {"site_report_page": _kept(2000, 301)})
        r = rc.find(self.api, self.p(), self.NOW)
        self.assertEqual([x["job"] for x in r["reports"]], [2000])
        self.assertEqual(r["looked"], 451)

    def test_named_jobs_only(self):
        f = self.fake
        f.add(102, 71, 10, {"site_report_page": _kept(102, 10)})
        f.add(103, 72, 20, {"site_report_run": {}})
        r = rc.find(self.api, self.p(jobs=["103", "102", "999"]), self.NOW)
        self.assertEqual([x["job"] for x in r["reports"]], [102])
        self.assertEqual((r["since"], r["until"]), ("", ""))
        self.assertTrue(any(w.startswith("job 103: kept no report") for w in r["warnings_text"]), r["warnings_text"])
        self.assertTrue(any(w.startswith("job 999: AAP answered HTTP 404") for w in r["warnings_text"]), r["warnings_text"])
        self.assertIn("is not a job ID", rc.find(self.api, self.p(jobs=["web01"]), self.NOW)["problems"][0])

    def test_a_report_that_cannot_be_read_is_a_warning(self):
        self.fake.add(102, 71, 10, {"site_report_page": ["garbage"]})
        self.fake.add(103, 71, 20, {"site_report_page": _kept(103, 20)})
        r = rc.find(self.api, self.p(), self.NOW)
        self.assertEqual([x["job"] for x in r["reports"]], [103])
        self.assertIn("job 102 (T71): its report cannot be read - not a kept report", r["warnings_text"][0])

    def test_no_auditor_role(self):
        self.fake.forbid = True
        with self.assertRaises(IOError) as e:
            rc.find(self.api, self.p(template_id=0), self.NOW)
        self.assertIn("HTTP 403", str(e.exception))
        self.assertIn("Organization Auditor on the organizations of the report templates", str(e.exception))

    def test_times(self):
        self.assertEqual(rc.parse_time("2026-10-24T06:01:12.5Z"), datetime.datetime(2026, 10, 24, 6, 1, 12, 500000))
        self.assertEqual(rc.parse_time("2026-10-24T06:01:12Z"), datetime.datetime(2026, 10, 24, 6, 1, 12))
        self.assertIsNone(rc.parse_time("yesterday"))
        self.assertIsNone(rc.parse_time(None))


PWSH = shutil.which("pwsh")
READ_PS = os.path.join(REPO, "roles", "site_email", "files", "site_report_web_read.ps1")


def clm(script, env=None):
    """Run a .ps1 in Constrained Language Mode (as a locked-down server would); its ###SITE-JSON### answer."""
    wrapper = "$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'\n. '%s'\n" % script.replace("'", "''")
    out = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", wrapper], capture_output=True, text=True,
                         env=dict(os.environ, **(env or {})), timeout=120)
    line = [x for x in out.stdout.splitlines() if x.startswith("###SITE-JSON### ")]
    assert line, (out.stdout, out.stderr)
    return json.loads(line[-1][16:])


class SiteScript(unittest.TestCase):
    """0.63.0: an atlas page's script, written beside the index as site-report.js, and loaded from there by the page
    and the index (a Content-Security-Policy with script-src 'self' runs it)."""
    PAGE = ('<html><head><script>try{}catch(e){}</script>\n<script src="site-report.js"></script>\n</head><body>'
            '<script id="site-report-js">\n(function(){if(window.siteReport)return;window.siteReport=1;})();\n\n</script></body></html>')

    def test_the_script_out_of_the_page(self):
        self.assertEqual(rw.page_script(self.PAGE), "(function(){if(window.siteReport)return;window.siteReport=1;})();\n")
        self.assertEqual(rw.page_script("<html><script>x</script></html>"), "")       # an email look: none

    def test_loaded_from_the_top_of_the_folder(self):
        js = rw.page_script(self.PAGE)
        v = hashlib.sha1(js.encode("utf-8")).hexdigest()[:10]
        self.assertEqual(rw.script_tag("Linux - health check/latest.html", js), '<script src="../site-report.js?v=%s"></script>' % v)
        self.assertEqual(rw.script_tag("site/a/b/p.html", js), '<script src="../../../site-report.js?v=%s"></script>' % v)
        self.assertEqual(rw.script_tag("index.html", js), '<script src="site-report.js?v=%s"></script>' % v)
        self.assertNotEqual(rw.script_tag("index.html", js + " "), rw.script_tag("index.html", js))   # a new script: a new address

    def test_the_index_loads_it(self):
        html = rw.index_html([], "index.html", "x", None, "now", (), "atlas", "var a;\n")
        self.assertIn(rw.ATLAS_EARLY + "\n" + rw.script_tag("index.html", "var a;\n"), html)
        self.assertIn("if(window.siteReport)return;", html)                           # its own script then does nothing
        self.assertNotIn("<script src=", rw.index_html([], "index.html", "x", None, "now", (), "atlas"))
        self.assertNotIn("<script src=", rw.index_html([], "index.html", "x", None, "now", (), "portal", "var a;\n"))

    def test_a_page_with_a_script_file_of_its_own(self):
        """0.88.0: a page whose script is not the atlas one names its file (data-file; the ticket insights' site-insights.js):
        written at the folder's top under that name, loaded by the page from there; the index never loads it."""
        page = ('<html><body><script src="site-insights.js"></script><script id="site-report-js" data-file="site-insights.js">\n'
                '(function(){if(window.siteInsights)return;window.siteInsights=1;})();\n</script></body></html>')
        js = rw.page_script(page)
        self.assertEqual(js, "(function(){if(window.siteInsights)return;window.siteInsights=1;})();\n")
        self.assertEqual(rw.page_script_name(page), "site-insights.js")
        self.assertEqual(rw.page_script_name(self.PAGE), "site-report.js")
        v = hashlib.sha1(js.encode("utf-8")).hexdigest()[:10]
        self.assertEqual(rw.script_tag("ticket-insights/ops-linux/latest.html", js, "site-insights.js"),
                         '<script src="../../site-insights.js?v=%s"></script>' % v)
        self.assertEqual(rw.page_script_name('<script id="site-report-js" data-file="../evil">x</script>'), "site-report.js")   # not a plain name: ignored

    def test_the_real_script_parses_and_is_ascii(self):
        with open(os.path.join(REPO, "roles", "site_email", "templates", "site_report.js")) as f:
            js = f.read()
        js.encode("ascii")
        self.assertIn("window.siteReport", js)


class Windows(unittest.TestCase):
    def test_text_with_the_end_of_a_here_string(self):
        self.assertEqual(rw._ps_text("a"), "(@'\na\n'@)")
        self.assertEqual(rw._ps_text("x\n'@y"), "(@'\nx\n'@) + \"`n'@\" + (@'\ny\n'@)")
        self.assertTrue(rw._ps_text("'@z").startswith("'''@' + "))

    def test_staged_ascii_and_the_index_read_before(self):
        st = rw.Staged("index.json", {"reports": [{"key": "a"}]})          # Ansible may have read the JSON already
        self.assertEqual(json.loads(st.read("index.json")), {"reports": [{"key": "a"}]})
        self.assertIsNone(st.read("other.json"))
        self.assertIsNone(rw.Staged("index.json", "").read("index.json"))
        st.write("a/p.html", "<p>café —</p>", "")
        st.write("a/p.html", "<p>again</p>", "")
        self.assertEqual(st.files, [("a/p.html", "<p>again</p>")])
        st.write("a/q.html", "<p>café —</p>", "")
        self.assertEqual(st.files[-1][1], "<p>caf&#233; &#8212;</p>")

    def test_why_the_server_gave_no_answer(self):
        where = "web01.example.mil (winrm, port 5986)"
        says = sf.site_web_win_error({"unreachable": True, "msg": "ntlm: the specified credentials\nwere rejected"}, where)
        self.assertIn("could not reach report_web_host web01.example.mil (winrm, port 5986): ntlm: the specified credentials were rejected", says)
        self.assertIn("Report website credential", says)
        self.assertIn("failed: Access is denied", sf.site_web_win_error({"rc": 1, "stderr": "Access is denied"}, where))
        self.assertIn("no answer (rc 0)", sf.site_web_win_error({"rc": 0}, where))

    @unittest.skipUnless(PWSH, "no pwsh")
    def test_scripts_in_constrained_language_mode(self):
        tmp = tempfile.mkdtemp()
        dest = os.path.join(tmp, "D reports")
        os.mkdir(dest)
        page = "<p>it's $x `y \"z\"</p>\n'@ at a line's start\n"
        files = [("t/p.html", page), ("t/latest.html", page), ("index.json", '{"reports": []}\n')]
        script = os.path.join(tmp, "w.ps1")
        with open(script, "wb") as f:
            f.write(rw.windows_script(dest, files).encode("utf-8-sig"))
        self.assertEqual(rw.windows_script(dest, files).count("<p>it's"), 1)             # the same text once
        self.assertIn("Save-File @('t', 'latest.html') $t0 $true", rw.windows_script(dest, files, (), ["t/latest.html"]))
        out = clm(script)["write"]
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["written"], ["t/p.html", "t/latest.html", "index.json"])
        with open(os.path.join(dest, "t", "latest.html"), "rb") as f:
            self.assertEqual(f.read(), page.encode("ascii"))                       # exactly: no BOM, no newline added
        self.assertEqual(sorted(os.listdir(os.path.join(dest, "t"))), ["latest.html", "p.html"])
        with open(script, "wb") as f:                                   # older runs deleted - one not there is fine
            f.write(rw.windows_script(dest, files[1:], ["t/p.html", "t/gone.json"]).encode("utf-8-sig"))
        out = clm(script)["write"]
        self.assertTrue(out["ok"], out)
        self.assertEqual((out["deleted"], out["left"]), (["t/p.html"], []))
        self.assertEqual(sorted(os.listdir(os.path.join(dest, "t"))), ["latest.html"])
        cfg = {"SITE_CHECK_CONFIG": json.dumps({"dest": dest, "index": ["index.json"]})}
        self.assertEqual(clm(READ_PS, cfg)["read"], {"ok": True, "error": "", "index": '{"reports": []}\n'})
        self.assertIsNone(clm(READ_PS, {"SITE_CHECK_CONFIG": json.dumps({"dest": dest, "index": ["none.json"]})})["read"]["index"])
        gone = clm(READ_PS, {"SITE_CHECK_CONFIG": json.dumps({"dest": dest + "-gone", "index": []})})["read"]
        self.assertFalse(gone["ok"])
        self.assertIn("is not there on", gone["error"])
        shutil.rmtree(dest)
        out = clm(script)["write"]
        self.assertFalse(out["ok"])
        self.assertIn("is not there on", out["error"])
        self.assertFalse(os.path.exists(dest))                                      # never made


class BriefIndex(unittest.TestCase):
    """0.92.0: the index page in the brief look - every report by area, and the first step each report's line carries
    (entry.step). 0.94.0: calm - a plain title and one line (how many, the last run, the counts as small chips), no
    alarm headline and no cards: the list is the page, a report's first step under its line."""

    def reports(self):
        runs = [_run(9, "2026-10-09 06:00 UTC", "critical"), _run(8, "2026-10-08 06:00 UTC", "warning")]
        return [{"key": "d/latest.html", "name": "Linux - Disk <space>", "status": "critical", "when": runs[0]["when"], "job": 9,
                 "page": runs[0]["page"], "latest": "d/latest.html", "title": "4 <full>", "runs": runs,
                 "step": {"title": "/var is <95%>", "code": "du -xh </var>", "status": "critical"}},
                {"key": "v/latest.html", "name": "VMware - VM health", "status": "warning", "when": "2026-10-09 05:00 UTC", "job": "local",
                 "page": "v/b.html", "latest": "v/latest.html", "title": "3 snapshots"},
                {"key": "c/latest.html", "name": "Linux - Certificates", "status": "ok", "when": "2026-10-09 04:00 UTC", "job": 7,
                 "page": "c/b.html", "latest": "c/latest.html", "title": "none expire"}]

    def test_the_brief(self):
        html = rw.index_html(self.reports(), "site/index.html", "HQ & ops", ("data:image/png;base64,AA", 48, 64), "now", (), "brief")
        self.assertIn('<section class="hero"><h1>Reports</h1><p class="say">3 reports &middot; last run 2026-10-09 06:00 UTC</p>', html)
        self.assertIn('<div class="tally"><span class="chip s-critical"><svg', html)                  # the counts as small chips
        self.assertIn('<b>1</b> Critical</span><span class="chip s-warning">', html)
        self.assertNotIn(" Info</span>", html)                                      # none for information: no chip
        self.assertNotRegex(html, r'need(s)? action|class="(nb|bento|counts?)[ "]')    # no alarm headline, no cards
        self.assertIn('<div class="runs"><div class="step"><span class="lbl">Start here</span><p class="stt">/var is &lt;95%&gt;</p>'
                      '<div class="code"><pre>du -xh &lt;/var&gt;</pre><button class="copy" type="button" hidden>Copy</button></div>'
                      '<a class="open" href="../d/latest.html">Open the report &rarr;</a></div><table>', html)
        self.assertEqual(html.count('class="step"'), 1)                             # only a report that gives one
        self.assertIn("Linux - Disk &lt;space&gt;", html)
        self.assertIn('<img class="logo" src="data:image/png;base64,AA" width="48" height="64"', html)
        self.assertIn(".logo{display:block;border-radius:9px;height:auto;max-height:60px;width:auto}", html)
        self.assertNotRegex(html, r'<link|src="https?:|@import')
        self.assertNotIn("<space>", html)
        self.assertNotIn("job local", html)

    def test_no_poster_type(self):
        """Nothing on the index set larger than a heading (the user: some text was "crazy big")."""
        html = rw.index_html(self.reports(), "index.html", "x", None, "now", (), "brief")
        style = html.split("<style>")[1].split("</style>")[0]
        for size in re.findall(r"font-size:clamp\([^,]+,[^,]+,\s*([\d.]+)(rem|px)\)", style):
            self.assertLessEqual(float(size[0]) * (16 if size[1] == "rem" else 1), 34, size)

    def test_every_report_by_area(self):
        html = rw.index_html(self.reports(), "site/index.html", "HQ", None, "now", (), "brief")
        self.assertIn('<div class="grp a1"><b>Linux</b><span class="n">2</span></div>', html)
        self.assertIn('<div class="grp a2"><b>VMware</b><span class="n">1</span></div>', html)
        self.assertLess(html.index('<div class="grp a1">'), html.index('<div class="grp a2">'))
        self.assertIn("#t1:checked~.list>:not(.a1){display:none}", html)           # an area's tab: its heading and lines
        self.assertIn('<label for="t1"><span class="dot c-critical"></span>Linux <span class="n">2</span></label>', html)
        # the last runs as squares, oldest to newest, the newest ringed, each a link to its page
        self.assertIn('<span class="sqs" aria-label="The last 2 runs"><a class="sq c-warning" href="../', html)
        self.assertRegex(html, r'<a class="sq c-critical now" href="\.\./[^"]+" title="2026-10-09 06:00 UTC - Critical')

    def test_nothing_to_do_and_none_yet(self):
        ok = [dict(r, status="ok") for r in self.reports()]
        html = rw.index_html(ok, "index.html", "x", None, "now", (), "brief")
        self.assertIn('<div class="tally"><span class="chip s-ok"><svg', html)
        self.assertNotIn('class="step"', html)                                      # a fine report's step: not shown
        empty = rw.index_html([], "index.html", "x", None, "now", (), "brief")
        self.assertIn("No reports yet", empty)
        self.assertNotIn('class="tab"', empty)
        self.assertNotIn('class="tally"', empty)

    def test_colours_as_the_page(self):
        """The index page's colours are the brief report page's (roles/site_email/templates/report_brief.html.j2)."""
        import re
        with open(os.path.join(REPO, "roles", "site_email", "templates", "report_brief.html.j2")) as f:
            tpl = f.read()
        light = tpl[tpl.index("{%- set light %}"):tpl.index("{%- set dark %}")]
        dark = tpl[tpl.index("{%- set dark %}"):tpl.index("{%- endset %}", tpl.index("{%- set dark %}"))]
        for mine, theirs in ((rw.BRIEF_LIGHT, light), (rw.BRIEF_DARK, dark)):
            tokens = dict(re.findall(r"(--[\w-]+):([^;]+);", mine))
            self.assertGreater(len(tokens), 30)
            for k, v in tokens.items():
                self.assertIn("%s:%s;" % (k, v), theirs, k)

    def test_switch_without_the_script(self):
        html = rw.index_html([], "index.html", "x", None, "now", (), "brief")
        self.assertIn('<div class="seg" id="theme" role="radiogroup" aria-label="Light or dark">', html)
        self.assertIn(":root:has(#theme-dark:checked){--ink:#e0e0e0;", html)
        self.assertIn('<script src="site-report.js?v=', rw.index_html([], "index.html", "x", None, "now", (), "brief", "var a;\n"))

    def test_first_step(self):
        self.assertEqual(rw.first_step({"sections": [{"title": "a", "lines": ["x"]}, {"cards": [{"title": "t", "code": "c", "status": "warning"}]}]}),
                         {"title": "t", "code": "c", "more": False, "status": "warning"})
        self.assertIsNone(rw.first_step({"sections": [{"cards": [{"meta": "nothing to do"}]}]}))
        self.assertIsNone(rw.first_step(None))
        self.assertIsNone(rw.first_step({"sections": "not a list of sections"[:0]}))
        self.assertEqual(rw.first_step({"sections": [{"cards": [{"code": "x" * 5000}]}]})["code"], "")      # long: on the page
        self.assertTrue(rw.first_step({"sections": [{"cards": [{"code": "x" * 5000}]}]})["more"])

    def test_a_long_step_stays_short(self):
        """0.94.0: a STIG rule's whole check (a 20-line policy line, say) never lands on the index - the title on one
        line, the command only when it is short, a link to the report for the rest."""
        check = "\n".join("CRYPTO_POLICY='-oCiphers=aes256-gcm@example.mil,aes%d-ctr -oMACs=hmac-sha2-512'" % i for i in range(20))
        step = rw.first_step({"sections": [{"cards": [{"status": "critical", "title": "The system must use   approved ciphers.\n" + "y" * 400,
                                                      "code": check}]}]})
        self.assertEqual(step["code"], "")
        self.assertTrue(step["more"])
        self.assertLessEqual(len(step["title"]), 200)
        self.assertTrue(step["title"].startswith("The system must use approved ciphers. yyy"))
        self.assertTrue(step["title"].endswith("\u2026"))
        self.assertEqual(rw.first_step({"sections": [{"cards": [{"title": "t", "code": "a\nb\nc"}]}]})["code"], "a\nb\nc")
        r = dict(BriefIndex.reports(self)[0], step=step)
        html = rw.index_html([r], "site/index.html", "x", None, "now", (), "brief")
        self.assertNotIn("CRYPTO_POLICY", html)
        self.assertIn('<a class="open" href="../d/latest.html">The command and the rest in the report &rarr;</a>', html)
        self.assertIn("-webkit-line-clamp:2", html)                                 # the title at most 2 lines
        old = dict(r, step={"title": "t", "code": check, "status": "critical"})            # a line 0.92.0 wrote
        self.assertNotIn("CRYPTO_POLICY", rw.index_html([old], "site/index.html", "x", None, "now", (), "brief"))

    def test_web_page_brief(self):
        page = rw.web_page('<html><head><title>t</title></head><body></body></html>', [], "brief")
        self.assertNotIn("first-of-type", page)                                  # a web page already: nothing added


class Retries(unittest.TestCase):
    """0.98.1: a failure that passes is tried again from a new login (site_report_web.passing); a real one is not; a
    website copy that still fails is said at the top of the email (report_notice)."""
    USER = ("Failed to authenticate with server: SpnegoError (4294967295): Major (851968): Unspecified GSS failure.  "
            "Minor code may provide more information, Minor (2529638953): Message stream modified, Context: Processing security token")

    def test_passing(self):
        for why, word in ((self.USER, "message stream modified"),
                          ("Cannot contact any KDC for realm 'EXAMPLE.MIL'", "cannot contact any kdc"),
                          ("[Errno 104] Connection reset by peer", "connection reset"),
                          ("[Errno 111] Connection refused", "connection refused"),
                          ("timed out", "timed out"),
                          ("Received unexpected status from the server: STATUS_NETWORK_NAME_DELETED (0xc00000c9)", "network_name_deleted"),
                          ("STATUS_USER_SESSION_DELETED: 0xc0000203", "user_session_deleted"),
                          ("STATUS_NETWORK_SESSION_EXPIRED", "network_session_expired"),
                          ("uploading https://web/r/x.html: HTTP 503 Service Unavailable", "http 503"),
                          ("writing /mnt/reports/x.html: Stale file handle", "stale file handle")):
            with self.subTest(why=why):
                self.assertEqual(rw.passing(rw.Fail("logging in to fs01: " + why)), word)

    def test_not_passing(self):
        for why in ("[Error 3221225581] STATUS_LOGON_FAILURE: The attempted logon is invalid",
                    "STATUS_ACCESS_DENIED", "STATUS_BAD_NETWORK_NAME", "Configuration file does not specify default realm",
                    "Unspecified GSS failure.  Minor (2529638918): Server not found in Kerberos database",
                    "Unspecified GSS failure.  Minor (2529638936): Preauthentication failed",
                    "Unspecified GSS failure.  Minor (2529638949): Clock skew too great",
                    "Cannot find KDC for realm \"EXAMPLE.MIL\"",
                    "the folder /mnt/reports is not there - is the share mounted",
                    "uploading https://web/r/x.html: HTTP 401 Unauthorized", "HTTP 403 Forbidden", "HTTP 404 Not Found",
                    "unsupported hash type md4"):
            with self.subTest(why=why):
                self.assertIsNone(rw.passing(rw.Fail(why)))

    def test_notice(self):
        r = {"title": "T", "sections": [{"title": "A", "lines": ["x"]}]}
        self.assertIs(sf.report_notice(r, ""), r)
        n = sf.report_notice(r, "The report website copy failed: x; the email is the full report.")
        self.assertEqual([s["title"] for s in n["sections"]], ["Report website", "A"])
        self.assertEqual(n["sections"][0]["status"], "warning")
        self.assertEqual(len(r["sections"]), 1)                        # the report itself unchanged
        self.assertIn("The report website copy failed: x", sf.report_text(n))
        self.assertLess(sf.report_text(n).index("copy failed"), sf.report_text(n).index("\nA\n"))



# the module run as Ansible runs it: its arguments in a file, the repo's module_utils as ansible.cfg gives them; patch
# (Python, with m the module) runs before main() - a target that changes the folder half-way, say
BOOT = ("import importlib.util, json, os, sys\n"
        "args, module, utils, patch = sys.argv[1:5]\n"
        "for n in ('site_aap', 'site_report_page', 'site_banner', 'site_time'):\n"
        "    sp = importlib.util.spec_from_file_location('ansible.module_utils.' + n, os.path.join(utils, n + '.py'))\n"
        "    u = importlib.util.module_from_spec(sp)\n"
        "    sys.modules['ansible.module_utils.' + n] = u\n"
        "    sp.loader.exec_module(u)\n"
        "sp = importlib.util.spec_from_file_location('site_report_web', module)\n"
        "m = importlib.util.module_from_spec(sp)\n"
        "sp.loader.exec_module(m)\n"
        "exec(patch)\n"
        "sys.argv = ['m', args]\n"
        "m.main()\n")


def run_module(args, patch="", env=None):
    """site_report_web's answer (a dict) to args."""
    argf = os.path.join(tempfile.mkdtemp(), "args.json")
    with open(argf, "w") as f:
        json.dump({"ANSIBLE_MODULE_ARGS": dict({"retries": 0}, **args)}, f)
    out = subprocess.run([sys.executable, "-c", BOOT, argf, os.path.join(REPO, "roles", "site_email", "library", "site_report_web.py"),
                          os.path.join(REPO, "plugins", "module_utils"), patch], capture_output=True, text=True,
                         env=dict(os.environ, **(env or {})))
    try:
        return json.loads(out.stdout)
    except ValueError:
        raise AssertionError(out.stdout[-800:] + out.stderr[-800:])


def web_args(dest, page="vm/2026-10-01_0600_job-1.html", key="vm/latest.html", banner=None, **kw):
    entry = {"key": key, "name": "VMware - VM health report", "title": "VM health", "status": "ok", "when": "2026-10-01 06:00 UTC", "job": 1}
    return dict({"dest": dest, "files": {"page": page, "latest": key, "index": "index.html"}, "entry": entry,
                 "html": "<html><head></head><body><p>report</p></body></html>",
                 "data": {"report": {"title": "VM health"}, "banner": banner or {}}}, **kw)


class Traversal(unittest.TestCase):
    """1.24.0: a kept report's file names stay inside report_web_dest - refused before anything is written, whatever the
    target (a folder, a share, an upload, the Windows script)."""
    BAD = ("../../outside/evil.html", "/etc/evil.html", "\\evil.html", "C:/evil.html", "D:evil.html", "a\\..\\evil.html",
           "a/./evil.html", "a//evil.html", "a/..", "a/evil.htm", ".", "")

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.dest = os.path.join(self.root, "a", "reports")
        os.makedirs(self.dest)

    def files(self):
        return sorted(os.path.relpath(os.path.join(dp, f), self.root) for dp, _, fs in os.walk(self.root) for f in fs)

    def test_the_rule(self):
        for rel in ("index.html", "vm/latest.html", "site/vm/2026/ok-1.html"):
            self.assertTrue(rw.safe_name(rel), rel)
            self.assertTrue(rc.safe_name(rel), rel)
        for rel in self.BAD + (None, 5):
            self.assertFalse(rw.safe_name(rel), rel)
            self.assertFalse(rc.safe_name(rel), rel)
        self.assertTrue(rw.safe_name("site-report.js", rw.WRITTEN))

    def test_a_folder_target_writes_nothing(self):
        for k in ("page", "latest", "index"):
            with self.subTest(name=k):
                args = web_args(self.dest)
                args["files"][k] = "../../outside/evil.html"
                r = run_module(args)
                self.assertTrue(r.get("failed"), r)
                self.assertIn("'../../outside/evil.html': not a file name inside report_web_dest", r["msg"])
                self.assertEqual(self.files(), [])
        r = run_module(web_args(self.dest, pages=[{"page": "../x/evil.html", "html": "<p>x</p>"}]))
        self.assertTrue(r.get("failed"), r)
        self.assertEqual(self.files(), [])
        r = run_module(dict(web_args(self.dest, page="/tmp/evil.html"), required=False))        # a warning, not a failure
        self.assertEqual((r.get("failed"), r["published"]), (None, False))
        self.assertIn("not a file name inside report_web_dest", r["error"])
        self.assertEqual(self.files(), [])
        r = run_module(dict(web_args(self.dest, page="../../evil.html"), keep=True))            # never kept for the copy job
        self.assertTrue(r.get("failed"), r)
        r = run_module(web_args(self.dest))                                                      # a good name is written
        self.assertTrue(r["published"], r)
        self.assertIn("a/reports/vm/2026-10-01_0600_job-1.html", self.files())

    def test_no_windows_script_for_them(self):
        tmp = tempfile.mkdtemp()
        r = run_module(web_args("D:\\reports", page="..\\..\\inetpub\\wwwroot\\default.html", windows_host="web01.example.mil"),
                       env={"TMPDIR": tmp})
        self.assertTrue(r.get("failed"), r)
        self.assertNotIn("script", r)
        self.assertEqual(os.listdir(tmp), [])
        with self.assertRaises(rw.Fail):
            rw.windows_script("D:\\reports", [("../../inetpub/wwwroot/default.html", "x")])
        with self.assertRaises(rw.Fail):
            rw.windows_script("D:\\reports", [("a/p.html", "x")], remove=["../x.html"])
        self.assertIn("Save-File @('a', 'p.html')", rw.windows_script("D:\\reports", [("a/p.html", "x")]))

    def test_the_copy_job_names_the_report_and_goes_on(self):
        good, bad = kept_doc(907), kept_doc(908)
        bad["files"]["index"] = "../../inetpub/wwwroot/default.html"
        warnings, out = [], []

        class Aap(object):
            def call(self, method, path):
                i = int(path.split("/")[1])
                return 200, {"id": i, "name": "T", "artifacts": {"site_report_page": pg.pack(good if i == 907 else bad, 1 << 20)[0]}}
        self.assertTrue(rc.kept(Aap(), {"id": 908}, out, warnings))
        self.assertTrue(rc.kept(Aap(), {"id": 907}, out, warnings))
        self.assertEqual([r["job"] for r in out], [907])
        self.assertEqual(len(warnings), 1)
        self.assertIn("job 908 (T): its page linux-certificate-trust-report/2026-10-23_0605_job-908.html is not copied - "
                      "../../inetpub/wwwroot/default.html: not a file name inside report_web_dest", warnings[0])


class IndexBanner(unittest.TestCase):
    """1.24.0: the index carries the highest banner among its lines' runs - an unclassified run never takes a classified
    report's marking off the index."""
    SECRET = {"on": True, "text": "SECRET//NOFORN", "bg": "#c8102e", "fg": "#ffffff", "subject": True, "enclave": "classified"}

    def test_the_highest(self):
        ts = {"on": True, "text": "TOP SECRET", "bg": "#ff8c00", "fg": "#111111"}
        cui = {"on": True, "text": "CUI", "bg": "#502b85", "fg": "#ffffff"}
        unc = {"on": True, "text": "UNCLASSIFIED", "bg": "#007a33", "fg": "#ffffff"}
        line = lambda *bs: {"key": "k", "page": "p.html", "runs": [{"page": "p%d.html" % i, "banner": b} for i, b in enumerate(bs)]}  # noqa: E731
        self.assertEqual(rw.top_banner({}, [line(None, self.SECRET), line(unc)])["text"], "SECRET//NOFORN")
        self.assertEqual(rw.top_banner(unc, [line(cui), line(ts)])["text"], "TOP SECRET")
        self.assertEqual(rw.top_banner(cui, [line(unc)])["text"], "CUI")
        self.assertEqual(rw.top_banner({}, [line(None), {"key": "old", "page": "o.html"}]), {})   # lines from before: none
        self.assertTrue(rw.from_before([{"key": "k", "banner": {}, "runs": [{"page": "a.html", "banner": {}}, {"page": "b.html"}]}]))
        self.assertFalse(rw.from_before([dict(line(None, unc), banner=unc)]))
        self.assertEqual(rw.top_banner({}, [{"key": "k", "banner": {}, "runs": [{"page": "a.html", "banner": {}},
                                                                               {"page": "b.html"}]}], self.SECRET)["text"], "SECRET//NOFORN")
        self.assertEqual(rw.top_banner(dict(self.SECRET, bg="#c8102e"), [line(dict(self.SECRET, bg="#000000"))])["bg"], "#c8102e")  # this run's own
        odd = rw.top_banner({}, [line({"on": True, "text": "x", "bg": "red;background:url(x)", "fg": "\"><b>"})])
        self.assertEqual((odd["bg"], odd["fg"]), ("#4d4d4d", "#ffffff"))             # colours checked

    def test_an_unclassified_run_after_a_classified_one(self):
        dest = tempfile.mkdtemp()
        r = run_module(web_args(dest, banner=self.SECRET))
        self.assertTrue(r["published"], r)
        r = run_module(web_args(dest, page="os/2026-10-01_0600_job-2.html", key="os/latest.html"))
        self.assertTrue(r["published"], r)
        with open(os.path.join(dest, "index.html")) as f:
            index = f.read()
        self.assertEqual(index.count('class="site-banner '), 2)
        self.assertIn("SECRET//NOFORN", index)
        with open(os.path.join(dest, "os", "latest.html")) as f:
            self.assertNotIn("site-banner", f.read())                                # the page keeps its own (none)
        with open(os.path.join(dest, "index.json")) as f:
            lines = dict((x["key"], x) for x in json.load(f)["reports"])
        self.assertEqual(lines["vm/latest.html"]["banner"]["text"], "SECRET//NOFORN")
        self.assertEqual(lines["vm/latest.html"]["runs"][0]["banner"]["text"], "SECRET//NOFORN")
        self.assertEqual(lines["os/latest.html"]["banner"], {})                     # 1.24.0b: the key, empty (a line from now)
        self.assertEqual(lines["os/latest.html"]["runs"][0]["banner"], {})          # 1.24.0c: a run from now has the key too

    def test_an_upgraded_site_keeps_its_banner(self):
        """1.24.0b: an index written before 1.24.0 (its lines without a banner key) carried SECRET: unclassified runs keep
        it while such a line is listed - from its index.html, then from index.json's own banner - and the marking goes
        only when every older line has been written again."""
        dest = tempfile.mkdtemp()
        old = {"key": "vm/latest.html", "name": "VM health", "status": "critical", "page": "vm/2026-09-01_0600_job-1.html",
               "latest": "vm/latest.html", "when": "2026-09-01 06:00 UTC", "runs": [{"page": "vm/2026-09-01_0600_job-1.html",
               "status": "critical", "when": "2026-09-01 06:00 UTC", "job": 1, "title": "VM health"}]}
        os.makedirs(os.path.join(dest, "vm"))
        for rel in ("vm/latest.html", "vm/2026-09-01_0600_job-1.html"):                 # its pages are there (not pruned)
            with open(os.path.join(dest, rel), "w") as f:
                f.write("<html><body>VM health</body></html>")
        with open(os.path.join(dest, "index.json"), "w") as f:
            json.dump({"generated": "2026-09-01 06:00 UTC", "reports": [old]}, f)
        with open(os.path.join(dest, "index.html"), "w") as f:
            f.write(rw.banner_inject("<html><body><h1>Reports</h1></body></html>", self.SECRET))
        self.assertEqual(rw.index_banner("", open(os.path.join(dest, "index.html")).read())["text"], "SECRET//NOFORN")

        def index():
            with open(os.path.join(dest, "index.html")) as f:
                html = f.read()
            with open(os.path.join(dest, "index.json")) as f:
                return html, json.load(f)
        r = run_module(web_args(dest, page="os/2026-10-01_0600_job-2.html", key="os/latest.html"))
        self.assertTrue(r["published"], r)
        html, doc = index()
        self.assertIn("SECRET//NOFORN", html)                                             # from the index.html before
        self.assertEqual(doc["banner"]["text"], "SECRET//NOFORN")
        r = run_module(web_args(dest, page="os/2026-10-02_0600_job-3.html", key="os/latest.html"))
        html, doc = index()
        self.assertIn("SECRET//NOFORN", html)                                             # now from index.json's own
        r = run_module(web_args(dest, page="vm/2026-10-03_0600_job-4.html", key="vm/latest.html"))   # its next run: unclassified
        html, doc = index()
        lines = dict((x["key"], x) for x in doc["reports"])
        self.assertEqual(lines["vm/latest.html"]["banner"], {})
        self.assertNotIn("banner", lines["vm/latest.html"]["runs"][1])                    # its SECRET run from before, still listed
        self.assertIn("SECRET//NOFORN", html)                                             # 1.24.0c: so the marking stays
        self.assertEqual(doc["banner"]["text"], "SECRET//NOFORN")
        for day in range(4, 8):                                                           # 4 more: the old run leaves its 5
            run_module(web_args(dest, page="vm/2026-10-%02d_0600_job-%d.html" % (day, day + 1), key="vm/latest.html"))
        html, doc = index()
        self.assertNotIn("vm/2026-09-01_0600_job-1.html", json.dumps(doc))
        self.assertNotIn("site-banner", html)                                             # every line and run from now: none left
        self.assertEqual(doc["banner"], {})

    def test_a_banner_the_entry_brings_is_not_kept(self):
        dest = tempfile.mkdtemp()
        args = web_args(dest)
        args["entry"]["banner"] = {"on": True, "text": "x", "bg": "#000000", "fg": "#ffffff"}
        self.assertTrue(run_module(args)["published"])
        with open(os.path.join(dest, "index.json")) as f:
            self.assertEqual(json.load(f)["reports"][0]["banner"], {})


class IndexReadAgain(unittest.TestCase):
    """1.24.0: index.json is read again just before it is written - a line another job wrote in between stays."""
    def test_a_line_written_meanwhile_stays(self):
        dest = tempfile.mkdtemp()
        self.assertTrue(run_module(web_args(dest))["published"])
        # the next run: while it looks for the lines' latest pages (prune), another job writes its own line
        patch = ("import json, os\n"
                 "Base = m.Target\n"
                 "class T(Base):\n"
                 "    def exists(self, rel):\n"
                 "        path = os.path.join(self.dest, 'index.json')\n"
                 "        doc = json.load(open(path))\n"
                 "        doc['reports'].append({'key': 'other/latest.html', 'name': 'Other', 'status': 'ok', 'latest': 'other/latest.html',\n"
                 "                               'page': 'other/1.html', 'when': '2026-10-01 05:00 UTC'})\n"
                 "        json.dump(doc, open(path, 'w'))\n"
                 "        return Base.exists(self, rel)\n"
                 "m.Target = T\n")
        args = web_args(dest, page="os/2026-10-01_0600_job-2.html", key="os/latest.html", drop=["gone/*"])
        self.assertTrue(run_module(args, patch)["published"])
        with open(os.path.join(dest, "index.json")) as f:
            keys = sorted(x["key"] for x in json.load(f)["reports"])
        self.assertEqual(keys, ["os/latest.html", "other/latest.html", "vm/latest.html"])
        with open(os.path.join(dest, "index.html")) as f:
            self.assertIn("other/latest.html", f.read())


class NoRedirect(unittest.TestCase):
    """1.24.0: the upload client follows no redirect - the login never goes to another address; the 3xx is a failure
    that names where it pointed."""
    def setUp(self):
        self.seen = []
        seen = self.seen

        class Other(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def any(self):
                seen.append((self.command, self.headers.get("Authorization")))
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(b"{}")
            do_GET = do_HEAD = do_PUT = do_DELETE = do_MKCOL = any

        self.other = ThreadingHTTPServer(("127.0.0.1", 0), Other)
        to = "http://127.0.0.1:%d/idp/login" % self.other.server_port

        class First(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def any(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(n)
                self.send_response(302)
                self.send_header("Location", to)
                self.send_header("Content-Length", "0")
                self.end_headers()
            do_GET = do_HEAD = do_PUT = do_DELETE = do_MKCOL = any

        self.first = ThreadingHTTPServer(("127.0.0.1", 0), First)
        for srv in (self.other, self.first):
            threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.to = to
        os.environ["REPORT_WEB_TOKEN"] = "t0ken"

    def tearDown(self):
        os.environ.pop("REPORT_WEB_TOKEN", None)
        for srv in (self.other, self.first):
            srv.shutdown()
            srv.server_close()

    def test_a_302_is_not_followed(self):
        t = rw.Target("http://127.0.0.1:%d/reports" % self.first.server_port, "", 5)
        for what, call in (("read", lambda: t.read("index.json")), ("exists", lambda: t.exists("vm/latest.html")),
                           ("write", lambda: t.write("vm/p.html", "<p>x</p>", "text/html")), ("remove", lambda: t.remove("vm/p.html")),
                           ("folder", lambda: t._folders("vm/p.html"))):
            with self.subTest(what=what):
                with self.assertRaises(rw.Fail) as e:
                    call()
                self.assertIn("HTTP 302", str(e.exception))
                self.assertIn("a redirect to %s, which is not followed" % self.to, str(e.exception))
                self.assertIsNone(rw.passing(e.exception))                              # not tried again
        self.assertEqual(self.seen, [])                                                 # the other address saw nothing, no login
        r = run_module(web_args("http://127.0.0.1:%d/reports" % self.first.server_port), env={"REPORT_WEB_TOKEN": "t0ken"})
        self.assertTrue(r.get("failed"), r)
        self.assertIn(self.to, r["msg"])
        self.assertEqual(self.seen, [])


class TimeZone(unittest.TestCase):
    """1.49.0: with report_time_zone the index page shows its times in that time zone, each with its abbreviation -
    what is stored (when, index.json, the order of the runs) stays UTC, and '' is the page as it was."""
    tm = sys.modules["ansible.module_utils.site_time"]
    shown = staticmethod(getattr(tm, "local"))

    def reports(self):
        return [{"key": "vm/latest.html", "name": "VMware - VM health report", "title": "VM health", "status": "warning",
                 "latest": "vm/latest.html", "page": "vm/3.html", "when": "2026-11-13 04:37 UTC", "job": 4651,
                 "runs": [{"page": "vm/3.html", "status": "warning", "when": "2026-11-13 04:37 UTC", "job": 4651, "title": "VM health"},
                          {"page": "vm/2.html", "status": "ok", "when": "2026-07-13 22:30 UTC", "job": 4000, "title": "VM health"},
                          {"page": "vm/1.html", "status": "ok", "when": "", "job": 3999, "title": "VM health"}]}]

    def test_winter_and_summer_time(self):
        tz, note = self.tm.zone("Europe/Berlin")
        self.assertEqual(note, "")
        self.assertEqual(self.shown("2026-11-13 04:37 UTC", tz), "2026-11-13 05:37 CET")
        self.assertEqual(self.shown("2026-07-13 04:37 UTC", tz), "2026-07-13 06:37 CEST")        # summer time, as on that day
        self.assertEqual(self.shown("2026-07-13 22:30 UTC", tz), "2026-07-14 00:30 CEST")        # the day too
        self.assertEqual(self.shown("2026-10-21 06:01", tz), "2026-10-21 08:01 CEST")            # the strip's, no UTC word
        self.assertEqual(self.shown("2026-11-13 04:37 UTC", self.tm.zone("America/New_York")[0]), "2026-11-12 23:37 EST")
        for text in ("this run", "", "now", "job 12", "2026-02-31 04:37 UTC", None):               # not a time: as it is
            self.assertEqual(self.shown(text, tz), text)

    def test_no_time_zone_is_the_text_as_stored(self):
        for name in ("", "  ", None, "UTC", "utc", "Etc/UTC", "GMT", "Z"):
            self.assertEqual(self.tm.zone(name), (None, ""), name)
        self.assertEqual(self.shown("2026-11-13 04:37 UTC"), "2026-11-13 04:37 UTC")
        for look in ("portal", "atlas", "brief"):
            args = (self.reports(), "site/index.html", "HQ", None, "2026-11-13 04:38 UTC", (), look)
            self.assertEqual(rw.index_html(*args), rw.index_html(*args, js="", tz=None), look)
            self.assertIn("2026-11-13 04:37 UTC", rw.index_html(*args))

    def test_a_name_not_known_is_utc_with_a_note(self):
        tz, note = self.tm.zone("Mars/Olympus_Mons")
        self.assertIsNone(tz)
        self.assertIn("report_time_zone Mars/Olympus_Mons is not a time zone this execution environment knows", note)
        self.assertIn("tzdata", note)
        self.assertIn("UTC", note)
        saved, self.tm.zoneinfo = self.tm.zoneinfo, None                # a Python without zoneinfo (before 3.9)
        try:
            tz, note = self.tm.zone("Europe/Berlin")
        finally:
            self.tm.zoneinfo = saved
        self.assertIsNone(tz)
        self.assertIn("no zoneinfo", note)

    def test_index_page_in_each_look(self):
        tz = self.tm.zone("Europe/Berlin")[0]
        for look in ("portal", "atlas", "brief"):
            html = rw.index_html(self.reports(), "site/index.html", "HQ", None, "2026-11-13 04:38 UTC", (), look, "", tz)
            self.assertEqual(html.count("2026-11-13 05:37 CET"), 4 if look == "brief" else 2, look)   # the line, its newest run (brief: "last run" and its square too)
            self.assertIn("2026-11-13 05:37 CET</a> (newest)", html.replace(' <span style="color:#5b6b66;font-size:12px;">', " ").replace("</span>", ""))
            self.assertIn("2026-07-14 00:30 CEST", html)
            self.assertIn("updated 2026-11-13 05:38 CET", html)
            self.assertIn(">this run</a>", html)                         # a run without a time, as before
            self.assertNotIn(" UTC", html, look)
        brief = rw.index_html(self.reports(), "index.html", "HQ", None, "now", (), "brief", "", tz)
        self.assertIn("last run 2026-11-13 05:37 CET", brief)
        self.assertIn('title="2026-07-14 00:30 CEST - Successful - VM health"', brief)

    def run_twice(self, **second):
        dest = tempfile.mkdtemp()
        self.assertTrue(run_module(web_args(dest))["published"])        # a run from before the update: no time_zone
        with open(os.path.join(dest, "index.html")) as f:
            self.assertIn("2026-10-01 06:00 UTC", f.read())
        args = web_args(dest, page="vm/2026-11-13_0437_job-2.html", **second)
        args["entry"] = dict(args["entry"], when="2026-11-13 04:37 UTC", job=2)
        out = run_module(args)
        self.assertTrue(out["published"])
        with open(os.path.join(dest, "index.html")) as f:
            html = f.read()
        with open(os.path.join(dest, "index.json")) as f:
            doc = json.load(f)
        return out, html, doc

    def test_module_shows_older_runs_in_the_zone_and_stores_utc(self):
        for look in ("portal", "atlas", "brief"):
            out, html, doc = self.run_twice(time_zone="Europe/Berlin", theme=look)
            self.assertIn("2026-11-13 05:37 CET", html)
            self.assertIn("2026-10-01 08:00 CEST", html)                 # written before the update, shown local too
            self.assertNotIn(" UTC", html, look)
            self.assertNotIn("report_time_zone", json.dumps(out.get("warnings") or []))
            line = doc["reports"][0]
            self.assertEqual([x["when"] for x in line["runs"]], ["2026-11-13 04:37 UTC", "2026-10-01 06:00 UTC"])
            self.assertEqual(line["when"], "2026-11-13 04:37 UTC")
            self.assertRegex(doc["generated"], r"^\d{4}-\d\d-\d\d \d\d:\d\d UTC$")
            self.assertNotIn("CET", json.dumps(doc))

    def test_module_unknown_zone_is_utc_said_once(self):
        out, html, doc = self.run_twice(time_zone="Mars/Olympus_Mons")
        said = json.dumps(out.get("warnings") or [])                    # 2.16: texts; 2.22: a summary of each
        self.assertEqual(said.count("report_time_zone Mars/Olympus_Mons is not a time zone this execution environment knows"), 1, said)
        self.assertIn("2026-11-13 04:37 UTC", html)
        self.assertIn("2026-10-01 06:00 UTC", html)

    def test_module_empty_zone_writes_the_same_bytes(self):
        stamp = re.compile(r"updated \d{4}-\d\d-\d\d \d\d:\d\d UTC")
        for look in ("portal", "atlas", "brief"):
            a = self.run_twice(theme=look)                                # no time_zone at all, as before 1.49.0
            b = self.run_twice(theme=look, time_zone="")
            c = self.run_twice(theme=look, time_zone="UTC")
            self.assertEqual(len(stamp.findall(a[1])), 1, look)
            self.assertEqual(stamp.sub("updated", a[1]), stamp.sub("updated", b[1]), look)
            self.assertEqual(stamp.sub("updated", a[1]), stamp.sub("updated", c[1]), look)
            self.assertEqual(dict(a[2], generated=""), dict(b[2], generated=""))
            self.assertNotIn("report_time_zone", json.dumps(b[0].get("warnings") or []))


if __name__ == "__main__":
    unittest.main(verbosity=1)
