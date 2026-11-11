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
        raise AssertionError("not asked for: " + path)

    def rules(self, bench, rev):
        self.calls.append("rules %s %s" % (bench, rev))
        if (bench, rev) not in self.d["rules"]:
            raise RuntimeError("HTTP 404 from /stigs/%s/revisions/%s/rules" % (bench, rev))
        return self.d["rules"][(bench, rev)]

    def reviews(self, c, a, bench):
        return self.d["reviews"].get(a, [])


def params(**kw):
    p = {"collection": "VMware", "collections": [], "auto": "none", "create_assets": True, "benchmarks": {}, "min_match": 0.8, "candidates": 25, "search_all": False,
         "other_revision": False, "overwrite_accepted": False, "status": "submitted", "detail_max": 8000, "day": "2026-11-05"}
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
    return out + (f,)


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


if __name__ == "__main__":
    unittest.main()
