#!/usr/bin/env python3
"""The reports' look (roles/site_email): the strip of past runs - report_headline, report_runs, the plain-text
line (plugins/filter/site_filters.py), site_report_history against the fake AAP (tests/servicenow/aapmock.py),
in this process - the logo's size (image_size), and the command, portal and atlas looks' templates rendered with Jinja.
    python3 tests/test_report_history.py"""
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
_spec = importlib.util.spec_from_file_location("ansible.module_utils.site_aap", os.path.join(REPO, "plugins", "module_utils", "site_aap.py"))
_aap = importlib.util.module_from_spec(_spec)
sys.modules["ansible.module_utils.site_aap"] = _aap
_spec.loader.exec_module(_aap)
sys.path.insert(0, os.path.join(REPO, "roles", "site_email", "library"))

import site_filters as sf  # noqa: E402
import site_report_history as rh  # noqa: E402

REPORT = {"title": "Snapshot cleanup: deleted 4", "status": "warning",
          "summary": [{"label": "Space freed", "value": "212 GB"}, {"label": "Deleted", "value": 4, "status": "ok"},
                      {"label": "Failed", "value": 1}]}


def past(*runs):
    """past((job status, report status or None, value), ...) as site_report_history returns them, oldest first."""
    out = []
    for i, (job, st, value) in enumerate(runs):
        out.append({"id": 100 + i, "status": job, "finished": "2026-10-%02dT06:01:12.5Z" % (i + 1),
                    "report": {"status": st, "value": value, "label": "Deleted"} if st else None})
    return out


class Headline(unittest.TestCase):
    def test_the_first_plain_number(self):
        self.assertEqual(sf.report_headline(REPORT), {"status": "warning", "value": 4, "label": "Deleted"})

    def test_numbers_as_text(self):
        r = {"status": "ok", "summary": [{"label": "A", "value": "98%"}, {"label": "B", "value": "1,200"}]}
        self.assertEqual(sf.report_headline(r)["value"], 1200)
        self.assertEqual(sf.report_headline({"summary": [{"label": "x", "value": "2.5"}]})["value"], 2.5)

    def test_none(self):
        self.assertEqual(sf.report_headline({"status": "odd", "summary": [{"label": "On", "value": True}]}),
                         {"status": "info", "value": None, "label": ""})
        self.assertEqual(sf.report_headline(None), {"status": "info", "value": None, "label": ""})


class Runs(unittest.TestCase):
    def test_this_run_last_the_tallest_outlined(self):
        bars = sf.report_runs(past(("successful", "ok", 2), ("successful", "ok", 0)), REPORT)
        self.assertEqual([b["current"] for b in bars], [False, False, True])
        self.assertEqual([b["height"] for b in bars], [4 + 16, 4, 36])
        self.assertEqual(bars[-1]["text"], "this run - Deleted 4 - warning")
        self.assertEqual(bars[0]["text"], "2026-10-01 06:01 - Deleted 2 - ok")

    def test_how_the_job_ended_without_a_report(self):
        bars = sf.report_runs(past(("successful", None, None), ("failed", None, None), ("error", None, None),
                                   ("canceled", None, None), ("failed", "ok", 3)), REPORT)
        self.assertEqual([b["status"] for b in bars], ["ok", "critical", "critical", "unknown", "critical", "warning"])
        self.assertEqual([b["height"] for b in bars[:4]], [18] * 4)

    def test_no_numbers_anywhere_all_half(self):
        r = {"status": "ok", "summary": [{"label": "Freed", "value": "3 GB"}]}
        self.assertEqual([b["height"] for b in sf.report_runs(past(("successful", "ok", None)), r)], [18, 18])
        self.assertEqual([b["height"] for b in sf.report_runs(past(("successful", "ok", 0)), dict(REPORT, summary=[]))], [4, 18])

    def test_at_most_runs_bars(self):
        bars = sf.report_runs(past(*[("successful", "ok", i) for i in range(20)]), REPORT, 14)
        self.assertEqual(len(bars), 14)
        self.assertEqual(bars[0]["text"], "2026-10-08 06:01 - Deleted 7 - ok")
        self.assertEqual(len(sf.report_runs(past(("successful", "ok", 1)), REPORT, 1)), 1)

    def test_plain_text_line(self):
        bars = sf.report_runs(past(("successful", "ok", 1), ("failed", None, None)), REPORT)
        text = sf.report_text(REPORT, "AAP job 9", bars)
        self.assertIn("\nLast 3 runs, oldest first: ok critical warning (this run)\n", text)
        self.assertNotIn("Last ", sf.report_text(REPORT, "AAP job 9"))


