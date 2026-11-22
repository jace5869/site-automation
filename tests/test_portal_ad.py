#!/usr/bin/env python3
"""1.101.2: the portal's Active Directory page (/portal/ad) from the report database - the AD job's writer
(roles/ad_report/library/site_ad_db.py, plugins/filter/ad_db_filters.py), the portal's reader (site_portal.py AdDb: now, since
when, the last 48 runs, the week), its answer (AdLive: the database's runs or why not, SolarWinds for the newest run, the link
to the full report, a read that fails said as stale), the routes, the deploy's files and roles/site_portal_stack/files/static/
ad-page.js in Chromium (every part drawn, every state said, a DC that changes said). SQLite stands in for MySQL here; tests/ad/
run_ad_db_test.sh runs the same against a real MySQL 8.4. Placeholders only.
  python tests/test_portal_ad.py"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FILES = os.path.join(ROOT, "roles", "site_portal_stack", "files")
sys.path[:0] = [os.path.join(ROOT, "plugins", "module_utils"), os.path.join(ROOT, "plugins", "filter"), FILES, HERE,
                os.path.join(HERE, "site_portal")]
import site_portal as sp  # noqa: E402
import ad_swis_filters as af  # noqa: E402
import ad_db_filters as dbf  # noqa: E402
import site_report_db  # noqa: E402
import ansible.module_utils  # noqa: E402
sys.modules.setdefault("ansible.module_utils.site_report_db", site_report_db)
ansible.module_utils.site_report_db = site_report_db
sys.path.insert(0, os.path.join(ROOT, "roles", "ad_report", "library"))
import site_ad_db as adb  # noqa: E402

JDOE = "CN=DOE.JANE.Q.1234567890,OU=CONTRACTOR,OU=PKI,O=U.S. Government,C=US"


def utc(minutes_ago):
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(time.time() - minutes_ago * 60))


def sw_answer(down=("DC02",)):
    """site_ad_swis.from_live's shape: DC01-DC03 as SolarWinds sees them."""
    def d(name):
        return {"dc": name, "ip": "", "matched": "name", "status": "Down" if name in down else "Up", "status_text": "",
                "cpu": None if name in down else 12, "mem": None if name in down else 40, "alerts": [], "apps": {}, "last_down": None}
    return {"ok": True, "source": "swis-live", "read_at": time.strftime("%Y-%m-%d %H:%M", time.gmtime()), "age_min": 0, "why": [],
            "sam": False, "sam_why": "SAM's applications are not on this Orion", "events": False,
            "dcs": [d("DC01"), d("DC02"), d("DC03")]}


class Site(object):
    """A portal's folders: data, actions, and the report website's index.json with the AD job's line (live: ad)."""

    def __init__(self, test, when=None, index=True):
        self.tmp = tempfile.mkdtemp()
        test.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "reports")
        os.makedirs(os.path.join(self.root, "ad-health"))
        os.makedirs(os.path.join(self.tmp, "data"))
        with open(os.path.join(self.tmp, "a.json"), "w") as fh:
            json.dump({"actions": {}}, fh)
        self.when = when or utc(20)
        if index:
            self.index()

    def index(self, extra=(), rel="ad-health/latest.html"):
        lines = [{"key": rel, "latest": rel, "when": self.when, "job": "77", "live": "ad"},
                 {"key": "linux/latest.html", "latest": "linux/latest.html", "when": utc(1), "job": "5"}] + list(extra)
        with open(os.path.join(self.root, "index.json"), "w") as fh:
            json.dump({"reports": lines}, fh)

    def cfg(self, **env):
        e = {"PORTAL_DATA": os.path.join(self.tmp, "data"), "PORTAL_REPORTS": self.root, "PORTAL_ACTIONS": os.path.join(self.tmp, "a.json"),
             "PORTAL_AAP_URL": "http://127.0.0.1:9", "PORTAL_AAP_TOKEN": "x", "PORTAL_TIME_ZONE": ""}
        e.update(env)
        return sp.Config(e)


