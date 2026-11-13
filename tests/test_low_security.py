#!/usr/bin/env python3
"""1.56.0: the Low level in the certificate, fapolicyd, trust store, STIG drift, ONTAP, availability and STIG Manager
reports - worth knowing, nothing to do now: always with the word "Low", never a Start here card, never the report's
status by itself; an explicit setting keeps its meaning."""
import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "plugins", "filter"))
sys.path.insert(0, os.path.join(ROOT, "roles", "check_stig", "library"))
sys.path.insert(0, os.path.join(ROOT, "tests", "stig"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load("ansible.module_utils.site_fapolicyd", os.path.join(ROOT, "plugins", "module_utils", "site_fapolicyd.py"))
import site_filters as f            # noqa: E402
import cert_filters as cf           # noqa: E402
import fapolicyd_filters as ff      # noqa: E402
import trust_filters as tf          # noqa: E402
import stig_filters as sf           # noqa: E402
import ontap_filters as of          # noqa: E402
import availability_filters as af   # noqa: E402
import stigman_health_filters as smh  # noqa: E402

FR = _load("test_fapolicyd_rules_builders", os.path.join(HERE, "test_fapolicyd_rules.py"))
FX = _load("test_fapolicyd_fix_builders", os.path.join(HERE, "test_fapolicyd_fix.py"))
ST = _load("test_stig_builders", os.path.join(HERE, "test_stig.py"))


class Certificates(unittest.TestCase):
    CERTS = [{"days": 20, "expires": "2026-12-03", "host": "web01.example.mil", "subject": "CN=web01", "where": "tcp 443"},
             {"days": 45, "expires": "2026-12-28", "host": "web02.example.mil", "subject": "CN=web02", "where": "tcp 443"},
             {"days": 200, "expires": "2027-06-01", "host": "web03.example.mil", "subject": "CN=web03", "where": "tcp 443"}]

    def test_notice_window_is_low_with_the_word(self):
        s = f.cert_report_section(self.CERTS, 30, 60)
        self.assertEqual(s["row_status"], ["warning", "low", ""])
        self.assertEqual(s["cell_status"][1][0], "low")
        self.assertEqual(s["rows"][1][5], "Low: expires within 60 days")
        self.assertIn("Low = within 60 days", s["text"])
        self.assertEqual(s["status"], "warning", "the warn window keeps its meaning")

    def test_low_alone_never_the_section_status(self):
        self.assertNotIn("status", f.cert_report_section(self.CERTS[1:], 30, 60))

    def test_inventory_keeps_notice_in_the_database(self):
        self.assertEqual(cf.status_of(45, 30, 7, 60), "notice")


class Fapolicyd(unittest.TestCase):
    BLOCK = {"kind": "denied", "exe": "/usr/bin/bash", "path": "/opt/app/tool", "perm": "execute", "count": 2, "last": "x",
             "rule_no": "13", "rule": "deny_audit perm=execute all : all", "where": "the host", "change": "trust file: ..."}

    def test_would_be_blocked_is_low(self):
        r = ff.fapolicyd_rules_job_report([{"_fr": FR.host(blocks=[self.BLOCK])}, {"_fr": FR.host(permissive=True, changes=[], blocks=[
            dict(self.BLOCK, kind="would deny", count=3)])}], ["a", "b"])
        s = {x["title"]: x for x in r["sections"]}["What fapolicyd blocked"]
        self.assertEqual(s["row_status"], ["warning", "low"])
        self.assertTrue(s["rows"][1][1].startswith("Low"))

    def test_journal_while_running_is_low_stopped_critical(self):
        ap = {"state": "rolled back", "active_after": "active", "error": "x", "applied": [], "not_here": [], "refused": [], "bad": [],
              "journal": ["db_max_size needs to be increased"]}
        r = ff.fapolicyd_fix_report([{"_ff": FX.host(active="active"), "_ff_apply": ap}], ["fa03"], check=False)
        j = {x["title"]: x for x in r["sections"]}["fa03: fapolicyd's journal"]
        self.assertEqual(j["status"], "low")
        self.assertTrue(j["text"].startswith("Low - "))
        r = ff.fapolicyd_fix_report([{"_ff": FX.host(active="failed", journal=["it failed"])}], ["fa04"], check=False)
        j = {x["title"]: x for x in r["sections"]}["fa04: fapolicyd's journal"]
        self.assertEqual(j["status"], "critical")
        self.assertFalse(j["text"].startswith("Low"))


class Trust(unittest.TestCase):
    SCAN = {"stores": [{"kind": "system", "where": "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem", "count": 140,
                        "hits": [{"name": "AAA Certificate Services", "severity": "notice"}], "blocked": []}],
            "software": [], "not_read": [], "counts": {}}

    def test_notice_shows_as_low(self):
        r = tf.trust_result(self.SCAN, "web01.example.mil")
        self.assertEqual((r["rows"][0][5], r["row_status"][0]), ("Low", "low"))
        self.assertEqual(tf.trust_findings(self.SCAN), [], "no finding, as before")
        secs = tf.trust_report_sections([r])
        four = [s for s in secs if s["title"].startswith("4. Listed only (Low)")][0]
        self.assertEqual((four["row_status"], four["status"]), (["low"], "ok"))
        self.assertEqual([(b["label"], b["status"]) for b in secs[0]["boxes"]][-1], ("Listed only (Low)", "low"))
        every = [s for s in secs if s["title"].startswith("Everything read")][0]
        self.assertEqual(every["status"], "ok", "Low never makes the section amber")

    def test_check_trust_severity_low_is_listed_only(self):
        scan = {"stores": [dict(self.SCAN["stores"][0], hits=[{"name": "emSign ECC Root CA - G3", "severity": "warning"}])],
                "software": [], "not_read": [], "counts": {}}
        self.assertEqual(len(tf.trust_findings(scan)), 1)
        self.assertEqual(tf.trust_findings(scan, {"emSign ECC Root CA - G3": "low"}), [])
        self.assertEqual(tf.trust_result(scan, "h", {"emSign ECC Root CA - G3": "Low"})["row_status"], ["low"])


class Stig(unittest.TestCase):
    def compare(self, **kw):
        return sf.stig_compare(ST.scanned({"ensure_gpgcheck_local_packages": "fail"}, before={}), ST.sm(SV_257821=("pass", "accepted")),
                               "web01", stigman_used=True, **kw)

    def test_fixed_still_open_is_low(self):
        a = sf.stig_compare(ST.scanned({"ensure_gpgcheck_local_packages": "fail", "accounts_tmout": "fail", "banner_etc_issue": "fail",
                                        "sshd_disable_root_login": "fail"}, ST.ALL_PASS,
                                       before=dict((k, "t") for k in ST.ALL_PASS if k != "SV-257779")),
                            ST.sm(SV_257821=("pass", "accepted"), SV_257779=("pass", "accepted"), SV_258176=("fail", "saved")), "web01",
                            stigman_used=True)
        s = {x["title"].split(" - ")[0]: x for x in sf.stig_report_sections([a])}
        fo = s["4. Fixed on the host, still Open in STIG Manager"]
        self.assertEqual((set(fo["row_status"]), fo["status"]), ({"low"}, "low"))
        self.assertTrue(fo["text"].startswith("Low - "))
        box = [b for b in s["1. Drifted"]["boxes"] if b["label"] == "Fixed, still Open"][0]
        self.assertEqual(box["status"], "low")
        self.assertIn("Low", box["note"])

    def test_disagree_severity_low(self):
        c = self.compare(disagree_severity="low")
        self.assertEqual([(x["id"], x["severity"]) for x in c["findings"]], [("stig:disagree", "low")])
        self.assertEqual(c["row"]["status"], "ok", "a Low finding alone leaves the host ok")
        s = sf.stig_report_sections([c])
        dis = [x for x in s if x["title"].startswith("3. ")][0]
        self.assertEqual((set(dis["row_status"]), dis["status"]), ({"low"}, "low"))
        self.assertIn("Low findings", dis["text"])
        rep = f.findings_report({"web01": c["findings"]}, "STIG drift")
        self.assertEqual(rep["status"], "ok")
        self.assertEqual(rep["sections"][0]["title"] if rep["sections"] else "", "Findings", "no Start here card for Low")

    def test_none_and_warning_unchanged(self):
        self.assertEqual(self.compare()["findings"], [])
        self.assertEqual([x["severity"] for x in self.compare(disagree_severity="warning")["findings"]], ["warning"])
        self.assertEqual(self.compare(disagree_severity="warning")["row"]["status"], "warning")


class Ontap(unittest.TestCase):
    @staticmethod
    def report(**data):
        base = {"cluster": {"records": [{"name": "c1"}]}}
        base.update({k: {"records": v} for k, v in data.items()})
        return of.ontap_report({"read_at": "2026-10-07T12:00:00Z", "clusters": [{"address": "c1.example.mil", "reachable": True, "data": base}]})

    @staticmethod
    def section(r, title):
        return [x for x in r["report"]["sections"] if x["title"].startswith(title)][0]

    def test_information_alert_is_low(self):
        r = self.report(alerts=[{"perceived_severity": "information", "node": "c1-01", "alert_id": "A1", "subsystem": "cifs"},
                                {"perceived_severity": "minor", "node": "c1-01", "alert_id": "A2", "subsystem": "cifs"}])
        s = self.section(r, "Health alerts")
        self.assertEqual(s["row_status"], ["warning", "low"])
        self.assertEqual(s["rows"][1][2], "INFORMATION (Low)")
        self.assertEqual([p["severity"] for p in r["problems"] if p["area"] == "health alert"], ["warning"], "Low is no problem")

    def test_battery_partially_discharged_is_low(self):
        r = self.report(nodes=[{"name": "c1-01", "state": "up", "nvram": {"battery_state": "battery_partially_discharged"},
                                "service_processor": {"state": "online"}}])
        s = self.section(r, "Nodes")
        self.assertEqual(s["cell_status"][0][8], "low")
        self.assertEqual(s["rows"][0][8], "partially discharged (Low)")
        self.assertFalse([p for p in r["problems"] if "battery" in p["text"]])
        r = self.report(nodes=[{"name": "c1-01", "state": "up", "nvram": {"battery_state": "battery_near_end_of_life"},
                                "service_processor": {"state": "online"}}])
        self.assertEqual([p["severity"] for p in r["problems"] if "battery" in p["text"]], ["warning"], "near end of life stays")

    def test_unassigned_disk_is_low(self):
        r = self.report(disks=[{"name": "1.0.1", "container_type": "unassigned", "state": "present", "node": {"name": "c1-01"}}])
        s = self.section(r, "Disks")
        self.assertEqual(s["row_status"], ["low"])
        self.assertEqual(s["rows"][0][3], "unassigned (Low)")


class SeveritySettings(unittest.TestCase):
    def test_availability_node_down_low(self):
        probes = [{"name": "web", "group": "service", "good": 3, "attempts": [{}, {}, {}], "ms_median": 50},
                  {"name": "node1", "group": "node", "good": 0, "attempts": [{}, {}, {}], "why": "refused"}]
        j = af.availability_judge(probes, {"node_down": "low"})
        self.assertEqual((j["status"], [x["severity"] for x in j["findings"]]), ("up", ["low"]))
        self.assertEqual(af.availability_judge(probes, {})["status"], "degraded", "the default keeps its meaning")
        sec = af.availability_sections(probes, None, {"node_down": "low"})[0]
        self.assertEqual((sec["row_status"][1], sec["rows"][1][3]), ("low", "DOWN (Low)"))
        self.assertNotIn("status", sec)

    def test_stigman_node_down_low(self):
        t = [{"name": "node1", "kind": "node", "url": "https://192.0.2.10", "state": {"status": 0, "why": "refused"}}]
        rows = smh.stigman_health_sections({}, t, {"node_down": "low"})[0]
        self.assertEqual(rows["row_status"], ["low"])
        self.assertIn("(Low)", rows["rows"][0][3])
        self.assertEqual([x["severity"] for x in smh.stigman_health_findings({}, t, {"node_down": "low"})
                          if x["id"].endswith("node1")][:1], ["low"])


if __name__ == "__main__":
    unittest.main()
