#!/usr/bin/env python3
"""Unit tests for the log analysis: plugins/filter/log_filters.py (settings and profiles, the window, reading
what the collectors returned, signatures, grouping across hosts, ranking, caps, evidence, the prompt, the
report, the names ACT masks) and the pure parts of roles/vmware_vm/library/site_vmware_logs.py (line
parsing, the window, reading a log back through a stand-in DiagnosticManager).
Needs ansible-core importable (the module), not pyVmomi: python3 tests/test_log_analysis.py"""
import importlib.util
import json
import os
import re
import sys
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "plugins", "filter"))
sys.path.insert(0, os.path.join(ROOT, "roles", "vmware_vm", "library"))
_spec = importlib.util.spec_from_file_location("ansible.module_utils.site_vcenters", os.path.join(
    ROOT, "roles", "vmware_vm", "module_utils", "site_vcenters.py"))
site_vcenters = importlib.util.module_from_spec(_spec)
sys.modules["ansible.module_utils.site_vcenters"] = site_vcenters
_spec.loader.exec_module(site_vcenters)
import log_filters as lf  # noqa: E402
import site_vmware_logs as vl  # noqa: E402
import vmware_alarm_filters as va  # noqa: E402

SHIPPED = yaml.safe_load(open(os.path.join(ROOT, "roles", "log_analysis", "vars", "main.yml")))["_log_analysis_profiles"]
DEFAULTS = yaml.safe_load(open(os.path.join(ROOT, "roles", "log_analysis", "defaults", "main.yml")))
NOW = 1792742400.0          # 2026-10-23T08:00:00Z


def cfg(**kw):
    c = {k[len("log_analysis_"):]: v for k, v in DEFAULTS.items() if k.startswith("log_analysis_")}
    c.update(kw)
    return c


class Settings(unittest.TestCase):
    def test_base_only(self):
        s = lf.log_settings(cfg(), SHIPPED, "linux")
        self.assertEqual(s["profiles"], [])
        self.assertEqual(s["files"], [])
        self.assertEqual(s["win_logs"], ["System", "Application"])
        self.assertTrue(s["match"].startswith("error|fail"))
        self.assertIn("Event 10016:", s["ignore"])

    def test_a_profile_adds_its_files_and_units(self):
        s = lf.log_settings(cfg(profiles=["nginx", "sssd"]), SHIPPED, "linux")
        self.assertEqual(s["profiles"], ["nginx", "sssd"])
        self.assertIn("/var/log/nginx/error.log", s["files"])
        self.assertIn("/var/log/sssd/sssd.log", s["files"])
        self.assertEqual(s["units"], ["nginx", "sssd"])

    def test_extra_adds_never_replaces(self):
        s = lf.log_settings(cfg(profiles=["nginx"], files=["/var/log/messages"], files_extra=["/opt/app/logs/*.log"],
                                match_extra=["SEVERE"], ignore_extra=["sshd.*closed"], win_logs_extra="DNS Server, Directory Service"),
                            SHIPPED, "linux")
        self.assertEqual(s["files"], ["/var/log/messages", "/var/log/nginx/error.log", "/opt/app/logs/*.log"])
        self.assertTrue(s["match"].endswith("|SEVERE"))
        self.assertEqual(s["ignore"][-1], "sshd.*closed")
        self.assertIn("Event 10016:", s["ignore"])
        self.assertEqual(s["win_logs"], ["System", "Application", "DNS Server", "Directory Service"])

    def test_vcsa_switches_its_profile_on_and_unknown_is_named(self):
        s = lf.log_settings(cfg(profiles="nope"), SHIPPED, "vcsa")
        self.assertEqual(s["profiles"], ["vcsa"])
        self.assertEqual(s["unknown_profiles"], ["nope"])
        self.assertIn("/var/log/vmware/vpxd/vpxd.log", s["files"])

    def test_profiles_extra_adds_and_replaces(self):
        s = lf.log_settings(cfg(profiles=["stigman", "nginx"],
                                profiles_extra={"stigman": {"units": ["stigman-api"]}, "nginx": {"files": ["/srv/nginx/error.log"]}}),
                            SHIPPED, "linux")
        self.assertEqual(s["units"], ["stigman-api"])
        self.assertEqual(s["files"], ["/srv/nginx/error.log"])

    def test_windows_ids_and_mid_server(self):
        s = lf.log_settings(cfg(profiles=["mid_server"], win_event_ids=[4625, "System:7031", "4740"]), SHIPPED, "windows")
        self.assertIn({"log": "", "id": 4625}, s["win_event_ids"])
        self.assertIn({"log": "", "id": 4740}, s["win_event_ids"])
        self.assertEqual(sum(1 for x in s["win_event_ids"] if x == {"log": "System", "id": 7031}), 1)
        self.assertTrue(any("agent0.log" in f for f in s["win_files"]))
        self.assertIn("SEVERE", s["match"])
        re.compile(s["match"])

    def test_every_shipped_profile_is_well_formed(self):
        for name, p in SHIPPED.items():
            self.assertTrue(set(p) <= set(lf.PROFILE_KEYS), name)
            s = lf.log_settings(cfg(profiles=[name]), SHIPPED, "linux")
            re.compile(s["match"])
            for x in s["ignore"]:
                re.compile(x)


