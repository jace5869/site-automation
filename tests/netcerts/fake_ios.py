"""A stand-in Cisco IOS-XE device for tests/netcerts: an SSH server (paramiko) with the CLI the network
certificate renewal uses - show version, configuration mode, crypto key generate rsa, crypto pki trustpoint,
crypto pki enroll (its prompts, a real CSR made here) - logging each command to a file.
  python3 fake_ios.py PORT LOGFILE [HOSTNAME [ADDRESS]]   (user admin, password admin; on 127.0.0.2 by default -
an address no real SSH server here has a known host key for; runs until killed)"""
import socket
import sys
import threading

import paramiko
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

PORT, LOG = int(sys.argv[1]), sys.argv[2]
HOST = sys.argv[3] if len(sys.argv) > 3 else "rtr01"
ADDRESS = sys.argv[4] if len(sys.argv) > 4 else "127.0.0.2"
HOST_KEY = paramiko.RSAKey.generate(2048)
STATE = {"keys": {}, "trustpoints": {}}
VERSION = """Cisco IOS XE Software, Version 17.09.04a
Cisco IOS Software [Cupertino], Virtual XE Software (X86_64_LINUX_IOSD-UNIVERSALK9-M), Version 17.9.4a, RELEASE SOFTWARE (fc3)
ROM: IOS-XE ROMMON
{h} uptime is 3 weeks, 2 days, 4 hours, 10 minutes
System image file is "bootflash:packages.conf"
cisco C8000V (VXE) processor (revision VXE) with 2028012K/3075K bytes of memory.
Processor board ID 9ABCDEF0123
Configuration register is 0x2102
""".format(h=HOST)


def log(line):
    with open(LOG, "a") as f:
        f.write(line + "\n")


def make_csr(subject, names, bits):
    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    parts = []
    for p in subject.split(","):
        if "=" in p:
            k, v = p.split("=", 1)
            oid = {"CN": NameOID.COMMON_NAME, "O": NameOID.ORGANIZATION_NAME, "OU": NameOID.ORGANIZATIONAL_UNIT_NAME,
                   "C": NameOID.COUNTRY_NAME}.get(k.strip().upper())
            if oid:
                parts.append(x509.NameAttribute(oid, v.strip()))
    b = x509.CertificateSigningRequestBuilder().subject_name(x509.Name(parts))
    if names:
        b = b.add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in names]), critical=False)
    return b.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode()


class Server(paramiko.ServerInterface):
    def check_auth_password(self, username, password):
        return paramiko.AUTH_SUCCESSFUL if (username, password) == ("admin", "admin") else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_pty_request(self, *a):
        return True

    def check_channel_shell_request(self, channel):
        return True


def readline(chan, echo=True, _cr={}):
    """One line: ends at CR or LF; the LF of a CR LF is skipped."""
    buf = b""
    while True:
        c = chan.recv(1)
        if not c:
            return None
        if c == b"\n" and _cr.get(id(chan)):
            _cr[id(chan)] = False
            continue
        _cr[id(chan)] = c == b"\r"
        if c in (b"\r", b"\n"):
            if echo:
                chan.send(b"\r\n")
            return buf.decode(errors="replace")
        if echo:
            chan.send(c)
        buf += c


def session(chan):
    mode = "exec"
    tp = None

    def prompt():
        p = {"exec": "#", "config": "(config)#", "tp": "(ca-trustpoint)#"}[mode]
        chan.send(("\r\n%s%s" % (HOST, p)).encode())

    def out(text):
        chan.send(text.replace("\n", "\r\n").encode())

    prompt()
    while True:
        line = readline(chan)
        if line is None:
            return
        cmd = line.strip()
        if not cmd:
            prompt()
            continue
        log("%s| %s" % (mode, cmd))
        words = cmd.split()
        if cmd in ("terminal length 0", "terminal width 512", "terminal width 0") or cmd.startswith("terminal "):
            pass
        elif cmd == "show version":
            out(VERSION)
        elif cmd.startswith("show running-config"):
            out("Building configuration...\n\nhostname %s\n!\nend\n" % HOST)
        elif cmd.startswith("show crypto key mypubkey rsa"):
            label = words[-1]
            if label in STATE["keys"]:
                out("%% Key pair was generated at: 06:00:00 UTC Oct 20 2026\nKey name: %s\nKey type: RSA KEYS\n Storage Device: private-config\n" % label)
        elif cmd == "configure terminal" and mode == "exec":
            out("Enter configuration commands, one per line.  End with CNTL/Z.\n")
            mode = "config"
        elif cmd == "end":
            mode = "exec"
        elif cmd == "exit" and mode == "tp":
            mode = "config"
        elif cmd == "exit" and mode == "config":
            mode = "exec"
        elif mode == "config" and cmd.startswith("crypto key generate rsa"):
            label = words[words.index("label") + 1]
            bits = int(words[words.index("modulus") + 1]) if "modulus" in words else 2048
            STATE["keys"][label] = bits
            out("The name for the keys will be: %s\n\n%% The key modulus size is %d bits\n%% Generating %d bit RSA keys, keys will be non-exportable...\n[OK] (elapsed time was 1 seconds)\n" % (label, bits, bits))
        elif mode in ("config", "tp") and cmd.startswith("crypto pki trustpoint"):
            tp = words[-1]
            STATE["trustpoints"].setdefault(tp, {})
            mode = "tp"
        elif mode == "tp":
            if cmd.startswith("subject-name "):
                STATE["trustpoints"][tp]["subject"] = cmd[len("subject-name "):]
            elif cmd.startswith("subject-alt-name "):
                STATE["trustpoints"][tp]["names"] = cmd.split()[1:]
            elif cmd.startswith("rsakeypair "):
                STATE["trustpoints"][tp]["key"] = words[-1]
        elif mode == "config" and cmd.startswith("crypto pki enroll"):
            t = STATE["trustpoints"].get(words[-1])
            if t is None or t.get("key") not in STATE["keys"]:
                out("% You must authenticate the Certificate Authority before you can enroll with it.\n")
            else:
                out("%% Start certificate enrollment ..\n\n%% The subject name in the certificate will include: %s\n"
                    "Display Certificate Request to terminal? [yes/no]: " % t.get("subject", ""))
                a = readline(chan)
                log("answer| %s" % a)
                if (a or "").strip() == "yes":
                    csr = make_csr(t.get("subject", ""), t.get("names") or [], STATE["keys"][t["key"]])
                    out("Certificate Request follows:\n\n%s\n---End - This line not part of the certificate request---\n\n"
                        "Redisplay enrollment request? [yes/no]: " % csr.strip())
                    a = readline(chan)
                    log("answer| %s" % a)
        else:
            pass
        prompt()


def handle(client):
    t = paramiko.Transport(client)
    t.add_server_key(HOST_KEY)
    t.start_server(server=Server())
    while t.is_active():
        chan = t.accept(5)
        if chan is None:
            continue
        threading.Thread(target=session, args=(chan,), daemon=True).start()


sock = socket.socket()
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind((ADDRESS, PORT))
sock.listen(16)
while True:
    c, _ = sock.accept()
    threading.Thread(target=handle, args=(c,), daemon=True).start()
