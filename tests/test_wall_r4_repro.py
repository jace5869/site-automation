#!/usr/bin/env python3
"""Round-4 counterexamples. Run with python3 tests/test_wall_r4_repro.py -v."""

# DROPPED / REWRITTEN
# Journal (P1.1, P1.2, P2.1 x5): the per-account back-off journal is gone (1.130.0 stop on refusal); each case is rewritten
# to the new rules in tests/test_wall_stop_on_refusal.py (Lifecycle, Refusals, Ambiguous, Journal, Shutdown).
# P1.4 (both report caps): a capped report by itself must never mark a wall check run.
# P2.2 (URL and addressless rows): even unmatchable DB rows must leave live lights alone.
# P2.2 (path before the first poll): DB numbers are allowed as neutral path information.
# P2.3 (DB row labelled Healthy): the wall must ignore that classification altogether.
# P2.4 (cached DB after failed refresh): test the resulting Storage lamp, regardless of cache policy.
# P2.5/8 (failed/empty DB checks, absent datastore measurement): only live evidence can light a card.
# P3.12 (fresh tracker per fuzz case): render open -> last_known -> cleared from one tracker.
# P3.12 (animation frames): the review identified a browser timing coverage gap, with no
# failing frame to reproduce. The scanner test below provides deterministic CSS positive controls.
# The grow guards, grey cluster average and already-rejected partial replies were passing checks in the review.
# P3.12 scanner (pseudo-elements, gradients, tooltip, right/bottom borders): SKIPPED - stage 2 by the coordinator's bar
# for round 5 (fuzz polish is not a blocker); kept so stage 2 starts from a failing test.

import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.dont_write_bytecode = True
TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
FILES = ROOT / "roles/site_portal_stack/files"
sys.path[:0] = [str(p) for p in (TESTS, FILES, ROOT / "plugins/module_utils", ROOT / "plugins/filter")]

import test_netapp_live as db_fixture
from test_ontap_expand import volume_module
from test_wall import NOW, demo
from test_wall_live import Clock
import netapp_live
import ontap_expand_filters as survey
import ontap_live_filters as report
import site_ontap_expand as expand
import site_portal
import wall_live as live
import wall_model as wall

ARRAY = "array.corp.example.mil"
OTHER = "array2.corp.example.mil"
VC = "vc.corp.example.mil"
HOST = "host.corp.example.mil"
WINDOW = 900


def ontap_snap(**rows):
    return dict({"cluster": "SITEA", "nodes": [], "aggrs": [], "volumes": [],
                 "luns": [], "snapmirror": []}, **rows)


def source(kind, name, snap, now=NOW):
    return {kind + ":" + name: {"kind": kind, "name": name, "snap": snap,
                                "at": now if snap is not None else None,
                                "state": "ok" if snap is not None else "never"}}


def model(sources=None, db=None, tracker=None, now=NOW):
    return wall.build({"sources": sources or {}}, db, now if db else None, {}, tracker, now)


def area(answer, name):
    return next(a for a in answer["areas"] if a["id"] == name)


def db_run(address=ARRAY):
    return {"run": {"clusters": [{"address": address, "name": "SITEA"}]},
            "parts": {"snapmirror": {"rows": []}, "nodes": {"rows": []}}}


def relationship(**changes):
    return dict({"src": "SITEA:vol1", "dst": "SITEA:vol2", "healthy": False,
                 "state": "snapmirrored"}, **changes)


def host(state):
    return demo.host(HOST, "SITEA", 20, 30, [], [], state=state)


def reader(address=ARRAY, user="reader"):
    # All transport is replaced before polling, including unexpected GETs.
    s = live.OntapSource(address, user, "example", verify=False)
    s._get = Mock(side_effect=AssertionError("unexpected GET in fixture"))
    s._read = Mock(return_value=({"name": "SITEA"}, [], {k: [] for k, *_ in live.ONTAP_GETS}, {}))
    return s




