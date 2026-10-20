#!/usr/bin/env python3
"""Unit tests for plugins/filter/cert_order_filters.py (the certificate renewal's catalog orders):
python3 tests/test_cert_order.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "filter"))
import cert_order_filters as f  # noqa: E402

# the PKI's form, as the Service Catalog API gives its questions
Q = [{"name": "title_rank", "label": "Title/Rank:", "type": 6, "mandatory": False},
     {"name": "requested_for", "label": "Requested For", "type": 31, "mandatory": True},
     {"name": "contact", "label": "Preferred method of contact", "type": 5, "mandatory": True,
      "choices": [{"value": "", "label": "-- None --"}, {"value": "email", "label": "Email"}]},
     {"name": "location", "label": "Location:", "type": "8", "mandatory": "false", "reference": "cmn_location"},
     {"name": "info", "label": "Please provide the CSR", "type": 11, "mandatory": True},
     {"name": "box", "label": "Container", "type": 19, "children": [
         {"name": "csr", "label": "Please add CSR", "type": 33, "mandatory": True}]},
     {"name": "sans", "label": "Additional SANs", "type": 2, "mandatory": False}]


class Check(unittest.TestCase):
    def test_fits(self):
        c = f.cert_catalog_check(Q, {"requested_for": "jdoe", "contact": "Email", "location": "a" * 32, "title_rank": ["CTR", "x"]},
                                 "", "sans")
        self.assertEqual((c["errors"], c["warnings"], c["csr_var"]), ([], [], "csr"))   # the only attachment question, in a container
        self.assertEqual(c["answers"], {"requested_for": "jdoe", "contact": "email", "location": "a" * 32, "title_rank": "CTR, x"})
        self.assertEqual(c["lookups"], [{"name": "requested_for", "table": "sys_user", "value": "jdoe"}])   # a sys_id is not looked up

    def test_problems(self):
        c = f.cert_catalog_check(Q, {"contact": "Fax", "colour": "blue", "csr": "x"}, "nope", "missing")
        e = " | ".join(c["errors"])
        for want in ("cert_renew_catalog_csr_variable: the item has no question nope", "cert_renew_catalog_names_variable: the item has no question missing",
                     "the item has no question colour", "'Fax' is not one of its choices (email = Email)",
                     "requested_for (Requested For) is mandatory"):
            self.assertIn(want, e)
        self.assertNotIn("info (", e)                        # a label has nothing to fill in, mandatory or not
        self.assertNotIn("contact (Preferred method of contact) is mandatory", e)   # its wrong choice is said once
        self.assertIn("csr (Please add CSR) is mandatory", " | ".join(f.cert_catalog_check(Q, {}, "nope", "")["errors"]))
        c = f.cert_catalog_check(Q, {"requested_for": "u", "contact": "email", "csr": "x"}, "", "")
        self.assertIn("csr is the CSR's question - the job fills it in", " ".join(c["errors"]))
        c = f.cert_catalog_check(Q, {"requested_for": "u", "contact": "email"}, "title_rank", "")
        self.assertIn("title_rank is a single line text question, not an attachment", " ".join(c["errors"]))

    def test_attachment_questions(self):
        two = Q + [{"name": "csr2", "type": 33}]
        self.assertIn("2 attachment questions (csr, csr2)", " ".join(f.cert_catalog_check(two, {}, "", "")["errors"]))
        none = [q for q in Q if q["name"] != "box"]
        c = f.cert_catalog_check(none, {"requested_for": "u", "contact": "email"}, "", "")
        self.assertEqual((c["errors"], c["csr_var"]), ([], ""))
        self.assertIn("the CSR is attached to the request", c["warnings"][0])

    def test_resolve(self):
        c = f.cert_catalog_check(Q, {"requested_for": "jdoe", "contact": "email", "location": "Building 9"}, "", "")
        r = f.cert_catalog_resolve(c, [{"name": "requested_for", "table": "sys_user", "value": "jdoe",
                                        "rows": [{"sys_id": "u2", "user_name": "jdoe", "name": "Jane Doe"}]},
                                       {"name": "location", "table": "cmn_location", "value": "Building 9", "rows": []}])
        self.assertEqual(r["answers"]["requested_for"], "u2")
        self.assertIn("location: no record named 'Building 9' in cmn_location - give its sys_id instead", r["errors"])
        # a table without a name column: ServiceNow may ignore the condition and answer anything - not taken as a match
        r = f.cert_catalog_resolve(c, [{"name": "location", "table": "u_dir", "value": "X", "rows": [{"sys_id": "1"}, {"sys_id": "2"}]}])
        self.assertIn("no record named 'X' in u_dir", " ".join(r["errors"]))
        r = f.cert_catalog_resolve(c, [{"name": "location", "table": "cmn_location", "value": "B",
                                        "rows": [{"sys_id": "1", "name": "b"}, {"sys_id": "2", "name": "B"}]}])
        self.assertIn("2 records named 'B' in cmn_location - give the sys_id of the right one (1, 2)", " ".join(r["errors"]))


class Names(unittest.TestCase):
    def test_name_and_query(self):
        self.assertEqual(f.cert_catalog_date("2026-11-14 10:00 UTC"), "20261114")
        self.assertEqual(f.cert_catalog_name("web01.example.mil-1a2b3c4d.csr", "2026-11-14"), "web01.example.mil-1a2b3c4d-exp20261114.csr")
        self.assertEqual(f.cert_catalog_name("DC0_H0-1a2b3c4d.txt", "2026-11-14T10:00:00Z"), "DC0_H0-1a2b3c4d-exp20261114.txt")
        self.assertEqual(f.cert_catalog_name("x.csr", ""), "x.csr")
        self.assertEqual(f.cert_catalog_query("DC0_H0-", "2026-11-14", ["sc_req_item"]),
                         "file_nameSTARTSWITHDC0_H0-^file_nameLIKE-exp20261114.^table_nameINsc_req_item,ZZ_YYsc_req_item")
        self.assertEqual(f.cert_catalog_query("x", "", None), "file_nameSTARTSWITHx^table_nameINsc_req_item,ZZ_YYsc_req_item")

    def test_vars(self):
        cert = {"host": "web01", "subject": "CN=web01", "sans": ["DNS:web01", "DNS:web"], "expires": "2026-11-14", "wheres": ["/a", "/b"]}
        v = f.cert_catalog_vars(cert, {"why": "Renew {subject} on {host} ({expires}) in {where}", "n": 1}, "csr", "att1", "sans")
        self.assertEqual(v, {"why": "Renew CN=web01 on web01 (2026-11-14) in /a ; /b", "n": "1", "sans": "DNS:web01, DNS:web", "csr": "att1"})
        self.assertNotIn("csr", f.cert_catalog_vars(cert, {}, "csr", "", ""))
        self.assertEqual(f.cert_catalog_state("4"), "Closed Incomplete")
        self.assertEqual(f.cert_catalog_state(9), "9")

    def test_questions(self):
        qs = f.cert_catalog_questions(Q)
        self.assertEqual([q["name"] for q in qs], ["title_rank", "requested_for", "contact", "location", "info", "box", "csr", "sans"])
        self.assertEqual(next(q for q in qs if q["name"] == "requested_for")["reference"], "sys_user")
        self.assertEqual(next(q for q in qs if q["name"] == "csr")["type_name"], "attachment")
        self.assertFalse(next(q for q in qs if q["name"] == "info")["answer"])
        self.assertEqual(f._type({"type": "x", "friendly_type": "attachment"}), 33)
        vs = f.cert_catalog_questions([{"name": "standard_questions", "label": "Standard Employee Questions", "type": 0}])[0]
        self.assertEqual((vs["type_name"], vs["answer"]), ("variable set", False))
        html = '<p><span style="x"><strong>*</strong>Note</span>: To add&nbsp;CSR files <img src="a.svgx" /></p>'
        self.assertEqual(f.cert_catalog_questions([{"name": "n", "label": html, "type": 32}])[0]["label"], "* Note : To add CSR files")


if __name__ == "__main__":
    unittest.main()
