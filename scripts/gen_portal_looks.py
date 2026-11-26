#!/usr/bin/env python3
"""1.135.0: the site portal's looks (portal_look: strata, metro, relay, chroma, press, atlas, tower) -
roles/site_portal_stack/files/static/portal-looks.css, made from portal-looks.css.in and the palettes below.

The report palette (html[data-look], scripts/gen_portal_css.py) stays the base layer; a look is html[data-skin=KEY] on top of it.
Each look gets the five blocks the base palette has (light, dark by the system, data-theme=dark, and the Light / Dark switch
checked), so a click on Light or Dark never shows half a palette. The fonts are the bundled woff2 files in static/fonts/
(SIL Open Font License, the licence beside each family). tests/test_portal_looks.py checks the file is up to date.
  python scripts/gen_portal_looks.py          (writes the file)
  python scripts/gen_portal_looks.py --check  (exit 1 when it is out of date)"""
from __future__ import absolute_import, division, print_function

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "roles", "site_portal_stack", "files", "portal-looks.css.in")
OUT = os.path.join(REPO, "roles", "site_portal_stack", "files", "static", "portal-looks.css")

# the families bundled (static/fonts/<file>-latin-<weight>-normal.woff2) and the weights shipped
FONTS = (("Chakra Petch", "chakra-petch"), ("IBM Plex Mono", "ibm-plex-mono"), ("Figtree", "figtree"), ("Fira Code", "fira-code"),
         ("Barlow Semi Condensed", "barlow-semi-condensed"), ("Chivo Mono", "chivo-mono"), ("Manrope", "manrope"), ("Inter", "inter"))
WEIGHTS = (400, 600, 700)
SYS_SANS = '"Segoe UI",system-ui,-apple-system,Roboto,Arial,sans-serif'
SYS_MONO = 'ui-monospace,"Cascadia Mono",Consolas,Menlo,monospace'

