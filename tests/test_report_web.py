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
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
sys.path.insert(0, os.path.join(REPO, "roles", "site_email", "library"))
for _name in ("site_aap", "site_report_page", "site_banner", "site_time", "site_report_files", "site_festive"):   # as Ansible gives them to the modules (ansible.cfg: module_utils)
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

    def test_a_folder_of_its_own(self):
        """1.74.0: a dry run's files go in their own folder whatever the patterns use - a literal latest page or
        {template_id} would otherwise put them over the real run's. The index stays shared."""
        for pats in (PATTERNS, dict(PATTERNS, latest="patch/latest.html"),
                     {"page": "{template_id}/{job}.html", "latest": "{template_id}/latest.html", "index": "index.html"}):
            real = sf.report_web_files(pats, NAMES)
            dry = sf.report_web_folder(sf.report_web_files(pats, dict(NAMES, template=NAMES["template"] + " - dry run")), "dry-run")
            self.assertEqual(dry["index"], real["index"])
            for k in ("page", "latest"):
                self.assertTrue(dry[k].startswith("dry-run/"), dry[k])
                self.assertNotEqual(dry[k], real[k])
        self.assertEqual(sf.report_web_folder({"page": "a/b.html", "latest": "", "index": "i.html"}, "dry-run"),
                         {"page": "dry-run/a/b.html", "latest": "", "index": "i.html"})
        self.assertEqual(sf.report_web_folder(real, ""), real)
        for bad in ("../x", "a/b", ".x"):
            with self.assertRaises(sf._FilterError):
                sf.report_web_folder(real, bad)

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
        """1.72.0: the board in the atlas look - escaped names, links from the index's folder, a tab per area with no
        script (radio buttons), each area's card, a line's last runs."""
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
        self.assertIn('<label for="t1">VMware<span class="tn">1</span>', html)
        self.assertIn('#t1:checked~.grid .area:not(.a1){display:none}', html)
        self.assertIn("The last 2 runs keep their pages", html)
        self.assertIn('<section class="area a1', html)
        self.assertNotRegex(html, r'<link|src="https?:|@import')
        self.assertIn("No reports yet.", rw.index_html([], "index.html", "x", None, "now", (), "atlas"))
        self.assertNotIn('class="tab"', rw.index_html([], "index.html", "x", None, "now", (), "atlas"))

    def test_atlas_switch_without_the_script(self):
        """The index page's light / auto / dark switch: radio buttons the CSS acts on, so it works where a web server's
        Content-Security-Policy blocks the page's script; the script only keeps the choice."""
        html = rw.index_html([], "index.html", "x", None, "now", (), "atlas")
        self.assertIn('<div class="seg" id="theme" role="radiogroup" aria-label="Light or dark">', html)   # not hidden
        for t in ("light", "auto", "dark"):
            self.assertIn('<input type="radio" name="site-report-theme" id="theme-%s" value="%s"' % (t, t), html)
            self.assertIn('<label for="theme-%s"' % t, html)
        self.assertIn('id="theme-auto" value="auto" checked>', html)
        self.assertIn(":root:has(#theme-light:checked){--bg:#f3f5f7;", html)
        self.assertIn(":root:has(#theme-dark:checked){--bg:#0b0f14;", html)
        self.assertIn(':root[data-theme="dark"]{--bg:#0b0f14;', html)      # older browsers without :has(): the script
        self.assertIn(rw.script_tag("index.html", rw.INDEX_SCRIPT, rw.INDEX_SCRIPT_FILE), html)
        self.assertIn("localStorage.setItem('site-report-theme',n)", rw.INDEX_SCRIPT)
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
        self.assertEqual(sorted(line["runs"][0]), sorted(set(rw.RUN_KEYS) - {"banner", "counts"}))   # counts: only when counted
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
        never.add("Workflow dry run - the guard (1.107.0)")               # not a job template: a guard other playbooks import
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
        v = hashlib.sha256(js.encode("utf-8")).hexdigest()
        self.assertEqual(rw.script_tag("Linux - health check/latest.html", js), '<script src="../site-report.%s.js"></script>' % v)
        self.assertEqual(rw.script_tag("site/a/b/p.html", js), '<script src="../../../site-report.%s.js"></script>' % v)
        self.assertEqual(rw.script_tag("index.html", js), '<script src="site-report.%s.js"></script>' % v)
        self.assertNotEqual(rw.script_tag("index.html", js + " "), rw.script_tag("index.html", js))   # a new script: a new address

    def test_the_index_loads_it(self):
        html = rw.index_html([], "index.html", "x", None, "now", (), "atlas", "var a;\n")
        self.assertIn(rw.script_tag("index.html", "var a;\n"), html)
        self.assertNotIn("<script>", html)
        self.assertNotIn('<script src="site-report.', rw.index_html([], "index.html", "x", None, "now", (), "atlas"))
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
        r = clm(READ_PS, cfg)["read"]
        sha = hashlib.sha256(b'{"reports": []}\n').hexdigest().upper()
        self.assertEqual(r, {"ok": True, "error": "", "index": '{"reports": []}\n', "gone": [], "sha": sha, "pending": []})   # 1.79.14: sha
        self.assertIsNone(clm(READ_PS, {"SITE_CHECK_CONFIG": json.dumps({"dest": dest, "index": ["none.json"]})})["read"]["index"])
        gone = clm(READ_PS, {"SITE_CHECK_CONFIG": json.dumps({"dest": dest + "-gone", "index": []})})["read"]
        self.assertFalse(gone["ok"])
        self.assertIn("is not there on", gone["error"])
        shutil.rmtree(dest)
        out = clm(script)["write"]
        self.assertFalse(out["ok"])
        self.assertIn("is not there on", out["error"])
        self.assertFalse(os.path.exists(dest))                                      # never made


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
        "for n in ('site_aap', 'site_report_page', 'site_banner', 'site_time', 'site_report_files', 'site_festive'):\n"
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
    """site_report_web's answer (a dict) to args. Its temporary files (a Windows server's script) in a folder of its own
    (1.79.14: never left in the machine's /tmp by a test that does not delete them)."""
    argf = os.path.join(tempfile.mkdtemp(), "args.json")
    env = dict({"TMPDIR": os.path.dirname(argf)}, **(env or {}))
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
        html = rw.index_html(self.reports(), "site/index.html", "HQ", None, "2026-11-13 04:38 UTC", (), "portal", "", tz)
        self.assertEqual(html.count("2026-11-13 05:37 CET"), 2)                    # the line, its newest run
        self.assertIn("2026-11-13 05:37 CET</a> (newest)", html.replace(' <span style="color:#5b6b66;font-size:12px;">', " ").replace("</span>", ""))
        self.assertIn("2026-07-14 00:30 CEST", html)
        self.assertIn("updated 2026-11-13 05:38 CET", html)
        self.assertIn(">this run</a>", html)                                 # a run without a time, as before
        self.assertNotIn(" UTC", html)
        for look in rw.WEB_LOOKS:                                             # 1.72.0, the board: the time alone on its own day
            html = rw.index_html(self.reports(), "site/index.html", "HQ", None, "2026-11-13 04:38 UTC", (), look, "", tz)
            self.assertIn('<span class="when" title="2026-11-13 05:37 CET">05:37</span>', html, look)
            self.assertIn('title="2026-11-13 05:37 CET - job 4651">05:37</a> <span class="mutec">(newest)</span>', html, look)
            self.assertIn('title="2026-07-14 00:30 CEST - job 4000">14 Jul 00:30</a>', html, look)    # another day: the day too
            self.assertIn("Updated <b>2026-11-13 05:38 CET</b>", html, look)
            self.assertIn(">this run</a>", html, look)
            self.assertNotIn(" UTC", html, look)

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
        stamp = re.compile(r"[Uu]pdated (?:<b>)?\d{4}-\d\d-\d\d \d\d:\d\d UTC")
        for look in ("portal", "atlas", "brief"):
            a = self.run_twice(theme=look)                                # no time_zone at all, as before 1.49.0
            b = self.run_twice(theme=look, time_zone="")
            c = self.run_twice(theme=look, time_zone="UTC")
            self.assertEqual(len(stamp.findall(a[1])), 1, look)
            self.assertEqual(stamp.sub("updated", a[1]), stamp.sub("updated", b[1]), look)
            self.assertEqual(stamp.sub("updated", a[1]), stamp.sub("updated", c[1]), look)
            self.assertEqual(dict(a[2], generated=""), dict(b[2], generated=""))
            self.assertNotIn("report_time_zone", json.dumps(b[0].get("warnings") or []))


