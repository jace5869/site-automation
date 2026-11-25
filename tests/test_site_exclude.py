#!/usr/bin/env python3
"""Leave hosts out of every job (1.67.0 part J): the matcher (plugins/filter/site_exclude_filters.py), and the guard -
every playbook with a play on hosts starts with the site_exclude play and ends each such play's hosts line with the
suffix, so a host in site_exclude_hosts is never in it.
    python3 tests/test_site_exclude.py"""
import glob
import os
import sys
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
import site_exclude_filters as X  # noqa: E402

SUFFIX = "hostvars['localhost']['site_exclude_not'] | default('')"


def aap_side(hosts):
    """A play on localhost, the stand-in (localhost or the Limit's first host, connection local) or localhost,...: the
    AAP side - its hosts line never connects to a host of its own."""
    h = " ".join(str(hosts).split())
    return h == "localhost" or "'localhost' if ansible_limit" in h or h.startswith("localhost,")


class Matcher(unittest.TestCase):
    HOSTS = ["web01.example.mil", "web02.example.mil", "web10", "db01.example.mil", "192.0.2.10", "test1.lab.example.mil", "OLD01.example.mil"]

    def test_names_short_and_whole_either_way(self):
        m = X.site_exclude_match
        self.assertEqual(m(self.HOSTS, ["web01"]), ["web01.example.mil"])                     # short -> the FQDN
        self.assertEqual(m(self.HOSTS, ["web10.example.mil"]), ["web10"])                     # an FQDN -> the short host
        self.assertEqual(m(self.HOSTS, ["WEB0?"]), ["web01.example.mil", "web02.example.mil"])  # ? and any case
        self.assertEqual(m(self.HOSTS, ["*.lab.example.mil", "old01"]), ["OLD01.example.mil", "test1.lab.example.mil"])
        self.assertEqual(m(self.HOSTS, "web02, db01"), ["db01.example.mil", "web02.example.mil"])   # text, with commas
        self.assertEqual(m(self.HOSTS, ["192.0.2.10"]), ["192.0.2.10"])
        self.assertEqual(m(self.HOSTS, ["192"]), [])                                          # an address is never shortened
        self.assertEqual(m(self.HOSTS, ["web"]), [])                                          # a part of a name is no match
        self.assertEqual(m(self.HOSTS, []), [])
        self.assertEqual(m(self.HOSTS, None), [])
        with self.assertRaises(ValueError):
            m(self.HOSTS, {"web01": 1})

    def test_groups(self):
        groups = {"all": self.HOSTS, "ungrouped": ["web10"], "retired": ["db01.example.mil"], "lab_east": ["test1.lab.example.mil"],
                  "web": ["web01.example.mil", "web02.example.mil"]}
        m = X.site_exclude_match
        self.assertEqual(m(self.HOSTS, [], groups, ["retired", "LAB_*"]), ["db01.example.mil", "test1.lab.example.mil"])
        self.assertEqual(m(self.HOSTS, [], groups, ["all", "ungrouped", "a*"]), [])           # never every host
        self.assertEqual(m(self.HOSTS, ["web01"], groups, ["retired"]), ["db01.example.mil", "web01.example.mil"])

    def test_the_line(self):
        self.assertEqual(X.site_exclude_line([]), "")
        self.assertEqual(X.site_exclude_line(["b", "A"]), "Left out by site_exclude_hosts: 2 - A, b")
        hs = ["h%02d" % i for i in range(13)]
        self.assertEqual(X.site_exclude_line(hs), "Left out by site_exclude_hosts: 13 - %s and 3 more" % ", ".join(hs[:10]))

    def test_vm_patterns(self):
        self.assertEqual(X.site_exclude_patterns(["web01.example.mil", "db*", "192.0.2.5", "*.lab.example.mil"]),
                         ["web01.example.mil", "web01", "web01.*", "db*", "db*.*", "192.0.2.5", "*.lab.example.mil"])
        self.assertEqual(X.site_exclude_match(["web10", "x.lab.example.mil"], ["*.lab.example.mil"]), ["x.lab.example.mil"])
        self.assertEqual(X.site_exclude_name("WEB01", ["web01.example.mil"]), "web01.example.mil")
        self.assertEqual(X.site_exclude_name("web03", ["web01.example.mil"]), "")


