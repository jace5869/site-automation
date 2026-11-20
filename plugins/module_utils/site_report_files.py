# -*- coding: utf-8 -*-
"""The files a report hands over on the report website (1.79.8, roles/site_email report_web_files; the site_report_web
module): each run's files in a folder beside its page, <page without .html>-files/, under names made safe (letters,
digits . _ -), of the allowed types and sizes; the page's Files line and its Download links; and the ledger of every
run's files in index.json ("files"), which keeps the website from filling up - a run's files go with its page, after
report_web_files_keep_days, when all of them pass report_web_files_total_max_mb (the oldest first, never a latest page's),
and when their page is gone. A page whose files were removed says so in place of its links. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import base64
import datetime
import html as htmllib
import os
import posixpath
import re

from ansible.module_utils.six.moves.urllib.parse import quote, unquote

SUFFIX = '-files'                       # a run's folder: its page's name without .html, and this
TYPES = ('.xml', '.docx', '.xlsx', '.csv', '.md', '.txt', '.pdf')
CTYPES = {'.xml': 'application/xml', '.csv': 'text/csv; charset=utf-8', '.md': 'text/markdown; charset=utf-8',
          '.txt': 'text/plain; charset=utf-8', '.pdf': 'application/pdf',
          '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
          '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}
MARK = '<!--site-report-files-->'       # where a look puts the Files line (atlas, brief, ops: under the header)
START, END = '<!--site-report-files:start-->', '<!--site-report-files:end-->'
# a Download link a report puts in its page (a text's file: the answer file) - made into the run's link here
LINK_RE = re.compile(r'<a class="dl" href="site-report-file:([^"]*)" download>.*?</a>', re.S)
MB = 1024 * 1024


def safe_file_name(name):
    """A file's name as it goes on the website: its last part, letters, digits, '.', '_' and '-' only (the rest '-'), no
    leading or trailing '.', '-' or '_', at most 100 characters (its type kept); '' when nothing is left."""
    base = str(name or '').replace('\\', '/').rsplit('/', 1)[-1]
    s = re.sub(r'-+\.', '.', re.sub(r'-{2,}', '-', re.sub(r'[^A-Za-z0-9._-]+', '-', base))).strip('.-_')
    if len(s) > 100:
        root, ext = os.path.splitext(s)
        s = root[:100 - len(ext)].rstrip('.-_') + ext
    return s


def folder_of(page):
    """The run's folder of files beside its page: a/2026-11-20_0605_job-7.html -> a/2026-11-20_0605_job-7-files."""
    return (page[:-5] if page.endswith('.html') else page) + SUFFIX


def ours(folder):
    """A folder this module made (a page's name without .html, and -files) - the only kind it ever removes."""
    return isinstance(folder, str) and folder.endswith(SUFFIX) and '\\' not in folder and ':' not in folder \
        and not any(x in ('', '.', '..') for x in folder.split('/'))


