#!/usr/bin/python
# -*- coding: utf-8 -*-
"""The certificates of vCenter (its Machine SSL certificate - what a browser sees on port 443) and of
its ESXi hosts, and certificate signing requests (CSRs) for them, made where the key stays: vCenter
(its REST API, the same call as the vSphere Client's "Generate Certificate Signing Request") and the
host (vSphere API GenerateCertificateSigningRequest, the vSphere Client's "Generate CSR using FQDN")."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vmware_certs
short_description: Read vCenter's and its ESXi hosts' certificates; make CSRs for them where their keys stay
description:
  - C(read) - every vCenter of the job (VMWARE_HOST and vcenters) - its Machine SSL certificate, read on its
    HTTPS port without checking it (an expired certificate is read all the same) - and, with esxi, every
    connected ESXi host's certificate (its certificate manager, through vCenter). A host that is not
    connected, or whose certificate cannot be read, is listed in not_checked. Changes nothing.
  - C(csr) - a CSR for each of items. vCenter makes the CSR for its Machine SSL certificate with the
    current certificate's subject and names (fields win where set); the host makes its own. The new
    private key stays on vCenter or the host, waiting for the issued certificate - a CSR made later
    replaces it, so a CSR is made once per renewal. Check mode makes nothing.
  - vCenter's address and account come from VMWARE_HOST, VMWARE_USER, VMWARE_PASSWORD (and VMWARE_PORT),
    as a "VMware vCenter" credential in AAP gives them.
options:
  action: {description: read or csr., type: str, choices: [read, csr], default: read}
  vcenters: {description: More vCenters besides the credential's (same account; name or name:port)., type: list, elements: str, default: []}
  esxi: {description: Read the ESXi hosts' certificates too (read)., type: bool, default: true}
  items: {description: "The certificates to make CSRs for (csr): [{vcenter, kind: vcenter or esxi, name}].", type: list, elements: dict, default: []}
  fields: {description: "Subject fields for vCenter's CSR, over the current certificate's: organization, organization_unit, locality, state_or_province, country, email_address, key_size.", type: dict, default: {}}
  validate_certs: {description: Check vCenter's certificate for the API calls (never for reading the certificate itself)., type: bool, default: true}
  timeout: {description: Seconds for one connection., type: int, default: 30}
author: site automation
'''

RETURN = r'''
certs:
  description: (read) One record per certificate.
  type: list
  returned: success
  sample: [{vcenter: vc01.example.mil, kind: esxi, name: esx01.example.mil, cluster: CL1, datacenter: DC1,
            subject: "CN=esx01.example.mil,O=Example", issuer: "CN=CA,DC=vsphere,DC=local", sans: [],
            not_after: "2026-11-30 12:00 UTC", expires: "2026-11-30", days: 42, self_signed: false, status: expiring}]
not_checked:
  description: (read) The hosts whose certificate could not be read, with why.
  type: list
  returned: success
  sample: [{vcenter: vc01.example.mil, kind: esxi, name: esx02.example.mil, why: "not connected (notResponding)"}]
made:
  description: (csr) One record per item - the CSR, its subject and names, or the error.
  type: list
  returned: success
  sample: [{vcenter: vc01.example.mil, kind: vcenter, name: vc01.example.mil, csr: "-----BEGIN CERTIFICATE REQUEST-----...",
            csr_subject: "CN=vc01.example.mil,O=Example", csr_names: [vc01.example.mil], error: ""}]
'''

import base64
import datetime
import json
import os
import socket
import ssl

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_vcenters import connect, disconnect, each_vcenter, host_port
from ansible.module_utils.six.moves.urllib import error as urlerror
from ansible.module_utils.six.moves.urllib import request as urlrequest

try:
    from pyVmomi import vim
    HAS_PYVMOMI = True
except ImportError:
    HAS_PYVMOMI = False

try:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    HAS_CRYPTO = True
    # subject parts by their OID: what vCenter's tls-csr call takes
    PART = dict((o.dotted_string, n) for o, n in (
        (NameOID.COMMON_NAME, "commonName"), (NameOID.ORGANIZATION_NAME, "organizationName"),
        (NameOID.ORGANIZATIONAL_UNIT_NAME, "organizationalUnitName"), (NameOID.LOCALITY_NAME, "localityName"),
        (NameOID.STATE_OR_PROVINCE_NAME, "stateOrProvinceName"), (NameOID.COUNTRY_NAME, "countryName"),
        (NameOID.EMAIL_ADDRESS, "emailAddress")))
except ImportError:
    HAS_CRYPTO = False
    PART = {}

CSR_PATH = "/vcenter/certificate-management/vcenter/tls-csr"
CSR_FIELDS = ("organization", "organization_unit", "locality", "state_or_province", "country", "email_address")


def fault_text(e):
    """A vSphere fault (or any error) as one line. A missing privilege is named: that is what to grant."""
    pid = getattr(e, "privilegeId", None)
    if pid:
        return "the vCenter account lacks the privilege %s" % pid
    msg = getattr(e, "msg", None) or str(e)
    msg = " ".join(str(msg).split())
    name = type(e).__name__.split(".")[-1]
    return "%s: %s" % (name, msg) if msg and name not in msg else (msg or name)


def utc(d):
    """A datetime in UTC (one without a zone is taken as UTC)."""
    return d.replace(tzinfo=datetime.timezone.utc) if d.tzinfo is None else d.astimezone(datetime.timezone.utc)


def days_left(not_after, now=None):
    """Whole days until not_after (a datetime): 0 = today or within 24 hours, -1 = expired within the last day."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return int((utc(not_after) - now).total_seconds() // 86400)


