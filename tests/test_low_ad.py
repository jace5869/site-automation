#!/usr/bin/env python3
"""1.56.0: the "low" level (worth knowing, nothing to do now) in the AD health report and the account lockout report:
which rows moved from info to low, that the word Low is always shown with it, that low is never a problem and never
the report's status, and that the ad_*_severity settings take "low" too. Standard library only:
python3 tests/test_low_ad.py"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
import ad_filters as af  # noqa: E402
import test_ad as ta  # noqa: E402
import test_ad_lockout as tl  # noqa: E402


def rows_of(s, status):
    return [r for r, st in zip(s.get("rows") or [], s.get("row_status") or []) if st == status]


class Rank(unittest.TestCase):
    def test_order(self):
        self.assertEqual(sorted(["ok", "info", "low", "warning", "critical"], key=lambda x: af.RANK[x]),
                         ["critical", "warning", "low", "info", "ok"])
        self.assertEqual(af.worst(["ok", "low", "info"]), "low")
        self.assertEqual(af.worst(["low", "warning"]), "warning")


class Events(unittest.TestCase):
    def report(self, events):
        d = ta.dc("dc_ok", "dc01.corp.test")
        d["data"] = dict(d["data"], events=events)
        return ta.report([d], [ta.dom("domain_ok")])

    def ev(self, log, eid, level=3, source=""):
        return {"log": log, "id": eid, "source": source, "count": 2, "last": ta.NOW, "level": level, "message": "made-up text"}

    def test_listed_events_are_low(self):
        r = self.report([self.ev("Directory Service", 2886), self.ev("Directory Service", 2889),
                         self.ev("DFS Replication", 5014), self.ev("System", 5807)])
        s = ta.section(r, "Events")
        self.assertEqual(s["row_status"], ["low"] * 4)
        self.assertEqual([x[3] for x in s["rows"]], ["LOW"] * 4)          # the word, not the colour alone
        self.assertEqual(ta.problems(r, "event"), [])                     # never a problem
        self.assertEqual(r["report"]["status"], "ok")                     # never the report's status

    def test_other_error_is_low_critical_level_stays_warning(self):
        r = self.report([self.ev("System", 9999, level=2), self.ev("System", 9998, level=1)])
        s = ta.section(r, "Events")
        self.assertEqual(s["row_status"], ["warning", "low"])             # warning first, low after
        self.assertEqual([x[3] for x in s["rows"]], ["WARNING", "LOW"])
        self.assertIn("Low: worth knowing", s["text"])

    def test_listed_warning_unchanged(self):
        r = self.report([self.ev("Directory Service", 2887)])
        self.assertEqual(ta.section(r, "Events")["row_status"], ["warning"])


class ClientsSitesCerts(unittest.TestCase):
    def test_clients_in_no_site_below_the_warn_line_are_low(self):
        d = ta.dc("dc_ok", "dc01.corp.test")
        d["data"] = dict(d["data"], no_client_site={"clients": ["192.0.2.7 (PC1)"], "count": 3, "distinct": 1, "error": ""})
        r = ta.report([d], [ta.dom("domain_ok")], no_client_site_warn=2)
        s = ta.section(r, "Clients in no site")
        self.assertEqual(s["row_status"], ["low"])
        self.assertEqual(s["rows"][0][1], "1 (Low)")
        self.assertEqual(ta.problems(r, "sites"), [])
        # at the warn line: amber as before, the number alone
        r = ta.report([d], [ta.dom("domain_ok")], no_client_site_warn=1)
        s = ta.section(r, "Clients in no site")
        self.assertEqual((s["row_status"], s["rows"][0][1]), (["warning"], 1))
        self.assertEqual(len(ta.problems(r, "sites", "warning")), 1)

    def test_no_ldaps_or_kdc_certificate_is_low(self):
        d = ta.dc("dc_ok", "dc01.corp.test")
        d["data"] = dict(d["data"], certs=[])
        r = ta.report([d], [ta.dom("domain_ok")])
        s = ta.section(r, "Certificates")
        self.assertEqual(s["row_status"], ["low", "low"])
        self.assertEqual([x[2] for x in s["rows"]], ["none (Low)", "none (Low)"])
        self.assertEqual(s["cell_status"][0][2], "low")
        self.assertIn("Low: none", s["text"])
        self.assertEqual(r["report"]["status"], "ok")

    def test_site_without_dc_or_subnet_is_low(self):
        dm = ta.dom("domain_ok")
        dm["data"] = dict(dm["data"], sites=[{"name": "HQ", "dcs": 2, "subnets": 1}, {"name": "SITEB", "dcs": 0, "subnets": 0},
                                             {"name": "SITEA", "dcs": 1, "subnets": 0}])
        r = ta.report([ta.dc("dc_ok", "dc01.corp.test")], [dm])
        s = ta.section(r, "Sites")
        self.assertEqual(s["row_status"], ["low", "low", ""])
        self.assertEqual(s["rows"][0][2:], ["0 (Low)", "0 (Low)"])
        self.assertEqual(s["rows"][1][2:], [1, "0 (Low)"])
        self.assertEqual(s["rows"][2][2:], [2, 1])                        # a healthy site: as before
        self.assertIn("Low: no DC", s["text"])
        self.assertEqual(r["report"]["status"], "ok")


class SeveritySettings(unittest.TestCase):
    def setUp(self):
        self.dm = ta.dom("domain_ok")
        g = {"name": "Domain Admins", "sam": "Domain Admins", "changes": [{"action": "added", "member": "CN=svc_test,OU=x,DC=corp,DC=test",
                                                                           "dc": "DC01", "time": ta.NOW}]}
        self.dm["data"] = dict(self.dm["data"], groups=[g], config_changes=[{"class": "nTDSDSA", "change": "created", "name": "DC09", "time": ta.NOW}])

    def test_sev_accepts_low(self):
        self.assertEqual(af._sev({"x": "LOW"}, "x", "warning"), "low")
        self.assertEqual(af._sev({"x": "loud"}, "x", "warning"), "warning")

    def test_low_settings(self):
        r = ta.report([ta.dc("dc_ok", "dc01.corp.test")], [self.dm], privileged_change_severity="low", dc_change_severity="low")
        s = ta.section(r, "Privileged group changes")
        self.assertEqual((s["row_status"], s["rows"][0][3]), (["low"], "ADDED (Low)"))
        s = ta.section(r, "Schema and configuration changes")
        self.assertEqual(rows_of(s, "low")[0][4], "CREATED (Low)")
        self.assertEqual(ta.problems(r, "privileged") + ta.problems(r, "config"), [])
        tile = [t for t in r["report"]["summary"] if t["label"] == "Privileged changes"][0]
        self.assertEqual((tile["status"], tile.get("note")), ("low", "Low"))
        self.assertEqual(r["report"]["status"], "ok")

    def test_defaults_unchanged(self):
        r = ta.report([ta.dc("dc_ok", "dc01.corp.test")], [self.dm])
        s = ta.section(r, "Privileged group changes")
        self.assertEqual((s["row_status"], s["rows"][0][3]), (["warning"], "ADDED"))
        tile = [t for t in r["report"]["summary"] if t["label"] == "Privileged changes"][0]
        self.assertEqual((tile["status"], "note" in tile), ("warning", False))
        self.assertEqual(len(ta.problems(r, "privileged", "warning")), 1)


class Lockout(unittest.TestCase):
    def test_failed_sign_ins_tile_is_low(self):
        r = tl.report()
        tile = [t for t in r["report"]["summary"] if t["label"] == "Failed sign-ins"][0]
        self.assertTrue(tile["value"])
        self.assertEqual((tile["status"], tile["note"]), ("low", "Low"))
        src = [t for t in r["report"]["summary"] if t["label"] == "Source computers"][0]
        self.assertEqual(src["status"], "info")                           # kept as it was

    def test_no_failed_sign_ins_ok(self):
        r = tl.report([])
        tile = [t for t in r["report"]["summary"] if t["label"] == "Failed sign-ins"][0]
        self.assertEqual((tile["value"], tile["status"], "note" in tile), (0, "ok", False))

if __name__ == "__main__":
    unittest.main()
