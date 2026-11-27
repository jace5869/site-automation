# -*- coding: utf-8 -*-
"""1.138.0: Unit and template tests for steps 9-16 of plan-reports-by-server.md, all 24 items of
vdi-design-check.txt, chrome-only reports (ONTAP, UCS, Netmap), the glass web look, and the shared
section-kind dispatcher across all seven top-level report templates.
Placeholders only: SITEA, corp.example.mil, 192.0.2.x.
"""
import os
import re
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))

import ad_filters as adf
import finding_filters as ff
import server_view_filters as svf
import site_filters as sf
import stig_filters as stf
import stigman_health_filters as smh


def _env():
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
        report_cards_html=sf.report_cards_html,
        report_rule_texts_html=sf.report_rule_texts_html,
        safe_link=sf.safe_link,
        safe_ref=sf.safe_ref,
        safe_site_url=sf.safe_site_url,
        safe_data=sf.safe_data,
        report_tidy=sf.report_tidy,
        report_head=sf.report_head,
    )
    return env


def _render(template_name, report, web=True, **extra):
    ctx = {
        "_report": report,
        "_email_footer": "AAP job 42 (SITEA report)",
        "_page_logo_cid": "",
        "_page_template": "SITEA - Report",
        "report_email_header_text": "SITEA automation report",
    }
    if web:
        ctx["_page_runs"] = []
    ctx.update(extra)
    return _env().get_template(template_name).render(**ctx)


