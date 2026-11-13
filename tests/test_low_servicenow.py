#!/usr/bin/env python3
"""1.56.0: the ServiceNow node log rated - critical / warning / low - and the "Low" level in the report looks.
The log lines have the shapes of a real node's, with made-up ids, paths, threads and people."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
sys.path.insert(0, os.path.join(HERE, "..", "roles", "servicenow", "library"))
import servicenow_node_filters as nf  # noqa: E402
import site_servicenow_node as nm  # noqa: E402
import site_filters as sf  # noqa: E402
from test_servicenow_node import node, unit  # noqa: E402

LINE = ("@T@ (%d) %s SYSTEM txid=%s GlideServletTransaction *** WARNING *** GlideServlet:Transaction waited excessive "
        "time to run: %dms, semaphore wait: %dms, session wait: %dms - transaction: #%d %s, user: %s")


def tx(n, run, sem, sess, path, who="pat.example", thread="Default-thread-%d"):
    return LINE % (100 + n, thread % n, "%032x" % (n * 7919), run, sem, sess, 5000 + n, path, who)


class Parse(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def read(self, lines, **kw):
        t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 600))
        with open(os.path.join(self.d, "localhost_log.%s.txt" % time.strftime("%Y-%m-%d")), "w") as f:
            f.write("".join(l.replace("@T@", t) + "\n" for l in lines))
        return nm.read_log(self.d, "localhost_log*", 24, 200, 20, **kw)

    def test_session_waits_fold_per_path_with_the_median(self):
        lines = [tx(i, 3000 + i * 1000, 0, 2950 + i * 1000, "/api/now/ui/page_timing/%032x" % (i * 31), "jane.roe.ctr") for i in range(5)]
        lines += [tx(10 + i, 2500, 3, 2490, "/api/now/v1/batch/%d" % (700 + i), "john.doe") for i in range(2)]
        lg = self.read(lines)
        low = sorted((g for g in lg["groups"] if g["kinds"] == ["slow transaction (session wait)"]), key=lambda g: g["path"])
        self.assertEqual([(g["path"], g["count"]) for g in low], [("/api/now/ui/page_timing", 5), ("/api/now/v1/batch", 2)])
        self.assertEqual((low[0]["max_session_ms"], low[0]["median_session_ms"]), (6950, 4950))
        self.assertEqual(low[1]["median_session_ms"], 2490)
        self.assertEqual((lg["slow"], lg["slow_session"]), (7, 7))
        self.assertTrue(all("session_ms" not in g for g in lg["groups"]))      # the waits: only their median is kept
        for who in ("jane.roe", "john.doe"):
            self.assertNotIn(who, json.dumps(lg))

    def test_semaphore_wait_levels(self):
        lg = self.read([tx(1, 45210, 42000, 0, "/incident_list.do"), tx(2, 2400, 1500, 0, "/task_list.do"),
                        tx(3, 1800, 400, 1300, "/home.do")])
        by = {g["message"].split("transaction: ")[1].split(",")[0].split(" ")[1]: g for g in lg["groups"]}
        self.assertEqual(by["/incident_list.do"]["kinds"], ["slow transaction (semaphore wait)"])
        self.assertEqual(by["/incident_list.do"]["max_sem_ms"], 42000)
        self.assertEqual(by["/task_list.do"]["kinds"], ["slow transaction (semaphore wait)"])
        # a semaphore wait of 100 ms or more is not "session wait only": a plain slow transaction, as before
        self.assertEqual(by["/home.do"]["kinds"], ["slow transaction"])
        self.assertEqual(lg["slow_session"], 0)
        self.assertEqual([nf.sn_log_level(by[p]) for p in ("/incident_list.do", "/task_list.do", "/home.do")],
                         ["critical", "warning", ""])

    def test_script_errors(self):
        lg = self.read(["@T@ (201) glide.scheduler.worker.4 SEVERE checkDCSupportsTLS: Cannot convert null to an object",
                        "@T@ (202) glide.scheduler.worker.5 SEVERE checkDCSupportsTLS: Cannot convert null to an object",
                        "@T@ (203) http-16000-2 SEVERE *** ERROR *** Evaluator: org.mozilla.javascript.EcmaError: Cannot read "
                        "property \"name\" from undefined Caused by error in sys_script_include.0123456789abcdef0123456789abcdef.script at line 5",
                        "@T@ (204) http-16000-3 SEVERE *** ERROR *** Transaction cancelled: maximum execution time exceeded"])
        by = {g.get("script", ""): g for g in lg["groups"]}
        self.assertEqual(by["checkDCSupportsTLS"]["count"], 2)
        self.assertEqual(by["checkDCSupportsTLS"]["kinds"], ["SEVERE", "script error"])
        self.assertTrue(by["checkDCSupportsTLS"]["first"] and by["checkDCSupportsTLS"]["last"])
        self.assertEqual(by["sys_script_include.script"]["kinds"], ["SEVERE", "script error"])
        self.assertEqual(by[""]["kinds"], ["SEVERE"])                           # not a script error: as before
        self.assertEqual([nf.sn_log_level(by[k]) for k in ("checkDCSupportsTLS", "")], ["warning", ""])

    def test_tx_path_and_script_of(self):
        self.assertEqual(nm.tx_path("... - transaction: #5001 /api/now/ui/page_timing/0123456789abcdef0123456789abcdef, user: …"),
                         "/api/now/ui/page_timing")
        self.assertEqual(nm.tx_path("transaction: #12 /nav_to.do?uri=incident.do?sys_id=abc&x=1, user: …"), "/nav_to.do?uri=…&x=…")
        self.assertEqual(nm.script_of("checkDCSupportsTLS: Cannot convert null to an object"), (True, "checkDCSupportsTLS"))
        self.assertEqual(nm.script_of("Transaction cancelled: maximum execution time exceeded"), (False, ""))
        self.assertEqual(nm.script_of("Evaluator: TypeError: undefined is not a function"), (True, ""))


def grp(kinds, count=1, **kw):
    g = {"message": "m", "count": count, "kinds": kinds, "first": "2026-11-13 08:01:00", "last": "2026-11-13 10:15:00",
         "max_ms": 0, "max_session_ms": 0, "max_sem_ms": 0}
    g.update(kw)
    return g


class Rated(unittest.TestCase):
    def test_thresholds_follow_the_settings(self):
        g = grp(["slow transaction (semaphore wait)"], max_sem_ms=12000)
        self.assertEqual(nf.sn_log_level(g, {}), "critical")
        self.assertEqual(nf.sn_log_level(g, {"sem_crit_ms": 15000}), "warning")
        # never below the warning line: a critical line under it is lifted to it
        self.assertEqual(nf._sem_ms({"sem_warn_ms": 20000, "sem_crit_ms": 15000}), (20000, 20000))
        # the older setting still counts when the new one is not given
        self.assertEqual(nf._sem_ms({"sem_wait_ms": 2000}), (2000, 10000))

    def test_table_order_words_and_note(self):
        gs = [grp(["slow transaction (session wait)"], 40, path="/api/now/ui/page_timing", max_session_ms=9100, median_session_ms=3100),
              grp(["SEVERE"], 7), grp(["SEVERE", "script error"], 12, script="checkDCSupportsTLS"),
              grp(["slow transaction (semaphore wait)"], 2, max_sem_ms=42000), grp(["semaphore"], 3)]
        sec = nf.sn_node_sections([node(units=[unit(groups=gs)])], [], [], {})[-1]
        self.assertEqual(sec["columns"][0], "Level")
        self.assertEqual([r[0] for r in sec["rows"]], ["Critical", "Warning", "Warning", "-", "Low"])
        self.assertEqual(sec["row_status"], ["critical", "warning", "warning", "", "low"])
        self.assertEqual(sec["rows"][-1][-1], "40 slow session(s) on /api/now/ui/page_timing, worst 9100 ms, median 3100 ms")
        self.assertIn("(script checkDCSupportsTLS, first 2026-11-13 08:01:00)", sec["rows"][1][-1])
        self.assertEqual(len(sec["lines"]), 1)
        self.assertIn("session wait", sec["lines"][0])
        self.assertEqual(len(sec["columns"]), len(sec["rows"][0]))
        # no Low row: no note
        sec = nf.sn_node_sections([node(units=[unit(groups=gs[1:2])])], [], [], {})[-1]
        self.assertNotIn("lines", sec)

    def test_findings(self):
        gs = [grp(["slow transaction (session wait)"], 300, path="/api/now/ui/page_timing", max_session_ms=9100),
              grp(["SEVERE", "script error"], 12, script="checkDCSupportsTLS", message="checkDCSupportsTLS: Cannot convert null to an object"),
              grp(["slow transaction (semaphore wait)"], 2, max_sem_ms=42000)]
        u = unit(sem=2, groups=gs)
        u["log"].update(slow=302, slow_session=300, slow_max_ms=45210)
        f = {x["id"].rsplit(":", 1)[1]: x for x in nf.sn_node_findings([node(units=[u])], [], {})}
        self.assertEqual(sorted(f), ["log-semaphore", "script-errors"])   # 2 slow ones without the session waits: under 200
        self.assertEqual(f["log-semaphore"]["severity"], "critical")
        self.assertIn("waited 42000 ms for a semaphore", f["log-semaphore"]["summary"])
        s = f["script-errors"]
        self.assertEqual(s["severity"], "warning")
        self.assertIn("12 SEVERE script error(s) in the last 24 h (checkDCSupportsTLS: 12 time(s), 2026-11-13 08:01:00 to 2026-11-13 10:15:00)", s["summary"])
        self.assertIn("System Logs > All, filtered on checkDCSupportsTLS", s["hint"])
        self.assertIn("Scheduled Jobs that ran at 2026-11-13 08:01:00", s["hint"])
        # A1: servicenow_node_slow_tx_warn counts the other slow transactions; the session waits are named
        u["log"].update(slow=520, slow_session=300)
        f = {x["id"].rsplit(":", 1)[1]: x for x in nf.sn_node_findings([node(units=[u])], [], {})}
        self.assertIn("logged 220 slow transaction(s)", f["slow"]["summary"])
        self.assertTrue(f["slow"]["summary"].endswith(", plus 300 session wait(s), Low"))
        # the Start here cards: critical and warning only
        cards, _ = sf.findings_cards({"n": list(f.values()) + [{"severity": "low", "summary": "x"}]})
        self.assertNotIn("low", [c["status"] for c in cards])

    def test_old_module_output(self):
        # a node read by the module before 1.56.0: no max_sem_ms, no slow_session - rated as before, no crash
        gs = [{"message": "m", "count": 3, "kinds": ["slow transaction (semaphore wait)"], "last": ""}]
        self.assertEqual(nf.sn_log_level(gs[0]), "warning")
        sec = nf.sn_node_sections([node(units=[unit(groups=gs)])], [], [], {})[-1]
        self.assertEqual(sec["rows"][0][0], "Warning")


class Looks(unittest.TestCase):
    def test_card_palette(self):
        self.assertEqual(sf.CARD_PAL["low"][5], "Low")
        html = sf.report_cards_html([{"status": "low", "title": "t"}])
        self.assertIn(">Low<", html)
        self.assertEqual(sf.report_cards_text([{"status": "low", "title": "t"}])[0], "1. [LOW] t")

    def test_findings_report_low_is_not_amber(self):
        r = sf.findings_report({"h1": [{"severity": "low", "check": "certs", "summary": "expires within 90 days"}],
                                "h2": []}, "Health")
        self.assertEqual(r["status"], "ok")
        self.assertNotIn("Start here", [s["title"] for s in r["sections"]])
        fs = next(s for s in r["sections"] if s["title"] == "Findings")
        self.assertEqual((fs["rows"][0][1], fs["row_status"]), ("LOW", ["low"]))
        self.assertIn(("Low", 1, "low"), [(b["label"], b["value"], b.get("status")) for b in r["summary"]])
        self.assertIn("Healthy (2)", [s["title"] for s in r["sections"]])
        # without a low finding: no Low box, no row_status (as before)
        r = sf.findings_report({"h1": [{"severity": "warning", "check": "certs", "summary": "x"}]}, "Health")
        self.assertNotIn("Low", [b["label"] for b in r["summary"]])
        self.assertNotIn("row_status", next(s for s in r["sections"] if s["title"] == "Findings"))


class Fit(unittest.TestCase):
    """report_fit documents (tests/report_fit_docs.py NAMED): the node log with every level, a findings report with Low."""

    def test_report_fit_document(self):
        gs = [grp(["slow transaction (session wait)"], 40 - i, path="/api/now/ui/page_timing/section_%d/with/a/longer/path" % i,
                  max_session_ms=9100 + i, median_session_ms=3100) for i in range(3)]
        gs += [grp(["SEVERE", "script error"], 12, script="checkDCSupportsTLS",
                   message="checkDCSupportsTLS: Cannot convert null to an object"),
               grp(["slow transaction (semaphore wait)"], 2, max_sem_ms=42000, max_ms=45210,
                   message="GlideServlet:Transaction waited excessive time to run: 45210ms, semaphore wait: 42000ms, session "
                           "wait: 0ms - transaction: #5004 /incident_list.do, user: …"),
               grp(["SEVERE"], 7, message="Invalid query detected, please check logs for details [Unknown field sys_id1 in table incident]")]
        secs = nf.sn_node_sections([node(units=[unit(groups=gs)])], [], [], {})
        self.assertIn("low", secs[-1]["row_status"])

    def test_findings_report_fit_document(self):
        by = {"host%02d.corp.example.mil" % i: [{"severity": sev, "check": "certs", "summary": "certificate CN=www%d.corp.example.mil %s" % (i, txt),
                                                 "hint": "openssl x509 -enddate -noout -in /etc/pki/tls/certs/www%d.crt" % i}]
              for i, (sev, txt) in enumerate([("warning", "expires in 20 days"), ("low", "expires within 90 days"),
                                               ("low", "expires within 90 days"), ("critical", "EXPIRED")])}
        r = sf.findings_report(by, "Certificates")
        self.assertEqual(r["status"], "critical")


if __name__ == "__main__":
    unittest.main()
