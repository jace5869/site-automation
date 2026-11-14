#!/usr/bin/env python3
"""1.62.0: trend lines in the workbooks as Excel's own sparklines (roles/site_email/library/site_xlsx.py: a sheet's
sparklines - points in hidden columns right of the table, a row's own cells, or one line down a column), and the reports
that pass them: NetApp ONTAP capacity trends, VMware capacity, the STIG scorecard, About to breach, patch age.
The XML is well formed, the sparkline groups are in the sheet with the right ranges, a row with one point has none, the
hidden block stays hidden; openpyxl opens the file, LibreOffice reads the sparklines (each part skips without it).
  python3 tests/test_xlsx_sparklines.py"""
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
sys.path.insert(0, os.path.join(REPO, "roles", "site_email", "library"))
import site_xlsx as xl  # noqa: E402
import site_filters as sf  # noqa: E402
import capacity_filters as cf  # noqa: E402
import patch_age_filters as pa  # noqa: E402
import servicenow_insights_filters as snf  # noqa: E402
import test_capacity as tc  # noqa: E402
import test_ontap_capacity as to  # noqa: E402
import test_patch_age as tp  # noqa: E402
import test_servicenow_breach as tsb  # noqa: E402
import test_stig_scorecard as ts  # noqa: E402

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "x14": "http://schemas.microsoft.com/office/spreadsheetml/2009/9/main",
      "xm": "http://schemas.microsoft.com/office/excel/2006/main"}
BANNER = {"text": "UNCLASSIFIED", "bg": "007a33", "fg": "ffffff"}


def write(sheets, banner=None):
    d = tempfile.mkdtemp(prefix="xlsx_spark_")
    p = os.path.join(d, "t.xlsx")
    xl.write_xlsx(p, sheets, banner)
    return p


def sheet_root(path, i=1):
    with zipfile.ZipFile(path) as z:
        return ET.fromstring(z.read("xl/worksheets/sheet%d.xml" % i))


def lines(path, i=1):
    """[(data range, cell)] of a sheet's sparklines."""
    root = sheet_root(path, i)
    return [(s.find("xm:f", NS).text, s.find("xm:sqref", NS).text) for s in root.iter("{%s}sparkline" % NS["x14"])]


def soffice():
    return shutil.which("soffice") or shutil.which("libreoffice")


