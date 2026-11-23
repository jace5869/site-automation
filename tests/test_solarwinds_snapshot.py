#!/usr/bin/env python3
"""Unit tests for SolarWinds in the report database (1.96.0): plugins/module_utils/site_swis_db.py against the fake SWIS
(tests/netmap/fake_swis.py over tests/solarwinds/fixture_more.py) - what one snapshot reads, the rows it writes, the dict
it gives back (exactly site_swis.collect()'s), a module this Orion lacks, events read incrementally, the reader's
choice between the snapshot and SolarWinds itself, the other reports' readers, the job's lines and the doc's statements.
The real database: tests/solarwinds/run_snapshot_test.sh (MySQL 8.4 and MariaDB 10.11)."""
import datetime
import json
import os
import re
import shutil
import sqlite3
import ssl
import socket
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "plugins", "module_utils"), os.path.join(ROOT, "plugins", "filter")]
import site_report_db as report_db  # noqa: E402
import site_swis_db as sd  # noqa: E402
sw = sd.site_swis       # the site_swis the reader uses: another test may have loaded it as ansible.module_utils.site_swis
import solarwinds_filters as sf  # noqa: E402

PW = 'ro&"<test>9'
TMP = tempfile.mkdtemp()


def certs():
    ca, key, crt = (os.path.join(TMP, n) for n in ("ca.pem", "srv.key", "srv.pem"))
    if not os.path.exists(crt):
        q = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.check_call(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=Test CA",
                               "-keyout", os.path.join(TMP, "ca.key"), "-out", ca, "-addext", "basicConstraints=critical,CA:TRUE",
                               "-addext", "keyUsage=critical,keyCertSign"], **q)
        subprocess.check_call(["openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=127.0.0.1", "-keyout", key,
                               "-out", os.path.join(TMP, "srv.csr")], **q)
        with open(os.path.join(TMP, "ext.cnf"), "w") as fh:
            fh.write("subjectAltName=IP:127.0.0.1\nbasicConstraints=CA:FALSE\n")
        subprocess.check_call(["openssl", "x509", "-req", "-in", os.path.join(TMP, "srv.csr"), "-CA", ca, "-CAkey",
                               os.path.join(TMP, "ca.key"), "-CAcreateserial", "-days", "2", "-out", crt, "-extfile",
                               os.path.join(TMP, "ext.cnf")], **q)
    return ca, crt, key


def fixtures():
    plain, more = os.path.join(TMP, "fx0.json"), os.path.join(TMP, "fx.json")
    if not os.path.exists(more):
        subprocess.check_call([sys.executable, os.path.join(ROOT, "tests", "netmap", "fixture.py"), plain, "600", "svc_swis_ro", PW],
                              stdout=subprocess.DEVNULL)
        subprocess.check_call([sys.executable, os.path.join(ROOT, "tests", "solarwinds", "fixture_more.py"), plain, more],
                              stdout=subprocess.DEVNULL)
    return plain, more


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Fake(object):
    """One fake SWIS process."""

    def __init__(self, fx, env=None):
        self.port = free_port()
        self.log = os.path.join(TMP, "swis-%d.log" % self.port)
        self.ca, crt, key = certs()
        self.p = subprocess.Popen([sys.executable, os.path.join(ROOT, "tests", "netmap", "fake_swis.py"), str(self.port), fx, self.log, crt, key],
                                  env=dict(os.environ, **(env or {})))
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", self.port), 0.2).close()
                break
            except OSError:
                import time
                time.sleep(0.05)

    def client(self):
        return sw.Client("https://127.0.0.1:%d" % self.port, "svc_swis_ro", PW, ca_path=self.ca, timeout=10)

    def queries(self):
        with open(self.log) as fh:
            return fh.read().splitlines()

    def stop(self):
        self.p.terminate()
        self.p.wait()


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def as_db(rows, table):
    """A table's rows as the database gives them back: dicts by column."""
    return [dict(zip(sd.COLS[table], r)) for r in rows[table]]


def back(snap, opts):
    rows = sd.to_rows(snap, 1)
    core = json.loads(json.dumps(sd.core_meta(snap)))
    return sd.raw_from_rows(core, as_db(rows, "sw_node"), as_db(rows, "sw_link"), as_db(rows, "sw_interface"), opts, "2026-11-21T09:00:00Z", 120)


def same(live, db):
    """collect()'s dict and the reader's, without what is the read's own (when, how long, how many queries) and the reader's
    three keys; a custom property that is not there - the same line, its reason in the reader's words."""
    a, b = dict(live), dict(db)
    for k in ("read_at", "seconds", "queries", "snapshot_at", "age_seconds", "source"):
        a.pop(k, None)
        b.pop(k, None)
    for r in (a, b):
        r["not_read"] = [dict(x, why="") if x["what"].startswith("custom property ") else x for x in r["not_read"]]
    return a, b


