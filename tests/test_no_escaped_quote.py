#!/usr/bin/env python3
"""1.112.3: an apostrophe in a message is never written \\' inside a single-quoted Jinja string of a task file. In a block
(>-, |) YAML keeps the backslash, and how Jinja then reads 'the portal\\'s' depends on the ansible-core release: one
printed a stray backslash, an older one failed the task (expected token ')', got 's') - the AD job's report database
line, a path no fake run took. Such text uses ’ (or double quotes). A backslash that is really meant ('\\\\', a Windows
path or a regex) is not this: only \\' followed by a letter is.
Run: python3 tests/test_no_escaped_quote.py"""
import glob
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAD = re.compile(r"(?<!\\)\\'[A-Za-z]")


class NoEscapedApostrophe(unittest.TestCase):
    def test_task_files(self):
        found = []
        for path in sorted(glob.glob(os.path.join(ROOT, "roles", "*", "tasks", "*.yml")) + glob.glob(os.path.join(ROOT, "playbooks", "*.yml"))):
            with open(path, encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    if BAD.search(line):
                        found.append("%s:%d" % (os.path.relpath(path, ROOT), n))
        self.assertEqual(found, [], "write the apostrophe as ’ (or use a double-quoted string)")


if __name__ == "__main__":
    unittest.main()
