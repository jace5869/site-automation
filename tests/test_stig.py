#!/usr/bin/env python3
"""Unit tests for the STIG drift check: reading oscap's results (roles/check_stig/library/site_stig_scan.py - one
item per STIG rule, the worst of its SCAP rules, the STIG version, the runs kept on the host) and Evaluate-STIG's
checklists (CKLB, CKL; running it, reading what it wrote, the scanner changed), finding an asset
and its reviews in STIG Manager (roles/check_stig/library/site_stigman_read.py), and the comparison, findings and
report sections (plugins/filter/stig_filters.py).

    python tests/test_stig.py
"""
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "roles", "check_stig", "library"))
sys.path.insert(0, os.path.join(ROOT, "plugins", "filter"))
sys.path.insert(0, os.path.join(ROOT, "tests", "stig"))
import make_cklb                   # noqa: E402
import make_results                # noqa: E402
import site_stig_scan as scan     # noqa: E402
import site_stigman_read as smr   # noqa: E402
import site_stigman_attachments as sma  # noqa: E402
import stig_filters as sf         # noqa: E402


def results(res=None, version="V2R9"):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "r.xml")
    make_results.write(p, res or {}, version)
    return scan.read_results(p)


def items(res=None):
    meta, rules, rr = results(res)
    return scan.items_of(rules, rr)


class Read(unittest.TestCase):
    def test_meta_and_version(self):
        meta, rules, rr = results({}, "V1R2")
        self.assertEqual(meta["profile"], "xccdf_org.ssgproject.content_profile_stig")
        self.assertEqual(meta["profile_title"], "DISA STIG for Red Hat Enterprise Linux 9")
        self.assertEqual(scan.stig_version(meta["profile_title"], meta["profile_text"]), "V1R2")
        self.assertEqual(meta["ssg_version"], "0.1.82")
        self.assertNotIn("xccdf_org.ssgproject.content_rule_not_in_profile", rr, "a notselected rule is not part of the scan")

    def test_rule_references(self):
        meta, rules, rr = results()
        r = rules["xccdf_org.ssgproject.content_rule_sshd_disable_root_login"]
        self.assertEqual((r["sv"], r["rule"], r["v"], r["stigid"], r["severity"]),
                         ("SV-257985", "SV-257985r1069364_rule", "V-257985", "RHEL-09-255045", "medium"))

    def test_one_item_per_stig_rule_the_worst_counts(self):
        its = {i["key"]: i for i in items({"audit_rules_privileged_commands_su": "fail"})}
        self.assertEqual(len(its), 9)                       # 9 SCAP rules: two of them one STIG rule, one of them two
        a = its["SV-258176"]
        self.assertEqual(a["result"], "fail")
        self.assertEqual(a["scap"], ["audit_rules_privileged_commands_su", "audit_rules_privileged_commands_sudo"])
        self.assertEqual(a["scap_ids"], ["xccdf_org.ssgproject.content_rule_audit_rules_privileged_commands_su",
                                         "xccdf_org.ssgproject.content_rule_audit_rules_privileged_commands_sudo"])
        self.assertEqual(its["enable_authselect"]["result"], "pass", "a SCAP rule with no STIG rule is its own item")

    def test_one_scap_rule_several_stig_rules(self):
        """dconf_gnome_banner_enabled checks SV-258012 and SV-258013: its result counts for both (before 0.44.0 only
        the last one got a result; in scap-security-guide 0.1.82: 7 STIG rules of RHEL 9, 11 of RHEL 8)."""
        its = {i["key"]: i for i in items({"dconf_gnome_banner_enabled": "fail"})}
        for k in ("SV-258012", "SV-258013"):
            self.assertEqual((its[k]["result"], its[k]["v"], its[k]["scap"]), ("fail", "V-" + k[3:], ["dconf_gnome_banner_enabled"]))
            self.assertEqual(its[k]["stigid"], "", "the content's order does not pair STIG ids with SV ids: none rather than a wrong one")
        self.assertEqual(its["SV-258013"]["rule"], "SV-258013r1045082_rule")
        self.assertEqual(its["SV-257985"]["stigid"], "RHEL-09-255045", "one STIG rule: its STIG id")

    def test_passed_before(self):
        d = tempfile.mkdtemp()
        for st, items_ in (("20261001T100000Z", {"SV-1": "pass", "SV-2": "fail"}), ("20261008T100000Z", {"SV-1": "fail", "SV-3": "pass"})):
            with open(os.path.join(d, st + "_summary.json"), "w") as f:
                json.dump({"time": st, "items": items_}, f)
        self.assertEqual(scan.passed_before(d, scan.runs_in(d), {"items": {}}), {"SV-1": "20261001T100000Z", "SV-3": "20261008T100000Z"},
                         "a summary from before 0.44.0: built from the scans kept")
        self.assertEqual(scan.passed_before(d, scan.runs_in(d), {"passed_before": {"SV-9": "t"}}), {"SV-9": "t"},
                         "the last summary's own list (it outlives the scans deleted)")

    def test_worst(self):
        self.assertEqual(scan.worst(["pass", "notapplicable"]), "pass")
        self.assertEqual(scan.worst(["notapplicable", "notchecked"]), "notchecked")
        self.assertEqual(scan.worst(["pass", "error", "fail"]), "fail")
        self.assertEqual(scan.worst([]), "notchecked")

    def test_disa_benchmark_rule_ids(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "r.xml")
        ns = "http://checklists.nist.gov/xccdf/1.2"
        open(p, "w").write('<Benchmark xmlns="%s" id="xccdf_mil.disa.stig_benchmark_RHEL_9_STIG"><Group id="V-257985">'
                           '<Rule id="xccdf_mil.disa.stig_rule_SV-257985r1069364_rule" severity="medium"><version>RHEL-09-255045</version>'
                           '<title>No root SSH.</title></Rule></Group><TestResult version="2"><profile idref="xccdf_mil.disa.stig_profile_MAC-1_Classified"/>'
                           '<rule-result idref="xccdf_mil.disa.stig_rule_SV-257985r1069364_rule"><result>fail</result></rule-result></TestResult></Benchmark>' % ns)
        meta, rules, rr = scan.read_results(p)
        its = scan.items_of(rules, rr)
        self.assertEqual([(i["key"], i["stigid"], i["result"]) for i in its], [("SV-257985", "RHEL-09-255045", "fail")])

    def test_runs_kept(self):
        d = tempfile.mkdtemp()
        for n in ("20261001T100000Z_summary.json", "20261002T100000Z_summary.json", "latest-summary.json", "notes_summary.json"):
            open(os.path.join(d, n), "w").write("{}")
        self.assertEqual(scan.runs_in(d), ["20261001T100000Z", "20261002T100000Z"])


class StigManager(unittest.TestCase):
    A = [{"name": "web01", "fqdn": ""}, {"name": "db01.example.mil", "fqdn": ""}, {"name": "x", "fqdn": "app01.example.mil"}]

    def test_exact_name_or_fqdn_case_blind(self):
        self.assertEqual(smr.match_assets(["WEB01"], self.A), [self.A[0]])
        self.assertEqual(smr.match_assets(["app01.example.mil"], self.A), [self.A[2]])

    def test_short_names_when_nothing_exact(self):
        self.assertEqual(smr.match_assets(["db01"], self.A), [self.A[1]])
        self.assertEqual(smr.match_assets(["web01.example.mil"], self.A), [self.A[0]])
        self.assertEqual(smr.match_assets(["nope"], self.A), [])

    def test_reviews_by_stig_rule_every_revision(self):
        r = smr.reviews_of([{"ruleId": "SV-1r2_rule", "ruleIds": ["SV-1r2_rule", "SV-1r9_rule"], "result": "pass",
                             "status": {"label": "accepted"}, "rule": {"version": "X-1", "severity": "high", "title": "T"}},
                            {"ruleId": "SV-2r1_rule", "result": "fail", "status": "saved"}])
        self.assertEqual(r["SV-1"]["status"], "accepted")
        self.assertEqual(r["SV-1"]["title"], "T")
        self.assertEqual(r["SV-2"]["status"], "saved")
        self.assertEqual(smr.sv_of("SV-257985r1069364_rule"), "SV-257985")


def scanned(res, prev=None, before=None):
    """A scan: res the SCAP rules' results, prev the last scan's ({key: result}), before passed_before (None: what passed in prev)."""
    its = items(res)
    out = {"scanned": True, "items": its, "counts": scan.counts(its), "previous": {"time": "2026-10-01T10:00:00Z", "items": prev} if prev else {},
           "saved": {"report": "/var/lib/site-automation/stig/x_report.html"}, "stig_version": "V2R9", "profile_title": "DISA STIG", "os": "RHEL 9",
           "profile": "xccdf_org.ssgproject.content_profile_stig", "datastream": "/usr/share/xml/scap/ssg/content/ssg-rhel9-ds.xml", "tailoring": ""}
    if before is not None:
        out["passed_before"] = before
    return out


ALL_PASS = {i["key"]: "pass" for i in items()}


def sm(**reviews):
    return {"status": "found", "collection": "C", "asset": "web01", "revision": "V2R7",
            "reviews": {k.replace("_", "-"): {"result": v[0], "status": v[1], "ts": "2026-09-01T00:00:00Z", "title": "DISA " + k} for k, v in reviews.items()}}


class Compare(unittest.TestCase):
    def test_drift_new_fixed_unreviewed(self):
        c = sf.stig_compare(scanned({"ensure_gpgcheck_local_packages": "fail", "accounts_tmout": "fail", "package_telnet-server_removed": "fail"}, ALL_PASS),
                            sm(SV_257821=("pass", "accepted"), SV_257883=("fail", "saved"), SV_257985=("fail", "accepted")), "web01", stigman_used=True)
        self.assertEqual([r["key"] for r in c["drift"]], ["SV-257821"])
        self.assertEqual(c["drift"][0]["title"], "DISA SV_257821", "STIG Manager's (DISA's) title wins")
        self.assertEqual([r["key"] for r in c["new"]], ["SV-257835"], "an Open review that fails now is not new")
        self.assertEqual([r["key"] for r in c["fixed_open"]], ["SV-257985"])
        self.assertEqual([r["key"] for r in c["unreviewed"]], ["SV-257835"])
        ids = {f["id"]: f for f in c["findings"]}
        self.assertEqual(ids["stig:drift"]["severity"], "critical")
        self.assertIn("1 STIG rule STIG Manager has as Not a Finding or Not Applicable fails now: V-257821 CAT I", ids["stig:drift"]["summary"])
        self.assertEqual(ids["stig:new"]["severity"], "critical")
        self.assertNotIn("stig:stigman", ids)

    def test_hint_is_the_command_note_says_why(self):
        """The hint is what to run (the Start here card's Copy, the ticket's "look further"), the note what it means."""
        c = sf.stig_compare(scanned({"ensure_gpgcheck_local_packages": "fail"}, ALL_PASS), sm(SV_257821=("pass", "accepted")), "web01", stigman_used=True)
        f = c["findings"][0]
        self.assertEqual(f["hint"], "sudo oscap xccdf eval --profile xccdf_org.ssgproject.content_profile_stig --rule "
                                    "xccdf_org.ssgproject.content_rule_ensure_gpgcheck_local_packages /usr/share/xml/scap/ssg/content/ssg-rhel9-ds.xml")
        self.assertIn("passed on this host in an earlier scan", f["note"])
        self.assertIn("/var/lib/site-automation/stig/x_report.html", f["note"])
        t = dict(scanned({}), tailoring="/var/lib/site-automation/stig/content/t.xml")
        self.assertIn("--tailoring-file /var/lib/site-automation/stig/content/t.xml --rule", sf.check_command(t, {"scap_ids": ["x"]}))

    def test_drift_needs_an_earlier_pass_else_the_two_disagree(self):
        res = {"ensure_gpgcheck_local_packages": "fail"}
        review = sm(SV_257821=("pass", "accepted"))
        c = sf.stig_compare(scanned(res, before={}), review, "web01", stigman_used=True)
        self.assertEqual(([r["key"] for r in c["drift"]], [r["key"] for r in c["disagree"]]), ([], ["SV-257821"]),
                         "it never passed here: nothing moved that the job saw")
        self.assertEqual(c["findings"], [], "report only by default")
        c = sf.stig_compare(scanned(res, before={}), review, "web01", stigman_used=True, disagree_severity="warning")
        self.assertEqual([(f["id"], f["severity"]) for f in c["findings"]], [("stig:disagree", "warning")])
        self.assertIn("1 STIG rule STIG Manager has as Not a Finding or Not Applicable has failed in every scan of this host: V-257821", c["findings"][0]["summary"])
        self.assertIn("check_stig_ignore_rules", c["findings"][0]["note"])

    def test_drift_stays_while_it_fails(self):
        """The last scan failed it too, but it passed before that: still drift, every run until it is fixed."""
        c = sf.stig_compare(scanned({"ensure_gpgcheck_local_packages": "fail"}, dict(ALL_PASS, **{"SV-257821": "fail"}),
                                    before={"SV-257821": "2026-09-24T10:00:00Z"}),
                            sm(SV_257821=("pass", "accepted")), "web01", stigman_used=True)
        self.assertEqual([r["key"] for r in c["drift"]], ["SV-257821"])
        self.assertEqual(c["drift"][0]["passed"], "2026-09-24T10:00:00Z")
        c = sf.stig_compare(scanned({"accounts_tmout": "fail"}, dict(ALL_PASS, **{"SV-257883": "fail"}), before={"SV-257883": "2026-09-24T10:00:00Z"}),
                            {}, "web01")
        self.assertEqual([r["key"] for r in c["new"]], ["SV-257883"], "a new fail too")
        self.assertIn("passed in an earlier scan (last 2026-09-24) and fails now", c["findings"][0]["summary"])

    def test_ignore_rules(self):
        res = {"ensure_gpgcheck_local_packages": "fail", "sshd_disable_root_login": "fail", "accounts_tmout": "fail"}
        review = sm(SV_257821=("pass", "accepted"), SV_257985=("pass", "accepted"), SV_257883=("pass", "accepted"))
        for ign in (["V-257821", "RHEL-09-255045", "accounts_tmout"], "SV-257821r1015077_rule, rhel-09-255045, ACCOUNTS_TMOUT"):
            c = sf.stig_compare(scanned(res, ALL_PASS), review, "web01", stigman_used=True, ignore=ign)
            self.assertEqual((c["drift"], c["findings"]), ([], []), ign)
            self.assertEqual([r["key"] for r in c["ignored"]], ["SV-257821", "SV-257883", "SV-257985"])
        c = sf.stig_compare(scanned({}, ALL_PASS), review, "web01", stigman_used=True, ignore=["V-257821"])
        self.assertEqual(c["ignored"], [], "a rule left out that passes is not listed")

    def test_by_hand(self):
        """The rules the checklist has a review of that the scan gives no result for: DISA's manual checks."""
        review = sm(SV_257821=("pass", "accepted"), SV_999001=("pass", "accepted"), SV_999002=("fail", "saved"), SV_999003=("pass", "rejected"))
        c = sf.stig_compare(scanned({}), review, "web01", stigman_used=True)
        self.assertEqual(c["by_hand"], 2)
        self.assertEqual(c["row"]["cells"][9], "2")
        self.assertIsNone(sf.stig_compare(scanned({}), {}, "web01")["by_hand"])
        self.assertEqual(sf.stig_compare(scanned({}), {}, "web01")["row"]["cells"][9], "-")

    def test_not_applicable_in_stig_manager_is_drift_rejected_does_not_count(self):
        c = sf.stig_compare(scanned({"sshd_disable_root_login": "fail", "banner_etc_issue": "fail"}, ALL_PASS),
                            sm(SV_257985=("notapplicable", "accepted"), SV_257779=("pass", "rejected")), "web01", stigman_used=True)
        self.assertEqual([r["key"] for r in c["drift"]], ["SV-257985"])
        self.assertEqual([r["key"] for r in c["unreviewed"]], ["SV-257779"])
        c = sf.stig_compare(scanned({"banner_etc_issue": "fail"}, ALL_PASS), sm(SV_257779=("pass", "submitted")), "web01", statuses=["accepted"], stigman_used=True)
        self.assertEqual(c["drift"], [], "statuses [accepted]: a submitted review is not the checklist")

    def test_severity_setting(self):
        c = sf.stig_compare(scanned({"ensure_gpgcheck_local_packages": "fail"}, ALL_PASS), sm(SV_257821=("pass", "accepted")), "web01",
                            severity={"high": "warning"}, stigman_used=True)
        self.assertEqual(c["findings"][0]["severity"], "warning")

    def test_first_scan_and_no_stig_manager(self):
        c = sf.stig_compare(scanned({"accounts_tmout": "fail"}), {}, "web01")
        self.assertEqual(c["findings"], [], "nothing to compare with: no finding")
        self.assertEqual(c["row"]["status"], "ok")

    def test_stig_manager_problems(self):
        c = sf.stig_compare(scanned({}), {"status": "not_found", "why": "no asset named web01"}, "web01", stigman_used=True)
        self.assertEqual([(f["id"], f["severity"]) for f in c["findings"]], [("stig:stigman", "warning")])
        c = sf.stig_compare(scanned({}), {"status": "not_found", "why": "x"}, "web01", stigman_used=True, missing_severity="none")
        self.assertEqual(c["findings"], [])

    def test_not_scanned(self):
        c = sf.stig_compare({"scanned": False, "why": "oscap is not installed"}, {}, "web01")
        self.assertEqual([f["id"] for f in c["findings"]], ["stig:not-scanned"])
        self.assertIn("oscap is not installed", c["row"]["cells"][1])


