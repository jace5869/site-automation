#!/usr/bin/env python3
"""1.129.0: the portal's ops-center wall (/portal/wall): the rules and the model (roles/site_portal_stack/files/wall_model.py:
sustained-high windows, LATE / STALE, never green when stale, the thresholds, the tracker), the answer (/api/wall: its shape,
the ETag), the page frame and its CSP (no inline script or style), the Feedback button and list (who may use and read them),
the role's settings, and the page itself in headless Chrome (each style drawn, a detail card opened, no sideways scroll).
Made-up data: tests/wall/demo.py. Placeholders only.
  python3 tests/test_wall.py"""
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
ROLE = os.path.join(ROOT, "roles", "site_portal_stack")
sys.path[:0] = [os.path.join(HERE, "wall"), FILES, os.path.join(ROOT, "plugins", "module_utils"), os.path.join(ROOT, "plugins", "filter"), HERE,
                os.path.join(HERE, "netapp"), os.path.join(HERE, "site_portal")]
import demo  # noqa: E402
import wall_model as M  # noqa: E402
import site_portal as sp  # noqa: E402

NOW = 1_800_000_000.0
O = M.opts_of({})


def flat(level, minutes=60, now=NOW, step=60):
    return [(now - m * step, level) for m in range(minutes, -1, -1)]


class Rules(unittest.TestCase):
    """the conditions' rules: one place (wall_model), every style draws them"""

    def test_sustained_window(self):
        s = demo.series(NOW, 14, 95.0, 50.0)                   # 14 min at 95: building, not on the wall
        v, start = M.run_of(s, 90, 60)
        self.assertEqual(v, 95.0)
        self.assertLess(M.held(start, s[-1][0], 60), 15 * 60)
        s = demo.series(NOW, 15, 95.0, 50.0)                   # 15 samples a minute apart + the poll the newest stands for = 15 min
        v, start = M.run_of(s, 90, 60)
        self.assertGreaterEqual(M.held(start, s[-1][0], 60), 15 * 60)
        self.assertEqual(M.run_of(demo.series(NOW, 30, 89.9, 50.0), 90, 60), (89.9, None))   # under the line: never
        self.assertEqual(M.run_of([], 90, 60), (None, None))

    def test_a_gap_breaks_a_run(self):
        s = [(NOW - 40 * 60 + i * 60, 95.0) for i in range(20)] + [(NOW - 5 * 60 + i * 60, 95.0) for i in range(6)]   # 15 min of no sample
        _v, start = M.run_of(s, 90, 60)
        self.assertEqual(start, NOW - 5 * 60)                  # the run starts again after the gap
        self.assertLess(M.held(start, s[-1][0], 60), 15 * 60)

    def test_late_and_stale(self):
        self.assertEqual([M.fstate(a) for a in (0, 180, 181, 600, 601, None)], ["ok", "ok", "late", "late", "stale", "stale"])

    def test_thresholds_from_the_settings(self):
        o = M.opts_of({"ds_warn": 80, "ds_crit": 90, "high_pct": 85, "high_minutes": 30, "seconds": 10})
        self.assertEqual((o["ds_warn"], o["ds_crit"], o["high_pct"], o["high_minutes"], o["seconds"]), (80.0, 90.0, 85.0, 30, 30))
        self.assertEqual(M.opts_of({"ds_warn": 96, "ds_crit": 95})["ds_warn"], 85.0)       # warning over critical: the defaults
        self.assertEqual(M.opts_of({"aggr_warn": "x"})["aggr_warn"], 85.0)
        k = M.kinds(o)
        self.assertIn("85 % or more for 30 min", k["cpu"]["label"])
        self.assertEqual(k["dsfull"]["label"], "Datastore 90 % used or more")
        self.assertEqual((M.level(89.9, 85, 95), M.level(85, 85, 95), M.level(95, 85, 95)), ("warn", "warn", "crit"))

    def test_tracker_keeps_the_start_and_the_cleared(self):
        t = M.Tracker()
        c = {"id": "host:a", "kind": "host"}
        out = t.update([dict(c)], NOW)
        self.assertEqual(out[0]["start"], NOW)
        out = t.update([dict(c)], NOW + 300)
        self.assertEqual(out[0]["start"], NOW)                 # still open: the first start
        out = t.update([], NOW + 600)
        self.assertEqual(out[0]["end"], NOW + 600)             # cleared: on the timeline
        self.assertEqual(t.update([], NOW + 600 + 4 * 3600 + 1), [])   # after 4 hours: gone


def model(view=None, **kw):
    return M.build(view if view is not None else demo.view(NOW), kw.pop("nm", None), kw.pop("asof", None), kw.pop("opts", demo.OPTS), M.Tracker(), NOW, **kw)


