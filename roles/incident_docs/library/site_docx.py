#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Write a Word document (.docx) from a plain description of it - headings, labelled bullets, bullets, numbered
steps, paragraphs, tables - with the site's logo on top and a marking banner (e.g. CUI) at the top and bottom of
every page. Standard library only (a .docx is a zip of XML files), so any execution environment runs it. Used by
the outage AAR and SOP jobs (roles/incident_docs)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_docx
short_description: Write a Word document (.docx) from a description of its blocks
description:
  - Writes O(path) - a .docx Word, LibreOffice and Google Docs open - from O(doc). Nothing else is read or written.
  - Text that XML cannot hold (control characters pasted from a terminal) is left out; line breaks split a value into
    lines of its own where the block allows it.
options:
  path: {description: The .docx file to write., type: path, required: true}
  doc:
    description: >-
      The document: banner (text at the top and bottom of every page, in bold, '' = none), banner_color (hex RRGGBB), logo (a PNG, JPEG or GIF; '' = none),
      logo_height (pixels), font, size (points), heading_color and note_color (hex RRGGBB), blocks - each a dict
      with type: heading (text), field (label, text or lines: a bullet with the label in bold; several lines become
      bullets under it; empty: the empty text in grey), bullets (items, level), steps (items: numbered from 1),
      labelled (items of label and text), para (text, bold, italic, color, size, align), note (text: small, grey, italic), table
      (columns, rows, widths: their shares, bold_first: the first column bold), title (text: large, bold), space;
      keep: true keeps a para or steps block with what follows it on the page.
    type: dict
    required: true
