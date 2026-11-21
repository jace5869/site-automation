#!/usr/bin/env python3
"""A report website page with its deferred rows (1.100.0: the defer-main, defer-mini and defer-detail JSON blocks)
put back as the markup site-report.js builds from them (its unpack()), so a test greps the page as people see it.
Shapes and their attributes in order, a null attribute bare (hidden), text and values escaped; a -1 node (a hosted
file's link) cloned from the page's Files card as the script does - a link becomes class="dl" with the text
"Download NAME", anything else (a retired file's reason) copied as it is, empty when the card has no entry. Nested
blocks are unpacked too.
Usage: unpack_deferred.py PAGE.html   (the page, unpacked, on stdout)"""
import html
import json
import re
import sys

BLOCK = re.compile(r'<script type="application/json" class="defer-(?:main|mini|detail)"[^>]*>(.*?)</script>', re.S)


FILES = re.compile(r'<[a-z]+ [^>]*class="card sfiles".*?</section>', re.S)


def hosted(page, mark):
    """The element after <!--mark--> in the Files card, as site-report.js clones it ('' when there is none)."""
    card = FILES.search(page)
    m = card and re.search(r'<!--%s-->(<([a-z]+)\b([^>]*)>.*?</\2>)' % re.escape(mark), card.group(0), re.S)
    if not m:
        return ''
    if m.group(2) != 'a':
        return m.group(1)
    attrs = re.sub(r'\bclass="[^"]*"', 'class="dl"', m.group(3))
    name = re.search(r'\bdownload="([^"]*)"', attrs)
    return '<a%s>Download %s</a>' % (attrs, name.group(1) if name else '')


def build(n, shapes, page):
    if isinstance(n, str):
        return html.escape(n, quote=False)
    if n[0] == -1:
        return hosted(page, n[1])
    tag, attrs = shapes[n[0]]
    a = ''.join(' %s' % k if v is None else ' %s="%s"' % (k, html.escape(v, quote=True)) for k, v in attrs)
    return '<%s%s>%s</%s>' % (tag, a, ''.join(build(c, shapes, page) for c in n[1:]), tag)


def main():
    text = page = open(sys.argv[1], encoding='utf-8').read()

    def unpack(m):
        shapes, nodes = json.loads(m.group(1))
        return ''.join(build(n, shapes, page) for n in nodes)

    while BLOCK.search(text):
        text = BLOCK.sub(unpack, text)
    sys.stdout.write(text)


if __name__ == '__main__':
    main()
