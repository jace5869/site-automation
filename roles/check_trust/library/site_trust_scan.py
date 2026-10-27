#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Every trust store on a Linux host, read for a list of known certificates (a CA whose key was compromised, a
root the browsers dropped): the system bundles, p11-kit's live view and its blocklist, Java cacerts, Python's
certifi bundles, Node's built-in roots, the bundles inside running podman containers, any certificate file
under the folders named - and the software that carries its own trust logic (Go programs, Node, NSS), with
their versions against the ones that fixed it. Read-only. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_trust_scan
short_description: Which trust stores on this host still hold a known (compromised or distrusted) CA certificate
description:
  - Reads every certificate it can find on the host and matches each one, by its SHA-256 and by the SHA-256 of
    its public key, against C(known). Changes nothing; a dry run reads too.
  - The system bundles (C(bundles)); p11-kit's live anchors and blocklist (C(trust extract)); the blocklist
    folders; every Java cacerts under C(java_dirs); every certifi bundle under C(python_dirs); Node's built-in
    roots (C(node) binaries found); the bundles inside running podman containers (root's and each user's);
    certificate files under C(find_dirs).
  - Go programs (running, and under C(go_dirs)) with the Go version they were built with; Node and NSS versions.
options:
  known: {description: "The certificates to look for: [{name, kind, sha256, spki_sha256, severity, why}].", type: list, elements: dict, default: []}
  bundles: {description: The system CA bundles (the ones that exist are read)., type: list, elements: str, default: []}
  blocklist_dirs: {description: p11-kit blocklist folders (a certificate there is distrusted)., type: list, elements: str, default: []}
  p11kit: {description: Read p11-kit's live view with trust extract (anchors and blocklist)., type: bool, default: true}
  java: {description: Read Java cacerts., type: bool, default: true}
  java_dirs: {description: Folders searched for cacerts / jssecacerts., type: list, elements: str, default: []}
  python: {description: Read Python certifi bundles., type: bool, default: true}
  python_dirs: {description: Folders searched for certifi/cacert.pem., type: list, elements: str, default: []}
  node: {description: Read Node's built-in roots and version., type: bool, default: true}
  node_paths: {description: Where node binaries are (globs); running ones are found too., type: list, elements: str, default: []}
  node_fixed: {description: "Node's fixed version per major: {'22': '22.23.4'}.", type: dict, default: {}}
  go: {description: Find Go programs and the Go version they were built with., type: bool, default: true}
  go_dirs: {description: Folders searched for Go programs (running ones are found too)., type: list, elements: str, default: []}
  go_fixed: {description: "Go's fixed version per line: {'1.27': '1.27.3'}.", type: dict, default: {}}
  go_max: {description: At most this many programs looked at., type: int, default: 200}
  nss_fixed: {description: The NSS version that fixed it ('' = not compared)., type: str, default: ''}
  ca_certificates_fixed: {description: The ca-certificates package version that fixed it ('' = not compared)., type: str, default: ''}
  containers: {description: Read the bundles inside running podman containers., type: bool, default: false}
  containers_max: {description: At most this many containers., type: int, default: 30}
  nss_dirs: {description: "NSS databases (certutil -L): the system's, root's, each user's.", type: list, elements: str, default: []}
  find_dirs: {description: Folders searched for certificate files (bundles an application carries)., type: list, elements: str, default: []}
  find_max: {description: At most this many files read from find_dirs., type: int, default: 2000}
  find_exclude: {description: Regular expression on the path; matching files are skipped., type: str, default: ''}
  timeout: {description: Seconds for each command run (keytool, node, podman) and each search., type: int, default: 60}
author: site automation
'''

RETURN = r'''
stores:
  description: One record per trust store read (the ones with hits first).
  type: list
  returned: success
  sample: [{kind: system, where: /etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem, version: "", count: 146,
            hits: [{name: "emSign ECC Root CA - G3", kind: root, severity: warning, how: certificate}], blocked: [], note: ""}]
software:
  description: Programs with their own trust logic and their versions.
  type: list
  returned: success
  sample: [{kind: go, where: /usr/local/bin/prog, program: "github.com/x/prog", version: "1.22.5", fixed: "", status: unsupported}]
not_read:
  description: What could not be read, with why.
  type: list
  returned: success
counts:
  description: How much was looked at.
  type: dict
  returned: success
'''

import glob
import mmap
import os
import pwd
import re
import stat
import subprocess
import tempfile

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_trust import Known, der_certs, fixed_for, below, pem_certs

GO_MAGIC = b"\xff Go buildinf:"
ELF = b"\x7fELF"
CERT_NAME = re.compile(r"(\.pem|\.crt|\.cer|\.der|\.ca-bundle|cacert[^/]*|ca-bundle[^/]*|ca-certificates[^/]*|cert\.pem|certs\.pem|bundle\.pem|cacerts)$", re.I)
MAX_FILE = 16 * 1024 * 1024


def run(args, timeout, stdin=None, env=None):
    """(rc, stdout, stderr) of a command; rc 124 on timeout, 127 when it is not there."""
    try:
        p = subprocess.Popen(args, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    except OSError as e:
        return 127, b"", str(e).encode()
    try:
        out, err = p.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        return 124, b"", b"timed out after %d seconds" % timeout
    return p.returncode, out, err


def read_file(path, limit=MAX_FILE):
    with open(path, "rb") as f:
        return f.read(limit)


def realpath(p):
    try:
        return os.path.realpath(p)
    except OSError:
        return p


class Scan(object):
    def __init__(self, module):
        self.m = module
        self.p = module.params
        self.known = Known(self.p["known"])
        self.timeout = int(self.p["timeout"])
        self.stores, self.software, self.skipped = [], [], []
        self.counts = {"files": 0, "certificates": 0}
        self.read_paths = set()

    # ---- helpers ------------------------------------------------------------------------------------
    def store(self, kind, where, certs, version="", note="", owner=""):
        r = self.known.scan(certs)
        self.counts["certificates"] += r["count"]
        rec = {"kind": kind, "where": where, "version": version, "count": r["count"], "hits": r["hits"],
               "blocked": r["blocked"], "note": note}
        if owner:
            rec["owner"] = owner
        self.stores.append(rec)
        return rec

    def skip(self, where, reason, kind=""):
        self.skipped.append({"kind": kind, "where": where, "reason": " ".join(str(reason).split())[:300]})

    def file_store(self, kind, path, version="", note=""):
        rp = realpath(path)
        if rp in self.read_paths:
            return None
        self.read_paths.add(rp)
        try:
            st = os.stat(path)
            if not stat.S_ISREG(st.st_mode):
                return None
            if st.st_size > MAX_FILE:
                self.skip(path, "bigger than 16 MB: not read", kind)
                return None
            blob = read_file(path)
        except (OSError, IOError) as e:
            self.skip(path, "not readable: %s" % e, kind)
            return None
        self.counts["files"] += 1
        certs = der_certs(blob)
        if not certs:
            return None
        return self.store(kind, path, certs, version, note)

    def find(self, dirs, expr, max_depth=None, extra=()):
        """find(1) under dirs (the ones that exist), on the same filesystem, within the timeout; [] when it fails."""
        dirs = [d for d in dirs if os.path.isdir(d)]
        if not dirs:
            return []
        args = ["find"] + list(extra) + dirs + ["-xdev"] + (["-maxdepth", str(max_depth)] if max_depth else []) + expr
        rc, out, err = run(["timeout", str(self.timeout)] + args, self.timeout + 5)
        if rc == 124:
            self.skip(", ".join(dirs), "the search took more than %d seconds (check_trust_timeout): part of it was read" % self.timeout)
        return [x for x in out.decode("utf-8", "replace").split("\n") if x]

    def running_exes(self):
        """The executables of the running processes, each once."""
        out = set()
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                exe = os.readlink("/proc/%s/exe" % pid)
            except OSError:
                continue
            exe = exe.split(" (deleted)")[0]
            if exe.startswith("/") and os.path.isfile(exe):
                out.add(exe)
        return sorted(out)

    def pkg_version(self, *names):
        """The installed version of the first of names found (rpm, then dpkg); '' when none."""
        for n in names:
            rc, out, _ = run(["rpm", "-q", "--qf", "%{VERSION}-%{RELEASE}", n], 20)
            if rc == 0 and out.strip() and b"not installed" not in out:
                return out.decode("utf-8", "replace").strip()
        for n in names:
            rc, out, _ = run(["dpkg-query", "-W", "-f", "${Version}", n], 20)
            if rc == 0 and out.strip():
                return out.decode("utf-8", "replace").strip()
        return ""

    # ---- the system ---------------------------------------------------------------------------------
    def system(self):
        for b in self.p["bundles"]:
            if os.path.exists(b):
                self.file_store("system", b)
        ver = self.pkg_version("ca-certificates")
        if ver:
            fixed = self.p["ca_certificates_fixed"] or ""
            self.software.append({"kind": "ca-certificates", "where": "package ca-certificates", "program": "ca-certificates",
                                  "version": ver, "fixed": fixed,
                                  "status": ("below" if below(ver, fixed) else "ok") if fixed else "unknown"})
        if self.p["p11kit"] and self.m.get_bin_path("trust"):
            tmp = tempfile.mkdtemp(prefix="trust-scan-")
            try:
                for flt, kind, what in (("ca-anchors", "p11-kit", "p11-kit trust (the live anchors, what NSS / GnuTLS clients and update-ca-trust use)"),
                                        ("blocklist", "p11-kit-blocklist", "p11-kit trust (the blocklist)")):
                    out = os.path.join(tmp, flt + ".pem")
                    rc, _, err = run(["trust", "extract", "--filter=" + flt, "--format=pem-bundle", "--overwrite", out], self.timeout)
                    if rc != 0:
                        self.skip(what, "trust extract failed: %s" % err.decode("utf-8", "replace").strip()[:200], kind)
                        continue
                    try:
                        certs = pem_certs(read_file(out))
                    except (OSError, IOError):
                        certs = []
                    if flt == "blocklist":
                        rec = self.store(kind, what, [(d, True) for d, _ in certs])
                        rec["blocked"] = [h["name"] for h in self.known.scan(certs)["hits"]]
                    else:
                        self.store(kind, what, certs)
            finally:
                for f in glob.glob(os.path.join(tmp, "*")):
                    os.unlink(f)
                os.rmdir(tmp)
        for d in self.p["blocklist_dirs"]:
            if not os.path.isdir(d):
                continue
            for f in sorted(os.listdir(d)):
                path = os.path.join(d, f)
                try:
                    certs = der_certs(read_file(path))
                except (OSError, IOError):
                    continue
                if certs:
                    rec = self.store("blocklist", path, [(c, True) for c, _ in certs])
                    rec["blocked"] = [h["name"] for h in self.known.scan(certs)["hits"]]
        ver = self.pkg_version("nss", "libnss3")
        if ver:
            fixed = self.p["nss_fixed"] or ""
            self.software.append({"kind": "nss", "where": "package nss", "program": "NSS", "version": ver, "fixed": fixed,
                                  "status": ("below" if below(ver, fixed) else "ok") if fixed else "unknown"})

    # ---- Java ---------------------------------------------------------------------------------------
    def java(self):
        files = self.find(self.p["java_dirs"], ["-type", "f", "(", "-name", "cacerts", "-o", "-name", "jssecacerts", ")"], 7, ["-L"])
        for exe in self.running_exes():
            if os.path.basename(exe) == "java":
                for rel in ("../lib/security/cacerts", "../jre/lib/security/cacerts"):
                    f = os.path.normpath(os.path.join(os.path.dirname(exe), rel))
                    if os.path.isfile(f):
                        files.append(f)
        seen = set()
        for f in files:
            rp = realpath(f)
            if rp in seen:
                continue
            seen.add(rp)
            self.read_paths.add(rp)
            version = java_version(f)
            try:
                blob = read_file(f)
            except (OSError, IOError) as e:
                self.skip(f, "not readable: %s" % e, "java")
                continue
            certs = jks_certs(blob)
            if certs is None:
                self.keytool_store(f, version)
            else:
                self.store("java", f, [(c, False) for c in certs], version)

    def keytool_store(self, f, version):
        """A keystore that is not JKS (a PKCS12 cacerts): listed with keytool, matched by the fingerprints it prints."""
        kt = self.m.get_bin_path("keytool")
        if not kt:
            self.skip(f, "not a JKS keystore, and no keytool on this host to read it", "java")
            return
        rc, out, err = run([kt, "-list", "-keystore", f, "-storepass", "changeit"], self.timeout)
        if rc != 0:
            self.skip(f, "keytool could not list it (%s)" % err.decode("utf-8", "replace").strip()[:150], "java")
            return
        fps = [x.replace(":", "").upper() for x in re.findall(r"\(SHA-?256\):\s*([0-9A-Fa-f:]{95})", out.decode("utf-8", "replace"))]
        hits, seen = [], set()
        for fp in fps:
            e = self.known.by_sha.get(fp)
            if e is not None and e["name"] not in seen:
                seen.add(e["name"])
                hits.append(dict(e, how="certificate", sha256=fp))
        self.stores.append({"kind": "java", "where": f, "version": version, "count": len(fps), "hits": hits, "blocked": [],
                            "note": "read with keytool (by fingerprint: a certificate with the same key under another name is not seen)"})
        self.counts["certificates"] += len(fps)

    # ---- Python -------------------------------------------------------------------------------------
    def python(self):
        files = self.find(self.p["python_dirs"], ["-type", "f", "-path", "*/certifi/cacert.pem"], 12)
        for f in files:
            version = ""
            init = os.path.join(os.path.dirname(f), "__init__.py")
            try:
                m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)", read_file(init, 65536).decode("utf-8", "replace"))
                version = m.group(1) if m else ""
            except (OSError, IOError):
                pass
            self.file_store("python", f, version)

    # ---- Node ---------------------------------------------------------------------------------------
    def node(self):
        cands = []
        for g in self.p["node_paths"]:
            cands += glob.glob(g)
        b = self.m.get_bin_path("node")
        if b:
            cands.append(b)
        cands += [e for e in self.running_exes() if os.path.basename(e) in ("node", "nodejs")]
        seen = set()
        for c in cands:
            rp = realpath(c)
            if rp in seen or not os.access(c, os.X_OK):
                continue
            seen.add(rp)
            if len(seen) > 10:
                self.skip(c, "more than 10 node binaries: the rest not read", "node")
                break
            rc, out, err = run([c, "--version"], self.timeout)
            if rc != 0:
                self.skip(c, "node --version failed: %s" % err.decode("utf-8", "replace").strip()[:150], "node")
                continue
            version = out.decode("utf-8", "replace").strip().lstrip("v")
            fixed, status = fixed_for(version, self.p["node_fixed"])
            self.software.append({"kind": "node", "where": c, "program": "Node.js", "version": version, "fixed": fixed, "status": status})
            rc, out, err = run([c, "-e", "for (const c of require('tls').rootCertificates) console.log(c)"], self.timeout)
            if rc != 0:
                self.skip(c, "node could not list its built-in roots: %s" % err.decode("utf-8", "replace").strip()[:150], "node")
                continue
            self.store("node", "%s (built-in roots)" % c, pem_certs(out), version)

    # ---- Go -----------------------------------------------------------------------------------------
    def go(self):
        cands = self.running_exes()
        cands += self.find(self.p["go_dirs"], ["-type", "f", "-perm", "/111", "-size", "+1M"], 4)
        seen, n = set(), 0
        for c in cands:
            rp = realpath(c)
            if rp in seen:
                continue
            seen.add(rp)
            n += 1
            if n > int(self.p["go_max"]):
                self.skip(c, "more than %d programs: the rest not looked at (check_trust_go_max)" % int(self.p["go_max"]), "go")
                break
            info = go_buildinfo(c)
            if not info:
                continue
            version, path = info
            fixed, status = fixed_for(version.lstrip("go"), self.p["go_fixed"])
            self.software.append({"kind": "go", "where": c, "program": path or os.path.basename(c), "version": version.lstrip("go"),
                                  "fixed": fixed, "status": status})

    # ---- containers ---------------------------------------------------------------------------------
    def containers(self):
        podman = self.m.get_bin_path("podman")
        if not podman:
            return
        owners = [("root", [podman])]
        for d in sorted(glob.glob("/run/user/*")):
            uid = os.path.basename(d)
            if not uid.isdigit() or int(uid) < 1000:
                continue
            try:
                name = pwd.getpwuid(int(uid)).pw_name
            except KeyError:
                continue
            owners.append((name, ["runuser", "-u", name, "--", "env", "XDG_RUNTIME_DIR=" + d, podman]))
        n = 0
        for owner, pm in owners:
            rc, out, err = run(pm + ["ps", "--format", "{{.ID}}|{{.Names}}|{{.Image}}"], self.timeout)
            if rc != 0:
                self.skip("podman containers of %s" % owner, "podman ps failed: %s" % err.decode("utf-8", "replace").strip()[:150], "container")
                continue
            for line in out.decode("utf-8", "replace").splitlines():
                parts = line.split("|")
                if len(parts) < 2:
                    continue
                cid, cname = parts[0], parts[1]
                image = parts[2] if len(parts) > 2 else ""
                n += 1
                if n > int(self.p["containers_max"]):
                    self.skip("container %s (%s)" % (cname, owner), "more than %d containers: the rest not read (check_trust_containers_max)" % int(self.p["containers_max"]), "container")
                    return
                rc, cout, cerr = run(pm + ["exec", cid, "sh", "-c",
                                           "cat /etc/pki/tls/certs/ca-bundle.crt /etc/ssl/certs/ca-certificates.crt /etc/ssl/cert.pem "
                                           "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem 2>/dev/null; "
                                           "find / -xdev -path '*/certifi/cacert.pem' -exec cat {} + 2>/dev/null"], self.timeout)
                certs = pem_certs(cout)
                if not certs:
                    self.skip("container %s (%s)" % (cname, owner),
                              "no CA bundle read inside it (no shell, or none of the usual files): %s" % (cerr.decode("utf-8", "replace").strip()[:120] or "nothing printed"), "container")
                    continue
                self.store("container", "container %s (%s)" % (cname, owner), certs, image, owner=owner)

    # ---- files --------------------------------------------------------------------------------------
    def files(self):
        exclude = re.compile(self.p["find_exclude"]) if self.p["find_exclude"] else None
        found = self.find(self.p["find_dirs"], ["-type", "f", "-size", "-16M", "(", "-iname", "*.pem", "-o", "-iname", "*.crt", "-o", "-iname", "*.cer",
                                                "-o", "-iname", "*.der", "-o", "-iname", "cacert*", "-o", "-iname", "*ca-bundle*",
                                                "-o", "-iname", "ca-certificates*", "-o", "-iname", "cacerts", "-o", "-iname", "*.p7b",
                                                "-o", "-iname", "*.jks", "-o", "-iname", "*.keystore", "-o", "-iname", "keystore*",
                                                "-o", "-iname", "*truststore*", "-o", "-iname", "*.p12", "-o", "-iname", "*.pfx", ")"])
        n = 0
        for f in sorted(found):
            if exclude and exclude.search(f):
                continue
            if realpath(f) in self.read_paths:
                continue
            n += 1
            if n > int(self.p["find_max"]):
                self.skip(f, "more than %d certificate files: the rest not read (check_trust_find_max)" % int(self.p["find_max"]), "file")
                break
            base = os.path.basename(f).lower()
            if base == "cacerts" or base.endswith((".jks", ".keystore", ".p12", ".pfx")) or base.startswith("keystore") or "truststore" in base:
                # a Java keystore: JKS natively (the trusted entries need no password); PKCS12 with keytool
                try:
                    certs = jks_certs(read_file(f))
                except (OSError, IOError):
                    certs = None
                self.read_paths.add(realpath(f))
                if certs:
                    self.store("java", f, [(c, False) for c in certs], java_version(f))
                elif certs is None and base != "cacerts":
                    self.keytool_store(f, java_version(f))
                    if self.stores and self.stores[-1]["where"] == f and not self.stores[-1]["hits"]:
                        self.stores.pop()           # a keystore without a hit is counted, not listed
                continue
            rec = self.file_store("file", f)
            if rec is not None and not rec["hits"] and not rec["blocked"]:
                self.stores.pop()               # a file without a hit is counted, not listed

    # ---- NSS databases (certutil) -------------------------------------------------------------------
    def nss(self):
        """The NSS databases (the system's /etc/pki/nssdb: curl and other NSS clients on older RHEL; root's and
        each user's ~/.pki/nssdb: Chrome, Evolution), read with certutil - each certificate by its nickname, so the
        removal can delete it by that name."""
        dirs = []
        for g in self.p["nss_dirs"]:
            dirs += glob.glob(g)
        certutil = self.m.get_bin_path("certutil")
        for d in sorted(set(dirs)):
            if not os.path.isdir(d) or not any(os.path.exists(os.path.join(d, x)) for x in ("cert9.db", "cert8.db")):
                continue
            if not certutil:
                self.skip(d, "no certutil on this host to read the NSS database (nss-tools)", "nssdb")
                continue
            rc, out, err = run([certutil, "-L", "-d", "sql:" + d], self.timeout)
            if rc != 0:
                self.skip(d, "certutil could not list it: %s" % err.decode("utf-8", "replace").strip()[:150], "nssdb")
                continue
            certs, nicknames = [], {}
            for line in out.decode("utf-8", "replace").splitlines():
                m = re.match(r"^(.+?)\s{2,}([CTcPpuw,]*)\s*$", line.rstrip())
                if not m or m.group(1).startswith("Certificate Nickname") or m.group(1).startswith("SSL,"):
                    continue
                nick, flags = m.group(1).strip(), m.group(2)
                rc, pem, _ = run([certutil, "-L", "-d", "sql:" + d, "-n", nick, "-a"], self.timeout)
                if rc != 0:
                    continue
                for der, _ in pem_certs(pem):
                    rejected = "p" in flags.split(",")[0]       # p = prohibited (distrusted) for SSL
                    certs.append((der, rejected))
                    e = self.known.match(der)
                    if e is not None:
                        nicknames.setdefault(e["name"], nick)
            rec = self.store("nssdb", d, certs)
            rec["nicknames"] = nicknames

    def all(self):
        self.system()
        if self.p["nss_dirs"]:
            self.nss()
        if self.p["java"]:
            self.java()
        if self.p["python"]:
            self.python()
        if self.p["node"]:
            self.node()
        if self.p["go"]:
            self.go()
        if self.p["containers"]:
            self.containers()
        if self.p["find_dirs"]:
            self.files()
        order = {"critical": 0, "warning": 1}
        self.stores.sort(key=lambda s: (0 if s["hits"] else 1, min([order.get(h.get("severity"), 2) for h in s["hits"]] or [3]), s["where"]))
        return {"stores": self.stores, "software": self.software, "not_read": self.skipped, "counts": self.counts}


def java_version(cacerts):
    """The JDK's version from its release file (next to lib/), looking up to four folders up; '' when none."""
    d = os.path.dirname(realpath(cacerts))
    for _ in range(5):
        rel = os.path.join(d, "release")
        if os.path.isfile(rel):
            try:
                m = re.search(r'JAVA_VERSION="([^"]+)"', read_file(rel, 65536).decode("utf-8", "replace"))
                if m:
                    return m.group(1)
            except (OSError, IOError):
                return ""
        d = os.path.dirname(d)
    return ""


def jks_certs(blob):
    """The certificates (DER) in a JKS / JCEKS keystore; None when the bytes are not one (PKCS12: keytool)."""
    if len(blob) < 12 or blob[:4] not in (b"\xfe\xed\xfe\xed", b"\xce\xce\xce\xce"):
        return None
    nonlocal_pos = [4]

    def u4():
        nonlocal_pos[0] += 4
        return int.from_bytes(blob[nonlocal_pos[0] - 4:nonlocal_pos[0]], "big")

    def utf():
        n = int.from_bytes(blob[nonlocal_pos[0]:nonlocal_pos[0] + 2], "big")
        nonlocal_pos[0] += 2 + n
        return blob[nonlocal_pos[0] - n:nonlocal_pos[0]]

    out = []
    try:
        version = u4()
        count = u4()
        for _ in range(count):
            tag = u4()
            utf()                               # alias
            nonlocal_pos[0] += 8                # timestamp
            if tag == 1:                        # a private key with its chain
                n = u4()
                nonlocal_pos[0] += n
                chain = u4()
                for _ in range(chain):
                    if version == 2:
                        utf()                   # X.509
                    n = u4()
                    out.append(blob[nonlocal_pos[0]:nonlocal_pos[0] + n])
                    nonlocal_pos[0] += n
            elif tag == 2:                      # a trusted certificate
                if version == 2:
                    utf()
                n = u4()
                out.append(blob[nonlocal_pos[0]:nonlocal_pos[0] + n])
                nonlocal_pos[0] += n
            else:
                return out
    except (IndexError, ValueError):
        pass
    return out


def go_buildinfo(path):
    """(version, module path) of a Go program (its build information); None when it is not one."""
    try:
        size = os.path.getsize(path)
        if size < 64 or size > 1024 * 1024 * 1024:
            return None
        with open(path, "rb") as f:
            if f.read(4) != ELF:
                return None
            f.seek(0)
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            try:
                i = mm.find(GO_MAGIC)
                if i < 0:
                    return None
                flags = mm[i + 15]
                if flags & 2:                   # Go 1.18 and later: the strings follow, length-prefixed
                    pos = i + 32
                    ver, pos = _varint_str(mm, pos)
                    mod, _ = _varint_str(mm, pos)
                else:                           # before Go 1.18: pointers into the binary's own memory - the
                    hit = re.search(rb"go1\.[0-9]+(\.[0-9]+)?(rc[0-9]+|beta[0-9]+)?", mm)   # version by its pattern
                    ver, mod = (hit.group(0).decode() if hit else ""), ""
            finally:
                mm.close()
    except (OSError, IOError, ValueError, IndexError):
        return None
    if not ver.startswith("go1"):
        return None
    m = re.search(r"^path\t(\S+)", mod, re.M)
    return ver, (m.group(1) if m else "")


def _varint_str(mm, pos):
    n, shift = 0, 0
    while True:
        b = mm[pos]
        pos += 1
        n |= (b & 0x7F) << shift
        if b < 0x80:
            break
        shift += 7
        if shift > 35:
            raise ValueError("varint")
    if n > 1024 * 1024:
        raise ValueError("long")
    return mm[pos:pos + n].decode("utf-8", "replace"), pos + n


def main():
    module = AnsibleModule(argument_spec=dict(
        known=dict(type="list", elements="dict", default=[]),
        bundles=dict(type="list", elements="str", default=[]),
        blocklist_dirs=dict(type="list", elements="str", default=[]),
        p11kit=dict(type="bool", default=True),
        java=dict(type="bool", default=True),
        java_dirs=dict(type="list", elements="str", default=[]),
        python=dict(type="bool", default=True),
        python_dirs=dict(type="list", elements="str", default=[]),
        node=dict(type="bool", default=True),
        node_paths=dict(type="list", elements="str", default=[]),
        node_fixed=dict(type="dict", default={}),
        go=dict(type="bool", default=True),
        go_dirs=dict(type="list", elements="str", default=[]),
        go_fixed=dict(type="dict", default={}),
        go_max=dict(type="int", default=200),
        nss_fixed=dict(type="str", default=""),
        ca_certificates_fixed=dict(type="str", default=""),
        nss_dirs=dict(type="list", elements="str", default=[]),
        containers=dict(type="bool", default=False),
        containers_max=dict(type="int", default=30),
        find_dirs=dict(type="list", elements="str", default=[]),
        find_max=dict(type="int", default=2000),
        find_exclude=dict(type="str", default=""),
        timeout=dict(type="int", default=60),
    ), supports_check_mode=True)
    result = Scan(module).all()
    module.exit_json(changed=False, **result)


if __name__ == "__main__":
    main()
