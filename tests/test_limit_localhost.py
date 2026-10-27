#!/usr/bin/env python3
"""A Limit that leaves out localhost (AAP's Limit field, --limit) makes Ansible skip a playbook's localhost play -
"skipping: no hosts matched" - and the job would end GREEN with no report, no ACT, no tickets. So no playbook that
runs on hosts has a localhost play, except the few that work on the AAP side (GUARDED), and those check the Limit first:
- the scan: a playbook with plays on hosts has no localhost play - its report part runs on the hosts, once, on the AAP
  side (include_role with apply: delegate_to: localhost, run_once: true, become: false, and the vars
  ansible_connection: local and ansible_python_interpreter) - or it is in GUARDED, with the Limit check as the first
  task of its first play on hosts (roles/site_findings/tasks/limit_check.yml, or net_config's own);
- the check itself (the implicit localhost, and a localhost in the inventory): no Limit, a Limit with localhost and a
  group that has localhost in it pass; a Limit without it fails, with what to do;
- a converted playbook with a Limit of one host: every play has that host (none is skipped); a GUARDED one with a
  Limit of one host that cannot be reached: refused, and no host contacted.
Needs ansible-core: python3 tests/test_limit_localhost.py   (ANSIBLE_PLAYBOOK=... for another ansible-core)"""
import glob
import os
import re
import subprocess
import sys
import tempfile
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
AP = os.environ.get("ANSIBLE_PLAYBOOK", "ansible-playbook")
GUARDS = {("site_findings", "limit_check.yml"), ("net_config", "limit_check.yml")}
# they work on the AAP side (the instance, the network devices' backups and changes): a Limit must take localhost in
GUARDED = {"net_cert_renew.yml", "net_config_backup.yml", "net_config_load.yml", "servicenow_health.yml"}
# their report part runs on the hosts, once, on the AAP side (0.32.0): a Limit of hosts needs no localhost
AAP_SIDE = {"ad_health_report.yml", "ad_lockout.yml", "cert_install_approved.yml", "cert_install_check.yml",
            "cert_renew_csr.yml", "fapolicyd_containers.yml", "fapolicyd_denials.yml", "log_analysis.yml", "win_cert_install_approved.yml", "win_cert_install_check.yml",
            "win_cert_renew_csr.yml"}
# groups filled while the job runs (add_host): empty when the hosts are listed
RUNTIME_GROUPS = {"adl_sources"}
ENV = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="False", ANSIBLE_INVENTORY_UNPARSED_WARNING="False",
           ANSIBLE_HOST_KEY_CHECKING="False", ANSIBLE_TIMEOUT="5",
           ANSIBLE_ROLES_PATH=os.pathsep.join(os.path.join(REPO, d) for d in ("roles", "vendor/act/ansible/roles")))   # as ansible.cfg


def hosts_of(play):
    return str(play.get("hosts", "")).strip()


def plays_of(path):
    with open(path, encoding="utf-8") as fh:
        return [p for p in (yaml.safe_load(fh) or []) if isinstance(p, dict) and "hosts" in p]


def mixing():
    """{playbook: its first play on hosts} for each playbook with a localhost play and a play on hosts (any order)."""
    found = {}
    for path in sorted(glob.glob(os.path.join(REPO, "playbooks", "*.yml"))):
        plays = plays_of(path)
        on_hosts = [p for p in plays if hosts_of(p) != "localhost"]
        if on_hosts and len(on_hosts) < len(plays):
            found[os.path.basename(path)] = on_hosts[0]
    return found


def is_check(task):
    """The Limit check: an include of a limit_check.yml, or an assert on ansible_limit and localhost."""
    task = task or {}
    inc = task.get("ansible.builtin.include_role") or task.get("include_role")
    if isinstance(inc, dict):
        return (inc.get("name"), inc.get("tasks_from")) in GUARDS
    that = (task.get("ansible.builtin.assert") or task.get("assert") or {}).get("that", "")
    return "ansible_limit" in str(that) and "'localhost'" in str(that)


