#!/usr/bin/python
# -*- coding: utf-8 -*-
"""fapolicyd's blocks and rules on a Linux host, each change worded as the line someone approves: what it blocked -
or, permissive, would block - with a trust entry (a host file, pinned to its size and hash) or an allow rule (a
container's file) for each; the rules files' clean-up (a repeated rule, a wildcard that matches nothing, a rule for an
RPM-owned file, a rule that opens too much, a file that sorts after the stock deny rules); and permissive mode on or
off. Read-only. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_fapolicyd_rules
short_description: fapolicyd's blocks and rules, and the change for each, for approval
description:
  - Reads fapolicyd's audit events (C(ausearch -m FANOTIFY -ts since -i)) - denials (resp=deny), and in permissive mode
    the would-be denials (resp=allow with a deny rule's number; without rule info - RHEL 8 kernels - any audited allow
    while permissive) - grouped by program, file, permission.
  - For each group, the change that lets it through - a trust entry for a host file (pinned to its size and sha256:
    a changed file is a new line), an allow rule for a container's file (as the containers job) - or why a person must
    decide (a file in a folder users can write, an RPM-owned file, a file already trusted that changed since).
  - Reviews the rules files (rules.d, fapolicyd's own and those listed in never_edit left out) and proposes their
    clean-up - a rule commented out, a file renamed - saying whether it changes what runs.
  - Proposes permissive = 0 (it is on: a STIG finding) or permissive = 1 (blocks seen while enforcing: to see all it
    would block, blocking nothing, before approving).
  - Changes nothing; a dry run reads too.
options:
  since: {description: "ausearch -ts: recent, today, yesterday, this-week, boot, or a date and time.", type: str, default: today}
  dir: {description: fapolicyd's folder., type: str, default: /etc/fapolicyd}
  own_rules: {description: The rules file (in rules.d) the job writes its allow rules to., type: str, default: 36-site-approved.rules}
  own_trust: {description: The trust file (in trust.d) the job writes its trust entries to., type: str, default: site-approved}
  never_edit: {description: Rules files (in rules.d) the job reviews but never changes., type: list, elements: str, default: [35-site-containers.rules]}
  labels: {description: Regular expression on the SELinux labels of the program and the file that marks a container., type: str, default: ''}
  programs: {description: Container programs (base names)., type: list, elements: str, default: []}
  paths: {description: Regular expressions on the program's or the file's path that mark container storage., type: list, elements: str, default: []}
  user_dirs: {description: "Folders users can write: a blocked file there gets no trust entry (a person decides).", type: list, elements: str, default: [/home/, /run/user/, /tmp/, /var/tmp/, /dev/shm/]}
  hash_max_mb: {description: A file larger than this is not hashed (a person adds it)., type: int, default: 512}
  max_groups: {description: At most this many groups returned (the most blocked first)., type: int, default: 200}
  timeout: {description: Seconds for each command., type: int, default: 120}
author: site automation
'''

RETURN = r'''
state:
  description: "read, not installed, or not read (with error)."
  type: str
  returned: always
changes:
  description: "Each change with its approval line: [{kind (trust, rule, remove, rename, permissive), line, why, effect}]."
  type: list
  returned: success
blocks:
  description: The blocks, grouped, the most first.
  type: list
  returned: success
'''

import glob
import hashlib
import os
import pwd
import re
import stat
import sys
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_fapolicyd import (
    CGROUP_ID, RULE, SAFE, STOCK_RULES, block_kind, fan_rule, conf_values, event_path, parse_events, perm_of, propose,
    review_rules, rpm_reference, rule_list, run, same_file, storage_conf, storage_rule, why_container)

MIB = 1024 * 1024
# what each review finding becomes: (the change, its effect on what runs)
SAME = "no change in what runs"
STRICTER = "blocks more: what the rule let through is checked again"
LOOSER = "lets more through: its allow rules are reached"
RENAMED_DENY = "blocks more and lets more through: its allow rules and its deny rules are reached before the stock ones"


def cleanup_of(finding):
    """A review finding -> (kind, effect) of its clean-up, or None when only a person can say (a program's own folder)."""
    what, line = finding.get("what", ""), finding.get("line", 0)
    if line == 0:
        return ("rename", LOOSER) if "sorts after" in what else None
    # what opens too much first: its text names RPM-owned files too
    if "allows everything" in what or "any program may then run" in what or "any user who can write there" in what:
        return ("remove", STRICTER)
    if "repeats line" in what or "matches nothing" in what or what.startswith("RPM-owned") or "not a rule fapolicyd reads" in what:
        return ("remove", SAME)
    return None


def renamed(name, existing):
    """99-local.rules -> 39-local.rules: after 30-patterns, before 40-bad-elf and the stock deny rules."""
    base = re.sub(r"^\d+-", "", name)
    for n in range(39, 30, -1):
        new = "%d-%s" % (n, base)
        if new not in existing:
            return new
    return ""


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def trusted_lines(d):
    """{path: (size, sha256, trust file)} from fapolicyd.trust and trust.d/* - 'path size sha256' lines."""
    out = {}
    for f in [os.path.join(d, "fapolicyd.trust")] + sorted(glob.glob(os.path.join(d, "trust.d", "*"))):
        try:
            with open(f) as fh:
                for line in fh:
                    parts = line.strip().rsplit(" ", 2)
                    if len(parts) == 3 and parts[0].startswith("/"):
                        out[parts[0]] = (parts[1], parts[2], f)
        except (IOError, OSError, UnicodeDecodeError):
            continue
    return out


def event_epoch(stamp):
    """ausearch -i's '10/30/2026 08:00:01' (local time) -> seconds, None when it does not read."""
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%y %H:%M:%S"):
        try:
            return time.mktime(time.strptime(stamp, fmt))
        except (ValueError, OverflowError):
            continue
    return None


class Reader(object):
    def __init__(self, module):
        self.m = module
        self.p = module.params
        self._cg = {}
        self._rpm = {}

    def cgroup_of(self, pid):
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

    def rpm_owned(self, path):
        if path not in self._rpm:
            if len(self._rpm) >= 200:
                return ""
            rc, out, _ = run(["rpm", "-qf", path], 30)
            self._rpm[path] = out.strip().splitlines()[0] if rc == 0 and out.strip() and "not owned" not in out else ""
        return self._rpm[path]

    def user_of(self, name):
        if not name or name in ("root", "unset", "?"):
            return None
        try:
            pw = pwd.getpwnam(name) if not str(name).isdigit() else pwd.getpwuid(int(name))
        except (KeyError, ValueError, OverflowError):
            return None
        return {"name": pw.pw_name, "uid": pw.pw_uid, "home": pw.pw_dir}

    def host_change(self, path, trusted):
        """The trust entry for a host file - or (None, why a person must decide)."""
        real = os.path.realpath(path)
        if real.startswith(tuple(self.p["user_dirs"])):
            return None, ("%s is in a folder users can write: trusting it lets whoever writes there run anything put in its "
                          "place - move it to a system folder (e.g. /opt) first" % real)
        try:
            st = os.stat(real)
        except OSError:
            return None, "%s is not there now: nothing to trust" % real
        if not stat.S_ISREG(st.st_mode):
            return None, "%s is not a regular file" % real
        if not SAFE.match(real):
            return None, "%s has a space or a character a trust line cannot hold: add it by hand (fapolicyd-cli --file add)" % real
        pkg = self.rpm_owned(real)
        if pkg:
            return None, ("%s is RPM-owned (%s), so trusted already: fapolicyd's trust database is out of date (fapolicyd-cli "
                          "--update) or the file no longer matches the package (rpm -V %s)" % (real, pkg, pkg.rsplit("-", 2)[0]))
        if st.st_size > self.p["hash_max_mb"] * MIB:
            return None, "%s is %.0f MB: not hashed here - add it by hand after review" % (real, st.st_size / float(MIB))
        sha = sha256_of(real)
        if real in trusted:
            size, hx, where = trusted[real]
            if (size, hx) == (str(st.st_size), sha):
                return None, "%s is in %s already as it is now: fapolicyd has not loaded it (fapolicyd-cli --update)" % (real, where)
            return None, ("%s is in %s, but it changed since it was trusted (now %d bytes): if the change is approved, "
                          "fapolicyd-cli --file update %s" % (real, where, st.st_size, real))
        return {"kind": "trust", "path": real, "size": st.st_size, "sha256": sha,
                "line": "trust file: %s size %d sha256 %s" % (real, st.st_size, sha)}, ""

    def read(self):
        p, t, d = self.p, self.p["timeout"], self.p["dir"].rstrip("/")
        r = {"state": "read", "error": "", "changes": [], "blocks": [], "by_hand": [], "review": [], "rules_files": {},
             "python": sys.executable}
        rc, out, _ = run(["rpm", "-q", "fapolicyd"], t)
        if rc != 0:
            r["state"] = "not installed"
            return r
        r["version"] = out.strip()
        r["active"] = run(["systemctl", "is-active", "fapolicyd"], t)[1].strip() or "unknown"
        r["auditd"] = run(["systemctl", "is-active", "auditd"], t)[1].strip() or "unknown"
        try:
            with open(d + "/fapolicyd.conf") as f:
                conf = conf_values(f.read())
        except (IOError, OSError) as e:
            r["error"] = "cannot read %s/fapolicyd.conf: %s" % (d, e)
            return r
        permissive = conf.get("permissive", "0") == "1"
        r["permissive"] = permissive
        rules_d = d + "/rules.d"
        r["rules_d"] = os.path.isdir(rules_d)
        r["fagenrules"] = bool(self.m.get_bin_path("fagenrules"))
        rc, out, _ = run(["rpm", "-q", "--qf", "[%{FILEMODES:perms} %{FILEUSERNAME} %{FILEGROUPNAME} %{FILENAMES}\\n]", "fapolicyd"], t)
        ref = rpm_reference(out if rc == 0 else "").get("trust", ("root", "fapolicyd", 0o644))
        r["trust_ref"] = {"owner": ref[0], "group": ref[1], "mode": "%04o" % ref[2]}
        own_rules = os.path.join(rules_d, p["own_rules"])
        own_trust = os.path.join(d, "trust.d", p["own_trust"])
        r["own_rules"] = own_rules
        r["own_trust"] = own_trust
        r["own_rules_exists"] = os.path.isfile(own_rules)
        r["own_trust_exists"] = os.path.isfile(own_trust)
        try:
            with open(own_rules) as f:
                r["managed"] = [x.strip() for x in f if x.strip() and not x.strip().startswith("#")]
        except (IOError, OSError):
            r["managed"] = []

        # the rules as fapolicyd numbers them, and since when (an older event's number may point at another rule now)
        rc, out, _ = run(["fapolicyd-cli", "--list"], t)
        rule_text = rule_list(out) if rc == 0 else {}
        try:
            compiled_at = os.stat(d + "/compiled.rules").st_mtime
        except OSError:
            compiled_at = 0

        # the blocks
        if r["auditd"] != "active":
            r["by_hand"].append("auditd is %s: fapolicyd's blocks are not recorded - start auditd" % r["auditd"])
        rc, out, err = run(["ausearch", "-m", "FANOTIFY", "-ts"] + p["since"].split() + ["-i"], t)
        if rc == 124 or (rc != 0 and "no matches" not in (err + out).lower()):
            r["error"] = "ausearch failed (exit %d): %s" % (rc, (err or out).strip()[:200])
            return r
        try:
            with open("/etc/containers/storage.conf") as f:
                graph, rootless = storage_conf(f.read())
        except (IOError, OSError, UnicodeDecodeError):
            graph, rootless = storage_conf("")
        storage_roots = tuple(x for x in (graph + "/", rootless and rootless.split("$")[0], "/run/containers/storage/", "/var/lib/docker/") if x)
        labels = re.compile(p["labels"]) if p["labels"] else None
        cpaths = [re.compile(x) for x in p["paths"]]
        programs = set(p["programs"])
        groups = {}
        for e in parse_events(out):
            when = event_epoch(e["time"])
            fresh = when is not None and when >= compiled_at
            kind = block_kind(e, rule_text if fresh else {}, permissive)
            if not kind:
                continue
            sc = e["syscall"]
            path, obj, rec = event_path(e)
            exe = sc.get("exe", "")
            perm = perm_of(sc.get("syscall", ""))
            key = (exe, path, perm, kind)
            g = groups.get(key)
            if g is None:
                why, cid = why_container(e, path, obj, labels, programs, cpaths, self.cgroup_of)
                no = fan_rule(e["fan"])
                g = groups[key] = {"exe": exe, "path": path, "perm": perm, "kind": kind, "count": 0, "first": e["time"],
                                   "last": e["time"], "rule_no": no, "rule": rule_text.get(no, "") if fresh else "",
                                   "container": bool(why), "why": why, "uid": sc.get("uid", ""), "rec": rec}
            g["count"] += 1
            g["last"] = e["time"]
        trusted = trusted_lines(d)
        seen = set()
        files = []
        for f in sorted(glob.glob(os.path.join(rules_d, "*.rules")), key=lambda x: [int(c) if c.isdigit() else c for c in re.split(r"(\d+)", os.path.basename(x))]):
            try:
                with open(f) as fh:
                    text = fh.read()
            except (IOError, OSError, UnicodeDecodeError):
                text = ""
            files.append((os.path.basename(f), text))
        in_files = {}
        for name, text in files:
            for x in text.splitlines():
                x = " ".join(x.split())
                if x and not x.startswith("#"):
                    in_files.setdefault(x, name)
        for g in sorted(groups.values(), key=lambda g: (-g["count"], g["exe"], g["path"]))[:p["max_groups"]]:
            rec = g.pop("rec")
            change, note = None, ""
            if g["container"]:
                in_storage = g["path"].startswith("/") and (g["path"].startswith(storage_roots) or not same_file(g["path"], rec))
                if in_storage:
                    rule, note = storage_rule(g["path"], g["exe"], self.user_of(g["uid"]), graph, rootless)
                    g["where"] = "container storage"
                else:
                    rule, note = propose(g["exe"], g["path"], g["perm"], "path", True)
                    g["where"] = "the host (a container's)"
                if rule and rule not in in_files:
                    change = {"kind": "rule", "rule": rule, "line": "allow rule: %s" % rule}
                elif rule:
                    note = "already in %s: blocked before it was added, or fapolicyd has not loaded it" % in_files[rule]
            elif g["path"].startswith("/"):
                g["where"] = "the host"
                change, note = self.host_change(g["path"], trusted)
            else:
                g["where"] = "?"
                note = "the file's full path is not in the audit record: decide by hand"
            g["change"] = change["line"] if change else ""
            g["note"] = note
            if change and change["line"] not in seen:
                seen.add(change["line"])
                change["why"] = "%s %s %s x%d (%s%s)" % (g["kind"], g["exe"] or "?", g["path"], g["count"], g["perm"],
                                                        ", rule %s" % g["rule_no"] if g["rule_no"] else "")
                change["effect"] = "lets this through"
                if change["kind"] == "rule" and note:
                    change["why"] += " - " + note
                r["changes"].append(change)
            elif not change and note:
                r["by_hand"].append("%s %s -> %s: %s" % (g["kind"], g["exe"] or "?", g["path"] or "?", note))
            r["blocks"].append(g)

        # the rules files: their review, and the clean-up of the ones this job may edit
        names = set(n for n, _ in files)
        review = review_rules(files, self.rpm_owned)
        r["review"] = review
        for f in review:
            c = cleanup_of(f)
            name = f["file"]
            if not c:
                continue
            if name in STOCK_RULES or name in p["never_edit"] or name == p["own_rules"]:
                r["by_hand"].append("%s%s: %s - %s (not edited by this job%s)" % (
                    name, (" line %d" % f["line"]) if f["line"] else "", f["rule"] or "the file", f["what"],
                    ": the containers job's own file" if name in p["never_edit"] else ""))
                continue
            if c[0] == "rename":
                new = renamed(name, names)
                if not new:
                    continue
                line = "rename rules file: %s -> %s" % (name, new)
                ch = {"kind": "rename", "file": name, "to": new, "line": line}
                # its deny rules are reached earlier too: it blocks more as well
                if any(RULE.match(x) and x.startswith("deny") for x in (" ".join(y.split()) for y in dict(files)[name].splitlines())):
                    c = ("rename", RENAMED_DENY)
            else:
                line = "remove rule: %s: %s" % (name, f["rule"])
                ch = {"kind": "remove", "file": name, "rule": f["rule"], "line": line}
            if line in seen:
                continue
            seen.add(line)
            ch.update({"why": f["what"], "effect": c[1], "stricter": c[1] in (STRICTER, RENAMED_DENY)})
            r["changes"].append(ch)
            r["rules_files"][name] = dict(files)[name]

        # permissive
        would = sum(g["count"] for g in r["blocks"] if g["kind"] == "would deny")
        if permissive:
            r["changes"].append({"kind": "permissive", "from": "1", "to": "0", "effect": "blocks what it only logged until now",
                                 "why": "fapolicyd is permissive - it blocks nothing (a RHEL STIG requires permissive = 0). %d "
                                        "would-be block(s) since %s: approve their changes first, or they will be blocked"
                                        % (would, p["since"]), "line": "permissive: 1 -> 0"})
        elif r["active"] == "active" and any(g["kind"] == "denied" for g in r["blocks"]):
            r["changes"].append({"kind": "permissive", "from": "0", "to": "1", "effect": "blocks nothing until permissive: 1 -> 0",
                                 "why": "to see all it would block before approving changes, blocking nothing meanwhile - keep it "
                                        "short: a RHEL STIG requires permissive = 0", "line": "permissive: 0 -> 1"})
        r["would"] = would
        return r


def main():
    module = AnsibleModule(argument_spec=dict(
        since=dict(type="str", default="today"),
        dir=dict(type="str", default="/etc/fapolicyd"),
        own_rules=dict(type="str", default="36-site-approved.rules"),
        own_trust=dict(type="str", default="site-approved"),
        never_edit=dict(type="list", elements="str", default=["35-site-containers.rules"]),
        labels=dict(type="str", default=""),
        programs=dict(type="list", elements="str", default=[]),
        paths=dict(type="list", elements="str", default=[]),
        user_dirs=dict(type="list", elements="str", default=["/home/", "/run/user/", "/tmp/", "/var/tmp/", "/dev/shm/"]),
        hash_max_mb=dict(type="int", default=512),
        max_groups=dict(type="int", default=200),
        timeout=dict(type="int", default=120),
    ), supports_check_mode=True)
    try:
        result = Reader(module).read()
    except Exception as e:                      # noqa: BLE001 - a host that cannot be read is a row in the report
        result = {"state": "not read", "error": "%s: %s" % (e.__class__.__name__, e), "changes": [], "blocks": [], "by_hand": [],
                  "review": [], "rules_files": {}}
    module.exit_json(changed=False, **result)


if __name__ == "__main__":
    main()
