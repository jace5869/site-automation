# -*- coding: utf-8 -*-
"""1.139.0: Unit and template tests for the server-first report view (steps 1-8 of plan-reports-by-server.md):
- plugins/filter/server_view_filters.py: normalize_severity (all 5 vocabularies), host_counts,
  fleet_summary_lines, missing_map / missing_lines, server_view (3-host capped + not-reached case,
  low never amber, hint Copy vs fix prose, timezone formatting, row_tags data.sev).
- plugins/filter/site_filters.py: findings_report with {host: reason} missing + legacy list form,
  report_servers_view switch (on by default), report_tidy keeping server_view.
- roles/site_email/templates/: report_macros.html.j2 shared import across atlas/brief/ops,
  report_servers.html.j2 + report_servers.css.j2 rendering & escaping & <= 34px in brief,
  sidebar counts & narrow-screen select, dashboard rendered in atlas/brief/ops,
  site_report.js / report_pop.js ASCII, no HTML sinks, no Jinja markers, double-run guard.
Placeholders only: SITEA, corp.example.mil, 192.0.2.x.
"""
import os
import re
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))

import server_view_filters as svf
import site_filters as sf


def _render_look(look, report, **extra):
    import jinja2
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(os.path.join(REPO, "roles", "site_email", "templates")),
        autoescape=False,
    )
    env.filters.update(
        report_cell_html=sf.report_cell_html,
        report_hidden_columns=sf.report_hidden_columns,
        report_sparkline=sf.report_sparkline,
        report_runway_bar=sf.report_runway_bar,
        safe_link=sf.safe_link,
        safe_ref=sf.safe_ref,
        safe_site_url=sf.safe_site_url,
        safe_data=sf.safe_data,
        report_tidy=sf.report_tidy,
        report_head=sf.report_head,
    )
    ctx = {
        "_report": report,
        "_email_footer": "AAP job 42 (SITEA health check)",
        "_page_logo_cid": "",
        "_page_runs": [],
        "_page_template": "SITEA - Linux health check",
        "report_email_header_text": "SITEA automation report",
    }
    ctx.update(extra)
    return env.get_template("report_%s.html.j2" % look).render(**ctx)


class NormalizeSeverityTests(unittest.TestCase):
    """1.139.0: One severity normalizer across all five vocabularies."""

    def test_five_vocabularies(self):
        cases = [
            # 1. Common four + ok / unknown
            ("critical", "critical"),
            ("warning", "warning"),
            ("low", "low"),
            ("info", "info"),
            ("ok", "ok"),
            ("unknown", "unknown"),
            # 2. STIG (high / medium / low)
            ("high", "critical"),
            ("HIGH", "critical"),
            ("medium", "warning"),
            ("Medium", "warning"),
            ("low", "low"),
            # 3. CAT I / II / III
            ("CAT I", "critical"),
            ("cat 1", "critical"),
            ("CAT1", "critical"),
            ("CAT II", "warning"),
            ("cat-2", "warning"),
            ("CAT III", "low"),
            ("cat_iii", "low"),
            # 4. aap_health (PASS / WARN / FAIL / INFO / SKIP)
            ("FAIL", "critical"),
            ("WARN", "warning"),
            ("INFO", "info"),
            ("PASS", "ok"),
            ("SKIP", "unknown"),
            # 5. UCS (critical / major / minor)
            ("major", "warning"),
            ("minor", "low"),
            ("cleared", "ok"),
        ]
        for raw, want in cases:
            self.assertEqual(svf.normalize_severity(raw), want, raw)

    def test_defaults_and_whitespace(self):
        self.assertEqual(svf.normalize_severity(""), "warning")
        self.assertEqual(svf.normalize_severity(None), "warning")
        self.assertEqual(svf.normalize_severity("mystery", default="info"), "info")
        self.assertEqual(svf.normalize_severity("  CAT   I  "), "critical")


