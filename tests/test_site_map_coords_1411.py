#!/usr/bin/env python3
"""1.141.1: Unit & UI tests for Site map coordinate overrides (portal_site_map_coords):
override wins over SolarWinds, case-insensitive match, bad coords ignored+warned (never crash),
unknown site reported, coordinate source (settings / SolarWinds / none) in details & list view,
portal env wiring, all.yml example, docs, and CHANGELOG. Placeholders only (SITEA/SITEB, 192.0.2.x)."""
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

import yaml

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

NOW = 1_800_000_000.0


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return fh.read()


def sample_swis(now=NOW):
    return {
        "read_at": now - 20,
        "url": "https://swis.corp.example.mil:17774",
        "cut": False,
        "nodes": [
            {"id": 1, "name": "SITEA-RTR01.corp.example.mil", "ip": "192.0.2.1",
             "status_code": 1, "status_text": "Up", "vendor": "Cisco", "model": "ISR4451",
             "location": "SITEA, Bldg 1", "resp": 20.0, "loss": 0.0, "unmanaged": False,
             "last_sync": now - 30, "props": {"Lat": 10.0, "Lon": 20.0}, "wm": [38.895, -77.036]},
            {"id": 2, "name": "SITEB-RTR01.corp.example.mil", "ip": "192.0.2.11",
             "status_code": 1, "status_text": "Up", "vendor": "Cisco", "model": "ISR4451",
             "location": "SITEB, Core", "resp": 35.0, "loss": 0.0, "unmanaged": False,
             "last_sync": now - 30, "props": {}, "wm": [50.110, 8.682]},
            {"id": 3, "name": "SITEC-RTR01.corp.example.mil", "ip": "192.0.2.21",
             "status_code": 1, "status_text": "Up", "vendor": "Cisco", "model": "ISR4451",
             "location": "SITEC, Closet 3", "resp": 18.0, "loss": 0.0, "unmanaged": False,
             "last_sync": now - 30, "props": {}, "wm": [0.0, 0.0]},
            {"id": 4, "name": "SITED-RTR01.corp.example.mil", "ip": "192.0.2.31",
             "status_code": 1, "status_text": "Up", "vendor": "Cisco", "model": "ISR4451",
             "location": "SITED, Room 4", "resp": 22.0, "loss": 0.0, "unmanaged": False,
             "last_sync": now - 30, "props": {}, "wm": None},
        ],
        "volumes_read": True, "volumes": [],
        "interfaces_read": True, "interfaces": [],
        "connections": [[1, 100, 2, 200]],
        "topology": "Orion.TopologyConnections", "topology_ok": True,
        "groups": {1: ["Core"], 2: ["Core"], 3: ["Edge"], 4: ["Edge"]},
        "not_read": {},
    }