def png(path, w, h):
    raw = b"".join(b"\x00" + b"\x10\x30\x90" * w for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class ImageSize(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def p(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_png_gif_jpeg(self):
        png(os.path.join(self.tmp, "a.png"), 371, 481)
        self.assertEqual(sf.image_size(os.path.join(self.tmp, "a.png")), [371, 481])
        self.assertEqual(sf.image_size(os.path.join(self.tmp, "a.png"), 84), [65, 84])
        self.assertEqual(sf.image_size(self.p("b.gif", b"GIF89a" + struct.pack("<HH", 300, 100) + b"\x00" * 8), 50), [150, 50])
        # a JPEG: SOI, an APP0 segment, then SOF0 with height 120 and width 90
        jpg = b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9 \
            + b"\xff\xc0" + struct.pack(">HBHH", 17, 8, 120, 90) + b"\x00" * 10
        self.assertEqual(sf.image_size(self.p("c.jpg", jpg)), [90, 120])

    def test_not_a_picture(self):
        self.assertEqual(sf.image_size(self.p("d.png", b"hello, this is text")), [0, 0])
        self.assertEqual(sf.image_size(os.path.join(self.tmp, "none.png")), [0, 0])


class History(unittest.TestCase):
    """site_report_history against the fake AAP: job template 71 has jobs 901-918."""

    def setUp(self):
        from http.server import ThreadingHTTPServer
        for k in ("AAPMOCK_LEGACY", "AAPMOCK_JOBS_FORBID", "AAPMOCK_JOBS_HIDE", "AAPMOCK_JOB_CUT"):
            os.environ.pop(k, None)
        spec = importlib.util.spec_from_file_location("aapmock_%d" % id(self), os.path.join(HERE, "servicenow", "aapmock.py"))
        self.mock = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mock)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), self.mock.H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.env = {"CONTROLLER_HOST": "http://127.0.0.1:%d" % self.srv.server_port, "CONTROLLER_USERNAME": "admin",
                    "CONTROLLER_PASSWORD": "goodpw"}

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def hist(self, job=919, template=71, runs=13, **env):
        return rh.history(_aap.Api(_aap.connection(dict(self.env, **env))), job, template, runs)

    def test_the_finished_runs_before_this_one_oldest_first(self):
        runs, why = self.hist()
        self.assertEqual(why, "")
        self.assertEqual([r["id"] for r in runs], list(range(905, 918)))          # 918 is still running
        self.assertEqual(runs[0], {"id": 905, "status": "successful", "finished": "2026-10-05T06:01:12.5Z", "report": None})
        self.assertEqual(runs[-1]["report"], {"status": "ok", "value": 5, "label": "Deleted"})
        self.assertEqual(self.hist(job=910, runs=3)[0][-1]["id"], 909)

    def test_older_api_path_and_a_token(self):
        os.environ["AAPMOCK_LEGACY"] = "1"
        runs, why = self.hist(runs=2, CONTROLLER_USERNAME="", CONTROLLER_PASSWORD="", CONTROLLER_OAUTH_TOKEN="goodtoken")
        self.assertEqual((why, [r["id"] for r in runs]), ("", [916, 917]))

    def test_a_job_whose_answer_is_cut_off(self):
        # a job with large artifacts, its answer cut off on the way (IncompleteRead): that run's bar without its report, no crash
        os.environ["AAPMOCK_JOB_CUT"] = "917,916"
        runs, why = self.hist(runs=3)
        self.assertEqual(why, "")
        self.assertEqual([(r["id"], r["status"], r["report"]) for r in runs],
                         [(915, "successful", {"status": "warning", "value": 0, "label": "Deleted"}), (916, "failed", None), (917, "successful", None)])
        api = _aap.Api(_aap.connection(self.env))
        api.probe()
        self.assertEqual(api.call("GET", "jobs/917/"), (-1, {"detail": "the answer was cut off: IncompleteRead"}))

    def test_no_runs_yet(self):
        self.assertEqual(self.hist(job=901, template=71), ([], ""))      # the first run: AAP shows this job

    def test_jobs_hidden_from_the_user(self):
        # AAP answers 200 with an empty list for jobs its user may not see - not 403: the strip would show this run only
        os.environ["AAPMOCK_JOBS_HIDE"] = "1"
        runs, why = self.hist()
        self.assertEqual(runs, [])
        self.assertIn("The AAP user admin cannot see this template's jobs", why)
        self.assertIn("not even this job 919", why)
        self.assertIn("give admin Organization Auditor on the organization of job template 71", why)

    def test_why_not(self):
        os.environ["AAPMOCK_JOBS_FORBID"] = "1"
        runs, why = self.hist()
        self.assertEqual(runs, [])
        self.assertIn("HTTP 403", why)
        self.assertIn("Organization Auditor on the template's organization", why)
        os.environ.pop("AAPMOCK_JOBS_FORBID")
        self.assertIn("refused the login", self.hist(CONTROLLER_PASSWORD="bad")[1])
        self.assertEqual(rh.history(_aap.Api(_aap.connection({}), no_credential=rh.NO_CREDENTIAL), 1, 71, 13)[1], rh.NO_CREDENTIAL)


class CommandLook(unittest.TestCase):
    """roles/site_email/templates/report_command.html.j2, rendered with plain Jinja (it uses no Ansible filter)."""

    def render(self, **v):
        import jinja2
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(REPO, "roles", "site_email", "templates")),
                                 autoescape=False)
        base = {"_report": dict(REPORT, subtitle="<b>sub</b>",
                                sections=[{"title": "Deleted", "status": "ok", "columns": ["VM"], "rows": [["<x>"]]}]),
                "_email_footer": "AAP job 9", "_email_logo_cid": "", "_email_runs": [],
                "report_email_header_text": "Site automation report"}
        base.update(v)
        return env.get_template("report_command.html.j2").render(**base)

    def test_no_logo_no_strip(self):
        h = self.render()
        self.assertNotIn("cid:", h)
        self.assertNotIn("RUNS</td>", h)
        self.assertIn(">WARNING</span>", h)
        self.assertIn("&lt;b&gt;sub&lt;/b&gt;", h)
        self.assertIn("&lt;x&gt;", h)

    def test_logo_and_strip(self):
        bars = sf.report_runs(past(("successful", "ok", 2), ("failed", None, None)), REPORT)
        h = self.render(_email_logo_cid="site-report-logo", _email_logo_size=[65, 84], _email_runs=bars)
        self.assertIn('src="cid:site-report-logo" width="65" height="84"', h)
        self.assertIn("LAST 3 RUNS", h)
        self.assertEqual(h.count('valign="bottom" height="40"'), 3)
        self.assertIn("border:2px solid #ffffff;", h)
        self.assertEqual(h.count("border:2px solid #ffffff;"), 1)

    def test_many_numbers_wrap_after_five(self):
        h = self.render(_report=dict(REPORT, summary=[{"label": "N%d" % i, "value": i} for i in range(7)]))
        self.assertEqual(h.count("letter-spacing:1px;color:#5a6779;text-transform:uppercase;"), 7)
        self.assertEqual(h.count('<tr><td style="padding:6px 20px 4px 20px;">'), 1)


class Ago(unittest.TestCase):
    def test_how_long_ago_each_past_run_finished(self):
        import datetime
        now = datetime.datetime(2026, 10, 4, 6, 31)
        bars = sf.report_runs(past(("successful", "ok", 1), ("successful", "ok", 2), ("successful", "ok", 3)), REPORT, now=now)
        self.assertEqual([b["ago"] for b in bars], ["3 days ago", "2 days ago", "24 h ago", ""])
        self.assertEqual(sf._run_ago("", now), "")
        self.assertEqual(sf._run_ago("2026-10-05T00:00:00Z", now), "0 min ago")


class PortalLook(unittest.TestCase):
    """roles/site_email/templates/report_portal.html.j2, rendered with plain Jinja (it uses no Ansible filter)."""

    def render(self, **v):
        import jinja2
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(REPO, "roles", "site_email", "templates")),
                                 autoescape=False)
        base = {"_report": dict(REPORT, subtitle="<b>sub</b>",
                                sections=[{"title": "Deleted", "status": "ok", "columns": ["VM", "State"], "rows": [["<x>", "off"]],
                                           "cell_status": [["", "critical"]]},
                                          {"title": "Notes", "lines": ["one <line>"]}]),
                "_email_footer": "AAP job 9", "_email_logo_cid": "", "_email_runs": [],
                "report_email_header_text": "Site <automation> report"}
        base.update(v)
        return env.get_template("report_portal.html.j2").render(**base)

    def test_no_logo_no_strip(self):
        h = self.render()
        self.assertNotIn("cid:", h)
        self.assertNotIn("RUNS</td>", h)
        self.assertIn("Run from the command line", h)
        self.assertIn(">Warning</span>", h)
        for raw in ("<b>sub</b>", "<x>", "<line>", "<automation>"):
            self.assertNotIn(raw, h)
        self.assertIn("Site &lt;automation&gt; report", h)
        self.assertIn("one &lt;line&gt;", h)

    def test_tiles_rows_of_four_amber_red_outlined(self):
        h = self.render(_report=dict(REPORT, summary=[{"label": "N%d" % i, "value": i, "status": "critical" if i == 5 else ""}
                                                      for i in range(6)]))
        self.assertEqual(h.count('<td width="25%" valign="top"'), 6)
        self.assertEqual(h.count('<td width="25%" style="padding:0 0 0 6px;">&nbsp;</td>'), 2)
        self.assertEqual(h.count("border:2px solid #b42318;border-radius:18px"), 1)

    def test_job_line_logo_strip_last_run(self):
        import datetime
        bars = sf.report_runs(past(("successful", "ok", 2), ("failed", None, None)), REPORT, 14, 44,
                              now=datetime.datetime(2026, 10, 4, 6, 1))
        h = self.render(_email_logo_cid="site-report-logo", _email_logo_size=[43, 56], _email_runs=bars,
                        awx_job_id=4182, awx_job_template_name="VMware - <snap>")
        self.assertIn('src="cid:site-report-logo" width="43" height="56"', h)
        self.assertIn("AAP job 4182 &middot; VMware - &lt;snap&gt;", h)
        self.assertIn("LAST 3 RUNS", h)
        self.assertEqual(h.count('valign="bottom" height="48"'), 3)
        self.assertEqual(h.count('bgcolor="#0f1f1a" style="background:#0f1f1a;padding:2px;'), 1)
        self.assertEqual(h.count("<!--[if mso]>"), h.count("<!--[if !mso]><!-->") + 2)  # + the fixed-width box
        self.assertIn('<!--[if mso]><table role="presentation" width="760"', h)
        self.assertIn('height="44" bgcolor="#d98a1c" title="this run - Deleted 4 - warning"', h)
        self.assertIn(">Critical</span><!--<![endif]--></td>\n<td style=\"font-size:13px;color:#5b6b66;white-space:nowrap;\">47 h ago</td>", h)

    def test_cell_status_as_a_pill(self):
        self.assertIn("color:#b42318;font-weight:600;padding:1px 9px;\">off</span>", self.render())


