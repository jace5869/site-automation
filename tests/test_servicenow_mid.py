#!/usr/bin/env python3
"""Unit tests for plugins/filter/servicenow_mid_filters.py (the MID Server part of the ServiceNow health
check): python3 tests/test_servicenow_mid.py"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugins", "filter"))
import servicenow_mid_filters as mf  # noqa: E402

NOW = datetime.datetime(2026, 10, 24, 12, 0, 0, tzinfo=datetime.timezone.utc).timestamp()


def agent(name, status="Up", validated="true", host="mid01.example.mil", version="xanadu-07-02-2024", minutes_ago=2):
    t = datetime.datetime.fromtimestamp(NOW - minutes_ago * 60, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return {"name": name, "status": status, "validated": validated, "host_name": host, "version": version, "last_refreshed": t}


def ids(findings):
    return sorted((f["id"], f["severity"]) for f in findings)


def mid(service="snc_mid_mid01", name="mid01", state="Running", start="Auto", url="https://dev.example.mil/", log_age=1,
        severe=0, jvm=0, free=40.0):
    return {"service": service, "display": "ServiceNow MID Server_" + name, "state": state, "start_mode": start,
            "agent_dir": "D:\\ServiceNow\\%s\\agent" % name, "config": True, "name": name, "url": url,
            "log": {"exists": True, "age_min": log_age, "count": severe, "last": ["10/24/26 11:00:00 (1) Worker SEVERE boom"] if severe else []},
            "wrapper": {"exists": True, "age_min": 5, "count": jvm, "last": ["wrapper | 2026/10/24 10:00:00 | JVM appears hung"] if jvm else []},
            "disk": {"drive": "D:", "free_gb": 20.0, "free_pct": free}}


OPTS = {"stale_minutes": 15, "queue_minutes": 10, "queue_warn": 1, "queue_critical": 50, "instance_url": "https://dev.example.mil",
        "log_stale_minutes": 30, "severe_warn": 20, "jvm_warn": 1, "disk_warn": 10, "disk_critical": 5, "hours": 24}


class Agents(unittest.TestCase):
    def test_age_names_and_filter(self):
        a = mf.sn_mid_agents([agent("mid02", minutes_ago=40), agent("mid01"), {"name": ""}], {"now": NOW})
        self.assertEqual([x["name"] for x in a], ["mid01", "mid02"])
        self.assertEqual(a[1]["age_min"], 40.0)
        self.assertTrue(a[0]["validated"])
        self.assertEqual([x["name"] for x in mf.sn_mid_agents([agent("mid01"), agent("lab01")], {"now": NOW, "names": ["MID*"]})], ["mid01"])

    def test_queue_counts(self):
        rows = [{"groupby_fields": [{"field": "agent", "value": "mid.server.mid01"}], "stats": {"count": "7"}},
                {"groupby_fields": [{"field": "agent", "value": "mid.server.mid02"}], "stats": {"count": "0"}}]
        self.assertEqual(mf.sn_mid_queue(rows), {"mid01": 7})


class Findings(unittest.TestCase):
    def judge(self, rows, queue=None, issues=None, hosts=None, **o):
        return mf.sn_mid_findings(mf.sn_mid_agents(rows, {"now": NOW}), queue or {}, issues or [], hosts or [], dict(OPTS, **o))

    def test_all_well(self):
        self.assertEqual(self.judge([agent("mid01"), agent("mid02", host="mid02.example.mil")]), [])

    def test_down_not_validated_stale_paused(self):
        f = self.judge([agent("mid01", status="Down"), agent("mid02", validated="false"), agent("mid03", minutes_ago=45),
                        agent("mid04", status="Paused")])
        self.assertEqual(ids(f), [("servicenow:mid-stale:mid03", "critical"), ("servicenow:mid:mid01", "critical"),
                                  ("servicenow:mid:mid02", "critical"), ("servicenow:mid:mid04", "warning")])
        self.assertIn("has not reported for 45 minutes", [x for x in f if x["id"].endswith("mid03")][0]["summary"])

    def test_the_host_side_named_in_the_instance_finding(self):
        f = self.judge([agent("mid01", status="Down")], hosts=[{"host": "mid01.example.mil", "reachable": True, "mids": [mid(state="Stopped")]}])
        down = [x for x in f if x["id"] == "servicenow:mid:mid01"][0]
        self.assertIn("its service snc_mid_mid01 is Stopped on mid01.example.mil", down["summary"])
        self.assertIn("servicenow:mid-host:mid01.example.mil:snc_mid_mid01:stopped", [x["id"] for x in f])

    def test_expected_versions_queue_issues(self):
        f = self.judge([agent("mid01"), agent("mid02", version="washingtondc-12-20-2023")],
                       queue={"mid01": 3, "mid02": 80},
                       issues=[{"mid_server": {"display_value": "mid01"}, "message": "Certificate expires soon", "state": "new"},
                               {"mid_server": "mid01", "message": "old", "state": "resolved"}],
                       expected=["mid01", "mid03"])
        self.assertEqual(ids(f), [("servicenow:mid-issues:mid01", "warning"), ("servicenow:mid-missing:mid03", "critical"),
                                  ("servicenow:mid-queue:mid01", "warning"), ("servicenow:mid-queue:mid02", "critical"),
                                  ("servicenow:mid-versions", "warning")])
        self.assertIn("1 open MID Server Issue(s): Certificate expires soon", [x for x in f if "issues" in x["id"]][0]["summary"])


class Hosts(unittest.TestCase):
    def test_host_problems(self):
        hosts = [{"host": "mid01.example.mil", "reachable": True,
                  "mids": [mid(start="Manual", log_age=95, severe=25, jvm=2, free=4.0, url="https://prod.example.mil/")]},
                 {"host": "mid02.example.mil", "reachable": False, "error": "ntlm: the specified credentials were rejected"},
                 {"host": "mid03.example.mil", "reachable": True, "mids": []},
                 {"host": "mid04.example.mil", "reachable": True, "error": "not a Windows host"}]
        f = mf.sn_mid_host_findings(hosts, OPTS)
        b = "servicenow:mid-host:mid01.example.mil:snc_mid_mid01:"
        self.assertEqual(ids(f), sorted([(b + "start-mode", "warning"), (b + "url", "critical"), (b + "log-stale", "critical"),
                                         (b + "severe", "warning"), (b + "jvm", "warning"), (b + "disk", "critical"),
                                         ("servicenow:mid-host:mid02.example.mil:unreachable", "critical"),
                                         ("servicenow:mid-host:mid03.example.mil:no-service", "warning"),
                                         ("servicenow:mid-host:mid04.example.mil:check-error", "warning")]))
        self.assertIn("points at prod.example.mil, not at this instance (dev.example.mil)", [x for x in f if x["id"].endswith(":url")][0]["summary"])

    def test_stopped_service_is_not_also_hung(self):
        f = mf.sn_mid_host_findings([{"host": "m", "reachable": True, "mids": [mid(state="Stopped", log_age=500)]}], OPTS)
        self.assertEqual(ids(f), [("servicenow:mid-host:m:snc_mid_mid01:stopped", "critical")])

    def test_well(self):
        self.assertEqual(mf.sn_mid_host_findings([{"host": "m", "reachable": True, "mids": [mid()]}], OPTS), [])
        self.assertEqual(mf.sn_mid_host_findings([{"host": "m", "reachable": True, "mids": [mid(url="https://DEV.example.mil:443/")]}], OPTS), [])


class Report(unittest.TestCase):
    def test_section_and_status(self):
        agents = mf.sn_mid_agents([agent("mid01"), agent("mid02", status="Down", host="mid02.example.mil")], {"now": NOW})
        hosts = [{"host": "mid01.example.mil", "reachable": True, "mids": [mid(), mid(service="snc_mid_extra", name="extra")]},
                 {"host": "mid09.example.mil", "reachable": False}]
        s = mf.sn_mid_section(agents, {"mid02": 4}, hosts, OPTS)
        self.assertEqual([r[0] for r in s["rows"]], ["mid01", "mid02", "extra", "-"])
        self.assertEqual(s["row_status"], ["", "critical", "warning", "critical"])
        self.assertEqual(s["rows"][0][7], "snc_mid_mid01 Running")
        self.assertEqual(s["rows"][1][6], "4")
        self.assertEqual(mf.sn_mid_status(agents, {}, hosts), "MID Servers: mid01 Up, mid02 Down; hosts checked: 1 of 2")


if __name__ == "__main__":
    unittest.main()
