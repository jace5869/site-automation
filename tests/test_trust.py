#!/usr/bin/env python3
"""Unit tests for the trust check: the certificate matching (roles/check_trust/module_utils/site_trust.py - by
fingerprint and by key, PEM and DER, OpenSSL's rejected trust blocks), the JKS and Go build-information readers
(roles/check_trust/library/site_trust_scan.py), the findings and report rows (plugins/filter/trust_filters.py),
vCenter's trusted-root listing (roles/vmware_vm/library/site_vmware_trust.py), and that the two copies of
site_trust.py are the same file.

    python tests/test_trust.py
"""
import filecmp
import importlib.util
import os
import struct
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "trust", "fixtures")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


st = _load("ansible.module_utils.site_trust", os.path.join(ROOT, "roles", "check_trust", "module_utils", "site_trust.py"))
sys.path.insert(0, os.path.join(ROOT, "roles", "check_trust", "library"))
sys.path.insert(0, os.path.join(ROOT, "plugins", "filter"))
import site_trust_scan as scan   # noqa: E402
import site_trust_remove as rm   # noqa: E402
import trust_filters as tf       # noqa: E402

KNOWN = yaml.safe_load(open(os.path.join(ROOT, "roles", "check_trust", "vars", "main.yml")))["check_trust_known"]
KNOWN_BY_NAME = {}
for e in KNOWN:
    KNOWN_BY_NAME.setdefault(e["name"], e)


def pem(name):
    return open(os.path.join(FIX, name + ".pem")).read()


