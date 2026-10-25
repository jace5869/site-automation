#!/usr/bin/env python3
"""Unit tests for the network performance report: ping's summary and the interface counters
(roles/net_perf/library/site_net_perf.py), the judging, the trend, iperf3 and the tables (plugins/filter/net_perf_filters.py).

    python tests/test_net_perf.py
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "roles", "net_perf", "library"))
sys.path.insert(0, os.path.join(ROOT, "plugins", "filter"))
import site_net_perf as m        # noqa: E402
import net_perf_filters as nf    # noqa: E402

IPUTILS = """PING gw (192.0.2.1) 56(84) bytes of data.

--- gw ping statistics ---
20 packets transmitted, 19 received, 5% packet loss, time 3805ms
rtt min/avg/max/mdev = 0.312/0.402/0.611/0.071 ms
"""
ERRORS = "20 packets transmitted, 0 received, +20 errors, 100% packet loss, time 3810ms\n"


def raw(targets, history=None, nics=None, ocsp=None):
    return {"targets": targets, "history": history or [], "nics": nics or [], "ocsp": ocsp or [], "time": "2026-10-25T00:00:00Z"}


def tgt(name, loss=0.0, avg=1.0, **kw):
    return dict({"name": name, "host": name.lower() + ".example.mil", "port": 0, "loss_pct": loss, "rtt_avg": avg, "rtt_max": avg, "rtt_mdev": 0.1}, **kw)


def ids(res):
    return [(f["id"], f["severity"]) for f in res["findings"]]


class Ping(unittest.TestCase):
    def test_iputils_summary(self):
        r = m.parse_ping(IPUTILS)
        self.assertEqual((r["sent"], r["received"], r["loss_pct"], r["rtt_avg"], r["rtt_mdev"]), (20, 19, 5.0, 0.402, 0.071))

    def test_no_answer(self):
        r = m.parse_ping(ERRORS)
        self.assertEqual((r["sent"], r["loss_pct"], r["rtt_avg"]), (20, 100.0, None))


class Counters(unittest.TestCase):
    def test_deltas_and_reset(self):
        last = {"time": 0, "counters": {"eth0": {"rx_dropped": 10, "tx_dropped": 0, "rx_errors": 1, "tx_errors": 0}}}
        d = m.nic_deltas({"eth0": {"rx_dropped": 110, "tx_dropped": 5, "rx_errors": 3, "tx_errors": 0}, "eth1": {}}, last, 7200)
        e0 = [x for x in d if x["name"] == "eth0"][0]
        self.assertEqual((e0["hours"], e0["rx_dropped"], e0["tx_dropped"], e0["errors"]), (2.0, 100, 5, 2))
        self.assertTrue([x for x in d if x["name"] == "eth1"][0]["first"])
        r = m.nic_deltas({"eth0": {"rx_dropped": 1}}, last, 7200)[0]
        self.assertTrue(r["first"] and r.get("reset"), "a counter that went down starts over")


class Judge(unittest.TestCase):
    def test_thresholds_and_own_thresholds(self):
        r = nf.net_perf_result(raw([tgt("A", loss=2), tgt("B", avg=60), tgt("C", avg=250), tgt("D", avg=250), tgt("E", loss=100, avg=None)]), "h",
                               targets=[{"name": "D", "rtt_warn_ms": 300}])
        self.assertEqual(ids(r), [("netperf:loss:A", "warning"), ("netperf:rtt:B", "warning"), ("netperf:rtt:C", "critical"),
                                  ("netperf:loss:E", "critical")])
        self.assertIn("does not answer ping", r["findings"][-1]["summary"])

    def test_slower_than_usual(self):
        hist = [{"rtt": {"A": 1.0}}, {"rtt": {"A": 1.2}}, {"rtt": {"A": 0.9}}]
        self.assertEqual(ids(nf.net_perf_result(raw([tgt("A", avg=8.0)], hist), "h")), [("netperf:slower:A", "warning")])
        self.assertEqual(ids(nf.net_perf_result(raw([tgt("A", avg=3.0)], hist), "h")), [], "3 ms against 1 ms: under slower_min_ms")
        self.assertEqual(nf.net_perf_result(raw([tgt("A", avg=8.0)], hist), "h")["targets"][0]["usual"], 1.0)

    def test_tcp(self):
        r = nf.net_perf_result(raw([tgt("A", port=443, tcp_ok=0, tcp_fail=5, tcp_why="refused"), tgt("B", port=443, tcp_ok=4, tcp_fail=1)]), "h")
        self.assertEqual(ids(r), [("netperf:tcp:A", "critical"), ("netperf:tcp:B", "warning")])

    def test_drops(self):
        r = nf.net_perf_result(raw([], nics=[{"name": "eth0", "hours": 2.0, "rx_dropped": 300, "tx_dropped": 0, "errors": 0},
                                             {"name": "eth1", "hours": 2.0, "rx_dropped": 10, "tx_dropped": 0, "errors": 30},
                                             {"name": "eth2", "first": True}]), "h")
        self.assertEqual(ids(r), [("netperf:drops:eth0", "warning"), ("netperf:drops:eth1", "warning")])

    def test_ocsp_plain_and_failed(self):
        r = nf.net_perf_result(raw([], ocsp=[{"name": "P", "url": "http://ocsp.example.mil", "mode": "reach", "code": 405, "ms": 30},
                                             {"name": "Q", "url": "http://ocsp.example.mil", "mode": "reach", "code": 0, "why": "no answer: refused"},
                                             {"name": "S", "url": "http://ocsp.example.mil", "mode": "reach", "code": 200, "ms": 5000}]), "h")
        self.assertEqual(ids(r), [("netperf:ocsp:Q", "critical"), ("netperf:ocsp-slow:S", "warning")])


class Iperf(unittest.TestCase):
    def test_results(self):
        ok = {"stdout": json.dumps({"end": {"sum_sent": {"bits_per_second": 9.4e8, "retransmits": 3}, "sum_received": {"bits_per_second": 9.3e8}}})}
        low = {"stdout": json.dumps({"end": {"sum_sent": {"bits_per_second": 1e8}, "sum_received": {"bits_per_second": 1e8}}})}
        bad = {"stdout": json.dumps({"error": "unable to connect to server: Connection refused"})}
        pairs = [{"server": "a", "client": "b", "min_mbps": 900}, {"server": "a", "client": "c", "min_mbps": 900}, {"server": "a", "client": "d"},
                 {"server": "a", "client": "e"}]
        r = nf.net_perf_iperf([ok, low, bad, {"skipped": True}], pairs)
        self.assertEqual([(x["mbps"], x["status"]) for x in r], [(930.0, "ok"), (100.0, "warning"), (None, "warning"), (None, "ok")])
        self.assertIn("Connection refused", r[2]["why"])
        self.assertEqual(nf.net_perf_iperf_finding(r[1])["id"], "netperf:iperf:a")


class Sections(unittest.TestCase):
    def test_worst_first_and_boxes(self):
        a = nf.net_perf_result(raw([tgt("A"), tgt("B", loss=50)]), "h1")
        s = nf.net_perf_report_sections([a])
        self.assertEqual(s[0]["rows"][0][1], "B")
        self.assertEqual(s[0]["boxes"][1]["value"], 1)
        json.dumps(s)


if __name__ == "__main__":
    unittest.main(verbosity=1)
