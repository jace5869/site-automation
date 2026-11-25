# -*- coding: utf-8 -*-
"""The site portal's readers, approvers and requesters from AD groups:
each group's members - nested groups too (LDAP_MATCHING_RULE_IN_CHAIN), never a disabled account - read over LDAP,
read-only, with the "LDAP Bind Account" login, in ONE shared read every few minutes and kept. A person's card CN
(LAST.FIRST.MI.1234567890) is matched to their AD account by its EDIPI: userPrincipalName = <EDIPI>@mil by default
(portal_ad_card_attribute, portal_ad_card_match). When AD cannot be read - or has not been yet - nobody is a member:
the portal falls back to the named lists only for group-based access. The connection is
the AD report's (site_ldap). Standard library and ldap3 (dnspython for the DCs of a domain, when it is here)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import fnmatch
import re
import threading
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


def edipi(cn):
    """The EDIPI at the end of a card's CN (LAST.FIRST.MI.1234567890) -> '1234567890', or '' when it has none."""
    m = re.search(r"(?:^|\.)(\d{10})$", str(cn or "").strip())
    return m.group(1) if m else ""


def card_key(cn, match="{edipi}@mil"):
    """What the card's AD account holds in the card attribute: match with {edipi} and {cn} filled in (lower case);
    '' when the CN has no EDIPI and the match needs one, or the match names neither (it would match anyone)."""
    e = edipi(cn)
    if ("{edipi}" not in match and "{cn}" not in match) or ("{edipi}" in match and not e):
        return ""
    if "{cn}" in match and not str(cn or "").strip():
        return ""                                         # 1.122.2: no CN, nothing to match (never the template's own text)
    return match.replace("{edipi}", e).replace("{cn}", str(cn or "").strip()).lower()


