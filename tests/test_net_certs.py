"""roles/net_certs/library/site_net_certs.py: a network device's certificate, read from the controller -
against a real TLS server on 127.0.0.1 (a certificate made here), a closed port, a sweep, exclusions."""
import datetime
import os
import socket
import ssl
import sys
import tempfile
import threading
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "roles", "net_certs", "library"))
import site_net_certs as nc  # noqa: E402

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402


def make_cert(cn, days):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(0x0abc)
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=days, hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(cn), x509.IPAddress(__import__("ipaddress").ip_address("127.0.0.1"))]),
                           critical=False)
            .sign(key, hashes.SHA256()))
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "c.pem"), "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(os.path.join(d, "k.pem"), "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return os.path.join(d, "c.pem"), os.path.join(d, "k.pem")


class TlsServer(object):
    def __init__(self, cn="sw01.example.mil", days=20):
        c, k = make_cert(cn, days)
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(c, k)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            try:
                with self.ctx.wrap_socket(conn, server_side=True) as t:
                    t.recv(1)
            except (OSError, ssl.SSLError):
                pass


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def params(**kw):
    p = dict(targets=[], subnets=[], ports=[443], exclude=[], timeout=3, workers=8, max_addresses=4096, resolve_names=False)
    p.update(kw)
    return p


class NetCerts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = TlsServer()

    def test_inventory_target(self):
        closed = free_port()
        r = nc.run(params(targets=[{"host": "sw01", "address": "127.0.0.1", "ports": [self.srv.port, closed]}]))
        self.assertEqual(len(r["certs"]), 1)
        c = r["certs"][0]
        self.assertEqual((c["host"], c["port"], c["subject"], c["days"], c["found_by"]), ("sw01", self.srv.port, "CN=sw01.example.mil", 20, "inventory"))
        self.assertEqual(c["sans"], ["DNS:sw01.example.mil", "IP:127.0.0.1"])
        self.assertEqual((c["tier"], c["key"], c["serial"], c["self_signed"]), ("self-signed", "EC P-256", "0ABC", True))
        self.assertEqual((len(c["sha1"]), len(c["sha256"])), (40, 64))
        self.assertEqual(c["where"], "https://127.0.0.1:%d" % self.srv.port)
        self.assertTrue(c["tls"].startswith("TLS"))
        self.assertEqual([(x["host"], x["port"]) for x in r["not_answering"]], [("sw01", closed)])
        self.assertIn("refused", r["not_answering"][0]["why"])

    def test_sweep(self):
        r = nc.run(params(subnets=["127.0.0.1/32"], ports=[self.srv.port, free_port()]))
        self.assertEqual([(c["host"], c["found_by"]) for c in r["certs"]], [("127.0.0.1", "sweep")])
        self.assertEqual(r["swept"], {"subnets": ["127.0.0.1/32"], "addresses": 1, "probes": 2, "answered": 1})
        self.assertEqual(r["not_answering"], [])        # a sweep's silent ports are only counted

    def test_inventory_and_sweep_once(self):
        r = nc.run(params(targets=[{"host": "sw01", "address": "127.0.0.1", "ports": [self.srv.port]}], subnets=["127.0.0.1/32"],
                          ports=[self.srv.port]))
        self.assertEqual([(c["host"], c["found_by"]) for c in r["certs"]], [("sw01", "inventory")])

    def test_exclude(self):
        r = nc.run(params(targets=[{"host": "sw01", "address": "127.0.0.1", "ports": [self.srv.port]}], subnets=["127.0.0.0/30"],
                          ports=[self.srv.port], exclude=["sw0*", "127.0.0.0/30"]))
        self.assertEqual(r["certs"], [])

    def test_too_big(self):
        with self.assertRaises(ValueError):
            nc.sweep_addresses(["198.18.0.0/15"], 4096)
        self.assertEqual(len(nc.sweep_addresses(["192.0.2.0/24", "192.0.2.0/25"], 4096)), 254)

    def test_fixture_fields(self):
        """The same fields as the other reports for the shared test certificates (tests/fixtures/pki)."""
        import base64
        pki = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "pki")
        want = {"root": ("root", "RSA 3072"), "int": ("intermediate", "EC P-256"), "web": ("ca-signed", "RSA 2048"), "self": ("self-signed", "EC P-384")}
        for n, (tier, key) in want.items():
            with open(os.path.join(pki, n + ".pem")) as f:
                der = base64.b64decode("".join(x for x in f.read().splitlines() if not x.startswith("-----")))
            r = nc.cert_record(der)
            self.assertEqual((r["tier"], r["key"]), (tier, key), n)
        self.assertEqual(nc.cert_record(der)["sans"], ["DNS:self01.example.mil"])


class IosCsrRefuses(unittest.TestCase):
    """site_ios_csr: a subject or name from the served certificate that is not a plain DN / host name is refused
    and nothing reaches the device; a plain one goes through."""

    def run_module(self, subject, names):
        import site_ios_csr as ic
        sent, out = [], {}

        class Done(Exception):
            pass

        class FakeModule(object):      # AnsibleModule's part main() uses, the same on every ansible-core
            def __init__(self, **kw):
                self.params = {"trustpoint": "SA-RENEW-20270101", "subject": subject, "names": names, "key_bits": 2048}
                self._socket_path = "/nonexistent"

            def exit_json(self, **kw):
                out.update(kw)
                raise Done()

            def fail_json(self, **kw):
                out.update(kw, failed=True)
                raise Done()

        class FakeConn(object):
            def __init__(self, path):
                pass

            def get(self, command):
                return ""

            def edit_config(self, candidate):
                sent.extend(candidate)
                return {"response": ["-----BEGIN CERTIFICATE REQUEST-----\nAAAA\n-----END CERTIFICATE REQUEST-----"]}
        with unittest.mock.patch.object(ic, "AnsibleModule", FakeModule), unittest.mock.patch.object(ic, "Connection", FakeConn):
            with self.assertRaises(Done):
                ic.main()
        return out, sent

    def test_ios_csr_refuses_line_break_in_subject(self):
        out, sent = self.run_module("CN=sw01.example.mil\nusername x privilege 15", ["sw01.example.mil"])
        self.assertTrue(out.get("failed"))
        self.assertIn("nothing sent", out["msg"])
        self.assertEqual(sent, [])

    def test_ios_csr_refuses_odd_name(self):
        for bad in ["sw01.example.mil\rend", "sw01 example", "sw01.example.mil;x", "sw\u00e9.example.mil"]:
            out, sent = self.run_module("CN=sw01.example.mil", ["sw01.example.mil", bad])
            self.assertTrue(out.get("failed"), bad)
            self.assertEqual(sent, [], bad)

    def test_ios_csr_plain_subject_goes_through(self):
        out, sent = self.run_module("CN=sw01.example.mil,OU=Network,O=Example Org,C=US", ["sw01.example.mil", "*.example.mil"])
        self.assertFalse(out.get("failed"), out)
        self.assertIn(" subject-name CN=sw01.example.mil,OU=Network,O=Example Org,C=US", sent)
        self.assertIn(" subject-alt-name sw01.example.mil *.example.mil", sent)


if __name__ == "__main__":
    unittest.main(verbosity=1)
