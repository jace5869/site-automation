#!/usr/bin/env python3
"""1.61.0 patch age (plugins/filter/patch_age_filters.py): the status of a host (Linux, Windows, ESXi), the groups, the
sections of the health reports and the VMware version report (squares on the website, the tables in the email, the
workbook's sheet, the job artifact), "% on time" over 12 weeks from the job history, ESXi's update date worked out from
the version report's own runs, findings_report taking the boxes, site_report_history's artifacts, and the squares in the
atlas and brief looks.
    python3 tests/test_patch_age.py"""
import importlib.util
import os
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
sys.path.insert(0, HERE)

import patch_age_filters as pa  # noqa: E402
import site_filters as sf  # noqa: E402

D = 86400
NOW = 1794000000                      # 2026-11-06 (a fixed "now" for the tests)


def iso(epoch):
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%S.5Z", time.gmtime(epoch))


def lin(host, days, group="SITEA-linux-web", **kw):
    return dict({"host": host, "group": group, "days": days, "late_after": 35, "crit_from": 0, "restart": False,
                 "last": "" if days is None else "2026-10-01", "setting": "check_patching_max_age_days"}, **kw)


def win(host, days, group="SITEA-windows-app", **kw):
    return dict({"host": host, "group": group, "days": days, "late_after": 34, "crit_from": 60, "restart": False,
                 "last": "" if days is None else "2026-10-01", "setting": "win_check_patching_warn_days"}, **kw)


def run(days_ago, groups=None, esxi=None):
    art = {}
    if groups is not None:
        art["patch_age"] = {"groups": groups}
    if esxi is not None:
        art["vm_versions"] = {"esxi_hosts": esxi}
    return {"id": 1, "status": "successful", "finished": iso(NOW - days_ago * D), "report": None, "artifacts": art}


class Status(unittest.TestCase):
    def test_linux_thresholds(self):
        st = lambda d: pa.host_status(lin("h", d))
        self.assertEqual([st(d) for d in (0, 28, 29, 35, 36, 400)], ["ontime", "ontime", "due", "due", "late", "late"])
        self.assertEqual(st(None), "nodata")
        self.assertEqual(pa.host_status(lin("h", 25), 14), "due")          # patch_age_due_days 14

    def test_windows_late_at_warn_days_critical_at_crit_days(self):
        st = lambda d: pa.host_status(win("h", d))
        self.assertEqual([st(d) for d in (27, 28, 34, 35, 59, 60)], ["ontime", "due", "due", "late", "late", "critical"])

    def test_a_floor_is_late_or_not_known_never_on_time(self):
        self.assertEqual(pa.host_status(lin("h", 40, at_least=True)), "late")
        self.assertEqual(pa.host_status(lin("h", 35, at_least=True)), "unknown")
        self.assertEqual(pa.host_status(lin("h", 2, at_least=True)), "unknown")

    def test_groups(self):
        self.assertEqual(pa.patch_age_group(["all", "SITEB-db", "SITEA-web"], ["SITEA-web", "SITEB-db"]), "SITEA-web")
        self.assertEqual(pa.patch_age_group(["SITEB-db"], ["SITEA-web"]), "Other")
        self.assertEqual(pa.patch_age_group(["SITEB-db", "SITEA-web"], []), "SITEB-db")
        self.assertEqual(pa.patch_age_group(["ungrouped"], []), "Other")


