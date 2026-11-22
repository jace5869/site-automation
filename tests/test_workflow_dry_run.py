#!/usr/bin/env python3
"""A dry-run template must run in Check mode. A regular Run template can act on its hosts.
Runs ansible-playbook (ANSIBLE_PLAYBOOK for another ansible-core).
    python3 tests/test_workflow_dry_run.py"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PLAY = """---
- name: The dry-run guard, as a playbook imports it
  ansible.builtin.import_playbook: %s/playbooks/workflow_dry_run.yml

- name: Acts after the guard
  hosts: "{{ target | default('patch_hosts') }}"
  gather_facts: false
  tasks:
    - name: The step itself
      ansible.builtin.debug:
        msg: "ACTED on {{ inventory_hostname }}"
""" % REPO
INV = """all:
  vars: {ansible_connection: local}
  children:
    patch_hosts:
      hosts: {web01: {}, web02: {}}
"""


class DryRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ap = os.environ.get("ANSIBLE_PLAYBOOK") or shutil.which("ansible-playbook")
        cls.work = tempfile.mkdtemp()
        with open(os.path.join(cls.work, "p.yml"), "w") as f:
            f.write(PLAY)
        with open(os.path.join(cls.work, "inv.yml"), "w") as f:
            f.write(INV)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def run_it(self, extra, check=False):
        if not self.ap:
            self.skipTest("no ansible-playbook")
        env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_STDOUT_CALLBACK="default",
                   ANSIBLE_LOCALHOST_WARNING="False", ANSIBLE_INVENTORY_UNPARSED_WARNING="False")
        cmd = [self.ap, "-i", os.path.join(self.work, "inv.yml"), os.path.join(self.work, "p.yml"), "-e", json.dumps(extra)]
        if check:
            cmd.append("--check")
        p = subprocess.run(cmd, cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        return p.returncode, " ".join(p.stdout.split())

    def test_a_dry_run_template_run_as_run_acts_on_nothing(self):
        for extra in ({"site_dry_run_only": True}, {"awx_job_template_name": "Patch hosts (dry run)"}):
            rc, out = self.run_it(extra)
            self.assertNotEqual(rc, 0, extra)
            self.assertIn("it ran as Run. Set its Job type back to Check.", out)
            self.assertNotIn("ACTED on", out)

    def test_a_dry_run_template_passes_in_check(self):
        rc, out = self.run_it({"site_dry_run_only": True}, check=True)
        self.assertEqual(rc, 0, out[-600:])
        self.assertNotIn("FAILED", out)

    def test_a_run_template_acts(self):
        rc, out = self.run_it({})
        self.assertEqual(rc, 0, out[-600:])
        self.assertIn("ACTED on web02", out)


if __name__ == "__main__":
    unittest.main()
