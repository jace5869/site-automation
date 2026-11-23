# -*- coding: utf-8 -*-
"""1.115.0: the Christmas theme of the report website and the Site portal - 16-bit (or 8-bit) pixel decor in the header bar:
a string of lights that twinkle, a small tree by the site's name, a candy stripe under the active tab, a few snowflakes in
the page's side margins. One source for both: the report website's module (roles/site_email/library/site_report_web.py)
puts it into the atlas, brief and ops pages and their index when it writes them, and writes CSS / JS beside site-report.js
(site-festive.css, site-festive.js); the portal (roles/site_portal_stack/files/site_portal.py, this file copied beside it)
serves the same two as static/festive.css and static/festive.js. No inline script or style, nothing from outside: the
sprites are inline SVG (shape-rendering crispEdges), coloured by attributes and the CSS file.

The settings: report_theme_season none | christmas | auto (Dec 1 to Jan 6), report_theme_season_style 16bit | 8bit,
report_theme_season_lights / _twinkle / _snow (true: the viewer may turn it on and off; false: off for everyone). The page
carries data-season="christmas" and data-season-until (the last day, a Jan 6): festive.js shows nothing after it, so a page
written in the season stops being festive by itself. The viewer's choices (Festive, Lights, Twinkle, Snow) stay in their
browser (localStorage). Content is never touched: no rule names a tile, a table, a chip or a status class; print hides it
all; the emails never get it. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import hashlib
import re

SEASONS = ('none', 'christmas', 'auto')
STYLES = ('16bit', '8bit')
PARTS = ('lights', 'twinkle', 'snow')            # what the site lets the viewer turn on and off (each on by default)
CSS_FILE = 'site-festive.css'                    # the report website's file names, at the folder's top
JS_FILE = 'site-festive.js'


def season_until(setting, today):
    """The season's last day ('YYYY-MM-DD', a Jan 6) when the decor is on for a page written on today, else ''.
    christmas: on (until the coming Jan 6); auto: on from Dec 1 to Jan 6; none (or anything else, false): off."""
    s = '' if setting is None or setting is False else str(setting).strip().lower()
    if s not in ('christmas', 'auto'):
        return ''
    in_season = today.month == 12 or (today.month == 1 and today.day <= 6)
    if s == 'auto' and not in_season:
        return ''
    year = today.year if (today.month == 1 and today.day <= 6) else today.year + 1
    return datetime.date(year, 1, 6).isoformat()


def season_end(until, tz=None):
    """The instant the season is over - the start of the day after until, in the site's time zone (tz, a tzinfo; None =
    UTC) - as UTC ('2027-01-06T23:00:00Z' for Europe/Berlin): every viewer's page stops being festive at the same moment."""
    if not until:
        return ''
    d = datetime.datetime.strptime(until, '%Y-%m-%d') + datetime.timedelta(days=1)
    d = d.replace(tzinfo=tz or datetime.timezone.utc)
    return d.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def style_of(style):
    s = str(style or '').strip().lower()
    return s if s in STYLES else '16bit'


def flag(v, default=True):
    if v is None or v == '':
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() not in ('0', 'false', 'no', 'off', 'n')


def allowed(lights=True, twinkle=True, snow=True):
    """The parts the site allows, in PARTS' order (twinkle only with lights)."""
    out = []
    if flag(lights):
        out.append('lights')
        if flag(twinkle):
            out.append('twinkle')
    if flag(snow):
        out.append('snow')
    return out


def version(text):
    return hashlib.sha1(text.encode('utf-8')).hexdigest()[:10]


# ---------------------------------------------------------------- the pixel sprites (one char = one pixel; '.' = none)
PAL16 = {"G": "#2f9e44", "g": "#1b6b2c", "H": "#6fcf6a", "R": "#e0301e", "r": "#8c1a10", "Y": "#ffc83d", "y": "#c48a12",
         "B": "#3c8cf0", "b": "#1d4fa6", "P": "#f06aa8", "W": "#fff6e0", "T": "#7a4a22", "t": "#4e2e14", "K": None}