class Sections(unittest.TestCase):
    def test_order_and_boxes(self):
        a = sf.stig_compare(scanned({"ensure_gpgcheck_local_packages": "fail", "accounts_tmout": "fail", "banner_etc_issue": "fail",
                                     "sshd_disable_root_login": "fail"}, ALL_PASS, before=dict((k, "t") for k in ALL_PASS if k != "SV-257779")),
                            sm(SV_257821=("pass", "accepted"), SV_257779=("pass", "accepted"), SV_258176=("fail", "saved")), "web01",
                            stigman_used=True, ignore=["V-257985"])
        b = sf.stig_compare({"scanned": False, "why": "no content"}, {}, "web02")
        s = sf.stig_report_sections([a, b])
        self.assertEqual([x["title"].split(" - ")[0] for x in s],
                         ["1. Drifted", "2. New fails", "3. The scan and the checklist disagree", "4. Fixed on the host, still Open in STIG Manager",
                          "Every host", "Left out on purpose", "Fails STIG Manager has no review for"])
        self.assertTrue(all(x.get("top") for x in s[:4]))
        self.assertEqual([(x["label"], x["value"], x["status"]) for x in s[0]["boxes"]],
                         [("Drifted", 1, "critical"), ("New fails", 1, "warning"), ("Disagree", 1, "info"),
                          ("Fixed, still Open", 1, "info"), ("Not scanned", 1, "warning")])
        self.assertTrue(all(x.get("note") for x in s[0]["boxes"]), "every tile says what it means")
        self.assertEqual(s[4]["rows"][0][0], "web01", "worst first: the critical host before the one not scanned")
        self.assertEqual(len(s[4]["rows"][0]), len(s[4]["columns"]))
        self.assertEqual(len(s[4]["rows"][1]), len(s[4]["columns"]), "a host not scanned: the same number of cells")
        json.dumps(s)

    def test_start_here_only_cat_i(self):
        """Start here: one card per CAT I rule that drifted or fails new; CAT II, the host not scanned: the tables."""
        a = sf.stig_compare(scanned({"ensure_gpgcheck_local_packages": "fail", "sshd_disable_root_login": "fail", "package_telnet-server_removed": "fail"},
                                    ALL_PASS), sm(SV_257821=("pass", "accepted"), SV_257985=("pass", "accepted")), "web01", stigman_used=True)
        b = sf.stig_compare({"scanned": False, "why": "no content"}, {}, "web02")
        s = sf.stig_report_sections([a, b])
        cards = s[0]["start_here"]
        self.assertEqual([(c["meta"], c["subject"], c["chip"], c["status"]) for c in cards],
                         [("Drifted", "web01 - V-257821 (RHEL-09-214020)", "CAT I", "critical"),
                          ("New fail", "web01 - V-257835 (RHEL-09-215040)", "CAT I", "critical")])
        self.assertIn("Passed 2026-10-01, fails now. STIG Manager: Not a Finding (accepted).", cards[0]["next"])
        self.assertIn("--rule xccdf_org.ssgproject.content_rule_ensure_gpgcheck_local_packages", cards[0]["code"])
        self.assertIn("No review in STIG Manager.", cards[1]["next"])
        n = sf.stig_report_sections([sf.stig_compare(scanned({"package_telnet-server_removed": "fail"}, ALL_PASS), {}, "web01")])
        self.assertEqual(n[0]["start_here"][0]["next"], "Passed 2026-10-01, fails now. Fix the host. To check it again on the host:",
                         "no STIG Manager: nothing said about it")
        sys.path.insert(0, os.path.join(ROOT, "plugins", "filter"))
        import site_filters
        rep = site_filters.findings_report({"web01": a["findings"], "web02": b["findings"]}, "STIG drift report", [], ["stig"], s)
        first = rep["sections"][0]
        self.assertEqual((first["title"], len(first["cards"])), ("Start here", 2), "the report's own cards, not one per finding")
        self.assertIn("The CAT I rules that moved", first["text"])
        q = sf.stig_report_sections([sf.stig_compare(scanned({"sshd_disable_root_login": "fail"}, ALL_PASS), sm(SV_257985=("pass", "accepted")),
                                                     "web01", stigman_used=True)])
        rep = site_filters.findings_report({"web01": [{"check": "stig", "severity": "warning", "summary": "x"}]}, "STIG drift report", [], ["stig"], q)
        self.assertNotEqual(rep["sections"][0]["title"], "Start here", "no CAT I moved: no Start here, the tables lead")

    def test_cells_carry_their_status(self):
        """CAT, the scan and STIG Manager as chips (the website's icons, the emails' colours): the drifted rule's FAIL
        critical beside STIG Manager's Not a Finding ok."""
        a = sf.stig_compare(scanned({"ensure_gpgcheck_local_packages": "fail", "accounts_tmout": "fail"}, ALL_PASS),
                            sm(SV_257821=("pass", "accepted"), SV_257985=("fail", "saved")), "web01", stigman_used=True)
        s = {x["title"].split(" - ")[0]: x for x in sf.stig_report_sections([a])}
        d = s["1. Drifted"]
        self.assertEqual(d["columns"], ["Host", "STIG rule", "CAT", "Title", "Scan now", "Last passed", "STIG Manager"])
        self.assertEqual(d["rows"][0][4:], ["FAIL", "2026-10-01", "Not a Finding (accepted)"])
        self.assertEqual(d["cell_status"][0], ["", "", "critical", "", "critical", "", "ok"])
        self.assertEqual(s["2. New fails"]["cell_status"][0][2], "warning", "CAT II: the setting's warning")
        self.assertEqual(s["4. Fixed on the host, still Open in STIG Manager"]["cell_status"][0][4:], ["ok", "warning"])
        for x in s.values():
            if x.get("rows"):
                self.assertEqual(len(x.get("cell_status") or x["rows"]), len(x["rows"]), x["title"])

    def test_no_drift(self):
        s = sf.stig_report_sections([sf.stig_compare(scanned({}), {}, "web01")])
        self.assertEqual(s[0]["title"], "No drift")
        self.assertEqual(s[0]["status"], "ok")
        # a host's first scan fails what STIG Manager has as Not a Finding: disagree - the card must not say every host passes
        first = sf.stig_compare(scanned({"sshd_disable_root_login": "fail"}), sm(SV_257985=("pass", "accepted")), "web01", stigman_used=True)
        self.assertEqual((len(first["drift"]), len(first["disagree"])), (0, 1))
        s = {x["title"]: x for x in sf.stig_report_sections([first])}
        self.assertNotIn("No drift", s)
        card = s["No drift - but the scan and the checklist disagree"]
        self.assertEqual(card["status"], "info")
        self.assertIn("the scan fails 1 STIG rule STIG Manager has as Not a Finding or Not Applicable, on 1 host", card["lines"][0])
        self.assertIn("check_stig_disagree_severity: warning (or critical) makes them findings", card["lines"][0])
        self.assertNotIn("Every host scanned passes", card["lines"][0])
        made = sf.stig_compare(scanned({"sshd_disable_root_login": "fail"}), sm(SV_257985=("pass", "accepted")), "web01",
                               stigman_used=True, disagree_severity="warning")
        card = [x for x in sf.stig_report_sections([made]) if x["title"].startswith("No drift")][0]
        self.assertEqual((card["status"], card["lines"][0].endswith("They are findings (check_stig_disagree_severity).")), ("warning", True))

    def test_unreviewed_capped(self):
        c = sf.stig_compare(scanned({k: "fail" for k, *_ in make_results.RULES}), sm(), "web01", stigman_used=True)
        s = sf.stig_report_sections([c], list_max=2)
        u = [x for x in s if x["title"].startswith("Fails STIG Manager has no review for")][0]
        self.assertEqual(len(u["rows"]), 2)
        self.assertIn("The first 2 of 8", u["text"])


class FakeModule(object):
    check_mode = False

    def __init__(self, **params):
        self.params = dict(dir="", keep=10, save=True, keep_report=True, keep_results=True, oscap="", content_dir="", datastream="",
                           profile="stig", tailoring="", timeout=60, scanner="evaluate_stig", evaluate="", evaluate_select={},
                           evaluate_args=[], evaluate_read="", evaluate_output="CKLB", evaluate_rejected=True, pwsh="", benchmarks={"8": "RHEL_8_STIG", "9": "RHEL_9_STIG", "10": "RHEL_10_STIG"},
                           vulns=[], history=True, answers="", answers_arg="--AFPath", answers_error="", answers_source="", answers_count=0)
        self.params.update(params)


FAKE_ES = """#!/bin/bash
# Evaluate-STIG_Bash.sh --Output CKLB --OutputPath DIR [--SelectSTIG S] [...]: writes DIR/HOST/Checklist/HOST_RHEL9.cklb
out=""; args="$*"
while [ $# -gt 0 ]; do case "$1" in --OutputPath) out="$2"; shift ;; esac; shift; done
echo "$args" > "$(dirname "$0")/args.txt"
mkdir -p "$out/WEB01/Checklist"
python3 "%s" "$out/WEB01/Checklist/WEB01_RHEL9_V2R10.cklb" "$(cat "$(dirname "$0")/results.json")" V2R10
echo "Evaluate-STIG done"
"""


