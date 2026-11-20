#!/usr/bin/env python3
"""A '\\n' in a Jinja string inside {{ }} reaches Jinja as two characters - a backslash, then n - unless the YAML value
is double-quoted ("...\\n..." is a real newline before Jinja sees it). In a block (>-, |), unquoted or single-quoted
value, Ansible escapes the backslash inside {{ }}: join('\\n') joined with the two characters, '\\n' ~ x started with
them, replace('\\n', ' ') and split('\\n') looked for them and never found a line break (the answer keys' evidence,
1.79.10). Measured on ansible-core 2.16 and 2.22:
- inside {% %} (set, for, if) and in a bare when / that / failed_when, '\\n' is a real newline;
- a regex (regex_replace / regex_search / regex_findall, the match / search / regex tests) reads the two characters
  as its own \\n escape: right as it is.
The scan: every '\\n' in a string literal inside {{ }} of a value in roles/ and playbooks/ must be a regex argument
or on ALLOWED (file, the text around it, why). A real newline: a task var (_nl: "\\n") or a double-quoted value.
The run: ansible-playbook shows the rule (the forms above) and renders seven fixed texts from their files (1.79.17).
Run: python3 tests/test_template_newlines.py   (ANSIBLE_PLAYBOOK=... for another ansible-core)"""
import glob
import json
import os
import re
import subprocess
import tempfile
import unittest

import jinja2
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
AP = os.environ.get("ANSIBLE_PLAYBOOK", "ansible-playbook")

REGEX_CALLS = {"regex_replace", "regex_search", "regex_findall", "match", "search", "regex"}   # every argument a regex
REGEX_FILTERS = {"regex_replace", "regex_search", "regex_findall"}                      # map('regex_replace', ...)
REGEX_TESTS = {"match", "search", "regex"}                    # select('match', ...), selectattr('a', 'search', ...)
# the call, the argument that names a regex test or filter, the arguments from then on that are regexes
BY_NAME = {"select": (0, 1, REGEX_TESTS), "reject": (0, 1, REGEX_TESTS),
           "selectattr": (1, 2, REGEX_TESTS), "rejectattr": (1, 2, REGEX_TESTS), "map": (0, 1, REGEX_FILTERS)}
BACKSLASH_N = re.compile(r"(?<!\\)(?:\\\\)*\\n")              # a \n not itself after a backslash

# (file, text the line holds, why it is right as it is). Empty: every one found was a line break meant, and fixed.
ALLOWED = []


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_multi_constructor("!", lambda loader, suffix, node: None)


def _scalars(node):
    if isinstance(node, yaml.MappingNode):
        for k, v in node.value:
            yield from _scalars(v)
    elif isinstance(node, yaml.SequenceNode):
        for v in node.value:
            yield from _scalars(v)
    elif isinstance(node, yaml.ScalarNode) and isinstance(node.value, str):
        yield node


def _regex_arg(frame):
    """Is the argument being read in frame (a call's parentheses) a regex: by the call, and by its position?"""
    if frame is None or frame["name"] is None:
        return False
    if frame["name"] in REGEX_CALLS:
        return True
    if frame["name"] in BY_NAME:
        at, start, names = BY_NAME[frame["name"]]
        return frame["argi"] >= start and frame["args"].get(at) in names
    return False


