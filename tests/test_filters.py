"""Unit tests for plugins/filter/site_filters.py (standard library only: python3 tests/test_filters.py)."""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "filter"))
import site_filters as f  # noqa: E402


class FromCsv(unittest.TestCase):
    def test_rows_headers_bom_blank(self):
        text = "﻿POAM ID , Status\n1, Ongoing\n,\n2,\"Completed\"\n"
        self.assertEqual(f.from_csv(text), [{"POAM ID": "1", "Status": "Ongoing"},
                                            {"POAM ID": "2", "Status": "Completed"}])

    def test_quoted_commas(self):
        rows = f.from_csv('ID,Vulnerability IDs\n7,"V-1, V-2"\n')
        self.assertEqual(rows[0]["Vulnerability IDs"], "V-1, V-2")

    def test_empty(self):
        self.assertEqual(f.from_csv(""), [])
        self.assertEqual(f.from_csv(None), [])


class DaysUntil(unittest.TestCase):
    def test_formats(self):
        today = datetime.date.today()
        fmts = ["%Y-%m-%d", "%m/%d/%Y"]
        self.assertEqual(f.days_until((today + datetime.timedelta(days=3)).isoformat(), fmts), 3)
        self.assertEqual(f.days_until((today - datetime.timedelta(days=2)).strftime("%m/%d/%Y"), fmts), -2)

    def test_not_a_date(self):
        self.assertIsNone(f.days_until("TBD", ["%Y-%m-%d"]))
        self.assertIsNone(f.days_until("", ["%Y-%m-%d"]))



class SiteResult(unittest.TestCase):
    def test_last_marker_line_wins(self):
        lines = ["noise", '###SITE-JSON### {"a": 1}', "more", '###SITE-JSON### {"a": 2}']
        self.assertEqual(f.site_result(lines), {"a": 2})

    def test_string_and_defaults(self):
        self.assertEqual(f.site_result('x\n###SITE-JSON### {"b": [1]}\n'), {"b": [1]})
        self.assertEqual(f.site_result([]), {})
        self.assertEqual(f.site_result(["###SITE-JSON### null"], {"x": 0}), {"x": 0})
        self.assertEqual(f.site_result(["###SITE-JSON### {not json"], {"x": 0}), {"x": 0})
        self.assertEqual(f.site_result(None), {})

if __name__ == "__main__":
    unittest.main()
