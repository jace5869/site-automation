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
  - Every command is checked against the fixed grammar of the read-only check (stat -c, sysctl -n, rpm -q - and DISA's
    "dnf list installed PKG" read as rpm -q PKG - , systemctl
    is-enabled / is-active / show -p, getent, id, grep with its listed flags, find without -exec / -delete / -fprint,
    auditctl -l, getenforce, sestatus) and run as a literal argv - never through a shell. A pipeline (|) only of such
    commands; 2>/dev/null is the only redirection taken. Anything else is not run, and said. Only the last command's
    stderr is read (its output when it printed nothing else, and not after 2>/dev/null).
  - 1.79.10 - a command not on the list is read another way where it can be, with programs on it - the files it reads
    (cat, more, awk FILE, sshd -T, mount, gsettings, grubby ...), find -printf for -exec stat / ls, systemctl
    is-enabled and is-active for status, rpm -qa for dnf list installed. The evidence shows the command as run.
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


# ---- 1.79.10: DISA's commands read another way, read-only --------------------------------------------------------------
# A command the read-only list does not have is read, where it can be, with programs it has: the files the command
# itself reads (grep with an empty pattern: every line), find's -printf for -exec stat / ls, systemctl is-enabled /
# is-active for status. Nothing here runs a shell, writes, starts a service or reaches the network. The command as run
# is what the evidence shows (ACT reads it beside DISA's own line). A rewrite that would change what a later grep finds
# (mount's " on " lines) is not made: that line stays not run.
READ = ["/usr/bin/grep", "-h", "-s", ""]           # every line of the files, no file names, no "no such file"
SSHD_FILES = ["/etc/ssh/sshd_config", "/etc/ssh/sshd_config.d/*.conf"]
DCONF_DIRS = ["/etc/dconf/db/*.d"]
ZONE_DIRS = ["/etc/firewalld/zones", "/usr/lib/firewalld/zones"]
LS_PRINTF = "%M %u %g %s %p %l\\n"                  # ls -l's columns, as find -printf writes them
LS_NAMES = "%p\\n"                                  # ls without -l: the names only (no owner looked up - Codex, 1.79.10)
# cut and awk select fields: read another way they show whole lines. Only of these files, which hold no secret
# (DISA's cut and awk read /etc/passwd, /etc/group and /etc/shadow - never shadow's hashes: Codex, 1.79.10)
FIELD_FILES = ("/etc/passwd", "/etc/group")
STAT_TO_PRINTF = {"%n": "%p", "%U": "%u", "%G": "%g", "%u": "%U", "%g": "%G", "%a": "%m", "%A": "%M"}
# the evidence-only forms (never in the read-only check's grammar, which the code is held to): grep's -P -H -R and
# context lines, find -L, rpm -qa [GLOB] and the SUMMARY tag, and filters that only read their input (no file operand).
# grep -o is dropped: the whole line is read, so the redactor sees a value with its setting's name (Codex, 1.79.10)
GREP_MORE = ("-P", "-o", "-H", "-R")
RX_GLOB_PKG = re.compile(r"^[A-Za-z0-9*?][A-Za-z0-9*?._+-]*$")
RX_QF_MORE = re.compile(RX_QF.pattern.replace("|VENDOR|", "|VENDOR|SUMMARY|"))
_ESC = {"\\(": "\x01LP", "\\)": "\x01RP", "\\;": "\x01SC"}
_UNESC = {"\x01LP": "(", "\x01RP": ")", "\x01SC": ";"}


def _paths(a):
    return bool(a) and all(_m(RX_PATH, x) for x in a)


def _grep_more(a):
    """grep's arguments with the evidence-only flags (-P -o -H -R, -A/-B/-C N, --invert-match, --ignore-case) -> the
    same arguments, and '' when the rest is the read-only grammar's; else why not."""
    a = ["-v" if x == "--invert-match" else "-i" if x == "--ignore-case" else x for x in a]
    b, rest, i = [], [], 0
    while i < len(a):
        x = a[i]
        if re.match(r"^-[A-Za-z]{2,}$", x) and all(("-" + c) in GREP_FLAGS + GREP_MORE for c in x[1:]):
            b += ["-" + c for c in x[1:]]
        elif x in GREP_FLAGS or x in GREP_MORE:
            b.append(x)
        elif re.match(r"^-[ABC][0-9]{1,3}$", x):
            b.append(x)
        elif x in ("-A", "-B", "-C") and i + 1 < len(a) and re.match(r"^[0-9]{1,3}$", a[i + 1]):
            b.append(x + a[i + 1])
            i += 1
        else:
            break
        i += 1
    rest = a[i:]
    b = [x for x in b if x != "-o"]         # the whole line (pipelines_of: never with a stage after it)
    plain = [x for x in b if x in GREP_FLAGS]
    why = gram("/usr/bin/grep", plain + rest)
    return (b + rest, "") if not why else (None, why)