def card_pattern(cn, match="{edipi}@mil"):
    """The card's key as a compiled whole-value pattern: the template's own * and ? match anything, the person's EDIPI
    and CN only themselves (a * in a CN is a *). None when the card cannot match (card_key '')."""
    if not card_key(cn, match):
        return None
    ident = {"{edipi}": re.escape(edipi(cn)), "{cn}": re.escape(str(cn or "").strip().lower())}
    out = []
    for part in re.split(r"(\{edipi\}|\{cn\}|\*|\?)", match.lower()):
        out.append(ident[part] if part in ident else {"*": ".*", "?": "."}.get(part) or re.escape(part))
    return re.compile("".join(out) + r"\Z", re.S)        # used with fullmatch: the whole value, no trailing newline


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
    timeout, attribute. opener (the tests): a callable (opts) -> (conn, server, error) in place of the real connection."""

    def __init__(self, opts, opener=None):
        self.o = opts
        self.opener = opener

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
        """-> (conn, server, error): the first DC that takes the bind, LDAPS first, StartTLS on 389 when it is allowed."""
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
                    return None, "", "; ".join(why)       # the login is wrong: not tried on the next DC (the account would lock)
        return None, "", "; ".join(why)

    def scrub(self, text):
        """What a DC or a library said, without the password in it, should it ever echo it (any password of 2 or more)."""
        t = str(text or "")
        pw = str(self.o.get("password") or "")
        return t.replace(pw, "***") if len(pw) >= 2 else t

    def search(self, c, base, flt, attrs, scope="sub", missing_ok=False):
        """Every page; any result but success (and noSuchObject where missing_ok: a group DN that is not there) fails the
        whole read - never a part of the members taken for all of them."""
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
            ctl = ((res.get("controls") or {}).get("1.2.840.113556.1.4.319") or {}).get("value") or {}
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

    def members(self, c, base, dn, attr):
        """The enabled users in the group, nested groups too -> [[their attr values, lower case]]."""
        flt = "(&(objectCategory=person)(objectClass=user)(memberOf:%s:=%s)(!(userAccountControl:%s:=%d)))" % (
            IN_CHAIN, escape_filter_chars(dn), BIT_AND, DISABLED)
        out = []
        for e in self.search(c, base, flt, [attr, "userAccountControl"]):
            a = e.get("attributes") or {}
            uac = a.get("userAccountControl")
            uac = uac[0] if isinstance(uac, list) and uac else uac
            try:
                if uac in (None, "") or int(uac) & DISABLED:
                    continue                              # disabled - or not said: never a member, whatever the DC's filter did
            except (TypeError, ValueError):
                continue
            v = a.get(attr)
            vals = [str(x).lower() for x in (v if isinstance(v, list) else [v]) if x not in (None, "")]
            if vals:
                out.append(vals)
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
        attr = str(self.o.get("attribute") or "userPrincipalName")
        try:
            base = self.base(c)
            if not base:
                raise LookupError("no base DN (portal_ldap_base, or the domain's name)")
            seen = {}
            for role in ROLES:
                for name in want[role]:
                    if name not in seen:
                        dn, why = self.group_dn(c, base, name)
                        seen[name] = {"name": short_name(name), "dn": dn, "found": bool(dn), "why": why, "keys": self.members(c, base, dn, attr) if dn else []}
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
        self.reader = Reader(opts, opener)
        self.match = str(match or "{edipi}@mil")
        self.seconds = max(60, int(seconds or SECONDS))
        self.on_change = on_change
        self.lock = threading.Lock()
        self.last = None                                  # the last read (good or not)
        self.next_at = 0.0
        self.thread = None
        self.stop = threading.Event()

    def configured(self, role=None):
        return bool(self.want.get(role)) if role else any(self.want.values())

    def refresh(self):
        r = self.reader.read(self.want)
        if not r["ok"] and "invalidCredentials" in str(r.get("error")):
            r["error"] += " - not tried again until the portal is deployed again with the right login (the account would lock out)"
        with self.lock:
            before = self.last
            self.last = r
            self.next_at = r["at"] + (self.seconds if r["ok"] else 10 ** 9 if "invalidCredentials" in str(r.get("error")) else RETRY)
        if self.on_change and (before is None or before["ok"] != r["ok"] or (not r["ok"] and before.get("error") != r["error"])):
            self.on_change(r)
        return r

    def start(self):
        if not self.configured() or self.thread is not None:
            return

        def run():
            while not self.stop.is_set():
                try:
                    self.refresh()
                except Exception as e:                    # noqa: BLE001 - never kills the thread: said as not read
                    with self.lock:
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
        if not self.want.get(role):
            return False
        pat = card_pattern(cn, self.match)
        if pat is None:
            return False
        with self.lock:
            r = self.last
        if not self.fresh(r):
            return False                                  # fail closed: the named lists only
        for g in r["groups"].get(role) or []:
            for vals in g.get("keys") or []:
                if any(pat.fullmatch(v) for v in vals):
                    return True
        return False

    def card_found(self, cn, now=None):
        """1.130.0: does the card's person have an AD account (portal_ad_card_attribute / card_match)? -> True / False / None
        (not known). From the last read's member keys first; else one LDAP lookup, at most once per person per 5 minutes."""
        now = time.time() if now is None else now
        pat = card_pattern(cn, self.match)
        if pat is None:
            return False
        with self.lock:
            r = self.last
            cache = self.__dict__.setdefault("_cards", {})
            hit = cache.get(cn)
        if r and r.get("ok"):
            for gs in (r.get("groups") or {}).values():
                for g in gs:
                    if any(any(pat.fullmatch(v) for v in vals) for vals in g.get("keys") or []):
                        return True
        if not (r and r.get("ok")):
            return None                                   # AD not read: not known - and no lookup while it is down
        if hit and now - hit[0] < CARD_CHECK_S:
            return hit[1]
        found = None
        try:
            c, _server, _err = self.reader.open()
            if c is not None:
                try:
                    base = self.reader.base(c)
                    key = card_key(cn, self.match)
                    val = "*".join(escape_filter_chars(p) for p in key.split("*"))
                    attr = str(self.reader.o.get("attribute") or "userPrincipalName")
                    rows = self.reader.search(c, base, "(&(objectCategory=person)(objectClass=user)(%s=%s))" % (attr, val), [attr]) if base else []
                    found = False
                    for e in rows:
                        v = (e.get("attributes") or {}).get(attr)
                        if any(pat.fullmatch(str(x).lower()) for x in (v if isinstance(v, list) else [v]) if x not in (None, "")):
                            found = True
                finally:
                    try:
                        c.unbind()
                    except Exception:                     # noqa: BLE001
                        pass
        except Exception:                                 # noqa: BLE001 - not known
            found = None
        with self.lock:
            cache[cn] = (now, found)                      # "not known" too: at most one lookup per person per 5 minutes
            if len(cache) > 1000:
                for k in sorted(cache, key=lambda k: cache[k][0])[:500]:
                    cache.pop(k, None)
        return found

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
    """A role's people (1.120.0): the named list (portal_approvers / portal_requesters: card CNs) - a list as before, so
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
