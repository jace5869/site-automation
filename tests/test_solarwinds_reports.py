#!/usr/bin/env python3
"""1.97.0: SolarWinds in the health checks, troubleshoot and the VMware alarms reports - plugins/module_utils/site_host_swis.py
(the hosts as the report database's SolarWinds snapshot has them, Log Analyzer's syslog in one capped query) and
plugins/filter/solarwinds_report_filters.py (the sections, tiles, the email's lines, ACT's evidence). The snapshot's
reader is a stand-in with site_swis_db's shapes; the real database and the playbooks: tests/solarwinds/run_swreport_test.sh.
Placeholders only.
  python tests/test_solarwinds_reports.py"""
import copy
import datetime
import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "plugins", "module_utils"), os.path.join(ROOT, "plugins", "filter"), os.path.join(ROOT, "tests"),
                os.path.join(ROOT, "tests", "solarwinds")]
import site_redact
import site_host_swis as hs  # noqa: E402
import solarwinds_report_filters as swr  # noqa: E402
import site_filters as sf  # noqa: E402
import vmware_alarm_filters as va  # noqa: E402
import reads  # noqa: E402
from test_filters import VmwareAlarms  # noqa: E402

T = lambda s: datetime.datetime.strptime(s, "%Y-%m-%d %H:%M")    # noqa: E731


class Snap(object):
    """site_swis_db as site_host_swis reads it: one snapshot (snap 7) with web01 (linked to sw-acc-02 Gi1/0/14), dc01 in two
    domains, db01 by its IP, esx01 in VIM."""

    def __init__(self, age=360, orion="orion.example.mil:17774", latest="ok"):
        self.counts = {"alerts": 1, "events": 4}
        self.core, self.notes = {}, {}
        self.age, self.orion, self.kind, self.calls = age, orion, latest, []

    def begin_read(self, conn):
        self.calls.append("begin")

    def end_read(self, conn):
        self.calls.append("end")

    def latest(self, conn, fresh=True):
        self.calls.append(("latest", fresh))
        if self.kind == "1146":
            raise Exception("(1146, \"Table 'site_reports.sw_snapshot' doesn't exist\")")
        if self.kind == "none":
            return None
        return {"snap_id": 7, "read_at": T("2026-11-21 10:40"), "age_seconds": self.age, "orion": self.orion, "core": self.core,
                "not_read": [], **self.counts}

    def last_failure(self, conn):
        return (T("2026-11-21 10:50"), "login refused")

    def part_note(self, snap, part):
        return self.notes.get(part)

    def nodes_for_hosts(self, conn, sid, fulls, shorts, ips):
        keys = shorts + ips
        self.calls.append(("nodes", tuple(keys)))
        rows = {"web01": [{"node_id": 101, "name": "web01", "name_key": "web01", "dns": "web01.example.mil", "ip": "192.0.2.21",
                           "status_code": 1, "status": "Up", "status_text": "", "unmanaged": False, "last_poll": T("2026-11-21 10:38"),
                           "cpu_pct": 12, "mem_pct": 40, "matched_by": "name"}],
                "dc01": [{"node_id": 111, "name": "dc01.a.example.mil", "name_key": "dc01", "dns": "", "ip": "192.0.2.31", "status_code": 1,
                          "status": "Up", "status_text": "", "unmanaged": False, "last_poll": None, "cpu_pct": None, "mem_pct": None},
                         {"node_id": 112, "name": "dc01.b.example.mil", "name_key": "dc01", "dns": "", "ip": "192.0.2.32", "status_code": 1,
                          "status": "Up", "status_text": "", "unmanaged": False, "last_poll": None, "cpu_pct": None, "mem_pct": None}],
                "192.0.2.22": [{"node_id": 102, "name": "srv-db-a", "name_key": "srv-db-a", "dns": "", "ip": "192.0.2.22", "status_code": 14,
                                "status": "Warning", "status_text": "", "unmanaged": False, "last_poll": None, "cpu_pct": -2, "mem_pct": None}],
                "esx01": [{"node_id": 201, "name": "esx01", "name_key": "esx01", "dns": "esx01.example.mil", "ip": "192.0.2.11",
                           "status_code": 1, "status": "Up", "status_text": "", "unmanaged": False, "last_poll": None, "cpu_pct": 1, "mem_pct": 1}]}
        return [r for k in keys for r in rows.get(k, [])]

    def vim_nodes(self, conn, sid, ids):
        return []

    def apps_for(self, conn, sid, ids, own_tx=True):
        self.calls.append(("apps", own_tx))
        return {101: [reads.sam_app("Apache HTTP Server", "Down", ["httpd process"])]}

    def alerts_for(self, conn, sid, ids):
        self.calls.append(("alerts", tuple(ids)))
        return {101: [{"name": "Volume /var over 95 %", "severity": 2, "triggered_at": T("2026-11-21 10:02"), "message": "x",
                       "entity_type": "Orion.Volumes", "entity_caption": "web01 /var", "acknowledged": 0}]}

    def volumes_for(self, conn, sid, ids):
        return {101: [reads.vol("/var", 96.0)]}

    def node_details(self, conn, sid, ids):
        return {101: {"vendor": "Dell", "model": "R750", "os_version": "Red Hat Enterprise Linux 9", "kind": "", "location": "",
                      "last_boot": None}}

    def ports_for(self, conn, sid, ids):
        self.calls.append(("ports", tuple(ids)))
        return {101: [reads.port()]} if 101 in ids else {}

    def events_for(self, conn, start, end, ids, ports=(), limit=2000, orion=None):
        """As site_swis_db.events_for: the nodes' own events, a switch's only of the ports asked (the SQL does it)."""
        self.calls.append(("events", tuple(ids), tuple(ports), orion, round((end - start).total_seconds() / 3600)))
        e = lambda i, t, node, kind, typ, oid, msg: {"event_id": i, "event_time": T(t), "node_id": node, "net_object_type": typ,  # noqa: E731
                                                       "net_object_id": oid, "event_type": 0, "type_name": kind, "message": msg}
        evs = [e(1, "2026-11-21 09:56", 501, "Interface Down", "I", 7014, "Gi1/0/14 is down"),
               e(2, "2026-11-21 09:57", 501, "Interface Up", "I", 7999, "Gi1/0/9 is up (another port)"),
               e(3, "2026-11-21 09:58", 501, "Node Rebooted", "N", 501, "sw-acc-02 rebooted (the switch itself)"),
               e(4, "2026-11-21 10:02", 101, "Alert Triggered", "N", 101, "Volume /var over 95 %")]
        out = []
        for x in evs:
            if x["node_id"] in ids:
                out.append(dict(x, port=None))
            elif x["net_object_type"] == "I" and (x["node_id"], x["net_object_id"]) in set(tuple(p) for p in ports):
                out.append(dict(x, port=[x["node_id"], x["net_object_id"]]))
        return sorted(out, key=lambda x: x["event_time"], reverse=True)

    def vim_for(self, conn, sid, names):
        self.calls.append(("vim", tuple(tuple(sorted(n.items())) for n in names)))
        return dict((hs.sdb.vim_key(n["kind"], n.get("vcenter"), n["name"]),
                     {"kind": "host", "name": n["name"], "status": "Warning", "status_code": 3, "connection_state": "connected",
                      "power_state": "poweredOn", "cluster": "cluster1", "node_id": 201, "vcenter": "vc01.example.mil", "why": ""}
                     if n["name"].startswith("esx01") and n["kind"] == "host" else None) for n in names)


NOW = "2026-11-21T10:46:00Z"
HOSTS = [{"host": "web01.example.mil"}, {"host": "dc01.a.example.mil"}, {"host": "db01", "ip": "192.0.2.22"}, {"host": "gone01"}]


class Read1970(unittest.TestCase):
    def test_hosts_matched_by_full_name_short_name_and_ip(self):
        s = Snap()
        r = hs.read(HOSTS, {"max_age": 20, "now": NOW}, object(), s)
        self.assertTrue(r["ok"], r["why"])
        by = dict((h["host"], h) for h in r["hosts"])
        self.assertEqual((by["web01.example.mil"]["node"]["node_id"], by["web01.example.mil"]["matched"]), (101, "name"))
        self.assertEqual(by["dc01.a.example.mil"]["node"]["node_id"], 111)      # its domain's node, not dc01.b
        self.assertEqual((by["db01"]["node"]["node_id"], by["db01"]["matched"]), (102, "ip"))
        self.assertEqual(by["db01"]["node"]["status"], "Critical")             # Orion's 14: said as it is
        self.assertIsNone(by["db01"]["node"]["cpu"])                           # -2: not polled
        self.assertIsNone(by["gone01"]["node"])
        self.assertEqual((r["read_at"], r["age_min"]), ("2026-11-21 10:40", 6))
        self.assertEqual(s.calls[0], "begin")                                  # one read-only moment of the database
        self.assertEqual(s.calls[-1], "end")
        self.assertIn(("latest", False), s.calls)
        w = by["web01.example.mil"]
        self.assertEqual((w["alerts"][0]["name"], w["alerts"][0]["since"]), ("Volume /var over 95 %", "2026-11-21 10:02"))
        self.assertEqual(w["node"]["vendor"], "Dell")
        self.assertEqual(w["ports"], [])                                       # health: no switch port, no events read
        self.assertFalse([c for c in s.calls if c[0] in ("ports", "events")])

    def test_troubleshoot_reads_the_port_and_only_its_events(self):
        s = Snap()
        r = hs.read([{"host": "web01.example.mil"}], {"max_age": 20, "mode": "troubleshoot", "event_hours": 24, "now": NOW}, object(), s)
        w = r["hosts"][0]
        self.assertEqual([p["port"] for p in w["ports"]], ["Gi1/0/14"])
        self.assertEqual([(e["message"], e["on"]) for e in w["events"]],          # newest first; the switch's: its port's only
                         [("Volume /var over 95 %", "node"), ("Gi1/0/14 is down", "port")])
        ev = [c for c in s.calls if c[0] == "events"][0]
        self.assertEqual(ev[1:], ((101,), ((501, 7014),), "orion.example.mil:17774", 24))
        self.assertEqual(w["events"][1]["switch"], "sw-acc-02")
        self.assertTrue(r["events"])

    def test_vcenter_mode_asks_vim(self):
        r = hs.read([{"host": "esx01.example.mil", "kind": "host"}], {"max_age": 20, "mode": "vcenter", "now": NOW}, object(), Snap())
        self.assertEqual(r["vim"][hs.sdb.vim_key("host", "", "esx01.example.mil")]["status"], "Warning")
        self.assertEqual((r["hosts"][0]["kind"], r["hosts"][0]["vim_status"]), ("host", "Warning"))

    def test_a_snapshot_that_cannot_be_used_is_one_reason(self):
        cases = [(Snap(age=45 * 60), {"max_age": 20}, "the SolarWinds snapshot is 45 min old (of 2026-11-21 10:40 UTC), older than "
                                                         "solarwinds_db_max_age (20 min). The newer runs failed: login refused"),
                 (Snap(latest="none"), {"max_age": 20}, "the report database has no whole SolarWinds snapshot yet (the last run: login refused)"),
                 (Snap(latest="1146"), {"max_age": 20}, "the report database has no SolarWinds snapshot yet (the job SolarWinds - snapshot has not run)"),
                 (Snap(), {"max_age": 0}, "solarwinds_db_max_age is 0: the snapshot is never used"),
                 (Snap(), {"max_age": 20, "orion": "https://orion2.example.mil:17774"},
                  "the SolarWinds snapshot is of another SolarWinds (orion.example.mil:17774, not orion2.example.mil:17774)")]
        for s, o, why in cases:
            r = hs.read(HOSTS, dict(o, now=NOW), object(), s)
            self.assertEqual((r["ok"], r["why"], r["hosts"]), (False, [why], []), why)
            self.assertEqual(s.calls[-1], "end")

    def test_no_database_no_read(self):
        r = hs.read(HOSTS, {}, None, Snap())
        self.assertEqual((r["ok"], r["hosts"]), (False, []))