def dn_pairs(dn):
    """'CN=a, OU=b\\, c, O=d' -> [('CN', 'a'), ('OU', 'b, c'), ('O', 'd')] (the key in upper case)."""
    out, cur, esc = [], "", False
    for ch in str(dn or ""):
        if esc:
            cur += ch
            esc = False
        elif ch == "\\":
            esc = True
        elif ch in ",+":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    pairs = []
    for part in out:
        if "=" in part:
            k, v = part.split("=", 1)
            pairs.append((k.strip().upper(), v.strip()))
    return pairs


def same_dn(a, b):
    """The same distinguished name, whatever the order and spacing (a self-signed certificate: subject = issuer)."""
    pa, pb = dn_pairs(a), dn_pairs(b)
    return bool(pa) and sorted((k, v.lower()) for k, v in pa) == sorted((k, v.lower()) for k, v in pb)


def cert_record(der, now=None):
    """A certificate (DER) -> {subject, issuer, sans, not_after, expires, days, self_signed}."""
    c = x509.load_der_x509_certificate(der)
    na = c.not_valid_after_utc if hasattr(c, "not_valid_after_utc") else utc(c.not_valid_after)
    try:
        ext = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        sans = [str(n) for n in ext.get_values_for_type(x509.DNSName)] + [str(n) for n in ext.get_values_for_type(x509.IPAddress)]
    except x509.ExtensionNotFound:
        sans = []
    subject, issuer = c.subject.rfc4514_string(), c.issuer.rfc4514_string()
    return {"subject": subject, "issuer": issuer, "sans": sans, "not_after": na.strftime("%Y-%m-%d %H:%M UTC"),
            "expires": na.strftime("%Y-%m-%d"), "days": days_left(na, now), "self_signed": c.subject == c.issuer,
            "parts": [[PART.get(a.oid.dotted_string, a.oid.dotted_string), a.value] for a in c.subject]}


def tls_der(host, port, timeout):
    """The certificate a TLS server presents (DER), not checked: an expired or untrusted one is read all the same."""
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


def _first(parts, *names):
    for k, v in parts:
        if k in names:
            return v
    return ""


def csr_spec(cur, name, fields=None):
    """The body of vCenter's tls-csr call: the current certificate's subject and names (cur: a cert_record),
    a field set in fields over it. vCenter's API wants organization, organization_unit, locality,
    state_or_province and country; one the certificate does not have goes empty (the PKI sets the
    subject by its own rules)."""
    f = dict((k, v) for k, v in (fields or {}).items() if v not in (None, ""))
    parts = cur.get("parts") or []
    spec = {"common_name": _first(parts, "commonName") or name,
            "organization": _first(parts, "organizationName"),
            "organization_unit": _first(parts, "organizationalUnitName"),
            "locality": _first(parts, "localityName"),
            "state_or_province": _first(parts, "stateOrProvinceName"),
            "country": _first(parts, "countryName"),
            "email_address": _first(parts, "emailAddress")}
    for k in CSR_FIELDS:
        if k in f:
            spec[k] = str(f[k])
    if f.get("key_size"):
        spec["key_size"] = int(f["key_size"])
    names = list(cur.get("sans") or [])
    if spec["common_name"] not in names:
        names.insert(0, spec["common_name"])
    spec["subject_alt_name"] = names
    return spec


