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
import importlib.util
import json
import os
import random
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
for _name in ("site_aap", "site_report_page"):          # as Ansible gives them to the modules (ansible.cfg: module_utils)
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
