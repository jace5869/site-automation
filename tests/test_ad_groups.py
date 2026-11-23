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
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "plugins", "module_utils"), os.path.join(REPO, "plugins", "filter"),
                os.path.join(REPO, "roles", "site_portal_stack", "files")]
import site_ad_groups as G  # noqa: E402

BASE = "DC=corp,DC=example,DC=mil"


def dn(cn, ou="Groups"):
    return "CN=%s,OU=%s,%s" % (cn, ou, BASE)


# the directory: groups (their direct members: user or group DNs) and users (upn, uac, altSecurityIdentities)
USERS = {dn("Alex Approver", "Users"): {"upn": "3456789012@mil", "uac": 512, "alt": ["X509:<I>CN=DOD ID CA-70<S>CN=APPROVER.ALEX.B.3456789012"]},
         dn("Jo Lead", "Users"): {"upn": "4567890123@mil", "uac": 512, "alt": []},
         dn("Old Lead", "Users"): {"upn": "5678901234@mil", "uac": 514, "alt": []},                    # 512 + 2: disabled
         dn("Deep Member", "Users"): {"upn": "6789012345@mil", "uac": 66048, "alt": []},              # 512 + 65536 (no expiry): enabled
         dn("Sam Operator", "Users"): {"upn": "2345678901@mil", "uac": 512, "alt": []}}
GROUPS = {dn("AAP-Approvers"): [dn("Alex Approver", "Users"), dn("Platform-Leads")],
          dn("Platform-Leads"): [dn("Jo Lead", "Users"), dn("Old Lead", "Users"), dn("Deep-Team")],
          dn("Deep-Team"): [dn("Deep Member", "Users"), dn("AAP-Approvers")],                        # a loop back: counted once
          dn("AAP-Operators"): [dn("Sam Operator", "Users")]}
SAM = {"AAP-Approvers": "aap_approvers"}


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

    def _set(self, entries):
        self.response = [dict(e, type="searchResEntry") for e in entries]
        self.result = {"result": 0, "description": "success", "controls": {}}
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
                out.append({"dn": u, "attributes": a})
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
        self.assertEqual(G.card_key("A.B.C.1234567890", "*CN={cn}"), "*cn=a.b.c.1234567890")
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

    def test_7_another_attribute_and_a_glob(self):
        g = G.Groups(WANT, {"base": BASE, "attribute": "altSecurityIdentities"}, match="*CN={cn}", opener=opener())
        g.refresh()
        self.assertTrue(g.has("approvers", "APPROVER.ALEX.B.3456789012"))
        self.assertFalse(g.has("approvers", "LEAD.JO.K.4567890123"))                               # no altSecurityIdentities


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
        p = G.card_pattern("A*.B.C.1234567890", "*CN={cn}")
        self.assertTrue(p.match("x509:<s>cn=a*.b.c.1234567890"))
        self.assertFalse(p.match("x509:<s>cn=abc.b.c.1234567890"))                                 # the CN's * is a *
        self.assertFalse(G.card_pattern("APPROVER.ALEX.B.3456789012").match("13456789012@mil"))   # a whole value, not a part
        g = G.Groups(WANT, {"base": BASE}, match="*@mil", opener=opener())
        g.refresh()
        self.assertFalse(g.has("approvers", "APPROVER.ALEX.B.3456789012"))

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
        self.assertIn("(userPrincipalName)", h)
        self.assertNotIn("AD not read", h)

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


if __name__ == "__main__":
    unittest.main()
