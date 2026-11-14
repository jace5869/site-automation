#!/usr/bin/env python3
"""The cluster heat grid (1.65.0, plugins/filter/vmware_heat_filters.py): a made-up vCenter with SITEA-PROD (8 hosts, one
not responding, one at 93% memory), SITEA-MGMT (one in maintenance), SITEB-DMZ (one needing a restart) and a standalone
host - each host's status (the health report's), the cards, NEW against a week ago, back to OK, a cluster's CPU moving,
the lines from earlier runs (the day's average), the hosts not OK with how long, the numbers, the boxes, the workbook's
sheet with its sparklines, where the sections go, the grid in the atlas and brief looks only, and the report database's
points (site_vm_heat_db).
  python3 tests/test_vmware_heat.py"""
import calendar
import copy
import datetime
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
sys.path.insert(0, os.path.join(REPO, "roles", "vmware_vm", "library"))
sys.path.insert(0, os.path.join(REPO, "plugins", "module_utils"))
import vmware_heat_filters as hf  # noqa: E402
import site_report_db  # noqa: E402
import ansible.module_utils  # noqa: E402
sys.modules.setdefault("ansible.module_utils.site_report_db", site_report_db)
ansible.module_utils.site_report_db = site_report_db
import site_vm_heat_db as hdb  # noqa: E402

VC = "vc01.corp.example.mil"
NOW = "2026-11-13T05:00:00Z"
NOW_E = calendar.timegm((2026, 11, 13, 5, 0, 0))
DAY = 86400
OPTS = {"days": 30, "change_pts": 5}


def host(name, cluster, cpu=30, mem=60, dc="DC1", **kw):
    h = {"vcenter": VC, "datacenter": dc, "cluster": cluster, "standalone": not cluster, "name": name + ".corp.example.mil",
         "connection": "connected", "power": "poweredOn", "maintenance": False, "standby": "none", "status": "green",
         "ha_state": "connectedToMaster", "cpu_used_mhz": cpu * 1000, "cpu_mhz": 100000, "mem_used_mb": mem * 1000, "mem_mb": 100000,
         "uptime_days": 40, "reboot_required": False, "sensors_bad": [], "config_issues": [], "alarms": []}
    h.update(kw)
    return h


def cluster(name, dc="DC1"):
    return {"vcenter": VC, "datacenter": dc, "name": name, "status": "green",
            "ha": {"enabled": True, "host_monitoring": "enabled", "admission": "hosts", "admission_hosts": 1, "failover_level_now": 1},
            "drs": {"enabled": True, "behavior": "fullyAutomated", "recommendations": 0, "faults": 0, "score": 97},
            "vcls_retreat": False, "config_issues": [], "alarms": []}


def read():
    hosts = [host("esx%02d" % i, "SITEA-PROD", cpu=40 + i, mem=60 + i) for i in range(1, 9)]
    hosts[2].update(mem_used_mb=93000)                                       # esx03: memory 93% - warning
    hosts[6].update(connection="notResponding", cpu_used_mhz=0, mem_used_mb=0)  # esx07: critical
    hosts += [host("esx%02d" % i, "SITEA-MGMT", cpu=50, mem=60) for i in range(9, 13)]
    hosts[9].update(maintenance=True)                                        # esx10: maintenance - Low
    hosts += [host("esx%02d" % i, "SITEB-DMZ", cpu=45, mem=70) for i in range(25, 28)]
    hosts[-1].update(reboot_required=True)                                   # esx27: needs a restart - warning
    hosts.append(host("esx90", "", dc="DC1"))
    return {"vcenter": VC, "vcenters": [{"name": VC, "ok": True}], "read_at": NOW, "datacenters": [{"vcenter": VC, "name": "DC1", "alarms": []}],
            "clusters": [cluster("SITEA-PROD"), cluster("SITEA-MGMT"), cluster("SITEB-DMZ")], "hosts": hosts}


