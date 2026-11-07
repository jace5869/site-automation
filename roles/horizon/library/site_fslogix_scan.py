#!/usr/bin/python
# -*- coding: utf-8 -*-
"""The FSLogix profile shares, listed (playbooks/horizon_fslogix_report.yml): each share's folder (\\\\server\\share\\folder,
over SMB 3 with smbprotocol - no mount; or a folder on the AAP side), its user folders and the files in each - names,
sizes, last written - and the share's free space. Directory listings only: no file is opened, so no container is read,
written or locked (the folders are opened to list them with share access read, write and delete - an attached
container is not disturbed). The judging is plugins/filter/horizon_fslogix_filters.py. Standard library only, but for
a share: smbprotocol (in the execution environment)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_fslogix_scan
short_description: List FSLogix profile shares (folders, files, sizes, free space), read-only
description:
  - Lists each folder of C(shares) - a share's folder (C(\\server\share\folder), C(smb://server/share/folder),
    C(//server/share/folder)) or a folder on this side - and each folder directly under it (the users' folders), with
    the name, size and last write time of every file in them, and the share's free space.
  - Directory listings only (and one file system query for the free space). No file is opened.
options:
  shares: {description: "The folders that hold the users' folders (FSLogix's VHDLocations, CCDLocations).", type: list, elements: str, required: true}
  base: {description: "A folder a share is relative to, when it is a folder on the AAP side (the playbooks folder).", type: str, default: ''}
  username: {description: "The account (DOMAIN\\user or user@DOMAIN) for the shares ('' = Kerberos, a ticket already here).", type: str, default: ''}
  password: {description: Its password., type: str, default: '', no_log: true}
  auth: {description: "negotiate (Kerberos, else NTLM), ntlm or kerberos.", type: str, default: negotiate}
  encrypt: {description: Encrypt (SMB 3) even when the share does not require it., type: bool, default: false}
  port: {description: The shares' port., type: int, default: 445}
  timeout: {description: Seconds to connect., type: int, default: 30}
  max_folders: {description: At most this many user folders per share (the rest not read - said)., type: int, default: 20000}
author: site automation
'''

RETURN = r'''
shares:
  description: "Each share: {path, error ('' = read), total, free (bytes; null = not known), truncated, files (directly in it),
    folders: [{name, error, files: [{name, size, alloc, modified (seconds since 1970)}], dirs (sub-folder names)}]}."
  type: list
  returned: always
account:
  description: The account the shares were read as ('' for folders on the AAP side) - never its password.
  type: str
  returned: always
'''

import datetime
import os

from ansible.module_utils.basic import AnsibleModule


class Fail(Exception):
    pass


def smb_path(src):
    """\\\\server\\share\\folder, smb://server/share/folder or //server/share/folder -> (server, the UNC path); None for a
    folder on this side (as site_answer_files.py)."""
    s = (src or "").strip()
    if s.lower().startswith("smb:"):
        s = "\\\\" + s[4:].lstrip("/").replace("/", "\\")
    elif s.startswith("//"):
        s = "\\\\" + s[2:].replace("/", "\\")
    if not s.startswith("\\\\"):
        return None
    parts = [x for x in s.rstrip("\\")[2:].split("\\") if x]
    if len(parts) < 2:
        raise Fail("%s names no share (\\\\server\\share\\folder)" % src)
    return parts[0], "\\\\" + "\\".join(parts)


def smb_path_safe(src):
    try:
        return smb_path(src)
    except Fail:
        return None


def seconds(t):
    """A datetime (SMB's FILETIME, read as UTC) or seconds -> seconds since 1970 (0 when not known)."""
    try:
        if isinstance(t, datetime.datetime):
            return (t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)).timestamp()
        return float(t or 0)
    except (TypeError, ValueError, OverflowError, OSError):
        return 0


def why(what, e, p):
    """A failure on the share in plain words, with what to look at (as site_answer_files.py)."""
    text = str(e)
    low = text.lower()
    if "logon_failure" in low or "logon failure" in low or "status_logon" in low or "invalid credentials" in low:
        hint = ": the user name or password is wrong (the Windows file share credential - user@YOURDOMAIN.MIL), or the account is locked"
    elif "access_denied" in low or "access is denied" in low:
        hint = ": the account may log in but not list this folder - give it Read (List folder) on the share and the folder"
    elif "bad_network_name" in low or "0xc00000cc" in low:
        hint = ": no such share on that server (the part after the server name)"
    elif "0xc0000225" in low:
        hint = ": no such share or folder on that server (Samba says so for a share it does not have)"
    elif "0xc0000034" in low or "0xc000003a" in low or "no such file" in low:
        hint = ": no such folder on the share"
    elif "default realm" in low:
        hint = ": write the credential's user name as user@YOURDOMAIN.MIL - the domain's full name in capitals (its Kerberos realm)"
    elif "server not found in kerberos database" in low:
        hint = ": write the server by its full name (\\\\fileserver.yourdomain.mil\\share\\folder) - Kerberos asks for a ticket to that name"
    elif "md4" in low or "unsupported hash" in low:
        hint = (": NTLM needs MD4, which a FIPS execution environment does not have, and Kerberos did not happen - "
                "horizon_fslogix_auth: negotiate, the user name user@YOURDOMAIN.MIL, the server's full name")
    elif "failed to connect" in low or "timed out" in low or "refused" in low or "name or service" in low:
        hint = ": the server cannot be reached on port %d from the execution node (a firewall, or the name)" % p["port"]
    else:
        hint = ""
    return "%s: %s%s" % (what, text.splitlines()[0][:300] if text else type(e).__name__, hint)