class ServerViewFilterTests(unittest.TestCase):
    """1.139.0: server_view(by_host, health, missing, capped, opts) and per-host counts."""

    def setUp(self):
        # Required 3-host scenario: one host with findings + capped, one healthy, one not reached with reason
        self.by_host = {
            "srv01.corp.example.mil": [
                {
                    "check": "disk",
                    "id": "disk:/var",
                    "severity": "critical",
                    "summary": "/var is 96% full (critical at 95%)",
                    "note": "Filesystem /dev/mapper/vg-var mounted on /var",
                    "fix": "Clean old journal logs or grow /var.",
                    "hint": "df -h /var && du -xh --max-depth=1 /var | sort -h",
                },
                {
                    "check": "fapolicyd",
                    "id": "fapolicyd:/opt/sitea/bin/helper",
                    "severity": "low",
                    "summary": "fapolicyd permissive audit entry for helper",
                    "fix": "Review the binary before enforcing.",
                    "hint": "",
                },
            ],
            "srv02.corp.example.mil": [],
        }
        self.health = {
            "srv01.corp.example.mil": {
                "status": "findings",
                "checks": ["disk", "fapolicyd", "services"],
                "time": "2026-11-27T09:45:00Z",
                "ip": "192.0.2.1",
            },
            "srv02.corp.example.mil": {
                "status": "healthy",
                "checks": ["disk", "fapolicyd", "services"],
                "time": "2026-11-27T09:45:00Z",
                "ip": "192.0.2.2",
            },
        }
        self.missing = {"srv03.corp.example.mil": "SSH connection timed out"}
        self.capped = {"srv01.corp.example.mil": {"fapolicyd_deny": 14}}

    def test_three_hosts_capped_and_not_reached(self):
        sec = svf.server_view(self.by_host, self.health, self.missing, self.capped)
        self.assertTrue(sec["server_view"])
        self.assertEqual(sec["status"], "critical")
        self.assertEqual(sec["title"], "Servers")

        # Worst-first ordering: critical -> not reached (unknown) -> clear (ok)
        hosts = [s["host"] for s in sec["servers"]]
        self.assertEqual(
            hosts,
            ["srv01.corp.example.mil", "srv03.corp.example.mil", "srv02.corp.example.mil"],
        )

        # Capped host explicitly states what was not listed
        s1 = sec["servers"][0]
        self.assertEqual(s1["status"], "critical")
        self.assertEqual(s1["ip"], "192.0.2.1")
        self.assertEqual(s1["counts"], {"critical": 1, "warning": 0, "low": 1, "info": 0, "total": 2})
        self.assertIn("and 14 more of fapolicyd_deny, not listed", s1["capped_text"])
        self.assertIn("+14 more, not listed", s1["worst"])
        self.assertEqual(s1["checked"], "2026-11-27 09:45 UTC")
        # hint vs fix kept distinct
        self.assertEqual(s1["findings"][0]["fix"], "Clean old journal logs or grow /var.")
        self.assertIn("df -h /var", s1["findings"][0]["hint"])
        self.assertEqual(s1["findings"][1]["hint"], "")
        self.assertEqual(s1["findings"][1]["fix"], "Review the binary before enforcing.")

        # Not-reached host carries reason and status unknown
        s3 = sec["servers"][1]
        self.assertTrue(s3["missing"])
        self.assertEqual(s3["status"], "unknown")
        self.assertEqual(s3["status_label"], "Not reached")
        self.assertEqual(s3["reason"], "SSH connection timed out")
        self.assertEqual(s3["worst"], "SSH connection timed out")

        # Healthy host is ok
        s2 = sec["servers"][2]
        self.assertFalse(s2["missing"])
        self.assertEqual(s2["status"], "ok")
        self.assertEqual(s2["worst"], "No findings")

        # row_tags emit data.sev for report_pop.js sevChips()
        sevs = [t["data"]["sev"] for t in sec["row_tags"]]
        self.assertEqual(sevs, ["critical", "unknown", "ok"])

        # Tiles match counts
        self.assertEqual(len(sec["tiles"]), 4)
        self.assertEqual(sec["counts"]["hosts"], 2)
        self.assertEqual(sec["counts"]["missing"], 1)
        self.assertEqual(sec["counts"]["critical"], 1)
        self.assertEqual(sec["counts"]["low"], 1)
        self.assertEqual(sec["counts"]["capped"], 14)

    def test_low_never_makes_host_amber(self):
        by_host = {
            "srv10.corp.example.mil": [
                {"check": "patching", "id": "p1", "severity": "low", "summary": "Due in 5 days"},
                {"check": "stig", "id": "s1", "severity": "CAT III", "summary": "Minor setting"},
            ],
            "srv11.corp.example.mil": [],
        }
        sec = svf.server_view(by_host)
        s10 = sec["servers"][0]
        self.assertEqual(s10["host"], "srv10.corp.example.mil")
        self.assertEqual(s10["status"], "low")
        self.assertNotEqual(s10["status"], "warning")
        self.assertEqual(s10["counts"]["low"], 2)
        self.assertEqual(s10["counts"]["warning"], 0)
        self.assertEqual(sec["status"], "low")

        hc = svf.host_counts(by_host)
        self.assertFalse(hc["hosts"]["srv10.corp.example.mil"]["actionable"])
        self.assertEqual(hc["totals"]["warning"], 0)
        self.assertEqual(hc["totals"]["low"], 2)

    def test_missing_accepts_legacy_list_and_dict(self):
        sec_list = svf.server_view({"srv01.corp.example.mil": []}, missing=["srv02.corp.example.mil"])
        self.assertEqual(sec_list["servers"][0]["host"], "srv02.corp.example.mil")
        self.assertEqual(sec_list["servers"][0]["status"], "unknown")
        self.assertEqual(sec_list["servers"][0]["reason"], "")
        self.assertEqual(svf.missing_lines(["srv02.corp.example.mil"]), ["srv02.corp.example.mil"])
        self.assertEqual(
            svf.missing_lines({"srv02.corp.example.mil": "connection refused"}),
            ["srv02.corp.example.mil: connection refused"],
        )

    def test_timezone_formatting(self):
        sec_utc = svf.server_view(self.by_host, self.health, opts={"zone": ""})
        self.assertEqual(sec_utc["columns"][8], "Checked (UTC)")
        self.assertEqual(sec_utc["servers"][0]["checked"], "2026-11-27 09:45 UTC")

        sec_cet = svf.server_view(self.by_host, self.health, opts={"zone": "Europe/Berlin"})
        self.assertEqual(sec_cet["columns"][8], "Checked")
        self.assertEqual(sec_cet["servers"][0]["checked"], "2026-11-27 10:45 CET")

    def test_fleet_summary_lines(self):
        lines = svf.fleet_summary_lines(
            self.by_host,
            {"srv03.corp.example.mil": "ssh timed out", "srv04.corp.example.mil": "skipped", "srv05.corp.example.mil": ""},
        )
        self.assertIn("1 healthy, 1 with findings, 3 did not report", lines[0])
        self.assertIn("srv01.corp.example.mil: 1 critical, 0 warning, 1 low (disk, fapolicyd)", lines)
        self.assertIn("srv03.corp.example.mil: NOT REACHED - ssh timed out", lines)
        self.assertIn("srv04.corp.example.mil: skipped", lines)
        self.assertIn(
            "srv05.corp.example.mil: NO REPORT (unreachable, or the job failed on it before the checks ran)",
            lines,
        )