class Window(unittest.TestCase):
    def test_hours(self):
        w = lf.log_window(24, "", "", NOW)
        self.assertEqual((w["since_min"], w["until_min"], w["begin"], w["end"]), (1440, 0, "2026-10-22T08:00:00Z", ""))
        self.assertEqual(w["label"], "the last 24 h")

    def test_since_until(self):
        w = lf.log_window(24, "2026-10-23 05:00", "2026-10-23 06:30", NOW)
        self.assertEqual((w["since_min"], w["until_min"]), (180, 90))
        self.assertEqual((w["begin"], w["end"]), ("2026-10-23T05:00:00Z", "2026-10-23T06:30:00Z"))
        self.assertEqual(w["label"], "2026-10-23 05:00 to 2026-10-23 06:30 UTC")
        self.assertEqual(lf.log_window(24, "2026-10-23 07:00", "", NOW)["since_min"], 60)

    def test_wrong(self):
        self.assertIn("must look like", lf.log_window(24, "yesterday", "", NOW)["error"])
        self.assertIn("is not after", lf.log_window(24, "2026-10-23 06:00", "2026-10-23 05:00", NOW)["error"])
        self.assertIn("more than 0", lf.log_window(0, "", "", NOW)["error"])


def journal(prio, msg, ts, ident="sshd", cursor=""):
    return json.dumps({"PRIORITY": str(prio), "MESSAGE": msg, "SYSLOG_IDENTIFIER": ident,
                       "__REALTIME_TIMESTAMP": str(int(ts * 1e6)), "__CURSOR": cursor or "c%s" % ts})


class Readers(unittest.TestCase):
    def test_linux_sections(self):
        out = "\n".join([
            "@@@ journal ",
            journal(3, "error: kex_exchange_identification: Connection reset by 192.0.2.10 port 4711", NOW - 60),
            "@@@ journal units",
            journal(3, "error: kex_exchange_identification: Connection reset by 192.0.2.10 port 4711", NOW - 60),   # the same line again
            journal(4, "Backend is offline", NOW - 120, "sssd_be"),
            "@@@ container svc_app/web",
            "2026-10-23T07:59:00.123456789+00:00 ERROR could not connect to db01:5432",
            "@@@ file /var/log/app.log",
            "2026-10-23 07:58:00 FATAL out of memory",
            "plain line with a timeout and no time",
            "@@@ error not readable",
            "@@@ skipped /var/log/old.log unchanged in the window",
        ])
        r = lf.log_linux_entries(out, "web01")
        self.assertEqual([e["source"] for e in r["entries"]], ["journal", "journal", "container svc_app/web", "/var/log/app.log",
                                                                "/var/log/app.log"])
        self.assertEqual(r["entries"][0]["level"], "error")
        self.assertEqual(r["entries"][0]["time"], "2026-10-23T07:59:00Z")
        self.assertEqual(r["entries"][1]["level"], "warning")
        self.assertEqual(r["entries"][2]["time"], "2026-10-23T07:59:00Z")
        self.assertEqual(r["entries"][2]["app"], "svc_app/web")
        self.assertEqual(r["entries"][3]["level"], "critical")
        self.assertEqual(r["entries"][3]["time"], "2026-10-23T07:58:00")         # a text log's own time: local, no Z
        self.assertEqual(r["sources"][3]["error"], "not readable")
        self.assertEqual(r["sources"][-1], {"name": "/var/log/old.log", "lines": 0, "error": "", "skipped": "unchanged in the window"})

    def test_windows(self):
        res = {"findings": [], "facts": {"entries": [
            {"source": "System", "time": "2026-10-23T07:00:00Z", "level": "error", "app": "Service Control Manager",
             "message": "Event 7031: The snc_mid service terminated unexpectedly."},
            {"source": "D:\\ServiceNow\\mid1\\agent\\logs\\agent0.log", "time": "", "level": "", "app": "",
             "message": "10/23/26 07:01:02 (123) Worker SEVERE *** ERROR *** connection refused"}],
            "sources": [{"name": "System", "lines": 1, "error": ""}]}}
        r = lf.log_win_entries(res, "mid01")
        self.assertEqual([e["level"] for e in r["entries"]], ["error", "error"])
        self.assertEqual(r["sources"][0]["name"], "System")


