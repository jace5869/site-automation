#!/usr/bin/env python3
"""1.130.0: the portal's ops-center wall (/portal/wall): the rules and the model (roles/site_portal_stack/files/wall_model.py:
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
        out = t.update([], NOW + 450)
        self.assertEqual((out[0].get("end"), out[0]["last_known"]), (None, True))      # gone from the read, not seen OK: last known
        out = t.update([], NOW + 600, ok={"host:a"})
        self.assertEqual(out[0]["end"], NOW + 600)             # seen OK: cleared, on the timeline
        self.assertEqual(t.update([], NOW + 600 + 4 * 3600 + 1), [])   # after 4 hours: gone


def model(view=None, **kw):
    return M.build(view if view is not None else demo.view(NOW), kw.pop("nm", None), kw.pop("asof", None), kw.pop("opts", demo.OPTS), M.Tracker(), NOW, **kw)


class NetmapPlaces131(unittest.TestCase):
    """1.131.0: the Map takes the network map's netmap_sites when portal_wall_site_places has no entry for a site"""

    def test_mapping(self):
        o = M.opts_of({"netmap_sites": {"Site A": {"lat": 39.4, "lon": -93.2}, "SITB*": {"lat": 34.9, "lon": -112.8},
                                        "bad": {"lat": "x"}, "far": {"lat": 120, "lon": 0}},
                       "site_places": {"sitb02": "DE"}})
        self.assertEqual(M.place_of("SITE A", o), [-93.2, 39.4])           # by name, any case; {lat, lon} -> [lon, lat]
        self.assertEqual(M.place_of("SITB01", o), [-112.8, 34.9])          # by pattern, any case
        self.assertEqual(M.place_of("SITB02", o), "DE")                    # portal_wall_site_places wins
        self.assertIsNone(M.place_of("BAD", o))
        self.assertIsNone(M.place_of("FAR", o))
        self.assertIsNone(M.place_of("SITC01", o))
        self.assertEqual(M.netmap_places(o), 2)
        self.assertEqual(M.place_of("SITA01", M.opts_of({})), None)
        self.assertEqual(M.opts_of({"netmap_sites": "not a mapping"})["netmap_sites"], {})

    def test_most_specific_pattern_and_bad_numbers(self):
        o = M.opts_of({"netmap_sites": {"*": {"lat": 1, "lon": 2}, "ABC*": {"lat": 3, "lon": 4}, "AB?01": {"lat": 5, "lon": 6},
                                        "BIG*": {"lat": 10 ** 400, "lon": 0}, "NAN*": {"lat": float("nan"), "lon": 0},
                                        "INF*": {"lat": "inf", "lon": 0}}})
        self.assertEqual(M.place_of("ABC02", o), [4.0, 3.0])                # ABC* is more specific than * (the first match)
        self.assertEqual(M.place_of("ABC01", o), [6.0, 5.0])                # AB?01 more specific than ABC*
        import site_swis
        conf = {"*": [1, 2], "ABC*": [3, 4], "AB?01": [5, 6]}
        for name in ("ABC01", "ABC02", "ZZZ"):                               # the same choice as the network map
            self.assertEqual(M.place_of(name, o), list(reversed([float(x) for x in site_swis.place(conf, name)])))
        for s in ("BIG1", "NAN1", "INF1"):
            self.assertEqual(M.place_of(s, o), [2.0, 1.0])                  # the bad entry left out: the catch-all
        self.assertEqual(M.netmap_ignored(o), ["BIG*", "INF*", "NAN*"])
        self.assertIsNone(site_swis._num(10 ** 400))
        import site_portal_filters as F
        self.assertIn("Map places from netmap_sites: 3", F.site_portal_wall_summary({"netmap_sites": o["netmap_sites"]}))

    def test_summary_text(self):
        import site_portal_filters as F
        base = {"wall": True, "vc_on": False, "vc_mode": "auto", "vc_host": "", "vcenters": [], "vc_user": "", "vc_pass": False,
                "ontap_on": False, "ontap_mode": "auto", "clusters": [], "ontap_user": "", "ontap_pass": False,
                "netmap_sites": {"Site A": {"lat": 39.4, "lon": -93.2}, "SITB*": {"lat": 34.9, "lon": -112.8}}}
        self.assertEqual(F.site_portal_wall_summary(base),
                         "Wall: VMware off (no vCenter credential attached (VMWARE_HOST empty)), Storage off (ontap_clusters empty), "
                         "Map places from netmap_sites: 2")
        self.assertEqual(F.site_portal_wall_summary(dict(base, clusters=["cl01"])).split(", Map")[0].split(", ")[-1],
                         "Storage off (no NetApp credential (ONTAP_USERNAME empty))")
        self.assertEqual(F.site_portal_wall_summary(dict(base, vc_on=True, ontap_on=True, netmap_sites={})),
                         "Wall: VMware on, Storage on, Map places from netmap_sites: 0")
        self.assertIn("VMware off (portal_wall_vcenter: false)", F.site_portal_wall_summary(dict(base, vc_mode="False")))
        self.assertEqual(F.site_portal_wall_summary(dict(base, wall=False)), "Wall: off (portal_wall: false)")


