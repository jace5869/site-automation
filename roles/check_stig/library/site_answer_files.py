#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Evaluate-STIG's answer files, read once from where your site keeps them - a Windows file share
(\\\\server\\share\\folder, over SMB with smbprotocol: no mount) or a folder on the AAP side - into a folder on the AAP
side, for the job to copy to each Linux host before Evaluate-STIG runs (check_stig_evaluate_answers). Only reads the
share. Standard library only, but for a share: smbprotocol (in the execution environment)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_answer_files
short_description: Read Evaluate-STIG's answer files from a Windows file share (or a folder) into a folder here
description:
  - Lists C(src) - a share's folder (C(\\server\share\folder), C(smb://server/share/folder), C(//server/share/folder))
    or a folder on this side - and copies the files whose names match C(patterns) into C(dest), which is emptied first.
  - Reads only: nothing on the share is written, renamed or deleted. Sub-folders are not read.
  - Fails (C(error) set, nothing in C(dest)) when the share cannot be read, a file is larger than C(max_mb), there are
    more than C(max_files), or none matches - the job then runs no Evaluate-STIG rather than one without its answers.
options:
  src: {description: "The share's folder, or a folder on the AAP side.", type: str, required: true}
  dest: {description: A folder on the AAP side (emptied first)., type: str, required: true}
  base: {description: "A folder src is relative to, when it is a folder on the AAP side (the playbooks folder).", type: str, default: ''}
  patterns: {description: "File names to take (fnmatch, any case).", type: list, elements: str, default: ['*.xml']}
  username: {description: "The account (DOMAIN\\user or user@DOMAIN) for the share ('' = Kerberos, a ticket already here).", type: str, default: ''}
  password: {description: Its password., type: str, default: '', no_log: true}
  auth: {description: "negotiate (Kerberos, else NTLM), ntlm or kerberos.", type: str, default: negotiate}
  encrypt: {description: Encrypt (SMB 3) even when the share does not require it., type: bool, default: false}
  port: {description: The share's port., type: int, default: 445}
  timeout: {description: Seconds to connect., type: int, default: 30}
  max_files: {description: At most this many files., type: int, default: 500}
  max_mb: {description: A file larger than this many MB is refused., type: int, default: 20}
author: site automation
'''

RETURN = r'''
files:
  description: "Each file taken: [{name, size, sha256, modified, line}], by name (modified: when it last changed, UTC;
    line: the three in words, for the job output)."
  type: list
  returned: always
account:
  description: The account the share was read as ('' for a folder on the AAP side) - never its password.
  type: str
  returned: always
source:
  description: Where they were read from, as given (the report says it).
  type: str
  returned: always
error:
  description: Why none could be taken, in plain words ('' = read).
  type: str
  returned: always