class Snapshot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plain, cls.more = fixtures()
        cls.fake = Fake(cls.more)
        cls.snap = sd.read_swis(cls.fake.client(), {"page": 300})

    @classmethod
    def tearDownClass(cls):
        cls.fake.stop()

    def test_each_vim_host_has_its_vcenter(self):
        # 1.113.0: Orion.VIM.Hosts.DataCenterID -> Orion.VIM.DataCenters.VCenterID -> Orion.VIM.VCenters.Name
        self.assertEqual(set(h.get("vcenter") for h in self.snap["extra"]["vim_host"]), {"vc01.example.mil"})
        self.assertTrue(all("datacenter_id" not in h for h in self.snap["extra"]["vim_host"]))

    def test_every_part_is_read(self):
        s = self.snap
        self.assertTrue(s["raw"]["reachable"])
        self.assertEqual(s["not_read"], [])
        self.assertEqual(sorted(s["extra"]), ["alert", "app", "component", "event", "iface", "ip", "node", "vim_host", "vim_vm", "volume"])
        self.assertEqual(s["props"], ["Lat", "Lon", "Region", "Site"])
        self.assertTrue(s["worldmap"])
        alert = [a for a in s["extra"]["alert"] if a["node_caption"] == "esx02"]
        self.assertEqual([(a["name"], a["severity"]) for a in alert], [("Node is down", 2)])
        self.assertEqual(sorted(set(c["status"] for c in s["extra"]["component"])), [2, 3])     # not up only
        app = [a for a in s["extra"]["app"] if a["app_id"] == 309][0]                       # 2 of 9 down
        self.assertEqual((app["components"], app["components_not_up"]), (9, 2))
        self.assertEqual(sorted((a["components"], a["components_not_up"]) for a in s["extra"]["app"] if a["app_id"] != 309),
                         [(3, 0)] * 17 + [(3, 1)] * 2)
        down = [c for c in s["extra"]["component"] if c["app_id"] == 309]
        self.assertEqual([(c["name"], c["value"], c["message"]) for c in down],
                         [("Component 1", 0.0, "Port 443 refused the connection"), ("Component 5", 40.0, "Port 443 refused the connection")])
        self.assertIn("GROUP BY ApplicationID", "\n".join(self.fake.queries()))
        self.assertEqual([h["cluster"] for h in s["extra"]["vim_host"]], ["cluster1"] * 3)

    def test_only_named_columns_never_a_secret_or_a_person(self):
        q = "\n".join(self.fake.queries())
        self.assertNotIn("SECRET-COLUMN-ASKED", q)
        for word in ("AcknowledgedBy", "AcknowledgedNote", "Engine,", " Engine ", "Community", "LicenseKey"):
            self.assertNotIn(word, q, word)
        self.assertTrue(all(l.startswith("POST /SolarWinds/InformationService/v3/Json/Query ") for l in self.fake.queries()))

    def test_the_rows(self):
        rows = sd.to_rows(self.snap, 7)
        n = len(self.snap["raw"]["nodes"])
        self.assertEqual(len(rows["sw_node"]), n)
        self.assertTrue(all(r[0] == 7 for t in sd.STATE for r in rows[t]))
        node = dict(zip(sd.COLS["sw_node"], [r for r in rows["sw_node"] if r[4] == "esx02"][0]))
        self.assertEqual((node["dns"], node["status_code"], node["kind"]), ("esx02.example.mil", 2, "ICMP"))
        self.assertIsInstance(node["last_poll"], datetime.datetime)
        self.assertEqual(json.loads(node["props"]), {"Lat": None, "Lon": None, "Region": "Region 1", "Site": "SITE-A"})
        with_ips = [r for r in rows["sw_node"] if r[7] and len(json.loads(r[7])) == 2]
        self.assertEqual(len(with_ips), 10)
        c = sd.counts(rows, self.snap)
        self.assertEqual((c["apps"], c["apps_down"], c["vim_hosts"], c["vim_vms"], c["alerts"]), (20, 2, 3, 36, 7))
        self.assertEqual(c["nodes"], n)

    def test_names_with_a_secrets_word_stay_names(self):
        """1.122.2: the backstop never takes a node's, a VM's or an alert's name whole (it broke name_key's matching)."""
        snap = {"raw": {"url": "orion.example.mil", "nodes": [{"id": 1, "name": "token-01", "dns": "token-01.corp.example.mil"},
                                                             {"id": 2, "name": "srv-key01", "dns": ""}]},
                "extra": {"vim_vm": [{"vm_id": 5, "host_id": 1, "name": "pw-app01"}],
                          "vim_host": [{"host_id": 1, "name": "app_keycloak.corp.example.mil"}],
                          "alert": [{"alert_active_id": 1, "alert_id": 2, "name": "SNMP Community Mismatch",
                                     "message": "password=Hunter2x on this line"}]}}
        rows = sd.to_rows(snap, 3)
        nodes = [dict(zip(sd.COLS["sw_node"], r)) for r in rows["sw_node"]]
        self.assertEqual([(n["name"], n["name_key"], n["dns"]) for n in nodes],
                         [("token-01", "token-01", "token-01.corp.example.mil"), ("srv-key01", "srv-key01", "")])
        vm = dict(zip(sd.COLS["sw_vim_vm"], rows["sw_vim_vm"][0]))
        self.assertEqual((vm["name"], vm["name_key"]), ("pw-app01", "pw-app01"))
        host = dict(zip(sd.COLS["sw_vim_host"], rows["sw_vim_host"][0]))
        self.assertEqual(host["name_key"], "app_keycloak")
        alert = dict(zip(sd.COLS["sw_alert_active"], rows["sw_alert_active"][0]))
        self.assertEqual(alert["name"], "SNMP Community Mismatch")
        self.assertNotIn("Hunter2x", alert["message"])                                                # free text: still masked

    def test_alerts_and_components_read_by_id(self):
        # review d: keyset paging (WHERE id > @last ORDER BY id) - never row numbers that shift while rows move
        q = [l for l in self.fake.queries() if " Orion.AlertActive " in l or " Orion.APM.Component " in l]
        self.assertTrue(q)
        self.assertFalse(any("WITH ROWS" in l for l in q), q)

    def test_a_property_named_auto_or_location(self):
        # review b: collect() takes Location and Auto for site_by's words - neither path reads such a property
        with open(self.more) as fh:
            fx = json.load(fh)
        fx["properties"] = fx["properties"] + ["Auto"]
        for n in fx["nodes"]:
            n["cp"]["Auto"] = "x"
        odd = os.path.join(TMP, "fx-auto.json")
        with open(odd, "w") as fh:
            json.dump(fx, fh)
        f = Fake(odd)
        try:
            snap = sd.read_swis(f.client(), {"parts": []})
            live = sw.collect(f.client(), {})
        finally:
            f.stop()
        self.assertNotIn("Auto", snap["props"])
        self.assertNotIn("Auto", live["custom_properties"])
        a, b = same(live, back(snap, {}))
        self.assertEqual(b, a)

    def test_a_row_read_twice_is_written_once(self):
        # 1.96.1 (review P2-7): a component paged twice while it moved - one row, never a PK error that loses the snapshot
        snap = dict(self.snap, extra=dict(self.snap["extra"]))
        comp = snap["extra"]["component"]
        snap["extra"]["component"] = comp + [dict(comp[0], name="seen again")]
        snap["extra"]["event"] = snap["extra"]["event"] + snap["extra"]["event"][:3]
        rows = sd.to_rows(snap, 7)
        self.assertEqual(len(rows["sw_component"]), len(comp))
        self.assertEqual(len(rows["sw_event"]), len(self.snap["extra"]["event"]))
        self.assertEqual(set(r[0] for r in rows["sw_event"]), {sd.orion_key(self.snap["raw"]["url"])})   # each event: its Orion

    def test_the_reader_gives_back_what_collect_gives(self):
        for o in ({}, {"site_by": "Site", "lat_property": "Lat", "lon_property": "Lon", "properties": ["Region"]},
                  {"properties": ["Nope", "bad name"], "max_nodes": 100}, {"properties": ["Region", "Site"], "page": 200}):
            live = sw.collect(self.fake.client(), o)
            db = back(self.snap, o)
            self.assertEqual((db["source"], db["age_seconds"], db["snapshot_at"]), ("db-snapshot", 120, "2026-11-21T09:00:00Z"))
            a, b = same(live, db)
            self.assertEqual(a, b, o)
        db = back(self.snap, {"properties": ["Nope"]})
        self.assertIn("not among the node custom properties in the snapshot", db["not_read"][0]["why"])

    def test_and_the_network_map_draws_the_same(self):
        o = {"site_by": "Location", "lat_property": "Lat", "lon_property": "Lon", "properties": ["Region"]}
        a = sw.model(sw.collect(self.fake.client(), o), dict(o, warn_util=90))
        b = sw.model(back(self.snap, o), dict(o, warn_util=90))
        a.pop("read_at")
        b.pop("read_at")
        self.assertEqual(a, b)


