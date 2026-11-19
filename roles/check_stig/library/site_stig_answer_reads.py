#!/usr/bin/python
# -*- coding: utf-8 -*-
"""1.77.0 (playbooks/stig_answer_keys.yml, the cross-check): the read-only evidence ACT gets for a rule on this host. The
commands of DISA's check text that fit the read-only list - else the code's own calls (the read-only check's exact
command lines) - each checked here against the same fixed grammar as the read-only check (site_stig_answer_host's
gate), then run once, read-only, outside any sandbox (as 1.76.0's replay). Never the code itself, never a shell: each
command is a literal argv, a pipeline only of such commands. Writes nothing. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_stig_answer_reads
short_description: The read-only evidence for STIG answer keys' cross-check - DISA's commands (or the code's), checked, run once
description:
  - For each rule - the command lines of DISA's check text (after its "$" or "#" prompt, sudo dropped) that fit the
    read-only list; when none fits, the code's own calls (the read-only check's exact command lines).
  - Every command is checked against the fixed grammar of the read-only check (stat -c, sysctl -n, rpm -q, systemctl
    is-enabled / is-active / show -p, getent, id, grep with its listed flags, find without -exec / -delete / -fprint,
    auditctl -l, getenforce, sestatus) and run as a literal argv - never through a shell. A pipeline (|) only of such
    commands; 2>/dev/null is the only redirection taken. Anything else is not run, and said. Only the last command's
    stderr is read (its output when it printed nothing else, and not after 2>/dev/null).
  - The output is cut at out_max bytes; password hashes, keys and secret values are removed (site_redact).
  - Writes nothing.
options:
  rules: {description: "[{id, check_text, calls: [[program, args...]] (the code's, from the read-only check)}]", type: list, elements: dict, default: []}
  timeout: {description: Seconds each command may run., type: int, default: 30}
  budget: {description: Seconds all of a host's commands may take together; past it the rest is not run., type: int, default: 600}
  out_max: {description: Bytes kept of each command's output., type: int, default: 4000}
author: site automation
'''

RETURN = r'''
reads: {description: "{id: {from: DISA's / the code's / '', ran: [{cmd, rc, out}], why, refused: [command lines not run, with why]}}", type: dict, returned: always}
'''

import glob
import re
import shlex
import subprocess
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_redact import head, redact, run_main

# the read-only check's grammar (site_stig_answer_host.py GATE_PS1, Gram) - the same lists, matched whole
RX_PATH = re.compile(r"^/[A-Za-z0-9._/*?+@,:-]*$")
RX_PKG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
RX_QF = re.compile(r"^(?:%\{(?:NAME|VERSION|RELEASE|ARCH|EPOCH|EPOCHNUM|NEVRA|NEVR|NVRA|NVR|VENDOR|INSTALLTIME|BUILDTIME)(?::date)?\}"
                   r"|[A-Za-z0-9 ._,:=/|-]|\\n)+$")
RX_KEY = re.compile(r"^[a-z0-9_][a-z0-9_.]*$")
RX_STATFMT = re.compile(r"^(?=.*%)(?:%[UGaAugn]|[ :])+$")
RX_UNIT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._:\\-]*$")
RX_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9._-]*$")
FIND_FLAGS = ("-xdev", "-mount", "-nouser", "-nogroup", "-prune", "-o", "-or", "-a", "-and", "-not", "!", "(", ")", "-print",
              "-depth", "-empty")
_FN = r"^[A-Za-z0-9._*?/\[\]^!+@,:~][A-Za-z0-9._*?/\[\]^!+@,:~-]*$"
FIND_VALS = {"-type": r"^[fdlbcps](?:,[fdlbcps])*$",
             "-perm": r"^[-/]?(?:[0-7]{1,4}|[ugoa]*[+=-][rwxXst]+(?:,[ugoa]*[+=-][rwxXst]+)*)$",
             "-uid": r"^[+-]?[0-9]+$", "-gid": r"^[+-]?[0-9]+$", "-user": RX_NAME.pattern, "-group": RX_NAME.pattern,
             "-name": _FN, "-iname": _FN, "-path": _FN, "-ipath": _FN, "-maxdepth": r"^[0-9]+$", "-mindepth": r"^[0-9]+$",
             "-fstype": r"^[a-z0-9]+$", "-printf": r"^(?:%[pfugmMUGhklsP]|[A-Za-z0-9 :,=._-]|\\n)+$"}
GREP_FLAGS = ("-i", "-r", "-h", "-E", "-q", "-s", "-n", "-l", "-c", "-v", "-w", "-x")
# a program as DISA writes it -> the path the read-only check knows
PROGRAMS = {"stat": "/usr/bin/stat", "find": "/usr/bin/find", "sysctl": "/usr/sbin/sysctl", "rpm": "/usr/bin/rpm",
            "systemctl": "/usr/bin/systemctl", "getenforce": "/usr/sbin/getenforce", "sestatus": "/usr/sbin/sestatus",
            "grep": "/usr/bin/grep", "getent": "/usr/bin/getent", "id": "/usr/bin/id", "auditctl": "/usr/sbin/auditctl"}
KNOWN = set(PROGRAMS.values()) | {"/usr/bin/sysctl"}


def _m(rx, s):
    rx = rx if hasattr(rx, "match") else re.compile(rx)
    return "\n" not in s and rx.match(s) is not None


def gram(prog, a):
    """One command (program, arguments) -> '' when the read-only check's grammar takes it, else why not."""
    n = len(a)
    if prog == "/usr/bin/rpm":
        if (n == 2 and a[0] == "-q" and _m(RX_PKG, a[1])) or \
                (n == 4 and a[0] == "-q" and a[1] in ("--qf", "--queryformat") and _m(RX_QF, a[2]) and _m(RX_PKG, a[3])):
            return ""
        return "rpm: only -q PACKAGE, or -q --qf FORMAT PACKAGE"
    if prog in ("/usr/sbin/sysctl", "/usr/bin/sysctl"):
        return "" if n == 2 and a[0] == "-n" and _m(RX_KEY, a[1]) else "sysctl: only -n KEY"
    if prog == "/usr/sbin/auditctl":
        return "" if a == ["-l"] else "auditctl: only -l (the list)"
    if prog in ("/usr/sbin/getenforce", "/usr/sbin/sestatus"):
        return "" if n == 0 else "%s: no arguments" % prog.rsplit("/", 1)[1]
    if prog == "/usr/bin/stat":
        if n >= 3 and a[0] == "-c" and _m(RX_STATFMT, a[1]) and all(_m(RX_PATH, x) for x in a[2:]):
            return ""
        return "stat: only -c FORMAT (of %U %G %a %A %u %g %n) and paths (/...)"
    if prog == "/usr/bin/systemctl":
        if (n == 2 and a[0] in ("is-enabled", "is-active") and _m(RX_UNIT, a[1])) or \
                (n == 4 and a[0] == "show" and a[1] == "-p" and _m(r"^[A-Za-z]+$", a[2]) and _m(RX_UNIT, a[3])):
            return ""
        return "systemctl: only is-enabled UNIT, is-active UNIT or show -p PROPERTY UNIT"
    if prog == "/usr/bin/getent":
        if 1 <= n <= 2 and a[0] in ("passwd", "group", "shadow", "gshadow") and (n == 1 or _m(RX_NAME, a[1])):
            return ""
        return "getent: only passwd, group, shadow or gshadow, and a name"
    if prog == "/usr/bin/id":
        i = 0
        while i < n and a[i] in ("-u", "-g", "-G", "-n"):
            i += 1
        return "" if i == n or (i == n - 1 and _m(RX_NAME, a[i])) else "id: only -u, -g, -G, -n and a user name"
    if prog == "/usr/bin/grep":
        i = 0
        while i < n and a[i] in GREP_FLAGS:
            i += 1
        pats = 0
        while i + 1 < n and a[i] == "-e":
            i += 2
            pats += 1
        if pats == 0:
            if i < n and not a[i].startswith("-") and "\n" not in a[i]:
                i += 1
            else:
                return "grep: the flags (%s, each alone), a pattern (or -e PATTERN) and paths (/...)" % " ".join(GREP_FLAGS)
        while i < n:
            if not _m(RX_PATH, a[i]):
                return "grep: %s is not a path (/...)" % a[i]
            i += 1
        return ""
    if prog == "/usr/bin/find":
        i = 0
        while i < n and _m(RX_PATH, a[i]):
            i += 1
        if i == 0:
            return "find: a path (/...) first"
        while i < n:
            t = a[i]
            if t in FIND_FLAGS:
                i += 1
                continue
            if t in FIND_VALS:
                if i + 1 < n and _m(FIND_VALS[t], a[i + 1]):
                    i += 2
                    continue
                return "find: %s with a value it does not take" % t
            return "find: %s is not allowed" % t
        return ""
    return "not an allowed program"


