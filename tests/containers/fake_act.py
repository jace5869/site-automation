#!/usr/bin/env python3
"""A stand-in for ACT for tests/containers (and the nested-container run): writes an act.result/1
file and records what it was given. Behaviour from $FAKE_ACT_CONFIG (or /etc/fake-act.json):
{"propose": ["cmd", ...], "summary": "..."}. Records in $FAKE_ACT_OUT (or /tmp): fake-act-task.txt
(the task text), fake-act-allow.json (the --allow patterns), fake-act-evidence.txt (the file the
task says to `cat`, read while ACT runs - the playbook removes it afterwards)."""
import json
import os
import re
import sys

args = sys.argv[1:]
res = args[args.index("--result-file") + 1]
allow = [a.split("=", 1)[1] for a in args if a.startswith("--allow=")]
task = args[-1]
out = os.environ.get("FAKE_ACT_OUT", "/tmp")
with open(os.path.join(out, "fake-act-task.txt"), "w") as fh:
    fh.write(task)
with open(os.path.join(out, "fake-act-allow.json"), "w") as fh:
    json.dump(allow, fh)
m = re.search(r"`cat (\S+)`", task)
if m:
    try:
        text = open(m.group(1)).read()
    except OSError as e:
        text = "UNREADABLE: %s" % e
    with open(os.path.join(out, "fake-act-evidence.txt"), "w") as fh:
        fh.write(text)
cfg = {}
path = os.environ.get("FAKE_ACT_CONFIG", "/etc/fake-act.json")
if os.path.exists(path):
    cfg = json.load(open(path))
denied = [{"command": c, "pre_approvable": True, "reason": "needs approval"} for c in cfg.get("propose", [])]
result = {"schema": "act.result/1", "status": "needs_approval" if denied else "ok", "exit_code": 0,
          "summary": cfg.get("summary", "fake ACT: root cause = stopped on purpose"), "changed": False,
          "commands": [], "denied": denied, "files_changed": [], "stop_reason": ""}
with open(res, "w") as fh:
    json.dump(result, fh)
