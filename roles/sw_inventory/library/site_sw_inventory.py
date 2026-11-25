#!/usr/bin/python
# -*- coding: utf-8 -*-
"""1.133.0: the Linux software inventory's controller step (playbooks/linux_sw_inventory.yml, docs/LINUX_SW_INVENTORY.md):
the hosts' reads (roles/sw_inventory/files/sw_read.sh, one file per host in work_dir) parsed here, compared with each
host's last list (sw-inventory-state.json) and written - every export on its own: one that fails says which, why (an
SA-SWI code) and the fix, and the others are still written.

  shield     a text file per host (#shield-host v1): <dir>/<shield_subdir>/<short name>.txt
  csv, json  sw-inventory-YYYYMMDD-HHMM.csv / .json and sw-inventory-latest.csv / .json in dir
  share      every file above also on a Windows file share over SMB (smbprotocol; the "Windows file share" credential),
             or a folder mounted on the execution node; each file on its own

The package lists never come back in the result (AAP keeps every task's result): counts, the report, file names.
Needs smbprotocol for a share (the all-in-one EE has it)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_sw_inventory
short_description: Parse the hosts' rpm reads and write the software inventory's exports
description:
  - Reads <work_dir>/<sha1 of the host name>.read for each host of hosts; a host with a reason, or no file, is not read
    (skipped hosts are left out).
  - Compares each host with its last list in sw-inventory-state.json (dir, else the share); a host not read this run keeps
    its last list.
  - Writes each export of outputs on its own, then copies the files to the share, each file on its own; check mode writes
    nothing (the share is logged in to and its folder looked at).
  - Deletes only this job's own dated files (sw-inventory-YYYYMMDD-HHMM.csv / .json) older than keep_days.
options:
  work_dir: {description: The folder of the hosts' reads., type: str, required: true}
  hosts: {description: "[{host, reason, skipped}] - every host of the play.", type: list, elements: dict, required: true}
  read_at: {description: When this run started (UTC)., type: str, required: true}
  job: {description: This job's ID., type: int, default: 0}
  outputs: {description: "Any of shield, csv, json.", type: list, elements: str, default: [shield, csv, json]}
  dir: {description: "The folder the files are written in ('' = a folder in work_dir, gone after the job).", type: str, default: ''}
  shield_subdir: {description: The shield files' folder under dir and under the share's folder., type: str, default: shield}
  share: {description: "A Windows file share's folder (\\\\server\\share\\folder) or a mounted folder the files are copied to ('' = none).", type: str, default: ''}
  share_subdir: {description: A folder under the share's folder for them ('' = the share's folder itself)., type: str, default: ''}
  share_refused: {description: "The share address had a user or password in it: refused (the task never hands it on).", type: bool, default: false}
  share_auth: {description: "negotiate, kerberos or ntlm.", type: str, default: negotiate}
  share_encrypt: {description: SMB 3 encryption always., type: bool, default: false}
  share_port: {description: SMB's port., type: int, default: 445}
  keep_days: {description: "The dated files kept, here and on the share (0 = all).", type: int, default: 90}
  xlsx_max_rows: {description: The workbook's Packages sheet at most (0 = all)., type: int, default: 20000}
  email_max_mb: {description: Each email attachment at most (MB)., type: float, default: 10}
  top: {description: The report's top changed packages., type: int, default: 15}
author: site automation
'''

RETURN = r'''
report: {description: "The report (roles/site_email's _report).", type: dict}
sheets_file: {description: "The workbook's sheets as JSON (roles/site_email's _report_xlsx); '' when it could not be made.", type: str}
attachments: {description: "[{path, filename, desc}] for the email (each within email_max_mb).", type: list}
web_files: {description: "[{path, filename, desc}] for the report website (the CSV and the JSON, whatever their size).", type: list}
outputs: {description: "[{name, state: ok|failed|skipped, where, error, fix, code}].", type: list}
failed_outputs: {description: The outputs that failed., type: list}
lines: {description: What happened, a line each (the job output)., type: list}
'''

import datetime
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
import zipfile

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils import site_sw_inventory as sw

STATE = "sw-inventory-state.json"
DATED = re.compile(r"^sw-inventory-(\d{8})-\d{4,6}(?:-job\d+)?\.(csv|json)$")
WAIT = (0.5, 1, 2, 3, 4)                # a file in use on the share: tried again after these seconds (as the report website's)


class Fail(Exception):
    """An output that failed: code (SA-SWI-..), what, fix."""

    def __init__(self, code, what, fix):
        Exception.__init__(self, "%s %s" % (code, what))
        self.code, self.what, self.fix = code, what, fix


def read_name(host):
    """The file a host's read is kept in: the SHA-1 of its inventory name - two names never meet (rack/node, rack_node)."""
    return hashlib.sha1(str(host).encode("utf-8")).hexdigest() + ".read"


