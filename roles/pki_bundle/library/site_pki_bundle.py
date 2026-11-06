#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Is there a newer DoD PKI CA bundle than the one in your repository (cert_install_ca_bundle)? Downloads the PKCS7
bundle zip, checks it - DISA's signed list of its files' SHA-256 when the system can, and every CA certificate's
signature up to a root you already trust - and says what it adds and removes. Writes nothing in your repository."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_pki_bundle
short_description: Check the DoD PKI's PKCS7 CA bundle against the copy in your repository
description:
  - Downloads the bundle (C(url) - the zip DISA publishes, a .p7b, or a file on the execution environment) and reads
    its CA certificates.
  - "The zip's signed list of its files' SHA-256 (the .sha256 file, CMS signed by the DoD PKE code signing certificate):
    its signature checked with the openssl command, its signer's certificate up to a root of C(current) by signature. DISA
    still signs it with SHA-1, which this system's crypto policy may refuse (FIPS mode always does) - then it is said
    that the signature was not checked."
  - Every CA certificate is checked by its signature up to a root of C(current) (no C(current) - the bundle's own roots,
    taken on trust). A root that is not in C(current) is listed; the CAs under it count only when DISA's signature on
    the list of files verified - else the check fails (compare that root's fingerprint with DISA's).
  - Compares it with C(current) - the CAs it adds, the ones it no longer has, the ones expiring - and writes the
    checked .p7b into C(dest_dir) (for the email) when it differs. Reads only - also in check mode.
