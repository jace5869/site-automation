#!/usr/bin/env python3
"""1.139.0: Unit tests for Network & AD health wall area, offline Site map (SolarWinds lat/lon),
wall_model integration, settings clamping, deploy wiring, CSP, and offline assets.
Placeholders only (SITEA, corp.example.mil, 192.0.2.x)."""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ROLE = os.path.join(ROOT, "roles", "site_portal_stack")
FILES = os.path.join(ROLE, "files")
STATIC = os.path.join(FILES, "static")
for p in (FILES, os.path.join(ROOT, "plugins", "module_utils"), os.path.join(ROOT, "plugins", "filter")):
    if p not in sys.path:
        sys.path.insert(0, p)

import site_portal as sp  # noqa: E402
import wall_health as wh  # noqa: E402
import wall_model as wm  # noqa: E402

NOW = 1_800_000_000.0


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return fh.read()


def sample_swis(now=NOW, down_ids=(), vol_pct=52.0, rtt=25.0, loss=0.0, if_err=0.0, if_util=35.0):
    nodes = [
        {"id": 1, "name": "SITEA-RTR01.corp.example.mil", "dns": "sitea-rtr01.corp.example.mil", "ip": "192.0.2.1",
         "status_code": 2 if 1 in down_ids else 1, "status_text": "Down" if 1 in down_ids else "Up",
         "vendor": "Cisco", "model": "ISR4451", "location": "SITEA, Bldg 1", "resp": rtt, "loss": loss,
         "unmanaged": False, "last_sync": now - 30, "props": {}, "wm": [38.895, -77.036]},
        {"id": 2, "name": "SITEA-SW01.corp.example.mil", "dns": "sitea-sw01.corp.example.mil", "ip": "192.0.2.2",
         "status_code": 2 if 2 in down_ids else 1, "status_text": "Down" if 2 in down_ids else "Up",
         "vendor": "Cisco", "model": "Cat9300", "location": "SITEA, Closet 2", "resp": 12.0, "loss": 0.0,
         "unmanaged": False, "last_sync": now - 30, "props": {}, "wm": [38.896, -77.035]},
        {"id": 3, "name": "SITEB-RTR01.corp.example.mil", "dns": "siteb-rtr01.corp.example.mil", "ip": "192.0.2.11",
         "status_code": 2 if 3 in down_ids else 1, "status_text": "Down" if 3 in down_ids else "Up",
         "vendor": "Cisco", "model": "ISR4451", "location": "SITEB, Core", "resp": 42.0, "loss": 0.0,
         "unmanaged": False, "last_sync": now - 30, "props": {}, "wm": [50.110, 8.682]},
    ]
    return {
        "read_at": now - 20, "url": "https://swis.corp.example.mil:17774", "nodes": nodes, "cut": False,
        "volumes_read": True,
        "volumes": [{"id": 10, "node_id": 1, "caption": "/var/log", "pct": vol_pct, "status": 1}],
        "interfaces_read": True,
        "interfaces": [
            {"id": 100, "node_id": 1, "name": "Gi0/0/0 WAN", "speed": 1_000_000_000, "status": 1, "oper_status": 1,
             "in_util": if_util, "out_util": if_util, "in_err": if_err * 360.0, "out_err": 0.0,
             "in_disc": 0.0, "out_disc": 0.0, "in_pps": 10.0, "out_pps": 0.0},
            {"id": 200, "node_id": 3, "name": "Gi0/0/0 WAN", "speed": 1_000_000_000, "status": 1, "oper_status": 1,
             "in_util": 20.0, "out_util": 20.0, "in_err": 0.0, "out_err": 0.0, "in_disc": 0.0, "out_disc": 0.0,
             "in_pps": 10.0, "out_pps": 0.0},
        ],
        "connections": [[1, 100, 3, 200], [1, 101, 2, 201]],
        "topology": "Orion.TopologyConnections", "topology_ok": True,
        "groups": {1: ["Network Core"], 2: ["Network Access"], 3: ["Network Core"]},
        "not_read": {},
    }


def sample_ad(now=NOW, dc2_read=True, prev_unread=(), lag_hours=0.25, failures=0):
    iso = lambda ts: __import__("datetime").datetime.fromtimestamp(ts, __import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "run_id": 42, "read_at": iso(now - 300), "job": 100, "template_id": 7, "status": "ok" if dc2_read and not failures else "warning",
        "dcs": [
            {"dc": "SITEA-DC01.corp.example.mil", "domain": "corp.example.mil", "site": "SITEA", "read": True, "repl_read": True, "status": "ok", "worst": ""},
            {"dc": "SITEB-DC01.corp.example.mil", "domain": "corp.example.mil", "site": "SITEB", "read": dc2_read, "repl_read": dc2_read,
             "status": "ok" if dc2_read else "critical", "worst": "" if dc2_read else "WinRM timed out"},
        ],
        "links": [
            {"frm": "SITEA-DC01.corp.example.mil", "to": "SITEB-DC01.corp.example.mil", "nc": "Domain",
             "last_success": iso(now - 300 - lag_hours * 3600), "failures": failures, "error": "1722" if failures else ""},
        ],
        "prev_unread": list(prev_unread),
    }