class GlassLook(PortalLook):
    """roles/site_email/templates/report_glass.html.j2: the email, and the web copy (_page_runs set) with the blur,
    icons and print that only a browser gets."""

    def render(self, **v):
        import jinja2
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(REPO, "roles", "site_email", "templates")),
                                 autoescape=False)
        base = {"_report": dict(REPORT, subtitle="<b>sub</b>",
                                summary=REPORT["summary"] + [{"label": "Old <ones>", "value": 3, "status": "warning"}],
                                sections=[{"title": "Deleted", "status": "ok", "columns": ["VM", "State"], "rows": [["<x>", "off"]],
                                           "cell_status": [["", "critical"]]},
                                          {"title": "Notes", "lines": ["one <line>"]}]),
                "_email_footer": "AAP job 9", "_email_logo_cid": "", "_email_runs": [],
                "report_email_header_text": "Site <automation> report"}
        base.update(v)
        return env.get_template("report_glass.html.j2").render(**base)

    def test_no_logo_no_strip(self):
        h = self.render()
        self.assertNotIn("cid:", h)
        self.assertNotIn(" runs</div>", h)
        self.assertIn("Run from the command line", h)
        self.assertIn('color:#9a5300;line-height:1.2;">Warning</div>\n<div style="font-size:12px;color:#0f2236;line-height:1.3;">Needs attention</div>', h)
        for raw in ("<b>sub</b>", "<x>", "<line>", "<automation>", "<ones>"):
            self.assertNotIn(raw, h)
        self.assertIn("Site &lt;automation&gt; report", h)
        self.assertIn("one &lt;line&gt;", h)

    def test_email_preview_line_phone_rules_no_browser_only_parts(self):
        h = self.render()
        # the line under the subject in the inbox: the subtitle, then the warning and critical numbers
        self.assertIn("mso-hide:all;font-size:1px;line-height:1px;color:#dde8f0;\">&lt;b&gt;sub&lt;/b&gt; · Old &lt;ones&gt;: 3</div>", h)
        self.assertIn("@media (max-width:760px)", h)
        for web_only in ("<svg", "backdrop-filter", "@media print", 'class="g-bg"'):
            self.assertNotIn(web_only, h)
        self.assertNotIn("Open the full report", h)
        self.assertIn("Open the full report", self.render(_email_web_url="https://reports.example.mil/a/latest.html"))

    def test_web_copy_blur_icons_print(self):
        h = self.render(_page_runs=[], _page_logo_cid="", _email_web_url="https://reports.example.mil/a/latest.html")
        for web_only in ("<svg", "backdrop-filter:blur(22px)", "@media print", 'class="g-bg"', "@media (max-width:760px)"):
            self.assertIn(web_only, h)
        self.assertNotIn("mso-hide:all", h)
        self.assertNotIn("Open the full report", h)       # the page is the full report

    def test_tiles_rows_of_four_amber_red_outlined(self):
        h = self.render(_report=dict(REPORT, summary=[{"label": "N%d" % i, "value": i, "status": "critical" if i == 5 else ""}
                                                      for i in range(6)]))
        self.assertEqual(h.count('<td class="g-tile" width="25%" valign="top"'), 6)
        self.assertEqual(h.count('<td class="g-tile g-tile-empty" width="25%" style="padding:0 0 0 6px;">&nbsp;</td>'), 2)
        self.assertEqual(h.count("background:rgba(253,232,232,.80);border:1px solid #f5bfbf;"), 1)
        # every see-through surface has its solid colour for mail programs that draw no transparency
        self.assertEqual(h.count('bgcolor="#f8fbfd" style="width:100%;background:rgba(255,255,255,.66)'),
                         h.count("background:rgba(255,255,255,.66)") - 1)   # the critical tile: its own tint

    def test_job_line_logo_strip_last_run(self):
        import datetime
        bars = sf.report_runs(past(("successful", "ok", 2), ("failed", None, None)), REPORT, 14, 54,
                              now=datetime.datetime(2026, 10, 4, 6, 1))
        h = self.render(_email_logo_cid="site-report-logo", _email_logo_size=[62, 80], _email_runs=bars,
                        awx_job_id=4182, awx_job_template_name="VMware - <snap>")
        self.assertIn('src="cid:site-report-logo" width="62" height="80"', h)
        self.assertIn("AAP job 4182 &middot; VMware - &lt;snap&gt;", h)
        self.assertIn("Last 3 runs</div>", h)
        self.assertEqual(h.count('valign="bottom" height="60"'), 3)
        self.assertEqual(h.count('bgcolor="#0f2236" style="background:#0f2236;padding:2px;'), 1)
        self.assertEqual(h.count("<!--[if mso]>"), h.count("<!--[if !mso]><!-->") + 2)  # + the fixed-width box
        self.assertIn('<!--[if mso]><table role="presentation" width="760"', h)
        self.assertIn('height="54" bgcolor="#e0951f" title="this run - Deleted 4 - warning"', h)
        self.assertIn("Last run &middot; 47 h ago</div>", h)
        self.assertIn("&#9679;</span>&nbsp;Critical</span>", h)   # the colour key: only the colours in the strip
        self.assertNotIn("&nbsp;Unknown</span>", h)

    def test_cell_status_as_a_pill(self):
        self.assertIn("color:#b42318;font-weight:600;padding:1px 10px;\">off</span>", self.render())



def _wrap_columns(case):
    """1.12.0: a report may name a column's kind itself (wrap: lines break at spaces, the heading too, no minimum width,
    a status chip too) and give a short heading its full name as its title; without them, as before."""
    sec = {"title": "T", "columns": ["Cluster", "Takeover possible", "Size"], "rows": [["na-cluster-a", "no (partner down)", "1 GB"]],
           "cell_status": [["", "warning", ""]], "kinds": ["wrap", "wrap", ""], "column_titles": ["Cluster", "Takeover possible after a failure", "Size"]}
    h = case.render(_report=dict(REPORT, sections=[sec]))
    case.assertIn('<th class="k-wrap" scope="col"><button type="button">Cluster', h)
    case.assertIn('<th class="k-wrap" scope="col" title="Takeover possible after a failure"><button type="button">Takeover possible', h)
    case.assertIn('<th class="k-nw" scope="col"><button type="button">Size', h)                 # '' = from what it holds
    case.assertIn('<td class="k-wrap" data-l="Cluster" data-v="na-cluster-a"><span class="w">na-cluster-a</span></td>', h)
    case.assertIn("td.k-wrap .w{white-space:nowrap}", h)                       # a word up to 16 characters kept whole
    sec2 = dict(sec, rows=[["na-cluster-site-a-prod c1-02\nhome: c1-01", "no", "1 GB"]])
    case.assertIn('data-v="na-cluster-site-a-prod c1-02\nhome: c1-01">na-cluster-site-a-prod <span class="w">c1-02</span>\n'
                  '<span class="w">home:</span> <span class="w">c1-01</span></td>', case.render(_report=dict(REPORT, sections=[sec2])))
    case.assertIn('<td data-l="Takeover possible after a failure" data-v="no (partner down)" class="k-wrap">', h)   # the chip wraps too
    sec3 = dict(sec, rows=[["c", "+398 s", "1 GB"]])                                  # but a short one stays whole
    case.assertIn('<td data-l="Takeover possible after a failure" data-v="+398 s" class="nw">', case.render(_report=dict(REPORT, sections=[sec3])))
    case.assertIn("td.k-wrap{overflow-wrap:break-word}", h)
    h = case.render(_report=dict(REPORT, sections=[dict((k, v) for k, v in sec.items() if k not in ("kinds", "column_titles"))]))
    case.assertNotIn("k-wrap\"", h)
    case.assertNotIn(' title="Takeover', h)
    case.assertIn('<td data-l="Takeover possible" data-v="no (partner down)" class="nw">', h)


