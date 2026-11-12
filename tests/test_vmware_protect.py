#!/usr/bin/env python3
"""The VMware protected list, as the jobs read it (roles/vmware_vm/tasks/protected.yml, guard.yml, snapshot_split.yml,
snapshot_cleanup.yml, pick_snapshots.yml) and the certificate install's plan (cert_install_apply.yml) - run with
ansible-playbook on localhost, no vCenter: a VM name with spaces given as survey text is protected; "dcs" as text
protects the group dcs; the snapshot cleanup holds back the snapshots of vmware_protected_groups; the snapshot
cleanup and the snapshot pick list are refused when the inventory cannot say which VM is the AAP server; the
certificate install takes only its own workflow's plan.
Needs ansible-core: python3 tests/test_vmware_protect.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))

INV_AAP = """all:
  hosts:
    localhost: {ansible_connection: local}
  children:
    aap:
      hosts:
        aap01.example.mil: {ansible_host: 192.0.2.5}
    dcs:
      hosts:
        dc01.example.mil: {ansible_host: 192.0.2.30}
"""
INV_NOAAP = """all:
  hosts:
    localhost: {ansible_connection: local}
"""


def playbook():
    ap = os.environ.get("ANSIBLE_PLAYBOOK")
    if ap:
        return ap
    near = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return near if os.path.exists(near) else shutil.which("ansible-playbook")


class Protected(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ap = playbook()
        if not cls.ap:
            raise unittest.SkipTest("ansible-playbook not found")
        cls.tmp = tempfile.mkdtemp()
        for name, text in (("inv.yml", INV_AAP), ("inv-noaap.yml", INV_NOAAP)):
            with open(os.path.join(cls.tmp, name), "w") as fh:
                fh.write(text)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_tasks(self, tasks_from, extra, after=None, inv="inv.yml", env=None):
        """Include roles/vmware_vm/tasks/<tasks_from> on localhost with these extra variables, then the tasks
        after (YAML text). -> (exit code, output)"""
        pb = os.path.join(self.tmp, "play.yml")
        with open(pb, "w") as fh:
            fh.write("- hosts: localhost\n  gather_facts: false\n  tasks:\n"
                     "    - ansible.builtin.include_role:\n        name: vmware_vm\n        tasks_from: %s\n%s" % (tasks_from, after or ""))
        ev = dict(os.environ, ANSIBLE_CONFIG=os.path.join(REPO, "ansible.cfg"), ANSIBLE_ROLES_PATH=os.path.join(REPO, "roles"),
                  ANSIBLE_FILTER_PLUGINS=os.path.join(REPO, "plugins", "filter"), ANSIBLE_NOCOLOR="1",
                  ANSIBLE_LOCALHOST_WARNING="False", ANSIBLE_INVENTORY_UNPARSED_WARNING="False",
                  ANSIBLE_DEPRECATION_WARNINGS="False", ANSIBLE_STDOUT_CALLBACK="default")
        ev.pop("VMWARE_HOST", None)
        ev.update(env or {})
        r = subprocess.run([self.ap, "-i", os.path.join(self.tmp, inv), pb, "-e", json.dumps(extra)], cwd=REPO, env=ev,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=300)
        return r.returncode, r.stdout

    # ---- 1.36.0: a protected name that matches no VM is said (protected_check.yml) - never a failure ----
    def test_check_read_fails_said_once_job_goes_on(self):
        prot = {"groups": ["aap", "aap_hosts", "dcs"], "ids": [], "names": ["VCSA01"], "aap_known": True}
        rc, out = self.run_tasks("protected_check.yml", {"_vm_prot": prot, "vmware_protected_vms": ["VCSA01"],
                                                         "vmware_protected_groups": ["aap", "dcs", "nogroup"]},
                                 after="    - ansible.builtin.debug: {msg: 'AFTER THE CHECK'}\n",
                                 env={"VMWARE_HOST": "127.0.0.1", "VMWARE_PORT": "1", "VMWARE_USER": "u", "VMWARE_PASSWORD": "p"})
        self.assertEqual(rc, 0, out)
        self.assertIn("AFTER THE CHECK", out)
        self.assertEqual(out.count("WARNING: Could not check the protected names: "), 1, out)
        self.assertIn("WARNING: Protected group not in this inventory: nogroup (vmware_protected_groups) - check the spelling", out)
        self.assertNotIn("not in this inventory: dcs", out)
        self.assertNotIn("not in this inventory: aap", out)

    def test_check_off(self):
        prot = {"groups": ["aap", "aap_hosts"], "ids": [], "names": ["VCSA01"], "aap_known": True}
        rc, out = self.run_tasks("protected_check.yml", {"_vm_prot": prot, "vmware_protected_vms": ["VCSA01"], "vmware_protected_check": False,
                                                         "vmware_protected_groups": ["nogroup"]},
                                 env={"VMWARE_HOST": "127.0.0.1", "VMWARE_PORT": "1"})
        self.assertEqual(rc, 0, out)
        self.assertNotIn("WARNING:", out)

    # ---- restart, shut down, VLAN change (guard.yml) ----
    def test_spaced_vm_name_in_survey_text_is_protected(self):
        rc, out = self.run_tasks("guard.yml", {"_vm_found": [{"name": "File Server 01", "ids": ["fs01"]}],
                                               "vmware_protected_vms": "File Server 01, VCSA01"})
        self.assertNotEqual(rc, 0, out)
        self.assertIn("Refused: File Server 01 is the AAP server or a protected VM", out)

    def test_group_given_as_text_is_protected(self):
        rc, out = self.run_tasks("guard.yml", {"_vm_found": [{"name": "DC-01", "ids": ["dc-01", "192.0.2.30"]}],
                                               "vmware_protected_groups": "dcs"})
        self.assertNotEqual(rc, 0, out)
        self.assertIn("Refused: DC-01 is the AAP server or a protected VM (groups aap, aap_hosts, dcs", out)

    def test_other_vm_passes(self):
        rc, out = self.run_tasks("guard.yml", {"_vm_found": [{"name": "web01", "ids": ["web01", "192.0.2.40"]}],
                                               "vmware_protected_groups": "dcs", "vmware_protected_vms": "File Server 01, VCSA01"})
        self.assertEqual(rc, 0, out)

    def test_protected_list_given_as_a_variable_keeps_aap(self):
        rc, out = self.run_tasks("guard.yml", {"_vm_found": [], "_vm_prot": {"groups": [], "ids": [], "names": [], "aap_known": True}})
        self.assertNotEqual(rc, 0, out)
        self.assertIn("does not have the AAP server on it", out)

    # ---- the snapshot cleanup, report and pick list (snapshot_split.yml) ----
    SNAPS = {"vcenters": [{"name": "vc01.example.mil", "ok": True}],
             "snapshots": [{"vm": "DC-01", "moid": "vm-1", "vcenter": "vc01.example.mil", "id": 1, "name": "old", "description": "",
                            "age_days": 30.0, "size_gb": 1.0, "hostname": "dc01.example.mil", "ip": "192.0.2.30"},
                           {"vm": "File Server 01", "moid": "vm-2", "vcenter": "vc01.example.mil", "id": 1, "name": "old", "description": "",
                            "age_days": 30.0, "size_gb": 1.0, "hostname": "", "ip": ""},
                           {"vm": "web01", "moid": "vm-3", "vcenter": "vc01.example.mil", "id": 1, "name": "old", "description": "",
                            "age_days": 30.0, "size_gb": 1.0, "hostname": "web01.example.mil", "ip": "192.0.2.40"}]}
    SPLIT_CHECK = """    - ansible.builtin.debug:
        msg: "SPLIT old={{ _vm_sn.old | map(attribute='vm') | join('|') }} held={{ _vm_sn.held | map(attribute='vm') | join('|') }}"
