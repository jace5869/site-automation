#!/usr/bin/python
# -*- coding: utf-8 -*-
"""site_net_certs: the TLS certificates of network devices (switches, firewalls, printers, iLO / iDRAC, UPS,
storage ...), read from the controller - no login to the devices."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
module: site_net_certs
short_description: Read the TLS certificate each network device presents, from the controller
description:
  - For each target (an inventory host, address and ports) and, optionally, each address of the given
    subnets, connects to each port, does a TLS handshake and reads the certificate the device presents.
    The certificate is not checked - an expired, self-signed or untrusted one is read all the same - and old
    TLS (1.0, weak ciphers: printers, out-of-band controllers) is accepted: only the certificate is read.
  - No login to any device, nothing is sent but a TLS hello. A port that does not answer, or is not TLS,
    is listed for an inventory target (not_answering) and only counted for a subnet sweep.
  - Needs the cryptography library (ansible-core's own requirement).
options:
  targets:
    description: "[{host, address, ports, servername}]: host is the name in the report, address what is connected to
      (default host), servername the name sent in the TLS hello (SNI; default host, none for an address)."
    type: list
    elements: dict
    default: []
  subnets:
    description: CIDR ranges to sweep (e.g. 192.0.2.0/24); every address, each of I(ports).
    type: list
    elements: str
    default: []
  ports:
    description: The ports of a subnet sweep (and of a target that names none).
    type: list
    elements: int
    default: [443]
  exclude:
    description: Addresses, CIDR ranges or host name patterns (fnmatch) never connected to.
    type: list
    elements: str
    default: []
  timeout:
    description: Seconds to wait for a connection, and for the handshake.
    type: float
    default: 5
  workers:
    description: Connections at the same time.
    type: int
    default: 32
  max_addresses:
    description: A sweep bigger than this (all subnets together) is refused - a typo like /8 would take hours.
    type: int
    default: 4096
  resolve_names:
    description: For a swept address that answers, its DNS name (PTR) as the host - else the address.
    type: bool
    default: true
"""

RETURN = r"""
certs:
  description: One per host and port that answered with a certificate.
  type: list
  elements: dict
  returned: always
  sample: [{"host": "sw01", "address": "192.0.2.10", "port": 443, "where": "https://192.0.2.10:443", "subject": "CN=sw01.example.mil",
            "issuer": "CN=Issuing CA", "sans": ["DNS:sw01.example.mil"], "not_after": "2027-01-31 12:00 UTC", "expires": "2027-01-31",
            "days": 103, "serial": "1A2B", "sha1": "...", "sha256": "...", "key": "RSA 2048", "sig_alg": "sha256WithRSAEncryption",
            "ca": false, "signs": false, "self_signed": false, "tier": "ca-signed", "tls": "TLSv1.2", "found_by": "inventory"}]
not_answering:
  description: The inventory targets' ports that gave no certificate, with why.
  type: list
  elements: dict
  returned: always
swept:
  description: "The sweep: {subnets, addresses, probes, answered}."
  type: dict
  returned: always
"""

import concurrent.futures
import datetime
import fnmatch
import ipaddress
import socket
import ssl
import warnings

from ansible.module_utils.basic import AnsibleModule

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, rsa
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

CURVES = {"secp256r1": "P-256", "secp384r1": "P-384", "secp521r1": "P-521"}


