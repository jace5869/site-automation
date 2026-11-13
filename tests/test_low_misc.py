#!/usr/bin/env python3
"""1.56.0: the "Low" level (worth knowing, nothing to do now) in the Horizon, FSLogix, log analysis, mail relay,
ServiceNow ticket insights and UCS reports - each moved row, that the word "Low" is shown with it, that it sorts
after warning, and that it never makes a report amber. Needs ansible-core importable (the insights part)."""
import copy
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
import horizon_filters as hz  # noqa: E402
import horizon_fslogix_filters as fx  # noqa: E402
import log_filters as lf  # noqa: E402
import mail_relay_filters as mr  # noqa: E402
import ucs_inventory_filters as ucs  # noqa: E402
import test_horizon as th  # noqa: E402
import test_horizon_fslogix as tf  # noqa: E402
import test_mail_relay as tm  # noqa: E402
import test_ucs_inventory as tu  # noqa: E402


def sec(report, title):
    return next(s for s in report["sections"] if s["title"] == title)


class Horizon(unittest.TestCase):
    def sess(self, i, user, mach, **kw):
        d = {"id": i, "user_id": user, "machine_id": mach, "desktop_pool_id": "p1", "session_state": "CONNECTED",
             "start_time": th.NOW - th.H, "session_protocol": "BLAST", "client_data": {"type": "WINDOWS", "version": "8.12.0"}}
        d.update(kw)
        return d

    def test_health_pool_rows(self):
        pools = [dict(th.IC, enabled=False, enable_provisioning=False),
                 dict(th.IC, id="p9", name="IC-Two")]
        ms = [th.mach("1", "p9", "AVAILABLE", agent_version="8.12"), th.mach("2", "p9", "AVAILABLE", agent_version="8.11")]
        h = hz.hz_health(th.data(pools, ms), {"now": th.NOW})
        low = [(p["what"], p["text"]) for p in h["problems"] if p["severity"] == "low"]
        self.assertIn(("IC-Two", "Horizon Agent of 2 versions: 8.12 (1), 8.11 (1)"), low)
        texts = [t for _, t in low]
        self.assertIn("the pool is disabled", texts)
        # low sorts after warning, its word in the Severity column, its own chip colour
        sevs = [p["severity"] for p in h["problems"]]
        self.assertEqual(sevs, sorted(sevs, key=lambda s: hz.SEV[s]))
        rows = sec(h["report"], "Problems")
        i = next(n for n, r in enumerate(rows["rows"]) if r[3] == "the pool is disabled")
        self.assertEqual((rows["rows"][i][0], rows["cell_status"][i][0]), ("LOW", "low"))
        # low alone: the report is healthy, and counts no warning
        base = hz.hz_health(th.data([th.IC], []), {"now": th.NOW})
        only = hz.hz_health(th.data([dict(th.IC, enabled=False)], []), {"now": th.NOW})
        self.assertEqual((only["warning"], only["critical"], only["report"]["status"], only["report"]["title"]),
                         (base["warning"], base["critical"], base["report"]["status"], base["report"]["title"]))

    def test_provisioning_off_is_low(self):
        p = dict(th.IC, enable_provisioning=False)
        h = hz.hz_health(th.data([p], []), {"now": th.NOW})
        self.assertIn(("low", "provisioning is off (left off after a maintenance?)"),
                      [(x["severity"], x["text"]) for x in h["problems"]])

    def test_sessions_old_client_and_protocol(self):
        ms = [th.mach("1", "p1", "CONNECTED")]
        ss = [self.sess("a", "S-1", "1", client_data={"type": "WINDOWS", "version": "8.9.0"}),
              self.sess("b", "S-2", "1", session_protocol="PCOIP")]
        r = hz.hz_sessions_report(th.data([th.IC], ms, ss), {"now": th.NOW, "client_min": {"WINDOWS": "8.10"},
                                                            "protocols": ["blast"]})
        self.assertEqual((r["warning"], r["low"], r["report"]["status"]), (0, 2, "ok"))
        low = sec(r["report"], "Low - worth knowing")
        self.assertEqual(low["status"], "low")
        self.assertEqual(len(low["lines"]), 2)
        self.assertFalse([s for s in r["report"]["sections"] if s["title"] == "Problems"])
        tile = next(t for t in r["report"]["summary"] if t["label"] == "Old clients")
        self.assertEqual((tile["status"], tile["note"]), ("low", "Low"))
        # nothing low: as before 1.56.0 (no Low section, the tile without a note)
        r = hz.hz_sessions_report(th.data([th.IC], ms, [self.sess("c", "S-3", "1")]), {"now": th.NOW})
        self.assertNotIn("Low - worth knowing", [s["title"] for s in r["report"]["sections"]])
        self.assertEqual(next(t for t in r["report"]["summary"] if t["label"] == "Old clients"),
                         {"label": "Old clients", "value": 0, "status": "ok"})