class SiteMapCoordsModelTests(unittest.TestCase):
    def test_override_wins_over_solarwinds_and_places_unplaced_site(self):
        snap = sample_swis(NOW)
        sm = wh.build_sitemap(
            snap, {"state": "ok", "asof": NOW - 10}, {}, wh.HealthTracker(), NOW,
            netmap_opts={"site_by": "location", "lat_property": "Lat", "lon_property": "Lon"},
            site_places={"SITEA": [-100.0, 40.0]},
            site_map_coords={"SITEA": [39.4, -93.2], "SITEC": [34.9, -112.8]},
        )
        by = {s["name"]: s for s in sm["sites"]}
        # Override wins over SolarWinds WorldMapPoint, CustomProperty, and site_places
        self.assertTrue(by["SITEA"]["placed"])
        self.assertEqual((by["SITEA"]["lat"], by["SITEA"]["lon"]), (39.4, -93.2))
        self.assertEqual(by["SITEA"]["coord_source"], "portal_site_map_coords")
        self.assertEqual(by["SITEA"]["coord_source_kind"], "settings")
        self.assertEqual(by["SITEA"]["unplaced_why"], "")
        # Null Island site placed cleanly by override
        self.assertTrue(by["SITEC"]["placed"])
        self.assertEqual((by["SITEC"]["lat"], by["SITEC"]["lon"]), (34.9, -112.8))
        self.assertEqual(by["SITEC"]["coord_source_kind"], "settings")
        self.assertEqual(by["SITEC"]["unplaced_why"], "")
        # Non-overridden sites keep SolarWinds / none
        self.assertEqual((by["SITEB"]["lat"], by["SITEB"]["lon"]), (50.11, 8.682))
        self.assertEqual(by["SITEB"]["coord_source"], "Orion.WorldMap.Point")
        self.assertEqual(by["SITEB"]["coord_source_kind"], "SolarWinds")
        self.assertFalse(by["SITED"]["placed"])
        self.assertEqual(by["SITED"]["coord_source_kind"], "none")
        self.assertEqual(sm["warnings"], [])
        self.assertEqual(sm["unknown_coord_sites"], [])

    def test_case_insensitive_match_and_duplicate_case_warning(self):
        snap = sample_swis(NOW)
        sm = wh.build_sitemap(
            snap, {"state": "ok", "asof": NOW - 10}, {}, wh.HealthTracker(), NOW,
            site_map_coords={"sitea": [39.4, -93.2], "SiteB": [51.5, -0.12]},
        )
        by = {s["name"]: s for s in sm["sites"]}
        self.assertEqual((by["SITEA"]["lat"], by["SITEA"]["lon"]), (39.4, -93.2))
        self.assertEqual(by["SITEA"]["coord_source_kind"], "settings")
        self.assertEqual((by["SITEB"]["lat"], by["SITEB"]["lon"]), (51.5, -0.12))
        self.assertEqual(by["SITEB"]["coord_source_kind"], "settings")
        self.assertEqual(sm["warnings"], [])
        self.assertEqual(sm["unknown_coord_sites"], [])

        # Duplicate case-insensitive keys: keeps first, warns clearly
        sm_dup = wh.build_sitemap(
            snap, {"state": "ok", "asof": NOW - 10}, {}, wh.HealthTracker(), NOW,
            site_map_coords={"SITEA": [39.4, -93.2], "sitea": [40.0, -75.0]},
        )
        by_dup = {s["name"]: s for s in sm_dup["sites"]}
        self.assertEqual((by_dup["SITEA"]["lat"], by_dup["SITEA"]["lon"]), (39.4, -93.2))
        self.assertTrue(any("more than once" in w and "sitea" in w for w in sm_dup["warnings"]))

    def test_bad_coords_ignored_and_warned_never_crashes(self):
        snap = sample_swis(NOW)
        bad_map = {
            "SITEA": [95.0, 10.0],              # lat out of range -> falls back to SolarWinds
            "SITEB": [0, 0],                    # Null Island -> falls back to SolarWinds
            "SITEC": ["not-a-lat", -77.0],      # non-numeric -> stays unplaced
            "SITED": "39.4, -93.2",             # not a [lat, lon] pair -> stays unplaced
            "BAD_LEN": [39.4],                  # wrong length
            "BAD_LON": [10.0, -181.0],          # lon out of range
            "BAD_BOOL": [True, 10.0],           # bool rejected by valid_coord
            "BAD_NAN": [float("nan"), 10.0],    # NaN rejected by valid_coord
            "BAD_DICT": {"lat": 39.4, "lon": -93.2},
            "BAD_NONE": None,
            "": [39.4, -93.2],                  # blank site name
        }
        sm = wh.build_sitemap(
            snap, {"state": "ok", "asof": NOW - 10}, {}, wh.HealthTracker(), NOW,
            site_map_coords=bad_map,
        )
        by = {s["name"]: s for s in sm["sites"]}
        # SITEA and SITEB fall back to valid SolarWinds coordinates
        self.assertEqual((by["SITEA"]["lat"], by["SITEA"]["lon"]), (38.895, -77.036))
        self.assertEqual(by["SITEA"]["coord_source_kind"], "SolarWinds")
        self.assertEqual((by["SITEB"]["lat"], by["SITEB"]["lon"]), (50.11, 8.682))
        self.assertEqual(by["SITEB"]["coord_source_kind"], "SolarWinds")
        # SITEC and SITED remain unplaced (never crashed, never dropped)
        self.assertFalse(by["SITEC"]["placed"])
        self.assertEqual(by["SITEC"]["coord_source_kind"], "none")
        self.assertFalse(by["SITED"]["placed"])
        self.assertEqual(by["SITED"]["coord_source_kind"], "none")
        # Every bad entry produced a plain warning
        joined = "\n".join(sm["warnings"])
        self.assertEqual(len(sm["warnings"]), len(bad_map))
        for needle in ("SITEA", "out of valid range", "SITEB", "Null Island", "SITEC",
                       "SITED", "expected [lat, lon]", "BAD_LEN", "BAD_LON", "BAD_BOOL",
                       "BAD_NAN", "BAD_DICT", "BAD_NONE", "blank site name"):
            self.assertIn(needle, joined)

        # Malformed top-level values (bad JSON string, list, int) never crash and warn
        for bad_top in ("{bad json", ["SITEA", 1, 2], 42):
            sm_bad = wh.build_sitemap(snap, {"state": "ok", "asof": NOW - 10}, {}, wh.HealthTracker(), NOW,
                                      site_map_coords=bad_top)
            self.assertTrue(sm_bad["warnings"], bad_top)
            self.assertEqual(len(sm_bad["sites"]), 4)

    def test_unknown_site_in_setting_reported(self):
        snap = sample_swis(NOW)
        sm = wh.build_sitemap(
            snap, {"state": "ok", "asof": NOW - 10}, {}, wh.HealthTracker(), NOW,
            site_map_coords={"sitea": [39.4, -93.2], "SITEZ": [35.0, -80.0], "GhostSite": [48.8, 2.3]},
        )
        self.assertEqual(sm["unknown_coord_sites"], ["SITEZ", "GhostSite"])
        joined = "\n".join(sm["warnings"])
        self.assertIn("SITEZ", joined)
        self.assertIn("GhostSite", joined)
        self.assertIn("unknown site", joined)
        self.assertNotIn("sitea", joined)

        # Before SolarWinds is read (no nodes yet), valid sites are NOT falsely flagged as unknown,
        # while invalid coordinates are still warned immediately.
        sm_empty = wh.build_sitemap(
            None, {"state": "never", "why": "not read"}, {}, wh.HealthTracker(), NOW,
            site_map_coords={"SITEZ": [35.0, -80.0], "BAD": [999, 0]},
        )
        self.assertEqual(sm_empty["unknown_coord_sites"], [])
        self.assertEqual(len(sm_empty["warnings"]), 1)
        self.assertIn("BAD", sm_empty["warnings"][0])


