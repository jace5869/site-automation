#!/usr/bin/env python3
"""Unit tests for workflow 40, the checklist import: reading checklists (CKL, CKLB - VMware's InSpec ones with their own
STIG id and no rule revisions), matching them to STIG Manager's assets and installed STIGs, the plan
(roles/stigman_ckl_import/library/site_ckl_import.py), its report (plugins/filter/stig_ckl_filters.py), and what the
update (roles/stigman_update/library/site_stigman_update.py) does with the plan's new parts; 0.76.0, as a STIG Manager
Watcher's job: the asset looked for in several collections (a staging one for the new), written at once (auto), the files
done with moved.
    python tests/test_stig_ckl_import.py
"""
import copy
import hashlib
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
        if path == "/user":
            return self.d.get("user", {"userId": "1"})
        parts = path.split("/")
        # 1.26.0: a collection's grants and a grant's access list (STIG Manager gives them to Manage only: grants_error)
        if len(parts) in (4, 6) and parts[1] == "collections" and parts[3] == "grants":
            if self.d.get("grants_error"):
                raise RuntimeError("HTTP 403 from %s: User has insufficient privilege to complete this request." % path)
            if len(parts) == 4:
                return self.d.get("grants", {}).get(parts[2], [])
            if parts[5] == "acl":
                return self.d.get("acl", {}).get((parts[2], parts[4]), {"defaultAccess": "none", "acl": []})
        raise AssertionError("not asked for: " + path)

    def rules(self, bench, rev):
        self.calls.append("rules %s %s" % (bench, rev))
        if (bench, rev) not in self.d["rules"]:
            raise RuntimeError("HTTP 404 from /stigs/%s/revisions/%s/rules" % (bench, rev))
        return self.d["rules"][(bench, rev)]

    def reviews(self, c, a, bench):
        self.calls.append("reviews %s %s %s" % (c, a, bench))       # where they are read (1.26.0: a moving asset's in staging)
        return self.d["reviews"].get(a, [])


def params(**kw):
    p = {"collection": "VMware", "collections": [], "auto": "none", "create_assets": True, "benchmarks": {}, "min_match": 0.8, "candidates": 25, "search_all": False,
         "other_revision": False, "overwrite_accepted": False, "status": "submitted", "detail_max": 8000, "day": "2026-11-05",
         "routes": [], "route_move": "staging", "assign_stigs": False}
    p.update(kw)
    return p


def fixture():
    with open(FIXTURE, "rb") as f:
        return f.read()


def run(d, files=None, **kw):
    files = files if files is not None else [{"name": "vrops01/apache.ckl", "data": fixture(), "modified": "2026-11-05 10:00 UTC"}]
    return ci.plan_all(params(**kw), Fake(d), copy.deepcopy(files))[:4]


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

    def test_entities_refused_in_utf16(self):
        """1.24.0: the DOCTYPE is refused whatever the file's encoding (its BOM or XML declaration)."""
        doc = ('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE CHECKLIST [<!ENTITY a "AAAA"><!ENTITY b "&a;&a;&a;">]>'
               '<CHECKLIST><ASSET><HOST_NAME>&b;</HOST_NAME></ASSET><STIGS/></CHECKLIST>')
        for enc, bom in (("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff")):
            raw = bom + doc.encode(enc)
            with self.assertRaises(ci.Fail) as e:
                ci.parse("x.ckl", raw)
            self.assertIn("DOCTYPE", str(e.exception), enc)
        # a UTF-16 checklist with no DOCTYPE is still read
        ok = '<?xml version="1.0" encoding="UTF-16"?><CHECKLIST><ASSET><HOST_NAME>web01</HOST_NAME></ASSET><STIGS/></CHECKLIST>'
        self.assertEqual(ci.parse_ckl(ok.encode("utf-16"))["target"]["host"], "web01")

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
                                         [{"name": "a.ckl", "data": fixture(), "modified": ""}])[:4]
        self.assertEqual((cks[0]["by"], cks[0]["benchmark"]), ("setting", ARIA))
        self.assertEqual([c for c in f.calls if c.startswith("rules")], ["rules %s V1R1" % ARIA])

    def test_two_checklists_disagree(self):
        other = fixture().replace(b"<STATUS>Open</STATUS>", b"<STATUS>NotAFinding</STATUS>")
        cks, plan, left, _ = run(data(), [{"name": "a.ckl", "data": fixture(), "modified": "1"}, {"name": "b.ckl", "data": other, "modified": "2"}])
        self.assertNotIn("SV-260002r1_rule", [p.get("ruleId") for p in plan])
        self.assertEqual(len([x for x in left if x["kind"] == "conflict"]), 2)
        self.assertEqual([p.get("reviews") for p in plan if p["kind"] == "create"], [2])    # the same answers twice: once

    def test_a_newer_checklist_like_stig_manager_still_disagrees(self):
        """1.24.0: a checklist STIG Manager agrees with takes part in the choice - an older one that disagrees is not
        written over the newer result, and both stay (not settled)."""
        d = data()
        d["assets"]["5"] = [{"assetId": "41", "name": "vrops01", "stigs": [{"benchmarkId": ARIA, "revisionStr": "V1R1"}]}]
        d["reviews"]["41"] = [{"ruleId": "SV-260002r1_rule", "result": "pass", "status": {"label": "submitted"}, "ts": "t1"}]
        new = fixture().replace(b"<STATUS>Open</STATUS>", b"<STATUS>NotAFinding</STATUS>")       # V-260002: Not a Finding, as STIG Manager
        files = [{"name": "new.ckl", "data": new, "modified": "2026-11-10 10:00 UTC"},
                 {"name": "old.ckl", "data": fixture(), "modified": "2025-01-01 10:00 UTC"}]   # V-260002: Open
        for order in (files, files[::-1]):
            cks, plan, left, _ = run(d, order)
            self.assertNotIn("SV-260002r1_rule", [p.get("ruleId") for p in plan])
            two = sorted((x["file"], x["kind"]) for x in left if x["version"] == "VRPE-8X-000002")
            self.assertEqual(two, [("new.ckl", "conflict"), ("old.ckl", "conflict")])
            ci_out = {"files": [{"name": n, "sha256": n, "checklists": 1} for n in ("new.ckl", "old.ckl")], "checklists": cks, "left": left}
            moved = dict((x["name"], x["move"]) for x in cf.stig_ckl_settled(ci_out, [i for i in plan if i["kind"] == "import"]))
            self.assertEqual(moved, {"new.ckl": False, "old.ckl": False})
        # the newer one disagrees with STIG Manager, an older one agrees: they disagree too - a person decides
        swapped = [dict(files[0], modified="2025-01-01 10:00 UTC"), dict(files[1], modified="2026-11-10 10:00 UTC")]
        cks, plan, left, _ = run(d, swapped)
        self.assertNotIn("SV-260002r1_rule", [p.get("ruleId") for p in plan])
        self.assertEqual(sorted((x["file"], x["kind"]) for x in left if x["version"] == "VRPE-8X-000002"),
                         [("new.ckl", "conflict"), ("old.ckl", "conflict")])
        # both say what STIG Manager has: same, from both files, nothing written
        cks, plan, left, _ = run(d, [dict(files[0]), dict(files[1], data=new)])
        self.assertNotIn("SV-260002r1_rule", [p.get("ruleId") for p in plan])
        self.assertEqual(sorted((x["file"], x["kind"]) for x in left if x["version"] == "VRPE-8X-000002"),
                         [("new.ckl", "same"), ("old.ckl", "same")])
        # one checklist only, as STIG Manager: same, as before
        cks, plan, left, _ = run(d, [files[0]])
        self.assertEqual([x["kind"] for x in left if x["version"] == "VRPE-8X-000002"], ["same"])

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

    def test_asset_made_by_an_earlier_run(self):
        """1.24.0: the plan's asset is there already. Made by an earlier run of this plan (its name, in the plan's collection,
        the description this job gives it): its reviews are written to it, each read again first. Made by anyone else: nothing
        written - a state that fails the job (tasks/main.yml _su_bad)."""
        desc = "Created by the checklist import (site-automation), approved in AAP: vrops01/apache.ckl"
        plan = [{"kind": "create", "collectionId": "5", "asset": "vrops01", "fqdn": "", "benchmark": ARIA, "benchmarks": [ARIA],
                 "revision": "V1R1", "description": desc, "line": "NEW ASSET vrops01"}]
        plan += [{"kind": "import", "collectionId": "5", "assetId": "", "new_asset": "vrops01", "ruleId": "SV-26000%dr1_rule" % n,
                  "result": "pass", "status": "submitted", "was": {"result": "", "status": "", "ts": ""}, "detail": "x", "engine": None,
                  "line": "vrops01 V-26000%d" % n} for n in (1, 2)]

        class C(object):
            def __init__(self, there, db=None):
                self.there, self.db, self.made, self.posts, self.read = there, dict(db or {}), [], [], []

            def grants(self):
                return {"5": 4}

            def assets(self, c):
                return self.there

            def create(self, c, name, fqdn, benches, description="", ip="", mac=""):
                self.made.append(name)
                return {"assetId": "99", "stigs": [{"benchmarkId": ARIA, "revisionStr": "V1R1"}]}

            def review(self, c, a, rule):
                self.read.append((a, rule))
                return self.db.get(rule)

            def write(self, c, a, reviews):
                self.posts.append((a, [r["ruleId"] for r in reviews]))
                for r in reviews:
                    self.db[r["ruleId"]] = {"result": r["result"], "status": {"label": r["status"]}, "ts": "t9"}
                return {"rejected": []}

            def asset_reviews(self, c, a):
                return dict((k, smu.held(v)) for k, v in self.db.items())
        # the earlier run created it, then one review was written before it failed: the other is written now
        mine = {"assetId": "77", "name": "VROPS01", "description": desc, "stigs": [{"benchmarkId": ARIA, "revisionStr": "V1R1"}]}
        c = C([mine], {"SV-260001r1_rule": {"result": "pass", "status": {"label": "submitted"}, "ts": "t1"}})
        items = smu.update({"plan": plan, "skip_attachments": False}, c, False)
        self.assertEqual([i["state"] for i in items], ["there from an earlier run", "already so", "written"])
        self.assertEqual((items[0]["assetId"], c.made, c.posts), ("77", [], [("77", ["SV-260002r1_rule"])]))
        self.assertEqual(sorted(c.read), [("77", "SV-260001r1_rule"), ("77", "SV-260002r1_rule"), ("77", "SV-260002r1_rule")])
        # a review someone wrote meanwhile is left
        c = C([mine], {"SV-260002r1_rule": {"result": "fail", "status": {"label": "saved"}, "ts": "t5"}})
        self.assertEqual([i["state"] for i in smu.update({"plan": plan, "skip_attachments": False}, c, False)],
                         ["there from an earlier run", "written", "changed since"])
        # check mode: read, nothing written
        c = C([mine])
        self.assertEqual([i["state"] for i in smu.update({"plan": plan, "skip_attachments": False}, c, True)],
                         ["there from an earlier run", "would write", "would write"])
        self.assertEqual(c.posts, [])
        # made by a person (another description, or none): not used - states the job fails on
        for other in (dict(mine, description="web server"), dict(mine, description=None), dict(mine, name="vrops01.example.mil")):
            c = C([other])
            items = smu.update({"plan": plan, "skip_attachments": False}, c, False)
            self.assertEqual([i["state"] for i in items], ["there since the check", "asset not created", "asset not created"])
            self.assertEqual((c.made, c.posts, c.read), ([], [], []))
            self.assertIn("run the import again", items[0]["why"])
        with open(os.path.join(ROOT, "roles", "stigman_update", "tasks", "main.yml")) as f:
            bad = f.read().split("_su_bad:")[1].split("\n  when:")[0]
        for state in ("there since the check", "asset not created", "rejected", "error"):
            self.assertIn("'%s'" % state, bad)

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