def utc(d):
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def days_left(na, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return int((na - now).total_seconds() // 86400)


def cert_record(der, now=None):
    """A certificate (DER) -> the inventory's fields (the same as the Linux, Windows and VMware reports)."""
    c = x509.load_der_x509_certificate(der)
    na = c.not_valid_after_utc if hasattr(c, "not_valid_after_utc") else utc(c.not_valid_after)
    sans = []
    try:
        ext = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        sans = (["DNS:%s" % n for n in ext.get_values_for_type(x509.DNSName)]
                + ["IP:%s" % n for n in ext.get_values_for_type(x509.IPAddress)]
                + ["email:%s" % n for n in ext.get_values_for_type(x509.RFC822Name)]
                + ["URI:%s" % n for n in ext.get_values_for_type(x509.UniformResourceIdentifier)])
    except x509.ExtensionNotFound:
        pass
    pk = c.public_key()
    if isinstance(pk, rsa.RSAPublicKey):
        key = "RSA %d" % pk.key_size
    elif isinstance(pk, ec.EllipticCurvePublicKey):
        key = "EC " + CURVES.get(pk.curve.name, pk.curve.name)
    else:
        key = type(pk).__name__.replace("PublicKey", "").lstrip("_")
    try:
        ca = bool(c.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
    except x509.ExtensionNotFound:
        ca = False
    try:
        signs = bool(c.extensions.get_extension_for_class(x509.KeyUsage).value.key_cert_sign)
    except x509.ExtensionNotFound:
        signs = False
    self_signed = c.subject == c.issuer
    try:
        sig = c.signature_algorithm_oid._name
    except AttributeError:
        sig = c.signature_algorithm_oid.dotted_string
    serial = "%X" % c.serial_number
    return {"subject": c.subject.rfc4514_string(), "issuer": c.issuer.rfc4514_string(), "sans": sans,
            "not_after": na.strftime("%Y-%m-%d %H:%M UTC"), "expires": na.strftime("%Y-%m-%d"), "days": days_left(na, now),
            "serial": ("0" if len(serial) % 2 else "") + serial, "sha1": c.fingerprint(hashes.SHA1()).hex().upper(),
            "sha256": c.fingerprint(hashes.SHA256()).hex().upper(), "key": key, "sig_alg": sig, "ca": ca, "signs": signs,
            "self_signed": self_signed,
            # root: a self-signed CA that signs certificates - not a device's own self-signed one
            "tier": "root" if ca and self_signed and signs else ("self-signed" if self_signed else ("intermediate" if ca else "ca-signed"))}


def context():
    """A TLS client that reads any certificate: no check, old TLS and ciphers allowed (only the certificate is read)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
    with warnings.catch_warnings():           # TLS 1.0 is deprecated - on purpose here: old devices only speak it
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            ctx.minimum_version = ssl.TLSVersion.TLSv1
        except (AttributeError, ValueError):
            pass
    try:
        ctx.set_ciphers("ALL:@SECLEVEL=0")
    except ssl.SSLError:
        pass
    return ctx


def is_ip(s):
    try:
        ipaddress.ip_address(str(s).strip("[]"))
        return True
    except ValueError:
        return False


def probe(address, port, servername, timeout):
    """(der, tls version) or raises: the reason it gave no certificate."""
    sock = socket.create_connection((address, port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        sni = servername if servername and not is_ip(servername) else None
        tls = context().wrap_socket(sock, server_hostname=sni)
        try:
            der = tls.getpeercert(binary_form=True)
            if not der:
                raise ValueError("no certificate in the handshake")
            return der, tls.version()
        finally:
            tls.close()
    finally:
        sock.close()


def why(e):
    if isinstance(e, socket.timeout):
        return "no answer (timeout)"
    if isinstance(e, ConnectionRefusedError):
        return "refused (nothing listens on the port)"
    if isinstance(e, socket.gaierror):
        return "the name does not resolve (DNS)"
    if isinstance(e, ssl.SSLError):
        return "not TLS, or a TLS this controller cannot speak (%s)" % (e.reason or e)
    if isinstance(e, OSError):
        return e.strerror or str(e)
    return str(e)


def excluded(addr, host, exclude):
    for x in exclude or []:
        x = str(x).strip()
        if not x:
            continue
        try:
            if "/" in x and is_ip(addr) and ipaddress.ip_address(addr) in ipaddress.ip_network(x, strict=False):
                return True
        except ValueError:
            pass
        if addr == x or fnmatch.fnmatchcase(str(host).lower(), x.lower()):
            return True
    return False


def sweep_addresses(subnets, max_addresses):
    out, seen = [], set()
    for s in subnets or []:
        net = ipaddress.ip_network(str(s).strip(), strict=False)
        hosts = [net.network_address] if net.num_addresses == 1 else list(net.hosts())
        if len(out) + len(hosts) > max_addresses:
            raise ValueError("the sweep would be more than %d addresses (%s has %d): give smaller subnets, or raise max_addresses"
                             % (max_addresses, s, len(hosts)))
        for a in hosts:
            if str(a) not in seen:
                seen.add(str(a))
                out.append(str(a))
    return out


def run(params, now=None):
    jobs = []        # (host, address, port, servername, found_by)
    seen = set()
    for t in params["targets"] or []:
        host = str(t.get("host") or t.get("address") or "").strip()
        address = str(t.get("address") or host).strip()
        if not host or excluded(address, host, params["exclude"]):
            continue
        for p in t.get("ports") or params["ports"]:
            k = (address.lower(), int(p))
            if k not in seen:
                seen.add(k)
                jobs.append((host, address, int(p), str(t.get("servername") or host), "inventory"))
    swept = {"subnets": list(params["subnets"] or []), "addresses": 0, "probes": 0, "answered": 0}
    addrs = sweep_addresses(params["subnets"], params["max_addresses"])
    swept["addresses"] = len(addrs)
    for a in addrs:
        if excluded(a, a, params["exclude"]):
            continue
        for p in params["ports"]:
            k = (a.lower(), int(p))
            if k not in seen:
                seen.add(k)
                jobs.append((a, a, int(p), "", "sweep"))
                swept["probes"] += 1

    def one(j):
        host, address, port, sni, by = j
        try:
            der, version = probe(address, port, sni, params["timeout"])
        except Exception as e:  # noqa: BLE001 - every reason is reported
            return j, None, why(e)
        try:
            rec = cert_record(der, now)
        except Exception as e:  # noqa: BLE001
            return j, None, "a certificate this controller cannot read (%s)" % e
        if by == "sweep" and params["resolve_names"]:
            try:
                host = socket.gethostbyaddr(address)[0]
            except (OSError, socket.herror):
                pass
        url = "https://%s:%d" % ("[%s]" % address if ":" in address else address, port)
        rec.update(host=host, address=address, port=port, servername=sni, where=url, tls=version, found_by=by)
        return j, rec, ""

    certs, not_answering = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, int(params["workers"]))) as pool:
        for j, rec, err in pool.map(one, jobs):
            if rec:
                certs.append(rec)
                if j[4] == "sweep":
                    swept["answered"] += 1
            elif j[4] == "inventory":
                not_answering.append({"host": j[0], "address": j[1], "port": j[2], "why": err})
    certs.sort(key=lambda r: (r["days"], r["host"], r["port"]))
    return {"certs": certs, "not_answering": not_answering, "swept": swept}


def main():
    module = AnsibleModule(
        argument_spec=dict(
            targets=dict(type="list", elements="dict", default=[]),
            subnets=dict(type="list", elements="str", default=[]),
            ports=dict(type="list", elements="int", default=[443]),
            exclude=dict(type="list", elements="str", default=[]),
            timeout=dict(type="float", default=5),
            workers=dict(type="int", default=32),
            max_addresses=dict(type="int", default=4096),
            resolve_names=dict(type="bool", default=True),
        ),
        supports_check_mode=True,
    )
    if not HAS_CRYPTO:
        module.fail_json(msg="site_net_certs needs the Python library cryptography in the execution environment")
    try:
        res = run(module.params)
    except ValueError as e:
        module.fail_json(msg=str(e))
    module.exit_json(changed=False, **res)


if __name__ == "__main__":
    main()