class AtlasLook(unittest.TestCase):
    """roles/site_email/templates/report_atlas.html.j2 (the report website's look), rendered with plain Jinja."""

    def render(self, **v):
        import jinja2
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(REPO, "roles", "site_email", "templates")),
                                 autoescape=False)
        base = {"_report": dict(REPORT, subtitle="<b>sub</b>", sections=[
                    {"title": "Start here", "status": "critical", "cards": [
                        {"status": "critical", "chip": "Critical", "meta": "Certificate", "title": "<t>", "subject": "web01",
                         "next": "Look further:", "code": "openssl x509 -in </x>"}]},
                    {"title": "Checks", "status": "warning", "columns": ["Host", "Result", "State", "Status"],
                     "rows": [["<h>", "PASSED", "off", "late"], ["db01", "x", "on", "breached"]],
                     "row_status": ["", "critical"], "cell_status": [["", "", "warning", ""]]},
                    {"title": "Healthy (2)", "text": "a, b", "passed": ["web<1>", "web2"], "passed_checks": ["disk", "time"]},
                    {"title": "Notes", "lines": ["one <line>"]}]),
                "_email_footer": "AAP job 9", "_page_logo_cid": "", "_page_runs": [],
                "report_email_header_text": "Site <automation> report"}
        base.update(v)
        return env.get_template("report_atlas.html.j2").render(**base)

    def test_wrap_columns_and_titles(self):
        _wrap_columns(self)

    def test_escaped_and_self_contained(self):
        h = self.render()
        for raw in ("<b>sub</b>", "<h>", "<line>", "<automation>", "<t>", "</x>", "web<1>"):
            self.assertNotIn(raw, h)
        self.assertNotRegex(h, r'<link|src="https?:|@import|url\(https?:')        # nothing from elsewhere: it opens from a share
        self.assertNotIn("cid:", h)

    def test_light_dark_and_the_switch(self):
        h = self.render()
        self.assertIn('@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){', h)
        self.assertIn(':root[data-theme="dark"]{', h)
        self.assertIn('class="seg" id="theme"', h)
        self.assertIn("localStorage.getItem('site-report-theme')", h)
        self.assertIn("@media print", h)

    def test_switches_without_the_script(self):
        """Light / auto / dark and the row height are radio buttons the CSS acts on: they work where a web server's
        Content-Security-Policy blocks the page's script (it only keeps the choice); Print needs it, so hidden then."""
        h = self.render()
        self.assertIn('<div class="seg" id="theme" role="radiogroup" aria-label="Light or dark">', h)
        self.assertIn('<div class="seg" id="density" role="radiogroup" aria-label="Row height">', h)
        for t in ("light", "auto", "dark"):
            self.assertIn('<input type="radio" name="site-report-theme" id="theme-%s" value="%s"' % (t, t), h)
        self.assertIn('id="theme-auto" value="auto" checked>', h)
        self.assertIn('id="density-comfortable" value="comfortable" checked>', h)
        self.assertIn(":root:has(#theme-light:checked){--ink:#111827;", h.replace("\n", "").replace(" ", ""))
        self.assertIn(":root:has(#theme-dark:checked){--ink:#e6edf3;", h.replace("\n", "").replace(" ", ""))
        self.assertIn(":root:has(#density-compact:checked){--cy:5px;", h)
        self.assertIn(".btn[hidden]{display:none}", h)
        self.assertIn('<button class="btn" type="button" id="print" hidden>', h)
        self.assertNotIn('role="radio"', h)

    def test_status_chips_with_words(self):
        h = self.render()
        self.assertIn('<span class="chip s-ok sh"><svg', h)                        # PASSED: a check mark, its word kept (on one line, 1.21.0)
        self.assertIn("<span>Passed</span>", h)
        self.assertRegex(h, r'<span class="chip s-warning( sh| c\d)?"><svg')         # cell_status
        self.assertIn('<tr class="r-critical">', h)                                # row_status
        self.assertIn("<span>breached</span>", h)                                  # a Status column: the row's status
        self.assertNotIn("<span>late</span>", h)                                    # ... a row without one: plain
        self.assertIn(">late</td>", h)

    def test_start_here_and_passed(self):
        h = self.render()
        self.assertIn('<article class="todo c-critical">', h)
        self.assertIn('<pre>openssl x509 -in &lt;/x&gt;</pre><button class="copy" type="button" hidden>Copy</button>', h)
        self.assertIn("Every check passed:", h)
        self.assertIn('<span class="mono">web&lt;1&gt;</span>', h)
        self.assertNotIn("<p>a, b</p>", h)                                         # the hosts instead of the text

    def test_copy_on_tiles_and_sections(self):
        h = self.render(_report=dict(REPORT, summary=[{"label": "Done <x>", "value": 4, "note": "in \"the\" week"},
                                                      {"label": "Failed", "value": 0}],
                                     sections=[{"title": "WAR <lines>", "lines": ["a"]}, {"title": "T", "columns": ["A"], "rows": [["1"]]}]))
        self.assertIn('aria-label="Copy Done &lt;x&gt;" data-copy="Done &lt;x&gt;: 4 (in &#34;the&#34; week)"', h)
        self.assertIn('data-copy="Failed: 0">', h)                                 # no note: no brackets
        self.assertEqual(h.count('<button class="tcopy" type="button" hidden'), 2)  # one per tile, shown by the script
        self.assertEqual(h.count('<button class="copy scopy" type="button" hidden'), 2)  # one per section
        self.assertIn('aria-label="Copy WAR &lt;lines&gt;"', h)
        self.assertIn("e.preventDefault();e.stopPropagation()", h)                 # a click on it does not fold the section
        self.assertIn("'text/html':new Blob", h)                                   # a table pastes as a table
        self.assertIn(".appbar,.tools2,.copy,.tcopy,.hero:after{display:none!important}", h)   # not printed

    def test_logo_strip_and_tiles(self):
        bars = sf.report_runs(past(("successful", "ok", 2), ("failed", None, None)), REPORT)
        h = self.render(_page_logo_cid="site-report-logo", _page_logo_size=[42, 56], _page_runs=bars)
        self.assertIn('src="cid:site-report-logo" width="42" height="56"', h)
        self.assertIn("Last 3 runs", h)
        self.assertEqual(h.count('<span class="bar c-'), 3)
        self.assertEqual(h.count('class="card tile'), 3)
        self.assertIn('<a href="#s2">', h)                                         # the bar of the sections

    def test_columns_sized_by_what_they_hold(self):
        """0.53.0: numbers right-aligned, short values whole, a mid one broken only at its spaces, paths at / and \\."""
        h = self.render(_report=dict(REPORT, sections=[{"title": "T", "columns": ["Host", "Rule", "Bytes", "Where", "Title", "Win"],
            "rows": [["web01.example.mil", "V-257800 (RHEL-09-412035)", "1,200", "/var/lib/containers/storage/overlay/abc/diff",
                      "RHEL 9 must automatically exit interactive command shell user sessions after 10 minutes of inactivity.", "C:\\x\\y.exe"],
                     ["db01", "V-1 (X-1)", "37", "/opt/a <b>", "short", "D:\\z"]]}]))
        self.assertIn('<th class="k-nw" scope="col"><button type="button">Host', h)
        self.assertIn('<th class="k-mid" scope="col"><button type="button">Rule', h)
        self.assertIn('<th class="k-num" scope="col"><button type="button">Bytes', h)
        self.assertIn('<th class="k-path" scope="col"><button type="button">Where', h)
        self.assertIn('<th class="k-long" scope="col"><button type="button">Title', h)
        self.assertIn('<td class="k-num" data-l="Bytes">1,200</td>', h)
        self.assertIn('<span class="w">V-257800</span> <span class="w">(RHEL-09-412035)</span>', h)
        self.assertIn('/<wbr>var/<wbr>lib/<wbr>', h)
        self.assertIn('/<wbr>opt/<wbr>a &lt;b&gt;</td>', h)                        # escaped first, then the breaks
        self.assertIn('C:\\<wbr>x\\<wbr>y.exe', h)

    def test_density_and_bar(self):
        h = self.render()
        self.assertIn('class="seg" id="density"', h)
        self.assertIn(":root[data-density=\"compact\"]{", h)
        self.assertIn("localStorage.getItem('site-report-density')", h)
        self.assertIn('<div class="appbar">', h)
        self.assertIn("max-width:1760px", h)
        self.assertIn("IntersectionObserver", h)                                   # the section in view, marked


    def test_rail_search_columns_csv(self):
        """0.63.0: the section rail (more than two sections), the search box, Columns (check boxes the CSS acts on, a
        rule per place up to the most columns here) and CSV; what needs the script hidden until it runs."""
        h = self.render()
        self.assertIn('<div class="wrap has-rail">', h)
        self.assertIn('<nav class="card rail" aria-label="Sections of this report">', h)
        self.assertIn('<a href="#s2"><span class="dot c-warning"></span><span class="t">Checks</span><span class="m"></span><span class="n">2</span></a>', h)
        self.assertIn('<nav class="nav railed" aria-label="Sections">', h)
        self.assertIn('<main class="main" id="top">', h)
        self.assertIn('<div class="find" role="search" hidden>', h)
        self.assertIn('<input id="find" type="search"', h)
        self.assertIn("[hidden]{display:none!important}", h)                        # a hidden tool is never shown dead
        self.assertIn('<details class="cols"><summary class="btn"', h)
        for i, c in enumerate(["Host", "Result", "State", "Status"], 1):
            self.assertIn('<label><input type="checkbox" class="c%d" checked>%s</label>' % (i, c), h)
            self.assertIn(".body:has(.cols .c%d:not(:checked)) .tw :is(th,td):nth-child(%d){display:none!important}" % (i, i), h)
        self.assertNotIn(".cols .c5", h)
        self.assertIn('<button class="btn csv" type="button" hidden', h)
        self.assertIn('<button class="call" type="button" hidden>Show all</button>', h)
        self.assertNotIn('class="filter"', h)                                      # 2 rows: no filter
        h = self.render(_report=dict(REPORT, sections=[{"title": "A", "lines": ["x"]}, {"title": "B", "columns": ["One"], "rows": [[str(i)] for i in range(9)]}]))
        self.assertNotIn('class="card rail"', h)                                    # two sections: no rail, no list in the bar
        self.assertIn('<div class="wrap">', h)
        self.assertNotIn('<details class="cols">', h)                               # one column: nothing to take off
        self.assertIn('<input class="filter" type="search" hidden placeholder="Filter these 9 rows"', h)

    def test_rail_put_away(self):
        """0.66.0: the rail's button puts it away without the script (a check box the CSS acts on): a strip with the
        button, the tables get the width and the bar lists the sections again; the script keeps the choice."""
        h = self.render()
        self.assertIn('<div class="top"><div class="lbl">Sections</div><input type="checkbox" id="rail-hide"', h)
        self.assertIn('<label class="railx" for="rail-hide"', h)
        hid = ':is(:root:has(#rail-hide:checked),:root[data-rail="hidden"])'
        self.assertIn(hid + " .wrap.has-rail{grid-template-columns:44px minmax(0,1fr)}", h)
        self.assertIn(hid + " .rail>:not(.top),", h)
        self.assertIn(hid + " .nav.railed{display:flex}", h)
        self.assertIn("localStorage.getItem('site-report-rail')==='hidden'", h)   # set before the page is drawn
        self.assertIn("localStorage.setItem('site-report-rail','hidden')", h)
        css = h[h.index("<style>"):h.index("</style>")]
        self.assertLess(css.index("  .nav.railed{display:none}"), css.index(hid + " .nav.railed{display:flex}"))  # the later rule wins
        h = self.render(_report=dict(REPORT, sections=[{"title": "A", "lines": ["x"]}, {"title": "B", "lines": ["y"]}]))
        self.assertNotIn('id="rail-hide"', h)                                       # no rail, no button

    def test_copy_beside_a_command(self):
        h = self.render(_report=dict(REPORT, sections=[{"title": "F", "columns": ["Host", "Look further"],
                                                        "rows": [["web01", "systemctl status chronyd; journalctl -u chronyd <x>"], ["web02", ""]]}]))
        self.assertIn('<td class="lk" data-l="Look further" data-v="systemctl status chronyd; journalctl -u chronyd &lt;x&gt;"><span class="cv">'
                      '<span class="cmd">systemctl status chronyd; journalctl -u chronyd &lt;x&gt;</span><button class="ccopy" type="button" hidden '
                      'title="Copy" aria-label="Copy the command">', h)
        self.assertEqual(h.count('class="ccopy"'), 1)                               # an empty cell: no button
        self.assertIn(":root:not(.js) td.lk .cmd,:root:not(.js) .code pre{-webkit-user-select:all;user-select:all}", h)

    def test_the_script_loaded_from_the_site_and_inline(self):
        """The page loads site-report.js (site_report_web makes the address relative to the page) and carries the same
        script inline, which runs once."""
        h = self.render()
        self.assertIn('<script src="site-report.js"></script>', h)
        with open(os.path.join(REPO, "roles", "site_email", "templates", "site_report.js")) as f:
            js = f.read()
        self.assertIn('<script id="site-report-js">\n' + js, h)
        self.assertIn("if(window.siteReport)return;", js)                           # runs once (1.20.0: its sort key on it)
        self.assertIn("window.siteReport={key:skey,", js)
        js.encode("ascii")                                                          # the Windows writer writes ASCII
        for t in ("{{", "{%", "{#", "</script"):
            self.assertNotIn(t, js)
        node = shutil.which("node")
        if node:
            out = subprocess.run([node, "--check", os.path.join(REPO, "roles", "site_email", "templates", "site_report.js")],
                                 capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)