class Sections(unittest.TestCase):
    def doc(self, hosts, runs=None, **o):
        return pa.patch_age_sections(hosts, dict({"what": "Linux", "now": NOW}, **o), runs or [])

    def fleet(self):
        return [lin("web%02d.corp.example.mil" % i, d) for i, d in enumerate([3, 20, 30, 41, 75])] \
            + [lin("db01.corp.example.mil", 67, group="SITEA-linux-db", restart=True), lin("db02.corp.example.mil", None, group="SITEA-linux-db")]

    def test_by_group_table_and_boxes(self):
        r = self.doc(self.fleet())
        g, old = r["sections"]
        self.assertEqual(g["columns"], ["Group", "Hosts", "On time", "Due soon", "Late", "No data", "Oldest (days)", "% on time, 12 weeks", "Status"])
        self.assertEqual(g["rows"], [["SITEA-linux-db", 2, "-", "-", 1, 1, 67, "0%", "1 late"],
                                     ["SITEA-linux-web", 5, 2, 1, 2, "-", 75, "40%", "2 late"]])
        self.assertEqual(g["row_status"], ["warning", "warning"])
        self.assertIn("late = no update for 36 days or more (check_patching_max_age_days); due soon (Low) = late within 7 days", g["text"])
        self.assertEqual([(b["label"], b["value"], b["status"]) for b in g["boxes"]],
                         [("Patching late", 3, "warning"), ("Patching due soon", 1, "low"), ("Oldest update", "75 days", "warning")])
        self.assertEqual(r["status"], "warning")
        self.assertEqual(r["artifact"]["groups"], {"SITEA-linux-db": [1, 0], "SITEA-linux-web": [5, 2]})
        self.assertEqual(r["counts"], {"hosts": 7, "on_time": 2, "due": 1, "late": 3, "not_known": 0, "no_data": 1})

    def test_the_oldest_hosts(self):
        old = self.doc(self.fleet())["sections"][1]
        self.assertEqual(old["title"], "Patch age: the 6 oldest hosts")
        self.assertEqual(old["rows"][:2], [["web04.corp.example.mil", "SITEA-linux-web", "2026-10-01", 75, "no", "Late"],
                                           ["db01.corp.example.mil", "SITEA-linux-db", "2026-10-01", 67, "yes", "Late"]])
        self.assertEqual(old["row_status"], ["warning", "warning", "warning", "low", "", ""])
        self.assertIn("No data (1): db02.corp.example.mil - the last update could not be read.", old["lines"])
        self.assertEqual(len(self.doc(self.fleet(), oldest=3)["sections"][1]["rows"]), 3)

    def test_on_time_over_12_weeks_from_the_history(self):
        runs = [run(120, {"SITEA-linux-web": [5, 1]}),           # older than 12 weeks: left out
                run(80, {"SITEA-linux-web": [5, 4]}), run(10, {"SITEA-linux-web": [5, 5]}), run(5)]
        r = self.doc(self.fleet(), runs)
        rows = r["sections"][0]["rows"]
        self.assertEqual(rows[1][7], "80% → 40%")
        self.assertEqual(rows[0][7], "0%")                       # no earlier run had this group
        self.assertNotIn("no earlier run", " ".join(r["sections"][1].get("lines", [])))
        first = self.doc(self.fleet())["sections"][1]["lines"]
        self.assertIn("% on time, 12 weeks: no earlier run in AAP's job history yet - the trend starts with the next run.", first)

    def test_the_12_week_line_on_the_website_only(self):
        """int-11: a Trend column beside "% on time, 12 weeks" on the website - every run of the 12 weeks, then this one;
        fewer than 2 points: no line. The email's table stays as it was."""
        runs = [run(120, {"SITEA-linux-web": [5, 1]}), run(80, {"SITEA-linux-web": [5, 4]}), run(10, {"SITEA-linux-web": [5, 5]}), run(5)]
        r = self.doc(self.fleet(), runs)
        mail, web = r["sections"][0], r["web_sections"][1]
        self.assertNotIn("Trend", mail["columns"])
        self.assertNotIn("sparks", mail)
        at = web["columns"].index("Trend")
        self.assertEqual(web["columns"][at - 1], "% on time, 12 weeks")
        self.assertEqual(web["kinds"][at], "spark")
        self.assertEqual([len(x) for x in web["rows"]], [len(web["columns"])] * len(web["rows"]))
        self.assertEqual([len(x) for x in web["cell_status"]], [len(web["columns"])] * len(web["rows"]))
        self.assertEqual(web["rows"][1][at - 1], "80% → 40%")                     # the number stays
        self.assertEqual(web["sparks"][0], {})                                     # one point: no line
        self.assertEqual(web["rows"][0][at], "")
        sp = web["sparks"][1]
        self.assertEqual([p["text"] for p in sp["data_points"]], ["80% on time", "100% on time", "40% on time"])
        self.assertEqual(sp["data_points"][-1]["label"], "Fri 6 Nov 2026")
        self.assertEqual(sp["name"], "SITEA-linux-web")
        self.assertEqual(web["rows"][1][at], sp["label"])
        self.assertIn("80%, 100%, 40%", sp["label"])
        xs = [p["x"] for p in sp["data_points"]]
        self.assertEqual(xs, sorted(xs))
        self.assertEqual(xs[-1], pa.SPARK_W - pa.SPARK_PAD)
        self.assertEqual(sp["last"], [sp["data_points"][-1]["x"], sp["data_points"][-1]["y"]])
        self.assertEqual(self.doc(self.fleet())["web_sections"][1], self.doc(self.fleet())["sections"][0])   # no history: as the email

    def test_windows_critical_and_rule(self):
        r = pa.patch_age_sections([win("app01.corp.example.mil", 61), win("app02.corp.example.mil", 10)],
                                  {"what": "Windows", "now": NOW, "setting": "win_check_patching_warn_days"})
        g = r["sections"][0]
        self.assertEqual(g["rows"][0][-1], "1 late")
        self.assertEqual(g["row_status"], ["critical"])
        self.assertIn("late = no update for 35 days or more (win_check_patching_warn_days)", g["text"])
        self.assertIn("critical from 60 days", g["text"])
        self.assertEqual(r["sections"][1]["cell_status"][0][-1], "critical")
        self.assertEqual(r["web_sections"][0]["squares"]["legend"][3]["text"], "35-60 days (late)")

    def test_squares_on_the_website_only(self):
        r = self.doc(self.fleet())
        sq = r["web_sections"][0]
        self.assertEqual(sq["title"], "Patch age at a glance")
        self.assertNotIn(sq, r["sections"])
        self.assertEqual([x["text"] for x in sq["squares"]["legend"]],
                         ["0-14 days", "15-28 days", "29-35 days (due soon)", "36-60 days (late)", "61+ days (late)", "no data"])
        web = sq["squares"]["groups"][1]
        self.assertEqual((web["name"], web["sub"], web["status"], web["word"]), ("SITEA-linux-web", "5 hosts", "warning", "2 late"))
        self.assertEqual([c["q"] for c in web["cells"]], ["q5", "q4", "q3", "q2", "q1"])        # oldest first
        self.assertEqual(web["cells"][0]["tip"], "web04.corp.example.mil: 75 days (2026-10-01) - Late")
        self.assertEqual(sq["squares"]["groups"][0]["cells"][1], {"q": "q0", "tip": "db02.corp.example.mil: no data"})

    def test_sheet_and_lines(self):
        r = self.doc(self.fleet())
        self.assertEqual(r["sheet"]["columns"][:4], ["Host", "Group", "Last update", "Days"])
        self.assertEqual(len(r["sheet"]["rows"]), 7)
        self.assertEqual(r["sheet"]["rows"][0], ["db01.corp.example.mil", "SITEA-linux-db", "2026-10-01", 67, "", "yes", "Late", 36])
        self.assertTrue(r["lines"][0].startswith("Patch age: 7 host(s) - 2 on time, 1 due soon, 3 late, 1 no data; oldest 75 days"))

    def test_all_on_time_and_nothing(self):
        r = self.doc([lin("a.corp.example.mil", 1), lin("b.corp.example.mil", 2)])
        self.assertEqual((r["status"], r["sections"][0]["rows"][0][-1], r["sections"][0]["row_status"]), ("ok", "On time", [""]))
        self.assertNotIn("No data", r["sections"][0]["columns"])
        self.assertEqual(self.doc([]), {})


