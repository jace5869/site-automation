# -*- coding: utf-8 -*-
"""The site portal's readers, approvers and requesters from AD groups:
each group's members - nested groups too (LDAP_MATCHING_RULE_IN_CHAIN), never a disabled account - read over LDAP,
read-only, with the "LDAP Bind Account" login, in ONE shared read every few minutes and kept. A person's card CN
(LAST.FIRST.MI.1234567890) is matched against the account attributes in portal_ad_card_matches.
When AD cannot be read - or has not been yet - nobody is a member: the portal falls back to the named lists only for group-based access. The connection is
the AD report's (site_ldap). Standard library and ldap3 (dnspython for the DCs of a domain, when it is here)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import re
import threading
import uuid
import time

try:
    from ansible.module_utils import site_ldap as sl
except ImportError:                                      # the portal and the tests: plugins/module_utils on sys.path
    import site_ldap as sl

try:
    import ldap3
    from ldap3.utils.conv import escape_filter_chars
except ImportError:
    ldap3 = None

    def escape_filter_chars(s):
        return re.sub(r"[\\*()\x00]", lambda m: "\\%02x" % ord(m.group(0)), s)

IN_CHAIN = "1.2.840.113556.1.4.1941"               # LDAP_MATCHING_RULE_IN_CHAIN: a member of a member ... counts
BIT_AND = "1.2.840.113556.1.4.803"                 # LDAP_MATCHING_RULE_BIT_AND
DISABLED = 0x2                                     # userAccountControl ACCOUNTDISABLE
ROLES = ("approvers", "requesters", "readers", "wall_feedback", "wall_readers")   # 1.130.0: portal readers; the wall's Feedback button and list
SECONDS, RETRY = 300, 60                           # read every 5 minutes; 60 s after a failure
# 1.133.0: a card is matched only by exact forms of two attributes (the director's cut: altSecurityIdentities and any
# other attribute are gone). No * or ? anywhere; {digits6} (six ASCII digits) is the only variable part.
CARD_ATTRIBUTES = {"userprincipalname": "userPrincipalName", "samaccountname": "sAMAccountName"}
# 1.133.0 (director): a CLOSED list - exactly these five forms, a site picks a subset; nothing user-defined. Each form names
# the EDIPI it carries (group 1); values are ASCII only (any other character: no match), compared in ASCII lower case.
CARD_FORMS = {
    ("userPrincipalName", "{edipi}@mil"): re.compile(r"([0-9]{10})@mil", re.ASCII),
    ("userPrincipalName", "{edipi}{digits6}@mil"): re.compile(r"([0-9]{10})[0-9]{6}@mil", re.ASCII),
    ("sAMAccountName", "{edipi}.ctr"): re.compile(r"([0-9]{10})\.ctr", re.ASCII),
    ("sAMAccountName", "{edipi}.civ"): re.compile(r"([0-9]{10})\.civ", re.ASCII),
    ("sAMAccountName", "{edipi}.mil"): re.compile(r"([0-9]{10})\.mil", re.ASCII),
}
ALL_FORMS = [{"attribute": a, "match": m} for a, m in CARD_FORMS]      # every form: what an account's values name
DEFAULT_CARD_MATCHES = [
    {"attribute": "userPrincipalName", "match": "{edipi}@mil"},
    {"attribute": "sAMAccountName", "match": "{edipi}.ctr"},
    {"attribute": "sAMAccountName", "match": "{edipi}.civ"},
    {"attribute": "sAMAccountName", "match": "{edipi}.mil"},
    {"attribute": "userPrincipalName", "match": "{edipi}{digits6}@mil"},
]
WILD_AFTER = re.compile(r"[*?]")
WILD_WHY = "would match other accounts of the same person or other people - list the exact suffixes"
FORMS_WHY = ("a card is matched by these forms only: userPrincipalName {edipi}@mil or {edipi}{digits6}@mil, sAMAccountName "
             "{edipi}.ctr, {edipi}.civ or {edipi}.mil")


def _form(attribute, pattern):
    """(canonical attribute, canonical match) of one of the five forms, or None"""
    a = CARD_ATTRIBUTES.get(str(attribute or "").strip().lower())
    key = (a, str(pattern or "").strip().lower())
    return key if key in CARD_FORMS else None


def card_rule_problem(attribute, pattern):
    """'' when a card rule is one of the five forms, else why not (the deploy and the portal refuse it alike)"""
    if WILD_AFTER.search(str(pattern or "")):
        return "a * or ? in a card rule " + WILD_WHY
    if _form(attribute, pattern) is None:
        return "%s / %s: %s" % (str(attribute or "")[:40], str(pattern or "")[:40], FORMS_WHY)
    return ""


def card_matches(entries, attribute="userPrincipalName", match="{edipi}@mil"):
    """Resolve the deployed list or the settings file from an older deployment -> [{attribute, match}] of the five forms."""
    if attribute != "userPrincipalName" or match != "{edipi}@mil":
        if entries is not None:
            raise ValueError("portal_ad_card_matches conflicts with portal_ad_card_attribute or portal_ad_card_match")
        entries = [{"attribute": attribute, "match": match}]
    if entries is None:
        entries = DEFAULT_CARD_MATCHES
    if not isinstance(entries, list):
        raise ValueError("portal_ad_card_matches must be a list")
    result = []
    for i, entry in enumerate(entries, 1):
        if not isinstance(entry, dict) or not isinstance(entry.get("attribute"), str) or not entry["attribute"].strip():
            raise ValueError("portal_ad_card_matches entry %d needs an attribute name" % i)
        pattern = entry.get("match")
        if not isinstance(pattern, str) or ("{edipi}" not in pattern and "{cn}" not in pattern):
            raise ValueError("portal_ad_card_matches entry %d needs {edipi} or {cn} to identify the person" % i)
        why = card_rule_problem(entry["attribute"], pattern)
        if why:
            raise ValueError("portal_ad_card_matches entry %d: %s" % (i, why))
        a, m = _form(entry["attribute"], pattern)
        result.append({"attribute": a, "match": m})
    return result


def edipi(cn):
    """The EDIPI at the end of a card's CN (LAST.FIRST.MI.1234567890) -> '1234567890', or '' when it has none (ASCII digits only)."""
    m = re.search(r"(?:^|\.)([0-9]{10})$", str(cn or "").strip(), re.ASCII)
    return m.group(1) if m else ""


