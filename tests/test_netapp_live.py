#!/usr/bin/env python3
"""The portal's NetApp page (/portal/netapp): NetApp - refresh volume list's run in the report database (the payload
plugins/filter/ontap_live_filters.py makes, the writer roles/ontap_report/library/site_ontap_live_db.py, the growth math
plugins/module_utils/site_ontap_live.py), the portal's reader (roles/site_portal_stack/files/netapp_live.py), the page and
its answer (/portal/netapp, /api/netapp: the tab only with data, Expand... per volume with the survey's own choice and Check
preselected, the button off with its reason), the Run dialog's Job type (a template that asks it at launch) and the
strict CSP (no inline script). SQLite stands in for MySQL. Made-up data: tests/netapp/live_fixture.py. Placeholders only.
  python3 tests/test_netapp_live.py"""
import datetime
import json
import os
import re
import sys
import tempfile
import shutil
import subprocess
import threading
import types
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FILES = os.path.join(ROOT, "roles", "site_portal_stack", "files")
sys.path[:0] = [os.path.join(ROOT, "plugins", "module_utils"), os.path.join(ROOT, "plugins", "filter"), FILES, HERE,
                os.path.join(HERE, "netapp"), os.path.join(HERE, "site_portal")]
import site_report_db  # noqa: E402
import site_ontap_live as L  # noqa: E402
import ansible.module_utils  # noqa: E402
for _n, _m in (("site_report_db", site_report_db), ("site_ontap_live", L)):
    sys.modules.setdefault("ansible.module_utils." + _n, _m)
    setattr(ansible.module_utils, _n, _m)
sys.path.insert(0, os.path.join(ROOT, "roles", "ontap_report", "library"))
import site_ontap_live_db as ldb  # noqa: E402
import ontap_live_filters as lf  # noqa: E402
import ontap_expand_filters as ef  # noqa: E402
import live_fixture as fx  # noqa: E402
import site_portal as sp  # noqa: E402
import netapp_live as nl  # noqa: E402

GB = 1024 ** 3


def sqlite_schema(sql):
    sql = sql.replace("CREATE TABLE IF NOT EXISTS", "CREATE TABLE")
    sql = sql.replace("run_id BIGINT NOT NULL AUTO_INCREMENT", "run_id INTEGER")
    sql = re.sub(r", KEY \w+ \([^)]*\)", "", sql)
    return sql.split(" ENGINE=")[0]


class Lite(object):
    """A SQLite session that takes the writer's MySQL statements (%s marks; ON DUPLICATE KEY dropped)."""

    def __init__(self, tables=True):
        import sqlite3
        self.db = sqlite3.connect(":memory:", detect_types=0, check_same_thread=False)
        if tables:
            for c in ldb.CREATE:
                self.db.execute(sqlite_schema(c))

    def cursor(self):
        db = self.db

        class C(object):
            lastrowid = None
            rows = []

            def _sql(self, sql):
                sql = re.sub(r" ON DUPLICATE KEY UPDATE .*$", "", sql)
                if sql.startswith("START TRANSACTION"):
                    return "SELECT 1"
                return sql.replace("%s", "?")

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

            def fetchone(self):
                return self.rows[0] if self.rows else None
        return C()

    def commit(self):
        self.db.commit()

    def rollback(self):
        pass

    def close(self):
        pass


def payload(hours_ago=0.0, new=True, health=True, perf=True):
    lst, found = fx.listed(hours_ago, new=new)
    texts = ef.ontap_expand_choices(lst["choices"], found)["choices"]
    return lf.ontap_live_payload({"list": lst, "choices": texts, "found": found, "collected": fx.collected() if (health or perf) else {},
                                  "health": health, "perf": perf, "opts": fx.OPTS}), texts


def fill(db=None, hours=(48, 36, 24, 12, 6, 3, 1, 0), now=None):
    """Runs of the list hours ago (oldest first); vol_new01 only in the last two hours."""
    db = db or Lite()
    now = now or fx.at(0)
    for h in hours:
        p, _t = payload(h, new=h <= 2)
        rid, _v, _a, _p, _parts = ldb.write(db, now - datetime.timedelta(hours=h), 9000 + int(100 - h), 58, p, "NetApp - expand a volume",
                                            "ontap_expand_volume", fx.OPTS)
        ldb.rate_all(db, now - datetime.timedelta(hours=h), rid, fx.OPTS)
    db.commit()
    return db