def first_task(play):
    """The task that runs first in a play: pre_tasks come before roles, roles before tasks."""
    for key in ("pre_tasks", "roles", "tasks"):
        if play.get(key):
            return key, play[key][0]
    return None, None


def tasks_of(play):
    """Every task of a play, the ones in blocks too."""
    out, todo = [], list(play.get("pre_tasks") or []) + list(play.get("tasks") or []) + list(play.get("post_tasks") or [])
    while todo:
        t = todo.pop(0)
        if "block" in t:
            todo = list(t.get("block") or []) + list(t.get("rescue") or []) + list(t.get("always") or []) + todo
        else:
            out.append(t)
    return out


def aap_side_problem(task):
    """'' when the task runs once on the AAP side (as roles/site_findings/tasks/report.yml includes site_email), else why not."""
    inc = task.get("ansible.builtin.include_role") or task.get("include_role")
    if not isinstance(inc, dict):
        return "not an include_role (it would run on every host)"
    apply = inc.get("apply") or {}
    if apply.get("delegate_to") != "localhost" or apply.get("run_once") is not True or apply.get("become") is not False:
        return "its apply lacks delegate_to: localhost, run_once: true, become: false"
    v = task.get("vars") or {}
    if v.get("ansible_connection") != "local" or "ansible_playbook_python" not in str(v.get("ansible_python_interpreter")):
        return "its vars lack ansible_connection: local and ansible_python_interpreter: \"{{ ansible_playbook_python }}\""
    return ""


def on_aap_side(task):
    """The task is meant for the AAP side: an include_role with an apply (delegate_to, run_once ...), whole or not."""
    inc = task.get("ansible.builtin.include_role") or task.get("include_role")
    return isinstance(inc, dict) and bool(inc.get("apply"))


def run(playbook, inventory, *args):
    return subprocess.run([AP, playbook, "-i", inventory] + list(args), cwd=REPO, env=ENV,
                          capture_output=True, text=True, timeout=300)


class Scan(unittest.TestCase):
    def test_no_localhost_play_among_plays_on_hosts(self):
        found = mixing()
        self.assertEqual(sorted(set(found) - GUARDED), [], "a localhost play in a playbook that runs on hosts: a Limit without "
                         "localhost would skip it and the job end green. Run that part on the hosts, once, on the AAP side "
                         "(as log_analysis.yml) - or, when the job works on the AAP side, add the Limit check and GUARDED")
        self.assertEqual(sorted(GUARDED - set(found)), [], "no longer mixes localhost and hosts: take it out of GUARDED")
        for name, play in found.items():
            key, task = first_task(play)
            with self.subTest(playbook=name):
                self.assertNotEqual(key, "roles", "%s: the Limit check goes in pre_tasks, before the roles" % name)
                self.assertTrue(is_check(task), "%s: the first task of the play '%s' is not the Limit check (include_role "
                                "site_findings, tasks_from: limit_check.yml) - a Limit without localhost would skip its "
                                "localhost play and end green" % (name, play.get("name")))

    def test_aap_side_plays(self):
        """In AAP_SIDE, each play that runs on the AAP side does it in every task (a task without would run on every host)."""
        for name in sorted(AAP_SIDE):
            plays = plays_of(os.path.join(REPO, "playbooks", name))
            side = [p for p in plays if any(on_aap_side(t) for t in tasks_of(p))]
            with self.subTest(playbook=name):
                self.assertTrue(side, "%s: no play runs on the AAP side" % name)
                self.assertFalse([p for p in plays if hosts_of(p) == "localhost"], "%s: a localhost play" % name)
                for p in side:
                    for t in tasks_of(p):
                        self.assertEqual(aap_side_problem(t), "", "%s, play '%s', task '%s'" % (name, p.get("name"), t.get("name")))


class TheCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.play = os.path.join(cls.tmp.name, "limit.yml")
        with open(cls.play, "w") as fh:
            fh.write("- hosts: all\n  gather_facts: false\n  tasks:\n"
                     "    - ansible.builtin.include_role: {name: site_findings, tasks_from: limit_check.yml}\n"
                     "    - ansible.builtin.debug: {msg: passed}\n      run_once: true\n")
        cls.implicit = os.path.join(cls.tmp.name, "implicit.yml")      # localhost not in the inventory, as in AAP
        with open(cls.implicit, "w") as fh:
            fh.write("all:\n  children:\n    web:\n      hosts:\n        web01: {ansible_connection: local}\n"
                     "        web02: {ansible_connection: local}\n")
        cls.explicit = os.path.join(cls.tmp.name, "explicit.yml")      # a localhost in the inventory, in the group web
        with open(cls.explicit, "w") as fh:
            fh.write("all:\n  children:\n    web:\n      hosts:\n        web01: {ansible_connection: local}\n"
                     "        localhost: {ansible_connection: local}\n")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def check(self, inventory, want_ok, *args):
        out = run(self.play, inventory, "-e", "ansible_python_interpreter=" + sys.executable, *args)
        if want_ok:
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertIn("passed", out.stdout)
        else:
            self.assertNotEqual(out.returncode, 0, out.stdout)
            self.assertIn("leaves out localhost", out.stdout)
            self.assertIn('Add \\",localhost\\" to the Limit', out.stdout)
            self.assertNotIn("passed", out.stdout)
        return out

    def test_no_limit(self):
        self.check(self.implicit, True)

    def test_limit_without_localhost(self):
        self.check(self.implicit, False, "-l", "web01")
        self.check(self.implicit, False, "-l", "web")

    def test_limit_with_localhost(self):
        self.check(self.implicit, True, "-l", "web01,localhost")
        self.check(self.implicit, True, "-l", "web:localhost")

    def test_a_group_with_localhost_in_it(self):
        self.check(self.explicit, True, "-l", "web")
        self.check(self.explicit, False, "-l", "web01")


class ThePlaybooks(unittest.TestCase):
    GROUPS = ["web", "win_dc", "servicenow_mid_hosts", "net_config_devices", "net_cert_devices", "adl_sources"]

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.inv = os.path.join(cls.tmp.name, "inv.yml")
        with open(cls.inv, "w") as fh:
            fh.write("all:\n  hosts:\n    gone01.example.mil: {ansible_host: 127.0.0.1, ansible_port: 1}\n  children:\n"
                     + "".join("    %s:\n      hosts:\n        gone01.example.mil:\n" % g for g in cls.GROUPS))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_aap_side_every_play_has_the_host(self):
        """A Limit of one host: every play has it (none would be skipped), the AAP side part too."""
        for name in sorted(AAP_SIDE):
            with self.subTest(playbook=name):
                out = run(os.path.join("playbooks", name), self.inv, "--list-hosts", "-l", "gone01.example.mil")
                text = out.stdout + out.stderr
                self.assertEqual(out.returncode, 0, text[-2000:])
                plays = re.split(r"\n  play #\d+ ", out.stdout)[1:]
                self.assertGreaterEqual(len(plays), 2, text[-2000:])
                for p in plays:
                    if any("pattern: ['%s']" % g in p for g in RUNTIME_GROUPS):
                        continue
                    self.assertIn("gone01.example.mil", p, "%s: a play without the host of the Limit:\n%s" % (name, p))

    def test_guarded_refused_before_any_host(self):
        """A GUARDED playbook with a Limit of one host it cannot reach: the check stops it before any host is contacted."""
        for name in sorted(GUARDED):
            with self.subTest(playbook=name):
                out = run(os.path.join("playbooks", name), self.inv, "-l", "gone01.example.mil")
                text = out.stdout + out.stderr
                self.assertNotEqual(out.returncode, 0, text[-2000:])
                self.assertIn("leaves out localhost", text, text[-2000:])
                self.assertNotIn("UNREACHABLE", text, "%s contacted a host before checking the Limit" % name)


if __name__ == "__main__":
    unittest.main(verbosity=1)
