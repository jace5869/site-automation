#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Publish one report on the report website (roles/site_email, report_web_dest): its page, a copy as the
template's latest page, the report's data as JSON beside each, and the index of every report (index.html and
index.json, this template's line updated). Into a folder the job can write (a share mounted on the execution
node), uploaded with HTTP PUT (WebDAV: nginx, Apache, IIS), or - with windows_host - as a PowerShell script that
writes them on a Windows server (the role runs it there over WinRM). Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_report_web
short_description: Publish a report as web pages (a folder or an HTTP PUT upload), with an index of every report
description:
  - Writes the page, the template's latest page and their JSON, then reads the index (index.json), puts this
    template's line in it and writes index.json and index.html again.
  - dest is a folder (an absolute path) or an http(s):// address that accepts PUT. A login for the upload comes
    from the environment - REPORT_WEB_TOKEN (sent as a bearer token), or REPORT_WEB_USERNAME and
    REPORT_WEB_PASSWORD (basic) - as the "Report website" credential sets it, never from a parameter.
  - The pictures of the page (src="cid:name", the logo) are put inside it (data:), so every page stands alone.
  - With windows_host, dest is a folder on that Windows server: nothing is written here, the files are put in a
    PowerShell script (returned as script, a temporary file) that writes them there; index_text is that
    server's index.json, read before. The script only uses what Constrained Language Mode allows.
  - In check mode nothing is written; the address is returned.
options:
  dest: {description: The report folder (an absolute path) or the http(s) address it is uploaded to., type: str, required: true}
  url: {description: The address people open the folder at (default - dest when it is http(s))., type: str, default: ''}
  files: {description: "The names inside the folder - {page, latest, index} (report_web_files); '' = none.", type: dict, required: true}
  html: {description: The page., type: str, required: true}
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
author: site automation
'''

RETURN = r'''
url: {description: The page's address ('' without url or an http dest)., type: str}
latest_url: {description: The template's latest page., type: str}
index_url: {description: The index of every report., type: str}
written: {description: The files written (inside the folder)., type: list}
place: {description: "Where the page is, without an address (/mnt/reports/..., web01:D:\\reports\\...).", type: str}
script: {description: "With windows_host - the PowerShell script that writes them there (a temporary file; delete it).", type: str}
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
    """Where a file is, without an address: /mnt/reports/a/b.html, or web01:D:\\reports\\a\\b.html."""
    return where + ('\\' + rel.replace('/', '\\') if win else '/' + rel)


def _json_name(rel):
    return rel[:-5] + '.json'


def main():
    module = AnsibleModule(
        argument_spec=dict(
            dest=dict(type='str', required=True),
            url=dict(type='str', default=''),
            files=dict(type='dict', required=True),
            html=dict(type='str', required=True),
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
        ),
        supports_check_mode=True,
    )
    p = module.params
    dest = p['dest'].strip()
    win = p['windows_host'].strip()
    where = ('%s:%s' % (win, dest.rstrip('/\\'))) if win else dest.rstrip('/')
    page, latest, index = (str(p['files'].get(k) or '') for k in ('page', 'latest', 'index'))
    base = (p['url'].strip() or (dest if re.match(r'(?i)https?://', dest) and not win else '')).rstrip('/')
    urls = dict(url=_join(base, page), latest_url=_join(base, latest), index_url=_join(base, index))
    if not page:
        module.fail_json(msg='no page name (report_web_page)')
    if module.check_mode:
        module.exit_json(changed=False, written=[], msg='Dry run (Check): the report would be published at %s'
                         % (urls['url'] or _place(where, page, win)), **urls)
    try:
        target = Staged(_json_name(index) if index else '', p['index_text']) if win else Target(dest, p['ca_path'], p['timeout'])
        html = web_page(p['html'], p['images'])
        written = []
        for rel in (page, latest):
            if not rel:
                continue
            target.write(rel, html, 'text/html; charset=utf-8')
            written.append(rel)
            if p['json']:
                doc = dict(p['data'], page=page, url=urls['url'], latest=latest, index=index)
                target.write(_json_name(rel), json.dumps(doc, indent=2, sort_keys=True, default=str) + '\n',
                             'application/json; charset=utf-8')
                written.append(_json_name(rel))
        if index:
            entry = dict(p['entry'], page=page, latest=latest)
            known = target.read(_json_name(index))
            try:
                reports = (json.loads(known).get('reports') or []) if known else []
            except (ValueError, AttributeError):
                reports = []                              # unreadable: start again with this report
            reports = [r for r in reports if isinstance(r, dict) and r.get('key') != entry.get('key')] + [entry]
            reports.sort(key=lambda r: (RANK.get(r.get('status'), 2), str(r.get('name') or r.get('key') or '').lower()))
            generated = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            logo = None
            if p['images'] and len(p['logo_size']) == 2 and p['logo_size'][0] > 0:
                uri = inline_images('cid:' + str(p['images'][0].get('cid')), p['images'][:1])
                logo = (uri, p['logo_size'][0], p['logo_size'][1]) if uri.startswith('data:') else None
            target.write(_json_name(index), json.dumps({'generated': generated, 'reports': reports}, indent=2,
                                                       sort_keys=True, default=str) + '\n', 'application/json; charset=utf-8')
            target.write(index, index_html(reports, index, p['header_text'], logo, generated), 'text/html; charset=utf-8')
            written += [_json_name(index), index]
    except Fail as e:
        module.fail_json(msg='The report website: %s' % e, **urls)
    if win:
        fd, script = tempfile.mkstemp(prefix='site_report_web_', suffix='.ps1')
        with os.fdopen(fd, 'wb') as f:
            f.write(windows_script(dest, target.files).encode('utf-8-sig'))   # PowerShell 5.1 reads it as UTF-8
        module.exit_json(changed=False, written=written, script=script, place=_place(where, page, win),
                         msg='Made, for %s: %s' % (win, ', '.join(written)), **urls)
    module.exit_json(changed=True, written=written, place=_place(where, page, win),
                     msg='Published: %s' % (urls['url'] or _place(where, page, win)), **urls)


if __name__ == '__main__':
    main()
