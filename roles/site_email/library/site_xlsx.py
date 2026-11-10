#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Write a report's tables as an Excel workbook (.xlsx) - one sheet per table, its first row the column names (bold,
frozen, with filters), numbers as numbers. For the email's attachment (roles/site_email: report_email_xlsx, or a
report's own _report_xlsx). Python standard library only (zipfile): no openpyxl needed in the execution environment."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_xlsx
short_description: Write tables as an Excel workbook (.xlsx), standard library only
description:
  - Each sheet is a table - its name, its column names, its rows. Numbers stay numbers (sums and sorting work in Excel);
    everything else is text. The first row is bold, frozen and has filters; the columns are as wide as their text (up
    to 60 characters).
  - Without path, the workbook is written to a new temporary file, returned as path (delete it when it is sent).
  - Check mode writes nothing.
options:
  sheets:
    description: "[{name, columns: [names], rows: [[values]]}] - a sheet name is cut to Excel's 31 characters, without : \\ / ? * [ ],
      made unique."
    type: list
    elements: dict
    required: true
  path: {description: "The file to write ('' = a new temporary file).", type: str, default: ''}
  banner: {description: "The classification banner, {on, text, bg, fg} (roles/site_email): each sheet's first row, across the
      table, in its colours; the column names under it. {} = none.", type: dict, default: {}}
author: site automation
'''

RETURN = r'''
path: {description: The workbook written., type: str}
sheets: {description: "Each sheet's name in the workbook and its number of rows: [[name, rows]].", type: list}
'''

import os
import re
import tempfile
import zipfile
from xml.sax.saxutils import escape

from ansible.module_utils.basic import AnsibleModule

# what XML 1.0 cannot carry (a control character in a guest's name would make Excel refuse the whole file)
BAD_XML = re.compile(u'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]')
MAX_CELL = 32767                                        # Excel's longest text in a cell

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
    '%s</Types>')
ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="xl/workbook.xml"/></Relationships>')
# style 1: the bold header row on a light green fill
# style 2: the classification banner (bold, its colours, centred) - only with a banner
STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<fonts count="3"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><color rgb="FF%(fg)s"/><name val="Calibri"/></font></fonts>'
    '<fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFE3F5EC"/><bgColor indexed="64"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FF%(bg)s"/><bgColor indexed="64"/></patternFill></fill></fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
    '<xf numFmtId="0" fontId="2" fillId="3" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1">'
    '<alignment horizontal="center"/></xf></cellXfs>'
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')


def sheet_names(names):
    """Excel's rules: at most 31 characters, none of : \\ / ? * [ ], not empty, unique (any case)."""
    out, seen = [], set()
    for i, n in enumerate(names):
        base = re.sub(r"[:\\/?*\[\]]", "-", str(n or "")).strip().strip("'")[:31].strip() or "Sheet%d" % (i + 1)
        name, k = base, 2
        while name.lower() in seen:
            tail = " (%d)" % k
            name, k = base[:31 - len(tail)] + tail, k + 1
        seen.add(name.lower())
        out.append(name)
    return out


def col_name(i):
    """0 -> A, 25 -> Z, 26 -> AA."""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _text(v):
    return BAD_XML.sub("", u"%s" % v)[:MAX_CELL]


def cell(ref, v, style=0):
    st = ' s="%d"' % style if style else ''
    if isinstance(v, bool) or v is None:
        v = "" if v is None else ("yes" if v else "no")
    if isinstance(v, (int, float)) and v == v and v not in (float("inf"), float("-inf")):
        return '<c r="%s"%s><v>%s</v></c>' % (ref, st, repr(v) if isinstance(v, float) else v)
    t = _text(v)
    space = ' xml:space="preserve"' if t != t.strip() else ''
    return '<c r="%s"%s t="inlineStr"><is><t%s>%s</t></is></c>' % (ref, st, space, escape(t))


def _rgb(c, default):
    c = str(c or "").lstrip("#")
    return c.upper() if re.match(r"^[0-9a-fA-F]{6}$", c) else default


def sheet_xml(columns, rows, banner=None):
    """A sheet; with banner ({text}), its text in the first row across the table, the column names in the second."""
    columns, rows = list(columns or []), [list(r) if isinstance(r, (list, tuple)) else [r] for r in rows or []]
    top = 1 if banner and banner.get("text") else 0      # rows above the column names
    width = max([len(columns)] + [len(r) for r in rows] or [1]) or 1
    wide = [min(max([len(_text(columns[i])) if i < len(columns) else 0]
                    + [len(_text(r[i])) for r in rows[:2000] if i < len(r) and r[i] is not None]) + 2, 60) for i in range(width)]
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">']
    if columns:
        out.append('<sheetViews><sheetView workbookViewId="0"><pane ySplit="%d" topLeftCell="A%d" activePane="bottomLeft" state="frozen"/>'
                   '</sheetView></sheetViews>' % (1 + top, 2 + top))
    out.append('<cols>%s</cols>' % ''.join('<col min="%d" max="%d" width="%d" customWidth="1"/>' % (i + 1, i + 1, max(w, 6))
                                            for i, w in enumerate(wide)))
    out.append('<sheetData>')
    n = 0
    if top:
        n += 1
        out.append('<row r="1">%s</row>' % cell('A1', banner["text"], 2))
    if columns:
        n += 1
        out.append('<row r="%d">%s</row>' % (n, ''.join(cell('%s%d' % (col_name(i), n), c, 1) for i, c in enumerate(columns))))
    for r in rows:
        n += 1
        out.append('<row r="%d">%s</row>' % (n, ''.join(cell('%s%d' % (col_name(i), n), v) for i, v in enumerate(r) if v not in (None, ''))))
    out.append('</sheetData>')
    if columns and rows:
        out.append('<autoFilter ref="A%d:%s%d"/>' % (1 + top, col_name(width - 1), n))
    if top and width > 1:
        out.append('<mergeCells count="1"><mergeCell ref="A1:%s1"/></mergeCells>' % col_name(width - 1))
    out.append('</worksheet>')
    return ''.join(out)


def write_xlsx(path, sheets, banner=None):
    """sheets [{name, columns, rows}] -> the workbook at path; [[name, rows]]. banner: {text, bg, fg} - each sheet's first row."""
    banner = banner if banner and banner.get("text") else None
    top = 1 if banner else 0
    sheets = [s for s in sheets or [] if isinstance(s, dict)] or [{"name": "Report", "columns": [], "rows": []}]
    names = sheet_names([s.get("name") for s in sheets])
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES % ''.join(
            '<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-officedocument.'
            'spreadsheetml.worksheet+xml"/>' % (i + 1) for i in range(len(sheets))))
        z.writestr("_rels/.rels", ROOT_RELS)
        z.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>%s</sheets>%s</workbook>' % (
                       ''.join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (escape(n, {'"': '&quot;'}), i + 1, i + 1)
                               for i, n in enumerate(names)),
                       '<definedNames>%s</definedNames>' % ''.join(
                           '<definedName name="_xlnm._FilterDatabase" localSheetId="%d" hidden="1">\'%s\'!$A$%d:$%s$%d</definedName>'
                           % (i, escape(n.replace("'", "''")), 1 + top, col_name(max(len(s.get("columns") or []), 1) - 1),
                              len(s.get("rows") or []) + 1 + top)
                           for i, (n, s) in enumerate(zip(names, sheets)) if s.get("columns") and s.get("rows"))
                       if any(s.get("columns") and s.get("rows") for s in sheets) else ''))
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s'
                   '<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
                   'Target="styles.xml"/></Relationships>' % (''.join(
                       '<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                       'Target="worksheets/sheet%d.xml"/>' % (i + 1, i + 1) for i in range(len(sheets))), len(sheets) + 1))
        z.writestr("xl/styles.xml", STYLES % {"bg": _rgb((banner or {}).get("bg"), "FFFFFF"), "fg": _rgb((banner or {}).get("fg"), "111111")})
        for i, s in enumerate(sheets):
            z.writestr("xl/worksheets/sheet%d.xml" % (i + 1), sheet_xml(s.get("columns"), s.get("rows"), banner))
    return [[n, len(s.get("rows") or [])] for n, s in zip(names, sheets)]


def main():
    module = AnsibleModule(argument_spec=dict(sheets=dict(type="list", elements="dict", required=True),
                                              path=dict(type="str", default=""),
                                              banner=dict(type="dict", default={})),
                           supports_check_mode=True)
    p = module.params
    if module.check_mode:
        module.exit_json(changed=False, path="", sheets=[[n, len(s.get("rows") or [])] for n, s in
                                                          zip(sheet_names([s.get("name") for s in p["sheets"]]), p["sheets"])])
    path = p["path"]
    if not path:
        fd, path = tempfile.mkstemp(prefix="site_report_", suffix=".xlsx")
        os.close(fd)
    try:
        done = write_xlsx(path, p["sheets"], p["banner"] if p["banner"].get("on", True) else None)
    except (IOError, OSError) as e:
        module.fail_json(msg="the workbook %s could not be written: %s" % (path, e))
    module.exit_json(changed=True, path=path, sheets=done)


if __name__ == "__main__":
    main()