class Client(object):
    """SolarWinds for syslog_live: Metadata.Entity, Metadata.Property and the one Log Analyzer query."""

    def __init__(self, entity=True, cols=("NodeID", "DateTime", "Level", "Message", "LogEntryID"), rows=None, fail=None):
        self.entity, self.cols, self.rows, self.fail, self.q = entity, cols, rows or [], fail, []
        self.calls = 0

    def query(self, q, params=None):
        self.q.append((q, params))
        self.calls += 1
        if "Metadata.Entity" in q:
            return [{"FullName": hs.OLM}] if self.entity else []
        if "Metadata.Property" in q:
            return [{"EntityName": hs.OLM, "Name": c, "IsNavigable": False} for c in self.cols]
        if self.fail:
            raise self.fail
        cap = int(re.search(r"TOP (\d+)", q).group(1))
        return self.rows[:cap]


def entry(node, t, msg, level="Error"):
    return {"NodeID": node, "DateTime": "2026-11-21T%s:00.000Z" % t, "Level": level, "Message": msg}


class Syslog1970(unittest.TestCase):
    def test_one_capped_query_counted_by_message_and_node(self):
        rows = ([entry(101, "10:%02d" % (30 - i), "httpd[%d]: AH00052: child pid %d exit signal Segmentation fault (11)" % (4000 + i, 4000 + i))
                 for i in range(5)] + [entry(101, "10:12", "kernel: EXT4-fs warning (device dm-3): Directory index full")] * 2
                + [entry(102, "08:44", "mariadbd: Aborted connection 812")])
        c = Client(rows=rows)
        r = hs.syslog_live(c, [101, 102, 101], {"hours": 24, "max_rows": 5000, "now": NOW})
        self.assertTrue(r["ok"], r["why"])
        q, p = c.q[-1]
        self.assertEqual(q, "SELECT TOP 5000 NodeID, DateTime, Level, Message FROM Orion.OLM.LogEntry WHERE NodeID IN @ids AND "
                            "DateTime >= @since AND Level IN @levels ORDER BY DateTime DESC")
        self.assertEqual(p, {"ids": [101, 102], "since": "2026-11-20T10:46:00", "levels": ["Emergency", "Alert", "Critical", "Error"]})
        self.assertEqual(c.calls, 3)                                          # the entity, its columns, the one query
        n = r["by_node"][101]
        self.assertEqual(n["count"], 7)
        self.assertEqual([(t["count"], t["last"]) for t in n["top"]], [(5, "2026-11-21 10:30"), (2, "2026-11-21 10:12")])
        self.assertEqual(n["top"][0]["message"], "httpd[4000]: AH00052: child pid 4000 exit signal Segmentation fault (11)")   # the newest
        self.assertFalse(r["capped"])

    def test_many_hosts_in_chunks_capped_in_all(self):
        c = Client(rows=[entry(101, "10:00", "x %d" % i) for i in range(9)])
        r = hs.syslog_live(c, list(range(1, 1201)), {"max_rows": 20, "now": NOW})
        qs = [q for q in c.q if "OLM.LogEntry WHERE" in q[0]]
        self.assertEqual([(len(p["ids"]), q.split()[2]) for q, p in qs], [(500, "20"), (500, "11"), (200, "2")])
        self.assertEqual((r["capped"], r["by_node"][101]["count"]), (True, 20))

    def test_utc_time_column_first(self):
        c = Client(cols=("NodeID", "DateTime", "DateTimeUtc", "Level", "Message"))
        hs.syslog_live(c, [101], {"now": NOW})
        self.assertIn("DateTimeUtc >= @since", c.q[-1][0])

    def test_capped_said(self):
        r = hs.syslog_live(Client(rows=[entry(101, "10:00", "x %d" % i) for i in range(9)]), [101], {"max_rows": 4, "now": NOW})
        self.assertEqual((r["capped"], r["by_node"][101]["count"]), (True, 4))

    def test_levels_as_written_by_the_site(self):
        c = Client()
        hs.syslog_live(c, [101], {"levels": ["error", " critical ", ""], "now": NOW})
        self.assertEqual(c.q[-1][1]["levels"], ["Error", "Critical"])

    def test_no_log_analyzer_no_column_no_answer(self):
        self.assertEqual(hs.syslog_live(Client(entity=False), [1], {})["why"], "this SolarWinds has no Log Analyzer (no Orion.OLM.LogEntry)")
        self.assertEqual(hs.syslog_live(Client(cols=("NodeID", "DateTime", "Message")), [1], {})["why"],
                         "Log Analyzer's Orion.OLM.LogEntry has no Level column on this SolarWinds")
        r = hs.syslog_live(Client(fail=RuntimeError("boom")), [1], {})
        self.assertEqual((r["ok"], r["why"]), (False, "Log Analyzer could not be read: a RuntimeError"))
        self.assertTrue(hs.syslog_live(Client(), [], {})["ok"])            # no node: nothing asked
        c = Client()
        hs.syslog_live(c, [], {})
        self.assertEqual(c.q, [])

    def test_the_key_makes_numbers_alike(self):
        self.assertEqual(hs.norm("child pid 4121 exit 0x1f deadbeef12"), hs.norm("child  pid 977 exit 0xa0 cafebabe99"))


def texts(x):
    """Every string a reader sees in a section (captions, lines, cells, glance texts)."""
    if isinstance(x, dict):
        for k, v in x.items():
            if k in ("status", "row_status", "cell_status", "kinds", "columns"):
                continue
            for t in texts(v):
                yield t
    elif isinstance(x, list):
        for v in x:
            for t in texts(v):
                yield t
    elif isinstance(x, str):
        yield x


class Health1970(unittest.TestCase):
    def view(self, **o):
        return swr.swr_health(reads.get("health"), reads.findings(), o)

    def test_three_sections_right_under_findings(self):
        v = self.view()
        self.assertEqual([s["title"] for s in v["web_sections"]],
                         ["SolarWinds says (6 hosts)", "SolarWinds alerts, our checks are green (1)", "Not in SolarWinds (2)"])
        rep = sf.findings_report(reads.findings(), "Health check", ["gone01.example.mil"], ["disk"], v["web_sections"])
        titles = [s["title"] for s in rep["sections"]]
        i = titles.index("Findings")
        self.assertEqual(titles[i + 1:i + 4], ["SolarWinds says (6 hosts)", "SolarWinds alerts, our checks are green (1)", "Not in SolarWinds (2)"])
        self.assertEqual(titles[i + 4], "Did not report")
        self.assertIn({"label": "SolarWinds alerts", "value": 3, "status": "critical", "note": "on 2 hosts", "counted": False}, rep["summary"])
        self.assertEqual(rep["status"], "critical")                          # the job's own (web01's disk), never SolarWinds'

    def test_the_table(self):
        s = self.view()["web_sections"][0]
        self.assertEqual(s["columns"], ["", "Host", "Node", "Alerts", "Apps down", "Volumes over 90 %", "Syslog errors (24 h)", "Our checks"])
        self.assertEqual([r[1] for r in s["rows"]], ["web01", "app02", "db01", "lab07", "mon01", "app03"])    # worst first
        self.assertEqual(s["rows"][0], ["", "web01", "Up", "2", "1 of 3", "/var 96 %", "41", "2 findings"])
        self.assertEqual(s["cell_status"][0], ["", "", "ok", "critical", "critical", "critical", "warning", "critical"])
        self.assertEqual(s["rows"][3], ["", "lab07", "Missing", "-", "-", "-", "-", "green"])
        self.assertEqual(s["row_status"], ["critical", "warning", "warning", "warning", "warning", ""])
        det = s["details"][0]
        self.assertEqual([d["columns"][0] for d in det], ["Severity", "SAM application", "Volume", "Syslog errors (24 h)"])
        self.assertEqual(det[0]["rows"][0], ["Critical", "Volume /var over 95 %", "web01 /var", "10:02"])
        self.assertEqual(det[1]["rows"][0], ["Apache HTTP Server", "Down", "httpd process, HTTPS port 443"])
        self.assertEqual(det[2]["rows"][0], ["/var", "96 %", "2.0 GB"])
        self.assertEqual(len(det[3]["rows"]), 3)
        self.assertEqual(s["details"][5], [])                                # app03: nothing to open
        self.assertIn("From the SolarWinds snapshot of 2026-11-21 10:40 UTC (6 min old).", s["text"])

    def test_disagree_and_not_in_solarwinds(self):
        v = self.view()
        g = v["web_sections"][1]["glance"]
        self.assertEqual(g, [{"status": "warning", "label": "Warning", "name": "app02",
                              "text": "High response time: HTTPS 443 over 2 s, since 07:15. Our checks found nothing. Worth a look."}])
        self.assertEqual(v["web_sections"][2]["lines"], ["lab07.example.mil", "mon01.example.mil"])
        self.assertEqual(v["web_sections"][2]["status"], "warning")          # the user's choice: a warning, not information

    def test_skip_hosts(self):
        v = self.view(skip_hosts=["lab*"])
        self.assertEqual(v["web_sections"][2]["lines"], ["mon01.example.mil"])
        s = v["web_sections"][0]
        self.assertEqual(s["title"], "SolarWinds says (5 hosts)")
        self.assertNotIn("lab07", [r[1] for r in s["rows"]])
        self.assertEqual(s["note"], "1 host of solarwinds_report_skip_hosts left out.")
        self.assertEqual(s["text"], "One row per host: open it for its alerts, applications, volumes and top syslog messages. "
                                    "From the SolarWinds snapshot of 2026-11-21 10:40 UTC (6 min old).")
        self.assertEqual(len(self.view(skip_hosts=["*.example.mil"])["web_sections"]), 2)    # nobody missing: no section

    def test_volume_levels(self):
        s = self.view(volume_pct=40, volume_critical_pct=99)["web_sections"][0]
        self.assertEqual(s["columns"][5], "Volumes over 40 %")
        self.assertEqual(s["rows"][0][5], "/var 96 %, / 41 %")
        self.assertEqual(s["cell_status"][0][5], "warning")

    def test_syslog_not_read_or_capped(self):
        r = reads.get("health")
        r["syslog"] = {"ok": False, "why": "this SolarWinds has no Log Analyzer (no Orion.OLM.LogEntry)", "by_node": {}}
        s = swr.swr_health(r, reads.findings())["web_sections"][0]
        self.assertEqual(s["rows"][0][6], "-")
        self.assertEqual(s["note"], "Syslog not read: this SolarWinds has no Log Analyzer (no Orion.OLM.LogEntry).")
        r = reads.get("health")
        r["syslog"]["capped"] = True
        s = swr.swr_health(r, reads.findings())["web_sections"][0]
        self.assertEqual(s["rows"][0][6], "41+")
        self.assertIn("only the newest 5,000 entries were read (solarwinds_report_syslog_max_rows)", s["note"])
        r["syslog"] = None
        self.assertEqual(swr.swr_health(r, reads.findings())["web_sections"][0]["columns"][6], "Syslog errors")

    def test_the_email_one_line_per_section(self):
        v = self.view()
        self.assertEqual(len(v["sections"]), 1)
        e = v["sections"][0]
        self.assertEqual(e["lines"], ["SolarWinds says (6 hosts): 3 alerts on 2 hosts, 1 application not up, 1 volume over 90 %.",
                                      "SolarWinds alerts, our checks are green: app02.",
                                      "Not in SolarWinds: lab07, mon01."])
        self.assertTrue(e["under_findings"])
        self.assertEqual(e["boxes"][0]["label"], "SolarWinds alerts")
        self.assertNotIn("rows", e)

    def test_stale_is_one_muted_line_and_off_is_nothing(self):
        v = swr.swr_health(reads.get("stale"), reads.findings())
        self.assertEqual(v["web_sections"], [{"title": "Not read", "counted": False, "under_findings": True, "text": swr.NOT_READ,
                                              "lines": ["SolarWinds: the SolarWinds snapshot is 45 min old (of 2026-11-21 09:55 UTC), "
                                                        "older than solarwinds_db_max_age (20 min)."]}])
        self.assertNotIn("status", v["web_sections"][0])
        self.assertEqual(swr.swr_health({"skipped": True}, {}), {"sections": [], "web_sections": [], "lines": [], "sheets": []})

    def test_reader_text(self):
        """No ";" or " - " chains; a caption is at most two sentences."""
        for v in (self.view(), self.view(skip_hosts=["lab*"]), swr.swr_troubleshoot(reads.troubleshoot(events=27)),
                  swr.swr_alarms(reads.get("vcenter"), VmwareAlarms.D), swr.swr_health(reads.get("stale"), {})):
            for t in texts(v):
                self.assertNotIn(";", t, t)
                self.assertNotIn(" - ", t, t)
            for s in v.get("web_sections") or [v.get("section")]:
                cap = re.sub(r"\d+\.\d+", "N", s.get("text") or "")
                self.assertLessEqual(len(re.findall(r"\.(\s|$)", cap)), 2, s.get("text"))


