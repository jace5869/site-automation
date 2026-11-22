#!/usr/bin/env python3
"""1.101.2: the live AD page (/portal/ad) - roles/site_portal_stack/files/site_portal.py AdLive and ad_page (the AD job's
newest page from the report website: its index line marked live: ad, its page and JSON of one job; each missing,
unreadable or half-written file said, the last whole run kept with its own time), the routes, the AD job's live block
(plugins/filter/ad_swis_filters.py ad_live_block) and roles/ad_report/files/web/ad-live.js in Chromium (the strip, the
From SolarWinds cells and tile written again, a DC that changes said, a newer run, no motion). Placeholders only.
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
WEB = os.path.join(ROOT, "roles", "ad_report", "files", "web")
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
LIVE = {"v": 1, "kind": "ad", "dcs": [{"dc": "DC01", "ip": ""}, {"dc": "DC02", "ip": ""}, {"dc": "DC03", "ip": "192.0.2.13"},
                                       {"dc": "DC04", "ip": ""}],
        "problems": [{"domain": "corp.test", "object": "DC01", "severity": "critical", "text": "LDAP not in sync"}],
        "opts": {"cpu_warn": 90, "mem_warn": 90, "days": 30}}


def utc(minutes_ago):
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(time.time() - minutes_ago * 60))


def sw_answer(down=("DC02",)):
    """site_ad_swis.from_live's shape: DC01-DC04 as SolarWinds sees them."""
    def d(name):
        return {"dc": name, "ip": "", "matched": "name", "status": "Down" if name in down else "Up", "status_text": "",
                "cpu": None if name in down else 12, "mem": None if name in down else 40, "alerts": [], "apps": {}, "last_down": None}
    return {"ok": True, "source": "swis-live", "read_at": time.strftime("%Y-%m-%d %H:%M", time.gmtime()), "age_min": 0, "why": [],
            "sam": False, "sam_why": "SAM's applications are not on this Orion", "events": False,
            "dcs": [d("DC01"), d("DC02"), d("DC03"), d("DC04")]}


def ad_page_html(look="report_atlas"):
    """The AD page in a look, with From SolarWinds (tests/test_ad_swis.py's snapshot read and fixture)."""
    import test_ad_swis as T
    import jinja2
    import site_filters as sf
    r = T.ad.read(T.DCS, {"now": T.NOW}, conn=T.Conn(["2026-10-15 06:43", "2026-10-15 06:33"]), swdb=T.Snap())
    r["asked"] = True
    doc = af.ad_swis_insert(T.ad_doc(), af.ad_swis_view(r, {"problems": []}, {"now": T.NOW, "days": 30}))
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(ROOT, "roles", "site_email", "templates")), autoescape=False)
    env.filters.update(sf.FilterModule().filters())
    return env.get_template(look + ".html.j2").render(_report=doc, _email_footer="x", _page_logo_cid="", report_email_header_text="x", _page_runs=[])


class Site(object):
    """A report folder as site_report_web writes it: index.json, the AD template's latest page and its JSON."""

    def __init__(self, test, when=None, job="77", live=True, html="<!doctype html><html><head><title>AD</title></head><body><h1>AD</h1>"
                 '<script src="site-report.js"></script><a href="../index.html">All reports</a><a href="#s5">x</a></body></html>'):
        self.tmp = tempfile.mkdtemp()
        test.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "reports")
        os.makedirs(os.path.join(self.root, "ad-health"))
        os.makedirs(os.path.join(self.tmp, "data"))
        with open(os.path.join(self.tmp, "a.json"), "w") as fh:
            json.dump({"actions": {}}, fh)
        self.when = when or utc(20)
        self.page(html)
        self.data(job, live)
        self.index(job)

    def page(self, html):
        with open(os.path.join(self.root, "ad-health", "latest.html"), "w") as fh:
            fh.write(html)

    def data(self, job, live=True):
        rep = {"title": "Active Directory health report"}
        if live:
            rep["live"] = LIVE
        with open(os.path.join(self.root, "ad-health", "latest.json"), "w") as fh:
            json.dump({"job": job, "template_id": "12", "report": rep}, fh)

    def index(self, job, when=None, live="ad", extra=()):
        lines = [{"key": "ad-health/latest.html", "latest": "ad-health/latest.html", "page": "ad-health/2026-11-22_0300_job-%s.html" % job,
                  "when": when or self.when, "job": job, "live": live}] + list(extra)
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

    def test_5_payload_never_raises(self):
        self.assertIn("were not made into rows", dbf.ad_db_payload({"dcs": "not a list"}, {}, {})["why"])
        p = dbf.ad_db_payload(ad_run(0, "warning")[1], {}, {"crit_hours": 24})
        self.assertEqual([(d["dc"], d["status"]) for d in p["dcs"]], [("DC01", "ok"), ("DC02", "ok"), ("DC03", "warning")])
        self.assertEqual([x["status"] for x in p["links"]], ["critical", "ok"])
        p = dbf.ad_db_payload({"dcs": [{"dc": "DC09", "domain": "corp.test", "read": False, "error": "WinRM did not answer"}]})
        self.assertEqual((p["dcs"][0]["status"], p["dcs"][0]["worst"]), ("critical", "not read: WinRM did not answer"))


