"""Prevalence and suggested baseline exports for the Windows local groups report."""
import json
import sys
from pathlib import Path

import unittest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "filter"))
import win_local_groups_filters as w

SERVER = r"CORP\Server Admins"
DOMAIN = r"CORP\Domain Admins"
GENERATED = "2026-11-24T12:00:00Z"


def host(index, admins=(), backup=()):
    def group(name, sid, members):
        return {"name": name, "sid": sid, "found": True,
                "members": [{"name": member, "source": "Domain"} for member in members]}
    return {"host": "SRV-APP%02d" % index, "result": {"groups": [
        group("Backup Operators", "S-1-5-32-551", backup), group("Administrators", w.ADMINS, admins)]}}


def sections(model, web=True):
    return {s["title"]: s for s in w.win_local_groups_sections(model, web)}


def _group_order_spelling_ties_and_nonresponders(web):
    hosts = [host(i, [SERVER.lower() if i == 1 else SERVER, DOMAIN], [SERVER]) for i in range(1, 6)]
    hosts[0]["result"]["groups"][1]["members"] *= 4
    hosts[0]["result"]["groups"].pop(0)
    hosts += [{"host": "SRV-APP06", "unreached": "timeout"}, {"host": "SRV-APP07"}]
    report = sections(w.win_local_groups_model(hosts), web)
    assert report["Most common members"]["rows"] == [
        ["Administrators", DOMAIN, "in 5 of 5 servers (100 %)"],
        ["Administrators", SERVER, "in 5 of 5 servers (100 %)"],
        ["Backup Operators", SERVER, "in 4 of 5 servers (80 %)"]]
    assert report["At a glance"]["lines"] == [
        "Suggested baseline: 2 members in 1 group (>= 90 %)",
        "Prevalence is out of 5 that answered; 2 did not answer: SRV-APP06, SRV-APP07"]
    assert "Unusual members" not in report


def test_inclusive_thresholds_and_export_contract():
    hosts = [host(i, [SERVER if i <= 54 else DOMAIN], [DOMAIN] if i <= 56 else []) for i in range(1, 61)]
    model = w.win_local_groups_model(hosts)
    assert sections(model)["Unusual members"]["rows"] == [
        ["Administrators", DOMAIN, "in 6 of 60 servers (10 %)", ", ".join(h["host"] for h in hosts[-6:])]]
    baseline = json.loads(json.dumps(w.win_local_groups_baseline(model, GENERATED)))
    assert baseline == {"generated": GENERATED, "servers_answered": 60, "threshold_pct": 90, "groups": {
        "Administrators": [{"member": SERVER, "count": 54, "pct": 90}],
        "Backup Operators": [{"member": DOMAIN, "count": 56, "pct": 93}]}}
    sheet = next(s for s in w.win_local_groups_sheets(model) if s["name"] == "Suggested baseline")
    assert [r[:6] for r in sheet["rows"]] == [
        ["Administrators", SERVER, 54, 60, 90, 90], ["Backup Operators", DOMAIN, 56, 60, 93, 90]]
    text = w.win_local_groups_baseline_snippet(model)
    flow, block = list(yaml.safe_load_all(text))
    assert len([line for line in text.split("---")[0].splitlines() if not line.startswith("#")]) == 1
    assert flow == block == {"win_local_groups_approved": {"groups": {"Administrators": [SERVER]}}}
    assert [yaml.safe_load(cell) for cell in sheet["rows"][0][6:]] == [flow, block]
    files = w.win_local_groups_checks(model, flow["win_local_groups_approved"])
    assert any(f["name"].endswith(".xml") and SERVER in f["content"] for f in files)


def _half_up_and_unanimity(count, total, pct):
    assert w._display_pct(count, total) == pct


def test_thresholds_use_exact_share():
    model = w.win_local_groups_model([host(i, [SERVER] if i <= 43 else [DOMAIN]) for i in range(1, 49)])
    rows = model["prevalence"][0]["members"]
    assert [(r["pct"], r["baseline"], r["rare"]) for r in rows] == [(90, False, False), (10, False, False)]


def test_overrides_and_no_answers():
    model = w.win_local_groups_model([host(1, [SERVER]), host(2)], baseline_pct="50", rare_pct=50)
    member, = model["prevalence"][0]["members"]
    assert member["baseline"] and member["rare"]
    empty = w.win_local_groups_model([{"host": "SRV-APP01"}])
    assert set(sections(empty)) == {"At a glance", "Not read"}
    assert w.win_local_groups_baseline(empty, GENERATED)["groups"] == {}
    assert list(yaml.safe_load_all(w.win_local_groups_baseline_snippet(empty)))[0] == {
        "win_local_groups_approved": {"groups": {"Administrators": []}}}