class Esxi(unittest.TestCase):
    """ESXi: no read-only "last update" date in vCenter - the version report's own runs and the boot time."""

    def host(self, name, build, boot_days_ago, cluster="CL-SITEA"):
        return {"name": name, "cluster": cluster, "vcenter": "vc01.corp.example.mil", "build": build,
                "boot": NOW - boot_days_ago * D if boot_days_ago is not None else None}

    def age(self, hosts, runs, late=35):
        return dict((h["host"], h) for h in pa.patch_age_esxi(hosts, runs, {"now": NOW, "late_after": late}))

    def test_a_build_change_gives_a_real_date(self):
        runs = [run(60, esxi={"esx01": ["100", NOW - 90 * D]}), run(30, esxi={"esx01": ["101", NOW - 40 * D]})]
        h = self.age([self.host("esx01", "101", 40)], runs)["esx01"]
        self.assertEqual((h["days"], h["at_least"], h["last"]), (40, False, "2026-09-27"))     # the boot the new build came with
        self.assertEqual(pa.host_status(h), "late")
        runs = [run(60, esxi={"esx01": ["100", NOW - 90 * D]}), run(20, esxi={"esx01": ["101", NOW - 25 * D]})]
        h = self.age([self.host("esx01", "101", 25)], runs)["esx01"]
        self.assertEqual((h["days"], pa.host_status(h)), (25, "ontime"))                     # normal statuses apply
        h = self.age([self.host("esx01", "101", 25)], runs, late=30)["esx01"]
        self.assertEqual(pa.host_status(h, 7), "due")

    def test_a_build_change_whose_boot_is_not_in_the_window(self):
        runs = [run(60, esxi={"esx01": ["100", NOW - 90 * D]}), run(30, esxi={"esx01": ["101", None]})]
        h = self.age([self.host("esx01", "101", None)], runs)["esx01"]
        self.assertEqual((h["days"], h["at_least"]), (30, False))                            # the first run that saw it

    def test_no_change_long_enough_is_late(self):
        runs = [run(50, esxi={"esx02": ["200", NOW - 52 * D]}), run(20, esxi={"esx02": ["200", NOW - 52 * D]})]
        h = self.age([self.host("esx02", "200", 52)], runs)["esx02"]
        self.assertEqual((h["days"], h["at_least"], pa.host_status(h)), (52, True, "late"))

    def test_no_change_short_is_not_known_yet(self):
        h = self.age([self.host("esx03", "300", 4)], [])["esx03"]                           # the first run: the uptime only
        self.assertEqual((h["days"], h["at_least"], pa.host_status(h)), (4, True, "unknown"))

    def test_a_reboot_without_a_build_change_keeps_the_floor(self):
        runs = [run(70, esxi={"esx04": ["400", NOW - 75 * D]}), run(40, esxi={"esx04": ["400", NOW - 75 * D]})]
        h = self.age([self.host("esx04", "400", 1)], runs)["esx04"]                         # rebooted yesterday, same build
        self.assertEqual((h["days"], h["at_least"], pa.host_status(h)), (75, True, "late"))

    def test_a_run_without_the_host_does_not_end_the_streak(self):
        runs = [run(60, esxi={"esx05": ["500", None]}), run(30, esxi={}), run(10, esxi={"esx05": ["500", None]})]
        h = self.age([self.host("esx05", "500", 2)], runs)["esx05"]
        self.assertEqual((h["days"], h["at_least"]), (60, True))

    def test_not_connected_is_no_data_and_restart_not_known(self):
        h = self.age([self.host("esx06", "", None)], [])["esx06"]
        self.assertEqual((h["days"], pa.host_status(h), h["restart"]), (None, "nodata", None))

    def test_not_known_yet_in_the_report(self):
        hosts = pa.patch_age_esxi([self.host("esx03", "300", 4), self.host("esx07", "700", 3)],
                                  [run(10, esxi={"esx07": ["699", None]})], {"now": NOW, "late_after": 35})
        r = pa.patch_age_sections(hosts, {"what": "ESXi", "now": NOW, "setting": "patch_age_esxi_late_days"}, [])
        g, old = r["sections"]
        self.assertIn("Not known yet", g["columns"])
        self.assertEqual(g["rows"][0][2:6], [1, "-", "-", 1])                      # esx07 on time (a real date), esx03 not known
        self.assertEqual(g["rows"][0][7], "50%")                                 # not known is not on time
        self.assertEqual(g["rows"][0][-1], "1 not known yet")
        self.assertEqual(g["row_status"], ["low"])
        self.assertIn("Not known yet (Low): the update date is known once the build changes, or after 35 days of history.", old["lines"])
        self.assertEqual(old["rows"][0][2:6], ["not known", "at least 4", "-", "Not known yet"])
        self.assertEqual([b["label"] for b in g["boxes"]][-1], "Patch date not known yet")

    def test_artifact_and_groups(self):
        hs = [self.host("esx01", "101", 3), dict(self.host("esx02", "", None), vcenter="vc02.corp.example.mil", cluster="")]
        self.assertEqual(pa.patch_age_esxi_artifact(hs), {"esx01": ["101", NOW - 3 * D], "esx02": ["", None]})
        self.assertEqual([h["group"] for h in pa.patch_age_esxi(hs, [], {"now": NOW})], ["vc01 / CL-SITEA", "vc02 / (standalone)"])