def smb_in_use(e):
    """An SMB error that a moment later may not be there: the file open in a viewer, or being deleted."""
    low = str(e).lower()
    return any(w in low for w in ("sharing_violation", "sharing violation", "0xc0000043", "access_denied", "access is denied",
                                  "0xc0000022", "delete_pending", "0xc0000056", "being used by another"))


def again(what, retry, wait=None):
    """what(), done again after each pause while retry(the error) says it may work a moment later."""
    for pause in tuple(WAIT if wait is None else wait) + (None,):
        try:
            return what()
        except Exception as e:                  # noqa: BLE001
            if pause is None or not retry(e):
                raise
            time.sleep(pause)


# ---- the share: smbprotocol, as roles/site_email/library/site_report_web.py writes the report website's share ----------

def smb_path(dest):
    """\\\\server\\share\\folder, //server/share/folder or smb://server/share/folder -> (server, UNC), or None (not a share).
    A user or password in it (smb://user:pw@server/...) is refused and never repeated."""
    d = (dest or "").strip()
    m = re.match(r"(?i)^smb:/+(.+)$", d)
    if m:
        d = "//" + m.group(1)
    if not (d.startswith("\\\\") or d.startswith("//")):
        return None
    parts = [x for x in re.split(r"[\\/]+", d) if x]
    if parts and "@" in parts[0]:
        raise Fail("SA-SWI-20", "sw_inventory_share has a user or password in it (user@server) - not used, not shown",
                   "write only \\\\fileserver.yourdomain.mil\\share\\folder; the login comes from the Windows file share credential")
    if len(parts) < 2:
        raise Fail("SA-SWI-20", "sw_inventory_share is not \\\\server\\share\\folder",
                   "write it as \\\\fileserver.yourdomain.mil\\share\\folder")
    return parts[0], "\\\\" + "\\".join(parts)


def why_smb(what, e, auth, port, secret=""):
    """An SMB error as (code, what, fix) - the same hints as the report website's share; the password never in it."""
    text = str(e)
    if secret:
        text = text.replace(secret, "[redacted]")
    low = text.lower()
    line = "%s: %s" % (what, text.splitlines()[0][:300] if text else type(e).__name__)
    if "logon_failure" in low or "logon failure" in low or "status_logon" in low or "invalid credentials" in low:
        return "SA-SWI-21", line, ('the user name or password is wrong, or the account is locked: the "Windows file share" '
                                   'credential, user@YOURDOMAIN.MIL')
    if "access_denied" in low or "access is denied" in low:
        return "SA-SWI-22", line, "give the account Modify on the folder (the share and NTFS)"
    if "bad_network_name" in low or "0xc00000cc" in low:
        return "SA-SWI-23", line, "no such share on that server: the part after the server name in sw_inventory_share"
    if "default realm" in low:
        return "SA-SWI-24", line, "write the credential's user name as user@YOURDOMAIN.MIL (the realm in capitals)"
    if "server not found in kerberos database" in low:
        return "SA-SWI-24", line, "write the server by its full name in sw_inventory_share (\\\\fileserver.yourdomain.mil\\...)"
    if "invalid token was supplied" in low and auth == "kerberos":
        return "SA-SWI-24", line, "sw_inventory_share_auth: negotiate"
    if "md4" in low or "unsupported hash" in low:
        return "SA-SWI-24", line, ("NTLM needs MD4, which FIPS does not have: negotiate, user@YOURDOMAIN.MIL and the server's "
                                   "full name, so Kerberos is used")
    if "failed to connect" in low or "timed out" in low or "refused" in low or "name or service" in low:
        return "SA-SWI-25", line, "open TCP %d from the execution nodes to the file server (a firewall), or correct its name" % port
    return "SA-SWI-29", line, "see the error; docs/LINUX_SW_INVENTORY.md, When something fails"


