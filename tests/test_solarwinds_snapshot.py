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
import socket
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "plugins", "module_utils"), os.path.join(ROOT, "plugins", "filter")]
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
        f = Fake(self.more, {"FAKE_SWIS_HIDE": "Orion.APM.Application,Orion.APM.Component,Orion.VIM.Hosts,Orion.VIM.VirtualMachines,Orion.VIM.Clusters"})
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

    def test_not_reached(self):
        c = sw.Client("https://127.0.0.1:%d" % free_port(), "svc_swis_ro", PW, timeout=2)
        s = sd.read_swis(c)
        self.assertFalse(s["raw"]["reachable"])
        self.assertEqual(s["extra"], {})
        self.assertIn("refused", s["raw"]["error"])

    def test_the_events_from_the_newest_kept_on(self):
        f = Fake(self.more)
        try:
            ev = json.load(open(self.more))["tables"]["Orion.Events"]
            s = sd.read_swis(f.client(), {"parts": ["event"], "events_after": ev[-1]["EventID"] - 5, "event_hours": 48})
            q = [l for l in f.queries() if " Orion.Events " in l]
        finally:
            f.stop()
        self.assertEqual([e["event_id"] for e in s["extra"]["event"]], [e["EventID"] for e in ev[-5:]])
        self.assertEqual(s["extra"]["event"][0]["type_name"], dict((t["EventType"], t["Name"]) for t in
                                                                   json.load(open(self.more))["tables"]["Orion.EventTypes"])[ev[-5]["EventType"]])
        self.assertEqual(len(q), 1)

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
        self.description, self.rows = self.conn.answer(sql, args)
        return len(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class FakeConn(object):
    def __init__(self, answer):
        self.answer, self.sql = answer, []

    def cursor(self):
        return FakeCur(self)


class Answer(unittest.TestCase):
    NOW = datetime.datetime(2026, 11, 21, 9, 0, 0)

    def snaps(self, age_min, failed=None):
        def ans(sql, args):
            if "WHERE ok = 0" in sql:
                return [("read_at",), ("error",)], ([(self.NOW, failed)] if failed else [])
            if "FROM sw_snapshot WHERE ok = 1" in sql:
                if age_min is None:
                    return [], []
                return [("snap_id",)] * 7, [(5, self.NOW - datetime.timedelta(minutes=age_min), json.dumps({"props": []}), "[]", 10, 0, 0)]
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

    def test_a_database_that_fails_is_a_reason(self):
        why = []
        self.assertIsNone(sd.answer({}, conn=FakeConn(RuntimeError("SELECT command denied")), why=why))
        self.assertEqual(why, ["the report database's SolarWinds snapshot could not be read: SELECT command denied"])

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
        web = got[11][0]
        self.assertEqual((web["app"], web["status"], web["components"], len(web["components_not_up"])), ("Web server", "Down", 9, 2))
        self.assertEqual([(c["name"], c["status"], c["value"], c["message"]) for c in web["components_not_up"]],
                         [("Port 443", "Down", None, "Port 443 refused the connection"), ("HTTPS monitor", "Warning", 812, "Slow")])
        self.assertEqual((got[11][1]["status"], got[11][1]["components_not_up"]), ("Up", []))
        self.assertEqual(conn.sql[-1][1][:2], [5, 309])
        self.assertEqual(sd.apps_for(conn, 5, []), {})
        note = {"what": sd.PART_WORDS["app"], "why": "not on this Orion (no Orion.APM.Application)", "absent": True}
        self.assertEqual(sd.part_note({"not_read": [note]}, "app"), note)
        self.assertIsNone(sd.part_note({"not_read": []}, "app"))

    def test_name_key(self):
        self.assertEqual([sd.name_key(x) for x in ("ESX07.corp.example.mil", "db01", "192.0.2.7", "", None)],
                         ["esx07", "db01", "192.0.2.7", "", ""])
        self.assertEqual(sd.name_key("", "Core01"), "core01")


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


class Doc(unittest.TestCase):
    def test_the_doc_has_the_statements_word_for_word(self):
        with open(os.path.join(ROOT, "docs", "REPORT_DATABASE.md"), encoding="utf-8") as fh:
            doc = fh.read()
        m = re.search(r"<!-- the statements: site_swis_db.ddl\(\)[^>]*-->\n(.*?)\n<!-- end of the SolarWinds statements -->", doc, re.S)
        self.assertIsNotNone(m)
        blocks = re.findall(r"```sql\n(.*?)\n```", m.group(1), re.S)
        self.assertEqual("\n\n".join(blocks), sd.ddl())

    def test_every_table_in_the_doc(self):
        with open(os.path.join(ROOT, "docs", "REPORT_DATABASE.md"), encoding="utf-8") as fh:
            doc = fh.read()
        for t in sd.TABLES:
            self.assertIn("`%s`" % t, doc, t)


if __name__ == "__main__":
    unittest.main()
