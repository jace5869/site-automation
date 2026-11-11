"""Unit tests for the DoD PKI bundle check (roles/pki_bundle/library/site_pki_bundle.py) against a made-up PKI laid out
as DISA's zip is: the PKCS7 bundle, a list of its files' SHA-256 signed (CMS) by a code signing certificate, and that
certificate's chain. First, current, newer, a root not in yours, a file changed after it was signed, a list signed under
another root, a download over HTTP; a bundle that differs without a checked signature (not offered unless
pki_bundle_accept_unverified), and the role's output and email for it with an expired CA (needs ansible-playbook;
ANSIBLE_PLAYBOOK=<2.16's ansible-playbook> for 2.16).
    python3 tests/test_pki_bundle.py"""
import datetime
import hashlib
import http.server
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import zipfile

import yaml

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


def expired_ca(name, cn, by):
    """A CA under by that expired last month (openssl before 3.4 cannot make one: the cryptography library does)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    ca = x509.load_pem_x509_certificate(read(by + ".crt"))
    ca_key = serialization.load_pem_private_key(read(by + ".key"), None)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    subj = x509.Name(list(ca.subject)[:-1] + [x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (x509.CertificateBuilder().subject_name(subj).issuer_name(ca.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=60))
            .not_valid_after(now - datetime.timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .sign(ca_key, hashes.SHA256()))
    write(name + ".crt", cert.public_bytes(serialization.Encoding.PEM))


def check(data, current=None, **kw):
    url = write("dl-%d.zip" % len(os.listdir(W)), data) if isinstance(data, bytes) else data
    p = dict(url=url, current=current or "", dest_dir=os.path.join(W, "out"), validate_certs=True, ca_path=None, timeout=10,
             max_bytes=20971520, warn_days=90, accept_unverified=False)
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

    def test_a_new_root_alone_without_a_signature_is_not_offered(self):
        """A root not in yours, nothing under it, and no signed list (a copy on a share; FIPS mode is the same): not
        "newer" on its own word - "not verified", no file to attach. pki_bundle_accept_unverified: true offers it."""
        cur = write("mine.p7b", p7b("r3", "id70", "email70"))
        r = check(write("bare-root.p7b", p7b("r3", "id70", "email70", "r9")), cur)
        self.assertEqual((r["status"], r["problems"], r["file"]), ("not verified", [], ""), r)
        self.assertTrue(r["signature"].startswith("not checked"))
        self.assertEqual(subjects(r["new_roots"]), ["CN=TEST ROOT CA 9"])
        # a CA added under your own root, no signed list: not offered either
        r = check(write("bare-id.p7b", p7b("r3", "id70", "id71", "email70")), cur)
        self.assertEqual((r["status"], r["file"]), ("not verified", ""), r)
        # the operator accepts unverified bundles: offered as newer, the file written
        r = check(write("bare-root2.p7b", p7b("r3", "id70", "email70", "r9")), cur, accept_unverified=True)
        self.assertEqual(r["status"], "newer", r)
        self.assertTrue(r["file"])
        # CAs under a root nothing vouches for stay refused, whatever the setting
        r = check(write("bare2.p7b", p7b("r3", "id70", "email70", "r9", "id90")), cur, accept_unverified=True)
        self.assertEqual((r["status"], r["file"]), ("failed", ""), r)

    def test_the_output_and_the_email(self):
        """The role's result lines and email sections (roles/pki_bundle/tasks/main.yml) for a bundle not verified - no
        "save it" advice, no attachment - and an expired CA listed as EXPIRED, both run through ansible-playbook."""
        exe = os.environ.get("ANSIBLE_PLAYBOOK") or shutil.which("ansible-playbook")
        if not exe:
            self.skipTest("ansible-playbook not on PATH")
        expired_ca("old70", "TEST OLD CA-70", "r3")
        cur = write("mine.p7b", p7b("r3", "id70", "email70"))
        r = check(write("bare-old.p7b", p7b("r3", "id70", "email70", "r9", "old70")), cur)
        self.assertEqual(r["status"], "not verified", r)
        self.assertEqual(subjects(r["expired"]), ["CN=TEST OLD CA-70"])
        with open(os.path.join(here, "..", "roles", "pki_bundle", "tasks", "main.yml"), encoding="utf-8") as fh:
            role = {t["name"]: t for t in yaml.safe_load(fh)}
        email = role["PKI bundle | the email (site_email)"]["vars"]
        result = role["PKI bundle | the result"]["vars"]
        lines_f, mail_f = os.path.join(W, "lines.json"), os.path.join(W, "mail.json")
        keep = [{"name": "the lines", "ansible.builtin.copy": {"content": "{{ _pkib_lines | to_json }}", "dest": lines_f}, "vars": result},
                {"name": "the email", "ansible.builtin.copy": {
                    "content": "{{ {'title': _report.title, 'sections': _report.sections, 'attachments': _report_attachments} | to_json }}",
                    "dest": mail_f}, "vars": email}]
        with open(os.path.join(here, "..", "roles", "pki_bundle", "defaults", "main.yml"), encoding="utf-8") as fh:
            defaults = yaml.safe_load(fh)
        play = [{"hosts": "localhost", "gather_facts": False,
                 "vars": dict(defaults, _pkib={"bundle": r}, _pkib_current=cur, _pkib_save_as="playbooks/files/dod_pki_bundle.p7b",
                              cert_install_ca_bundle="files/dod_pki_bundle.p7b"),
                 "tasks": [role["PKI bundle | what to do with one that is not verified (the output and the email)"],
                           role["PKI bundle | the result"]] + keep}]
        p = write("play.yml", yaml.safe_dump(play))
        out = subprocess.run([exe, "-i", "localhost,", "-c", "local", p], capture_output=True, text=True,
                             env=dict(os.environ, ANSIBLE_NOCOLOR="1"), timeout=300)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        with open(lines_f, encoding="utf-8") as fh:
            lines = json.load(fh)
        with open(mail_f, encoding="utf-8") as fh:
            mail = json.load(fh)
        self.assertTrue(lines[0].startswith("NOT VERIFIED"), lines)
        self.assertIn("EXPIRED CN=TEST OLD CA-70", "\n".join(lines))
        self.assertIn("pki_bundle_accept_unverified: true", "\n".join(lines))
        self.assertFalse(any(x.startswith("The checked bundle") for x in lines), lines)
        self.assertIn("not verified", mail["title"])
        self.assertEqual(mail["attachments"], [])
        titles = [s_["title"] for s_ in mail["sections"]]
        self.assertIn("Not verified", titles)
        self.assertNotIn("What to do", titles)
        rows = [row for s_ in mail["sections"] for row in s_.get("rows", [])]
        self.assertIn(["expired", r["expired"][0]["subject"], r["expired"][0]["not_after"], r["expired"][0]["sha1"]], rows)
        self.assertNotIn("replace the old file", json.dumps(mail))

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