class SmbFolder(object):
    """A folder on a Windows file share (smbprotocol; no mount). The login: SMB_SHARE_USERNAME / _PASSWORD (the
    "Windows file share" credential), else REPORT_WEB_USERNAME / _PASSWORD (the "Report website" credential)."""

    def __init__(self, dest, auth="negotiate", encrypt=False, port=445, timeout=30):
        self.server, self.root = smb_path(dest)
        self.auth, self.port = auth, port
        try:
            import smbclient
        except ImportError:
            raise Fail("SA-SWI-26", "this execution environment has no smbprotocol",
                       "use the all-in-one EE of ee/ (it has smbprotocol), or a mounted folder as sw_inventory_share")
        self.smb, self.kw = smbclient, {"port": port}
        user = os.environ.get("SMB_SHARE_USERNAME") or os.environ.get("REPORT_WEB_USERNAME") or ""
        self.secret = os.environ.get("SMB_SHARE_PASSWORD") or os.environ.get("REPORT_WEB_PASSWORD") or ""
        if auth not in ("negotiate", "ntlm", "kerberos"):
            raise Fail("SA-SWI-20", "sw_inventory_share_auth is %s" % auth, "negotiate, kerberos or ntlm")
        if not user and auth != "kerberos":
            raise Fail("SA-SWI-27", "no login for the share %s" % self.root,
                       'attach the "Windows file share" credential (user@YOURDOMAIN.MIL and its password) to the template')
        try:
            smbclient.register_session(self.server, username=user or None, password=self.secret or None, port=port,
                                       encrypt=encrypt or None, auth_protocol=auth, connection_timeout=timeout)
        except Exception as e:                  # noqa: BLE001
            raise Fail(*why_smb("logging in to %s" % self.server, e, auth, port, self.secret))
        try:
            ok = smbclient.path.isdir(self.root, **self.kw)
        except Exception as e:                  # noqa: BLE001
            raise Fail(*why_smb("opening %s" % self.root, e, auth, port, self.secret))
        if not ok:
            raise Fail("SA-SWI-28", "the folder %s is not there" % self.root, "make it once by hand (the job never makes it)")

    def path(self, rel):
        return self.root + ("\\" + rel.replace("/", "\\") if rel else "")

    def read(self, rel):
        """The file, None when it is not there; any other error raises (a baseline never taken for missing)."""
        try:
            with self.smb.open_file(self.path(rel), mode="rb", **self.kw) as f:
                return f.read()
        except Exception as e:                  # noqa: BLE001
            low = str(e).lower()
            if getattr(e, "errno", None) == 2 or "object_name_not_found" in low or "0xc0000034" in low or "no such file" in low:
                return None
            raise Fail(*why_smb("reading %s" % self.path(rel), e, self.auth, self.port, self.secret))

    def write(self, rel, body):
        p = self.path(rel)
        folder = p.rsplit("\\", 1)[0]
        tmp = "%s\\.%s.%s.tmp" % (folder, p.rsplit("\\", 1)[1], uuid.uuid4().hex)   # never another run's
        try:
            if folder != self.root:
                self.smb.makedirs(folder, exist_ok=True, **self.kw)
            with self.smb.open_file(tmp, mode="xb", **self.kw) as f:      # made new, never an existing file
                f.write(body)
            again(lambda: self.smb.replace(tmp, p, **self.kw), smb_in_use)  # replaced whole; a file open just then: again
        except Exception as e:                  # noqa: BLE001
            try:
                self.smb.remove(tmp, **self.kw)
            except Exception:                   # noqa: BLE001
                pass
            raise Fail(*why_smb("writing %s" % p, e, self.auth, self.port, self.secret))

    def listdir(self, rel):
        """The names, or None when the folder cannot be listed (said as a retention warning)."""
        try:
            return list(self.smb.listdir(self.path(rel), **self.kw))
        except Exception:                       # noqa: BLE001
            return None

    def remove(self, rel):
        """True when it is gone; False when it stays (the next run tries again)."""
        try:
            again(lambda: self.smb.remove(self.path(rel), **self.kw), smb_in_use)
            return True
        except Exception:                       # noqa: BLE001
            return False

    def close(self):
        try:
            self.smb.reset_connection_cache()
        except Exception:                       # noqa: BLE001
            pass