class Troubleshoot1970(unittest.TestCase):
    def report(self, read=None, **o):
        v = swr.swr_troubleshoot(read or reads.get("troubleshoot"), o)
        F = {"severity": "warning", "check": "services", "summary": "httpd restarted 4 times", "hint": "journalctl -u httpd"}
        return v, sf.findings_report({"web01.example.mil": [F]}, "Troubleshoot web01", [], ["services"], v["web_sections"])

    def test_first_on_the_page_above_start_here(self):
        v, rep = self.report()
        self.assertEqual([s["title"] for s in rep["sections"]][:5],
                         ["What SolarWinds sees", "Its switch port", "Its events, last 24 h (12)", "Start here", "Findings"])

    def test_what_it_sees(self):
        v, _ = self.report()
        g = v["web_sections"][0]["glance"]
        self.assertEqual([(x["status"], x["label"], x["name"]) for x in g],
                         [("ok", "Up", "web01"), ("critical", "2 alerts", "Active alerts"), ("warning", "Errors", "Its switch port"),
                          ("info", "12", "Events (24 h)")])
        self.assertEqual(g[0]["text"], "Polled 10:38 UTC (2 min ago). 192.0.2.21, Dell R750, Red Hat Enterprise Linux 9.")
        self.assertEqual(g[2]["text"], "sw-acc-02 Gi1/0/14, 1 Gb/s: 412 errors this hour.")
        p = v["web_sections"][1]
        self.assertEqual(p["rows"], [["sw-acc-02", "Gi1/0/14", "Up", "1 Gb/s", "71", "12", "412", "0"]])
        self.assertEqual(p["cell_status"][0][6], "warning")
        ev = v["web_sections"][2]
        self.assertEqual(ev["rows"][3], ["09:56", "Interface Down", "Gi1/0/14 is down", "sw-acc-02"])
        self.assertEqual(ev["rows"][0][3], "web01")
        self.assertEqual(ev["row_status"][:4], ["warning", "warning", "", "warning"])

    def test_over_twenty_events_fold(self):
        v, _ = self.report(reads.troubleshoot(events=27))
        t = [s["title"] for s in v["web_sections"]]
        self.assertEqual(t[2:], ["Its events, last 24 h (27)", "Older events (7)"])
        self.assertEqual(len(v["web_sections"][2]["rows"]), 20)
        self.assertTrue(v["web_sections"][3]["collapsed"])
        self.assertEqual(v["web_sections"][2]["text"], "Newest first. The switch's events are its port's only.")

    def test_no_port_no_node(self):
        r = reads.get("troubleshoot")
        r["hosts"][0]["ports"] = []
        v = swr.swr_troubleshoot(r)
        self.assertEqual(v["web_sections"][0]["glance"][2]["text"], "SolarWinds' topology links no switch port to this host.")
        self.assertNotIn("Its switch port", [s["title"] for s in v["web_sections"]])
        r["hosts"][0]["node"] = None
        r["hosts"][0]["unmatched_why"] = "2 SolarWinds nodes are named web01"
        v = swr.swr_troubleshoot(r)
        self.assertEqual(v["web_sections"][0]["glance"][0]["label"], "Missing")
        self.assertTrue(v["web_sections"][0]["glance"][0]["text"].startswith("2 SolarWinds nodes are named web01. "))

    def test_two_hosts_say_which(self):
        r = reads.get("troubleshoot")
        r["hosts"].append(reads.host("web02.example.mil"))
        t = [s["title"] for s in swr.swr_troubleshoot(r)["web_sections"]]
        self.assertEqual(t, ["What SolarWinds sees: web01", "Its switch port: web01", "Its events, last 24 h (12): web01",
                             "What SolarWinds sees: web02"])

    def test_stale(self):
        v = swr.swr_troubleshoot(reads.get("stale"))
        self.assertEqual(v["web_sections"][0]["title"], "Not read")
        self.assertTrue(v["web_sections"][0]["first"])
        _, rep = self.report(reads.get("stale"))
        self.assertEqual(rep["sections"][0]["lines"], ["SolarWinds: the SolarWinds snapshot is 45 min old (of 2026-11-21 09:55 UTC), "
                                                       "older than solarwinds_db_max_age (20 min)."])

    def test_email(self):
        v = swr.swr_troubleshoot(reads.get("troubleshoot"))
        self.assertEqual(v["sections"][0]["lines"],
                         ["web01: node up, 2 alerts, port sw-acc-02 Gi1/0/14, 1 Gb/s: 412 errors this hour, 12 events in 24 h."])