class NoSources131(unittest.TestCase):
    """1.131.0 (field): logins in the container but no source from them - logged, and the wall says why, not: no login"""

    def run_with(self, logins):
        from unittest import mock
        recs = []
        log = mock.Mock()
        log.add.side_effect = recs.append
        cfg = mock.Mock(wall_opts=M.opts_of({}), wall_auth_retry=900, data=tempfile.mkdtemp(dir=os.environ.get("TMPDIR")), wall_logins=logins,
                        wall_enclave="", wall_title="NOC")
        self.addCleanup(shutil.rmtree, cfg.data, True)
        w = sp.WallLive(cfg, log, None)
        self.assertIsNone(w.collector)
        return w, recs

    def test_login_without_addresses(self):
        w, recs = self.run_with({"vcenter": {"user": "svc", "password": "pw", "hosts": []},
                                 "ontap": {"user": "svc", "password": "pw", "clusters": ["https://"]}})
        no = [r for r in recs if r.get("collector") == "no sources"]
        self.assertEqual(len(no), 1, recs)
        self.assertIn("VMware: vCenter login present but no vCenter address", no[0]["why"])
        self.assertTrue(any(r.get("state") == "left out" and r.get("source") == "ontap" for r in recs), recs)
        areas = {a["id"]: a for a in w.model()["areas"]}
        self.assertEqual(areas["vmware"]["why"], "vCenter login present but no vCenter address: set portal_wall_vcenters")
        self.assertTrue(areas["storage"]["why"].startswith("ONTAP login present but the cluster list is empty or not usable: "),
                        areas["storage"]["why"])
        self.assertNotIn("pw", json.dumps(recs))

    def test_logins_error_is_logged(self):
        from unittest import mock
        import wall_live
        with mock.patch.object(wall_live, "sources_from_config", side_effect=RuntimeError("boom")):
            w, recs = self.run_with({"vcenter": {"user": "svc", "password": "pw", "hosts": ["vc01.corp.example.mil"]}})
        self.assertTrue(any(r.get("collector") == "no sources" and "boom" in json.dumps(r) for r in recs), recs)

    def test_codex_new1_an_address_with_a_password_never_shows_it(self):
        w, recs = self.run_with({"ontap": {"user": "svc", "password": "pw", "clusters": ["https://admin:S3cretPw@cluster1.example.mil"]}})
        self.assertNotIn("S3cretPw", json.dumps(recs))
        self.assertNotIn("S3cretPw", json.dumps(w.model()))
        self.assertTrue(any("user or password" in json.dumps(r) for r in recs), recs)

    def test_codex_new5_a_source_error_is_said_as_that_error(self):
        from unittest import mock
        import wall_live
        with mock.patch.object(wall_live, "sources_from_config", side_effect=RuntimeError("boom")):
            w, recs = self.run_with({"vcenter": {"user": "svc", "password": "pw", "hosts": ["vc01.corp.example.mil"]}})
        no = [r for r in recs if r.get("collector") == "no sources"]
        self.assertEqual(len(no), 1, recs)
        self.assertIn("boom", no[0]["why"])
        why = {a["id"]: a for a in w.model()["areas"]}["vmware"]["why"]
        self.assertIn("boom", why)
        self.assertNotIn("no vCenter address", why)

    def test_bad_places_logged_once(self):
        from unittest import mock
        recs = []
        log = mock.Mock()
        log.add.side_effect = recs.append
        cfg = mock.Mock(wall_opts=M.opts_of({"netmap_sites": {"X*": {"lat": 10 ** 400, "lon": 1}}}), wall_auth_retry=900,
                        data=tempfile.mkdtemp(dir=os.environ.get("TMPDIR")), wall_logins={}, wall_enclave="", wall_title="NOC")
        self.addCleanup(shutil.rmtree, cfg.data, True)
        sp.WallLive(cfg, log, None)
        self.assertEqual([r.get("sites") for r in recs if r.get("map")], [["X*"]])

    def test_no_password_logged_once(self):
        w, recs = self.run_with({"ontap": {"user": "svc", "password": "", "clusters": ["cl01.corp.example.mil"]}})
        self.assertEqual(len([r for r in recs if r.get("collector") == "no sources"]), 1, recs)
        self.assertEqual({a["id"]: a for a in w.model()["areas"]}["storage"]["why"], M.STORAGE_OFF)


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
        """Codex verify 4: the report database is never a light - with no live ONTAP, Storage is not on the wall; the run's rows
        only fill the storage path card, as information"""
        db = demo.netapp_db(11)
        import netapp_live as nl
        nm = nl.Database(lambda: db, mark="?").read()
        at = sp.ad_when(nm["run"]["read_at"])
        m = model(demo.view(NOW, ontap=False), nm=nm, asof=at)
        st = [a for a in m["areas"] if a["id"] == "storage"][0]
        self.assertEqual((st["on"], st["feeds"], st["why"]), (False, [], M.STORAGE_OFF))
        self.assertFalse([c for c in m["conds"] if c["area"] == "storage"])
        self.assertEqual((m["storage"]["aggr"], m["storage"]["ran"]), ([], {"sm": False, "nodes": False}))
        self.assertIn("DS-SITEA-07", m["storage"]["paths"])
        self.assertTrue(all(r["info"] and r["feed"] == "netapp-run" for p in m["storage"]["paths"].values() for r in p["rows"]))
        m = model(nm=nm, asof=at)                                                       # with the live clusters (the demo's two)
        st = [a for a in m["areas"] if a["id"] == "storage"][0]
        self.assertTrue(st["on"])
        self.assertNotIn("netapp-run", st["feeds"])
        self.assertEqual(set(a["feed"] for a in m["storage"]["aggr"]), {"ontap:" + demo.NA1, "ontap:" + demo.NA2})
        self.assertEqual(m["storage"]["aggr"][0]["v"], max(a["v"] for a in m["storage"]["aggr"]))

    def test_live_ontap_takes_the_place_of_the_run(self):
        v = demo.view(NOW, ontap=False)
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
    """1.130.0 pre-release review: the model's part"""

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

    def test_one_source_per_cluster(self):
        """Codex verify 4: Storage from the live reads only - the run adds no feed, no aggregate, no condition; an empty node
        state is unknown (never up, never a condition: the area is not whole)"""
        m = self.live_and_db()
        st = [a for a in m["areas"] if a["id"] == "storage"][0]
        self.assertIn("ontap:cluster1.example.mil", st["feeds"])
        self.assertFalse([k for k in st["feeds"] if not k.startswith(("ontap:", "vc:"))])
        self.assertTrue(all(a["feed"].startswith("ontap:") for a in m["storage"]["aggr"]))
        self.assertTrue(all(c.get("feed", "").startswith(("ontap:", "vc:")) or c.get("feed") == "" for c in m["conds"]))
        self.assertFalse(any(c["id"].startswith("node:cluster1.example.mil:") for c in m["conds"]))
        self.assertIn("node n2: state '' not known", m["storage"]["incomplete"])
        lun = sorted(c["id"] for c in m["conds"] if c["kind"] == "voloff")
        self.assertEqual(lun, ["voloff:cluster1.example.mil:s1:/vol/v/l", "voloff:cluster1.example.mil:s2:/vol/v/l"])   # the SVM in the id
        self.assertTrue(all("age" not in f for f in m["feeds"].values()))   # no per-second age in the answer (the ETag)

class CodexModel(unittest.TestCase):
    """Codex 1.130.0 pre-release review: the model's part"""

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
        """the run says a node is down, the live read says up: only the live read counts (the run is never a condition)"""
        import netapp_live as nl
        db = demo.netapp_db(25)
        nm = nl.Database(lambda: db, mark="?").read()
        nm["parts"].setdefault("nodes", {"rows": []})["rows"] = [{"cluster": "cluster1", "node": "cluster1-01", "state": "down"}]
        v = demo.view(NOW, ontap=False)
        v["sources"]["ontap:cluster1.example.mil"] = {"kind": "ontap", "name": "cluster1.example.mil", "state": "ok", "why": "", "at": NOW - 20,
                                                      "snap": {"cluster": "cluster1", "nodes": [{"name": "cluster1-01", "state": "up"}], "aggrs": [],
                                                               "volumes": [], "luns": [], "snapmirror": []}}
        m = M.build(v, nm, sp.ad_when(nm["run"]["read_at"]), demo.OPTS, M.Tracker(), NOW)
        self.assertEqual([c["id"] for c in m["conds"] if c["kind"] == "node"], [])
        self.assertEqual(m["storage"]["nodes"], [1, 1])

    def test_nothing_read_nothing_on(self):
        m = M.build({"sources": {}, "history": {}}, None, None, {}, M.Tracker(), NOW)
        self.assertEqual([a["id"] for a in m["areas"] if a["on"]], [])