class Storage(unittest.TestCase):
    def check_cap(self, part, limit, make_row, filter_rows, ran):
        clusters = []
        for addr, count in ((ARRAY, limit), (OTHER, 1)):
            records = [make_row(i) for i in range(count)]
            clusters.append({"address": addr, "_name": addr, "data": {part: {"records": records}}})
        db = db_run()
        db["run"]["clusters"].append({"address": OTHER, "name": OTHER})
        body = filter_rows(clusters, {})
        # Preserve the historical report shape even if a future writer lifts its row cap.
        db["parts"][part] = dict(body, rows=body["rows"][:limit])
        answer = model(db=db)
        self.assertFalse(answer["storage"]["ran"][ran], "a report is never a live check")
        self.assertFalse(area(answer, "storage")["on"])
        self.assertFalse(answer["conds"])

    def test_p1_4_capped_snapmirror_report(self):
        self.check_cap("snapmirror", 50, lambda i: {"healthy": False, "destination": {"path": f"SITEA:v{i}"}},
                       report._snapmirror, "sm")

    def test_p1_4_capped_node_report(self):
        self.check_cap("nodes", 200, lambda i: {"name": f"SITEA-{i}", "state": "down"},
                       report._nodes, "nodes")

    def test_p2_2_url_alias_cannot_add_db_aggregate(self):
        url = f"https://{ARRAY}:443"
        db = db_run(url)
        db["aggrs"] = [{"addr": url, "c": "SITEA", "a": "aggr1", "size": 100, "pct": 99}]
        live_source, = live.sources_from_config({"ontap": {"clusters": [url], "user": "reader", "password": "example"}})
        sources = source("ontap", live_source.name, ontap_snap())
        before, after = model(sources), model(sources, db)
        self.assertEqual(after["storage"]["aggr"], before["storage"]["aggr"])
        self.assertEqual(area(after, "storage")["feeds"], area(before, "storage")["feeds"])
        self.assertEqual(after["conds"], before["conds"])

    def test_p2_2_addressless_db_health_is_neutral(self):
        db = db_run()
        db["run"]["clusters"].append({"address": OTHER, "name": "SITEA"})
        db["parts"]["nodes"]["rows"] = [{"cluster": "SITEA", "node": "SITEA-01", "state": "down"}]
        sources = source("ontap", ARRAY, ontap_snap())
        before, after = model(sources), model(sources, db)
        self.assertEqual((after["storage"]["nodes"], after["conds"], area(after, "storage")["complete"]),
                         (before["storage"]["nodes"], before["conds"], area(before, "storage")["complete"]))

    def test_p2_3_unknown_db_snapmirror_is_no_wall_check(self):
        db = db_run()
        db["parts"]["snapmirror"] = report._snapmirror(
            [{"address": ARRAY, "_name": "SITEA", "data": {"snapmirror": {"records": [{}]}}}], {})
        storage = model(db=db)["storage"]
        self.assertEqual((storage["ran"]["sm"], storage["smRel"]), (False, 0))

    def test_p2_4_failed_db_refresh_cannot_enable_storage(self):
        db = db_run()
        db["run"]["read_at"] = datetime.datetime.fromtimestamp(NOW, datetime.timezone.utc).isoformat()
        cache = SimpleNamespace(model=db, stale="DB refresh failed", lock=threading.Lock())
        portal = SimpleNamespace(nal=cache, collector=None, lock=threading.Lock(), tracker=wall.Tracker(),
                                 cfg=SimpleNamespace(report_db=True, wall_opts={}, wall_enclave="", wall_title=""))
        portal.netapp = lambda: site_portal.WallLive.netapp(portal)
        answer = site_portal.WallLive.model(portal, now=NOW)
        self.assertFalse(area(answer, "storage")["on"])

    def test_p2_5_failed_report_health_cannot_enable_storage(self):
        db = db_run()
        db["parts"]["snapmirror"]["errors"] = [ARRAY + ": not read"]
        answer = model(db=db)
        self.assertFalse(area(answer, "storage")["on"])
        self.assertFalse(any(answer["storage"]["ran"].values()))

    def test_p2_5_no_live_cluster_with_healthy_vcenter(self):
        answer = model(source("vcenter", VC, {"hosts": [host("connected")]}), db_run())
        self.assertTrue(area(answer, "vmware")["on"])
        self.assertFalse(area(answer, "storage")["on"])

    def test_p2_5_empty_report_parts_are_not_checks(self):
        db = db_run()
        db["parts"] = {"nodes": {}, "snapmirror": {}}
        self.assertEqual(model(db=db)["storage"]["ran"], {"sm": False, "nodes": False})

    def test_p2_5_db_datastore_never_adds_storage_conditions(self):
        db = db_run()
        db["volumes"] = [{"ds": "DS-SITEA", "src": "datastore used", "pct": 99, "free": 1, "size": 100}]
        answer = model(db=db)
        self.assertFalse(answer["conds"])
        self.assertFalse(answer["storage"]["ds"])

    def test_p1_3_db_run_cannot_clear_live_alert(self):
        t = wall.Tracker()
        sources = source("ontap", ARRAY, ontap_snap(snapmirror=[relationship()]))
        first, = model(sources, tracker=t)["conds"]
        # Keep the source present and fresh. Only the DB claims to have read this relationship.
        sources = source("ontap", ARRAY, ontap_snap(), NOW + 60)
        db = db_run(OTHER)
        db["parts"]["snapmirror"]["rows"].append({"dst": "SITEA:vol2", "addr": OTHER, "st": "ok"})
        second, = model(sources, db, t, NOW + 60)["conds"]
        self.assertEqual((second["id"], second["end"], second.get("last_known")), (first["id"], None, True))


