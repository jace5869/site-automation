#!/usr/bin/python
# -*- coding: utf-8 -*-
"""1.132.0: the Linux software inventory's controller step (playbooks/linux_sw_inventory.yml, docs/LINUX_SW_INVENTORY.md):
the hosts' reads (roles/sw_inventory/files/sw_read.sh, one file per host in work_dir) parsed here, compared with each
host's last list (sw-inventory-state.json) and written - every export on its own: one that fails says which, why (an
SA-SWI code) and the fix, and the others are still written.

  shield     a text file per host (#shield-host v1): <dir>/<shield_subdir>/<short name>.txt
  csv, json  sw-inventory-YYYYMMDD-HHMM.csv / .json and sw-inventory-latest.csv / .json in dir
  share      every file above also on a Windows file share over SMB (smbprotocol; the "Windows file share" credential),
             or a folder mounted on the execution node; each file on its own
  shares     1.135.2: more destinations (sw_inventory_shares), each on its own - one that fails (login, folder, space)
             never stops the next; sw_inventory_share is the first when set; the same folder twice is copied once

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
  hosts: {description: "[{host, reason, skipped}] - every host of the play.", type: list, elements: dict, default: []}
  read_at: {description: "When this run started (UTC); needed unless result_from.", type: str, default: ''}
  job: {description: This job's ID., type: int, default: 0}
  result_from:
    description: "1.135.2: true = return the first call's result from <work_dir>/result.json and delete it (that call
      runs with no_log: its arguments hold the share passwords; ansible-core 2.16 would mask them inside its result)."
    type: bool
    default: false

  outputs: {description: "Any of shield, csv, json.", type: list, elements: str, default: [shield, csv, json]}
  dir: {description: "The folder the files are written in ('' = a folder in work_dir, gone after the job).", type: str, default: ''}
  shield_subdir: {description: The shield files' folder under dir and under the share's folder., type: str, default: shield}
  share: {description: "A Windows file share's folder (\\\\server\\share\\folder) or a mounted folder the files are copied to ('' = none).", type: str, default: ''}
  share_subdir: {description: A folder under the share's folder for them ('' = the share's folder itself)., type: str, default: ''}
  share_refused: {description: "The share address had a user or password in it: refused (the task never hands it on).", type: bool, default: false}
  share_auth: {description: "negotiate, kerberos or ntlm.", type: str, default: negotiate}
  share_encrypt: {description: SMB 3 encryption always., type: bool, default: false}
  share_port: {description: SMB's port., type: int, default: 445}
  shares:
    description:
      - "1.135.2: more destinations, after share: [{path, subdir, user, password, auth, encrypt, refused}]. user / password
        empty = the Windows file share credential's (SMB_SHARE_USERNAME / _PASSWORD); auth / encrypt empty = share_auth /
        share_encrypt. The same path and subdir as one before it is left out (copied once). The task calls this module
        with no_log (ansible-core 2.16 logs a module's arguments)."
    type: list
    elements: dict
    default: []
  shares_error:
    description: "The task could not read sw_inventory_shares: 'not a list', or any other text = could not be read."
    type: str
    default: ''
  keep_days: {description: "The dated files kept, here and on the share (0 = all).", type: int, default: 90}
  xlsx_max_rows: {description: The workbook's Packages sheet at most (0 = all)., type: int, default: 20000}
  email_max_mb: {description: Each email attachment at most (MB)., type: float, default: 10}
  top: {description: The report's top changed packages., type: int, default: 15}
author: site automation
'''

RETURN = r'''
# 1.135.2: the first call (no_log) returns only changed; everything below comes from the second call (result_from)
report: {description: "The report (roles/site_email's _report).", type: dict}
sheets_file: {description: "The workbook's sheets as JSON (roles/site_email's _report_xlsx); '' when it could not be made.", type: str}
attachments: {description: "[{path, filename, desc}] for the email (each within email_max_mb).", type: list}
web_files: {description: "[{path, filename, desc}] for the report website (the CSV and the JSON, whatever their size).", type: list}
outputs: {description: "[{name, state: ok|failed|skipped, where, error, fix, code}] - one share each with more than one
  (share 1, share 2, ...).", type: list}
failed_outputs: {description: The outputs that failed., type: list}
lines: {description: What happened, a line each (the job output)., type: list}
'''

import datetime
import errno
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

BAD_SMB = re.compile(r'[<>:"|?*\x00-\x1f]')      # not in a Windows share or folder name (a URL's ?query never taken)


def smb_path(dest):
    """\\\\server\\share\\folder, //server/share/folder or smb://server/share/folder -> (server, UNC), or None (not a share).
    A user or password in it (smb://user:pw@server/...) is refused and never repeated."""
    d = (dest or "").strip()
    m = re.match(r"(?i)^smb:/+(.+)$", d)
    if m:
        if "#" in m.group(1) or "?" in m.group(1):      # a URL's fragment or query: never a folder name
            raise Fail("SA-SWI-20", "the share address has a URL part (# or ?) in it (not shown)",
                       "write only \\\\fileserver.yourdomain.mil\\share\\folder")
        d = "//" + m.group(1)
    if not (d.startswith("\\\\") or d.startswith("//")):
        return None
    parts = [x for x in re.split(r"[\\/]+", d) if x]
    if parts and "@" in parts[0]:
        raise Fail("SA-SWI-20", "the share address has a user or password in it (user@server) - not used, not shown",
                   "write only \\\\fileserver.yourdomain.mil\\share\\folder; the login comes from the Windows file share credential")
    if len(parts) < 2:
        raise Fail("SA-SWI-20", "the share address is not \\\\server\\share\\folder (not shown)",
                   "write it as \\\\fileserver.yourdomain.mil\\share\\folder")
    if not re.match(r"^[A-Za-z0-9._-]+\Z", parts[0]) or any(BAD_SMB.search(x) or x in (".", "..") for x in parts[1:]):
        raise Fail("SA-SWI-20", "the share address has a character a Windows share or folder name cannot have (not shown)",
                   "write only \\\\fileserver.yourdomain.mil\\share\\folder: no ? : * \" < > | in it")
    return parts[0], "\\\\" + "\\".join(parts)


NO_SPACE = "free space on the share's disk, or raise the account's quota there"
FULL_STATUS = (0xC000007F, 0xC0000044, 0xC0000802)    # disk full, quota exceeded, disk quota exceeded
STATUS_CODES = dict([(x, ("SA-SWI-35", NO_SPACE)) for x in FULL_STATUS] + [
    (0xC000006D, ("SA-SWI-21", 'the user name or password is wrong, or the account is locked: the "Windows file share" '
                               'credential, user@YOURDOMAIN.MIL')),
    (0xC0000022, ("SA-SWI-22", "give the account Modify on the folder (the share and NTFS)"))])


def why_smb(what, e, auth, port, secret=""):
    """An SMB error as (code, what, fix) - the same hints as the report website's share; the password never in it."""
    text = str(e)
    if secret:
        text = text.replace(secret, "[redacted]")
    low = re.sub(r"'[^']*'|\"[^\"]*\"|\\\\\S+", " ", text).lower()   # the words only: a path never decides the code
    line = "%s: %s" % (what, text.splitlines()[0][:300] if text else type(e).__name__)
    status = getattr(e, "ntstatus", None) or getattr(e, "status", None)
    status = status if isinstance(status, int) else None
    if status in STATUS_CODES:                  # the server's own status first
        return STATUS_CODES[status][0], line, STATUS_CODES[status][1]
    if getattr(e, "errno", None) in (errno.ENOSPC, errno.EDQUOT):
        return "SA-SWI-35", line, NO_SPACE
    if "logon_failure" in low or "logon failure" in low or "status_logon" in low or "invalid credentials" in low:
        return "SA-SWI-21", line, ('the user name or password is wrong, or the account is locked: the "Windows file share" '
                                   'credential, user@YOURDOMAIN.MIL')
    if "access_denied" in low or "access is denied" in low:
        return "SA-SWI-22", line, "give the account Modify on the folder (the share and NTFS)"
    if "bad_network_name" in low or "0xc00000cc" in low:
        return "SA-SWI-23", line, ("no such share on that server: the part after the server name in sw_inventory_share "
                                   "(or that sw_inventory_shares entry's path)")
    if "default realm" in low:
        return "SA-SWI-24", line, "write the credential's user name as user@YOURDOMAIN.MIL (the realm in capitals)"
    if "server not found in kerberos database" in low:
        return "SA-SWI-24", line, ("write the server by its full name in sw_inventory_share / sw_inventory_shares "
                                   "(\\\\fileserver.yourdomain.mil\\...)")
    if "invalid token was supplied" in low and auth == "kerberos":
        return "SA-SWI-24", line, "sw_inventory_share_auth: negotiate"
    if "md4" in low or "unsupported hash" in low:
        return "SA-SWI-24", line, ("NTLM needs MD4, which FIPS does not have: negotiate, user@YOURDOMAIN.MIL and the server's "
                                   "full name, so Kerberos is used")
    if getattr(e, "ntstatus", None) in FULL_STATUS or getattr(e, "errno", None) in (errno.ENOSPC, errno.EDQUOT) or any(
            w in low for w in ("0xc000007f", "0xc0000044", "0xc0000802", "status_disk_full", "status_quota_exceeded",
                               "status_disk_quota_exceeded", "no space left on device", "not enough space on the disk")):
        return "SA-SWI-35", line, NO_SPACE
    if "failed to connect" in low or "timed out" in low or "refused" in low or "name or service" in low:
        return "SA-SWI-25", line, "open TCP %d from the execution nodes to the file server (a firewall), or correct its name" % port
    return "SA-SWI-29", line, "see the error; docs/LINUX_SW_INVENTORY.md, When something fails"


class SmbFolder(object):
    """A folder on a Windows file share (smbprotocol; no mount). The login: user / password (a sw_inventory_shares
    entry's own), each empty = SMB_SHARE_USERNAME / _PASSWORD (the "Windows file share" credential), else
    REPORT_WEB_USERNAME / _PASSWORD (the "Report website" credential). Each folder its own connections (1.135.2): two
    destinations on one server with two logins never share a session."""

    def __init__(self, dest, auth="negotiate", encrypt=False, port=445, timeout=30, user="", password=""):
        self.server, self.root = smb_path(dest)
        self.auth, self.port = auth, port
        try:
            import smbclient
        except ImportError:
            raise Fail("SA-SWI-26", "this execution environment has no smbprotocol",
                       "use the all-in-one EE of ee/ (it has smbprotocol), or a mounted folder as sw_inventory_share")
        self.cache = {}
        self.smb = smbclient
        user = user or os.environ.get("SMB_SHARE_USERNAME") or os.environ.get("REPORT_WEB_USERNAME") or ""
        self.secret = password or os.environ.get("SMB_SHARE_PASSWORD") or os.environ.get("REPORT_WEB_PASSWORD") or ""
        if auth not in ("negotiate", "ntlm", "kerberos"):
            raise Fail("SA-SWI-20", "the share's auth is not negotiate, kerberos or ntlm",
                       "sw_inventory_share_auth (or the entry's auth): negotiate, kerberos or ntlm")
        if not user and auth != "kerberos":
            raise Fail("SA-SWI-27", "no login for the share %s" % self.root,
                       'attach the "Windows file share" credential (user@YOURDOMAIN.MIL and its password) to the template')
        # every call with this destination's own login and cache: a reconnect or a DFS referral keeps them (1.135.2)
        self.kw = {"port": port, "connection_cache": self.cache, "username": user or None, "password": self.secret or None,
                   "encrypt": encrypt or None, "auth_protocol": auth, "connection_timeout": timeout}
        try:
            try:
                smbclient.register_session(self.server, **self.kw)
            except Exception as e:              # noqa: BLE001
                raise Fail(*why_smb("logging in to %s" % self.server, e, auth, port, self.secret))
            try:
                ok = smbclient.path.isdir(self.root, **self.kw)
            except Exception as e:              # noqa: BLE001
                raise Fail(*why_smb("opening %s" % self.root, e, auth, port, self.secret))
            if not ok:
                raise Fail("SA-SWI-28", "the folder %s is not there" % self.root, "make it once by hand (the job never makes it)")
        except Fail:
            self.close()                        # this destination's connections never left open
            raise

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
        if getattr(self, "closed", False):
            return
        self.closed = True
        try:
            self.smb.reset_connection_cache(connection_cache=self.cache)
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
            if os.path.realpath(root) == os.sep:
                raise Fail("SA-SWI-30", "%s is the root folder /" % what, "give the share's own folder (/mnt/inventory)")
            if not os.path.isdir(root):
                raise Fail("SA-SWI-28", "the folder %s is not there" % root, "make it once by hand, or mount the share")
        elif not check:
            try:
                os.makedirs(root, exist_ok=True)
            except OSError as e:
                if getattr(e, "errno", None) in (errno.ENOSPC, errno.EDQUOT):
                    raise Fail("SA-SWI-35", "cannot make %s: %s" % (root, e.strerror or e), NO_SPACE.replace("share's", "folder's"))
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
            if getattr(e, "errno", None) in (errno.ENOSPC, errno.EDQUOT):
                raise Fail("SA-SWI-35", "cannot write %s: %s" % (p, e.strerror or e), NO_SPACE.replace("share's", "folder's"))
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
        raise Fail("SA-SWI-30", "%s is not a plain folder name (not shown)" % what,
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


REFUSED = ("a user or password in the address (user@server) - not used, not shown",
           "write only \\\\fileserver.yourdomain.mil\\share\\folder; the login comes from the Windows file share credential "
           "(or the entry's user and password)")


def dest_key(path, subdir):
    """One destination's identity: \\\\FS01\\it\\inv\\, //fs01/it/inv and smb://fs01/it/inv are one (Windows names have
    no case); a mounted folder keeps its case."""
    p = path or ""
    m = re.match(r"(?i)^smb:/+(.+)$", p.strip())
    if m:
        p = "//" + m.group(1)
    sub = "/".join(x for x in re.split(r"[\\/]+", (subdir or "").strip()) if x)
    if p.strip().startswith("\\\\") or p.strip().startswith("//"):
        p = p.strip()
        return ("smb", "/".join(x for x in re.split(r"[\\/]+", p) if x).lower(), sub.lower())
    return ("dir", re.sub(r"/+", "/", p).rstrip("/") or p, sub)   # never normpath: /mnt/gone/../inv may not be /mnt/inv


TRUE, FALSE = ("true", "yes", "on", "1"), ("false", "no", "off", "0")


def destinations(p):
    """Every share destination, in order: sw_inventory_share (share, share_subdir, ...) first when set, then each
    sw_inventory_shares entry -> ([{name, path, subdir, user, password, auth, encrypt, refused, bad, legacy, what,
    show, n}], lines). An entry's empty auth / encrypt = share_auth / share_encrypt; an encrypt that is not true or
    false makes the entry bad (never repeated). The same folder as one before it is left out (said in the job output).
    Names: share when there is one, else share 1, share 2, ..."""
    raw = []
    if p.get("share") or p.get("share_refused"):
        path = str(p.get("share") or "")             # as written: a mounted folder's name may end in a space
        refused = bool(p.get("share_refused")) or "@" in path
        raw.append({"path": "" if refused else path, "subdir": str(p.get("share_subdir") or ""),
                    "user": "", "password": "", "auth": p.get("share_auth") or "negotiate",
                    "encrypt": bool(p.get("share_encrypt")), "refused": refused, "bad": None, "legacy": True,
                    "what": "sw_inventory_share"})
    for i, e in enumerate(p.get("shares") or [], 1):
        e = e if isinstance(e, dict) else {"path": e}
        path = str(e.get("path") or "")
        path = path if path.strip() else ""
        refused = bool(e.get("refused")) or "@" in path
        enc, bad = e.get("encrypt"), None
        if e.get("unresolved"):                 # 2.22: a key given whose value is an undefined variable (never a default)
            bad = ("its %s could not be read (an undefined variable)" % "/".join(sorted(e["unresolved"])),
                   "define the variable, or take that key out of the entry")
        elif enc is None or (isinstance(enc, str) and not enc.strip()):
            enc = bool(p.get("share_encrypt"))
        elif isinstance(enc, bool):
            pass
        elif str(enc).strip().lower() in TRUE:
            enc = True
        elif str(enc).strip().lower() in FALSE:
            enc = False
        else:
            bad, enc = ("its encrypt is not true or false (not shown)", "encrypt: true, or false, or leave it out"), False
        raw.append({"path": "" if refused else path, "subdir": str(e.get("subdir") or ""), "user": str(e.get("user") or ""),
                    "password": str(e.get("password") or ""),
                    "auth": str(e.get("auth") or "").strip() or p.get("share_auth") or "negotiate", "encrypt": enc,
                    "refused": refused, "bad": bad, "legacy": False, "what": "sw_inventory_shares entry %d" % i})
    out, seen, lines = [], set(), []
    for d in raw:
        d["show"] = shown(d)
        key = None if d["refused"] or d["bad"] or not d["path"] else dest_key(d["path"], d["subdir"])
        if key is not None and key in seen:
            lines.append("share: %s is the same folder as one before it - copied once" % d["what"])
            continue
        if key is not None:
            seen.add(key)
        out.append(d)
    for n, d in enumerate(out, 1):
        d["n"], d["name"] = n, "share" if len(out) == 1 else "share %d" % n
    return out, lines


def shown(d):
    """What a destination is called in the report: its folder only when the whole of it is valid (a share address
    with a server and a share, or an absolute folder, and a plain sub folder); else only which setting it is - a value
    not valid is never repeated (it could carry a login)."""
    hidden = "%s (not shown)" % d["what"]
    if d["refused"] or not d["path"]:
        return hidden
    try:
        sub = safe_sub(d["subdir"], "subdir")
        if smb_path(d["path"]) is None and not os.path.isabs(d["path"]):
            return hidden
    except Fail:
        return hidden
    return d["path"] + (" (sub folder %s)" % sub if sub else "")


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


def scrub(x, secrets):
    """x with every secret replaced by [redacted], in every string inside it."""
    if isinstance(x, dict):
        return dict((k, scrub(v, secrets)) for k, v in x.items())
    if isinstance(x, list):
        return [scrub(v, secrets) for v in x]
    if isinstance(x, str):
        for sec in secrets:
            x = x.replace(sec, "[redacted]")
    return x


def scrub_report(report, secrets):
    """The report's words with every secret replaced (its notes too); its statuses, columns and kinds untouched."""
    r = dict(report)
    for k in ("title", "subtitle", "footer", "head_name"):
        if isinstance(r.get(k), str):
            r[k] = scrub(r[k], secrets)
    r["head_scope"] = scrub(r.get("head_scope") or [], secrets)
    r["head_counts"] = [[c[0], scrub(c[1], secrets)] + list(c[2:]) for c in r.get("head_counts") or []]
    r["sections"] = []
    for sec in report.get("sections") or []:
        sec = dict(sec)
        for k in ("title", "text"):
            if isinstance(sec.get(k), str):
                sec[k] = scrub(sec[k], secrets)
        for k in ("lines", "rows"):
            if k in sec:
                sec[k] = scrub(sec[k], secrets)
        r["sections"].append(sec)
    return r


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
    shield_sub, local = None, None
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
    dests, dup_lines = destinations(p)
    multi = len(dests) > 1
    res["lines"].extend(dup_lines)
    shares = []                                 # the destinations open: [(d, folder, sub)], in order
    if p.get("shares_error") and want:          # the task's own words only (the list itself is never shown)
        said("shares", "failed", code="SA-SWI-20",
             error="sw_inventory_shares could not be read (not a list, or an undefined variable in it) - none of its folders written",
             fix="a list of {path: '\\\\fileserver.yourdomain.mil\\share\\folder', ...} (docs/LINUX_SW_INVENTORY.md); "
                 "define every variable it uses, or take that entry out")

    def dest_said(d, state, **kw):
        if multi and kw.get("error") and not d["show"].startswith(d["what"]):   # a hidden one names itself already
            kw["error"] = "%s: %s" % (d["show"], kw["error"])
        return said(d["name"], state, dest=d["show"], **kw)
    try:
        for d in dests if want else []:        # each destination on its own: one that fails never stops the next
            open_dest(p, check, d, dest_said, shares)
        return finish(p, check, res, said, dest_said, now, outputs, ok, bad, want, file_outs, local, shield_sub, dests,
                      multi, shares)
    finally:
        for _d, folder, _sub in shares:         # every destination's connections closed, whatever happened
            folder.close()


def open_dest(p, check, d, dest_said, shares):
    """One destination checked and opened (added to shares), or said failed."""
    if d["refused"]:
        dest_said(d, "failed", code="SA-SWI-20", error="%s has %s" % (d["what"], REFUSED[0]), fix=REFUSED[1])
        return
    if d["bad"]:
        dest_said(d, "failed", code="SA-SWI-20", error="%s: %s" % (d["what"], d["bad"][0]), fix=d["bad"][1])
        return
    if not d["path"]:
        dest_said(d, "failed", code="SA-SWI-20", error="%s has no path (empty, or an undefined variable)" % d["what"],
                  fix="write it as \\\\fileserver.yourdomain.mil\\share\\folder")
        return
    try:
        sub = safe_sub(d["subdir"], "sw_inventory_share_subdir" if d["legacy"] else d["what"] + " subdir")
        if smb_path(d["path"]):
            folder = SmbFolder(d["path"], d["auth"], d["encrypt"], p["share_port"], user=d["user"], password=d["password"])
        else:
            folder = LocalFolder(d["path"], make=False, check=check, what=d["what"])
    except Fail as e:
        dest_said(d, "failed", code=e.code, error=e.what, fix=e.fix)
        return
    shares.append((d, folder, sub))


def finish(p, check, res, said, dest_said, now, outputs, ok, bad, want, file_outs, local, shield_sub, dests, multi, shares):
    """run(), after the destinations are open: the baseline, the files, each share, the report."""
    # ---- the baseline: sw-inventory-state.json here and on each share, each host's newest list of them all ----
    state_doc, base_from, baseline, damaged, state_ok = None, "", {}, [], True
    state_bad = set()                           # shares whose state file could not be read: never written over there
    if want:
        found, broken = [], []                  # [(where, doc)] read whole; where a file was not JSON
        here = "sw-inventory-state.json in %s" % (p["dir"] or "the job's folder")
        try:
            raw = local.read(STATE) if local else None
        except Fail as e:                       # not "missing": never written over anywhere, said (as in 1.134.0)
            state_ok, raw = False, None
            said("baseline", "failed", code=e.code, error=e.what + " - no changes this run, the file is left as it is", fix=e.fix)
        sources = [(here, raw)] if raw is not None else []
        errs = []
        for d, folder, sub in shares if state_ok else []:   # each share on its own: one that cannot be read hides none
            try:
                got = folder.read(join(sub, STATE))
            except Fail as e:
                state_bad.add(d["name"])
                errs.append((d, e))
                continue
            if got is not None:
                sources.append(("sw-inventory-state.json on the share" + (" %s" % d["show"] if multi else ""), got))
        for where, body in sources:
            try:
                doc = json.loads(body.decode("utf-8"))
                if not isinstance(doc, dict):
                    raise ValueError("not an object")
                found.append((where, doc))
            except (ValueError, AttributeError, UnicodeDecodeError):
                broken.append(where)
        if state_ok and errs and not found:     # none read and one unreadable: the only last list may be there
            state_ok = False
            e = errs[0][1]
            said("baseline", "failed", code=e.code, error=e.what + " - no changes this run, the file is left as it is", fix=e.fix)
        for d, e in (errs if state_ok else []):
            res["lines"].append("%s: sw-inventory-state.json could not be read (%s %s) - left as it is there"
                                % (d["name"], e.code, e.what))
        if found:
            merged, bad_hosts, kept_bad = {}, set(), {}   # each host: the newest list of every copy (a stale copy never wins)
            for _where, doc in found:
                good, bad_here = sw.state_hosts(doc)
                bad_hosts.update(bad_here)
                for h in bad_here:              # a damaged entry is kept as it is (never dropped from the copies)
                    kept_bad.setdefault(h, doc["hosts"][h])
                for h in good:
                    entry = doc["hosts"][h]
                    if h not in merged or str(entry.get("read_at") or "") > str(merged[h].get("read_at") or ""):
                        merged[h] = entry
            baseline, _ = sw.state_hosts({"hosts": merged})
            damaged = sorted(bad_hosts - set(merged))
            state_doc = {"format": "sw-inventory-state/1",
                         "hosts": dict([(h, kept_bad[h]) for h in damaged] + list(merged.items()))}
            base_from = found[0][0] + (" (and %d more copies: each host's newest)" % (len(found) - 1) if len(found) > 1 else "")
        elif broken:
            damaged = ["(the whole file)"]
        for where in (broken if found else []):
            res["lines"].append("%s could not be read (not JSON) - the other copies used" % where)
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
        # a host whose kept list is newer than this read (an older job finishing late) keeps the newer one
        stored = (state_doc or {}).get("hosts") or {}
        newer = [h for h in ok if not (isinstance(stored.get(h["host"]), dict)
                                       and str(stored[h["host"]].get("read_at") or "") > str(h.get("read_at") or ""))]
        files["state"] = [{"rel": STATE, "body": json.dumps(sw.state_merge(state_doc, newer), separators=(",", ":")).encode("utf-8")}]
    kept_here = bool(p["dir"])
    done_here = set()                           # the files really written here (the email says where)
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
    if not kept_here and want and not dests:
        res["lines"].append("The files are only in the job's own folder (gone after the job): set sw_inventory_share or sw_inventory_dir.")

    # ---- the shares: each destination on its own, each file on its own ----
    done_shares = []                            # [(d, where, the files copied)] (the email says where)
    for d, folder, sub in shares:
        todo = [f for o in want for f in files[o]] + (files["state"] if d["name"] not in state_bad else [])
        where = folder.path(sub) if sub else folder.root
        done = set()
        if check:
            dest_said(d, "ok", where="(check mode: logged in, %s is there; %d files to copy)" % (folder.root, len(todo)))
        elif todo:
            failed = []
            for f in todo:
                try:
                    folder.write(join(sub, f["rel"]), f["body"])
                    done.add(f["rel"])
                except Fail as e:
                    failed.append(e)
            gone, left = prune_dated(folder, sub, p["keep_days"], now)
            if left:
                res["lines"].append("%s: %d old dated files could not be deleted: %s" % (d["name"], len(left), ", ".join(left[:5])))
            if failed:
                for e in failed:                # every one in the job output
                    res["lines"].append("%s: not copied - %s %s" % (d["name"], e.code, e.what))
                more = " and %d more (the job output lists them all)" % (len(failed) - 50) if len(failed) > 50 else ""
                dest_said(d, "failed", code=failed[0].code,
                          error="%d of %d files not copied to %s - %s%s" % (len(failed), len(todo), where,
                                                                           "; ".join(e.what for e in failed[:50]), more), fix=failed[0].fix)
            else:
                dest_said(d, "ok", where="%d files in %s%s" % (len(todo), where, ("; %d old dated files deleted" % gone) if gone else ""))
        else:
            dest_said(d, "skipped", why="no host read: nothing to copy")
        done_shares.append((d, where, done))
        folder.close()

    # ---- the report, the workbook, the email's and the website's files ----
    secrets = [d["password"] for d in dests if d["password"]]
    if secrets:                                 # an entry's password never in the words (each SMB error has it out already);
        for x in res["outputs"]:                # the states untouched - the report is made from these words
            for k in ("where", "error", "fix", "why", "dest"):
                x[k] = scrub(x.get(k, ""), secrets)
    res["not_read"], res["first_seen"] = bad, first
    view = {"hosts": [{"host": h["host"], "packages": len(h["packages"])} for h in ok], "not_read": bad,
            "outputs": res["outputs"], "changes_by_host": chg, "first_seen": first, "read_at": p["read_at"],
            "repo_notes": [h["host"] for h in ok if h["repo_note"]], "baseline": base_from, "state_damaged": damaged,
            "shares": sorted([x for x in res["outputs"] if x.get("dest")], key=lambda x: [d["name"] for d in dests].index(x["name"]))
            if multi else []}
    res["report"] = sw.report_doc(view, {"top": p["top"]})
    notes = []
    work = p["work_dir"]
    try:                                        # the workbook's sheets: written whole, or none (never a half file)
        sheets = sw.xlsx_sheets({"pkg_rows": list(sw.csv_rows(ok)), "changes_by_host": chg}, p["xlsx_max_rows"])
        LocalFolder(work).write("sheets.json", json.dumps(sheets).encode("utf-8"))
        res["sheets_file"] = os.path.join(work, "sheets.json")
    except (Fail, IOError, OSError, ValueError) as e:
        notes.append("The workbook could not be made (%s): the report goes without it." % getattr(e, "what", e))

    def where_is(rels, it="it is", were="it was"):
        """Where these files really are, from the writes that worked: the first share that has them all, else
        sw_inventory_dir, else nowhere."""
        for _d, where, done in done_shares:
            if rels and all(r in done for r in rels):
                return "%s on the share, %s" % (it, where)
        if rels and kept_here and not check and all(r in done_here for r in rels):
            return "%s in %s on the execution node" % (it, p["dir"])
        if not dests and not kept_here:
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
    if secrets:                                 # the words only: every state, status and path left as it is
        res["lines"] = scrub(res["lines"], secrets)
        res["report"] = scrub_report(res["report"], secrets)
    res["hosts_read"], res["hosts_not_read"] = len(ok), len(bad)
    res["packages"] = sum(len(h["packages"]) for h in ok)
    res["hosts_changed"] = len([h for h in chg if chg[h]])
    return res


def main():
    module = AnsibleModule(
        argument_spec=dict(work_dir=dict(type="str", required=True), hosts=dict(type="list", elements="dict", default=[]),
                           read_at=dict(type="str", default=""), job=dict(type="int", default=0),
                           result_from=dict(type="bool", default=False),
                           outputs=dict(type="list", elements="str", default=["shield", "csv", "json"]),
                           dir=dict(type="str", default=""), shield_subdir=dict(type="str", default="shield"),
                           share=dict(type="str", default=""), share_subdir=dict(type="str", default=""),
                           share_refused=dict(type="bool", default=False),
                           share_auth=dict(type="str", default="negotiate"), share_encrypt=dict(type="bool", default=False),
                           share_port=dict(type="int", default=445), keep_days=dict(type="int", default=90),
                           shares=dict(type="list", elements="dict", default=[],
                                       options=dict(path=dict(type="str", default=""), subdir=dict(type="str", default=""),
                                                    user=dict(type="str", default=""),
                                                    password=dict(type="str", default="", no_log=True),
                                                    auth=dict(type="str"),       # none: share_auth
                                                    encrypt=dict(type="raw"),    # none or '': share_encrypt; checked here
                                                    refused=dict(type="bool", default=False),
                                                    unresolved=dict(type="list", elements="str", default=[]))),
                           shares_error=dict(type="str", default=""),
                           xlsx_max_rows=dict(type="int", default=20000), email_max_mb=dict(type="float", default=10),
                           top=dict(type="int", default=15)),
        supports_check_mode=True)
    if module.params["result_from"]:            # the second call: the first one's result, as this task's own
        try:
            module.exit_json(**result_take(module.params["work_dir"]))
        except (IOError, OSError, ValueError):
            module.fail_json(msg="the controller step's result could not be read (see the step before)")
    if not module.params["read_at"]:
        module.fail_json(msg="read_at is required")
    res = run(module.params, module.check_mode, module.warn)
    res["changed"] = not module.check_mode and any(x["state"] == "ok" for x in res["outputs"])
    # 1.135.2: the result in a file of this run's own folder, read by the next task - never through exit_json, which
    # (ansible-core 2.16) masks every no_log value inside it: a password "fail" would turn "failed" into "********ed"
    result_put(module.params["work_dir"], res)
    module.exit_json(changed=res["changed"])    # no_log call: only that it worked (no path: 2.16 would mask it too)


RESULT = "result.json"


def result_put(work_dir, res):
    """The result in <work_dir>/result.json: made new (never through a link or an old file), owner only."""
    out = os.path.join(work_dir, RESULT)
    if os.path.lexists(out):
        os.unlink(out)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(res, f)


def result_take(work_dir):
    """<work_dir>/result.json read (never through a link) and deleted at once."""
    out = os.path.join(work_dir, RESULT)
    fd = os.open(out, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "r") as f:
        res = json.load(f)
    os.unlink(out)
    return res


if __name__ == "__main__":
    main()