class FSLogix(unittest.TestCase):
    def test_stale_and_leftovers_low(self):
        r = tf.run(tf.scan([("S-1-5-21-1-2-3-1001_jdoe", [tf.fi("Profile_jdoe.vhdx", 10, 200)]),
                            ("S-1-5-21-1-2-3-1002_asmith", [])]), stale_days=90)
        jd = next(c for c in r["containers"] if c["file"] == "Profile_jdoe.vhdx")
        self.assertEqual([x["severity"] for x in jd["flags"]], ["low"])
        self.assertEqual(r["report"]["status"], "ok")                      # low alone is never amber
        st = sec(r["report"], "Stale - space to reclaim")
        self.assertEqual((st["status"], st["row_status"]), ("low", ["low"]))
        self.assertTrue(st["text"].startswith("Low: worth knowing"))
        tile = next(t for t in r["report"]["summary"] if t["label"] == "Stale")
        self.assertEqual(tile["status"], "low")
        self.assertTrue(tile["note"].startswith("Low - "))
        emp = sec(r["report"], "Folders with no container, other files")
        self.assertEqual(emp["row_status"], ["low"])
        self.assertTrue(emp["rows"][0][1].startswith("Low - "))

    def test_nothing_stale_unchanged(self):
        r = tf.run(tf.scan([("S-1-5-21-1-2-3-1001_jdoe", [tf.fi("Profile_jdoe.vhdx", 10, 1)])]), stale_days=90)
        st = sec(r["report"], "Stale - space to reclaim")
        self.assertNotIn("status", st)
        self.assertNotIn("row_status", sec(r["report"], "Folders with no container, other files"))
        self.assertNotIn("status", next(t for t in r["report"]["summary"] if t["label"] == "Stale"))


class Logs(unittest.TestCase):
    def test_levels(self):
        for lv in ("5", "6", "7", "notice", "info", "no", "in", "debug"):
            self.assertEqual(lf.log_level(lv), "low", lv)
        for lv in ("4", "warn", "warning", "wa"):
            self.assertEqual(lf.log_level(lv), "warning", lv)
        self.assertEqual(lf.log_level(None, "something odd"), "warning")       # no level: stays warning
        self.assertEqual(lf.log_level("3"), "error")

    def entries(self):
        return [{"host": "web01.example.mil", "source": "journal", "time": "2026-10-23T07:00:00Z", "level": "low", "message": "unit started"},
                {"host": "web01.example.mil", "source": "journal", "time": "2026-10-23T07:01:00Z", "level": "warning", "message": "disk slow"}]

    def test_low_lines_listed_last_never_amber(self):
        d = lf.log_analysis([{"host": "web01.example.mil", "source": "linux", "entries": self.entries()}])
        self.assertEqual((d["warning"], d["low"]), (1, 1))
        self.assertEqual([p["severity"] for p in d["all"]], ["warning", "low"])
        rep = lf.log_report(d)
        pats = next(s for s in rep["sections"] if s["title"].startswith("Patterns"))
        self.assertEqual(pats["row_status"], ["warning", "low"])
        self.assertEqual(pats["rows"][1][1], "LOW")
        self.assertIn({"label": "Low", "value": 1, "status": "low", "note": "notice / info / debug"}, rep["summary"])
        self.assertIn("Low: 1 pattern(s)", rep["subtitle"])
        only = lf.log_analysis([{"host": "web01.example.mil", "source": "linux", "entries": self.entries()[:1]}])
        self.assertEqual(lf.log_report(only)["status"], "ok")
        self.assertIn("LOW ones", lf.log_act_task(only["problems"]))
        self.assertIn("[LOW]", lf.log_evidence(only, only["problems"]))

    def test_no_low_unchanged(self):
        d = lf.log_analysis([{"host": "web01.example.mil", "source": "linux", "entries": self.entries()[1:]}])
        rep = lf.log_report(d)
        self.assertNotIn("Low", [t["label"] for t in rep["summary"]])
        self.assertNotIn("Low:", rep["subtitle"])
        self.assertNotIn("LOW ones", lf.log_act_task(d["problems"]))


class MailRelay(unittest.TestCase):
    def test_unused_subnet_low(self):
        r1 = tm.relay("relay1", ["127.0.0.0/8", "192.0.2.11", "203.0.113.0/28"], 330, sent={"203.0.113.5": 400})
        u = mr.mail_relay_unused([r1], {"days": 183, "now": tm.NOW})
        st = dict((r[0], s) for r, s in zip(u["rows"], u["row_status"]))
        verdict = dict((r[0], r[-1]) for r in u["rows"])
        self.assertEqual((st["203.0.113.0/28"], st["192.0.2.11"]), ("low", "warning"))   # the address to remove stays warning
        self.assertEqual(verdict["203.0.113.0/28"], "Low: UNUSED subnet - listed only, not removed")
        self.assertEqual(verdict["192.0.2.11"], "UNUSED")


