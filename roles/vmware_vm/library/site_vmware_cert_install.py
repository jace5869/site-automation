#!/usr/bin/python
# -*- coding: utf-8 -*-
"""The certificates the PKI issued for vCenter's and the ESXi hosts' CSRs (playbooks/vmware_cert_renew.yml made them
where their keys stay), installed there: what installing each would do (plan), then - after the approval - the
install itself, checked. vCenter takes its Machine SSL certificate through its REST API (the vSphere Client's
"Replace with external CA certificate where CSR is generated from vCenter Server"), a host its certificate through
vCenter (vSphere API InstallServerCertificate, the key the host made with its CSR)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vmware_cert_install
short_description: Plan, then install, the issued certificates of vCenter and its ESXi hosts
description:
  - C(plan) - for each item (an issued certificate, found on its catalog request by site_cert_issued), what installing
    it would do, or why a person must first do something - read from vCenter, changing nothing. A host - connected,
    vCenter's vpxd.certmgmt.mode custom (else vCenter may put its own certificate back on the host), the CA chain in
    vCenter's trusted roots (else vCenter refuses the host after the replacement: the plan adds the chain - trust_check).
    vCenter - whether its services restart (before vCenter 8.0 Update 2).
  - C(install) - the approved items, each planned again first (the same certificate, still ready, or refused); the
    hosts first, vCenter's own certificate last. A host - the CA chain added to vCenter's trusted roots when missing,
    the certificate installed (the key waiting on the host), the host's services told, the host reconnected when
    vCenter lost it, and vCenter must then show the new certificate. vCenter - the certificate and its chain replace
    the Machine SSL certificate (the key waiting in vCenter), then port 443 must serve it (vcenter_wait). Nothing is
    put back: the old key was replaced by the CSR's, so a failure is said with what to do. Check mode changes nothing.
  - vCenter's address and account come from VMWARE_HOST, VMWARE_USER, VMWARE_PASSWORD (and VMWARE_PORT).
options:
  action: {description: plan or install., type: str, choices: [plan, install], default: plan}
  items:
    description: >-
      The certificates - key, vcenter, kind (vcenter or esxi), name, old ({expires, sha256} of the certificate in use when
      it was read), cert ({pem, sha256, serial, subject, issuer, not_after}), chain ([{pem, sha256, subject, self_signed}]
      from the issuer up), and anything else (ritm ...), kept as it is.
    type: list
    elements: dict
    default: []
  trust_check: {description: Check (and add) the CA chain in vCenter's trusted roots for a host's certificate., type: bool, default: true}
  host_wait: {description: Seconds a host may take to show the new certificate (and to reconnect)., type: int, default: 300}
  vcenter_wait: {description: Seconds vCenter may take to serve the new certificate on its port (its services restart before 8.0 Update 2)., type: int, default: 1800}
  validate_certs: {description: Check vCenter's certificate for the API calls., type: bool, default: true}
  timeout: {description: Seconds for one connection., type: int, default: 30}
author: site automation
'''

RETURN = r'''
plan:
  description: (plan) Each item with status (ready, held, not connected, installed already, failed), reason, steps, notes.
  type: list
  returned: success
  sample: [{key: "vc01|esxi|esx01.example.mil", kind: esxi, name: esx01.example.mil, status: ready, reason: "",
            steps: ["add the CA chain to vCenter vc01's trusted roots (2 certificates)", "install it on the host ..."],
            add_chain: true, notes: []}]
installed:
  description: (install) Each approved item with status (installed, installed already, would install, refused, failed), reason, done, verified.
  type: list
  returned: success
'''

import base64
import datetime
import hashlib
import json
import os
import socket
import ssl
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_vcenters import connect, disconnect, host_port
from ansible.module_utils.six.moves.urllib import error as urlerror
from ansible.module_utils.six.moves.urllib import request as urlrequest

try:
    from pyVmomi import vim
    HAS_PYVMOMI = True
except ImportError:
    HAS_PYVMOMI = False

TLS_PATH = "/vcenter/certificate-management/vcenter/tls"
ROOTS_PATH = "/vcenter/certificate-management/vcenter/trusted-root-chains"
MODE_KEY = "vpxd.certmgmt.mode"
NO_RESTART = (8, 0, 2)          # vCenter 8.0 Update 2 and later replace the Machine SSL certificate without a restart
POLL = 15


def fault_text(e):
    """A vSphere fault (or any error) as one line; a missing privilege is named."""
    pid = getattr(e, "privilegeId", None)
    if pid:
        return "the vCenter account lacks the privilege %s" % pid
    msg = " ".join(str(getattr(e, "msg", None) or e).split())
    name = type(e).__name__.split(".")[-1]
    return "%s: %s" % (name, msg) if msg and name not in msg else (msg or name)


def version_tuple(v):
    out = []
    for x in str(v or "").split("."):
        try:
            out.append(int(x))
        except ValueError:
            break
    return tuple(out + [0] * (3 - len(out)))


def der_sha256(der):
    return hashlib.sha256(der).hexdigest()


def pem_sha256(pem):
    """A PEM certificate's SHA-256 fingerprint (lower case hex); '' when it is not one."""
    try:
        body = "".join(x for x in str(pem).strip().splitlines() if x and not x.startswith("-----"))
        return der_sha256(base64.b64decode(body))
    except Exception:                           # noqa: BLE001
        return ""


