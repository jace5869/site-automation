#!/usr/bin/env python3
"""Unit tests for the Active Directory health report (plugins/filter/ad_filters.py): the report built
from what the collectors answered for made-up domains (tests/ad/fixtures, made by
tests/ad/make_fixtures.sh from tests/windows/run_ad_scenario.ps1). Standard library only:
python3 tests/test_ad.py"""
import copy
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
import ad_filters as af  # noqa: E402


def fx(name):
    with open(os.path.join(HERE, "ad", "fixtures", name + ".json")) as f:
        return json.load(f)


def dc(name, host):
    return {"host": host, "reachable": True, "error": "", "data": fx(name)["dc"]}


def dom(name, host="dc01.corp.test"):
    d = fx(name)["domain"]
    return {"host": host, "domain": d.get("dns", ""), "error": "", "data": d}


NOW = fx("dc_ok")["dc"]["read_at"]


def report(dcs, domains, **opts):
    o = {"now": NOW, "hours": 24, "groups_should_be_empty": [], "dcdiag_skip": ["SystemLog"]}
    o.update(opts)
    return af.ad_report(dcs, domains, o)


def problems(r, area=None, severity=None):
    return [p for p in r["problems"] if (area is None or p["area"] == area) and (severity is None or p["severity"] == severity)]


def section(r, title_start):
    for s in r["report"]["sections"]:
        if s["title"].startswith(title_start):
            return s
    raise AssertionError("no section " + title_start)


class Healthy(unittest.TestCase):
    def setUp(self):
        self.r = report([dc("dc_ok", "dc01.corp.test")], [dom("domain_ok")])

    def test_green(self):
        self.assertEqual(self.r["problems"], [])
        self.assertEqual(self.r["report"]["status"], "ok")
        self.assertIn("all 1 domain controller(s) healthy (corp.test)", self.r["report"]["title"])

    def test_dc_not_in_inventory_is_listed_blue(self):
        s = section(self.r, "Domain controllers")
        rows = dict(zip([x[0] for x in s["rows"]], s["row_status"]))
        self.assertEqual(rows.get("DC02"), "info")
        self.assertEqual(rows.get("DC01"), "")

    def test_retired_and_own_utdv_entries_left_out(self):
        s = section(self.r, "Replication from every DC")
        self.assertEqual([x[1] for x in s["rows"]], ["DC02"])

    def test_domain_row(self):
        row = section(self.r, "Domains")["rows"][0]
        self.assertEqual(row[0], "corp.test")
        self.assertEqual(row[5], "1 of 2")
        self.assertEqual(row[8], "on")

    def test_every_section_renders_as_text(self):
        sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
        import site_filters  # noqa: E402
        txt = site_filters.report_text(self.r["report"])
        self.assertIn("Active Directory health report", txt)
        self.assertIn("Inbound replication", txt)


class Broken(unittest.TestCase):
    def setUp(self):
        self.r = report([dc("dc_ok", "dc01.corp.test"), dc("dc_broken", "dc02.corp.test")], [dom("domain_changes")])

    def has(self, area, severity, text):
        hits = [p for p in problems(self.r, area, severity) if text in p["text"] or text in str(p["object"])]
        self.assertTrue(hits, "no %s %s problem with %r in %s" % (severity, area, text, json.dumps(problems(self.r, area), indent=1)))

    def test_status_and_title(self):
        self.assertEqual(self.r["report"]["status"], "critical")
        self.assertTrue(self.r["report"]["title"].startswith("Active Directory health report: %d critical, %d warning" % (self.r["critical"], self.r["warning"])))

    def test_dc(self):
        self.has("dc", "critical", "initial replication")
        self.has("service", "critical", "kdc")
        self.has("sysvol", "critical", "share missing: SYSVOL")
        self.has("sysvol", "critical", "In error")
        self.has("time", "critical", "-91.5")
        self.has("disk", "critical", "D:")
        self.has("certificate", "critical", "expires in")

    def test_replication(self):
        self.has("replication", "critical", "8453 (replication access was denied)")   # failing, last success 30 h ago
        self.has("replication", "critical", "1722")          # never succeeded
        self.has("replication", "warning", "inbound queue")

    def test_dcdiag_and_dns(self):
        self.has("dcdiag", "critical", "Replications")
        self.has("dns", "warning", "dcdiag DNS")
        self.has("dns", "critical", "_kerberos._tcp.dc._msdcs.corp.test")
        self.has("dns", "warning", "DC02")                   # missing from the _ldap SRV record

    def test_events(self):
        self.has("event", "critical", "2213")
        self.has("event", "warning", "2887")
        self.has("event", "warning", "5719")

    def test_sites(self):
        self.has("sites", "warning", "client address")
        self.has("sites", "warning", "not linked to a site")

    def test_domain(self):
        self.has("fsmo", "critical", "Infrastructure master")   # dc09 is not a DC
        self.has("domain", "warning", "Recycle Bin")
        self.has("domain", "critical", "RID pool")
        self.has("domain", "warning", "krbtgt")
        self.has("sysvol", "warning", "'Start'")
        self.has("trust", "critical", "partner.test")
        self.has("trust", "warning", "was created")
        self.has("schema", "warning", "schema version")
        self.has("gpo", "warning", "version 7 in AD but 5 in SYSVOL")
        self.has("gpo", "warning", "no folder")

    def test_privileged_changes_with_who(self):
        s = section(self.r, "Privileged group changes")
        rows = {(x[3], x[4]): x for x in s["rows"]}
        self.assertEqual(rows[("ADDED", "Mallory")][5], "DC02")
        self.assertEqual(rows[("ADDED", "Mallory")][6], "CORP\\admin1")     # from DC02's Security log
        self.assertEqual(rows[("REMOVED", "Old Admin")][6], "-")              # not audited
        self.assertEqual(len(problems(self.r, "privileged", "warning")), 2)

    def test_changes(self):
        created = section(self.r, "Users, groups and computers created")
        by = {x[3]: x[6] for x in created["rows"]}
        self.assertEqual(by["mallory"], "CORP\\admin1")
        self.assertEqual(sorted(x[4] for x in created["rows"]), ["CREATED", "CREATED", "DELETED"])
        acc = section(self.r, "Accounts enabled")
        self.assertEqual(sorted((x[3], x[4]) for x in acc["rows"]), [("carol", "DISABLED"), ("dave", "UNLOCKED")])
        self.assertIn("locked out now: erin", " ".join(acc["lines"]))
        gpo = section(self.r, "Group Policy changes")
        self.assertEqual(sorted(x[3] for x in gpo["rows"]), ["CREATED", "LINKS CHANGED"])
        cfg = section(self.r, "Schema and configuration")
        whats = sorted(x[2] for x in cfg["rows"])
        self.assertEqual(whats, ["attributeSchema", "domain controller", "schema", "subnet", "trust"])

    def test_security_log_cleared(self):
        self.has("security", "critical", "Security log was cleared")

    def test_severity_settings(self):
        r = report([dc("dc_ok", "dc01.corp.test"), dc("dc_broken", "dc02.corp.test")], [dom("domain_changes")],
                   privileged_change_severity="critical", dc_change_severity="warning", schema_change_severity="info")
        self.assertEqual(len(problems(r, "privileged", "critical")), 2)
        self.assertTrue(problems(r, "config", "warning"))
        self.assertFalse(problems(r, "schema"))

    def test_groups_that_should_be_empty(self):
        r = report([dc("dc_ok", "dc01.corp.test")], [dom("domain_changes")], groups_should_be_empty=["Domain Admins"])
        self.assertTrue([p for p in problems(r, "privileged", "warning") if "should be empty" in p["text"]])

    def test_max_rows(self):
        r = report([dc("dc_ok", "dc01.corp.test"), dc("dc_broken", "dc02.corp.test")], [dom("domain_changes")], max_rows=1)
        s = section(r, "Events")
        self.assertEqual(len(s["rows"]), 1)
        self.assertIn("The first 1 of 3", s["text"])
        self.assertEqual(s["row_status"], ["critical"])        # problems first