class DbAnswer1012(unittest.TestCase):
    """/api/ad's database part: the model with its times, or why not; a read that fails keeps the last one said as stale."""

    def test_1_model_in_the_answer_and_solarwinds_for_its_run(self):
        s = Site(self)
        lite = fill(["ok"] * 5 + ["critical"] * 2)
        a = sp.AdLive(s.cfg(), sp.Log(None), source=lambda live: sw_answer(), db=sp.AdDb(None, connect=lambda: lite, mark="?"))
        a.cfg.ad_swis_why = ""
        a.read_db()
        src = a.sw_source(a.run()[0])
        self.assertEqual(src[0], "9006")                              # the database's newest run, not the page's job 77
        a.sw = a.read_sw(src)
        out = json.loads(a.answer()[1])
        m = out["db"]["model"]
        self.assertTrue(out["db"]["on"])
        self.assertEqual((m["run"]["job"], m["run"]["old"]), (9006, False))
        self.assertTrue(m["run"]["as_of"].endswith("UTC"))
        d3 = m["dcs"][0]
        self.assertEqual((d3["dc"], d3["runs_since"]), ("DC03", 2))
        self.assertTrue(d3["since_shown"].endswith("UTC"))
        self.assertNotIn("live", m)
        self.assertEqual(out["sw"]["job"], "9006")
        self.assertIn("section", out["sw"])

    def test_2_a_failed_read_said_the_last_model_kept_as_stale(self):
        s = Site(self)
        lite = fill(["ok"] * 3)
        calls = []

        def connect():
            calls.append(1)
            if len(calls) > 1:
                raise RuntimeError("(2003, \"Can't connect to MySQL server\")")
            return lite
        a = sp.AdLive(s.cfg(), sp.Log(None), db=sp.AdDb(None, connect=connect, mark="?"))
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