class Writer(unittest.TestCase):
    def test_points_in_a_hidden_block(self):
        p = write([{"name": "Vol's trend", "columns": ["Name", "Used %"], "rows": [["a", 50], ["b", 60], ["c", 70]],
                    "sparklines": [{"column": "Trend", "points": [[1, 2, 3, 5], [4], [3, None, 5]]}]}], BANNER)
        # the banner on row 1, the names on row 2: data from row 3; Trend added at the end (C), the points from D
        self.assertEqual(lines(p), [("'Vol''s trend'!D3:G3", "C3"), ("'Vol''s trend'!D5:F5", "C5")])   # b: one point, no line
        root = sheet_root(p)
        hidden = [c.attrib for c in root.find("m:cols", NS) if c.get("hidden") == "1"]
        self.assertEqual(hidden, [{"min": "4", "max": "7", "width": "8", "hidden": "1", "customWidth": "1"}])
        self.assertEqual(root.find("m:autoFilter", NS).get("ref"), "A2:C5")          # the filter: the table, not the block
        self.assertEqual(root.find("m:mergeCells/m:mergeCell", NS).get("ref"), "A1:C1")
        heads = [c.find("m:is/m:t", NS).text for c in root.find("m:sheetData", NS)[1]]
        self.assertEqual(heads, ["Name", "Used %", "Trend", "Trend 1", "Trend 2", "Trend 3", "Trend 4"])
        cells = {c.get("r"): c for c in root.iter("{%s}c" % NS["m"])}
        self.assertNotIn("E5", cells)                                               # the gap stays empty
        self.assertNotIn("C3", cells)                                               # the line's cell has no value
        g = root.find(".//x14:sparklineGroup", NS)
        self.assertEqual((g.get("displayHidden"), g.get("displayEmptyCellsAs"), g.get("last"), g.get("type")), ("1", "gap", "1", None))
        self.assertEqual(g.find("x14:colorSeries", NS).get("rgb"), "FF2563EB")      # the web's --spark (atlas, light)
        self.assertIsNone(g.get("displayXAxis"))                                    # no axis

    def test_a_rows_cells_and_one_line_down_a_column(self):
        p = write([{"name": "Weeks", "columns": ["Name", "W1", "W2", "W3", "Trend"], "rows": [["a", 1, 2, 3], ["b", 1, "-", "-"]],
                    "sparklines": [{"column": "Trend", "from": ["W1", "W3"]}]},
                   {"name": "By day", "columns": ["Day", "Breached"], "rows": [["d%d" % i, i % 4] for i in range(30)],
                    "sparklines": [{"column": "Trend", "down": "Breached"}]}])
        self.assertEqual(lines(p, 1), [("'Weeks'!B2:D2", "E2")])                    # b has one number: no line
        self.assertEqual(lines(p, 2), [("'By day'!B2:B31", "C2")])
        self.assertIsNone(sheet_root(p, 1).find("m:cols/m:col[@hidden]", NS))

    def test_without_sparklines_as_before(self):
        p = write([{"name": "VMs", "columns": ["VM", "CPU"], "rows": [["a", 1]]},
                   {"name": "One", "columns": ["VM", "Trend"], "rows": [["a"]], "sparklines": [{"column": "Trend", "points": [[1]]}]}])
        for i in (1, 2):
            with zipfile.ZipFile(p) as z:
                self.assertNotIn(b"extLst", z.read("xl/worksheets/sheet%d.xml" % i))

    def test_every_part_is_xml(self):
        p = write([{"name": "A", "columns": ["N"], "rows": [["x"]], "sparklines": [{"column": "Trend", "points": [[1, 2]]}]}], BANNER)
        with zipfile.ZipFile(p) as z:
            for n in z.namelist():
                ET.fromstring(z.read(n))
            wb = z.read("xl/workbook.xml").decode()
        self.assertIn("'A'!$A$2:$B$3", wb)                                          # the filter's name: the table with Trend

    def test_python36(self):
        with open(xl.__file__) as f:
            src = f.read()
        self.assertFalse(re.search(r"\bf\"|\bf'|:=|\bnonlocal\b", src))             # the module runs in any execution environment

    def test_openpyxl_opens_it(self):
        try:
            import openpyxl
        except ImportError:
            self.skipTest("no openpyxl")
        p = write([{"name": "Vol", "columns": ["Name"], "rows": [["a"]], "sparklines": [{"column": "Trend", "points": [[1, 2, 3]]}]}], BANNER)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")                                           # it does not keep sparklines, and says so
            ws = openpyxl.load_workbook(p).worksheets[0]
        self.assertEqual([c.value for c in ws[2]], ["Name", "Trend", "Trend 1", "Trend 2", "Trend 3"])
        self.assertEqual([c.value for c in ws[3]], ["a", None, 1, 2, 3])
        self.assertTrue(ws.column_dimensions["C"].hidden)

    def test_libreoffice_reads_the_lines(self):
        if not soffice():
            self.skipTest("no LibreOffice")
        p = write([{"name": "Vol", "columns": ["Name", "Used %"], "rows": [["a", 1], ["b", 2], ["c", 3]],
                    "sparklines": [{"column": "Trend", "points": [[1, 2, 3], [5], [4, None, 6]]}]},
                   {"name": "By day", "columns": ["Day", "Breached"], "rows": [["d1", 1], ["d2", 3]],
                    "sparklines": [{"column": "Trend", "down": "Breached"}]}], BANNER)
        d = os.path.dirname(p)
        r = subprocess.run([soffice(), "-env:UserInstallation=file://%s/profile" % d, "--headless", "--convert-to", "ods",
                            "--outdir", d, p], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=240)
        self.assertEqual(r.returncode, 0, r.stdout)
        with zipfile.ZipFile(os.path.join(d, "t.ods")) as z:
            content = z.read("content.xml").decode()
        got = [(html.unescape(a), html.unescape(b)) for a, b in
               re.findall(r'<calcext:sparkline calcext:cell-address="([^"]+)" calcext:data-range="([^"]+)"', content)]
        self.assertEqual(got, [("Vol.C3", "Vol.D3:Vol.F3"), ("Vol.C5", "Vol.D5:Vol.F5"), ("'By day'.C3", "'By day'.B3:'By day'.B4")])
        self.assertIn('calcext:display-hidden="true"', content)
        self.assertIn('calcext:color-series="#2563eb"', content)