class Evaluate(unittest.TestCase):
    def cklb(self, res=None, fmt="cklb", version="V2R10"):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "WEB01_RHEL9." + fmt)
        make_cklb.write(p, res or {}, version, fmt)
        return p

    def test_cklb_and_ckl_read_alike(self):
        res = {"SV-257985": "open", "SV-257821": "not_applicable", "SV-258999": "not_reviewed"}
        got = []
        for fmt in ("cklb", "ckl"):
            stigs = scan.read_checklist(self.cklb(res, fmt))
            self.assertEqual([s["stig_id"] for s in stigs], ["RHEL_9_STIG", "MOZ_Firefox_STIG"])
            st = scan.pick_stig(stigs, "RHEL_9_STIG", "9")
            self.assertEqual(scan.checklist_version(st), "V2R10")
            its = {i["key"]: i for i in scan.checklist_items(st)}
            self.assertEqual(its["SV-257985"]["result"], "fail")
            self.assertEqual(its["SV-257821"]["result"], "notapplicable")
            self.assertEqual(its["SV-258999"]["result"], "notchecked")
            self.assertEqual(its["SV-257883"]["result"], "pass")
            self.assertEqual((its["SV-257985"]["v"], its["SV-257985"]["stigid"], its["SV-257985"]["severity"]), ("V-257985", "RHEL-09-255045", "medium"))
            self.assertIn("is not set as required", its["SV-257985"]["details"])
            self.assertNotIn("details", its["SV-257883"])            # only the fails keep what was found
            self.assertEqual(set(its), {"SV-257985", "SV-257821", "SV-258176", "SV-257883", "SV-257835", "SV-257779", "SV-258012",
                                        "SV-258013", "SV-258999"})
            got.append(scan.checklist_items(st))
        self.assertEqual(got[0], got[1])

    def test_pick_by_benchmark_or_name(self):
        stigs = scan.read_checklist(self.cklb())
        self.assertEqual(scan.pick_stig(stigs, "", "9")["stig_id"], "RHEL_9_STIG")
        self.assertIsNone(scan.pick_stig(stigs, "RHEL_8_STIG", "8"))
        stigs[0]["stig_id"] = "RHEL9"                                 # named otherwise: by its title
        self.assertEqual(scan.pick_stig(stigs, "RHEL_9_STIG", "9")["stig_id"], "RHEL9")

    def test_find_evaluate_the_folder_or_the_script(self):
        d = tempfile.mkdtemp()
        self.assertEqual(scan.find_evaluate(d), "")
        os.makedirs(os.path.join(d, "Src", "Evaluate-STIG"))
        ps1 = os.path.join(d, "Src", "Evaluate-STIG", "Evaluate-STIG.ps1")
        open(ps1, "w").close()
        self.assertEqual(scan.find_evaluate(d), ps1)
        sh = os.path.join(d, "Evaluate-STIG_Bash.sh")
        open(sh, "w").close()
        self.assertEqual(scan.find_evaluate(d), sh)
        self.assertEqual(scan.find_evaluate(ps1), ps1)

    def es(self, res):
        d = tempfile.mkdtemp()
        sh = os.path.join(d, "Evaluate-STIG_Bash.sh")
        open(sh, "w").write(FAKE_ES % os.path.join(ROOT, "tests", "stig", "make_cklb.py"))
        json.dump(res, open(os.path.join(d, "results.json"), "w"))
        return d

    def test_run_it_then_compare_then_scanner_changes(self):
        es = self.es({"SV-257985": "open"})
        kept = tempfile.mkdtemp()
        m = FakeModule(dir=kept, evaluate=es, evaluate_select={"9": "RHEL9"}, evaluate_args=["--ScanType", "Classified"])
        with unittest.mock.patch.object(scan, "os_release", return_value={"VERSION_ID": "9.6", "PRETTY_NAME": "RHEL 9.6"}):
            out = scan.Scan(m).run()
        self.assertTrue(out["scanned"], out.get("why"))
        self.assertEqual((out["scanner"], out["stig_version"], out["benchmark"]), ("Evaluate-STIG", "V2R10", "RHEL_9_STIG"))
        self.assertEqual(open(os.path.join(es, "args.txt")).read().split()[:2], ["--Output", "CKLB"])
        self.assertIn("--SelectSTIG RHEL9 --ScanType Classified", open(os.path.join(es, "args.txt")).read())
        self.assertTrue(out["saved"]["results"].endswith("_results.cklb.gz"))
        self.assertNotIn("report", out["saved"])
        self.assertEqual(out["previous"], {})
        json.dump({}, open(os.path.join(es, "results.json"), "w"))
        with unittest.mock.patch.object(scan, "os_release", return_value={"VERSION_ID": "9.6"}):
            out2 = scan.Scan(m).run()
        self.assertEqual(out2["previous"]["items"]["SV-257985"], "fail")
        self.assertIn("SV-257883", out2["passed_before"])
        # an OpenSCAP summary before it: another scanner - not compared with
        for f in os.listdir(kept):
            if f.endswith("_summary.json"):
                j = json.load(open(os.path.join(kept, f)))
                j["scanner"] = "OpenSCAP"
                json.dump(j, open(os.path.join(kept, f), "w"))
        with unittest.mock.patch.object(scan, "os_release", return_value={"VERSION_ID": "9.6"}):
            out3 = scan.Scan(m).run()
        self.assertEqual((out3["previous"], out3["passed_before"], out3["scanner_before"]), ({}, {}, "OpenSCAP"))

    def test_read_what_it_wrote_and_why_not(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "WEB01", "Checklist"))
        m = FakeModule(dir=tempfile.mkdtemp(), evaluate_read=d)
        with unittest.mock.patch.object(scan, "os_release", return_value={"VERSION_ID": "9.6"}):
            self.assertIn("no Evaluate-STIG checklist", scan.Scan(m).run()["why"])
            make_cklb.write(os.path.join(d, "WEB01", "Checklist", "old.ckl"), {"SV-257985": "open"}, "V2R9", "ckl")
            os.utime(os.path.join(d, "WEB01", "Checklist", "old.ckl"), (1, 1))
            make_cklb.write(os.path.join(d, "WEB01", "Checklist", "new.cklb"), {}, "V2R10")
            out = scan.Scan(m).run()
            self.assertEqual((out["stig_version"], out["checklist"], out["counts"].get("fail", 0)), ("V2R10", "new.cklb", 0))
            self.assertTrue(out["checklist_time"])
            m.params["benchmarks"] = {"9": "RHEL_9_STIG"}
        with unittest.mock.patch.object(scan, "os_release", return_value={"VERSION_ID": "8.10"}):
            self.assertIn("no checklist of the RHEL 8 STIG", scan.Scan(m).run()["why"])
        m = FakeModule(dir=tempfile.mkdtemp(), evaluate="/nowhere/Evaluate-STIG")
        self.assertIn("Evaluate-STIG is not at /nowhere/Evaluate-STIG", scan.Scan(m).run()["why"])
        self.assertIn("check_stig_evaluate is not set", scan.Scan(FakeModule()).run()["why"])

    SAID = """Scanning...
ERROR: 'WEB01_x_V6R7_20261030-131517.cklb' : failed schema validation. Moving to %(bad)s.
Scan-RHEL8_Checks (Get-V244531) : Failed. See Evaluate-STIG.log for details.
Scan-RHEL8_Checks (Get-V244532) : Failed. See Evaluate-STIG.log for details.
ERROR: 'WEB01_RHEL9_V2R10_20261030-132053.cklb' : failed schema validation. Moving to %(bad)s.
Please report issues to https://example.mil/evaluate-stig/-/issues
Done!
Total checklists in Results Directory : 0"""

    def test_what_it_said(self):
        t = scan.evaluate_said(self.SAID % {"bad": "/tmp/Evaluate-STIG/Bad_CKL"})
        self.assertEqual(t["rejected"], ["/tmp/Evaluate-STIG/Bad_CKL/WEB01_x_V6R7_20261030-131517.cklb",
                                         "/tmp/Evaluate-STIG/Bad_CKL/WEB01_RHEL9_V2R10_20261030-132053.cklb"])
        self.assertEqual(t["failed"], ["V-244531", "V-244532"])
        self.assertEqual(len(t["lines"]), 5)
        self.assertEqual(scan.evaluate_said("Done!"), {"rejected": [], "failed": [], "log": "", "lines": []})
        wrapped = scan.evaluate_said("ERROR: 'WEB01_RHEL9_V2R10_20261030-132053.cklb' : failed schema validation. Moving to \n/tmp/Evaluate-STIG/Bad_CKL.")
        self.assertEqual(wrapped["rejected"], ["/tmp/Evaluate-STIG/Bad_CKL/WEB01_RHEL9_V2R10_20261030-132053.cklb"], "the line wrapped before the folder")

    def test_its_schema_check_rejected_the_checklist(self):
        d = tempfile.mkdtemp()
        bad = os.path.join(d, "Evaluate-STIG", "Bad_CKL")
        os.makedirs(bad)
        open(os.path.join(d, "Evaluate-STIG", "Evaluate-STIG.log"), "w").close()
        make_cklb.write(os.path.join(bad, "WEB01_RHEL9_V2R10_20261030-132053.cklb"), {"SV-257985": "open"}, "V2R10")
        sh = os.path.join(d, "Evaluate-STIG_Bash.sh")
        with open(sh, "w") as f:
            f.write("#!/bin/bash\ncat <<'EOT'\n%s\nEOT\n" % (self.SAID % {"bad": bad}))
        m = FakeModule(dir=tempfile.mkdtemp(), evaluate=sh)
        with unittest.mock.patch.object(scan, "os_release", return_value={"VERSION_ID": "9.6"}):
            out = scan.Scan(m).run()
            self.assertTrue(out["scanned"], out.get("why"))
            self.assertEqual((out["counts"]["fail"], out["evaluate_failed"]), (1, ["V-244531", "V-244532"]))
            self.assertEqual(out["evaluate_rejected"], [os.path.join(bad, "WEB01_RHEL9_V2R10_20261030-132053.cklb")])
            c = sf.stig_compare(out, {}, "web01", scanner="evaluate_stig")
            self.assertIn("failed Evaluate-STIG's own schema check (moved to %s) - read anyway; its checks that failed: "
                          "V-244531, V-244532 - see %s" % (bad, os.path.join(d, "Evaluate-STIG", "Evaluate-STIG.log")), c["row"]["cells"][1])
            self.assertIn("  Evaluate-STIG: its checklist failed", "\n".join(sf.stig_lines(c)))
            m.params["evaluate_rejected"] = False
            out = scan.Scan(m).run()
        self.assertFalse(out["scanned"])
        self.assertEqual(out["why"], "Evaluate-STIG wrote no checklist (exit 0): its schema check rejected WEB01_x_V6R7_20261030-131517.cklb, "
                         "WEB01_RHEL9_V2R10_20261030-132053.cklb (moved to %s; check_stig_evaluate_rejected: true reads them); its checks "
                         "that failed: V-244531, V-244532 - see %s" % (bad, os.path.join(d, "Evaluate-STIG", "Evaluate-STIG.log")))
        no = sf.stig_compare(out, {}, "web01", scanner="evaluate_stig")
        self.assertEqual(no["findings"][0]["hint"], "less " + os.path.join(d, "Evaluate-STIG", "Evaluate-STIG.log"))
        m.params.update(evaluate_rejected=True, evaluate_output="CKL")
        os.remove(os.path.join(bad, "WEB01_RHEL9_V2R10_20261030-132053.cklb"))
        with unittest.mock.patch.object(scan, "os_release", return_value={"VERSION_ID": "9.6"}):
            self.assertIn("rejected WEB01_x_V6R7_20261030-131517.cklb, WEB01_RHEL9_V2R10_20261030-132053.cklb (moved to %s, not there any more)"
                          % bad, scan.Scan(m).run()["why"])

    def test_compare_and_report(self):
        st = scan.pick_stig(scan.read_checklist(self.cklb({"SV-257821": "open"})), "RHEL_9_STIG", "9")
        its = scan.checklist_items(st)
        sc = {"scanned": True, "items": its, "counts": scan.counts(its), "scanner": "Evaluate-STIG", "stig_version": "V2R10",
              "previous": {"time": "2026-10-01T10:00:00Z", "items": {"SV-257821": "pass"}}, "passed_before": {"SV-257821": "2026-10-01T10:00:00Z"},
              "saved": {"results": "/var/lib/site-automation/stig/x_results.cklb.gz"}, "profile_title": "Red Hat Enterprise Linux 9"}
        c = sf.stig_compare(sc, sm(SV_257821=("pass", "accepted")), "web01", stigman_used=True, scanner="evaluate_stig")
        self.assertEqual([r["key"] for r in c["drift"]], ["SV-257821"])
        self.assertEqual(c["drift"][0]["check"], "")
        self.assertIn("x_results.cklb.gz (STIG Manager imports it)", c["findings"][0]["note"])
        self.assertEqual(c["findings"][0]["hint"], "/var/lib/site-automation/stig/x_results.cklb.gz")
        self.assertEqual(c["row"]["cells"][1], "V2R10 (Evaluate-STIG)")
        self.assertEqual(c["row"]["cells"][-1], "/var/lib/site-automation/stig/x_results.cklb.gz")
        cards, _ = sf.stig_cards([c])
        self.assertIn("What Evaluate-STIG found:", cards[0]["next"])
        self.assertIn("SV-257821 is not set as required", cards[0]["code"])
        c2 = sf.stig_compare(dict(sc, scanner_before="OpenSCAP"), {}, "web01")
        self.assertIn("first scan with it, after OpenSCAP", c2["row"]["cells"][1])
        no = sf.stig_compare({"scanned": False, "why": "Evaluate-STIG is not at /x", "evaluate": ""}, {}, "web01", scanner="evaluate_stig")
        self.assertIn("Check Evaluate-STIG on the host", no["findings"][0]["note"])
        self.assertEqual(sf.stig_compare({"scanned": False, "why": "x"}, {}, "web01")["findings"][0]["hint"], "rpm -q openscap-scanner scap-security-guide")


def win_scan(key="Windows_Server_2022_STIG", label="Microsoft Windows Server 2022", **results):
    """A Windows checklist as win_stig.ps1 gives it: the scan before and when each rule last passed on the items."""
    its = []
    for k, v in sorted(results.items()):
        now, prev, passed = (v + (None, None))[:3]
        it = {"key": k.replace("_", "-"), "v": "V-" + k[3:], "rule": k.replace("_", "-") + "r1_rule", "stigid": "WN22-00-" + k[3:], "severity": "high",
              "result": now}
        if now == "fail":
            it.update(title="Rule " + k, details="Found: it is not set.")
        if prev:
            it["prev"] = prev
        if passed:
            it["passed"] = passed
        its.append(it)
    return {"scanned": True, "key": key, "benchmark": key.split("_Default")[0], "label": label, "profile_title": label, "stig_version": "V2R4",
            "scanner": "Evaluate-STIG", "platform": "windows", "history": "items", "items": its, "counts": scan.counts(its),
            "site": "Default Web Site" if "Default_Web_Site" in key else "", "previous": {"time": "2026-10-01T10:00:00Z"}, "saved": {"dir": "C:\\ProgramData\\x", "results": "C:\\ProgramData\\x\\1_checklist.cklb"}}