class HealthModelTests(unittest.TestCase):
    def test_missing_feeds_show_grey_not_read_never_green(self):
        m = wh.build_health_model(None, {"state": "never", "why": "no SolarWinds login"}, None,
                                  {"state": "never", "why": "no report database"}, {}, wh.HealthTracker(), NOW)
        self.assertEqual(m["state"], "stale")
        self.assertTrue(all(k["state"] == "stale" and k["value"] == "Not read" for k in m["kpis"]))
        feeds = {f["id"]: f for f in m["feeds"]}
        self.assertEqual(feeds["swis"]["fresh"], "not_read")
        self.assertIn("no SolarWinds login", feeds["swis"]["why"])
        self.assertEqual(feeds["adjob"]["fresh"], "not_read")

    def test_stale_feed_never_green(self):
        snap = sample_swis(NOW - 900)
        m = wh.build_health_model(snap, {"state": "ok", "asof": NOW - 900}, sample_ad(NOW), {"state": "ok"}, {}, wh.HealthTracker(), NOW)
        self.assertEqual(m["state"], "stale")
        nodes_kpi = [k for k in m["kpis"] if k["id"] == "nodes"][0]
        self.assertEqual(nodes_kpi["state"], "stale")
        self.assertEqual(nodes_kpi["value"], "Not read")

    def test_down_confirmation_and_dependent_child_suppression(self):
        tr = wh.HealthTracker()
        # First poll: router 1 & leaf switch 2 both down -> router waits for 2nd poll, leaf suppressed behind down parent
        m1 = wh.build_health_model(sample_swis(NOW, down_ids=(1, 2)), {"state": "ok", "asof": NOW - 60},
                                   sample_ad(NOW), {"state": "ok"}, {"down_polls": 2}, tr, NOW)
        self.assertEqual([c["id"] for c in m1["conds"] if c["bucket"] == "down"], [])
        self.assertGreaterEqual(m1["counts"]["suppressed"], 2)
        # Second poll: router 1 confirmed down; switch 2 still suppressed behind down parent router 1
        m2 = wh.build_health_model(sample_swis(NOW + 60, down_ids=(1, 2)), {"state": "ok", "asof": NOW + 40},
                                   sample_ad(NOW + 60), {"state": "ok"}, {"down_polls": 2}, tr, NOW + 60)
        down_ids = [c["id"] for c in m2["conds"] if c["bucket"] == "down"]
        self.assertEqual(down_ids, ["net:down:1"])
        self.assertTrue(any("dependent behind down parent" in s["why"] for s in m2["suppressed"]))

    def test_full_and_sustained_errors_suppress_blips_and_clear_on_ok(self):
        tr = wh.HealthTracker()
        # Short RTT/loss spike at t=0 -> suppressed; Volume 96% -> immediate Full crit
        m1 = wh.build_health_model(sample_swis(NOW, vol_pct=96.0, rtt=180.0, loss=4.0), {"state": "ok", "asof": NOW},
                                   sample_ad(NOW), {"state": "ok"}, {"sustained_minutes": 15}, tr, NOW)
        self.assertEqual([c["id"] for c in m1["conds"]], ["net:vol:10"])
        self.assertEqual(m1["conds"][0]["bucket"], "full")
        self.assertEqual(m1["conds"][0]["sev"], "crit")
        # Step forward across 15 min in 3-min intervals (within gap limit)
        for step in (180, 360, 540, 720, 900):
            m2 = wh.build_health_model(sample_swis(NOW + step, vol_pct=50.0, rtt=180.0, loss=4.0),
                                       {"state": "ok", "asof": NOW + step},
                                       sample_ad(NOW + step), {"state": "ok"}, {"sustained_minutes": 15}, tr, NOW + step)
        err_ids = sorted(c["id"] for c in m2["conds"] if c["bucket"] == "err")
        self.assertEqual(err_ids, ["net:loss:1", "net:rtt:1"])
        # Volume dropped to 50% on fresh read -> cleared from open conds and present in timeline
        self.assertNotIn("net:vol:10", [c["id"] for c in m2["conds"]])
        self.assertTrue(any(t["id"] == "net:vol:10" and t["end"] is not None for t in m2["timeline"]))

    def test_ad_two_check_confirmation_and_repl_lag(self):
        tr = wh.HealthTracker()
        # Single failed DC check -> suppressed
        m1 = wh.build_health_model(sample_swis(NOW), {"state": "ok", "asof": NOW - 10},
                                   sample_ad(NOW, dc2_read=False, prev_unread=()), {"state": "ok"}, {}, tr, NOW)
        self.assertNotIn("ad:dc:siteb-dc01.corp.example.mil", [c["id"] for c in m1["conds"]])
        self.assertTrue(any("1 failed AD check" in s["why"] for s in m1["suppressed"]))
        # Second consecutive failed DC check + 26h repl lag -> Critical DC down + Critical AD repl
        m2 = wh.build_health_model(sample_swis(NOW + 60), {"state": "ok", "asof": NOW + 50},
                                   sample_ad(NOW + 60, dc2_read=False, prev_unread=("siteb-dc01.corp.example.mil",), lag_hours=26.0, failures=5),
                                   {"state": "ok"}, {}, tr, NOW + 60)
        by_id = {c["id"]: c for c in m2["conds"]}
        self.assertEqual(by_id["ad:dc:siteb-dc01.corp.example.mil"]["sev"], "crit")
        self.assertEqual(by_id["ad:repl:sitea-dc01.corp.example.mil>siteb-dc01.corp.example.mil"]["sev"], "crit")
        self.assertEqual(len(m2["ad"]["dcs"]), 2)
        self.assertEqual(len(m2["ad"]["pairs"]), 1)

    def test_wall_model_backwards_compat_and_health_projection(self):
        # Default build without health keeps nothing on (test_wall.py compatibility)
        m0 = wm.build({"sources": {}, "history": {}}, None, None, {}, wm.Tracker(), NOW)
        self.assertEqual([a["id"] for a in m0["areas"] if a["on"]], [])
        # With health projected, health area and live feeds appear and summary respects stale
        hm = wh.build_health_model(sample_swis(NOW), {"state": "ok", "asof": NOW - 10}, sample_ad(NOW), {"state": "ok"}, {}, wh.HealthTracker(), NOW)
        m1 = wm.build({"sources": {}, "history": {}}, None, None, {}, wm.Tracker(), NOW, health=hm)
        self.assertIn("health", [a["id"] for a in m1["areas"]])
        s1 = wm.summary(m1, NOW)
        by_a = {a["id"]: a for a in s1["areas"]}
        self.assertEqual(by_a["health"]["state"], "ok")