class Run1012(unittest.TestCase):
    """The newest AD run: found by its index line, its page and JSON of one job; every other state said, never old as new."""

    def test_1_a_whole_run(self):
        s = Site(self)
        run, why = sp.AdLive(s.cfg(), sp.Log(None)).run()
        self.assertEqual(why, "")
        self.assertEqual((run["rel"], run["line"]["job"], run["live"]["kind"]), ("ad-health/latest.html", "77", "ad"))

    def test_2_each_missing_part_said(self):
        s = Site(self)
        os.unlink(os.path.join(s.root, "index.json"))
        self.assertIn("no index.json yet", sp.AdLive(s.cfg(), sp.Log(None)).run()[1])
        s.index("77", live="")
        self.assertIn("No AD health report on the report website has its live part yet", sp.AdLive(s.cfg(), sp.Log(None)).run()[1])
        s.index("77")
        os.unlink(os.path.join(s.root, "ad-health", "latest.json"))
        self.assertIn("report_web_json true", sp.AdLive(s.cfg(), sp.Log(None)).run()[1])
        s.data("77", live=False)
        self.assertIn("made before 1.101.2", sp.AdLive(s.cfg(), sp.Log(None)).run()[1])
        os.unlink(os.path.join(s.root, "ad-health", "latest.html"))
        self.assertIn("is not in the report folder", sp.AdLive(s.cfg(), sp.Log(None)).run()[1])

    def test_3_half_written_keeps_the_last_whole_run_said(self):
        s = Site(self)
        a = sp.AdLive(s.cfg(), sp.Log(None))
        self.assertEqual(a.run()[0]["line"]["job"], "77")
        s.index("78", when=utc(1))                          # the index is the new job's, its JSON still the old one's
        run, why = a.run()
        self.assertEqual(run["line"]["job"], "77")          # the last whole run, with its own time
        self.assertIn("being written", why)
        with open(os.path.join(s.root, "index.json"), "w") as fh:
            fh.write('{"reports": [')                       # cut short
        run, why = a.run()
        self.assertEqual(run["line"]["job"], "77")
        self.assertIn("index.json could not be read", why)
        s.data("78")
        s.index("78", when=utc(1))
        run, why = a.run()
        self.assertEqual((run["line"]["job"], why), ("78", ""))

    def test_4_never_outside_the_folder(self):
        s = Site(self)
        for bad in ("../secret.html", "/etc/passwd.html", "ad-health\\latest.html"):
            with open(os.path.join(s.root, "index.json"), "w") as fh:
                json.dump({"reports": [{"latest": bad, "when": s.when, "job": "77", "live": "ad"}]}, fh)
            self.assertIn("names no page inside the report folder", sp.AdLive(s.cfg(), sp.Log(None)).run()[1], bad)

    def test_5_the_newest_of_two(self):
        s = Site(self)
        other = {"key": "ad-old/latest.html", "latest": "ad-old/latest.html", "when": utc(600), "job": "5", "live": "ad"}
        s.index("77", extra=[other])
        self.assertEqual(sp.AdLive(s.cfg(), sp.Log(None)).run()[0]["rel"], "ad-health/latest.html")


