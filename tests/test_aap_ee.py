#!/usr/bin/env python3
"""Tests for "AAP - set execution environment" (playbooks/aap_ee.yml, roles/aap_ee/library/site_aap_ee.py): which
templates are picked, in memory; the module against a fake AAP in this process (execution environments - two with one
name -, job templates over several pages, one with no execution environment set, one already on it, one the user may not
change, one of another project); and the playbook itself against that fake.
Needs ansible-core: python3 tests/test_aap_ee.py"""
import copy
import importlib.util
import json
import os
import subprocess
import sys
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(REPO, "roles", "aap_ee", "library"))
_spec = importlib.util.spec_from_file_location("ansible.module_utils.site_aap", os.path.join(REPO, "plugins", "module_utils", "site_aap.py"))
_aap = importlib.util.module_from_spec(_spec)
sys.modules["ansible.module_utils.site_aap"] = _aap
_spec.loader.exec_module(_aap)
import site_aap_ee as m  # noqa: E402


def _ee(i, name, image, org=None, pull="missing"):
    return {"id": i, "name": name, "image": image, "pull": pull, "organization": 1 if org else None,
            "summary_fields": {"organization": {"name": org}} if org else {}}


def _jt(i, name, ee, eename=None, project="Site automation", edit=True):
    sf = {"project": {"name": project}, "user_capabilities": {"edit": edit}}
    if ee:
        sf["execution_environment"] = {"id": ee, "name": eename}
    return {"id": i, "name": name, "execution_environment": ee, "summary_fields": sf}


MIN = "Minimal execution environment"


class Pick(unittest.TestCase):
    JOBS = [_jt(11, "Health check", 1, MIN), _jt(13, "Network config backup", 8, "Network EE"), _jt(14, "Log analysis", None),
            _jt(17, "Other", 1, MIN, project="Other project")]

    def names(self, picked):
        return [(j["name"], why) for j, why in picked]

    def test_project_no_ee_set_is_listed_not_picked(self):
        self.assertEqual(self.names(m.pick(self.JOBS, "Site automation")),
                         [("Health check", "picked"), ("Network config backup", "picked"), ("Log analysis", "unset")])

    def test_from_any_project_and_none(self):
        self.assertEqual(self.names(m.pick(self.JOBS, "", {1})), [("Health check", "picked"), ("Other", "picked")])
        self.assertEqual(self.names(m.pick(self.JOBS, "Site automation", {1}, True)), [("Health check", "picked"), ("Log analysis", "picked")])

    def test_named_whatever_they_are_on_and_exclude(self):
        self.assertEqual(self.names(m.pick(self.JOBS, "", named_ids={14, 17})), [("Log analysis", "picked"), ("Other", "picked")])
        self.assertEqual(self.names(m.pick(self.JOBS, "Site automation", exclude_ids={13, 14})), [("Health check", "picked")])

    def test_nothing_named_nothing_picked(self):
        self.assertEqual(m.pick(self.JOBS), [])