class Observations(unittest.TestCase):
    def test_p1_3_missing_host_stays_open_until_seen_connected(self):
        t = wall.Tracker()
        def read(hosts, now):
            return model(source("vcenter", VC, {"hosts": hosts}, now), tracker=t, now=now)
        first, = read([host("disconnected")], NOW)["conds"]
        absent, = read([dict(host("connected"), name="other.corp.example.mil")], NOW + 30)["conds"]
        recovered, = read([host("connected")], NOW + 60)["conds"]
        self.assertEqual((absent["id"], absent["start"], absent["end"], absent.get("last_known")),
                         (first["id"], first["start"], None, True))
        self.assertEqual((recovered["id"], recovered["end"]), (first["id"], NOW + 60))

    def test_p2_3_error_key_invalidates_collection(self):
        for key, value in (("error", {"message": "partial"}), ("error", None), ("errors", []),
                           ("errors", [{"message": "partial"}])):
            with self.subTest(key=key, value=value):
                s = reader()
                s._get = Mock(return_value={"records": [], key: value})
                with self.assertRaises((live.CollectionFailed, RuntimeError)):   # 1.130.0: an odd answer stops the poll
                    s.records("/api/snapmirror/relationships", "state")

    def test_p2_3_next_without_href_at_page_limit(self):
        s = reader()
        with patch.object(live, "ONTAP_MAX_PAGES", 2):
            s._get = Mock(side_effect=[{"records": [], "_links": {"next": {"href": "/page2"}}},
                                       {"records": [], "_links": {"next": {}}}])
            with self.assertRaises(live.CollectionFailed):
                s.records("/api/cluster/nodes", "state")

    def test_p2_3_partial_snapmirror_never_clears(self):
        for changes in ({"healthy": None}, {"state": None, "healthy": True},
                        {"state": "unexpected", "healthy": True}):
            with self.subTest(changes=changes):
                t = wall.Tracker()
                def read(row, now):
                    src = source("ontap", ARRAY, ontap_snap(snapmirror=[row]), now)
                    return model(src, tracker=t, now=now)
                first, = read(relationship(), NOW)["conds"]
                second = read(relationship(**changes), NOW + 30)
                alert, = second["conds"]
                self.assertEqual((alert["id"], alert["end"], alert.get("last_known"),
                                  area(second, "storage")["complete"]),
                                 (first["id"], None, True, False))
                recovered, = read(relationship(healthy=True), NOW + 60)["conds"]
                self.assertEqual(recovered["end"], NOW + 60)

    def test_p2_3_offline_lun_without_container_state(self):
        s = reader()
        s._read.return_value[2]["luns"] = [{"name": "/vol/vol1/lun1", "status": {"state": "offline"}}]
        snap = s.poll()
        self.assertEqual(snap["luns"][0]["state"], "offline")
        self.assertIn("voloff", [c["kind"] for c in model(source("ontap", ARRAY, snap))["conds"]])

    def test_p2_3_unrecognized_volume_or_lun_state_is_unknown(self):
        for key, row in (("volumes", {"name": "vol1"}), ("luns", {"path": "/vol/vol1/lun1"})):
            with self.subTest(collection=key):
                snap = ontap_snap(**{key: [dict(row, state="unrecognized", svm="SITEA")]})
                answer = model(source("ontap", ARRAY, snap))
                self.assertFalse(area(answer, "storage")["complete"])
                self.assertFalse(answer["conds"])

    def test_p2_7_unknown_maintenance_is_not_connected_and_ok(self):
        for value in (None, "unreadable"):
            with self.subTest(maintenance=value):
                props = {"runtime.connectionState": "connected", "summary.hardware.cpuMhz": 1000,
                         "summary.hardware.numCpuCores": 2, "summary.hardware.memorySize": 1024**3,
                         "summary.quickStats.overallCpuUsage": 400, "summary.quickStats.overallMemoryUsage": 200}
                if value is not None:
                    props["runtime.inMaintenanceMode"] = value
                answer = model(source("vcenter", VC, live.shape([("HostSystem", HOST, props)])))
                row, = answer["vmware"]["hosts"]
                self.assertEqual((row["st"], area(answer, "vmware")["complete"]), ("stale", False))

    def test_p2_7_missing_vm_power_is_unknown(self):
        for power in (None, "unrecognized"):
            with self.subTest(power=power):
                vm = {"name": "SITEA-vm", "heartbeat": "green", "cpu_pct": 5, "mem_pct": 10}
                if power is not None:
                    vm["power"] = power
                self.assertFalse(area(model(source("vcenter", VC, {"vms": [vm]})), "vmware")["complete"])

    def test_p2_7_gray_heartbeat_is_not_recovery(self):
        t = wall.Tracker()
        vm = {"name": "SITEA-vm", "power": "poweredOn", "cpu_pct": 20, "mem_pct": 30}
        def read(color, now):
            return model(source("vcenter", VC, {"vms": [dict(vm, heartbeat=color)]}, now), tracker=t, now=now)
        first, = read("red", NOW)["conds"]
        second = read("gray", NOW + 30)
        alert, = second["conds"]
        self.assertEqual((alert["id"], alert["end"], alert.get("last_known"), area(second, "vmware")["complete"]),
                         (first["id"], None, True, False))
        recovered, = read("green", NOW + 60)["conds"]
        self.assertEqual(recovered["end"], NOW + 60)

    def test_p2_7_missing_soap_accessibility_is_unknown(self):
        snap = live.shape([("Datastore", "DS-SITEA", {"summary.capacity": 100, "summary.freeSpace": 80})])
        answer = model(source("vcenter", VC, snap))
        row, = answer["vmware"]["datastores"]
        self.assertEqual((snap["datastores"][0]["accessible"], row["accessible"],
                          area(answer, "vmware")["complete"], answer["conds"]), (None, None, False, []))