class Release1720(unittest.TestCase):
    """1.72.0, the index's data (whatever its look): each run's findings by level (the report's own counts, else its rows'
    row_status), a trend of the last 12 runs kept apart from the pages, the change since the previous run, when a
    report is late (judged by site-report.js when the page is read), the four tiles' data, and Copy link on each line."""

    REPORT = {"title": "x", "status": "critical", "sections": [
        {"title": "Findings", "columns": ["a"], "rows": [["1"], ["2"], ["3"], ["4"]], "row_status": ["critical", "warning", "warning", ""]},
        {"title": "Low", "columns": ["a"], "rows": [["5"]], "row_status": ["low"]},
        {"title": "Start here", "cards": [{"status": "critical", "title": "do this"}]},
        {"title": "Notes", "lines": ["n"]}]}

    def line(self, runs, key="vm/latest.html", latest_url="", launch=""):
        """A line after these runs: [(when, status, counts or None)], oldest first; launch (1.118.0) on each run."""
        line = None
        for i, (when, status, counts) in enumerate(runs):
            e = dict(_run(i + 1, when, status), key=key, name="VMware - VM health report", latest=key)
            if launch:
                e["launch"] = launch
            if counts is not None:
                e["counts"] = counts
            if latest_url:
                e["latest_url"] = latest_url
            line, _ = rw.history(line, e, 5)
        return line

    def test_counts_given_or_from_the_rows(self):
        self.assertEqual(rw.run_counts(self.REPORT), {"critical": 1, "warning": 2, "low": 1})
        self.assertEqual(rw.run_counts(dict(self.REPORT, counts={"critical": 3, "warning": 0})), {"critical": 3, "warning": 0, "low": 0})
        self.assertEqual(rw.run_counts(dict(self.REPORT, counts={"critical": "x"})), {"critical": 1, "warning": 2, "low": 1})   # not numbers: the rows
        self.assertEqual(rw.run_counts({"title": "t", "sections": [{"title": "s", "rows": [], "row_status": []}]}), {"critical": 0, "warning": 0, "low": 0})
        self.assertIsNone(rw.run_counts({"title": "t", "sections": [{"title": "Notes", "lines": ["a"]}]}))     # only informs
        self.assertIsNone(rw.run_counts(None))

    def test_the_trend_keeps_12_whatever_the_pages(self):
        runs = [("2026-11-%02d 06:00 UTC" % d, "warning", {"critical": d % 3, "warning": 2, "low": 0}) for d in range(1, 15)]
        line = self.line(runs)
        self.assertEqual(len(line["runs"]), 5)                                             # the pages: history 5
        self.assertEqual([x["when"][:10] for x in line["trend"]], ["2026-11-%02d" % d for d in range(14, 2, -1)])   # 12, newest first
        self.assertEqual(line["trend"][0]["counts"], {"critical": 2, "warning": 2, "low": 0})
        self.assertEqual(sorted(line["trend"][0]), ["counts", "job", "status", "when"])
        # a line from before 1.72.0 (no trend) starts from its runs; the copy job's run already there is there once
        old = dict(line)
        old.pop("trend")
        e = dict(_run(99, "2026-11-15 06:00 UTC"), key="vm/latest.html", name="n", latest="vm/latest.html")
        new, _ = rw.history(old, e, 5)
        self.assertEqual([x["job"] for x in new["trend"]], [99, 14, 13, 12, 11, 10])
        again, _ = rw.history(new, e, 5)
        self.assertEqual([x["job"] for x in again["trend"]], [99, 14, 13, 12, 11, 10])
        self.assertIsNone(new["trend"][0]["counts"])                                        # not counted: a gap

    def test_since_the_previous_run(self):
        up = self.line([("2026-11-16 06:00 UTC", "critical", {"critical": 1, "warning": 4, "low": 0}),
                        ("2026-11-17 06:00 UTC", "critical", {"critical": 3, "warning": 4, "low": 1})])
        self.assertEqual(rw.run_delta(up), {"critical": 2, "warning": 0, "low": 1})
        down = self.line([("2026-11-16 06:00 UTC", "critical", {"critical": 5, "warning": 4, "low": 0}),
                          ("2026-11-17 06:00 UTC", "critical", {"critical": 2, "warning": 6, "low": 0})])
        self.assertEqual(rw.run_delta(down)["critical"], -3)
        self.assertIsNone(rw.run_delta(self.line([("2026-11-16 06:00 UTC", "ok", None), ("2026-11-17 06:00 UTC", "ok", {"critical": 0})])))
        self.assertIsNone(rw.run_delta(self.line([("2026-11-17 06:00 UTC", "ok", {"critical": 0})])))

    def test_two_runs_in_the_same_minute(self):
        """1.72.0b (Codex P2): two runs in the same minute - the one just added is the newest, in the trend as in the
        runs: critical 1 -> 3 is +2 and red, and the sparkline ends on the new count."""
        same = "2026-11-17 06:00 UTC"
        line = self.named("Health - AD", "ad/latest.html", [(same, "critical", {"critical": 1, "warning": 0, "low": 0}),
                                                            (same, "critical", {"critical": 3, "warning": 0, "low": 0})])
        self.assertEqual([x["job"] for x in line["runs"]], [2, 1])
        self.assertEqual([x["job"] for x in line["trend"]], [2, 1])                       # as the runs: the same order
        self.assertEqual(rw.run_delta(line)["critical"], 2)
        self.assertEqual(rw.index_summary([line])["critical_up"][0]["delta"], 2)
        self.assertEqual(rw.spark(line)["vals"], [1, 3])                                  # ends on the new count
        html = self.board([line])
        self.assertIn('class="tile t-bad"', html)
        self.assertIn("Since the previous run: +2 critical</span>", html)
        self.assertIn('<b class="spv">3</b>', html)
        for _ in range(3):                                                               # a third run, same minute again
            line, _ = rw.history(line, dict(_run(9, same, "critical"), key="ad/latest.html", name="Health - AD", latest="ad/latest.html",
                                            counts={"critical": 0, "warning": 0, "low": 0}), 5)
        self.assertEqual([x["job"] for x in line["trend"]], [9, 2, 1])                    # once, newest first
        self.assertEqual(rw.run_delta(line)["critical"], -3)

    def test_when_a_report_is_late(self):
        # 1.118.0: scheduled runs (the newest one's launch); the steady rule: Late1180
        daily = self.line([("2026-11-%02d 06:%02d UTC" % (d, d) , "ok", None) for d in range(10, 15)], launch="scheduled")
        self.assertEqual(rw.due_hours(daily), 48)                                           # twice its usual gap
        hourly = self.line([("2026-11-17 %02d:00 UTC" % h, "ok", None) for h in range(1, 6)], launch="scheduled")
        self.assertEqual(rw.due_hours(hourly), 26)                                          # at least 26 h
        weekly = self.line([("2026-10-%02d 06:00 UTC" % d, "ok", None) for d in (6, 13, 20, 27)], launch="scheduled")
        self.assertEqual(rw.due_hours(weekly), 336)
        self.assertIsNone(rw.due_hours(self.line([("2026-11-17 06:00 UTC", "ok", None)], launch="scheduled")))   # one run: no judgement

    def test_the_tiles_data(self):
        a = self.line([("2026-11-16 06:00 UTC", "critical", {"critical": 1, "warning": 0, "low": 0}),
                       ("2026-11-17 06:00 UTC", "critical", {"critical": 3, "warning": 0, "low": 0})], key="ad/latest.html")
        a["name"] = "AD health"
        b = self.line([("2026-11-17 06:00 UTC", "unknown", None)], key="nas/latest.html")
        b["name"] = "NetApp ONTAP health"
        c = self.line([("2026-11-16 06:00 UTC", "critical", {"critical": 5, "warning": 0, "low": 0}),
                       ("2026-11-17 06:00 UTC", "critical", {"critical": 2, "warning": 0, "low": 0})], key="vm/latest.html")
        s = rw.index_summary([a, b, c])
        self.assertEqual((s["total"], s["runs_ok"], s["not_checked"]), (3, 2, ["NetApp ONTAP health"]))
        self.assertEqual(s["critical_up"], [{"name": "AD health", "key": "ad/latest.html", "delta": 2, "before": 1, "now": 3}])

    def named(self, name, key, runs, latest_url="", launch=""):
        line = self.line(runs, key=key, latest_url=latest_url, launch=launch)
        line["name"] = name
        return line

    def board(self, reports, look="atlas", base=""):
        return rw.index_html(reports, "index.html", "Site reports", None, "2026-11-17 07:00 UTC", (), look, "", None, base)

    def test_the_sparkline_draws_as_the_dashboard(self):
        """Each line's sparkline - its critical and warning findings over its last runs - is drawn as nuc-dev's dashboard
        draws its weekly line (report_dash_filters.dash_path): the same points, a gap where a run was not counted."""
        runs = [("2026-11-%02d 06:00 UTC" % d, "critical", c) for d, c in
                [(1, {"critical": 4, "warning": 2, "low": 9}), (2, None), (3, {"critical": 1, "warning": 2, "low": 0}),
                 (4, {"critical": 3, "warning": 0, "low": 0}), (5, {"critical": 0, "warning": 0, "low": 0})]]
        sp = rw.spark(self.line(runs))
        self.assertEqual(sp["vals"], [6, None, 3, 3, 0])                                  # oldest first, low not drawn
        self.assertEqual((sp["max"], sp["top"]), (6, 6))
        sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
        import report_dash_filters as dash
        same = dash.dash_path(sp["vals"], sp["top"], rw.SPARK_W, rw.SPARK_H, rw.SPARK_PAD)
        self.assertEqual((sp["d"], sp["pts"]), (same["d"], same["pts"]))
        self.assertTrue(sp["d"].startswith("M4.0 0.0 M"))                                 # the gap lifts the pen
        self.assertEqual(rw.spark(self.line(runs), "critical")["vals"], [4, None, 1, 3, 0])
        self.assertEqual(rw.spark(self.line(runs[-1:]))["top"], 4)                       # a small count is not drawn as a peak
        self.assertIsNone(rw.spark(self.line([("2026-11-01 06:00 UTC", "info", None)])))  # nothing counted: no line
        html = self.board([self.named("Health - AD", "ad/latest.html", runs)])
        self.assertIn('role="img" aria-label="Critical and warning findings, last 5 runs, oldest first: 6, -, 3, 3, 0"', html)
        self.assertIn('<b class="spv">0</b>', html)
        info = self.named("Tickets - insights", "t/latest.html", [("2026-11-17 06:00 UTC", "info", None)])
        self.assertIn('<span class="nos">report only</span>', self.board([info]))

    def test_calm_unless_a_run_needs_a_look(self):
        """Findings are counts in plain ink: a report with critical findings that did not go up is calm. Red only for a
        run that could not check and a critical count up since the run before (and, by the script, a late report)."""
        same = self.named("Health - VM", "vm/latest.html", [("2026-11-16 06:00 UTC", "critical", {"critical": 3, "warning": 1, "low": 0}),
                                                             ("2026-11-17 06:00 UTC", "critical", {"critical": 3, "warning": 2, "low": 0})])
        down = self.named("Health - Linux", "lx/latest.html", [("2026-11-16 06:00 UTC", "critical", {"critical": 5, "warning": 0, "low": 0}),
                                                               ("2026-11-17 06:00 UTC", "critical", {"critical": 2, "warning": 0, "low": 0})])
        calm = self.board([same, down])
        self.assertNotIn('class="tile t-bad"', calm)
        self.assertNotIn('<section class="look', calm)                              # 1.118.0: nothing worse, nothing can be late: no box
        self.assertNotIn('class="flag">', calm)                                     # only the hidden late word
        self.assertIn('<span class="ct"><i class="sh sh-c" aria-hidden="true"></i>3 critical</span>', calm)
        self.assertIn('<span class="calm">Since the previous run: &minus;3 critical</span>', calm)
        self.assertIn('<span class="ah good">', calm)
        self.assertIn("All 2 ran", calm)
        self.assertIn("Findings in the newest runs:", calm)
        self.assertIn("<b>5</b> critical", calm)
        up = self.named("Health - AD", "ad/latest.html", [("2026-11-16 06:00 UTC", "critical", {"critical": 1, "warning": 4, "low": 0}),
                                                          ("2026-11-17 06:00 UTC", "critical", {"critical": 3, "warning": 4, "low": 0})])
        nc = self.named("Health - NAS", "nas/latest.html", [("2026-11-16 06:00 UTC", "ok", {"critical": 0, "warning": 0, "low": 0}),
                                                            ("2026-11-17 06:00 UTC", "unknown", None)])
        hot = self.board([same, down, up, nc])
        self.assertEqual(hot.count('class="tile t-bad"'), 2)                         # Not checked, Critical up
        self.assertIn('data-tile="ok" data-n="3">3</b><span class="of">of 4</span>', hot)
        self.assertIn("Health - AD: 1 &rarr; 3 critical", hot)
        self.assertIn("Health - NAS: the last run could not check", hot)
        self.assertIn('<span class="flag">', hot)
        self.assertIn("Since the previous run: +2 critical</span>", hot)
        self.assertIn('<h2 id="look-h">Look at these first</h2>', hot)
        self.assertIn('<section class="look lf-crit" aria-labelledby="look-h" data-cap="5">', hot)    # 1.118.0: the thin red top only when worse
        self.assertIn('<h3 class="lf-gh">Got worse <span class="n">2</span></h3>', hot)
        self.assertIn('<b>+2 critical</b><span class="lf-ft">1&#8202;&rarr;&#8202;3</span>', hot)
        self.assertIn('<span class="lf-d lf-nc">', hot)
        self.assertNotIn("Since the previous run: +2 critical (1", hot)
        self.assertIn('<b class="ahn">2</b> <span class="ahv">need</span> a look', hot)
        self.assertIn('<i class="tdot"></i>', hot)                                    # its tab's dot, with its words
        nums = [m.group(1) for m in re.finditer(r'<details class="rr[^"]*" id="r\d+" data-name="([^"]+)"', hot)]
        self.assertEqual(nums, ["Health - AD", "Health - NAS", "Health - Linux", "Health - VM"])   # those that need a look first

    def test_each_look_its_colours_readable(self):
        """Each look's board colours: its own surfaces, every text colour 4.5:1 or better on the card and the page, the
        status shapes 3:1 - light and dark."""
        def lum(h):
            c = [int(h[k:k + 2], 16) / 255.0 for k in (1, 3, 5)]
            c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
            return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

        def ratio(a, b):
            la, lb = sorted((lum(a), lum(b)), reverse=True)
            return (la + 0.05) / (lb + 0.05)
        for look in rw.WEB_LOOKS:
            for mode, t in zip(("light", "dark"), rw.BOARD_LOOK[look]):
                self.assertEqual(sorted(t), sorted(rw.BOARD_TOKENS), look)
                for k, v in t.items():
                    self.assertRegex(v, r"^#[0-9a-f]{6}$", (look, mode, k))
                for text in ("ink", "ink2", "mute", "accent", "crit", "ok"):
                    for on in ("card", "bg", "card2"):
                        self.assertGreaterEqual(ratio(t[text], t[on]), 4.5, (look, mode, text, on))
                for shape in ("warn", "crit-fill", "spark"):
                    self.assertGreaterEqual(ratio(t[shape], t["card"]), 3.0, (look, mode, shape))
            html = self.board([], look)
            self.assertIn(":root{--bg:%s;" % rw.BOARD_LOOK[look][0]["bg"], html)
            self.assertIn(':root[data-theme="dark"]{--bg:%s;' % rw.BOARD_LOOK[look][1]["bg"], html)
            self.assertIn('<body class="board look-%s">' % look, html)

    def test_copy_link_on_each_line(self):
        """Copy link beside each report's name: its latest page's address (report_web_url), else the page's own address
        made by the script from the link (data-rel); hidden until the script shows it."""
        runs = [("2026-11-17 06:00 UTC", "ok", {"critical": 0, "warning": 0, "low": 0})]
        a = self.named("Health - VM", "vm health/latest.html", runs, latest_url="https://reports.example.mil/vm%20health/latest.html")
        html = self.board([a])
        self.assertIn('<a class="rn" href="vm%20health/latest.html">Health - VM</a><button class="tcopy lcopy" type="button" '
                      'data-copy="https://reports.example.mil/vm%20health/latest.html" title="Copy the address of its latest page" hidden>Copy link</button>', html)
        a.pop("latest_url")
        self.assertIn('data-copy="https://reports.example.mil/x/vm%20health/latest.html"', self.board([a], base="https://reports.example.mil/x/"))
        self.assertIn('data-copy="vm%20health/latest.html" data-rel=""', self.board([a]))
        lone = {"key": "k", "name": "Lone", "status": "ok", "when": "x"}
        self.assertNotIn("Copy link", self.board([lone]))                             # no page: nothing to copy

    def test_wide_areas(self):
        """An area of five reports or more takes the row (its lines in two columns); so does one left alone in its row."""
        self.assertEqual(rw._place_areas(["H", "V", "C", "S", "N", "P"], {"H": 7, "V": 4, "C": 3, "S": 3, "N": 3, "P": 2}), {"H", "P"})
        self.assertEqual(rw._place_areas(["A", "B", "C"], {"A": 1, "B": 6, "C": 2}), {"A", "B", "C"})
        self.assertEqual(rw._place_areas(["A", "B"], {"A": 1, "B": 2}), set())

    def test_the_script_marks_a_late_report(self):
        sys.path.insert(0, HERE)
        try:
            import test_report_hover as RH
            from test_report_fit import _browser
        except ImportError:
            self.skipTest("no browser helpers")
        if not _browser():
            self.skipTest("no Chrome")
        now = datetime.datetime.now(datetime.timezone.utc)
        when = lambda h: (now - datetime.timedelta(hours=h)).strftime("%Y-%m-%d %H:%M UTC")
        c0 = {"critical": 0, "warning": 0, "low": 0}
        late = self.named("Backup - nightly", "late/latest.html", [(when(h), "ok", c0) for h in (100, 76, 52)], launch="scheduled")   # daily, 52 h ago
        fine = self.named("Backup - weekly", "fine/latest.html", [(when(h), "ok", c0) for h in (50, 26, 2)], launch="scheduled")     # daily, 2 h ago
        worse = [self.named("Patch - %d" % i, "p%d/latest.html" % i, [(when(26), "critical", dict(c0, critical=1)),
                                                                       (when(2), "critical", dict(c0, critical=1 + i))]) for i in range(1, 7)]
        with open(os.path.join(REPO, "roles", "site_email", "templates", "site_report.js"), encoding="utf-8") as f:
            js = f.read()
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "index.html"), "w") as f:
                f.write(rw.index_html([late, fine] + worse, "index.html", "Site reports", None, when(0), (), "ops", js, None, ""))
            with open(os.path.join(d, rw.report_script_file(js)), "w") as f:
                f.write(js)
            srv = ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **k: RH.Csp(*a, directory=d, **k))
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            c = RH.Cdp(_browser(), 1280, 900)
            try:
                c.call("Page.enable")
                c.call("Page.navigate", url="http://127.0.0.1:%d/index.html" % srv.server_address[1])
                for _ in range(100):
                    if c.js("document.readyState === 'complete' && !!window.siteReport"):
                        break
                    time.sleep(0.1)
                got = c.js("""(() => {const q = s => document.querySelector(s);
                    const rows = [...document.querySelectorAll('details.rr')].map(r => [r.dataset.name, r.hasAttribute('data-late'),
                        !(r.querySelector('.late-slot') || {hidden: true}).hidden, getComputedStyle(r.querySelector('.si-late')).display !== 'none']);
                    const b = q('.lcopy'); b.click();
                    return {rows, late: q('[data-tile=late]').textContent, ok: q('[data-tile=ok]').textContent,
                            box: q('[data-tile-box=late]').className, note: q('[data-tile-note=late]').textContent,
                            look: !q('.look').hidden, li: [...document.querySelectorAll('.look li:not([hidden])')].length,
                            lateLine: q('.lf-late').hidden ? '' : q('.lf-lt').textContent, listShut: !q('.lf-ld').open,
                            head: [...document.querySelectorAll('.lf-gh+.lf-rows>li')].map(x => x.querySelector('.lf-n').textContent),
                            more: q('.lf-more').hidden ? '' : q('.lf-more .m1').textContent,
                            ahead: q('.area .ah.bad').hidden ? '' : q('.area .ah.bad').textContent, dot: !q('label[for=t1] .tdot').hidden,
                            copy: !b.hidden, open: b.closest('details').open}})()""")
            finally:
                c.close()
                srv.shutdown()
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(sorted(got["rows"])[:2], [["Backup - nightly", True, True, True], ["Backup - weekly", False, False, False]])
        self.assertEqual((got["late"], got["ok"]), ("1", "7"))
        self.assertIn("t-bad", got["box"])
        self.assertTrue(got["note"].startswith("Backup - nightly: last ran "), got["note"])
        self.assertEqual(got["look"], True)
        self.assertEqual(got["lateLine"], "1 report is late:Backup - nightly")       # 1.118.0: one line, the list shut under it
        self.assertTrue(got["listShut"])
        self.assertEqual(got["head"], ["Patch - 6", "Patch - 5", "Patch - 4", "Patch - 3"])   # the late line takes the 5th line
        self.assertEqual(got["more"], "Show 2 more")
        self.assertEqual(got["ahead"], "1 needs a look")
        self.assertTrue(got["dot"])
        self.assertTrue(got["copy"])
        self.assertFalse(got["open"])                                                  # Copy link copies, the line stays shut

