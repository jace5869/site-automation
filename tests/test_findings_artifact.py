#!/usr/bin/env python3
"""The findings with stable ids (1.67.0 part C, plugins/filter/finding_filters.py) and roles/finding_ref: the job artifact
site_findings of the report jobs - a health check's findings (disk, services, fapolicyd and its denials, every other kind),
a STIG drift job's rules, the checklist import's new assets and fixed-on-host rules, the fapolicyd job's blocks - each id
<kind>:<host>:<key> of the allowed characters, the same every run; the caps; the buttons of the site portal's actions by
kind and data (when), as plain links; the actions file's kinds, button and when checked; and the finding read back by
reference against a stand-in AAP (site_finding_ref): every way it is refused.
  python3 tests/test_findings_artifact.py"""
import importlib.util
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
sys.path.insert(0, HERE)
import finding_filters as ff  # noqa: E402
import site_portal_filters as spf  # noqa: E402
import yaml  # noqa: E402

URL = "https://stigman.example.mil/portal"
ID = re.compile(r"^[A-Za-z0-9_.:/@-]{1,200}$")
HEALTH = [
    {"check": "disk", "id": "disk:/var", "severity": "critical", "summary": "/var is 93% full (critical at 90%), 1.2 GB free of 20 GB",
     "hint": "du -xh /var", "data": {"mount": "/var", "pct": 93, "warn": 85, "crit": 90, "size_b": 21474836480, "avail_b": 1288490188}},
    {"check": "disk", "id": "disk-inodes:/tmp", "severity": "warning", "summary": "/tmp has used 91% of its inodes (too many files; critical at 95%)"},
    {"check": "services", "id": "unit-failed:httpd.service", "severity": "critical", "summary": "systemd unit httpd.service has FAILED"},
    {"check": "services", "id": "unit-down:chronyd.service", "severity": "critical", "summary": "required service chronyd.service is inactive"},
    {"check": "fapolicyd", "id": "fapolicyd:denials", "severity": "warning", "summary": "7 fapolicyd denial(s) since today",
     "hint": "ausearch -m FANOTIFY -ts today -i | tail -40",
     "data": {"denials": ff.fapolicyd_denials_data(["/usr/bin/python3.11 -> /opt/app/run.py x5", "/opt/tool/bin/tool -> /etc/x x2"])}},
    {"check": "mounts", "id": "mount-full:/data", "severity": "warning", "summary": "/data is full"},
    {"check": "certs", "id": "cert:/etc/pki/tls/certs/web cert.pem", "severity": "warning", "summary": "expires in 20 days"},
]
WIN = [{"check": "disk", "id": "disk:C:", "severity": "warning", "summary": "C: is 88% full (warning at 85%), 4.1 GiB free"},
       {"check": "services", "id": "services:Spooler", "severity": "critical", "summary": "required service Spooler (Print Spooler) is Stopped"},
       {"check": "services", "id": "services:crash:W32Time", "severity": "warning", "summary": "service 'W32Time' stopped unexpectedly 3 times"},
       {"check": "eventlog", "id": "eventlog:errors", "severity": "warning", "summary": "41 errors"}]


def by_kind(entries):
    out = {}
    for e in entries:
        out.setdefault(e["kind"], []).append(e)
    return out


class Ids(unittest.TestCase):
    def test_the_form(self):
        self.assertEqual(ff.finding_id("disk_full", "web01.example.mil", "/var"), "disk_full:web01.example.mil:/var")
        odd = ff.finding_id("cert", "web01.example.mil", "/etc/pki/web cert.pem")
        self.assertRegex(odd, r"^cert:web01\.example\.mil:h[0-9a-f]{16}$")
        self.assertEqual(odd, ff.finding_id("cert", "web01.example.mil", "/etc/pki/web cert.pem"), "the same every run")
        self.assertRegex(ff.finding_id("x", "web01.example.mil", "a" * 500), r"^x:web01\.example\.mil:h[0-9a-f]{16}$")
        self.assertRegex(ff.finding_id("x", "bad host:name", "k"), r"^x:h[0-9a-f]{16}:k$")
        self.assertRegex(ff.finding_id("x", "h", "{{ lookup('pipe','id') }}"), r"^x:h:h[0-9a-f]{16}$", "no template survives in an id")


