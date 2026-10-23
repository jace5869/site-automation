#!/usr/bin/env python3
"""The report website (roles/site_email, report_web_*): the file names (report_web_files,
plugins/filter/site_filters.py) and the site_report_web module's pieces - the logo put inside the page, the
index page, the report folder (a local path), and the PowerShell scripts for a Windows server (report_web_host) -
run by pwsh in Constrained Language Mode when pwsh is there.
    python3 tests/test_report_web.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
sys.path.insert(0, os.path.join(REPO, "roles", "site_email", "library"))

import site_filters as sf  # noqa: E402
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

    def test_folder_target(self):
        t = rw.Target(self.tmp, "", 5)
        self.assertIsNone(t.read("a/index.json"))
        t.write("a/b/page.html", "<p>é</p>", "text/html")
        self.assertEqual(t.read("a/b/page.html"), "<p>é</p>")
        self.assertEqual([f for f in os.listdir(os.path.join(self.tmp, "a", "b"))], ["page.html"])   # no temporary file left
        for bad in ("relative/path", os.path.join(self.tmp, "not-mounted")):
            with self.assertRaises(rw.Fail):
                rw.Target(bad, "", 5)

    def test_http_login_from_the_environment(self):
        os.environ.update(REPORT_WEB_TOKEN="", REPORT_WEB_USERNAME="up", REPORT_WEB_PASSWORD="pw")
        self.assertEqual(rw.Target("https://reports.example.mil/r", "", 5).auth, "Basic dXA6cHc=")
        os.environ["REPORT_WEB_TOKEN"] = "tok"
        self.assertEqual(rw.Target("https://reports.example.mil/r", "", 5).auth, "Bearer tok")
        for k in ("REPORT_WEB_TOKEN", "REPORT_WEB_USERNAME", "REPORT_WEB_PASSWORD"):
            os.environ.pop(k)
        self.assertEqual(rw.Target("http://reports.example.mil/r", "", 5).auth, "")


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
        out = clm(script)["write"]
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["written"], ["t/p.html", "t/latest.html", "index.json"])
        with open(os.path.join(dest, "t", "latest.html"), "rb") as f:
            self.assertEqual(f.read(), page.encode("ascii"))                       # exactly: no BOM, no newline added
        self.assertEqual(sorted(os.listdir(os.path.join(dest, "t"))), ["latest.html", "p.html"])
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


if __name__ == "__main__":
    unittest.main(verbosity=1)
