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
import types
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


def sw_answer(down=("dc02",), domain="corp.test"):
    """site_ad_swis.from_live's shape: dc01-dc03 as SolarWinds sees them (each with its domain, as the reader keeps it)."""
    def d(name):
        return {"dc": name, "domain": domain, "ip": "", "matched": "name", "status": "Down" if name in down else "Up", "status_text": "",
                "cpu": None if name in down else 12, "mem": None if name in down else 40, "alerts": [], "apps": {}, "last_down": None, "node_id": int(name[-2:])}
    return {"ok": True, "source": "swis-live", "read_at": time.strftime("%Y-%m-%d %H:%M", time.gmtime()), "age_min": 0, "why": [],
            "sam": False, "sam_why": "SAM's applications are not on this Orion", "events": False,
            "dcs": [d("dc01"), d("dc02"), d("dc03")]}


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


# SQLite uses the production column definitions; only the MySQL table/index syntax differs.
def sqlite_schema(sql):
    sql = sql.replace("CREATE TABLE IF NOT EXISTS", "CREATE TABLE")
    sql = re.sub(r"run_id BIGINT NOT NULL AUTO_INCREMENT", "run_id INTEGER", sql)
    sql = re.sub(r", KEY \w+ \([^)]*\)", "", sql)
    return sql.split(" ENGINE=")[0]


SQLITE = tuple(sqlite_schema(c) for c in adb.CREATE)