def literal_newlines(text):
    """Each string literal holding a two-character \\n inside {{ }} of text, not a regex argument: [(literal, call)]."""
    if "\\n" not in text or "{{" not in text:
        return []
    env = jinja2.Environment()
    try:
        tokens = list(env.lex(text))
    except jinja2.TemplateSyntaxError as e:
        return [("(not read: %s)" % e.message, "lex")]       # a template the scan cannot read is not passed
    found, inside, frames, prev = [], False, [], None
    for _, kind, value in tokens:
        if kind == "variable_begin":
            inside, frames, prev = True, [], None
            continue
        if kind == "variable_end":
            inside = False
            continue
        if not inside or kind in ("whitespace",):
            continue
        frame = frames[-1] if frames else None
        if kind == "operator" and value in "([{":
            if frame is not None:
                frame["fresh"] = False
            name = prev[1] if value == "(" and prev and prev[0] == "name" else None
            frames.append({"name": name, "argi": 0, "args": {}, "fresh": True})
        elif kind == "operator" and value in ")]}":
            if frames:
                frames.pop()
        elif kind == "operator" and value == "," and frame is not None:
            frame["argi"] += 1
            frame["fresh"] = True
        elif kind == "string":
            if frame is not None and frame["fresh"]:
                frame["args"][frame["argi"]] = value.strip("'\"")
            if BACKSLASH_N.search(value) and not _regex_arg(frame):
                found.append((value, frame["name"] if frame else None))
            if frame is not None:
                frame["fresh"] = False
        elif frame is not None:
            frame["fresh"] = False
        prev = (kind, value)
    return found


def scan():
    hits = []
    paths = sorted(glob.glob(os.path.join(REPO, "roles", "**", "*.yml"), recursive=True)
                   + glob.glob(os.path.join(REPO, "roles", "**", "*.yaml"), recursive=True)
                   + glob.glob(os.path.join(REPO, "playbooks", "**", "*.yml"), recursive=True)
                   + glob.glob(os.path.join(REPO, "playbooks", "**", "*.yaml"), recursive=True))
    for path in paths:
        rel = os.path.relpath(path, REPO)
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        try:
            docs = list(yaml.compose_all(src, Loader=_Loader))
        except yaml.YAMLError as e:
            hits.append("%s: not read as YAML (%s)" % (rel, str(e).splitlines()[0]))
            continue
        lines = src.splitlines()
        for doc in docs:
            for node in _scalars(doc):
                for lit, call in literal_newlines(node.value):
                    first, last = node.start_mark.line, node.end_mark.line
                    where = next((i for i in range(first, min(last + 1, len(lines))) if lit in lines[i]), first)
                    text = lines[where].strip() if where < len(lines) else ""
                    if any(f == rel and t in text for f, t, _ in ALLOWED):
                        continue
                    hits.append("%s:%d %s in %s(): %s" % (rel, where + 1, lit, call or "-", text[:100]))
    return hits


class Scan(unittest.TestCase):
    def test_self(self):
        self.assertEqual([c for _, c in literal_newlines("{{ x | join('\\n') }}")], ["join"])
        self.assertEqual([c for _, c in literal_newlines("{{ '\\n' ~ x }}")], [None])
        self.assertEqual([c for _, c in literal_newlines("{{ x | replace('\\n', ' ') }}")], ["replace"])
        self.assertEqual([c for _, c in literal_newlines("{{ x.split('\\n') }}")], ["split"])
        # by position (1.79.17, Codex): a test's name in a filter that is no select is no regex; a pattern after it is
        self.assertEqual([c for _, c in literal_newlines("{{ x | replace('match', '\\n') }}")], ["replace"])
        self.assertEqual([c for _, c in literal_newlines("{{ x | select('\\n') }}")], ["select"])
        self.assertEqual([c for _, c in literal_newlines("{{ x | map('replace', '\\n', ' ') }}")], ["map"])
        self.assertEqual([c for _, c in literal_newlines("{{ f(['match', '\\n']) }}")], [None])
        for meant in ("{{ x | regex_replace('[\\r\\n]+', ',') }}", "{{ x is search('\\n') }}",
                      "{{ x | reject('match', '^/[^\\r\\n]+\\Z') | list }}", "{% set nl = '\\n' %}{{ x | join(nl) }}",
                      "{%- for l in x.split('\\n') -%}{{ l }}{%- endfor %}", "{{ x | join('\\\\n') }}",
                      "printf '%s\\n' {{ x }}", "{{ x | join(_nl) }}",
                      "{{ rows | selectattr('msg', 'search', '\\n') | list }}",
                      "{{ rows | rejectattr('msg', 'match', '^a\\nb') | list }}",
                      "{{ x | map('regex_replace', '\\n', ' ') | list }}", "{{ x | select('search', '\\n') | list }}"):
            self.assertEqual(literal_newlines(meant), [], meant)
        self.assertEqual([c for _, c in literal_newlines("{{ x | join('\\n' }}")], ["lex"])   # not read: a hit

    def test_no_literal_newline(self):
        self.assertEqual(scan(), [], "a '\\n' inside {{ }} of a block, unquoted or single-quoted YAML value is two "
                         "characters, not a line break: use a task var (_nl: \"\\n\") or a double-quoted value - or, "
                         "when the two characters are meant, add it to ALLOWED with why")