SQLITE = ("CREATE TABLE ad_run (run_id INTEGER PRIMARY KEY AUTOINCREMENT, read_at TEXT NOT NULL, job INTEGER NOT NULL DEFAULT 0, "
          "template_id INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT '', critical INTEGER NOT NULL DEFAULT 0, "
          "warning INTEGER NOT NULL DEFAULT 0, dcs INTEGER NOT NULL DEFAULT 0, dcs_read INTEGER NOT NULL DEFAULT 0, live TEXT)",
          "CREATE TABLE ad_dc (run_id INTEGER NOT NULL, dc TEXT NOT NULL, domain TEXT NOT NULL DEFAULT '', site TEXT NOT NULL DEFAULT '', "
          "is_read INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT '', problems INTEGER NOT NULL DEFAULT 0, worst TEXT NOT NULL DEFAULT '', "
          "PRIMARY KEY (run_id, domain, dc))",
          "CREATE TABLE ad_link (run_id INTEGER NOT NULL, frm TEXT NOT NULL, to_dc TEXT NOT NULL, nc TEXT NOT NULL DEFAULT '', last_success TEXT, "
          "failures INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', last_failure TEXT, status TEXT NOT NULL DEFAULT '', "
          "PRIMARY KEY (run_id, frm, to_dc, nc))")


class Lite(object):
    """A SQLite session that takes the MySQL statements of site_ad_db (%s marks; ON DUPLICATE KEY dropped) - the tests' report database."""

    def __init__(self):
        import sqlite3
        self.db = sqlite3.connect(":memory:", detect_types=0)
        for c in SQLITE:
            self.db.execute(c)

    def cursor(self):
        db = self.db

        class C(object):
            lastrowid = None

            def _sql(self, sql):
                return re.sub(r" ON DUPLICATE KEY UPDATE .*$", "", sql).replace("%s", "?")

            def _args(self, a):
                return tuple(x.strftime("%Y-%m-%d %H:%M:%S") if hasattr(x, "strftime") else x for x in a)

            def execute(self, sql, args=()):
                c = db.execute(self._sql(sql), self._args(args))
                self.lastrowid, self.rows = c.lastrowid, c.fetchall()
                return c.rowcount

            def executemany(self, sql, rows):
                db.executemany(self._sql(sql), [self._args(r) for r in rows])

            def fetchall(self):
                return self.rows
        return C()

    def commit(self):
        self.db.commit()

    def close(self):
        pass


