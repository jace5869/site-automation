#!/usr/bin/env python3
"""Unit tests for workflow 40, the checklist import: reading checklists (CKL, CKLB - VMware's InSpec ones with their own
STIG id and no rule revisions), matching them to STIG Manager's assets and installed STIGs, the plan
(roles/stigman_ckl_import/library/site_ckl_import.py), its report (plugins/filter/stig_ckl_filters.py), and what the
update (roles/stigman_update/library/site_stigman_update.py) does with the plan's new parts.
    python tests/test_stig_ckl_import.py
"""
import copy
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "roles", "stigman_ckl_import", "library"))
sys.path.insert(0, os.path.join(ROOT, "roles", "stigman_update", "library"))
sys.path.insert(0, os.path.join(ROOT, "plugins", "filter"))
sys.path.insert(0, os.path.join(ROOT, "tests", "stig"))
import make_cklb                   # noqa: E402
import site_ckl_import as ci       # noqa: E402
import site_stigman_update as smu  # noqa: E402
import stig_ckl_filters as cf      # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "stig", "ckl", "inspec_apache.ckl")
ARIA = "VMW_Aria_Operations_8-x_Apache_STIG"


def aria_rules(rev="r1"):
    """STIG Manager's rules of the Aria Operations Apache STIG: DISA's ids, the versions the InSpec checklist uses."""
    return [{"ruleId": "SV-2600%02d%s_rule" % (n, rev), "groupId": "V-2600%02d" % n, "version": "VRPE-8X-%06d" % n,
             "severity": "medium", "title": "Apache rule %d" % n} for n in (1, 2, 5, 7, 9)]


def data():
    return {"collections": [{"collectionId": "5", "name": "VMware"}, {"collectionId": "6", "name": "RHEL"}],
            "assets": {"5": []},
            "collection_stigs": {"5": []},
            "stigs": [{"benchmarkId": ARIA, "title": "VMware Aria Operations 8.x Apache Server Security Technical Implementation Guide",
                       "lastRevisionStr": "V1R1"},
                      {"benchmarkId": "Apache_Server_2-4_UNIX_Server_STIG", "title": "Apache Server 2.4 UNIX Server STIG", "lastRevisionStr": "V3R1"},
                      {"benchmarkId": "VMW_vSphere_8-0_ESXi_STIG", "title": "VMware vSphere 8.0 ESXi STIG", "lastRevisionStr": "V2R2"},
                      {"benchmarkId": "RHEL_9_STIG", "title": "Red Hat Enterprise Linux 9 STIG", "lastRevisionStr": "V2R9"}],
            "rules": {(ARIA, "V1R1"): aria_rules(),
                      ("Apache_Server_2-4_UNIX_Server_STIG", "V3R1"): [{"ruleId": "SV-214228r1_rule", "groupId": "V-214228",
                                                                       "version": "AS24-U1-000010", "severity": "medium", "title": "x"}],
                      ("VMW_vSphere_8-0_ESXi_STIG", "V2R2"): [], ("RHEL_9_STIG", "V2R9"): []},
            "reviews": {}}


class Fake(ci.Client):
    """STIG Manager from a dict: the GETs site_ckl_import makes."""
    def __init__(self, d):
        self.d, self.calls = d, []

    def get(self, path, query=None):
        self.calls.append(path)
        q = dict(query or [])
        if path == "/collections":
            return self.d["collections"]
        if path == "/assets":
            return self.d["assets"].get(q["collectionId"], [])
        if path.startswith("/collections/") and path.endswith("/stigs"):
            return self.d["collection_stigs"].get(path.split("/")[2], [])
        if path == "/stigs":
            return self.d["stigs"]
        raise AssertionError("not asked for: " + path)

    def rules(self, bench, rev):
        self.calls.append("rules %s %s" % (bench, rev))
        if (bench, rev) not in self.d["rules"]:
            raise RuntimeError("HTTP 404 from /stigs/%s/revisions/%s/rules" % (bench, rev))
        return self.d["rules"][(bench, rev)]

    def reviews(self, c, a, bench):
        return self.d["reviews"].get(a, [])


def params(**kw):
    p = {"collection": "VMware", "create_assets": True, "benchmarks": {}, "min_match": 0.8, "candidates": 25, "search_all": False,
         "other_revision": False, "overwrite_accepted": False, "status": "submitted", "detail_max": 8000, "day": "2026-11-05"}
    p.update(kw)
    return p