class Parts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plain, cls.more = fixtures()

    def test_a_module_this_orion_lacks(self):
        f = Fake(self.more, {"FAKE_SWIS_HIDE": "Orion.APM.Application,Orion.APM.Component,Orion.VIM.Hosts,Orion.VIM.VirtualMachines,Orion.VIM.Clusters,Orion.VIM.DataCenters,Orion.VIM.VCenters"})
        try:
            s = sd.read_swis(f.client())
        finally:
            f.stop()
        self.assertEqual(sorted(s["extra"]), ["alert", "event", "iface", "ip", "node", "volume"])
        self.assertEqual([(x["what"], x.get("absent")) for x in s["not_read"]],
                         [(sd.PART_WORDS["app"], True), (sd.PART_WORDS["component"], True), (sd.PART_WORDS["vim"], True)])
        c = sd.counts(sd.to_rows(s, 1), s)
        self.assertEqual((c["apps"], c["vim_hosts"], c["volumes"]), (None, None, 240))
        lines = sf.swis_snapshot_lines({"ok": True, "snap_id": 4, "counts": c, "not_read": s["not_read"], "seconds": 1.5, "queries": 20})
        self.assertIn("Not on this Orion (a module it does not have):", lines)
        self.assertNotIn("Not read:", lines)
        self.assertFalse(any("SAM application" in l for l in lines))

    def vim_read(self, change):
        """read_swis() against the fixture, with change(swql, rows, cut) -> (rows, cut) on each paged query (or raising)."""
        f = Fake(self.more)
        try:
            c = f.client()
            orig = c.paged

            def paged(swql, *args, **kw):
                rows, cut = orig(swql, *args, **kw)
                return change(swql, rows, cut)
            c.paged = paged
            return sd.read_swis(c)
        finally:
            f.stop()

    def test_vims_hosts_without_its_vms_are_not_kept(self):
        # review 1113 P1-1: the VMs timed out after the hosts were read - neither is kept, VIM is said not read
        def change(swql, rows, cut):
            if "Orion.VIM.VirtualMachines" in swql:
                raise sd.site_swis.SwisError("timed out after 30 s", "timeout")
            return rows, cut
        s = self.vim_read(change)
        self.assertNotIn("vim_host", s["extra"])
        self.assertNotIn("vim_vm", s["extra"])
        self.assertEqual([(x["what"], x.get("absent")) for x in s["not_read"]], [(sd.PART_WORDS["vim"], None)])
        c = sd.counts(sd.to_rows(s, 1), s)
        self.assertEqual((c["vim_hosts"], c["vim_vms"]), (None, None))

    def test_two_vcenters_of_one_name(self):
        # review 1113 P1-4: VCenterIDs 1 and 2 both vc01.example.mil - their hosts get no vCenter, never one for both
        def change(swql, rows, cut):
            if "Orion.VIM.VCenters" in swql:
                return rows + [dict(rows[0], VCenterID=2, Name="VC01.example.mil.", IPAddress="192.0.2.251")], cut
            return rows, cut
        s = self.vim_read(change)
        self.assertEqual(set(h["vcenter"] for h in s["extra"]["vim_host"]), {None})
        self.assertEqual([x["what"] for x in s["not_read"]], ["the VIM vCenters' names"])
        self.assertIn("vc01.example.mil: more than one vCenter has this name", s["not_read"][0]["why"])

    def test_the_newest_snapshot_says_whether_vim_was_read(self):
        # review 1113 P1-2: VIM's counts come back with the snapshot (NULL: not asked or failed)
        self.assertIn("vim_hosts, vim_vms FROM sw_snapshot", "\n".join(x[0] for x in self.latest_sql()))

    def latest_sql(self):
        conn = FakeConn(lambda sql, args: ([("x",)], []))
        sd.latest(conn)
        return conn.sql

    def test_an_orion_without_metadata_property(self):
        f = Fake(self.plain, {"FAKE_SWIS_HIDE": "Metadata.Property"})     # an Orion that refuses Metadata.Property
        try:
            s = sd.read_swis(f.client())
        finally:
            f.stop()
        self.assertTrue(s["raw"]["reachable"])
        self.assertEqual((s["extra"], s["props"]), ({}, []))
        self.assertEqual(len(s["not_read"]), 1)
        self.assertIn("Metadata.Property", s["not_read"][0]["what"])
        # 1.96.1 (review P1-3): never a whole snapshot - the sites and points would be lost; written with ok 0
        self.assertTrue(s["failed"].startswith("which columns this Orion has could not be read (Metadata.Property): "), s["failed"])

    def test_metadata_property_that_answers_nothing(self):
        # 1.96.2 (review P1-4): HTTP 200 with no rows - the same as not read, never ok 1 with every property "absent"
        f = Fake(self.more, {"FAKE_SWIS_EMPTY": "Metadata.Property"})
        try:
            s = sd.read_swis(f.client())
        finally:
            f.stop()
        self.assertTrue(s["raw"]["reachable"])
        self.assertEqual((s["extra"], s["props"]), ({}, []))
        self.assertEqual(s["failed"], "which columns this Orion has could not be read (Metadata.Property): it listed no columns of Orion.Nodes")

    def test_not_reached(self):
        c = sw.Client("https://127.0.0.1:%d" % free_port(), "svc_swis_ro", PW, timeout=2)
        s = sd.read_swis(c)
        self.assertFalse(s["raw"]["reachable"])
        self.assertEqual(s["extra"], {})
        self.assertIn("refused", s["raw"]["error"])

    def recording(self, f):
        """f's client, every SWQL it sends kept in .sent (1.96.2: the WHERE and paging are checked)."""
        c = f.client()
        c.sent, q = [], c.query

        def query(swql, params=None, *a, **k):
            c.sent.append((re.sub(r"\s+", " ", swql), dict(params or {})))
            return q(swql, params, *a, **k)
        c.query = query
        return c

    def test_the_events_from_the_newest_kept_on(self):
        f = Fake(self.more)
        try:
            ev = json.load(open(self.more))["tables"]["Orion.Events"]
            c = self.recording(f)
            s = sd.read_swis(c, {"parts": ["event"], "events_after": ev[-6]["EventID"], "events_after_at": ev[-6]["EventTime"],
                                 "events_orion": "o:17774", "event_hours": 48})
        finally:
            f.stop()
        # the newest kept on, and the last EVENT_OVERLAP_MINUTES before it again (an event committed late; the upsert keeps one)
        self.assertEqual([e["event_id"] for e in s["extra"]["event"]], [e["EventID"] for e in ev[-6:]])
        names = dict((t["EventType"], t["Name"]) for t in json.load(open(self.more))["tables"]["Orion.EventTypes"])
        self.assertEqual([e["type_name"] for e in s["extra"]["event"]][-5], names[ev[-5]["EventType"]])
        q = [x for x, _p in c.sent if " Orion.Events" in x]
        self.assertEqual(q, ["SELECT MAX(EventID) AS Newest FROM Orion.Events",
                             "SELECT TOP 1000 EventID, EventTime, NetworkNode, NetObjectType, NetObjectID, EventType, Message, "
                             "Acknowledged FROM Orion.Events WHERE (EventID > @after OR EventTime >= @since) ORDER BY EventID"])
        self.assertEqual((s["event_orion"], s["events_after"], s.get("events_reset")), ("o:17774", ev[-6]["EventID"], None))
        self.assertEqual(sd.counts(sd.to_rows(s, 1), s)["events"], 5)              # only the new ones count

    def test_events_paged_by_eventid_never_by_rows(self):
        # 1.96.2 (review P1-1): WITH ROWS lost the event at a page's edge when Orion purged one between two pages
        f = Fake(self.more)
        try:
            ev = json.load(open(self.more))["tables"]["Orion.Events"]
            c = self.recording(f)
            s = sd.read_swis(c, {"parts": ["event"], "events_after": ev[39]["EventID"], "events_after_at": ev[39]["EventTime"],
                                 "events_orion": "o:17774", "page": 50, "event_days": 400})
        finally:
            f.stop()
        q = [(x, p_) for x, p_ in c.sent if "FROM Orion.Events WHERE" in x]
        self.assertEqual(len(q), 5)                                                  # 201 events, 50 a page
        self.assertFalse(any("WITH ROWS" in x for x, _p in q))
        self.assertTrue(all(x.startswith("SELECT TOP 50 ") and x.endswith(" ORDER BY EventID") for x, _p in q))
        self.assertTrue(all("(EventID > @after OR EventTime >= @since) AND EventTime >= @floor AND EventID > @last" in x for x, _p in q[1:]))
        self.assertEqual([p_["last"] for _x, p_ in q[1:]], [ev[38 + 50 * k]["EventID"] for k in range(1, 5)])     # ev[39] itself again: its time
        self.assertEqual([e["event_id"] for e in s["extra"]["event"]], [e["EventID"] for e in ev[39:]])

    def test_a_busy_ten_minutes_is_read_whole(self):
        # 1.96.2 (review P1-2): 300 events committed late (lower EventIDs) in the 10 minutes before the newest kept - the
        # 200-EventID overlap lost 100 of them; by their time, all are read
        fx = json.load(open(self.more))
        ev = fx["tables"]["Orion.Events"]
        last = sd.stamp(ev[-1]["EventTime"])
        late = [dict(ev[-1], EventID=400000 + k, EventTime=sd.iso(last - datetime.timedelta(seconds=2 * k))) for k in range(300)]
        fx["tables"]["Orion.Events"] = ev + late
        path = os.path.join(TMP, "fx-busy.json")
        json.dump(fx, open(path, "w"))
        f = Fake(path)
        try:
            s = sd.read_swis(f.client(), {"parts": ["event"], "events_after": ev[-1]["EventID"], "events_after_at": ev[-1]["EventTime"],
                                          "events_orion": "o:17774", "event_days": 400})
        finally:
            f.stop()
        got = set(e["event_id"] for e in s["extra"]["event"])
        self.assertTrue(set(e["EventID"] for e in late) <= got, len(got))
        self.assertNotIn(ev[-2]["EventID"], got)                                     # 12 minutes before: kept already

    def test_eventids_that_started_again(self):
        # 1.96.2 (review P1-2): Orion rebuilt at the same address - its newest EventID below the newest kept: a new count,
        # read by time, kept under orion#2; the events kept before stay as they are
        f = Fake(self.more)
        try:
            ev = json.load(open(self.more))["tables"]["Orion.Events"]
            c = self.recording(f)
            s = sd.read_swis(c, {"parts": ["event"], "events_after": 900000, "events_after_at": ev[-1]["EventTime"],
                                 "events_orion": "o:17774", "event_days": 400})
        finally:
            f.stop()
        self.assertEqual(s["events_reset"], {"newest_there": ev[-1]["EventID"], "newest_kept": 900000})
        self.assertEqual((s["event_orion"], s["events_after"]), ("o:17774#2", 0))
        self.assertEqual([e["event_id"] for e in s["extra"]["event"]], [e["EventID"] for e in ev])
        self.assertIn("FROM Orion.Events WHERE EventTime >= @floor ORDER BY EventID", [x for x, _p in c.sent][-1])
        rows = sd.to_rows(dict(s, raw=dict(s["raw"], url="https://o")), 1)
        self.assertEqual(set(r[0] for r in rows["sw_event"]), {"o:17774#2"})
        self.assertEqual(sd.counts(rows, s)["events"], len(ev))
        self.assertEqual([sd.next_epoch(k) for k in ("o:17774", "o:17774#2", "o:17774#9")], ["o:17774#2", "o:17774#3", "o:17774#10"])

    def test_an_outage_longer_than_event_hours_loses_nothing(self):
        # 1.96.1 (review P1-1): the job (or Orion) away 40 hours, event_hours 1 - every event since the newest kept is read
        f = Fake(self.more)
        try:
            ev = json.load(open(self.more))["tables"]["Orion.Events"]
            s = sd.read_swis(f.client(), {"parts": ["event"], "events_after": ev[39]["EventID"], "event_hours": 1, "event_days": 400})
            q = [l for l in f.queries() if " Orion.Events " in l]
        finally:
            f.stop()
        got = set(e["event_id"] for e in s["extra"]["event"])
        self.assertTrue(set(e["EventID"] for e in ev[40:]) <= got)                 # 200 events over 40 hours, none lost
        self.assertEqual(s["not_read"], [])
        self.assertEqual(len(q), 2)                                                # the newest EventID there, then the events

    def test_a_gap_older_than_event_days_is_said(self):
        f = Fake(self.more)
        try:
            ev = json.load(open(self.more))["tables"]["Orion.Events"]
            three = sd.iso(sd.now_utc() - datetime.timedelta(days=3))
            s = sd.read_swis(f.client(), {"parts": ["event"], "events_after": ev[0]["EventID"], "events_after_at": three, "event_days": 1})
        finally:
            f.stop()
        self.assertEqual(len(s["not_read"]), 1)
        self.assertTrue(s["not_read"][0]["what"].startswith("the events between %s and " % three), s["not_read"])
        self.assertIn("older than solarwinds_db_event_days (1)", s["not_read"][0]["why"])
        day = sd.now_utc() - datetime.timedelta(days=1, minutes=1)
        self.assertTrue(all(sd.stamp(e["event_time"]) >= day for e in s["extra"]["event"]))

    def test_the_properties_named_and_skipped(self):
        f = Fake(self.more)
        try:
            s = sd.read_swis(f.client(), {"properties": ["Region", "Site", "Lat"], "skip": ["Lat"], "parts": []})
        finally:
            f.stop()
        self.assertEqual(s["props"], ["Region", "Site"])
        self.assertEqual(s["extra"], {})
        self.assertEqual(sorted(s["raw"]["nodes"][0]["props"]), ["Region", "Site"])


