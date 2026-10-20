"""Stand-in network devices for tests/netcerts: TLS servers on 127.0.0.1, each with a certificate made here.
  python3 tls_servers.py PORT:CN:DAYS [PORT:CN:DAYS ...]     (DAYS may be negative: an expired certificate)
TLS_SERVERS_DIR=<folder>: the certificates are kept there and used again on the next start (the same device,
the same certificate) - one per CN and DAYS. Runs until killed."""
import datetime
import ipaddress
import os
import socket
import ssl
import sys
import tempfile
import threading
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def make(cn, days, d):
    safe = "".join(ch if ch.isalnum() or ch in ".-" else "_" for ch in cn) + "_%d" % days
    c, k = os.path.join(d, safe + ".crt"), os.path.join(d, safe + ".key")
    if os.path.exists(c) and os.path.exists(k):
        return c, k
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn), x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Test Net")])
    now = datetime.datetime.now(datetime.timezone.utc)
    after = now + datetime.timedelta(days=days, hours=1)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(min(now, after) - datetime.timedelta(days=30)).not_valid_after(after)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(cn), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    open(c, "wb").write(cert.public_bytes(serialization.Encoding.PEM))
    open(k, "wb").write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return c, k


def serve(port, c, k):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(c, k)
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(16)
    while True:
        conn, _ = s.accept()
        try:
            with ctx.wrap_socket(conn, server_side=True) as t:
                t.settimeout(2)
                t.recv(1)
        except (OSError, ssl.SSLError):
            pass


d = os.environ.get("TLS_SERVERS_DIR") or tempfile.mkdtemp()
os.makedirs(d, exist_ok=True)
for spec in sys.argv[1:]:
    p, cn, days = spec.split(":")
    c, k = make(cn, int(days), d)
    threading.Thread(target=serve, args=(int(p), c, k), daemon=True).start()
while True:
    time.sleep(3600)
