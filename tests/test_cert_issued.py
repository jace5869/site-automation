"""Unit tests for finding the issued certificate on its catalog request (roles/servicenow/library/site_cert_issued.py):
every way a PKI hands a certificate back (PEM, DER, PKCS7 - text or binary -, a zip, base64 without its BEGIN line,
pasted into a work note), the chain, the names, and a run against the fake ServiceNow (tests/servicenow/snmock.py).
    python3 tests/test_cert_issued.py
The whole install, through the playbooks: tests/certs/run_cert_install_test.sh"""
import base64
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import zipfile

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "roles", "servicenow", "library"))
import site_cert_issued as m  # noqa: E402
from cryptography import x509  # noqa: E402

W = tempfile.mkdtemp(prefix="sa-issued-")


def ossl(*args, data=None):
    r = subprocess.run(["openssl"] + list(args), cwd=W, input=data, capture_output=True)
    if r.returncode:
        raise RuntimeError("openssl %s: %s" % (" ".join(args), r.stderr.decode()))
    return r.stdout


def read(name):
    with open(os.path.join(W, name), "rb") as fh:
        return fh.read()


def setUpModule():
    # a root, an intermediate it signs, and leaves the intermediate signs - one from a CSR (the renewal's)
    ossl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "root.key", "-out", "root.crt", "-days", "400",
         "-subj", "/C=US/O=U.S. Government/OU=DoD/OU=PKI/CN=TEST ROOT CA 3",
         "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    ossl("req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", "int.key", "-out", "int.csr",
         "-subj", "/C=US/O=U.S. Government/OU=DoD/OU=PKI/CN=TEST ID CA-70")
    with open(os.path.join(W, "ca.ext"), "w") as fh:
        fh.write("basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\n")
    ossl("x509", "-req", "-in", "int.csr", "-CA", "root.crt", "-CAkey", "root.key", "-CAcreateserial", "-days", "300",
         "-out", "int.crt", "-extfile", "ca.ext")
    ossl("req", "-new", "-newkey", "rsa:3072", "-nodes", "-keyout", "web.key", "-out", "web.csr",
         "-subj", "/C=US/O=U.S. Government/CN=web01.example.mil",
         "-addext", "subjectAltName=DNS:web01.example.mil,DNS:web.example.mil,IP:192.0.2.3")
    ossl("x509", "-req", "-in", "web.csr", "-CA", "int.crt", "-CAkey", "int.key", "-CAcreateserial", "-days", "365",
         "-out", "web.crt", "-copy_extensions", "copy")
    # the same names, another key (someone else's request); one missing a name; one valid for 10 days
    ossl("req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", "other.key", "-out", "other.csr",
         "-subj", "/C=US/O=U.S. Government/CN=web01.example.mil", "-addext", "subjectAltName=DNS:web01.example.mil")
    ossl("x509", "-req", "-in", "other.csr", "-CA", "int.crt", "-CAkey", "int.key", "-CAcreateserial", "-days", "365",
         "-out", "other.crt", "-copy_extensions", "copy")
    ossl("req", "-new", "-key", "web.key", "-out", "few.csr", "-subj", "/C=US/O=U.S. Government/CN=web01.example.mil",
         "-addext", "subjectAltName=DNS:web01.example.mil")
    ossl("x509", "-req", "-in", "few.csr", "-CA", "int.crt", "-CAkey", "int.key", "-CAcreateserial", "-days", "10",
         "-out", "few.crt", "-copy_extensions", "copy")
    ossl("x509", "-in", "web.crt", "-outform", "DER", "-out", "web.der")
    ossl("crl2pkcs7", "-nocrl", "-certfile", "web.crt", "-certfile", "int.crt", "-certfile", "root.crt", "-out", "web.p7b")
    ossl("crl2pkcs7", "-nocrl", "-certfile", "web.crt", "-certfile", "int.crt", "-outform", "DER", "-out", "web-der.p7b")


def tearDownModule():
    shutil.rmtree(W, ignore_errors=True)


def csr_key(name):
    return m.spki(x509.load_pem_x509_csr(read(name)))