CARD_CN = re.compile(r"[A-Za-z][A-Za-z' -]*\.[A-Za-z][A-Za-z' -]*(?:\.[A-Za-z][A-Za-z' -]*)?\.[0-9]{10}", re.ASCII)


def card_cn(cn):
    """Codex 1.133.0 #9: a card's CN has the shape LAST.FIRST[.MIDDLE].EDIPI - anything else (a bare number, another
    shape) gets no AD lookup"""
    return bool(CARD_CN.fullmatch(str(cn or "")))


def value_edipi(rule, value):
    """the EDIPI a value carries under one card rule, or '' (not that form, or not ASCII)"""
    v = str(value if value is not None else "")
    rx = CARD_FORMS.get(_form(rule["attribute"], rule["match"]) or ("", ""))
    if rx is None or not v.isascii():
        return ""
    m = rx.fullmatch(v.lower())
    return m.group(1) if m else ""


def entry_edipis(rules, attrs):
    """every EDIPI the account's values carry under the site's rules (an account with two is refused for both cards)"""
    out = set()
    for r in rules:
        v = attrs.get(r["attribute"])
        for x in (v if isinstance(v, list) else [v]):
            e = value_edipi(r, x)
            if e:
                out.add(e)
    return out


def _values(v):
    """an attribute's values as a list, without the empty ones"""
    return [x for x in (v if isinstance(v, list) else [v]) if x not in (None, "", b"")]


class _CardForm(object):
    def __init__(self, rule, e):
        self.rule, self.e = rule, e

    def fullmatch(self, value):
        return value_edipi(self.rule, value) == self.e or None


def card_key(cn, match="{edipi}@mil"):
    """the value a card's account holds under one form (lower case), '' when the card has no EDIPI or the form is unknown"""
    e = edipi(cn)
    if not e or not any(m == str(match or "").strip().lower() for _a, m in CARD_FORMS):
        return ""
    return str(match).strip().lower().replace("{edipi}", e)


def card_pattern(cn, match="{edipi}@mil", attribute=None):
    """an object whose fullmatch(value) is true for exactly this card's value under that form (ASCII only); None when the
    card has no EDIPI or the form is not one of the five"""
    e = edipi(cn)
    forms = [k for k in CARD_FORMS if k[1] == str(match or "").strip().lower() and (attribute is None or k == _form(attribute, match))]
    if not e or not forms:
        return None
    return _CardForm({"attribute": forms[0][0], "match": forms[0][1]}, e)


def _guid(v):
    """objectGUID as compared: its GUID text form in lower-case hex, whatever form the library gave - the 16 bytes as AD
    stores them (the first three fields little-endian, Codex r4 new #5) or '{...}' text"""
    v = v[0] if isinstance(v, list) and v else v
    if isinstance(v, (bytes, bytearray)):
        return uuid.UUID(bytes_le=bytes(v)).hex if len(v) == 16 else ""
    t = str(v or "").strip().strip("{}").replace("-", "").lower()
    return t if re.fullmatch(r"[0-9a-f]{32}", t, re.ASCII) else ""


def is_dn(name):
    return bool(re.match(r"(?i)^\s*cn=.+,.*dc=", str(name or "")))


def base_of(domain):
    """corp.example.mil -> DC=corp,DC=example,DC=mil ('' for none)."""
    return ",".join("DC=" + p for p in str(domain or "").strip().strip(".").split(".") if p)