def tls_der(host, port, timeout):
    """The certificate a TLS server presents (DER), not checked."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        tls = ctx.wrap_socket(sock, server_hostname=host)
        try:
            return tls.getpeercert(binary_form=True)
        finally:
            tls.close()
    finally:
        sock.close()


def error_text(d):
    if isinstance(d, dict):
        v = d.get("value") if isinstance(d.get("value"), dict) else d
        msgs = [m.get("default_message", "") for m in (v.get("messages") or []) if isinstance(m, dict)]
        if msgs:
            return "; ".join(x for x in msgs if x)
        return json.dumps(d)[:300]
    return str(d or "")[:300]


class Api(object):
    """vCenter's REST API with the credential's account: /api (vCenter 7 and later), else /rest (6.7)."""

    def __init__(self, host, port, validate_certs, timeout):
        self.base = "https://%s:%d" % ("[%s]" % host if ":" in host else host, port)
        self.timeout = timeout
        self.ctx = ssl.create_default_context()
        if not validate_certs:
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE
        self.token, self.old = "", False

    def call(self, method, path, body=None, auth=False):
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if auth:
            pair = "%s:%s" % (os.environ.get("VMWARE_USER", ""), os.environ.get("VMWARE_PASSWORD", ""))
            headers["Authorization"] = "Basic " + base64.b64encode(pair.encode("utf-8")).decode("ascii")
        elif self.token:
            headers["vmware-api-session-id"] = self.token
        req = urlrequest.Request(self.base + path, data=data, headers=headers)
        req.get_method = lambda: method
        try:
            resp = urlrequest.urlopen(req, timeout=self.timeout, context=self.ctx)
            try:
                code, raw = resp.getcode(), resp.read()
            finally:
                resp.close()
        except urlerror.HTTPError as e:
            try:
                code, raw = e.code, e.read()
            finally:
                e.close()
        try:
            return code, json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            return code, raw.decode("utf-8", "replace")[:300]

    def path(self, p):
        return ("/rest" if self.old else "/api") + p

    def value(self, d):
        return d.get("value") if self.old and isinstance(d, dict) and "value" in d else d

    def login(self, name):
        code, d = self.call("POST", "/api/session", auth=True)
        if code == 404:
            self.old = True
            code, d = self.call("POST", "/rest/com/vmware/cis/session", auth=True)
            d = d.get("value") if isinstance(d, dict) else d
        if code in (401, 403):
            raise RuntimeError("vCenter %s refused the login to its API (HTTP %d) - the VMware vCenter credential's user "
                               "name (name@domain) or password" % (name, code))
        if code not in (200, 201) or not isinstance(d, str) or not d:
            raise RuntimeError("vCenter %s: no API session (HTTP %s: %s)" % (name, code, error_text(d)))
        self.token = d

    def logout(self):
        if self.token:
            try:
                self.call("DELETE", "/rest/com/vmware/cis/session" if self.old else "/api/session")
            except Exception:                   # noqa: BLE001
                pass
            self.token = ""

    def trusted_roots(self, name):
        """The SHA-256 of every certificate in vCenter's trusted root chains."""
        code, d = self.call("GET", self.path(ROOTS_PATH))
        if code == 404:
            raise RuntimeError("vCenter %s has no certificate API (%s, HTTP 404)" % (name, ROOTS_PATH))
        if code in (401, 403):
            raise RuntimeError("vCenter %s refused to list its trusted roots (HTTP %d): the account needs Certificate "
                               "Management (CAAdmins, or the privilege as a Global Permission)" % (name, code))
        if code != 200 or not isinstance(self.value(d), list):
            raise RuntimeError("vCenter %s: its trusted roots could not be read (HTTP %s: %s)" % (name, code, error_text(d)))
        out = set()
        for ch in self.value(d):
            cid = ch.get("chain") if isinstance(ch, dict) else ch
            code, one = self.call("GET", self.path(ROOTS_PATH + "/" + str(cid)))
            one = self.value(one)
            if code != 200 or not isinstance(one, dict):
                raise RuntimeError("vCenter %s: its trusted root chain %s could not be read (HTTP %s)" % (name, cid, code))
            for pem in ((one.get("cert_chain") or {}).get("cert_chain") or []):
                s = pem_sha256(pem)
                if s:
                    out.add(s)
        return out

    def add_roots(self, name, pems):
        body = {"cert_chain": {"cert_chain": list(pems)}}
        code, d = self.call("POST", self.path(ROOTS_PATH), {"spec": body} if self.old else body)
        if code in (401, 403):
            raise RuntimeError("vCenter %s refused to add the CA chain to its trusted roots (HTTP %d): the account needs "
                               "Certificate Management (CAAdmins, or the privilege as a Global Permission)" % (name, code))
        if code not in (200, 201, 204):
            raise RuntimeError("vCenter %s did not add the CA chain to its trusted roots (HTTP %s: %s)" % (name, code, error_text(d)))

    def replace_tls(self, name, pem, chain):
        """Replace the Machine SSL certificate with the issued one - no key: vCenter's own, made with its CSR."""
        spec = {"cert": pem}
        if chain:
            spec["root_cert"] = "\n".join(p.strip() for p in chain) + "\n"
        code, d = self.call("PUT", self.path(TLS_PATH), {"spec": spec} if self.old else spec)
        if code == 404:
            raise RuntimeError("vCenter %s has no certificate API (%s, HTTP 404): replace it in the vSphere Client" % (name, TLS_PATH))
        if code in (401, 403):
            raise RuntimeError("vCenter %s refused to replace its certificate (HTTP %d: %s): the account needs the privileges "
                               "CertificateManagement.Manage and CertificateManagement.Administer (administrator@vsphere.local, "
                               "CAAdmins)" % (name, code, error_text(d)))
        if code not in (200, 204):
            raise RuntimeError("vCenter %s did not take the certificate (HTTP %s: %s) - nothing was changed. A key mismatch: "
                               "a CSR was made on vCenter after this one (its key replaced): order again" % (name, code, error_text(d)))


