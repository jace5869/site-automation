#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Active Directory health without a domain admin (roles/ad_report, ad_health_mode: reader): everything is read from
the AAP side - LDAP (ldap3), DNS (dnspython), the LDAPS certificates from the TLS handshake and Kerberos - as the job's
own account, a plain domain user with nothing granted. Nothing runs on a domain controller and nothing is written to
AD. Every judgement (red, amber, green) is made in plugins/filter/ad_filters.py (ad_reader_report): this only reads,
and says what it could not read and why - never a guess."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_ad_reader
short_description: Read Active Directory's health over LDAP, DNS and Kerberos as a plain domain user
description:
  - Finds the domain's DCs (the inventory's, the SRV records', and AD's own list in Configuration), binds to one
    (a simple bind over LDAPS, else StartTLS on 389 - never in clear) and reads the domain - the DCs, FSMO holders,
    functional levels, the privileged groups and their members, accounts locked out, created, enabled or disabled.
  - Each DC - LDAP 389 and LDAPS 636 answer and bind, the rootDSE (isSynchronized, isGlobalCatalogReady, currentTime),
    its LDAPS certificate (from the TLS handshake), its KDC (a TGT with kinit, or an AS-REQ), its replication as far as
    a plain user may read it (msDS-NCReplInboundNeighbors, repsFrom, replUpToDateVector), its DNS records.
  - Read only, also in check mode. The user name and password are the job's Machine credential (the action plugin of
    the same name passes them) unless given.
  - The last run's privileged group members come from this template's previous job's artifacts in AAP
    (history_job_id, history_template_id; the "Red Hat Ansible Automation Platform" credential) or a file (history_file).
