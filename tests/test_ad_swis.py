#!/usr/bin/env python3
"""1.94.0: the AD health report's "From SolarWinds" - plugins/module_utils/site_ad_swis.py (the snapshot in the report
database, else SolarWinds itself) and plugins/filter/ad_swis_filters.py (the section, its tile, Not read). The snapshot's
reader (1.96.0 site_swis_db) is a stand-in with its shapes; SolarWinds is tests/netmap/fake_swis.py over HTTPS with
tests/ad/swis_fixture.json. Placeholders only.
  python tests/test_ad_swis.py"""
import copy
import datetime
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "plugins", "module_utils"), os.path.join(ROOT, "plugins", "filter"), os.path.join(ROOT, "tests")]
import jinja2  # noqa: E402
import site_swis as sw  # noqa: E402
import site_ad_swis as ad  # noqa: E402
import ad_swis_filters as af  # noqa: E402
import site_filters as sf  # noqa: E402

FX = os.path.join(ROOT, "tests", "ad", "swis_fixture.json")
PW = json.load(open(FX))["password"]
NOW = "2026-10-15T06:53:00Z"
DCS = [{"dc": "DC01", "ip": ""}, {"dc": "DC02", "ip": ""}, {"dc": "DC03", "ip": "192.0.2.13"}, {"dc": "DC04", "ip": ""}]


def t(s):
    return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M")


class Conn(object):
    """The report database's session, as far as the interval reads it (sw_snapshot's read_at, newest first)."""

    def __init__(self, stamps):
        self.stamps = stamps

    def cursor(self):
        conn = self

        class C(object):
            def execute(self, sql, args=None):
                assert sql.startswith("SELECT read_at FROM sw_snapshot ORDER BY snap_id DESC LIMIT 4")

            def fetchall(self):
                return [(t(x),) for x in conn.stamps]
        return C()


