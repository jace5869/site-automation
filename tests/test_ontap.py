#!/usr/bin/env python3
"""Unit tests for the NetApp ONTAP report: the collector's requests (GET only, pages, the fallback
for older ONTAP) with a fake client, and the report's helpers. Standard library plus ansible-core:
python3 tests/test_ontap.py"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "roles", "ontap_report", "library"))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
import site_ontap_collect as oc  # noqa: E402
import ontap_filters as of  # noqa: E402


class FakeClient(object):
    """Answers like ONTAP: pages of 2, HTTP 400 for the fields in `bad`, and remembers every URL."""

    def __init__(self, records, bad=(), ignore_ok=True, status=200):
        self.address, self.records, self.bad, self.ignore_ok, self.status, self.urls = "https://c1", records, set(bad), ignore_ok, status, []

    def get(self, url):
        self.urls.append(url)
        if self.status != 200:
            return self.status, json.dumps({"error": {"message": "nope", "code": "6"}})
        from urllib.parse import urlparse, parse_qs
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        ignore = q.get("ignore_unknown_fields") == "true"
        if ignore and not self.ignore_ok:
            return 400, json.dumps({"error": {"message": "Unexpected argument", "code": "262179"}})
        if not ignore and set(q.get("fields", "").split(",")) & self.bad:
            return 400, json.dumps({"error": {"message": "invalid field", "code": "262197", "target": "fields"}})
        start = int(q.get("start", 0))
        page = self.records[start:start + 2]
        body = {"records": page}
        if start + 2 < len(self.records):
            body["_links"] = {"next": {"href": "/api/x?start=%d&fields=%s" % (start + 2, q.get("fields", ""))}}
        return 200, json.dumps(body)


class Collector(unittest.TestCase):
    def test_pages_are_followed(self):
        c = FakeClient([{"n": i} for i in range(5)])
        r = oc.run_query(c, {"key": "x", "path": "/api/x", "fields": ["n"]})
        self.assertEqual([x["n"] for x in r["records"]], [0, 1, 2, 3, 4])
        self.assertEqual((r["error"], r["reduced"], r["truncated"]), ("", "", False))
        self.assertEqual(len(c.urls), 3)

    def test_cap(self):
        r = oc.run_query(FakeClient([{"n": i} for i in range(5)]), {"key": "x", "path": "/api/x", "fields": ["n"], "max": 3})
        self.assertEqual((len(r["records"]), r["truncated"]), (3, True))

    def test_unknown_field_then_ignore_unknown_fields(self):
        c = FakeClient([{"n": 1}], bad={"new"})
        r = oc.run_query(c, {"key": "x", "path": "/api/x", "fields": ["n", "new"]})
        self.assertEqual(r["records"], [{"n": 1}])
        self.assertIn("ignore_unknown_fields=true", c.urls[1])
        self.assertTrue(r["reduced"].startswith("reduced detail"))

    def test_older_ontap_falls_back_to_the_fallback_fields(self):
        c = FakeClient([{"n": 1}], bad={"new"}, ignore_ok=False)
        r = oc.run_query(c, {"key": "x", "path": "/api/x", "fields": ["n", "new"], "fallback_fields": ["n"],
                             "query": {"t": ">=1"}, "fallback_query": {}})
        self.assertEqual(len(c.urls), 3)
        self.assertIn("fields=n&", c.urls[2] + "&")
        self.assertNotIn("t=", c.urls[2])                       # the fallback query replaced the filter
        self.assertEqual(r["records"], [{"n": 1}])

    def test_cli_passthrough_never_asks_for_star(self):
        ways = oc.attempts({"key": "x", "path": "/api/private/cli/snapmirror", "fields": ["lag-time"]})
        self.assertNotIn(["*"], [w[0] for w in ways])
        self.assertEqual(oc.attempts({"key": "x", "path": "/api/x", "fields": ["a"]})[-1][0], ["*"])

    def test_errors(self):
        r = oc.run_query(FakeClient([], status=403), {"key": "x", "path": "/api/x", "fields": ["n"]})
        self.assertEqual(r["error"], "HTTP 403: nope - the account's role may not allow this")
        with self.assertRaises(oc.Unreachable):
            oc.run_query(FakeClient([], status=401), {"key": "x", "path": "/api/x", "fields": ["n"]})

    def test_collect_stops_at_an_unreachable_cluster(self):
        class Down(FakeClient):
            def get(self, url):
                raise oc.Unreachable("timed out")
        res = oc.collect(Down([]), [{"key": "a", "path": "/api/a"}, {"key": "b", "path": "/api/b"}])
        self.assertEqual((res["reachable"], res["error"], res["data"]), (False, "timed out", {}))

    def test_a_slow_table_is_that_tables_problem_not_the_clusters(self):
        class Slow(FakeClient):
            def get(self, url):
                if "/api/slow" in url:
                    raise oc.Unreachable("timed out", "timeout")
                return FakeClient.get(self, url)
        qs = [{"key": "a", "path": "/api/a"}, {"key": "slow", "path": "/api/slow"}, {"key": "b", "path": "/api/b"}]
        res = oc.collect(Slow([{"n": 1}]), qs)
        self.assertTrue(res["reachable"])
        self.assertIn("no answer within", res["data"]["slow"]["error"])
        self.assertEqual(res["data"]["b"]["records"], [{"n": 1}])
        # two slow in a row: the cluster is down after all
        qs = [{"key": "a", "path": "/api/a"}, {"key": "s1", "path": "/api/slow1"}, {"key": "s2", "path": "/api/slow2"}]
        res = oc.collect(Slow([{"n": 1}]), qs)
        self.assertFalse(res["reachable"])
        self.assertIn("ontap_timeout: 120", res["error"])

    def test_the_first_request_timing_out_means_unreachable(self):
        class Down(FakeClient):
            def get(self, url):
                raise oc.Unreachable("timed out", "timeout")
        res = oc.collect(Down([]), [{"key": "a", "path": "/api/a"}, {"key": "b", "path": "/api/b"}])
        self.assertFalse(res["reachable"])
        self.assertIn("firewall", res["error"])

    def test_a_timeout_is_asked_once_more(self):
        import socket
        calls = []

        class Resp(object):
            def getcode(self):
                return 200

            def read(self):
                return b'{"records": []}'

        class Opener(object):
            def open(self, req, timeout=None):
                calls.append(1)
                if len(calls) == 1:
                    raise oc.URLError(socket.timeout("timed out"))
                return Resp()
        c = oc.Client("https://c1", "u", "p", None, 5, opener=Opener())
        self.assertEqual(c.get("/api/cluster")[0], 200)
        self.assertEqual(len(calls), 2)

    def test_why_a_cluster_cannot_be_read(self):
        import socket
        import ssl
        self.assertEqual(oc.kind_of(oc.URLError(socket.gaierror(-2, "Name or service not known"))), "dns")
        self.assertEqual(oc.kind_of(oc.URLError(ConnectionRefusedError(111, "Connection refused"))), "refused")
        self.assertEqual(oc.kind_of(oc.URLError(ssl.SSLError(1, "[SSL: UNSAFE_LEGACY_RENEGOTIATION_DISABLED] unsafe legacy renegotiation disabled"))), "tls")
        self.assertIn("ontap_tls_legacy: true", oc.explain("[SSL: UNSAFE_LEGACY_RENEGOTIATION_DISABLED] x", "tls", "c1", 30))
        self.assertIn("ontap_ca_path", oc.explain("[SSL: CERTIFICATE_VERIFY_FAILED] x", "tls", "c1", 30))
        self.assertIn("-k skips", oc.explain("[SSL: CERTIFICATE_VERIFY_FAILED] x", "tls", "c1", 30))
        self.assertIn("IP address", oc.explain("Name or service not known", "dns", "c1", 30))
        old = dict(os.environ)
        try:
            os.environ.pop("no_proxy", None)
            os.environ.pop("NO_PROXY", None)
            os.environ["https_proxy"] = "http://proxy.example.mil:3128"
            self.assertIn("add the cluster to no_proxy", oc.explain("timed out", "timeout", "c1.example.mil", 30))
            os.environ["no_proxy"] = ".example.mil"
            self.assertNotIn("proxy", oc.explain("timed out", "timeout", "c1.example.mil", 30))
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_get_only(self):
        sent = []

        class Opener(object):
            def open(self, req, timeout=None):
                sent.append(req.get_method())
                raise oc.URLError("x")
        c = oc.Client("https://c1", "u", "p", None, 5, opener=Opener())
        with self.assertRaises(oc.Unreachable):
            c.get("/api/cluster")
        self.assertEqual(sent, ["GET"])

    def test_cli_keys_and_url(self):
        self.assertEqual(oc.underscore_keys([{"lag-time": "PT1H", "a": {"b-c": 1}}]), [{"lag_time": "PT1H", "a": {"b_c": 1}}])
        self.assertEqual(oc.build_url("c1", "/api/x", ["a", "b.c"], {"s": "error|alert"}, 10),
                         "https://c1/api/x?s=error|alert&fields=a,b.c&max_records=10")


class Helpers(unittest.TestCase):
    def test_uptime_and_durations(self):
        self.assertEqual(of.uptime_text(93784), "1d 02:03")
        self.assertEqual(of.uptime_text(None), "?")
        for v, secs in (("PT8H35M42S", 30942), ("P2DT1H", 176400), ("P1W", 604800), ("1:02:03", 3723), ("2:01:00:00", 176400),
                        ("1d 2h", 93600), (3600, 3600), ("-", None), ("", None)):
            self.assertEqual(of.duration_seconds(v), secs, v)
        self.assertEqual(of.duration_text(176400), "2d 01h 00m")

    def test_sizes_and_levels(self):
        self.assertEqual(of.human_bytes(1536), "1.5 KB")
        self.assertEqual(of.human_bytes(10 * 1024 ** 4), "10.0 TB")
        self.assertEqual(of.human_bytes(None), "?")
        self.assertEqual(of.pct(9, 10), 90.0)
        self.assertIsNone(of.pct(1, 0))
        self.assertEqual([of.level(x, 85, 90) for x in (80, 85, 90, None)], ["ok", "warning", "critical", "unknown"])
        self.assertEqual(of.level(5, 60, 14, higher_is_worse=False), "critical")
        self.assertEqual(of.short_version("NetApp Release 9.14.1P3: Mon Jan 01 2026"), "9.14.1P3")

    def test_times(self):
        self.assertEqual(of.time_text("2026-10-07T10:22:33-04:00"), "2026-10-07 14:22")
        self.assertEqual(of.time_text("2026-10-07T10:22:33Z"), "2026-10-07 10:22")
        self.assertIsNotNone(of.parse_time(1790000000))


class Report(unittest.TestCase):
    def test_unreachable_and_empty(self):
        r = of.ontap_report({"read_at": "2026-10-07T12:00:00Z", "clusters": [
            {"address": "c9.example.mil", "reachable": False, "error": "timed out", "data": {}}]}, {})
        self.assertEqual(r["report"]["status"], "critical")
        self.assertEqual(r["clusters"], [{"cluster": "c9.example.mil", "reachable": False, "error": "timed out"}])
        first = r["report"]["sections"][0]
        self.assertEqual(first["rows"][0][3], "UNREACHABLE")
        self.assertEqual(first["rows"][0][-1], "timed out")
        self.assertEqual(r["critical"], 1)


    @staticmethod
    def one(volumes, alerts=(), ems=()):
        GB = 1024 ** 3

        def vol(name, size_gb, used_pct, state="online"):
            return {"name": name, "svm": {"name": "svm1"}, "aggregates": [{"name": "aggr1"}], "state": state, "type": "rw",
                    "space": {"size": size_gb * GB, "used": size_gb * GB * used_pct // 100, "available": size_gb * GB * (100 - used_pct) // 100,
                              "percent_used": used_pct}, "is_svm_root": False}
        data = {"cluster": {"records": [{"name": "c1"}]}, "volumes": {"records": [vol(*v) for v in volumes]},
                "alerts": {"records": list(alerts)}, "ems": {"records": list(ems)}}
        return {"read_at": "2026-10-07T12:00:00Z", "clusters": [{"address": "c1.example.mil", "reachable": True, "data": data}]}

    def test_small_volumes_not_flagged_for_used_pct(self):
        col = self.one([("vol0_small", 10, 97), ("vol_49", 49, 92), ("vol_big", 500, 95), ("vol_50", 50, 88), ("vol_off", 5, 10, "offline"),
                        ("vol_restr", 20, 0, "restricted"), ("vol_big_off", 100, 0, "offline")])
        r = of.ontap_report(col, {"volume_min_gb": 50})
        sec = [x for x in r["report"]["sections"] if x["title"].startswith("Volumes")][0]
        self.assertEqual(sorted(row[2] for row in sec["rows"]), ["vol_50", "vol_big", "vol_big_off"])  # small ones offline too: left out
        off = [row for row in sec["rows"] if row[2] == "vol_big_off"][0]
        self.assertEqual((off[4], off[6:9]), ("OFFLINE", ["100.0 GB", "-", "-"]))                   # not online: its size, no usage
        self.assertEqual([row for row in sec["rows"] if row[2] == "vol_big"][0][6:9], ["500.0 GB", "25.0 GB", "95%"])
        self.assertIn("Volumes under 50 GB are left out (2 at or above 85%, 2 not online).", sec["text"])
        self.assertEqual(sorted(p["object"] for p in r["problems"] if p["area"] == "capacity"),
                         ["volume svm1 / vol_50", "volume svm1 / vol_big", "volume svm1 / vol_big_off"])
        r0 = of.ontap_report(col, {"volume_min_gb": 0})                                                 # 0 = every volume
        self.assertEqual(sum(1 for p in r0["problems"] if p["area"] == "capacity"), 7)
        self.assertEqual(sum(1 for p in of.ontap_report(col)["problems"] if p["area"] == "capacity"), 3)   # the default: 50 GB

    def test_alerts_and_events_last(self):
        alert = {"node": "n1", "perceived_severity": "major", "alert_id": "DualPathToDiskShelf_Alert", "probable_cause": "one path"}
        ems = {"node": {"name": "n1"}, "message": {"name": "callhome.battery.low", "severity": "error"}, "time": "2026-10-07T11:00:00Z",
               "log_message": "battery low"}
        titles = [x["title"] for x in of.ontap_report(self.one([("v", 100, 10)], [alert], [ems]))["report"]["sections"]]
        self.assertEqual(titles[-2], "Health alerts")
        self.assertTrue(titles[-1].startswith("Error events"), titles[-1])
        self.assertLess(titles.index("System health"), titles.index("Health alerts"))
        self.assertGreater(titles.index("Health alerts"), titles.index("Certificates expiring"))
        self.assertTrue(titles[2].startswith("Volumes "), titles[:4])                      # after Clusters and Nodes
        self.assertEqual(titles[:2], ["Clusters", "Nodes"])


    def test_ldap_events_left_out_when_ldap_is_not_configured(self):
        ems = [{"node": {"name": "n1"}, "message": {"name": n, "severity": "error"}, "time": "2026-10-07T11:00:00Z", "log_message": n}
               for n in ("secd.ldap.noServers", "secd.LDAP.slowServer", "callhome.battery.low")]
        def events(ldap, opts=None):
            col = self.one([("v", 100, 10)], ems=ems)
            if ldap is not None:
                col["clusters"][0]["data"]["ldap"] = ldap
            sec = [x for x in of.ontap_report(col, opts or {})["report"]["sections"] if x["title"].startswith("Error events")][0]
            return sorted(r[3] for r in sec.get("rows", [])), sec.get("lines", [])
        rows, lines = events({"records": []})
        self.assertEqual(rows, ["callhome.battery.low"])
        self.assertIn("c1: 2 LDAP event(s) left out - no SVM has an LDAP client configured", lines)
        self.assertEqual(len(events({"records": [{"svm": {"name": "svm1"}}]})[0]), 3)            # LDAP configured: kept
        self.assertEqual(len(events({"error": "HTTP 403", "records": []})[0]), 3)                # not readable: kept
        self.assertEqual(len(events(None)[0]), 3)                                                # not read at all: kept
        self.assertEqual(events({"records": [{"svm": {"name": "svm1"}}]}, {"ems_ignore_ldap": "true"})[0], ["callhome.battery.low"])
        self.assertEqual(len(events({"records": []}, {"ems_ignore_ldap": "false"})[0]), 3)
        rows, lines = events(None, {"ems_ignore": [r"^callhome\.battery"]})
        self.assertNotIn("callhome.battery.low", rows)
        self.assertIn("c1: 1 event(s) left out by ontap_ems_ignore", lines)


if __name__ == "__main__":
    unittest.main()