class DesignCheckAndSteps9To14Tests(unittest.TestCase):
    """1.138.0: Covers all 24 items of vdi-design-check.txt and build steps 9-14."""

    def test_item1_no_host_reporting_shows_not_checked_no_zeroes(self):
        sec = svf.server_view({}, missing={"srv01.corp.example.mil": "SSH refused", "srv02.corp.example.mil": "timeout"})
        self.assertEqual(sec["status"], "unknown")
        self.assertEqual(sec["tiles"][0]["label"], "Not checked")
        self.assertEqual(sec["tiles"][0]["value"], 2)
        self.assertEqual(sec["tiles"][0]["status"], "unknown")
        self.assertEqual(sec["tiles"][1]["value"], "-")
        self.assertEqual(sec["tiles"][2]["value"], "-")
        self.assertEqual(sec["tiles"][3]["value"], "-")

    def test_item2_uncapped_by_host_capped_without_double_counting(self):
        fs = [{"check": "disk", "kind": "disk", "id": "d%d" % i, "severity": "warning", "summary": "Vol %d full" % i}
              for i in range(15)]
        sec = svf.server_view(
            {"srv01.corp.example.mil": fs},
            capped={"srv01.corp.example.mil": {"disk": 5}},
            opts={"max_per_kind": 10},
        )
        s = sec["servers"][0]
        self.assertEqual(len(s["findings"]), 10)
        self.assertEqual(s["capped_total"], 5)
        self.assertIn("and 5 more of disk, not listed", s["capped_text"])

    def test_item3_and_4_info_not_warning_and_site_health_status_ignored(self):
        by_host = {
            "srv01.corp.example.mil": [{"check": "config", "id": "i1", "severity": "info", "summary": "Note only"}],
            "srv02.corp.example.mil": [{"check": "patching", "id": "l1", "severity": "low", "summary": "Due in 4 days"}],
        }
        health = {
            "srv01.corp.example.mil": {"status": "findings", "checks": ["config"]},
            "srv02.corp.example.mil": {"status": "findings", "checks": ["patching"]},
        }
        sec = svf.server_view(by_host, health=health)
        by = dict((s["host"], s) for s in sec["servers"])
        self.assertEqual(by["srv01.corp.example.mil"]["status"], "info")
        self.assertEqual(by["srv01.corp.example.mil"]["counts"]["warning"], 0)
        self.assertEqual(by["srv02.corp.example.mil"]["status"], "low")
        self.assertEqual(by["srv02.corp.example.mil"]["counts"]["warning"], 0)

    def test_item5_step14_stigman_health_groups_by_finding_host(self):
        kc = {"url": "https://sso.corp.example.mil/realms/sitea", "oidc": {"status": 503, "why": "503"}, "token": {"status": 200}}
        targets = [
            {"name": "sm01.corp.example.mil", "kind": "node", "url": "https://sm01.corp.example.mil/api",
             "state": {"status": 200, "currentState": "unavailable", "db": False, "oidc": True}},
            {"name": "sm02.corp.example.mil", "kind": "node", "url": "https://sm02.corp.example.mil/api",
             "state": {"status": 200, "currentState": "available", "ms": 40}, "configuration": {"status": 200, "version": "1.6.18"}},
        ]
        fs = smh.stigman_health_findings(kc, targets)
        fs.append({"check": "stigman", "id": "stigman:host:sm03.corp.example.mil", "host": "sm03.corp.example.mil",
                   "severity": "warning", "summary": "sm03.corp.example.mil: its host was not read"})
        sec = svf.server_view(
            {"localhost": fs},
            opts={"known_hosts": ["sm01.corp.example.mil", "sm02.corp.example.mil", "sm03.corp.example.mil"],
                  "group_by_finding_host": True},
        )
        by = dict((s["host"], s) for s in sec["servers"])
        self.assertNotIn("localhost", by)
        self.assertEqual(by["sso.corp.example.mil"]["status"], "critical")
        self.assertEqual(by["sm01.corp.example.mil"]["status"], "critical")
        self.assertEqual(by["sm02.corp.example.mil"]["status"], "ok")
        self.assertTrue(by["sm03.corp.example.mil"]["missing"])
        self.assertEqual(by["sm03.corp.example.mil"]["status"], "unknown")

    def test_items6_and_7_step13_ad_dedupes_dc_spellings_keeps_low_and_separates_domain_objects(self):
        # Verify ad_filters._Report keeps low in low_problems while leaving problems strictly crit/warn
        rep_obj = adf._Report({})
        rep_obj.problem("corp.example.mil", "events", "DC02", "low", "3 informational events")
        rep_obj.problem("corp.example.mil", "dcdiag", "DC01", "critical", "Advertising failed")
        self.assertEqual(len(rep_obj.problems), 1)
        self.assertEqual(len(rep_obj.low_problems), 1)
        self.assertEqual(rep_obj.low_problems[0]["severity"], "low")

        ad_res = {
            "dcs": [
                {"dc": "DC01", "host": "dc01.corp.example.mil", "fqdn": "dc01.corp.example.mil", "domain": "corp.example.mil", "read": True},
                {"dc": "DC02", "host": "dc02.corp.example.mil", "fqdn": "dc02.corp.example.mil", "domain": "corp.example.mil", "read": True},
                {"dc": "DC03", "host": "dc03.corp.example.mil", "fqdn": "dc03.corp.example.mil", "domain": "corp.example.mil", "read": False, "error": "WinRM timeout"},
            ],
            "problems": [
                {"domain": "corp.example.mil", "area": "dcdiag", "object": "DC01", "severity": "critical", "text": "Advertising failed"},
                {"domain": "corp.example.mil", "area": "replication", "object": "dc01.corp.example.mil", "severity": "warning", "text": "Inbound lag 6h"},
                {"domain": "corp.example.mil", "area": "gpo", "object": "Default Domain Policy", "severity": "warning", "text": "GPO modified"},
            ],
            "low_problems": [
                {"domain": "corp.example.mil", "area": "events", "object": "DC02", "severity": "low", "text": "3 informational events"},
                {"domain": "corp.example.mil", "area": "sites", "object": "SITEA-Branch", "severity": "low", "text": "Site has no subnet"},
            ],
        }
        secs = svf.ad_server_view(ad_res)
        self.assertEqual(len(secs), 2)
        dc_sec, dom_sec = secs
        # No duplicate DC rows despite DC01 and dc01.corp.example.mil spellings
        self.assertEqual(len(dc_sec["servers"]), 3)
        by = dict((s["host"], s) for s in dc_sec["servers"])
        self.assertEqual(by["dc01.corp.example.mil"]["counts"]["critical"], 1)
        self.assertEqual(by["dc01.corp.example.mil"]["counts"]["warning"], 1)
        # Low-only DC stays low, NEVER amber
        self.assertEqual(by["dc02.corp.example.mil"]["status"], "low")
        self.assertEqual(by["dc02.corp.example.mil"]["counts"]["low"], 1)
        self.assertEqual(by["dc02.corp.example.mil"]["counts"]["warning"], 0)
        # Unread DC is unknown
        self.assertEqual(by["dc03.corp.example.mil"]["status"], "unknown")
        self.assertTrue(by["dc03.corp.example.mil"]["missing"])
        # Non-DC objects go to Domain & forest objects
        self.assertEqual(dom_sec["title"], "Domain & forest objects")
        self.assertEqual(len(dom_sec["rows"]), 2)
        self.assertEqual([t["data"]["sev"] for t in dom_sec["row_tags"]], ["warning", "low"])

    def test_item8_step12_cert_rollup_expired_mapping_and_table_tags(self):
        self.assertEqual(svf.normalize_severity("expired"), "critical")
        certs = [
            {"host": "web01.corp.example.mil", "days": -2, "expires": "2026-11-25", "subject": "CN=web01", "where": "/etc/pki/web.pem", "status": "expired"},
            {"host": "web01.corp.example.mil", "days": 20, "expires": "2026-12-17", "subject": "CN=web01-alt", "where": "/etc/pki/alt.pem"},
            {"host": "app01.corp.example.mil", "days": 180, "expires": "2027-05-26", "subject": "CN=app01", "where": "/etc/pki/app.pem"},
        ]
        rollup = svf.cert_host_rollup(certs, {"warn_days": 30, "crit_days": 7, "notice_days": 60, "missing": {"db01.corp.example.mil": "SSH refused"}})
        self.assertTrue(rollup["cert_rollup"])
        self.assertEqual(rollup["status"], "critical")
        hosts = [h["host"] for h in rollup["rollup_hosts"]]
        self.assertEqual(hosts, ["web01.corp.example.mil", "db01.corp.example.mil", "app01.corp.example.mil"])
        self.assertIn("2 certificates", rollup["rollup_hosts"][0]["summary"])
        self.assertIn("expired 2 days ago", rollup["rollup_hosts"][0]["summary"])
        tbl = sf.cert_report_section(certs, 30, 60)
        tagged = svf.cert_table_tag(tbl, 30, 60, 7)
        self.assertEqual(tagged["row_tags"][0]["data"], {"host": "web01.corp.example.mil", "sev": "critical"})
        self.assertEqual(tagged["row_tags"][1]["data"], {"host": "web01.corp.example.mil", "sev": "warning"})
        self.assertEqual(tagged["row_tags"][2]["data"], {"host": "app01.corp.example.mil", "sev": "ok"})

    def test_items9_10_11_12_step9_stig_multichecklist_not_scanned_severity_map_and_per_rule_expander(self):
        # Item 10: stig:not-scanned carries checklist key when present, stays stig:not-scanned when empty
        c_empty = stf.stig_compare({"scanned": False, "why": "missing"}, {}, "srv01.corp.example.mil")
        self.assertEqual(c_empty["findings"][0]["id"], "stig:not-scanned")
        c_keyed = stf.stig_compare({"scanned": False, "why": "missing", "key": "iis10", "label": "IIS 10", "platform": "windows"}, {}, "win01.corp.example.mil")
        self.assertEqual(c_keyed["findings"][0]["id"], "stig:not-scanned:iis10")
        self.assertTrue(c_keyed["findings"][0]["summary"].startswith("IIS 10: "))

        # Item 11: _stig_entries honours severity_map / rec['level']
        res_custom = {
            "benchmark": "RHEL 9 STIG",
            "drift": [{"key": "SV-257777", "stigid": "RHEL-09-211010", "severity": "high", "level": "warning", "title": "Boot loader password"}],
        }
        ents = ff._stig_entries(res_custom, "srv01.corp.example.mil", severity_map={"high": "warning"})
        self.assertEqual(ents[0]["severity"], "warning")

        # Items 9 & 12: multi-checklist folded to ONE server row + per-rule expander with check hint (Copy) vs fix prose (no Copy)
        r1 = {
            "host": "win01.corp.example.mil", "scanned": True, "label": "win01.corp.example.mil - Win 2022", "key": "win2022",
            "drift": [{"key": "SV-254238", "stigid": "WN22-00-000010", "severity": "high", "level": "critical", "title": "Domain joined"}],
            "new": [], "disagree": [], "fixed_open": [], "findings": [],
        }
        r2 = {
            "host": "win01.corp.example.mil", "scanned": True, "label": "win01.corp.example.mil - IIS 10", "key": "iis10",
            "drift": [], "new": [], "disagree": [],
            "fixed_open": [{"key": "SV-218735", "stigid": "IIST-SV-000100", "severity": "medium", "title": "Logging enabled"}],
            "findings": [],
        }
        texts = {
            "SV-254238": {
                "check": "Run the following command:\n$ Get-ComputerInfo -Property CsPartOfDomain",
                "fix": "Join the server to the SITEA Active Directory domain through System Properties.",
            }
        }
        sec = svf.stig_server_view([r1, r2], texts=texts, opts={"portal_url": "https://portal.corp.example.mil/portal"})
        self.assertEqual(len(sec["servers"]), 1)
        s = sec["servers"][0]
        self.assertEqual(s["checklists"], ["Win 2022", "IIS 10"])
        self.assertEqual(len(s["checklist_breakdown"]), 2)
        self.assertEqual(s["counts"]["critical"], 1)
        self.assertEqual(s["counts"]["low"], 1)
        self.assertIn("Get-ComputerInfo", s["findings"][0]["hint"])
        self.assertIn("Join the server", s["findings"][0]["fix"])
        self.assertEqual(len(s["host_buttons"]), 1)

    def test_item13_step10_fapolicyd_permissive_low_capped_and_no_copy_on_rules(self):
        blocks = [{"exe": "/usr/bin/python3", "path": "/opt/sitea/job%02d.py" % i, "count": 2, "permissive": True,
                   "proposal": "allow perm=any exe=/usr/bin/python3 : path=/opt/sitea/job%02d.py" % i}
                  for i in range(15)]
        hvs = [
            {"_ff": {"mode": "permissive", "state": "ok"}, "_fr": {"mode": "permissive", "blocks": blocks}},
            {"site_host_skipped": True},
            {"site_host_unreached": "SSH timed out"},
        ]
        names = ["app01.corp.example.mil", "skip01.corp.example.mil", "down01.corp.example.mil"]
        sec = svf.fapolicyd_server_view(hvs, names, {"max_denials": 10, "since": "today"})
        by = dict((s["host"], s) for s in sec["servers"])
        a = by["app01.corp.example.mil"]
        # Permissive 'would deny' is low, NEVER amber
        self.assertEqual(a["status"], "low")
        self.assertEqual(a["counts"]["warning"], 0)
        self.assertEqual(len(a["findings"]), 10)
        self.assertEqual(a["capped_total"], 5)
        self.assertIn("and 5 more of fapolicyd_deny, not listed", a["capped_text"])
        # Proposed allow rule is in fix (prose), NOT in hint (read-only command only)
        self.assertIn("allow perm=any", a["findings"][0]["fix"])
        self.assertNotIn("allow perm=any", a["findings"][0]["hint"])
        self.assertIn("ausearch -m FANOTIFY", a["findings"][0]["hint"])
        # Skipped host noted
        self.assertIn("1 left out on purpose", sec["summary_text"])

    def test_item14_step11_sw_inventory_list_only_and_skipped_count(self):
        data = {
            "hosts": [
                {"host": "web01.corp.example.mil", "os": "RHEL 9.4", "kernel": "5.14.0-427", "packages": 512, "read_at": "2026-11-27T09:00:00Z"},
                {"host": "web02.corp.example.mil", "os": "RHEL 9.4", "kernel": "5.14.0-427", "packages": 500, "read_at": "2026-11-27T09:00:00Z"},
            ],
            "not_read": [{"host": "web03.corp.example.mil", "reason": "NOT REACHED - SSH refused"}],
            "skipped": ["win01.corp.example.mil", "win02.corp.example.mil"],
            "changes_by_host": {"web01.corp.example.mil": [{"change": "updated", "name": "openssl", "before": "3.0.7-24", "after": "3.0.7-27"}]},
            "first_seen": [],
            "read_at": "2026-11-27T09:00:00Z",
        }
        sec = svf.sw_inventory_server_view(data)
        self.assertTrue(sec["list_only"])
        self.assertNotIn("Crit", sec["columns"])
        self.assertIn("2 left out on purpose", sec["summary_text"])
        self.assertIn("2 hosts left out on purpose", sec["note"])
        self.assertEqual([s["status_label"] for s in sec["servers"]], ["Not read", "Read", "Read"])

    def test_item15_step11_solarwinds_uncertain_not_green_and_excluded_from_matched_counts(self):
        read = {
            "ok": True, "read_at": "2026-11-27 09:40 UTC", "age_min": 5, "orion": "orion.corp.example.mil",
            "hosts": [
                {"host": "web01.corp.example.mil", "node": {"node_id": 1, "name": "web01", "status": "Up", "ip": "192.0.2.10"}, "alerts": []},
                {"host": "dc01", "uncertain": True, "candidates": [{"name": "dc01.a.corp.example.mil"}, {"name": "dc01.b.corp.example.mil"}]},
                {"host": "gone01.corp.example.mil", "node": None},
            ],
        }
        sec = svf.swr_server_view(read)
        self.assertTrue(sec["list_only"])
        self.assertFalse(sec["counted"])
        self.assertEqual(sec["counts"]["matched"], 1)
        self.assertEqual(sec["counts"]["uncertain"], 1)
        by = dict((s["host"], s) for s in sec["servers"])
        self.assertEqual(by["dc01"]["status"], "unknown")
        self.assertEqual(by["dc01"]["status_label"], "Not certain")
        self.assertNotEqual(by["dc01"]["status"], "ok")

    def test_items16_17_18_20_21_22_23_24_remaining_design_checks(self):
        # Item 16: At a glance / server_view sits at index 0 ABOVE SolarWinds first:true
        # Item 17: Findings table gets data.sev row_tags even without report_teams
        by_host = {
            "Web02.corp.example.mil": [{"check": "disk", "id": "d1", "severity": "warning", "summary": "/var 85%"}],
            "app01.corp.example.mil": [],
        }
        first_sec = {"title": "SolarWinds overview", "first": True, "text": "Orion snapshot"}
        rep = sf.findings_report(
            by_host, "SITEA health",
            extra=[first_sec],
            servers={
                "enabled": True,
                "health": {
                    "app01.corp.example.mil": {"checks": [], "findings": [{"severity": "critical", "summary": "stale copy"}]},
                    "Web02.corp.example.mil": {"checks": ["disk"]},
                    "site_report_alias": {"checks": ["disk"]},
                },
            },
        )
        self.assertTrue(rep["sections"][0].get("server_view"))
        self.assertEqual(rep["sections"][1]["title"], "SolarWinds overview")
        f_sec = [s for s in rep["sections"] if s.get("title") == "Findings"][0]
        self.assertEqual(f_sec["row_tags"][0]["data"]["sev"], "warning")
        self.assertNotIn("team_fold", f_sec)
        sv = rep["sections"][0]
        # Item 18: unmatched site_health entry recorded in other_health
        self.assertEqual(sv["other_health"], ["site_report_alias"])
        # Item 22 & 24: app01 had empty checks -> Not checked (unknown), and stale health.findings ignored
        by = dict((s["host"], s) for s in sv["servers"])
        self.assertEqual(by["app01.corp.example.mil"]["status"], "unknown")
        self.assertEqual(by["app01.corp.example.mil"]["status_label"], "Not checked")
        self.assertEqual(by["app01.corp.example.mil"]["counts"]["critical"], 0)
        # Item 20: missing-only section has status unknown (not warning)
        sv_miss = svf.server_view({"srv01.corp.example.mil": []}, missing={"srv02.corp.example.mil": "refused"})
        self.assertEqual(sv_miss["status"], "unknown")
        # Item 21: case-insensitive sort
        sv_sort = svf.server_view({"Zeta.corp.example.mil": [], "alpha.corp.example.mil": [], "Beta.corp.example.mil": []})
        self.assertEqual([s["host"] for s in sv_sort["servers"]], ["alpha.corp.example.mil", "Beta.corp.example.mil", "Zeta.corp.example.mil"])
        # Item 23: single-host run keeps host name
        sv_one = svf.server_view({"solo01.corp.example.mil": [{"check": "disk", "id": "d1", "severity": "warning", "summary": "80%"}]})
        self.assertEqual(sv_one["servers"][0]["host"], "solo01.corp.example.mil")
        self.assertIn("Server", sv_one["columns"])