def ad_run(minutes_ago, dc3="ok", fail_link=True):
    """_ad as ad_filters makes it: DC01-DC03, DC03's state as given, one link failing."""
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, second=0, microsecond=0) - dt.timedelta(minutes=minutes_ago)
    iso = lambda h: (now - dt.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")      # noqa: E731
    probs = [{"domain": "corp.test", "object": "DC03", "severity": dc3, "text": "dcdiag: Replications failed (8524)"}] if dc3 != "ok" else []
    return now, {"report": {"status": "critical" if dc3 == "critical" else "ok"}, "critical": 1 if dc3 == "critical" else 0, "warning": 0,
                 "problems": probs,
                 "dcs": [{"dc": "DC01", "domain": "corp.test", "read": True}, {"dc": "DC02", "domain": "corp.test", "read": True},
                         {"dc": "DC03", "domain": "corp.test", "read": True}],
                 "repl": {"now": iso(0), "dcs": [{"name": "DC01", "site": "SITA0101"}, {"name": "DC02", "site": "SITA0101"}, {"name": "DC03", "site": "SITB0201"}],
                          "links": [{"frm": "DC01", "to": "DC03", "nc": "corp.test", "last_success": iso(31 if fail_link else 0.2), "failures": 3 if fail_link else 0,
                                     "error": "8524" if fail_link else "", "last_failure": iso(0.1)},
                                    {"frm": "DC02", "to": "DC01", "nc": "Configuration", "last_success": iso(0.1), "failures": 0}]}}


def fill(states, template_id=12):
    db = Lite()
    for k, st in enumerate(states):                   # oldest first, every 30 min
        now, ad = ad_run(30 * (len(states) - 1 - k), st)
        adb.write(db, now, 9000 + k, template_id, dbf.ad_db_payload(ad, {"kind": "ad", "dcs": [{"dc": "DC01"}]}, {}))
    db.commit()
    return db


class Db1012(unittest.TestCase):
    """The AD job's run written (site_ad_db, ad_db_filters) and read back by the portal (AdDb): now, since when, the trend, the week."""

    def test_1_round_trip(self):
        db = fill(["ok"] * 10 + ["critical"] * 4)
        m = sp.AdDb(None, connect=lambda: db, mark="?").read()
        self.assertEqual((m["run"]["job"], m["run"]["status"], m["run"]["dcs"], m["run"]["dcs_read"]), (9013, "critical", 3, 3))
        self.assertEqual([d["dc"] for d in m["dcs"]], ["DC03", "DC01", "DC02"])           # worst first
        d3 = m["dcs"][0]
        self.assertEqual((d3["now"], d3["runs_since"], d3["site"]), ("critical", 4, "SITB0201"))
        self.assertEqual(d3["since"], m["times"][-4])                                     # the run its state began
        self.assertEqual(d3["trend"], ["ok"] * 10 + ["critical"] * 4)
        self.assertIn("8524", d3["worst"])
        self.assertEqual([(x["frm"], x["to"], x["now"], x["failures"]) for x in m["links"]], [("DC01", "DC03", "critical", 3)])   # ok links left out
        self.assertEqual(len(m["hist"]), 14)
        self.assertEqual((m["hist"][-1]["c"], m["hist"][0]["c"]), (1, 0))
        self.assertEqual(m["live"]["kind"], "ad")

    def test_2_since_further_back_than_the_trend(self):
        db = fill(["critical"] * 3 + ["ok"] * 2 + ["critical"] * 50)
        d3 = [d for d in sp.AdDb(None, connect=lambda: db, mark="?").read()["dcs"] if d["dc"] == "DC03"][0]
        self.assertTrue(d3["longer"])
        self.assertEqual(len(d3["trend"]), 48)
        self.assertEqual(d3["runs_since"], 48)
        import datetime as dt
        began = dt.datetime.strptime(d3["since"], "%Y-%m-%dT%H:%M:%SZ")
        age = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - began).total_seconds() / 60
        self.assertTrue(49 * 30 - 5 <= age <= 50 * 30 + 5, age)                           # 50 runs back: after the last ok

    def test_3_template_and_nothing_yet(self):
        db = fill(["ok"] * 3, template_id=12)
        self.assertIsNone(sp.AdDb(None, connect=lambda: db, mark="?").read(template_id=99)["run"])
        self.assertEqual(sp.AdDb(None, connect=lambda: db, mark="?").read(template_id=12)["run"]["template_id"], 12)

    def test_4_the_writer_prunes_and_says_its_tables(self):
        db = fill(["ok"] * 3)
        import datetime as dt
        n = adb.prune(db, dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(days=401), 400)
        self.assertEqual(n, 3)
        self.assertEqual(db.db.execute("SELECT COUNT(*) FROM ad_dc").fetchone()[0], 0)
        ddl = adb.ddl()
        for t in ("CREATE TABLE IF NOT EXISTS ad_run", "CREATE TABLE IF NOT EXISTS ad_dc", "CREATE TABLE IF NOT EXISTS ad_link", "'ad_run', 1"):
            self.assertIn(t, ddl)

    def test_6_the_statements_in_the_doc_are_the_modules(self):
        doc = open(os.path.join(ROOT, "docs", "REPORT_DATABASE.md")).read()
        for part in adb.ddl().split("\n\n"):
            self.assertIn(part, doc, "docs/REPORT_DATABASE.md: the statement differs from site_ad_db.ddl()")

    def test_5_payload_never_raises(self):
        self.assertIn("were not made into rows", dbf.ad_db_payload({"dcs": "not a list"}, {}, {})["why"])
        p = dbf.ad_db_payload(ad_run(0, "warning")[1], {}, {"crit_hours": 24})
        self.assertEqual([(d["dc"], d["status"]) for d in p["dcs"]], [("DC01", "ok"), ("DC02", "ok"), ("DC03", "warning")])
        self.assertEqual([x["status"] for x in p["links"]], ["critical", "ok"])
        p = dbf.ad_db_payload({"dcs": [{"dc": "DC09", "domain": "corp.test", "read": False, "error": "WinRM did not answer"}]})
        self.assertEqual((p["dcs"][0]["status"], p["dcs"][0]["worst"]), ("critical", "not read: WinRM did not answer"))


def live(lite, site, **kw):
    """An AdLive on the SQLite database, SolarWinds stood in (DC02 Down unless said)."""
    a = sp.AdLive(site.cfg(**kw.pop("env", {})), sp.Log(None), source=lambda lv: sw_answer(kw.get("down", ("DC02",))),
                  db=sp.AdDb(None, connect=lambda: lite, mark="?") if lite is not None else None)
    a.cfg.ad_swis_why = kw.get("swis_why", "")
    return a


