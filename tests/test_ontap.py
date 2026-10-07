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


if __name__ == "__main__":
    unittest.main()