def account_name(name):
    """1.129.1: DOMAIN\\Group (as Windows writes it) -> Group: one domain (the portal reads one), so the NetBIOS part is dropped."""
    s = str(name or "").strip()
    return s.split("\\", 1)[1].strip() if "\\" in s and not is_dn(s) else s


def short_name(name):
    """A group as said on the page: its CN when it is a DN, its name without DOMAIN\\ when written so."""
    m = re.match(r"(?i)^\s*cn=((?:\\,|[^,])+)", str(name or ""))
    return m.group(1).replace("\\,", ",") if m else account_name(name)


class Reader(object):
    """One read of every group: read() -> {ok, error, at, server, groups: {role: [{name, dn, found, count, keys}]}}.
    opts: servers [{fqdn, address}], domain, base, user, password, ports {ldaps, ldap}, starttls, validate, ca_file,
    timeout, attribute or matches. opener (the tests): a callable (opts) -> (conn, server, error) in place of the real connection."""

    def __init__(self, opts, opener=None, match="{edipi}@mil"):
        self.o = opts
        self.opener = opener
        self.matches = card_matches(opts.get("matches"), opts.get("attribute", "userPrincipalName"), match)
        # 1.133.0 (Codex r4 #2): both attributes are always read, whatever subset of the forms the site enabled - an account
        # whose values name another EDIPI under ANY of the five forms is nobody's
        self.attributes = ["userPrincipalName", "sAMAccountName"]
        # Codex 1.133.0 #11, #12: one bind at a time; a refused login (seen in the raw answer, before any redaction) stops
        # every later bind of this process - the group reads' and the card lookups' alike
        self.bind_lock = threading.Lock()
        self.refused = False

    def servers(self):
        """The DCs to ask: [{fqdn, address}] - the deploy's (portal_ldap_server, the inventory's DCs: {name, address}), else
        the domain's from DNS."""
        ip = re.compile(r"^[0-9.:]+$")
        s = []
        for x in self.o.get("servers") or []:
            if not isinstance(x, dict) or not (x.get("fqdn") or x.get("name") or x.get("address")):
                continue
            addr = str(x.get("address") or "").strip()       # the address given, if any (ansible_host)
            fqdn = str(x.get("fqdn") or x.get("name") or addr).strip()
            if "." not in fqdn and "." in addr and not ip.match(addr):
                fqdn = addr                                   # 1.122.2: dc01 at dc01.corp.example.mil - the full name is the address's
            s.append({"fqdn": fqdn, "address": addr})
        s = [x for x in s if "://" not in x["fqdn"] + x["address"]]        # ldap://host would be a bind in clear: a host only
        dom = str(self.o.get("domain") or "").strip().strip(".")
        for x in s:                                       # dc01 + corp.example.mil: the name its certificate carries
            if dom and "." not in x["fqdn"] and not ip.match(x["fqdn"]):
                x["fqdn"] = "%s.%s" % (x["fqdn"], dom)
            x["address"] = x["address"] or x["fqdn"]      # 1.122.2: no address given - the full name is dialed, never bare dc01
        if s or not self.o.get("domain"):
            return s
        try:                                              # the domain's DCs from DNS (the AD report's way too)
            import dns.resolver
            ans = dns.resolver.resolve("_ldap._tcp.dc._msdcs." + self.o["domain"].strip("."), "SRV", lifetime=float(self.o.get("timeout") or 5) * 2)
            return [{"fqdn": str(a.target).rstrip("."), "address": str(a.target).rstrip(".")} for a in sorted(ans, key=lambda a: (a.priority, -a.weight))]
        except Exception:                                 # noqa: BLE001 - no DC from DNS: said by read()
            return []

    def open(self):
        """-> (conn, server, error): the first DC that takes the bind, LDAPS first, StartTLS on 389 when it is allowed. None
        once a bind was refused (invalidCredentials): held until the next deploy."""
        with self.bind_lock:
            if self.refused:
                return None, "", "the bind was refused (invalidCredentials) - held until the next deploy"
            c, server, err = self._open()
            if c is None and "invalidCredentials" in str(err):
                self.refused = True
            return c, server, err

    def _open(self):
        if self.opener:
            return self.opener(self.o)
        if not self.o.get("user") or not self.o.get("password"):
            return None, "", "no LDAP login (the \"LDAP Bind Account\" credential on the portal deploy's template, portal_ldap_vars)"
        servers = self.servers()
        if not servers:
            return None, "", "no domain controller to ask (portal_ldap_server, the inventory's DCs or ad_reader_domain)"
        why = []
        ports = self.o.get("ports") or {}
        for s in servers[:5]:
            fqdn = s.get("fqdn") or s.get("address")
            addr = s.get("address") or fqdn
            for how, port in (("ldaps", ports.get("ldaps") or 636),) + ((("starttls", ports.get("ldap") or 389),) if self.o.get("starttls") else ()):
                c, _ms, err = sl.connect(fqdn, addr, port, how, self.o["user"], self.o["password"], int(self.o.get("timeout") or 5),
                                         bool(self.o.get("validate", True)), self.o.get("ca_file") or None)
                if c is not None:
                    return c, "%s:%s" % (fqdn, port), ""
                why.append("%s:%s %s" % (fqdn, port, self.scrub(err)))
                if str(err).startswith("the bind was refused") and "invalidCredentials" in str(err):
                    self.refused = True                   # from the raw answer (a password equal to the marker is scrubbed)
                    return None, "", "; ".join(why) + " (invalidCredentials)"   # not tried on the next DC (the account would lock)
        return None, "", "; ".join(why)

    def scrub(self, text):
        """What a DC or a library said, without the password in it, should it ever echo it (any password of 2 or more)."""
        t = str(text or "")
        pw = str(self.o.get("password") or "")
        return t.replace(pw, "***") if len(pw) >= 2 else t

    def search(self, c, base, flt, attrs, scope="sub", missing_ok=False, strict=False):
        """Every page; any result but success (and noSuchObject where missing_ok: a group DN that is not there) fails the
        whole read - never a part of the members taken for all of them. strict (a card's lookup, Codex 1.133.0 #6): a page
        without the paging control fails too - only the last page's empty cookie says the search is complete."""
        sc = {"sub": ldap3.SUBTREE if ldap3 else "sub", "base": ldap3.BASE if ldap3 else "base"}[scope]
        out, cookie = [], None
        while True:
            kw = {} if scope == "base" else {"paged_size": 500, "paged_cookie": cookie}
            c.search(base, flt, sc, attributes=attrs, **kw)
            page = [e for e in (c.response or []) if e.get("type") == "searchResEntry"]
            res = c.result or {}
            code = res.get("result")
            if code == 32 and missing_ok:
                return []
            if code != 0:
                raise LookupError("%s %s" % (res.get("description") or "search failed (%s)" % code, res.get("message") or ""))
            out += page
            raw = (res.get("controls") or {}).get("1.2.840.113556.1.4.319")
            if strict and scope != "base" and not (isinstance(raw, dict) and isinstance(raw.get("value"), dict) and "cookie" in raw["value"]):
                raise LookupError("the paged search ended without its end marker - not complete")
            ctl = (raw or {}).get("value") or {} if isinstance(raw, dict) else {}
            cookie = ctl.get("cookie") if isinstance(ctl, dict) else None
            if scope == "base" or not cookie:
                return out

    def base(self, c):
        b = str(self.o.get("base") or "").strip() or base_of(self.o.get("domain"))
        if b:
            return b
        first = (self.servers() or [{}])[0].get("fqdn") or ""
        if "." in first and not re.match(r"^[0-9.:]+$", first):
            return base_of(first.split(".", 1)[1])        # the first DC's domain: the same base whichever DC answers
        r = self.search(c, "", "(objectClass=*)", ["defaultNamingContext"], "base")
        v = ((r[0].get("attributes") or {}).get("defaultNamingContext") if r else "") or ""
        return v[0] if isinstance(v, list) and v else str(v or "")

    def root(self, c):
        """1.133.0 (director C): the domain root - the DC's defaultNamingContext, else the domain's own base - never
        portal_ldap_base (an account elsewhere in the domain must still count); '' when not known (the lookup fails closed)"""
        try:
            r = self.search(c, "", "(objectClass=*)", ["defaultNamingContext"], "base")
            v = ((r[0].get("attributes") or {}).get("defaultNamingContext") if r else "") or ""
            v = v[0] if isinstance(v, list) and v else v
            v = v.decode("utf-8") if isinstance(v, (bytes, bytearray)) else v      # Neon 1330s #2: odd values -> the fallback
            v = v.strip() if isinstance(v, str) else ""
        except Exception:                                 # noqa: BLE001
            v = ""
        if v:
            return v
        # Codex 1.133.0 #8: never a base guessed from a DC's name (another domain's DC may have answered): the configured
        # domain only, else '' (the lookup fails closed)
        return base_of(str(self.o.get("domain") or "").strip())

    def group_dn(self, c, base, name):
        """-> (dn, why): the group's DN, or '' and why not (not found; two or more of that name: give its DN)."""
        if is_dn(name):
            r = self.search(c, name.strip(), "(objectClass=group)", ["distinguishedName"], "base", missing_ok=True)
            return (name.strip(), "") if r else ("", "not found in AD")
        n = escape_filter_chars(account_name(name))
        r = self.search(c, base, "(&(objectCategory=group)(|(cn=%s)(sAMAccountName=%s)))" % (n, n), ["distinguishedName"])
        dns = sorted(set(e.get("dn") or "" for e in r) - {""})
        if len(dns) > 1:
            return "", "%d groups have this name - give its DN" % len(dns)       # never one of them by chance
        return (dns[0], "") if dns else ("", "not found in AD")

    def members(self, c, base, dn):
        """The enabled users in the group, nested groups too with values kept under the attribute they came from."""
        flt = "(&(objectCategory=person)(objectClass=user)(memberOf:%s:=%s)(!(userAccountControl:%s:=%d)))" % (
            IN_CHAIN, escape_filter_chars(dn), BIT_AND, DISABLED)
        out = []
        attrs = list(dict.fromkeys(self.attributes + ["userAccountControl", "objectGUID"]))
        for e in self.search(c, base, flt, attrs):
            a = e.get("attributes") or {}
            uac = a.get("userAccountControl")
            uac = uac[0] if isinstance(uac, list) and uac else uac
            try:
                if uac in (None, "") or int(uac) & DISABLED:
                    continue                              # disabled - or not said: never a member, whatever the DC's filter did
            except (TypeError, ValueError):
                continue
            member = {"dn": str(e.get("dn") or ""), "guid": _guid(a.get("objectGUID"))}    # which account it is: DN and objectGUID
            for attr in self.attributes:
                v = a.get(attr)
                vals = [str(x) for x in (v if isinstance(v, list) else [v]) if x not in (None, "")]
                if vals:
                    member[attr] = vals
            out.append(member)              # Codex #21: every enabled member counts; one with no card value just matches no card
        return out

    def accounts(self, c, base, cn):
        """1.133.0: the candidate accounts of a card, from the domain root -> {(dn, guid): {"dn", "attr", "enabled", "full", "edipis", "guid"}}.
        The filter takes the site's forms; userPrincipalName AND sAMAccountName are read for every entry, whatever forms the
        site enabled (Codex r4 #2). edipis: every EDIPI its values name under ANY of the five forms (two: refused for both
        cards). attr: the site's form that matched this card, None when none did. A candidate is every entry whose values
        name this card under the site's forms, and every entry not fully read - an attribute the filter could have matched
        on, or its sAMAccountName (every AD account has one), missing: it may be this card's (Codex r4 #4). A fully read
        entry that does not name this card (the 16-digit filter's neighbour: 1234567890.adm@mil) is not one.
        enabled: True (userAccountControl read, ACCOUNTDISABLE not set), None (not known)."""
        e_ = edipi(cn)
        rules = [r for r in self.matches if _form(r["attribute"], r["match"])]
        if not rules or not base or not e_:
            return {}
        terms = []
        for r in rules:
            key = r["match"].replace("{edipi}", e_).replace("{digits6}", "*")
            terms.append("(%s=%s)" % (r["attribute"], "*".join(escape_filter_chars(p) for p in key.split("*"))))
        attrs = ["userPrincipalName", "sAMAccountName", "userAccountControl", "objectGUID"]
        flt = "(&(objectCategory=person)(objectClass=user)(!(userAccountControl:%s:=%d))(|%s))" % (BIT_AND, DISABLED, "".join(terms))
        out = {}
        for n, e in enumerate(self.search(c, base, flt, attrs, strict=True)):
            a = e.get("attributes") or {}
            uac = a.get("userAccountControl")
            uac = uac[0] if isinstance(uac, list) and uac else uac
            try:
                enabled = None if uac in (None, "") else not (int(uac) & DISABLED)
            except (TypeError, ValueError):
                enabled = None
            if enabled is False:
                continue                                  # disabled: not an account of the card (the DC's filter said so too)
            attr = next((r["attribute"] for r in rules if any(value_edipi(r, x) == e_ for x in _values(a.get(r["attribute"])))), None)
            # Codex 1.133.0 #1: fully read only with BOTH attributes returned (every AD account has a sAMAccountName); an
            # account not fully read is a candidate, and as the only one it grants nothing (its state is not known)
            full = all(_values(a.get(x)) for x in ("userPrincipalName", "sAMAccountName"))
            if attr is None and full:
                continue                                  # fully read and not this card's (the broad 16-digit filter's neighbour)
            dn = str(e.get("dn") or "") or "<no dn %d>" % n
            guid = _guid(a.get("objectGUID"))
            # Codex 1.133.0 #5: one entry per (DN, objectGUID) - the same DN seen with two GUIDs is two accounts (ambiguous)
            out[(dn.casefold(), guid or "<no guid %d>" % n)] = {"dn": dn, "attr": attr, "enabled": enabled, "full": full,
                                                                 "edipis": entry_edipis(ALL_FORMS, a), "guid": guid}
        return out

    def read(self, groups):
        """groups: {role: [name or DN]} -> the read (see the class)."""
        at = time.time()
        want = dict((r, [str(g).strip() for g in groups.get(r) or [] if str(g).strip()]) for r in ROLES)
        res = {"ok": False, "error": "", "at": at, "server": "", "groups": dict((r, []) for r in ROLES)}
        if not any(want.values()):
            res["ok"] = True
            return res
        if not self.opener and not (self.o.get("user") and self.o.get("password")):
            res["error"] = "no LDAP login (the \"LDAP Bind Account\" credential on the portal deploy's template, portal_ldap_vars)"
            return res
        if ldap3 is None and not self.opener:
            res["error"] = "the ldap3 Python library is not here"
            return res
        c, server, err = self.open()
        if c is None:
            res["error"] = err or "AD did not answer"
            return res
        res["server"] = server
        try:
            base = self.base(c)
            if not base:
                raise LookupError("no base DN (portal_ldap_base, or the domain's name)")
            seen = {}
            for role in ROLES:
                for name in want[role]:
                    if name not in seen:
                        dn, why = self.group_dn(c, base, name)
                        seen[name] = {"name": short_name(name), "dn": dn, "found": bool(dn), "why": why, "keys": self.members(c, base, dn) if dn else []}
                        seen[name]["count"] = len(seen[name]["keys"])
                    res["groups"][role].append(seen[name])
            res["ok"] = True
        except Exception as e:                            # noqa: BLE001 - whatever it was: not read, said
            res["error"] = "LDAP: " + sl.short_err(self.scrub(getattr(e, "message", "") or e))
            res["groups"] = dict((r, []) for r in ROLES)
        finally:
            try:
                c.unbind()
            except Exception:                             # noqa: BLE001
                pass
        return res