class BriefLook(unittest.TestCase):
    """roles/site_email/templates/report_brief.html.j2 (0.92.0: the report website's brief look), rendered with plain Jinja."""

    def render(self, **v):
        import jinja2
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(REPO, "roles", "site_email", "templates")),
                                 autoescape=False)
        base = {"_report": dict(REPORT, status="critical", subtitle="<b>sub</b>", summary=[{"label": "Past <90%>", "value": 2, "status": "critical"},
                                                                                         {"label": "Hosts", "value": 12, "note": "read"}],
                                sections=[
                    {"title": "Start here", "status": "critical", "cards": [
                        {"status": "critical", "chip": "Critical", "meta": "Filesystem", "title": "<t>", "subject": "web01",
                         "next": "Look further:", "code": "du -xh </x>"},
                        {"status": "warning", "title": "two"}]},
                    {"title": "Filesystems", "status": "critical", "columns": ["Host", "Used", "Inodes", "Status"],
                     "rows": [["<h>", "95%", "1%", "late"], ["db01", "86.5%", "91%", "FAIL"]],
                     "row_status": ["critical", "warning"], "cell_status": [["", "critical", "", ""], ["", "warning", "critical", ""]]},
                    {"title": "Healthy (2)", "text": "a, b", "passed": ["web<1>", "web2"], "passed_checks": ["disk"]},
                    {"title": "Notes", "lines": ["one <line>"]}]),
                "_email_footer": "AAP job 9", "_page_logo_cid": "", "_page_runs": [], "_page_template": "Linux - Disk <space>",
                "report_email_header_text": "Site <automation> report"}
        base.update(v)
        return env.get_template("report_brief.html.j2").render(**base)

    def test_wrap_columns_and_titles(self):
        _wrap_columns(self)

    def test_escaped_and_self_contained(self):
        h = self.render()
        for raw in ("<b>sub</b>", "<h>", "<line>", "<automation>", "<t>", "</x>", "web<1>", "<90%>", "<space>"):
            self.assertNotIn(raw, h)
        self.assertNotRegex(h, r'<link|src="https?:|@import|url\(https?:')        # nothing from elsewhere: it opens from a share
        self.assertNotIn("oklch(", h.split("<style>")[1].split("</style>")[0].replace("oklch(0.", ""))   # hex only (the oklch in a comment)

    def test_no_poster_type(self):
        """0.94.0: the verdict a chip and the title a heading - nothing set larger than 34 px (the user: "crazy big")."""
        style = self.render().split("<style>")[1].split("</style>")[0]
        self.assertIn(".verdict{display:inline-flex;", style)
        sizes = re.findall(r"font-size:clamp\([^;]*?,\s*([\d.]+)(rem|px)\)", style) + re.findall(r"font-size:([\d.]+)(rem|px)", style)
        self.assertTrue(sizes)
        for v, unit in sizes:
            self.assertLessEqual(float(v) * (16 if unit == "rem" else 1), 34, (v, unit))
        self.assertIn(".logo{display:block;border-radius:8px;height:auto;max-height:52px;width:auto}", style)   # the logo larger

    def test_the_verdict_first(self):
        """A report with a verdict leads with it (its icon and word - a chip since 0.94.0), then its title; one that only
        informs, with its title."""
        h = self.render()
        self.assertIn('<div class="verdict c-critical"><svg class="ic"', h)
        self.assertIn("<span>Action required</span></div>\n<h1>", h)
        self.assertIn('<span class="eyebrow">Linux - Disk &lt;space&gt;</span>', h)
        i = self.render(_report=dict(REPORT, status="info", sections=[]))
        self.assertNotIn('class="verdict', i)
        self.assertIn('<h1 class="big">Snapshot cleanup: deleted 4</h1>', i)
        self.assertRegex(i, r'<span class="chip s-info( sh| c\d)?">')                # the status still said: icon and word
        self.assertIn('<div class="verdict c-ok">', self.render(_report=dict(REPORT, status="ok", sections=[])))

    def test_figures_steps_and_bars(self):
        h = self.render()
        self.assertIn('<div class="tiles">', h)
        self.assertIn('<div class="tile hot-critical">', h)
        self.assertEqual(h.count('<button class="tcopy" type="button" hidden'), 2)
        self.assertIn('<details class="sec band" id="s1" open>', h)                # Start here on its band
        self.assertIn('<details class="sec" id="s2" open>', h)
        self.assertEqual(h.count('<span class="num" aria-hidden="true"></span>'), 2)   # numbered by the CSS
        self.assertIn("counter(step,decimal-leading-zero)", h)
        self.assertIn('<pre>du -xh &lt;/x&gt;</pre><button class="copy" type="button" hidden>Copy</button>', h)
        # a column of percentages: a bar, in its cell's status (with the icon) - never its row's alone
        self.assertIn('<th class="k-num" scope="col"><button type="button">Used', h)
        self.assertIn('data-v="95%"><span class="pct"><span class="meter m-critical"><i style="width:95.0%"></i></span><span class="pv c-critical"><svg', h)
        self.assertIn('<span class="meter"><i style="width:1.0%"></i></span><span class="pv">1%</span>', h)
        self.assertIn('<span class="meter m-critical"><i style="width:91.0%"></i>', h)
        self.assertIn("<span>Fail</span>", h)                                      # a Status column: the row's status as its chip
        self.assertIn("<span>late</span>", h)                                     # ... the row's status as its chip

    def test_what_the_script_works_on(self):
        """The parts site_report.js looks for are atlas's: the one script serves both looks."""
        h = self.render()
        for part in ('id="theme"', 'id="density"', 'id="print"', 'id="find"', 'id="rail-hide"', 'class="nav railed"', 'class="card rail"',
                     'class="copy scopy"', 'class="btn csv"', '<details class="cols">', 'class="tw"', 'class="todo c-critical"', 'class="row1"',
                     '<div class="body">', '<summary><h2>', '<script id="site-report-js">', '<script src="site-report.js"></script>'):
            self.assertIn(part, h, part)
        self.assertIn("if(window.siteReport)return;", h)

    def test_soft_and_readable(self):
        """0.94.0: soft and translucent (the user: "a bit too much contrasty", "even a bit more softer") - dark in neutral
        greys (page #121212, text #e0e0e0), cards and chips translucent over the page's faint glow - and every text colour
        still 4.5:1 or better on every surface it is drawn on, the translucent layers composited as the browser does."""
        h = self.render()

        def tokens(block):
            return dict(re.findall(r"--([a-z-]+):(#[0-9a-f]{6}|rgba\([^)]*\));", block.replace("\n", "").replace(" ", "")))
        light = tokens(h.split(":root{")[1].split("}")[0])
        dark = tokens(h.split(':root[data-theme="dark"]{')[1].split("}")[0])
        self.assertEqual((dark["bg"], dark["card"], dark["ink"]), ("#121212", "rgba(255,255,255,.05)", "#e0e0e0"))

        def rgba(v):
            if v.startswith("#"):
                return [int(v[i:i + 2], 16) for i in (1, 3, 5)] + [1.0]
            return [float(x) for x in v[5:-1].split(",")]

        def over(top, under):
            return [top[i] * top[3] + under[i] * (1 - top[3]) for i in range(3)] + [1.0]

        def lum(c):
            r, g, b = [v / 255 / 12.92 if v / 255 <= 0.04045 else ((v / 255 + 0.055) / 1.055) ** 2.4 for v in c[:3]]
            return 0.2126 * r + 0.7152 * g + 0.0722 * b

        def ratio(a, b):
            hi, lo = sorted((lum(a), lum(b)), reverse=True)
            return (hi + 0.05) / (lo + 0.05)
        for name, t in (("light", light), ("dark", dark)):
            surfaces, tinted = [], []
            for page in (rgba(t["bg"]), over(rgba(t["glow"]), rgba(t["bg"]))):
                card = over(rgba(t["card"]), page)
                band = over(rgba(t["card-strong"]), page)                  # Start here's band, and a card on it
                surfaces += [page, card, band, over(rgba(t["card"]), band), rgba(t["th"]), rgba(t["card-solid"])]
                surfaces += [over(rgba(t[k]), card) for k in ("card-strong", "zebra", "hover", "code", "critical-row", "warning-row")]
                tinted.append(over(rgba(t["accent-soft"]), card))             # a chosen tab: text on it, never a chip
            for fg in ("ink", "muted", "label", "accent", "ok", "warning", "critical", "info", "unknown"):
                for bg in surfaces + tinted:
                    self.assertGreaterEqual(ratio(rgba(t[fg]), bg), 4.5, (name, fg, bg))
            for k in ("ok", "warning", "critical", "info", "unknown"):
                for bg in surfaces:
                    self.assertGreaterEqual(ratio(rgba(t[k]), over(rgba(t[k + "-bg"]), bg)), 4.5, (name, k, "chip", bg))

    def test_light_dark_print_motion(self):
        h = self.render()
        flat = h.replace("\n", "").replace(" ", "")
        self.assertIn(":root:has(#theme-light:checked){--ink:#182027;", flat)
        self.assertIn(":root:has(#theme-dark:checked){--ink:#e0e0e0;", flat)
        self.assertIn('@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){', h)
        self.assertIn("@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}", h)
        self.assertIn("@media print{", h)
        palette = h[h.index("<style>"):h.index("/* one family")]
        self.assertNotRegex(palette, r"#000\b|#000000|#fff\b|#ffffff")         # the palette: never pure black or white



