"""The site-automation look for the guides' PDFs, on top of actpdf.py (the ACT guides keep actpdf's own):
a cover in the colours of the reports' glass look, its title on a see-through card; a band under each
chapter's title; rounded tables, notes and code. White pages inside, for printing.

The logo: SITE_PDF_LOGO=<a PNG or JPEG> puts it on the cover and at the top of every page. Without it
there is none. The PDFs in the repository are built without one; a site builds its own copies with its
logo (README.md, "Your logo on them")."""
import os
import re
import sys
from xml.sax.saxutils import escape

from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth

import actpdf
from actpdf import *  # noqa: F401,F403 - fonts, palette, styles, helpers, GuideDoc

# ---------------------------------------------------------------------------- palette (the glass look's)
SKY = colors.HexColor("#CFE2EE")
MIST = colors.HexColor("#E6EEF6")
SEA = colors.HexColor("#D3E9E4")
SLATE = colors.HexColor("#3E4C59")
RULE = colors.HexColor("#D6E1EA")
ZEBRA = colors.HexColor("#F3F7FA")
CHIP = colors.HexColor("#F7FAFC")
TEAL_D = colors.HexColor("#0A6670")   # teal for small text: 5:1 and more on the light fills

# ---------------------------------------------------------------------------- the logo
LOGO_FILE = os.environ.get("SITE_PDF_LOGO", "").strip()
if LOGO_FILE and not os.path.isfile(LOGO_FILE):
    sys.exit("SITE_PDF_LOGO: there is no file %s" % LOGO_FILE)
LOGO = ImageReader(LOGO_FILE) if LOGO_FILE else None


def draw_logo(c, x, y, height):
    """The logo with its bottom left corner at x, y, `height` points tall; returns its width."""
    iw, ih = LOGO.getSize()
    width = height * iw / ih
    c.drawImage(LOGO, x, y, width, height, mask="auto")
    return width


# ---------------------------------------------------------------------------- styles
# the same ST the helpers read (each builder is a process of its own: the ACT guides never see this)
ST["h1"] = style("h1", fontName="Sans-Bold", fontSize=19, leading=24, textColor=NAVY, leftIndent=16,
                 spaceBefore=12, spaceAfter=18, keepWithNext=1)
ST["tochead"] = style("tochead", fontName="Sans-Bold", fontSize=19, leading=24, textColor=NAVY,
                      spaceAfter=12)
ST["toc1"] = style("toc1", fontName="Sans-Semi", fontSize=10.4, leading=18, textColor=NAVY)
ST["toc2"] = style("toc2", fontSize=9.3, leading=13.5, leftIndent=16, textColor=MUTED)
COVER_SPACE = 3.55 * inch            # the story on a cover starts below the title card


class ChapterHead(Paragraph):
    """A chapter's title on a light band with a teal bar at its left; the contents and the outline
    read it as any h1."""

    def draw(self):
        c, w, h = self.canv, self.width, self.height
        c.saveState()
        p = c.beginPath()
        p.roundRect(0, -8, w, h + 15, 8)
        c.clipPath(p, stroke=0, fill=0)
        c.linearGradient(0, 0, w, 0, (MIST, colors.white))
        c.setFillColor(TEAL)
        c.rect(0, -8, 4.5, h + 15, stroke=0, fill=1)
        c.restoreState()
        Paragraph.draw(self)


def H1(text):
    m = re.match(r"(\d+\.|Appendix:)\s+(.*)$", text)
    if m:                            # the number in teal: "5. Title" stays the text of the contents
        return ChapterHead('<font color="#0E7C86">%s</font> %s' % (escape(m.group(1)), escape(m.group(2))),
                           ST["h1"])
    return ChapterHead(escape(text), ST["h1"])


def section(title, first):
    """H1 (keepWithNext) followed by its first block(s)."""
    return [CondPageBreak(2.2 * inch), H1(title)] + (first if isinstance(first, list) else [first])


def code(text, label=None):
    t = actpdf.code(text, label)
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, RULE), ("ROUNDEDCORNERS", [5, 5, 5, 5])]))
    return t


def callout(kind, title, body_items):
    """kind: note | warn | good | risk - a rounded card in the kind's colours."""
    t = actpdf.callout(kind, title, body_items)
    edge = {"note": "#8CC6CB", "warn": "#DDBB86", "good": "#97C9AB", "risk": "#DDA39C"}[kind]
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor(edge)),
                           ("ROUNDEDCORNERS", [6, 6, 6, 6]), ("LEFTPADDING", (0, 0), (-1, -1), 12)]))
    return t


def table(header, rows, widths, zebra=True, bold_first=False):
    t = actpdf.table(header, rows, widths, zebra=False, bold_first=bold_first)
    cmds = [("BOX", (0, 0), (-1, -1), 0.6, RULE), ("LINEBELOW", (0, 1), (-1, -1), 0.4, RULE),
            ("ROUNDEDCORNERS", [5, 5, 5, 5])]
    if zebra:
        cmds += [("BACKGROUND", (0, i), (-1, i), ZEBRA) for i in range(2, len(rows) + 1, 2)]
    t.setStyle(TableStyle(cmds))
    return t


# ---------------------------------------------------------------------------- the document
def _fit(text, font, size, width, least=9):
    while size > least and stringWidth(text, font, size) > width:
        size -= 0.5
    return size


