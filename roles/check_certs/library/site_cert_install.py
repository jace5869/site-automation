#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Install the certificate the PKI issued for a renewal's CSR, where the old one is - on the host, with
the openssl command (every RHEL, OpenSSL 1.0.2 to 3.x, FIPS mode), no Python library. Before anything is
replaced it is copied aside; after, the service is reloaded and the port asked which certificate it serves;
if anything is wrong, the copies go back. Runs on the managed host (Python 3.6 and later)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_cert_install
short_description: Install issued certificates in place of the old ones - planned, approved, checked, undone
description:
  - C(action=pending) lists the renewals waiting for their certificate in O(dir) (each key, CSR and info file
    the certificate renewal made), with the CSR - what the AAP side needs to find the issued certificate.
  - C(action=plan) says, for each issued certificate (O(issued)), what installing it would do - the files it
    replaces (the certificate, with the same layout - text or binary, with its chain, with its key - and the
    old key's file), the service it reloads (systemd, a reload only), the port that must then serve it - or
    why it waits for a person. Reads only.
  - C(action=install) installs the approved ones (O(approved), the plan as approved), each planned again
    first and refused when anything changed since. Each file is copied aside first (O(dir)/backup), the
    new ones written with the old ones' owner, mode and SELinux label, the service's configuration tested and
    the service reloaded, then the port must serve the new certificate. If one step fails, the copies go
    back and the service is reloaded again. Done, the renewal's files move to O(dir)/installed.
  - C(action=restore) puts back a backup (O(backup), or the newest of O(only)) - the old certificate - and
    reloads the service; the renewal is pending again.
  - Never automatic, with the reason (by hand) - a keystore (its program needs a restart), a port whose
    file is unknown, a service that cannot reload (a restart). Held for a person - the certificate not for
    the key on this host, a chain that does not build, the file replaced since the CSR, the old key not found
    or found in several files, its key with a password, no service found that uses it.
options:
  action: {description: What to do., type: str, choices: [pending, plan, install, restore], default: pending}
  dir: {description: The renewal's folder (cert_renew_dir)., type: path, required: true}
  issued: {description: "plan - the issued certificates, as site_cert_issued found them, each with its renewal's base name (base).", type: list, elements: dict, default: []}
  approved: {description: install - the plan's items that were approved., type: list, elements: dict, default: []}
  services: {description: "A certificate file -> the systemd service that uses it, for files no configuration names: [{path: glob, service: unit}].", type: list, elements: dict, default: []}
  never_reload: {description: Services (globs) never reloaded by the job - their certificates are installed by a person., type: list, elements: str, default: []}
  key_files: {description: "A certificate file -> its key's file, when it cannot be found: {cert path: key path}.", type: dict, default: {}}
  key_dirs: {description: Folders searched for the old key when no configuration names it., type: list, elements: path, default: []}
  known: {description: "The services whose configuration names their certificate files: [{dir, service, test}] (test - the command that checks the configuration).", type: list, elements: dict, default: []}
  trust_check: {description: The chain must build up to a root (the host's trust store or the one the PKI sent)., type: bool, default: true}
  verify_wait: {description: Seconds the port has, after the reload, to serve the new certificate., type: int, default: 15}
  backup: {description: restore - the backup's folder name (in O(dir)/backup)., type: str, default: ''}
  only: {description: "restore - the newest backup of the certificates whose name or subject match (a regular expression).", type: str, default: ''}
  job: {description: The AAP job, written in the backup's and the install's record., type: str, default: ''}
  install_notes: {description: "The start of a path -> what to do instead (cert_renew_install_notes): something else deploys that file, so a person installs it.", type: dict, default: {}}
author: site automation
'''

RETURN = r'''
pending: {description: "action=pending - base, stem, csr, expires, names, subject, found_in.", type: list, returned: always}
plan: {description: "action=plan - per certificate - base, status (ready, held, by hand, installed already, gone), reason, steps, files, units, verify, notes, new, cert_pem, chain_pem.", type: list, returned: always}
installed: {description: "action=install - per certificate - base, status (installed, rolled back, refused, failed, installed already), reason, backup, files, units, verified.", type: list, returned: always}
restored: {description: "action=restore - per backup - backup, base, status (restored, would restore, failed), reason, files, units.", type: list, returned: always}
backups: {description: "action=restore - every backup there is (name, base, subject, made, restored).", type: list, returned: always}
'''

import base64
import fnmatch
import glob
import hashlib
import json
import os
import re
import shutil
import time

from ansible.module_utils.basic import AnsibleModule

PEM_ANY = re.compile(r'-----BEGIN ([A-Z0-9 ]+)-----\s+[A-Za-z0-9+/=\s:,-]*?-----END \1-----\s*', re.S)
KEY_KINDS = ('PRIVATE KEY', 'RSA PRIVATE KEY', 'EC PRIVATE KEY', 'ENCRYPTED PRIVATE KEY', 'DSA PRIVATE KEY')
CERT_KINDS = ('CERTIFICATE', 'TRUSTED CERTIFICATE', 'X509 CERTIFICATE')
STARTTLS = {25: 'smtp', 587: 'smtp', 110: 'pop3', 143: 'imap', 21: 'ftp', 389: 'ldap', 5432: 'postgres'}
# where an old key usually is, besides the folder of its certificate and a private/ next to it
KEY_DIRS = ['/etc/pki/tls/private', '/etc/ssl/private', '/etc/pki/tls/certs', '/etc/httpd', '/etc/nginx', '/etc/haproxy',
            '/etc/postfix', '/etc/dovecot', '/etc/pki/nginx', '/etc/cockpit/ws-certs.d']
KNOWN = [{'dir': '/etc/httpd', 'service': 'httpd.service', 'test': ['apachectl', 'configtest']},
         {'dir': '/etc/apache2', 'service': 'apache2.service', 'test': ['apachectl', 'configtest']},
         {'dir': '/etc/nginx', 'service': 'nginx.service', 'test': ['nginx', '-t']},
         {'dir': '/etc/haproxy', 'service': 'haproxy.service', 'test': ['haproxy', '-c', '-f', '/etc/haproxy/haproxy.cfg']},
         {'dir': '/etc/postfix', 'service': 'postfix.service', 'test': ['postfix', 'check']},
         {'dir': '/etc/dovecot', 'service': 'dovecot.service', 'test': ['doveconf', '-n']},
         {'dir': '/etc/openldap', 'service': 'slapd.service', 'test': []},
         {'dir': '/etc/cockpit', 'service': 'cockpit.service', 'test': []}]


class Held(Exception):
    """Why a certificate waits for a person (the message)."""


class ByHand(Exception):
    """Why a certificate is installed by a person, never by the job (the message), and how (steps)."""
    def __init__(self, msg, steps=None):
        Exception.__init__(self, msg)
        self.steps = steps or []


def q(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


def blocks_of(text):
    """The PEM blocks of a file, in order: [(kind, the block's text)] - kind cert, key, or other (parameters)."""
    out = []
    for mt in PEM_ANY.finditer(text):
        kind = mt.group(1)
        out.append(('cert' if kind in CERT_KINDS else 'key' if kind in KEY_KINDS else 'other', mt.group(0).strip() + '\n'))
    return out


def der_of(pem):
    body = re.sub(r'-----[^-]+-----', '', pem)
    return base64.b64decode(re.sub(r'\s+', '', body))


def sha256_pem(pem):
    return hashlib.sha256(der_of(pem)).hexdigest()


def new_dir(parent, name):
    """parent/<date>.<ms>-name, made 0700: the names sort in the order they were made (another one in the same
    millisecond takes the next)."""
    if not os.path.isdir(parent):
        os.makedirs(parent, 0o700)
    os.chmod(parent, 0o700)
    t = time.time()
    ms = int((t - int(t)) * 1000)
    stamp = time.strftime('%Y%m%d-%H%M%S', time.gmtime(t))
    while True:
        d = os.path.join(parent, '%s.%03d-%s' % (stamp, ms % 1000, name))
        try:
            os.mkdir(d, 0o700)
            os.chmod(d, 0o700)
            return d
        except OSError:
            if not os.path.isdir(d) or ms >= 999:
                raise
            ms += 1


class Installer(object):
    def __init__(self, m):
        self.m = m
        self.p = m.params
        self.dir = self.p['dir']
        self.known = self.p['known'] or KNOWN
        self.key_dirs = self.p['key_dirs'] or KEY_DIRS
        self._ss = None

    # ---- commands -------------------------------------------------------------------------------
    def run(self, args, data=None):
        rc, out, err = self.m.run_command(args, data=data, binary_data=data is not None)
        return rc, out or '', err or ''

    def openssl(self, args, data=None):
        rc, out, err = self.run(['openssl'] + args, data=data)
        return out if rc == 0 else None

    def cert_pub(self, pem):
        return self.openssl(['x509', '-noout', '-pubkey'], data=pem)

    def key_pub(self, pem):
        """The public key of a private key (text) - None when openssl cannot read it (a password, not a key)."""
        return self.openssl(['pkey', '-pubout', '-passin', 'pass:'], data=pem)

    def names(self, pem):
        out = self.openssl(['x509', '-noout', '-subject', '-issuer', '-nameopt', 'RFC2253'], data=pem) or ''
        sub = re.search(r'^subject=\s*(.*)$', out, re.M)
        iss = re.search(r'^issuer=\s*(.*)$', out, re.M)
        return (sub.group(1).strip() if sub else ''), (iss.group(1).strip() if iss else '')

    # ---- the renewals waiting in the folder -------------------------------------------------------
    def pending(self):
        out = []
        for info_path in sorted(glob.glob(os.path.join(self.dir, '*.json'))):
            base = info_path[:-5]
            if not (os.path.isfile(base + '.csr') and os.path.isfile(base + '.key')):
                continue
            try:
                with open(info_path) as fh:
                    info = json.load(fh)
                with open(base + '.csr') as fh:
                    csr = fh.read()
            except (IOError, OSError, ValueError):
                continue
            name = os.path.basename(base)
            short = name.rsplit('-', 1)[0]
            # the CSR's name in the order: <common name>-<8 characters of the CSR's SHA-256> (site_cert_csr)
            out.append({'base': name, 'stem': '%s-%s' % (short, hashlib.sha256(csr.encode('utf-8')).hexdigest()[:8]),
                        'csr': csr, 'expires': info.get('expires', ''), 'names': info.get('names') or [],
                        'subject': info.get('certificate', ''), 'found_in': info.get('found_in') or [],
                        'serial': info.get('serial', ''), 'made': info.get('made', '')})
        return out

    def load(self, base):
        path = os.path.join(self.dir, os.path.basename(base))
        if not all(os.path.isfile(path + ext) for ext in ('.json', '.csr', '.key')):
            return None
        with open(path + '.json') as fh:
            info = json.load(fh)
        with open(path + '.key') as fh:
            key = fh.read()
        return {'path': path, 'info': info, 'key': key, 'old_fp8': os.path.basename(path).rsplit('-', 1)[-1].lower()}

    # ---- the plan ----------------------------------------------------------------------------------
    def plan_one(self, iss):
        base = os.path.basename(str(iss.get('base') or ''))
        cert = iss.get('cert') or {}
        item = {'base': base, 'subject': '', 'expires': '', 'ritm': iss.get('ritm', ''), 'ritm_sys_id': iss.get('ritm_sys_id', ''),
                'status': '', 'reason': '', 'steps': [], 'files': [], 'units': [], 'verify': [], 'notes': [],
                'new': {k: cert.get(k) for k in ('sha256', 'sha1', 'serial', 'subject', 'issuer', 'not_after', 'names', 'key')},
                'cert_pem': cert.get('pem', ''), 'chain_pem': [c.get('pem', '') for c in iss.get('chain') or []],
                'source': iss.get('source', '')}
        pend = self.load(base) if base else None
        if pend is None:
            item.update(status='gone', reason='no renewal %s waiting on this host (installed or removed since)' % base)
            return item
        info = pend['info']
        item.update(subject=info.get('certificate', ''), expires=info.get('expires', ''))
        wrong = ''
        try:
            self.check_cert(item, iss, pend)
        except Held as e:
            wrong = str(e)          # the certificate itself; where it goes is still said (a person may install it)
        try:
            done = self.targets(item, pend, info)
        except ByHand as e:
            item.update(status='by hand', reason=str(e) + ((' - and: ' + wrong) if wrong else ''), steps=e.steps,
                        files=[], units=[], verify=[])
            return item
        except Held as e:
            item.update(status='held', reason='; '.join(x for x in (wrong, str(e)) if x))
            return item
        if wrong:
            item.update(status='held', reason=wrong)
        elif done:
            item.update(status='installed already', reason='every file already holds the new certificate')
        else:
            item['status'] = 'ready'
        return item

    def check_cert(self, item, iss, pend):
        leaf = item['cert_pem']
        if not leaf or sha256_pem(leaf) != (item['new'].get('sha256') or ''):
            raise Held('the issued certificate did not come with this plan intact')
        if iss.get('problems'):
            raise Held('; '.join(iss['problems']))
        pub = self.cert_pub(leaf)
        if not pub or pub != self.key_pub(pend['key']):
            raise Held('the issued certificate is not for the key on this host (%s.key)' % pend['path'])
        if not self.p['trust_check']:
            return
        roots = [c for c in item['chain_pem'] if self.names(c)[0] == self.names(c)[1]]
        inter = [c for c in item['chain_pem'] if c not in roots]
        ok, why = self.verify_chain(leaf, inter, roots)
        if not ok:
            raise Held('its chain does not build up to a root: %s (the PKI\'s chain with it, or cert_install_trust_check: false)' % why)
        if roots:
            alone, _ = self.verify_chain(leaf, inter, [])
            if not alone:
                item['notes'].append('its root (%s) is not in this host\'s trust store - clients need it' % self.names(roots[0])[0])

    def verify_chain(self, leaf, inter, roots):
        tmp = os.path.join(self.dir, '.verify-%d' % os.getpid())
        os.makedirs(tmp, 0o700)
        try:
            args = ['verify', '-purpose', 'sslserver']
            if inter:
                with open(os.path.join(tmp, 'untrusted.pem'), 'w') as fh:
                    fh.write(''.join(inter))
                args += ['-untrusted', os.path.join(tmp, 'untrusted.pem')]
            if roots:
                bundle = self.system_bundle()
                with open(os.path.join(tmp, 'trusted.pem'), 'w') as fh:
                    if bundle:
                        with open(bundle) as b:
                            fh.write(b.read() + '\n')
                    fh.write(''.join(roots))
                args += ['-CAfile', os.path.join(tmp, 'trusted.pem')]
            with open(os.path.join(tmp, 'leaf.pem'), 'w') as fh:
                fh.write(leaf)
            rc, out, err = self.run(['openssl'] + args + [os.path.join(tmp, 'leaf.pem')])
            if rc == 0 and ': OK' in out:
                return True, ''
            why = [ln for ln in (out + '\n' + err).splitlines() if 'error' in ln.lower()]
            return False, (why[0] if why else (out + err).strip()[:200]).replace(os.path.join(tmp, 'leaf.pem'), 'the certificate')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def system_bundle(self):
        for f in ('/etc/pki/tls/certs/ca-bundle.crt', '/etc/ssl/certs/ca-certificates.crt', '/etc/pki/tls/cert.pem', '/etc/ssl/cert.pem'):
            if os.path.isfile(f):
                return f
        return ''

    def targets(self, item, pend, info):
        """Where the old certificate is: each file, its key, the services, the ports - True when every file holds
        the new one already (installed by hand)."""
        files, keystores, ports = [], [], []
        for w in info.get('found_in') or []:
            w = str(w)
            mt = re.search(r'\(https://(.+):(\d+)\)$', w)
            if mt:
                ports.append((mt.group(1).strip('[]'), int(mt.group(2))))
            elif ' (alias ' in w or re.search(r'[.](p12|pfx|jks|keystore)$', w, re.I):
                keystores.append(w)
            elif w.startswith('/'):
                files.append(w)
        if keystores:
            raise ByHand('in a keystore (%s): its program needs a restart to take it - a person installs it' % ', '.join(keystores),
                         self.keystore_steps(keystores[0], pend))
        for path in files:
            note = next((v for k, v in sorted((self.p['install_notes'] or {}).items(), key=lambda kv: -len(kv[0]))
                         if k and path.startswith(k)), None)
            if note:
                raise ByHand('%s is deployed by something else (cert_renew_install_notes): %s' % (path, ' '.join(str(note).split())))
        if not files:
            raise ByHand('only seen on a port (%s) - where its service keeps the certificate is not known: a person installs it; '
                         'list the file in check_certs_files so the next renewal names it'
                         % ', '.join('%s:%d' % p for p in ports))
        done = 0
        for path in files:
            f = self.file_plan(path, pend, item)
            if f is None:
                done += 1
                continue
            for x in f:
                if x['path'] not in [y['path'] for y in item['files']]:     # one key file for two certificate files
                    item['files'].append(x)
        if done == len(files):
            return True
        if done:
            item['notes'].append('%d of the files hold the new certificate already' % done)
        self.check_key_shared(item, pend)
        item['units'] = self.units_for(item, [f['path'] for f in item['files'] if f['role'] == 'cert'], ports)
        item['verify'] = self.verify_targets(item, ports, pend)
        if not item['verify']:
            item['notes'].append('no port found that serves it: once reloaded, only the files are checked')
        return False

    def keystore_steps(self, where, pend):
        mt = re.match(r'^(.*) \(alias (.*)\)$', where)
        ks, alias = (mt.group(1), mt.group(2)) if mt else (where, '')
        key = pend['path'] + '.key'
        if alias:
            return ['cp -p %s %s.bak' % (q(ks), q(ks)),
                    'openssl pkcs12 -export -inkey %s -in <the issued certificate> -certfile <its chain> -name %s -out /root/new.p12'
                    % (q(key), q(alias)),
                    'keytool -importkeystore -srckeystore /root/new.p12 -srcstoretype PKCS12 -srcalias %s -destalias %s -destkeystore %s'
                    ' (answer yes to overwrite the alias)' % (q(alias), q(alias), q(ks)),
                    'restart the program that uses it; once it works, delete /root/new.p12 and %s.bak' % ks]
        return ['openssl pkcs12 -export -inkey %s -in <the issued certificate> -certfile <its chain> -out %s.new' % (q(key), q(ks)),
                'mv %s %s.bak ; mv %s.new %s - then restart the program that uses it' % (q(ks), q(ks), q(ks), q(ks))]

    def file_plan(self, path, pend, item):
        """One certificate file: [its plan, and its key's when that is another file] - None when it holds the new
        certificate already."""
        try:
            with open(path, 'rb') as fh:
                raw = fh.read()
        except (IOError, OSError) as e:
            raise Held('%s cannot be read now (%s)' % (path, e))
        text = raw.decode('utf-8', 'replace')
        blocks = blocks_of(text)
        fmt = 'pem'
        if not blocks:
            pem = self.openssl(['x509', '-inform', 'DER'], data=raw)
            if not pem:
                raise Held('%s holds no certificate now' % path)
            blocks, fmt = [('cert', pem)], 'der'
        certs = [b for k, b in blocks if k == 'cert']
        fps = [sha256_pem(c) for c in certs]
        if item['new']['sha256'] in fps:
            return None
        olds = [i for i, f in enumerate(fps) if f[:8] == pend['old_fp8']]
        if not olds:
            raise Held('%s no longer holds the certificate the CSR was made for (replaced since): nothing replaced' % path)
        old_pem = certs[olds[0]]
        old_pub = self.cert_pub(old_pem)
        layout, chain = [], 0
        for k, b in blocks:
            if k == 'cert':
                if certs.index(b) == olds[0]:
                    layout.append('leaf')
                else:
                    layout.append('ca')
                    chain += 1
            else:
                layout.append(k if k == 'key' else 'other')
        st = os.stat(path)
        f = {'path': path, 'role': 'cert', 'format': fmt, 'layout': layout, 'mode': '%04o' % (st.st_mode & 0o7777),
             'owner': '%d:%d' % (st.st_uid, st.st_gid), 'old_sha256': fps[olds[0]]}
        out = [f]
        keys = [b for k, b in blocks if k == 'key']
        if keys:
            if any('ENCRYPTED' in b for b in keys):
                raise Held('the key in %s has a password: a person installs it' % path)
            if self.key_pub(keys[0]) != old_pub:
                raise Held('%s holds a key that is not the old certificate\'s' % path)
            f['with_key'] = True
        else:
            out.append(self.key_plan(path, old_pub))
        if chain:
            self.chain_plan(f, item, [b for i, b in enumerate(certs) if i != olds[0]])
        return out

    def chain_plan(self, f, item, old_cas):
        """A file that holds its chain gets the new certificate's chain: the PKI's, as many as the old file had
        (a root only when it had one) - or the old chain itself when the new certificate has the same issuer."""
        if item['chain_pem']:
            had_root = any(self.names(c)[0] == self.names(c)[1] for c in old_cas)
            f['chain_pem'] = [c for c in item['chain_pem'] if had_root or self.names(c)[0] != self.names(c)[1]]
            return
        first_old = self.names(old_cas[0])[0] if old_cas else ''
        if first_old and first_old == self.names(item['cert_pem'])[1]:
            f['chain_pem'] = old_cas
            item['notes'].append('%s: the same issuer - its chain is kept' % f['path'])
            return
        raise Held('%s holds a chain, the new certificate has another issuer (%s), and no chain came with it: '
                   'attach the chain (.p7b) to the request' % (f['path'], self.names(item['cert_pem'])[1]))

    def key_plan(self, cert_path, old_pub):
        """The old key's file: named in the settings (key_files), else in the configuration that names the
        certificate, else the one file in the usual folders with the old certificate's key. Several - held."""
        given = (self.p['key_files'] or {}).get(cert_path)
        if given:
            if not os.path.isfile(given):
                raise Held('cert_install_key_files names %s for %s: no such file' % (given, cert_path))
            hits = [given] if self.key_file_pub(given) == old_pub else []
            if not hits:
                raise Held('cert_install_key_files names %s for %s, but it is not that certificate\'s key' % (given, cert_path))
        else:
            hits = [k for k in self.config_paths(cert_path) if self.key_file_pub(k) == old_pub]
            if not hits:
                hits = [k for k in self.scan_keys(cert_path) if self.key_file_pub(k) == old_pub]
        hits = sorted(set(os.path.realpath(h) for h in hits))
        if not hits:
            enc = [k for k in self.scan_keys(cert_path) if self.key_file_pub(k) is None and 'ENCRYPTED' in self.head(k)]
            if enc:
                raise Held('the old key of %s is not readable without a password (%s?): a person installs it' % (cert_path, enc[0]))
            raise Held('the old key of %s was not found (its configuration, or %s): name it in cert_install_key_files'
                       % (cert_path, ', '.join(self.key_dirs[:3])))
        if len(hits) > 1:
            raise Held('the old key of %s is in several files (%s): name the one in use in cert_install_key_files'
                       % (cert_path, ', '.join(hits)))
        st = os.stat(hits[0])
        return {'path': hits[0], 'role': 'key', 'format': 'pem', 'layout': ['key'], 'mode': '%04o' % (st.st_mode & 0o7777),
                'owner': '%d:%d' % (st.st_uid, st.st_gid), 'for': cert_path}

    def head(self, path):
        try:
            with open(path, 'rb') as fh:
                return fh.read(4096).decode('utf-8', 'replace')
        except (IOError, OSError):
            return ''

    def key_file_pub(self, path):
        text = self.head(path)
        if 'PRIVATE KEY-----' not in text:
            return None
        try:
            with open(path, 'rb') as fh:
                data = fh.read()
        except (IOError, OSError):
            return None
        keys = [b for k, b in blocks_of(data.decode('utf-8', 'replace')) if k == 'key']
        if len(keys) != 1 or 'ENCRYPTED' in keys[0]:
            return None
        return self.key_pub(keys[0])

    def config_files(self, path):
        """The configuration files (of the known services) that name this file - [(service, the file)]."""
        out = []
        for k in self.known:
            d = k.get('dir') or ''
            if not os.path.isdir(d):
                continue
            n = 0
            for root, dirs, fnames in os.walk(d):
                dirs[:] = [x for x in dirs if not x.startswith('.')]
                for fn in fnames:
                    n += 1
                    if n > 2000:
                        break
                    full = os.path.join(root, fn)
                    try:
                        if os.path.getsize(full) > 1048576 or not os.path.isfile(full):
                            continue
                        with open(full, 'rb') as fh:
                            if path.encode('utf-8') in fh.read():
                                out.append((k, full))
                    except (IOError, OSError):
                        continue
        return out

    def config_paths(self, cert_path):
        """The other files the configuration naming this certificate names - where its key is, usually."""
        out = []
        for _, conf in self.config_files(cert_path):
            try:
                with open(conf) as fh:
                    text = fh.read()
            except (IOError, OSError, UnicodeDecodeError):
                continue
            for p in re.findall(r'(/[^\s"\';<>,]+)', text):
                if p != cert_path and os.path.isfile(p) and p not in out:
                    out.append(p)
        return out

    def scan_keys(self, cert_path):
        d = os.path.dirname(cert_path)
        dirs = [d, os.path.join(os.path.dirname(d), 'private'), os.path.join(d, 'private')] + list(self.key_dirs)
        out, seen = [], set()
        for top in dirs:
            if not os.path.isdir(top) or top in seen:
                continue
            seen.add(top)
            depth0 = top.rstrip('/').count('/')
            for root, sub, fnames in os.walk(top):
                if root.count('/') - depth0 >= 2:
                    sub[:] = []
                for fn in fnames:
                    full = os.path.join(root, fn)
                    if full.startswith(self.dir.rstrip('/') + '/') or not os.path.isfile(full):
                        continue        # never the renewal's own keys (the new one), nor its backups
                    if os.path.getsize(full) < 65536 and full not in out:
                        out.append(full)
        return out

    def check_key_shared(self, item, pend):
        """A key file whose key another certificate (not replaced here) also uses: replacing it would break that one."""
        mine = set(f['path'] for f in item['files'])
        for f in [x for x in item['files'] if x['role'] == 'key']:
            pub = self.key_file_pub(f['path'])
            for c in self.scan_keys(f['for']):
                if c in mine or c == f['path']:
                    continue
                text = self.head(c)
                if 'BEGIN CERTIFICATE' not in text:
                    continue
                try:
                    with open(c) as fh:
                        certs = [b for k, b in blocks_of(fh.read()) if k == 'cert']
                except (IOError, OSError, UnicodeDecodeError):
                    continue
                for b in certs[:1]:
                    if sha256_pem(b)[:8] == pend['old_fp8']:
                        item['notes'].append('%s is a copy of the old certificate: not replaced' % c)
                    elif self.cert_pub(b) == pub:
                        raise Held('the old key (%s) is also the key of %s, another certificate: replacing it would break that one'
                                   % (f['path'], c))

    # ---- the services and the ports -------------------------------------------------------------
    def listeners(self):
        """Every TCP port listening, with its processes' services: [(address, port, [services])]."""
        if self._ss is not None:
            return self._ss
        self._ss = []
        rc, out, _ = self.run(['ss', '-ltnp'])
        for line in out.splitlines():
            if not line.startswith('LISTEN'):
                continue
            cols = line.split()
            if len(cols) < 5:
                continue
            local = cols[3]
            addr, _, port = local.rpartition(':')
            try:
                port = int(port)
            except ValueError:
                continue
            pids = [int(x) for x in re.findall(r'pid=(\d+)', line)]
            self._ss.append((addr.strip('[]').split('%')[0], port, sorted(set(self.unit_of(p) for p in pids))))
        return self._ss

    def unit_of(self, pid):
        rc, out, _ = self.run(['ps', '-o', 'unit=', '-p', str(pid)])
        u = out.strip()
        if rc == 0 and u and u != '-':
            return u
        try:
            with open('/proc/%d/cgroup' % pid) as fh:
                for line in fh:                 # cgroup v2 (0::/system.slice/x.service) or v1 (1:name=systemd:/...)
                    if line.startswith('0::') or ':name=systemd:' in line:
                        return line.strip().rsplit('/', 1)[-1]
        except (IOError, OSError):
            pass
        return ''

    def units_for(self, item, cert_paths, ports):
        units = []
        for path in cert_paths:
            for s in self.p['services'] or []:
                if fnmatch.fnmatch(path, str(s.get('path') or '')) and s.get('service'):
                    units.append((self.unit_name(s['service']), s.get('test') or []))
            for k, _ in self.config_files(path):
                units.append((self.unit_name(k['service']), k.get('test') or []))
        for host, port in ports:
            for addr, p, us in self.listeners():
                if p == port:
                    for u in us:
                        if u.endswith('.scope') or u.startswith('user@') or not u.endswith('.service'):
                            raise ByHand('the port %d is served by %s (a container, or a user\'s program): a person restarts it' % (port, u or 'a process'))
                        units.append((u, next((k.get('test') or [] for k in self.known if self.unit_name(k['service']) == u), [])))
        out, seen = [], set()
        for u, test in units:
            if u in seen:
                continue
            seen.add(u)
            if any(fnmatch.fnmatch(u, g) or fnmatch.fnmatch(u.rsplit('.', 1)[0], g) for g in self.p['never_reload'] or []):
                raise ByHand('%s is in cert_install_never_reload: a person installs it' % u)
            rc, show, _ = self.run(['systemctl', 'show', '-p', 'LoadState', '-p', 'ActiveState', '-p', 'CanReload', u])
            props = dict(ln.split('=', 1) for ln in show.splitlines() if '=' in ln)
            if props.get('LoadState') != 'loaded':
                raise Held('the service %s is not on this host (%s)' % (u, props.get('LoadState') or 'systemctl show failed'))
            active = props.get('ActiveState') in ('active', 'reloading')
            if active and props.get('CanReload') != 'yes':
                raise ByHand('%s cannot reload - it needs a restart to take the new certificate: a person restarts it' % u)
            if test and not self.m.get_bin_path(test[0]):
                test = []
            out.append({'unit': u, 'reload': active, 'test': list(test)})
            if not active:
                item['notes'].append('%s is not running: nothing to reload (it takes the new certificate when it starts)' % u)
        if not out:
            raise Held('no service found that uses %s (no configuration of %s names it, no port of it known): '
                       'name it in cert_install_services' % (', '.join(cert_paths), ', '.join(sorted(set(k['service'] for k in self.known)))))
        return out

    def unit_name(self, u):
        u = str(u)
        return u if '.' in u else u + '.service'

    def served(self, addr, port, sni, starttls):
        """The certificate the port serves now (its SHA-256), or ''."""
        host = {'': '127.0.0.1', '*': '127.0.0.1', '0.0.0.0': '127.0.0.1', '::': '::1'}.get(addr, addr)
        target = ('[%s]:%d' % (host, port)) if ':' in host else ('%s:%d' % (host, port))
        args = ['timeout', '10', 'openssl', 's_client', '-connect', target]
        if sni:
            args += ['-servername', sni]
        if starttls:
            args += ['-starttls', starttls]
        rc, out, _ = self.run(args, data=b'\n')
        b = [x for k, x in blocks_of(out) if k == 'cert']
        return sha256_pem(b[0]) if b else ''

    def verify_targets(self, item, ports, pend):
        """The ports that serve the old certificate now - each must serve the new one after the reload."""
        names = [n.split(':', 1)[1] for n in (item['new'].get('names') or []) if n.startswith('dns:')]
        cn = re.search(r'(?:^|,)CN=([^,]+)', item['subject'] or '')
        snis = ([cn.group(1)] if cn else []) + names + ['']
        units = set(u['unit'] for u in item['units'])
        cands = []
        for a, p in ports:
            if p not in [x for _, x in cands]:
                cands.append((a, p))
        for addr, port, us in self.listeners():
            if set(us) & units and port not in [x for _, x in cands]:
                cands.append((addr, port))
        out = []
        for addr, port in cands:
            for sni in [s for i, s in enumerate(snis) if s not in snis[:i]][:4]:
                fp = self.served(addr, port, sni, STARTTLS.get(port, ''))
                if fp[:8] == pend['old_fp8']:
                    out.append({'addr': addr, 'port': port, 'sni': sni, 'starttls': STARTTLS.get(port, '')})
                    break
        return out

    # ---- the install ---------------------------------------------------------------------------
    def install_one(self, ap):
        base = os.path.basename(str(ap.get('base') or ''))
        res = {'base': base, 'subject': ap.get('subject', ''), 'ritm': ap.get('ritm', ''), 'ritm_sys_id': ap.get('ritm_sys_id', ''),
               'status': '', 'reason': '', 'backup': '', 'files': [], 'units': [], 'verified': [],
               'new': ap.get('new') or {}}
        if ap.get('status') not in ('ready', 'installed already'):
            res.update(status='refused', reason='it was not ready in the plan (%s)' % ap.get('status'))
            return res
        iss = {'base': base, 'ritm': ap.get('ritm'), 'ritm_sys_id': ap.get('ritm_sys_id'), 'source': ap.get('source', ''),
               'cert': dict(ap.get('new') or {}, pem=ap.get('cert_pem', '')), 'chain': [{'pem': c} for c in ap.get('chain_pem') or []]}
        now = self.plan_one(iss)
        if now['status'] == 'installed already':
            if not self.m.check_mode:
                self.archive(base, ap, None)
            res.update(status='installed already', reason=now['reason'])
            return res
        if now['status'] != 'ready':
            res.update(status='refused', reason='changed since the approval - now %s: %s' % (now['status'], now['reason']))
            return res
        want = sorted((f['path'], f['role']) for f in ap.get('files') or [])
        got = sorted((f['path'], f['role']) for f in now['files'])
        if want != got:
            res.update(status='refused', reason='changed since the approval - the files are now %s, approved %s'
                       % (', '.join(p for p, _ in got), ', '.join(p for p, _ in want)))
            return res
        if sorted(u['unit'] for u in now['units']) != sorted(u.get('unit') for u in ap.get('units') or []):
            res.update(status='refused', reason='changed since the approval - the services are now %s'
                       % ', '.join(u['unit'] for u in now['units']))
            return res
        res.update(files=[f['path'] for f in now['files']], units=[u['unit'] for u in now['units'] if u['reload']])
        if self.m.check_mode:
            res.update(status='would install', verified=['%s:%d' % (v['addr'] or '127.0.0.1', v['port']) for v in now['verify']])
            return res
        pend = self.load(base)
        contents = self.contents(now, pend)
        backup = self.backup(base, now, ap)
        res['backup'] = backup
        written = []
        try:
            for f in now['files']:
                self.write_like(f['path'], contents[f['path']])
                written.append(f['path'])
        except (IOError, OSError) as e:
            self.put_back(backup, written)
            res.update(status='rolled back', reason='writing %s failed (%s): the old files are back' % (f['path'], e))
            return res
        for u in now['units']:
            if u['test']:
                rc, out, err = self.run(u['test'])
                if rc != 0:
                    self.put_back(backup, written)
                    res.update(status='rolled back', reason='the configuration test of %s failed (%s): %s - the old files are back, '
                               'nothing was reloaded' % (u['unit'], ' '.join(u['test']), (err or out).strip()[-300:]))
                    return res
        reloaded = []
        for u in [x for x in now['units'] if x['reload']]:
            rc, out, err = self.run(['systemctl', 'reload', u['unit']])
            reloaded.append(u['unit'])
            if rc != 0:
                self.undo(backup, written, reloaded)
                res.update(status='rolled back', reason='systemctl reload %s failed: %s - the old files are back, reloaded again'
                           % (u['unit'], (err or out).strip()[-300:]))
                return res
        bad = []
        for v in now['verify']:
            fp = ''
            for _ in range(max(1, self.p['verify_wait'])):
                fp = self.served(v['addr'], v['port'], v['sni'], v['starttls'])
                if fp == now['new']['sha256']:
                    break
                time.sleep(1)
            where = '%s:%d' % (v['addr'] or '127.0.0.1', v['port'])
            if fp == now['new']['sha256']:
                res['verified'].append(where)
            else:
                bad.append('%s serves %s' % (where, ('a certificate %s...' % fp[:12]) if fp else 'nothing'))
        if bad:
            self.undo(backup, written, reloaded)
            res.update(status='rolled back', reason='after the reload, %s - not the new certificate: the old files are back, '
                       'reloaded again' % '; '.join(bad))
            return res
        self.archive(base, ap, backup)
        res['status'] = 'installed'
        return res

    def contents(self, plan, pend):
        """Each file's new bytes, in its old layout."""
        out = {}
        leaf = plan['cert_pem'].strip() + '\n'
        key = pend['key'].strip() + '\n'
        for f in plan['files']:
            if f['role'] == 'key':
                out[f['path']] = key.encode('ascii')
                continue
            if f['format'] == 'der':
                out[f['path']] = der_of(leaf)
                continue
            with open(f['path']) as fh:
                old = blocks_of(fh.read())
            parts, chain_done = [], False
            for (k, b), lay in zip(old, f['layout']):
                if lay == 'leaf':
                    parts.append(leaf)
                elif lay == 'key':
                    parts.append(key)
                elif lay == 'ca':
                    if not chain_done:
                        parts.extend(c.strip() + '\n' for c in f.get('chain_pem') or [])
                        chain_done = True
                else:
                    parts.append(b)
            out[f['path']] = ''.join(parts).encode('ascii')
        return out

    def write_like(self, path, data, meta=None):
        """Write a file in place of another - the same owner, mode and SELinux label - through a new file renamed
        over it (never half-written)."""
        st = os.stat(path) if meta is None and os.path.exists(path) else None
        label = None
        try:
            label = os.getxattr(path, 'security.selinux') if meta is None else None
        except (OSError, AttributeError):
            label = None
        if meta is not None:
            mode, uid, gid = int(meta['mode'], 8), meta['uid'], meta['gid']
            label = base64.b64decode(meta['selinux']) if meta.get('selinux') else None
        else:
            mode, uid, gid = st.st_mode & 0o7777, st.st_uid, st.st_gid
        tmp = os.path.join(os.path.dirname(path), '.%s.site-new' % os.path.basename(path))
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.chown(tmp, uid, gid)
        except OSError:
            pass              # not root (the tests): the owner stays
        os.chmod(tmp, mode)
        if label:
            try:
                os.setxattr(tmp, 'security.selinux', label)
            except (OSError, AttributeError):
                pass
        os.rename(tmp, path)

    def backup(self, base, plan, ap):
        d = new_dir(os.path.join(self.dir, 'backup'), base)          # it holds the old key: 0700, the copies 0600
        name = os.path.basename(d)
        os.mkdir(os.path.join(d, 'files'), 0o700)
        os.chmod(os.path.join(d, 'files'), 0o700)
        files = []
        for i, f in enumerate(plan['files']):
            st = os.stat(f['path'])
            try:
                label = base64.b64encode(os.getxattr(f['path'], 'security.selinux')).decode()
            except (OSError, AttributeError):
                label = ''
            copy = os.path.join(d, 'files', '%d-%s' % (i, os.path.basename(f['path'])))
            shutil.copyfile(f['path'], copy)
            os.chmod(copy, 0o600)
            with open(copy, 'rb') as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
            files.append({'path': f['path'], 'role': f['role'], 'copy': os.path.basename(copy), 'mode': '%04o' % (st.st_mode & 0o7777),
                          'uid': st.st_uid, 'gid': st.st_gid, 'selinux': label, 'sha256': digest})
        rec = {'base': base, 'subject': plan.get('subject', ''), 'made': time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime()),
               'aap_job': self.p['job'], 'ritm': ap.get('ritm', ''), 'old_sha256': next((f['old_sha256'] for f in plan['files'] if f.get('old_sha256')), ''),
               'new': plan['new'], 'files': files, 'units': plan['units'], 'verify': plan['verify']}
        with open(os.path.join(d, 'backup.json'), 'w') as fh:
            json.dump(rec, fh, indent=2)
        return name

    def put_back(self, name, paths=None):
        d = os.path.join(self.dir, 'backup', name)
        with open(os.path.join(d, 'backup.json')) as fh:
            rec = json.load(fh)
        for f in rec['files']:
            if paths is not None and f['path'] not in paths:
                continue
            with open(os.path.join(d, 'files', f['copy']), 'rb') as fh:
                self.write_like(f['path'], fh.read(), meta=f)
        return rec

    def undo(self, name, written, reloaded):
        self.put_back(name, written)
        for u in reloaded:
            self.run(['systemctl', 'reload', u])

    def archive(self, base, ap, backup):
        d = new_dir(os.path.join(self.dir, 'installed'), base)
        for ext in ('.key', '.csr', '.json'):
            src = os.path.join(self.dir, base + ext)
            if os.path.exists(src):
                os.rename(src, os.path.join(d, base + ext))
        with open(os.path.join(d, 'installed.json'), 'w') as fh:
            json.dump({'base': base, 'installed': time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime()), 'aap_job': self.p['job'],
                       'ritm': ap.get('ritm', ''), 'new': ap.get('new') or {}, 'backup': backup or '',
                       'files': [f.get('path') for f in ap.get('files') or []]}, fh, indent=2)

    # ---- restore -------------------------------------------------------------------------------
    def backups(self):
        out = []
        for d in sorted(glob.glob(os.path.join(self.dir, 'backup', '*', 'backup.json'))):
            try:
                with open(d) as fh:
                    rec = json.load(fh)
            except (IOError, OSError, ValueError):
                continue
            out.append({'name': os.path.basename(os.path.dirname(d)), 'base': rec.get('base', ''), 'subject': rec.get('subject', ''),
                        'made': rec.get('made', ''), 'restored': rec.get('restored', ''), 'files': [f['path'] for f in rec.get('files', [])]})
        return out

    def restore(self):
        allb = self.backups()
        pick = []
        if self.p['backup']:
            pick = [b for b in allb if b['name'] == self.p['backup']]
            if not pick:
                return allb, [{'backup': self.p['backup'], 'status': 'failed', 'reason': 'no backup %s on this host' % self.p['backup']}]
        elif self.p['only']:
            newest = {}
            for b in allb:
                if re.search(self.p['only'], b['base'] + ' ' + b['subject'], re.I):
                    newest[b['base']] = b
            pick = sorted(newest.values(), key=lambda b: b['name'])
        out = []
        for b in pick:
            d = os.path.join(self.dir, 'backup', b['name'])
            with open(os.path.join(d, 'backup.json')) as fh:
                rec = json.load(fh)
            r = {'backup': b['name'], 'base': rec.get('base', ''), 'subject': rec.get('subject', ''), 'status': '', 'reason': '',
                 'files': [f['path'] for f in rec['files']], 'units': [u['unit'] for u in rec.get('units') or [] if u.get('reload')]}
            if self.m.check_mode:
                r['status'] = 'would restore'
                out.append(r)
                continue
            try:
                self.put_back(b['name'])
                for u in rec.get('units') or []:
                    if u.get('test'):
                        rc, o, e = self.run(u['test'])
                        if rc != 0:
                            raise IOError('the configuration test of %s failed after the restore: %s' % (u['unit'], (e or o).strip()[-200:]))
                for u in r['units']:
                    rc, o, e = self.run(['systemctl', 'reload', u])
                    if rc != 0:
                        raise IOError('systemctl reload %s failed: %s' % (u, (e or o).strip()[-200:]))
                old = rec.get('old_sha256', '')
                for v in rec.get('verify') or []:
                    fp = ''
                    for _ in range(max(1, self.p['verify_wait'])):
                        fp = self.served(v['addr'], v['port'], v['sni'], v['starttls'])
                        if fp == old:
                            break
                        time.sleep(1)
                    if fp != old:
                        raise IOError('%s:%d does not serve the old certificate again' % (v['addr'] or '127.0.0.1', v['port']))
                self.pending_again(rec.get('base', ''))
                rec['restored'] = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())
                with open(os.path.join(d, 'backup.json'), 'w') as fh:
                    json.dump(rec, fh, indent=2)
                r['status'] = 'restored'
            except (IOError, OSError, ValueError) as e:
                r.update(status='failed', reason=str(e))
            out.append(r)
        return allb, out

    def pending_again(self, base):
        """The renewal that installed this certificate waits again (its key and CSR back in the folder): the next
        install check finds its certificate again."""
        done = sorted(glob.glob(os.path.join(self.dir, 'installed', '*-' + base)))
        if not done:
            return
        src = done[-1]
        for ext in ('.key', '.csr', '.json'):
            f = os.path.join(src, base + ext)
            if os.path.exists(f) and not os.path.exists(os.path.join(self.dir, base + ext)):
                os.rename(f, os.path.join(self.dir, base + ext))


def main():
    m = AnsibleModule(argument_spec=dict(
        action=dict(type='str', default='pending', choices=['pending', 'plan', 'install', 'restore']),
        dir=dict(type='path', required=True), issued=dict(type='list', elements='dict', default=[]),
        approved=dict(type='list', elements='dict', default=[]), services=dict(type='list', elements='dict', default=[]),
        never_reload=dict(type='list', elements='str', default=[]), key_files=dict(type='dict', default={}),
        key_dirs=dict(type='list', elements='path', default=[]), known=dict(type='list', elements='dict', default=[]),
        trust_check=dict(type='bool', default=True), verify_wait=dict(type='int', default=15),
        backup=dict(type='str', default=''), only=dict(type='str', default=''), job=dict(type='str', default=''),
        install_notes=dict(type='dict', default={})),
        supports_check_mode=True)
    i = Installer(m)
    a = m.params['action']
    if a == 'pending':
        m.exit_json(changed=False, pending=i.pending() if os.path.isdir(m.params['dir']) else [])
    if a == 'plan':
        m.exit_json(changed=False, plan=[i.plan_one(x) for x in m.params['issued']])
    if a == 'install':
        out = [i.install_one(x) for x in m.params['approved']]
        m.exit_json(changed=any(r['status'] in ('installed', 'rolled back') for r in out), installed=out)
    allb, out = i.restore()
    m.exit_json(changed=any(r['status'] == 'restored' for r in out), backups=allb, restored=out)


if __name__ == '__main__':
    main()