def _when(dt):
    if dt is None:
        return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _same_dn(a, b):
    def pairs(dn):
        out = []
        for part in str(dn or "").replace("\\,", "\0").split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                out.append((k.strip().upper(), v.strip().replace("\0", ",").lower()))
        return sorted(out)
    return bool(pairs(a)) and pairs(a) == pairs(b)


def host_state(h):
    """What vCenter shows of a host's certificate: {connection, not_after, issuer, subject, sha256 ('' unknown)}."""
    out = {"connection": str(h.runtime.connectionState), "not_after": "", "issuer": "", "subject": "", "sha256": ""}
    cm = h.configManager.certificateManager
    info = cm.certificateInfo if cm is not None else None
    if info is not None:
        out.update(not_after=_when(info.notAfter), issuer=info.issuer or "", subject=info.subject or "")
    try:
        raw = h.config.certificate
        if raw:
            pem = raw if isinstance(raw, (bytes, bytearray)) else bytes(int(b) & 0xFF for b in raw)
            out["sha256"] = pem_sha256(pem.decode("ascii", "replace"))
    except Exception:                           # noqa: BLE001
        pass
    return out


def shows_new(st, cert):
    """Does vCenter show the issued certificate on the host? Its fingerprint when vCenter gives it, else its expiry and issuer."""
    if st.get("sha256"):
        return st["sha256"] == str(cert.get("sha256", "")).lower()
    return st.get("not_after") == cert.get("not_after") and _same_dn(st.get("issuer"), cert.get("issuer"))