def host(name, entries, source="linux", **kw):
    h = {"host": name, "source": source, "reachable": True, "error": "", "sources": [{"name": "journal", "lines": len(entries), "error": ""}],
         "entries": [dict(e, host=name) for e in entries]}
    h.update(kw)
    return h


def e(msg, level="error", t="2026-10-23T07:00:00Z", source="journal", app="sshd"):
    return {"source": source, "time": t, "level": level, "app": app, "message": msg}


class Condense(unittest.TestCase):
    def test_signature(self):
        a = lf.log_signature("2026-10-23T07:00:01Z Connection reset by 192.0.2.10 port 4711 [preauth] pid=1234")
        b = lf.log_signature("Connection reset by 198.51.100.7 port 50022 [preauth] pid=99")
        self.assertEqual(a, b)
        self.assertEqual(lf.log_signature('open "/srv/a b" failed: 0x1f, uuid 123e4567-e89b-12d3-a456-426614174000'),
                         "open <q> failed: <hex>, uuid <uuid>")

    def test_grouping_across_hosts_and_ranking(self):
        hosts = [host("web01.example.mil", [e("Connection reset by 192.0.2.10 port 1"), e("Connection reset by 192.0.2.11 port 2"),
                                            e("disk almost full", "warning", app="df")]),
                 host("web02.example.mil", [e("Connection reset by 192.0.2.12 port 3", t="2026-10-23T07:30:00Z"),
                                            e("kernel panic - not syncing", "critical", app="kernel")]),
                 host("web03.example.mil", [], reachable=False, error="unreachable: ssh refused")]
        d = lf.log_analysis(hosts, {"max_items": 2})
        self.assertEqual(d["patterns"], 3)
        self.assertEqual((d["critical"], d["error"], d["warning"], d["not_read"]), (1, 1, 1, 1))
        self.assertEqual([p["severity"] for p in d["all"]], ["critical", "error", "warning"])
        conn = d["all"][1]
        self.assertEqual((conn["count"], conn["hosts"]), (3, ["web01.example.mil", "web02.example.mil"]))
        self.assertEqual((conn["first"], conn["last"]), ("2026-10-23T07:00:00Z", "2026-10-23T07:30:00Z"))
        self.assertEqual(len(conn["examples"]), 3)
        self.assertEqual([p["id"] for p in d["problems"]], ["P1", "P2"])
        self.assertEqual([p["id"] for p in d["not_analyzed"]], ["P3"])
        self.assertEqual(next(h for h in d["hosts"] if h["host"] == "web01.example.mil")["patterns"], 2)
        self.assertNotIn("entries", d["hosts"][0])

    def test_ignore_window_and_min_count(self):
        hosts = [host("a", [e("Event 10016: DCOM permission"), e("sshd closed by user"), e("real failure"),
                            e("old failure", t="2026-10-20T00:00:00Z"), e("local-time failure", t="2026-10-20T00:00:00")],
                      ignore=["closed by user"])]
        d = lf.log_analysis(hosts, {"ignore": ["Event 10016:"], "begin": "2026-10-22T08:00:00Z", "end": "2026-10-23T08:00:00Z"})
        self.assertEqual(sorted(p["problem"] for p in d["all"]), ["local-time failure", "real failure"])
        self.assertEqual((d["ignored"], d["outside"]), (2, 1))
        self.assertEqual(lf.log_analysis(hosts, {"min_count": 2})["patterns"], 0)

    def test_rotations_are_one_source(self):
        for a, b in (("/var/log/messages-20261020", "/var/log/messages"), ("vpxd-123.log", "vpxd.log"), ("agent0.log.1", "agent0.log")):
            self.assertEqual(lf._source_key(a), lf._source_key(b), a)