def run(days_ago, clusters, hosts, hour=5):
    t = datetime.datetime(2026, 11, 13, hour, 0, tzinfo=datetime.timezone.utc) - datetime.timedelta(days=days_ago)
    at = t.strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": 500 - days_ago, "finished": at, "artifacts": {"vm_heat": {"read_at": at, "clusters": clusters, "hosts": hosts}}}


K = {"prod": VC + "/DC1/SITEA-PROD", "mgmt": VC + "/DC1/SITEA-MGMT", "dmz": VC + "/DC1/SITEB-DMZ"}


def history():
    """29 earlier days: SITEA-PROD's CPU from 30% up; a week ago esx27 already needed a restart (warning) and esx12 was a
    warning (back to OK now); esx27 the same for 12 days."""
    runs = []
    for d in range(29, 0, -1):
        hs = {}
        if d <= 12:
            hs[VC + "/esx27.corp.example.mil"] = "warning"
        if 5 <= d <= 9:
            hs[VC + "/esx12.corp.example.mil"] = "warning"
        runs.append(run(d, {K["prod"]: [30 + (29 - d) * 0.5, 60], K["mgmt"]: [50, 60], K["dmz"]: [45, 70]}, hs))
    runs.append(run(0, {K["prod"]: [40, 61]}, {VC + "/esx27.corp.example.mil": "warning"}, hour=1))   # earlier today: this run's value is today's
    return runs


def view(runs=None, data=None, **o):
    return hf.vm_heat_view(data or read(), runs if runs is not None else history(), dict(OPTS, **o))


def card(v, cl, name):
    c = [x for x in v["web_sections"][0]["heat"]["clusters"] if x["name"].endswith(cl)][0]
    return [x for x in c["cards"] if x["name"] == name][0]


def row(v, cl):
    return [x for x in v["web_sections"][0]["heat"]["clusters"] if x["name"].endswith(cl)][0]


class Hosts(unittest.TestCase):
    def test_status_is_the_health_reports(self):
        v = view()
        self.assertEqual((card(v, "SITEA-PROD", "esx07")["st"], card(v, "SITEA-PROD", "esx07")["body"]), ("critical", "not responding"))
        self.assertEqual((card(v, "SITEA-PROD", "esx03")["st"], card(v, "SITEA-PROD", "esx03")["mem"]), ("warning", "93%"))
        self.assertEqual((card(v, "SITEA-MGMT", "esx10")["st"], card(v, "SITEA-MGMT", "esx10")["body"]), ("low", "maintenance"))
        self.assertEqual(card(v, "SITEB-DMZ", "esx27")["st"], "warning")
        self.assertEqual((card(v, "SITEA-PROD", "esx01")["st"], card(v, "SITEA-PROD", "esx01")["cpu"]), ("ok", "41%"))
        self.assertEqual(row(v, "SITEA-PROD")["cpu"], "44%")                    # the hosts running VMs: esx07 not
        self.assertEqual(v["counts"]["not_ok"], 4)
        self.assertEqual([x["name"] for x in v["web_sections"][0]["heat"]["clusters"]],
                         ["SITEA-PROD", "SITEA-MGMT", "SITEB-DMZ", "standalone hosts"])
        self.assertIn("Memory 93% used", card(v, "SITEA-PROD", "esx03")["tip"])

    def test_new_and_back(self):
        v = view()
        self.assertTrue(card(v, "SITEA-PROD", "esx07")["new"])
        self.assertTrue(card(v, "SITEA-PROD", "esx03")["new"])
        self.assertFalse(card(v, "SITEB-DMZ", "esx27")["new"])                   # already a warning a week ago
        self.assertIn("New since last week (it was OK)", card(v, "SITEA-PROD", "esx07")["tip"])
        self.assertEqual(row(v, "SITEA-MGMT")["change"], "esx10 new low; esx12 back to OK (was warning)")
        self.assertEqual(row(v, "SITEB-DMZ")["change"], "no change")
        self.assertEqual((v["counts"]["new"], v["counts"]["back"]), (3, 1))

    def test_cluster_moved(self):
        ch = row(view(), "SITEA-PROD")["change"]
        self.assertIn("memory 60% to 68%", ch)                                   # 8 points: said
        self.assertNotIn("CPU", ch)                                              # 41% a week ago (30 + 22 x 0.5), 44% now: 3 points

    def test_no_week_ago(self):
        v = view(runs=[])
        self.assertFalse(any(c["new"] for r in v["web_sections"][0]["heat"]["clusters"] for c in r["cards"]))
        self.assertEqual(row(v, "SITEA-PROD")["change"], "")
        self.assertIn("No run about a week ago: nothing marked new yet.", v["web_sections"][0]["lines"][0])
        self.assertIn("One day of history so far: the lines start with the next runs.", v["web_sections"][0]["lines"])


