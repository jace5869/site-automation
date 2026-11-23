#!/usr/bin/env python3
"""1.116.1: the rooted review's other sources, from the real writers on SQLite through the page, email and saved AAR."""
import datetime
import importlib
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

from test_portal_ad import Lite, ROOT, adb, sqlite_schema
from test_netmap_db import ndb
from test_solarwinds_reports import Client
from test_outage_timeline import WATCH, director_outage, ot, watch_run
import incident_doc_filters as docs
import site_outage_context as ctx
import site_swis_db as swdb

# The production Ansible module imports the execution environment's bundled module_utils.
import ansible.module_utils
for name in ("site_host_swis", "site_outage_context", "site_report_db", "site_swis_db", "site_time", "site_redact"):
    mod = importlib.import_module(name)
    sys.modules.setdefault("ansible.module_utils." + name, mod)
    setattr(ansible.module_utils, name, mod)
import site_vm_outage_context as reader

DAY = "2026-11-20 "
ORION = "orion-sitea.example.mil:17774"
HOST = "esx-sitea-01.example.mil"


def at(clock):
    return datetime.datetime.fromisoformat(DAY + clock)


def epoch(clock):
    return at(clock).replace(tzinfo=datetime.timezone.utc).timestamp()


class ReportDB(Lite):
    def __init__(self):
        super().__init__()
        for ddl in list(swdb.CREATE) + list(ndb.CREATE.values()):
            self.db.execute(sqlite_schema(ddl).replace("snap_id BIGINT NOT NULL AUTO_INCREMENT", "snap_id INTEGER"))
        self.guard_queries = False
        self.statements = []
        self.read_starts = 0
        self.reading = False
        self.rollbacks = 0

    def cursor(self):
        cur = super().cursor()
        execute = cur.execute
        cur.fetchone = lambda: (cur.rows[0] if getattr(cur, "rows", None) else None)   # 1.113.0's writer reads one row

        def run(sql, args=()):
            if "information_schema.columns" in sql:      # 1.113.0's writer checks its added columns: SQLite's own answer
                return execute("SELECT COUNT(*) FROM pragma_table_info(%s) WHERE name = %s", args)
            if "information_schema.tables" in sql:       # 1.118.7's writer checks sw_link_day: SQLite's own tables
                return execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'sw_link_day'")
            if sql == "ROLLBACK AND NO CHAIN":
                self.db.rollback()
                return
            if sql == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ":
                return
            if sql == "START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY":
                self.db.execute("PRAGMA query_only = ON")
                self.db.execute("BEGIN")
                self.read_starts += 1
                self.reading = True
                return
            if self.guard_queries:
                inside = self.reading and self.db.in_transaction
                self.statements.append((sql, inside))
                if not inside:
                    raise AssertionError("source query outside the read-only transaction")
            return execute(sql, args)
        cur.execute = run
        return cur

    def rollback(self):
        self.db.rollback()
        self.db.execute("PRAGMA query_only = OFF")
        self.reading = False
        self.rollbacks += 1


def snapshot(db):
    def event(n, t, nid, kind, message, oid=None):
        return dict(event_id=n, event_time=at(t), node_id=nid, type_name=kind, message=message,
                    net_object_type="I" if kind.startswith("Interface") else "N", net_object_id=oid)

    events = [event(1, "21:54:59", 3, "Node Warning", "too early"),
              event(2, "21:55:00", 2, "Interface Down", "Ethernet1/29 is Down", 29),
              event(3, "22:10:00", 3, "Node Down", "controller down"),
              event(4, "22:11:00", 4, "Node Down", "third hop at another site"),
              event(5, "22:49:00", 3, "Node Up", "controller up"),
              event(6, "23:05:00", 2, "Interface Up", "Ethernet1/29 is Up", 29),
              event(7, "23:05:01", 2, "Node Warning", "too late")]
    snap = dict(raw=dict(reachable=True, url=ORION, nodes=[dict(id=i, name=name, location="SITEB" if i == 4 else "SITEA") for i, name in enumerate(
        [HOST, "sitea-fc-sw01", "sitea-array01", "siteb-sw01"], 1)],
        interfaces=[dict(NodeID=2, InterfaceID=29, Caption="Ethernet1/29", OperStatus=1)],
        connections=[[2, 21, 1, 11], [2, 29, 3, 31], [3, 32, 4, 41]]),
        extra=dict(vim_host=[dict(host_id=11, name=HOST, node_id=1)], event=events,
                   alert=[dict(alert_active_id=5, node_id=3, triggered_at=at("22:10:00"), acknowledged=1,
                               name="Controller fault", severity=2, message="controller B"),
                          dict(alert_active_id=6, node_id=2, triggered_at=at("21:54:59"), name="outside alert")]))
    swdb.write(db, snap, now=at("23:30:00"))