def _glow(c, x, y, r, color, alpha=0.012, steps=40):
    """A soft round shape (the blurred shapes behind the glass look): rings that add up, closer
    together towards the middle, so no edge shows."""
    c.setFillColor(color)
    c.setFillAlpha(alpha)
    for i in range(steps):
        c.circle(x, y, r * (1 - i / steps) ** 0.6, stroke=0, fill=1)


class SiteDoc(GuideDoc):
    """GuideDoc in the site-automation look. A builder sets the cover and header texts, version_text
    (e.g. "site-automation 0.24.0") and cover_note (the line at the foot of the cover)."""
    eyebrow = "SITE AUTOMATION"
    version_text = ""
    cover_note = ""

    @staticmethod
    def cover_bg(c, doc):
        c.saveState()
        c.linearGradient(0, PAGE_H, PAGE_W, 0, (SKY, MIST, SEA), (0, 0.45, 1))
        _glow(c, PAGE_W - 1.1 * inch, PAGE_H - 1.2 * inch, 2.6 * inch, colors.HexColor("#5B8DEF"))
        _glow(c, 0.6 * inch, PAGE_H - 3.6 * inch, 2.2 * inch, TEAL)
        _glow(c, PAGE_W * 0.75, 0.9 * inch, 2.8 * inch, colors.HexColor("#F0D9A8"), alpha=0.008)
        c.restoreState()

        # the title card: see-through white, a soft shadow under it
        x, w = MARGIN - 18, CONTENT_W + 36
        top, h = PAGE_H - 0.8 * inch, 2.75 * inch
        y = top - h
        c.saveState()
        c.setFillColor(NAVY)
        c.setFillAlpha(0.07)
        c.roundRect(x + 2, y - 5, w, h, 18, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFillAlpha(0.68)
        c.setStrokeColor(colors.white)
        c.setStrokeAlpha(0.95)
        c.setLineWidth(1.2)
        c.roundRect(x, y, w, h, 18, stroke=1, fill=1)
        c.restoreState()

        c.saveState()
        tx = x + 30
        if LOGO:
            tx += draw_logo(c, tx, top - 30 - 92, 92) + 26
        avail = x + w - 30 - tx
        c.setFillColor(TEAL_D)
        c.setFont("Sans-Semi", 8.6)
        c.drawString(tx, top - 40, doc.eyebrow, charSpace=2.6)
        c.setFillColor(NAVY)
        size = _fit(doc.cover_title, "Sans-Bold", 30, avail, least=20)
        c.setFont("Sans-Bold", size)
        c.drawString(tx, top - 76, doc.cover_title)
        c.setFillColor(SLATE)
        size = min(_fit(line, "Sans", 13, avail) for line in doc.cover_lines)
        c.setFont("Sans", size)
        for i, line in enumerate(doc.cover_lines):
            c.drawString(tx, top - 102 - i * (size + 5), line)

        # the chips: what it is, the version, the date
        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.line(x + 30, y + 52, x + w - 30, y + 52)
        cx = x + 30
        for text in (doc.cover_meta, doc.version_text, DATE):
            if not text:
                continue
            tw = stringWidth(text, "Sans-Semi", 8.4)
            c.setFillColor(CHIP)
            c.setStrokeColor(RULE)
            c.roundRect(cx, y + 18, tw + 20, 20, 10, stroke=1, fill=1)
            c.setFillColor(NAVY)
            c.setFont("Sans-Semi", 8.4)
            c.drawString(cx + 10, y + 24.6, text)
            cx += tw + 28
        if doc.cover_note:
            c.setFillColor(SLATE)
            c.setFont("Sans", 8)
            c.drawString(MARGIN, 0.55 * inch, doc.cover_note)
        c.restoreState()

    @staticmethod
    def chrome(c, doc):
        c.saveState()
        base = PAGE_H - 0.5 * inch
        hx = MARGIN
        if LOGO:
            hx += draw_logo(c, MARGIN, base - 4, 17) + 7
        c.setFont("Sans-Semi", 7.8)
        c.setFillColor(MUTED)
        c.drawString(hx, base, doc.header_title)
        if doc.version_text:
            tw = stringWidth(doc.version_text, "Sans-Semi", 7.4)
            c.setFillColor(TEAL_L)
            c.roundRect(PAGE_W - MARGIN - tw - 16, base - 4, tw + 16, 13.5, 6.75, stroke=0, fill=1)
            c.setFillColor(TEAL_D)
            c.setFont("Sans-Semi", 7.4)
            c.drawString(PAGE_W - MARGIN - tw - 8, base, doc.version_text)
        p = c.beginPath()
        p.rect(MARGIN, PAGE_H - 0.62 * inch, CONTENT_W, 1.4)
        c.clipPath(p, stroke=0, fill=0)
        c.linearGradient(MARGIN, 0, PAGE_W - MARGIN, 0, (TEAL, NAVY))
        c.restoreState()

        c.saveState()
        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.line(MARGIN, 0.6 * inch, PAGE_W - MARGIN, 0.6 * inch)
        c.setFillColor(MUTED)
        c.setFont("Sans", 7.8)
        c.drawString(MARGIN, 0.42 * inch, DATE)
        c.setFont("Sans-Semi", 6.8)
        c.drawCentredString(PAGE_W / 2, 0.42 * inch, doc.eyebrow, charSpace=2.2)
        c.setFont("Sans", 7.8)
        c.drawRightString(PAGE_W - MARGIN, 0.42 * inch, "Page %d" % doc.page)
        c.restoreState()