class ChromeOnlyGlassAndDispatcherTests(unittest.TestCase):
    """1.138.0: Chrome-only ONTAP/UCS/Netmap, Glass web vs email, byte-identical off-mode, Step 15 dispatcher."""

    def test_chrome_only_preserves_native_keys_for_ontap_ucs_netmap(self):
        doc = {
            "title": "ONTAP health: 1 cluster",
            "status": "critical",
            "summary": [
                {"label": "Clusters", "value": 1, "status": "ok"},
                {"label": "Volumes over 90%", "value": 1, "status": "critical"},
                {"label": "SVMs", "value": 3, "status": "ok"},
            ],
            "sections": [
                {"title": "Volumes", "status": "critical", "columns": ["Cluster", "SVM", "Volume", "Used %"],
                 "rows": [["cl-sitea-01", "svm-prod", "vol_db", "96%"], ["cl-sitea-01", "svm-prod", "vol_web", "42%"]],
                 "row_status": ["critical", ""]},
            ],
        }
        wrapped = svf.chrome_report_view(doc, {"kind_label": "Storage · native cluster & volume view"})
        self.assertTrue(wrapped["sections"][0]["chrome_view"])
        self.assertEqual(wrapped["sections"][1]["title"], "Volumes")
        self.assertEqual(wrapped["sections"][1]["columns"], ["Cluster", "SVM", "Volume", "Used %"])
        self.assertEqual([t["data"]["sev"] for t in wrapped["sections"][1]["row_tags"]], ["critical", "ok"])

    def test_all_four_looks_render_server_view_and_glass_email_stays_untouched(self):
        by_host = {
            "srv01.corp.example.mil": [
                {"check": "disk", "id": "d1", "severity": "critical", "summary": "/var 96%",
                 "fix": "Grow /var.", "hint": "df -h /var"},
            ],
            "srv02.corp.example.mil": [],
        }
        rep = sf.findings_report(
            by_host, "SITEA health",
            missing={"srv03.corp.example.mil": "SSH timed out"},
            servers={"enabled": True, "capped": {"srv01.corp.example.mil": {"fapolicyd_deny": 12}}},
        )
        for look in ("atlas", "brief", "ops", "glass"):
            html = _render("report_%s.html.j2" % look, rep, web=True)
            self.assertIn('class="srv-glance"', html, look)
            self.assertIn("srv-hot", html, look)
            self.assertIn("and 12 more not listed", html, look)
            self.assertIn("Not checked:", html, look)
            self.assertIn("Check command (read-only hint)", html, look)
            self.assertIn("<b>What to do:</b> Grow /var.", html, look)
            self.assertIn('id="rail-jump"', html, look)
        # Glass email (web=False) MUST stay classic email layout (no JS, no srv-glance)
        email_html = _render("report_glass.html.j2", rep, web=False)
        self.assertNotIn("srv-glance", email_html)
        self.assertNotIn("site-report-js", email_html)

    def test_off_mode_is_byte_identical_across_all_four_web_looks(self):
        rep_plain = sf.findings_report(
            {"srv01.corp.example.mil": [{"check": "disk", "id": "d1", "severity": "warning", "summary": "/var 85%"}]},
            "SITEA health",
        )
        for look in ("atlas", "brief", "ops", "glass"):
            a = _render("report_%s.html.j2" % look, rep_plain, web=True, report_servers_view=False)
            b = _render("report_%s.html.j2" % look, rep_plain, web=True, report_servers_view=False)
            self.assertEqual(a, b, look)
            self.assertNotIn("srv-glance", a, look)
            self.assertNotIn("report_servers", a, look)

    def test_step15_section_kind_dispatcher_used_in_all_seven_templates(self):
        tdir = os.path.join(REPO, "roles", "site_email", "templates")
        for name in ("report.html.j2", "report_command.html.j2", "report_portal.html.j2", "report_glass.html.j2",
                     "report_atlas.html.j2", "report_brief.html.j2", "report_ops.html.j2"):
            with open(os.path.join(tdir, name), "r", encoding="utf-8") as f:
                src = f.read()
            self.assertIn("report_section_kind.html.j2", src, name)
            self.assertNotIn("report_story_email.html.j2", src, name)
            self.assertNotIn("report_story.html.j2", src, name)

    def test_report_servers_js_is_ascii_es5_safe_and_idempotent(self):
        path = os.path.join(REPO, "roles", "site_email", "templates", "report_servers.js")
        with open(path, "r", encoding="utf-8") as f:
            js = f.read()
        js.encode("ascii")
        self.assertIn("if(window.siteReportServers)return;", js)
        for forbidden in ("Promise", "=>", "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "{{", "{%", "{#", "</script"):
            self.assertNotIn(forbidden, js, forbidden)


if __name__ == "__main__":
    unittest.main()
