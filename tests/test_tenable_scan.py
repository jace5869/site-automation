#!/usr/bin/env python3
"""Unit tests for the Tenable.sc scan job: the pure parts of roles/tenable_scan/library/site_tenable_scan.py (the login from the
credential, a zone's ranges, the zone of each host and why, the plan, a scanner's status, the scans' names) and
plugins/filter/tenable_filters.py (the targets from the inventory, the report). Needs ansible-core importable.
    python3 tests/test_tenable_scan.py"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
sys.path.insert(0, os.path.join(HERE, "..", "roles", "tenable_scan", "library"))
import tenable_filters as af  # noqa: E402
import site_tenable_scan as sc  # noqa: E402

ZONES = [{"id": "1", "name": "Datacenter East", "ranges": ["192.0.2.0/25", "198.51.100.10-198.51.100.20"],
          "scanners": [{"id": "1", "name": "scanner-east", "status": "working"}]},
         {"id": "2", "name": "Datacenter West", "ranges": ["192.0.2.128/25"], "scanners": [{"id": "2", "name": "scanner-west", "status": "unable to connect"}]},
         {"id": "3", "name": "Lab", "ranges": ["192.0.2.0/24", "2001:db8::/32"], "scanners": [{"id": "1", "name": "scanner-east", "status": "working"}]}]


def t(name, address="", zone="", groups=()):
    return {"name": name, "address": address or name, "zone": zone, "groups": list(groups)}


class Connection(unittest.TestCase):
    def test_from_the_credential(self):
        c = sc.connection({"TENABLE_SC_URL": "tenable.example.mil/rest/", "TENABLE_SC_ACCESS_KEY": "a", "TENABLE_SC_SECRET_KEY": "s", "TENABLE_SC_VERIFY_SSL": "False"})
        self.assertEqual((c["url"], c["access_key"], c["verify"]), ("https://tenable.example.mil", "a", False))
        c = sc.connection({"TENABLE_SC_URL": "https://tenable.example.mil", "TENABLE_SC_USERNAME": "svc", "TENABLE_SC_PASSWORD": "p"})
        self.assertEqual((c["user"], c["password"], c["verify"], c["access_key"]), ("svc", "p", True, ""))
        self.assertEqual(sc.connection({})["url"], "")

    def test_scanner_status(self):
        self.assertEqual(sc.scanner_status("1"), "working")
        self.assertEqual(sc.scanner_status("16"), "unable to connect")
        self.assertEqual(sc.scanner_status("24"), "disabled, unable to connect")
        self.assertEqual(sc.scanner_status("4096"), "status 4096")
        self.assertEqual(sc.scanner_status(None), "unknown")

    def test_usable(self):
        self.assertEqual([x["id"] for x in sc._usable({"usable": [{"id": 1}, {"id": 2}], "manageable": [{"id": 2}, {"id": 3}]})], [1, 2, 3])
        self.assertEqual(sc._usable([{"id": 1}, "x"]), [{"id": 1}])
        self.assertEqual(sc.error_text({"error_msg": "Invalid  credentials."}), "Invalid credentials.")


class Zones(unittest.TestCase):
    def test_ranges(self):
        self.assertEqual(sc.parse_range("192.0.2.0/25"), (3221225984, 3221226111))
        self.assertEqual(sc.parse_range("192.0.2.5"), (3221225989, 3221225989))
        self.assertEqual(sc.parse_range("198.51.100.10-198.51.100.20")[1] - sc.parse_range("198.51.100.10-198.51.100.20")[0], 10)
        self.assertIsNone(sc.parse_range("198.51.100.20-198.51.100.10"))          # backwards
        self.assertIsNone(sc.parse_range("scanner.example.mil"))
        self.assertIsNone(sc.parse_range(""))
        self.assertIsNotNone(sc.parse_range("2001:db8::/32"))

    def test_the_narrowest_range_wins(self):
        self.assertEqual(sc.zone_for_ip("192.0.2.10", ZONES)["name"], "Datacenter East")      # /25 beats the Lab's /24
        self.assertEqual(sc.zone_for_ip("192.0.2.130", ZONES)["name"], "Datacenter West")
        self.assertEqual(sc.zone_for_ip("198.51.100.15", ZONES)["name"], "Datacenter East")
        self.assertEqual(sc.zone_for_ip("2001:db8::1", ZONES)["name"], "Lab")
        self.assertIsNone(sc.zone_for_ip("203.0.113.9", ZONES))
        self.assertIsNone(sc.zone_for_ip("not-an-ip", ZONES))

    def test_zone_named(self):
        self.assertEqual(sc.zone_named("datacenter west", ZONES)["id"], "2")
        self.assertEqual(sc.zone_named("3", ZONES)["name"], "Lab")
        self.assertIsNone(sc.zone_named("Nope", ZONES))

    def test_site_of(self):
        self.assertEqual(sc.site_of(t("XY2app01.example.mil"), r"^([a-z]{2}[0-9])"), "xy2")
        self.assertEqual(sc.site_of(t("app01"), r"^([a-z]{2}[0-9])"), "")
        self.assertEqual(sc.site_of(t("app01"), ""), "")
        self.assertEqual(sc.site_of(t("app01"), "(["), "")                               # a broken expression: no site


class Choose(unittest.TestCase):
    def choose(self, target, ip, sites=None, regex="", default=""):
        return sc.choose_zone(target, ip, ZONES, sites or {}, regex, default)

    def test_by_address(self):
        c = self.choose(t("web01"), "192.0.2.10")
        self.assertEqual((c["zone"], c["zone_id"], c["how"], c["note"], c["problem"]), ("Datacenter East", "1", "its address", "", ""))

    def test_tenable_zone_first_and_the_address_elsewhere_said(self):
        c = self.choose(t("db01", zone="Datacenter West"), "192.0.2.11")
        self.assertEqual((c["zone"], c["how"]), ("Datacenter West", "tenable_zone"))
        self.assertIn("is in the ranges of zone Datacenter East, not of Datacenter West", c["note"])
        c = self.choose(t("db01", zone="Nope"), "192.0.2.11")
        self.assertEqual(c["problem"], "tenable_zone Nope is no zone of Tenable.sc")

    def test_by_site_group_or_name_code(self):
        c = self.choose(t("app01", groups=["web", "dc_west"]), "192.0.2.12", {"DC_West": "Datacenter West"})
        self.assertEqual((c["zone"], c["how"]), ("Datacenter West", "site DC_West"))
        c = self.choose(t("xy2app01"), "192.0.2.12", {"xy2": "datacenter west"}, r"^([a-z]{2}[0-9])")
        self.assertEqual((c["zone"], c["how"]), ("Datacenter West", "site xy2"))
        c = self.choose(t("xy2app01"), "192.0.2.12", {"xy2": "Nope"}, r"^([a-z]{2}[0-9])")
        self.assertIn("maps xy2 to Nope, which is no zone", c["problem"])
        c = self.choose(t("ab1app01"), "192.0.2.12", {"xy2": "Datacenter West"}, r"^([a-z]{2}[0-9])")
        self.assertEqual(c["how"], "its address")                                        # another site: the address decides

    def test_no_zone_default_auto_or_none(self):
        c = self.choose(t("web03"), "203.0.113.9")
        self.assertIn("no zone's ranges hold 203.0.113.9", c["problem"])
        c = self.choose(t("web03"), "203.0.113.9", default="auto")
        self.assertEqual((c["zone"], c["zone_id"], c["how"], c["problem"]), ("(automatic distribution)", "0", "default: auto", ""))
        c = self.choose(t("web03"), "203.0.113.9", default="Lab")
        self.assertEqual((c["zone"], c["how"]), ("Lab", "default"))
        self.assertIn("no zone's ranges hold", c["note"])
        c = self.choose(t("web03"), "203.0.113.9", default="Nope")
        self.assertIn("tenable_scan_zone_default Nope is no zone", c["problem"])

    def test_not_resolved(self):
        c = self.choose(t("nores01.invalid"), "")
        self.assertIn("does not resolve from the execution environment", c["problem"])
        c = self.choose(t("nores01.invalid", zone="Lab"), "")                             # tenable_zone needs no address
        self.assertEqual((c["zone"], c["problem"]), ("Lab", ""))


class Plan(unittest.TestCase):
    def test_grouped_by_zone(self):
        cache = {"web01.example.mil": "192.0.2.10", "web02.example.mil": "192.0.2.130", "nores01.invalid": ""}
        p = sc.plan([t("web01.example.mil"), t("web02.example.mil"), t("db01", "192.0.2.11", zone="Datacenter West"), t("web03", "203.0.113.9"),
                     t("nores01.invalid"), t("web01.example.mil")], ZONES, {}, "", "", cache)
        self.assertEqual([(g["zone"], g["targets"], g["ips"]) for g in p["scans"]],
                         [("Datacenter East", ["web01.example.mil", "web01.example.mil"], ["192.0.2.10"]),
                          ("Datacenter West", ["web02.example.mil", "db01"], ["192.0.2.130", "192.0.2.11"])])
        self.assertEqual(len(p["problems"]), 2)
        self.assertTrue(p["problems"][0].startswith("web03: no zone's ranges hold 203.0.113.9"))
        self.assertEqual([x["ip"] for x in p["targets"]][:3], ["192.0.2.10", "192.0.2.130", "192.0.2.11"])
        self.assertEqual(sc.plan([], ZONES, {}, "", ""), {"targets": [], "scans": [], "problems": []})

    def test_scan_name(self):
        self.assertEqual(sc.scan_name("AAP {target} - {zone}", "web", "Datacenter East"), "AAP web - Datacenter East")
        self.assertEqual(sc.scan_name("", "web", "Z"), "AAP web - Z")
        self.assertEqual(len(sc.scan_name("x" * 200, "a", "b")), 120)

    def test_wait_for_looks_until_done_or_the_time_is_up(self):
        class FakeApi(object):
            def __init__(self):
                self.looks = {}

            def call(self, method, path, body=None):
                rid = path.split("/")[2].split("?")[0]
                self.looks[rid] = self.looks.get(rid, 0) + 1
                if rid == "9":
                    raise sc.ApiError("gone")
                st = "Completed" if self.looks[rid] >= 2 and rid != "8" else "Running"       # 8 never ends
                return {"status": st, "importStatus": "Finished" if st == "Completed" else "", "scannedIPs": "2", "totalIPs": "2", "errorDetails": ""}

        api = FakeApi()
        slept = []
        res = sc.wait_for(api, [{"result_id": "7"}, {"result_id": "9"}, {"result_id": ""}], 10, 5, sleep=slept.append, clock=lambda: 0)
        self.assertEqual([(r["result_id"], r["status"]) for r in res], [("7", "completed"), ("9", "not read")])
        self.assertEqual(res[1]["error"], "gone")
        self.assertEqual(slept, [5])
        clock = iter([0, 0, 700, 700, 700])
        res = sc.wait_for(FakeApi(), [{"result_id": "8"}], 10, 5, sleep=lambda s: None, clock=lambda: next(clock))
        self.assertEqual(res[0]["status"], "still running after 10 minutes")


class Targets(unittest.TestCase):
    def test_from_the_inventory_and_a_list(self):
        hv = {"web01.example.mil": {"ansible_host": "192.0.2.10", "group_names": ["web", "dc_east"]},
              "db01.example.mil": {"tenable_zone": "Datacenter West", "group_names": ["db"]}, "localhost": {}}
        out = af.tenable_scan_targets(["web01.example.mil", "localhost", "db01.example.mil", "web01.example.mil"], hv,
                                   ["198.51.100.5", {"name": "nas01", "address": "198.51.100.6", "zone": "Lab"}, "db01.example.mil"])
        self.assertEqual(out, [{"name": "web01.example.mil", "address": "192.0.2.10", "zone": "", "groups": ["web", "dc_east"]},
                               {"name": "db01.example.mil", "address": "db01.example.mil", "zone": "Datacenter West", "groups": ["db"]},
                               {"name": "198.51.100.5", "address": "198.51.100.5", "zone": "", "groups": []},
                               {"name": "nas01", "address": "198.51.100.6", "zone": "Lab", "groups": []}])
        self.assertEqual(af.tenable_scan_targets(["ghost"], {}), [{"name": "ghost", "address": "ghost", "zone": "", "groups": []}])


def result(**kw):
    r = {"ok": True, "error": "", "url": "https://tenable.example.mil", "user": "svc-tenable", "zones": ZONES,
         "policies": [{"id": "7", "name": "Basic Network Scan", "description": "ports"}], "repositories": [{"id": "1", "name": "Main", "type": "Local", "format": "IPv4"}],
         "credentials": [{"id": "3", "name": "Linux ssh", "type": "ssh"}], "plan": {"targets": [], "scans": [], "problems": []}, "launched": [], "results": [], "launch": False}
    r.update(kw)
    return r


PLAN = {"targets": [{"name": "web01", "address": "192.0.2.10", "ip": "192.0.2.10", "zone": "Datacenter East", "zone_id": "1", "how": "its address", "note": "", "problem": ""},
                    {"name": "web03", "address": "203.0.113.9", "ip": "203.0.113.9", "zone": "", "zone_id": "", "how": "", "note": "",
                     "problem": "no zone's ranges hold 203.0.113.9 (and no tenable_zone, site or default zone): not scanned"}],
        "scans": [{"zone": "Datacenter East", "zone_id": "1", "targets": ["web01"], "ips": ["192.0.2.10"]}],
        "problems": ["web03: no zone's ranges hold 203.0.113.9 (and no tenable_zone, site or default zone): not scanned"]}


class Report(unittest.TestCase):
    def test_nothing_set(self):
        r = af.tenable_scan_report(result(), {})
        self.assertEqual(r["status"], "ok")
        self.assertIn("nothing to scan - 3 zone(s), 1 policies, 1 repositories listed", r["title"])
        sec = {s["title"]: s for s in r["sections"]}
        self.assertIn("Scan zones and their scanners", sec)
        self.assertEqual(sec["Scan zones and their scanners"]["row_status"][0], "")                     # East: fine, not used
        self.assertEqual(sec["Scan zones and their scanners"]["row_status"][1], "warning")              # West: its scanner cannot be reached
        self.assertEqual(sec["Policies this account may use"]["lines"], ["Basic Network Scan (id 7) - ports"])
        self.assertIn("Repositories", sec)
        self.assertIn("Scan credentials", sec)

    def test_would_launch_and_check(self):
        r = af.tenable_scan_report(result(plan=PLAN), {"policy": "", "repository": "", "target_label": "web"})
        self.assertEqual(r["status"], "warning")
        self.assertIn("would launch 1 scan(s) in 1 zone(s) for 1 host(s), nothing launched: set tenable_scan_policy", r["title"])
        self.assertIn("1 host(s) not scanned", r["title"])
        sec = {s["title"]: s for s in r["sections"]}
        self.assertEqual(sec["What would be launched"]["lines"][0], "zone Datacenter East: 1 host(s) - web01")
        self.assertEqual(sec["The hosts and their zones"]["rows"][0][0], "web03")                      # the problem first
        self.assertEqual(sec["The hosts and their zones"]["row_status"], ["warning", "ok"])
        r = af.tenable_scan_report(result(plan=PLAN), {"policy": "Basic Network Scan", "repository": "Main", "check": True})
        self.assertIn("Check: would launch 1 scan(s)", r["title"])
        self.assertIn("Check: nothing launched", r["subtitle"])

    def test_launched_waited_and_failed(self):
        launched = [{"zone": "Datacenter East", "zone_id": "1", "name": "AAP web - Datacenter East", "scan_id": "101", "result_id": "501", "targets": ["web01"], "status": "launched", "error": ""},
                    {"zone": "Datacenter West", "zone_id": "2", "name": "AAP web - Datacenter West", "scan_id": "102", "result_id": "", "targets": ["web02"], "status": "not launched", "error": "Scan zone unavailable"}]
        results = [{"result_id": "501", "status": "completed", "critical": 1, "high": 2, "medium": 3, "low": 1, "info": 5,
                    "hosts": [{"ip": "192.0.2.10", "name": "host-10", "critical": 1, "high": 2, "medium": 3, "low": 1, "total": 12}]}]
        r = af.tenable_scan_report(result(plan=PLAN, launched=launched, results=results, launch=True), {"policy": "Basic Network Scan", "repository": "Main"})
        self.assertEqual(r["status"], "critical")
        self.assertIn("1 scan(s) launched in 1 zone(s) for 1 host(s), 1 not launched, 1 critical, 2 high, 1 host(s) not scanned", r["title"])
        sec = {s["title"]: s for s in r["sections"]}
        self.assertEqual(sec["Scans"]["rows"][0][:6], ["Datacenter East", "AAP web - Datacenter East", "101", "501", 1, "launched - completed"])
        self.assertEqual(sec["Scans"]["rows"][0][7:], [1, 2, 3, 1])
        self.assertEqual(sec["Scans"]["row_status"], ["critical", "critical"])
        self.assertEqual(sec["Findings by host, the worst first"]["rows"][0], ["Datacenter East", "host-10", "192.0.2.10", 1, 2, 3, 1, 12])
        self.assertNotIn("Policies this account may use", sec)
        self.assertEqual([x["value"] for x in r["summary"] if x["label"] in ("Scans launched", "Critical")], [1, 1])

    def test_not_run(self):
        r = af.tenable_scan_report(result(ok=False, error="Tenable.sc did not answer"), {})
        self.assertEqual((r["status"], r["sections"][0]["title"]), ("critical", "Not run"))
        self.assertIn("not run", r["title"])


if __name__ == "__main__":
    unittest.main()