class Answer1012(unittest.TestCase):
    """/api/ad: the database's runs with their times, SolarWinds for the newest run, the full report's link; every state said."""

    def test_1_model_and_solarwinds_for_its_run(self):
        a = live(fill(["ok"] * 5 + ["critical"] * 2), Site(self))
        a.read_db()
        src = a.sw_source()
        self.assertEqual(src[0], "9006")                              # the database's newest run
        a.sw = a.read_sw(src)
        out = json.loads(a.answer()[1])
        m = out["db"]["model"]
        self.assertTrue(out["db"]["on"])
        self.assertEqual((m["run"]["job"], m["run"]["old"]), (9006, False))
        self.assertTrue(m["run"]["as_of"].endswith("UTC"))
        d3 = m["dcs"][0]
        self.assertEqual((d3["dc"], d3["runs_since"]), ("DC03", 2))
        self.assertTrue(d3["since_shown"].endswith("UTC"))
        self.assertNotIn("live", m)                                    # the DCs for SolarWinds stay on the server
        self.assertEqual(m["worst7"], {"dc": "DC03", "critical": 2})
        self.assertEqual(out["sw"]["job"], "9006")
        rows = dict((r[1], r) for r in out["sw"]["section"]["rows"])
        self.assertEqual(rows["DC02"][2], "Down")
        self.assertIn("read by the portal every 10 min", out["sw"]["section"]["text"])
        self.assertEqual(out["sw"]["tile"]["value"], 1)
        self.assertEqual(out["report"]["url"], "/portal/reports/ad-health/latest.html")

    def test_2_a_failed_read_said_the_last_model_kept_as_stale(self):
        lite = fill(["ok"] * 3)
        calls = []

        def connect():
            calls.append(1)
            if len(calls) > 1:
                raise RuntimeError("(2003, \"Can't connect to MySQL server\")")
            return lite
        a = sp.AdLive(Site(self).cfg(), sp.Log(None), db=sp.AdDb(None, connect=connect, mark="?"))
        a.read_db()
        with self.assertRaises(RuntimeError):
            a.read_db()
        a.db_stale = "Can't connect to MySQL server"                  # as the loop says it
        out = json.loads(a.answer()[1])["db"]
        self.assertEqual(out["stale"], "Can't connect to MySQL server")
        self.assertEqual(out["model"]["run"]["job"], 9002)            # the last one read, with its own time and age

    def test_3_no_login_said(self):
        out = json.loads(sp.AdLive(Site(self).cfg(), sp.Log(None)).answer()[1])["db"]
        self.assertFalse(out["on"])
        self.assertIn("report_reader", out["why"])
        self.assertNotIn("model", out)

    def test_4_old_is_said(self):
        lite = Lite()
        now, ad = ad_run(200)
        adb.write(lite, now, 9000, 12, dbf.ad_db_payload(ad, {"kind": "ad"}, {}))
        a = live(lite, Site(self), env={"PORTAL_AD_OLD_MINUTES": "90"})
        a.read_db()
        r = json.loads(a.answer()[1])["db"]["model"]["run"]
        self.assertTrue(r["old"])
        self.assertGreaterEqual(r["age_min"], 199)

    def test_5_etag_and_304_and_a_read_of_another_run_not_shown(self):
        lite = fill(["ok"] * 3)
        a = live(lite, Site(self))
        a.read_db()
        a.sw = a.read_sw(a.sw_source())
        code, _b, etag = a.answer()
        self.assertEqual(a.answer(etag), (304, "", etag))
        real = time.time
        try:                                                            # 5 min later, the same reads: the same tag (the ages are not in it)
            sp.time.time = lambda: real() + 300
            self.assertEqual(a.answer(etag)[0], 304)
        finally:
            sp.time.time = real
        now, ad = ad_run(0, "critical")
        adb.write(lite, now, 9100, 12, dbf.ad_db_payload(ad, {"kind": "ad", "dcs": [{"dc": "DC01"}]}, {}))
        a.read_db()
        out = json.loads(a.answer(etag)[1])
        self.assertEqual(out["db"]["model"]["run"]["job"], 9100)
        self.assertNotIn("section", out["sw"])                         # job 9002's SolarWinds: read again for 9100 first
        self.assertTrue(a.wake.is_set())

    def test_6_solarwinds_failure_and_off_said(self):
        a = live(fill(["ok"] * 2), Site(self))
        a.read_db()
        bad = sp.AdLive(a.cfg, sp.Log(None), source=lambda lv: {"ok": False, "why": ["the login was refused"]}, db=a.db)
        bad.read_db()
        with self.assertRaisesRegex(ValueError, "the login was refused"):
            bad.read_sw(bad.sw_source())
        bad.sw_stale = "the login was refused"
        self.assertEqual(json.loads(bad.answer()[1])["sw"]["stale"], "the login was refused")
        off = live(fill(["ok"] * 2), Site(self), swis_why="the portal has no SolarWinds login")
        out = json.loads(off.answer()[1])["sw"]
        self.assertEqual((out["on"], out["why"]), (False, "the portal has no SolarWinds login"))

    def test_7_two_forests_never_mixed(self):
        lite = fill(["critical"] * 3, template_id=12)
        now, ad = ad_run(0)
        adb.write(lite, now, 9500, 34, dbf.ad_db_payload(ad, {"kind": "ad"}, {}))
        m = sp.AdDb(None, connect=lambda: lite, mark="?").read()
        self.assertEqual((m["run"]["template_id"], len(m["times"])), (34, 1))     # the newest run's template only
        m = sp.AdDb(None, connect=lambda: lite, mark="?").read(template_id=12)
        self.assertEqual((m["run"]["template_id"], len(m["times"])), (12, 3))     # portal_ad_template