class WebColumns(unittest.TestCase):
    """1.11.0, the atlas and brief looks: short headers with their full names (hover, Columns, the phone's labels), columns
    that start hidden, the choice kept in the browser, a compact table, chip text as it is (keep_case), and the page's own
    report (_page_report) over a caller's _report."""
    SEC = {"title": "vc01 - DC1 / Linux", "columns": ["VM", "Enc", "EFI", "SB", "Tools ver."],
           "column_titles": ["VM", "Encrypted", "Firmware (EFI or BIOS)", "Secure Boot", "VMware Tools version"],
           "hidden_columns": ["SB", "Tools ver."], "compact": True, "keep_case": True,
           "rows": [["web01", "no", "BIOS", "off", "12.3.5"]], "cell_status": [["", "warning", "warning", "warning", ""]]}

    def render(self, look, **v):
        import jinja2
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(REPO, "roles", "site_email", "templates")),
                                 autoescape=False)
        base = {"_report": dict(REPORT, sections=[dict(self.SEC)]), "_email_footer": "AAP job 9", "_page_logo_cid": "",
                "_page_runs": [], "report_email_header_text": "Site automation report"}
        base.update(v)
        return env.get_template("report_%s.html.j2" % look).render(**base)

    def test_titles_hidden_compact_keep_case(self):
        for look in ("atlas", "brief"):
            h = self.render(look)
            self.assertIn('scope="col" title="Encrypted"><button type="button">Enc<', h)
            self.assertIn('scope="col"><button type="button">VM<', h)                 # the same name: no title
            self.assertIn('<input type="checkbox" class="c2" checked>Encrypted</label>', h)
            self.assertIn('<input type="checkbox" class="c4">Secure Boot</label>', h)     # starts hidden, in the menu
            self.assertIn('<input type="checkbox" class="c5">VMware Tools version</label>', h)
            self.assertIn('data-l="Firmware (EFI or BIOS)"', h)                       # the phone's card label
            self.assertIn('<table class="cc">', h)
            self.assertIn("table.cc.sq td.k-nw{white-space:normal}", h)               # a hyphen breaks only when it must
            self.assertRegex(h, r'<span class="chip s-warning sh">.*?<span>BIOS</span>')  # not "Bios"
            plain = self.render(look, _report=dict(REPORT, sections=[dict(self.SEC, compact=False, keep_case=False,
                                                                          hidden_columns=[])]))
            self.assertIn("<span>Bios</span>", plain)                                  # upper case shown as a word, as before
            self.assertIn('<input type="checkbox" class="c4" checked>', plain)
            self.assertIn("<div class=\"tw\"><table>", plain)
            mine = self.render(look, _page_report=dict(REPORT, sections=[{"title": "The website copy", "lines": ["x"]}]))
            self.assertIn("The website copy", mine)                                    # site_email's own, over a caller's _report
            self.assertNotIn(">Enc<", mine)

    def test_the_choice_kept(self):
        js = open(os.path.join(REPO, "roles", "site_email", "templates", "site_report.js")).read()
        self.assertIn("'site-report-cols:'", js)                                       # a key per set of columns
        self.assertIn("localStorage.setItem(k,JSON.stringify(off))", js)
        self.assertIn("if(o!==d&&colsig(o)===k)colput(o,off)", js)                     # every table with those columns follows
        self.assertIn("(t.classList.contains('cc')?['sq']:[]).concat(", js)              # a compact table's sq comes first (1.21.0)
        js.encode("ascii")


