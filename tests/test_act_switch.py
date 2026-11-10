#!/usr/bin/env python3
"""site_act_enabled (1.3.0): false = no job asks ACT - no request, no endpoint, no error. Every place that calls ACT,
walked here, so that a role added later cannot miss the switch:

- the ways into roles/site_act: main.yml (site_act_result), evidence_analysis.yml and evidence_batches.yml (_ea_*) check
  the switch themselves before anything is asked; run.yml and evidence_ask.yml (what asks) are only theirs;
- env.yml only resolves the settings (and _site_act_off): a file that includes it and then runs ACT itself must check
  _site_act_off (or site_act_enabled) before it does;
- ACT's own role (act_triage) and ACT's playbooks (vendor/act) are run only from roles/site_act, or from a file that checks
  the switch; the model's address (GENAI_URL ...) only from such a file too;
- a job that fails when ACT did not answer (an assert or fail about ACT) does not fail for ACT off (_ea_off / the switch).
Run: python tests/test_act_switch.py"""
import glob
import os
import re
import sys
import unittest

import yaml

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SITE_ACT = os.path.join("roles", "site_act", "tasks")
GUARDED = {"main.yml", "evidence_analysis.yml", "evidence_batches.yml"}     # check the switch themselves
INTERNAL = {"run.yml", "evidence_ask.yml", "evidence_one_batch.yml"}       # only from the guarded ones
SWITCH = re.compile(r"\b(_site_act_off|site_act_enabled)\b")
# files that name ACT but ask it nothing (each with the reason)
NOT_A_CALL = {
    "roles/win_act/tasks/main.yml": "installs act.ps1 on Windows servers; asks no model",
    "playbooks/win_act_install.yml": "the job of roles/win_act",
    "playbooks/act_fix_approved.yml": "runs the fixes a person approved; asks no model",
    "roles/service_watch/tasks/apply.yml": "runs the approved fix; asks no model",
    "roles/service_watch/tasks/record.yml": "records act_triage_result; asks no model",
}
ENDPOINT = re.compile(r"\b(GENAI_URL|GENAI_BETA_URL|ASKSAGE_URL|chat/completions)\b")


def rel(p):
    return os.path.relpath(p, REPO).replace(os.sep, "/")


def yaml_files():
    pats = ["roles/*/tasks/**/*.yml", "roles/*/handlers/*.yml", "playbooks/*.yml", "playbooks/**/*.yml"]
    out = set()
    for p in pats:
        out.update(glob.glob(os.path.join(REPO, p), recursive=True))
    return sorted(f for f in out if "/group_vars/" not in f and "/host_vars/" not in f)


def load(path):
    with open(path) as f:
        return yaml.safe_load(f) or []          # a file that does not parse fails here (and in the job)


def walk(items, conds=()):
    """Every task (and play) with the conditions of the blocks, plays and includes around it: (task, [when ...])."""
    for t in items if isinstance(items, list) else []:
        if not isinstance(t, dict):
            continue
        w = t.get("when")
        here = list(conds) + ([w] if isinstance(w, str) else list(w) if isinstance(w, list) else [str(w)] if w is not None else [])
        yield t, here
        for k in ("block", "rescue", "always", "tasks", "pre_tasks", "post_tasks", "handlers"):
            if isinstance(t.get(k), list):
                for x in walk(t[k], here):
                    yield x
        for r in t.get("roles") or []:
            if isinstance(r, dict):
                yield r, here
            elif isinstance(r, str):
                yield {"role": r}, here


def role_use(t):
    """(role, tasks_from) of an include_role / import_role / roles: entry, else (None, None)."""
    for k in ("ansible.builtin.include_role", "include_role", "ansible.builtin.import_role", "import_role"):
        if isinstance(t.get(k), dict):
            return t[k].get("name"), t[k].get("tasks_from") or "main.yml"
    if "role" in t and isinstance(t["role"], str):
        return t["role"], "main.yml"
    return None, None


def act_uses():
    """[(file, task, conditions, kind, detail)] - every place a task file or playbook reaches ACT."""
    out = []
    for f in yaml_files():
        doc = load(f)
        for t, conds in walk(doc):
            name, tf = role_use(t)
            if name == "site_act":
                out.append((rel(f), t, conds, "site_act", os.path.basename(str(tf))))
            elif name == "act_triage":
                out.append((rel(f), t, conds, "act_triage", ""))
            for k in ("ansible.builtin.import_playbook", "import_playbook"):
                if isinstance(t.get(k), str) and "vendor/act" in t[k]:
                    out.append((rel(f), t, conds, "act_playbook", t[k]))
    return out


def text(f):
    with open(os.path.join(REPO, f)) as h:
        return h.read()


