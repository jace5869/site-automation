"""Shared ReportLab building blocks for the ACT guides (fonts, palette, styles, callouts, code
blocks, tables, diagram primitives, and the document template)."""
import os
import re
import sys
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Flowable, Frame, KeepTogether,
                                NextPageTemplate, PageBreak, PageTemplate, Paragraph,
                                Preformatted, Spacer, Table, TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

VERSION = "0.6.22"
DATE = "September 2026"

# ---------------------------------------------------------------------------- fonts
# Noto Sans + Noto Sans Mono TTFs (Fedora/RHEL: google-noto-sans-fonts, google-noto-sans-mono-fonts).
# Elsewhere set NOTO_FONT_DIR to the folder that holds NotoSans-Regular.ttf etc.
FD = os.path.join(os.environ.get("NOTO_FONT_DIR", "/usr/share/fonts/google-noto"), "")
for name, f in (("Sans", "NotoSans-Regular.ttf"), ("Sans-Bold", "NotoSans-Bold.ttf"),
                ("Sans-Italic", "NotoSans-Italic.ttf"), ("Sans-BoldItalic", "NotoSans-BoldItalic.ttf"),
                ("Sans-Semi", "NotoSans-SemiBold.ttf"), ("Mono", "NotoSansMono-Regular.ttf"),
                ("Mono-Bold", "NotoSansMono-Bold.ttf")):
    pdfmetrics.registerFont(TTFont(name, FD + f))
pdfmetrics.registerFontFamily("Sans", normal="Sans", bold="Sans-Bold", italic="Sans-Italic",
                              boldItalic="Sans-BoldItalic")
pdfmetrics.registerFontFamily("Mono", normal="Mono", bold="Mono-Bold", italic="Mono", boldItalic="Mono-Bold")

# ---------------------------------------------------------------------------- palette
NAVY = colors.HexColor("#1B3A5C")
TEAL = colors.HexColor("#0E7C86")
TEAL_L = colors.HexColor("#E3F2F3")
AMBER = colors.HexColor("#A86A0B")
AMBER_L = colors.HexColor("#FDF3E1")
RED = colors.HexColor("#A93226")
RED_L = colors.HexColor("#FBE9E7")
GREEN = colors.HexColor("#2E7D4F")
GREEN_L = colors.HexColor("#E6F4EC")
INK = colors.HexColor("#1F2933")
MUTED = colors.HexColor("#5B6770")
LINE = colors.HexColor("#D3DAE1")
PANEL = colors.HexColor("#F4F6F8")
CODEBG = colors.HexColor("#F6F8FA")
CODEINK = colors.HexColor("#8A2B1F")

PAGE_W, PAGE_H = letter
MARGIN = 0.75 * inch
CONTENT_W = PAGE_W - 2 * MARGIN

# ---------------------------------------------------------------------------- styles
def style(name, **kw):
    base = dict(fontName="Sans", fontSize=10, leading=14.2, textColor=INK, spaceAfter=6)
    base.update(kw)
    return ParagraphStyle(name, **base)


ST = {
    "body": style("body"),
    "lead": style("lead", fontSize=11.5, leading=16.5, textColor=INK, spaceAfter=10),
    "small": style("small", fontSize=8.6, leading=11.8, textColor=MUTED),
    "h1": style("h1", fontName="Sans-Bold", fontSize=19, leading=24, textColor=NAVY, spaceBefore=4,
                spaceAfter=10, keepWithNext=1),
    "tochead": style("tochead", fontName="Sans-Bold", fontSize=19, leading=24, textColor=NAVY,
                     spaceAfter=10),
    "h2": style("h2", fontName="Sans-Bold", fontSize=13, leading=17, textColor=TEAL, spaceBefore=12,
                spaceAfter=5, keepWithNext=1),
    "h3": style("h3", fontName="Sans-Semi", fontSize=10.8, leading=14.5, textColor=NAVY, spaceBefore=8,
                spaceAfter=3, keepWithNext=1),
    "bullet": style("bullet", leftIndent=14, bulletIndent=3, spaceAfter=3),
    "cell": style("cell", fontSize=8.8, leading=11.8, spaceAfter=0),
    "cellb": style("cellb", fontName="Sans-Bold", fontSize=8.8, leading=11.8, spaceAfter=0),
    "head": style("head", fontName="Sans-Bold", fontSize=8.8, leading=11.8, textColor=colors.white,
                  spaceAfter=0),
    "code": style("code", fontName="Mono", fontSize=7.9, leading=10.4, textColor=INK, spaceAfter=0),
    "callout": style("callout", fontSize=9.6, leading=13.6, spaceAfter=0),
    "caption": style("caption", fontName="Sans-Italic", fontSize=8.4, leading=11, textColor=MUTED,
                     alignment=TA_CENTER, spaceBefore=2, spaceAfter=10),
    "toc1": style("toc1", fontSize=10.5, leading=17),
    "toc2": style("toc2", fontSize=9.4, leading=13.5, leftIndent=16, textColor=MUTED),
}


