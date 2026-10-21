"""A stand-in Cisco IOS-XE device for tests/netcerts and tests/netconfig: an SSH server (paramiko) with the CLI
the network jobs use - show version, show running-config / startup-config (a configuration it keeps: lines
and blocks such as interface and line, changed in configuration mode, "no" lines removed), write memory, copy
running-config startup-config, crypto key generate rsa, crypto pki trustpoint, crypto pki enroll (its prompts,
a real CSR made here), scp to and from its flash (scp -t / -f), configure replace <file> force, delete /force -
logging each command to a file.
  python3 fake_ios.py PORT LOGFILE [HOSTNAME [ADDRESS]]   (user admin, password admin; on 127.0.0.2 by default -
an address no real SSH server here has a known host key for; runs until killed)"""
import socket
import sys
import threading
import time

import paramiko
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

PORT, LOG = int(sys.argv[1]), sys.argv[2]
HOST = sys.argv[3] if len(sys.argv) > 3 else "rtr01"
ADDRESS = sys.argv[4] if len(sys.argv) > 4 else "127.0.0.2"
HOST_KEY = paramiko.RSAKey.generate(2048)
CONFIG = """version 17.9
service timestamps debug datetime msec
hostname {h}
!
enable secret 9 $9$fakeenablehash
username admin privilege 15 secret 9 $9$fakeuserhash
!
interface GigabitEthernet1
 description uplink
 ip address 192.0.2.1 255.255.255.0
!
ntp server 192.0.2.123
snmp-server community s3cr3t RO
!
line vty 0 4
 login local
 transport input ssh
!
end
""".format(h=HOST)
PARENTS = ("interface ", "line ", "router ", "ip access-list ", "vlan ", "class-map ", "policy-map ", "archive")
SUBPROMPT = {"interface ": "config-if", "line ": "config-line", "router ": "config-router", "ip access-list ": "config-ext-nacl"}


def parse(text):
    """A configuration as [[top-level line, [child lines]], ...]."""
    blocks = []
    for raw in str(text).replace("\r", "").split("\n"):
        x = raw.rstrip()
        if not x.strip() or x.strip() == "!" or x.strip() == "end" or x.startswith("Building configuration") or x.startswith("Current configuration"):
            continue
        if x.startswith(" ") and blocks:
            blocks[-1][1].append(x.strip())
        elif not x.startswith(" "):
            blocks.append([x, []])
    return blocks


def render(blocks):
    out = []
    for top, kids in blocks:
        out.append(top)
        out += [" " + k for k in kids]
        if kids:
            out.append("!")
    return "\n".join(out + ["end"]) + "\n"


STATE = {"keys": {}, "trustpoints": {}, "config": parse(CONFIG), "startup": parse(CONFIG), "files": {}, "reads": [0]}


def show_running():
    STATE["reads"][0] += 1
    body = render(STATE["config"])
    return ("Building configuration...\n\nCurrent configuration : %d bytes\n!\n! Last configuration change at 06:%02d:00 UTC Tue Oct 20 2026\n!\n%s"
            % (len(body), STATE["reads"][0] % 60, body))


def config_line(cmd, parent):
    """A configuration-mode line: -> the parent now (None: global configuration)."""
    cfg = STATE["config"]
    if cmd.startswith(PARENTS) and not cmd.startswith("no "):
        if not any(b[0] == cmd for b in cfg):
            cfg.append([cmd, []])
        return cmd
    if parent is not None:
        b = [x for x in cfg if x[0] == parent][0]
        if cmd.startswith("no "):
            b[1][:] = [k for k in b[1] if k != cmd[3:] and not k.startswith(cmd[3:] + " ")]
        elif cmd not in b[1]:
            b[1].append(cmd)
        return parent
    if cmd.startswith("no "):
        cfg[:] = [b for b in cfg if b[0] != cmd[3:] and not b[0].startswith(cmd[3:] + " ")]
        return None
    first = cmd.split()[0]
    if first in ("hostname",) or cmd.startswith("banner motd"):
        cfg[:] = [b for b in cfg if not b[0].startswith(cmd.split(" ", 1)[0] + " " if first == "hostname" else "banner motd")]
    if not any(b[0] == cmd for b in cfg):
        cfg.append([cmd, []])
    return None
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
        channel.fake_kind = "shell"       # what the client asked for on this channel
        return True

    def check_channel_exec_request(self, channel, command):
        cmd = command.decode(errors="replace")
        log("exec| %s" % cmd)
        channel.fake_kind = "exec"
        if cmd.startswith("scp "):
            threading.Thread(target=scp, args=(channel, cmd.split()), daemon=True).start()
            return True
        return False