class Groups(object):
    """The kept read and its thread. has(role, cn): the card's person is a member of one of the role's groups in the
    last good read - False when there is none (not read yet, AD down: the named lists only). status(): for the page."""

    def __init__(self, groups, opts, match="{edipi}@mil", seconds=SECONDS, opener=None, on_change=None):
        self.want = dict((r, [str(g).strip() for g in (groups or {}).get(r) or [] if str(g).strip()]) for r in ROLES)
        self.reader = Reader(opts, opener, match)
        self.seconds = max(60, int(seconds or SECONDS))
        self.on_change = on_change
        self.lock = threading.Lock()
        self.last = None                                  # the last read (good or not)
        self.next_at = 0.0
        self.thread = None
        self.stop = threading.Event()

    def configured(self, role=None):
        return bool(self.want.get(role)) if role else any(self.want.values())

    def held(self):
        return bool(self.__dict__.get("_held") or self.reader.refused)

    def refresh(self):
        with self.lock:
            if self.held():
                return self.last                          # a refused login: no bind at all until the next deploy
        r = self.reader.read(self.want)
        refused = not r["ok"] and ("invalidCredentials" in str(r.get("error")) or self.reader.refused)
        if refused:
            r["error"] += " - not tried again until the portal is deployed again with the right login (the account would lock out)"
        with self.lock:
            before = self.last
            self._gen = self.__dict__.get("_gen", 0) + 1        # 1.133.0: every group read drops the card lookups
            self.__dict__.setdefault("_accts", {}).clear()
            if self.__dict__.get("_held"):
                # Codex r4 #7: a refused login (a card lookup's, while this read was under way) holds until the next
                # deploy - a read that was already bound never lifts it, nor sets a next read
                return self.last
            self.last = r
            if refused:
                self._held = True
            self.next_at = r["at"] + (self.seconds if r["ok"] else 10 ** 9 if self.__dict__.get("_held") else RETRY)
        if self.on_change and (before is None or before["ok"] != r["ok"] or (not r["ok"] and before.get("error") != r["error"])):
            self.on_change(r)
        return r

    def start(self):
        if not self.configured() or self.thread is not None:
            return

        def run():
            while not self.stop.is_set():
                if time.time() < self.next_at:
                    # Codex r3 #7: a hold set meanwhile (a card lookup's refused login) is honoured - never a bind before it
                    self.stop.wait(min(60.0, max(1.0, self.next_at - time.time())))
                    continue
                try:
                    self.refresh()
                except Exception as e:                    # noqa: BLE001 - never kills the thread: said as not read
                    with self.lock:
                        if self.__dict__.get("_held"):
                            continue                      # the hold stands (Codex r4 #7)
                        self.last = {"ok": False, "error": "LDAP: " + sl.short_err(self.reader.scrub(getattr(e, "message", "") or e)), "at": time.time(), "server": "",
                                     "groups": dict((r, []) for r in ROLES)}
                        self.next_at = time.time() + RETRY
                self.stop.wait(max(5.0, self.next_at - time.time()))
        self.thread = threading.Thread(target=run, name="ad-groups", daemon=True)
        self.thread.start()

    def fresh(self, r, now=None):
        """A good read still good: at most the interval and two retries old (a refresh that hangs keeps nobody in)."""
        age = (now or time.time()) - r["at"] if r else 0
        return bool(r and r.get("ok")) and -RETRY <= age <= self.seconds + 2 * RETRY    # the clock set back: no read either

    def has(self, role, cn):
        return next(self.group_matches(role, cn), None) is not None

    def group_matches(self, role, cn):
        """Yield each of the role's groups the card's one AD account is a member of (its name) and the attribute that matched."""
        if not self.want.get(role):
            return
        # 1.133.0 (Codex r2 #2): the card is ONE AD account first (every card rule over the whole directory); none, or
        # more than one (a separate .adm account, two 16-digit forms): no group counts. Then only that account's memberships.
        found = self.account(cn)
        if not found[0]:
            return
        dn, attr, guid = found
        # Codex 1.133.0 #4: the group read as it is AFTER the lookup (a read that landed meanwhile, or failed, decides)
        with self.lock:
            r = self.last
            held = self.held()
        if held or not self.fresh(r):
            return                                  # fail closed: the named lists only
        for g in r["groups"].get(role) or []:
            # the member entry must be that account - its DN (any case) AND its objectGUID (Codex r3 #5: a DN reused for a
            # new account never passes the old one's membership on) - and its own values must name this card's EDIPI only,
            # under any of the five forms (Codex r4 #2)
            if any(isinstance(m, dict) and str(m.get("dn") or "").casefold() == dn.casefold() and m.get("guid") and m.get("guid") == guid
                   and entry_edipis(ALL_FORMS, m) == {edipi(cn)} for m in g.get("keys") or []):
                yield g["name"], attr

    def account(self, cn, now=None):
        """-> (the card's one enabled AD account's DN, the attribute that matched, its objectGUID) or (None, why). Looked up
        from the domain root, at most once per person per CARD_CHECK_S (a failed lookup: RETRY). The cache is per CN and
        dropped at every group read (generation); a lookup stores its answer only when no newer one was stored meanwhile
        (sequence), and a request answers with the newest stored decision (Codex 1.133.0 #3) - an older "yes" never wins
        over a newer "no". A group read that lands during the lookup: no answer counts this time. The bind goes through
        the reader's guard: a refused login holds every lookup until the next deploy, as it holds the group reads."""
        now = time.monotonic() if now is None else now           # Neon r2 #3: the cache's clock never steps back
        if not card_cn(cn):
            return (None, "the card's name is not LAST.FIRST[.MIDDLE].EDIPI - no AD lookup")     # Codex 1.133.0 #9
        if not self.reader.matches:
            return (None, "no card form is set - no AD lookup")                                     # Neon 1330s #6
        with self.lock:
            cache = self.__dict__.setdefault("_accts", {})
            gen = self.__dict__.setdefault("_gen", 0)
            hit = cache.get(cn)
            if hit and hit[3] == gen and 0 <= now - hit[0] < hit[2] and not self.held():
                return hit[1]
            self._seq = self.__dict__.get("_seq", 0) + 1
            seq = self._seq
            held = self.held() or (self.last and "invalidCredentials" in str(self.last.get("error") or ""))
        if held:
            return (None, "the LDAP login was refused - held until the next deploy")
        res, ttl = (None, "not looked up"), RETRY
        try:
            c, _server, err = self.reader.open()
            if c is None:
                res = (None, "AD did not answer")
                if self.reader.refused or "invalidCredentials" in str(err):
                    with self.lock:                       # the same hold as a group read's (the account would lock)
                        self._held = True
                        self.last = dict(self.last or {}, ok=False, error=err + " - not tried again until the portal is deployed again "
                                         "with the right login (the account would lock out)", at=time.time())
                        self.next_at = time.time() + 10 ** 9
                        self._gen = self.__dict__.get("_gen", 0) + 1      # Neon 1330s #3: no cached answer outlives the refusal
                        cache.clear()
                        last = self.last
                    if self.on_change:
                        self.on_change(last)              # said in the portal's log, as a group read's refusal is
                    return (None, "the LDAP login was refused - held until the next deploy")
            else:
                try:
                    root = self.reader.root(c)
                    found = self.reader.accounts(c, root, cn) if root else None
                finally:
                    try:
                        c.unbind()
                    except Exception:                     # noqa: BLE001
                        pass
                if found is None:
                    res = (None, "the domain root is not known")
                else:
                    ttl = CARD_CHECK_S
                    if len(found) == 1:
                        x = next(iter(found.values()))
                        dn = x["dn"]
                        if x["enabled"] is not True or dn.startswith("<no dn") or not x["guid"] or not x["attr"] or not x["full"]:
                            res = (None, "the card's AD account state is not known")
                        elif x["edipis"] != {edipi(cn)}:
                            # Codex r3 #2: an account whose values name two EDIPIs is nobody's (refused for both cards)
                            res = (None, "the card's AD account also carries another EDIPI - none counts")
                        else:
                            res = (dn, x["attr"], x["guid"])
                    elif not found:
                        res = (None, "no AD account matches this card")
                    else:
                        res = (None, "more than one AD account matches this card (%d) - none counts" % len(found))
        except Exception:                                 # noqa: BLE001 - not known: no group counts
            res = (None, "the card's AD account could not be looked up")
        with self.lock:
            cur = cache.get(cn)
            if self.__dict__.get("_gen", 0) != gen:
                res = (None, "AD was read again during the lookup - not counted this time")
            elif cur is not None and cur[3] == gen and cur[4] > seq:
                res = cur[1]                              # a newer lookup decided meanwhile: its answer
            else:
                cache[cn] = (now, res, ttl, gen, seq)
            if len(cache) > 2000:
                for k in sorted(cache, key=lambda k: cache[k][0])[:1000]:
                    cache.pop(k, None)
        return res

    def card_found(self, cn, now=None):
        """1.130.0: does the card's person have an AD account (any rule of portal_ad_card_matches)? -> True / False / None
        (not known). 1.133.0: the same lookup as the access check (Groups.account) - never while AD is not read."""
        with self.lock:
            r = self.last
        if not (r and r.get("ok")):
            return None                                   # AD not read: not known - and no lookup while it is down
        if not card_cn(cn) or not [e for e in self.reader.matches if card_pattern(cn, e["match"]) is not None]:
            return False                                  # no card-shaped name (Codex 1.133.0 #9) or no rule: no AD account
        dn, why = self.account(cn, now)[:2]
        if dn or why.startswith("more than one"):
            return True
        return False if why.startswith("no AD account") else None

    def diag(self, cn):
        """1.130.0: for a refused person - is AD read (and the short reason when not), each configured group (found, how many
        members), and whether their card matched an AD account. Never names of members, never the bind account or a DC."""
        with self.lock:
            r = self.last
        ok = self.fresh(r)
        groups = []
        for role in ROLES:
            for name in self.want.get(role) or []:
                g = next((x for x in ((r or {}).get("groups") or {}).get(role) or [] if x.get("name") == short_name(name)), None) if ok else None
                groups.append({"role": role, "name": short_name(name), "found": None if g is None else bool(g.get("found")),
                               "members": None if g is None else int(g.get("count") or 0)})
        return {"read": ok, "why": "" if ok else short_reason((r or {}).get("error") or ("not read yet" if not r else "the last read is too old")),
                "groups": groups, "card": self.card_found(cn)}

    def status(self, role):
        """{configured, ok, read (a read was made), at, next, error, groups: [{name, count, found}]} for the role."""
        with self.lock:
            r, nxt = self.last, self.next_at
        ok = self.fresh(r)
        now = time.time()
        # 1.122.2: the next read only when there is one to come - none after a wrong password (held until the next deploy),
        # none when it is past (a read under way, or hung)
        nxt = nxt if nxt and now <= nxt <= now + self.seconds + 2 * RETRY else None
        return {"configured": bool(self.want.get(role)), "read": r is not None, "ok": ok, "at": (r or {}).get("at"),
                "next": nxt, "error": (r or {}).get("error") or ("" if not r else "" if ok else "the last read is too old") or ("" if r else "not read yet"),
                "groups": [{"name": g["name"], "count": g["count"], "found": g["found"], "why": g.get("why") or ""} for g in ((r or {}).get("groups") or {}).get(role) or []]
                if ok else [{"name": short_name(n), "count": None, "found": None, "why": ""} for n in self.want.get(role) or []]}


