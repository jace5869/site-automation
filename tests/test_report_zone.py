#!/usr/bin/env python3
"""report_time_zone in the reports that printed their own UTC times (1.54.0): the AD reports (admin, reader, not
read), account lockout, VMware capacity, ONTAP, log analysis and the VM outage watch. With a zone each time shown is
in it, CET or CEST by its own date ("2026-11-13 05:37 CET", "2026-07-13 06:37 CEST"), and a "(UTC)" column header
loses its "(UTC)" (each cell says its zone); "" and a zone the EE does not know give the report as before, byte for
byte. Also plugins/module_utils/site_time.py's local() with seconds and ISO, and column().
Standard library only: python3 tests/test_report_zone.py"""
import copy
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
import site_filters as sf  # noqa: E402
import test_ad as TA  # noqa: E402
import test_ad_reader as TR  # noqa: E402
import test_ad_lockout as TL  # noqa: E402
import test_capacity as TC  # noqa: E402
import test_ontap as TO  # noqa: E402
import test_log_analysis as TLA  # noqa: E402
import test_vm_watch as TW  # noqa: E402

BERLIN = "Europe/Berlin"
UNKNOWN = "Nowhere/Atlantis"
WINTER = "2026-11-13T04:37:00Z"           # CET, UTC+1
SUMMER = "2026-07-13T04:37:00Z"           # CEST, UTC+2
tm = sf._time
to_local = getattr(tm, "local")          # not an attribute chain: check_public reads that as a host name


def same_without_zone(test, build):
    """build(opts) -> report: no option, "", UTC and an unknown zone give the same report."""
    base = build({})
    for z in ("", "UTC", UNKNOWN):
        test.assertEqual(build({"time_zone": z}), base, z)
    return base


class SiteTime(unittest.TestCase):
    def test_local_minutes_seconds_iso(self):
        tz = tm.zone(BERLIN)[0]
        self.assertEqual(to_local("2026-11-13 04:37 UTC", tz), "2026-11-13 05:37 CET")
        self.assertEqual(to_local("2026-11-13 04:37", tz), "2026-11-13 05:37 CET")
        self.assertEqual(to_local("2026-07-13 04:37:09", tz), "2026-07-13 06:37:09 CEST")
        self.assertEqual(to_local("2026-07-13T04:37:09Z", tz), "2026-07-13 06:37:09 CEST")
        self.assertEqual(to_local("2026-07-13T04:37:09.123456Z", tz), "2026-07-13 06:37:09 CEST")
        self.assertEqual(to_local("2026-07-13T04:37:09+00:00", tz), "2026-07-13 06:37:09 CEST")
        for t in ("-", "", "?", "this run", "2026-07-13", "2026-07-13 04:37 EDT", "2026-07-13T04:37:09-04:00"):
            self.assertEqual(to_local(t, tz), t)                                   # not a UTC time: as it is
        for t in ("2026-11-13 04:37 UTC", "2026-07-13T04:37:09Z", "x"):
            self.assertEqual(to_local(t, None), t)                                 # no zone: as it is

    def test_dst_change_day(self):
        tz = tm.zone(BERLIN)[0]                                                    # 2026-10-25 01:00 UTC: CEST -> CET
        self.assertEqual(to_local("2026-10-25 00:59 UTC", tz), "2026-10-25 02:59 CEST")
        self.assertEqual(to_local("2026-10-25 01:00 UTC", tz), "2026-10-25 02:00 CET")

    def test_column(self):
        tz = tm.zone(BERLIN)[0]
        self.assertEqual(tm.column("When (UTC)", tz), "When")
        self.assertEqual(tm.column("When (UTC)", None), "When (UTC)")
        self.assertEqual(tm.column("DC", tz), "DC")

    def test_report_time_local_filter(self):
        self.assertEqual(sf.report_time_local("2026-07-13T04:37:09Z", BERLIN), "2026-07-13 06:37:09 CEST")
        self.assertEqual(sf.report_time_local("2026-07-13T04:37:09Z", ""), "2026-07-13T04:37:09Z")
        self.assertEqual(sf.report_time_local("2026-07-13T04:37:09Z", UNKNOWN), "2026-07-13T04:37:09Z")
        self.assertEqual(sf.report_time_column("Taken (UTC)", BERLIN), "Taken")
        self.assertEqual(sf.report_time_column("Taken (UTC)", ""), "Taken (UTC)")
        self.assertEqual(sf.report_time_column("Taken (UTC)", UNKNOWN), "Taken (UTC)")