class Matching(unittest.TestCase):
    def test_the_shipped_list(self):
        self.assertEqual(len(KNOWN), 20)
        self.assertEqual(sum(1 for e in KNOWN if e["kind"] == "intermediate"), 16)
        self.assertEqual(sum(1 for e in KNOWN if e["severity"] == "critical"), 16)
        self.assertEqual({e["name"] for e in KNOWN if e["kind"] == "root"},
                         {"emSign ECC Root CA - G3", "emSign Root TLS CA - G3", "emSign Root CS CA - G3", "AAA Certificate Services"})
        for e in KNOWN:
            self.assertEqual(len(e["sha256"]), 64)
            self.assertEqual(len(e["spki_sha256"]), 64)
            self.assertTrue(e["why"] and e["since"])

    def test_fingerprint_and_key_of_the_fixtures(self):
        known = st.Known(KNOWN)
        for f, name, sev in (("dv-g3b-emudhra", "EM DV TLS CA - G3B", "critical"), ("dv-g3b-aaa", "EM DV TLS CA - G3B", "critical"),
                             ("emsign-ecc-root-ca-g3", "emSign ECC Root CA - G3", "warning"), ("aaa-certificate-services", "AAA Certificate Services", "notice")):
            certs = st.pem_certs(pem(f))
            self.assertEqual(len(certs), 1)
            r = known.scan(certs)
            self.assertEqual([(h["name"], h["how"], h["severity"]) for h in r["hits"]], [(name, "certificate", sev)], f)
            self.assertEqual(st.spki_sha256(certs[0][0]), KNOWN_BY_NAME[name]["spki_sha256"])
            self.assertEqual(st.subject_cn(certs[0][0]), name)

    def test_the_same_key_under_another_certificate_is_a_hit(self):
        # only the eMudhra-issued copy on the list: the Sectigo cross-sign (same key) is found by its key
        only = [e for e in KNOWN if e["name"] == "EM DV TLS CA - G3B" and e["issuer"] == "emSign Root TLS CA - G3"]
        r = st.Known(only).scan(st.pem_certs(pem("dv-g3b-aaa")))
        self.assertEqual([(h["name"], h["how"], h["cn"]) for h in r["hits"]], [("EM DV TLS CA - G3B", "key", "EM DV TLS CA - G3B")])

    def test_a_bundle_each_once_and_der(self):
        known = st.Known(KNOWN)
        text = pem("emsign-ecc-root-ca-g3") + pem("aaa-certificate-services") + pem("emsign-ecc-root-ca-g3") + pem("dv-g3b-emudhra")
        r = known.scan(st.pem_certs(text))
        self.assertEqual(r["count"], 4)
        self.assertEqual([h["name"] for h in r["hits"]], ["emSign ECC Root CA - G3", "AAA Certificate Services", "EM DV TLS CA - G3B"])
        der = st.pem_certs(pem("dv-g3b-emudhra"))[0][0]
        self.assertEqual(st.der_certs(der), [(der, False)])
        self.assertEqual(st.der_certs(b"not a certificate at all"), [])
        self.assertEqual(st.pem_certs("-----BEGIN CERTIFICATE-----\n!!!\n-----END CERTIFICATE-----"), [])

    def test_openssl_rejected_trust_block(self):
        if not subprocess.run(["which", "openssl"], capture_output=True).returncode == 0:
            self.skipTest("no openssl")
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "r.pem")
            open(src, "w").write(pem("emsign-ecc-root-ca-g3"))
            out = subprocess.run(["openssl", "x509", "-in", src, "-addreject", "serverAuth", "-trustout"], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            certs = st.pem_certs(out.stdout)
            self.assertEqual(len(certs), 1)
            self.assertTrue(certs[0][1], "rejected")
            r = st.Known(KNOWN).scan(certs)
            self.assertEqual((r["hits"], r["blocked"]), ([], ["emSign ECC Root CA - G3"]))
            out = subprocess.run(["openssl", "x509", "-in", src, "-addtrust", "serverAuth", "-trustout"], capture_output=True, text=True)
            self.assertFalse(st.pem_certs(out.stdout)[0][1], "trusted, not rejected")

    def test_versions(self):
        self.assertEqual(st.version_tuple("v22.23.4"), (22, 23, 4))
        self.assertEqual(st.version_tuple("3.131.1-2.el9"), (3, 131, 1, 2, 9))
        self.assertTrue(st.below("3.126.0-1.fc44", "3.131.1"))
        self.assertFalse(st.below("3.131.1-1", "3.131.1"))
        self.assertFalse(st.below("", "3.131.1"))
        go = {"1.26": "1.26.10", "1.27": "1.27.3"}
        self.assertEqual(st.fixed_for("1.27.2", go), ("1.27.3", "below"))
        self.assertEqual(st.fixed_for("1.27.3", go), ("1.27.3", "ok"))
        self.assertEqual(st.fixed_for("1.26.10", go), ("1.26.10", "ok"))
        self.assertEqual(st.fixed_for("1.22.5", go), ("", "unsupported"))
        self.assertEqual(st.fixed_for("1.28rc1", go), ("", "unknown"))
        self.assertEqual(st.fixed_for("", go), ("", "unknown"))
        self.assertEqual(st.fixed_for("20.9.0", {"22": "22.23.4", "24": "24.22.1"}), ("", "unsupported"))


def fake_jks(certs, version=2):
    """A JKS keystore with trusted certificate entries (the format keytool writes, without the digest)."""
    out = b"\xfe\xed\xfe\xed" + struct.pack(">II", version, len(certs))
    for i, der in enumerate(certs):
        alias = ("ca%d" % i).encode()
        out += struct.pack(">I", 2) + struct.pack(">H", len(alias)) + alias + b"\x00" * 8
        if version == 2:
            out += struct.pack(">H", 5) + b"X.509"
        out += struct.pack(">I", len(der)) + der
    return out + b"\x00" * 20


def fake_go(version, path="", size=1200000):
    """An ELF file with Go 1.18+ build information: the magic, pointer size, flags (strings follow), the version
    and the module information as length-prefixed strings at offset 32."""
    mod = ("path\t%s\nmod\t%s\t(devel)\t\n" % (path, path)) if path else ""
    info = scan.GO_MAGIC + bytes([8, 2]) + b"\x00" * 16
    for s in (version, mod):
        b = s.encode()
        n, enc = len(b), b""
        while True:
            if n < 0x80:
                enc += bytes([n])
                break
            enc += bytes([(n & 0x7F) | 0x80])
            n >>= 7
        info += enc + b
    return scan.ELF + b"\x00" * 300 + info + b"\x00" * (size - 304 - len(info))


class Readers(unittest.TestCase):
    def test_jks(self):
        d1 = st.pem_certs(pem("emsign-ecc-root-ca-g3"))[0][0]
        d2 = st.pem_certs(pem("aaa-certificate-services"))[0][0]
        self.assertEqual(scan.jks_certs(fake_jks([d1, d2])), [d1, d2])
        self.assertEqual(scan.jks_certs(fake_jks([d1], version=1)), [d1])
        self.assertIsNone(scan.jks_certs(b"\x30\x82\x01\x00" + b"\x00" * 40), "PKCS12: not JKS")
        self.assertEqual(scan.jks_certs(b"\xfe\xed\xfe\xed" + struct.pack(">II", 2, 3) + b"\x00" * 3), [], "truncated: what was read")

    def test_go_buildinfo(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "tool")
            open(p, "wb").write(fake_go("go1.22.5", "github.com/example/tool"))
            self.assertEqual(scan.go_buildinfo(p), ("go1.22.5", "github.com/example/tool"))
            open(p, "wb").write(fake_go("go1.27.3"))
            self.assertEqual(scan.go_buildinfo(p), ("go1.27.3", ""))
            open(p, "wb").write(b"\x7fELF" + b"\x00" * 2000 + b"just some go1.19.2 text (an old layout)" + b"\x00" * 100)
            self.assertIsNone(scan.go_buildinfo(p), "no build information: not a Go program")
            open(p, "wb").write(b"#!/bin/sh\necho go1.22.5\n" + b"\x00" * 100)
            self.assertIsNone(scan.go_buildinfo(p), "not ELF")

    def test_java_version_from_the_release_file(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "jdk", "lib", "security"))
            open(os.path.join(d, "jdk", "release"), "w").write('IMPLEMENTOR="Red Hat"\nJAVA_VERSION="17.0.9"\n')
            open(os.path.join(d, "jdk", "lib", "security", "cacerts"), "wb").write(b"")
            self.assertEqual(scan.java_version(os.path.join(d, "jdk", "lib", "security", "cacerts")), "17.0.9")
            self.assertEqual(scan.java_version(os.path.join(d, "nothing", "cacerts")), "")


