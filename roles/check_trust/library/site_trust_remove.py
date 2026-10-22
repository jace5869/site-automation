#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Take the listed certificates out of the trust stores the scan found them in (roles/check_trust). The system
trust is blocklisted (never edited: the package would put the certificate back), a Java keystore loses the entry,
a PEM bundle loses the block, a file that is the certificate itself is moved away. Everything changed is backed
up first. Standard library only; check mode shows the plan and changes nothing."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_trust_remove
short_description: Remove the certificates on the list from the trust stores the scan found them in
description:
  - Input is the scan's stores (site_trust_scan). For each store with a hit at one of the severities asked for,
    the certificate is taken out - system bundles by blocklisting it (RHEL and SUSE: a PEM in the blocklist folder,
    then update-ca-trust / update-ca-certificates; Debian and Ubuntu: the certificate's line in
    /etc/ca-certificates.conf is switched off, then update-ca-certificates), a Java keystore by deleting the entry
    (JKS natively; PKCS12 with keytool), a certifi or application bundle by cutting the PEM block out, a file that
    is the certificate itself by moving it to the backup folder. Every file changed is copied to the backup folder
    first. A file a package owns is left alone (the package update is the fix). Node, Go programs, containers and
    p11-kit's own view are reported, not changed.
options:
  known: {description: The list (the scan's known)., type: list, elements: dict, default: []}
  stores: {description: The scan's stores (kind, where, hits)., type: list, elements: dict, default: []}
  kinds: {description: Which kinds of store to change., type: list, elements: str, default: [system, java, python, file, nssdb]}
  severities: {description: Only certificates at these severities are removed., type: list, elements: str, default: [critical, warning]}
  backup_dir: {description: Where the copies of the changed files go (a folder per run)., type: str, default: /var/backups/site-automation/trust}
  blocklist_dir: {description: The system blocklist folder; empty = the distribution's., type: str, default: ''}
  update_cmd: {description: The command that rebuilds the system trust after blocklisting; empty = the distribution's., type: str, default: ''}
  keystore_password: {description: The password of the Java keystores., type: str, default: changeit, no_log: true}
  timeout: {description: Seconds for each command., type: int, default: 120}
author: site automation
'''

RETURN = r'''
actions:
  description: One per store changed (or that would be, in check mode) - kind, where, names, action, done, backup, error.
  type: list
  returned: success
not_removed:
  description: The stores with a hit that were left alone, with why.
  type: list
  returned: success
backup:
  description: The folder with the copies of the changed files ('' when nothing was changed).
  type: str
  returned: success
'''

import base64
import hashlib
import os
import re
import shutil
import struct
import subprocess
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_trust import PEM, Known, der_certs, pem_certs

NOT_REMOVABLE = {
    "container": "a container's bundle comes from its image: pull or rebuild the image and restart the container",
    "p11-kit": "p11-kit's view follows the system trust: it clears when the system bundle is blocklisted",
    "p11-kit-blocklist": "already the blocklist",
    "blocklist": "already the blocklist",
    "node": "Node's roots are built into the binary: update Node, or run it with --use-openssl-ca",
    "go": "a Go program carries its verification: rebuild it with a fixed Go",
}
SYSTEM_JAVA = ("/etc/pki/ca-trust/", "/etc/pki/java/", "/etc/ssl/certs/java/", "/var/lib/ca-certificates/")


def run(args, timeout, cwd=None):
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
    except OSError as e:
        return 127, b"", str(e).encode("utf-8")
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        return 124, b"", b"timed out after %d seconds" % timeout
    return p.returncode, out, err


def slug(name):
    return re.sub(r"[^A-Za-z0-9.]+", "-", name).strip("-")[:80] or "certificate"


def to_pem(der):
    b64 = base64.b64encode(der).decode("ascii")
    return "-----BEGIN CERTIFICATE-----\n" + "\n".join(b64[i:i + 64] for i in range(0, len(b64), 64)) + "\n-----END CERTIFICATE-----\n"


def jks_entries(blob):
    """A JKS keystore -> (version, [(alias, tag, entry bytes, [der])]) with the byte range of every entry, or None."""
    if len(blob) < 12 or blob[:4] != b"\xfe\xed\xfe\xed":
        return None
    pos = [4]

    def u4():
        pos[0] += 4
        return struct.unpack(">I", blob[pos[0] - 4:pos[0]])[0]

    def utf():
        n = struct.unpack(">H", blob[pos[0]:pos[0] + 2])[0]
        pos[0] += 2 + n
        return blob[pos[0] - n:pos[0]]

    version = u4()
    count = u4()
    entries = []
    for _ in range(count):
        start = pos[0]
        tag = u4()
        alias = utf().decode("utf-8", "replace")
        pos[0] += 8
        certs = []
        if tag == 1:
            pos[0] += u4()
            for _ in range(u4()):
                if version == 2:
                    utf()
                n = u4()
                certs.append(blob[pos[0]:pos[0] + n])
                pos[0] += n
        elif tag == 2:
            if version == 2:
                utf()
            n = u4()
            certs.append(blob[pos[0]:pos[0] + n])
            pos[0] += n
        else:
            return None
        entries.append((alias, tag, blob[start:pos[0]], certs))
    return version, entries


def jks_write(version, entries, password):
    """The keystore bytes for these entries, with the integrity digest keytool checks (SHA-1 over the password
    as UTF-16BE, 'Mighty Aphrodite', the data)."""
    body = b"\xfe\xed\xfe\xed" + struct.pack(">II", version, len(entries)) + b"".join(e[2] for e in entries)
    return body + hashlib.sha1(password.encode("utf-16-be") + b"Mighty Aphrodite" + body).digest()


class Remover(object):
    def __init__(self, module):
        self.m = module
        self.p = module.params
        self.check = module.check_mode
        self.known = Known(self.p["known"])
        self.sev = set(self.p["severities"] or [])
        self.kinds = set(self.p["kinds"] or [])
        self.timeout = int(self.p["timeout"])
        self.actions, self.not_removed = [], []
        self.backup = ""
        self.changed = False
        self.system_hits = {}                   # sha256 -> (name, der): what to blocklist, once per host

    # ---- helpers ------------------------------------------------------------------------------------
    def wanted(self, der):
        """The list entry when this certificate is to be removed (on the list, at a wanted severity)."""
        m = self.known.match(der)
        if m is None or str(m.get("severity", "warning")) not in self.sev:
            return None
        return m

    def names(self, store):
        return sorted(set(h["name"] for h in store.get("hits") or [] if str(h.get("severity", "warning")) in self.sev))

    def leave(self, store, why):
        self.not_removed.append({"kind": store.get("kind", ""), "where": store.get("where", ""), "names": self.names(store), "why": why})

    def act(self, store, action, names, done=True, backup="", error="", **extra):
        rec = {"kind": store.get("kind", ""), "where": store.get("where", ""), "names": names, "action": action,
               "done": done and not self.check, "planned": self.check, "backup": backup, "error": error}
        rec.update(extra)
        self.actions.append(rec)
        if done and not self.check:
            self.changed = True
        return rec

    def backup_file(self, path):
        if self.check:
            return ""
        if not self.backup:
            self.backup = os.path.join(self.p["backup_dir"], time.strftime("%Y-%m-%d-%H%M%S"))
            os.makedirs(self.backup, 0o700)
        dest = os.path.join(self.backup, path.lstrip("/").replace("/", "__"))
        shutil.copy2(path, dest)
        return dest

    def owned_by_package(self, path):
        for args in (["rpm", "-qf", "--qf", "%{NAME}", path], ["dpkg", "-S", path]):
            if not self.m.get_bin_path(args[0]):
                continue
            rc, out, _ = run(args, self.timeout)
            if rc == 0 and out.strip():
                return out.decode("utf-8", "replace").strip().split(":")[0].split("\n")[0][:80]
            return ""
        return ""

    def write_back(self, path, data):
        st = os.stat(path)
        tmp = path + ".site-trust-new"
        with open(tmp, "wb") as f:
            f.write(data)
        os.chmod(tmp, st.st_mode & 0o7777)
        try:
            os.chown(tmp, st.st_uid, st.st_gid)
        except OSError:
            pass
        os.rename(tmp, path)

    # ---- the stores ---------------------------------------------------------------------------------
    def system(self, store):
        """Collect what to blocklist; the blocklist is written once, after every store (self.finish_system)."""
        path = store["where"]
        try:
            with open(path, "rb") as f:
                certs = der_certs(f.read())
        except (OSError, IOError) as e:
            return self.leave(store, "not readable: %s" % e)
        for der, rejected in certs:
            m = self.wanted(der)
            if m is not None and not rejected:
                self.system_hits.setdefault(m["sha256"], (m["name"], der))
        self.act(store, "blocklisted (the certificate stays in the package's bundle, distrusted by the system trust)",
                 self.names(store), done=False)

    def finish_system(self):
        if not self.system_hits:
            return
        recs = [a for a in self.actions if a["kind"] == "system"]
        bl, cmd, conf = self.distribution()
        if not bl and not conf:
            for a in recs:
                a["error"] = "no blocklist folder on this host (check_trust_remove_blocklist_dir)"
            return
        try:
            if conf:                            # Debian, Ubuntu: switch the certificate's line off
                self.debian_conf(conf)
            else:
                if not os.path.isdir(bl) and not self.check:
                    os.makedirs(bl, 0o755)
                for sha, (name, der) in sorted(self.system_hits.items()):
                    dest = os.path.join(bl, slug(name) + ".pem")
                    pem = to_pem(der)
                    if os.path.exists(dest):
                        with open(dest, "rb") as f:
                            if f.read().decode("utf-8", "replace") == pem:
                                continue
                    if not self.check:
                        with open(dest, "w") as f:
                            f.write(pem)
                    for a in recs:
                        a.setdefault("written", []).append(dest)
            if cmd and not self.check:
                rc, _, err = run(cmd.split(), self.timeout)
                if rc != 0:
                    raise RuntimeError("%s failed: %s" % (cmd, err.decode("utf-8", "replace").strip()[:200]))
            for a in recs:
                a["done"] = not self.check
                a["update"] = cmd
            if not self.check:
                self.changed = True
        except (OSError, IOError, RuntimeError) as e:
            for a in recs:
                a["error"] = str(e)

    def distribution(self):
        """(blocklist folder, rebuild command, Debian's conf file or '') for this host."""
        bl, cmd = self.p["blocklist_dir"], self.p["update_cmd"]
        if bl or cmd:
            return bl, cmd, ""
        if os.path.isdir("/etc/pki/ca-trust/source"):
            return "/etc/pki/ca-trust/source/blocklist", "update-ca-trust", ""
        if os.path.isdir("/etc/pki/trust"):
            return "/etc/pki/trust/blacklist", "update-ca-certificates", ""
        if os.path.isfile("/etc/ca-certificates.conf"):
            return "", "update-ca-certificates", "/etc/ca-certificates.conf"
        return "", "", ""

    def debian_conf(self, conf):
        """/etc/ca-certificates.conf: a '!' in front of the certificate's line (its file under
        /usr/share/ca-certificates) takes it out of the system bundle on the next update-ca-certificates."""
        with open(conf, "rb") as f:
            lines = f.read().decode("utf-8", "replace").split("\n")
        targets = {}
        for sha, (name, der) in self.system_hits.items():
            for i, line in enumerate(lines):
                rel = line.strip().lstrip("!").strip()
                if not rel or rel.startswith("#"):
                    continue
                try:
                    with open(os.path.join("/usr/share/ca-certificates", rel), "rb") as f:
                        if any(hashlib.sha256(d).hexdigest().upper() == sha for d, _ in pem_certs(f.read())):
                            targets[i] = rel
                except (OSError, IOError):
                    continue
        recs = [a for a in self.actions if a["kind"] == "system"]
        if not targets:
            raise RuntimeError("the certificate is not among the files %s lists: take it out of the bundle it is in by hand" % conf)
        for i, rel in targets.items():
            if not lines[i].lstrip().startswith("!"):
                lines[i] = "!" + rel
        for a in recs:
            a["written"] = [conf + ": !" + rel for rel in sorted(targets.values())]
        if not self.check:
            self.backup_file(conf)
            self.write_back(conf, "\n".join(lines).encode("utf-8"))

    def java(self, store):
        path = store["where"]
        rp = os.path.realpath(path)
        if any(rp.startswith(x) or path.startswith(x) for x in SYSTEM_JAVA):
            return self.leave(store, "it follows the system trust (the blocklist clears it)")
        try:
            with open(path, "rb") as f:
                blob = f.read()
        except (OSError, IOError) as e:
            return self.leave(store, "not readable: %s" % e)
        parsed = jks_entries(blob)
        if parsed is None:
            return self.java_keytool(store, path)
        version, entries = parsed
        keep, gone = [], []
        for alias, tag, raw, certs in entries:
            if tag == 2 and certs and self.wanted(certs[0]) is not None:
                gone.append(alias)
            else:
                keep.append((alias, tag, raw, certs))
        if not gone:
            return self.leave(store, "the certificate is in a key entry's chain, not a trusted-certificate entry: the application's own certificate chain")
        names = sorted(set(self.wanted(c[0])["name"] for a, t, r, c in entries if a in gone))
        backup = ""
        try:
            backup = self.backup_file(path)
            if not self.check:
                self.write_back(path, jks_write(version, keep, self.p["keystore_password"]))
            self.act(store, "deleted the entries %s from the keystore - restart the Java services that use it (a running JVM keeps the old trust until it restarts)" % ", ".join(gone), names, backup=backup, aliases=gone)
        except (OSError, IOError) as e:
            self.act(store, "delete the entries %s from the keystore" % ", ".join(gone), names, done=False, backup=backup, error=str(e))

    def java_keytool(self, store, path):
        kt = self.m.get_bin_path("keytool")
        if not kt:
            return self.leave(store, "not a JKS keystore and no keytool on this host: keytool -delete by hand")
        pw = self.p["keystore_password"]
        rc, out, err = run([kt, "-list", "-keystore", path, "-storepass", pw], self.timeout)
        if rc != 0:
            return self.leave(store, "keytool could not open it: %s" % err.decode("utf-8", "replace").strip()[:150])
        gone, names = [], set()
        alias = ""
        for line in out.decode("utf-8", "replace").splitlines():
            m = re.match(r"^(.+?),\s+\w+ \d+, \d{4},\s+(trustedCertEntry|PrivateKeyEntry)", line)
            if m:
                alias = m.group(1).strip() if m.group(2) == "trustedCertEntry" else ""
                continue
            m = re.search(r"\(SHA-?256\):\s*([0-9A-Fa-f:]{95})", line)
            if m and alias:
                e = self.known.by_sha.get(m.group(1).replace(":", "").upper())
                if e is not None and str(e.get("severity", "warning")) in self.sev:
                    gone.append(alias)
                    names.add(e["name"])
        if not gone:
            return self.leave(store, "its matching entries are key entries, or matched by key only: keytool -delete by hand")
        backup = self.backup_file(path)
        errors = []
        for a in gone:
            if self.check:
                continue
            rc, _, err = run([kt, "-delete", "-alias", a, "-keystore", path, "-storepass", pw], self.timeout)
            if rc != 0:
                errors.append("%s: %s" % (a, err.decode("utf-8", "replace").strip()[:120]))
        self.act(store, "deleted the entries %s with keytool - restart the Java services that use it (a running JVM keeps the old trust until it restarts)" % ", ".join(gone), sorted(names), done=not errors, backup=backup,
                 error="; ".join(errors), aliases=gone)

    def bundle(self, store):
        """A certifi or application PEM bundle: the matching blocks cut out. A single DER (or a bundle that would be
        left empty) is moved to the backup folder instead."""
        path = store["where"]
        pkg = self.owned_by_package(path)
        if pkg:
            return self.leave(store, "owned by the package %s: update the package (pip install -U certifi in a venv)" % pkg)
        try:
            with open(path, "rb") as f:
                blob = f.read()
        except (OSError, IOError) as e:
            return self.leave(store, "not readable: %s" % e)
        if b"-----BEGIN" not in blob:
            certs = der_certs(blob)
            if certs and self.wanted(certs[0][0]) is not None:
                return self.move_away(store, path, [self.wanted(certs[0][0])["name"]])
            return self.leave(store, "not a PEM file: take the certificate out by hand")
        text = blob.decode("utf-8", "replace")
        names, kept = set(), 0
        parts, last = [], 0
        for m in PEM.finditer(text):
            try:
                der = base64.b64decode("".join(m.group(2).split()))
            except (TypeError, ValueError):
                kept += 1
                continue
            w = self.wanted(der)
            if w is None:
                kept += 1
                continue
            names.add(w["name"])
            # the block, and the comment lines right before it (certifi, ca-bundle: "# Issuer: ... Label: ...";
            # a file header's "## ..." lines stay)
            seg = text[last:m.start()].split("\n")
            while seg and (not seg[-1].strip() or (seg[-1].startswith("#") and not seg[-1].startswith("##"))):
                seg.pop()
            parts.append("\n".join(seg) + ("\n" if seg else ""))
            last = m.end()
            if text[last:last + 1] == "\n":
                last += 1
        parts.append(text[last:])
        if not names:
            return self.leave(store, "its matching certificate is not a PEM block: take it out by hand")
        if kept == 0 and store.get("kind") == "python":
            return self.leave(store, "the bundle holds nothing else - cut, it would leave the environment without roots: pip install -U certifi there")
        if kept == 0:
            return self.move_away(store, path, sorted(names))
        backup = ""
        try:
            backup = self.backup_file(path)
            if not self.check:
                self.write_back(path, "".join(parts).encode("utf-8"))
            self.act(store, "removed the block from the bundle (%d certificates kept)" % kept, sorted(names), backup=backup)
        except (OSError, IOError) as e:
            self.act(store, "remove the block from the bundle", sorted(names), done=False, backup=backup, error=str(e))

    def move_away(self, store, path, names):
        try:
            backup = self.backup_file(path)
            if not self.check:
                os.unlink(path)
            self.act(store, "the file is the certificate itself: moved to the backup folder", names, backup=backup)
        except (OSError, IOError) as e:
            self.act(store, "move the file to the backup folder", names, done=False, error=str(e))

    def nssdb(self, store):
        """An NSS database: the certificate deleted by its nickname (certutil -D), exported to the backup first."""
        certutil = self.m.get_bin_path("certutil")
        if not certutil:
            return self.leave(store, "no certutil on this host (nss-tools): certutil -D by hand")
        d = store["where"]
        nicks = {n: k for n, k in (store.get("nicknames") or {}).items() if n in self.names(store)}
        if not nicks:
            return self.leave(store, "no nickname known for the certificate: certutil -L -d sql:%s, then certutil -D -n <nickname>" % d)
        action = "deleted %s from the NSS database with certutil" % ", ".join(sorted(set(nicks.values())))
        if self.check:
            return self.act(store, action, sorted(nicks), done=False)
        errors, backups = [], []
        for name, nick in sorted(nicks.items()):
            rc, pem, err = run([certutil, "-L", "-d", "sql:" + d, "-n", nick, "-a"], self.timeout)
            if rc == 0 and pem:
                if not self.backup:
                    self.backup = os.path.join(self.p["backup_dir"], time.strftime("%Y-%m-%d-%H%M%S"))
                    os.makedirs(self.backup, 0o700)
                dest = os.path.join(self.backup, d.lstrip("/").replace("/", "__") + "__" + slug(nick) + ".pem")
                with open(dest, "wb") as f:
                    f.write(pem)
                backups.append(dest)
            rc, _, err = run([certutil, "-D", "-d", "sql:" + d, "-n", nick], self.timeout)
            if rc != 0:
                errors.append("%s: %s" % (nick, err.decode("utf-8", "replace").strip()[:120]))
        self.act(store, action, sorted(nicks), done=not errors, backup=", ".join(backups), error="; ".join(errors))

    def all(self):
        for s in self.p["stores"]:
            if not self.names(s):
                continue
            kind = s.get("kind", "")
            if kind in NOT_REMOVABLE:
                self.leave(s, NOT_REMOVABLE[kind])
            elif kind not in self.kinds:
                self.leave(s, "the kind %s is not in trust_remove_kinds" % kind)
            elif kind == "system":
                self.system(s)
            elif kind == "java":
                self.java(s)
            elif kind in ("python", "file"):
                self.bundle(s)
            elif kind == "nssdb":
                self.nssdb(s)
            else:
                self.leave(s, "no removal for this kind")
        self.finish_system()
        return {"actions": self.actions, "not_removed": self.not_removed, "backup": self.backup, "changed": self.changed}


def main():
    module = AnsibleModule(argument_spec=dict(
        known=dict(type="list", elements="dict", default=[]),
        stores=dict(type="list", elements="dict", default=[]),
        kinds=dict(type="list", elements="str", default=["system", "java", "python", "file", "nssdb"]),
        severities=dict(type="list", elements="str", default=["critical", "warning"]),
        backup_dir=dict(type="str", default="/var/backups/site-automation/trust"),
        blocklist_dir=dict(type="str", default=""),
        update_cmd=dict(type="str", default=""),
        keystore_password=dict(type="str", default="changeit", no_log=True),
        timeout=dict(type="int", default=120),
    ), supports_check_mode=True)
    module.exit_json(**Remover(module).all())


if __name__ == "__main__":
    main()
