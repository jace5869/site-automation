#!/usr/bin/python
# -*- coding: utf-8 -*-
"""A new private key and a certificate signing request (CSR) for each certificate FILE that expires
soon, with the same subject and the same names (subjectAltName) - made on the host with the openssl
command, so it works on every RHEL (OpenSSL 1.0.2 to 3.x, FIPS mode) without a Python library.
Runs on the managed host (Python 3.6 and later)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_cert_csr
short_description: Make a new key and a CSR for the certificate files that expire soon
description:
  - For each certificate given (from the certs check), reads the certificate file, and makes a new
    private key and a CSR with the same subject and subjectAltName, in O(dir). The key never leaves
    the host and is never returned; only the CSR (public) is.
  - A pending CSR for the same certificate (same SHA-256 fingerprint) is reused, never made again,
    so the CSR the PKI already has stays valid.
  - Not made, with the reason - a certificate served on a port or kept in a keystore (only files are
    renewed), a CA certificate (a chain, or a root - it signs certificates), a self-signed one (unless
    O(self_signed)), a package's placeholder (O(placeholder) - postfix's), one under O(exclude) or
    issued by O(exclude_issuer), one not matching O(only) / O(issuer).
  - Each CSR is checked before it is kept - it verifies, its public key is the new key's, and its
    subject and names are exactly the old certificate's.
  - In check mode nothing is written.
options:
  certs:
    description: The certificates to renew, as the certs check lists them (where, subject, end, days, expires, id, kind, severity).
    type: list
    elements: dict
    required: true
  dir: {description: Where the new keys and CSRs are kept (created 0700)., type: path, required: true}
  key_bits: {description: The RSA key size (at least; a bigger old key keeps its size). An EC certificate gets a new key on the same curve., type: int, default: 2048}
  digest: {description: The CSR's signature digest., type: str, default: sha256}
  only: {description: A regular expression - only certificate files whose path matches (empty = all)., type: str, default: ''}
  issuer: {description: A regular expression - only certificates whose issuer matches (empty = any CA)., type: str, default: ''}
  exclude: {description: A regular expression - certificate files whose path matches are never renewed., type: str, default: ''}
  exclude_issuer: {description: A regular expression - certificates whose issuer matches are never renewed., type: str, default: ''}
  self_signed: {description: "A self-signed certificate (made on the host) gets a CSR too, for the PKI to issue a real certificate in its place - not a CA's own (a root, Certificate Sign), not one only for localhost. O(issuer) does not apply to it.", type: bool, default: false}
  placeholder: {description: "A regular expression on the subject - a package's placeholder certificate (postfix's, O=SomeOrganization) never gets a CSR, whatever O(self_signed) says.", type: str, default: ''}
  max: {description: At most this many new CSRs in one run (pending ones do not count)., type: int, default: 10}
  file_ext: {description: "The extension of the CSR's attachment name (<common name>-<8 characters of the CSR's SHA-256>.<ext>).", type: str, default: csr}
  job: {description: The AAP job, written in the CSR's info file., type: str, default: ''}
author: site automation
'''

RETURN = r'''
certs:
  description: >-
    One entry per certificate (a certificate found in two places is one entry) - wheres, ids, subject,
    issuer, expires, days, severity, status (made, exists, would make, skipped, failed), reason, sans,
    key, key_path, csr_path, file_name, csr (the PEM text), csr_sha256.
  type: list
  returned: always
'''

import base64
import calendar
import hashlib
import json
import os
import re
import time

from ansible.module_utils.basic import AnsibleModule

PEM_CERT = re.compile(r'-----BEGIN CERTIFICATE-----\s+.*?-----END CERTIFICATE-----', re.S)
HEX = '0123456789abcdefABCDEF'
SAN_KINDS = (('DNS:', 'DNS'), ('IP Address:', 'IP'), ('email:', 'email'), ('URI:', 'URI'))
CURVES = {'P-256': 'prime256v1', 'P-384': 'secp384r1', 'P-521': 'secp521r1'}


class CertError(Exception):
    """A certificate that cannot be renewed this way: the message is the reason."""


class CertGone(CertError):
    """The certificate the check reported is no longer there (replaced since): nothing to renew."""


def parse_rfc2253(text):
    """The subject as `openssl -nameopt RFC2253` prints it (last RDN first, escaped) ->
    [(type, value, joined to the one before by '+')], in the printed order."""
    out, typ, buf, joined, i = [], None, bytearray(), False, 0
    while i < len(text):
        ch = text[i]
        if ch == '\\' and i + 1 < len(text):
            pair = text[i + 1:i + 3]
            if len(pair) == 2 and pair[0] in HEX and pair[1] in HEX:
                buf.append(int(pair, 16))
                i += 3
            else:
                buf.extend(text[i + 1].encode('utf-8'))
                i += 2
            continue
        if typ is None:
            if ch == '=':
                typ, buf = buf.decode('utf-8').strip(), bytearray()
                if text[i + 1:i + 2] == '#':
                    raise CertError('its subject has an encoded (#hex) value: make this CSR by hand')
            else:
                buf.extend(ch.encode('utf-8'))
        elif ch in ',+':
            out.append((typ, buf.decode('utf-8'), joined))
            typ, buf, joined = None, bytearray(), ch == '+'
        else:
            buf.extend(ch.encode('utf-8'))
        i += 1
    if typ is not None:
        out.append((typ, buf.decode('utf-8'), joined))
    elif buf.strip():
        raise CertError('its subject cannot be read: %s' % text)
    return out


def conf_escape(value):
    """A value for an openssl config file: \\ $ # " ' escaped (variables, comments, quotes)."""
    return re.sub(r'([\\$#"\'])', r'\\\1', value)


