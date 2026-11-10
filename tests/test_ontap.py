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
        self.assertEqual(first["rows"][0][2], "UNREACHABLE")                       # the address is the cluster's cell (1.12.0)
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
        self.assertEqual(titles[-3:-1], ["Health alerts", "Health alerts: cause and what to do"])   # two tables (1.12.0)
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


class Fit(unittest.TestCase):
    """1.12.0: the wide tables made to fit a laptop's screen without dropping anything - columns merged into one cell,
    SnapMirror and Health alerts in two tables each, short headings with their full names, and the columns that wrap."""
    TB = 1024 ** 4

    def report(self, **data):
        base = {"cluster": {"records": [{"name": "c1", "version": {"full": "NetApp Release 9.14.1"}}]},
                "nodes": {"records": [{"name": "c1-01", "state": "up"}]}}
        base.update(dict((k, {"records": v}) for k, v in data.items()))
        r = of.ontap_report({"read_at": "2026-10-07T12:00:00Z", "clusters": [{"address": "c1.example.mil", "reachable": True, "data": base}]})
        return dict((s["title"], s) for s in r["report"]["sections"])

    def test_every_table(self):
        aggr = {"name": "aggr1", "node": {"name": "c1-02"}, "home_node": {"name": "c1-01"}, "state": "online",
                "space": {"block_storage": {"size": 4 * self.TB, "used": 3 * self.TB, "available": self.TB}},
                "block_storage": {"primary": {"raid_type": "raid_dp", "disk_count": 24}}}
        secs = self.report(aggregates=[aggr])
        for s in secs.values():
            if "columns" in s:
                self.assertLessEqual(len(s["columns"]), 10, s["title"])
                self.assertEqual(len(s.get("kinds", s["columns"])), len(s["columns"]), s["title"])
                self.assertEqual(len(s.get("column_titles", s["columns"])), len(s["columns"]), s["title"])
                for name in ("Cluster", "SVM"):                                   # long hyphenated names break at their hyphens
                    if name in s["columns"]:
                        self.assertEqual(s["kinds"][s["columns"].index(name)], "wrap", s["title"])
        c = secs["Clusters"]
        self.assertEqual(c["columns"], ["Cluster", "ONTAP", "Health", "Nodes up", "Space used", "NTP servers", "Location", "Notes"])
        self.assertEqual(c["rows"][0][0], "c1\nc1.example.mil")                  # the address under the name
        self.assertEqual(c["column_titles"][4], "Space used on its aggregates")
        a = secs["Aggregates"]
        self.assertEqual(a["columns"], ["Cluster", "Aggregate", "Node", "State", "Space", "Used %", "RAID"])
        self.assertEqual(a["rows"][0][2:], ["c1-02\nhome: c1-01", "ONLINE", "3.0 TB used of 4.0 TB\n1.0 TB available", "75%", "raid_dp, 24 disks"])
        self.assertEqual(a["cell_status"][0][2], "warning")                       # away from home
        self.assertEqual(secs["Aggregates not on their home node"]["rows"], [["c1", "aggr1", "c1-02", "c1-01"]])

    def test_snapmirror_two_tables(self):
        def sm(dst, healthy, lag, state="snapmirrored", status="idle"):
            return {"source_path": "src:" + dst, "destination_path": "dst:" + dst, "state": state, "status": status, "healthy": healthy,
                    "lag_time": lag, "last_transfer_end_timestamp": "2026-10-07T10:00:00Z", "total_progress": 1024 ** 3,
                    "progress_last_updated": "2026-10-07T11:00:00Z", "policy": "MirrorAllSnapshots",
                    "unhealthy_reason": "" if healthy else "Transfer failed."}
        secs = self.report(snapmirror_cli=[sm("v_lag", True, "P1DT1H"), sm("v_bad", False, "PT1H"), sm("v_move", True, "PT1H", status="transferring")])
        s, x = secs["SnapMirror"], secs["SnapMirror transfers"]
        self.assertEqual(s["columns"], ["Cluster", "Source", "Destination", "State", "Healthy", "Lag", "Reason / last error"])
        self.assertEqual(x["columns"], ["Cluster", "Destination", "Last transfer (UTC)", "Progress", "Progress updated", "Policy"])
        self.assertEqual([r[2] for r in s["rows"]], [r[1] for r in x["rows"]])  # one row each, the same order
        self.assertEqual([r[2] for r in s["rows"]], ["dst:v_bad", "dst:v_lag", "dst:v_move"])
        self.assertEqual(s["rows"][2][3], "snapmirrored\ntransferring")          # the mirror state, the transfer status under it
        self.assertEqual(x["rows"][0][2:], ["2026-10-07 10:00", "1.0 GB", "2026-10-07 11:00", "MirrorAllSnapshots"])
        self.assertNotIn("SnapMirror transfers", self.report(snapmirror_cli=[]))

    def test_health_alerts_two_tables(self):
        al = [{"node": "c1-01", "perceived_severity": "minor", "alert_id": "A_minor", "probable_cause": "pc1", "corrective_actions": "ca1"},
              {"node": "c1-02", "perceived_severity": "major", "alert_id": "B_major", "probable_cause": "pc2", "corrective_actions": "ca2"}]
        secs = self.report(alerts=al)
        h, w = secs["Health alerts"], secs["Health alerts: cause and what to do"]
        self.assertEqual(h["columns"], ["Cluster", "Node", "Severity", "Subsystem", "Alert", "Resource", "Since (UTC)"])
        self.assertEqual([r[4] for r in h["rows"]], ["B_major", "A_minor"])     # the major one first
        self.assertEqual(w["rows"], [["c1", "c1-02", "B_major", "pc2", "ca2"], ["c1", "c1-01", "A_minor", "pc1", "ca1"]])

    def test_lif_port(self):
        lif = {"svm": {"name": "svm1"}, "name": "lif1", "enabled": True, "state": "up", "services": ["data_nfs", "data_cifs"],
               "location": {"is_home": False, "node": {"name": "c1-02"}, "port": {"name": "e0b"}, "home_node": {"name": "c1-01"},
                            "home_port": {"name": "e0a"}}, "ip": {"address": "192.0.2.10"}}
        l = self.report(lifs=[lif])["Network interfaces (LIFs): down, not at home, or disabled"]
        self.assertEqual(l["columns"], ["Cluster", "SVM", "LIF", "Type", "State", "Port", "Address", "Services"])
        self.assertEqual(l["rows"][0][5:], ["c1-02:e0b\nhome: c1-01:e0a", "192.0.2.10", "data_nfs, data_cifs"])
        self.assertEqual((l["cell_status"][0][5], l["row_status"][0]), ("warning", "warning"))


