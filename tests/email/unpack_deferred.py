#!/usr/bin/env python3
"""A report website page with its deferred rows (1.100.0: the defer-main, defer-mini and defer-detail JSON blocks)
put back as the markup site-report.js builds from them (its unpack()), so a test greps the page as people see it.
Shapes and their attributes in order, a null attribute bare (hidden), text and values escaped; a -1 node (a hosted
file's link, cloned from the page by the script) left empty. Nested blocks are unpacked too.
Usage: unpack_deferred.py PAGE.html   (the page, unpacked, on stdout)"""
import html
import json
import re
import sys

BLOCK = re.compile(r'<script type="application/json" class="defer-(?:main|mini|detail)"[^>]*>(.*?)</script>', re.S)


def build(n, shapes):
    if isinstance(n, str):
        return html.escape(n, quote=False)
    if n[0] == -1:
        return ''
    tag, attrs = shapes[n[0]]
    a = ''.join(' %s' % k if v is None else ' %s="%s"' % (k, html.escape(v, quote=True)) for k, v in attrs)
    return '<%s%s>%s</%s>' % (tag, a, ''.join(build(c, shapes) for c in n[1:]), tag)


def unpack(m):
    shapes, nodes = json.loads(m.group(1))
    return ''.join(build(n, shapes) for n in nodes)


def main():
    text = open(sys.argv[1], encoding='utf-8').read()
    while BLOCK.search(text):
        text = BLOCK.sub(unpack, text)
    sys.stdout.write(text)


if __name__ == '__main__':
    main()