class LookFirst1180(unittest.TestCase):
    """1.118.0: Look at these first - "Got worse" one line per report, the late reports one line (site-report.js), at most 5
    lines; only a report AAP runs on a schedule or with a steady cadence can be late; the Site portal link."""

    def line(self, name, gaps, launch="", crit=(0, 0), status="ok", title="t"):
        """A line whose runs are gaps hours apart (oldest first), its newest run 1 h ago; crit: the critical counts, oldest first."""
        t = datetime.datetime(2026, 11, 23, 8, 0, tzinfo=datetime.timezone.utc)
        whens = [t]
        for g in reversed(gaps):
            whens.insert(0, whens[0] - datetime.timedelta(hours=g))
        line, key = None, name.lower().replace(" ", "-")
        for i, w in enumerate(whens):
            c = crit[min(i, len(crit) - 1)] if i >= len(whens) - len(crit) else 0
            e = dict(_run(i + 1, w.strftime("%Y-%m-%d %H:%M UTC"), status if i == len(whens) - 1 else "ok"),
                     key=key + "/latest.html", name=name, latest=key + "/latest.html", title=title,
                     counts={"critical": c, "warning": 0, "low": 0})
            if launch:
                e["launch"] = launch[i] if isinstance(launch, list) else launch
            line, _ = rw.history(line, e, 5)
        return line

    def board(self, reports, look="atlas", portal=""):
        return rw.index_html(reports, "index.html", "Site reports", None, "2026-11-23 09:00 UTC", (), look, "", None, "", "", portal)

    def test_1_only_scheduled_or_steady_reports_can_be_late(self):
        self.assertEqual(rw.due_hours(self.line("Steady", [24, 23, 25, 24, 26])), 48)              # 5 gaps within 25 %
        self.assertIsNone(rw.due_hours(self.line("Four gaps", [24, 24, 24, 24])))                   # too few to call steady
        odd = [9, 21, 15, 30, 6]
        self.assertIsNone(rw.due_hours(self.line("By hand", odd, "manual")))
        self.assertIsNone(rw.due_hours(self.line("A workflow's step", odd, "workflow")))
        self.assertEqual(rw.due_hours(self.line("Scheduled", odd, "scheduled")), 30)               # median 15, twice that
        self.assertIsNone(rw.due_hours(self.line("Now by hand", odd, ["scheduled"] * 5 + ["manual"])))   # the newest run decides
        self.assertTrue(rw.steady(self.line("Edge", [20, 25, 25, 25, 30])))                         # 25 +- 25 % holds
        self.assertFalse(rw.steady(self.line("Over", [18, 25, 25, 25, 30])))
        self.assertEqual(rw._trend_point({"when": "w", "launch": "scheduled"})["launch"], "scheduled")
        self.assertNotIn("launch", rw._trend_point({"when": "w"}))                                # a run that did not say: none

    def test_2_got_worse_one_line_each_at_most_five(self):
        ups = [self.line("Up %d" % i, [24], "scheduled", crit=(1, 1 + i)) for i in range(1, 8)]
        nc = self.line("Down here", [24], "scheduled", status="unknown")
        late = self.line("Late one", [24, 24, 24, 24, 24])
        late["trend"] = [dict(x, when=(rw._when_dt(x["when"]) - datetime.timedelta(hours=72)).strftime("%Y-%m-%d %H:%M UTC")) for x in late["trend"]]
        late["when"] = late["trend"][0]["when"]
        h = self.board(ups + [nc, late])
        box = h[h.index('<section class="look'):h.index('</section>', h.index('<section class="look')) + 10]
        self.assertIn('<h3 class="lf-gh">Got worse <span class="n">8</span></h3>', box)
        head = box[:box.index('<details class="lf-more">')]
        self.assertEqual(head.count('<li class="lf-r">'), 5)              # no row kept back: the script makes room for a late line
        self.assertIn('<span class="m1">Show 3 more</span>', box)
        self.assertIn('data-cap="5"', box)
        names = re.findall(r'<li class="lf-r"><a class="lf-n" href="[^"]*" title="([^"]*)"', box)
        self.assertEqual(names, ["Up 7", "Up 6", "Up 5", "Up 4", "Up 3", "Up 2", "Up 1", "Down here"])   # most first, not checked last
        self.assertIn('<li data-late-li="r9" data-name="Late one" hidden>', box)
        self.assertIn('<div class="lf-late" hidden>', box)                                   # no script: no late line
        self.assertNotIn("Since the previous run", box)
        # never "Show 1 more": the row shows instead
        five = self.board(ups[:4] + [nc, late])
        self.assertNotIn("Show 1 more", five)
        self.assertEqual(five.count('<li class="lf-r">'), 5)
        self.assertIn('<details class="lf-more" hidden><summary><span class="m1">Show 0 more</span>', five)   # there for the script
        ups = [self.line("Up %d" % i, [24], "manual", crit=(1, 1 + i)) for i in range(1, 8)]     # by hand: none can be late
        six = self.board(ups[:6])
        self.assertNotIn('<details class="lf-more">', six)
        self.assertEqual(six.count('<li class="lf-r">'), 6)
        seven = self.board(ups[:7])
        self.assertIn("Show 2 more", seven)

    def test_3_red_only_when_worse_and_the_summary_cut(self):
        long_ = "x" * 150
        h = self.board([self.line("Up", [24], "scheduled", crit=(1, 3), title=long_)])
        self.assertIn('<section class="look lf-crit"', h)
        self.assertIn('<span class="lf-s" title="%s">%s…</span>' % (long_, "x" * 109), h)
        late = self.line("Only late", [24, 24, 24, 24, 24], "scheduled")
        h = self.board([late])
        self.assertIn('<section class="look" aria-labelledby="look-h" data-cap="5" hidden>', h)          # late only: neutral, shown by the script
        self.assertIn("(every 24 hours)", h)
        self.assertNotIn("look lf-crit", h)
        hourly = self.board([self.line("Hourly", [1, 1, 1, 1, 1], "scheduled")])
        self.assertIn('data-due="26"', hourly)                                              # late after 26 h (the floor) ...
        self.assertIn("expected by 2026-11-23 09:00 UTC (every 1 hour)", hourly)              # ... expected every hour (review)
        quarter = self.board([self.line("Quarter", [0.25] * 5, "scheduled")])
        self.assertIn("expected by 2026-11-23 08:15 UTC (every 15 minutes)", quarter)
        far = self.line("Far", [24], "scheduled")
        far["trend"] = [dict(x, when=w) for x, w in zip(far["trend"], ("9999-12-31 08:00 UTC", "9999-12-30 08:00 UTC"))]
        far["when"] = far["trend"][0]["when"]
        self.assertIn("every 24 hours", self.board([far]))                                    # no date past 9999: no crash

    def test_4_the_portal_link(self):
        for ok in ("https://portal.example.mil/portal/", "/portal/", "http://192.0.2.5/portal"):
            self.assertEqual(rw.portal_href(ok), ok)
        for bad in ("https://portal\u00a0team.example.mil/", "", "auto", "javascript:alert(1)", "//elsewhere.example.mil/", "portal/", 'https://a.example.mil/"x', "/a b",
                    "/\\elsewhere.example.mil/", "https://?x", "https://h.example.mil:port/", "/portal/\x01"):
            self.assertEqual(rw.portal_href(bad), "", bad)
        r = [self.line("A", [24], "scheduled")]
        for look in rw.WEB_LOOKS:
            h = self.board(r, look, "/portal/")
            self.assertIn('<a class="plink" href="/portal/" title="The site portal: run jobs, follow them, approvals">Site portal', h, look)
            self.assertLess(h.index('class="plink"'), h.index('id="theme"'))                  # in the top bar, before Light / Auto / Dark
            self.assertNotIn('class="plink"', self.board(r, look, ""))
            self.assertNotIn('class="plink"', self.board(r, look, "javascript:x"))
        self.assertIn('<a class="plink" href="https://p.example.mil/portal"', self.board(r, "portal", "https://p.example.mil/portal"))
        self.assertNotIn('class="plink"', self.board(r, "portal", ""))
        page = "<html><head></head><body><div class=\"appbar-in\"><!--site-report-back--></div></body></html>"
        h = rw.back_link(page, "vm/latest.html", "index.html", None, "/portal/")
        self.assertRegex(h, r'All reports</a><span aria-hidden="true" style="opacity:.55">&middot;</span><a class="sr-portal" href="/portal/"')
        self.assertNotIn("sr-portal", rw.back_link(page, "vm/latest.html", "index.html"))
        self.assertNotIn("sr-portal", rw.back_link(page, "vm/latest.html", "index.html", None, "javascript:x"))

    def test_5_the_role_vars(self):
        """vars/main.yml: _web_portal_link (report_portal_link auto / an address / "") and _web_launch (AWX's awx_meta_vars)."""
        import jinja2
        import yaml
        with open(os.path.join(REPO, "roles", "site_email", "vars", "main.yml")) as f:
            v = yaml.safe_load(f)
        env = jinja2.Environment()

        def r(key, **ctx):
            return env.from_string(v[key]).render(**ctx).strip()
        self.assertEqual(r("_web_portal_link"), "")
        self.assertEqual(r("_web_portal_link", report_portal_url="https://p.example.mil/portal"), "https://p.example.mil/portal")
        self.assertEqual(r("_web_portal_link", portal_path="/portal/"), "/portal/")
        self.assertEqual(r("_web_portal_link", report_portal_link="", report_portal_url="https://p.example.mil/portal"), "")
        self.assertEqual(r("_web_portal_link", report_portal_link="/site/"), "/site/")
        self.assertEqual(r("_web_launch"), "")
        self.assertEqual(r("_web_launch", awx_job_launch_type="manual"), "manual")
        self.assertEqual(r("_web_launch", awx_job_launch_type="scheduled", awx_schedule_id=4), "scheduled")
        self.assertEqual(r("_web_launch", awx_job_launch_type="workflow", awx_workflow_job_launch_type="manual"), "workflow")
        self.assertEqual(r("_web_launch", awx_job_launch_type="workflow", awx_workflow_job_launch_type="scheduled",
                           awx_parent_job_schedule_id=9), "scheduled")
        self.assertEqual(r("_web_launch", tower_job_launch_type="scheduled"), "scheduled")
        for empty in (None, "", "0", 0, True, "1.5", "1e2", -3):                            # a placeholder is no schedule (review)
            self.assertEqual(r("_web_launch", awx_job_launch_type="manual", awx_schedule_id=empty), "manual", empty)
        self.assertEqual(r("_web_launch", awx_job_launch_type="manual", awx_schedule_id=12), "scheduled")
        self.assertEqual(r("_web_portal_link", report_portal_url=None, portal_path="/portal/"), "/portal/")


