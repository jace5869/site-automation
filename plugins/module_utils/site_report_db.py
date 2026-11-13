# -*- coding: utf-8 -*-
"""The report database (MySQL; the "Report database" credential, docs/REPORT_DATABASE.md) for a module: a session the way
the credential asks (plain TCP, or TLS with the certificate checked), and an error as one line an admin can act on. The
same login care as roles/servicenow_insights/library/site_insights_db.py, for the modules written after it (1.52.0:
site_ontap_capacity_db). Needs PyMySQL in the execution environment - HAS_PYMYSQL says whether it is there."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import os
import ssl

try:
    import pymysql
    HAS_PYMYSQL = True
except ImportError:
    pymysql = None
    HAS_PYMYSQL = False

TABLE_OPTS = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"
SCHEMA_VERSION_TABLE = ("CREATE TABLE IF NOT EXISTS schema_version (component VARCHAR(64) NOT NULL PRIMARY KEY, version INT NOT NULL, "
                        "applied_at DATETIME NOT NULL) " + TABLE_OPTS)
NO_PYMYSQL = "PyMySQL is not in this execution environment (pip install PyMySQL; the all-in-one EE has it)"
PYMYSQL_AUTH_BUG = ("PyMySQL %s has a bug in the caching_sha2_password login over plain TCP (1.2.1 to 1.2.3: \"'NoneType' "
                    "object has no attribute 'is_auth_switch_request'\"). Launch again: the second login usually works. "
                    "The lasting fix: PyMySQL 1.2.4 or later in the execution environment (ee/*/requirements.txt). First error: %s")
# what a module takes for the session: the "Report database" credential's fields
ARGS = dict(host=dict(type="str", required=True), port=dict(type="int", default=3309), database=dict(type="str", required=True),
            user=dict(type="str", required=True), password=dict(type="str", required=True, no_log=True),
            tls=dict(type="bool", default=False), ca_file=dict(type="str", default=""), connect_timeout=dict(type="int", default=10))


def connect(p):
    """(session, 'TLSv1.3 cipher' or 'none') - plain TCP, or (tls) TLS with the certificate checked (the CA and the host's
    name) - or an exception."""
    ctx = None
    if p["tls"]:
        ca = p["ca_file"] if p["ca_file"] and os.path.isfile(p["ca_file"]) and os.path.getsize(p["ca_file"]) > 0 else None
        ctx = ssl.create_default_context(cafile=ca)          # no CA on the credential: the EE's trust store
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
    kw = dict(host=p["host"], port=p["port"], user=p["user"], password=p["password"], database=p["database"],
              connect_timeout=p["connect_timeout"], autocommit=False, charset="utf8mb4")
    conn = pymysql.connect(ssl=ctx, **kw) if ctx is not None else connect_plain(kw)
    cur = conn.cursor()
    cur.execute("SHOW SESSION STATUS WHERE Variable_name IN ('Ssl_version', 'Ssl_cipher')")
    st = dict((str(k), str(v or "")) for k, v in cur.fetchall())
    if p["tls"] and not st.get("Ssl_cipher"):
        conn.close()
        raise RuntimeError("the session is not encrypted - the server must offer TLS")
    return conn, ("%s %s" % (st.get("Ssl_version", ""), st.get("Ssl_cipher", ""))) if st.get("Ssl_cipher") else "none"


def connect_plain(kw):
    """Plain TCP. PyMySQL 1.2.1 to 1.2.3 raise AttributeError on the first caching_sha2_password login after the server's
    password cache is empty (a restart, FLUSH PRIVILEGES); the server has accepted the login by then, so the second takes
    the fast path. One more try, never more."""
    def once():
        try:                    # PyMySQL 1.1+ would use TLS unchecked when the server offers it: plain TCP, as asked
            return pymysql.connect(ssl_disabled=True, **kw)
        except TypeError:       # an older PyMySQL: plain TCP is its default
            return pymysql.connect(**kw)
    try:
        return once()
    except AttributeError as first:
        try:
            return once()
        except AttributeError:
            raise RuntimeError(PYMYSQL_AUTH_BUG % (getattr(pymysql, "VERSION_STRING", "?"), first))


def short_error(e):
    a = getattr(e, "args", None) or ()
    if len(a) >= 2 and isinstance(a[0], int):
        return "%s (MySQL error %s)" % (str(a[1]).strip(), a[0])
    return str(e).strip() or e.__class__.__name__


def explain(e, p):
    """An exception of connect() or a statement, as one line: what failed and what to change."""
    msg = short_error(e)
    if isinstance(e, ssl.SSLError):
        return "TLS: %s - the server's certificate must be signed by the CA and name %s" % (msg, p["host"])
    if "cryptography" in msg:
        return ("the login needs the cryptography library: without TLS, caching_sha2_password sends the password RSA-encrypted "
                "(the all-in-one EE has it; docs/REPORT_DATABASE.md, The EE)")
    if "secure transport" in msg.lower() and not p["tls"]:
        return msg + " - the server wants TLS: set Use TLS on the Report database credential"
    if p["tls"] and "support" in msg:
        return msg + " - the server does not offer TLS: clear Use TLS on the Report database credential, or give the server a certificate"
    if p["tls"] and ("SSL" in msg or "certificate" in msg.lower()):
        return msg + " - the server's certificate must be signed by the CA and name %s" % p["host"]
    return msg
