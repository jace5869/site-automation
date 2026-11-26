#!/usr/bin/env python3
"""A stand-in for ACT for tests/containers (and the nested-container run): writes an act.result/1
file and records what it was given. Behaviour from $FAKE_ACT_CONFIG (or /etc/fake-act.json):
{"propose": ["cmd", ...], "summary": "..."}; "summaries": [...] answers one call each, in order (the config is
written back without the one used), then "summary". Records in $FAKE_ACT_OUT (or /tmp): fake-act-task.txt
(the task text), fake-act-allow.json (the --allow patterns), fake-act-evidence.txt (the file the
task says to `cat`, read while ACT runs - the playbook removes it afterwards), fake-act-stdin.txt
(what was piped in, with --allow-piped-upload: evidence mode), fake-act-env.json (ACT_STREAM,
GENAI_TIMEOUT, ACT_PROVIDER, GENAI_MODEL, ASKSAGE_MODEL, ACT_TOOLS, ACT_JSON_MODE, ACT_PREFILL as ACT got them).
The jobs ask several questions at once (plugins/action/site_act_ask.py): each call also keeps what was piped in as
fake-act-stdin.d/<n>.txt (n: the call's number), and the answers queue is read under a lock. "slow" (seconds)
makes each call that long, so a test sees the questions overlap (fake-act-times.log: start and end of each call).
"summaries_by": {"TEXT": "answer"} answers a call whose piped input holds TEXT (the first that matches), before the
queue: questions asked at the same time each get their own answer, whichever starts first."""
import fcntl
import json
import os
import re
import sys
import time

args = sys.argv[1:]
res = args[args.index("--result-file") + 1]
allow = [a.split("=", 1)[1] for a in args if a.startswith("--allow=")]
task = args[-1]
out = os.environ.get("FAKE_ACT_OUT", "/tmp")
lock = open(os.path.join(out, "fake-act.lock"), "a")
fcntl.flock(lock, fcntl.LOCK_EX)
with open(os.path.join(out, "fake-act-calls.log"), "a") as fh:     # one line per run: how often ACT was asked
    fh.write("call\n")
with open(os.path.join(out, "fake-act-calls.log")) as fh:
    number = len(fh.readlines())
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
with open(os.path.join(out, "fake-act-env.json"), "w") as fh:
    json.dump({k: os.environ.get(k) for k in ("ACT_STREAM", "GENAI_TIMEOUT", "ACT_PROVIDER", "GENAI_MODEL",
                                              "ASKSAGE_MODEL", "ACT_TOOLS", "ACT_JSON_MODE", "ACT_PREFILL")}, fh)
with open(os.path.join(out, "fake-act-masking.json"), "w") as fh:     # 1.104.0: the role's masking settings as ACT got them
    json.dump({k: os.environ.get(k) for k in ("ACT_PSEUDONYMIZE", "ACT_PSEUDO_NAMES", "XDG_RUNTIME_DIR")}, fh)
with open(os.path.join(out, "fake-act-env-names.json"), "w") as fh:    # 1.104.0: the names ACT got (never the values)
    json.dump(sorted(os.environ), fh)
cmdlines = {}
for process, pid in (("self", "self"), ("parent", os.getppid())):
    try:
        with open("/proc/%s/cmdline" % pid, "rb") as fh:
            cmdlines[process] = [os.fsdecode(arg) for arg in fh.read().split(b"\0")[:-1]]
    except OSError:
        cmdlines[process] = []
with open(os.path.join(out, "fake-act-cmdlines.json"), "w") as fh:
    json.dump(cmdlines, fh)
if os.environ.get("FAKE_ACT_SOURCE"):        # 1.104.0: each provider's key pool as ACT's own code reads it (_KEY_ENV ...)
    import ast
    src = open(os.environ["FAKE_ACT_SOURCE"]).read()
    tree = ast.parse(src)
    want = {"_KEY_ENV", "split_keys", "_unique_keys", "env_key_entries"}
    part = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in want)
            or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") in want for t in n.targets))]
    ns = {"os": os, "re": re}
    exec(compile(ast.Module(body=part, type_ignores=[]), "act", "exec"), ns)
    with open(os.path.join(out, "fake-act-pools.json"), "w") as fh:
        json.dump({p: [k for k, _label in ns["env_key_entries"](p)] for p in ns["_KEY_ENV"]}, fh)
if "--allow-piped-upload" in args:
    piped = sys.stdin.read()
    with open(os.path.join(out, "fake-act-stdin.txt"), "w") as fh:
        fh.write(piped)
    os.makedirs(os.path.join(out, "fake-act-stdin.d"), exist_ok=True)
    with open(os.path.join(out, "fake-act-stdin.d", "%d.txt" % number), "w") as fh:
        fh.write(piped)
cfg = {}
piped = piped if "--allow-piped-upload" in args else ""
path = os.environ.get("FAKE_ACT_CONFIG", "/etc/fake-act.json")
if os.path.exists(path):
    cfg = json.load(open(path))
summary = cfg.get("summary", "fake ACT: root cause = stopped on purpose")
by = [v for k, v in (cfg.get("summaries_by") or {}).items() if k in piped]
if by:
    summary = by[0]
elif cfg.get("summaries"):
    summary = cfg["summaries"].pop(0)
    with open(path, "w") as fh:
        json.dump(cfg, fh)
fcntl.flock(lock, fcntl.LOCK_UN)
if cfg.get("slow"):
    begun = time.time()
    time.sleep(float(cfg["slow"]))
    with open(os.path.join(out, "fake-act-times.log"), "a") as fh:
        fh.write("%.3f %.3f\n" % (begun, time.time()))
denied = [{"command": c, "pre_approvable": True, "reason": "needs approval"} for c in cfg.get("propose", [])]
result = {"schema": "act.result/1", "status": "needs_approval" if denied else "ok", "exit_code": 0,
          "summary": summary, "changed": False,
          "commands": [], "denied": denied, "files_changed": [], "stop_reason": ""}
with open(res, "w") as fh:
    json.dump(result, fh)