def fixture():
    with open(FIXTURE, "rb") as f:
        return f.read()


def run(d, files=None, **kw):
    files = files if files is not None else [{"name": "vrops01/apache.ckl", "data": fixture(), "modified": "2026-11-05 10:00 UTC"}]
    return ci.plan_all(params(**kw), Fake(d), copy.deepcopy(files))


class Parse(unittest.TestCase):
    def test_inspec_ckl(self):
        c = ci.parse("a.ckl", fixture())
        self.assertEqual(c["target"]["host"], "vrops01")
        self.assertEqual(c["target"]["fqdn"], "vrops01.example.mil")
        self.assertFalse(c["target"]["web"])
        s = c["stigs"][0]
        self.assertEqual(s["stig"], "VMware Aria Operations 8.x Apache Profile")
        self.assertEqual(s["revision"], "")               # no releaseinfo in an InSpec checklist
        self.assertEqual([r["status"] for r in s["rules"]], ["NotAFinding", "Open", "Not_Applicable", "Not_Reviewed"])
        self.assertEqual(s["rules"][0]["rule_id"], "SV-VRPE-8X-000001")
        self.assertEqual(s["rules"][0]["version"], "VRPE-8X-000001")
        self.assertEqual(s["rules"][0]["engine"], {"type": "script", "product": "InSpec"})
        self.assertIn("MaxKeepAliveRequests", s["rules"][0]["details"])

    def test_cklb_and_ckl_of_evaluate_stig(self):
        d = tempfile.mkdtemp()
        for kind in ("cklb", "ckl"):
            p = os.path.join(d, "h." + kind)
            make_cklb.write(p, {"SV-257985": "open"}, "V2R9", kind)
            with open(p, "rb") as f:
                c = ci.parse(p, f.read())
            rhel = [s for s in c["stigs"] if s["stig"] == "RHEL_9_STIG"][0]
            self.assertEqual(rhel["revision"], "V2R9", kind)
            op = [r for r in rhel["rules"] if r["rule_id"].startswith("SV-257985")][0]
            self.assertEqual(ci.CKL_RESULT[op["status"].lower()], "fail")
            self.assertEqual(op["engine"], {"type": "script", "product": "Evaluate-STIG"}, kind)

    def test_entities_refused(self):
        bad = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><CHECKLIST>&a;</CHECKLIST>'
        with self.assertRaises(ci.Fail):
            ci.parse("x.ckl", bad)
        with self.assertRaises(ci.Fail):
            ci.parse("x.ckl", b"<other/>")
        with self.assertRaises(ci.Fail):
            ci.parse("x.cklb", b"{not json")

    def test_web_database_asset_name(self):
        self.assertEqual(ci.asset_name({"host": "db01", "web": True, "site": "", "instance": "MSSQLSERVER"}), "db01-NA-MSSQLSERVER")
        self.assertEqual(ci.asset_name({"host": "web01", "web": False, "site": "x"}), "web01")
        self.assertEqual(ci.asset_name({"host": "", "web": True}), "")

    def test_revision(self):
        self.assertEqual(ci._revision("2", "Release: 9 Benchmark Date: 24 Jul 2024"), "V2R9")
        self.assertEqual(ci._revision("1", ""), "")


class Match(unittest.TestCase):
    def test_candidates_by_name(self):
        ck = {"stig": "VMware Aria Operations 8.x Apache Profile", "title": "VMware Aria Operations 8.x Apache Profile"}
        got = [s["benchmarkId"] for s, _how in ci.candidates(ck, data()["stigs"], {}, 25)]
        self.assertEqual(got[0], ARIA)
        self.assertNotIn("RHEL_9_STIG", got)

    def test_rules_by_version_v_number_and_rule_id(self):
        rules = aria_rules()
        hits = ci.match_rules([{"rule_id": "SV-VRPE-8X-000001", "v": "VRPE-8X-000001", "version": "VRPE-8X-000001"},
                               {"rule_id": "SV-260002r9_rule", "v": "", "version": ""},
                               {"rule_id": "", "v": "V-260005", "version": ""},
                               {"rule_id": "", "v": "VRPE-8X-000007", "version": ""},
                               {"rule_id": "SV-VRPE-8X-000099", "v": "VRPE-8X-000099", "version": "VRPE-8X-000099"}], rules)
        self.assertEqual([h[1] for h in hits], ["version", "rule id", "V-number", "version", ""])
        self.assertEqual(hits[0][0]["ruleId"], "SV-260001r1_rule")


