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
import subprocess

from ansible.module_utils.basic import AnsibleModule

EXEC_CALLS = ("execve", "execveat", "uselib")
# folders a rule's dir= must never open: everything the host itself runs is under them
SYSTEM_DIRS = ("/", "/bin/", "/sbin/", "/usr/", "/usr/bin/", "/usr/sbin/", "/usr/lib/", "/usr/lib64/", "/usr/libexec/",
               "/usr/local/", "/usr/local/bin/", "/usr/local/sbin/", "/usr/local/lib/", "/usr/local/lib64/", "/lib/",
               "/lib64/", "/etc/", "/tmp/", "/var/", "/var/tmp/", "/dev/", "/dev/shm/", "/run/", "/home/", "/root/", "/opt/")
# everything under these is the host's own (RPM-owned files there are trusted already) ...
SYSTEM_ROOTS = ("/usr/", "/lib/", "/lib64/", "/bin/", "/sbin/", "/etc/")
# ... and under these a user may write
USER_ROOTS = ("/home/", "/run/user/", "/tmp/", "/var/tmp/", "/dev/shm/")
# fapolicyd's own rules files, in the order fagenrules takes them: 41 and 43 deny untrusted libraries and programs
STOCK_RULES = ("10-languages.rules", "20-dracut.rules", "21-updaters.rules", "30-patterns.rules", "40-bad-elf.rules",
               "41-shared-obj.rules", "42-trusted-elf.rules", "43-known-elf.rules", "70-trusted-lang.rules", "72-shell.rules",
               "90-deny-execute.rules", "95-allow-open.rules")
LAST_BEFORE_DENY = "41-shared-obj.rules"              # a site file must sort before this one to allow untrusted files
KV = re.compile(r'(\w+)=("[^"]*"|\S+)')
SAFE = re.compile(r"^/[A-Za-z0-9._+@%:,/=~-]*$")         # a value a rule can hold (no space, no quote)
CGROUP_ID = re.compile(r"(?:libpod-|docker-|crio-|containers/)([0-9a-f]{12,64})")
RULE = re.compile(r"^(allow|allow_audit|allow_syslog|allow_log|deny|deny_audit|deny_syslog|deny_log)\s+perm=(\S+)\s+(.+?)\s*:\s*(.+)$")
DEV = re.compile(r"^[0-9a-fA-F]+:[0-9a-fA-F]+$")


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
    """The file the event was about: (the first PATH record's name, made absolute with the CWD record; its SELinux
    label; the record itself - inode, dev)."""
    paths = sorted(e["paths"], key=lambda p: int(p.get("item", "0")) if str(p.get("item", "0")).isdigit() else 0)
    for p in paths:
        name = p.get("name", "")
        if name and name not in ("(null)", "?"):
            if not name.startswith("/") and e["cwd"].startswith("/"):
                name = os.path.normpath(os.path.join(e["cwd"], name))
            return name, p.get("obj", ""), p
    return "", "", {}


def same_file(path, rec):
    """The file at path on the host is the one the audit record is about - the same inode (and device, when the
    record has one): a host file, bind-mounted into the container at the same path. False for a file inside an image:
    its path in the record is the container's, and names nothing - or another file - on the host."""
    if not path.startswith("/"):
        return False
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return False
    inode = str(rec.get("inode", ""))
    if not inode.isdigit() or int(inode) != st.st_ino:
        return False
    dev = str(rec.get("dev", ""))
    if DEV.match(dev):
        major, minor = (int(x, 16) for x in dev.split(":"))
        return (os.major(st.st_dev), os.minor(st.st_dev)) == (major, minor)
    return True


def storage_conf(text):
    """podman's storage.conf -> (graphroot, rootless_storage_path) - podman's own defaults when not set."""
    graph, rootless = "/var/lib/containers/storage", ""
    for line in (text or "").splitlines():
        m = re.match(r'^\s*(graphroot|rootless_storage_path)\s*=\s*"?([^"#]*?)"?\s*(#.*)?$', line)
        if m and m.group(2).strip():
            if m.group(1) == "graphroot":
                graph = m.group(2).strip()
            else:
                rootless = m.group(2).strip()
    return graph.rstrip("/") or "/var/lib/containers/storage", rootless.rstrip("/")