class FakeClient(object):
    def __init__(self, assets, reviews, rules=None):
        self.assets, self.reviews, self.rules, self.asked = assets, reviews, rules or {}, []

    def get(self, path, **q):
        self.asked.append((path, q))
        if path == "/collections":
            return [{"collectionId": "1", "name": "Windows Servers"}]
        if path == "/assets":
            return [a for a in self.assets if "benchmarkId" not in q or q["benchmarkId"] in [x["benchmarkId"] for x in a["stigs"]]]
        if path.startswith("/stigs/"):
            return self.rules.get(path.split("/")[2], [])
        # a review with a revision is of an earlier revision's rule only: with rules=all, as STIG Manager
        every = q.get("rules") == "all"
        return [dict(r, stigs=[{"benchmarkId": q.get("benchmarkId"), "revisionStr": r.get("revision") or "V2R9", "isDefault": not r.get("revision")}])
                if every else r for r in self.reviews.get(path.split("/")[-1], [])
                if r.get("benchmarkId", q.get("benchmarkId")) == q.get("benchmarkId") and (every or not r.get("revision"))]


class Windows(unittest.TestCase):
    """Several checklists per host (Windows, roles/win_check_stig): one result each, named for its STIG."""

    def test_history_on_the_items_label_and_finding_ids(self):
        c = sf.stig_compare(win_scan(SV_1=("fail", "pass", "2026-10-01T10:00:00Z"), SV_2=("fail",), SV_3=("pass", "fail", "2026-09-01T10:00:00Z")),
                            sm(SV_1=("pass", "accepted"), SV_2=("pass", "accepted")), "win01", stigman_used=True)
        self.assertEqual(c["label"], "win01 - Microsoft Windows Server 2022")
        self.assertEqual([r["key"] for r in c["drift"]], ["SV-1"])
        self.assertEqual([r["key"] for r in c["disagree"]], ["SV-2"], "never passed: disagree, not drift")
        self.assertEqual([r["key"] for r in c["fixed"]], ["SV-3"])
        self.assertEqual([f["id"] for f in c["findings"]], ["stig:drift:Windows_Server_2022_STIG"])
        self.assertTrue(c["findings"][0]["summary"].startswith("Microsoft Windows Server 2022: 1 STIG rule"))
        self.assertEqual(c["drift"][0]["details"], "Found: it is not set.")
        self.assertEqual(c["row"]["cells"][0], "win01 - Microsoft Windows Server 2022")
        self.assertEqual(c["row"]["cells"][-1], "C:\\ProgramData\\x\\1_checklist.cklb")
        lines = sf.stig_lines(c)
        self.assertTrue(lines[0].startswith("win01 - Microsoft Windows Server 2022: "))

    def test_a_stig_the_asset_does_not_have_is_no_finding(self):
        c = sf.stig_compare(win_scan(SV_1=("fail", "pass", "2026-10-01T10:00:00Z")), {"status": "no_stig", "why": "the asset win01 does not have it"},
                            "win01", stigman_used=True)
        self.assertEqual([f["id"] for f in c["findings"]], ["stig:new:Windows_Server_2022_STIG"])
        self.assertIn("not compared: the asset win01 does not have it", c["row"]["cells"][2])
        lin = sf.stig_compare(scanned({}), {"status": "no_stig", "why": "x"}, "web01", stigman_used=True)
        self.assertEqual([f["id"] for f in lin["findings"]], ["stig:stigman"], "Linux: its one STIG not in STIG Manager is still a finding")

    def test_one_not_found_finding_per_host(self):
        a = sf.stig_compare(win_scan(), {"status": "not_found", "why": "no asset named win01"}, "win01", stigman_used=True)
        b = sf.stig_compare(win_scan("IIS_10-0_Site_STIG_Default_Web_Site", "Microsoft IIS 10.0 Site - Default Web Site"),
                            {"status": "not_found", "why": "no asset named win01-Default Web Site-NA"}, "win01", stigman_used=True)
        self.assertIn("<host>-<site>-<instance>", b["findings"][0]["note"])
        self.assertEqual([f["id"] for f in sf.stig_findings([a, b])], ["stig:stigman"])
        found = sf.stig_compare(win_scan(), sm(), "win01", stigman_used=True)
        self.assertEqual(sf.stig_findings([found, b]), [], "the host's asset is there: its web site's not being there is no finding")

    def test_not_scanned_says_what_to_set(self):
        c = sf.stig_compare({"scanned": False, "why": "Evaluate-STIG was not found on the server", "platform": "windows"}, {}, "win01", scanner="evaluate_stig")
        self.assertEqual([f["id"] for f in c["findings"]], ["stig:not-scanned"])
        self.assertIn("win_check_stig_evaluate", c["findings"][0]["note"])
        self.assertIn("Evaluate-STIG.ps1", c["findings"][0]["hint"])
        self.assertEqual(c["row"]["cells"][0], "win01")

    def test_report_names_each_checklist_and_counts_hosts(self):
        a = sf.stig_compare(win_scan(SV_2=("fail",)), sm(SV_2=("pass", "accepted")), "win01", stigman_used=True)
        b = sf.stig_compare(win_scan("IIS_10-0_Site_STIG_Default_Web_Site", "Microsoft IIS 10.0 Site - Default Web Site", SV_5=("fail",)),
                            sm(SV_5=("pass", "accepted")), "win01", stigman_used=True)
        secs = sf.stig_report_sections([a, b])
        dis = [s for s in secs if s["title"].startswith("3.")][0]
        self.assertEqual(sorted(r[0] for r in dis["rows"]), ["win01 - Microsoft IIS 10.0 Site - Default Web Site", "win01 - Microsoft Windows Server 2022"])
        card = [s for s in secs if s["title"].startswith("No drift")][0]
        self.assertIn("on 1 host ", card["lines"][0], "two checklists of one server are one host")

    def test_stig_manager_one_entry_per_checklist(self):
        assets = [{"assetId": "21", "name": "win01", "fqdn": "", "stigs": ["Windows_Server_2022_STIG"]},
                  {"assetId": "22", "name": "win01-Default Web Site-NA", "fqdn": "", "stigs": ["IIS_10-0_Site_STIG"]}]
        for a in assets:
            a["stigs"] = [{"benchmarkId": b} for b in a["stigs"]]
        client = FakeClient(assets, {"21": [{"ruleId": "SV-1r1_rule", "result": "pass", "status": {"label": "accepted"}}]})
        p = {"collections": [], "hosts": [
            {"id": "win01|Windows_Server_2022_STIG", "host": "win01", "names": ["win01"], "benchmark": "Windows_Server_2022_STIG"},
            {"id": "win01|IIS", "host": "win01", "names": ["win01-Default Web Site-NA"], "benchmark": "IIS_10-0_Site_STIG"},
            {"id": "win01|MS_Edge_STIG", "host": "win01", "names": ["win01"], "benchmark": "MS_Edge_STIG"},
            {"id": "win02|MS_Edge_STIG", "host": "win02", "names": ["win02"], "benchmark": "MS_Edge_STIG"}]}
        out = smr.read(p, client)
        self.assertEqual(out["win01|Windows_Server_2022_STIG"]["status"], "found")
        self.assertEqual(out["win01|Windows_Server_2022_STIG"]["reviews"]["SV-1"]["result"], "pass")
        self.assertEqual(out["win01|IIS"]["asset"], "win01-Default Web Site-NA")
        self.assertEqual(out["win01|MS_Edge_STIG"]["status"], "no_stig")
        self.assertIn("does not have MS_Edge_STIG assigned", out["win01|MS_Edge_STIG"]["why"])
        self.assertEqual(out["win02|MS_Edge_STIG"]["status"], "not_found")
        self.assertTrue(all(path in ("/collections", "/assets") or "/reviews/" in path for path, _q in client.asked))


def sm_list(bench="RHEL_9_STIG", rules=("SV-1", "SV-2", "SV-3", "SV-4", "SV-5"), **reviews):
    """One checklist in STIG Manager with its revision's rules (site_stigman_read with rules: true)."""
    c = sm(**reviews)
    c.update(benchmark=bench, revision="V2R7", rules=dict((k, {"v": "V-" + k[3:], "rule": k + "r1_rule", "version": "X-" + k[3:], "severity": "medium",
                                                                "title": "Rule " + k}) for k in rules))
    return c


def recheck_scan(**results):
    """The recheck's scan (site_stig_scan with vulns, history false): only the rules asked for."""
    its = [{"key": k.replace("_", "-"), "v": "V-" + k[3:], "rule": k.replace("_", "-") + "r1_rule", "stigid": "", "severity": "medium", "result": v,
            "details": "Found: it is not set." if v == "fail" else ""} for k, v in sorted(results.items())]
    return {"scanned": True, "scanner": "Evaluate-STIG", "stig_version": "V2R10", "items": its, "selected": True, "vulns_asked": len(its),
            "saved": {"results": "/var/lib/site-automation/stig/recheck/latest-checklist.cklb"}}