class Alarms1970(unittest.TestCase):
    def setUp(self):
        self.D = copy.deepcopy(VmwareAlarms.D)
        self.s = swr.swr_alarms(reads.get("vcenter"), self.D)

    def test_the_objects_asked_about(self):
        self.assertEqual(swr.swr_vcenter_objects(self.D),
                         [{"host": h, "kind": k, "vcenter": self.D["vcenter"]} for h, k in (("esx01", "host"), ("esx02", "host"), ("esx03", "host"), ("db01", "vm"))])

    def test_section_and_tile(self):
        s = self.s["section"]
        self.assertEqual(s["title"], "SolarWinds alerts on the same hosts (3)")
        self.assertEqual(s["columns"], ["Severity", "Object", "SolarWinds alert", "Since (UTC)", "VIM status", "In vCenter too"])
        self.assertEqual(s["rows"], [["CRITICAL", "Host esx01", "Hardware: power supply 2 failed", "2026-10-07 03:01", "Warning",
                                      "Host hardware power status"],
                                     ["CRITICAL", "Host esx02", "Node down", "2026-10-07 04:58", "Unknown", "Not responding (Host connection)"],
                                     ["WARNING", "VM db01", "High CPU load over 90 % for 15 min", "2026-10-07 06:55", "Warning", "no"]])
        self.assertEqual(self.s["tile"], {"label": "SolarWinds alerts", "value": 3, "status": "critical", "note": "2 match a vCenter alarm",
                                          "counted": False})
        doc = swr.swr_alarm_doc(va.vm_alarm_report(self.D), self.s)
        self.assertEqual([x["title"] for x in doc["sections"]][:2], ["Triggered alarms", "SolarWinds alerts on the same hosts (3)"])
        self.assertEqual([x["label"] for x in doc["summary"]][2:4], ["Hosts not connected", "SolarWinds alerts"])
        em = swr.swr_alarm_doc(va.vm_alarm_report(self.D), self.s, True)["sections"][1]
        self.assertNotIn("rows", em)
        self.assertEqual(em["lines"], ["SolarWinds alerts on the same hosts: 3 (2 match a vCenter alarm). "
                                       "From the SolarWinds snapshot of 2026-10-07 10:40 UTC (6 min old)."])

    def test_none_stale_off(self):
        r = reads.get("vcenter")
        for h in r["hosts"]:
            h["alerts"] = []
        s = swr.swr_alarms(r, self.D)
        self.assertEqual(s["section"]["text"], "SolarWinds has no active alert on this vCenter's hosts and VMs. "
                                               "From the SolarWinds snapshot of 2026-10-07 10:40 UTC (6 min old).")
        st = swr.swr_alarms(reads.get("stale"), self.D)
        doc = swr.swr_alarm_doc(va.vm_alarm_report(self.D), st)
        self.assertEqual(doc["sections"][-1]["title"], "Not read")
        self.assertEqual(doc["sections"][-1]["lines"], ["SolarWinds: the SolarWinds snapshot is 45 min old (of 2026-11-21 09:55 UTC), "
                                                        "older than solarwinds_db_max_age (20 min)."])
        self.assertNotIn("SolarWinds alerts", [x["label"] for x in doc["summary"]])
        off = swr.swr_alarms({"skipped": True}, self.D)
        self.assertEqual(swr.swr_alarm_doc({"sections": [{"title": "Triggered alarms"}]}, off), {"sections": [{"title": "Triggered alarms"}]})

    def test_host_connection_fits(self):
        hc = [x for x in va.vm_alarm_report(self.D)["sections"] if x["title"] == "Host connection"][0]
        self.assertEqual(hc["columns"], ["Severity", "Host", "State now", "Events", "Last event (UTC)", "Last message", "Cluster", "Datacenter"])
        self.assertEqual(hc["rows"][0][2], "not responding")
        self.assertEqual(va._state("notResponding"), "not responding")
        self.assertEqual(va._state("standbyMode"), "standby mode")

    def test_act_evidence_task_and_report(self):
        probs = va.vm_alarm_problems(self.D)["problems"]
        ev = swr.swr_alarm_evidence(self.s, probs)
        lines = ev.strip().splitlines()
        self.assertTrue(lines[0].startswith("SOLARWINDS (read only, its snapshot of 2026-10-07 10:40 UTC, 6 min old)."))
        self.assertTrue(all(l.startswith("SolarWinds: ") for l in lines[1:]), lines)
        self.assertIn("SolarWinds: Host esx01 alert [CRITICAL] Hardware: power supply 2 failed, since 2026-10-07 03:01 UTC. Node Up, "
                      "VIM status Warning.", lines)
        self.assertIn("SolarWinds: esx02 is linked to sw-acc-04 Gi1/0/3 (down, 0 errors this hour).", lines)
        self.assertIn("SolarWinds: 2026-10-07 04:57 UTC esx02 port event on sw-acc-04: Gi1/0/3 is down", lines)
        self.assertEqual(swr.swr_alarm_evidence(swr.swr_alarms(reads.get("stale"), self.D), probs), "")
        task = va.vm_alarm_act_task(probs, {"solarwinds": True})
        self.assertIn("start each line you quote with the tool it came from: \"vCenter:\" or \"SolarWinds:\"", task)
        self.assertNotIn("SolarWinds", va.vm_alarm_act_task(probs, {}))
        with open(os.path.join(ROOT, "tests", "fixtures", "report_fit", "vm_alarm_act_report.json")) as f:
            doc = json.load(f)
        out = swr.swr_act_report(doc, self.s, probs)
        self.assertEqual(out["summary"][-1]["label"], "With SolarWinds")
        self.assertEqual(out["sections"][-1]["title"], "What ACT was given from SolarWinds (3 alerts)")
        self.assertTrue(out["sections"][-1]["collapsed"])
        self.assertEqual(out["sections"][-1]["text"], "The alerts on the same hosts and VMs, and their switch ports' events. From the "
                                                      "SolarWinds snapshot of 2026-10-07 10:40 UTC (6 min old). ACT reads them next to "
                                                      "vCenter's alarms.")
        self.assertEqual(len(out["sections"]), len(doc["sections"]) + 1)
        st = swr.swr_act_report(doc, swr.swr_alarms(reads.get("stale"), self.D), probs)
        self.assertEqual(st["sections"][-1]["title"], "Not read")
        self.assertEqual(st["summary"], doc["summary"])


class Placement1970(unittest.TestCase):
    def test_first_and_under_findings(self):
        F = {"severity": "critical", "check": "disk", "summary": "full", "hint": "df"}
        ex = [{"title": "Low", "lines": ["x"]}, {"title": "Under", "under_findings": True, "lines": ["y"], "boxes": [{"label": "B", "value": 1}]},
              {"title": "Top", "top": True, "lines": ["z"]}, {"title": "First", "first": True, "top": True, "lines": ["w"]}]
        rep = sf.findings_report({"h1": [F], "h2": []}, "T", ["h3"], ["disk"], ex)
        self.assertEqual([s["title"] for s in rep["sections"]],
                         ["First", "Start here", "Top", "Findings", "Under", "Did not report", "Low", "Healthy (1)"])
        self.assertIn({"label": "B", "value": 1}, rep["summary"])
        plain = sf.findings_report({"h1": [F]}, "T", [], ["disk"], [{"title": "Low", "lines": ["x"]}])
        self.assertEqual([s["title"] for s in plain["sections"]], ["Start here", "Findings", "Low"])


class Wiring1970(unittest.TestCase):
    def read(self, p):
        with open(os.path.join(ROOT, p)) as f:
            return f.read()

    def test_the_playbooks_ask_for_it(self):
        for p, mode in (("playbooks/health_check.yml", "health"), ("playbooks/win_health_check.yml", "health"),
                        ("playbooks/troubleshoot.yml", "troubleshoot"), ("playbooks/win_troubleshoot.yml", "troubleshoot")):
            self.assertIn("_swr_mode: %s " % mode, self.read(p), p)
        r = self.read("roles/site_findings/tasks/report.yml")
        self.assertIn("_swr_view.sections | default([])", r)
        self.assertIn("_swr_view.web_sections | default([])", r)
        for p in ("roles/vmware_vm/tasks/alarms.yml", "roles/vmware_vm/tasks/alarm_act.yml"):
            self.assertIn("alarms_solarwinds.yml", self.read(p))              # 1.113.0: the alarms reports again

    def test_never_the_jobs_result_and_read_only(self):
        t = self.read("roles/solarwinds_report/tasks/hosts.yml")
        self.assertIn("failed_when: false", t)
        lib = self.read("plugins/module_utils/site_host_swis.py") + self.read("roles/solarwinds_report/library/site_host_swis.py")
        for word in ("INSERT", "UPDATE ", "DELETE", "Invoke", "/Create", "/Update"):
            self.assertNotIn(word, lib, word)
        self.assertEqual(len(re.findall(r'"SELECT TOP %d', lib)), 1)      # the one live query


def N(nid, name, dns="", ip="", other=()):
    return {"node_id": nid, "name": name, "dns": dns, "ip": ip, "other_ips": list(other), "status_code": 1, "status": "Up",
            "status_text": "", "unmanaged": False, "last_poll": None, "cpu_pct": None, "mem_pct": None}


def H(*hosts):
    return hs._hosts([dict(host=h[0], **(h[1] if len(h) > 1 else {})) for h in hosts])


CANON_SQL = "LOWER(TRIM(TRAILING '.' FROM TRIM(REPLACE(REPLACE(REPLACE(%s, CHAR(9), ' '), CHAR(13), ' '), CHAR(10), ' '))))"
CAPTION_SQL = (CANON_SQL % "name", CANON_SQL % "dns", "name_key",
               "SUBSTRING_INDEX(%s, '.', 1)" % (CANON_SQL % "name"), "SUBSTRING_INDEX(%s, '.', 1)" % (CANON_SQL % "dns"))


class CaptionNorm1970(unittest.TestCase):
    def test_full_caption_wins_over_short_name(self):
        host, = H(("web01.example.mil",))
        retired = N(2, "web01", dns="retired01")
        for fqdn in ("web01.example.mil", "web01.example.mil."):
            for caption in (" web01.example.mil", "WEB01.EXAMPLE.MIL."):
                with self.subTest(fqdn=fqdn, caption=caption):
                    current = N(1, caption, dns="mgmt01.example.mil")
                    result = hs.match([dict(host, fqdn=fqdn)], [retired, current])
                    self.assertEqual(result[0][:2], (current, "name"))

    def test_sql_normalizes_candidate_names(self):
        import site_swis_db as sd
        from test_solarwinds_snapshot import FakeConn
        conn = FakeConn(lambda sql, args: ([], []))
        sd.nodes_for_hosts(conn, 7, ["web01.example.mil"], ["web01"], [])
        [(sql, args)] = conn.sql
        for expr in CAPTION_SQL:
            self.assertIn(expr + " IN (%s)", sql)
        self.assertEqual(args, [7, "web01.example.mil", "web01.example.mil", "web01", "web01", "web01"])


