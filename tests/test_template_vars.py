#!/usr/bin/env python3
"""aap/extra_vars/ is generated (scripts/gen_template_vars.py) and up to date; every file is valid
YAML as it is, and still valid - no setting twice - with every setting switched on.

    python tests/test_template_vars.py
"""
import glob
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class NoDuplicates(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(k, deep=deep) for k, _ in node.value]
        twice = sorted({k for k in keys if keys.count(k) > 1})
        if twice:
            raise yaml.constructor.ConstructorError(None, None, "set twice: %s" % ", ".join(map(str, twice)), node.start_mark)
        return super().construct_mapping(node, deep=deep)


def main():
    fails = []
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "gen_template_vars.py"), "--check"],
                       capture_output=True, text=True)
    if r.returncode:
        fails.append(r.stderr.strip() or r.stdout.strip())
    files = sorted(glob.glob(os.path.join(ROOT, "aap", "extra_vars", "*.yml")))
    for path in files:
        text = open(path, encoding="utf-8").read()
        name = os.path.relpath(path, ROOT)
        try:
            as_is = yaml.load(text, Loader=NoDuplicates)
            if as_is is not None and not isinstance(as_is, dict):
                fails.append("%s: not a dictionary as it is" % name)
            body = text.split("\n---\n", 1)[1]
            on = "\n".join(line[2:] if line.startswith("# ") and not line.startswith("# ----") else line
                           for line in body.splitlines())
            all_on = yaml.load(on, Loader=NoDuplicates)
            if not isinstance(all_on, dict) or not all_on:
                fails.append("%s: no settings when switched on" % name)
        except yaml.YAMLError as e:
            fails.append("%s: %s" % (name, str(e).splitlines()[0]))
    if fails:
        print("FAIL")
        for f in fails:
            print("  - " + f)
        return 1
    print("ok: aap/extra_vars up to date, %d files valid as they are and with every setting switched on" % len(files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