class Model(unittest.TestCase):
    def ids(self, m, kind):
        return [c["id"] for c in m["conds"] if c["kind"] == kind and c["end"] is None]

    def test_the_demo(self):
        m = model(enclave="SITEA", title="NOC")
        self.assertEqual((m["title"], m["enclave"]), ("NOC", "SITEA"))
        self.assertEqual(self.ids(m, "host"), ["host:%s:sita01-esx03.corp.example.mil" % demo.VC1])
        host = [h for h in m["vmware"]["hosts"] if h["n"] == "sita01-esx03.corp.example.mil"][0]
        self.assertEqual((host["st"], host["nvms"], host["ds"][0]), ("crit", 14, "DS-SITEA-01"))       # the VMs and datastores on a down host
        self.assertEqual(self.ids(m, "vc"), ["vc:" + demo.VC2])
        vc = [c for c in m["conds"] if c["kind"] == "vc"][0]
        self.assertEqual((vc["sev"], vc["obj"]), ("crit", "login failed - check the credential"))
        self.assertEqual(sorted(x["n"] for x in m["vmware"]["hot"]), ["sita01-esx02.corp.example.mil", "sita01-esx10.corp.example.mil", "sitb02-app07"])
        self.assertEqual([x["n"] for x in m["vmware"]["building"]], ["sita01-db02"])                    # 4 min: a blip, off the wall
        app = [c for c in m["conds"] if c.get("obj_name") == "sitb02-app07"][0]
        self.assertEqual(app["start"], NOW - 41 * 60)                                                   # since: the first sample at the line
        self.assertEqual(len([x for x in m["vmware"]["hot"] if x["n"] == "sitb02-app07"][0]["spark"]), 60)
        self.assertEqual([c["sev"] for c in m["conds"] if c["kind"].startswith("ds")], ["warn"])        # DS-SITEA-07 92.9 %
        self.assertEqual(m["feeds"]["vc:" + demo.VC2]["fresh"], "stale")                                # never read: stale, never green
        self.assertEqual(m["feeds"]["vc:" + demo.VC1]["fresh"], "ok")
        self.assertEqual([a["id"] for a in m["areas"] if a["on"]], ["storage", "vmware"])              # storage: the vCenter's datastores
        self.assertEqual(m["sites"], [{"id": "SITA01", "place": "DE"}, {"id": "SITB02", "place": "US"}])

    def test_the_answer_shape(self):
        m = model()
        for k in ("now", "title", "enclave", "late", "stale", "seconds", "areas", "feeds", "kinds", "conds", "vmware", "storage", "thresholds", "sites"):
            self.assertIn(k, m)
        for c in m["conds"]:
            self.assertTrue({"id", "kind", "area", "sev", "t", "obj", "start", "end"} <= set(c), c)
        self.assertEqual(len(set(c["id"] for c in m["conds"])), len(m["conds"]))
        json.dumps(m)                                                                                   # plain JSON

    def test_maintenance_expected_and_heartbeat(self):
        v = demo.view(NOW)
        snap = v["sources"]["vcenter:" + demo.VC1]["snap"]
        snap["hosts"][0]["maint"] = True
        snap["vms"][0]["heartbeat"] = "red"
        m = model(v)
        self.assertEqual(len(self.ids(m, "maint")), 1)
        self.assertEqual(len(self.ids(m, "vmhung")), 1)
        m = model(v, opts=dict(demo.OPTS, maintenance_expected=["sita01-esx0*"], vm_heartbeat=False))
        self.assertEqual((self.ids(m, "maint"), self.ids(m, "vmhung")), ([], []))

    def test_datastore_thresholds(self):
        m = model(opts=dict(demo.OPTS, ds_warn=70, ds_crit=90))
        sev = dict((c["ds"], c["sev"]) for c in m["conds"] if c.get("ds") and c["kind"] in ("dsfull", "dswarn"))
        self.assertEqual(sev["DS-SITEA-07"], "crit")
        self.assertEqual(sev["DS-SITEA-01"], "warn")
        inacc = demo.view(NOW)
        inacc["sources"]["vcenter:" + demo.VC1]["snap"]["datastores"][1]["accessible"] = False
        self.assertEqual(len(self.ids(model(inacc), "dsi")), 1)

    def test_a_stale_vcenter_keeps_its_last_conditions(self):
        v = demo.view(NOW)
        v["sources"]["vcenter:" + demo.VC1].update(state="error", why="timed out", at=NOW - 900, fail_since=NOW - 840)
        m = model(v)
        self.assertEqual(m["feeds"]["vc:" + demo.VC1]["fresh"], "stale")
        self.assertTrue(self.ids(m, "host"))                     # the last known stays (grey on the page), never "all clear"
        self.assertIn("vc:" + demo.VC1, self.ids(m, "vc"))

    def test_netapp_from_the_report_database(self):
        db = demo.netapp_db(11)
        import netapp_live as nl
        nm = nl.Database(lambda: db, mark="?").read()
        at = sp.ad_when(nm["run"]["read_at"])
        m = model(nm=nm, asof=at)
        self.assertEqual(m["feeds"]["ontap"]["fresh"], "stale")                                         # 11 min old
        self.assertTrue(any(c["kind"] == "aggrwarn" for c in m["conds"]))
        self.assertIn("DS-SITEA-07", m["storage"]["paths"])
        self.assertTrue(m["storage"]["paths"]["DS-SITEA-07"]["rows"][0]["aggr"])
        self.assertEqual([a["id"] for a in m["areas"] if a["on"]], ["storage", "vmware"])
        self.assertEqual(m["storage"]["aggr"][0]["v"], max(a["v"] for a in m["storage"]["aggr"]))

    def test_live_ontap_takes_the_place_of_the_run(self):
        v = demo.view(NOW)
        TB = 1024 ** 4
        v["sources"]["ontap:cluster9.example.mil"] = {"kind": "ontap", "name": "cluster9.example.mil", "state": "ok", "why": "", "at": NOW - 20,
                                                      "snap": {"cluster": "cluster9", "nodes": [{"name": "n1", "state": "up"}, {"name": "n2", "state": "down", "takeover": False}],
                                                               "aggrs": [{"name": "aggr_x", "node": "n1", "size": 10 * TB, "used": int(9.8 * TB)}],
                                                               "volumes": [{"name": "v1", "svm": "s1", "state": "offline"}], "luns": [],
                                                               "snapmirror": [{"src": "a:b", "dst": "c:d", "state": "broken_off", "healthy": False}]}}
        m = model(v)
        kinds = sorted(c["kind"] for c in m["conds"] if c.get("cluster") == "cluster9")
        self.assertEqual(kinds, ["aggrfull", "node", "sm", "voloff"])
        self.assertEqual(m["storage"]["nodes"], [1, 2])
        self.assertEqual(m["storage"]["source"], "live")
        self.assertIn("ontap:cluster9.example.mil", [a for x in m["areas"] if x["id"] == "storage" for a in x["feeds"]])


