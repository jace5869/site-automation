#!/usr/bin/env python3
"""1.91.0: playbooks/java4shell.yml (Security - Java4Shell check (Keycloak)) - its compose-file edit, run by ansible-playbook on this machine
(-c local, no become) against fake compose files: a map environment, a list, an existing JAVA_OPTS_APPEND, none at all, the flag already
there; refused: a one-line environment, an anchor/merge (<<:). In check mode nothing is written; a real run writes the file, keeps a private
backup and the result reads as YAML. No Keycloak, no container: the read step finds none and says so (podman or docker are only listed).
ANSIBLE_PLAYBOOK picks the ansible-playbook (ansible-core 2.16 and the current one in CI).
    python3 tests/test_java4shell.py"""
import glob
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PB = os.path.join(REPO, "playbooks", "java4shell.yml")
AP = os.environ.get("ANSIBLE_PLAYBOOK") or shutil.which("ansible-playbook")
FLAG = "-XX:-UseBASE64Intrinsics"
ENV = dict(os.environ, ANSIBLE_STDOUT_CALLBACK="j4s_capture", ANSIBLE_CALLBACK_PLUGINS=os.path.join(HERE, "java4shell", "callback_plugins"),
           ANSIBLE_NOCOLOR="1", ANSIBLE_PYTHON_INTERPRETER=sys.executable, ANSIBLE_LOCALHOST_WARNING="0", ANSIBLE_INVENTORY_UNPARSED_WARNING="0",
           ANSIBLE_RETRY_FILES_ENABLED="0")


STANDALONE_NOTE = ("# site_exclude is not honoured in this copy: it is the air-gapped one-file copy of playbooks/java4shell.yml, without its\n"
                   "# import of site_exclude.yml (made by tests/test_java4shell.py --standalone; the repo's AAP job honours it).\n")


def standalone(text):
    """The air-gapped copy: the repository file without its one marked import line, and a head line that says so."""
    lines = text.splitlines(True)
    marked = [i for i, l in enumerate(lines) if "J4S-STANDALONE" in l]
    if len(marked) != 1 or not lines[marked[0]].startswith("  ansible.builtin.import_playbook: site_exclude.yml") \
            or not lines[marked[0] - 1].startswith("- name: Leave out the hosts of site_exclude_hosts"):
        raise ValueError("the marked import (its name line and the import) is not there exactly once")
    i = marked[0] - 1
    del lines[i:i + 2]
    if lines[i].strip() == "":
        del lines[i]                                           # the blank line after it
    return lines[0] + STANDALONE_NOTE + "".join(lines[1:])


def result(p):
    """The capture callback's document (tests/java4shell/callback_plugins/j4s_capture.py): {tasks: {name: result}, stats}."""
    line = [x for x in p.stdout.splitlines() if x.startswith('{"tasks"')]
    if not line:
        raise AssertionError(p.stdout[-2000:] + p.stderr[-2000:])
    return json.loads(line[-1])

MAP = """services:
  db:
    image: docker.io/library/postgres:16
    environment:
      POSTGRES_DB: keycloak
  keycloak:
    image: quay.io/keycloak/keycloak:25.0.5
    command: start
    environment:
      KC_DB: postgres
      KC_HOSTNAME: auth.example.mil
    ports:
      - "8443:8443"
"""
LIST = """services:
  keycloak:
    image: "quay.io/keycloak/keycloak:25.0.5"
    environment:
      - KC_DB=postgres
      - KC_HOSTNAME=auth.example.mil
    depends_on:
      - db
  db:
    image: docker.io/library/postgres:16
"""
EXISTING = """services:
  auth:
    image: quay.io/keycloak/keycloak:25.0.5
    environment:
      KC_DB: postgres
      JAVA_OPTS_APPEND: "-Xms512m -Xmx1g"
"""
NONE = """services:
  keycloak:
    image: quay.io/keycloak/keycloak:25.0.5
    command: start-dev
"""
ALREADY = """services:
  keycloak:
    image: quay.io/keycloak/keycloak:25.0.5
    environment:
      JAVA_OPTS_APPEND: "-XX:-UseBASE64Intrinsics"
"""
INLINE = """services:
  keycloak:
    image: quay.io/keycloak/keycloak:25.0.5
    environment: {KC_DB: postgres}
"""
MERGE = """x-common: &common
  restart: always
services:
  keycloak:
    <<: *common
    image: quay.io/keycloak/keycloak:25.0.5
    environment:
      KC_DB: postgres
"""


