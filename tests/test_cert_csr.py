"""Unit tests for the certificate renewal (roles/check_certs/library/site_cert_csr.py) and the
correlation ID the tickets step and the renewal share: python3 tests/test_cert_csr.py
The whole renewal against openssl and a fake ServiceNow: tests/certs/run_cert_renew_test.sh"""
import os
import sys
import unittest

here = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(here, "..", "roles", "check_certs", "library"))
sys.path.insert(0, os.path.join(here, "..", "plugins", "filter"))
import site_cert_csr as m  # noqa: E402
import site_filters as f  # noqa: E402


class CorrelationId(unittest.TestCase):
    def test_same_as_the_hash_filter(self):
        """The ID the tickets step used to build with Jinja's hash('sha1') - so tickets opened before stay found."""
        from ansible.plugins.filter.core import get_hash
        fid = "cert:/etc/pki/tls/certs/web01.crt:CN=web01.example.mil,O=U.S. Government,C=US"
        want = "site-health:%s:certs:%s" % (get_hash("web01.example.mil", "sha1")[:8], get_hash(fid, "sha1")[:12])
        self.assertEqual(f.site_correlation_id(fid, "web01.example.mil", "certs", "site-health"), want)
        self.assertEqual(f.site_correlation_id(fid, "web01.example.mil", "certs"), want)

    def test_accents(self):
        from ansible.plugins.filter.core import get_hash
        fid = "cert:/x:OU=Ünit"
        self.assertTrue(f.site_correlation_id(fid, "h", "certs", "p").endswith(get_hash(fid, "sha1")[:12]))


class Subject(unittest.TestCase):
    def test_escapes(self):
        self.assertEqual(m.parse_rfc2253(r"CN=app01.example.mil,OU=\C3\9Cnit #1 $x 'q' \"d\",O=Smith\, Jones \+ Co,C=US"),
                         [("CN", "app01.example.mil", False), ("OU", "Ünit #1 $x 'q' \"d\"", False),
                          ("O", "Smith, Jones + Co", False), ("C", "US", False)])

    def test_two_values_in_one_rdn(self):
        rdns = m.parse_rfc2253("CN=mv01.example.mil+UID=mv01,O=Test,C=US")
        self.assertEqual(rdns, [("CN", "mv01.example.mil", False), ("UID", "mv01", True), ("O", "Test", False), ("C", "US", False)])
        self.assertEqual(m.dn_lines(rdns), ["0.C = US", "1.O = Test", "2.UID = mv01", "3.+CN = mv01.example.mil"])

    def test_repeated_names_get_numbers(self):
        self.assertEqual(m.dn_lines(m.parse_rfc2253("CN=w,OU=TEST,OU=PKI,OU=DoD,O=U.S. Government,C=US")),
                         ["0.C = US", "1.O = U.S. Government", "2.OU = DoD", "3.OU = PKI", "4.OU = TEST", "5.CN = w"])

    def test_encoded_value_refused(self):
        with self.assertRaises(m.CertError):
            m.parse_rfc2253("CN=x,1.2.3.4=#0403616263")   # public-ok: an OID, not an address

    def test_config_escape(self):
        self.assertEqual(m.conf_escape("a$b#c\"d'e\\f"), "a\\$b\\#c\\\"d\\'e\\\\f")


class Text(unittest.TestCase):
    OSSL3 = """        Subject Public Key Info:
            Public Key Algorithm: rsaEncryption
                Public-Key: (3072 bit)
        X509v3 extensions:
            X509v3 Subject Alternative Name:
                DNS:web01.example.mil, DNS:web.example.mil, IP Address:192.0.2.3, IP Address:::1, email:ops@example.mil
            X509v3 Basic Constraints: critical
                CA:FALSE
"""
    OSSL111_EC = """            Public Key Algorithm: id-ecPublicKey
                Public-Key: (384 bit)
                ASN1 OID: secp384r1
                NIST CURVE: P-384
"""

    def test_names(self):
        self.assertEqual(m.parse_sans(self.OSSL3), ["DNS:web01.example.mil", "DNS:web.example.mil", "IP:192.0.2.3", "IP:::1", "email:ops@example.mil"])
        self.assertEqual(m.parse_sans("no names here"), [])

    def test_a_name_kind_that_cannot_be_written_back(self):
        with self.assertRaises(m.CertError):
            m.parse_sans("X509v3 Subject Alternative Name:\n    DNS:a, othername:<unsupported>\n")

    def test_keys(self):
        self.assertEqual(m.key_of(self.OSSL3), ("rsa", 3072))
        self.assertEqual(m.key_of("Public Key Algorithm: rsaEncryption\n    RSA Public-Key: (2048 bit)\n"), ("rsa", 2048))   # OpenSSL 1.1.1
        self.assertEqual(m.key_of(self.OSSL111_EC), ("ec", "secp384r1"))
        self.assertEqual(m.key_of("Public Key Algorithm: id-ecPublicKey\n NIST CURVE: P-256\n"), ("ec", "prime256v1"))
        self.assertEqual(m.key_of("Public Key Algorithm: ED25519\n")[0], "other")

    def test_end_date(self):
        self.assertEqual(m.end_epoch("Nov  3 12:00:00 2026 GMT"), 1793707200)

    def test_host_names(self):
        """A self-signed certificate only for localhost gets no CSR: no name a PKI could issue it for."""
        self.assertEqual(m.host_names("CN=localhost", ["DNS:localhost", "IP:127.0.0.1"]), [])
        self.assertEqual(m.host_names("CN=localhost.localdomain", ["DNS:localhost6", "IP:::1"]), [])
        self.assertEqual(m.host_names("CN=self01.example.mil", []), ["self01.example.mil"])
        self.assertEqual(m.host_names("CN=localhost", ["DNS:web.example.mil", "IP:192.0.2.3"]), ["web.example.mil", "192.0.2.3"])

    def test_file_name(self):
        self.assertEqual(m.safe_name("web01.example.mil"), "web01.example.mil")
        self.assertEqual(m.safe_name("*.example.mil"), "example.mil")
        self.assertEqual(m.safe_name("../../etc"), "etc")


