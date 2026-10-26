# -*- coding: utf-8 -*-
"""The OCSP responders to ask, from the certificates themselves (the VMware health report's vm_sso_ocsp, the network
performance report's net_perf_ocsp, and site_ocsp_certs for both): ocsp_target fills in what an entry leaves out,
ocsp_cert_entry turns a site_ocsp_certs item (a cert file's path) into an entry.

  url     missing: the certificate's own OCSP address (its Authority Information Access extension - every DoD
          certificate carries one)
  issuer  missing: the next certificate in the cert file when it is the issuer, else the issuer certificate the
          certificate's caIssuers address serves (DER, PEM or a .p7c bundle), downloaded here on the AAP side

Runs on the AAP side (a filter). Never raises: what could not be found is in the entry's why."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import re

try:
    from urllib.request import urlopen
except ImportError:                                    # Python 2 - not used by the execution environments
    from urllib2 import urlopen

try:
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import pkcs7
    from cryptography.x509.oid import AuthorityInformationAccessOID, ExtensionOID
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

PEM_RE = re.compile(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", re.S)


def _aia(cert, method):
    """The certificate's http(s) addresses of one Authority Information Access method."""
    try:
        ext = cert.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_INFORMATION_ACCESS).value
    except x509.ExtensionNotFound:
        return []
    return [d.access_location.value for d in ext
            if d.access_method == method and isinstance(d.access_location, x509.UniformResourceIdentifier)
            and d.access_location.value.lower().startswith(("http://", "https://"))]


def _download_issuer(cert, url, timeout):
    """The issuer certificate (PEM text) from a caIssuers address: DER, PEM or PKCS#7 (.p7c)."""
    raw = urlopen(url, timeout=timeout).read(1 << 20)
    found = []
    if b"-----BEGIN CERTIFICATE-----" in raw:
        found = [x509.load_pem_x509_certificate(m.encode("ascii")) for m in PEM_RE.findall(raw.decode("ascii", "replace"))]
    else:
        try:
            found = [x509.load_der_x509_certificate(raw)]
        except ValueError:
            found = pkcs7.load_der_pkcs7_certificates(raw)
    for c in found:
        if c.subject == cert.issuer:
            from cryptography.hazmat.primitives.serialization import Encoding
            return c.public_bytes(Encoding.PEM).decode("ascii")
    raise ValueError("none of its %d certificate(s) is the issuer (%s)" % (len(found), cert.issuer.rfc4514_string()))


def ocsp_target(entry, timeout=15):
    """entry {name, url, cert (PEM text), issuer (PEM text)} -> the same with url and issuer filled in from the
    certificate where they were left out, cert being its first certificate, and why ('' = ready to ask)."""
    e = dict(entry or {})
    e.setdefault("name", "")
    url, cert_pem, issuer_pem = str(e.get("url") or "").strip(), str(e.get("cert") or ""), str(e.get("issuer") or "")
    e.update({"url": url, "why": "", "from_cert": []})
    if not cert_pem.strip():
        if not url:
            e["why"] = "neither a url nor a cert: give the responder's url, or a certificate it knows (cert)"
        return e
    if not HAS_CRYPTO:
        e["why"] = "the Python package cryptography is missing on the AAP side (needed to read the certificate)"
        return e
    pems = PEM_RE.findall(cert_pem)
    try:
        cert = x509.load_pem_x509_certificate(pems[0].encode("ascii")) if pems else None
    except ValueError:
        cert = None
    if cert is None:
        e["why"] = "the cert is not a PEM certificate"
        return e
    e["cert"] = pems[0]
    if not url:
        urls = _aia(cert, AuthorityInformationAccessOID.OCSP)
        if not urls:
            e["why"] = "the certificate %s names no OCSP address (Authority Information Access): give the responder's url" % (
                cert.subject.rfc4514_string())
            return e
        e["url"] = urls[0]
        e["from_cert"].append("url")
    if not e["name"]:
        e["name"] = e["url"]
    if issuer_pem.strip():
        return e
    for p in pems[1:]:                                   # a cert file with its chain: the next one may be the issuer
        c = x509.load_pem_x509_certificate(p.encode("ascii"))
        if c.subject == cert.issuer:
            e["issuer"] = p
            e["from_cert"].append("issuer")
            return e
    urls = _aia(cert, AuthorityInformationAccessOID.CA_ISSUERS)
    if not urls:
        e["why"] = "the certificate names no issuer address (caIssuers): give the issuer's certificate (issuer)"
        return e
    try:
        e["issuer"] = _download_issuer(cert, urls[0], timeout)
        e["from_cert"].append("issuer")
    except Exception as ex:                              # noqa: BLE001 - any download or parse failure is the why
        if hasattr(ex, "close"):                          # an HTTPError holds its connection
            ex.close()
        e["why"] = "the issuer's certificate could not be read from %s: %s - give it as issuer" % (urls[0], getattr(ex, "reason", ex))
    return e


def ocsp_cert_entry(x):
    """One item of site_ocsp_certs - a cert file's path, or an entry like vm_sso_ocsp's - as an entry."""
    return dict(x) if isinstance(x, dict) else {"cert": str(x)}


class FilterModule(object):
    def filters(self):
        return {"ocsp_target": ocsp_target, "ocsp_cert_entry": ocsp_cert_entry}