def choice(**fields):
    return dict({"address": ARRAY, "cluster": "SITEA", "volume": "vol1", "svm": "svm1",
                 "size": 100 * expand.GB, "used": 0, "lun": "", "aggr": "aggr1"}, **fields)


class Capacity(unittest.TestCase):
    def assert_unknown_first(self, item):
        known = choice(volume="known", used=80 * expand.GB)
        choices = survey.ontap_expand_choices([known, item])["choices"]
        shown = [expand.choice_shown(text) for text in choices]
        self.assertIn("use unknown", shown[0], shown)
        self.assertIn(item["volume"], shown[0])
        self.assertNotIn("% free", shown[0])
        if item.get("lun") and not item.get("lun_size"):
            self.assertNotIn(expand.human(item["size"]), shown[0], "the volume size is not the LUN size")

    def test_p2_6_lun_missing_size_sorts_as_least_free(self):
        for fields in ({"lun_used": 0}, {"lun_size": 0}, {"lun_size": 10 * expand.GB}):
            with self.subTest(fields=fields):
                self.assert_unknown_first(choice(lun="/vol/vol1/lun1", **fields))

    def test_p2_6_volume_missing_use_sorts_as_least_free(self):
        self.assert_unknown_first(choice(used=None))

    def test_r5_23_volume_size_missing_use_known(self):
        """Codex r5 #23: used 10, no size - "size unknown, use unknown", never "100% free"; sorted as the least free"""
        self.assert_unknown_first(choice(used=10, size=0))
        shown = expand.choice_shown(expand.choice_text(choice(used=10, size=0)))
        self.assertIn("size unknown, use unknown", shown)

    def test_p2_6_volume_list_preserves_missing_use(self):
        v = volume_module().vol_in({"name": "vol1", "space": {"size": 100 * expand.GB}})
        self.assertIsNone(v["used"])

    def test_p2_6_unknown_volume_use_survives_db_round_trip(self):
        db = db_fixture.Lite()
        self.addCleanup(db.db.close)
        payload = report.ontap_live_payload({"list": {"choices": [choice(used=None)], "aggregates": [],
                                                       "clusters": [{"address": ARRAY, "name": "SITEA"}]}})
        stamp = datetime.datetime(2026, 11, 25)
        rid = db_fixture.ldb.write(db, stamp, 1, 1, payload)[0]
        db_fixture.ldb.rate_all(db, stamp, rid)
        row, = netapp_live.Database(lambda: db, mark="?").read()["volumes"]
        self.assertEqual((row["used"], row["free"], row["pct"], row["st"]), (None, None, None, "unknown"))

    def test_p2_6_list_never_reports_unmeasured_aggregate_free(self):
        volume = {"name": "vol1", "svm": {"name": "svm1"}, "state": "online",
                  "aggregates": [{"name": "aggr1"}], "space": {"size": 100, "used": 10}}
        reads = {"/api/storage/volumes": [volume, dict(volume, name="vol2", type="dp")],
                 "/api/storage/aggregates": [{"name": "aggr1", "space": {"block_storage": {"size": 1000}}}],
                 "/api/storage/luns": [], "/api/network/ip/interfaces": []}
        client = Mock(address=ARRAY)
        client.call.return_value = {"name": "SITEA"}
        client.records.side_effect = lambda path, *args, **kw: reads[path]
        _, offered, excluded, _ = volume_module().list_cluster(client, {"exclude": []})
        self.assertEqual([r["aggr_free"] for r in offered + excluded], [None, None])


