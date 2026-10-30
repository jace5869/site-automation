# -*- coding: utf-8 -*-
"""What the fapolicyd jobs read with (roles/fapolicyd_containers, roles/fapolicyd_rules, roles/fapolicyd_fix): the
audit log's fapolicyd events (ausearch -m FANOTIFY -i, or raw), the file each was about, a block told apart from an
audited allow (permissive mode answers allow and keeps the audit bit: a would-be denial is resp=allow with a deny
rule's number), the allow rule for a group, the review of the rules files in place, the fapolicyd package's own owner
and modes. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import os
import re
import subprocess

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


# ---- blocks: denied, or would be denied (permissive) --------------------------------------------------------------
DENY_RESP = ("deny", "2", "0x2", "0x02")
ALLOW_RESP = ("allow", "1", "0x1", "0x01")


def fan_rule(fan):
    """The rule number of a FANOTIFY record, as fapolicyd-cli --list numbers them ('' = none). The kernel writes fan_info
    in hex; ausearch -i (audit 3.1 and later) writes it in decimal, with fan_type=rule_info; older audit leaves both as
    the kernel wrote them (fan_type=1, fan_info in hex)."""
    fan = fan or {}
    t, v = str(fan.get("fan_type", "")), str(fan.get("fan_info", ""))
    try:
        if t == "rule_info":
            return str(int(v, 10))
        if t == "1":
            return str(int(v, 16))
    except ValueError:
        pass
    return ""


def block_kind(e, rule_text, permissive):
    """'denied' (resp=deny), 'would deny' (resp=allow - permissive mode answers allow and keeps the audit bit - with a
    deny rule's number, or, without the rule's number - a kernel without rule info, RHEL 8 - any audited allow while
    fapolicyd is permissive), or '' (an allow_audit rule's allow)."""
    fan = e.get("fan") or {}
    resp = str(fan.get("resp", "")).lower()
    if resp in DENY_RESP:
        return "denied"
    if resp not in ALLOW_RESP:
        return ""
    no = fan_rule(fan)
    rule = rule_text.get(no) if no else None
    if rule is not None:
        return "would deny" if rule.startswith("deny") else ""
    return "would deny" if permissive else ""


def rule_list(text):
    """fapolicyd-cli --list -> {rule number: rule text} (the %set lines left out)."""
    found = {}
    for line in str(text).splitlines():
        m = re.match(r"\s*(\d+)\.\s+(.*\S)", line)
        if m:
            found[m.group(1)] = m.group(2)
    return found


# ---- the fapolicyd package's own owner, group and modes -----------------------------------------------------------
def perms_mode(text):
    """'-rw-r--r--' -> 0o644 (the permission bits only)."""
    bits = 0
    for i, ch in enumerate(str(text)[1:10]):
        if ch not in "-ST":
            bits |= 1 << (8 - i)
    return bits


def rpm_reference(text):
    """rpm -q --qf '[%{FILEMODES:perms} %{FILEUSERNAME} %{FILEGROUPNAME} %{FILENAMES}\\n]' fapolicyd ->
    {'dir' | 'trust' | 'trust.d': (owner, group, mode)} for /etc/fapolicyd, its fapolicyd.trust and trust.d."""
    keys = {"/etc/fapolicyd": "dir", "/etc/fapolicyd/fapolicyd.trust": "trust", "/etc/fapolicyd/trust.d": "trust.d"}
    ref = {}
    for line in str(text).splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4 and parts[3].strip() in keys:
            ref[keys[parts[3].strip()]] = (parts[1], parts[2], perms_mode(parts[0]))
    return ref


def conf_values(text):
    """fapolicyd.conf -> {key: value} (comments and blank lines left out)."""
    out = {}
    for line in str(text).splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out