class Formats(unittest.TestCase):
    def leaf_of(self, data):
        got = m.certs_in(data)
        want = csr_key("web.csr")
        return got, [c for c in got if m.spki(c) == want]

    def test_pem(self):
        got, leaf = self.leaf_of(read("web.crt"))
        self.assertEqual((len(got), len(leaf)), (1, 1))

    def test_pem_bundle_with_text_around(self):
        data = b"Here is your certificate:\r\n" + read("web.crt").replace(b"\n", b"\r\n") + b"\r\n" + read("int.crt") + b"thanks\n"
        got, leaf = self.leaf_of(data)
        self.assertEqual((len(got), len(leaf)), (2, 1))

    def test_der(self):
        got, leaf = self.leaf_of(read("web.der"))
        self.assertEqual((len(got), len(leaf)), (1, 1))

    def test_base64_without_begin_line(self):
        body = base64.encodebytes(read("web.der"))
        got, leaf = self.leaf_of(body)
        self.assertEqual(len(leaf), 1)

    def test_p7b_text_and_binary(self):
        for name, n in (("web.p7b", 3), ("web-der.p7b", 2)):
            got, leaf = self.leaf_of(read(name))
            self.assertEqual((len(got), len(leaf)), (n, 1), name)

    def test_zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("certs/web01.cer", read("web.der"))
            z.writestr("certs/chain.p7b", read("web.p7b"))
            z.writestr("readme.txt", b"from the PKI")
        got, leaf = self.leaf_of(buf.getvalue())
        self.assertEqual((len(got), len(leaf)), (4, 2))

    def test_nothing(self):
        self.assertEqual(m.certs_in(b"%PDF-1.4 not a certificate"), [])
        self.assertEqual(m.certs_in(b""), [])
        self.assertEqual(m.certs_in(b"PK\x03\x04broken zip"), [])

    def test_windows_csr_header(self):
        """certreq writes NEW CERTIFICATE REQUEST: the module reads it as the renewal's CSR."""
        text = read("web.csr").decode().replace("CERTIFICATE REQUEST", "NEW CERTIFICATE REQUEST")
        k = m.spki(x509.load_pem_x509_csr(text.replace("NEW CERTIFICATE REQUEST", "CERTIFICATE REQUEST").encode()))
        self.assertEqual(k, csr_key("web.csr"))

    def test_journal(self):
        note = "2026-10-24 09:00:00 - PKI Admin (Work notes)\nIssued:\n" + read("web.crt").decode() + "\n"
        self.assertEqual(len(m.journal_pem(note)), 1)
        self.assertEqual(m.journal_pem("nothing here"), [])


class Chain(unittest.TestCase):
    def test_order_from_the_issuer_up(self):
        leaf = m.certs_in(read("web.crt"))[0]
        cas = m.certs_in(read("root.crt")) + m.certs_in(read("int.crt"))
        chain = m.chain_for(leaf, cas)
        self.assertEqual([c.subject.rfc4514_string() for c in chain],
                         ["CN=TEST ID CA-70,OU=PKI,OU=DoD,O=U.S. Government,C=US", "CN=TEST ROOT CA 3,OU=PKI,OU=DoD,O=U.S. Government,C=US"])
        d = [m.describe(c) for c in chain]
        self.assertEqual([x["self_signed"] for x in d], [False, True])

    def test_no_chain(self):
        leaf = m.certs_in(read("web.crt"))[0]
        self.assertEqual(m.chain_for(leaf, []), [])


class Names(unittest.TestCase):
    def test_both_renewals(self):
        self.assertEqual([m.norm_name(x) for x in ["DNS:Web01.example.mil", "IP:192.0.2.3", "email:a@b", "URI:https://x"]],
                         ["dns:web01.example.mil", "ip:192.0.2.3", "email:a@b", "uri:https://x"])
        self.assertEqual([m.norm_name(x) for x in ["dns=web01.example.mil", "ipaddress=192.0.2.3", "url=https://x"]],
                         ["dns:web01.example.mil", "ip:192.0.2.3", "uri:https://x"])
        # the Windows renewal's info file: the values alone
        self.assertEqual([m.norm_name(x) for x in ["Web01.example.mil", "192.0.2.3", "2001:db8:0:0:0:0:0:1", "a@b.mil", "https://x"]],
                         ["dns:web01.example.mil", "ip:192.0.2.3", "ip:2001:db8::1", "email:a@b.mil", "uri:https://x"])
        self.assertEqual(m.norm_name("IP:2001:DB8:0:0:0:0:0:1"), "ip:2001:db8::1")
        self.assertEqual(m.norm_name("ipaddress=2001:db8:0:0:0:0:0:1"), "ip:2001:db8::1")

    def test_described(self):
        d = m.describe(m.certs_in(read("web.crt"))[0])
        self.assertEqual(d["names"], ["dns:web01.example.mil", "dns:web.example.mil", "ip:192.0.2.3"])
        self.assertEqual(d["key"], "RSA 3072-bit")
        self.assertEqual(len(d["sha1"]), 40)
        self.assertTrue(d["sha1"].isupper())
        self.assertFalse(d["ca"])