class Snap(object):
    """1.96.0's site_swis_db, as nuc-dev-2 gave its shapes."""

    def __init__(self, age=600, latest="ok", sam=True, fail=None):
        self.age, self.kind, self.sam, self.fail, self.calls = age, latest, sam, fail, []

    def latest(self, conn):
        self.calls.append("latest")
        if self.kind == "1146":
            raise Exception("(1146, \"Table 'site_reports.sw_snapshot' doesn't exist\")")
        if self.kind == "none":
            return None
        return {"snap_id": 7, "read_at": t("2026-10-15 06:43"), "age_seconds": self.age, "nodes": 3, "alerts": 1, "events": 4, "core": {}, "not_read": []}

    def last_failure(self, conn):
        return (t("2026-10-15 06:50"), "login refused")

    def nodes_for(self, conn, snap_id, names):
        self.calls.append(("nodes_for", tuple(names)))
        rows = {"dc01": {"node_id": 11, "name": "dc01.corp.test", "name_key": "dc01", "dns": "", "ip": "192.0.2.11", "status_code": 2,
                         "status": "Down", "status_text": "Node status is Down.", "unmanaged": False, "last_poll": t("2026-10-15 06:40"),
                         "cpu_pct": None, "mem_pct": None},
                "dc02": {"node_id": 12, "name": "DC02", "name_key": "dc02", "dns": "", "ip": "192.0.2.12", "status_code": 1, "status": "Up",
                         "status_text": "", "unmanaged": False, "last_poll": t("2026-10-15 06:41"), "cpu_pct": 97.2, "mem_pct": 91},
                "192.0.2.13": {"node_id": 13, "name": "srv-dc-third", "name_key": "srv-dc-third", "dns": "", "ip": "192.0.2.13", "status_code": 3,
                               "status": "Warning", "status_text": "", "unmanaged": False, "last_poll": t("2026-10-15 06:42"), "cpu_pct": 22, "mem_pct": 40}}
        if self.fail == "nodes_for":
            raise RuntimeError("boom")
        return dict((n, rows.get(n)) for n in names)

    def part_note(self, snap, part):
        return {} if self.sam else {"what": "SAM's applications (Orion.APM.Application)", "why": "not on this Orion (no Orion.APM.Application)", "absent": True}

    def apps_for(self, conn, snap_id, ids):
        self.calls.append(("apps_for", tuple(ids)))
        c = lambda n, st: {"component_id": 1, "name": n, "kind": 8, "status": st, "status_code": 2, "status_text": st, "value": "3", "message": "8524"}  # noqa: E731
        return {11: [{"app_id": 1, "app": "AD", "status": "Unknown", "status_code": 0, "status_text": "", "unmanaged": False, "components": 9,
                      "components_not_up": [c("x%d" % i, "Unknown") for i in range(9)]}],
                12: [{"app_id": 2, "app": "AD", "status": "Down", "status_code": 2, "status_text": "", "unmanaged": False, "components": 9,
                      "components_not_up": [c("Replication", "Down"), c("Netlogon", "Down")]}],
                13: [{"app_id": 3, "app": "AD", "status": "Warning", "status_code": 3, "status_text": "", "unmanaged": False, "components": 9,
                      "components_not_up": [c("SYSVOL share", "Warning")]}]}

    def alerts_for(self, conn, snap_id, ids):
        return {12: [{"name": "High CPU", "severity": 1, "triggered_at": t("2026-10-15 06:20"), "message": "CPU 97", "entity_type": "Orion.Nodes",
                      "entity_caption": "DC02", "acknowledged": False}]}

    def events_between(self, conn, start, end, ids):
        assert (end - start).days == 30
        return [{"event_id": 1, "event_time": t("2026-10-15 06:15"), "node_id": 11, "event_type": 1},
                {"event_id": 2, "event_time": t("2026-10-14 22:14"), "node_id": 13, "event_type": 1},
                {"event_id": 3, "event_time": t("2026-10-14 22:20"), "node_id": 13, "event_type": 5},
                {"event_id": 4, "event_time": t("2026-08-01 10:00"), "node_id": 12, "event_type": 1}]      # outside the 30 days


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Live(object):
    """tests/netmap/fake_swis.py over HTTPS with the AD fixture."""

    def __init__(self, tmp, hide=""):
        self.port, self.log = free_port(), os.path.join(tmp, "swis-%s.log" % hide.replace(",", "_"))
        self.crt, self.key = os.path.join(tmp, "c.pem"), os.path.join(tmp, "k.pem")
        if not os.path.exists(self.crt):
            subprocess.check_call(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", self.key, "-out", self.crt, "-days", "2",
                                   "-subj", "/CN=127.0.0.1"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.p = subprocess.Popen([sys.executable, os.path.join(ROOT, "tests", "netmap", "fake_swis.py"), str(self.port), FX, self.log, self.crt, self.key],
                                  env=dict(os.environ, FAKE_SWIS_HIDE=hide), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", self.port), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        self.client = sw.Client("https://127.0.0.1:%d" % self.port, "svc_swis_ro", PW, verify=False, timeout=10)

    def stop(self):
        self.p.kill()
        self.p.wait()


class Read1940(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.live = Live(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        cls.live.stop()
        shutil.rmtree(cls.tmp, True)

    def test_1_the_snapshot(self):
        swdb = Snap()
        r = ad.read(DCS, {"now": NOW, "days": 30, "max_age": 60}, conn=Conn(["2026-10-15 06:43", "2026-10-15 06:33", "2026-10-15 06:24", "2026-10-15 06:13"]), swdb=swdb)
        self.assertEqual((r["source"], r["age_min"], r["interval_min"], r["stale"]), ("db-snapshot", 10, 10, False))
        d = dict((x["dc"], x) for x in r["dcs"])
        self.assertEqual((d["DC01"]["matched"], d["DC03"]["matched"], d["DC04"]["matched"]), ("name", "ip", ""))     # name, then the IP
        self.assertEqual(d["DC02"]["apps"]["text"], "2 of 9 down: Netlogon, Replication")
        self.assertEqual(d["DC01"]["apps"]["text"], "9 of 9 unknown")
        self.assertEqual(d["DC01"]["last_down"], {"at": "2026-10-15 06:15", "minutes": 38, "ongoing": True})
        self.assertEqual(d["DC03"]["last_down"], {"at": "2026-10-14 22:14", "minutes": 6, "ongoing": False})
        self.assertIsNone(d["DC02"]["last_down"])                                    # outside the 30 days
        self.assertEqual([a["name"] for a in d["DC02"]["alerts"]], ["High CPU"])
        self.assertIsNone(d["DC02"]["ifaces"])                                       # no interfaces_for: not read (said so)
        self.assertEqual(swdb.calls[1], ("nodes_for", ("192.0.2.13", "dc01", "dc02", "dc03", "dc04")))   # the names and the IPs, one call

    def test_2_no_snapshot_table_then_live(self):
        r = ad.read(DCS, {"now": NOW}, conn=Conn([]), swdb=Snap(latest="1146"), client=self.live.client)
        self.assertEqual(r["source"], "swis-live")
        self.assertIn("the job SolarWinds - snapshot has not run", r["why"][0])
        d = dict((x["dc"], x) for x in r["dcs"])
        self.assertEqual(d["DC02"]["apps"]["text"], "2 of 9 down: AD Replication (repadmin), Netlogon service")
        self.assertEqual(d["DC02"]["apps"]["total"], 9)                              # the Up ones counted too
        self.assertEqual(d["DC02"]["ifaces"], [{"name": "Ethernet1", "status": "Up", "errors": 1204, "discards": 0}])
        self.assertEqual(d["DC01"]["ifaces"], [])                                    # none monitored
        self.assertFalse(r["events"])
        log = open(self.live.log).read()
        self.assertNotIn("SECRET-COLUMN-ASKED", log)
        self.assertNotIn(PW, log)

    def test_3_no_whole_snapshot_and_no_solarwinds(self):
        r = ad.read(DCS, {"now": NOW}, conn=Conn([]), swdb=Snap(latest="none"))
        self.assertFalse(r["ok"])
        self.assertIn("login refused", r["why"][0])

    def test_4_an_old_snapshot_is_said_old(self):
        r = ad.read(DCS, {"now": NOW, "max_age": 60}, conn=Conn([]), swdb=Snap(age=7200))
        self.assertTrue(r["ok"] and r["stale"])
        self.assertIn("120 min old", r["why"][0])
        self.assertIn("login refused", r["why"][0])
        v = af.ad_swis_view(dict(r, asked=True), {}, {"now": NOW, "max_age": 60})
        self.assertIn("OLDER than ad_solarwinds_max_age", v["section"]["text"])
        r = ad.read(DCS, {"now": NOW, "max_age": 60}, conn=Conn([]), swdb=Snap(age=7200), client=self.live.client)
        self.assertEqual(r["source"], "swis-live")                                   # SolarWinds itself is fresher

    def test_5_no_sam(self):
        r = ad.read(DCS, {"now": NOW}, conn=Conn([]), swdb=Snap(sam=False))
        self.assertFalse(r["sam"])
        v = af.ad_swis_view(dict(r, asked=True), {}, {"now": NOW})
        self.assertNotIn("AD monitors (SAM)", v["section"]["columns"])
        self.assertIn("not on this Orion", v["section"]["text"])
        tmp = tempfile.mkdtemp()
        live = Live(tmp, hide="Orion.APM.Application,Orion.APM.Component")
        try:
            r = ad.read(DCS, {"now": NOW}, client=live.client)
            self.assertFalse(r["sam"])
            self.assertTrue(r["ok"])
        finally:
            live.stop()
            shutil.rmtree(tmp, True)

    def test_6_never_raises(self):
        r = ad.read(DCS, {"now": NOW}, conn=Conn([]), swdb=Snap(fail="nodes_for"))
        self.assertFalse(r["ok"])
        self.assertIn("a RuntimeError", " ".join(r["why"]))
        self.assertNotIn("boom", " ".join(r["why"]))                                 # 1.94.0 review: fixed words, never the text

        class Dead(object):
            def query(self, *a, **k):
                raise sw.SwisError("SolarWinds did not answer")
            paged = query
        r = ad.read(DCS, {"now": NOW}, client=Dead())
        self.assertFalse(r["ok"])
        self.assertIn("did not answer", r["why"][0])


def ad_doc():
    return json.load(open(os.path.join(ROOT, "tests", "fixtures", "report_fit", "ad_report.json")))


class View1940(unittest.TestCase):
    def setUp(self):
        self.r = ad.read(DCS, {"now": NOW}, conn=Conn(["2026-10-15 06:43", "2026-10-15 06:33"]), swdb=Snap())
        self.r["asked"] = True
        self.ad = {"problems": [{"domain": "corp.test", "area": "LDAP", "object": "DC01", "severity": "critical", "text": "LDAP not in sync"}]}
        self.v = af.ad_swis_view(self.r, self.ad, {"now": NOW, "days": 30})

    def test_1_the_section(self):
        s = self.v["section"]
        self.assertEqual(s["columns"], ["", "DC", "SolarWinds", "AD monitors (SAM)", "CPU / memory (%)", "Alerts", "Last down (30 days)", "What it means"])
        self.assertEqual([r[1] for r in s["rows"]], ["DC01", "DC02", "DC03", "DC04"])     # worst first; not found: info
        r = dict((x[1], x) for x in s["rows"])
        self.assertEqual(r["DC01"][7], "Down in SolarWinds and in AD (LDAP not in sync): the DC itself")
        self.assertEqual(r["DC01"][6], "now, for 38 min")
        self.assertEqual(r["DC02"][4], "97 / 91")
        self.assertEqual(r["DC04"][2], "not in SolarWinds")
        self.assertIn("Not found by name", r["DC04"][7])
        self.assertIn("taken every 10 min", s["text"])
        self.assertEqual(s["details"][1][0]["rows"][0][0], "Netlogon")                   # by name within a status; one template: not repeated
        self.assertEqual(self.v["tile"], {"label": "SolarWinds: down or warning", "status": "critical", "value": 3})

    def test_2_inserted_and_the_counts_unchanged(self):
        doc = ad_doc()
        before = (doc["status"], doc["title"], len(doc["sections"]))
        out = af.ad_swis_insert(doc, self.v)
        titles = [s["title"] for s in out["sections"]]
        self.assertEqual(titles.index("From SolarWinds"), titles.index("Domain controllers' health") + 1)
        self.assertEqual(out["summary"][2]["label"], "SolarWinds: down or warning")   # after the first plain-number tile
        self.assertEqual(sf.report_headline(out), sf.report_headline(doc))
        self.assertEqual((out["status"], out["title"], len(out["sections"]) - 1), before)    # SolarWinds never changes AD's counts

    def test_3_no_credential_one_line(self):
        v = af.ad_swis_view({"skipped": True}, {}, {"explain_off": "this job has no SolarWinds credential"})
        out = af.ad_swis_insert(ad_doc(), v)
        self.assertNotIn("From SolarWinds", [s["title"] for s in out["sections"]])
        nr = [s for s in out["sections"] if s["title"] == "Not read"][0]
        self.assertIn("SolarWinds: not read - this job has no SolarWinds credential", nr["lines"])
        self.assertNotIn("SolarWinds: down or warning", [x["label"] for x in out["summary"]])

    def test_4_every_look_and_the_workbook(self):
        doc = af.ad_swis_insert(ad_doc(), self.v, True)                               # the email's and the workbook's form
        titles = [x["title"] for x in doc["sections"]]
        self.assertEqual(titles.index("From SolarWinds: AD monitors not up"), titles.index("From SolarWinds") + 1)
        self.assertNotIn("From SolarWinds: AD monitors not up", [x["title"] for x in af.ad_swis_insert(ad_doc(), self.v)["sections"]])
        sec = [x for x in doc["sections"] if x["title"] == "From SolarWinds"][0]
        self.assertEqual(sec["columns"][0], "DC")                                    # the email: no column for rows that open
        self.assertNotIn("details", sec)
        self.assertTrue(all(len(r) == len(sec["columns"]) for r in sec["rows"] + sec["cell_status"]))
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(ROOT, "roles", "site_email", "templates")), autoescape=False)
        env.filters.update(sf.FilterModule().filters())
        for look in ("report", "report_atlas", "report_brief", "report_ops"):
            kw = {} if look == "report" else {"_page_runs": []}
            html = env.get_template(look + ".html.j2").render(_report=doc, _email_footer="x", _page_logo_cid="", report_email_header_text="x", **kw)
            self.assertIn("From SolarWinds", html, look)
            self.assertIn("2 of 9 down: Netlogon, Replication", html, look)
            self.assertIn("8524", html, look)                                        # the components not up: in the email too
        sheets = sf.FilterModule().filters()["report_xlsx_sheets"](doc)
        self.assertIn("From SolarWinds", [s["name"] for s in sheets])

    def test_5_a_time_zone(self):
        v = af.ad_swis_view(self.r, self.ad, {"now": NOW, "time_zone": "Europe/Berlin"})
        self.assertIn("08:43 CEST", v["section"]["text"])

    def test_6_dcs_and_their_ips(self):
        hv = {"dc01.corp.test": {"ansible_host": "192.0.2.11"}, "dc02.corp.test": {"ansible_host": "dc02.corp.test"}}
        self.assertEqual(af.ad_swis_dcs([{"dc": "DC01", "host": "dc01.corp.test", "fqdn": "dc01.corp.test"}, {"dc": "DC02", "host": "dc02.corp.test", "fqdn": "dc02.corp.test"}, {"dc": "DC03", "host": "x"}], hv),
                         [{"dc_id": "dc01", "dc": "DC01", "ip": "192.0.2.11", "fqdn": "dc01.corp.test", "domain": ""}, {"dc_id": "dc02", "dc": "DC02", "ip": "", "fqdn": "dc02.corp.test", "domain": ""},
                          {"dc_id": "dc03", "dc": "DC03", "ip": "", "fqdn": "", "domain": ""}])


class Review1940(unittest.TestCase):
    """1.94.0 review round: the full name first, a short name only when one node has it, an IP only when one node has it,
    never one node for two DCs; the headline stays AD's; problems by the whole DC name; no exception text; never raises."""

    N = [{"node_id": 1, "name": "DC01.child.test", "dns": "", "ip": "192.0.2.91", "status": "Down"},
         {"node_id": 2, "name": "dc01.corp.test", "dns": "dc01.corp.test", "ip": "192.0.2.11", "status": "Up"},
         {"node_id": 3, "name": "dc02", "dns": "", "ip": "192.0.2.12", "status": "Up"},
         {"node_id": 4, "name": "sql-vip", "dns": "", "ip": "192.0.2.12", "status": "Up"},
         {"node_id": 5, "name": "dc05", "dns": "", "ip": "", "status": "Up"},
         {"node_id": 6, "name": "dc05", "dns": "", "ip": "192.0.2.15", "status": "Warning"}]

    def m(self, dcs):                                    # by the DC's name, for reading (match() gives them by position)
        got = ad.match(dcs, self.N)
        return dict((d["dc"], (got[i][0] and got[i][0]["node_id"], got[i][1], got[i][2])) for i, d in enumerate(dcs))

    def test_1_two_domains_one_short_name(self):
        r = self.m([{"dc": "DC01", "fqdn": "dc01.corp.test", "ip": "192.0.2.11"}])
        self.assertEqual(r["DC01"][:2], (2, "name"))                                 # its own FQDN, not DC01.child.test
        r = self.m([{"dc": "DC01", "fqdn": "dc01.corp.test", "ip": ""}])
        self.assertEqual(r["DC01"][:2], (2, "name"))
        nodes = [dict(n) for n in self.N if n["node_id"] != 2]                      # its own node gone: the other domain's never stands in
        self.assertEqual(ad.match([{"dc": "DC01", "fqdn": "dc01.corp.test", "ip": ""}], nodes)[0][0]["node_id"], 1)

    def test_2_a_shared_ip_is_not_a_match(self):
        r = self.m([{"dc": "DC02", "fqdn": "", "ip": "192.0.2.12"}])
        self.assertEqual(r["DC02"][:2], (3, "name"))                                 # the name is unique: matched by it
        r = ad.match([{"dc": "DCX", "fqdn": "", "ip": "192.0.2.12"}], self.N)[0]
        self.assertIsNone(r[0])
        self.assertIn("ambiguous (2 nodes with IP", r[2])
        r = self.m([{"dc": "DC05", "fqdn": "", "ip": "192.0.2.15"}])
        self.assertEqual(r["DC05"][:2], (6, "ip"))                                   # two nodes named dc05: its IP decides

    def test_3_one_node_never_two_dcs(self):
        r = ad.match([{"dc": "DC02", "fqdn": "", "ip": ""}, {"dc": "dc02", "fqdn": "", "ip": "192.0.2.12"}], self.N)
        self.assertIsNone(r[0][0])
        self.assertIsNone(r[1][0])
        self.assertIn("the same SolarWinds node claimed by dc02, dc02", r[1][2])
        v = af.ad_swis_view({"asked": True, "ok": True, "source": "swis-live", "read_at": "2026-10-15 06:53", "sam": False,
                             "dcs": [{"dc": "dc02", "matched": "", "unmatched_why": r[1][2]}]}, {}, {"now": NOW})
        row = v["section"]["rows"][0]
        self.assertEqual((row[2], v["section"]["cell_status"][0][2]), ("not matched: the same SolarWinds node claimed by dc02, dc02", "warning"))
        self.assertIn("the same SolarWinds node claimed by dc02, dc02", row[-1])

    def test_4_the_headline_stays_ads(self):
        r = ad.read(DCS, {"now": NOW}, conn=Conn([]), swdb=Snap())
        v = af.ad_swis_view(dict(r, asked=True), {}, {"now": NOW})
        for name in ("ad_report", "ad_reader_report"):
            doc = json.load(open(os.path.join(ROOT, "tests", "fixtures", "report_fit", name + ".json")))
            self.assertEqual(sf.report_headline(af.ad_swis_insert(doc, v)), sf.report_headline(doc), name)

    def test_5_problems_by_the_whole_dc_name(self):
        d = [{"dc": "DC01", "domain": "a.test", "matched": "name", "status": "Down"}, {"dc": "DC01", "domain": "b.test", "matched": "name", "status": "Up"}]
        v = af.ad_swis_view({"asked": True, "ok": True, "source": "swis-live", "read_at": "2026-10-15 06:53", "sam": False, "dcs": d},
                            {"problems": [{"domain": "a.test", "object": "DC01", "severity": "critical", "text": "LDAP not in sync"}]}, {"now": NOW})
        r = [x[-1] for x in v["section"]["rows"]]
        self.assertEqual(sum("LDAP not in sync" in x for x in r), 1)                # only a.test's DC01 has a.test's problem
        self.assertIn("LDAP not in sync", r[0])

    def test_6_no_exception_text(self):
        class Err(Exception):
            pass
        snap = Snap()

        def latest(conn):
            raise Err(1045, "Access denied for user 'svc_report_x'@'198.51.100.7' (using password: YES)")
        snap.latest = latest
        r = ad.read(DCS, {"now": NOW}, conn=Conn([]), swdb=snap)
        w = " ".join(r["why"])
        self.assertIn("the login was refused", w)
        for bad in ("svc_report_x", "198.51.100.7", "Access denied"):
            self.assertNotIn(bad, w)

    def test_7_never_raises(self):
        class Odd(object):
            def paged(self, *a, **k):
                raise ValueError("the internal secret text")
            query = paged
        r = ad.read(DCS, {"now": NOW}, client=Odd())
        self.assertFalse(r["ok"])
        self.assertNotIn("secret", " ".join(r["why"]))
        bad = {"asked": True, "ok": True, "source": "swis-live", "read_at": "x", "sam": True,
               "dcs": [{"dc": "DC01", "matched": "name", "status": "Up", "cpu": "n/a", "mem": [], "apps": "not a dict"}]}
        v = af.ad_swis_view(bad, {}, {"now": NOW})                                    # the view never raises: one Not read line
        self.assertIsNone(v["section"])
        self.assertTrue(v["not_read"])
        v = af.ad_swis_view({"failed": False, "module_stderr": "Traceback ... password=x", "msg": "MODULE FAILURE"}, {}, {})   # failed_when: false
        self.assertIn("the reader stopped", v["not_read"][0])
        self.assertNotIn("password", json.dumps(v))

    def test_8_the_rest(self):
        self.assertIsNone(ad._pct(-2))                                               # no data, not "-2 %"
        s = Snap()
        s.latest = lambda conn: {"snap_id": 7, "read_at": t("2026-10-15 03:00"), "nodes": 3}      # no age_seconds: from its time
        r = ad.read(DCS, {"now": NOW, "max_age": 60}, conn=Conn([]), swdb=s)
        self.assertTrue(r["stale"])

        class Cut(object):
            def paged(self, *a, **k):
                return [{"NodeID": 9, "Caption": "other", "IPAddress": "192.0.2.99", "Status": 1}], True
            def query(self, *a, **k):
                raise sw.SwisError("Metadata.Entity: not allowed")
        r = ad.read([{"dc": "DC01", "ip": "", "fqdn": ""}], {"now": NOW}, client=Cut())
        self.assertTrue(r["ok"])                                                     # the nodes kept without SAM or alerts
        self.assertIn("cut", r["dcs"][0]["unmatched_why"])
        self.assertIn("not read", r["sam_why"])


    def test_9_two_dc01s_in_two_domains(self):
        """The final review's case: admin mode names both "DC01"; each gets its own node and its own problems."""
        dcs = [{"dc": "DC01", "fqdn": "dc01.corp.test", "ip": "192.0.2.11", "domain": "corp.test"},
               {"dc": "DC01", "fqdn": "dc01.child.test", "ip": "192.0.2.50", "domain": "child.test"}]

        class Two(object):
            def paged(self, *a, **k):
                return [{"NodeID": 1, "Caption": "DC01.child.test", "IPAddress": "192.0.2.50", "Status": 2},
                        {"NodeID": 2, "Caption": "dc01.corp.test", "IPAddress": "192.0.2.11", "Status": 1}], False

            def query(self, *a, **k):
                return []
        r = ad.read(dcs, {"now": NOW}, client=Two())
        self.assertEqual([(x["fqdn"], x["node_id"], x["status"]) for x in r["dcs"]],
                         [("dc01.corp.test", 2, "Up"), ("dc01.child.test", 1, "Down")])
        v = af.ad_swis_view(dict(r, asked=True), {"problems": [{"domain": "child.test", "object": "DC01", "severity": "critical",
                                                                "text": "LDAP not in sync"}]}, {"now": NOW})
        rows = v["section"]["rows"]                                                  # worst first: child.test's (Down) first
        self.assertEqual([x[2] for x in rows], ["Down", "Up"])
        self.assertIn("Down in SolarWinds and in AD (LDAP not in sync)", rows[0][-1])
        self.assertNotIn("LDAP", rows[1][-1])
        self.assertTrue(af._plain("1,200") and af._plain(2.5) and not af._plain("98%"))



class Counts1940(unittest.TestCase):
    """1.94.0 third review: SolarWinds' rows are never the run's counts (run history, the trend); times with an offset;
    Orion's Critical; a down with no Up event while the node is up."""

    def test_1_run_counts_unchanged(self):
        import importlib.util
        for name in ("site_aap", "site_report_page", "site_banner", "site_time", "site_report_files"):   # as Ansible gives them (test_report_web)
            if "ansible.module_utils." + name not in sys.modules:
                sp = importlib.util.spec_from_file_location("ansible.module_utils." + name, os.path.join(ROOT, "plugins", "module_utils", name + ".py"))
                mod = importlib.util.module_from_spec(sp)
                sys.modules["ansible.module_utils." + name] = mod
                sp.loader.exec_module(mod)
        spec = importlib.util.spec_from_file_location("srw", os.path.join(ROOT, "roles", "site_email", "library", "site_report_web.py"))
        srw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(srw)
        r = ad.read(DCS, {"now": NOW}, conn=Conn([]), swdb=Snap())
        v = af.ad_swis_view(dict(r, asked=True), {}, {"now": NOW})
        for name in ("ad_report", "ad_reader_report"):
            doc = json.load(open(os.path.join(ROOT, "tests", "fixtures", "report_fit", name + ".json")))
            for flat in (False, True):
                self.assertEqual(srw.run_counts(af.ad_swis_insert(doc, v, flat)), srw.run_counts(doc), (name, flat))

    def test_2_an_offset_is_converted(self):
        self.assertEqual(ad._utc("2026-10-15T08:53:00+02:00"), t("2026-10-15 06:53"))
        self.assertEqual(ad._utc("2026-10-15T01:53:00-05:00"), t("2026-10-15 06:53"))

    def test_3_critical_and_an_open_down(self):
        nodes = [{"node_id": 1, "name": "dc01", "dns": "", "ip": "", "status": "Warning", "status_code": 14},
                 {"node_id": 2, "name": "dc02", "dns": "", "ip": "", "status": "Up", "status_code": 1}]
        rows = ad._dc_rows([{"dc": "DC01"}, {"dc": "DC02"}], ad.match([{"dc": "DC01"}, {"dc": "DC02"}], nodes), {}, {},
                           {2: {"at": "2026-10-15 06:15", "minutes": 38, "ongoing": True}}, False)
        self.assertEqual(rows[0]["status"], "Critical")
        self.assertEqual((rows[1]["last_down"]["ongoing"], rows[1]["last_down"]["open"]), (False, True))
        v = af.ad_swis_view({"asked": True, "ok": True, "source": "db-snapshot", "read_at": "2026-10-15 06:43", "age_min": 10, "sam": False,
                             "events": True, "dcs": rows}, {}, {"now": NOW})
        r = dict((x[1], x) for x in v["section"]["rows"])
        self.assertEqual(v["section"]["cell_status"][0][2], "critical")
        self.assertIn("its end not in SolarWinds' events", r["DC02"][5])


class Orion1101(unittest.TestCase):
    """1.101.0: a snapshot of another SolarWinds is not this one's - SolarWinds itself is read (orion_key compared, never the URL)."""

    def test_1_another_orion(self):
        s = Snap()
        base = s.latest

        def latest(conn):
            r = base(conn)
            r["orion"] = "orion-b.corp.example.mil:17774"
            return r
        s.latest = latest
        r = ad.read(DCS, {"now": NOW, "orion": "https://orion-a.corp.example.mil"}, conn=Conn([]), swdb=s)
        self.assertFalse(r["ok"])
        self.assertIn("another SolarWinds", " ".join(r["why"]))
        r = ad.read(DCS, {"now": NOW, "orion": "https://orion-b.corp.example.mil/"}, conn=Conn([]), swdb=s)
        self.assertEqual(r["source"], "db-snapshot")                                # the same Orion: host:port, not the URL


class KnownD1012(unittest.TestCase):
    def test_all_dcs_are_matched_in_snapshot_and_live_reads(self):
        from unittest.mock import Mock
        dcs = [{"dc": "DC01", "domain": "ad.example.mil"}, {"dc": "DC02", "domain": "ad.example.mil"}]
        client = Mock()
        client.paged.return_value = ([{"NodeID": 11, "Caption": "dc01.example.mil", "Status": 1},
                                     {"NodeID": 12, "Caption": "dc02.example.mil", "Status": 1}], False)
        client.query.return_value = []
        for r in [ad.from_db(dcs, {"now": NOW}, Conn([]), Snap())[0], ad.from_live(dcs, {"now": NOW}, client)]:
            self.assertTrue(r["ok"])
            self.assertEqual(len(r["dcs"]), 2)
            self.assertTrue(all(d["matched"] for d in r["dcs"]))

    def test_job_matches_discovered_and_polled_dcs(self):
        import yaml
        with open(os.path.join(ROOT, "roles", "ad_report", "tasks", "swis.yml")) as fh:
            tasks = yaml.safe_load(fh)
        params = next(t["site_ad_swis"] for t in tasks if "site_ad_swis" in t)
        env = jinja2.Environment()
        env.filters.update(af.FilterModule().filters())
        data = {"dcs": [{"dc": "DC01", "domain": "ad.example.mil"}],
                "repl": {"dcs": [{"name": "DC01", "domain": "ad.example.mil"}, {"name": "DC02", "domain": "ad.example.mil"}]}}
        got = yaml.safe_load(env.from_string(params["dcs"]).render(_ad=data, hostvars={}))
        self.assertEqual([d["dc"] for d in got], ["dc01", "dc02"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