# Only element creation, text and attributes are needed for the cards under test.
DOM = """
class Element {
    constructor() {
        this.children = [];
        this.attrs = {};
        this.style = {};
        this.classList = {add() {}, remove() {}};
    }
    append(child) { this.children.push(child); }
    appendChild(child) { this.append(child); return child; }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    getAttribute(k) { return this.attrs[k] || null; }
    get textContent() { return this.children.map(c => typeof c === "string" ? c : c.textContent).join(""); }
}
const document = {
    getElementById: () => new Element(),
    createElement: () => new Element(),
    createElementNS: () => new Element(),
    createTextNode: String
};
const location = {search: ""};
function elements(root) {
    return [root].concat(...root.children.filter(c => c instanceof Element).map(elements));
}
"""


def page_prefix(name, before):
    script = (FILES / "static" / name).read_text(encoding="utf-8")
    prefix, found, _ = script.rpartition(before)
    if not found:
        raise RuntimeError(f"could not locate {name} startup")
    return prefix


@unittest.skipUnless(shutil.which("node"), "wall-page.js needs Node")
class WallPage(unittest.TestCase):
    def render(self, answer, probe, *, malformed=False, setup=""):
        # Exercise the real adapter and renderer inside their closure. Leave fetch and timers idle.
        prefix = page_prefix("wall-page.js", "\n  load(true);")
        script = DOM + setup + "\nconst answer = " + json.dumps(answer) + ";\n" + prefix + """
          SKEW = answer.now * 1000 - Date.now();
          function inspect() { W = adapt(answer);
        """ + probe + """
          }
        """
        if malformed:
            # An exception from the production adapter on malformed input is itself a finding.
            script += 'try { console.log(JSON.stringify(inspect())); } catch (e) { console.log(JSON.stringify(String(e))); }'
        else:
            script += "console.log(JSON.stringify(inspect()));"
        run = subprocess.run(["node", "-"], input=script + "\n})();",
                             capture_output=True, text=True, timeout=10, check=True)
        return json.loads(run.stdout)

    def test_p1_3_last_known_on_fresh_feed_renders_grey(self):
        answer = model(source("vcenter", VC, {"hosts": [host("disconnected")]}), tracker=wall.Tracker())
        answer["conds"][0]["last_known"] = True
        result = self.render(answer, """
            const c = W.conds[0];
            return [DET.c(c.id).st, areaCounts("vmware").crit];
        """)
        self.assertEqual(result, ["stale", 0])

    def test_p2_2_path_before_first_live_read_is_neutral(self):
        db = db_run()
        db["volumes"] = [{"addr": ARRAY, "c": "SITEA", "v": "vol1", "svm": "svm1", "ds": "DS-SITEA",
                          "src": "volume used", "size": 100, "used": 99, "pct": 99}]
        answer = model(source("ontap", ARRAY, None), db)
        classes = self.render(answer, """
            card = new Element(); back = new Element(); tip = new Element();
            openDs("DS-SITEA", true);
            return elements(card).map(e => e.className || e.getAttribute("class") || "");
        """)
        self.assertFalse({"wd crit", "wd warn", "wd ok", "fl f-crit", "fl f-warn", "fl f-ok"}.intersection(classes))

    def test_p2_2_same_named_live_aggregate_opens_own_gauge(self):
        sources = {}
        for addr, used in ((ARRAY, 98), (OTHER, 10)):
            sources.update(source("ontap", addr, ontap_snap(aggrs=[{"name": "aggr1", "size": 100, "used": used}])))
        result = self.render(model(sources), """
            return W.storage.aggr.map(row => {
                const ref = aggrGauge(row).getAttribute("data-d");
                const detail = DET.aggr(ref.slice("aggr:".length));
                return [row.feed, detail.fk, detail.st];
            });
        """)
        self.assertEqual(result, [["ontap:" + ARRAY, "ontap:" + ARRAY, "crit"],
                                  ["ontap:" + OTHER, "ontap:" + OTHER, "ok"]])

    def test_p2_5_report_without_datastore_cannot_light_counter(self):
        result = self.render(model(db=db_run()), 'return DET.ctr("ds").st;')
        self.assertIn(result, ["off", "stale"])

    def test_p2_8_db_aggregate_cannot_light_map_dot(self):
        db = db_run()
        db["aggrs"] = [{"a": "aggr1", "addr": ARRAY, "c": "SITEA", "site": "SITEA", "pct": 10, "size": 100}]
        result = self.render(model(db=db), 'return siteState("SITEA").k;')
        self.assertIn(result, ["off", "stale"])

    def test_p2_8_stale_snapmirror_group_header_is_grey(self):
        answer = model(source("ontap", ARRAY, ontap_snap(snapmirror=[relationship()])), tracker=wall.Tracker())
        answer["feeds"]["ontap:" + ARRAY]["asof"] -= 901
        result = self.render(answer, 'return [DET.ctr("sm").st, DET.grp("sm").st];')
        self.assertEqual(result, ["stale", "stale"])

    def test_p2_8_stale_critical_cannot_promote_current_warning(self):
        sources = {}
        for address, used in ((ARRAY, 98), (OTHER, 90)):
            sources.update(source("ontap", address, ontap_snap(
                cluster=address, aggrs=[{"name": "aggr1", "size": 100, "used": used}])))
        answer = model(sources, tracker=wall.Tracker())
        answer["feeds"]["ontap:" + ARRAY]["asof"] -= 901
        result = self.render(answer, """
            const tile = elements(condTiles()).find(e => e.getAttribute("data-d") === "grp:aggr");
            return [DET.grp("aggr").st, tile.className.split(" ").includes("k-warn")];
        """)
        self.assertEqual(result, ["warn", True])

    def feed_answer(self):
        return model(source("vcenter", VC, {"hosts": [host("connected")]}))

    def test_p2_9_missing_feed_dictionary(self):
        answer = self.feed_answer()
        del answer["feeds"]
        self.assertIn(self.render(answer, 'return areaState("vmware");', malformed=True), ("stale", "off"))

    def test_p2_9_missing_area_feeds(self):
        answer = self.feed_answer()
        del area(answer, "vmware")["feeds"]
        result = self.render(answer, 'return [areaState("vmware"), status("ok", W.vmware.hosts[0].feed)];',
                             malformed=True)
        self.assertIsInstance(result, list, result)
        self.assertEqual(len(result), 2)
        for state in result:
            self.assertIn(state, ("stale", "off"))

    def test_p2_9_missing_feed_state(self):
        answer = self.feed_answer()
        del answer["feeds"]["vc:" + VC]["state"]
        self.assertIn(self.render(answer, 'return areaState("vmware");'), ("stale", "off"))

    def test_p2_9_never_feed_with_recent_timestamp(self):
        answer = self.feed_answer()
        answer["feeds"]["vc:" + VC]["state"] = "never"
        self.assertIn(self.render(answer, 'return areaState("vmware");'), ("stale", "off"))

    def test_p3_12_render_tracked_loss_and_recovery(self):
        t = wall.Tracker()
        def read(hosts, now):
            return model(source("vcenter", VC, {"hosts": hosts}, now), tracker=t, now=now)
        first = read([host("disconnected")], NOW)
        lost = read([dict(host("connected"), state=None)], NOW + 30)
        recovered = read([host("connected")], NOW + 60)
        self.assertTrue(lost["conds"][0]["last_known"])
        self.assertEqual(recovered["conds"][0]["end"], NOW + 60)
        first["followup"] = [lost, recovered]
        states = self.render(first, """
            const frames = [W, ...answer.followup.map(adapt)];
            return frames.map(frame => { W = frame; return condBody(frame.conds[0]).st; });
        """)
        self.assertEqual(states, ["crit", "stale", "ok"])

    @unittest.skip("stage 2 (coordinator, round 4): fuzz polish - pseudo-elements, gradients, the tooltip, right/bottom borders")
    def test_p3_12_scanner_detects_visible_green(self):
        # Present one visible element at a time. These are inputs to the computed-style
        # scanner, so a miss is independent of the browser theme, animation or geometry.
        setup = """
            const window = {}, green = "rgb(0, 128, 0)";
            class SVGElement extends Element {}
            const wallRoot = new Element(), target = new Element();
            wallRoot.setAttribute("data-test", "1");
            document.getElementById = () => wallRoot;
            document.body = new Element();
            Element.prototype.removeChild = function(e) { this.children.splice(this.children.indexOf(e), 1); };
            target.tagName = "DIV";
            target.childNodes = [];
            target.getClientRects = () => [{}];
            wallRoot.querySelectorAll = sel => sel === "*" ? [target] : [];
            let scannerLocation = "", property = "", pseudo = "";
            target.closest = sel => sel === "#tip" && scannerLocation === "tooltip" ? target : null;
            function getComputedStyle(e, part) {
                return {visibility: "visible", display: "block", opacity: "1", color: e.style.color,
                    getPropertyValue(k) {
                        if (e === document.body && k === "--ok") return green;
                        if (e !== target || (part || "") !== pseudo) return "";
                        if (k === "content" && pseudo) return '"SITEA"';
                        if (k.endsWith("-width")) return "2px";
                        if (k !== property) return "";
                        return k === "background-image" ? "linear-gradient(" + green + ", white)" : green;
                    }};
            }
        """
        result = self.render(model(), """
            const detected = {};
            for (const [label, p, part, location] of [
                    ["background", "background-color", "", ""],
                    ["tooltip", "background-color", "", "tooltip"],
                    ["before", "background-color", "::before", ""],
                    ["after", "background-color", "::after", ""],
                    ["gradient", "background-image", "", ""],
                    ["right", "border-right-color", "", ""],
                    ["bottom", "border-bottom-color", "", ""]]) {
                property = p; pseudo = part; scannerLocation = location;
                detected[label] = window.__wallTest.greens().greens.length > 0;
            }
            return detected;
        """, setup=setup)
        self.assertTrue(result.pop("background"), "ordinary green background is the positive control")
        self.assertEqual([label for label, detected in result.items() if not detected], [])