class FindingsReportIntegrationTests(unittest.TestCase):
    """1.139.0: findings_report missing reasons, single count source, and report_servers_view switch."""

    def test_missing_reason_and_legacy_list(self):
        by_host = {"srv01.corp.example.mil": []}
        rep_dict = sf.findings_report(by_host, "SITEA health", missing={"srv03.corp.example.mil": "WinRM refused"})
        dnr = [s for s in rep_dict["sections"] if s["title"] == "Did not report"][0]
        self.assertEqual(dnr["lines"], ["srv03.corp.example.mil: WinRM refused"])

        rep_list = sf.findings_report(by_host, "SITEA health", missing=["srv03.corp.example.mil"])
        dnr_list = [s for s in rep_list["sections"] if s["title"] == "Did not report"][0]
        self.assertEqual(dnr_list["lines"], ["srv03.corp.example.mil"])

    def test_servers_view_off_by_default_and_opt_in_agrees_on_counts(self):
        by_host = {
            "srv01.corp.example.mil": [
                {"check": "disk", "id": "d1", "severity": "critical", "summary": "/var 96%", "hint": "df -h /var"},
                {"check": "time", "id": "t1", "severity": "warning", "summary": "clock skew", "fix": "Check chrony."},
            ],
            "srv02.corp.example.mil": [],
        }
        missing = {"srv03.corp.example.mil": "no route to host"}

        rep_off = sf.findings_report(by_host, "SITEA health", missing=missing)
        self.assertFalse(any(s.get("server_view") for s in rep_off["sections"]))

        rep_on = sf.findings_report(
            by_host,
            "SITEA health",
            missing=missing,
            servers={"enabled": True, "capped": {"srv01.corp.example.mil": {"disk": 3}}},
        )
        svs = [s for s in rep_on["sections"] if s.get("server_view")]
        self.assertEqual(len(svs), 1)
        # Flat Findings table stays present below for the first pass
        self.assertTrue(any(s.get("title") == "Findings" for s in rep_on["sections"]))
        # Counts agree between summary and server_view
        crit_box = [b for b in rep_on["summary"] if b["label"] == "Critical"][0]
        warn_box = [b for b in rep_on["summary"] if b["label"] == "Warning"][0]
        self.assertEqual(crit_box["value"], svs[0]["counts"]["critical"])
        self.assertEqual(warn_box["value"], svs[0]["counts"]["warning"])

        # report_tidy preserves server_view even when all hosts are healthy
        rep_ok = sf.findings_report({"srv01.corp.example.mil": []}, "SITEA health", servers={"enabled": True})
        tidied = sf.report_tidy(rep_ok)
        self.assertTrue(any(s.get("server_view") for s in tidied["sections"]))