def hit(name, how="certificate", **kw):
    e = dict(KNOWN_BY_NAME[name], how=how)
    e.update(kw)
    return e


RESULT = {
    "stores": [
        {"kind": "system", "where": "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem", "version": "", "count": 146,
         "hits": [hit("emSign ECC Root CA - G3"), hit("AAA Certificate Services")], "blocked": [], "note": ""},
        {"kind": "system", "where": "/etc/pki/ca-trust/extracted/openssl/ca-bundle.trust.crt", "version": "", "count": 146,
         "hits": [hit("emSign ECC Root CA - G3"), hit("AAA Certificate Services")], "blocked": [], "note": ""},
        {"kind": "p11-kit", "where": "p11-kit trust (the live anchors)", "version": "", "count": 146, "hits": [hit("emSign ECC Root CA - G3")], "blocked": [], "note": ""},
        {"kind": "python", "where": "/opt/app/venv/lib/python3.12/site-packages/certifi/cacert.pem", "version": "2025.8.3", "count": 150, "hits": [hit("emSign ECC Root CA - G3")], "blocked": [], "note": ""},
        {"kind": "python", "where": "/opt/app/venv/lib/python3.12/site-packages/pip/_vendor/certifi/cacert.pem", "version": "2025.1.31", "count": 150, "hits": [hit("emSign ECC Root CA - G3")], "blocked": [], "note": ""},
        {"kind": "java", "where": "/usr/lib/jvm/java-17/lib/security/cacerts", "version": "17.0.9", "count": 140, "hits": [], "blocked": [], "note": ""},
        {"kind": "file", "where": "/opt/app/certs/bundle.pem", "version": "", "count": 3, "hits": [hit("EM DV TLS CA - G3B", "key", cn="EM DV TLS CA - G3B")], "blocked": [], "note": ""},
        {"kind": "file", "where": "/opt/app/certs/sectigo.pem", "version": "", "count": 1, "hits": [hit("AAA Certificate Services")], "blocked": [], "note": ""},
        {"kind": "container", "where": "container web (root)", "version": "registry/web:1", "count": 140, "hits": [hit("emSign ECC Root CA - G3")], "blocked": ["emSign ECC Root CA - G3"], "note": ""},
    ],
    "software": [
        {"kind": "ca-certificates", "where": "package ca-certificates", "program": "ca-certificates", "version": "2025.2.80-7", "fixed": "", "status": "unknown"},
        {"kind": "go", "where": "/usr/bin/dockerd", "program": "github.com/docker/docker/cmd/dockerd", "version": "1.24.6", "fixed": "", "status": "unsupported"},
        {"kind": "go", "where": "/usr/local/bin/tool", "program": "github.com/example/tool", "version": "1.27.1", "fixed": "1.27.3", "status": "below"},
        {"kind": "go", "where": "/usr/local/bin/fine", "program": "github.com/example/fine", "version": "1.27.3", "fixed": "1.27.3", "status": "ok"},
        {"kind": "node", "where": "/usr/bin/node", "program": "Node.js", "version": "20.9.0", "fixed": "", "status": "unsupported"},
        {"kind": "nss", "where": "package nss", "program": "NSS", "version": "3.126.0-1", "fixed": "3.131.1", "status": "below"},
    ],
    "not_read": [{"kind": "java", "where": "/opt/x/cacerts", "reason": "keytool could not list it"}],
    "counts": {"files": 9, "certificates": 1016},
}