def _check(prog, a, first, last=True):
    """One stage -> (argv, '') the read-only grammar or an evidence-only form takes, or (None, why)."""
    if prog == "/usr/bin/grep":
        # grep -o read as whole lines: a wc -l or a grep after it would count or find something else (Codex, 1.79.10)
        if not last and any(re.match(r"^-[A-Za-z]+$", x) and "o" in x[1:] for x in a):
            return None, "grep -o with a command after it"
        b, why = _grep_more(a)
        return ([prog] + b, "") if b is not None else (None, why)
    if prog == "/usr/bin/find" and a[:1] == ["-L"]:
        why = gram(prog, a[1:])
        return ([prog] + a, "") if not why else (None, why)
    if prog == "/usr/bin/rpm":
        if (a[:1] == ["-qa"] and len(a) <= 2 and all(_m(RX_GLOB_PKG, x) for x in a[1:])) or \
                (len(a) == 4 and a[0] == "-q" and a[1] in ("--qf", "--queryformat") and _m(RX_QF_MORE, a[2]) and _m(RX_PKG, a[3])):
            return [prog] + a, ""
    prog = prog if prog in KNOWN else prog.rsplit("/", 1)[-1]
    if not first and prog in ("tail", "head"):          # input only: no file operand
        if a == [] or (len(a) == 1 and re.match(r"^-[0-9]{1,4}$", a[0])) or (len(a) == 2 and a[0] == "-n" and re.match(r"^[0-9]{1,4}$", a[1])):
            n = "10" if not a else a[-1].lstrip("-")
            return ["/usr/bin/" + prog, "-n", n], ""
        return None, "%s: only -n N, reading its input" % prog
    if not first and prog == "uniq" and len(a) <= 1 and all(x in ("-d", "-u", "-c") for x in a):
        return ["/usr/bin/uniq"] + a, ""
    if not first and prog == "wc" and a == ["-l"]:
        return ["/usr/bin/wc", "-l"], ""
    why = gram(prog, a) if prog in KNOWN else "%s is not on the read-only list" % prog
    return ([prog] + a, "") if not why else (None, why)


def _find_action(s):
    """find ... -exec stat -c FMT {} ; (or +), -exec ls -l {} ;, -ls -> the same find with -printf (find prints, runs nothing)."""
    if "-exec" not in s:
        return s[:-1] + ["-printf", LS_PRINTF] if s[-1:] == ["-ls"] and s.count("-ls") == 1 else s
    i = s.index("-exec")
    tail = s[i + 1:]
    if not tail or tail[-1] not in (";", "+") or "{}" not in tail or tail.index("{}") != len(tail) - 2:
        return s
    cmd = [x for x in tail[:-2] if x != "sudo"]
    if cmd[:1] == ["stat"]:
        # stat -L reads the link's target, stat the link: so does find -printf with find -L and without it. They must
        # agree, or it is not read another way (Codex, 1.79.10)
        if ("-L" in cmd[1:]) != (s[1:2] == ["-L"]):
            return s
        c = [x for x in cmd[1:] if x != "-L"]
        if len(c) == 2 and c[0] == "-c" and re.match(r"^(?:%[nUGugaA]|[ :,])+$", c[1]):
            return s[:i] + ["-printf", re.sub(r"%[nUGugaA]", lambda m: STAT_TO_PRINTF[m.group(0)], c[1]) + "\\n"]
    if cmd[:1] == ["ls"] and len(cmd) == 2 and re.match(r"^-[ldaL]+$", cmd[1]) and "l" in cmd[1]:
        return s[:i] + ["-printf", LS_PRINTF]
    return s