class Prevalence(unittest.TestCase):
    def baseline_sheet(self, model):
        return next(s for s in w.win_local_groups_sheets(model) if s["name"] == "Suggested baseline")

    def test_one_line_yaml_in_block_mapping(self):
        name = 'CORP\\Ops "A"\nB'
        model = w.win_local_groups_model([host(1, [name])])
        flow = self.baseline_sheet(model)["rows"][0][6]
        value = {"groups": {"Administrators": [name]}}
        self.assertEqual(flow, "win_local_groups_approved: " + json.dumps(value, separators=(",", ":")))
        self.assertIn("\n" + flow + "\n---", w.win_local_groups_baseline_snippet(model))
        self.assertEqual(yaml.safe_load(flow), {"win_local_groups_approved": value})
        combined = yaml.safe_load("before: true\n" + flow + "\nafter: false\n")
        self.assertEqual(combined, {"before": True, "win_local_groups_approved": value, "after": False})

    def test_member_identity_and_display_name(self):
        sid = "S-1-5-21-111"
        hosts = []
        names = [None] * 4 + ["Alpha", "Zulu", "Zulu", "Beta", "Beta"]
        for i, name in enumerate(names):
            server = host(i)
            members = server["result"]["groups"][1]["members"]
            members.append({"sid": sid, "name": name, "source": "Domain" if name else "Unresolved"})
            if i == 0:
                members.append({"sid": "S-1-5-21-222", "name": "Beta", "source": "Domain"})
            hosts.append(server)
        model = w.win_local_groups_model(hosts)
        rows = model["prevalence"][0]["members"]
        self.assertEqual([(r["member"], r["count"], r["baseline"]) for r in rows],
                         [("Beta", 9, True), ("Beta", 1, False)])

    def admins_entries(self, model):
        return list(yaml.safe_load_all(w.win_local_groups_baseline_snippet(model)))[0]["win_local_groups_approved"]["groups"]["Administrators"]

    def test_unresolved_on_some_servers_exports_the_sid(self):
        """1.129.0 (Codex): one server names the SID CORP\\Ops, two cannot - 3 of 3, and the check must accept all three."""
        sid = "S-1-5-21-555-1105"
        hosts = []
        for i, name in enumerate(["CORP\\Ops", None, None]):
            server = host(i + 1)
            server["result"]["groups"][1]["members"].append({"sid": sid, "name": name, "source": "Domain" if name else "Unresolved"})
            hosts.append(server)
        model = w.win_local_groups_model(hosts)
        row, = model["prevalence"][0]["members"]
        self.assertEqual((row["count"], row["baseline"], row["entry"]), (3, True, sid))
        self.assertEqual(self.admins_entries(model), [sid])

    def test_well_known_principals_across_languages(self):
        """1.129.0 (Codex): Authenticated Users (S-1-5-11) on an English and a German server is one member, 2 of 2, its SID exported."""
        hosts = []
        for i, name in enumerate(["NT AUTHORITY\\Authenticated Users", "NT-AUTORIT\u00c4T\\Authentifizierte Benutzer"]):
            server = host(i + 1)
            server["result"]["groups"][1]["members"].append({"sid": "S-1-5-11", "name": name, "source": "Local", "type": "Group"})
            hosts.append(server)
        model = w.win_local_groups_model(hosts)
        rows = model["prevalence"][0]["members"]
        self.assertEqual([(r["count"], r["pct"], r["baseline"]) for r in rows], [(2, 100, True)])
        self.assertEqual(self.admins_entries(model), ["S-1-5-11"])

    def test_sheet_warns_about_yaml_omissions(self):
        hosts = [host(1), host(2)]
        for i, server in enumerate(hosts):
            server["result"]["groups"][1]["members"].append({
                "name": server["host"] + r"\{{svc}}", "source": "Local", "sid": "S-1-5-21-%d-1001" % i})
        sheet = self.baseline_sheet(w.win_local_groups_model(hosts))
        row, = sheet["rows"]
        self.assertEqual(row[1], r".\{{svc}}")
        for snippet in row[6:8]:
            self.assertEqual(yaml.safe_load(snippet)["win_local_groups_approved"]["groups"]["Administrators"], [])
        self.assertIn("Omitted entries containing template marks without a shared SID", row[sheet["columns"].index("Warning")])

    def test_single_host_local_baseline_entry(self):
        server = host(1)
        server["result"]["groups"][1]["members"].append({"source": "Local", "name": r"SRV-APP01\Administrator"})
        model = w.win_local_groups_model([server])
        for form in yaml.safe_load_all(w.win_local_groups_baseline_snippet(model)):
            self.assertEqual(form["win_local_groups_approved"]["groups"]["Administrators"], [r"{computer}\Administrator"])

    def test_threshold_validation(self):
        for setting, default in [("baseline_pct", 90), ("rare_pct", 10)]:
            for value, expected in [(None, default), ("", default), ("  ", default), (0, 0), ("100", 100)]:
                with self.subTest(setting=setting, value=value):
                    self.assertEqual(w.win_local_groups_model([], **{setting: value})[setting], expected)
            for value in (True, False, float("nan"), "sNaN", "Infinity", -1, 101, "90%"):
                with self.subTest(setting=setting, value=value):
                    with self.assertRaisesRegex(ValueError, setting + ".*between 0 and 100"):
                        w.win_local_groups_model([], **{setting: value})

    def test_sheet_strips_surrogates_before_sizing(self):
        name = "Ops\ud800" + "\udfff" * 32768 + "😀"
        sheet = self.baseline_sheet(w.win_local_groups_model([host(1, [name])]))
        for snippet in sheet["rows"][0][6:8]:
            self.assertEqual(yaml.safe_load(snippet)["win_local_groups_approved"]["groups"]["Administrators"], ["Ops😀"])

    def test_group_order_spelling_ties_and_nonresponders(self):
        for web in (True, False):
            with self.subTest(web=web):
                _group_order_spelling_ties_and_nonresponders(web)

    def test_half_up_and_unanimity(self):
        for args in [(1, 8, 13), (5, 8, 63), (199, 200, 99), (200, 200, 100)]:
            with self.subTest(args=args):
                _half_up_and_unanimity(*args)

    def test_inclusive_thresholds_and_export_contract(self):
        test_inclusive_thresholds_and_export_contract()

    def test_thresholds_use_exact_share(self):
        test_thresholds_use_exact_share()

    def test_overrides_and_no_answers(self):
        test_overrides_and_no_answers()


if __name__ == "__main__":
    unittest.main()
