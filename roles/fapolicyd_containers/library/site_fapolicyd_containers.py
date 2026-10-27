#!/usr/bin/python
# -*- coding: utf-8 -*-
"""What fapolicyd blocked in containers on a Linux host: its denials from the audit log (ausearch -m FANOTIFY),
the ones a container caused told apart from the rest, grouped (program, file, permission), the container they came
from when it still runs, the rule that denied them - and for each group the narrowest allow rule that would let it
through, for someone to approve. Read-only. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_fapolicyd_containers
short_description: fapolicyd denials caused by containers, grouped, with the allow rule that would let each through
description:
  - Reads fapolicyd's denials from the audit log (C(ausearch -m FANOTIFY -ts since -i)) and keeps the ones a
    container caused - a container's SELinux label on the program or the file, a container program (crun, conmon,
    podman ...), a file under container storage, or a process in a podman container's cgroup.
  - Groups them by program, file and permission, with the rule that denied them (C(fapolicyd-cli --list)) and the
    container's name while it runs, and proposes for each group the narrowest allow rule (the program and the file,
    or its folder). Changes nothing; a dry run reads too.
options:
  since: {description: "ausearch -ts: recent, today, yesterday, this-week, boot, or a date and time.", type: str, default: today}
  rules_file: {description: The rules file this repository manages (its rules are read back)., type: str, default: /etc/fapolicyd/rules.d/35-site-containers.rules}
  scope: {description: "The proposed rule's object: path (the file) or dir (its folder; never a system folder).", type: str, default: path}
  with_exe: {description: The proposed rule names the program that was denied (narrower)., type: bool, default: true}
  labels: {description: Regular expression on the SELinux labels of the program and the file that marks a container., type: str, default: ''}
  programs: {description: Container programs (base names)., type: list, elements: str, default: []}
  paths: {description: Regular expressions on the program's or the file's path that mark container storage., type: list, elements: str, default: []}
  names: {description: Look up the running containers' names (podman ps, root's and each logged-in user's)., type: bool, default: true}
  max_groups: {description: At most this many groups returned (the most denied first)., type: int, default: 200}
  timeout: {description: Seconds for each command., type: int, default: 120}
author: site automation
'''

RETURN = r'''
state:
  description: "read, not installed, or not read (with error)."
  type: str
  returned: always
groups:
  description: The container denials, grouped, the most first, each with its proposed rule (or why there is none).
  type: list
  returned: success
  sample: [{exe: /usr/bin/crun, path: /app/server, perm: execute, count: 4, first: "10/27/2026 08:00:01", last: "10/27/2026 09:12:40",
            rule_no: 13, rule: "deny_audit perm=execute all : all", containers: [web01], uids: [root], why: [container label],
            proposal: "allow perm=execute exe=/usr/bin/crun : path=/app/server", note: ""}]
others:
  description: The denials that are not a container's, the most first (program -> file x count).
  type: list
  returned: success
'''

import os
import pwd
import re
import subprocess

from ansible.module_utils.basic import AnsibleModule

EXEC_CALLS = ("execve", "execveat", "uselib")
# folders a rule's dir= must never open: everything the host itself runs is under them
SYSTEM_DIRS = ("/", "/bin/", "/sbin/", "/usr/", "/usr/bin/", "/usr/sbin/", "/usr/lib/", "/usr/lib64/", "/usr/libexec/",
               "/usr/local/", "/usr/local/bin/", "/usr/local/sbin/", "/usr/local/lib/", "/usr/local/lib64/", "/lib/",
               "/lib64/", "/etc/", "/tmp/", "/var/", "/var/tmp/", "/dev/", "/dev/shm/", "/run/", "/home/", "/root/", "/opt/")
KV = re.compile(r'(\w+)=("[^"]*"|\S+)')
SAFE = re.compile(r"^/[A-Za-z0-9._+@%:,/=~-]*$")         # a value a rule can hold (no space, no quote)
CGROUP_ID = re.compile(r"(?:libpod-|docker-|crio-|containers/)([0-9a-f]{12,64})")


def run(args, timeout):
    """(rc, stdout text, stderr text); rc 124 on timeout, 127 when it is not there."""
    try:
        p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:
        return 127, "", str(e)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        return 124, "", "timed out after %d seconds" % timeout
    return p.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def parse_events(text):
    """ausearch -i output -> [{syscall: {...}, paths: [{...}], cwd: str, fan: {...}, time: str}], one per event."""
    events, cur = [], None
    for line in text.splitlines():
        if line.startswith("----"):
            if cur:
                events.append(cur)
            cur = None
            continue
        if not line.startswith("type="):
            continue
        kind = line[5:].split(" ", 1)[0]
        head, _, body = line.partition(" : ")
        m = re.search(r"msg=audit\(([^)]*)\)", head)
        stamp = m.group(1).rsplit(":", 1)[0] if m else ""
        if cur is None:
            cur = {"syscall": {}, "paths": [], "cwd": "", "fan": {}, "time": stamp.rsplit(".", 1)[0]}
        kv = dict((k, v.strip('"')) for k, v in KV.findall(body))
        if kind == "SYSCALL":
            cur["syscall"] = kv
        elif kind == "PATH":
            cur["paths"].append(kv)
        elif kind == "CWD":
            cur["cwd"] = kv.get("cwd", "")
        elif kind == "FANOTIFY":
            cur["fan"] = kv
    if cur:
        events.append(cur)
    return [e for e in events if e["fan"]]


def event_path(e):
    """The file the event was about: the first PATH record's name, made absolute with the CWD record."""
    paths = sorted(e["paths"], key=lambda p: int(p.get("item", "0")) if str(p.get("item", "0")).isdigit() else 0)
    for p in paths:
        name = p.get("name", "")
        if name and name not in ("(null)", "?"):
            if not name.startswith("/") and e["cwd"].startswith("/"):
                name = os.path.normpath(os.path.join(e["cwd"], name))
            return name, p.get("obj", "")
    return "", ""


