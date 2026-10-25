#!/usr/bin/env python3
"""Unit tests for the STIG drift check: reading oscap's results (roles/check_stig/library/site_stig_scan.py - one
item per STIG rule, the worst of its SCAP rules, the STIG version, the runs kept on the host), finding an asset
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "roles", "check_stig", "library"))
sys.path.insert(0, os.path.join(ROOT, "plugins", "filter"))
sys.path.insert(0, os.path.join(ROOT, "tests", "stig"))
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
        self.assertEqual(len(its), 7)                       # 8 SCAP rules, two of them one STIG rule
        a = its["SV-258176"]
        self.assertEqual(a["result"], "fail")
        self.assertEqual(a["scap"], ["audit_rules_privileged_commands_su", "audit_rules_privileged_commands_sudo"])
        self.assertEqual(its["enable_authselect"]["result"], "pass", "a SCAP rule with no STIG rule is its own item")

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


def scanned(res, prev=None):
    its = items(res)
    return {"scanned": True, "items": its, "counts": scan.counts(its), "previous": {"time": "2026-10-01T10:00:00Z", "items": prev} if prev else {},
            "saved": {"report": "/var/lib/site-automation/stig/x_report.html"}, "stig_version": "V2R9", "profile_title": "DISA STIG", "os": "RHEL 9"}


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

    def test_not_applicable_in_stig_manager_is_drift_rejected_does_not_count(self):
        c = sf.stig_compare(scanned({"sshd_disable_root_login": "fail", "banner_etc_issue": "fail"}),
                            sm(SV_257985=("notapplicable", "accepted"), SV_257779=("pass", "rejected")), "web01", stigman_used=True)
        self.assertEqual([r["key"] for r in c["drift"]], ["SV-257985"])
        self.assertEqual([r["key"] for r in c["unreviewed"]], ["SV-257779"])
        c = sf.stig_compare(scanned({"banner_etc_issue": "fail"}), sm(SV_257779=("pass", "submitted")), "web01", statuses=["accepted"], stigman_used=True)
        self.assertEqual(c["drift"], [], "statuses [accepted]: a submitted review is not the checklist")

    def test_severity_setting(self):
        c = sf.stig_compare(scanned({"ensure_gpgcheck_local_packages": "fail"}), sm(SV_257821=("pass", "accepted")), "web01",
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
        self.assertIn("oscap is not installed", c["row"]["cells"][2])


class Sections(unittest.TestCase):
    def test_order_and_boxes(self):
        a = sf.stig_compare(scanned({"ensure_gpgcheck_local_packages": "fail", "accounts_tmout": "fail"}, ALL_PASS),
                            sm(SV_257821=("pass", "accepted"), SV_257985=("fail", "saved")), "web01", stigman_used=True)
        b = sf.stig_compare({"scanned": False, "why": "no content"}, {}, "web02")
        s = sf.stig_report_sections([a, b])
        self.assertEqual([x["title"].split(" - ")[0] for x in s],
                         ["1. Drifted", "2. New fails", "3. Fixed on the host, still Open in STIG Manager", "Every host",
                          "Fails STIG Manager has no review for"])
        self.assertTrue(all(x.get("top") for x in s[:3]))
        self.assertEqual([x["value"] for x in s[0]["boxes"]], [1, 1, 1, 1])
        self.assertEqual(s[3]["rows"][0][0], "web01", "worst first: the critical host before the one not scanned")
        json.dumps(s)

    def test_no_drift(self):
        s = sf.stig_report_sections([sf.stig_compare(scanned({}), {}, "web01")])
        self.assertEqual(s[0]["title"], "No drift")
        self.assertEqual(s[0]["status"], "ok")

    def test_unreviewed_capped(self):
        c = sf.stig_compare(scanned({k: "fail" for k, *_ in make_results.RULES}), sm(), "web01", stigman_used=True)
        s = sf.stig_report_sections([c], list_max=2)
        u = [x for x in s if x["title"].startswith("Fails STIG Manager has no review for")][0]
        self.assertEqual(len(u["rows"]), 2)
        self.assertIn("The first 2 of 6", u["text"])


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