class Growth(unittest.TestCase):
    """site_ontap_live: the slope from the database's sums, the days, the statuses."""

    def sums(self, pts, now_s):
        x = [(t - now_s) / 86400.0 for t, _y in pts]
        y = [v for _t, v in pts]
        return (len(pts), min(t for t, _ in pts), max(t for t, _ in pts), sum(x), sum(y), sum(a * a for a in x), sum(a * b for a, b in zip(x, y)))

    def test_rate_is_the_least_squares_slope(self):
        now = 1_800_000_000
        pts = [(now - h * 3600, 100 + 12.0 * (24 - h) / 24.0) for h in (24, 18, 12, 6, 0)]
        g, why = L.rate(*self.sums(pts, now))
        self.assertAlmostEqual(g, 12.0, places=6)
        self.assertEqual(why, "")

    def test_no_rate_before_3_points_or_6_hours(self):
        now = 1_800_000_000
        g, why = L.rate(*self.sums([(now - 3600, 1.0), (now, 2.0)], now))
        self.assertIsNone(g)
        self.assertEqual(why, "rate after 3 points over 6 h (2 so far, 1 h)")
        g, why = L.rate(*self.sums([(now - 1800, 1.0), (now - 900, 2.0), (now, 3.0)], now))
        self.assertIsNone(g)                                    # three points over 30 minutes: noise, not a rate
        self.assertIn("(3 so far, 0.5 h)", why)
        self.assertIsNotNone(L.rate(*self.sums([(now - 7 * 3600, 1.0), (now - 3600, 2.0), (now, 3.0)], now))[0])

    def test_days_until(self):
        self.assertAlmostEqual(L.days_until(100 * GB, 10.0), 10.0)
        self.assertIsNone(L.days_until(100 * GB, -1.0))
        self.assertIsNone(L.days_until(100 * GB, None))
        self.assertEqual(L.days_until(0, 5.0), 0.0)

    def test_statuses_from_the_settings(self):
        o = fx.OPTS
        self.assertEqual(L.volume_status(96.0, None, o), "critical")
        self.assertEqual(L.volume_status(86.0, 500, o), "warning")
        self.assertEqual(L.volume_status(50.0, 5, o), "critical")              # under 85 %, but full within 7 days
        self.assertEqual(L.volume_status(50.0, 20, o), "warning")
        self.assertEqual(L.volume_status(50.0, None, o), "ok")
        self.assertEqual(L.aggr_status(91.0, 400, o), "critical")
        self.assertEqual(L.aggr_status(70.0, 0.0, o), "critical")              # at the expand job's limit now


class Payload(unittest.TestCase):
    """ontap_live_filters: the list's entries with their own survey text (by its key, never by position), the parts."""

    def test_each_entry_gets_its_own_choice_by_key(self):
        p, texts = payload()
        offered = [v for v in p["volumes"] if v["offered"]]
        self.assertEqual(len(offered), len(texts))
        for v in offered:
            k = ef.X.parse_choice(v["choice"])
            self.assertEqual((k["address"], k["svm"], k["volume"], k["lun"]), (v["address"], v["svm"], v["volume"], v["lun"]))
        ds = [v for v in p["volumes"] if v["volume"] == "vol_ds03"][0]
        self.assertEqual((ds["datastore"], ds["ds_kind"]), ("DS-SITEA-03", "VMFS"))       # found[] is in the list's own order
        self.assertEqual((ds["full_src"], ds["thick"]), ("datastore", True))

    def test_a_choice_cut_by_max_choices_is_said(self):
        lst, found = fx.listed()
        texts = ef.ontap_expand_choices(lst["choices"], found, 3)["choices"]
        p = lf.ontap_live_payload({"list": lst, "choices": texts, "found": found, "opts": fx.OPTS})
        cut = [v for v in p["volumes"] if not v["offered"] and not v["root"] and v["why"].startswith("not in the survey")]
        self.assertTrue(cut)
        self.assertEqual(p["parts"], {})                          # health and perf off: no parts

    def test_excluded_volumes_keep_their_reason_and_sizes(self):
        p, _t = payload()
        dp = [v for v in p["volumes"] if v["volume"] == "vol_ds02_dst"][0]
        self.assertFalse(dp["offered"])
        self.assertIn("SnapMirror destination", dp["why"])
        self.assertEqual(dp["size"], 2048 * GB)
        self.assertTrue([v for v in p["volumes"] if v["volume"] == "vol0"][0]["root"])

    def test_health_parts(self):
        p, _t = payload()
        sm = p["parts"]["snapmirror"]
        self.assertEqual((sm["counts"]["all"], sm["counts"]["broken"], sm["counts"]["lagging"], sm["counts"]["transferring"]), (5, 1, 1, 1))
        self.assertEqual([(r["word"], r["st"]) for r in sm["rows"][:3]], [("Broken off", "critical"), ("Lagging", "warning"), ("Transferring", "ok")])
        lag = sm["rows"][1]
        self.assertEqual((lag["sched"], lag["per"]), ("hourly", 3600))
        disks = {(d["cluster"], d["node"]): d for d in p["parts"]["disks"]["rows"]}
        c201 = disks[("cluster2", "cluster2-01")]
        self.assertEqual((c201["st"], c201["failed"], c201["rebuild"], c201["spares"]), ("critical", 1, 1, 0))
        self.assertIn("no spare disk left", c201["why"])
        ems = p["parts"]["ems"]
        self.assertEqual(ems["rows"][0]["ev"], "wafl.vol.autoSize.fail")                  # newest first
        self.assertEqual(ems["rows"][0]["n"], 2)                                           # the same event on the same node once
        self.assertNotIn("old.event", [r["ev"] for r in ems["rows"]])                      # older than ontap_ems_hours
        self.assertNotIn("secd.ldap.noServers", [r["ev"] for r in ems["rows"]])            # no SVM has an LDAP client there
        self.assertEqual(ems["counts"]["left_out"], 1)
        cl = {c["name"]: c for c in p["parts"]["clusters"]["rows"]}
        self.assertEqual((cl["cluster1"]["st"], cl["cluster2"]["st"]), ("ok", "critical"))
        self.assertEqual(cl["cluster1"]["ontap"], "9.14.1P3")
        perf = p["parts"]["perf"]
        top = sorted(perf["rows"], key=lambda r: -r["lat"])[0]
        self.assertEqual((top["vol"], top["lat"], top["st"]), ("vol_ds07", 22.4, "critical"))
        self.assertNotIn(("vol_ds03", ""), [(r["vol"], r["lun"]) for r in perf["rows"]])   # a volume of LUNs: its LUNs are the rows
        self.assertEqual([r["ds"] for r in perf["rows"] if r["vol"] == "vol_ds03"], ["DS-SITEA-03"])
        self.assertEqual(perf["counts"]["sample"], "PT15S")

    def test_a_cluster_not_read_is_said_in_every_part(self):
        col = fx.collected()
        col["clusters"][1] = {"address": fx.C2, "reachable": False, "error": "no answer on port 443"}
        lst, found = fx.listed()
        p = lf.ontap_live_payload({"list": lst, "choices": [], "found": found, "collected": col, "health": True, "perf": True, "opts": fx.OPTS})
        for part in ("snapmirror", "nodes", "disks", "ems", "perf"):
            self.assertIn("%s: no answer on port 443" % fx.C2, p["parts"][part]["errors"])
        self.assertEqual([c["st"] for c in p["parts"]["clusters"]["rows"] if c["name"] == fx.C2], ["critical"])
        col = fx.collected()
        col["clusters"][0]["data"]["nodes"] = {"records": [], "error": "HTTP 404: API not found"}
        p = lf.ontap_live_payload({"list": lst, "choices": [], "found": found, "collected": col, "health": True, "opts": fx.OPTS})
        c1 = [c for c in p["parts"]["clusters"]["rows"] if c["name"] == "cluster1"][0]
        self.assertEqual((c1["st"], c1["why"]), ("unknown", "its nodes were not read"))       # never "healthy" unread

    def test_period_of_a_schedule(self):
        self.assertEqual(lf.period_seconds({"cron": {"minutes": [5]}}), 3600)
        self.assertEqual(lf.period_seconds({"cron": {"minutes": [0], "hours": [0, 12]}}), 43200)
        self.assertEqual(lf.period_seconds({"interval": "PT15M"}), 900)
        self.assertIsNone(lf.period_seconds(None))


