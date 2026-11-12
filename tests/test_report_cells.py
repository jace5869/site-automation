#!/usr/bin/env python3
"""1.28.0: a report's cells come out as the text the role gave, through Ansible's own templar - every look (the email's
report.html.j2, command, portal, glass, atlas, brief). On ansible-core 2.16 a template's output, a macro's too, goes
through a step that makes a text of exactly True, False, None, a number or {...} / [...] a Python value again: a cell
of "True" stopped the page (keep("True") came back a bool: '<' in kv). Each value in every column kind (from what it
holds, and named by the report: num, pct, path, wrap, mid, nw, long), a command column, a status column, a row and a
cell status, a summary tile and a line. tests/test_report_fit.py renders with plain Jinja and cannot see this.
Needs ansible-core: python3 tests/test_report_cells.py (ANSIBLE_PLAYBOOK=... for another one)"""
import html
import json
import os
import re
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
LOOKS = ("report", "report_command", "report_portal", "report_glass", "report_atlas", "report_brief")
# what 2.16 would turn into a Python value (and back to another text: {"a": 1} -> {'a': 1}, 1.0 -> 1.0, [1, 2] -> [1, 2])
VALUES = ["True", "False", "None", "1.0", "2", '{"a": 1}', "[1, 2]", "[1,2]", "{1:2}", "[true]", "['x']", "{}", "[]",
          "true", "FALSE", "True False"]
KINDS = ["", "num", "pct", "path", "wrap", "mid", "nw", "long"]
COLUMNS = ["Plain", "Number", "Percent", "Path", "Wrapped", "Middle", "Short", "Long", "Command", "Status"]


def report():
    rows = [[v] * len(COLUMNS) for v in VALUES]
    return {
        "title": "Cells as given", "subtitle": "every look", "status": "warning",
        "summary": [{"label": "Value %d" % i, "value": v} for i, v in enumerate(VALUES)],
        "sections": [
            {"title": "Kinds named", "status": "warning", "columns": COLUMNS, "kinds": KINDS + ["", ""], "rows": rows,
             "row_status": ["warning"] * len(rows), "cell_status": [["", "critical"] + [""] * (len(COLUMNS) - 2)] * len(rows),
             "lines": VALUES},
            {"title": "Kinds from the cells", "status": "info", "columns": COLUMNS, "rows": rows},
        ],
    }


PLAY = """- hosts: localhost
  gather_facts: false
  vars:
    _email_footer: AAP job 9
    _page_logo_cid: ""
    _page_runs: []
    report_email_header_text: Site automation report
  tasks:
    - name: each look
      ansible.builtin.copy:
        content: "{{ lookup('ansible.builtin.template', '%(templates)s/' ~ item ~ '.html.j2') }}"
        dest: "%(out)s/{{ item }}.html"
        mode: "0644"
      loop: %(looks)s
"""


def cells(page):
    """The text of each table cell of the two sections (tags out, entities read, spaces as one)."""
    out = []
    for td in re.findall(r"<td\b[^>]*>((?:(?!<td\b).)*?)</td>", page, re.S):     # the innermost: a layout cell holds a table
        td = re.sub(r'<span class="g-lab"[^>]*>.*?</span>', "", td)    # 1.46.0: glass's column label, hidden in a wide window
        t = re.sub(r"<[^>]+>", "", td)
        out.append(" ".join(html.unescape(t).split()))
    return out


class Cells(unittest.TestCase):
    def test_every_look_keeps_the_text(self):
        ap = os.environ.get("ANSIBLE_PLAYBOOK", "ansible-playbook")
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR") or "/tmp") as d:
            with open(os.path.join(d, "report.json"), "w") as f:
                json.dump({"_report": report()}, f)
            with open(os.path.join(d, "play.yml"), "w") as f:
                f.write(PLAY % {"templates": os.path.join(REPO, "roles", "site_email", "templates"), "out": d,
                                "looks": json.dumps(list(LOOKS))})
            r = subprocess.run([ap, os.path.join(d, "play.yml"), "-i", "localhost,", "-c", "local", "-e", "@" + os.path.join(d, "report.json")],
                               capture_output=True, text=True, timeout=600, cwd=REPO)
            self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
            for look in LOOKS:
                with open(os.path.join(d, look + ".html")) as f:
                    page = f.read()
                got = cells(page)
                for v in VALUES:
                    with self.subTest(look=look, value=v):
                        # each value in each column of both sections (a chip may write it Capitalized: FALSE -> False)
                        n = sum(1 for c in got if c == v or (v.isupper() and c == v.capitalize()))
                        self.assertGreaterEqual(n, 2 * len(COLUMNS), "%s: %r in %d cells of %d" % (look, v, n, 2 * len(COLUMNS)))
                # the summary tiles and the lines too: the text, not a Python value's ({"a": 1} never {'a': 1})
                self.assertIn('{"a": 1}', html.unescape(page), look)
                self.assertNotIn("{'a': 1}", html.unescape(page), look)


if __name__ == "__main__":
    unittest.main()
