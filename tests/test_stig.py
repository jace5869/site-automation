#!/usr/bin/env python3
"""Unit tests for the STIG drift check: reading oscap's results (roles/check_stig/library/site_stig_scan.py - one
item per STIG rule, the worst of its SCAP rules, the STIG version, the runs kept on the host) and Evaluate-STIG's
checklists (CKLB, CKL; running it, reading what it wrote, the scanner changed), finding an asset
and its reviews in STIG Manager (roles/check_stig/library/site_stigman_read.py), and the comparison, findings and
report sections (plugins/filter/stig_filters.py).

    python tests/test_stig.py
"""
import json
import os
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
                           evaluate_args=[], evaluate_read="", evaluate_output="CKLB", evaluate_rejected=True, pwsh="", benchmarks={"8": "RHEL_8_STIG", "9": "RHEL_9_STIG", "10": "RHEL_10_STIG"})
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