"""

    def test_snapshot_cleanup_honours_protected_groups(self):
        for groups in ("dcs", ["aap", "aap_hosts", "dcs"]):
            rc, out = self.run_tasks("snapshot_split.yml", {"_vm_snap_read": self.SNAPS, "vmware_protected_groups": groups,
                                                            "vm_snapshot_cleanup_exclude_vms": "File Server 01, VCSA01"},
                                     self.SPLIT_CHECK)
            self.assertEqual(rc, 0, out)
            self.assertIn("SPLIT old=web01 held=DC-01|File Server 01", out)

    def test_snapshot_cleanup_refused_without_the_aap_group(self):
        rc, out = self.run_tasks("snapshot_cleanup.yml", {}, inv="inv-noaap.yml", env={"VMWARE_HOST": "vc01.example.mil"})
        self.assertNotEqual(rc, 0, out)
        self.assertIn("cannot tell which VM is the AAP server, so it will not delete any snapshot", out)

    def test_snapshot_pick_list_refused_without_the_aap_group(self):
        rc, out = self.run_tasks("pick_snapshots.yml", {}, inv="inv-noaap.yml", env={"VMWARE_HOST": "vc01.example.mil"})
        self.assertNotEqual(rc, 0, out)
        self.assertIn("cannot tell which VM is the AAP server, so it will not offer or delete any snapshot", out)

    # ---- the certificate install takes only its own workflow's plan ----
    PLAN = [{"name": "esx01.example.mil", "kind": "esxi", "ritm": "RITM0012345"}]

    def test_cert_plan_from_another_workflow_refused(self):
        rc, out = self.run_tasks("cert_install_apply.yml", {"vmware_cert_install_plan": self.PLAN, "vmware_cert_install_plan_workflow": "11",
                                                            "awx_workflow_job_id": 12})
        self.assertNotEqual(rc, 0, out)
        self.assertIn("did not come from this workflow's own check step (it is from workflow job 11, this is workflow job 12)", out)

    def test_cert_plan_outside_a_workflow_refused(self):
        rc, out = self.run_tasks("cert_install_apply.yml", {"vmware_cert_install_plan": self.PLAN})
        self.assertNotEqual(rc, 0, out)
        self.assertIn("(it is from no workflow, this is no workflow)", out)

    def test_cert_plan_of_this_workflow_passes_the_check(self):
        rc, out = self.run_tasks("cert_install_apply.yml", {"vmware_cert_install_plan": self.PLAN, "vmware_cert_install_plan_workflow": "12",
                                                            "awx_workflow_job_id": 12})
        self.assertNotEqual(rc, 0, out)                    # no vCenter credential here: the next step stops it
        self.assertNotIn("own check step", out)
        self.assertIn("No vCenter to talk to", out)


class TaskFiles(unittest.TestCase):
    """What only a vCenter shows, read from the task files."""

    def test_snapshot_create_reads_the_credentials_vcenter_only(self):
        # a VM ID (moid) repeats in every vCenter: "already has a snapshot of this name" is the VM's own vCenter only
        import yaml
        with open(os.path.join(REPO, "roles/vmware_vm/tasks/snapshot_create.yml")) as fh:
            tasks = yaml.safe_load(fh)
        read = [t for t in tasks if "site_vmware_snapshots" in t]
        self.assertEqual(len(read), 1)
        self.assertEqual(read[0]["site_vmware_snapshots"]["vcenters"], [])

    def test_snapshot_delete_by_id(self):
        # several snapshots of one name: each is deleted by its ID (by name, vCenter deletes only the first)
        import yaml
        with open(os.path.join(REPO, "roles/vmware_vm/tasks/snapshot_delete.yml")) as fh:
            tasks = yaml.safe_load(fh)
        dele = [t for t in tasks if "vmware.vmware.vm_snapshot" in t][0]["vmware.vmware.vm_snapshot"]
        self.assertNotIn("snapshot_name", dele)
        self.assertIn("item.id", dele["snapshot_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