class Review1970(unittest.TestCase):
    """Neon's review of 1.97.0 (ae4972e1): a test for each finding, failing before the fix."""

    # P1-1: the wrong host across domains or by a suffix
    def test_p1_1_another_domain_is_never_this_host(self):
        nodes = [N(1, "web01.a.example.mil", ip="192.0.2.10")]
        got = hs.match(H(("web01", {"fqdn": "web01.b.example.mil"})), nodes)           # ansible_host web01.b: refused
        self.assertEqual(got[0], (None, "", "a SolarWinds node named web01 is web01.a.example.mil"))
        self.assertIsNone(hs.match(H(("web01",)), [N(1, "web01", dns="web01.other.example.mil")])[0][0])   # a bare name never takes a domain
        got = hs.match(H(("web01.b.example.mil", {"ip": "192.0.2.20"})), [N(1, "web01", ip="192.0.2.10")])
        self.assertEqual(got[0], (None, "", "the SolarWinds node named web01 has another IP (192.0.2.10)"))
        self.assertEqual(hs.match(H(("web01", {"fqdn": "web01.a.example.mil"})), nodes)[0][:2], (nodes[0], "name"))
        self.assertEqual(hs.match(H(("web01",)), [N(1, "WEB01", ip="192.0.2.10")])[0][1], "name")      # both without a domain
        self.assertEqual(hs.match(H(("app", {"ip": "192.0.2.10"})), nodes)[0][1], "ip")                  # the IP settles it

    def test_p1_1_the_task_passes_ansible_host_as_the_full_name(self):
        with open(os.path.join(ROOT, "roles", "site_findings", "tasks", "solarwinds.yml")) as f:
            t = f.read()
        self.assertIn("'fqdn': (ah if '.' in ah", t)

    # P1-2: ESXi host / VM by vCenter name and kind
    def test_p1_2_vim_by_kind_and_full_name(self):
        import test_solarwinds_snapshot as ts
        import site_swis_db as sd

        def ans(sql, args):
            if "information_schema.columns" in sql:
                return [("n",)], [(1,)]
            if "DISTINCT vcenter" in sql:
                return [("vcenter",)], [("vc01.example.mil",)]
            cols = [(k,) for k in ("vim_id", "name", "name_key", "status", "connection_state", "power_state", "cluster", "node_id", "vcenter")]
            if "FROM sw_vim_host" in sql:
                return cols, [(1, "esx01.a.example.mil", "esx01", 1, "connected", "poweredOn", "c1", 11, "vc01.example.mil"),
                              (2, "esx01.b.example.mil", "esx01", 2, "x", "y", "c2", 12, "vc01.example.mil")]
            return cols, [(3, "esx01", "esx01", 3, None, "poweredOn", None, None, "vc01.example.mil")]
        got = sd.vim_for(ts.FakeConn(ans), 7, [{"name": "esx01.b.example.mil", "kind": "host", "vcenter": "vc01"},
                                               {"name": "esx01", "kind": "vm", "vcenter": "vc01"},
                                               {"name": "esx01", "kind": "host", "vcenter": "vc01"}])
        b = got[sd.vim_key("host", "vc01", "esx01.b.example.mil")]
        self.assertEqual((b["status"], b["cluster"], b["vcenter"]), ("Down", "c2", "vc01.example.mil"))
        host = got[sd.vim_key("host", "vc01", "esx01")]    # host esx01: no host has that full name - never a guess, never the VM
        self.assertIsNone(host["node_id"])
        self.assertTrue(host["other_kind"])
        self.assertEqual(got[sd.vim_key("vm", "vc01", "esx01")]["kind"], "vm")

    def test_p1_2_alarms_join_by_kind_and_full_name(self):
        d = {"hosts": [{"name": "esx01.a.example.mil", "connection": "connected"}, {"name": "esx01.b.example.mil", "connection": "notResponding"}],
             "alarms": [{"entity_type": "host", "entity": "esx01.b.example.mil", "alarm": "Host CPU usage"},
                        {"entity_type": "vm", "entity": "esx01", "alarm": "VM CPU usage"}], "events": []}
        self.assertEqual(swr.swr_vcenter_objects(d), [{"host": "esx01.a.example.mil", "kind": "host", "vcenter": ""}, {"host": "esx01.b.example.mil", "kind": "host", "vcenter": ""},
                                                      {"host": "esx01", "kind": "vm", "vcenter": ""}])
        r = reads.get("vcenter")
        r["hosts"] = [reads.host("esx01.a.example.mil", reads.node(1, "esx01.a.example.mil"), kind="host", alerts=[reads.alert("Fan failed", 2, "2026-10-07 03:00")]),
                      reads.host("esx01", reads.node(2, "esx01-vm"), kind="vm", alerts=[reads.alert("Disk full", 1, "2026-10-07 04:00")])]
        s = swr.swr_alarms(r, d)
        self.assertEqual([(x[1], x[5]) for x in s["section"]["rows"]], [("Host esx01.a.example.mil", "no"), ("VM esx01", "VM CPU usage")])
        probs = [{"object_type": "host", "object": "esx01.b.example.mil"}]
        self.assertEqual(swr.swr_alarm_evidence(s, probs), "")                           # esx01.a's alert is not esx01.b's
        self.assertEqual(swr.swr_act_report({"sections": [], "summary": []}, s, probs)["summary"][-1]["value"], 0)

    # P1-3: SolarWinds is never counted
    def test_p1_3_never_counted(self):
        import importlib.util
        sys.path.insert(0, os.path.join(ROOT, "roles", "site_email", "library"))
        for name in ("site_aap", "site_report_page", "site_banner", "site_time", "site_report_files", "site_festive"):   # as test_report_web.py loads them
            if "ansible.module_utils." + name not in sys.modules:
                spec = importlib.util.spec_from_file_location("ansible.module_utils." + name, os.path.join(ROOT, "plugins", "module_utils", name + ".py"))
                mod = importlib.util.module_from_spec(spec)
                sys.modules["ansible.module_utils." + name] = mod
                spec.loader.exec_module(mod)
        import site_report_web as rw
        ok = dict((h, []) for h in reads.findings())
        v = swr.swr_health(reads.get("health"), ok)
        rep = sf.findings_report(ok, "Health check", [], ["disk"], v["web_sections"])
        self.assertEqual(rw.run_counts(rep), None)                                       # no finding, no SolarWinds row counted
        self.assertTrue(all(sec.get("counted") is False for sec in v["web_sections"] + v["sections"]))
        self.assertTrue(all(b.get("counted") is False for sec in v["web_sections"] for b in sec.get("boxes") or []))
        from test_report_fit import render
        html = render("ops", copy.deepcopy(rep))
        hero = html[html.index('<header class="hero'):html.index("</header>", html.index('<header class="hero'))]
        self.assertNotIn("SolarWinds alerts", hero)
        D = copy.deepcopy(VmwareAlarms.D)
        doc = swr.swr_alarm_doc(va.vm_alarm_report(D), swr.swr_alarms(reads.get("vcenter"), D))
        self.assertEqual(rw.run_counts(doc), rw.run_counts(va.vm_alarm_report(D)))
        html = render("ops", copy.deepcopy(doc))
        start = html[html.index('id="start"'):] if 'id="start"' in html else ""
        self.assertNotIn("Hardware: power supply 2 failed", start[:start.find("</section>")])

    # P1-4: no warning for the other OS's hosts, nor for solarwinds_report_skip_hosts
    def test_p1_4_skip_hosts_as_text_and_in_troubleshoot(self):
        self.assertTrue(swr._skipped("lab07.example.mil", "lab*"))
        self.assertFalse(swr._skipped("web01.example.mil", "lab*"))                    # "*" is never a pattern by itself
        self.assertTrue(swr._skipped("web01.lab.example.mil", "*.lab.example.mil, other"))
        self.assertFalse(swr._skipped("web01.example.mil", "web01.lab.*"))            # a dotted pattern: the full name only
        r = reads.get("troubleshoot")
        r["hosts"] = [reads.host("lab07.example.mil")]
        g = swr.swr_troubleshoot(r, {"skip_hosts": ["lab*"]})["web_sections"][0]
        self.assertEqual((g["status"], g["glance"][0]["label"]), ("info", "Left out"))

    def test_p1_4_the_hosts_less_the_other_os(self):
        with open(os.path.join(ROOT, "roles", "site_findings", "tasks", "report.yml")) as f:
            t = f.read()
        block = t[t.index("_swr_targets: >-"):t.index("- name:", t.index("_swr_targets: >-"))]
        self.assertIn("site_windows_connections", block)
        self.assertIn("site_host_skipped", block)
        with open(os.path.join(ROOT, "roles", "site_findings", "tasks", "solarwinds.yml")) as f:
            t = f.read()
        self.assertIn("_swr_targets | difference(ansible_play_hosts)", t)
        self.assertNotIn("ansible_play_hosts_all", t)

    # P1-5: a stale snapshot is never current, and its age is always said
    def test_p1_5_age_in_seconds_and_always_said(self):
        why = hs.read(HOSTS, {"max_age": 20, "now": NOW}, object(), Snap(age=1259))["why"]
        self.assertEqual(why, ["the SolarWinds snapshot is 20 min old (of 2026-11-21 10:40 UTC), older than solarwinds_db_max_age (20 min). "
                               "The newer runs failed: login refused"])
        none = Snap(age=None)
        none.latest = lambda conn, fresh=True: {"snap_id": 7, "read_at": T("2026-11-21 08:30"), "age_seconds": None, "orion": "", "core": {},
                                                "not_read": []}
        self.assertIn("2 h 16 min old", hs.read(HOSTS, {"max_age": 20, "now": NOW}, object(), none)["why"][0])
        none.latest = lambda conn, fresh=True: {"snap_id": 7, "read_at": None, "age_seconds": None, "orion": "", "core": {}, "not_read": []}
        self.assertEqual(hs.read(HOSTS, {"max_age": 20, "now": NOW}, object(), none)["why"], ["the SolarWinds snapshot has no time: it is never used"])
        D = copy.deepcopy(VmwareAlarms.D)
        s = swr.swr_alarms(reads.get("vcenter"), D)
        self.assertIn("2026-10-07 10:40 UTC (6 min old)", s["section"]["text"])
        self.assertIn("6 min old", swr.swr_alarm_evidence(s, va.vm_alarm_problems(D)["problems"]))

    # P2-1: one read-only transaction for the whole read
    def test_p2_1_apps_inside_the_callers_transaction(self):
        s = Snap()
        hs.read([{"host": "web01.example.mil"}], {"max_age": 20, "now": NOW}, object(), s)
        self.assertIn(("apps", False), s.calls)
        import test_solarwinds_snapshot as ts
        import site_swis_db as sd
        c = ts.FakeConn(lambda sql, args: ([("x",)], []))
        sd.apps_for(c, 7, [1], own_tx=False)
        self.assertFalse([x for x in c.sql if x[0] in ("COMMIT", "ROLLBACK") or str(x[0]).startswith("START")])

    # P2-2: a node the capped read never asked: "?"; a count the cap may cut: "0+"
    def test_p2_2_unasked_and_capped(self):
        r = reads.get("health")
        r["syslog"].update(capped=True, queried=["101"])
        rows = dict((x[1], x) for x in swr.swr_health(r, reads.findings())["web_sections"][0]["rows"])
        self.assertEqual((rows["web01"][6], rows["db01"][6]), ("41+", "?"))
        r["syslog"].update(queried=["101", "102", "103", "104"], by_node={})
        rows = dict((x[1], x) for x in swr.swr_health(r, reads.findings())["web_sections"][0]["rows"])
        self.assertEqual(rows["app03"][6], "0+")

    # P2-3: the newest events, another port's never in the limit
    def test_p2_3_events_newest_first_ports_in_sql(self):
        import test_solarwinds_snapshot as ts
        import site_swis_db as sd
        c = ts.FakeConn(lambda sql, args: ([("event_id",), ("event_time",), ("node_id",), ("net_object_type",), ("net_object_id",),
                                             ("event_type",), ("type_name",), ("message",)], [(2, T("2026-11-21 10:00"), 501, "I", 7014, 10, "x", "y")]))
        got = sd.events_for(c, T("2026-11-20 10:00"), T("2026-11-21 10:00"), [101], [(501, 7014)], limit=50, orion="o:1")
        sql, args = c.sql[-1]
        self.assertIn("ORDER BY event_time DESC, event_id DESC LIMIT 50", sql)
        self.assertIn("(node_id = %s AND net_object_type = 'I' AND net_object_id = %s)", sql)
        self.assertEqual(args[-3:], [101, 501, 7014])
        self.assertEqual(got[0]["port"], [501, 7014])

    # P2-4: a host on two switches: each port event names its own switch; links between asked nodes kept
    def test_p2_4_multi_homed(self):
        r = reads.get("troubleshoot")
        h = r["hosts"][0]
        h["ports"] = [reads.port("sw1", "Gi1/0/1", sid=1, iid=11), reads.port("sw2", "Gi1/0/2", sid=2, iid=22)]
        h["events"] = [{"time": "2026-11-21 10:00", "kind": "Interface Down", "message": "Gi1/0/1 is down", "on": "port", "switch": "sw1",
                        "event_id": 5}]
        ev = [x for x in swr.swr_troubleshoot(r)["web_sections"] if x["title"].startswith("Its events")][0]
        self.assertEqual(ev["rows"][0][3], "sw1")
        import test_solarwinds_snapshot as ts
        import site_swis_db as sd

        def ans(sql, args):
            if "FROM sw_link" in sql:
                return [("src_node",), ("src_if",), ("dst_node",), ("dst_if",)], [(1, 10, 2, 20)]
            if "FROM sw_interface" in sql:
                return ([(c,) for c in ("interface_id", "node_id", "name", "caption", "status", "speed", "in_pct", "out_pct", "in_errors_hour",
                                        "out_errors_hour", "in_discards_hour", "out_discards_hour")],
                        [(20, 2, "eth0", "eth0", 1, 1e9, 1, 1, 0, 0, 0, 0), (10, 1, "eth0", "eth0", 1, 1e9, 1, 1, 0, 0, 0, 0)])
            return [("node_id",), ("name",)], [(1, "a"), (2, "b")]
        got = sd.ports_for(ts.FakeConn(ans), 7, [1, 2])
        self.assertEqual(([p["switch"] for p in got[1]], [p["switch"] for p in got[2]]), (["b"], ["a"]))

    # P2-5: the workbook has SolarWinds' tables, the ACT email one line
    def test_p2_5_sheets_and_short_act_email(self):
        v = swr.swr_health(reads.get("health"), reads.findings())
        self.assertEqual([x["name"] for x in v["sheets"]], ["SolarWinds says (6 hosts)"])
        self.assertEqual(v["sheets"][0]["columns"][0], "Host")
        D = copy.deepcopy(VmwareAlarms.D)
        s = swr.swr_alarms(reads.get("vcenter"), D)
        self.assertEqual([x["name"] for x in s["sheets"]], ["SolarWinds alerts (3)"])
        probs = va.vm_alarm_problems(D)["problems"]
        em = swr.swr_act_report({"sections": [], "summary": []}, s, probs, True)["sections"][-1]
        self.assertNotIn("rows", em)
        with open(os.path.join(ROOT, "roles", "vmware_vm", "tasks", "alarm_act.yml")) as f:
            t = f.read()
        self.assertIn("_vm_report_web_doc", t)
        self.assertIn("_vm_al_swr.lines", t)
        with open(os.path.join(ROOT, "roles", "site_findings", "tasks", "report.yml")) as f:
            self.assertIn("_swr_view.sheets", f.read())

    # P2-6: other IPs match; a part the snapshot could not read is said; a failure is under Not read
    def test_p2_6_other_ip_part_failure_not_read(self):
        self.assertEqual(hs.match(H(("app", {"ip": "198.51.100.7"})), [N(1, "web01", ip="192.0.2.10", other=["198.51.100.7"])])[0][1],
                         "other ip")
        s = Snap()
        s.part_note = lambda snap, part: {"what": "x", "why": "timeout", "absent": False} if part == "app" else None
        r = hs.read([{"host": "web01.example.mil"}], {"max_age": 20, "now": NOW}, object(), s)
        self.assertIsNone(r["sam"])
        self.assertEqual(r["not_read"], ["SAM's applications: not read by the last snapshot (timeout)"])
        h = reads.get("health")
        h.update(sam=None, not_read=r["not_read"])
        v = swr.swr_health(h, reads.findings())
        self.assertEqual(dict((x[1], x[4]) for x in v["web_sections"][0]["rows"])["web01"], "?")
        self.assertEqual(v["web_sections"][-1]["title"], "Not read")
        self.assertEqual(swr.swr_health({"ok": False, "why": ["the report database: the login was refused"]}, {})["web_sections"][0]["title"],
                         "Not read")