class Answer1012(unittest.TestCase):
    def test_1_the_run_and_solarwinds_off(self):
        s = Site(self)
        code, body, etag = sp.AdLive(s.cfg(), sp.Log(None)).answer()
        a = json.loads(body)
        self.assertEqual(code, 200)
        self.assertTrue(a["ok"])
        self.assertFalse(a["run"]["old"])
        self.assertIn(a["run"]["age_min"], (19, 20, 21))
        self.assertFalse(a["sw"]["on"])
        self.assertIn("no SolarWinds login", a["sw"]["why"])
        self.assertNotIn("section", a["sw"])

    def test_2_old_is_said(self):
        s = Site(self, when=utc(200))
        a = json.loads(sp.AdLive(s.cfg(PORTAL_AD_OLD_MINUTES="90"), sp.Log(None)).answer()[1])
        self.assertTrue(a["run"]["old"])
        self.assertEqual(a["run"]["old_after"], 90)

    def test_3_solarwinds_read_the_section_and_tile(self):
        s = Site(self)
        a = sp.AdLive(s.cfg(), sp.Log(None), source=lambda live: sw_answer())
        a.cfg.ad_swis_why = ""
        a.sw = a.read_sw(a.run()[0])
        out = json.loads(a.answer()[1])["sw"]
        sec = out["section"]
        rows = dict((r[1], r) for r in sec["rows"])
        self.assertEqual(rows["DC02"][2], "Down")
        self.assertEqual(out["tile"], {"label": "SolarWinds: down or warning", "status": "critical", "value": 1})
        self.assertIn("read by the portal every 10 min while this page is open", sec["text"])
        self.assertNotIn("details", sec)                      # the rows' details stay as of the run
        self.assertEqual(rows["DC01"][-1], "-")
        self.assertIn("LDAP not in sync", [r for r in sec["rows"] if r[1] == "DC01"][0][-1] + "LDAP not in sync")

    def test_4_a_read_of_another_run_is_not_shown(self):
        s = Site(self)
        a = sp.AdLive(s.cfg(), sp.Log(None), source=lambda live: sw_answer())
        a.cfg.ad_swis_why = ""
        a.sw = a.read_sw(a.run()[0])
        s.data("78")
        s.index("78", when=utc(1))
        out = json.loads(a.answer()[1])
        self.assertEqual(out["run"]["job"], "78")
        self.assertNotIn("section", out["sw"])                # the DCs of job 77: read again for 78 first
        self.assertTrue(a.wake.is_set())

    def test_5_etag_and_304(self):
        s = Site(self)
        a = sp.AdLive(s.cfg(), sp.Log(None))
        code, _body, etag = a.answer()
        self.assertEqual(a.answer(etag), (304, "", etag))
        s.index("77", when=utc(400))                          # old now: another answer
        self.assertEqual(a.answer(etag)[0], 200)

    def test_6_solarwinds_failure_said(self):
        s = Site(self)

        def boom(live):
            raise RuntimeError("SolarWinds did not answer")
        a = sp.AdLive(s.cfg(), sp.Log(None), source=boom)
        a.cfg.ad_swis_why = ""
        with self.assertRaises(RuntimeError):
            a.read_sw(a.run()[0])
        a.sw_stale = "SolarWinds did not answer"
        out = json.loads(a.answer()[1])["sw"]
        self.assertEqual(out["stale"], "SolarWinds did not answer")
        bad = sp.AdLive(s.cfg(), sp.Log(None), source=lambda live: {"ok": False, "why": ["the login was refused"]})
        bad.cfg.ad_swis_why = ""
        with self.assertRaisesRegex(ValueError, "the login was refused"):
            bad.read_sw(bad.run()[0])


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

    def test_1_the_page(self):
        s = Site(self)
        base = self.serve(s)
        code, html, h = self.get(base + "/ad")
        self.assertEqual(code, 200)
        self.assertIn("'unsafe-inline'", h["Content-Security-Policy"])                     # the report pages' own CSP
        self.assertIn('<script src="/portal/reports/ad-health/site-report.js">', html)      # relative links: the report folder's
        self.assertIn('href="/portal/reports/ad-health/../index.html"', html)
        self.assertIn('href="#s5"', html)                                                   # the page's own places stay
        self.assertRegex(html, r'<meta name="ad-live" content="/portal/api/ad" data-portal="/portal/" data-tag="[0-9a-f]{12}">')
        self.assertRegex(html, r'<script src="/portal/static/ad-live.js\?v=[0-9a-f]*"></script></body>')
        code, body, h = self.get(base + "/api/ad")
        self.assertEqual((code, json.loads(body)["run"]["job"]), (200, "77"))
        self.assertEqual(self.get(base + "/api/ad", {"If-None-Match": h["ETag"]})[0], 304)

    def test_2_no_run_said_in_the_portal(self):
        s = Site(self)
        os.unlink(os.path.join(s.root, "index.json"))
        code, html, _h = self.get(self.serve(s) + "/ad")
        self.assertEqual(code, 200)
        self.assertIn("no index.json yet", html)
        self.assertIn('<a href="/portal/ad">Active Directory</a>', html)

    def test_3_off(self):
        s = Site(self)
        base = self.serve(s, PORTAL_AD="0")
        self.assertEqual(self.get(base + "/ad")[0], 404)
        self.assertEqual(self.get(base + "/api/ad")[0], 404)
        self.assertNotIn(">Active Directory</a>", self.get(base + "/")[1])

    def test_4_a_signed_in_person_only(self):
        s = Site(self)
        base = self.serve(s)
        self.assertEqual(self.get(base + "/api/ad", {"X-SSL-Client-Verify": "NONE"})[0], 403)

    def test_5_the_deploy_ships_the_files(self):
        with open(os.path.join(ROOT, "roles", "site_portal_stack", "tasks", "main.yml")) as fh:
            t = fh.read()
        for n in ("site_swis_db.py", "site_ad_swis.py", "ad_swis_filters.py", "static/ad-live.js", "static/ad-live.css"):
            self.assertIn("dest: %s}" % n, t)
        for k in ("PORTAL_AD:", "PORTAL_AD_SECONDS:", "PORTAL_AD_OLD_MINUTES:"):
            self.assertIn(k, t)
        # the portal's copy of the section: its site_time beside it, no plugins/ folder
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        for n in ("ad_swis_filters.py",):
            shutil.copy(os.path.join(ROOT, "plugins", "filter", n), d)
        for n in ("site_time.py", "site_swis.py", "site_swis_db.py", "site_ad_swis.py"):
            shutil.copy(os.path.join(ROOT, "plugins", "module_utils", n), d)
        out = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, %r); import ad_swis_filters, site_ad_swis; print('ok')" % d],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(out.stdout.strip(), "ok", out.stdout)


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

    def test_2_the_role_marks_it(self):
        with open(os.path.join(ROOT, "roles", "ad_report", "tasks", "report.yml")) as fh:
            t = fh.read()
        self.assertIn("_report_live: ad", t)
        self.assertIn("ad_live_block(", t)
        with open(os.path.join(ROOT, "roles", "site_email", "tasks", "main.yml")) as fh:
            self.assertIn("live: \"{{ _report_live | default('') if _web_report.live is defined else '' }}\"", fh.read())


