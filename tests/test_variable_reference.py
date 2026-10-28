#!/usr/bin/env python3
"""docs/VARIABLES_REFERENCE.md is generated from roles/*/defaults/main.yml: the parser reads what
it should, every setting is listed, and the committed file is up to date. And no setting is missing
from it: a name a playbook or role reads with "| default(...)" that nothing in playbooks/ or roles/
sets is a setting - it needs its line in a role's defaults file (or HOST_SETTINGS, for one host).
Run: python tests/test_variable_reference.py"""
import glob
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import gen_variable_reference as gen  # noqa: E402

SAMPLE = """---
# DEMO: what this role checks.
# Second header line.

# Above the setting: describes warn.
demo_warn_pct: 85        # inline note
demo_list:
  - a
  - 'b # not a comment'
demo_quoted: "x # still the value"   # the comment
demo_empty: ""
_demo_internal: "{{ demo_empty }}"
"""

# read with "| default(...)" and set by nothing in playbooks/ or roles/, yet not a setting
INTERNAL = {
    "act_triage": "the ACT triage job's hand-over (set_stats in vendor/act)",
    "act_triage_result": "what the vendored act_triage role leaves behind",
    "ad_lockout_now": "the tests' fixed clock (tests/ad)",
    "ad_report_now": "the tests' fixed clock (tests/ad)",
    "horizon_now": "the tests' fixed clock (tests/horizon)",
    "servicenow_war_now": "the tests' fixed clock (tests/servicenow/run_war_test.sh)",
}
READ = re.compile(r"(?<![\w.'\"\[])([a-z][a-z0-9_]*)\s*\|\s*default\(")
SETS = [re.compile(p, re.M) for p in (r"^\s*-?\s*([a-z_][a-z0-9_]*)\s*:", r"register:\s*([a-z_]\w*)",
                                       r"loop_var:\s*([a-z_]\w*)", r"\{%-?\s*(?:set|for)\s+([a-z_]\w*)",
                                       r"\{%-?\s*for\s+\w+\s*,\s*([a-z_]\w*)")]
SAMPLE_TASKS = """- name: demo
  vars:
    _x: "{{ demo_setting | default(1) }}"
  when: item.name | default('') and demo_set_here | default(false)
  register: demo_result
  # demo_commented | default(1)
- set_fact:
    demo_set_here: "{{ demo_result.rc | default(0) }}"
"""


def reads(text):
    """The names read with | default(...) - not item.x, not hostvars['h'].x, not in a comment line."""
    return set(READ.findall("\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))))


def sets(text):
    """The names a file gives a value: a YAML key (vars:, set_fact:, set_stats:), register:, loop_var:, {% set %}."""
    return {n for p in SETS for n in p.findall(text)}


def undocumented():
    """Names read with a default that no role default, no HOST_SETTINGS line and nothing in playbooks/ or roles/
    sets. Neither tests/ nor inventories/ count: a test or an example setting it does not document it."""
    files = sorted(glob.glob(os.path.join(ROOT, "playbooks", "*.yml")) + glob.glob(os.path.join(ROOT, "roles", "*", "*", "*")))
    known = {s["name"] for p in glob.glob(os.path.join(ROOT, "roles", "*", "defaults", "main.yml")) for s in gen.parse_defaults(p)[1]}
    known |= {name for _, name, _, _ in gen.HOST_SETTINGS} | set(INTERNAL)
    found, given = {}, set()
    for path in files:
        if not os.path.isfile(path) or "/files/" in path or "/library/" in path:
            continue
        text = open(path, encoding="utf-8", errors="replace").read()
        given |= sets(text)
        for name in reads(text):
            found.setdefault(name, set()).add(os.path.relpath(path, ROOT))
    return {n: sorted(f) for n, f in found.items()
            if n not in known and n not in given and not n.startswith(("ansible_", "awx_", "tower_"))}


def main():
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "main.yml")
        with open(path, "w") as fh:
            fh.write(SAMPLE)
        header, settings = gen.parse_defaults(path)
    by = {s["name"]: s for s in settings}
    checks = [
        (header == ["DEMO: what this role checks.", "Second header line."], "header comment"),
        (list(by) == ["demo_warn_pct", "demo_list", "demo_quoted", "demo_empty"], "setting names in order"),
        (by["demo_warn_pct"]["value"] == "85", "plain value without its inline comment"),
        (by["demo_warn_pct"]["doc"] == ["Above the setting: describes warn.", "inline note"], "comment above + inline"),
        (by["demo_list"]["value"] == "- a - 'b # not a comment'", "multi-line list joined, # in quotes kept"),
        (by["demo_quoted"]["value"] == '"x # still the value"', "# inside quotes is not a comment"),
        (by["demo_quoted"]["doc"] == ["the comment"], "inline comment after a quoted value"),
    ]
    for ok, what in checks:
        if not ok:
            failures.append("parser: " + what)

    text = open(os.path.join(ROOT, "docs", "VARIABLES_REFERENCE.md"), encoding="utf-8").read()
    total = 0
    for path in sorted(glob.glob(os.path.join(ROOT, "roles", "*", "defaults", "main.yml"))):
        for s in gen.parse_defaults(path)[1]:
            total += 1
            if "| `%s` |" % s["name"] not in text:
                failures.append("missing from the reference: " + s["name"])
            if not " ".join(s["doc"]).strip():      # every setting says what it does
                failures.append("no description (a comment above it, or '# ...' after it, in %s): %s"
                                % (os.path.relpath(path, ROOT), s["name"]))
    if total < 100:
        failures.append("only %d settings found; the parser lost some" % total)
    for name in re.findall(r"^([a-z][a-z_]*):", open(os.path.join(ROOT, "roles", "site_act", "defaults", "main.yml")).read(), re.M):
        if "`%s`" % name not in text:
            failures.append("site_act setting not documented: " + name)
    for _, name, _, _ in gen.HOST_SETTINGS:
        if "| `%s` |" % name not in text:
            failures.append("missing from the reference (settings of one host): " + name)

    if reads(SAMPLE_TASKS) != {"demo_setting", "demo_set_here"} or sets(SAMPLE_TASKS) & {"demo_setting"} \
            or not {"demo_set_here", "demo_result"} <= sets(SAMPLE_TASKS):
        failures.append("the undocumented-setting scan: reads %s, sets %s"
                        % (sorted(reads(SAMPLE_TASKS)), sorted(sets(SAMPLE_TASKS))))
    for name, where in sorted(undocumented().items()):
        failures.append("%s is read with a default (%s) but is in no role's defaults file: add it there, with a "
                        "comment saying what it does (one host only: HOST_SETTINGS in scripts/gen_variable_reference.py)"
                        % (name, ", ".join(where[:3])))

    proc = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "gen_variable_reference.py"), "--check"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        failures.append("the reference is out of date: " + proc.stderr.strip())

    if failures:
        print("FAIL")
        for f in failures:
            print("  -", f)
        return 1
    print("ok: variable reference parser, %d settings listed, file up to date" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