class Review1970b(unittest.TestCase):
    def test_vcenter_keys(self):
        name = "web01.a.example.mil"
        d = {"hosts": [{"name": name, "vcenter": "vc01", "connection": "connected"}],
             "alarms": [{"entity": name, "entity_type": "host", "alarm": "Power", "vcenter": "vc02"}],
             "events": [{"vm": name.upper(), "severity": "warning", "vcenter": "vc03"}]}
        objs = swr.swr_vcenter_objects(d)
        self.assertEqual([(o["vcenter"], o["kind"]) for o in objs], [("vc01", "host"), ("vc02", "host"), ("vc03", "vm")])
        snap = Snap()
        snap.nodes_for_hosts = lambda *args: [N(101, name)]
        got = hs.read(objs, {"mode": "vcenter", "now": NOW}, object(), snap)
        self.assertTrue(got["ok"], got["why"])
        self.assertTrue(all(not h["node"] for h in got["hosts"]))
        r = reads.get("vcenter")
        r["hosts"] = [reads.host(o["host"], reads.node(i, name), kind=o["kind"], vcenter=o["vcenter"],
                                 alerts=[reads.alert(o["vcenter"], 2, NOW)]) for i, o in enumerate(objs)]
        view = swr.swr_alarms(r, d)
        self.assertEqual({a["alert"]: a["vcenter"] for a in view["alerts"]}, {"vc01": "no", "vc02": "Power", "vc03": "no"})
        p = {"object": name, "object_type": "host", "datacenter": "vc01"}
        self.assertEqual(swr.swr_alarm_evidence(view, [p]), "")
        p["object_type"] = "vm"
        self.assertIn("vc03", swr.swr_alarm_evidence(view, [p]))
        self.assertEqual(swr.swr_act_report({}, view, [p])["summary"][-1]["value"], 1)
        p["vcenter"] = "vc02"
        self.assertEqual(swr.swr_act_report({}, view, [p])["summary"][-1]["value"], 0)
        r["hosts"][1]["node"] = r["hosts"][0]["node"]
        self.assertEqual([a["alert"] for a in swr.swr_alarms(r, d)["alerts"]], ["vc03"])

    def test_vim_node_ownership(self):
        name = "esx01.a.example.mil"
        snap = Snap()
        snap.vim_for = lambda *args: {hs.sdb.vim_key("vm", "", name): {"node_id": 101, "kind": "vm", "why": ""}}
        snap.nodes_for_hosts = lambda *args: [N(77, name)]
        snap.vim_nodes = lambda *args: [dict(N(101, "guest01"), kind="vm", vim_id=1), dict(N(77, name), kind="vm", vim_id=2)]
        objs = [{"host": name, "kind": kind} for kind in ("host", "vm")]
        for requested in (objs, objs[:1]):
            r = hs.read(requested, {"mode": "vcenter", "now": NOW}, object(), snap)
            self.assertTrue(r["ok"], r["why"])
            self.assertIsNone(r["hosts"][0]["node"])
            if len(requested) == 2:
                vm = r["hosts"][1]
                self.assertEqual((vm["matched"], vm["node"]["node_id"], len(vm["alerts"])), ("vim", 101, 1))

    def test_vim_full_name_only(self):
        import site_swis_db as sd
        from test_solarwinds_snapshot import FakeConn
        cols = [(k,) for k in ("vim_id", "name", "name_key", "status", "connection_state", "power_state", "cluster", "node_id", "vcenter")]
        row = (1, "esx01.a.example.mil", "esx01", 1, "", "", "", 11, "vc01")

        def ans(sql, args):
            if "information_schema.columns" in sql:
                return [("n",)], [(1,)]
            if "DISTINCT vcenter" in sql:
                return [("vcenter",)], [("vc01",)]
            return cols, [row] if "FROM sw_vim_host" in sql else []
        got = sd.vim_for(FakeConn(ans), 7, [{"kind": "host", "name": n, "vcenter": "vc01"} for n in ("esx01.b.example.mil", "esx01")])
        self.assertEqual(set(got.values()), {None})          # review 1970c P1-2: a short name is never VIM's proof

    def test_inventory_fqdn_first(self):
        hosts = H(("web01.a.example.mil", {"fqdn": "mgmt01.b.example.mil"}), ("alias", {"fqdn": "web01.a.example.mil"}))
        self.assertEqual([h["fqdn"] for h in hosts], ["web01.a.example.mil"] * 2)

    def test_short_unique_across_job(self):
        hosts = H(("web01.a.example.mil",), ("web01.b.example.mil",))
        n = N(1, "web01", ip="192.0.2.10")
        for order in (hosts, hosts[::-1]):
            self.assertEqual(list(hs.match(order, [n]).values()), [(None, "", "2 hosts of this job could be the SolarWinds node web01")] * 2)
        hosts[1]["ip"] = "192.0.2.10"
        got = hs.match(hosts, [n])
        self.assertIsNone(got[0][0])
        self.assertEqual(got[1][:2], (n, "ip"))
        nodes = [N(2, "web01"), N(3, "web01.b.example.mil", ip="192.0.2.10")]
        got = hs.match(hosts, nodes)
        self.assertEqual(got[0][:2], (nodes[0], "name"))
        self.assertEqual(got[1][:2], (nodes[1], "name"))

    def test_caption_candidates(self):
        import site_swis_db as sd
        from test_solarwinds_snapshot import FakeConn
        name = "web01.a.example.mil"
        row = dict(N(101, name, dns="mgmt01.a.example.mil"), name_key="mgmt01", ips='["192.0.2.20"]')
        c = FakeConn(lambda sql, args: ([(k,) for k in row], [tuple(row.values())]))
        snap = Snap()
        snap.nodes_for_hosts = sd.nodes_for_hosts
        r = hs.read([{"host": name, "ip": "192.0.2.20"}], {"now": NOW}, c, snap)
        self.assertTrue(r["ok"], r["why"])
        self.assertEqual(r["hosts"][0]["node"]["node_id"], 101)
        [(sql, args)] = c.sql
        for expr in CAPTION_SQL + ("LOWER(ip)",):
            self.assertIn(expr + " IN", sql)
        self.assertIn("ips LIKE %s", sql)
        self.assertIn('%"192.0.2.20"%', args)
        [candidate] = sd.nodes_for_hosts(c, 7, [name], ["web01"], [])
        self.assertEqual((candidate["matched_by"], candidate["other_ips"]), ("name", ["192.0.2.20"]))

    def test_ipv6_identity(self):
        hosts = H(("web01", {"ip": "2001:0DB8:0:0::1"}))
        self.assertEqual(hosts[0]["ip"], "2001:db8::1")
        self.assertEqual(hs.match(hosts, [N(1, "mgmt01", ip="2001:0DB8::1")])[0][1], "ip")
        self.assertEqual(hs.match(hosts, [N(1, "mgmt01", other=["2001:0DB8::1"])])[0][1], "other ip")
        self.assertIsNone(hs.match(hosts, [N(1, "web01", ip="2001:db8::2")])[0][0])

    def test_skip_resolved_fqdn(self):
        r = reads.get("health")
        r["hosts"] = [reads.host("alias", fqdn="web01.a.example.mil"), reads.host("web02", reads.node(2, "web02"))]
        opts = {"skip_hosts": "*.a.example.mil"}
        self.assertEqual([x[1] for x in swr.swr_health(r, {}, opts)["web_sections"][0]["rows"]], ["web02"])
        self.assertEqual(swr.swr_troubleshoot(r, opts)["web_sections"][0]["glance"][0]["label"], "Left out")