def short_reason(error):
    """an AD read's error -> a short reason a refused person may see (never a DC name, an account or a DN)"""
    e = str(error or "").lower()
    if not e:
        return ""
    if "invalidcredentials" in e or "bind" in e and ("refus" in e or "fail" in e):
        return "bind refused"
    if "certificate" in e or "ssl" in e or "tls" in e:
        return "LDAPS certificate"
    if "no ldap login" in e:
        return "no LDAP login on the portal"
    if "ldap3" in e and "library" in e:
        return "LDAP library missing"
    if "not read yet" in e:
        return "not read yet"
    if "too old" in e:
        return "the last read is too old"
    if "did not answer" in e or "no answer" in e or "timed out" in e or "timeout" in e or "unreachable" in e or "connect" in e:
        return "no DC answered"
    return "AD could not be read"


CARD_CHECK_S = 300                      # a card's account looked up at most once per person per 5 minutes (cached)


class People(list):
    """A role's people (1.120.0): the named list (portal_approvers / portal_runners: card CNs) - a list as before, so
    it is written and compared as one - and the members of the role's AD groups. `cn in people`: named, or a member in
    the last good read. True when anyone or any group is set: an empty named list with groups set is not "anyone"."""

    def __init__(self, named, role="", groups=None):
        list.__init__(self, named or [])
        self.role = role
        self.groups = groups

    def __contains__(self, cn):
        return list.__contains__(self, cn) or bool(self.groups is not None and self.groups.has(self.role, cn))

    def __bool__(self):
        return len(self) > 0 or bool(self.groups is not None and self.groups.configured(self.role))

    __nonzero__ = __bool__

    def named(self):
        return list(self)
