#!/usr/bin/python
# -*- coding: utf-8 -*-
"""The certificates the PKI issued for the renewal's CSRs, found on the catalog requests that carry them
(cert_renew_servicenow: catalog). Runs on the AAP side (the execution environment) - the hosts never get
the ServiceNow credential. A certificate counts only when its public key is the CSR's: whatever else is
attached to a request is never taken for it."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_cert_issued
short_description: Find the issued certificate of each pending CSR on its ServiceNow catalog request
description:
  - For each pending CSR (made by the certificate renewal on a Linux or Windows host), finds the catalog
    request (RITM) that carries it - by the CSR's name in the order, as the renewal does - and reads what
    the PKI put there - the files attached to the RITM, to its attachment questions or to its REQ, and the
    certificates pasted into its work notes or comments.
  - A file may hold one certificate or several, as text (PEM) or binary (DER), a PKCS7 bundle (.p7b) or a
    zip of them. The certificate whose public key is the CSR's is the issued one; the CA certificates with
    it are its chain. A certificate for another key is listed, never used.
  - Says what still stands in the way of installing it (C(problems)) - not valid yet, expired or valid
    for too few days, names the old certificate had and this one lacks. The hosts check the rest.
  - Only reads ServiceNow: also in check mode.
options:
  url: {description: The instance (https://...)., type: str, required: true}
  username: {description: The API account., type: str, required: true}
  password: {description: Its password., type: str, required: true, no_log: true}
  validate_certs: {description: Check the instance's certificate., type: bool, default: true}
  ca_path: {description: A CA bundle for the instance's certificate., type: path}
  timeout: {description: Seconds per request., type: int, default: 30}
  pending:
    description: >-
      The CSRs waiting for their certificate - key (any unique text), host, stem (the CSR's name in the order
      without the date and the extension), expires (the old certificate's expiry), csr (the PEM text), names
      (the old certificate's subjectAltName, as the renewal wrote them).
    type: list
    elements: dict
    required: true
  reorder_states: {description: "A request in one of these states (Closed Incomplete, Closed Skipped) holds no certificate to install.", type: list, elements: str, default: ['4', '7']}
  exts: {description: The attachments read (their extension)., type: list, elements: str, default: [cer, crt, pem, der, p7b, p7c, p7, cert, zip, txt]}
  max_bytes: {description: A bigger attachment is not read., type: int, default: 1048576}
  journal: {description: Also look for certificates pasted into the request's work notes and comments., type: bool, default: true}
  min_days: {description: The issued certificate must be valid at least this many more days., type: int, default: 30}
  fewer_names: {description: "Install a certificate that lacks names the old one had (false: held for a person).", type: bool, default: false}
author: site automation
'''

RETURN = r'''
issued:
  description: >-
    One entry per pending CSR - key, host, stem, status (issued, waiting, not ordered, closed, failed), reason,
    ritm, ritm_sys_id, ritm_state, request_sys_id, read (where it looked), others (certificates there for
    another key), and when issued - cert (pem, subject, issuer, serial, sha1, sha256, not_before, not_after,
    days, names, key, ca), chain (pem, subject, issuer, sha1, self_signed - in order from the issuer up),
    source (the file or note it came from), problems (why it must wait for a person; empty = it may be installed).
  type: list
  returned: always
'''

import base64
import datetime
import hashlib
import io
import ipaddress
import json
import re
import warnings
import zipfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.urls import open_url

try:
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import ec, rsa
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, pkcs7
    HAS_CRYPTO = True
except ImportError:          # the module says so (ansible-core itself needs cryptography: every EE has it)
    HAS_CRYPTO = False

PEM_BLOCK = re.compile(r'-----BEGIN (CERTIFICATE|PKCS7|X509 CERTIFICATE|TRUSTED CERTIFICATE)-----\s+([A-Za-z0-9+/=\s]+?)-----END \1-----')
B64_ONLY = re.compile(r'^[A-Za-z0-9+/=\s]+$')
RITM_STATES = {"-5": "Pending", "1": "Open", "2": "Work in Progress", "3": "Closed Complete", "4": "Closed Incomplete",
               "7": "Closed Skipped"}


class SnError(Exception):
    pass


def norm_name(name):
    """A subjectAltName as a renewal wrote it - Linux 'DNS:a' / 'IP:192.0.2.1'; Windows the value alone, 'a' /
    '192.0.2.1' (its certreq form 'dns=a' too) - or as read here, in one form to compare: 'dns:a', 'ip:192.0.2.1'
    (an address in its usual form, IPv6 shortened), 'email:x', 'uri:x'."""
    s = str(name or '').strip()
    m = re.match(r'^(DNS|IP Address|IP|ipaddress|email|URI|url|dns)\s*[:=]\s*(.*)$', s, re.I)
    if not m:
        # the value alone: its kind from its form
        try:
            return 'ip:' + str(ipaddress.ip_address(s))
        except ValueError:
            pass
        if '://' in s:
            return 'uri:' + s
        if '@' in s:
            return 'email:' + s
        return 'dns:' + s.lower().rstrip('.')
    kind, value = m.group(1).strip().lower(), m.group(2).strip()
    kind = {'ip address': 'ip', 'ipaddress': 'ip', 'url': 'uri'}.get(kind, kind)
    if kind == 'ip':
        try:
            value = str(ipaddress.ip_address(value))
        except ValueError:
            value = value.lower()
    elif kind == 'dns':
        value = value.lower().rstrip('.')
    return '%s:%s' % (kind, value)


def _utc(cert, which):
    v = getattr(cert, which + '_utc', None)       # cryptography 42 and later
    if v is None:
        v = getattr(cert, which).replace(tzinfo=None)
    return v.replace(tzinfo=None)


def cert_names(cert):
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return []
    out = []
    for g in ext:
        if isinstance(g, x509.DNSName):
            out.append('dns:' + g.value.lower().rstrip('.'))
        elif isinstance(g, x509.IPAddress):
            out.append('ip:' + str(g.value))
        elif isinstance(g, x509.RFC822Name):
            out.append('email:' + g.value)
        elif isinstance(g, x509.UniformResourceIdentifier):
            out.append('uri:' + g.value)
    return out


def is_ca(cert):
    try:
        return bool(cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
    except x509.ExtensionNotFound:
        return False


def spki(obj):
    """The public key, written the one way (SubjectPublicKeyInfo, DER): the same key always gives the same bytes."""
    return obj.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


def key_text(cert):
    k = cert.public_key()
    if isinstance(k, rsa.RSAPublicKey):
        return 'RSA %d-bit' % k.key_size
    if isinstance(k, ec.EllipticCurvePublicKey):
        return 'EC %s' % k.curve.name
    return type(k).__name__


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def describe(cert, now=None):
    """What the hosts and the report need of a certificate."""
    now = now or utcnow()
    nb, na = _utc(cert, 'not_valid_before'), _utc(cert, 'not_valid_after')
    der = cert.public_bytes(Encoding.DER)
    return {'pem': cert.public_bytes(Encoding.PEM).decode('ascii'),
            'subject': cert.subject.rfc4514_string(), 'issuer': cert.issuer.rfc4514_string(),
            'serial': '%X' % cert.serial_number,
            'sha1': hashlib.sha1(der).hexdigest().upper(), 'sha256': hashlib.sha256(der).hexdigest(),
            'not_before': nb.strftime('%Y-%m-%d %H:%M UTC'), 'not_after': na.strftime('%Y-%m-%d %H:%M UTC'),
            'not_before_epoch': int((nb - datetime.datetime(1970, 1, 1)).total_seconds()),
            'not_after_epoch': int((na - datetime.datetime(1970, 1, 1)).total_seconds()),
            'days': int((na - now).total_seconds() // 86400), 'names': cert_names(cert), 'key': key_text(cert),
            'ca': is_ca(cert), 'self_signed': cert.subject == cert.issuer}


def certs_in(data, depth=0):
    """Every certificate in a file's bytes: PEM blocks (certificates, PKCS7), else binary DER - a certificate or
    a PKCS7 bundle - or text that is only base64 (a .cer without its BEGIN line), or a zip of such files."""
    if depth > 1:
        return []
    if data[:4] == b'PK\x03\x04':
        out = []
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist()[:50]:
                    if not info.is_dir() and info.file_size <= 1048576:
                        out.extend(certs_in(z.read(info), depth + 1))
        except (zipfile.BadZipfile, RuntimeError, ValueError):     # encrypted, broken
            return []
        return out
    text = data.decode('utf-8', 'replace')
    blocks = PEM_BLOCK.findall(text)
    if blocks:
        out = []
        for kind, body in blocks:
            der = _b64(body)
            if der is not None:
                out.extend(_der_certs(der, kind == 'PKCS7'))
        return out
    stripped = text.strip()
    if stripped and B64_ONLY.match(stripped) and len(stripped) > 100:
        der = _b64(stripped)
        if der is not None:
            got = _der_certs(der)
            if got:
                return got
    return _der_certs(data)


def _b64(body):
    try:
        return base64.b64decode(re.sub(r'\s+', '', body), validate=True)
    except (ValueError, TypeError):
        return None


def _der_certs(der, p7_first=False):
    loaders = [lambda b: [x509.load_der_x509_certificate(b)], pkcs7.load_der_pkcs7_certificates]
    for load in (reversed(loaders) if p7_first else loaders):
        try:
            with warnings.catch_warnings():       # a .p7b in BER, as openssl and many PKIs write it: read all the same
                warnings.simplefilter('ignore')
                return list(load(der))
        except Exception:      # not this kind - cryptography raises ValueError or its own errors
            continue
    return []


def chain_for(leaf, others):
    """The CA certificates that make the leaf's chain, from its issuer up (by subject and issuer names)."""
    out, cur, seen = [], leaf, set()
    while cur.subject != cur.issuer:
        nxt = next((c for c in others if c.subject == cur.issuer and is_ca(c) and spki(c) not in seen), None)
        if nxt is None:
            break
        seen.add(spki(nxt))
        out.append(nxt)
        cur = nxt
    return out


