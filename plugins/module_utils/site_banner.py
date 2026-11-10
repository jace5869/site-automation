# -*- coding: utf-8 -*-
"""The classification banner of every report (1.3.0, docs/ENCLAVES.md): on or off (report_banner: auto = on in a
classified enclave - site_enclave, or a target host under site_enclave_classified_domains), its text
(report_banner_text) and colours (report_banner_color, else the standard colour of the text's classification; the
text black or white, whichever reads 4.5:1 or better). Where it goes: the top and bottom of an HTML page or email
(inject), the first and last line of a plain-text email (wrap_text). Shared by roles/site_email (site_report_web) and
plugins/filter/site_filters.py (which loads this file). Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import html as htmllib
import re

# the standard banner colours, by the classification the text starts with (the longest first)
COLOURS = (
    ("TOP SECRET", "#ff8c00"),
    ("SECRET", "#c8102e"),
    ("CONFIDENTIAL", "#0033a0"),
    ("CONTROLLED UNCLASSIFIED", "#502b85"),
    ("CUI", "#502b85"),
    ("UNCLASSIFIED", "#007a33"),
)
OTHER = "#4d4d4d"                     # a text of no known classification, without report_banner_color
INKS = ("#ffffff", "#111111")
ENCLAVES = ("classified", "unclassified")
MARK = 'class="site-banner'
HEX = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")


def _lum(hexc):
    h = hexc.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    out = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255.0
        out.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]


def contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def colour_of(text):
    """The standard banner colour of the text's classification ('' when it names none)."""
    t = re.sub(r"\s+", " ", str(text or "")).strip().upper()
    for name, c in COLOURS:
        if t == name or t.startswith(name + "/") or t.startswith(name + " ") or t.startswith(name + "("):
            return c
    return ""


def ink(bg):
    """The text colour on bg: white or near-black, whichever has the higher contrast (4.5:1 or better on every standard colour)."""
    return max(INKS, key=lambda c: contrast(c, bg))


def _bool(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "yes", "on", "1", "y")


def _domains(v):
    if isinstance(v, str):
        v = v.replace(",", " ").split()
    return [str(d).strip().lower().strip(".") for d in (v or []) if str(d).strip().strip(".")]


def enclave_of(enclave, domains, hosts):
    """(enclave, why): site_enclave when set (classified or unclassified - it always wins), else classified when a host's
    name or address (hosts: [[name, address]]) ends in one of domains (site_enclave_classified_domains), else unclassified."""
    e = str(enclave or "").strip().lower()
    if e:
        if e not in ENCLAVES:
            raise ValueError("site_enclave must be classified, unclassified or empty (found from the hosts), not %s" % enclave)
        return e, "site_enclave: %s" % e
    ds = _domains(domains)
    for h in hosts or []:
        for name in (h if isinstance(h, (list, tuple)) else [h]):
            n = str(name or "").strip().lower().rstrip(".")
            for d in ds:
                if n and (n == d or n.endswith("." + d)):
                    return "classified", "%s is under %s (site_enclave_classified_domains)" % (n, d)
    return "unclassified", ("no target host under site_enclave_classified_domains" if ds else "site_enclave is not set")


def banner(opts):
    """opts: {banner (auto | true | false), text, color, subject, enclave, domains, hosts} -> {on, text, bg, fg, subject,
    enclave, why}; on false: no banner anywhere."""
    enclave, why = enclave_of(opts.get("enclave"), opts.get("domains"), opts.get("hosts"))
    mode = opts.get("banner", "auto")
    auto = mode is None or str(mode).strip().lower() in ("", "auto")
    on = enclave == "classified" if auto else _bool(mode)
    text = re.sub(r"\s+", " ", str(opts.get("text") or "")).strip() or "SECRET"
    want = str(opts.get("color") or "").strip()
    if want and not HEX.match(want):
        raise ValueError("report_banner_color must be a colour as #rrggbb (or empty: the standard colour of the text), not %s" % want)
    bg = ("#" + want.lstrip("#").lower()) if want else (colour_of(text) or OTHER)
    if len(bg) == 4:
        bg = "#" + "".join(c * 2 for c in bg[1:])
    return {"on": bool(on), "text": text, "bg": bg, "fg": ink(bg), "subject": bool(on) and _bool(opts.get("subject", True)),
            "enclave": enclave, "why": why if auto else "report_banner: %s" % ("true" if on else "false")}


def html(b, where="top"):
    """The banner as one full-width row: a table (Outlook draws it too), its colours inline, kept in print."""
    return ('<table role="presentation" class="site-banner site-banner-%s" width="100%%" cellpadding="0" cellspacing="0" border="0" '
            'style="width:100%%;border-collapse:collapse;margin:0;padding:0"><tr><td align="center" bgcolor="%s" '
            'style="background:%s;color:%s;font:700 13px/1.6 Arial,Helvetica,sans-serif;letter-spacing:.06em;text-align:center;'
            'padding:3px 8px;-webkit-print-color-adjust:exact;print-color-adjust:exact">%s</td></tr></table>'
            % (where, b["bg"], b["bg"], b["fg"], htmllib.escape(b["text"])))


# in print, the banner on every sheet: the top one at the top, the bottom one at the bottom
PRINT_CSS = ('<style>@media print{.site-banner-top{position:fixed;top:0;left:0;right:0}'
             '.site-banner-bottom{position:fixed;bottom:0;left:0;right:0}body{padding-top:28px!important;padding-bottom:28px!important}}</style>')


def inject(page, b):
    """page with the banner at the top and bottom of its body (b off or empty, or the banner already there: page as it is)."""
    if not b or not b.get("on") or not page or MARK in page:
        return page
    top, bottom = PRINT_CSS + html(b, "top"), html(b, "bottom")
    m = re.search(r"<body\b[^>]*>", page, re.I)
    page = (page[:m.end()] + top + page[m.end():]) if m else top + page
    i = page.lower().rfind("</body>")
    return (page[:i] + bottom + page[i:]) if i >= 0 else page + bottom


def wrap_text(text, b):
    """A plain-text report with the banner as its first and last line."""
    if not b or not b.get("on"):
        return text
    return "%s\n\n%s\n\n%s" % (b["text"], str(text or "").strip("\n"), b["text"])


def subject(s, b):
    """The email subject with "(TEXT) " before it (report_banner_subject)."""
    if not b or not b.get("on") or not b.get("subject"):
        return s
    return "(%s) %s" % (b["text"], s)