class EnvAndDeployWiringTests(unittest.TestCase):
    def cfg(self, **env):
        base = {"PORTAL_DATA": "/tmp/sa-1411-test", "PORTAL_REPORTS": "/tmp", "PORTAL_ACTIONS": "/nonexistent",
                "PORTAL_AAP_URL": "http://127.0.0.1:9", "PORTAL_AAP_TOKEN": "x"}
        base.update(env)
        return sp.Config(base)

    def test_portal_config_and_healthboard_wire_site_map_coords(self):
        # JSON string in env var (as from container env)
        c_json = self.cfg(PORTAL_SITE_MAP_COORDS=json.dumps({"sitea": [39.4, -93.2], "SITEZ": [35.0, -80.0]}))
        self.assertEqual(c_json.site_map_coords, {"sitea": [39.4, -93.2], "SITEZ": [35.0, -80.0]})
        self.assertEqual(c_json.site_map_coords_warnings, [])

        # Native dict (as from settings.json)
        c_dict = self.cfg(PORTAL_SITE_MAP_COORDS={"SITEA": [39.4, -93.2]})
        self.assertEqual(c_dict.site_map_coords, {"SITEA": [39.4, -93.2]})

        # Bad JSON / non-mapping never crashes Config and surfaces a plain warning on /api/sitemap
        c_bad = self.cfg(PORTAL_SITE_MAP_COORDS="{not-json")
        self.assertEqual(c_bad.site_map_coords, {})
        self.assertTrue(any("invalid JSON" in w for w in c_bad.site_map_coords_warnings))

        c_list = self.cfg(PORTAL_SITE_MAP_COORDS=["SITEA"])
        self.assertEqual(c_list.site_map_coords, {})
        self.assertTrue(any("mapping" in w for w in c_list.site_map_coords_warnings))

        # End-to-end through HealthBoard.answer_sitemap()
        hb = wh.HealthBoard(c_json, None)
        hb.swis_snap = sample_swis(NOW)
        hb.swis_meta = {"state": "ok", "asof": NOW - 10, "why": ""}
        code, body, etag = hb.answer_sitemap(now=NOW)
        self.assertEqual(code, 200)
        self.assertTrue(etag)
        doc = json.loads(body)
        by = {s["name"]: s for s in doc["sites"]}
        self.assertEqual((by["SITEA"]["lat"], by["SITEA"]["lon"]), (39.4, -93.2))
        self.assertEqual(by["SITEA"]["coord_source_kind"], "settings")
        self.assertEqual(doc["unknown_coord_sites"], ["SITEZ"])
        self.assertTrue(any("SITEZ" in w for w in doc["warnings"]))

        hb_bad = wh.HealthBoard(c_bad, None)
        hb_bad.swis_snap = sample_swis(NOW)
        hb_bad.swis_meta = {"state": "ok", "asof": NOW - 10, "why": ""}
        _, bad_body, _ = hb_bad.answer_sitemap(now=NOW)
        bad_doc = json.loads(bad_body)
        self.assertTrue(any("invalid JSON" in w for w in bad_doc["warnings"]))

    def test_defaults_tasks_all_yml_docs_changelog_and_csp(self):
        defaults = read(ROLE, "defaults", "main.yml")
        tasks = read(ROLE, "tasks", "main.yml")
        all_yml = read(ROOT, "playbooks", "group_vars", "all.yml")
        doc = read(ROOT, "docs", "SITE_PORTAL.md")
        ch = read(ROOT, "CHANGELOG.md")
        js = read(STATIC, "site-map.js")

        # 1.141.1 changelog header at top
        self.assertTrue(ch.startswith("# Changelog\n\n## 1.141.1 — 2026-11-28\n"))
        # Setting default and comment
        self.assertRegex(defaults, r"(?m)^portal_site_map_coords:\s*\{\}\s*#.*1\.141\.1")
        # Task validation and env wiring
        parsed_tasks = yaml.safe_load(tasks)
        settings, = [t["vars"]["_settings"] for t in parsed_tasks if "_settings" in t.get("vars", {})]
        self.assertIn("PORTAL_SITE_MAP_COORDS", settings)
        self.assertIn("portal_site_map_coords", settings["PORTAL_SITE_MAP_COORDS"])
        self.assertIn("portal_site_map_coords is mapping", tasks)
        # Commented example in playbooks/group_vars/all.yml with SITEA/SITEB placeholders (not live)
        self.assertRegex(all_yml, r"(?m)^#\s*portal_site_map_coords:")
        self.assertRegex(all_yml, r"(?m)^#\s+SITEA:\s*\[")
        self.assertRegex(all_yml, r"(?m)^#\s+SITEB:\s*\[")
        self.assertNotRegex(all_yml, r"(?m)^portal_site_map_coords:")
        # Docs section 'Correct a site's place on the map'
        self.assertIn("Correct a site's place on the map", doc)
        self.assertIn("portal_site_map_coords", doc)
        # Strict CSP on site-map.js
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("insertAdjacentHTML", js)
        self.assertNotIn("eval(", js)
        self.assertIn("1.141.1", js)
        self.assertEqual(wh.VERSION, "1.141.1")