def flash_name(path):
    p = path.strip("'\"")
    return p if ":" in p else "flash:" + p.lstrip("/")


def scp(chan, words):
    """scp -t PATH (a file sent to the device) or scp -f PATH (one sent from it)."""
    path = flash_name(words[-1])
    try:
        if "-t" in words:
            chan.send(b"\0")
            head = b""
            while not head.endswith(b"\n"):
                head += chan.recv(1)
            size = int(head.split()[1])
            chan.send(b"\0")
            data = b""
            while len(data) < size:
                data += chan.recv(size - len(data))
            chan.recv(1)
            STATE["files"][path] = data.decode()
            log("scp| received %s (%d bytes)" % (path, size))
            chan.send(b"\0")
        else:
            chan.recv(1)
            if path not in STATE["files"]:
                chan.send(("\x01scp: %s: No such file or directory\n" % path).encode())
            else:
                data = STATE["files"][path].encode()
                chan.send(("C0644 %d %s\n" % (len(data), path.split(":")[-1])).encode())
                chan.recv(1)
                chan.send(data + b"\0")
                chan.recv(1)
        chan.send_exit_status(0)
    except Exception as e:      # noqa: BLE001 - a test stand-in: say it, end the channel
        log("scp| error %s" % e)
        chan.send_exit_status(1)
    chan.close()


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
    for _ in range(200):    # the shell or exec request comes just after the channel opens
        if getattr(chan, "fake_kind", None):
            break
        time.sleep(0.05)
    if getattr(chan, "fake_kind", None) != "shell":
        return              # an exec channel (scp): its own thread
    mode = "exec"
    tp = None

    parent = [None]

    def prompt():
        if mode == "sub":
            p = "(%s)#" % next((v for k, v in SUBPROMPT.items() if parent[0].startswith(k)), "config-sub")
        else:
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
        elif cmd.startswith("show running-config") and mode == "exec":
            out(show_running())
        elif cmd == "show startup-config" and mode == "exec":
            out("Using %d out of 33554432 bytes\n!\n%s" % (len(render(STATE["startup"])), render(STATE["startup"])))
        elif cmd in ("write memory", "write") and mode == "exec":
            STATE["startup"] = [[t, list(k)] for t, k in STATE["config"]]
            out("Building configuration...\n[OK]\n")
        elif cmd == "copy running-config startup-config" and mode == "exec":
            out("Destination filename [startup-config]? ")
            a = readline(chan)
            log("answer| %s" % a)
            STATE["startup"] = [[t, list(k)] for t, k in STATE["config"]]
            out("Building configuration...\n[OK]\n")
        elif cmd.startswith("configure replace ") and mode == "exec":
            f = flash_name(words[2])
            if f not in STATE["files"]:
                out("%%Error opening %s (File not found)\n" % f)
            elif "INVALID-LINE" in STATE["files"][f]:
                out("%%Rollback aborted: the configuration has errors\n")
            else:
                STATE["config"] = parse(STATE["files"][f])
                out("Total number of passes: 1\nRollback Done\n")
        elif cmd.startswith("delete ") and mode == "exec":
            STATE["files"].pop(flash_name(words[-1]), None)
        elif cmd == "exit" and mode == "sub":
            mode, parent[0] = "config", None
        elif mode in ("config", "sub") and not cmd.startswith("crypto ") and cmd not in ("end", "exit"):
            parent[0] = config_line(cmd, parent[0] if mode == "sub" else None)
            mode = "sub" if parent[0] else "config"
        elif cmd.startswith("show crypto key mypubkey rsa"):
            label = words[-1]
            if label in STATE["keys"]:
                out("%% Key pair was generated at: 06:00:00 UTC Oct 20 2026\nKey name: %s\nKey type: RSA KEYS\n Storage Device: private-config\n" % label)
        elif cmd == "configure terminal" and mode == "exec":
            out("Enter configuration commands, one per line.  End with CNTL/Z.\n")
            mode = "config"
        elif cmd == "end":
            mode, parent[0] = "exec", None
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
