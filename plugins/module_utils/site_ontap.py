# -*- coding: utf-8 -*-
"""NetApp ONTAP's REST API, the parts every ONTAP module shares: why a cluster could not be reached (and what to do), an
ONTAP error body as one line, the TLS context. Moved out of roles/ontap_report/library/site_ontap_collect.py (1.123.0), so
site_ontap_volume (NetApp - expand a volume) says the same things the same way."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import json
import socket
import ssl

try:
    from urllib.request import getproxies, proxy_bypass
except ImportError:                      # Python 2
    from urllib import getproxies, proxy_bypass


class Unreachable(Exception):
    """The cluster itself cannot be reached (not just one query). kind: timeout, dns, refused, tls, auth, other."""

    def __init__(self, msg, kind="other"):
        Exception.__init__(self, msg)
        self.kind = kind


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
    ("CERTIFICATE_VERIFY_FAILED", "the cluster's certificate is not trusted: set ontap_ca_path to its CA file, or "
                                  "ontap_validate_certs: false (docs/NETAPP.md). A curl with -k skips this check"),
    ("ostname mismatch", "the address is not one of the names on the cluster's certificate: list the cluster by the name its "
                         "certificate has, or ontap_validate_certs: false"),
    ("doesn't match", "the address is not one of the names on the cluster's certificate: list the cluster by the name its "
                      "certificate has, or ontap_validate_certs: false"),
    ("UNSAFE_LEGACY_RENEGOTIATION_DISABLED", "this ONTAP's TLS is older than the execution environment allows: "
                                             "ontap_tls_legacy: true (or update ONTAP)"),
    ("KEY_TOO_SMALL", "the cluster's key is smaller than the execution environment allows: ontap_tls_legacy: true, or a new "
                      "certificate on the cluster (security certificate create)"),
    ("MD_TOO_WEAK", "the cluster's certificate is signed with SHA-1: ontap_tls_legacy: true, or a new certificate on the cluster"),
    ("WRONG_VERSION_NUMBER", "that port does not speak HTTPS: give the cluster management address (port 443)"),
    ("UNSUPPORTED_PROTOCOL", "the cluster offers only TLS versions the execution environment refuses: ontap_tls_legacy: true, "
                             "or allow TLS 1.2 on the cluster (security config modify -supported-protocols)"),
    ("HANDSHAKE_FAILURE", "the cluster and the execution environment share no cipher: ontap_tls_legacy: true, or check the "
                          "cluster's security config (FIPS)"),
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
    """The reason a cluster could not be read, with what to do."""
    hint = ""
    if kind == "tls":
        hint = next((h for k, h in TLS_HINTS if k in text), "a TLS problem between the execution environment and the cluster")
    elif kind == "dns":
        hint = ("the name does not resolve where the job runs (the AAP execution node's container, not your shell): "
                "use the name DNS knows there, or the IP address")
    elif kind == "timeout":
        hint = ("no answer within %s s, asked twice: a firewall between the AAP execution node and the cluster's "
                "management address on port 443, or a slow cluster (ontap_timeout: 120)" % timeout)
    elif kind == "refused":
        hint = "nothing answered on port 443 from the AAP execution node: the cluster management address, a firewall"
    proxy = proxy_for(address if "://" in address else "https://" + address)
    if proxy and kind in ("timeout", "refused", "other", "tls"):
        hint += ("; it went through the proxy %s (https_proxy where the job runs): add the cluster to no_proxy"
                 % proxy.split("@")[-1])
    return text + (" - " + hint if hint else "")


def error_text(code, body):
    """An ONTAP error body ({"error": {"message", "code", "target"}}) as one line."""
    try:
        e = json.loads(body or "{}").get("error") or {}
        msg = e.get("message") or ""
        if e.get("target"):
            msg += " (%s)" % e["target"]
        return "HTTP %s: %s" % (code, msg) if msg else "HTTP %s" % code
    except (ValueError, AttributeError):
        return "HTTP %s" % code


def ssl_context(validate_certs=True, ca_path=None, tls_legacy=False):
    """The TLS context for the clusters: their certificates checked (against ca_path, or the execution environment's trust),
    or not; tls_legacy for older ONTAP whose TLS the execution environment refuses by default."""
    if validate_certs:
        context = ssl.create_default_context(cafile=ca_path or None)
    else:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    if tls_legacy:
        context.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
        try:
            context.set_ciphers("DEFAULT:@SECLEVEL=1")
        except ssl.SSLError:                 # FIPS: the policy decides
            pass
    return context
