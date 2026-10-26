#!/usr/bin/env python3
"""Unit tests for the sso check's findings (plugins/filter/sso_filters.py): what the read prints -> findings; and for
vCenter's single sign-on (roles/vmware_vm/library/site_vcenter_sso.py's pure parts, plugins/filter/vcenter_sso_filters.py).

    python tests/test_sso.py
"""
import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "filter"))
sys.path.insert(0, os.path.join(ROOT, "roles", "vmware_vm", "library"))
_spec = importlib.util.spec_from_file_location("ansible.module_utils.site_vcenters",
                                               os.path.join(ROOT, "roles", "vmware_vm", "module_utils", "site_vcenters.py"))
sys.modules["ansible.module_utils.site_vcenters"] = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sys.modules["ansible.module_utils.site_vcenters"])
import sso_filters as sf             # noqa: E402
import site_vcenter_sso as vs        # noqa: E402
import vcenter_sso_filters as vf     # noqa: E402

OK = ["sssd_conf yes", "sssd active", "winbind_conf no", "realm example.mil|active-directory|sssd|kerberos-member",
      "domain example.mil|online|dc01.example.mil|", "offline_events 0", "kdc_events 0", "keytab exists|WEB01$@EXAMPLE.MIL",
      "kinit ok|", "testjoin example.mil|ok|", "user svc|ok"]


def ids(lines, **kw):
    return [(f["id"], f["severity"]) for f in sf.sso_findings(lines, **kw)["findings"]]


def swap(prefix, new):
    return [new if l.startswith(prefix) else l for l in OK]


class Sso(unittest.TestCase):
    def test_not_joined(self):
        r = sf.sso_findings(["sssd_conf no", "winbind_conf no", "offline_events 4", "keytab missing|"])
        self.assertFalse(r["joined"])
        self.assertEqual(r["findings"], [])

    def test_healthy(self):
        self.assertEqual(ids(OK), [])
        self.assertIn("sssd domain example.mil: online via dc01.example.mil", sf.sso_findings(OK)["lines"])

    def test_each_problem(self):
        self.assertEqual(ids(swap("sssd ", "sssd failed")), [("sso:sssd-down", "critical")])
        self.assertEqual(ids(swap("domain ", "domain example.mil|offline||")), [("sso:offline:example.mil", "critical")])
        self.assertEqual(ids(swap("kinit", "kinit fail|Client not found")), [("sso:keytab", "critical")])
        self.assertEqual(ids(swap("testjoin", "testjoin example.mil|fail|gone")), [("sso:testjoin:example.mil", "critical")])
        self.assertEqual(ids(swap("user", "user svc|fail")), [("sso:user:svc", "critical")])
        self.assertEqual(ids(swap("keytab", "keytab missing|")), [("sso:keytab", "critical")])

    def test_log_counts(self):
        self.assertEqual(ids(swap("offline_events", "offline_events 2")), [("sso:went-offline", "warning")])
        self.assertEqual(ids(swap("offline_events", "offline_events 2"), offline_warn=3), [])
        self.assertEqual(ids(swap("kdc_events", "kdc_events 3")), [("sso:kdc", "warning")])
        both = [l for l in swap("domain ", "domain example.mil|offline||") if not l.startswith("offline_events")] + ["offline_events 5"]
        self.assertEqual(ids(both), [("sso:offline:example.mil", "critical")], "offline now: no extra went-offline warning")

    def test_winbind(self):
        self.assertEqual(ids(["sssd_conf no", "winbind_conf yes", "winbind inactive", "offline_events 0", "kdc_events 0"]),
                         [("sso:winbind-down", "critical")])


VC = {"name": "vc01.example.mil", "error": "", "notes": [], "providers": [{"name": "example.mil", "type": "ActiveDirectoryOverLdap", "default": True}],
      "services": [{"name": "sts", "found": True, "state": "STARTED"}, {"name": "vmdird", "found": False, "state": ""}],
      "sts": [{"subject": "CN=ssoserverSign", "expires": "2027-08-01", "days": 300}], "login": {"user": "svc@example.mil", "ok": True}}


def sso(**kw):
    v = dict(VC, **kw.pop("vc", {}))
    return {"vcenters": [v], "ocsp": kw.get("ocsp", []), "crl": kw.get("crl", [])}


