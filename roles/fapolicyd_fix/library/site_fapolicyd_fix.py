#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Why fapolicyd is down, or near its limits, on a Linux host - and the fix for each, worded as the line someone
approves: trust files fapolicyd cannot read (owner, group, mode against the package's own), their SELinux labels,
db_max_size too small for the trust database, fapolicyd not running. And what only a person can judge: a very large
trust file. Read-only. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_fapolicyd_fix
short_description: Why fapolicyd is down or near its limits, and the fix for each, for approval
description:
  - Compares the trust files (C(fapolicyd.trust), C(trust.d) and each file in it, and the folder) with the owner,
    group and mode the fapolicyd package gives C(fapolicyd.trust) and C(trust.d) (C(rpm -q --qf ... fapolicyd)); a
    file the fapolicyd user (C(uid)/C(gid) of fapolicyd.conf) cannot read, or that anyone but root may write, gets a
    fix to the package's own.
  - SELinux labels that differ from the policy's (C(restorecon -R -n -v)).
  - C(db_max_size) against the trust database's needs - fapolicyd's own journal lines (C(db_max_size needs to be
    increased), C(Trust database at N% capacity)) or an estimate from the trust files' size and the RPM files - with
    C(auto) proposed where this fapolicyd sizes its database itself (1.4 and later), else a number.
  - fapolicyd not enabled at boot (systemctl enable), approved as its own line.
  - fapolicyd not running - a restart, approved as its own line; and permissive = 1 first (it blocks nothing, logs what
    it would block), approved as its own line.
  - A trust file over C(large_mb), listed for a person (every file in it is trusted).
  - Changes nothing; a dry run reads too.
options:
  dir: {description: fapolicyd's folder., type: str, default: /etc/fapolicyd}
  binary: {description: The fapolicyd program (read to see whether it sizes its database itself)., type: str, default: /usr/sbin/fapolicyd}
  owner: {description: The trust files' owner when the package does not say., type: str, default: root}
  group: {description: Their group when the package does not say., type: str, default: fapolicyd}
  file_mode: {description: A trust file's mode when the package does not say., type: str, default: '0644'}
  dir_mode: {description: The folders' mode when the package does not say., type: str, default: '0750'}
  large_mb: {description: A trust file larger than this many MB is listed for a person., type: int, default: 20}
  headroom: {description: The proposed db_max_size is the estimate times this., type: float, default: 1.5}
  journal_lines: {description: fapolicyd's journal lines read (this boot)., type: int, default: 400}
  timeout: {description: Seconds for each command., type: int, default: 120}
author: site automation
'''

RETURN = r'''
state:
  description: "read, not installed, or not read (with error)."
  type: str
  returned: always
fixes:
  description: "The fixes, each with its approval line: [{kind (access, label, db, restart), line, path, from, to, why, ...}]."
  type: list
  returned: success
  sample: [{kind: access, line: "trust file access: /etc/fapolicyd/trust.d/app root:root 0600 -> root:fapolicyd 0644",
            path: /etc/fapolicyd/trust.d/app, from: "root:root 0600", to: "root:fapolicyd 0644", owner: root, group: fapolicyd,
            mode: "0644", why: "the fapolicyd user cannot read it"}]
by_hand:
  description: What only a person can judge (a very large trust file).
  type: list
  returned: success
db:
  description: "{current, auto, need_mb, trust_mb, rpm_files, evidence}: db_max_size and what it was weighed against."
  type: dict
  returned: success
journal:
  description: fapolicyd's journal lines of its last run (this boot) that say something went wrong (the last 15).
  type: list
  returned: success
'''

import grp
import math
import os
import pwd
import re
import stat
import subprocess
import sys

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_fapolicyd import conf_values, perms_mode, rpm_reference  # noqa: F401

MIB = 1024 * 1024
ENTRY = 160            # bytes of trust data an RPM file adds (path, size, hash) - before LMDB's own overhead
LMDB = 2.0             # LMDB's pages and tree: about twice the data
DB_SAYS = re.compile(r"db_max_size needs to be increased|might want to increase db_max_size|trust DB full|failed to grow trust DB")
CAPACITY = re.compile(r"Trust database at (\d+)% capacity - might want to increase")
CANNOT_READ = re.compile(r"Cannot read (\S+) \((.*)\)")
RELABEL = re.compile(r"Would relabel (\S+) from (\S+) to (\S+)")
TROUBLE = re.compile(r"error|cannot|failed|denied|too small|increase|unable|refus", re.I)


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


def who(conf):
    """The fapolicyd user's (uid, [gids]) from fapolicyd.conf's uid and gid (names or numbers); None when unknown."""
    uid, gids = None, []
    u, g = conf.get("uid", "fapolicyd"), conf.get("gid", "fapolicyd")
    try:
        pw = pwd.getpwuid(int(u)) if u.isdigit() else pwd.getpwnam(u)
        uid = pw.pw_uid
        gids.append(pw.pw_gid)
        gids += [x.gr_gid for x in grp.getgrall() if pw.pw_name in x.gr_mem]
    except (KeyError, ValueError):
        pass
    try:
        gids.append(int(g) if g.isdigit() else grp.getgrnam(g).gr_gid)
    except (KeyError, ValueError):
        pass
    return uid, sorted(set(gids))


def readable(mode, fuid, fgid, is_dir, uid, gids):
    """Can that user (uid, gids) read a file of that mode, owner and group (a folder: and go through it)?"""
    if uid == 0:
        return True
    if uid is not None and fuid == uid:
        bits = (mode >> 6) & 7
    elif fgid in gids:
        bits = (mode >> 3) & 7
    else:
        bits = mode & 7
    need = 5 if is_dir else 4
    return bits & need == need


def name_of(uid, gid):
    try:
        u = pwd.getpwuid(uid).pw_name
    except KeyError:
        u = str(uid)
    try:
        g = grp.getgrgid(gid).gr_name
    except KeyError:
        g = str(gid)
    return u, g


def id_of(owner, group):
    """(uid, gid) of names (or numbers); None for one that does not exist on this host."""
    try:
        u = int(owner) if str(owner).isdigit() else pwd.getpwnam(owner).pw_uid
    except KeyError:
        u = None
    try:
        g = int(group) if str(group).isdigit() else grp.getgrnam(group).gr_gid
    except KeyError:
        g = None
    return u, g


def access_fix(path, st, ref, uid, gids, cannot_read):
    """The fix for one trust file or folder - to the package's owner, group and mode - or None when it is fine."""
    u, g = name_of(st.st_uid, st.st_gid)
    mode, is_dir = stat.S_IMODE(st.st_mode), stat.S_ISDIR(st.st_mode)
    why = []
    if not readable(mode, st.st_uid, st.st_gid, is_dir, uid, gids):
        why.append("the fapolicyd user cannot open the folder" if is_dir else "the fapolicyd user cannot read it")
    if st.st_uid != 0 and u != ref[0]:
        why.append("owned by %s, not root" % u)
    if mode & 0o022:
        why.append("writable by its group or by everyone: anyone in it could trust a program")
    if path in cannot_read:
        why.append("fapolicyd says: Cannot read it (%s)" % cannot_read[path])
    if not why:
        return None
    frm, to = "%s:%s %04o" % (u, g, mode), "%s:%s %04o" % (ref[0], ref[1], ref[2])
    if frm == to:
        return None
    ru, rg = id_of(ref[0], ref[1])
    # the package's own mode must let fapolicyd read it; else say so - the fix would not help
    if not readable(ref[2], st.st_uid if ru is None else ru, st.st_gid if rg is None else rg, is_dir, uid, gids):
        why.append("the package's own %s would not let the fapolicyd user read it either: look at uid and gid in fapolicyd.conf" % to)
    return {"kind": "access", "path": path, "from": frm, "to": to, "owner": ref[0], "group": ref[1], "mode": "%04o" % ref[2],
            "why": "; ".join(why), "line": "trust file access: %s %s -> %s" % (path, frm, to)}


def db_fix(current, auto_ok, says, need_mb, active, headroom=1.5, trust_mb=0.0, rpm_files=0):
    """The db_max_size fix - or None. fapolicyd's own journal lines (says) are the reason; while it is down, also the
    estimate (need_mb) over db_max_size - a running fapolicyd says itself when its database gets full, and the estimate
    counts every RPM file (folders and docs too), more than fapolicyd keeps. auto where this fapolicyd can (1.4+)."""
    cur = int(current) if str(current).isdigit() else (100 if auto_ok else 50)   # fapolicyd's own default: 1.3: 50, 1.4: 100
    if current == "auto" or not (says or (active != "active" and need_mb >= cur)):
        return None
    proposed = "auto" if auto_ok else str(int(math.ceil(max(need_mb * headroom, cur * 2 if says else 0) / 50.0) * 50))
    why = ("fapolicyd says: %s" % says[-1]) if says else (
        "fapolicyd is %s and the estimate (%d MB: %.1f MB of trust files and %d RPM files, about twice that in the database) "
        "is over db_max_size %s" % (active, need_mb, trust_mb, rpm_files, current or "(unset: %d)" % cur))
    return {"kind": "db", "from": current or "unset", "to": proposed, "auto": auto_ok,
            "why": why + ("; this fapolicyd sizes its database itself with auto" if auto_ok else ""),
            "line": "db_max_size: %s -> %s" % (current or "unset", proposed)}


def lines_in(path):
    n = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            n += chunk.count(b"\n")
    return n


class Reader(object):
    def __init__(self, module):
        self.m = module
        self.p = module.params

    def read(self):
        p, t = self.p, self.p["timeout"]
        r = {"state": "read", "error": "", "fixes": [], "by_hand": [], "journal": [], "db": {}, "python": sys.executable}
        rc, out, _ = run(["rpm", "-q", "fapolicyd"], t)
        if rc != 0:
            r["state"] = "not installed"
            return r
        r["version"] = out.strip()
        rc, out, _ = run(["systemctl", "is-active", "fapolicyd"], t)
        r["active"] = out.strip() or "unknown"
        r["enabled"] = run(["systemctl", "is-enabled", "fapolicyd"], t)[1].strip() or "unknown"
        d = p["dir"].rstrip("/")
        conf_path = d + "/fapolicyd.conf"
        try:
            with open(conf_path) as f:
                conf = conf_values(f.read())
        except (IOError, OSError) as e:
            r["error"] = "cannot read %s: %s" % (conf_path, e)
            return r
        r["permissive"] = conf.get("permissive", "0") == "1"
        uid, gids = who(conf)

        # the journal: what fapolicyd said in its last run (this boot's earlier runs said what a fix may have changed since)
        rc, inv, _ = run(["systemctl", "show", "-p", "InvocationID", "--value", "fapolicyd"], t)
        inv = inv.strip() if rc == 0 and re.match(r"^[0-9a-f]{32}$", inv.strip()) else ""
        rc, jout, _ = run(["journalctl", "-b", "--no-pager", "-o", "cat", "-n", str(p["journal_lines"])]
                          + (["_SYSTEMD_INVOCATION_ID=" + inv, "+", "INVOCATION_ID=" + inv] if inv else ["-u", "fapolicyd"]), t)
        jlines = [x.strip() for x in jout.splitlines() if x.strip()] if rc == 0 else []
        cannot_read = {}
        for x in jlines:
            m = CANNOT_READ.search(x)
            if m:
                cannot_read[m.group(1)] = m.group(2)
        r["journal"] = [x for x in jlines if TROUBLE.search(x)][-15:]

        # the trust files against the package's own owner, group and mode
        rc, out, _ = run(["rpm", "-q", "--qf", "[%{FILEMODES:perms} %{FILEUSERNAME} %{FILEGROUPNAME} %{FILENAMES}\\n]", "fapolicyd"], t)
        ref = rpm_reference(out if rc == 0 else "")
        fmode, dmode = int(p["file_mode"], 8), int(p["dir_mode"], 8)
        ref_file = ref.get("trust", (p["owner"], p["group"], fmode))
        ref_dir = ref.get("trust.d", (p["owner"], p["group"], dmode))
        ref_top = ref.get("dir", ref_dir)
        r["reference"] = {"file": "%s:%s %04o" % ref_file, "folder": "%s:%s %04o" % ref_dir,
                          "from": "the fapolicyd package" if ref else "the role's defaults (rpm gave none)"}
        trust_d = d + "/trust.d"
        files = [(d, ref_top), (d + "/fapolicyd.trust", ref_file), (trust_d, ref_dir)]
        if os.path.isdir(trust_d) and not os.path.islink(trust_d):
            files += [(os.path.join(trust_d, n), ref_file) for n in sorted(os.listdir(trust_d))]
        trust_bytes, big = 0, []
        for path, rf in files:
            try:
                st = os.lstat(path)
            except OSError:
                continue
            if stat.S_ISLNK(st.st_mode) or not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
                r["by_hand"].append("%s: not a regular file or folder - fapolicyd reads only files; look at it" % path)
                continue
            if stat.S_ISREG(st.st_mode) and path != d:
                trust_bytes += st.st_size
                if st.st_size > p["large_mb"] * MIB:
                    big.append((path, st.st_size))
            fx = access_fix(path, st, rf, uid, gids, cannot_read)
            if fx:
                r["fixes"].append(fx)
        for path, size in big:
            try:
                n = lines_in(path)
            except (IOError, OSError):
                n = -1
            r["by_hand"].append("%s: a large trust file, %.1f MB%s - every file listed in it is trusted. Check what wrote it and "
                                "whether all of it is needed: the trust database must hold it (db_max_size), and fapolicyd reads it "
                                "at each start." % (path, size / float(MIB), (", %d lines" % n) if n >= 0 else ""))

        # SELinux labels, as the policy has them
        if run(["selinuxenabled"], t)[0] == 0:
            rc, out, err = run(["restorecon", "-R", "-n", "-v", d], t)
            for x in (out + "\n" + err).splitlines():
                m = RELABEL.search(x)
                if m:
                    r["fixes"].append({"kind": "label", "path": m.group(1), "from": m.group(2), "to": m.group(3),
                                       "why": "its SELinux label is not the policy's: fapolicyd may not be allowed to read it",
                                       "line": "SELinux label: %s %s -> %s" % (m.group(1), m.group(2), m.group(3))})

        # db_max_size: what fapolicyd said, or the estimate
        try:
            with open(p["binary"], "rb") as f:
                auto_ok = b"autosize" in f.read()
        except (IOError, OSError):
            auto_ok = False
        current = conf.get("db_max_size", "")
        rpm_files = 0
        if "rpmdb" in conf.get("trust", "rpmdb,file"):
            rc, out, _ = run(["rpm", "-qa", "--qf", "[%{FILENAMES}\\n]"], t)
            rpm_files = out.count("\n") if rc == 0 else 0
        need_mb = int(math.ceil((trust_bytes + rpm_files * ENTRY) * LMDB / MIB))
        says = [x for x in jlines if DB_SAYS.search(x) or CAPACITY.search(x)]
        r["db"] = {"current": current or "unset", "auto": auto_ok, "need_mb": need_mb, "trust_mb": round(trust_bytes / float(MIB), 1),
                   "rpm_files": rpm_files, "evidence": says[-3:]}
        fx = db_fix(current, auto_ok, says, need_mb, r["active"], p["headroom"], trust_bytes / float(MIB), rpm_files)
        if fx:
            r["fixes"].append(fx)
        elif current == "auto" and any("trust DB full" in x or "failed to grow" in x for x in says):
            r["by_hand"].append("db_max_size is auto and fapolicyd says its database is full: %s - look at the disk under "
                                "/var/lib/fapolicyd and the trust files' size" % says[-1])

        # it starts at boot: a disabled fapolicyd is gone again after the next reboot
        if r["enabled"] == "disabled":
            r["fixes"].append({"kind": "enable", "from": "disabled", "to": "enabled",
                               "why": "fapolicyd does not start at boot: a reboot leaves the host without application allow-listing",
                               "line": "enable fapolicyd at boot (it is disabled)"})
        elif r["enabled"] == "masked":
            r["by_hand"].append("fapolicyd is masked (systemctl mask): find out who masked it and why, then systemctl unmask fapolicyd")

        # last: fapolicyd not running - and the way to start it without blocking anything yet
        if r["active"] != "active" and conf.get("permissive", "0") != "1":
            r["fixes"].append({"kind": "permissive", "from": "0", "to": "1",
                               "why": "start it permissive first: it logs what it would block and blocks nothing. The fapolicyd "
                                      "rules job then shows what it would block, for approval, and turns it back (permissive: "
                                      "1 -> 0) - a RHEL STIG requires permissive = 0, so keep it short",
                               "line": "permissive: 0 -> 1"})
        if r["active"] != "active":
            r["fixes"].append({"kind": "restart", "from": r["active"], "to": "active",
                               "why": "fapolicyd is %s: nothing is allow-listed" % r["active"],
                               "line": "restart fapolicyd (it is %s)" % r["active"]})
        return r


def main():
    module = AnsibleModule(argument_spec=dict(
        dir=dict(type="str", default="/etc/fapolicyd"),
        binary=dict(type="str", default="/usr/sbin/fapolicyd"),
        owner=dict(type="str", default="root"),
        group=dict(type="str", default="fapolicyd"),
        file_mode=dict(type="str", default="0644"),
        dir_mode=dict(type="str", default="0750"),
        large_mb=dict(type="int", default=20),
        headroom=dict(type="float", default=1.5),
        journal_lines=dict(type="int", default=400),
        timeout=dict(type="int", default=120),
    ), supports_check_mode=True)
    try:
        result = Reader(module).read()
    except Exception as e:                      # noqa: BLE001 - a host that cannot be read is a row in the report
        result = {"state": "not read", "error": "%s: %s" % (e.__class__.__name__, e), "fixes": [], "by_hand": [], "journal": [], "db": {}}
    module.exit_json(changed=False, **result)


if __name__ == "__main__":
    main()
