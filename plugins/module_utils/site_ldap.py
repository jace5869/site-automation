# -*- coding: utf-8 -*-
"""1.120.0: the LDAP connection the AD report's reader (roles/ad_report/library/site_ad_reader.py) and the site portal's
AD groups (site_ad_groups.py) share: LDAPS (bind over TLS), else StartTLS on 389 then the bind, or 389 with no bind
(anonymous: the RootDSE only) - never a simple bind in clear. ldap3 (in the all-in-one EE the jobs and the portal run in)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import re
import ssl
import time

try:
    import ldap3
except ImportError:                       # said by the callers (the module's own message, the portal's page)
    ldap3 = None


def short_err(e):
    """An exception in one line (its message when it has one, as the AD report's reader always said it)."""
    s = str(getattr(e, "message", "") or e)
    return re.sub(r"\s+", " ", s).strip()[:300] or e.__class__.__name__


def tls(fqdn, validate, ca_file=None):
    """The TLS settings: the server's certificate checked against ca_file (else the system's trust store) and its name,
    or not checked at all (validate false: said by the callers)."""
    if validate:
        return ldap3.Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=ca_file or None, valid_names=[fqdn],
                         version=ssl.PROTOCOL_TLS_CLIENT, sni=fqdn)
    return ldap3.Tls(validate=ssl.CERT_NONE, version=ssl.PROTOCOL_TLS_CLIENT, sni=fqdn)


def connect(fqdn, address, port, how, user="", password="", timeout=5, validate=True, ca_file=None):
    """how: 'ldaps' (bind over TLS), 'starttls' (StartTLS on port, then the bind), 'anon' (port, no bind).
    -> (conn, ms, error): conn None and why when it could not be opened or the bind was refused."""
    if ldap3 is None:
        return None, None, "the ldap3 Python library is not here"
    if "://" in str(address) or "/" in str(address):
        return None, None, "a host name or address, not a URI (%s)" % str(address)[:80]     # ldap:// would override use_ssl
    kw = {} if how == "anon" else {"authentication": ldap3.SIMPLE, "user": user, "password": password}
    t0 = time.time()
    c = None
    try:
        srv = ldap3.Server(address, port=int(port), use_ssl=how == "ldaps", tls=tls(fqdn, validate, ca_file), get_info=ldap3.NONE,
                           connect_timeout=timeout)
        c = ldap3.Connection(srv, receive_timeout=timeout * 3, raise_exceptions=False, auto_referrals=False, **kw)
        c.open()
        if c.closed:
            return None, None, "no answer"
        if how == "starttls" and not c.start_tls():
            why = "StartTLS refused: %s" % (c.result or {}).get("description", "?")
            _close(c)                         # 1.122.2: the socket to 389 closed, never left for the next retry
            return None, None, why
        if how != "anon" and not c.bind():
            r = c.result or {}
            msg = (r.get("description") or "") + (": " + r.get("message") if r.get("message") else "")
            _close(c)
            return None, int((time.time() - t0) * 1000), "the bind was refused - " + (msg or "?")
        return c, int((time.time() - t0) * 1000), ""
    except Exception as e:                    # noqa: BLE001 - any failure is an answer about this server
        _close(c)                             # 1.122.2: a TLS handshake that failed after open() leaves no socket behind
        return None, None, short_err(e)


def _close(c):
    if c is None:
        return
    try:
        c.unbind()
    except Exception:                         # noqa: BLE001 - closing what may never have opened
        pass