class ActAndReport(unittest.TestCase):
    def setUp(self):
        self.d = lf.log_analysis([host("web01.example.mil", [e("Connection reset by 192.0.2.10"), e("kernel panic", "critical", app="kernel")]),
                                  host("web02.example.mil", [e("Connection reset by 192.0.2.12")]),
                                  host("web03.example.mil", [], reachable=False, error="ssh refused")], {"max_items": 1})

    def test_evidence_and_task(self):
        ev = lf.log_evidence(self.d, self.d["problems"], {"window": "the last 24 h", "question": "slow logins since 07:00"})
        self.assertIn("THE OPERATIONS TEAM ASKS: slow logins since 07:00", ev)
        self.assertIn("P1 [CRITICAL] journal kernel on web01.example.mil", ev)
        self.assertIn("web03.example.mil: Linux, 0 line(s) - NOT READ: ssh refused", ev)
        self.assertNotIn("P2", ev)
        self.assertIn("evidence cut at 50 characters", lf.log_evidence(self.d, self.d["problems"], {"max_chars": 50}))
        task = lf.log_act_task(self.d["problems"], {"what": "3 host(s) of web", "window": "the last 24 h", "question": "slow logins"})
        self.assertIn('The team asks: "slow logins"', task)
        self.assertIn("BEGIN_ACT_ANALYSIS", task)
        self.assertIn("(P1)", task)
        self.assertNotIn("The team asks", lf.log_act_task(self.d["problems"], {}))

    def test_answer_reads_back_with_the_shared_parser(self):
        answer = ("x\nBEGIN_ACT_ANALYSIS\n" + json.dumps({"overall": "Fix the kernel first.", "items": [
            {"id": "P1", "cause": "A driver bug.", "evidence": "kernel panic", "fix": "Update the driver.", "confidence": 85}]})
            + "\nEND_ACT_ANALYSIS")
        parsed = va.act_analysis_parse(answer, self.d["problems"])
        doc = lf.log_report(self.d, parsed, {"act": True, "ran": True, "what": "web", "window": "the last 24 h",
                                             "question": "slow logins", "settings": ["Profile nginx: web01."], "max_items": 1})
        self.assertEqual(doc["title"], "Log analysis: likely causes and fixes for 1 pattern(s), 1 host(s) not read")
        titles = [s["title"] for s in doc["sections"]]
        self.assertEqual(titles, ["Not read (1)", "Your question", "What to do first", "Likely causes and fixes",
                                  "Patterns (2), most severe first", "Hosts read (2)", "What was read"])
        act = doc["sections"][3]
        self.assertEqual(act["rows"][0][-1], "85% high")
        pats = doc["sections"][4]
        self.assertEqual(pats["columns"], ["#", "Level", "Hosts", "Log", "Lines", "First seen", "Last seen", "Example"])
        self.assertEqual(pats["rows"][1][:5], ["P2", "ERROR", "web01.example.mil, web02.example.mil", "journal sshd", 2])
        self.assertIn("P2 to P2 (over log_analysis_max_items, 1) were not given to ACT.", pats["text"])
        self.assertIn("Profile nginx: web01.", doc["sections"][-1]["lines"])
        self.assertEqual(doc["status"], "critical")

    def test_no_act_and_act_failed(self):
        doc = lf.log_report(self.d, {}, {"act": False})
        self.assertEqual(doc["title"], "Log analysis: 2 pattern(s) in the logs of 2 host(s), 1 host(s) not read")
        self.assertNotIn("Likely causes and fixes", [s["title"] for s in doc["sections"]])
        self.assertIn("ACT: not asked (log_analysis_act: false).", doc["sections"][-1]["lines"])
        doc = lf.log_report(self.d, {}, {"act": True, "ran": False, "reason": "no key"})
        self.assertTrue(doc["title"].startswith("Log analysis: ACT did not run - 2 pattern(s) not analyzed"))
        self.assertEqual(doc["sections"][1]["lines"][0], "no key")

    def test_clean(self):
        d = lf.log_analysis([host("a.example.mil", [])])
        doc = lf.log_report(d, {}, {"act": True})
        self.assertEqual(doc["title"], "Log analysis: no warning or error in the logs of 1 host(s)")
        self.assertEqual(doc["status"], "ok")
        self.assertIn("ACT: nothing to analyze.", doc["sections"][-1]["lines"])

    def test_names(self):
        n = lf.log_names(self.d, ["PRODCL", "db01"])
        for x in ("web01.example.mil", "web01", "web03", "PRODCL", "db01"):
            self.assertIn(x, n)