class Facts(object):
    """What the plan needs of one vCenter: version, vpxd.certmgmt.mode, its hosts, its trusted roots (read once)."""

    def __init__(self, name, validate_certs, timeout, need_roots):
        self.name, self.validate_certs, self.timeout = name, validate_certs, timeout
        self.si = None
        self.error, self.version, self.build, self.mode, self.hosts = "", "", "", "", {}
        self.roots, self.roots_error = None, ""
        self.need_roots = need_roots

    def read(self):
        try:
            self.si = connect(self.name, self.validate_certs, self.timeout)
            content = self.si.RetrieveContent()
            self.version, self.build = content.about.version or "", content.about.build or ""
            try:
                self.mode = str(content.setting.QueryView(name=MODE_KEY)[0].value or "")
            except vim.fault.InvalidName:
                self.mode = "vmca"              # not set: vCenter's default
            view = content.viewManager.CreateContainerView(content.rootFolder, [vim.HostSystem], True)
            try:
                self.hosts = dict((h.name, h) for h in view.view)
            finally:
                view.Destroy()
        except Exception as e:                  # noqa: BLE001
            self.error = str(e) if isinstance(e, RuntimeError) else fault_text(e)
            return self
        if self.need_roots:
            self.read_roots()
        return self

    def read_roots(self):
        host, port = host_port(self.name)
        api = Api(host, port, self.validate_certs, self.timeout)
        try:
            api.login(self.name)
            self.roots, self.roots_error = api.trusted_roots(self.name), ""
        except Exception as e:                  # noqa: BLE001
            self.roots, self.roots_error = None, str(e)
        finally:
            api.logout()

    def close(self):
        if self.si is not None:
            disconnect(self.si)
            self.si = None


def plan_one(it, f, trust_check, timeout, tls=tls_der):
    """One item -> it with status, reason, steps, notes (and add_chain, restart). f: the Facts of its vCenter."""
    r = dict(it)
    r.update(status="ready", reason="", steps=[], notes=[], add_chain=False, restart=False)
    cert, chain = it.get("cert") or {}, [c for c in it.get("chain") or [] if c.get("pem")]
    new = str(cert.get("sha256", "")).lower()
    if not cert.get("pem") or not new:
        return dict(r, status="failed", reason="no issued certificate to install")
    if f.error:
        return dict(r, status="failed", reason="vCenter %s could not be read: %s" % (f.name, f.error))
    vc = "vCenter %s" % f.name
    if it.get("kind") == "esxi":
        h = f.hosts.get(it.get("name"))
        if h is None:
            return dict(r, status="failed", reason="%s has no host %s (any more)" % (vc, it.get("name")))
        st = host_state(h)
        if shows_new(st, cert):
            return dict(r, status="installed already", reason="the host has it already")
        if st["connection"] != "connected":
            return dict(r, status="not connected", reason="the host is %s in %s - a later run" % (st["connection"], vc))
        old = it.get("old") or {}
        if old.get("expires") and st["not_after"] and not st["not_after"].startswith(old["expires"]):
            return dict(r, status="held", reason="the host's certificate changed since it was ordered (it expires %s, the ordered "
                                                 "one replaces one expiring %s): look at it - its request is out of date" % (st["not_after"], old["expires"]))
        if f.mode != "custom":
            return dict(r, status="held", reason="%s's vpxd.certmgmt.mode is %s, not custom: vCenter may put its own certificate "
                                                 "back on the host. Set it to custom (vCenter > Configure > Settings > Advanced "
                                                 "Settings - a person's change), and the next run plans it" % (vc, f.mode or "not set"))
        if trust_check:
            if not chain:
                return dict(r, status="held", reason="no CA chain came with the certificate on %s: vCenter must trust the issuer, or it "
                                                     "refuses the host after the replacement - ask the PKI for the chain (.p7b) on the "
                                                     "request, or keep a CA bundle that has its issuer (cert_install_ca_bundle; or "
                                                     "vmware_cert_install_trust_check: false when %s trusts it)" % (it.get("ritm", "its request"), vc))
            if f.roots is None:
                return dict(r, status="held", reason="%s's trusted roots could not be read (%s): without them the job cannot tell whether "
                                                     "vCenter trusts the issuer (vmware_cert_install_trust_check: false when you know it does)"
                                                     % (vc, f.roots_error))
            missing = [c for c in chain if str(c.get("sha256", "")).lower() not in f.roots]
            if missing:
                r["add_chain"] = True
                r["steps"].append("add the CA chain to %s's trusted roots (%s)" % (vc, "; ".join(c.get("subject", "?") for c in missing)))
        r["steps"].append("install it on the host (InstallServerCertificate: the key the host made with its CSR) and tell the host's services")
        r["steps"].append("reconnect the host in %s if it shows disconnected; then %s must show the new certificate" % (vc, vc))
        r["notes"].append("the host stays in use: no maintenance mode, its VMs keep running")
        return r
    if it.get("kind") == "vcenter":
        host, port = host_port(f.name)
        try:
            cur = der_sha256(tls(host, port, timeout))
        except Exception as e:                  # noqa: BLE001
            return dict(r, status="failed", reason="%s's certificate could not be read on port %d: %s" % (vc, port, e))
        if cur == new:
            return dict(r, status="installed already", reason="vCenter serves it already")
        old = it.get("old") or {}
        if old.get("expires") and old.get("current") and old["expires"] != old["current"]:
            return dict(r, status="held", reason="%s's certificate changed since it was ordered (it expires %s; the ordered one replaces one "
                                                 "expiring %s): look at it - its request is out of date" % (vc, old["current"], old["expires"]))
        if old.get("sha256") and cur != str(old["sha256"]).lower():
            return dict(r, status="held", reason="%s's certificate changed since it was ordered: look at it - its request is out of date" % vc)
        if trust_check and not chain:
            return dict(r, status="held", reason="no CA chain came with the certificate on %s: vCenter needs it with its certificate - ask "
                                                 "the PKI for the chain (.p7b) on the request, or keep a CA bundle that has its issuer (cert_install_ca_bundle)"
                                                 % it.get("ritm", "its request"))
        r["restart"] = version_tuple(f.version) < NO_RESTART
        r["steps"].append("replace %s's Machine SSL certificate (the key vCenter made with its CSR)%s" % (vc, " and add the CA chain" if chain else ""))
        r["steps"].append("then port %d must serve it" % port)
        r["notes"].append(("vCenter %s (%s): its services RESTART - about 10 to 20 minutes without vCenter (vCenter 8.0 Update 2 and later: "
                           "no restart)" % (f.version or "?", f.build or "?")) if r["restart"] else
                          "vCenter %s: no restart (8.0 Update 2 and later)" % (f.version or "?"))
        r["notes"].append("VMware advises a snapshot of vCenter first (vCenters in linked mode: all of them, together) and vCenter HA off")
        return r
    return dict(r, status="failed", reason="kind must be vcenter or esxi, not %s" % it.get("kind"))