def run_play(tasks, extra_vars=None):
    with tempfile.TemporaryDirectory() as tmp:
        play = os.path.join(tmp, "p.yml")
        with open(play, "w") as fh:
            fh.write(yaml.safe_dump([{"hosts": "localhost", "gather_facts": False, "vars": extra_vars or {},
                                      "tasks": tasks}], width=1000))
        env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="False",
                   ANSIBLE_INVENTORY_UNPARSED_WARNING="False", ANSIBLE_STDOUT_CALLBACK="default")
        out = subprocess.run([AP, play, "-i", "localhost,", "-c", "local"], env=env, capture_output=True, text=True,
                             timeout=300)
    return out


class AsAnsibleReadsIt(unittest.TestCase):
    """The rule above, measured: the same text as the scanned files (a folded value is a plain str here)."""

    def test_rule(self):
        exprs = {
            "join_braces": "{{ ['a', 'b'] | join('\\n') }}",
            "replace_braces": "{{ multi | replace('\\n', ' ') }}",
            "regex_braces": "{{ multi | regex_replace('\\n', ' ') }}",
            "set_nl": "{%- set nl = '\\n' -%}{{ ['a', 'b'] | join(nl) }}",
            "nl_var": "{{ ['a', 'b'] | join(_nl) }}",
        }
        tasks = [{"name": "rule", "vars": dict(exprs, _nl="\n"),
                  "ansible.builtin.debug": {"msg": "{{ {%s} | to_json }}" % ", ".join(
                      "'%s': %s" % (k, k) for k in exprs)}}]
        out = run_play(tasks, {"multi": "x\ny"})
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        msg = json.loads(json.loads(re.search(r'"msg": ("(?:[^"\\]|\\.)*")', out.stdout).group(1)))
        self.assertEqual(msg["join_braces"], "a\\nb")            # the two characters: why the scan exists
        self.assertEqual(msg["replace_braces"], "x\ny")          # nothing replaced
        self.assertEqual(msg["regex_braces"], "x y")
        self.assertEqual(msg["set_nl"], "a\nb")
        self.assertEqual(msg["nl_var"], "a\nb")


def _task(rel, starts):
    """The one task in rel named starts, or whose name starts with it (in blocks too)."""
    with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        data = yaml.load(fh, Loader=_Loader)            # noqa: S506 - SafeLoader, unknown tags dropped
    todo = list(data)                                   # a task list, or plays (their tasks below)
    found = []
    while todo:
        t = todo.pop(0)
        if not isinstance(t, dict):
            continue
        if str(t.get("name", "")).startswith(starts):
            found.append(t)
        for k in ("block", "rescue", "always", "tasks"):
            todo += t.get(k) or []
    exact = [t for t in found if t.get("name") == starts]
    if len(exact or found) != 1:
        raise AssertionError("%d tasks %r in %s" % (len(exact or found), starts, rel))
    return (exact or found)[0]


