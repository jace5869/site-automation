"""Unit tests for the Linux certificate install (roles/check_certs/library/site_cert_install.py) - the file layouts
and the reasons to wait, against real openssl files in a temporary folder: a binary (DER) file, a key and
certificate in one file (haproxy), a chain file with its root, a key with a password, the old key in two files, a
key another certificate also uses, a keystore, a path something else deploys, the issued certificate for another
key. The services are not reached (cert_install_services names a stand-in).
    python3 tests/test_cert_install.py
The whole install, through the playbooks: tests/certs/run_cert_install_test.sh"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "roles", "check_certs", "library"))
import site_cert_install as m  # noqa: E402

W = tempfile.mkdtemp(prefix="sa-install-")


def ossl(*args, data=None):
    r = subprocess.run(["openssl"] + list(args), cwd=W, input=data, capture_output=True)
    if r.returncode:
        raise RuntimeError("openssl %s: %s" % (" ".join(args), r.stderr.decode()))
    return r.stdout


def read(p):
    with open(os.path.join(W, p), "rb") as fh:
        return fh.read()


def write(p, data, mode=0o644):
    p = os.path.join(W, p)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(data if isinstance(data, bytes) else data.encode())
    os.chmod(p, mode)
    return p


class FakeModule(object):
    """What site_cert_install asks of AnsibleModule: run a command, find a program, its settings."""
    check_mode = False

    def __init__(self, **params):
        self.params = dict(dir=os.path.join(W, "renew"), services=[{"path": "*", "service": "web"}], never_reload=[],
                           key_files={}, key_dirs=[os.path.join(W, "keys")], known=[{"dir": os.path.join(W, "noconf"), "service": "x"}],
                           trust_check=True, verify_wait=1, backup="", only="", job="", install_notes={}, by_hand="")
        self.params.update(params)

    def run_command(self, args, data=None, binary_data=False):
        if args[0] == "systemctl":
            return 0, "LoadState=loaded\nActiveState=active\nCanReload=yes\n", ""
        if args[0] in ("ss", "ps"):
            return 0, "", ""
        r = subprocess.run(args, input=(data.encode() if isinstance(data, str) else data), capture_output=True)
        return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")

    def get_bin_path(self, name):
        return shutil.which(name)


def setUpModule():
    os.makedirs(os.path.join(W, "renew"))
    os.makedirs(os.path.join(W, "keys"))
    os.makedirs(os.path.join(W, "noconf"))
    ossl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "root.key", "-out", "root.crt", "-days", "400", "-subj", "/CN=TEST ROOT",
         "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    ossl("req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", "int.key", "-out", "int.csr", "-subj", "/CN=TEST INT")
    write("ca.ext", "basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\n")
    ossl("x509", "-req", "-in", "int.csr", "-CA", "root.crt", "-CAkey", "root.key", "-CAcreateserial", "-days", "390", "-out", "int.crt", "-extfile", "ca.ext")


def tearDownModule():
    shutil.rmtree(W, ignore_errors=True)


def leaf(name, key=None):
    """A certificate for name (a new key, or key), signed by the intermediate -> (cert pem, key path)."""
    key = key or os.path.join(W, "%s.key" % name)
    if not os.path.exists(key):
        ossl("genrsa", "-out", key, "2048")
    ossl("req", "-new", "-key", key, "-out", "%s.csr" % name, "-subj", "/CN=%s.example.mil" % name,
         "-addext", "subjectAltName=DNS:%s.example.mil" % name)
    ossl("x509", "-req", "-in", "%s.csr" % name, "-CA", "int.crt", "-CAkey", "int.key", "-CAcreateserial", "-days", "200",
         "-out", "%s.crt" % name, "-copy_extensions", "copy")
    return read("%s.crt" % name).decode(), key


def fp(pem):
    return m.sha256_pem(pem)


def renewal(name, old_pem, found_in):
    """What the renewal leaves in the folder for the old certificate: <cn>-<fp8>.key/.csr/.json, and the issued
    certificate for that new key (as site_cert_issued hands it on)."""
    base = "%s.example.mil-%s" % (name, fp(old_pem)[:8])
    key = os.path.join(W, "renew", base + ".key")
    ossl("genrsa", "-out", key, "2048")
    ossl("req", "-new", "-key", key, "-out", os.path.join(W, "renew", base + ".csr"), "-subj", "/CN=%s.example.mil" % name)
    with open(os.path.join(W, "renew", base + ".json"), "w") as fh:
        json.dump({"certificate": "CN=%s.example.mil" % name, "found_in": found_in, "names": ["DNS:%s.example.mil" % name]}, fh)
    new_pem, _ = leaf(name + "-new", key)
    return base, {"base": base, "ritm": "RITM1", "cert": {"pem": new_pem, "sha256": fp(new_pem), "serial": "1", "names": ["dns:%s.example.mil" % name]},
                  "chain": [{"pem": read("int.crt").decode()}, {"pem": read("root.crt").decode()}], "problems": []}


def plan(iss, **kw):
    return m.Installer(FakeModule(**kw)).plan_one(iss)


class Layouts(unittest.TestCase):
    def test_der_file_and_its_key(self):
        old, okey = leaf("der01")
        der = write("pki/der01.cer", ossl("x509", "-in", "der01.crt", "-outform", "DER"))
        k = write("keys/der01.key", read("der01.key"), 0o600)
        base, iss = renewal("der01", old, [der])
        p = plan(iss)
        self.assertEqual(p["status"], "ready", p["reason"])
        f = {x["path"]: x for x in p["files"]}
        self.assertEqual((f[der]["format"], f[k]["role"]), ("der", "key"))
        out = m.Installer(FakeModule()).contents(p, m.Installer(FakeModule()).load(base))
        self.assertEqual(hashlib.sha256(out[der]).hexdigest(), iss["cert"]["sha256"])        # binary, as before
        self.assertIn(b"PRIVATE KEY", out[k])

    def test_key_and_certificate_in_one_file(self):
        old, okey = leaf("hap01")
        both = write("pki/hap01.pem", read("hap01.key").decode() + read("hap01.crt").decode() + read("int.crt").decode(), 0o600)
        base, iss = renewal("hap01", old, [both])
        p = plan(iss)
        self.assertEqual(p["status"], "ready", p["reason"])
        self.assertEqual([x["path"] for x in p["files"]], [both])                          # no other key file
        self.assertEqual(p["files"][0]["layout"], ["key", "leaf", "ca"])
        out = m.Installer(FakeModule()).contents(p, m.Installer(FakeModule()).load(base))[both].decode()
        blocks = m.blocks_of(out)
        self.assertEqual([k for k, _ in blocks], ["key", "cert", "cert"])                   # key first, as before
        self.assertEqual(fp(blocks[1][1]), iss["cert"]["sha256"])
        self.assertEqual(blocks[0][1].strip(), read("renew/%s.key" % base).decode().strip())

    def test_chain_keeps_its_root_when_it_had_one(self):
        old, _ = leaf("ch01")
        full = write("pki/ch01-full.pem", read("ch01.crt").decode() + read("int.crt").decode() + read("root.crt").decode())
        write("keys/ch01.key", read("ch01.key"), 0o600)
        base, iss = renewal("ch01", old, [full])
        p = plan(iss)
        self.assertEqual(p["status"], "ready", p["reason"])
        out = m.Installer(FakeModule()).contents(p, m.Installer(FakeModule()).load(base))[full].decode()
        self.assertEqual(len(m.blocks_of(out)), 3)

    def test_another_certificate_in_the_file_is_kept(self):
        old, _ = leaf("mix01")
        other, _ = leaf("mix02")
        mixed = write("pki/mix01-bundle.pem", old + read("int.crt").decode() + other)
        write("keys/mix01.key", read("mix01.key"), 0o600)
        base, iss = renewal("mix01", old, [mixed])
        p = plan(iss)
        self.assertEqual(p["status"], "ready", p["reason"])
        self.assertEqual([f["layout"] for f in p["files"] if f["role"] == "cert"], [["leaf", "ca", "other"]])
        out = m.Installer(FakeModule()).contents(p, m.Installer(FakeModule()).load(base))[mixed].decode()
        got = [fp(b) for k, b in m.blocks_of(out)]
        self.assertEqual(got[0], iss["cert"]["sha256"])
        self.assertEqual(got[-1], fp(other))                                               # the other one, untouched

    def test_same_issuer_no_chain_sent_keeps_the_old_chain(self):
        old, _ = leaf("same01")
        full = write("pki/same01-full.pem", read("same01.crt").decode() + read("int.crt").decode())
        write("keys/same01.key", read("same01.key"), 0o600)
        base, iss = renewal("same01", old, [full])
        iss["chain"] = []
        p = plan(iss, trust_check=False)
        self.assertEqual(p["status"], "ready", p["reason"])
        self.assertTrue(any("its chain is kept" in n for n in p["notes"]), p["notes"])


class Held(unittest.TestCase):
    def test_key_with_a_password(self):
        old, _ = leaf("pw01")
        both = write("pki/pw01.pem", ossl("pkey", "-in", "pw01.key", "-aes256", "-passout", "pass:x").decode() + old)
        base, iss = renewal("pw01", old, [both])
        p = plan(iss)
        self.assertEqual(p["status"], "held")
        self.assertIn("has a password", p["reason"])

    def test_old_key_in_two_files(self):
        old, _ = leaf("two01")
        c = write("pki/two01.crt", old)
        write("keys/two01.key", read("two01.key"), 0o600)
        write("keys/two01.key.bak", read("two01.key"), 0o600)
        base, iss = renewal("two01", old, [c])
        p = plan(iss)
        self.assertEqual(p["status"], "held")
        self.assertIn("in several files", p["reason"])
        p = plan(iss, key_files={c: os.path.join(W, "keys/two01.key")})                     # named: ready
        self.assertEqual(p["status"], "ready", p["reason"])

    def test_old_key_not_found(self):
        old, _ = leaf("nokey01")
        c = write("pki/nokey01.crt", old)
        base, iss = renewal("nokey01", old, [c])
        p = plan(iss)
        self.assertEqual(p["status"], "held")
        self.assertIn("was not found", p["reason"])

    def test_key_shared_with_another_certificate(self):
        old, okey = leaf("sh01")
        c = write("pki/sh01.crt", old)
        write("keys/sh01.key", read("sh01.key"), 0o600)
        other, _ = leaf("sh02", okey)                                                       # another certificate, the same key
        write("keys/sh02.crt", other)
        base, iss = renewal("sh01", old, [c])
        p = plan(iss)
        self.assertEqual(p["status"], "held")
        self.assertIn("also the key of", p["reason"])

    def test_not_for_this_key(self):
        old, _ = leaf("wk01")
        c = write("pki/wk01.crt", old)
        write("keys/wk01.key", read("wk01.key"), 0o600)
        base, iss = renewal("wk01", old, [c])
        stranger, _ = leaf("stranger")
        iss["cert"] = dict(iss["cert"], pem=stranger, sha256=fp(stranger))
        p = plan(iss)
        self.assertEqual(p["status"], "held")
        self.assertIn("not for the key on this host", p["reason"])

    def test_replaced_since(self):
        old, _ = leaf("rp01")
        c = write("pki/rp01.crt", old)
        write("keys/rp01.key", read("rp01.key"), 0o600)
        base, iss = renewal("rp01", old, [c])
        newer, _ = leaf("rp01b")
        write("pki/rp01.crt", newer)
        p = plan(iss)
        self.assertEqual(p["status"], "held")
        self.assertIn("replaced since", p["reason"])


class ByHand(unittest.TestCase):
    def test_keystore(self):
        old, _ = leaf("ks01")
        base, iss = renewal("ks01", old, ["/opt/app/keystore.jks (alias server)"])
        p = plan(iss)
        self.assertEqual(p["status"], "by hand")
        self.assertIn("keytool -importkeystore", " ".join(p["steps"]))

    def test_deployed_by_something_else(self):
        old, _ = leaf("dep01")
        c = write("stig/dep01.crt", old)
        base, iss = renewal("dep01", old, [c])
        p = plan(iss, install_notes={os.path.join(W, "stig") + "/": "STIG Manager: the AAP credential"})
        self.assertEqual(p["status"], "by hand")
        self.assertIn("STIG Manager", p["reason"])

    def test_aap_host(self):
        old, _ = leaf("aap01")
        c = write("pki/aap01.crt", old)
        base, iss = renewal("aap01", old, [c])
        p = plan(iss, by_hand="an AAP host")
        self.assertEqual((p["status"], p["reason"]), ("by hand", "an AAP host"))

    def test_service_that_cannot_reload(self):
        old, _ = leaf("rl01")
        c = write("pki/rl01.crt", old)
        write("keys/rl01.key", read("rl01.key"), 0o600)
        base, iss = renewal("rl01", old, [c])
        mod = FakeModule()
        mod.run_command = lambda args, data=None, binary_data=False: (
            (0, "LoadState=loaded\nActiveState=active\nCanReload=no\n", "") if args[0] == "systemctl" else FakeModule.run_command(mod, args, data, binary_data))
        p = m.Installer(mod).plan_one(iss)
        self.assertEqual(p["status"], "by hand")
        self.assertIn("cannot reload", p["reason"])


class Backups(unittest.TestCase):
    """The copies made before an install (cert_renew_dir/backup/<name>/) back in place - all of them or none - and a
    rollback that says when the service is not back on the old certificate."""

    def setUp(self):
        n = self.id().rsplit(".", 1)[-1]
        self.crt = write("bk/%s.crt" % n, "old cert\n")
        self.key = write("bk/%s.key" % n, "old key\n", 0o600)
        self.i = m.Installer(FakeModule())
        self.name = self.i.backup("bk-" + n, {"files": [{"path": self.crt, "role": "leaf", "old_sha256": "0" * 64}, {"path": self.key, "role": "key"}],
                                             "subject": "CN=bk", "new": {"sha256": "1" * 64},
                                             "units": [{"unit": "web.service", "reload": True, "test": None}], "verify": []}, {})
        write(self.crt, "new cert\n")
        write(self.key, "new key\n", 0o600)
        self.copies = os.path.join(W, "renew", "backup", self.name, "files")

    def live(self):
        return read(self.crt), read(self.key)

    def test_both_back_with_their_mode(self):
        os.chmod(self.key, 0o644)
        self.i.put_back(self.name)
        self.assertEqual(self.live(), (b"old cert\n", b"old key\n"))
        self.assertEqual(oct(os.stat(self.key).st_mode & 0o777), "0o600")

    def test_a_missing_copy_changes_nothing(self):
        os.remove(os.path.join(self.copies, "1-" + os.path.basename(self.key)))
        with self.assertRaises(IOError) as c:
            self.i.put_back(self.name)
        self.assertIn("nothing was put back", str(c.exception))
        self.assertEqual(self.live(), (b"new cert\n", b"new key\n"))          # never the old certificate next to the new key

    def test_a_damaged_copy_changes_nothing(self):
        with open(os.path.join(self.copies, "0-" + os.path.basename(self.crt)), "wb") as fh:
            fh.write(b"not the certificate\n")
        with self.assertRaises(IOError) as c:
            self.i.put_back(self.name)
        self.assertIn("checksum differs", str(c.exception))
        self.assertEqual(self.live(), (b"new cert\n", b"new key\n"))

    def test_restore_says_failed_and_leaves_the_files(self):
        os.remove(os.path.join(self.copies, "1-" + os.path.basename(self.key)))
        _, out = m.Installer(FakeModule(backup=self.name)).restore()
        self.assertEqual(out[0]["status"], "failed")
        self.assertIn("nothing was put back", out[0]["reason"])
        self.assertEqual(self.live(), (b"new cert\n", b"new key\n"))

    def test_rollback_says_when_the_reload_fails_again(self):
        mod = FakeModule()
        mod.run_command = lambda args, data=None, binary_data=False: (
            (1, "", "Job for web.service failed") if args[:2] == ["systemctl", "reload"] else FakeModule.run_command(mod, args, data, binary_data))
        r = m.Installer(mod).rolled(self.name, [self.crt, self.key], ["web.service"], "systemctl reload web.service failed: x")
        self.assertEqual(r["status"], "failed")
        self.assertIn("the old files are back, but systemctl reload web.service failed again (Job for web.service failed)", r["reason"])
        self.assertIn("Check the service by hand", r["reason"])
        self.assertEqual(self.live(), (b"old cert\n", b"old key\n"))
        r = self.i.rolled(self.name, [self.crt, self.key], ["web.service"], "after the reload, 127.0.0.1:443 serves nothing")
        self.assertEqual((r["status"], r["reason"]),
                         ("rolled back", "after the reload, 127.0.0.1:443 serves nothing - the old files are back, reloaded again"))
        r = self.i.rolled(self.name, [self.crt, self.key], [], "writing %s failed (No space left on device)" % self.key)
        self.assertEqual(r["status"], "rolled back")
        self.assertTrue(r["reason"].endswith("the old files are back, nothing was reloaded"))

    def test_rollback_with_a_damaged_backup_is_failed(self):
        os.remove(os.path.join(self.copies, "0-" + os.path.basename(self.crt)))
        r = self.i.rolled(self.name, [self.crt, self.key], ["web.service"], "systemctl reload web.service failed: x")
        self.assertEqual(r["status"], "failed")
        self.assertIn("could NOT all be put back", r["reason"])
        self.assertIn("Check the files by hand", r["reason"])


class Folders(unittest.TestCase):
    def test_names_sort_in_the_order_made(self):
        d = os.path.join(W, "dirs")
        made = [os.path.basename(m.new_dir(d, "x")) for _ in range(5)]
        self.assertEqual(made, sorted(made))
        self.assertEqual(len(set(made)), 5)
        self.assertEqual(oct(os.stat(os.path.join(d, made[0])).st_mode & 0o777), "0o700")


if __name__ == "__main__":
    unittest.main(verbosity=1)