class Health(unittest.TestCase):
    def setUp(self):
        self.a = ff.findings_artifact(HEALTH, "web01.example.mil", "linux", 50)
        self.k = by_kind(self.a["entries"])

    def test_every_id_is_well_formed_and_stable(self):
        for e in self.a["entries"]:
            self.assertRegex(e["id"], ID)
            self.assertTrue(e["id"].startswith("%s:%s:" % (e["kind"], e["host"])))
            self.assertIn(e["severity"], ff.SEVERITIES)
        again = ff.findings_artifact(list(reversed(HEALTH)), "web01.example.mil", "linux", 50)
        self.assertEqual(sorted(e["id"] for e in again["entries"]), sorted(e["id"] for e in self.a["entries"]))

    def test_disk_inodes_services(self):
        d = self.k["disk_full"][0]
        self.assertEqual((d["id"], d["severity"]), ("disk_full:web01.example.mil:/var", "critical"))
        self.assertEqual(d["data"], {"look": "du -xh /var", "mount": "/var", "pct": 93, "warn": 85, "crit": 90, "size_b": 21474836480,
                                     "avail_b": 1288490188, "os": "linux"})
        self.assertEqual(self.k["disk_inodes"][0]["data"], {"mount": "/tmp", "pct": 91.0})
        sv = dict((e["data"]["service"], e["data"]["state"]) for e in self.k["service_down"])
        self.assertEqual(sv, {"httpd.service": "failed", "chronyd.service": "down"})

    def test_fapolicyd_and_its_denials(self):
        self.assertEqual(self.k["fapolicyd"][0]["id"], "fapolicyd:web01.example.mil:denials")
        den = self.k["fapolicyd_deny"]
        self.assertEqual(sorted((e["data"]["program"], e["data"]["path"], e["data"]["count"]) for e in den),
                         [("/opt/tool/bin/tool", "/etc/x", 2), ("/usr/bin/python3.11", "/opt/app/run.py", 5)])
        for e in den:
            self.assertRegex(e["id"], r"^fapolicyd_deny:web01\.example\.mil:h[0-9a-f]{16}$")
        # the fapolicyd job's block of the same program and file: the same id
        job = ff.fapolicyd_block_entries([{"kind": "denied", "exe": "/usr/bin/python3.11", "path": "/opt/app/run.py", "count": 9, "perm": "execute",
                                           "rule_no": 12, "where": "/opt"}], "web01.example.mil")
        self.assertIn(job["entries"][0]["id"], [e["id"] for e in den])
        self.assertEqual(job["entries"][0]["data"]["mode"], "denied")

    def test_every_other_kind_is_kept(self):
        self.assertEqual(self.k["mount_full"][0]["id"], "mount_full:web01.example.mil:/data")
        self.assertRegex(self.k["cert"][0]["id"], r"^cert:web01\.example\.mil:h[0-9a-f]{16}$")

    def test_windows(self):
        k = by_kind(ff.findings_artifact(WIN, "win01.example.mil", "windows", 50)["entries"])
        self.assertEqual(k["disk_full"][0]["data"], {"mount": "C:", "pct": 88.0, "os": "windows"})
        self.assertEqual(k["service_down"][0]["data"], {"service": "Spooler", "state": "stopped", "os": "windows"})
        self.assertEqual(k["service_crash"][0]["data"]["service"], "W32Time")
        self.assertEqual(k["eventlog"][0]["id"], "eventlog:win01.example.mil:errors")

    def test_caps(self):
        many = [{"check": "disk", "id": "disk:/m%02d" % i, "severity": "critical" if i % 2 else "warning", "summary": "x"} for i in range(60)]
        a = ff.findings_artifact(many, "web01.example.mil", "linux", 10)
        self.assertEqual(len(a["entries"]), 10)
        self.assertEqual(a["capped"], {"disk_full": 50})
        self.assertTrue(all(e["severity"] == "critical" for e in a["entries"]), "the worst first")
        long = ff.findings_artifact([dict(HEALTH[0], summary="x" * 900)], "h", "linux", 5)["entries"][0]
        self.assertEqual(len(long["title"]), ff.DATA_MAX)