class NetAppSummary(unittest.TestCase):
    def test_p2_11_unknown_capacity_has_no_ok_summary_chip(self):
        # Run the real NetApp summary locally. Qt's JS engine also works on hosts where
        # browser process/socket creation is restricted. Otherwise use the repo's browser fixture.
        import site
        with patch.object(sys, "path", sys.path + site.getsitepackages([sys.base_prefix])):
            try:
                from PySide6.QtCore import QCoreApplication
                from PySide6.QtQml import QJSEngine
            except ImportError:
                QJSEngine = None

        volume = {"name": "SITEA-vol1", "pct": None, "free": None, "st": "unknown"}
        if QJSEngine is not None:
            app = QCoreApplication.instance() or QCoreApplication([])
            engine = QJSEngine(app)
            prefix = page_prefix("netapp-page.js", "\n  // ---- Expand...")
            probe = """
                const panel = glance({run: {}, parts: {}, volumes: [VOLUME]});
                const tiles = elements(panel).filter(e => e.className === "tile" &&
                    e.children[0].textContent.startsWith("How full over"));
                return JSON.stringify(tiles.map(t => elements(t).some(e => e.className === "chip s-ok")));
            """
            value = engine.evaluate(DOM + 'const URLSearchParams = class { get() { return ""; } };\n'
                                    + prefix + probe.replace("VOLUME", json.dumps(volume)) + "})();")
            if value.isError():
                raise RuntimeError(f"{value.property('lineNumber').toInt()}: {value.toString()}")
            chips = json.loads(value.toString())
        else:
            if not db_fixture.browser():
                self.skipTest("NetApp rendering needs headless Chrome or QtQml")
            answer = db_fixture.answer_for(self)
            answer["model"]["volumes"] = [dict(answer["model"]["volumes"][0], **volume)]
            js = """r.ok = [...document.querySelectorAll('.nap-tiles .tile')].filter(
                t => t.querySelector('.k').textContent.startsWith('How full over')
            ).map(t => !!t.querySelector('.chip.s-ok'));"""
            html = db_fixture.run_page(db_fixture.page_files(self, answer, js=js), 1280)
            from html import unescape
            import re
            chips = json.loads(unescape(re.search(r'<pre id="res" hidden="">(.*?)</pre>', html, re.S)[1]))["ok"]
        self.assertEqual(chips, [False, False], "warning and critical threshold tiles")


if __name__ == "__main__":
    unittest.main()