def disa_lines(text):
    """DISA's check text -> its command lines: after a "$" or "#" prompt (a prompt typed twice too)."""
    out = []
    for ln in (text or "").splitlines():
        m = re.match(r"^\s*(?:[$#]\s+)+(.+?)\s*$", ln)
        if not m:
            continue
        cmd = m.group(1)
        if cmd not in out:
            out.append(cmd)
    return out


def _normal(prog, a):
    """The few ways DISA writes a command the grammar takes in its own form: sysctl KEY (-> sysctl -n KEY), grep's
    flags run together (-ir -> -i -r)."""
    if prog in ("/usr/sbin/sysctl", "/usr/bin/sysctl") and len(a) == 1 and _m(RX_KEY, a[0]):
        return ["-n", a[0]]
    if prog == "/usr/bin/grep":
        b = []
        for x in a:
            if re.match(r"^-[A-Za-z]{2,}$", x) and all(("-" + c) in GREP_FLAGS for c in x[1:]):
                b += ["-" + c for c in x[1:]]
            else:
                b.append(x)
        return b
    return a


class Pipeline(list):
    """A pipeline's argvs; quiet: the stages a 2>/dev/null followed (1.77.0f: their stderr goes to /dev/null)."""
    quiet = frozenset()


def pipeline_of(line):
    """One command line -> ([[argv], ...] a pipeline the grammar takes, '') or (None, why not)."""
    if re.search(r"`|\$\(|\$\{", line):
        return None, "a substitution (` or $( or ${)"
    try:
        lx = shlex.shlex(line, posix=True, punctuation_chars="|&;<>()")
        lx.whitespace_split = True
        toks = list(lx)
    except ValueError as e:
        return None, "it does not parse (%s)" % e
    stages, cur, i, quiet = [], [], 0, set()
    while i < len(toks):
        t = toks[i]
        if t == "|":
            stages.append(cur)
            cur = []
        elif t == "2" and i + 2 < len(toks) and toks[i + 1] == ">" and toks[i + 2] == "/dev/null":
            quiet.add(len(stages))
            i += 2
        elif t in ("2>", ) and i + 1 < len(toks) and toks[i + 1] == "/dev/null":
            quiet.add(len(stages))
            i += 1
        elif re.match(r"^[|&;<>()]+$", t):
            return None, "a shell operator (%s)" % t
        else:
            cur.append(t)
        i += 1
    stages.append(cur)
    out = []
    for s in stages:
        while s and s[0] == "sudo":         # sudo and its options: the job runs it as root already
            s = s[1:]
            while s and s[0].startswith("-"):
                s = s[1:]
        if not s:
            return None, "an empty command in the pipeline"
        prog = PROGRAMS.get(s[0], s[0])
        if prog not in KNOWN:
            return None, "%s is not on the read-only list" % s[0]
        a = _normal(prog, s[1:])
        why = gram(prog, a)
        if why:
            return None, why
        out.append([prog] + a)
    out = Pipeline(out)
    out.quiet = frozenset(quiet)
    return out, ""


