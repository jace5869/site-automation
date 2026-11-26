#!/usr/bin/env python3
"""1.120.0: the site portal's approvers and requesters from AD groups (plugins/module_utils/site_ad_groups.py, the portal's
use of it, the deploy's). A fake LDAP directory answers the reader's searches as a DC does - nested groups through
LDAP_MATCHING_RULE_IN_CHAIN, a disabled account, a DC that is down - so it runs anywhere.
    python3 tests/test_ad_groups.py"""
import json
import os
import re
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "plugins", "module_utils"), os.path.join(REPO, "plugins", "filter"),
                os.path.join(REPO, "roles", "site_portal_stack", "files")]
import site_ad_groups as G  # noqa: E402

BASE = "DC=corp,DC=example,DC=mil"


def dn(cn, ou="Groups"):
    return "CN=%s,OU=%s,%s" % (cn, ou, BASE)


# the directory: groups (their direct members: user or group DNs) and users (upn, uac, altSecurityIdentities)
def guid(d):
    """the fake directory's objectGUID of an account: fixed per DN (any case)"""
    import hashlib
    return hashlib.md5(str(d).lower().encode()).hexdigest()


USERS = {dn("Alex Approver", "Users"): {"sam": "aapprover", "upn": "3456789012@mil", "uac": 512, "alt": ["X509:<I>CN=DOD ID CA-70<S>CN=APPROVER.ALEX.B.3456789012"]},
         dn("Jo Lead", "Users"): {"sam": "jlead", "upn": "4567890123@mil", "uac": 512, "alt": []},
         dn("Old Lead", "Users"): {"sam": "olead", "upn": "5678901234@mil", "uac": 514, "alt": []},                    # 512 + 2: disabled
         dn("Deep Member", "Users"): {"sam": "dmember", "upn": "6789012345@mil", "uac": 66048, "alt": []},              # 512 + 65536 (no expiry): enabled
         dn("Sam Operator", "Users"): {"sam": "soperator", "upn": "2345678901@mil", "uac": 512, "alt": []}}
GROUPS = {dn("AAP-Approvers"): [dn("Alex Approver", "Users"), dn("Platform-Leads")],
          dn("Platform-Leads"): [dn("Jo Lead", "Users"), dn("Old Lead", "Users"), dn("Deep-Team")],
          dn("Deep-Team"): [dn("Deep Member", "Users"), dn("AAP-Approvers")],                        # a loop back: counted once
          dn("AAP-Operators"): [dn("Sam Operator", "Users")]}
SAM = {"AAP-Approvers": "aap_approvers"}


def sam_of(u):
    """every AD account has a sAMAccountName: the fixture's, else one made from the DN; "sam": None = not returned"""
    a = USERS[u]
    return a["sam"] if "sam" in a else "u" + guid(u)[:8]


def chain(group, seen=None):
    """The users in a group, nested groups too (what IN_CHAIN does on a DC)."""
    seen = set() if seen is None else seen
    if group in seen:
        return set()
    seen.add(group)
    out = set()
    for m in GROUPS.get(group, []):
        out |= {m} if m in USERS else chain(m, seen)
    return out