options:
  url: {description: "The bundle: https://..., file:///..., or a path on the execution environment.", type: str, required: true}
  current: {description: "Your bundle file (cert_install_ca_bundle); empty = none yet.", type: str, default: ''}
  dest_dir: {description: The folder the checked .p7b is written to., type: path, required: true}
  validate_certs: {description: Check the download site's certificate., type: bool, default: true}
  ca_path: {description: A CA bundle for the download site's certificate., type: path}
  timeout: {description: Seconds for the download., type: int, default: 120}
  max_bytes: {description: A bigger download is refused., type: int, default: 20971520}
  warn_days: {description: A CA in the bundle expiring within this many days is listed., type: int, default: 90}
author: site automation
'''

RETURN = r'''
bundle:
  description: >-
    status (current, newer, first, failed), version (5.14), name (the .p7b's file name), file (the checked copy in
    dest_dir, when it differs from current), cas (its CA certificates), unreadable, roots (checked up to; of current, else its own), signature (verified, not checked: why, FAILED: why),
    hashes (match, mismatch: files, not in the zip), added / removed / expiring / expired ([{subject, not_after, sha1}]),
    new_roots, untrusted, broken (its chain does not build in the bundle), current_count, problems (why it must not be used).
  type: dict
  returned: always
'''

import base64
import datetime
import hashlib
import io
import os
import re
import shutil
import subprocess
import tempfile
import warnings
import zipfile
from urllib.error import HTTPError, URLError

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.urls import open_url

try:
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

PEM_BLOCK = re.compile(r'-----BEGIN (CERTIFICATE|PKCS7)-----(.+?)-----END \1-----', re.S)
BUNDLE = re.compile(r'certificates_pkcs7_v(\d+)_(\d+)(?:_(\d+))?_([a-z]+)\.(der|pem)\.p7b$', re.I)
# DISA's .sha256 is signed with SHA-1: allowed for this one check (RHEL's crypto policy refuses it by default)
SHA1_CNF = 'openssl_conf = d\n[d]\nalg_section = a\n[a]\nrh-allow-sha1-signatures = yes\n'


def _tlv(data, i):
    """One DER element at i -> (tag, start of its value, end of it); definite lengths only."""
    tag, n = data[i], data[i + 1]
    i += 2
    if n & 0x80:
        k = n & 0x7f
        if not k or k > 4:
            raise ValueError('indefinite or too long a length')
        n = int.from_bytes(data[i:i + k], 'big')
        i += k
    if i + n > len(data):
        raise ValueError('cut short')
    return tag, i, i + n


def p7_certs(der):
    """The certificates of a PKCS7 SignedData, each read on its own (one the library cannot read - the DoD bundle has an
    old root with a NULL in its algorithm - does not lose the others) -> (certificates, how many could not be read)."""
    out, bad = [], 0
    _, i, _ = _tlv(der, 0)                         # ContentInfo
    _, _, i = _tlv(der, i)                         # contentType
    _, i, _ = _tlv(der, i)                         # [0] content
    _, i, end = _tlv(der, i)                       # SignedData
    while i < end:
        tag, v, e = _tlv(der, i)
        if tag == 0xA0:                            # [0] certificates
            while v < e:
                _, _, ce = _tlv(der, v)
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter('ignore')
                        out.append(x509.load_der_x509_certificate(der[v:ce]))
                except Exception:
                    bad += 1
                v = ce
            break
        i = e
    return out, bad


def certs_of(data):
    """A .p7b (DER or PEM), or PEM certificates -> (certificates, unreadable)."""
    text = data.decode('latin-1')
    blocks = PEM_BLOCK.findall(text)
    if not blocks:
        try:
            return p7_certs(data)
        except (ValueError, IndexError):
            try:
                return [x509.load_der_x509_certificate(data)], 0
            except Exception:
                return [], 0
    out, bad = [], 0
    for kind, body in blocks:
        der = base64.b64decode(re.sub(r'\s+', '', body))
        if kind == 'PKCS7':
            got, b = p7_certs(der)
            out.extend(got)
            bad += b
        else:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore')
                    out.append(x509.load_der_x509_certificate(der))
            except Exception:
                bad += 1
    return out, bad


def pick(files):
    """The whole bundle's .p7b among a zip's files (not the one-root ones): DER first. -> (name, version) or (None, '')."""
    best = None
    for name in files:
        m = BUNDLE.search(os.path.basename(name))
        if m:
            rank = (m.group(5).lower() != 'der', len(name))
            if best is None or rank < best[0]:
                best = (rank, name, '.'.join(x for x in m.group(1, 2, 3) if x))
    return (best[1], best[2]) if best else (None, '')


def bundle_files(data, url):
    """The download -> {name: bytes}: the zip's files, or the one file."""
    if data[:4] == b'PK\x03\x04':
        out = {}
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in z.infolist()[:200]:
                if not info.is_dir() and info.file_size <= 20971520:
                    out[info.filename] = z.read(info)
        return out
    return {os.path.basename(url.split('?')[0]) or 'bundle.p7b': data}


def ca_entry(c):
    der = c.public_bytes(Encoding.DER)
    return {'subject': c.subject.rfc4514_string(), 'not_after': utc_after(c).strftime('%Y-%m-%d'),
            'sha1': hashlib.sha1(der).hexdigest().upper(), 'sha256': hashlib.sha256(der).hexdigest()}


def utc_after(c):
    v = getattr(c, 'not_valid_after_utc', None)
    return v.replace(tzinfo=None) if v is not None else c.not_valid_after


def signed_by(cert, ca):
    try:
        cert.verify_directly_issued_by(ca)
        return True
    except Exception:
        return False


def root_of(c, pool, limit=8):
    """The root c chains up to through pool (by signature) -> that root, or None."""
    cur = c
    for _ in range(limit):
        if cur.subject == cur.issuer:
            return cur if signed_by(cur, cur) else None
        nxt = next((x for x in pool if x.subject == cur.issuer and signed_by(cur, x)), None)
        if nxt is None:
            return None
        cur = nxt
    return None


def signer_chain(signer, pool, anchor_der, limit=8):
    """Up from the signer's certificate, by signature, through pool -> True when it reaches one of the anchors."""
    cur = signer
    for _ in range(limit):
        if cur.public_bytes(Encoding.DER) in anchor_der:
            return True
        if cur.subject == cur.issuer:
            return False                    # a root, not one of yours
        nxt = next((x for x in pool if x.subject == cur.issuer and signed_by(cur, x)), None)
        if nxt is None:
            return False
        cur = nxt
    return False


def check_signature(files, sig_name, anchors, work):
    """DISA's signed list of SHA-256 -> (signature text, {file: sha256} or None). openssl checks the signature itself
    (-noverify: openssl 3.0 does not use -certfile to build the signer's chain, 3.2 and later do); the signer's
    certificate is then checked here, by signature, up to one of your roots through the zip's DoD_PKE_CA_chain.pem."""
    chain = next((n for n in files if os.path.basename(n).lower() == 'dod_pke_ca_chain.pem'), None)
    exe = shutil.which('openssl')
    if not exe:
        return 'not checked: no openssl command in this execution environment', None
    for name, data in (('sig.der', files[sig_name]), ('sha1.cnf', SHA1_CNF.encode())):
        with open(os.path.join(work, name), 'wb') as fh:
            fh.write(data)
    args = [exe, 'cms', '-verify', '-noverify', '-inform', 'DER', '-in', os.path.join(work, 'sig.der'),
            '-signer', os.path.join(work, 'signer.pem'), '-out', os.path.join(work, 'hashes.txt')]
    r = subprocess.run(args, capture_output=True, text=True, env=dict(os.environ, OPENSSL_CONF=os.path.join(work, 'sha1.cnf')))
    err = (r.stderr or '').strip()
    if r.returncode != 0:
        if re.search(r'invalid digest|unsupported|disabled for FIPS|not allowed|ca md too weak|sha1', err, re.I):
            return 'not checked: this system refuses SHA-1 signatures (FIPS mode or its crypto policy), and DISA signs the list with SHA-1', None
        last = [ln for ln in err.splitlines() if ln.strip()]
        return 'FAILED: %s' % (last[-1][:300] if last else 'openssl cms -verify rc %d' % r.returncode), None
    try:
        with open(os.path.join(work, 'signer.pem'), 'rb') as fh:
            signers, _ = certs_of(fh.read())
    except (IOError, OSError):
        signers = []
    if not signers:
        return 'FAILED: the list carries no signer certificate', None
    pool = (certs_of(files[chain])[0] if chain else []) + list(anchors)
    anchor_der = set(c.public_bytes(Encoding.DER) for c in anchors)
    for c in signers:
        if not signer_chain(c, pool, anchor_der):
            return 'FAILED: signed by %s, which does not chain up to a root of yours' % c.subject.rfc4514_string(), None
    hashes = {}
    with open(os.path.join(work, 'hashes.txt'), errors='replace') as fh:
        for line in fh:
            m = re.match(r'([0-9a-fA-F]{64})\s+\*?(.+?)\s*$', line)
            if m:
                hashes[m.group(2)] = m.group(1).lower()
    return 'verified', hashes


def run(m):
    p = m.params
    res = {'status': 'failed', 'version': '', 'name': '', 'file': '', 'cas': 0, 'unreadable': 0, 'roots': [],
           'signature': '', 'hashes': '', 'added': [], 'removed': [], 'expiring': [], 'expired': [], 'new_roots': [],
           'untrusted': [], 'broken': [], 'current_count': 0, 'current_version': '', 'problems': []}
    url = p['url'].strip()
    try:
        if re.match(r'^[a-z]+://', url, re.I):
            r = open_url(url, method='GET', validate_certs=p['validate_certs'], ca_path=p['ca_path'], timeout=p['timeout'])
            data = r.read(p['max_bytes'] + 1)
        else:
            with open(url, 'rb') as fh:
                data = fh.read(p['max_bytes'] + 1)
    except HTTPError as e:
        res['problems'].append('the download %s answered HTTP %s' % (url, e.code))
        return res
    except (URLError, IOError, OSError) as e:
        res['problems'].append('the bundle could not be downloaded from %s (%s): no way out to the internet from the '
                               'execution environment? a proxy (pki_bundle_proxy), or a copy on a file share (pki_bundle_url)'
                               % (url, getattr(e, 'reason', None) or e))
        return res
    if len(data) > p['max_bytes']:
        res['problems'].append('the download is bigger than %d bytes: not the bundle?' % p['max_bytes'])
        return res
    try:
        files = bundle_files(data, url)
    except zipfile.BadZipfile as e:
        res['problems'].append('the download is a broken zip (%s)' % e)
        return res
    name, version = pick(files)
    if name is None:
        if len(files) == 1:
            name = list(files)[0]
        else:
            res['problems'].append('no Certificates_PKCS7_v..._<group>.der.p7b in the download (%s): not the PKCS7 bundle zip?'
                                   % ', '.join(sorted(files)[:8]))
            return res
    res.update(name=os.path.basename(name), version=version)
    new, bad = certs_of(files[name])
    new = [c for c in new if c.subject == c.issuer or _is_ca(c)]
    res.update(cas=len(new), unreadable=bad)
    if not new:
        res['problems'].append('%s holds no CA certificate' % name)
        return res

    cur = []
    if p['current']:
        try:
            with open(p['current'], 'rb') as fh:
                cdata = fh.read()
        except (IOError, OSError) as e:
            res['problems'].append('your bundle cert_install_ca_bundle %s cannot be read (%s)' % (p['current'], getattr(e, 'strerror', None) or e))
            return res
        if cdata[:4] == b'PK\x03\x04':
            cfiles = bundle_files(cdata, p['current'])
            cname, cver = pick(cfiles)
            cdata = cfiles.get(cname, b'') if cname else b''
            res['current_version'] = cver
        else:
            mv = BUNDLE.search(os.path.basename(p['current']))
            res['current_version'] = '.'.join(x for x in mv.group(1, 2, 3) if x) if mv else ''
        cur, _ = certs_of(cdata)
        res['current_count'] = len(cur)
    anchors = [c for c in (cur or new) if c.subject == c.issuer and signed_by(c, c)]
    if not anchors:
        res['problems'].append('your bundle has no root CA to check the new one against' if cur else '%s has no root CA' % name)
        return res
    res['roots'] = [ca_entry(c) for c in anchors]
    anchor_der = set(c.public_bytes(Encoding.DER) for c in anchors)

    work = tempfile.mkdtemp(prefix='pki-bundle-')
    try:
        sig = next((n for n in files if n.lower().endswith('.sha256')), None)
        hashes = None
        if sig:
            res['signature'], hashes = check_signature(files, sig, anchors, work)
        else:
            res['signature'] = 'not checked: no signed .sha256 list with it (only the zip DISA publishes has one)'
        if hashes is not None:
            wrong = [n for n in files if os.path.basename(n) in hashes and hashlib.sha256(files[n]).hexdigest() != hashes[os.path.basename(n)]]
            res['hashes'] = ('mismatch: ' + ', '.join(os.path.basename(n) for n in wrong)) if wrong else 'match'
            if os.path.basename(name) not in hashes:
                res['hashes'] += '; %s is not in the signed list' % os.path.basename(name)
                res['problems'].append('%s is not in the signed list of the zip\'s files' % os.path.basename(name))
            if wrong:
                res['problems'].append('the SHA-256 of %s is not the one DISA signed' % ', '.join(os.path.basename(n) for n in wrong))
        if res['signature'].startswith('FAILED'):
            res['problems'].append('DISA\'s signature on the list of the zip\'s files did not verify against your roots: %s' % res['signature'][8:])

        # every CA up to a root you trust, by its signature. A root not in yours: DISA's verified signature vouches for
        # it (listed, to compare); without that signature the CAs under it are not taken
        signed = res['signature'] == 'verified' and res['hashes'] == 'match'
        for c in new:
            top = root_of(c, new)
            if top is None:
                res['broken'].append(ca_entry(c)['subject'])
            elif top.public_bytes(Encoding.DER) not in anchor_der:
                e = ca_entry(top)
                if c is top:
                    res['new_roots'].append(e)
                elif not signed:
                    res['untrusted'].append(ca_entry(c)['subject'] + ': under ' + e['subject'] + ', a root not in your bundle')
        if res['untrusted']:
            res['problems'].append('%d CA certificate(s) are under a root not in your bundle, and DISA\'s signature could not '
                                   'vouch for it: compare that root\'s SHA-1 with DISA\'s, then put the bundle in by hand' % len(res['untrusted']))

        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        for c in new:
            left = (utc_after(c) - now).days
            if left < 0:
                res['expired'].append(ca_entry(c))
            elif left <= p['warn_days']:
                res['expiring'].append(ca_entry(c))
        have = set(c.public_bytes(Encoding.DER) for c in cur)
        got = set(c.public_bytes(Encoding.DER) for c in new)
        res['added'] = [ca_entry(c) for c in new if c.public_bytes(Encoding.DER) not in have] if cur else []
        res['removed'] = [ca_entry(c) for c in cur if c.public_bytes(Encoding.DER) not in got]
        res['status'] = 'first' if not cur else ('newer' if res['added'] or res['removed'] else 'current')
        if res['problems']:
            res['status'] = 'failed'
        elif res['status'] != 'current':
            if not os.path.isdir(p['dest_dir']):
                os.makedirs(p['dest_dir'], 0o755)
            res['file'] = os.path.join(p['dest_dir'], os.path.basename(name))
            with open(res['file'], 'wb') as fh:
                fh.write(files[name])
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return res


def _is_ca(c):
    try:
        return bool(c.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
    except x509.ExtensionNotFound:
        return False


def main():
    m = AnsibleModule(argument_spec=dict(
        url=dict(type='str', required=True), current=dict(type='str', default=''), dest_dir=dict(type='path', required=True),
        validate_certs=dict(type='bool', default=True), ca_path=dict(type='path'), timeout=dict(type='int', default=120),
        max_bytes=dict(type='int', default=20971520), warn_days=dict(type='int', default=90)),
        supports_check_mode=True)
    if not HAS_CRYPTO:
        m.fail_json(msg='the Python library cryptography is not in this execution environment (ansible-core needs it: use an EE built on ansible-core)')
    m.exit_json(changed=False, bundle=run(m))


if __name__ == '__main__':
    main()