class FakeCur(object):
    def __init__(self, conn):
        self.conn, self.description, self.rows = conn, [], []

    def execute(self, sql, args=None):
        self.conn.sql.append((sql, args))
        if isinstance(self.conn.answer, Exception):
            raise self.conn.answer
        if sql.startswith(("COMMIT", "ROLLBACK")):
            self.conn.in_transaction = self.conn.chain and "NO CHAIN" not in sql
            return 0
        if sql.startswith("SET TRANSACTION") and self.conn.in_transaction:
            raise RuntimeError("cannot set isolation inside a transaction")
        if sql.startswith("START TRANSACTION"):
            self.conn.in_transaction = True
        if sql.startswith(("START TRANSACTION", "SET TRANSACTION")):
            self.description, self.rows = [], []
            return 0
        answer = self.conn.answer(sql, args)
        if isinstance(answer, sqlite3.Cursor):   # 1.118.7: exercise the writer's joins and deletes on stored rows
            self.lastrowid, self.description, self.rows = answer.lastrowid, answer.description, answer.fetchall()
            return answer.rowcount if self.description is None else len(self.rows)
        self.description, self.rows = answer
        return len(self.rows)

    def executemany(self, sql, rows):
        for row in rows:
            self.execute(sql, row)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class FakeConn(object):
    def __init__(self, answer):
        self.answer, self.sql = answer, []
        self.chain = self.in_transaction = False

    def cursor(self):
        return FakeCur(self)

    def rollback(self):
        self.cursor().execute("ROLLBACK")

    def commit(self):
        self.cursor().execute("COMMIT")


