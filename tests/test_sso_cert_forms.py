"""site_vcenter_sso.load_cert: every form a certificate arrives in reads the same, and the STS chain never crashes the job.

1.66.1: a vCenter's STS chain (or an OCSP test certificate) whose text was not plain PEM stopped the whole single sign-on
step with "Unable to load PEM file ... InvalidHeader". The body is now decoded as DER, and a certificate that cannot be read
is a note, not a crash.
"""
import datetime
import importlib.util
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "roles", "vmware_vm", "module_utils"))

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


def load_module():
    import site_vcenters
    sys.modules.setdefault("ansible.module_utils.site_vcenters", site_vcenters)
    spec = importlib.util.spec_from_file_location("site_vcenter_sso", os.path.join(REPO, "roles", "vmware_vm", "library", "site_vcenter_sso.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def make_pem():
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, u"sts.example.mil")])
    now = datetime.datetime(2026, 1, 1)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(7).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=365))
            .sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


@unittest.skipUnless(HAS_CRYPTO, "cryptography is not installed")
class CertForms(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_module()
        cls.pem = make_pem()

    def subject(self, text):
        return self.m.load_cert(text).subject.rfc4514_string()

    def test_every_form_reads_the_same(self):
        body = "".join(line for line in self.pem.splitlines() if "CERTIFICATE" not in line)
        forms = {
            "pem": self.pem,
            "windows line ends": self.pem.replace("\n", "\r\n"),
            "escaped line ends": self.pem.replace("\n", "\\n"),
            "escaped windows line ends": self.pem.replace("\n", "\\r\\n"),
            "bare base64 body": body,
            "body on one line after BEGIN": self.pem.replace("-----\n", "-----", 1),
        }
        for what, text in forms.items():
            with self.subTest(what):
                self.assertEqual(self.subject(text), "CN=sts.example.mil")

    def test_not_a_certificate_is_a_value_error(self):
        for text in ("", "junk", "MIIF", "-----BEGIN CERTIFICATE-----\nnot base64 at all\n-----END CERTIFICATE-----"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    self.m.load_cert(text)

    def test_the_chain_in_a_json_answer(self):
        chain = {"active_cert_chain": {"cert_chain": [self.pem.replace("\n", "\r\n")]}}
        pems = self.m.pems_in(chain)
        self.assertEqual(len(pems), 1)
        self.assertEqual(self.subject(pems[0]), "CN=sts.example.mil")


if __name__ == "__main__":
    unittest.main()