def perm_of(syscall):
    return "execute" if syscall in EXEC_CALLS else ("open" if syscall else "any")


def why_container(e, path, obj, labels, programs, paths, cgroup_of):
    """Why this denial is a container's ([] = it is not)."""
    sc = e["syscall"]
    why = []
    if labels and (labels.search(sc.get("subj", "")) or labels.search(obj)):
        why.append("container SELinux label")
    exe = sc.get("exe", "")
    if os.path.basename(exe) in programs:
        why.append("container program %s" % os.path.basename(exe))
    if any(p.search(path) or p.search(exe) for p in paths):
        why.append("container storage")
    cid = cgroup_of(sc.get("pid", "")) or cgroup_of(sc.get("ppid", ""))
    if cid:
        why.append("container cgroup")
    return why, cid


def propose(exe, path, perm, scope, with_exe):
    """(the narrowest allow rule for this group, or '', and a note why not / what it opens)."""
    if not path.startswith("/"):
        return "", "the file's full path is not in the audit record: add a rule by hand"
    obj = "path=%s" % path
    note = ""
    if scope == "dir":
        d = os.path.dirname(path).rstrip("/") + "/"
        if d in SYSTEM_DIRS:
            note = "a file's rule, not its folder's: %s is a system folder" % d
        else:
            obj = "dir=%s" % d
    if not SAFE.match(obj.split("=", 1)[1]):
        return "", "the path has a space or a character a rule cannot hold: add a rule by hand"
    subj = "all"
    if with_exe and exe.startswith("/"):
        if not SAFE.match(exe):
            return "", "the program's path has a character a rule cannot hold: add a rule by hand"
        subj = "exe=%s" % exe
    if subj == "all":
        note = (note + "; " if note else "") + "any program on the host may then use this file"
    return "allow perm=%s %s : %s" % (perm, subj, obj), note