def journal_pem(text):
    """Certificates pasted into a work note or comment (the PEM blocks in the text)."""
    out = []
    for kind, body in PEM_BLOCK.findall(text or ''):
        der = _b64(body)
        if der is not None:
            out.extend(_der_certs(der, kind == 'PKCS7'))
    return out


def problems_of(desc, old_names, min_days, fewer_names, now_epoch):
    out = []
    if desc['not_before_epoch'] > now_epoch + 3600:
        out.append('not valid yet: from %s' % desc['not_before'])
    if desc['days'] < 0:
        out.append('expired on %s' % desc['not_after'])
    elif desc['days'] < min_days:
        out.append('valid only %d more days, until %s (cert_install_min_days: %d)' % (desc['days'], desc['not_after'], min_days))
    want = [norm_name(n) for n in old_names or []]
    lacks = [n for n in want if n not in desc['names']]
    if lacks and not fewer_names:
        out.append('the old certificate had names this one lacks: %s (cert_install_fewer_names: true installs it anyway)'
                   % ', '.join(lacks))
    return out


class Sn(object):
    def __init__(self, m):
        self.m = m
        p = m.params
        self.base = p['url'].rstrip('/')
        self.opts = dict(url_username=p['username'], url_password=p['password'], force_basic_auth=True,
                         validate_certs=p['validate_certs'], timeout=p['timeout'])
        if p['ca_path']:
            self.opts['ca_path'] = p['ca_path']

    def get(self, path, query=None, raw=False):
        url = self.base + path + (('?' + urlencode(query)) if query else '')
        try:
            r = open_url(url, method='GET', headers={'Accept': '*/*' if raw else 'application/json'}, **self.opts)
            body = r.read()
        except HTTPError as e:
            why = ''
            try:
                err = json.loads(e.read() or b'{}').get('error') or {}
                why = ': ' + ' - '.join(x for x in (err.get('message'), err.get('detail')) if x)
            except Exception:
                pass
            raise SnError('HTTP %s from %s%s' % (e.code, path, why))
        except (URLError, IOError, OSError) as e:
            raise SnError('%s: %s' % (path, getattr(e, 'reason', e)))
        if raw:
            return body
        try:
            return json.loads(body or b'{}').get('result')
        except ValueError:
            raise SnError('%s did not answer JSON (a login page? check the instance address)' % path)