PAL8 = {"G": "#00a800", "R": "#d82800", "Y": "#f8b800", "B": "#0078f8", "W": "#fcfcfc", "T": "#ac7c00", "K": None}
TREE16 = ["......Y......", ".....YWY.....", "......Y......", ".....gHg.....", "....gGRGg....", "....GHGGG....",
          "...gGGGBGg...", "...GYGHGGG...", "..gGGGGGRGg..", "..GHGGBGGGG..", ".gGRGGGGGYGg.", ".GGGHGGRGGGG.",
          "gGBGGGYGGGHGg", "gggggggggggg.", ".....tTt.....", ".....tTt....."]
TREE8 = ["....Y....", "...YYY...", "....G....", "...GGG...", "..GRGGG..", "..GGGYG..", ".GGBGGRG.", ".GGGGGGG.",
         "GYGGRGGBG", "....T....", "....T...."]
HOLLY16 = ["..g.....g.....", ".gGg...gGg....", "gGHGg.gGHGg...", ".gGGgRgGGg....", "..gGRrRGg.....", "...gRRrg......"]
HOLLY8 = ["G.....G.", "GG...GG.", ".GGRGG..", "..RRR...", "...R...."]
FLAKE16 = ["...a...", ".a.a.a.", "..aba..", "aabwbaa", "..aba..", ".a.a.a.", "...a..."]
FLAKE8 = ["a.a.a", ".aaa.", "aa.aa", ".aaa.", "a.a.a"]
BULB16 = [".KK.", ".KK.", "CCCC", "CLCC", "CCCC", "CCCc", ".Cc."]
BULB8 = [".K.", "CCC", "CCC", ".C."]
SHADE = {"R": ("#e0301e", "#ff8a70", "#8c1a10"), "G": ("#2f9e44", "#8ef08a", "#1b6b2c"),
         "Y": ("#ffc83d", "#fff2a8", "#c48a12"), "B": ("#3c8cf0", "#a8d4ff", "#1d4fa6")}
BULBS = 8                                          # bulbs in one repeat of the string: each its own twinkle (staggered)


def _runs(row):
    out, x = [], 0
    while x < len(row):
        c, e = row[x], x
        while e + 1 < len(row) and row[e + 1] == c:
            e += 1
        if c != ".":
            out.append((x, e - x + 1, c))
        x = e + 1
    return out


def _rects(rows, fill, dx=0, dy=0):
    return "".join('<rect x="%d" y="%d" width="%d" height="1"%s/>' % (dx + x, dy + y, n, fill(c))
                   for y, row in enumerate(rows) for x, n, c in _runs(row))


def _sprite(rows, pal, scale, cls):
    w, h = max(len(r) for r in rows), len(rows)

    def fill(c):
        if c in "abw":
            return ' class="%s"' % {"a": "s", "b": "s2", "w": "s"}[c]
        return ' class="w"' if pal.get(c, "") is None else ' fill="%s"' % pal[c]
    return ('<svg class="%s" viewBox="0 0 %d %d" width="%d" height="%d" shape-rendering="crispEdges" aria-hidden="true" focusable="false">%s</svg>'
            % (cls, w, h, w * scale, h * scale, _rects(rows, fill)))


def tree(style):
    return _sprite(TREE16 if style == '16bit' else TREE8, PAL16 if style == '16bit' else PAL8, 2, "fz-tree")


