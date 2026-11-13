#!/usr/bin/env python3
"""Old copies of ACT (1.53.0): roles/site_act/library/site_act_old_copies.py in a temporary folder - missing, our link,
a link into the install folder, a link elsewhere, an old file, something else; link (the backup, its name and mode),
delete, report, check mode; the installed ACT not there, python3 too old; the users' saved models (reported, never the
key; rewritten only with site_act_fix_user_model, mode kept; not JSON). Every playbook and role that can run ACT on a
host reaches roles/site_act/tasks/old_copies.yml (ACT on or off); the ones that run ACT only on the AAP side are listed.
python3 tests/test_act_old_copies.py"""
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "roles", "site_act", "library"))
import site_act_old_copies as m  # noqa: E402

KEY = "sk-made-up-0123456789abcdef"
NOW = "20261113T120000Z"
OLD = '#!/usr/bin/env python3\nACT_VERSION = "0.4.2"  # old\nprint("old act")\n'
NEW = '#!/usr/bin/env python3\nACT_VERSION = "0.6.25"\nprint("new act")\n'


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.opt = os.path.join(self.d, "opt", "act")
        self.bin = os.path.join(self.d, "usr", "local", "bin")
        os.makedirs(self.opt)
        os.makedirs(self.bin)
        self.target = os.path.join(self.opt, "act")
        self.write(self.target, NEW, 0o755)
        self.act = os.path.join(self.bin, "act")
        self.home = os.path.join(self.d, "home")
        os.makedirs(self.home)

    def write(self, path, text, mode=0o644):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, mode)

    def params(self, **kw):
        p = {"paths": [self.act], "action": "link", "target": self.target, "backup_dir": os.path.join(self.opt, "old"),
             "job_model": "gemini-4-argon-withtools", "fix_user_model": False,
             "homes": [os.path.join(self.home, "*")], "apply": True, "installing": False}
        p.update(kw)
        return p

    def run_it(self, check=False, py=(True, ""), **kw):
        r = m.run(self.params(**kw), check, now=NOW, python_check=lambda: py)
        self.assertNotIn(KEY, json.dumps(r))                                    # never the key
        return r


class Copies(Base):
    def test_missing(self):
        r = self.run_it()
        self.assertEqual((r["copies"], r["lines"], r["changed"]), ([], [], False))

    def test_our_link(self):
        os.symlink(self.target, self.act)
        r = self.run_it()
        self.assertEqual(r["copies"][0]["kind"], "ours")
        self.assertEqual((r["lines"], r["changed"]), ([], False))
        self.assertEqual(os.readlink(self.act), self.target)

    def test_a_link_into_the_install_folder_stays(self):
        self.write(os.path.join(self.opt, "act-0.6.20"), NEW, 0o755)
        os.symlink(os.path.join(self.opt, "act-0.6.20"), self.act)
        r = self.run_it()
        self.assertEqual((r["copies"][0]["kind"], r["changed"], r["lines"]), ("ours", False, []))

    def test_an_old_file_is_linked_and_kept(self):
        self.write(self.act, OLD, 0o755)
        r = self.run_it()
        self.assertTrue(r["changed"])
        self.assertEqual(os.readlink(self.act), self.target)
        backups = os.listdir(os.path.join(self.opt, "old"))
        self.assertEqual(len(backups), 1)
        self.assertRegex(backups[0], r"^act\.%s\.[0-9a-f]{8}$" % NOW)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.opt, "old")).st_mode), 0o700)
        with open(os.path.join(self.opt, "old", backups[0])) as f:
            self.assertEqual(f.read(), OLD)
        self.assertIn("was an old copy (ACT 0.4.2): linked to %s (ACT 0.6.25), the old one kept at" % self.target,
                      r["lines"][0])
        # a second run: our link now, nothing more
        r = self.run_it()
        self.assertEqual((r["changed"], r["lines"]), (False, []))

    def test_a_link_elsewhere(self):
        other = os.path.join(self.d, "srv", "act")
        self.write(other, OLD, 0o755)
        os.symlink(other, self.act)
        r = self.run_it()
        self.assertEqual(os.readlink(self.act), self.target)
        kept = os.path.join(self.opt, "old", os.listdir(os.path.join(self.opt, "old"))[0])
        self.assertEqual(os.readlink(kept), other)                              # kept as the link it was
        self.assertIn("was a link to %s (ACT 0.4.2): linked to" % other, r["lines"][0])
        self.assertTrue(os.path.exists(other))                                  # the file it pointed to stays

    def test_check_mode_changes_nothing(self):
        self.write(self.act, OLD, 0o755)
        r = self.run_it(check=True)
        self.assertFalse(r["changed"])
        self.assertFalse(os.path.islink(self.act))
        self.assertFalse(os.path.exists(os.path.join(self.opt, "old")))
        self.assertIn("would be linked to %s (ACT 0.6.25)" % self.target, r["lines"][0])
        r = self.run_it(apply=False)                                            # the look before a run, the same
        self.assertFalse(os.path.islink(self.act))

    def test_delete(self):
        self.write(self.act, OLD, 0o755)
        r = self.run_it(action="delete")
        self.assertFalse(os.path.lexists(self.act))
        self.assertEqual(len(os.listdir(os.path.join(self.opt, "old"))), 1)
        self.assertIn("deleted, the old one kept at", r["lines"][0])

    def test_report(self):
        self.write(self.act, OLD, 0o755)
        r = self.run_it(action="report")
        self.assertFalse(r["changed"])
        self.assertFalse(os.path.islink(self.act))
        self.assertIn("was an old copy (ACT 0.4.2): left as it is (site_act_old_bin: report).", r["lines"][0])

    def test_not_a_file(self):
        os.makedirs(self.act)
        r = self.run_it()
        self.assertTrue(os.path.isdir(self.act))
        self.assertIn("not a file or a link", r["lines"][0])

    def test_the_installed_act_missing(self):
        os.unlink(self.target)
        self.write(self.act, OLD, 0o755)
        r = self.run_it()
        self.assertFalse(os.path.islink(self.act))
        self.assertIn("the installed ACT (%s) is not there: not linked" % self.target, r["lines"][0])
        # in Check, with the install to come: what the run would do
        r = self.run_it(check=True, installing=True)
        self.assertIn("would be linked", r["lines"][0])
        # a real run never links to an ACT that is not there, whatever it was told
        r = self.run_it(installing=True)
        self.assertFalse(os.path.islink(self.act))

    def test_python_too_old(self):
        self.write(self.act, OLD, 0o755)
        r = self.run_it(py=(False, "python3 here is 3.6: not linked, as typing act would fail"))
        self.assertFalse(os.path.islink(self.act))
        self.assertIn("python3 here is 3.6", r["lines"][0])
        r = self.run_it(action="delete", py=(False, "x"))                     # delete does not run it
        self.assertFalse(os.path.lexists(self.act))

    def test_python3_ok_reads_this_python(self):
        ok, why = m.python3_ok()
        self.assertEqual(ok, sys.version_info >= (3, 8), why)