author: site automation
'''

RETURN = r'''
path: {description: The file written., type: str}
size: {description: Its size in bytes., type: int}
logo: {description: "The logo as placed: [width, height] in pixels; [] when there is none (or it could not be read).", type: list}
notes: {description: What was left out (a logo that could not be read)., type: list}
'''

import os
import re
import struct
import zipfile

from ansible.module_utils.basic import AnsibleModule

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
NS_DOC = ('xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" ' + W + ' ' + R + ' '
          'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
          'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
          'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"')
EMU_PER_PX = 9525                       # 914400 EMU an inch, 96 pixels an inch
# what XML 1.0 cannot hold: control characters but tab, line feed and carriage return; lone surrogates; U+FFFE/U+FFFF
_BAD = re.compile(u"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def clean(text):
    return _BAD.sub("", u"%s" % ("" if text is None else text))


def esc(text):
    return clean(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def lines_of(value):
    """A value as lines: a list as it is, text split at its line breaks (CRLF from a browser too); blank lines out."""
    if isinstance(value, (list, tuple)):
        items = [clean(x) for x in value]
    else:
        items = clean(value).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return [x.strip() for x in items if x and x.strip()]


def image_size(path):
    """(kind, width, height) of a PNG, JPEG or GIF - read from its header - or None."""
    try:
        with open(path, "rb") as f:
            head = f.read(32)
            if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
                w, h = struct.unpack(">II", head[16:24])
                return "png", w, h
            if head[:6] in (b"GIF87a", b"GIF89a"):
                w, h = struct.unpack("<HH", head[6:10])
                return "gif", w, h
            if head[:2] == b"\xff\xd8":
                f.seek(2)
                while True:
                    b = f.read(1)
                    while b and b != b"\xff":
                        b = f.read(1)
                    while b == b"\xff":
                        b = f.read(1)
                    if not b:
                        return None
                    marker = ord(b)
                    if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                        continue
                    seg = f.read(2)
                    if len(seg) < 2:
                        return None
                    n = struct.unpack(">H", seg)[0]
                    if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                        d = f.read(5)
                        h, w = struct.unpack(">HH", d[1:5])
                        return "jpeg", w, h
                    f.seek(n - 2, 1)
    except (IOError, OSError, struct.error):
        return None
    return None


class Doc(object):
    def __init__(self, d):
        self.d = d
        self.font = clean(d.get("font") or "Times New Roman")
        self.size = float(d.get("size") or 12)
        self.heading_color = re.sub("[^0-9A-Fa-f]", "", str(d.get("heading_color") or "1F2D86"))[:6] or "1F2D86"
        self.note_color = re.sub("[^0-9A-Fa-f]", "", str(d.get("note_color") or "6B7280"))[:6] or "6B7280"
        self.nums = []                  # each numbered list: its own w:num, so each one starts at 1
        self.body = []

    # ---- runs and paragraphs ----
    def run(self, text, bold=False, italic=False, color="", size=0, underline=False):
        props = []
        if bold:
            props.append("<w:b/>")
        if italic:
            props.append("<w:i/>")
        if underline:
            props.append('<w:u w:val="single"/>')
        if color:
            props.append('<w:color w:val="%s"/>' % re.sub("[^0-9A-Fa-f]", "", str(color))[:6])
        if size:
            props.append('<w:sz w:val="%d"/><w:szCs w:val="%d"/>' % (round(float(size) * 2), round(float(size) * 2)))
        rpr = ("<w:rPr>%s</w:rPr>" % "".join(props)) if props else ""
        return '<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (rpr, esc(text))

    def para(self, runs, style="", num=None, level=0, align="", before=None, after=None, keep_next=False):
        ppr = []
        if style:
            ppr.append('<w:pStyle w:val="%s"/>' % style)
        if keep_next:
            ppr.append("<w:keepNext/>")
        if num is not None:
            ppr.append('<w:numPr><w:ilvl w:val="%d"/><w:numId w:val="%d"/></w:numPr>' % (level, num))
        if before is not None or after is not None:
            ppr.append('<w:spacing%s%s/>' % ((' w:before="%d"' % before) if before is not None else "",
                                              (' w:after="%d"' % after) if after is not None else ""))
        if align:
            ppr.append('<w:jc w:val="%s"/>' % align)
        return "<w:p>%s%s</w:p>" % (("<w:pPr>%s</w:pPr>" % "".join(ppr)) if ppr else "", "".join(runs))

    def new_list(self, kind):
        """A list of its own: bullets (abstract 1) or steps (abstract 2, starting at 1)."""
        self.nums.append(kind)
        return len(self.nums) + 1               # numId 1 is the shared bullet list

    # ---- blocks ----
    def block(self, b):
        t = str(b.get("type") or "para")
        out = self.body
        if t == "title":
            out.append(self.para([self.run(b.get("text"), bold=True, size=b.get("size") or self.size + 6, color=b.get("color") or "")],
                                 align=b.get("align") or "", after=120))
        elif t == "heading":
            out.append(self.para([self.run(b.get("text"), bold=True, underline=True, color=b.get("color") or self.heading_color)],
                                 style="SiteHeading", keep_next=True))
        elif t == "field":
            vals = lines_of(b["lines"] if "lines" in b else b.get("text"))
            label = self.run(clean(b.get("label")) + " ", bold=True)
            if not vals:
                out.append(self.para([label, self.run(b.get("empty") or "Not given.", italic=True, color=self.note_color)], num=1))
            elif len(vals) == 1:
                out.append(self.para([label, self.run(vals[0], italic=bool(b.get("italic")))], num=1))
            else:
                out.append(self.para([label], num=1))
                for v in vals:
                    out.append(self.para([self.run(v, italic=bool(b.get("italic")))], num=1, level=1))
        elif t == "bullets":
            for v in lines_of(b.get("items")):
                out.append(self.para([self.run(v, italic=bool(b.get("italic")), color=b.get("color") or "")], num=1,
                                     level=int(b.get("level") or 0)))
        elif t == "steps":
            items = lines_of(b.get("items"))
            if not items:
                out.append(self.para([self.run(b.get("empty") or "None given.", italic=True, color=self.note_color)]))
                return
            n = self.new_list("steps")
            for i, v in enumerate(items):
                out.append(self.para([self.run(v, italic=bool(b.get("italic")), color=b.get("color") or "")], num=n,
                                     keep_next=bool(b.get("keep")) and i < len(items) - 1))
        elif t == "labelled":
            # bullets with a bold lead-in of their own: [{label, text}]
            for it in b.get("items") or []:
                out.append(self.para([self.run(clean(it.get("label")) + " ", bold=True, color=b.get("label_color") or ""),
                                      self.run(it.get("text"), italic=bool(b.get("italic")))], num=1))
        elif t == "note":
            out.append(self.para([self.run(b.get("text"), italic=True, color=b.get("color") or self.note_color, size=b.get("size") or self.size - 2)]))
        elif t == "space":
            out.append(self.para([]))
        elif t == "table":
            out.append(self.table(b.get("columns") or [], b.get("rows") or [], b.get("widths") or [], bool(b.get("bold_first"))))
            out.append(self.para([], after=0))
        else:
            out.append(self.para([self.run(b.get("text"), bold=bool(b.get("bold")), italic=bool(b.get("italic")),
                                           color=b.get("color") or "", size=b.get("size") or 0)], align=b.get("align") or "",
                                 keep_next=bool(b.get("keep"))))

    def table(self, columns, rows, widths=(), bold_first=False):
        n = max([len(columns)] + [len(r) for r in rows]) or 1
        total = 10080                           # the text's width (7 inches), in twentieths of a point
        try:
            parts = [float(x) for x in widths][:n]
        except (TypeError, ValueError):
            parts = []
        parts = parts + [1.0] * (n - len(parts)) if len(parts) == n or not parts else [1.0] * n
        ws = [int(total * x / sum(parts)) for x in parts]
        grid = "".join('<w:gridCol w:w="%d"/>' % w for w in ws)

        def cell(text, i, head=False):
            shade = '<w:shd w:val="clear" w:color="auto" w:fill="E5E7EB"/>' if head or (bold_first and i == 0) else ""
            paras = "".join(self.para([self.run(x, bold=head or (bold_first and i == 0))], after=0) for x in (lines_of(text) or [""]))
            return '<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/>%s</w:tcPr>%s</w:tc>' % (ws[i], shade, paras)
        trs = []
        if columns:
            trs.append('<w:tr><w:trPr><w:tblHeader/></w:trPr>%s</w:tr>' % "".join(
                cell(c, i, True) for i, c in enumerate(list(columns) + [""] * (n - len(columns)))))
        for r in rows:
            trs.append("<w:tr>%s</w:tr>" % "".join(cell(c, i) for i, c in enumerate(list(r) + [""] * (n - len(r)))))
        borders = "".join('<w:%s w:val="single" w:sz="4" w:space="0" w:color="9CA3AF"/>' % s
                          for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
        return ('<w:tbl><w:tblPr><w:tblW w:w="%d" w:type="dxa"/><w:tblBorders>%s</w:tblBorders>'
                '<w:tblCellMar><w:left w:w="80" w:type="dxa"/><w:right w:w="80" w:type="dxa"/></w:tblCellMar></w:tblPr>'
                '<w:tblGrid>%s</w:tblGrid>%s</w:tbl>' % (sum(ws), borders, grid, "".join(trs)))

    # ---- the parts of the file ----
    def logo_xml(self, cx, cy):
        return ('<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="%d" cy="%d"/>'
                '<wp:docPr id="1" name="Logo" descr="Logo"/><wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>'
                '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic>'
                '<pic:nvPicPr><pic:cNvPr id="1" name="logo"/><pic:cNvPicPr/></pic:nvPicPr>'
                '<pic:blipFill><a:blip r:embed="rIdLogo"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
                '<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="%d" cy="%d"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
                '</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>' % (cx, cy, cx, cy))

    def banner_part(self, tag):
        text = clean(self.d.get("banner") or "")
        p = self.para([self.run(text, bold=True, color=self.d.get("banner_color") or "555555", size=self.d.get("banner_size") or 11)],
                      align="center", after=0)
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:%s %s %s>%s</w:%s>' % (tag, W, R, p, tag))

    def styles(self):
        sz = round(self.size * 2)
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:styles %s>'
                '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="%s" w:hAnsi="%s" w:cs="%s" w:eastAsia="%s"/>'
                '<w:sz w:val="%d"/><w:szCs w:val="%d"/><w:lang w:val="en-US"/></w:rPr></w:rPrDefault>'
                '<w:pPrDefault><w:pPr><w:spacing w:after="40" w:line="252" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
                '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
                '<w:style w:type="paragraph" w:styleId="SiteHeading"><w:name w:val="Site Heading"/><w:basedOn w:val="Normal"/>'
                '<w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="200" w:after="80"/><w:outlineLvl w:val="0"/></w:pPr>'
                '<w:rPr><w:b/><w:u w:val="single"/><w:color w:val="%s"/></w:rPr></w:style>'
                '<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/><w:uiPriority w:val="99"/>'
                '<w:semiHidden/><w:tblPr><w:tblInd w:w="0" w:type="dxa"/><w:tblCellMar><w:top w:w="0" w:type="dxa"/>'
                '<w:left w:w="108" w:type="dxa"/><w:bottom w:w="0" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>'
                '</w:styles>' % (W, esc(self.font), esc(self.font), esc(self.font), esc(self.font), sz, sz, self.heading_color))

    def numbering(self):
        bullet = ('<w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="hybridMultilevel"/>%s</w:abstractNum>' % "".join(
            '<w:lvl w:ilvl="%d"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="%s"/><w:lvlJc w:val="left"/>'
            '<w:pPr><w:ind w:left="%d" w:hanging="360"/></w:pPr><w:rPr><w:rFonts w:ascii="%s" w:hAnsi="%s" w:hint="default"/></w:rPr></w:lvl>'
            % (i, s, 720 + 360 * i, f, f) for i, (s, f) in enumerate([(u"\u2022", "Symbol"), ("o", "Courier New"), (u"\u25aa", "Wingdings")])))
        steps = ('<w:abstractNum w:abstractNumId="2"><w:multiLevelType w:val="hybridMultilevel"/>%s</w:abstractNum>' % "".join(
            '<w:lvl w:ilvl="%d"><w:start w:val="1"/><w:numFmt w:val="%s"/><w:lvlText w:val="%%%d."/><w:lvlJc w:val="left"/>'
            '<w:pPr><w:ind w:left="%d" w:hanging="360"/></w:pPr></w:lvl>' % (i, fmt, i + 1, 720 + 360 * i)
            for i, fmt in enumerate(["decimal", "lowerLetter", "lowerRoman"])))
        nums = '<w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>' + "".join(
            '<w:num w:numId="%d"><w:abstractNumId w:val="2"/><w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/></w:lvlOverride></w:num>' % (i + 2)
            for i in range(len(self.nums)))
        return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:numbering %s>%s%s%s</w:numbering>' % (W, bullet, steps, nums)

    def write(self, path, logo_path, logo_height):
        warnings, logo, media = [], [], None
        if logo_path:
            info = image_size(logo_path)
            if not info or info[1] <= 0 or info[2] <= 0:
                warnings.append("the logo %s is not there, or not a PNG, JPEG or GIF: the document has none" % logo_path)
            else:
                kind, w, h = info
                hh = int(logo_height or 64)
                ww = max(1, int(round(w * hh / float(h))))
                if ww > 600:                    # never wider than the text
                    ww, hh = 600, max(1, int(round(h * 600.0 / w)))
                logo, media = [ww, hh], (kind, logo_path)
                self.body.append(self.para([self.logo_xml(ww * EMU_PER_PX, hh * EMU_PER_PX)], after=120))
        for b in self.d.get("blocks") or []:
            self.block(b)
        banner = bool(clean(self.d.get("banner") or "").strip())
        sect = ('<w:sectPr>%s<w:pgSz w:w="12240" w:h="15840"/>'
                '<w:pgMar w:top="1080" w:right="1080" w:bottom="1080" w:left="1080" w:header="500" w:footer="500" w:gutter="0"/>'
                '</w:sectPr>' % ('<w:headerReference w:type="default" r:id="rIdHeader"/><w:footerReference w:type="default" r:id="rIdFooter"/>'
                                 if banner else ""))
        document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:document %s><w:body>%s%s</w:body></w:document>'
                    % (NS_DOC, "".join(self.body), sect))
        rels = ['<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>',
                '<Relationship Id="rIdNumbering" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>']
        if banner:
            rels += ['<Relationship Id="rIdHeader" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>',
                     '<Relationship Id="rIdFooter" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>']
        if media:
            rels.append('<Relationship Id="rIdLogo" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                        'Target="media/logo.%s"/>' % media[0])
        overrides = ['<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>',
                     '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>',
                     '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>',
                     '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>']
        if banner:
            overrides += ['<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>',
                          '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>']
        content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                         '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                         '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                         '<Default Extension="xml" ContentType="application/xml"/>'
                         '<Default Extension="png" ContentType="image/png"/><Default Extension="jpeg" ContentType="image/jpeg"/>'
                         '<Default Extension="gif" ContentType="image/gif"/>%s</Types>' % "".join(overrides))
        root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                     '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                     '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                     '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
                     '</Relationships>')
        app = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
               '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
               '<Application>site automation</Application></Properties>')
        doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s</Relationships>' % "".join(rels))
        tmp = path + ".part"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", content_types.encode("utf-8"))
            z.writestr("_rels/.rels", root_rels.encode("utf-8"))
            z.writestr("docProps/app.xml", app.encode("utf-8"))
            z.writestr("word/document.xml", document.encode("utf-8"))
            z.writestr("word/styles.xml", self.styles().encode("utf-8"))
            z.writestr("word/numbering.xml", self.numbering().encode("utf-8"))
            z.writestr("word/_rels/document.xml.rels", doc_rels.encode("utf-8"))
            if banner:
                z.writestr("word/header1.xml", self.banner_part("hdr").encode("utf-8"))
                z.writestr("word/footer1.xml", self.banner_part("ftr").encode("utf-8"))
            if media:
                with open(media[1], "rb") as f:
                    z.writestr("word/media/logo.%s" % media[0], f.read())
        os.replace(tmp, path) if hasattr(os, "replace") else os.rename(tmp, path)
        return logo, warnings


def write_docx(path, doc):
    """Write doc to path -> (logo [w, h] or [], warnings). Pure apart from the file, for the tests."""
    return Doc(doc).write(path, str(doc.get("logo") or "").strip(), doc.get("logo_height") or 64)


def main():
    module = AnsibleModule(argument_spec=dict(path=dict(type="path", required=True), doc=dict(type="dict", required=True)),
                           supports_check_mode=True)
    p = module.params
    if module.check_mode:
        module.exit_json(changed=False, path=p["path"], size=0, logo=[], notes=[])
    try:
        logo, warnings = write_docx(p["path"], p["doc"])
    except (IOError, OSError, ValueError, TypeError, KeyError) as e:
        module.fail_json(msg="The Word document %s could not be written: %s" % (p["path"], e))
    module.exit_json(changed=True, path=p["path"], size=os.path.getsize(p["path"]), logo=logo, notes=warnings)


if __name__ == "__main__":
    main()