class VmwareLogs(unittest.TestCase):
    def test_parse_line(self):
        self.assertEqual(vl.parse_line("2026-10-23T07:00:01.123Z Wa(164) Hostd[2099803]: [Originator@6876 sub=Vimsvc] Task failed"),
                         ("2026-10-23T07:00:01Z", "warning", "Hostd", "[Originator@6876 sub=Vimsvc] Task failed"))
        self.assertEqual(vl.parse_line("2026-10-23T07:00:01.123Z error vpxd[06891] [Originator@6876 sub=drmLogger] boom")[:3],
                         ("2026-10-23T07:00:01Z", "error", "vpxd"))
        self.assertEqual(vl.parse_line("2026-10-23T07:00:01.123Z cpu12:2097152)WARNING: NMP: path dead")[:3],
                         ("2026-10-23T07:00:01Z", "", "vmkernel"))
        self.assertEqual(vl.parse_line("2026-10-23T09:00:01+02:00 info hostd[1] x")[0], "2026-10-23T07:00:01Z")
        self.assertEqual(vl.parse_line("   continuation"), ("", "", "", "continuation"))

    def test_keep_lines(self):
        lines = ["2026-10-22T06:00:00.000Z Er(163) Hostd[1]: too old",
                 "2026-10-23T07:00:00.000Z In(166) Hostd[1]: all fine",
                 "2026-10-23T07:00:01.000Z In(166) Hostd[1]: connection timeout to 192.0.2.1",
                 "  more of the line above",
                 "2026-10-23T07:00:02.000Z Er(163) Hostd[1]: something broke",
                 "2026-10-23T07:59:00.000Z Er(163) Hostd[1]: after the end"]
        rx = re.compile(vl.DEFAULT_MATCH, re.I)
        kept = vl.keep_lines(lines, "2026-10-23T00:00:00Z", rx, "esx01", "hostd", 500, 400, "2026-10-23T07:30:00Z")
        self.assertEqual([k["message"] for k in kept], ["connection timeout to 192.0.2.1 more of the line above", "something broke"])
        self.assertEqual(kept[1]["level"], "error")
        self.assertEqual(len(vl.keep_lines(lines, "", rx, "esx01", "hostd", 1)), 1)

    def test_log_keys(self):
        self.assertEqual(vl.log_keys(["vpxd:vpxd-12.log", "vpxd-profiler:x.log", "hostd"], ["vpxd", "vmkernel"]),
                         [("vpxd", "vpxd:vpxd-12.log"), ("vmkernel", None)])

    def test_read_back_in_chunks(self):
        log = ["line %d" % i for i in range(1, 2501)]

        class Header(object):
            def __init__(self, start, end, text):
                self.lineStart, self.lineEnd, self.lineText = start, end, text
        calls = []

        def browse(key, start, lines):
            calls.append((start, lines))
            if start > len(log):
                return Header(len(log), len(log), [])
            return Header(start, min(len(log), start + lines - 1), log[start - 1:start - 1 + lines])
        got = vl.read_back(browse, "hostd", 1500)
        self.assertEqual(got, log[1000:])
        self.assertEqual(calls, [(999999999, 1), (1001, 1000), (2001, 500)])
        self.assertEqual(vl.read_back(lambda k, s, n: Header(0, 0, []), "x", 10), [])

    def test_host_wanted(self):
        self.assertTrue(vl.host_wanted("ESX01.example.mil", ["esx0*"]))
        self.assertFalse(vl.host_wanted("esx11.example.mil", ["esx0*"]))
        self.assertTrue(vl.host_wanted("x", []))


if __name__ == "__main__":
    unittest.main()