class TheFixedTasks(unittest.TestCase):
    """1.79.17: the fixed texts, read from the files and rendered by ansible-playbook with the task's own vars."""

    CASES = [
        # (file, task name start, key path, stub vars, expected)
        ("playbooks/disk_space_request.yml", "Space request | the text", ["ansible.builtin.set_fact", "_sr_text"],
         {"finding": {"data": {"mount": "/var", "pct": 91, "warn": 80, "crit": 90}}, "_sr_growth": {},
          "finding_job": {"id": 7}},
         "Request: more space on localhost, /var\n"
         "Now: 91% used. The health check warns at 80% and is critical at 90%.\n"
         "Growth: not known (the capacity history has less than a day, or none for this mount).\n"
         "Nothing in the tmp folders can be cleaned to make room. Reported by the health check, AAP job 7."),
        ("roles/check_stig/tasks/answer_keys_cross_act_one.yml", "Answer keys cross-check | ACT's answer",
         ["ansible.builtin.set_fact", "_akx_answers"],
         {"_akx_answers": {}, "_akx_ask": {"id": "k1", "v": "V-1"}, "_ea_ran": False, "_ea_reason": "no key"},
         {"k1": "unsure\nACT did not answer: no key"}),
        ("roles/site_findings/tasks/reach.yml", "Reached?", ["ansible.builtin.set_fact", "site_host_unreached"],
         {"_site_reach": {"unreachable": True, "msg": "Failed to connect\n  to the host via ssh:\r\n refused"}},
         "Failed to connect to the host via ssh: refused"),
        ("roles/fapolicyd_fix/tasks/apply.yml", "Fapolicyd fix | the result", ["vars", "_ff_lost_why"],
         {"_ff_ansible": {"unreachable": True, "msg": "Failed to connect\n  to the host via ssh"}},
         "unreachable: Failed to connect to the host via ssh"),
        ("roles/fapolicyd_rules/tasks/apply.yml", "Fapolicyd rules | the result", ["vars", "_fr_lost_why"],
         {"_fr_ansible": {"failed": True, "module_stderr": "line one\r\nline two\n"}}, "line one line two"),
        ("roles/site_email/tasks/main.yml", "Email | send the report", ["site_mail", "body"],
         {"_email_notice": "N", "_report_body": "B", "_act_off": True, "_act_old": ["o1", "o2"], "_email_footer": "F",
          "_email_web_url": "", "_banner": {}},
         "N\n\nB\n\nACT is off (site_act_enabled false).\n\nACT old copies:\no1\no2\n\n--\nF"),
        ("roles/net_config/tasks/store_git.yml", "Network config | commit", ["vars", "_ncg_msg"],
         {"_ncg_res": {"devices": [{"host": "sw1", "status": "changed"}]}, "_ncg_keep_ref": "", "awx_job_id": 5},
         re.compile(r"Backup \d{4}-\d\d-\d\d \d\d:\d\d UTC: 1 changed \(sw1\)\n\nAAP job 5\Z")),
    ]

    def test_fixed(self):
        tasks, want = [], {}
        for i, (rel, name, path, stubs, expected) in enumerate(self.CASES):
            t = _task(rel, name)
            value = t
            for k in path:
                value = value[k]
            own = {k: v for k, v in (t.get("vars") or {}).items() if k not in stubs and k != path[-1]}
            tasks.append({"name": "case %d" % i, "vars": dict(own, **stubs, _case=value),
                          "ansible.builtin.debug": {"msg": "{{ {'case': %d, 'out': _case} | to_json }}" % i}})
            want[i] = (rel, expected)
        old = os.environ.get("ANSIBLE_FILTER_PLUGINS")
        os.environ["ANSIBLE_FILTER_PLUGINS"] = os.path.join(REPO, "plugins", "filter")
        try:
            out = run_play(tasks)
        finally:
            if old is None:
                os.environ.pop("ANSIBLE_FILTER_PLUGINS")
            else:
                os.environ["ANSIBLE_FILTER_PLUGINS"] = old
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        got = {}
        for m in re.finditer(r'"msg": ("(?:[^"\\]|\\.)*")', out.stdout):
            d = json.loads(json.loads(m.group(1)))
            got[d["case"]] = d["out"]
        for i, (rel, expected) in want.items():
            with self.subTest(rel):
                if isinstance(expected, re.Pattern):
                    self.assertRegex(got.get(i, ""), expected, rel)
                else:
                    self.assertEqual(got.get(i), expected, rel)


if __name__ == "__main__":
    unittest.main(verbosity=1)