class TheSiteActWaysIn(unittest.TestCase):
    def test_the_default_is_on(self):
        d = yaml.safe_load(text("roles/site_act/defaults/main.yml"))
        self.assertIs(d["site_act_enabled"], True)

    def test_each_guarded_way_in_checks_the_switch_before_it_asks(self):
        for f in sorted(GUARDED):
            tasks = load(os.path.join(REPO, SITE_ACT, f))
            seen_switch = False
            for t, conds in walk(tasks):
                c = " ".join(conds)
                if SWITCH.search(c) or SWITCH.search(str(t.get("ansible.builtin.set_fact", ""))):
                    seen_switch = True
                inc = t.get("ansible.builtin.include_tasks") or t.get("ansible.builtin.include_role", {})
                inc = inc if isinstance(inc, str) else (inc or {}).get("name", "")
                if inc in INTERNAL or inc in ("act_triage",):
                    self.assertTrue(SWITCH.search(c) or "_ea_off" in c, "%s: %s runs without the switch in its when (%s)" % (f, inc, c))
            self.assertTrue(seen_switch, "%s never reads site_act_enabled" % f)

    def test_the_off_result_says_so_in_one_line(self):
        main = text(os.path.join(SITE_ACT, "main.yml"))
        self.assertIn('status: "off"', main)
        self.assertIn("ACT is off (site_act_enabled false).", main)
        for f in ("evidence_analysis.yml",):
            s = text(os.path.join(SITE_ACT, f))
            self.assertIn("_ea_reason: ACT is off (site_act_enabled false).", s)
            self.assertIn("_ea_ran: false", s)

    def test_env_yml_says_off(self):
        self.assertRegex(text(os.path.join(SITE_ACT, "env.yml")), r"_site_act_off: \"\{\{ not site_act_enabled \| bool \}\}\"")

    def test_the_inner_files_are_only_the_guarded_ones(self):
        for f, t, conds, kind, detail in act_uses():
            if kind == "site_act":
                self.assertNotIn(detail, INTERNAL, "%s includes site_act's %s - use main.yml, evidence_analysis.yml or "
                                 "evidence_batches.yml (they check site_act_enabled)" % (f, detail))
        for f in yaml_files():
            if rel(f).startswith(SITE_ACT.replace(os.sep, "/")):
                continue
            for inc in INTERNAL:
                self.assertNotRegex(text(rel(f)), r"tasks_from:\s*%s" % re.escape(inc), rel(f))


class EveryCaller(unittest.TestCase):
    def test_there_are_callers(self):
        kinds = [k for _, _, _, k, _ in act_uses()]
        self.assertGreater(kinds.count("site_act"), 15)
        self.assertIn("act_triage", kinds)
        self.assertIn("act_playbook", kinds)

    def test_every_call_is_guarded(self):
        bad = []
        for f, t, conds, kind, detail in act_uses():
            if f.startswith("roles/site_act/") or f in NOT_A_CALL:
                continue
            if kind == "site_act" and detail in GUARDED:
                continue                                  # site_act checks the switch itself
            # env.yml then ACT run here, act_triage, ACT's playbooks: the file checks the switch before it asks
            if kind == "site_act" and detail == "env.yml" or kind in ("act_triage", "act_playbook"):
                if kind == "act_playbook" and not SWITCH.search(" ".join(conds)):
                    bad.append("%s: %s imported without the switch in its when" % (f, detail))
                elif not SWITCH.search(text(f)):
                    bad.append("%s: %s %s - the file never checks _site_act_off / site_act_enabled" % (f, kind, detail))
                continue
            bad.append("%s: site_act %s - not a way in this test knows" % (f, detail))
        self.assertEqual(bad, [], "\n".join(bad))

    def test_act_triage_has_the_switch_in_its_when(self):
        """act_triage outside site_act: its when (or its block's) names the switch, or a fact set from it in the file."""
        for f, t, conds, kind, detail in act_uses():
            if kind != "act_triage" or f.startswith("roles/site_act/"):
                continue
            c = " ".join(conds)
            facts = set(k for x, _ in walk(load(os.path.join(REPO, f))) for k, v in (x.get("ansible.builtin.set_fact") or {}).items()
                        if SWITCH.search(str(v)))
            self.assertTrue(SWITCH.search(c) or any(x in c for x in facts),
                            "%s: act_triage runs when %r - none of it is the switch" % (f, c))

    def test_the_models_address_only_where_the_switch_is_read(self):
        for f in yaml_files():
            r = rel(f)
            if r.startswith("roles/site_act/") or "/group_vars/" in r:
                continue
            if ENDPOINT.search(text(r)):
                self.assertRegex(text(r), SWITCH, "%s names the model's address but never reads the switch" % r)

    def test_no_failure_for_act_off(self):
        """An assert or fail about ACT not answering skips itself for ACT off."""
        bad = []
        for f in yaml_files():
            r = rel(f)
            for t, conds in walk(load(f)):
                body = t.get("ansible.builtin.assert") or t.get("ansible.builtin.fail")
                if not isinstance(body, dict):
                    continue
                msg = str(body.get("fail_msg") or body.get("msg") or "")
                that = str(body.get("that") or "")
                if re.search(r"\bACT (did not|made|analy)", msg) or re.search(r"_ea_ran|_la_ran|_vm_al_ran", that):
                    c = " ".join(conds)
                    if "_ea_off" not in c and not SWITCH.search(c):
                        bad.append("%s: %s" % (r, t.get("name")))
        self.assertEqual(bad, [], "fails the job with ACT off:\n" + "\n".join(bad))

    def test_the_not_a_call_list_is_current(self):
        for f in NOT_A_CALL:
            self.assertTrue(os.path.exists(os.path.join(REPO, f)), f)


class TheReport(unittest.TestCase):
    def test_site_email_says_act_off(self):
        s = text("roles/site_email/tasks/main.yml")
        self.assertIn("_site_act_off", s)
        self.assertIn("report_act_off(_act_off | bool)", s)
        for t in ("report.html.j2", "report_command.html.j2", "report_portal.html.j2", "report_glass.html.j2"):
            self.assertIn("ACT is off (site_act_enabled false).", text("roles/site_email/templates/" + t), t)

    def test_the_filter(self):
        sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
        import site_filters as sf
        r = sf.report_act_off({"title": "t", "sections": [{"title": "a"}]}, True)
        self.assertEqual(r["sections"][-1], {"title": "ACT", "status": "info", "text": "ACT is off (site_act_enabled false)."})
        self.assertEqual(sf.report_act_off({"title": "t"}, False), {"title": "t"})


if __name__ == "__main__":
    unittest.main(verbosity=1)