class UCS(unittest.TestCase):
    def test_major_minor_low(self):
        c = tu.collected()
        data = c["domains"][0]["data"]
        data["faultInst"]["objects"].append({"dn": "sys/chassis-1/blade-2/fault-F0998", "code": "F0998", "severity": "minor",
                                             "ack": "no", "descr": "made up", "cause": "x", "created": "2026-11-01T08:00:00.000", "occur": "1"})
        before = ucs.ucs_inventory_doc(tu.collected(), {"severities": ["critical", "major", "minor"]})
        d = ucs.ucs_inventory_doc(c, {"severities": ["critical", "major", "minor"]})
        f = tu.sec(d, "Faults (critical, major, minor)")
        by = dict((r[2], s) for r, s in zip(f["rows"], f["row_status"]))
        self.assertEqual((by["F0369"], by["F0283"], by["F0998"]), ("warning", "low", "low"))
        self.assertIn("Low (the tinted rows): major and minor faults", f["text"])
        self.assertEqual((d["status"], d["title"]), (before["status"], before["title"]))   # a low fault changes neither

    def test_only_critical_no_low_text(self):
        c = tu.collected()
        data = c["domains"][0]["data"]
        data["faultInst"]["objects"] = [x for x in data["faultInst"]["objects"] if x["severity"] == "critical"]
        f = tu.sec(ucs.ucs_inventory_doc(c), "Faults (critical, major)")
        self.assertNotIn("Low", f["text"])


class Insights(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_servicenow_insights as ti
        import servicenow_insights_filters as F
        cls.ti, cls.F = ti, F
        s = ti.site()
        cls.m = F.servicenow_insights_model(s["r"], None, s["mask"], dict(ti.OPTS, categories=["Account", "Hardware", "Software", "Other"]))

    def doc(self, m):
        return self.F.servicenow_insights_doc(m, "all", self.ti.OPTS)

    def test_backlog_grew_is_low(self):
        m = copy.deepcopy(self.m)
        sc = m["scopes"]["all"]
        sc["backlog"] = [120, 100] + list(sc["backlog"][2:])
        sc["age"] = list(sc["age"][:3]) + [0] + list(sc["age"][4:])
        d = self.doc(m)
        tile = next(t for t in d["summary"] if t["label"] == "Open now")
        self.assertEqual((tile["status"], tile["note"]), ("low", "Low: grew from 100 at the start"))
        self.assertEqual(d["status"], "ok")                                # low is never the report's status
        sc["backlog"] = [101, 100] + list(sc["backlog"][2:])
        tile = next(t for t in self.doc(m)["summary"] if t["label"] == "Open now")
        self.assertEqual((tile["status"], tile["note"]), ("", "was 100 at the start"))

    def test_matrix_and_categories(self):
        m = copy.deepcopy(self.m)
        mx = m["scopes"]["all"]["matrix"]
        mx["pct"] = 12.0
        s = next(x for x in self.doc(m)["sections"] if x["title"] == "Priority set by hand")
        self.assertEqual(s["status"], "low")
        self.assertTrue(s["text"].startswith("Low: "))
        mx["pct"] = 9.0
        s = next(x for x in self.doc(m)["sections"] if x["title"] == "Priority set by hand")
        self.assertEqual(s["status"], "")
        self.assertFalse(s["text"].startswith("Low"))
        cat = next(x for x in self.doc(self.m)["sections"] if x["title"] == "Categories")
        self.assertIn("low", cat["row_status"])
        self.assertNotIn("warning", cat["row_status"])
        self.assertIn("Low (the tinted rows): not on the official list.", cat["text"])

    def test_fair_is_low(self):
        m = copy.deepcopy(self.m)
        m["scopes"]["all"]["survey"] = {"n": 40, "avg": 3.1, "scale": [1.0, 5.0, "read"], "grade": "Fair", "label": "Survey"}
        t = next(x for x in self.doc(m)["summary"] if x["label"] == "Satisfaction")
        self.assertEqual(t["status"], "low")
        self.assertTrue(t["note"].startswith("Fair"))
        m["scopes"]["all"]["survey"]["grade"] = "Low"                      # the survey's own grade Low stays critical
        self.assertEqual(next(x for x in self.doc(m)["summary"] if x["label"] == "Satisfaction")["status"], "critical")


if __name__ == "__main__":
    unittest.main()
