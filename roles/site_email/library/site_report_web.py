#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Publish one report on the report website (roles/site_email, report_web_dest): its page, a copy as the
template's latest page, the report's data as JSON beside each, and the index of every report (index.html and
index.json, this template's line updated, with its last runs - history; the pages of older runs deleted). Into a
folder the job can write (a share mounted on the execution node), onto a Windows file share over SMB (a
\\\\server\\share\\folder path: smbprotocol, no mount), uploaded with HTTP PUT (WebDAV: nginx, Apache, IIS), or - with
windows_host - as a PowerShell script that writes them on a Windows server (the role runs it there over WinRM). With keep (report_web_copy), nothing is written:
the report is packed for the job "Report website - copy", which writes it later (plugins/module_utils/site_report_page.py).
Standard library only, but for the SMB share: smbprotocol (pure Python; in the execution environment)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_report_web
short_description: Publish a report as web pages (a folder or an HTTP PUT upload), with an index of every report
description:
  - Writes the page, the template's latest page and their JSON, then reads the index (index.json), puts this
    template's line in it and writes index.json and index.html again.
  - Each template's line keeps its last runs (history, newest first); with delete_old, the pages (and JSON) of the runs
    that fall off it are deleted - only pages the index listed for this template, never its latest page or the index.
    One that cannot be deleted is a warning, not a failure.
  - A page being read just then (open in a browser, on a share) is written again a moment later, a few times.
  - dest is a folder (an absolute path), a Windows file share's folder (\\server\share\folder, or
    smb://server/share/folder) or an http(s):// address that accepts PUT. A login for the upload comes
    from the environment - REPORT_WEB_TOKEN (sent as a bearer token), or REPORT_WEB_USERNAME and
    REPORT_WEB_PASSWORD (basic) - as the "Report website" credential sets it, never from a parameter. The share's
    login is REPORT_WEB_USERNAME (user@YOURDOMAIN.MIL - the realm in capitals - for Kerberos; DOMAIN\user for NTLM
    only) and REPORT_WEB_PASSWORD too, or a Kerberos ticket (smb_auth kerberos, no password). The share's folder must
    be there (it is never made); the folders inside it are.
  - In check mode nothing is written; with a share, the job still logs in and looks at the folder (read-only), so a
    dry run proves the login and the path.
  - The pictures of the page (src="cid:name", the logo) are put inside it (data:), so every page stands alone.
  - With windows_host, dest is a folder on that Windows server - nothing is written here, the files are put in a
    PowerShell script (returned as script, a temporary file) that writes them there; index_text is that
    server's index.json, read before. The script only uses what Constrained Language Mode allows.
  - The template's latest page and its line in the index are only written over by a run at least as new as the one
    already there (its when) - a copy of an older run (the copy job) never takes the place of a newer one.
  - With keep, nothing is written and html is not needed - the report, its file names, address, index line and footer
    are returned packed (packed, at most max_kb) for the copy job; a report too big even with its longest tables cut
    returns packed empty, and msg says so.
  - In check mode nothing is written; the address is returned.
options:
  dest: {description: The report folder (an absolute path) or the http(s) address it is uploaded to., type: str, required: true}
  url: {description: The address people open the folder at (default - dest when it is http(s))., type: str, default: ''}
  files: {description: "The names inside the folder - {page, latest, index} (report_web_files); '' = none.", type: dict, required: true}
  page_url: {description: "The page's address, as the report job printed it (the copy job; '' = url and the page's name).", type: str, default: ''}
  html: {description: The page (not with keep)., type: str, default: ''}
  images: {description: "Pictures in the page: [{cid: name, path: file}].", type: list, elements: dict, default: []}
  data: {description: The report and its run (written as JSON beside the page)., type: dict, default: {}}
  entry: {description: "This template's line of the index: key (one per template), name, title, status, when, job.", type: dict, required: true}
  json: {description: Write the JSON files., type: bool, default: true}
  history: {description: "The runs each template's line of the index keeps (report_web_history), at least 1.", type: int, default: 5}
  delete_old: {description: "Delete the pages of the runs older than those (report_web_delete_old).", type: bool, default: true}
  header_text: {description: The line beside the logo on the index page., type: str, default: Site automation report}
  logo_size: {description: "[width, height] of the logo on the index page ([] = no logo).", type: list, elements: int, default: []}
  ca_path: {description: A CA file for the upload address (default - the CAs the execution environment trusts)., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 30}
  windows_host: {description: "The Windows server dest is on (report_web_host): a script to write the files there.", type: str, default: ''}
  index_text: {description: "With windows_host - the index.json there ('' = none yet; as text, or read already).", type: raw, default: ''}
  keep: {description: Write nothing - return the report packed for the copy job (report_web_copy)., type: bool, default: false}
  max_kb: {description: With keep - the packed report's greatest size (report_web_copy_max_kb)., type: int, default: 256}
  smb_auth: {description: "The share's login - negotiate (Kerberos, else NTLM), ntlm or kerberos.", type: str, default: negotiate}
  smb_encrypt: {description: Encrypt the share's traffic (SMB 3); a share that requires it is encrypted anyway., type: bool, default: false}
  smb_port: {description: The share's port., type: int, default: 445}
  footer: {description: With keep - the email's footer line (the AAP job, who started it, when) for the copied page., type: str, default: ''}
author: site automation
'''

RETURN = r'''
url: {description: The page's address ('' without url or an http dest)., type: str}
latest_url: {description: The template's latest page., type: str}
index_url: {description: The index of every report., type: str}
written: {description: The files written (inside the folder)., type: list}
deleted: {description: "The pages (and JSON) of older runs deleted (with windows_host - to be deleted by the script).", type: list}
not_deleted: {description: "Older runs' files that could not be deleted: [[file, why]].", type: list}
place: {description: "Where the page is, without an address (/mnt/reports/..., web01:D:\\reports\\...).", type: str}
script: {description: "With windows_host - the PowerShell script that writes them there (a temporary file; delete it).", type: str}
index_text: {description: The index (index.json) as written - for the next page of the same run (the copy job)., type: str}
newest: {description: This run is at least as new as the template's line in the index (its latest page was written)., type: bool}
packed: {description: "With keep - the report packed for the copy job ('' = too big, see msg)", type: str}
note: {description: "With keep - what was kept, its size (and the tables cut to fit).", type: str}
cut: {description: "With keep - the tables cut to fit max_kb: [[section, rows kept, rows]]", type: list}
'''

import base64
import datetime
import errno
import fnmatch
import html as htmllib
import json
import os
import posixpath
import re
import ssl
import tempfile
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_report_page import KEEP_AT_LEAST, pack
from ansible.module_utils.six.moves.urllib.error import HTTPError, URLError
from ansible.module_utils.six.moves.urllib.parse import quote
from ansible.module_utils.six.moves.urllib.request import Request, build_opener, HTTPSHandler

IMAGE_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif'}
RANK = {'critical': 0, 'warning': 1, 'unknown': 2, 'info': 3, 'ok': 4}
PAL = {
    'ok': ('#13734f', '#e3f5ec', '#bfe6d3', 'Successful'),
    'warning': ('#9a5806', '#fdf2df', '#f3d6a6', 'Warning'),
    'critical': ('#b42318', '#fdeaea', '#f5c4c4', 'Critical'),
    'info': ('#1d4ed8', '#e6effd', '#c3d5f7', 'Info'),
    'unknown': ('#4b5753', '#eef2f1', '#d5ddda', 'Unknown')}
IN_USE = (errno.EACCES, errno.EBUSY, errno.EPERM, errno.ETXTBSY)    # a mounted share's file open in a browser
RUN_KEYS = ('page', 'status', 'when', 'job', 'title')
WAIT = (0.5, 1, 2, 3, 4)                                 # a file in use: written again after these seconds


def again(what, retry):
    """what(), done again after each of WAIT's pauses while retry(the error) says it may work a moment later."""
    for pause in WAIT + (None,):
        try:
            return what()
        except Exception as e:                          # noqa: BLE001
            if pause is None or not retry(e):
                raise
            time.sleep(pause)


class Fail(Exception):
    pass


class Target(object):
    """The report folder: a local path, or an http(s) address (GET to read, PUT to write, MKCOL for a folder)."""

    def __init__(self, dest, ca_path, timeout):
        self.dest = dest.rstrip('/')
        self.http = bool(re.match(r'(?i)https?://', dest))
        self.timeout = timeout
        self.auth = ''
        if self.http:
            ctx = ssl.create_default_context(cafile=ca_path or None)
            self.opener = build_opener(HTTPSHandler(context=ctx))
            token = os.environ.get('REPORT_WEB_TOKEN', '')
            user, password = os.environ.get('REPORT_WEB_USERNAME', ''), os.environ.get('REPORT_WEB_PASSWORD', '')
            self.auth = ('Bearer ' + token if token else
                         'Basic ' + base64.b64encode(('%s:%s' % (user, password)).encode('utf-8')).decode('ascii') if user else '')
        elif not os.path.isabs(dest):
            raise Fail('report_web_dest must be a full path (/mnt/reports) or an http(s):// address, not %s' % dest)
        elif not os.path.isdir(dest):                    # never a new folder on the execution node's own disk
            raise Fail('the folder %s is not there - is the share mounted, and the path exposed to the job '
                       '(docs/WEB_REPORTS.md)?' % dest)

    def _call(self, method, rel, body=None, ctype=None):
        req = Request(self.dest + '/' + quote(rel), data=body)
        req.get_method = lambda: method
        if self.auth:
            req.add_header('Authorization', self.auth)
        if ctype:
            req.add_header('Content-Type', ctype)
        return self.opener.open(req, timeout=self.timeout)

    def read(self, rel):
        """The file's text, or None when it is not there."""
        if not self.http:
            path = os.path.join(self.dest, *rel.split('/'))
            if not os.path.exists(path):
                return None
            with open(path, 'rb') as f:
                return f.read().decode('utf-8')
        try:
            return self._call('GET', rel).read().decode('utf-8')
        except HTTPError as e:
            if e.code == 404:
                return None
            raise Fail('reading %s/%s: HTTP %s %s%s' % (self.dest, rel, e.code, e.reason, _login_hint(e.code)))
        except URLError as e:
            raise Fail('reading %s/%s: %s' % (self.dest, rel, e.reason))

    def write(self, rel, text, ctype):
        body = text.encode('utf-8')
        if not self.http:
            path = os.path.join(self.dest, *rel.split('/'))
            folder = os.path.dirname(path)
            try:
                if not os.path.isdir(folder):
                    os.makedirs(folder)
                fd, tmp = tempfile.mkstemp(prefix='.', suffix='.tmp', dir=folder)
                with os.fdopen(fd, 'wb') as f:
                    f.write(body)
                try:
                    os.chmod(tmp, 0o644)             # readable by the web server (a share may not allow it)
                except OSError:
                    pass
                try:
                    again(lambda: os.replace(tmp, path), lambda e: isinstance(e, OSError) and e.errno in IN_USE)
                except OSError:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                    raise
            except OSError as e:
                raise Fail('writing %s: %s' % (path, e.strerror or e))
            return
        try:
            self._call('PUT', rel, body, ctype).read()
        except HTTPError as e:
            if e.code != 409 or '/' not in rel:          # 409: the folder is not there (WebDAV) - make it, again
                raise Fail('uploading %s/%s: HTTP %s %s%s' % (self.dest, rel, e.code, e.reason, _login_hint(e.code)))
            self._folders(rel)
            try:
                self._call('PUT', rel, body, ctype).read()
            except HTTPError as e2:
                raise Fail('uploading %s/%s: HTTP %s %s%s' % (self.dest, rel, e2.code, e2.reason, _login_hint(e2.code)))
        except URLError as e:
            raise Fail('uploading %s/%s: %s' % (self.dest, rel, e.reason))

    def remove(self, rel):
        """Delete a file (gone already is fine)."""
        if not self.http:
            path = os.path.join(self.dest, *rel.split('/'))
            try:
                again(lambda: os.remove(path), lambda e: isinstance(e, OSError) and e.errno in IN_USE)
            except OSError as e:
                if e.errno != errno.ENOENT:
                    raise Fail(e.strerror or str(e))
            return
        try:
            self._call('DELETE', rel).read()
        except HTTPError as e:
            if e.code not in (404, 410):
                raise Fail('HTTP %s %s%s' % (e.code, e.reason, _login_hint(e.code)))
        except URLError as e:
            raise Fail(str(e.reason))

    def _folders(self, rel):
        parts = rel.split('/')[:-1]
        for i in range(1, len(parts) + 1):
            try:
                self._call('MKCOL', '/'.join(parts[:i]) + '/').read()
            except HTTPError as e:
                if e.code == 409:                        # the report folder itself is not there - never made here
                    raise Fail('the folder %s is not there on the website (report_web_dest)' % self.dest)
                if e.code != 405:                        # 405: it is there already
                    raise Fail('making the folder %s/%s: HTTP %s %s%s' % (self.dest, '/'.join(parts[:i]), e.code, e.reason, _login_hint(e.code)))


def smb_path(dest):
    """\\\\server\\share\\folder, //server/share/folder or smb://server/share/folder -> (server, '\\\\server\\share\\folder'),
    or None when dest is no share."""
    d = dest.strip()
    m = re.match(r'(?i)^smb:/+(.+)$', d)                # smb://server/..., also smb:/server/... (one slash lost on the way)
    if m:
        d = '//' + m.group(1)
    if not (d.startswith('\\\\') or d.startswith('//')):
        return None
    parts = [x for x in re.split(r'[\\/]+', d) if x]
    if len(parts) < 2:
        raise Fail('report_web_dest %s: a share is \\\\server\\share\\folder' % dest)
    return parts[0], '\\\\' + '\\'.join(parts)


class SmbTarget(object):
    """A folder on a Windows file share, over SMB 2/3 (smbprotocol): no mount, the login from the Report website credential."""

    def __init__(self, dest, auth='negotiate', encrypt=False, port=445, timeout=30):
        self.server, self.root = smb_path(dest)
        try:
            import smbclient
            from smbprotocol import exceptions as smbexc
        except ImportError:
            raise Fail('report_web_dest is a file share (%s), and this execution environment has no smbprotocol: add it to '
                       'the EE (the EEs of ee/ have it since 0.32.0: build yours again), or let the copy job write the pages '
                       '(report_web_copy) from an EE that has it (docs/REPORT_SHARE.md, step 3)' % self.root)
        self.smb, self.exc = smbclient, smbexc
        self.kw, self.auth = {'port': port}, auth
        user, password = os.environ.get('REPORT_WEB_USERNAME', ''), os.environ.get('REPORT_WEB_PASSWORD', '')
        if auth not in ('negotiate', 'ntlm', 'kerberos'):
            raise Fail('report_web_smb_auth must be negotiate, ntlm or kerberos, not %s' % auth)
        if not user and auth != 'kerberos':
            raise Fail('no login for the share %s: attach the "Report website" credential (user@YOURDOMAIN.MIL and its password) to '
                       'the template - or report_web_smb_auth: kerberos with a ticket' % self.root)
        try:
            smbclient.register_session(self.server, username=user or None, password=password or None, port=port,
                                       encrypt=encrypt or None, auth_protocol=auth, connection_timeout=timeout)
        except Exception as e:                          # noqa: BLE001 - every way a login fails, said in words
            raise Fail(self._why('logging in to %s as %s' % (self.server, user or 'the Kerberos ticket'), e))
        try:
            ok = smbclient.path.isdir(self.root, **self.kw)
        except Exception as e:                          # noqa: BLE001
            raise Fail(self._why('opening %s' % self.root, e))
        if not ok:
            share = '\\\\%s\\%s' % (self.server, self.root.split('\\')[3])
            try:
                smbclient.listdir(share, **self.kw)     # the share itself: is it there?
            except Exception as e:                      # noqa: BLE001
                raise Fail(self._why('opening the share %s' % share, e))
            raise Fail('the folder %s is not there - make it once by hand (report_web_dest)' % self.root)

    def _why(self, what, e):
        text = str(e)
        low = text.lower()
        if 'logon_failure' in low or 'logon failure' in low or 'status_logon' in low or 'invalid credentials' in low:
            hint = ': the user name or password is wrong (the Report website credential - user@YOURDOMAIN.MIL), or the account is locked'
        elif 'access_denied' in low or 'access is denied' in low:
            hint = (': the account may log in but not use this folder - give it Modify on it (the share and the folder); or the '
                    'file was open all that time (a browser, an editor), tried again for about 10 seconds')
        elif 'bad_network_name' in low or '0xc00000cc' in low or (what.startswith('opening the share') and (
                '0xc0000225' in low or 'no such file' in low)):
            hint = ': no such share on that server (the part after the server name)'
        elif 'default realm' in low:
            hint = (': write the Report website credential\'s user name as user@YOURDOMAIN.MIL - the domain\'s full name in capitals '
                    '(its Kerberos realm)')
        elif 'invalid token was supplied' in low and self.auth == 'kerberos':
            hint = (': kerberos sends a bare Kerberos token, which this server does not take - use report_web_smb_auth: negotiate '
                    '(Kerberos too, the way the server expects it)')
        elif 'server not found in kerberos database' in low:
            hint = (': write the server by its full name in report_web_dest (\\\\fileserver.yourdomain.mil\\share\\folder) - '
                    'Kerberos asks for a ticket to that name')
        elif 'md4' in low or 'unsupported hash' in low:
            hint = (': NTLM needs MD4, which a FIPS execution environment does not have, and Kerberos did not happen - '
                    'report_web_smb_auth: negotiate, the user name user@YOURDOMAIN.MIL (the realm in capitals), the server\'s '
                    'full name in report_web_dest')
        elif 'failed to connect' in low or 'timed out' in low or 'refused' in low or 'name or service' in low:
            hint = ': the server cannot be reached on port %d from the execution node (a firewall, or the name)' % self.kw['port']
        else:
            hint = ''
        return '%s: %s%s' % (what, text.splitlines()[0][:300] if text else type(e).__name__, hint)

    def _path(self, rel):
        return self.root + '\\' + rel.replace('/', '\\')

    def read(self, rel):
        try:
            with self.smb.open_file(self._path(rel), mode='rb', **self.kw) as f:
                return f.read().decode('utf-8')
        except OSError as e:
            if getattr(e, 'errno', None) == 2:          # not there
                return None
            raise Fail(self._why('reading ' + self._path(rel), e))
        except Exception as e:                          # noqa: BLE001
            raise Fail(self._why('reading ' + self._path(rel), e))

    def write(self, rel, text, ctype):
        path = self._path(rel)
        folder = path.rsplit('\\', 1)[0]
        tmp = '%s\\.%s.%s.tmp' % (folder, path.rsplit('\\', 1)[1], os.getpid())
        try:
            if folder != self.root:
                self.smb.makedirs(folder, exist_ok=True, **self.kw)
            with self.smb.open_file(tmp, mode='wb', **self.kw) as f:
                f.write(text.encode('utf-8'))
            # the old page is replaced whole (never half a page); one open just then, in a browser, a moment later
            again(lambda: self.smb.replace(tmp, path, **self.kw), _smb_in_use)
        except Exception as e:                          # noqa: BLE001
            try:
                self.smb.remove(tmp, **self.kw)
            except Exception:                           # noqa: BLE001
                pass
            raise Fail(self._why('writing ' + path, e))

    def remove(self, rel):
        path = self._path(rel)
        try:
            again(lambda: self.smb.remove(path, **self.kw), _smb_in_use)
        except OSError as e:
            if getattr(e, 'errno', None) != errno.ENOENT and 'object_name_not_found' not in str(e).lower():
                raise Fail(self._why('deleting ' + path, e))
        except Exception as e:                          # noqa: BLE001
            raise Fail(self._why('deleting ' + path, e))

    def close(self):
        try:
            self.smb.reset_connection_cache()
        except Exception:                               # noqa: BLE001
            pass


def _smb_in_use(e):
    """An SMB error that a moment later may not be there: the file open in a browser, or being deleted."""
    low = str(e).lower()
    return any(w in low for w in ('sharing_violation', 'sharing violation', '0xc0000043', 'access_denied', 'access is denied',
                                  '0xc0000022', 'delete_pending', '0xc0000056', 'being used by another'))


class Staged(object):
    """A folder on a Windows server (windows_host): the files kept here, for the script that writes them there."""

    def __init__(self, index_json, index_text):
        self.index_json, self.index_text, self.files, self.removed = index_json, index_text, [], []

    def read(self, rel):
        if rel != self.index_json or not self.index_text:
            return None
        # Ansible may have read the text as JSON already
        return self.index_text if isinstance(self.index_text, str) else json.dumps(self.index_text)

    def write(self, rel, text, ctype):
        # ASCII only: the script writes it in any PowerShell, in Constrained Language Mode too - every other
        # character as an HTML character reference (the JSON is ASCII already: \uXXXX)
        if rel.endswith('.html'):
            text = text.encode('ascii', 'xmlcharrefreplace').decode('ascii')
        self.files = [f for f in self.files if f[0] != rel] + [(rel, text)]

    def remove(self, rel):
        self.removed.append(rel)


def _ps_quote(text):
    return "'" + text.replace("'", "''") + "'"


def _ps_text(text):
    """A PowerShell expression of the text: here-strings, split where a line would start with '@ (their end)."""
    if text.startswith("'@"):
        return "'''@' + " + _ps_text(text[2:])
    return ' + "`n\'@" + '.join("(@'\n%s\n'@)" % part for part in text.split("\n'@"))


# writes the files into the folder: each to a temporary name beside it, then over the old one
PS_WRITER = r"""# site-automation - the report website (roles/site_email, report_web_host): this run's pages and the index,
# written into report_web_dest on this server, older runs' pages deleted. Made by the site_report_web module for one
# run, then deleted. Windows PowerShell 5.1 and PowerShell 7; also works in Constrained Language Mode.
$ErrorActionPreference = 'Stop'
$dest = %(dest)s
$out = @{ ok = $false; error = ''; written = @(); deleted = @(); left = @(); busy = @() }
function Save-File([string[]] $Parts, [string] $Text, [bool] $Soft = $false) {
    $path = $dest
    foreach ($p in $Parts) { $path = Join-Path -Path $path -ChildPath $p }
    $dir = Split-Path -Path $path -Parent
    if (-not (Test-Path -LiteralPath $dir -PathType Container)) { $null = New-Item -ItemType Directory -Path $dir -Force }
    $tmp = Join-Path -Path $dir -ChildPath ('.' + (Split-Path -Path $path -Leaf) + '.tmp')
    Set-Content -LiteralPath $tmp -Value $Text -Encoding Ascii -NoNewline
    $n = 0
    while ($true) {                                  # a page being read just then: again, a moment later
        try { Move-Item -LiteralPath $tmp -Destination $path -Force; $out.written += @($Parts -join '/'); break }
        catch {
            $n++
            if ($n -ge 6) {                          # about 8 seconds
                Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
                if (-not $Soft) { throw }
                $out.busy += @(($Parts -join '/') + ': ' + [string]$_.Exception.Message)   # latest or the index: next run
                return
            }
            Start-Sleep -Milliseconds (500 * $n)
        }
    }
}
function Remove-Old([string[]] $Parts, [string] $Rel) {
    $path = $dest
    foreach ($p in $Parts) { $path = Join-Path -Path $path -ChildPath $p }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return }
    $n = 0
    while ($true) {                                  # an older run's page: never a failure of this run
        try { Remove-Item -LiteralPath $path -Force; $out.deleted += @($Rel); break }
        catch {
            $n++
            if ($n -ge 5) { $out.left += @($Rel + ': ' + [string]$_.Exception.Message); break }
            Start-Sleep -Milliseconds 500
        }
    }
}
try {
    if (-not (Test-Path -LiteralPath $dest -PathType Container)) {
        throw ('the folder ' + $dest + ' is not there on ' + $env:COMPUTERNAME + ' (report_web_dest)')
    }
%(body)s
    $out.ok = $true
}
catch { $out.error = [string]$_.Exception.Message }

'###SITE-JSON### ' + (ConvertTo-Json -InputObject @{ write = $out } -Depth 4 -Compress)
"""


def windows_script(dest, files, remove=(), soft=()):
    """The PowerShell script that writes files [(rel, text)] into dest, then deletes remove [rel]; a text used twice is
    in it once. One of soft (latest, the index) open there all that time is left for the next run, not a failure."""
    body, seen = [], {}
    for rel, text in files:
        if text not in seen:
            seen[text] = '$t%d' % len(seen)
            body.append('    %s = %s' % (seen[text], _ps_text(text)))
        body.append('    Save-File @(%s) %s%s' % (', '.join(_ps_quote(p) for p in rel.split('/')), seen[text],
                                                ' $true' if rel in soft else ''))
    for rel in remove:
        body.append('    Remove-Old @(%s) %s' % (', '.join(_ps_quote(p) for p in rel.split('/')), _ps_quote(rel)))
    return PS_WRITER % {'dest': _ps_quote(dest), 'body': '\n'.join(body)}


def _login_hint(code):
    return (' - check the login (the "Report website" credential: REPORT_WEB_TOKEN, or REPORT_WEB_USERNAME and REPORT_WEB_PASSWORD)'
            if code in (401, 403) else '')


def inline_images(page, images):
    """src="cid:name" -> src="data:..." for each picture that can be read."""
    for img in images or []:
        cid, path = str(img.get('cid') or ''), str(img.get('path') or '')
        ctype = IMAGE_TYPES.get(os.path.splitext(path)[1].lower())
        if not cid or not ctype or not os.path.isfile(path):
            continue
        with open(path, 'rb') as f:
            uri = 'data:%s;base64,%s' % (ctype, base64.b64encode(f.read()).decode('ascii'))
        page = page.replace('cid:' + cid, uri)
    return page


# in a browser, the email's background fills the window (the outer table at least as tall, its content at the top)
WEB_STYLE = ('<style>body>table:first-of-type{height:100vh}'
             'body>table:first-of-type>tbody>tr>td{vertical-align:top}</style>')


def web_page(page, images):
    """The email's HTML as a web page: its pictures inside it, its background filling the window."""
    page = inline_images(page, images)
    return page.replace('</head>', WEB_STYLE + '\n</head>', 1)


def _href(src, dst):
    """A link from the file src to the file dst, both inside the folder."""
    return quote(posixpath.relpath(dst, posixpath.dirname(src) or '.'))


def _join(base, rel):
    return (base.rstrip('/') + '/' + quote(rel)) if base and rel else ''


def _pill(status, small=False):
    fg, bg, bd, word = PAL.get(status, PAL['unknown'])
    return ('<span style="display:inline-block;border-radius:999px;background:%s;border:1px solid %s;color:%s;font-size:%dpx;'
            'font-weight:700;padding:%s;white-space:nowrap;">&#9679; %s</span>'
            % (bg, bd, fg, 12 if small else 13, '1px 9px' if small else '3px 11px', word))


def area(name, tabs=()):
    """The tab a report is on: the first of tabs (report_web_tabs: [{tab, match: [names, * = anything]}]) one of whose
    names its template's name matches, any case; else its name up to ' - ' (Backup - nightly: Backup); else Other."""
    low = str(name or '').lower()
    for t in tabs or []:
        match = t.get('match') or []
        if any(fnmatch.fnmatchcase(low, str(m).lower()) for m in ([match] if isinstance(match, str) else match)):
            return str(t.get('tab') or 'Other')
    head = str(name or '').split(' - ', 1)
    return head[0].strip() if len(head) == 2 and head[0].strip() else 'Other'


def _job(job):
    return ('job %s' % htmllib.escape(str(job))) if job not in (None, '', 'local') else ''


def _runs(r):
    """A line's runs, newest first - an index from before 0.34.0 has none: its one run."""
    runs = [x for x in (r.get('runs') or []) if isinstance(x, dict) and x.get('page')]
    return runs or ([dict((k, r.get(k)) for k in RUN_KEYS)] if r.get('page') else [])


# the index page's tabs (radio buttons and their labels: no script) and its lines that open (details) - every other
# style of the page is inline
INDEX_STYLE = """<style>
.tab{position:absolute;opacity:0;pointer-events:none}
.bar label{display:inline-block;cursor:pointer;background:#ffffff;border:2px solid #cfdcd7;border-radius:999px;padding:6px 14px;
margin:0 6px 8px 0;font-size:13px;font-weight:700;color:#45534e}
.bar label:hover{border-color:#0f6b4f}
.r>summary{list-style:none;cursor:pointer}
.r>summary::-webkit-details-marker{display:none}
.r>summary:hover{background:#f3f8f6}
.r .caret{display:inline-block;transition:transform .15s;color:#45534e}
.r[open] .caret{transform:rotate(90deg)}
@media (max-width:600px){.head{display:none!important}.tiles td{padding:0 3px!important}.tiles td>div{padding:10px 8px!important}
.tiles td>div>div:first-child{font-size:10px!important;letter-spacing:0!important}}
%s</style>"""

INDEX_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(header)s - reports</title>
%(style)s
</head>
<body style="margin:0;padding:0;background:#e7f1ee;font-family:'Segoe UI',Arial,Helvetica,sans-serif;font-size:14px;color:#0f1f1a;">
<div style="min-height:100vh;background:linear-gradient(160deg,#d9f0e7 0%%,#e5eff4 55%%,#dde8f2 100%%);padding:22px 12px;box-sizing:border-box;">
<div style="max-width:1000px;margin:0 auto;">
<div style="%(card)spadding:18px 20px;"><table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%%;"><tr>%(logo)s
<td><div style="font-size:18px;font-weight:700;">%(header)s</div><div style="color:#5b6b66;font-size:13px;">Every report's last runs &middot; %(n)d report(s) &middot; updated %(generated)s</div></td>
</tr></table></div>
<table class="tiles" role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%%;margin:14px 0;table-layout:fixed;"><tr>%(tiles)s</tr></table>
%(tabs)s<div class="list" style="%(card)spadding:4px 8px;">
<div class="head" style="display:flex;flex-wrap:wrap;gap:6px 14px;padding:8px 12px 8px 36px;%(th)s"><span style="width:120px;">Status</span><span style="flex:1 1 260px;">Report</span><span>Last run - open a line for its runs</span></div>
%(rows)s
</div>
</div></div>
</body>
</html>
"""


def index_html(reports, index, header_text, logo, generated, tabs=()):
    """The index page: a tab per area (All first), every report's line - worst first - opening to its last runs."""
    e = htmllib.escape
    counts = dict((k, 0) for k in PAL)
    for r in reports:
        counts[r.get('status') if r.get('status') in PAL else 'unknown'] += 1
    card = 'background:#ffffff;border:2px solid #cfdcd7;border-radius:20px;box-shadow:0 6px 22px rgba(15,70,52,.08);'
    tiles = ''.join(
        '<td style="padding:0 6px;"><div style="%spadding:12px 16px;%s"><div style="font-size:12px;font-weight:700;'
        'letter-spacing:1px;text-transform:uppercase;color:#45534e;">%s</div><div style="font-family:Consolas,Menlo,monospace;'
        'font-size:28px;font-weight:700;color:%s;">%d</div></div></td>'
        % (card, ('border-color:%s;' % PAL[k][0]) if counts[k] and k in ('critical', 'warning') else '', label,
           PAL[k][0] if k != 'all' else '#0f1f1a', counts[k])
        for k, label in (('critical', 'Critical'), ('warning', 'Warning'), ('ok', 'Successful'), ('info', 'Info')))
    of = [area(r.get('name') or r.get('key'), tabs) for r in reports]
    order = [str(t.get('tab')) for t in tabs or []]               # report_web_tabs' order, then A to Z, Other last
    areas = sorted(set(of), key=lambda a: (a == 'Other', order.index(a) if a in order else len(order), a.lower()))
    tab = dict((a, i + 1) for i, a in enumerate(areas))
    rows = []
    for r, a in zip(reports, of):
        latest = r.get('latest') or r.get('page') or ''
        name = '<a href="%s" style="color:#0f6b4f;font-weight:700;text-decoration:none;">%s</a>' % (
            _href(index, latest), e(r.get('name') or r.get('key') or '')) if latest else e(r.get('name') or '')
        run = e(r.get('when') or '')
        if r.get('page'):
            run = '<a href="%s" style="color:#45534e;">%s</a>' % (_href(index, r['page']), run or 'this run')
        runs = _runs(r)
        lines = ''.join(
            '<tr><td style="padding:5px 12px 5px 0;">%s</td><td style="padding:5px 12px 5px 0;white-space:nowrap;">'
            '<a href="%s" style="color:#0f6b4f;font-weight:600;">%s</a>%s</td>'
            '<td style="padding:5px 12px 5px 0;white-space:nowrap;color:#45534e;font-size:13px;">%s</td>'
            '<td style="padding:5px 0;color:#5b6b66;font-size:13px;min-width:200px;">%s</td></tr>'
            % (_pill(x.get('status'), True), _href(index, x['page']), e(x.get('when') or 'this run'),
               ' <span style="color:#5b6b66;font-size:12px;">(newest)</span>' if not i else '', _job(x.get('job')),
               e(x.get('title') or '')) for i, x in enumerate(runs))
        rows.append(
            '<details class="r a%d" style="border-top:1px solid #e4ece9;"><summary style="display:flex;flex-wrap:wrap;'
            'align-items:center;gap:6px 14px;padding:10px 12px;"><span class="caret">&#9656;</span>'
            '<span style="width:120px;">%s</span><span style="flex:1 1 260px;min-width:0;">%s'
            '<div style="color:#5b6b66;font-size:13px;">%s</div></span>'
            '<span style="white-space:nowrap;color:#45534e;font-size:13px;">%s%s &middot; %d run%s</span></summary>'
            '<div style="padding:2px 12px 12px 36px;overflow-x:auto;"><table cellpadding="0" cellspacing="0" border="0" '
            'style="border-collapse:collapse;">%s</table></div></details>'
            % (tab[a], _pill(r.get('status')), name, e(r.get('title') or ''), run,
               (' &middot; ' + _job(r.get('job'))) if _job(r.get('job')) else '', len(runs), '' if len(runs) == 1 else 's',
               lines or '<tr><td style="color:#5b6b66;">No page of its runs listed.</td></tr>'))
    # All, then the areas, A to Z (Other last); a dot on an area with a Critical or a Warning
    tabs, rules = [], []
    for i, label in enumerate(['All'] + areas):
        mine = [r for r, a in zip(reports, of) if not i or a == label]
        worst = min([RANK.get(r.get('status'), 2) for r in mine] or [4])
        dot = '<span style="color:%s;">&#9679;</span> ' % PAL['critical' if worst == 0 else 'warning'][0] if i and worst <= 1 else ''
        tabs.append(('<input type="radio" name="tab" id="t%d" class="tab"%s>' % (i, '' if i else ' checked'),
                     '<label for="t%d">%s%s <span style="font-weight:400;">%d</span></label>' % (i, dot, e(label), len(mine))))
        rules.append('#t%d:checked~.bar label[for=t%d]{background:#0f6b4f;border-color:#0f6b4f;color:#ffffff}\n'
                     '#t%d:checked~.bar label[for=t%d] span{color:#ffffff!important}\n'
                     '#t%d:focus-visible~.bar label[for=t%d]{outline:2px solid #0f6b4f;outline-offset:2px}\n' % ((i,) * 6))
        if i:
            rules.append('#t%d:checked~.list .r:not(.a%d){display:none}\n' % (i, i))
    logo_img = ('<td style="padding-right:14px;width:%dpx;"><img src="%s" width="%d" height="%d" alt="" style="display:block;"></td>'
                % (logo[1], logo[0], logo[1], logo[2])) if logo else ''
    return INDEX_PAGE % {
        'header': e(header_text), 'card': card, 'logo': logo_img, 'n': len(reports), 'generated': e(generated), 'tiles': tiles,
        'style': INDEX_STYLE % ''.join(rules),
        'tabs': (''.join(t[0] for t in tabs) + '\n<div class="bar">' + ''.join(t[1] for t in tabs) + '</div>\n') if reports else '',
        'th': 'font-size:12px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#45534e;',
        'rows': '\n'.join(rows) or '<div style="padding:12px;color:#5b6b66;">No reports yet.</div>'}


def history(old, entry, n):
    """This template's line of the index with this run in its runs (newest first, n of them), and the runs that fell
    off it. A run of the copy job older than the line's own (old) leaves the line as it is, its runs but for this."""
    this = dict((k, entry.get(k)) for k in RUN_KEYS)
    runs = [x for x in (_runs(old) if old else []) if x.get('page') != this['page']]
    runs = sorted(runs + [this], key=lambda x: (str(x.get('when') or ''), x is this), reverse=True)   # this run first of its minute
    line = dict(entry if newer(old, entry) else old)
    line['runs'] = runs[:max(1, n)]
    return line, [x for x in runs[max(1, n):] if x is not this]          # this run's page, just written, stays


def deletable(rel, kept):
    """An older run's page (or its JSON) that may be deleted: inside the folder, a page, not one still listed."""
    return (isinstance(rel, str) and rel.endswith(('.html', '.json')) and rel not in kept and not rel.startswith('/')
            and '\\' not in rel and ':' not in rel and '..' not in rel.split('/') and '' not in rel.split('/'))


def _place(where, rel, win):
    """Where a file is, without an address: /mnt/reports/a/b.html, web01:D:\\reports\\a\\b.html or \\\\fs01\\its\\a\\b.html."""
    return where + ('\\' + rel.replace('/', '\\') if win else '/' + rel)


def _json_name(rel):
    return rel[:-5] + '.json'


def _reports(known):
    """The index's lines, from index.json's text (none when it is not there or cannot be read)."""
    try:
        reports = (json.loads(known).get('reports') or []) if known else []
    except (ValueError, AttributeError):
        reports = []                                     # unreadable: start again with this report
    return [r for r in reports if isinstance(r, dict)]


def newer(old, entry):
    """This run (entry) is at least as new as the index's line for its template (old) - by when, 2026-10-23 06:05 UTC."""
    return not old or str(entry.get('when') or '') >= str(old.get('when') or '')


def keep(module, p, page, latest, index, urls, where, win):
    """With keep: the report packed for the copy job, nothing written."""
    place = urls['url'] or _place(where, page, win)
    if module.check_mode:
        module.exit_json(changed=False, packed='', cut=[], msg='Dry run (Check): the report would be kept for the job Report website - copy, '
                         'for %s' % place, **urls)
    doc = {'files': {'page': page, 'latest': latest, 'index': index}, 'url': urls['url'], 'data': p['data'],
           'entry': dict(p['entry'], page=page, latest=latest), 'footer': p['footer'],
           'history': p['history'], 'delete_old': p['delete_old']}       # the report template's own, for the copy job
    text, cut = pack(doc, max(16, p['max_kb']) * 1024)
    if not text:
        module.exit_json(changed=False, packed='', cut=[list(c) for c in cut], msg=(
            'No web page for this run: its report is bigger than report_web_copy_max_kb (%d KB) even with its tables cut to %d '
            'rows - raise report_web_copy_max_kb, or let this template write its pages itself (report_web_copy: false)'
            % (p['max_kb'], KEEP_AT_LEAST)), **urls)
    note = 'kept for the job Report website - copy, which writes it there (%d KB%s)' % (
        (len(text) + 1023) // 1024, '; cut to fit: ' + ', '.join('%s %d of %d' % c for c in cut) if cut else '')
    module.exit_json(changed=False, packed=text, cut=[list(c) for c in cut], place=place, note=note,
                     msg='%s%s: %s' % (note[0].upper(), note[1:], place), **urls)


def main():
    module = AnsibleModule(
        argument_spec=dict(
            dest=dict(type='str', required=True),
            url=dict(type='str', default=''),
            files=dict(type='dict', required=True),
            html=dict(type='str', default=''),
            images=dict(type='list', elements='dict', default=[]),
            data=dict(type='dict', default={}),
            entry=dict(type='dict', required=True),
            json=dict(type='bool', default=True),
            history=dict(type='int', default=5),
            delete_old=dict(type='bool', default=True),
            tabs=dict(type='list', elements='dict', default=[]),
            header_text=dict(type='str', default='Site automation report'),
            logo_size=dict(type='list', elements='int', default=[]),
            ca_path=dict(type='str', default=''),
            timeout=dict(type='int', default=30),
            windows_host=dict(type='str', default=''),
            index_text=dict(type='raw', default=''),
            page_url=dict(type='str', default=''),
            keep=dict(type='bool', default=False),
            max_kb=dict(type='int', default=256),
            footer=dict(type='str', default=''),
            smb_auth=dict(type='str', default='negotiate'),
            smb_encrypt=dict(type='bool', default=False),
            smb_port=dict(type='int', default=445),
        ),
        supports_check_mode=True,
    )
    p = module.params
    dest = p['dest'].strip()
    win = p['windows_host'].strip()
    try:
        smb = None if win else smb_path(dest)
    except Fail as e:
        module.fail_json(msg='The report website: %s' % e)
    where = ('%s:%s' % (win, dest.rstrip('/\\'))) if win else smb[1] if smb else dest.rstrip('/')
    bs = bool(win or smb)                                # places written with backslashes
    page, latest, index = (str(p['files'].get(k) or '') for k in ('page', 'latest', 'index'))
    base = (p['url'].strip() or (dest if re.match(r'(?i)https?://', dest) and not win else '')).rstrip('/')
    urls = dict(url=p['page_url'].strip() or _join(base, page), latest_url=_join(base, latest), index_url=_join(base, index))
    if not page:
        module.fail_json(msg='no page name (report_web_page)')
    if p['keep']:
        keep(module, p, page, latest, index, urls, where, bs)
    if module.check_mode and not smb:
        module.exit_json(changed=False, written=[], msg='Dry run (Check): the report would be published at %s'
                         % (urls['url'] or _place(where, page, bs)), **urls)
    target = None
    try:
        if win:
            target = Staged(_json_name(index) if index else '', p['index_text'])
        elif smb:
            target = SmbTarget(dest, p['smb_auth'], p['smb_encrypt'], p['smb_port'], p['timeout'])
            if module.check_mode:                        # logged in and the folder is there: that is all a dry run does
                module.exit_json(changed=False, written=[], msg='Dry run (Check): logged in to %s, the folder %s is there; the report '
                                 'would be published at %s' % (target.server, target.root, urls['url'] or _place(where, page, True)), **urls)
        else:
            target = Target(dest, p['ca_path'], p['timeout'])
        html = web_page(p['html'], p['images'])
        written = []
        entry = dict(p['entry'], page=page, latest=latest)
        reports = _reports(target.read(_json_name(index))) if index else []
        old = next((r for r in reports if r.get('key') == entry.get('key')), None)
        is_newest = newer(old, entry)
        busy = []                                        # latest and the index, open somewhere all that time: next run

        def put(rel, text, ctype, soft=False):
            try:
                target.write(rel, text, ctype)
                written.append(rel)
            except Fail as e:
                if not soft:
                    raise
                busy.append([rel, str(e)])
        for rel in (page, latest if is_newest else ''):
            if not rel:
                continue
            put(rel, html, 'text/html; charset=utf-8', rel == latest)
            if p['json']:
                doc = dict(p['data'], page=page, url=urls['url'], latest=latest, index=index)
                put(_json_name(rel), json.dumps(doc, indent=2, sort_keys=True, default=str) + '\n',
                    'application/json; charset=utf-8', rel == latest)
        index_text = ''
        deleted, not_deleted = [], []
        if index:
            line, dropped = history(old, entry, p['history'])
            reports = [r for r in reports if r.get('key') != entry.get('key')] + [line]
            reports.sort(key=lambda r: (RANK.get(r.get('status'), 2), str(r.get('name') or r.get('key') or '').lower()))
            generated = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            logo = None
            if p['images'] and len(p['logo_size']) == 2 and p['logo_size'][0] > 0:
                uri = inline_images('cid:' + str(p['images'][0].get('cid')), p['images'][:1])
                logo = (uri, p['logo_size'][0], p['logo_size'][1]) if uri.startswith('data:') else None
            index_text = json.dumps({'generated': generated, 'reports': reports}, indent=2, sort_keys=True, default=str) + '\n'
            put(_json_name(index), index_text, 'application/json; charset=utf-8')
            put(index, index_html(reports, index, p['header_text'], logo, generated, p['tabs']), 'text/html; charset=utf-8', True)
            # the pages of the runs that fell off this template's line - after the index, which no longer lists them
            kept = set([index, _json_name(index)])
            for r in reports:
                for rel in [r.get('latest'), r.get('page')] + [x.get('page') for x in _runs(r)]:
                    if rel:
                        kept.update([rel, _json_name(rel)])
            # only with a latest page: the line is then one template's (report_web_latest is per template - without it,
            # templates whose pages start with the same folder, {site}/{template}/..., would share a line)
            for x in (dropped if p['delete_old'] and latest else []):
                for rel in (x['page'], _json_name(x['page'])):
                    if not deletable(rel, kept):
                        continue
                    try:
                        target.remove(rel)
                        deleted.append(rel)
                    except Fail as e:
                        not_deleted.append([rel, str(e)])
            for rel, why in not_deleted:
                module.warn('The report website: an older run\'s %s could not be deleted (%s) - give the account Modify on '
                            'the folder, or report_web_delete_old: false' % (rel, why))
        for rel, why in busy:
            module.warn('The report website: %s was not written - it stayed open somewhere (a browser, an editor) for the '
                        '10 seconds the job tried; the next run writes it (%s)' % (rel, why))
    except Fail as e:
        module.fail_json(msg='The report website: %s' % e, **urls)
    finally:
        if isinstance(target, SmbTarget):
            target.close()
    if win:
        fd, script = tempfile.mkstemp(prefix='site_report_web_', suffix='.ps1')
        with os.fdopen(fd, 'wb') as f:
            f.write(windows_script(dest, target.files, target.removed, [latest, _json_name(latest) if latest else '', index]).encode('utf-8-sig'))   # PowerShell 5.1 reads it as UTF-8
        module.exit_json(changed=False, written=written, script=script, place=_place(where, page, bs), index_text=index_text,
                         newest=is_newest, deleted=deleted, not_deleted=not_deleted, not_written=busy, msg='Made, for %s: %s' % (win, ', '.join(written)), **urls)
    module.exit_json(changed=True, written=written, place=_place(where, page, bs), index_text=index_text, newest=is_newest,
                     deleted=deleted, not_deleted=not_deleted, not_written=busy, msg='Published: %s%s' % (
                         urls['url'] or _place(where, page, bs),
                         ' (older runs deleted: %s)' % ', '.join(d for d in deleted if d.endswith('.html')) if deleted else ''), **urls)


if __name__ == '__main__':
    main()