class Link1012(unittest.TestCase):
    """The full report's link: index.json's newest line marked live: ad, a page inside the folder, read again when it changed."""

    def test_1_the_newest_and_never_outside(self):
        s = Site(self)
        a = sp.AdLive(s.cfg(), sp.Log(None))
        self.assertEqual(a.report_link()["url"], "/portal/reports/ad-health/latest.html")
        s.index(extra=[{"latest": "ad-other/latest.html", "when": utc(0), "job": "9", "live": "ad"}])
        self.assertEqual(a.report_link()["url"], "/portal/reports/ad-other/latest.html")
        for bad in ("../secret.html", "/etc/x.html", "a/../../x.html", "ad\\\\x.html"):
            with open(os.path.join(s.root, "index.json"), "w") as fh:
                json.dump({"reports": [{"latest": bad, "when": utc(0), "job": "9", "live": "ad"}]}, fh)
            self.assertIsNone(sp.AdLive(s.cfg(), sp.Log(None)).report_link(), bad)

    def test_2_missing_or_half_written(self):
        s = Site(self, index=False)
        a = sp.AdLive(s.cfg(), sp.Log(None))
        self.assertIsNone(a.report_link())
        s.index()
        self.assertIsNotNone(a.report_link())
        with open(os.path.join(s.root, "index.json"), "w") as fh:
            fh.write('{"reports": [')
        self.assertEqual(a.report_link()["url"], "/portal/reports/ad-health/latest.html")    # the last one, read again next time


