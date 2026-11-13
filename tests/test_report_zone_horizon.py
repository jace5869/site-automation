#!/usr/bin/env python3
"""The Horizon reports' times in report_time_zone (1.54.0): plugins/filter/horizon_filters.py (health, events, sessions,
disconnected sessions, logon times, broken desktops, the image push start), horizon_fslogix_filters.py and
horizon_image_filters.py. With opts time_zone set each time shows in that zone, summer time by its date (CET in
November, CEST in July); "" and a name not known here give exactly what no time_zone gives (UTC, as before). What is
handed on stays UTC (a session's "since", the push start's milliseconds); horizon_push_start is typed in UTC and the
plan says so. Standard library only: python3 tests/test_report_zone_horizon.py"""
import copy
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
import horizon_filters as f  # noqa: E402
import horizon_fslogix_filters as fx  # noqa: E402
import horizon_image_filters as hi  # noqa: E402
import test_horizon as th  # noqa: E402
import test_horizon_fslogix as tfx  # noqa: E402
import test_horizon_image as thi  # noqa: E402

BER = "Europe/Berlin"
NOV = 1794544620000          # 2026-11-13 04:37 UTC, milliseconds = 05:37 CET
JUL = 1783917420000          # 2026-07-13 04:37 UTC = 06:37 CEST
H = 3600 * 1000
D = 24 * H


def same_without_zone(test, fn):
    """fn(opts extra) gives the same result with no time_zone, "" and an unknown name."""
    base = fn({})
    test.assertEqual(fn({"time_zone": ""}), base)
    test.assertEqual(fn({"time_zone": "Nowhere/Atlantis"}), base)
    return base


class When(unittest.TestCase):
    def test_when(self):
        tz = f._tz({"time_zone": BER})
        self.assertEqual(f._when(NOV), "2026-11-13 04:37 UTC")
        self.assertEqual(f._when(NOV, tz), "2026-11-13 05:37 CET")
        self.assertEqual(f._when(JUL, tz), "2026-07-13 06:37 CEST")
        self.assertEqual(f._when(0, tz), "")
        self.assertIsNone(f._tz({"time_zone": "Nowhere/Atlantis"}))
        self.assertIsNone(f._tz({}))


class Health(unittest.TestCase):
    def data(self, now):
        return th.data([], [], [], connection_servers=[{"name": "cs1", "status": "OK",
                                                        "certificate": {"valid": True, "valid_to": now + 20 * D}}],
                       license=[{"licensed": True, "expiration_time": now - D}])

    def run_(self, now, extra):
        o = {"now": now}
        o.update(extra)
        return f.hz_health(self.data(now), o)

    def test_cet_cest(self):
        r = self.run_(NOV, {"time_zone": BER})
        texts = [p["text"] for p in r["problems"]]
        self.assertIn("its certificate expires in 20 days (2026-12-03 05:37 CET)", texts)
        self.assertIn("the license expired 2026-11-12 05:37 CET", texts)
        r = self.run_(JUL, {"time_zone": BER})
        texts = [p["text"] for p in r["problems"]]
        self.assertIn("its certificate expires in 20 days (2026-08-02 06:37 CEST)", texts)
        self.assertIn("the license expired 2026-07-12 06:37 CEST", texts)
        cells = str(r["report"]["sections"])
        self.assertIn("2026-08-02 06:37 CEST", cells)
        self.assertNotIn(" UTC", cells)

    def test_no_zone(self):
        base = same_without_zone(self, lambda x: self.run_(NOV, x))
        self.assertIn("the license expired 2026-11-12 04:37 UTC", [p["text"] for p in base["problems"]])


