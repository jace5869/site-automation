#!/usr/bin/env python3
"""A regex in a playbook or role reaches Python as Ansible reads it - and in a block (>-, |) or unquoted YAML value,
or inside single quotes, a backslash is not an escape: '\\s+' there arrives as \\\\s+ (a backslash, then s), so
regex_replace('\\s+', ' ') never matched a space and '_x001B_\\[[0-9;]*m' printed "FutureWarning: Possible nested
set" and left PowerShell's colour codes in the error text. In such a value one backslash is right ('\\s+' only in
double-quoted YAML).
- the scan: no regex (regex_replace / regex_search / regex_findall, match / search / regex tests), as Ansible sees
  it, has a doubled backslash before a regex escape (\\\\s, \\\\d, \\\\., \\\\[ ...). A literal backslash: [\\\\\\\\];
- the Windows error text (CLIXML, colour codes, line breaks) of collect_windows.yml, win_result.yml and
  answered.yml cleaned by ansible-playbook itself, with no FutureWarning.
Run: python3 tests/test_yaml_regex.py   (ANSIBLE_PLAYBOOK=... for another ansible-core)"""
import glob
import json
import os
import re
import subprocess
import tempfile
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
AP = os.environ.get("ANSIBLE_PLAYBOOK", "ansible-playbook")
CALL = re.compile(r"""(?:regex_(?:replace|search|findall)\(\s*|['"](?:match|search|regex)['"]\s*,\s*|is\s+(?:match|search|regex)\(\s*)"""
                  r"""('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")""")
# exactly two backslashes, then a character a regex escapes: the regex wants a literal backslash there - not meant
DOUBLED = re.compile(r"(?<!\\)\\\\(?=[sSdDwWbB.\[\](){}+*?|^$])")
CLIXML = ('#< CLIXML\n<Objs><S S="Error">_x001B_[31;1mGet-WinEvent : No events were found_x000D__x000A_</S>'
          '<S S="Error">   that match the criteria._x001B_[0m_x000D__x000A_</S></Objs>')


def strings(node):
    if isinstance(node, dict):
        for v in node.values():
            yield from strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from strings(v)
    elif isinstance(node, str):
        yield node


def doubled():
    found = []
    for path in sorted(glob.glob(os.path.join(REPO, "roles", "**", "*.yml"), recursive=True)
                       + glob.glob(os.path.join(REPO, "playbooks", "*.yml"))):
        with open(path, encoding="utf-8") as fh:
            try:
                doc = yaml.safe_load(fh)
            except yaml.YAMLError:
                continue
        for text in strings(doc):
            for lit in CALL.findall(text):
                if DOUBLED.search(lit):
                    found.append("%s: %s" % (os.path.relpath(path, REPO), lit))
    return found


def var_of(path, task_name, var):
    with open(os.path.join(REPO, path), encoding="utf-8") as fh:
        tasks = yaml.safe_load(fh)
    for t in tasks:
        if t.get("name") == task_name:
            return t["vars"][var]
    raise KeyError("%s: no task %r" % (path, task_name))


class Scan(unittest.TestCase):
    def test_self(self):
        self.assertTrue(DOUBLED.search(r"'\\s+'") and DOUBLED.search(r"'_x001B_\\[[0-9;]*m'") and DOUBLED.search(r"'^5\\.5'"))
        self.assertFalse(DOUBLED.search(r"'\s+'") or DOUBLED.search(r"'^.*[\\\\]'") or DOUBLED.search(r"'C:\\Program'"))

    def test_no_doubled_backslash(self):
        self.assertEqual(doubled(), [], "a regex with \\\\ in a block, unquoted or single-quoted YAML value reaches "
                         "Python with two backslashes: write one (\\s+), or a literal backslash as [\\\\\\\\]")


class WindowsErrorText(unittest.TestCase):
    def test_cleaned_by_ansible(self):
        exprs = {"collect_windows": var_of("roles/log_analysis/tasks/collect_windows.yml", "Logs | this server's lines", "_la_err")}
        with open(os.path.join(REPO, "roles/site_findings/tasks/win_result.yml"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(r"regex_replace('_x001B_\[[0-9;]*m', '')", text)
        with open(os.path.join(REPO, "roles/win_patch/tasks/answered.yml"), encoding="utf-8") as fh:
            self.assertIn(r"regex_replace('_x001B_\[[0-9;]*m', '') | regex_replace('_x000[AD]_', ' ') | regex_replace('\s+', ' ')", fh.read())
        with tempfile.TemporaryDirectory() as tmp:
            play = os.path.join(tmp, "p.yml")
            with open(play, "w") as fh:
                yaml.safe_dump([{"hosts": "localhost", "gather_facts": False,
                                 "vars": {"_la_wout": {"stderr": CLIXML}, "_la_err": exprs["collect_windows"]},
                                 "tasks": [{"ansible.builtin.debug": {"msg": "[{{ _la_err }}]"}}]}], fh, width=1000)
            env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="False", ANSIBLE_INVENTORY_UNPARSED_WARNING="False",
                       ANSIBLE_STDOUT_CALLBACK="default", PYTHONWARNINGS="always")
            out = subprocess.run([AP, play, "-i", "localhost,", "-c", "local"], env=env, capture_output=True, text=True, timeout=300)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertNotIn("nested set", out.stdout + out.stderr)
        msg = json.loads(re.search(r'"msg": ("(?:[^"\\]|\\.)*")', out.stdout).group(1))
        self.assertEqual(msg, "[Get-WinEvent : No events were found that match the criteria.]")


if __name__ == "__main__":
    unittest.main(verbosity=1)