class TemplateRenderingTests(unittest.TestCase):
    """1.139.0: Templates (atlas, brief, ops) render server_view, macros, sidebar counts/select, and dashboard."""

    def _sample_report(self):
        by_host = {
            "srv01.corp.example.mil": [
                {
                    "check": "disk",
                    "id": "disk:/var",
                    "severity": "critical",
                    "summary": "/var is 96% full <script>alert(1)</script>",
                    "fix": "Free disk space on /var <b>now</b>.",
                    "hint": "df -h /var && echo 'safe'",
                },
                {
                    "check": "accounts",
                    "id": "acct:root",
                    "severity": "low",
                    "summary": "Password policy note",
                    "fix": "Document exception in ticket.",
                    "hint": "",
                },
            ],
            "srv02.corp.example.mil": [],
        }
        return sf.findings_report(
            by_host,
            "SITEA health check",
            missing={"srv03.corp.example.mil": "SSH timed out <bad>"},
            checks=["disk", "accounts"],
            servers={
                "enabled": True,
                "health": {
                    "srv01.corp.example.mil": {"time": "2026-11-27T09:45:00Z", "ip": "192.0.2.1", "checks": ["disk", "accounts"]},
                    "srv02.corp.example.mil": {"time": "2026-11-27T09:45:00Z", "ip": "192.0.2.2", "checks": ["disk", "accounts"]},
                },
                "capped": {"srv01.corp.example.mil": {"fapolicyd_deny": 14}},
            },
        )

    def test_server_view_in_all_three_web_looks(self):
        rep = self._sample_report()
        for look in ("atlas", "brief", "ops"):
            html = _render_look(look, rep)
            # Server view structure & controls
            self.assertIn('class="srv-glance"', html, look)
            self.assertIn('class="srv-tiles"', html, look)
            self.assertIn('class="srv-meter"', html, look)
            self.assertIn('class="xt srv-tbl"', html, look)
            self.assertIn('class="btn srv-exp" type="button" hidden', html, look)
            self.assertIn('class="btn srv-col" type="button" hidden', html, look)
            # Severity data attributes wake up report_pop.js sevChips()
            self.assertIn('data-sev="critical"', html, look)
            self.assertIn('data-sev="unknown"', html, look)
            self.assertIn('data-sev="ok"', html, look)
            # Row expander + no-script details + capped notice
            self.assertIn('class="xb" type="button" aria-expanded="false"', html, look)
            self.assertIn('<!--defer-detail--><tr class="xr" hidden>', html, look)
            self.assertIn('<details class="srv-det">', html, look)
            self.assertIn("and 14 more of fapolicyd_deny, not listed", html, look)
            # hint gets Copy; fix-only finding gets prose "What to do:" and NO Copy button
            self.assertIn('<div class="code"><pre><code>df -h /var &amp;&amp; echo &#39;safe&#39;</code></pre><button class="copy" type="button" hidden>Copy</button></div>', html, look)
            acct_block = html.split("Password policy note", 1)[1].split("</article>", 1)[0]
            self.assertIn("<b>What to do:</b> Document exception in ticket.", acct_block, look)
            self.assertNotIn('class="copy"', acct_block, look)
            # Escaping
            self.assertNotIn("<script>alert(1)</script>", html, look)
            self.assertNotIn("<b>now</b>", html, look)
            self.assertNotIn("<bad>", html, look)
            # Sidebar counts + narrow-screen select
            self.assertIn('<select class="rail-sel" id="rail-jump"', html, look)
            self.assertIn('<span class="n">3</span>', html, look)

    def test_brief_stays_under_34px_with_server_view(self):
        html = _render_look("brief", self._sample_report())
        style = html.split("<style>", 1)[1].split("</style>", 1)[0]
        sizes = re.findall(r"font-size:clamp\([^;]*?,\s*([\d.]+)(rem|px)\)", style) + re.findall(r"font-size:([\d.]+)(rem|px)", style)
        for v, unit in sizes:
            self.assertLessEqual(float(v) * (16 if unit == "rem" else 1), 34, (v, unit))

    def test_dashboard_rendered_in_atlas_brief_and_ops(self):
        dash = {
            "facts": {},
            "panels": [
                {
                    "t": "tiles",
                    "tiles": [{"t": "fixed", "label": "Hosts", "value": 3, "note": "SITEA", "status": "ok"}],
                    "view": {"tiles": [{"v": 3, "notby": []}]},
                }
            ],
        }
        doc = {
            "title": "SITEA scorecard",
            "status": "ok",
            "summary": [{"label": "Hosts", "value": 3, "status": "ok"}],
            "dashboard": dash,
            "sections": [
                {"title": "At a glance", "status": "ok", "glance": [{"status": "ok", "label": "OK", "name": "SITEA", "text": "3 hosts"}]},
            ],
        }
        for look in ("atlas", "brief", "ops"):
            html = _render_look(look, doc)
            self.assertIn('<div class="dash" data-dash>', html, look)
            self.assertNotIn('<div class="tiles', html, look)
            if look == "brief":
                style = html.split("<style>", 1)[1].split("</style>", 1)[0]
                sizes = re.findall(r"font-size:clamp\([^;]*?,\s*([\d.]+)(rem|px)\)", style) + re.findall(r"font-size:([\d.]+)(rem|px)", style)
                for v, unit in sizes:
                    self.assertLessEqual(float(v) * (16 if unit == "rem" else 1), 34, (v, unit))


