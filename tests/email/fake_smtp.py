#!/usr/bin/env python3
"""A small fake mail relay for tests/email/run_email_test.sh (standard library only).

  fake_smtp.py PORT OUTDIR [--tls CERT KEY] [--ssl] [--no-starttls] [--auth USER:PASS]

Speaks enough SMTP for Python's smtplib: EHLO/HELO, STARTTLS (when --tls and not --no-starttls),
AUTH PLAIN / LOGIN (when --auth), MAIL, RCPT (refuses addresses starting with "bad"), DATA, RSET,
NOOP, QUIT. --ssl wraps every connection in TLS from the start (port 465 style). Each message is
written to OUTDIR/N.eml, preceded by X-Envelope-From / X-Envelope-To / X-Auth / X-TLS lines.
Serves one connection at a time until killed."""
import base64
import os
import socket
import ssl
import sys

port, out = int(sys.argv[1]), sys.argv[2]
args = sys.argv[3:]
cert = key = auth = None
use_ssl = "--ssl" in args
starttls = "--no-starttls" not in args
if "--tls" in args:
    i = args.index("--tls")
    cert, key = args[i + 1], args[i + 2]
if "--auth" in args:
    auth = args[args.index("--auth") + 1]
ctx = None
if cert:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
os.makedirs(out, exist_ok=True)
srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", port))
srv.listen(5)
count = 0


def session(conn):
    global count
    tls = False
    if use_ssl:
        conn = ctx.wrap_socket(conn, server_side=True)
        tls = True
    f = conn.makefile("rwb")

    def say(line):
        f.write((line + "\r\n").encode())
        f.flush()

    say("220 fake.relay ESMTP")
    sender, rcpts, who = None, [], None
    while True:
        raw = f.readline()
        if not raw:
            return
        line = raw.decode(errors="replace").rstrip("\r\n")
        cmd = line.split(" ", 1)[0].upper()
        arg = line[len(cmd):].strip()
        if cmd in ("EHLO", "HELO"):
            ext = ["fake.relay"]
            if ctx and starttls and not tls and not use_ssl:
                ext.append("STARTTLS")
            if auth:
                ext.append("AUTH PLAIN LOGIN")
            for n, e in enumerate(ext):
                say(("250 " if n == len(ext) - 1 else "250-") + e)
        elif cmd == "STARTTLS" and ctx and starttls and not tls:
            say("220 go ahead")
            conn = ctx.wrap_socket(conn, server_side=True)
            f = conn.makefile("rwb")
            tls = True
        elif cmd == "AUTH" and auth:
            parts = arg.split()
            if parts[0].upper() == "PLAIN":
                data = parts[1] if len(parts) > 1 else None
                if data is None:
                    say("334 ")
                    data = f.readline().decode().strip()
                _, u, pw = base64.b64decode(data).decode().split("\0")
            else:
                say("334 " + base64.b64encode(b"Username:").decode())
                u = base64.b64decode(f.readline().strip()).decode()
                say("334 " + base64.b64encode(b"Password:").decode())
                pw = base64.b64decode(f.readline().strip()).decode()
            if "%s:%s" % (u, pw) == auth:
                who = u
                say("235 ok")
            else:
                say("535 bad credentials")
        elif cmd == "MAIL":
            if auth and not who:
                say("530 authentication required")
                continue
            sender, rcpts = arg.split(":", 1)[1].strip().strip("<>"), []
            say("250 ok")
        elif cmd == "RCPT":
            r = arg.split(":", 1)[1].strip().strip("<>")
            if r.startswith("bad"):
                say("550 no such user")
            else:
                rcpts.append(r)
                say("250 ok")
        elif cmd == "DATA":
            say("354 end with .")
            body = []
            while True:
                b = f.readline()
                if b in (b".\r\n", b".\n", b""):
                    break
                body.append(b[1:] if b.startswith(b"..") else b)
            count += 1
            with open(os.path.join(out, "%d.eml" % count), "wb") as fh:
                fh.write(("X-Envelope-From: %s\r\nX-Envelope-To: %s\r\nX-Auth: %s\r\nX-TLS: %s\r\n"
                          % (sender, ",".join(rcpts), who or "-", "yes" if tls else "no")).encode())
                fh.writelines(body)
            say("250 queued")
        elif cmd in ("RSET", "NOOP"):
            say("250 ok")
        elif cmd == "QUIT":
            say("221 bye")
            return
        else:
            say("502 not implemented")


while True:
    c, _ = srv.accept()
    try:
        session(c)
    except Exception as e:      # a client that hangs up mid-way, a TLS handshake it refused
        sys.stderr.write("session error: %r\n" % e)
    finally:
        try:
            c.close()
        except OSError:
            pass