class Findings(unittest.TestCase):
    def test_boxes_join_and_the_status_stays(self):
        r = pa.patch_age_sections([lin("web01.corp.example.mil", 80)], {"now": NOW})
        rep = sf.findings_report({"web01.corp.example.mil": [], "web02.corp.example.mil": []}, "Health check", [], ["patching"], r["sections"])
        self.assertEqual(rep["status"], "ok")
        self.assertEqual(rep["title"], "Health check: no findings - all 2 host(s) healthy")
        self.assertIn("Patching late", [b["label"] for b in rep["summary"]])
        self.assertEqual([s["title"] for s in rep["sections"]], ["Patch age by group", "Patch age: the oldest host", "Healthy (2)"])


class Looks(unittest.TestCase):
    def test_squares_in_atlas_and_brief(self):
        import test_report_fit as T
        r = pa.patch_age_sections(Sections().fleet(), {"now": NOW})
        doc = {"title": "Health check", "status": "warning", "summary": r["boxes"], "sections": r["web_sections"]}
        for look in ("atlas", "brief"):
            h = T.render(look, doc)
            self.assertIn('<div class="sq-legend"><span>Days since the last update:</span>', h)
            self.assertIn('title="web04.corp.example.mil: 75 days (2026-10-01) - Late"', h)
            self.assertEqual(h.count('<i class="sq q'), 6 + 7)
            self.assertIn(">2 late<", h.replace("&nbsp;", " "))
            self.assertIn("--q5:#0e3a6d", h)
            self.assertIn("--q5:#dff0ff", h)
        for look in ("command", "portal", "glass"):
            self.assertNotIn("sq-legend", T.render(look, dict(doc, sections=r["sections"])))

    def test_report_fit_document(self):
        hs = [lin("web%02d.corp.example.mil" % i, d, group="SITEA-linux-web") for i, d in enumerate(range(2, 80, 3))]
        hs += [win("app%02d.corp.example.mil" % i, d, group="SITEB-windows-app-servers") for i, d in enumerate((70, 20, None, 34))]
        hs += pa.patch_age_esxi([{"name": "esx%02d.corp.example.mil" % i, "cluster": "CL-SITEA-PROD", "vcenter": "vc01", "build": "24022510",
                                  "boot": NOW - (5 + 9 * i) * D} for i in range(6)], [], {"now": NOW})
        r = pa.patch_age_sections(hs, {"now": NOW}, [run(30, {"SITEA-linux-web": [26, 20]})])
        self.assertEqual(len(r["sections"][0]["columns"]), 10)
        web = sf.findings_report({"web00.corp.example.mil": [{"severity": "warning", "check": "patching", "summary": "no package has been "
                                                              "updated for 77 days (limit 35)", "hint": "rpm -qa --last | head -10"}]},
                                 "Health check", [], ["patching"], r["web_sections"])
        self.assertEqual(web["sections"][2]["title"], "Patch age at a glance")