class LocalFolder(object):
    """A folder here: the files' dir (made), or a share mounted on the execution node (must be there, Check too)."""

    def __init__(self, root, make=True, check=False, what="sw_inventory_dir"):
        self.root = root
        if not os.path.isabs(root):             # the value never repeated (a share address could carry a login)
            raise Fail("SA-SWI-30", "%s is neither an absolute path nor \\\\server\\share\\folder" % what,
                       "give the full path (/var/lib/...), or the share as \\\\fileserver.yourdomain.mil\\share\\folder")
        if not make:
            if not os.path.isdir(root):
                raise Fail("SA-SWI-28", "the folder %s is not there" % root, "make it once by hand, or mount the share")
        elif not check:
            try:
                os.makedirs(root, exist_ok=True)
            except OSError as e:
                raise Fail("SA-SWI-31", "cannot make %s: %s" % (root, e.strerror or e),
                           "a folder the job's user may write (in the EE: a path AAP exposes to jobs), or sw_inventory_share")

    def path(self, rel):
        return os.path.join(self.root, *rel.split("/")) if rel else self.root

    def inside(self, p):
        """p's folder, links resolved, is the root's or under it: a linked sub folder (shield -> /etc) is refused."""
        base = os.path.realpath(self.root)
        real = os.path.realpath(os.path.dirname(p))
        if real != base and not real.startswith(base + os.sep):
            raise Fail("SA-SWI-34", "%s leads outside %s (a link) - nothing written or deleted there" % (os.path.dirname(p), self.root),
                       "remove the link, or make it a real folder")
        if os.path.islink(p):
            raise Fail("SA-SWI-34", "%s is a link - not written over, not deleted" % p, "remove the link")

    def read(self, rel):
        """The file, None when it is not there; any other error raises (a baseline never taken for missing)."""
        try:
            with open(self.path(rel), "rb") as f:
                return f.read()
        except (IOError, OSError) as e:
            if getattr(e, "errno", None) == 2:
                return None
            raise Fail("SA-SWI-32", "cannot read %s: %s" % (self.path(rel), e.strerror or e),
                       "give the job's user read access to it (it is the last list of each host)")

    def write(self, rel, body):
        p = self.path(rel)
        tmp = None
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            self.inside(p)
            fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(p) + ".", suffix=".tmp", dir=os.path.dirname(p))
            with os.fdopen(fd, "wb") as f:      # made new (O_EXCL): never through a link someone put there
                f.write(body)
            os.chmod(tmp, 0o644)
            os.replace(tmp, p)
        except (IOError, OSError) as e:
            try:
                if tmp and os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            raise Fail("SA-SWI-31", "cannot write %s: %s" % (p, e.strerror or e),
                       "give the job's user write access there, or another sw_inventory_dir")

    def listdir(self, rel):
        try:
            return os.listdir(self.path(rel))
        except OSError as e:
            return [] if getattr(e, "errno", None) == 2 else None

    def remove(self, rel):
        try:
            self.inside(self.path(rel))
            os.remove(self.path(rel))
            return True
        except (OSError, Fail):
            return False

    def close(self):
        pass


def join(*parts):
    return "/".join(p.strip("/\\").replace("\\", "/") for p in parts if p and p.strip("/\\"))


def safe_sub(name, what):
    """A sub folder: plain names only (letters, digits . _ -), '/' between them, no '.' or '..' part."""
    n = (name or "").strip().strip("/\\")
    if not n:
        return ""
    parts = re.split(r"[\\/]+", n)
    if any(x in (".", "..") or not re.match(r"^[A-Za-z0-9._-]+\Z", x) for x in parts):
        raise Fail("SA-SWI-30", "%s %r is not a plain folder name" % (what, name),
                   "letters, digits . _ - only (e.g. shield, or linux/inventory)")
    return "/".join(parts)