class Events(unittest.TestCase):
    def run_(self, now, extra):
        ev = [{"type": "VLSI_X", "severity": "AUDIT_SUCCESS", "module": "Vlsi", "time": now - H, "message": "User CORP\\adm changed it"},
              {"type": "BROKER_POOL_EMPTY", "severity": "ERROR", "time": now - H, "message": "empty"},
              {"type": "BROKER_USER_AUTHFAILED_BAD_USER_PASSWORD", "severity": "AUDIT_FAIL", "time": now - H,
               "message": "User CORP\\jdoe has failed to authenticate to Horizon from 198.51.100.23"}]
        o = {"now": now, "hours": 24}
        o.update(extra)
        return f.hz_events(th.data([th.IC], audit_events=ev), o)

    def test_cet_cest(self):
        s = self.run_(NOV, {"time_zone": BER})["report"]["sections"]
        self.assertEqual(s[-1]["rows"][0][0], "2026-11-13 04:37 CET")          # an hour before 05:37 CET
        self.assertEqual(s[-2]["rows"][0][3], "2026-11-13 04:37 CET")
        self.assertEqual(s[-3]["rows"][0][4], "2026-11-13 04:37 CET")
        s = self.run_(JUL, {"time_zone": BER})["report"]["sections"]
        self.assertEqual(s[-1]["rows"][0][0], "2026-07-13 05:37 CEST")

    def test_no_zone(self):
        base = same_without_zone(self, lambda x: self.run_(NOV, x))
        self.assertEqual(base["report"]["sections"][-1]["rows"][0][0], "2026-11-13 03:37 UTC")


class Sessions(unittest.TestCase):
    def old(self, now, extra):
        ss = [{"id": "a", "user_id": "S-1", "machine_id": "1", "desktop_pool_id": "p1", "session_state": "DISCONNECTED",
               "disconnected_time": now - 9 * H}]
        o = {"now": now, "hours": 8}
        o.update(extra)
        return f.hz_sessions_old(th.data([th.IC], [th.mach("1", "p1", "DISCONNECTED")], ss), o)

    def test_old_cet_cest(self):
        r = self.old(NOV, {"time_zone": BER})
        self.assertEqual(r["report"]["sections"][0]["rows"][0][3], "2026-11-12 20:37 CET")
        self.assertEqual(r["logoff"][0]["since"], "2026-11-12 19:37 UTC")     # handed on: UTC
        r = self.old(JUL, {"time_zone": BER})
        self.assertEqual(r["report"]["sections"][0]["rows"][0][3], "2026-07-12 21:37 CEST")

    def test_old_no_zone(self):
        base = same_without_zone(self, lambda x: self.old(NOV, x))
        self.assertEqual(base["report"]["sections"][0]["rows"][0][3], "2026-11-12 19:37 UTC")

    def report(self, now, extra):
        ms = [th.mach("1", "p1", "CONNECTED")]
        ss = [{"id": "c", "user_id": "S-1", "machine_id": "1", "desktop_pool_id": "p1", "session_state": "PENDING",
               "start_time": now - H},
              {"id": "d", "user_id": "S-2", "machine_id": "1", "desktop_pool_id": "p1", "session_state": "CONNECTED",
               "start_time": now - 80 * H}]
        o = {"now": now, "long_hours": 72}
        o.update(extra)
        return f.hz_sessions_report(th.data([th.IC], ms, ss), o)

    def test_report(self):
        cells = str(self.report(NOV, {"time_zone": BER})["report"]["sections"])
        self.assertIn("2026-11-13 04:37 CET", cells)                          # pending since an hour
        self.assertIn("2026-11-09 21:37 CET", cells)                          # 80 hours long
        self.assertIn("2026-07-13 05:37 CEST", str(self.report(JUL, {"time_zone": BER})["report"]["sections"]))
        base = same_without_zone(self, lambda x: self.report(NOV, x))
        self.assertIn("2026-11-13 03:37 UTC", str(base["report"]["sections"]))


class Logon(unittest.TestCase):
    def run_(self, now, extra):
        raw = {"logon:a": [{"logon_segment_data": [{"n": "x", "d": 20}]}]}
        ss = [{"id": "a", "user_id": "S-1", "machine_id": "1", "desktop_pool_id": "p1", "session_state": "CONNECTED", "start_time": now}]
        o = {}
        o.update(extra)
        return f.hz_logon(th.data([th.IC], [th.mach("1", "p1", "CONNECTED")], ss, **raw), o)

    def test_logon(self):
        self.assertIn("2026-11-13 05:37 CET", str(self.run_(NOV, {"time_zone": BER})["report"]["sections"]))
        self.assertIn("2026-07-13 06:37 CEST", str(self.run_(JUL, {"time_zone": BER})["report"]["sections"]))
        base = same_without_zone(self, lambda x: self.run_(NOV, x))
        self.assertIn("2026-11-13 04:37 UTC", str(base["report"]["sections"]))