class VcenterSso(unittest.TestCase):
    def test_service_states_from_both_lists(self):
        got = vs.service_states({"sts": {"state": "STARTED", "health": "HEALTHY"}}, {"vmdird": {"state": "STOPPED"}}, ["sts", "vmdird", "nope"])
        self.assertEqual([(x["name"], x["found"], x["state"]) for x in got], [("sts", True, "STARTED"), ("vmdird", True, "STOPPED"), ("nope", False, "")])
        self.assertEqual(vs.service_states([{"key": "sts", "value": {"state": "STARTED"}}], {}, ["sts"])[0]["state"], "STARTED")

    def test_pems_anywhere_in_the_answer(self):
        pem = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"
        self.assertEqual(len(vs.pems_in({"active_cert_chain": {"cert_chain": [pem, pem]}})), 2)

    def test_all_well(self):
        self.assertEqual(vf.vcenter_sso_problems(sso()), [])
        self.assertEqual(vf.vcenter_sso_conditions(sso()), [])

    def test_down_now_in_both(self):
        s = sso(vc={"services": [{"name": "sts", "found": True, "state": "STOPPED"}], "login": {"user": "svc", "ok": False, "why": "HTTP 401"}})
        self.assertEqual(sorted(c["kind"] for c in vf.vcenter_sso_conditions(s)), ["sso-login", "sso-service"])
        self.assertEqual(len(vf.vcenter_sso_problems(s)), 2)

    def test_sts_expiry_in_the_report_only(self):
        for days, sev in ((-1, "critical"), (10, "critical"), (40, "warning"), (61, None)):
            s = sso(vc={"sts": [{"subject": "CN=s", "expires": "x", "days": days}]})
            self.assertEqual([p["severity"] for p in vf.vcenter_sso_problems(s)], [sev] if sev else [], days)
            self.assertEqual(vf.vcenter_sso_conditions(s), [])

    def test_ocsp_filed_under_the_first_vcenter(self):
        s = sso(ocsp=[{"url": "http://ocsp.example.mil", "ok": False, "why": "no answer"}])
        c = vf.vcenter_sso_conditions(s)[0]
        self.assertEqual((c["vcenter"], c["object_type"], c["severity"]), ("vc01.example.mil", "OCSP responder", "critical"))
        self.assertEqual(vf.vcenter_sso_problems(s, {"ocsp_severity": "warning"})[0]["severity"], "warning")

    def test_crl_in_the_report_only_a_warning(self):
        bad = {"url": "http://crl.example.mil/ca.crl", "ok": False, "why": "the CRL is stale: its next update (2026-10-01 00:00 UTC) has passed",
               "entries": 120000, "size": 31457280, "next_update": "2026-10-01 00:00 UTC", "ms": 4200}
        s = sso(crl=[bad])
        p = vf.vcenter_sso_problems(s)
        self.assertEqual([(x["severity"], x["object"]) for x in p], [("warning", "CRL http://crl.example.mil/ca.crl")])
        self.assertIn("the CRL is stale", p[0]["problem"])
        self.assertEqual(vf.vcenter_sso_conditions(s), [])                                    # the watch: never
        self.assertEqual(vf.vcenter_sso_problems(s, {"crl_severity": "critical"})[0]["severity"], "critical")
        good = dict(bad, ok=True, why="", next_update="2026-11-01 00:00 UTC")
        sec = vf.vcenter_sso_doc({"title": "T", "sections": []}, sso(crl=[good]))["sections"][0]
        row = [r for r in sec["rows"] if r[1].startswith("CRL ")][0]
        self.assertEqual(row[0], "OK")
        self.assertEqual(row[2], "120000 certificate(s) listed, 30.0 MB, next update 2026-11-01 00:00 UTC; the test certificate is not on it")

    def test_doc_section_title_status(self):
        doc = {"title": "VMware health report: healthy - 1 host(s)", "status": "ok", "sections": [{"title": "Needs attention"}, {"title": "Datacenters"}]}
        d = vf.vcenter_sso_doc(doc, sso(vc={"login": {"user": "svc", "ok": False, "why": "x"}}))
        self.assertEqual([x["title"] for x in d["sections"]], ["Needs attention", "Single sign-on and OCSP", "Datacenters"])
        self.assertEqual(d["status"], "critical")
        self.assertTrue(d["title"].endswith("single sign-on: 1 critical"))
        self.assertEqual(vf.vcenter_sso_doc(doc, sso())["title"], doc["title"], "no problem: the title stays")


if __name__ == "__main__":
    unittest.main(verbosity=1)