def csr_names(pem):
    """A CSR's subject and names: (subject, [names])."""
    r = x509.load_pem_x509_csr(pem.encode() if isinstance(pem, str) else pem)
    try:
        ext = r.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        names = [str(n) for n in ext.get_values_for_type(x509.DNSName)] + [str(n) for n in ext.get_values_for_type(x509.IPAddress)]
    except x509.ExtensionNotFound:
        names = []
    return r.subject.rfc4514_string(), names


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

    def tls_csr(self, name, spec):
        if self.old:
            code, d = self.call("POST", "/rest" + CSR_PATH, {"spec": spec})
            d = d.get("value") if isinstance(d, dict) and "value" in d else d
        else:
            code, d = self.call("POST", "/api" + CSR_PATH, spec)
        if code == 404:
            raise RuntimeError("vCenter %s has no certificate API (%s, HTTP 404): it comes with vCenter 6.7 Update 2 and later - "
                               "make the CSR in the vSphere Client (Administration > Certificate Management > Machine SSL "
                               "Certificate > Generate Certificate Signing Request)" % (name, CSR_PATH))
        if code in (401, 403):
            raise RuntimeError("vCenter %s refused to make the CSR (HTTP %d: %s): the account needs the privileges "
                               "CertificateManagement.Manage and CertificateManagement.Administer - administrator@vsphere.local, a member "
                               "of the single sign-on group CAAdmins, or a role with Certificate Management given as a Global Permission"
                               % (name, code, error_text(d)))
        if code not in (200, 201) or not isinstance(d, dict) or not d.get("csr"):
            raise RuntimeError("vCenter %s did not make the CSR (HTTP %s: %s) - if it names a subject field, set it in "
                               "vmware_cert_csr_fields (the PKI sets the subject by its own rules)" % (name, code, error_text(d)))
        return d["csr"]


def error_text(d):
    """vCenter's error body as one line: its messages, else the text."""
    if isinstance(d, dict):
        v = d.get("value") if isinstance(d.get("value"), dict) else d
        msgs = [m.get("default_message", "") for m in (v.get("messages") or []) if isinstance(m, dict)]
        if msgs:
            return "; ".join(x for x in msgs if x)
        return json.dumps(d)[:300]
    return str(d or "")[:300]


def _place(h):
    """(cluster, datacenter) of a host: its cluster ('' for a host on its own) and datacenter."""
    cluster, p = "", h.parent
    if isinstance(p, vim.ClusterComputeResource):
        cluster = p.name
    while p is not None and not isinstance(p, vim.Datacenter):
        p = getattr(p, "parent", None)
    return cluster, (p.name if p is not None else "")


def _read(content, name, esxi, timeout, certs, not_checked):
    host, port = host_port(name)
    try:
        rec = cert_record(tls_der(host, port, timeout))
        rec.pop("parts", None)
        rec.update(vcenter=name, kind="vcenter", name=host, port=port, cluster="", datacenter="", status="",
                   product=getattr(content.about, "fullName", "") or "")
        certs.append(rec)
    except Exception as e:                      # noqa: BLE001
        not_checked.append({"vcenter": name, "kind": "vcenter", "name": host,
                            "why": "its certificate could not be read on port %d: %s" % (port, e)})
    if not esxi:
        return None
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.HostSystem], True)
    try:
        hosts = list(view.view)
    finally:
        view.Destroy()
    now = datetime.datetime.now(datetime.timezone.utc)
    for h in sorted(hosts, key=lambda x: x.name):
        try:
            state = str(h.runtime.connectionState)
            if state != "connected":
                not_checked.append({"vcenter": name, "kind": "esxi", "name": h.name, "why": "not connected (%s)" % state})
                continue
            cm = h.configManager.certificateManager
            info = cm.certificateInfo if cm is not None else None
            if info is None or info.notAfter is None:
                not_checked.append({"vcenter": name, "kind": "esxi", "name": h.name, "why": "the host reports no certificate"})
                continue
            cluster, dc = _place(h)
            na = utc(info.notAfter)
            certs.append({"vcenter": name, "kind": "esxi", "name": h.name, "cluster": cluster, "datacenter": dc,
                          "subject": info.subject or "", "issuer": info.issuer or "", "sans": [],
                          "not_after": na.strftime("%Y-%m-%d %H:%M UTC"), "expires": na.strftime("%Y-%m-%d"),
                          "days": days_left(na, now), "self_signed": same_dn(info.subject, info.issuer),
                          "status": str(info.status or ""), "product": ""})
        except Exception as e:                  # noqa: BLE001
            not_checked.append({"vcenter": name, "kind": "esxi", "name": getattr(h, "name", "?"),
                                "why": "its certificate could not be read: %s" % fault_text(e)})
    return None