def dn_lines(rdns):
    """The [dn] section: the printed (reversed) order turned back, a number before each name so the
    same name can repeat (OU), '+' for a value in the same RDN as the one before."""
    rev = list(reversed(rdns))
    lines = []
    for j, (typ, value, _) in enumerate(rev):
        plus = j > 0 and rdns[len(rdns) - j][2]
        lines.append('%d.%s%s = %s' % (j, '+' if plus else '', typ, conf_escape(value)))
    return lines


def parse_sans(text, what='certificate'):
    """The subjectAltName entries of `openssl x509|req -text` -> ['DNS:a', 'IP:192.0.2.1', ...]."""
    lines = text.splitlines()
    for n, line in enumerate(lines):
        if 'X509v3 Subject Alternative Name' in line and n + 1 < len(lines):
            out = []
            for entry in [e.strip() for e in lines[n + 1].split(', ') if e.strip()]:
                for printed, kind in SAN_KINDS:
                    if entry.startswith(printed):
                        out.append('%s:%s' % (kind, entry[len(printed):]))
                        break
                else:
                    raise CertError('its %s names a kind openssl cannot write back (%s): make this CSR by hand' % (what, entry))
            return out
    return []


def key_of(text):
    """('rsa', bits) / ('ec', curve) / ('other', description) from `openssl x509 -text`."""
    alg = re.search(r'Public Key Algorithm:\s*(\S+)', text)
    alg = alg.group(1) if alg else ''
    bits = re.search(r'Public[- ]Key:\s*\((\d+) bit', text)
    if alg == 'rsaEncryption':
        return 'rsa', int(bits.group(1)) if bits else 2048
    if alg == 'id-ecPublicKey':
        oid = re.search(r'ASN1 OID:\s*(\S+)', text)
        nist = re.search(r'NIST CURVE:\s*(\S+)', text)
        curve = oid.group(1) if oid else CURVES.get(nist.group(1) if nist else '', '')
        if curve:
            return 'ec', curve
    return 'other', alg or 'unknown'