def history(db):
    for i, (t, status) in enumerate([("21:54:59", "Down"), ("22:10:00", "Down"), ("22:15:00", "Up")]):
        ndb.write(db, at(t), i + 1, True, [dict(site="SITEA", nodes=5, down=int(status == "Down"), status="Warning")],
                  [dict(key="wan", site_a="SITEA", site_b="HUB", status=status)], source=ORION)
    link = dict(frm="dc-sitea-01", to="dc-hub-01", nc="DomainDnsZones", last_failure=at("22:14:30"),
                error="The RPC server is unavailable.", failures=1)
    run = dict(status="warning", dcs=[dict(dc="dc-sitea-01", site="SITEA")], links=[link])
    adb.write(db, at("22:16:00"), 1, 7, run)
    adb.write(db, at("23:30:00"), 2, 7, dict(run, links=[dict(link, failures=0, last_success=at("22:22:00"))]))
    db.commit()


def cards(result):
    return {c["chip"]: c for c in result["cards"]}


class OutageContext(unittest.TestCase):
    def setUp(self):
        self.db = ReportDB()
        self.addCleanup(self.db.db.close)
        snapshot(self.db)
        history(self.db)
        self.scope = dict(begin=epoch("22:10:00"), end=epoch("22:50:00"), hosts=[HOST, "esx-sitea-02"])
        self.params = dict(scope=self.scope, minutes=15, storage_nodes=[], syslog=True, time_zone="UTC",
                           url="", validate_certs=True, ca_path="", timeout=5)

    def test_two_hops_both_directions_and_inclusive_margin(self):
        r = ctx.read(self.db, self.scope, storage_nodes=["siteb-*"])
        c = cards(r)
        self.assertEqual(r["judged"], "SolarWinds topology: 1 of 2 hosts linked to storage (sw_link)")
        self.assertIn("4 events on the storage path", c[ctx.EVENTS]["title"])
        self.assertIn("1 event elsewhere", c[ctx.EVENTS]["subject"])
        for text in ("third hop at another site", "too early", "too late", "outside alert"):
            self.assertNotIn(text, str(r))
        self.assertIn("21:55:00 UTC", c[ctx.EVENTS]["next"])
        self.assertIn("23:05:00 UTC", c[ctx.EVENTS]["next"])
        self.assertIn("acknowledged", c[ctx.ALERTS]["title"])
        self.assertIn("Only alerts still active", c[ctx.ALERTS]["next"])
        self.assertIn("2 successful map reads", str(c[ctx.MAP]))
        self.assertIn("SITEA - HUB", c[ctx.MAP]["next"])
        self.assertIn("Worked again", c[ctx.AD]["title"])
        self.assertEqual([(v["group"], v["state"]) for v in r["issues"] if v["kind"] == "dc"], [("AD site SITEA", "Recovered")])
        self.assertEqual({(v["name"], v["state"]) for v in r["issues"] if v["kind"] == "net"},
                         {("sitea-array01", "At risk"), ("sitea-fc-sw01 · Ethernet1/29", "Recovered"), ("SITEA - HUB", "Recovered")})
        self.assertEqual(self.db.rollbacks, 1)
        narrow = cards(ctx.read(self.db, self.scope, minutes=0))
        self.assertIn("2 events on the storage path", narrow[ctx.EVENTS]["title"])
        self.assertNotIn("Ethernet1/29", narrow[ctx.EVENTS]["next"])

    def test_source_queries_require_read_only_transaction(self):
        self.db.guard_queries = True
        ctx.read(self.db, self.scope)
        self.assertEqual(self.db.read_starts, 1)
        self.assertTrue(self.db.statements)
        for sql, inside in self.db.statements:
            self.assertTrue(inside, sql)
            self.assertTrue(sql.startswith("SELECT") and " LIMIT " in sql, sql)
        self.assertEqual(self.db.rollbacks, 1)
        self.assertFalse(self.db.db.in_transaction)

    def test_isolated_vim_host_is_outside_the_path(self):
        isolated = self.scope["hosts"][1]
        self.db.db.execute("INSERT INTO sw_vim_host (snap_id, host_id, name, name_key, node_id) VALUES (1, 12, ?, ?, 5)",
                           (isolated, isolated))
        self.db.db.execute("INSERT INTO sw_event (orion, event_id, event_time, node_id, message, snap_id) VALUES (?, 9, ?, 5, ?, 1)",
                           (ORION, at("22:30:00"), "isolated host event"))
        self.db.db.execute("UPDATE sw_alert_active SET node_id=5, triggered_at=? WHERE alert_active_id=6", (at("22:30:00"),))
        self.db.commit()
        live = Mock(return_value=dict(ok=True, by_node={}, capped=False))
        result = ctx.read(self.db, self.scope, storage_nodes=["sitea-fc-*", "sitea-array01"], live=live)
        self.assertIn("1 of 2 hosts linked", result["judged"])
        self.assertIn("4 events on the storage path", cards(result)[ctx.EVENTS]["title"])
        self.assertNotIn("isolated host event", str(result))
        self.assertNotIn("outside alert", str(result))
        self.assertEqual(live.call_args.args[1], [1, 2, 3])

    def test_shared_switch_and_wildcard_require_site_and_links(self):
        self.db.db.execute("UPDATE sw_link SET src_node=2 WHERE dst_node=4")
        self.db.db.execute("UPDATE sw_alert_active SET node_id=4, triggered_at=? WHERE alert_active_id=6", (at("22:30:00"),))
        self.db.commit()
        live = Mock(return_value=dict(ok=True, by_node={}, capped=False))
        result = ctx.read(self.db, self.scope, storage_nodes=["*"], live=live)
        self.assertEqual(live.call_args.args[1], [1, 2, 3])
        self.assertIn("1 event elsewhere", cards(result)[ctx.EVENTS]["subject"])
        self.assertNotIn("third hop at another site", str(result))
        self.assertNotIn("outside alert", str(result))
        self.db.db.execute("DELETE FROM sw_link")
        self.db.db.execute("INSERT INTO sw_event (orion, event_id, event_time, node_id, message, snap_id) VALUES (?, 8, ?, 1, ?, 1)",
                           (ORION, at("22:30:00"), "unlinked ESXi event"))
        self.db.commit()
        live.reset_mock()
        result = ctx.read(self.db, self.scope, storage_nodes=["*"], live=live)
        self.assertIn("6 events elsewhere", cards(result)[ctx.EVENTS]["subject"])
        self.assertNotIn("unlinked ESXi event", str(result))
        self.assertIn(ctx.UNKNOWN, cards(result)[ctx.ALERTS]["title"])
        live.assert_not_called()

    def test_event_node_ids_use_their_collecting_snapshot(self):
        snap = dict(raw=dict(reachable=True, url=ORION, nodes=[dict(id=1, name=HOST, location="SITEA"),
                    dict(id=4, name="sitea-fc-sw02", location="SITEA")], connections=[[1, 11, 4, 42]]),
                    event_orion=ORION + "#2", extra=dict(vim_host=[dict(host_id=11, name=HOST, node_id=1)],
                    event=[dict(event_id=1, event_time=at("23:00:00"), node_id=4, message="new fabric event")]))
        swdb.write(self.db, snap, now=at("23:45:00"))
        result = cards(ctx.read(self.db, self.scope))[ctx.EVENTS]
        self.assertIn("5 events on the storage path", result["title"])
        self.assertIn("1 event elsewhere", result["subject"])
        self.assertIn("new fabric event", result["next"])
        self.assertIn("controller down", result["next"])
        self.assertNotIn("third hop at another site", str(result))
        self.db.db.execute("DELETE FROM sw_link WHERE snap_id=1")
        self.db.commit()
        result = cards(ctx.read(self.db, self.scope))[ctx.EVENTS]
        self.assertRegex(result["title"], r"^1 events? on the storage path")
        self.assertIn("5 events elsewhere", result["subject"])

    def test_ad_latest_read_wins_over_insert_order(self):
        self.db.db.execute("DELETE FROM ad_link")
        link = dict(frm="example-dc", to="example-peer", nc="example", last_failure=at("22:20:00"), failures=1)
        success = dict(link, failures=0, last_success=at("22:30:00"))
        for job, (when, row) in enumerate([
                ("23:00:00", success), ("23:00:00", link), ("22:40:00", success),
                ("22:35:00", dict(success, last_failure=at("22:10:00")))], 3):
            adb.write(self.db, at(when), job, 7, dict(dcs=[], links=[row]))
        self.db.commit()
        controller, = [i for i in ctx.read(self.db, self.scope)["issues"] if i["kind"] == "dc"]
        self.assertEqual([i["state"] for i in controller["items"]], ["Recovered", "At risk"])
        self.assertEqual(controller["state"], "At risk")
        self.assertIn("23:00:00 UTC", controller["items"][1]["what"])

    def test_controller_with_unread_link_is_not_recovered(self):
        self.db.db.execute("DELETE FROM ad_link")
        links = [dict(frm="example-dc", to="example-peer-%02d" % i, nc="example", failures=int(i == 40),
                      last_failure=at("22:14:%02d" % i), last_success=at("22:13:00" if i == 40 else "22:20:00"))
                 for i in range(41)]
        adb.write(self.db, at("23:30:00"), 3, 7, dict(status="warning", dcs=[], links=links))
        self.db.commit()
        result = ctx.read(self.db, self.scope)
        controller, = [i for i in result["issues"] if i["kind"] == "dc"]
        self.assertEqual(controller["n"], 40)
        self.assertTrue(all(item["state"] == "Recovered" for item in controller["items"]))
        self.assertEqual(controller["state"], "Not judged")
        self.db.db.execute("UPDATE ad_link SET failures=1 WHERE frm='example-dc'")
        self.db.commit()
        controller, = [i for i in ctx.read(self.db, self.scope)["issues"] if i["kind"] == "dc"]
        self.assertTrue(all(item["state"] == "At risk" for item in controller["items"]))
        self.assertEqual(controller["state"], "Not judged")

    def test_absent_application_table(self):
        self.db.db.execute("DROP TABLE sw_app")
        result = ctx.read(self.db, self.scope)
        app = cards(result)[ctx.APPS]
        self.assertEqual(app["title"], ctx.MISSING)
        self.assertNotIn("snapshot", app["subject"])
        self.assertIn("4 events on the storage path", cards(result)[ctx.EVENTS]["title"])

    def test_no_topology_names_patterns_and_unknown(self):
        self.db.db.execute("DELETE FROM sw_link")
        self.db.commit()
        r = ctx.read(self.db, self.scope, storage_nodes=["SITEA-FC-*", "sitea-array01"])
        self.assertIn("0 of 2 hosts linked", r["judged"])
        self.assertIn("5 events elsewhere", cards(r)[ctx.EVENTS]["subject"])
        self.assertIn(ctx.UNKNOWN, cards(r)[ctx.EVENTS]["title"])
        live = Mock(side_effect=AssertionError("unknown scope must not query Orion"))
        for setting in ([], ["absent*"]):
            r = ctx.read(self.db, self.scope, storage_nodes=setting, live=live)
            for source in (ctx.EVENTS, ctx.ALERTS, ctx.SYSLOG):
                self.assertIn(ctx.UNKNOWN, cards(r)[source]["title"])
            self.assertIn("RPC", str(cards(r)[ctx.AD]))
        live.assert_not_called()

    def test_fqdn_and_short_name_are_unambiguous(self):
        self.db.db.execute("UPDATE sw_vim_host SET name = name_key")
        self.db.commit()
        self.assertIn("1 of 2 hosts linked", ctx.read(self.db, self.scope)["judged"])
        self.db.db.execute("INSERT INTO sw_vim_host (snap_id, host_id, name, name_key, node_id) VALUES (1, 12, ?, ?, 4)",
                           ("esx-sitea-01.siteb.example.mil", "esx-sitea-01"))
        self.db.commit()
        r = ctx.read(self.db, self.scope)
        self.assertIn("0 of 2 hosts linked", r["judged"])
        self.assertIn(ctx.UNKNOWN, cards(r)[ctx.EVENTS]["title"])

    def test_empty_window_and_unrelated_orion(self):
        self.db.db.execute("INSERT INTO sw_event (orion, event_id, event_time, node_id, message, snap_id) VALUES (?, 2, ?, 3, ?, 1)",
                           ("orion-siteb.example.mil:17774", at("22:30:00"), "another Orion"))
        self.db.commit()
        r = ctx.read(self.db, dict(self.scope, begin=epoch("22:29:00"), end=epoch("22:31:00")), minutes=0)
        self.assertEqual(r["issues"], [])
        for source in (ctx.EVENTS, ctx.ALERTS, ctx.MAP, ctx.AD, ctx.APPS):
            self.assertEqual(cards(r)[source]["title"], ctx.EMPTY)
        self.assertNotIn("another Orion", str(r))
        self.assertIn("0 events elsewhere", cards(r)[ctx.EVENTS]["subject"])
        self.assertIn("not read", cards(r)[ctx.TRAPS]["title"])
        self.assertIn("snapshot only", cards(r)[ctx.APPS]["subject"])

    def test_missing_table_only_affects_its_source(self):
        for table, source in (("sw_alert_active", ctx.ALERTS), ("netmap_link", ctx.MAP), ("ad_run", ctx.AD)):
            self.db.db.execute("DROP TABLE " + table)
            r = ctx.read(self.db, self.scope)
            self.assertEqual(cards(r)[source]["title"], "no data yet")
            self.assertIn("4 events on the storage path", cards(r)[ctx.EVENTS]["title"])
        cur = Mock()
        cur.execute.side_effect = RuntimeError(1146, "table absent")
        with self.assertRaisesRegex(ctx.NoData, "^no data yet$"):
            ctx.select(cur, "SELECT 1")
        cur.execute.side_effect = RuntimeError(1932, "no such table")
        with self.assertRaisesRegex(ctx.NoData, "^not read: " + ctx.DB_ERROR + "$"):
            ctx.select(cur, "SELECT 1")

    def test_later_source_fault_keeps_successful_cards(self):
        self.db.db.execute("ALTER TABLE netmap_link RENAME COLUMN status TO missing_status")
        result = cards(ctx.read(self.db, self.scope))
        self.assertEqual(result[ctx.MAP]["title"], "not read: " + ctx.DB_ERROR)
        self.assertIn("4 events on the storage path", result[ctx.EVENTS]["title"])
        self.assertIn("Controller fault", result[ctx.ALERTS]["title"])
        self.assertIn("Worked again", result[ctx.AD]["title"])

    def test_database_fault_is_fixed_and_the_review_continues(self):
        secret = "placeholder-password"
        broken = Mock()
        broken.cursor.return_value.execute.side_effect = RuntimeError(secret)
        with patch.object(reader.db, "connect", return_value=(broken, "none")):
            r = reader.review(self.params)
        broken.close.assert_called_once()
        with patch.object(reader.db, "connect", side_effect=RuntimeError(secret)):
            self.assertEqual(reader.review(self.params), r)
        self.assertNotIn(secret, str(r))
        review = ot.outage_timeline(*director_outage())
        for reason in (ctx.DB_ERROR, "no Report database credential", "vm_watch_review_context is off"):
            enriched = ot.outage_review_context(review, r if reason == ctx.DB_ERROR else None, reason)
            self.assertEqual(enriched["title"], review["title"])
            self.assertEqual(len(enriched["draft"]["what_else"]), 7)
            self.assertEqual({c["title"] for c in enriched["draft"]["what_else"]}, {"not read: " + reason})
            self.assertIn("not read: " + reason, str(ot.outage_timeline_doc(WATCH, enriched, False, True)))

    def test_live_read_once_capped_with_historical_upper_bound(self):
        client = Client(rows=[dict(NodeID=2, Level="Warning", DateTime="2026-11-20T21:56:01Z", Message="fabric port warning")] * 150)
        client.url = ORION
        def ready(*args, **kwargs):
            self.assertFalse(self.db.reading)
            self.assertEqual(self.db.rollbacks, 1)
            return client
        with patch.object(reader.db, "connect", return_value=(self.db, "none")), patch.object(reader.hs, "client_of", side_effect=ready):
            c = cards(reader.review(self.params))[ctx.SYSLOG]
        reads = [(q, params) for q, params in client.q if "FROM Orion.OLM.LogEntry " in q]
        self.assertEqual(len(reads), 1)
        query, params = reads[0]
        self.assertIn("TOP 100", query)
        self.assertIn("DateTime <= @until", query)
        self.assertEqual(params, dict(ids=[1, 2, 3], since="2026-11-20T21:55:00", until="2026-11-20T23:05:00"))
        self.assertIn("100 entries", c["title"])
        self.assertIn("cap reached", c["subject"])
        self.assertIn("21:56:01 UTC", c["next"])

    def test_syslog_unread_reasons_preserve_other_sources(self):
        with patch.object(reader.db, "connect", return_value=(self.db, "none")), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(cards(reader.review(self.params))[ctx.SYSLOG]["title"], "not read: no SolarWinds credential")
            for client in (Client(fail=RuntimeError("placeholder-password")), Client(entity=False), Client()):
                client.url = ORION if client.fail or not client.entity else "orion-siteb.example.mil"
                with patch.object(reader.hs, "client_of", return_value=client):
                    r = reader.review(self.params)
                title = cards(r)[ctx.SYSLOG]["title"]
                self.assertTrue(title.startswith("not read: "))
                self.assertRegex(title, "could not be read|no Log Analyzer|different Orion")
                self.assertNotIn("placeholder-password", str(r))
                self.assertIn("4 events", cards(r)[ctx.EVENTS]["title"])
            with patch.object(reader.hs, "client_of") as create:
                self.assertIn("syslog is off", cards(reader.review(dict(self.params, syslog=False)))[ctx.SYSLOG]["title"])
                create.assert_not_called()

    def test_only_roots_select_context_hosts_and_dates(self):
        host = dict(name=HOST, moid="host-1", datacenter="SITEA")
        loss = dict(type="HostConnectionLostEvent", host=HOST, vcenter="vc", time=epoch("22:10:00"))
        events = [loss, dict(loss, type="HostConnectedEvent", time=epoch("22:50:00")),
                  dict(loss, host="esx-elsewhere.example.mil", time=epoch("21:45:00"))]
        opts = dict(begin=epoch("21:00:00"), end=epoch("23:30:00"), hosts=[dict(host, vcenter="vc")])
        # 1.116.2: with no watch root, vCenter's loss events are the roots - both lost hosts are read
        fallback = ot.outage_timeline(events, [], opts)["context_scope"]
        self.assertEqual(sorted(h["name"] for h in fallback["hosts"]), ["esx-elsewhere.example.mil", HOST])
        opts["watch_runs"] = [watch_run(epoch("22:20:00"), [host])]
        scope = ot.outage_timeline(events, [], opts)["context_scope"]
        expected = dict(vcenter="vc", host=HOST.split(".")[0], name=HOST, site="SITEA")
        self.assertEqual(scope, dict(begin=self.scope["begin"], end=self.scope["end"], hosts=[expected], sites=["SITEA"]))
        self.assertEqual(ctx.read(self.db, scope)["judged"], "SolarWinds topology: 1 of 1 hosts linked to storage (sw_link)")
        self.assertEqual(ot.outage_timeline(events, [], dict(opts, review_object="another-root"))["context_scope"], {})

    def test_same_hostname_on_two_vcenters_is_unresolved(self):
        host = dict(name="esx01", moid="host-1", datacenter="SITEA")
        runs = [watch_run(epoch("22:20:00"), [host], vc=vc) for vc in ("vc-a", "vc-b")]
        review = ot.outage_timeline([], [], dict(begin=self.scope["begin"], end=self.scope["end"], watch_runs=runs))
        self.db.db.execute("UPDATE sw_vim_host SET name='esx01', name_key='esx01'")
        self.db.commit()
        result = ctx.read(self.db, review["context_scope"])
        self.assertIn("0 of 2 hosts linked", result["judged"])
        self.assertIn("ambiguous", result["judged"])
        self.assertIn("unresolved", result["judged"])
        self.assertIn("2 events elsewhere", cards(result)[ctx.EVENTS]["subject"])

    def test_page_email_and_saved_aar(self):
        from test_report_fit import EmailTiles
        r = ot.outage_timeline(*director_outage())
        story = r["draft"]["story"]["window"]
        self.assertEqual(r["context_scope"]["begin"], datetime.datetime.fromisoformat(story["start"]["iso"]).timestamp())
        self.assertEqual(r["context_scope"]["end"], datetime.datetime.fromisoformat(story["end"]["iso"]).timestamp())
        self.assertEqual([h["name"] for h in r["context_scope"]["hosts"]],
                         ["esx-sitea-%02d.corp.example.mil" % n for n in (2, 4, 5)])
        host = r["context_scope"]["hosts"][0]["name"]
        self.db.db.execute("UPDATE sw_vim_host SET name=?, name_key=?", (host, host.split(".")[0]))
        self.db.commit()
        context = ctx.read(self.db, r["context_scope"])
        self.assertEqual(context["judged"], "SolarWinds topology: 1 of 3 hosts linked to storage (sw_link)")
        enriched = ot.outage_review_context(r, context)
        env = EmailTiles()._env()
        for web, look in ((True, "_atlas"), (False, "")):
            doc = ot.outage_timeline_doc(WATCH, enriched, web, True)
            titles = [s["title"] for s in doc["sections"]]
            previous = titles[titles.index(ctx.TITLE) - 1]
            self.assertTrue(previous.endswith(" - chart") if web else previous == "4. Recovered")
            html = env.get_template("report%s.html.j2" % look).render(_report=doc, _email_footer="AAP job 1",
                       _page_logo_cid="", _page_runs=[], report_email_header_text="Outage review")
            for source in ctx.SOURCES:
                self.assertIn(source, html)
            self.assertGreaterEqual(html.count("same time, not cause"), 7)
            self.assertIn(context["judged"], html)
        for kinds in ([], ["dc"], ["net"], ["net", "dc"]):
            section = next(s for s in enriched["web_sections"] if s.get("kind") == "by_kind")
            part = env.get_template("report_by_kind.html.j2").render(s=dict(section, issues=[i for i in section["issues"] if i["kind"] in kinds]))
            for k in ("net", "dc"):
                self.assertEqual('for="ibk-%s"' % k in part, k in kinds)
        draft = docs.incident_review_draft(enriched["draft"])
        record = docs.incident_review_fill(docs.incident_record({"kind": "aar"}), draft)
        blocks = docs.incident_aar_docx(record, {}, review=draft)["blocks"]
        heads = [b["text"] for b in blocks if b["type"] == "heading"]
        self.assertEqual(heads[heads.index(ctx.TITLE) + 1], "Lessons Learned & Action Plan")
        for source in ctx.SOURCES:
            self.assertIn(source + " (same time, not cause)", str(blocks))
        self.assertEqual(docs.incident_noc_docx(draft, {}), docs.incident_noc_docx(r["draft"], {}))

    def test_snapshot_redacts_before_caption_and_message_limits(self):
        url = "https://reader:example-value-" + "x" * 1000 + "@example.invalid/path"
        rows = swdb.to_rows(dict(raw={}, extra=dict(alert=[dict(alert_active_id=5, entity_caption=url, message=url)])), 1)
        alert = dict(zip(swdb.COLS["sw_alert_active"], rows["sw_alert_active"][0]))
        for field in ("entity_caption", "message"):
            self.assertNotIn("example-value", alert[field])
            self.assertIn("example.invalid/path", alert[field])

    def test_legacy_snapshot_cut_inside_userinfo(self):
        url = "https://reader:example-value-" + "x" * 1000 + "@example.invalid/path"
        self.db.db.execute("UPDATE sw_alert_active SET entity_caption=?, message=? WHERE alert_active_id=5", (url[:512], url[:1000]))
        self.db.db.execute("UPDATE sw_event SET message=? WHERE event_id=3", (url[:1000],))
        self.db.commit()
        result = ctx.read(self.db, self.scope)
        self.assertIn("Controller fault", cards(result)[ctx.ALERTS]["title"])
        self.assertNotIn("example-value", str(result))
        self.assertNotIn("https://reader:", str(result))

    def test_url_userinfo_removed_before_source_limits(self):
        for user in ("o'brien", "example:reader", "example%27reader"):
            with self.subTest(user=user):
                url = "https://" + user + ":example-value-" + "x" * 350 + "@example.invalid/path"
                self.db.db.execute("UPDATE sw_event SET message=? WHERE event_id=3", (url,))
                self.db.commit()
                client = Client(rows=[dict(NodeID=2, Level="Warning", DateTime="2026-11-20T22:14:00Z", Message=url)])
                client.url = ORION
                with patch.object(reader.db, "connect", return_value=(self.db, "none")), patch.object(reader.hs, "client_of", return_value=client):
                    context = reader.review(self.params)
                for source in (ctx.EVENTS, ctx.SYSLOG):
                    self.assertIn("https://example.invalid/path", cards(context)[source]["next"])
                review = ot.outage_review_context(ot.outage_timeline(*director_outage()), context)
                job = "\n".join(review["lines"])
                for fragment in (user, "example-value", "@example.invalid"):
                    self.assertNotIn(fragment, job)

    def test_source_passwords_redacted_in_rendered_outputs(self):
        import tempfile
        import zipfile
        from test_incident_docs import d as docx
        from test_report_fit import EmailTiles

        fields = ("caption", "node", "interface", "alert", "message", "syslog")
        urls = {f: "https://reader:placeholder-%s@example.invalid/%s" % (f, f) for f in fields}
        self.db.db.execute("UPDATE sw_alert_active SET entity_caption=?, message=? WHERE alert_active_id=5",
                           (urls["caption"], urls["alert"]))
        self.db.db.execute("UPDATE sw_node SET name=? WHERE node_id=2", (urls["node"],))
        self.db.db.execute("UPDATE sw_interface SET caption=? WHERE interface_id=29", (urls["interface"],))
        self.db.db.execute("UPDATE sw_event SET message=? WHERE event_id=3", (urls["message"],))
        self.db.commit()
        client = Client(rows=[dict(NodeID=2, Level="Warning", DateTime="2026-11-20T22:14:00Z", Message=urls["syslog"])])
        client.url = ORION
        with patch.object(reader.db, "connect", return_value=(self.db, "none")), patch.object(reader.hs, "client_of", return_value=client):
            result = reader.review(self.params)
        for field in fields:
            self.assertIn("example.invalid/" + field, str(result))
        review = ot.outage_review_context(ot.outage_timeline(*director_outage()), result)
        outputs = {"job": "\n".join(review["lines"]), "reader": str(result)}
        env = EmailTiles()._env()
        for web, template in ((True, "report_atlas.html.j2"), (False, "report.html.j2")):
            doc = ot.outage_timeline_doc(WATCH, review, web, True)
            outputs[template] = env.get_template(template).render(_report=doc, _email_footer="AAP job 1",
                                _page_logo_cid="", _page_runs=[], report_email_header_text="Outage review")
        draft = docs.incident_review_draft(review["draft"])
        record = docs.incident_review_fill(docs.incident_record({"kind": "aar"}), draft)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "example.docx")
            docx.write_docx(path, docs.incident_aar_docx(record, {}, review=draft))
            with zipfile.ZipFile(path) as package:
                outputs["AAR"] = package.read("word/document.xml").decode()
        for channel, output in outputs.items():
            self.assertIn("example.invalid/caption", output, channel)
            for field in fields:
                self.assertNotIn("placeholder-" + field, output, channel)

    def test_throttle_and_module_rescue(self):
        import yaml
        tasks = yaml.safe_load(Path(ROOT, "roles/vmware_vm/tasks/watch.yml").read_text())
        block = next(t for t in tasks if t["name"] == "VMware | what else saw the outage")
        self.assertIn("_vm_w_review | bool", block["when"])
        self.assertIn("not _vm_w_tl.skip", block["when"])
        task = block["block"][0]
        self.assertIn("site_vm_outage_context", task)
        self.assertIn("vm_watch_review_context | bool and _vm_w_db_host | length > 0", task["when"])
        self.assertTrue(task["no_log"])
        self.assertIn("outage_review_context", block["rescue"][0]["ansible.builtin.set_fact"]["_vm_w_tl"])


if __name__ == "__main__":
    unittest.main()