class Review(unittest.TestCase):
    """1.129.0 pre-release review: the model's part"""

    def live_and_db(self):
        import netapp_live as nl
        db = demo.netapp_db(25)
        nm = nl.Database(lambda: db, mark="?").read()
        v = demo.view(NOW)
        v["sources"]["ontap:cluster1.example.mil"] = {"kind": "ontap", "name": "cluster1.example.mil", "state": "ok", "why": "", "at": NOW - 20,
                                                      "snap": {"cluster": "cluster1", "nodes": [{"name": "n1", "state": "up"}, {"name": "n2", "state": ""}],
                                                               "aggrs": [], "volumes": [],
                                                               "luns": [{"path": "/vol/v/l", "svm": "s1", "volume": "v", "state": "offline"},
                                                                        {"path": "/vol/v/l", "svm": "s2", "volume": "v", "state": "offline"}],
                                                               "snapmirror": [{"src": "a:b", "dst": "c:d", "state": "snapmirrored", "healthy": True}]}}
        return M.build(v, nm, sp.ad_when(nm["run"]["read_at"]), demo.OPTS, M.Tracker(), NOW)

    def test_live_ontap_is_not_made_stale_by_the_run(self):
        m = self.live_and_db()
        st = [a for a in m["areas"] if a["id"] == "storage"][0]
        self.assertNotIn("netapp-run", st["feeds"])                      # the run's age stays on the storage path card only
        self.assertNotIn("ontap", m["feeds"])
        self.assertEqual(m["feeds"]["netapp-run"]["fresh"], "stale")
        self.assertEqual(m["storage"]["smRel"], 1)                      # the live count replaces the run's (never added)
        self.assertTrue(any(c["id"] == "node:cluster1:n2" for c in m["conds"]))   # an empty state is not "up"
        lun = sorted(c["id"] for c in m["conds"] if c["kind"] == "voloff")
        self.assertEqual(lun, ["voloff:cluster1:s1:/vol/v/l", "voloff:cluster1:s2:/vol/v/l"])   # the SVM in the id
        self.assertTrue(all("age" not in f for f in m["feeds"].values()))   # no per-second age in the answer (the ETag)


class CodexModel(unittest.TestCase):
    """Codex 1.129.0 pre-release review: the model's part"""

    def test_a_host_state_not_read_is_never_up(self):
        v = demo.view(NOW)
        v["sources"]["vcenter:" + demo.VC1]["snap"]["hosts"][0]["state"] = ""
        m = model(v)
        h = m["vmware"]["hosts"][0]
        self.assertEqual(h["st"], "stale")
        cl = [c for c in m["vmware"]["clusters"] if c["n"] == h["cluster"]][0]
        self.assertIn(h["n"], cl["unknown"])
        self.assertEqual(cl["up"], cl["of"] - 1 - len(cl["down"]))

    def test_a_live_ontap_read_clears_the_runs_conditions_of_its_cluster(self):
        import netapp_live as nl
        db = demo.netapp_db(25)
        nm = nl.Database(lambda: db, mark="?").read()
        nm["parts"].setdefault("nodes", {"rows": []})["rows"] = [{"cluster": "cluster1", "node": "cluster1-01", "state": "down"}]
        v = demo.view(NOW)
        v["sources"]["ontap:cluster1.example.mil"] = {"kind": "ontap", "name": "cluster1.example.mil", "state": "ok", "why": "", "at": NOW - 20,
                                                      "snap": {"cluster": "cluster1", "nodes": [{"name": "cluster1-01", "state": "up"}], "aggrs": [],
                                                               "volumes": [], "luns": [], "snapmirror": []}}
        m = M.build(v, nm, sp.ad_when(nm["run"]["read_at"]), demo.OPTS, M.Tracker(), NOW)
        self.assertEqual([c["id"] for c in m["conds"] if c["kind"] == "node"], [])     # the run said down, the live read says up
        self.assertEqual(m["storage"]["nodes"], [1, 1])

    def test_nothing_read_nothing_on(self):
        m = M.build({"sources": {}, "history": {}}, None, None, {}, M.Tracker(), NOW)
        self.assertEqual([a["id"] for a in m["areas"] if a["on"]], [])