class Recheck(unittest.TestCase):
    """The STIG recheck (playbooks/stig_recheck.yml, win_stig_recheck.yml): what STIG Manager has Open or Not Reviewed, checked again."""

    def test_asked_open_and_not_reviewed(self):
        c = sm_list(SV_1=("fail", "accepted"), SV_2=("pass", "accepted"), SV_3=("pass", "rejected"), SV_4=("notchecked", "saved"))
        asked = sf.recheck_asked(c)
        self.assertEqual(dict((k, w) for k, (w, _r) in asked.items()), {"SV-1": "open", "SV-3": "not_reviewed", "SV-4": "not_reviewed", "SV-5": "not_reviewed"})
        self.assertEqual(list(sf.recheck_asked(c, which=["open"])), ["SV-1"])
        self.assertEqual(list(sf.recheck_asked(c, statuses=["accepted"], which=["open"])), ["SV-1"])
        self.assertEqual(list(sf.recheck_asked(sm_list(SV_1=("fail", "saved")), statuses=["accepted"], which=["open"])), [], "saved does not count here")
        self.assertEqual(sf.stig_recheck_vulns(c), ["V-1", "V-3", "V-4", "V-5"])
        self.assertEqual(sf.stig_recheck_vulns({"status": "not_found"}), [])
        no_rules = sm(SV_1=("fail", "accepted"), SV_2=("pass", "accepted"))
        self.assertEqual(sf.stig_recheck_vulns(no_rules), ["V-1"], "without the revision's rules: only what has a review")

    def test_sorted_linux(self):
        c = sm_list(SV_1=("fail", "accepted"), SV_2=("fail", "saved"), SV_3=("fail", "accepted"), SV_4=("pass", "rejected"))
        out = sf.stig_recheck(c, [recheck_scan(SV_1="pass", SV_2="fail", SV_3="notapplicable", SV_4="fail", SV_5="notchecked")], "web01")
        self.assertEqual(len(out), 1)
        r = out[0]
        self.assertEqual([x["key"] for x in r["fixed"]], ["SV-1"])
        self.assertEqual([x["key"] for x in r["still_open"]], ["SV-2"])
        self.assertEqual([x["key"] for x in r["now_na"]], ["SV-3"])
        self.assertEqual([x["key"] for x in r["answered"]], ["SV-4"])
        self.assertEqual([(x["key"], x["no_answer"]) for x in r["by_hand"]], [("SV-5", "Not Reviewed by the scan")])
        self.assertEqual((r["asked_open"], r["asked_not_reviewed"]), (3, 2))
        self.assertEqual(r["findings"], [], "report only")
        self.assertEqual(r["row"]["status"], "info")
        self.assertEqual(r["row"]["cells"][10], "only these rules (-SelectVuln)")
        lines = sf.stig_recheck_lines(r)
        self.assertIn("web01: 3 Open, 2 Not Reviewed in STIG Manager - fixed 1, now n/a 1, answered 1, still open 1, by hand 1", lines[0])
        self.assertIn("  ANSWER V-4 (X-4) CAT II Rule SV-4 - set Open", lines)
        self.assertEqual(sf._ask_cell(r["answered"][0]), "Not a Finding (rejected, 2026-09-01)")

    def test_not_in_the_scan_ignored_and_in_full(self):
        c = sm_list(rules=("SV-1", "SV-2"), SV_1=("fail", "accepted"))
        s = dict(recheck_scan(SV_1="fail"), selected=False)
        r = sf.stig_recheck(c, [s], "web01", ignore=["V-1"])[0]
        self.assertEqual([x["key"] for x in r["ignored"]], ["SV-1"])
        self.assertEqual([x["no_answer"] for x in r["by_hand"]], ["not in the scan's checklist (another STIG revision?)"])
        self.assertEqual(r["how"], "in full - this Evaluate-STIG has no -SelectVuln")
        o = dict(s, scanner="OpenSCAP", selected=True)
        r = sf.stig_recheck(c, [o], "web01")[0]
        self.assertEqual([x["no_answer"] for x in r["by_hand"]], ["no SCAP check for it (a manual check)"])
        self.assertEqual(r["how"], "only these rules (--rule)")

    def test_headline_says_what_is_open(self):
        """The recheck's report title and status: what is open - never "no findings" over open rules."""
        c = sm_list(SV_1=("fail", "accepted"), SV_2=("fail", "saved"))
        c["rules"]["SV-2"]["severity"] = "high"
        out = sf.stig_recheck(c, [recheck_scan(SV_1="pass", SV_2="fail", SV_3="fail", SV_4="pass", SV_5="notchecked")], "web01")
        h = sf.recheck_headline(out)
        self.assertEqual((h["text"], h["status"]), ("2 rules open (CAT I: 1), 3 status updates in STIG Manager, 1 for manual review", "critical"))
        rep = __import__("site_filters").findings_report({"web01": []}, "STIG recheck", [], ["stig"], sf.stig_recheck_sections(out))
        self.assertEqual((rep["title"], rep["status"]), ("STIG recheck: 2 rules open (CAT I: 1), 3 status updates in STIG Manager, 1 for manual review", "critical"))
        self.assertNotIn("Healthy", [b["label"] for b in rep["summary"]])
        nothing = sf.stig_recheck(sm_list(rules=("SV-1",), SV_1=("pass", "accepted")), [], "web01")
        self.assertEqual(sf.recheck_headline(nothing)["status"], "ok")
        fixed_only = sf.stig_recheck(sm_list(rules=("SV-1",), SV_1=("fail", "accepted")), [recheck_scan(SV_1="pass")], "web01")
        self.assertEqual(sf.recheck_headline(fixed_only), {"text": "0 rules open, 1 status update in STIG Manager", "status": "info",
                                                           "open": 0, "open_status": "ok"})

    def test_nothing_not_found_not_scanned(self):
        r = sf.stig_recheck(sm_list(rules=("SV-1",), SV_1=("pass", "accepted")), [], "web01")
        self.assertTrue(r[0]["nothing"])
        self.assertEqual(r[0]["findings"], [])
        self.assertIn("nothing to recheck", sf.stig_recheck_lines(r[0])[0])
        r = sf.stig_recheck({"status": "not_found", "why": "no asset named web01"}, [], "web01")
        self.assertEqual([f["id"] for f in r[0]["findings"]], ["stig:stigman"])
        self.assertIn("NOT RECHECKED - nothing to recheck: no asset named web01", sf.stig_recheck_lines(r[0])[0])
        r = sf.stig_recheck(sm_list(SV_1=("fail", "accepted")), [{"scanned": False, "why": "Evaluate-STIG is not at /x"}], "web01")
        self.assertEqual([f["id"] for f in r[0]["findings"]], ["stig:not-scanned"])
        self.assertIn("Evaluate-STIG is not at /x", r[0]["findings"][0]["summary"])
        self.assertEqual(r[0]["row"]["status"], "warning")
        self.assertEqual((r[0]["by_hand"], r[0]["asked_open"]), ([], 1), "not rechecked: counted, not sorted")

    def test_windows_each_checklist_its_scan(self):
        ws = dict(sm_list("Windows_Server_2022_STIG", rules=("SV-1", "SV-2"), SV_1=("fail", "accepted")), asset="win01", site="", instance="")
        iis = dict(sm_list("IIS_10-0_Site_STIG", rules=("SV-7",), SV_7=("fail", "accepted")), asset="win01-Default Web Site-NA", site="Default Web Site", instance="")
        dbx = dict(sm_list("MS_SQL_STIG", rules=("SV-9",)), asset="win01-NA-SQL1", site="", instance="SQL1")
        host = {"status": "found", "why": "", "collection": "Windows Servers", "asset": "win01", "checklists": [ws, iis, dbx]}
        self.assertEqual(sf.stig_recheck_vulns(host), ["V-1", "V-2", "V-7", "V-9"])
        a = dict(win_scan(SV_1=("pass",), SV_2=("fail",)), selected=True)
        b = dict(win_scan("IIS_10-0_Site_STIG_Default_Web_Site", "Microsoft IIS 10.0 Site - Default Web Site", SV_7=("fail",)), selected=True)
        out = sf.stig_recheck(host, [a, b], "win01")
        self.assertEqual([r["label"] for r in out], ["win01 - Microsoft Windows Server 2022", "win01 - Microsoft IIS 10.0 Site - Default Web Site",
                                                     "win01 - MS_SQL_STIG - SQL1"])
        self.assertEqual([x["key"] for x in out[0]["fixed"]], ["SV-1"])
        self.assertEqual([x["key"] for x in out[0]["answered"]], ["SV-2"])
        self.assertEqual([x["key"] for x in out[1]["still_open"]], ["SV-7"])
        self.assertFalse(out[2]["scanned"])
        self.assertIn("Evaluate-STIG wrote no checklist of MS_SQL_STIG", out[2]["why"])
        self.assertEqual(out[1]["stigman"]["asset"], "win01-Default Web Site-NA")
        secs = sf.stig_recheck_sections(out)
        self.assertEqual([s["title"][:2] for s in secs[:3]], ["1.", "2.", "3."])
        self.assertEqual([b["value"] for b in secs[0]["boxes"]], [2, 2, 0, 1], "open now (still open, answered Open), to update, by hand, not rechecked")
        self.assertEqual([b["label"] for b in secs[0]["boxes"]], ["Open findings", "Status updates", "Manual review", "Not rechecked"])
        self.assertEqual(secs[0]["boxes"][1]["note"], "no fix needed: the host passes, no longer applies, or was answered - only STIG "
                                                      "Manager's review changes (1 answered as a fail: also under Open findings)")
        self.assertEqual((secs[0]["headline"], secs[0]["headline_status"], secs[0]["summary_only"]),
                         ("2 rules open, 2 status updates in STIG Manager, 1 not rechecked", "warning", True))
        every = [s for s in secs if s["title"].startswith("Every host")][0]
        self.assertEqual(len(every["rows"]), 3)
        self.assertTrue(all(len(r) == len(every["columns"]) for r in every["rows"]))
        full = [s for s in secs if s["title"].startswith("What Evaluate-STIG found")][0]
        self.assertEqual(len(full["cards"]), 2)

    def test_nothing_to_update_section(self):
        r = sf.stig_recheck(sm_list(rules=("SV-1",), SV_1=("fail", "accepted")), [recheck_scan(SV_1="fail")], "web01")
        secs = sf.stig_recheck_sections(r)
        self.assertEqual([s["title"] for s in secs][:2], ["3. Still open - the scan agrees with STIG Manager", "Nothing to update in STIG Manager"])

    def test_stig_manager_every_and_rules(self):
        assets = [{"assetId": "21", "name": "win01", "fqdn": "", "stigs": [{"benchmarkId": "W", "revisionStr": "V2R3"}]},
                  {"assetId": "22", "name": "win01-Default Web Site-NA", "fqdn": "", "stigs": [{"benchmarkId": "I", "revisionStr": ""}]},
                  {"assetId": "23", "name": "win010-Default Web Site-NA", "fqdn": "", "stigs": [{"benchmarkId": "I"}]},
                  {"assetId": "24", "name": "win01-old", "fqdn": "", "stigs": [{"benchmarkId": "I"}]}]
        rules = {"W": [{"ruleId": "SV-1r2_rule", "groupId": "V-1", "version": "WN-1", "severity": "high", "title": "One"}],
                 "I": [{"ruleId": "SV-7r1_rule", "version": "IIS-7", "severity": "medium", "title": "Seven"}]}
        client = FakeClient(assets, {"21": [{"benchmarkId": "W", "ruleId": "SV-1r2_rule", "result": "fail", "status": {"label": "accepted"}}]}, rules)
        out = smr.read({"collections": [], "hosts": [{"host": "win01", "names": ["win01"], "every": True},
                                                     {"host": "lin01", "names": ["win01"], "benchmark": "W", "rules": True, "id": "lin"}]}, client)
        w = out["win01"]
        self.assertEqual(w["status"], "found")
        self.assertEqual([(c["asset"], c["benchmark"], c["site"], c["instance"]) for c in w["checklists"]],
                         [("win01", "W", "", ""), ("win01-Default Web Site-NA", "I", "Default Web Site", "")])
        self.assertEqual(w["checklists"][0]["rules"]["SV-1"]["v"], "V-1")
        self.assertEqual(w["checklists"][1]["rules"]["SV-7"]["v"], "V-7", "no groupId: from the SV id")
        self.assertEqual(w["checklists"][0]["reviews"]["SV-1"]["result"], "fail")
        self.assertEqual(out["lin"]["rules"]["SV-1"]["version"], "WN-1")
        self.assertIn(("/stigs/I/revisions/latest/rules", {}), client.asked, "no revision on the asset: the latest")
        self.assertEqual(len([p for p, _q in client.asked if p == "/stigs/W/revisions/V2R3/rules"]), 1, "each revision's rules read once")
        self.assertEqual(smr.tail_of("win01", "win01-NA-SQL1"), ("", "SQL1"))
        self.assertEqual(smr.tail_of("win01", "win01-My-Site-NA"), ("My-Site", ""))
        self.assertIsNone(smr.tail_of("win01", "win01-old"))
        self.assertIsNone(smr.tail_of("win01", "win010-a-b"))
        none = smr.read({"collections": [], "hosts": [{"host": "win09", "names": ["win09"], "every": True}]}, client)
        self.assertEqual(none["win09"]["status"], "not_found")

    def test_evaluate_call(self):
        self.assertEqual(scan.ps_call("/opt/es/Evaluate-STIG.ps1", ["--Output", "CKLB", "-SelectVuln", ["V-1", "V-2"], "--AnswerKey", "Site's"]),
                         "& '/opt/es/Evaluate-STIG.ps1' -Output 'CKLB' -SelectVuln 'V-1','V-2' -AnswerKey 'Site''s'")
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "Src", "Evaluate-STIG"))
        bash = os.path.join(d, "Evaluate-STIG_Bash.sh")
        ps1 = os.path.join(d, "Src", "Evaluate-STIG", "Evaluate-STIG.ps1")
        open(bash, "w").write("#!/bin/bash\n")
        self.assertEqual(scan.evaluate_ps1(bash), "")
        open(ps1, "w").write("param([string[]] $SelectVuln)\n")
        self.assertEqual(scan.evaluate_ps1(bash), ps1)
        self.assertTrue(scan.takes_select_vuln(ps1, bash))
        self.assertFalse(scan.takes_select_vuln(bash, os.path.join(d, "none")))

    def test_module_recheck_bash(self):
        d = tempfile.mkdtemp()
        es = os.path.join(d, "es")
        os.makedirs(es)
        open(os.path.join(es, "Evaluate-STIG_Bash.sh"), "w").write(FAKE_ES % os.path.join(ROOT, "tests", "stig", "make_cklb.py") + "# --SelectVuln\n")
        open(os.path.join(es, "results.json"), "w").write('{"SV-257883": "open"}')
        m = FakeModule(dir=os.path.join(d, "kept"), evaluate=es, vulns=["V-257883", "V-258176"], history=False)
        with unittest.mock.patch.object(scan, "os_release", return_value={"ID": "rhel", "VERSION_ID": "9.4", "PRETTY_NAME": "RHEL 9.4"}):
            r = scan.Scan(m).run()
        self.assertTrue(r["scanned"], r.get("why"))
        self.assertEqual(sorted((i["v"], i["result"]) for i in r["items"]), [("V-257883", "fail"), ("V-258176", "pass")])
        self.assertTrue(r["selected"])
        self.assertIn("--SelectVuln V-257883,V-258176", open(os.path.join(es, "args.txt")).read())
        self.assertEqual(r["saved"]["results"], os.path.join(d, "kept", "recheck", "latest-checklist.cklb"))
        self.assertEqual(os.listdir(os.path.join(d, "kept")), ["recheck"], "nothing kept with the drift report's scans")
        self.assertEqual(r["previous"], {})

    def test_module_recheck_select_vuln_refused(self):
        d = tempfile.mkdtemp()
        es = os.path.join(d, "es")
        os.makedirs(es)
        refuse = '[[ "$*" == *--SelectVuln* ]] && { echo "ERROR: --SelectVuln needs --SelectSTIG"; exit 1; }\n'
        open(os.path.join(es, "Evaluate-STIG_Bash.sh"), "w").write(FAKE_ES.replace('out=""; args="$*"', refuse + 'out=""; args="$*"')
                                                                   % os.path.join(ROOT, "tests", "stig", "make_cklb.py"))
        open(os.path.join(es, "results.json"), "w").write('{"SV-257883": "open"}')
        m = FakeModule(dir=os.path.join(d, "kept"), evaluate=es, vulns=["V-257883"], history=False, save=False)
        with unittest.mock.patch.object(scan, "os_release", return_value={"ID": "rhel", "VERSION_ID": "9.4", "PRETTY_NAME": "RHEL 9.4"}):
            r = scan.Scan(m).run()
        self.assertTrue(r["scanned"], r.get("why"))
        self.assertFalse(r["selected"])
        self.assertIn("with -SelectVuln it wrote no checklist (exit 1: ERROR: --SelectVuln needs --SelectSTIG), so it ran in full", r["select_note"])
        self.assertEqual([(i["v"], i["result"]) for i in r["items"]], [("V-257883", "fail")])
        self.assertEqual(sf._recheck_how(r), "in full - " + r["select_note"])


sys.path.insert(0, os.path.join(ROOT, "roles", "stigman_update", "library"))
import site_stigman_update as smu  # noqa: E402