class _Module(object):
    def __init__(self, **params):
        self.params = dict({"trust_dirs": [], "keystore_passwords": {}, "report_only_dirs": [], "keep_dirs": [], "keep_files": []}, **params)


class TrustAndInstall(unittest.TestCase):
    def test_trust_store_never_renewed(self):
        r = m.Renewer(_Module(trust_dirs=["/srv/anchors"]))
        for where in ["/etc/pki/ca-trust/source/anchors/dod.crt", "/usr/share/pki/ca-trust-source/anchors/x.pem",
                      "/srv/anchors/test.crt", "/srv/anchors/test.crt (trust store)"]:
            self.assertTrue(r.in_trust(where), where)
        for where in ["/etc/pki/tls/certs/web01.crt", "/srv/anchorsX/test.crt", "TLS port 443, nginx (https://127.0.0.1:443)"]:
            self.assertFalse(r.in_trust(where), where)

    def test_extra_dirs_are_the_report_only(self):
        r = m.Renewer(_Module(report_only_dirs=["/home", "/root/"], keep_dirs=["/etc/pki/tls/certs", "/home/app/tls"],
                              keep_files=["/home/svc/*.crt"]))
        self.assertEqual(r.report_only("/home/jdoe/test.crt"), "/home")
        self.assertEqual(r.report_only("/root/x.pem"), "/root/")
        for where in ["/home/app/tls/web.crt", "/home/svc/web.crt", "/etc/pki/tls/certs/web01.crt", "/homeX/a.crt",
                      "TLS port 443, nginx (https://127.0.0.1:443)"]:
            self.assertEqual(r.report_only(where), "", where)
        self.assertEqual(m.Renewer(_Module()).report_only("/home/jdoe/test.crt"), "")      # cert_renew_extra_dirs: true

    def test_where_the_issued_certificate_goes(self):
        r = m.Renewer(_Module())
        entry = {"key_path": "/etc/pki/tls/renew/web01-1.key", "der": False}
        self.assertEqual(r.install(dict(entry, srcs=[("file", "/etc/pki/tls/certs/web01.crt"), ("tls", "TLS port 443 (https://h:443)")])), "")
        der = r.install(dict(entry, der=True, srcs=[("file", "/etc/pki/tls/certs/Web 01.CER")]))
        self.assertIn("binary (DER)", der)
        self.assertIn("-outform DER -out '/etc/pki/tls/certs/Web 01.CER'", der)
        ks = r.install(dict(entry, srcs=[("keystore", "/opt/app/app.jks (alias tomcat)"), ("tls", "TLS port 8443 (https://h:8443)")]))
        self.assertIn("in place of alias tomcat", ks)
        self.assertIn("keytool -importkeystore -srckeystore /root/tomcat-new.p12", ks)
        self.assertTrue(ks.index("cp -p /opt/app/app.jks /opt/app/app.jks.bak") < ks.index("keytool -importkeystore"))
        self.assertNotIn("keytool -delete", ks)       # never the working entry gone before the new one is in
        self.assertIn("-inkey /etc/pki/tls/renew/web01-1.key", ks)
        self.assertIn("openssl pkcs12 -export", r.install(dict(entry, srcs=[("keystore", "/opt/app/bundle.p12")])))
        self.assertIn("only the port serves it", r.install(dict(entry, srcs=[("tls", "TLS port 8443, java (https://h:8443)")])))


if __name__ == "__main__":
    unittest.main()