def lights(style):
    """The string of lights: one SVG as wide as the bar - the wire, then each bulb of a repeat its own <pattern> (fz-b1 ..
    fz-b8), so festive.css twinkles them at staggered times, in steps. A holly knot at each end."""
    if style == '16bit':
        base, sc, bulb, hh = 64, 2, BULB16, 9
        wire = [(0, 2), (8, 3), (16, 4), (24, 4), (32, 3), (40, 2), (48, 1), (56, 1)]
    else:
        base, sc, bulb, hh = 48, 3, BULB8, 7
        wire = [(0, 1), (12, 2), (24, 2), (36, 1)]
    tile = base * 2
    wire = wire + [(x + base, y) for x, y in wire]
    seg = base * 2 // len(wire)
    wr = "".join('<rect class="w" x="%d" y="%d" width="%d" height="1"/>' % (x, y, seg) for x, y in wire)
    pats, uses = [], []
    for i in range(BULBS):
        c = "RGYB"[i % 4]
        x = i * tile // BULBS + tile // (2 * BULBS) - 2
        y = [yy for xx, yy in wire if xx <= x][-1] + 1
        base_c, hi, lo = SHADE[c]
        if style == '16bit':
            fill = (lambda ch, b=base_c, h=hi, l=lo: ' class="w"' if ch == "K" else ' fill="%s"' % {"C": b, "L": h, "c": l}[ch])
        else:
            fill = (lambda ch, b=base_c: ' class="w"' if ch == "K" else ' fill="%s"' % b)
        pats.append('<pattern id="fz-p%d" width="%d" height="%d" patternUnits="userSpaceOnUse" patternTransform="scale(%d)">%s</pattern>'
                    % (i + 1, tile, hh, sc, _rects(bulb, fill, x, y - 1)))
        uses.append('<rect class="fz-b fz-b%d" width="100%%" height="100%%" fill="url(#fz-p%d)"/>' % (i + 1, i + 1))
    pal, hs = (PAL16, 2) if style == '16bit' else (PAL8, 3)
    holly = HOLLY16 if style == '16bit' else HOLLY8
    return ('<div class="fz-lights" aria-hidden="true"><svg class="fz-str" width="100%%" height="%d" shape-rendering="crispEdges" focusable="false">'
            '<defs><pattern id="fz-pw" width="%d" height="%d" patternUnits="userSpaceOnUse" patternTransform="scale(%d)">%s</pattern>%s</defs>'
            '<rect width="100%%" height="100%%" fill="url(#fz-pw)"/>%s</svg>%s%s</div>'
            % (hh * sc, tile, hh, sc, wr, "".join(pats), "".join(uses),
               _sprite(holly, pal, hs, "fz-holly l"), _sprite(holly, pal, hs, "fz-holly r")))


def snow(style):
    one = _sprite(FLAKE16 if style == '16bit' else FLAKE8, {}, 2 if style == '16bit' else 3, "fz-fl")
    return '<div class="fz-snow" aria-hidden="true">%s</div>' % "".join('<i class="fz-f%d">%s</i>' % (i + 1, one) for i in range(14))


def menu(style, parts):
    """The viewer's switches: one "Festive" button in the bar (its state beside it) opening a small panel - Festive (all of
    it), then each part the site allows: Lights, Twinkle (shown while Lights is on), Snow. Shown by festive.js (hidden without
    it); every choice kept in this browser."""
    ic = _sprite(FLAKE16 if style == '16bit' else FLAKE8, {}, 2, "fz-ic")
    row = ('<button type="button" class="fz-sw fz-r-%s" role="switch" data-k="%s" aria-checked="true">'
           '<span class="fz-l">%s</span><span class="fz-s" aria-hidden="true">On</span></button>')
    rows = row % ('festive', 'festive', 'Festive') + "".join(row % (k, k, k.capitalize()) for k in parts)
    return ('<div class="fz-m" hidden><button type="button" class="fz-tg" aria-expanded="false" aria-controls="fz-pop" '
            'title="Festive decorations - this browser keeps your choices">%s<span class="fz-w">Festive</span>'
            '<span class="fz-s" aria-hidden="true">On</span></button>'
            '<div class="fz-pop" id="fz-pop" role="group" aria-label="Festive decorations" hidden>%s</div></div>' % (ic, rows))


def html_attrs(until, style, parts, tz=None):
    return (' data-season="christmas" data-season-until="%s" data-season-end="%s" data-fz="%s" data-fz-allow="%s"'
            % (until, season_end(until, tz), style, " ".join(parts)))


BRAND_RE = re.compile(r'(<div class="brand">.*?</b>)(</div>)', re.S)
BODY_RE = re.compile(r'<body\b[^>]*>', re.I)
SWITCH = '<div class="seg" id="theme"'