def catalog_query(stem, expires):
    """The same query the renewal uses to find its order (plugins/filter/cert_order_filters.py cert_catalog_query)."""
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', str(expires or ''))
    q = 'file_nameSTARTSWITH%s' % stem
    if m:
        q += '^file_nameLIKE-exp%s.' % ''.join(m.groups())
    return q + '^table_nameINsc_req_item,ZZ_YYsc_req_item'


def find_one(sn, p, item, now):
    out = {'key': item.get('key', ''), 'host': item.get('host', ''), 'stem': item.get('stem', ''), 'status': '', 'reason': '',
           'ritm': '', 'ritm_sys_id': '', 'ritm_state': '', 'request_sys_id': '', 'read': [], 'others': [], 'problems': []}
    csr_text = str(item.get('csr') or '').replace('NEW CERTIFICATE REQUEST', 'CERTIFICATE REQUEST')
    try:
        want = spki(x509.load_pem_x509_csr(csr_text.encode('ascii', 'replace')))
    except Exception as e:
        out.update(status='failed', reason='the CSR on the host cannot be read (%s)' % e)
        return out
    found = sn.get('/api/now/attachment', {'sysparm_query': catalog_query(item['stem'], item.get('expires')),
                                           'sysparm_fields': 'sys_id,file_name,table_name,table_sys_id', 'sysparm_limit': '50'}) or []
    ids = []
    for a in found:
        if a.get('table_sys_id') and a['table_sys_id'] not in ids:
            ids.append(a['table_sys_id'])
    ritms = []
    for sid in ids:
        try:
            r = sn.get('/api/now/table/sc_req_item/%s' % quote(sid), {'sysparm_fields': 'sys_id,number,state,request'})
        except SnError:
            continue          # an attachment question's file, uploaded for an order that was never made
        if r:
            req = r.get('request')
            r['request'] = (req.get('value') if isinstance(req, dict) else req) or ''
            ritms.append(r)
    if not ritms:
        out.update(status='not ordered', reason='no catalog request carries this CSR (%s): ordered by hand, or with an incident?' % item['stem'])
        return out
    live = [r for r in ritms if str(r.get('state')) not in p['reorder_states']]
    if not live:
        r = sorted(ritms, key=lambda x: x.get('number', ''))[-1]
        out.update(status='closed', ritm=r.get('number', ''), ritm_sys_id=r.get('sys_id', ''),
                   ritm_state=RITM_STATES.get(str(r.get('state')), str(r.get('state'))),
                   reason='its request %s was closed %s: the renewal orders it again' % (r.get('number'), RITM_STATES.get(str(r.get('state')), r.get('state'))))
        return out
    stem = item['stem'].lower()
    exts = set(e.lower().lstrip('.') for e in p['exts'])
    for r in sorted(live, key=lambda x: x.get('number', ''), reverse=True):
        out.update(ritm=r.get('number', ''), ritm_sys_id=r.get('sys_id', ''), request_sys_id=r.get('request', ''),
                   ritm_state=RITM_STATES.get(str(r.get('state')), str(r.get('state'))))
        on = [x for x in (r.get('sys_id'), r.get('request')) if x]
        files = sn.get('/api/now/attachment', {'sysparm_query': 'table_sys_idIN' + ','.join(on),
                                               'sysparm_fields': 'sys_id,file_name,table_name,table_sys_id,size_bytes',
                                               'sysparm_limit': '100'}) or []
        cands = []        # (where it came from, [certificates])
        for a in files:
            name = str(a.get('file_name') or '')
            ext = name.rpartition('.')[2].lower() if '.' in name else ''
            if name.lower().startswith(stem) or ext == 'csr' or ext not in exts:
                continue
            try:
                size = int(a.get('size_bytes') or 0)
            except ValueError:
                size = 0
            if size > p['max_bytes']:
                out['read'].append('%s (not read: %d bytes, over cert_install_max_bytes)' % (name, size))
                continue
            data = sn.get('/api/now/attachment/%s/file' % quote(a['sys_id']), raw=True)
            got = certs_in(data)
            out['read'].append('%s (%s)' % (name, ('%d certificate(s)' % len(got)) if got else 'no certificate in it'))
            cands.append((name, got))
        if p['journal']:
            try:
                j = sn.get('/api/now/table/sc_req_item/%s' % quote(r['sys_id']),
                           {'sysparm_fields': 'work_notes,comments', 'sysparm_display_value': 'true'}) or {}
                for field in ('work_notes', 'comments'):
                    got = journal_pem(j.get(field) if isinstance(j.get(field), str) else '')
                    if got:
                        out['read'].append('the %s (%d certificate(s) pasted)' % (field.replace('_', ' '), len(got)))
                        cands.append(('its ' + field.replace('_', ' '), got))
            except SnError:
                pass              # an account that cannot read the journal: the attachments still count
        every = [c for _, got in cands for c in got]
        leaf = None
        for where, got in cands:
            for c in got:
                if spki(c) == want and not is_ca(c):
                    if leaf is None:
                        leaf, source = c, where
                elif not is_ca(c):
                    d = describe(c, now)
                    note = '%s (%s, serial %s): not for this key' % (d['subject'], where, d['serial'])
                    if note not in out['others']:
                        out['others'].append(note)
        if leaf is not None:
            desc = describe(leaf, now)
            out.update(status='issued', cert=desc, source=source,
                       chain=[describe(c, now) for c in chain_for(leaf, [c for c in every if is_ca(c)])])
            now_epoch = int((now - datetime.datetime(1970, 1, 1)).total_seconds())
            out['problems'] = problems_of(desc, item.get('names'), p['min_days'], p['fewer_names'], now_epoch)
            return out
    out.update(status='waiting', reason='%s (%s): no certificate for this key on it yet%s' % (
        out['ritm'], out['ritm_state'], ('; ' + '; '.join(out['others'])) if out['others'] else ''))
    return out