class Stig(unittest.TestCase):
    def test_drift_rules(self):
        import test_stig as ts
        c = ts.sf.stig_compare(ts.scanned({"ensure_gpgcheck_local_packages": "fail", "accounts_tmout": "fail",
                                           "package_telnet-server_removed": "fail"}, ts.ALL_PASS),
                               ts.sm(SV_257821=("pass", "accepted"), SV_257883=("fail", "saved"), SV_257985=("fail", "accepted")),
                               "web01.example.mil", stigman_used=True)
        k = by_kind(ff.findings_artifact(c["findings"], "web01.example.mil", "linux", 50, c)["entries"])
        rules = dict((e["data"]["rule"], e["data"]) for e in k["stig_rule_fail"])
        self.assertEqual(sorted(rules), ["SV-257821", "SV-257835"])
        self.assertEqual(rules["SV-257821"]["group"], "drift")
        self.assertEqual(rules["SV-257821"]["cat"], "CAT I")
        self.assertEqual(rules["SV-257821"]["os"], "linux")
        self.assertEqual(k["stig_fixed_open"][0]["data"]["rule"], "SV-257985")
        self.assertEqual(k["stig_fixed_open"][0]["id"], "stig_fixed_open:web01.example.mil:SV-257985")
        self.assertNotIn("stig_no_asset", k)
        gone = ts.sf.stig_compare(ts.scanned({}, ts.ALL_PASS), {"status": "not_found", "why": "no asset web01.example.mil", "collection": "C"},
                                  "web01.example.mil", stigman_used=True)
        na = by_kind(ff.findings_artifact([], "web01.example.mil", "linux", 50, [gone])["entries"])["stig_no_asset"][0]
        self.assertEqual((na["id"], na["data"]["collection"]), ("stig_no_asset:web01.example.mil:C", "C"))

    def test_checklist_import(self):
        a = ff.ckl_findings_artifact([{"kind": "create", "collection": "SITEA - RHEL 9", "asset": "web03.example.mil", "benchmarks": ["RHEL_9_STIG"],
                                       "why": "its asset waits for the approval of this import"}],
                                     [{"host": "web01.example.mil", "collection": "SITEA - RHEL 9", "rule": "SV-257985", "cat": "CAT II",
                                       "title": "x", "asset": "web01.example.mil"}])
        self.assertEqual(sorted(a["hosts"]), ["web01.example.mil", "web03.example.mil"])
        na = a["hosts"]["web03.example.mil"][0]
        self.assertRegex(na["id"], r"^stig_no_asset:web03\.example\.mil:h[0-9a-f]{16}$")     # a collection with spaces: hashed
        self.assertEqual((na["data"]["stig"], na["data"]["os"]), ("RHEL_9_STIG", "linux"))
        self.assertEqual(a["hosts"]["web01.example.mil"][0]["id"], "stig_fixed_open:web01.example.mil:SV-257985")


ACTIONS = {
    "act_triage": {"title": "Ask ACT", "button": "Ask ACT", "kinds": ["fapolicyd", "fapolicyd_deny", "eventlog"]},
    "disk_cleanup": {"title": "Clean up", "button": "Clean up", "kinds": ["disk_full"], "when": {"os": "linux"}},
    "space_request": {"title": "Ask for space", "kinds": ["disk_full"]},
    "disk_more": {"title": "More one", "kinds": ["disk_full"]},
    "stig_fix_rule": {"title": "Fix rule", "button": "Fix rule", "kinds": ["stig_rule_fail"], "when": {"cat": ["CAT II", "CAT III"]}},
}