class Filters(unittest.TestCase):
    def test_findings_grouped(self):
        f = tf.trust_findings(RESULT)
        ids = [x["id"] for x in f]
        self.assertEqual(ids, ["trust:system", "trust:python", "trust:file:/opt/app/certs/bundle.pem",
                               "trust:software:go", "trust:software:node", "trust:software:nss"])
        by = dict((x["id"], x) for x in f)
        self.assertEqual(by["trust:system"]["severity"], "warning")
        self.assertIn("the system trust (tls-ca-bundle.pem and 2 more, ca-certificates 2025.2.80-7) trusts emSign ECC Root CA - G3 - it cross-signs", by["trust:system"]["summary"])
        self.assertNotIn("AAA Certificate Services", by["trust:system"]["summary"], "notice-level: not in the finding")
        self.assertIn("update-ca-trust", by["trust:system"]["hint"])
        self.assertIn("2 Python certifi bundles (/opt/app/venv/lib/python3.12/site-packages/certifi/cacert.pem, ", by["trust:python"]["summary"])
        self.assertIn("pip install -U 'certifi>=2026.10.19'", by["trust:python"]["hint"])
        self.assertTrue(by["trust:python"]["hint"].endswith("the environments: /opt/app/venv"), by["trust:python"]["hint"])
        self.assertEqual(by["trust:file:/opt/app/certs/bundle.pem"]["severity"], "critical")
        self.assertIn("trusts EM DV TLS CA - G3B (the same key, as EM DV TLS CA - G3B) - its private key is computable", by["trust:file:/opt/app/certs/bundle.pem"]["summary"])
        self.assertIn("2 Go programs built with a Go before the fix: github.com/docker/docker/cmd/dockerd (/usr/bin/dockerd, Go 1.24.6); github.com/example/tool (/usr/local/bin/tool, Go 1.27.1)", by["trust:software:go"]["summary"])
        self.assertIn("Node.js 20.9.0 (/usr/bin/node) is a line older than the ones that got the fix", by["trust:software:node"]["summary"])
        self.assertIn("NSS 3.126.0-1 is below 3.131.1", by["trust:software:nss"]["summary"])
        # the container's root is blocked inside the container: no finding for it; the notice-only file: none
        self.assertNotIn("trust:container:container web (root)", ids)
        self.assertNotIn("trust:file:/opt/app/certs/sectigo.pem", ids)

    def test_severity_overrides_and_software_severity(self):
        f = tf.trust_findings(RESULT, {"emSign ECC Root CA - G3": "notice", "AAA Certificate Services": "critical"}, "critical")
        by = dict((x["id"], x) for x in f)
        self.assertEqual(by["trust:system"]["severity"], "critical")
        self.assertIn("trusts AAA Certificate Services - Sectigo's root", by["trust:system"]["summary"])
        self.assertNotIn("trust:python", by, "its only hit is notice now")
        self.assertEqual(by["trust:file:/opt/app/certs/sectigo.pem"]["severity"], "critical")
        self.assertEqual(by["trust:software:go"]["severity"], "critical")

    def test_blocklisted_but_still_in_the_bundle(self):
        r = {"stores": [{"kind": "system", "where": "/etc/ssl/certs/ca-certificates.crt", "count": 140, "hits": [hit("emSign ECC Root CA - G3")], "blocked": []},
                        {"kind": "blocklist", "where": "/etc/pki/ca-trust/source/blocklist/emsign.pem", "count": 1, "hits": [], "blocked": ["emSign ECC Root CA - G3"]}],
             "software": [], "not_read": [], "counts": {}}
        f = tf.trust_findings(r)
        self.assertEqual(len(f), 1)
        self.assertIn("emSign ECC Root CA - G3 is in the blocklist already: run update-ca-trust", f[0]["summary"])

    def test_nothing_read(self):
        f = tf.trust_findings({"stores": [], "software": [], "not_read": [], "counts": {}})
        self.assertEqual([x["id"] for x in f], ["trust:unreadable"])
        f = tf.trust_findings({"stores": [{"kind": "system", "where": "/etc/ssl/cert.pem", "count": 1, "hits": [], "blocked": []}], "software": [], "not_read": [], "counts": {}})
        self.assertEqual(f, [])

    def test_rows_and_sections(self):
        r = tf.trust_result(RESULT, "web01.example.mil")
        self.assertEqual(len(r["rows"]), 9 + 6)
        self.assertEqual(r["rows"][0][:3], ["web01.example.mil", "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem", "system CA bundle"])
        self.assertEqual(r["rows"][0][4], "146 certificates: emSign ECC Root CA - G3, AAA Certificate Services [notice]")
        self.assertEqual(r["rows"][0][5], "distrusted root still trusted")
        self.assertEqual(r["row_status"][0], "warning")
        container = [x for x in r["rows"] if x[1].startswith("container web")][0]
        self.assertEqual((container[4], container[5]), ("140 certificates: blocked: emSign ECC Root CA - G3", "blocked"))
        self.assertEqual(r["skipped"], [["web01.example.mil", "/opt/x/cacerts", "keytool could not list it"]])
        self.assertEqual(r["lines"][0], "9 stores read, 1016 certificates, 6 programs looked at; 1 not read")
        self.assertEqual(r["hits"], 6)
        vm = {"stores": [{"vcenter": "vc01.example.mil", "kind": "vcenter", "name": "vc01.example.mil", "count": 12, "hits": [hit("emSign ECC Root CA - G3")], "blocked": []},
                         {"vcenter": "vc01.example.mil", "kind": "esxi", "name": "esx01.example.mil", "count": 3, "hits": [], "blocked": []}],
              "not_read": [{"vcenter": "vc01.example.mil", "kind": "esxi", "name": "esx02.example.mil", "why": "not connected (disconnected)"}],
              "vcenters": [{"name": "vc01.example.mil", "ok": True, "error": ""}, {"name": "vc02.example.mil", "ok": False, "error": "refused the login"}]}
        secs = tf.trust_report_sections([r], vm)
        self.assertEqual([s["title"] for s in secs], ["1. Remove now - a compromised CA is trusted", "2. Remove next - a distrusted root is still trusted",
                                                      "3. Then update - built-in roots and programs with their own trust logic", "4. Listed only - your decision",
                                                      "Everything read - every trust store and program, worst first",
                                                      "VMware - everything read: vCenter's trusted roots and each ESXi host's trusted CAs", "Not read"])
        self.assertEqual([x[1] for x in secs[0]["rows"]], ["/opt/app/certs/bundle.pem"])
        self.assertTrue(secs[0]["rows"][0][4].startswith("take it out of the file"), secs[0]["rows"][0][4])
        self.assertIn("vc01.example.mil", [x[0] for x in secs[1]["rows"]], "vCenter's store is in the remove-next list")
        self.assertIn("/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem", [x[1] for x in secs[1]["rows"]])
        self.assertTrue(all("emSign ECC Root CA - G3" in x[3] for x in secs[1]["rows"]))
        self.assertTrue(any(x[2].startswith("Go program") for x in secs[2]["rows"]) and any(x[2].startswith("Node") for x in secs[2]["rows"]), secs[2]["rows"])
        self.assertEqual(secs[3]["status"], "ok")
        self.assertEqual(secs[4]["row_status"][0], "critical", "worst first")
        self.assertEqual(secs[4]["rows"][0][1], "/opt/app/certs/bundle.pem")
        self.assertEqual(secs[4]["status"], "critical")
        self.assertEqual([x[4] for x in secs[5]["rows"]], ["distrusted root still trusted", "ok", "not read", "not read"])
        self.assertEqual(secs[5]["status"], "warning")
        self.assertEqual(tf.store_status(vm["stores"][0]), "warning")


