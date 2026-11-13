#!/usr/bin/env python3
"""report_time_zone in the smaller reports (1.54.0): the ServiceNow nodes' tables (the cluster's heartbeats, the database's
last run), the mail relay's unused addresses (the cutoff, each address's last mail), the OCSP table of the network
performance report and the single sign-on table of the VMware health report (the next update). With a zone: the time in
it, CET or CEST by its date; "" or a zone not known here: the same text as without the option. The findings and problems
(ticket text) and the stored results stay UTC. Standard library only: python3 tests/test_report_zone_misc.py"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugins", "filter"))
import mail_relay_filters as mr  # noqa: E402
import net_perf_filters as npf  # noqa: E402
import servicenow_node_filters as nf  # noqa: E402
import vcenter_sso_filters as vf  # noqa: E402

BERLIN = "Europe/Berlin"
UNKNOWN = "Mars/Olympus_Mons"
NOV = datetime.datetime(2026, 11, 9, 12, 0, 0, tzinfo=datetime.timezone.utc).timestamp()     # CET, UTC+1
JUL = datetime.datetime(2026, 7, 9, 12, 0, 0, tzinfo=datetime.timezone.utc).timestamp()      # CEST, UTC+2


def _iso(t):
    return datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ServiceNowNodes(unittest.TestCase):
    def cluster_cell(self, now, zone=None):
        t = datetime.datetime.fromtimestamp(now - 180, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        c = nf.sn_node_cluster([{"system_id": "node01:prod_16000", "node_id": "n1", "status": "online", "most_recent_message": t,
                                 "schedulers": "any"}], {"now": now})
        o = {"cluster_read": True, "inventory": ["node01.yoursite.mil"]}
        if zone is not None:
            o["time_zone"] = zone
        sec = [s for s in nf.sn_node_sections([], c, [], o) if s["title"].startswith("Cluster")][0]
        return sec["columns"][3], sec["rows"][0][3]

    def db_growth(self, last_time, zone=None):
        G = 1073741824
        db = {"datadir": "/database/data", "datadir_under": True, "source": "information_schema", "total_bytes": 50 * G,
              "datadir_fs": {"mount": "/database", "size_b": 500 * G, "used_b": 300 * G, "free_b": 200 * G, "used_pct": 60.0},
              "history": {"change_bytes": 2 * G, "last_time": last_time, "per_day_bytes": None}}
        d = {"host": "snowdb01.yoursite.mil", "reached": True, "problems": 0, "result": {"db": db, "disk": [], "memory": {}, "fapolicyd": {}}}
        o = {} if zone is None else {"time_zone": zone}
        return [x for x in nf.sn_node_sections([], [], [d], o) if x["title"] == "Database size"][0]["rows"][0][5]

    def test_cluster_heartbeat(self):
        self.assertEqual(self.cluster_cell(NOV), ("Last heartbeat", "2026-11-09 11:57:00 UTC (3 min ago)"))
        self.assertEqual(self.cluster_cell(NOV, BERLIN), ("Last heartbeat", "2026-11-09 12:57:00 CET (3 min ago)"))
        self.assertEqual(self.cluster_cell(JUL, BERLIN)[1], "2026-07-09 13:57:00 CEST (3 min ago)")
        for z in ("", UNKNOWN, "UTC"):
            self.assertEqual(self.cluster_cell(NOV, z), self.cluster_cell(NOV))

    def test_database_last_run(self):
        self.assertEqual(self.db_growth("2026-11-08 06:00 UTC"), "+2.0 GB since the last run (2026-11-08 06:00 UTC)")
        self.assertEqual(self.db_growth("2026-11-08 06:00 UTC", BERLIN), "+2.0 GB since the last run (2026-11-08 07:00 CET)")
        self.assertEqual(self.db_growth("2026-07-08 06:00 UTC", BERLIN), "+2.0 GB since the last run (2026-07-08 08:00 CEST)")
        for z in ("", UNKNOWN):
            self.assertEqual(self.db_growth("2026-11-08 06:00 UTC", z), self.db_growth("2026-11-08 06:00 UTC"))

    def test_findings_stay_utc(self):
        # the findings go on to tickets: their text stays UTC whatever the zone
        t = datetime.datetime.fromtimestamp(NOV - 3600, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        c = nf.sn_node_cluster([{"system_id": "node01:prod_16000", "node_id": "n1", "status": "online", "most_recent_message": t,
                                 "schedulers": "any"}], {"now": NOV})
        f = nf.sn_node_findings([], c, {"cluster_read": True, "time_zone": BERLIN, "stale_minutes": 10})
        self.assertIn("(2026-11-09 11:00:00 UTC;", " ".join(x["summary"] for x in f))


class MailRelayUnused(unittest.TestCase):
    def run_it(self, now, zone=None):
        relay = {"host": "relay1", "words": ["192.0.2.10", "192.0.2.11"], "problem": "",
                 "logs": {"oldest": _iso(now - 200 * 86400), "newest": _iso(now), "sources": [{"source": "/var/log/maillog", "error": ""}],
                          "clients": {"192.0.2.10": {"sent": 3, "last_sent": _iso(now - 3600), "first_sent": _iso(now - 7200),
                                                     "connects": 3, "last_connect": _iso(now - 3600)}}}}
        o = {"days": 183, "now": now}
        if zone is not None:
            o["time_zone"] = zone
        u = mr.mail_relay_unused([relay], o)
        return u["cutoff"], dict((r[0], r[3]) for r in u["rows"])

    def test_cutoff_and_last_mail(self):
        self.assertEqual(self.run_it(NOV), ("2026-05-10 12:00 UTC", {"192.0.2.10": "2026-11-09 11:00 UTC", "192.0.2.11": "never in the logs"}))
        self.assertEqual(self.run_it(NOV, BERLIN), ("2026-05-10 14:00 CEST", {"192.0.2.10": "2026-11-09 12:00 CET",
                                                                              "192.0.2.11": "never in the logs"}))
        self.assertEqual(self.run_it(JUL, BERLIN)[1]["192.0.2.10"], "2026-07-09 13:00 CEST")
        for z in ("", UNKNOWN):
            self.assertEqual(self.run_it(NOV, z), self.run_it(NOV))


class NetPerfOcsp(unittest.TestCase):
    def table(self, nu, zone=None, stale=False):
        why = ("the answer is stale: its next update (%s) has passed" % nu) if stale else ""
        r = {"host": "h1", "targets": [], "nics": [],
             "ocsp": [{"name": "ca1", "url": "http://ocsp.example.mil", "ms": 120, "status": "critical" if stale else "ok",
                       "judged": {"status": "successful", "cert_status": "good", "next_update": nu, "why": why}}]}
        s = npf.net_perf_report_sections([r]) if zone is None else npf.net_perf_report_sections([r], None, zone)
        return [x for x in s if x["title"].startswith("OCSP")][0]["rows"][0]

    def test_next_update(self):
        self.assertEqual(self.table("2026-11-14 10:00 UTC")[4], "2026-11-14 10:00 UTC")
        self.assertEqual(self.table("2026-11-14 10:00 UTC", BERLIN)[4], "2026-11-14 11:00 CET")
        self.assertEqual(self.table("2026-07-14 10:00 UTC", BERLIN)[4], "2026-07-14 12:00 CEST")
        stale = self.table("2026-11-14 10:00 UTC", BERLIN, stale=True)
        self.assertEqual(stale[6], "the answer is stale: its next update (2026-11-14 11:00 CET) has passed")
        self.assertEqual(self.table("", BERLIN)[4], "-")
        for z in ("", UNKNOWN):
            self.assertEqual(self.table("2026-11-14 10:00 UTC", z, True), self.table("2026-11-14 10:00 UTC", stale=True))


class VcenterSsoTable(unittest.TestCase):
    def rows(self, nu, zone=None):
        sso = {"vcenters": [{"name": "vc01.example.mil", "services": [], "sts": [], "providers": []}],
               "ocsp": [{"url": "http://ocsp.example.mil", "ok": False, "mode": "request", "status": "successful", "cert_status": "good",
                         "next_update": nu, "ms": 90, "why": "the answer is stale: its next update (%s) has passed" % nu}],
               "crl": [{"url": "http://crl.example.mil/ca.crl", "ok": True, "why": "", "entries": 10, "size": 1048576, "next_update": nu, "ms": 40}]}
        o = {} if zone is None else {"time_zone": zone}
        sec = vf.vcenter_sso_doc({"title": "T", "sections": []}, sso, o)["sections"][0]
        return [r for r in sec["rows"] if not r[1].startswith("vc01")], vf.vcenter_sso_problems(sso, o)

    def test_next_update(self):
        rows, _ = self.rows("2026-11-14 10:00 UTC", BERLIN)
        self.assertEqual(rows[0][2], "successful, certificate good, next update 2026-11-14 11:00 CET")
        self.assertEqual(rows[0][6], "the answer is stale: its next update (2026-11-14 11:00 CET) has passed")
        self.assertEqual(rows[1][2], "10 certificate(s) listed, 1.0 MB, next update 2026-11-14 11:00 CET; the test certificate is not on it")
        self.assertEqual(self.rows("2026-07-14 10:00 UTC", BERLIN)[0][1][2], "10 certificate(s) listed, 1.0 MB, next update 2026-07-14 "
                                                                            "12:00 CEST; the test certificate is not on it")
        plain = self.rows("2026-11-14 10:00 UTC")
        self.assertEqual(plain[0][0][2], "successful, certificate good, next update 2026-11-14 10:00 UTC")
        for z in ("", UNKNOWN):
            self.assertEqual(self.rows("2026-11-14 10:00 UTC", z), plain)

    def test_problems_stay_utc(self):
        _, problems = self.rows("2026-11-14 10:00 UTC", BERLIN)
        self.assertIn("(2026-11-14 10:00 UTC)", problems[0]["problem"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
