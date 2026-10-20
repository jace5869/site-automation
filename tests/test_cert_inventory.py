"""plugins/filter/cert_filters.py: every certificate report's rows -> one inventory record (CSV/JSON, SQL Server)."""
import csv
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "filter"))
import cert_filters as cf  # noqa: E402


class Records(unittest.TestCase):
    def test_linux_row(self):
        r = cf.cert_records([{"host": "web01", "where": "/etc/pki/tls/certs/web.crt", "subject": "CN=web01.example.mil,O=Test PKI,C=US",
                              "issuer": "CN=Test Issuing CA,O=Test PKI,C=US", "sans": ["DNS:web01.example.mil", "IP:192.0.2.10"],
                              "expires": "2031-04-01", "days": 20, "trust": False, "key": "RSA 2048", "sig_alg": "sha256WithRSAEncryption",
                              "serial": "2002", "sha1": "48:7b:8e", "sha256": "9d4f", "tier": "ca-signed", "ca": False}],
                            "linux", 30, 7, 60, "2026-10-20T06:00:00Z", 42)[0]
        self.assertEqual(r["status"], "warning")
        self.assertEqual(r["location"], "/etc/pki/tls/certs/web.crt")
        self.assertEqual(r["sha1"], "487B8E")
        self.assertEqual(r["sha256"], "9D4F")
        self.assertIsNone(r["in_use"])
        self.assertEqual((r["source"], r["job"], r["scanned_at"]), ("linux", "42", "2026-10-20T06:00:00Z"))
        self.assertEqual(list(r.keys()), cf.FIELDS)
        self.assertEqual(r["public_key"], "RSA 2048")
        self.assertEqual(len(r["id"]), 64)

    def test_windows_names_become_openssl_names(self):
        r = cf.cert_records([{"host": "srv01", "where": "LocalMachine\\My", "subject": "CN=srv01", "issuer": "CN=CA", "days": 3,
                              "sig_alg": "sha256RSA", "serial": "0080AB", "in_use": True, "sans": ["DNS:srv01"]}], "windows")[0]
        self.assertEqual(r["sig_alg"], "sha256WithRSAEncryption")
        self.assertEqual(r["serial"], "80AB")          # Windows keeps the 00 byte DER adds, openssl does not
        self.assertEqual(r["status"], "critical")
        self.assertTrue(r["in_use"])
        self.assertEqual(r["tier"], "ca-signed")       # no tier given: from the issuer

    def test_serials(self):
        self.assertEqual(cf.norm_serial("0F965E"), "0F965E")
        self.assertEqual(cf.norm_serial("abc"), "0ABC")
        self.assertEqual(cf.norm_serial("00"), "00")
        self.assertEqual(cf.norm_serial("0x1001"), "1001")

    def test_vmware_bare_names(self):
        self.assertEqual(cf.norm_sans(["vc01.example.mil", "192.0.2.5", "2001:db8::1", "DNS:x", "IP Address:192.0.2.6", "DNS:x"]),
                         ["DNS:vc01.example.mil", "IP:192.0.2.5", "IP:2001:db8::1", "DNS:x", "IP:192.0.2.6"])
        self.assertEqual(cf.norm_sans("DNS:a, DNS:b"), ["DNS:a", "DNS:b"])

    def test_tiers(self):
        t = cf.tier_of
        self.assertEqual(t({"subject": "CN=R", "issuer": "CN=R", "ca": True, "signs": True}), "root")
        self.assertEqual(t({"subject": "CN=R", "issuer": "CN=R", "ca": True, "trust": True}), "root")
        self.assertEqual(t({"subject": "CN=S", "issuer": "CN=S", "ca": True}), "self-signed")   # openssl req -x509: CA:TRUE, signs nothing
        self.assertEqual(t({"subject": "CN=I", "issuer": "CN=R", "ca": True}), "intermediate")
        self.assertEqual(t({"subject": "CN=S", "issuer": "CN=S"}), "self-signed")
        self.assertEqual(t({"subject": "CN=W", "issuer": "CN=I"}), "ca-signed")
        self.assertEqual(t({"subject": "CN=W", "issuer": ""}), "")
        self.assertEqual(t({"tier": "Root"}), "root")

    def test_served_root_is_self_signed(self):
        row = {"host": "esx01", "where": "ESXi host", "subject": "CN=esx01", "issuer": "CN=esx01", "tier": "root", "days": 300}
        self.assertEqual(cf.cert_records([dict(row, in_use=True)], "vmware")[0]["tier"], "self-signed")
        self.assertEqual(cf.cert_records([row], "linux")[0]["tier"], "root")          # a root CA in a file stays one

    def test_status_and_order(self):
        rows = [{"host": "a", "where": "x", "subject": "CN=%d" % d, "days": d, "sha1": str(i)} for i, d in enumerate([100, -3, 50, 7, -99999])]
        recs = cf.cert_records(rows, "linux", 30, 7, 60)
        self.assertEqual([r["status"] for r in recs], ["unknown", "expired", "critical", "notice", "ok"])

    def test_one_record_per_host_place_certificate(self):
        row = {"host": "a", "where": "x", "subject": "CN=a", "sha1": "AA", "days": 5}
        self.assertEqual(len(cf.cert_records([row, dict(row), dict(row, where="y")], "linux")), 2)

    def test_id_stays(self):
        row = {"host": "a", "where": "x", "subject": "CN=a", "sha1": "AA", "days": 5}
        self.assertEqual(cf.cert_records([row], "linux")[0]["id"], cf.cert_records([dict(row, days=4)], "linux")[0]["id"])
        self.assertNotEqual(cf.cert_records([row], "linux")[0]["id"], cf.cert_records([row], "windows")[0]["id"])

    def test_flat(self):
        r = cf.cert_flat(cf.cert_records([{"host": "a", "where": "x", "subject": "CN=a", "days": None, "sans": ["DNS:a", "IP:192.0.2.1"]}],
                                          "net", scanned_at="2026-10-20T06:00:00Z"))[0]
        self.assertEqual((r["sans"], r["not_after"], r["scanned_at"]), ("DNS:a; IP:192.0.2.1", None, "2026-10-20T06:00:00"))

    def test_csv(self):
        recs = cf.cert_records([{"host": "a", "where": "x,y", "subject": 'CN=a,O="Smith, Jones"', "days": 5, "sans": ["DNS:a", "DNS:b"],
                                 "trust": True}], "linux")
        rows = list(csv.reader(io.StringIO(cf.cert_csv(recs))))
        self.assertEqual(rows[0], cf.FIELDS)
        r = dict(zip(rows[0], rows[1]))
        self.assertEqual((r["location"], r["subject"], r["sans"], r["trust_store"], r["in_use"]),
                         ("x,y", 'CN=a,O="Smith, Jones"', "DNS:a; DNS:b", "true", ""))


if __name__ == "__main__":
    unittest.main(verbosity=1)