class Back1872(unittest.TestCase):
    """1.87.2: every page on the website has "All reports" (to the index, relative to the page), put in at publish time - never in the email;
    a link inside the page and the insights' period and view replace the address (no pushState), so Back leaves the report in one click."""
    LOOKS = ("report.html.j2", "report_portal.html.j2", "report_command.html.j2", "report_glass.html.j2", "report_atlas.html.j2", "report_brief.html.j2", "report_ops.html.j2")

    def test_a_the_link_is_relative_to_the_page(self):
        p = "<html><head></head><body><div class=\"appbar-in\"><!--site-report-back--><div class=\"brand\">S</div></div></body></html>"
        for rel, index, href in (("vm/2026-10-01_0600_job-1.html", "index.html", "../index.html"), ("vm/latest.html", "index.html", "../index.html"),
                                 ("HQ-East/vm/2026/w-1.html", "index.html", "../../../index.html"), ("reports/vm/x.html", "reports/index.html", "../index.html"),
                                 ("top.html", "index.html", "index.html"), ("a b/p.html", "index.html", "../index.html")):
            h = rw.back_link(p, rel, index)
            self.assertIn('<div class="appbar-in"><a class="sr-back" href="%s" style="' % href, h, rel)
            self.assertIn('<html data-site-index="%s">' % href, h, rel)
            self.assertIn("All reports</a><div class=\"brand\">", h)
            self.assertNotIn("site-report-back", h)
        self.assertEqual(rw.back_link(p, "vm/x.html", ""), p)                                       # no index: no link, no attribute (decided here, not guessed)
        self.assertEqual(rw.back_link(p, "index.html", "index.html"), p)                             # the index itself
        w = rw.back_link("<html><body><table><tr><td><!--site-report-back w=900--><table></table></td></tr></table></body></html>", "vm/x.html", "index.html")
        self.assertIn('<td><div class="sr-backbar" style="max-width:900px;margin:0 auto 10px;text-align:left;color:#1f2937;', w)
        f = rw.back_link("<html><body><table><tr><td><!--site-report-back w=full--><table></table></td></tr></table></body></html>", "vm/x.html", "index.html")
        self.assertIn('<td><div class="sr-backbar" style="margin:0 auto 10px;text-align:left;', f)                # glass, portal: as wide as the card's shell
        banner = {"on": True, "text": "UNCLASSIFIED", "bg": "#007a33", "fg": "#ffffff"}
        b = rw.banner_inject("<html><body><p>x</p></body></html>", banner)
        n = rw.back_link(b, "vm/x.html", "index.html", banner)                                       # no marker: a line at the top, under the banner
        self.assertLess(n.index("UNCLASSIFIED"), n.index("sr-backbar"))
        self.assertLess(n.index("sr-backbar"), n.index("<p>x</p>"))
        self.assertIn('href="../index.html"', rw.back_link("<html><body><p>x</p></body></html>", "vm/x.html", "index.html"))
        self.assertIn('href="../a%20%22b%22.html"', rw.back_link(p, "vm/x.html", 'a "b".html'))      # quoted in the address, escaped in the attribute

    def test_b_published_pages_have_it(self):
        d = tempfile.mkdtemp()
        try:
            html = "<html><head></head><body><div class=\"appbar-in\"><!--site-report-back--></div><p>report</p></body></html>"
            out = run_module(web_args(d, html=html, pages=[{"page": "vm/more/part.html", "html": html}]))
            self.assertFalse(out.get("failed"), out)
            for rel, href in (("vm/2026-10-01_0600_job-1.html", "../index.html"), ("vm/latest.html", "../index.html"), ("vm/more/part.html", "../../index.html")):
                with open(os.path.join(d, rel)) as fh:
                    h = fh.read()
                self.assertIn('<a class="sr-back" href="%s"' % href, h, rel)
                self.assertIn('data-site-index="%s"' % href, h, rel)
            with open(os.path.join(d, "index.html")) as fh:
                self.assertNotIn("sr-back", fh.read())
            e = tempfile.mkdtemp()
            out = run_module(web_args(e, html=html, files={"page": "vm/p.html", "latest": "", "index": ""}))        # no index: nothing to go back to
            with open(os.path.join(e, "vm", "p.html")) as fh:
                h = fh.read()
            self.assertNotIn("sr-back", h)
            self.assertNotIn("data-site-index", h)
            shutil.rmtree(e, ignore_errors=True)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_f_pages_without_their_own_index(self):
        """Codex P1: the ticket insights' site, unit and team pages are written without the index (files.index ''): they link to
        the site's (site_index); with neither, no link."""
        d = tempfile.mkdtemp()
        try:
            html = "<html><head></head><body><div class=\"appbar-in\"><!--site-report-back--></div><p>team</p></body></html>"
            out = run_module(dict(web_args(d, html=html, files={"page": "", "latest": "", "index": ""}, site_index="index.html",
                                           pages=[{"page": "insights/team/a.html", "html": html}])))
            self.assertFalse(out.get("failed"), out)
            with open(os.path.join(d, "insights", "team", "a.html")) as fh:
                self.assertIn('<a class="sr-back" href="../../index.html"', fh.read())
            self.assertFalse(os.path.exists(os.path.join(d, "index.html")))                       # the index is not written by this call
            e = tempfile.mkdtemp()
            run_module(dict(web_args(e, html=html, files={"page": "", "latest": "", "index": ""}, pages=[{"page": "t/a.html", "html": html}])))
            with open(os.path.join(e, "t", "a.html")) as fh:
                self.assertNotIn("sr-back", fh.read())
            shutil.rmtree(e, ignore_errors=True)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        with open(os.path.join(REPO, "roles", "site_email", "tasks", "pages.yml")) as fh:
            self.assertIn("site_index: \"{{ _web_site_index | default('') }}\"", fh.read())
        with open(os.path.join(REPO, "roles", "servicenow_insights", "tasks", "main.yml")) as fh:
            self.assertIn("report_web_site_index: \"{{ _sni_site_index | default('') }}\"", fh.read())

    def env(self):
        import jinja2
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(REPO, "roles", "site_email", "templates")), autoescape=False,
                                 extensions=["jinja2.ext.loopcontrols", "jinja2.ext.do"])
        for k in dir(sf):
            f = getattr(sf, k)
            if callable(f) and not k.startswith("_"):
                env.filters[k] = f
        import report_dash_filters as rd
        env.filters.update(dict((k, getattr(rd, k)) for k in dir(rd) if k.startswith("report_")))
        try:
            import ansible.plugins.filter.core as fc                                                                 # to_json, regex_replace ... (Ansible's own)
            env.filters.update(dict((k, f) for k, f in fc.FilterModule().filters().items() if k not in env.filters))
        except ImportError:
            pass
        return env

    def test_c_every_look_marks_its_top_for_the_web_only(self):
        env = self.env()
        doc = {"title": "T", "subtitle": "", "status": "warning", "summary": [{"label": "Hosts", "value": 3, "status": "warning"}],
               "sections": [{"title": "A", "lines": ["x"]}, {"title": "B", "lines": ["y"]}, {"title": "C", "lines": ["z"]}]}
        base = dict(_report=doc, _email_footer="AAP job 7 (T), started by x, 2026-11-21 06:00 UTC.", report_email_header_text="Site automation report",
                    awx_job_id=7, awx_job_template_name="T")
        for look in self.LOOKS:
            web = env.get_template(look).render(dict(base, _page_runs=[], _page_logo_cid="", _page_logo_size=[0, 0]))
            self.assertEqual(len(rw.BACK_RE.findall(web)), 1, look)
            if look in ("report_atlas.html.j2", "report_brief.html.j2", "report_ops.html.j2"):
                self.assertIn('<div class="appbar"><div class="appbar-in"><!--site-report-back-->', web, look)       # first in the top bar, before the title
                self.assertIn("tr.is-target>td{", web, look)                                                      # one highlight: the click's and the address's
                self.assertNotIn("tr:target>", web, look)                                                         # no CSS :target highlight
                continue
            self.assertRegex(web, r"<!--site-report-back w=(1000|full)-->", look)
            mail = env.get_template(look).render(base)
            self.assertNotIn("site-report-back", mail, look)                                                         # the email: as it was
            self.assertNotIn("All reports", mail, look)

    def test_d_insights_pages_have_it(self):
        import servicenow_insights_page as ip
        with open(os.path.join(REPO, "plugins", "filter", "servicenow_insights_page.py")) as fh:
            src = fh.read()
        self.assertEqual(src.count('<header class="bar gut"><div class="wrap"><!--site-report-back--><div class="brand">'), 2)       # the report and the breach page
        self.assertIn("history.replaceState(history.state,'',url)", ip.SCRIPT)

    def test_e_no_step_in_the_history(self):
        files = [os.path.join(REPO, "roles", "site_email", "templates", "site_report.js"), os.path.join(REPO, "plugins", "filter", "servicenow_insights_page.py")]
        files += [os.path.join(REPO, "roles", "netmap_report", "files", "web", f) for f in os.listdir(os.path.join(REPO, "roles", "netmap_report", "files", "web")) if f.endswith(".js")]
        for f in files:
            with open(f) as fh:
                t = fh.read()
            self.assertNotIn("pushState", t, f)                                                                   # tabs, filters, Columns, search, the period: never a step
            self.assertNotRegex(t, r"location\.hash\s*=(?!=)", f)                                                 # nor a plain hash write (a step too)
        with open(files[0]) as fh:
            js = fh.read()
        h = js[js.index("1.87.2: a link to a place on this page"):]
        self.assertIn("var a=e.target.closest&&e.target.closest('a[href^=\"#\"]')", h)
        self.assertIn("e.preventDefault();", h)
        self.assertIn("history.replaceState(history.state,'',url)", h)
        self.assertIn("for(var d=t;d&&d!==document;d=d.parentNode)if(d.tagName==='DETAILS'&&!d.open)d.open=true;", js)   # the place itself opens too (Codex)
        self.assertIn("f.focus({preventScroll:true})", h)                                                         # the keyboard goes on from there
        self.assertIn("t0.classList.add('is-target')", h)                                                         # a row the address names: the same mark
        self.assertIn("document.addEventListener('click',function(e){if(e.button||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;", js)   # the lift: not for a new tab
        self.assertIn("box.addEventListener('click',function(e){if(e.button||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;var a=e.target.closest&&e.target.closest('a[data-drill]')", js)
        self.assertIn("var b=still()?'instant':'auto';", h)                                                       # the page's scroll-behavior; at once when still
        self.assertIn("@media (prefers-reduced-motion:no-preference){html{scroll-behavior:smooth}}", rw.WEB_STYLE)       # the email looks on the web: smooth too
        self.assertIn("if(e.defaultPrevented||e.button||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;", h)  # a drill link's own handler, a new tab: theirs
        self.assertIn("if(id&&!t)return;", h)                                                                     # a name not on the page: the browser's
        self.assertIn("var a=e.target.closest&&e.target.closest('a[href^=\"#\"]');if(a)try{liftFor(", js)   # the row past the cap: lifted first (capture)
        import servicenow_insights_page as ip
        self.assertIn('try{history.replaceState(null,"",u)}catch(e){if(!win)location.replace("#p="', ip.SCRIPT)   # the period stays in the address, no step
        self.assertIn('hs.push("tab="+encodeURIComponent(id));', ip.SCRIPT)                                     # the team tab too (Codex), restored on load
        self.assertIn("m=/[#&]tab=([^&]*)/.exec(hash)", ip.SCRIPT)                                      # restored on load and on hashchange (1.106.0)


class Outlook1872(unittest.TestCase):
    """1.87.2: classic Outlook (the Word engine) mis-measures a variable font and negative em letter-spacing - the title and the status
    box came out with overlapping and gapped letters. No email asks for "Segoe UI Variable" before "Segoe UI"; the web looks tell Outlook
    (and only it) to keep normal letter-spacing."""

    def test_every_look_email(self):
        env = Back1872().env()
        doc = {"title": "fapolicyd: 2 hosts need action", "subtitle": "", "status": "critical", "summary": [{"label": "Hosts", "value": 9, "status": "critical"}],
               "sections": [{"title": "A", "lines": ["x"]}, {"title": "B", "lines": ["y"]}, {"title": "C", "lines": ["z"]}]}
        base = dict(_report=doc, _email_footer="AAP job 7 (T), started by x, 2026-11-21 06:00 UTC.", report_email_header_text="Site automation report",
                    awx_job_id=7, awx_job_template_name="T")
        for look in Back1872.LOOKS:
            h = env.get_template(look).render(base)
            v, s_ = h.find("Segoe UI Variable"), h.find('Segoe UI"') if '"Segoe UI"' in h else h.find("Segoe UI")
            self.assertTrue(v < 0 or (0 <= s_ < v), look)                                               # never a variable font first
            self.assertNotIn("Segoe UI Variable", h, look)
            self.assertNotRegex(h, r'style="[^"]*letter-spacing:\s*-[0-9.]+em', look)                   # no negative em spacing inline
            if look in ("report_atlas.html.j2", "report_brief.html.j2", "report_ops.html.j2"):
                self.assertIn("<!--[if mso]><style>*{letter-spacing:normal!important}</style><![endif]-->", h, look)
        import servicenow_insights_page as ip
        with open(ip.__file__) as fh:
            src = fh.read()
        self.assertNotIn("Segoe UI Variable", src)
        self.assertEqual(src.count("<!--[if mso]><style>*{letter-spacing:normal!important}</style><![endif]--></head>"), 2)