class ActiveDirectory(unittest.TestCase):
    def admin(self, opts, now=None):
        return TA.report([TA.dc("dc_ok", "dc01.corp.test"), TA.dc("dc_broken", "dc02.corp.test")], [TA.dom("domain_changes")],
                         **dict(opts, **({"now": now} if now else {})))["report"]

    def test_admin_mode(self):
        base = same_without_zone(self, self.admin)
        self.assertTrue(base["subtitle"].startswith("Read 2026-10-15 06:53 UTC - changes of the last 24 h"), base["subtitle"])
        r = self.admin({"time_zone": BERLIN})
        self.assertTrue(r["subtitle"].startswith("Read 2026-10-15 08:53 CEST - changes of the last 24 h"), r["subtitle"])
        self.assertTrue(self.admin({"time_zone": BERLIN}, WINTER)["subtitle"].startswith("Read 2026-11-13 05:37 CET - "))
        self.assertNotIn("UTC", r["subtitle"])
        # every time in the tables follows: a cell that was '2026-10-15 05:53' is '2026-10-15 07:53 CEST'
        cells = [str(c) for s in base["sections"] for row in s.get("rows") or [] for c in row]
        zcells = [str(c) for s in r["sections"] for row in s.get("rows") or [] for c in row]
        times = [c for c in cells if tm.STORED.match(c)]
        self.assertTrue(times)
        tz = tm.zone(BERLIN)[0]
        self.assertEqual(sorted(to_local(c, tz) for c in times), sorted(c for c in zcells if c.endswith((" CET", " CEST"))))
        self.assertFalse([c for c in zcells if tm.STORED.match(c)])                 # no bare UTC time left

    def test_reader_mode(self):
        def build(opts):
            return TR.judge(TR.reader(), **opts)["report"]
        base = same_without_zone(self, build)
        self.assertTrue(base["subtitle"].startswith("Read 2026-11-10 09:00 UTC - reader mode: "), base["subtitle"])
        self.assertTrue(build({"time_zone": BERLIN})["subtitle"].startswith("Read 2026-11-10 10:00 CET - reader mode: "))
        r = TR.judge(TR.reader(), now=SUMMER, time_zone=BERLIN)["report"]
        self.assertTrue(r["subtitle"].startswith("Read 2026-07-13 06:37 CEST - reader mode: "), r["subtitle"])

    def test_not_read(self):
        def build(opts):
            return TR.judge(TR.reader(ok=False, why="no DC answered"), **opts)["report"]
        base = same_without_zone(self, build)
        self.assertEqual(base["subtitle"], "Read 2026-11-10 09:00 UTC - reader mode")
        self.assertEqual(build({"time_zone": BERLIN})["subtitle"], "Read 2026-11-10 10:00 CET - reader mode")