def main():
    m = AnsibleModule(argument_spec=dict(
        url=dict(type='str', required=True), username=dict(type='str', required=True),
        password=dict(type='str', required=True, no_log=True), validate_certs=dict(type='bool', default=True),
        ca_path=dict(type='path'), timeout=dict(type='int', default=30),
        pending=dict(type='list', elements='dict', required=True),
        reorder_states=dict(type='list', elements='str', default=['4', '7']),
        exts=dict(type='list', elements='str', default=['cer', 'crt', 'pem', 'der', 'p7b', 'p7c', 'p7', 'cert', 'zip', 'txt']),
        max_bytes=dict(type='int', default=1048576), journal=dict(type='bool', default=True),
        min_days=dict(type='int', default=30), fewer_names=dict(type='bool', default=False)),
        supports_check_mode=True)
    if not HAS_CRYPTO:
        m.fail_json(msg='the Python library cryptography is not in this execution environment (ansible-core needs it: use an EE built on ansible-core)')
    sn = Sn(m)
    now = utcnow()
    out = []
    for item in m.params['pending']:
        try:
            out.append(find_one(sn, m.params, item, now))
        except SnError as e:
            out.append({'key': item.get('key', ''), 'host': item.get('host', ''), 'stem': item.get('stem', ''),
                        'status': 'failed', 'reason': 'ServiceNow: %s' % e, 'read': [], 'others': [], 'problems': []})
    m.exit_json(changed=False, issued=out)


if __name__ == '__main__':
    main()