# ---- 0.76.0: several collections, auto, the files moved --------------------------------------------------------------
def multi():
    """Watcher (the staging collection), Ops, Lab, RHEL - no assets yet; the Aria STIG at V1R1 and V1R2."""
    d = data()
    d["collections"] = [{"collectionId": "5", "name": "Watcher"}, {"collectionId": "7", "name": "Ops"},
                        {"collectionId": "8", "name": "Lab"}, {"collectionId": "9", "name": "RHEL"}]
    d["assets"] = {"5": [], "7": [], "8": [], "9": []}
    d["collection_stigs"] = {"5": [], "7": [], "8": [], "9": []}
    d["rules"][(ARIA, "V1R2")] = aria_rules("r2")
    return d


def asset(aid, name, *stigs):
    return {"assetId": aid, "name": name, "fqdn": "", "stigs": [{"benchmarkId": b, "revisionStr": r} for b, r in stigs]}


def host(name):
    """The fixture, for another host."""
    return fixture().replace(b"vrops01", name.encode())


def run_many(d, collections=("*",), files=None, **kw):
    files = files if files is not None else [{"name": "vrops01/apache.ckl", "data": fixture(), "modified": "2026-11-05 10:00 UTC"}]
    f = Fake(d)
    out = ci.plan_all(params(collection="Watcher", collections=list(collections), **kw), f, copy.deepcopy(files))
    return out[:5] + (f,)            # (1.26.0 adds the routes and the moves held: run_routes)


class Collections(unittest.TestCase):
    def test_pick(self):
        cols = multi()["collections"]
        st, s = ci.pick_collections(cols, ["*"], "watcher")
        self.assertEqual(st, {"collectionId": "5", "name": "Watcher"})
        self.assertEqual([c["name"] for c in s], ["Lab", "Ops", "RHEL", "Watcher"], "the staging one last")
        self.assertEqual([c["name"] for c in ci.pick_collections(cols, ["ops", "L*", "Nothing*", "7"], "Watcher")[1]], ["Ops", "Lab", "Watcher"])
        self.assertEqual([c["name"] for c in ci.pick_collections(cols, [], "Watcher")[1]], ["Watcher"], "[]: as 0.75.0")
        with self.assertRaises(ci.Fail) as e:
            ci.pick_collections(cols, ["Nope"], "Watcher")
        self.assertIn("no collection Nope (stigman_ckl_import_collections)", str(e.exception))
        with self.assertRaises(ci.Fail):
            ci.pick_collections(cols, ["*"], "Nope")
        # the client's grants: Restricted anywhere searched - refused (it may not see the host's asset there)
        full = {"5": 3, "7": 2, "8": 2, "9": 2}
        self.assertEqual(len(ci.pick_collections(cols, ["*"], "Watcher", full)[1]), 4)
        with self.assertRaises(ci.Fail) as e:
            ci.pick_collections(cols, ["*"], "Watcher", dict(full, **{"8": 1}))
        self.assertIn("the client has only Restricted in Lab - it may not see every asset there", str(e.exception))
        self.assertIn("created again in Watcher", str(e.exception))
        self.assertEqual(len(ci.pick_collections(cols, ["Ops"], "Watcher", dict(full, **{"8": 1}))[1]), 2, "Lab not searched: fine")
        d = multi()
        d["user"] = {"userId": "1", "collectionGrants": [{"roleId": r, "collection": {"collectionId": c}} for c, r in dict(full, **{"9": 1}).items()]}
        with self.assertRaises(ci.Fail):
            run_many(d)
        self.assertEqual(ci.my_grants(Fake(d)), dict(full, **{"9": 1}))
        self.assertIsNone(ci.my_grants(Fake(multi())), "no grants in the answer: not known, not refused")

    def test_written_where_it_is_not_in_staging(self):
        d = multi()
        d["assets"]["7"] = [asset("71", "vrops01", (ARIA, "V1R1"))]
        d["assets"]["5"] = [asset("51", "vrops01", (ARIA, "V1R1"))]
        cks, plan, left, st, searched, f = run_many(d)
        self.assertEqual(searched, ["Lab", "Ops", "RHEL", "Watcher"])
        self.assertEqual(sorted(set((p["collectionId"], p["assetId"], p["collection"]) for p in plan)), [("7", "71", "Ops")])
        self.assertEqual(len(plan), 3)
        self.assertEqual([(c["collection"], c["state"]) for c in cks], [("Ops", "planned"), ("Watcher", "in staging too")])
        self.assertIn("delete it from Watcher", cks[1]["why"])
        self.assertTrue(plan[0]["line"].startswith("Ops/vrops01 %s VRPE-8X-000001" % ARIA), plan[0]["line"])
        self.assertEqual(cf.stig_ckl_lines(cks, True)[0].split(" -> ")[1][:12], "Ops/vrops01 ")
        # the staging collection alone has it: written there
        d["assets"]["7"] = []
        cks, plan, left, st, searched, f = run_many(d)
        self.assertEqual(sorted(set(p["collectionId"] for p in plan)), ["5"])

    def test_each_collection_that_has_the_stig_at_its_revision(self):
        d = multi()
        d["assets"]["7"] = [asset("71", "vrops01", (ARIA, "V1R1"))]
        d["assets"]["8"] = [asset("81", "vrops01.example.mil", (ARIA, "V1R2"))]
        d["assets"]["9"] = [asset("91", "VROPS01", ("RHEL_9_STIG", "V2R9"))]
        cks, plan, left, st, searched, f = run_many(d)
        got = sorted((p["collection"], p["revision"], p["ruleId"]) for p in plan)
        self.assertEqual(got, [("Lab", "V1R2", "SV-260001r2_rule"), ("Lab", "V1R2", "SV-260002r2_rule"), ("Lab", "V1R2", "SV-260005r2_rule"),
                               ("Ops", "V1R1", "SV-260001r1_rule"), ("Ops", "V1R1", "SV-260002r1_rule"), ("Ops", "V1R1", "SV-260005r1_rule")])
        self.assertEqual(dict((c["collection"], c["state"]) for c in cks), {"Lab": "planned", "Ops": "planned", "RHEL": "STIG not on this asset"})
        self.assertIn("written where it has it (Lab, Ops)", [c for c in cks if c["collection"] == "RHEL"][0]["why"])
        self.assertEqual(cf.STATE_CHIP["STIG not on this asset"], "info", "not a problem: its reviews went elsewhere")
        self.assertEqual(sorted(c for c in f.calls if c.startswith("rules " + ARIA)), ["rules %s V1R1" % ARIA, "rules %s V1R2" % ARIA])
        # none has the STIG: as 0.75.0 - assign it
        d["assets"]["7"], d["assets"]["8"] = [], []
        cks, plan, left, st, searched, f = run_many(d)
        self.assertEqual(([c["state"] for c in cks], plan), (["no STIG on the asset"], []))

    def test_several_assets_fit_in_one_collection(self):
        d = multi()
        d["assets"]["7"] = [asset("71", "vrops01", (ARIA, "V1R1")), dict(asset("72", "vrops01-old", (ARIA, "V1R1")), fqdn="vrops01.example.mil")]
        d["assets"]["8"] = [asset("81", "vrops01", (ARIA, "V1R1"))]
        cks, plan, left, st, searched, f = run_many(d)
        self.assertEqual(sorted(set(p["collection"] for p in plan)), ["Lab"])
        self.assertEqual([(c["collection"], c["state"]) for c in cks if c["state"] == "no asset"], [("Ops", "no asset")])
        self.assertIn("several assets in Ops fit vrops01", [c for c in cks if c["collection"] == "Ops"][0]["why"])

    def test_new_in_the_staging_collection(self):
        cks, plan, left, st, searched, f = run_many(multi())
        self.assertEqual((plan[0]["kind"], plan[0]["collection"], plan[0]["collectionId"], plan[0]["import"]), ("create", "Watcher", "5", True))
        self.assertEqual(set(p["collectionId"] for p in plan), {"5"})
        self.assertEqual((cks[0]["collection"], cks[0]["new"]), ("Watcher", True))
        cks, plan, left, st, searched, f = run_many(multi(), create_assets=False)
        self.assertIn("no asset vrops01 in Lab, Ops, RHEL, Watcher", cks[0]["why"])

    def test_auto(self):
        d = multi()
        d["assets"]["7"] = [asset("71", "vrops01", (ARIA, "V1R1"))]
        files = [{"name": "vrops01.ckl", "data": fixture(), "modified": "1"}, {"name": "vrops03.ckl", "data": host("vrops03"), "modified": "1"}]
        plan = run_many(d, files=files)[1]
        self.assertEqual(set(p["auto"] for p in plan), {False}, "none: everything waits for the approval")
        self.assertIn("approved in AAP", plan[-1]["comment"])
        plan = run_many(d, files=files, auto="existing")[1]
        self.assertEqual([(p.get("kind"), p.get("new_asset", ""), p["auto"]) for p in plan],
                         [("create", "", False)] + [("import", "vrops03", False)] * 3 + [("import", "", True)] * 3)
        self.assertIn("no approval (stigman_ckl_import_auto)", [p for p in plan if p["auto"]][0]["comment"])
        self.assertIn("approved in AAP", plan[1]["comment"])
        plan = run_many(d, files=files, auto="all")[1]
        self.assertEqual(set(p["auto"] for p in plan), {True})
        self.assertNotIn("approved", plan[0]["description"])

    def test_same_answer_twice_names_both_files(self):
        d = multi()
        d["assets"]["7"] = [asset("71", "vrops01", (ARIA, "V1R1"))]
        files = [{"name": "a.ckl", "data": fixture(), "modified": "1"}, {"name": "b.ckl", "data": fixture(), "modified": "2"}]
        plan = run_many(d, files=files)[1]
        self.assertEqual(set((p["file"], tuple(p["files"])) for p in plan), {("b.ckl", ("a.ckl", "b.ckl"))})


class Settled(unittest.TestCase):
    """Which files are done with, to be moved (stig_ckl_settled)."""
    def ci(self):
        return {"files": [{"name": n, "sha256": n, "checklists": 1} for n in ("a.ckl", "b.ckl", "e.ckl", "f.ckl", "g.ckl")]
                + [{"name": "c.ckl", "sha256": "c", "checklists": 0}, {"name": "d.ckl", "error": "not a checklist"}],
                "checklists": [{"file": "e.ckl", "state": "not matched", "why": "no STIG"}, {"file": "a.ckl", "state": "nothing to write"},
                               {"file": "g.ckl", "state": "STIG not on this asset"}],
                "left": [{"file": "a.ckl", "kind": "same"}, {"file": "a.ckl", "kind": "unmatched"}, {"file": "a.ckl", "kind": "accepted"},
                         {"file": "f.ckl", "kind": "conflict", "why": "the checklists disagree"}]}

    def moves(self, *a, **kw):
        return dict((x["name"], x["move"]) for x in cf.stig_ckl_settled(self.ci(), *a, **kw))

    def test_held_and_moved(self):
        got = cf.stig_ckl_settled(self.ci(), [{"kind": "import", "file": "b.ckl", "files": ["b.ckl"], "line": "L"}])
        self.assertEqual(dict((x["name"], x["move"]) for x in got),
                         {"a.ckl": True, "b.ckl": False, "c.ckl": False, "d.ckl": False, "e.ckl": False, "f.ckl": False, "g.ckl": True})
        why = dict((x["name"], x["why"]) for x in got)
        self.assertEqual((why["b.ckl"], why["c.ckl"], why["d.ckl"]), ("waits for the approval (L)", "no STIG in it", "not a checklist"))
        self.assertTrue(why["f.ckl"].startswith("Checklists disagree"))
        self.assertEqual(got[0]["sha256"], "a.ckl")

    def test_written_at_once(self):
        auto = [{"kind": "import", "file": "a.ckl", "files": ["a.ckl", "g.ckl"]}]
        self.assertFalse(self.moves([], auto, {"items": [{"n": 0, "state": "rejected", "line": "x", "why": "y"}]})["g.ckl"])
        self.assertTrue(self.moves([], auto, {"items": [{"n": 0, "state": "written"}]})["g.ckl"])
        self.assertTrue(self.moves([], auto, {"items": [{"n": 0, "state": "already so"}]})["a.ckl"])
        self.assertFalse(self.moves([], auto, {"items": [{"n": 0, "state": "would write"}]})["a.ckl"], "check mode: not written")
        self.assertFalse(self.moves([], auto, {"items": [], "error": ""})["a.ckl"], "the write did not run")
        self.assertEqual(set(self.moves([], auto, {"items": [], "error": "no token"}).values()), {False})
        self.assertEqual(set(self.moves([], [], None, {}, "HTTP 500").values()), {False}, "attachments not read: nothing moved")
        self.assertFalse(self.moves([], [], None, {"left": [{"file": "a.ckl", "attachments": -1}]})["a.ckl"])
        self.assertTrue(self.moves([], [], None, {"left": [{"file": "a.ckl", "attachments": 2}]})["a.ckl"])
        bad = dict(self.ci(), error="no token")
        self.assertEqual(set(x["move"] for x in cf.stig_ckl_settled(bad)), {False})