class SiteMapModelTests(unittest.TestCase):
    def test_valid_coord_rejects_null_island_and_invalid(self):
        self.assertEqual(wh.valid_coord(38.895, -77.036)[:2], (True, (38.895, -77.036)))
        for bad in ((0, 0), (0.0, 0.0), (91, 10), (10, -181), (None, 10), ("nan", 10), (True, 10)):
            ok, coord, why = wh.valid_coord(*bad)
            self.assertFalse(ok, bad)
            self.assertIsNone(coord)
            self.assertTrue(why, bad)

    def test_placement_and_unplaced_sites_never_dropped(self):
        snap = sample_swis(NOW)
        snap["nodes"].extend([
            {"id": 4, "name": "SITEC-RTR01.corp.example.mil", "ip": "192.0.2.21", "status_code": 2, "status_text": "Down",
             "location": "SITEC, Bldg 4", "resp": None, "loss": 100.0, "unmanaged": False, "last_sync": NOW - 20, "props": {}, "wm": [0.0, 0.0]},
            {"id": 5, "name": "SITED-RTR01.corp.example.mil", "ip": "192.0.2.31", "status_code": 1, "status_text": "Up",
             "location": "SITED, Room 2", "resp": 20.0, "loss": 0.0, "unmanaged": False, "last_sync": NOW - 20, "props": {}, "wm": None},
        ])
        sm = wh.build_sitemap(snap, {"state": "ok", "asof": NOW - 10}, {}, wh.HealthTracker(), NOW)
        by = {s["name"]: s for s in sm["sites"]}
        self.assertTrue(by["SITEA"]["placed"])
        self.assertEqual(by["SITEA"]["coord_source"], "Orion.WorldMap.Point")
        self.assertTrue(by["SITEB"]["placed"])
        # Null Island (0, 0) rejected and site NOT dropped
        self.assertFalse(by["SITEC"]["placed"])
        self.assertEqual(by["SITEC"]["status"], "crit")
        self.assertIn("Null Island", by["SITEC"]["unplaced_why"])
        # Missing coords NOT dropped
        self.assertFalse(by["SITED"]["placed"])
        self.assertEqual(sm["counts"]["unplaced"], 2)
        self.assertEqual(len(sm["links"]), 1)
        self.assertTrue(any(r["name"] == "North America" for r in sm["regions"]))
        self.assertTrue(any(r["name"] == "Europe" for r in sm["regions"]))

    def test_stale_sitemap_never_green(self):
        sm = wh.build_sitemap(sample_swis(NOW - 900), {"state": "ok", "asof": NOW - 900}, {}, wh.HealthTracker(), NOW)
        self.assertEqual(sm["state"], "stale")
        self.assertTrue(all(s["status"] == "stale" for s in sm["sites"]))


