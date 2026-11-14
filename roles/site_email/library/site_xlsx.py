#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Write a report's tables as an Excel workbook (.xlsx) - one sheet per table, its first row the column names (bold,
frozen, with filters), numbers as numbers, a trend as a native Excel sparkline (1.62.0). For the email's attachment (roles/site_email: report_email_xlsx, or a
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
  - A sheet may have trend lines (sparklines) - Excel's own, one line in a cell drawn from cells of the sheet (Excel 2010
    and later; LibreOffice). The numbers stay in cells, so sort, filter and copy work as before.
  - Check mode writes nothing.
options:
  sheets:
    description: "[{name, columns: [names], rows: [[values]], sparklines}] - a sheet name is cut to Excel's 31 characters, without
      : \\ / ? * [ ], made unique. sparklines (optional): [{column, points | from | down}] - column: the column the lines are in (a
      name; one not in columns is added at the end); points: [[numbers] per row] - each row's points in hidden columns right of
      the table (None: a gap); from: [first, last] - each row's own cells from column first to last; down: a column - one line over
      its cells, top to bottom, in the first row. A row with fewer than 2 numbers gets no line."
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
SPARK_RGB = "FF2563EB"                                  # the website's --spark (atlas, light): one colour, the last point marked
SPARK_WIDE = 18                                         # a sparkline column's width (characters)
# Excel 2010's extension for sparklines (x14): the sheet's extLst, after everything else; displayHidden: drawn from hidden points too
X14_SPARK = ('<extLst><ext uri="{05C60535-1F16-4fd2-B633-F4F36F0B64E0}" '
             'xmlns:x14="http://schemas.microsoft.com/office/spreadsheetml/2009/9/main">'
             '<x14:sparklineGroups xmlns:xm="http://schemas.microsoft.com/office/excel/2006/main">%s</x14:sparklineGroups></ext></extLst>')
X14_GROUP = ('<x14:sparklineGroup displayEmptyCellsAs="gap" displayHidden="1" lineWeight="1.25" last="1">'
             '<x14:colorSeries rgb="%(c)s"/><x14:colorNegative rgb="%(c)s"/><x14:colorAxis rgb="FF000000"/>'
             '<x14:colorMarkers rgb="%(c)s"/><x14:colorFirst rgb="%(c)s"/><x14:colorLast rgb="%(c)s"/>'
             '<x14:colorHigh rgb="%(c)s"/><x14:colorLow rgb="%(c)s"/><x14:sparklines>%(lines)s</x14:sparklines></x14:sparklineGroup>')

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


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v not in (float("inf"), float("-inf"))


def cell(ref, v, style=0):
    st = ' s="%d"' % style if style else ''
    if isinstance(v, bool) or v is None:
        v = "" if v is None else ("yes" if v else "no")
    if _number(v):
        return '<c r="%s"%s><v>%s</v></c>' % (ref, st, repr(v) if isinstance(v, float) else v)
    t = _text(v)
    space = ' xml:space="preserve"' if t != t.strip() else ''
    return '<c r="%s"%s t="inlineStr"><is><t%s>%s</t></is></c>' % (ref, st, space, escape(t))


def _rgb(c, default):
    c = str(c or "").lstrip("#")
    return c.upper() if re.match(r"^[0-9a-fA-F]{6}$", c) else default


def _col(columns, want):
    """A column by its name (or its index) -> its index, or None."""
    if isinstance(want, int) and not isinstance(want, bool):
        return want if 0 <= want < len(columns) else None
    names = [u"%s" % c for c in columns]
    return names.index(u"%s" % want) if u"%s" % want in names else None


def spark_plan(columns, rows, sparklines=None):
    """A sheet's trend lines laid out -> {columns (with each line's column), rows, helper: [[values] per row] (the hidden
    points right of the table), helper_heads, spark_cols (indexes), lines: [[group, (col, row), (c1, r1, c2, r2)]]} - rows
    and columns 0-based in the table's data (row 0: the first under the column names)."""
    columns = list(columns or [])
    rows = [list(r) if isinstance(r, (list, tuple)) else [r] for r in rows or []]
    specs = [x for x in sparklines or [] if isinstance(x, dict) and x.get("column") not in (None, "")]
    if not specs or not columns:
        return {"columns": columns, "rows": rows, "helper": [[] for r in rows], "helper_heads": [], "spark_cols": [], "lines": [],
                "width": max([len(columns)] + [len(r) for r in rows])}
    for x in specs:
        if _col(columns, x["column"]) is None:
            columns.append(u"%s" % x["column"])
    width = max([len(columns)] + [len(r) for r in rows])
    helper, heads, lines, spark_cols = [[] for r in rows], [], [], []
    for g, x in enumerate(specs):
        at = _col(columns, x["column"])
        spark_cols.append(at)
        if x.get("points") is not None:
            pts = [list(p) if isinstance(p, (list, tuple)) else [] for p in x.get("points") or []]
            n = max([len(p) for p in pts] or [0])
            if not n:
                continue
            start = width + len(heads)
            heads += [u"%s %d" % (x["column"], i + 1) for i in range(n)]
            for i, r in enumerate(helper):
                p = pts[i] if i < len(pts) else []
                r += [v if _number(v) else None for v in p] + [None] * (n - len(p))
                filled = [k for k, v in enumerate(p) if _number(v)]
                if len(filled) >= 2:
                    lines.append([g, (at, i), (start + filled[0], i, start + filled[-1], i)])
        elif x.get("from"):
            a, b = _col(columns, x["from"][0]), _col(columns, x["from"][-1])
            if a is None or b is None:
                continue
            a, b = min(a, b), max(a, b)
            for i, r in enumerate(rows):
                if len([v for v in r[a:b + 1] if _number(v)]) >= 2:
                    lines.append([g, (at, i), (a, i, b, i)])
        elif x.get("down") not in (None, ""):
            c = _col(columns, x["down"])
            if c is not None and rows and len([r[c] for r in rows if c < len(r) and _number(r[c])]) >= 2:
                lines.append([g, (at, 0), (c, 0, c, len(rows) - 1)])
    return {"columns": columns, "rows": rows, "helper": helper if heads else [[] for r in rows], "helper_heads": heads,
            "spark_cols": spark_cols, "lines": lines, "width": width}