class GateReviewTests(unittest.TestCase):
    """1.139.0 gate regressions for server reports and the email headline."""

    def test_unread_marker_preserves_api_finding_in_either_order(self):
        api = {"host": "SITEA", "id": "api", "summary": "API unavailable", "severity": "critical"}
        marker = {"host": "SITEA", "id": "stigman:host:SITEA", "summary": "Host check unread"}
        for findings in ([api, marker], [marker, api]):
            sec = svf.server_view({"localhost": findings}, opts={"group_by_finding_host": True})
            row, = sec["servers"]
            self.assertEqual((row["status"], row["findings"][0]["id"]), ("critical", "api"))
            self.assertEqual((row["reason"], sec["counts"]["unchecked"]), ("Host check unread", 1))
            self.assertEqual(sec["tiles"][1]["value"], 1)

    def test_stig_rules_and_other_checks_share_headline(self):
        results = [{"host": "SITEA", "scanned": True, "drift": [{"key": "rule", "severity": "medium"}]}]
        raw = {"sitea": [{"check": "stig", "id": "stig:drift", "severity": "warning"},
                         {"check": "disk", "severity": "critical"}], "sitea-other": []}
        rep = sf.findings_report(raw, "SITEA", servers={"stig_results": results})
        rows = rep["sections"][0]["servers"]
        self.assertEqual([r["host"] for r in rows], ["SITEA", "sitea-other"])
        self.assertEqual({f["check"] for f in rows[0]["findings"]}, {"stig", "disk"})
        self.assertEqual(rep["head_counts"], [[1, "critical", "critical"], [1, "warning", "warning"]])
        self.assertEqual(rep["status"], "critical")

    def test_only_portal_buttons_render(self):
        for scheme in ("http", "https"):
            base = scheme + "://portal.corp.example.mil/portal"
            good = [base + "/actions/check", "/portal/actions/check"]
            bad = ["javascript:void(0)", "data:text/plain,SITEA", "//other.corp.example.mil/portal",
                   base.replace("portal.", "other."), base + "-other", "/portal/%2e%2e/other"]
            buttons = [{"label": "button%d" % i, "url": url} for i, url in enumerate(good + bad)]
            rep = sf.findings_report({"SITEA": [{"buttons": buttons}]}, "SITEA", servers={})
            html = _render_look("atlas", rep, report_portal_url=base)
            for url in good:
                self.assertIn('href="%s"' % url, html)
            for i in range(len(good), len(buttons)):
                self.assertNotIn(">button%d<" % i, html)

    def test_denial_reader_inactive_service(self):
        sec = svf.fapolicyd_server_view([{"_fr": {"active": "inactive"}}], ["SITEA"])
        self.assertEqual(sec["servers"][0]["status"], "critical")

    def test_certificate_uses_worse_of_days_and_status(self):
        for days, status in ((2, "expiring"), (90, "expired")):
            sec = svf.cert_host_rollup([{"host": "SITEA", "days": days, "status": status}])
            self.assertEqual(sec["rollup_hosts"][0]["status"], "critical")

    def test_only_failed_check_is_not_clear(self):
        health = {"SITEA": {"checks": ["disk"], "checks_failed": ["disk"]}}
        sec = svf.server_view({"SITEA": []}, health)
        self.assertEqual(sec["servers"][0]["checks"], [])
        self.assertEqual(sec["status"], "unknown")
        self.assertEqual((sec["counts"]["unchecked"], sec["counts"]["clear"]), (1, 0))

    def test_cmd_single_quotes_do_not_protect_redirection(self):
        for hint in ("reg query SITEA '>' result", "echo '>' result"):
            sec = svf.server_view({"SITEA": [{"hint": hint}]})
            f = sec["servers"][0]["findings"][0]
            self.assertEqual(f["hint"], "")
            self.assertIn(hint, f["evidence"])

    def test_info_only_email_counts(self):
        rep = sf.findings_report({"SITEA": [{"severity": "info"}]}, "SITEA")
        self.assertEqual(rep["head_counts"], [[1, "info", "info"]])