class Buttons(unittest.TestCase):
    def setUp(self):
        self.lin = by_kind(ff.findings_artifact(HEALTH, "web01.example.mil", "linux", 50)["entries"])

    def test_by_kind_and_when(self):
        b = ff.finding_buttons(self.lin["disk_full"][0], ACTIONS, URL, "4711")
        self.assertEqual([x["label"] for x in b], ["Clean up", "More one", "Ask for space"])
        self.assertEqual(b[0]["url"], URL + "/actions/disk_cleanup?finding_id=disk_full%3Aweb01.example.mil%3A%2Fvar&report_job=4711"
                                            "&target=web01.example.mil")
        win = ff.findings_artifact(WIN, "win01.example.mil", "windows", 50)["entries"][0]
        self.assertNotIn("Clean up", [x["label"] for x in ff.finding_buttons(win, ACTIONS, URL, "4711")], "when: {os: linux}")
        cat1 = {"id": "stig_rule_fail:h:SV-1", "kind": "stig_rule_fail", "host": "h", "data": {"cat": "CAT I"}}
        self.assertEqual(ff.finding_buttons(cat1, ACTIONS, URL, "4711"), [])
        self.assertEqual(len(ff.finding_buttons(dict(cat1, data={"cat": "CAT II"}), ACTIONS, URL, "4711")), 1)

    def test_none(self):
        e = self.lin["disk_full"][0]
        for base, job in (("", "4711"), (URL, ""), ("javascript:alert(1)", "4711"), (URL, "47x")):
            self.assertEqual(ff.finding_buttons(e, ACTIONS, base, job), [], (base, job))
        self.assertEqual(ff.finding_buttons(dict(e, id="bad id"), ACTIONS, URL, "4711"), [])
        self.assertEqual(ff.finding_buttons(self.lin["mount_full"][0], ACTIONS, URL, "4711"), [])

    def test_a_section_of_its_own(self):
        hosts = {"web01.example.mil": ff.findings_artifact(HEALTH, "web01.example.mil", "linux", 50)["entries"]}
        s = ff.findings_actions_section(hosts, ACTIONS, URL, "4711", kinds=["fapolicyd_deny"])
        self.assertEqual(s["columns"], ["Host", "Finding", "Severity", "Actions"])
        self.assertEqual(len(s["rows"]), 2)
        self.assertEqual(s["kinds"][-1], "buttons")
        self.assertIsNone(ff.findings_actions_section(hosts, ACTIONS, URL, "4711", kinds=["stig_rule_fail"]))
        self.assertIsNone(ff.findings_actions_section(hosts, ACTIONS, "", "4711"))


class ActionsFile(unittest.TestCase):
    def test_kinds_button_when_are_checked(self):
        ok = {"title": "T", "aap": {"type": "job", "name": "J"}}
        good, p = spf.check_actions({"x": dict(ok, kinds=["disk_full"], button="Clean up", when={"os": "linux", "cat": ["CAT II"]})})
        self.assertEqual(p, [])
        self.assertEqual((good["x"]["kinds"], good["x"]["button"]), (["disk_full"], "Clean up"))
        for bad, word in ((dict(ok, kinds="disk_full"), "kinds must be"), (dict(ok, kinds=["Disk Full"]), "kinds must be"),
                          (dict(ok, kinds=["d"], button="x" * 41), "button must be"), (dict(ok, kinds=["d"], button="{{ x }}"), "button must be"),
                          (dict(ok, kinds=["d"], when="os"), "when must be"), (dict(ok, when={"os": "linux"}), "when needs kinds")):
            _, p = spf.check_actions({"x": bad})
            self.assertIn(word, " ".join(p), bad)

    def test_the_shipped_file(self):
        doc = yaml.safe_load(open(os.path.join(REPO, "roles", "site_portal_stack", "files", "actions.yml")))
        acts, p = spf.check_actions(doc["actions"])
        self.assertEqual(p, [])
        self.assertEqual(acts["act_triage"]["kinds"], ["fapolicyd", "fapolicyd_deny", "eventlog"])
        for name, a in acts.items():               # an action that takes a finding takes its reference, and the host is the Limit
            if a["kinds"]:
                self.assertTrue({"target", "report_job", "finding_id"} <= set(a["vars"]), name)
                self.assertEqual(a["limit_from"], "target", name)


class FakeApi(object):
    user = "portal-reader"

    def __init__(self, jobs, probe=""):
        self.jobs, self.why = jobs, probe

    def probe(self):
        return self.why

    def call(self, method, path):
        m = re.match(r"jobs/(\d+)/$", path)
        if m and int(m.group(1)) in self.jobs:
            return 200, self.jobs[int(m.group(1))]
        return 404, {"detail": "Not found."}