class FakeConn(object):
    """An ldap3 Connection's search, answered from the directory above; disabled_filter False: a DC that ignores the
    ACCOUNTDISABLE part of the filter (the reader checks userAccountControl itself too)."""

    def __init__(self, disabled_filter=True, fail_on=None):
        self.disabled_filter, self.fail_on = disabled_filter, fail_on
        self.response, self.result, self.searches, self.unbound = [], {}, [], False
        self.member_attributes = []

    def _set(self, entries):
        self.response = [dict(e, type="searchResEntry") for e in entries]
        # the paging control with its empty cookie: the search is complete (a card lookup checks it)
        self.result = {"result": 0, "description": "success", "controls": {"1.2.840.113556.1.4.319": {"value": {"size": 0, "cookie": b""}}}}
        return True

    def search(self, base, flt, scope, attributes=None, **kw):
        self.searches.append((base, flt))
        if self.fail_on and self.fail_on in flt:
            self.response, self.result = [], {"result": 1, "description": "operationsError", "message": "000020D6: SvcErr"}
            return False
        if base == "" and flt == "(objectClass=*)":
            return self._set([{"dn": "", "attributes": {"defaultNamingContext": BASE}}])
        m = re.search(r"memberOf:1\.2\.840\.113556\.1\.4\.1941:=([^)]+)\)", flt)
        if m:
            self.member_attributes.append(attributes)
            g = m.group(1)
            users = chain(g)
            out = []
            for u in sorted(users):
                if self.disabled_filter and "userAccountControl:1.2.840.113556.1.4.803:=2" in flt and USERS[u]["uac"] & 2:
                    continue
                a = {"userAccountControl": USERS[u]["uac"]}
                if "userPrincipalName" in (attributes or []):
                    a["userPrincipalName"] = USERS[u]["upn"]
                if "altSecurityIdentities" in (attributes or []):
                    a["altSecurityIdentities"] = USERS[u]["alt"]
                if "sAMAccountName" in (attributes or []) and sam_of(u):
                    a["sAMAccountName"] = sam_of(u)
                if "objectGUID" in (attributes or []):
                    a["objectGUID"] = USERS[u].get("guid", guid(u))
                out.append({"dn": u, "attributes": a})
            return self._set(out)
        if flt.startswith("(&(objectCategory=person)(objectClass=user)(!(userAccountControl") and "(|" in flt:
            # 1.133.0: a card's accounts over the whole directory - each (attribute=value) term as AD reads it (* any, any case)
            import fnmatch
            self.searches.append((base, flt))
            terms = re.findall(r"\((\w+)=([^()]*)\)", flt.split("(|", 1)[1])
            unesc = lambda v: re.sub(r"\\([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), v)    # noqa: E731
            out = []
            for u in sorted(USERS):
                a = USERS[u]
                if not u.lower().endswith(base.lower()) or (a["uac"] is not None and a["uac"] & 2):
                    continue                              # the search's base (a subtree), and the DC's disabled filter
                vals = {"userPrincipalName": [a["upn"]], "altSecurityIdentities": list(a.get("alt") or []),
                        "employeeID": [a.get("employeeID")], "sAMAccountName": [sam_of(u)]}
                if any(fnmatch.fnmatchcase(str(v).lower(), unesc(val).lower()) for attr, val in terms for v in vals.get(attr, []) if v):
                    out.append({"dn": u, "attributes": dict(dict({"userAccountControl": a["uac"]} if a["uac"] is not None else {},
                                                                  objectGUID=a.get("guid", guid(u))),
                                                            **{k: (v if k == "altSecurityIdentities" else v[0]) for k, v in vals.items()
                                                               if k in (attributes or []) and v and v[0] is not None})})
            return self._set(out)
        if flt == "(objectClass=group)":                  # a DN given: is it a group?
            return self._set([{"dn": base, "attributes": {}}] if base in GROUPS else [])
        m = re.search(r"\(cn=([^)]+)\)\(sAMAccountName=", flt)
        if m:
            name = m.group(1).lower()
            hits = [g for g in GROUPS if g.split(",")[0][3:].lower() == name or SAM.get(g.split(",")[0][3:], "").lower() == name]
            return self._set([{"dn": g, "attributes": {}} for g in hits])
        return self._set([])

    def unbind(self):
        self.unbound = True


def opener(conn=None, down=False):
    def o(_opts):
        if down:
            return None, "", "dc01.corp.example.mil:636 no answer"
        return conn or FakeConn(), "dc01.corp.example.mil:636", ""
    return o


WANT = {"approvers": ["AAP-Approvers"], "requesters": ["AAP-Operators"]}


class Card(unittest.TestCase):
    def test_edipi_and_the_key(self):
        self.assertEqual(G.edipi("APPROVER.ALEX.B.3456789012"), "3456789012")
        self.assertEqual(G.edipi("APPROVER.ALEX.3456789012"), "3456789012")            # no middle initial
        self.assertEqual(G.edipi("NOT.A.CARD"), "")
        self.assertEqual(G.edipi("X.Y.12345678901"), "")                               # 11 digits: not an EDIPI
        self.assertEqual(G.card_key("APPROVER.ALEX.B.3456789012"), "3456789012@mil")
        self.assertEqual(G.card_key("NOT.A.CARD"), "")                                 # never matches anything
        self.assertEqual(G.base_of("corp.example.mil"), BASE)
        self.assertEqual(G.short_name(dn("AAP-Approvers")), "AAP-Approvers")
        self.assertTrue(G.is_dn(dn("AAP-Approvers")))
        self.assertFalse(G.is_dn("AAP-Approvers"))


class Read(unittest.TestCase):
    def test_1_nested_groups_count_a_disabled_account_never(self):
        g = G.Groups(WANT, {"base": BASE}, opener=opener())
        r = g.refresh()
        self.assertTrue(r["ok"], r)
        st = g.status("approvers")
        self.assertEqual(st["groups"], [{"name": "AAP-Approvers", "count": 3, "found": True, "why": ""}])   # Alex, Jo, Deep (not Old: disabled)
        self.assertTrue(g.has("approvers", "APPROVER.ALEX.B.3456789012"))                         # direct
        self.assertTrue(g.has("approvers", "LEAD.JO.K.4567890123"))                               # through Platform-Leads
        self.assertTrue(g.has("approvers", "MEMBER.DEEP.Q.6789012345"))                           # two levels down
        self.assertFalse(g.has("approvers", "LEAD.OLD.P.5678901234"))                             # disabled
        self.assertFalse(g.has("approvers", "OPERATOR.SAM.J.2345678901"))                         # a requester, not an approver
        self.assertTrue(g.has("requesters", "OPERATOR.SAM.J.2345678901"))
        self.assertFalse(g.has("approvers", "NOT.A.CARD"))

    def test_2_a_dc_that_ignores_the_disabled_filter(self):
        g = G.Groups(WANT, {"base": BASE}, opener=opener(FakeConn(disabled_filter=False)))
        g.refresh()
        self.assertFalse(g.has("approvers", "LEAD.OLD.P.5678901234"))                             # userAccountControl checked here too
        self.assertEqual(g.status("approvers")["groups"][0]["count"], 3)

    def test_3_one_shared_read_the_filter_and_the_base(self):
        conn = FakeConn()
        g = G.Groups({"approvers": ["AAP-Approvers"], "requesters": ["aap_approvers"]}, {}, opener=opener(conn))   # same group twice; base from the DC
        g.refresh()
        member_reads = [f for _b, f in conn.searches if "memberOf:" in f]
        self.assertEqual(len(member_reads), 2)                                                     # two names, one read each (one connection)
        self.assertIn("(memberOf:1.2.840.113556.1.4.1941:=%s)" % dn("AAP-Approvers"), member_reads[0])
        self.assertIn("(!(userAccountControl:1.2.840.113556.1.4.803:=2))", member_reads[0])
        self.assertEqual(conn.searches[0], ("", "(objectClass=*)"))                                # defaultNamingContext
        self.assertTrue(conn.unbound)
        self.assertTrue(g.has("requesters", "LEAD.JO.K.4567890123"))                               # by its sAMAccountName

    def test_4_a_dn_a_name_not_found_and_a_name_that_needs_escaping(self):
        conn = FakeConn()
        g = G.Groups({"approvers": [dn("Platform-Leads"), "No-Such-Group", "a*)(cn=*"]}, {"base": BASE}, opener=opener(conn))
        g.refresh()
        st = g.status("approvers")
        self.assertEqual([(x["name"], x["count"], x["found"]) for x in st["groups"]],
                         [("Platform-Leads", 3, True), ("No-Such-Group", 0, False), ("a*)(cn=*", 0, False)])   # Jo, Deep and - round the loop - Alex
        self.assertIn("(cn=a\\2a\\29\\28cn=\\2a)", " ".join(f for _b, f in conn.searches))         # never a filter injection

    def test_4b_domain_backslash_group_is_its_name(self):
        """1.129.1: DOMAIN\\Group (as Windows writes it) is looked up as Group; a DN is kept as it is."""
        self.assertEqual(G.account_name("CORP\\AAP-Approvers"), "AAP-Approvers")
        self.assertEqual(G.account_name(" AAP-Approvers "), "AAP-Approvers")
        self.assertEqual(G.short_name("CORP\\AAP-Approvers"), "AAP-Approvers")
        self.assertEqual(G.account_name(dn("AAP-Approvers")), dn("AAP-Approvers").strip())
        conn = FakeConn()
        g = G.Groups({"approvers": ["CORP\\AAP-Approvers"]}, {"base": BASE}, opener=opener(conn))
        g.refresh()
        st = g.status("approvers")
        self.assertEqual([(x["name"], x["found"]) for x in st["groups"]], [("AAP-Approvers", True)])
        self.assertTrue(g.has("approvers", "APPROVER.ALEX.B.3456789012"))
        self.assertNotIn("CORP", " ".join(f for _b, f in conn.searches))                          # the domain part is never searched

    def test_5_ad_down_fails_closed_and_comes_back(self):
        said = []
        g = G.Groups(WANT, {"base": BASE}, opener=opener(down=True), on_change=said.append)
        self.assertFalse(g.has("approvers", "APPROVER.ALEX.B.3456789012"))                        # not read yet: nobody
        r = g.refresh()
        self.assertFalse(r["ok"])
        self.assertIn("no answer", g.status("approvers")["error"])
        self.assertFalse(g.has("approvers", "APPROVER.ALEX.B.3456789012"))
        self.assertGreater(g.next_at, r["at"])                                                     # again in a minute
        self.assertLessEqual(g.next_at - r["at"], G.RETRY)
        g.reader.opener = opener()
        g.refresh()
        self.assertTrue(g.has("approvers", "APPROVER.ALEX.B.3456789012"))
        self.assertEqual([x["ok"] for x in said], [False, True])                                  # said when it changed
        g.reader.opener = opener(down=True)
        g.refresh()
        self.assertFalse(g.has("approvers", "APPROVER.ALEX.B.3456789012"))                        # down again: the last good read is NOT kept
        conn = FakeConn(fail_on="memberOf:")                                                       # a search that fails half-way
        g.reader.opener = opener(conn)
        r = g.refresh()
        self.assertFalse(r["ok"])
        self.assertTrue(r["error"].startswith("LDAP: operationsError"), r["error"])
        self.assertFalse(g.has("approvers", "APPROVER.ALEX.B.3456789012"))

    def test_6_no_login_is_said(self):
        r = G.Reader({"servers": [{"name": "dc01.corp.example.mil", "address": ""}]}).read(WANT)
        self.assertFalse(r["ok"])
        self.assertIn("no LDAP login", r["error"])
        self.assertEqual(G.Reader({"servers": [{"name": "dc01.corp.example.mil", "address": ""}]}).servers(),
                         [{"fqdn": "dc01.corp.example.mil", "address": "dc01.corp.example.mil"}])   # the inventory's DC, its name as address

class NeonR132(unittest.TestCase):
    """1.133.0 (Neon #2, #3): a card matches only its own account - the subject's whole CN, exact UPN suffixes"""

    def test_3_exact_forms(self):
        rules = G.card_matches(None)
        cn = "DOE.JANE.Q.1234567890"
        pats = [(r["attribute"], G.card_pattern(cn, r["match"])) for r in rules]
        for attr, value, ok in (("userPrincipalName", "1234567890@mil", True), ("userPrincipalName", "1234567890197005@MIL", True),
                                ("sAMAccountName", "1234567890.CTR", True), ("sAMAccountName", "1234567890.civ", True),
                                ("sAMAccountName", "1234567890.%s" % "mil", True), ("userPrincipalName", "1234567890.ctr@mil", False),
                                ("userPrincipalName", "12345678901@mil", False), ("userPrincipalName", "1234567890.adm@mil", False),
                                ("sAMAccountName", "1234567890.adm", False), ("sAMAccountName", "1234567890.ctr.adm", False),
                                ("userPrincipalName", "91234567890@mil", False), ("sAMAccountName", "1234567890@mil", False)):
            self.assertEqual(any(a == attr and p.fullmatch(value) for a, p in pats), ok, (attr, value))
        for bad in ("{edipi}*@mil", "{edipi}?@mil", "{cn}*", "{cn}?x", "*{edipi}@mil", "?{edipi}@mil", "*{cn}"):
            with self.assertRaisesRegex(ValueError, "list the exact suffixes"):
                G.card_matches([{"attribute": "userPrincipalName", "match": bad}])
            self.assertIsNone(G.card_pattern(cn, bad))
        for attr in ("altSecurityIdentities", "employeeID", "employee ID", "mail"):
            with self.assertRaisesRegex(ValueError, "these forms only"):
                G.card_matches([{"attribute": attr, "match": "{edipi}"}])
        self.assertEqual(G.card_matches([{"attribute": "samaccountname", "match": "{edipi}.ctr"}])[0]["attribute"], "sAMAccountName")

class Piv16(unittest.TestCase):
    """1.133.0 (field): the 16-digit PIV form - {edipi}{digits6}@mil matches exactly six ASCII digits after the EDIPI"""

    def test_sixteen_digits_only(self):
        p = G.card_pattern("DOE.JANE.Q.1234567890", "{edipi}{digits6}@mil")
        self.assertTrue(p.fullmatch("1234567890197005@mil"))
        self.assertTrue(p.fullmatch("1234567890000001@MIL"))
        for bad in ("1234567890.admin@mil", "123456789019700@mil", "12345678901970051@mil", "1234567890abcdef@mil",
                    "9234567890197005@mil", "1234567890@mil", "1234567890\u0661\u0662\u0663\u0664\u0665\u0666@mil",
                    "x1234567890197005@mil"):
            self.assertFalse(p.fullmatch(bad), bad)
        self.assertEqual(G.card_matches([{"attribute": "userPrincipalName", "match": "{edipi}{digits6}@mil"}])[0]["match"],
                         "{edipi}{digits6}@mil")
        for loose in ("{edipi}{digits6}*@mil", "{edipi}*{digits6}@mil"):
            with self.assertRaisesRegex(ValueError, "list the exact suffixes"):
                G.card_matches([{"attribute": "userPrincipalName", "match": loose}])


class FieldRules1133(unittest.TestCase):
    """1.133.0: the field's card rules together - sAMAccountName {edipi}.ctr and the 16-digit UPN - on a group read"""

    def test_members_by_samaccountname_and_piv16(self):
        import time as _t
        rules = [{"attribute": "userPrincipalName", "match": "{edipi}@mil"}, {"attribute": "sAMAccountName", "match": "{edipi}.ctr"},
                 {"attribute": "userPrincipalName", "match": "{edipi}{digits6}@mil"}]
        extra = {dn("Doe Jane", "Users"): {"upn": "jane@example.invalid", "sam": "1111111111.CTR", "uac": 512, "alt": []},
                 dn("Roe Rich", "Users"): {"upn": "2222222222197005@mil", "uac": 512, "alt": []},
                 dn("Poe Edgar", "Users"): {"upn": "3333333333.admin@mil", "sam": "3333333333.ctr.adm", "uac": 512, "alt": []}}
        g = G.Groups({"requesters": ["Ops"]}, {"base": BASE, "matches": rules}, opener=opener())
        g.last = {"ok": True, "at": _t.time(), "error": "", "groups": {"requesters": [{"setting": "Ops", "name": "Ops", "found": True, "count": 3, "keys": [
            {"dn": d, "guid": guid(d), "sAMAccountName": [u["sam"]] if u.get("sam") else [], "userPrincipalName": [u["upn"]]} for d, u in extra.items()]}]}}
        with patch.dict(USERS, extra):
            self.assertTrue(g.has("requesters", "DOE.JANE.Q.1111111111"))         # sAMAccountName, any case
            self.assertTrue(g.has("requesters", "ROE.RICH.A.2222222222"))         # the 16-digit UPN
            self.assertFalse(g.has("requesters", "POE.EDGAR.A.3333333333"))       # .ctr.adm and .admin: never
            self.assertTrue(g.card_found("DOE.JANE.Q.1111111111"))


class CodexR2(unittest.TestCase):
    """1.133.0: Codex's second review of the Admin release - card matching (each fails before its fix)"""

    def test_02_a_card_with_two_accounts_gets_no_group(self):
        import time as _t
        reg, adm = dn("Doe Regular", "Users"), dn("Doe Contractor", "Users")
        extra = {reg: {"upn": "1234567890@mil", "uac": 512, "alt": []},
                 adm: {"upn": "doe.adm@example.invalid", "sam": "1234567890.ctr", "uac": 512, "alt": []}}
        g = G.Groups({"approvers": ["Portal-Admins"]}, {"base": BASE}, opener=opener())
        g.last = {"ok": True, "at": _t.time(), "error": "", "groups": {"approvers": [{"name": "Portal-Admins", "found": True,
                  "count": 1, "keys": [{"dn": adm, "guid": guid(adm), "sAMAccountName": ["1234567890.ctr"], "userPrincipalName": [extra[adm]["upn"]]}]}]}}
        with patch.dict(USERS, extra):
            self.assertFalse(g.has("approvers", "DOE.JANE.Q.1234567890"))
            self.assertIn("more than one AD account", g.account("DOE.JANE.Q.1234567890")[1])
        g2 = G.Groups({"approvers": ["Portal-Admins"]}, {"base": BASE}, opener=opener())
        g2.last = g.last
        with patch.dict(USERS, {adm: extra[adm]}):                     # only the one account: its membership counts
            self.assertTrue(g2.has("approvers", "DOE.JANE.Q.1234567890"))
        rules = [{"attribute": "userPrincipalName", "match": "{edipi}{digits6}@mil"}]
        g3 = G.Groups({"approvers": ["Portal-Admins"]}, {"base": BASE, "matches": rules}, opener=opener())
        a, b = dn("Piv One", "Users"), dn("Piv Two", "Users")
        g3.last = {"ok": True, "at": _t.time(), "error": "", "groups": {"approvers": [{"name": "Portal-Admins", "found": True,
                   "count": 1, "keys": [{"dn": a, "guid": guid(a), "userPrincipalName": ["1234567890197005@mil"]}]}]}}
        with patch.dict(USERS, {a: {"upn": "1234567890197005@mil", "uac": 512, "alt": []}, b: {"upn": "1234567890000001@mil", "uac": 512, "alt": []}}):
            self.assertFalse(g3.has("approvers", "DOE.JANE.Q.1234567890"))     # two 16-digit forms of one EDIPI: none counts

    def test_03_04_no_wildcard_anywhere(self):
        for bad in ("{edipi}.*@mil", "svc?.{edipi}@mil", "*.{edipi}@mil", "{edipi}@mil*"):
            with self.assertRaisesRegex(ValueError, "list the exact suffixes"):
                G.card_matches([{"attribute": "userPrincipalName", "match": bad}])
            self.assertIsNone(G.card_pattern("DOE.JANE.Q.1234567890", bad))

    def test_16_odd_suggestions_never_cost_the_membership_read(self):
        class Odd(FakeConn):
            def search(self, base, flt, scope, attributes=None, **kw):
                if flt == "(objectCategory=group)":
                    return self._set([{"dn": 17, "attributes": {}}, {"dn": "CN=Ok,DC=x", "attributes": {}}])
                return FakeConn.search(self, base, flt, scope, attributes, **kw)
        r = G.Reader({"base": BASE, "suggest_groups": True}, opener(Odd()))
        res = r.read(WANT)
        self.assertTrue(res["ok"], res.get("error"))
        self.assertTrue(res["groups"]["approvers"][0]["found"])


class DirectorC(unittest.TestCase):
    """1.133.0 (director C): the card lookup - domain root, enabled only when known, one DN, a guarded and ordered cache"""

    def group(self, member_dn, attrs):
        import time as _t
        g = G.Groups({"approvers": ["Portal-Admins"]}, {"base": "OU=Staff," + BASE}, opener=opener())
        g.last = {"ok": True, "at": _t.time(), "error": "", "groups": {"approvers": [{"name": "Portal-Admins",
                  "found": True, "count": 1, "keys": [dict({"dn": member_dn, "guid": guid(member_dn)}, **attrs)]}]}}
        return g

    def test_from_the_domain_root_not_the_configured_base(self):
        staff, admins = dn("Doe", "Staff"), dn("Doe Adm", "Admins")
        g = self.group(staff, {"userPrincipalName": ["1234567890@mil"]})
        with patch.dict(USERS, {staff: {"upn": "1234567890@mil", "uac": 512, "alt": []},
                                admins: {"upn": "x@example.invalid", "sam": "1234567890.ctr", "uac": 512, "alt": []}}):
            self.assertFalse(g.has("approvers", "DOE.JANE.Q.1234567890"))     # the second account is outside OU=Staff: still counted

    def test_an_account_whose_state_is_not_known_counts_and_grants_nothing(self):
        a, b = dn("Doe", "Staff"), dn("Doe Two", "Staff")
        g = self.group(a, {"userPrincipalName": ["1234567890@mil"]})
        with patch.dict(USERS, {a: {"upn": "1234567890@mil", "uac": 512, "alt": []}, b: {"upn": "y@example.invalid", "sam": "1234567890.civ", "uac": None, "alt": []}}):
            self.assertFalse(g.has("approvers", "DOE.JANE.Q.1234567890"))
        g2 = self.group(a, {"userPrincipalName": ["1234567890@mil"]})
        with patch.dict(USERS, {a: {"upn": "1234567890@mil", "uac": None, "alt": []}}):
            self.assertFalse(g2.has("approvers", "DOE.JANE.Q.1234567890"))
            self.assertIn("state is not known", g2.account("DOE.JANE.Q.1234567890")[1])

    def test_a_reused_dn_never_passes_on_a_membership(self):
        d = dn("Duty", "Staff")
        g = self.group(d, {"userPrincipalName": ["1111111111@mil"]})                  # Alice was at that DN at the group read
        with patch.dict(USERS, {d: {"upn": "2222222222@mil", "uac": 512, "alt": []}}):  # now Bob is
            self.assertFalse(g.has("approvers", "BOB.B.B.2222222222"))

    def test_an_older_yes_never_overwrites_a_newer_no_and_a_group_read_drops_the_cache(self):
        d = dn("Doe", "Staff")
        g = self.group(d, {"userPrincipalName": ["1234567890@mil"]})
        cn = "DOE.JANE.Q.1234567890"
        real = g.reader.accounts
        calls = []

        def slow_then_fast(c, base, card):
            calls.append(1)
            if len(calls) == 1:                                   # the first lookup: a yes, but it finishes last
                with patch.dict(USERS, {d: {"upn": "1234567890@mil", "uac": 512, "alt": []}}):
                    res = real(c, base, card)
                g.reader.accounts = real
                with patch.dict(USERS, {d: {"upn": "1234567890@mil", "uac": 512, "alt": []},
                                        dn("Dup", "Staff"): {"upn": "z@example.invalid", "sam": "1234567890.%s" % "mil", "uac": 512, "alt": []}}):
                    self.assertIsNone(g.account(cn)[0])           # meanwhile a newer lookup: two accounts - no
                return res
            return real(c, base, card)
        g.reader.accounts = slow_then_fast
        g.account(cn)
        self.assertIsNone(g.account(cn)[0], "the older yes overwrote the newer no")
        g.reader.opener = opener()
        g.refresh()                                               # a group read: the cache is dropped
        self.assertEqual(g.__dict__.get("_accts"), {})

    def test_dn_case_and_a_cache_that_never_steps_back(self):
        d = dn("Doe", "Staff")
        g = self.group(d.upper(), {"userPrincipalName": ["1234567890@mil"]})        # Neon #2: the group read spells the DN in capitals
        with patch.dict(USERS, {d: {"upn": "1234567890@mil", "uac": 512, "alt": []}}):
            self.assertTrue(g.has("approvers", "DOE.JANE.Q.1234567890"))
            self.assertIsNotNone(g.account("DOE.JANE.Q.1234567890", now=10 ** 6)[0])
        with patch.dict(USERS, {d: {"upn": "1234567890@mil", "uac": 512, "alt": []},
                                dn("Dup", "Admins"): {"upn": "q@example.invalid", "sam": "1234567890.ctr", "uac": 512, "alt": []}}):
            # Neon #3: an answer stored "in the future" (a clock that stepped back) is never used - looked up again: two, no
            self.assertIsNone(g.account("DOE.JANE.Q.1234567890", now=10 ** 6 - 1000)[0])

    def test_a_refused_login_holds_the_lookups(self):
        d = dn("Doe", "Staff")
        g = self.group(d, {"userPrincipalName": ["1234567890@mil"]})
        tries = []

        def refused(_o):
            tries.append(1)
            return None, "", "dc01.corp.example.mil:636 the bind was refused (invalidCredentials)"
        g.reader.opener = refused
        self.assertIsNone(g.account("DOE.JANE.Q.1234567890")[0])
        self.assertIsNone(g.account("ROE.RICH.A.2222222222")[0])
        self.assertEqual(len(tries), 1)                             # the second card never binds with the refused login
        self.assertFalse(g.fresh(g.last))


class CodexR3Cards(DirectorC):
    """1.133.0: Codex's final review - the closed card forms (each fails before its fix)"""

    def test_01_only_the_five_forms(self):
        for attr, match in (("sAMAccountName", "{edipi}.adm"), ("userPrincipalName", "{edipi}.ctr@mil"), ("sAMAccountName", "{edipi}"),
                            ("userPrincipalName", "{cn}@mil")):
            with self.assertRaisesRegex(ValueError, "these forms only"):
                G.card_matches([{"attribute": attr, "match": match}])
        self.assertEqual(len(G.card_matches(None)), 5)

    def test_02_an_account_naming_two_edipis_is_nobodys(self):
        d = dn("Shared", "Staff")
        attrs = {"userPrincipalName": ["1111111111@mil"], "sAMAccountName": ["2222222222.civ"]}
        for card in ("ONE.A.A.1111111111", "TWO.B.B.2222222222"):
            g = self.group(d, attrs)
            with patch.dict(USERS, {d: {"upn": "1111111111@mil", "sam": "2222222222.civ", "uac": 512, "alt": []}}):
                self.assertFalse(g.has("approvers", card), card)

    def test_03_ascii_only(self):
        p = G.card_pattern("DOE.JANE.Q.1234567890", "{edipi}.mil")
        self.assertFalse(p.fullmatch("1234567890.m\u0131l"))                 # dotless i
        self.assertFalse(p.fullmatch("1234567890.%s\u200b" % "MIL"))
        self.assertTrue(p.fullmatch("1234567890.%s" % "MIL"))
        self.assertFalse(G.card_pattern("X.1234567890", "{edipi}{digits6}@mil").fullmatch("1234567890\u0661\u0662\u0663\u0664\u0665\u0666@mil"))
        d = dn("Doe", "Staff")
        g = self.group(d, {"userPrincipalName": ["1234567890X@mil"], "sAMAccountName": ["1234567890.m\u0131l"]})
        with patch.dict(USERS, {d: {"upn": "1234567890X@mil", "sam": "1234567890.m\u0131l", "uac": 512, "alt": []}}):
            self.assertFalse(g.has("approvers", "DOE.JANE.Q.1234567890"))

    def test_04_a_candidate_without_its_value_makes_the_card_ambiguous(self):
        d, d2 = dn("Doe", "Staff"), dn("Doe Two", "Staff")

        class Bare(FakeConn):                         # the DC returns a second entry without the value it matched on
            def search(self, base, flt, scope, attributes=None, **kw):
                ok = FakeConn.search(self, base, flt, scope, attributes, **kw)
                if flt.startswith("(&(objectCategory=person)(objectClass=user)(!(userAccountControl") and "(|" in flt:
                    self.response.append({"type": "searchResEntry", "dn": d2, "attributes": {"userAccountControl": 512, "objectGUID": guid(d2)}})
                return ok
        g = self.group(d, {"userPrincipalName": ["1234567890@mil"]})
        g.reader.opener = opener(Bare())
        with patch.dict(USERS, {d: {"upn": "1234567890@mil", "uac": 512, "alt": []}}):
            self.assertFalse(g.has("approvers", "DOE.JANE.Q.1234567890"))
            self.assertIn("more than one", g.account("DOE.JANE.Q.1234567890")[1])

    def test_05_a_new_account_at_an_old_dn_gets_nothing(self):
        d = dn("Duty", "Staff")
        g = self.group(d, {"userPrincipalName": ["1234567890@mil"]})                    # the old account at the group read
        with patch.dict(USERS, {d: {"upn": "x@example.invalid", "sam": "1234567890.civ", "uac": 512, "alt": [], "guid": "f" * 32}}):
            self.assertFalse(g.has("approvers", "DOE.JANE.Q.1234567890"))                 # same DN, another objectGUID


for _n in [n for n in vars(DirectorC) if n.startswith("test_")]:
    setattr(CodexR3Cards, _n, None)


class CodexR4(DirectorC):
    """1.133.0: Codex review round 4 - both attributes always read, a partly read candidate, the bind hold (each fails before its fix)"""

    def rules_group(self, member_dn, attrs, rules):
        g = self.group(member_dn, attrs)
        g2 = G.Groups({"approvers": ["Portal-Admins"]}, {"base": BASE, "matches": rules}, opener=opener())
        g2.last = g.last
        return g2

    def test_02_both_attributes_whatever_forms_the_site_enabled(self):
        d = dn("Shared", "Staff")
        attrs = {"userPrincipalName": ["1111111111@mil"], "sAMAccountName": ["2222222222.civ"]}
        g = self.rules_group(d, attrs, [{"attribute": "userPrincipalName", "match": "{edipi}@mil"}])     # only the plain UPN form
        conn = FakeConn()
        g.reader.opener = opener(conn)
        with patch.dict(USERS, {d: {"upn": "1111111111@mil", "sam": "2222222222.civ", "uac": 512, "alt": []}}):
            self.assertFalse(g.has("approvers", "ONE.A.A.1111111111"))
            self.assertIn("another EDIPI", g.account("ONE.A.A.1111111111")[1])
        self.assertEqual(g.reader.attributes, ["userPrincipalName", "sAMAccountName"])       # the group read takes both too
        # the group read's own entry: its values name another EDIPI under a form the site did not enable - no group counts
        d2 = dn("Doe", "Staff")
        g3 = self.rules_group(d2, {"userPrincipalName": ["1234567890@mil"], "sAMAccountName": ["2222222222.ctr"]},
                              [{"attribute": "userPrincipalName", "match": "{edipi}@mil"}])
        with patch.dict(USERS, {d2: {"upn": "1234567890@mil", "sam": "jdoe", "uac": 512, "alt": []}}):
            self.assertFalse(g3.has("approvers", "DOE.JANE.Q.1234567890"))

    def test_04_a_partly_read_candidate_counts(self):
        d, d2 = dn("Doe", "Staff"), dn("Doe Two", "Staff")

        class Partly(FakeConn):                       # the second entry matched on its sAMAccountName, which is not returned
            def search(self, base, flt, scope, attributes=None, **kw):
                ok = FakeConn.search(self, base, flt, scope, attributes, **kw)
                if flt.startswith("(&(objectCategory=person)(objectClass=user)(!(userAccountControl") and "(|" in flt:
                    self.response.append({"type": "searchResEntry", "dn": d2, "attributes": {
                        "userAccountControl": 512, "objectGUID": guid(d2), "userPrincipalName": "2222222222@mil"}})
                return ok
        rules = [{"attribute": "userPrincipalName", "match": "{edipi}@mil"}, {"attribute": "sAMAccountName", "match": "{edipi}.ctr"}]
        g = self.rules_group(d, {"userPrincipalName": ["1111111111@mil"], "sAMAccountName": ["jdoe"]}, rules)
        g.reader.opener = opener(Partly())
        with patch.dict(USERS, {d: {"upn": "1111111111@mil", "sam": "jdoe", "uac": 512, "alt": []}}):
            self.assertFalse(g.has("approvers", "DOE.JANE.Q.1111111111"))
            self.assertIn("more than one", g.account("DOE.JANE.Q.1111111111")[1])

    def test_new_04_a_fully_read_neighbour_is_not_a_candidate(self):
        d, adm = dn("Doe", "Staff"), dn("Doe Adm", "Staff")
        g = self.group(d, {"userPrincipalName": ["1234567890@mil"], "sAMAccountName": ["jdoe"]})
        with patch.dict(USERS, {d: {"upn": "1234567890@mil", "sam": "jdoe", "uac": 512, "alt": []},
                                adm: {"upn": "1234567890.adm@mil", "sam": "jdoe.adm", "uac": 512, "alt": []}}):
            self.assertTrue(g.has("approvers", "DOE.JANE.Q.1234567890"))          # the 16-digit filter's neighbour: not the card's

    def test_07_a_read_already_bound_never_lifts_the_hold(self):
        d = dn("Doe", "Staff")
        g = self.group(d, {"userPrincipalName": ["1234567890@mil"]})
        real_read = g.reader.read

        def read_while_a_card_is_refused(want):
            g.reader.opener = lambda _o: (None, "", "dc01.corp.example.mil:636 the bind was refused (invalidCredentials)")
            self.assertIsNone(g.account("ROE.RICH.A.2222222222")[0])          # a card lookup's bind is refused meanwhile
            g.reader.opener = opener()
            return real_read(want)                                            # the group read (bound before) succeeds
        g.reader.read = read_while_a_card_is_refused
        g.refresh()
        self.assertFalse(g.last["ok"])
        self.assertIn("invalidCredentials", g.last["error"])
        self.assertGreater(g.next_at, time.time() + 10 ** 8)                  # held until the next deploy
        tries = []
        g.reader.opener = lambda _o: (tries.append(1), (FakeConn(), "dc01", ""))[1]
        g.reader.read = real_read
        self.assertIsNone(g.account("DOE.JANE.Q.1234567890")[0])
        g.refresh()                                                           # a refresh called while held
        self.assertEqual(tries, [])                                           # no further bind
        self.assertFalse(g.last["ok"])

    def test_neon_r4_2_escaped_bytes_in_a_cn_stay_as_they_are(self):
        import site_portal as sp
        h = lambda d: {"X-SSL-Client-Verify": "SUCCESS", "X-SSL-Client-S-DN": d}      # noqa: E731
        self.assertNotEqual(sp.who(h(r"CN=EXAMPLE.ADMIN\20,O=Example"))[0], "EXAMPLE.ADMIN")
        self.assertNotEqual(sp.who(h(r"CN=TEST.1111111111\C2\A0,O=Example"))[0], "TEST.1111111111")
        self.assertNotIn("\x00", sp.who(h(r"CN=\00a blank entry,O=Example"))[0] or "")

    def test_new_05_one_objectguid_in_binary_and_text(self):
        import uuid
        u = uuid.UUID("00112233-4455-4677-8899-aabbccddeeff")
        self.assertEqual(G._guid(u.bytes_le), G._guid("{00112233-4455-4677-8899-aabbccddeeff}"))
        self.assertEqual(G._guid([u.bytes_le]), "00112233445546778899aabbccddeeff")
        self.assertEqual(G._guid(b"short"), "")


    def test_neon_r4_1_an_incomplete_candidate_naming_another_edipi_still_counts(self):
        one, two = dn("One", "Staff"), dn("Two", "Staff")

        class Omits(FakeConn):                        # Two matched on sAMAccountName=1111111111.ctr; the DC leaves it out
            def search(self, base, flt, scope, attributes=None, **kw):
                ok = FakeConn.search(self, base, flt, scope, attributes, **kw)
                if flt.startswith("(&(objectCategory=person)(objectClass=user)(!(userAccountControl") and "(|" in flt:
                    self.response.append({"type": "searchResEntry", "dn": two, "attributes": {
                        "userAccountControl": 512, "objectGUID": guid(two), "userPrincipalName": "2222222222@mil"}})
                return ok
        for rules in (None, [{"attribute": "userPrincipalName", "match": "{edipi}@mil"}, {"attribute": "sAMAccountName", "match": "{edipi}.ctr"}]):
            with self.subTest(rules=rules):
                g = self.rules_group(one, {"userPrincipalName": ["1111111111@mil"], "sAMAccountName": ["one"]}, rules)
                g.reader.opener = opener(Omits())
                with patch.dict(USERS, {one: {"upn": "1111111111@mil", "sam": "one", "uac": 512, "alt": []}}):
                    self.assertFalse(g.has("approvers", "ONE.A.A.1111111111"))
                    self.assertIn("more than one", g.account("ONE.A.A.1111111111")[1])

    def test_neon_r4_5_wildcard_neighbours_fully_read_never_lock_the_card_out(self):
        d = dn("Doe Jane", "Users")
        for upn in ("1234567890.adm@mil", "12345678901@mil", "123456789012345@mil"):
            with self.subTest(neighbour=upn):
                other = dn("Neighbour", "Admins")
                g = self.group(d, {"userPrincipalName": ["1234567890@mil"], "sAMAccountName": ["jane"]})
                with patch.dict(USERS, {d: {"upn": "1234567890@mil", "sam": "jane", "uac": 512, "alt": []},
                                        other: {"upn": upn, "sam": "jane.adm", "uac": 512, "alt": []}}):
                    self.assertTrue(g.has("approvers", "DOE.JANE.Q.1234567890"))


for _n in [n for n in vars(DirectorC) if n.startswith("test_")]:
    setattr(CodexR4, _n, None)


class Codex1330s(DirectorC):
    """1.133.0: Codex's review of the split release (rel-1330s 6ab8f57a) - each fails before its fix"""

    LOOKUP = "(&(objectCategory=person)(objectClass=user)(!(userAccountControl"

    def test_01_a_sole_partly_read_account_grants_nothing(self):
        d = dn("Doe", "Staff")
        g = self.group(d, {"userPrincipalName": ["1111111111@mil"]})
        with patch.dict(USERS, {d: {"upn": "1111111111@mil", "sam": None, "uac": 512, "alt": []}}):     # its SAM is not returned
            self.assertFalse(g.has("approvers", "DOE.JANE.Q.1111111111"))
            self.assertIn("state is not known", g.account("DOE.JANE.Q.1111111111")[1])

    def test_02_10_the_rules_as_set(self):
        import site_portal as sp
        one = [{"attribute": "userPrincipalName", "match": "{edipi}@mil"}]
        self.assertEqual(sp.card_rules_of({"PORTAL_AD_CARD_ATTRIBUTE": "userPrincipalName", "PORTAL_AD_CARD_MATCH": "{edipi}@mil"}), one)
        self.assertEqual(sp.card_rules_of({"PORTAL_AD_CARD_MATCH": "{edipi}@mil"}), one)
        self.assertEqual(sp.card_rules_of({"PORTAL_AD_CARD_ATTRIBUTE": "sAMAccountName", "PORTAL_AD_CARD_MATCH": "{edipi}.ctr"}),
                         [{"attribute": "sAMAccountName", "match": "{edipi}.ctr"}])
        self.assertIsNone(sp.card_rules_of({}))                                     # nothing set: the five
        for bad in ("null", "", "[", None, "{}", {"a": 1}, 7):
            self.assertEqual(sp.card_rules_of({"PORTAL_AD_CARD_MATCHES": bad}), "unreadable", bad)
        self.assertEqual(sp.card_rules_of({"PORTAL_AD_CARD_MATCHES": "[]"}), [])
        # a 1.132.0 settings file (the default pair): the plain UPN only - a .ctr-only account is no match
        c = sp.Config({"PORTAL_DATA": tempfile.mkdtemp(), "PORTAL_APPROVER_GROUPS": '["AAP-Approvers"]', "PORTAL_LDAP_BASE": BASE,
                       "PORTAL_AD_CARD_ATTRIBUTE": "userPrincipalName", "PORTAL_AD_CARD_MATCH": "{edipi}@mil"})
        self.assertEqual(c.ad_groups.reader.matches, one)
        # null at runtime: no card is matched in AD (named lists only), said - never the five
        c = sp.Config({"PORTAL_DATA": tempfile.mkdtemp(), "PORTAL_APPROVER_GROUPS": '["AAP-Approvers"]', "PORTAL_LDAP_BASE": BASE,
                       "PORTAL_AD_CARD_MATCHES": "null"})
        self.assertIsNone(c.ad_groups)
        self.assertIn("card rules are not usable", c.ad_groups_why)

    def test_03_a_request_answers_with_the_newest_decision(self):
        d, dup = dn("Doe", "Staff"), dn("Dup", "Admins")
        g = self.group(d, {"userPrincipalName": ["1234567890@mil"]})
        cn = "DOE.JANE.Q.1234567890"
        real = g.reader.accounts
        state = {"n": 0}

        def first_slow(c, base, card):
            out = real(c, base, card)                   # the first request sees one account ...
            if state["n"] == 0:
                state["n"] = 1
                with patch.dict(USERS, {dup: {"upn": "q@example.invalid", "sam": "1234567890.ctr", "uac": 512, "alt": []}}):
                    self.assertIsNone(g.account(cn)[0])  # ... a second request meanwhile sees two and stores "no"
            return out
        g.reader.accounts = first_slow
        with patch.dict(USERS, {d: {"upn": "1234567890@mil", "uac": 512, "alt": []}}):
            self.assertIsNone(g.account(cn)[0])           # the first request answers with that newer "no"

    def test_04_a_group_read_during_the_lookup_decides(self):
        import time as _t
        d = dn("Doe", "Staff")
        g = self.group(d, {"userPrincipalName": ["1234567890@mil"]})
        real = g.reader.accounts

        def read_lands(c, base, card):
            out = real(c, base, card)
            g.reader.read = lambda want: {"ok": True, "at": _t.time(), "error": "", "server": "", "groups": {"approvers": [
                {"name": "Portal-Admins", "found": True, "count": 0, "keys": []}]}}          # Jane is no longer a member
            g.refresh()
            return out
        g.reader.accounts = read_lands
        with patch.dict(USERS, {d: {"upn": "1234567890@mil", "uac": 512, "alt": []}}):
            self.assertFalse(g.has("approvers", "DOE.JANE.Q.1234567890"))

    def test_05_one_dn_two_guids_is_two_accounts(self):
        d = dn("Doe", "Staff")
        test = self

        class TwoGuids(FakeConn):
            def search(self, base, flt, scope, attributes=None, **kw):
                ok = FakeConn.search(self, base, flt, scope, attributes, **kw)
                if flt.startswith(test.LOOKUP) and "(|" in flt:
                    self.response.append(dict(self.response[0], attributes=dict(self.response[0]["attributes"], objectGUID="e" * 32)))
                return ok
        g = self.group(d, {"userPrincipalName": ["1234567890@mil"]})
        g.reader.opener = opener(TwoGuids())
        with patch.dict(USERS, {d: {"upn": "1234567890@mil", "uac": 512, "alt": []}}):
            self.assertFalse(g.has("approvers", "DOE.JANE.Q.1234567890"))
            self.assertIn("more than one", g.account("DOE.JANE.Q.1234567890")[1])

    def test_06_a_paged_search_without_its_end_marker_fails(self):
        d = dn("Doe", "Staff")
        test = self

        class NoEnd(FakeConn):
            def search(self, base, flt, scope, attributes=None, **kw):
                ok = FakeConn.search(self, base, flt, scope, attributes, **kw)
                if flt.startswith(test.LOOKUP) and "(|" in flt:
                    self.result = dict(self.result, controls={})
                return ok
        g = self.group(d, {"userPrincipalName": ["1234567890@mil"]})
        g.reader.opener = opener(NoEnd())
        with patch.dict(USERS, {d: {"upn": "1234567890@mil", "uac": 512, "alt": []}}):
            self.assertFalse(g.has("approvers", "DOE.JANE.Q.1234567890"))
            self.assertIn("could not be looked up", g.account("DOE.JANE.Q.1234567890")[1])

    def test_08_no_base_guessed_from_a_dc_name(self):
        r = G.Reader({"servers": [{"fqdn": "dc01.child.corp.example.mil"}]}, opener())

        class NoRoot(FakeConn):
            def search(self, base, flt, scope, attributes=None, **kw):
                self.response, self.result = [], {"result": 1, "description": "operationsError"}
                return False
        self.assertEqual(r.root(NoRoot()), "")
        self.assertEqual(G.Reader({"domain": "corp.example.mil"}, opener()).root(NoRoot()), BASE)

    def test_09_only_a_card_shaped_cn_is_looked_up(self):
        import site_portal as sp
        for ok in ("DOE.JANE.Q.1234567890", "DOE.JANE.1234567890", "O'NEIL-SMITH.ANN MARIE.Q.1234567890"):
            self.assertTrue(G.card_cn(ok), ok)
        for bad in ("1234567890", "EXAMPLE.1234567890", "A.B.C.D.1234567890", "DOE.JANE.Q.12345678901", "DOE..Q.1234567890"):
            self.assertFalse(G.card_cn(bad), bad)
        d = dn("Doe", "Staff")
        g = self.group(d, {"userPrincipalName": ["1234567890@mil"]})
        tries = []
        g.reader.opener = lambda _o: (tries.append(1), (FakeConn(), "dc01", ""))[1]
        self.assertIsNone(g.account("1234567890")[0])
        self.assertEqual(tries, [])                                            # no lookup at all
        h = {"X-SSL-Client-Verify": "SUCCESS", "X-SSL-Client-S-DN": "CN=DOE.JANE.Q.1234567890,CN=ROE.RICH.A.2222222222,O=Example"}
        self.assertIsNone(sp.who(h)[0])

    def test_12_a_refusal_holds_even_when_the_marker_is_scrubbed(self):
        calls = []

        def refused(*a, **kw):
            calls.append(1)
            return None, None, "the bind was refused: invalidCredentials"
        r = G.Reader({"servers": [{"fqdn": "dc01.corp.example.mil"}, {"fqdn": "dc02.corp.example.mil"}], "user": "u",
                      "password": "Credentials"})
        with patch.object(G.sl, "connect", refused):
            self.assertIsNone(r.open()[0])
            self.assertIsNone(r.open()[0])
        self.assertEqual(calls, [1])                                           # one bind, never the next DC, never again
        self.assertTrue(r.refused)


for _n in [n for n in vars(DirectorC) if n.startswith("test_")]:
    setattr(Codex1330s, _n, None)


class Review1120(unittest.TestCase):
    """The review's findings (Codex): an ambiguous name, a match that names nobody, a partial read, a stale read, a member
    without userAccountControl, a URI as server, a wrong password on every DC, a new card of the same person."""

    def test_1_two_groups_of_one_name_count_for_nobody(self):
        GROUPS[dn("AAP-Approvers", "Other")] = [dn("Sam Operator", "Users")]
        try:
            g = G.Groups({"approvers": ["AAP-Approvers"]}, {"base": BASE}, opener=opener())
            g.refresh()
            st = g.status("approvers")
            self.assertEqual((st["groups"][0]["found"], st["groups"][0]["why"]), (False, "2 groups have this name - give its DN"))
            self.assertFalse(g.has("approvers", "APPROVER.ALEX.B.3456789012"))
            self.assertFalse(g.has("approvers", "OPERATOR.SAM.J.2345678901"))
        finally:
            del GROUPS[dn("AAP-Approvers", "Other")]

    def test_2_the_match_names_the_person(self):
        self.assertEqual(G.card_key("APPROVER.ALEX.B.3456789012", "*@mil"), "")                   # no placeholder: matches nobody
        self.assertIsNone(G.card_pattern("APPROVER.ALEX.B.3456789012", "*@mil"))
        self.assertFalse(G.card_pattern("APPROVER.ALEX.B.3456789012").fullmatch("13456789012@mil"))   # a whole value, not a part
        with self.assertRaisesRegex(ValueError, r"needs \{edipi\} or \{cn\}"):
            G.Groups(WANT, {"base": BASE}, match="*@mil", opener=opener())

    def test_3_a_partial_read_is_no_read(self):
        class Partial(FakeConn):
            def search(self, base, flt, scope, attributes=None, **kw):
                ok = FakeConn.search(self, base, flt, scope, attributes, **kw)
                if "memberOf:" in flt:
                    self.result = {"result": 3, "description": "timeLimitExceeded"}                  # entries, then the limit
                    return False
                return ok
        g = G.Groups(WANT, {"base": BASE}, opener=opener(Partial()))
        r = g.refresh()
        self.assertFalse(r["ok"])
        self.assertIn("timeLimitExceeded", r["error"])
        self.assertFalse(g.has("approvers", "APPROVER.ALEX.B.3456789012"))

    def test_4_a_read_too_old_counts_for_nobody(self):
        g = G.Groups(WANT, {"base": BASE}, opener=opener())
        g.refresh()
        self.assertTrue(g.has("approvers", "APPROVER.ALEX.B.3456789012"))
        g.last["at"] -= g.seconds + 2 * G.RETRY + 1                                                  # the next read hangs
        self.assertFalse(g.has("approvers", "APPROVER.ALEX.B.3456789012"))
        self.assertFalse(g.status("approvers")["ok"])
        self.assertEqual(g.status("approvers")["error"], "the last read is too old")                 # said ...
        self.assertEqual(g.status("approvers")["groups"][0]["count"], None)                          # ... but it is not counted

    def test_5_no_user_account_control_is_not_counted(self):
        class NoUac(FakeConn):
            def search(self, base, flt, scope, attributes=None, **kw):
                ok = FakeConn.search(self, base, flt, scope, attributes, **kw)
                for e in self.response:
                    (e.get("attributes") or {}).pop("userAccountControl", None)
                return ok
        g = G.Groups(WANT, {"base": BASE}, opener=opener(NoUac()))
        g.refresh()
        self.assertFalse(g.has("approvers", "APPROVER.ALEX.B.3456789012"))

    def test_6_a_uri_is_no_server_and_a_wrong_password_is_tried_once(self):
        calls = []

        def fake_connect(fqdn, address, port, how, *a, **k):
            calls.append((fqdn, port, how))
            return None, 3, "the bind was refused - invalidCredentials: 80090308: LdapErr ... password Pw-Secret-1 rejected"
        real = G.sl.connect
        G.sl.connect = fake_connect
        try:
            r = G.Reader({"user": "svc", "password": "Pw-Secret-1", "starttls": True,
                          "servers": [{"name": "ldap://dc00.corp.example.mil"}, {"name": "dc01.corp.example.mil"}, {"name": "dc02.corp.example.mil"}]})
            conn, _server, err = r.open()
        finally:
            G.sl.connect = real
        self.assertEqual(calls, [("dc01.corp.example.mil", 636, "ldaps")])                         # the URI left out; one bind, no more
        self.assertIsNone(conn)
        self.assertNotIn("Pw-Secret-1", err)
        self.assertIn("***", err)
        import site_ldap
        self.assertIn("not a URI", site_ldap.connect("x", "ldap://x", 389, "ldaps")[2] if site_ldap.ldap3 else "not a URI")


class Review1120b(unittest.TestCase):
    """The second pass: a value with a trailing newline, a short DC name, a busy DC, a wrong password held, one base
    whichever DC answers, a clock set back, a short password in an error, a bad port."""

    def test_1_the_whole_value(self):
        self.assertIsNone(G.card_pattern("APPROVER.ALEX.B.3456789012").fullmatch("3456789012@mil\n"))
        self.assertIsNotNone(G.card_pattern("APPROVER.ALEX.B.3456789012").fullmatch("3456789012@mil"))

    def test_2_a_short_name_gets_the_domain_and_one_base(self):
        r = G.Reader({"domain": "corp.example.mil", "servers": [{"name": "dc01", "address": "192.0.2.11"}, {"name": "192.0.2.12"}]})
        self.assertEqual(r.servers(), [{"fqdn": "dc01.corp.example.mil", "address": "192.0.2.11"}, {"fqdn": "192.0.2.12", "address": "192.0.2.12"}])
        r2 = G.Reader({"servers": [{"name": "dc01.corp.example.mil"}, {"name": "dc07.other.example.mil"}]})
        self.assertEqual(r2.base(FakeConn()), BASE)                                                   # the first DC's domain, never the one that answered

    def test_3_busy_fails_over_a_wrong_password_is_held(self):
        calls = []

        def fake(fqdn, address, port, how, *a, **k):
            calls.append(fqdn)
            return None, 3, ("the bind was refused - busy" if fqdn.startswith("dc01") else "the bind was refused - invalidCredentials: 80090308")
        real, had = G.sl.connect, G.ldap3
        G.sl.connect, G.ldap3 = fake, G.ldap3 or object()          # no ldap3 needed: the connection is the fake
        try:
            g = G.Groups(WANT, {"user": "svc", "password": "Pw", "servers": [{"name": "dc01.corp.example.mil"}, {"name": "dc02.corp.example.mil"},
                                                                            {"name": "dc03.corp.example.mil"}]})
            r = g.refresh()
        finally:
            G.sl.connect, G.ldap3 = real, had
        self.assertEqual(calls, ["dc01.corp.example.mil", "dc02.corp.example.mil"])                  # busy: the next; wrong password: no more
        self.assertIn("not tried again until the portal is deployed again", r["error"])
        self.assertGreater(g.next_at - r["at"], 10 ** 8)

    def test_4_a_clock_set_back_and_a_short_password(self):
        g = G.Groups(WANT, {"base": BASE}, opener=opener())
        g.refresh()
        g.last["at"] += G.RETRY + 5                                                                    # the clock went back
        self.assertFalse(g.has("approvers", "APPROVER.ALEX.B.3456789012"))
        self.assertEqual(G.Reader({"password": "pw"}).scrub("bind for pw refused"), "bind for *** refused")

    def test_5_a_bad_port_is_an_answer(self):
        import site_ldap
        if site_ldap.ldap3 is None:
            self.skipTest("no ldap3 here")
        c, _ms, err = site_ldap.connect("dc01.corp.example.mil", "127.0.0.1", "notaport", "ldaps")
        self.assertIsNone(c)
        self.assertTrue(err)


class People(unittest.TestCase):
    def test_the_named_and_the_groups(self):
        g = G.Groups(WANT, {"base": BASE}, opener=opener())
        approvers = G.People(["NAMED.ONE.A.1111111111"], "approvers", g)
        requesters = G.People([], "requesters", g)
        self.assertIn("NAMED.ONE.A.1111111111", approvers)
        self.assertNotIn("APPROVER.ALEX.B.3456789012", approvers)                                  # not read yet
        self.assertTrue(requesters)                                                                # groups set: not "anyone", even before a read
        g.refresh()
        self.assertIn("APPROVER.ALEX.B.3456789012", approvers)
        self.assertEqual(json.dumps(approvers), '["NAMED.ONE.A.1111111111"]')                     # written as the named list it was
        self.assertEqual(approvers.named(), ["NAMED.ONE.A.1111111111"])
        self.assertFalse(G.People([], "approvers", G.Groups({}, {})))                              # nothing set: empty, as before
        self.assertFalse(G.People([], "requesters", None))


class Portal(unittest.TestCase):
    """The portal with the groups: who may decide and run, the Approvals page's line, AD down."""

    def cfg(self, **kw):
        import site_portal as sp
        env = {"PORTAL_DATA": tempfile.mkdtemp(), "PORTAL_APPROVERS": '["NAMED.ONE.A.1111111111"]', "PORTAL_REQUESTERS": "[]",
               "PORTAL_APPROVER_GROUPS": '["AAP-Approvers"]', "PORTAL_REQUESTER_GROUPS": '["AAP-Operators"]',
               "PORTAL_LDAP_LOGIN": '{"user": "svc_aap_adreader@corp.example.mil", "password": "x"}', "PORTAL_LDAP_BASE": BASE,
               "PORTAL_TIME_ZONE": "Europe/Berlin"}
        env.update(kw)
        c = sp.Config(env)
        return sp, c

    def test_1_ad_read(self):
        sp, c = self.cfg()
        c.ad_groups.reader.opener = opener()
        c.ad_groups.refresh()
        self.assertIn("APPROVER.ALEX.B.3456789012", c.approvers)
        self.assertIn("NAMED.ONE.A.1111111111", c.approvers)
        self.assertNotIn("OPERATOR.SAM.J.2345678901", c.approvers)
        import site_portal_filters as rules
        act = {"who": None}
        self.assertTrue(rules.may_run(act, "OPERATOR.SAM.J.2345678901", c.requesters))
        self.assertFalse(rules.may_run(act, "SOMEONE.ELSE.X.9999999999", c.requesters))           # groups set: not anyone
        h = sp.deciders_html(c)
        self.assertIn("<b>Who may decide:</b> AAP-Approvers (AD), 3 people &middot; 1 named in <code>portal_approvers</code>.", h)
        self.assertIn("disabled accounts never", h)
        self.assertIn("by its number (sAMAccountName, userPrincipalName)", h)
        self.assertNotIn("AD not read", h)

    def test_default_card_formats_and_diagnostics(self):
        cn = "EXAMPLE.PERSON.A.1234567890"
        for upn, sam, matched in [
            ("1234567890@mil", "aperson", "userPrincipalName"),
            ("1234567890197005@MIL", "aperson", "userPrincipalName"),
            ("person@example.invalid", "1234567890.CTR", "sAMAccountName"),
            ("1234567890.ctr@mil", "aperson", None),                  # the UPN .ctr form is not a default any more
            ("1234567890.ctr@mil.example", "1234567890.adm", None),
            ("1234567891@mil", "1234567891.ctr", None),
        ]:
            with self.subTest(upn=upn, sam=sam):
                sp, cfg = self.cfg(PORTAL_REQUESTER_GROUPS='["AAP-Approvers"]')
                conn = FakeConn()
                cfg.ad_groups.reader.opener = opener(conn)
                with patch.dict(USERS[dn("Alex Approver", "Users")], upn=upn, sam=sam):     # the directory as it is at the check too
                    cfg.ad_groups.refresh()
                    self.assertEqual(cn in cfg.approvers, matched is not None)
                    self.assertEqual(cfg.ad_groups.card_found(cn), matched is not None)
                    self.assertEqual(cfg.ad_groups.account(cn)[1] if matched else None, matched)
                self.assertEqual(conn.member_attributes, [["userPrincipalName", "sAMAccountName", "userAccountControl", "objectGUID"]])

    def test_2_ad_down_only_the_named(self):
        sp, c = self.cfg()
        c.ad_groups.reader.opener = opener(down=True)
        c.ad_groups.refresh()
        self.assertNotIn("APPROVER.ALEX.B.3456789012", c.approvers)
        self.assertIn("NAMED.ONE.A.1111111111", c.approvers)
        import site_portal_filters as rules
        self.assertFalse(rules.may_run({"who": None}, "OPERATOR.SAM.J.2345678901", c.requesters))   # never open: nobody but the named
        h = sp.deciders_html(c)
        self.assertIn('<span class="chip s-warning">AD not read</span><b>Only the named approvers may decide</b>: 1 in <code>portal_approvers</code>.', h)
        self.assertIn("AAP-Approvers (AD) is not counted until AD answers.", h)
        self.assertIn("dc01.corp.example.mil:636 no answer - tried ", h)
        sp2, none = self.cfg(PORTAL_APPROVERS="[]")
        none.ad_groups.reader.opener = opener(down=True)
        none.ad_groups.refresh()
        self.assertIn("nobody (<code>portal_approvers</code> is empty)", sp2.deciders_html(none))
        sp3, first = self.cfg()
        self.assertIn("not read yet - the first read is under way", sp3.deciders_html(first))

    def test_3_no_groups_as_before(self):
        sp, c = self.cfg(PORTAL_APPROVER_GROUPS="[]", PORTAL_REQUESTER_GROUPS="[]")
        self.assertIsNone(c.ad_groups)
        import site_portal_filters as rules
        self.assertTrue(rules.may_run({"who": None}, "ANYONE.A.B.1212121212", c.requesters))       # [] = any person with a card, as before
        self.assertEqual(sp.deciders_html(c), '<div class="note deciders"><b>Who may decide:</b> 1 named in <code>portal_approvers</code>.</div>')
        self.assertNotIn("ANYONE.A.B.1212121212", c.approvers)

    def test_5_without_the_reader_closed_and_a_new_card_is_the_same_person(self):
        import site_portal as sp
        import site_portal_filters as rules
        had = sp.site_ad_groups
        sp.site_ad_groups = None                                                                      # an older deploy's files
        try:
            c = sp.Config({"PORTAL_DATA": tempfile.mkdtemp(), "PORTAL_REQUESTERS": "[]", "PORTAL_REQUESTER_GROUPS": '["AAP-Operators"]',
                           "PORTAL_APPROVERS": '["NAMED.ONE.A.1111111111"]', "PORTAL_APPROVER_GROUPS": '["AAP-Approvers"]'})
        finally:
            sp.site_ad_groups = had
        self.assertFalse(rules.may_run({"who": None}, "ANYONE.A.B.1212121212", c.requesters))       # never open
        self.assertIn("NAMED.ONE.A.1111111111", c.approvers)
        self.assertIn("The portal&#x27;s AD groups reader (site_ad_groups.py) is not in the service", sp.deciders_html(c))
        self.assertTrue(sp.same_person("DOE.JANE.Q.1234567890", "SMITH.JANE.Q.1234567890"))        # a new card after a name change
        self.assertTrue(sp.same_person("A.B", "A.B"))
        self.assertFalse(sp.same_person("DOE.JANE.Q.1234567890", "DOE.JANE.Q.1234567891"))
        self.assertFalse(sp.same_person("", ""))

    def test_4_the_approvals_page_and_the_log(self):
        sp, c = self.cfg()
        c.ad_groups.reader.opener = opener(down=True)
        portal = type("P", (), {"token": lambda self, cn: "t"})()
        h = sp.approvals_page(c, portal, "NAMED.ONE.A.1111111111")
        self.assertEqual(h.count('class="note warn deciders"'), 1)
        self.assertLess(h.index("deciders"), h.index("AAP records each decision"))
        log = []
        p = sp.Portal.__new__(sp.Portal)
        p.log = type("L", (), {"add": lambda self, rec: log.append(rec)})()
        c.ad_groups.on_change = p._ad_groups_changed
        c.ad_groups.refresh()
        self.assertEqual(log[-1]["event"], "ad-groups")
        self.assertIn("AD not read - only the named lists", log[-1]["why"])
        self.assertNotIn('"password"', json.dumps(log))


class Deploy(unittest.TestCase):
    def read(self, *p):
        with open(os.path.join(REPO, *p)) as fh:
            return fh.read()

    def test_the_login_is_a_podman_secret_never_a_setting(self):
        t = self.read("roles", "site_portal_stack", "tasks", "main.yml")
        self.assertIn("Secret=site-portal-ldap,type=env,target=PORTAL_LDAP_LOGIN", self.read("roles", "site_portal_stack", "templates", "site-portal-app.container.j2"))
        settings = t[t.index("PORTAL_APPROVERS:"):t.index("PORTAL_LAUNCHES_PER_HOUR")]
        self.assertNotIn("PORTAL_LDAP_LOGIN", settings)
        self.assertNotIn("password", settings)
        for name in ("Secret | the AD groups' LDAP login - the value", "Secret | the AD groups' LDAP login - create or replace"):
            task = t[t.index(name):t.index("- name:", t.index(name) + 10)]
            self.assertIn("no_log: true", task, name)
        self.assertIn("site_ldap.py", t)
        self.assertIn("site_ad_groups.py", t)
        d = self.read("roles", "site_portal_stack", "defaults", "main.yml")
        self.assertIn("portal_ldap_vars: {user: MICROSOFT_AD_LDAP_USERNAME, password: MICROSOFT_AD_LDAP_PASSWORD}", d)
        ct = self.read("aap", "credential_types", "ldap_bind_account.yml")
        self.assertIn('MICROSOFT_AD_LDAP_USERNAME: "{{ ldap_username }}"', ct)
        self.assertIn("secret: true", ct)

    def test_the_ad_report_connects_the_same_way(self):
        r = self.read("roles", "ad_report", "library", "site_ad_reader.py")
        self.assertIn("return site_ldap.connect(", r)
        self.assertNotIn("ldap3.Connection(", r)


class Review1122(unittest.TestCase):
    """1.122.2 (the review after the release): a short DC name dialed by its full name, a wrong password's read never
    "again at" a time decades away, aap-check never binds, a group of two names logged as such, the socket closed when
    StartTLS fails, the {cn} match with no CN, the approver as the service says on each read."""

    def test_1_a_short_name_is_dialed_by_its_full_name(self):
        r = G.Reader({"domain": "corp.example.mil", "servers": [{"name": "dc01", "address": ""}]})
        self.assertEqual(r.servers(), [{"fqdn": "dc01.corp.example.mil", "address": "dc01.corp.example.mil"}])
        r2 = G.Reader({"servers": [{"name": "dc01", "address": "dc01.corp.example.mil"}, {"name": "dc02", "address": "192.0.2.12"}]})
        self.assertEqual(r2.servers(), [{"fqdn": "dc01.corp.example.mil", "address": "dc01.corp.example.mil"},
                                        {"fqdn": "dc02", "address": "192.0.2.12"}])
        self.assertEqual(r2.base(FakeConn()), BASE)                                                   # the first DC's domain

    def test_2_a_wrong_password_is_never_again_at(self):
        g = G.Groups(WANT, {"base": BASE}, opener=lambda o: (None, "", "dc01.corp.example.mil:636 the bind was refused - invalidCredentials: 80090308"))
        r = g.refresh()
        self.assertGreater(g.next_at - r["at"], 10 ** 8)                                             # still held: no second bind
        self.assertIsNone(g.status("approvers")["next"])
        import site_portal as sp
        c = sp.Config({"PORTAL_DATA": tempfile.mkdtemp(), "PORTAL_APPROVERS": "[]", "PORTAL_APPROVER_GROUPS": '["AAP-Approvers"]',
                       "PORTAL_LDAP_LOGIN": '{"user": "svc", "password": "x"}', "PORTAL_LDAP_BASE": BASE, "PORTAL_TIME_ZONE": "Europe/Berlin"})
        c.ad_groups.reader.opener = g.reader.opener
        c.ad_groups.refresh()
        h = sp.deciders_html(c)
        self.assertIn("not tried again until the portal is deployed again", h)
        self.assertIn(" - tried ", h)
        self.assertNotIn("again at", h)
        c.ad_groups.reader.opener = opener(down=True)                                                 # a read that is late: no time in the past
        c.ad_groups.refresh()
        c.ad_groups.next_at = time.time() - 30
        self.assertIsNone(c.ad_groups.status("approvers")["next"])
        self.assertNotIn("again at", sp.deciders_html(c))
        c.ad_groups.next_at = time.time() + 60
        self.assertIn(", again at ", sp.deciders_html(c))

    def test_3_aap_check_never_starts_the_reader_and_two_names_are_said(self):
        import site_portal as sp
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "a.json"), "w") as fh:
            json.dump({"actions": {}}, fh)
        c = sp.Config({"PORTAL_DATA": d, "PORTAL_ACTIONS": os.path.join(d, "a.json"), "PORTAL_APPROVERS": "[]",
                       "PORTAL_APPROVER_GROUPS": '["AAP-Approvers"]', "PORTAL_LDAP_LOGIN": '{"user": "svc", "password": "x"}',
                       "PORTAL_LDAP_BASE": BASE, "PORTAL_AAP_URL": "http://127.0.0.1:9", "PORTAL_AAP_TOKEN": "x"})
        started = []
        c.ad_groups.start = lambda: started.append(1)
        sp.Portal(c, log=sp.Log(""), cache_catalog=False, start_ad=False)
        self.assertEqual(started, [])
        sp.Portal(c, log=sp.Log(""), cache_catalog=False)
        self.assertEqual(started, [1])
        with open(sp.__file__) as fh:
            self.assertIn("Portal(cfg, log=Log(\"\"), cache_catalog=False, start_ad=False)", fh.read())    # --aap-check
        log = []
        p = sp.Portal.__new__(sp.Portal)
        p.log = type("L", (), {"add": lambda self, rec: log.append(rec)})()
        p._ad_groups_changed({"ok": True, "server": "dc01", "groups": {"approvers": [
            {"name": "Ops", "found": False, "count": 0, "why": "2 groups have this name - give its DN"},
            {"name": "Gone", "found": False, "count": 0, "why": ""}]}})
        self.assertEqual(log[-1]["groups"]["approvers"], ["Ops: 2 groups have this name - give its DN", "Gone: not found"])

    def test_4_starttls_refused_or_a_failed_handshake_closes_the_socket(self):
        import site_ldap
        made = []

        class Conn(object):
            def __init__(self, *a, **k):
                self.closed, self.result, self.unbound = False, {"description": "unavailable"}, 0
                made.append(self)

            def open(self):
                pass

            def start_tls(self):
                if mode[0] == "raise":
                    raise OSError("handshake failed")
                return False

            def unbind(self):
                self.unbound += 1

        fake = type("L", (), {"Server": staticmethod(lambda *a, **k: None), "Connection": Conn, "Tls": staticmethod(lambda **k: None),
                              "SIMPLE": "SIMPLE", "NONE": "NONE"})
        real = site_ldap.ldap3
        site_ldap.ldap3 = fake
        try:
            mode = ["false"]
            c, _ms, err = site_ldap.connect("dc01.corp.example.mil", "dc01.corp.example.mil", 389, "starttls", "u", "p")
            self.assertIsNone(c)
            self.assertIn("StartTLS refused", err)
            mode = ["raise"]
            c, _ms, err = site_ldap.connect("dc01.corp.example.mil", "dc01.corp.example.mil", 389, "starttls", "u", "p")
            self.assertIsNone(c)
            self.assertIn("handshake failed", err)
        finally:
            site_ldap.ldap3 = real
        self.assertEqual([m.unbound for m in made], [1, 1])

    def test_5_a_cn_match_with_no_cn(self):
        self.assertEqual(G.card_key("", "{cn}"), "")
        self.assertIsNone(G.card_pattern("", "{cn}"))
        self.assertIsNone(G.card_pattern("  ", "{cn}"))
        self.assertIsNone(G.card_pattern("DOE.JANE.Q.1234567890", "{cn}"))      # 1.133.0: not one of the five forms

    def test_6_the_approver_as_the_service_says(self):
        import site_portal as sp
        with open(sp.__file__) as fh:
            src = fh.read()
        self.assertIn('"approver": bool(cn) and cn in self.cfg.approvers}', src)
        with open(os.path.join(REPO, "roles", "site_portal_stack", "files", "static", "portal.js")) as fh:
            js = fh.read()
        self.assertIn('if (typeof r.approver === "boolean") approver = r.approver;', js)
        self.assertIn('code("portal_approve_groups")', js)
        with open(os.path.join(REPO, "roles", "site_portal_stack", "tasks", "main.yml")) as fh:
            t = fh.read()
        self.assertIn("'win_dc' if (groups.get('win_dc') or []) | length > 0 else 'domain_controllers'", t)   # an empty win_dc: the other



class CardMatchDefault(unittest.TestCase):
    """1.130.1: the deploy's default portal_ad_card_match is exact ({edipi}@mil) - no other account of the same person matches"""

    def test_suffixes_and_anchors(self):
        import os
        import re as _re
        d = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "roles", "site_portal_stack", "defaults", "main.yml"),
                 encoding="utf-8").read()
        self.assertRegex(d, r'(?m)^portal_ad_card_match: ""')            # 1.133.0: unset - set, it is the ONE rule
        pat = G.card_pattern("DOE.JANE.Q.1234567890", "{edipi}@mil")      # the one rule's form when left out
        for ok in ("1234567890@mil",):
            self.assertTrue(pat.fullmatch(ok), ok)
        for bad in ("9876543210@mil", "1234567890.adm@mil", "12345678901@mil", "1234567890.ctr@mil", "1234567890.ctr@mil.example", "x1234567890@mil", "1234567890@mil\n"):
            self.assertFalse(pat.fullmatch(bad), bad)

if __name__ == "__main__":
    unittest.main()
