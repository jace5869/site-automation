#!/usr/bin/env python3
"""STIG Manager stack (roles/stigman_stack): the refusal to change a deployed database password names the secret
and never prints a hash. The label read and the compare run with no_log; the refusal itself has no loop (a failed
loop prints its whole item) and reads only the names. With ansible-playbook on PATH, the three tasks also run
against a made-up label and the output is checked for the hashes
(ANSIBLE_PLAYBOOK=<2.16's ansible-playbook> for 2.16).
Run: python tests/test_stigman_stack.py"""
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest

import yaml

PLAYBOOK = os.environ.get("ANSIBLE_PLAYBOOK") or shutil.which("ansible-playbook")
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TASKS = os.path.join(REPO, "roles", "stigman_stack", "tasks", "main.yml")


def tasks():
    with open(TASKS, encoding="utf-8") as fh:
        return {t.get("name"): t for t in yaml.safe_load(fh) if isinstance(t, dict)}


class SecretsTest(unittest.TestCase):
    def test_no_hash_in_the_output(self):
        t = tasks()
        self.assertIs(t["Secrets | current state"].get("no_log"), True)        # the deployed hash, also at -v
        self.assertIs(t["Secrets | desired state"].get("no_log"), True)
        differ = t["Secrets | the deployed ones whose value in AAP differs"]
        self.assertIs(differ.get("no_log"), True)
        refuse = t["Secrets | refuse to silently change a deployed database password"]
        self.assertNotIn("loop", refuse)
        text = yaml.safe_dump(refuse)
        for word in ("item", "sha", "have", "_stigman_secrets "):
            self.assertNotIn(word, text.replace("_stigman_secrets_differ", ""), word)
        self.assertIn("_stigman_secrets_differ", refuse["ansible.builtin.fail"]["msg"])

    @unittest.skipUnless(PLAYBOOK, "ansible-playbook not on PATH")
    def test_the_refusal_runs(self):
        t = tasks()
        old, new = "Old-db-Pw-0001", "New-db-Pw-0002"
        have = hashlib.sha256(old.encode()).hexdigest()
        want = hashlib.sha256(new.encode()).hexdigest()
        # the label read, as podman would answer it: the root secret differs, the db one is not deployed yet
        state = {"results": [{"rc": 0, "stdout": have}, {"rc": 125, "stdout": ""}]}
        play = [{"hosts": "localhost", "gather_facts": False,
                 "vars": {"_stigman_secret_state": state, "stigman_mysql_root_password": new,
                          "stigman_db_password": "Db-Pw-0003"},
                 "tasks": [t["Secrets | desired state"], t["Secrets | the deployed ones whose value in AAP differs"],
                           t["Secrets | refuse to silently change a deployed database password"]]}]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "play.yml")
            with open(p, "w", encoding="utf-8") as fh:
                yaml.safe_dump(play, fh)
            env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0")
            out = subprocess.run([PLAYBOOK, "-i", "localhost,", "-c", "local", "-vvv", p],
                                 capture_output=True, text=True, env=env, timeout=300)
            text = out.stdout + out.stderr
            self.assertNotEqual(out.returncode, 0, text)
            self.assertIn("The stigman-mysql-root value in AAP differs", text)
            self.assertNotIn("stigman-db-password value", text)                 # not deployed: nothing to refuse
            for h in (have, want, old, new):
                self.assertNotIn(h, text)
            # allowed: nothing refused
            out = subprocess.run([PLAYBOOK, "-i", "localhost,", "-c", "local", p, "-e", "stigman_replace_secrets=true"],
                                 capture_output=True, text=True, env=env, timeout=300)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