# key: (name, sans, display, mono, radius, {dark}, {light}) - bg, pane, edge, ink, mute, a1 (accent), a2, ok, warn, crit
LOOKS = (
    ("strata", "Strata Console", "Inter", "Chakra Petch", "IBM Plex Mono", "6px",
     dict(bg="#0e1118", pane="#191e2a", edge="#333d54", ink="#f2f5fa", mute="#9aa6bf", a1="#3b9eff", a2="#00cfa8", ok="#29d67d", warn="#f5b324", crit="#ff4f66", lip="#07090f"),
     dict(bg="#e6e3da", pane="#fbfaf6", edge="#b5b1a4", ink="#141821", mute="#4e5566", a1="#1458d4", a2="#087a6a", ok="#0f7c48", warn="#b36600", crit="#c81e3a", lip="#c4bfb2")),
    ("metro", "Metro Live Tiles", "Figtree", "Figtree", "Fira Code", "2px",
     dict(bg="#101114", pane="#1b1d22", edge="#2b2f37", ink="#f4f5f7", mute="#a3a9b5", a1="#3fb1e8", a2="#9c85ff", ok="#4cd685", warn="#f2b33d", crit="#ff6b6b", lip="#101114"),
     dict(bg="#eef0f3", pane="#ffffff", edge="#d5d9e0", ink="#15171c", mute="#586070", a1="#0b6ea0", a2="#5a3fc4", ok="#12784a", warn="#9a5c00", crit="#c62a2a", lip="#d5d9e0")),
    ("relay", "Relay Panel", "Barlow Semi Condensed", "Barlow Semi Condensed", "IBM Plex Mono", "4px",
     dict(bg="#171a1b", pane="#252a2c", edge="#50595c", ink="#f1f3ee", mute="#afb9b7", a1="#ffc35a", a2="#79c8d3", ok="#74d99a", warn="#ffd064", crit="#ff9286", lip="#0e1011"),
     dict(bg="#dbdeda", pane="#f3f4ee", edge="#a1aaa5", ink="#202725", mute="#52605b", a1="#845000", a2="#146778", ok="#21653d", warn="#805400", crit="#a32f27", lip="#a1aaa5")),
    ("chroma", "Chroma Perspex", "Chakra Petch", "Chakra Petch", "Chivo Mono", "4px",
     dict(bg="#121319", pane="#1b1d27", edge="#3a4058", ink="#f4f5f8", mute="#a3aac0", a1="#d9507f", a2="#5677d6", ok="#3cc47c", warn="#e3b341", crit="#e5534b", lip="#06070b"),
     dict(bg="#f3efe6", pane="#ffffff", edge="#23263a", ink="#14161f", mute="#4c536b", a1="#b23a68", a2="#2f51c8", ok="#007d40", warn="#9e6200", crit="#cc142c", lip="#23263a")),
    ("press", "Signal Press", "Barlow Semi Condensed", "Barlow Semi Condensed", "Chivo Mono", "3px",
     dict(bg="#1e201f", pane="#282b28", edge="#4a4f48", ink="#f8f4e6", mute="#b9bcaf", a1="#ff996f", a2="#9ebdf7", ok="#b2d593", warn="#fbd278", crit="#ff9185", lip="#1e201f"),
     dict(bg="#eeeae0", pane="#fffdf3", edge="#cac8bc", ink="#23251f", mute="#53594f", a1="#aa3519", a2="#335a91", ok="#446630", warn="#855500", crit="#af342a", lip="#cac8bc")),
    ("atlas", "Contour Atlas", "Manrope", "Manrope", "IBM Plex Mono", "16px",
     dict(bg="#101422", pane="#1b2033", edge="#373c53", ink="#f3f1ff", mute="#b6bad1", a1="#b8adff", a2="#77dfed", ok="#72e2bf", warn="#ffcc77", crit="#ff90ad", lip="#0a0d17"),
     dict(bg="#f0f0f8", pane="#fefeff", edge="#d6d6e5", ink="#222139", mute="#575a73", a1="#6246a8", a2="#126877", ok="#08755d", warn="#885500", crit="#b73559", lip="#d6d6e5")),
    ("tower", "Timing Tower", "Chakra Petch", "Chakra Petch", "Chivo Mono", "3px",
     dict(bg="#0f1014", pane="#191b22", edge="#2c2f3a", ink="#ffffff", mute="#a4a9b8", a1="#b07cff", a2="#4fd1ff", ok="#3ccf7a", warn="#f2c037", crit="#ff5a5a", lip="#050507"),
     dict(bg="#e9eaee", pane="#ffffff", edge="#cfd2db", ink="#101218", mute="#525868", a1="#6d3fd1", a2="#0a7fa6", ok="#13803f", warn="#8f6400", crit="#c42727", lip="#cfd2db")),
)
KEYS = tuple(x[0] for x in LOOKS)


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def hexc(c):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in c)


def mix(a, b, t):
    """a with t of b (0..1)"""
    a, b = rgb(a), rgb(b)
    return hexc([a[i] + (b[i] - a[i]) * t for i in range(3)])


def rgba(h, alpha):
    r, g, b = rgb(h)
    return "rgba(%d,%d,%d,%.2f)" % (r, g, b, alpha)


