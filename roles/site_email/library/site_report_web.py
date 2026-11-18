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
  - Each line also keeps a trend of its last 12 runs, pages or not (1.72.0) - when, status, job and the run's findings
    by level (counts - report.counts, else the rows' row_status) - and latest_url, its latest page's address. The index
    shows the change since the previous run and when a report did not run on time (twice its usual gap, at least 26
    hours; judged by site-report.js when the page is read).
  - A page being read just then (open in a browser, on a share) is written again a moment later, a few times.
  - dest is a folder (an absolute path), a Windows file share's folder (\\server\share\folder, or
    smb://server/share/folder) or an http(s):// address that accepts PUT. A login for the upload comes
    from the environment - REPORT_WEB_TOKEN (sent as a bearer token), or REPORT_WEB_USERNAME and
    REPORT_WEB_PASSWORD (basic) - as the "Report website" credential sets it, never from a parameter; a redirect is
    never followed (the login stays with that address) - the 3xx is a failure that names where it pointed. The share's
    login is REPORT_WEB_USERNAME (user@YOURDOMAIN.MIL - the realm in capitals - for Kerberos; DOMAIN\user for NTLM
    only) and REPORT_WEB_PASSWORD too, or a Kerberos ticket (smb_auth kerberos, no password). The share's folder must
    be there (it is never made); the folders inside it are.
  - In check mode nothing is written; with a share, the job still logs in and looks at the folder (read-only), so a
    dry run proves the login and the path.
  - The pictures of the page (src="cid:name", the logo) are put inside it (data:), so every page stands alone.
  - With windows_host, dest is a folder on that Windows server - nothing is written here, the files are put in a
    PowerShell script (returned as script, a temporary file) that writes them there; index_text is that
    server's index.json, read before. The script only uses what Constrained Language Mode allows.
  - Every name (files' page, latest and index, each of pages' page) must be a name inside dest - a path with / between
    its parts, ending in .html, no leading / or \, no drive or ':', no backslash, no empty, '.' or '..' part - or nothing
    is written (also with keep, and for windows_host's script).
  - index.json is read again just before it is written, and only this template's line is this run's (another job's
    line written in between stays); not with windows_host (the script writes the index read before).
  - Each line of the index keeps its runs' banner; the index carries the highest of them and this run's.
  - The template's latest page and its line in the index are only written over by a run at least as new as the one
    already there (its when) - a copy of an older run (the copy job) never takes the place of a newer one.
  - With keep, nothing is written and html is not needed - the report, its file names, address, index line and footer
    are returned packed (packed, at most max_kb) for the copy job; a report too big even with its longest tables cut
    returns packed empty, and msg says so.
  - In check mode nothing is written; the address is returned.
  - A login or publish that fails for a reason that passes (Kerberos "Message stream modified" while a server's machine
    password changes and AD replicates, no KDC reachable, a connection reset or timed out, an SMB session deleted or
    expired, HTTP 502/503/504, a stale mount) is tried again from the start - a new login, a new SMB session, a fresh
    Kerberos ticket - up to retries times, retry_delay seconds apart, each retry said in one warning. A real error (a
    wrong password, access denied, no such share or folder, the realm) is not retried.
  - With required false, a publish that still fails is not a failure - published false and error (why) are returned,
    with a warning; the role then says it in the email.
  - A page in the atlas, brief or ops look carries its script (roles/site_email/templates/site_report.js); it is written beside the
    folder's top as site-report.js too (when it changed), and the page and the index load it from there - a web server
    whose Content-Security-Policy allows no inline script (script-src 'self') runs that one.
options:
  dest: {description: The report folder (an absolute path) or the http(s) address it is uploaded to., type: str, required: true}
  url: {description: The address people open the folder at (default - dest when it is http(s))., type: str, default: ''}
  files: {description: "The names inside the folder - {page, latest, index} (report_web_files); '' = none.", type: dict, required: true}
  page_url: {description: "The page's address, as the report job printed it (the copy job; '' = url and the page's name).", type: str, default: ''}
  html: {description: The page (not with keep)., type: str, default: ''}
  images: {description: "Pictures in the page: [{cid: name, path: file}].", type: list, elements: dict, default: []}
  data: {description: "The report and its run (written as JSON beside the page); its banner ({on, text, bg, fg} - plugins/module_utils/site_banner.py) goes at the top and bottom of the page and the index.", type: dict, default: {}}
  entry: {description: "This template's line of the index: key (one per template), name, title, status, when, job (and step, the first Start here card of data's report - the brief index shows it).", type: dict, required: true}
  prune: {description: "Leave out of the index the lines whose latest page is no longer there (deleted by hand). Not with windows_host (the server's folder is not read here).", type: bool, default: true}
  drop: {description: "Leave out of the index the lines whose key matches one of these patterns (* and ?) - e.g. pages a report once listed there.", type: list, elements: str, default: []}
  pages: {description: "More pages written in the same session, beside the page and never on the index (1.14.0, the ticket insights' site, unit and team pages, many per call) - each {page, html, report, runs}: its HTML (images and the banner as the page's) and, with json, its data (data with its own report and runs).", type: list, elements: dict, default: []}
  sub_pages: {description: "The report's other pages this run wrote (ticket insights - a site's, directorate's, team's), kept on its index line; with delete_old, those an older run wrote and this one did not are deleted.", type: list, elements: str, default: []}
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
  theme: {description: "The website's look (report_web_theme) - atlas, brief and ops draw the index page in it too; the others, in the portal look.", type: str, default: portal}
  time_zone: {description: "The time zone the index page shows its times in (report_time_zone, 1.49.0; a name as timedatectl list-timezones prints them): 2026-11-13 05:37 CET. What is stored (when, the JSON) stays UTC. '' = UTC; a name not known here = UTC, with a warning.", type: str, default: ''}
  retries: {description: "Tries again after a failure that passes (report_web_retries); 0 = none.", type: int, default: 3}
  retry_delay: {description: Seconds between two tries (report_web_retry_delay)., type: int, default: 30}
  required: {description: "false = a publish that still fails after the retries returns published false and error, not a failure (report_web_required).", type: bool, default: true}
author: site automation
'''

RETURN = r'''
url: {description: The page's address ('' without url or an http dest)., type: str}
latest_url: {description: The template's latest page., type: str}
index_url: {description: The index of every report., type: str}
written: {description: "The files written (inside the folder; site-report.js when it changed).", type: list}
deleted: {description: "The pages (and JSON) of older runs deleted (with windows_host - to be deleted by the script).", type: list}
not_deleted: {description: "Older runs' files that could not be deleted: [[file, why]].", type: list}
place: {description: "Where the page is, without an address (/mnt/reports/..., web01:D:\\reports\\...).", type: str}
script: {description: "With windows_host - the PowerShell script that writes them there (a temporary file; delete it).", type: str}
index_text: {description: The index (index.json) as written - for the next page of the same run (the copy job)., type: str}
unlisted: {description: "The keys of the index lines left out this run (drop, or their latest page gone).", type: list}
newest: {description: This run is at least as new as the template's line in the index (its latest page was written)., type: bool}
packed: {description: "With keep - the report packed for the copy job ('' = too big, see msg)", type: str}
note: {description: "With keep - what was kept, its size (and the tables cut to fit).", type: str}
cut: {description: "With keep - the tables cut to fit max_kb: [[section, rows kept, rows]]", type: list}
published: {description: "The report is on the website (false only with required false, when it still failed).", type: bool}
error: {description: "Why it was not published (with published false).", type: str}
retried: {description: "The tries that failed and were tried again: [why, ...].", type: list}
'''

import base64
import datetime
import errno
import fnmatch
import hashlib
import html as htmllib
import json
import os
import posixpath
import re
import ssl
import tempfile
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_banner import COLOURS, OTHER, colour_of, ink, inject as banner_inject
from ansible.module_utils.site_report_page import KEEP_AT_LEAST, pack
from ansible.module_utils.site_time import local as local_time, zone as zone_of
from ansible.module_utils.six.moves.urllib.error import HTTPError, URLError
from ansible.module_utils.six.moves.urllib.parse import quote
from ansible.module_utils.six.moves.urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPSHandler

IMAGE_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif'}
RANK = {'critical': 0, 'warning': 1, 'unknown': 2, 'low': 3, 'info': 4, 'ok': 5}
PAL = {
    'ok': ('#13734f', '#e3f5ec', '#bfe6d3', 'Successful'),
    'warning': ('#9a5806', '#fdf2df', '#f3d6a6', 'Warning'),
    'critical': ('#b42318', '#fdeaea', '#f5c4c4', 'Critical'),
    'low': ('#88358e', '#f6e9f6', '#e6c6e8', 'Low'),            # 1.56.0: worth knowing, nothing to do now
    'info': ('#1d4ed8', '#e6effd', '#c3d5f7', 'Info'),
    'unknown': ('#4b5753', '#eef2f1', '#d5ddda', 'Unknown')}
IN_USE = (errno.EACCES, errno.EBUSY, errno.EPERM, errno.ETXTBSY)    # a mounted share's file open in a browser
RUN_KEYS = ('page', 'status', 'when', 'job', 'title', 'banner', 'counts')
COUNT_KEYS = ('critical', 'warning', 'low')     # a run's findings by level (1.72.0)
TREND_N = 12                                       # the runs a line's trend keeps, pages or not (1.72.0)
WAIT = (0.5, 1, 2, 3, 4)                                 # a file in use: written again after these seconds
SCRIPT = 'site-report.js'                                # the atlas, brief and ops pages' script, at the folder's top
WEB_LOOKS = ('atlas', 'brief', 'ops')                   # the looks made for a browser (never an email): pages already
SCRIPT_TAG = '<script src="site-report.js"></script>'    # the atlas page's link to it, made relative to each page
# a page's own script: <script id="site-report-js">...</script> (site-report.js), or with data-file="name.js" a file
# of its own at the folder's top (the ticket insights' site-insights.js), linked by <script src="name.js"></script>
INLINE_RE = re.compile(r'<script id="site-report-js"(?: data-file="([A-Za-z0-9_.-]+\.js)")?>\s*(.*?)\s*</script>', re.S)


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


class _NoRedirect(HTTPRedirectHandler):
    """Follows no redirect: the 3xx answer comes back as an HTTPError (urllib would send the Authorization header on)."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http(e):
    """An HTTP error in words: its code, the login hint (401, 403) or where a redirect pointed (never followed)."""
    if 300 <= e.code < 400:
        where = str((e.headers.get('Location') if e.headers is not None else '') or 'no Location')[:300]
        return ('HTTP %s %s - the address answers with a redirect to %s, which is not followed (the login is never sent on): '
                'set report_web_dest to the address that takes the upload itself' % (e.code, e.reason, where))
    return 'HTTP %s %s%s' % (e.code, e.reason, _login_hint(e.code))


def safe_name(rel, ends=('.html',)):
    """A file name inside the report folder: a relative path with / between its parts - no leading / or \\, no drive or
    ':', no backslash, no empty, '.' or '..' part - that ends in one of ends."""
    return (isinstance(rel, str) and rel.endswith(ends) and '\\' not in rel and ':' not in rel
            and not any(x in ('', '.', '..') for x in rel.split('/')))


WRITTEN = ('.html', '.json', '.js')                     # the files the module writes: pages, their JSON, the pages' script


class Target(object):
    """The report folder: a local path, or an http(s) address (GET to read, PUT to write, MKCOL for a folder)."""

    def __init__(self, dest, ca_path, timeout):
        self.dest = dest.rstrip('/')
        self.http = bool(re.match(r'(?i)https?://', dest))
        self.timeout = timeout
        self.auth = ''
        if self.http:
            ctx = ssl.create_default_context(cafile=ca_path or None)
            self.opener = build_opener(_NoRedirect(), HTTPSHandler(context=ctx))   # the login stays with this address
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
            raise Fail('reading %s/%s: %s' % (self.dest, rel, _http(e)))
        except URLError as e:
            raise Fail('reading %s/%s: %s' % (self.dest, rel, e.reason))

    def exists(self, rel):
        """Is the file there? An answer that is not a clear no (an error) counts as yes: a line is never dropped by mistake."""
        if not self.http:
            return os.path.exists(os.path.join(self.dest, *rel.split('/')))
        try:
            self._call('HEAD', rel).close()
            return True
        except HTTPError as e:
            if 300 <= e.code < 400:                      # a redirect is never followed: said, not taken for a yes
                raise Fail('looking for %s/%s: %s' % (self.dest, rel, _http(e)))
            return e.code not in (404, 410)
        except Exception:                               # noqa: BLE001
            return True

    def write(self, rel, text, ctype):
        body = text.encode('utf-8')
        if not self.http:
            path = os.path.join(self.dest, *rel.split('/'))
            folder = os.path.dirname(path)
            try:
                if os.path.getsize(path) == len(body):
                    with open(path, 'rb') as f:
                        if f.read() == body:         # the same page as there: not written again (0.97.0)
                            return
            except OSError:
                pass
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
                raise Fail('uploading %s/%s: %s' % (self.dest, rel, _http(e)))
            self._folders(rel)
            try:
                self._call('PUT', rel, body, ctype).read()
            except HTTPError as e2:
                raise Fail('uploading %s/%s: %s' % (self.dest, rel, _http(e2)))
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
                raise Fail(_http(e))
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
                    raise Fail('making the folder %s/%s: %s' % (self.dest, '/'.join(parts[:i]), _http(e)))


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

    def exists(self, rel):
        try:
            return bool(self.smb.path.exists(self._path(rel), **self.kw))
        except Exception:                               # noqa: BLE001 - not a clear no: the line stays
            return True

    def write(self, rel, text, ctype):
        path = self._path(rel)
        folder = path.rsplit('\\', 1)[0]
        body = text.encode('utf-8')
        try:
            if self.smb.stat(path, **self.kw).st_size == len(body):
                with self.smb.open_file(path, mode='rb', **self.kw) as f:
                    if f.read() == body:                 # the same page as there: not written again (0.97.0)
                        return
        except Exception:                               # noqa: BLE001 - not there, or not read: written
            pass
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


# A failure that passes (tried again from a new login) and one that does not (said at once): the words of the error.
# The ones that do not pass are looked at first - "Unspecified GSS failure" also comes with a wrong password.
NOT_PASSING = ('logon_failure', 'logon failure', 'status_logon', 'invalid credentials', 'preauthentication failed',
               'password has expired', 'password_expired', 'account_disabled', 'account_locked', 'account_restriction',
               'access_denied', 'access is denied', 'bad_network_name', '0xc00000cc', 'no such share', 'default realm',
               'server not found in kerberos database', 'cannot find kdc', 'clock skew', 'skew too great', 'md4',
               'unsupported hash', 'invalid token was supplied', 'revoked', 'is not there', 'must be', 'no login for',
               'no smbprotocol', 'certificate verify failed', 'http 400', 'http 401', 'http 403', 'http 404', 'http 405',
               'http 409', 'http 413', 'http 301', 'http 302', 'http 303', 'http 307', 'http 308')
PASSING = ('message stream modified', 'cannot contact any kdc', 'unable to reach any kdc', 'kdc unreachable',
           'unspecified gss failure', 'connection reset', 'connection refused', 'connection aborted', 'broken pipe',
           'timed out', 'timeout', 'network_name_deleted', '0xc00000c9', 'user_session_deleted', '0xc0000203',
           'network_session_expired', '0xc000035c', 'session expired', 'connection_disconnected', '0xc000020c',
           'io_timeout', '0xc00000b5', 'remote end closed', 'eof occurred', 'stale file handle', 'input/output error',
           'host is down', 'transport endpoint is not connected', 'no route to host', 'network is unreachable',
           'temporarily unavailable', 'try again', 'http 502', 'http 503', 'http 504')


def passing(why):
    """A failure that a moment later may not be there (tried again from the start), or None; else a real error."""
    low = str(why).lower()
    if any(w in low for w in NOT_PASSING):
        return None
    return next((w for w in PASSING if w in low), None)


class Staged(object):
    """A folder on a Windows server (windows_host): the files kept here, for the script that writes them there."""

    def __init__(self, index_json, index_text):
        self.index_json, self.index_text, self.files, self.removed = index_json, index_text, [], []

    def read(self, rel):
        if rel != self.index_json or not self.index_text:
            return None
        # Ansible may have read the text as JSON already
        return self.index_text if isinstance(self.index_text, str) else json.dumps(self.index_text)

    def exists(self, rel):
        return True                                      # the server's folder is not read here: every line stays

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
    for rel in [f[0] for f in files] + list(remove):     # only names inside dest go into the script (main checked them)
        if not safe_name(rel, WRITTEN):
            raise Fail('the file name %s is not a name inside report_web_dest - nothing was written' % rel)
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


def web_page(page, images, theme='portal'):
    """The email's HTML as a web page: its pictures inside it, its background filling the window (the atlas look is a
    web page already, and so is the brief look)."""
    page = inline_images(page, images)
    return page if theme in WEB_LOOKS else page.replace('</head>', WEB_STYLE + '\n</head>', 1)


def page_script(html):
    """The script a page carries (an atlas page: roles/site_email/templates/site_report.js, inline in it; a page with a
    script file of its own: data-file); '' for a page without."""
    m = INLINE_RE.search(html or '')
    return (m.group(2) + '\n') if m else ''


def page_script_name(html):
    """The file the page's script is written as at the folder's top: site-report.js, or the page's own (data-file)."""
    m = INLINE_RE.search(html or '')
    return (m.group(1) or SCRIPT) if m else SCRIPT


def script_tag(rel, js, name=SCRIPT):
    """The <script> that loads the script file (site-report.js, or the page's own) into the file rel (a page or the
    index), its version in the address - a browser that keeps a copy takes the new one."""
    return '<script src="%s?v=%s"></script>' % (_href(rel, name), hashlib.sha1(js.encode('utf-8')).hexdigest()[:10])


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
    return runs or ([_run(r)] if r.get('page') else [])


def _run(r):
    """A run as a line lists it. Its banner key ({} for none) only when the line had one: a run from before 1.24.0 has
    none, and keeps none (top_banner); its counts only when it had them (1.72.0)."""
    return dict((k, r.get(k)) for k in RUN_KEYS if k not in ('banner', 'counts') or k in r)


def run_counts(report):
    """1.72.0: a run's findings by level -> {critical, warning, low} or None (not counted). The report's own counts when
    it gives them (report.counts: {critical: n, warning: n, low: n}); else its tables' rows by row_status - a report that
    shows one finding in two tables counts it twice, so a report that does should give its own. No table with
    row_status (a report that only informs): None."""
    if not isinstance(report, dict):
        return None
    given = report.get('counts')
    if isinstance(given, dict) and any(k in given for k in COUNT_KEYS):
        try:
            return dict((k, max(int(given.get(k) or 0), 0)) for k in COUNT_KEYS)
        except (TypeError, ValueError):
            pass
    out, seen = dict((k, 0) for k in COUNT_KEYS), False
    for sec in report.get('sections') or []:
        rs = sec.get('row_status') if isinstance(sec, dict) else None
        if isinstance(rs, list):
            seen = True
            for st in rs:
                if st in out:
                    out[st] += 1
    return out if seen else None


def _trend_point(x):
    return dict((k, x.get(k)) for k in ('when', 'status', 'job', 'counts'))


def newest_first(items, this):
    """Runs newest first by when; this run (the one just added) first of its minute - two jobs in the same minute: the
    one that ran now is the newest. The runs a line lists (history) and its trend (1.72.0b) both sort with it."""
    return sorted(items, key=lambda x: (str(x.get('when') or ''), x is this), reverse=True)


def trend(old, entry, n=TREND_N):
    """1.72.0: a line's last n runs, newest first - when, status, job and counts - whatever happened to their pages
    (history and delete_old keep fewer). A line from before 1.72.0 starts from its runs. A run already in it (the same
    when and job: the copy job) is there once."""
    this = _trend_point(entry)
    past = (old or {}).get('trend')
    if not isinstance(past, list):
        past = [_trend_point(x) for x in _runs(old)] if old else []
    past = [x for x in past if isinstance(x, dict) and not (str(x.get('when')) == str(this['when']) and str(x.get('job')) == str(this['job']))]
    return newest_first(past + [this], this)[:max(1, int(n))]


def run_delta(line):
    """1.72.0: the newest run's counts against the run before it -> {critical, warning, low} (now minus before), or None
    when either was not counted. "Since the previous run", never "new findings": one fixed and one new read 0."""
    t = [x for x in (line or {}).get('trend') or [] if isinstance(x, dict)]
    if len(t) < 2 or not isinstance(t[0].get('counts'), dict) or not isinstance(t[1].get('counts'), dict):
        return None
    return dict((k, int(t[0]['counts'].get(k) or 0) - int(t[1]['counts'].get(k) or 0)) for k in COUNT_KEYS)


def _when_dt(text):
    """'2026-11-17 06:05 UTC' (or with seconds, or ISO) -> an aware datetime; None when it is not one."""
    m = re.match(r'^\s*(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d)(?::(\d\d))?(?:\.\d+)?\s*(?:Z|UTC)?\s*$', str(text or ''))
    if not m:
        return None
    try:
        return datetime.datetime(*[int(x or 0) for x in m.groups()], tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def due_hours(line, floor=26):
    """1.72.0: how long after its last run a report is late - twice its usual gap (the median between the runs its trend
    keeps), at least floor hours; None with fewer than two runs (no judgement). The page decides at view time
    (site-report.js): the index is written when a report runs, never when it is read."""
    ts = sorted([d for d in (_when_dt(x.get('when')) for x in (line or {}).get('trend') or [] if isinstance(x, dict)) if d], reverse=True)
    gaps = sorted((a - b).total_seconds() / 3600.0 for a, b in zip(ts, ts[1:]) if a > b)
    if not gaps:
        return None
    mid = gaps[len(gaps) // 2] if len(gaps) % 2 else (gaps[len(gaps) // 2 - 1] + gaps[len(gaps) // 2]) / 2.0
    return int(round(max(2 * mid, float(floor))))


def index_summary(reports):
    """1.72.0: the index's four tiles, as far as the page can know them when written: every report; not checked (its
    newest run could not check: status unknown); critical up since the previous run (with the counts); runs ok = the
    rest. Didn't run on time is counted by site-report.js at view time (due_hours) and taken off runs ok there."""
    out = {'total': len(reports or []), 'not_checked': [], 'critical_up': [], 'runs_ok': 0}
    for r in reports or []:
        name = str(r.get('name') or r.get('key') or '')
        if r.get('status') == 'unknown':
            out['not_checked'].append(name)
        d = run_delta(r)
        if d and d['critical'] > 0:
            t = r['trend']
            out['critical_up'].append({'name': name, 'key': r.get('key'), 'delta': d['critical'],
                                       'before': int(t[1]['counts'].get('critical') or 0), 'now': int(t[0]['counts'].get('critical') or 0)})
    out['runs_ok'] = out['total'] - len(out['not_checked'])
    return out


def from_before(reports):
    """A line, or a run it lists, written before 1.24.0 (no banner key): its marking is not known."""
    return any('banner' not in x for r in reports for x in [r] + _runs(r))


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


def index_html(reports, index, header_text, logo, generated, tabs=(), theme='portal', js='', tz=None, base=''):
    """The index page: a tab per area (All first), every report's line - worst first - opening to its last runs."""
    if theme in WEB_LOOKS:                               # 1.72.0: the board, in the look's colours
        return index_board(reports, index, header_text, logo, generated, tabs, js, tz, base, theme)
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
        run = e(local_time(r.get('when') or '', tz))
        if r.get('page'):
            run = '<a href="%s" style="color:#45534e;">%s</a>' % (_href(index, r['page']), run or 'this run')
        runs = _runs(r)
        lines = ''.join(
            '<tr><td style="padding:5px 12px 5px 0;">%s</td><td style="padding:5px 12px 5px 0;white-space:nowrap;">'
            '<a href="%s" style="color:#0f6b4f;font-weight:600;">%s</a>%s</td>'
            '<td style="padding:5px 12px 5px 0;white-space:nowrap;color:#45534e;font-size:13px;">%s</td>'
            '<td style="padding:5px 0;color:#5b6b66;font-size:13px;min-width:200px;">%s</td></tr>'
            % (_pill(x.get('status'), True), _href(index, x['page']), e(local_time(x.get('when'), tz) or 'this run'),
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
        'header': e(header_text), 'card': card, 'logo': logo_img, 'n': len(reports), 'generated': e(local_time(generated, tz)), 'tiles': tiles,
        'style': INDEX_STYLE % ''.join(rules),
        'tabs': (''.join(t[0] for t in tabs) + '\n<div class="bar">' + ''.join(t[1] for t in tabs) + '</div>\n') if reports else '',
        'th': 'font-size:12px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#45534e;',
        'rows': '\n'.join(rows) or '<div style="padding:12px;color:#5b6b66;">No reports yet.</div>'}


# ---- the index page's parts in every look made for a browser
# the light / auto / dark switch: radio buttons, so it works without the script too (a web server's
# Content-Security-Policy can block it); the script keeps the choice in this browser (the report pages' key)
ATLAS_SWITCH = (
    '<div class="seg" id="theme" role="radiogroup" aria-label="Light or dark">'
    '<input type="radio" name="site-report-theme" id="theme-light" value="light"><label for="theme-light" title="Light"><svg class="ic" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="3.2" '
    'fill="currentColor"/><path d="M8 1v1.8M8 13.2V15M1 8h1.8M13.2 8H15M3 3l1.3 1.3M11.7 11.7L13 13M3 13l1.3-1.3M11.7 4.3L13 3" stroke="currentColor" '
    'stroke-width="1.5" stroke-linecap="round"/></svg><span>Light</span></label>'
    '<input type="radio" name="site-report-theme" id="theme-auto" value="auto" checked><label for="theme-auto" title="As this device is set"><svg class="ic" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" '
    'cy="8" r="6.4" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M8 1.6a6.4 6.4 0 010 12.8z" fill="currentColor"/></svg><span>Auto</span></label>'
    '<input type="radio" name="site-report-theme" id="theme-dark" value="dark"><label for="theme-dark" title="Dark"><svg class="ic" viewBox="0 0 16 16" aria-hidden="true"><path d="M13.6 10.2A6 6 0 015.8 2.4a6 6 '
    '0 107.8 7.8z" fill="currentColor"/></svg><span>Dark</span></label></div>')
ATLAS_EARLY = ("<script>try{var t=localStorage.getItem('site-report-theme');if(t==='light'||t==='dark')"
               "document.documentElement.setAttribute('data-theme',t)}catch(e){}</script>")
ATLAS_SCRIPT = """<script>
(function(){
  if(window.siteReport)return;                     // site-report.js ran: it does this
  var root=document.documentElement,seg=document.getElementById('theme');if(!seg)return;
  var c=seg.querySelector('input[value="'+(root.getAttribute('data-theme')||'auto')+'"]');if(c)c.checked=true;
  seg.addEventListener('change',function(ev){var n=ev.target.value;
    if(n==='auto')root.removeAttribute('data-theme');else root.setAttribute('data-theme',n);
    try{if(n==='auto')localStorage.removeItem('site-report-theme');else localStorage.setItem('site-report-theme',n)}catch(e){}})
})();
</script>"""


def first_step(report):
    """The first step a report gives (its first Start here card), short, for its line of the index: the title on one
    line (at most 200 characters), the command only when it is short (3 lines and 300 characters at most - a long one,
    a STIG rule's whole check say, stays on the report's page: more is then true); None when it gives none."""
    for s in (report or {}).get('sections') or [] if isinstance(report, dict) else []:
        for c in (s.get('cards') or []) if isinstance(s, dict) else []:
            if isinstance(c, dict) and (c.get('title') or c.get('code')):
                title = ' '.join(str(c.get('title') or '').split())
                code = str(c.get('code') or '').strip('\n')
                short = len(code.splitlines()) <= 3 and len(code) <= 300
                return {'title': title if len(title) <= 200 else title[:199].rstrip() + '\u2026',
                        'code': code if short else '', 'more': bool(code) and not short, 'status': str(c.get('status') or '')}
    return None


def _plural(n, one, many):
    return '%d %s' % (n, one if n == 1 else many)


# ---- the index page (1.72.0): the same board in every look made for a browser (atlas, brief, ops), each in its own
# colours. Calm on purpose: red only for a run that could not check, a critical count up since the run before, or a
# report that did not run on time; the findings as counts in plain ink. At a glance (four tiles), Look at these first,
# a tab per area, a card per area, each report's line with a sparkline of its last 12 runs and its last runs' pages.
# Light, dark and the tabs work without the script; site-report.js judges "Didn't run on time" and gives Copy link.
BOARD_TOKENS = ('bg', 'bg2', 'card', 'card2', 'edge', 'line', 'ink', 'ink2', 'mute', 'accent', 'ok', 'ok-soft', 'warn', 'crit',
                'crit-fill', 'crit-soft', 'crit-edge', 'spark', 'spark-base')
# each look's colours (light, dark): its own surfaces and ink, the same status steps. Every text token is 4.5:1 or
# better on the card and the page (tests/test_report_web.py Release1720 checks it).
BOARD_LOOK = {
    'ops': ({'bg': '#eef1f6', 'bg2': '#e4e9f1', 'card': '#fbfcfe', 'card2': '#f4f6fa', 'edge': '#d8deea', 'line': '#e6eaf1',
             'ink': '#152033', 'ink2': '#36425a', 'mute': '#5a6680', 'accent': '#1b5cc0', 'ok': '#17773a', 'ok-soft': '#e5f2ea',
             'warn': '#b8860b', 'crit': '#b0261d', 'crit-fill': '#c62f25', 'crit-soft': '#fbeae8', 'crit-edge': '#f0c4bf',
             'spark': '#4f5b74', 'spark-base': '#d5dbe6'},
            {'bg': '#0e1424', 'bg2': '#0b1120', 'card': '#151d31', 'card2': '#111a2c', 'edge': '#26314a', 'line': '#1e2740',
             'ink': '#e9edf5', 'ink2': '#c9d1e2', 'mute': '#93a0b8', 'accent': '#8ab4f8', 'ok': '#5fcf8a', 'ok-soft': '#14301f',
             'warn': '#e3a33b', 'crit': '#f09a92', 'crit-fill': '#d03b3b', 'crit-soft': '#3a1717', 'crit-edge': '#6a2a2a',
             'spark': '#b4bed0', 'spark-base': '#2a3550'}),
    'atlas': ({'bg': '#f3f5f7', 'bg2': '#e9eef2', 'card': '#ffffff', 'card2': '#f7f9fb', 'edge': '#dde3ea', 'line': '#e8ecf1',
               'ink': '#111827', 'ink2': '#374151', 'mute': '#4b5563', 'accent': '#0b7352', 'ok': '#0a6b45', 'ok-soft': '#e6f5ed',
               'warn': '#b8860b', 'crit': '#b42318', 'crit-fill': '#c62f25', 'crit-soft': '#fdeceb', 'crit-edge': '#f0a59c',
               'spark': '#4b5563', 'spark-base': '#d3dae3'},
              {'bg': '#0b0f14', 'bg2': '#0e141b', 'card': '#121820', 'card2': '#171f29', 'edge': '#263243', 'line': '#1e2834',
               'ink': '#e6edf3', 'ink2': '#c2ccd6', 'mute': '#9aa7b4', 'accent': '#3ecf98', 'ok': '#4fd69a', 'ok-soft': '#12302a',
               'warn': '#e3a33b', 'crit': '#ff8a80', 'crit-fill': '#d03b3b', 'crit-soft': '#341a1a', 'crit-edge': '#6a2c2a',
               'spark': '#a8b3bf', 'spark-base': '#263243'}),
    'brief': ({'bg': '#f4f8f6', 'bg2': '#eef2f0', 'card': '#fbfdfc', 'card2': '#f1f4f3', 'edge': '#e0e5e3', 'line': '#eaeeec',
               'ink': '#182027', 'ink2': '#363e45', 'mute': '#545c63', 'accent': '#00704e', 'ok': '#0e6538', 'ok-soft': '#e4efe9',
               'warn': '#b8860b', 'crit': '#a82823', 'crit-fill': '#c62f25', 'crit-soft': '#f8eceb', 'crit-edge': '#e8c2bf',
               'spark': '#545c63', 'spark-base': '#d6dcd9'},
              {'bg': '#121212', 'bg2': '#161616', 'card': '#1e1e1e', 'card2': '#242424', 'edge': '#2e2e2e', 'line': '#272727',
               'ink': '#e0e0e0', 'ink2': '#bdbdbd', 'mute': '#a3a3a3', 'accent': '#7fcbac', 'ok': '#87c79c', 'ok-soft': '#1f2b24',
               'warn': '#dcb671', 'crit': '#f0968f', 'crit-fill': '#d03b3b', 'crit-soft': '#331d1c', 'crit-edge': '#5c3532',
               'spark': '#b0b0b0', 'spark-base': '#333333'})}
BOARD_STYLE = """:root{@LIGHT@}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){@DARK@}}
:root[data-theme="dark"]{@DARK@}
:root:has(#theme-light:checked){@LIGHT@}
:root:has(#theme-dark:checked){@DARK@}
*{box-sizing:border-box}
[hidden]{display:none!important}
html{-webkit-text-size-adjust:100%;background:var(--bg)}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 "Segoe UI Variable Text","Segoe UI",system-ui,-apple-system,"DejaVu Sans",Arial,sans-serif;
font-variant-numeric:tabular-nums}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
a:focus-visible,button:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:6px}
svg{display:block}
.ic{width:14px;height:14px;flex:none;display:block}
.vh{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.mutec{color:var(--mute)}.small{font-size:12px}
.top{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--edge)}
.top .in{max-width:1240px;margin:0 auto;padding:12px 20px;display:flex;align-items:center;gap:14px}
.mark{width:40px;height:40px;border-radius:10px;background:var(--ink);color:var(--card);display:grid;place-items:center;font-weight:800;letter-spacing:.02em;font-size:14px;flex:none}
.logo{display:block;border-radius:8px;flex:none;max-height:44px;width:auto}
.top h1{margin:0;font:700 clamp(18px,1.5vw,21px)/1.15 "Segoe UI Variable Display","Segoe UI",system-ui,sans-serif;letter-spacing:-.01em}
.top .site{color:var(--mute);font-size:12.5px}
.top .grow{flex:1}
.top .upd{color:var(--mute);font-size:12.5px;text-align:right;line-height:1.35}
.top .upd b{color:var(--ink2);font-weight:600}
.seg{display:inline-flex;position:relative;background:var(--card);border:1px solid var(--edge);border-radius:999px;padding:2px;gap:2px}
.seg input{position:absolute;opacity:0;width:1px;height:1px;margin:0;pointer-events:none}
.seg label{font-size:12.5px;font-weight:600;color:var(--mute);border-radius:999px;padding:4px 10px;cursor:pointer;display:inline-flex;align-items:center;gap:6px;user-select:none}
.seg label:hover{color:var(--ink)}
.seg input:checked+label{background:var(--card2);color:var(--ink);box-shadow:inset 0 0 0 1px var(--edge)}
.seg input:focus-visible+label,.tabs label:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
main{max-width:1240px;margin:0 auto;padding:18px 20px 40px}
.hero{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));background:var(--card);border:1px solid var(--edge);border-radius:14px;overflow:hidden}
.tile{padding:14px 18px 16px;display:flex;flex-direction:column;gap:4px;min-width:0;border-left:1px solid var(--line);position:relative}
.tile:first-child{border-left:0}
.tlab{font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--mute);display:flex;gap:6px;align-items:center}
.tv{display:flex;align-items:baseline;gap:6px}
.big{font:700 clamp(28px,2.6vw,36px)/1.05 "Segoe UI Variable Display","Segoe UI",system-ui,sans-serif;letter-spacing:-.02em}
.of{color:var(--mute);font-size:13px}
.td{color:var(--mute);font-size:12.5px;overflow-wrap:anywhere}
.t-good .tlab .ic{color:var(--ok)}
.t-bad::before{content:"";position:absolute;left:0;right:0;top:0;height:3px;background:var(--crit-fill)}
.t-bad .tlab,.t-bad .big,.t-bad .tlab .ic{color:var(--crit)}
.t-bad .td{color:var(--ink2)}
.totals{margin:10px 2px 0;color:var(--mute);font-size:12.5px;display:flex;flex-wrap:wrap;gap:4px 14px;align-items:center}
.totals b{color:var(--ink);font-weight:600}
.look{margin-top:18px;background:var(--card);border:1px solid var(--crit-edge);border-radius:14px;padding:12px 16px 6px}
.look h2,.area h2{margin:0;font-size:11.5px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:var(--mute)}
.look h2{color:var(--crit)}
.look ul{list-style:none;margin:6px 0 0;padding:0}
.look li{display:grid;grid-template-columns:22px minmax(150px,220px) minmax(0,1fr) auto;gap:10px;align-items:center;padding:9px 0}
.look li:not([hidden])~li:not([hidden]){border-top:1px solid var(--line)}
.look li>a:not(.go){font-weight:600}
.lw{color:var(--ink2);font-size:13px;overflow-wrap:anywhere}
.go{font-size:12.5px;font-weight:600;white-space:nowrap}
.tab{position:absolute;opacity:0;pointer-events:none}
.tabs{margin:22px 0 12px;display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.tabs label{font-size:13px;font-weight:600;color:var(--ink2);border:1px solid var(--edge);border-radius:999px;padding:6px 12px;cursor:pointer;display:flex;gap:6px;align-items:center}
.tabs label:hover{background:var(--card)}
.tn{font-weight:400;opacity:.75}
.tdot{width:7px;height:7px;border-radius:50%;background:var(--crit-fill);display:inline-block;box-shadow:0 0 0 2px var(--bg)}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
.area{background:var(--card);border:1px solid var(--edge);border-radius:14px;padding:12px 16px 6px;min-width:0}
.span2{grid-column:span 2}
.span2 .rows{columns:2;column-gap:28px;column-rule:1px solid var(--line)}
.span2 .rr{break-inside:avoid}
.area header{display:flex;justify-content:space-between;align-items:center;gap:10px;padding-bottom:6px}
.area h2{color:var(--ink);font-size:12px}
.area h2 .n{color:var(--mute);font-weight:400;letter-spacing:0;margin-left:4px}
.ah{display:flex;gap:5px;align-items:center;font-size:12.5px;font-weight:600}
.ah.good{color:var(--ok)}.ah.bad{color:var(--crit)}.ah b{font-weight:600}
.rr{border-top:1px solid var(--line)}
.rr>summary{list-style:none;cursor:pointer;display:grid;grid-template-columns:22px minmax(0,1fr) auto;gap:10px;align-items:center;padding:10px 0}
.rr>summary::-webkit-details-marker{display:none}
.si{width:20px;height:20px;border-radius:6px;display:grid;place-items:center}
.si-ok{color:var(--ok);background:var(--ok-soft)}
.si-info{color:var(--mute);background:var(--card2)}
.si-notchecked,.si-late,.si-up{color:#fff;background:var(--crit-fill)}
.rr[data-late] .si-norm,.rr:not([data-late]) .si-late{display:none}
.rt{display:flex;flex-direction:column;min-width:0;gap:1px}
.rh{display:flex;flex-wrap:wrap;align-items:center;gap:2px 10px}
.rn{font-weight:650;color:var(--ink)}
.rr>summary:hover .rn{text-decoration:underline}
.lcopy{font:inherit;font-size:11.5px;font-weight:600;color:var(--accent);background:transparent;border:1px solid var(--edge);border-radius:999px;padding:0 8px;line-height:18px;cursor:pointer}
.lcopy:hover{background:var(--card2)}
.lcopy.done{color:var(--ok);border-color:var(--ok)}
.rs{color:var(--ink2);font-size:13px;overflow-wrap:anywhere}
.rm{display:flex;flex-wrap:wrap;gap:2px 12px;font-size:12.5px;color:var(--ink2);align-items:center}
.when{color:var(--mute)}
.ct{display:inline-flex;align-items:center;gap:5px}
.sh{display:inline-block;width:8px;height:8px}
.sh-c{background:var(--crit-fill);transform:rotate(45deg);width:7px;height:7px}
.sh-w{width:0;height:0;border-left:5px solid transparent;border-right:5px solid transparent;border-bottom:8px solid var(--warn)}
.flag{display:inline-flex;align-items:center;gap:4px;font-weight:700;color:var(--crit);font-size:12px}
.flag .ic{width:11px;height:11px}
.calm{font-size:12px;color:var(--ink2);font-weight:600}
.sp{display:flex;align-items:center;gap:6px}
.sp path{fill:none;stroke:var(--spark);stroke-width:1.6;stroke-linejoin:round;stroke-linecap:round}
.sp .base{stroke:var(--spark-base);stroke-width:1}
.sp .end{fill:var(--ink);stroke:var(--card);stroke-width:1.5}
.sp .lone{fill:var(--spark)}
.sp .miss{stroke:var(--crit-fill);stroke-width:1.5;stroke-dasharray:2 2}
.rr.up .sp .end{fill:var(--crit-fill)}
.spv{min-width:18px;text-align:right;font-size:13px;color:var(--ink)}
.nos{font-size:12px;color:var(--mute);width:136px;text-align:right}
.runs{padding:0 0 12px 32px}
.runs table{border-collapse:collapse;width:100%;font-size:12.5px}
.runs th{text-align:left;font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute);font-weight:700;padding:4px 8px 4px 0;border-bottom:1px solid var(--line)}
.runs td{padding:6px 8px 6px 0;border-bottom:1px solid var(--line);vertical-align:top}
.runs td.nw{white-space:nowrap}
.runs td.wr{overflow-wrap:anywhere}
.st-i{display:inline-flex;align-items:center;gap:4px}
.st-i .ic{width:12px;height:12px}
.st-ok{color:var(--ok)}.st-info{color:var(--mute)}.st-notchecked{color:var(--crit)}
.st-w{color:var(--ink2)}
.none{padding:14px 2px;color:var(--mute)}
.foot{margin-top:22px;color:var(--mute);font-size:12px;display:flex;flex-wrap:wrap;gap:4px 16px;justify-content:space-between}
@media (max-width:980px){.grid{grid-template-columns:minmax(0,1fr)}.span2{grid-column:auto}
.hero{grid-template-columns:repeat(2,minmax(0,1fr))}.tile:nth-child(3){border-left:0}.tile:nth-child(n+3){border-top:1px solid var(--line)}}
@media (max-width:860px){.span2 .rows{columns:1}.seg label span{display:none}}
@media (prefers-reduced-motion:no-preference){.rr[open]>.runs{animation:board-in .25s cubic-bezier(.16,1,.3,1)}
@keyframes board-in{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:none}}}
@media print{.top{position:static}.seg,.tabs,.lcopy{display:none!important}html,body{background:#fff}}
@TABS@"""
BOARD_ICON = {   # each state its own shape and word, never colour alone
    'ok': '<svg class="ic" viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" stroke-width="2" '
          'stroke-linecap="round" stroke-linejoin="round"/></svg>',
    'info': '<svg class="ic" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="2.5" fill="currentColor"/></svg>',
    'notchecked': '<svg class="ic" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor" '
                  'stroke-width="2" stroke-linecap="round"/></svg>',
    'late': '<svg class="ic" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" stroke-width="1.6"/>'
            '<path d="M8 5v3.2l2 1.4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
    'up': '<svg class="ic" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3l5 8H3z" fill="currentColor"/></svg>'}
BOARD_WORD = {'ok': 'Ran', 'info': 'Ran, report only', 'notchecked': 'Not checked', 'late': "Didn't run on time"}
SPARK_W, SPARK_H, SPARK_PAD, SPARK_TOP = 112, 20, 4, 4      # a line's sparkline: drawn in 112 x 20 (0 at the bottom), 4 at least on top


def _state(status):
    """A run's state on the index: Ran (ok, warning, critical, low), Ran, report only (info), Not checked (unknown)."""
    return 'notchecked' if status not in PAL or status == 'unknown' else ('info' if status == 'info' else 'ok')


def _tokens(look, dark):
    t = BOARD_LOOK.get(look) or BOARD_LOOK['ops']
    return ''.join('--%s:%s;' % (k, t[1 if dark else 0][k]) for k in BOARD_TOKENS) + 'color-scheme:%s;' % ('dark' if dark else 'light')


def spark(line, keys=('critical', 'warning'), floor=SPARK_TOP):
    """1.72.0: a line's trend as a sparkline, oldest first: vals (the runs' findings of keys added; None for a run not
    counted), max, and the SVG path and points - drawn as report_dash_filters.dash_path draws the dashboard's weekly line
    (a gap at None) in SPARK_W x SPARK_H, the top at least floor (tests/test_report_web.py holds the two to the same
    points). None when no run was counted."""
    keys = (keys,) if isinstance(keys, str) else tuple(keys)
    t = [x for x in (line or {}).get('trend') or [] if isinstance(x, dict)][::-1]
    vals = [sum(int(x['counts'].get(k) or 0) for k in keys) if isinstance(x.get('counts'), dict) else None for x in t]
    have = [v for v in vals if v is not None]
    if not have:
        return None
    top = max([floor] + have)
    n, w, h, pad = len(vals), SPARK_W, SPARK_H, SPARK_PAD
    pts = [(round(pad + (w - 2 * pad) * (i / float(max(1, n - 1))), 1), round(h - (h * v / float(top)), 1)) if v is not None else None
           for i, v in enumerate(vals)]
    d, pen = [], False
    for q in pts:
        if q is None:
            pen = False
            continue
        d.append(('L' if pen else 'M') + '%s %s' % q)
        pen = True
    return {'vals': vals, 'max': max(have), 'top': top, 'd': ' '.join(d), 'pts': pts}


def _spark_svg(r):
    """A line's sparkline: its critical and warning findings over its last runs, the newest right; its last count
    beside it ("-": the last run was not counted). Report only: no line."""
    sp = spark(r)
    if not sp:
        return '<span class="nos">%s</span>' % ('report only' if r.get('status') == 'info' else 'no counts yet')
    e = htmllib.escape
    vals, pts = sp['vals'], sp['pts']
    n = len(vals)
    label = 'Critical and warning findings, last %s, oldest first: %s' % (
        _plural(n, 'run', 'runs'), ', '.join('-' if v is None else str(v) for v in vals))
    lone = ''.join('<circle class="lone" cx="%s" cy="%s" r="1.6"/>' % p for i, p in enumerate(pts)
                   if p and (i == 0 or pts[i - 1] is None) and (i == n - 1 or pts[i + 1] is None) and i != n - 1)
    end = ('<circle class="end" cx="%s" cy="%s" r="3"/>' % pts[-1]) if pts[-1] else ''
    x = round(SPARK_W - SPARK_PAD, 1)
    miss = '' if pts[-1] else '<line class="miss" x1="%s" y1="0" x2="%s" y2="%d"/>' % (x, x, SPARK_H)
    return ('<span class="sp"><svg viewBox="0 0 %d %d" width="%d" height="%d" role="img" aria-label="%s"><title>%s</title>'
            '<g transform="translate(0 4)"><line class="base" x1="%d" y1="%d" x2="%d" y2="%d"/><path d="%s"/>%s%s%s</g></svg>'
            '<b class="spv">%s</b></span>'
            % (SPARK_W, SPARK_H + 8, SPARK_W, SPARK_H + 8, e(label), e(label), SPARK_PAD, SPARK_H, SPARK_W - SPARK_PAD, SPARK_H,
               sp['d'], lone, end, miss, '-' if vals[-1] is None else vals[-1]))


def _short(when, generated, tz):
    """A run's time in a line: the time alone on the index's own day, else the day and the time (the zone in its title)."""
    full = local_time(when or '', tz)
    m = re.match(r'^(\d{4})-(\d\d)-(\d\d) (\d\d:\d\d)', full)
    if not m:
        return full
    day = local_time(generated or '', tz)[:10]
    if full[:10] == day:
        return m.group(4)
    return '%d %s %s' % (int(m.group(3)), ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')[int(m.group(2)) - 1],
                         m.group(4))


def _counts_html(c, plain=False):
    """A run's findings as counts in plain ink, each level its own small shape: 3 critical, 4 warning, 1 low."""
    if not isinstance(c, dict):
        return ''
    c = dict((k, int(c.get(k) or 0)) for k in COUNT_KEYS)
    if plain:
        return ' &middot; '.join('%d %s' % (c[k], k) for k in COUNT_KEYS if c[k]) or 'no findings'
    parts = []
    if c['critical']:
        parts.append('<span class="ct"><i class="sh sh-c" aria-hidden="true"></i>%d critical</span>' % c['critical'])
    if c['warning']:
        parts.append('<span class="ct"><i class="sh sh-w" aria-hidden="true"></i>%d warning</span>' % c['warning'])
    if c['low']:
        parts.append('<span class="ct mutec">%d low</span>' % c['low'])
    return ''.join(parts) or '<span class="ct mutec">no findings</span>'


def _place_areas(areas, size):
    """Which areas take the whole row (span 2): five reports or more, and an area left alone in its row."""
    wide, pending = set(), None
    for a in areas:
        if size[a] >= 5:
            if pending is not None:
                wide.add(pending)
            pending = None
            wide.add(a)
        elif pending is None:
            pending = a
        else:
            pending = None
    if pending is not None:
        wide.add(pending)
    return wide


def index_board(reports, index, header_text, logo, generated, tabs=(), js='', tz=None, base='', look='atlas'):
    """1.72.0: the index page in the look made for a browser (atlas, brief, ops): At a glance (Runs ok, Not checked,
    Critical up since last run, Didn't run on time), the findings of the newest runs, Look at these first, a tab per
    area (radio buttons, no script), a card per area with each report's line (its state, what it said, the change since
    the previous run, its counts, a sparkline of its last runs, Copy link) opening to its last runs' pages."""
    e = htmllib.escape
    s = index_summary(reports)
    up = dict((x['key'], x) for x in s['critical_up'])
    of = [area(r.get('name') or r.get('key'), tabs) for r in reports]
    order = [str(t.get('tab')) for t in tabs or []]               # report_web_tabs' order, then A to Z, Other last
    areas = sorted(set(of), key=lambda a: (a == 'Other', order.index(a) if a in order else len(order), a.lower()))
    tab = dict((a, i + 1) for i, a in enumerate(areas))
    size = dict((a, of.count(a)) for a in areas)
    wide = _place_areas(areas, size)
    rows = dict((a, []) for a in areas)
    look_li, late_li = [], []
    tot = dict((k, 0) for k in COUNT_KEYS)
    lid = 0
    for r, a in zip(reports, of):
        lid += 1
        rid = 'r%d' % lid
        name = str(r.get('name') or r.get('key') or '')
        latest = r.get('latest') or r.get('page') or ''
        href = _href(index, latest) if latest else ''
        st = _state(r.get('status'))
        u = up.get(r.get('key')) if r.get('key') is not None else None
        d = run_delta(r)
        c = r.get('counts') if isinstance(r.get('counts'), dict) else None
        if c:
            for k in COUNT_KEYS:
                tot[k] += int(c.get(k) or 0)
        w, due = _when_dt(r.get('when')), due_hours(r)
        hook = ((' data-when="%s"' % w.strftime('%Y-%m-%dT%H:%M:%SZ')) + (' data-due="%d"' % due if due else '')) if w else ''
        bad = bool(u) or st == 'notchecked'
        cls = 'rr a%d%s%s%s' % (tab[a], ' bad' if bad else '', ' up' if u else '', ' nc' if st == 'notchecked' else '')
        icon = 'up' if u else st
        if u:
            chip = '<span class="flag">%sSince the previous run: +%d critical</span>' % (BOARD_ICON['up'], u['delta'])
        elif st == 'notchecked':
            chip = '<span class="flag">%sNot checked</span>' % BOARD_ICON['notchecked']
        elif d and d['critical'] < 0:
            chip = '<span class="calm">Since the previous run: &minus;%d critical</span>' % -d['critical']
        else:
            chip = ''
        late = ('<span class="flag late-slot" hidden>%s%s</span>' % (BOARD_ICON['late'], BOARD_WORD['late'])) if hook and due else ''
        url = str(r.get('latest_url') or '') or (_join(base, r['latest']) if base and r.get('latest') else '')
        copy = ''
        if url.startswith(('http://', 'https://')):
            copy = '<button class="tcopy lcopy" type="button" data-copy="%s" title="Copy the address of its latest page" hidden>Copy link</button>' % e(url)
        elif href:
            copy = ('<button class="tcopy lcopy" type="button" data-copy="%s" data-rel="" title="Copy the address of its latest page" hidden>'
                    'Copy link</button>' % e(href))
        when_s = _short(r.get('when'), generated, tz)
        runs = _runs(r)
        lines = ''.join(
            '<tr><td class="nw"><a href="%s" title="%s">%s</a>%s</td><td class="nw"><span class="st-i st-%s">%s</span> <span class="st-w">%s</span></td>'
            '<td class="nw">%s</td><td class="wr">%s</td></tr>'
            % (_href(index, x['page']), ' - '.join(t for t in (e(local_time(x.get('when') or '', tz)), _job(x.get('job'))) if t),
               e(_short(x.get('when'), generated, tz) or 'this run'),
               ' <span class="mutec">(newest)</span>' if not i else '', _state(x.get('status')), BOARD_ICON[_state(x.get('status'))],
               BOARD_WORD[_state(x.get('status'))], _counts_html(x.get('counts'), True) if isinstance(x.get('counts'), dict) else '-',
               e(x.get('title') or '')) for i, x in enumerate(runs))
        table = ('<div class="runs"><table><thead><tr><th>Run</th><th>State</th><th>Findings</th><th>What it said</th></tr></thead><tbody>%s'
                 '</tbody></table><p class="mutec small">The last %s keep their pages (report_web_history).</p></div>'
                 % (lines, _plural(len(runs), 'run', 'runs')) if runs else '<div class="runs"><p class="mutec small">No page of its runs listed.</p></div>')
        rows[a].append((not bad, name.lower(), (
            '<details class="%s" id="%s" data-name="%s"%s><summary><span class="si si-%s si-norm" title="%s">%s<span class="vh">%s</span></span>'
            '<span class="si si-late" title="%s">%s<span class="vh">%s</span></span>'
            '<span class="rt"><span class="rh">%s%s</span><span class="rs">%s</span><span class="rm">%s%s%s<span class="when" title="%s">%s</span>%s</span></span>'
            '%s</summary>%s</details>'
            % (cls, rid, e(name), hook, icon, e(BOARD_WORD[st]), BOARD_ICON[icon], e(BOARD_WORD[st]), e(BOARD_WORD['late']), BOARD_ICON['late'],
               e(BOARD_WORD['late']), ('<a class="rn" href="%s">%s</a>' % (href, e(name))) if href else '<span class="rn">%s</span>' % e(name),
               copy, e(r.get('title') or ''), chip, late, _counts_html(c) if c else '', e(local_time(r.get('when') or '', tz)), e(when_s),
               '' if r.get('page') or not _job(r.get('job')) else ' <span class="when">%s</span>' % _job(r.get('job')),
               _spark_svg(r), table))))
        go = ('<a class="go" href="%s">Open &rsaquo;</a>' % href) if href else '<span></span>'
        nm = ('<a href="%s">%s</a>' % (href, e(name))) if href else '<span>%s</span>' % e(name)
        if u:
            look_li.append('<li><span class="si si-up">%s</span>%s<span class="lw">Since the previous run: +%d critical (%d &rarr; %d). %s</span>%s</li>'
                           % (BOARD_ICON['up'], nm, u['delta'], u['before'], u['now'], e(r.get('title') or ''), go))
        elif st == 'notchecked':
            look_li.append('<li><span class="si si-notchecked">%s</span>%s<span class="lw">Not checked. %s</span>%s</li>'
                           % (BOARD_ICON['notchecked'], nm, e(r.get('title') or ''), go))
        if hook and due:
            late_li.append('<li data-late-li="%s" hidden><span class="si si-late">%s</span>%s<span class="lw">Didn&#39;t run on time: last ran %s, '
                           'usually every %s.</span>%s</li>' % (rid, BOARD_ICON['late'], nm, e(local_time(r.get('when') or '', tz)),
                                                              _plural(max(1, int(round(due / 2.0))), 'hour', 'hours'), go))
    # the areas' cards, report_web_tabs' order; in each, the reports that need a look first, then A to Z
    cards = []
    for a in areas:
        mine = sorted(rows[a], key=lambda x: (x[0], x[1]))
        nbad = sum(1 for x in mine if not x[0])
        head = ('<span class="ah bad"%s>%s<span><b class="ahn">%d</b> <span class="ahv">%s</span> a look</span></span>'
                '<span class="ah good"%s>%sAll %d ran</span>' % ('' if nbad else ' hidden', BOARD_ICON['up'], nbad, 'needs' if nbad == 1 else 'need',
                                                                 ' hidden' if nbad else '', BOARD_ICON['ok'], len(mine)))
        cards.append('<section class="area a%d%s" data-tab="%d" aria-labelledby="h-a%d"><header><h2 id="h-a%d">%s <span class="n">%d</span></h2>%s</header>'
                     '<div class="rows">%s</div></section>' % (tab[a], ' span2' if a in wide else '', tab[a], tab[a], tab[a], e(a), len(mine), head,
                                                               ''.join(x[2] for x in mine)))
    # the tabs: All, then the areas; a dot on an area with a report that needs a look (the script adds one for a late report)
    inputs, labels, rules = [], [], []
    for i, label in enumerate(['All'] + areas):
        n = len(reports) if not i else size[label]
        dot = ''
        if i:
            hot = any((up.get(r.get('key')) or _state(r.get('status')) == 'notchecked') for r, x in zip(reports, of) if x == label)
            dot = '<i class="tdot"%s></i><span class="vh"%s> (needs a look)</span>' % (('', '') if hot else (' hidden', ' hidden'))
        inputs.append('<input type="radio" name="tab" id="t%d" class="tab"%s>' % (i, '' if i else ' checked'))
        labels.append('<label for="t%d">%s<span class="tn">%d</span>%s</label>' % (i, e(label), n, dot))
        rules.append('#t%d:checked~.tabs label[for=t%d]{background:var(--ink);border-color:var(--ink);color:var(--card)}\n'
                     '#t%d:checked~.tabs label[for=t%d] .tdot{box-shadow:0 0 0 2px var(--ink)}\n'
                     '#t%d:focus-visible~.tabs label[for=t%d]{outline:2px solid var(--accent);outline-offset:2px}\n' % ((i,) * 6))
        if i:
            rules.append('#t%d:checked~.grid .area:not(.a%d){display:none}\n#t%d:checked~.grid .area.a%d{grid-column:1 / -1}\n' % (i, i, i, i))
    nc = s['not_checked']
    ups = s['critical_up']

    def more(names):
        return '' if len(names) < 2 else ' and %d more' % (len(names) - 1)
    tiles = (
        '<div class="tile t-good"><span class="tlab">%sRuns ok</span><span class="tv"><b class="big" data-tile="ok" data-n="%d">%d</b>'
        '<span class="of">of %d</span></span><span class="td">ran on time and checked everything</span></div>'
        % (BOARD_ICON['ok'], s['runs_ok'], s['runs_ok'], s['total']) +
        '<div class="tile %s"><span class="tlab">%sNot checked</span><span class="tv"><b class="big">%d</b></span><span class="td">%s</span></div>'
        % ('t-bad' if nc else 't-good', BOARD_ICON['notchecked'], len(nc),
           ('%s: the last run could not check%s' % (e(nc[0]), more(nc))) if nc else 'every run could check') +
        '<div class="tile %s"><span class="tlab">%sCritical up since last run</span><span class="tv"><b class="big">%d</b><span class="of">%s</span></span>'
        '<span class="td">%s</span></div>'
        % ('t-bad' if ups else 't-good', BOARD_ICON['up'], len(ups), 'report' if len(ups) == 1 else 'reports',
           ('%s: %d &rarr; %d critical%s' % (e(ups[0]['name']), ups[0]['before'], ups[0]['now'], more(ups))) if ups else 'no critical count went up') +
        '<div class="tile t-good" data-tile-box="late"><span class="tlab">%sDidn&#39;t run on time</span><span class="tv"><b class="big" data-tile="late">0</b>'
        '</span><span class="td" data-tile-note="late">every report ran in its usual time</span></div>' % BOARD_ICON['late'])
    totals = ('<p class="totals"><span>Findings in the newest runs:</span><span class="ct"><i class="sh sh-c" aria-hidden="true"></i><b>%d</b> critical</span>'
              '<span class="ct"><i class="sh sh-w" aria-hidden="true"></i><b>%d</b> warning</span><span><b>%d</b> low</span>'
              '<span>&middot; each report&#39;s own page has them, worst first</span></p>' % (tot['critical'], tot['warning'], tot['low'])
              if any(isinstance(r.get('counts'), dict) for r in reports) else '')
    lookfirst = ('<section class="look" aria-labelledby="look-h"%s><h2 id="look-h">Look at these first</h2><ul>%s%s</ul></section>'
                 % ('' if look_li else ' hidden', ''.join(look_li), ''.join(late_li))) if look_li or late_li else ''
    newest = max(reports, key=lambda r: str(r.get('when') or '')) if reports else None
    words = [w for w in re.split(r'\W+', str(header_text or '')) if w][:2]
    mark = (('<img class="logo" src="%s" width="%d" height="%d" alt="">' % (logo[0], logo[1], logo[2])) if logo
            else '<div class="mark" aria-hidden="true">%s</div>' % e(''.join(w[0] for w in words).upper() or 'R'))
    style = (BOARD_STYLE.replace('@LIGHT@', _tokens(look, False)).replace('@DARK@', _tokens(look, True)).replace('@TABS@', ''.join(rules)))
    body = ((''.join(inputs) + '<nav class="tabs" aria-label="Areas">' + ''.join(labels) + '</nav>\n<div class="grid">\n' + '\n'.join(cards) + '\n</div>\n')
            if reports else '<div class="none">No reports yet. A report job writes its page here when it runs.</div>\n')
    return ('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n<meta name="color-scheme" content="light dark">\n'
            '<title>' + e(header_text) + ' - reports</title>\n' + ATLAS_EARLY + ('\n' + script_tag(index, js) if js else '') +
            '\n<style>\n' + style + '</style>\n</head>\n<body class="board look-' + e(look) + '">\n'
            '<div class="top"><div class="in">' + mark + '<div><h1>' + e(header_text) + '</h1><div class="site">' +
            _plural(len(reports), 'report', 'reports') + ' in ' + _plural(len(areas), 'area', 'areas') + '</div></div><div class="grow"></div>'
            '<div class="upd">Updated <b>' + e(local_time(generated, tz)) + '</b>' +
            ('<br>newest run: ' + e(str(newest.get('name') or newest.get('key') or '')) if newest else '') + '</div>' + ATLAS_SWITCH + '</div></div>\n'
            '<main>\n<section class="hero" aria-label="At a glance">' + tiles + '</section>\n' + totals + '\n' + lookfirst + '\n' + body +
            '<div class="foot"><span>The sparkline: each report&#39;s critical and warning findings over its last 12 runs, newest right. A gap: not counted.</span>'
            '<span>Light, dark and the area tabs work without the page&#39;s script.</span></div>\n</main>\n' + ATLAS_SCRIPT + '\n</body>\n</html>\n')


def history(old, entry, n):
    """This template's line of the index with this run in its runs (newest first, n of them), and the runs that fell
    off it. A run of the copy job older than the line's own (old) leaves the line as it is, its runs but for this."""
    this = _run(entry)
    runs = [x for x in (_runs(old) if old else []) if x.get('page') != this['page']]
    runs = newest_first(runs + [this], this)               # this run first of its minute
    line = dict(entry if newer(old, entry) else old)
    line['runs'] = runs[:max(1, n)]
    line['trend'] = trend(old, entry)                    # 1.72.0: the last 12 runs' counts, whatever their pages
    return line, [x for x in runs[max(1, n):] if x is not this]          # this run's page, just written, stays


def deletable(rel, kept):
    """An older run's page (or its JSON) that may be deleted: inside the folder, a page, not one still listed."""
    return safe_name(rel, ('.html', '.json')) and rel not in kept


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


HEX6 = re.compile(r'^#[0-9a-fA-F]{6}$')


def _mark(b):
    """A banner as an index line keeps it - {on, text, bg, fg}, its colours checked - or {} for none."""
    if not isinstance(b, dict) or not b.get('on') or not str(b.get('text') or '').strip():
        return {}
    text = re.sub(r'\s+', ' ', str(b['text'])).strip()[:200]
    bg = str(b.get('bg') or '')
    bg = bg if HEX6.match(bg) else (colour_of(text) or OTHER)
    fg = str(b.get('fg') or '')
    return {'on': True, 'text': text, 'bg': bg, 'fg': fg if HEX6.match(fg) else ink(bg)}


def _level(b):
    """How high a banner's marking is: 0 none, then UNCLASSIFIED, CUI, CONFIDENTIAL, SECRET, TOP SECRET. A text of no
    known classification counts as SECRET (the default text)."""
    m = _mark(b)
    if not m:
        return 0
    colours = [c for _, c in COLOURS]
    c = colour_of(m['text'])
    return len(colours) - colours.index(c) if c else len(colours) - 1


def top_banner(this, reports, floor=None):
    """The index's banner: the highest marking among its lines and their runs (this run's own when it is as high). A line
    from before 1.24.0 has no banner key: while one is listed, the banner the index carried before (floor) stays the
    lowest it may have - an upgraded site keeps its marking until every older line and run has left the index (1.24.0c:
    a run too - a classified template that runs again unclassified still lists its older run)."""
    best = _mark(this)
    cands = [b for r in reports for b in [r.get('banner')] + [x.get('banner') for x in _runs(r)]]
    if floor and from_before(reports):
        cands.append(floor)
    for b in cands:
        if _level(b) > _level(best):
            best = _mark(b)
    return best


BANNER_ROW = re.compile(r'class="site-banner site-banner-top"[^>]*>\s*<tr><td[^>]*\bbgcolor="(#[0-9a-fA-F]{6})"[^>]*>([^<]*)</td>')


def index_banner(json_text, html_text):
    """The banner the index carried when it was written: index.json's own (1.24.0b on), else the top banner row of an
    index.html from before - {} for none."""
    try:
        doc = json.loads(json_text) if json_text else {}
    except ValueError:
        doc = {}
    if isinstance(doc, dict) and 'banner' in doc:
        return _mark(doc.get('banner'))
    m = BANNER_ROW.search(html_text or '')
    return _mark({'on': True, 'text': htmllib.unescape(m.group(2)), 'bg': m.group(1)}) if m else {}


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
            theme=dict(type='str', default='portal'),
            smb_auth=dict(type='str', default='negotiate'),
            smb_encrypt=dict(type='bool', default=False),
            smb_port=dict(type='int', default=445),
            prune=dict(type='bool', default=True),
            drop=dict(type='list', elements='str', default=[]),
            sub_pages=dict(type='list', elements='str', default=[]),
            pages=dict(type='list', elements='dict', default=[]),
            time_zone=dict(type='str', default=''),
            retries=dict(type='int', default=3),
            retry_delay=dict(type='int', default=30),
            required=dict(type='bool', default=True),
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
    if not page and not p['pages']:                    # pages alone (1.14.0): only those, no page of its own, no index
        module.fail_json(msg='no page name (report_web_page)')
    # every name stays inside report_web_dest - also one a kept report brings to the copy job: refused before anything
    bad = [n for n in [page, latest, index] + [sp.get('page') for sp in p['pages'] if isinstance(sp, dict)]
           if n and not safe_name(n)]
    if bad or not all(isinstance(sp, dict) for sp in p['pages']):
        why = ('%s: not a file name inside report_web_dest (a path below the folder with / between its parts, ending in .html - '
               'no leading / or \\, no drive or \':\', no backslash, no \'.\' or \'..\' part); nothing was written'
               % ', '.join(repr(str(n)) for n in bad or ['pages']))
        if p['required']:
            module.fail_json(msg='The report website: %s' % why, **urls)
        module.warn('The report website: not published (%s) - the job goes on (report_web_required: false)' % why)
        module.exit_json(changed=False, published=False, error=why, retried=[], written=[], **urls)
    if p['keep']:
        keep(module, p, page, latest, index, urls, where, bs)
    # the index page's times in report_time_zone (1.49.0) - only as they are shown: when, the JSON and the order of the
    # runs stay UTC. A name not known here: UTC, said once
    tz, tz_note = zone_of(p['time_zone'])
    if tz_note and index:
        module.warn('The report website: %s' % tz_note)
    if module.check_mode and not smb:
        module.exit_json(changed=False, written=[], msg='Dry run (Check): the report would be published at %s'
                         % (urls['url'] or _place(where, page or str((p['pages'][0] or {}).get('page') or ''), bs)), **urls)
    box = {}

    def publish():
        if win:
            target = Staged(_json_name(index) if index else '', p['index_text'])
        elif smb:
            target = SmbTarget(dest, p['smb_auth'], p['smb_encrypt'], p['smb_port'], p['timeout'])
            if module.check_mode:                        # logged in and the folder is there: that is all a dry run does
                module.exit_json(changed=False, written=[], msg='Dry run (Check): logged in to %s, the folder %s is there; the report '
                                 'would be published at %s' % (target.server, target.root, urls['url'] or _place(where, page, True)), **urls)
        else:
            target = Target(dest, p['ca_path'], p['timeout'])
        box['target'] = target
        banner = (p['data'] or {}).get('banner') or {}      # the classification banner (1.3.0): the page and the index
        html = banner_inject(web_page(p['html'], p['images'], p['theme']), banner)
        js = page_script(html)
        js_name = page_script_name(html)                 # site-report.js, or the page's own file (data-file)
        js_tag = '<script src="%s"></script>' % js_name
        written = []
        entry = dict(p['entry'], page=page, latest=latest)
        entry.pop('banner', None)                        # this run's banner, never one the entry brings
        # kept on its line and run, an empty one too (1.24.0b/c: a line or run with the key is from 1.24.0 on): the index carries the
        # highest
        entry['banner'] = _mark(banner)
        if p['sub_pages']:
            entry['sub'] = sorted(set(str(x) for x in p['sub_pages'] if x))
        step = first_step((p['data'] or {}).get('report'))     # the brief index's first step for this report (0.92.0)
        if step:
            entry['step'] = step
        counts = run_counts((p['data'] or {}).get('report'))   # 1.72.0: its findings by level, for the trend
        if counts is not None:
            entry['counts'] = counts
        if urls['latest_url'].startswith(('http://', 'https://')):
            entry['latest_url'] = urls['latest_url']           # 1.72.0: Copy link on the index
        known = target.read(_json_name(index)) if index else None
        reports = _reports(known)
        old = next((r for r in reports if r.get('key') == entry.get('key')), None)
        is_newest = newer(old, entry)
        busy = []                                        # latest and the index, open somewhere all that time: next run

        def put(rel, text, ctype, soft=False):
            if not safe_name(rel, WRITTEN):              # checked above; never written outside the folder
                raise Fail('%s is not a file name inside report_web_dest - not written' % rel)
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
            put(rel, html.replace(js_tag, script_tag(rel, js, js_name), 1) if js else html, 'text/html; charset=utf-8', rel == latest)
            if p['json']:
                doc = dict(p['data'], page=page, url=urls['url'], latest=latest, index=index)
                put(_json_name(rel), json.dumps(doc, indent=2, sort_keys=True, default=str) + '\n',
                    'application/json; charset=utf-8', rel == latest)
        scripts = {}
        for sp in p['pages']:                            # more pages, the same way (1.14.0): never on the index
            rel = str(sp.get('page') or '')
            if not rel:
                continue
            h = banner_inject(web_page(str(sp.get('html') or ''), p['images'], p['theme']), banner)
            sjs, sname = page_script(h), page_script_name(h)
            if sjs and not (js and sname == js_name):
                scripts[sname] = sjs
            put(rel, h.replace('<script src="%s"></script>' % sname, script_tag(rel, sjs, sname), 1) if sjs else h, 'text/html; charset=utf-8')
            if p['json']:
                doc = dict(p['data'], page=rel, url=_join(base, rel), latest='', index='')
                doc.update(dict((k, sp[k]) for k in ('report', 'runs') if k in sp))
                put(_json_name(rel), json.dumps(doc, indent=2, sort_keys=True, default=str) + '\n', 'application/json; charset=utf-8')
        for sname, sjs in sorted(scripts.items()):       # the more pages' script, when the page above did not carry it
            try:
                there = target.read(sname)
            except Fail:
                there = None
            if there != sjs:
                put(sname, sjs, 'text/javascript; charset=utf-8', True)
        if js:                                           # the pages' script, where they load it from - when it changed
            try:
                there = target.read(js_name)
            except Fail:
                there = None
            if there != js:
                put(js_name, js, 'text/javascript; charset=utf-8', True)
        index_js = js if js_name == SCRIPT else ''      # the index loads the atlas pages' script, never a page's own
        if js and js_name != SCRIPT:
            try:
                index_js = target.read(SCRIPT) or ''
            except Fail:
                index_js = ''
        index_text = ''
        deleted, not_deleted, gone = [], [], []
        if index:
            line, dropped = history(old, entry, p['history'])
            reports = [r for r in reports if r.get('key') != entry.get('key')]
            # lines a report asks to leave out (drop: its key patterns), and lines whose latest page is gone (prune:
            # its folder deleted by hand) - only the line goes, never a file
            gone = [r for r in reports if any(fnmatch.fnmatchcase(str(r.get('key') or ''), d) for d in p['drop'])]
            reports = [r for r in reports if r not in gone]
            if p['prune']:
                lost = [r for r in reports if r.get('latest') and not target.exists(r['latest'])]
                gone += lost
                reports = [r for r in reports if r not in lost]
            now = target.read(_json_name(index)) if not win else known
            if now and now != known and _reports(now):
                # another job wrote the index since it was read above (before the pages, the script and the HEADs): its
                # lines as they are now, only this template's line this run's, the lines left out above still out (1.24.0)
                out = set(str(r.get('key')) for r in gone)
                reports = [r for r in _reports(now) if r.get('key') != entry.get('key') and str(r.get('key')) not in out
                           and not any(fnmatch.fnmatchcase(str(r.get('key') or ''), d) for d in p['drop'])]
            reports.append(line)
            reports.sort(key=lambda r: (RANK.get(r.get('status'), 2), str(r.get('name') or r.get('key') or '').lower()))
            generated = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            logo = None
            if p['images'] and len(p['logo_size']) == 2 and p['logo_size'][0] > 0:
                uri = inline_images('cid:' + str(p['images'][0].get('cid')), p['images'][:1])
                logo = (uri, p['logo_size'][0], p['logo_size'][1]) if uri.startswith('data:') else None
            floor = {}
            if from_before(reports):     # lines or runs from before 1.24.0: the banner the index carried stays
                last = now or known
                page_before = ''
                if not win and not (last and '"banner"' in last):
                    try:
                        page_before = target.read(index) or ''
                    except Fail:
                        page_before = ''
                floor = index_banner(last, page_before)
            top = top_banner(banner, reports, floor)
            index_text = json.dumps({'generated': generated, 'reports': reports, 'banner': top}, indent=2, sort_keys=True,
                                    default=str) + '\n'
            put(_json_name(index), index_text, 'application/json; charset=utf-8')
            put(index, banner_inject(index_html(reports, index, p['header_text'], logo, generated, p['tabs'], p['theme'], index_js, tz, base),
                                     top),
                'text/html; charset=utf-8', True)
            # the pages of the runs that fell off this template's line - after the index, which no longer lists them
            kept = set([index, _json_name(index)])
            for r in reports:
                for rel in [r.get('latest'), r.get('page')] + [x.get('page') for x in _runs(r)]:
                    if rel:
                        kept.update([rel, _json_name(rel)])
            # only with a latest page: the line is then one template's (report_web_latest is per template - without it,
            # templates whose pages start with the same folder, {site}/{template}/..., would share a line)
            kept.update(entry.get('sub') or [])
            # the other pages an older run wrote and this one did not (a team gone, the split switched off) - 0.97.0
            gone_sub = sorted(set((old or {}).get('sub') or []) - set(entry.get('sub') or [])) if p['sub_pages'] else []
            for rel in (gone_sub if p['delete_old'] and latest else []):
                if deletable(rel, kept):
                    try:
                        target.remove(rel)
                        deleted.append(rel)
                    except Fail as e:
                        not_deleted.append([rel, str(e)])
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
        return dict(target=target, written=written, index_text=index_text, is_newest=is_newest, gone=gone,
                    deleted=deleted, not_deleted=not_deleted, busy=busy, js_name=js_name)

    # a failure that passes (Kerberos "Message stream modified", a KDC or the server not reached, a session gone) is
    # tried again from the start: a new login, a new SMB session, a fresh Kerberos ticket (report_web_retries)
    retried, tries = [], max(0, p['retries']) + 1
    fresh_ticket = bool(smb and os.environ.get('REPORT_WEB_USERNAME') and os.environ.get('REPORT_WEB_PASSWORD'))
    for n in range(1, tries + 1):
        if fresh_ticket and n > 1:                       # the password's own ticket, never one cached before
            os.environ['KRB5CCNAME'] = 'MEMORY:site-report-web-%d-%d' % (os.getpid(), n)
        try:
            done = publish()
            break
        except Fail as e:
            why = passing(e)
            if n == tries or not why or win:
                if p['required']:
                    module.fail_json(msg='The report website: %s%s' % (e, (' (tried %d times: %s)' % (n, '; '.join(retried + [why or str(e)])))
                                                                      if retried else ''), retried=retried, **urls)
                module.warn('The report website: not published (%s)%s - the job goes on (report_web_required: false)' % (
                    e, (' after %d tries' % n) if retried else ''))
                module.exit_json(changed=False, published=False, error=str(e), retried=retried, written=[], **urls)
            retried.append(why)
            module.warn('The report website: try %d of %d failed (%s): %s - trying again in %d s, a new login' % (
                n, tries, why, str(e)[:300], p['retry_delay']))
        finally:
            target = box.pop('target', None)
            if isinstance(target, SmbTarget):
                target.close()
            elif smb:                                    # a login that failed half-way: its connection dropped too
                try:
                    import smbclient
                    smbclient.reset_connection_cache()
                except Exception:                       # noqa: BLE001
                    pass
        time.sleep(max(0, p['retry_delay']))
    target, written, index_text, is_newest = done['target'], done['written'], done['index_text'], done['is_newest']
    gone, deleted, not_deleted, busy, js_name = done['gone'], done['deleted'], done['not_deleted'], done['busy'], done['js_name']
    if win:
        fd, script = tempfile.mkstemp(prefix='site_report_web_', suffix='.ps1')
        with os.fdopen(fd, 'wb') as f:
            f.write(windows_script(dest, target.files, target.removed, [latest, _json_name(latest) if latest else '', index, SCRIPT, js_name]).encode('utf-8-sig'))   # PowerShell 5.1 reads it as UTF-8
        module.exit_json(changed=False, written=written, script=script, place=_place(where, page, bs), index_text=index_text,
                         unlisted=[r.get('key') for r in gone],
                         newest=is_newest, deleted=deleted, not_deleted=not_deleted, not_written=busy, published=True, retried=retried,
                         msg='Made, for %s: %s' % (win, ', '.join(written)), **urls)
    module.exit_json(changed=True, written=written, place=_place(where, page, bs), index_text=index_text, newest=is_newest,
                     unlisted=[r.get('key') for r in gone],
                     deleted=deleted, not_deleted=not_deleted, not_written=busy, published=True, retried=retried,
                     msg='Published%s: %s%s' % (' (after %d tries)' % (len(retried) + 1) if retried else '',
                         urls['url'] or _place(where, page, bs),
                         ' (older runs deleted: %s)' % ', '.join(d for d in deleted if d.endswith('.html')) if deleted else ''), **urls)


if __name__ == '__main__':
    main()