class ReportSpeed(unittest.TestCase):
    def page(self, look, count):
        rows = [['', 'item-%04d' % i, 'Placeholder words'] for i in range(count)]
        details = [{'columns': ['Host'], 'kinds': ['wrap'], 'rows': [['example-%04d' % i]],
                    'text': 'detail-%04d </script><b>text</b>' % i} for i in range(count)]
        section = dict(title='Findings', columns=['Open', 'Item', 'Description'], kinds=['x', 'mid', 'wrap'],
                       rows=rows, details=details, row_tags=[{'id': 'item-%04d' % i, 'data': {'tip': 'Example tip %d' % i}} for i in range(count)])
        doc = dict(title='Example scorecard', status='ok', sections=[section])
        return self.publish(look, doc)

    def publish(self, look, doc):
        raw = Back1872().env().get_template('report_%s.html.j2' % look).render(
            _report=doc, _page_runs=[], _page_logo_cid='', _email_footer='Example', report_email_header_text='Example')
        js = rw.page_script(raw)
        return rw.deferred_page(raw, 'page.html', js, rw.SCRIPT), js

    def test_first_hundred_and_dom_budget(self):
        from html.parser import HTMLParser

        class Elements(HTMLParser):
            def __init__(self):
                super().__init__()
                self.tags = []
                self.text_nodes = 0

            def handle_data(self, data):
                self.text_nodes += 1

            def handle_starttag(self, tag, attrs):
                self.tags.append((tag, dict(attrs)))

        for look in ('atlas', 'brief', 'ops'):
            page, js = self.page(look, 2000)
            dom = Elements()
            dom.feed(page)
            self.assertEqual([a['id'] for tag, a in dom.tags if tag == 'tr' and 'id' in a],
                             ['item-%04d' % i for i in range(100)], look)
            self.assertLess(len(dom.tags) + dom.text_nodes, 3000, look)
            self.assertFalse(any(tag == 'table' and a.get('class') == 'mini' for tag, a in dom.tags), look)
            inline = [a.get('id') for tag, a in dom.tags if tag == 'script' and not a.get('src') and a.get('type') != 'application/json']
            self.assertEqual(inline, ['site-report-js'] if look == 'ops' else [None, 'site-report-js'], look)
            self.assertIn('@media print{.capbar{display:none}tr[data-cap][hidden]{display:table-row!important}', page, look)
            self.assertEqual(rw.page_script(page), js, look)
            block, = [a for tag, a in dom.tags if a.get('class') == 'defer-main']
            self.assertEqual(block['data-count'], '2000')
            self.assertEqual(json.loads(block['data-ids']), ['item-%04d' % i for i in range(100, 2000)])

    def test_publish_verifies_decoder_before_deferring_rows(self):
        rows = ['<tr><td>Example %d</td></tr>' % i for i in range(105)]
        args_html = SiteScript.PAGE.replace('<body>', '<body><table>' + ''.join(rows[:100]) +
                                            '<!--defer-main 105-->' + ''.join(rows[100:]) + '<!--/defer-main--></table>')
        source = rw.page_script(args_html)
        asset = rw.report_script_file(source)
        for failure in ('locked', 'missing after write', ''):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as dest:
                patch = """write = m.Target.write
def write_asset(self, rel, text, ctype):
    if rel == %r:
        if %r == 'locked':
            raise m.Fail('locked')
        if %r:
            return
    return write(self, rel, text, ctype)
m.Target.write = write_asset""" % (asset, failure, failure)
                args = web_args(dest, html=args_html, pages=[dict(page='extra.html', html=args_html)])
                out = run_module(args, patch)
                self.assertTrue(out.get('published'), out)
                for rel in [args['files']['page'], args['files']['latest'], 'extra.html']:
                    with open(os.path.join(dest, rel)) as f:
                        html = f.read()
                    if failure:
                        self.assertEqual(html.count('<tr>'), 105)
                        self.assertIn(rows[-1], html)
                        self.assertNotIn('class="defer-main"', html)
                        self.assertNotIn(asset, html)
                    else:
                        self.assertLess(out['written'].index(asset), out['written'].index(rel))
                        self.assertIn(asset, html)
                        self.assertEqual(html.count('<tr>'), 100)

    def test_passing_script_failure_retries_the_publish(self):
        """An HTTP 503 (a failure that passes) on the versioned script is tried again from the start, the retry said -
        never a silent page with every row inline; a real error (HTTP 403) still keeps every row inline, no retry."""
        rows = ['<tr><td>Example %d</td></tr>' % i for i in range(105)]
        args_html = SiteScript.PAGE.replace('<body>', '<body><table>' + ''.join(rows[:100]) +
                                            '<!--defer-main 105-->' + ''.join(rows[100:]) + '<!--/defer-main--></table>')
        asset = rw.report_script_file(rw.page_script(args_html))
        for why, retried, deferred in (('HTTP 503 Service Unavailable', ['http 503'], True), ('HTTP 403 Forbidden', [], False)):
            with self.subTest(why=why), tempfile.TemporaryDirectory() as dest:
                patch = """write = m.Target.write
left = [1]
def write_asset(self, rel, text, ctype):
    if rel == %r and left[0]:
        left[0] -= 1
        raise m.Fail('uploading https://web/r/' + rel + ': %s')
    return write(self, rel, text, ctype)
m.Target.write = write_asset""" % (asset, why)
                out = run_module(web_args(dest, html=args_html, retries=3, retry_delay=0), patch)
                self.assertTrue(out.get('published'), out)
                self.assertEqual(out.get('retried'), retried, out)
                with open(os.path.join(dest, web_args(dest)['files']['page'])) as f:
                    html = f.read()
                self.assertEqual('class="defer-main"' in html, deferred)
                self.assertEqual(asset in html, deferred)
                self.assertEqual(html.count('<tr>'), 100 if deferred else 105)

    def test_retained_page_keeps_its_decoder_after_upgrade(self):
        with tempfile.TemporaryDirectory() as dest:
            for version in (1, 2):
                html = SiteScript.PAGE.replace('siteReport=1', 'siteReport=%d' % version)
                args = web_args(dest, page='example/run-%d.html' % version, html=html)
                self.assertTrue(run_module(args).get('published'))
            for version in (1, 2):
                source = rw.page_script(SiteScript.PAGE.replace('siteReport=1', 'siteReport=%d' % version))
                name = rw.report_script_file(source)
                with open(os.path.join(dest, 'example/run-%d.html' % version)) as f:
                    self.assertIn('../' + name, f.read())
                with open(os.path.join(dest, name)) as f:
                    self.assertEqual(f.read(), source)

    @unittest.skipUnless(PWSH, "no pwsh")
    def test_windows_verifies_script_on_destination(self):
        source = rw.page_script(SiteScript.PAGE)
        asset = rw.report_script_file(source)
        tail = '<tr><td>Example tail</td></tr>'
        html = SiteScript.PAGE.replace('<body>', '<body><table><!--defer-main 101-->' + tail + '<!--/defer-main--></table>')
        with tempfile.TemporaryDirectory() as dest:
            args = web_args(dest, html=html, theme='ops', windows_host='example')
            out = run_module(args)
            self.assertTrue(out.get('published'), out)
            for verified in (True, False):
                if not verified:
                    with open(os.path.join(dest, asset), 'w') as f:
                        f.write('different content')
                result = clm(out['script'])['write']
                self.assertTrue(result['ok'], result)
                self.assertEqual(bool(result['busy']), not verified)
                for rel in (args['files']['page'], args['files']['latest']):
                    with open(os.path.join(dest, rel)) as f:
                        page = f.read()
                    self.assertEqual(asset in page, verified)
                    self.assertEqual(tail in page, not verified)

    def test_index_keeps_history_but_draws_five_runs(self):
        runs = [_run(i, '2026-10-%02d 06:00 UTC' % i) for i in range(8, 0, -1)]
        with tempfile.TemporaryDirectory() as folder:
            for look in ('atlas', 'brief', 'ops', 'portal'):
                with self.subTest(look=look):
                    with open(os.path.join(folder, 'index.json'), 'w') as f:
                        json.dump({'reports': [dict(runs[0], key='example/latest.html', runs=runs)]}, f)
                    out = run_module(web_args(folder, theme=look, prune=False))
                    self.assertFalse(out.get('failed'), out)
                    with open(os.path.join(folder, 'index.html')) as f:
                        page = f.read()
                    older = re.search(r'<details class="older-runs"><summary>Show 3 older runs</summary>(.*?)</details>', page, re.S)[1]
                    for i, run in enumerate(runs):
                        href = 'href="%s"' % run['page']
                        self.assertIn(href, page)
                        self.assertEqual(href in older, i >= 5)
                    self.assertIn('st-i st-ok' if look != 'portal' else rw._pill('ok', True), older)
                    if look != 'portal':
                        self.assertTrue(os.path.isfile(os.path.join(folder, rw.INDEX_SCRIPT_FILE)))
                    with open(os.path.join(folder, 'index.json')) as f:
                        self.assertEqual(next(r for r in json.load(f)['reports'] if r['key'] == 'example/latest.html')['runs'], runs)

    def check_browser_page(self, folder, page, probe, expected):
        with open(os.path.join(folder, 'probe.js'), 'w') as f:
            f.write("document.addEventListener('DOMContentLoaded',function(){" + probe + "})")
        with open(os.path.join(folder, 'page.html'), 'w') as f:
            f.write(page.replace('</body>', '<output></output><script src="probe.js"></script></body>'))
        result = subprocess.run([shutil.which('google-chrome') or shutil.which('chromium'), '--headless', '--no-sandbox',
                                 '--disable-gpu', '--allow-file-access-from-files', '--user-data-dir=' + folder + '/profile',
                                 '--dump-dom', '--virtual-time-budget=3000', 'file://' + folder + '/page.html'],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('<output>' + expected + '</output>', result.stdout)

    @unittest.skipUnless(shutil.which('google-chrome') or shutil.which('chromium'), 'no Chrome or Chromium')
    def test_standalone_rows_and_details(self):
        page, js = self.page('ops', 105)
        probe = """var buttons=document.querySelectorAll('.capbar button');
          var initial=document.querySelector('.shown').textContent;
          buttons[0].click();
          var rows=Array.from(document.querySelectorAll('tr[id^=item-]'));
          rows[104].querySelector('.xb').click();
          document.querySelector('output').textContent=[buttons.length,initial,rows.every((r,i)=>
            !r.hidden && r.id==='item-'+String(i).padStart(4,'0')),rows.length,rows[104].nextElementSibling.querySelector('p').textContent].join('|');"""
        with tempfile.TemporaryDirectory() as folder:
            for external in (False, True):
                with self.subTest(external=external):
                    if external:
                        with open(os.path.join(folder, rw.report_script_file(js)), 'w') as f:
                            f.write(js)
                    self.check_browser_page(folder, page, probe, '1|100 of 105|true|105|detail-0104 &lt;/script&gt;&lt;b&gt;text&lt;/b&gt;')

    @unittest.skipUnless(shutil.which('google-chrome') or shutil.which('chromium'), 'no Chrome or Chromium')
    def test_saved_outage_filter_counts_all_events_on_load(self):
        tags = [dict(data=dict(cls='example', rel=int(i >= 100))) for i in range(150)]
        tags[-1]['data']['rep'] = '1'
        section = dict(title='The events', columns=['What happened'], rows=[['Example event %d' % i] for i in range(150)],
                       row_tags=tags, outage=dict(subject='Example', lanes=[dict(name='Example')]))
        page, _ = self.publish('ops', dict(title='Example outage review', status='ok', sections=[section]))
        probe = """document.querySelector('output').textContent=[document.querySelectorAll('tr[data-cls]').length,
          document.querySelectorAll('tr[data-cls]:not([hidden])').length,document.querySelector('.shown').textContent].join('|');"""
        with tempfile.TemporaryDirectory() as folder:
            for saved, visible in [({}, 149), ({'show': 'rel', 'rep': True}, 50)]:
                with self.subTest(saved=saved):
                    seed = "<script>localStorage.setItem('site-report-outage',JSON.stringify(%s))</script>" % json.dumps(saved)
                    self.check_browser_page(folder, page.replace('<head>', '<head>' + seed), probe,
                                            '150|%d|%d of 150' % (visible, visible))

    @unittest.skipUnless(shutil.which('google-chrome') or shutil.which('chromium'), 'no Chrome or Chromium')
    def test_browser_show_all_search_filter_export_and_link(self):
        page, js = self.page('ops', 105)
        # Each probe starts on a fresh page and uses the published external script under the portal's script policy.
        prelude = """var tb=document.querySelector('.tw table'), rows=()=>Array.from(tb.tBodies[0].rows).filter(r=>!r.hidden&&!r.classList.contains('xr')),
                       more=document.querySelector('.capbar button'), answer=document.querySelector('output');"""
        probes = [
            ("var n=rows().length;var label=more.textContent;more.click();answer.textContent=[n,label,rows().length].join('|')",
             '100|Show all 105 rows|105'),
            ("window.dispatchEvent(new Event('beforeprint'));var printed=rows().length;"
             "window.dispatchEvent(new Event('afterprint'));var restored=rows().length;"
             "var f=document.querySelector('.filter');f.value='item-0104';f.dispatchEvent(new Event('input'));"
             "window.dispatchEvent(new Event('beforeprint'));answer.textContent=[printed,restored,rows().length,rows()[0].id].join('|')",
             '105|100|1|item-0104'),
            ("var first=rows()[0]._at;more.click();var r=rows()[104];"
             "r.dispatchEvent(new PointerEvent('pointerenter',{pointerType:'mouse'}));var tip=document.querySelector('#site-tip');"
             "answer.textContent=[first===rows()[0]._at,r.tabIndex,tip.hidden,tip.textContent].join('|')",
             'true|0|false|Example tip 104'),
            ("var f=document.querySelector('#find');f.value='detail-0104';f.dispatchEvent(new Event('search'));"
             "var r=rows()[0];r.querySelector('.xb').click();answer.textContent=[rows().length,r.id,r.nextElementSibling.querySelector('p').textContent].join('|')",
             '1|item-0104|detail-0104 &lt;/script&gt;&lt;b&gt;text&lt;/b&gt;'),
            ("var f=document.querySelector('.filter');f.value='item-0104';f.dispatchEvent(new Event('input'));answer.textContent=rows()[0].id", 'item-0104'),
            ("var a=document.createElement('a');a.href='#item-0104';document.body.append(a);var n=history.length;a.click();"
             "answer.textContent=[history.length===n,document.querySelector(a.hash).hidden].join('|')", 'true|false'),
            ("var c=document.querySelector('details.cols .c3');c.click();navigator.msSaveOrOpenBlob=async function(b){var csv=await b.text();"
             "answer.textContent=[csv.trim().split('\\r\\n').length,csv.includes('item-0104'),csv.includes('Placeholder words')].join('|')};"
             "document.querySelector('.csv').click()", '106|true|false'),
        ]
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, rw.report_script_file(js)), 'w') as f:
                f.write(js)
            page = page.replace('<head>', """<head><meta http-equiv="Content-Security-Policy" content="script-src 'self'">""")
            for probe, expected in probes:
                with self.subTest(expected=expected):
                    self.check_browser_page(folder, page, prelude + probe, expected)



class WideTables(unittest.TestCase):
    """1.79.2: the wide hardware and capacity tables start with their least-used columns behind Columns."""
    COLS = ["Cluster", "Controller", "Model", "Serial", "System ID", "ONTAP", "HA partner", "CPU", "Memory", "System firmware", "SP / BMC",
            "NVRAM battery", "Flash Cache", "Ports", "Up", "State"]

    def test_a_wide_table_of_a_known_title(self):
        sec = {"title": "Controllers", "columns": self.COLS}
        self.assertEqual(sf.report_hidden_columns(sec), ["System ID", "HA partner", "SP / BMC", "NVRAM battery", "Flash Cache", "System firmware"])

    def test_b_a_table_that_names_its_own_wins_and_others_are_left(self):
        self.assertEqual(sf.report_hidden_columns({"title": "Controllers", "columns": self.COLS, "hidden_columns": ["Ports"]}), ["Ports"])
        self.assertEqual(sf.report_hidden_columns({"title": "Controllers", "columns": ["Cluster", "Controller", "State"]}), [])          # a short table
        self.assertEqual(sf.report_hidden_columns({"title": "Controllers", "columns": [c for c in self.COLS if c != "CPU"][:8]}), [])
        self.assertEqual(sf.report_hidden_columns({"title": "Hosts", "columns": self.COLS}), [])                                          # other columns than the ones named
        self.assertEqual(sf.report_hidden_columns(None), [])

    def test_c_the_page_unchecks_them_and_keeps_the_rows(self):
        import jinja2
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(os.path.dirname(HERE), "roles", "site_email", "templates")), autoescape=False)
        env.filters.update(report_hidden_columns=sf.report_hidden_columns, report_tidy=sf.report_tidy, report_head=sf.report_head, report_sparkline=sf.report_sparkline, report_runway_bar=sf.report_runway_bar,
                           safe_link=sf.safe_link, safe_ref=sf.safe_ref, safe_site_url=sf.safe_site_url, safe_data=sf.safe_data)
        doc = {"title": "T", "subtitle": "", "status": "ok", "sections": [{"title": "Controllers", "columns": self.COLS, "rows": [["x"] * len(self.COLS)]}]}
        h = env.get_template("report_atlas.html.j2").render(_report=doc, _email_footer="x", _page_logo_cid="", _page_runs=[], report_email_header_text="S")
        self.assertIn('<input type="checkbox" class="c5">System ID', h)                                                                  # unchecked: no " checked"
        self.assertIn('<input type="checkbox" class="c1" checked>Cluster', h)