def _expand(argv):
    """grep's and stat's paths with * ? [ as a shell would give them (sorted, at most 64); none matches: as written."""
    if argv[0] not in ("/usr/bin/grep", "/usr/bin/stat"):
        return argv
    out = [argv[0]]
    for x in argv[1:]:
        if x.startswith("/") and re.search(r"[*?\[]", x):
            got = sorted(glob.glob(x))[:64]
            out += got or [x]
        else:
            out.append(x)
    return out


READ_MAX = 1 << 20      # 1.77.0c: the most of one command's output that is redacted (then cut to out_max)


def mask(text):
    """The output as it may leave the host: every secret value removed (plugins/module_utils/site_redact.py)."""
    return redact(text)


def run_pipeline(stages, seconds, out_max):
    """A pipeline of literal argvs, read-only, no shell -> {cmd, rc, out}. Only the last stage's stderr is read (the
    output when it printed nothing else); an earlier stage's goes to /dev/null - a pipe nobody reads would fill and
    stop the pipeline (1.77.0f) - and so does a stage's that 2>/dev/null followed."""
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "SYSTEMD_PAGER": "", "SYSTEMD_COLORS": "0"}
    quiet = getattr(stages, "quiet", frozenset())
    stages = [_expand(s) for s in stages]
    cmd = " | ".join(" ".join(shlex.quote(x) for x in s) + (" 2>/dev/null" if n in quiet else "") for n, s in enumerate(stages))
    # 1.77.0h: the command as shown - DISA's words kept, a value it quotes (grep -e name=value) and what a * expanded to
    # redacted; the argvs run as they are
    shown = redact(cmd, backstop=False)
    procs, prev = [], subprocess.DEVNULL
    try:
        for i, s in enumerate(stages):
            last = i == len(stages) - 1
            p = subprocess.Popen(s, stdin=prev, stdout=subprocess.PIPE, env=env, close_fds=True,
                                 stderr=subprocess.PIPE if last and i not in quiet else subprocess.DEVNULL)
            if procs:
                procs[-1].stdout.close()        # the earlier stage gets SIGPIPE when this one stops reading
            procs.append(p)
            prev = p.stdout
        out, err = procs[-1].communicate(timeout=seconds)
        for p in procs[:-1]:
            p.wait(timeout=max(1, seconds))
        rc = procs[-1].returncode
    except subprocess.TimeoutExpired:
        for p in procs:
            p.kill()
        for p in procs:
            p.wait()
        return {"cmd": shown, "rc": 124, "out": "no answer in %d seconds" % seconds}
    except OSError as e:
        for p in procs:
            p.kill()
        return {"cmd": shown, "rc": 127, "out": mask(str(e))}
    finally:
        for p in procs:
            for f in (p.stdout, p.stderr):
                if f:
                    f.close()
    text = out.decode("utf-8", "replace")
    if not text.strip() and err:
        text = err.decode("utf-8", "replace")
    # 1.77.0c: secrets removed from the whole output first, then it is cut (a cut inside a quoted value would leave the
    # rest of it bare); past READ_MAX it is dropped unread, and so is the line READ_MAX would split (1.77.0h)
    cut = len(text) > out_max
    text = mask(head(text, max(READ_MAX, out_max)))
    if cut or len(text) > out_max:
        text = text[:out_max] + "\n... (cut at %d bytes)" % out_max
    return {"cmd": shown, "rc": rc, "out": text}