class CodexVerifyModel(unittest.TestCase):
    def test_a_name_never_clears_another_addresss_alert(self):
        """Codex verify 4: the run's "cluster not read" is never an alert on the wall (live only); a live cluster's own alert
        carries its own address"""
        import netapp_live as nl
        db = demo.netapp_db(25)
        nm = nl.Database(lambda: db, mark="?").read()
        nm["parts"]["clusters"] = {"rows": [{"name": "cluster1.example.mil", "why": "not read: timed out"}]}
        m0 = M.build(demo.view(NOW), nm, sp.ad_when(nm["run"]["read_at"]), demo.OPTS, M.Tracker(), NOW)
        self.assertFalse([c for c in m0["conds"] if c["kind"] == "node"])
        v = demo.view(NOW)
        v["sources"]["ontap:192.0.2.10"] = {"kind": "ontap", "name": "192.0.2.10", "state": "error", "why": "timed out", "at": None, "snap": None}
        m = M.build(v, nm, sp.ad_when(nm["run"]["read_at"]), demo.OPTS, M.Tracker(), NOW)
        self.assertEqual([c["id"] for c in m["conds"] if c["kind"] == "node"], ["node:192.0.2.10"])

    def test_checks_not_run(self):
        """the run's health parts never run a wall check; a live cluster's SnapMirror / nodes not read: not run"""
        import netapp_live as nl
        db = demo.netapp_db(5)
        nm = nl.Database(lambda: db, mark="?").read()
        self.assertEqual(model(demo.view(NOW, ontap=False), nm=nm, asof=NOW - 60)["storage"]["ran"], {"sm": False, "nodes": False})
        self.assertEqual(model(nm=nm, asof=NOW - 60)["storage"]["ran"], {"sm": True, "nodes": True})
        v = demo.view(NOW)
        v["sources"]["ontap:" + demo.NA1]["snap"].update(snapmirror=None, nodes=None)
        self.assertEqual(model(v)["storage"]["ran"], {"sm": False, "nodes": False})

class CodexVerify2Model(unittest.TestCase):
    def live(self, name, cl, state="up"):
        return {"kind": "ontap", "name": name, "state": "ok", "why": "", "at": NOW - 20,
                "snap": {"cluster": cl, "nodes": [{"name": cl + "-01", "state": state}], "aggrs": [], "volumes": [], "luns": [], "snapmirror": []}}

    def test_two_clusters_of_one_short_name_never_collide(self):
        v = demo.view(NOW, ontap=False)
        v["sources"]["ontap:array.west.example.mil"] = self.live("array.west.example.mil", "array", state="down")
        v["sources"]["ontap:array.east.example.mil"] = self.live("array.east.example.mil", "array")
        m = M.build(v, None, None, demo.OPTS, M.Tracker(), NOW)
        ids = [c["id"] for c in m["conds"] if c["kind"] == "node"]
        self.assertEqual(ids, ["node:array.west.example.mil:array-01"])        # each alert by its own address

    def test_unknown_numbers(self):
        v = demo.view(NOW)
        v["sources"]["ontap:c9"] = self.live("c9", "c9")
        v["sources"]["ontap:c9"]["snap"]["aggrs"] = [{"name": "a1", "node": "n", "size": None, "used": 5}, {"name": "a2", "node": "n", "size": 0, "used": None}]
        v["sources"]["vcenter:" + demo.VC1]["snap"]["datastores"][0]["free"] = None
        m = model(v)
        self.assertEqual([a["v"] for a in m["storage"]["aggr"] if a["c"] == "c9"], [None, None])          # never 0 %
        d = [x for x in m["vmware"]["datastores"] if x["n"] == "DS-SITEA-07"][0]
        self.assertIsNone(d["v"])                                                                       # never 100 %
        st = dict((a["id"], a) for a in m["areas"])
        self.assertFalse(st["storage"]["complete"])
        self.assertFalse(st["vmware"]["complete"])
        self.assertTrue(any("DS-SITEA-07" in x for x in st["vmware"]["incomplete"]))

    def test_a_failed_health_collection_is_not_run(self):
        v = demo.view(NOW)
        v["sources"]["ontap:" + demo.NA2]["snap"].update(snapmirror=None, failed={"snapmirror": "an error in the answer"})
        m = model(v)
        self.assertEqual(m["storage"]["ran"]["sm"], False)
        self.assertTrue(any("snapmirror not read whole" in x for x in m["storage"]["incomplete"]))

    def test_unread_lun_usage_is_never_zero_upstream(self):
        import site_ontap_expand as X
        import test_ontap_expand as TE
        V = TE.volume_module()                                  # the module with its module_utils as Ansible gives them
        GB = 1024 ** 3
        e = {"lun": "/vol/v/l", "lun_size": 100 * GB, "lun_used": None, "size": 1000 * GB, "used": 100 * GB, "svm": "s", "volume": "v",
             "address": "cluster1.example.mil", "luns": 1, "cluster": "c1"}           # Codex verify 3: 100 GB LUN, use unknown, 1000 GB volume
        self.assertEqual(X.lun_fullness(e), (100 * GB, None, "LUN"))                   # the LUN, its use unknown - never the volume's numbers
        self.assertEqual(X.fullness_text(e), "LUN 100.0 GB, use unknown")
        text = X.choice_text(e)
        self.assertIn("LUN 100.0 GB, use unknown", text)                               # not "LUN 1000.0 GB, 90% free"
        self.assertNotIn("free", X.choice_shown(text))
        sys.path.insert(0, os.path.join(ROOT, "plugins", "filter"))
        import ontap_expand_filters as EF
        full = dict(e, volume="v2", lun_used=95 * GB, lun_uuid="b")                   # a LUN 95 % written
        got = EF.ontap_expand_choices([full, e], [{}, {}])["choices"]
        self.assertIn("use unknown", X.choice_shown(got[0]))                           # use unknown: first, as the least free
        self.assertIsNone(V.opt_of({"space": {"size": 1}}, "space", "used"))
        self.assertEqual(V.opt_of({"space": {"used": 0}}, "space", "used"), 0)