def end_epoch(value):
    """'Nov  3 12:00:00 2026 GMT' -> seconds since 1970."""
    return calendar.timegm(time.strptime(value.strip(), '%b %d %H:%M:%S %Y %Z'))


def host_names(subject, sans):
    """The names a PKI could issue a certificate for: the CN and the DNS and IP names, without
    localhost and the loopback addresses."""
    names = [v for t, v, _ in parse_rfc2253(subject) if t == 'CN'] + [n.split(':', 1)[1] for n in sans if n.startswith(('DNS:', 'IP:'))]
    local = re.compile(r'^(localhost[46]?(\.localdomain[46]?)?|127\.\d+\.\d+\.\d+|::1|0:0:0:0:0:0:0:1)$', re.I)
    return [n for n in names if n.strip() and not local.match(n.strip())]


def safe_name(value):
    return re.sub(r'[^A-Za-z0-9._-]+', '_', value).strip('._')[:60] or 'certificate'


class Renewer(object):
    def __init__(self, module):
        self.m = module
        self.p = module.params

    def run(self, args, data=None):
        rc, out, err = self.m.run_command(args, data=data, binary_data=data is not None,
                                          environ_update={'LC_ALL': 'C'})
        return rc, out, err

    def openssl(self, args, data=None, what='openssl'):
        rc, out, err = self.run(['openssl'] + args, data)
        if rc != 0:
            last = (err or out).strip().splitlines()
            raise CertError('%s failed: %s' % (what, last[-1] if last else 'exit %d' % rc))
        return out

    def cert_info(self, pem):
        head = self.openssl(['x509', '-noout', '-subject', '-issuer', '-enddate', '-serial', '-nameopt', 'RFC2253'], pem, 'reading the certificate')
        info = {}
        for line in head.splitlines():
            key, _, value = line.partition('=')
            info[key.strip().lower()] = value.strip()
        text = self.openssl(['x509', '-noout', '-text'], pem, 'reading the certificate')
        body = ''.join(pem.strip().splitlines()[1:-1])
        return {'subject': info.get('subject', ''), 'issuer': info.get('issuer', ''), 'serial': info.get('serial', ''),
                'end': end_epoch(info.get('notafter', '')), 'text': text,
                'fingerprint': hashlib.sha256(base64.b64decode(body)).hexdigest(),
                'ca': bool(re.search(r'CA:TRUE', text)),
                # a real CA (a root): key usage Certificate Sign. A self-signed server certificate made with
                # openssl req -x509 is CA:TRUE too (RHEL 8 and 9), but without it
                'cert_sign': bool(re.search(r'X509v3 Key Usage:[^\n]*\n[^\n]*Certificate Sign', text))}

    def csr_info(self, path):
        self.openssl(['req', '-in', path, '-noout', '-verify'], what='checking the CSR')
        subject = self.openssl(['req', '-in', path, '-noout', '-subject', '-nameopt', 'RFC2253'], what='reading the CSR')
        subject = subject.strip()
        subject = subject[len('subject='):].strip() if subject.startswith('subject=') else subject
        text = self.openssl(['req', '-in', path, '-noout', '-text'], what='reading the CSR')
        pub = self.openssl(['req', '-in', path, '-noout', '-pubkey'], what='reading the CSR')
        return subject, parse_sans(text, 'CSR'), pub.strip()

    def key_pub(self, path):
        return self.openssl(['pkey', '-in', path, '-pubout'], what='reading the new key').strip()

    def pending_ok(self, csr, key, subject, sans):
        """A pending CSR is reused when it verifies, matches its key, and still names the same."""
        try:
            got_subject, got_sans, pub = self.csr_info(csr)
            return got_subject == subject and got_sans == sans and pub == self.key_pub(key)
        except CertError:
            return False

    def make(self, cert, base):
        """The new key and CSR, into base.key / base.csr (through .tmp files, kept only once checked)."""
        rdns = parse_rfc2253(cert['subject'])
        if not rdns:
            raise CertError('it has no subject: make this CSR by hand')
        conf = ['[req]', 'prompt = no', 'utf8 = yes', 'string_mask = utf8only', 'distinguished_name = dn']
        if cert['sans']:
            conf.append('req_extensions = ext')
        conf += ['', '[dn]'] + dn_lines(rdns)
        if cert['sans']:
            conf += ['', '[ext]', 'subjectAltName = @alt', '', '[alt]']
            count = {}
            for entry in cert['sans']:
                kind, _, value = entry.partition(':')
                count[kind] = count.get(kind, 0) + 1
                conf.append('%s.%d = %s' % (kind, count[kind], conf_escape(value)))
        tmp = {ext: '%s.%s.tmp' % (base, ext) for ext in ('cnf', 'key', 'csr')}
        old_umask = os.umask(0o077)
        try:
            with open(tmp['cnf'], 'w') as fh:
                fh.write('\n'.join(conf) + '\n')
            if cert['key'][0] == 'ec':
                self.openssl(['genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:%s' % cert['key'][1],
                              '-pkeyopt', 'ec_param_enc:named_curve', '-out', tmp['key']], what='making the key')
            else:
                self.openssl(['genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:%d' % cert['key'][1],
                              '-out', tmp['key']], what='making the key')
            os.chmod(tmp['key'], 0o600)
            self.openssl(['req', '-new', '-config', tmp['cnf'], '-key', tmp['key'], '-out', tmp['csr'],
                          '-' + self.p['digest']], what='making the CSR')
            subject, sans, pub = self.csr_info(tmp['csr'])
            if subject != cert['subject']:
                raise CertError('the CSR came out with a different subject (%s, not %s): make this CSR by hand' % (subject, cert['subject']))
            if sans != cert['sans']:
                raise CertError('the CSR came out with different names (%s, not %s): make this CSR by hand' % (', '.join(sans), ', '.join(cert['sans'])))
            if pub != self.key_pub(tmp['key']):
                raise CertError('the CSR does not match its new key')
            os.rename(tmp['key'], base + '.key')
            os.rename(tmp['csr'], base + '.csr')
        finally:
            os.umask(old_umask)
            for path in tmp.values():
                if os.path.exists(path):
                    os.remove(path)

    def leaf_from_file(self, cand):
        """The certificate in the file that the check reported (same subject and expiry)."""
        path = cand['where']
        try:
            with open(path) as fh:
                blocks = PEM_CERT.findall(fh.read())
        except (IOError, OSError) as e:
            raise CertError('the file cannot be read now (%s)' % e)
        want = cand.get('subject', '')
        found = []
        for pem in blocks:
            info = self.cert_info(pem + '\n')
            if info['subject'].replace('|', ' ') == want:
                found.append((info, pem))
        if not found:
            raise CertGone('the certificate is no longer in the file (replaced since the check)')
        same_end = [f for f in found if f[0]['end'] == int(cand.get('end') or 0)]
        info, pem = sorted(same_end or found, key=lambda f: f[0]['end'])[0]
        info['in_chain'] = len(blocks) > 1
        return info, pem

    def main(self):
        p = self.p
        certs = {}            # fingerprint -> entry
        order = []
        others = []           # TLS ports and keystores: matched to a file below, or listed
        results = []

        def skipped(cand, reason, status='skipped'):
            results.append({'wheres': [cand['where']], 'ids': [cand.get('id', '')], 'subject': cand.get('subject', ''),
                            'issuer': '', 'expires': cand.get('expires', ''), 'days': cand.get('days'),
                            'severity': cand.get('severity', 'warning'), 'status': status, 'reason': reason})

        for cand in p['certs']:
            if cand.get('kind') != 'file':
                others.append(cand)
                continue
            if p['exclude'] and re.search(p['exclude'], cand['where']):
                skipped(cand, 'its path is in cert_renew_exclude (managed by something else, e.g. Satellite or subscription-manager)')
                continue
            if p['only'] and not re.search(p['only'], cand['where']):
                skipped(cand, 'its path is not in cert_renew_only')
                continue
            try:
                info = self.leaf_from_file(cand)[0]
            except CertGone as e:
                skipped(cand, str(e))
                continue
            except CertError as e:
                skipped(cand, str(e), 'failed')
                continue
            fp = info['fingerprint']
            if fp in certs:
                certs[fp]['wheres'].append(cand['where'])
                certs[fp]['ids'].append(cand.get('id', ''))
                continue
            entry = {'wheres': [cand['where']], 'ids': [cand.get('id', '')], 'subject': info['subject'],
                     'issuer': info['issuer'], 'serial': info['serial'], 'end': info['end'],
                     'expires': cand.get('expires', ''), 'days': cand.get('days'), 'severity': cand.get('severity', 'warning'),
                     'status': '', 'reason': '', 'fingerprint': fp}
            certs[fp] = entry
            order.append(fp)
            self_signed = info['subject'] == info['issuer']
            if p['placeholder'] and re.search(p['placeholder'], info['subject']):
                entry.update(status='skipped', reason='a placeholder certificate a package made at install (postfix makes one; its subject matches'
                                                      ' check_certs_placeholder_subject): a PKI issues nothing for that subject - no CSR')
            elif info['ca'] and (info['in_chain'] or not self_signed):
                entry.update(status='skipped', reason='a CA certificate (part of a chain): replace it with the CA\'s new certificate from the PKI - there is no CSR for it')
            elif self_signed and info['cert_sign']:
                entry.update(status='skipped', reason='a CA\'s own certificate (a root: it signs certificates) - there is no CSR for it')
            elif self_signed and not p['self_signed']:
                entry.update(status='skipped', reason='self-signed (made on the host - by its application or a person - not by a PKI): no CSR'
                                                      ' (cert_renew_self_signed: true makes one, for the PKI to issue a real certificate in its place)')
            elif p['exclude_issuer'] and re.search(p['exclude_issuer'], info['issuer']):
                entry.update(status='skipped', reason='issued by %s - in cert_renew_exclude_issuer (renewed by its own tool, e.g. Satellite)' % info['issuer'])
            elif p['issuer'] and not self_signed and not re.search(p['issuer'], info['issuer']):
                entry.update(status='skipped', reason='issued by %s - not cert_renew_issuer' % info['issuer'])
            else:
                try:
                    entry['sans'] = parse_sans(info['text'])
                    entry['key'] = key_of(info['text'])
                    if entry['key'][0] != 'ec':
                        entry['key'] = ('rsa', max(p['key_bits'], entry['key'][1] if entry['key'][0] == 'rsa' else 0))
                    if self_signed:
                        entry['self_signed'] = True
                        if not host_names(entry['subject'], entry['sans']):
                            entry.update(status='skipped', reason='self-signed, and only for localhost: a PKI issues no certificate for it')
                except CertError as e:
                    entry.update(status='failed', reason=str(e))

        # a TLS port / keystore entry of a certificate that is also a file: the file's CSR covers it
        for cand in others:
            match = [certs[fp] for fp in order if certs[fp]['subject'].replace('|', ' ') == cand.get('subject')
                     and certs[fp]['end'] == int(cand.get('end') or 0)]
            if match:
                match[0]['wheres'].append(cand['where'])
                match[0]['ids'].append(cand.get('id', ''))
            elif cand.get('kind') == 'tls':
                skipped(cand, 'served on a port, and no certificate file with it was found: add its file to check_certs_files to renew it here')
            else:
                skipped(cand, 'in a keystore (Java or PKCS12): make its CSR with the keystore\'s own tool (keytool -certreq) - not done here')

        made = 0
        for fp in order:
            entry = certs[fp]
            if entry['status']:
                results.append(entry)
                continue
            try:
                cn = [v for t, v, _ in parse_rfc2253(entry['subject']) if t == 'CN']
            except CertError:
                cn = []           # make() says why
            short = safe_name(cn[0] if cn else entry['subject'])
            name = '%s-%s' % (short, fp[:8])      # on the host: one per old certificate, so a run again finds it
            base = os.path.join(p['dir'], name)
            ext = p['file_ext'].lstrip('.')
            entry.update(key_path=base + '.key', csr_path=base + '.csr', file_name='%s.%s' % (short, ext),
                         key_text='%s %s' % (('RSA', '%d-bit' % entry['key'][1]) if entry['key'][0] == 'rsa' else ('EC', entry['key'][1])))
            try:
                if os.path.exists(base + '.csr') and os.path.exists(base + '.key') and self.pending_ok(base + '.csr', base + '.key', entry['subject'], entry['sans']):
                    entry['status'] = 'exists'
                elif made >= p['max']:
                    entry.update(status='skipped', reason='more than cert_renew_max (%d) new CSRs in this run: the next run makes it' % p['max'])
                elif self.m.check_mode:
                    entry['status'] = 'would make'
                    made += 1
                else:
                    if not os.path.isdir(p['dir']):
                        os.makedirs(p['dir'], 0o700)
                        os.chmod(p['dir'], 0o700)
                    self.make(entry, base)
                    entry['status'] = 'made'
                    made += 1
                    with open(base + '.json', 'w') as fh:
                        json.dump({'certificate': entry['subject'], 'issuer': entry['issuer'], 'serial': entry['serial'],
                                   'expires': entry['expires'], 'found_in': entry['wheres'], 'names': entry['sans'],
                                   'key': entry['key_text'], 'made': time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime()),
                                   'aap_job': p['job']}, fh, indent=2)
            except (CertError, IOError, OSError) as e:
                entry.update(status='failed', reason=str(e))
            if entry['status'] in ('made', 'exists'):
                with open(base + '.csr') as fh:
                    entry['csr'] = fh.read()
                entry['csr_sha256'] = hashlib.sha256(entry['csr'].encode('utf-8')).hexdigest()
                # named after the CSR itself: a CSR made again (its key was lost ...) is a new attachment
                entry['file_name'] = '%s-%s.%s' % (short, entry['csr_sha256'][:8], ext)
            results.append(entry)

        for entry in results:
            entry.pop('end', None)
            if 'key' in entry:
                entry['key'] = entry.pop('key_text', '')
        changed = any(e['status'] == 'made' for e in results)
        failed = [e for e in results if e['status'] == 'failed']
        self.m.exit_json(changed=changed, certs=results, failed_count=len(failed))


def main():
    module = AnsibleModule(
        argument_spec=dict(
            certs=dict(type='list', elements='dict', required=True),
            dir=dict(type='path', required=True),
            key_bits=dict(type='int', default=2048),
            digest=dict(type='str', default='sha256'),
            only=dict(type='str', default=''),
            issuer=dict(type='str', default=''),
            exclude=dict(type='str', default=''),
            exclude_issuer=dict(type='str', default=''),
            self_signed=dict(type='bool', default=False),
            placeholder=dict(type='str', default=''),
            max=dict(type='int', default=10),
            file_ext=dict(type='str', default='csr'),
            job=dict(type='str', default=''),
        ),
        supports_check_mode=True,
    )
    if module.params['key_bits'] < 2048:
        module.fail_json(msg='cert_renew_key_bits is %d: at least 2048' % module.params['key_bits'])
    if not re.match(r'^sha(256|384|512)$', module.params['digest']):
        module.fail_json(msg='cert_renew_digest is %s: sha256, sha384 or sha512' % module.params['digest'])
    Renewer(module).main()


if __name__ == '__main__':
    main()