class Lines(unittest.TestCase):
    def test_days(self):
        r = row(view(), "SITEA-PROD")
        self.assertEqual(len(r["cpu_points"]), 30)                              # 29 earlier days and today (this run)
        self.assertEqual(r["cpu_points"][0][1], 30.0)
        self.assertEqual(r["cpu_points"][-1], [NOW_E, 44.1])
        self.assertEqual(r["cpu"], "44%")

    def test_the_days_average(self):
        runs = [run(3, {K["prod"]: [20, 50]}, {}, hour=1), run(3, {K["prod"]: [40, 70]}, {}, hour=9)]
        self.assertEqual(row(view(runs=runs), "SITEA-PROD")["cpu_points"][0][1], 30.0)

    def test_outside_the_window(self):
        self.assertEqual(len(row(view(runs=[run(40, {K["prod"]: [20, 50]}, {})]), "SITEA-PROD")["cpu_points"]), 1)


class Tables(unittest.TestCase):
    def test_hosts_not_ok(self):
        s = view()["web_sections"][1]
        self.assertEqual(s["columns"], ["Cluster", "Host", "Status", "Why", "Since"])
        self.assertEqual([(r[1].split(".")[0], r[2], r[4]) for r in s["rows"]],
                         [("esx07", "Critical", "new (was OK)"), ("esx03", "Warning", "new (was OK)"),
                          ("esx27", "Warning", "same for 12 days"), ("esx10", "Low", "new (was OK)")])
        self.assertEqual(s["rows"][0][3], "Not responding to vCenter")
        self.assertEqual(s["row_status"], ["critical", "warning", "warning", "low"])

    def test_numbers(self):
        s = view()["web_sections"][2]
        self.assertEqual(s["columns"], ["Cluster", "CPU 30 days ago", "CPU now", "Change", "Memory 30 days ago", "Memory now", "Change"])
        self.assertEqual(s["rows"][0], ["SITEA-PROD", "30%", "44%", "+14", "60%", "68%", "+8"])
        self.assertEqual(s["rows"][3][1:4], ["-", "30%", ""])                   # the standalone host: no history

    def test_boxes(self):
        b = view()["boxes"]
        self.assertEqual(b[0], {"label": "Hosts not OK", "value": 4, "status": "critical", "note": "a week ago 2: +3 new, -1 back to OK"})
        self.assertEqual(b[1], {"label": "Fastest change", "value": "SITEA-PROD", "status": "info", "note": "CPU 30% to 44% in 30 days"})
        self.assertEqual([x["label"] for x in view(runs=[])["boxes"]], ["Hosts not OK"])

    def test_the_email_has_the_numbers_only(self):
        v = view()
        self.assertEqual([s["title"] for s in v["sections"]], ["Clusters: CPU and memory over 30 days"])
        self.assertNotIn("heat", v["sections"][0])

    def test_artifact(self):
        a = view()["artifact"]
        self.assertEqual(a["read_at"], NOW)
        self.assertEqual(a["clusters"][K["prod"]], [44.1, 68.4])
        self.assertEqual(a["hosts"], {VC + "/esx03.corp.example.mil": "warning", VC + "/esx07.corp.example.mil": "critical",
                                      VC + "/esx10.corp.example.mil": "low", VC + "/esx27.corp.example.mil": "warning"})

    def test_workbook(self):
        sh = view()["sheets"][0]
        self.assertEqual(sh["name"], "Clusters by day")
        self.assertEqual(sh["columns"][:4], ["Cluster", "Measure", "Now %", "Trend"])
        self.assertEqual(len(sh["columns"]), 4 + 30)
        self.assertEqual(sh["sparklines"], [{"column": "Trend", "from": [sh["columns"][4], sh["columns"][-1]]}])
        self.assertEqual(sh["rows"][0][:3], ["SITEA-PROD", "CPU", 44.1])
        sys.path.insert(0, os.path.join(REPO, "roles", "site_email", "library"))
        import site_xlsx                                                        # the lines drawn: one per row with 2 days or more
        plan = site_xlsx.spark_plan(sh["columns"], sh["rows"], sh["sparklines"])
        self.assertEqual(len(plan["lines"]), 6)

    def test_no_host(self):
        self.assertEqual(hf.vm_heat_view({"hosts": []}, [], {}), {})


