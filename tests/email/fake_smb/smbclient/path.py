"""smbclient.path for the stand-in (tests/email/fake_smb)."""
import os


def isdir(p, **kw):
    from . import local
    return os.path.isdir(local(p))


def exists(p, **kw):
    from . import local
    return os.path.exists(local(p))