class Reports(unittest.TestCase):
    def test_ontap_volumes_and_the_sections_lines(self):
        x = to.report()["xlsx"]
        vols = next(s for s in x if s["name"] == "Volumes")
        pts = vols["sparklines"][0]["points"]
        self.assertEqual(len(pts), len(vols["rows"]))
        for row, p in zip(vols["rows"], pts):
            self.assertEqual(p[-1], row[5])                                          # the last point: Used % now
            self.assertLessEqual(len(p), 60)
        aggr = next(s for s in x if s["name"] == "Aggregates")
        self.assertEqual(len(aggr["sparklines"][0]["points"]), len(aggr["rows"]))
        self.assertTrue(all(len(p) >= 2 for p in aggr["sparklines"][0]["points"]))
        self.assertNotIn("sparklines", next(s for s in x if s["name"] == "Points"))
        n = len([p for p in pts if len([v for v in p if v is not None]) >= 2])
        self.assertEqual(len(lines(write([vols]))), n)

    def test_vmware_capacity(self):
        r = cf.vm_capacity_report(tc.board_data(), tc.BOPTS)
        plain = sf.report_xlsx_sheets(r["report"])
        sheets = cf.vm_capacity_sheets(sf.report_xlsx_sheets(r["report"], r["sheets"]), r["sheet"])
        self.assertEqual([s["name"] for s in sheets], [s["name"] for s in plain])         # nothing removed, the order kept
        by = {s["name"]: s for s in sheets}
        cl = by["Runway: the first limit per cluster"]
        self.assertEqual([x["column"] for x in cl["sparklines"]], ["CPU trend", "Memory trend", "Storage trend"])
        self.assertEqual(cl["rows"], by["Runway: the first limit per cluster"]["rows"])
        self.assertEqual(cl["columns"], next(s for s in plain if s["name"] == cl["name"])["columns"])
        soon = [r_ for r_ in r["web"]["sections"][0]["board"]["rows"] if r_["name"] == "SOON"][0]
        i = [x[0] for x in cl["rows"]].index("SOON")
        mem = [c for c in soon["cells"] if c["res"] == "Memory"][0]
        self.assertEqual(cl["sparklines"][1]["points"][i], [v for t, v in mem["series"]])
        ds = by["Runway: datastores"]
        self.assertEqual(len(ds["sparklines"][0]["points"]), len(ds["rows"]))
        nb = by["The numbers behind the lines"]
        self.assertEqual(len(nb["sparklines"][0]["points"]), len(nb["rows"]))
        p = write(sheets)
        i = [s["name"] for s in sheets].index(cl["name"]) + 1
        got = lines(p, i)
        self.assertTrue(got)
        name = xl.sheet_names([s["name"] for s in sheets])[i - 1]                       # the sheet's name in Excel: 31 characters, no ":"
        self.assertEqual(name, "Runway- the first limit per clu")
        self.assertTrue(all(f.startswith("'%s'!" % name) for f, c in got))

    def test_stig_scorecard_open_per_week(self):
        d = ts.card()
        by = d["xlsx"][0]
        self.assertEqual(by["name"], "By collection")
        pts = by["sparklines"][0]["points"]
        week = next(s for s in d["xlsx"] if s["name"] == "Open per week")
        for name, p in zip([r[0] for r in by["rows"]], pts):
            row = next(r for r in week["rows"] if r[0] == name)
            self.assertEqual(p, [None if v == "-" else v for v in row[1:]])
        self.assertEqual(len(lines(write([by]))), len([p for p in pts if len([v for v in p if v is not None]) >= 2]))
        first = ts.card(runs=[])["xlsx"][0]                                           # the first run: one point, no line
        self.assertFalse(lines(write([first])))

    def test_breaches_by_day(self):
        m = snf.breach_model(tsb.seed_input(), {})
        day = next(s for s in snf.breach_xlsx(m) if s["name"] == "Breaches by day")
        self.assertEqual(day["sparklines"], [{"column": "Trend", "down": "Breached"}])
        self.assertEqual(lines(write([day])), [("'Breaches by day'!B2:B%d" % (len(day["rows"]) + 1), "F2")])
        self.assertLess(day["rows"][0][0], day["rows"][-1][0])                          # oldest first: the line reads left to right

    def test_patch_age_by_group(self):
        runs = [tp.run(120, {"SITEA-linux-web": [5, 1]}), tp.run(80, {"SITEA-linux-web": [5, 4]}), tp.run(10, {"SITEA-linux-web": [5, 5]}), tp.run(5)]
        r = pa.patch_age_sections(tp.Sections().fleet(), {"what": "Linux", "now": tp.NOW}, runs)
        g = r["group_sheet"]
        self.assertEqual((g["name"], g["columns"], g["rows"]), (r["sections"][0]["title"], r["sections"][0]["columns"], r["sections"][0]["rows"]))
        self.assertEqual(g["sparklines"][0]["points"], [[], [80, 100, 40]])        # db: one run, no line
        sheets = sf.report_xlsx_sheets({"sections": r["sections"]}, [g])
        self.assertEqual([s["name"] for s in sheets], [s["title"] for s in r["sections"]])
        self.assertIs(sheets[0], g)
        self.assertEqual(lines(write(sheets)), [("'Patch age by group'!K3:M3", "J3")])

    def test_own_sheets_not_in_the_report_follow(self):
        rep = {"sections": [{"title": "A", "columns": ["x"], "rows": [[1]]}]}
        self.assertEqual([s["name"] for s in sf.report_xlsx_sheets(rep, [{"name": "B", "columns": ["y"], "rows": []}])], ["A", "B"])
        self.assertEqual(sf.report_xlsx_sheets(rep), sf.report_xlsx_sheets(rep, []))


if __name__ == "__main__":
    unittest.main()