class ScriptMacrosAndDefaultsTests(unittest.TestCase):
    """1.139.0: JS hygiene, macro deduplication, and default-on setting."""

    def test_scripts_ascii_safe_and_idempotent(self):
        for name, guard in (
            ("site_report.js", "if(window.siteReport)return;"),
            ("report_pop.js", "if(window.reportPop)return;"),
        ):
            path = os.path.join(REPO, "roles", "site_email", "templates", name)
            with open(path, "r", encoding="utf-8") as f:
                js = f.read()
            js.encode("ascii")
            self.assertIn(guard, js, name)
            for tok in ("{{", "{%", "{#", "</script"):
                self.assertNotIn(tok, js, (name, tok))
            for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
                self.assertNotIn(sink, js, (name, sink))

    def test_macros_deduplicated(self):
        tdir = os.path.join(REPO, "roles", "site_email", "templates")
        with open(os.path.join(tdir, "report_macros.html.j2"), "r", encoding="utf-8") as f:
            macros = f.read()
        for name in ("icon", "copyicons", "nbw", "keep", "dw", "chip", "breakable", "xmini"):
            self.assertIn("macro %s(" % name, macros)
        for look in ("report_atlas.html.j2", "report_brief.html.j2", "report_ops.html.j2"):
            with open(os.path.join(tdir, look), "r", encoding="utf-8") as f:
                src = f.read()
            self.assertIn("from 'report_macros.html.j2' import icon, copyicons, nbw, keep, dw, chip, breakable, xmini with context", src, look)
            for name in ("icon", "copyicons", "nbw", "keep", "dw", "chip", "breakable", "xmini"):
                self.assertNotIn("macro %s(" % name, src, (look, name))

    def test_setting_on_by_default(self):
        path = os.path.join(REPO, "roles", "site_findings", "defaults", "main.yml")
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertRegex(text, r"(?m)^report_servers_view:\s*true\b")


if __name__ == "__main__":
    unittest.main()