class LinkDayDB(FakeConn):
    """The fake with stored rows for write/prune/links_on. SQLite runs the same joins against the writer's columns."""
    def __init__(self):
        super().__init__(self.answer_sql)
        self.db = sqlite3.connect(":memory:")
        for sql in [sd.SCHEMA_VERSION_TABLE] + sd.CREATE:
            self.answer_sql(sql)
        self.db.execute("INSERT INTO schema_version VALUES ('solarwinds', ?, '2026-11-21')", (sd.SCHEMA_VERSION,))
        self.commit()

    def answer_sql(self, sql, args=None):
        if "FROM information_schema.columns" in sql:       # 1.113.0's column check: SQLite's own columns
            table, column = args
            return self.db.execute("SELECT COUNT(*) FROM pragma_table_info(?) WHERE name = ?", (table, column))
        if "FROM information_schema.tables" in sql:
            names = [r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
            if " LIKE " in sql:
                return [], [(n,) for n in names if n.startswith("sw_")]
            return [], [(n,) for n in names if "'%s'" % n in sql]
        if sql.startswith("CREATE TABLE"):
            sql = sql.split(" ENGINE=")[0].replace("BIGINT NOT NULL AUTO_INCREMENT", "INTEGER")
            sql = re.sub(r", KEY \w+ \([^)]*\)", "", sql)
        return self.db.execute(sql.replace("%s", "?"), args or ())

    def commit(self):
        self.db.commit()

    def rollback(self):
        self.db.rollback()


class DailyTopology(unittest.TestCase):
    """1.118.7: the named links survive pruning of the snapshot from which they came."""
    def setUp(self):
        self.conn = LinkDayDB()
        self.addCleanup(self.conn.db.close)

    def write(self, at, name="Router A", links=True, failed="", host="orion.example.mil", **raw_more):
        raw = {"reachable": True, "url": host, "read_at": at, "topology_ok": True, "cut": False, "not_read": [],
               "nodes": [{"id": 1, "name": name, "dns": "r1.example.mil"}, {"id": 2, "name": "Switch B"}],
               "interfaces": [{"InterfaceID": 10, "Name": "port-a"}, {"InterfaceID": 20, "Caption": "port-b"}],
               "connections": [[1, 10, 2, 20], [2, 20, 3, None]] if links else []}
        raw.update(raw_more)
        return sd.write(self.conn, {"raw": raw, "failed": failed}, now=datetime.datetime(2026, 11, 25))

    def test_a_read_that_is_not_whole_leaves_the_day_open(self):
        """1.122.2: links cut, nodes cut, or the interfaces' read failed: the day waits for a whole read (never locked in)."""
        at = "2026-11-21T00:05:00Z"
        failed_ifaces = [{"what": "the links' interfaces (Orion.NPM.Interfaces)", "why": "timed out"}]
        for more, why in (({"topology_ok": False}, "the links were not all read"), ({"cut": True}, "nodes were left out"),
                          ({"not_read": failed_ifaces, "interfaces": []}, "the links' interfaces were not read")):
            r = self.write(at, **more)
            self.assertTrue(r["ok"])
            self.assertIn("topology: not kept - this read was not whole (" + why, r["topology"])
            self.assertEqual(sd.links_on(self.conn, "2026-11-21"), [])
        no_npm = [{"what": "the links' interfaces", "why": "this Orion has no Orion.NPM.Interfaces (NPM): links without speed or traffic"}]
        whole = self.write("2026-11-21T00:15:00Z", not_read=no_npm)                                   # no NPM: whole as it can be
        self.assertEqual(whole["topology"], "topology: copied for 2026-11-21")
        self.assertEqual([r["snap_id"] for r in sd.links_on(self.conn, "2026-11-21")], [whole["snap_id"]] * 2)

    def test_first_whole_snapshot_with_links_per_utc_day_and_orion(self):
        first = self.write("2026-11-22T00:59:00+01:00")
        second = self.write("2026-11-21T23:59:30Z", "Renamed")
        other = self.write("2026-11-21T23:59:30Z", "Other Orion", host="other.example.mil")
        tomorrow = self.write("2026-11-22T00:00:00Z", "Renamed")
        self.assertEqual(sf.swis_snapshot_lines(first)[0], "Snapshot 1 kept. topology: copied for 2026-11-21")
        self.assertEqual(sf.swis_snapshot_lines(second)[0], "Snapshot 2 kept. topology: 2026-11-21 already kept")
        sd.prune(self.conn, keep_snapshots=1, keep_days=1, now=datetime.datetime(2026, 11, 25))
        live = self.conn.db.execute("SELECT DISTINCT snap_id FROM sw_link").fetchall()
        self.assertEqual(live, [(tomorrow["snap_id"],)])
        rows = sd.links_on(self.conn, "2026-11-21", "orion.example.mil:17774")
        self.assertEqual([r["seq"] for r in rows], [0, 1])
        self.assertEqual(rows[0], {"day": "2026-11-21", "orion": "orion.example.mil:17774",
                                   "seq": 0, "snap_id": first["snap_id"],
                                   "src_node": 1, "src_if": 10, "dst_node": 2, "dst_if": 20,
                                   "src_name": "Router A", "src_name_key": "r1", "src_if_name": "port-a",
                                   "dst_name": "Switch B", "dst_name_key": "switch b", "dst_if_name": "port-b"})
        self.assertEqual((rows[1]["dst_node"], rows[1]["dst_name"], rows[1]["dst_if_name"]), (3, None, None))
        all_rows = sd.links_on(self.conn, "2026-11-21")
        self.assertEqual([r["snap_id"] for r in all_rows], [first["snap_id"]] * 2 + [other["snap_id"]] * 2)
        self.assertIn("LIMIT 50000", self.conn.sql[-1][0])
        self.assertEqual(sd.links_on(self.conn, "2026-11-22")[0]["src_name"], "Renamed")
        self.assertEqual(sd.links_on(self.conn, "2026-11-23"), [])

    def test_failed_and_empty_snapshots_leave_the_day_open(self):
        at = "2026-11-21T09:00:00Z"
        self.assertFalse(self.write(at, failed="read failed")["ok"])
        self.assertEqual(sd.links_on(self.conn, "2026-11-21"), [])
        self.assertEqual(self.write(at, links=False)["topology"], "topology: no links for 2026-11-21")
        self.assertEqual(sd.links_on(self.conn, "2026-11-21"), [])
        whole = self.write(at)
        self.assertEqual([r["snap_id"] for r in sd.links_on(self.conn, "2026-11-21")], [whole["snap_id"]] * 2)

    def test_copy_and_snapshot_roll_back_together(self):
        self.conn.db.execute("CREATE TRIGGER reject_link BEFORE INSERT ON sw_link_day WHEN NEW.seq = 1 "
                             "BEGIN SELECT RAISE(ABORT, 'copy interrupted'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "copy interrupted"):
            self.write("2026-11-21T09:00:00Z")
        self.conn.rollback()    # the job's write error path
        for table in ("sw_snapshot", "sw_link", "sw_link_day"):
            self.assertEqual(self.conn.db.execute("SELECT COUNT(*) FROM " + table).fetchone(), (0,))

    def test_daily_retention(self):
        for day in (20, 21, 22):
            self.write("2026-11-%dT09:00:00Z" % day)
        now = datetime.datetime(2026, 11, 23, 12)
        sd.prune(self.conn, now=now, link_days=0)
        self.assertEqual(len(sd.links_on(self.conn, "2026-11-20")), 2)
        pr = sd.prune(self.conn, now=now, link_days=2)
        self.assertEqual(pr["sw_link_day"], 2)
        self.assertEqual(sd.links_on(self.conn, "2026-11-20"), [])
        self.assertEqual(len(sd.links_on(self.conn, "2026-11-21")), 2)   # the cutoff day is kept whole
        self.assertEqual(len(sd.links_on(self.conn, "2026-11-22")), 2)

    def test_by_hand_database_without_daily_table(self):
        self.conn.db.execute("DROP TABLE sw_link_day")
        self.assertEqual(sd.schema(self.conn, False), (True, False))
        run = self.write("2026-11-21T09:00:00Z")
        self.assertTrue(run["ok"])
        self.assertEqual(self.conn.db.execute("SELECT ok, links FROM sw_snapshot").fetchall(), [(1, 2)])
        self.assertEqual(self.conn.db.execute("SELECT COUNT(*) FROM sw_link").fetchone(), (2,))
        run["pruned"] = sd.prune(self.conn, now=datetime.datetime(2026, 11, 23))
        note = "no sw_link_day table: the daily topology copy is off - make it (docs/REPORT_DATABASE.md)"
        self.assertEqual("\n".join(sf.swis_snapshot_lines(run)).count(note), 1)
        self.assertEqual(sd.links_on(self.conn, "2026-11-21"), [])
        self.assertEqual(sd.schema(self.conn, True), (True, True))
        self.assertEqual(self.write("2026-11-21T10:00:00Z")["topology"], "topology: copied for 2026-11-21")
        self.conn.db.execute("DROP TABLE sw_link")
        self.assertEqual(sd.schema(self.conn, False), (False, False))


class Answer(unittest.TestCase):
    NOW = datetime.datetime(2026, 11, 21, 9, 0, 0)

    def snaps(self, age_min, failed=None, core=None, links=0, missing_links=0):
        core = core or {}
        def ans(sql, args):
            if "FROM sw_link" in sql:
                return [(k,) for k in ("src_node", "src_if", "dst_node", "dst_if")], [(1, 2, 3, 4)] * (links - missing_links)
            if "WHERE ok = 0" in sql:
                return [("read_at",), ("error",)], ([(self.NOW, failed)] if failed else [])
            if "FROM sw_snapshot WHERE ok = 1" in sql:
                if age_min is None:
                    return [], []
                return [("snap_id",)] * 10, [(5, self.NOW - datetime.timedelta(minutes=age_min), json.dumps(dict({"props": ["Site"]}, **core)), "[]",
                                              0, 0, 0, "orion.example.mil:17774", age_min * 60 + 7, links)]
            return [("x",)], []
        return FakeConn(ans)

    def test_no_snapshot_yet(self):
        why = []
        self.assertIsNone(sd.answer({}, conn=self.snaps(None), why=why, now=self.NOW))
        self.assertEqual(why, ["the report database has no SolarWinds snapshot yet (the job SolarWinds - snapshot)"])

    def test_too_old_and_why(self):
        why = []
        self.assertIsNone(sd.answer({}, conn=self.snaps(45, "no answer in time"), max_age=1200, why=why, now=self.NOW))
        self.assertEqual(why, ["the newest SolarWinds snapshot is 45 min old (solarwinds_db_max_age: 20 min); the last snapshot run failed: no answer in time"])

    def test_fresh_from_the_database(self):
        r = sd.answer({}, conn=self.snaps(4), max_age=1200, now=self.NOW)
        self.assertEqual((r["source"], r["age_seconds"], r["snapshot_at"], r["nodes"]), ("db-snapshot", 240, "2026-11-21T08:56:00Z", []))
        self.assertEqual(sd.source_text(r, "2026.1"), "SolarWinds 2026.1 (the report database's snapshot of 08:56 UTC, 4 min old)")

    def test_another_orion_is_not_served(self):
        # 1.96.1 (review P1-2): the snapshot of orion.example.mil is never another SolarWinds' map
        self.assertEqual(sd.answer({}, conn=self.snaps(4), now=self.NOW, orion="https://ORION.example.mil/")["source"], "db-snapshot")
        why = []
        self.assertIsNone(sd.answer({}, conn=self.snaps(4), now=self.NOW, orion="https://orion2.example.mil:17774", why=why))
        self.assertEqual(why, ["the newest SolarWinds snapshot is of orion.example.mil:17774, not orion2.example.mil:17774 (the address "
                               "of this template's credential or netmap_solarwinds_url)"])
        self.assertEqual(sd.answer({}, conn=self.snaps(4), now=self.NOW, orion="")["source"], "db-snapshot")     # no address: any

    def test_a_snapshot_without_the_site_property_is_not_served(self):
        # 1.96.1 (review P1-3): every node would be "Not placed"
        why = []
        self.assertIsNone(sd.answer({"site_by": "Region", "lat_property": "Lat"}, conn=self.snaps(4), now=self.NOW, why=why))
        self.assertEqual(why, ["the newest SolarWinds snapshot has no node custom property Region, Lat (solarwinds_snapshot_properties or "
                               "solarwinds_snapshot_skip_properties)"])
        for o in ({"site_by": "Site"}, {"site_by": "auto"}, {"site_by": "Location"}, {}):
            self.assertEqual(sd.answer(o, conn=self.snaps(4), now=self.NOW)["source"], "db-snapshot", o)

    def test_max_age_zero_cut_and_too_many_links(self):
        # review a: 0 never serves the snapshot (it served any age); b: a cut snapshot, or more links than collect() reads
        for kw, max_age, o, want in (({}, 0, {}, "solarwinds_db_max_age is 0: SolarWinds itself every time"),
                                     ({"core": {"cut": True}}, 1200, {}, "the newest SolarWinds snapshot was cut at solarwinds_snapshot_max_nodes"),
                                     ({"links": 2001}, 1200, {"max_nodes": 100},
                                      "the newest SolarWinds snapshot has more links (2001) than netmap_max_nodes x 20 (2000)")):
            why = []
            self.assertIsNone(sd.answer(o, conn=self.snaps(4, **kw), max_age=max_age, now=self.NOW, why=why), kw)
            self.assertEqual(why, [want])
        self.assertEqual(sd.answer({}, conn=self.snaps(400), max_age=None, now=self.NOW)["source"], "db-snapshot")   # None: any age
        self.assertEqual(sd.answer({"max_nodes": 100}, conn=self.snaps(4, links=2000), now=self.NOW)["source"], "db-snapshot")

    def test_incomplete_links_are_not_served(self):
        why = []
        self.assertIsNone(sd.answer(conn=self.snaps(4, links=3, missing_links=1), why=why, now=self.NOW))
        self.assertIn("snapshot 5 is incomplete: read 2 of 3 links", why[0])

    def test_apps_for_keeps_the_callers_transaction(self):
        conn = self.snaps(4)
        self.assertEqual(sd.apps_for(conn, 5, [11], fresh=False), {11: []})
        self.assertTrue(conn.sql)
        self.assertTrue(all(sql.startswith("SELECT") for sql, _ in conn.sql))

    def test_tls_error_names_host_without_password(self):
        host = "db.example.test"
        p = {"host": host, "tls": True, "password": PW}
        for error in (ssl.SSLError("certificate failure " + PW), RuntimeError(2026, "SSL error " + PW),
                      RuntimeError("server does not support TLS"),
                      RuntimeError("the session is not encrypted - the server must offer TLS")):
            message = report_db.explain(error, p)
            self.assertIn(host, message)
            self.assertNotIn(PW, message)
        p["password"] = r"test\placeholder"
        p["host"] = "user:" + p["password"] + "@" + host
        detail = "certificate failure for " + p["host"] + " with password " + p["password"]
        message = report_db.explain(ssl.SSLError(detail), p)
        self.assertIn("certificate failure for " + host + " with password [redacted]", message)
        self.assertNotIn("user:", message)
        self.assertNotIn("placeholder", message)

    def test_no_tls_report_reason_hides_host(self):
        p = {"host": "db.example.test", "tls": True}
        error = RuntimeError("the session is not encrypted - the server must offer TLS")
        diagnostic = report_db.explain(error, p)
        self.assertIn(p["host"], diagnostic)
        reason = sd.short_error(diagnostic)
        self.assertNotIn(p["host"], reason)
        self.assertIn("does not offer TLS: clear Use TLS", reason)

    def test_one_read_only_transaction(self):
        # review c: latest() and the rows in one read-only transaction; nodes deleted meanwhile are a reason, never half a map
        conn = self.snaps(4)
        conn.chain = conn.in_transaction = True       # completion_type=CHAIN on an already active session
        self.assertEqual(sd.answer({}, conn=conn, now=self.NOW)["source"], "db-snapshot")
        self.assertFalse(conn.in_transaction)
        q = [x for x, _a in conn.sql]
        self.assertEqual(q[:3], ["ROLLBACK AND NO CHAIN", "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ",
                                 "START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY"])
        self.assertEqual(q[-1], "COMMIT AND NO CHAIN")
        def gone(sql, args):
            if "FROM sw_snapshot WHERE ok = 1" in sql:
                return [("x",)] * 10, [(5, self.NOW, json.dumps({"props": []}), "[]", 900, 0, 0, "o:17774", 60, 0)]
            return [("x",)], []
        why = []
        self.assertIsNone(sd.answer({}, conn=FakeConn(gone), why=why, now=self.NOW))
        self.assertEqual(why, ["the report database's SolarWinds snapshot could not be read: snapshot 5 has 0 of its 900 nodes "
                               "(deleted while it was read)"])

    def test_the_age_on_the_databases_clock(self):
        # review P2-6: no now - the database's own TIMESTAMPDIFF, never this host's clock against the database's
        conn = self.snaps(4)
        s = sd.latest(conn)
        self.assertEqual((s["age_seconds"], s["orion"]), (247, "orion.example.mil:17774"))
        self.assertIn("TIMESTAMPDIFF(SECOND, read_at, UTC_TIMESTAMP())", conn.sql[-1][0])
        self.assertEqual(sd.latest(self.snaps(4), now=self.NOW)["age_seconds"], 240)

    def test_a_database_that_fails_is_a_reason(self):
        why = []
        self.assertIsNone(sd.answer({}, conn=FakeConn(RuntimeError("SELECT command denied")), why=why))
        self.assertEqual(why, ["the report database's SolarWinds snapshot could not be read: SELECT command denied"])
        why = []
        sd.answer({}, conn=FakeConn(RuntimeError("(1142, \"SELECT command denied to user 'report_reader'@'192.0.2.9' for table "
                                                 "'sw_snapshot'\")")), why=why)
        self.assertNotIn("report_reader", why[0])                                 # review P2-12: no account or host
        self.assertIn("denied to user the account for table", why[0])

    def test_live(self):
        plain, more = fixtures()
        f = Fake(more)
        try:
            r = sd.answer({"properties": ["Region"]}, conn=None, client=f.client)
        finally:
            f.stop()
        self.assertEqual((r["source"], r["age_seconds"], r["snapshot_at"]), ("swis-live", 0, r["read_at"]))
        self.assertEqual(sd.source_text(r, "2026.1"), "SolarWinds 2026.1 (SWIS, read-only)")

    def test_the_other_reports_readers(self):
        def ans(sql, args):
            if "FROM sw_node" in sql:
                cols = ["node_id", "name", "name_key", "dns", "ip", "status_code", "status_text", "unmanaged", "last_poll", "cpu_pct", "mem_pct"]
                return [(c,) for c in cols], [(11, "esx07", "esx07", "esx07.example.mil", "192.0.2.7", 2, "Node status is Down.", 0, None, None, None),
                                              (12, "db01", "db01", "db01.example.mil", "192.0.2.8", 1, "Up", 0, None, 4.0, 30.0)]
            if "FROM sw_alert_active" in sql:
                cols = ["node_id", "name", "severity", "triggered_at", "message", "entity_type", "entity_caption", "acknowledged"]
                return [(c,) for c in cols], [(11, "Node is down", 2, self.NOW, "esx07 is down", "Orion.Nodes", "esx07", 0)]
            return [("x",)], []
        conn = FakeConn(ans)
        got = sd.nodes_for(conn, 5, ["ESX07.example.mil", "192.0.2.8", "web99", ""])
        self.assertEqual((got["ESX07.example.mil"]["status"], got["192.0.2.8"]["name"], got["web99"], got[""]), ("Down", "db01", None, None))
        sql, args = conn.sql[-1]
        self.assertEqual(args[0], 5)
        self.assertIn("name_key IN (%s, %s, %s)", sql)
        al = sd.alerts_for(conn, 5, [11, 12])
        self.assertEqual(([a["name"] for a in al[11]], al[12]), (["Node is down"], []))
        self.assertEqual(sd.events_between(conn, self.NOW, self.NOW, []), [])

    def test_nodes_for_all(self):
        cols = ["node_id", "name", "name_key", "dns", "ip", "ips", "status_code", "status_text", "unmanaged", "last_poll", "cpu_pct", "mem_pct"]
        rows = [(21, "dc01", "dc01", "dc01.a.example.mil", "192.0.2.21", '["192.0.2.21", "192.0.2.50"]', 1, "Up", 0, None, None, None),
                (22, "dc01", "dc01", "dc01.b.example.mil", "192.0.2.22", '["192.0.2.22"]', 2, "Down", 0, None, None, None),
                (23, "sql01", "sql01", "sql01.a.example.mil", "192.0.2.50", None, 1, "Up", 0, None, None, None)]
        conn = FakeConn(lambda sql, args: ([(c,) for c in cols], rows))
        got = sd.nodes_for_all(conn, 5, ["DC01.a.example.mil", "192.0.2.50", "web99", ""])
        self.assertEqual([(r["node_id"], r["dns"], r["status"], r["matched_by"]) for r in got["DC01.a.example.mil"]],
                         [(21, "dc01.a.example.mil", "Up", "name"), (22, "dc01.b.example.mil", "Down", "name")])     # two domains
        self.assertEqual([(r["node_id"], r["matched_by"]) for r in got["192.0.2.50"]], [(21, "other ip"), (23, "ip")])   # a shared address
        self.assertEqual((got["web99"], got[""]), ([], []))
        self.assertNotIn("ips", got["192.0.2.50"][0])
        sql, args = conn.sql[-1]
        self.assertIn("OR ips LIKE %s", sql)
        self.assertEqual(args[0], 5)
        self.assertEqual(args[-1], '%"192.0.2.50"%')
        self.assertEqual(sd.nodes_for_all(conn, 5, []), {})

    def test_apps_for(self):
        def ans(sql, args):
            if "FROM sw_app" in sql:
                cols = ["app_id", "node_id", "name", "status", "status_text", "unmanaged", "components", "components_not_up"]
                return [(c,) for c in cols], [(309, 11, "Web server", 2, "Down", 0, 9, 2), (310, 11, "Database", 1, "Up", 0, 3, 0)]
            if "FROM sw_component" in sql:
                cols = ["component_id", "app_id", "name", "kind", "status", "status_text", "value", "message"]
                return [(c,) for c in cols], [(3094, 309, "HTTPS monitor", "8", 3, "Warning", 812.0, "Slow"),
                                              (3090, 309, "Port 443", "3", 2, "Down", None, "Port 443 refused the connection")]
            return [("x",)], []
        conn = FakeConn(ans)
        got = sd.apps_for(conn, 5, [11, 12, None])
        self.assertEqual(sorted(got), [11, 12])
        self.assertEqual(got[12], [])
        self.assertEqual([a["app"] for a in got[11]], ["Database", "Web server"])          # by name, as ORDER BY name gives
        web = got[11][1]
        self.assertEqual((web["app"], web["status"], web["components"], len(web["components_not_up"])), ("Web server", "Down", 9, 2))
        self.assertEqual([(c["name"], c["status"], c["value"], c["message"]) for c in web["components_not_up"]],
                         [("Port 443", "Down", None, "Port 443 refused the connection"), ("HTTPS monitor", "Warning", 812, "Slow")])
        self.assertEqual((got[11][0]["status"], got[11][0]["components_not_up"]), ("Up", []))
        self.assertEqual([a for q, a in conn.sql if q.startswith("SELECT")][-1][:2], [5, 309])
        self.assertEqual([q for q, _a in conn.sql][-1], "COMMIT AND NO CHAIN")                  # review c: in one read-only transaction
        self.assertEqual(sd.apps_for(conn, 5, []), {})
        note = {"what": sd.PART_WORDS["app"], "why": "not on this Orion (no Orion.APM.Application)", "absent": True}
        self.assertEqual(sd.part_note({"not_read": [note]}, "app"), note)
        self.assertIsNone(sd.part_note({"not_read": []}, "app"))

    def test_orion_key_stamp_and_events_of_one_orion(self):
        self.assertEqual([sd.orion_key(u) for u in ("https://Orion.example.mil", "https://orion.example.mil:17774/SolarWinds",
                                                   "orion.example.mil:17778", "https://192.0.2.5", "")],
                         ["orion.example.mil:17774", "orion.example.mil:17774", "orion.example.mil:17778", "192.0.2.5:17774", ""])
        # review P2-4: an offset is turned into UTC, never dropped
        self.assertEqual(sd.stamp("2026-11-21T10:00:05.1230000+01:00"), datetime.datetime(2026, 11, 21, 9, 0, 5))
        self.assertEqual(sd.stamp("2026-11-21T09:00:05-0130"), datetime.datetime(2026, 11, 21, 10, 30, 5))
        self.assertEqual(sd.stamp("2026-11-21T09:00:05Z"), datetime.datetime(2026, 11, 21, 9, 0, 5))
        self.assertEqual(sd.stamp(datetime.datetime(2026, 11, 21, 10, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=1)))),
                         datetime.datetime(2026, 11, 21, 9, 0))
        conn = FakeConn(lambda sql, args: ([("x",)], [("orion_1.example.mil:17774",)] if "FROM sw_snapshot" in sql else []))
        sd.events_between(conn, self.NOW, self.NOW)
        self.assertEqual(conn.sql[-2][0], "SELECT orion FROM sw_snapshot WHERE ok = 1 ORDER BY snap_id DESC LIMIT 1")
        # 1.96.2: its events from before its EventIDs started again too (orion#N), its own _ taken literally
        self.assertIn("AND (orion = %s OR orion LIKE %s)", conn.sql[-1][0])
        self.assertEqual(conn.sql[-1][1][2:], ["orion_1.example.mil:17774", "orion\\_1.example.mil:17774#%"])
        sd.events_between(conn, self.NOW, self.NOW, orion="orion.example.mil:17774")
        self.assertEqual(conn.sql[-1][1][2:], ["orion.example.mil:17774", "orion.example.mil:17774#%"])

    def test_the_event_cursor_of_the_newest_count(self):
        def ans(sql, args):
            if sql.startswith("SELECT DISTINCT orion"):
                return [("orion",)], [("o:17774#10",), ("o:17774",), ("o:17774#2",)]
            return [("event_id",), ("event_time",)], [(77, self.NOW)] if args == ("o:17774#10",) else []
        self.assertEqual(sd.event_cursor(FakeConn(ans), "o:17774"), ("o:17774#10", 77, self.NOW))
        self.assertEqual(sd.newest_event(FakeConn(ans), "o:17774"), (77, self.NOW))
        none = FakeConn(lambda sql, args: ([("x",)], []))
        self.assertEqual(sd.event_cursor(none, "o:17774"), ("o:17774", 0, None))

    def test_a_newer_snapshot_written_meanwhile(self):
        # 1.96.2 (review P1-3): a run that lost its lock never writes its older read as the newest
        conn = FakeConn(lambda sql, args: ([("m",)], [(9,)] if args == (7,) else [(None,)]))
        self.assertEqual(sd.newer_snapshot(conn, 7), 9)
        self.assertEqual(conn.sql[0], ("ROLLBACK", None))                           # a fresh look, not the read's old view
        self.assertIn("WHERE ok = 1 AND snap_id > %s", conn.sql[-1][0])
        self.assertEqual(sd.newer_snapshot(conn, 9), 0)

    def test_an_address_with_a_login_is_never_repeated(self):
        # 1.96.2 (review P1-6): "user:pa" of https://user:pa/ss@... landed in the reason
        for u in ("https://user:pa/ss@orion2.example.mil", "https://user:pass@orion2.example.mil:17774", "user@orion2.example.mil",
                  "https://orion2.example.mil:17774/a@b", "https://or ion.example.mil", "https://orion.example.mil:x"):
            self.assertEqual(sd.orion_key(u), "", u)
        self.assertEqual(sd.orion_key("https://[2001:DB8::1]"), "[2001:db8::1]:17774")
        why = []
        sd.answer({}, conn=self.snaps(4), now=self.NOW, orion="https://user:pa/ss@orion2.example.mil", why=why)
        self.assertFalse(any("user" in w or "pa" in w for w in why), why)

    def test_short_error_names_no_host(self):
        for e in ("(2003, \"Can't connect to MySQL server on 'db01.example.mil' ([Errno 111] Connection refused)\")",
                  "(2005, \"Unknown MySQL server host 'db01.example.mil' (-2)\")"):
            self.assertNotIn("db01", sd.short_error(RuntimeError(e)))
            self.assertIn("the database's host", sd.short_error(RuntimeError(e)))

    def test_an_ipv6_address_matched_in_any_case(self):
        # 1.96.2 (review P2-9): the column is utf8mb4_bin - the other addresses kept lower case, the address compared so
        snap = {"raw": {"url": "https://o", "nodes": [{"id": 5, "name": "fw01", "ip": "2001:DB8::A"}]},
                "extra": {"ip": [{"node_id": 5, "ip": "2001:DB8::B"}]}}
        row = sd.to_rows(snap, 1)["sw_node"][0]
        self.assertEqual(json.loads(row[sd.COLS["sw_node"].index("ips")]), ["2001:db8::b"])
        conn = FakeConn(lambda sql, args: ([("x",)], []))
        sd.nodes_for(conn, 1, ["2001:DB8::A"])
        sd.nodes_for_all(conn, 1, ["2001:DB8::A"])
        self.assertTrue(all("LOWER(ip) IN (%s)" in s for s, _a in conn.sql))
        self.assertEqual(conn.sql[-1][1][1:], ["2001:db8::a", "2001:db8::a", '%"2001:db8::a"%'])

    def test_max_age_zero_asks_the_database_nothing(self):
        # 1.96.2 (review P2-10)
        conn, why = FakeConn(RuntimeError("never asked")), []
        self.assertIsNone(sd.answer({}, conn=conn, max_age=0, why=why, now=self.NOW))
        self.assertEqual((why, conn.sql), (["solarwinds_db_max_age is 0: SolarWinds itself every time"], []))

    def test_a_snapshot_without_a_hierarchy_property_is_not_served(self):
        # 1.96.2 (review P1-5): netmap_hierarchy's properties too - else every node "Not placed"
        why = []
        self.assertIsNone(sd.answer({"site_by": "Site", "hierarchy_properties": ["Region", "Site"]}, conn=self.snaps(4), now=self.NOW, why=why))
        self.assertEqual(why, ["the newest SolarWinds snapshot has no node custom property Region (solarwinds_snapshot_properties or "
                               "solarwinds_snapshot_skip_properties)"])
        self.assertEqual(sd.answer({"hierarchy_properties": ["Site"]}, conn=self.snaps(4), now=self.NOW)["source"], "db-snapshot")

    def test_name_key(self):
        self.assertEqual([sd.name_key(x) for x in ("ESX07.corp.example.mil", "db01", "192.0.2.7", "", None)],
                         ["esx07", "db01", "192.0.2.7", "", ""])
        self.assertEqual(sd.name_key("", "Core01"), "core01")