class History(unittest.TestCase):
    """site_report_history's artifacts option, against the fake AAP (tests/servicenow/aapmock.py)."""

    def test_named_artifacts(self):
        from http.server import ThreadingHTTPServer
        spec = importlib.util.spec_from_file_location("ansible.module_utils.site_aap", os.path.join(REPO, "plugins", "module_utils", "site_aap.py"))
        aap = importlib.util.module_from_spec(spec)
        sys.modules["ansible.module_utils.site_aap"] = aap
        spec.loader.exec_module(aap)
        sys.path.insert(0, os.path.join(REPO, "roles", "site_email", "library"))
        import site_report_history as rh
        spec = importlib.util.spec_from_file_location("aapmock_pa", os.path.join(HERE, "servicenow", "aapmock.py"))
        mock = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mock)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), mock.H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            env = {"CONTROLLER_HOST": "http://127.0.0.1:%d" % srv.server_port, "CONTROLLER_USERNAME": "admin", "CONTROLLER_PASSWORD": "goodpw"}
            runs, why = rh.history(aap.Api(aap.connection(env)), 919, 71, 13, ["other", "patch_age"])
            self.assertEqual(why, "")
            self.assertEqual(runs[-1]["artifacts"], {"other": 1})
            self.assertEqual(runs[0]["artifacts"], {})
            runs, _ = rh.history(aap.Api(aap.connection(env)), 919, 71, 13)
            self.assertNotIn("artifacts", runs[-1])
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
