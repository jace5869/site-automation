"""aap/job_templates.yml - the catalog "AAP - set up job templates" makes the job templates from - is the same as
docs/JOB_TEMPLATES.md (names and playbooks) and up to date with its generator, so the two cannot drift; and the credential
kinds read right from the wordings the Variables files use."""
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import gen_job_catalog as g  # noqa: E402


class Catalog(unittest.TestCase):
    def catalog(self):
        import yaml
        with open(os.path.join(ROOT, "aap", "job_templates.yml")) as f:
            return yaml.safe_load(f)["job_templates"]

    def test_up_to_date(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "gen_job_catalog.py"), "--check"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(r.returncode, 0, r.stdout.decode())

    def test_the_same_templates_as_the_docs(self):
        docs = [(n, "playbooks/" + p) for n, p, _ in g.doc_rows()]
        cat = [(e["name"], e["playbook"]) for e in self.catalog()]
        self.assertEqual(cat, docs)
        self.assertGreater(len(cat), 100)
        self.assertEqual(len(set(n for n, _ in cat)), len(cat))                    # each name once
        for _, pb in cat:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, pb)), pb)

    def test_each_entry_is_whole(self):
        kinds = set(k for _, k in g.KINDS)
        for e in self.catalog():
            self.assertIn(e["ee"], ("all", "windows", "network"), e["name"])
            self.assertIn(e["area"], g.AREAS, e["name"])
            self.assertTrue(set(e["credentials"] + e["optional_credentials"]) <= kinds, e["name"])
            self.assertFalse(set(e["credentials"]) & set(e["optional_credentials"]), e["name"])
            if e["variables_file"]:
                self.assertTrue(os.path.isfile(os.path.join(ROOT, e["variables_file"])), e["name"])
        names = [e["name"] for e in self.catalog()]
        self.assertIn("AAP - set up job templates", names)                       # it makes its own template too

    def test_the_credential_wordings(self):
        cases = {"Machine, MariaDB monitor (optional)": (["machine"], ["mariadb"]),
                 "STIG Manager API (optional)": ([], ["stigman_api"]),
                 "Red Hat Ansible Automation Platform (optional); STIG Manager API (only for Keycloak's address)": ([], ["aap", "stigman_api"]),
                 "Machine, STIG Manager API, ACT model key (with answer_keys_act)": (["machine", "stigman_api"], ["act_model_key"]),
                 "Machine (Machine (Windows) for Windows hosts), ACT model key (not with log_analysis_act: false)":
                     (["machine", "act_model_key"], ["machine_windows"]),
                 "Machine, ServiceNow API (for the work notes)": (["machine", "servicenow"], []),
                 "Machine (ACT model key with use_act)": (["machine"], ["act_model_key"]),
                 "Machine, STIG Manager API; Windows file share with check_stig_evaluate_answers": (["machine", "stigman_api"], ["windows_share"]),
                 "Machine for the Cisco devices (network_cli), Palo Alto PAN-OS API, Network config git repository (ServiceNow API: only "
                 "with net_config_tickets)": (["machine", "panos", "net_git"], ["servicenow"]),
                 "none (ACT model key for the ACT options)": ([], ["act_model_key"]),
                 "VMware vCenter (read-only + Global > Diagnostics), ACT model key": (["vmware", "act_model_key"], []),
                 "Machine (Keystore password, SQL Server (certificate inventory): optional)": (["machine"], ["keystore", "sqlserver"]),
                 "Machine (Windows) (SQL Server (certificate inventory): optional)": (["machine_windows"], ["sqlserver"]),
                 "ServiceNow API, Red Hat Ansible Automation Platform (optional)": (["servicenow"], ["aap"]),
                 "Machine and VMware vCenter (read-only), both": (["machine", "vmware"], []),
                 "Machine (and VMware vCenter with patch_snapshot)": (["machine"], ["vmware"]),
                 "STIG Manager API (read-only is enough; with stigman_ckl_import_auto the client that writes), Windows file share":
                     (["stigman_api", "windows_share"], [])}
        for text, (req, opt) in cases.items():
            got = g.kinds_of(text)
            self.assertEqual((sorted(got[0]), sorted(got[1])), (sorted(req), sorted(opt)), text)
        self.assertEqual(g.kinds_of("Machine, Red Hat Ansible Automation Platform", windows=True), (["machine_windows", "aap"], []))

    def test_the_launch_contract(self):
        # docs/SETUP_AAP.md's own section over what the credentials suggest (Codex)
        by = dict((e["name"], e) for e in self.catalog())
        self.assertFalse(by["ServiceNow health"]["ask_limit"])                 # "no Prompt on launch": it runs on the controller
        self.assertEqual(by["ServiceNow health"]["optional_credentials"], ["machine_windows", "mariadb"])     # one Machine
        self.assertEqual(by["ServiceNow health - nodes"]["optional_credentials"], ["mariadb", "machine"])
        for n in ("Patch hosts", "Apply approved ACT fix", "Database health - MariaDB / MySQL", "ServiceNow - test ticket"):
            self.assertTrue(by[n].get("ask_job_type"), n)
        self.assertFalse(by["Troubleshoot"].get("ask_job_type"))
        self.assertEqual([e["name"] for e in self.catalog() if e.get("portal_actions")],
                         ["Disk - space request", "Site portal - open ticket"])

    def test_areas_and_portal(self):
        by = dict((e["name"], e) for e in self.catalog())
        self.assertEqual(by["STIG drift report"]["area"], "STIG")
        self.assertEqual(by["Active Directory health report"]["area"], "Windows")         # not ACT
        self.assertEqual(by["VM - restart"]["area"], "VMware")
        self.assertEqual(by["ACT - triage finding"].get("made_by"), "AAP - set up portal actions")
        self.assertTrue(by["STIG recheck"].get("portal"))
        self.assertEqual(by["ServiceNow health - nodes"].get("extra_vars"), {"servicenow_health_part": "nodes"})


if __name__ == "__main__":
    unittest.main()