class Writer(unittest.TestCase):
    """site_ontap_live_db: the run, the volumes, the aggregates, the points, the growth, the parts, the pruning."""

    def test_round_trip_with_growth(self):
        db = fill()
        q = lambda s, *a: db.db.execute(s, a).fetchall()        # noqa: E731
        rid = q("SELECT MAX(run_id) FROM ontap_live_run")[0][0]
        self.assertEqual(q("SELECT COUNT(*) FROM ontap_live_run")[0][0], 8)
        g = dict(((r[0], r[1]), r[2:]) for r in q("SELECT volume, lun, growth_gb_day, days_full, rate_why, status FROM ontap_live_volume WHERE run_id = ?", rid))
        self.assertAlmostEqual(g[("vol_ds03", "/vol/vol_ds03/lun1")][0], 18.2, places=3)
        self.assertAlmostEqual(g[("vol_ds03", "/vol/vol_ds03/lun1")][1], (2048 - 1974) / 18.2, places=2)
        self.assertEqual(g[("vol_ds03", "/vol/vol_ds03/lun1")][3], "critical")
        self.assertAlmostEqual(g[("vol_ds01", "/vol/vol_ds01/lun2")][0], 22.5 / 2, places=3)  # each LUN its own rate (its datastore's used)
        self.assertIsNone(g[("vol_ds05", "")][1])                                            # shrinking: not filling
        self.assertLess(g[("vol_ds05", "")][0], 0)
        self.assertIsNone(g[("vol_new01", "/vol/vol_new01/lun1")][0])                        # 2 points in 1 h: no rate yet
        self.assertEqual(g[("vol_new01", "/vol/vol_new01/lun1")][2], "rate after 3 points over 6 h (2 so far, 1 h)")
        a = dict((r[0], r[1:]) for r in q("SELECT aggr, growth_gb_day, days_limit, days_full, status FROM ontap_live_aggr WHERE run_id = ?", rid))
        self.assertAlmostEqual(a["aggr2_c2n2"][0], 28, places=2)
        self.assertAlmostEqual(a["aggr2_c2n2"][1], 21.6 * 1024 * (97 - 91.2) / 100.0 / 28, delta=0.05)
        self.assertEqual(a["aggr2_c2n2"][3], "critical")
        parts = [r[0] for r in q("SELECT part FROM ontap_live_part WHERE run_id = ? ORDER BY part", rid)]
        self.assertEqual(parts, ["clusters", "disks", "ems", "nodes", "perf", "snapmirror"])
        run = q("SELECT expand_template, expand_question, volumes, offered FROM ontap_live_run WHERE run_id = ?", rid)[0]
        self.assertEqual(run[:2], ("NetApp - expand a volume", "ontap_expand_volume"))
        obj = q("SELECT pt_obj FROM ontap_live_volume WHERE run_id = ? AND lun = '/vol/vol_ds01/lun1'", rid)[0][0]
        self.assertEqual(q("SELECT COUNT(*) FROM ontap_live_point WHERE kind = 'volume' AND obj = ?", obj)[0][0], 8)   # one a run, each LUN
        src = dict(((r[0], r[1]), r[2]) for r in q("SELECT volume, lun, full_src FROM ontap_live_volume WHERE run_id = ?", rid))
        self.assertEqual((src[("vol_ds03", "/vol/vol_ds03/lun1")], src[("vol_new01", "/vol/vol_new01/lun1")], src[("vol_home", "")]),
                         ("datastore", "LUN", "volume"))

    def test_p2_a_history_per_source_and_uuid(self):
        """vCenter lost: the LUN's blocks written start a history of their own - no jump inside one, no false rate"""
        db = fill(hours=(48, 24, 12, 6))
        now = fx.at(0)
        lst, found = fx.listed(0, new=False)
        texts = ef.ontap_expand_choices(lst["choices"], found)["choices"]
        p = lf.ontap_live_payload({"list": lst, "choices": texts, "found": [{}] * len(found), "opts": fx.OPTS})     # no vCenter
        rid = ldb.write(db, now, 9200, 58, p, EXPAND, "ontap_expand_volume", fx.OPTS)[0]
        ldb.rate_all(db, now, rid, fx.OPTS)
        r = db.db.execute("SELECT full_src, growth_gb_day, rate_why FROM ontap_live_volume WHERE run_id = ? AND volume = 'vol_ds03'", (rid,)).fetchone()
        self.assertEqual(r[0], "LUN")
        self.assertIsNone(r[1])                                                      # a new history: one point, no rate
        self.assertIn("(1 so far", r[2])

    def test_prune(self):
        db = fill()
        now = fx.at(0)
        rid = db.db.execute("SELECT MAX(run_id) FROM ontap_live_run").fetchone()[0]
        out = ldb.prune(db, now, rid, 1, 10)
        self.assertEqual(out["runs"], 4)                                                     # 48, 36, 24 and 12 h ago
        self.assertEqual(db.db.execute("SELECT COUNT(*) FROM ontap_live_volume WHERE run_id NOT IN (SELECT run_id FROM ontap_live_run)").fetchone()[0], 0)
        self.assertEqual(db.db.execute("SELECT COUNT(*) FROM ontap_live_point WHERE at_s < ?", (ldb.epoch(now) - 86400,)).fetchone()[0], 0)
        self.assertEqual(ldb.prune(db, now + datetime.timedelta(days=30), rid, 0, 1)["runs"], 3)    # the newest is always kept
        self.assertEqual(db.db.execute("SELECT COUNT(*) FROM ontap_live_run").fetchone()[0], 1)

    def test_the_statements_in_the_doc_are_the_modules(self):
        doc = open(os.path.join(ROOT, "docs", "REPORT_DATABASE.md")).read()
        for part in ldb.ddl().split("\n\n"):
            self.assertIn(part, doc, "docs/REPORT_DATABASE.md: the statement differs from site_ontap_live_db.ddl()")