class Place(unittest.TestCase):
    def test_before_needs_attention_and_the_boxes(self):
        rep = {"summary": [{"label": "Hosts"}], "sections": [{"title": "Needs attention"}, {"title": "Hosts"}]}
        v = view()
        web = hf.vm_heat_insert(rep, v["web_sections"], v["boxes"])
        self.assertEqual([s["title"] for s in web["sections"]], ["Every host, by cluster", "Hosts that are not OK",
                                                                 "Clusters: CPU and memory over 30 days", "Needs attention", "Hosts"])
        self.assertEqual([b["label"] for b in web["summary"]], ["Hosts", "Hosts not OK", "Fastest change"])
        mail = hf.vm_heat_insert(rep, v["sections"], v["boxes"], "Hosts")
        self.assertEqual([s["title"] for s in mail["sections"]], ["Needs attention", "Clusters: CPU and memory over 30 days", "Hosts"])
        self.assertEqual([s["title"] for s in rep["sections"]], ["Needs attention", "Hosts"])


class Looks(unittest.TestCase):
    def doc(self, v, sections):
        return hf.vm_heat_insert({"title": "VMware health report: 1 critical, 2 warning(s)", "status": "critical", "summary": [],
                                  "sections": [{"title": "Needs attention", "text": "-"}]}, sections, v["boxes"])

    def test_atlas_and_brief(self):
        import test_report_fit as T
        v = view()
        for look in ("atlas", "brief"):
            h = T.render(look, self.doc(v, v["web_sections"]))
            self.assertEqual(h.count('<div class="hg-row">'), 4)
            self.assertEqual(h.count('class="hg-h s-'), 16)
            self.assertEqual(h.count('<i class="hg-new">NEW</i>'), 3 + 1)      # three cards and the legend
            self.assertIn('<div class="hg-h s-critical" role="listitem" data-tip="esx07.corp.example.mil\nCritical: Not responding', h)
            self.assertEqual(h.count('<svg class="spark"'), 6)                 # CPU and memory of the three clusters with history
            self.assertIn("no line yet", h)                                    # the standalone host
            self.assertIn(".hg-row{display:grid", h)

    def test_not_in_an_email(self):
        import test_report_fit as T
        v = view()
        for look in ("command", "portal", "glass"):
            h = T.render(look, self.doc(v, v["sections"]))
            self.assertNotIn('class="hg-', h)
            self.assertIn("Clusters: CPU and memory over 30 days", h)


class FakeCursor(object):
    def __init__(self, rows):
        self.rows, self.out, self.many = rows, [], []

    def execute(self, sql, args=None):
        if sql.startswith("SELECT read_at, job, cluster"):
            self.out = sorted([r for r in self.rows if args[0] <= r[0] < args[1]], key=lambda r: (r[0], r[2]))
        return 0

    def executemany(self, sql, rows):
        self.many.append((sql, list(rows)))

    def fetchall(self):
        return self.out


class FakeConn(object):
    def __init__(self, rows=()):
        self.cur = FakeCursor(list(rows))

    def cursor(self):
        return self.cur