class NotRead(unittest.TestCase):
    def test_unreachable_dc(self):
        r = report([dc("dc_ok", "dc01.corp.test"), {"host": "dc02.corp.test", "reachable": False, "error": "timed out", "data": None}],
                   [dom("domain_ok")])
        p = problems(r, "dc", "critical")
        self.assertEqual(len(p), 1)
        self.assertIn("timed out", p[0]["text"])
        self.assertEqual(p[0]["domain"], "corp.test")           # found by its name in the domain's DC list
        self.assertEqual(r["report"]["summary"][0]["value"], "1 of 2")
        self.assertEqual([x["read"] for x in r["dcs"]], [True, False])

    def test_domain_part_not_read(self):
        r = report([dc("dc_ok", "dc01.corp.test")], [{"host": "dc01.corp.test", "domain": "corp.test", "error": "boom", "data": None}])
        self.assertTrue([p for p in problems(r, "domain", "critical") if "boom" in p["text"]])

    def test_no_ad_module(self):
        r = report([dc("dc_no_ad", "dc01.corp.test")], [])
        self.assertTrue([p for p in problems(r, "dc", "warning") if "PowerShell module" in p["text"]])
        self.assertFalse(problems(r, "dc", "critical"))
        notes = section(r, "Not read")["lines"]
        self.assertTrue(any("ActiveDirectory" in n for n in notes))

    def test_dcdiag_not_in_english(self):
        d = copy.deepcopy(dc("dc_ok", "dc01.corp.test"))
        d["data"]["dcdiag"] = {"ran": True, "tests": [], "error": "no test result in its output (is dcdiag in English?)"}
        r = report([d], [dom("domain_ok")])
        self.assertTrue([p for p in problems(r, "dcdiag", "warning") if "English" in p["text"]])

    def test_empty(self):
        r = report([], [])
        self.assertEqual(r["report"]["status"], "ok")
        self.assertEqual(r["report"]["summary"][0]["value"], "0 of 0")


class Helpers(unittest.TestCase):
    def test_nc_name(self):
        self.assertEqual(af.nc_name("DC=corp,DC=test"), "corp.test")
        self.assertEqual(af.nc_name("CN=Configuration,DC=corp,DC=test"), "Configuration")
        self.assertEqual(af.nc_name("CN=Schema,CN=Configuration,DC=corp,DC=test"), "Schema")
        self.assertEqual(af.nc_name("DC=ForestDnsZones,DC=corp,DC=test"), "ForestDnsZones")

    def test_short(self):
        self.assertEqual(af.short("CN=Old\\, Admin,CN=Users,DC=corp"), "Old, Admin")
        self.assertEqual(af.short("dc01.corp.test"), "dc01")

    def test_age_text(self):
        self.assertEqual(af.age_text(None), "never")
        self.assertEqual(af.age_text(0.5), "30 min")
        self.assertEqual(af.age_text(30), "30.0 h")
        self.assertEqual(af.age_text(100), "4 days")

    def test_ps_error(self):
        clixml = '#< CLIXML\r\n<Objs Version="1.1.0.1"><S S="Error">dcdiag.exe : The term_x000D__x000A_</S><S S="Error">is not recognized_x000D__x000A_</S></Objs>'
        self.assertEqual(af.ps_error(clixml), "dcdiag.exe : The term is not recognized")


if __name__ == "__main__":
    unittest.main(verbosity=1)