options:
  domain: {description: The AD domain's DNS name ("" = the inventory DCs' domain)., type: str, default: ''}
  dcs: {description: "The inventory's DCs: [{name, address}].", type: list, elements: dict, default: []}
  user: {description: The account (UPN, DOMAIN\\user or a DN)., type: str, default: ''}
  password: {description: Its password., type: str, default: '', no_log: true}
  ports: {description: "{ldap, ldaps, kerberos, dns}.", type: dict, default: {}}
  endpoints: {description: "Where a DC is reached when not at its name: {fqdn: {address, ldap, ldaps, kerberos}}.", type: dict, default: {}}
  dns_servers: {description: DNS servers to ask ([] = the system's)., type: list, elements: str, default: []}
  validate_certs: {description: Check the LDAPS certificate (trusted and for the DC's name) before sending the password., type: bool, default: true}
  ca_file: {description: A CA file ("" = what the execution environment trusts)., type: str, default: ''}
  timeout: {description: Seconds per connection., type: int, default: 5}
  kerberos: {description: "auto (a TGT with kinit when there is a kinit, else an AS-REQ), tgt, probe (AS-REQ only), off.", type: str, default: auto}
  kinit: {description: The kinit command., type: str, default: kinit}
  hours: {description: The changes of the last this-many hours., type: float, default: 24}
  privileged_groups_extra: {description: More privileged groups (names or SIDs)., type: list, elements: str, default: []}
  changes_max: {description: "Look at most at this many accounts per list (locked out, created, changed); a list cut there says so (locked_out_capped, created_capped, changes_capped).", type: int, default: 500}
  history_file: {description: 'A file with the last run''s members ("" = none).', type: str, default: ''}
  history_job_id: {description: This job's ID (0 = not in AAP)., type: int, default: 0}
  history_template_id: {description: Its template's ID., type: int, default: 0}
  history_runs: {description: How many earlier runs to look through for the members., type: int, default: 5}
  history_ca_path: {description: A CA file for AAP's certificate., type: str, default: ''}
author: site automation
'''

RETURN = r'''
reader: {description: "What was read (roles/ad_report/tasks/reader.yml; plugins/filter/ad_filters.py ad_reader_report).", type: dict}
'''

import datetime
import json
import os
import random
import re
import shutil
import socket
import ssl
import struct
import subprocess
import tempfile
import time
import uuid

from ansible.module_utils.basic import AnsibleModule

try:
    import ldap3
    from ldap3.utils.conv import escape_bytes, escape_filter_chars
    HAS_LDAP3 = True
except ImportError:
    HAS_LDAP3 = False
try:
    import dns.resolver
    import dns.exception
    HAS_DNS = True
except ImportError:
    HAS_DNS = False
try:
    from cryptography import x509
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False
try:
    from ansible.module_utils.site_aap import Api, connection, error_text
    HAS_AAP = True
except ImportError:
    HAS_AAP = False

EPOCH_1601 = datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)
PAGED_RESULTS = "1.2.840.113556.1.4.319"            # the LDAP paged-results control: its cookie asks for the next page
PAGE_SIZE = 500
NEEDS_ADMIN = "not read (needs admin)"
# replPropertyMetaData attribute IDs (ATTRTYP) in the fixed prefix table every DC shares (1.2.840.113556.1.4.x = 0x9xxxx)
ATTID = {0x00090008: "userAccountControl", 0x00090296: "lockoutTime"}
UF_ACCOUNTDISABLE, UF_SERVER_TRUST, UF_PARTIAL_SECRETS = 0x2, 0x2000, 0x4000000
# the privileged groups: (name, RID in the domain) - the forest root's own three only there - and the built-in ones
PRIV_RIDS = [("Domain Admins", 512), ("Group Policy Creator Owners", 520), ("Key Admins", 526), ("Cert Publishers", 517)]
PRIV_ROOT_RIDS = [("Enterprise Admins", 519), ("Schema Admins", 518), ("Enterprise Key Admins", 527)]
PRIV_BUILTIN = ["S-1-5-32-544", "S-1-5-32-548", "S-1-5-32-549", "S-1-5-32-550", "S-1-5-32-551"]
KRB_ERRORS = {6: "the account is not known to the KDC", 18: "the account is disabled, locked out or expired",
              23: "the account's password has expired", 24: "pre-authentication failed (the password)",
              25: "pre-authentication required", 37: "the clocks differ too much (more than 5 minutes)",
              68: "wrong realm"}


# ---- small helpers ------------------------------------------------------------------------------------------------------
def iso(t):
    return t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if t else ""


def gentime(v):
    """'20261110080006.0Z' (LDAP GeneralizedTime) or a datetime -> aware datetime, or None."""
    if isinstance(v, datetime.datetime):
        return v if v.tzinfo else v.replace(tzinfo=datetime.timezone.utc)
    m = re.match(r"^(\d{14})(?:\.\d+)?Z?$", str(v or "").strip())
    if not m:
        return None
    return datetime.datetime.strptime(m.group(1), "%Y%m%d%H%M%S").replace(tzinfo=datetime.timezone.utc)


def filetime(v):
    """A FILETIME / large integer (100 ns since 1601: lockoutTime, pwdLastSet) -> aware datetime; 0 / never -> None."""
    if isinstance(v, datetime.datetime):
        return None if v.year <= 1601 else (v if v.tzinfo else v.replace(tzinfo=datetime.timezone.utc))
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    if n <= 0 or n >= 0x7FFFFFFFFFFFFFFF:
        return None
    return EPOCH_1601 + datetime.timedelta(microseconds=n // 10)


def to_filetime(t):
    """An aware datetime -> FILETIME (100 ns since 1601), for an LDAP filter on lockoutTime."""
    return max(0, (t - EPOCH_1601) // datetime.timedelta(microseconds=1) * 10)


def lockout_window(v):
    """The domain's lockoutDuration (negative 100 ns intervals, or a timedelta) -> how long a lockout lasts, a timedelta;
    None when it lasts until an administrator unlocks the account: 0, or the most negative value AD and Samba store for
    it (-9223372036854775808), or a value not read."""
    if isinstance(v, datetime.timedelta):
        v = abs(v)
        return v if v and v < datetime.timedelta(days=36500) else None       # AD allows 99999 minutes at most
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    if n == 0 or n <= -0x7FFFFFFFFFFFFFFF or n >= 0x7FFFFFFFFFFFFFFF:
        return None
    return datetime.timedelta(microseconds=abs(n) // 10)


def dstime(seconds):
    """DSTIME (seconds since 1601) -> aware datetime; 0 -> None. A time before 2000 is a placeholder (a cursor a new DC
    copied at its join): None, never a date in 1970."""
    if not seconds:
        return None
    t = EPOCH_1601 + datetime.timedelta(seconds=int(seconds))
    return t if t.year >= 2000 else None


def xml_time(v):
    """'2026-11-10T08:00:08Z' in the DS_REPL_* XML; 1601 = never -> None."""
    s = str(v or "").strip()
    if not s or s.startswith("1601"):
        return None
    try:
        t = datetime.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    return t.replace(tzinfo=datetime.timezone.utc)


def from_iso(s):
    """'2026-11-10T08:00:08Z' (what this module returns) -> aware datetime, or None."""
    try:
        return datetime.datetime.strptime(str(s or "")[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def dn_to_dns(dn):
    return ".".join(re.findall(r"DC=([^,]+)", str(dn or ""), re.I))


def rdn(dn):
    m = re.match(r"^(?:CN|OU|DC)=((?:\\,|[^,])+)", str(dn or ""), re.I)
    return m.group(1).replace("\\,", ",") if m else str(dn or "")


def parent_dn(dn):
    m = re.match(r"^(?:\\,|[^,])+,(.*)$", str(dn or ""))
    return m.group(1) if m else ""


def site_of(server_dn):
    """'CN=DC01,CN=Servers,CN=Site-A,CN=Sites,CN=Configuration,...' -> 'Site-A'."""
    m = re.search(r",CN=Servers,CN=((?:\\,|[^,])+),CN=Sites,", str(server_dn or ""), re.I)
    return m.group(1) if m else ""


def sid_bytes(sid):
    """'S-1-5-21-1-2-3-512' -> its binary form (for an LDAP filter on objectSid)."""
    p = [int(x) for x in sid.split("-")[1:]]
    rev, auth, subs = p[0], p[1], p[2:]
    return struct.pack("<BB", rev, len(subs)) + auth.to_bytes(6, "big") + b"".join(struct.pack("<I", s) for s in subs)


def sid_text(b):
    if isinstance(b, str):
        return b
    if not b or len(b) < 8:
        return ""
    rev, n = b[0], b[1]
    auth = int.from_bytes(b[2:8], "big")
    subs = struct.unpack("<%dI" % n, b[8:8 + 4 * n])
    return "S-%d-%d%s" % (rev, auth, "".join("-%d" % s for s in subs))


def guid_text(v):
    if isinstance(v, (bytes, bytearray)) and len(v) == 16:
        return str(uuid.UUID(bytes_le=bytes(v)))
    return str(v or "").strip("{}").lower()


def first(e, name, default=None):
    v = (e.get("attributes") or {}).get(name)
    if isinstance(v, list):
        return v[0] if v else default
    return default if v in (None, "") else v


def raw_all(e, name):
    return list((e.get("raw_attributes") or {}).get(name) or [])


def short_err(e):
    s = str(getattr(e, "message", "") or e)
    return re.sub(r"\s+", " ", s).strip()[:300] or e.__class__.__name__


# ---- replication blobs (what a plain user may read on an NC head) ---------------------------------------------------------
def parse_reps_from(b):
    """repsFrom (REPLICA_LINK v1, [MS-DRSR] 5.171): {partner_guid, invocation_id, last_success, last_attempt, failures,
    result, address}."""
    b = bytes(b)
    ver, _, cb, fails, ls, la, result, ooff, olen, _flags = struct.unpack_from("<IIIIqqIIII", b, 0)
    if ver != 1:
        raise ValueError("repsFrom version %d" % ver)
    off = 48 + 84 + 4 + 24
    dsa, inv = uuid.UUID(bytes_le=b[off:off + 16]), uuid.UUID(bytes_le=b[off + 16:off + 32])
    addr = ""
    if ooff and ooff + 4 <= len(b):
        n = struct.unpack_from("<I", b, ooff)[0]
        addr = b[ooff + 4:ooff + 4 + n].split(b"\0")[0].decode("utf-8", "replace")
    return {"partner_guid": str(dsa), "invocation_id": str(inv), "last_success": iso(dstime(ls)),
            "last_attempt": iso(dstime(la)), "failures": fails, "result": result, "address": addr}


def parse_utd(b):
    """replUpToDateVector (UPTODATE_VECTOR v1 or v2): [{invocation_id, usn, last_sync}] - v1 has no time."""
    b = bytes(b)
    ver, _, n, _ = struct.unpack_from("<IIII", b, 0)
    out = []
    size = 32 if ver == 2 else 24
    for i in range(n):
        o = 16 + i * size
        if o + size > len(b):
            break
        inv = uuid.UUID(bytes_le=b[o:o + 16])
        usn = struct.unpack_from("<q", b, o + 16)[0]
        t = None
        if ver == 2:
            nt = struct.unpack_from("<q", b, o + 24)[0]
            t = dstime(nt // 10 ** 7) if nt > 0 else None
        out.append({"invocation_id": str(inv), "usn": usn, "last_sync": iso(t)})
    return out


def parse_prop_meta(b):
    """replPropertyMetaData (PROPERTY_META_DATA_EXT_VECTOR): {attid: {version, time, invocation_id}}."""
    b = bytes(b)
    ver, _, n, _ = struct.unpack_from("<IIII", b, 0)
    out = {}
    for i in range(n):
        o = 16 + i * 48
        if o + 48 > len(b):
            break
        attid, version, t = struct.unpack_from("<IIq", b, o)
        out[attid] = {"version": version, "time": iso(dstime(t)), "invocation_id": str(uuid.UUID(bytes_le=b[o + 16:o + 32]))}
    return out


def xml_fields(s):
    """One DS_REPL_* XML value (as Windows constructs msDS-* attributes) -> {tag: text}."""
    s = s.decode("utf-8", "replace") if isinstance(s, (bytes, bytearray)) else str(s or "")
    return dict((m.group(1), m.group(2).strip()) for m in re.finditer(r"<([A-Za-z]+)>([^<]*)</\1>", s))


def parse_neighbor_xml(s):
    """msDS-NCReplInboundNeighbors (DS_REPL_NEIGHBOR XML) -> as parse_reps_from, with the partner's DN."""
    f = xml_fields(s)
    return {"nc": f.get("pszNamingContext", ""), "partner_dn": f.get("pszSourceDsaDN", ""),
            "invocation_id": f.get("uuidSourceDsaInvocationID", ""), "partner_guid": f.get("uuidSourceDsaObjGuid", ""),
            "last_success": iso(xml_time(f.get("ftimeLastSyncSuccess"))), "last_attempt": iso(xml_time(f.get("ftimeLastSyncAttempt"))),
            "failures": int(f.get("cNumConsecutiveSyncFailures") or 0), "result": int(f.get("dwLastSyncResult") or 0),
            "address": f.get("pszSourceDsaAddress", "")}


def parse_value_meta_xml(s):
    """msDS-ReplValueMetaData (DS_REPL_VALUE_META_DATA XML) -> {member, created, deleted, changed, dsa_dn, version}."""
    f = xml_fields(s)
    return {"attribute": f.get("pszAttributeName", ""), "member": f.get("pszObjectDn", ""),
            "created": iso(xml_time(f.get("ftimeCreated"))), "deleted": iso(xml_time(f.get("ftimeDeleted"))),
            "changed": iso(xml_time(f.get("ftimeLastOriginatingChange"))), "dsa_dn": f.get("pszLastOriginatingDsaDN", ""),
            "invocation_id": f.get("uuidLastOriginatingDsaInvocationID", ""), "version": int(f.get("dwVersion") or 0)}


def parse_attr_meta_xml(s):
    """msDS-ReplAttributeMetaData (DS_REPL_ATTR_META_DATA XML) -> {attribute, time, dsa_dn, invocation_id, version}."""
    f = xml_fields(s)
    return {"attribute": f.get("pszAttributeName", ""), "time": iso(xml_time(f.get("ftimeLastOriginatingChange"))),
            "dsa_dn": f.get("pszLastOriginatingDsaDN", ""), "invocation_id": f.get("uuidLastOriginatingDsaInvocationID", ""),
            "version": int(f.get("dwVersion") or 0)}


# ---- Kerberos: an AS-REQ by hand (DER), so the check needs no Kerberos library -----------------------------------------
def _der(tag, content):
    n = len(content)
    if n < 0x80:
        ln = bytes([n])
    else:
        nb = n.to_bytes((n.bit_length() + 7) // 8, "big")
        ln = bytes([0x80 | len(nb)]) + nb
    return bytes([tag]) + ln + content


def _int(v):
    return _der(0x02, v.to_bytes(max(1, (v.bit_length() + 8) // 8), "big", signed=True))


def _gstr(s):
    return _der(0x1B, s.encode())


def _ctx(n, content):
    return _der(0xA0 + n, content)


def _seq(*parts):
    return _der(0x30, b"".join(parts))


def _principal(name_type, names):
    return _seq(_ctx(0, _int(name_type)), _ctx(1, _seq(*[_gstr(x) for x in names])))


def as_req(user, realm, nonce=None):
    """An AS-REQ for user@realm to krbtgt/realm, with no pre-authentication: a KDC that serves the realm answers with
    KRB-ERROR 25 (pre-authentication required) for an account that has it - every normal account."""
    if "@" in user:
        cname = _principal(10, [user])                         # NT-ENTERPRISE: a UPN with another suffix
    else:
        cname = _principal(1, [user])
    body = _seq(_ctx(0, _der(0x03, b"\x00\x40\x81\x00\x10")),  # forwardable, renewable, canonicalize, renewable-ok
                _ctx(1, cname), _ctx(2, _gstr(realm)), _ctx(3, _principal(2, ["krbtgt", realm])),
                _ctx(5, _der(0x18, b"20370913024805Z")), _ctx(7, _int(nonce if nonce is not None else random.randint(1, 0x7FFFFFFF))),
                _ctx(8, _seq(_int(18), _int(17), _int(23))))
    return _der(0x6A, _seq(_ctx(1, _int(5)), _ctx(2, _int(10)), _ctx(4, body)))


def _read_tlv(b, i):
    tag = b[i]
    n = b[i + 1]
    i += 2
    if n & 0x80:
        k = n & 0x7F
        n = int.from_bytes(b[i:i + k], "big")
        i += k
    return tag, b[i:i + n], i + n


def parse_krb_answer(b):
    """A KDC's answer -> {kind: 'error'|'as-rep', code, text, stime} (stime: the KDC's clock, from a KRB-ERROR)."""
    b = bytes(b)
    if not b:
        raise ValueError("no answer")
    if b[0] == 0x6B:
        return {"kind": "as-rep", "code": None, "text": "a ticket without pre-authentication (the account does not require it)", "stime": ""}
    if b[0] != 0x7E:
        raise ValueError("not a Kerberos answer (first byte 0x%02x)" % b[0])
    _, inner, _ = _read_tlv(b, 0)
    _, seq, _ = _read_tlv(inner, 0)
    out = {"kind": "error", "code": None, "text": "", "stime": ""}
    i = 0
    while i < len(seq):
        tag, val, i = _read_tlv(seq, i)
        if tag == 0xA6:
            _, v, _ = _read_tlv(val, 0)
            out["code"] = int.from_bytes(v, "big", signed=True)
        elif tag == 0xA4:
            _, v, _ = _read_tlv(val, 0)
            out["stime"] = iso(gentime(v.decode()))
        elif tag == 0xAB:
            _, v, _ = _read_tlv(val, 0)
            out["text"] = v.decode("utf-8", "replace")
    out["text"] = KRB_ERRORS.get(out["code"], out["text"] or "error %s" % out["code"])
    return out


def krb_probe(address, port, user, realm, timeout):
    """Ask a KDC over TCP (then UDP): {ok, answer, code, ms, error}. ok: it answered for the realm."""
    req = as_req(user, realm)
    t0 = time.time()
    data, err = None, ""
    try:
        with socket.create_connection((address, port), timeout=timeout) as s:
            s.sendall(struct.pack(">I", len(req)) + req)
            hdr = b""
            while len(hdr) < 4:
                c = s.recv(4 - len(hdr))
                if not c:
                    raise OSError("the KDC closed the connection")
                hdr += c
            n = struct.unpack(">I", hdr)[0]
            data = b""
            while len(data) < n:
                c = s.recv(n - len(data))
                if not c:
                    break
                data += c
    except (OSError, socket.timeout) as e:
        err = "TCP %d: %s" % (port, short_err(e))
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(timeout)
                s.sendto(req, (address, port))
                data, _ = s.recvfrom(65535)
        except (OSError, socket.timeout) as e2:
            return {"ok": False, "error": "%s; UDP %d: %s" % (err, port, short_err(e2)), "ms": None}
    ms = int((time.time() - t0) * 1000)
    try:
        a = parse_krb_answer(data)
    except (ValueError, IndexError) as e:
        return {"ok": False, "error": "the answer on %d is not a KDC's: %s" % (port, e), "ms": ms}
    ok = a["kind"] == "as-rep" or a["code"] not in (68,)
    return {"ok": ok, "answer": a["text"], "code": a["code"], "kdc_time": a["stime"], "ms": ms,
            "error": "" if ok else a["text"], "no_preauth": a["kind"] == "as-rep"}


def kinit_tgt(kinit, principal, password, realm, address, port, timeout, enterprise=False):
    """A TGT from this KDC only (a krb5.conf naming it, before the system's), in a ticket cache of its own that is
    removed again: {ok, error, ms}."""
    tmp = tempfile.mkdtemp(prefix="site-ad-krb-")
    try:
        conf = os.path.join(tmp, "krb5-%s.conf" % re.sub(r"[^A-Za-z0-9.-]", "_", address))
        with open(conf, "w") as fh:
            fh.write("[libdefaults]\n default_realm = %s\n dns_lookup_kdc = false\n dns_lookup_realm = false\n"
                     " udp_preference_limit = 1\n\n[realms]\n %s = {\n  kdc = %s:%d\n }\n" % (realm, realm, address, port))
        env = dict(os.environ, KRB5_CONFIG=conf + (":/etc/krb5.conf" if os.path.exists("/etc/krb5.conf") else ""),
                   KRB5CCNAME="FILE:" + os.path.join(tmp, "cc"), LC_ALL="C")
        cmd = [kinit] + (["-E"] if enterprise else []) + [principal]
        t0 = time.time()
        p = subprocess.run(cmd, input=(password + "\n").encode(), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=max(10, timeout * 3))
        ms = int((time.time() - t0) * 1000)
        if p.returncode == 0:
            return {"ok": True, "error": "", "ms": ms}
        msg = (p.stderr or p.stdout).decode("utf-8", "replace").strip().splitlines()
        return {"ok": False, "error": re.sub(r"^kinit: ", "", msg[-1] if msg else "kinit exit %d" % p.returncode), "ms": ms}
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": short_err(e), "ms": None}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---- the reader --------------------------------------------------------------------------------------------------------
class Reader(object):
    def __init__(self, p, check_mode=False):
        self.p = p
        self.check_mode = check_mode
        self.ports = dict({"ldap": 389, "ldaps": 636, "kerberos": 88, "dns": 53}, **{k: int(v) for k, v in (p.get("ports") or {}).items() if v})
        self.timeout = int(p.get("timeout") or 5)
        self.now = datetime.datetime.now(datetime.timezone.utc)
        self.not_read = []
        self.errors = []
        self.tmp = tempfile.mkdtemp(prefix="site-ad-reader-")

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- where a DC is reached ------------------------------------------------------------------------------------------
    def endpoint(self, fqdn, inv_address=""):
        e = {}
        for k, v in (self.p.get("endpoints") or {}).items():
            if str(k).lower() == fqdn.lower() or str(k).lower().split(".")[0] == fqdn.split(".")[0].lower():
                e = v or {}
        return {"address": str(e.get("address") or inv_address or fqdn), "ldap": int(e.get("ldap") or self.ports["ldap"]),
                "ldaps": int(e.get("ldaps") or self.ports["ldaps"]), "kerberos": int(e.get("kerberos") or self.ports["kerberos"]),
                "nat": bool(e)}

    def tls(self, fqdn, validate):
        if validate:
            return ldap3.Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=self.p.get("ca_file") or None, valid_names=[fqdn],
                             version=ssl.PROTOCOL_TLS_CLIENT, sni=fqdn)
        return ldap3.Tls(validate=ssl.CERT_NONE, version=ssl.PROTOCOL_TLS_CLIENT, sni=fqdn)


    def connect(self, fqdn, ep, how):
        """how: 'ldaps' (bind over TLS), 'starttls' (389, StartTLS, bind), 'anon' (389, no bind). -> (conn, ms, error)."""
        validate = bool(self.p.get("validate_certs", True))
        if how == "ldaps":
            srv = ldap3.Server(ep["address"], port=ep["ldaps"], use_ssl=True, tls=self.tls(fqdn, validate), get_info=ldap3.NONE,
                               connect_timeout=self.timeout)
        else:
            srv = ldap3.Server(ep["address"], port=ep["ldap"], use_ssl=False, tls=self.tls(fqdn, validate), get_info=ldap3.NONE,
                               connect_timeout=self.timeout)
        kw = {} if how == "anon" else {"authentication": ldap3.SIMPLE, "user": self.p["user"], "password": self.p["password"]}
        t0 = time.time()
        try:
            c = ldap3.Connection(srv, receive_timeout=self.timeout * 3, raise_exceptions=False, auto_referrals=False, **kw)
            c.open()
            if c.closed:
                return None, None, "no answer"
            if how == "starttls" and not c.start_tls():
                return None, None, "StartTLS refused: %s" % (c.result or {}).get("description", "?")
            if how != "anon" and not c.bind():
                r = c.result or {}
                msg = (r.get("description") or "") + (": " + r.get("message") if r.get("message") else "")
                c.unbind()
                return None, int((time.time() - t0) * 1000), "the bind was refused - " + (msg or "?")
            return c, int((time.time() - t0) * 1000), ""
        except Exception as e:                    # noqa: BLE001 - any failure is an answer about this DC
            return None, None, short_err(e)

    def search(self, c, base, flt, attrs, scope=None, controls=None, size=0):
        return self.search_capped(c, base, flt, attrs, scope, controls, size)[0]

    def search_capped(self, c, base, flt, attrs, scope=None, controls=None, size=0):
        """-> (entries, capped). A SUBTREE search reads every page (it follows the paged-results cookie), up to size
        entries when size is set; capped: there were more than that (or the DC's own size limit cut it)."""
        scope = scope if scope is not None else ldap3.SUBTREE
        res, cookie = [], None
        while True:
            try:
                if scope == ldap3.BASE:
                    ok = c.search(base, flt, scope, attributes=attrs, controls=controls)
                else:
                    ok = c.search(base, flt, scope, attributes=attrs, controls=controls, paged_size=PAGE_SIZE, paged_cookie=cookie)
            except Exception as e:                    # noqa: BLE001
                raise LookupError(short_err(e))
            page = [e for e in (c.response or []) if e.get("type") == "searchResEntry"]
            result = c.result or {}
            if not ok and not page and result.get("result") not in (0, 4, 32):
                raise LookupError("%s: %s" % (result.get("description"), result.get("message", "")))
            res += page
            if size and len(res) > size:
                return res[:size], True
            if result.get("result") == 4:             # sizeLimitExceeded: the DC stopped it
                return res, True
            ctl = ((result.get("controls") or {}).get(PAGED_RESULTS) or {}).get("value") or {}
            cookie = ctl.get("cookie") if isinstance(ctl, dict) else None
            if scope == ldap3.BASE or not cookie:
                return res, False

    def base(self, c, dn, attrs):
        r = self.search(c, dn, "(objectClass=*)", attrs, scope=ldap3.BASE)
        return r[0] if r else {}

    # -- DNS ----------------------------------------------------------------------------------------------------------------
    def resolver(self):
        r = dns.resolver.Resolver(configure=not self.p.get("dns_servers"))
        if self.p.get("dns_servers"):
            r.nameservers = [str(x) for x in self.p["dns_servers"]]
        r.port = self.ports["dns"]
        r.lifetime = float(self.timeout) * 2
        r.timeout = float(self.timeout)
        return r

    def srv(self, name):
        try:
            ans = self.resolver().resolve(name, "SRV", tcp=False)
            return sorted({str(a.target).rstrip(".").lower() for a in ans}), ""
        except dns.resolver.NXDOMAIN:
            return [], "no such name (NXDOMAIN)"
        except dns.resolver.NoAnswer:
            return [], "no SRV record"
        except (dns.exception.DNSException, OSError) as e:
            return [], short_err(e) or e.__class__.__name__

    def a(self, name):
        try:
            return sorted(str(x) for x in self.resolver().resolve(name, "A")), ""
        except dns.resolver.NXDOMAIN:
            return [], "no such name (NXDOMAIN)"
        except dns.resolver.NoAnswer:
            return [], "no A record"
        except (dns.exception.DNSException, OSError) as e:
            return [], short_err(e) or e.__class__.__name__

    # -- the certificate of an LDAPS port --------------------------------------------------------------------------------------
    def cert(self, fqdn, ep):
        out = {"subject": "", "issuer": "", "not_after": "", "trusted": None, "error": ""}
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with socket.create_connection((ep["address"], ep["ldaps"]), timeout=self.timeout) as s:
                with ctx.wrap_socket(s, server_hostname=fqdn) as t:
                    der = t.getpeercert(binary_form=True)
        except (OSError, ssl.SSLError, socket.timeout) as e:
            out["error"] = short_err(e)
            return out
        if HAS_CRYPTO and der:
            c = x509.load_der_x509_certificate(der)
            na = getattr(c, "not_valid_after_utc", None) or c.not_valid_after.replace(tzinfo=datetime.timezone.utc)
            out.update(subject=c.subject.rfc4514_string(), issuer=c.issuer.rfc4514_string(), not_after=iso(na))
        elif der:
            out["error"] = "the certificate was read, but the cryptography library is missing to read its dates"
        if self.p.get("validate_certs", True):
            try:
                ctx = ssl.create_default_context(cafile=self.p.get("ca_file") or None)
                with socket.create_connection((ep["address"], ep["ldaps"]), timeout=self.timeout) as s:
                    with ctx.wrap_socket(s, server_hostname=fqdn):
                        pass
                out["trusted"] = True
            except ssl.SSLCertVerificationError as e:
                out["trusted"] = False
                out["trust_error"] = short_err(getattr(e, "verify_message", "") or e)
            except (OSError, ssl.SSLError, socket.timeout) as e:
                out["trusted"] = False
                out["trust_error"] = short_err(e)
        return out

    # -- Kerberos ------------------------------------------------------------------------------------------------------------
    def principal(self):
        u = str(self.p.get("user") or "")
        realm = self.domain.upper()
        if "\\" in u:
            return u.split("\\", 1)[1], realm, False
        if "@" in u:
            name, suffix = u.split("@", 1)
            if suffix.upper() == realm:
                return name, realm, False
            return u, realm, True
        if u.upper().startswith("CN="):
            return rdn(u), realm, False
        return u, realm, False

    def kerberos(self, fqdn, ep):
        mode = str(self.p.get("kerberos") or "auto").lower()
        if mode == "off":
            return None
        name, realm, ent = self.principal()
        if not name:
            return {"kind": "probe", "ok": None, "error": "no user name to ask the KDC about"}
        kinit = shutil.which(self.p.get("kinit") or "kinit")
        if mode in ("auto", "tgt") and kinit and self.p.get("password"):
            r = kinit_tgt(kinit, name, self.p["password"], realm, ep["address"], ep["kerberos"], self.timeout,
                          enterprise=ent)
            r["kind"] = "tgt"
            if not r["ok"]:
                pr = krb_probe(ep["address"], ep["kerberos"], name, realm, self.timeout)
                r["kdc_answers"] = pr["ok"]
                r["probe"] = pr.get("answer") or pr.get("error")
            return r
        if mode == "tgt":
            return {"kind": "tgt", "ok": None, "error": "no kinit in this execution environment" if not kinit else "no password"}
        r = krb_probe(ep["address"], ep["kerberos"], name, realm, self.timeout)
        r["kind"] = "probe"
        return r

    # -- the domain, when ad_reader_domain is not set ------------------------------------------------------------------------
    RESOLV_CONF = "/etc/resolv.conf"

    def anon_rootdse(self, name, address):
        """The rootDSE of a DC, read without a bind (any client may): ({defaultNamingContext, dnsHostName}, error)."""
        ep = self.endpoint(name, address)
        c, _, err = self.connect(name, ep, "anon")
        if c is None:
            return {}, err
        try:
            r = self.base(c, "", ["defaultNamingContext", "dnsHostName"])
            return {"defaultNamingContext": first(r, "defaultNamingContext") or "", "dnsHostName": str(first(r, "dnsHostName") or "").lower()}, ""
        except LookupError as e:
            return {}, str(e)
        finally:
            c.unbind()

    def resolv_search(self):
        """The first search (or domain) suffix of the EE's resolv.conf, or ''."""
        try:
            with open(self.RESOLV_CONF) as fh:
                for line in fh:
                    w = line.split()
                    if len(w) > 1 and w[0] in ("search", "domain"):
                        return w[1].strip(".").lower()
        except OSError:
            pass
        return ""

    def learn_domain(self, inv):
        """The domain to read, and where it came from: ad_reader_domain; the inventory DCs' names; their ansible_host when
        an FQDN; each DC's rootDSE (anonymous, no bind, no password); the EE's resolv.conf search domain. A short
        inventory name becomes the DC's dnsHostName from its rootDSE (the name its LDAPS certificate has), else
        <short>.<domain>. -> (domain, source, why_not); inv is changed in place."""
        dom, source = str(self.p.get("domain") or "").strip().lower().rstrip("."), "ad_reader_domain"
        if not dom:
            doms = sorted({d["name"].split(".", 1)[1] for d in inv if "." in d["name"]})
            if len(doms) > 1:
                return "", "", "the inventory's DCs are in more than one domain (%s): set ad_reader_domain" % ", ".join(doms)
            if doms:
                dom, source = doms[0], "the inventory's DC names"
        if not dom:
            fq = [d for d in inv if "." in d["address"] and not re.search(r"^[\d.]+$|:", d["address"])]
            doms = sorted({d["address"].lower().split(".", 1)[1] for d in fq})
            if len(doms) == 1:
                dom, source = doms[0], "the inventory's ansible_host (%s)" % fq[0]["address"].lower()
        tried = []
        for d in inv:
            if "." in d["name"]:
                continue
            r, err = self.anon_rootdse(d["name"], d["address"])
            if r.get("dnsHostName") or r.get("defaultNamingContext"):
                d["rootdse_name"] = r.get("dnsHostName") or ""
                if not dom:
                    dom = dn_to_dns(r.get("defaultNamingContext")) or (r["dnsHostName"].split(".", 1)[1] if "." in r.get("dnsHostName", "") else "")
                    source = "the rootDSE of %s (read without a bind)" % d["name"]
            else:
                tried.append("%s: %s" % (d["name"], err or "no answer"))
        if not dom:
            dom = self.resolv_search()
            source = "the execution environment's DNS search domain (resolv.conf)"
        if not dom:
            return "", "", ("no domain to read: the inventory's DCs have short names (dc01, not dc01.corp.example.mil), their ansible_host is "
                            "no FQDN, no DC answered a rootDSE read on LDAP 389 (%s), and the execution environment's resolv.conf has no "
                            "search domain - set ad_reader_domain" % ("; ".join(tried[:3]) or "no DC in the inventory"))
        for d in inv:                                   # a short inventory name (dc01) is a DC of this domain
            if "." not in d["name"]:
                rd = d.pop("rootdse_name", "")
                d["name"] = rd if rd.endswith("." + dom) else d["name"] + "." + dom
        return dom, source, ""

    # -- the whole read ------------------------------------------------------------------------------------------------------
    def run(self):
        p = self.p
        inv = [{"name": str(d.get("name") or "").lower(), "address": str(d.get("address") or "")} for d in p.get("dcs") or [] if d.get("name")]
        self.domain, source, why = self.learn_domain(inv)
        if not self.domain:
            return {"ok": False, "why": why}
        out = {"ok": True, "why": "", "domain": self.domain, "domain_source": source, "read_at": iso(self.now),
               "user": p.get("user") or "", "inventory": [d["name"] for d in inv]}

        # 1. who to start from: the inventory's DCs, then the SRV records'
        srv_dc, srv_err = self.srv("_ldap._tcp.dc._msdcs." + self.domain)
        start = [d["name"] for d in inv] + [x for x in srv_dc if x not in [d["name"] for d in inv]]
        inv_addr = dict((d["name"], d["address"]) for d in inv)
        conn, read_from, tried, refused = None, "", [], False
        for fq in start:
            ep = self.endpoint(fq, inv_addr.get(fq, ""))
            for how in ("ldaps", "starttls"):
                c, _, err = self.connect(fq, ep, how)
                if c:
                    conn, read_from = c, fq
                    out["read_via"] = "LDAPS" if how == "ldaps" else "StartTLS on %d" % ep["ldap"]
                    break
                tried.append("%s %s: %s" % (fq, "LDAPS" if how == "ldaps" else "StartTLS", err))
                # the account or its password was refused: asking again (another DC, StartTLS) would only count more
                # wrong passwords against it - and lock it out
                refused = err.startswith("the bind was refused")
                if refused:
                    break
            if conn or refused:
                break
        if not conn:
            out.update(ok=False, why=("no DC of %s could be read: %s" % (self.domain, "; ".join(tried[:6])) if tried else
                                      "no DC of %s to read: none in the inventory, and DNS has no _ldap._tcp.dc._msdcs.%s (%s)"
                                      % (self.domain, self.domain, srv_err)))
            out["dns_srv_error"] = srv_err
            return out
        out["read_from"] = read_from
        try:
            self.read_domain(conn, out)
        except LookupError as e:
            out.update(ok=False, why="reading the domain on %s failed: %s" % (read_from, e))
            return out

        # 2. every DC: AD's and the inventory's
        ad_names = [d["fqdn"] for d in out["dcs_ad"]]
        all_names = ad_names + [d["name"] for d in inv if d["name"] not in ad_names and d["name"].split(".")[0] not in [x.split(".")[0] for x in ad_names]]
        dcs = []
        for fq in all_names:
            rec = next((d for d in out["dcs_ad"] if d["fqdn"] == fq), None)
            in_inv = next((d for d in inv if d["name"] == fq or d["name"].split(".")[0] == fq.split(".")[0]), None)
            dc = self.read_dc(fq, rec, in_inv, out)
            dcs.append(dc)
        # clocks against the others (the median of the offsets cancels this side's own clock)
        offs = sorted(d["raw_offset"] for d in dcs if d.get("raw_offset") is not None)
        if len(offs) >= 2:
            mid = offs[len(offs) // 2] if len(offs) % 2 else (offs[len(offs) // 2 - 1] + offs[len(offs) // 2]) / 2.0
            for d in dcs:
                if d.get("raw_offset") is not None:
                    d["offset"] = round(d["raw_offset"] - mid, 1)
        out["dcs"] = dcs

        # 3. DNS
        recs = ["_ldap._tcp.dc._msdcs.%s" % self.domain, "_kerberos._tcp.dc._msdcs.%s" % self.domain,
                "_ldap._tcp.%s" % self.domain, "_kerberos._tcp.%s" % self.domain, "_ldap._tcp.gc._msdcs.%s" % out.get("forest", self.domain)]
        out["dns_srv"] = []
        for r in recs:
            t, e = (srv_dc, srv_err) if r == recs[0] else self.srv(r)
            out["dns_srv"].append({"record": r, "targets": t, "error": e})

        # 4. the privileged groups, the accounts
        for what, fn in (("the privileged groups", self.read_groups), ("the accounts", self.read_accounts)):
            try:
                fn(conn, out)
            except LookupError as e:
                self.not_read.append("%s: %s" % (what, e))
        conn.unbind()

        # 5. the last run's members
        self.previous(out)
        out["not_read"] = self.not_read
        out["errors"] = self.errors
        return out

    def read_domain(self, c, out):
        root = self.base(c, "", ["defaultNamingContext", "configurationNamingContext", "schemaNamingContext",
                                 "rootDomainNamingContext", "domainFunctionality", "forestFunctionality", "namingContexts"])
        nc = first(root, "defaultNamingContext") or ""
        cfg = first(root, "configurationNamingContext") or ""
        sch = first(root, "schemaNamingContext") or ""
        forest_dn = first(root, "rootDomainNamingContext") or nc
        out.update(base_dn=nc, config_dn=cfg, forest=dn_to_dns(forest_dn), is_root=(forest_dn.lower() == nc.lower()),
                   naming_contexts=list((root.get("attributes") or {}).get("namingContexts") or []))
        d = self.base(c, nc, ["objectSid", "msDS-Behavior-Version", "lockoutDuration", "fSMORoleOwner"])
        self.domain_sid = sid_text(first(d, "objectSid"))
        info = {"sid": self.domain_sid, "level": first(d, "msDS-Behavior-Version"), "lockout_duration": first(d, "lockoutDuration")}
        part = self.base(c, "CN=Partitions," + cfg, ["msDS-Behavior-Version", "fSMORoleOwner"])
        info["forest_level"] = first(part, "msDS-Behavior-Version")
        info["schema_version"] = first(self.base(c, sch, ["objectVersion", "fSMORoleOwner"]), "objectVersion")
        try:
            ds = self.base(c, "CN=Directory Service,CN=Windows NT,CN=Services," + cfg, ["tombstoneLifetime"])
            info["tombstone_days"] = first(ds, "tombstoneLifetime")
        except LookupError:
            pass
        try:
            rb = self.base(c, "CN=Recycle Bin Feature,CN=Optional Features,CN=Directory Service,CN=Windows NT,CN=Services," + cfg,
                           ["msDS-EnabledFeatureBL"])
            info["recycle_bin"] = bool((rb.get("attributes") or {}).get("msDS-EnabledFeatureBL")) if rb else None
        except LookupError:
            info["recycle_bin"] = None
        k = self.search(c, nc, "(sAMAccountName=krbtgt)", ["pwdLastSet"])
        info["krbtgt_pwd_last_set"] = iso(filetime(first(k[0], "pwdLastSet"))) if k else ""
        try:
            rid = self.base(c, "CN=RID Manager$,CN=System," + nc, ["rIDAvailablePool", "fSMORoleOwner"])
            pool = first(rid, "rIDAvailablePool")
            if pool is not None:
                pool = int(pool)
                info["rid"] = {"total": pool >> 32, "issued": pool & 0xFFFFFFFF}
            else:
                self.not_read.append("the RID pool (rIDAvailablePool): " + NEEDS_ADMIN)
        except LookupError as e:
            self.not_read.append("the RID pool: %s" % e)
        out["domain_info"] = info

        # the DCs AD knows: the server objects in Configuration (site, NTDS Settings) and the DC computer accounts (OS)
        servers = self.search(c, "CN=Sites," + cfg, "(objectClass=server)", ["dNSHostName", "serverReference", "cn"])
        dsas = self.search(c, "CN=Sites," + cfg, "(objectClass=nTDSDSA)", ["options", "objectGUID", "invocationId", "msDS-isRODC", "objectCategory"])
        comps = self.search(c, nc, "(&(objectCategory=computer)(|(primaryGroupID=516)(primaryGroupID=521)))",
                            ["dNSHostName", "operatingSystem", "operatingSystemVersion", "cn", "userAccountControl"])
        dsa_of = {}
        for e in dsas:
            dsa_of[parent_dn(e["dn"]).lower()] = e
        comp_of = {}
        for e in comps:
            comp_of[str(first(e, "dNSHostName") or first(e, "cn") or "").lower()] = e
        self.dsa_name = {}                          # NTDS Settings DN, objectGUID, invocationId -> fqdn
        dcs = []
        for s in servers:
            dsa = dsa_of.get(s["dn"].lower())
            if not dsa:
                continue                            # a server object with no NTDS Settings: not a DC (or demoted)
            fq = str(first(s, "dNSHostName") or "").lower() or (first(s, "cn") or "").lower() + "." + self.domain
            if not fq.endswith("." + self.domain) and fq != self.domain:
                continue                            # a DC of another domain of the forest
            comp = comp_of.get(fq) or {}
            opts = int(first(dsa, "options") or 0)
            rodc = str(first(dsa, "msDS-isRODC") or "").upper() == "TRUE" or "NTDS-DSA-RO" in str(first(dsa, "objectCategory") or "")
            rec = {"fqdn": fq, "name": rdn(s["dn"]).upper(), "site": site_of(s["dn"]), "gc": bool(opts & 1), "rodc": rodc,
                   "os": first(comp, "operatingSystem") or "", "os_version": first(comp, "operatingSystemVersion") or "",
                   "dsa_dn": dsa["dn"], "dsa_guid": guid_text(first(dsa, "objectGUID")),
                   "invocation_id": guid_text(first(dsa, "invocationId")), "roles": [], "computer": bool(comp)}
            dcs.append(rec)
            for k2 in (dsa["dn"].lower(), rec["dsa_guid"], rec["invocation_id"]):
                self.dsa_name[k2] = fq
        # DC accounts with no server object in Configuration (a DC removed badly, or one being built)
        for fq, comp in comp_of.items():
            if fq and not any(d["fqdn"] == fq for d in dcs) and fq.endswith("." + self.domain):
                dcs.append({"fqdn": fq, "name": fq.split(".")[0].upper(), "site": "", "gc": False,
                            "rodc": bool(int(first(comp, "userAccountControl") or 0) & UF_PARTIAL_SECRETS),
                            "os": first(comp, "operatingSystem") or "", "os_version": first(comp, "operatingSystemVersion") or "",
                            "dsa_dn": "", "dsa_guid": "", "invocation_id": "", "roles": [], "computer": True, "no_server": True})
        dcs.sort(key=lambda x: x["fqdn"])
        out["dcs_ad"] = dcs

        # FSMO: fSMORoleOwner of the five objects
        fsmo = []
        for role, dn in (("PDC emulator", nc), ("RID master", "CN=RID Manager$,CN=System," + nc), ("Infrastructure master", "CN=Infrastructure," + nc),
                         ("Schema master", sch), ("Domain naming master", "CN=Partitions," + cfg)):
            try:
                owner = first(self.base(c, dn, ["fSMORoleOwner"]), "fSMORoleOwner") or ""
            except LookupError as e:
                fsmo.append({"role": role, "holder": "", "holder_dn": "", "error": str(e)})
                continue
            holder = self.dsa_name.get(owner.lower(), "")
            deleted = "\\0ADEL:" in owner or "\nDEL:" in owner
            fsmo.append({"role": role, "holder": holder, "holder_dn": owner, "deleted": deleted,
                         "holder_name": holder.split(".")[0].upper() if holder else rdn(parent_dn(owner)).upper()})
            for d in dcs:
                if d["fqdn"] == holder:
                    d["roles"].append(role)
        out["fsmo"] = fsmo

    def read_dc(self, fq, rec, inv, out):
        dc = dict(rec or {"fqdn": fq, "name": fq.split(".")[0].upper(), "site": "", "gc": False, "rodc": False, "os": "", "roles": []})
        dc.update(fqdn=fq, in_ad=rec is not None, in_inventory=inv is not None)
        ep = self.endpoint(fq, (inv or {}).get("address", ""))
        dc["address"] = ep["address"]
        # DNS: its A record, against the inventory's address when that is an address
        dc["dns_a"], dc["dns_a_error"] = self.a(fq)
        if inv and re.match(r"^\d+\.\d+\.\d+\.\d+$", inv.get("address") or "") and not ep["nat"]:
            dc["inventory_address"] = inv["address"]
        if ep["nat"]:
            dc["nat"] = True
        # LDAP 389 (anonymous rootDSE), LDAPS 636 (bind), StartTLS
        a, ms, err = self.connect(fq, ep, "anon")
        dc["ldap"] = {"ok": a is not None, "ms": ms, "error": err}
        bound, how = None, ""
        b, ms, err = self.connect(fq, ep, "ldaps")
        dc["ldaps"] = {"ok": b is not None, "ms": ms, "error": err}
        if b:
            bound, how = b, "LDAPS"
        else:
            b2, ms2, err2 = self.connect(fq, ep, "starttls") if a is not None else (None, None, "")
            dc["starttls"] = {"ok": b2 is not None, "ms": ms2, "error": err2}
            if b2:
                bound, how = b2, "StartTLS"
        dc["bind"] = {"ok": bound is not None, "via": how}
        if not bound:
            dc["bind"]["error"] = dc["ldaps"]["error"] or (dc.get("starttls") or {}).get("error") or dc["ldap"]["error"]
        # rootDSE: synchronized, GC ready, its clock, its USN
        src = bound or a
        if src is not None:
            try:
                t0 = time.time()
                r = self.base(src, "", ["isSynchronized", "isGlobalCatalogReady", "currentTime", "highestCommittedUSN", "dsServiceName", "dnsHostName"])
                t1 = time.time()
                ct = gentime(first(r, "currentTime"))
                dc.update(synchronized=str(first(r, "isSynchronized") or "").upper() == "TRUE" if first(r, "isSynchronized") is not None else None,
                          gc_ready=str(first(r, "isGlobalCatalogReady") or "").upper() == "TRUE" if first(r, "isGlobalCatalogReady") is not None else None,
                          current_time=iso(ct), usn=first(r, "highestCommittedUSN"))
                if ct:
                    local_mid = datetime.datetime.fromtimestamp((t0 + t1) / 2.0, datetime.timezone.utc)
                    dc["raw_offset"] = round((ct - local_mid).total_seconds() + 0.5, 1)   # currentTime has whole seconds
            except LookupError as e:
                dc["rootdse_error"] = str(e)
        # replication: what a plain user may read on this DC's own copy of each NC head
        if bound:
            dc["repl"] = self.read_repl(bound, out)
            bound.unbind()
        if a is not None:
            a.unbind()
        # the LDAPS certificate, the KDC - not tried on a DC whose LDAP ports both gave no answer (it is down, or not there)
        if a is None and not dc["ldaps"]["ok"] and "timed out" in (dc["ldap"]["error"] + dc["ldaps"]["error"]):
            dc["cert"] = {"error": "not tried: the DC does not answer"}
            dc["kerberos"] = None if str(self.p.get("kerberos") or "auto") == "off" else \
                {"kind": "probe", "ok": None, "error": "not tried: the DC does not answer"}
        else:
            dc["cert"] = self.cert(fq, ep)
            dc["kerberos"] = self.kerberos(fq, ep)
        return dc

    def read_repl(self, c, out):
        """{source, links: [{nc, partner, last_success, last_attempt, failures, result}], cursors, why}. msDS-NCReplInboundNeighbors
        (Windows builds it), else repsFrom, else replUpToDateVector (when each partner's changes last arrived)."""
        ncs = [n for n in out.get("naming_contexts") or [] if n]
        res = {"source": "", "links": [], "cursors": [], "why": ""}
        seen_any = False
        for nc in ncs:
            try:
                e = self.base(c, nc, ["msDS-NCReplInboundNeighbors", "repsFrom", "replUpToDateVector"])
            except LookupError:
                continue
            nbr = raw_all(e, "msDS-NCReplInboundNeighbors")
            reps = raw_all(e, "repsFrom")
            utd = raw_all(e, "replUpToDateVector")
            if nbr:
                res["source"] = res["source"] or "msDS-NCReplInboundNeighbors"
                for v in nbr:
                    x = parse_neighbor_xml(v)
                    res["links"].append(self._link(nc, x))
                seen_any = True
            elif reps:
                res["source"] = res["source"] or "repsFrom"
                for v in reps:
                    try:
                        x = parse_reps_from(v)
                    except (ValueError, struct.error) as err:
                        self.errors.append("repsFrom on %s: %s" % (nc, err))
                        continue
                    res["links"].append(self._link(nc, x))
                seen_any = True
            if utd:
                seen_any = True
                for v in utd:
                    try:
                        for cur in parse_utd(v):
                            name = self.dsa_name.get(cur["invocation_id"], "")
                            res["cursors"].append({"nc": nc, "partner": name, "invocation_id": cur["invocation_id"], "last_sync": cur["last_sync"]})
                    except (ValueError, struct.error) as err:
                        self.errors.append("replUpToDateVector on %s: %s" % (nc, err))
        if not res["source"] and res["cursors"]:
            res["source"] = "replUpToDateVector"
        if not seen_any:
            res["why"] = NEEDS_ADMIN if len(out.get("dcs_ad") or []) > 1 else "one DC: no partner"
        return res

    def _link(self, nc, x):
        partner = self.dsa_name.get(str(x.get("partner_dn") or "").lower()) or self.dsa_name.get(str(x.get("partner_guid") or "").lower()) \
            or self.dsa_name.get(str(x.get("invocation_id") or "").lower()) or rdn(parent_dn(x.get("partner_dn") or "")) or x.get("address", "")
        return {"nc": nc, "partner": partner, "last_success": x["last_success"], "last_attempt": x["last_attempt"],
                "failures": x["failures"], "result": x["result"]}

    def read_groups(self, c, out):
        nc = out["base_dn"]
        hours = float(self.p.get("hours") or 24)
        since = self.now - datetime.timedelta(hours=hours)
        ids = [("sid", self.domain_sid + "-%d" % r, n) for n, r in PRIV_RIDS]
        if out.get("is_root"):
            ids += [("sid", self.domain_sid + "-%d" % r, n) for n, r in PRIV_ROOT_RIDS]
        ids += [("sid", s, "") for s in PRIV_BUILTIN]
        for g in self.p.get("privileged_groups_extra") or []:
            g = str(g).strip()
            if g:
                ids.append(("sid", g, "") if re.match(r"^S-1-", g, re.I) else ("name", g, g))
        groups, seen, meta_ok = [], set(), None
        for kind, ident, label in ids:
            flt = ("(&(objectClass=group)(objectSid=%s))" % escape_bytes(sid_bytes(ident)) if kind == "sid"
                   else "(&(objectClass=group)(|(sAMAccountName=%s)(cn=%s)))" % (escape_filter_chars(ident), escape_filter_chars(ident)))
            try:
                r = self.search(c, nc, flt, ["cn", "sAMAccountName", "objectSid", "member", "msDS-ReplValueMetaData"])
            except LookupError as e:
                self.not_read.append("group %s: %s" % (label or ident, e))
                continue
            if not r:
                if kind == "name":
                    self.not_read.append("group %s: not found" % ident)
                continue
            e = r[0]
            if e["dn"].lower() in seen:
                continue
            seen.add(e["dn"].lower())
            members = sorted(str(m) for m in (e.get("attributes") or {}).get("member") or [])
            g = {"name": first(e, "cn") or label, "sam": first(e, "sAMAccountName") or "", "sid": sid_text(first(e, "objectSid")) or ident,
                 "dn": e["dn"], "members": len(members), "member_dns": members, "member_names": [rdn(m) for m in members], "changes": []}
            vm = raw_all(e, "msDS-ReplValueMetaData")
            if vm:
                meta_ok = True
                for v in vm:
                    x = parse_value_meta_xml(v)
                    if x["attribute"] and x["attribute"].lower() != "member":
                        continue
                    t = from_iso(x["changed"])
                    if not t or t < since:
                        continue
                    g["changes"].append({"member": rdn(x["member"]), "member_dn": x["member"], "action": "removed" if x["deleted"] else "added",
                                         "time": x["changed"], "dc": (self.dsa_name.get(x["dsa_dn"].lower()) or rdn(parent_dn(x["dsa_dn"]))).split(".")[0].upper()})
            elif members:
                meta_ok = meta_ok or False
            groups.append(g)
        out["groups"] = groups
        out["group_meta"] = "read" if meta_ok else ("" if meta_ok is None else NEEDS_ADMIN)
        if meta_ok is False:
            self.not_read.append("when members were added or removed (msDS-ReplValueMetaData of member): " + NEEDS_ADMIN)

    def read_accounts(self, c, out):
        nc = out["base_dn"]
        hours = float(self.p.get("hours") or 24)
        since = self.now - datetime.timedelta(hours=hours)
        gen = since.strftime("%Y%m%d%H%M%S.0Z")
        mx = int(self.p.get("changes_max") or 500)
        # locked out now: lockoutTime within the domain's lockout duration; a lockout that lasts until an admin unlocks the
        # account (lockoutDuration 0 or the most negative value) never runs out. Only those are asked for, every page of them.
        win = lockout_window((out.get("domain_info") or {}).get("lockout_duration"))
        try:
            lock_from = self.now - win if win is not None else None
        except OverflowError:
            lock_from = None
        if lock_from is not None and lock_from <= EPOCH_1601:
            lock_from = None
        flt = "(lockoutTime>=%d)" % max(1, to_filetime(lock_from)) if lock_from is not None else "(lockoutTime>=1)"
        locked = []
        found, out["locked_out_capped"] = self.search_capped(c, nc, "(&(objectCategory=person)(objectClass=user)%s)" % flt,
                                                             ["sAMAccountName", "lockoutTime"], size=mx)
        for e in found:
            t = filetime(first(e, "lockoutTime"))
            if not t:
                continue
            if lock_from is not None and t <= lock_from:
                continue                                    # its lockout has run out
            locked.append({"name": first(e, "sAMAccountName") or rdn(e["dn"]), "time": iso(t)})
        out["locked_out"] = sorted(locked, key=lambda x: x["time"], reverse=True)
        found, out["created_capped"] = self.search_capped(
            c, nc, "(&(|(objectCategory=person)(objectClass=group)(objectClass=computer))(whenCreated>=%s))" % gen,
            ["sAMAccountName", "whenCreated", "objectClass"], size=mx)
        out["created"] = [{"class": self._class(e), "name": first(e, "sAMAccountName") or rdn(e["dn"]), "dn": e["dn"],
                           "time": iso(gentime(first(e, "whenCreated")))} for e in found]
        changed, out["changes_capped"] = self.search_capped(
            c, nc, "(&(|(objectCategory=person)(objectClass=computer))(whenChanged>=%s)(!(whenCreated>=%s)))" % (gen, gen),
            ["sAMAccountName", "userAccountControl", "lockoutTime", "whenChanged", "objectClass",
             "msDS-ReplAttributeMetaData", "replPropertyMetaData"], size=mx)
        how_seen = set()
        acc = []
        for e in changed:
            uac = int(first(e, "userAccountControl") or 0)
            meta = {}
            am = raw_all(e, "msDS-ReplAttributeMetaData")
            pm = raw_all(e, "replPropertyMetaData")
            if am:
                how_seen.add("metadata")
                for v in am:
                    x = parse_attr_meta_xml(v)
                    if x["attribute"] in ("userAccountControl", "lockoutTime"):
                        meta[x["attribute"]] = {"time": x["time"], "dc": self.dsa_name.get(x["dsa_dn"].lower()) or rdn(parent_dn(x["dsa_dn"]))}
            elif pm:
                how_seen.add("metadata")
                for attid, x in parse_prop_meta(pm[0]).items():
                    if attid in ATTID:
                        meta[ATTID[attid]] = {"time": x["time"], "dc": self.dsa_name.get(x["invocation_id"], "")}
            if meta:
                for attr, m in meta.items():
                    t = from_iso(m["time"])
                    if not t or t < since:
                        continue
                    if attr == "userAccountControl":
                        what = "disabled" if uac & UF_ACCOUNTDISABLE else "enabled"
                    else:
                        what = "unlocked" if not filetime(first(e, "lockoutTime")) else "locked out"
                    acc.append({"class": self._class(e), "name": first(e, "sAMAccountName") or rdn(e["dn"]), "change": what, "time": m["time"],
                                "dc": (m["dc"] or "").split(".")[0].upper(), "how": "metadata"})
            else:
                how_seen.add("whenChanged")
                acc.append({"class": self._class(e), "name": first(e, "sAMAccountName") or rdn(e["dn"]),
                            "change": "disabled" if uac & UF_ACCOUNTDISABLE else "enabled", "time": iso(gentime(first(e, "whenChanged"))),
                            "dc": "", "how": "whenChanged"})
        out["account_changes"] = sorted(acc, key=lambda x: x["time"], reverse=True)
        out["account_changes_how"] = sorted(how_seen)
        self.not_read.append("accounts deleted (the Deleted Objects container): " + NEEDS_ADMIN)

    @staticmethod
    def _class(e):
        cls = [str(x).lower() for x in (e.get("attributes") or {}).get("objectClass") or []]
        return "computer" if "computer" in cls else ("group" if "group" in cls else "user")

    # -- the last run's members ----------------------------------------------------------------------------------------------
    def previous(self, out):
        p = self.p
        now_members = {g["name"]: g["member_dns"] for g in out.get("groups") or []}
        out["members_now"] = {"domain": self.domain, "read_at": iso(self.now), "groups": now_members}
        prev, why = None, ""
        if p.get("history_file"):
            path = p["history_file"]
            try:
                with open(path) as fh:
                    data = json.load(fh)
                prev = data.get(self.domain) if isinstance(data, dict) else None
                if prev:
                    prev = dict(prev, source="the file %s" % path)
                else:
                    why = "no earlier list for %s in %s: the first run" % (self.domain, path)
            except (OSError, ValueError) as e:
                why = "no earlier list: the first run, or %s could not be read (%s)" % (path, short_err(e))
            if not self.check_mode and now_members:
                try:
                    data = {}
                    if os.path.exists(path):
                        with open(path) as fh:
                            data = json.load(fh)
                    data = data if isinstance(data, dict) else {}
                    data[self.domain] = out["members_now"]
                    d = os.path.dirname(path)
                    if d and not os.path.isdir(d):
                        os.makedirs(d, mode=0o750)
                    tmp = path + ".tmp"
                    with open(tmp, "w") as fh:
                        json.dump(data, fh, indent=1, sort_keys=True)
                    os.replace(tmp, path)
                    out["history_written"] = path
                except (OSError, ValueError) as e:
                    self.errors.append("the history file %s could not be written: %s" % (path, short_err(e)))
        elif p.get("history_job_id") and p.get("history_template_id"):
            prev, why = self.previous_from_aap()
        else:
            why = "no earlier list: not run from AAP (no job ID), and no ad_reader_history_file"
        out["previous"] = prev
        out["previous_why"] = why

    def previous_from_aap(self):
        if not HAS_AAP:
            return None, "no earlier list: the AAP helper is missing"
        p = self.p
        nocred = ('no earlier list: the first run, or the last run\'s artifact couldn\'t be read (the AAP credential?) - '
                  'attach a credential of type "Red Hat Ansible Automation Platform" to this job template')
        api = Api(connection(), p.get("history_ca_path") or "", self.timeout * 3, ca_setting="report_history_aap_ca_path", no_credential=nocred)
        try:
            why = api.probe()
            if why:
                return None, why if why == nocred else "no earlier list: the first run, or the last run's artifact couldn't be read (the AAP credential?): " + why
            code, data = api.call("GET", "jobs/?job_template=%d&id__lt=%d&status__in=successful,failed&job_type=run&order_by=-id&page_size=%d"
                                  % (int(p["history_template_id"]), int(p["history_job_id"]), max(1, int(p.get("history_runs") or 5))))
            if code != 200:
                return None, "no earlier list: the first run, or the last run's artifact couldn't be read (the AAP credential?): HTTP %d %s" % (code, error_text(data))
            for j in data.get("results") or []:
                c, d = api.call("GET", "jobs/%d/" % int(j.get("id")))
                art = (d.get("artifacts") or {}) if c == 200 else {}
                m = (art.get("ad_privileged_members") or {}) if isinstance(art, dict) else {}
                if isinstance(m, dict) and str(m.get("domain") or "").lower() == self.domain and isinstance(m.get("groups"), dict):
                    return dict(m, source="job %s" % j.get("id")), ""
            return None, "no earlier list: the first run, or the last run's artifact couldn't be read (the AAP credential?)"
        except Exception as e:                              # noqa: BLE001 - never fail the report over the history
            return None, "no earlier list: the first run, or the last run's artifact couldn't be read (the AAP credential?): %s" % short_err(e)


def main():
    module = AnsibleModule(
        argument_spec=dict(
            domain=dict(type="str", default=""), dcs=dict(type="list", elements="dict", default=[]),
            user=dict(type="str", default=""), password=dict(type="str", default="", no_log=True),
            ports=dict(type="dict", default={}),
            endpoints=dict(type="dict", default={}), dns_servers=dict(type="list", elements="str", default=[]),
            validate_certs=dict(type="bool", default=True), ca_file=dict(type="str", default=""), timeout=dict(type="int", default=5),
            kerberos=dict(type="str", default="auto", choices=["auto", "tgt", "probe", "off"]), kinit=dict(type="str", default="kinit"),
            hours=dict(type="float", default=24), privileged_groups_extra=dict(type="list", elements="str", default=[]),
            changes_max=dict(type="int", default=500), history_file=dict(type="str", default=""),
            history_job_id=dict(type="int", default=0), history_template_id=dict(type="int", default=0),
            history_runs=dict(type="int", default=5), history_ca_path=dict(type="str", default="")),
        supports_check_mode=True)
    missing = [n for n, ok in (("ldap3", HAS_LDAP3), ("dnspython", HAS_DNS)) if not ok]
    if missing:
        module.fail_json(msg="reader mode needs the Python libraries %s on the AAP side (the Windows EE and the all-in-one EE have "
                             "them: ee/windows/requirements.txt)" % " and ".join(missing))
    if not (module.params["user"] and module.params["password"]):
        module.fail_json(msg="reader mode has no user name and password to bind with: give the template a Machine credential (a plain "
                             "domain user) or set ad_reader_user and ad_reader_password")
    r = Reader(module.params, module.check_mode)
    try:
        out = r.run()
    finally:
        r.close()
    module.exit_json(changed=bool(out.get("history_written")), reader=out)


if __name__ == "__main__":
    main()