class CodexVerify3Model(unittest.TestCase):
    TB = 1024 ** 4

    def nm(self, minutes=1):
        import netapp_live as nl
        db = demo.netapp_db(minutes)
        return nl.Database(lambda: db, mark="?").read()

    def live(self, v, name, at=NOW - 20, **snap):
        base = {"cluster": "cluster1", "nodes": [{"name": "cluster1-01", "state": "up"}], "aggrs": [], "volumes": [], "luns": [], "snapmirror": [],
                "failed": {}}
        base.update(snap)
        v["sources"]["ontap:" + name] = {"kind": "ontap", "name": name, "state": "ok", "why": "", "at": at, "snap": base}
        return v

    def test_an_old_live_read_never_hides_or_greens(self):
        """cluster1 read live 15 min ago, healthy; the run (newer) says its aggregate is 99 % full: the run is never used - and the
        live read is stale (never OK)"""
        nm = self.nm()
        for a in nm["aggrs"]:
            a["pct"] = 99.0
        v = self.live(demo.view(NOW, ontap=False), "cluster1.example.mil", at=NOW - 900)
        m = M.build(v, nm, NOW - 1, demo.OPTS, M.Tracker(), NOW)
        self.assertEqual(m["feeds"]["ontap:cluster1.example.mil"]["fresh"], "stale")
        self.assertEqual(m["storage"]["aggr"], [])
        self.assertFalse([c for c in m["conds"] if c["kind"].startswith("aggr")])

    def test_failed_or_unknown_live_checks(self):
        v = self.live(demo.view(NOW), "cluster1.example.mil", nodes=None, snapmirror=[{"src": "a:b", "dst": "c:d", "state": None, "healthy": None}],
                      volumes=[{"name": "v1", "svm": "s", "state": None}], failed={"nodes": "errors in the answer"})
        m = M.build(v, None, None, demo.OPTS, M.Tracker(), NOW)
        self.assertEqual(m["storage"]["ran"], {"sm": False, "nodes": False})
        inc = " ".join(m["storage"]["incomplete"])
        for word in ("nodes not read whole", "SnapMirror c:d", "volume v1: state not read"):
            self.assertIn(word, inc)
        self.assertFalse([c for c in m["conds"] if c["kind"] == "voloff"])                # unknown is not "offline" - and not "online"
        st = dict((a["id"], a) for a in m["areas"])["storage"]
        self.assertFalse(st["complete"])

    def test_storage_completeness_covers_its_sources(self):
        v = demo.view(NOW)
        v["sources"]["vcenter:" + demo.VC1]["snap"]["datastores"][0]["capacity"] = None   # a datastore of Storage not read
        v["sources"]["vcenter:" + demo.VC1]["snap"]["datastores"][1]["accessible"] = None  # one whose accessibility was not read
        v["sources"]["ontap:" + demo.NA1]["snap"]["aggrs"] = None                          # a live collection not read
        m = M.build(v, self.nm(), NOW - 60, demo.OPTS, M.Tracker(), NOW)
        st = dict((a["id"], a) for a in m["areas"])["storage"]
        inc = " ".join(m["storage"]["incomplete"])
        self.assertIn("datastore DS-SITEA-07", inc)
        self.assertIn("accessibility not read", inc)
        self.assertIn("aggregates not read", inc)
        self.assertFalse(st["complete"])
        self.assertEqual(sorted(k.split(":")[0] for k in st["feeds"]), ["ontap", "ontap", "vc", "vc"])   # never the run

    def test_vmware_unknowns(self):
        v = demo.view(NOW)
        snap = v["sources"]["vcenter:" + demo.VC1]["snap"]
        snap["vms"][0].update(cpu_pct=None, heartbeat=None)
        snap["hosts"][0].pop("maint")
        m = model(v)
        inc = " ".join(m["vmware"]["incomplete"])
        self.assertIn("CPU or memory not read", inc)
        self.assertIn("heartbeat not read", inc)
        self.assertIn("maintenance mode not read", inc)
        import wall_live as W
        sh = W.shape([("Datastore", "ds-1", {"name": "DS-1", "summary.capacity": "100", "summary.accessible": "true"}),
                      ("HostSystem", "host-1", {"name": "esx1", "runtime.connectionState": "connected"})])
        self.assertIsNone(sh["datastores"][0]["free"])                                     # not read: never 0 (a false 100 % full)
        self.assertIsNone(sh["hosts"][0]["maint"])
        d = [x for x in model({"sources": {"vcenter:x": {"kind": "vcenter", "name": "x", "state": "ok", "at": NOW, "snap": sh}}, "history": {}})["vmware"]["datastores"]][0]
        self.assertIsNone(d["v"])

    def test_loss_of_data_is_not_a_recovery(self):
        t = M.Tracker()
        v = demo.view(NOW, vc2_state="ok")
        m1 = M.build(v, None, None, demo.OPTS, t, NOW)
        down = [c["id"] for c in m1["conds"] if c["kind"] == "host"][0]
        v2 = demo.view(NOW + 60, vc2_state="ok")
        v2["sources"]["vcenter:" + demo.VC1].update(state="error", why="timed out")
        for h in v2["sources"]["vcenter:" + demo.VC1]["snap"]["hosts"]:
            h["state"] = ""                                                                # this read could not see the host
        m2 = M.build(v2, None, None, demo.OPTS, t, NOW + 60)
        c = [x for x in m2["conds"] if x["id"] == down][0]
        self.assertIsNone(c["end"])                                                        # still open, as last known
        self.assertTrue(c.get("last_known"))
        v3 = demo.view(NOW + 120, vc2_state="ok")
        for h in v3["sources"]["vcenter:" + demo.VC1]["snap"]["hosts"]:
            h.update(state="connected", cpu_pct=10.0, mem_pct=10.0)
        m3 = M.build(v3, None, None, demo.OPTS, t, NOW + 120)
        self.assertEqual([x["end"] for x in m3["conds"] if x["id"] == down], [NOW + 120])  # an observed OK clears it


