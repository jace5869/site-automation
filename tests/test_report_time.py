#!/usr/bin/env python3
"""Times and clocks in the VMware health and alarms reports (plugins/filter/vmware_health_filters.py,
vmware_alarm_filters.py): UTC as before without report_time_zone; with it each time in that zone first
(summer time as it was on that day), then UTC's; a zone the EE does not know -> UTC and a note; the ACT
evidence stays UTC. The health report's host clocks against vCenter's, the NTP setting, vCenter's clock,
and when a host not connected lost vCenter. Standard library only: python3 tests/test_report_time.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "filter"))
import vmware_alarm_filters as va  # noqa: E402
import vmware_health_filters as vh  # noqa: E402

VC = "vc01.example.mil"
NY = "America/New_York"


def host(name, **kw):
    h = {"vcenter": VC, "datacenter": "DC1", "cluster": "CL1", "standalone": False, "name": name, "moid": "host-" + name,
         "connection": "connected", "power": "poweredOn", "maintenance": False, "standby": "none", "status": "green",
         "ha_state": "", "cpu_used_mhz": 1000, "cpu_mhz": 64000, "mem_used_mb": 1000, "mem_mb": 524288, "uptime_days": 10.0,
         "reboot_required": False, "version": "8.0.2", "build": "1", "vendor": "", "model": "", "sensors_bad": [],
         "config_issues": [], "alarms": [], "clock_offset_s": 0.4, "ntp_servers": ["ntp1.example.mil"], "clock_protocol": "ntp",
         "ntp_running": True, "down_since": "", "down_event": ""}
    h.update(kw)
    return h


def data(hosts, **vc):
    info = {"vcenter": VC, "version": "8.0.3", "build": "1", "name": "VMware vCenter Server 8.0.3", "alarms": [],
            "vcls_readable": True, "clock_offset_s": 0.2}
    info.update(vc)
    return {"vcenter": VC, "vcenters": [{"name": VC, "ok": True, "error": "", "note": ""}], "read_at": "2026-10-26T02:30:00Z",
            "vcenter_info": [info], "datacenters": [{"vcenter": VC, "name": "DC1", "moid": "datacenter-1", "status": "green",
                                                     "config_issues": [], "alarms": []}],
            "clusters": [], "hosts": hosts}


def problems(d, **o):
    return [(p["severity"], p["object"], p["problem"]) for p in vh.vm_health_problems(d, o)]


class Times(unittest.TestCase):
    def test_utc_as_before(self):
        """No report_time_zone (or UTC): the words of before - 'read ... UTC', 'since ... UTC'."""
        d = data([host("esx01", alarms=[{"alarm": "Host memory usage", "status": "yellow", "time": "2026-10-05T03:52:47Z", "acknowledged": False}])])
        for o in ({}, {"time_zone": ""}, {"time_zone": "UTC"}, {"time_zone": "Etc/UTC"}):
            r = vh.vm_health_report(d, o)
            self.assertTrue(r["subtitle"].endswith("- read 2026-10-26 02:30:00 UTC"), r["subtitle"])
            self.assertIn(("warning", "esx01", "Alarm: Host memory usage (since 2026-10-05 03:52:47 UTC)"), problems(d, **o))

    def test_zone_and_summer_time(self):
        """Each time as it was on its day in that zone, then UTC's; the date of UTC when it differs."""
        w = vh._zone(NY)[0]
        self.assertEqual(vh._when("2026-03-08T06:30:00Z", w), "2026-03-08 01:30:00 EST (06:30 UTC)")    # before the change
        self.assertEqual(vh._when("2026-03-08T07:30:00Z", w), "2026-03-08 03:30:00 EDT (07:30 UTC)")    # after it
        self.assertEqual(vh._when("2026-11-01T05:30:00Z", w), "2026-11-01 01:30:00 EDT (05:30 UTC)")    # back: 1:30 twice
        self.assertEqual(vh._when("2026-11-01T06:30:00Z", w), "2026-11-01 01:30:00 EST (06:30 UTC)")
        self.assertEqual(vh._when("2026-10-26T02:30:00Z", w), "2026-10-25 22:30:00 EDT (2026-10-26 02:30 UTC)")
        b = vh._zone("Europe/London")[0]
        self.assertEqual(vh._when("2026-10-24T12:00:00Z", b), "2026-10-24 13:00:00 BST (12:00 UTC)")
        self.assertEqual(vh._when("2026-10-26T12:00:00Z", b), "2026-10-26 12:00:00 GMT (12:00 UTC)")
        self.assertEqual(vh._when("", w), "")
        d = data([host("esx01", alarms=[{"alarm": "Host memory usage", "status": "yellow", "time": "2026-01-05T03:52:47Z", "acknowledged": True}])])
        r = vh.vm_health_report(d, {"time_zone": NY})
        self.assertTrue(r["subtitle"].endswith("- read 2026-10-25 22:30:00 EDT (2026-10-26 02:30 UTC)"), r["subtitle"])
        self.assertIn(("warning", "esx01", "Alarm: Host memory usage, acknowledged (since 2026-01-04 22:52:47 EST (2026-01-05 03:52 UTC))"),
                      problems(d, time_zone=NY))

    def test_unknown_zone(self):
        """Not a time zone (or no time zone data in the EE): UTC, and the subtitle says why."""
        d = data([host("esx01")])
        for name in ("Mars/Olympus", "../etc/passwd", "EST5EDT/x"):
            r = vh.vm_health_report(d, {"time_zone": name})
            self.assertIn("read 2026-10-26 02:30:00 UTC - report_time_zone %s is not a time zone this execution environment knows" % name,
                          r["subtitle"])
        a = va.vm_alarm_report({"vcenter": VC, "read_at": "2026-10-07T12:00:00Z", "window_hours": 0}, {"time_zone": "Mars/Olympus"})
        self.assertIn("read 2026-10-07 12:00:00 UTC - report_time_zone Mars/Olympus is not a time zone", a["subtitle"])

    def test_down_since(self):
        """A host not connected: since vCenter's event of it losing vCenter, in the zone too; none read: no time."""
        d = data([host("esx01", connection="notResponding", down_since="2026-10-25T14:03:12Z", clock_offset_s=None),
                  host("esx02", connection="disconnected", clock_offset_s=None),
                  host("esx03", connection="disconnected", down_since="2026-10-25T15:00:00Z", clock_offset_s=None)])
        ps = problems(d)
        self.assertIn(("critical", "esx01", "Not responding to vCenter since 2026-10-25 14:03:12 UTC (vCenter's event)"), ps)
        self.assertIn(("critical", "esx02", "Disconnected from vCenter"), ps)
        self.assertIn(("critical", "esx03", "Disconnected from vCenter since 2026-10-25 15:00:00 UTC (vCenter's event)"), ps)
        self.assertIn(("critical", "esx01", "Not responding to vCenter since 2026-10-25 10:03:12 EDT (14:03 UTC) (vCenter's event)"),
                      problems(d, time_zone=NY))