# ---- the portal ------------------------------------------------------------------------------------------------------------
JDOE = "CN=DOE.JANE.Q.1234567890,OU=CONTRACTOR,OU=PKI,O=U.S. Government,C=US"
CN = "DOE.JANE.Q.1234567890"
EXPAND = "NetApp - expand a volume"


def cfg_for(tmp, **env):
    os.makedirs(os.path.join(tmp, "data"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "reports"), exist_ok=True)
    with open(os.path.join(tmp, "a.json"), "w") as fh:
        json.dump({"actions": {}}, fh)
    e = {"PORTAL_DATA": os.path.join(tmp, "data"), "PORTAL_REPORTS": os.path.join(tmp, "reports"), "PORTAL_ACTIONS": os.path.join(tmp, "a.json"),
         "PORTAL_AAP_URL": "http://127.0.0.1:9", "PORTAL_AAP_TOKEN": "x", "PORTAL_TIME_ZONE": ""}
    e.update(env)
    return sp.Config(e)


def expand_action(texts, ask=True, who=None):
    """the expand template as discovery makes it: its survey's Volume question with the list's current choices"""
    return {"name": "jt_58", "title": EXPAND, "aap": {"type": "job", "name": EXPAND}, "tid": 58, "discovered": True, "who": who,
            "vars": {"ontap_expand_volume": {"kind": "choice", "values": list(texts), "label": "Which volume to grow?", "required": True},
                     "ontap_expand_size": {"kind": "text", "max": 20, "label": "Size", "required": False}},
            "limit_from": "", "approval": False, "sig": "s1", "job_type": {"ask": ask, "default": "run"}}