def md(text, mono_size=None):
    """Tiny markup: `code`, **bold**, *italic*. Escapes & < > first."""
    spans = []

    def keep(m):
        spans.append(m.group(1))
        return "\x00%d\x00" % (len(spans) - 1)

    text = re.sub(r"`([^`]+)`", keep, text)
    text = escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", text)
    size = mono_size or 8.9

    def restore(m):
        return '<font name="Mono" size="%s" color="#8A2B1F">%s</font>' % (size, escape(spans[int(m.group(1))]))

    text = re.sub(r"\x00(\d+)\x00", restore, text)
    # Noto Sans has no arrow glyph (it would render blank): show menu paths as breadcrumbs.
    return text.replace("\u2192", "\u203a")


def P(text, st="body"):
    return Paragraph(md(text), ST[st])


def bullets(items, st="bullet"):
    return [Paragraph(md(t), ST[st], bulletText="•") for t in items]


def numbered(items):
    return [Paragraph(md(t), ST["bullet"], bulletText="%d." % (i + 1)) for i, t in enumerate(items)]


def code(text, label=None):
    """A code/output block in a light panel. Lines must stay <= ~100 chars."""
    lines = text.strip("\n").splitlines()
    assert max(len(l) for l in lines) <= 100, "code line too long: " + max(lines, key=len)
    cell = []
    if label:
        cell.append(Paragraph('<font color="#5B6770" size="7.6">%s</font>' % escape(label), ST["cell"]))
        cell.append(Spacer(1, 4))
    cell.append(Preformatted("\n".join(lines), ST["code"]))
    t = Table([[cell]], colWidths=[CONTENT_W], spaceAfter=8)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CODEBG),
        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


def callout(kind, title, body_items):
    """kind: note | warn | good | risk"""
    bar, bg = {"note": (TEAL, TEAL_L), "warn": (AMBER, AMBER_L), "good": (GREEN, GREEN_L),
               "risk": (RED, RED_L)}[kind]
    content = [Paragraph('<font name="Sans-Bold" color="%s">%s</font>' % (bar.hexval().replace("0x", "#"),
                                                                        escape(title)), ST["callout"])]
    for item in body_items:
        if isinstance(item, str):
            content.append(Paragraph(md(item), ST["callout"]))
        else:
            content.append(item)
    t = Table([[content]], colWidths=[CONTENT_W], spaceAfter=9)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("LINEBEFORE", (0, 0), (0, -1), 3.2, bar),
        ("LEFTPADDING", (0, 0), (-1, -1), 11), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def table(header, rows, widths, zebra=True, bold_first=False):
    w = [CONTENT_W * f for f in widths]
    data = [[Paragraph(md(h), ST["head"]) for h in header]]
    for r in rows:
        data.append([Paragraph(md(c), ST["cellb" if (bold_first and i == 0) else "cell"])
                     for i, c in enumerate(r)])
    t = Table(data, colWidths=w, repeatRows=1)
    cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if zebra:
        for i in range(1, len(data)):
            if i % 2 == 0:
                cmds.append(("BACKGROUND", (0, i), (-1, i), PANEL))
    t.setStyle(TableStyle(cmds))
    return t


def space(h=8):
    return Spacer(1, h)


# ---------------------------------------------------------------------------- diagrams
def _box(d, x, y, w, h, title, lines=(), fill=PANEL, stroke=LINE, tcolor=NAVY, tsize=9.2, lsize=7.6,
         radius=6):
    d.add(Rect(x, y, w, h, rx=radius, ry=radius, fillColor=fill, strokeColor=stroke, strokeWidth=0.9))
    n = len(lines)
    total = tsize + (n * (lsize + 2.6))
    top = y + h / 2 + total / 2 - tsize + 1
    d.add(String(x + w / 2, top, title, fontName="Sans-Bold", fontSize=tsize, fillColor=tcolor,
                 textAnchor="middle"))
    for i, ln in enumerate(lines):
        d.add(String(x + w / 2, top - (i + 1) * (lsize + 2.8) - 1, ln, fontName="Sans", fontSize=lsize,
                     fillColor=INK, textAnchor="middle"))


def _arrow(d, x1, y1, x2, y2, color=MUTED, width=1.2, label=None, lx=0, ly=4, lsize=7.2, head=6):
    d.add(Line(x1, y1, x2, y2, strokeColor=color, strokeWidth=width))
    import math
    ang = math.atan2(y2 - y1, x2 - x1)
    a1, a2 = ang + math.radians(152), ang - math.radians(152)
    d.add(Polygon([x2, y2, x2 + head * math.cos(a1), y2 + head * math.sin(a1),
                   x2 + head * math.cos(a2), y2 + head * math.sin(a2)],
                  fillColor=color, strokeColor=color, strokeWidth=0.5))
    if label:
        d.add(String((x1 + x2) / 2 + lx, (y1 + y2) / 2 + ly, label, fontName="Sans", fontSize=lsize,
                     fillColor=MUTED, textAnchor="middle"))


