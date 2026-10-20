# -*- coding: utf-8 -*-
"""Filters for the certificate inventory (roles/cert_inventory): every certificate report's rows - Linux
(roles/check_certs), Windows (roles/win_check_certs), VMware (roles/vmware_vm, site_vmware_certs) and network
devices (roles/net_certs) - turned into one record with the same fields, for the CSV/JSON files and the SQL
Server table a dashboard (Power BI ...) reads."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import csv
import hashlib
import io
import ipaddress

# the record's fields, in the CSV's column order (the SQL table has the same names)
# (id: the same host, place and certificate keep their id from run to run - the SQL table's key)
FIELDS = ["id", "source", "host", "location", "subject", "issuer", "sans", "not_after", "days", "status", "tier",
          "trust_store", "in_use", "public_key", "sig_alg", "serial", "sha1", "sha256", "scanned_at", "job"]

# Windows' names for a signature algorithm -> openssl's (what Linux, VMware and the network scan say)
SIG_ALGS = {
    "sha1rsa": "sha1WithRSAEncryption", "sha256rsa": "sha256WithRSAEncryption", "sha384rsa": "sha384WithRSAEncryption",
    "sha512rsa": "sha512WithRSAEncryption", "md5rsa": "md5WithRSAEncryption", "rsassa-pss": "rsassaPss",
    "sha1ecdsa": "ecdsa-with-SHA1", "sha256ecdsa": "ecdsa-with-SHA256", "sha384ecdsa": "ecdsa-with-SHA384",
    "sha512ecdsa": "ecdsa-with-SHA512",
}


def norm_serial(s):
    """A serial number as whole bytes in upper-case hex, without the 00 byte DER adds before a high bit
    (Windows keeps it, openssl does not): '0F96...' stays, '0080AB' -> '80AB', 'ABC' -> '0ABC'."""
    s = str(s or "").strip().upper().replace(":", "")
    if s.startswith("0X"):
        s = s[2:]
    if len(s) % 2:
        s = "0" + s
    while len(s) > 2 and s.startswith("00"):
        s = s[2:]
    return s


def norm_sans(sans):
    """The names (subjectAltName) as 'DNS:x' / 'IP:x' / 'email:x' / 'URI:x': a bare name (VMware's) gets its kind."""
    if isinstance(sans, str):
        sans = [x.strip() for x in sans.replace(";", ",").split(",")]
    out = []
    for n in sans or []:
        n = str(n).strip()
        if not n:
            continue
        if ":" not in n or n.count(":") > 1 and not n.split(":", 1)[0].isalpha():
            try:
                ipaddress.ip_address(n)
                n = "IP:" + n
            except ValueError:
                n = "DNS:" + n
        elif n.lower().startswith("ip address:"):
            n = "IP:" + n.split(":", 1)[1].strip()
        if n not in out:
            out.append(n)
    return out


def tier_of(r):
    """root / intermediate / ca-signed / self-signed - from the row's own tier, or its CA flag and issuer."""
    t = str(r.get("tier") or "").strip().lower()
    if t:
        return t
    subject, issuer = str(r.get("subject") or ""), str(r.get("issuer") or "")
    if not issuer:
        return ""
    self_signed = bool(r.get("self_signed")) or issuer == subject
    if self_signed:
        # root: a self-signed CA that signs certificates or that a host trusts - not a server's own self-signed one
        return "root" if r.get("ca") and (r.get("signs") or r.get("trust")) else "self-signed"
    return "intermediate" if r.get("ca") else "ca-signed"


def status_of(days, warn, crit, notice):
    """expired / critical / warning / notice / ok - the report's colours."""
    if days is None:
        return "unknown"
    if days < 0:
        return "expired"
    if days <= crit:
        return "critical"
    if days <= warn:
        return "warning"
    if days <= notice:
        return "notice"
    return "ok"


def cert_records(rows, source, warn_days=30, crit_days=7, notice_days=60, scanned_at="", job=""):
    """A report's rows -> the inventory's records (FIELDS), one per host, place and certificate.

    rows: dicts with host, where (or location), subject, issuer, sans, expires (or not_after), days, trust,
    in_use, key, sig_alg, serial, sha1, sha256, and tier (or ca and self_signed)."""
    out, seen = [], set()
    for r in rows or []:
        r = dict(r or {})
        try:
            days = int(r.get("days"))
        except (TypeError, ValueError):
            days = None
        if days is not None and days <= -99999:
            days = None                                  # a certificate whose date could not be read
        location = str(r.get("location") or r.get("where") or "")
        sha1 = str(r.get("sha1") or "").upper().replace(":", "")
        ident = "|".join([source, str(r.get("host") or ""), location, sha1 or str(r.get("subject") or "")])
        rec = {
            "id": hashlib.sha256(ident.encode("utf-8")).hexdigest(),
            "source": source,
            "host": str(r.get("host") or ""),
            "location": location,
            "subject": str(r.get("subject") or ""),
            "issuer": str(r.get("issuer") or ""),
            "sans": norm_sans(r.get("sans")),
            "not_after": str(r.get("expires") or r.get("not_after") or "")[:10],
            "days": days,
            "status": status_of(days, int(warn_days), int(crit_days), int(notice_days)),
            # a certificate a server presents for its own service is its own, whatever its CA flags say: never a root
            "tier": "self-signed" if tier_of(r) == "root" and r.get("in_use") is True else tier_of(r),
            "trust_store": bool(r.get("trust")),
            "in_use": r.get("in_use") if isinstance(r.get("in_use"), bool) else None,
            "public_key": str(r.get("public_key") or r.get("key") or ""),
            "sig_alg": SIG_ALGS.get(str(r.get("sig_alg") or "").lower(), str(r.get("sig_alg") or "")),
            "serial": norm_serial(r.get("serial")),
            "sha1": sha1,
            "sha256": str(r.get("sha256") or "").upper().replace(":", ""),
            "scanned_at": scanned_at,
            "job": str(job or ""),
        }
        k = (rec["host"], location, sha1 or rec["subject"])
        if k in seen:
            continue
        seen.add(k)
        out.append(rec)
    return sorted(out, key=lambda x: (x["days"] if x["days"] is not None else -10 ** 9, x["host"], x["location"]))


def cert_csv(records):
    """The records as CSV text (FIELDS as the header; the names joined with '; '; true/false; no value = empty)."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(FIELDS)
    for r in records or []:
        row = []
        for f in FIELDS:
            v = r.get(f)
            if f == "sans":
                v = "; ".join(v or [])
            elif isinstance(v, bool):
                v = "true" if v else "false"
            elif v is None:
                v = ""
            row.append(v)
        w.writerow(row)
    return buf.getvalue()


def cert_flat(records):
    """The records for the SQL Server step: the names as one text ('; '), no date = null."""
    out = []
    for r in records or []:
        r = dict(r)
        r["sans"] = "; ".join(r.get("sans") or [])
        r["not_after"] = r.get("not_after") or None
        r["scanned_at"] = str(r.get("scanned_at") or "").rstrip("Z") or None   # datetime2 takes it without the Z (UTC)
        out.append(r)
    return out


class FilterModule(object):
    def filters(self):
        return {"cert_records": cert_records, "cert_csv": cert_csv, "cert_flat": cert_flat}