class RevisionGuard(unittest.TestCase):
    """0.67.0: an answer to a rule that changed between the scan's STIG revision and STIG Manager's is never written; a
    review STIG Manager keeps for an earlier revision of the rule is said beside "no review"."""

    def setUp(self):
        c = sm_list(rules=("SV-1", "SV-2", "SV-3"), SV_1=("fail", "accepted"))
        c.update(revision="V2R9", collectionId="7", assetId="42",
                 earlier={"SV-3": {"result": "pass", "status": "accepted", "revision": "V2R8", "comment": True}})
        c["rules"]["SV-1"]["rule"], c["rules"]["SV-3"]["rule"] = "SV-1r2_rule", "SV-3r2_rule"     # changed in V2R9
        self.c = c

    def scan(self, version="V2R8", ids=True):
        s = dict(recheck_scan(SV_1="pass", SV_2="pass", SV_3="pass"), stig_version=version)
        for it in s["items"]:
            it["rule"] = it["rule"] if ids else ""
        return s

    def test_changed_rules_held_back(self):
        r = sf.stig_recheck(self.c, [self.scan()], "web01")[0]
        self.assertEqual([x["key"] for x in r["answered"]], ["SV-2"], "SV-2 did not change (r1 in both): written")
        self.assertEqual([x["key"] for x in r["fixed"]], [])
        self.assertEqual(([x["key"] for x in r["held"]], r["by_hand"], r["carried"]), (["SV-1", "SV-3"], [], []),
                         "0.69.0: held back - not Manual review")
        self.assertEqual(r["held"][0]["no_answer"], "the rule changed - the scan checked SV-1r1_rule, STIG Manager has SV-1r2_rule - the scan says "
                                                     "pass, not written (update the scan's STIG to V2R9, or review it)")
        self.assertEqual(r["revision_other"], "scanned with V2R8, STIG Manager has V2R9")
        self.assertEqual(r["findings"], [], "no finding: the check's job fails only to ask for the approval")
        self.assertEqual(r["changed"], 2)
        self.assertEqual(r["row"]["status"], "warning")
        self.assertIn("2 answers held back: the scan's STIG is older", r["row"]["cells"][1])
        self.assertEqual(r["row"]["cells"][7:9], ["0", "2"], "Manual review 0, Held back 2")
        lines = sf.stig_recheck_lines(r)
        self.assertIn("by hand 0, left out 0, held back 2; STIG Manager", lines[0])
        self.assertIn("  REVISION scanned with V2R8, STIG Manager has V2R9 - 2 answers held back: the rule changed since the scan's STIG - update it", lines)
        p = sf.stig_update_plan([r])
        self.assertEqual([x["v"] for x in p], ["V-2"], "never written: SV-1 (Open, passes the old check), SV-3")
        self.assertEqual(sf._ask_cell(r["held"][1]), "no review in V2R9 - V2R8: Not a Finding (accepted)")
        secs = sf.stig_recheck_sections([r])
        self.assertEqual([(b["label"], b["value"]) for b in secs[0]["boxes"]], [("Open findings", 0), ("Status updates", 1), ("Manual review", 0),
                                                                               ("Held back: scan's STIG older", 2), ("Not rechecked", 0)])
        self.assertIn("(scanned with V2R8, STIG Manager has V2R9): update Evaluate-STIG", secs[0]["boxes"][3]["note"])
        self.assertEqual(secs[0]["headline"], "0 rules open, 1 status update in STIG Manager, 2 held back (the scan's STIG is older)")
        held = [x for x in secs if x["title"] == "Held back - the scan's STIG is older than STIG Manager's"][0]
        self.assertEqual(len(held["rows"]), 2)
        every = [x for x in secs if x["title"].startswith("Every host")][0]
        self.assertTrue(all(len(row) == len(every["columns"]) for row in every["rows"]))
        self.assertEqual(sf._ask_cell(r["answered"][0]), "no review")

    def test_same_revision_or_no_rule_ids(self):
        r = sf.stig_recheck(self.c, [self.scan("V2R9", ids=False)], "web01")[0]
        self.assertEqual(([x["key"] for x in r["fixed"]], [x["key"] for x in r["answered"]], r["by_hand"]), (["SV-1"], ["SV-2", "SV-3"], []),
                         "the same revision, no rule ids: as before")
        r = sf.stig_recheck(self.c, [self.scan("V2R8", ids=False)], "web01")[0]
        self.assertEqual([x["key"] for x in r["held"]], ["SV-1", "SV-2", "SV-3"], "another revision, no rule ids to tell: none written")
        self.assertEqual(sf.stig_update_plan([r]), [])
        r = sf.stig_recheck(self.c, [self.scan("", ids=False)], "web01")[0]
        self.assertEqual(r["by_hand"], [], "the scan's revision unknown, no ids: as before")
        plan = sf.stig_update_plan(sf.stig_recheck(self.c, [self.scan("V2R9", ids=False)], "web01"))
        sec = sf.stig_update_sections(plan)[0]
        self.assertEqual([row[6] for row in sec["rows"]], ["Open (accepted)", "no review", "no review in V2R9 - V2R8: Not a Finding (accepted)"])

    def test_carry_forward(self):
        """0.69.0 check_stig_recheck_carry_forward: the earlier revision's counted answer that the old scan agrees with is
        written to the rule of STIG Manager's revision, said so; the rest stays held back."""
        self.c["earlier"]["SV-1"] = {"result": "pass", "status": "accepted", "revision": "V2R8"}     # Open in V2R9: never carried
        r = sf.stig_recheck(self.c, [self.scan()], "web01", carry_forward=True)[0]
        self.assertEqual(([x["key"] for x in r["carried"]], [x["key"] for x in r["held"]]), (["SV-3"], ["SV-1"]))
        self.assertEqual(r["carried"][0]["carried_from"], "V2R8")
        p = sf.stig_update_plan([r], day="2026-11-04")
        self.assertEqual([(x["v"], x["kind"], x["result"], x["ruleId"]) for x in p], [("V-2", "answered", "pass", "SV-2r1_rule"),
                                                                                     ("V-3", "carried", "pass", "SV-3r2_rule")])
        self.assertEqual(p[1]["comment"], "Carried forward by the STIG recheck (site-automation), approved in AAP: Not a Finding (accepted) in "
                                          "V2R8, and Evaluate-STIG on web01 on 2026-11-04 checked it against V2R8 - not against V2R9 "
                                          "(check_stig_recheck_carry_forward).")
        self.assertEqual(p[1]["line"], "web01 RHEL_9_STIG V-3: no review -> Not a Finding (submitted, carried forward from V2R8)")
        self.assertEqual([x["v"] for x in sf.stig_update_plan([r], kinds=["fixed"])], ["V-3"], "carried: switched on by its own setting")
        self.assertIn("  CARRY  V-3 (X-3) CAT II Rule SV-3 - set Not a Finding, carried forward from V2R8", sf.stig_recheck_lines(r))
        secs = sf.stig_recheck_sections([r])
        self.assertEqual(secs[0]["boxes"][1]["value"], 2, "Status updates: answered and carried")
        self.assertIn("4. Carried forward from an earlier revision (check_stig_recheck_carry_forward)", [x["title"] for x in secs])
        # not carried: the earlier review does not count, says another result, or the scan says something else
        for e, scan_says in (({"result": "pass", "status": "rejected", "revision": "V2R8"}, "pass"),
                             ({"result": "notapplicable", "status": "accepted", "revision": "V2R8"}, "pass"),
                             ({"result": "pass", "status": "accepted", "revision": "V2R8"}, "fail")):
            self.c["earlier"]["SV-3"] = e
            s = self.scan()
            s["items"] = [dict(it, result=scan_says) if it["key"] == "SV-3" else it for it in s["items"]]
            r = sf.stig_recheck(self.c, [s], "web01", carry_forward=True)[0]
            self.assertEqual(r["carried"], [], (e, scan_says))
        r = sf.stig_recheck(self.c, [self.scan()], "web01")[0]
        self.assertEqual(r["carried"], [], "off by default")

    def test_helpers(self):
        self.assertTrue(sf.other_revision("V2R8", "V2R9"))
        self.assertFalse(sf.other_revision("V2R9", "v2r9"))
        self.assertFalse(sf.other_revision("", "V2R9"))
        self.assertTrue(sf.rule_changed("SV-1r1_rule", "SV-1r2_rule", False))
        self.assertFalse(sf.rule_changed("SV-1r2_rule", "SV-1r2_rule", True), "the same rule id: the same check, whatever the revision")
        self.assertTrue(sf.rule_changed("", "SV-1r2_rule", True))
        self.assertFalse(sf.rule_changed("", "", False))

    def test_read_earlier_reviews(self):
        assets = [{"assetId": "11", "name": "web01", "fqdn": "", "stigs": [{"benchmarkId": "RHEL_8_STIG", "revisionStr": "V2R9"}]}]
        reviews = {"11": [{"ruleId": "SV-2r1_rule", "ruleIds": ["SV-2r1_rule"], "result": "pass", "status": {"label": "accepted"}},
                          {"ruleId": "SV-3r1_rule", "result": "pass", "status": {"label": "accepted"}, "comment": "ok", "revision": "V2R8"},
                          {"ruleId": "SV-3r0_rule", "result": "fail", "status": {"label": "accepted"}, "revision": "V2R7"},
                          {"ruleId": "SV-2r0_rule", "result": "fail", "status": {"label": "accepted"}, "revision": "V2R7"}]}
        client = FakeClient(assets, reviews, {"RHEL_8_STIG": [{"ruleId": "SV-%dr2_rule" % i, "groupId": "V-%d" % i} for i in (1, 2, 3)]})
        out = smr.read({"collections": [], "hosts": [{"host": "web01", "names": ["web01"], "benchmark": "RHEL_8_STIG", "rules": True}]}, client)
        h = out["web01"]
        self.assertEqual(sorted(h["reviews"]), ["SV-2"])
        self.assertEqual(h["earlier"], {"SV-3": dict(smr.reviews_of([reviews["11"][1]])["SV-3"], revision="V2R8")},
                         "the newest earlier revision's; SV-2 has a review in V2R9")
        self.assertIn(("/collections/1/reviews/11", {"benchmarkId": "RHEL_8_STIG", "rules": "all", "projection": ["rule", "stigs"]}),
                      client.asked)
        out = smr.read({"collections": [], "hosts": [{"host": "web01", "names": ["web01"], "benchmark": "RHEL_8_STIG"}]}, client)
        self.assertNotIn("earlier", out["web01"], "only with rules: true (the recheck)")


class Update(unittest.TestCase):
    """Workflow 37: the recheck's plan (stig_update_plan) and the write after the approval (site_stigman_update)."""

    def plan(self, **kw):
        c = sm_list(SV_1=("fail", "accepted"), SV_2=("fail", "saved"), SV_3=("fail", "accepted"), SV_4=("pass", "rejected"))
        c["reviews"]["SV-1"].update(comment=True, rule="SV-1r0_rule")      # its review on an earlier revision, with a comment
        c.update(collectionId="7", assetId="42")
        out = sf.stig_recheck(c, [dict(recheck_scan(SV_1="pass", SV_2="fail", SV_3="notapplicable", SV_4="fail", SV_5="notchecked"),
                                       time="2026-11-02T08:00:00Z")], "web01")
        return sf.stig_update_plan(out, day="2026-11-02", **kw)

    def test_plan(self):
        p = self.plan()
        self.assertEqual([(x["v"], x["kind"], x["result"]) for x in p], [("V-1", "fixed", "pass"), ("V-3", "now_na", "notapplicable"), ("V-4", "answered", "fail")])
        one = p[0]
        self.assertEqual((one["collectionId"], one["assetId"], one["ruleId"], one["status"]), ("7", "42", "SV-1r0_rule", "submitted"))
        self.assertEqual(one["was"], {"result": "fail", "status": "accepted", "ts": "2026-09-01T00:00:00Z"})
        self.assertIsNone(one["comment"], "a review with a comment keeps it")
        self.assertTrue(p[2]["comment"].startswith("Set by the STIG recheck (site-automation), approved in AAP: Evaluate-STIG checked it again on web01 on 2026-11-02"))
        self.assertEqual(p[2]["detail"], "Found: it is not set.")
        self.assertEqual(p[0]["engine"], {"type": "script", "product": "Evaluate-STIG", "time": "2026-11-02T08:00:00Z"})
        self.assertEqual(p[0]["line"], "web01 RHEL_9_STIG V-1: Open -> Not a Finding (submitted)")
        self.assertEqual([x["v"] for x in self.plan(kinds=["fixed"], status="saved")], ["V-1"])
        self.assertEqual(self.plan(kinds=["fixed"], status="saved")[0]["status"], "saved")
        self.assertTrue(all(len(x["detail"]) <= 60 for x in self.plan(detail_max=60)))
        self.assertEqual(sf.stig_update_plan([{"scanned": True, "fixed": [{"rule_id": "SV-1r1_rule"}]}]), [], "no ids: nothing written")
        sec = sf.stig_update_sections(p)[0]
        self.assertEqual((sec["title"], len(sec["rows"]), sec["rows"][0][9]), ("Approve writes these reviews to STIG Manager", 3, "kept"))
        self.assertEqual(sf.stig_update_sections([])[0]["title"], "Nothing to write to STIG Manager")

    def test_plan_windows_each_checklist_its_asset(self):
        site = sm_list(bench="IIS_10-0_Site_STIG", SV_1=("fail", "accepted"))
        site.update(asset="win01-Default Web Site-NA", assetId="9", site="Default Web Site", instance="")
        server = sm_list(bench="MS_Windows_Server_2022_STIG", SV_2=("fail", "accepted"))
        server.update(asset="win01", assetId="8")
        smh = {"status": "found", "collection": "Windows Servers", "collectionId": "3", "asset": "win01", "checklists": [server, site]}
        scans = [dict(recheck_scan(SV_2="pass"), platform="windows", benchmark="MS_Windows_Server_2022_STIG", label="Windows Server 2022"),
                 dict(recheck_scan(SV_1="pass"), platform="windows", benchmark="IIS_10-0_Site_STIG", site="Default Web Site", instance="",
                      label="IIS 10.0 Site - Default Web Site")]
        p = sf.stig_update_plan(sf.stig_recheck(smh, scans, "win01"))
        self.assertEqual(sorted((x["collectionId"], x["assetId"], x["benchmark"], x["v"]) for x in p),
                         [("3", "8", "MS_Windows_Server_2022_STIG", "V-2"), ("3", "9", "IIS_10-0_Site_STIG", "V-1")])

    def test_write(self):
        p = self.plan()
        p.append(dict(p[0], ruleId="SV-9/../users", line="bad"))

        class C(object):
            def __init__(self):
                self.db = {"SV-1r0_rule": {"result": "fail", "status": {"label": "accepted"}, "ts": "2026-09-01T00:00:00Z"},
                           "SV-3r1_rule": {"result": "notapplicable", "status": {"label": "submitted"}, "ts": "2026-10-01T00:00:00Z"},
                           "SV-4r1_rule": {"result": "pass", "status": {"label": "rejected"}, "ts": "2026-09-01T00:00:00Z"}}
                self.posts = []

            def review(self, c, a, rule):
                return self.db.get(rule)

            def attachments(self, c, a, rule):
                return self.files.get(rule, (0, []))

            files = {}

            def write(self, c, a, reviews):
                self.posts.append((c, a, reviews))
                for r in reviews:
                    if r["ruleId"] == "SV-4r1_rule":
                        continue
                    self.db[r["ruleId"]] = {"result": r["result"], "status": {"label": r["status"]}, "ts": "now"}
                return {"affected": {"updated": 1, "inserted": 0}, "rejected": [{"ruleId": "SV-4r1_rule", "reason": "comment is required"}]}
        c = C()
        items = smu.update({"plan": p}, c, False)
        self.assertEqual([i["state"] for i in items], ["written", "already so", "rejected", "bad"])
        self.assertEqual([r["ruleId"] for r in c.posts[0][2]], ["SV-1r0_rule", "SV-4r1_rule"])
        self.assertEqual((c.posts[0][0], c.posts[0][1]), ("7", "42"))
        self.assertIsNone(c.posts[0][2][0]["comment"])
        self.assertEqual(sorted(c.posts[0][2][0]), ["comment", "detail", "result", "resultEngine", "ruleId", "status"])
        c = C()
        c.db["SV-1r0_rule"]["ts"] = "2026-10-30T00:00:00Z"                  # a person changed it since the check
        items = smu.update({"plan": p[:1]}, c, False)
        self.assertEqual((items[0]["state"], c.posts), ("changed since", []))
        c = C()
        self.assertEqual([i["state"] for i in smu.update({"plan": p[:3]}, c, True)], ["would write", "already so", "would write"])
        self.assertEqual(c.posts, [], "check mode writes nothing")
        r = sf.stig_update_report(items, False, "", 1)
        self.assertEqual((r["status"], r["sections"][0]["rows"][0][6]), ("warning", "changed since"))
        c = C()
        c.files = {"SV-1r0_rule": (1, ["poam.pdf"])}                        # a person attached evidence to it
        items = smu.update({"plan": p[:1]}, c, False)
        self.assertEqual((items[0]["state"], c.posts), ("has attachments", []))
        self.assertIn("1 attachment(s) in STIG Manager: poam.pdf - left as it is", items[0]["why"])
        self.assertEqual(smu.update({"plan": p[:1], "skip_attachments": False}, C(), False)[0]["state"], "written")
        r = sf.stig_update_report(items, False, "", 1)
        self.assertEqual(dict((s["label"], s["value"]) for s in r["summary"])["Has attachments (left)"], 1)

    def test_writes_only_reviews(self):
        """The module's only requests: the token, GET of one review (and of its metadata keys, for its attachments), POST of
        an asset's reviews; GET /user - the client's own grants (0.76.0, the checklist import's items)."""
        with open(os.path.join(ROOT, "roles", "stigman_update", "library", "site_stigman_update.py")) as f:
            src = f.read()
        self.assertEqual(sorted(set(re.findall(r'"%s(/[^"]*)"', src))), ["/assets?%s", "/collections/%s/reviews/%s", "/collections/%s/reviews/%s/%s",
                                                                        "/collections/%s/reviews/%s/%s/metadata/keys", "/user"])
        self.assertEqual(re.findall(r'get_method = lambda: "?(\w+)"?', src), ["method", "POST"])
        self.assertEqual(re.findall(r'_send\("(\w+)", "([^"]+)"', src), [("POST", "/assets?projection=stigs")], "0.72.0: a new asset only")
        for word in ("DELETE", "PATCH", "PUT", "/users", "/grants", "/stigs"):
            self.assertNotIn(word, src)