def fake_portal(actions=None, found=None):
    return types.SimpleNamespace(actions=actions or {}, found=found or {"skipped": [], "no_execute": []})


def live_for(test, db=None, actions=None, found=None, **env):
    tmp = tempfile.mkdtemp()
    test.addCleanup(shutil.rmtree, tmp, True)
    cfg = cfg_for(tmp, **env)
    db = db or fill()
    n = sp.NetappLive(cfg, sp.Log(None), fake_portal(actions, found), db=nl.Database(lambda: db, mark="?"))
    n.refresh()
    return n, cfg


def newest_texts():
    return payload(0)[1]


class Reader(unittest.TestCase):
    """netapp_live.Database: the newest run, a row per LUN and per NFS volume, worst first; the tab only with data."""

    def test_model(self):
        m = nl.Database(lambda: fill(), mark="?").read()
        names = [v["name"] for v in m["volumes"]]
        self.assertEqual(names[0], "vol_ds03")                                    # critical and soonest full
        self.assertIn("vol_ds01/lun1", names)
        self.assertIn("vol_ds01/lun2", names)                                    # each LUN its own row
        self.assertNotIn("vol0", names)
        self.assertEqual(m["left_out"], {"roots": 1, "small": 0})
        d3 = m["volumes"][0]
        self.assertEqual((d3["src"], d3["full_text"], d3["thick"], d3["ds"]), ("datastore used", "datastore 96% used", True, "DS-SITEA-03"))
        self.assertTrue(d3["offered"])
        self.assertEqual(m["aggrs"][0]["a"], "aggr2_c2n2")
        self.assertEqual(set(m["parts"]), {"snapmirror", "nodes", "disks", "ems", "clusters", "perf"})

    def test_no_table_no_run_no_tab(self):
        bare = Lite(tables=False)
        self.assertEqual(nl.Database(lambda: bare, mark="?").read(), {"empty": nl.NO_TABLES})
        self.assertFalse(nl.Database(lambda: bare, mark="?").has_data())
        self.assertFalse(nl.Database(lambda: Lite(), mark="?").has_data())
        self.assertTrue(nl.Database(lambda: fill(hours=(0,)), mark="?").has_data())