def rows_conn(rows):
    """A connection that answers apps_for()'s and alerts_for()'s queries from a snapshot's rows (to_rows), as MySQL would:
    the columns asked for, filtered by the ids, in the ORDER BY's order."""
    def ans(sql, args):
        cols = [c.strip() for c in re.match(r"SELECT (.*?) FROM", sql).group(1).split(",")]
        table = re.search(r"FROM (\w+)", sql).group(1)
        key = re.search(r"AND (\w+) IN", sql).group(1)
        names = sd.COLS[table]
        want = set(args[1:])
        got = [dict(zip(names, r)) for r in rows[table] if r[0] == args[0] and dict(zip(names, r))[key] in want]
        if table == "sw_alert_active":
            old = datetime.datetime(1, 1, 1)
            got.sort(key=lambda r: (r["triggered_at"] or old, r["alert_active_id"]), reverse=True)
        return [(c,) for c in cols], [tuple(r[c] for c in cols) for r in got]
    return FakeConn(ans)


class Live(unittest.TestCase):
    """1.96.1: apps_live() and alerts_live() read SolarWinds itself and give what apps_for() and alerts_for() give."""

    @classmethod
    def setUpClass(cls):
        cls.plain, cls.more = fixtures()
        cls.fake = Fake(cls.more)
        cls.snap = sd.read_swis(cls.fake.client())
        cls.rows = sd.to_rows(cls.snap, 3)
        t = json.load(open(cls.more))["tables"]
        cls.app_nodes = sorted(set(a["NodeID"] for a in t["Orion.APM.Application"]))
        cls.alert_nodes = sorted(set(o["RelatedNodeId"] for o in t["Orion.AlertObjects"] if o.get("RelatedNodeId") is not None))

    @classmethod
    def tearDownClass(cls):
        cls.fake.stop()

    def test_apps_live_is_apps_for(self):
        ids = self.app_nodes + [999999]
        why = []
        live = sd.apps_live(self.fake.client(), ids, why)
        self.assertEqual(live, sd.apps_for(rows_conn(self.rows), 3, ids))
        self.assertEqual(why, [])
        two = [a for n in live.values() for a in n if a["app_id"] == 309][0]          # 2 of 9 down
        self.assertEqual((two["components"], len(two["components_not_up"])), (9, 2))
        self.assertEqual(live[999999], [])

    def test_alerts_live_is_alerts_for(self):
        ids = self.alert_nodes + [999999]
        live = sd.alerts_live(self.fake.client(), ids)
        self.assertEqual(live, sd.alerts_for(rows_conn(self.rows), 3, ids))
        self.assertGreater(sum(len(v) for v in live.values()), 0)
        q = "\n".join(self.fake.queries())
        self.assertNotIn("AcknowledgedBy", q)

    def test_only_the_nodes_asked(self):
        before = len(self.fake.queries())
        sd.apps_live(self.fake.client(), self.app_nodes[:2])
        q = self.fake.queries()[before:]
        self.assertEqual([l.split(" ")[2] for l in q], ["Metadata.Property", "Orion.APM.Application", "Orion.APM.Component",
                                                        "Orion.APM.Component"])           # the apps, the components, the count
        self.assertFalse(any("Orion.AlertActive" in l or "Orion.Nodes " in l for l in q))
        self.assertEqual(sd.apps_live(self.fake.client(), []), {})

    def test_an_orion_without_sam(self):
        f = Fake(self.more, {"FAKE_SWIS_HIDE": "Orion.APM.Application,Orion.APM.Component"})
        try:
            why = []
            got = sd.apps_live(f.client(), self.app_nodes[:3], why)
        finally:
            f.stop()
        self.assertEqual(got, dict((n, []) for n in self.app_nodes[:3]))
        self.assertEqual(why[0], {"what": sd.PART_WORDS["app"], "why": "not on this Orion (no Orion.APM.Application)", "absent": True})

    def test_interfaces_for(self):
        cols = sd.COLS["sw_interface"]
        rows = [dict(zip(cols, r)) for r in self.rows["sw_interface"]]
        node = rows[0]["node_id"]
        mine = sorted((r for r in rows if r["node_id"] == node), key=lambda r: (r["name"] or "", r["interface_id"]))
        got = sd.interfaces_for(rows_conn(self.rows), 3, [node, 999999])
        self.assertEqual(got[999999], [])                                         # not on a link: not in the snapshot
        self.assertEqual([f["interface_id"] for f in got[node]], [r["interface_id"] for r in mine])
        f, r = got[node][0], mine[0]
        self.assertEqual(sorted(f), ["caption", "discards", "errors", "in_pct", "interface_id", "name", "out_pct", "speed", "status",
                                     "status_code"])
        self.assertEqual(f["status"], sw.status_of(r["status"]))
        want = None if r["in_errors_hour"] is None and r["out_errors_hour"] is None else int(r["in_errors_hour"] or 0) + int(r["out_errors_hour"] or 0)
        self.assertEqual(f["errors"], want)
        self.assertEqual(sd.interfaces_for(rows_conn(self.rows), 3, []), {})

    def test_not_reached_raises(self):
        c = sw.Client("https://127.0.0.1:%d" % free_port(), "svc_swis_ro", PW, timeout=2)
        with self.assertRaises(sd._swis_errors()):
            sd.alerts_live(c, [1])