def install_esxi(r, f, host_wait, check):
    """Install one planned host certificate; r: plan_one's result (ready)."""
    out = dict(r, done=[], verified=[])
    if check:
        return dict(out, status="would install")
    vc = "vCenter %s" % f.name
    if r.get("add_chain"):
        host, port = host_port(f.name)
        api = Api(host, port, f.validate_certs, f.timeout)
        try:
            api.login(f.name)
            api.add_roots(f.name, [c["pem"] for c in r.get("chain") or [] if c.get("pem")])
        except Exception as e:                  # noqa: BLE001
            return dict(out, status="failed", reason="%s - nothing was changed on the host" % e)
        finally:
            api.logout()
        out["done"].append("the CA chain added to %s's trusted roots" % vc)
    h = f.hosts[r["name"]]
    cm = h.configManager.certificateManager
    try:
        cm.InstallServerCertificate(cert=r["cert"]["pem"])
    except Exception as e:                      # noqa: BLE001
        return dict(out, status="failed", reason="the host refused the certificate (%s) - it keeps its old one. A key mismatch: a CSR "
                                                 "was made on the host after this one (its key replaced): order again" % fault_text(e))
    out["done"].append("installed on the host")
    try:
        cm.NotifyAffectedServices()
        out["done"].append("the host's services told")
    except Exception as e:                      # noqa: BLE001
        out["done"].append("the host's services could not be told (%s): they take it after a restart of its management agents" % fault_text(e))
    deadline, reconnected, st = time.time() + host_wait, False, {}
    while True:
        try:
            st = host_state(h)
        except Exception:                       # noqa: BLE001
            st = {"connection": "unknown"}
        if st.get("connection") == "connected" and shows_new(st, r["cert"]):
            break
        if st.get("connection") in ("disconnected", "notResponding") and not reconnected:
            reconnected = True
            try:
                task = h.ReconnectHost_Task()
                while task.info.state in ("queued", "running") and time.time() < deadline:
                    time.sleep(2)
                out["done"].append("reconnected in %s%s" % (vc, "" if task.info.state == "success" else
                                                            " - it says: %s" % fault_text(task.info.error)))
            except Exception as e:              # noqa: BLE001
                out["done"].append("the reconnect failed: %s" % fault_text(e))
            continue                            # look again at once
        if time.time() >= deadline:
            return dict(out, status="failed", reason="installed, but after %d s %s shows the host %s with %s: look at the host in the vSphere "
                                                     "Client (Configure > System > Certificate; Connection > Connect)" % (
                                                         host_wait, vc, st.get("connection"), ("a certificate expiring " + st["not_after"])
                                                         if st.get("not_after") else "no certificate"))
        time.sleep(POLL)
    out["verified"].append("the host (%s shows it)" % vc)
    return dict(out, status="installed")