class Problems(unittest.TestCase):
    def now(self):
        return int(time.time())

    def test_good(self):
        d = m.describe(m.certs_in(read("web.crt"))[0])
        self.assertEqual(m.problems_of(d, ["DNS:web01.example.mil", "IP:192.0.2.3"], 30, False, self.now()), [])

    def test_fewer_names_and_short(self):
        d = m.describe(m.certs_in(read("few.crt"))[0])
        p = m.problems_of(d, ["DNS:web01.example.mil", "DNS:web.example.mil"], 30, False, self.now())
        self.assertEqual(len(p), 2, p)
        self.assertIn("valid only", p[0])
        self.assertIn("lacks: dns:web.example.mil", p[1])
        self.assertEqual(len(m.problems_of(d, ["DNS:web.example.mil"], 5, True, self.now())), 0)

    def test_expired_and_future(self):
        d = m.describe(m.certs_in(read("web.crt"))[0])
        self.assertIn("expired", m.problems_of(dict(d, days=-1), [], 30, False, self.now())[0])
        self.assertIn("not valid yet", m.problems_of(d, [], 30, False, d["not_before_epoch"] - 7200)[0])


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class FakeModule(object):
    def __init__(self, port, **kw):
        self.params = dict(url="http://127.0.0.1:%d" % port, username="api", password="goodpw", validate_certs=False,
                           ca_path=None, timeout=10, reorder_states=["4", "7"],
                           exts=["cer", "crt", "pem", "der", "p7b", "p7c", "p7", "cert", "zip", "txt"],
                           max_bytes=1048576, journal=True, min_days=30, fewer_names=False)
        self.params.update(kw)