def human(n):
    """A size in words: 812 bytes, 14 KB, 3.2 MB."""
    n = max(0, int(n or 0))
    if n < 1024:
        return '%d bytes' % n
    if n < MB:
        return '%d KB' % ((n + 1023) // 1024)
    return '%.1f MB' % (n / float(MB))


def prepare(items, max_mb, types=TYPES, packed_only=False):
    """The files a run hands over -> (ok, refused). items: [{path, filename}] (a file on the AAP side) or
    [{filename, b64}] (one kept for the copy job). ok: [{name, size, ctype, path | data}], refused: [{name, why}] -
    a type not in types, a file not there, one over max_mb, or all of them together over max_mb (the later ones).
    packed_only (the copy job: what a kept report brings): only {filename, b64} - never a path on this machine."""
    allowed = set(str(t).strip().lower() if str(t).strip().startswith('.') else '.' + str(t).strip().lower()
                  for t in types or [] if str(t).strip())
    cap = max(0, int(max_mb)) * MB
    ok, refused, total, seen = [], [], 0, set()
    for it in items or []:
        if not isinstance(it, dict):
            continue
        name = safe_file_name(it.get('filename') or it.get('path') or '')
        ext = os.path.splitext(name)[1].lower()
        shown = name or str(it.get('filename') or '?')[:80]
        if not name or ext not in allowed:
            refused.append({'name': shown, 'why': 'its type (%s) is not one the website hosts (report_web_files_types)'
                            % (ext or 'none')})
            continue
        root, own, n = name[:-len(ext)], name[-len(ext):], 2
        while name.lower() in seen:                      # two files of one name: the second -2 (any case: a Windows folder)
            tail = '-%d%s' % (n, own)                    # within the 100 characters, the suffix and the type kept
            name, n = root[:100 - len(tail)].rstrip('.-_') + tail, n + 1
        if packed_only and 'b64' not in it:
            refused.append({'name': name, 'why': 'a kept report brings its files\' content, never a path - not hosted'})
            continue
        if 'b64' in it:
            try:
                data = base64.b64decode(str(it['b64']), validate=True)
            except (ValueError, TypeError):
                refused.append({'name': name, 'why': 'it could not be read back'})
                continue
            size, src = len(data), {'data': data}
        else:
            path = str(it.get('path') or '')
            if not path or not os.path.isfile(path):
                refused.append({'name': name, 'why': 'the job did not make it'})
                continue
            size, src = os.path.getsize(path), {'path': path}
        if size > cap:
            refused.append({'name': name, 'why': '%s, over report_web_files_max_mb (%d MB)' % (human(size), max_mb)})
            continue
        if total + size > cap:
            refused.append({'name': name, 'why': 'the run\'s files together are over report_web_files_max_mb (%d MB)' % max_mb})
            continue
        seen.add(name.lower())
        total += size
        ok.append(dict(src, name=name, size=size, ctype=CTYPES.get(ext, 'application/octet-stream')))
    return ok, refused


def read_bytes(f):
    """A prepared file's bytes."""
    if 'data' in f:
        return f['data']
    with open(f['path'], 'rb') as fh:
        return fh.read()


def _href(src, dst):
    return quote(posixpath.relpath(dst, posixpath.dirname(src) or '.'))


def mark_id(folder, name):
    """A hosted file's mark in a page: its run's folder and its name (a latest page's link to a newer run's file of the
    same name is another mark: striking an older run's files never touches it)."""
    return posixpath.basename(folder) + '/' + name


def _item(mid, inner):
    return '<!--sf:%s-->%s<!--/sf:%s-->' % (mid, inner, mid)


def gone_html(folder, name, why):
    """A file removed from the website, in place of its link: its name and why (ASCII only - the Windows script writes it)."""
    e = htmllib.escape
    return _item(mark_id(folder, name), '<span class="dl off">%s: %s</span>' % (e(name), e(why)))


def block_html(files, refused, page, folder, web_look=True):
    """The page's Files line: each file a plain link that saves it (download, no script) with its size; the ones not
    hosted with why. '' when the run hands over none."""
    if not files and not refused:
        return ''
    e = htmllib.escape
    items = ['<li>%s</li>' % _item(mark_id(folder, f['name']), '<a class="sf-a" href="%s" download="%s">%s</a> <span class="sf-z">%s</span>'
                                    % (_href(page, folder + '/' + f['name']), e(f['name']), e(f['name']), e(human(f['size']))))
             for f in files]
    items += ['<li><span class="dl off">%s is not on the website: %s</span></li>' % (e(r['name']), e(r['why'])) for r in refused]
    if web_look:
        body = ('<section class="card sfiles" aria-label="Files"><h2 class="sf-h">Files</h2><ul class="sf-l">%s</ul></section>'
                % ''.join(items))
    else:                                                # an email look on the website: its own inline style
        body = ('<div class="sfiles" style="max-width:760px;margin:12px auto;padding:10px 16px;background:#ffffff;border:1px solid '
                '#cfdcd7;border-radius:12px;font-family:\'Segoe UI\',Arial,sans-serif;font-size:14px;color:#0f1f1a;">'
                '<b>Files</b><ul style="margin:6px 0 0;padding-left:20px;">%s</ul></div>' % ''.join(items))
    return START + body + END


def put_files(html, files, refused, page, folder, web_look=True):
    """The page with its Files line (at MARK, else at the top of its body) and each Download link a report put in it
    (site-report-file:NAME) made into the link to this run's file - or why it is not on the website, or nothing when
    the run hands over no files."""
    block = block_html(files, refused, page, folder, web_look)
    if MARK in html:
        html = html.replace(MARK, block, 1)
    elif block:
        m = re.search(r'<body\b[^>]*>', html, re.I)
        html = (html[:m.end()] + block + html[m.end():]) if m else block + html
    have = dict((f['name'], f) for f in files)
    why = dict((r['name'], r['why']) for r in refused)
    e = htmllib.escape

    def link(m):
        name = safe_file_name(unquote(m.group(1)))
        if name in have:
            return _item(mark_id(folder, name), '<a class="dl" href="%s" download="%s">Download %s</a>' % (
                _href(page, folder + '/' + name), e(name), e(name)))
        if name in why:
            return '<span class="dl off">%s is not on the website: %s</span>' % (e(name), e(why[name]))
        return ''
    return LINK_RE.sub(link, html)


def strike(html, folder, names, why):
    """A page whose files (of the run folder folder) were removed: each one's link (the Files line, a Download link) says
    why instead - only that run's marks."""
    for n in names:
        q = re.escape(mark_id(folder, n))
        html = re.sub(r'<!--sf:%s-->.*?<!--/sf:%s-->' % (q, q), lambda _m, n=n: gone_html(folder, n, why), html, flags=re.S)
    return html


def ps_strike(folder, names, why):
    """strike() for the Windows script: [(regex, replacement)] for PowerShell's -creplace (Constrained Language Mode)."""
    return [('(?s)<!--sf:%s-->.*?<!--/sf:%s-->' % (re.escape(mark_id(folder, n)), re.escape(mark_id(folder, n))),
             gone_html(folder, n, why).replace('$', '$$')) for n in names]


def _page_ok(page):
    return (isinstance(page, str) and page.endswith('.html') and '\\' not in page and ':' not in page
            and not any(x in ('', '.', '..') for x in page.split('/')))


def ledger(doc):
    """index.json's ledger of hosted files: [{dir, page, latest, key, when, files: [{name, size}]}] - only well-formed
    ones: a page inside the folder, and dir that page's own folder of files (never another folder to clean up)."""
    out = []
    for x in (doc.get('files') if isinstance(doc, dict) else None) or []:
        if (isinstance(x, dict) and _page_ok(x.get('page')) and x.get('dir') == folder_of(x['page']) and ours(x.get('dir'))
                and isinstance(x.get('files'), list)):
            fl = [f for f in x['files'] if isinstance(f, dict) and safe_file_name(f.get('name')) == f.get('name')]
            out.append(dict(x, files=fl))
    return out


def size_of(entry):
    return sum(int(f.get('size') or 0) for f in entry.get('files') or [])


def _when(text):
    m = re.match(r'^\s*(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d)', str(text or ''))
    try:
        return datetime.datetime(*[int(x) for x in m.groups()], tzinfo=datetime.timezone.utc) if m else None
    except ValueError:
        return None


def plan(entries, latest_runs, new_bytes, now, keep_days, total_mb, page_gone, hold=()):
    """What goes from the ledger this run -> (removals [(entry, why)], kept entries, total bytes with the new files,
    room - False when the new files cannot fit even then). In order: a folder whose page is gone (deleted by hand: an
    orphan - page_gone(page)); files older than keep_days (0 = never), a latest page's too; then the oldest first while
    all of them and the new ones pass total_mb - never a latest page's (latest_runs). The pages of hold: counted, never
    removed (this publish)."""
    removals, kept = [], []
    for e in entries:
        if e.get('page') in hold:
            kept.append(e)
            continue
        if page_gone(e.get('page')):
            removals.append((e, 'orphan'))
            continue
        w = _when(e.get('when'))
        if keep_days > 0 and w and now - w > datetime.timedelta(days=keep_days):
            removals.append((e, 'age'))
            continue
        kept.append(e)
    cap = max(0, int(total_mb)) * MB
    total = sum(size_of(e) for e in kept) + new_bytes
    for e in sorted(kept, key=lambda x: str(x.get('when') or '')):
        if total <= cap:
            break
        if e.get('page') in latest_runs or e.get('page') in hold:
            continue
        removals.append((e, 'cap'))
        total -= size_of(e)
    kept = [e for e in kept if not any(e is r for r, _ in removals)]
    return removals, kept, total, total <= cap


def why_words(code, keep_days, total_mb):
    return {'age': 'removed after %d days (report_web_files_keep_days)' % keep_days,
            'cap': 'removed to keep all hosted files under %d MB (report_web_files_total_max_mb)' % total_mb,
            'orphan': 'removed: its page is gone',
            'run': 'removed with its page'}.get(code, 'removed')


def used_line(entries, total_mb, keep_days):
    """The index's and the job's line: the space the hosted files take, and the limits."""
    used = sum(size_of(e) for e in entries)
    n = len([e for e in entries if e.get('files')])
    return ('Hosted files: %s in %d run%s, of %d MB (report_web_files_total_max_mb); each run\'s kept %s.'
            % (human(used), n, '' if n == 1 else 's', total_mb,
               ('%d days (report_web_files_keep_days)' % keep_days) if keep_days > 0 else 'as long as its page'))