class SharedIp1970(unittest.TestCase):
    """The director's rule: an IP matches only when one host of the job and one node have it; shared, the short name."""

    def test_a_shared_ip_never_matches(self):
        vip = "192.0.2.50"
        nodes = [N(1, "app01.example.mil", ip="192.0.2.11", other=[vip]), N(2, "app02.example.mil", ip="192.0.2.12", other=[vip])]
        got = hs.match(H(("lb", {"ip": vip})), nodes)                                   # two nodes have it
        self.assertEqual(got[0], (None, "", "its IP 192.0.2.50 is shared (1 host of this job, 2 SolarWinds nodes): not used to match"))
        two = hs.match(H(("svc-a", {"ip": "192.0.2.11"}), ("svc-b", {"ip": "192.0.2.11"})), nodes)   # two hosts have it
        self.assertEqual([two[0][0], two[1][0]], [None, None])
        one = hs.match(H(("app01", {"ip": vip})), nodes)                               # shared: the unique short name decides
        self.assertEqual(one[0][:2], (None, ""))                                       # app01 has a domain, the host none: refused
        ok = hs.match(H(("app01.example.mil", {"ip": vip})), nodes)
        self.assertEqual(ok[0][:2], (nodes[0], "name"))                                # the full name first
        self.assertEqual(hs.match(H(("x", {"ip": "192.0.2.12"})), nodes)[0][1], "ip")   # one host, one node: the IP


class Review1970d(unittest.TestCase):
    def setUp(self):
        self.db = Snap()

    def read(self, hosts=None, mode="troubleshoot"):
        r = hs.read(hosts or HOSTS[:1], {"mode": mode, "now": NOW}, object(), self.db)
        self.assertTrue(r["ok"], r["why"])
        return r

    def glance(self, r, name):
        return next(g for g in swr.swr_troubleshoot(r)["web_sections"][0]["glance"] if g["name"] == name)

    def unread(self, part):
        self.db.notes[part] = {"what": part, "why": "timeout", "absent": False}

    def test_alerts_not_read(self):
        for failed in (False, True):
            self.db = Snap()
            if failed:
                self.unread("alert")
            else:
                self.db.counts["alerts"] = None
            r = self.read()
            self.assertFalse(r["alerts_read"])
            self.assertFalse(any(c[0] == "alerts" for c in self.db.calls))
            self.assertIn("the active alerts: not read by the last snapshot", r["not_read"])
            r["hosts"][0].update(apps=[], alerts=[{"name": "stale"}])
            view = swr.swr_health(r)
            sec, nr = view["web_sections"]
            self.assertEqual(nr["title"], "Not read")
            self.assertEqual(sec["rows"][0][3], "?")
            tile = sec["boxes"][0]
            self.assertEqual((tile["value"], tile["note"], tile["status"]), ("?", "not read", "info"))
            self.assertIn("alerts not read", view["lines"][0])
            g = self.glance(r, "Active alerts")
            self.assertEqual((g["label"], g["status"], g["text"]),
                             ("Not read", "info", "The active alerts were not read by the last snapshot."))

    def test_incomplete_identity(self):
        self.db.core["cut"] = True
        self.unread("ip")
        hosts = HOSTS[:1] + [HOSTS[2], {"host": "short01"}]
        self.db.nodes_for_hosts = lambda *args: [N(1, "WEB01.EXAMPLE.MIL"), N(2, "other", ip="192.0.2.22"), N(3, "short01")]
        for mode in ("health", "vcenter"):
            r = self.read(hosts, mode)
            self.assertEqual([h["matched"] for h in r["hosts"]], ["name", "", ""])
            self.assertTrue(any("short" in line and "max_nodes" in line for line in r["not_read"]))
            self.assertTrue(any("IP" in line and "addresses" in line for line in r["not_read"]))
        self.db.notes["ip"]["absent"] = True                  # review 1970e A: cut, so no address match either
        self.assertEqual([h["matched"] for h in self.read(hosts)["hosts"]], ["name", "", ""])
        self.db.core.clear()
        self.unread("ip")
        self.assertEqual([h["matched"] for h in self.read(hosts)["hosts"]], ["name", "", "name"])
        self.db.notes["ip"]["absent"] = True
        self.assertEqual([h["matched"] for h in self.read(hosts)["hosts"]], ["name", "ip", "name"])

    def test_canonical_caption(self):
        host = H(("tab host01.example.mil",))
        node = N(91, "\tTAB\rHOST01.EXAMPLE.MIL...\n", dns="mgmt91.example.mil")
        self.assertEqual(hs.match(host, [N(92, "tab host01"), node])[0][:2], (node, "name"))
        self.assertEqual(hs._short(node["name"]), "tab host01")
        self.assertEqual(hs._full("\vHOST.EXAMPLE.MIL."), "host.example.mil")

    def test_events_not_read(self):
        for failed in (False, True):
            self.db = Snap()
            if failed:
                self.unread("event")
            else:
                self.db.counts["events"] = None
            r = self.read()
            self.assertFalse(r["events"])
            self.assertFalse(any(c[0] == "events" for c in self.db.calls))
            self.assertTrue(any("events" in line for line in r["not_read"]))
            g = self.glance(r, "Events (24 h)")
            self.assertEqual((g["label"], g["status"]), ("Not read", "info"))
            self.assertIn("events not read", swr.swr_troubleshoot(r)["lines"][0])

    def test_topology_and_interface_not_read(self):
        self.db.core["topology_ok"] = False
        r = self.read()
        self.assertFalse(r["ports_read"])
        self.assertTrue(any("topology" in line for line in r["not_read"]))
        self.assertFalse(any(c[0] == "ports" for c in self.db.calls))
        g = self.glance(r, "Its switch port")
        self.assertEqual((g["label"], g["text"]), ("Not read", "SolarWinds' topology was not read by the last snapshot."))
        self.db.core.clear()
        self.db.ports_for = lambda *args: {101: [dict(reads.port(), status="", errors=None)]}
        r = self.read()
        g = self.glance(r, "Its switch port")
        self.assertEqual((g["label"], g["status"]), ("Not read", "info"))
        self.assertIn("status not read", g["text"])
        self.assertIn("errors not read", g["text"])
        row = swr.swr_troubleshoot(r)["sheets"][0]["rows"][0]
        self.assertEqual((row[2], row[6]), ("?", "?"))

    def test_volume_summary(self):
        self.unread("volume")
        r = self.read()
        self.assertIn("volumes not read", swr.swr_health(r)["lines"][0])
        self.db.notes["volume"]["absent"] = True
        self.assertNotIn("volume", swr.swr_health(self.read())["lines"][0])
        self.db.notes.clear()
        self.assertIn("1 volume over 90 %", swr.swr_health(self.read())["lines"][0])

    def test_last_poll_not_read(self):
        self.unread("node")
        r = self.read()
        self.assertFalse(r["poll_read"])
        self.assertTrue(any("last poll" in line for line in r["not_read"]))
        g = self.glance(r, "web01")
        self.assertIn("Last poll not read.", g["text"])
        self.assertNotIn("ago", g["text"])

    def test_redact_read(self):
        bearer = "Authorization: Bearer abc123secretvalue"
        password = "password=hunter2x"
        r = self.read()
        host = r["hosts"][0]
        host["alerts"][0].update(name=password, message=bearer)
        host["events"][0]["message"] = password
        r["syslog"] = {"by_node": {"101": {"top": [{"message": bearer}]}}}
        result = hs.redact_read(r, site_redact.redact)
        for secret in ("abc123secretvalue", "hunter2x"):
            self.assertNotIn(secret, json.dumps(result))
        self.assertEqual(result["syslog"]["by_node"]["101"]["top"][0]["message"], site_redact.redact(bearer))


class Review1970e(unittest.TestCase):
    """Codex's check of 0276f907: a test for each item left open, failing before the fix."""

    def setUp(self):
        self.db = Snap()

    def read(self, hosts=None, mode="troubleshoot"):
        r = hs.read(hosts or HOSTS[:1], {"mode": mode, "now": NOW}, object(), self.db)
        self.assertTrue(r["ok"], r["why"])
        return r

    def test_a_cut_snapshot_never_matches_by_address(self):
        self.db.core["cut"] = True                            # the other addresses were read: only the cut is said
        self.db.nodes_for_hosts = lambda *args: [N(2, "other", ip="192.0.2.22")]
        r = self.read([HOSTS[2]], "health")
        self.assertEqual((r["hosts"][0]["matched"], r["hosts"][0]["node"]), ("", None))
        self.assertTrue(any("IP matching" in line and "max_nodes" in line for line in r["not_read"]))

    def test_b_syslog_redacted_before_the_cut(self):
        secret = "hunter2secretpass"
        msg = "x" * (300 - len("https://admin:" + secret)) + "https://admin:" + secret + "@db.example.mil/x"
        self.assertEqual(len(msg.split("@")[0]), 300)                  # character 300 ends the password, before "@"
        r = hs.syslog_live(Client(rows=[entry(101, "10:00", msg)]), [101], {"now": NOW, "redact": site_redact.redact})
        r = hs.redact_read({"syslog": r, "hosts": []}, site_redact.redact)
        self.assertNotIn("hunter2", json.dumps(r))

    def test_c_unicode_white_space_around_a_caption(self):
        host = H(("web01.example.mil",))
        down = dict(N(1, "\u00a0WEB01.EXAMPLE.MIL.\u2003"), status_code=2, status="Down")
        up = N(2, "web01")
        self.assertEqual(hs.match(host, [up, down])[0][:2], (down, "name"))
        self.assertEqual(hs.canon("\u00a0Web01.Example.Mil.\u3000"), "web01.example.mil")

    def test_d_unresolved_after_a_failed_address_read_is_unknown(self):
        self.db.notes["ip"] = {"what": "ip", "why": "timeout", "absent": False}
        self.db.nodes_for_hosts = lambda *args: [N(101, "web01.example.mil"), N(2, "other", ip="192.0.2.22")]
        r = self.read([HOSTS[0], HOSTS[2]], "health")
        h = r["hosts"][1]
        self.assertTrue(h["uncertain"])
        self.assertEqual(h["unmatched_why"], "match not certain: the other node addresses were not read")
        self.assertEqual(r["uncertain"], 1)
        view = swr.swr_health(r)
        titles = [s["title"] for s in view["web_sections"]]
        self.assertIn("Match not certain (1)", titles)
        self.assertFalse(any(t.startswith("Not in SolarWinds") for t in titles))
        says = view["web_sections"][0]
        row = next(x for x in says["rows"] if x[1] == "db01")
        self.assertEqual(row[2:7], ["?"] * 5)
        tile = says["boxes"][0]
        self.assertEqual((tile["value"], tile["note"], tile["status"]), ("1+", "1 host not certain", "critical"))
        self.assertIn("(1 host not certain)", view["lines"][0])
        self.assertIn("The counts are of the matched hosts only (1 host not certain).", says["note"])
        self.assertNotIn("Not in SolarWinds", " ".join(view["lines"]))
        t = swr.swr_troubleshoot(self.read([HOSTS[2]]))
        g = t["web_sections"][0]["glance"][0]
        self.assertEqual((g["label"], g["status"]), ("Not certain", "info"))
        self.assertIn("db01: match not certain.", t["lines"])

    def test_e_port_events_not_checked(self):
        self.db.core["topology_ok"] = False
        r = self.read()
        self.assertFalse(r["port_events_read"])
        self.assertTrue(r["events"])
        self.assertTrue(any("switch port events" in line for line in r["not_read"]))
        t = swr.swr_troubleshoot(r)
        g = next(x for x in t["web_sections"][0]["glance"] if x["name"] == "Events (24 h)")
        self.assertTrue(g["label"].endswith("+"), g)
        self.assertIn("Switch port events not read.", g["text"])
        self.assertIn("(switch port events not read)", t["lines"][0])