def _spark_xml(name, plan, top):
    """The sheet's sparklines as Excel 2010's x14 extension ('' without any): a group per line column."""
    if not plan["lines"]:
        return ''
    first = 2 + top                                       # the first data row's number
    sheet = "'%s'" % escape(name.replace("'", "''"))
    groups = []
    for g in sorted(set(x[0] for x in plan["lines"])):
        mine = ''.join('<x14:sparkline><xm:f>%s!%s%d:%s%d</xm:f><xm:sqref>%s%d</xm:sqref></x14:sparkline>' % (
            sheet, col_name(c1), r1 + first, col_name(c2), r2 + first, col_name(at[0]), at[1] + first)
            for gg, at, (c1, r1, c2, r2) in plan["lines"] if gg == g)
        groups.append(X14_GROUP % {"c": SPARK_RGB, "lines": mine})
    return X14_SPARK % ''.join(groups)


def sheet_xml(columns, rows, banner=None, sparklines=None, name="Sheet1"):
    """A sheet; with banner ({text}), its text in the first row across the table, the column names in the second; with
    sparklines (spark_plan), the lines' columns and their hidden points."""
    plan = spark_plan(columns, rows, sparklines)
    columns, rows, helper, heads = plan["columns"], plan["rows"], plan["helper"], plan["helper_heads"]
    top = 1 if banner and banner.get("text") else 0      # rows above the column names
    width = max([len(columns)] + [len(r) for r in rows] or [1]) or 1
    wide = [min(max([len(_text(columns[i])) if i < len(columns) else 0]
                    + [len(_text(r[i])) for r in rows[:2000] if i < len(r) and r[i] is not None]) + 2, 60) for i in range(width)]
    for c in plan["spark_cols"]:
        if c is not None and c < width:
            wide[c] = max(wide[c], SPARK_WIDE)
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">']
    if columns:
        out.append('<sheetViews><sheetView workbookViewId="0"><pane ySplit="%d" topLeftCell="A%d" activePane="bottomLeft" state="frozen"/>'
                   '</sheetView></sheetViews>' % (1 + top, 2 + top))
    out.append('<cols>%s%s</cols>' % (''.join('<col min="%d" max="%d" width="%d" customWidth="1"/>' % (i + 1, i + 1, max(w, 6))
                                              for i, w in enumerate(wide)),
                                      '<col min="%d" max="%d" width="8" hidden="1" customWidth="1"/>' % (width + 1, width + len(heads))
                                      if heads else ''))
    out.append('<sheetData>')
    n = 0
    if top:
        n += 1
        out.append('<row r="1">%s</row>' % cell('A1', banner["text"], 2))
    if columns:
        n += 1
        out.append('<row r="%d">%s%s</row>' % (n, ''.join(cell('%s%d' % (col_name(i), n), c, 1) for i, c in enumerate(columns)),
                                               ''.join(cell('%s%d' % (col_name(width + i), n), c, 1) for i, c in enumerate(heads))))
    for r, h in zip(rows, helper):
        n += 1
        out.append('<row r="%d">%s%s</row>' % (n, ''.join(cell('%s%d' % (col_name(i), n), v) for i, v in enumerate(r) if v not in (None, '')),
                                               ''.join(cell('%s%d' % (col_name(width + i), n), v) for i, v in enumerate(h) if v is not None)))
    out.append('</sheetData>')
    if columns and rows:
        out.append('<autoFilter ref="A%d:%s%d"/>' % (1 + top, col_name(width - 1), n))
    if top and width > 1:
        out.append('<mergeCells count="1"><mergeCell ref="A1:%s1"/></mergeCells>' % col_name(width - 1))
    out.append(_spark_xml(name, plan, top))
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
                           % (i, escape(n.replace("'", "''")), 1 + top, col_name(max(spark_plan(s.get("columns"), s.get("rows"), s.get("sparklines"))["width"]
                                                                                if s.get("sparklines") else len(s.get("columns") or []), 1) - 1),
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
            z.writestr("xl/worksheets/sheet%d.xml" % (i + 1), sheet_xml(s.get("columns"), s.get("rows"), banner, s.get("sparklines"), names[i]))
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