def _host(content, name):
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.HostSystem], True)
    try:
        found = [h for h in view.view if h.name == name]
    finally:
        view.Destroy()
    if not found:
        raise RuntimeError("vCenter has no host %s (any more)" % name)
    return found[0]


def make_csrs(module, items, fields, validate_certs, timeout):
    out = []
    for it in items:
        r = {"vcenter": it.get("vcenter", ""), "kind": it.get("kind", ""), "name": it.get("name", ""),
             "csr": "", "csr_subject": "", "csr_names": [], "error": ""}
        out.append(r)
        if module.check_mode:
            continue
        try:
            host, port = host_port(r["vcenter"])
            if r["kind"] == "vcenter":
                cur = cert_record(tls_der(host, port, timeout))
                api = Api(host, port, validate_certs, timeout)
                api.login(r["vcenter"])
                try:
                    r["csr"] = api.tls_csr(r["vcenter"], csr_spec(cur, host, fields))
                finally:
                    api.logout()
            elif r["kind"] == "esxi":
                si = connect(r["vcenter"], validate_certs, timeout)
                try:
                    cm = _host(si.RetrieveContent(), r["name"]).configManager.certificateManager
                    r["csr"] = cm.GenerateCertificateSigningRequest(useIpAddressAsCommonName=False)
                finally:
                    disconnect(si)
            else:
                raise RuntimeError("kind must be vcenter or esxi, not %s" % r["kind"])
            r["csr_subject"], r["csr_names"] = csr_names(r["csr"])
        except Exception as e:                  # noqa: BLE001
            r["csr"] = ""
            r["error"] = fault_text(e) if not isinstance(e, RuntimeError) else str(e)
    return out


def main():
    module = AnsibleModule(
        argument_spec=dict(action=dict(type="str", choices=["read", "csr"], default="read"),
                           vcenters=dict(type="list", elements="str", default=[]),
                           esxi=dict(type="bool", default=True),
                           items=dict(type="list", elements="dict", default=[]),
                           fields=dict(type="dict", default={}),
                           validate_certs=dict(type="bool", default=True),
                           timeout=dict(type="int", default=30)),
        supports_check_mode=True,
    )
    p = module.params
    if not HAS_PYVMOMI:
        module.fail_json(msg="pyVmomi is not installed in the execution environment (the vmware.vmware collection needs it too)")
    if not HAS_CRYPTO:
        module.fail_json(msg="the Python library cryptography is not in the execution environment (ansible-core needs it: an "
                             "execution environment without it is broken - see docs/EXECUTION_ENVIRONMENT.md)")
    if p["action"] == "csr":
        made = make_csrs(module, p["items"], p["fields"], p["validate_certs"], p["timeout"])
        module.exit_json(changed=any(m["csr"] for m in made), made=made, check_mode=module.check_mode)
    certs, not_checked = [], []
    _, statuses = each_vcenter(module, lambda si, content, name: _read(content, name, p["esxi"], p["timeout"], certs, not_checked),
                               p["vcenters"], p["validate_certs"], "the certificates", timeout=p["timeout"])
    module.exit_json(changed=False, certs=certs, not_checked=not_checked, vcenters=statuses)


if __name__ == "__main__":
    main()
