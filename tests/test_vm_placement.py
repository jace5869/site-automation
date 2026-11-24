#!/usr/bin/env python3
"""1.116.6: the VM placement history (plugins/module_utils/site_vm_placement.py; the job "VMware - VM placement snapshot",
roles/vmware_vm/library/site_vm_placement_snapshot.py; the review's reads site_vm_placement_read.py and, as the fallback,
site_vmware_object_vms.py). The writer keeps only changes plus one whole snapshot a day and prunes; the reader answers
"as of" a time (before, exactly at, no snapshot); the review of a past window lists the VMs from it, in the page, the
tile, the workbook and the AAR; the setup catalog has the job. A fake cursor over SQLite stands in for MySQL.
    python3 tests/test_vm_placement.py"""
import datetime as dt
import importlib.util
import os
import re
import sqlite3
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "roles", "vmware_vm", "library"))


def _as_module_util(name, path):
    spec = importlib.util.spec_from_file_location("ansible.module_utils." + name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ansible.module_utils." + name] = mod
    spec.loader.exec_module(mod)
    return mod


vp = _as_module_util("site_vm_placement", os.path.join(REPO, "plugins", "module_utils", "site_vm_placement.py"))
_as_module_util("site_report_db", os.path.join(REPO, "plugins", "module_utils", "site_report_db.py"))
_as_module_util("site_vcenters", os.path.join(REPO, "roles", "vmware_vm", "module_utils", "site_vcenters.py"))
import site_vm_placement_snapshot as snap  # noqa: E402
import site_vmware_object_vms as live  # noqa: E402
import test_outage_compact as tc  # noqa: E402

ot = tc.ot
STAMP = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")


class FakeCursor(object):
    """The statements site_vm_placement sends, run on SQLite: placeholders, the MySQL-only words, DATETIME back as datetime."""

    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.cur = self.db.cursor()
        self.sql = []

    @staticmethod
    def _sql(sql):
        sql = sql.replace("%s", "?").replace(" " + vp.TABLE_OPTS, "").replace("UTC_TIMESTAMP()", "CURRENT_TIMESTAMP")
        sql = sql.replace("information_schema.tables WHERE table_schema = DATABASE() AND table_name = ?",
                          "sqlite_master WHERE type = 'table' AND name = ?").replace("SELECT table_name FROM", "SELECT name FROM")
        sql = re.sub(r", KEY \w+ \([^)]*\)", "", sql)
        return sql.replace("ON DUPLICATE KEY UPDATE version = VALUES(version)", "ON CONFLICT(component) DO UPDATE SET version = excluded.version")

    @staticmethod
    def _arg(v):
        return v.strftime("%Y-%m-%d %H:%M:%S") if isinstance(v, dt.datetime) else v

    @staticmethod
    def _back(v):
        return dt.datetime.strptime(v, "%Y-%m-%d %H:%M:%S") if isinstance(v, str) and STAMP.match(v) else v

    def execute(self, sql, args=()):
        self.sql.append(sql)
        self.cur.execute(self._sql(sql), tuple(self._arg(a) for a in args))
        return self.cur.rowcount

    def executemany(self, sql, rows):
        self.cur.executemany(self._sql(sql), [tuple(self._arg(a) for a in r) for r in rows])

    def fetchone(self):
        r = self.cur.fetchone()
        return None if r is None else tuple(self._back(v) for v in r)

    def fetchall(self):
        return [tuple(self._back(v) for v in r) for r in self.cur.fetchall()]


def at(day, hms):
    h, m, s = (int(x) for x in hms.split(":"))
    return dt.datetime(2026, 11, day, h, m, s)


def vm(moid, name, host, stores, power="poweredOn", cluster="cluster1"):
    return dict(moid=moid, name=name, host=host, cluster=cluster, datastores=stores, power=power)


VC = "vc01.example.mil"
FIRST = [vm("vm-1", "app01", "esx01.example.mil", ["ds_shared_01"]), vm("vm-2", "app02", "esx02.example.mil", ["ds_shared_01", "ds_local_02"]),
         vm("vm-3", "db01", "esx02.example.mil", ["ds_local_02"], "poweredOff")]


def written():
    """Three runs on day 1 and one on day 2 -> (cursor, [(whole, kept)])."""
    cur = FakeCursor()
    vp.schema(cur, True)
    runs = [vp.write(cur, VC, at(22, "06:00:00"), FIRST, 11)]
    runs.append(vp.write(cur, VC, at(22, "07:00:00"), FIRST, 12))                                   # nothing changed
    moved = [FIRST[0], dict(FIRST[1], host="esx03.example.mil"), vm("vm-4", "web01", "esx01.example.mil", ["ds_shared_01"])]
    runs.append(vp.write(cur, VC, at(22, "08:00:00"), moved, 13))                                   # moved, new, gone
    runs.append(vp.write(cur, VC, at(23, "00:30:00"), moved, 14))                                   # a new day: whole
    return cur, runs


class Writer(unittest.TestCase):
    def test_changes_only_and_one_whole_snapshot_a_day(self):
        cur, runs = written()
        self.assertEqual(runs, [(True, 3), (False, 0), (False, 3), (True, 3)])
        cur.execute("SELECT at, vm_moid, kind FROM vm_placement WHERE at = ? ORDER BY vm_moid", (at(22, "08:00:00"),))
        self.assertEqual([r[1:] for r in cur.fetchall()], [("vm-2", "C"), ("vm-3", "G"), ("vm-4", "C")])
        cur.execute("SELECT whole, vms, kept, job FROM vm_placement_run ORDER BY at")
        self.assertEqual(cur.fetchall(), [(1, 3, 3, 11), (0, 3, 0, 12), (0, 3, 3, 13), (1, 3, 3, 14)])

    def test_retention_keeps_the_whole_snapshot_it_needs(self):
        cur, _ = written()
        later = at(23, "10:00:00")
        self.assertEqual(vp.prune(cur, later, 0), 0)
        gone = vp.prune(cur, later, 1)            # cutoff 22 Nov 10:00: kept from 23 Nov 00:30, the oldest whole snapshot after it
        self.assertEqual(gone, 6 + 3)              # day 1's rows (3 + 3) and its three run rows
        self.assertEqual(vp.state_as_of(cur, VC, later)[1].keys(), {"vm-1", "vm-2", "vm-4"})
        self.assertEqual(vp.state_as_of(cur, VC, at(22, "09:00:00")), (None, {}))

    def test_the_table_is_made_once_and_said_when_not_there(self):
        cur = FakeCursor()
        self.assertEqual(vp.schema(cur, False), (False, False))
        self.assertEqual(vp.schema(cur, True), (True, True))
        self.assertEqual(vp.schema(cur, True), (True, False))
        cur.execute("SELECT version FROM schema_version WHERE component = 'vm_placement'")
        self.assertEqual(cur.fetchone(), (1,))

    def test_a_dry_run_writes_nothing(self):
        cur = FakeCursor()
        vp.schema(cur, True)
        conn = types.SimpleNamespace(cursor=lambda: cur, commit=lambda: None)
        p = dict(create=True, keep_days=400, job=0)
        runs, pruned, created = snap.keep(conn, [(VC, FIRST)], at(22, "06:00:00"), p, True)
        self.assertEqual((runs, pruned, created), ([{"vcenter": VC, "vms": 3, "whole": True, "kept": 0}], 0, False))
        cur.execute("SELECT COUNT(*) FROM vm_placement")
        self.assertEqual(cur.fetchone(), (0,))

    def test_vcenter_read_to_rows(self):
        T = lambda: types.SimpleNamespace()                                  # noqa: E731

        def obj(moid):
            o = T()
            o._moId = moid
            return o
        cl, h1, d1, d2 = obj("domain-c1"), obj("host-1"), obj("datastore-1"), obj("datastore-2")
        props = {"cluster": {"domain-c1": (cl, {"name": "cluster1"})},
                 "datastore": {"datastore-1": (d1, {"name": "ds_shared_01"}), "datastore-2": (d2, {"name": "ds_local_02"})},
                 "host": {"host-1": (h1, {"name": "esx01.example.mil", "parent": cl})},
                 "vm": {"vm-1": (obj("vm-1"), {"name": "app01", "runtime.host": h1, "runtime.powerState": "poweredOn", "datastore": [d2, d1]}),
                        "vm-9": (obj("vm-9"), {"name": "tpl", "runtime.host": h1, "config.template": True, "datastore": [d1]})}}
        self.assertEqual(snap.placements(props), [vm("vm-1", "app01", "esx01.example.mil", ["ds_local_02", "ds_shared_01"])])

    def test_the_doc_has_the_statements(self):
        doc = open(os.path.join(REPO, "docs", "REPORT_DATABASE.md"), encoding="utf-8").read()
        part = doc.split("<!-- the statements: site_vm_placement.ddl()", 1)[1].split("<!-- end of the VM placement statements -->")[0]
        self.assertEqual("\n\n".join(re.findall(r"```sql\n(.*?)\n```", part, re.S)), vp.ddl())


class Reader(unittest.TestCase):
    def setUp(self):
        self.cur, _ = written()

    def item(self, when, kind="datastore", name="ds_shared_01"):
        return dict(vcenter=VC, kind=kind, name=name, at=when)

    def test_as_of_before_exactly_at_and_none(self):
        before, exact, early = vp.lookup(self.cur, [self.item("2026-11-22T07:30:00Z"), self.item("2026-11-22T08:00:00Z"),
                                                     self.item("2026-11-22T05:59:59Z")])
        self.assertEqual((before["as_of"], [v["name"] for v in before["vms"]]), ("2026-11-22T07:00:00Z", ["app01", "app02"]))
        self.assertEqual((exact["as_of"], [v["name"] for v in exact["vms"]]), ("2026-11-22T08:00:00Z", ["app01", "app02", "web01"]))
        self.assertEqual((early["as_of"], early["vms"]), ("", []))

    def test_a_host_by_its_full_or_short_name(self):
        for name in ("esx02.example.mil", "esx02"):
            got, = vp.lookup(self.cur, [self.item("2026-11-22T07:30:00Z", "host", name)])
            self.assertEqual([(v["name"], v["power"]) for v in got["vms"]], [("app02", "poweredOn"), ("db01", "poweredOff")])
        moved, = vp.lookup(self.cur, [self.item("2026-11-22T08:30:00Z", "host", "esx02")])
        self.assertEqual(moved["vms"], [])                     # app02 moved to esx03, db01 gone

    def test_the_live_fallback_and_the_merge(self):
        def obj(moid):
            o = types.SimpleNamespace()
            o._moId = moid
            return o
        h, d, v1 = obj("host-1"), obj("datastore-11"), obj("vm-1")
        table = {"Datastore": {"datastore-11": (d, {"name": "ds_shared_01", "vm": [v1]})},
                 "HostSystem": {"host-1": (h, {"name": "esx01.example.mil", "vm": [v1]})},
                 "VirtualMachine": {"vm-1": (v1, {"name": "app01", "runtime.host": h, "runtime.powerState": "poweredOn", "datastore": [d]})}}
        saved = getattr(live, "vim", None)
        live.vim = types.SimpleNamespace(Datastore="Datastore", HostSystem="HostSystem", VirtualMachine="VirtualMachine")
        try:
            got = live._read(types.SimpleNamespace(rootFolder=None), [dict(vcenter=VC, kind="datastore", name="ds_shared_01", moid="")],
                             VC, "2026-11-23T08:30:00Z", props=lambda c, r, t, p: table[t])
        finally:
            live.vim = saved
        self.assertEqual(got[0]["as_of"], "2026-11-23T08:30:00Z")
        self.assertEqual(got[0]["source"], "review")
        self.assertEqual(got[0]["vms"][0]["host"], "esx01.example.mil")
        items = [dict(vcenter=VC, kind="datastore", name="ds_shared_01"), dict(vcenter=VC, kind="host", name="esx01")]
        history = [dict(items[0], as_of=""), dict(items[1], as_of="2026-11-23T06:00:00Z", vms=[])]
        self.assertEqual(ot.outage_placement_missing(items, history), [items[0]])
        merged = ot.outage_placements(history, {"placements": got})
        self.assertEqual([(p["kind"], p.get("source")) for p in merged], [("host", None), ("datastore", "review")])


def past_review(placements):
    events, tickets, opts = tc.shared_ds_outage()
    return ot.outage_timeline(events, tickets, dict(opts, placements=placements))


def history_for_fixture():
    """A placement history of the fixture's site: 42 VMs on ds_shared_01 at 06:00 UTC (07:00 CET), before the first loss."""
    cur = FakeCursor()
    vp.schema(cur, True)
    vms = [vm("vm-%d" % i, "sitea-vm%02d" % i, tc.HOSTS[i % 14], [tc.DS]) for i in range(1, 43)]
    vp.write(cur, tc.VC, at(23, "06:00:00"), vms)
    return cur


class PastReview(unittest.TestCase):
    def test_the_review_lists_the_vms_from_the_history(self):
        events, _, opts = tc.shared_ds_outage()
        items = ot.outage_review_objects(events, opts)
        self.assertEqual(items, [dict(vcenter=tc.VC, kind="datastore", name=tc.DS, moid="datastore-11", at="2026-11-23T06:08:41Z")])
        placements = vp.lookup(history_for_fixture(), items)
        r = past_review(placements)
        d = r["draft"]
        line = d["files_lines"][0]
        self.assertTrue(line.startswith("42 VMs had files on ds_shared_01 (as of 23 Nov 07:00:00 CET, the placement snapshot; "
                                        "may have hung or failed, not proof they were down): sitea-vm01 (esx02, on)"), line)
        self.assertTrue(line.endswith(" +34 more"))
        self.assertEqual(d["files_unread"], [])
        self.assertEqual(r["numbers"]["affected_vms"], 0)
        self.assertEqual(next(b for b in r["boxes"] if b["label"] == "VMs affected")["note"], "no VM confirmed · 42 with files on ds_shared_01")
        sheet = next(s for s in r["sheets"] if s["name"] == "VMs on lost objects")
        self.assertEqual(len(sheet["rows"]), 42)
        self.assertEqual(sheet["rows"][0], ["ds_shared_01", "datastore", "sitea-vm01", "esx02.example.mil", "on", "ds_shared_01",
                                            "2026-11-23T06:00:00Z", "placement snapshot"])
        blocks = tc.aar(r)["blocks"]
        self.assertIn({"type": "para", "text": line}, blocks)
        handoff = __import__("incident_doc_filters").incident_noc_docx(d)
        self.assertIn("42 VMs had files on ds_shared_01", str(handoff["blocks"]))

    def test_a_confirmed_vm_is_listed_once_in_its_own_tier(self):
        events, tickets, opts = tc.shared_ds_outage()
        events.append(dict(events[0], key=88888, time="2026-11-23T06:09:00Z", type="VmDasBeingResetEvent", host=tc.HOSTS[0],
                           vm="sitea-vm05", datastore=tc.DS, message="sitea-vm05 is reset by vSphere HA"))
        placements = vp.lookup(history_for_fixture(), ot.outage_review_objects(events, opts))
        r = ot.outage_timeline(events, tickets, dict(opts, placements=placements))
        self.assertIn("sitea-vm05", str(r["draft"]["affected"]))
        self.assertTrue(r["draft"]["files_lines"][0].startswith("41 VMs had files on ds_shared_01"))
        self.assertNotIn("sitea-vm05 (", r["draft"]["files_lines"][0])

    def test_a_host_root_lists_the_vms_that_ran_on_it(self):
        from test_outage_timeline import Review1162, watch_run
        c = Review1162()
        cur = FakeCursor()
        vp.schema(cur, True)
        vp.write(cur, c.vc, dt.datetime(1970, 1, 1, 0, 1, 40), [vm("vm-1", "app01", c.host, ["ds"]), vm("vm-2", "app02", "esx9", ["ds"])])
        events = [c.event("HostConnectionLostEvent", 600, host=c.host), c.event("HostConnectedEvent", 1500, host=c.host)]
        opts = dict(begin=100, end=2400, review=True, watch_runs=[watch_run(990, [dict(name=c.host, moid="host-1")], vc=c.vc)])
        items = ot.outage_review_objects(events, opts)
        self.assertEqual([(i["kind"], i["at"]) for i in items], [("host", "1970-01-01T00:10:00Z")])
        r = ot.outage_timeline(events, [], dict(opts, placements=vp.lookup(cur, items)))
        self.assertEqual(r["draft"]["files_lines"], ["1 VM ran on %s (as of 01 Jan 00:01:40 UTC, the placement snapshot; not proof "
                                                     "they were down): app01 (esx, on)" % c.host.split(".")[0]])


class EventTier(unittest.TestCase):
    """1.116.6: tier 2 - VMs with events of their own inside a root's loss that touch it; one tier per VM, never counted."""

    def test_three_tiers_without_double_listing(self):
        events, tickets, opts = tc.shared_ds_outage()
        base = dict(events[0], alarm="", entity="", entity_type="", args={})
        events += [dict(base, key=90001, time="2026-11-23T06:16:10Z", type="AlarmStatusChangedEvent", host=tc.HOSTS[9], vm="sitea-vm03",
                        alarm="Virtual machine heartbeat", entity="sitea-vm03", entity_type="vm", to="red", **{"from": "green"}),
                   dict(base, key=90002, time="2026-11-23T06:09:30Z", type="VmPoweredOffEvent", host=tc.HOSTS[1], vm="sitea-vm07"),
                   dict(base, key=90003, time="2026-11-23T08:30:00Z", type="VmPoweredOffEvent", host=tc.HOSTS[1], vm="sitea-vm08"),
                   dict(base, key=90004, time="2026-11-23T06:09:00Z", type="VmDasBeingResetEvent", host=tc.HOSTS[0],
                        vm="sitea-vm05", datastore=tc.DS)]
        placements = vp.lookup(history_for_fixture(), ot.outage_review_objects(events, opts))
        r = ot.outage_timeline(events, tickets, dict(opts, placements=placements))
        d = r["draft"]
        self.assertEqual(r["numbers"]["affected_vms"], 1)                       # sitea-vm05: HA reset it (confirmed)
        self.assertEqual(d["files_lines"][0], "2 VMs had events of their own during the outage (not proof they were down): "
                                              "sitea-vm07 (07:09:30 VM powered off), sitea-vm03 (07:16:10 Alarm red: Virtual machine heartbeat)")
        self.assertTrue(d["files_lines"][1].startswith("39 VMs had files on ds_shared_01"))   # 42 less the three above
        for vm_name in ("sitea-vm03 (", "sitea-vm05 (", "sitea-vm07 ("):
            self.assertNotIn(vm_name, d["files_lines"][1])
        self.assertNotIn("sitea-vm08", d["files_lines"][0])                     # after the outage: not a signal
        tile = next(b for b in r["boxes"] if b["label"] == "VMs affected")["note"]
        self.assertEqual(tile, "1 VM confirmed · 2 with events · 39 with files on ds_shared_01")
        sheet = next(x for x in r["sheets"] if x["name"] == "VMs with events")
        self.assertEqual([row[:2] for row in sheet["rows"]], [["sitea-vm07", "VM powered off"],
                                                               ["sitea-vm03", "Alarm red: Virtual machine heartbeat"]])

    def test_no_double_listing_from_the_watchs_members(self):
        events, tickets, opts = tc.shared_ds_outage(still_down=True, members=["sitea-vm01", "sitea-vm02", "sitea-vm03"])
        events.append(dict(events[0], key=90011, time="2026-11-23T06:09:20Z", type="AlarmStatusChangedEvent", host=tc.HOSTS[1],
                           vm="sitea-vm02", alarm="Virtual machine heartbeat", entity="sitea-vm02", entity_type="vm", to="red",
                           **{"from": "green"}))
        d = ot.outage_timeline(events, tickets, opts)["draft"]
        # a member of the lost datastore with a signal of its own is confirmed (as since 1.112.4c), and listed only there
        self.assertIn("sitea-vm02", str(d["affected"]))
        self.assertEqual(d["files_lines"], ["2 VMs have files on ds_shared_01 (may have hung or failed): sitea-vm01, sitea-vm03"])

    def test_no_empty_table_above_the_lines(self):
        r = tc.review()
        section = next(x for x in r["web_sections"] if x["title"].startswith("What it affected"))
        self.assertNotIn("columns", section)
        from test_report_hover import render
        html = render("report_atlas.html.j2", ot.outage_timeline_doc(dict(title="x", sections=[]), r, True, True))
        part = html.split("What it affected: no VM confirmed", 1)[1].split("Down now", 1)[0]
        self.assertNotIn('class="none">None.', part)

    def test_usage_alarms_are_never_a_signal(self):
        r = tc.review()                                     # 307 usage-alarm changes on VMs of the lost hosts, in the window
        self.assertFalse([line for line in r["draft"]["files_lines"] if "events of their own" in line])


class Catalog(unittest.TestCase):
    def test_the_job_is_in_the_setup_catalog(self):
        import yaml
        root = os.path.join(REPO, "aap")
        entry = next(t for t in yaml.safe_load(open(os.path.join(root, "job_templates.yml")))["job_templates"]
                     if t["name"] == "VMware - VM placement snapshot")
        self.assertEqual((entry["playbook"], entry["credentials"], entry["area"], entry["ask_limit"]),
                         ("playbooks/vmware_placement_snapshot.yml", ["vmware", "report_db"], "VMware", False))
        self.assertTrue(os.path.isfile(os.path.join(REPO, entry["variables_file"])))
        self.assertIn("vm_placement_days: 400", open(os.path.join(REPO, entry["variables_file"])).read())
        self.assertIn("every 60 minutes", entry["description"]["does"].lower())


if __name__ == "__main__":
    unittest.main()