def install_vcenter(r, f, vcenter_wait, check, timeout, tls=tls_der, sleep=time.sleep, clock=time.time):
    out = dict(r, done=[], verified=[])
    if check:
        return dict(out, status="would install")
    host, port = host_port(f.name)
    api = Api(host, port, f.validate_certs, timeout)
    new = str(r["cert"]["sha256"]).lower()
    try:
        api.login(f.name)
        api.replace_tls(f.name, r["cert"]["pem"], [c["pem"] for c in r.get("chain") or [] if c.get("pem")])
        out["done"].append("vCenter took the certificate")
    except RuntimeError as e:
        return dict(out, status="failed", reason=str(e))
    except Exception as e:                      # noqa: BLE001 - the connection dropped: its services restart - see what it serves
        out["done"].append("the connection dropped during the replacement (%s): its services restart" % e)
    finally:
        try:
            api.logout()
        except Exception:                       # noqa: BLE001
            pass
    deadline, last = clock() + vcenter_wait, ""
    while True:
        try:
            if der_sha256(tls(host, port, timeout)) == new:
                break
            last = "the old certificate"
        except Exception as e:                  # noqa: BLE001
            last = "not answering (%s)" % e
        if clock() >= deadline:
            return dict(out, status="failed", reason="after %d s port %d serves %s: look at vCenter (its services may still be starting; "
                                                     "Administration > Certificate Management)" % (vcenter_wait, port, last))
        sleep(POLL)
    out["verified"].append("port %d" % port)
    return dict(out, status="installed")


def run(module, p):
    items = p["items"]
    names = []
    for it in items:
        if it.get("vcenter") not in names:
            names.append(it.get("vcenter"))
    facts = {}
    try:
        for n in names:
            need = p["trust_check"] and any(i.get("kind") == "esxi" and i.get("vcenter") == n for i in items)
            facts[n] = Facts(n, p["validate_certs"], p["timeout"], need).read()
        planned = [plan_one(it, facts[it.get("vcenter")], p["trust_check"], p["timeout"]) for it in items]
        if p["action"] == "plan":
            return {"changed": False, "plan": planned}
        done = []
        # the hosts first: vCenter's own certificate may restart its services
        order = sorted(range(len(items)), key=lambda i: (items[i].get("kind") == "vcenter", i))
        for i in order:
            it, r = items[i], planned[i]
            if r["status"] == "installed already":
                done.append(dict(r, done=[], verified=[]))
                continue
            if r["status"] != "ready":
                done.append(dict(r, status="refused", reason="planned again, it is no longer ready - %s: %s" % (r["status"], r["reason"]),
                                 done=[], verified=[]))
                continue
            f = facts[it.get("vcenter")]
            if it.get("kind") == "esxi":
                res = install_esxi(r, f, p["host_wait"], module.check_mode)
                if res.get("status") == "installed" and r.get("add_chain"):
                    f.read_roots()              # the next host of this vCenter: the chain is there now
            else:
                f.close()                       # its services may restart: no session held across it
                res = install_vcenter(r, f, p["vcenter_wait"], module.check_mode, p["timeout"])
            done.append(res)
        return {"changed": any(d.get("status") == "installed" for d in done), "installed": done}
    finally:
        for f in facts.values():
            f.close()


def main():
    module = AnsibleModule(
        argument_spec=dict(action=dict(type="str", choices=["plan", "install"], default="plan"),
                           items=dict(type="list", elements="dict", default=[]),
                           trust_check=dict(type="bool", default=True),
                           host_wait=dict(type="int", default=300),
                           vcenter_wait=dict(type="int", default=1800),
                           validate_certs=dict(type="bool", default=True),
                           timeout=dict(type="int", default=30)),
        supports_check_mode=True,
    )
    if not HAS_PYVMOMI:
        module.fail_json(msg="pyVmomi is not installed in the execution environment (the vmware.vmware collection needs it too)")
    module.exit_json(**run(module, module.params))


if __name__ == "__main__":
    main()