class Users(Base):
    def config(self, user, model, mode=0o600):
        cfg = os.path.join(self.home, user, ".config", "act", "config.json")
        self.write(cfg, json.dumps({"provider": "genai", "genai": {"key": KEY, "model": model, "url": "https://x"}},
                                   indent=4), mode)
        return cfg

    def test_reported_never_the_key(self):
        cfg = self.config("jdoe", "gemini-4-argon")
        before = open(cfg).read()
        r = self.run_it()
        self.assertEqual(r["lines"], ["user %s's saved model is gemini-4-argon: `act` uses it when typed; "
                                      "the jobs pass their own." % m.user_of(os.path.join(self.home, "jdoe"),
                                                                             os.stat(cfg))])
        self.assertEqual(open(cfg).read(), before)                              # never written
        self.assertFalse(r["changed"])

    def test_the_jobs_model_or_none_is_quiet(self):
        self.config("a", "gemini-4-argon-withtools")
        self.config("b", "")
        self.assertEqual(self.run_it()["lines"], [])

    def test_fixed_only_when_asked(self):
        cfg = self.config("jdoe", "gemini-4-argon", 0o640)
        r = self.run_it(check=True, fix_user_model=True)
        self.assertIn("it would be set to gemini-4-argon-withtools", r["lines"][0])
        self.assertIn('"gemini-4-argon"', open(cfg).read())
        r = self.run_it(fix_user_model=True)
        self.assertTrue(r["changed"])
        data = json.load(open(cfg))
        self.assertEqual(data, {"provider": "genai", "genai": {"key": KEY, "model": "gemini-4-argon-withtools",
                                                                "url": "https://x"}})   # only that key
        self.assertEqual(stat.S_IMODE(os.stat(cfg).st_mode), 0o640)
        bak = cfg + "." + NOW + ".bak"
        self.assertEqual(json.load(open(bak))["genai"]["model"], "gemini-4-argon")
        self.assertEqual(stat.S_IMODE(os.stat(bak).st_mode), 0o640)

    def test_not_json(self):
        cfg = os.path.join(self.home, "x", ".config", "act", "config.json")
        self.write(cfg, "{ key: " + KEY)
        r = self.run_it()
        self.assertIn("are not JSON: not read", r["lines"][0])


class Python36(unittest.TestCase):
    def test_no_newer_syntax(self):
        src = open(os.path.join(REPO, "roles", "site_act", "library", "site_act_old_copies.py")).read()
        self.assertNotRegex(src, r"""\bf["']""")                                # f-strings: 3.6 has them, kept out anyway
        self.assertNotIn(":=", src)
        self.assertNotIn("capture_output", src)                                 # 3.7
        self.assertNotIn("text=True", src)                                      # 3.7