class Desktops(unittest.TestCase):
    def run_(self, now, extra):
        d = th.data([th.IC], [th.mach("8", "p1", "PROVISIONING", operation_time=now - 6 * H)])
        d["machines"][0]["created_ms"] = now - 6 * H
        o = {"now": now, "stuck_hours": 4}
        o.update(extra)
        return f.hz_desktops(d, o)

    def test_stuck_since(self):
        self.assertIn("stuck provisioning since 2026-11-12 23:37 CET", self.run_(NOV, {"time_zone": BER})["held"][0]["reason"])
        self.assertIn("since 2026-07-13 00:37 CEST", self.run_(JUL, {"time_zone": BER})["held"][0]["reason"])
        base = same_without_zone(self, lambda x: self.run_(NOV, x))
        self.assertIn("since 2026-11-12 22:37 UTC", base["held"][0]["reason"])


class PushStart(unittest.TestCase):
    def test_typed_in_utc(self):
        r = f.hz_push_start("2026-11-13 04:37", NOV - D, BER)
        self.assertEqual(r["text"], "2026-11-13 05:37 CET (2026-11-13 04:37 UTC)")
        self.assertEqual(r["ms"], NOV)                                        # UTC, as typed
        self.assertEqual(f.hz_push_start("2026-07-13 04:37", JUL - D, BER)["text"], "2026-07-13 06:37 CEST (2026-07-13 04:37 UTC)")
        self.assertEqual(f.hz_push_start("2026-11-13 04:37", NOV + D, BER)["problem"],
                         "horizon_push_start 2026-11-13 05:37 CET (2026-11-13 04:37 UTC) is in the past")

    def test_no_zone(self):
        base = f.hz_push_start("2026-11-13 04:37", NOV - D)
        self.assertEqual(base, {"ms": NOV, "text": "2026-11-13 04:37 UTC", "problem": ""})
        self.assertEqual(f.hz_push_start("2026-11-13 04:37", NOV - D, ""), base)
        self.assertEqual(f.hz_push_start("2026-11-13 04:37", NOV - D, "Nowhere/Atlantis"), base)
        self.assertEqual(f.hz_push_start("", NOV, BER)["text"], "as soon as the approved push job runs")


class FSLogix(unittest.TestCase):
    def run_(self, now_s, extra):
        sc = tfx.scan([("%s_jdoe" % tfx.J, [{"name": "Profile_jdoe.vhdx", "size": 990 * tfx.MB, "modified": now_s - 3600}])])
        o = {"size_mb": 1000, "now": now_s}
        o.update(extra)
        return fx.hz_fslogix(sc, o)

    def test_last_written(self):
        cells = str(self.run_(NOV / 1000.0, {"time_zone": BER})["report"]["sections"])
        self.assertIn("2026-11-13 04:37 CET", cells)
        self.assertNotIn("UTC", cells)
        self.assertIn("2026-07-13 05:37 CEST", str(self.run_(JUL / 1000.0, {"time_zone": BER})["report"]["sections"]))
        base = same_without_zone(self, lambda x: self.run_(NOV / 1000.0, x))
        self.assertIn("2026-11-13 03:37 UTC", str(base["report"]["sections"]))
        self.assertEqual(self.run_(NOV / 1000.0, {"time_zone": BER})["flagged"][0]["last"], "2026-11-13 03:37 UTC")   # artifact: UTC


class Image(unittest.TestCase):
    def run_(self, created, extra):
        v = copy.deepcopy(thi.GOOD_VM)
        v["snapshots"]["current_created"] = created
        o = dict(thi.OPTS)
        o.update(extra)
        return hi.hzi_report(thi.vc(v), thi.guest(), o)

    def test_taken(self):
        self.assertIn("taken 2026-11-01 13:00:00 CET", str(self.run_("2026-11-01T12:00:00Z", {"time_zone": BER})["checks"]))
        self.assertIn("taken 2026-07-01 14:00:00 CEST",                      # fractions of a second dropped
                      str(self.run_("2026-07-01T12:00:00.5Z", {"time_zone": BER})["checks"]))
        base = same_without_zone(self, lambda x: self.run_("2026-11-01T12:00:00Z", x))
        self.assertIn("taken 2026-11-01 12:00:00 UTC", str(base["checks"]))


if __name__ == "__main__":
    unittest.main()