class Lines(unittest.TestCase):
    def test_the_jobs_lines(self):
        r = {"ok": True, "snap_id": 12, "seconds": 41.2, "queries": 31, "version": {"platform": "2026.1"}, "created": True,
             "where": "db.example.mil:3309/site_reports",
             "counts": {"nodes": 967, "nodes_down": 2, "nodes_warning": 14, "links": 1013, "interfaces": 2026, "alerts": 7, "events": 120,
                        "apps": 40, "apps_down": 1, "components": 3, "volumes": 2400, "vim_hosts": 12, "vim_vms": 480},
             "pruned": {"sw_node": 967, "sw_link": 1013, "sw_event": 4, "sw_snapshot": 0},
             "not_read": [{"what": "the volumes (Orion.Volumes)", "why": "SolarWinds could not run this query (HTTP 400)"}]}
        self.assertEqual(sf.swis_snapshot_lines(r), [
            "Snapshot 12 kept.", "It holds:", "- 967 nodes (2 down, 14 warning)", "- 1,013 links", "- 2,026 interfaces on links",
            "- 7 active alerts", "- 120 new events", "- 40 SAM applications (1 down)", "- 3 SAM components not up", "- 2,400 volumes",
            "- 12 VIM hosts", "- 480 VIM VMs", "Read in 41.2 s with 31 SWQL queries from SolarWinds 2026.1.", "Not read:",
            "- the volumes (Orion.Volumes): SolarWinds could not run this query (HTTP 400)",
            "Deleted: 1,980 rows of older snapshots, 4 old events, 0 old snapshot rows.",
            "The SolarWinds tables were made in db.example.mil:3309/site_reports."])
        self.assertEqual(sf.swis_snapshot_lines({"skipped_lock": True}), ["Another SolarWinds snapshot run is writing: this run kept nothing."])
        self.assertEqual(sf.swis_snapshot_lines({"ok": True, "counts": {}}, True)[0], "Check: SolarWinds read, nothing written.")