class AgainstTheFakeServiceNow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.log = os.path.join(W, "sn.jsonl")
        cls.p = subprocess.Popen([sys.executable, os.path.join(here, "servicenow", "snmock.py"), str(cls.port)],
                                 env=dict(os.environ, SNMOCK_LOG=cls.log), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", cls.port), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.p.kill()

    def call(self, method, path, body=None, raw=None, ctype="application/json"):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        r = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=data, method=method,
                                   headers={"Authorization": "Basic " + base64.b64encode(b"api:goodpw").decode(), "Content-Type": ctype})
        return json.loads(urllib.request.urlopen(r).read() or b"{}").get("result")

    def order(self, stem, csr):
        """What the renewal does: the CSR uploaded for the item's question, the order, the file moved to the RITM."""
        up = self.call("POST", "/api/now/attachment/file?table_name=ZZ_YYsc_cart_item&table_sys_id=x&file_name=%s-exp20261114.csr" % stem,
                       raw=csr, ctype="text/plain")
        res = self.call("POST", "/api/sn_sc/servicecatalog/items/%s/order_now" % ("c0ffee" + "0" * 26),
                        {"variables": {"requested_for": "u1", "contact_method": "email", "csr_file": up["sys_id"]}})
        ritm = [r for r in self.call("GET", "/api/now/table/sc_req_item?sysparm_query=request=%s" % res["sys_id"]) or []]
        return ritm[0] if ritm else None

    def pending(self, stem, csr="web.csr", names=("DNS:web01.example.mil", "DNS:web.example.mil", "IP:192.0.2.3")):
        return {"key": "web01|" + stem, "host": "web01.example.mil", "stem": stem, "expires": "2026-11-14 10:00 UTC",
                "csr": read(csr).decode(), "names": list(names)}

    def find(self, item, **kw):
        mod = FakeModule(self.port, **kw)
        return m.find_one(m.Sn(mod), mod.params, item, m.utcnow())

    def test_not_ordered(self):
        r = self.find(self.pending("nothing.example.mil-00000000"))
        self.assertEqual(r["status"], "not ordered", r)

    def test_waiting_then_issued(self):
        ritm = self.order("web01.example.mil-aaaa1111", read("web.csr"))
        r = self.find(self.pending("web01.example.mil-aaaa1111"))
        self.assertEqual((r["status"], r["ritm"]), ("waiting", ritm["number"]), r)
        # someone else's certificate on it first: listed, never taken
        self.call("POST", "/api/now/attachment/file?table_name=sc_req_item&table_sys_id=%s&file_name=other.cer" % ritm["sys_id"],
                  raw=read("other.crt"), ctype="application/octet-stream")
        r = self.find(self.pending("web01.example.mil-aaaa1111"))
        self.assertEqual(r["status"], "waiting", r)
        self.assertIn("not for this key", r["reason"])
        # the PKI attaches it to the REQ, binary, and the chain as a p7b on the RITM
        self.call("POST", "/api/now/attachment/file?table_name=sc_request&table_sys_id=%s&file_name=web01.cer" % ritm["request"],
                  raw=read("web.der"), ctype="application/pkix-cert")
        self.call("POST", "/api/now/attachment/file?table_name=sc_req_item&table_sys_id=%s&file_name=chain.p7b" % ritm["sys_id"],
                  raw=read("web.p7b"), ctype="application/x-pkcs7-certificates")
        r = self.find(self.pending("web01.example.mil-aaaa1111"))
        self.assertEqual(r["status"], "issued", r)
        self.assertEqual(r["problems"], [])
        self.assertEqual(r["cert"]["names"], ["dns:web01.example.mil", "dns:web.example.mil", "ip:192.0.2.3"])
        self.assertEqual([c["subject"].split(",")[0] for c in r["chain"]], ["CN=TEST ID CA-70", "CN=TEST ROOT CA 3"])
        self.assertTrue(any("web01.example.mil-aaaa1111" not in x for x in r["read"]))
        self.assertFalse(any(x.startswith("web01.example.mil-aaaa1111") for x in r["read"]), r["read"])   # our CSR: not read

    def test_pasted_into_a_work_note(self):
        ritm = self.order("web01.example.mil-bbbb2222", read("web.csr"))
        self.call("PATCH", "/api/now/table/sc_req_item/%s" % ritm["sys_id"], {"work_notes": "Issued by DISA:\n" + read("web.crt").decode()})
        r = self.find(self.pending("web01.example.mil-bbbb2222"))
        self.assertEqual((r["status"], r["source"]), ("issued", "its work notes"), r)
        r = self.find(self.pending("web01.example.mil-bbbb2222"), journal=False)
        self.assertEqual(r["status"], "waiting", r)

    def test_closed_incomplete(self):
        ritm = self.order("web01.example.mil-cccc3333", read("web.csr"))
        self.call("PATCH", "/api/now/table/sc_req_item/%s" % ritm["sys_id"], {"state": "4"})
        r = self.find(self.pending("web01.example.mil-cccc3333"))
        self.assertEqual(r["status"], "closed", r)
        self.assertIn("Closed Incomplete", r["reason"])

    def test_fewer_names_held(self):
        ritm = self.order("web01.example.mil-dddd4444", read("web.csr"))
        self.call("POST", "/api/now/attachment/file?table_name=sc_req_item&table_sys_id=%s&file_name=web01.pem" % ritm["sys_id"],
                  raw=read("few.crt"), ctype="text/plain")
        r = self.find(self.pending("web01.example.mil-dddd4444"))
        self.assertEqual(r["status"], "issued", r)
        self.assertEqual(len(r["problems"]), 2, r["problems"])

    def test_too_big_and_other_types_not_read(self):
        ritm = self.order("web01.example.mil-eeee5555", read("web.csr"))
        self.call("POST", "/api/now/attachment/file?table_name=sc_req_item&table_sys_id=%s&file_name=form.pdf" % ritm["sys_id"],
                  raw=b"%PDF", ctype="application/pdf")
        self.call("POST", "/api/now/attachment/file?table_name=sc_req_item&table_sys_id=%s&file_name=web01.cer" % ritm["sys_id"],
                  raw=read("web.crt"), ctype="text/plain")
        r = self.find(self.pending("web01.example.mil-eeee5555"), max_bytes=100)
        self.assertEqual(r["status"], "waiting", r)
        self.assertTrue(any("over cert_install_max_bytes" in x for x in r["read"]), r["read"])
        self.assertFalse(any("form.pdf" in x for x in r["read"]), r["read"])

    def test_bad_password(self):
        mod = FakeModule(self.port, password="wrong")
        with self.assertRaises(m.SnError) as e:
            m.find_one(m.Sn(mod), mod.params, self.pending("x-1"), m.utcnow())
        self.assertIn("HTTP 401", str(e.exception))


if __name__ == "__main__":
    unittest.main(verbosity=1)
