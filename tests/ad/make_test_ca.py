#!/usr/bin/env python3
"""A made-up CA and an LDAPS certificate for each test DC (tests/ad/run_ad_reader_test.sh): what an AD CS template would
issue - the DC's name in the subject alternative name, server authentication, the key identifiers a strict verifier
(Python 3.13 and later) asks for. Test only.
Usage: make_test_ca.py DIR dc01=400 dc02=5     (the DC's short name = how many days its certificate is valid)
Writes DIR/ca.crt, DIR/<dc>.crt and DIR/<dc>.key (PEM) for <dc>.corp.example.mil."""
import datetime
import os
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

DOMAIN = "corp.example.mil"


def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def write(path, data, mode=0o644):
    with open(path, "wb") as fh:
        fh.write(data)
    os.chmod(path, mode)


def main():
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)
    ca_key = key()
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Issuing CA"), x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Example")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=3650))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False, data_encipherment=False,
                                       key_agreement=False, key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    write(os.path.join(out, "ca.crt"), ca.public_bytes(serialization.Encoding.PEM))
    for arg in sys.argv[2:]:
        name, days = arg.split("=")
        fq = "%s.%s" % (name, DOMAIN)
        k = key()
        c = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, fq)])).issuer_name(ca_name)
             .public_key(k.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now)
             .not_valid_after(now + datetime.timedelta(days=float(days), minutes=5))
             .add_extension(x509.SubjectAlternativeName([x509.DNSName(fq)]), critical=False)
             .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
             .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
             .add_extension(x509.SubjectKeyIdentifier.from_public_key(k.public_key()), critical=False)
             .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
             .sign(ca_key, hashes.SHA256()))
        write(os.path.join(out, name + ".crt"), c.public_bytes(serialization.Encoding.PEM))
        # read by the container's root (copied there to a 0600 file): readable here so a rootless container may copy it
        write(os.path.join(out, name + ".key"), k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                                                serialization.NoEncryption()), 0o644)
        print(fq, days, "days")


if __name__ == "__main__":
    main()
