#!/usr/bin/python
# -*- coding: utf-8 -*-
"""vCenter single sign-on, read through vCenter's REST API (vCenter 8.0), and the OCSP responders smart card logins
depend on: vCenter's SSO services (sts, lookupsvc, trustmanagement ... and vmdird / vmafdd), the STS signing
certificate's expiry, the identity providers, a login by a test domain account (the surest proof that domain users
can still log in), and each OCSP responder asked about a certificate (a real OCSP request: the answer, its status,
how fresh). Read-only: the test account's session is closed again. Runs on the AAP side."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vcenter_sso
short_description: vCenter's single sign-on (services, STS certificate, identity providers, a test login) and OCSP responders
description:
  - For each vCenter (the credential's, and C(vcenters)) with the credential's account - GET /api/vcenter/services and
    /api/appliance/services (the SSO services named in C(services)), the STS signing certificate
    (/api/vcenter/certificate-management/vcenter/signing-certificate) and the identity providers (/api/vcenter/identity/providers).
  - With C(test_user) a login as that domain account (POST /api/session), then its logout.
  - Each OCSP responder in C(ocsp) - with a certificate and its issuer, a real OCSP request (the answer must be successful,
    the certificate good, the answer not past its next update); with a URL only, that it answers HTTP at all.
options:
  vcenters: {description: More vCenters besides the credential's (VMWARE_HOST)., type: list, elements: str, default: []}
  services: {description: The SSO services that must be started (names as vCenter lists them)., type: list, elements: str, default: []}
  test_user: {description: "A domain account that logs in ('' = no login test).", type: str, default: ''}
  test_password: {description: Its password., type: str, default: '', no_log: true}
  ocsp: {description: "[{url, cert: PEM, issuer: PEM}] - cert and issuer optional.", type: list, elements: dict, default: []}
  validate_certs: {description: Check vCenter's HTTPS certificate., type: bool, default: true}
  timeout: {description: Seconds per request., type: int, default: 30}
  now: {description: The time to count expiry from (tests)., type: str, default: ''}
author: site automation
'''

RETURN = r'''
vcenters:
  description: "Per vCenter: {name, error, services: [{name, state, health, found}], sts: [{subject, expires, days}],
                providers: [{name, type, default, domains}], login: {user, ok, code, why}, notes: []}"
  type: list
  returned: always
ocsp:
  description: "Per responder: {url, ok, mode (request / reach), status, cert_status, this_update, next_update, ms, why}"
  type: list
  returned: always
'''

import base64
import datetime
import json
import os
import re
import ssl
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_vcenters import host_port, vcenter_list
from ansible.module_utils.six.moves.urllib import error as urlerror
from ansible.module_utils.six.moves.urllib import request as urlrequest

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.x509 import ocsp
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

PEM_RE = re.compile(r"-----BEGIN CERTIFICATE-----[A-Za-z0-9+/=\s\\n]+?-----END CERTIFICATE-----")
SIGNING_PATH = "/api/vcenter/certificate-management/vcenter/signing-certificate"


def utc_now(now=""):
    if now:
        return datetime.datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    return datetime.datetime.now(datetime.timezone.utc)


def _aware(d):
    return d if d is None or d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def not_after(c):
    return _aware(getattr(c, "not_valid_after_utc", None) or c.not_valid_after)


def pems_in(obj):
    """Every PEM certificate anywhere in a JSON answer (vCenter's field names vary between releases)."""
    return [p.replace("\\n", "\n") for p in PEM_RE.findall(json.dumps(obj) if not isinstance(obj, str) else obj)]


def service_states(vc_services, appl_services, wanted):
    """vCenter's (vmon) and the appliance's service lists -> [{name, state, health, found}] for the wanted ones."""
    both = {}
    for src in (appl_services, vc_services):
        if isinstance(src, dict):
            for k, v in src.items():
                both[k.lower()] = v if isinstance(v, dict) else {"state": str(v)}
        elif isinstance(src, list):                 # [{key, value}] in older answers
            for x in src:
                if isinstance(x, dict) and "key" in x:
                    both[str(x["key"]).lower()] = x.get("value") or {}
    out = []
    for w in wanted:
        s = both.get(w.lower())
        out.append({"name": w, "found": s is not None, "state": (s or {}).get("state", ""), "health": (s or {}).get("health", "")})
    return out


class Rest(object):
    def __init__(self, host, port, validate_certs, timeout):
        self.base = "https://%s:%d" % ("[%s]" % host if ":" in host else host, port)
        self.timeout = timeout
        self.ctx = ssl.create_default_context()
        if not validate_certs:
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE

    def call(self, method, path, token="", user=None):
        headers = {"Accept": "application/json"}
        if user is not None:
            headers["Authorization"] = "Basic " + base64.b64encode(("%s:%s" % user).encode("utf-8")).decode("ascii")
        elif token:
            headers["vmware-api-session-id"] = token
        req = urlrequest.Request(self.base + path, headers=headers)
        req.get_method = lambda: method
        try:
            resp = urlrequest.urlopen(req, timeout=self.timeout, context=self.ctx)
            code, raw = resp.getcode(), resp.read()
        except urlerror.HTTPError as e:
            code, raw = e.code, e.read()
        try:
            return code, json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            return code, raw.decode("utf-8", "replace")[:300]

    def session(self, user, password):
        code, d = self.call("POST", "/api/session", user=(user, password))
        return code, d if code in (200, 201) and isinstance(d, str) else ""

    def logout(self, token):
        if token:
            try:
                self.call("DELETE", "/api/session", token=token)
            except Exception:                   # noqa: BLE001
                pass


def read_vcenter(name, p, now):
    host, port = host_port(name)
    r = Rest(host, port, p["validate_certs"], p["timeout"])
    out = {"name": name, "error": "", "services": [], "sts": [], "providers": [], "login": {}, "notes": []}
    try:
        code, tok = r.session(os.environ.get("VMWARE_USER", ""), os.environ.get("VMWARE_PASSWORD", ""))
    except (urlerror.URLError, OSError, ssl.SSLError) as e:
        out["error"] = "cannot reach vCenter %s: %s" % (name, getattr(e, "reason", e))
        return out
    if not tok and code == 404:                 # no /api: not a vCenter 7 or 8 REST API (an older vCenter, a simulator)
        out["notes"].append("vCenter %s has no REST API at /api (HTTP 404): single sign-on is read on vCenter 8.0" % name)
        return out
    if not tok:
        out["error"] = ("vCenter %s refused the credential's login (HTTP %s): single sign-on itself may be down, or the VMware vCenter "
                        "credential's user name or password is wrong" % (name, code))
        if p["test_user"]:
            out["login"] = test_login(r, p["test_user"], p["test_password"])
        return out
    try:
        vc_code, vc = r.call("GET", "/api/vcenter/services", token=tok)
        ap_code, ap = r.call("GET", "/api/appliance/services", token=tok)
        if vc_code != 200 and ap_code != 200:
            out["notes"].append("the service lists were not readable (HTTP %s / %s): the account needs read access to the appliance" % (vc_code, ap_code))
        else:
            out["services"] = service_states(vc if vc_code == 200 else {}, ap if ap_code == 200 else {}, p["services"])
        code, d = r.call("GET", SIGNING_PATH, token=tok)
        if code == 200 and HAS_CRYPTO:
            active = d.get("active_cert_chain") if isinstance(d, dict) and d.get("active_cert_chain") else d
            seen = set()
            for pem in pems_in(active):
                c = x509.load_pem_x509_certificate(pem.encode("ascii"))
                fp = c.fingerprint(hashes.SHA256())
                if fp in seen:                  # every certificate of the active chain once: the signing one and the CA above it
                    continue
                seen.add(fp)
                na = not_after(c)
                out["sts"].append({"subject": c.subject.rfc4514_string(), "expires": na.strftime("%Y-%m-%d"),
                                   "days": int((na - now).total_seconds() // 86400)})
        elif code == 200:
            out["notes"].append("the STS certificate was read but not judged: the Python package cryptography is missing")
        else:
            out["notes"].append("the STS signing certificate was not readable (HTTP %s): the account needs CertificateManagement.Administer "
                                "(read)" % code)
        code, d = r.call("GET", "/api/vcenter/identity/providers", token=tok)
        if code == 200 and isinstance(d, list):
            for x in d:
                out["providers"].append({"name": x.get("name") or x.get("provider") or "", "type": x.get("config_tag") or "",
                                         "default": bool(x.get("is_default")), "domains": x.get("domain_names") or []})
        elif code != 404:
            out["notes"].append("the identity providers were not readable (HTTP %s)" % code)
    finally:
        r.logout(tok)
    if p["test_user"]:
        out["login"] = test_login(r, p["test_user"], p["test_password"])
    return out


def test_login(r, user, password):
    """The test domain account logs in (and out). 403 = authenticated, no permission in vCenter: SSO works."""
    try:
        code, tok = r.session(user, password)
    except (urlerror.URLError, OSError, ssl.SSLError) as e:
        return {"user": user, "ok": False, "code": 0, "why": "vCenter did not answer: %s" % getattr(e, "reason", e)}
    if tok:
        r.logout(tok)
        return {"user": user, "ok": True, "code": code, "why": ""}
    if code == 403:
        return {"user": user, "ok": True, "code": code, "why": "authenticated, but the account has no permission in vCenter"}
    return {"user": user, "ok": False, "code": code,
            "why": "vCenter refused the login (HTTP %s): its identity source (Active Directory over LDAP(S), or the federated provider) "
                   "does not answer or does not know the account - or the account is locked, disabled or its password expired" % code}


def check_ocsp(x, timeout, now):
    url = x.get("url") or ""
    rec = {"url": url, "ok": False, "mode": "reach", "status": "", "cert_status": "", "this_update": "", "next_update": "", "ms": 0, "why": ""}
    t0 = time.time()
    try:
        if x.get("cert") and x.get("issuer"):
            if not HAS_CRYPTO:
                rec["why"] = "the Python package cryptography is missing (needed for an OCSP request)"
                return rec
            rec["mode"] = "request"
            cert = x509.load_pem_x509_certificate(x["cert"].encode("ascii"))
            issuer = x509.load_pem_x509_certificate(x["issuer"].encode("ascii"))
            body = ocsp.OCSPRequestBuilder().add_certificate(cert, issuer, hashes.SHA1()).build().public_bytes(serialization.Encoding.DER)
            req = urlrequest.Request(url, data=body, headers={"Content-Type": "application/ocsp-request", "Accept": "application/ocsp-response"})
            resp = urlrequest.urlopen(req, timeout=timeout)
            raw = resp.read()
            rec["ms"] = int((time.time() - t0) * 1000)
            o = ocsp.load_der_ocsp_response(raw)
            rec["status"] = o.response_status.name.lower()
            if o.response_status != ocsp.OCSPResponseStatus.SUCCESSFUL:
                rec["why"] = "the responder answered %s, not successful" % rec["status"]
                return rec
            rec["cert_status"] = o.certificate_status.name.lower()
            tu = _aware(getattr(o, "this_update_utc", None) or o.this_update)
            nu = _aware(getattr(o, "next_update_utc", None) or o.next_update)
            rec["this_update"] = tu.strftime("%Y-%m-%d %H:%M UTC") if tu else ""
            rec["next_update"] = nu.strftime("%Y-%m-%d %H:%M UTC") if nu else ""
            if nu and nu < now:
                rec["why"] = "the answer is stale: its next update (%s) has passed - the responder serves old revocation data" % rec["next_update"]
            elif o.certificate_status != ocsp.OCSPCertStatus.GOOD:
                rec["why"] = "the responder says the test certificate is %s" % rec["cert_status"]
            else:
                rec["ok"] = True
            return rec
        resp = urlrequest.urlopen(urlrequest.Request(url), timeout=timeout)
        rec["status"] = "http %d" % resp.getcode()
        rec["ok"] = True
    except urlerror.HTTPError as e:
        rec["status"] = "http %d" % e.code
        if rec["mode"] == "reach":                  # an OCSP responder answers a bare GET with 4xx: it is up
            rec["ok"] = e.code < 500
            rec["why"] = "" if rec["ok"] else "the responder answered HTTP %d" % e.code
        else:
            rec["why"] = "the responder answered HTTP %d to the OCSP request" % e.code
    except (urlerror.URLError, OSError) as e:
        rec["why"] = "no answer: %s" % getattr(e, "reason", e)
    except ValueError as e:
        rec["why"] = "the answer is not an OCSP response: %s" % e
    rec["ms"] = rec["ms"] or int((time.time() - t0) * 1000)
    return rec


def main():
    module = AnsibleModule(argument_spec=dict(
        vcenters=dict(type="list", elements="str", default=[]),
        services=dict(type="list", elements="str", default=[]),
        test_user=dict(type="str", default=""),
        test_password=dict(type="str", default="", no_log=True),
        ocsp=dict(type="list", elements="dict", default=[]),
        validate_certs=dict(type="bool", default=True),
        timeout=dict(type="int", default=30),
        now=dict(type="str", default=""),
    ), supports_check_mode=True)
    p = module.params
    now = utc_now(p["now"])
    vcs = [read_vcenter(n, p, now) for n in vcenter_list(p["vcenters"])]
    oc = [check_ocsp(x, p["timeout"], now) for x in p["ocsp"] if x.get("url")]
    module.exit_json(changed=False, vcenters=vcs, ocsp=oc)


if __name__ == "__main__":
    main()
