#!/usr/bin/env python3
"""docs/VARIABLES_REFERENCE.md is generated from roles/*/defaults/main.yml: the parser reads what
it should, every setting is listed, and the committed file is up to date. Run: python tests/test_variable_reference.py"""
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
"""


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
    if total < 100:
        failures.append("only %d settings found; the parser lost some" % total)
    for name in re.findall(r"^([a-z_]+):", open(os.path.join(ROOT, "roles", "site_act", "defaults", "main.yml")).read(), re.M):
        if "`%s`" % name not in text:
            failures.append("site_act setting not documented: " + name)

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