class Review1970f(unittest.TestCase):
    """The director's read of Codex's check of 2923b32a: the other totals of a job with uncertain hosts are floors too."""

    def test_application_and_volume_totals_with_uncertain_hosts(self):
        db = Snap()
        db.notes["ip"] = {"what": "ip", "why": "timeout", "absent": False}
        db.nodes_for_hosts = lambda *args: [N(101, "web01.example.mil"), N(2, "other", ip="192.0.2.22")]
        r = hs.read([HOSTS[0], HOSTS[2]], {"mode": "health", "now": NOW}, object(), db)
        r["sam"] = True
        r["hosts"][0].update(apps=[], volumes=[])
        view = swr.swr_health(r)
        line = view["lines"][0]
        self.assertIn("0+ applications not up", line)
        self.assertIn("0+ volumes over 90 %", line)
        self.assertNotIn("0 applications", line)
        self.assertNotIn("0 volumes", line)
        self.assertEqual(view["sections"][0]["lines"][0], line)          # the email says the same
        says = view["web_sections"][0]
        self.assertIn("matched hosts only", says["note"])
        sec = next(s for s in view["web_sections"] if s["title"].startswith("Match not certain"))
        self.assertIn("applications and volumes", sec["text"])
        r["hosts"][1] = dict(r["hosts"][0], host="web02.example.mil")     # every host matched: plain counts
        r["hosts"][1]["node"] = dict(r["hosts"][0]["node"], node_id=103)
        self.assertIn("0 applications not up", swr.swr_health(r)["lines"][0])


def vim_conn(hosts=(), vms=(), centers=None, column=True):
    """site_swis_db's VIM reads on a FakeConn: hosts (vim_id, name, node_id, vcenter), vms (vim_id, name, node_id, vcenter)."""
    from test_solarwinds_snapshot import FakeConn
    cols = [(k,) for k in ("vim_id", "name", "name_key", "status", "connection_state", "power_state", "cluster", "node_id", "vcenter")]

    def ans(sql, args):
        if "information_schema.columns" in sql:
            return [("n",)], [(1 if column else 0,)]
        if "DISTINCT vcenter" in sql:
            return [("vcenter",)], [(c,) for c in (centers if centers is not None else sorted(set(h[3] for h in hosts if h[3])))]
        src = hosts if "FROM sw_vim_host" in sql else vms
        return cols, [(i, n, hs.sdb.name_key(n), 1, "", "", "", nid, vc) for i, n, nid, vc in src]
    return FakeConn(ans)


class Review1113(unittest.TestCase):
    """Codex's 5 VMware/VIM findings of rev-1970c, and the director's vCenter rule (1.113.0): a test each, failing on 35d1287e."""

    def read(self, objs, snap):
        r = hs.read(objs, {"mode": "vcenter", "now": NOW}, object(), snap)
        self.assertTrue(r["ok"], r["why"])
        return r

    # P1-1: the VIM object of the job's vCenter only
    def test_vim_link_only_in_the_jobs_vcenter(self):
        c = vim_conn(vms=[(1, "payroll", 41, "vc-a.example.mil")], centers=["vc-a.example.mil", "vc-b.example.mil"])
        got = hs.sdb.vim_for(c, 7, [{"name": "payroll", "kind": "vm", "vcenter": "vc-b.example.mil"},
                                     {"name": "payroll", "kind": "vm", "vcenter": "vc-a"}])
        self.assertIsNone(got[hs.sdb.vim_key("vm", "vc-b.example.mil", "payroll")])          # vc-a's VM is not vc-b's
        self.assertEqual(got[hs.sdb.vim_key("vm", "vc-a", "payroll")]["node_id"], 41)        # the short name fits one vCenter
        snap = Snap()
        snap.vim_for = lambda conn, sid, names: hs.sdb.vim_for(c, sid, names)
        snap.nodes_for_hosts = lambda *args: [N(41, "payroll")]
        snap.vim_nodes = lambda *args: [dict(N(41, "payroll"), kind="vm", vim_id=1)]
        r = self.read([{"host": "payroll", "kind": "vm", "vcenter": "vc-b.example.mil"}], snap)
        self.assertIsNone(r["hosts"][0]["node"])                                             # nor by its name: VIM's node is vc-a's

    def test_the_vcenter_name_rule(self):
        same = hs.sdb.same_center
        two = ["vc01.a.example.mil", "vc02.a.example.mil"]
        self.assertTrue(same("VC01.A.example.mil.", "vc01.a.example.mil", two))
        self.assertTrue(same("vc01", "vc01.a.example.mil", two))
        self.assertTrue(same("vc01.a.example.mil", "vc01", ["vc01", "vc02"]))
        self.assertFalse(same("vc01", "vc01.a.example.mil", two + ["vc01.b.example.mil"]))   # two fit: doubtful, no link
        self.assertFalse(same("vc01.b.example.mil", "vc01.a.example.mil", two))
        self.assertFalse(same("192.0.2.5", "vc01.a.example.mil", two))
        self.assertFalse(same("", "vc01.a.example.mil", two))

    def test_an_old_snapshot_is_not_verifiable(self):
        for c in (vim_conn(hosts=[(1, "esx01.example.mil", 11, None)]), vim_conn(column=False)):
            got = hs.sdb.vim_for(c, 7, [{"name": "esx01.example.mil", "kind": "host", "vcenter": "vc01.example.mil"}])
            e = got[hs.sdb.vim_key("host", "vc01.example.mil", "esx01.example.mil")]
            self.assertIsNone(e["node_id"])
            self.assertTrue(e["why"].startswith("VIM not verifiable"), e)
            snap = Snap()
            snap.vim_for = lambda conn, sid, names, c=c: hs.sdb.vim_for(c, sid, names)
            snap.nodes_for_hosts = lambda *args: [N(11, "esx01.example.mil")]
            r = self.read([{"host": "esx01.example.mil", "kind": "host", "vcenter": "vc01.example.mil"}], snap)
            self.assertIsNone(r["hosts"][0]["node"])                       # never a guess by the name either
            self.assertTrue(r["hosts"][0]["unmatched_why"].startswith("VIM not verifiable"))
            self.assertTrue(any(x.startswith("VIM not verifiable") for x in r["not_read"]))

    # P1-2: the full name, never a short one (test_vim_full_name_only above too)
    def test_a_short_name_is_never_vims_proof(self):
        c = vim_conn(vms=[(1, "payroll.backup", 41, "vc01")])
        got = hs.sdb.vim_for(c, 7, [{"name": "payroll", "kind": "vm", "vcenter": "vc01"}])
        self.assertIsNone(got[hs.sdb.vim_key("vm", "vc01", "payroll")])

    # P1-3: a node linked to two VIM objects is neither's
    def test_a_node_of_two_vim_objects(self):
        snap = Snap()
        snap.vim_for = lambda *args: {hs.sdb.vim_key("vm", "vc01", "payroll-copy"): {"node_id": 41, "kind": "vm", "why": ""}}
        snap.nodes_for_hosts = lambda *args: []
        snap.vim_nodes = lambda *args: [dict(N(41, "payroll"), kind="vm", vim_id=1), dict(N(41, "payroll"), kind="vm", vim_id=2)]
        r = self.read([{"host": "payroll-copy", "kind": "vm", "vcenter": "vc01"}], snap)
        self.assertEqual((r["hosts"][0]["node"], r["hosts"][0]["unmatched_why"]), (None, "the VIM node could not be identified"))

    # P1-4: an address counted over every node before VIM's nodes are set aside
    def test_an_address_shared_with_a_vim_node(self):
        snap = Snap()
        snap.vim_for = lambda *args: {}
        snap.nodes_for_hosts = lambda *args: [N(50, "guest50", ip="192.0.2.50"), N(51, "other51", ip="192.0.2.50")]
        snap.vim_nodes = lambda *args: [dict(N(50, "guest50", ip="192.0.2.50"), kind="vm", vim_id=7)]
        r = self.read([{"host": "192.0.2.50", "kind": "host", "vcenter": "vc01"}], snap)
        self.assertIsNone(r["hosts"][0]["node"])
        self.assertIn("192.0.2.50", r["hosts"][0]["unmatched_why"])

    # P1-5: a name VIM also gives an object of the other kind, or VIM not read: no node by the name
    def test_the_kind_must_be_known(self):
        snap = Snap()
        snap.vim_for = lambda *args: {hs.sdb.vim_key("host", "vc01", "esx01"): {"node_id": None, "kind": "host", "other_kind": True, "why": ""}}
        snap.nodes_for_hosts = lambda *args: [N(9, "esx01")]
        r = self.read([{"host": "esx01", "kind": "host", "vcenter": "vc01"}], snap)
        self.assertIsNone(r["hosts"][0]["node"])
        self.assertIn("VIM has a VM of this name too", r["hosts"][0]["unmatched_why"])
        snap = Snap()
        snap.vim_for = lambda *args: {}
        snap.nodes_for_hosts = lambda *args: [N(9, "esx01")]
        snap.notes["vim"] = {"what": "vim", "why": "timeout", "absent": False}
        r = self.read([{"host": "esx01", "kind": "host", "vcenter": "vc01"}], snap)
        self.assertIsNone(r["hosts"][0]["node"])
        self.assertIn("VIM was not read", r["hosts"][0]["unmatched_why"])
        snap.notes["vim"]["absent"] = True                                   # no VIM on this Orion: the name is all there is
        self.assertEqual(self.read([{"host": "esx01", "kind": "host", "vcenter": "vc01"}], snap)["hosts"][0]["node"]["node_id"], 9)


if __name__ == "__main__":
    unittest.main()