def _browser():
    for b in ("google-chrome", "chromium", "chromium-browser"):
        if shutil.which(b):
            return shutil.which(b)
    return None


SCRIPT = os.path.join(REPO, "roles", "site_email", "templates", "site_report.js")
# the script's sort key in node: no page, so the page's parts wait for a DOMContentLoaded that never comes
NODE_STUB = ("var cl={add:function(){}};global.window={};global.document={readyState:'loading',addEventListener:function(){},"
             "documentElement:{classList:cl,setAttribute:function(){},getAttribute:function(){return null}}};"
             "global.localStorage={getItem:function(){throw new Error('none')}};require(process.argv[1]);var S=window.siteReport;")


class Sorting(unittest.TestCase):
    """1.20.0, the atlas and brief looks: a column's sort key (one number; IPv4 addresses by their octets, versions and
    names with a number in them naturally; numbers before text; an empty cell last; data-v before the cell's text), the
    arrows as characters (a page under Trusted Types sorts), a first click on a column already in order, the whole
    header cell, and no arrows or pointer before the script runs."""

    def order(self, values, asc=True):
        node = shutil.which("node")
        if not node:
            self.skipTest("no node here")
        out = subprocess.run([node, "-e", NODE_STUB + "var v=JSON.parse(process.argv[2]),a=process.argv[3]==='1';"
                              "console.log(JSON.stringify(v.slice().sort(function(x,y){return S.compare(x,y,a)})))",
                              SCRIPT, json.dumps(values), "1" if asc else "0"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_ip_addresses(self):
        self.assertEqual(self.order(["192.0.2.45", "192.0.2.5", "198.51.100.7", "192.0.2.100", "203.0.113.9", "192.0.2.10"]),
                         ["192.0.2.5", "192.0.2.10", "192.0.2.45", "192.0.2.100", "198.51.100.7", "203.0.113.9"])
        self.assertEqual(self.order(["192.0.2.45", "192.0.2.5", "198.51.100.7"], asc=False),
                         ["198.51.100.7", "192.0.2.45", "192.0.2.5"])

    def test_versions_and_names(self):
        self.assertEqual(self.order(["8.0.10", "8.0.2", "7.0.3", "8.0", "8.0.2-1", "12.3.5", "11.3.5"]),
                         ["7.0.3", "8.0", "8.0.2", "8.0.2-1", "8.0.10", "11.3.5", "12.3.5"])
        self.assertEqual(self.order(["vmx-21", "vmx-9", "vmx-13", "vmx-10"]), ["vmx-9", "vmx-10", "vmx-13", "vmx-21"])
        self.assertEqual(self.order(["host10", "Host2", "host1", "app01"]), ["app01", "host1", "Host2", "host10"])
        self.assertEqual(self.order(["ESXi 8.0.2", "ESXi 7.0 U3", "ESXi 8.0"]), ["ESXi 7.0 U3", "ESXi 8.0", "ESXi 8.0.2"])
        self.assertEqual(self.order(["1.5 GB", "1.25 GB", "10 GB"]), ["1.25 GB", "1.5 GB", "10 GB"])   # a decimal stays one

    def test_numbers(self):
        self.assertEqual(self.order(["45%", "4.5 %", "100%", "-3%", "0%"]), ["-3%", "0%", "4.5 %", "45%", "100%"])
        self.assertEqual(self.order(["1,234", "999", "12,345.5", "-7", "0.5", "-0.25", "+3"]),
                         ["-7", "-0.25", "0.5", "+3", "999", "1,234", "12,345.5"])
        self.assertEqual(self.order(["10", "9", "100"], asc=False), ["100", "10", "9"])

    def test_mixed_and_empty(self):
        """Numbers before text, as before 1.20.0; an empty cell last in either direction."""
        vals = ["abc", "", "12", "  ", "2", "Bcd", "192.0.2.1"]
        self.assertEqual(self.order(vals), ["2", "12", "192.0.2.1", "abc", "Bcd", "", "  "])
        self.assertEqual(self.order(vals, asc=False), ["Bcd", "abc", "192.0.2.1", "12", "2", "", "  "])

    def test_a_lone_dash_is_empty(self):
        """A report writes a missing value as a dash (the VM tables: "-"): last in either direction, like an empty cell.
        A dash with more to it is text."""
        vals = ["no", "-", "yes", " \u2013 ", "\u2014", "", "-1", "a-b"]
        self.assertEqual(self.order(vals), ["-1", "a-b", "no", "yes", "-", " \u2013 ", "\u2014", ""])
        self.assertEqual(self.order(vals, asc=False), ["yes", "no", "a-b", "-1", "-", " \u2013 ", "\u2014", ""])

    def test_data_v_first(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("no node here")
        out = subprocess.run([node, "-e", NODE_STUB + "function c(v,t){return {getAttribute:function(){return v},textContent:t}}"
                              "console.log(JSON.stringify([S.cellValue(c('12','twelve')),S.cellValue(c(null,' x ')),"
                              "S.cellValue(c('','y')),S.cellValue(null),S.key(S.cellValue(c('3','-'))),S.key(S.cellValue(c(null,'-')))]))",
                              SCRIPT], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), ["12", " x ", "y", "", [3], None])      # data-v over a shown dash

    def test_no_html_sinks(self):
        with open(SCRIPT) as f:
            js = f.read()
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
            self.assertNotIn(sink, js)
        self.assertIn("a.textContent='\\u2195'", js)
        js.encode("ascii")
        for look in ("atlas", "brief"):
            h = WebColumns().render(look)
            self.assertIn(":root:not(.js) th .sort{display:none}\n.js th{cursor:pointer}", h)
            self.assertNotRegex(h, r"th button\{[^}]*cursor:pointer")

    HARNESS = """<script>
window.addEventListener('load',function(){var R={errors:E},root=document.documentElement;
  try{document.createElement('div').innerHTML='<b>x</b>';R.tt=false}catch(e){R.tt=true}
  var tb=document.querySelector('.tw table'),ths=[].slice.call(tb.tHead.rows[0].cells);
  function col(i){return [].map.call(tb.tBodies[0].rows,function(r){return r.cells[i].textContent.trim()})}
  function st(i){var th=ths[i];return [th.getAttribute('aria-sort'),th.querySelector('.sort').textContent]}
  var s=ths[0].querySelector('.sort');R.js=[getComputedStyle(s).display,getComputedStyle(ths[0]).cursor];
  root.classList.remove('js');R.nojs=[getComputedStyle(s).display,getComputedStyle(ths[0]).cursor];root.classList.add('js');
  ths[0].querySelector('button').click();R.vm1=[st(0),col(0)];
  ths[0].querySelector('button').click();R.vm2=[st(0),col(0)];
  ths[1].querySelector('button').click();R.ip=[st(1),st(0),col(1)];
  ths[5].querySelector('button').click();R.tie=[st(5),col(5),col(0)];
  var r=ths[2].getBoundingClientRect(),el=document.elementFromPoint(r.left+3,r.top+3);R.corner0=el===ths[2];
  if(ths[2].contains(el))el.click();R.corner=[st(2),col(2)];
  ths[3].dispatchEvent(new MouseEvent('click',{bubbles:true}));R.cell=[st(3),col(3)];
  ths[3].querySelector('button').click();R.cell2=[st(3),col(3)];
  [].forEach.call(tb.tBodies[0].rows,function(row){var c=row.cells[4];c.setAttribute('data-v',{z:'1',y:'3',x:'2'}[c.textContent.trim()])});
  ths[4].querySelector('button').click();R.datav=[st(4),col(4)];
  document.getElementById('out').textContent=JSON.stringify(R)});
</script><pre id="out"></pre>"""

    def test_in_a_browser(self):
        """Both looks, light and dark, under require-trusted-types-for 'script': the policy is in force (an innerHTML
        throws), and the headers sort."""
        b = _browser()
        if not b:
            self.skipTest("no headless browser here")
        more = ["Red Hat Enterprise Linux 9 (64-bit)", "esx01.example.mil", "cluster1-prod-a", "datastore-ssd-01"]
        # vTPM in value order only after IP is sorted (db01, app01, web01), not in the page's own order; Used's "-" is how
        # the VM tables write a missing value
        sec = {"title": "VMs", "columns": ["VM", "IP address", "HW", "Used", "Note", "vTPM", "Guest operating system",
                                           "ESXi host", "Cluster", "Datastore"],  # at 900 px some headers wrap, HW's does not
               "rows": [["app01", "192.0.2.45", "vmx-21", "45%", "z", "no"] + more, ["db01", "192.0.2.5", "vmx-9", "4.5%", "y", "no"] + more,
                        ["web01", "198.51.100.7", "vmx-13", "-", "x", "yes"] + more]}
        for look in ("atlas", "brief"):
            for theme in ("light", "dark"):
                h = WebColumns().render(look, _report=dict(REPORT, sections=[sec]))
                h = h.replace("<head>", "<head>\n<meta http-equiv=\"Content-Security-Policy\" content=\"require-trusted-types-for 'script'\">", 1)
                h = h.replace("<html", '<html data-theme="%s"' % theme, 1)
                h = h.replace("</body>", "<script>var E=[];window.addEventListener('error',function(e){E.push(String(e.message))})"
                              "</script>" + self.HARNESS + "</body>", 1)
                with tempfile.TemporaryDirectory() as d:
                    p = os.path.join(d, "page.html")
                    with open(p, "w") as f:
                        f.write(h)
                    out = subprocess.run([b, "--headless=new", "--disable-gpu", "--no-sandbox", "--window-size=900,800",
                                          "--virtual-time-budget=6000", "--dump-dom", "file://" + p],
                                         capture_output=True, text=True, timeout=120)
                m = re.search(r'<pre id="out">(.*?)</pre>', out.stdout, re.S)
                self.assertTrue(m and m.group(1), (look, theme, out.stderr[-800:]))
                import html as htmllib
                r = json.loads(htmllib.unescape(m.group(1)))
                k = (look, theme)
                self.assertTrue(r["tt"], k)                                            # Trusted Types in force
                self.assertEqual(r["errors"], [], k)
                self.assertNotEqual(r["js"][0], "none", k)                             # the arrows, and the pointer
                self.assertEqual(r["js"][1], "pointer", k)
                self.assertEqual(r["nojs"], ["none", "auto"], k)                       # no arrows, no pointer, without script
                # VM came in order: the first click sorts it the other way, the second back
                self.assertEqual(r["vm1"], [["descending", "\u25bc"], ["web01", "db01", "app01"]], k)
                self.assertEqual(r["vm2"], [["ascending", "\u25b2"], ["app01", "db01", "web01"]], k)
                self.assertEqual(r["ip"], [["ascending", "\u25b2"], [None, "\u2195"], ["192.0.2.5", "192.0.2.45", "198.51.100.7"]], k)
                # HW's header is shorter than its row (the long one wraps): its top corner is the cell, not the button
                self.assertTrue(r["corner0"], k)
                self.assertEqual(r["corner"], [["ascending", "\u25b2"], ["vmx-9", "vmx-13", "vmx-21"]], k)
                self.assertEqual(r["cell"], [["ascending", "\u25b2"], ["4.5%", "45%", "-"]], k)         # a click on a cell; a dash last
                self.assertEqual(r["cell2"], [["descending", "\u25bc"], ["45%", "4.5%", "-"]], k)       # ... either way
                # ties in value order after another column's sort: the first click sorts the other way, and it shows
                self.assertEqual(r["tie"], [["descending", "\u25bc"], ["yes", "no", "no"], ["web01", "app01", "db01"]], k)   # ties: the page's order
                self.assertEqual(r["datav"], [["ascending", "\u25b2"], ["z", "x", "y"]], k)             # data-v 1, 2, 3


if __name__ == "__main__":
    unittest.main()