def _module():
    import ansible.module_utils
    sys.path.insert(0, os.path.join(REPO, "plugins", "module_utils"))
    import site_aap
    sys.modules.setdefault("ansible.module_utils.site_aap", site_aap)
    ansible.module_utils.site_aap = site_aap
    spec = importlib.util.spec_from_file_location("site_finding_ref", os.path.join(REPO, "roles", "finding_ref", "library", "site_finding_ref.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FindingRef(unittest.TestCase):
    def setUp(self):
        self.m = _module()
        art = {"web01.example.mil": ff.findings_artifact(HEALTH, "web01.example.mil", "linux", 50)["entries"],
               "web03.example.mil": ff.findings_artifact(HEALTH[:1], "web03.example.mil", "linux", 50)["entries"]}
        self.api = FakeApi({4711: {"name": "Linux health check", "status": "failed", "finished": "2026-11-16T06:00:00Z",
                                   "artifacts": {"site_findings": art}},
                            5: {"name": "Patch", "artifacts": {}}})

    def test_read_back(self):
        f, job, why = self.m.find(self.api, 4711, "disk_full:web01.example.mil:/var", "web01.example.mil", ["disk_full"])
        self.assertEqual(why, "")
        self.assertEqual((f["kind"], f["data"]["mount"], f["data"]["pct"]), ("disk_full", "/var", 93))
        self.assertEqual(sorted(f), ["data", "host", "id", "kind", "severity", "title"])
        self.assertEqual(job, {"id": 4711, "name": "Linux health check", "finished": "2026-11-16T06:00:00Z", "status": "failed"})

    def test_every_refusal(self):
        f = lambda *a: self.m.find(self.api, *a)[2]   # noqa: E731
        self.assertIn("AAP job 99 cannot be read (HTTP 404)", f(99, "disk_full:web01.example.mil:/var", "web01.example.mil", []))
        self.assertIn("job 5 has no site_findings artifact", f(5, "disk_full:web01.example.mil:/var", "web01.example.mil", []))
        self.assertIn("job 4711 reported nothing for web02x.example.mil", f(4711, "disk_full:web02x.example.mil:/var", "web02x.example.mil", []))
        self.assertIn("has no finding disk_full:web01.example.mil:/gone", f(4711, "disk_full:web01.example.mil:/gone", "web01.example.mil", []))
        self.assertIn("has no finding disk_full:web03.example.mil:/var for web01.example.mil",       # another host's id, this host
                      f(4711, "disk_full:web03.example.mil:/var", "web01.example.mil", []))
        self.assertIn("is a disk_full finding; this job takes only service_down",
                      f(4711, "disk_full:web01.example.mil:/var", "web01.example.mil", ["service_down"]))
        self.assertIn("is not one", f(4711, "disk_full:web01.example.mil:/var; rm", "web01.example.mil", []))
        self.assertIn("is not one", f(4711, "x" * 201, "web01.example.mil", []))
        self.assertEqual(self.m.find(FakeApi({}, probe="no AAP connection"), 1, "a:b:c", "b", [])[2], "no AAP connection")

    def test_a_forged_artifact_entry(self):
        """An entry whose host or kind does not match its own id is refused, even when the id is listed."""
        bad = {"site_findings": {"web01.example.mil": [{"id": "disk_full:web01.example.mil:/var", "kind": "service_down",
                                                        "host": "web01.example.mil", "data": {}}]}}
        why = self.m.find(FakeApi({7: {"name": "x", "artifacts": bad}}), 7, "disk_full:web01.example.mil:/var", "web01.example.mil", [])[2]
        self.assertIn("is not web01.example.mil's", why)


class Wiring(unittest.TestCase):
    def test_the_report_jobs_publish_it(self):
        for path in ("roles/site_findings/tasks/report.yml", "playbooks/fapolicyd.yml", "roles/stigman_ckl_import/tasks/main.yml"):
            text = open(os.path.join(REPO, path)).read()
            self.assertIn("site_findings: ", text, path)
            self.assertIn("site_findings_capped", text, path)
        self.assertIn("check_stig_results", open(os.path.join(REPO, "roles/site_findings/tasks/report.yml")).read(), "Windows drift too")


if __name__ == "__main__":
    unittest.main(verbosity=1)