'''

import datetime
import fnmatch
import hashlib
import os
import shutil

from ansible.module_utils.basic import AnsibleModule


class Fail(Exception):
    pass


def smb_path(src):
    """\\\\server\\share\\folder, smb://server/share/folder or //server/share/folder -> (server, the UNC path);
    None for anything else (a folder on this side)."""
    s = (src or "").strip()
    low = s.lower()
    if low.startswith("smb:"):
        s = "\\\\" + s[4:].lstrip("/").replace("/", "\\")
    elif s.startswith("//"):
        s = "\\\\" + s[2:].replace("/", "\\")
    if not s.startswith("\\\\"):
        return None
    s = s.rstrip("\\")
    parts = [x for x in s[2:].split("\\") if x]
    if len(parts) < 2:
        raise Fail("%s names no share (\\\\server\\share\\folder)" % src)
    return parts[0], "\\\\" + "\\".join(parts)


def smb_path_safe(src):
    try:
        return smb_path(src)
    except Fail:
        return None


def wanted(name, patterns):
    n = name.lower()
    return any(fnmatch.fnmatchcase(n, p.lower()) for p in patterns or ["*"])


def keep(p, name, read, size, modified=""):
    """Copy one file (read: a function giving its bytes) into dest -> its record (modified: when it last changed, UTC)."""
    if size > p["max_mb"] * 1024 * 1024:
        raise Fail("%s is %.1f MB - over check_stig_evaluate_answers_max_mb (%d)" % (name, size / 1048576.0, p["max_mb"]))
    if name in (".", "..") or "/" in name or "\\" in name:
        raise Fail("%s is not a plain file name" % name)
    data = read()
    with open(os.path.join(p["dest"], name), "wb") as f:
        f.write(data)
    sha = hashlib.sha256(data).hexdigest()
    return {"name": name, "size": len(data), "sha256": sha, "modified": modified,
            "line": "%s - %s bytes%s, SHA-256 %s" % (name, len(data), (", changed " + modified) if modified else "", sha)}


def when(t):
    """A time (seconds, or a datetime) -> 'YYYY-MM-DD HH:MM UTC' ('' when not known)."""
    try:
        if isinstance(t, datetime.datetime):
            d = t.astimezone(datetime.timezone.utc) if t.tzinfo else t
        else:
            d = datetime.datetime.fromtimestamp(float(t), datetime.timezone.utc)
        return d.strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def from_folder(p):
    src = p["src"] if os.path.isabs(p["src"]) or not p["base"] else os.path.join(p["base"], p["src"])
    if not os.path.isdir(src):
        raise Fail("no folder %s on the AAP side" % src)
    names = sorted(n for n in os.listdir(src) if os.path.isfile(os.path.join(src, n)) and wanted(n, p["patterns"]))

    def reader(path):
        def read():
            with open(path, "rb") as f:
                return f.read()
        return read
    return names, lambda n: keep(p, n, reader(os.path.join(src, n)), os.path.getsize(os.path.join(src, n)),
                                 when(os.path.getmtime(os.path.join(src, n))))


def why(what, e, p):
    """A failure on the share in plain words, with what to look at (as the report website's share: site_report_web.py)."""
    text = str(e)
    low = text.lower()
    if "logon_failure" in low or "logon failure" in low or "status_logon" in low or "invalid credentials" in low:
        hint = ": the user name or password is wrong (the Windows file share credential - user@YOURDOMAIN.MIL), or the account is locked"
    elif "access_denied" in low or "access is denied" in low:
        hint = ": the account may log in but not read this folder - give it Read on the share and the folder"
    elif "bad_network_name" in low or "0xc00000cc" in low:
        hint = ": no such share on that server (the part after the server name)"
    elif "0xc0000034" in low or "0xc000003a" in low or "no such file" in low:
        hint = ": no such folder on the share"
    elif "default realm" in low:
        hint = ": write the credential's user name as user@YOURDOMAIN.MIL - the domain's full name in capitals (its Kerberos realm)"
    elif "server not found in kerberos database" in low:
        hint = ": write the server by its full name (\\\\fileserver.yourdomain.mil\\share\\folder) - Kerberos asks for a ticket to that name"
    elif "md4" in low or "unsupported hash" in low:
        hint = (": NTLM needs MD4, which a FIPS execution environment does not have, and Kerberos did not happen - "
                "check_stig_evaluate_answers_auth: negotiate, the user name user@YOURDOMAIN.MIL, the server's full name")
    elif "failed to connect" in low or "timed out" in low or "refused" in low or "name or service" in low:
        hint = ": the server cannot be reached on port %d from the execution node (a firewall, or the name)" % p["port"]
    else:
        hint = ""
    return "%s: %s%s" % (what, text.splitlines()[0][:300] if text else type(e).__name__, hint)


def size_of(entry, unc, smbclient, kw):
    info = getattr(entry, "smb_info", None)
    if info is not None and getattr(info, "end_of_file", None) is not None:
        return int(info.end_of_file)
    return smbclient.stat(unc + "\\" + entry.name, **kw).st_size


def from_share(p, server, unc):
    try:
        import smbclient
    except ImportError:
        raise Fail("the answer files are on a file share (%s), and this execution environment has no smbprotocol: add it "
                   "to the execution environment (pip install smbprotocol)" % unc)
    if not p["username"] and p["auth"] != "kerberos":
        raise Fail("no login for the share %s: attach the \"Windows file share\" credential (user@YOURDOMAIN.MIL and its password) "
                   "to the template - or check_stig_evaluate_answers_auth: kerberos with a ticket" % unc)
    kw = {"port": p["port"]}
    try:
        smbclient.register_session(server, username=p["username"] or None, password=p["password"] or None, port=p["port"],
                                   encrypt=p["encrypt"] or None, auth_protocol=p["auth"], connection_timeout=p["timeout"])
    except Exception as e:                              # noqa: BLE001 - every way a login fails, said in words
        raise Fail(why("logging in to %s as %s" % (server, p["username"] or "the Kerberos ticket"), e, p))
    try:
        entries = [x for x in smbclient.scandir(unc, **kw) if x.is_file() and wanted(x.name, p["patterns"])]
        # the size the listing gave (an entry's own stat() would connect again, on the default port)
        sizes = dict((x.name, size_of(x, unc, smbclient, kw)) for x in entries)
        changed = dict((x.name, when(getattr(getattr(x, "smb_info", None), "last_write_time", None))) for x in entries)
    except Exception as e:                              # noqa: BLE001
        raise Fail(why("reading the folder %s" % unc, e, p))
    names = sorted(sizes)

    def take(n):
        def read():
            try:
                with smbclient.open_file(unc + "\\" + n, mode="rb", **kw) as f:
                    return f.read()
            except Exception as e:                      # noqa: BLE001
                raise Fail(why("reading %s\\%s" % (unc, n), e, p))
        return keep(p, n, read, sizes[n], changed[n])
    return names, take


def read_all(p):
    if os.path.isdir(p["dest"]):
        shutil.rmtree(p["dest"])
    os.makedirs(p["dest"], mode=0o700)
    share = smb_path(p["src"])
    names, take = from_share(p, *share) if share else from_folder(p)
    if not names:
        raise Fail("no answer file (%s) in %s" % (", ".join(p["patterns"]), p["src"]))
    if len(names) > p["max_files"]:
        raise Fail("%d files in %s - over check_stig_evaluate_answers_max_files (%d)" % (len(names), p["src"], p["max_files"]))
    return [take(n) for n in names]


def main():
    module = AnsibleModule(argument_spec=dict(
        src=dict(type="str", required=True),
        dest=dict(type="str", required=True),
        base=dict(type="str", default=""),
        patterns=dict(type="list", elements="str", default=["*.xml"]),
        username=dict(type="str", default=""),
        password=dict(type="str", default="", no_log=True),
        auth=dict(type="str", default="negotiate", choices=["negotiate", "ntlm", "kerberos"]),
        encrypt=dict(type="bool", default=False),
        port=dict(type="int", default=445),
        timeout=dict(type="int", default=30),
        max_files=dict(type="int", default=500),
        max_mb=dict(type="int", default=20),
    ), supports_check_mode=True)
    p = module.params
    # who read it, for the job output: the share's account (never its password), or the Kerberos ticket
    account = (p["username"] or ("the Kerberos ticket" if p["auth"] == "kerberos" else "")) if smb_path_safe(p["src"]) else ""
    try:
        files = read_all(p)
    except Fail as e:
        shutil.rmtree(p["dest"], ignore_errors=True)
        module.exit_json(changed=False, files=[], source=p["src"], account=account, error=str(e))
    module.exit_json(changed=False, files=files, source=p["src"], account=account, error="")


if __name__ == "__main__":
    main()