class EveryPlaybook(unittest.TestCase):
    """The guard: no playbook runs a play on hosts without the site_exclude play before it and the suffix on it."""

    def test_every_play_on_hosts(self):
        missing, plays_on_hosts, files = [], 0, 0
        for path in sorted(glob.glob(os.path.join(REPO, "playbooks", "*.yml"))):
            name = os.path.basename(path)
            if name in ("site_exclude.yml", "workflow_dry_run.yml"):    # workflow_dry_run.yml: an imported guard, one assert, no host task
                continue
            with open(path) as f:
                text = f.read()
            items = yaml.safe_load(text) or []
            on_hosts = [p for p in items if isinstance(p, dict) and "hosts" in p and not aap_side(p["hosts"])]
            if not on_hosts:
                continue
            files += 1
            first = next((p for p in items if isinstance(p, dict)), {})
            if (first.get("ansible.builtin.import_playbook") or first.get("import_playbook")) != "site_exclude.yml":
                missing.append("%s: its first entry is not - import_playbook: site_exclude.yml" % name)
            for p in on_hosts:
                plays_on_hosts += 1
                if SUFFIX not in " ".join(str(p["hosts"]).split()):
                    missing.append("%s, play %r: its hosts line lacks %s" % (name, p.get("name"), SUFFIX))
            for p in items:            # a playbook it imports from elsewhere (ACT's): its target carries the suffix
                imp = isinstance(p, dict) and (p.get("ansible.builtin.import_playbook") or p.get("import_playbook")) or ""
                if imp.startswith("../vendor/") and "site_exclude_target" not in str((p.get("vars") or {}).get("target", "")):
                    missing.append("%s: the import of %s does not pass target: site_exclude_target" % (name, imp))
        self.assertEqual(missing, [])
        self.assertGreater(files, 50)
        self.assertGreater(plays_on_hosts, 90)

    def test_the_exclude_play(self):
        with open(os.path.join(REPO, "playbooks", "site_exclude.yml")) as f:
            plays = yaml.safe_load(f)
        self.assertEqual(len(plays), 1)
        p = plays[0]
        self.assertEqual((p["hosts"], p["gather_facts"]), ("all,localhost", False))      # any Limit; no connection
        with open(os.path.join(REPO, "roles", "site_exclude", "tasks", "main.yml")) as f:
            tasks = yaml.safe_load(f)
        mods = set(k for t in tasks for k in t if k.startswith("ansible.builtin."))
        self.assertLessEqual(mods, {"ansible.builtin.set_fact", "ansible.builtin.add_host", "ansible.builtin.debug"})   # never a host
        self.assertTrue(all(t.get("run_once") for t in tasks))

    def test_a_target_with_commas_still_leaves_hosts_out(self):
        """1.128.0 (Codex): target A,B with A left out - the exclusion is a term of its own (A,B,!site_excluded); with ':'
        Ansible split at the commas first and A ran. Without a comma (x:&y) ':' stays. Runs ansible-playbook (2.16 too:
        ANSIBLE_PLAYBOOK)."""
        import shutil
        import subprocess
        import tempfile
        ap = os.environ.get("ANSIBLE_PLAYBOOK") or shutil.which("ansible-playbook")
        if not ap:
            self.skipTest("no ansible-playbook")
        tmp = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmp, "inv.ini"), "w") as f:
                f.write("[x]\nhostA ansible_connection=local\nhostB ansible_connection=local\nhostC ansible_connection=local\n"
                        "[y]\nhostA\nhostB\n")
            with open(os.path.join(tmp, "p.yml"), "w") as f:
                f.write("- ansible.builtin.import_playbook: %s\n- hosts: \"{{ target | default('all') }}{{ %s }}\"\n"
                        "  gather_facts: false\n  tasks:\n    - ansible.builtin.debug:\n        msg: 'RAN-{{ inventory_hostname }}'\n"
                        % (os.path.join(REPO, "playbooks", "site_exclude.yml"), SUFFIX))
            env = dict(os.environ, ANSIBLE_FILTER_PLUGINS=os.path.join(REPO, "plugins", "filter"), ANSIBLE_HOST_KEY_CHECKING="False",
                       ANSIBLE_PYTHON_INTERPRETER=sys.executable, ANSIBLE_LOCALHOST_WARNING="False")
            for target, want in (("hostA,hostB", {"hostB"}), ("x:&y", {"hostB"}), ("all", {"hostB", "hostC"})):
                p = subprocess.run([ap, "-i", os.path.join(tmp, "inv.ini"), os.path.join(tmp, "p.yml"), "-e",
                                    '{"target": "%s", "site_exclude_hosts": ["hostA"]}' % target], cwd=tmp, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
                self.assertEqual(p.returncode, 0, p.stdout[-3000:])
                ran = set(x.split("RAN-")[1].split('"')[0] for x in p.stdout.splitlines() if '"msg": "RAN-' in x)
                self.assertEqual(ran, want, target)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_reports_and_vmware_read_it(self):
        with open(os.path.join(REPO, "roles", "site_email", "tasks", "main.yml")) as f:
            self.assertIn("site_exclude_line(10)", f.read())
        with open(os.path.join(REPO, "roles", "vmware_vm", "tasks", "protected.yml")) as f:
            prot = f.read()
        self.assertIn("site_exclude_patterns", prot)
        self.assertIn("'aap_known': aap | length > 0 or names | length > 0", prot)      # the AAP check: its own list only
        for path in ("roles/vmware_vm/tasks/esxi_security.yml", "roles/vmware_vm/tasks/rightsize.yml", "roles/vmware_vm/tasks/esxi_network.yml",
                     "roles/vmware_vm/tasks/watch.yml", "roles/horizon/tasks/desktop_opts.yml"):
            with open(os.path.join(REPO, path)) as f:
                self.assertIn("site_exclude_patterns", f.read(), path)


if __name__ == "__main__":
    unittest.main()