def figure(drawing, caption):
    """Drawing + caption as one unsplittable cell (no nested KeepTogether)."""
    t = Table([[[drawing, Paragraph(escape(caption), ST["caption"])]]], colWidths=[CONTENT_W],
              spaceBefore=4)
    t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                           ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    return t


# ---------------------------------------------------------------------------- document
class GuideDoc(BaseDocTemplate):
    """Letter-size document: a cover page template and a content page template with a header
    line, footer and page numbers. Headings (h1/h2) feed the outline and the TOC."""
    cover_title = "ACT Automated Triage"
    cover_lines = ("Health checks, AI diagnosis and approved fixes", "with Ansible Automation Platform")
    cover_meta = "Overview, setup guide and examples"
    header_title = "ACT Automated Triage  -  Overview and Setup Guide"
    pdf_title = "ACT Automated Triage - Overview and Setup Guide"

    def __init__(self, path):
        super().__init__(path, pagesize=letter, leftMargin=MARGIN, rightMargin=MARGIN,
                         topMargin=0.85 * inch, bottomMargin=0.8 * inch,
                         title=self.pdf_title,
                         author="ACT", subject="ACT %s with Ansible Automation Platform" % VERSION)
        frame = Frame(MARGIN, 0.8 * inch, CONTENT_W, PAGE_H - 1.65 * inch, id="f", leftPadding=0,
                      rightPadding=0, topPadding=0, bottomPadding=0)
        cover_frame = Frame(MARGIN, 0.8 * inch, CONTENT_W, PAGE_H - 1.6 * inch, id="c", leftPadding=0,
                            rightPadding=0)
        self.addPageTemplates([PageTemplate("cover", [cover_frame], onPage=self.cover_bg),
                               PageTemplate("content", [frame], onPage=self.chrome)])
        self._h1_count = 0

    @staticmethod
    def cover_bg(c, doc):
        self = doc
        c.saveState()
        c.setFillColor(NAVY)
        c.rect(0, PAGE_H - 4.1 * inch, PAGE_W, 4.1 * inch, stroke=0, fill=1)
        c.setFillColor(TEAL)
        c.rect(0, PAGE_H - 4.1 * inch - 6, PAGE_W, 6, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont("Sans-Bold", 30)
        c.drawString(MARGIN, PAGE_H - 1.75 * inch, self.cover_title)
        c.setFont("Sans", 14)
        for i, line in enumerate(self.cover_lines):
            c.drawString(MARGIN, PAGE_H - (2.2 + 0.27 * i) * inch, line)
        c.setFont("Sans", 10)
        c.setFillColor(colors.HexColor("#BFD3E6"))
        c.drawString(MARGIN, PAGE_H - 3.2 * inch,
                     "%s   |   ACT %s   |   %s" % (self.cover_meta, VERSION, DATE))
        c.setFillColor(MUTED)
        c.setFont("Sans", 8)
        c.drawString(MARGIN, 0.55 * inch, "ACT-Linux and ACT-Windows %s. Example outputs in this guide are "
                                          "illustrative; host names are placeholders." % VERSION)
        c.restoreState()

    @staticmethod
    def chrome(c, doc):
        self = doc
        c.saveState()
        c.setStrokeColor(LINE)
        c.setLineWidth(0.6)
        c.line(MARGIN, PAGE_H - 0.6 * inch, PAGE_W - MARGIN, PAGE_H - 0.6 * inch)
        c.setFont("Sans", 7.8)
        c.setFillColor(MUTED)
        c.drawString(MARGIN, PAGE_H - 0.52 * inch, self.header_title)
        c.drawRightString(PAGE_W - MARGIN, PAGE_H - 0.52 * inch, "ACT %s" % VERSION)
        c.line(MARGIN, 0.6 * inch, PAGE_W - MARGIN, 0.6 * inch)
        c.drawRightString(PAGE_W - MARGIN, 0.44 * inch, "Page %d" % doc.page)
        c.drawString(MARGIN, 0.44 * inch, DATE)
        c.restoreState()

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and flowable.style.name in ("h1", "h2"):
            level = 0 if flowable.style.name == "h1" else 1
            text = flowable.getPlainText()
            key = "k%d" % id(flowable)
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, level=level, closed=level > 0)
            if level == 0 or getattr(flowable, "_toc", False):
                self.notify("TOCEntry", (level, text, self.page, key))


def H1(text):
    return Paragraph(escape(text), ST["h1"])


def H2(text, toc=False):
    p = Paragraph(escape(text), ST["h2"])
    p._toc = toc
    return p


def H3(text):
    return Paragraph(escape(text), ST["h3"])


def section(title, first):
    """H1 (keepWithNext) followed by its first block(s)."""
    return [CondPageBreak(2.2 * inch), H1(title)] + (first if isinstance(first, list) else [first])