class Plan(unittest.TestCase):
    def test_new_asset_with_its_stig(self):
        cks, plan, left, col = run(data())
        self.assertEqual(col, {"collectionId": "5", "name": "VMware"})
        self.assertEqual(cks[0]["state"], "planned")
        self.assertTrue(cks[0]["new"])
        self.assertEqual((cks[0]["benchmark"], cks[0]["revision"], cks[0]["match"], cks[0]["by"]), (ARIA, "V1R1", "4 of 4", "version"))
        new = plan[0]
        self.assertEqual(new["kind"], "create")
        self.assertEqual((new["asset"], new["fqdn"], new["ip"], new["benchmarks"]), ("vrops01", "vrops01.example.mil", "192.0.2.41", [ARIA]))
        self.assertEqual(new["reviews"], 3)
        revs = plan[1:]
        self.assertEqual([(r["ruleId"], r["result"]) for r in revs],
                         [("SV-260001r1_rule", "pass"), ("SV-260002r1_rule", "fail"), ("SV-260005r1_rule", "notapplicable")])
        r = revs[1]
        self.assertEqual((r["kind"], r["new_asset"], r["assetId"], r["status"]), ("import", "vrops01", "", "submitted"))
        self.assertEqual(r["engine"], {"type": "script", "product": "InSpec"})
        self.assertIn("SessionCookieName", r["detail"])
        self.assertIn("Imported from the checklist vrops01/apache.ckl", r["comment"])
        self.assertEqual(r["was"], {"result": "", "status": "", "ts": ""})
        self.assertEqual(left, [])              # Not Reviewed is never written, and not listed
        self.assertEqual(cks[0]["counts"], {"pass": 1, "fail": 1, "notapplicable": 1, "notchecked": 1})
        # what the update checks before writing: every item is one it can write once the asset is made
        for it in revs:
            self.assertEqual(smu.bad_item(dict(it, assetId="9")), "")
        self.assertEqual(smu.bad_create(new), "")

    def test_existing_asset_its_reviews(self):
        d = data()
        d["assets"]["5"] = [{"assetId": "41", "name": "VROPS01", "fqdn": "vrops01.example.mil", "stigs": [{"benchmarkId": ARIA, "revisionStr": "V1R1"}]}]
        d["reviews"]["41"] = [{"ruleId": "SV-260001r1_rule", "result": "pass", "status": {"label": "submitted"}, "ts": "t1"},
                              {"ruleId": "SV-260002r1_rule", "result": "pass", "status": {"label": "accepted"}, "ts": "t2"},
                              {"ruleId": "SV-260005r1_rule", "result": "fail", "status": {"label": "saved"}, "ts": "t3"}]
        cks, plan, left, _ = run(d)
        self.assertFalse(cks[0]["new"])
        self.assertEqual(cks[0]["assetId"], "41")
        self.assertEqual([(p["ruleId"], p["result"], p["was"]["result"], p["assetId"]) for p in plan],
                         [("SV-260005r1_rule", "notapplicable", "fail", "41")])
        self.assertEqual(sorted((x["kind"], x["version"]) for x in left), [("accepted", "VRPE-8X-000002"), ("same", "VRPE-8X-000001")])
        cks, plan, left, _ = run(d, overwrite_accepted=True)
        self.assertEqual([p["ruleId"] for p in plan], ["SV-260002r1_rule", "SV-260005r1_rule"])

    def test_asset_without_the_stig_is_left_for_a_person(self):
        d = data()
        d["assets"]["5"] = [{"assetId": "41", "name": "vrops01", "stigs": [{"benchmarkId": "VMW_vSphere_8-0_ESXi_STIG", "revisionStr": "V2R2"}]}]
        cks, plan, left, _ = run(d)
        self.assertEqual(plan, [])
        self.assertEqual(cks[0]["state"], "no STIG on the asset")
        self.assertIn("assign it to the asset", cks[0]["why"])
        self.assertEqual(len(left), 3)

    def test_no_create(self):
        cks, plan, left, _ = run(data(), create_assets=False)
        self.assertEqual((plan, cks[0]["state"]), ([], "no asset"))

    def test_no_host_name(self):
        raw = fixture().replace(b"<HOST_NAME>vrops01</HOST_NAME>", b"<HOST_NAME></HOST_NAME>")
        cks, plan, left, _ = run(data(), [{"name": "x.ckl", "data": raw, "modified": ""}])
        self.assertEqual((plan, cks[0]["state"]), ([], "no asset"))
        self.assertIn("HOST_NAME", cks[0]["why"])

    def test_not_matched_and_the_setting(self):
        d = data()
        d["rules"][(ARIA, "V1R1")] = aria_rules()[:2]           # only 2 of the checklist's 4 rules
        cks, plan, left, _ = run(d)
        self.assertEqual(cks[0]["state"], "not matched")
        self.assertIn("2 of 4", cks[0]["why"])
        self.assertEqual(plan, [])
        cks, plan, left, _ = run(d, min_match=0.5)
        self.assertEqual(cks[0]["state"], "planned")
        self.assertEqual([x["kind"] for x in left], ["unmatched"])         # VRPE-8X-000005 is not in it
        # named in stigman_ckl_import_benchmarks: no other STIG read
        d = data()
        d["stigs"][0]["title"] = "Something else"
        f = Fake(d)
        cks, plan, left, _ = ci.plan_all(params(benchmarks={"vmware aria operations 8.x apache*": ARIA}), f,
                                         [{"name": "a.ckl", "data": fixture(), "modified": ""}])
        self.assertEqual((cks[0]["by"], cks[0]["benchmark"]), ("setting", ARIA))
        self.assertEqual([c for c in f.calls if c.startswith("rules")], ["rules %s V1R1" % ARIA])

    def test_two_checklists_disagree(self):
        other = fixture().replace(b"<STATUS>Open</STATUS>", b"<STATUS>NotAFinding</STATUS>")
        cks, plan, left, _ = run(data(), [{"name": "a.ckl", "data": fixture(), "modified": "1"}, {"name": "b.ckl", "data": other, "modified": "2"}])
        self.assertNotIn("SV-260002r1_rule", [p.get("ruleId") for p in plan])
        self.assertEqual(len([x for x in left if x["kind"] == "conflict"]), 2)
        self.assertEqual([p.get("reviews") for p in plan if p["kind"] == "create"], [2])    # the same answers twice: once

    def test_another_revision_is_held(self):
        d = data()
        d["rules"][(ARIA, "V1R1")] = aria_rules("r2")
        raw = fixture().replace(b"SV-VRPE-8X-000002", b"SV-260002r1_rule")
        cks, plan, left, _ = run(d, [{"name": "a.ckl", "data": raw, "modified": ""}])
        self.assertEqual([x["kind"] for x in left], ["revision"])
        cks, plan, left, _ = run(d, [{"name": "a.ckl", "data": raw, "modified": ""}], other_revision=True)
        self.assertIn("SV-260002r2_rule", [p.get("ruleId") for p in plan])

    def test_collection_not_found(self):
        with self.assertRaises(ci.Fail):
            run(data(), collection="nope")

    def test_report(self):
        cks, plan, left, col = run(data())
        rep = cf.stig_ckl_report({"files": [{"name": "vrops01/apache.ckl", "checklists": 1}], "checklists": cks, "plan": plan, "left": left,
                                  "collection": col, "source": "\\\\fs\\stig\\ckl"}, plan, [], True, 200)
        self.assertEqual(rep["title"], "Checklist import: 3 review(s) to write, 1 new asset(s)")
        self.assertEqual(rep["sections"][0]["title"], "Approve writes these reviews to STIG Manager and creates 1 asset")
        self.assertEqual(rep["sections"][0]["rows"][0][0], "NEW ASSET")
        self.assertEqual(len(rep["sections"][0]["rows"]), 1)       # the new asset's reviews are counted in its row
        self.assertEqual(cf.stig_ckl_lines(cks)[0].split(" -> ")[1][:13], "vrops01 (new)")
        empty = cf.stig_ckl_report({"error": "no checklist (*.ckl) in x"}, [], [], True)
        self.assertEqual(empty["status"], "critical")