@unittest.skipUnless(AP, "no ansible-playbook here")
class Edit(unittest.TestCase):
    def run_pb(self, text, check, **extra):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        f = os.path.join(d, "docker-compose.yml")
        with open(f, "w") as fh:
            fh.write(text)
        os.chmod(f, 0o644)
        ev = dict({"j4s_compose_file": f, "j4s_fix": True, "j4s_become": False, "j4s_scan_host_jvms": False}, **extra)
        cmd = [AP, "-i", "localhost,", "-c", "local", PB, "-e", json.dumps(ev)] + (["--check"] if check else [])
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, env=ENV, timeout=300, cwd=REPO)
        tasks = result(p)["tasks"]
        with open(f) as fh:
            now = fh.read()
        return p.returncode, tasks, now, d

    def edit(self, tasks):
        return json.loads(tasks["Java4Shell | add the flag to the Keycloak service (a backup is kept beside the file)"]["stdout"])

    def test_a_check_mode_writes_nothing(self):
        for name, text in (("map", MAP), ("list", LIST), ("existing", EXISTING), ("none", NONE)):
            rc, tasks, now, d = self.run_pb(text, True)
            self.assertEqual(rc, 0, name)
            e = self.edit(tasks)
            self.assertTrue(e["changed"], (name, e))
            self.assertEqual(e["refused"], [], name)
            self.assertEqual(now, text, name)                                                         # nothing written
            self.assertEqual(glob.glob(os.path.join(d, "*.bak-java4shell-*")), [], name)
            self.assertIn("(check mode: nothing written)", tasks["Java4Shell | the change"]["msg"], name)
            self.assertNotIn(FLAG, "".join(e["shown"]), name)                                        # option values never shown
            self.assertIn("RESULT: ", tasks["Java4Shell | the report"]["msg"][0])                      # the read ran (check_mode: false)

    def test_b_the_edit_cases(self):
        want = {
            "map": ('      KC_HOSTNAME: auth.example.mil\n      JAVA_OPTS_APPEND: "%s"\n    ports:' % FLAG, "JAVA_OPTS_APPEND added"),
            "list": ('      - KC_HOSTNAME=auth.example.mil\n      - "JAVA_OPTS_APPEND=%s"\n    depends_on:' % FLAG, "JAVA_OPTS_APPEND added"),
            "existing": ('      JAVA_OPTS_APPEND: "-Xms512m -Xmx1g %s"\n' % FLAG, "JAVA_OPTS_APPEND: %s added to its options" % FLAG),
            "none": ('    command: start-dev\n    environment:\n      JAVA_OPTS_APPEND: "%s"' % FLAG, "environment: added, with JAVA_OPTS_APPEND"),
        }
        for name, text in (("map", MAP), ("list", LIST), ("existing", EXISTING), ("none", NONE)):
            rc, tasks, now, d = self.run_pb(text, False)
            self.assertEqual(rc, 0, name)
            e = self.edit(tasks)
            self.assertIn(want[name][0], now, (name, now))
            self.assertEqual([s["did"] for s in e["services"]], [[want[name][1]]], name)
            self.assertEqual(now.count(FLAG), 1, name)
            if name == "map":
                self.assertIn("  db:\n    image: docker.io/library/postgres:16\n    environment:\n      POSTGRES_DB: keycloak\n", now)   # the other service as it was
            b = glob.glob(os.path.join(d, "docker-compose.yml.bak-java4shell-*"))
            self.assertEqual(len(b), 1, name)
            with open(b[0]) as fh:
                self.assertEqual(fh.read(), text, name)                                               # the backup is the file before
            self.assertEqual(stat.S_IMODE(os.stat(b[0]).st_mode), 0o600, name)                        # private
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(d, "docker-compose.yml")).st_mode), 0o644, name)   # the file keeps its mode
            y = tasks.get("Java4Shell | the file still reads as YAML (else the backup is put back)", {})
            self.assertIn(y.get("stdout", ""), ("ok: YAML", "skipped: no PyYAML on the host"), name)
        rc, tasks, now, d = self.run_pb(ALREADY, False)                                               # the flag already there: no change
        self.assertEqual((rc, now), (0, ALREADY))
        self.assertEqual(self.edit(tasks)["services"][0]["did"], ["already has %s" % FLAG])
        self.assertEqual(glob.glob(os.path.join(d, "*.bak-java4shell-*")), [])

    def test_c_refused(self):
        for name, text, why in (("inline", INLINE, "environment is written on one line"), ("merge", MERGE, "uses YAML anchors or merges")):
            for check in (True, False):
                rc, tasks, now, d = self.run_pb(text, check)
                self.assertNotEqual(rc, 0, (name, check))                                            # the job says it failed
                e = self.edit(tasks)
                self.assertTrue(any(why in r for r in e["refused"]), (name, e))
                self.assertIn('JAVA_OPTS_APPEND: "%s" by hand' % FLAG, e["refused"][0], name)       # what to add by hand
                self.assertEqual(now, text, name)                                                     # nothing written
                self.assertEqual(glob.glob(os.path.join(d, "*.bak-java4shell-*")), [], name)
                self.assertTrue(tasks["Java4Shell | the change"]["msg"][0].startswith("REFUSED - nothing written: "), name)

    def test_d_a_named_service_and_a_missing_file(self):
        rc, tasks, now, d = self.run_pb(EXISTING, True, j4s_service="nope")
        self.assertNotEqual(rc, 0)
        self.assertIn("no service 'nope' in the file", self.edit(tasks)["refused"][0])
        rc, tasks, now, d = self.run_pb(MAP, True, j4s_compose_file="/nonexistent/docker-compose.yml")
        self.assertNotEqual(rc, 0)
        self.assertIn("is not there - set j4s_compose_file", self.edit(tasks)["refused"][0])

    def test_e_read_only_by_default(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        f = os.path.join(d, "docker-compose.yml")
        with open(f, "w") as fh:
            fh.write(MAP)
        p = subprocess.run([AP, "-i", "localhost,", "-c", "local", PB, "-e", json.dumps({"j4s_compose_file": f, "j4s_become": False})],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, env=ENV, timeout=300, cwd=REPO)
        self.assertEqual(p.returncode, 0, p.stdout[-2000:] + p.stderr[-2000:])
        out = result(p)
        self.assertEqual(out["tasks"]["Java4Shell | add the flag to the Keycloak service (a backup is kept beside the file)"]["_state"], "skipped")   # j4s_fix false
        self.assertEqual(out["stats"]["changed"], 0)
        with open(f) as fh:
            self.assertEqual(fh.read(), MAP)


@unittest.skipUnless(AP, "no ansible-playbook here")
class Restart(unittest.TestCase):
    """v3 (the user's AT RISK after a "successful" fix): RHEL's older podman-compose STARTS an existing container again, so a new
    environment never reaches it. The flag already in the file, the old container running without it: the restart removes exactly that
    service's containers by id (rm -f, volumes kept), compose creates a new one, and the check after it reads the flag live: MITIGATED.
    A stand-in podman and podman-compose (tests/java4shell/fake) - nothing runs."""

    def test_old_container_is_replaced(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        proj = os.path.join(d, "stigman")
        os.makedirs(proj)
        f = os.path.join(proj, "docker-compose.yml")
        with open(f, "w") as fh:
            fh.write(ALREADY)
        state = os.path.join(d, "state.json")
        with open(state, "w") as fh:
            json.dump({"project": "stigman", "n": 0, "log": [], "containers": [
                {"id": "kc0old", "name": "stigman_keycloak_1", "image": "quay.io/keycloak/keycloak:25.0.5", "service": "keycloak",
                 "project": "stigman", "running": True, "flag": False},
                {"id": "db0", "name": "stigman_db_1", "image": "docker.io/library/postgres:16", "service": "db", "project": "stigman",
                 "running": True, "flag": False}]}, fh)
        env = dict(ENV, J4S_FAKE_STATE=state, PATH=os.path.join(HERE, "java4shell", "fake") + os.pathsep + os.environ.get("PATH", ""))
        ev = {"j4s_compose_file": f, "j4s_fix": True, "j4s_restart": True, "j4s_become": False, "j4s_scan_host_jvms": False, "j4s_engine": "podman"}
        p = subprocess.run([AP, "-i", "localhost,", "-c", "local", PB, "-e", json.dumps(ev)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, env=env, timeout=600, cwd=REPO)
        out = result(p)
        t = out["tasks"]
        self.assertEqual(p.returncode, 0, json.dumps(t, default=str)[-3000:])
        self.assertTrue(any(x.startswith("RESULT: ") and x != "RESULT: MITIGATED" for x in t["Java4Shell | the report"]["msg"]))    # before: the flag not live
        self.assertEqual(self.edit_did(t), ["already has %s" % FLAG])                                    # the file had it already
        with open(state) as fh:
            st = json.load(fh)
        self.assertEqual(st["log"], ["rm kc0old", "created keycloak"])                                    # removed by id, then created - never only started
        self.assertEqual(sorted(c["id"] for c in st["containers"]), ["db0", "kc1new"])                   # the database untouched (--no-deps)
        self.assertIn("MITIGATED: the flag is live in every recreated container", t["Java4Shell | after the restart"]["msg"])
        with open(f) as fh:
            self.assertEqual(fh.read(), ALREADY)                                                          # nothing to write

    def edit_did(self, t):
        return json.loads(t["Java4Shell | add the flag to the Keycloak service (a backup is kept beside the file)"]["stdout"])["services"][0]["did"]


@unittest.skipUnless(AP, "no ansible-playbook here")
class Standalone(unittest.TestCase):
    def test_it_parses_alone(self):
        """The air-gapped copy, alone in a folder (no site_exclude.yml beside it): ansible-playbook --syntax-check passes."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        with open(PB) as fh:
            one = standalone(fh.read())
        f = os.path.join(d, "java4shell.yml")
        with open(f, "w") as fh:
            fh.write(one)
        p = subprocess.run([AP, "--syntax-check", "-i", "localhost,", f], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, env=dict(os.environ, ANSIBLE_CONFIG=os.path.join(d, "none.cfg")), cwd=d, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout[-1500:] + p.stderr[-1500:])
        self.assertEqual(os.listdir(d), ["java4shell.yml"])


class Plumbing(unittest.TestCase):
    def test_one_file_and_the_template(self):
        with open(PB) as fh:
            y = fh.read()
        self.assertIn('hosts: "{{ j4s_hosts | default(\'all\') }}{{ hostvars[\'localhost\'][\'site_exclude_not\'] | default(\'\') }}"', y)   # the Limit, the site's exclusions
        one = standalone(y)                                                                           # the air-gapped copy: one file
        self.assertNotRegex(one, r"(?m)^\s*-?\s*(ansible\.builtin\.)?(import|include)_(tasks|playbook|role|vars)\s*:")
        self.assertNotRegex(one, r"(?m)^\s*roles\s*:")
        self.assertIn("site_exclude is not honoured in this copy", one.splitlines()[1])
        self.assertEqual(one.replace(STANDALONE_NOTE, ""), y.replace(
            "- name: Leave out the hosts of site_exclude_hosts (1.67.0)\n  ansible.builtin.import_playbook: site_exclude.yml   # J4S-STANDALONE: the air-gapped copy is this file without these two lines\n\n", ""))   # nothing else differs
        for k in ("j4s_compose_file: /podman/stigman/docker-compose.yml", "j4s_fix: false", "j4s_restart: false"):
            self.assertIn(k, y)
        with open(os.path.join(REPO, "aap", "job_templates.yml")) as fh:
            t = fh.read()
        self.assertIn("  - name: Security - Java4Shell check (Keycloak)\n    playbook: playbooks/java4shell.yml\n", t)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--standalone"]:                    # python3 tests/test_java4shell.py --standalone OUT: the air-gapped copy
        with open(PB) as fh, open(sys.argv[2], "w") as out:
            out.write(standalone(fh.read()))
        sys.exit(0)
    unittest.main()