class VmwareApi(unittest.TestCase):
    def setUp(self):
        _load("ansible.module_utils.site_vcenters", os.path.join(ROOT, "roles", "vmware_vm", "module_utils", "site_vcenters.py"))
        sys.path.insert(0, os.path.join(ROOT, "roles", "vmware_vm", "library"))
        import site_vmware_trust as vt
        self.vt = vt

    def test_trusted_roots_new_and_old_api(self):
        root = pem("emsign-ecc-root-ca-g3")
        calls = []

        def fake(method, path, auth=False):
            calls.append(path)
            if path.endswith("/trusted-root-chains"):
                return 200, [{"chain": "A1"}, {"chain": "B2"}] if path.startswith("/api") else {"value": [{"chain": "A1"}, {"chain": "B2"}]}
            if path.endswith("/A1"):
                d = {"cert_chain": {"cert_chain": [root]}}
                return 200, d if path.startswith("/api") else {"value": d}
            if path.endswith("/B2"):
                return 404, None
            return 500, "boom"
        api = self.vt.Api("vc01.example.mil", 443, False, 5)
        api.call = fake
        chains = api.trusted_roots("vc01.example.mil")
        self.assertEqual([c for c, _ in chains], ["A1"])
        text = chains[0][1]
        self.assertEqual(st.Known(KNOWN).scan(st.pem_certs(text))["hits"][0]["name"], "emSign ECC Root CA - G3")
        self.assertEqual(calls, ["/api/vcenter/certificate-management/vcenter/trusted-root-chains",
                                 "/api/vcenter/certificate-management/vcenter/trusted-root-chains/A1",
                                 "/api/vcenter/certificate-management/vcenter/trusted-root-chains/B2"])
        api.old = True
        calls[:] = []
        self.assertEqual(api.trusted_roots("vc01.example.mil"), chains)
        self.assertTrue(calls[0].startswith("/rest/"))
        api.call = lambda m, p, auth=False: (404, None)
        with self.assertRaises(RuntimeError) as cm:
            api.trusted_roots("vc01.example.mil")
        self.assertIn("HTTP 404", str(cm.exception))
        api.call = lambda m, p, auth=False: (403, None)
        with self.assertRaises(RuntimeError) as cm:
            api.trusted_roots("vc01.example.mil")
        self.assertIn("CertificateManagement.Manage", str(cm.exception))


    def test_remove_deletes_only_the_chains_with_a_hit(self):
        root, aaa = pem("emsign-ecc-root-ca-g3"), pem("aaa-certificate-services")
        calls = []

        def fake(method, path, auth=False):
            calls.append((method, path.rsplit("/", 1)[-1]))
            if method == "POST":
                return 201, "tok"
            if method == "DELETE":
                return 204, None
            if path.endswith("/trusted-root-chains"):
                return 200, [{"chain": "A1"}, {"chain": "B2"}]
            return 200, {"cert_chain": {"cert_chain": [root if path.endswith("/A1") else aaa]}}
        self.vt.Api.call = staticmethod(fake)
        try:
            known = st.Known(KNOWN)
            stores, not_read, removed = [], [], []
            # check mode: the plan, no DELETE
            self.vt.read_vcenter(known, "vc01.example.mil", False, False, 5, stores, not_read, ({"critical", "warning"}, True, removed))
            self.assertEqual(not_read, [])
            self.assertEqual([r["names"] for r in removed], [["emSign ECC Root CA - G3"]])
            self.assertTrue(removed[0]["planned"] and not removed[0]["done"])
            self.assertEqual([c for m, c in calls if m == "DELETE"], ["session"])        # only the logout
            # for real: the root's chain goes, AAA (notice) stays
            calls[:] = []
            removed[:] = []
            self.vt.read_vcenter(known, "vc01.example.mil", False, False, 5, stores, not_read, ({"critical", "warning"}, False, removed))
            self.assertEqual([c for m, c in calls if m == "DELETE"], ["A1", "session"])
            self.assertTrue(removed[0]["done"])
            # without remove: nothing deleted
            calls[:] = []
            self.vt.read_vcenter(known, "vc01.example.mil", False, False, 5, stores, not_read)
            self.assertEqual([c for m, c in calls if m == "DELETE"], ["session"])
        finally:
            del self.vt.Api.call