def decorate(page, where, until, style='16bit', parts=PARTS, css_href=CSS_FILE, js_href=JS_FILE, tz=None):
    """A report website page (where: report - an atlas, brief or ops page; index - its index board) with the decor, as the
    module writes it when the season is on (until: season_until's day). A page without the bar's places (another look, a
    report's own page) comes back as it is - no decor - and so does every page with until ''."""
    if not until or not page:
        return page
    style = style_of(style)
    parts = [p for p in PARTS if p in parts]
    out = page
    if SWITCH not in out or '</head>' not in out or not BODY_RE.search(out):
        return page
    if where == 'report':
        if not BRAND_RE.search(out) or '</div></div>\n<div class="wrap' not in out:
            return page
        out = BRAND_RE.sub(lambda m: m.group(1) + tree(style) + m.group(2), out, count=1)
        out = out.replace('</div></div>\n<div class="wrap', '</div>' + lights(style) + '</div>\n<div class="wrap', 1)
    elif where == 'index':
        if '</h1>' not in out or '</div></div>\n<main>' not in out:
            return page
        out = out.replace('</h1>', tree(style) + '</h1>', 1)
        out = out.replace('</div></div>\n<main>', '</div>' + lights(style) + '</div>\n<main>', 1)
    else:
        return page
    out = out.replace(SWITCH, menu(style, parts) + SWITCH, 1)
    out = out.replace('<html', '<html' + html_attrs(until, style, parts, tz), 1)
    out = out.replace('</head>', '<link rel="stylesheet" href="%s"><script src="%s"></script></head>' % (css_href, js_href), 1)
    m = BODY_RE.search(out)
    return out[:m.end()] + snow(style) + out[m.end():]


