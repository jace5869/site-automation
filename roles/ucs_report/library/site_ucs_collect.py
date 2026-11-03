#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Read Cisco UCS Manager domains through their XML API - queries only, Python's standard library only (no ucsmsdk
or collection needed). For each domain: log in, read each class, log out; keep going when one class fails: the report
shows what could not be read and why."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_ucs_collect
short_description: Read Cisco UCS Manager domains through the XML API (queries only)
description:
  - For each UCS Manager domain (the fabric interconnects' cluster address), logs in (aaaLogin), reads each class
    with configResolveClass, and logs out (aaaLogout) - always, also after a failure, so no session is left open.
    Every call is an HTTPS POST to /nuova; this module sends no method but aaaLogin, aaaRefresh, aaaLogout,
    configResolveClass, configResolveClasses, configResolveDn and configResolveDns - it cannot change anything.
  - A class this UCS Manager does not know, or one that fails, is recorded for that class and the others still run.
    A domain that cannot be reached or refuses the login is recorded once; its classes are skipped.
  - The reason a domain cannot be read says what to do (DNS, port, proxy, certificate, TLS, the login).
  - The account comes from UCSM_USERNAME and UCSM_PASSWORD (an AAP "Cisco UCS Manager" credential). UCS Manager's
    read-only role is enough. An account of a remote authentication domain (LDAP, TACACS+, RADIUS) logs in as
    ucs-DOMAIN\\user when the domain is not the default one.
options:
  domains: {description: UCS Manager addresses (names or IPs; https:// and a port optional)., type: list, elements: str, required: true}
  classes: {description: "The classes to read: class (the class ID), max (most objects to keep)", type: list, elements: dict, required: true}
  validate_certs: {description: Check UCS Manager's certificate., type: bool, default: true}
  ca_path: {description: A CA file to check it with (e.g. your own CA)., type: str}
  timeout: {description: Seconds for one request., type: int, default: 60}
  tls_legacy:
    description: "Connect to an older UCS Manager whose TLS the execution environment refuses by default (legacy
      renegotiation, small keys, SHA-1)."
    type: bool
    default: false
author: site automation
'''

RETURN = r'''
domains:
  description: One entry per domain.
  type: list
  returned: always
  sample: [{address: ucs01.example.mil, reachable: true, error: "", version: "4.2(3d)", priv: read-only,
            data: {computeBlade: {objects: [{dn: sys/chassis-1/blade-1, model: UCSB-B200-M5, serial: FLM0000001}],
                                  error: "", truncated: false}}}]
'''

import os
import socket
import ssl
import time
import xml.etree.ElementTree as ET

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.six.moves.urllib.error import HTTPError, URLError
from ansible.module_utils.six.moves.urllib.request import Request, build_opener, HTTPSHandler
try:
    from urllib.request import getproxies, proxy_bypass
except ImportError:                      # Python 2
    from urllib import getproxies, proxy_bypass

# The only XML API methods this module sends: log in and out, and read. Everything else (configConfMo and the like)
# is refused before it leaves.
READ_METHODS = ("aaaLogin", "aaaRefresh", "aaaLogout", "configResolveClass", "configResolveClasses", "configResolveDn",
                "configResolveDns")
SESSION_GONE = ("552", "555")            # Authorization required / session not found: the cookie ran out - log in again


class Unreachable(Exception):
    """The domain itself cannot be read (not just one class). kind: timeout, dns, refused, tls, auth, other."""

    def __init__(self, msg, kind="other"):
        Exception.__init__(self, msg)
        self.kind = kind


class ApiError(Exception):
    """UCS Manager answered with an error (errorCode, errorDescr)."""

    def __init__(self, code, descr):
        Exception.__init__(self, "error %s: %s" % (code, descr) if descr else "error %s" % code)
        self.code = str(code)


def kind_of(e):
    """What sort of network failure an exception from urlopen is."""
    reason = getattr(e, "reason", e)
    text = str(reason)
    if isinstance(reason, socket.timeout) or "timed out" in text.lower():
        return "timeout"
    if isinstance(reason, socket.gaierror) or "Name or service not known" in text or "nodename nor servname" in text \
            or "Temporary failure in name resolution" in text:
        return "dns"
    if isinstance(reason, ssl.SSLError) or isinstance(reason, getattr(ssl, "CertificateError", ssl.SSLError)) or "SSL" in text \
            or "CERTIFICATE" in text:
        return "tls"
    if "refused" in text.lower() or "No route to host" in text or "unreachable" in text.lower():
        return "refused"
    return "other"


TLS_HINTS = (
    ("CERTIFICATE_VERIFY_FAILED", "UCS Manager's certificate is not trusted: set ucs_ca_path to its CA file, or "
                                  "ucs_validate_certs: false (docs/UCS.md). A curl with -k skips this check"),
    ("ostname mismatch", "the address is not one of the names on UCS Manager's certificate: list the domain by the name its "
                         "certificate has, or ucs_validate_certs: false"),
    ("doesn't match", "the address is not one of the names on UCS Manager's certificate: list the domain by the name its "
                      "certificate has, or ucs_validate_certs: false"),
    ("UNSAFE_LEGACY_RENEGOTIATION_DISABLED", "this UCS Manager's TLS is older than the execution environment allows: "
                                             "ucs_tls_legacy: true (or update UCS Manager)"),
    ("KEY_TOO_SMALL", "UCS Manager's key is smaller than the execution environment allows: ucs_tls_legacy: true, or a new "
                      "key ring and certificate in UCS Manager"),
    ("MD_TOO_WEAK", "UCS Manager's certificate is signed with SHA-1: ucs_tls_legacy: true, or a new certificate"),
    ("WRONG_VERSION_NUMBER", "that port does not speak HTTPS: give UCS Manager's cluster address (port 443)"),
    ("UNSUPPORTED_PROTOCOL", "UCS Manager offers only TLS versions the execution environment refuses: ucs_tls_legacy: true, "
                             "or allow TLS 1.2 in UCS Manager (Communication Services, HTTPS)"),
    ("HANDSHAKE_FAILURE", "UCS Manager and the execution environment share no cipher: ucs_tls_legacy: true, or check "
                          "UCS Manager's HTTPS cipher suite (Communication Services)"),
)


def proxy_for(url):
    """The proxy urllib sends this URL through (https_proxy in the environment, not in no_proxy), or ''."""
    try:
        host = url.split("://", 1)[-1].split("/", 1)[0].rsplit("@", 1)[-1]
        scheme = url.split("://", 1)[0] if "://" in url else "https"
        proxy = getproxies().get(scheme, "")
        if proxy and not proxy_bypass(host.split(":")[0]):
            return proxy
    except Exception:  # pylint: disable=broad-except
        pass
    return ""


def explain(text, kind, address, timeout):
    """The reason a domain could not be read, with what to do."""
    hint = ""
    if kind == "tls":
        hint = next((h for k, h in TLS_HINTS if k in text), "a TLS problem between the execution environment and UCS Manager")
    elif kind == "dns":
        hint = ("the name does not resolve where the job runs (the AAP execution node's container, not your shell): "
                "use the name DNS knows there, or the IP address")
    elif kind == "timeout":
        hint = ("no answer within %s s, asked twice: a firewall between the AAP execution node and UCS Manager's cluster "
                "address on port 443 (HTTPS - the XML API; SSH on port 22 is not used), or a slow UCS Manager "
                "(ucs_timeout: 180)" % timeout)
    elif kind == "refused":
        hint = ("nothing answered on port 443 from the AAP execution node: UCS Manager's cluster address (the fabric "
                "interconnects' virtual IP), a firewall, or HTTPS turned off in UCS Manager (Communication Services)")
    proxy = proxy_for(address if "://" in address else "https://" + address)
    if proxy and kind in ("timeout", "refused", "other", "tls"):
        hint += ("; it went through the proxy %s (https_proxy where the job runs): add the domain to no_proxy"
                 % proxy.split("@")[-1])
    return text + (" - " + hint if hint else "")


def login_hint(code, descr):
    """Why a login was refused, with what to do."""
    return ("UCS Manager refused the login (error %s: %s): check the Cisco UCS Manager credential - the user exists on "
            "this domain (or in its LDAP / TACACS+ / RADIUS), its password, not locked; an account of an authentication "
            "domain that is not the default one logs in as ucs-DOMAIN\\user" % (code, descr or "authentication failed"))


def element(method, attrs, children=None):
    """<method a="..."/> as bytes (the values escaped by ElementTree)."""
    if method not in READ_METHODS:
        raise ValueError("refused to send %s: this module only reads (%s)" % (method, ", ".join(READ_METHODS)))
    e = ET.Element(method, dict((k, str(v)) for k, v in attrs.items()))
    for c in children or []:
        e.append(c)
    return ET.tostring(e)


class Client(object):
    """One UCS Manager domain. Sends only READ_METHODS."""

    def __init__(self, address, user, password, context, timeout, opener=None, retries=1):
        self.address, self.timeout, self.retries = address, timeout, retries
        self.user, self.password = user, password
        base = address if "://" in address else "https://" + address
        self.url = base.rstrip("/") + "/nuova"
        self.opener = opener or build_opener(HTTPSHandler(context=context))
        self.cookie, self.version, self.privileges = "", "", ""

    def post(self, body):
        """The answer's root element. Raises Unreachable when the domain does not answer at all, ApiError for an
        error answer."""
        req = Request(self.url, data=body, headers={"Content-Type": "application/xml"})
        tries = 0
        while True:
            try:
                r = self.opener.open(req, timeout=self.timeout)
                text = r.read()
                break
            except HTTPError as e:
                raise Unreachable("HTTP %s from %s - not UCS Manager's XML API? Give the cluster address of the fabric "
                                  "interconnects" % (e.code, self.url), "other")
            except (URLError, ssl.SSLError, OSError) as e:
                kind = kind_of(e)
                if kind == "timeout" and tries < self.retries:
                    tries += 1
                    continue
                raise Unreachable(str(getattr(e, "reason", e)) or e.__class__.__name__, kind)
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            raise Unreachable("the answer from %s is not XML (%s) - not UCS Manager's XML API?"
                              % (self.url, text[:120].decode("utf-8", "replace")), "other")
        if root.tag == "error" or root.get("errorCode"):
            raise ApiError(root.get("errorCode", "?"), root.get("errorDescr", ""))
        return root

    def login(self):
        try:
            root = self.post(element("aaaLogin", {"inName": self.user, "inPassword": self.password}))
        except ApiError as e:
            raise Unreachable(login_hint(e.code, str(e).split(": ", 1)[-1]), "auth")
        self.cookie = root.get("outCookie", "")
        if not self.cookie:
            raise Unreachable("UCS Manager gave no session (no outCookie in its aaaLogin answer)", "auth")
        self.version, self.privileges = root.get("outVersion", ""), root.get("outPriv", "")

    def logout(self):
        if self.cookie:
            try:
                self.post(element("aaaLogout", {"inCookie": self.cookie}))
            except (Unreachable, ApiError):
                pass
            self.cookie = ""

    def resolve_class(self, class_id):
        """Every object of the class: [{attribute: value, _class: tag}]. The session ran out: log in once more."""
        for attempt in (1, 2):
            try:
                root = self.post(element("configResolveClass", {"cookie": self.cookie, "classId": class_id,
                                                                "inHierarchical": "false"}))
                break
            except ApiError as e:
                if e.code in SESSION_GONE and attempt == 1:
                    self.login()
                    continue
                raise
        out = []
        for configs in root.iter("outConfigs"):
            for o in configs:
                d = dict(o.attrib)
                d["_class"] = o.tag
                out.append(d)
        return out


def collect(client, classes):
    """Every class of one domain -> {address, reachable, error, version, priv, data}."""
    res = {"address": client.address, "reachable": True, "error": "", "version": "", "priv": "", "data": {}}
    try:
        client.login()
        res["version"], res["priv"] = client.version, client.privileges
        answered, slow = False, 0
        for c in classes:
            cid, cap = c["class"], int(c.get("max") or 20000)
            started = time.time()
            item = {"objects": [], "error": "", "truncated": False}
            try:
                objs = client.resolve_class(cid)
                item["objects"], item["truncated"] = objs[:cap], len(objs) > cap
                answered, slow = True, 0
            except ApiError as e:
                item["error"] = str(e)
            except Unreachable as e:
                # the domain answered before: one slow class is that class's problem, not the domain's
                if answered and e.kind == "timeout" and slow < 1:
                    slow += 1
                    item["error"] = "no answer within %s s, asked twice - ucs_timeout: 180 gives it longer" % client.timeout
                else:
                    raise
            item["seconds"] = round(time.time() - started, 1)
            res["data"][cid] = item
    except Unreachable as e:
        res["reachable"] = False
        res["error"] = str(e) if e.kind == "auth" else explain(str(e), e.kind, client.address, client.timeout)
        res["data"] = {}
    finally:
        client.logout()                  # always: an open session counts against the account's session limit
    return res


def main():
    module = AnsibleModule(
        argument_spec=dict(domains=dict(type="list", elements="str", required=True),
                           classes=dict(type="list", elements="dict", required=True),
                           validate_certs=dict(type="bool", default=True), ca_path=dict(type="str"),
                           timeout=dict(type="int", default=60), tls_legacy=dict(type="bool", default=False)),
        supports_check_mode=True,
    )
    p = module.params
    user, password = os.environ.get("UCSM_USERNAME", ""), os.environ.get("UCSM_PASSWORD", "")
    if not user or not password:
        module.fail_json(msg='No UCS Manager account: attach a credential of type "Cisco UCS Manager" to this job template '
                             '(aap/credential_types/cisco_ucs_manager.yml).')
    if not p["domains"]:
        module.fail_json(msg="ucs_domains is empty: list the UCS Manager addresses - each domain's fabric interconnect "
                             "cluster address (docs/UCS.md).")
    for c in p["classes"]:
        if not c.get("class"):
            module.fail_json(msg="a class needs its class ID (class: computeBlade): %s" % c)
    if p["validate_certs"]:
        context = ssl.create_default_context(cafile=p["ca_path"] or None)
    else:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    if p["tls_legacy"]:
        context.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
        try:
            context.set_ciphers("DEFAULT:@SECLEVEL=1")
        except ssl.SSLError:                 # FIPS: the policy decides
            pass
    started = time.time()
    out = [collect(Client(a, user, password, context, p["timeout"]), p["classes"]) for a in p["domains"]]
    module.exit_json(changed=False, domains=out, seconds=round(time.time() - started, 1),
                     read_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)))


if __name__ == "__main__":
    main()