class FakeAap(object):
    """Execution environments 1, 2, 7, 8, 9 (7 and 9 share a name); job templates 11-17 over three pages; writes logged."""

    def __init__(self):
        self.ees = [_ee(1, MIN, "registry.redhat.io/ansible-automation-platform-25/ee-minimal-rhel9:latest", pull="always"),
                    _ee(2, "Default execution environment", "registry.redhat.io/ansible-automation-platform-25/ee-supported-rhel9:latest"),
                    _ee(7, "All-in-one EE", "hub.example.mil/site-ee-all:1", org="Site"),
                    _ee(8, "Network EE", "hub.example.mil/site-ee-network:1", org="Site"),
                    _ee(9, "Old EE", "hub.example.mil/old:3", org="Site"),
                    _ee(10, "Old EE", "hub.example.mil/old:2")]
        self.jobs = [_jt(11, "Health check", 1, MIN), _jt(12, "VM - health report", 1, MIN),
                     _jt(13, "Network config backup", 8, "Network EE"), _jt(14, "Log analysis", None),
                     _jt(15, "Certificate report", 7, "All-in-one EE"), _jt(16, "Read only report", 1, MIN, edit=False),
                     _jt(17, "Other project job", 1, MIN, project="Other project"),
                     _jt(18, "Patch hosts", 2, "Default execution environment", project="Other project"),
                     _jt(19, "Patch hosts", 2, "Default execution environment", project="Other project")]
        self.writes = []
        self.page_size = 3
        self.refuse = set()
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj=None):
                data = json.dumps(obj if obj is not None else {}).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _do(self, method):
                n = int(self.headers.get("Content-Length", "0") or 0)
                body = json.loads(self.rfile.read(n) or b"null") if n else None
                url = urllib.parse.urlparse(self.path)
                qs = dict(urllib.parse.parse_qsl(url.query))
                base = "/api/controller/v2/"
                if not url.path.startswith(base):
                    return self._send(404, {"detail": "Not found."})
                if self.headers.get("Authorization") != "Bearer goodtoken":
                    return self._send(401, {"detail": "Authentication credentials were not provided."})
                rest = url.path[len(base):].strip("/").split("/")
                return self._send(*fake.answer(method, rest, qs, body, base + "/".join(rest) + "/"))

            def do_GET(self):
                self._do("GET")

            def do_PATCH(self):
                self._do("PATCH")

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d" % self.srv.server_port

    def page(self, items, qs, path):
        p = int(qs.get("page", "1"))
        chunk = items[(p - 1) * self.page_size:p * self.page_size]
        nxt = ("%s?page=%d&page_size=%s" % (path, p + 1, qs.get("page_size", "25"))) if p * self.page_size < len(items) else None
        return 200, {"count": len(items), "next": nxt, "results": copy.deepcopy(chunk)}

    def answer(self, method, rest, qs, body, path):
        if rest == ["me"]:
            return 200, {"results": [{"username": "admin"}]}
        if rest == ["execution_environments"] and method == "GET":
            return self.page(self.ees, qs, path)
        if rest[:1] != ["job_templates"]:
            return 404, {"detail": "Not found."}
        if len(rest) == 1 and method == "GET":
            return self.page(self.jobs, qs, path)
        jt = next((x for x in self.jobs if str(x["id"]) == rest[1]), None)
        if jt is None:
            return 404, {"detail": "Not found."}
        if method == "PATCH":
            if not jt["summary_fields"]["user_capabilities"]["edit"]:
                return 403, {"detail": "You do not have permission to perform this action."}
            if jt["id"] in self.refuse:
                return 400, {"execution_environment": ["You do not have permission to use this execution environment."]}
            ee = next(e for e in self.ees if e["id"] == body["execution_environment"])
            jt["execution_environment"] = ee["id"]
            jt["summary_fields"]["execution_environment"] = {"id": ee["id"], "name": ee["name"]}
            self.writes.append((jt["id"], body))
            return 200, jt
        return 200, copy.deepcopy(jt)

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def params(**kw):
    p = {"ee": "All-in-one EE", "project": "Site automation", "from_ee": [], "templates": [], "exclude": []}
    p.update(kw)
    return p