# ---- a folder on this side (tests, a share mounted on the execution node) ----------------------------------------------
def local_entries(folder):
    out = []
    with os.scandir(folder) as it:
        for x in it:
            st = x.stat(follow_symlinks=False)
            out.append({"name": x.name, "dir": x.is_dir(follow_symlinks=False), "size": st.st_size,
                        "alloc": getattr(st, "st_blocks", 0) * 512, "modified": st.st_mtime})
    return out


def local_free(folder):
    try:
        v = os.statvfs(folder)
        return v.f_blocks * v.f_frsize, v.f_bavail * v.f_frsize
    except (OSError, AttributeError):
        return None, None


# ---- a share --------------------------------------------------------------------------------------------------------
class Share(object):
    def __init__(self, p, server):
        try:
            import smbclient
        except ImportError:
            raise Fail("the profiles are on a file share, and this execution environment has no smbprotocol: add it to the "
                       "execution environment (pip install smbprotocol)")
        if not p["username"] and p["auth"] != "kerberos":
            raise Fail("no login for the share: attach the \"Windows file share\" credential (user@YOURDOMAIN.MIL and its password) "
                       "to the template - or horizon_fslogix_auth: kerberos with a ticket")
        self.smb, self.p, self.kw = smbclient, p, {"port": p["port"]}
        try:
            smbclient.register_session(server, username=p["username"] or None, password=p["password"] or None, port=p["port"],
                                       encrypt=p["encrypt"] or None, auth_protocol=p["auth"], connection_timeout=p["timeout"])
        except Exception as e:                          # noqa: BLE001 - every way a login fails, said in words
            raise Fail(why("logging in to %s as %s" % (server, p["username"] or "the Kerberos ticket"), e, p))

    def entries(self, folder):
        # scandir opens the folder only (share access read, write, delete) and lists it: no file is opened
        out = []
        for x in self.smb.scandir(folder, **self.kw):
            if x.name in (".", ".."):
                continue
            info = x.smb_info
            out.append({"name": x.name, "dir": x.is_dir(), "size": int(info.end_of_file), "alloc": int(info.allocation_size),
                        "modified": seconds(info.last_write_time)})
        return out

    def free(self, folder):
        # FileFsFullSizeInformation, the folder opened with FILE_READ_ATTRIBUTES only (smbprotocol 1.10 and later)
        stat_volume = getattr(self.smb, "stat_volume", None)
        if stat_volume is None:
            return None, None
        try:
            v = stat_volume(folder, **self.kw)
            return int(v.total_size), int(v.caller_available_size)
        except Exception:                               # noqa: BLE001 - the report says "not known"
            return None, None


def plain(e):
    return {"name": e["name"], "size": e["size"], "alloc": e["alloc"], "modified": e["modified"]}


def scan_one(p, src, sessions):
    rec = {"path": src, "error": "", "total": None, "free": None, "truncated": False, "files": [], "folders": []}
    try:
        share = smb_path(src)
        if share:
            server, folder = share
            if server.lower() not in sessions:
                sessions[server.lower()] = Share(p, server)
            s = sessions[server.lower()]
            list_, free = s.entries, s.free
        else:
            folder = src if os.path.isabs(src) or not p["base"] else os.path.join(p["base"], src)
            if not os.path.isdir(folder):
                raise Fail("no folder %s on the AAP side" % folder)
            list_, free = local_entries, local_free
        sep = "\\" if share else os.sep
        try:
            top = sorted(list_(folder), key=lambda x: x["name"].lower())
        except Exception as e:                        # noqa: BLE001
            raise Fail(why("listing %s" % src, e, p))
        rec["total"], rec["free"] = free(folder)
        rec["files"] = [plain(x) for x in top if not x["dir"]]
        dirs = [x for x in top if x["dir"]]
        if len(dirs) > p["max_folders"]:
            rec["truncated"], dirs = True, dirs[:p["max_folders"]]
        for d in dirs:
            fo = {"name": d["name"], "error": "", "files": [], "dirs": []}
            try:
                for x in sorted(list_(folder.rstrip(sep) + sep + d["name"]), key=lambda x: x["name"].lower()):
                    if x["dir"]:
                        fo["dirs"].append(x["name"])
                    else:
                        fo["files"].append(plain(x))
            except Exception as e:                      # noqa: BLE001 - one folder not read: said, the others still read
                fo["error"] = why("listing it", e, p)
            rec["folders"].append(fo)
    except Fail as e:
        rec["error"] = str(e)
    return rec


def main():
    module = AnsibleModule(argument_spec=dict(
        shares=dict(type="list", elements="str", required=True),
        base=dict(type="str", default=""),
        username=dict(type="str", default=""),
        password=dict(type="str", default="", no_log=True),
        auth=dict(type="str", default="negotiate", choices=["negotiate", "ntlm", "kerberos"]),
        encrypt=dict(type="bool", default=False),
        port=dict(type="int", default=445),
        timeout=dict(type="int", default=30),
        max_folders=dict(type="int", default=20000),
    ), supports_check_mode=True)
    p = module.params
    sessions = {}
    shares = [scan_one(p, s, sessions) for s in p["shares"] if str(s or "").strip()]
    any_smb = any(smb_path_safe(s) for s in p["shares"])
    account = (p["username"] or ("the Kerberos ticket" if p["auth"] == "kerberos" else "")) if any_smb else ""
    module.exit_json(changed=False, shares=shares, account=account)


if __name__ == "__main__":
    main()