class Expand(unittest.TestCase):
    """Expand... per LUN or volume: the survey's own current choice (key included) and Check preselected; off with its reason."""

    def model(self, **kw):
        n, _cfg = live_for(self, **kw)
        code, body, _tag = n.answer(CN)
        self.assertEqual(code, 200)
        return json.loads(body)["model"]

    def test_the_button_carries_the_current_choice_and_check(self):
        texts = newest_texts()
        m = self.model(actions={"jt_58": expand_action(texts)})
        self.assertTrue(m["expand"]["on"])
        v = [x for x in m["volumes"] if x["name"] == "vol_ds01/lun2"][0]
        href = v["x"][0]["href"]
        self.assertTrue(href.startswith("/portal/actions/jt_58?"), href)
        q = urllib.parse.parse_qs(href.split("?", 1)[1])
        want = [t for t in texts if "|vol_ds01|/vol/vol_ds01/lun2|" in t][0]
        self.assertEqual(q["ontap_expand_volume"], [want])                       # the exact choice text, key included
        self.assertEqual(q["portal-job-type"], ["check"])
        dp = [x for x in m["volumes"] if x["name"] == "vol_ds02_dst"][0]
        self.assertEqual(dp["x"], [])
        self.assertIn("Not offered: DP volume", dp["off"])

    def test_the_choice_as_the_survey_has_it_now(self):
        texts = newest_texts()
        moved = [t.replace("% free", "% free ") if "vol_ds03" in t else t for t in texts]        # the survey rewritten since (its text changed, its key not)
        m = self.model(actions={"jt_58": expand_action(moved)})
        v = [x for x in m["volumes"] if x["name"] == "vol_ds03"][0]
        q = urllib.parse.parse_qs(v["x"][0]["href"].split("?", 1)[1])
        self.assertEqual(q["ontap_expand_volume"], [[t for t in moved if "vol_ds03" in t][0]])
        gone = self.model(actions={"jt_58": expand_action([t for t in texts if "vol_ds03" not in t])})
        v = [x for x in gone["volumes"] if x["name"] == "vol_ds03"][0]
        self.assertEqual(v["x"], [])
        self.assertIn("not in the job's current list", v["off"])

    def test_p1_the_same_path_made_again_is_not_offered(self):
        texts = newest_texts()
        again = [re.sub(r"\|([0-9a-f-]+)\]$", "|0000aaaa-0000-0000-0000-000000000000]", t) if "|vol_ds03|" in t else t for t in texts]
        m = self.model(actions={"jt_58": expand_action(again)})
        v = [x for x in m["volumes"] if x["name"] == "vol_ds03"][0]
        self.assertEqual(v["x"], [])
        self.assertTrue(v["dis"])
        self.assertEqual(v["off"], "the list changed - wait for the next refresh")

    def test_p2_a_template_fixed_to_run_has_no_expand(self):
        m = self.model(actions={"jt_58": expand_action(newest_texts(), ask=False)})
        v = [x for x in m["volumes"] if x["name"] == "vol_ds03"][0]
        self.assertEqual(v["x"], [])
        self.assertEqual(v["off"], "turn on Prompt on launch for Job type on NetApp - expand a volume")
        self.assertFalse(m["expand"]["on"])
        a = expand_action(newest_texts(), ask=False)
        a["job_type"]["default"] = "check"                                         # fixed to Check: allowed, nothing to ask
        v = [x for x in self.model(actions={"jt_58": a})["volumes"] if x["name"] == "vol_ds03"][0]
        self.assertEqual(len(v["x"]), 1)
        self.assertNotIn("portal-job-type", v["x"][0]["href"])

    def test_off_with_its_reason(self):
        m = self.model(found={"skipped": [], "no_execute": [EXPAND]})
        v = [x for x in m["volumes"] if x["name"] == "vol_ds03"][0]
        self.assertTrue(v["dis"])
        self.assertEqual(v["off"], "needs Execute on NetApp - expand a volume")
        m = self.model(actions={"jt_58": expand_action(newest_texts(), who=["SOMEONE.ELSE"])})
        self.assertIn("not on the list of people", m["expand"]["why"])
        m = self.model()
        self.assertIn("needs Execute on NetApp - expand a volume", m["expand"]["why"])

    def test_no_login_and_no_tables(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        n = sp.NetappLive(cfg_for(tmp), sp.Log(None), fake_portal())
        self.assertEqual(json.loads(n.answer(CN)[1])["message"], nl.NO_LOGIN)
        self.assertFalse(n.check_tab())
        n, cfg = live_for(self, db=Lite(tables=False))
        self.assertEqual(json.loads(n.answer(CN)[1])["message"], nl.NO_TABLES)
        self.assertFalse(n.check_tab())
        n, cfg = live_for(self)
        self.assertTrue(n.check_tab())
        self.assertIn('href="/portal/netapp"', sp.shell(cfg, "x", "", CN))
        cfg.netapp_tab = False
        self.assertNotIn('href="/portal/netapp"', sp.shell(cfg, "x", "", CN))


class RunDialog(unittest.TestCase):
    """The portal's Run dialog: Job type for a template that asks it at launch, Check preselected by the address; launched with it."""

    def portal(self, action):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        p = sp.Portal(cfg_for(tmp))
        p.aap.ready = ""
        p.found_ready, p.found_at = True, sp.time.time() + 3600
        p.actions = {"jt_58": action}
        return p

    def test_dialog_preselects(self):
        texts = newest_texts()
        p = self.portal(expand_action(texts))
        q = {"ontap_expand_volume": [texts[2]], "portal-job-type": ["check"]}
        h = sp.action_page(p.cfg, p, CN, "jt_58", q, embed=True)
        sel = re.search(r'<option value="([^"]*)" selected>', h)
        self.assertEqual(sp.html.unescape(sel.group(1)), texts[2])
        self.assertIn('name="portal-job-type" value="check" checked', h)
        self.assertNotIn('value="run" checked', h)
        self.assertNotIn("is required", h)
        h = sp.action_page(p.cfg, p, CN, "jt_58", {}, embed=True)
        self.assertIn('value="run" checked', h)                                  # the template's own Job type otherwise
        p.actions["jt_58"]["job_type"] = {"ask": False, "default": "run"}
        self.assertNotIn("portal-job-type", sp.action_page(p.cfg, p, CN, "jt_58", q, embed=True))

    def test_launch_sends_the_job_type(self):
        texts = newest_texts()
        p = self.portal(expand_action(texts))
        sent = []
        p.same_template = lambda a: ""
        p.aap.call = lambda m, path, body=None: (sent.append((m, path, body)) or (201, {"job": 77, "id": 77}))
        code, out = p.launch(CN, "192.0.2.5", "jt_58", {"ontap_expand_volume": texts[0]}, "s1", "check")
        self.assertEqual(code, 201, out)
        self.assertEqual(sent[-1][2]["job_type"], "check")
        self.assertEqual(sent[-1][2]["extra_vars"], {"ontap_expand_volume": texts[0]})
        self.assertEqual(p.launch(CN, "192.0.2.5", "jt_58", {"ontap_expand_volume": texts[0]}, "s1", "drop")[0], 400)
        p.actions["jt_58"]["job_type"] = {"ask": False, "default": "run"}
        p.launch(CN, "192.0.2.5", "jt_58", {"ontap_expand_volume": texts[1]}, "s1", "check")
        self.assertNotIn("job_type", sent[-1][2])                                # a template that does not ask: never sent


class Page(unittest.TestCase):
    """/portal/netapp and /api/netapp: the frame under the strict CSP (no inline script or style), the answer with an ETag."""

    def test_routes(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        cfg = cfg_for(tmp)
        p = sp.Portal(cfg)
        p.aap.ready = ""
        sp.sys.stdout = open(os.devnull, "w")
        self.addCleanup(setattr, sp.sys, "stdout", sys.__stdout__)
        db = fill()
        orig = sp.NetappLive.__init__

        def init(self_, c, log, portal=None, db_=None):
            orig(self_, c, log, portal, db=nl.Database(lambda: db, mark="?"))
        sp.NetappLive.__init__ = init
        self.addCleanup(setattr, sp.NetappLive, "__init__", orig)
        srv = sp.PortalServer(("127.0.0.1", 0), sp.make_handler(cfg, p, os.path.join(FILES, "static")))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = "http://127.0.0.1:%d" % srv.server_address[1]

        def get(u, h=None):
            hh = {"X-SSL-Client-Verify": "SUCCESS", "X-SSL-Client-S-DN": JDOE}
            hh.update(h or {})
            try:
                with urllib.request.urlopen(urllib.request.Request(base + u, headers=hh), timeout=20) as f:
                    return f.status, f.read().decode(), dict(f.headers)
            except urllib.error.HTTPError as e:
                return e.code, e.read().decode(), dict(e.headers)
        code, page, h = get("/netapp")
        self.assertEqual(code, 200)
        self.assertIn("script-src 'self';", h["Content-Security-Policy"])
        self.assertNotIn("<script>", page)
        self.assertNotIn(" style=", page)
        self.assertRegex(page, r'<script src="/portal/static/netapp-page.js\?v=[0-9a-f]{10}" defer></script>')
        self.assertIn('<dialog class="dlg" id="dlg"', page)
        for n in ("netapp-page.js", "netapp-page.css"):
            code, body, _h = get("/static/" + n)
            self.assertEqual(code, 200, n)
        js = open(os.path.join(FILES, "static", "netapp-page.js")).read()
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("eval(", js)
        for _ in range(50):
            code, body, h = get("/api/netapp")
            if "model" in json.loads(body):
                break
            sp.time.sleep(0.1)
        self.assertIn("model", json.loads(body))
        self.assertEqual(get("/api/netapp", {"If-None-Match": h["ETag"]})[0], 304)
        self.assertIn('href="/portal/netapp"', get("/")[1])                     # the tab, now that the database has a run


def browser():
    for b in ("google-chrome", "chromium", "chromium-browser"):
        if shutil.which(b):
            return shutil.which(b)
    return None


def page_files(test, answer, theme="light", width=1280, js=""):
    """the page in a folder, the portal's answer stood in by a fetch stand-in; -> its path"""
    d = tempfile.mkdtemp()
    test.addCleanup(shutil.rmtree, d, True)
    tmp = tempfile.mkdtemp()
    test.addCleanup(shutil.rmtree, tmp, True)
    cfg = cfg_for(tmp)
    cfg.netapp_tab = True
    html = sp.netapp_page(cfg, CN, {}).replace("/portal/static/", "")
    for n in ("netapp-page.js", "netapp-page.css", "portal.css", "portal-theme.js", "portal.js"):
        shutil.copy(os.path.join(FILES, "static", n), d)
    open(os.path.join(d, "banner.css"), "w").close()
    with open(os.path.join(d, "stub.js"), "w") as fh:
        fh.write("(function(){var A=%s;var f0=window.fetch;window.fetch=function(u,o){if(String(u).indexOf('api/netapp')<0)return f0(u,o);"
                 "return Promise.resolve({status:200,ok:true,headers:{get:function(){return 'e1'}},json:function(){return Promise.resolve(A)}})}})();"
                 % json.dumps(answer))
    with open(os.path.join(d, "t.js"), "w") as fh:
        fh.write("setTimeout(function(){var r={};try{%s}catch(e){r.err=String(e)}document.getElementById('res').textContent=JSON.stringify(r)},1500);" % js)
    html = html.replace("<head>", '<head><script src="stub.js"></script>', 1).replace('<html lang="en"', '<html lang="en" data-theme="%s"' % theme, 1)
    html = html.replace("</body>", '<pre id="res" hidden></pre><script src="t.js"></script></body>', 1)
    with open(os.path.join(d, "p.html"), "w") as fh:
        fh.write(html)
    return os.path.join(d, "p.html")


def run_page(path, width, height=900, shot=None):
    args = [browser(), "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--allow-file-access-from-files",
            "--window-size=%d,%d" % (width, height), "--virtual-time-budget=20000"]
    if shot:
        args += ["--screenshot=" + shot]
    else:
        args += ["--dump-dom"]
    return subprocess.run(args + ["file://" + path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True, timeout=180).stdout


def answer_for(test, **kw):
    n, _cfg = live_for(test, actions={"jt_58": expand_action(newest_texts())}, **kw)
    return json.loads(n.answer(CN)[1])


@unittest.skipUnless(browser(), "no google-chrome or chromium")
class Browser(unittest.TestCase):
    """netapp-page.js in the portal's shell: every part drawn, Expand... buttons, the datastore search, no sideways scroll."""

    READ = ("var q=function(s){return [].map.call(document.querySelectorAll(s),function(x){return x.textContent.trim()})};"
            "r.h2=q('#na section.sec > h2');r.tiles=q('.nap-tiles .tile .k');r.expand=document.querySelectorAll('a[data-expand]').length;"
            "r.off=q('.notoff');r.rows=document.querySelectorAll('.nap-vols tbody tr').length;"
            "r.side=document.documentElement.scrollWidth-document.documentElement.clientWidth;r.h=document.documentElement.scrollHeight;"
            "var i=document.getElementById('nap-q');i.value='SITEA-07';i.dispatchEvent(new Event('input',{bubbles:true}));"
            "r.found=q('.nap-vols tbody tr b.mono');")

    def test_page(self):
        a = answer_for(self)
        for w in (1280, 800):
            out = run_page(page_files(self, a, width=w, js=self.READ), w)
            m = re.search(r'<pre id="res" hidden="">(.*?)</pre>', out, re.S)
            self.assertIsNotNone(m, out[-800:])
            r = json.loads(sp.html.unescape(m.group(1)))
            self.assertNotIn("err", r)
            self.assertEqual([x.split("\n")[0][:12] for x in r["h2"]][:2], ["At a glance", "Space runway"])
            self.assertTrue(r["h2"][2].startswith("SnapMirror"))
            self.assertEqual(len(r["tiles"]), 8)
            self.assertGreaterEqual(r["expand"], 5)
            self.assertTrue(any(x.startswith("Not offered: DP volume") for x in r["off"]), r["off"])
            self.assertEqual(r["side"], 0, w)
            self.assertEqual(r["found"], ["vol_ds07"])


@unittest.skipUnless(browser(), "no google-chrome or chromium")
class DialogSubmit(unittest.TestCase):
    """The Run dialog with the Job type, submitted in Chromium (portal.js initRun): Check and Run each reach the launch."""

    def submit(self, pick):
        texts = newest_texts()
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        p = sp.Portal(cfg_for(tmp))
        p.actions = {"jt_58": expand_action(texts)}
        frag = sp.action_page(p.cfg, p, CN, "jt_58", {"ontap_expand_volume": [texts[0]], "portal-job-type": ["check"]}, embed=True)
        html = sp.shell(p.cfg, "x", frag, CN, token="t1", page="x").replace("/portal/static/", "")
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        for n in ("portal.css", "portal-theme.js", "portal.js"):
            shutil.copy(os.path.join(FILES, "static", n), d)
        open(os.path.join(d, "banner.css"), "w").close()
        with open(os.path.join(d, "t.js"), "w") as fh:
            fh.write("window.__sent=null;window.fetch=function(u,o){if(o&&o.method==='POST')window.__sent=JSON.parse(o.body);"
                     "return Promise.resolve({ok:true,status:201,json:function(){return Promise.resolve({job_id:7,url:'#done'})}})};"
                     "document.addEventListener('DOMContentLoaded',function(){var f=document.getElementById('run');initRun(f);"
                     "var r=f.querySelector('input[name=\"portal-job-type\"][value=\"%s\"]');r.checked=true;r.dispatchEvent(new Event('change'));"
                     "var label=document.getElementById('go').textContent;f.requestSubmit();"
                     "setTimeout(function(){document.getElementById('res').textContent=JSON.stringify({sent:window.__sent,label:label,"
                     "state:document.getElementById('state').textContent})},800)});" % pick)
        html = html.replace('<script src="portal.js" defer></script>', '<script src="portal.js"></script><script src="t.js"></script>')
        html = html.replace("</body>", '<pre id="res"></pre></body>', 1)
        with open(os.path.join(d, "p.html"), "w") as fh:
            fh.write(html)
        out = run_page(os.path.join(d, "p.html"), 1280)
        m = re.search(r'<pre id="res">(.*?)</pre>', out, re.S)
        self.assertIsNotNone(m, out[-600:])
        return json.loads(sp.html.unescape(m.group(1))), texts

    def test_check_and_run_launch(self):
        r, texts = self.submit("check")
        self.assertEqual(r["sent"]["job_type"], "check", r)
        self.assertEqual(r["sent"]["vars"], {"ontap_expand_volume": texts[0], "ontap_expand_size": ""})
        self.assertEqual(r["label"], "Launch as Check")
        r, _t = self.submit("run")
        self.assertEqual(r["sent"]["job_type"], "run", r)
        self.assertEqual(r["label"], "Launch job")


if __name__ == "__main__":
    unittest.main(verbosity=1)