class Files1181(unittest.TestCase):
    """1.118.1: the run's Files line under every page's header - every look here, the ticket insights' pages in
    tests/test_servicenow_insights.py and tests/test_servicenow_breach.py - and each file said in plain words (the report's
    own description, else its type's)."""
    WEB = ("report_atlas.html.j2", "report_brief.html.j2", "report_ops.html.j2")

    def test_1_every_look_puts_it_under_its_header(self):
        rf = sys.modules["ansible.module_utils.site_report_files"]
        env = Back1872().env()
        doc = {"title": "Weekly title", "subtitle": "", "status": "warning", "summary": [{"label": "Hosts", "value": 3, "status": "warning"}],
               "sections": [{"title": "A", "lines": ["x"]}]}
        base = dict(_report=doc, _email_footer="AAP job 7 (T).", report_email_header_text="Site automation report", awx_job_id=7, awx_job_template_name="T")
        files = [{"name": "t.xlsx", "size": 3879731, "desc": "Excel workbook - every incident of the period"}, {"name": "aar.docx", "size": 2048}]
        for look in Back1872.LOOKS:
            web = env.get_template(look).render(dict(base, _page_runs=[], _page_logo_cid="", _page_logo_size=[0, 0]))
            self.assertEqual(web.count(rf.MARK) + len(rf.ROW_RE.findall(web)), 1, look)
            h = rf.put_files(web, files, [], "t/2026-11-23_0600_job-7.html", "t/2026-11-23_0600_job-7-files", look in self.WEB)
            at = h.index(rf.START)
            self.assertNotRegex(h[:at], r"<body[^>]*>\s*$", look)                       # never on top of the page (its bar)
            self.assertGreater(at, h.index("Weekly title", h.index("<body")), look)         # under the header's title
            if look not in self.WEB:
                self.assertRegex(h[:at], r'<tr><td style="[^"]*">$', look)                 # a row of the email look's card
                self.assertNotIn('style="max-width:760px;margin:12px auto', h, look)        # the row's own width
            self.assertIn('<span class="sf-z">3.7 MB</span> <span class="sf-d">&middot; Excel workbook - every incident of the period</span>', h, look)
            self.assertIn('<span class="sf-d">&middot; Word document</span>', h, look)               # no description given: its type's
            if look not in self.WEB:                                                          # an email look's email: no mark
                self.assertNotIn("site-report-files", env.get_template(look).render(base), look)
            if look in self.WEB:
                self.assertIn(".sf-d{", web, look)

    def test_2_descriptions(self):
        rf = sys.modules["ansible.module_utils.site_report_files"]
        self.assertEqual(rf.describe(None, ".xlsx"), "Excel workbook - the tables of this page, one sheet each")
        self.assertEqual(rf.describe("  Word document -\n the AAR draft ", ".docx"), "Word document - the AAR draft")
        self.assertEqual(len(rf.describe("x" * 400, ".csv")), 300)
        self.assertEqual(rf.describe("", ".zip"), "ZIP file")                              # a type with no words of its own
        self.assertEqual(rf.describe("", ""), "")
        f = [{"name": "a.xlsx", "size": 10}]
        own = rf.put_files("<body><header>H</header><main>%s</main></body>" % rf.MARK, f, [], "p/x.html", "p/x-files", False)
        self.assertIn('<section class="card sfiles"', own)        # a page with its own Files style (the insights) - whatever report_web_theme is
        row = rf.put_files('<body><table><tr><td>H</td></tr><!--site-report-files row="padding:0"--></table></body>', f, [], "p/x.html", "p/x-files", True)
        self.assertIn('<tr><td style="padding:0"><!--site-report-files:start--><div class="sfiles" style="margin:0;', row)
        ok, _ = rf.prepare([{"filename": "a.csv", "b64": base64.b64encode(b"a").decode()},
                            {"filename": "b.csv", "b64": base64.b64encode(b"b").decode(), "desc": "CSV - the hosts"}], 5, packed_only=True)
        self.assertEqual([f["desc"] for f in ok], ["CSV - a table of this page, as text (Excel opens it)", "CSV - the hosts"])   # the copy job too
        with open(os.path.join(REPO, "roles", "site_email", "library", "site_report_web.py")) as fh:
            self.assertIn("'desc': f.get('desc') or ''}]", fh.read())                         # a kept report keeps them

    def test_3_the_reports_say_what_their_files_are(self):
        for rel, words in (("roles/servicenow_insights/tasks/main.yml", ("every incident of the period", "Automation backlog: every ticket cluster")),
                           ("roles/servicenow_insights/tasks/breach.yml", ("(sheet About to breach)",)),
                           ("roles/vmware_vm/tasks/watch.yml", ("(sheet Timeline:", "after action report (AAR) draft")),
                           ("roles/vmware_vm/tasks/report.yml", ("_report_xlsx_desc:",)),
                           ("roles/site_email/tasks/main.yml", ("'desc': _report_xlsx_desc",))):
            with open(os.path.join(REPO, rel)) as fh:
                src = " ".join(fh.read().split())
            for w in words:
                self.assertIn(w, src, rel)


rfs = sys.modules["ansible.module_utils.site_report_files"]
FAKE_WEB = os.path.join(HERE, "email", "fake_web.py")
FAKE_SMB = os.path.join(HERE, "email", "fake_smb")
XML = b'<?xml version="1.0" encoding="utf-8"?>\n<STIGComments Name="RHEL9"/>\n'
DOCX = bytes(range(256)) * 40                                             # a binary file: every byte, 10 KB


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Site(object):
    """One report website for the 1.79.8 tests, on one transport: a folder, an upload (tests/email/fake_web.py, WebDAV), a
    share (tests/email/fake_smb on the module's PYTHONPATH) or a Windows server (the script run by pwsh in Constrained
    Language Mode, the index and its gone pages read first by site_report_web_read.ps1, as the role does). root: the
    folder as the server has it."""

    def __init__(self, kind, conditional=False):
        self.kind, self.tmp = kind, tempfile.mkdtemp()
        self.root, self.env, self.proc = os.path.join(self.tmp, "site"), {}, None
        if kind == "web":
            os.makedirs(self.root)
            port = _free_port()
            self.proc = subprocess.Popen([sys.executable, FAKE_WEB, str(port), self.root],
                                         env=dict(os.environ, FAKE_WEB_CONDITIONAL="1" if conditional else ""))
            end = time.time() + 10
            while True:
                try:
                    urllib.request.urlopen("http://127.0.0.1:%d/none" % port, timeout=1)
                except urllib.error.HTTPError:
                    break
                except Exception:                      # noqa: BLE001 - not up yet
                    assert time.time() < end
                    time.sleep(0.1)
            self.dest = "http://127.0.0.1:%d" % port
        elif kind == "smb":
            self.root = os.path.join(self.tmp, "smb", "its", "reports")
            os.makedirs(self.root)
            self.env = {"PYTHONPATH": FAKE_SMB, "FAKE_SMB_ROOT": os.path.join(self.tmp, "smb"),
                        "REPORT_WEB_USERNAME": "svc_reports@EXAMPLE.MIL", "REPORT_WEB_PASSWORD": "x"}
            self.dest = "\\\\fakesrv\\its\\reports"
        else:
            os.makedirs(self.root)
            self.dest = self.root

    def run(self, args):
        args = dict(args, dest=self.dest)
        if self.kind == "win":
            read = clm(READ_PS, {"SITE_CHECK_CONFIG": json.dumps({"dest": self.root, "index": ["index.json"]})})["read"]
            args.update(windows_host="web01.example.mil", index_text=read["index"] or "", index_gone=read["gone"],
                        index_sha=read.get("sha") or "", index_pending=read.get("pending") or [])
        out = run_module(args, env=self.env)
        if self.kind == "win" and out.get("script"):
            w = clm(out["script"])["write"]
            os.remove(out["script"])
            assert w["ok"], w
            out["ps"] = w
        return out

    def path(self, rel):
        return os.path.join(self.root, *rel.split("/"))

    def text(self, rel):
        with open(self.path(rel), encoding="utf-8") as f:
            return f.read()

    def index(self):
        return json.loads(self.text("index.json"))

    def close(self):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        shutil.rmtree(self.tmp, ignore_errors=True)


def _day(day):
    """Day n of the tests' runs: ten days ago plus n (the files' age is counted from today)."""
    return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=10 - day)).strftime("%Y-%m-%d")


def _page(job, day, folder="vm"):
    return "%s/%s_0600_job-%d.html" % (folder, _day(day), job)


def _files_args(job, day, downloads, key="vm/latest.html", template="VMware - VM health report", **kw):
    page = _page(job, day, key.split("/")[0])
    entry = {"key": key, "name": template, "title": "t", "status": "ok", "when": "%s 06:00 UTC" % _day(day), "job": job}
    html = ('<html><head></head><body><header>h</header>' + rfs.MARK + '<p>report</p><div class="code"><pre>x</pre>'
            '<a class="dl" href="site-report-file:answer-keys-RHEL_9_STIG-V2R10.xml" download>Download answer-keys-RHEL_9_STIG-V2R10.xml</a>'
            '</div></body></html>')
    return dict({"files": {"page": page, "latest": key, "index": "index.html"}, "entry": entry, "html": html, "theme": "atlas",
                 "data": {"report": {"title": "t"}}, "downloads": downloads, "history": 5}, **kw)