class Routes1012(unittest.TestCase):
    def serve(self, s, **env):
        cfg = s.cfg(**env)
        p = sp.Portal(cfg)
        p.aap.ready = ""
        srv = sp.PortalServer(("127.0.0.1", 0), sp.make_handler(cfg, p, os.path.join(FILES, "static")))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return "http://127.0.0.1:%d" % srv.server_address[1]

    def get(self, url, headers=None):
        h = {"X-SSL-Client-Verify": "SUCCESS", "X-SSL-Client-S-DN": JDOE}
        h.update(headers or {})
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=20) as f:
                return f.status, f.read().decode(), dict(f.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(), dict(e.headers)

    def setUp(self):
        sp.sys.stdout = open(os.devnull, "w")
        self.addCleanup(setattr, sp.sys, "stdout", sys.__stdout__)

    def test_1_the_page_and_its_answer(self):
        base = self.serve(Site(self))
        code, page, h = self.get(base + "/ad")
        self.assertEqual(code, 200)
        self.assertIn("script-src 'self';", h["Content-Security-Policy"])                    # the portal's own CSP: no inline script
        self.assertNotIn("<script>", page)
        self.assertNotIn(" style=", page)
        self.assertRegex(page, r'<div id="ad" data-src="/portal/api/ad">')
        self.assertRegex(page, r'<script src="/portal/static/ad-page.js\?v=[0-9a-f]{10}"></script>')
        self.assertRegex(page, r'<link rel="stylesheet" href="/portal/static/ad-page.css\?v=[0-9a-f]{10}">')
        self.assertIn('aria-current="page">Active Directory</a>', page)
        code, body, h = self.get(base + "/api/ad")
        a = json.loads(body)
        self.assertEqual((code, a["db"]["on"], a["report"]["url"]), (200, False, "/portal/reports/ad-health/latest.html"))
        self.assertEqual(self.get(base + "/api/ad", {"If-None-Match": h["ETag"]})[0], 304)
        code, js, h = self.get(base + "/static/ad-page.js")
        self.assertEqual((code, h["Content-Type"]), (200, "text/javascript; charset=utf-8"))

    def test_2_off(self):
        base = self.serve(Site(self), PORTAL_AD="0")
        self.assertEqual(self.get(base + "/ad")[0], 404)
        self.assertEqual(self.get(base + "/api/ad")[0], 404)
        self.assertNotIn(">Active Directory</a>", self.get(base + "/")[1])

    def test_3_a_signed_in_person_only(self):
        base = self.serve(Site(self))
        self.assertEqual(self.get(base + "/api/ad", {"X-SSL-Client-Verify": "NONE"})[0], 403)
        self.assertEqual(self.get(base + "/ad", {"X-SSL-Client-Verify": "NONE"})[0], 403)

    def test_4_the_login_from_its_secret(self):
        s = Site(self)
        cfg = s.cfg(PORTAL_REPORT_DB=json.dumps({"host": "db.example.mil", "port": 3309, "database": "site_reports", "user": "report_reader",
                                                 "password": "x", "tls": False}))
        self.assertEqual(cfg.report_db["user"], "report_reader")
        self.assertIsNotNone(sp.AdLive(cfg, sp.Log(None)).db)
        self.assertEqual(s.cfg(PORTAL_REPORT_DB="not json").report_db, {})
        self.assertEqual(s.cfg(PORTAL_REPORT_DB='{"host": ""}').report_db, {})

    def test_5_the_deploy_ships_the_files(self):
        with open(os.path.join(ROOT, "roles", "site_portal_stack", "tasks", "main.yml")) as fh:
            t = fh.read()
        for n in ("site_swis_db.py", "site_ad_swis.py", "ad_swis_filters.py", "site_report_db.py", "static/ad-page.js", "static/ad-page.css"):
            self.assertIn("dest: %s}" % n, t)
        for k in ("PORTAL_AD:", "PORTAL_AD_SECONDS:", "PORTAL_AD_OLD_MINUTES:", "PORTAL_AD_TEMPLATE:", "site-portal-reportdb"):
            self.assertIn(k, t)
        with open(os.path.join(ROOT, "roles", "site_portal_stack", "templates", "site-portal-app.container.j2")) as fh:
            self.assertIn("Secret=site-portal-reportdb,type=env,target=PORTAL_REPORT_DB", fh.read())
        # the portal's copy of the readers: site_time beside them, no plugins/ folder, no ansible
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        shutil.copy(os.path.join(ROOT, "plugins", "filter", "ad_swis_filters.py"), d)
        for n in ("site_time.py", "site_swis.py", "site_swis_db.py", "site_ad_swis.py", "site_report_db.py"):
            shutil.copy(os.path.join(ROOT, "plugins", "module_utils", n), d)
        out = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, %r); sys.modules['ansible'] = None; "
                              "import ad_swis_filters, site_ad_swis, site_report_db; print('ok')" % d],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(out.stdout.strip(), "ok", out.stdout)
        with open(os.path.join(FILES, "start.sh")) as fh:
            self.assertIn("for want in pymysql sys", fh.read())

    def test_6_the_catalog_attaches_the_reader(self):
        import yaml
        cat = yaml.safe_load(open(os.path.join(ROOT, "aap", "job_templates.yml")))["job_templates"]
        t = dict((e["name"], e) for e in cat)
        self.assertEqual(t["Site portal - deploy"]["optional_credentials"], ["report_db_reader", "report_db"])
        self.assertIn("report_db", t["Active Directory health report"]["optional_credentials"])