class AgainstFakeAap(unittest.TestCase):
    def setUp(self):
        self.aap = FakeAap()
        self.api = m.Api(m.connection({"CONTROLLER_HOST": self.aap.url, "CONTROLLER_OAUTH_TOKEN": "goodtoken"}), no_credential=m.NO_CREDENTIAL)
        self.assertEqual(self.api.probe(), "")

    def tearDown(self):
        self.aap.close()

    def by_name(self, results):
        return dict((r["template"], r) for r in results)

    def test_the_project(self):
        ee, results, problems = m.run(self.api, params(), False)
        self.assertEqual(problems, [])
        self.assertEqual(ee, {"id": 7, "name": "All-in-one EE", "image": "hub.example.mil/site-ee-all:1", "pull": "pull only if not present",
                              "organization": "Site"})
        r = self.by_name(results)
        self.assertEqual(sorted(r), ["Certificate report", "Health check", "Log analysis", "Network config backup", "Read only report",
                                     "VM - health report"])                  # not the other project's
        self.assertEqual(r["Health check"]["action"], MIN + " -> All-in-one EE")
        self.assertEqual(r["Network config backup"]["action"], "Network EE -> All-in-one EE")
        self.assertEqual(r["Certificate report"]["action"], "already on it")
        self.assertFalse(r["Certificate report"]["changed"])
        self.assertIn("left as it is - no execution environment set", r["Log analysis"]["action"])
        self.assertIsNone(self.aap.jobs[3]["execution_environment"])           # left as it is
        self.assertFalse(r["Read only report"]["ok"])
        self.assertIn("may not change this job template", r["Read only report"]["error"])
        self.assertEqual(sorted(w[0] for w in self.aap.writes), [11, 12, 13])
        self.assertEqual(self.aap.writes[0][1], {"execution_environment": 7})
        self.assertTrue(r["Health check"]["written"])
        _, again, _ = m.run(self.api, params(), False)
        self.assertEqual([x["template"] for x in again if x["changed"]], [])    # a second run changes nothing

    def test_check_mode_only_reads(self):
        _, results, _ = m.run(self.api, params(), True)
        self.assertEqual(self.aap.writes, [])
        self.assertTrue(self.by_name(results)["Health check"]["changed"])

    def test_from_none_exclude_and_named(self):
        _, results, problems = m.run(self.api, params(project="", from_ee=[MIN, "none"], exclude=["Read only report", "17"],
                                                      templates=["Network config backup"]), False)
        self.assertEqual(problems, [])
        self.assertEqual([x["template"] for x in results], ["Health check", "Log analysis", "Network config backup", "VM - health report"])
        self.assertEqual(self.by_name(results)["Log analysis"]["action"], "(none set: the project's or organization's default) -> All-in-one EE")
        self.assertEqual(sorted(w[0] for w in self.aap.writes), [11, 12, 13, 14])

    def test_by_id_and_two_with_one_name(self):
        ee, _, problems = m.run(self.api, params(ee="Old EE"), True)
        self.assertEqual((ee, len(problems)), ({}, 1))
        self.assertIn("2 execution environments are named Old EE (ID 9, Site, ID 10, every organization): name it by its ID", problems[0])
        ee, results, problems = m.run(self.api, params(ee="10", from_ee=["Network EE"]), True)
        self.assertEqual((ee["name"], problems), ("Old EE", []))
        self.assertEqual([x["template"] for x in results], ["Network config backup"])

    def test_what_is_not_there(self):
        _, _, problems = m.run(self.api, params(ee="Nope"), True)
        self.assertIn("aap_ee_name: no execution environment Nope that the AAP user can see", problems[0])
        _, _, problems = m.run(self.api, params(project="Nope project", from_ee=["Gone EE"], templates=["Nope template"]), True)
        self.assertEqual(problems, ["aap_ee_from: no execution environment Gone EE that the AAP user can see",
                                    "aap_ee_templates: no job template Nope template that the AAP user can see",
                                    "aap_ee_project: no job template of a project named Nope project that the AAP user can see"])

    def test_a_misspelled_exclude_changes_nothing(self):
        """an exclude that matches nothing must not let the template it meant to keep move: nothing is written"""
        _, results, problems = m.run(self.api, params(exclude=["Network config bakcup"]), False)
        self.assertEqual(problems, ["aap_ee_exclude: no job template Network config bakcup that the AAP user can see - check its name"])
        self.assertEqual(self.aap.writes, [])
        r = self.by_name(results)["Network config backup"]
        self.assertEqual((r["changed"], r["written"], r["action"]), (True, False, "Network EE -> All-in-one EE"))

    def test_two_templates_one_name(self):
        _, results, problems = m.run(self.api, params(project="", templates=["Patch hosts"]), False)
        self.assertEqual(problems, ["aap_ee_templates: 2 job templates are named Patch hosts (ID 18, every organization, ID 19, every "
                                    "organization): name it by its ID"])
        self.assertEqual((results, self.aap.writes), ([], []))
        _, results, problems = m.run(self.api, params(project="", templates=["19"], exclude=["Patch hosts"]), False)
        self.assertEqual((problems, results, self.aap.writes), ([], [], []))       # an exclude by a shared name: both left out
        _, results, problems = m.run(self.api, params(project="", templates=["19"]), False)
        self.assertEqual([(x["template"], x["id"], x["written"]) for x in results], [("Patch hosts", 19, True)])
        self.assertEqual(self.aap.writes, [(19, {"execution_environment": 7})])

    def test_never_every_template(self):
        _, results, problems = m.run(self.api, params(project=""), False)
        self.assertEqual(results, [])
        self.assertIn("never changes every template in AAP", problems[0])
        self.assertEqual(self.aap.writes, [])

    def test_aap_refuses(self):
        self.aap.refuse = {12}
        _, results, _ = m.run(self.api, params(), False)
        r = self.by_name(results)["VM - health report"]
        self.assertEqual((r["ok"], r["changed"]), (False, False))
        self.assertEqual(r["error"], "AAP refused the change (HTTP 400): execution_environment: You do not have permission to use this "
                                     "execution environment.")

    def test_no_credential(self):
        self.assertIn("Use on the execution environment", m.Api(m.connection({}), no_credential=m.NO_CREDENTIAL).probe())

    def test_the_playbook(self):
        """playbooks/aap_ee.yml against the fake: what it prints, Check reads only, and a failed template fails the job."""
        ap = os.environ.get("ANSIBLE_PLAYBOOK", "ansible-playbook")
        env = dict(os.environ, CONTROLLER_HOST=self.aap.url, CONTROLLER_OAUTH_TOKEN="goodtoken", ANSIBLE_NOCOLOR="1",
                   ANSIBLE_LOCALHOST_WARNING="False", ANSIBLE_INVENTORY_UNPARSED_WARNING="False")

        def run(extra, *a):
            return subprocess.run([ap, "playbooks/aap_ee.yml", "-i", "localhost,", "-e", json.dumps(dict(extra, ansible_python_interpreter=sys.executable))]
                                  + list(a), cwd=REPO, env=env, capture_output=True, text=True, timeout=300)
        out = run({"aap_ee_name": "All-in-one EE", "aap_ee_project": "Site automation"}, "--check")
        self.assertNotEqual(out.returncode, 0, out.stdout)                     # Read only report: cannot be changed
        self.assertIn("the execution environment: 'All-in-one EE' (ID 7, Site): image hub.example.mil/site-ee-all:1, pull only if not present",
                      out.stdout)
        self.assertIn("job template 11 'Health check' (Site automation): would be %s -> All-in-one EE" % MIN, out.stdout)
        self.assertIn("job template 15 'Certificate report' (Site automation): already on it", out.stdout)
        self.assertIn("job template 16 'Read only report' (Site automation): FAILED: the AAP user may not change", out.stdout)
        self.assertEqual(self.aap.writes, [])
        # a survey's text: names split at commas and line breaks
        out = run({"aap_ee_name": "7", "aap_ee_from": MIN + "\nnone", "aap_ee_exclude": "Read only report, Other project job"})
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("job template 14 'Log analysis' (Site automation): (none set: the project's or organization's default) -> All-in-one EE",
                      out.stdout)
        self.assertEqual(sorted(w[0] for w in self.aap.writes), [11, 12, 14])
        out = run({"aap_ee_name": "All-in-one EE"})
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("never changes every template in AAP", out.stdout)
        # a misspelled exclude, for real: nothing written, each change said as "would be", and the job fails on it
        before = len(self.aap.writes)
        out = run({"aap_ee_name": "Old EE", "aap_ee_from": "10, Network EE, " + MIN, "aap_ee_project": "Site automation",
                   "aap_ee_exclude": ["Netwrok config backup"]})
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("2 execution environments are named Old EE", out.stdout)
        self.assertEqual(len(self.aap.writes), before)
        out = run({"aap_ee_name": "10", "aap_ee_project": "Site automation", "aap_ee_exclude": ["Netwrok config backup"]})
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("job template 13 'Network config backup' (Site automation): would be Network EE -> Old EE", out.stdout)
        self.assertIn("aap_ee_exclude: no job template Netwrok config backup", out.stdout)
        self.assertEqual(len(self.aap.writes), before)


if __name__ == "__main__":
    unittest.main()
