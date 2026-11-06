"""Unit tests for the DoD PKI bundle check (roles/pki_bundle/library/site_pki_bundle.py) against a made-up PKI laid out
as DISA's zip is: the PKCS7 bundle, a list of its files' SHA-256 signed (CMS) by a code signing certificate, and that
certificate's chain. First, current, newer, a root not in yours, a file changed after it was signed, a list signed under
another root, a download over HTTP.
    python3 tests/test_pki_bundle.py"""
import hashlib
import http.server
import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import zipfile

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "roles", "pki_bundle", "library"))
import site_pki_bundle as m  # noqa: E402

W = tempfile.mkdtemp(prefix="sa-pkib-")
SUBJ = "/C=US/O=U.S. Government/OU=DoD/OU=PKI/CN="


def ossl(*args):
    r = subprocess.run(["openssl"] + list(args), cwd=W, capture_output=True)
    if r.returncode:
        raise RuntimeError("openssl %s: %s" % (" ".join(args), r.stderr.decode()))
    return r.stdout


def read(name):
    with open(os.path.join(W, name), "rb") as fh:
        return fh.read()


def write(name, data):
    with open(os.path.join(W, name), "wb") as fh:
        fh.write(data if isinstance(data, bytes) else data.encode())
    return os.path.join(W, name)


def root(name, cn):
    ossl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", name + ".key", "-out", name + ".crt", "-days", "400",
         "-subj", SUBJ + cn, "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign")


def signed(name, cn, by, ext):
    ossl("req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", name + ".key", "-out", name + ".csr", "-subj", SUBJ + cn)
    ossl("x509", "-req", "-in", name + ".csr", "-CA", by + ".crt", "-CAkey", by + ".key", "-CAcreateserial", "-days", "300",
         "-out", name + ".crt", "-extfile", ext)


def setUpModule():
    write("ca.ext", "basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\n")
    write("sign.ext", "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=codeSigning\n")
    root("r3", "TEST ROOT CA 3")
    signed("id70", "TEST ID CA-70", "r3", "ca.ext")
    signed("id71", "TEST ID CA-71", "r3", "ca.ext")
    signed("email70", "TEST EMAIL CA-70", "r3", "ca.ext")
    signed("signer", "TEST PKE CODE SIGNING", "email70", "sign.ext")
    root("r9", "TEST ROOT CA 9")                         # a root DISA adds
    signed("id90", "TEST ID CA-90", "r9", "ca.ext")
    root("evil", "TEST ROOT CA 3")                       # another key, the same name
    signed("evilsigner", "TEST PKE CODE SIGNING", "evil", "sign.ext")


def tearDownModule():
    shutil.rmtree(W, ignore_errors=True)


def p7b(*names):
    args = ["crl2pkcs7", "-nocrl", "-outform", "DER"]
    for n in names:
        args += ["-certfile", n + ".crt"]
    return ossl(*args)


def bundle_zip(version, certs, signer="signer", chain=("email70", "r3"), tamper=False):
    """A zip as DISA's: Certificates_PKCS7_v<version>_TEST/ - the bundle (DER and PEM), the signed .sha256, the chain."""
    d = "Certificates_PKCS7_v%s_TEST" % version.replace(".", "_")
    der = p7b(*certs)
    files = {"%s.der.p7b" % d: der, "README.txt": b"made up"}
    listing = "".join("%s  %s\n" % (hashlib.sha256(v).hexdigest(), k) for k, v in sorted(files.items()))
    write("list.txt", listing)
    ossl("cms", "-sign", "-binary", "-nodetach", "-md", "sha256", "-in", "list.txt", "-signer", signer + ".crt",
         "-inkey", signer + ".key", "-outform", "DER", "-out", "list.sha256")
    if tamper:
        files["%s.der.p7b" % d] = p7b(*(list(certs) + ["evil"]))
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as f:
        for k, v in files.items():
            f.writestr(d + "/" + k, v)
        f.writestr(d + "/%s.sha256" % d, read("list.sha256"))
        f.writestr(d + "/DoD_PKE_CA_chain.pem", b"".join(read(n + ".crt") for n in chain))
    return z.getvalue()


def check(data, current=None, **kw):
    url = write("dl-%d.zip" % len(os.listdir(W)), data) if isinstance(data, bytes) else data
    p = dict(url=url, current=current or "", dest_dir=os.path.join(W, "out"), validate_certs=True, ca_path=None, timeout=10,
             max_bytes=20971520, warn_days=90)
    p.update(kw)
    return m.run(types.SimpleNamespace(params=p))


def subjects(entries):
    return sorted(e["subject"].split(",")[0] for e in entries)