def _source(s, after):
    """A first stage the read-only list does not have -> [[argv-stages], ...] (several: each run on its own), or None."""
    p, a = s[0].rsplit("/", 1)[-1], s[1:]
    if p in ("cat", "more", "less") and _paths(a):
        return [[READ + a]]
    if p in ("head", "tail") and a and _paths(a[-1:]) and len([x for x in a if x.startswith("/")]) == 1 and \
            not re.search(r"[*?\[]", a[-1]):     # one file: with several (or a glob), head -n 1 gives each file's
        # first line, one grep pipe only the first (Codex, 1.79.10)
        n, files = "10", list(a)
        if re.match(r"^-[0-9]{1,4}$", files[0]):
            n, files = files[0][1:], files[1:]
        elif files[0] == "-n" and len(files) > 2 and re.match(r"^[0-9]{1,4}$", files[1]):
            n, files = files[1], files[2:]
        if _paths(files):
            return [[READ + files, ["/usr/bin/" + p, "-n", n]]]
    if p in ("awk", "cut") and not after:               # the file it reads; its filter left to the reader (the line says how)
        # only the plain forms (cut -d X -f LIST, awk [-F X] 'PROGRAM'), nothing after it in the pipe (a uniq or grep
        # there would read whole lines, not the fields), never a file of secrets (Codex, 1.79.10)
        k = len(a)
        while k > 0 and _m(RX_PATH, a[k - 1]):
            k -= 1
        o, files = a[:k], a[k:]
        if p == "cut":
            ok = re.match(r"^(-d ?\S -f ?[0-9,-]+|-f ?[0-9,-]+ -d ?\S)$", " ".join(o)) is not None
        else:
            ok = (len(o) == 1 or (len(o) == 2 and re.match(r"^-F\S+$", o[0])) or (len(o) == 3 and o[0] == "-F")) and \
                not o[-1].startswith("-") and not o[-1].startswith("/")
        if ok and files and all(f in FIELD_FILES for f in files):
            return [[READ + files]]
    if p == "sshd" and a == ["-T"]:
        return [[READ + SSHD_FILES]]
    if p == "grub2-editenv" and a == ["list"]:
        return [[READ + ["/boot/grub2/grubenv"]]]
    if p == "grubby" and a == ["--info=ALL"]:            # DEFAULT: one entry - not told apart here (Codex, 1.79.10)
        return [[["/usr/bin/grep", "-h", "-s", "-e", "^options", "-e", "^kernelopts", "/boot/loader/entries/*.conf", "/boot/grub2/grubenv"]]]
    if p == "mount" and not a:
        if any(" on " in x for st in after for x in st):
            return None                                 # /proc/self/mounts has no " on ": a grep for it would find nothing
        return [[READ + ["/proc/self/mounts"]]]
    if p == "findmnt" and len(a) == 1 and _m(RX_PATH, a[0]) and not re.search(r"[*?\[]", a[0]):
        # the mount point (field 2) or the device (field 1, at the line's start - Codex, 1.79.10)
        return [[["/usr/bin/grep", "-s", "-E", r"(^|\s)%s\s" % re.escape(a[0]), "/proc/self/mounts"]]]
    if p in ("dnf", "yum") and a[:2] in (["list", "installed"], ["list", "--installed"]) and len(a) <= 3 and all(_m(RX_GLOB_PKG, x) for x in a[2:]):
        return [[["/usr/bin/rpm", "-qa"] + a[2:]]]
    if p == "systemctl" and len(a) == 2 and a[0] == "status" and _m(RX_UNIT, a[1]) and not after:
        return [[["/usr/bin/systemctl", "is-enabled", a[1]]], [["/usr/bin/systemctl", "is-active", a[1]]]]
    if p == "systemctl" and a == ["get-default"]:
        # the site's link, and the default the package ships (used when /etc has none - Codex, 1.79.10)
        return [[["/usr/bin/find", "/etc/systemd/system/default.target", "/usr/lib/systemd/system/default.target", "-maxdepth", "0",
                  "-printf", "%p %l\\n"]]]
    if p == "ls" and a:
        k = 0
        flags = ""
        while k < len(a) and re.match(r"^-[lLdaAR]+$", a[k]):
            flags += a[k][1:]
            k += 1
        if _paths(a[k:]):
            # -d wins over -R (ls -dR lists the directory itself); owner and group only where ls -l shows them
            return [[["/usr/bin/find"] + (["-L"] if "L" in flags else []) + a[k:] +
                     (["-maxdepth", "0"] if "d" in flags else [] if "R" in flags else ["-maxdepth", "1"]) +
                     ["-printf", LS_PRINTF if "l" in flags else LS_NAMES]]]
    if p == "gsettings" and len(a) == 3 and a[0] in ("get", "writable") and re.match(r"^[a-z][a-z0-9-]*$", a[2]) and \
            re.match(r"^[a-z][a-z0-9.-]*$", a[1]):
        return [[["/usr/bin/grep", "-r", "-s", "-e", a[2]] + DCONF_DIRS]]
    if p == "firewall-cmd":
        b = [x for x in a if x != "--permanent"]
        if b == ["--state"]:
            return [[["/usr/bin/systemctl", "is-active", "firewalld"]]]
        if b == ["--get-default-zone"]:
            return [[["/usr/bin/grep", "-s", "-e", "DefaultZone", "/etc/firewalld/firewalld.conf"]]]
        if b in (["--list-all"], ["--list-all-zones"], ["--get-active-zones"]):
            return [[["/usr/bin/grep", "-s", "-e", "DefaultZone", "/etc/firewalld/firewalld.conf"]], [READ + [d + "/*.xml" for d in ZONE_DIRS]]]
        z = re.match(r"^--info-zone=([A-Za-z0-9_-]+)$", b[0]) if len(b) == 1 else None
        if z:
            return [[READ + ["%s/%s.xml" % (d, z.group(1)) for d in ZONE_DIRS]]]
    if p == "update-crypto-policies" and a == ["--show"]:
        return [[READ + ["/etc/crypto-policies/config", "/etc/crypto-policies/state/current"]]]
    if p == "postconf" and a and a[-1:] and re.match(r"^[a-z0-9_]+$", a[-1]) and a[:-1] in ([], ["-n"]):
        return [[["/usr/bin/grep", "-s", "-E", r"^\s*%s\s*=" % a[-1], "/etc/postfix/main.cf"]]]
    if p == "fips-mode-setup" and a == ["--check"]:
        return [[READ + ["/proc/sys/crypto/fips_enabled"]]]
    if p == "authselect" and a == ["current"]:
        return [[READ + ["/etc/authselect/authselect.conf"]]]
    return None