def lum(h):
    def ch(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb(h)
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def tokens(p, dark):
    """the portal's whole token set (portal.css reads these) from a look's ten colours"""
    ink_on_a1 = "#ffffff" if contrast("#ffffff", p["a1"]) >= contrast("#0b0f14", p["a1"]) else "#0b0f14"
    t = {
        "ink": p["ink"], "muted": p["mute"], "label": mix(p["mute"], p["ink"], 0.35), "spark": p["a2"],
        "accent": p["a1"], "accent-ink": ink_on_a1, "accent-soft": rgba(p["a1"], 0.16 if dark else 0.11),
        "pri": p["a1"], "pri-ink": ink_on_a1, "pri-hover": mix(p["a1"], p["ink"], 0.18),
        "bg": p["bg"], "bg2": mix(p["bg"], p["pane"], 0.5), "glow": "transparent", "glow1": "transparent", "glow2": "transparent",
        "card": p["pane"], "card-solid": p["pane"], "card-strong": mix(p["pane"], p["ink"], 0.05), "th": mix(p["pane"], p["ink"], 0.05),
        "edge": p["edge"], "line": mix(p["edge"], p["pane"], 0.45), "zebra": mix(p["pane"], p["ink"], 0.025),
        "hover": rgba(p["a1"], 0.08), "code": mix(p["bg"], p["pane"], 0.3), "bar": rgba(p["pane"], 0.9),
        "shadow": "0 1px 2px rgba(0,0,0,.35)" if dark else "0 1px 2px rgba(16,24,40,.07)",
        "lip": p["lip"], "a2": p["a2"],
    }
    for name, key in (("ok", "ok"), ("warning", "warn"), ("critical", "crit")):
        t[name] = p[key]
        t[name + "-bg"] = rgba(p[key], 0.14 if dark else 0.10)
        t[name + "-bd"] = rgba(p[key], 0.42 if dark else 0.38)
    t["critical-row"] = rgba(p["crit"], 0.06 if dark else 0.045)
    t["warning-row"] = rgba(p["warn"], 0.06 if dark else 0.05)
    t["info"], t["info-bg"], t["info-bd"] = p["a2"], rgba(p["a2"], 0.13), rgba(p["a2"], 0.38)
    t["unknown"], t["unknown-bg"], t["unknown-bd"] = p["mute"], rgba(p["mute"], 0.12), rgba(p["mute"], 0.34)
    t["color-scheme"] = "dark" if dark else "light"
    return t


def block(sel, t, fonts):
    body = ";".join(("%s:%s" % (k, v)) if k == "color-scheme" else ("--%s:%s" % (k, v)) for k, v in sorted(t.items()))
    return "%s{%s;%s}" % (sel, fonts, body)


def font_faces():
    out = []
    for fam, f in FONTS:
        for w in WEIGHTS:
            out.append('@font-face{font-family:"%s";font-style:normal;font-weight:%d;font-display:swap;src:url("fonts/%s-latin-%d-normal.woff2") format("woff2")}'
                       % (fam, w, f, w))
    return "\n".join(out)


def look_tokens():
    parts = []
    for key, _name, sans, display, mono, radius, dark, light in LOOKS:
        sel = ':root[data-skin="%s"]' % key
        fonts = '--sans:"%s",%s;--display:"%s",%s;--mono:"%s",%s;--r:%s' % (sans, SYS_SANS, display, SYS_SANS, mono, SYS_MONO, radius)
        L, D = tokens(light, False), tokens(dark, True)
        parts.append("\n".join((
            block(sel, L, fonts),
            "@media (prefers-color-scheme:dark){%s}" % block(sel + ':not([data-theme="light"])', D, fonts),
            block(sel + '[data-theme="dark"]', D, fonts),
            block(sel + ":has(#theme-light:checked)", L, fonts),
            block(sel + ":has(#theme-dark:checked)", D, fonts))))
    return "\n".join(parts)


def build():
    head = "/* made by scripts/gen_portal_looks.py from portal-looks.css.in - do not edit static/portal-looks.css */\n"
    with open(SRC) as fh:
        src = fh.read()
    return head + src.replace("@@FONTS@@", font_faces()).replace("@@TOKENS@@", look_tokens())


def check_contrast():
    """status colours and the text readable on each look's page and cards (WCAG AA 4.5 for text, 3 for the big status words)"""
    bad = []
    for key, _n, _s, _d, _m, _r, dark, light in LOOKS:
        for mode, p in (("dark", dark), ("light", light)):
            for fg in ("ink", "mute"):
                for bg in ("bg", "pane"):
                    if contrast(p[fg], p[bg]) < 4.5:
                        bad.append("%s %s: %s on %s %.2f" % (key, mode, fg, bg, contrast(p[fg], p[bg])))
            for fg in ("ok", "warn", "crit", "a1"):
                if contrast(p[fg], p["pane"]) < 3.0:
                    bad.append("%s %s: %s on pane %.2f" % (key, mode, fg, contrast(p[fg], p["pane"])))
    return bad


if __name__ == "__main__":
    want = build()
    if "--contrast" in sys.argv:
        b = check_contrast()
        print("\n".join(b) or "ok: every look's text and status colours are readable")
        sys.exit(1 if b else 0)
    if "--check" in sys.argv:
        have = ""
        if os.path.exists(OUT):
            with open(OUT) as fh:
                have = fh.read()
        if have != want:
            print("roles/site_portal_stack/files/static/portal-looks.css is out of date: python scripts/gen_portal_looks.py")
            sys.exit(1)
        print("ok: portal-looks.css is up to date")
    else:
        with open(OUT, "w") as fh:
            fh.write(want)
        print("wrote %s (%d bytes)" % (OUT, len(want)))