class DbPathUnknown(unittest.TestCase):
    """Codex verify 3: the NetApp run's path - an aggregate use not read is never 0 % (payload, writer, reader)"""

    def test_aggregate_use_not_read(self):
        import test_netapp_live as T
        import ontap_live_filters as lf
        p, _t = T.payload(0)
        p["aggrs"][0]["used"] = None
        db = T.Lite()
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, microsecond=0)
        T.ldb.write(db, now, 9100, 58, p, "NetApp - expand a volume", "ontap_expand_volume", T.fx.OPTS)
        import netapp_live as nl
        m = nl.Database(lambda: db, mark="?").read()
        a = [x for x in m["aggrs"] if x["a"] == p["aggrs"][0]["name"] and x["addr"].lower() == p["aggrs"][0]["address"].lower()][0]
        self.assertEqual((a["pct"], a["free"], a["st"]), (None, None, "unknown"))
        lst = {"aggregates": [{"address": "c1", "cluster": "c", "name": "a1", "node": "n", "size": 100, "used": None}], "choices": [], "clusters": []}
        out = lf.ontap_live_payload({"list": lst, "choices": [], "found": [], "collected": {}, "health": False, "perf": False, "opts": {}})
        self.assertIsNone(out["aggrs"][0]["used"])


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
    def test_preview_banner(self):
        """portal_wall_preview (default true): the banner on the page, outside every style and the kiosk's hidden bars; off: gone"""
        _cfg, _p, call, _b = serve(self)
        page = call("/wall")[1]
        self.assertIn('<p class="wall-preview" role="note" id="wall-preview"><b>Preview</b> - a light may show OK when data is missing or late. '
                      'Check vCenter / NetApp before acting.</p><div id="wall"', page)
        css = open(os.path.join(FILES, "static", "wall-page.css"), encoding="utf-8").read()
        self.assertIn(".wall-preview{position:sticky", css)
        self.assertNotRegex(css, r"kiosk[^{}]*\.wall-preview")                       # never hidden in kiosk
        self.assertNotIn("wall-preview", open(os.path.join(FILES, "static", "wall-page.js"), encoding="utf-8").read())   # no script closes it
        _cfg, _p, call, _b = serve(self, PORTAL_WALL_PREVIEW="0")
        self.assertNotIn("wall-preview", call("/wall")[1])
        d = open(os.path.join(ROOT, "roles", "site_portal_stack", "defaults", "main.yml"), encoding="utf-8").read()
        self.assertIn("portal_wall_preview: true", d)

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

    def test_a_blank_settings_string_closes_the_feedback(self):
        cfg, p, call, base = serve(self, PORTAL_WALL_FEEDBACK_GROUPS="   ")
        self.assertFalse(sp.wall_may_feedback(cfg, JDOE_CN))
        self.assertEqual(post_feedback(call, base, portal=p)[0], 403)

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
    """Codex 1.130.0: the deploy copied site_portal.py but not wall_model.py - the portal could not start. Every module the service
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
        for st, mark in (("cockpit", 'class="sK"'), ("lights", 'class="sL"'), ("map", 'class="sM"'),
                         ("strata", 'class="sS"'), ("relay", 'class="sR"'), ("metro", 'class="sX"')):          # 1.135.0
            for w in (1920, 1280):
                out = self.dom("style=%s&freeze=1" % st, w)
                self.assertIn('data-ready="1"', out, (st, w))
                self.assertIn(mark, out, st)
                self.assertIn("sita01-esx03.corp.example.mil not responding", out, st)
        out = self.dom("style=cockpit&freeze=1")
        self.assertIn("Not on the wall yet", out)                                    # VDI, AD, Outages, Network: grey
        self.assertIn('data-d="ctr:hosts"', out)                                     # every counter opens a card

    def test_room_styles_show_what_the_wall_watches(self):
        """review r1 (Codex 7, Neon 7): ?area=watch in Strata, Relay and Metro is the watch list, not the board"""
        for st in ("strata", "relay", "metro"):
            out = self.dom("style=%s&area=watch&freeze=1" % st)
            self.assertIn('data-ready="1"', out, st)
            self.assertIn("What the wall watches", out, st)
            self.assertNotIn('class="wx wx-', out, st)
            self.assertNotRegex(out, r'<html[^>]*class="[^"]*\bscreen\b', st)

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
            for k in [k for k in v["sources"] if k.startswith("ontap:")]:
                v["sources"][k]["at"] = time.time() - 900                         # the live ONTAP reads old too
            v["history"] = {}
            return v
        url, stop = demo.portal(collector=demo.FakeCollector(stale_view), netapp_minutes_old=30)
        self.addCleanup(stop)
        for st in ("lights", "cockpit", "map", "strata", "relay", "metro"):
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
        """no live ONTAP cluster (and the report database's run there): Storage is not on the wall - nothing of it green"""
        url, stop = demo.portal(collector=demo.FakeCollector(lambda: demo.view(vc2_state="ok", ontap=False)))
        self.addCleanup(stop)
        for area in ("overview", "storage"):
            out = self.run_page(url, "style=cockpit&freeze=1&area=" + area)
            self.assertNotRegex(out, r'<div class="ctr ok"[^>]*data-d="ctr:(sm|nodes|aggr|ds)"')
            self.assertNotRegex(out, r'class="gauge s-ok"[^>]*data-d="aggr:')

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

    GREEN = re.compile(r'class="(?:word ok|wd ok|lamp s-ok|ctr ok|fl f-ok|val g-ok|ln g-ok|cell s-ok|brow s-ok|gauge s-ok|'
                       r'(?:sdot|sc|tag|nr|cd|sg) k-ok|'
                       r'(?:sx-tile|sx-stage|wr|crow|hq|tb|rx-bay|plate|cc|t|bb) ok)[" ]')       # 1.135.0: Strata, Relay, Metro

    def greens(self, out):
        return [m.group(0) for m in self.GREEN.finditer(out)]

    def test_never_green_walk(self):
        """every status element, every style, the areas and the detail cards, with the sources old / missing: none green"""
        def old_vc():
            v = demo.view(vc2_state="ok", ontap=False)
            for k in ("vcenter:" + demo.VC1, "vcenter:" + demo.VC2):
                v["sources"][k]["at"] = time.time() - 900
            return v
        cases = (("vCenter old, no NetApp", old_vc, False), ("nothing read", lambda: {"sources": {}, "history": {}}, False))
        refs = ("", "ctr:hosts", "ctr:sm", "ctr:nodes", "ctr:hot", "host:%s|sita01-esx02.corp.example.mil" % demo.VC1,
                "cl:%s|SITA01-CL02|hosts" % demo.VC1, "cl:%s|SITA01-CL02|cpu" % demo.VC1, "ds:%s|DS-SITEA-01" % demo.VC1, "feed:vc:" + demo.VC1)
        for name, fn, netapp in cases:
            url, stop = demo.portal(netapp=netapp, collector=demo.FakeCollector(fn))
            try:
                for st in ("lights", "cockpit", "map", "strata", "relay", "metro"):
                    for area in ("overview", "storage", "vmware"):
                        out = self.run_page(url, "style=%s&area=%s&freeze=1" % (st, area))
                        self.assertIn('data-ready="1"', out, (name, st, area))
                        self.assertEqual(self.greens(out), [], (name, st, area))
                for ref in refs[1:]:
                    out = self.run_page(url, "style=cockpit&freeze=1&open=" + urllib.request.quote(ref))
                    self.assertEqual(self.greens(out), [], (name, ref))
            finally:
                stop()

    def test_checks_not_run_are_grey(self):
        import test_netapp_live as T
        url, stop = demo.portal(collector=demo.FakeCollector(lambda: demo.view(vc2_state="ok")), netapp_minutes_old=1)
        self.addCleanup(stop)
        db = None
        out = self.run_page(url, "style=cockpit&freeze=1")
        self.assertNotRegex(out, r'<div class="ctr off"[^>]*title="SnapMirror health and lag')              # the run has the health parts
        def unread():                                          # the live reads: SnapMirror and nodes not read (their checks did not run)
            v = demo.view(vc2_state="ok")
            for k in [k for k in v["sources"] if k.startswith("ontap:")]:
                v["sources"][k]["snap"].update(snapmirror=None, nodes=None, failed={"snapmirror": "busy", "nodes": "busy"})
            return v
        url2, stop2 = demo.portal(collector=demo.FakeCollector(unread), netapp_minutes_old=1, health=False)
        self.addCleanup(stop2)
        for st, area in (("cockpit", "overview"), ("cockpit", "storage"), ("lights", "storage")):
            out = self.run_page(url2, "style=%s&area=%s&freeze=1" % (st, area))
            self.assertNotIn("None over threshold or unhealthy", out, (st, area))
            self.assertNotRegex(out, r'<div class="ctr ok"[^>]*data-d="ctr:(sm|nodes)"', (st, area))
        out = self.run_page(url2, "style=cockpit&freeze=1&open=ctr:sm")
        self.assertEqual(self.greens(out.split('id="dsCard"', 1)[1]), [])

    def test_a_host_state_not_read_is_never_all_connected(self):
        def view():
            v = demo.view(vc2_state="ok")
            v["sources"]["vcenter:" + demo.VC1]["snap"]["hosts"][2]["state"] = ""            # esx03: no state (and not down any more)
            return v
        url, stop = demo.portal(collector=demo.FakeCollector(view))
        self.addCleanup(stop)
        out = self.run_page(url, "style=cockpit&freeze=1")
        self.assertNotIn("all hosts connected", out)
        self.assertRegex(out, r'<div class="ctr stale"[^>]*data-d="ctr:hosts"')

    def test_aggregate_card_links_by_vcenter_and_name(self):
        def view():
            v = demo.view(vc2_state="ok")
            d7 = [d for d in v["sources"]["vcenter:" + demo.VC1]["snap"]["datastores"] if d["name"] == "DS-SITEA-07"][0]
            v["sources"]["vcenter:" + demo.VC2]["snap"]["datastores"] = [dict(d7, free=d7["capacity"] // 2)]
            snap = demo.ontap_snap("cluster2.example.mil")
            snap["aggrs"] = [{"name": "aggr2_c2n2", "node": "cluster2-02", "size": 10 * demo.TB, "used": 5 * demo.TB}]
            v["sources"]["ontap:cluster2.example.mil"] = {"kind": "ontap", "name": "cluster2.example.mil", "state": "ok", "why": "",
                                                         "at": time.time() - 20, "snap": snap}
            return v
        url, stop = demo.portal(collector=demo.FakeCollector(view))
        self.addCleanup(stop)
        out = self.run_page(url, "style=cockpit&freeze=1&open=" + urllib.request.quote("aggr:ontap:cluster2.example.mil|aggr2_c2n2"))
        self.assertIn('data-d="ds:%s|DS-SITEA-07"' % demo.VC1, out)
        self.assertIn('data-d="ds:%s|DS-SITEA-07"' % demo.VC2, out)

    def test_path_card_unread_fill_is_not_ok(self):
        url, stop = demo.portal(collector=demo.FakeCollector(lambda: demo.view(vc2_state="ok")), netapp_minutes_old=1)   # a current run
        self.addCleanup(stop)
        out = self.run_page(url, "style=cockpit&freeze=1&open=" + urllib.request.quote("ds:%s|DS-SITEA-03" % demo.VC1))
        card = out.split('id="dsCard"', 1)[1]
        for row in ("<b>LUN</b>", "<b>Volume</b>"):                                            # VMFS: the run has the datastore's fill only
            part = card.split(row, 1)[1].split('class="lr', 1)[0]
            self.assertIn("the LUN's own fill is not in the refresh" if row == "<b>LUN</b>" else "size ", part)
            self.assertNotIn('class="wd ', part, row)                                          # information: no status word at all

    def test_path_card_an_old_live_aggregate_is_not_ok(self):
        def view():
            v = demo.view(vc2_state="ok")
            TB = 1024 ** 4
            v["sources"]["ontap:cluster2.example.mil"] = {"kind": "ontap", "name": "cluster2.example.mil", "state": "error", "why": "timed out",
                                                         "at": time.time() - 900,
                                                         "snap": {"cluster": "cluster2", "nodes": [], "volumes": [], "luns": [], "snapmirror": [],
                                                                  "aggrs": [{"name": "aggr2_c2n2", "node": "cluster2-02", "size": 10 * TB, "used": 5 * TB}]}}
            return v
        url, stop = demo.portal(collector=demo.FakeCollector(view), netapp_minutes_old=1)     # the run (the path) current, the live read old
        self.addCleanup(stop)
        out = self.run_page(url, "style=cockpit&freeze=1&open=" + urllib.request.quote("ds:%s|DS-SITEA-07" % demo.VC1))
        part = out.split('id="dsCard"', 1)[1].split("NetApp · ", 1)[1]
        # Codex verify 4: the path card's NetApp rows are the report database's - information, labelled with their age, never a status
        self.assertIn("from the NetApp refresh", part.split("</div>", 1)[0])
        self.assertNotIn('class="wd ', part.split('class="df"', 1)[0])
        self.assertNotRegex(part.split('class="df"', 1)[0], r'class="fl f-(ok|warn|crit)')

    def test_lights_down_hosts_first_never_hidden(self):
        def view():
            v = demo.view(vc2_state="ok")
            snap = v["sources"]["vcenter:" + demo.VC1]["snap"]
            for h in snap["hosts"]:
                if h["state"] == "notResponding":
                    h["name"] = "zz-down.corp.example.mil"                                     # last by name
            for i in range(150):
                snap["hosts"].append(demo.host("aa-maint%03d.corp.example.mil" % i, "SITA01-CL02", 10, 10, [], [], maint=True))
            return v
        url, stop = demo.portal(collector=demo.FakeCollector(view))
        self.addCleanup(stop)
        out = self.run_page(url, "style=lights&area=vmware&freeze=1")
        cells = re.findall(r'class="cell (s-[a-z]+)[^"]*"[^>]*data-tip="([^|"]+)', out)
        self.assertEqual(cells[0], ("s-crit", "zz-down.corp.example.mil"))
        self.assertNotIn("all connected", out)
        self.assertEqual(len([c for c in cells if c[0] == "s-warn"]), 150)                    # every warning shown, none hidden

    FUZZ_SEED = 1290
    FUZZ_RUNS = 40

    def fuzz_answers(self, seed, runs):
        """runs answers: the demo (or a quiet demo: nothing open) with one VMware input and one Storage input taken away, made
        unknown, aged LATE / STALE, or the source failing now with a recent good read (seeded)"""
        import random
        import netapp_live as nl
        rng = random.Random(seed)
        now = time.time()
        db = demo.netapp_db(1)
        base_nm = nl.Database(lambda: db, mark="?").read()
        out = []
        for i in range(runs):
            quiet = rng.random() < 0.5
            v = demo.view(now, vc2_state="ok")
            v["sources"]["vcenter:" + demo.VC2]["snap"] = demo.vcenter_snap()
            for h in v["sources"]["vcenter:" + demo.VC2]["snap"]["hosts"]:
                h["name"] = h["name"].replace("sit", "sitz")
            for k in ("vcenter:" + demo.VC1, "vcenter:" + demo.VC2):
                v["sources"][k]["snap"]["vms"] = [x for x in v["sources"][k]["snap"]["vms"] if x.get("cpu_pct") is not None]
            if quiet:                                   # nothing open: the Now strip, the empty timeline, the "None" lines are on show
                v["history"] = {}
                for k in ("vcenter:" + demo.VC1, "vcenter:" + demo.VC2):
                    for h in v["sources"][k]["snap"]["hosts"]:
                        h.update(state="connected", cpu_pct=30.0, mem_pct=40.0)
                    for x in v["sources"][k]["snap"]["vms"]:
                        x["heartbeat"] = "green"
                    for d in v["sources"][k]["snap"]["datastores"]:
                        d["free"] = d["capacity"] // 2
            snap = v["sources"]["vcenter:" + demo.VC1]["snap"]
            cut = rng.choice(["stale", "late", "never", "error_recent", "auth_recent", "host_state", "host_cpu", "host_mem", "host_maint",
                              "ds_cap", "ds_free", "ds_shared", "host_hardware", "vm_template", "vm_tools_seen", "vm_cpu", "vm_heartbeat"])
            src = v["sources"]["vcenter:" + rng.choice([demo.VC1, demo.VC2])]
            if cut == "stale":
                src["at"] = now - 900
            elif cut == "late":
                src["at"] = now - 300
            elif cut == "never":
                src.update(at=None, snap=None, state="never")
            elif cut in ("error_recent", "auth_recent"):    # failing now - its last good read only 20 s old
                src.update(at=now - 20, state="error" if cut == "error_recent" else "auth", why="timed out", fail_since=now - 5)
            elif cut.startswith("host"):
                h = rng.choice([x for x in snap["hosts"] if x["state"] == "connected"])
                key = {"host_state": "state", "host_cpu": "cpu_pct", "host_mem": "mem_pct", "host_maint": "maint", "host_hardware": "hardware"}[cut]
                if rng.random() < 0.5:
                    h.pop(key)
                else:
                    h[key] = None
            elif cut.startswith("vm"):
                vm = rng.choice([x for x in snap["vms"] if x.get("power") == "poweredOn"])
                vm[{"vm_cpu": "cpu_pct", "vm_heartbeat": "heartbeat", "vm_template": "template", "vm_tools_seen": "tools_seen"}[cut]] = None
            else:
                d = rng.choice([x for x in snap["datastores"] if x["shared"]])
                d[{"ds_cap": "capacity", "ds_free": "free", "ds_shared": "shared"}[cut]] = None
            # Storage: live ONTAP only (Codex verify 4) - the report database is information on the path card, never a cut that matters
            nm, at = json.loads(json.dumps(base_nm)), now - 60
            ons = [x for k, x in v["sources"].items() if k.startswith("ontap:")]
            if quiet:
                for x in ons:
                    for ag in x["snap"]["aggrs"]:
                        ag["used"] = int(ag["size"] * 0.4)
                    for r in x["snap"]["snapmirror"]:
                        r.update(state="snapmirrored", healthy=True)
            scut = rng.choice(["none", "stale", "late", "never", "error_recent", "auth_recent", "aggr_null", "aggr_size0", "node_state",
                               "sm_health", "sm_state", "sm_lag", "vol_state", "lun_state", "failed_sm", "failed_nodes", "failed_aggrs"])
            on = rng.choice(ons)
            osnap = on["snap"]
            if scut == "none":
                for k in [k for k in v["sources"] if k.startswith("ontap:")]:
                    del v["sources"][k]
            elif scut == "stale":
                on["at"] = now - 900
            elif scut == "late":
                on["at"] = now - 300
            elif scut == "never":
                on.update(at=None, snap=None, state="never")
            elif scut in ("error_recent", "auth_recent"):
                on.update(at=now - 20, state="error" if scut == "error_recent" else "auth", why="timed out", fail_since=now - 5)
            elif scut == "aggr_null":
                rng.choice(osnap["aggrs"])["used"] = None
            elif scut == "aggr_size0":
                rng.choice(osnap["aggrs"])["size"] = 0
            elif scut == "node_state":
                rng.choice(osnap["nodes"])["state"] = rng.choice([None, "booting", "unknown", ""])
            elif scut == "sm_lag":
                osnap["snapmirror"][0].update(healthy=True, state="snapmirrored", lag_s=rng.choice([None, -1, "unknown", False]))
            elif scut == "sm_health":
                osnap["snapmirror"][0].update(state="snapmirrored", healthy=rng.choice([None, "true", {}]))
            elif scut == "sm_state":
                osnap["snapmirror"][0].update(healthy=True, state=rng.choice([None, "paused", "gray", ""]))
            elif scut in ("vol_state", "lun_state"):
                rng.choice(osnap["volumes" if scut == "vol_state" else "luns"])["state"] = rng.choice([None, "gray", "never", ""])
            else:
                coll = {"failed_sm": "snapmirror", "failed_nodes": "nodes", "failed_aggrs": "aggrs"}[scut]
                osnap[coll] = None
                osnap["failed"] = {coll: "an error in the answer"}
            a = M.build(v, nm, at, demo.OPTS, M.Tracker(), now, enclave="SITEA", title="Ops center")
            a.update(zone="Europe/Berlin", poll=30, full={"storage": "/portal/netapp"}, auth_retry=900,
                     me={"feedback": True, "reader": False, "areas": [["general", "The wall"]], "max": 1000})
            out.append({"cut": "%s%s/%s" % ("quiet:" if quiet else "", cut, scut), "a": json.loads(json.dumps(a, default=str))})
        return out

    maxDiff = None

    def test_never_green_fuzz(self):
        """seeded fuzz: each run takes one VMware input and one Storage input away (or makes it unknown, old, or failing now);
        every style, every area and every kind of detail card is drawn and EVERY visible element's computed colours are read -
        nothing may be in an OK colour. On a failure the seed and the cut are said."""
        seed = int(os.environ.get("WALL_FUZZ_SEED") or self.FUZZ_SEED)
        answers = self.fuzz_answers(seed, self.FUZZ_RUNS)
        answers.append({"cut": "control", "control": True, "a": self.control_answer()})
        bad = self.fuzz_page(answers)
        self.assertIn("control: green seen", bad)                       # the scan sees green where green is right (it is not blind)
        bad.remove("control: green seen")
        self.assertEqual(bad, [], "green on missing / old inputs - seed %d (WALL_FUZZ_SEED=%d to replay)" % (seed, seed))

    def control_answer(self):
        """everything read, current and quiet: here the wall must show green"""
        now = time.time()
        v = demo.view(now, vc2_state="ok")
        v["sources"]["vcenter:" + demo.VC2]["snap"] = {"hosts": [], "clusters": [], "datastores": [], "vms": []}
        v["history"] = {}
        snap = v["sources"]["vcenter:" + demo.VC1]["snap"]
        for h in snap["hosts"]:
            h.update(state="connected", cpu_pct=30.0, mem_pct=40.0)
        for x in snap["vms"]:
            x["heartbeat"] = "green"
        for d in snap["datastores"]:
            d["free"] = d["capacity"] // 2
        for k in [k for k in v["sources"] if k.startswith("ontap:")]:
            for ag in v["sources"][k]["snap"]["aggrs"]:
                ag["used"] = int(ag["size"] * 0.4)
            for r in v["sources"][k]["snap"]["snapmirror"]:
                r.update(state="snapmirrored", healthy=True)
        a = M.build(v, None, None, demo.OPTS, M.Tracker(), now, enclave="SITEA", title="Ops center")
        a.update(zone="Europe/Berlin", poll=30, full={}, auth_retry=900, me={"feedback": False, "reader": False, "areas": [], "max": 1000})
        return json.loads(json.dumps(a, default=str))

    def scripted_page(self, answers, script, query="", budget=120000):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        import test_netapp_live as T
        cfg = T.cfg_for(tmp, PORTAL_WALL_TEST="1")
        html = sp.wall_page(cfg, JDOE_CN, {}).replace("/portal/static/", "")
        for n in ("wall-page.js", "wall-page.css", "wall-land.js", "portal.css", "portal-theme.js", "portal.js",
                  "wall-styles.js", "wall-styles.css", "wall-share.js", "portal-looks.css"):          # 1.135.0: the room's styles too
            shutil.copy(os.path.join(FILES, "static", n), d)
        open(os.path.join(d, "banner.css"), "w").close()
        with open(os.path.join(d, "ans.js"), "w") as fh:
            fh.write("window.ANS=%s;" % json.dumps(answers))
        with open(os.path.join(d, "stub.js"), "w") as fh:
            fh.write("(function(){window.fetch=function(){return Promise.resolve({status:200,ok:true,headers:{get:function(){return ''}},"
                     "json:function(){return Promise.resolve(JSON.parse(JSON.stringify(window.ANS[0].a)))}})}})();")
        with open(os.path.join(d, "t.js"), "w") as fh:
            fh.write(script)
        html = html.replace("<head>", '<head><script src="ans.js"></script><script src="stub.js"></script>', 1)
        html = html.replace("</body>", '<pre id="res" hidden></pre><script src="t.js"></script></body>', 1)
        with open(os.path.join(d, "p.html"), "w") as fh:
            fh.write(html)
        t0 = time.time()
        args = [browser(), "--headless=new", "--no-sandbox", "--disable-gpu", "--allow-file-access-from-files", "--window-size=1920,1080",
                "--virtual-time-budget=%d" % budget, "--dump-dom", "file://" + os.path.join(d, "p.html") + "?" + query]
        out = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True, timeout=400).stdout
        m = re.search(r'<pre id="res" hidden="">(.*?)</pre>', out, re.S)
        self.assertTrue(m and m.group(1), "the wall test script did not finish")
        self.assertLess(time.time() - t0, 120)
        return json.loads(sp.html.unescape(m.group(1)))

    def test_rotate_pauses_on_click_and_resumes_after_idle(self):
        snapshots = self.scripted_page([{"a": self.control_answer()}], """
window.addEventListener("load", async function () {
    const button = document.querySelector("#rotBtn");
    const status = () => button.querySelector("#rotCd").innerText;
    const style = () => document.querySelector("[data-style][aria-pressed=true]").dataset.style;
    const key = (target, key) => target.dispatchEvent(new KeyboardEvent("keydown", {key, bubbles: true}));
    const mouse = (target, type) => target.dispatchEvent(new MouseEvent(type, {bubbles: true}));
    const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
    const seen = {};

    mouse(document.body, "mousemove");
    document.body.dispatchEvent(new PointerEvent("pointermove", {bubbles: true}));
    key(document.body, "Shift");
    seen.afterMovement = status();
    key(document.body, "r");
    seen.popoverOpened = [button.getAttribute("aria-expanded"), status()];
    const every = document.querySelector("#rotEvery");
    mouse(every, "mousedown");
    key(every, "ArrowDown");
    every.value = "60";
    every.dispatchEvent(new Event("change", {bubbles: true}));
    seen.settingChanged = status();
    every.value = "30";
    every.dispatchEvent(new Event("change", {bubbles: true}));
    mouse(button, "mousedown");
    button.click();
    seen.popoverClosed = [button.getAttribute("aria-expanded"), status()];

    document.body.click();
    seen.afterClick = status();
    await wait(19000);
    mouse(document.body, "mousemove");
    seen.beforeResume = status();
    seen.styleDuringPause = style();
    await wait(2100);
    seen.afterResume = status();
    seen.styleAfterResume = style();
    await wait(30000);
    seen.nextStyle = style();
    document.querySelector("#res").textContent = JSON.stringify(seen);
});""", query="rotate=styles&style=lights&every=30", budget=55000)
        self.assertEqual(snapshots["afterMovement"], "next in 30 s")
        self.assertEqual(snapshots["popoverOpened"], ["true", "next in 30 s"])
        self.assertEqual(snapshots["settingChanged"], "next in 60 s")
        self.assertEqual(snapshots["popoverClosed"], ["false", "next in 30 s"])
        self.assertEqual(snapshots["afterClick"], "paused - resumes in 20 s")
        self.assertRegex(snapshots["beforeResume"], r"^paused - resumes in [12] s$")
        self.assertEqual(snapshots["styleDuringPause"], "lights")
        self.assertRegex(snapshots["afterResume"], r"^next in (29|30) s$")
        self.assertEqual(snapshots["styleAfterResume"], "lights")
        self.assertEqual(snapshots["nextStyle"], "cockpit")

    def test_kiosk_rotation_ignores_pointer_input(self):
        snapshots = self.scripted_page([{"a": self.control_answer()}], """
window.addEventListener("load", function () {
    const status = () => document.querySelector("#rotCd").innerText;
    for (const type of ["mousemove", "pointermove", "mousedown", "click", "wheel", "touchstart"]) {
        document.body.dispatchEvent(new Event(type, {bubbles: true}));
    }
    const seen = [status()];
    document.body.dispatchEvent(new KeyboardEvent("keydown", {key: "a", bubbles: true}));
    seen.push(status());
    document.querySelector("#res").textContent = JSON.stringify(seen);
});""", query="kiosk=1&rotate=styles&every=30", budget=2000)
        self.assertEqual(snapshots, ["next in 30 s", "paused - resumes in 20 s"])

    def fuzz_page(self, answers):
        refs = ["ctr:hardware", "kind:vmhung", "ctr:hosts", "ctr:vc", "ctr:dsi", "ctr:hung", "ctr:hot", "ctr:maint", "ctr:sm", "ctr:nodes", "ctr:ds", "ctr:aggr", "grp:sm", "grp:hostvc",
                "grp:ds", "grp:dsvm", "grp:load", "grp:aggr", "feed:vc:" + demo.VC1, "feed:ontap", "feed:netapp-run"]
        return self.scripted_page(answers, """setTimeout(function(){var T=window.__wallTest,bad=[],add=function(p,r,map){
   if(r.scanned<40)bad.push(p+': only '+r.scanned+' elements drawn');
   if(map&&(!r.sdots||!r.ticks))bad.push(p+': the map or the timeline was not drawn');
   r.greens.forEach(function(g){bad.push(p+': '+g)})};
var ctl=0;
window.ANS.forEach(function(x,i){var a=x.a,R=%s.slice();
 if(x.control){T.set(JSON.parse(JSON.stringify(a)));['lights','cockpit','map','strata','relay','metro'].forEach(function(st){T.view(st,'vmware');ctl+=T.greens().greens.length});
   if(ctl)bad.push('control: green seen');return;}
 (a.vmware.hosts.slice(0,3)).forEach(function(h){R.push('host:'+h.vc+'|'+h.n)});
 (a.vmware.clusters.slice(0,2)).forEach(function(c){R.push('cl:'+c.vc+'|'+c.n+'|hosts');R.push('cl:'+c.vc+'|'+c.n+'|cpu')});
 (a.storage.aggr.slice(0,2)).forEach(function(g){R.push('aggr:'+g.ref)});
 (a.storage.ds.slice(0,3)).forEach(function(s){R.push('ds:'+(s.vc?s.vc+'|':'')+s.n)});
 T.set(JSON.parse(JSON.stringify(a)));
 ['lights','cockpit','map','strata','relay','metro'].forEach(function(st){['overview','storage','vmware'].forEach(function(ar){T.view(st,ar);
   if(['strata','relay','metro'].indexOf(st)>=0&&!document.querySelector('.wx-'+st))bad.push(i+' '+st+'/'+ar+': the board was not drawn');
   add(i+' '+x.cut+' '+st+'/'+ar,T.greens(),st==='map')})});
 T.view('cockpit','overview');
 R.forEach(function(r){T.open(r);add(i+' '+x.cut+' card '+r,T.greens(),false)});
});
document.getElementById('res').textContent=JSON.stringify(bad.slice(0,40));},1200);""" % json.dumps(refs))

    def test_red_beats_grey(self):
        """one cluster's SnapMirror check failed, another's relationship is confirmed broken: the counter is red, not grey"""
        v = demo.view(vc2_state="ok")
        for name, sm in (("c1.example.mil", None), ("c2.example.mil", [{"src": "a:b", "dst": "c:d", "state": "broken_off", "healthy": False}])):
            v["sources"]["ontap:" + name] = {"kind": "ontap", "name": name, "state": "ok", "why": "", "at": time.time() - 10,
                                             "snap": {"cluster": name.split(".")[0], "nodes": [], "aggrs": [], "volumes": [], "luns": [],
                                                      "snapmirror": sm, "failed": {} if sm else {"snapmirror": "busy"}}}
        m = M.build(v, None, None, demo.OPTS, M.Tracker(), time.time())
        self.assertFalse(m["storage"]["ran"]["sm"])                                        # one cluster's check did not run ...
        url, stop = demo.portal(netapp=False, collector=demo.FakeCollector(lambda: v))
        self.addCleanup(stop)
        out = self.run_page(url, "style=cockpit&freeze=1&area=storage")
        self.assertRegex(out, r'<div class="ctr warn"[^>]*data-d="ctr:sm"')              # ... the other's broken one still shows

    def test_a_feed_missing_from_the_answer(self):
        a = self.control_answer()
        a["areas"][1]["feeds"].append("vc:vc09.corp.example.mil")                         # listed, but not in the answer's feeds
        a["areas"][0].pop("complete", None)                                               # and "complete" missing
        bad = self.fuzz_page([{"cut": "missing feed", "a": a}])
        self.assertEqual(bad, [])                                                         # drawn whole (no throw), and nothing green

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