class Cifs(unittest.TestCase):
    """1.6.0 / 1.6.1: ONTAP lists every DC of the domain and marks those it has not needed "undetermined" - its normal
    state, even for every DC (a real run: CIFS works with all of them undetermined): not contacted, a count, never a
    finding. Not reachable (ONTAP tried and failed) is amber; red only when it tried every DC it lists and reached none.
    Every server is listed in "CIFS domain controllers"."""
    @staticmethod
    def report(servers, enabled=True, opts=None):
        data = {"cluster": {"records": [{"name": "c1"}]},
                "cifs": {"records": [{"svm": {"name": "svm1"}, "name": "CIFS1", "enabled": enabled, "ad_domain": {"fqdn": "corp.example.mil"}}]},
                "cifs_domains": {"records": [{"svm": {"name": "svm1"}, "discovered_servers": servers}]}}
        r = of.ontap_report({"read_at": "2026-10-07T12:00:00Z", "clusters": [{"address": "c1.example.mil", "reachable": True, "data": data}]}, opts or {})
        sec = [x for x in r["report"]["sections"] if x["title"] == "CIFS (SMB) servers"][0]
        dcs = [x for x in r["report"]["sections"] if x["title"] == "CIFS domain controllers"][0]
        return sec, [(p["severity"], p["text"]) for p in r["problems"] if p["area"] == "cifs"], dcs

    @staticmethod
    def dc(name, state, pref="adequate", node="c1-01", typ="ms_dc", ip=None):
        return {"server_name": name, "server_ip": ip or "192.0.2.%d" % (10 + int(name[-1]) if name[-1].isdigit() else 99), "server_type": typ,
                "state": state, "preference": pref, "node": {"name": node}}

    def test_all_ok(self):
        sec, p, _ = self.report([self.dc("dc1", "ok", "preferred"), self.dc("dc2", "ok")])
        self.assertEqual((sec["rows"][0][5:], sec["cell_status"][0][5]), (["2 OK", ""], "ok"))
        self.assertNotIn("lines", sec)
        self.assertEqual(p, [])

    def test_ok_and_not_contacted(self):
        sec, p, _ = self.report([self.dc("dc1", "ok", "preferred")] + [self.dc("dc%d" % i, "undetermined") for i in range(2, 6)]
                                + [self.dc("dc6", "ok", "favored")])
        self.assertEqual(sec["rows"][0][5:], ["2 OK \u00b7 4 not contacted", ""])
        self.assertEqual((sec["cell_status"][0][5], sec["row_status"][0], p), ("ok", "", []))

    def test_all_not_contacted_is_information(self):
        """The real run: every DC undetermined, CIFS working - blue, no finding, the commands to look and to probe again."""
        sec, p, _ = self.report([self.dc("dc%d" % i, "undetermined") for i in range(1, 6)])
        self.assertEqual(sec["rows"][0][5:7], ["5 not contacted", ""])
        self.assertEqual((sec["cell_status"][0][5], sec["row_status"][0], p), ("info", "info", []))
        self.assertNotIn("status", sec)
        self.assertEqual(sec["lines"], ["c1 / svm1: not contacted - vserver cifs domain discovered-servers show -vserver svm1; to make ONTAP "
                                        "probe them again: vserver cifs domain discovered-servers reset-servers -vserver svm1 (a change: run it "
                                        "in a quiet window)"])

    def test_none_listed_is_information(self):
        sec, p, dcs = self.report([])
        self.assertEqual((sec["rows"][0][5], sec["cell_status"][0][5], p), ("none listed", "info", []))
        self.assertIn("c1 / svm1: no DC listed - vserver cifs domain discovered-servers discovery-mode show -vserver svm1", sec["lines"][0])
        self.assertEqual(dcs["text"], dcs["text"].strip())
        self.assertNotIn("rows", dcs)

    def test_unavailable_is_amber(self):
        sec, p, _ = self.report([self.dc("dc1", "ok"), self.dc("dc2", "unavailable"), self.dc("dc3", "undetermined")])
        self.assertEqual(sec["rows"][0][5:7], ["1 OK \u00b7 1 not contacted \u00b7 1 not reachable", "dc2 (unavailable)"])
        self.assertEqual(p, [("warning", "domain controllers not reachable: dc2 (unavailable)")])

    def test_unavailable_and_not_contacted_none_ok_is_amber(self):
        """ONTAP has not tried them all: amber, not red."""
        _, p, _ = self.report([self.dc("dc1", "unavailable", "preferred"), self.dc("dc2", "undetermined")])
        self.assertEqual(p, [("warning", "domain controllers not reachable: dc1 (unavailable, a preferred DC)")])

    def test_tried_every_dc_and_reached_none_is_red(self):
        sec, p, _ = self.report([self.dc("dc1", "unavailable"), self.dc("dc2", "unavailable")])
        self.assertEqual((sec["rows"][0][5], sec["cell_status"][0][5]), ("2 not reachable", "critical"))
        self.assertEqual(p, [("critical", "ONTAP reports no DC it is connected to - it tried them and reached none: dc1 (unavailable); dc2 (unavailable)")])

    def test_disabled_is_red(self):
        sec, p, _ = self.report([self.dc("dc1", "undetermined")], enabled=False)
        self.assertEqual((sec["rows"][0][4], sec["cell_status"][0][4]), ("NO", "critical"))
        self.assertEqual(p, [("critical", "the CIFS server is DISABLED")])

    def test_the_list_rows_and_sort(self):
        """One row per DC and type: not reachable first, then OK, then not contacted, then by name; the state as a chip."""
        _, _, dcs = self.report([self.dc("dc3", "undetermined"), self.dc("dc1", "ok", "preferred"), self.dc("dc2", "unavailable"),
                                 self.dc("dc1", "ok", "preferred", typ="kerberos"), self.dc("dc4", "undetermined", typ="ms_ldap")])
        self.assertEqual(dcs["columns"], ["Cluster", "SVM", "DC", "Address", "Type", "Preference", "State", "Nodes"])
        self.assertEqual([r[2:] for r in dcs["rows"]],
                         [["dc2", "192.0.2.12", "DC", "adequate", "unavailable", "all"],
                          ["dc1", "192.0.2.11", "DC", "preferred", "ok", "all"],
                          ["dc1", "192.0.2.11", "KDC", "preferred", "ok", "all"],
                          ["dc3", "192.0.2.13", "DC", "adequate", "not contacted", "all"],
                          ["dc4", "192.0.2.14", "LDAP", "adequate", "not contacted", "all"]])
        self.assertEqual([c[6] for c in dcs["cell_status"]], ["warning", "ok", "ok", "info", "info"])
        self.assertEqual(dcs["row_status"], ["warning", "", "", "", ""])
        self.assertNotIn("collapsed", dcs)
        self.assertIn("Not contacted (undetermined): ONTAP has not needed it", dcs["text"])

    def test_one_dc_on_several_nodes(self):
        """ONTAP lists a DC once per node: one row; the nodes named when they differ."""
        sec, p, dcs = self.report([self.dc("dc1", "ok", node="c1-01"), self.dc("dc1", "undetermined", node="c1-02"),
                                   self.dc("dc2", "ok", node="c1-01"), self.dc("dc2", "unavailable", node="c1-02"),
                                   self.dc("dc3", "undetermined", node="c1-01"), self.dc("dc3", "undetermined", node="c1-02")])
        self.assertEqual(sec["rows"][0][5:7], ["1 OK \u00b7 1 not contacted \u00b7 1 not reachable", "dc2 (unavailable - c1-01 ok, c1-02 unavailable)"])
        self.assertEqual([(r[2], r[6], r[7]) for r in dcs["rows"]],
                         [("dc2", "unavailable", "c1-01 ok, c1-02 unavailable"), ("dc1", "ok", "c1-01 ok, c1-02 not contacted"),
                          ("dc3", "not contacted", "all")])
        self.assertEqual(p[0][0], "warning")

    def test_a_long_list_is_collapsed_and_not_cut_at_max_rows(self):
        _, _, dcs = self.report([self.dc("dc%02d" % i, "undetermined") for i in range(1, 61)], opts={"max_rows": 50})
        self.assertTrue(dcs["collapsed"])
        self.assertEqual(len(dcs["rows"]), 60)

    def test_no_preference_field(self):
        servers = [{"server_name": "dc1", "server_type": "ms_dc", "state": "ok"}, {"server_name": "dc2", "server_type": "ms_dc", "state": "slow"}]
        sec, p, dcs = self.report(servers)
        self.assertEqual(p, [("warning", "domain controllers not reachable: dc2 (slow)")])
        self.assertEqual(dcs["rows"][0][3:6], ["", "DC", "-"])


if __name__ == "__main__":
    unittest.main()