def _sshd_chain(line):
    """DISA's sshd -dd 2>&1 | awk '/filename/ ...' | ... | xargs grep FLAGS 'PATTERN' (the files sshd reads, then grep) ->
    grep FLAGS 'PATTERN' on those files."""
    m = re.match(r"""^(?:sudo\s+)?(?:/usr/sbin/)?sshd\s+-dd\s+2>&1\s*\|.*\|\s*xargs\s+(?:sudo\s+)?grep\s+(-[A-Za-z]+\s+)?('[^']*'|"[^"]*"|\S+)\s*$""", line)
    if not m:
        return line
    return "grep %s%s %s" % (m.group(1) or "", m.group(2), " ".join(SSHD_FILES))


def pipelines_of(line):
    """One command line -> ([pipeline, ...] the grammar takes, '') or (None, why not). Most lines give one pipeline;
    a line read another way (1.79.10) may give two (systemctl status: is-enabled, then is-active)."""
    line = _sshd_chain(line)
    if re.search(r"`|\$\(|\$\{", line):
        return None, "a substitution (` or $( or ${)"
    literal = _quoted_globs(line)
    for k, v in _ESC.items():
        line = line.replace(k, " %s " % v)
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
            if t in _UNESC:
                t = _UNESC[t]
            else:                       # inside quotes the backslash was the text's own: put it back (Codex, 1.79.10)
                for k, v in _ESC.items():
                    t = t.replace(" %s " % v, k)
                if t.startswith("/") and t in literal:
                    t = Lit(t)          # a quoted path: one file named so, never a pattern a shell would expand
            cur.append(t)
        i += 1
    stages.append(cur)
    clean = []
    for s in stages:
        while s and s[0] == "sudo":         # sudo and its options: the job runs it as root already
            s = s[1:]
            while s and s[0].startswith("-"):
                s = s[1:]
        if not s:
            return None, "an empty command in the pipeline"
        if s[0] == "egrep":
            s = ["grep", "-E"] + s[1:]
        clean.append(s)
    # a pager at the end reads nothing more than it is given; an awk at the end only filters it (the line says so)
    while len(clean) > 1 and (clean[-1] in (["more"], ["less"]) or (clean[-1][0] == "awk" and len(clean[-1]) == 2)):
        if clean[-1][0] == "awk" and clean[0][0].rsplit("/", 1)[-1] in ("cat", "more", "less", "head", "tail") and \
                not all(x in FIELD_FILES for x in clean[0][1:] if x.startswith("/")):
            break                       # it would show the whole file, not its fields (Codex, 1.79.10)
        clean = clean[:-1]
    first = clean[0]
    # 1.79.7: DISA's "dnf list installed PKG" (or yum, or --installed) asks the local package database only for
    # whether PKG is there - read as rpm -q PKG, which never reaches a repository (dnf itself may refresh them)
    if len(first) == 4 and first[0] in ("dnf", "yum") and first[1] == "list" and first[2] in ("installed", "--installed") and _m(RX_PKG, first[3]):
        alts = [[["rpm", "-q", first[3]]] + clean[1:]]
    elif PROGRAMS.get(first[0], first[0]) in KNOWN and not (first[0].rsplit("/", 1)[-1] == "systemctl" and first[1:2] in (["status"], ["get-default"])):
        alts = [[_find_action(first) if first[0] in ("find", "/usr/bin/find") else first] + clean[1:]]
    else:
        src = _source(first, clean[1:])
        if not src:
            prog = PROGRAMS.get(first[0], first[0])
            return None, (gram(prog, first[1:]) if prog in KNOWN else "%s is not on the read-only list" % first[0])
        alts = []
        for alt in src:
            rest = list(clean[1:])
            if first[0].rsplit("/", 1)[-1] == "grubby" and rest[:1] == [["grep", "args"]]:
                rest = rest[1:]                 # grubby's "args" are the entries' options lines
            alts.append(alt + rest)
    out_all = []
    for alt in alts:
        out = []
        for n, s in enumerate(alt):
            prog = PROGRAMS.get(s[0], s[0])
            if prog in KNOWN:
                argv, why = _check(prog, _normal(prog, s[1:]), n == 0, n == len(alt) - 1)
            else:
                argv, why = _check(s[0], s[1:], n == 0, n == len(alt) - 1)
            if why:
                return None, why
            out.append(argv)
        pl = Pipeline(out)
        pl.quiet = frozenset(quiet) if len(alts) == 1 and len(alt) == len(stages) else frozenset()
        out_all.append(pl)
    return out_all, ""


