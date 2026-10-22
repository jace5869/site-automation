# -*- coding: utf-8 -*-
"""Certificates in a trust store, matched against a list of known ones (roles/check_trust: a CA whose key was
compromised, a root the browsers dropped). Standard library only: it runs on every managed host with the
host's own Python, and on the controller for vCenter. The same file is in roles/check_trust/module_utils and
roles/vmware_vm/module_utils (each role's modules find their own); tests/test_trust.py keeps them identical.

A certificate is known by its SHA-256 fingerprint, or by the SHA-256 of its public key (SubjectPublicKeyInfo):
a certificate not on the list but carrying a compromised key - a cross-sign nobody listed - is a hit too."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import base64
import binascii
import hashlib
import re

PEM = re.compile(r"-----BEGIN (TRUSTED CERTIFICATE|CERTIFICATE)-----(.*?)-----END \1-----", re.S)
OID_CN = b"\x06\x03\x55\x04\x03"


def _tlv(b, i):
    """The DER element at b[i]: (tag, start of its value, end). ValueError when the bytes are not DER."""
    if i + 2 > len(b):
        raise ValueError("short")
    tag, l = b[i], b[i + 1]
    hdr = 2
    if l & 0x80:
        n = l & 0x7F
        if n == 0 or n > 4 or i + 2 + n > len(b):
            raise ValueError("length")
        l = int(binascii.hexlify(b[i + 2:i + 2 + n]), 16)
        hdr = 2 + n
    start = i + hdr
    if start + l > len(b):
        raise ValueError("overrun")
    return tag, start, start + l


def spki_sha256(der):
    """The SHA-256 of a certificate's SubjectPublicKeyInfo (the whole element), upper-case hex; '' when the
    certificate cannot be walked."""
    try:
        _, cs, _ = _tlv(der, 0)                 # Certificate
        _, pos, _ = _tlv(der, cs)               # TBSCertificate
        tag, _, end = _tlv(der, pos)
        if tag == 0xA0:                         # [0] version
            pos = end
        for _ in range(5):                      # serial, signature algorithm, issuer, validity, subject
            _, _, pos = _tlv(der, pos)
        _, _, end = _tlv(der, pos)              # subjectPublicKeyInfo
        return hashlib.sha256(der[pos:end]).hexdigest().upper()
    except (ValueError, IndexError):
        return ""


def subject_cn(der):
    """The certificate's common name (its subject's CN; '' when none or unreadable)."""
    try:
        _, cs, _ = _tlv(der, 0)
        _, pos, _ = _tlv(der, cs)
        tag, _, end = _tlv(der, pos)
        if tag == 0xA0:
            pos = end
        for _ in range(4):
            _, _, pos = _tlv(der, pos)
        _, ss, se = _tlv(der, pos)              # subject
        i = der.find(OID_CN, ss, se)
        if i < 0:
            return ""
        _, vs, ve = _tlv(der, i + len(OID_CN))
        return der[vs:ve].decode("utf-8", "replace")
    except (ValueError, IndexError):
        return ""


def pem_certs(text):
    """Every certificate in a PEM text -> [(der, rejected)]. A 'TRUSTED CERTIFICATE' block (OpenSSL's trust
    bundle) carries trust settings after the certificate; rejected = it says the certificate is NOT to be
    trusted (a blocklisted one). Blocks that do not decode are skipped."""
    out = []
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    for m in PEM.finditer(text):
        try:
            der = base64.b64decode("".join(m.group(2).split()))
        except (binascii.Error, ValueError):
            continue
        if not der:
            continue
        rejected = False
        if m.group(1) == "TRUSTED CERTIFICATE":
            try:
                _, _, end = _tlv(der, 0)
                aux = der[end:]
                der = der[:end]
                if aux:
                    _, a0, a1 = _tlv(aux, 0)
                    pos = a0
                    while pos < a1:
                        tag, _, nxt = _tlv(aux, pos)
                        if tag == 0xA0:         # [0] reject: OIDs the certificate is NOT trusted for
                            rejected = True
                        pos = nxt
            except (ValueError, IndexError):
                pass
        out.append((der, rejected))
    return out


def der_certs(blob):
    """A file's certificates: PEM blocks if it has any, else the file as one DER certificate (when it is one)."""
    if b"-----BEGIN" in blob:
        return pem_certs(blob)
    try:
        tag, _, end = _tlv(blob, 0)
        if tag == 0x30 and end == len(blob) and spki_sha256(blob):
            return [(blob, False)]
    except (ValueError, IndexError):
        pass
    return []


class Known(object):
    """The list to look for: [{name, kind (root / intermediate), sha256, spki_sha256, severity, why, ...}]."""

    def __init__(self, entries):
        self.by_sha, self.by_spki = {}, {}
        for e in entries or []:
            e = dict(e)
            e["name"] = str(e.get("name") or e.get("sha256") or "?")
            s = str(e.get("sha256") or "").replace(":", "").upper()
            k = str(e.get("spki_sha256") or "").replace(":", "").upper()
            if s:
                self.by_sha[s] = e
            if k and k not in self.by_spki:
                self.by_spki[k] = e

    def match(self, der):
        """The entry a certificate matches, with how: {'how': 'certificate'} (the certificate itself) or
        {'how': 'key'} (another certificate with the same compromised key); None when it is not on the list."""
        s = hashlib.sha256(der).hexdigest().upper()
        e = self.by_sha.get(s)
        if e is not None:
            return dict(e, how="certificate", sha256=s)
        k = spki_sha256(der)
        e = self.by_spki.get(k) if k else None
        if e is not None:
            return dict(e, how="key", sha256=s, cn=subject_cn(der))
        return None

    def scan(self, certs):
        """[(der, rejected)] -> {'count': n, 'hits': [...], 'blocked': [names]}: the hits in the order found,
        each once; a rejected certificate's name goes to blocked instead."""
        hits, blocked, seen = [], [], set()
        for der, rejected in certs:
            m = self.match(der)
            if m is None:
                continue
            key = (m["name"], m["how"], m.get("cn", ""))
            if rejected:
                if m["name"] not in blocked:
                    blocked.append(m["name"])
            elif key not in seen:
                seen.add(key)
                hits.append(m)
        return {"count": len(certs), "hits": hits, "blocked": blocked}


def version_tuple(v):
    """'1.26.10' / 'v22.23.4' / '3.131.1-2.el9' -> (1, 26, 10): the leading numbers, for comparing."""
    out = []
    for part in re.split(r"[^0-9]+", str(v or "").lstrip("vV")):
        if not part:
            if out:
                break
            continue
        out.append(int(part))
    return tuple(out)


def below(version, fixed):
    """True when version is older than fixed (both as strings); False when either is empty."""
    a, b = version_tuple(version), version_tuple(fixed)
    return bool(a and b) and a < b


def fixed_for(version, fixed_by_line):
    """The fixed version for a program's line: fixed_by_line {'1.26': '1.26.10', '1.27': '1.27.3'} (Go) or
    {'22': '22.23.4'} (Node). Returns (fixed, status): status 'below' / 'ok' / 'unsupported' (a line older than
    every one that got a fix - no fixed version to go to) / 'unknown' (no version, or a newer line than listed)."""
    v = version_tuple(version)
    lines = {}
    for k, f in (fixed_by_line or {}).items():
        lines[version_tuple(k)] = str(f)
    if not v or not lines:
        return "", "unknown"
    n = len(next(iter(lines)))
    line = v[:n]
    if line in lines:
        return lines[line], ("below" if below(version, lines[line]) else "ok")
    if line < min(lines):
        return "", "unsupported"
    return "", "unknown"