class Folder(unittest.TestCase):
    def test_folder_and_sub_folders(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "vrops01"))
        with open(os.path.join(d, "vrops01", "apache.CKL"), "wb") as f:
            f.write(fixture())
        with open(os.path.join(d, "notes.txt"), "w") as f:
            f.write("x")
        p = {"src": d, "base": "", "patterns": ["*.ckl", "*.cklb"], "recurse": True, "max_files": 10, "max_mb": 1}
        got = ci.read_files(p)
        self.assertEqual([g["name"] for g in got], ["vrops01/apache.CKL"])
        self.assertEqual(got[0]["data"], fixture())
        self.assertTrue(ci.read_files(dict(p, max_mb=0))[0]["error"].endswith("left out"))
        with self.assertRaises(ci.Fail):
            ci.read_files(dict(p, recurse=False))


class Update(unittest.TestCase):
    """The update with the import's plan: a new asset with several STIGs, a review no engine answered."""
    def test_null_engine_only_for_the_import(self):
        it = {"collectionId": "5", "assetId": "41", "ruleId": "SV-260001r1_rule", "result": "pass", "status": "submitted",
              "was": {}, "detail": "x", "engine": None}
        self.assertEqual(smu.bad_item(it), "no result engine")
        self.assertEqual(smu.bad_item(dict(it, kind="import")), "")
        self.assertIsNone(smu.body_of(dict(it, kind="import"))["resultEngine"])

    def test_create_with_benchmarks(self):
        sent = []

        class C(object):
            def assets(self, c):
                return []

            def create(self, c, name, fqdn, benches, description="", ip="", mac=""):
                sent.append((name, list(benches), description, ip))
                return {"assetId": "77", "stigs": [{"benchmarkId": ARIA, "revisionStr": "V1R1"}]}
        plan = [{"kind": "create", "collectionId": "5", "asset": "vrops01", "fqdn": "", "benchmark": ARIA,
                 "benchmarks": [ARIA, "VMW_Aria_Operations_8-x_Application_STIG"], "revision": "V1R1, V1R2", "description": "Created by the checklist import",
                 "ip": "192.0.2.41"}]
        rows, ids = smu.make_assets({"plan": plan}, C(), False)
        self.assertEqual(sent, [("vrops01", [ARIA, "VMW_Aria_Operations_8-x_Application_STIG"], "Created by the checklist import", "192.0.2.41")])
        self.assertEqual(rows[0]["state"], "created")
        self.assertIn("without VMW_Aria_Operations_8-x_Application_STIG", rows[0]["why"])
        self.assertEqual(ids[("5", "vrops01")], "77")

    def test_benchmark_ids_with_spaces(self):
        """STIG Manager takes any benchmark id up to 255 characters - STIG content from elsewhere has spaces in it."""
        it = {"kind": "create", "collectionId": "5", "asset": "vrops01", "benchmark": "VMware Aria Operations 8.x Apache",
              "benchmarks": ["VMware Aria Operations 8.x Apache", "VCF Photon OS 5.0", "RHEL_9_STIG"]}
        self.assertEqual(smu.bad_create(it), "")
        self.assertEqual(smu.bad_create(dict(it, benchmarks=["a\nb"])), "no STIG (benchmark)")
        self.assertEqual(smu.bad_create(dict(it, benchmarks=["x" * 256])), "no STIG (benchmark)")

    def test_rule_ids_not_of_disa(self):
        """STIG Manager takes any rule id up to 45 characters - STIG content from elsewhere has SV-PHTN-50-000003."""
        it = {"kind": "import", "collectionId": "5", "assetId": "41", "ruleId": "SV-PHTN-50-000003", "result": "pass", "status": "submitted",
              "was": {}, "detail": "x", "engine": None}
        self.assertEqual(smu.bad_item(it), "")
        self.assertEqual(smu.bad_item(dict(it, ruleId="SV-257985r1069364_rule")), "")
        self.assertEqual(smu.bad_item(dict(it, ruleId="x" * 46)), "not a STIG rule id: " + "x" * 46)
        self.assertEqual(smu.bad_item(dict(it, ruleId="")), "not a STIG rule id: ")
        self.assertEqual(smu.bad_item(dict(it, ruleId="SV-1/../../users")), "not a STIG rule id: SV-1/../../users")
        for dots in (".", "..", "..."):            # a proxy in front of STIG Manager takes ".." as a step up the path
            self.assertEqual(smu.bad_item(dict(it, ruleId=dots)), "not a STIG rule id: " + dots)
        self.assertEqual(smu.bad_item(dict(it, ruleId="SV-1.2")), "")


if __name__ == "__main__":
    unittest.main()