class SchemaTwo(unittest.TestCase):
    """1.113.0: schema 2 (sw_vim_host.vcenter) - an older release's tables brought up in place, the ALTER only when
    information_schema lacks the column (MySQL 8.4 has no ADD COLUMN IF NOT EXISTS); without it the snapshot is
    written without the column and says so."""

    def conn(self, has_column, version=1):
        st = {"column": has_column, "version": version}

        def ans(sql, args):
            if "information_schema.tables" in sql:
                return [("table_name",)], ([(t,) for t in sd.TABLES] if "LIKE" in sql else [("schema_version",)])
            if "information_schema.columns" in sql:
                return [("n",)], [(1 if st["column"] else 0,)]
            if sql.startswith("ALTER TABLE sw_vim_host ADD COLUMN vcenter"):
                if st["column"]:
                    raise RuntimeError("Duplicate column name 'vcenter'")
                st["column"] = True
                return [], []
            if sql.startswith("SELECT version FROM schema_version"):
                return [("version",)], [(st["version"],)]
            if sql.startswith("UPDATE schema_version"):
                st["version"] = 2
                return [], []
            return [], []
        return FakeConn(ans), st

    def test_an_older_release_brought_up_in_place_once(self):
        c, st = self.conn(False)
        self.assertEqual(sd.missing_columns(c), [("sw_vim_host", "vcenter")])
        self.assertEqual(sd.schema(c, True), (True, False))
        self.assertEqual(st, {"column": True, "version": 2})
        self.assertEqual(sum(1 for s, _a in c.sql if s.startswith("ALTER")), 1)
        sd.schema(c, True)                                   # again: no second ALTER, and no UPDATE (a read-only account gets that far)
        self.assertEqual(sum(1 for s, _a in c.sql if s.startswith("ALTER")), 1)
        self.assertEqual(sum(1 for s, _a in c.sql if s.startswith("UPDATE")), 1)

    def test_tables_made_by_hand_are_never_altered(self):
        c, st = self.conn(False)
        self.assertEqual(sd.schema(c, False), (True, False))
        self.assertEqual(st, {"column": False, "version": 1})
        self.assertFalse(any(s.startswith(("ALTER", "UPDATE")) for s, _a in c.sql))

    def test_written_without_a_missing_column(self):
        got = []

        class Cur(object):
            def executemany(self, sql, rows):
                got.append((sql, list(rows)))
        row = (7, 60, 101, "esx01.example.mil", "esx01", "cluster1", 1, "connected", "poweredOn", 10.0, 20.0, 12, 11, "VMware ESXi",
               "vc01.example.mil")
        self.assertEqual(len(row), len(sd.COLS["sw_vim_host"]))
        sd._insert(Cur(), "sw_vim_host", [row], [("sw_vim_host", "vcenter")])
        sd._insert(Cur(), "sw_vim_host", [row])
        (old_sql, old_rows), (new_sql, new_rows) = got
        self.assertNotIn("vcenter", old_sql)
        self.assertEqual(old_rows, [row[:-1]])
        self.assertIn(", vcenter)", new_sql)
        self.assertEqual(new_rows, [row])

    def test_the_vcenter_of_each_vim_host(self):
        snap = {"extra": {"vim_host": [{"host_id": 60, "node_id": 101, "name": "esx01.example.mil", "vcenter": "vc01.example.mil"},
                                       {"host_id": 61, "node_id": 102, "name": "esx02.example.mil"}]}}
        rows = sd.to_rows(dict(snap, raw={"nodes": [], "links": []}), 7)["sw_vim_host"]
        i = sd.COLS["sw_vim_host"].index("vcenter")
        self.assertEqual([r[i] for r in rows], ["vc01.example.mil", None])


class Doc(unittest.TestCase):
    def test_the_doc_has_the_statements_word_for_word(self):
        with open(os.path.join(ROOT, "docs", "REPORT_DATABASE.md"), encoding="utf-8") as fh:
            doc = fh.read()
        m = re.search(r"<!-- the statements: site_swis_db.ddl\(\)[^>]*-->\n(.*?)\n<!-- end of the SolarWinds statements -->", doc, re.S)
        self.assertIsNotNone(m)
        blocks = re.findall(r"```sql\n(.*?)\n```", m.group(1), re.S)
        self.assertEqual("\n\n".join(blocks), sd.ddl())

    def test_the_doc_has_the_upgrade_word_for_word(self):
        with open(os.path.join(ROOT, "docs", "REPORT_DATABASE.md"), encoding="utf-8") as fh:
            doc = fh.read()
        m = re.search(r"<!-- the upgrade: site_swis_db.upgrade_ddl\(\)[^>]*-->\n```sql\n(.*?)\n```\n<!-- end of the SolarWinds upgrade -->", doc, re.S)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), sd.upgrade_ddl())

    def test_every_table_in_the_doc(self):
        with open(os.path.join(ROOT, "docs", "REPORT_DATABASE.md"), encoding="utf-8") as fh:
            doc = fh.read()
        for t in sd.TABLES:
            self.assertIn("`%s`" % t, doc, t)


if __name__ == "__main__":
    unittest.main()