class Lockout(unittest.TestCase):
    def build(self, opts):
        return TL.report(**opts)

    def test_zone(self):
        base = same_without_zone(self, self.build)
        self.assertEqual(base["report"]["subtitle"], "Read 2026-10-20 18:54 UTC - the Security logs of the last 24 h")
        r = self.build({"time_zone": BERLIN})
        self.assertEqual(r["report"]["subtitle"], "Read 2026-10-20 20:54 CEST - the Security logs of the last 24 h")
        text = repr(r["report"])
        self.assertNotIn("UTC", text)
        self.assertIn("UTC", repr(base["report"]))
        cols = [c for s in r["report"]["sections"] for c in s.get("columns") or []]
        self.assertIn("When", cols)
        self.assertNotIn("When (UTC)", cols)
        every = same_without_zone(self, lambda o: self.build(dict(o, asked=False)))          # no account asked: every lockout
        self.assertIn("Locked out since (UTC)", [c for s in every["report"]["sections"] for c in s.get("columns") or []])
        r2 = self.build({"time_zone": BERLIN, "asked": False})["report"]
        sec = [s for s in r2["sections"] if "Locked out since" in (s.get("columns") or [])][0]
        self.assertTrue(all(row[2].endswith((" CET", " CEST")) for row in sec["rows"]), sec["rows"])
        lines = [ln for a in r["accounts"] for ln in a["lines"]]
        self.assertTrue(any(" since 2026-10-20 " in ln and "CEST" in ln for ln in lines), lines)
        # what the next steps read stays UTC: the accounts' lockout time and sources
        self.assertEqual([a["lockout_time"] for a in r["accounts"]], [a["lockout_time"] for a in base["accounts"]])
        self.assertEqual([a["sources"] for a in r["accounts"]], [a["sources"] for a in base["accounts"]])

    def test_winter(self):
        r = self.build({"time_zone": BERLIN, "now": WINTER})
        self.assertTrue(r["report"]["subtitle"].startswith("Read 2026-11-13 05:37 CET - "))


class Capacity(unittest.TestCase):
    def build(self, opts, read_at=None):
        d = copy.deepcopy(TC.DATA)
        if read_at:
            d["read_at"] = read_at
        return TC.cf.vm_capacity_report(d, dict(TC.OPTS, **opts))["report"]

    def test_zone(self):
        base = same_without_zone(self, self.build)
        self.assertIn(" - read 2026-09-21 14:13 UTC", base["subtitle"])
        self.assertIn(" - read 2026-09-21 16:13 CEST", self.build({"time_zone": BERLIN})["subtitle"])
        self.assertIn(" - read 2026-11-13 05:37 CET", self.build({"time_zone": BERLIN}, WINTER)["subtitle"])
        self.assertEqual(self.build({"time_zone": BERLIN})["sections"], base["sections"])   # no other time shown


class Ontap(unittest.TestCase):
    def col(self, read_at="2026-10-07T12:00:00Z"):
        alert = {"node": "n1", "perceived_severity": "major", "alert_id": "DualPathToDiskShelf_Alert", "probable_cause": "one path",
                 "indication_time": "2026-10-07T10:00:00Z"}
        ems = {"node": {"name": "n1"}, "message": {"name": "callhome.battery.low", "severity": "error"}, "time": "2026-10-07T11:00:00Z",
               "log_message": "battery low"}
        c = TO.Report.one([("v", 100, 10)], [alert], [ems])
        c["read_at"] = read_at
        return c

    def build(self, opts, read_at="2026-10-07T12:00:00Z"):
        return TO.of.ontap_report(self.col(read_at), opts)["report"]

    def test_zone(self):
        base = same_without_zone(self, self.build)
        self.assertEqual(base["subtitle"][:25], "Read 2026-10-07 12:00 UTC")
        r = self.build({"time_zone": BERLIN})
        self.assertEqual(r["subtitle"][:26], "Read 2026-10-07 14:00 CEST")
        self.assertTrue(self.build({"time_zone": BERLIN}, WINTER)["subtitle"].startswith("Read 2026-11-13 05:37 CET"))
        sec = {s["title"]: s for s in r["sections"]}
        alerts = sec["Health alerts"]
        self.assertEqual(alerts["columns"][-1], "Since")
        self.assertEqual(alerts["rows"][0][-1], "2026-10-07 12:00 CEST")
        ev = [s for t, s in sec.items() if t.startswith("Error events")][0]
        self.assertIn("Last", ev["columns"])
        self.assertIn("2026-10-07 13:00 CEST", ev["rows"][0])
        bsec = {s["title"]: s for s in base["sections"]}
        self.assertEqual(bsec["Health alerts"]["columns"][-1], "Since (UTC)")
        self.assertEqual(bsec["Health alerts"]["rows"][0][-1], "2026-10-07 10:00")
        for s in r["sections"]:                                    # a wrapped column keeps its wrap under the new header
            for w in s.get("wrap") or []:
                self.assertIn(w, s["columns"], s["title"])


