#!/usr/bin/python
# -*- coding: utf-8 -*-
"""What fapolicyd blocked in containers on a Linux host: its denials from the audit log (ausearch -m FANOTIFY),
the ones a container caused told apart from the rest, grouped (program, file, permission), the container they came
from when it still runs, the rule that denied them - and for each group the allow rule that would let it through,
for someone to approve: the narrowest one for a host file (that program, that file), container storage's folder for
a file inside an image (its path in the audit log is the container's, not the host's). And a review of the rules
files in place: what to tighten. Read-only. Standard library only."""
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
    container's name while it runs, and proposes for each group an allow rule - the narrowest one (the program and the
    file, or its folder) for a file that is on the host at that path (a bind mount), container storage's folder for a
    file inside an image (the audit log holds the container's path for it, which names nothing on the host, and the
    host path of an image's file changes at each pull). Reviews the rules files in place (C(rules.d/*.rules)): rules
    with wildcards (fapolicyd has none), a system folder or a user folder opened to any program, duplicates, rules for
    RPM-owned files (trusted already), a file that sorts after the stock deny rules. Changes nothing; a dry run reads too.
options:
  since: {description: "ausearch -ts: recent, today, yesterday, this-week, boot, or a date and time.", type: str, default: today}
  rules_file: {description: The rules file this repository manages (its rules are read back; its folder's files are reviewed)., type: str, default: /etc/fapolicyd/rules.d/35-site-containers.rules}
  scope: {description: "The proposed rule's object for a host file: path (the file) or dir (its folder; never a system folder).", type: str, default: path}
  with_exe: {description: The proposed rule for a host file names the program that was denied (narrower)., type: bool, default: true}
  storage_rules: {description: "A file inside an image (not on the host at its path) gets container storage's folder as its rule (dir=.../storage/overlay/) instead of a path no rule can match.", type: bool, default: true}
  review: {description: Review the rules files in place (the folder of rules_file) and list what to tighten., type: bool, default: true}
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
rules_review:
  description: "The rules files in place, what to tighten: [{file, line, rule, what, status}] (line 0: about the file)."
  type: list
  returned: success
  sample: [{file: 99-local.rules, line: 4, rule: "allow perm=any all : dir=/usr/lib/", status: warning,
            what: "any program may then run any untrusted file under a system folder; RPM-owned files there are trusted already"}]
'''

import glob
import os
import pwd
import re

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_fapolicyd import (  # noqa: F401 - shared with roles/fapolicyd_rules
    fan_rule, CGROUP_ID, DEV, EXEC_CALLS, KV, LAST_BEFORE_DENY, RULE,
    SAFE, STOCK_RULES, SYSTEM_DIRS, SYSTEM_ROOTS, USER_ROOTS, _values,
    event_path, parse_events, perm_of, propose, review_rules, run,
    same_file, storage_conf, storage_rule, why_container)

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

    def rpm_owned(self, path, _cache={}, limit=[60]):
        """The package that owns the file, or '' (at most 60 lookups per run)."""
        if path not in _cache:
            if limit[0] <= 0:
                return ""
            limit[0] -= 1
            rc, out, _ = run(["rpm", "-qf", path], 30)
            _cache[path] = out.strip().splitlines()[0] if rc == 0 and out.strip() else ""
        return _cache[path]

    def review(self):
        """What to tighten in the rules files of rules_file's folder (rules.d), [] when there is none."""
        folder = os.path.dirname(self.p["rules_file"])
        files = []
        for f in sorted(glob.glob(os.path.join(folder, "*.rules"))):
            try:
                with open(f) as fh:
                    files.append((os.path.basename(f), fh.read()))
            except (IOError, OSError, UnicodeDecodeError):
                files.append((os.path.basename(f), ""))
        return review_rules(files, self.rpm_owned)

    def user_of(self, name):
        """{name, uid, home} of the denial's user (ausearch -i gives the name), or None for root / not known."""
        if not name or name in ("root", "unset", "?"):
            return None
        try:
            pw = pwd.getpwnam(name) if not str(name).isdigit() else pwd.getpwuid(int(name))
        except (KeyError, ValueError, OverflowError):
            return None
        return {"name": pw.pw_name, "uid": pw.pw_uid, "home": pw.pw_dir}

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
        r["rules_review"] = self.review() if self.p["review"] and r["rules_d"] else []
        try:
            with open("/etc/containers/storage.conf") as f:
                graph, rootless = storage_conf(f.read())
        except (IOError, OSError, UnicodeDecodeError):
            graph, rootless = storage_conf("")
        storage_roots = tuple(x for x in (graph + "/", rootless and rootless.split("$")[0], "/run/containers/storage/", "/var/lib/docker/") if x)

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
            path, obj, rec = event_path(e)
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
                rule_no = fan_rule(e["fan"])
                # on the host at that path (a bind mount): its own rule; else (inside an image) container storage's
                in_storage = path.startswith("/") and (path.startswith(storage_roots) or not same_file(path, rec))
                g = groups[key] = {"exe": exe, "path": path, "perm": perm, "syscall": sc.get("syscall", ""), "count": 0,
                                   "first": e["time"], "last": e["time"], "rule_no": rule_no, "in_storage": in_storage,
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
            if self.p["storage_rules"] and g.pop("in_storage", False):
                g["where"] = "container storage"
                g["proposal"], g["note"] = storage_rule(g["path"], g["exe"], self.user_of(g["uids"][0] if g["uids"] else ""), graph, rootless)
            else:
                g.pop("in_storage", None)
                g["where"] = "the host"
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
        storage_rules=dict(type="bool", default=True),
        review=dict(type="bool", default=True),
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
        result = {"state": "not read", "error": "%s: %s" % (e.__class__.__name__, e), "groups": [], "others": [], "managed": [],
                  "rules_review": []}
    module.exit_json(changed=False, **result)


if __name__ == "__main__":
    main()