CSS = r"""/* site-festive.css (site-automation 1.115.0, plugins/module_utils/site_festive.py): the Christmas theme of the report website
   and the Site portal. It never touches content: no rule names a tile, a table cell's colour, a chip or a status class. Nothing
   shows unless festive.js set html[data-festive="on"] - no script, no decor. Print: none of it. Motion moves in steps and
   stops for the system's reduced-motion setting (no twinkle, no snow) and the site's motion off. */
:root{--fz-wire:#3d5a44;--fz-snow:#a9bfdc;--fz-snow2:#6f90bf;--fz-r:#c4142c;--fz-g:#1f7a3a;--fz-ic:#4f7fc0;--fz-lh:18px}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--fz-wire:#8fa594;--fz-snow:#eef4ff;--fz-snow2:#9db9e4;--fz-r:#ff5a5f;--fz-g:#3fbf6a;--fz-ic:#bcd6ff}}
:root[data-theme="dark"],:root:has(#theme-dark:checked){--fz-wire:#8fa594;--fz-snow:#eef4ff;--fz-snow2:#9db9e4;--fz-r:#ff5a5f;--fz-g:#3fbf6a;--fz-ic:#bcd6ff}
:root:has(#theme-light:checked){--fz-wire:#3d5a44;--fz-snow:#a9bfdc;--fz-snow2:#6f90bf;--fz-r:#c4142c;--fz-g:#1f7a3a;--fz-ic:#4f7fc0}
html[data-fz="8bit"]{--fz-lh:21px;--fz-px:8px;--fz-h:4px}

.fz-lights,.fz-snow,.fz-tree{display:none}
.fz-str .w,.fz-holly .w{fill:var(--fz-wire)}
.fz-snow .s{fill:var(--fz-snow)}.fz-snow .s2{fill:var(--fz-snow2)}

/* the viewer's switches: one Festive button in the bar, its panel below it */
.fz-m{display:none}
html[data-festive] .fz-m:not([hidden]){display:inline-flex;position:relative;flex:none}
.fz-tg,.fz-sw{font-family:inherit;font-size:12.5px;font-weight:600;line-height:1;cursor:pointer;color:var(--muted,var(--mute,#4b5563))}
.fz-tg{display:inline-flex;align-items:center;gap:6px;background:var(--card-strong,var(--card2,#f7f9fb));border:1px solid var(--edge,#dde3ea);
  border-radius:10px;padding:4px 5px 4px 9px;min-height:30px}
.fz-tg:hover,.fz-sw:hover{color:var(--ink,#111827)}
.fz-tg:focus-visible,.fz-sw:focus-visible{outline:2px solid var(--accent,#0b7352);outline-offset:2px}
.fz-tg .fz-ic{flex:none;width:14px;height:14px}
.fz-tg .fz-ic .s,.fz-tg .fz-ic .s2{fill:currentColor}
html[data-festive="on"] .fz-tg .fz-ic .s{fill:var(--fz-ic)}
.fz-s{font-size:11px;font-weight:700;border-radius:6px;padding:4px 7px;min-width:30px;text-align:center;
  border:1px solid var(--edge,#dde3ea);background:transparent;color:var(--muted,var(--mute,#4b5563))}
html[data-festive="on"] .fz-tg .fz-s,.fz-sw[aria-checked="true"] .fz-s{background:var(--ink,#111827);border-color:var(--ink,#111827);color:var(--card,#fff)}
.fz-pop{position:absolute;top:calc(100% + 6px);right:0;z-index:60;min-width:172px;display:flex;flex-direction:column;gap:2px;padding:6px;
  background:var(--card,#fff);border:1px solid var(--edge,#dde3ea);border-radius:10px;box-shadow:0 8px 24px rgba(15,23,42,.18)}
.fz-pop[hidden],.fz-sw[hidden]{display:none}
.fz-sw{display:flex;align-items:center;justify-content:space-between;gap:14px;width:100%;background:transparent;border:0;border-radius:8px;padding:6px 6px 6px 8px;text-align:left}
.fz-sw:hover{background:var(--card-strong,var(--card2,#f3f5f8))}
@media (max-width:900px){.fz-tg .fz-w{display:none}html[data-festive="on"] .appbar .brand .fz-tree{display:none}.fz-tg{padding-left:7px}}

/* ON: the tree by the site's name (no switch of its own) */
html[data-festive="on"] .fz-tree{display:inline-block;flex:none;vertical-align:-3px;margin-left:8px}
html[data-festive="on"] h1 .fz-tree{vertical-align:-1px;margin-left:10px}

/* the string of lights: a strip at the bottom of the bar, inside it (the bar is that much taller, nothing hangs over the page) */
html[data-festive="on"][data-fz-lights="on"] .fz-lights{display:block;position:relative;pointer-events:none;overflow:hidden}
.fz-lights .fz-str{display:block;width:100%}
.fz-lights .fz-holly{position:absolute;top:0}
.fz-lights .fz-holly.l{left:clamp(6px,1.4vw,24px)}
.fz-lights .fz-holly.r{right:clamp(6px,1.4vw,24px);transform:scaleX(-1)}
html[data-festive="on"] .appbar,html[data-festive="on"] .top{border-bottom-color:var(--fz-g)}
/* the bar stays at the top while scrolling (wider than 640 px): the tables' sticky heads and the sections' rail sit under
   its lights, not behind them */
@media (min-width:641px){
  html[data-festive="on"][data-fz-lights="on"]{scroll-padding-top:calc(var(--appbar,0px) + var(--fz-lh) + 12px)}
  html[data-festive="on"][data-fz-lights="on"] .wrap th{top:calc(var(--appbar,0px) + var(--fz-lh))}
  html[data-festive="on"][data-fz-lights="on"] .has-rail>.rail{top:calc(var(--appbar,0px) + var(--fz-lh) + 20px)}}

/* the twinkle: each bulb of a repeat fades and comes back in steps, at its own time */
@keyframes fz-tw{0%,52%{opacity:1}58%{opacity:.8}64%{opacity:.6}70%{opacity:.4}76%{opacity:.6}82%{opacity:.8}88%,100%{opacity:1}}
html[data-festive="on"][data-fz-lights="on"][data-fz-twinkle="on"] .fz-b{animation:fz-tw var(--tw,3.2s) steps(1,end) infinite;animation-delay:var(--twd,0s)}
.fz-b1{--tw:3.4s;--twd:-.3s}.fz-b2{--tw:2.9s;--twd:-1.9s}.fz-b3{--tw:3.8s;--twd:-2.6s}.fz-b4{--tw:3.1s;--twd:-.9s}
.fz-b5{--tw:3.6s;--twd:-3.1s}.fz-b6{--tw:2.7s;--twd:-1.2s}.fz-b7{--tw:4s;--twd:-2.2s}.fz-b8{--tw:3.3s;--twd:-1.6s}

/* the active tab: a red-and-green candy stripe under it (pixel blocks, never a status colour's meaning) */
html[data-festive="on"] .nav a.on,html[data-festive="on"] .nav a[aria-current="page"],html[data-festive="on"] .tabs label{position:relative}
html[data-festive="on"] .nav a.on::after,html[data-festive="on"] .nav a[aria-current="page"]::after,
html[data-festive="on"] #t0:checked~.tabs label[for=t0]::after,html[data-festive="on"] #t1:checked~.tabs label[for=t1]::after,
html[data-festive="on"] #t2:checked~.tabs label[for=t2]::after,html[data-festive="on"] #t3:checked~.tabs label[for=t3]::after,
html[data-festive="on"] #t4:checked~.tabs label[for=t4]::after,html[data-festive="on"] #t5:checked~.tabs label[for=t5]::after,
html[data-festive="on"] #t6:checked~.tabs label[for=t6]::after,html[data-festive="on"] #t7:checked~.tabs label[for=t7]::after,
html[data-festive="on"] #t8:checked~.tabs label[for=t8]::after,html[data-festive="on"] #t9:checked~.tabs label[for=t9]::after,
html[data-festive="on"] #t10:checked~.tabs label[for=t10]::after,html[data-festive="on"] #t11:checked~.tabs label[for=t11]::after{
  content:"";position:absolute;left:6px;right:6px;bottom:-5px;height:var(--fz-h,3px);pointer-events:none;
  background:repeating-linear-gradient(90deg,var(--fz-r) 0 var(--fz-px,6px),var(--fz-g) var(--fz-px,6px) calc(2 * var(--fz-px,6px)))}

/* snow: a few flakes in the page's side margins only (1200 px and wider), on a pixel grid; no stacking context added */
html[data-festive="on"][data-fz-snow="on"] .fz-snow{display:block;position:fixed;inset:0;z-index:0;pointer-events:none;overflow:hidden}
.fz-snow i{position:absolute;top:-20px;opacity:.85;animation:fz-fall var(--d) steps(var(--n)) infinite;animation-delay:var(--dl)}
html[data-fz="16bit"] .fz-snow i{--n:275}
html[data-fz="8bit"] .fz-snow i{--n:184}
.fz-snow svg{display:block}
.fz-f1,.fz-f3,.fz-f5,.fz-f7{left:4px}.fz-f2,.fz-f4,.fz-f6{left:16px}
.fz-f8,.fz-f10,.fz-f12,.fz-f14{right:4px}.fz-f9,.fz-f11,.fz-f13{right:16px}
.fz-f1{--d:19s;--dl:-3s}.fz-f2{--d:23s;--dl:-14s}.fz-f3{--d:21s;--dl:-8s}.fz-f4{--d:25s;--dl:-20s}.fz-f5{--d:18s;--dl:-5s}
.fz-f6{--d:22s;--dl:-17s}.fz-f7{--d:20s;--dl:-10s}.fz-f8{--d:24s;--dl:-2s}.fz-f9{--d:19s;--dl:-12s}.fz-f10{--d:23s;--dl:-7s}
.fz-f11{--d:21s;--dl:-19s}.fz-f12{--d:25s;--dl:-4s}.fz-f13{--d:20s;--dl:-11s}.fz-f14{--d:22s;--dl:-16s}
@media (max-width:1199px){html[data-festive="on"] .fz-snow{display:none}}
@keyframes fz-fall{from{transform:translateY(0)}to{transform:translateY(calc(100vh + 40px))}}

/* less motion (the system's setting - whatever the site's motion setting says - or the site's motion off): no twinkle, no
   snow, and their switches are not offered; the lights stay, every bulb lit */
@media (prefers-reduced-motion:reduce){
  html .fz-b{animation:none!important}
  html .fz-snow,html .fz-r-twinkle,html .fz-r-snow{display:none!important}}
html.motion-off .fz-b{animation:none!important}
html.motion-off .fz-snow,html.motion-off .fz-r-twinkle,html.motion-off .fz-r-snow{display:none!important}

@media print{.fz-lights,.fz-snow,.fz-tree,.fz-m{display:none!important}
  html[data-festive="on"] .nav a.on::after,html[data-festive="on"] .nav a[aria-current="page"]::after,html[data-festive="on"] .tabs label::after{content:none!important}}
"""