class LogAnalysis(unittest.TestCase):
    def test_window_label(self):
        n = TLA.NOW
        for z in ("", UNKNOWN):
            self.assertEqual(TLA.lf.log_window(24, "2026-10-23 05:00", "2026-10-23 06:30", n, zone=z),
                             TLA.lf.log_window(24, "2026-10-23 05:00", "2026-10-23 06:30", n))
        w = TLA.lf.log_window(24, "2026-10-23 05:00", "2026-10-23 06:30", n, zone=BERLIN)
        self.assertEqual(w["label"], "2026-10-23 07:00 CEST to 2026-10-23 08:30 CEST (2026-10-23 05:00 to 2026-10-23 06:30 UTC)")
        self.assertEqual((w["begin"], w["end"]), ("2026-10-23T05:00:00Z", "2026-10-23T06:30:00Z"))   # what is read: UTC
        w = TLA.lf.log_window(24, "2026-10-23 05:00", "", n, zone=BERLIN)
        self.assertEqual(w["label"], "2026-10-23 07:00 CEST to now (2026-10-23 05:00 to now UTC)")
        w = TLA.lf.log_window(4, "", "2026-10-23 06:30", n, zone=BERLIN)
        self.assertEqual(w["label"], "the last 4 h before 2026-10-23 08:30 CEST (2026-10-23 06:30 UTC)")
        self.assertEqual(TLA.lf.log_window(24, "", "", n, zone=BERLIN)["label"], "the last 24 h")
        w = TLA.lf.log_window(24, "2026-10-01 05:00", "2026-10-01 06:00", n + 40 * 86400, zone=BERLIN)
        self.assertTrue(w["label"].startswith("2026-10-01 07:00 CEST to"), w["label"])

    def test_report_cells(self):
        data = {"hosts": [{"host": "web01", "source": "journal", "lines": 3, "patterns": 1, "reachable": True}],
                "problems": [{"id": "P1", "severity": "error", "hosts": ["web01"], "source": "journal", "count": 3,
                              "first": "2026-11-13T04:37:09Z", "last": "2026-07-13T04:37:09Z", "problem": "disk full"}],
                "patterns": 1, "lines": 3, "error": 1}

        def build(opts):
            return TLA.lf.log_report(data, None, dict({"window": "the last 24 h"}, **opts))
        base = same_without_zone(self, build)
        row = [s for s in base["sections"] if s["title"].startswith("Patterns")][0]["rows"][0]
        self.assertEqual(row[5:7], ["2026-11-13 04:37:09", "2026-07-13 04:37:09"])
        r = build({"time_zone": BERLIN})
        row = [s for s in r["sections"] if s["title"].startswith("Patterns")][0]["rows"][0]
        self.assertEqual(row[5:7], ["2026-11-13 05:37:09 CET", "2026-07-13 06:37:09 CEST"])


class OutageWatch(unittest.TestCase):
    def build(self, opts, read_at=None):
        d = TW.data(suspects=[TW.suspect("app01", "hung")])
        if read_at:
            d["read_at"] = read_at
        cs = TW.w.vm_watch_conditions(d)
        return TW.w.vm_watch_report(d, cs, TW.w.vm_watch_actions(cs, {"aap_known": True}), opts)

    def test_zone(self):
        base = same_without_zone(self, self.build)
        self.assertTrue(base["subtitle"].startswith("Read 2026-10-23T10:02:00Z UTC: "), base["subtitle"])
        self.assertTrue(self.build({"time_zone": BERLIN})["subtitle"].startswith("Read 2026-10-23 12:02 CEST: "))
        self.assertTrue(self.build({"time_zone": BERLIN}, WINTER)["subtitle"].startswith("Read 2026-11-13 05:37 CET: "))
        self.assertEqual(self.build({}), self.build(None))          # no options at all, as the role called it before


if __name__ == "__main__":
    unittest.main()