class NewAsset(unittest.TestCase):
    """0.72.0, check_stig_update_create_assets: a host with no asset - every rule, the asset planned, created once approved."""

    def sm(self):
        rules = sm_list()["rules"]
        return {"status": "not_found", "why": "no asset in RHEL Servers: one to create once approved", "rules": rules,
                "create": {"name": "web01", "fqdn": "web01.example.mil", "collection": "RHEL Servers", "collectionId": "7",
                           "benchmark": "RHEL_9_STIG", "revision": "V2R7"}}

    def scan(self):
        return dict(recheck_scan(SV_1="pass", SV_2="fail", SV_3="notapplicable", SV_4="notchecked"), selected=False, vulns_asked=0)

    def test_every_rule_and_the_report(self):
        self.assertEqual(sf.stig_recheck_vulns(self.sm(), which=["open"]), ["V-1", "V-2", "V-3", "V-4", "V-5"], "every rule, whatever is asked")
        self.assertEqual(sf.stig_recheck_vulns({"status": "not_found", "why": "x"}), [])
        out = sf.stig_recheck(self.sm(), [self.scan()], "web01.example.mil", which=["open"])
        self.assertEqual(len(out), 1)
        c = out[0]
        self.assertEqual((c["create"]["name"], c["stigman"]["status"], c["assetId"], c["how"]), ("web01", "new", "", "in full - a new asset"))
        self.assertEqual(([r["v"] for r in c["answered"]], [r["v"] for r in c["by_hand"]]), (["V-1", "V-2", "V-3"], ["V-4", "V-5"]))
        self.assertTrue(c["row"]["cells"][2].startswith("NEW, created once approved: RHEL Servers / web01 (V2R7)"))
        lines = sf.stig_recheck_lines(c)
        self.assertIn("NO ASSET in STIG Manager - web01 (web01.example.mil) in RHEL Servers, with RHEL_9_STIG V2R7 - created once approved", lines[0])
        self.assertIn("Not a Finding 1, Open 1, Not Applicable 1, manual review 2", lines[0])
        self.assertEqual([x for x in lines if x.startswith("  OPEN")], ["  OPEN   V-2 (X-2) CAT II Rule SV-2"])
        head = sf.recheck_headline(out)
        self.assertEqual((head["text"], head["status"], head["open"]), ("1 rule open, 0 status updates in STIG Manager, 1 new asset for STIG Manager", "warning", 1))
        secs = sf.stig_recheck_sections(out)
        t = [x["title"] for x in secs]
        self.assertEqual(t[:2], ["New in STIG Manager - no asset for these hosts", "New assets - what fails (Open once written)"])
        self.assertNotIn("2. Not Reviewed in STIG Manager - the scan has an answer", t)
        self.assertNotIn("Manual review - the scan gave no answer", t)
        self.assertEqual(secs[0]["rows"][0][:8], ["web01.example.mil", "web01", "web01.example.mil", "RHEL Servers", "RHEL_9_STIG V2R7", "1", "1", "1"])
        self.assertIn("New assets", [b["label"] for b in secs[0]["boxes"]])
        # a scan that failed: nothing to create
        bad = sf.stig_recheck(self.sm(), [{"scanned": False, "why": "oscap is not installed"}], "web01")
        self.assertEqual(sf.stig_update_plan(bad), [])

    def test_plan_create_first(self):
        out = sf.stig_recheck(self.sm(), [self.scan()], "web01.example.mil")
        p = sf.stig_update_plan(out, day="2026-11-05")
        self.assertEqual(p[0]["kind"], "create")
        self.assertEqual((p[0]["asset"], p[0]["fqdn"], p[0]["collectionId"], p[0]["benchmark"], p[0]["reviews"]), ("web01", "web01.example.mil", "7", "RHEL_9_STIG", 3))
        self.assertEqual(p[0]["line"], "NEW ASSET web01 (web01.example.mil) in RHEL Servers with RHEL_9_STIG V2R7 - then 3 review(s): "
                                       "Not a Finding 1, Open 1, Not Applicable 1")
        self.assertEqual([(x["v"], x["result"], x["assetId"], x["new_asset"]) for x in p[1:]],
                         [("V-1", "pass", "", "web01"), ("V-2", "fail", "", "web01"), ("V-3", "notapplicable", "", "web01")])
        self.assertIn("checked it with the new asset on web01.example.mil on 2026-11-05", p[2]["comment"])
        self.assertEqual(sf.stig_update_lines(p), [p[0]["line"]])
        self.assertEqual(sf.stig_update_plan(out, kinds=["fixed"])[0]["reviews"], 0, "answered not asked: the asset, no reviews")
        sec = sf.stig_update_sections(p)[0]
        self.assertEqual((sec["title"], len(sec["rows"]), sec["rows"][0][7]), ("Approve writes these reviews to STIG Manager and creates 1 asset", 1, "created"))
        # two hosts that would get one name (web01.a, web01.b): neither planned, the report says why
        two = sf.stig_recheck(self.sm(), [self.scan()], "web01.a.example.mil") + sf.stig_recheck(self.sm(), [self.scan()], "web01.b.example.mil")
        self.assertEqual(sf.stig_update_plan(two), [])
        rows = sf.stig_recheck_sections(two)[0]["rows"]
        self.assertEqual(rows[0][1], "web01 - NOT CREATED: also the name of web01.b.example.mil - set check_stig_stigman_asset for each")
        att = sf.stig_update_attachments(p, [])
        self.assertEqual((len(att["plan"]), att["left"]), (4, []), "a new asset has no attachments to look for")

    def test_update_creates_once(self):
        p = sf.stig_update_plan(sf.stig_recheck(self.sm(), [self.scan()], "web01.example.mil"), day="2026-11-05")

        class C(object):
            def __init__(self, there=()):
                self.there, self.made, self.posts, self.db = list(there), [], [], {}

            def assets(self, c):
                return self.there

            def create(self, c, name, fqdn, benches, description="", ip="", mac=""):
                self.made.append((c, name, fqdn, benches))
                a = {"assetId": "88", "name": name, "fqdn": fqdn, "stigs": [{"benchmarkId": b, "revisionStr": "V2R7"} for b in benches]}
                self.there.append(a)
                return a

            def write(self, c, a, reviews):
                self.posts.append((c, a, [r["ruleId"] for r in reviews]))
                for r in reviews:
                    self.db[r["ruleId"]] = {"result": r["result"], "status": r["status"], "ts": "now"}
                return {"affected": {"inserted": len(reviews)}, "rejected": []}

            def asset_reviews(self, c, a):
                return dict(self.db)

            def review(self, c, a, rule):
                raise AssertionError("a new asset's reviews are not read one by one")
        c = C()
        items = smu.update({"plan": p}, c, False)
        self.assertEqual([i["state"] for i in items], ["created", "written", "written", "written"])
        self.assertEqual((c.made, c.posts[0][:2]), ([("7", "web01", "web01.example.mil", ["RHEL_9_STIG"])], ("7", "88")))
        self.assertEqual(items[0]["assetId"], "88")
        self.assertEqual(sf.stig_update_item_lines(items), ["created - " + p[0]["line"] + " - its reviews: written 3"])
        r = sf.stig_update_report(items)
        self.assertEqual((r["title"], r["sections"][0]["title"], r["status"]), ("STIG Manager update: 3 review(s) written, 1 asset(s) created", "New assets", "ok"))
        # run again: it is there now - not created twice, nothing written
        items = smu.update({"plan": p}, c, False)
        self.assertEqual([i["state"] for i in items], ["there since the check"] + ["asset not created"] * 3)
        self.assertEqual((len(c.made), len(c.posts)), (1, 1))
        # 1.24.0: the report counts an asset left there and its reviews as not written, as the job does
        left = sf.stig_update_report(items)
        self.assertEqual((left["status"], left["sections"][0]["status"], left["sections"][0]["row_status"]), ("critical", "critical", ["critical"]))
        self.assertTrue(left["title"].endswith(", 4 not"), left["title"])
        again = [dict(items[0], state="there from an earlier run")] + [dict(i, state="written") for i in items[1:]]
        r = sf.stig_update_report(again)
        self.assertEqual((r["status"], r["sections"][0]["status"], r["sections"][0]["row_status"]), ("ok", "ok", ["ok"]))
        # an asset of the host's name made by a person since the check (its short name): left
        c = C(there=[{"assetId": "5", "name": "WEB01"}])
        self.assertEqual(smu.update({"plan": p}, c, False)[0]["state"], "there since the check")
        self.assertEqual(c.made, [])
        # check mode: nothing created or written, said
        c = C()
        self.assertEqual([i["state"] for i in smu.update({"plan": p}, c, True)], ["would create"] + ["would write"] * 3)
        self.assertEqual((c.made, c.posts), ([], []))
        # the same asset twice in a plan: once only
        c = C()
        self.assertEqual([i["state"] for i in smu.update({"plan": p + [dict(p[0])]}, c, False)], ["created", "bad", "written", "written", "written"])
        self.assertEqual(len(c.made), 1)
        # not a plan item the check gives
        bad = [dict(p[0], collectionId="7/../users")] + p[1:]
        self.assertEqual([i["state"] for i in smu.update({"plan": bad}, C(), False)], ["bad"] + ["asset not created"] * 3)


class Attachments(unittest.TestCase):
    def test_what_the_review_panel_writes(self):
        import json as _j
        self.assertEqual(sma.artifacts_of(_j.dumps([{"name": "a.png", "digest": "d1"}, {"name": "b.pdf"}])), (2, ["a.png", "b.pdf"]))
        self.assertEqual(sma.artifacts_of("[]"), (0, []))
        self.assertEqual(sma.artifacts_of(""), (0, []))
        self.assertEqual(sma.artifacts_of(None), (0, []))
        self.assertEqual(sma.artifacts_of("not json"), (-1, []))                 # there, but not readable: counts as some

    def test_the_plan_without_them(self):
        plan = [{"label": "web01", "collection": "C", "asset": "web01", "v": "V-1", "result": "pass", "line": "one",
                 "was": {"result": "fail", "status": "accepted"}},
                {"label": "web01", "collection": "C", "asset": "web01", "v": "V-2", "result": "pass", "line": "two", "was": {}},
                {"label": "web01", "collection": "C", "asset": "web01", "v": "V-3", "result": "fail", "line": "three", "was": {}}]
        out = sf.stig_update_attachments(plan, [{"attachments": 1, "names": ["poam.pdf"]}, {"attachments": 0},
                                                {"attachments": -1, "error": "HTTP 500"}])
        self.assertEqual([p["line"] for p in out["plan"]], ["two"])
        self.assertEqual([r[2] for r in out["section"]["rows"]], ["V-1", "V-3"])
        self.assertEqual(out["section"]["rows"][0][3:], ["Open (accepted)", "Not a Finding", "poam.pdf"])
        self.assertEqual(out["section"]["rows"][1][5], "could not be read: HTTP 500")
        self.assertIsNone(sf.stig_update_attachments(plan[1:2], [{"attachments": 0}])["section"])
        bad = sf.stig_update_attachments(plan, [], "no token")
        self.assertEqual((bad["plan"], len(bad["left"])), ([], 3))
        self.assertIn("no token", " ".join(bad["section"]["lines"]))


import site_answer_files as saf  # noqa: E402