def _browser():
    for b in ("google-chrome", "chromium", "chromium-browser"):
        if shutil.which(b):
            return shutil.which(b)
    return None


@unittest.skipUnless(_browser(), "no google-chrome or chromium")
class Browser1012(unittest.TestCase):
    """ad-live.js on the real AD page (atlas, brief, ops), the portal's answers stood in by a fetch stand-in."""

    def answer(self, down=("DC02",), tag="t1", old=False, sw_on=True):
        s = Site(self)
        a = sp.AdLive(s.cfg(), sp.Log(None), source=lambda live: sw_answer(down))
        a.cfg.ad_swis_why = ""
        a.sw = a.read_sw(a.run()[0])
        out = json.loads(a.answer()[1])
        out["run"]["tag"], out["run"]["old"] = tag, old
        if old:
            out["run"]["age_min"] = 200
        if not sw_on:
            out["sw"] = {"on": False, "why": "the portal has no SolarWinds login", "stale": "", "every": 600}
        return out

    def page(self, answers, js, look="report_atlas", motion=True, hidden=False):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        html = ad_page_html(look)
        stub = ("(function(){var A=%s,i=0;window.__reloads=0;%s"
                "window.fetch=function(){var a=A[Math.min(i++,A.length-1)];return Promise.resolve({status:200,ok:true,"
                "headers:{get:function(){return 'e'+i}},json:function(){return Promise.resolve(a)}})}})();"
                % (json.dumps(answers), "Object.defineProperty(document,'hidden',{get:function(){return true}});" if hidden else ""))
        with open(os.path.join(d, "stub.js"), "w") as fh:
            fh.write(stub)
        with open(os.path.join(d, "t.js"), "w") as fh:
            fh.write("setTimeout(function(){var r={};try{%s}catch(e){r.err=String(e)}document.getElementById('res').textContent=JSON.stringify(r)},%d);"
                     % (js, 40000 if len(answers) > 1 else 1500))
        shutil.copy(os.path.join(WEB, "ad-live.js"), d)
        head = '<meta name="ad-live" content="/portal/api/ad" data-portal="/portal/" data-tag="t1"><script src="stub.js"></script>'
        html = html.replace("</head>", head + "</head>", 1)
        if not motion:
            html = html.replace("<html", '<html class="motion-off"', 1)
        html = html.replace("</body>", '<pre id="res"></pre><script src="ad-live.js"></script><script src="t.js"></script></body>', 1)
        with open(os.path.join(d, "p.html"), "w") as fh:
            fh.write(html)
        out = subprocess.run([_browser(), "--headless=new", "--no-sandbox", "--disable-gpu", "--window-size=1280,900", "--virtual-time-budget=60000",
                              "--dump-dom", "file://" + os.path.join(d, "p.html")], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             universal_newlines=True, timeout=180).stdout
        m = re.search(r'<pre id="res">(.*?)</pre>', out, re.S)
        self.assertIsNotNone(m, out[-2000:])
        return json.loads(m.group(1).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))

    CELLS = ("var s=adLive.section(),rows=[].slice.call(s.querySelectorAll('table.xt > tbody > tr:not(.xr)'));"
             "r.cells={};rows.forEach(function(tr){var dc=tr.querySelector('td[data-l=\"DC\"]').textContent.trim();"
             "var c=tr.querySelector('td[data-l=\"SolarWinds\"]');r.cells[dc]=[c.textContent.trim(),(c.querySelector('.chip')||{className:''}).className,"
             "tr.className,tr.querySelector('td[data-l=\"Last down (30 days)\"]').textContent.trim()]});"
             "var t=[].filter.call(document.querySelectorAll('.tile .tcopy'),function(b){return b.getAttribute('aria-label')==='Copy SolarWinds: down or warning'})[0].closest('.tile');"
             "r.tile=[t.querySelector('.v').textContent,t.querySelector('.v').className];"
             "r.strip=document.querySelector('.adl-strip').textContent;r.badge=(s.querySelector('h2 .adl-badge')||{}).textContent;"
             "r.toasts=[].map.call(document.querySelectorAll('.adl-toast .t'),function(x){return x.textContent});"
             "r.flash=document.querySelectorAll('.adl-flash').length;r.newer=!document.querySelector('.adl-newer').hidden;"
             "r.note=document.querySelector('.adl-note').hidden?'':document.querySelector('.adl-note').textContent;"
             "r.stripAfterH1=document.querySelector('h1').parentNode.contains(document.querySelector('.adl-strip'));"
             "r.text=s.querySelector('.body > p').textContent;")

    def test_1_the_cells_and_the_tile_in_every_look(self):
        for look in ("report_atlas", "report_brief", "report_ops"):
            r = self.page([self.answer()], self.CELLS, look)
            self.assertNotIn("err", r, look)
            self.assertEqual(r["cells"]["DC02"][:3], ["Down", "chip s-critical", "r-critical"], (look, r["cells"]))
            self.assertEqual(r["cells"]["DC01"][:2], ["Up", "chip s-ok"], look)
            self.assertEqual(r["cells"]["DC01"][3], "now, for 38 min", look)               # Last down: the run's (not read live)
            self.assertEqual(r["tile"][0], "1", look)
            self.assertIn("c-critical", r["tile"][1], look)
            self.assertIn("Live on the portal", r["strip"])
            self.assertIn("SolarWinds parts: updated just now (read every 10 min)", r["strip"])
            self.assertIn("AD checks: as of ", r["strip"])
            self.assertEqual(r["badge"], "updated just now", look)
            self.assertTrue(r["stripAfterH1"], look)
            self.assertIn("read by the portal every 10 min", r["text"], look)
            self.assertEqual((r["toasts"], r["flash"], r["newer"]), ([], 0, False), look)  # the first answer: nothing said

    def test_2_a_dc_that_changes_is_said(self):
        r = self.page([self.answer(), self.answer(down=("DC02", "DC03"))], self.CELLS)
        self.assertNotIn("err", r)
        self.assertEqual(r["cells"]["DC03"][0], "Down")
        self.assertEqual(r["toasts"], ["DC03: SolarWinds: Down"])
        self.assertEqual(r["flash"], 1)
        r = self.page([self.answer(), self.answer(down=("DC02", "DC03"))], self.CELLS, motion=False)
        self.assertEqual((r["toasts"], r["flash"]), (["DC03: SolarWinds: Down"], 0))      # report_motion off: said, no flash

    def test_3_old_newer_and_solarwinds_off(self):
        r = self.page([self.answer(old=True)], self.CELLS)
        self.assertIn("200 min", r["strip"].replace("3.3 h", "200 min"))
        self.assertIn("old: the AD job has not run since", r["strip"])
        r = self.page([self.answer(tag="t2")], self.CELLS)
        self.assertTrue(r["newer"])                           # a newer run: "Show it"
        r = self.page([self.answer(sw_on=False)], self.CELLS)
        self.assertIn("SolarWinds parts: as of the AD run (the portal has no SolarWinds login)", r["strip"])
        self.assertEqual(r["cells"]["DC01"][0], "Down")       # the run's own cells stay
        self.assertEqual(r["badge"], "as of the AD run")


if __name__ == "__main__":
    unittest.main(verbosity=2)