def plan(rule):
    """One rule -> (from, [pipelines], refused): DISA's commands that fit; none - the code's calls that fit."""
    disa, refused = [], []
    for ln in disa_lines(rule.get("check_text")):
        st, why = pipeline_of(ln)
        if st:
            if st not in disa:
                disa.append(st)
        else:
            # 1.77.0d: redacted, then cut - a command line (DISA's text), so without the line backstop
            refused.append("%s - %s" % (redact(ln, backstop=False)[:200], mask(why)))
    if disa:
        return "DISA's", disa[:8], refused
    code = []
    for c in rule.get("calls") or []:
        c = [str(x) for x in c or []]
        if c and c[0] in KNOWN and not gram(c[0], c[1:]) and [c] not in code:
            code.append([c])
    return ("the code's" if code else ""), code[:8], refused


def main():
    module = AnsibleModule(argument_spec=dict(
        rules=dict(type="list", elements="dict", default=[]),
        timeout=dict(type="int", default=30),
        budget=dict(type="int", default=600),
        out_max=dict(type="int", default=4000),
    ), supports_check_mode=True)
    p = module.params
    reads, start = {}, time.time()
    for r in p["rules"]:
        rid = str(r.get("id") or "")
        src, todo, refused = plan(r)
        ran = []
        for st in todo:
            if time.time() - start > p["budget"]:
                refused.append("%s - not run: past the host's budget (%d s)" % (redact(" | ".join(" ".join(s) for s in st), backstop=False), p["budget"]))
                continue
            ran.append(run_pipeline(st, max(1, p["timeout"]), max(200, p["out_max"])))
        why = "" if ran else ("no command of DISA's check text fits the read-only list, and the code calls no program on it"
                              if not todo else "past the host's budget")
        reads[rid] = {"from": src if ran else "", "ran": ran, "why": why, "refused": refused[:10]}
    module.exit_json(changed=False, reads=reads)


if __name__ == "__main__":
    run_main(main)     # 1.77.0h: an unexpected error redacted, no raw traceback