class AnswerFiles(unittest.TestCase):
    """0.55.0: Evaluate-STIG's answer files from a Windows share (or a folder on the AAP side), copied down for the scan."""

    def params(self, **kw):
        p = dict(src="", dest=os.path.join(tempfile.mkdtemp(), "files"), base="", patterns=["*.xml"], username="", password="",
                 auth="negotiate", encrypt=False, port=445, timeout=5, max_files=500, max_mb=20)
        p.update(kw)
        return p

    def folder(self, **files):
        d = tempfile.mkdtemp()
        for n, t in files.items():
            with open(os.path.join(d, n.replace("_xml", ".xml").replace("_txt", ".txt")), "w") as f:
                f.write(t)
        return d

    def test_smb_path(self):
        self.assertEqual(saf.smb_path("\\\\fs01\\stig\\AnswerFiles\\"), ("fs01", "\\\\fs01\\stig\\AnswerFiles"))
        self.assertEqual(saf.smb_path("smb://fs01.example.mil/stig/AF"), ("fs01.example.mil", "\\\\fs01.example.mil\\stig\\AF"))
        self.assertEqual(saf.smb_path("//fs01/stig"), ("fs01", "\\\\fs01\\stig"))
        for local in ("/srv/answers", "files/stig/answers"):
            self.assertIsNone(saf.smb_path(local))
        with self.assertRaises(saf.Fail):
            saf.smb_path("\\\\fs01")

    def test_from_a_folder(self):
        d = self.folder(RHEL_9_xml="<a/>", Firefox_XML="<b/>", notes_txt="x")
        os.rename(os.path.join(d, "Firefox_XML"), os.path.join(d, "Firefox.XML"))
        os.makedirs(os.path.join(d, "old.xml"))                          # a folder: not read
        p = self.params(src=d)
        os.makedirs(p["dest"])
        open(os.path.join(p["dest"], "stale.xml"), "w").close()             # emptied first
        got = saf.read_all(p)
        self.assertEqual([f["name"] for f in got], ["Firefox.XML", "RHEL_9.xml"])
        self.assertEqual(sorted(os.listdir(p["dest"])), ["Firefox.XML", "RHEL_9.xml"])
        self.assertEqual(got[1]["sha256"], __import__("hashlib").sha256(b"<a/>").hexdigest())
        rel = self.params(src=os.path.basename(d), base=os.path.dirname(d))  # from playbooks/: files/stig/answers
        self.assertEqual(len(saf.read_all(rel)), 2)

    def test_refused(self):
        for p, why in ((self.params(src="/nowhere"), "no folder /nowhere"),
                       (self.params(src=self.folder(a_txt="x")), "no answer file (*.xml)"),
                       (self.params(src=self.folder(a_xml="x" * 2048), max_mb=0), "over check_stig_evaluate_answers_max_mb"),
                       (self.params(src=self.folder(a_xml="x", b_xml="y"), max_files=1), "over check_stig_evaluate_answers_max_files")):
            with self.assertRaises(saf.Fail) as e:
                saf.read_all(p)
            self.assertIn(why, str(e.exception))

    def test_from_a_share(self):
        calls = []

        class Entry(object):
            def __init__(self, name, data, folder=False):
                self.name, self.data, self.folder = name, data, folder

            def is_file(self):
                return not self.folder

            @property
            def smb_info(self):
                return type("I", (), {"end_of_file": len(self.data), "last_write_time": datetime.datetime(2026, 10, 30, 14, 5, 7)})()

        share = {"RHEL_9.xml": b"<answers/>", "readme.txt": b"x"}

        class Smb(object):
            fail_login = None

            def register_session(self, server, **kw):
                calls.append(("login", server, kw["username"], kw["port"]))
                if self.fail_login:
                    raise ValueError(self.fail_login)

            def scandir(self, path, **kw):
                calls.append(("list", path))
                return [Entry(n, d) for n, d in share.items()] + [Entry("sub", b"", True)]

            def open_file(self, path, mode="rb", **kw):
                calls.append(("read", path, mode))
                import io
                return io.BytesIO(share[path.rsplit("\\", 1)[1]])
        fake = Smb()
        with unittest.mock.patch.dict(sys.modules, {"smbclient": fake}):
            got = saf.read_all(self.params(src="\\\\fs01\\stig\\AF", username="svc@EXAMPLE.MIL", password="pw"))
            self.assertEqual([f["name"] for f in got], ["RHEL_9.xml"])
            self.assertEqual(got[0]["modified"], "2026-10-30 14:05 UTC")
            self.assertEqual(got[0]["line"], "RHEL_9.xml - 10 bytes, changed 2026-10-30 14:05 UTC, SHA-256 "
                             + __import__("hashlib").sha256(b"<answers/>").hexdigest())
            self.assertEqual(calls, [("login", "fs01", "svc@EXAMPLE.MIL", 445), ("list", "\\\\fs01\\stig\\AF"),
                                     ("read", "\\\\fs01\\stig\\AF\\RHEL_9.xml", "rb")])   # only read, only what matches
            with self.assertRaises(saf.Fail) as e:
                saf.read_all(self.params(src="\\\\fs01\\stig"))
            self.assertIn('attach the "Windows file share" credential', str(e.exception))
            fake.fail_login = "STATUS_LOGON_FAILURE"
            with self.assertRaises(saf.Fail) as e:
                saf.read_all(self.params(src="\\\\fs01\\stig", username="svc", password="bad"))
            self.assertIn("the user name or password is wrong", str(e.exception))

    def es(self, options="--AFPath"):
        d = tempfile.mkdtemp()
        sh = os.path.join(d, "Evaluate-STIG_Bash.sh")
        with open(sh, "w") as f:
            f.write(FAKE_ES.replace("# Evaluate-STIG_Bash.sh", "# its options: %s\n# Evaluate-STIG_Bash.sh" % options)
                    % os.path.join(ROOT, "tests", "stig", "make_cklb.py"))
        with open(os.path.join(d, "results.json"), "w") as f:
            json.dump({}, f)
        return d

    def test_the_scan_given_them(self):
        es = self.es()
        m = FakeModule(dir=tempfile.mkdtemp(), evaluate=es, answers="/tmp/x.stig-answers", answers_source="\\\\fs01\\stig\\AF", answers_count=3)
        with unittest.mock.patch.object(scan, "os_release", return_value={"VERSION_ID": "9.6"}):
            out = scan.Scan(m).run()
        self.assertTrue(out["scanned"], out.get("why"))
        with open(os.path.join(es, "args.txt")) as f:
            self.assertIn("--AFPath /tmp/x.stig-answers", f.read())
        self.assertEqual(out["answers"], {"count": 3, "source": "\\\\fs01\\stig\\AF", "used": 0, "keys": [], "rules": []})
        self.assertEqual(sf.evaluate_note(out), "with 3 answer files from \\\\fs01\\stig\\AF; NO rule answered from them - check that "
                         "each file's STIG name matches the host's STIG and its answer key (DEFAULT, or the host's own) is in it")
        warn = [f for f in sf.stig_compare(out, host="web01", scanner="evaluate_stig")["findings"] if f["id"] == "stig:answers-unused"]
        self.assertEqual(len(warn), 1)
        self.assertEqual(warn[0]["severity"], "warning")
        self.assertIn("was given 3 answer files from \\\\fs01\\stig\\AF and answered no rule from them", warn[0]["summary"])

    def test_the_rules_answered_from_them(self):
        """0.58.0: the checklist's comments say which rules an answer file answered - counted, their answer keys named."""
        es = self.es()
        with open(os.path.join(es, "results.json"), "w") as f:
            json.dump({"SV-257985": "af:not_a_finding", "SV-258999": "af:not_applicable"}, f)
        m = FakeModule(dir=tempfile.mkdtemp(), evaluate=es, answers="/tmp/x.stig-answers", answers_source="\\\\fs01\\stig\\AF", answers_count=2)
        with unittest.mock.patch.object(scan, "os_release", return_value={"VERSION_ID": "9.6"}):
            out = scan.Scan(m).run()
        self.assertEqual(out["answers"]["used"], 2)
        self.assertEqual(out["answers"]["keys"], ["DEFAULT"])
        self.assertEqual(out["answers"]["rules"], ["V-257985", "V-258999"])
        self.assertEqual(sf.answers_used(out["answers"]), "2 rules answered from them (answer key DEFAULT)")
        self.assertIn("2 rules answered from them", sf.stig_compare(out, host="web01", scanner="evaluate_stig")["row"]["cells"][1])
        self.assertFalse([f for f in sf.stig_compare(out, host="web01")["findings"] if f["id"] == "stig:answers-unused"])
        p = os.path.join(tempfile.mkdtemp(), "c.ckl")
        make_cklb.write(p, {"SV-257985": "af:not_a_finding"}, "V2R10", "ckl")             # the CKL's COMMENTS too
        self.assertEqual(scan.answered_from_files(scan.read_checklist(p)[0])["used"], 1)

    def test_why_used_or_not(self):
        """0.58.0: one line in every run - whether the answer files from your share are used, and when not, why."""
        self.assertIn("NOT USED - the scanner is openscap", sf.answers_why("openscap", "\\\\fs01\\stig\\AF"))
        self.assertIn("NOT USED - check_stig_evaluate_answers is not set in this run", sf.answers_why("evaluate_stig", ""))
        self.assertIn("NOT USED - check_stig_evaluate_read is set (/opt/STIG_Compliance)",
                      sf.answers_why("evaluate_stig", "\\\\fs01\\s", "/opt/STIG_Compliance"))
        self.assertIn("not needed - no rule to check again", sf.answers_why("evaluate_stig", "\\\\fs01\\s", "", []))
        self.assertTrue(sf.answers_why("evaluate_stig", "\\\\fs01\\s", "", [["V-1"]]).startswith("answer files: USED - read from \\\\fs01\\s"))
        self.assertTrue(sf.answers_why("evaluate_stig", "\\\\fs01\\s").startswith("answer files: USED"))
        self.assertEqual(sf.answers_used({"used": 1, "keys": ["DEFAULT", "WEB"]}), "1 rule answered from them (answer keys DEFAULT, WEB)")
        self.assertEqual(sf.answers_used({"used": 0}, True), "none of the rules checked again was answered from them")

    def test_not_run_without_them_or_without_the_option(self):
        m = FakeModule(dir=tempfile.mkdtemp(), evaluate=self.es(), answers_error="reading the folder \\\\fs01\\stig: refused")
        with unittest.mock.patch.object(scan, "os_release", return_value={"VERSION_ID": "9.6"}):
            out = scan.Scan(m).run()
        self.assertFalse(out["scanned"])
        self.assertIn("Evaluate-STIG was not run: its answer files could not be read", out["why"])
        es = self.es(options="--SelectSTIG")
        m = FakeModule(dir=tempfile.mkdtemp(), evaluate=es, answers="/tmp/x")
        with unittest.mock.patch.object(scan, "os_release", return_value={"VERSION_ID": "9.6"}):
            out = scan.Scan(m).run()
        self.assertFalse(out["scanned"])
        self.assertIn("this Evaluate-STIG has no AFPath option", out["why"])
        self.assertFalse(os.path.exists(os.path.join(es, "args.txt")), "not run")


class Details(unittest.TestCase):
    """What Evaluate-STIG found: its own header and separators left out, the table cell short, the full text in a card."""
    HEX = "0123456789abcdef" * 4

    def test_its_header_and_rules_left_out(self):
        multi = ("Evaluate-STIG 1.2607.0 (Scan-RHEL8_Checks) found this to be OPEN on 10/30/2026\nResultHash: %s\n~~~~~~~~~~~~\n"
                 "The gssproxy package has been installed on the system.\n\nName : gssproxy\n-------------------------\n" % self.HEX)
        self.assertEqual(sf.details_text(multi), "The gssproxy package has been installed on the system.\n\nName : gssproxy")
        one = ("Evaluate-STIG 1.2607.0 (Scan-RHEL8_Checks) found this to be OPEN on 10/30/2026 ResultHash: %s ~~~~ "
               "The gssproxy package has been installed on the system. -------" % self.HEX)
        self.assertEqual(sf.details_text(one), "The gssproxy package has been installed on the system.")
        self.assertEqual(sf.details_text("Found: a --- b"), "Found: a --- b", "a short dash run in the text stays")
        self.assertEqual(sf.details_text(None), "")

    def test_short_cell_and_full_cards(self):
        long = "Evaluate-STIG 1.2607.0 (Scan-RHEL8_Checks) found this to be OPEN on 10/30/2026\n" + " ".join("word%d" % i for i in range(200))
        r = {"key": "SV-1", "v": "V-1", "severity": "medium", "title": "T", "details": long, "level": "warning", "sm": "pass", "scap": []}
        cell = sf._details_short(r)
        self.assertTrue(cell.startswith("word0 word1"))
        self.assertTrue(cell.endswith("(in full: What Evaluate-STIG found, below)"))
        self.assertLess(len(cell), 220)
        c = {"host": "web01", "disagree": [r], "drift": [], "new": [], "fixed_open": [], "unreviewed": [], "ignored": [], "findings": [],
             "scanned": True, "row": {"cells": ["web01"], "status": "ok"}}
        secs = sf.stig_report_sections([c])
        dis = [s for s in secs if s["title"].startswith("3.")][0]
        self.assertEqual(dis["rows"][0][-1], cell)
        full = [s for s in secs if s["title"] == "What Evaluate-STIG found - in full"][0]
        self.assertEqual(full["cards"][0]["code"], " ".join("word%d" % i for i in range(200)))
        self.assertEqual(full["cards"][0]["meta"], "Disagree")
        self.assertEqual(full["cards"][0]["subject"], "web01 - V-1")
        cards, n = sf.stig_details_cards([c], 0)
        self.assertEqual((cards, n), ([], 1))

    def test_kept_up_to_4000(self):
        st = {"rules": [{"rule": "SV-9r1_rule", "v": "V-9", "stigid": "X", "severity": "medium", "title": "T", "result": "fail", "details": "x" * 5000}]}
        self.assertEqual(len(scan.checklist_items(st)[0]["details"]), 4000)


class Module(unittest.TestCase):
    def test_docs_are_yaml(self):
        import yaml
        for m in (scan, smr):
            yaml.safe_load(m.DOCUMENTATION)
            yaml.safe_load(m.RETURN)

    def test_make_results_runs(self):
        d = tempfile.mkdtemp()
        subprocess.check_call([sys.executable, os.path.join(ROOT, "tests", "stig", "make_results.py"), os.path.join(d, "r.xml"), '{"accounts_tmout": "fail"}'])
        self.assertTrue(os.path.getsize(os.path.join(d, "r.xml")) > 1000)


if __name__ == "__main__":
    unittest.main(verbosity=1)