class Job1012(unittest.TestCase):
    def test_1_the_live_block(self):
        b = af.ad_live_block({"problems": [{"domain": "corp.test", "object": "DC01", "severity": "critical", "text": "x" * 400, "area": "LDAP"},
                                           {"text": "no object"}]},
                             [{"dc": "DC01", "ip": "192.0.2.11"}, {"dc": ""}], {"cpu_warn": "85", "days": "14"})
        self.assertEqual(b["kind"], "ad")
        self.assertEqual(b["dcs"], [{"dc": "DC01", "ip": "192.0.2.11"}])
        self.assertEqual(len(b["problems"]), 1)
        self.assertEqual(len(b["problems"][0]["text"]), 300)
        self.assertEqual(b["opts"], {"cpu_warn": 85.0, "mem_warn": 90.0, "days": 14})
        self.assertNotIn("area", b["problems"][0])

    def test_2_the_role(self):
        t = open(os.path.join(ROOT, "roles", "ad_report", "tasks", "db.yml")).read()
        for k in ("site_ad_db:", "ad_db_payload(", "ad_live_block(", "failed_when: false", "REPORT_DB_PASSWORD"):
            self.assertIn(k, t)
        self.assertIn("_report_live: ad", open(os.path.join(ROOT, "roles", "ad_report", "tasks", "report.yml")).read())
        self.assertIn("include_tasks: db.yml", open(os.path.join(ROOT, "roles", "ad_report", "tasks", "main.yml")).read())
        with open(os.path.join(ROOT, "roles", "site_email", "tasks", "main.yml")) as fh:
            self.assertIn("live: \"{{ _report_live | default('') }}\"", fh.read())


def _browser():
    for b in ("google-chrome", "chromium", "chromium-browser"):
        if shutil.which(b):
            return shutil.which(b)
    return None


