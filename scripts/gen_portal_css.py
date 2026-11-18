#!/usr/bin/env python3
"""1.75.2: the site portal's stylesheet - roles/site_portal_stack/files/static/portal.css - made from portal.css.in and the
palettes (light and dark) of the three report looks, read from their templates (roles/site_email/templates/report_*.html.j2),
so the portal and the report website keep one set of colours. tests/test_site_portal.py checks the file is up to date.
  python scripts/gen_portal_css.py          (writes the file)
  python scripts/gen_portal_css.py --check  (exit 1 when it is out of date)"""
from __future__ import absolute_import, division, print_function

import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(REPO, "roles", "site_email", "templates", "report_%s.html.j2")
SRC = os.path.join(REPO, "roles", "site_portal_stack", "files", "portal.css.in")
OUT = os.path.join(REPO, "roles", "site_portal_stack", "files", "static", "portal.css")
LOOKS = ("atlas", "brief", "ops")


def palette(look):
    t = open(TPL % look).read()
    out = []
    for name in ("light", "dark"):
        m = re.search(r"\{%- set " + name + r" %\}(.*?)\{%- endset %\}", t, re.S)
        out.append(" ".join(m.group(1).split()))
    return out


def tokens():
    parts = []
    for look in LOOKS:
        light, dark = palette(look)
        sel = ':root[data-look="%s"]' % look
        parts.append("%s{%s}\n@media (prefers-color-scheme:dark){%s:not([data-theme=\"light\"]){%s}}\n"
                     "%s[data-theme=\"dark\"]{%s}\n%s:has(#theme-light:checked){%s}\n%s:has(#theme-dark:checked){%s}"
                     % (sel, light, sel, dark, sel, dark, sel, light, sel, dark))
    return "\n".join(parts)


def build():
    return open(SRC).read().replace("@@TOKENS@@", tokens())


if __name__ == "__main__":
    want = build()
    if "--check" in sys.argv:
        have = open(OUT).read() if os.path.exists(OUT) else ""
        if have != want:
            print("roles/site_portal_stack/files/static/portal.css is out of date: python scripts/gen_portal_css.py")
            sys.exit(1)
        print("ok: portal.css is up to date")
    else:
        with open(OUT, "w") as fh:
            fh.write(want)
        print("wrote %s (%d bytes)" % (OUT, len(want)))