def prune_dated(target, base, keep_days, now):
    """This job's dated files older than keep_days, deleted -> (deleted, left); left is ['(the folder could not be
    listed)'] when it could not be read."""
    gone, left = 0, []
    if keep_days <= 0:
        return gone, left
    cut = (now - datetime.timedelta(days=keep_days)).strftime("%Y%m%d")
    names = target.listdir(base)
    if names is None:
        return gone, ["(the folder could not be listed: old dated files not deleted)"]
    for name in names:
        m = DATED.match(name)
        if m and m.group(1) < cut:
            if target.remove(join(base, name)):
                gone += 1
            else:
                left.append(name)
    return gone, left


# ---- the run -------------------------------------------------------------------------------------------------------

def read_hosts(work_dir, hosts):
    """(read: [{host, os, kernel, read_at, packages, repo_note}], not_read: [{host, reason}])."""
    ok, bad = [], []
    for h in hosts:
        name = str(h.get("host") or "")
        if not name or h.get("skipped"):
            continue
        if h.get("reason"):
            bad.append({"host": name, "reason": str(h["reason"])[:400]})
            continue
        try:
            with open(os.path.join(work_dir, read_name(name)), "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except (IOError, OSError):
            bad.append({"host": name, "reason": "no read came back from the host (the step before failed: see its output)"})
            continue
        r = sw.parse_read(text)
        if not r["ok"]:
            bad.append({"host": name, "reason": r["error"]})
            continue
        ok.append({"host": name, "os": r["os"], "kernel": r["kernel"], "read_at": r["read_at"], "packages": r["packages"],
                   "repo_note": r["repo_note"]})
    return ok, bad


def email_attachments(files, max_mb):
    """[{path, filename, desc}] for the email, and notes: each file (the CSV, the JSON, the shield zip) attached when it
    is not bigger than max_mb; one that is, left off - the email says where it is (its 'elsewhere')."""
    att, notes, cap = [], [], float(max_mb) * 1024 * 1024
    for f in files:
        size = os.path.getsize(f["path"]) if os.path.isfile(f["path"]) else -1
        if size < 0:
            continue
        if size > cap:
            notes.append("%s (%.1f MB) is not attached: bigger than sw_inventory_email_max_mb (%s) - %s"
                         % (f["filename"], size / 1048576.0, max_mb, f["elsewhere"]))
            continue
        att.append({"path": f["path"], "filename": f["filename"], "desc": f.get("desc", "")})
    return att, notes


def run(p, check=False, warn=lambda m: None):
    now = sw.stamp(p["read_at"]) or datetime.datetime.utcnow().replace(microsecond=0)
    outputs = [o for o in (p["outputs"] or []) if o]
    res = {"outputs": [], "lines": [], "read_at": p["read_at"], "attachments": [], "web_files": [], "sheets_file": "", "report": {}}

    def said(name, state, **kw):
        x = dict({"name": name, "state": state, "where": "", "error": "", "fix": "", "code": "", "why": ""}, **kw)
        res["outputs"].append(x)
        if state == "failed":
            res["lines"].append("%s NOT WRITTEN: %s %s - fix: %s" % (name, x["code"], x["error"], x["fix"]))
            warn("software inventory, %s not written: %s %s - fix: %s" % (name, x["code"], x["error"], x["fix"]))
        elif state == "ok":
            res["lines"].append("%s: %s" % (name, x["where"]))
        elif x["why"]:
            res["lines"].append("%s: %s" % (name, x["why"]))
        return x

    for o in [o for o in outputs if o not in sw.OUTPUTS]:
        said(o, "failed", code="SA-SWI-01", error="no output named %s" % o, fix="sw_inventory_outputs: any of " + ", ".join(sw.OUTPUTS))
    ok, bad = read_hosts(p["work_dir"], p["hosts"])
    want = [o for o in ("shield", "csv", "json") if o in outputs]

    # ---- where the files go: dir (here), and the share ----
    shield_sub, share_sub, local, share = None, "", None, None
    if "shield" in want:
        try:
            shield_sub = safe_sub(p["shield_subdir"], "sw_inventory_shield_subdir") or "shield"
        except Fail as e:                       # the shield files only: the CSV and the JSON still go
            said("shield", "failed", code=e.code, error=e.what, fix=e.fix)
            want.remove("shield")
    file_outs = list(want)
    if want:
        try:
            local = LocalFolder(p["dir"] or os.path.join(p["work_dir"], "files"), make=True, check=check)
        except Fail as e:
            for o in want:
                said(o, "failed", code=e.code, error=e.what, fix=e.fix)
            file_outs = []
    if p.get("share_refused") and want:
        said("share", "failed", code="SA-SWI-20", error="sw_inventory_share has a user or password in it (user@server) - not used, not shown",
             fix="write only \\\\fileserver.yourdomain.mil\\share\\folder; the login comes from the Windows file share credential")
    if p["share"] and want:
        try:
            share_sub = safe_sub(p["share_subdir"], "sw_inventory_share_subdir")
            if smb_path(p["share"]):
                share = SmbFolder(p["share"], p["share_auth"], p["share_encrypt"], p["share_port"])
            else:
                share = LocalFolder(p["share"], make=False, check=check, what="sw_inventory_share")
        except Fail as e:
            said("share", "failed", code=e.code, error=e.what, fix=e.fix)
            share = None

    # ---- the baseline: sw-inventory-state.json (dir, else the share) ----
    state_doc, base_from, baseline, damaged, state_ok = None, "", {}, [], True
    if want:
        raw, src = None, "sw-inventory-state.json in %s" % (p["dir"] or "the job's folder")
        try:
            raw = local.read(STATE) if local else None
            if raw is None and share is not None:
                raw, src = share.read(join(share_sub, STATE)), "sw-inventory-state.json on the share"
        except Fail as e:                       # not "missing": never written over, said
            state_ok = False
            said("baseline", "failed", code=e.code, error=e.what + " - no changes this run, the file is left as it is", fix=e.fix)
        if raw is not None:
            try:
                state_doc = json.loads(raw.decode("utf-8"))
                baseline, damaged = sw.state_hosts(state_doc)
                base_from = src
            except (ValueError, AttributeError, UnicodeDecodeError):
                state_doc, damaged = None, ["(the whole file)"]
    chg, first = {}, []
    for h in ok:
        if h["host"] in baseline:
            chg[h["host"]] = sw.changes(baseline[h["host"]], h["packages"])
        else:
            first.append(h["host"])

    # ---- the files ----
    stamp = sw.stamp_name(p["read_at"], p["job"])
    files = {"shield": [], "csv": [], "json": [], "state": []}
    if "shield" in want:
        names = {}
        for h in ok:
            n = sw.safe_file(sw.short_name(h["host"]))
            if n.lower() in names:              # APP01 and app01 are one file on a Windows share
                res["lines"].append("shield: %s and %s have the same short name %s - only %s's file is written"
                                    % (names[n.lower()], h["host"], n, names[n.lower()]))
                continue
            names[n.lower()] = h["host"]
            files["shield"].append({"rel": join(shield_sub, n + ".txt"), "body": sw.shield_text(sw.short_name(h["host"]), h).encode("utf-8")})
    if "csv" in want:
        body = sw.csv_text(ok).encode("utf-8")
        files["csv"] = [{"rel": "sw-inventory-%s.csv" % stamp, "body": body}, {"rel": "sw-inventory-latest.csv", "body": body}]
    if "json" in want:
        body = (json.dumps(sw.json_doc({"read_at": p["read_at"], "job": p["job"]}, ok, bad,
                                       [dict(c, host=h) for h in sorted(chg) for c in chg[h]]), indent=1) + "\n").encode("utf-8")
        files["json"] = [{"rel": "sw-inventory-%s.json" % stamp, "body": body}, {"rel": "sw-inventory-latest.json", "body": body}]
    if want and ok and state_ok:                # every host's last list: a host not read this run keeps its own
        files["state"] = [{"rel": STATE, "body": json.dumps(sw.state_merge(state_doc, ok), separators=(",", ":")).encode("utf-8")}]
    kept_here = bool(p["dir"])
    done_here, done_share = set(), set()       # the files really written, here and on the share (the email says where)
    for o in want:
        if o not in file_outs:
            continue
        if not files[o]:
            said(o, "skipped", why="no host read: nothing to write")
            continue
        if check:
            said(o, "ok", where="(check mode: %d files to %s)" % (len(files[o]), local.root))
            continue
        failed = []
        for f in files[o]:                      # each file on its own: one that fails never stops the next
            try:
                local.write(f["rel"], f["body"])
                done_here.add(f["rel"])
            except Fail as e:
                failed.append(e)
        gone, left = prune_dated(local, "", p["keep_days"], now) if o in ("csv", "json") else (0, [])
        if left:
            res["lines"].append("%s: %d old dated files could not be deleted in %s: %s" % (o, len(left), local.root, ", ".join(left[:5])))
        if failed:
            for e in failed:
                res["lines"].append("%s: not written - %s %s" % (o, e.code, e.what))
            more = " and %d more (the job output lists them all)" % (len(failed) - 50) if len(failed) > 50 else ""
            said(o, "failed", code=failed[0].code, fix=failed[0].fix,
                 error="%d of %d files not written - %s%s" % (len(failed), len(files[o]), "; ".join(e.what for e in failed[:50]), more))
        else:
            said(o, "ok", where="%d file%s in %s%s%s" % (
                len(files[o]), "" if len(files[o]) == 1 else "s", local.root,
                "" if kept_here else " (the job's own folder: gone after the job)",
                "; %d old dated files deleted" % gone if gone else ""))
    if files["state"] and local is not None and file_outs and not check:
        try:
            local.write(STATE, files["state"][0]["body"])
        except Fail as e:
            said("baseline", "failed", code="SA-SWI-33", error=e.what + " - the next run compares with the older list",
                 fix=e.fix)
    if not kept_here and want and not p["share"]:
        res["lines"].append("The files are only in the job's own folder (gone after the job): set sw_inventory_share or sw_inventory_dir.")

    # ---- the share: each file on its own ----
    share_ok = False
    if share is not None:
        todo = [f for o in want for f in files[o]] + files["state"]
        where = share.path(share_sub) if share_sub else share.root
        if check:
            said("share", "ok", where="(check mode: logged in, %s is there; %d files to copy)" % (share.root, len(todo)))
        elif todo:
            failed = []
            for f in todo:
                try:
                    share.write(join(share_sub, f["rel"]), f["body"])
                    done_share.add(f["rel"])
                except Fail as e:
                    failed.append(e)
            gone, left = prune_dated(share, share_sub, p["keep_days"], now)
            if left:
                res["lines"].append("share: %d old dated files could not be deleted: %s" % (len(left), ", ".join(left[:5])))
            if failed:
                for e in failed:                # every one in the job output
                    res["lines"].append("share: not copied - %s %s" % (e.code, e.what))
                more = " and %d more (the job output lists them all)" % (len(failed) - 50) if len(failed) > 50 else ""
                said("share", "failed", code=failed[0].code,
                     error="%d of %d files not copied to %s - %s%s" % (len(failed), len(todo), where,
                                                                      "; ".join(e.what for e in failed[:50]), more), fix=failed[0].fix)
            else:
                share_ok = True
                said("share", "ok", where="%d files in %s%s" % (len(todo), where, ("; %d old dated files deleted" % gone) if gone else ""))
        share.close()

    # ---- the report, the workbook, the email's and the website's files ----
    res["not_read"], res["first_seen"] = bad, first
    view = {"hosts": [{"host": h["host"], "packages": len(h["packages"])} for h in ok], "not_read": bad,
            "outputs": res["outputs"], "changes_by_host": chg, "first_seen": first, "read_at": p["read_at"],
            "repo_notes": [h["host"] for h in ok if h["repo_note"]], "baseline": base_from, "state_damaged": damaged}
    res["report"] = sw.report_doc(view, {"top": p["top"]})
    notes = []
    work = p["work_dir"]
    try:                                        # the workbook's sheets: written whole, or none (never a half file)
        sheets = sw.xlsx_sheets({"pkg_rows": list(sw.csv_rows(ok)), "changes_by_host": chg}, p["xlsx_max_rows"])
        LocalFolder(work).write("sheets.json", json.dumps(sheets).encode("utf-8"))
        res["sheets_file"] = os.path.join(work, "sheets.json")
    except (Fail, IOError, OSError, ValueError) as e:
        notes.append("The workbook could not be made (%s): the report goes without it." % getattr(e, "what", e))
    share_where = (share.path(share_sub) if share_sub else share.root) if share is not None else ""

    def where_is(rels, it="it is", were="it was"):
        """Where these files really are, from the writes that worked: the share, else sw_inventory_dir, else nowhere."""
        if rels and share is not None and all(r in done_share for r in rels):
            return "%s on the share, %s" % (it, share_where)
        if rels and kept_here and not check and all(r in done_here for r in rels):
            return "%s in %s on the execution node" % (it, p["dir"])
        if share is None and not p["share"] and not kept_here:
            return "%s not kept anywhere else (set sw_inventory_share)" % were
        return "%s not kept anywhere else (its copy failed: see the outputs not written)" % were
    mail = []
    for kind, desc in (("csv", "CSV - every package of every host read"),
                       ("json", "JSON - every host's packages, the hosts not read, what changed")):
        if not files[kind]:
            continue
        try:
            LocalFolder(work).write(files[kind][0]["rel"], files[kind][0]["body"])
        except Fail as e:
            notes.append("The %s could not be attached (%s)." % (kind.upper(), e.what))
            continue
        where_k = where_is([f["rel"] for f in files[kind]][:1])
        mail.append({"path": os.path.join(work, files[kind][0]["rel"]), "filename": files[kind][0]["rel"], "desc": desc,
                     "elsewhere": where_k})
    res["web_files"] = [dict((k, m[k]) for k in ("path", "filename", "desc")) for m in mail]
    if files["shield"]:
        zp = os.path.join(work, "sw-inventory-shield-%s.zip" % stamp[:8])
        try:
            with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
                for f in files["shield"]:
                    z.writestr(f["rel"].rsplit("/", 1)[-1], f["body"])
            mail.append({"path": zp, "filename": os.path.basename(zp), "desc": "Zip - the shield text file of each host read",
                         "elsewhere": where_is([f["rel"] for f in files["shield"]], "the shield files are", "they were")})
        except (IOError, OSError) as e:
            notes.append("The shield zip could not be made (%s)." % (e.strerror or e))
    att, more = email_attachments(mail, p["email_max_mb"])
    res["attachments"], notes = att, more + notes
    if notes:
        for s in res["report"]["sections"]:
            if s.get("title") == "Notes":
                s["lines"] = notes + s["lines"]
                break
        else:
            res["report"]["sections"].append({"title": "Notes", "lines": notes})
    res["failed_outputs"] = [x["name"] for x in res["outputs"] if x["state"] == "failed"]
    res["hosts_read"], res["hosts_not_read"] = len(ok), len(bad)
    res["packages"] = sum(len(h["packages"]) for h in ok)
    res["hosts_changed"] = len([h for h in chg if chg[h]])
    return res


def main():
    module = AnsibleModule(
        argument_spec=dict(work_dir=dict(type="str", required=True), hosts=dict(type="list", elements="dict", required=True),
                           read_at=dict(type="str", required=True), job=dict(type="int", default=0),
                           outputs=dict(type="list", elements="str", default=["shield", "csv", "json"]),
                           dir=dict(type="str", default=""), shield_subdir=dict(type="str", default="shield"),
                           share=dict(type="str", default=""), share_subdir=dict(type="str", default=""),
                           share_refused=dict(type="bool", default=False),
                           share_auth=dict(type="str", default="negotiate"), share_encrypt=dict(type="bool", default=False),
                           share_port=dict(type="int", default=445), keep_days=dict(type="int", default=90),
                           xlsx_max_rows=dict(type="int", default=20000), email_max_mb=dict(type="float", default=10),
                           top=dict(type="int", default=15)),
        supports_check_mode=True)
    res = run(module.params, module.check_mode, module.warn)
    res["changed"] = not module.check_mode and any(x["state"] == "ok" for x in res["outputs"])
    module.exit_json(**res)


if __name__ == "__main__":
    main()