class Move(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.d, "sub"))
        os.makedirs(os.path.join(self.d, "Imported", "2026-11-01"))
        for n in ("sub/a.ckl", "b.ckl", "Imported/2026-11-01/old.ckl"):
            with open(os.path.join(self.d, n), "wb") as f:
                f.write(fixture())
        self.sha = hashlib.sha256(fixture()).hexdigest()
        self.p = {"src": self.d, "base": "", "patterns": ["*.ckl"], "recurse": True, "max_files": 10, "max_mb": 1, "move_to": "imported",
                  "day": "2026-11-06", "port": 445, "username": "", "password": "", "auth": "negotiate", "encrypt": False,
                  "move": [{"name": "sub/a.ckl", "sha256": self.sha}, {"name": "b.ckl", "sha256": "0" * 64}, {"name": "../x.ckl", "sha256": ""}]}

    def test_the_folder_is_never_read(self):
        self.assertEqual([g["name"] for g in ci.read_files(self.p)], ["b.ckl", "sub/a.ckl"])
        self.assertEqual(len(ci.read_files(dict(self.p, move_to=""))), 3)

    def test_move(self):
        got = ci.move_files(self.p, True)
        self.assertEqual([g["state"] for g in got], ["would move", "changed since", "error"])
        self.assertTrue(os.path.exists(os.path.join(self.d, "sub", "a.ckl")), "check mode: nothing moved")
        got = ci.move_files(self.p, False)
        self.assertEqual([(g["state"], g["to"]) for g in got], [("moved", "imported/2026-11-06/sub/a.ckl"), ("changed since", ""), ("error", "")])
        self.assertTrue(os.path.exists(os.path.join(self.d, "imported", "2026-11-06", "sub", "a.ckl")))
        self.assertFalse(os.path.exists(os.path.join(self.d, "sub", "a.ckl")))
        self.assertTrue(os.path.exists(os.path.join(self.d, "b.ckl")), "written again since it was read: left")
        with open(os.path.join(self.d, "sub", "a.ckl"), "wb") as f:      # the same file again, the same day: a second name
            f.write(fixture())
        self.assertEqual(ci.move_files(self.p, False)[0]["to"], "imported/2026-11-06/sub/a-2.ckl")
        self.assertEqual(ci.move_files(dict(self.p, move_to="../up"), False)[0]["state"], "error")

    def test_safe_name(self):
        self.assertEqual([ci.safe_name(x) for x in ("a.ckl", "s/a.ckl", "../a", "/a", "s/../a", "", "C:/a", "s//a")],
                         [True, True, False, False, False, False, False, False])


class Grants(unittest.TestCase):
    """The update with the import's items: written only where the client has Full or more, created where Manage."""
    def item(self, c, name):
        return {"kind": "import", "collectionId": c, "collection": name, "assetId": "4" + c, "ruleId": "SV-260001r1_rule", "result": "pass",
                "status": "submitted", "was": {"result": "", "status": "", "ts": ""}, "detail": "x", "engine": None, "line": name}

    def test_no_grant(self):
        it = self.item("7", "Ops")
        self.assertEqual(smu.no_grant(None, it, 2), "", "not read: STIG Manager decides")
        self.assertIn("Restricted in Ops - the import writes only where it needs Full or more (a Restricted grant", smu.no_grant({"7": 1}, it, 2))
        self.assertEqual(smu.no_grant({"7": 2}, it, 2), "")
        self.assertIn("no grant in Ops", smu.no_grant({}, it, 2))
        new = {"kind": "create", "import": True, "collectionId": "5", "collection": "Watcher"}
        self.assertIn("Full in Watcher - creating an asset needs Manage", smu.no_grant({"5": 2}, new, 3))
        self.assertEqual(smu.no_grant({"5": 3}, new, 3), "")
        self.assertEqual(smu.no_grant({"7": 1}, {"collectionId": "7"}, 2), "", "workflow 37's items: as before")

    def test_update(self):
        class C(object):
            def __init__(self):
                self.posts, self.db = [], {}

            def grants(self):
                return {"7": 1, "8": 2}

            def review(self, c, a, rule):
                return self.db.get((c, rule))

            def write(self, c, a, reviews):
                self.posts.append(c)
                for r in reviews:
                    self.db[(c, r["ruleId"])] = {"result": r["result"], "status": {"label": r["status"]}}
                return {"rejected": []}
        c = C()
        items = smu.update({"plan": [self.item("7", "Ops"), self.item("8", "Lab")], "skip_attachments": False}, c, False)
        self.assertEqual([(i["line"], i["state"]) for i in items], [("Ops", "no grant"), ("Lab", "written")])
        self.assertEqual(c.posts, ["8"])
        r = __import__("stig_filters").stig_update_report(items)
        self.assertEqual(r["title"], "STIG Manager update: 1 review(s) written, 1 not")


class Report(unittest.TestCase):
    def test_auto_and_moved(self):
        d = multi()
        d["assets"]["7"] = [asset("71", "vrops01", (ARIA, "V1R1"))]
        files = [{"name": "vrops01.ckl", "data": fixture(), "modified": "1"}, {"name": "vrops03.ckl", "data": host("vrops03"), "modified": "1"}]
        cks, plan, left, st, searched, f = run_many(d, files=files, auto="existing")
        res = {"files": [{"name": x["name"], "sha256": "s", "checklists": 1} for x in files], "checklists": cks, "plan": plan, "left": left,
               "collection": st, "collections": searched, "source": "/ckl", "error": ""}
        auto = [p for p in plan if p["auto"]]
        wait = [p for p in plan if not p["auto"]]
        su = {"items": [{"n": n, "state": "written", "line": p["line"], "result": p["result"]} for n, p in enumerate(auto)], "error": ""}
        settled = cf.stig_ckl_settled(res, wait, auto, su)
        self.assertEqual([(x["name"], x["move"]) for x in settled], [("vrops01.ckl", True), ("vrops03.ckl", False)])
        moved = [{"name": "vrops01.ckl", "to": "imported/2026-11-06/vrops01.ckl", "state": "moved", "why": ""}]
        r = cf.stig_ckl_report(res, wait, [], True, 200, "existing", auto, su, False, moved, settled, "imported")
        self.assertEqual(r["title"], "Checklist import: 3 review(s) written, 3 to approve with 1 new asset(s)")
        t = [s["title"] for s in r["sections"]]
        self.assertEqual(t[:2], ["Written at once, no approval: 3 review(s)", "Approve writes these reviews to STIG Manager and creates 1 asset"])
        self.assertIn("Files: 1 moved to imported, 1 left", t)
        each = [s for s in r["sections"] if s["title"] == "Each checklist"][0]
        self.assertEqual([(x[3], x[9]) for x in each["rows"]], [("Ops / vrops01", "written"), ("Watcher / vrops03 (new)", "planned")])
        self.assertEqual(dict((x["label"], x["value"]) for x in r["summary"])["Written"], 3)
        # all of it written at once: nothing waits
        r = cf.stig_ckl_report(res, [], [], True, 200, "all", plan, {"items": [{"n": n, "state": "written"} for n in range(len(plan))]})
        self.assertEqual([s["title"] for s in r["sections"]][1], "Nothing waits for an approval")
        # the write failed: said first
        r = cf.stig_ckl_report(res, wait, [], True, 200, "existing", auto, {"items": [], "error": "no token for the client x"})
        self.assertEqual((r["status"], r["sections"][0]["title"]), ("critical", "Nothing written at once"))


# ---- 1.26.0: routes - a host's collection by its STIG, name, role; moved out of staging; a STIG assigned -----------------
ESXI = "VMW_vSphere_8-0_ESXi_STIG"


def rhel_rules():
    """STIG Manager's rules of RHEL 9 V2R9 (three)."""
    return [{"ruleId": "SV-2570%02dr1_rule" % n, "groupId": "V-2570%02d" % n, "version": "RHEL-09-0000%02d" % n,
             "severity": "medium", "title": "RHEL rule %d" % n} for n in (1, 2, 3)]


def routed():
    """Staging (the staging collection), Windows, Linux, VMware - no assets yet; the Aria STIG at V1R1 and V1R2, RHEL 9
    at V2R9; GET /user gives no grants (not known: nothing refused for them), no collection has grants."""
    d = data()
    d["collections"] = [{"collectionId": "5", "name": "Staging"}, {"collectionId": "7", "name": "Windows"},
                        {"collectionId": "8", "name": "Linux"}, {"collectionId": "9", "name": "VMware"}]
    d["assets"] = {"5": [], "7": [], "8": [], "9": []}
    d["collection_stigs"] = {"5": [], "7": [], "8": [], "9": []}
    d["rules"][(ARIA, "V1R2")] = aria_rules("r2")
    d["rules"][("RHEL_9_STIG", "V2R9")] = rhel_rules()
    return d


def give(d, roles):
    """The client's grants (GET /user): {collectionId: roleId}."""
    d["user"] = {"userId": "1", "collectionGrants": [{"roleId": r, "collection": {"collectionId": c}} for c, r in roles.items()]}


def aria(name="vc01", role="", tech=""):
    """The Aria Apache CKL for another host, with its ROLE and TECH_AREA."""
    raw = host(name).replace(b"<ROLE>None</ROLE>", b"<ROLE>" + role.encode() + b"</ROLE>")
    return raw.replace(b"<TECH_AREA>\n</TECH_AREA>", b"<TECH_AREA>" + tech.encode() + b"</TECH_AREA>")


def rhel(name="rhel01", role="", tech=""):
    """A RHEL 9 CKLB (STIG Viewer 3): Not a Finding, Open, Not Applicable - its role and technology_area."""
    rules = [{"rule_id": r["ruleId"], "group_id": r["groupId"], "rule_version": r["version"], "severity": "medium", "rule_title": r["title"],
              "status": st, "finding_details": "Found by the scan."} for r, st in zip(rhel_rules(), ("not_a_finding", "open", "not_applicable"))]
    return json.dumps({"title": "Evaluate-STIG", "cklb_version": "1.0",
                       "target_data": {"host_name": name, "fqdn": name + ".example.mil", "ip_address": "192.0.2.51", "role": role,
                                       "technology_area": tech},
                       "stigs": [{"stig_id": "RHEL_9_STIG", "stig_name": "Red Hat Enterprise Linux 9 STIG", "version": "2",
                                  "release_info": "Release: 9 Benchmark Date: 30 Sep 2026", "rules": rules}]}).encode()