@unittest.skipUnless(_browser(), "no google-chrome or chromium")
class Browser1012(unittest.TestCase):
    """ad-page.js in the real portal shell (atlas, brief, ops), the portal's answers stood in by a fetch stand-in."""

    def answer(self, states=None, down=("DC02",), sw_on=True, db=True, **kw):
        lite = fill(states or (["ok"] * 40 + ["critical"] * 8))
        a = live(lite if db else None, Site(self), down=down, swis_why="" if sw_on else "the portal has no SolarWinds login", env=kw.get("env", {}))
        if db:
            a.read_db()
            if kw.get("stale"):
                a.db_stale = kw["stale"]
            if sw_on:
                a.sw = a.read_sw(a.sw_source())
        return json.loads(a.answer()[1])

    def page(self, answers, js, look="atlas", width=1280, wait=1500, motion_off=False):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        cfg = Site(self).cfg(PORTAL_THEME=look, PORTAL_MOTION="off" if motion_off else "auto")
        html = sp.ad_page(cfg, "DOE.JANE.Q.1234567890", {}).replace("/portal/static/", "")
        for n in ("ad-page.js", "ad-page.css", "portal.css", "portal-theme.js"):
            shutil.copy(os.path.join(FILES, "static", n), d)
        with open(os.path.join(d, "banner.css"), "w") as fh:
            fh.write("")
        with open(os.path.join(d, "stub.js"), "w") as fh:
            fh.write("(function(){var A=%s,i=0;window.fetch=function(){var a=A[Math.min(i++,A.length-1)];return Promise.resolve({status:200,ok:true,"
                     "headers:{get:function(){return 'e'+i}},json:function(){return Promise.resolve(a)}})}})();" % json.dumps(answers))
        with open(os.path.join(d, "t.js"), "w") as fh:
            fh.write("setTimeout(function(){var r={};try{%s}catch(e){r.err=String(e)}document.getElementById('res').textContent=JSON.stringify(r)},%d);"
                     % (js, wait))
        html = html.replace('<script src="portal.js?v=" defer></script>', "").replace('<script src="portal.js" defer></script>', "")
        html = html.replace("<head>", '<head><script src="stub.js"></script>', 1)
        html = html.replace("</body>", '<pre id="res"></pre><script src="t.js"></script></body>', 1)
        with open(os.path.join(d, "p.html"), "w") as fh:
            fh.write(html)
        out = subprocess.run([_browser(), "--headless=new", "--no-sandbox", "--disable-gpu", "--window-size=%d,900" % width, "--virtual-time-budget=90000",
                              "--dump-dom", "file://" + os.path.join(d, "p.html")], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             universal_newlines=True, timeout=180).stdout
        m = re.search(r'<pre id="res">(.*?)</pre>', out, re.S)
        self.assertIsNotNone(m, out[-1500:])
        return json.loads(m.group(1).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))

    READ = ("var q=function(s){return [].map.call(document.querySelectorAll(s),function(x){return x.textContent.trim()})};"
            "r.h2=q('#ad section.sec > h2');r.tiles=q('.adp-tiles .tile .k');r.tv=q('.adp-tiles .tile .v');r.notes=q('#ad .note');"
            "r.live=q('.adp-live .l').join('');r.link=(document.querySelector('.adp-live a')||{}).href||'';"
            "r.dcs=[].map.call(document.querySelectorAll('.adp-dcs tbody tr'),function(tr){return [tr.dataset.dc,tr.className,tr.cells[2].textContent,"
            "tr.cells[3].textContent,tr.querySelectorAll('.adp-trend i').length,tr.querySelectorAll('.adp-trend i.k-critical').length,tr.cells[5].textContent]});"
            "r.links=q('#ad section:nth-of-type(3) tbody tr');r.bars=document.querySelectorAll('.adp-chart rect.bc').length;"
            "r.runs=document.querySelectorAll('#ad section:nth-of-type(4) tbody tr').length;"
            "r.toasts=q('.adp-toast b');r.flash=document.querySelectorAll('.adp-flash').length;"
            "r.side=document.documentElement.scrollWidth-document.documentElement.clientWidth;")

    def test_1_every_part_in_every_look(self):
        a = self.answer()
        for look in ("atlas", "brief", "ops"):
            for w in (1280, 800):
                r = self.page([a], self.READ, look, w)
                self.assertNotIn("err", r, (look, w))
                self.assertEqual(r["h2"][:4], ["At a glance", "Domain controllers3worst first · a square is one run, oldest left",
                                               "Replication links that need a look1as of " + a["db"]["model"]["run"]["as_of"][11:],
                                               "DCs with a problem, last 7 daysthe worst run of each 2 hours; the numbers: the runs below"], (look, w))
                self.assertEqual(len(r["tiles"]), 8)
                self.assertEqual(r["dcs"][0][:6], ["DC03", "r-critical", "Critical", r["dcs"][0][3], 48, 8], (look, w))
                self.assertRegex(r["dcs"][0][3], r"^since \d\d:\d\d UTC · 8 runs$")
                self.assertEqual([x[0] for x in r["dcs"]], ["DC03", "DC02", "DC01"])   # AD's worst, then SolarWinds down
                self.assertEqual(r["dcs"][1][6], "Down")
                self.assertIn("Live", r["live"])
                self.assertIn("SolarWinds: updated just now", r["live"])
                self.assertTrue(r["link"].endswith("/portal/reports/ad-health/latest.html"))
                self.assertEqual(r["notes"], [])
                self.assertGreaterEqual(r["bars"], 1)
                self.assertEqual(r["runs"], 8)
                self.assertEqual(r["side"], 0, (look, w))                    # no sideways scroll

    def test_2_each_state_said(self):
        r = self.page([self.answer(db=False)], self.READ)
        self.assertIn("The report database is off here", r["notes"][0])
        self.assertEqual(r["h2"], [])
        r = self.page([self.answer(stale="Can't connect to MySQL server")], self.READ)
        self.assertIn("The report database was not read just now (Can't connect to MySQL server)", r["notes"][0])
        self.assertIn("Not live", r["live"])
        r = self.page([self.answer(sw_on=False)], self.READ)
        self.assertIn("SolarWinds: not read (the portal has no SolarWinds login)", r["live"])
        self.assertEqual(r["dcs"][0][6], "not read")
        lite = Lite()
        a = live(lite, Site(self))
        a.read_db()
        r = self.page([json.loads(a.answer()[1])], self.READ)
        self.assertIn("No AD run in the report database yet", r["notes"][0])
        old = Lite()
        now, ad = ad_run(300)
        adb.write(old, now, 9000, 12, dbf.ad_db_payload(ad, {"kind": "ad"}, {}))
        a = live(old, Site(self))
        a.read_db()
        r = self.page([json.loads(a.answer()[1])], self.READ)
        self.assertTrue(any("old (more than 90 min): the AD job has not run since" in n for n in r["notes"]), r["notes"])

    def test_3_a_dc_that_changes_is_said(self):
        one = self.answer(["ok"] * 10)
        two = self.answer(["ok"] * 9 + ["critical"], down=("DC02", "DC03"))
        r = self.page([one, two], self.READ, wait=68000)                   # the second answer at 60 s, its toasts for 15 s
        self.assertEqual(sorted(r["toasts"]), ["DC03: AD Critical", "DC03: SolarWinds Down"])
        self.assertEqual(r["flash"], 1)
        self.assertTrue(r["dcs"][0][3].endswith(" · 1 run"), r["dcs"][0][3])
        r = self.page([one, two], self.READ, wait=68000, motion_off=True)
        self.assertEqual((len(r["toasts"]), r["flash"]), (2, 0))              # portal_motion off: said, no flash

if __name__ == "__main__":
    unittest.main(verbosity=2)