JS = r"""// site-festive.js (site-automation 1.115.0, plugins/module_utils/site_festive.py): the Christmas theme's switches. Loaded in
// <head> from the site's own files (a strict Content-Security-Policy runs it). The page carries data-season="christmas" while
// the site's season setting is on, data-season-until (its last day: after it, nothing - a page written in the season stops
// being festive by itself) and data-fz-allow (the parts the site lets the viewer turn on and off). This script decides before
// the page is drawn (html data-festive, data-fz-lights, data-fz-twinkle, data-fz-snow: on or off) and keeps the viewer's
// choices in this browser - the same keys for the report website and the portal.
(function () {
  "use strict";
  var root = document.documentElement;
  if (root.getAttribute("data-season") !== "christmas") return;
  // the season's end: the instant the site's day after its last day begins (data-season-end, UTC), else that last day's end here
  var endAt = Date.parse(root.getAttribute("data-season-end") || "");
  if (isNaN(endAt)) endAt = Date.parse((root.getAttribute("data-season-until") || "") + "T23:59:59");
  var over = function () { return !isNaN(endAt) && Date.now() >= endAt; };
  if (over()) return;
  var KEYS = {festive: "site-festive", lights: "site-festive-lights", twinkle: "site-festive-twinkle", snow: "site-festive-snow"};
  var PARTS = ["lights", "twinkle", "snow"];
  var allow = " " + (root.getAttribute("data-fz-allow") || "") + " ";
  var pref = function (k) { try { return localStorage.getItem(KEYS[k]) !== "off"; } catch (e) { return true; } };
  var st = {festive: pref("festive")};
  PARTS.forEach(function (k) { st[k] = allow.indexOf(" " + k + " ") >= 0 && pref(k); });
  var apply = function () {
    root.setAttribute("data-festive", st.festive ? "on" : "off");
    PARTS.forEach(function (k) { root.setAttribute("data-fz-" + k, st[k] && (k !== "twinkle" || st.lights) ? "on" : "off"); });
  };
  apply();
  document.addEventListener("DOMContentLoaded", function () {
    var m = document.querySelector(".fz-m");
    if (!m) return;
    var tg = m.querySelector(".fz-tg"), pop = m.querySelector(".fz-pop"), sws = m.querySelectorAll(".fz-sw");
    var sync = function () {
      var s = tg.querySelector(".fz-s");
      if (s) s.textContent = st.festive ? "On" : "Off";
      for (var i = 0; i < sws.length; i++) {
        var b = sws[i], k = b.getAttribute("data-k"), on = !!st[k];
        b.setAttribute("aria-checked", on ? "true" : "false");
        b.querySelector(".fz-s").textContent = on ? "On" : "Off";
        b.hidden = k !== "festive" && (!st.festive || (k === "twinkle" && !st.lights));
      }
    };
    var open = function (on) {
      pop.hidden = !on;
      tg.setAttribute("aria-expanded", on ? "true" : "false");
    };
    m.hidden = false;
    sync();
    tg.addEventListener("click", function () {
      open(pop.hidden);
      if (!pop.hidden) sws[0].focus();
    });
    for (var i = 0; i < sws.length; i++) {
      sws[i].addEventListener("click", function () {
        var k = this.getAttribute("data-k");
        st[k] = !st[k];
        try { if (st[k]) localStorage.removeItem(KEYS[k]); else localStorage.setItem(KEYS[k], "off"); } catch (e) { /* this page only */ }
        apply();
        sync();
      });
    }
    m.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !pop.hidden) { open(false); tg.focus(); }
    });
    document.addEventListener("click", function (e) { if (!pop.hidden && !m.contains(e.target)) open(false); });
  });
  // a page left open past the season's end (or woken up after it): the page as every other day
  var stop = function () {
    if (!over()) return;
    ["data-festive", "data-fz-lights", "data-fz-twinkle", "data-fz-snow"].forEach(function (a) { root.removeAttribute(a); });
    var m = document.querySelector(".fz-m");
    if (m) m.hidden = true;
    clearInterval(timer);
  };
  var timer = setInterval(stop, 60000);
  document.addEventListener("visibilitychange", stop);
})();
"""