# ---- the portal: the page, the answer, the Feedback ------------------------------------------------------------------------
JDOE, JDOE_CN = demo.DN, demo.CN
OTHER = "CN=ROE.RICHARD.A.2234567890,OU=USA,OU=PKI,OU=DoD,O=U.S. Government,C=US"


class FakeGroups(object):
    """site_ad_groups.Groups as the portal asks it: has(role, cn)"""

    def __init__(self, members):
        self.members = members

    def has(self, role, cn):
        return cn in self.members.get(role, ())

    def configured(self, role=None):
        return True


def serve(test, view=None, **env):
    tmp = tempfile.mkdtemp()
    test.addCleanup(shutil.rmtree, tmp, True)
    import test_netapp_live as T
    e = {"PORTAL_WALL_OPTS": json.dumps(demo.OPTS), "PORTAL_WALL_TITLE": "NOC"}
    e.update(env)
    cfg = T.cfg_for(tmp, **e)
    p = sp.Portal(cfg)
    p.aap.ready = ""
    sp.sys.stdout = open(os.devnull, "w")
    test.addCleanup(setattr, sp.sys, "stdout", sys.__stdout__)
    wall = sp.WallLive(cfg, p.log, None, collector=demo.FakeCollector(view or (lambda: demo.view())))
    srv = sp.PortalServer(("127.0.0.1", 0), sp.make_handler(cfg, p, os.path.join(FILES, "static"), wall=wall))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    test.addCleanup(srv.server_close)
    test.addCleanup(srv.shutdown)
    base = "http://127.0.0.1:%d" % srv.server_address[1]

    def call(u, h=None, dn=JDOE, data=None, method=None):
        hh = {"X-SSL-Client-Verify": "SUCCESS", "X-SSL-Client-S-DN": dn}
        hh.update(h or {})
        req = urllib.request.Request(base + u, headers=hh, data=data, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as f:
                return f.status, f.read().decode(), dict(f.headers)
        except urllib.error.HTTPError as err:
            return err.code, err.read().decode(), dict(err.headers)
    return cfg, p, call, base


def post_feedback(call, base, dn=JDOE, body=None, token=True, cn=JDOE_CN, portal=None):
    host = base.split("://", 1)[1]
    h = {"Content-Type": "application/json", "Host": host, "Origin": "https://" + host}
    if token:
        h["X-Portal-Token"] = portal.token(cn)
    return call("/api/wall/feedback", h, dn=dn, data=json.dumps(body or {"area": "vmware", "text": "The gauges are clear."}).encode(), method="POST")


class Portal(unittest.TestCase):
    def test_page_answer_and_csp(self):
        cfg, p, call, _b = serve(self)
        code, page, h = call("/wall")
        self.assertEqual(code, 200)
        self.assertIn("script-src 'self';", h["Content-Security-Policy"])
        self.assertIn("style-src 'self';", h["Content-Security-Policy"])
        self.assertNotIn("<script>", page)
        self.assertNotIn(" style=", page)
        self.assertRegex(page, r'<script src="/portal/static/wall-page.js\?v=[0-9a-f]{10}" defer></script>')
        self.assertIn('href="/portal/wall" aria-current="page">NOC</a>', page)       # the tab: portal_wall_title (outage_ops_center)
        for n in ("wall-page.js", "wall-page.css", "wall-land.js"):
            self.assertEqual(call("/static/" + n)[0], 200, n)
        code, body, h = call("/api/wall")
        a = json.loads(body)
        self.assertEqual(code, 200)
        self.assertEqual(a["title"], "NOC")
        self.assertEqual(a["me"]["feedback"], True)
        self.assertEqual(a["me"]["reader"], False)                                    # not an approver
        self.assertNotIn("password", body.lower())
        self.assertEqual(call("/api/wall", {"If-None-Match": h["ETag"]})[0] in (200, 304), True)
        self.assertIn('href="/portal/wall"', call("/")[1])

    def test_etag_holds_between_seconds(self):
        fixed = demo.view()                                   # a collector's read: the same until the next one
        _cfg, _p, call, _b = serve(self, view=lambda: fixed)
        _c, _body, h = call("/api/wall")
        time.sleep(1.2)
        self.assertEqual(call("/api/wall", {"If-None-Match": h["ETag"]})[0], 304)

    def test_csv_cells_are_never_formulas(self):
        cfg, p, call, base = serve(self, PORTAL_APPROVERS=json.dumps([JDOE_CN]))
        for t in ("=HYPERLINK(\"http://x\")", "+1", "@SUM(A1)", "-2", "plain"):
            self.assertEqual(post_feedback(call, base, body={"area": "vmware", "text": t, "ref": "=ref"}, portal=p)[0], 201)
        rows = call("/wall/feedback.csv")[1].splitlines()[1:]
        import csv
        cells = [r[3:] for r in csv.reader(rows)]
        self.assertEqual(sorted(c[1] for c in cells), sorted(["'=HYPERLINK(\"http://x\")", "'+1", "'@SUM(A1)", "'-2", "plain"]))
        self.assertTrue(all(c[0] == "'=ref" for c in cells))

    def test_blank_group_lists_close_the_feedback(self):
        cfg, p, call, base = serve(self, PORTAL_WALL_FEEDBACK_GROUPS='[" "]', PORTAL_WALL_FEEDBACK_READERS='["  "]', PORTAL_APPROVERS=json.dumps([JDOE_CN]))
        self.assertEqual(post_feedback(call, base, portal=p)[0], 403)                 # set, but no name: nobody - never everyone
        self.assertEqual(call("/wall/feedback")[0], 403)                               # ... and never "the approvers"
        self.assertEqual(json.loads(call("/api/wall")[1])["me"], dict(json.loads(call("/api/wall")[1])["me"], feedback=False, reader=False))
        t = open(os.path.join(ROLE, "tasks", "main.yml")).read()
        self.assertIn("portal_wall_feedback_groups | map('string') | map('trim') | select('equalto', '') | list | length == 0", t)

    def test_freeze_only_in_the_test_harness(self):
        _cfg, _p, call, _b = serve(self)
        self.assertNotIn('data-test="1"', call("/wall")[1])
        _cfg, _p, call, _b = serve(self, PORTAL_WALL_TEST="1")
        self.assertIn('data-test="1"', call("/wall")[1])
        js = open(os.path.join(FILES, "static", "wall-page.js")).read()
        self.assertIn('var FREEZE = TEST && QS.get("freeze") === "1";', js)
        self.assertIn('var PULL = TEST && QS.get("pull")', js)

    def test_off(self):
        _cfg, _p, call, _b = serve(self, PORTAL_WALL="0")
        self.assertEqual(call("/wall")[0], 404)
        self.assertEqual(call("/api/wall")[0], 404)
        self.assertNotIn('href="/portal/wall"', call("/")[1])

    def test_static_rules(self):
        js = open(os.path.join(FILES, "static", "wall-page.js")).read()
        for bad in (".innerHTML", ".outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "DOMParser", 'setAttribute("style"', "cssText"):
            self.assertNotIn(bad, js, bad)
        css = open(os.path.join(FILES, "static", "wall-page.css")).read()
        for bad in ("http://", "https://", "@import", "url("):
            self.assertNotIn(bad, css, bad)
        self.assertNotIn("days until full", (js + css).lower())                      # never on the wall


class Feedback(unittest.TestCase):
    def test_everyone_may_send_only_approvers_read(self):
        cfg, p, call, base = serve(self, PORTAL_APPROVERS=json.dumps([JDOE_CN]))
        self.assertEqual(post_feedback(call, base, portal=p)[0], 201)
        other = "ROE.RICHARD.A.2234567890"
        self.assertEqual(post_feedback(call, base, dn=OTHER, cn=other, portal=p)[0], 201)     # no groups set: everyone who opens the wall
        code, page, _h = call("/wall/feedback")
        self.assertEqual(code, 200)
        self.assertIn("The gauges are clear.", page)
        self.assertIn("2 entries", page)
        self.assertEqual(call("/wall/feedback", dn=OTHER)[0], 403)                   # not an approver: no list
        self.assertEqual(call("/wall/feedback.csv", dn=OTHER)[0], 403)
        code, csv_, h = call("/wall/feedback.csv?area=vmware")
        self.assertEqual(code, 200)
        self.assertIn("text/csv", h["Content-Type"])
        self.assertEqual(len(csv_.strip().splitlines()), 3)
        self.assertEqual(json.loads(call("/api/wall")[1])["me"]["reader"], True)
        self.assertEqual(json.loads(call("/api/wall", dn=OTHER)[1])["me"]["reader"], False)
        rows = [json.loads(x) for x in open(os.path.join(cfg.data, "wall-feedback.jsonl"))]
        self.assertEqual([r["user"] for r in rows], [JDOE_CN, other])
        self.assertEqual(oct(os.stat(os.path.join(cfg.data, "wall-feedback.jsonl")).st_mode & 0o777), "0o600")

    def test_groups(self):
        cfg, p, call, base = serve(self, PORTAL_WALL_FEEDBACK_GROUPS='["Wall-Feedback"]', PORTAL_WALL_FEEDBACK_READERS='["Wall-Readers"]')
        cfg.ad_groups = FakeGroups({"wall_feedback": {JDOE_CN}, "wall_readers": set()})
        other = "ROE.RICHARD.A.2234567890"
        self.assertEqual(post_feedback(call, base, portal=p)[0], 201)
        self.assertEqual(post_feedback(call, base, dn=OTHER, cn=other, portal=p)[0], 403)  # not in the group: the POST refused
        self.assertEqual(json.loads(call("/api/wall", dn=OTHER)[1])["me"]["feedback"], False)   # and no button
        self.assertEqual(call("/wall/feedback")[0], 403)                                    # a sender, not a reader
        cfg.ad_groups = FakeGroups({"wall_feedback": set(), "wall_readers": {other}})
        self.assertEqual(call("/wall/feedback", dn=OTHER)[0], 200)
        cfg.ad_groups = None                                                                # AD not read: fail closed
        self.assertEqual(post_feedback(call, base, portal=p)[0], 403)
        self.assertEqual(call("/wall/feedback", dn=OTHER)[0], 403)

    def test_refusals(self):
        _cfg, p, call, base = serve(self)
        self.assertEqual(post_feedback(call, base, token=False, portal=p)[0], 403)       # no token: not from the page
        self.assertEqual(post_feedback(call, base, body={"area": "vmware", "text": ""}, portal=p)[0], 400)
        self.assertEqual(post_feedback(call, base, body={"area": "nowhere", "text": "x"}, portal=p)[0], 400)
        self.assertEqual(post_feedback(call, base, body={"area": "vmware", "text": "x" * 1001}, portal=p)[0], 400)
        _cfg, p, call, base = serve(self, PORTAL_WALL_FEEDBACK="0")
        self.assertEqual(post_feedback(call, base, portal=p)[0], 403)
        self.assertEqual(call("/wall/feedback")[0], 404)
        self.assertEqual(json.loads(call("/api/wall")[1])["me"]["feedback"], False)


class Role(unittest.TestCase):
    def test_settings_and_secrets(self):
        d = open(os.path.join(ROLE, "defaults", "main.yml")).read()
        for k in ("portal_wall:", "portal_wall_title:", "portal_wall_datastore_warn: 85", "portal_wall_datastore_crit: 95", "portal_wall_aggr_crit: 97",
                  "portal_wall_high_pct: 90", "portal_wall_high_minutes: 15", "portal_wall_auth_retry_minutes: 15", "portal_wall_feedback: true",
                  "portal_wall_feedback_groups: []", "portal_wall_feedback_readers:"):
            self.assertIn(k, d)
        self.assertIn("outage_ops_center | default('Ops center', true)", d)
        t = open(os.path.join(ROLE, "tasks", "main.yml")).read()
        for k in ("PORTAL_WALL_OPTS:", "site-portal-vcenter", "site-portal-ontap", "PORTAL_WALL_FEEDBACK_GROUPS"):
            self.assertIn(k, t)
        # a login turned off is removed only after the service restarted without it; a changed login restarts the service
        i_restart = t.index("- name: Units | restart the service (what it runs changed)")
        self.assertGreater(t.index("- name: Secret | the wall's logins no longer used - removed"), i_restart)
        self.assertIn("or _portal_wall_set is changed", t[i_restart:i_restart + 1500])
        self.assertLess(t.index("the service stopped before a login is replaced (podman before 4.7)"),
                        t.index("- name: Secret | the wall's logins - the old value removed first (podman before 4.7)"))
        u = open(os.path.join(ROLE, "templates", "site-portal-app.container.j2")).read()
        self.assertIn("Secret=site-portal-vcenter,type=env,target=PORTAL_WALL_VCENTER_LOGIN", u)
        self.assertIn("Secret=site-portal-ontap,type=env,target=PORTAL_WALL_ONTAP_LOGIN", u)


class DeployCopiesEverything(unittest.TestCase):
    """Codex 1.129.0: the deploy copied site_portal.py but not wall_model.py - the portal could not start. Every module the service
    imports (and theirs, all the way down) and every static file its pages name must be in the role's copy loop."""

    def copied(self):
        import yaml
        t = yaml.safe_load(open(os.path.join(ROLE, "tasks", "main.yml")))
        task = [x for x in t if x.get("name") == "Files | the service, its pages, the actions' rules, AAP's client"][0]
        return dict((i["dest"], i["src"]) for i in task["loop"])

    def local_modules(self):
        """every .py the service could import from this repo: the role's files and plugins/{module_utils,filter}"""
        out = {}
        for d in (FILES, os.path.join(ROOT, "plugins", "module_utils"), os.path.join(ROOT, "plugins", "filter")):
            for n in os.listdir(d):
                if n.endswith(".py"):
                    out.setdefault(n[:-3], os.path.join(d, n))
        return out

    def test_modules(self):
        import ast
        cp, local = self.copied(), self.local_modules()
        todo, seen = ["site_portal"], set()
        while todo:
            m = todo.pop()
            if m in seen:
                continue
            seen.add(m)
            tree = ast.parse(open(local[m]).read())

            def top(body):
                """the module's own statements (and its try / if blocks) - an import inside a function runs only when that is called"""
                for st in body:
                    yield st
                    for sub in ("body", "orelse", "finalbody"):
                        if isinstance(st, (ast.Try, ast.If)) and getattr(st, sub, None):
                            yield from top(getattr(st, sub))
                    if isinstance(st, ast.Try):
                        for hd in st.handlers:
                            yield from top(hd.body)
            for node in top(tree.body):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
                for n in names:
                    n = n.split(".")[-1] if n.startswith("ansible.module_utils.") else n.split(".")[0]
                    if n in local and n not in seen:
                        todo.append(n)
        missing = sorted(m + ".py" for m in seen if m + ".py" not in cp)
        self.assertEqual(missing, [], "imported by the service but not copied by the deploy")

    def test_static_files(self):
        cp = self.copied()
        src = open(os.path.join(FILES, "site_portal.py")).read()
        named = set(re.findall(r'static/([A-Za-z0-9_.-]+\.(?:js|css|html))', src))
        for tup in re.findall(r'^[A-Z_]+_STATIC = \(([^)]*)\)', src, re.M):
            named |= set(re.findall(r'"([^"]+)"', tup))
        named -= {"festive.css", "festive.js", "banner.css"}           # made by the service itself (site_festive, the banner)
        missing = sorted(n for n in named if "static/" + n not in cp)
        self.assertEqual(missing, [], "named by the pages but not copied by the deploy")
        for dest, srcf in cp.items():                                   # and every source is there
            path = srcf.replace("{{ role_path }}", ROLE) if "{{" in srcf else os.path.join(FILES, srcf)
            self.assertTrue(os.path.isfile(os.path.normpath(path)), (dest, srcf))


def browser():
    for b in ("google-chrome", "chromium", "chromium-browser"):
        if shutil.which(b):
            return shutil.which(b)
    return None


@unittest.skipUnless(browser(), "no google-chrome or chromium")
class Browser(unittest.TestCase):
    """the real page from a local portal with the demo: each style drawn, a detail card opened, no sideways scroll"""

    @classmethod
    def setUpClass(cls):
        cls.url, cls.stop = demo.portal()

    @classmethod
    def tearDownClass(cls):
        cls.stop()

    def dom(self, query, width=1280, height=900):
        args = [browser(), "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--window-size=%d,%d" % (width, height),
                "--virtual-time-budget=8000", "--dump-dom", self.url + "wall?" + query]
        return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True, timeout=180).stdout

    def test_styles(self):
        for st, mark in (("cockpit", 'class="sK"'), ("lights", 'class="sL"'), ("map", 'class="sM"')):
            for w in (1920, 1280):
                out = self.dom("style=%s&freeze=1" % st, w)
                self.assertIn('data-ready="1"', out, (st, w))
                self.assertIn(mark, out, st)
                self.assertIn("sita01-esx03.corp.example.mil not responding", out, st)
        out = self.dom("style=cockpit&freeze=1")
        self.assertIn("Not on the wall yet", out)                                    # VDI, AD, Outages, Network: grey
        self.assertIn('data-d="ctr:hosts"', out)                                     # every counter opens a card

    def test_detail_cards(self):
        out = self.dom("style=cockpit&freeze=1&open=" + urllib.request.quote("c:host:%s:sita01-esx03.corp.example.mil" % demo.VC1))
        self.assertIn('id="dsBack" role="presentation" class="on"', out)
        self.assertIn("Where to verify", out)
        self.assertIn("sita01-app", out)                                             # the VMs on the down host
        out = self.dom("style=lights&freeze=1&open=ds:DS-SITEA-07")
        self.assertIn("NetApp · cluster2", out)                                     # datastore -> volume -> aggregate
        self.assertIn("aggr2_c2n2", out)
        out = self.dom("style=lights&freeze=1&open=ds:DS-SITEB-LOCAL")
        self.assertIn("No NetApp match found", out)

    def test_never_green_when_stale(self):
        """vCenter read 15 min ago, nothing wrong in that read: the VMware area is Not current, never OK"""
        def stale_view():
            v = demo.view(vc2_state="ok")
            s1 = v["sources"]["vcenter:" + demo.VC1]
            s1["at"] = s1["tried"] = time.time() - 900
            for h in s1["snap"]["hosts"]:
                h.update(state="connected", cpu_pct=40.0, mem_pct=50.0)
            for d in s1["snap"]["datastores"]:
                d["free"] = d["capacity"] // 2
            v["sources"]["vcenter:" + demo.VC2]["at"] = time.time() - 900
            v["history"] = {}
            return v
        url, stop = demo.portal(collector=demo.FakeCollector(stale_view), netapp_minutes_old=30)
        self.addCleanup(stop)
        for st in ("lights", "cockpit", "map"):
            args = [browser(), "--headless=new", "--no-sandbox", "--disable-gpu", "--window-size=1920,1080", "--virtual-time-budget=8000",
                    "--dump-dom", url + "wall?style=%s&freeze=1" % st]
            out = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True, timeout=180).stdout
            self.assertIn('data-ready="1"', out, st)
            self.assertNotIn('class="word ok"', out, st)                              # no green anywhere: every source is stale
            self.assertNotIn('class="lamp s-ok"', out, st)
            self.assertNotRegex(out, r'class="(sdot|sc|tag) k-ok', st)                    # Map: no green dot, chip or tally
            self.assertIn("Not current" if st != "map" else "not current", out, st)
            if st == "lights":
                self.assertIn('aria-label="VMware: Not current"', out)

    def run_page(self, url, query, budget=8000, width=1920):
        args = [browser(), "--headless=new", "--no-sandbox", "--disable-gpu", "--window-size=%d,1080" % width, "--virtual-time-budget=%d" % budget,
                "--dump-dom", url + "wall?" + query]
        return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True, timeout=240).stdout

    def test_a_current_down_host_is_not_hidden_by_an_old_vcenter(self):
        def view():
            v = demo.view(vc2_state="ok")
            v["sources"]["vcenter:" + demo.VC2]["at"] = time.time() - 900       # vc02 15 min old, vc01 current with a host down
            return v
        url, stop = demo.portal(collector=demo.FakeCollector(view))
        self.addCleanup(stop)
        out = self.run_page(url, "style=cockpit&freeze=1")
        self.assertRegex(out, r'<div class="ctr crit"[^>]*data-d="ctr:hosts"')       # Critical, not "? Not current"
        self.assertRegex(out, r'class="gauge gcrit"[^>]*data-d="c:host:')             # the hosts-up gauge too

    def test_portal_not_reachable(self):
        calls = {"n": 0}

        def view():
            calls["n"] += 1
            if calls["n"] > 2:
                raise RuntimeError("the portal's read is gone")
            return demo.view(vc2_state="ok")
        url, stop = demo.portal(collector=demo.FakeCollector(view))
        self.addCleanup(stop)
        out = self.run_page(url, "style=lights&pull=2", budget=25000)
        self.assertIn('class="unreach"', out)
        self.assertIn("Portal not reachable", out)
        self.assertNotIn('class="lamp s-ok"', out)
        self.assertNotIn('class="word ok"', out)
        self.assertNotIn('class="lamp s-crit"', out)                                   # not even red: nothing is current

    def test_sparkline_with_reads_every_two_minutes(self):
        def view():
            v = demo.view(vc2_state="ok")
            now = time.time()
            v["history"] = {("vcenter:" + demo.VC1, "vm", "sitb02-app07", "cpu"): [(now - 120 * i, 97.0) for i in range(30, -1, -1)]}
            return v
        url, stop = demo.portal(collector=demo.FakeCollector(view), opts={"seconds": 120})
        self.addCleanup(stop)
        out = self.run_page(url, "style=cockpit&freeze=1")
        m = re.search(r'<path class="ln g-warn" d="([^"]*)"', out)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).count("M"), 1)                                     # one line, no breaks at the empty minutes

    def test_feedback_on_a_host_card_keeps_its_ref(self):
        out = self.dom("style=cockpit&freeze=1&open=" + urllib.request.quote("host:%s|sita01-esx02.corp.example.mil" % demo.VC1))
        self.assertIn('data-fb="host:%s|sita01-esx02.corp.example.mil" data-fb-area="vmware"' % demo.VC1, out)

    def test_no_netapp_storage_counters_are_grey(self):
        url, stop = demo.portal(netapp=False, collector=demo.FakeCollector(lambda: demo.view(vc2_state="ok")))
        self.addCleanup(stop)
        out = self.run_page(url, "style=cockpit&freeze=1")
        self.assertRegex(out, r'<div class="ctr off"[^>]*title="SnapMirror broken: no NetApp read')
        self.assertRegex(out, r'<div class="ctr off"[^>]*title="Storage nodes down: no NetApp read')
        self.assertNotRegex(out, r'<div class="ctr ok"[^>]*data-d="ctr:(sm|nodes)"')

    def test_nothing_read_is_never_green(self):
        url, stop = demo.portal(netapp=False, collector=demo.FakeCollector(lambda: {"sources": {}, "history": {}}))
        self.addCleanup(stop)
        for st in ("lights", "cockpit", "map"):
            out = self.run_page(url, "style=%s&freeze=1" % st)
            self.assertIn('data-ready="1"', out, st)
            self.assertNotIn('class="lamp s-ok"', out, st)
            self.assertNotIn('class="word ok"', out, st)

    def test_datastore_card_inaccessible_and_two_of_a_name(self):
        def view():
            v = demo.view(vc2_state="ok")
            d1 = v["sources"]["vcenter:" + demo.VC1]["snap"]["datastores"][1]          # DS-SITEA-01, 83 % - inaccessible
            d1["accessible"] = False
            d1["free"] = int(d1["capacity"] * 0.6)                                        # 40 % used: the fill alone would be green
            v["sources"]["vcenter:" + demo.VC2]["snap"]["datastores"] = [dict(d1, accessible=True, free=d1["capacity"] // 10, hosts=[], vms=3)]
            return v
        url, stop = demo.portal(collector=demo.FakeCollector(view))
        self.addCleanup(stop)
        out = self.run_page(url, "style=cockpit&freeze=1&open=" + urllib.request.quote("ds:%s|DS-SITEA-01" % demo.VC1))
        card = out.split('id="dsCard"', 1)[1]
        self.assertIn('class="wd crit"', card)
        self.assertNotIn('class="wd ok"', card.split('class="sec"')[1].split('class="sec"')[0])  # the datastore row: not green
        self.assertIn("vCenter marks this datastore inaccessible", card)
        out = self.run_page(url, "style=cockpit&freeze=1&open=" + urllib.request.quote("ds:%s|DS-SITEA-01" % demo.VC2))
        card = out.split('id="dsCard"', 1)[1]
        self.assertIn("vCenter " + demo.VC2, card)                                       # the namesake of the other vCenter
        self.assertIn("90 %", card)

    def test_a_failed_first_read_is_tried_again(self):
        calls = {"n": 0}

        def view():
            calls["n"] += 1
            if calls["n"] <= 2:
                raise RuntimeError("not yet")
            return demo.view(vc2_state="ok")
        url, stop = demo.portal(collector=demo.FakeCollector(view))
        self.addCleanup(stop)
        out = self.run_page(url, "style=cockpit&pull=2", budget=20000)
        self.assertIn('data-ready="1"', out)

    def test_no_sideways_scroll(self):
        for st in ("cockpit", "lights", "map"):
            for w in (1920, 1280):
                out = self.dom("style=%s&freeze=1&check=1" % st, w)
                m = re.search(r'data-check="([^"]*)"', out)
                self.assertIsNotNone(m, (st, w))
                r = json.loads(sp.html.unescape(m.group(1)))
                self.assertLessEqual(r["sw"], r["iw"], (st, w, r))
        out = self.dom("style=cockpit&freeze=1&kiosk=1")
        self.assertIn('class="kiosk"', out)

if __name__ == "__main__":
    unittest.main(verbosity=1)