def storage_rule(path, exe, user, graph, rootless):
    """The rule for a file in container storage, and its note. user: {name, uid, home} of the denial's user (None =
    root, or not known). Docker's storage when the program or the file is under /var/lib/docker; the user's own
    rootless storage when the denial is a user's and that storage exists; root's otherwise - the overlay folder (every
    image's and container's files), or the volumes folder when the file is there."""
    note = ("every file in container storage, for any program: a file inside an image has no host path of its own to "
            "name (the audit log holds the container's path, and the image's layer changes at each pull)")
    if path.startswith("/var/lib/docker/") or exe.startswith("/var/lib/docker/"):
        root = "/var/lib/docker/overlay2/"
    elif path.startswith("/run/containers/storage/"):
        root = "/run/containers/storage/"
    else:
        root = graph + "/overlay/"
        if path.startswith(graph + "/volumes/"):
            root = graph + "/volumes/"
        if user and user.get("uid", 0) >= 1000:
            own = ((rootless.replace("$HOME", user.get("home", "")).replace("$USER", user.get("name", ""))
                    .replace("$UID", str(user.get("uid", ""))).rstrip("/")) if rootless else "") or (user.get("home", "").rstrip("/") + "/.local/share/containers/storage")
            if own.startswith("/") and os.path.isdir(own):
                root = own + ("/volumes/" if path.startswith(own + "/volumes/") else "/overlay/")
                note += ("; %s's own storage: anything they put there may then run - on a STIG host, prefer rootful "
                         "containers (quadlet units)" % user.get("name", "the user"))
    if not SAFE.match(root):
        return "", "the storage path has a character a rule cannot hold: add a rule by hand"
    return "allow perm=any all : dir=%s" % root, note


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


def _values(text):
    """'exe=/x dir=/y/' -> [(name, value)]; 'all' -> [('all', '')]."""
    return [(a.partition("=")[0], a.partition("=")[2]) for a in text.split()]


def review_rules(files, rpm_owned, last_before_deny=LAST_BEFORE_DENY):
    """The rules files in place -> what to tighten: [{file, line, rule, what, status}]. files: [(name, text)], in
    fagenrules' order; rpm_owned(path) -> the package that owns it, or ''. Line 0 is about the file itself."""
    out = []
    seen = {}
    for name, text in files:
        if name in STOCK_RULES:                        # fapolicyd's own: not reviewed
            continue
        allows_untrusted = False
        for no, raw in enumerate(text.splitlines(), 1):
            line = " ".join(raw.split())
            if not line or line.startswith("#"):
                continue
            m = RULE.match(line)
            if not m:
                out.append({"file": name, "line": no, "rule": line, "status": "warning", "what": "not a rule fapolicyd reads: decision perm=... SUBJECT : OBJECT"})
                continue
            decision, perm, subj, obj = m.groups()
            allow = decision.startswith("allow")
            sv, ov = _values(subj), _values(obj)
            findings = []
            dirs = [v for k, v in ov if k == "dir"]
            if any(("*" in v or "?" in v or "[" in v) for k, v in sv + ov if k in ("dir", "path", "exe", "device")):
                findings.append(("warning", "matches nothing: fapolicyd has no wildcards - name each folder or file"))
                dirs = []                              # what it would open does not matter: it opens nothing
            if allow and subj == "all" and (obj == "all" or ("dir", "/") in ov):
                findings.append(("critical", "allows everything (%s): fapolicyd is off for that - the last rule must be the deny" % perm))
            if allow and any(d in SYSTEM_DIRS or d.startswith(SYSTEM_ROOTS) for d in dirs) and not (("dir", "/") in ov):
                if subj == "all":
                    findings.append(("warning", "any program may then run any untrusted file under a system folder; RPM-owned files "
                                                "there are trusted already (trust=1) - name the file (path=) or the program (exe=)"))
                else:
                    findings.append(("info", "untrusted files under a system folder, for that program; RPM-owned files there are trusted already"))
            if allow and subj == "all" and any(d.startswith(USER_ROOTS) for d in dirs):
                findings.append(("warning", "any user who can write there may then run anything they put there"))
            if line in seen:
                findings.append(("info", "repeats line %d of %s" % seen[line]))
            else:
                seen[line] = (no, name)
            if allow and not findings:
                for k, v in ov:
                    if k == "path" and v.startswith("/"):
                        pkg = rpm_owned(v)
                        if pkg:
                            findings.append(("info", "RPM-owned (%s): trusted already, the rule adds nothing" % pkg))
            if allow and ("trust", "1") not in ov and perm in ("any", "execute", "open"):
                allows_untrusted = True
            for st, what in findings:
                out.append({"file": name, "line": no, "rule": line, "status": st, "what": what})
        if allows_untrusted and name not in STOCK_RULES and name > last_before_deny:
            out.append({"file": name, "line": 0, "rule": "", "status": "warning",
                        "what": "sorts after %s: the stock 41-shared-obj and 43-known-elf rules deny untrusted libraries and "
                                "programs before this file is reached - name it 3x-... (the job's own is 35-site-containers.rules)"
                                % last_before_deny})
    return out


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
                rule_no = str(e["fan"].get("fan_info", ""))
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
