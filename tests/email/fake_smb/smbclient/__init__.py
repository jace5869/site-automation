"""A stand-in for smbprotocol's smbclient (tests/email/run_web_test.sh, the retries of the report website): a share
\\\\server\\share\\folder is FAKE_SMB_ROOT/share/folder on this machine. Every login (register_session) is logged in
FAKE_SMB_LOG with the Kerberos cache it would use (KRB5CCNAME); FAKE_SMB_FAIL is a file "N|error": the next N logins
fail with that error ("always|error": every one). Only what roles/site_email/library/site_report_web.py uses."""
import os

from . import path  # noqa: F401  smbclient.path.isdir / exists

ROOT = os.environ.get("FAKE_SMB_ROOT", "/nonexistent")


def _log(line):
    with open(os.environ.get("FAKE_SMB_LOG", os.devnull), "a") as f:
        f.write(line + "\n")


def local(p):
    parts = [x for x in p.replace("/", "\\").split("\\") if x]
    return os.path.join(ROOT, *parts[1:])                # the server's name left out


def register_session(server, username=None, password=None, port=445, encrypt=None, auth_protocol="negotiate",
                     connection_timeout=60):
    _log("login %s as %s, cache %s" % (server, username, os.environ.get("KRB5CCNAME", "-")))
    spec = os.environ.get("FAKE_SMB_FAIL", "")
    if spec and os.path.exists(spec):
        with open(spec) as f:
            n, _, why = f.read().strip().partition("|")
        if n == "always":
            raise Exception(why)
        if int(n or 0) > 0:
            with open(spec, "w") as f:
                f.write("%d|%s" % (int(n) - 1, why))
            raise Exception(why)


def reset_connection_cache(*a, **kw):
    _log("reset")


def listdir(p, **kw):
    return os.listdir(local(p))


def open_file(p, mode="r", **kw):
    return open(local(p), mode)


def stat(p, **kw):
    return os.stat(local(p))


def makedirs(p, exist_ok=False, **kw):
    os.makedirs(local(p), exist_ok=exist_ok)


def replace(src, dst, **kw):
    os.replace(local(src), local(dst))


def remove(p, **kw):
    os.remove(local(p))