# The playbooks that run ACT only on the AAP side (the execution environment has no /usr/local/bin/act), and why
AAP_SIDE = {
    "playbooks/log_analysis.yml": "the analysis runs on localhost (delegate_to, run_once)",
    "playbooks/vm_log_analysis.yml": "on localhost",
}
# roles that call ACT only on the AAP side (evidence_* tasks, localhost) or only name a setting
AAP_SIDE_ROLES = {"vmware_vm", "servicenow_insights", "servicenow_war", "incident_docs", "log_analysis",
                  "aap_template_audit", "patch", "site_email", "site_findings", "vmware_ask_stack", "site_act"}
ACT_WORDS = re.compile(r"\b(site_act|act_triage|use_act)\b")


def load(path):
    with open(path) as f:
        return yaml.safe_load(f) or []


def text(path):
    with open(path) as f:
        return f.read()


def walk(node, out):
    """Every include_role / import_role (name, tasks_from), include_tasks / import_tasks and import_playbook below node."""
    if isinstance(node, dict):
        for k, v in node.items():
            short = k.split(".")[-1]
            if short in ("include_role", "import_role") and isinstance(v, dict):
                out.append(("role", v.get("name"), v.get("tasks_from") or "main"))
            elif short in ("include_tasks", "import_tasks"):
                out.append(("tasks", v if isinstance(v, str) else (v or {}).get("file"), None))
            elif short == "import_playbook":
                out.append(("playbook", v, None))
            elif k == "roles" and isinstance(v, list):
                for r in v:
                    out.append(("role", r if isinstance(r, str) else r.get("role") or r.get("name"), "main"))
            walk(v, out)
    elif isinstance(node, list):
        for v in node:
            walk(v, out)


def task_file(role, name):
    base = os.path.join(REPO, "roles", role, "tasks", name if name.endswith(".yml") else name + ".yml")
    if not os.path.exists(base):
        base = os.path.join(REPO, "vendor", "act", "ansible", "roles", role, "tasks", name + ".yml")
    return base


def reach(path, seen):
    """Every file a playbook or task file reaches (roles' tasks files, included task files, imported playbooks)."""
    if path in seen or not os.path.exists(path):
        return
    seen.add(path)
    out = []
    walk(load(path), out)
    for kind, name, tasks_from in out:
        if not name or "{{" in str(name):
            continue
        if kind == "role":
            reach(task_file(name, tasks_from), seen)
        elif kind == "tasks":
            reach(os.path.normpath(os.path.join(os.path.dirname(path), name)), seen)
        else:
            reach(os.path.normpath(os.path.join(os.path.dirname(path), name)), seen)


class EveryActJob(unittest.TestCase):
    OLD = os.path.join(REPO, "roles", "site_act", "tasks", "old_copies.yml")

    def act_playbooks(self):
        out = {}
        for f in sorted(os.listdir(os.path.join(REPO, "playbooks"))):
            if not f.endswith(".yml"):
                continue
            p = os.path.join(REPO, "playbooks", f)
            seen = set()
            reach(p, seen)
            uses = [s for s in seen if ACT_WORDS.search(text(s))]
            if uses:
                out["playbooks/" + f] = seen
        return out

    def test_every_host_act_job_reaches_the_check(self):
        missing = []
        for pb, seen in self.act_playbooks().items():
            if pb in AAP_SIDE:
                continue
            host_side = [s for s in seen if ACT_WORDS.search(text(s))
                         and not any("/roles/%s/" % r in s for r in AAP_SIDE_ROLES)
                         and "/vendor/act/ansible/roles/" not in s]       # act_triage: reached from both sides
            if host_side and self.OLD not in seen:
                missing.append("%s (%s)" % (pb, ", ".join(os.path.relpath(s, REPO) for s in sorted(host_side))))
        self.maxDiff = None
        self.assertEqual(missing, [], "these can run ACT on a host but never reach roles/site_act/tasks/old_copies.yml")

    def test_the_known_ones(self):
        jobs = self.act_playbooks()
        for pb in ("health_check", "database_health", "troubleshoot", "service_watch", "fapolicyd", "fapolicyd_denials",
                   "act_fix_approved", "act_old_copies"):
            self.assertIn(self.OLD, jobs.get("playbooks/%s.yml" % pb, set()), pb)

    def test_aap_side_list_is_true(self):
        for pb in AAP_SIDE:
            self.assertTrue(os.path.exists(os.path.join(REPO, pb)), pb)

    def test_act_on_or_off(self):
        # the check is never behind use_act or site_act_enabled
        for pb, seen in self.act_playbooks().items():
            for s in seen:
                for t in self.tasks_including_old(s):
                    when = json.dumps(t.get("when", ""))
                    self.assertNotRegex(when, r"use_act|site_act_enabled|_site_act_off", "%s: %s" % (s, when))

    def tasks_including_old(self, path):
        found = []

        def go(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    if k.split(".")[-1] in ("include_role", "import_role") and isinstance(v, dict) \
                            and v.get("name") == "site_act" and v.get("tasks_from") in ("old_copies", "old_copies.yml"):
                        found.append(node)
                    go(v)
            elif isinstance(node, list):
                for v in node:
                    go(v)
        go(load(path))
        return found


if __name__ == "__main__":
    unittest.main()