class Database(unittest.TestCase):
    def test_read_as_history_runs(self):
        at = datetime.datetime(2026, 11, 6, 5, 0)
        rows = [(at, 41, K["prod"], 41.0, 62.5, None), (at, 41, "*", None, None, '{"%s/esx27.corp.example.mil": "warning"}' % VC)]
        runs = hdb.read(FakeConn(rows), hdb.stamp(NOW), 30)
        self.assertEqual(runs, [{"id": 41, "finished": "2026-11-06T05:00:00Z", "artifacts": {"vm_heat": {
            "read_at": "2026-11-06T05:00:00Z", "clusters": {K["prod"]: [41.0, 62.5]}, "hosts": {VC + "/esx27.corp.example.mil": "warning"}}}}])
        self.assertFalse(card(view(runs=runs), "SITEB-DMZ", "esx27")["new"])     # a week ago it was a warning already

    def test_write(self):
        conn = FakeConn()
        n = hdb.write(conn, hdb.stamp(NOW), 42, {K["prod"]: [46.0, 65.0], K["dmz"]: [None, 70]}, {"h": "warning"})
        self.assertEqual(n, 3)
        rows = conn.cur.many[0][1]
        self.assertEqual(rows[-1][2:], ("*", None, None, '{"h": "warning"}'))
        self.assertEqual(rows[1][3:5], (None, 70.0))

    def test_the_statement_in_the_doc_is_the_modules(self):
        doc = open(os.path.join(REPO, "docs", "REPORT_DATABASE.md")).read()
        for part in hdb.ddl().split("\n\n"):
            self.assertIn(part, doc, "docs/REPORT_DATABASE.md: the statement differs from site_vm_heat_db.ddl()")

    def test_the_settings_are_in_the_defaults_and_the_docs(self):
        defaults = open(os.path.join(REPO, "roles", "vmware_vm", "defaults", "main.yml")).read()
        ref = open(os.path.join(REPO, "docs", "VARIABLES_REFERENCE.md")).read()
        guide = open(os.path.join(REPO, "docs", "VMWARE.md")).read()
        for name in ("vm_heat", "vm_heat_days", "vm_heat_change_pts", "vm_heat_history", "vm_heat_history_runs", "vm_heat_db",
                     "vm_heat_db_create", "vm_heat_db_timeout", "vm_heat_db_keep_days"):
            self.assertIn("\n%s:" % name, defaults, name)
            self.assertIn("`%s`" % name, ref, name)
            self.assertIn("`%s`" % name, guide, name)


class Fit(unittest.TestCase):
    """The documents test_report_fit draws (tests/report_fit_docs.py NAMED): the website's copy with the grid, the email's,
    and a big site - 40 hosts in a cluster, long cluster names."""
    def test_report_fit_web(self):
        v = view()
        hf.vm_heat_insert({"title": "VMware health report: 1 critical, 2 warning(s) - 1 datacenter(s), 3 cluster(s), 16 host(s)",
                           "status": "critical", "summary": [], "sections": [{"title": "Needs attention", "text": "-"}]},
                          v["web_sections"], v["boxes"])

    def test_report_fit_email(self):
        v = view()
        hf.vm_heat_insert({"title": "VMware health report (vc01)", "status": "critical", "summary": [],
                           "sections": [{"title": "Hosts", "text": "-"}]}, v["sections"], v["boxes"], "Hosts")

    def test_report_fit_big(self):
        d = read()
        d["clusters"].append(cluster("SITEB-PRODUCTION-COMPUTE-EAST"))
        d["hosts"] += [host("esx-siteb-prod-east-%03d" % i, "SITEB-PRODUCTION-COMPUTE-EAST", cpu=30 + i, mem=40 + i) for i in range(40)]
        v = hf.vm_heat_view(d, history(), OPTS)
        self.assertEqual(len(row(v, "SITEB-PRODUCTION-COMPUTE-EAST")["cards"]), 40)
        hf.vm_heat_insert({"title": "VMware health report", "status": "critical", "summary": [], "sections": []}, v["web_sections"], v["boxes"])


if __name__ == "__main__":
    unittest.main()