def ckfile(name, raw):
    return {"name": name, "data": raw, "modified": "2026-11-10 10:00 UTC"}


def staged(aid="51", name="vc01", stigs=((ARIA, "V1R1"),), labels=("web",), ts="2026-11-01T08:00:00Z"):
    """An asset as GET /assets gives it (projections stigs, statusStats), with its labels."""
    a = asset(aid, name, *stigs)
    a.update(fqdn=name + ".example.mil", labels=[{"labelId": "L-" + x, "name": x} for x in labels], statusStats={"maxTs": ts})
    return a


def run_routes(d, files, routes, **kw):
    """plan_all with routes -> (checklists, plan, left, staging, searched, routes, held, the Fake)."""
    f = Fake(d)
    kw.setdefault("collection", "Staging")
    out = ci.plan_all(params(routes=routes, **kw), f, copy.deepcopy(files))
    return tuple(out) + (f,)


VC = [ckfile("vc01.ckl", aria("vc01", "Member Server", "Web Server"))]
TO_VMWARE = [{"collection": "VMware", "stigs": "VMW_*"}]


class Routes(unittest.TestCase):
    """stigman_ckl_import_routes: where a host's asset is created, or moved to out of the staging collection."""

    def where(self, routes, files=VC, d=None):
        """The collections the plan creates the host's asset in."""
        plan = run_routes(d or routed(), files, routes)[1]
        return [p["collection"] for p in plan if p["kind"] == "create"]

    def test_rules_in_order_each_key_must_fit(self):
        # the first rule in the list's order wins
        self.assertEqual(self.where([{"collection": "Windows", "names": ["vc*"]}, {"collection": "VMware", "names": ["vc01"]}]), ["Windows"])
        self.assertEqual(self.where([{"collection": "VMware", "names": ["vc01"]}, {"collection": "Windows", "names": ["vc*"]}]), ["VMware"])
        # stigs: the matched installed STIG's benchmark id or its title, any case - never the checklist's own STIG id
        for pat in (["vmw_aria_*"], ["*Aria Operations 8.x Apache Server Security*"], ["RHEL_*", "VMW_Aria_Operations_8-x_Apache_STIG"]):
            self.assertEqual(self.where([{"collection": "VMware", "stigs": pat}]), ["VMware"], pat)
        self.assertEqual(self.where([{"collection": "VMware", "stigs": ["*Apache Profile"]}]), ["Staging"], "the checklist's STIG id")
        # names: the host name or its FQDN
        for pat in (["VC01"], ["vc01.example.mil"], ["*.EXAMPLE.MIL"]):
            self.assertEqual(self.where([{"collection": "VMware", "names": pat}]), ["VMware"], pat)
        self.assertEqual(self.where([{"collection": "VMware", "names": ["esx*", "vc01.yoursite.mil"]}]), ["Staging"])
        # roles and tech_areas: a CKL's ROLE and TECH_AREA
        self.assertEqual(self.where([{"collection": "Windows", "roles": ["member*"]}]), ["Windows"])
        self.assertEqual(self.where([{"collection": "Windows", "tech_areas": ["WEB SERVER"]}]), ["Windows"])
        self.assertEqual(self.where([{"collection": "Windows", "roles": ["domain controller"]}]), ["Staging"])
        self.assertEqual(self.where([{"collection": "Windows", "tech_areas": ["unix*"]}]), ["Staging"])
        # a CKLB's role and technology_area
        rh = [ckfile("rhel01.cklb", rhel("rhel01", "Member Server", "UNIX OS"))]
        self.assertEqual(self.where([{"collection": "Linux", "roles": ["member server"], "tech_areas": ["unix*"]}], rh), ["Linux"])
        self.assertEqual(self.where([{"collection": "Linux", "tech_areas": ["windows*"]}], rh), ["Staging"])
        self.assertEqual(self.where([{"collection": "Linux", "roles": ["workstation"]}], rh), ["Staging"])
        # each key given must fit: the STIG fits, the name does not - the next rule
        self.assertEqual(self.where([{"collection": "VMware", "stigs": ["VMW_*"], "names": ["esx*"]}, {"collection": "Linux"}]), ["Linux"])
        self.assertEqual(self.where([{"collection": "VMware", "stigs": ["VMW_*"], "names": ["vc*"], "roles": ["member*"],
                                      "tech_areas": ["web*"]}]), ["VMware"])
        # text: patterns separated by commas or lines, never cut at a space
        self.assertEqual(self.where([{"collection": "VMware", "names": "esx01, vc01"}]), ["VMware"])
        self.assertEqual(self.where([{"collection": "VMware", "names": "esx01\nvc01"}]), ["VMware"])
        self.assertEqual(self.where([{"collection": "Windows", "tech_areas": "Domain Controller,Web Server"}]), ["Windows"])
        self.assertEqual(self.where([{"collection": "Windows", "tech_areas": "Domain Controller Web"}]), ["Staging"])
        rts = ci.read_routes([{"collection": "VMware", "stigs": ["VMW_*"], "names": "esx01, vc01"}, {"collection": "9"}], routed()["collections"])
        self.assertEqual([(r["n"], r["want"], r["text"], r["collection"], r["names"]) for r in rts],
                         [(1, "VMware", "stigs VMW_*; names esx01, vc01", {"collectionId": "9", "name": "VMware"}, ["esx01", "vc01"]),
                          (2, "9", "every host", {"collectionId": "9", "name": "VMware"}, [])])
        # no rule fits: the staging collection (1.26.0 tester fix: the route text says so - "no route fits - created in Staging")
        cks, plan, left, st, searched, rts, held, f = run_routes(routed(), VC, [{"collection": "VMware", "names": ["esx*"]}])
        self.assertEqual((plan[0]["collection"], plan[0]["route"], cks[0]["route"]), ("Staging", 0, "no route fits - created in Staging"))
        self.assertNotIn("(route", plan[0]["line"])
        cks, plan, left, st, searched, rts, held, f = run_routes(routed(), VC, TO_VMWARE)
        self.assertEqual((plan[0]["route"], cks[0]["route"]), (1, "route 1 (stigs VMW_*) -> VMware"))
        self.assertIn("in VMware (route 1) with", plan[0]["line"])

    def test_two_stigs_two_routes_the_first_rule_wins(self):
        files = [ckfile("rhel01.ckl", aria("rhel01")), ckfile("rhel01.cklb", rhel("rhel01"))]
        routes = [{"collection": "Linux", "stigs": "RHEL_*"}, {"collection": "VMware", "stigs": "VMW_*"}]
        cks, plan, left, st, searched, rts, held, f = run_routes(routed(), files, routes)
        new = [p for p in plan if p["kind"] == "create"]
        self.assertEqual([(p["collection"], sorted(p["benchmarks"])) for p in new], [("Linux", sorted([ARIA, "RHEL_9_STIG"]))])
        self.assertEqual(set(p["collectionId"] for p in plan), {"8"}, "both checklists' reviews: one asset, in Linux")
        self.assertEqual(set(c["route"] for c in cks), {"route 1 (stigs RHEL_*) -> Linux; it fits route 2 (VMware, by %s) too - the first "
                                                         "rule in the list wins" % ARIA})
        cks, plan, left, st, searched, rts, held, f = run_routes(routed(), files, routes[::-1])
        self.assertEqual(set(p["collectionId"] for p in plan), {"9"})
        self.assertEqual(set(c["route"] for c in cks), {"route 1 (stigs VMW_*) -> VMware; it fits route 2 (Linux, by RHEL_9_STIG) too - the "
                                                         "first rule in the list wins"})
        # a later rule to the same collection is not another route
        cks = run_routes(routed(), files, [routes[0], dict(routes[1], collection="Linux")])[0]
        self.assertEqual(set(c["route"] for c in cks), {"route 1 (stigs RHEL_*) -> Linux"})

    def test_new_host_in_its_routes_collection_at_its_revision(self):
        d = routed()
        d["collection_stigs"]["9"] = [{"benchmarkId": ARIA, "revisionStr": "V1R2"}]      # VMware has V1R2, the staging one none
        cks, plan, left, st, searched, rts, held, f = run_routes(d, VC, TO_VMWARE)
        # 1.26.0 tester fix: with routes every collection the client reads is searched (stigman_ckl_import_collections: [] here)
        self.assertEqual(searched, ["Linux", "VMware", "Windows", "Staging"], "every collection read is searched")
        new = plan[0]
        self.assertEqual((new["kind"], new["collection"], new["collectionId"], new["revision"], new["route"]), ("create", "VMware", "9", "V1R2", 1))
        self.assertEqual([(p["collectionId"], p["revision"], p["ruleId"], p["new_asset"]) for p in plan[1:]],
                         [("9", "V1R2", "SV-26000%dr2_rule" % n, "vc01") for n in (1, 2, 5)])
        self.assertEqual((cks[0]["collection"], cks[0]["new"], cks[0]["revision"]), ("VMware", True, "V1R2"))
        # no route: the staging collection's (none: the newest installed, V1R1)
        plan = run_routes(d, VC, [])[1]
        self.assertEqual((plan[0]["collection"], plan[0]["revision"], plan[1]["ruleId"]), ("Staging", "V1R1", "SV-260001r1_rule"))

    def test_a_route_to_a_collection_not_read(self):
        """1.26.0 tester fix: a route whose collection the client cannot read (a typo, no grant) or that is not one name or id
        fails the plan before anything is read but the collections - before, its hosts were written in the staging
        collection, their files moved, and the route never done."""
        for routes, said in (([{"collection": "Linx", "stigs": "RHEL_*"}],
                              "stigman_ckl_import_routes, rule 1: no collection Linx that the client can read (it reads: Linux, Staging, VMware, "
                              "Windows) - check the name, or give the client a grant there; nothing planned"),
                             ([{"collection": "VMware"}, {"collection": "Secret", "stigs": "VMW_*"}],
                              "stigman_ckl_import_routes, rule 2: no collection Secret that the client can read"),
                             ([{"collection": "12"}], "stigman_ckl_import_routes, rule 1: no collection 12 that the client can read"),
                             ([{"collection": ["Linux"]}],
                              "stigman_ckl_import_routes, rule 1: collection is one collection's name or id, not ['Linux'] - nothing planned"),
                             ([{"collection": {"name": "Linux"}}], "stigman_ckl_import_routes, rule 1: collection is one collection's name or id, not {"),
                             ([{"collection": True}], "stigman_ckl_import_routes, rule 1: collection is one collection's name or id, not True"),
                             ([{"stigs": "VMW_*"}], "stigman_ckl_import_routes, rule 1: collection is one collection's name or id, not None"),
                             ([{"collection": " "}], "stigman_ckl_import_routes, rule 1: collection is one collection's name or id, not ' '"),
                             (["VMware"], "stigman_ckl_import_routes, rule 1: 'VMware' is not a rule - each rule is {collection: "),
                             ([{"collection": "VMware"}, {"collection": "Linux", "hosts": "rhel*"}],
                              "stigman_ckl_import_routes, rule 2: hosts is not a rule's key (collection, stigs, names, roles, tech_areas)")):
            d = routed()
            d["assets"]["5"] = [staged()]           # a host in staging: nothing read of it, nothing planned
            f = Fake(d)
            with self.assertRaises(ci.Fail) as e:
                ci.plan_all(params(routes=routes, collection="Staging", auto="all"), f, copy.deepcopy(VC))
            self.assertTrue(str(e.exception).startswith(said), str(e.exception))
            self.assertEqual([c for c in f.calls if c not in ("/collections", "/user")], [], "nothing read but the collections: %s" % routes)
        # by its id, any case of its name: read
        self.assertEqual([r["collection"]["name"] for r in ci.read_routes([{"collection": 8}, {"collection": "vmWARE"}], routed()["collections"])],
                         ["Linux", "VMware"])

    def test_an_asset_only_in_staging_is_moved_to_its_route(self):
        d = routed()
        d["assets"]["5"] = [staged()]
        d["reviews"]["51"] = [{"ruleId": "SV-260001r1_rule", "result": "fail", "status": {"label": "saved"}, "ts": "2026-11-01T08:00:00Z"}]
        cks, plan, left, st, searched, rts, held, f = run_routes(d, VC, TO_VMWARE)
        mv = plan[0]
        self.assertEqual(dict((k, mv[k]) for k in ("kind", "assetId", "asset", "fromId", "from", "collectionId", "collection", "fqdn", "stigs",
                                                   "labels", "maxTs", "route", "files", "reviews", "auto", "import")),
                         {"kind": "move", "assetId": "51", "asset": "vc01", "fromId": "5", "from": "Staging", "collectionId": "9",
                          "collection": "VMware", "fqdn": "vc01.example.mil", "stigs": [ARIA], "labels": ["web"], "maxTs": "2026-11-01T08:00:00Z",
                          "route": 1, "files": ["vc01.ckl"], "reviews": 3, "auto": False, "import": True})
        self.assertEqual(mv["hidden"], ["the Restricted grants in Staging are checked when it is moved (reading them needs Manage)"],
                         "GET /user gave no grants: the update checks them")
        self.assertTrue(mv["line"].startswith("MOVE vc01 from Staging to VMware (route 1)") and mv["line"].endswith(" - then 3 review(s)"), mv["line"])
        revs = plan[1:]
        self.assertEqual(set((p["kind"], p["collectionId"], p["assetId"], p["moved_asset"], p["readFrom"], p["auto"]) for p in revs),
                         {("import", "9", "51", "51", "5", False)})
        self.assertEqual([(p["ruleId"], p["was"]["result"]) for p in revs][0], ("SV-260001r1_rule", "fail"), "its review, read in staging")
        self.assertIn("reviews 5 51 " + ARIA, f.calls)
        self.assertNotIn("reviews 9 51 " + ARIA, f.calls)
        self.assertEqual((cks[0]["collection"], cks[0]["assetId"], cks[0]["moving"], cks[0]["moved_from"], cks[0]["state"]),
                         ("VMware", "51", "51", "Staging", "planned"))
        self.assertIn("[moved from Staging first]", cf.stig_ckl_lines(cks)[0])
        self.assertEqual((held, rts[0]["error"]), ([], ""))
        # auto: existing - a moving asset waits for the approval; all - the move and its reviews at once
        plan = run_routes(d, VC, TO_VMWARE, auto="existing")[1]
        self.assertEqual([(p["kind"], p["auto"]) for p in plan], [("move", False)] + [("import", False)] * 3)
        plan = run_routes(d, VC, TO_VMWARE, auto="all")[1]
        self.assertEqual([(p["kind"], p["auto"]) for p in plan], [("move", True)] + [("import", True)] * 3)
        self.assertIn("no approval", plan[1]["comment"])

    def test_route_move_none(self):
        d = routed()
        d["assets"]["5"] = [staged()]
        cks, plan, left, st, searched, rts, held, f = run_routes(d, VC, TO_VMWARE, route_move="none")
        self.assertEqual([p["kind"] for p in plan], ["import"] * 3)
        self.assertEqual(set((p["collectionId"], p["assetId"], "moved_asset" in p, "readFrom" in p) for p in plan), {("5", "51", False, False)})
        self.assertEqual(cks[0]["route"], "route 1 (stigs VMW_*) -> VMware - not moved (stigman_ckl_import_route_move: none) - written in Staging")
        self.assertNotIn("moving", cks[0])

    def restricted(self, acl11, roles=None, **kw):
        """vc01 (51, label web) only in the staging collection, routed to VMware; the client's grants (Manage in both); the
        staging collection's grants: auditor1 Restricted (its access list: acl11), the group auditors Restricted (default none),
        admin1 Full (it reaches every asset - not a Restricted grant)."""
        d = routed()
        d["assets"]["5"] = [staged(), staged("52", "esx01", labels=("db",))]
        give(d, roles or {"5": 3, "7": 2, "8": 2, "9": 3})
        d["grants"] = {"5": [{"grantId": "11", "roleId": 1, "user": {"userId": "21", "username": "auditor1"}},
                             {"grantId": "12", "roleId": 1, "userGroup": {"userGroupId": "31", "name": "auditors"}},
                             {"grantId": "13", "roleId": 2, "user": {"userId": "22", "username": "admin1"}}]}
        d["acl"] = {("5", "11"): acl11, ("5", "13"): {"defaultAccess": "rw", "acl": []}}
        d.update(kw)
        return run_routes(d, VC, TO_VMWARE)

    def test_a_restricted_grant_in_staging_holds_the_move(self):
        reach = [{"defaultAccess": "r", "acl": []},
                 {"defaultAccess": "none", "acl": [{"asset": {"assetId": "51", "name": "vc01"}, "access": "r"}]},
                 {"acl": [{"label": {"labelId": "L-web", "name": "web"}, "access": "rw"}]},
                 {"acl": [{"benchmarkId": ARIA, "access": "r"}]},
                 {"acl": [{"asset": {"assetId": "52"}, "access": "r"}, {"asset": {"assetId": "51"}, "benchmarkId": ARIA, "access": "rw"}]}]
        for acl in reach:
            cks, plan, left, st, searched, rts, held, f = self.restricted(acl)
            self.assertEqual([p["kind"] for p in plan], ["import"] * 3, acl)
            self.assertEqual(set((p["collectionId"], p["assetId"], "moved_asset" in p) for p in plan), {("5", "51", False)}, "its reviews: in staging")
            why = "the user auditor1 would lose vc01 (a Restricted grant in Staging) - move it in STIG Manager yourself, or take it out of that grant"
            self.assertEqual(held, [{"asset": "vc01", "assetId": "51", "from": "Staging", "to": "VMware", "route": 1, "who": ["the user auditor1"],
                                     "files": ["vc01.ckl"], "why": why}], acl)
            self.assertEqual(cks[0]["route"], "route 1 (stigs VMW_*) -> VMware - not moved - a person decides: " + why)
            self.assertIn("/collections/5/grants/11/acl", f.calls)
        # an access list that does not reach it (none, another asset, label or STIG): moved, the grants read - no note
        miss = [{"defaultAccess": "none", "acl": []},
                {"acl": [{"asset": {"assetId": "51"}, "access": "none"}]},
                {"acl": [{"label": {"labelId": "L-web"}, "access": "none"}, {"benchmarkId": ARIA, "access": "none"}]},
                {"acl": [{"asset": {"assetId": "52"}, "access": "r"}]},
                {"acl": [{"label": {"labelId": "L-db"}, "access": "rw"}]},
                {"acl": [{"benchmarkId": "RHEL_9_STIG", "access": "r"}]},
                {"acl": [{"asset": {"assetId": "51"}, "benchmarkId": "RHEL_9_STIG", "access": "r"}]},
                {"acl": [{"asset": {"assetId": "51"}, "label": {"labelId": "L-db"}, "access": "r"}]}]
        for acl in miss:
            cks, plan, left, st, searched, rts, held, f = self.restricted(acl)
            self.assertEqual(held, [], acl)
            self.assertEqual([(p["kind"], p["hidden"]) for p in plan if p["kind"] == "move"], [("move", [])], acl)
            self.assertEqual(set(p["collectionId"] for p in plan), {"9"})
        # a group's grant, by its default access
        cks, plan, left, st, searched, rts, held, f = self.restricted({"defaultAccess": "r"}, acl={("5", "11"): {"defaultAccess": "r"},
                                                                                                   ("5", "12"): {"defaultAccess": "r"}})
        self.assertEqual(held[0]["who"], ["the user auditor1", "the group auditors"])
        # the client without Manage in staging: the grants not read - moved, said to be checked at the move
        cks, plan, left, st, searched, rts, held, f = self.restricted(reach[0], {"5": 2, "7": 2, "8": 2, "9": 3})
        self.assertEqual((held, [c for c in f.calls if "/grants" in c]), ([], []))
        self.assertEqual(plan[0]["hidden"], ["the Restricted grants in Staging are checked when it is moved (reading them needs Manage)"])
        # the grants could not be read: as not read
        cks, plan, left, st, searched, rts, held, f = self.restricted(reach[0], grants_error=True)
        self.assertEqual((held, plan[0]["kind"]), ([], "move"))
        self.assertIn("checked when it is moved", plan[0]["hidden"][0])

    def test_an_asset_in_another_collection_is_never_moved(self):
        d = routed()
        d["assets"]["7"] = [staged("71", "vc01")]                # in Windows; its route: VMware
        cks, plan, left, st, searched, rts, held, f = run_routes(d, VC, TO_VMWARE, collections=["Windows"])
        self.assertEqual([p["kind"] for p in plan], ["import"] * 3)
        self.assertEqual(set((p["collectionId"], p["assetId"]) for p in plan), {("7", "71")})
        self.assertEqual(cks[0]["route"], "route 1 (stigs VMW_*) -> VMware - its asset is in Windows: written there, never moved out of it")
        # a copy in staging too: still written in Windows, nothing moved
        d["assets"]["5"] = [staged()]
        cks, plan, left, st, searched, rts, held, f = run_routes(d, VC, TO_VMWARE, collections=["Windows"])
        self.assertEqual(set((p["kind"], p["collectionId"]) for p in plan), {("import", "7")})
        self.assertEqual([c["state"] for c in cks], ["planned", "in staging too"])
        # in its route's collection already: found there (a route's collection is searched), written there
        d = routed()
        d["assets"]["9"] = [staged("91", "vc01")]
        cks, plan, left, st, searched, rts, held, f = run_routes(d, VC, TO_VMWARE)
        self.assertEqual(set((p["kind"], p["collectionId"], p["assetId"]) for p in plan), {("import", "9", "91")})
        self.assertEqual(cks[0]["route"], "route 1 (stigs VMW_*) -> VMware")

    def test_a_move_to_another_revision_says_the_reviews_not_shown(self):
        d = routed()
        d["assets"]["5"] = [staged()]
        d["collection_stigs"]["9"] = [{"benchmarkId": ARIA, "revisionStr": "V1R2"}]
        d["reviews"]["51"] = [{"ruleId": "SV-260001r1_rule", "ruleIds": ["SV-260001r1_rule", "SV-260001r2_rule"], "result": "pass",
                               "status": {"label": "submitted"}, "ts": "t1"},        # its rule is in V1R2 too: shown there
                              {"ruleId": "SV-260002r1_rule", "result": "fail", "status": {"label": "submitted"}, "ts": "t2"},
                              {"ruleId": "SV-260009r1_rule", "result": "pass", "status": {"label": "accepted"}, "ts": "t3"}]
        plan = run_routes(d, VC, TO_VMWARE)[1]
        note = "2 review(s) of %s not shown at V1R2 there (kept: shown at V1R1)" % ARIA
        self.assertEqual(plan[0]["hidden"][0], note)
        self.assertIn("; " + note, plan[0]["line"])
        self.assertEqual(set(p["revision"] for p in plan[1:]), {"V1R2"}, "its reviews: at the destination's revision")
        self.assertIn("rules %s V1R2" % ARIA, run_routes(d, VC, TO_VMWARE)[-1].calls)
        # the same revision there: no such note
        d["collection_stigs"]["9"] = [{"benchmarkId": ARIA, "revisionStr": "V1R1"}]
        plan = run_routes(d, VC, TO_VMWARE)[1]
        self.assertFalse([h for h in plan[0]["hidden"] if "not shown" in h])

    def test_assign_stigs(self):
        d = routed()
        d["assets"]["9"] = [asset("91", "vc01", (ESXI, "V2R2"))]
        d["collection_stigs"]["9"] = [{"benchmarkId": ARIA, "revisionStr": "V1R2"}]
        cks, plan, left, st, searched, rts, held, f = run_routes(d, VC, [], collections=["VMware"], assign_stigs=True, auto="existing")
        a = plan[0]
        self.assertEqual(dict((k, a[k]) for k in ("kind", "assetId", "asset", "collectionId", "collection", "benchmark", "revision", "moved_asset",
                                                  "files", "reviews", "auto")),
                         {"kind": "assign", "assetId": "91", "asset": "vc01", "collectionId": "9", "collection": "VMware", "benchmark": ARIA,
                          "revision": "V1R2", "moved_asset": "", "files": ["vc01.ckl"], "reviews": 3, "auto": False})
        self.assertEqual(a["line"], "ASSIGN %s V1R2 to vc01 in VMware - then 3 review(s)" % ARIA)
        self.assertEqual(set((p["assign_stig"], p["assetId"], p["collectionId"], p["revision"], p["auto"]) for p in plan[1:]),
                         {(ARIA, "91", "9", "V1R2", False)}, "auto existing: a review of a STIG assigned first waits")
        self.assertEqual((cks[0]["state"], cks[0]["assigning"]), ("planned", ARIA))
        self.assertEqual(left, [])
        self.assertIn("[%s assigned first]" % ARIA, cf.stig_ckl_lines(cks)[0])
        # false: as before - left for a person
        cks, plan, left, st, searched, rts, held, f = run_routes(d, VC, [], collections=["VMware"])
        self.assertEqual((plan, cks[0]["state"]), ([], "no STIG on the asset"))
        self.assertTrue(cks[0]["why"].endswith("then run this again (stigman_ckl_import_assign_stigs: false)"), cks[0]["why"])
        self.assertEqual([x["kind"] for x in left], ["no_stig"] * 3)
        # moved and assigned: an asset in staging without the STIG, routed to VMware
        d = routed()
        d["assets"]["5"] = [staged(stigs=((ESXI, "V2R2"),))]
        plan = run_routes(d, VC, TO_VMWARE, assign_stigs=True)[1]
        self.assertEqual([p["kind"] for p in plan], ["move", "assign", "import", "import", "import"])
        self.assertEqual((plan[0]["stigs"], plan[0]["reviews"]), ([ESXI], 3))
        self.assertEqual((plan[1]["collectionId"], plan[1]["assetId"], plan[1]["moved_asset"]), ("9", "51", "51"))
        self.assertEqual(set((p["collectionId"], p["moved_asset"], p["readFrom"], p["assign_stig"]) for p in plan[2:]), {("9", "51", "5", ARIA)})

    def test_auto_all_needs_manage(self):
        """stigman_ckl_import_auto: all writes at once - 1.26.0 tester fix: every grant a create or a move needs (Manage in the
        staging collection and in every route's collection) is checked before anything is written; one too low fails the
        whole plan (before: that host alone was refused, the rest written - a half-done run)."""
        def fails(roles, routes=TO_VMWARE, **kw):
            d = routed()
            d["assets"]["5"] = [staged()]
            give(d, roles)
            f = Fake(d)
            with self.assertRaises(ci.Fail) as e:
                ci.plan_all(params(routes=routes, collection="Staging", auto="all", **kw), f, copy.deepcopy(VC))
            self.assertFalse([c for c in f.calls if c.startswith("/assets") or c.startswith("reviews") or c.startswith("rules")],
                             "no host looked for: %s" % f.calls)
            return str(e.exception)
        said = ("stigman_ckl_import_auto: all creates and moves assets at once, so the client needs Manage in the staging collection and every "
                "route's collection - it has %s: nothing written. Give it Manage there, or stigman_ckl_import_auto: existing (the approval then "
                "creates and moves)")
        self.assertEqual(fails({"5": 3, "7": 2, "8": 2, "9": 2}), said % "Full in VMware")
        self.assertEqual(fails({"5": 2, "7": 2, "8": 2, "9": 3}), said % "Full in Staging")
        two = TO_VMWARE + [{"collection": "Linux", "stigs": "RHEL_*"}, {"collection": "vmware", "names": "esx*"}]
        self.assertEqual(fails({"5": 2, "7": 2, "8": 2, "9": 2}, two), said % "Full in Staging, Full in VMware, Full in Linux")
        # a route's collection needs it even when no host goes there this time (Linux: no RHEL checklist)
        self.assertEqual(fails({"5": 3, "7": 2, "8": 2, "9": 3}, two), said % "Full in Linux")
        # the staging collection: for a new asset (stigman_ckl_import_create_assets) or a move out of it (route_move staging)
        self.assertEqual(fails({"5": 2, "7": 2, "8": 2, "9": 3}, create_assets=False), said % "Full in Staging")
        self.assertEqual(fails({"5": 2, "7": 2, "8": 2, "9": 3}, route_move="none"), said % "Full in Staging")
        # no routes: nothing checked before the hosts are looked for - a run with no new host writes its reviews at once as
        # before (Full in staging is enough); a new host to create there stops it all, before anything is written
        planned = ("stigman_ckl_import_auto: all creates, moves and assigns at once, and the client has %s - that needs Manage: "
                   "nothing written. Give it Manage there, or stigman_ckl_import_auto: existing (the approval then does it)")
        d = routed()
        d["assets"]["5"] = [staged()]
        give(d, {"5": 2, "7": 2, "8": 2, "9": 3})
        plan = run_routes(d, VC, [], auto="all")[1]
        self.assertEqual(set((p["kind"], p["collectionId"], p["auto"]) for p in plan), {("import", "5", True)})
        d["assets"]["5"] = []
        with self.assertRaises(ci.Fail) as e:
            run_routes(d, VC, [], auto="all")
        self.assertEqual(str(e.exception), planned % "Full in Staging")
        # neither a create nor a move out of staging: Full there is enough - its reviews written there at once
        d = routed()
        d["assets"]["5"] = [staged()]
        give(d, {"5": 2, "7": 2, "8": 2, "9": 3})
        cks, plan, left, st, searched, rts, held, f = run_routes(d, VC, TO_VMWARE, auto="all", create_assets=False, route_move="none")
        self.assertEqual(set((p["kind"], p["collectionId"], p["auto"]) for p in plan), {("import", "5", True)})
        # Manage (or Owner) everywhere it creates or moves: planned, written at once
        give(d, {"5": 4, "7": 2, "8": 2, "9": 3})
        plan = run_routes(d, VC, TO_VMWARE, auto="all")[1]
        self.assertEqual([(p["kind"], p["auto"]) for p in plan], [("move", True)] + [("import", True)] * 3)
        d["assets"]["5"] = []
        plan = run_routes(d, VC, TO_VMWARE, auto="all")[1]
        self.assertEqual((plan[0]["kind"], plan[0]["collection"], plan[0]["auto"]), ("create", "VMware", True))
        # auto existing (the approval creates and moves) or grants not known (GET /user without them): not checked here
        give(d, {"5": 2, "7": 2, "8": 2, "9": 2})
        plan = run_routes(d, VC, TO_VMWARE, auto="existing")[1]
        self.assertEqual((plan[0]["kind"], plan[0]["auto"]), ("create", False))
        d["user"] = {"userId": "1"}
        plan = run_routes(d, VC, TO_VMWARE, auto="all")[1]
        self.assertEqual((plan[0]["kind"], plan[0]["collection"]), ("create", "VMware"))
        # an assignment where it has Full: it all stops before anything is written, said
        d = routed()
        d["assets"]["9"] = [asset("91", "vc01", (ESXI, "V2R2"))]
        give(d, {"5": 3, "7": 2, "8": 2, "9": 2})
        with self.assertRaises(ci.Fail) as e:
            run_routes(d, VC, [], collections=["VMware"], assign_stigs=True, auto="all")
        self.assertEqual(str(e.exception), planned % "Full in VMware")
        self.assertEqual(cf.STATE_CHIP["no grant"], "critical")

    def test_with_routes_every_collection_read_is_searched(self):
        """1.26.0 tester fix: with routes, every collection the client reads is searched, whatever stigman_ckl_import_collections
        says - a host whose asset is in a collection neither routed nor staging is written there: never created again in its
        route's collection or in staging, never moved."""
        for setting in ([], ["Linux"]):
            d = routed()
            d["assets"]["7"] = [staged("71", "vc01")]            # in Windows: no route goes there
            give(d, {"5": 3, "7": 2, "8": 3, "9": 3})
            for auto in ("none", "all"):
                cks, plan, left, st, searched, rts, held, f = run_routes(d, VC, TO_VMWARE, collections=setting, auto=auto)
                self.assertEqual(searched, ["Linux", "VMware", "Windows", "Staging"], setting)
                self.assertEqual(set((p["kind"], p["collectionId"], p["assetId"], p["auto"]) for p in plan), {("import", "7", "71", auto == "all")},
                                 (setting, auto))
                self.assertEqual((cks[0]["collection"], cks[0]["new"], held, left), ("Windows", False, [], []))
                self.assertEqual(cks[0]["route"], "route 1 (stigs VMW_*) -> VMware - its asset is in Windows: written there, never moved out of it")
            # no rule fits it: written where it is
            cks, plan = run_routes(d, VC, [{"collection": "VMware", "names": "esx*"}], collections=setting)[:2]
            self.assertEqual(set((p["kind"], p["collectionId"]) for p in plan), {("import", "7")})
            self.assertEqual(cks[0]["route"], "no route fits - written where it is: Windows")
        # a copy in staging too: written in Windows, nothing moved out of staging
        d["assets"]["5"] = [staged()]
        cks, plan = run_routes(d, VC, TO_VMWARE)[:2]
        self.assertEqual(set((p["kind"], p["collectionId"]) for p in plan), {("import", "7")})
        self.assertEqual([c["state"] for c in cks], ["planned", "in staging too"])
        # no routes: as before (69bbcff) - only the collections named are searched: a new asset in staging
        d["assets"]["5"] = []
        cks, plan, left, st, searched, rts, held, f = run_routes(d, VC, [])
        self.assertEqual((searched, plan[0]["kind"], plan[0]["collection"], cks[0]["route"]), (["Staging"], "create", "Staging", ""))
        self.assertNotIn("/assets", [c for c in f.calls if "7" in c])

    def test_with_routes_a_restricted_grant_anywhere(self):
        """With routes every collection read is searched; one where the client has only Restricted (it may not see every
        asset there) fails the plan - and says what to do: stigman_ckl_import_collections is not read with routes, so leaving the
        collection out of it is no help."""
        d = routed()
        give(d, {"5": 3, "7": 1, "8": 2, "9": 3})
        with self.assertRaises(ci.Fail) as e:
            run_routes(d, VC, TO_VMWARE, collections=["VMware"])
        self.assertIn("only Restricted in Windows", str(e.exception))
        self.assertIn("Full", str(e.exception))
        self.assertNotIn("leave it out of stigman_ckl_import_collections", str(e.exception),
                         "with routes that setting is not read - the advice cannot help")

    def test_no_route_fits_says_where(self):
        """1.26.0 tester fix: a host no rule fits says where it goes - created in staging, stays in staging, or written where it
        is - in its checklist's line and the report's Why cell ("Route: ...")."""
        miss = [{"collection": "VMware", "names": "esx*"}]
        d = routed()
        cks, plan = run_routes(d, VC, miss)[:2]
        self.assertEqual((cks[0]["route"], plan[0]["kind"], plan[0]["collection"]), ("no route fits - created in Staging", "create", "Staging"))
        self.assertTrue(cf.stig_ckl_lines(cks)[0].endswith(" [no route fits - created in Staging]"), cf.stig_ckl_lines(cks)[0])
        d["assets"]["5"] = [staged()]
        cks, plan, left, st, searched, rts, held, f = run_routes(d, VC, miss)
        self.assertEqual(cks[0]["route"], "no route fits - stays in Staging")
        self.assertEqual(set((p["kind"], p["collectionId"]) for p in plan), {("import", "5")}, "not moved")
        res = {"files": [{"name": "vc01.ckl", "sha256": "s", "checklists": 1}], "checklists": cks, "plan": plan, "left": left, "collection": st,
               "collections": searched, "routes": rts, "held": held, "source": "/ckl", "error": ""}
        r = cf.stig_ckl_report(res, plan, [], True, 200)
        each = [x for x in r["sections"] if x["title"] == "Each checklist"][0]
        self.assertTrue(each["rows"][0][10].endswith("Route: no route fits - stays in Staging"), each["rows"][0][10])
        d["assets"]["8"] = [staged("81", "vc01")]
        cks = run_routes(d, VC, miss)[0]
        self.assertEqual(cks[0]["route"], "no route fits - written where it is: Linux")
        # no routes: no route text
        self.assertEqual(run_routes(d, VC, [])[0][0]["route"], "")

    def test_a_rule_value_of_the_wrong_type(self):
        """1.26.0 tester fix: stigs, names, roles and tech_areas are a list of names or patterns or text - a number, a mapping or a
        list holding one fails the plan, naming the rule (before: str() of it, a pattern that never fits)."""
        for rule, said in ((dict(stigs=5), "stigs is a list of names or patterns (or text with commas), not 5 - nothing planned"),
                           (dict(stigs={"a": 1}), "stigs is a list of names or patterns (or text with commas), not {'a': 1} - nothing planned"),
                           (dict(names=[5]), "names is a list of names or patterns (or text with commas), not [5] - nothing planned"),
                           (dict(names=["vc01", None]), "names is a list of names or patterns (or text with commas), not ['vc01', None]"),
                           (dict(roles=[["member server"]]), "roles is a list of names or patterns (or text with commas), not [['member server']]"),
                           (dict(tech_areas=True), "tech_areas is a list of names or patterns (or text with commas), not True")):
            f = Fake(routed())
            with self.assertRaises(ci.Fail) as e:
                ci.plan_all(params(routes=[{"collection": "Linux"}, dict(rule, collection="VMware")], collection="Staging"), f, copy.deepcopy(VC))
            self.assertTrue(str(e.exception).startswith("stigman_ckl_import_routes, rule 2: " + said), str(e.exception))
            self.assertTrue(str(e.exception).endswith(" - nothing planned"), str(e.exception))
            self.assertFalse([c for c in f.calls if c.startswith("/assets")], "nothing looked for")
        # what is one: a list of text, text with commas or lines, a tuple; the key not given
        rts = ci.read_routes([{"collection": "VMware", "stigs": ("VMW_*",), "names": "esx01,\nvc01"}], routed()["collections"])
        self.assertEqual((rts[0]["stigs"], rts[0]["names"], rts[0]["roles"], rts[0]["tech_areas"]), (["VMW_*"], ["esx01", "vc01"], [], []))

    def test_a_rule_key_with_no_names(self):
        """1.26.0 round c: a key given with no names (null, "", [], [" "]) fit every host - refused before anything is read
        or written; a rule with only its collection is the one for every host (still allowed)."""
        for key in ("names", "stigs", "roles", "tech_areas"):
            for empty in (None, "", [], [" "], " , "):
                f = Fake(routed())
                with self.assertRaises(ci.Fail) as e:
                    ci.plan_all(params(routes=[{"collection": "Linux", key: empty}, {"collection": "Windows", "stigs": ["*Windows_Server*"]}],
                                       collection="Staging", auto="all"), f, copy.deepcopy(VC))
                self.assertEqual(str(e.exception), "stigman_ckl_import_routes, rule 1: %s has no names (%r) - give it names or patterns, "
                                 "or leave the key out (a rule with only a collection fits every host); nothing planned" % (key, empty))
                self.assertFalse([c for c in f.calls if c.startswith("/assets")], "nothing looked for")
        self.assertEqual(ci.read_routes([{"collection": "Linux"}], routed()["collections"])[0]["text"], "every host")

    def test_auto_all_asks_manage_only_where_it_can_act(self):
        """1.26.0 round c: routes, auto all, create_assets false and route_move none (no assign_stigs): nothing can be created,
        moved or assigned - Full in every collection is enough, the reviews are written at once."""
        d = routed()
        d["assets"]["5"] = [staged()]
        give(d, {"5": 2, "7": 2, "8": 2, "9": 2})
        cks, plan, left, st, searched, rts, held, f = run_routes(d, VC, TO_VMWARE, auto="all", create_assets=False, route_move="none")
        self.assertEqual(set((p["kind"], p["collectionId"], p["auto"]) for p in plan), {("import", "5", True)})
        # assign_stigs can assign there: Manage is asked for again
        with self.assertRaises(ci.Fail) as e:
            run_routes(d, VC, TO_VMWARE, auto="all", create_assets=False, route_move="none", assign_stigs=True)
        self.assertIn("it has Full in Staging, Full in VMware: nothing written", str(e.exception))

    def test_names_fit_the_short_name(self):
        """1.26.0 tester fix: names is compared with the host name, its FQDN and each one's short name (before the first dot),
        any case: names vc11 fits HOST_NAME vc11.example.mil."""
        def ckl(hn, fqdn):
            raw = fixture().replace(b"<HOST_NAME>vrops01</HOST_NAME>", b"<HOST_NAME>" + hn.encode() + b"</HOST_NAME>")
            return [ckfile(hn + ".ckl", raw.replace(b"<HOST_FQDN>vrops01.example.mil</HOST_FQDN>", b"<HOST_FQDN>" + fqdn.encode() + b"</HOST_FQDN>"))]
        long_name = ckl("vc11.example.mil", "vc11.example.mil")
        for pat in ("vc11", "VC11", "vc1?", "vc11.example.mil", "*.example.mil"):
            self.assertEqual(self.where([{"collection": "VMware", "names": pat}], long_name), ["VMware"], pat)
        for pat in ("vc1", "example", "vc11.example", "vc11.yoursite.mil"):
            self.assertEqual(self.where([{"collection": "VMware", "names": pat}], long_name), ["Staging"], pat)
        # the FQDN's short name: HOST_NAME vc12-mgmt, FQDN vc12.example.mil
        other = ckl("vc12-mgmt", "vc12.example.mil")
        self.assertEqual(self.where([{"collection": "VMware", "names": "VC12"}], other), ["VMware"])
        self.assertEqual(self.where([{"collection": "VMware", "names": "vc12-mgmt"}], other), ["VMware"])
        self.assertEqual(self.where([{"collection": "VMware", "names": "vc12-mgmt.example.mil"}], other), ["Staging"])
        self.assertEqual(ci.route_of(ci.read_routes([{"collection": "VMware", "names": "vc11"}], routed()["collections"]), "vc11.example.mil",
                                     [(ARIA, "", {"host": "vc11.example.mil", "fqdn": ""})])[0]["n"], 1)

    def test_no_routes_plan_as_before(self):
        """No routes: the plan is the one 69bbcff (1.24.0) made - checklists, plan items, what is left, the collections; the
        SHA-256 of them as 69bbcff gave them. A create item says route 0 and a checklist route '' and move_refused '' (taken out first)."""
        d = multi()
        d["assets"]["7"] = [asset("71", "vrops01", (ARIA, "V1R1"))]
        d["assets"]["8"] = [asset("81", "vrops01.example.mil", (ARIA, "V1R2"))]
        d["assets"]["9"] = [asset("91", "VROPS01", ("RHEL_9_STIG", "V2R9"))]
        d["assets"]["5"] = [asset("51", "vrops01", (ARIA, "V1R1"))]
        d["reviews"]["71"] = [{"ruleId": "SV-260001r1_rule", "result": "pass", "status": {"label": "submitted"}, "ts": "t1"},
                              {"ruleId": "SV-260002r1_rule", "result": "pass", "status": {"label": "accepted"}, "ts": "t2"}]
        files = [{"name": "vrops01.ckl", "data": fixture(), "modified": "1"}, {"name": "vrops03.ckl", "data": host("vrops03"), "modified": "1"}]
        out = ci.plan_all(params(collection="Watcher", collections=["*"]), Fake(d), copy.deepcopy(files))
        cks, plan, left, st, searched = copy.deepcopy(out[:5])
        self.assertEqual([c.pop("route", "") for c in cks], [""] * len(cks))
        self.assertEqual([c.pop("move_refused", "") for c in cks], [""] * len(cks))
        self.assertEqual([p.pop("route", 0) for p in plan if p["kind"] == "create"], [0])
        self.assertEqual([(p["kind"], p["collection"], p.get("ruleId", "")) for p in plan][:2],
                         [("create", "Watcher", ""), ("import", "Watcher", "SV-260001r1_rule")])
        self.assertEqual(hashlib.sha256(json.dumps([cks, plan, left, st, searched], sort_keys=True).encode()).hexdigest(), BEFORE_ROUTES)
        self.assertEqual(out[5:], ([], []), "no routes read, no move held")