class Clocks(unittest.TestCase):
    def clock_problems(self, d, **o):
        return [(s, ob, p) for s, ob, p in problems(d, **o) if "lock" in p or "NTP" in p]

    def test_thresholds(self):
        d = data([host("esx01", clock_offset_s=59.9), host("esx02", clock_offset_s=-75), host("esx03", clock_offset_s=301.2),
                  host("esx04", clock_offset_s=None), host("esx05", clock_offset_s=3725)])
        ps = self.clock_problems(d)
        self.assertEqual([(s, o) for s, o, _ in ps], [("critical", "esx03"), ("critical", "esx05"), ("warning", "esx02")])
        self.assertIn("Clock behind by 1 min 15 s against vCenter's", ps[2][2])
        self.assertIn("Clock ahead by 5 min 1 s against vCenter's", ps[0][2])
        self.assertIn("Clock ahead by 1 h 2 min 5 s against vCenter's", ps[1][2])
        fixes = {p["object"]: p["fix"] for p in vh.vm_health_problems(d, {})}
        self.assertIn("Active Directory logins fail from 5 minutes off", fixes["esx03"])
        self.assertNotIn("Active Directory", fixes["esx02"])
        # the thresholds are settings; 0 = that level off
        self.assertEqual([(s, o) for s, o, _ in self.clock_problems(d, clock_warn_seconds=30, clock_crit_seconds=0)],
                         [("warning", "esx01"), ("warning", "esx02"), ("warning", "esx03"), ("warning", "esx05")])

    def test_ntp(self):
        d = data([host("esx01", ntp_servers=[]), host("esx02", ntp_running=False), host("esx03", ntp_servers=None, ntp_running=False),
                  host("esx04", ntp_servers=[], clock_protocol="ptp"), host("esx05", ntp_running=None),
                  host("esx06", connection="notResponding", ntp_servers=[], clock_offset_s=900)])
        ps = self.clock_problems(d)
        self.assertEqual([(s, o) for s, o, _ in ps], [("warning", "esx01"), ("warning", "esx02")])
        self.assertTrue(ps[0][2].startswith("No NTP server set"))
        self.assertEqual(ps[1][2], "NTP service not running (servers ntp1.example.mil): its clock drifts")

    def test_vcenter_clock(self):
        self.assertEqual([p for p in problems(data([host("esx01")], clock_offset_s=59)) if p[1] == VC], [])
        self.assertEqual([p for p in problems(data([host("esx01")], clock_offset_s=None)) if p[1] == VC], [])
        ps = [p for p in problems(data([host("esx01")], clock_offset_s=-130)) if p[1] == VC]
        self.assertEqual(ps, [("warning", VC, "vCenter's clock and the AAP job's differ: vCenter's is behind by 2 min 10 s, so its "
                                              "event and alarm times are off by as much (or the AAP node's clock is)")])

    def test_hosts_table(self):
        d = data([host("esx01", clock_offset_s=2.4), host("esx02", clock_offset_s=-75), host("esx03", clock_offset_s=None),
                  host("esx04", connection="disconnected", clock_offset_s=None)])
        sec = [s for s in vh.vm_health_report(d, {})["sections"] if s["title"] == "Hosts"][0]
        i = sec["columns"].index("Clock")
        self.assertEqual(sec["columns"][i - 1:i + 2], ["Up (days)", "Clock", "ESXi"])
        cells = {r[1]: (r[i], sec["cell_status"][n][i]) for n, r in enumerate(sec["rows"])}
        self.assertEqual(cells, {"esx01": ("+2 s", ""), "esx02": ("-1 min 15 s", "warning"), "esx03": ("?", ""), "esx04": ("-", "")})
        self.assertIn("? = vCenter would not say", sec["text"])