class SettingsAndAssetsTests(unittest.TestCase):
    def cfg(self, **env):
        base = {"PORTAL_DATA": "/tmp/sa-139-test", "PORTAL_REPORTS": "/tmp", "PORTAL_ACTIONS": "/nonexistent",
                "PORTAL_AAP_URL": "http://127.0.0.1:9", "PORTAL_AAP_TOKEN": "x"}
        base.update(env)
        return sp.Config(base)

    def test_config_and_threshold_clamping(self):
        c = self.cfg()
        self.assertTrue(c.wall_health)
        self.assertTrue(c.site_map)
        self.assertTrue(c.my_access)
        c_off = self.cfg(PORTAL_WALL_HEALTH="0", PORTAL_SITE_MAP="0", PORTAL_MY_ACCESS="0")
        self.assertFalse(c_off.wall_health)
        self.assertFalse(c_off.site_map)
        self.assertFalse(c_off.my_access)
        # Invalid warn >= crit falls back to safe defaults
        o = wh.health_opts({"volume_warn": 98, "volume_crit": 90, "seconds": 5, "down_polls": 99})
        self.assertEqual((o["volume_warn"], o["volume_crit"], o["seconds"], o["down_polls"]), (85.0, 95.0, 30, 10))

    def test_defaults_tasks_docs_changelog_and_offline_csp(self):
        d = read(ROLE, "defaults", "main.yml")
        t = read(ROLE, "tasks", "main.yml")
        doc = read(ROOT, "docs", "SITE_PORTAL.md")
        ch = read(ROOT, "CHANGELOG.md")
        self.assertTrue(ch.startswith("# Changelog\n\n## 1.139.0 — 2026-11-27\n"))
        for setting in ("portal_wall_health", "portal_wall_health_seconds", "portal_wall_ad_repl_warn_hours",
                        "portal_wall_ad_repl_crit_hours", "portal_wall_volume_warn", "portal_wall_volume_crit",
                        "portal_wall_if_err_warn", "portal_wall_if_err_crit", "portal_wall_wan_util_warn",
                        "portal_wall_wan_util_crit", "portal_wall_wan_rtt_warn", "portal_wall_wan_rtt_crit",
                        "portal_wall_wan_loss_warn", "portal_wall_wan_loss_crit", "portal_wall_sustained_minutes",
                        "portal_wall_down_polls", "portal_site_map", "portal_my_access"):
            self.assertRegex(d, r"(?m)^%s:.*#" % re.escape(setting), setting)
            self.assertIn(setting, t, setting)
            self.assertIn(setting, doc, setting)
        for asset in ("wall_health.py", "my_access.py", "static/wall-health.js", "static/wall-health.css",
                      "static/site-map.js", "static/site-map.css", "static/my-access.js", "static/my-access.css"):
            self.assertTrue(os.path.isfile(os.path.join(FILES, asset)), asset)
            self.assertIn(asset, t, asset)
        # Strict CSP & offline guarantee (no innerHTML, no eval, no external URLs except SVG xmlns)
        for js_name in ("wall-health.js", "site-map.js", "my-access.js"):
            js = read(STATIC, js_name)
            self.assertNotIn("innerHTML", js, js_name)
            self.assertNotIn("insertAdjacentHTML", js, js_name)
            self.assertNotIn("eval(", js, js_name)
            self.assertNotRegex(js, r"https?://(?!www\.w3\.org/2000/svg)", js_name)
        # Site map bundles offline coastline via wall-land.js and defines tokens for all 10 looks
        sm_css = read(STATIC, "site-map.css")
        for skin in sp.SKIN_KEYS:
            self.assertIn(':root[data-skin="%s"]' % skin, sm_css, skin)
        self.assertTrue(os.path.isfile(os.path.join(STATIC, "wall-land.js")))


if __name__ == "__main__":
    unittest.main(verbosity=1)