@unittest.skipUnless(shutil.which("node"), "UI DOM tests need Node")
class SiteMapUiTests(unittest.TestCase):
    def test_list_view_details_drawer_side_list_and_warning_banner(self):
        sm = wh.build_sitemap(
            sample_swis(NOW), {"state": "ok", "asof": NOW - 10}, {}, wh.HealthTracker(), NOW,
            site_map_coords={"sitea": [39.4, -93.2], "SITEB": [99, 0], "SITEZ": [35.0, -80.0]},
        )
        payload = json.dumps(sm)
        harness = """
        const assert = require("node:assert/strict");
        class Element {
          constructor(tag) {
            this.tagName = String(tag || "div").toUpperCase();
            this.children = [];
            this.attrs = {};
            this.listeners = {};
            this.className = "";
            this.style = {};
            this.classList = {
              _s: new Set(),
              toggle(k, v) { if (v) this._s.add(k); else this._s.delete(k); },
              add(k) { this._s.add(k); },
              remove(k) { this._s.delete(k); },
              contains(k) { return this._s.has(k); }
            };
          }
          addEventListener(ev, fn) { (this.listeners[ev] ||= []).push(fn); }
          click() { (this.listeners.click || []).forEach(fn => fn({stopPropagation() {}, target: this})); }
          querySelector() { return null; }
          getAttribute(k) { return this.attrs[k] ?? null; }
          setAttribute(k, v) { this.attrs[k] = String(v); }
          get firstChild() { return this.children[0]; }
          appendChild(c) { this.children.push(c); return c; }
          removeChild(c) { this.children.splice(this.children.indexOf(c), 1); }
          findAll(pred, out = []) {
            if (pred(this)) out.push(this);
            for (const c of this.children) if (c instanceof Element) c.findAll(pred, out);
            return out;
          }
          get textContent() { return this.children.map(c => c instanceof Element ? c.textContent : String(c)).join(" "); }
        }
        const page = new Element("div");
        const drawerBack = new Element("div");
        const bodyKids = [];
        const document = {
          documentElement: new Element("html"),
          body: {appendChild(el) { bodyKids.push(el); }, contains: () => true},
          createElement: tag => new Element(tag),
          createElementNS: (ns, tag) => new Element(tag),
          createTextNode: s => String(s),
          addEventListener() {},
          getElementById: id => id === "site-map" ? page : (id === "sm-back" ? drawerBack : null)
        };
        const location = {search: "?view=list", href: "https://portal.corp.example.mil/portal/sitemap"};
        const window = {addEventListener() {}, WALL_LAND: []};
        function setInterval() { return 1; }
        function clearInterval() {}
        const DOC = """ + payload + """;
        function fetch() { return Promise.resolve({ok: true, status: 200, headers: {get: () => '"v1"'}, json: async () => DOC}); }
        const flush = () => new Promise(r => setImmediate(r));
        """
        probe = """
        (async () => {
          await flush();
          await flush();
          // 1. Plain settings warning banner rendered on page (bad coord SITEB + unknown site SITEZ)
          const warnBanner = page.findAll(e => e.className.includes("sm-coord-warn"))[0];
          assert.ok(warnBanner, "warning banner must render");
          assert.match(warnBanner.textContent, /SETTINGS WARNING/);
          assert.match(warnBanner.textContent, /SITEB.*out of valid range/);
          assert.match(warnBanner.textContent, /unknown site 'SITEZ'/);

          // 2. List view shows Source column with settings / SolarWinds / none
          const rows = page.findAll(e => e.tagName === "TR");
          assert.ok(rows.length >= 5, "header + 4 sites");
          assert.match(rows[0].textContent, /Source/);
          const rowBy = {};
          for (const tr of rows.slice(1)) {
            const first = tr.children[0]?.textContent.trim();
            rowBy[first] = tr;
          }
          assert.match(rowBy.SITEA.textContent, /39\\.400, -93\\.200/);
          assert.match(rowBy.SITEA.textContent, /\\bsettings\\b/);
          assert.match(rowBy.SITEB.textContent, /50\\.110, 8\\.682/);
          assert.match(rowBy.SITEB.textContent, /\\bSolarWinds\\b/);
          assert.match(rowBy.SITEC.textContent, /\\bnone\\b/);
          assert.match(rowBy.SITED.textContent, /\\bnone\\b/);

          // 3. Site details drawer shows Coordinate source for settings, SolarWinds, and none
          rowBy.SITEA.click();
          assert.match(drawerBack.textContent, /Coordinate source.*settings \\(portal_site_map_coords\\)/);
          rowBy.SITEB.click();
          assert.match(drawerBack.textContent, /Coordinate source.*SolarWinds \\(Orion\\.WorldMap\\.Point\\)/);
          rowBy.SITED.click();
          assert.match(drawerBack.textContent, /Coordinate source.*none/);

          // 4. Split view side list also shows settings / SolarWinds / none
          const splitBtn = page.findAll(e => e.tagName === "BUTTON" && e.textContent.trim() === "Split")[0];
          assert.ok(splitBtn, "Split button present");
          splitBtn.click();
          const sideItems = page.findAll(e => e.className.includes("sm-item "));
          const sideText = sideItems.map(e => e.textContent).join("\\n");
          assert.match(sideText, /SITEA.*settings/);
          assert.match(sideText, /SITEB.*SolarWinds/);
          assert.match(sideText, /SITED.*none/);
        })();
        """
        subprocess.run(["node", "-"], text=True, timeout=10, check=True,
                       input=harness + read(STATIC, "site-map.js") + "\n" + probe)


if __name__ == "__main__":
    unittest.main(verbosity=1)