class FakeModule(object):
    """Enough of AnsibleModule for the removal module."""
    def __init__(self, check=False, **params):
        import shutil
        self.params = dict(known=KNOWN, stores=[], kinds=["system", "java", "python", "file"], severities=["critical", "warning"],
                           backup_dir="", blocklist_dir="", update_cmd="", keystore_password="changeit", timeout=30)
        self.params.update(params)
        self.check_mode = check
        self.which = shutil.which

    def get_bin_path(self, name):
        return self.which(name)


def _der(name):
    import base64
    return base64.b64decode("".join(pem(name).split("-----")[2].split()))


def _jks(*names):
    """A JKS with one trusted-certificate entry per (alias, fixture)."""
    entries = b""
    for n, fixture in names:
        d = _der(fixture)
        entries += struct.pack(">I", 2) + struct.pack(">H", len(n)) + n.encode() + b"\x00" * 8 + struct.pack(">H", 5) + b"X.509" + struct.pack(">I", len(d)) + d
    return b"\xfe\xed\xfe\xed" + struct.pack(">II", 2, len(names)) + entries + b"\x00" * 20   # a made-up digest: keytool would refuse it


class Removal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="trust-remove-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_remove(self, stores, check=False, **params):
        params.setdefault("backup_dir", os.path.join(self.tmp, "backup"))
        m = FakeModule(check=check, stores=stores, **params)
        return rm.Remover(m).all()

    def test_jks_round_trip_and_keytool_accepts_the_digest(self):
        blob = _jks(("emsign", "emsign-ecc-root-ca-g3"), ("sectigo", "aaa-certificate-services"))
        version, entries = rm.jks_entries(blob)
        self.assertEqual([e[0] for e in entries], ["emsign", "sectigo"])
        out = rm.jks_write(version, entries[1:], "changeit")
        self.assertEqual(len(scan.jks_certs(out)), 1)
        self.assertEqual(st.Known(KNOWN).scan([(c, False) for c in scan.jks_certs(out)])["hits"][0]["name"], "AAA Certificate Services")
        kt = FakeModule().get_bin_path("keytool")
        if kt:
            f = os.path.join(self.tmp, "cacerts")
            open(f, "wb").write(out)
            r = subprocess.run([kt, "-list", "-keystore", f, "-storepass", "changeit"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(b"sectigo", r.stdout)

    def test_java_keystore_loses_the_root_and_keeps_the_rest(self):
        f = os.path.join(self.tmp, "jdk", "cacerts")
        os.makedirs(os.path.dirname(f))
        open(f, "wb").write(_jks(("emsign", "emsign-ecc-root-ca-g3"), ("sectigo", "aaa-certificate-services")))
        store = {"kind": "java", "where": f, "hits": [{"name": "emSign ECC Root CA - G3", "severity": "warning"}, {"name": "AAA Certificate Services", "severity": "notice"}]}
        plan = self.run_remove([store], check=True)
        self.assertEqual(plan["actions"][0]["aliases"], ["emsign"])
        self.assertFalse(plan["actions"][0]["done"]) and self.assertTrue(plan["actions"][0]["planned"])
        self.assertEqual(len(scan.jks_certs(open(f, "rb").read())), 2)
        r = self.run_remove([store])
        self.assertTrue(r["changed"] and r["actions"][0]["done"], r)
        self.assertTrue(os.path.isfile(r["actions"][0]["backup"]))
        certs = scan.jks_certs(open(f, "rb").read())
        self.assertEqual(len(certs), 1)
        self.assertEqual(st.subject_cn(certs[0]), "AAA Certificate Services")
        # a cacerts that follows the system trust is left alone
        store2 = dict(store, where="/etc/pki/java/cacerts")
        r = self.run_remove([store2])
        self.assertIn("follows the system trust", r["not_removed"][0]["why"])

    def test_bundle_loses_the_block_and_its_comment_lines(self):
        f = os.path.join(self.tmp, "certifi", "cacert.pem")
        os.makedirs(os.path.dirname(f))
        text = ("## Bundle of CA Root Certificates\n\n# Issuer: CN=emSign ECC Root CA - G3\n# Label: \"emSign ECC Root CA - G3\"\n" + pem("emsign-ecc-root-ca-g3")
                + "\n# Issuer: CN=AAA Certificate Services\n# Label: \"AAA Certificate Services\"\n" + pem("aaa-certificate-services"))
        open(f, "w").write(text)
        os.chmod(f, 0o640)
        store = {"kind": "python", "where": f, "hits": [{"name": "emSign ECC Root CA - G3", "severity": "warning"}]}
        r = self.run_remove([store])
        self.assertTrue(r["actions"][0]["done"], r)
        self.assertIn("1 certificates kept", r["actions"][0]["action"])
        after = open(f).read()
        self.assertNotIn("emSign", after)
        self.assertTrue(after.startswith("## Bundle of CA Root Certificates\n\n# Issuer: CN=AAA"), after[:120])
        self.assertEqual(len(st.pem_certs(after)), 1)
        self.assertEqual(oct(os.stat(f).st_mode & 0o777), "0o640")
        self.assertEqual(open(r["actions"][0]["backup"]).read(), text)
        # a certifi bundle that holds nothing else is left alone (requests would be left without roots)
        open(f, "w").write(pem("emsign-ecc-root-ca-g3"))
        r = self.run_remove([store])
        self.assertEqual(r["actions"], [])
        self.assertIn("holds nothing else", r["not_removed"][0]["why"])
        self.assertTrue(os.path.isfile(f))
        # a severity that is not asked for: left alone
        open(f, "w").write(text)
        r = self.run_remove([store], severities=["critical"])
        self.assertEqual(r["actions"], [])
        self.assertEqual(open(f).read(), text)

    def test_a_file_that_is_the_certificate_is_moved_away(self):
        f = os.path.join(self.tmp, "app", "chain.cer")
        os.makedirs(os.path.dirname(f))
        open(f, "wb").write(_der("dv-g3b-emudhra"))
        store = {"kind": "file", "where": f, "hits": [{"name": "EM DV TLS CA - G3B", "severity": "critical"}]}
        r = self.run_remove([store])
        self.assertTrue(r["actions"][0]["done"], r)
        self.assertIn("moved", r["actions"][0]["action"])
        self.assertFalse(os.path.exists(f))
        self.assertTrue(os.path.isfile(r["actions"][0]["backup"]))

    def test_system_bundle_is_blocklisted_not_edited(self):
        f = os.path.join(self.tmp, "etc", "tls-ca-bundle.pem")
        bl = os.path.join(self.tmp, "blocklist")
        os.makedirs(os.path.dirname(f))
        text = pem("emsign-ecc-root-ca-g3") + pem("aaa-certificate-services") + pem("dv-g3b-emudhra")
        open(f, "w").write(text)
        stores = [{"kind": "system", "where": f, "hits": [{"name": "emSign ECC Root CA - G3", "severity": "warning"},
                                                           {"name": "EM DV TLS CA - G3B", "severity": "critical"},
                                                           {"name": "AAA Certificate Services", "severity": "notice"}]},
                  {"kind": "p11-kit", "where": "p11-kit trust", "hits": [{"name": "emSign ECC Root CA - G3", "severity": "warning"}]},
                  {"kind": "container", "where": "container web (root)", "hits": [{"name": "emSign ECC Root CA - G3", "severity": "warning"}]}]
        plan = self.run_remove(stores, check=True, blocklist_dir=bl, update_cmd="true")
        self.assertFalse(os.path.isdir(bl))
        self.assertEqual(plan["actions"][0]["names"], ["EM DV TLS CA - G3B", "emSign ECC Root CA - G3"])
        r = self.run_remove(stores, blocklist_dir=bl, update_cmd="true")
        self.assertTrue(r["actions"][0]["done"], r)
        self.assertEqual(sorted(os.listdir(bl)), ["EM-DV-TLS-CA-G3B.pem", "emSign-ECC-Root-CA-G3.pem"])
        self.assertEqual(open(f).read(), text)                       # the bundle itself is not touched
        self.assertEqual(st.Known(KNOWN).scan(st.pem_certs(open(os.path.join(bl, "emSign-ECC-Root-CA-G3.pem")).read()))["hits"][0]["name"], "emSign ECC Root CA - G3")
        self.assertEqual([n["kind"] for n in r["not_removed"]], ["p11-kit", "container"])
        # a second run writes nothing new
        r = self.run_remove(stores, blocklist_dir=bl, update_cmd="true")
        self.assertNotIn("written", r["actions"][0])
        # the rebuild command failing is an error on the action
        r = self.run_remove(stores, blocklist_dir=bl, update_cmd="false")
        self.assertIn("failed", r["actions"][0]["error"])


class VmwareDoc(unittest.TestCase):
    def test_doc_title_status_and_why_the_job_fails(self):
        root = pem("emsign-ecc-root-ca-g3")
        hit = st.Known(KNOWN).scan(st.pem_certs(root))
        vm = {"stores": [dict(hit, vcenter="vc01.example.mil", kind="vcenter", name="vc01.example.mil"),
                         {"vcenter": "vc01.example.mil", "kind": "esxi", "name": "esx01", "count": 3, "hits": [], "blocked": []}],
              "not_read": [{"vcenter": "vc01.example.mil", "kind": "esxi", "name": "esx02", "why": "not connected"}], "vcenters": []}
        d = tf.trust_vmware_doc(vm)
        self.assertEqual(d["status"], "warning")
        self.assertTrue(d["title"].startswith("VMware trust store report: 1 store holds a compromised or dropped CA - 1 vCenter, 1 ESXi host read, 1 not read"), d["title"])
        self.assertEqual(d["bad"], ["vCenter trusted roots vc01.example.mil (vc01.example.mil) trusts emSign ECC Root CA - G3"])
        self.assertEqual([s["title"] for s in d["sections"]], ["2. Remove next - a distrusted root is still trusted",
                                                               "VMware - everything read: vCenter's trusted roots and each ESXi host's trusted CAs"])
        self.assertEqual(len(d["sections"][1]["rows"]), 3)
        # the root set to notice: nothing fails
        d = tf.trust_vmware_doc(vm, {"emSign ECC Root CA - G3": "notice"})
        self.assertEqual(d["bad"], [])
        self.assertEqual(d["status"], "ok")
        # nothing read at all: the job fails and says why
        d = tf.trust_vmware_doc({"stores": [], "not_read": vm["not_read"], "vcenters": []})
        self.assertEqual(d["status"], "warning")
        self.assertIn("nothing could be read: esxi esx02: not connected", d["bad"][0])
        # a removal: the title counts it, the Removed table comes first
        vm2 = dict(vm, removed=[{"vcenter": "vc01.example.mil", "kind": "vcenter", "name": "vc01.example.mil", "names": ["emSign ECC Root CA - G3"],
                                 "action": "deleted the chain A1", "done": True, "planned": False, "error": ""}])
        d = tf.trust_vmware_doc(vm2)
        self.assertTrue(d["title"].endswith("; 1 removed"), d["title"])
        self.assertEqual([x["title"] for x in d["sections"]][1], "Removed (check_trust_remove)")


class TwoCopies(unittest.TestCase):
    def test_site_trust_is_the_same_file_in_both_roles(self):
        a = os.path.join(ROOT, "roles", "check_trust", "module_utils", "site_trust.py")
        b = os.path.join(ROOT, "roles", "vmware_vm", "module_utils", "site_trust.py")
        self.assertTrue(filecmp.cmp(a, b, shallow=False), "roles/check_trust/module_utils/site_trust.py and roles/vmware_vm/module_utils/site_trust.py differ: copy one over the other")


if __name__ == "__main__":
    unittest.main(verbosity=1)