class AlarmsReport(unittest.TestCase):
    D = {"vcenter": VC, "read_at": "2026-10-07T12:00:00Z", "window_hours": 24.0,
         "alarms": [{"entity_type": "host", "entity": "esx01", "moid": "host-1", "cluster": "CL1", "datacenter": "DC1",
                     "alarm": "Host connection and power state", "status": "red", "severity": "critical", "time": "2026-10-07T03:52:47Z",
                     "acknowledged": True, "acknowledged_by": "CORP\\op", "acknowledged_time": "2026-10-07T04:00:00Z"}],
         "hosts": [{"name": "esx01", "moid": "host-1", "cluster": "CL1", "datacenter": "DC1", "connection": "notResponding",
                    "maintenance": False, "version": "8.0.2", "build": "1", "vendor": "", "model": ""}],
         "events": [{"key": 10, "time": "2026-10-07T03:52:40Z", "type": "HostNotRespondingEvent", "group": "connection", "severity": "critical",
                     "category": "error", "message": "Host esx01 is not responding", "user": "", "login_user": "", "ip": "", "host": "esx01",
                     "vm": "", "cluster": "CL1", "datacenter": "DC1"}]}

    def sec(self, r, title):
        return [s for s in r["sections"] if s["title"] == title][0]

    def test_utc_as_before(self):
        r = va.vm_alarm_report(self.D, {})
        self.assertIn("- read 2026-10-07 12:00:00 UTC", r["subtitle"])
        t = self.sec(r, "Triggered alarms")
        self.assertEqual(t["columns"][4], "Since (UTC)")
        self.assertEqual(t["rows"][0][4:6], ["2026-10-07 03:52:47", "yes - CORP\\op, 2026-10-07 04:00:00"])
        c = self.sec(r, "Host connection")
        self.assertEqual((c["columns"][5], c["rows"][0][5]), ("Last event (UTC)", "2026-10-07 03:52:40"))

    def test_zone(self):
        r = va.vm_alarm_report(self.D, {"time_zone": NY})
        self.assertIn("read 2026-10-07 08:00:00 EDT (12:00 UTC)", r["subtitle"])
        t = self.sec(r, "Triggered alarms")
        self.assertEqual(t["columns"][4], "Since")
        self.assertEqual(t["rows"][0][4:6], ["2026-10-06 23:52:47 EDT (03:52 UTC on 10-07)",
                                             "yes - CORP\\op, 2026-10-07 00:00:00 EDT (04:00 UTC)"])
        c = self.sec(r, "Host connection")
        self.assertEqual((c["columns"][5], c["rows"][0][5]), ("Last event", "2026-10-06 23:52:40 EDT (03:52 UTC on 10-07)"))
        self.assertFalse([col for s in r["sections"] for col in s.get("columns", []) if "(UTC)" in col])

    def test_act_evidence_stays_utc(self):
        """What GenAI reads keeps UTC whatever the report shows."""
        probs = va.vm_alarm_problems(self.D, {"time_zone": NY})
        ev = va.vm_alarm_evidence(self.D, probs["problems"], {"time_zone": NY})
        text = ev if isinstance(ev, str) else repr(ev)
        self.assertIn("read 2026-10-07 12:00:00 UTC", text)
        self.assertNotIn("EDT", text)


if __name__ == "__main__":
    unittest.main()