BEFORE_ROUTES = "96cca559c0a41064946db892e111922e60944905a06a72313e0cac8c6fcf8925"     # 69bbcff's plan_all, the same data


class RouteReport(unittest.TestCase):
    """The check's report with routes (stig_ckl_report), the files done with (stig_ckl_settled), the attachments check."""

    def ci(self, **kw):
        """vc01 moved from Staging to VMware, esx01 held (a Restricted grant has it), rhel01 in Linux without RHEL 9 (assigned);
        a third route to Windows that vc01 fits too (1.26.0 tester fix: a route to a collection the client cannot read now
        fails the plan - test_a_route_to_a_collection_not_read)."""
        d = routed()
        d["assets"]["5"] = [staged(), staged("52", "esx01", labels=("db",))]
        d["assets"]["8"] = [asset("81", "rhel01", (ESXI, "V2R2"))]
        give(d, {"5": 3, "7": 2, "8": 3, "9": 3})
        d["grants"] = {"5": [{"grantId": "11", "roleId": 1, "user": {"username": "auditor1"}}]}
        d["acl"] = {("5", "11"): {"acl": [{"asset": {"assetId": "52"}, "access": "r"}]}}
        d.update(kw)
        files = VC + [ckfile("esx01.ckl", aria("esx01")), ckfile("rhel01.cklb", rhel("rhel01"))]
        routes = [{"collection": "VMware", "stigs": "VMW_*"}, {"collection": "Linux", "stigs": "RHEL_*"}, {"collection": "Windows", "names": "vc*"}]
        cks, plan, left, st, searched, rts, held, f = run_routes(d, files, routes, assign_stigs=True)
        return {"files": [{"name": x["name"], "sha256": "s", "checklists": 1} for x in files], "checklists": cks, "plan": plan, "left": left,
                "collection": st, "collections": searched, "routes": rts, "held": held, "source": "/ckl", "error": ""}

    def test_report(self):
        res = self.ci()
        plan = res["plan"]
        self.assertEqual([p["kind"] for p in plan][:2], ["move", "assign"])
        r = cf.stig_ckl_report(res, plan, [], True, 200)
        self.assertEqual(r["title"], "Checklist import: 9 review(s) to write, 1 to move, 1 STIG(s) to assign, 1 move(s) for a person")
        self.assertEqual(r["status"], "warning")
        s = r["sections"][0]
        self.assertEqual(s["title"], "Approve writes these reviews to STIG Manager and moves 1 asset, assigns 1 STIG")
        self.assertEqual(s["rows"][0][:3], ["MOVE", "VMware / vc01", ARIA])
        self.assertTrue(s["rows"][0][5].startswith("from Staging (route 1), then its 3 review(s)"), s["rows"][0][5])
        self.assertEqual(s["rows"][1][:3], ["ASSIGN", "Linux / rhel01", "RHEL_9_STIG V2R9"])
        self.assertEqual(s["rows"][1][5], "the STIG assigned to it, then its 3 review(s)")
        self.assertIn("VMware / vc01 (moved first)", [x[1] for x in s["rows"]])
        self.assertIn(" MOVE: an asset only in the staging collection", s["text"])
        self.assertIn(" ASSIGN: the STIG its checklist matched", s["text"])
        t = dict((x["title"], x) for x in r["sections"])
        p = t["Not moved - a person decides (1)"]
        self.assertEqual(p["status"], "warning")
        self.assertEqual(p["rows"][0][:4], ["esx01", "Staging", "VMware (route 1)", "the user auditor1"])
        rt = t["Routes (3)"]
        self.assertEqual(rt["status"], "ok", "every route's collection read (one not read fails the plan)")
        self.assertEqual(rt["rows"], [["1", "VMware", "stigs VMW_*", "-"], ["2", "Linux", "stigs RHEL_*", "-"], ["3", "Windows", "names vc*", "-"]])
        each = t["Each checklist"]
        vc = [x for x in each["rows"] if x[0] == "vc01.ckl"][0]
        self.assertIn("Route: route 1 (stigs VMW_*) -> VMware; it fits route 3 (Windows, by %s) too" % ARIA, vc[10])
        tiles = dict((x["label"], x) for x in r["summary"])
        self.assertEqual((tiles["To move"]["value"], tiles["To move"]["note"]), (1, "1 for a person"))
        # nothing held, every route read: the tile says where from, the routes are ok
        res = self.ci(acl={})
        r = cf.stig_ckl_report(res, res["plan"], [], True, 200)
        self.assertEqual(r["title"], "Checklist import: 9 review(s) to write, 2 to move, 1 STIG(s) to assign")
        tiles = dict((x["label"], x) for x in r["summary"])
        self.assertEqual((tiles["To move"]["value"], tiles["To move"]["note"]), (2, "out of Staging"))
        self.assertEqual([x["status"] for x in r["sections"] if x["title"].startswith("Routes")], ["ok"])
        self.assertFalse([x for x in r["sections"] if x["title"].startswith("Not moved")])
        # no routes, nothing moved: no tile, no section
        r = cf.stig_ckl_report(dict(res, routes=[], held=[]), [p for p in res["plan"] if p["kind"] != "move"], [], True, 200)
        self.assertNotIn("To move", [x["label"] for x in r["summary"]])

    def test_report_fit_document(self):
        """The check's report with every routes part and long values - long collection names, long FQDNs, long route texts:
        MOVE, ASSIGN and NEW ASSET rows, Not moved - a person decides, Routes, Each checklist with its route; its document is
        tests/fixtures/report_fit/stig_ckl_report_routes.json (python3 tests/report_fit_docs.py --named), that
        tests/test_report_fit.py renders."""
        vmw, lin = "VMware_vSphere_8_Management_Clusters_East_Region", "Linux Servers Production East Region Datacenter"

        def ck(name, raw=None):
            raw = raw or host("vrops01")
            raw = raw.replace(b"<HOST_FQDN>vrops01.example.mil</HOST_FQDN>", b"<HOST_FQDN>vrops01.vsphere-management.east-region.example.mil</HOST_FQDN>")
            return ckfile(name + ".ckl", raw.replace(b"vrops01", name.encode()))

        def rh(name):
            raw = rhel(name, "Member Server", "UNIX OS").replace(b'"fqdn": "%s.example.mil"' % name.encode(),
                                                                 b'"fqdn": "%s.linux-servers.east-region.example.mil"' % name.encode())
            return ckfile(name + ".cklb", raw)
        d = routed()
        d["collections"] = [{"collectionId": "5", "name": "Staging"}, {"collectionId": "7", "name": "Windows"}, {"collectionId": "8", "name": lin},
                            {"collectionId": "9", "name": vmw}, {"collectionId": "10", "name": "Ops"}]
        d["assets"]["10"], d["collection_stigs"]["10"] = [staged("101", "web01")], []
        d["collection_stigs"]["9"] = [{"benchmarkId": ARIA, "revisionStr": "V1R2"}]
        d["assets"]["5"] = [staged(n, h, labels=lb) for n, h, lb in (("51", "vc01", ("vsphere-management-east",)), ("52", "vc02", ("web", "dmz")),
                                                                     ("53", "vc03", ("vsphere-management-east",)))]
        d["reviews"]["51"] = [{"ruleId": "SV-26000%dr1_rule" % n, "result": "pass", "status": {"label": "submitted"}, "ts": "t"} for n in (1, 2, 9)]
        d["assets"]["8"] = [asset("81", "rhel01", (ESXI, "V2R2"))]
        give(d, {"5": 3, "7": 2, "8": 3, "9": 3, "10": 2})
        d["grants"] = {"5": [{"grantId": "11", "roleId": 1, "user": {"username": "auditor-vsphere-management-east"}},
                             {"grantId": "12", "roleId": 1, "userGroup": {"name": "vsphere-auditors-east-region"}}]}
        d["acl"] = {("5", "11"): {"acl": [{"asset": {"assetId": "52"}, "access": "r"}]}, ("5", "12"): {"acl": [{"label": {"labelId": "L-web"}, "access": "r"}]}}
        files = [ck(h) for h in ("vc01", "vc02", "vc03", "esx01", "web01", "app01")] + [rh("rhel01"), rh("rhel02")]
        routes = [{"collection": vmw, "names": "vc*, esx*", "stigs": ["VMW_Aria_Operations_*", "VMW_vSphere_8-0_*"]},
                  {"collection": lin, "stigs": "RHEL_*", "roles": "Member Server, Domain Controller", "tech_areas": ["UNIX OS", "Database"]},
                  {"collection": "Windows", "roles": "Domain Controller", "tech_areas": "Windows OS"},
                  {"collection": "Windows", "stigs": "VMW_Aria_*", "names": "vc0*.vsphere-management.east-region.example.mil"}]
        cks, plan, left, st, searched, rts, held, f = run_routes(d, files, routes, assign_stigs=True)
        res = {"files": [{"name": x["name"], "sha256": "s", "checklists": 1} for x in files], "checklists": cks, "plan": plan, "left": left,
               "collection": st, "collections": searched, "routes": rts, "held": held, "source": "\\\\fileserver.example.mil\\stig\\ckl", "error": ""}
        r = cf.stig_ckl_report(res, plan, [], True, 200)
        t = dict((x["title"].split(" (")[0], x) for x in r["sections"])
        approve = r["sections"][0]
        self.assertEqual(sorted(set(x[0] for x in approve["rows"] if x[0].isupper())), ["ASSIGN", "MOVE", "NEW ASSET"])
        self.assertEqual(len([x for x in approve["rows"] if x[0] == "MOVE"]), 2)
        self.assertEqual([x[:2] for x in t["Not moved - a person decides"]["rows"]], [["vc02", "Staging"]])
        self.assertIn("the group vsphere-auditors-east-region", t["Not moved - a person decides"]["rows"][0][3])
        self.assertEqual(len(t["Routes"]["rows"]), 4)
        why = dict((x[0], x[10]) for x in t["Each checklist"]["rows"])
        self.assertIn("Route: no route fits - written where it is: Ops", why["web01.ckl"])
        self.assertIn("Route: no route fits - created in Staging", why["app01.ckl"])
        self.assertIn("it fits route 4 (Windows, by %s) too - the first rule in the list wins" % ARIA, why["vc01.ckl"])

    def test_settled(self):
        base = {"files": [{"name": n, "sha256": n, "checklists": 1} for n in ("h.ckl", "m.ckl")], "checklists": [], "left": [],
                "held": [{"asset": "esx01", "to": "VMware", "why": "the user auditor1 would lose esx01", "files": ["h.ckl"]}]}
        got = dict((x["name"], x) for x in cf.stig_ckl_settled(base))
        self.assertEqual((got["h.ckl"]["move"], got["m.ckl"]["move"]), (False, True))
        self.assertEqual(got["h.ckl"]["why"], "the move of esx01 to VMware waits for a person: the user auditor1 would lose esx01")

        def done(kind, state, review="written"):
            auto = [{"kind": kind, "files": ["m.ckl"], "line": kind.upper()}, {"kind": "import", "file": "m.ckl", "files": ["m.ckl"]}]
            su = {"items": [{"n": 0, "kind": kind, "state": state, "line": kind.upper()}, {"n": 1, "state": review}], "error": ""}
            return dict((x["name"], x["move"]) for x in cf.stig_ckl_settled(dict(base, held=[]), [], auto, su))["m.ckl"]
        for state in ("moved", "there from an earlier run"):
            self.assertTrue(done("move", state), state)
        for state in ("held", "changed since", "there since the check", "no grant", "error", "would move"):
            self.assertFalse(done("move", state, "asset not moved"), state)
        for state in ("assigned", "already so"):
            self.assertTrue(done("assign", state), state)
        for state in ("would assign", "no grant", "changed since", "asset not moved", "error"):
            self.assertFalse(done("assign", state, "STIG not assigned"), state)
        self.assertFalse(done("move", "moved", "changed since"), "moved, a review not written: the file stays")

    def test_attachments_round_trip(self):
        """The attachments check gets the reviews only, a moving asset's read where it is now (staging); after it the moves
        and assignments come first, the kept reviews go where they were planned again, a review of a STIG being assigned
        (not checked: nothing to read there yet) after them; the check not done: nothing."""
        mv = {"kind": "move", "assetId": "51", "fromId": "5", "collectionId": "9", "line": "MOVE"}
        asg = {"kind": "assign", "assetId": "81", "collectionId": "8", "benchmark": "RHEL_9_STIG", "line": "ASSIGN"}
        moving = [{"kind": "import", "collectionId": "9", "assetId": "51", "moved_asset": "51", "readFrom": "5", "ruleId": "SV-26000%dr1_rule" % n}
                  for n in (1, 2)]
        assigned = {"kind": "import", "collectionId": "8", "assetId": "81", "assign_stig": "RHEL_9_STIG", "ruleId": "SV-257001r1_rule"}
        plain = {"kind": "import", "collectionId": "7", "assetId": "71", "ruleId": "SV-260005r1_rule"}
        new = [{"kind": "create", "collectionId": "9", "asset": "vc02"}, {"kind": "import", "collectionId": "9", "new_asset": "vc02", "ruleId": "SV-1"}]
        plan = [mv, asg] + new + moving + [assigned, plain]
        keep = copy.deepcopy(plan)
        fa = cf.stig_ckl_for_attachments(plan)
        self.assertEqual(plan, keep, "the plan itself is not changed")
        self.assertEqual([(p.get("kind"), p["collectionId"], p.get("moveTo")) for p in fa],
                         [("create", "9", None), ("import", "9", None), ("import", "5", "9"), ("import", "5", "9"), ("import", "7", None)])
        # the check leaves out the second moving review (it has attachments): the rest back as planned
        kept = [p for p in fa if p.get("ruleId") != "SV-260002r1_rule"]
        back = cf.stig_ckl_after_attachments(kept, plan)
        self.assertEqual(back, [mv, asg] + new + [moving[0], plain, assigned])
        self.assertFalse([p for p in back if "moveTo" in p])
        self.assertEqual(cf.stig_ckl_after_attachments(fa, plan, "HTTP 500 from /collections/5/reviews"), [])
        self.assertEqual(cf.stig_ckl_after_attachments(cf.stig_ckl_for_attachments(new), new), new, "no moves: as before")
        fm = cf.FilterModule().filters()
        self.assertIs(fm["stig_ckl_for_attachments"], cf.stig_ckl_for_attachments)
        self.assertIs(fm["stig_ckl_after_attachments"], cf.stig_ckl_after_attachments)


if __name__ == "__main__":
    unittest.main()