class Reader(object):
    def __init__(self, module):
        self.m = module
        self.p = module.params
        self._cg = {}
        self._names = None

    def cgroup_of(self, pid):
        """The container ID of a process still running, from its cgroup ('' = none, or gone)."""
        if not str(pid).isdigit():
            return ""
        if pid not in self._cg:
            try:
                with open("/proc/%s/cgroup" % pid) as f:
                    m = CGROUP_ID.search(f.read())
                self._cg[pid] = m.group(1)[:12] if m else ""
            except (IOError, OSError):
                self._cg[pid] = ""
        return self._cg[pid]

    def names(self):
        """{container ID (12): name} of root's and each logged-in user's running podman containers."""
        if self._names is not None:
            return self._names
        self._names = {}
        podman = self.m.get_bin_path("podman")
        if not podman or not self.p["names"]:
            return self._names
        owners = [[podman]]
        for d in sorted(os.listdir("/run/user")) if os.path.isdir("/run/user") else []:
            if d.isdigit() and int(d) >= 1000:
                try:
                    owners.append(["runuser", "-u", pwd.getpwuid(int(d)).pw_name, "--", "env", "XDG_RUNTIME_DIR=/run/user/" + d, podman])
                except KeyError:
                    pass
        for pm in owners:
            rc, out, _ = run(pm + ["ps", "--format", "{{.ID}} {{.Names}}"], 30)
            if rc == 0:
                for line in out.splitlines():
                    parts = line.split()
                    if len(parts) >= 2:
                        self._names[parts[0][:12]] = parts[1]
        return self._names

    def rules(self):
        """{rule number: rule text} as fapolicyd numbers them (fapolicyd-cli --list)."""
        rc, out, _ = run(["fapolicyd-cli", "--list"], 30)
        found = {}
        for line in out.splitlines() if rc == 0 else []:
            m = re.match(r"\s*(\d+)\.\s+(.*\S)", line)
            if m:
                found[m.group(1)] = m.group(2)
        return found

    def managed(self):
        try:
            with open(self.p["rules_file"]) as f:
                return [x.strip() for x in f if x.strip() and not x.strip().startswith("#")]
        except (IOError, OSError):
            return []

    def read(self):
        r = {"state": "read", "error": "", "groups": [], "others": [], "managed": [], "counts": {}}
        rc, out, err = run(["rpm", "-q", "fapolicyd"], 30)
        if rc != 0:
            r["state"] = "not installed"
            return r
        r["version"] = out.strip()
        r["active"] = run(["systemctl", "is-active", "fapolicyd"], 30)[1].strip() or "unknown"
        r["auditd"] = run(["systemctl", "is-active", "auditd"], 30)[1].strip() or "unknown"
        conf = {}
        try:
            with open("/etc/fapolicyd/fapolicyd.conf") as f:
                for line in f:
                    k, eq, v = line.partition("=")
                    if eq and not k.strip().startswith("#"):
                        conf[k.strip()] = v.strip()
        except (IOError, OSError):
            pass
        r["permissive"] = conf.get("permissive", "0") == "1"
        r["integrity"] = conf.get("integrity", "none")
        r["rules_d"] = os.path.isdir(os.path.dirname(self.p["rules_file"]))
        r["fagenrules"] = bool(self.m.get_bin_path("fagenrules"))
        r["managed"] = self.managed()

        rc, out, err = run(["ausearch", "-m", "FANOTIFY", "-ts"] + self.p["since"].split() + ["-i"], self.p["timeout"])
        if rc == 124:
            r["error"] = "ausearch: %s" % err.strip()
            return r
        if rc != 0 and "no matches" not in (err + out).lower():
            r["error"] = "ausearch failed (exit %d): %s" % (rc, (err or out).strip()[:200])
            return r
        events = parse_events(out)
        labels = re.compile(self.p["labels"]) if self.p["labels"] else None
        paths = [re.compile(x) for x in self.p["paths"]]
        programs = set(self.p["programs"])
        rule_text = self.rules() if events else {}
        groups, others = {}, {}
        for e in events:
            sc = e["syscall"]
            path, obj = event_path(e)
            exe = sc.get("exe", "")
            perm = perm_of(sc.get("syscall", ""))
            why, cid = why_container(e, path, obj, labels, programs, paths, self.cgroup_of)
            if not why:
                k = "%s -> %s" % (exe or "?", path or "?")
                others[k] = others.get(k, 0) + 1
                continue
            key = (exe, path, perm)
            g = groups.get(key)
            if g is None:
                rule_no = str(e["fan"].get("fan_info", ""))
                g = groups[key] = {"exe": exe, "path": path, "perm": perm, "syscall": sc.get("syscall", ""), "count": 0,
                                   "first": e["time"], "last": e["time"], "rule_no": rule_no,
                                   "rule": rule_text.get(rule_no, ""), "container_ids": [], "uids": [], "why": []}
            g["count"] += 1
            g["last"] = e["time"]
            for lst, v in ((g["container_ids"], cid), (g["uids"], sc.get("uid", ""))):
                if v and v not in lst:
                    lst.append(v)
            for w in why:
                if w not in g["why"]:
                    g["why"].append(w)
        out_groups = sorted(groups.values(), key=lambda g: (-g["count"], g["exe"], g["path"]))
        names = self.names() if any(g["container_ids"] for g in out_groups) else {}
        for g in out_groups:
            g["containers"] = [names.get(c, c) for c in g.pop("container_ids")]
            g["proposal"], g["note"] = propose(g["exe"], g["path"], g["perm"], self.p["scope"], self.p["with_exe"])
            if g["proposal"] and g["proposal"] in r["managed"]:
                g["note"] = "already in %s: denied before it was added, or fapolicyd has not loaded it" % self.p["rules_file"]
        r["groups"] = out_groups[:self.p["max_groups"]]
        r["others"] = ["%s x%d" % (k, n) for k, n in sorted(others.items(), key=lambda x: -x[1])][:10]
        r["counts"] = {"denials": len(events), "container": sum(g["count"] for g in out_groups),
                       "groups": len(out_groups), "other": sum(others.values())}
        return r


def main():
    module = AnsibleModule(argument_spec=dict(
        since=dict(type="str", default="today"),
        rules_file=dict(type="str", default="/etc/fapolicyd/rules.d/35-site-containers.rules"),
        scope=dict(type="str", default="path", choices=["path", "dir"]),
        with_exe=dict(type="bool", default=True),
        labels=dict(type="str", default=""),
        programs=dict(type="list", elements="str", default=[]),
        paths=dict(type="list", elements="str", default=[]),
        names=dict(type="bool", default=True),
        max_groups=dict(type="int", default=200),
        timeout=dict(type="int", default=120),
    ), supports_check_mode=True)
    try:
        result = Reader(module).read()
    except Exception as e:                      # noqa: BLE001 - a host that cannot be read is a row in the report
        result = {"state": "not read", "error": "%s: %s" % (e.__class__.__name__, e), "groups": [], "others": [], "managed": []}
    module.exit_json(changed=False, **result)


if __name__ == "__main__":
    main()
