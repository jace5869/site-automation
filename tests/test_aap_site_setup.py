#!/usr/bin/env python3
"""AAP - site setup (1.112.2, playbooks/aap_site_setup.yml): one job, one paste of Variables - it imports AAP - set up job
templates and then AAP - incident document surveys, nothing else; its catalog entry (aap/job_templates.yml) and its
Variables file (the lines to fill in first, placeholders only); and a Check of it against the stand-in AAP of
tests/portal_setup/fake_aap_crud.py: both parts run, nothing is written.
Needs ansible-core: python3 tests/test_aap_site_setup.py   (ANSIBLE_PLAYBOOK=... for another ansible-core)"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
PLAYBOOK = os.path.join(REPO, "playbooks", "aap_site_setup.yml")
NAME = "AAP - site setup"


def catalog_entry():
    with open(os.path.join(REPO, "aap", "job_templates.yml"), encoding="utf-8") as fh:
        cat = yaml.safe_load(fh)
    return [t for t in cat["job_templates"] if t["name"] == NAME]


class Playbook(unittest.TestCase):
    def test_imports_the_two_in_order(self):
        with open(PLAYBOOK, encoding="utf-8") as fh:
            plays = yaml.safe_load(fh)
        self.assertEqual([p.get("ansible.builtin.import_playbook") for p in plays],
                         ["aap_templates_setup.yml", "incident_docs_survey.yml"])
        self.assertEqual([sorted(p) for p in plays], [["ansible.builtin.import_playbook", "name"]] * 2)   # nothing else

    def test_catalog_entry(self):
        found = catalog_entry()
        self.assertEqual(len(found), 1)
        e = found[0]
        self.assertEqual((e["playbook"], e["credentials"], e["optional_credentials"]), ("playbooks/aap_site_setup.yml", ["aap"], []))
        self.assertEqual((e["ask_limit"], e["ask_job_type"]), (False, True))
        self.assertEqual(e["variables_file"], "aap/extra_vars/aap_site_setup.yml")
        self.assertEqual(sorted(e["description"]), ["does", "produces", "supports", "targets"])
        with open(os.path.join(REPO, "docs", "JOB_TEMPLATES.md"), encoding="utf-8") as fh:
            self.assertIn("| %s | `playbooks/aap_site_setup.yml` |" % NAME, fh.read())
        with open(os.path.join(REPO, "docs", "SETUP_AAP.md"), encoding="utf-8") as fh:
            self.assertIn("### One job for the setup (1.112.2)", fh.read())

    def test_the_variables_to_fill_in(self):
        with open(os.path.join(REPO, "aap", "extra_vars", "aap_site_setup.yml"), encoding="utf-8") as fh:
            v = yaml.safe_load(fh)
        self.assertEqual(list(v), ["aap_templates_credentials", "aap_templates_match", "incident_docs_draft_credential",
                                   "aap_templates_organization", "aap_templates_project", "aap_templates_inventory"])
        self.assertEqual(v["aap_templates_match"], ["credentials"])
        creds = v["aap_templates_credentials"]
        for kind in ("report_db", "report_db_reader", "solarwinds", "act_model_key", "aap", "machine", "machine_windows"):
            self.assertIn(kind, creds)
        self.assertEqual(creds["solarwinds"], "<your SolarWinds (read-only) credential>")
        for k, val in list(creds.items()) + [(k, v[k]) for k in v if isinstance(v[k], str)]:
            self.assertTrue(val.startswith("<your ") and val.endswith(">"), (k, val))       # placeholders only
        # every kind the catalog names has its line
        with open(os.path.join(REPO, "aap", "job_templates.yml"), encoding="utf-8") as fh:
            cat = yaml.safe_load(fh)
        kinds = set(k for t in cat["job_templates"] for k in t["credentials"] + t["optional_credentials"])
        self.assertEqual(kinds - set(creds), set())


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class CheckAgainstStandIn(unittest.TestCase):
    """Check: AAP - set up job templates says what it would make, the surveys part what it would add; no write."""

    def setUp(self):
        self.work = tempfile.mkdtemp()
        state = {"next": 1000, "organizations": [{"id": 1, "name": "Default"}],
                 "projects": [{"id": 2, "name": "site-automation", "organization": 1}],
                 "inventories": [{"id": 3, "name": "Site hosts", "organization": 1}],
                 "execution_environments": [{"id": 5, "name": "All-in-one EE"}],
                 "credentials": [{"id": 12, "name": "AAP - jobs", "organization": 1, "credential_type": 17, "inputs": {},
                                  "summary_fields": {"credential_type": {"id": 17, "name": "Red Hat Ansible Automation Platform"}}}],
                 "job_templates": [{"id": 100, "name": "Outage AAR - Word document", "organization": 1, "project": 2,
                                    "playbook": "playbooks/incident_aar.yml", "inventory": 3, "ask_limit_on_launch": False,
                                    "ask_variables_on_launch": False, "ask_inventory_on_launch": False, "ask_job_type_on_launch": False,
                                    "execution_environment": 5, "credentials": [], "labels": [], "survey_enabled": False}],
                 "workflow_job_templates": [], "nodes": [], "approvals": [], "schedules": [], "roles": [], "users": [], "labels": []}
        self.state = os.path.join(self.work, "state.json")
        self.log = os.path.join(self.work, "writes.jsonl")
        with open(self.state, "w") as fh:
            json.dump(state, fh)
        self.port = free_port()
        env = dict(os.environ, FAKE_AAP_STATE=self.state, FAKE_AAP_LOG=self.log, FAKE_AAP_TOKEN="setup-token")
        self.fake = subprocess.Popen([sys.executable, os.path.join(HERE, "portal_setup", "fake_aap_crud.py"), str(self.port)],
                                     env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)

    def tearDown(self):
        self.fake.kill()
        self.fake.wait()
        for name in ("state.json", "writes.jsonl"):
            p = os.path.join(self.work, name)
            if os.path.exists(p):
                os.remove(p)
        os.rmdir(self.work)

    def check(self, *extra):
        ap = os.environ.get("ANSIBLE_PLAYBOOK", "ansible-playbook")
        env = dict(os.environ, CONTROLLER_HOST="http://127.0.0.1:%d" % self.port, CONTROLLER_OAUTH_TOKEN="setup-token",
                   ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="False", ANSIBLE_INVENTORY_UNPARSED_WARNING="False")
        args = [ap, "playbooks/aap_site_setup.yml", "-i", "localhost,", "--check"]
        for e in extra:
            args += ["-e", e]
        return subprocess.run(args, cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=600)

    def test_the_variables_file_as_pasted(self):
        """aap/extra_vars/aap_site_setup.yml with its placeholders left: Check stops in part 1 and names every credential
        AAP does not have (fill in or delete those lines); part 2 not run; then part 2 stops on the draft credential's
        placeholder; never a write."""
        fill = json.dumps({"ansible_python_interpreter": sys.executable, "aap_templates_organization": "Default",
                           "aap_templates_project": "site-automation", "aap_templates_inventory": "Site hosts",
                           "aap_templates_only": ["Outage AAR - Word document", "SOP - Word document"]})
        out = self.check("@aap/extra_vars/aap_site_setup.yml", fill,
                         json.dumps({"aap_templates_credentials": dict(self.pasted_credentials(), aap="AAP - jobs")}))
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("credential solarwinds (aap_templates_credentials) '<your SolarWinds (read-only) credential>': not found", out.stdout)
        self.assertNotIn("PLAY [AAP - incident document surveys]", out.stdout)
        self.assertFalse(os.path.exists(self.log) and open(self.log).read().strip(), "Check wrote to AAP")
        # the kinds filled in (here only aap), the draft credential's placeholder left: part 2 says so
        out = self.check("@aap/extra_vars/aap_site_setup.yml", fill, json.dumps({"aap_templates_credentials": {"aap": "AAP - jobs"}}))
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("PLAY [AAP - incident document surveys]", out.stdout)
        self.assertIn("(incident_docs_draft_credential): not there", out.stdout)
        self.assertFalse(os.path.exists(self.log) and open(self.log).read().strip(), "Check wrote to AAP")

    def pasted_credentials(self):
        with open(os.path.join(REPO, "aap", "extra_vars", "aap_site_setup.yml"), encoding="utf-8") as fh:
            return yaml.safe_load(fh)["aap_templates_credentials"]

    def test_check_runs_both_and_writes_nothing(self):
        extra = json.dumps({"ansible_python_interpreter": sys.executable, "aap_templates_organization": "Default",
                            "aap_templates_project": "site-automation", "aap_templates_inventory": "Site hosts",
                            "aap_templates_credentials": {"aap": "AAP - jobs"}, "aap_templates_match": ["credentials"],
                            "aap_templates_only": ["Outage AAR - Word document", "SOP - Word document"]})
        out = self.check(extra)
        self.assertEqual(out.returncode, 0, out.stdout[-4000:] + out.stderr[-2000:])
        text = out.stdout
        self.assertIn("PLAY [AAP - set up job templates]", text)
        self.assertIn("PLAY [AAP - incident document surveys]", text)
        self.assertLess(text.index("PLAY [AAP - set up job templates]"), text.index("PLAY [AAP - incident document surveys]"))
        self.assertIn("WOULD CREATE - 'SOP - Word document'", text)
        self.assertIn("job template 100 'Outage AAR - Word document': would be: added", text)
        self.assertIn("no job template runs playbooks/incident_sop.yml", text)       # made by part 1 on a Run, not on Check
        self.assertFalse(os.path.exists(self.log) and open(self.log).read().strip(), "Check wrote to AAP")


if __name__ == "__main__":
    unittest.main()
