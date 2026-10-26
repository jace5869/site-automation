#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Publish one report on the report website (roles/site_email, report_web_dest): its page, a copy as the
template's latest page, the report's data as JSON beside each, and the index of every report (index.html and
index.json, this template's line updated). Into a folder the job can write (a share mounted on the execution
node), onto a Windows file share over SMB (a \\\\server\\share\\folder path: smbprotocol, no mount), uploaded with HTTP PUT
(WebDAV: nginx, Apache, IIS), or - with windows_host - as a PowerShell script that
writes them on a Windows server (the role runs it there over WinRM). With keep (report_web_copy), nothing is written:
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
  - dest is a folder (an absolute path), a Windows file share's folder (\\server\share\folder, or
    smb://server/share/folder) or an http(s):// address that accepts PUT. A login for the upload comes
    from the environment - REPORT_WEB_TOKEN (sent as a bearer token), or REPORT_WEB_USERNAME and
    REPORT_WEB_PASSWORD (basic) - as the "Report website" credential sets it, never from a parameter. The share's
    login is REPORT_WEB_USERNAME (DOMAIN\user or user@domain) and REPORT_WEB_PASSWORD too, or a Kerberos ticket
    (smb_auth kerberos, no password). The share's folder must be there (it is never made); the folders inside it are.
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
import html as htmllib
import json
import os
import posixpath
import re
import ssl
import tempfile

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
                os.replace(tmp, path)
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
    m = re.match(r'(?i)^smb://(.+)$', d)
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
                       'the EE (ee/all-in-one/requirements.txt), or let the copy job write the pages (report_web_copy) from an EE '
                       'that has it (docs/WEB_REPORTS.md, "A Windows file share")' % self.root)
        self.smb, self.exc = smbclient, smbexc
        self.kw = {'port': port}
        user, password = os.environ.get('REPORT_WEB_USERNAME', ''), os.environ.get('REPORT_WEB_PASSWORD', '')
        if auth not in ('negotiate', 'ntlm', 'kerberos'):
            raise Fail('report_web_smb_auth must be negotiate, ntlm or kerberos, not %s' % auth)
        if not user and auth != 'kerberos':
            raise Fail('no login for the share %s: attach the "Report website" credential (DOMAIN\\user and its password) to '
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
            hint = ': the user name or password is wrong (the Report website credential - DOMAIN\\user), or the account is locked'
        elif 'access_denied' in low or 'access is denied' in low:
            hint = ': the account may log in but not use this folder - give it Modify on it (the share and the folder)'
        elif 'bad_network_name' in low or '0xc00000cc' in low or (what.startswith('opening the share') and (
                '0xc0000225' in low or 'no such file' in low)):
            hint = ': no such share on that server (the part after the server name)'
        elif 'md4' in low or 'unsupported hash' in low:
            hint = ': NTLM needs MD4, which a FIPS execution environment does not have - use report_web_smb_auth: kerberos'
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
            self.smb.replace(tmp, path, **self.kw)      # the old page is replaced whole: never half a page
        except Exception as e:                          # noqa: BLE001
            try:
                self.smb.remove(tmp, **self.kw)
            except Exception:                           # noqa: BLE001
                pass
            raise Fail(self._why('writing ' + path, e))

    def close(self):
        try:
            self.smb.reset_connection_cache()
        except Exception:                               # noqa: BLE001
            pass


class Staged(object):
    """A folder on a Windows server (windows_host): the files kept here, for the script that writes them there."""

    def __init__(self, index_json, index_text):
        self.index_json, self.index_text, self.files = index_json, index_text, []

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


def _ps_quote(text):
    return "'" + text.replace("'", "''") + "'"


def _ps_text(text):
    """A PowerShell expression of the text: here-strings, split where a line would start with '@ (their end)."""
    if text.startswith("'@"):
        return "'''@' + " + _ps_text(text[2:])
    return ' + "`n\'@" + '.join("(@'\n%s\n'@)" % part for part in text.split("\n'@"))


# writes the files into the folder: each to a temporary name beside it, then over the old one
PS_WRITER = r"""# site-automation - the report website (roles/site_email, report_web_host): this run's pages and the index,
# written into report_web_dest on this server. Made by the site_report_web module for one run, then deleted.
# Windows PowerShell 5.1 and PowerShell 7; also works in Constrained Language Mode.
$ErrorActionPreference = 'Stop'
$dest = %(dest)s
$out = @{ ok = $false; error = ''; written = @() }
function Save-File([string[]] $Parts, [string] $Text) {
    $path = $dest
    foreach ($p in $Parts) { $path = Join-Path -Path $path -ChildPath $p }
    $dir = Split-Path -Path $path -Parent
    if (-not (Test-Path -LiteralPath $dir -PathType Container)) { $null = New-Item -ItemType Directory -Path $dir -Force }
    $tmp = Join-Path -Path $dir -ChildPath ('.' + (Split-Path -Path $path -Leaf) + '.tmp')
    Set-Content -LiteralPath $tmp -Value $Text -Encoding Ascii -NoNewline
    $n = 0
    while ($true) {                                  # a page being read just then: again, a moment later
        try { Move-Item -LiteralPath $tmp -Destination $path -Force; break }
        catch {
            $n++
            if ($n -ge 5) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue; throw }
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


def windows_script(dest, files):
    """The PowerShell script that writes files [(rel, text)] into dest; a text used twice is in it once."""
    body, seen = [], {}
    for rel, text in files:
        if text not in seen:
            seen[text] = '$t%d' % len(seen)
            body.append('    %s = %s' % (seen[text], _ps_text(text)))
        body.append('    Save-File @(%s) %s' % (', '.join(_ps_quote(p) for p in rel.split('/')), seen[text]))
        body.append('    $out.written += @(%s)' % _ps_quote(rel))
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


def _pill(status):
    fg, bg, bd, word = PAL.get(status, PAL['unknown'])
    return ('<span style="display:inline-block;border-radius:999px;background:%s;border:1px solid %s;color:%s;font-size:13px;'
            'font-weight:700;padding:3px 11px;white-space:nowrap;">&#9679; %s</span>' % (bg, bd, fg, word))


def index_html(reports, index, header_text, logo, generated):
    """The index page: every template's last run, worst first - the portal look."""
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
    rows = []
    for r in reports:
        latest = r.get('latest') or r.get('page') or ''
        name = '<a href="%s" style="color:#0f6b4f;font-weight:700;text-decoration:none;">%s</a>' % (
            _href(index, latest), e(r.get('name') or r.get('key') or '')) if latest else e(r.get('name') or '')
        run = e(r.get('when') or '')
        if r.get('page'):
            run = '<a href="%s" style="color:#45534e;">%s</a>' % (_href(index, r['page']), run or 'this run')
        job = (' &middot; job %s' % e(str(r['job']))) if r.get('job') not in (None, '', 'local') else ''
        rows.append('<tr><td style="padding:10px 12px;border-top:1px solid #e4ece9;">%s</td>'
                    '<td style="padding:10px 12px;border-top:1px solid #e4ece9;">%s<div style="color:#5b6b66;font-size:13px;">%s</div></td>'
                    '<td style="padding:10px 12px;border-top:1px solid #e4ece9;white-space:nowrap;color:#45534e;font-size:13px;">%s%s</td></tr>'
                    % (_pill(r.get('status')), name, e(r.get('title') or ''), run, job))
    head_cell = ('padding:8px 12px;font-size:12px;font-weight:700;letter-spacing:1px;text-transform:uppercase;'
                 'color:#45534e;text-align:left;')
    logo_img = ('<td style="padding-right:14px;width:%dpx;"><img src="%s" width="%d" height="%d" alt="" style="display:block;"></td>'
                % (logo[1], logo[0], logo[1], logo[2])) if logo else ''
    return '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(header)s - reports</title>
</head>
<body style="margin:0;padding:0;background:#e7f1ee;font-family:'Segoe UI',Arial,Helvetica,sans-serif;font-size:14px;color:#0f1f1a;">
<div style="min-height:100vh;background:linear-gradient(160deg,#d9f0e7 0%%,#e5eff4 55%%,#dde8f2 100%%);padding:22px 12px;box-sizing:border-box;">
<div style="max-width:1000px;margin:0 auto;">
<div style="%(card)spadding:18px 20px;"><table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%%;"><tr>%(logo)s
<td><div style="font-size:18px;font-weight:700;">%(header)s</div><div style="color:#5b6b66;font-size:13px;">Every report's last run &middot; %(n)d report(s) &middot; updated %(generated)s</div></td>
</tr></table></div>
<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%%;margin:14px 0;table-layout:fixed;"><tr>%(tiles)s</tr></table>
<div style="%(card)spadding:8px 8px 4px 8px;overflow-x:auto;">
<table cellpadding="0" cellspacing="0" border="0" style="width:100%%;border-collapse:collapse;">
<tr><th style="%(th)swidth:130px;">Status</th><th style="%(th)s">Report</th><th style="%(th)swidth:230px;">Last run</th></tr>
%(rows)s
</table></div>
</div></div>
</body>
</html>
''' % {'header': e(header_text), 'card': card, 'logo': logo_img, 'n': len(reports), 'generated': e(generated),
       'tiles': tiles, 'th': head_cell,
       'rows': '\n'.join(rows) or '<tr><td colspan="3" style="padding:12px;color:#5b6b66;">No reports yet.</td></tr>'}


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
           'entry': dict(p['entry'], page=page, latest=latest), 'footer': p['footer']}
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
        is_newest = newer(next((r for r in reports if r.get('key') == entry.get('key')), None), entry)
        for rel in (page, latest if is_newest else ''):
            if not rel:
                continue
            target.write(rel, html, 'text/html; charset=utf-8')
            written.append(rel)
            if p['json']:
                doc = dict(p['data'], page=page, url=urls['url'], latest=latest, index=index)
                target.write(_json_name(rel), json.dumps(doc, indent=2, sort_keys=True, default=str) + '\n',
                             'application/json; charset=utf-8')
                written.append(_json_name(rel))
        index_text = ''
        if index:
            if is_newest:
                reports = [r for r in reports if r.get('key') != entry.get('key')] + [entry]
            reports.sort(key=lambda r: (RANK.get(r.get('status'), 2), str(r.get('name') or r.get('key') or '').lower()))
            generated = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            logo = None
            if p['images'] and len(p['logo_size']) == 2 and p['logo_size'][0] > 0:
                uri = inline_images('cid:' + str(p['images'][0].get('cid')), p['images'][:1])
                logo = (uri, p['logo_size'][0], p['logo_size'][1]) if uri.startswith('data:') else None
            index_text = json.dumps({'generated': generated, 'reports': reports}, indent=2, sort_keys=True, default=str) + '\n'
            target.write(_json_name(index), index_text, 'application/json; charset=utf-8')
            target.write(index, index_html(reports, index, p['header_text'], logo, generated), 'text/html; charset=utf-8')
            written += [_json_name(index), index]
    except Fail as e:
        module.fail_json(msg='The report website: %s' % e, **urls)
    finally:
        if isinstance(target, SmbTarget):
            target.close()
    if win:
        fd, script = tempfile.mkstemp(prefix='site_report_web_', suffix='.ps1')
        with os.fdopen(fd, 'wb') as f:
            f.write(windows_script(dest, target.files).encode('utf-8-sig'))   # PowerShell 5.1 reads it as UTF-8
        module.exit_json(changed=False, written=written, script=script, place=_place(where, page, bs), index_text=index_text,
                         newest=is_newest, msg='Made, for %s: %s' % (win, ', '.join(written)), **urls)
    module.exit_json(changed=True, written=written, place=_place(where, page, bs), index_text=index_text, newest=is_newest,
                     msg='Published: %s' % (urls['url'] or _place(where, page, bs)), **urls)


if __name__ == '__main__':
    main()
