#!/usr/bin/env python3
"""Unit tests for the OCSP responders read from the certificates (plugins/filter/ocsp_filters.py): the OCSP address
and the issuer from a test certificate's Authority Information Access, the issuer served as DER, PEM or .p7c by a
local web server, a cert file with its chain, and what is said when something is missing.
    python3 tests/test_ocsp_target.py"""
import datetime
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import AuthorityInformationAccessOID, NameOID

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
import ocsp_filters as of  # noqa: E402

SERVED = {}


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = SERVED.get(self.path)
        self.send_response(200 if body is not None else 404)
        self.end_headers()
        self.wfile.write(body or b"")

    def log_message(self, *a):
        pass


def name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def make(cn, issuer_cn, key, signer, ca=False, aia=None):
    now = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    b = (x509.CertificateBuilder().subject_name(name(cn)).issuer_name(name(issuer_cn)).public_key(key.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=3650))
         .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
    if aia:
        b = b.add_extension(x509.AuthorityInformationAccess([x509.AccessDescription(m, x509.UniformResourceIdentifier(u))
                                                             for m, u in aia]), critical=False)
    return b.sign(signer, hashes.SHA256())


def pem(c):
    return c.public_bytes(serialization.Encoding.PEM).decode("ascii")


class Target(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:%d" % cls.srv.server_port
        cls.ca_key, cls.other_key, key = (ec.generate_private_key(ec.SECP256R1()) for _ in range(3))
        cls.ca = make("Test ID CA-1", "Test ID CA-1", cls.ca_key, cls.ca_key, ca=True)
        other = make("Another CA", "Another CA", cls.other_key, cls.other_key, ca=True)
        SERVED["/ca.cer"] = cls.ca.public_bytes(serialization.Encoding.DER)
        SERVED["/ca.pem"] = pem(cls.ca).encode("ascii")
        SERVED["/ca.p7c"] = pkcs7.serialize_certificates([other, cls.ca], serialization.Encoding.DER)
        SERVED["/other.cer"] = other.public_bytes(serialization.Encoding.DER)

        def user(path):
            return pem(make("test.user", "Test ID CA-1", key, cls.ca_key, aia=[
                (AuthorityInformationAccessOID.CA_ISSUERS, base + path), (AuthorityInformationAccessOID.OCSP, "http://ocsp.example.mil")]))
        cls.user = {p: user(p) for p in ("/ca.cer", "/ca.pem", "/ca.p7c", "/other.cer", "/gone.cer")}
        cls.bare = pem(make("bare.user", "Test ID CA-1", key, cls.ca_key))

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_url_and_issuer_from_the_certificate(self):
        for path in ("/ca.cer", "/ca.pem", "/ca.p7c"):
            t = of.ocsp_target({"cert": self.user[path]})
            self.assertEqual((t["why"], t["url"], t["name"], t["from_cert"]), ("", "http://ocsp.example.mil", "http://ocsp.example.mil",
                                                                               ["url", "issuer"]), path)
            self.assertEqual(x509.load_pem_x509_certificate(t["issuer"].encode()).subject, self.ca.subject, path)

    def test_given_values_win(self):
        t = of.ocsp_target({"name": "DISA OCSP", "url": "http://ocsp2.example.mil", "cert": self.user["/gone.cer"], "issuer": pem(self.ca)})
        self.assertEqual((t["why"], t["url"], t["name"], t["from_cert"]), ("", "http://ocsp2.example.mil", "DISA OCSP", []))

    def test_issuer_from_the_chain_in_the_cert_file(self):
        t = of.ocsp_target({"cert": self.user["/gone.cer"] + pem(self.ca)})       # the download would fail: not needed
        self.assertEqual((t["why"], t["from_cert"]), ("", ["url", "issuer"]))
        self.assertEqual(t["cert"], self.user["/gone.cer"].strip())

    def test_what_is_said_when_something_is_missing(self):
        self.assertIn("names no OCSP address", of.ocsp_target({"cert": self.bare})["why"])
        self.assertIn("names no issuer address", of.ocsp_target({"cert": self.bare, "url": "http://ocsp.example.mil"})["why"])
        why = of.ocsp_target({"cert": self.user["/gone.cer"]})["why"]
        self.assertIn("could not be read from http://127.0.0.1", why)
        self.assertIn("give it as issuer", why)
        self.assertIn("none of its 1 certificate(s) is the issuer", of.ocsp_target({"cert": self.user["/other.cer"]})["why"])
        self.assertIn("neither a url nor a cert", of.ocsp_target({})["why"])
        self.assertEqual(of.ocsp_target({"url": "http://ocsp.example.mil"})["why"], "")     # a url alone: asked as before
        self.assertIn("not a PEM certificate", of.ocsp_target({"cert": "hello"})["why"])

    def test_a_known_why_is_kept(self):
        t = of.ocsp_target({"url": "", "cert": "", "why": "not found in the project: files/ocsp/x.crt"})
        self.assertEqual((t["why"], t["name"]), ("not found in the project: files/ocsp/x.crt", "OCSP"))

    def test_site_ocsp_certs_items(self):
        self.assertEqual(of.ocsp_cert_entry("files/ocsp/test-user.pem"), {"cert": "files/ocsp/test-user.pem"})
        self.assertEqual(of.ocsp_cert_entry({"cert": "a.pem", "name": "N"}), {"cert": "a.pem", "name": "N"})


if __name__ == "__main__":
    unittest.main()