class Check(unittest.TestCase):
    def test_first(self):
        r = check(bundle_zip("5.14", ["r3", "id70", "email70"]))
        self.assertEqual((r["status"], r["version"], r["cas"], r["problems"]), ("first", "5.14", 3, []), r)
        self.assertEqual(r["signature"], "verified")      # signed with SHA-256 here
        self.assertEqual(r["hashes"], "match")
        self.assertTrue(r["file"].endswith("Certificates_PKCS7_v5_14_TEST.der.p7b"))
        self.assertEqual(read(os.path.relpath(r["file"], W))[:1], b"0")

    def test_current(self):
        cur = write("mine.p7b", p7b("r3", "id70", "email70"))
        r = check(bundle_zip("5.14", ["r3", "id70", "email70"]), cur)
        self.assertEqual((r["status"], r["added"], r["removed"], r["file"]), ("current", [], [], ""), r)
        self.assertEqual(r["current_count"], 3)

    def test_newer(self):
        cur = write("mine.p7b", p7b("r3", "id70", "email70"))
        r = check(bundle_zip("5.15", ["r3", "id71", "email70"]), cur)
        self.assertEqual(r["status"], "newer", r)
        self.assertEqual((subjects(r["added"]), subjects(r["removed"])), (["CN=TEST ID CA-71"], ["CN=TEST ID CA-70"]))
        self.assertTrue(r["file"])

    def test_your_bundle_as_the_zip(self):
        cur = write("mine.zip", bundle_zip("5.14", ["r3", "id70", "email70"]))
        r = check(bundle_zip("5.14", ["r3", "id70", "email70"]), cur)
        self.assertEqual((r["status"], r["current_version"]), ("current", "5.14"), r)

    def test_a_new_root_vouched_for_by_the_signature(self):
        cur = write("mine.p7b", p7b("r3", "id70", "email70"))
        r = check(bundle_zip("5.15", ["r3", "id70", "email70", "r9", "id90"]), cur)
        self.assertEqual((r["status"], r["problems"]), ("newer", []), r)
        self.assertEqual(subjects(r["new_roots"]), ["CN=TEST ROOT CA 9"])

    def test_a_new_root_without_a_signature_is_refused(self):
        cur = write("mine.p7b", p7b("r3", "id70", "email70"))
        r = check(write("bare.p7b", p7b("r3", "id70", "email70", "r9", "id90")), cur)
        self.assertEqual(r["status"], "failed", r)
        self.assertTrue(r["signature"].startswith("not checked"))
        self.assertEqual(len(r["untrusted"]), 1)
        self.assertFalse(r["file"])

    def test_changed_after_signing(self):
        cur = write("mine.p7b", p7b("r3", "id70", "email70"))
        r = check(bundle_zip("5.15", ["r3", "id70", "email70"], tamper=True), cur)
        self.assertEqual(r["status"], "failed", r)
        self.assertTrue(r["hashes"].startswith("mismatch"), r["hashes"])
        self.assertFalse(r["file"])

    def test_signed_under_another_root(self):
        cur = write("mine.p7b", p7b("r3", "id70", "email70"))
        r = check(bundle_zip("5.15", ["r3", "id70", "email70"], signer="evilsigner", chain=("evil",)), cur)
        self.assertEqual(r["status"], "failed", r)
        self.assertTrue(r["signature"].startswith("FAILED"), r["signature"])

    def test_not_the_bundle(self):
        z = io.BytesIO()
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("a.txt", "x")
            f.writestr("b.txt", "y")
        r = check(z.getvalue())
        self.assertEqual(r["status"], "failed")
        self.assertIn("no Certificates_PKCS7", r["problems"][0])

    def test_no_download(self):
        r = check(os.path.join(W, "nothere.zip"))
        self.assertIn("could not be downloaded", r["problems"][0])

    def test_your_bundle_missing(self):
        r = check(bundle_zip("5.14", ["r3", "id70", "email70"]), os.path.join(W, "gone.p7b"))
        self.assertIn("cannot be read", r["problems"][0])

    def test_over_http(self):
        data = bundle_zip("5.14", ["r3", "id70", "email70"])

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            r = check("http://127.0.0.1:%d/unclass-certificates_pkcs7_TEST.zip" % srv.server_port)
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual((r["status"], r["version"]), ("first", "5.14"), r)


class Pieces(unittest.TestCase):
    def test_pick(self):
        self.assertEqual(m.pick(["x/Certificates_PKCS7_v5_14_DoD_DoD_Root_CA_3.der.p7b", "x/Certificates_PKCS7_v5_14_DoD.pem.p7b",
                                 "x/Certificates_PKCS7_v5_14_DoD.der.p7b"]), ("x/Certificates_PKCS7_v5_14_DoD.der.p7b", "5.14"))
        self.assertEqual(m.pick(["README.txt"]), (None, ""))

    def test_p7_certs_pem_and_der(self):
        der = p7b("r3", "id70")
        self.assertEqual(len(m.certs_of(der)[0]), 2)
        pem = ossl("pkcs7", "-inform", "DER", "-in", write("t.p7b", der))
        self.assertEqual(len(m.certs_of(pem)[0]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=1)