class Lite(object):
    """A SQLite session that takes the MySQL statements of site_ad_db (%s marks; ON DUPLICATE KEY dropped) - the tests' report database."""

    def __init__(self):
        import sqlite3
        self.db = sqlite3.connect(":memory:", detect_types=0, check_same_thread=False)
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
    """_ad as ad_filters makes it: dc01-dc03, dc03's state as given, one link failing."""
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, second=0, microsecond=0) - dt.timedelta(minutes=minutes_ago)
    iso = lambda h: (now - dt.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")      # noqa: E731
    probs = [{"domain": "corp.test", "object": "dc03", "severity": dc3, "text": "dcdiag: Replications failed (8524)"}] if dc3 != "ok" else []
    return now, {"report": {"status": "critical" if dc3 == "critical" else "ok"}, "critical": 1 if dc3 == "critical" else 0, "warning": 0,
                 "problems": probs,
                 "dcs": [{"dc": "dc01", "domain": "corp.test", "read": True}, {"dc": "dc02", "domain": "corp.test", "read": True},
                         {"dc": "dc03", "domain": "corp.test", "read": True}],
                 "repl": {"now": iso(0), "dcs": [{"name": "dc01", "site": "SITEA0101", "read": True}, {"name": "dc02", "site": "SITEA0101", "read": True}, {"name": "dc03", "site": "SITEB0201", "read": True}],
                          "links": [{"frm": "dc01", "to": "dc03", "nc": "corp.test", "last_success": iso(31 if fail_link else 0.2), "failures": 3 if fail_link else 0,
                                     "error": "8524" if fail_link else "", "last_failure": iso(0.1)},
                                    {"frm": "dc02", "to": "dc01", "nc": "Configuration", "last_success": iso(0.1), "failures": 0}]}}


def fill(states, template_id=12, every=30, db=None):
    db = db or Lite()
    for k, st in enumerate(states):                   # oldest first, every 30 min (every)
        now, ad = ad_run(every * (len(states) - 1 - k), st)
        adb.write(db, now, 9000 + k, template_id, dbf.ad_db_payload(ad, {"kind": "ad", "dcs": [{"dc": "dc01"}]}, {"solarwinds": sw_answer()}))
    db.commit()
    return db


class Db1012(unittest.TestCase):
    """The AD job's run written (site_ad_db, ad_db_filters) and read back by the portal (AdDb): now, since when, the trend, the week."""

    def test_1_round_trip(self):
        db = fill(["ok"] * 10 + ["critical"] * 4)
        m = sp.AdDb(None, connect=lambda: db, mark="?").read()
        self.assertEqual((m["run"]["job"], m["run"]["status"], m["run"]["dcs"], m["run"]["dcs_read"]), (9013, "critical", 3, 3))
        self.assertEqual([d["dc"] for d in m["dcs"]], ["dc03", "dc01", "dc02"])           # worst first
        d3 = m["dcs"][0]
        self.assertEqual((d3["now"], d3["runs_since"], d3["site"]), ("critical", 4, "SITEB0201"))
        self.assertEqual(d3["since"], m["times"][-4])                                     # the run its state began
        self.assertEqual(d3["trend"], ["ok"] * 10 + ["critical"] * 4)
        self.assertIn("8524", d3["worst"])
        self.assertEqual([(x["frm"], x["to"], x["now"], x["failures"]) for x in m["links"]], [("dc01", "dc03", "critical", 3)])   # ok links left out
        self.assertEqual(len(m["hist"]), 14)
        self.assertEqual((m["hist"][-1]["c"], m["hist"][0]["c"]), (1, 0))
        self.assertEqual(m["live"]["kind"], "ad")

    def test_2_since_further_back_than_the_trend(self):
        db = fill(["critical"] * 3 + ["ok"] * 2 + ["critical"] * 50)
        d3 = [d for d in sp.AdDb(None, connect=lambda: db, mark="?").read()["dcs"] if d["dc"] == "dc03"][0]
        self.assertFalse(d3["longer"])                                                    # its whole length found: no "+"
        self.assertEqual(len(d3["trend"]), 48)
        self.assertEqual(d3["runs_since"], 50)
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
        n = adb.prune(db, dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(days=401), 400, 12)
        self.assertEqual(n, 3)
        self.assertEqual(db.db.execute("SELECT COUNT(*) FROM ad_dc").fetchone()[0], 0)
        ddl = adb.ddl()
        for t in ("CREATE TABLE IF NOT EXISTS ad_run", "CREATE TABLE IF NOT EXISTS ad_dc", "CREATE TABLE IF NOT EXISTS ad_link", "'ad_run', 4"):
            self.assertIn(t, ddl)

    def test_6_the_statements_in_the_doc_are_the_modules(self):
        doc = open(os.path.join(ROOT, "docs", "REPORT_DATABASE.md")).read()
        for part in adb.ddl().split("\n\n"):
            self.assertIn(part, doc, "docs/REPORT_DATABASE.md: the statement differs from site_ad_db.ddl()")

    def test_5_payload_never_raises(self):
        self.assertIn("could not make this run's DCs and links into rows", dbf.ad_db_payload({"dcs": "not a list"}, {}, {})["why"])
        p = dbf.ad_db_payload(ad_run(0, "warning")[1], {}, {"crit_hours": 24})
        self.assertEqual([(d["dc"], d["status"]) for d in p["dcs"]], [("dc01", "ok"), ("dc02", "ok"), ("dc03", "warning")])
        self.assertEqual([x["status"] for x in p["links"]], ["critical", "ok"])
        p = dbf.ad_db_payload({"dcs": [{"dc": "dc09", "domain": "corp.test", "read": False, "error": "WinRM did not answer"}]})
        self.assertEqual((p["dcs"][0]["status"], p["dcs"][0]["worst"]), ("critical", "not read: WinRM did not answer"))

    def test_7_unpolled_dc_is_visible_in_replication_coverage(self):
        now, ad = ad_run(0)
        ad["dcs"] = ad["dcs"][:1]
        ad["repl"]["dcs"] = [{"name": "dc01", "domain": "corp.test", "read": True},
                             {"name": "dc02", "domain": "corp.test", "read": False}]
        p = dbf.ad_db_payload(ad)
        self.assertEqual([(d["dc"], d["status"]) for d in p["dcs"]], [("dc01", "ok"), ("dc02", "unknown")])
        db = Lite()
        self.addCleanup(db.db.close)
        adb.write(db, now, 9000, 12, p)
        self.assertEqual(db.db.execute("SELECT dc FROM ad_dc ORDER BY dc").fetchall(), [("dc01",), ("dc02",)])
        m = sp.AdDb(None, connect=lambda: db).read()
        self.assertEqual((m["run"]["dcs"], m["run"]["repl_dcs"], m["run"]["repl_read"]), (2, 2, 1))


def live(lite, site, **kw):
    """An AdLive on the SQLite database, SolarWinds stood in (dc02 Down unless said)."""
    a = sp.AdLive(site.cfg(**kw.pop("env", {})), sp.Log(None), source=lambda lv: sw_answer(kw.get("down", ("dc02",))),
                  db=sp.AdDb(None, connect=lambda: lite, mark="?") if lite is not None else None)
    a.cfg.ad_swis_why = kw.get("swis_why", "")
    return a


class Bare(Lite):
    """1.112.3: the report database before the AD job's first run with the Report database credential - no AD table at all."""

    def __init__(self):  # pylint: disable=super-init-not-called
        import sqlite3
        self.db = sqlite3.connect(":memory:", detect_types=0, check_same_thread=False)


class MySQLError(Exception):
    """PyMySQL's errors carry (code, message)."""


class NoTables11123(unittest.TestCase):
    """1.112.3: no AD table yet (MySQL 1146) is the usual start, said calmly (db.missing), not as a fault; a connection or grant
    error stays a fault (db.stale)."""

    def failed(self, e, lite=None):
        a = live(lite or Lite(), Site(self))
        a.db_failed(e)
        return a, json.loads(a.answer()[1])["db"]

    def test_1_no_table_is_not_a_fault(self):
        a = live(Bare(), Site(self))
        with self.assertRaises(Exception) as cm:                      # the read itself: SQLite's "no such table"
            a.read_db()
        self.assertTrue(sp.missing_table(cm.exception), cm.exception)
        for e in (cm.exception, MySQLError(1146, "Table 'site_reports.ad_run' doesn't exist"),
                  MySQLError(1146, "Table 'site_reports.ad_dc' doesn't exist"), MySQLError(1146, "Table 'site_reports.ad_link' doesn't exist")):
            a, db = self.failed(e)
            self.assertEqual((db["missing"], db["stale"]), (True, ""), e)
            self.assertNotIn("model", db)
            self.assertEqual(a.db_fails, 0)
            self.assertLessEqual(a.db_due - time.time(), sp.AD_POLL + 1)  # read again every minute, never later and later

    def test_2_a_connection_or_grant_error_stays_a_fault(self):
        for e in (MySQLError(2003, "Can't connect to MySQL server on 'db.example.mil'"),
                  MySQLError(1142, "SELECT command denied to user 'report_reader'@'192.0.2.10' for table 'ad_run'"),
                  MySQLError(1045, "Access denied for user 'report_reader'@'192.0.2.10'"),
                  MySQLError(1932, "Table 'site_reports.ad_link' doesn't exist in engine")):        # a storage fault, not "not made yet"
            a, db = self.failed(e)
            self.assertFalse(db["missing"], e)
            self.assertIn("MySQL error %d" % e.args[0], db["stale"])

    def test_3_the_tables_made_then_read(self):
        a = live(Bare(), Site(self))
        a.db_failed(MySQLError(1146, "Table 'site_reports.ad_run' doesn't exist"))
        a.db = sp.AdDb(None, connect=Lite, mark="?")                  # the AD job ran: its tables are there, no run yet
        a.read_db()
        db = json.loads(a.answer()[1])["db"]
        self.assertEqual((db["missing"], db["stale"]), (False, ""))
        self.assertIn("model", db)

    def test_5_tables_gone_after_a_good_read_is_a_fault(self):
        # Codex: a run was read, then the tables are gone - the run shown is the last good read, said as stale, never as live
        a = live(fill(["ok"] * 3), Site(self))
        a.read_db()
        a.db_failed(MySQLError(1146, "Table 'site_reports.ad_link' doesn't exist"))
        db = json.loads(a.answer()[1])["db"]
        self.assertFalse(db["missing"])
        self.assertIn("MySQL error 1146", db["stale"])
        self.assertEqual(db["model"]["run"]["job"], 9002)
        self.assertGreater(a.db_fails, 0)

    def test_6_the_helper(self):
        self.assertTrue(sp.missing_table(RuntimeError("Table 'site_reports.ad_run' doesn't exist (MySQL error 1146)")))   # short_error's text
        self.assertFalse(sp.missing_table(RuntimeError("Can't connect to MySQL server (MySQL error 2003)")))
        self.assertFalse(sp.missing_table(MySQLError(1932, "Table 'site_reports.ad_run' doesn't exist in engine")))
        self.assertIs(sp._missing_table, sp.missing_table)            # the map's AD layer: the same rule

    def test_4_said_once_in_the_log(self):
        logged = []
        a = live(Lite(), Site(self))
        a.log = types.SimpleNamespace(add=logged.append)
        for _ in range(3):
            a.db_failed(MySQLError(1146, "Table 'site_reports.ad_run' doesn't exist"))
        self.assertEqual([x["database"] for x in logged], ["no AD tables yet"])


class NotKept11123(unittest.TestCase):
    """1.112.3: ad_db on and no Report database credential on the template (no REPORT_DB_HOST): the run is not kept, and the
    job says so - in its output and in the report's "How this was read" - instead of skipping db.yml without a word."""
    LINE = ("Not kept in the report database: this template has no Report database credential - the portal's Active Directory "
            "page stays empty until it has one")

    def test_1_the_filter(self):
        rep = {"sections": [{"title": "Domain controllers"}, {"title": "How this was read", "text": "Reader mode", "lines": ["Not run - x."]}]}
        out = dbf.ad_db_how_line(rep, self.LINE)
        self.assertEqual(out["sections"][1]["lines"], ["Not run - x.", self.LINE])
        self.assertEqual(rep["sections"][1]["lines"], ["Not run - x."])              # the report given is not changed
        out = dbf.ad_db_how_line({"sections": [{"title": "Not read"}]}, self.LINE)
        self.assertEqual(out["sections"][-1], {"title": "How this was read", "status": "unknown", "text": self.LINE})
        self.assertEqual(dbf.ad_db_how_line({}, self.LINE), {})                        # the web form when there is none

    def play(self, ad_db, db_host=""):
        ap = os.environ.get("ANSIBLE_PLAYBOOK") or shutil.which("ansible-playbook")
        if not ap:
            self.skipTest("no ansible-playbook")
        import yaml
        with open(os.path.join(ROOT, "roles", "ad_report", "tasks", "main.yml")) as fh:
            task = [t for t in yaml.safe_load(fh) if str(t.get("name", "")).startswith("AD | not kept in the report database")]
        self.assertEqual(len(task), 1)
        task = dict(task[0], **{"ansible.builtin.include_tasks": os.path.join(ROOT, "roles", "ad_report", "tasks", "db_none.yml")})
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        play = [{"hosts": "localhost", "gather_facts": False, "vars": {
                    "ad_db": ad_db, "_ad_web": {},
                    "_ad_doc": {"title": "AD", "sections": [{"title": "How this was read", "text": "Reader mode", "lines": ["Not run - x."]}]}},
                 "tasks": [task, {"name": "the report", "ansible.builtin.debug": {"msg": "DOC{{ _ad_doc | to_json }}WEB{{ _ad_web | to_json }}END"}}]}]
        with open(os.path.join(d, "p.yml"), "w") as fh:
            yaml.safe_dump(play, fh)
        env = dict(os.environ, ANSIBLE_FILTER_PLUGINS=os.path.join(ROOT, "plugins", "filter"), ANSIBLE_NOCOLOR="1",
                   ANSIBLE_STDOUT_CALLBACK="default", ANSIBLE_LOCALHOST_WARNING="0", ANSIBLE_INVENTORY_UNPARSED_WARNING="0")
        env.pop("REPORT_DB_HOST", None)
        if db_host:
            env["REPORT_DB_HOST"] = db_host
        r = subprocess.run([ap, "-i", "localhost,", "-c", "local", os.path.join(d, "p.yml")], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, env=env, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        m = re.search(r"DOC(.*?)WEB(.*?)END", r.stdout.replace('\\"', '"'))
        self.assertIsNotNone(m, r.stdout[-3000:])
        return r.stdout, json.loads(m.group(1)), json.loads(m.group(2))

    def test_2_no_credential_said_in_the_output_and_the_report(self):
        out, doc, web = self.play(True)
        self.assertIn(self.LINE, " ".join(out.split()))                                # the job's output
        self.assertEqual(doc["sections"][0]["lines"], ["Not run - x.", self.LINE])     # How this was read
        self.assertEqual(web, {})

    def test_3_said_only_when_it_matters(self):
        for ad_db, host in ((False, ""), (True, "db.example.mil")):                    # ad_db off; or the credential is there (db.yml)
            out, doc, _web = self.play(ad_db, host)
            self.assertNotIn("Not kept in the report database", out, (ad_db, host))
            self.assertEqual(doc["sections"][0]["lines"], ["Not run - x."])


class Answer1012(unittest.TestCase):
    """/api/ad: the database's runs with their times, SolarWinds for the newest run, the full report's link; every state said."""

    def test_1_model_and_solarwinds_for_its_run(self):
        a = live(fill(["ok"] * 5 + ["critical"] * 2), Site(self))
        a.read_db()
        src = a.sw_source()
        self.assertEqual((src[0], src[3]), ("run-7", "9006"))          # the database's newest run (its row, its job)
        a.sw = a.read_sw(src)
        out = json.loads(a.answer()[1])
        m = out["db"]["model"]
        self.assertTrue(out["db"]["on"])
        self.assertEqual((m["run"]["job"], m["run"]["old"]), (9006, False))
        self.assertTrue(m["run"]["as_of"].endswith("UTC"))
        d3 = m["dcs"][0]
        self.assertEqual((d3["dc"], d3["runs_since"]), ("dc03", 2))
        self.assertTrue(d3["since_shown"].endswith("UTC"))
        self.assertNotIn("live", m)                                    # the DCs for SolarWinds stay on the server
        self.assertEqual(m["worst7"], {"dc_id": "dc03", "dc": "dc03", "domain": "corp.test", "critical": 2})
        self.assertEqual(out["sw"]["job"], "9006")
        rows = dict((r[1], r) for r in out["sw"]["section"]["rows"])
        self.assertEqual(rows["dc02"][2], "Down")
        self.assertIn("read by the portal every 10 min", out["sw"]["section"]["text"])
        self.assertEqual(out["sw"]["tile"]["value"], 1)
        self.assertIsNone(out["report"])                               # job 9006 is not on the index: no link to another run's page

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
        try:                                                            # 100 s later, the same reads: the same tag (the ages are not in it)
            sp.time.time = lambda: real() + 100
            self.assertEqual(a.answer(etag)[0], 304)
        finally:
            sp.time.time = real
        now, ad = ad_run(0, "critical")
        adb.write(lite, now, 9100, 12, dbf.ad_db_payload(ad, {"kind": "ad", "dcs": [{"dc": "dc01"}]}, {"solarwinds": sw_answer()}))
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


class Review1012(unittest.TestCase):
    """The review of 1.101.2: each finding's case."""

    def test_1_a_new_runs_failing_solarwinds_keeps_its_backoff(self):
        lite = fill(["ok"] * 2)
        calls = []

        def boom(lv):
            calls.append(1)
            raise RuntimeError("SolarWinds did not answer")
        a = sp.AdLive(Site(self).cfg(), sp.Log(None), source=boom, db=sp.AdDb(None, connect=lambda: lite, mark="?"))
        a.cfg.ad_swis_why = ""
        a.seen = time.time()
        a.db_due = 1e18                                                 # the database read once, by hand
        a.read_db()
        waits = []
        a.wake.wait = lambda t: waits.append(t) or (len(waits) >= 3 and (_ for _ in ()).throw(SystemExit))
        with self.assertRaises(SystemExit):
            a._run()
        self.assertEqual(len(calls), 1)                                 # tried once for the run, then its backoff - not every pass
        self.assertGreaterEqual(min(waits), 5)
        a.answer()
        self.assertFalse(a.wake.is_set())                               # a viewer's ask does not start another try for that run

    def test_2_an_answer_from_before_a_quiet_spell_is_said_as_refreshing(self):
        a = live(fill(["ok"] * 2), Site(self))
        a.read_db()
        code, _b, etag = a.answer()
        self.assertFalse(json.loads(_b)["db"]["refreshing"])
        a.db_at -= 3 * sp.AD_POLL
        code, b, etag2 = a.answer(etag)
        self.assertEqual(code, 200)                                     # another tag: the page hears it
        self.assertTrue(json.loads(b)["db"]["refreshing"])

    def test_3_a_new_ca_is_written_again(self):
        s = Site(self)
        seen = []
        cfg = s.cfg(PORTAL_REPORT_DB=json.dumps({"host": "db.example.mil", "user": "report_reader", "password": "x", "tls": True, "ca_pem": "OLD"}))
        real = sp.site_report_db.connect if sp.site_report_db else None
        try:
            sp.site_report_db.connect = lambda p: (seen.append(open(p["ca_file"]).read()), (object(), "tls"))[1]
            sp.site_report_db.HAS_PYMYSQL = True
            sp.AdDb(cfg)._connect()
            cfg.report_db["ca_pem"] = "NEW"
            sp.AdDb(cfg)._connect()
        finally:
            sp.site_report_db.connect = real
        self.assertEqual(seen, ["OLD", "NEW"])
        self.assertEqual(oct(os.stat(os.path.join(s.tmp, "data", "report-db-ca.pem")).st_mode & 0o777), "0o600")

    def test_4_a_problem_with_no_domain_counts_for_its_dc(self):
        _now, ad = ad_run(0)
        ad["problems"] = [{"domain": "", "object": "dc02", "severity": "critical", "text": "Security: the audit log was cleared"},
                          {"domain": "corp.test", "object": "dc01", "severity": "info", "text": "for your information"}]
        p = dbf.ad_db_payload(ad, {}, {})
        st = dict((d["dc"], (d["status"], d["worst"])) for d in p["dcs"])
        self.assertEqual(st["dc02"], ("critical", "Security: the audit log was cleared"))
        self.assertEqual(st["dc01"], ("ok", ""))                         # info: OK on the page

    def test_5_runs_told_apart_by_their_row_not_the_job(self):
        lite = Lite()
        for back in (60, 0):
            now, ad = ad_run(back)
            adb.write(lite, now, 0, 12, dbf.ad_db_payload(ad, {"kind": "ad", "dcs": [{"dc": "DC0%d" % (1 if back else 2)}]}, {}))
        a = live(lite, Site(self))
        a.read_db()
        self.assertEqual(a.sw_source()[0], "run-2")
        a.sw = a.read_sw(("run-1", {"dcs": [{"dc": "dc01"}]}, 12, "0"))     # the run before's read, job 0 as well
        self.assertNotIn("section", json.loads(a.answer()[1])["sw"])      # not shown as this run's

    def test_6_the_report_link_of_the_run_shown(self):
        s = Site(self)
        s.index(extra=[{"latest": "ad-forest2/latest.html", "page": "ad-forest2/9500.html", "when": utc(0), "job": "9500", "live": "ad",
                        "runs": [{"job": "9500", "page": "ad-forest2/9500.html", "when": utc(0)},
                                 {"job": "9499", "page": "ad-forest2/9499.html", "when": utc(60)}]}])
        a = sp.AdLive(s.cfg(), sp.Log(None))
        self.assertEqual(a.report_link("77")["url"], "/portal/reports/ad-health/latest.html")         # the forest shown
        self.assertEqual(a.report_link("9500")["url"], "/portal/reports/ad-forest2/9500.html")        # the run's own page
        self.assertEqual(a.report_link("9499")["url"], "/portal/reports/ad-forest2/9499.html")        # an older run: its own, not 9500's
        self.assertIsNone(a.report_link("1"))                                                         # not on the index: no link
        self.assertIsNone(a.report_link())


class ReportLink1012(unittest.TestCase):
    """The full report's link: index.json's newest line marked live: ad, a page inside the folder, read again when it changed."""

    def test_1_the_newest_and_never_outside(self):
        s = Site(self)
        a = sp.AdLive(s.cfg(), sp.Log(None))
        self.assertEqual(a.report_link("77")["url"], "/portal/reports/ad-health/latest.html")
        s.index(extra=[{"latest": "ad-other/latest.html", "when": utc(0), "job": "9", "live": "ad"}])
        self.assertEqual(a.report_link("9")["url"], "/portal/reports/ad-other/latest.html")
        self.assertIsNone(a.report_link("5"))                                                         # not marked live: ad
        for bad in ("../secret.html", "/etc/x.html", "a/../../x.html", "ad\\\\x.html"):
            with open(os.path.join(s.root, "index.json"), "w") as fh:
                json.dump({"reports": [{"latest": bad, "when": utc(0), "job": "9", "live": "ad"}]}, fh)
            self.assertIsNone(sp.AdLive(s.cfg(), sp.Log(None)).report_link("9"), bad)

    def test_2_missing_or_half_written(self):
        s = Site(self, index=False)
        a = sp.AdLive(s.cfg(), sp.Log(None))
        self.assertIsNone(a.report_link("77"))
        s.index()
        self.assertIsNotNone(a.report_link("77"))
        with open(os.path.join(s.root, "index.json"), "w") as fh:
            fh.write('{"reports": [')
        self.assertEqual(a.report_link("77")["url"], "/portal/reports/ad-health/latest.html")    # the last one, read again next time


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
        self.assertEqual((code, a["db"]["on"], a["report"]), (200, False, None))          # no run shown: no report link
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
        for n in ("site_swis_db.py", "site_ad_swis.py", "ad_swis_filters.py", "site_report_db.py", "site_ad_ident.py", "static/ad-page.js", "static/ad-page.css"):
            self.assertIn("dest: %s}" % n, t)
        for k in ("PORTAL_AD:", "PORTAL_AD_SECONDS:", "PORTAL_AD_OLD_MINUTES:", "PORTAL_AD_TEMPLATE:", "site-portal-reportdb"):
            self.assertIn(k, t)
        with open(os.path.join(ROOT, "roles", "site_portal_stack", "templates", "site-portal-app.container.j2")) as fh:
            self.assertIn("Secret=site-portal-reportdb,type=env,target=PORTAL_REPORT_DB", fh.read())
        # the portal's copy of the readers: site_time beside them, no plugins/ folder, no ansible
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        shutil.copy(os.path.join(ROOT, "plugins", "filter", "ad_swis_filters.py"), d)
        for n in ("site_time.py", "site_swis.py", "site_swis_db.py", "site_ad_swis.py", "site_report_db.py", "site_ad_ident.py"):
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
        self.assertEqual(t["Site portal - deploy"]["optional_credentials"], ["report_db_reader", "report_db", "vmware", "ontap"])   # 1.129.0: the wall's live reads
        self.assertIn("report_db", t["Active Directory health report"]["optional_credentials"])


class Job1012(unittest.TestCase):
    def test_1_the_live_block(self):
        b = af.ad_live_block({"problems": [{"domain": "corp.test", "object": "dc01", "severity": "critical", "text": "x" * 400, "area": "LDAP"},
                                           {"text": "no object"}]},
                             [{"dc": "dc01", "ip": "192.0.2.11"}, {"dc": ""}], {"cpu_warn": "85", "days": "14"})
        self.assertEqual(b["kind"], "ad")
        self.assertEqual(b["dcs"], [{"dc": "dc01", "ip": "192.0.2.11", "dc_id": "dc01"}])
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

    def answer(self, states=None, down=("dc02",), sw_on=True, db=True, **kw):
        states = states or (["ok"] * 40 + ["critical"] * 8)
        lite = kw.get("lite") or fill(states)
        site = Site(self)
        job = str(9000 + len(states) - 1)
        site.index(extra=[{"latest": "ad-health/latest.html", "page": "ad-health/%s.html" % job, "when": utc(0), "job": job, "live": "ad"}])
        a = live(lite if db else None, site, down=down, swis_why="" if sw_on else "the portal has no SolarWinds login", env=kw.get("env", {}))
        if db:
            a.read_db()
            if kw.get("stale"):
                a.db_stale = kw["stale"]
            if sw_on:
                a.source = kw.get("source") or a.source
                a.sw = a.read_sw(a.sw_source())
        return json.loads(a.answer()[1])

    def page(self, answers, js, look="atlas", width=1280, wait=1500, motion_off=False, budget=90000):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        cfg = Site(self).cfg(PORTAL_THEME=look, PORTAL_MOTION="off" if motion_off else "auto")
        html = sp.ad_page(cfg, "DOE.JANE.Q.1234567890", {}).replace("/portal/static/", "")
        for n in ("ad-page.js", "ad-page.css", "portal.css", "portal-theme.js"):
            shutil.copy(os.path.join(FILES, "static", n), d)
        with open(os.path.join(d, "banner.css"), "w") as fh:
            fh.write("")
        with open(os.path.join(d, "stub.js"), "w") as fh:
            fh.write("(function(){var A=%s,i=0;window.__sent=[];window.fetch=function(u,o){var a=A[Math.min(i++,A.length-1)];"
                     "window.__sent.push(((o||{}).headers||{})['If-None-Match']||'');"
                     "if(a==='FAIL')return Promise.reject(new Error('offline'));if(a==='HANG')return new Promise(function(){});"
                     "if(a==='304')return Promise.resolve({status:304,ok:false,headers:{get:function(){return ''}}});"
                     "if(a==='CUT'||a==='HANGBODY')return Promise.resolve({status:200,ok:true,headers:{get:function(){return 'e'+i}},json:function(){"
                     "return a==='CUT'?Promise.reject(new Error('the body was cut')):new Promise(function(){})}});"
                     "return Promise.resolve({status:200,ok:true,"
                     "headers:{get:function(){return 'e'+i}},json:function(){return Promise.resolve(a)}})}})();" % json.dumps(answers))
        with open(os.path.join(d, "t.js"), "w") as fh:
            fh.write("setTimeout(function(){var r={};try{%s}catch(e){r.err=String(e)}document.getElementById('res').textContent=JSON.stringify(r)},%d);"
                     % (js, wait))
        html = html.replace('<script src="portal.js?v=" defer></script>', "").replace('<script src="portal.js" defer></script>', "")
        html = html.replace("<head>", '<head><script src="stub.js"></script>', 1)
        html = html.replace("</body>", '<pre id="res"></pre><script src="t.js"></script></body>', 1)
        with open(os.path.join(d, "p.html"), "w") as fh:
            fh.write(html)
        out = subprocess.run([_browser(), "--headless=new", "--no-sandbox", "--disable-gpu", "--window-size=%d,900" % width, "--virtual-time-budget=%d" % budget,
                              "--dump-dom", "file://" + os.path.join(d, "p.html")], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             universal_newlines=True, timeout=180).stdout
        m = re.search(r'<pre id="res">(.*?)</pre>', out, re.S)
        self.assertIsNotNone(m, out[-1500:])
        return json.loads(m.group(1).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))

    READ = ("var q=function(s){return [].map.call(document.querySelectorAll(s),function(x){return x.textContent.trim()})};"
            "r.h2=q('#ad section.sec > h2');r.tiles=q('.adp-tiles .tile .k');r.tv=q('.adp-tiles .tile .v');r.tnotes=q('.adp-tiles .tile .n');r.notes=q('#ad .note');"
            "r.live=q('.adp-live .l').join('');r.link=(document.querySelector('.adp-live a')||{}).href||'';"
            "r.dcs=[].map.call(document.querySelectorAll('.adp-dcs tbody tr'),function(tr){return [tr.cells[0].querySelector('b').textContent,tr.className,tr.cells[2].textContent,"
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
                self.assertEqual(r["dcs"][0][:6], ["dc03", "r-critical", "Critical", r["dcs"][0][3], 48, 8], (look, w))
                self.assertRegex(r["dcs"][0][3], r"^since (\d{4}-\d\d-\d\d )?\d\d:\d\d UTC · 8 runs$")   # just after midnight UTC the runs began yesterday: its date said
                self.assertEqual([x[0] for x in r["dcs"]], ["dc03", "dc02", "dc01"])   # AD's worst, then SolarWinds down
                self.assertTrue(r["dcs"][1][6].startswith("Down"), r["dcs"][1][6])
                self.assertIn("Live", r["live"])
                self.assertIn("SolarWinds: updated just now", r["live"])
                self.assertTrue(r["link"].endswith("/portal/reports/ad-health/9047.html"), r["link"])       # the shown run's own page
                self.assertEqual(r["notes"], [])
                self.assertGreaterEqual(r["bars"], 1)
                self.assertEqual(r["runs"], 8)
                self.assertRegex(r["links"][0], r"failing, no success since \d{4}-\d\d-\d\d \d\d:\d\d UTC")   # another day: its date said
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
        two = self.answer(["ok"] * 9 + ["critical"], down=("dc02", "dc03"))
        r = self.page([one, two], self.READ, wait=68000)                   # the second answer at 60 s, its toasts for 15 s
        self.assertEqual(sorted(r["toasts"]), ["dc03: AD Critical", "dc03: SolarWinds Down"])
        self.assertEqual(r["flash"], 1)
        self.assertTrue(r["dcs"][0][3].endswith(" · 1 run"), r["dcs"][0][3])
        r = self.page([one, two], self.READ, wait=68000, motion_off=True)
        self.assertEqual((len(r["toasts"]), r["flash"]), (2, 0))              # portal_motion off: said, no flash


class Codex1012(unittest.TestCase):
    """Codex's review of 1.101.2: each finding's case (P1 first)."""

    def test_p1_1_links_as_the_matrix_says(self):
        _now, ad = ad_run(0, fail_link=False)
        ad["repl"]["links"] = [{"frm": "dc01", "to": "dc03", "nc": "corp.test", "last_success": "", "failures": 0},                # never replicated
                               {"frm": "dc02", "to": "dc01", "nc": "corp.test", "last_success": ad["repl"]["now"], "failures": 1},   # one failure, a success just now
                               {"frm": "dc03", "to": "dc02", "nc": "corp.test", "last_success": (dt_of(ad["repl"]["now"], -4.5)), "failures": 0}]
        st = [x["status"] for x in dbf.ad_db_payload(ad, {}, {"low_hours": 0, "warn_hours": 6, "crit_hours": 24})["links"]]
        self.assertEqual(st, ["warning", "warning", "ok"])               # never: warning; one failure: warning; low_hours 0: no Low
        st = [x["status"] for x in dbf.ad_db_payload(ad, {}, {"low_hours": 4})["links"]]
        self.assertEqual(st[2], "low")

    def test_p1_2_a_database_that_never_answers_is_given_up(self):
        seen = []
        real = sp.site_report_db.connect
        try:
            sp.site_report_db.connect = lambda p: (seen.append(p), (object(), "none"))[1]
            sp.site_report_db.HAS_PYMYSQL = True
            sp.AdDb(Site(self).cfg(PORTAL_REPORT_DB=json.dumps({"host": "db.example.mil", "user": "report_reader", "password": "x"})))._connect()
        finally:
            sp.site_report_db.connect = real
        self.assertEqual(seen[0]["read_timeout"], sp.AD_DB_READ_TIMEOUT)
        kw = []

        class Fake(object):
            @staticmethod
            def connect(**k):
                kw.append(k)
                raise RuntimeError("stop")
        real_pm = getattr(sp.site_report_db, "pymysql", None)
        try:
            sp.site_report_db.pymysql = Fake
            with self.assertRaises(Exception):
                sp.site_report_db.connect({"host": "h", "port": 1, "user": "u", "password": "p", "database": "d", "tls": False, "ca_file": "",
                                           "connect_timeout": 5, "read_timeout": 30})
        finally:
            sp.site_report_db.pymysql = real_pm
        self.assertEqual((kw[0]["read_timeout"], kw[0]["write_timeout"]), (30, 30))

    def test_p1_5_6_solarwinds_by_node_id_and_never_up_unless_up(self):
        a = live(fill(["ok"]), Site(self))
        a.source = lambda lv: dict(sw_answer(), dcs=[dict(d, matched="", status="", node_id=None) if d["dc"] == "dc03" else d for d in sw_answer()["dcs"]])
        a.read_db()
        a.sw = a.read_sw(a.sw_source())
        out = json.loads(a.answer()[1])
        self.assertEqual(sorted(out["sw"]["section"]["keys"]), ["", "1", "2"])
        if not _browser():
            return
        r = Browser1012.page(self, [out], Browser1012.READ)
        i = r["tiles"].index("SolarWinds: down or warning")
        self.assertNotIn("every DC up", self.tile_note(r, i))
        self.assertIn("dc02 Down", self.tile_note(r, i))

    @staticmethod
    def tile_note(r, i):
        return r["tnotes"][i]

    def test_p2_8_the_trend_is_24_hours_by_read_at(self):
        m = sp.AdDb(None, connect=lambda: fill(["ok"] * 40, every=60), mark="?").read()
        self.assertEqual(len(m["times"]), 24)                            # hourly: 24 runs, not 48 (two days)
        m = sp.AdDb(None, connect=lambda: fill(["ok"] * 200, every=10), mark="?").read()
        self.assertEqual(len(m["times"]), 144)                           # every 10 min: the whole day

    def test_p2_9_a_streak_with_no_run_before_it(self):
        m = sp.AdDb(None, connect=lambda: fill(["critical"] * 60), mark="?").read()
        d3 = [d for d in m["dcs"] if d["dc"] == "dc03"][0]
        self.assertEqual(d3["runs_since"], 60)                           # from the first kept run, not the window's first
        import datetime as dt
        age = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.datetime.strptime(d3["since"], "%Y-%m-%dT%H:%M:%SZ")).total_seconds() / 60
        self.assertTrue(59 * 30 - 5 <= age <= 59 * 30 + 5, age)
        # a run before the 24 h that the DC was not in ends the streak too (the runs after it: 48)
        db = fill(["critical"] * 50)
        db.db.execute("DELETE FROM ad_dc WHERE dc = 'dc03' AND run_id = 2")
        d3 = [d for d in sp.AdDb(None, connect=lambda: db, mark="?").read()["dcs"] if d["dc"] == "dc03"][0]
        self.assertEqual(d3["runs_since"], 48)

    def test_p2_10_an_inbound_failure_is_the_receiving_dcs(self):
        _now, ad = ad_run(0)
        ad["problems"] = [{"domain": "corp.test", "object": "dc02 <- dc01 (Configuration)", "severity": "critical", "text": "8524"}]
        st = dict((d["dc"], d["status"]) for d in dbf.ad_db_payload(ad, {}, {})["dcs"])
        self.assertEqual((st["dc02"], st["dc01"]), ("critical", "ok"))
        self.assertEqual(af.ad_live_block(ad, [])["problems"][0]["dc"], "dc02")

    def test_p2_11_second_domain_cannot_pollute_the_worst_dc(self):
        db = Lite()
        now, ad = ad_run(0)
        ad["dcs"].append({"dc": "dc01", "domain": "other.example.mil", "read": True})
        adb.write(db, now, 9000, 12, dbf.ad_db_payload(ad))
        model = sp.AdDb(None, connect=lambda: db).read()
        self.assertIsNone(model["worst7"])
        self.assertEqual(model["run"]["status"], "unknown")
        self.assertEqual(model["run"]["why"], dbf.ident.REFUSAL)

    def test_p2_13_the_next_run_per_template(self):
        calls = []

        class Aap(object):
            def call(self, method, path):
                tid = path.split("/")[1]
                calls.append(tid)
                return 200, {"results": [{"next_run": "2026-11-22T10:%s:00Z" % tid[-2:]}]}
        a = live(fill(["ok"] * 2, template_id=12), Site(self))
        a.aap = Aap()
        a._schedule("12")
        a._schedule("34")                                                # within 10 min of 12's: still asked
        a._schedule("12")                                                # cached
        self.assertEqual(calls, ["12", "34"])
        a.read_db()
        self.assertIn("10:12", json.loads(a.answer()[1])["db"]["model"]["run"]["next"])
        a.dbm = dict(a.dbm, run=dict(a.dbm["run"], template_id=34))
        self.assertIn("10:34", json.loads(a.answer()[1])["db"]["model"]["run"]["next"])   # 34's own, never 12's
        b = live(fill(["ok"] * 2), Site(self))                           # no SolarWinds DCs in the run: the schedule asked all the same
        b.aap = Aap()
        b.read_db()
        b.dbm = dict(b.dbm, live={})
        b.seen, b.db_due, b.sw_due = time.time(), 1e18, 1e18
        b.wake.wait = lambda t: (_ for _ in ()).throw(SystemExit)
        with self.assertRaises(SystemExit):
            b._run()
        self.assertEqual(calls[-1], "12")

    def test_p2_14_prune_keeps_other_templates_runs(self):
        db = fill(["ok"] * 2, template_id=12)
        fill(["ok"] * 3, template_id=34, db=db)
        import datetime as dt
        n = adb.prune(db, dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(days=8), 7, 12)
        self.assertEqual(n, 2)
        self.assertEqual(db.db.execute("SELECT template_id, COUNT(*) FROM ad_run GROUP BY template_id").fetchall(), [(34, 3)])
        self.assertEqual(db.db.execute("SELECT COUNT(*) FROM ad_dc").fetchone()[0], 9)

    def test_p2_15_an_old_portal_python_is_passed_over(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        old = os.path.join(d, "oldpy")
        with open(old, "w") as fh:
            fh.write("#!/bin/sh\n[ \"$1\" = -c ] && exit 1\necho OLD\n")      # a Python 3.6: its version check fails
        os.chmod(old, 0o755)
        with open(os.path.join(d, "p.py"), "w") as fh:
            fh.write("import sys\nprint(sys.version_info >= (3, 8))\n")
        with open(os.path.join(FILES, "start.sh")) as fh:
            sh = fh.read().replace("/app/site_portal.py", os.path.join(d, "p.py"))
        with open(os.path.join(d, "start.sh"), "w") as fh:
            fh.write(sh)
        out = subprocess.run(["/bin/sh", os.path.join(d, "start.sh")], env=dict(os.environ, PORTAL_PYTHON=old),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(out.stdout.strip(), "True", out.stdout)


def dt_of(iso, hours):
    import datetime as dt
    return (dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ") + dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


@unittest.skipUnless(_browser(), "no google-chrome or chromium")
class CodexBrowser1012(unittest.TestCase):
    """Codex's review of 1.101.2: the page's findings, in the browser."""
    answer = Browser1012.answer
    page = Browser1012.page
    READ = Browser1012.READ

    def test_p1_3_a_failed_poll_is_not_live_and_grows_old(self):
        a = self.answer(["ok"] * 3)
        a["poll"] = 15
        a["old_after"] = 89
        a["db"]["model"]["run"].update(age_min=89, old=False)
        r = self.page([a, "FAIL"], self.READ, wait=1500)
        self.assertIn("Live", r["live"])
        self.assertNotIn("Not live", r["live"])
        r = self.page([a, "FAIL"], self.READ, wait=70000)               # failing from 15 s on; 60 s on, the run is 90 min old
        self.assertIn("Not live", r["live"])
        self.assertTrue(any(n.startswith("The portal did not answer (offline): this is its answer of") for n in r["notes"]), r["notes"])
        self.assertTrue(any("old (more than 89 min)" in n for n in r["notes"]), r["notes"])
        r = self.page([a, "FAIL", a], self.READ, wait=40000)            # an answer again: Live again, the note gone
        self.assertIn("Live", r["live"])
        self.assertFalse(any("did not answer" in n for n in r["notes"]), r["notes"])

    def test_1112_3_no_ad_table_yet_is_said_calmly(self):
        a = live(Bare(), Site(self))
        a.db_failed(MySQLError(1146, "Table 'site_reports.ad_run' doesn't exist"))
        js = self.READ + "r.cls=[].map.call(document.querySelectorAll('#ad .note'),function(n){return n.className});"
        r = self.page([json.loads(a.answer()[1])], js)
        self.assertEqual(r["notes"], ["No AD run in the report database yet. Run Active Directory health report once with the Report "
                                      "database credential on its template (it makes the tables; ad_db_create). Then this page fills."])
        self.assertEqual(r["cls"], ["note muted"])                       # not red (note bad), no "Tried again"
        r = self.page([json.loads(a.answer()[1]), "304"], self.READ + "r.n=window.__sent.length;", wait=20000)
        self.assertEqual(r["n"], 1)                                     # an answer, not a read in progress: asked again in a minute, not 3 s
        b = live(fill(["ok"] * 3), Site(self))                           # tables gone after a good read: the last run, not Live
        b.read_db()
        b.db_failed(MySQLError(1146, "Table 'site_reports.ad_link' doesn't exist"))
        r = self.page([json.loads(b.answer()[1])], js)
        self.assertIn("Not live", r["live"])
        self.assertTrue(any(n.startswith("The report database was not read just now (Table") for n in r["notes"]), r["notes"])
        a = live(Lite(), Site(self))                                       # a grant error: still the red box
        a.db_failed(MySQLError(1142, "SELECT command denied to user 'report_reader'"))
        r = self.page([json.loads(a.answer()[1])], js)
        self.assertEqual(r["cls"], ["note bad"])
        self.assertTrue(r["notes"][0].startswith("The report database could not be read: SELECT command denied"), r["notes"])

    def test_p1_4_a_failed_read_after_an_empty_one_is_said(self):
        a = live(Lite(), Site(self))
        a.read_db()
        a.db_stale = "Access denied for user 'report_reader'"
        r = self.page([json.loads(a.answer()[1])], self.READ)
        self.assertTrue(r["notes"][0].startswith("The report database could not be read: Access denied"), r["notes"])
        self.assertFalse(any("No AD run in the report database yet" in n for n in r["notes"]), r["notes"])

    def test_p1_7_the_chart_card_without_inline_style(self):
        a = self.answer()
        js = (self.READ + "var h=document.querySelectorAll('.adp-chart rect.hit');var b=h[h.length-1].getBoundingClientRect();"
              "h[h.length-1].dispatchEvent(new MouseEvent('mousemove',{clientX:b.left+2,clientY:b.top+40,bubbles:true}));"
              "var g=document.querySelector('.adp-chart .tipg');r.vis=g.getAttribute('visibility');r.tip=g.textContent;"
              "r.styled=[].filter.call(document.querySelectorAll('#ad *'),function(e){return e.hasAttribute('style')}).length;"
              "var t=g.getAttribute('transform').match(/translate\\(([-\\d.]+),([-\\d.]+)\\)/);r.tx=+t[1];"
              "r.W=+document.querySelector('.adp-chart svg').getAttribute('viewBox').split(' ')[2];")
        r = self.page([a], js)
        self.assertEqual((r["vis"], r["styled"]), ("visible", 0))
        self.assertRegex(r["tip"], r"DCs? critical · \d+ warning$")
        self.assertTrue(0 <= r["tx"] < r["W"], r)                        # kept inside the chart
        with open(os.path.join(FILES, "static", "ad-page.js")) as fh:
            self.assertNotRegex(fh.read(), r"\.style\b|setAttribute\(\"style\"")


def one_run(ad_mut=None, payload_mut=None, template_id=12, db=None, job=9000, minutes_ago=0):
    """A database with one AD run (ad_run's dc01-dc03, changed by ad_mut / payload_mut)."""
    db = db or Lite()
    now, ad = ad_run(minutes_ago)
    if ad_mut:
        ad_mut(ad)
    p = dbf.ad_db_payload(ad, {"kind": "ad", "dcs": [{"dc": "dc01"}]}, {"solarwinds": sw_answer()})
    if payload_mut:
        payload_mut(p)
    adb.write(db, now, job, template_id, p)
    db.commit()
    return db


class CodexB1012(unittest.TestCase):
    """Codex's second pass on 1.101.2 (12 P1): each finding's case."""

    def test_1_a_node_dns_suffix_is_not_an_ad_domain(self):
        import site_ad_swis as asw
        nodes = [{"node_id": 1, "name": "dc01.example.mil", "dns": ""}]
        self.assertEqual(asw.match([{"dc": "dc01", "domain": "ad.example.mil"}], nodes)[0][0], nodes[0])

    def test_5_overdue_solarwinds_is_said_as_stale(self):
        a = live(fill(["ok"] * 2), Site(self))
        a.read_db()
        a.sw = a.read_sw(a.sw_source())
        self.assertEqual(json.loads(a.answer()[1])["sw"]["stale"], "")
        a.sw["at"] -= a.cfg.ad_seconds + 3 * sp.AD_POLL                  # nobody looked for a while; the new read not back yet
        a.wake.clear()
        out = json.loads(a.answer()[1])["sw"]
        self.assertIn("older than its 10 min interval", out["stale"])
        self.assertIn("section", out)                                  # the last known, said as such
        self.assertTrue(a.wake.is_set())

    def test_6_unread_replication_is_kept_as_unread(self):
        def mut(ad):
            ad["repl"]["dcs"] = [dict(d, read=False) for d in ad["repl"]["dcs"]]
            ad["repl"]["links"] = []
        db = one_run(mut)
        self.assertEqual(db.db.execute("SELECT SUM(repl_read) FROM ad_dc").fetchone()[0], 0)
        self.assertEqual(sp.AdDb(None, connect=lambda: db, mark="?").read()["run"]["repl_read"], 0)
        p = dbf.ad_db_payload(ad_run(0)[1], {}, {})
        self.assertTrue(all(d["repl_read"] for d in p["dcs"]))         # read: kept as read

    def test_7_a_failed_ad_read_keeps_its_own_status_in_the_history(self):
        def mut(p):
            p["dcs"], p["links"], p["status"] = [], [], "critical"
            p.update(repl_dcs=0, repl_read=0, live={})     # a NOT READ run: no DC rows
        db = one_run(payload_mut=mut)
        h = sp.AdDb(None, connect=lambda: db, mark="?").read()["hist"][-1]
        self.assertEqual((h["status"], h["c"], h["w"]), ("critical", 0, 0))

    def test_9_links_of_two_domains_refuse_the_run(self):
        def mut(ad):
            ad["repl"]["links"][0]["domain"] = "other.example.mil"
        db = one_run(mut)
        self.assertEqual(db.db.execute("SELECT COUNT(*) FROM ad_link").fetchone()[0], 0)
        self.assertEqual(db.db.execute("SELECT status, why FROM ad_run").fetchone(), ("unknown", dbf.ident.REFUSAL))

    def test_10_a_run_whose_rows_failed_says_so(self):
        def mut(ad):
            ad["repl"]["links"][0]["failures"] = "unknown"
        db = one_run(mut)
        why = db.db.execute("SELECT why FROM ad_run").fetchone()[0]
        self.assertIn("could not make this run's DCs and links into rows (ValueError", why)
        self.assertIn("ValueError", sp.AdDb(None, connect=lambda: db, mark="?").read()["run"]["why"])

    def test_11_a_new_database_secret_restarts_the_portal(self):
        t = open(os.path.join(ROOT, "roles", "site_portal_stack", "tasks", "main.yml")).read()
        when = t[t.index("- name: Units | restart"):]
        when = when[:when.index("\n\n")]
        self.assertIn("_portal_rdb_set is changed", when)
        self.assertIn("_portal_rdb_gone is changed", when)
        self.assertIn("register: _portal_rdb_gone", t)

    def test_12_the_deploys_probe_has_a_read_deadline(self):
        src = open(os.path.join(FILES, "..", "library", "site_portal_db_check.py")).read()
        self.assertRegex(src, r"db\.connect\(dict\(p, read_timeout=")
        kw = []

        class Fake(object):
            @staticmethod
            def connect(**k):
                kw.append(k)
                raise RuntimeError("stop")
        real = getattr(site_report_db, "pymysql", None)
        try:
            site_report_db.pymysql = Fake
            with self.assertRaises(Exception):
                site_report_db.connect({"host": "h", "port": 1, "user": "u", "password": "p", "database": "d", "tls": False, "ca_file": "",
                                        "connect_timeout": 10, "read_timeout": 20})
        finally:
            site_report_db.pymysql = real
        self.assertEqual(kw[0]["read_timeout"], 20)


@unittest.skipUnless(_browser(), "no google-chrome or chromium")
class CodexBBrowser1012(unittest.TestCase):
    """Codex's second pass on 1.101.2: the page's findings, in the browser."""
    answer = Browser1012.answer
    page = Browser1012.page
    READ = Browser1012.READ + "r.sent=window.__sent;"

    def answer_of(self, db, source=None):
        a = live(db, Site(self))
        if source:
            a.source = source
        a.read_db()
        a.sw = a.read_sw(a.sw_source())
        return json.loads(a.answer()[1])

    def test_2_a_row_without_node_id_is_never_a_dc_up(self):
        out = self.answer_of(one_run(), source=lambda lv: dict(sw_answer(down=()), dcs=[dict(d, node_id=None) for d in sw_answer(down=())["dcs"]]))
        r = self.page([out], self.READ + "r.sw=[].map.call(document.querySelectorAll('.adp-dcs tbody tr'),function(tr){return tr.cells[5].textContent});")
        self.assertTrue(all(x != "Up" for x in r["sw"]))
        self.assertFalse(any("every DC up" in n for n in r["tnotes"]))

    def test_3_the_tag_is_kept_only_with_its_body(self):
        a = self.answer(["ok"] * 3)
        a["poll"] = 15
        r = self.page([a, "CUT", "304"], self.READ, wait=40000)
        self.assertEqual(r["sent"][:3], ["", "e1", "e1"])                # the cut answer's tag (e2) never asked with
        self.assertIn("Live", r["live"])

    def test_4_a_poll_that_hangs_is_a_failed_poll(self):
        a = self.answer(["ok"] * 3)
        a["poll"] = 15
        for hang in ("HANG", "HANGBODY"):
            r = self.page([a, hang], self.READ, wait=40000)             # asked at 15 s; no answer by 35 s
            self.assertIn("Not live", r["live"], hang)
            self.assertTrue(any("no answer in 20 s" in n for n in r["notes"]), (hang, r["notes"]))

    def test_6_8_unread_replication_and_no_dc_found(self):
        def mut(ad):
            ad["repl"]["dcs"] = [dict(d, read=False) for d in ad["repl"]["dcs"]]
            ad["repl"]["links"] = []
        r = self.page([self.answer_of(one_run(mut))], self.READ)
        self.assertTrue(any(n.startswith("Replication was not read in full in this run: Replication read in full on 0 of 3") for n in r["notes"]), r["notes"])
        self.assertFalse(any("every replication link succeeded" in n for n in r["notes"] + r["tnotes"]))
        self.assertEqual(r["tv"][r["tiles"].index("Replication failing")], "not read")

        def nodc(p):
            p["dcs"], p["links"], p["status"] = [], [], "critical"
            p.update(repl_dcs=0, repl_read=0, live={})
        db = one_run(payload_mut=nodc)
        a = live(db, Site(self))
        a.read_db()
        r = self.page([json.loads(a.answer()[1])], self.READ)
        i = r["tiles"].index("DCs read")
        self.assertEqual((r["tv"][i], r["tnotes"][i]), ("none", "DC list not read - coverage unknown"))

    def test_7_10_the_failed_run_in_the_history_and_its_why(self):
        def mut(ad):
            ad["repl"]["links"][0]["failures"] = "unknown"
        db = one_run(mut)
        a = live(db, Site(self))
        a.read_db()
        r = self.page([json.loads(a.answer()[1])], self.READ + "r.badge=(document.querySelector('#ad section:nth-of-type(4) tbody tr td:nth-child(2)')||{}).textContent;")
        self.assertTrue(any(n.startswith("Rows not kept: the AD job could not make") for n in r["notes"]), r["notes"])
        self.assertEqual(r["badge"], "Unknown")
        nodc = one_run(payload_mut=lambda p: p.update(dcs=[], links=[], status="critical"))
        a = live(nodc, Site(self))
        a.read_db()
        r = self.page([json.loads(a.answer()[1])], self.READ + "r.badge=(document.querySelector('#ad section:nth-of-type(4) tbody tr td:nth-child(2)')||{}).textContent;")
        self.assertEqual(r["badge"], "Critical")                         # 0 DCs critical, but the run itself failed


class CodexC1012(unittest.TestCase):
    """Codex's third pass on 1.101.2 (9 P1): each finding's case."""

    def test_1_the_exact_dns_host_before_the_short_label(self):
        import site_ad_swis as asw
        nodes = [{"node_id": 1, "name": "dc01.other.example.mil"}, {"node_id": 2, "name": "dc01.example.mil"}]
        self.assertEqual(asw.match([{"dc": "dc01", "fqdn": "dc01.example.mil", "domain": "ad.example.mil"}], nodes)[0][0]["node_id"], 2)

    def test_2_a_node_claimed_by_two_dcs_matches_neither(self):
        import site_ad_swis as asw
        m = asw.match([{"dc": "dc01", "ip": "192.0.2.10"}, {"dc": "dc02", "ip": "192.0.2.10"}],
                      [{"node_id": 1, "name": "dc01", "ip": "192.0.2.10"}])
        self.assertEqual((m[0][0], m[1][0]), (None, None))
        self.assertTrue(all("claimed by" in m[i][2] for i in (0, 1)))

    def test_3_a_dc_with_a_partition_not_read_is_not_a_full_read(self):
        _now, ad = ad_run(0)
        ad["repl"]["dcs"][0]["partial"] = ["Configuration"]
        p = dbf.ad_db_payload(ad, {}, {})
        self.assertEqual(dict((d["dc"], d["repl_read"]) for d in p["dcs"]), {"dc01": False, "dc02": True, "dc03": True})
        self.assertEqual((p["repl_dcs"], p["repl_read"]), (3, 2))
        import ad_filters
        r = ad_filters._repl_reader([{"in_ad": True, "name": "dc01", "bind": {"ok": True},
                                      "repl": {"links": [], "not_granted": ["CN=Configuration,DC=corp,DC=test"]}}], __import__("datetime").datetime(2026, 11, 22), 60, "corp.test")
        self.assertEqual(r["dcs"][0]["partial"], ["Configuration: no right"])

    def test_4_5_admin_coverage_includes_unpolled_dcs(self):
        import ad_filters
        import datetime as dt
        doms = [{"data": {"dns": "ad.example.mil", "dcs": [{"name": "dc01.example.mil", "site": "SITEA0101"},
                                                             {"name": "dc02.example.mil", "site": "SITEB0201"}]}}]
        dcs = [{"_name": "dc01", "_domain": "ad.example.mil", "_ok": True,
                "data": {"errors": ["repadmin /showrepl: access denied"], "repl": []}}]
        r = ad_filters._repl_admin(dcs, doms, dt.datetime(2026, 11, 22))
        p = dbf.ad_db_payload({"dcs": [{"dc": "dc01", "domain": "ad.example.mil", "read": True}], "repl": r})
        self.assertEqual((p["repl_dcs"], p["repl_read"]), (2, 0))
        self.assertEqual([(d["dc"], d["repl_read"]) for d in p["dcs"]], [("dc01", False), ("dc02", False)])

    def test_6_site_lag_keeps_the_worst_of_one_link(self):
        def mut(ad):
            ad["repl"]["links"] = [dict(frm="dc01", src="SITEB0201\\dc01", to="dc02", nc="Configuration", last_success="2026-01-01T00:00:00Z", failures=4),
                                   dict(frm="dc01", src="SITEA0101\\dc01", to="dc02", nc="Configuration", last_success=ad["repl"]["now"], failures=0)]
        db = one_run(mut)
        self.assertEqual(db.db.execute("SELECT src, dst, status FROM ad_link").fetchall(), [("dc01", "dc02", "critical")])

    def test_8_a_run_whose_rows_failed_is_not_ok_in_the_history(self):
        def mut(ad):
            ad["repl"]["links"][0]["failures"] = "unknown"
        db = one_run(mut)
        self.assertEqual(db.db.execute("SELECT status FROM ad_run").fetchone()[0], "unknown")
        h = sp.AdDb(None, connect=lambda: db, mark="?").read()["hist"][-1]
        self.assertEqual(h["status"], "unknown")
        self.assertIn("could not make", h["why"])


@unittest.skipUnless(_browser(), "no google-chrome or chromium")
class CodexCBrowser1012(unittest.TestCase):
    """Codex's third pass on 1.101.2: the page's findings, in the browser."""
    answer = Browser1012.answer
    page = Browser1012.page
    READ = Browser1012.READ + "r.sent=window.__sent;"

    def test_7_failing_links_seen_are_shown_with_partial_coverage(self):
        db = one_run(lambda ad: ad["repl"].update(dcs=[dict(d, read=False) for d in ad["repl"]["dcs"]]))     # the one link failing seen, coverage 0
        a = live(db, Site(self))
        a.read_db()
        r = self.page([json.loads(a.answer()[1])], self.READ)
        self.assertTrue(any(n.startswith("Replication was not read in full in this run: Replication read in full on 0 of 3") and "The links below were seen" in n
                            for n in r["notes"]), r["notes"])
        self.assertEqual(len(r["links"]), 1)
        self.assertIn("dc01", r["links"][0])
        i = r["tiles"].index("Replication failing")
        self.assertEqual(r["tv"][i], "1 link")
        self.assertIn("and only Replication read in full on 0 of 3", r["tnotes"][i])

    def test_8_the_history_badge_of_a_run_whose_rows_failed(self):
        def mut(ad):
            ad["repl"]["links"][0]["failures"] = "unknown"
        db = one_run(mut, minutes_ago=30)
        one_run(db=db, job=9001)                                       # a good run after it
        a = live(db, Site(self))
        a.read_db()
        r = self.page([json.loads(a.answer()[1])], self.READ + "r.badges=q('#ad section:nth-of-type(4) tbody tr td:nth-child(2)');")
        self.assertEqual(r["badges"][:2], ["OK", "Unknown"])         # newest first: the good run, then the failed one

    def test_9_an_answer_that_cannot_be_drawn_is_a_failed_poll(self):
        a = self.answer(["ok"] * 3)
        a["poll"] = 15
        bad = {"db": {"on": True, "model": {"run": {}}}, "sw": {}}
        r = self.page([a, bad], self.READ, wait=20000)
        self.assertIn("Not live", r["live"])
        self.assertTrue(any("could not be read" in n for n in r["notes"]), r["notes"])
        self.assertEqual(len(r["dcs"]), 3)                             # the last good answer, kept
        r = self.page([a, bad, a], self.READ, wait=34000)               # asked again after it: Live again
        self.assertIn("Live", r["live"])
        self.assertNotIn("Not live", r["live"])
        self.assertEqual(r["sent"][:3], ["", "e1", "e1"])                 # the bad answer's tag never kept


class CodexD1012(unittest.TestCase):
    """Codex's fourth pass on 1.101.2 (5 P1): each finding's case."""

    def test_1_reader_dsa_replacement_keeps_the_worst_link(self):
        import ad_filters
        import site_ad_reader
        now, ad = ad_run(0)
        reader = site_ad_reader.Reader({})
        self.addCleanup(reader.close)
        nc = "CN=Configuration,DC=corp,DC=test"
        dns = ["CN=NTDS Settings,CN=dc01,CN=Servers,CN=%s,CN=Sites,%s" % (s, nc) for s in ("SITEA0101", "SITEB0201")]
        reader.dsa_name = dict((dn.lower(), "dc01") for dn in dns)
        links = [reader._link(nc, {"partner_dn": dn, "last_success": "2026-01-01T00:00:00Z" if i == 0 else ad["repl"]["now"],
                                  "last_attempt": ad["repl"]["now"], "failures": 4 if i == 0 else 0, "result": 8524 if i == 0 else 0})
                 for i, dn in enumerate(dns)]
        self.assertEqual([ln["partner"] for ln in links], ["dc01", "dc01"])
        ad["repl"] = ad_filters._repl_reader([{"in_ad": True, "name": "dc02", "bind": {"ok": True}, "repl": {"links": links}}], now, 60, "corp.test")
        self.assertEqual([ln["src"] for ln in ad["repl"]["links"]], [dn.lower() for dn in dns])
        p = dbf.ad_db_payload(ad, {}, {})
        self.assertEqual(len(p["links"]), 1)
        db = Lite()
        adb.write(db, now, 9000, 12, p)
        self.assertEqual(sorted(db.db.execute("SELECT src, status FROM ad_link").fetchall()), [("dc01", "critical")])

    def test_2_failed_partition_is_an_incomplete_read(self):
        import ad_filters
        now, ad = ad_run(0)
        dcs = [{"in_ad": True, "name": d["dc"], "bind": {"ok": True}, "repl": {"links": []}} for d in ad["dcs"]]
        dcs[1]["repl"] = {"links": [{"partner": "dc01", "nc": "DC=corp,DC=test", "last_success": ad["repl"]["now"]}],
                           "failed": ["CN=Configuration,DC=corp,DC=test"]}
        ad["repl"] = ad_filters._repl_reader(dcs, now, 60, "corp.test")
        self.assertEqual(ad["repl"]["dcs"][1]["partial"], ["Configuration: query failed"])
        self.assertEqual(len(ad["repl"]["links"]), 1)
        p = dbf.ad_db_payload(ad, {}, {})
        self.assertEqual([(d["dc"], d["repl_read"]) for d in p["dcs"]], [("dc01", True), ("dc02", False), ("dc03", True)])
        self.assertEqual((p["repl_dcs"], p["repl_read"]), (3, 2))

    def test_3_the_portal_uses_only_saved_node_ids(self):
        from unittest.mock import patch
        a = live(Lite(), Site(self))
        with patch.object(sp.site_ad_swis, "from_live", return_value={}) as read:
            a._swis({"dcs": [{"dc": "dc01", "node_id": 101}]})
            self.assertEqual(read.call_args[0][0], [{"dc": "dc01", "node_id": 101}])
            self.assertTrue(read.call_args[0][1]["by_node_id"])

    def test_4_source_site_is_an_attribute(self):
        import ad_filters
        now, ad = ad_run(0)
        doms = [{"data": {"dns": "corp.test", "dcs": [{"name": n, "site": "SITEA0101"} for n in ("dc01", "dc02")]}}]
        dcs = [{"_name": n, "_domain": "corp.test", "_ok": True, "data": {"site": "SITEA0101", "repl": []}} for n in ("dc01", "dc02")]
        dcs[1]["data"]["repl"] = [{"source": "dc01", "source_site": "SITEB0201", "nc": "CN=Configuration,DC=corp,DC=test",
                                    "last_success": ad["repl"]["now"], "failures": 0}]
        r = ad_filters._repl_admin(dcs, doms, now)
        self.assertEqual(len(r["dcs"]), 2)
        self.assertEqual(r["links"][0]["src_id"], "dc01")
        self.assertTrue(all(d["read"] for d in r["dcs"]))

    def test_5_schema_two_requires_recovery(self):
        from unittest.mock import Mock
        self.assertEqual(adb.SCHEMA_VERSION, 4)
        for version in (1, 2, 3):
            for create in (False, True):
                conn = Mock()
                cur = conn.cursor.return_value
                cur.fetchall.return_value = [(t,) for t in adb.TABLES]
                cur.fetchone.side_effect = [("schema_version",), (version,)]
                with self.assertRaises(RuntimeError) as err:
                    adb.schema(conn, create)
                self.assertEqual(str(err.exception), "the AD tables are version %d, from a build before 1.101.2 shipped: drop ad_run, ad_dc and ad_link "
                                 "(and its schema_version row) and the next run makes them again" % version)
                conn.commit.assert_not_called()


class Joint11012(unittest.TestCase):
    """1.101.2 + 1.102.0: the network map's AD layer (ad_repl_from_db) read from 1.101.2's tables (schema 4)."""

    def test_1_the_layer_reads_portal_ad_templates_run(self):
        def other_forest(ad):
            ad["repl"]["dcs"] = [dict(d, site="SITC0301") for d in ad["repl"]["dcs"]]
        db = one_run(template_id=12, minutes_ago=30)
        one_run(other_forest, template_id=34, db=db, job=9001)          # newer, another AD template
        c12, why12 = sp.ad_repl_from_db(lambda: db, mark="?", template_id=12)
        cnew, _ = sp.ad_repl_from_db(lambda: db, mark="?")
        self.assertEqual(why12, "")
        self.assertEqual(sorted(c12["dcs"]), ["SITEA0101", "SITEB0201"])  # portal_ad_template's run, as /portal/ad reads it
        self.assertEqual(sorted(cnew["dcs"]), ["SITC0301"])             # none named: the newest run (any template)

    def test_2_no_dc_replication_read_draws_no_layer(self):
        import site_ad_layer as al

        def unread(ad):
            ad["repl"]["dcs"] = [dict(d, read=False) for d in ad["repl"]["dcs"]]
            ad["repl"]["links"] = []
        db = one_run(unread)
        c, why = sp.ad_repl_from_db(lambda: db, mark="?", template_id=12)
        self.assertEqual((why, c["read"]), ("", 0))
        lay = al.layer(c, {"sites": ["SITEA0101", "SITEB0201"], "nodes": []}, source="db")
        self.assertTrue(lay["why"].startswith("no DC's replication could be read"), lay)
        self.assertNotIn("links", lay)                                  # nothing drawn as "good" when nothing was read
        ok, _ = sp.ad_repl_from_db(lambda: one_run(), mark="?", template_id=12)
        self.assertEqual(ok["read"], 3)                                 # read: the layer as before


class FableP2_11012(unittest.TestCase):
    """Fable's final check of 1.101.2: two P2s."""

    def test_1_an_application_partition_is_not_a_second_domain(self):
        import site_ad_ident as ident
        one = {"dcs": [{"dc": "dc01", "domain": "ad.example.mil"}],
               "repl": {"links": [{"nc": "DC=ad,DC=example,DC=mil"}, {"nc": "DC=TAPI3Directory,DC=ad,DC=example,DC=mil"},
                                  {"nc": "DC=DomainDnsZones,DC=ad,DC=example,DC=mil"}]}}
        self.assertEqual(ident.unsupported(one), "")
        two = dict(one, dcs=one["dcs"] + [{"dc": "dc09", "domain": "other.example.mil"}])
        self.assertIn("other.example.mil", ident.unsupported(two))        # a second domain still refused
        other_nc = {"repl": {"links": [{"nc": "DC=ad,DC=example,DC=mil"}, {"nc": "DC=other,DC=test"}]}}
        self.assertIn("partner NC: DC=other,DC=test", ident.unsupported(other_nc))

    def test_2_long_link_keys_are_cut_to_their_columns(self):
        def long_src(ad):
            # a source that resolves to no DC and has no GUID: its key is "dsa:<its NTDS DN>"
            ad["repl"]["links"][0]["src_id"] = "dsa:cn=ntds settings,cn=" + "x" * 300 + ",cn=servers,cn=sitea0101,cn=sites,cn=configuration,dc=ad,dc=example,dc=mil"
        db = one_run(long_src)
        rows = db.db.execute("SELECT LENGTH(src), LENGTH(dst), LENGTH(frm), LENGTH(to_dc), LENGTH(domain) FROM ad_link").fetchall()
        self.assertTrue(rows)
        for r in rows:
            self.assertTrue(r[0] <= 255 and all(n <= 191 for n in r[1:]), r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
