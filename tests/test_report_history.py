#!/usr/bin/env python3
"""The reports' look (roles/site_email): the strip of past runs - report_headline, report_runs, the plain-text
line (plugins/filter/site_filters.py), site_report_history against the fake AAP (tests/servicenow/aapmock.py),
in this process - the logo's size (image_size), and the command and portal looks' templates rendered with Jinja.
    python3 tests/test_report_history.py"""
import importlib.util
import os
import struct
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


if __name__ == "__main__":
    unittest.main()