def pipeline_of(line):
    """One command line -> (the first pipeline the grammar takes, '') or (None, why not)."""
    st, why = pipelines_of(line)
    return (st[0], "") if st else (None, why)


def _quoted_globs(line):
    """The words of a command line with a * ? or [ inside quotes or after a backslash, as the shell gives them: a
    shell never expands those (Codex, 1.79.10: /x/'*.conf' and /x/\\*.conf too)."""
    out, word, glob_q, q, i = set(), "", False, "", 0
    while i < len(line):
        ch = line[i]
        if q:
            if ch == q:
                q = ""
            elif q == '"' and ch == "\\" and i + 1 < len(line) and line[i + 1] in '"\\$`':
                i += 1
                word += line[i]
            else:
                word += ch
                glob_q = glob_q or ch in "*?["
        elif ch in "'\"":
            q = ch
        elif ch == "\\" and i + 1 < len(line):
            i += 1
            word += line[i]
            glob_q = glob_q or line[i] in "*?["
        elif ch.isspace() or ch in "|&;<>()":
            if word and glob_q:
                out.add(word)
            word, glob_q = "", False
        else:
            word += ch
        i += 1
    if word and glob_q:
        out.add(word)
    return out


class Lit(str):
    """A path the command line quoted: taken as written, never expanded (Codex, 1.79.10)."""


def _expand(argv):
    """grep's and stat's paths with * ? [ as a shell would give them (sorted, at most 64); none matches: as written."""
    if argv[0] == "/usr/bin/find":                      # 1.79.10: its starting paths (never a -path or -name value)
        k = 2 if argv[1:2] == ["-L"] else 1
        out = argv[:k]
        while k < len(argv) and argv[k].startswith("/"):
            got = sorted(glob.glob(argv[k]))[:64] if re.search(r"[*?\[]", argv[k]) and not isinstance(argv[k], Lit) else []
            out += got or [argv[k]]
            k += 1
        return out + argv[k:]
    if argv[0] not in ("/usr/bin/grep", "/usr/bin/stat"):
        return argv
    out = [argv[0]]
    for x in argv[1:]:
        if x.startswith("/") and re.search(r"[*?\[]", x) and not isinstance(x, Lit):
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
        sts, why = pipelines_of(ln)
        if sts:
            for st in sts:
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