class Files1798(unittest.TestCase):
    """1.79.8: a report's files on the report website (report_web_files; plugins/module_utils/site_report_files.py) - the
    names made safe, the types and sizes refused, the Files line and the Download links, the copy job's packing; on every
    transport (a folder, an upload, a share, a Windows server): the files beside the page, a run's files with its page, the
    age, the total (never a latest page's), an orphan's folder, check mode writing nothing."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def file(self, name, data):
        p = os.path.join(self.tmp, name)
        with open(p, "wb") as f:
            f.write(data)
        return {"path": p, "filename": name}

    # ---- the pieces
    def test_a_names_types_and_sizes(self):
        self.assertEqual(rfs.safe_file_name("../../etc/pass wd.xml"), "pass-wd.xml")
        self.assertEqual(rfs.safe_file_name("C:\\x\\Ärger (1).docx"), "rger-1.docx")
        self.assertEqual(rfs.safe_file_name("..."), "")
        self.assertEqual(len(rfs.safe_file_name("a" * 300 + ".xml")), 100)
        self.assertTrue(rfs.safe_file_name("a" * 300 + ".xml").endswith("a.xml"))
        big = self.file("big.xml", b"x" * (rfs.MB + 1))
        ok, refused = rfs.prepare([self.file("a.xml", XML), self.file("run.exe", b"MZ"), big, self.file("a.XML", XML),
                                   {"filename": "k.txt", "b64": base64.b64encode(b"kept").decode()},
                                   {"filename": "bad.txt", "b64": "!!"}, {"path": "/nonexistent/x.xml"}], 1, rfs.TYPES)
        self.assertEqual([f["name"] for f in ok], ["a.xml", "a-2.XML", "k.txt"])     # one name twice (any case): -2
        self.assertEqual(rfs.read_bytes(ok[2]), b"kept")
        why = dict((r["name"], r["why"]) for r in refused)
        self.assertIn("(.exe) is not one the website hosts (report_web_files_types)", why["run.exe"])
        self.assertIn("over report_web_files_max_mb (1 MB)", why["big.xml"])
        self.assertIn("could not be read back", why["bad.txt"])
        self.assertIn("did not make it", why["x.xml"])
        ok, refused = rfs.prepare([self.file("p.xml", b"x" * 700000), self.file("q.xml", b"x" * 700000)], 1)
        self.assertEqual(([f["name"] for f in ok], refused[0]["name"]), (["p.xml"], "q.xml"))
        self.assertIn("together are over", refused[0]["why"])
        self.assertEqual((rfs.human(812), rfs.human(14000), rfs.human(3 * rfs.MB + rfs.MB // 5)), ("812 bytes", "14 KB", "3.2 MB"))

    def test_a2_the_heads_file(self):
        """1.112.0: the head's file (the outage review's "Download AAR (Word)", report_head_file.html.j2): the run's link with
        its size and the label kept; not hosted: dashed, with why; no files on the website: said; removed later: struck."""
        head = ('<div class="hfile"><a class="hf-a" href="site-report-file:AAR-esx03.docx" download><svg></svg><span class="hf-l">'
                'Download AAR (Word)</span></a><span class="hf-m">A draft to finish.</span></div>')
        ok, _ = rfs.prepare([self.file("AAR-esx03.docx", b"PK" * 600)], 20)
        h = rfs.put_files(head, ok, [], "vm/2026-11-12_0345_job-4714.html", "vm/2026-11-12_0345_job-4714-files")
        self.assertIn('<a class="hf-a" href="2026-11-12_0345_job-4714-files/AAR-esx03.docx" download="AAR-esx03.docx"><svg></svg>'
                      '<span class="hf-l">Download AAR (Word)</span></a><span class="hf-m">AAR-esx03.docx &middot; 2 KB &middot; '
                      'A draft to finish.</span>', h)
        self.assertNotIn("site-report-file:", h)
        h2 = rfs.put_files(head, [], [{"name": "AAR-esx03.docx", "why": "23.4 MB, over report_web_files_max_mb (20 MB)"}], "p.html", "p-files")
        self.assertIn('<span class="hf-a off"><svg></svg><span class="hf-l">AAR (Word)</span></span>', h2)
        self.assertIn("AAR-esx03.docx is not on the website: 23.4 MB, over report_web_files_max_mb (20 MB).", h2)
        h3 = rfs.put_files(head, [], [], "p.html", "p-files")
        self.assertIn("Not on the website (report_web_files is off): attached to the email.", h3)
        gone = rfs.strike(h, "vm/2026-11-12_0345_job-4714-files", ["AAR-esx03.docx"], "removed after 30 days (report_web_files_keep_days)")
        self.assertIn("AAR-esx03.docx: removed after 30 days", gone)
        self.assertNotIn('class="hf-a" href', gone)

    def test_b_the_page_its_files_line_and_download_links(self):
        ok, _ = rfs.prepare([self.file("answer-keys-RHEL_9_STIG-V2R10.xml", XML)], 20)
        html = _files_args(1, 1, [])["html"]
        h = rfs.put_files(html, ok, [{"name": "run.exe", "why": "its type"}], "vm/latest.html", "vm/2026-10-01_0600_job-1-files")
        self.assertNotIn(rfs.MARK, h)
        self.assertIn('<a class="sf-a" href="2026-10-01_0600_job-1-files/answer-keys-RHEL_9_STIG-V2R10.xml" '
                      'download="answer-keys-RHEL_9_STIG-V2R10.xml">', h)
        self.assertIn('<a class="dl" href="2026-10-01_0600_job-1-files/answer-keys-RHEL_9_STIG-V2R10.xml" download="answer-keys-RHEL_9_STIG-V2R10.xml">'
                      'Download answer-keys-RHEL_9_STIG-V2R10.xml</a>', h)
        self.assertIn("run.exe is not on the website: its type", h)
        self.assertNotIn("site-report-file:", h)
        self.assertNotIn("<script", h)
        self.assertEqual(h.index("<header>") < h.index("sfiles") < h.index("<p>report"), True)   # under the header
        # in a folder of its own, the latest page elsewhere: the link goes there
        h2 = rfs.put_files(html, ok, [], "latest/vm.html", "vm/2026-10-01_0600_job-1-files")
        self.assertIn('href="../vm/2026-10-01_0600_job-1-files/answer-keys-RHEL_9_STIG-V2R10.xml"', h2)
        # an email look on the website (no spot for it): at the top of the body; no files: no line, the link gone
        h3 = rfs.put_files("<html><body><table></table></body></html>", ok, [], "a/p.html", "a/p-files", False)
        self.assertTrue(h3.startswith('<html><body><!--site-report-files:start--><div class="sfiles" style='))
        h4 = rfs.put_files(html, [], [], "vm/p.html", "vm/p-files")
        self.assertNotIn("sfiles", h4)
        self.assertNotIn("Download", h4)
        self.assertNotIn(rfs.MARK, h4)
        # removed later: each link says why, the page otherwise as it was
        s = rfs.strike(h, "vm/2026-10-01_0600_job-1-files", ["answer-keys-RHEL_9_STIG-V2R10.xml"],
                       "removed after 30 days (report_web_files_keep_days)")
        self.assertEqual(s.count("answer-keys-RHEL_9_STIG-V2R10.xml: removed after 30 days"), 2)
        self.assertNotIn('href="2026-10-01_0600_job-1-files/', s)
        self.assertIn("run.exe is not on the website", s)
        # (Codex) another run's file of the same name - a latest page that shows a newer run - is never struck
        self.assertEqual(rfs.strike(h, "vm/2026-09-30_0600_job-0-files", ["answer-keys-RHEL_9_STIG-V2R10.xml"], "x"), h)

    def test_c_the_plan(self):
        now = datetime.datetime(2026, 11, 20, 6, 0, tzinfo=datetime.timezone.utc)

        def e(page, day, size, key="k"):
            return {"dir": page[:-5] + "-files", "page": page, "key": key, "when": "2026-%s 06:00 UTC" % day, "files": [{"name": "a.xml", "size": size}]}
        old, mid, new, gone = e("a/1.html", "10-01", 10), e("a/2.html", "11-10", 400 * rfs.MB), e("a/3.html", "11-19", 50 * rfs.MB), e("b/1.html", "11-19", 5)
        rem, kept, total, room = rfs.plan([old, mid, new, gone], {"a/3.html"}, 100 * rfs.MB, now, 30, 500, lambda p: p == "b/1.html")
        self.assertEqual([(r[0]["page"], r[1]) for r in rem], [("a/1.html", "age"), ("b/1.html", "orphan"), ("a/2.html", "cap")])
        self.assertEqual(([k["page"] for k in kept], total, room), (["a/3.html"], 150 * rfs.MB, True))
        rem, kept, total, room = rfs.plan([new], {"a/3.html"}, 460 * rfs.MB, now, 0, 500, lambda p: False)
        self.assertEqual((rem, room), ([], False))                  # the latest page's files never go for the total: no room
        line = rfs.used_line([mid, new], 500, 30)
        self.assertEqual(line, "Hosted files: 450.0 MB in 2 runs, of 500 MB (report_web_files_total_max_mb); each run's kept "
                               "30 days (report_web_files_keep_days).")
        self.assertEqual(rfs.ledger({"files": [{"dir": "../x", "files": []}, {"dir": "a/1-files", "page": "a/1.html", "files": [{"name": "../e"}]},
                                               {"dir": "etc-files", "page": "a/2.html", "files": []}, {"dir": "b-files", "files": []},
                                               {"dir": "../b-files", "page": "../b.html", "files": []}]}),
                         [{"dir": "a/1-files", "page": "a/1.html", "files": []}])     # only a page's own folder, safe names
        # (Codex) two names made the same at 100 characters: the second still 100, its -2 and type kept
        ok, _ = rfs.prepare([self.file("a" * 120 + ".xml", XML), self.file("a" * 130 + ".xml", XML)], 1)
        self.assertEqual([len(f["name"]) for f in ok], [100, 100])
        self.assertTrue(ok[1]["name"].endswith("a-2.xml"))

    def test_d_the_windows_script_in_constrained_language_mode(self):
        if not PWSH:
            self.skipTest("no pwsh")
        dest = os.path.join(self.tmp, "D reports")
        os.makedirs(os.path.join(dest, "a", "p-files"))
        with open(os.path.join(dest, "a", "p-files", "old.xml"), "w") as f:
            f.write("x")
        with open(os.path.join(dest, "a", "q.html"), "w") as f:
            f.write("<p>" + rfs.block_html([{"name": "z.xml", "size": 3}], [], "a/q.html", "a/q-files") + "</p>")
        sub = rfs.ps_strike("a/q-files", ["z.xml"], "removed after 30 days ($x)")
        script = os.path.join(self.tmp, "w.ps1")
        with open(script, "wb") as f:
            f.write(rw.windows_script(dest, [("a/n-files/b.docx", DOCX), ("a/n.html", "<p>n</p>")], ["a/p-files/old.xml"], (),
                                      ["a/p-files"], [("a/q.html", sub)]).encode("utf-8-sig"))
        out = clm(script)["write"]
        self.assertTrue(out["ok"], out)
        with open(os.path.join(dest, "a", "n-files", "b.docx"), "rb") as f:
            self.assertEqual(f.read(), DOCX)                                     # every byte, exactly
        self.assertFalse(os.path.exists(os.path.join(dest, "a", "p-files")))     # its file deleted, then the folder
        with open(os.path.join(dest, "a", "q.html")) as f:
            q = f.read()
        self.assertIn("z.xml: removed after 30 days ($x)", q)
        self.assertNotIn('href="q-files/z.xml"', q)
        with self.assertRaises(rw.Fail):                                         # never a folder that is not ours
            rw.windows_script(dest, [], (), (), ["a/p"])
        with self.assertRaises(rw.Fail):
            rw.windows_script(dest, [("a/p-files/../../x.docx", b"x")])

    def test_e_kept_for_the_copy_job(self):
        files = [self.file("answer-keys-RHEL_9_STIG-V2R10.xml", XML), self.file("big.docx", os.urandom(300000))]
        out = run_module(dict(_files_args(1, 1, files), dest=self.tmp, keep=True, max_kb=64))
        doc = pg.unpack(out["packed"])
        self.assertEqual([d["filename"] for d in doc["downloads"]], ["answer-keys-RHEL_9_STIG-V2R10.xml"])
        self.assertEqual(base64.b64decode(doc["downloads"][0]["b64"]), XML)
        self.assertIn("too big to keep for the job Report website - copy (report_web_copy_max_kb)",
                      [r["why"] for r in doc["downloads_refused"] if r["name"] == "big.docx"][0])
        site = os.path.join(self.tmp, "site")
        os.mkdir(site)
        other = self.file("other.xml", XML)          # (Codex) a kept report brings content, never a path on the copy job's machine
        out = run_module(dict(_files_args(1, 1, [], kept_files=doc["downloads"] + [other], downloads_refused=doc["downloads_refused"]),
                              dest=site))        # the copy job
        self.assertIn("never a path - not hosted", [r["why"] for r in out["files_refused"] if r["name"] == "other.xml"][0])
        self.assertEqual([f["name"] for f in out["files"]], ["answer-keys-RHEL_9_STIG-V2R10.xml"])
        with open(os.path.join(site, *(_page(1, 1)[:-5] + "-files/answer-keys-RHEL_9_STIG-V2R10.xml").split("/")), "rb") as f:
            self.assertEqual(f.read(), XML)
        with open(os.path.join(site, *_page(1, 1).split("/"))) as f:
            self.assertIn("big.docx is not on the website: too big to keep", f.read())

    def test_f_no_index_no_files(self):
        out = run_module(dict(_files_args(1, 1, [self.file("a.xml", XML)], files={"page": _page(1, 1), "latest": "", "index": ""}), dest=self.tmp))
        self.assertEqual(out["files"], [])
        self.assertIn("only with an index (report_web_index)", out["files_refused"][0]["why"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, *(_page(1, 1)[:-5] + "-files").split("/"))))

    # ---- every transport
    def transport(self, kind):
        site = Site(kind)
        self.addCleanup(site.close)
        xml, docx = self.file("answer-keys-RHEL_9_STIG-V2R10.xml", XML), self.file("aar.docx", DOCX)
        exe = self.file("run.exe", b"MZ")
        f1 = _page(1, 1)[:-5] + "-files"

        def gone(rel):                     # not there - or, over WebDAV, left empty (a folder DELETE would take all in it)
            p = site.path(rel)
            return not os.path.exists(p) or (kind == "web" and os.path.isdir(p) and not os.listdir(p))
        # a dry run first: nothing written, what it would host said
        if kind != "win":                                           # (the role runs no Windows step in a dry run)
            out = run_module(dict(_files_args(1, 1, [xml]), dest=site.dest, _ansible_check_mode=True), env=site.env)
            self.assertIn("its files: answer-keys-RHEL_9_STIG-V2R10.xml", out["msg"])
            self.assertEqual(os.listdir(site.root), [])
        # run 1: the files beside the page, the page's links, the latest page's to the same folder, the JSON, the ledger
        out = site.run(_files_args(1, 1, [xml, docx, exe], history=2))
        self.assertEqual([f["name"] for f in out["files"]], ["answer-keys-RHEL_9_STIG-V2R10.xml", "aar.docx"], out)
        self.assertEqual(out["files_refused"][0]["name"], "run.exe")
        with open(site.path(f1 + "/aar.docx"), "rb") as f:
            self.assertEqual(f.read(), DOCX)
        for rel, href in ((_page(1, 1), f1[3:] + "/"), ("vm/latest.html", f1[3:] + "/")):
            h = site.text(rel)
            self.assertIn('class="dl" href="%sanswer-keys-RHEL_9_STIG-V2R10.xml" download=' % href, h)
            self.assertIn('class="sf-a" href="%saar.docx" download="aar.docx"' % href, h)
            self.assertIn("run.exe is not on the website", h)
        if kind != "win":
            self.assertEqual(json.loads(site.text(_page(1, 1)[:-5] + ".json"))["files"][1]["path"], f1 + "/aar.docx")
        led = site.index()["files"]
        self.assertEqual([(e["dir"], e["latest"], [x["name"] for x in e["files"]]) for e in led],
                         [(f1, "vm/latest.html", ["answer-keys-RHEL_9_STIG-V2R10.xml", "aar.docx"])])
        self.assertIn("Hosted files: 11 KB in 1 run, of 500 MB", site.text("index.html"))
        self.assertNotIn("aar.docx", site.text("index.html"))                     # the index never lists files
        # run 2, history 2: run 1's files stay, the latest page now links run 2's
        out = site.run(_files_args(2, 2, [xml], history=2))
        self.assertTrue(os.path.exists(site.path(f1 + "/aar.docx")))
        self.assertIn('href="%s-files/answer-keys' % _page(2, 2)[3:-5], site.text("vm/latest.html"))
        self.assertEqual([e["latest"] for e in site.index()["files"]], ["", "vm/latest.html"])
        # run 3, history 2: run 1 falls off - its page and its files go, the folder too
        out = site.run(_files_args(3, 3, [xml], history=2))
        self.assertTrue(gone(f1))
        self.assertFalse(os.path.exists(site.path(_page(1, 1))))
        self.assertTrue(any(x.startswith(f1 + " (2 files, 11 KB): removed with its page") for x in out["files_removed"]), out["files_removed"])
        # the age: run 2's files 40 days old (as the ledger has them) - removed at the next publish of any report, its page
        # stays and says so, as does the latest page when they were its (here: run 3's are the latest's)
        doc = site.index()
        for e in doc["files"]:
            if e["page"] == _page(2, 2):
                e["when"] = "%s 06:00 UTC" % _day(-40)
        with open(site.path("index.json"), "w") as f:
            json.dump(doc, f)
        out = site.run(_files_args(9, 4, [], key="ad/latest.html", template="AD - health report"))
        self.assertTrue(gone(_page(2, 2)[:-5] + "-files"))
        self.assertIn("answer-keys-RHEL_9_STIG-V2R10.xml: removed after 30 days (report_web_files_keep_days)", site.text(_page(2, 2)))
        self.assertIn('href="%s-files/answer-keys' % _page(3, 3)[3:-5], site.text("vm/latest.html"))
        self.assertTrue(any("removed after 30 days" in x for x in out["files_removed"]))
        # an orphan: run 3's page deleted by hand - its folder goes at the next publish, and the latest page that still
        # showed it says so; a folder not ours stays
        os.remove(site.path(_page(3, 3)))
        os.makedirs(site.path("vm/mine-files"))
        out = site.run(_files_args(10, 5, [], key="ad/latest.html", template="AD - health report"))
        self.assertTrue(gone(_page(3, 3)[:-5] + "-files"))
        self.assertTrue(os.path.exists(site.path("vm/mine-files")))
        self.assertTrue(any("its page is gone" in x for x in out["files_removed"]), out["files_removed"])
        self.assertIn("answer-keys-RHEL_9_STIG-V2R10.xml: removed: its page is gone", site.text("vm/latest.html"))
        self.assertNotIn("files", site.index())                                   # nothing hosted any more
        # the total: 1 MB for every report together - the oldest run's go first (its page says so), never a latest page's
        part = self.file("part.xml", b"x" * 400000)
        for job, day in ((11, 6), (12, 7), (13, 8)):
            out = site.run(_files_args(job, day, [part], files_total_max_mb=1, history=3))
        self.assertTrue(gone(_page(11, 6)[:-5] + "-files"))
        self.assertIn("part.xml: removed to keep all hosted files under 1 MB (report_web_files_total_max_mb)", site.text(_page(11, 6)))
        self.assertTrue(os.path.exists(site.path(_page(12, 7)[:-5] + "-files/part.xml")))
        # a run whose files cannot fit even then is not hosted, said - and nothing else goes for it (Codex)
        big = self.file("big.xml", b"x" * 700000)
        out = site.run(_files_args(14, 9, [big], key="ad/latest.html", template="AD - health report", files_total_max_mb=1))
        self.assertEqual(out["files"], [])
        self.assertIn("at report_web_files_total_max_mb (1 MB) with only latest pages' files left", out["files_refused"][0]["why"])
        self.assertTrue(os.path.exists(site.path(_page(12, 7)[:-5] + "-files/part.xml")))
        self.assertEqual(out["files_removed"], [])
        # check mode: lists what it would remove, removes nothing
        if kind != "win":
            os.remove(site.path(_page(12, 7)))
            out = run_module(dict(_files_args(15, 10, [], key="ad/latest.html", template="AD - health report"), dest=site.dest,
                                  _ansible_check_mode=True), env=site.env)
            self.assertIn("it would remove: %s-files" % _page(12, 7)[:-5], out["msg"])
            self.assertTrue(os.path.exists(site.path(_page(12, 7)[:-5] + "-files/part.xml")))

    def test_g_a_folder(self):
        self.transport("folder")

    def test_h_an_upload_webdav(self):
        self.transport("web")

    def test_i_a_share_smb(self):
        self.transport("smb")

    @unittest.skipUnless(PWSH, "no pwsh")
    def test_j_a_windows_server(self):
        self.transport("win")



# 1.79.14: reading the index (index.json) is slow here, so two jobs' read and write overlap - the race the lock prevents
SLOW_INDEX = ("_orig_read = m.Target.read\n"
              "def _slow_read(self, rel, _o=_orig_read):\n"
              "    t = _o(self, rel)\n"
              "    if rel == 'index.json':\n"
              "        import time as _t; _t.sleep(float(os.environ.get('SLOW_INDEX', '0')))\n"
              "    return t\n"
              "m.Target.read = _slow_read\n"
              "_orig_sread = getattr(m.SmbTarget, 'read')\n"
              "def _slow_sread(self, rel, _o=_orig_sread):\n"
              "    t = _o(self, rel)\n"
              "    if rel == 'index.json':\n"
              "        import time as _t; _t.sleep(float(os.environ.get('SLOW_INDEX', '0')))\n"
              "    return t\n"
              "m.SmbTarget.read = _slow_sread\n")


def _start_module(args, env=None, patch=""):
    """site_report_web started (not waited for): (process, its args file)."""
    argf = os.path.join(tempfile.mkdtemp(), "args.json")
    env = dict({"TMPDIR": os.path.dirname(argf)}, **(env or {}))
    with open(argf, "w") as f:
        json.dump({"ANSIBLE_MODULE_ARGS": dict({"retries": 0}, **args)}, f)
    return subprocess.Popen([sys.executable, "-c", BOOT, argf, os.path.join(REPO, "roles", "site_email", "library", "site_report_web.py"),
                             os.path.join(REPO, "plugins", "module_utils"), patch], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=dict(os.environ, **(env or {})))


class Lock17914(unittest.TestCase):
    """1.79.14: two jobs that publish at the same time never lose each other's line or hosted files: the index's lock
    (<index.json>.lock: a folder O_EXCL, a share's create-new, an upload made then read back; stale ones taken over);
    a server that honours If-Match refuses a write over an index changed meanwhile (412, tried again); a Windows server's
    script writes the index only when it is as read, else leaves this run's line and files beside it for the next run."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def file(self, name, data):
        p = os.path.join(self.tmp, name)
        with open(p, "wb") as f:
            f.write(data)
        return {"path": p, "filename": name}

    def test_a_the_lock_itself(self):
        site = os.path.join(self.tmp, "site")
        os.mkdir(site)
        t = rw.Target(site, "", 5)
        clock = [1000.0]
        self.assertEqual(rw.take_lock(t, "index.json", "job-a", 5, clock=lambda: clock[0], sleep=lambda s: None), "index.json.lock")
        clock2 = [1000.0]

        def tick(s):
            clock2[0] += s
        self.assertIsNone(rw.take_lock(t, "index.json", "job-b", 3, clock=lambda: clock2[0], sleep=tick))   # held: waited, not taken
        rw.release_lock(t, "index.json.lock", "job-b")                                     # never another job's
        self.assertTrue(os.path.exists(os.path.join(site, "index.json.lock")))
        rw.release_lock(t, "index.json.lock", "job-a")
        self.assertFalse(os.path.exists(os.path.join(site, "index.json.lock")))
        with open(os.path.join(site, "index.json.lock"), "w") as f:                       # a job that died 11 minutes ago
            json.dump({"token": "dead", "when": time.time() - 660}, f)
        self.assertEqual(rw.take_lock(t, "index.json", "job-c", 1), "index.json.lock")
        with open(os.path.join(site, "index.json.lock")) as f:
            self.assertIn("job-c", f.read())
        self.assertTrue(rw.lock_held(t, "index.json.lock", "job-c"))
        self.assertFalse(rw.lock_held(t, "index.json.lock", "job-a"))
        # (Codex) a lock with no time of its own (half written, or the Windows script's from before): its file's age
        with open(os.path.join(site, "index.json.lock"), "w") as f:
            f.write("")
        old = time.time() - 700
        os.utime(os.path.join(site, "index.json.lock"), (old, old))
        self.assertEqual(rw.take_lock(t, "index.json", "job-d", 1), "index.json.lock")
        # a folder of its own for the index: made for the lock (its first publish)
        self.assertEqual(rw.take_lock(t, "catalog/index.json", "job-e", 1), "catalog/index.json.lock")

    def test_a2_merge_lines(self):
        a = {"key": "k", "when": "2026-11-20 06:00 UTC", "status": "ok", "runs": [{"page": "p1", "when": "2026-11-20 06:00 UTC"}],
             "trend": [{"when": "2026-11-20 06:00 UTC", "job": 1}]}
        b = {"key": "k", "when": "2026-11-20 07:00 UTC", "status": "critical", "runs": [{"page": "p2", "when": "2026-11-20 07:00 UTC"},
                                                                                         {"page": "p1", "when": "2026-11-20 06:00 UTC"}],
             "trend": [{"when": "2026-11-20 07:00 UTC", "job": 2}, {"when": "2026-11-20 06:00 UTC", "job": 1}]}
        m = rw.merge_lines(a, b, 5)
        self.assertEqual((m["status"], [x["page"] for x in m["runs"]], len(m["trend"])), ("critical", ["p2", "p1"], 2))
        self.assertEqual(rw.merge_lines(b, a, 5)["status"], "critical")                  # the newer head, whatever the order
        self.assertEqual(len(rw.merge_lines(a, b, 1)["runs"]), 1)

    def race(self, kind, lock_wait=120, conditional=False):
        """Two templates published 0.3 s apart, each slow between reading the index and writing it -> the index."""
        site = Site(kind, conditional)
        self.addCleanup(site.close)
        a = dict(_files_args(1, 1, [self.file("a.xml", XML)], lock_wait=lock_wait), dest=site.dest)
        b = dict(_files_args(2, 1, [self.file("b.xml", XML)], key="ad/latest.html", template="AD - health report", lock_wait=lock_wait),
                 dest=site.dest)
        env = dict(site.env, SLOW_INDEX="2")
        pa = _start_module(dict(a, retries=2, retry_delay=1), env, SLOW_INDEX)
        time.sleep(0.3)
        pb = _start_module(dict(b, retries=2, retry_delay=1), env, SLOW_INDEX)
        outs = [json.loads(p.communicate(timeout=180)[0] or "{}") for p in (pa, pb)]
        return site.index(), outs, site

    def check_both(self, kind, **kw):
        doc, outs, site = self.race(kind, **kw)
        self.assertTrue(all(o.get("published") for o in outs), outs)
        self.assertEqual(sorted(r["key"] for r in doc["reports"]), ["ad/latest.html", "vm/latest.html"], doc)
        self.assertEqual(sorted(e["key"] for e in doc["files"]), ["ad/latest.html", "vm/latest.html"])
        self.assertFalse(os.path.exists(site.path("index.json.lock")))                 # let go
        return doc, outs

    def test_b_a_folder_both_kept(self):
        self.check_both("folder")

    def test_b2_two_runs_of_one_template_both_listed(self):
        """(Codex) two runs of the same template at once: its line made again from the index read under the lock - both
        runs on it, both runs' files in the ledger."""
        site = Site("folder")
        self.addCleanup(site.close)
        env = dict(site.env, SLOW_INDEX="2")
        pa = _start_module(dict(_files_args(1, 1, [self.file("a.xml", XML)]), dest=site.dest), env, SLOW_INDEX)
        time.sleep(0.3)
        pb = _start_module(dict(_files_args(2, 1, [self.file("b.xml", XML)]), dest=site.dest), env, SLOW_INDEX)
        for pr in (pa, pb):
            pr.communicate(timeout=180)
        doc = site.index()
        self.assertEqual(sorted(x["job"] for x in doc["reports"][0]["runs"]), [1, 2])
        self.assertEqual(len(doc["files"]), 2)

    def test_b3_a_lock_no_longer_ours_writes_nothing(self):
        """(Codex) the fence: the lock taken over (or another job's made over it) between taking it and writing - the index
        is not written, the publish fails (tried again with retries)."""
        site = os.path.join(self.tmp, "site")
        os.mkdir(site)
        patch = ("_orig_take = m.take_lock\n"
                 "def _take(target, rel, token, wait, **kw):\n"
                 "    lk = _orig_take(target, rel, token, wait, **kw)\n"
                 "    open(os.path.join(target.dest, lk), 'w').write('{\"token\": \"someone-else\", \"when\": 9e12}')\n"
                 "    return lk\n"
                 "m.take_lock = _take\n")
        out = run_module(dict(_files_args(1, 1, []), dest=site), patch=patch)
        self.assertTrue(out.get("failed"), out)
        self.assertIn("is no longer this job's", out["msg"])
        self.assertFalse(os.path.exists(os.path.join(site, "index.json")))

    def test_c_without_the_lock_one_is_lost(self):
        """The test's own proof: with lock_wait 0 (no lock) the same race loses the first job's line and files."""
        doc, outs, _ = self.race("folder", lock_wait=0)
        self.assertEqual([r["key"] for r in doc["reports"]], ["ad/latest.html"])
        self.assertEqual([e["key"] for e in doc["files"]], ["ad/latest.html"])

    def test_d_an_upload_webdav_both_kept(self):
        """nginx's dav ignores If-None-Match: the lock is made, then read back after a moment."""
        self.check_both("web")

    def test_e_a_conditional_server_refuses_the_lost_write(self):
        """No lock (lock_wait 0), a server that honours If-Match (Apache, IIS): the second write is refused (412) and
        tried again from the start - both kept."""
        doc, outs = self.check_both("web", lock_wait=0, conditional=True)
        self.assertTrue(any("412" in r for o in outs for r in o.get("retried") or []) or any("412" in str(o) for o in outs), outs)

    def test_f_a_share_both_kept(self):
        self.check_both("smb")

    @unittest.skipUnless(PWSH, "no pwsh")
    def test_g_a_windows_server_pending_then_put_in(self):
        """Two runs read the index, then their scripts run one after the other: the second finds it changed - its line and
        files go to index.pending-*.json, the index stays the first's; the next run puts them in and deletes the file."""
        site = Site("win")
        self.addCleanup(site.close)
        read = clm(READ_PS, {"SITE_CHECK_CONFIG": json.dumps({"dest": site.root, "index": ["index.json"]})})["read"]
        common = dict(windows_host="web01.example.mil", index_text=read["index"] or "", index_sha=read["sha"], dest=site.dest)
        oa = run_module(dict(_files_args(1, 1, [self.file("a.xml", XML)]), **common))
        ob = run_module(dict(_files_args(2, 1, [self.file("b.xml", XML)], key="ad/latest.html", template="AD - health report"), **common))
        wa, wb = clm(oa["script"])["write"], clm(ob["script"])["write"]
        self.assertTrue(wa["ok"] and wb["ok"], (wa, wb))
        self.assertEqual((wa.get("pending"), len(wb.get("pending") or [])), ([], 1))
        self.assertEqual([r["key"] for r in site.index()["reports"]], ["vm/latest.html"])     # the first's index stands
        self.assertFalse(os.path.exists(site.path("index.json.lock")))
        out = site.run(_files_args(3, 2, [], key="hc/latest.html", template="Health check"))    # the next run puts it in
        doc = site.index()
        self.assertEqual(sorted(r["key"] for r in doc["reports"]), ["ad/latest.html", "hc/latest.html", "vm/latest.html"])
        self.assertEqual(sorted(e["key"] for e in doc["files"]), ["ad/latest.html", "vm/latest.html"])
        self.assertEqual([f for f in os.listdir(site.root) if ".pending-" in f], [])
        self.assertIn(wb["pending"][0], out["ps"]["deleted"])

    @unittest.skipUnless(PWSH, "no pwsh")
    def test_h_a_pending_file_kept_until_its_merge_is_written(self):
        """(Codex) a run that puts a pending file in, but whose own index write is deferred too (the index changed again):
        the pending file stays - deleted only once an index that has it is written."""
        site = Site("win")
        self.addCleanup(site.close)
        read = clm(READ_PS, {"SITE_CHECK_CONFIG": json.dumps({"dest": site.root, "index": ["index.json"]})})["read"]
        common = dict(windows_host="web01.example.mil", index_text=read["index"] or "", index_sha=read["sha"], dest=site.dest)
        oa = run_module(dict(_files_args(1, 1, []), **common))
        ob = run_module(dict(_files_args(2, 1, [], key="ad/latest.html", template="AD - health report"), **common))
        clm(oa["script"]), clm(ob["script"])                                             # b went to a pending file
        read = clm(READ_PS, {"SITE_CHECK_CONFIG": json.dumps({"dest": site.root, "index": ["index.json"]})})["read"]
        self.assertEqual(len(read["pending"]), 1)
        common = dict(windows_host="web01.example.mil", index_text=read["index"] or "", index_sha=read["sha"],
                      index_pending=read["pending"], dest=site.dest)
        oc = run_module(dict(_files_args(3, 2, [], key="hc/latest.html", template="Health check"), **common))
        od = run_module(dict(_files_args(4, 2, [], key="db/latest.html", template="Database health"), **common))
        clm(od["script"])                                                                # d writes the index (with b's line)
        wc = clm(oc["script"])["write"]                                                  # c finds it changed: deferred
        self.assertEqual(len(wc["pending"]), 1)
        left = sorted(f for f in os.listdir(site.root) if ".pending-" in f)
        self.assertEqual(len(left), 1)                                                   # b's taken by d's write; c's now there
        self.assertIn("ad/latest.html", [r["key"] for r in site.index()["reports"]])


if __name__ == "__main__":
    unittest.main(verbosity=1)
